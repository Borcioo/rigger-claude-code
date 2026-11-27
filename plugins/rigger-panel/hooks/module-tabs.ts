import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Json, ModuleOption, ModulePane, ModuleRow, ModuleTab, ModuleTabs, RowRules, SelectRule, TabSource } from '../types'
import { bounded } from './issue-flow'

// A module adds tabs in `panel.tabs` of its rigger-module.json. The panel knows no tool behind them: it
// runs the tab's source, cuts the text into rows with the module's regexes and draws a plain list.
// The bounds hold before any regex runs, so no output a source gives can make a pane slow.
const ID = /^[A-Za-z0-9_-]{1,32}$/u
const PANE_ID = /^[A-Za-z0-9_-]{1,64}$/u
const PLACEHOLDER = /^\{select:([^}]*)\}$/u
const TEMPLATE_GROUP = /\{([A-Za-z_][A-Za-z0-9_]*)\}/gu
const HTTPS = /^https:\/\/\S+$/u
const NUMBER = /^-?\d+(?:\.\d+)?$/u
const TEXT_MAX = 256 * 1024
const LINE_MAX = 2000
const ROW_MAX = 50
const OPTION_MAX = 200
const SOURCE_MAX = 500
const SELECT_MAX = 3
const ARGV_MAX = 32
const TITLE_MAX = 40
const REASON_MAX = 200
const CALL_MS = 20_000

export const NO_MODULE_TABS: ModuleTabs = { tabs: [], skipped: [], recheck: false }

export const moduleTabs = atom({ plugin: 'rigger-panel', key: 'moduleTabs' } as const, NO_MODULE_TABS, {
  shape: 'module-tabs-1',
})

const modulePanes = atom({ plugin: 'rigger-panel', key: 'modulePanes' } as const, {} as { [pane: string]: ModulePane }, {
  shape: 'module-panes-1',
})

const isObject = (value: unknown): value is { [key: string]: unknown } =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isText = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length >= 1 && value.length <= max

class Skip extends Error {}

const refuse = (reason: string): never => {
  throw new Skip(reason)
}

// A regex source from a manifest: bounded, compiled with `u`; its named groups, so a rule can require one.
const compiled = (value: unknown, field: string) => {
  if (typeof value !== 'string') return refuse(`${field} is not a string`)
  if (value.length < 1 || value.length > SOURCE_MAX) return refuse(`${field} is not 1-${SOURCE_MAX} characters`)
  let regex: RegExp
  try {
    regex = new RegExp(value, 'u')
  } catch (error) {
    return refuse(`${field} does not compile: ${error instanceof Error ? error.message : String(error)}`)
  }
  // Every named group is a key of `groups`, matched or not; the empty alternative always matches.
  const groups = Object.keys(new RegExp(`${value}|`, 'u').exec('')?.groups ?? {})
  return { regex, groups }
}

const sourceOf = (value: { [key: string]: unknown }, field: string, keys: readonly string[]): TabSource => {
  const kinds = ['mcp', 'command'].filter(kind => kind in value)
  const other = Object.keys(value).filter(key => !keys.includes(key))
  if (other.length > 0) return refuse(`${field} has an unknown key ${other[0]}`)
  if (kinds.length !== 1) return refuse(`${field} needs exactly one of mcp or command`)
  if (kinds[0] === 'mcp') {
    const mcp = value.mcp
    if (!isObject(mcp)) return refuse(`${field}.mcp is not an object`)
    if (!isText(mcp.server, 200)) return refuse(`${field}.mcp.server is not a string`)
    if (!isText(mcp.tool, 200)) return refuse(`${field}.mcp.tool is not a string`)
    if (mcp.args !== undefined && !isObject(mcp.args)) return refuse(`${field}.mcp.args is not an object`)
    return { mcp: { server: mcp.server, tool: mcp.tool, args: (mcp.args ?? {}) as { [key: string]: Json } } }
  }
  const argv = value.command
  if (!Array.isArray(argv) || argv.length < 1 || argv.length > ARGV_MAX) return refuse(`${field}.command is not a list of 1-${ARGV_MAX} items`)
  if (!argv.every(item => typeof item === 'string' && item.length > 0)) return refuse(`${field}.command holds an item that is not a string`)
  return { command: argv as string[] }
}

