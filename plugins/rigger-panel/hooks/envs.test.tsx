import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import cases from '../tests/env-cases'
import { parseEnvironments } from './envs'

const doc = (environments: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  JSON.stringify({ version: 1, project: 'demo', environments, ...extra })

const SITE = { url: 'https://example.com' }

const namesOf = (text: string) => {
  const parsed = parseEnvironments(text)
  if (!parsed.ok) throw new Error(parsed.reason)
  return parsed.rows.map(row => row.name)
}

const reasonOf = (text: string) => {
  const parsed = parseEnvironments(text)
  if (parsed.ok) throw new Error('parsed')
  return parsed.reason
}

test('local, dev and prod come first in that order, other names follow in file order', () => {
  const text = doc({ staging: SITE, prod: SITE, qa: SITE, local: SITE, dev: SITE })
  expect(namesOf(text)).toEqual(['local', 'dev', 'prod', 'staging', 'qa'])
})

test('no field is required and every field is kept', () => {
  const full = {
    url: 'https://example.com',
    admin: 'https://example.com/wp-admin',
    server: 'example.com',
    account: 'demo',
    role: 'DEV',
    path: '~/sites/example.com',
    branch: 'main',
    database: 'demo_db',
    deploy: 'run the release script',
    notes: 'none',
  }
  const parsed = parseEnvironments(doc({ dev: full, local: SITE }))
  expect(parsed).toEqual({ ok: true, project: 'demo', rows: [{ name: 'local', ...SITE }, { name: 'dev', ...full }] })
})

test('a name or a value that looks like a secret refuses the whole file and names no value', () => {
  const cases: [string, unknown][] = [
    ['password', { ...SITE, password: 'x' }],
    ['passwd', { ...SITE, passwd: 'x' }],
    ['token', { ...SITE, token: 'x' }],
    ['secret', { ...SITE, secret: 'x' }],
    ['key', { ...SITE, key: 'x' }],
    ['a name in another case', { ...SITE, DB_PASSWORD: 'x' }],
    ['a connection string', { ...SITE, database: 'mysql://user:hunter2@example.com/app' }],
    ['credentials in the url', { url: 'https://user:hunter2@example.com' }],
  ]
  for (const [step, env] of cases) {
    const parsed = parseEnvironments(doc({ local: SITE, prod: env }))
    expect({ step, ok: parsed.ok }).toEqual({ step, ok: false })
    const reason = reasonOf(doc({ local: SITE, prod: env }))
    expect({ step, hit: /secret/.test(reason), leaked: /hunter2/.test(reason) }).toEqual({ step, hit: true, leaked: false })
  }
})

test('a secret outside the environments object refuses the file too', () => {
  expect(reasonOf(doc({ local: SITE }, { apiToken: 'x' }))).toMatch(/secret/)
})

test('a user name in a URL without a password is not a secret', () => {
  expect(namesOf(doc({ local: { url: 'https://example.com', deploy: 'git@example.com:demo/app.git' } }))).toEqual(['local'])
})

test('invalid JSON gives a reason and no rows', () => {
  expect(reasonOf('{ "version": 1,')).toMatch(/^invalid JSON/)
  expect(reasonOf('')).toMatch(/^invalid JSON/)
})

test('a wrong shape gives its own reason', () => {
  const cases: [string, string, RegExp][] = [
    ['an array', '[]', /expected an object/],
    ['version 2', JSON.stringify({ version: 2, environments: {} }), /"version"/],
    ['no environments', JSON.stringify({ version: 1 }), /"environments"/],
    ['an environment that is not an object', doc({ dev: 'https://example.com' }), /"dev" must be an object/],
    ['a number for a text', doc({ dev: { ...SITE, branch: 5 } }), /"dev.branch" must be a string/],
    ['a url that is not http', doc({ dev: { url: 'javascript:alert(1)' } }), /an http\(s\) URL/],
  ]
  for (const [step, text, reason] of cases) {
    expect({ step, reason: reasonOf(text).match(reason)?.[0] !== undefined }).toEqual({ step, reason: true })
  }
})

test('test_unknown_key_is_ignored: an unknown key is neither kept nor checked, whatever its shape', () => {
  for (const value of ['text', 5, true, null, [1], { a: 1 }]) {
    const parsed = parseEnvironments(doc({ local: SITE }, { extra: value }))
    expect({ value, ok: parsed.ok, kept: parsed.ok && 'extra' in parsed }).toEqual({ value, ok: true, kept: false })
    expect(parsed.ok && parsed.rows).toEqual([{ name: 'local', ...SITE }])
  }
})

test('every shared case gives the reason rigger-core env_check.py gives', () => {
  expect(cases.length).toBeGreaterThan(90)
  for (const item of cases) {
    const parsed = parseEnvironments(item.text)
    const reason = parsed.ok ? null : item.exact || item.reason === null ? parsed.reason : parsed.reason.slice(0, item.reason.length)
    expect({ name: item.name, reason }).toEqual({ name: item.name, reason: item.reason })
  }
})

// --- the pane ---

const PANE = {
  plugin: 'rigger-panel',
  component: 'Pane',
  requestId: 'rigger-envs',
  props: {
    title: 'Environments',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const FILE = '/proj/.claude/environments.json'
const norm = (path: string) => path.replaceAll('\\', '/').replace(/^\/?[A-Za-z]:(?=\/)/, '')

const world = (on: On, files: Record<string, string>) => {
  const reads: string[] = []
  const opened: string[] = []
  on('env.get', () => ({ value: undefined }))
  on('command.register', () => ({ value: { command: 'harness' } }))
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  const clock = mock.clock(on, { now: 1000 })
  on('mcp.call', () => ({ deny: 'no MCP server in this test' }))
  on('process.run', () => ({ deny: 'no gh in this test' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000 }, rateLimits: [] } }))
  on('fs.read', (_, e) => {
    reads.push(norm(e.path))
    const text = files[norm(e.path)]
    return text === undefined ? { deny: 'missing' } : { value: text }
  })
  return Object.assign(reads, { clock, opened })
}

const open = async ($: Engine, reads: { clock: { settle: () => Promise<unknown> } }, surface: 'desktop' | 'terminal' = 'terminal') => {
  await $.session.start({ cwd: '/proj', surface, isInteractive: true })
  await reads.clock.settle()
  return $.ui.mount({ ...PANE, surface })
}

test('no file draws one line and no environment', async ($, on) => {
  const reads = world(on, {})
  const ui = await open($, reads)
  expect((await ui.find({ type: 'Text', text: /^No / }))?.text).toBe('No `.claude/environments.json` in this project. Create one with /rigger-core:environments.')
  expect(await ui.find({ type: 'Link' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /local|dev|prod/ })).toBeUndefined()
  await ui.unmount()
})

test('the pane reads the file under the session directory and draws names and links', async ($, on) => {
  const reads = world(on, {
    [FILE]: doc({
      prod: { url: 'https://example.com', server: 'example.com', account: 'demo', role: 'PROD' },
      local: { url: 'http://demo.test', admin: 'http://demo.test/wp-admin' },
    }),
  })
  const ui = await open($, reads)
  expect(reads).toContain(FILE)
  expect((await ui.find({ type: 'Text', text: /^local$/ }))?.text).toBe('local')
  const hrefOf = async (text: RegExp) => ((await ui.find({ type: 'Link', text })) as { props?: { href?: string } } | undefined)?.props?.href
  expect(await hrefOf(/^http:\/\/demo\.test$/)).toBe('http://demo.test')
  expect(await hrefOf(/wp-admin$/)).toBe('http://demo.test/wp-admin')
  expect(await hrefOf(/^https:\/\/example\.com$/)).toBe('https://example.com')
  expect(await hrefOf(/other/)).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /^example\.com$/ }))?.text).toBe('example.com')
  expect((await ui.find({ type: 'Text', text: /^demo$/ }))?.text).toBe('demo')
  expect((await ui.find({ type: 'Button', key: 'refresh' }))?.text).toBe('Refresh')
  await ui.unmount()
})

