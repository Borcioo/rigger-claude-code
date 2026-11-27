import type { On } from 'claude-code'
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

const ALL = ['rigger-panel', 'rigger-issues', 'rigger-plan', 'rigger-envs', 'rigger-inbox']
const CONFIG = '/cfg'
const SESSION = 'sess-1'
const SNAPSHOT = `${CONFIG}/rigger/state/${SESSION}.resolved.json`
const GIT_DIR_ARGV = 'git rev-parse --path-format=absolute --git-common-dir'

const norm = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

type World = {
  // The session folder `$.session.cwd()` answers.
  cwd: string
  // Folder -> what `git rev-parse --git-common-dir` prints there; a folder not listed is not in a repository.
  gitDirs: Record<string, string>
  files: Record<string, string>
  store?: Record<string, unknown>
}

// The world beneath the plugin, with what it opened and closed recorded in order.
const world = (on: On, given: Partial<World> = {}) => {
  const state: World = { cwd: '/repo', gitDirs: { '/repo': '/repo/.git' }, files: {}, ...given }
  const opened: string[] = []
  const closed: string[] = []
  const clock = mock.clock(on, { now: 1000 })
  // The plugin's store as the engine keeps it: JSON data, read back as written.
  const store: Record<string, unknown> = { ...given.store }
  on('store.get', (_, e) => ({ value: store[e.key] }))
  on('store.set', (_, e) => {
    store[e.key] = JSON.parse(JSON.stringify(e.value))
    return { value: undefined }
  })
  on('env.get', (_, e) => ({ value: e.name === 'CLAUDE_CONFIG_DIR' ? CONFIG : undefined }))
  on('command.register', () => ({ value: { command: 'harness' } }))
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    closed.push(e.id)
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: state.cwd }))
  on('session.id', () => ({ value: SESSION }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('mcp.call', () => ({ deny: 'no MCP server in this test' }))
  on('process.run', (_, e) => {
    const where = e.init?.cwd ?? ''
    const ok = (stdout: string, exitCode = 0) => ({
      value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    })
    if (e.argv.join(' ') !== GIT_DIR_ARGV) return { deny: 'not this command in this test' }
    const dir = state.gitDirs[where]
    return dir === undefined ? ok('', 128) : ok(`${dir}\n`)
  })
  on('fs.read', (_, e) => {
    const text = state.files[norm(e.path)]
    return text === undefined ? { deny: 'missing' } : { value: text }
  })
  return Object.assign(state, { opened, closed, stored: store, clock })
}

// A session start, and the reads it set going settled, so none outlives the test.
const start = async ($: Engine, w: ReturnType<typeof world>, cwd = '/repo') => {
  await $.session.start({ cwd, surface: 'desktop', isInteractive: true })
  await w.clock.settle()
}

const snapshotWith = (panel: unknown) => JSON.stringify({ schema: 1, config: { effective: { schema: 1, panel } } })

const buttonText = async ($: Engine, id: string) => {
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const text = (await ui.find({ key: `tab-${id}` }))?.text
  await ui.unmount()
  return text
}

// A press of a tab's button in the drawn Rigger pane.
const press = async ($: Engine, id: string) => {
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: `tab-${id}` })
  await ui.unmount()
}

test('every tab opens when nothing was chosen, and the Rigger pane lists each with a mark', async ($, on) => {
  const w = world(on)
  await start($, w)
  expect(w.opened).toEqual(ALL)
  expect(await buttonText($, 'rigger-issues')).toBe('[x] Issues')
  expect(await buttonText($, 'rigger-envs')).toBe('[x] Environments')
})

