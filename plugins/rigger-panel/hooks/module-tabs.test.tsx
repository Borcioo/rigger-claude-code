import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { cutOptions, cutRows } from './module-tabs'

const paneOf = (requestId: string, title: string) =>
  ({
    plugin: 'rigger-panel',
    component: 'Pane',
    requestId,
    props: {
      title,
      isFocused: false,
      bodyColumns: 60,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: 40 },
      view: {},
    },
  }) as const

const CONFIG = '/cfg'
const SESSION = 'sess-1'
const SNAPSHOT = `${CONFIG}/rigger/state/${SESSION}.resolved.json`
const ROOT = '/plugins/tracker'
const MANIFEST = `${ROOT}/rigger-module.json`
const TICKETS = 'm-tracker-tickets'
const ENVIRONMENTS = '/repo/.claude/environments.json'
const GIT_DIR_ARGV = 'git rev-parse --path-format=absolute --git-common-dir'

// What the tracker's MCP server prints, generic names.
const TICKETS_TEXT = [
  'Tickets (2):',
  '',
  '- TRK-22: QA and rollout',
  '  Status: Accepted | Priority: High | Assignee: Someone | Project: Demo',
  '  Created: 2026-11-27 09:29',
  '',
  '- TRK-23: Post-release checks',
  '  Status: Accepted | Priority: Medium | Assignee: Someone | Project: Demo',
  '  Created: 2026-11-27 09:29',
].join('\n')

const PROJECTS_TEXT = [
  'Projects (2):',
  '',
  '- [DEM] Demo - support (ID: 2, tickets: 318)',
  '- [TRK] Tracker harness (ID: 61, tickets: 1)',
].join('\n')

const ROWS = {
  pattern: '^- (?<key>[A-Z0-9]+-\\d+): (?<title>.+)$',
  detail: '^\\s+Status: (?<detail>[^|]+?)\\s*\\|',
  url: 'https://example.com/tickets/{key}',
  empty: '^Tickets \\(0\\)',
}

const PROJECT_SELECT = {
  id: 'project',
  label: 'Project',
  type: 'number',
  options: {
    mcp: { server: 'tracker', tool: 'list-projects', args: {} },
    pattern: '^- \\[(?<key>[A-Z0-9]+)\\] (?<label>.+) \\(ID: (?<value>\\d+)',
  },
  default: 'tracker.project_id',
}

const ticketsTab = (extra: Record<string, unknown> = {}) => ({
  id: 'tickets',
  title: 'Tickets',
  source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { assigned_to_me: true, limit: 50 } } },
  rows: ROWS,
  ...extra,
})

const manifestWith = (tabs: unknown) => JSON.stringify({ schema: 1, name: 'tracker', panel: { tabs } })

const snapshotWith = (modules: unknown, panel: unknown = {}) =>
  JSON.stringify({ schema: 1, config: { effective: { schema: 1, panel } }, modules })

const TRACKER = [{ name: 'tracker', root: ROOT, origin: 'plugin' }]

type McpAnswer = { content: { type: string; text?: string }[]; isError: boolean; structuredContent?: unknown }
type Call = { server: string; tool: string; args: Record<string, unknown> }
type Run = { argv: string[]; cwd?: string }

const text = (body: string, isError = false): McpAnswer => ({ content: [{ type: 'text', text: body }], isError })

