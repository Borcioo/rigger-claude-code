import type { EngineInterface } from 'claude-code'

import type { IssueEmpty, IssueRow, IssueRows } from '../types'

// The issue list is read with the GitHub CLI and nothing else: `gh --version`, `gh auth status`,
// `git remote get-url origin` and `gh issue list`. Nothing here writes to GitHub.
export const LIMIT = 100

// `owner/repo` as GitHub allows it: no leading dash (so it is never read as an option), no shell or
// path characters, and neither `.` nor `..` as the name. Checked before it enters an argument or a text.
export const REPO = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/(?!\.\.?$)[A-Za-z0-9._-]{1,100}$/u

const RUN_MS = 10_000
const TITLE_CHARS = 60
const SPAWN_TITLE_CHARS = 59
const TEXT_CHARS = 300

// https, ssh:// and scp-style remotes of github.com, with an optional user, `.git` and trailing slash.
// Both forms take the user part from one pattern.
const USER = String.raw`(?:[^@/\s]+@)?`
const URL_REMOTE = new RegExp(String.raw`^(?:https?|ssh|git):\/\/${USER}github\.com(?::\d+)?\/([^/\s]+)\/([^/\s]+?)(?:\.git)?\/?$`, 'iu')
const SCP_REMOTE = new RegExp(String.raw`^${USER}github\.com:([^/\s]+)\/([^/\s]+?)(?:\.git)?\/?$`, 'iu')

export const isIssueNumber = (number: unknown): number is number =>
  typeof number === 'number' && Number.isSafeInteger(number) && number > 0

export const issueRef = (repo: string, number: number) => `${repo}#${number}`
export const issueUrl = (repo: string, number: number) => `https://github.com/${repo}/issues/${number}`

// `owner/repo` of a GitHub remote, or null for any other remote or text.
export function repoFromRemote(remote: string): string | null {
  const text = remote.trim()
  const found = URL_REMOTE.exec(text) ?? SCP_REMOTE.exec(text)
  if (!found) return null
  const repo = `${found[1]}/${found[2]}`
  return REPO.test(repo) ? repo : null
}

export function tidy(text: string) {
  return text.replace(/\s+/gu, ' ').trim()
}

export const clip = (text: string) => {
  const chars = Array.from(tidy(text))
  return chars.length > TEXT_CHARS ? `${chars.slice(0, TEXT_CHARS).join('')}…` : chars.join('')
}

export function shortTitle(title: string) {
  const text = tidy(title) || '(untitled)'
  const chars = Array.from(text)
  return chars.length > TITLE_CHARS ? `${chars.slice(0, TITLE_CHARS - 1).join('')}…` : text
}

export type Parsed = { ok: true; rows: IssueRow[] } | { ok: false; reason: string }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

// The JSON `gh issue list --json number,title,state,url` prints. Anything that does not fit is a
// failed read, never a shorter list; a url that is not this repository's issue is refused too.
export function parseIssues(text: string, repo: string): Parsed {
  let doc: unknown
  try {
    doc = JSON.parse(text)
  } catch {
    return { ok: false, reason: `gh issue list did not return JSON: ${JSON.stringify(Array.from(text.trim()).slice(0, 40).join(''))}` }
  }
  if (!Array.isArray(doc)) return { ok: false, reason: 'gh issue list returned JSON that is not a list' }
  const rows: IssueRow[] = []
  for (const [index, entry] of doc.entries()) {
    if (!isRecord(entry) || !isIssueNumber(entry.number) || typeof entry.title !== 'string' || typeof entry.state !== 'string' || typeof entry.url !== 'string') {
      return { ok: false, reason: `entry ${index + 1} is not an issue` }
    }
    if (entry.url !== issueUrl(repo, entry.number)) return { ok: false, reason: `issue ${entry.number} has an unexpected url` }
    rows.push({ number: entry.number, title: tidy(entry.title), state: tidy(entry.state), url: entry.url })
  }
  return { ok: true, rows }
}

const empty = (reason: IssueEmpty, repo?: string): IssueRows => ({ rows: [], limit: LIMIT, ...(repo ? { repo } : {}), empty: reason })

