import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

const CONFIG = '/home/boss/.claude'
const DIR = `${CONFIG}/rigger-panel/inbox`
const SELF = {
  sessionId: 'local_abc',
  title: 'Alpha',
  cwd: '/work/alpha',
  link: 'claude://claude.ai/epitaxy/local_abc',
  isRunning: true,
}
const WRAP = 'mcp__ccd_turn__wrap_up'
// The engine hands a hook the path as the platform spells it (a drive and backslashes on Windows).
const posix = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')
const WRAP_TOOLS = ['WrapUp', WRAP] as const

type World = {
  writes: Record<string, string>
  status: (string | undefined)[]
  lookups: () => number
  clock: ReturnType<typeof mock.clock>
}

type Opts = {
  self?: 'ok' | 'error' | 'throw' | 'hang'
  failWrite?: boolean
  home?: string | undefined
  // What the tool beneath the plugin answers a wrap_up call with.
  wrapResult?: unknown
}

// The world beneath the plugin: the app answers get_session, files are recorded, the clock is fixed.
const world = (on: On, opts: Opts = {}): World => {
  const writes: Record<string, string> = {}
  const status: (string | undefined)[] = []
  let lookups = 0
  const home = 'home' in opts ? opts.home : '/home/boss'
  on('env.get', (_, e) => ({ value: e.name === 'HOME' ? home : undefined }))
  const clock = mock.clock(on, { now: 1_000_000 })
  on('mcp.call', (_, e) => {
    if (e.server === 'ccd_session_mgmt' && e.tool === 'get_session') {
      lookups += 1
      if (opts.self === 'throw') throw new Error('no app here')
      if (opts.self === 'hang') return new Promise<never>(() => {})
      if (opts.self === 'error') return { value: { content: [{ type: 'text', text: 'Unknown session' }], isError: true } }
      return { value: { content: [{ type: 'text', text: JSON.stringify(SELF) }], isError: false } }
    }
    return { deny: 'not in this test' }
  })
  on('fs.write', (_, e) => {
    if (opts.failWrite) return { deny: 'disk full' }
    writes[posix(e.path)] = e.text
    return { value: undefined }
  })
  on('ui.status', (_, e) => {
    status.push(e.text)
    return { value: undefined }
  })
  on('tool.call', (_, e) =>
    (WRAP_TOOLS as readonly string[]).includes(e.tool) && opts.wrapResult !== undefined
      ? (opts.wrapResult as never)
      : { result: { content: 'shown' } },
  )
  on('tool.check', (_, e) => ({ decision: (e.input as { needsOk?: boolean })?.needsOk ? 'ask' : 'allow' }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  // The panel's own figures, which it reads at the end of every turn.
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('prompt.submit', (_, e) => ({ text: e.text }))
  return { writes, status, lookups: () => lookups, clock }
}

const entryAt = (w: World) => JSON.parse(w.writes[`${DIR}/local_abc.json`] ?? 'null')
const closedAt = (w: World) => w.writes[`${DIR}/local_abc.closed`]
const turn = ($: Engine, text = 'go') => $.turn.start({ text, turnId: 't1' })
const complete = ($: Engine, reason: 'answer' | 'aborted' | 'error' | 'refusal') =>
  $.turn.complete({ answer: '', durationMs: 1, isAborted: reason === 'aborted', turnId: 't1', reason } as never)

for (const tool of WRAP_TOOLS) {
  test(`a ${tool} with a question writes the session entry`, async ($, on) => {
    const w = world(on)
    await turn($)
    await $.tool.call({ tool, recap: 'Spec ready', ask: 'Approve?', actions: [{ label: 'Yes' }], blocked: true } as never)
    await w.clock.settle()
    const e = entryAt(w)
    expect(e.kind).toBe('question')
    expect(e.ask).toBe('Approve?')
    expect(e.actions).toEqual(['Yes'])
    expect(e.title).toBe('Alpha')
    expect(e.link).toBe(SELF.link)
    expect(e.at).toBe(1_000_000)
  })
}

test("a WrapUp result's next-step box is written in place of the raw input", async ($, on) => {
  const nextStep = { recap: 'key [REDACTED] rotated', ask: 'Ship it?', blocked: true, actions: [{ label: 'Ship' }, { action: 'verify' }] }
  const w = world(on, { wrapResult: { result: { asks: true, nextStep } } })
  await turn($)
  await $.tool.call({ tool: 'WrapUp', recap: 'key sk-raw-secret rotated', ask: 'Ship it?', actions: [{ label: 'Ship' }] } as never)
  await w.clock.settle()
  const e = entryAt(w)
  expect(e.text).toBe('key [REDACTED] rotated')
  expect(e.ask).toBe('Ship it?')
  expect(e.actions).toEqual(['Ship'])
  expect(e.kind).toBe('question')
})

const BOX = { recap: 'from the box', blocked: false, actions: [] }
for (const [name, wrapResult] of [
  ['an error', { isError: true, result: { nextStep: BOX } }],
  ['no next-step box', { result: { asks: false } }],
  ['a next-step box that is no object', { result: { nextStep: 'x' } }],
] as const) {
  test(`a WrapUp result with ${name} writes the input`, async ($, on) => {
    const w = world(on, { wrapResult })
    await turn($)
    await $.tool.call({ tool: 'WrapUp', recap: 'from the input' } as never)
    await w.clock.settle()
    expect(entryAt(w).text).toBe('from the input')
  })
}

test('a wrap_up input is cleaned before it is written', async ($, on) => {
  const w = world(on)
  const esc = String.fromCharCode(27)
  await turn($)
  await $.tool.call({ tool: WRAP, recap: `done${esc}[2J now`, ask: 'a'.repeat(700) } as never)
  await w.clock.settle()
  const raw = w.writes[`${DIR}/local_abc.json`] ?? ''
  expect(raw.includes(esc)).toBe(false)
  expect(entryAt(w).text).toBe('done [2J now')
  expect(entryAt(w).ask).toHaveLength(600)
})

test('an aborted turn without wrap_up writes an error, with wrap_up it does not', async ($, on) => {
  const w = world(on)
  await turn($)
  await complete($, 'aborted')
  expect(entryAt(w).kind).toBe('error')
  expect(entryAt(w).text).toBe('turn aborted')

  delete w.writes[`${DIR}/local_abc.json`]
  await turn($, '')
  await $.tool.call({ tool: WRAP, recap: 'done' } as never)
  await w.clock.settle()
  await complete($, 'aborted')
  expect(entryAt(w).kind).toBe('review')
})

for (const [reason, text] of [
  ['error', 'turn ended with an error'],
  ['refusal', 'the model refused to answer'],
] as const) {
  test(`a turn ending with ${reason} writes "${text}"`, async ($, on) => {
    const w = world(on)
    await turn($)
    await complete($, reason)
    expect(entryAt(w).text).toBe(text)
  })
}

test('a turn that answers writes no entry', async ($, on) => {
  const w = world(on)
  await turn($)
  await complete($, 'answer')
  expect(entryAt(w)).toBeNull()
})

test('a permission ask writes a permission entry, an allow writes nothing', async ($, on) => {
  const w = world(on)
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: false } })
  await w.clock.settle()
  expect(entryAt(w)).toBeNull()
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  const e = entryAt(w)
  expect(e.kind).toBe('permission')
  expect(e.text).toBe('waiting for approval: Bash')
})

