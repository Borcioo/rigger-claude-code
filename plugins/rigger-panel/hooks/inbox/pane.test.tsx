import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { openCommand } from './reader'

const DIR = '/home/boss/.claude/rigger-panel/inbox'
const NOW = 50_000_000
const PANE = {
  plugin: 'rigger-panel',
  component: 'Pane',
  requestId: 'rigger-inbox',
  props: {
    title: 'Inbox',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const
const BAND = {
  plugin: 'rigger-panel',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 3,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 3 },
    view: {},
  },
} as const
const OPENERS = new Set(['open', 'cmd', 'xdg-open'])
// The engine hands a hook the path as the platform spells it (a drive and backslashes on Windows).
const posix = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

const entry = (sessionId: string, title: string, at: number, more: Record<string, unknown> = {}) =>
  JSON.stringify({
    v: 1,
    sessionId,
    link: `claude://claude.ai/epitaxy/${sessionId}`,
    title,
    cwd: `/work/${title.toLowerCase()}`,
    kind: 'question',
    text: `${title} finished`,
    ask: `${title} asks?`,
    actions: ['Yes'],
    at,
    ...more,
  })

const ran = (exitCode: number, stdout: string, stderr: string) => ({
  exitCode,
  stdout,
  stderr,
  isStdoutTruncated: false,
  isStderrTruncated: false,
})

// `mtimes`: a file's modification time in the listing, by name; any other file was written just now.
type WorldOpts = {
  env?: Record<string, string>
  machine?: string
  uname?: string
  openerFails?: string
  mtimes?: Record<string, number>
  // What list_sessions answers: its JSON text, or a failure; unset, the app lists no session.
  sessions?: string | { isError: string } | 'throw' | 'hang'
}

// The world beneath the plugin: an inbox folder, the app's session tools and the system opener.
const world = (on: On, files: Record<string, string>, opts: WorldOpts = {}) => {
  on('env.get', (_, e) => ({ value: { HOME: '/home/boss', ...opts.env }[e.name] }))
  const clock = mock.clock(on, { now: NOW })
  const registered: { name: string; description?: string }[] = []
  const opened: { id: string; title?: string }[] = []
  on('command.register', (_, e) => {
    registered.push({ name: e.name, description: e.description })
    return { value: { command: e.name } }
  })
  on('ui.open', (_, e) => {
    opened.push({ id: e.id, title: e.title })
    return { value: { isPlaced: true } }
  })
  on('ui.status', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('session.machine', () =>
    opts.machine === undefined
      ? { deny: 'not served here' }
      : { value: { sysname: opts.machine, release: '1', machine: 'x86_64', packageManagers: [], runtimes: [] } },
  )
  on('fs.list', (_, e) => {
    if (posix(e.path) !== DIR) return { deny: 'missing' }
    const listed = Object.keys(files).map(name => ({ name, kind: 'file', size: 1, mtimeMs: opts.mtimes?.[name] ?? NOW, isLink: false }))
    return { value: listed }
  })
  const reads: string[] = []
  on('fs.read', (_, e) => {
    const path = posix(e.path)
    reads.push(path.slice(DIR.length + 1))
    const text = path.startsWith(`${DIR}/`) ? files[path.slice(DIR.length + 1)] : undefined
    return text === undefined ? { deny: 'missing' } : { value: text }
  })
  on('fs.write', (_, e) => {
    const path = posix(e.path)
    if (!path.startsWith(`${DIR}/`)) return { deny: 'outside the inbox' }
    files[path.slice(DIR.length + 1)] = e.text
    return { value: undefined }
  })
  const listings: unknown[] = []
  on('mcp.call', (_, e) => {
    if (e.server === 'ccd_session_mgmt' && e.tool === 'get_session') {
      const self = { sessionId: 'local_me', title: 'Me', cwd: '/work/me', link: 'claude://x/local_me' }
      return { value: { content: [{ type: 'text', text: JSON.stringify(self) }], isError: false } }
    }
    if (e.server === 'ccd_session_mgmt' && e.tool === 'list_sessions') {
      listings.push(e.args)
      const answer = opts.sessions ?? '[]'
      if (answer === 'throw') throw new Error('no app here')
      if (answer === 'hang') return new Promise<never>(() => {})
      if (typeof answer === 'object') return { value: { content: [{ type: 'text', text: answer.isError }], isError: true } }
      return { value: { content: [{ type: 'text', text: answer }], isError: false } }
    }
    return { deny: 'not in this test' }
  })
  const runs: string[][] = []
  const unames: string[][] = []
  on('process.run', (_, e) => {
    if (e.argv[0] === 'uname') {
      unames.push([...e.argv])
      return { value: ran(0, `${opts.uname ?? 'Darwin'}\n`, '') }
    }
    if (!OPENERS.has(e.argv[0] ?? '')) return { deny: 'not in this test' }
    runs.push([...e.argv])
    if (opts.openerFails !== undefined) return { value: ran(1, '', opts.openerFails) }
    return { value: ran(0, '', '') }
  })
  // The engine's own drawing beneath the band, for a band that passes `next(e)`.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  return { files, runs, unames, clock, registered, opened, reads, listings }
}

const open = async ($: Engine, surface: 'terminal' | 'desktop' = 'desktop') => {
  await $.session.start({ cwd: '/', surface, isInteractive: true })
  await $.command.run({ command: 'inbox', args: '' } as never)
  return $.ui.mount({ ...PANE, surface })
}

const STANDARD = () => ({
  'local_me.json': entry('local_me', 'Me', NOW - 1000),
  'local_done.json': entry('local_done', 'Closed', NOW - 9000),
  'local_done.closed': String(NOW - 9000),
  'local_broken.json': '{"v":1,',
  'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 8 * 60_000),
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`the pane lists only other sessions' open entries (${surface})`, async ($, on) => {
    world(on, STANDARD())
    const ui = await open($, surface)
    expect((await ui.find({ type: 'Text', text: /Alpha ·/ }))?.text).toBe('❓ Alpha · alpha · 8 min')
    expect((await ui.find({ type: 'Text', text: /Alpha asks/ }))?.text).toBe('Alpha asks?')
    expect(await ui.find({ type: 'Text', text: /Me ·|Closed/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /Nothing waiting/ })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'open-local_alpha' }))?.text).toBe('Open')
    expect((await ui.find({ type: 'Button', key: 'close-local_alpha' }))?.text).toBe('Close')
    expect(await ui.find({ type: 'Button', text: 'Yes' })).toBeUndefined()
    await ui.unmount()
  })
}