// The world beneath the plugin: files, an MCP server answering by tool, programs answering by argv.
const world = (on: On, files: Record<string, string>, tools: Record<string, McpAnswer | ((args: Record<string, unknown>) => McpAnswer | Promise<McpAnswer>)>) => {
  const state = { files, cwd: '/repo' }
  const calls: Call[] = []
  const runs: Run[] = []
  const programs: Record<string, { exitCode: number; stdout: string; stderr?: string; isStdoutTruncated?: boolean }> = {}
  const opened: string[] = []
  const closed: string[] = []
  const store: Record<string, unknown> = {}
  // A program start that answers only when the test lets it.
  const hanging: { program?: Promise<{ deny: string }> } = {}
  const clock = mock.clock(on, { now: 1000 })
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
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('session.cwd', () => ({ value: state.cwd }))
  on('session.id', () => ({ value: SESSION }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('tool.list', () => ({ value: [] }))
  on('mcp.call', async (_, e) => {
    // The inbox asks the desktop app for this session at start: refused here, and not the module's call.
    if (e.server === 'ccd_session_mgmt' && e.tool === 'get_session') return { deny: 'no desktop app in this test' }
    calls.push({ server: e.server, tool: e.tool, args: e.args })
    const answer = e.server === 'tracker' ? tools[e.tool] : undefined
    if (answer === undefined) return { deny: `no tool ${e.tool}` }
    return { value: typeof answer === 'function' ? await answer(e.args) : answer }
  })
  on('process.run', (_, e) => {
    const argv = [...e.argv]
    if (argv.join(' ') === GIT_DIR_ARGV) {
      return { value: { exitCode: 0, stdout: '/repo/.git\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    // The Issues tab's gh reads are not this test's; only the module's programs are recorded.
    if (argv[0] === 'gh') return { deny: 'no gh in this test' }
    runs.push({ argv: argv.map(norm), ...(e.init?.cwd ? { cwd: norm(e.init.cwd) } : {}) })
    const program = programs[norm(argv[0] ?? '')]
    if (program === undefined) return { deny: 'not this command in this test' }
    if (hanging.program !== undefined) return hanging.program
    return { value: { stderr: '', isStdoutTruncated: false, isStderrTruncated: false, ...program } }
  })
  on('fs.read', (_, e) => {
    const body = state.files[norm(e.path)]
    return body === undefined ? { deny: 'missing' } : { value: body }
  })
  return Object.assign(state, { calls, runs, programs, hanging, opened, closed, stored: store, clock })
}

const norm = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

const start = async ($: Engine, w: ReturnType<typeof world>) => {
  await $.session.start({ cwd: '/repo', surface: 'desktop', isInteractive: true })
  await w.clock.settle()
}

type Found = { type?: string; text?: string; props?: Record<string, unknown> }

// The module tab drawn once its read settled: every Text, every Link's href, the Select's value.
const drawTab = async ($: Engine, w: ReturnType<typeof world>, pane = TICKETS) => {
  const first = await $.ui.mount({ ...paneOf(pane, 'Tickets'), surface: 'desktop' })
  await w.clock.settle()
  await first.unmount()
  const ui = await $.ui.mount({ ...paneOf(pane, 'Tickets'), surface: 'desktop' })
  const texts = ((await ui.findAll({ type: 'Text' })) as Found[]).map(found => found.text ?? '')
  const links = ((await ui.findAll({ type: 'Link' })) as Found[]).map(found => String(found.props?.href))
  const select = (await ui.find({ key: 'select-project' })) as Found | undefined
  return { ui, texts, links, select }
}

const riggerLines = async ($: Engine) => {
  const ui = await $.ui.mount({ ...paneOf('rigger-panel', 'Rigger'), surface: 'desktop' })
  const texts = ((await ui.findAll({ type: 'Text' })) as Found[]).map(found => found.text ?? '')
  const buttons = ((await ui.findAll({ type: 'Button' })) as Found[]).map(found => found.text ?? '')
  await ui.unmount()
  return { texts, buttons }
}

const files = (tabs: unknown, extra: Record<string, string> = {}) => ({
  [SNAPSHOT]: snapshotWith(TRACKER),
  [MANIFEST]: manifestWith(tabs),
  ...extra,
})

test('a module tab opens with the others and draws each row with its key, detail and link', async ($, on) => {
  const w = world(on, files([ticketsTab()]), { 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  expect(w.opened).toEqual(['rigger-panel', 'rigger-issues', 'rigger-plan', 'rigger-envs', 'rigger-inbox', TICKETS])
  const { ui, texts, links } = await drawTab($, w)
  for (const line of ['TRK-22', 'QA and rollout', 'TRK-23', 'Post-release checks']) expect(texts).toContain(line)
  expect(texts.filter(line => line === 'Accepted')).toHaveLength(2)
  expect(links).toEqual(['https://example.com/tickets/TRK-22', 'https://example.com/tickets/TRK-23'])
  expect(w.calls).toEqual([{ server: 'tracker', tool: 'list-tickets', args: { assigned_to_me: true, limit: 50 } }])
  await ui.unmount()
})

test('text that holds something but no row is the failure line, never the empty state', async ($, on) => {
  const w = world(on, files([ticketsTab()]), { 'list-tickets': text('Something else entirely\nand more') })
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect(texts).toContain('module tracker tab tickets: output did not match its pattern')
  expect(texts).not.toContain('No items.')
  await ui.unmount()
})

test('blank text and a line matching the empty pattern draw the empty state', async ($, on) => {
  const answers: Record<string, McpAnswer> = { blank: text('  \n\n'), zero: text('Tickets (0):\n\nnothing here') }
  let now = 'blank'
  const w = world(on, files([ticketsTab()]), { 'list-tickets': () => answers[now] ?? text('') })
  await start($, w)
  for (const which of ['blank', 'zero']) {
    now = which
    const ui = await $.ui.mount({ ...paneOf(TICKETS, 'Tickets'), surface: 'desktop' })
    await ui.press({ key: 'refresh' })
    await w.clock.settle()
    await ui.unmount()
    const drawn = await drawTab($, w)
    expect({ which, empty: drawn.texts.includes('No items.'), failed: drawn.texts.some(line => line.startsWith('module ')) }).toEqual({
      which,
      empty: true,
      failed: false,
    })
    await drawn.ui.unmount()
  }
})

// A source that answered with nothing to read -> the failure line, never `No items.`.
const SILENT: [string, Record<string, unknown>, string][] = [
  ['an MCP result with no text block', { source: ticketsTab().source }, 'module tracker tab tickets: list-tickets returned no text'],
  ['a command with only stderr', { source: { command: ['./bin/rows.sh'] } }, 'module tracker tab tickets: rows.sh: no token found'],
]

test('an MCP result with only structuredContent is read as its indented JSON', async ($, on) => {
  const tab = ticketsTab({ rows: { pattern: '^\\s*"title": "(?<title>[^"]+)"' } })
  const answer = { content: [], isError: false, structuredContent: { items: [{ title: 'Structured row' }] } }
  const w = world(on, files([tab]), { 'list-tickets': answer })
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect({ row: texts.includes('Structured row'), failed: texts.some(line => line.startsWith('module ')) }).toEqual({ row: true, failed: false })
  await ui.unmount()
})

for (const [what, extra, line] of SILENT) {
  test(`${what} is the failure line, not the empty state`, async ($, on) => {
    const w = world(on, files([ticketsTab(extra)]), { 'list-tickets': { content: [{ type: 'image' }], isError: false } })
    w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, stdout: '', stderr: '\nrows.sh: no token found\nusage: rows.sh' }
    await start($, w)
    const { ui, texts } = await drawTab($, w)
    expect({ failure: texts.includes(line), empty: texts.includes('No items.') }).toEqual({ failure: true, empty: false })
    await ui.unmount()
  })
}

// A source that never answers -> the failure line once the 20 s bound passes, and not before.
const HANGS: [string, Record<string, unknown>, string][] = [
  ['an MCP call', {}, 'module tracker tab tickets: list-tickets did not answer within 20 s'],
  ['a command', { source: { command: ['./bin/rows.sh'] } }, `module tracker tab tickets: ${ROOT}/bin/rows.sh did not answer within 20 s`],
]

for (const [what, extra, line] of HANGS) {
  test(`${what} that never answers is the timeout failure after its bound`, async ($, on) => {
    let release = () => {}
    const never = new Promise<void>(resolve => {
      release = resolve
    })
    const w = world(on, files([ticketsTab(extra)]), { 'list-tickets': () => never.then(() => text('')) })
    w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, stdout: TICKETS_TEXT }
    w.hanging.program = never.then(() => ({ deny: 'released' }))
    try {
      await start($, w)
      const first = await $.ui.mount({ ...paneOf(TICKETS, 'Tickets'), surface: 'desktop' })
      await w.clock.settle()
      await first.unmount()
      await w.clock.advance(19_999)
      const early = await drawTab($, w)
      await early.ui.unmount()
      await w.clock.advance(1)
      const late = await drawTab($, w)
      await late.ui.unmount()
      expect({ early: early.texts.includes(line), late: late.texts.includes(line) }).toEqual({ early: false, late: true })
    } finally {
      release()
    }
  })
}

test('rows beside a line that matches the empty pattern are drawn, not the empty state', () => {
  const cut = cutRows(`Tickets (0) closed, open below:\n${TICKETS_TEXT}`, ROWS)
  expect(cut.kind === 'rows' ? cut.rows.map(row => row.key) : cut.kind).toEqual(['TRK-22', 'TRK-23'])
})

test('an error result is the failure line with the first line of its text', async ($, on) => {
  const w = world(on, files([ticketsTab()]), { 'list-tickets': text('\nUnauthorized: token expired\nretry later', true) })
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect(texts).toContain('module tracker tab tickets: Unauthorized: token expired')
  await ui.unmount()
})

test('a large output stays bounded: 50 rows and a note, a huge line skipped, and fast', async ($, on) => {
  const line = '- TRK-1: a matching title padded to be long enough to count'
  const big = Array.from({ length: Math.ceil((5 * 1024 * 1024) / (line.length + 1)) }, () => line).join('\n')
  const w = world(on, files([ticketsTab()]), { 'list-tickets': text(big) })
  await start($, w)
  const began = Date.now()
  const { ui, texts, links } = await drawTab($, w)
  expect(links).toHaveLength(50)
  expect(texts).toContain('The first 50 are shown.')
  await ui.unmount()

  const rules = { pattern: ROWS.pattern }
  const huge = `- TRK-9: ${'x'.repeat(1024 * 1024)}`
  expect(cutRows(huge, rules)).toEqual({ kind: 'unmatched' })
  // Past 256 KiB nothing is read: a row there is never seen.
  expect(cutRows(`header\n${'\n'.repeat(256 * 1024)}- TRK-5: late`, rules)).toEqual({ kind: 'unmatched' })
  expect(Date.now() - began).toBeLessThan(3000)
})

// Output that was cut somewhere -> whether the rows come with `list truncated`.
const TRUNCATED: [string, { stdout: string; isStdoutTruncated?: boolean }, boolean][] = [
  ['whole output', { stdout: TICKETS_TEXT }, false],
  ['text past 256 Ki', { stdout: `${TICKETS_TEXT}\n${'\n'.repeat(256 * 1024)}- TRK-5: late` }, true],
  ['a line over 2000 characters', { stdout: `${TICKETS_TEXT}\n${'y'.repeat(2001)}` }, true],
  ['stdout cut by the engine', { stdout: TICKETS_TEXT, isStdoutTruncated: true }, true],
]

for (const [what, output, truncated] of TRUNCATED) {
  test(`${what}: the rows are drawn${truncated ? ' with a list truncated note' : ' with no note'}`, async ($, on) => {
    const w = world(on, files([ticketsTab({ source: { command: ['./bin/rows.sh'] } })]), {})
    w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, ...output }
    await start($, w)
    const { ui, texts } = await drawTab($, w)
    expect({ rows: texts.includes('QA and rollout'), note: texts.includes('list truncated') }).toEqual({ rows: true, note: truncated })
    await ui.unmount()
  })
}

// environments.json contents -> the project the select starts on and the number the call gets.
const DEFAULTS: [string, string | undefined, number][] = [
  ['found', JSON.stringify({ tracker: { project_id: 61 } }), 61],
  ['found as a string', JSON.stringify({ tracker: { project_id: '61' } }), 61],
  ['missing file', undefined, 2],
  ['missing key', JSON.stringify({ tracker: {} }), 2],
  ['invalid JSON', '{"tracker":', 2],
  ['not an option', JSON.stringify({ tracker: { project_id: 99 } }), 2],
]

// One test per case: the choice is session state, so each case needs a session of its own.
for (const [what, environments, expected] of DEFAULTS) {
  test(`a select default (${what}) picks project ${expected}, and the number reaches the call`, async ($, on) => {
    const tab = ticketsTab({
      source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
      select: [PROJECT_SELECT],
    })
    const w = world(on, files([tab], environments === undefined ? {} : { [ENVIRONMENTS]: environments }), {
      'list-projects': text(PROJECTS_TEXT),
      'list-tickets': text(TICKETS_TEXT),
    })
    await start($, w)
    const { ui, select } = await drawTab($, w)
    const call = w.calls.find(item => item.tool === 'list-tickets')
    expect({ value: select?.props?.value, args: call?.args }).toEqual({ value: String(expected), args: { project_id: expected } })
    await ui.unmount()
  })
}

// The environment value names a project by its key group, as a ticket prefix does.
const KEY_DEFAULTS: [string, string, number][] = [
  ['a key', JSON.stringify({ tracker: { prefix: 'TRK' } }), 61],
  ['neither a value nor a key', JSON.stringify({ tracker: { prefix: 'NOPE' } }), 2],
]

for (const [what, environments, expected] of KEY_DEFAULTS) {
  test(`a select default that is ${what} picks project ${expected}`, async ($, on) => {
    const tab = ticketsTab({
      source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
      select: [{ ...PROJECT_SELECT, default: 'tracker.prefix' }],
    })
    const w = world(on, files([tab], { [ENVIRONMENTS]: environments }), {
      'list-projects': text(PROJECTS_TEXT),
      'list-tickets': text(TICKETS_TEXT),
    })
    await start($, w)
    const { ui, select } = await drawTab($, w)
    const call = w.calls.find(item => item.tool === 'list-tickets')
    expect({ value: select?.props?.value, args: call?.args }).toEqual({ value: String(expected), args: { project_id: expected } })
    await ui.unmount()
  })
}

test("a select default matches an option's value before another option's key", async ($, on) => {
  // Option 2's key is 61, option 61's value is 61: the value wins.
  const projects = ['- [61] Demo - support (ID: 2, tickets: 318)', '- [TRK] Tracker harness (ID: 61, tickets: 1)'].join('\n')
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [PROJECT_SELECT],
  })
  const w = world(on, files([tab], { [ENVIRONMENTS]: JSON.stringify({ tracker: { project_id: 61 } }) }), {
    'list-projects': text(projects),
    'list-tickets': text(TICKETS_TEXT),
  })
  await start($, w)
  const { ui, select } = await drawTab($, w)
  expect(select?.props?.value).toBe('61')
  await ui.unmount()
})

const ALL_SELECT = { ...PROJECT_SELECT, all: 'All projects' }
const optionLabels = (select: Found | undefined) => ((select?.props?.options ?? []) as { label: string }[]).map(option => option.label)

const ALL_DEFAULTS: [string, Record<string, unknown>, string | undefined, string, Record<string, unknown>][] = [
  ['no default', { all: 'All projects', default: undefined }, undefined, '-all', { assigned_to_me: true }],
  ['a default that matches nothing', ALL_SELECT, JSON.stringify({ tracker: { project_id: 99 } }), '-all', { assigned_to_me: true }],
  ['a default that matches', ALL_SELECT, JSON.stringify({ tracker: { project_id: 61 } }), '61', { assigned_to_me: true, project_id: 61 }],
]

for (const [what, extra, environments, value, args] of ALL_DEFAULTS) {
  test(`a select with an all option and ${what} picks ${value}; all leaves the argument out of the call`, async ($, on) => {
    const tab = ticketsTab({
      source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { assigned_to_me: true, project_id: '{select:project}' } } },
      select: [{ ...PROJECT_SELECT, ...extra }],
    })
    const w = world(on, files([tab], environments === undefined ? {} : { [ENVIRONMENTS]: environments }), {
      'list-projects': text(PROJECTS_TEXT),
      'list-tickets': text(TICKETS_TEXT),
    })
    await start($, w)
    const { ui, select, texts } = await drawTab($, w)
    const call = w.calls.find(item => item.tool === 'list-tickets')
    expect({ value: select?.props?.value, labels: optionLabels(select), args: call?.args }).toEqual({
      value,
      labels: ['All projects', 'Demo - support', 'Tracker harness'],
      args,
    })
    expect(texts).toContain('QA and rollout')
    await ui.unmount()
  })
}

