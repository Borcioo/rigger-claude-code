import { atom, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { TabChoice, Tabs } from '../types'
import { configDir } from './config-dir'

// A tab is one pane. The Rigger pane is always open; every other tab can be switched off per project.
export type Tab = { id: string; title: string }

export const RIGGER_TAB: Tab = { id: 'rigger-panel', title: 'Rigger' }

// The tabs the person can switch, in the order they open and are listed. Modules may append theirs.
export const TABS: Tab[] = [
  { id: 'rigger-issues', title: 'Issues' },
  { id: 'rigger-plan', title: 'Plan' },
  { id: 'rigger-envs', title: 'Environments' },
]

const RUN_MS = 5_000
const NO_CHOICE: TabChoice = { on: [], off: [] }

// The ids that are on, as last computed; drawn by the Rigger pane's Tabs row.
export const tabs = atom({ plugin: 'rigger-panel', key: 'tabs' } as const, { on: TABS.map(tab => tab.id) } as Tabs, {
  shape: 'tabs-1',
})

const stringList = (value: unknown) =>
  Array.isArray(value) && value.every(item => typeof item === 'string') ? (value as string[]) : []

// The per-project choice wins over the default off list.
export const isTabOn = (id: string, tabsOff: readonly string[], choice: TabChoice) =>
  !choice.off.includes(id) && (!tabsOff.includes(id) || choice.on.includes(id))

const slashes = (path: string) => path.replaceAll('\\', '/').replace(/(?<=.)\/+$/u, '')

// The main checkout's folder, so every worktree of a repository shares one choice: the parent of git's
// common dir. Outside git (or when git fails) the session folder itself.
export async function projectKey($: EngineInterface, fallback?: string) {
  const cwd = (await $.session.cwd().catch(() => undefined)) ?? fallback
  if (!cwd) return undefined
  try {
    const ran = await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], {
      cwd,
      timeoutMs: RUN_MS,
    })
    const lines = ran.stdout.trim().split('\n')
    const dir = slashes(lines[0]?.trim() ?? '')
    // An old git echoes the unknown flag back; only one absolute path is an answer.
    if (ran.exitCode === 0 && lines.length === 1 && /^(?:[A-Za-z]:)?\//u.test(dir)) {
      const parent = dir.replace(/\/[^/]+$/u, '')
      if (parent !== dir) return parent === '' || /^[A-Za-z]:$/u.test(parent) ? `${parent}/` : parent
    }
  } catch {
    // Not started, refused or timed out: key by the folder.
  }
  return slashes(cwd)
}

// `panel.tabs_off` from rigger-core's snapshot of this session's config; anything unreadable is none.
export async function readTabsOff($: EngineInterface) {
  try {
    const dir = await configDir($)
    if (dir === undefined) return []
    const text = await $.fs.read(`${dir}/rigger/state/${await $.session.id()}.resolved.json`)
    return stringList(JSON.parse(text)?.config?.effective?.panel?.tabs_off)
  } catch {
    return []
  }
}

const storeKey = (project: string) => `tabs:${project}`

export async function readChoice($: EngineInterface, project: string | undefined): Promise<TabChoice> {
  if (project === undefined) return NO_CHOICE
  try {
    const stored = (await $.store.get(storeKey(project))) as { on?: unknown; off?: unknown } | undefined
    return { on: stringList(stored?.on), off: stringList(stored?.off) }
  } catch {
    return NO_CHOICE
  }
}

// Computes which tabs are on for the session's project and keeps it for drawing.
export async function loadTabs($: EngineInterface, fallback?: string) {
  const project = await projectKey($, fallback)
  const [tabsOff, choice] = await Promise.all([readTabsOff($), readChoice($, project)])
  const on = TABS.filter(tab => isTabOn(tab.id, tabsOff, choice)).map(tab => tab.id)
  await update($, tabs, () => ({ project, on }))
  return on
}

// The Rigger pane, then each tab that is on.
export function openTabs($: EngineInterface, on: readonly string[]) {
  return Promise.all(
    [RIGGER_TAB, ...TABS.filter(tab => on.includes(tab.id))].map(tab => $.ui.open({ id: tab.id, title: tab.title })),
  )
}

// Switches one tab for the session's project: stored first, then the pane opens or closes.
export async function toggleTab($: EngineInterface, id: string) {
  const tab = TABS.find(item => item.id === id)
  if (tab === undefined) return
  const project = await projectKey($)
  const [tabsOff, choice] = await Promise.all([readTabsOff($), readChoice($, project)])
  const turnOn = !isTabOn(id, tabsOff, choice)
  const without = (list: string[]) => list.filter(item => item !== id)
  const next: TabChoice = turnOn
    ? { on: [...without(choice.on), id], off: without(choice.off) }
    : { on: without(choice.on), off: [...without(choice.off), id] }
  if (project !== undefined) await $.store.set(storeKey(project), next)
  const on = TABS.filter(item => isTabOn(item.id, tabsOff, next)).map(item => item.id)
  await update($, tabs, () => ({ project, on }))
  if (turnOn) await $.ui.open({ id: tab.id, title: tab.title })
  else await $.ui.close({ id: tab.id })
}