test('/inbox registers with its description and opens the Inbox pane', async ($, on) => {
  const { registered, opened } = world(on, {})
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(registered.filter(command => command.name === 'inbox')).toEqual([
    { name: 'inbox', description: 'Inbox: what every session waits on' },
  ])
  // The Inbox tab is on, so the start opens it once; /inbox opens it once more.
  expect(opened.filter(pane => pane.id === 'rigger-inbox')).toEqual([{ id: 'rigger-inbox', title: 'Inbox' }])
  await $.command.run({ command: 'inbox', args: '' } as never)
  expect(opened.filter(pane => pane.id === 'rigger-inbox')).toEqual([
    { id: 'rigger-inbox', title: 'Inbox' },
    { id: 'rigger-inbox', title: 'Inbox' },
  ])
})

test('a file whose name is another session id than it holds is skipped', async ($, on) => {
  world(on, { 'local_a.json': entry('local_b', 'Alpha', NOW - 1000) })
  const ui = await open($)
  expect((await ui.find({ type: 'Text', text: /Nothing|Alpha/ }))?.text).toBe('Nothing waiting.')
  await ui.unmount()
})

test('an entry written after the pane opened shows after one poll', async ($, on) => {
  const { files, clock } = world(on, {})
  const ui = await open($)
  files['local_alpha.json'] = entry('local_alpha', 'Alpha', NOW + 1000)
  await clock.advance(5000)
  expect((await ui.find({ type: 'Text', text: /Alpha ·/ }))?.text).toBe('❓ Alpha · alpha · now')
  await ui.unmount()
})

