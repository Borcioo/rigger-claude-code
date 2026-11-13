import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import {
  CLOSE_TOOL,
  COVER_MS,
  EMPTY_AGE,
  PLAN_TOOL,
  ageLine,
  applyPlan,
  closePlan,
  isCommit,
  nudgeText,
  planIcon,
  planMark,
  staleLine,
  touchAge,
  withEvidence,
} from './plan'

const STEPS = [
  { content: 'one', status: 'in_progress', session: 'first' },
  { content: 'two', status: 'pending' },
  { content: 'three', status: 'pending' },
] as const

test('items replace the list and keep the session name', async () => {
  const next = applyPlan([{ content: 'old', status: 'completed' }], {
    items: [{ content: 'new', status: 'in_progress', session: 'mx' }, { content: 'bad', status: 'odd' }],
  })
  expect(next).toEqual([
    { content: 'new', status: 'in_progress', session: 'mx' },
    { content: 'bad', status: 'pending' },
  ])
})

test('advance completes the running step and starts the next pending one', async () => {
  expect(applyPlan([...STEPS], { advance: true }).map(step => step.status)).toEqual(['completed', 'in_progress', 'pending'])
})

test('advance at the end leaves nothing running', async () => {
  const last = [{ content: 'one', status: 'completed' }, { content: 'two', status: 'in_progress' }] as const
  expect(applyPlan([...last], { advance: true }).map(step => step.status)).toEqual(['completed', 'completed'])
})

test('done and start count from one, out of order, and a number past the list is ignored', async () => {
  const next = applyPlan([...STEPS], { done: [2, 9], start: 3 })
  expect(next.map(step => step.status)).toEqual(['in_progress', 'completed', 'in_progress'])
})

test('wrap_up checks off every step, the ones not started included', async () => {
  expect(closePlan([...STEPS], 0).map(step => step.status)).toEqual(['completed', 'completed', 'completed'])
})

test('wrap_up leaves running steps open while an agent runs, and checks off the rest', async () => {
  const list = [
    { content: 'one', status: 'in_progress' },
    { content: 'two', status: 'completed' },
    { content: 'three', status: 'in_progress' },
    { content: 'four', status: 'pending' },
  ] as const
  expect(closePlan([...list], 2).map(step => step.status)).toEqual(['in_progress', 'completed', 'in_progress', 'completed'])
})

test('each status has its own mark', async () => {
  expect(new Set(['pending', 'in_progress', 'completed'].map(s => planMark(s as 'pending'))).size).toBe(3)
})

test('only the running step spins', async () => {
  expect(planIcon('in_progress')).toContain('animateTransform')
  expect(planIcon('completed')).not.toContain('animateTransform')
  expect(planIcon('pending')).not.toContain('animateTransform')
})

// --- the list's age and the evidence that it is behind ---

const MINUTE = 60_000
const T0 = 1_800_000_000_000
const OPEN = [{ content: 'one', status: 'completed' }, { content: 'two', status: 'pending' }] as const
const FRESH = { updatedAt: T0, touched: true, nudged: false } as const

test('a command that runs git commit is told from one that only mentions it', async () => {
  const verdicts = {
    'git commit -m x': true,
    'cd a && git -C "b c" commit -am x': true,
    'git add . ; git commit -F msg.txt': true,
    'git -c user.name=x commit --amend --no-edit': true,
    'git log --grep commit': false,
    'git commit --dry-run': false,
    'git commit-tree abc': false,
    'svn commit -m x': false,
    "git -C 'b c' commit -m x": true,
    'gh pr create --title "x"': false,
  }
  expect(Object.fromEntries(Object.keys(verdicts).map(command => [command, isCommit(command)]))).toEqual(verdicts)
  expect(isCommit(42)).toBe(false)
  expect(isCommit('  git commit -m x')).toBe(true)
})

test('a long run of options is read in linear time', async () => {
  const start = performance.now()
  expect(isCommit(`git${' --a'.repeat(40)} status`)).toBe(false)
  expect(isCommit(`git${' --a'.repeat(40)} commit -m x`)).toBe(true)
  expect(isCommit(`git${' -c'.repeat(40)} status`)).toBe(false)
  expect(isCommit(`git${' -c a=b'.repeat(40)} commit`)).toBe(true)
  expect(isCommit(`${'x '.repeat(20_000)}; git status`)).toBe(false)
  expect(performance.now() - start).toBeLessThan(200)
})