test('picking the all option reads the rows again without the argument', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [ALL_SELECT],
  })
  const w = world(on, files([tab], { [ENVIRONMENTS]: JSON.stringify({ tracker: { project_id: 61 } }) }), {
    'list-projects': text(PROJECTS_TEXT),
    'list-tickets': text(TICKETS_TEXT),
  })
  await start($, w)
  const { ui } = await drawTab($, w)
  await ui.select({ key: 'select-project', value: '-all' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.calls.filter(item => item.tool === 'list-tickets').map(item => item.args)).toEqual([{ project_id: 61 }, {}])
})

test('the all option drops a command argv item that is only the placeholder', async ($, on) => {
  const tab = ticketsTab({ source: { command: ['./bin/rows.sh', '--project', '{select:project}'] }, select: [{ ...STRING_SELECT, all: 'All' }] })
  const w = world(on, files([tab]), { 'list-projects': text('- demo Demo') })
  w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, stdout: TICKETS_TEXT }
  await start($, w)
  const { ui, texts, select } = await drawTab($, w)
  expect(optionValues(select)).toEqual(['-all', 'demo'])
  expect(texts).toContain('QA and rollout')
  await ui.select({ key: 'select-project', value: 'demo' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.runs.map(run => run.argv)).toEqual([
    [`${ROOT}/bin/rows.sh`, '--project'],
    [`${ROOT}/bin/rows.sh`, '--project', 'demo'],
  ])
})

