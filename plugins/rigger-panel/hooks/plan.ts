import type { PlanAge, PlanEvidence, PlanStep } from '../types'

export const PLAN_TOOL = 'mcp__ccd_turn__update_plan'
export const CLOSE_TOOL = 'mcp__ccd_turn__wrap_up'

export type PlanCall = {
  items?: { content?: unknown; status?: unknown; session?: unknown }[]
  advance?: boolean
  done?: number[]
  start?: number
}

const STATUSES = ['pending', 'in_progress', 'completed'] as const

const statusOf = (value: unknown): PlanStep['status'] =>
  STATUSES.find(status => status === value) ?? 'pending'

const fromItems = (items: NonNullable<PlanCall['items']>): PlanStep[] =>
  items.map(item => ({
    content: String(item.content ?? ''),
    status: statusOf(item.status),
    ...(typeof item.session === 'string' ? { session: item.session } : {}),
  }))

const advance = (steps: PlanStep[]): PlanStep[] => {
  const closed = steps.map(step => (step.status === 'in_progress' ? { ...step, status: 'completed' as const } : step))
  const next = closed.findIndex(step => step.status === 'pending')
  return closed.map((step, index) => (index === next ? { ...step, status: 'in_progress' as const } : step))
}

// `done` and `start` count from 1, as the tool does; an index past the list is ignored.
const mark = (steps: PlanStep[], done: number[], start?: number): PlanStep[] =>
  steps.map((step, index) => {
    if (done.includes(index + 1)) return { ...step, status: 'completed' }
    return index + 1 === start ? { ...step, status: 'in_progress' } : step
  })

// The list after one update_plan call: `items` replaces it, the rest moves steps within it.
export const applyPlan = (steps: PlanStep[], call: PlanCall): PlanStep[] => {
  if (Array.isArray(call.items)) return fromItems(call.items)
  if (call.advance === true) return advance(steps)
  return mark(steps, Array.isArray(call.done) ? call.done : [], call.start)
}

// wrap_up checks the whole list off, the steps not started included, except the steps still in
// progress while a subagent runs: the app keeps those open with their running task.
export const closePlan = (steps: PlanStep[], running: number): PlanStep[] =>
  steps.map(step => (step.status === 'in_progress' && running > 0 ? step : { ...step, status: 'completed' }))

// A subagent that stops to wait for its own background work sends a `completed` notification
// too, worded as waiting; it is not a task that finished.
export const isInterim = (text: string): boolean => /<summary>[^<]*waiting on its own background work/.test(text)

export const EMPTY_AGE: PlanAge = { touched: false, nudged: false }
// A list update this long before a command started was sent in the same message, so it covers the commit.
export const COVER_MS = 2000

// `git`, its global options (`-C <path>`, `-c <name=value>`, `--no-pager`), then the subcommand `commit`.
// Git's global options that take the next word as their value (`git -C <path> commit`).
const GIT_VALUE_OPTIONS = ['-C', '-c']

// One simple command's words; a quoted run of words is one word.
const words = (segment: string) => segment.match(/"[^"]*"|'[^']*'|\S+/g) ?? []

// `git`, its global options, then the subcommand `commit`, and no dry run among its own words.
const commits = (segment: string) => {
  const [program, ...rest] = words(segment)
  if (program !== 'git') return false
  let at = 0
  while (rest[at]?.startsWith('-')) at += GIT_VALUE_OPTIONS.includes(rest[at] ?? '') ? 2 : 1
  return rest[at] === 'commit' && !rest.slice(at + 1).includes('--dry-run')
}

// True when the shell command makes a commit: a dry run or a word after another subcommand does not
// count. The command is cut where the shell starts a new one and each piece is walked word by word,
// so its cost grows with the command's length and no faster.
export const isCommit = (command: unknown): boolean =>
  typeof command === 'string' && command.split(/[\n;&|({]/).some(commits)

export const touchAge = (now: number): PlanAge => ({ updatedAt: now, touched: true, nudged: false })

// The age after one sign of progress at `at`; `startedAt` is when the command behind a commit began.
export const withEvidence = (
  age: PlanAge,
  steps: PlanStep[],
  kind: PlanEvidence['kind'],
  at: number,
  startedAt = at,
): PlanAge => {
  if (age.updatedAt === undefined || (kind === 'commit' && startedAt - age.updatedAt <= COVER_MS)) return age
  // A turn may end with steps left for later; only a step still shown as running is behind.
  const behind = steps.some(step => (kind === 'turn' ? step.status === 'in_progress' : step.status !== 'completed'))
  if (!behind || (kind === 'turn' && age.evidence !== undefined)) return age
  return { ...age, evidence: { kind, at } }
}

const two = (value: number) => String(value).padStart(2, '0')

const clockTime = (at: number) => {
  const date = new Date(at)
  return `${two(date.getHours())}:${two(date.getMinutes())}`
}

const ago = (ms: number) => {
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 1) return 'just now'
  return minutes < 60 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ago`
}

export const ageLine = (age: PlanAge, now: number): string | undefined =>
  age.updatedAt === undefined ? undefined : `Updated ${clockTime(age.updatedAt)} (${ago(now - age.updatedAt)})`

const STALE_TEXT = {
  commit: (time: string) => `a commit at ${time}`,
  task: (time: string) => `a background task finished at ${time}`,
  turn: (time: string) => `a turn ended at ${time} with no update`,
} as const

export const staleLine = (age: PlanAge): string | undefined =>
  age.evidence === undefined ? undefined : `May be out of date: ${STALE_TEXT[age.evidence.kind](clockTime(age.evidence.at))}`

const NUDGE_EVENT = {
  commit: 'the last commit',
  task: 'a background task finished',
  turn: 'the previous turn ended',
} as const

// What the session reads: it asks to match the current state, never to mark a step done.
export const nudgeText = (evidence: PlanEvidence): string =>
  `The task list was last updated before ${NUDGE_EVENT[evidence.kind]}; update it to match the current state.`

export const planMark = (status: PlanStep['status']) =>
  status === 'completed' ? '✓' : status === 'in_progress' ? '▶' : '○'

const ICON = (body: string) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14">${body}</svg>`
const SPIN = '<animateTransform attributeName="transform" type="rotate" from="0 7 7" to="360 7 7" dur="1s" repeatCount="indefinite"/>'

// Gray holds up on the light and the dark theme, as the app's own list; the running step turns.
export const planIcon = (status: PlanStep['status'], still = false) => {
  if (status === 'completed') {
    return ICON('<circle cx="7" cy="7" r="6" fill="#8a8a8a"/><path d="M4 7.2l2 2 4-4.2" fill="none" stroke="#fff" stroke-width="1.5"/>')
  }
  if (status === 'in_progress') {
    return ICON(`<circle cx="7" cy="7" r="5.5" fill="none" stroke="#a0a0a0" stroke-width="1.6" stroke-dasharray="11 24" stroke-linecap="round">${still ? '' : SPIN}</circle>`)
  }
  return ICON('<circle cx="7" cy="7" r="5.5" fill="none" stroke="#8a8a8a" stroke-width="1.5" stroke-dasharray="2 2"/>')
}