test('switching a tab off closes its pane and stores it, and the next session opens without it', async ($, on) => {
  const w = world(on)
  await start($, w)
  await press($, 'rigger-issues')
  expect(w.closed).toEqual(['rigger-issues'])
  expect(w.stored['tabs:/repo']).toEqual({ on: [], off: ['rigger-issues'] })
  expect(await buttonText($, 'rigger-issues')).toBe('[ ] Issues')

  w.opened.length = 0
  await start($, w)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-plan', 'rigger-envs', 'rigger-inbox'])
  w.opened.length = 0
  await $.command.run({ command: 'harness', args: '' } as never)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-plan', 'rigger-envs', 'rigger-inbox'])

  // Switched on again: it opens and the store says so.
  await press($, 'rigger-issues')
  expect(w.opened.at(-1)).toBe('rigger-issues')
  expect(w.stored['tabs:/repo']).toEqual({ on: ['rigger-issues'], off: [] })
})

test('a worktree of the same repository shares the choice, and a folder outside git keys by itself', async ($, on) => {
  const w = world(on, {
    cwd: '/repo/.claude/worktrees/feature',
    gitDirs: { '/repo': '/repo/.git', '/repo/.claude/worktrees/feature': '/repo/.git' },
    store: { 'tabs:/repo': { on: [], off: ['rigger-plan'] } },
  })
  await start($, w, '/repo/.claude/worktrees/feature')
  expect(w.opened).toEqual(['rigger-panel', 'rigger-issues', 'rigger-envs', 'rigger-inbox'])

  w.cwd = '/scratch/notes'
  w.opened.length = 0
  await start($, w, '/scratch/notes')
  expect(w.opened).toEqual(ALL)
  await press($, 'rigger-envs')
  expect(w.stored['tabs:/scratch/notes']).toEqual({ on: [], off: ['rigger-envs'] })
  expect(w.stored['tabs:/repo']).toEqual({ on: [], off: ['rigger-plan'] })
})

// A session folder, git's answer there and the store key, as on Windows.
const WIN_CWD = 'C:\\work\\repo\\sub'
const WIN_GIT_DIR = 'C:/work/repo/.git/'
const WIN_KEY = 'tabs:C:/work/repo'

test('a Windows git dir keys by its parent with forward slashes', async ($, on) => {
  const w = world(on, {
    cwd: WIN_CWD,
    gitDirs: { [WIN_CWD]: WIN_GIT_DIR },
    store: { [WIN_KEY]: { on: [], off: ['rigger-issues'] } },
  })
  await start($, w, WIN_CWD)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-plan', 'rigger-envs', 'rigger-inbox'])
})

test('panel.tabs_off in the session snapshot keeps a tab closed, and the project switching it on wins', async ($, on) => {
  const w = world(on, { files: { [SNAPSHOT]: snapshotWith({ tabs_off: ['rigger-issues', 'rigger-plan'] }) } })
  await start($, w)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-envs', 'rigger-inbox'])
  expect(await buttonText($, 'rigger-plan')).toBe('[ ] Plan')

  await press($, 'rigger-plan')
  expect(w.stored['tabs:/repo']).toEqual({ on: ['rigger-plan'], off: [] })
  w.opened.length = 0
  await start($, w)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-plan', 'rigger-envs', 'rigger-inbox'])
})

test('a missing, corrupt or wrongly shaped snapshot leaves every tab on', async ($, on) => {
  const w = world(on)
  const cases: [string, string | undefined][] = [
    ['missing', undefined],
    ['not JSON', '{"config":'],
    ['no panel key', JSON.stringify({ config: { effective: { schema: 1 } } })],
    ['tabs_off not a list', snapshotWith({ tabs_off: 'rigger-issues' })],
    ['panel null', snapshotWith(null)],
    ['a JSON list', '[1]'],
  ]
  for (const [what, text] of cases) {
    if (text === undefined) delete w.files[SNAPSHOT]
    else w.files[SNAPSHOT] = text
    w.opened.length = 0
    await start($, w)
    expect({ what, opened: w.opened }).toEqual({ what, opened: ALL })
  }
})

test('a stored choice of the wrong shape counts as no choice', async ($, on) => {
  const w = world(on, { store: { 'tabs:/repo': { on: 'x', off: 'rigger-issues' } } })
  await start($, w)
  expect(w.opened).toEqual(ALL)
})