test('a refused file draws one reason line and nothing from the file', async ($, on) => {
  const reads = world(on, { [FILE]: doc({ prod: { ...SITE, password: 'x' }, local: { url: 'https://example.com/seen' } }) })
  const ui = await open($, reads)
  expect((await ui.find({ type: 'Text', text: /secret/ }))?.text).toMatch(/^Environments unavailable: file refused/)
  expect(await ui.find({ type: 'Link' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /seen/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^(prod|local)$/ })).toBeUndefined()
  await ui.unmount()
})

test('invalid JSON draws one reason line', async ($, on) => {
  const reads = world(on, { [FILE]: '{ nope' })
  const ui = await open($, reads)
  expect((await ui.find({ type: 'Text', text: /invalid JSON/ }))?.text).toMatch(/^Environments unavailable: invalid JSON/)
  expect(await ui.find({ type: 'Link' })).toBeUndefined()
  await ui.unmount()
})

test('Refresh reads the file again', async ($, on) => {
  const files: Record<string, string> = { [FILE]: doc({ local: SITE }) }
  const reads = world(on, files)
  const ui = await open($, reads)
  expect(await ui.find({ type: 'Text', text: /^dev$/ })).toBeUndefined()
  const before = reads.length

  files[FILE] = doc({ local: SITE, dev: { url: 'https://example.com' } })
  await $.ui.press({ plugin: 'rigger-panel', key: 'refresh' })

  expect(reads.length).toBe(before + 1)
  expect((await ui.find({ type: 'Text', text: /^dev$/ }))?.text).toBe('dev')
  await ui.unmount()
})