test('a permission entry left by a turn that ends without wrap_up is closed', async ($, on) => {
  const w = world(on)
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  expect(closedAt(w)).toBeUndefined()
  await complete($, 'answer')
  expect(closedAt(w)).toBe('1000000')
})

test('a permission entry closes as soon as the approved tool runs; one that never runs stays', async ($, on) => {
  const w = world(on)
  await turn($, '')
  await $.tool.call({ tool: 'Read', input: {} } as never)
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  expect(closedAt(w)).toBeUndefined()
  await $.tool.call({ tool: 'Bash', input: {} } as never)
  await w.clock.settle()
  expect(closedAt(w)).toBe('1000000')
})

// Only the owner's own prompt answers the entry: a message from another session, a task's
// notification, a schedule or a plugin's prompt starts a turn too, and leaves the entry open.
for (const [kind, isOwner] of [
  ['composer', true],
  ['sdk', true],
  ['bridge', true],
  ['slack-ping', true],
  ['auto-continuation', true],
  ['peer-send-message', false],
  ['peer', false],
  ['task-notification', false],
  ['unclassified', false],
  ['scheduled-trigger', false],
  ['projects-relay', false],
  ['channel', false],
  ['coordinator', false],
  ['observer', false],
  ['observer-activity', false],
  ['plugin', false],
] as const) {
  test(`a prompt from origin ${kind} ${isOwner ? 'closes' : 'leaves'} the own entry`, async ($, on) => {
    const w = world(on)
    await turn($, '')
    expect(closedAt(w)).toBeUndefined()
    await $.prompt.submit({ text: 'yes, go on', wait: false, origin: { kind } } as never)
    await turn($, 'yes, go on')
    expect(closedAt(w)).toBe(isOwner ? '1000000' : undefined)
  })
}

test('a subagent writes nothing', async ($, on) => {
  const w = world(on)
  await $.turn.start({ text: 'brief', turnId: 't2', agentId: 'a1' })
  await $.tool.check({ tool: 'Bash', input: { needsOk: true }, agentId: 'a1' } as never)
  await $.tool.call({ tool: WRAP, recap: 'r', agentId: 'a1' } as never)
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: true, turnId: 't2', reason: 'aborted', agentId: 'a1' } as never)
  await w.clock.settle()
  expect(Object.keys(w.writes)).toEqual([])
})