test('picking another option reads the rows again with its value', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [PROJECT_SELECT],
  })
  const w = world(on, files([tab]), { 'list-projects': text(PROJECTS_TEXT), 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  const { ui } = await drawTab($, w)
  await ui.select({ key: 'select-project', value: '61' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.calls.filter(item => item.tool === 'list-tickets').map(item => item.args)).toEqual([{ project_id: 2 }, { project_id: 61 }])
  const again = await drawTab($, w)
  expect(again.select?.props?.value).toBe('61')
  await again.ui.unmount()
})

// The read for the old value answers last; the pick made while it ran still wins.
test('a pick made while the tab is loading wins over that load', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [PROJECT_SELECT],
  })
  let release = () => {}
  const slow = new Promise<McpAnswer>(resolve => {
    release = () => resolve(text(TICKETS_TEXT))
  })
  const picked = text('Tickets (1):\n\n- TRK-61: Picked project work\n')
  let reads = 0
  const w = world(on, files([tab]), {
    'list-projects': text(PROJECTS_TEXT),
    'list-tickets': args => (args.project_id === 61 ? picked : ++reads === 1 ? text(TICKETS_TEXT) : slow),
  })
  await start($, w)
  const first = await drawTab($, w)
  await first.ui.press({ key: 'refresh' })
  for (let i = 0; i < 200 && reads < 2; i++) await Promise.resolve()
  expect(reads).toBe(2)
  await first.ui.unmount()
  const ui = await $.ui.mount({ ...paneOf(TICKETS, 'Tickets'), surface: 'desktop' })
  await ui.select({ key: 'select-project', value: '61' })
  release()
  await w.clock.settle()
  await ui.unmount()
  const again = await drawTab($, w)
  expect(again.select?.props?.value).toBe('61')
  expect(again.texts).toContain('Picked project work')
  expect(again.texts).not.toContain('QA and rollout')
  await again.ui.unmount()
})