test('a dry-run flag inside the commit message does not make it a dry run', async () => {
  expect(isCommit('git commit -m "support the --dry-run flag"')).toBe(true)
  expect(isCommit("git commit -m 'x' --dry-run")).toBe(false)
})

test('only a commit is covered by a list update just before it', async () => {
  expect(withEvidence(FRESH, [...OPEN], 'task', T0 + 1000).evidence).toEqual({ kind: 'task', at: T0 + 1000 })
})

test('evidence later than the list is kept while a step is still open', async () => {
  expect(withEvidence(FRESH, [...OPEN], 'commit', T0 + MINUTE).evidence).toEqual({ kind: 'commit', at: T0 + MINUTE })
})

test('a list never updated here, or with every step done, takes no evidence', async () => {
  expect(withEvidence(EMPTY_AGE, [...OPEN], 'commit', T0).evidence).toBeUndefined()
  expect(withEvidence(FRESH, [{ content: 'one', status: 'completed' }], 'commit', T0 + MINUTE).evidence).toBeUndefined()
})

test('an update during the command or up to two seconds before it covers the commit', async () => {
  const at = T0 + MINUTE
  expect(withEvidence(FRESH, [...OPEN], 'commit', at, T0 + COVER_MS).evidence).toBeUndefined()
  expect(withEvidence(FRESH, [...OPEN], 'commit', at, T0 - 500).evidence).toBeUndefined()
  expect(withEvidence(FRESH, [...OPEN], 'commit', at, T0 + COVER_MS + 1).evidence?.kind).toBe('commit')
})

test('a turn end counts only with a running step and never replaces other evidence', async () => {
  expect(withEvidence(FRESH, [...OPEN], 'turn', T0 + MINUTE).evidence).toBeUndefined()
  expect(withEvidence(FRESH, [...STEPS], 'turn', T0 + MINUTE).evidence?.kind).toBe('turn')
  const committed = withEvidence(FRESH, [...STEPS], 'commit', T0 + MINUTE)
  expect(withEvidence(committed, [...STEPS], 'turn', T0 + 2 * MINUTE).evidence).toEqual({ kind: 'commit', at: T0 + MINUTE })
  expect(withEvidence(committed, [...STEPS], 'task', T0 + 2 * MINUTE).evidence).toEqual({ kind: 'task', at: T0 + 2 * MINUTE })
})

test('a list update sets the time and clears evidence and the reminder mark', async () => {
  expect(touchAge(T0 + 5)).toEqual({ updatedAt: T0 + 5, touched: true, nudged: false })
})

test('the age line says the time and how long ago', async () => {
  expect(ageLine(EMPTY_AGE, T0)).toBeUndefined()
  expect(ageLine(FRESH, T0 + 59_000)).toMatch(/^Updated [0-9]{2}:[0-9]{2} \(just now\)$/)
  expect(ageLine(FRESH, T0 + 12 * MINUTE + 30_000)).toMatch(/^Updated [0-9]{2}:[0-9]{2} \(12 min ago\)$/)
  expect(ageLine(FRESH, T0 + 125 * MINUTE)).toMatch(/^Updated [0-9]{2}:[0-9]{2} \(2 h ago\)$/)
})

test('the stale line names the evidence, and there is none without evidence', async () => {
  expect(staleLine(FRESH)).toBeUndefined()
  const line = (kind: 'commit' | 'task' | 'turn') => staleLine({ ...FRESH, evidence: { kind, at: T0 } })
  expect(line('commit')).toMatch(/^May be out of date: a commit at [0-9]{2}:[0-9]{2}$/)
  expect(line('task')).toMatch(/^May be out of date: a background task finished at [0-9]{2}:[0-9]{2}$/)
  expect(line('turn')).toMatch(/^May be out of date: a turn ended at [0-9]{2}:[0-9]{2} with no update$/)
})

test('the reminder names the event and asks only to match the current state', async () => {
  const texts = (['commit', 'task', 'turn'] as const).map(kind => nudgeText({ kind, at: T0 }))
  expect(texts).toEqual([
    'The task list was last updated before the last commit; update it to match the current state.',
    'The task list was last updated before a background task finished; update it to match the current state.',
    'The task list was last updated before the previous turn ended; update it to match the current state.',
  ])
})

test('a running step that may be stale does not spin', async () => {
  expect(planIcon('in_progress', true)).not.toContain('animateTransform')
  expect(planIcon('in_progress', true)).toContain('<circle')
})