// The `{select:<id>}` values a source names.
const placeholders = (source: TabSource) =>
  ('mcp' in source ? Object.values(source.mcp.args) : source.command).flatMap(value =>
    typeof value === 'string' ? [value.match(PLACEHOLDER)?.[1]].filter((id): id is string => id !== undefined) : [],
  )

const selectOf = (value: unknown, index: number, seen: Set<string>): SelectRule => {
  const at = `select[${index}]`
  if (!isObject(value)) return refuse(`${at} is not an object`)
  if (typeof value.id !== 'string' || !ID.test(value.id)) return refuse(`${at}.id is not 1-32 letters, digits, _ or -`)
  if (seen.has(value.id)) return refuse(`${at}.id ${value.id} is used twice`)
  seen.add(value.id)
  if (!isText(value.label, TITLE_MAX)) return refuse(`${at}.label is not 1-${TITLE_MAX} characters`)
  const type = value.type ?? 'string'
  if (type !== 'string' && type !== 'number') return refuse(`${at}.type is not "string" or "number"`)
  if (!isObject(value.options)) return refuse(`${at}.options is not an object`)
  const source = sourceOf(value.options, `${at}.options`, ['mcp', 'command', 'pattern'])
  if (placeholders(source).length > 0) return refuse(`${at}.options names a select placeholder`)
  const pattern = compiled(value.options.pattern, `${at}.options.pattern`)
  if (!pattern.groups.includes('value')) return refuse(`${at}.options.pattern has no value group`)
  if (value.default !== undefined && (typeof value.default !== 'string' || !/^[^.]+(?:\.[^.]+)*$/u.test(value.default)))
    return refuse(`${at}.default is not a dot path`)
  return {
    id: value.id,
    label: value.label,
    type,
    source,
    pattern: value.options.pattern as string,
    ...(value.default === undefined ? {} : { default: value.default as string }),
  }
}

const rowsOf = (value: unknown): RowRules => {
  if (!isObject(value)) return refuse('rows is not an object')
  const pattern = compiled(value.pattern, 'rows.pattern')
  if (!pattern.groups.includes('title')) return refuse('rows.pattern has no title group')
  const rules: RowRules = { pattern: value.pattern as string }
  if (value.detail !== undefined) {
    if (!compiled(value.detail, 'rows.detail').groups.includes('detail')) return refuse('rows.detail has no detail group')
    rules.detail = value.detail as string
  }
  if (value.url !== undefined) {
    if (!isText(value.url, 2000)) return refuse('rows.url is not a string')
    const missing = [...value.url.matchAll(TEMPLATE_GROUP)].map(found => found[1] ?? '').find(name => !pattern.groups.includes(name))
    if (missing !== undefined) return refuse(`rows.url names {${missing}}, not a group of rows.pattern`)
    rules.url = value.url
  }
  if (value.empty !== undefined) {
    compiled(value.empty, 'rows.empty')
    rules.empty = value.empty as string
  }
  return rules
}

const tabOf = (module: string, root: string, value: unknown, seen: Set<string>): ModuleTab => {
  if (!isObject(value)) return refuse('not an object')
  if (typeof value.id !== 'string' || !ID.test(value.id)) return refuse('id is not 1-32 letters, digits, _ or -')
  if (seen.has(value.id)) return refuse('id is used twice in the module')
  seen.add(value.id)
  const pane = `m-${module}-${value.id}`
  if (!PANE_ID.test(pane)) return refuse(`pane id ${pane} is not 1-64 letters, digits, _ or -`)
  if (!isText(value.title, TITLE_MAX)) return refuse(`title is not 1-${TITLE_MAX} characters`)
  if (!isObject(value.source)) return refuse('source is not an object')
  const source = sourceOf(value.source, 'source', ['mcp', 'command'])
  const rows = rowsOf(value.rows)
  if (value.select !== undefined && (!Array.isArray(value.select) || value.select.length > SELECT_MAX))
    return refuse(`select is not a list of at most ${SELECT_MAX}`)
  const ids = new Set<string>()
  const select = ((value.select ?? []) as unknown[]).map((item, index) => selectOf(item, index, ids))
  const unknown = placeholders(source).find(id => !ids.has(id))
  if (unknown !== undefined) return refuse(`source names {select:${unknown}}, which no select declares`)
  return { pane, module, id: value.id, title: value.title, root, source, rows, select }
}