// The read for the old value answers the moment the pick is written, before the new read starts.
test('a read that answers right after the pick is written never takes the pick back', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [PROJECT_SELECT],
  })
  let release = () => {}
  const slow = new Promise<McpAnswer>(resolve => {
    release = () => resolve(text(TICKETS_TEXT))
  })
  const picked = text('Tickets (1):\n\n- TRK-61: Picked project work\n')
  let reads = 0
  const w = world(on, files([tab]), {
    'list-projects': text(PROJECTS_TEXT),
    'list-tickets': args => (args.project_id === 61 ? picked : ++reads === 1 ? text(TICKETS_TEXT) : slow),
  })
  let pickWritten = false
  let staleWrote = false
  // A test's hook on the pane state: right after the pick lands, the old read answers.
  on('state.set', { plugin: 'rigger-panel', key: 'modulePanes' }, async (_, e, next) => {
    const done = await next(e)
    type Held = { rows: { title: string }[]; selects: Record<string, { value: string }> }
    const pane = (e.value as { value: Record<string, Held | undefined> }).value[TICKETS]
    if (!pickWritten && pane?.selects.project?.value === '61') {
      pickWritten = true
      release()
      for (let i = 0; i < 200 && !staleWrote; i++) await Promise.resolve()
    } else if (pickWritten && pane?.rows.some(row => row.title === 'QA and rollout')) staleWrote = true
    return done
  })
  await start($, w)
  const first = await drawTab($, w)
  await first.ui.press({ key: 'refresh' })
  for (let i = 0; i < 200 && reads < 2; i++) await Promise.resolve()
  expect(reads).toBe(2)
  await first.ui.unmount()
  const ui = await $.ui.mount({ ...paneOf(TICKETS, 'Tickets'), surface: 'desktop' })
  await ui.select({ key: 'select-project', value: '61' })
  await w.clock.settle()
  await ui.unmount()
  const again = await drawTab($, w)
  expect({ pickWritten, staleWrote, value: again.select?.props?.value, picked: again.texts.includes('Picked project work') }).toEqual({
    pickWritten: true,
    staleWrote: false,
    value: '61',
    picked: true,
  })
  await again.ui.unmount()
})

// A chosen value reaches command argv: one read as an option there, or with a control character, is no option.
test('an option value that starts with a dash or holds a control character is dropped', () => {
  const rule = { id: 'project', label: 'Project', type: 'string', pattern: '^- (?<value>\\S+) (?<label>.+)$' } as never
  const text = '- --exec=x Dash\n- a\u0007b Bell\n- demo Demo\n- -n Short'
  expect(cutOptions(text, rule).options).toEqual([{ value: 'demo', label: 'Demo' }])
})