const PANE = {
  plugin: 'rigger-panel',
  component: 'Pane',
  requestId: 'rigger-plan',
  surface: 'desktop',
  props: { title: 'Plan', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

test('all four panes open at start, and Plan keeps the steps', async ($, on) => {
  const opened: string[] = []
  on('env.get', () => ({ value: undefined }))
  on('command.register', () => ({ value: { command: 'harness' } }))
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  mock.clock(on, { now: 1000 })
  on('mcp.call', () => ({ deny: 'no MCP server in this test' }))
  on('process.run', () => ({ deny: 'no gh in this test' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('fs.read', () => ({ deny: 'missing' }))
  on('tool.call', () => ({ result: {} }))

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect([...opened].sort()).toEqual(['rigger-envs', 'rigger-issues', 'rigger-panel', 'rigger-plan'])
  const empty = await $.ui.mount(PANE)
  expect((await empty.find({ type: 'Text', text: /No plan/ }))?.text).toBe('No plan.')
  await empty.unmount()

  await $.tool.call({ tool: PLAN_TOOL, items: [{ content: 'Task 1', status: 'in_progress', session: 'docs-fix' }] } as never)

  const ui = await $.ui.mount(PANE)
  expect((await ui.find({ type: 'Text', text: /Task 1/ }))?.text).toBe('Task 1')
  expect((await ui.find({ type: 'Text', text: /docs-fix/ }))?.text).toBe('docs-fix')
  expect(await ui.find({ type: 'Svg' })).toBeDefined()
  await ui.unmount()

  expect(((await (await $.ui.mount(PANE)).drawn()) as { props?: { rowGap?: number } }).props?.rowGap).toBe(1)

  const text = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await text.find({ type: 'Svg' })).toBeUndefined()
  expect(await text.find({ type: 'Text', text: /▶/ })).toBeDefined()
  expect(((await text.drawn()) as { props?: { rowGap?: number } }).props?.rowGap).toBe(0)
  await text.unmount()
})

// --- the pane says how old the list is, and the session is reminded ---

type ShellAnswer = { result: object; isError?: true; context?: string[] } | { deny: string }
type World = {
  // What the shell tools answer.
  shell?: () => ShellAnswer | Promise<ShellAnswer>
  // A reason makes the engine refuse the prompt.
  drop?: () => string | undefined
  // What `$.agent.list()` answers; undefined makes the call fail.
  agents?: () => { status: string }[] | undefined
  // The time the test's own clock reads instead of the mocked one; undefined makes the read fail.
  ownClock?: () => number | undefined
}

// A session with a clock at T0; returns the clock.
const session = async (
  $: Engine,
  on: On,
  { shell = () => ({ result: {} }), drop = () => undefined, ownClock, agents = () => [] }: World = {},
) => {
  on('env.get', () => ({ value: undefined }))
  on('command.register', () => ({ value: { command: 'harness' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  const clock = ownClock ? undefined : mock.clock(on, { now: T0 })
  if (ownClock) {
    on('clock.now', () => {
      const now = ownClock()
      return now === undefined ? { deny: 'clock down' } : { value: now }
    })
    on('clock.every', () => ({ deny: 'no timer in this test' }))
  }
  on('mcp.call', () => ({ deny: 'no MCP server in this test' }))
  on('process.run', () => ({ deny: 'no gh in this test' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('fs.read', () => ({ deny: 'missing' }))
  on('agent.list', () => {
    const list = agents()
    return list === undefined ? { deny: 'agents down' } : { value: list as never }
  })
  on('tool.call', async (_, e) => ((e.tool === 'Bash' || e.tool === 'PowerShell' ? await shell() : { result: {} }) as never))
  on('prompt.submit', (_, e) => {
    const reason = drop()
    return reason === undefined ? { text: e.text, context: e.context } : { drop: reason }
  })
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: 'answer' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  return clock
}

// The same session on the mocked clock, which the test then moves.
const mocked = async ($: Engine, on: On, world: Omit<World, 'ownClock'> = {}) => {
  const clock = await session($, on, world)
  if (clock === undefined) throw new Error('the mocked clock was not set up')
  return clock
}

const RUNNING = [{ content: 'Task 2', status: 'in_progress' }, { content: 'Task 3', status: 'pending' }]
const setPlan = ($: Engine, items: object[] = RUNNING) => $.tool.call({ tool: PLAN_TOOL, items } as never)
const shellCall = ($: Engine, command: string, tool = 'Bash', agentId?: string) =>
  $.tool.call({ tool, command, ...(agentId ? { agentId } : {}) } as never) as Promise<{ context?: string[] }>
const prompt = ($: Engine, kind = 'composer', text = 'next') =>
  $.prompt.submit({ text, wait: false, origin: { kind } } as never) as Promise<{ context?: string[] }>
const turnEnd = ($: Engine, agentId?: string) =>
  $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't', reason: 'answer', ...(agentId ? { agentId } : {}) } as never)
const turnStart = ($: Engine, agentId?: string) => $.turn.start({ text: '', turnId: 't', ...(agentId ? { agentId } : {}) } as never)

const paneTexts = async ($: Engine, surface: 'desktop' | 'terminal' = 'desktop') => {
  const ui = await $.ui.mount({ ...PANE, surface })
  const texts = (await ui.findAll({ type: 'Text' })).map(found => found.text ?? '')
  const tree = JSON.stringify(await ui.drawn())
  await ui.unmount()
  return { tree, age: texts.find(text => text.startsWith('Updated')), stale: texts.find(text => text.startsWith('May be')) }
}

const COMMIT_NUDGE = 'The task list was last updated before the last commit; update it to match the current state.'

test('the pane shows when the list was last updated, and nothing without a list', async ($, on) => {
  const clock = await mocked($, on)
  expect((await paneTexts($)).age).toBeUndefined()
  await setPlan($)
  await clock.advance(12 * MINUTE)
  const drawn = await paneTexts($)
  expect(drawn.age).toMatch(/^Updated [0-9]{2}:[0-9]{2} \(12 min ago\)$/)
  expect(drawn.stale).toBeUndefined()
  expect(drawn.tree).toContain('animateTransform')
  await setPlan($, [])
  expect((await paneTexts($)).age).toBeUndefined()
})

test('wrap_up counts as a list update', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(30 * MINUTE)
  await $.tool.call({ tool: CLOSE_TOOL, recap: 'x' } as never)
  expect((await paneTexts($)).age).toMatch(/just now/)
})

for (const tool of ['Bash', 'PowerShell']) {
  test(`a commit through ${tool} after the last update marks the list as possibly stale`, async ($, on) => {
    const clock = await mocked($, on)
    await setPlan($)
    await clock.advance(5 * MINUTE)
    await shellCall($, 'git add . && git commit -m "task 3"', tool)
    const drawn = await paneTexts($)
    expect(drawn.stale).toMatch(/^May be out of date: a commit at [0-9]{2}:[0-9]{2}$/)
    expect(drawn.tree).not.toContain('animateTransform')
    expect((await paneTexts($, 'terminal')).stale).toMatch(/a commit at/)
  })
}

test('a subagent commit marks the list too', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x', 'Bash', 'agent-1')
  expect((await paneTexts($)).stale).toMatch(/a commit at/)
})

test('a commit that failed or was refused is no evidence', async ($, on) => {
  let answer: ShellAnswer = { result: {}, isError: true }
  const clock = await mocked($, on, { shell: () => answer })
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  answer = { deny: 'blocked' }
  await shellCall($, 'git commit -m x').catch(() => undefined)
  expect((await paneTexts($)).stale).toBeUndefined()
})

test('a shell command that is not a commit is no evidence', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git log --grep commit')
  expect((await paneTexts($)).stale).toBeUndefined()
})

test('a finished background task marks the list as possibly stale', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await prompt($, 'task-notification')
  expect((await paneTexts($)).stale).toMatch(/^May be out of date: a background task finished at [0-9]{2}:[0-9]{2}$/)
})

// The notification a subagent sends when it stops to wait for its own background work.
const INTERIM =
  '<task-notification><task-id>a1</task-id><status>completed</status>' +
  '<summary>Agent "Gate" is waiting on its own background work</summary></task-notification>'
const FINISHED = '<task-notification><task-id>a1</task-id><status>completed</status><summary>Agent "Gate" finished</summary></task-notification>'

test('a subagent that waits for its own background work has not finished', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  expect((await prompt($, 'task-notification', INTERIM)).context).toBeUndefined()
  expect((await paneTexts($)).stale).toBeUndefined()
  await prompt($, 'task-notification', FINISHED)
  expect((await paneTexts($)).stale).toMatch(/a background task finished/)
})

// The marks the pane draws, in list order.
const marks = async ($: Engine) => {
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const found = (await ui.findAll({ type: 'Text' })).map(node => node.text ?? '').filter(text => ['✓', '▶', '○'].includes(text))
  await ui.unmount()
  return found.join('')
}
const WRAP = [
  { content: 'one', status: 'in_progress' },
  { content: 'two', status: 'completed' },
  { content: 'three', status: 'in_progress' },
  { content: 'four', status: 'pending' },
]

test('wrap_up keeps the steps open while subagents run and checks off the rest', async ($, on) => {
  await session($, on, { agents: () => [{ status: 'running' }, { status: 'running' }] })
  await setPlan($, WRAP)
  await $.tool.call({ tool: CLOSE_TOOL, recap: 'x' } as never)
  expect(await marks($)).toBe('▶✓▶✓')
})

test('wrap_up checks off a step whose subagent only waits on its own background work', async ($, on) => {
  await session($, on, { agents: () => [{ status: 'waiting' }, { status: 'idle' }, { status: 'completed' }] })
  await setPlan($, [{ content: 'one', status: 'completed' }, { content: 'two', status: 'in_progress' }, { content: 'three', status: 'pending' }])
  await $.tool.call({ tool: CLOSE_TOOL, recap: 'x' } as never)
  expect(await marks($)).toBe('✓✓✓')
})

test('wrap_up still checks the list off when the agent list cannot be read', async ($, on) => {
  await session($, on, { agents: () => undefined })
  await setPlan($)
  await $.tool.call({ tool: CLOSE_TOOL, recap: 'x' } as never)
  expect(await marks($)).toBe('✓✓')
})

test('a prompt the user typed is no evidence', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await prompt($)
  expect((await paneTexts($)).stale).toBeUndefined()
})

test('a turn that ends without a list update marks a running step as possibly stale', async ($, on) => {
  const clock = await mocked($, on)
  await turnStart($)
  await setPlan($)
  await turnEnd($)
  expect((await paneTexts($)).stale).toBeUndefined()
  await clock.advance(5 * MINUTE)
  await turnStart($)
  await turnEnd($)
  expect((await paneTexts($)).stale).toMatch(/^May be out of date: a turn ended at [0-9]{2}:[0-9]{2} with no update$/)
})

test('a subagent turn neither marks the list nor hides that the main turn updated it', async ($, on) => {
  const clock = await mocked($, on)
  await turnStart($)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await turnStart($, 'agent-1')
  await turnEnd($, 'agent-1')
  await turnEnd($)
  expect((await paneTexts($)).stale).toBeUndefined()
  // The main turn has not touched the list yet; a subagent ending inside it says nothing about it.
  await turnStart($)
  await turnEnd($, 'agent-1')
  expect((await paneTexts($)).stale).toBeUndefined()
})

test('an interrupted turn is no evidence', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await turnStart($)
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: true, turnId: 't', reason: 'aborted' } as never)
  expect((await paneTexts($)).stale).toBeUndefined()
})