const reasonOf = (error: unknown) => (error instanceof Error ? error.message : String(error))

// The tabs one module's manifest declares; a broken entry is skipped with a line, never the module.
export function manifestTabs(module: string, root: string, manifest: unknown, panes: Set<string>) {
  const tabs: ModuleTab[] = []
  const skipped: string[] = []
  if (!isObject(manifest) || manifest.panel === undefined) return { tabs, skipped }
  const list = isObject(manifest.panel) ? manifest.panel.tabs : undefined
  if (!Array.isArray(list)) return { tabs, skipped: [`module ${module} tab *: skipped: panel.tabs is not a list`] }
  const seen = new Set<string>()
  list.forEach((entry, index) => {
    const name = isObject(entry) && typeof entry.id === 'string' && ID.test(entry.id) ? entry.id : `#${index + 1}`
    try {
      const tab = tabOf(module, root, entry, seen)
      if (panes.has(tab.pane)) refuse(`pane id ${tab.pane} is taken by another module's tab`)
      panes.add(tab.pane)
      tabs.push(tab)
    } catch (error) {
      if (!(error instanceof Skip)) throw error
      skipped.push(`module ${module} tab ${name}: skipped: ${error.message}`)
    }
  })
  return { tabs, skipped }
}

// The modules the rigger-core snapshot lists, each manifest read from its root. A module without a
// manifest, or one that cannot be read or parsed, adds nothing.
export async function readModuleTabs($: EngineInterface, modules: unknown) {
  const all: ModuleTabs = { tabs: [], skipped: [], recheck: false }
  if (!Array.isArray(modules)) return all
  const panes = new Set<string>()
  const entries = modules.filter(
    (entry): entry is { name: string; root: string } => isObject(entry) && typeof entry.name === 'string' && typeof entry.root === 'string',
  )
  const manifests = await Promise.all(
    entries.map(async entry => {
      try {
        return JSON.parse(await $.fs.read(`${entry.root}/rigger-module.json`)) as unknown
      } catch {
        return undefined
      }
    }),
  )
  entries.forEach((entry, index) => {
    const found = manifestTabs(entry.name, entry.root, manifests[index], panes)
    all.tabs.push(...found.tabs)
    all.skipped.push(...found.skipped)
  })
  return all
}

// --- cutting text into rows ---

// The text a regex may see: at most 256 KiB, no line over 2000 characters.
const linesOf = (text: string) =>
  text
    .slice(0, TEXT_MAX)
    .split('\n')
    .map(line => (line.endsWith('\r') ? line.slice(0, -1) : line))
    .filter(line => line.length <= LINE_MAX)

export type Cut = { kind: 'empty' } | { kind: 'rows'; rows: ModuleRow[]; capped: boolean } | { kind: 'unmatched' }

const linkOf = (rules: RowRules, groups: { [name: string]: string | undefined }) => {
  if (rules.url !== undefined) {
    const url = rules.url.replace(TEMPLATE_GROUP, (_, name: string) => encodeURIComponent(groups[name] ?? ''))
    return HTTPS.test(url) ? url : undefined
  }
  return groups.url !== undefined && HTTPS.test(groups.url) ? groups.url : undefined
}

// Fail closed: text that holds something but no row is never shown as an empty list.
export function cutRows(text: string, rules: RowRules): Cut {
  if (text.slice(0, TEXT_MAX).trim() === '') return { kind: 'empty' }
  const lines = linesOf(text)
  const empty = rules.empty === undefined ? undefined : new RegExp(rules.empty, 'u')
  if (empty && lines.some(line => empty.test(line))) return { kind: 'empty' }
  const pattern = new RegExp(rules.pattern, 'u')
  const detail = rules.detail === undefined ? undefined : new RegExp(rules.detail, 'u')
  const rows: ModuleRow[] = []
  let current: ModuleRow | undefined
  for (const line of lines) {
    const found = pattern.exec(line)
    const groups = found?.groups ?? {}
    if (found && groups.title) {
      if (rows.length === ROW_MAX) return { kind: 'rows', rows, capped: true }
      const url = linkOf(rules, groups)
      current = {
        title: groups.title,
        ...(groups.key ? { key: groups.key } : {}),
        ...(groups.detail ? { detail: groups.detail } : {}),
        ...(url ? { url } : {}),
      }
      rows.push(current)
    } else if (current && current.detail === undefined && detail) {
      const value = detail.exec(line)?.groups?.detail
      if (value) current.detail = value
    }
  }
  return rows.length === 0 ? { kind: 'unmatched' } : { kind: 'rows', rows, capped: false }
}