// Options whose values a command's argv may or may not take; `./x` is a value, never a module path.
const ARGV_OPTIONS = ['demo', 'a&b', 'a|b', '50%', 'say"hi', 'x^y', '--exec=x', 'k=v', '/c', './x'].map(value => `- ${value} Label ${value}`).join('\n')
const STRING_SELECT = {
  id: 'project',
  label: 'Project',
  options: { mcp: { server: 'tracker', tool: 'list-projects', args: {} }, pattern: '^- (?<value>\\S+) (?<label>.+)$' },
}
const optionValues = (select: Found | undefined) => ((select?.props?.options ?? []) as { value: string }[]).map(option => option.value)

test('a select value bound for command argv: only a plain word reaches it, the rest dropped with a notice', async ($, on) => {
  const tab = ticketsTab({ source: { command: ['./bin/rows.sh', '--project', '{select:project}'] }, select: [STRING_SELECT] })
  const w = world(on, files([tab]), { 'list-projects': text(ARGV_OPTIONS) })
  w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, stdout: TICKETS_TEXT }
  await start($, w)
  const { ui, texts, select } = await drawTab($, w)
  expect(optionValues(select)).toEqual(['demo', './x'])
  expect(texts).toContain('module tracker tab tickets: select project: 8 options dropped (not safe as a command argument)')
  expect(texts).toContain('QA and rollout')
  await ui.select({ key: 'select-project', value: './x' })
  await w.clock.settle()
  await ui.unmount()
  expect(w.runs.map(run => run.argv)).toEqual([
    [`${ROOT}/bin/rows.sh`, '--project', 'demo'],
    [`${ROOT}/bin/rows.sh`, '--project', './x'],
  ])
})

test('a select value bound for MCP args keeps characters a command argument may not take', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project: '{select:project}' } } },
    select: [STRING_SELECT],
  })
  const w = world(on, files([tab]), { 'list-projects': text(ARGV_OPTIONS), 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  const { ui, texts, select } = await drawTab($, w)
  expect(optionValues(select)).toEqual(['demo', 'a&b', 'a|b', '50%', 'say"hi', 'x^y', 'k=v', '/c', './x'])
  expect(texts.filter(line => line.includes('dropped'))).toEqual([])
  await ui.unmount()
})

test("a select's options command resolves ./ against the module root too", async ($, on) => {
  const select = { ...STRING_SELECT, options: { command: ['./bin/options.sh'], pattern: STRING_SELECT.options.pattern } }
  const tab = ticketsTab({ source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project: '{select:project}' } } }, select: [select] })
  const w = world(on, files([tab]), { 'list-tickets': text(TICKETS_TEXT) })
  w.programs[`${ROOT}/bin/options.sh`] = { exitCode: 0, stdout: '- demo Demo' }
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect(w.runs.map(run => run.argv)).toEqual([[`${ROOT}/bin/options.sh`]])
  expect(texts).toContain('QA and rollout')
  await ui.unmount()
})