test('updating the list clears the stale mark', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  expect((await paneTexts($)).stale).toMatch(/a commit at/)
  await $.tool.call({ tool: PLAN_TOOL, advance: true } as never)
  const drawn = await paneTexts($)
  expect(drawn.stale).toBeUndefined()
  expect(drawn.tree).toContain('animateTransform')
})

test('a commit sent with the list update is covered by it', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(COVER_MS)
  expect((await shellCall($, 'git commit -m x')).context).toBeUndefined()
  expect((await paneTexts($)).stale).toBeUndefined()
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m y')
  expect((await paneTexts($)).stale).toMatch(/a commit at/)
})

test('a list update that follows the commit in the same message leaves no reminder', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  expect((await shellCall($, 'git commit -m x')).context).toBeUndefined()
  await $.tool.call({ tool: PLAN_TOOL, advance: true } as never)
  expect((await shellCall($, 'git status')).context).toBeUndefined()
  expect((await prompt($)).context).toBeUndefined()
})

test('a commit the list is still behind is named once, on the next main-loop shell call', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  expect((await shellCall($, 'git status')).context).toBeUndefined()
  expect((await shellCall($, 'git commit -m x')).context).toBeUndefined()
  expect((await shellCall($, 'git status', 'Bash', 'agent-1')).context).toBeUndefined()
  expect((await shellCall($, 'git status', 'PowerShell')).context).toEqual([COMMIT_NUDGE])
  expect((await shellCall($, 'git status')).context).toBeUndefined()
})