test('an entry older than 7 days is not shown', async ($, on) => {
  world(on, { 'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 7 * 86_400_000 - 1) })
  const ui = await open($)
  expect((await ui.find({ type: 'Text', text: /Nothing|Alpha/ }))?.text).toBe('Nothing waiting.')
  await ui.unmount()
})

// Every session's file stays in the folder, so the poll reads only files written in the last 7 days.
test('a file last written more than 7 days ago is not read at all', async ($, on) => {
  const old = NOW - 7 * 86_400_000 - 1
  const { reads } = world(
    on,
    {
      'local_old.json': entry('local_old', 'Old', NOW - 1000),
      'local_old.closed': String(NOW - 2000),
      'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 1000),
    },
    { mtimes: { 'local_old.json': old, 'local_old.closed': old } },
  )
  const ui = await open($)
  expect((await ui.find({ type: 'Text', text: /Alpha ·|Old ·/ }))?.text).toBe('❓ Alpha · alpha · now')
  expect(reads.filter(name => name.startsWith('local_old'))).toEqual([])
  expect(reads).toContain('local_alpha.json')
  await ui.unmount()
})

// A file named and holding an id with junk before `local_`: its link matches the session-link check, so
// only the id check keeps `&calc` from `cmd /c start`.
test('an entry whose session id carries junk before local_ is not shown and opens nothing', async ($, on) => {
  const id = 'x&calc&local_a'
  const { runs } = world(on, { [`${id}.json`]: entry(id, 'Crafted', NOW - 1000) }, { env: { OS: 'Windows_NT' } })
  const ui = await open($)
  expect((await ui.find({ type: 'Text', text: /Nothing|Crafted/ }))?.text).toBe('Nothing waiting.')
  expect(await ui.find({ type: 'Button', key: `open-${id}` })).toBeUndefined()
  expect(runs).toEqual([])
  await ui.unmount()
})

const TWO = () => ({
  'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 1000),
  'local_beta.json': entry('local_beta', 'Beta', NOW - 2000),
})
const row = (sessionId: string, isArchived: boolean) => ({ sessionId, title: sessionId, isArchived, isRunning: false })
const shown = async (ui: Awaited<ReturnType<typeof open>>) =>
  (await ui.findAll({ type: 'Text', text: / · / })).map(text => text.text)

test("an archived session's entry is hidden, an active one's shows", async ($, on) => {
  const { listings } = world(on, TWO(), { sessions: JSON.stringify([row('local_alpha', false), row('local_beta', true)]) })
  const ui = await open($)
  expect(await shown(ui)).toEqual(['❓ Alpha · alpha · now'])
  expect(listings[0]).toEqual({ include_archived: true, limit: 200 })
  await ui.unmount()
})

// The entry is a notice: when the app cannot say which sessions are archived, every entry shows.
const BETA_ARCHIVED = JSON.stringify([row('local_beta', true)])
for (const [name, sessions] of [
  ['an error, whatever its text', { isError: BETA_ARCHIVED }],
  ['a throw', 'throw'],
  ['text that is not JSON', 'not json'],
  ['JSON that is not a list', JSON.stringify({ sessionId: 'local_beta', isArchived: true })],
  ['a row whose isArchived is not true', JSON.stringify([{ sessionId: 'local_beta', isArchived: 'yes' }])],
] as const) {
  test(`a list_sessions that answers ${name} hides nothing`, async ($, on) => {
    world(on, TWO(), { sessions })
    const ui = await open($)
    expect(await shown(ui)).toEqual(['❓ Beta · beta · now', '❓ Alpha · alpha · now'])
    await ui.unmount()
  })
}

// One odd row neither hides the others nor drops the archived ones beside it.
test('rows of the wrong shape are skipped and an archived row beside them still hides its entry', async ($, on) => {
  const rows = [null, 'local_beta', 7, { sessionId: 7, isArchived: true }, row('local_alpha', true)]
  world(on, TWO(), { sessions: JSON.stringify(rows) })
  const ui = await open($)
  expect(await shown(ui)).toEqual(['❓ Beta · beta · now'])
  await ui.unmount()
})

test('a list_sessions that never answers is given up after 5 s and hides nothing', async ($, on) => {
  const { clock } = world(on, TWO(), { sessions: 'hang' })
  const opening = open($)
  await clock.advance(5000)
  const ui = await opening
  expect(await shown(ui)).toEqual(['❓ Beta · beta · now', '❓ Alpha · alpha · now'])
  await ui.unmount()
})

test('the archive check is asked at most once in 30 s, and not at all with nothing to show', async ($, on) => {
  const { files, clock, listings } = world(on, {}, { sessions: JSON.stringify([row('local_beta', true)]) })
  const ui = await open($)
  expect(listings).toHaveLength(0)
  Object.assign(files, TWO())
  await clock.advance(5000)
  expect(listings).toHaveLength(1)
  expect(await shown(ui)).toEqual(['❓ Alpha · alpha · now'])
  await clock.advance(20_000)
  expect(listings).toHaveLength(1)
  await clock.advance(10_000)
  expect(listings).toHaveLength(2)
  await ui.unmount()
})

test('an empty inbox says so', async ($, on) => {
  world(on, {})
  const ui = await open($)
  expect((await ui.find({ type: 'Text', text: /Nothing/ }))?.text).toBe('Nothing waiting.')
  await ui.unmount()
})

test('Close writes the close marker with the entry time and hides the item', async ($, on) => {
  const { files } = world(on, STANDARD())
  const ui = await open($)
  await ui.press({ key: 'close-local_alpha' })
  expect(files['local_alpha.closed']).toBe(String(NOW - 8 * 60_000))
  expect((await ui.find({ type: 'Text', text: /Nothing/ }))?.text).toBe('Nothing waiting.')
  await ui.unmount()
})

test('the band counts other sessions and names the oldest; it is absent with nothing waiting', async ($, on) => {
  world(on, { ...STANDARD(), 'local_new.json': entry('local_new', 'Beta', NOW - 60_000) })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'inbox', args: '' } as never)
  const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect((await band.find({ type: 'Text', text: /waiting/ }))?.text).toBe('📥 2 waiting · oldest: Alpha, 8 min ')
  expect((await band.find({ type: 'Button', key: 'open-inbox' }))?.text).toBe('Inbox')
  await band.unmount()
})

