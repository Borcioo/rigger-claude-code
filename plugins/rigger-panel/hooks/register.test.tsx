import type { On, SessionUsage } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

const PANE = {
  plugin: 'rigger-panel',
  component: 'Pane',
  requestId: 'rigger-panel',
  props: {
    title: 'Rigger',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const CONFIG = '/Users/boss/.claude'
const FILES: Record<string, string> = {
  [`${CONFIG}/plugins/installed_plugins.json`]:
    '{"plugins":{"rigger-core@rigger":[{"scope":"user","version":"49e57fe8958e"}]}}',
  [`${CONFIG}/lessons-inbox.md`]: '# Inbox\n- 2026-11-05 · global · a\n- 2026-11-05 · global · b\nnot an entry\n',
  [`${CONFIG}/state/compact-guard.log`]:
    '2026-11-04T11:00:00+00:00 aaa 100000 allow safe\n2026-11-04T12:36:50+00:00 bbb 288149 block coordinator\n',
}

// The world beneath the plugin: a session, a clock, usage figures, an environment and files that
// can change between steps. Every path the plugin reads is recorded in `reads`.
const FULL_USAGE: SessionUsage = {
  startedAt: 0,
  context: { tokens: 495000, window: 533000, percent: 93 },
  rateLimits: [{ kind: 'seven_day', percentUsed: 4 }],
  cost: { usd: 12.5 },
}

const world = (
  on: On,
  env: Record<string, string>,
  files: Record<string, string>,
  usage: SessionUsage = FULL_USAGE,
) => {
  const reads: string[] = []
  on('env.get', (_, e) => ({ value: env[e.name] }))
  on('command.register', () => ({ value: { command: 'harness' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  mock.clock(on, { now: 1000 })
  on('mcp.call', () => ({ deny: 'no MCP server in this test' }))
  on('process.run', () => ({ deny: 'no gh in this test' }))
  on('session.usage', () => ({ value: usage }))
  on('fs.read', (_, e) => {
    // The environments file is read from the project, not the config dir these tests count.
    if (!e.path.includes('environments.json')) reads.push(e.path)
    const text = files[tail(e.path)]
    if (text === undefined) return { deny: 'missing' }
    return { value: text }
  })
  return reads
}

test('the pane draws the session figures, the English labels and the harness files', async ($, on) => {
  world(on, { CLAUDE_CONFIG_DIR: CONFIG }, FILES)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  for (const word of [/495k \/ 533k \(93%\)/, /\$12\.50/, /^4%$/, /49e57fe/, /^2$/, /block coordinator at 288k/]) {
    const row = await ui.find({ type: 'Text', text: word })
    expect(row?.text).toMatch(word)
  }
  for (const label of ['Context', 'Session cost', 'Weekly limit', 'Agents in session', 'Rigger', 'Lessons in inbox', 'Compaction']) {
    expect((await ui.find({ type: 'Text', text: new RegExp(`^${label}$`) }))?.text).toBe(label)
  }
  await ui.unmount()
})

test('HARNESS_LESSONS_INBOX points the lesson count at a shared inbox', async ($, on) => {
  const shared = '/shared/inbox/lessons-inbox.md'
  const reads = world(on, { CLAUDE_CONFIG_DIR: CONFIG, HARNESS_LESSONS_INBOX: shared }, FILES)

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)

  expect(reads.some(path => path.replaceAll('\\', '/').endsWith('/shared/inbox/lessons-inbox.md'))).toBe(true)
  expect(reads.filter(path => path.includes('lessons-inbox')).every(path => path.includes('shared'))).toBe(true)
})

test('a missing file draws as a question mark, not an error', async ($, on) => {
  world(on, { CLAUDE_CONFIG_DIR: CONFIG }, {}, { startedAt: 0, context: { window: 533000 }, rateLimits: [] })

  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const row = await ui.find({ type: 'Text', text: /\? \/ 533k/ })
  expect(row?.text).toMatch(/\? \/ 533k/)
  await ui.unmount()
})

// --- the context bar ---

type Node = { type?: string; props?: Record<string, unknown>; children?: (Node | string)[] }

const find = (node: Node | string, match: (node: Node) => boolean): Node[] =>
  typeof node === 'string' ? [] : [...(match(node) ? [node] : []), ...(node.children ?? []).flatMap(child => find(child, match))]

const textOf = (node: Node | string): string =>
  typeof node === 'string' ? node : (node.children ?? []).map(textOf).join('')

const drawn = async ($: Engine, surface: 'desktop' | 'terminal', bodyColumns: number) => {
  await $.session.start({ cwd: '/', surface, isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)
  const ui = await $.ui.mount({ ...PANE, surface, props: { ...PANE.props, bodyColumns } })
  const tree = (await ui.drawn()) as Node
  await ui.unmount()
  return tree
}

test('a graphical surface draws the context bar as an Svg and the terminal as text that fits', async ($, on) => {
  world(on, { CLAUDE_CONFIG_DIR: CONFIG }, FILES)

  const desktop = await drawn($, 'desktop', 40)
  const [svg] = find(desktop, node => node.type === 'Svg')
  expect(svg?.props?.alt).toBe('Context 93% full')
  expect(String(svg?.props?.source)).toContain('#c62828')
  expect(find(desktop, node => node.type === 'Text' && /[█░]/.test(textOf(node)))).toEqual([])

  // The label column is 17 wide and the gap 1, so the bar gets what is left of the columns minus 4.
  for (const columns of [40, 30]) {
    const terminal = await drawn($, 'terminal', columns)
    expect(find(terminal, node => node.type === 'Svg')).toEqual([])
    const [bar] = find(terminal, node => node.type === 'Text' && /^[█░]+$/.test(textOf(node)))
    const cells = textOf(bar ?? '')
    expect({
      columns,
      fits: cells.length <= columns - 17 - 1 - 4 || cells.length === 6,
      filled: cells.replaceAll('░', '').length / cells.length > 0.8,
    }).toEqual({ columns, fits: true, filled: true })
  }
})

test('the label column is one width and no bar is drawn without a percentage', async ($, on) => {
  world(on, { CLAUDE_CONFIG_DIR: CONFIG }, {}, { startedAt: 0, context: { window: 533000 }, rateLimits: [] })
  const tree = await drawn($, 'desktop', 40)
  const widths = find(tree, node => typeof node.props?.width === 'number').map(node => node.props?.width)
  expect(widths).toEqual(Array(7).fill(17))
  expect(find(tree, node => node.type === 'Svg')).toEqual([])
})

const NAMES = ['plugins/installed_plugins.json', 'lessons-inbox.md', 'state/compact-guard.log']

// The engine hands an `fs.read` hook the path resolved for the host: a drive letter and backslashes
// on Windows, the working directory in front of a Windows path elsewhere. Compare what follows the drive.
const tail = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

type Step = [name: string, variables: Record<string, string>, dir: string | undefined]

test('the config dir is CLAUDE_CONFIG_DIR, then the home folder, and without either nothing is read', async ($, on) => {
  const env: Record<string, string> = {}
  const reads = world(on, env, {})
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)

  const steps: Step[] = [
    ['the variable beats the home folder', { CLAUDE_CONFIG_DIR: '/cfg/one', USERPROFILE: '/up', HOME: '/hm' }, '/cfg/one'],
    ['USERPROFILE beats HOME', { USERPROFILE: '/up', HOME: '/hm' }, '/up/.claude'],
    ['HOME alone', { HOME: '/hm' }, '/hm/.claude'],
    ['an empty variable counts as unset', { CLAUDE_CONFIG_DIR: '  ', HOME: '/hm' }, '/hm/.claude'],
    ['no variable at all', {}, undefined],
  ]
  for (const [step, variables, dir] of steps) {
    for (const key of Object.keys(env)) delete env[key]
    Object.assign(env, variables)
    reads.length = 0
    await $.command.run({ command: 'harness', args: '' } as never)
    expect({ step, reads: [...new Set(reads.map(tail))].sort() }).toEqual({
      step,
      reads: dir === undefined ? [] : NAMES.map(name => `${dir}/${name}`).sort(),
    })
  }
})

test('a Windows-style and a POSIX-style config dir both reach the files', async ($, on) => {
  const env: Record<string, string> = {}
  const reads = world(on, env, {})
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'harness', args: '' } as never)

  const steps: Step[] = [
    ['Windows variable, backslashes', { CLAUDE_CONFIG_DIR: 'C:\\Users\\boss\\.claude' }, '/Users/boss/.claude'],
    ['Windows variable, forward slashes', { CLAUDE_CONFIG_DIR: 'D:/data/cfg' }, '/data/cfg'],
    ['Windows variable, trailing backslash', { CLAUDE_CONFIG_DIR: 'C:\\Users\\boss\\.claude\\' }, '/Users/boss/.claude'],
    ['Windows home', { USERPROFILE: 'C:\\Users\\boss' }, '/Users/boss/.claude'],
    ['POSIX variable', { CLAUDE_CONFIG_DIR: '/Users/boss/.claude' }, '/Users/boss/.claude'],
    ['POSIX variable, trailing slash', { CLAUDE_CONFIG_DIR: '/Users/boss/.claude/' }, '/Users/boss/.claude'],
    ['POSIX home', { HOME: '/home/boss' }, '/home/boss/.claude'],
  ]
  for (const [step, variables, dir] of steps) {
    for (const key of Object.keys(env)) delete env[key]
    Object.assign(env, variables)
    reads.length = 0
    await $.command.run({ command: 'harness', args: '' } as never)
    expect({ step, reads: [...new Set(reads.map(tail))].sort() }).toEqual({
      step,
      reads: NAMES.map(name => `${dir}/${name}`).sort(),
    })
  }
})