test('a second commit carries the reminder for the first', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  expect((await shellCall($, 'git commit -m y')).context).toEqual([COMMIT_NUDGE])
  expect((await shellCall($, 'git commit -m z')).context).toBeUndefined()
})

test('a shell call that was refused does not use up the reminder', async ($, on) => {
  let answer: ShellAnswer = { result: {} }
  const clock = await mocked($, on, { shell: () => answer })
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  answer = { deny: 'blocked' }
  await shellCall($, 'git status').catch(() => undefined)
  answer = { result: {} }
  expect((await shellCall($, 'git status')).context).toEqual([COMMIT_NUDGE])
})

test('a commit with nothing behind it carries no reminder', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($, [{ content: 'Task 1', status: 'completed' }])
  await clock.advance(5 * MINUTE)
  expect((await shellCall($, 'git commit -m x')).context).toBeUndefined()
})

test('the next prompt carries the reminder once per stale period', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  expect((await prompt($)).context).toBeUndefined()
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x', 'Bash', 'agent-1')
  expect((await prompt($)).context).toEqual([COMMIT_NUDGE])
  expect((await prompt($)).context).toBeUndefined()
  await setPlan($)
  await clock.advance(5 * MINUTE)
  expect((await prompt($, 'task-notification')).context).toEqual([
    'The task list was last updated before a background task finished; update it to match the current state.',
  ])
})