const UNSAFE_VALUE = /^-|\p{Cc}/u

// A select's options: `value` required, `label` else the value; values unique, at most 200.
export function cutOptions(text: string, rule: SelectRule): ModuleOption[] {
  const pattern = new RegExp(rule.pattern, 'u')
  const options: ModuleOption[] = []
  for (const line of linesOf(text)) {
    const groups = pattern.exec(line)?.groups
    const value = groups?.value
    // A value lands in MCP args and command argv: a leading `-` would read as an option there, and a
    // control character has no place in either, so such an option is dropped.
    if (!value || UNSAFE_VALUE.test(value) || options.some(option => option.value === value)) continue
    if (rule.type === 'number' && !NUMBER.test(value)) refuse(`select ${rule.id} value ${value} is not a number`)
    options.push({ value, label: groups.label || value })
    if (options.length === OPTION_MAX) break
  }
  return options
}

// --- running a source ---

const firstLine = (text: string) => {
  const line = text.split('\n').map(part => part.trim()).find(part => part !== '') ?? ''
  return line.length > REASON_MAX ? `${line.slice(0, REASON_MAX)}…` : line
}

const mcpText = (result: { content: { type: string; text?: string }[]; structuredContent?: unknown }) => {
  const blocks = result.content.filter(block => block.type === 'text')
  if (blocks.length === 0 && result.structuredContent !== undefined) return JSON.stringify(result.structuredContent, null, 2)
  return blocks.map(block => block.text ?? '').join('\n')
}

// The source's text, or why there is none. The one MCP call and the one program start a manifest
// declares: the scan in tests/test_panel.py allows these two spellings here and nowhere else.
export async function runSource(
  $: EngineInterface,
  source: TabSource,
  root: string,
  cwd: string | undefined,
): Promise<{ text: string } | { error: string }> {
  try {
    if ('mcp' in source) {
      const outcome = await bounded($, CALL_MS, $.mcp.call(source.mcp.server, source.mcp.tool, source.mcp.args))
      if (!outcome.isDone) return { error: `${source.mcp.tool} did not answer within ${CALL_MS / 1000} s` }
      const text = mcpText(outcome.value)
      if (outcome.value.isError) return { error: firstLine(text) || `${source.mcp.tool} reported an error` }
      return { text }
    }
    const argv = source.command.map(item => (item.startsWith('./') ? `${root}/${item.slice(2)}` : item))
    const outcome = await bounded($, CALL_MS, $.process.run(argv, { ...(cwd ? { cwd } : {}), timeoutMs: CALL_MS }))
    if (!outcome.isDone) return { error: `${source.command[0]} did not answer within ${CALL_MS / 1000} s` }
    const ran = outcome.value
    if (ran.exitCode !== 0) return { error: firstLine(ran.stderr) || firstLine(ran.stdout) || `exit code ${ran.exitCode}` }
    return { text: ran.stdout }
  } catch (error) {
    return { error: firstLine(reasonOf(error)) || 'the call failed with no reason' }
  }
}

// `{select:<id>}` as a whole value becomes the chosen value, a number for a number select.
const filled = (source: TabSource, rules: SelectRule[], chosen: { [id: string]: string }): TabSource => {
  const fill = <T>(value: T): T | string | number => {
    const id = typeof value === 'string' ? value.match(PLACEHOLDER)?.[1] : undefined
    const rule = rules.find(item => item.id === id)
    if (id === undefined || rule === undefined) return value
    const text = chosen[id] ?? ''
    return rule.type === 'number' ? Number(text) : text
  }
  if ('mcp' in source) {
    const args = Object.fromEntries(Object.entries(source.mcp.args).map(([key, value]) => [key, fill(value)])) as { [key: string]: Json }
    return { mcp: { ...source.mcp, args } }
  }
  return { command: source.command.map(item => String(fill(item))) }
}