test('/harness opens the environments pane too', async ($, on) => {
  const { opened } = world(on, {})
  await $.session.start({ cwd: '/proj', surface: 'desktop', isInteractive: true })
  expect(opened).toContain('rigger-envs')
  opened.length = 0
  await $.command.run({ command: 'harness', args: '' } as never)
  expect(opened).toContain('rigger-envs')
})

test('an environment without a url is drawn from its other fields', () => {
  const parsed = parseEnvironments(doc({ staging: { branch: 'main', notes: 'no site here' } }))
  expect(parsed.ok).toBe(true)
  expect(parsed.ok && parsed.rows[0]).toEqual({ name: 'staging', branch: 'main', notes: 'no site here' })
})

test('Refresh reads the folder the session is in now', async ($, on) => {
  const moved = '/moved/.claude/environments.json'
  const reads = world(on, { [moved]: doc({ dev: { url: 'https://example.com/moved' } }) })
  let here: string | undefined
  on('session.cwd', () => (here ? { value: here } : { deny: 'not known yet' }))
  const ui = await open($, reads)
  expect((await ui.find({ type: 'Text', text: /^No / }))?.text).toContain('environments.json')

  here = '/moved'
  await $.ui.press({ plugin: 'rigger-panel', key: 'refresh' })
  await reads.clock.settle()

  expect(reads).toContain(moved)
  await ui.unmount()
})

test('a session that moved after the first read is read again when the pane is drawn, with no press', async ($, on) => {
  const moved = '/moved/.claude/environments.json'
  const reads = world(on, { [moved]: doc({ dev: { url: 'https://example.com/moved' } }) })
  let here = '/proj'
  on('session.cwd', () => ({ value: here }))
  await $.session.start({ cwd: '/proj', surface: 'terminal', isInteractive: true })
  await reads.clock.settle()
  expect(reads).toEqual([FILE])

  here = '/moved'
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(reads).toContain(moved)
  expect((await ui.find({ type: 'Text', text: /^dev$/ }))?.text).toBe('dev')
  await ui.unmount()
})

test('drawing the pane again does not read again while the session stays put, found file or not', async ($, on) => {
  const reads = world(on, { '/with/.claude/environments.json': doc({ dev: SITE }) })
  let here = '/proj'
  on('session.cwd', () => ({ value: here }))
  await $.session.start({ cwd: '/proj', surface: 'terminal', isInteractive: true })
  await reads.clock.settle()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.redraw()
  await ui.redraw()
  expect(reads).toEqual([FILE])

  here = '/with'
  await ui.redraw()
  await ui.redraw()
  await ui.redraw()
  expect(reads).toEqual([FILE, '/with/.claude/environments.json'])
  expect((await ui.find({ type: 'Text', text: /^dev$/ }))?.text).toBe('dev')
  await ui.unmount()
})

test('a pane drawn before the session start finished the first read still shows the file', async ($, on) => {
  const reads = world(on, { [FILE]: doc({ dev: SITE }) })
  on('session.cwd', () => ({ value: '/proj' }))

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })

  expect(reads).toContain(FILE)
  expect((await ui.find({ type: 'Text', text: /^dev$/ }))?.text).toBe('dev')
  await ui.unmount()
})

test('repository is kept when it is a web address, refused otherwise', () => {
  const parsed = parseEnvironments(doc({ local: SITE }, { repository: 'https://example.com/example/app' }))
  expect(parsed.ok && parsed.repository).toBe('https://example.com/example/app')
  expect(parseEnvironments(doc({ local: SITE }, { repository: 'git@example.com:example/app.git' })).ok).toBe(false)
  const secret = parseEnvironments(doc({ local: SITE }, { repository: 'https://user:hunter2@example.com/app' }))
  expect(secret.ok).toBe(false)
})