test('a reminder on a shell result is not repeated at the next prompt, and the prompt carries it otherwise', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  await shellCall($, 'git status')
  expect((await prompt($)).context).toBeUndefined()
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x')
  expect((await prompt($)).context).toEqual([COMMIT_NUDGE])
  expect((await shellCall($, 'git status')).context).toBeUndefined()
})

test('the reminder keeps the context another hook attached', async ($, on) => {
  const clock = await mocked($, on)
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x', 'Bash', 'agent-1')
  const sent = (await $.prompt.submit({ text: 'next', wait: false, origin: { kind: 'composer' }, context: ['theirs'] } as never)) as {
    context?: string[]
  }
  expect(sent.context).toEqual(['theirs', COMMIT_NUDGE])
})

test('the reminder on a shell result keeps the context another hook attached', async ($, on) => {
  const clock = await mocked($, on, { shell: () => ({ result: {}, context: ['theirs'] }) })
  await setPlan($)
  await clock.advance(5 * MINUTE)
  expect((await shellCall($, 'git commit -m x')).context).toEqual(['theirs'])
  expect((await shellCall($, 'git status')).context).toEqual(['theirs', COMMIT_NUDGE])
})

test('a prompt the engine refused does not use up the reminder', async ($, on) => {
  let reason: string | undefined = 'refused'
  const clock = await mocked($, on, { drop: () => reason })
  await setPlan($)
  await clock.advance(5 * MINUTE)
  await shellCall($, 'git commit -m x', 'Bash', 'agent-1')
  await prompt($).catch(() => undefined)
  reason = undefined
  expect((await prompt($)).context).toEqual([COMMIT_NUDGE])
})

test('a failure while noting progress costs neither the tool result, the prompt nor the answer', async ($, on) => {
  let now: number | undefined = T0
  await session($, on, { shell: () => ({ result: { marker: 'kept' } }), ownClock: () => now })
  await setPlan($)
  now = T0 + 5 * MINUTE
  expect((await shellCall($, 'git commit -m x', 'Bash', 'agent-1')).context).toBeUndefined()
  expect((await paneTexts($)).stale).toMatch(/a commit at/)
  now = undefined
  expect(await $.tool.call({ tool: 'Bash', command: 'git commit -m x' } as never)).toMatchObject({ result: { marker: 'kept' } })
  expect(await prompt($, 'task-notification')).toMatchObject({ text: 'next' })
  await turnStart($)
  expect(await turnEnd($)).toMatchObject({ text: 'answer' })
})

test('a slow commit is judged by when its command started', async ($, on) => {
  let clock: Awaited<ReturnType<typeof session>>
  clock = await session($, on, {
    shell: async () => {
      await clock?.sleep(5 * MINUTE)
      return { result: {} }
    },
  })
  await setPlan($)
  const call = shellCall($, 'npm test && git commit -m x')
  await clock?.advance(5 * MINUTE)
  await call
  expect((await paneTexts($)).stale).toBeUndefined()
})