test('the band is absent when only the own entry waits, before /inbox ran', async ($, on) => {
  const { clock } = world(on, { 'local_me.json': entry('local_me', 'Me', NOW - 1000) })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.settle()
  const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await band.find({ type: 'Text', text: /waiting/ })).toBeUndefined()
  expect((await band.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  await band.unmount()
})

test('Open hands the session link to the operating system', async ($, on) => {
  const { runs, unames } = world(on, STANDARD())
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect(runs).toEqual([['open', 'claude://claude.ai/epitaxy/local_alpha']])
  expect(unames).toEqual([['uname', '-s']])
  await ui.unmount()
})

test('Open asks the engine for the system first and runs no uname when it answers', async ($, on) => {
  const { runs, unames } = world(on, STANDARD(), { machine: 'Windows_NT', uname: 'Darwin' })
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect(runs).toEqual([['cmd', '/c', 'start', '', 'claude://claude.ai/epitaxy/local_alpha']])
  expect(unames).toEqual([])
  await ui.unmount()
})

test('Open on Linux by the engine uses xdg-open', async ($, on) => {
  const { runs } = world(on, STANDARD(), { machine: 'Linux' })
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect(runs).toEqual([['xdg-open', 'claude://claude.ai/epitaxy/local_alpha']])
  await ui.unmount()
})

test('Open on Windows, when the engine cannot say, starts the link through cmd', async ($, on) => {
  const { runs, unames } = world(on, STANDARD(), { env: { OS: 'Windows_NT', USERPROFILE: '/home/boss' } })
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect(runs).toEqual([['cmd', '/c', 'start', '', 'claude://claude.ai/epitaxy/local_alpha']])
  expect(unames).toEqual([])
  await ui.unmount()
})

for (const link of [
  'https://evil.example/x',
  'claude://claude.ai/epitaxy/local_x&calc',
  'claude://claude.ai/epitaxy/local_x\nrm',
  'x claude://claude.ai/epitaxy/local_alpha',
  'claude://claude.ai/epitaxy/local_other',
]) {
  test(`Open refuses a link that is not a session link: ${JSON.stringify(link)}`, async ($, on) => {
    const { runs, unames } = world(on, { 'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 1000, { link }) })
    const ui = await open($)
    await ui.press({ key: 'open-local_alpha' })
    expect(runs).toEqual([])
    expect(unames).toEqual([])
    expect((await ui.find({ type: 'Text', text: /not opened/ }))?.text).toBe('not opened: the link is not a session link')
    await ui.unmount()
  })
}

test('an opener that fails shows its reason on the item', async ($, on) => {
  world(on, STANDARD(), { openerFails: 'no handler for claude:\n' })
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect((await ui.find({ type: 'Text', text: /not opened/ }))?.text).toBe('not opened: no handler for claude:')
  await ui.unmount()
})

test("a failed Open shows on its item and not on the session's next entry", async ($, on) => {
  const { files, clock } = world(on, { 'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 1000, { link: 'x' }) })
  const ui = await open($)
  await ui.press({ key: 'open-local_alpha' })
  expect((await ui.find({ type: 'Text', text: /not opened/ }))?.text).toBe('not opened: the link is not a session link')
  files['local_alpha.json'] = entry('local_alpha', 'Alpha', NOW + 1000)
  await clock.advance(5000)
  expect((await ui.find({ type: 'Text', text: /Alpha ·/ }))?.text).toBe('❓ Alpha · alpha · now')
  expect(await ui.find({ type: 'Text', text: /not opened/ })).toBeUndefined()
  await ui.unmount()
})

test('openCommand picks the opener per operating system', () => {
  const link = 'claude://claude.ai/epitaxy/local_a'
  expect(openCommand('Darwin', link)).toEqual(['open', link])
  expect(openCommand('Windows_NT', link)).toEqual(['cmd', '/c', 'start', '', link])
  expect(openCommand('Linux', link)).toEqual(['xdg-open', link])
})