for (const self of ['error', 'throw'] as const) {
  test(`a get_session that answers ${self} writes nothing, says nothing and leaves wrap_up untouched`, async ($, on) => {
    const w = world(on, { self })
    await turn($)
    const result = await $.tool.call({ tool: WRAP, recap: 'r', ask: 'q' } as never)
    await w.clock.settle()
    await complete($, 'aborted')
    expect(Object.keys(w.writes)).toEqual([])
    expect(w.status).toEqual([])
    expect(result).toEqual({ result: { content: 'shown' } })
  })
}

test('a failed get_session is asked again at most once per turn', async ($, on) => {
  const w = world(on, { self: 'error' })
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  await $.tool.call({ tool: WRAP, recap: 'r' } as never)
  await w.clock.settle()
  await complete($, 'aborted')
  expect(w.lookups()).toBe(1)
  await turn($, '')
  await complete($, 'aborted')
  expect(w.lookups()).toBe(2)
})

test('a get_session that never answers is given up after 5 s and asked again the next turn', async ($, on) => {
  const w = world(on, { self: 'hang' })
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.advance(5000)
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  expect(w.lookups()).toBe(2)
})

test('an identity the app gave is asked for once, across hooks and turns', async ($, on) => {
  const w = world(on)
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  await $.tool.call({ tool: 'Bash', input: {} } as never)
  await w.clock.settle()
  await complete($, 'aborted')
  await turn($, '')
  await $.tool.call({ tool: WRAP, recap: 'r' } as never)
  await w.clock.settle()
  expect(w.lookups()).toBe(1)
  expect(entryAt(w).kind).toBe('review')
})

test('an identity read at session start is not asked for again by the first entry', async ($, on) => {
  const w = world(on)
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('fs.list', () => ({ deny: 'missing' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await w.clock.settle()
  await turn($, '')
  await $.tool.call({ tool: WRAP, recap: 'r' } as never)
  await w.clock.settle()
  expect(entryAt(w).kind).toBe('review')
  expect(w.lookups()).toBe(1)
})

test("a subagent's turn does not reset the main turn's open permission entry", async ($, on) => {
  const w = world(on)
  await turn($, '')
  await $.tool.check({ tool: 'Bash', input: { needsOk: true } })
  await w.clock.settle()
  await $.turn.start({ text: 'brief', turnId: 't2', agentId: 'a1' })
  await $.tool.call({ tool: 'Bash', input: {} } as never)
  await w.clock.settle()
  expect(closedAt(w)).toBe('1000000')
})

test('without a config folder get_session is never asked and nothing is written', async ($, on) => {
  const w = world(on, { home: undefined })
  await turn($)
  await $.tool.call({ tool: WRAP, recap: 'r' } as never)
  await w.clock.settle()
  await complete($, 'aborted')
  expect(w.lookups()).toBe(0)
  expect(Object.keys(w.writes)).toEqual([])
})

for (const self of ['error', 'hang'] as const) {
  test(`a session start whose get_session answers ${self} does not wait on it and says nothing`, async ($, on) => {
    const w = world(on, { self })
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    on('command.register', (_, e) => ({ value: { command: e.name } }))
    on('ui.open', () => ({ value: { isPlaced: true } }))
    on('fs.list', () => ({ deny: 'missing' }))
    let isStarted = false
    const started = $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true }).then(() => {
      isStarted = true
    })
    await w.clock.settle()
    expect(isStarted).toBe(true)
    await w.clock.advance(5000)
    await started
    expect(w.status).toEqual([])
  })
}

test('a failing file write says so and does not change what wrap_up returns', async ($, on) => {
  const w = world(on, { failWrite: true })
  await turn($)
  const result = await $.tool.call({ tool: WRAP, recap: 'r' } as never)
  await w.clock.settle()
  expect(result).toEqual({ result: { content: 'shown' } })
  expect(w.status.filter(text => text?.startsWith('rigger-panel inbox: entry not saved (') && text.includes('disk full'))).toHaveLength(1)
})

// A get_session that never answers must neither hold what the hook returns nor leave the write pending.
for (const [name, dispatch] of [
  ['wrap_up', ($: Engine) => $.tool.call({ tool: WRAP, recap: 'r', ask: 'q' } as never)],
  ['WrapUp', ($: Engine) => $.tool.call({ tool: 'WrapUp', recap: 'r', ask: 'q' } as never)],
  ['a permission ask', ($: Engine) => $.tool.check({ tool: 'Bash', input: { needsOk: true } })],
] as const) {
  test(`${name} returns at once while get_session hangs, and gives up after 5 s in silence`, async ($, on) => {
    const w = world(on, { self: 'hang' })
    await turn($)
    let isReturned = false
    const done = dispatch($).then(result => {
      isReturned = true
      return result
    })
    await w.clock.settle()
    expect(isReturned).toBe(true)
    await w.clock.advance(5000)
    await done
    expect(Object.keys(w.writes)).toEqual([])
    expect(w.status).toEqual([])
  })
}