// The value a dot path names in the session folder's `.claude/environments.json`; anything unreadable is none.
async function environmentValue($: EngineInterface, cwd: string | undefined, path: string) {
  if (cwd === undefined) return undefined
  try {
    let value: unknown = JSON.parse(await $.fs.read(`${cwd}/.claude/environments.json`))
    for (const part of path.split('.')) value = isObject(value) ? value[part] : undefined
    return typeof value === 'string' || typeof value === 'number' ? String(value) : undefined
  } catch {
    return undefined
  }
}

const tabLine = (tab: ModuleTab, reason: string) => `module ${tab.module} tab ${tab.id}: ${reason}`

function setPane($: EngineInterface, pane: string, change: (last: ModulePane | undefined) => ModulePane) {
  return update($, modulePanes, last => ({ ...last, [pane]: change(last[pane]) }))
}

// Claimed before the first await, so a draw and a Refresh in one tick make one read.
const claimed = new Set<string>()

// Reads a module tab's options (keeping a choice still offered), then its rows. Rows are cleared first,
// so a failed read never leaves the previous list standing as current.
export async function loadModuleTab($: EngineInterface, pane: string) {
  if (claimed.has(pane)) return
  claimed.add(pane)
  try {
    // Read before any write: a load a drawing started writes only once that drawing is done.
    const cwd = await $.session.cwd().catch(() => undefined)
    const tab = (await read($, moduleTabs)).tabs.find(item => item.pane === pane)
    if (tab === undefined) return
    await setPane($, pane, last => ({ phase: 'loading', rows: [], selects: last?.selects ?? {} }))
    const previous = (await read($, modulePanes))[pane]?.selects ?? {}
    const selects: ModulePane['selects'] = {}
    const fail = (reason: string) => setPane($, pane, () => ({ phase: 'failed', rows: [], note: tabLine(tab, reason), selects }))
    for (const rule of tab.select) {
      const ran = await runSource($, rule.source, tab.root, cwd)
      if ('error' in ran) return await fail(`select ${rule.id}: ${ran.error}`)
      let options: ModuleOption[]
      try {
        options = cutOptions(ran.text, rule)
      } catch (error) {
        return await fail(reasonOf(error))
      }
      if (options.length === 0) return await fail(`select ${rule.id} has no options`)
      const kept = previous[rule.id]?.value
      const wanted = kept ?? (rule.default === undefined ? undefined : await environmentValue($, cwd, rule.default))
      const value = options.find(option => option.value === wanted)?.value ?? options[0]?.value ?? ''
      selects[rule.id] = { options, value }
    }
    const chosen = Object.fromEntries(Object.entries(selects).map(([id, held]) => [id, held.value]))
    const ran = await runSource($, filled(tab.source, tab.select, chosen), tab.root, cwd)
    if ('error' in ran) return await fail(ran.error)
    const cut = cutRows(ran.text, tab.rows)
    if (cut.kind === 'unmatched') return await fail('output did not match its pattern')
    await setPane($, pane, () =>
      cut.kind === 'empty'
        ? { phase: 'ready', rows: [], empty: true, selects }
        : { phase: 'ready', rows: cut.rows, ...(cut.capped ? { capped: true } : {}), selects },
    )
  } finally {
    claimed.delete(pane)
  }
}

// A pick in a select: kept for the session, then the tab is read again with it.
export async function chooseOption($: EngineInterface, pane: string, id: string, value: string) {
  await setPane($, pane, last => {
    const held = last?.selects[id]
    const base = last ?? { phase: 'loading', rows: [], selects: {} }
    return held === undefined ? base : { ...base, selects: { ...base.selects, [id]: { ...held, value } } }
  })
  await loadModuleTab($, pane)
}

// /harness reads every module tab that is open again.
export async function reloadModuleTabs($: EngineInterface, on: readonly string[]) {
  const listed = await read($, moduleTabs)
  await Promise.all(listed.tabs.filter(tab => on.includes(tab.pane)).map(tab => loadModuleTab($, tab.pane)))
}