// The open issues assigned to the user in `repo` (`owner/repo`), or in the repository of the
// session folder's `origin` remote when `repo` is null. No `gh`, `gh` not logged in, no GitHub
// `origin` and no issue are empty states; a failing `gh issue list` throws its reason.
export async function listAssignedIssues($: EngineInterface, repo: string | null): Promise<IssueRows> {
  try {
    if ((await $.process.run(['gh', '--version'], { timeoutMs: RUN_MS })).exitCode !== 0) return empty('no-gh')
  } catch {
    return empty('no-gh')
  }
  let auth: Awaited<ReturnType<EngineInterface['process']['run']>>
  try {
    auth = await $.process.run(['gh', 'auth', 'status', '--hostname', 'github.com'], { timeoutMs: RUN_MS })
  } catch (error) {
    throw new Error(`gh auth status did not finish: ${error instanceof Error ? error.message : String(error)}`)
  }
  if (auth.exitCode !== 0) return empty('not-logged-in')
  let target = repo
  if (target === null) {
    let remote: string | undefined
    try {
      const origin = await $.process.run(['git', 'remote', 'get-url', 'origin'], { timeoutMs: RUN_MS })
      remote = origin.exitCode === 0 ? origin.stdout : undefined
    } catch {
      remote = undefined
    }
    target = remote === undefined ? null : repoFromRemote(remote)
    if (target === null) return empty('no-origin')
  }
  if (!REPO.test(target)) throw new Error(`not a GitHub repository: ${JSON.stringify(target)}`)
  const listed = await $.process.run(
    ['gh', 'issue', 'list', '--repo', target, '--assignee', '@me', '--state', 'open', '--limit', String(LIMIT), '--json', 'number,title,state,url'],
    { timeoutMs: RUN_MS },
  )
  if (listed.exitCode !== 0) throw new Error(`gh issue list failed: ${clip(listed.stderr) || `exit ${listed.exitCode}`}`)
  const parsed = parseIssues(listed.stdout, target)
  if (!parsed.ok) throw new Error(parsed.reason)
  if (parsed.rows.length === 0) return empty('none', target)
  return { rows: parsed.rows, limit: LIMIT, repo: target, ...(parsed.rows.length >= LIMIT ? { capped: true } : {}) }
}

// The one line a ready read with no rows draws.
export function emptyText(reason: IssueEmpty, repo?: string) {
  switch (reason) {
    case 'no-gh':
      return 'GitHub CLI (gh) not found. Install it from https://github.com/cli/cli, then press Refresh.'
    case 'not-logged-in':
      return 'gh is not logged in to github.com. Run `gh auth login`, then press Refresh.'
    case 'no-origin':
      return 'This folder has no GitHub `origin` remote, so there is no repository to list issues from.'
    case 'none':
      return `No open issues assigned to you in ${repo ?? 'this repository'}.`
  }
}

// The line under the list when gh returned a full page, so older issues are cut.
export const cutLine = () => `showing the first ${LIMIT}`

const checked = (repo: string, number: number) => {
  if (!REPO.test(repo) || !isIssueNumber(number)) throw new Error(`not an issue: ${JSON.stringify(`${repo}#${String(number)}`)}`)
}

// The `start_session` arguments. Only the checked repo and number enter the brief; the title, which
// anyone who can open an issue writes, goes only into the session's title.
export function startArgs(repo: string, number: number, title: string) {
  checked(repo, number)
  const ref = issueRef(repo, number)
  const url = issueUrl(repo, number)
  const readOnly = 'The issue stays read-only: no comments, labels, state changes or new issues.'

  return {
    initiation: 'own_initiative',
    context: 'fresh',
    title: `${ref}: ${shortTitle(title)}`,
    prompt:
      `Issue ${ref} (${url}) was started from the Rigger panel. ` +
      `Read it first with \`gh issue view ${number} --repo ${repo} --comments\`, then summarise it and propose a plan; ` +
      `do not change code before the user agrees. ${readOnly}`,
    background:
      `The user pressed Start on issue ${ref} (${url}) in the issue list of the Rigger panel; the session exists to work on that issue. ` +
      `The user agrees to a plan before any code changes. ${readOnly}`,
  }
}

// The `spawn_task` arguments: the same brief, proposed as a chip. `title` is under 60 characters.
export function spawnArgs(repo: string, number: number, title: string) {
  const { prompt } = startArgs(repo, number, title)
  const ref = issueRef(repo, number)
  const chars = Array.from(`${ref}: ${shortTitle(title)}`)

  return {
    title: chars.length > SPAWN_TITLE_CHARS ? `${chars.slice(0, SPAWN_TITLE_CHARS - 1).join('')}…` : chars.join(''),
    prompt,
    tldr: `Issue ${ref} was started from the panel; this session reads it first and changes no code until you agree to a plan.`,
  }
}

// What this session is asked when it has no start tool at all: work on the issue here, with the same
// brief a started session gets.
export function hereText(repo: string, number: number, title: string) {
  return `The user pressed "Do it here" on issue ${issueRef(repo, number)} in the Rigger panel. ${startArgs(repo, number, title).prompt}`
}

// The app's refusal of a mod's own `start_session` call outside bypass mode: it names the tool and says
// the call needs a person's approval. Only that part is matched, so the rest of the sentence may change.
const APPROVAL_REFUSAL = /mcp__ccd_session__start_session\b[\s\S]*needs a person['’]s approval/u

export const needsApproval = (text: string) => APPROVAL_REFUSAL.test(text)

// What the model is asked to do when a mod cannot start the session itself. The arguments it is
// handed are titled by the issue reference alone: the issue title, which anyone who can open an issue
// writes, reaches no text a model reads.
export function askText(repo: string, number: number, title: string) {
  const args = { ...startArgs(repo, number, title), title: issueRef(repo, number) }
  return (
    `The user pressed Start on issue ${issueRef(repo, number)} in the Rigger panel, and the panel could not start the session itself. ` +
    'Call mcp__ccd_session__start_session once with exactly these arguments and do nothing else: ' +
    JSON.stringify(args)
  )
}