test('options that fail to load are the failure line and the rows are not read', async ($, on) => {
  const tab = ticketsTab({
    source: { mcp: { server: 'tracker', tool: 'list-tickets', args: { project_id: '{select:project}' } } },
    select: [PROJECT_SELECT],
  })
  const w = world(on, files([tab]), { 'list-projects': text('Projects (0):'), 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect(texts).toContain('module tracker tab tickets: select project has no options')
  expect(w.calls.map(item => item.tool)).toEqual(['list-projects'])
  await ui.unmount()
})

test('a command source runs from the module root, in the session folder', async ($, on) => {
  const tab = ticketsTab({ source: { command: ['./bin/rows.sh', '--mine'] } })
  const w = world(on, files([tab]), {})
  w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 0, stdout: TICKETS_TEXT }
  await start($, w)
  const { ui, texts } = await drawTab($, w)
  expect(w.runs).toEqual([{ argv: [`${ROOT}/bin/rows.sh`, '--mine'], cwd: '/repo' }])
  expect(texts).toContain('QA and rollout')
  await ui.unmount()

  w.programs[`${ROOT}/bin/rows.sh`] = { exitCode: 2, stdout: '', stderr: 'rows.sh: no token\nusage: rows.sh' }
  const failed = await $.ui.mount({ ...paneOf(TICKETS, 'Tickets'), surface: 'desktop' })
  await failed.press({ key: 'refresh' })
  await w.clock.settle()
  await failed.unmount()
  const drawn = await drawTab($, w)
  expect(drawn.texts).toContain('module tracker tab tickets: rows.sh: no token')
  await drawn.ui.unmount()
})

const long = (size: number) => 'x'.repeat(size)

// A broken entry -> the reason its line names. Each is skipped; the valid tab beside them stays.
const INVALID: [unknown, string][] = [
  ['not a tab', 'module tracker tab #1: skipped: not an object'],
  [ticketsTab({ id: 'has space' }), 'module tracker tab #1: skipped: id is not 1-32 letters, digits, _ or -'],
  [ticketsTab({ id: 'ok', title: long(41) }), 'module tracker tab ok: skipped: title is not 1-40 characters'],
  [ticketsTab({ id: 'ok', title: 7 }), 'module tracker tab ok: skipped: title is not 1-40 characters'],
  [ticketsTab({ id: 'ok', source: { mcp: { server: 'tracker', tool: 't' }, command: ['x'] } }), 'module tracker tab ok: skipped: source needs exactly one of mcp or command'],
  [ticketsTab({ id: 'ok', source: {} }), 'module tracker tab ok: skipped: source needs exactly one of mcp or command'],
  [ticketsTab({ id: 'ok', source: { mcp: { server: 'tracker', tool: 't', args: [1] } } }), 'module tracker tab ok: skipped: source.mcp.args is not an object'],
  [ticketsTab({ id: 'ok', source: { mcp: { server: 3, tool: 't' } } }), 'module tracker tab ok: skipped: source.mcp.server is not a string'],
  [ticketsTab({ id: 'ok', source: { command: [] } }), 'module tracker tab ok: skipped: source.command is not a list of 1-32 items'],
  [ticketsTab({ id: 'ok', source: { command: Array(33).fill('x') } }), 'module tracker tab ok: skipped: source.command is not a list of 1-32 items'],
  [ticketsTab({ id: 'ok', source: { command: ['x', 1] } }), 'module tracker tab ok: skipped: source.command holds an item that is not a string'],
  [ticketsTab({ id: 'ok', rows: { pattern: '(?<title>' } }), 'module tracker tab ok: skipped: rows.pattern does not compile'],
  [ticketsTab({ id: 'ok', rows: { pattern: '^- (?<name>.+)$' } }), 'module tracker tab ok: skipped: rows.pattern has no title group'],
  [ticketsTab({ id: 'ok', rows: { pattern: `(?<title>${long(500)})` } }), 'module tracker tab ok: skipped: rows.pattern is not 1-500 characters'],
  [ticketsTab({ id: 'ok', rows: { ...ROWS, detail: '^(?<other>.+)$' } }), 'module tracker tab ok: skipped: rows.detail has no detail group'],
  [ticketsTab({ id: 'ok', rows: { ...ROWS, url: 'https://example.com/{id}' } }), 'module tracker tab ok: skipped: rows.url names {id}, not a group of rows.pattern'],
  [ticketsTab({ id: 'ok', rows: { ...ROWS, empty: '(' } }), 'module tracker tab ok: skipped: rows.empty does not compile'],
  [ticketsTab({ id: 'ok', rows: 'x' }), 'module tracker tab ok: skipped: rows is not an object'],
  [ticketsTab({ id: 'ok', source: { mcp: { server: 'tracker', tool: 't', args: { p: '{select:nope}' } } } }), 'module tracker tab ok: skipped: source names {select:nope}, which no select declares'],
  [ticketsTab({ id: 'ok', select: [PROJECT_SELECT, { ...PROJECT_SELECT, id: 'b' }, { ...PROJECT_SELECT, id: 'c' }, { ...PROJECT_SELECT, id: 'd' }] }), 'module tracker tab ok: skipped: select is not a list of at most 3'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, type: 'date' }] }), 'module tracker tab ok: skipped: select[0].type is not "string" or "number"'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, label: '' }] }), 'module tracker tab ok: skipped: select[0].label is not 1-40 characters'],
  [ticketsTab({ id: 'ok', select: [PROJECT_SELECT, PROJECT_SELECT] }), 'module tracker tab ok: skipped: select[1].id project is used twice'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, options: { ...PROJECT_SELECT.options, pattern: '^(?<label>.+)$' } }] }), 'module tracker tab ok: skipped: select[0].options.pattern has no value group'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, default: 7 }] }), 'module tracker tab ok: skipped: select[0].default is not a dot path'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, all: '' }] }), 'module tracker tab ok: skipped: select[0].all is not 1-40 characters'],
  [ticketsTab({ id: 'ok', select: [{ ...PROJECT_SELECT, all: long(41) }] }), 'module tracker tab ok: skipped: select[0].all is not 1-40 characters'],
  [ticketsTab({ id: 'tickets' }), 'module tracker tab tickets: skipped: id is used twice in the module'],
]

test('a pane id over 64 characters, or one another module already has, is skipped with its line', async ($, on) => {
  const longName = `tracker-${long(50)}`
  const modules = [
    { name: 'a', root: '/plugins/a' },
    { name: 'a-b', root: '/plugins/ab' },
    { name: longName, root: '/plugins/long' },
  ]
  const w = world(
    on,
    {
      [SNAPSHOT]: snapshotWith(modules),
      '/plugins/a/rigger-module.json': manifestWith([ticketsTab({ id: 'b-c' })]),
      '/plugins/ab/rigger-module.json': manifestWith([ticketsTab({ id: 'c' })]),
      '/plugins/long/rigger-module.json': manifestWith([ticketsTab()]),
    },
    {},
  )
  await start($, w)
  const { texts } = await riggerLines($)
  expect(texts.filter(line => line.includes(': skipped: '))).toEqual([
    "module a-b tab c: skipped: pane id m-a-b-c is taken by another module's tab",
    `module ${longName} tab tickets: skipped: pane id m-${longName}-tickets is not 1-64 letters, digits, _ or -`,
  ])
  expect(w.opened.filter(id => id.startsWith('m-'))).toEqual(['m-a-b-c'])
})

