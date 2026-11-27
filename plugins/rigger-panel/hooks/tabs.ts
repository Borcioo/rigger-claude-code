import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { TabChoice, Tabs } from '../types'
import { configDir } from './config-dir'
import { NO_MODULE_TABS, readModuleTabs } from './module-tabs'

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

// The same reference as in module-tabs.ts.
const moduleTabs = atom({ plugin: 'rigger-panel', key: 'moduleTabs' } as const, NO_MODULE_TABS, {
  shape: 'module-tabs-1',
})

const stringList =(value: unknown) =>
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

// rigger-core's snapshot of this session: `panel.tabs_off` (anything unreadable is none), the modules it
// lists, and whether the file was there at all.
export async function readSnapshot($: EngineInterface) {
  try {
    const dir = await configDir($)
    if (dir === undefined) return { found: false, tabsOff: [] as string[], modules: undefined }
    const text = await $.fs.read(`${dir}/rigger/state/${await $.session.id()}.resolved.json`)
    try {
      const data = JSON.parse(text)
      return { found: true, tabsOff: stringList(data?.config?.effective?.panel?.tabs_off), modules: data?.modules as unknown }
    } catch {
      return { found: true, tabsOff: [] as string[], modules: undefined }
    }
  } catch {
    return { found: false, tabsOff: [] as string[], modules: undefined }
  }
}

async function readTabsOff($: EngineInterface) {
  return (await readSnapshot($)).tabsOff
}

// The built-in tabs, then the module tabs of this session.
async function allTabs($: EngineInterface): Promise<Tab[]> {
  const listed = await read($, moduleTabs)
  return [...TABS, ...listed.tabs.map(tab => ({ id: tab.pane, title: tab.title }))]
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

// Computes which tabs are on for the session's project, the module tabs among them, and keeps both for
// drawing. `found`: the snapshot was there.
export async function loadTabs($: EngineInterface, fallback?: string) {
  const project = await projectKey($, fallback)
  const [snapshot, choice] = await Promise.all([readSnapshot($), readChoice($, project)])
  const listed = await readModuleTabs($, snapshot.modules)
  await update($, moduleTabs, last => ({ ...listed, recheck: last.recheck }))
  const on = (await allTabs($)).filter(tab => isTabOn(tab.id, snapshot.tabsOff, choice)).map(tab => tab.id)
  await update($, tabs, () => ({ project, on }))
  return { on, found: snapshot.found }
}

// The Rigger pane, then each tab that is on.
export async function openTabs($: EngineInterface, on: readonly string[]) {
  const listed = await allTabs($)
  return Promise.all(
    [RIGGER_TAB, ...listed.filter(tab => on.includes(tab.id))].map(tab => $.ui.open({ id: tab.id, title: tab.title })),
  )
}

// rigger-core's SessionStart hooks may write the snapshot after the panel's session.start. When it was
// missing then, the first main-loop turn reads it once more: a tab now on opens, one now off closes.
export async function recheckTabs($: EngineInterface) {
  if (!(await read($, moduleTabs)).recheck) return
  let mine = false
  await update($, moduleTabs, last => {
    mine = last.recheck
    return last.recheck ? { ...last, recheck: false } : last
  })
  if (!mine) return
  const before = (await read($, tabs)).on
  const { on } = await loadTabs($)
  const listed = await allTabs($)
  await Promise.all([
    ...listed.filter(tab => on.includes(tab.id) && !before.includes(tab.id)).map(tab => $.ui.open({ id: tab.id, title: tab.title })),
    ...before.filter(id => !on.includes(id)).map(id => $.ui.close({ id })),
  ])
}

// Switches one tab for the session's project: stored first, then the pane opens or closes.
export async function toggleTab($: EngineInterface, id: string) {
  const listed = await allTabs($)
  const tab = listed.find(item => item.id === id)
  if (tab === undefined) return
  const project = await projectKey($)
  const [tabsOff, choice] = await Promise.all([readTabsOff($), readChoice($, project)])
  const turnOn = !isTabOn(id, tabsOff, choice)
  const without = (list: string[]) => list.filter(item => item !== id)
  const next: TabChoice = turnOn
    ? { on: [...without(choice.on), id], off: without(choice.off) }
    : { on: without(choice.on), off: [...without(choice.off), id] }
  if (project !== undefined) await $.store.set(storeKey(project), next)
  const on = listed.filter(item => isTabOn(item.id, tabsOff, next)).map(item => item.id)
  await update($, tabs, () => ({ project, on }))
  if (turnOn) await $.ui.open({ id: tab.id, title: tab.title })
  else await $.ui.close({ id: tab.id })
}