test('the pane links the repository', async ($, on) => {
  const reads = world(on, { [FILE]: doc({ local: SITE }, { repository: 'https://example.com/example/app' }) })
  const ui = await open($, reads, 'desktop')
  expect(await ui.find({ type: 'Link', text: /example\/app/ })).toBeDefined()
  await ui.unmount()
})

// --- the cards ---

type Node = { type?: string; props?: Record<string, unknown>; children?: (Node | string)[] }

const nodes = (tree: unknown, match: (node: Node) => boolean): Node[] => {
  const found: Node[] = []
  const visit = (node: Node | string) => {
    if (typeof node === 'string') return
    if (match(node)) found.push(node)
    node.children?.forEach(visit)
  }
  visit(tree as Node)
  return found
}

const textOf = (node: Node | string): string =>
  typeof node === 'string' ? node : (node.children ?? []).map(textOf).join('')

const CARD = (node: Node) => node.props?.borderStyle === 'round'

const FULL = {
  url: 'https://example.com',
  admin: 'https://example.com/wp-admin',
  server: 'example.com',
  account: 'demo',
  role: 'PROD',
  path: '~/sites/example.com/wp-content/themes/a-very-long-theme-name-that-wraps',
  branch: 'main',
  database: 'demo_db',
  deploy: 'run the release script, then check the site by hand',
  notes: 'none',
}

// The world is built once per test: hooks cannot be added after the first call on `$`.
const cardsWorld = async ($: Engine, on: On) => {
  const files = { [FILE]: doc({ prod: FULL, local: { url: 'http://demo.test', role: 'LOCAL' } }, { repository: 'https://example.com/example/app' }) }
  const reads = world(on, files)
  await $.session.start({ cwd: '/proj', surface: 'terminal', isInteractive: true })
  await reads.clock.settle()
  return async (bodyColumns: number, surface: 'desktop' | 'terminal') => {
    const ui = await $.ui.mount({ ...PANE, surface, props: { ...PANE.props, bodyColumns } })
    const tree = await ui.drawn()
    await ui.unmount()
    return tree
  }
}

test('each environment is one rounded card, the loud one is PROD', async ($, on) => {
  const draw = await cardsWorld($, on)
  const cards = nodes(await draw(40, 'desktop'), CARD)
  expect(cards.map(card => textOf(card.children?.[0] ?? '').trim())).toEqual(['local LOCAL', 'prod PROD'])
  expect(cards.map(card => card.props?.borderColor)).toEqual([undefined, 'red'])
  expect(cards.map(card => card.props?.borderDimColor)).toEqual([true, undefined])
  const badges = cards.flatMap(card => nodes(card, node => node.type === 'Text' && typeof node.props?.backgroundColor === 'string'))
  expect(badges.map(badge => [textOf(badge), badge.props?.backgroundColor])).toEqual([
    [' LOCAL ', 'blue'],
    [' PROD ', 'red'],
  ])
})

test('a card lists links first, then the labels in order, all in one column width', async ($, on) => {
  const draw = await cardsWorld($, on)
  const [, prod] = nodes(await draw(40, 'terminal'), CARD)
  const labels = nodes(prod, node => node.type === 'Box' && typeof node.props?.width === 'number')
  expect(labels.map(label => textOf(label))).toEqual(['Site', 'Admin', 'Branch', 'Server', 'Account', 'Path', 'Database', 'Deploy', 'Notes'])
  expect(new Set(labels.map(label => label.props?.width))).toEqual(new Set([8]))
  expect(nodes(prod, node => node.type === 'Link').map(link => link.props?.href)).toEqual([FULL.url, FULL.admin])
  // The role is the badge, not a row.
  expect(nodes(prod, node => node.type === 'Text' && textOf(node) === 'PROD')).toEqual([])
})

test('at 40 and at 30 columns a value keeps room beside its label and wraps inside its own column', async ($, on) => {
  const draw = await cardsWorld($, on)
  for (const columns of [40, 30]) {
    const [, prod] = nodes(await draw(columns, 'terminal'), CARD)
    const label = nodes(prod, node => typeof node.props?.width === 'number')[0]?.props?.width as number
    // Border and padding take 4 columns, the label and the gap between the columns the rest.
    const room = columns - 4 - label - 1
    expect({ columns, room: room >= 15 }).toEqual({ columns, room: true })
    const values = nodes(prod, node => node.props?.flexShrink === 1 && node.props?.flexGrow === 1)
    expect(values).toHaveLength(9)
    expect(textOf(values[5] ?? '')).toBe(FULL.path)
    expect(nodes(values, node => typeof node.props?.width === 'number')).toEqual([])
  }
})