test('each invalid tab entry is skipped with its line in the Rigger pane, and the valid one stays', async ($, on) => {
  // Each entry written as `ok` gets an id of its own, so no two collide; `#N` counts the valid tab first.
  const entries = INVALID.map(([entry], index) =>
    typeof entry === 'object' && entry !== null && (entry as { id?: unknown }).id === 'ok' ? { ...entry, id: `e${index}` } : entry,
  )
  const lines = INVALID.map(([, line], index) => line.replace(' tab ok:', ` tab e${index}:`).replace(' tab #1:', ` tab #${index + 2}:`))
  const w = world(on, files([ticketsTab(), ...entries]), { 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  const { texts, buttons } = await riggerLines($)
  const skipped = texts.filter(line => line.includes(': skipped: '))
  // A compile error's own message follows the reason, so those lines are compared up to it.
  expect(skipped.map(line => line.replace(/(does not compile).*/u, '$1'))).toEqual(lines)
  expect(buttons).toContain('[x] Tickets')
  expect(w.opened.filter(id => id.startsWith('m-'))).toEqual([TICKETS])
})

test('no snapshot, an unreadable manifest, no panel key or panels.tabs not a list add no tab and break nothing', async ($, on) => {
  const cases: [string, Record<string, string>, string[]][] = [
    ['no snapshot', { [MANIFEST]: manifestWith([ticketsTab()]) }, []],
    ['no manifest', { [SNAPSHOT]: snapshotWith(TRACKER) }, []],
    ['manifest not JSON', { [SNAPSHOT]: snapshotWith(TRACKER), [MANIFEST]: '{"panel":' }, []],
    ['no panel key', { [SNAPSHOT]: snapshotWith(TRACKER), [MANIFEST]: JSON.stringify({ schema: 1 }) }, []],
    ['modules not a list', { [SNAPSHOT]: snapshotWith({ tracker: ROOT }), [MANIFEST]: manifestWith([ticketsTab()]) }, []],
    [
      'tabs not a list',
      { [SNAPSHOT]: snapshotWith(TRACKER), [MANIFEST]: JSON.stringify({ panel: { tabs: {} } }) },
      ['module tracker tab *: skipped: panel.tabs is not a list'],
    ],
  ]
  const w = world(on, {}, {})
  for (const [what, given, lines] of cases) {
    for (const key of Object.keys(w.files)) delete w.files[key]
    Object.assign(w.files, given)
    w.opened.length = 0
    await start($, w)
    const { texts } = await riggerLines($)
    expect({ what, opened: w.opened, lines: texts.filter(line => line.startsWith('module ')) }).toEqual({
      what,
      opened: ['rigger-panel', 'rigger-issues', 'rigger-plan', 'rigger-envs', 'rigger-inbox'],
      lines,
    })
  }
})

test('panel.tabs_off and the Tabs toggle treat a module tab like a built-in one', async ($, on) => {
  const w = world(on, { ...files([ticketsTab()]), [SNAPSHOT]: snapshotWith(TRACKER, { tabs_off: [TICKETS] }) }, { 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  expect(w.opened).not.toContain(TICKETS)
  expect((await riggerLines($)).buttons).toContain('[ ] Tickets')

  const ui = await $.ui.mount({ ...paneOf('rigger-panel', 'Rigger'), surface: 'desktop' })
  await ui.press({ key: `tab-${TICKETS}` })
  await ui.unmount()
  expect(w.opened.at(-1)).toBe(TICKETS)
  expect(w.stored['tabs:/repo']).toEqual({ on: [TICKETS], off: [] })

  const again = await $.ui.mount({ ...paneOf('rigger-panel', 'Rigger'), surface: 'desktop' })
  await again.press({ key: `tab-${TICKETS}` })
  await again.unmount()
  expect(w.closed).toEqual([TICKETS])
  expect(w.stored['tabs:/repo']).toEqual({ on: [], off: [TICKETS] })
})

test('a snapshot written after session start is read at the first main-loop turn, once', async ($, on) => {
  const w = world(on, { [MANIFEST]: manifestWith([ticketsTab()]) }, { 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  expect(w.opened).not.toContain(TICKETS)

  w.files[SNAPSHOT] = snapshotWith(TRACKER, { tabs_off: ['rigger-plan'] })
  await $.turn.start({ text: '', turnId: 's', agentId: 'a1' } as never)
  expect(w.opened).not.toContain(TICKETS)
  await $.turn.start({ text: '', turnId: 't' } as never)
  expect(w.opened.at(-1)).toBe(TICKETS)
  expect(w.closed).toEqual(['rigger-plan'])
  expect((await riggerLines($)).buttons).toContain('[x] Tickets')

  w.files[SNAPSHOT] = snapshotWith(TRACKER, { tabs_off: ['rigger-envs'] })
  await $.turn.start({ text: '', turnId: 'u' } as never)
  expect(w.closed).toEqual(['rigger-plan'])
})

test('/harness reads every open module tab again', async ($, on) => {
  const w = world(on, files([ticketsTab()]), { 'list-tickets': text(TICKETS_TEXT) })
  await start($, w)
  const { ui } = await drawTab($, w)
  await ui.unmount()
  expect(w.calls).toHaveLength(1)
  await $.command.run({ command: 'harness', args: '' } as never)
  expect(w.calls).toHaveLength(2)
})
