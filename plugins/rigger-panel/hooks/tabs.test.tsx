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
  // `$.ui.panes()` is refused.
  panesFail?: boolean
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
  // The engine's record of the open panes, which `$.ui.panes()` lists; a person's close mark removes one.
  const open = new Set<string>()
  on('ui.open', (_, e) => {
    opened.push(e.id)
    open.add(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    closed.push(e.id)
    open.delete(e.id)
    return { value: undefined }
  })
  on('ui.panes', () =>
    given.panesFail
      ? { deny: 'not served here' }
      : { value: [...open].map(id => ({ id, title: id, isShown: false, isFocused: false, isPlaced: true })) },
  )
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
  return Object.assign(state, { opened, closed, open, stored: store, clock })
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

const MENU = { plugin: 'rigger-panel', surface: 'desktop', component: 'ContextMenu', props: {} } as const

// The transcript's right-click menu lists every switchable tab, and a press switches it like the Tabs row.
test("the right-click menu has a row per tab that switches it, beside another plugin's rows", async ($, on) => {
  const w = world(on)
  // Another plugin's row in the same menu.
  on('ui.render', ($$, e) => {
    const { Button } = $$.ui.resolve(e)
    return (
      <Button key="other-row" onPress={() => undefined}>
        Copy
      </Button>
    )
  })
  await start($, w)
  await press($, 'rigger-inbox')
  const ui = await $.ui.mount(MENU as never)
  const rows = await ui.findAll({ type: 'Button' })
  // The engine places the rows beneath; only which rows there are is this plugin's.
  expect(rows.map(row => row.text).sort()).toEqual(['Copy', '[ ] Inbox', '[x] Environments', '[x] Issues', '[x] Plan'])
  await ui.press({ key: 'menu-tab-rigger-issues' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.closed).toEqual(['rigger-inbox', 'rigger-issues'])
  expect(w.stored['tabs:/repo']).toEqual({ on: [], off: ['rigger-inbox', 'rigger-issues'] })
  const again = await $.ui.mount(MENU as never)
  await again.press({ key: 'menu-tab-rigger-inbox' })
  await w.clock.settle()
  await again.unmount()
  expect(w.opened.at(-1)).toBe('rigger-inbox')
  expect(await buttonText($, 'rigger-issues')).toBe('[ ] Issues')
})

const menuText = async ($: Engine, id: string) => {
  const ui = await $.ui.mount(MENU as never)
  const text = (await ui.find({ key: `menu-tab-${id}` }))?.text
  await ui.unmount()
  return text
}

// A pane closed with its close mark is hidden, not switched off: its tab stays on, says it is closed, and a
// press in the menu or the Tabs row opens it again instead of switching it off.
test('a tab whose pane the person closed is marked closed, and a press opens it again and keeps it on', async ($, on) => {
  const w = world(on)
  await start($, w)
  w.open.delete('rigger-inbox')
  expect(await menuText($, 'rigger-inbox')).toBe('[x] Inbox (closed)')
  expect(await buttonText($, 'rigger-inbox')).toBe('[x] Inbox (closed)')
  expect(await menuText($, 'rigger-issues')).toBe('[x] Issues')
  w.opened.length = 0
  const ui = await $.ui.mount(MENU as never)
  await ui.press({ key: 'menu-tab-rigger-inbox' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.opened).toEqual(['rigger-inbox'])
  expect(w.closed).toEqual([])
  expect(w.stored['tabs:/repo']).toBeUndefined()
  expect(await menuText($, 'rigger-inbox')).toBe('[x] Inbox')
  w.open.delete('rigger-inbox')
  await press($, 'rigger-inbox')
  await w.clock.settle()
  expect(w.opened).toEqual(['rigger-inbox', 'rigger-inbox'])
  expect(w.stored['tabs:/repo']).toBeUndefined()
})

// Without the engine's pane list every tab counts as open: the marks and presses work as before.
test('when the open panes cannot be listed, a press switches the tab as before', async ($, on) => {
  const w = world(on, { panesFail: true })
  await start($, w)
  expect(await menuText($, 'rigger-inbox')).toBe('[x] Inbox')
  await press($, 'rigger-inbox')
  await w.clock.settle()
  expect(w.closed).toEqual(['rigger-inbox'])
  expect(w.stored['tabs:/repo']).toEqual({ on: [], off: ['rigger-inbox'] })
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

// Session folder, what git prints as its common dir there, and the project key it gives: the parent
// only of a `.git` folder; a submodule's or a bare repository's common dir is the key itself.
const KEYS: [string, string, string, string][] = [
  ['a normal checkout', '/work/app/src', '/work/app/.git', '/work/app'],
  ['a submodule', '/work/app/lib/x', '/work/app/.git/modules/x', '/work/app/.git/modules/x'],
  ['a bare repository', '/srv/app.git', '/srv/app.git', '/srv/app.git'],
]

for (const [what, cwd, gitDir, key] of KEYS) {
  test(`${what} keys its choice by ${key}`, async ($, on) => {
    const w = world(on, { cwd, gitDirs: { [cwd]: gitDir }, store: { [`tabs:${key}`]: { on: [], off: ['rigger-plan'] } } })
    await start($, w, cwd)
    expect(w.opened).toEqual(['rigger-panel', 'rigger-issues', 'rigger-envs', 'rigger-inbox'])
  })
}

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
