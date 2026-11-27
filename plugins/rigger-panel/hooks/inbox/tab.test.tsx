import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

// The Inbox as one of the panel's tabs: on, it opens at start and shows its band; off for the project,
// neither, while this session's own entry is still written for the others.
const CONFIG = '/home/boss/.claude'
const DIR = `${CONFIG}/rigger-panel/inbox`
const NOW = 50_000_000
const STORE_KEY = 'tabs:/repo'
const GIT_DIR_ARGV = 'git rev-parse --path-format=absolute --git-common-dir'
const RIGGER = {
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
const posix = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

const entry = (sessionId: string, title: string, at: number) =>
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
  })

// Another session's entry, waiting 8 minutes.
const FOREIGN = () => ({ 'local_alpha.json': entry('local_alpha', 'Alpha', NOW - 8 * 60_000) })
const OFF = { [STORE_KEY]: { on: [], off: ['rigger-inbox'] } }

// The world beneath the plugin: a repository at /repo, the plugin's store, the inbox folder and the app.
const world = (on: On, files: Record<string, string>, given: Record<string, unknown> = {}) => {
  const opened: string[] = []
  const closed: string[] = []
  const store: Record<string, unknown> = { ...given }
  const clock = mock.clock(on, { now: NOW })
  on('store.get', (_, e) => ({ value: store[e.key] }))
  on('store.set', (_, e) => {
    store[e.key] = JSON.parse(JSON.stringify(e.value))
    return { value: undefined }
  })
  on('env.get', (_, e) => ({ value: e.name === 'CLAUDE_CONFIG_DIR' ? CONFIG : undefined }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    closed.push(e.id)
    return { value: undefined }
  })
  on('ui.status', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: '/repo' }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('mcp.call', (_, e) => {
    if (e.server === 'ccd_session_mgmt' && e.tool === 'get_session') {
      const self = { sessionId: 'local_me', title: 'Me', cwd: '/work/me', link: 'claude://claude.ai/epitaxy/local_me' }
      return { value: { content: [{ type: 'text', text: JSON.stringify(self) }], isError: false } }
    }
    return { deny: 'not in this test' }
  })
  on('process.run', (_, e) => {
    if (e.argv.join(' ') !== GIT_DIR_ARGV) return { deny: 'not this command in this test' }
    return { value: { exitCode: 0, stdout: '/repo/.git\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('fs.list', (_, e) => {
    if (posix(e.path) !== DIR) return { deny: 'missing' }
    return { value: Object.keys(files).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })) }
  })
  on('fs.read', (_, e) => {
    const path = posix(e.path)
    const text = path.startsWith(`${DIR}/`) ? files[path.slice(DIR.length + 1)] : undefined
    return text === undefined ? { deny: 'missing' } : { value: text }
  })
  on('fs.write', (_, e) => {
    const path = posix(e.path)
    if (!path.startsWith(`${DIR}/`)) return { deny: 'outside the inbox' }
    files[path.slice(DIR.length + 1)] = e.text
    return { value: undefined }
  })
  on('tool.call', () => ({ result: { content: 'shown' } }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  on('prompt.submit', (_, e) => ({ text: e.text }))
  // The engine's own drawing beneath the band, for a band that passes `next(e)`.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  return { files, opened, closed, stored: store, clock }
}

const start = async ($: Engine, w: ReturnType<typeof world>) => {
  await $.session.start({ cwd: '/repo', surface: 'desktop', isInteractive: true })
  await w.clock.settle()
}

const bandText = async ($: Engine) => {
  const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
  const text = (await band.find({ type: 'Text', text: /waiting|engine/ }))?.text
  await band.unmount()
  return text
}

test('the Inbox pane opens at session start while its tab is on, and the band shows', async ($, on) => {
  const w = world(on, FOREIGN())
  await start($, w)
  expect(w.opened.filter(id => id === 'rigger-inbox')).toEqual(['rigger-inbox'])
  expect(await bandText($)).toBe('📥 1 waiting · oldest: Alpha, 8 min ')
})

test("with the project's choice off, no Inbox pane opens and the band is absent although an entry waits", async ($, on) => {
  const w = world(on, FOREIGN(), OFF)
  await start($, w)
  expect(w.opened).not.toContain('rigger-inbox')
  expect(await bandText($)).toBe('engine')
  // /inbox is an explicit request: it still opens the pane, once.
  await $.command.run({ command: 'inbox', args: '' } as never)
  expect(w.opened.filter(id => id === 'rigger-inbox')).toEqual(['rigger-inbox'])
})

test("switching the Inbox on from the Rigger pane's Tabs row opens it and brings the band back", async ($, on) => {
  const w = world(on, FOREIGN(), OFF)
  await start($, w)
  expect(await bandText($)).toBe('engine')
  const rigger = await $.ui.mount({ ...RIGGER, surface: 'desktop' })
  expect((await rigger.find({ key: 'tab-rigger-inbox' }))?.text).toBe('[ ] Inbox')
  await rigger.press({ key: 'tab-rigger-inbox' })
  await rigger.unmount()
  expect(w.stored[STORE_KEY]).toEqual({ on: ['rigger-inbox'], off: [] })
  expect(w.opened.at(-1)).toBe('rigger-inbox')
  expect(await bandText($)).toBe('📥 1 waiting · oldest: Alpha, 8 min ')
})

test("the writer still writes this session's entry while the Inbox tab is off", async ($, on) => {
  const w = world(on, {}, OFF)
  await start($, w)
  await $.turn.start({ text: 'go', turnId: 't1' })
  await $.tool.call({ tool: 'WrapUp', recap: 'Spec ready', ask: 'Approve?', blocked: true } as never)
  await w.clock.settle()
  const written = JSON.parse(w.files['local_me.json'] ?? 'null')
  expect({ kind: written?.kind, ask: written?.ask, at: written?.at }).toEqual({ kind: 'question', ask: 'Approve?', at: NOW })
})
