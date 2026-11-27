export type Snapshot = {
  tokens?: number
  window: number
  percent?: number
  usd?: number
  weekly?: number
  version?: string
  lessons?: number
  guard?: string
  agents: number
  updatedAt: number
}

// One open GitHub issue as `gh issue list --json number,title,state,url` gives it.
export type IssueRow = { number: number; title: string; state: string; url: string }

// Why a read drew no rows without failing: no `gh` on PATH, `gh` not logged in, no GitHub `origin`
// remote, or no open issue assigned to the user.
export type IssueEmpty = 'no-gh' | 'not-logged-in' | 'no-origin' | 'none'

// What `listAssignedIssues` resolves: the rows, the page size, the repository read (`owner/repo`) and,
// when there are no rows, why. `capped`: a full page, so older issues are cut.
export type IssueRows = { rows: IssueRow[]; limit: 100; repo?: string; empty?: IssueEmpty; capped?: boolean }

export type PlanStep = { content: string; status: 'pending' | 'in_progress' | 'completed'; session?: string }

// Something the session did that the list may not show yet: a commit, a finished background task,
// a turn that ended without a list update.
export type PlanEvidence = { kind: 'commit' | 'task' | 'turn'; at: number }

// `updatedAt`: the last update_plan or wrap_up; `evidence`: the newest sign of progress after it;
// `touched`: the list changed in the running turn; `nudged`: the session was reminded since.
export type PlanAge = { updatedAt?: number; evidence?: PlanEvidence; touched: boolean; nudged: boolean }

// running: the call is out; started: the app confirmed it; failed: it refused or did not run, so
// Start and the fallback are offered; unconfirmed and asked: nothing is offered, so one press is
// never two sessions.
export type StartPhase = 'running' | 'started' | 'failed' | 'unconfirmed' | 'asked'

export type StartState = { phase: StartPhase; text: string }

export type Issues = {
  phase: 'loading' | 'ready' | 'failed'
  rows: IssueRow[]
  // The repository the rows come from, `owner/repo`.
  repo?: string
  // Why a ready read has no rows.
  empty?: IssueEmpty
  // The failure reason.
  note?: string
  // gh returned a full page, so older issues are cut.
  capped?: boolean
  // Whether `$.tool.list()` held the start tool at the last load. False: Start proposes the session
  // with `spawn_task` when `spawnTool` is true, else each row offers `Do it here`. Absent: not known
  // (the list failed or is not read yet), so Start as before.
  startTool?: boolean
  // Whether the same list held `spawn_task`, which proposes the session as a chip.
  spawnTool?: boolean
  // Keyed by issue number.
  starts: Record<string, StartState>
}

export type EnvRow = {
  name: string
  url?: string
  admin?: string
  // Host name and login; never a password or a key.
  server?: string
  account?: string
  // A free label such as DEV or PROD; a role starting with `prod` is drawn in red.
  role?: string
  path?: string
  branch?: string
  database?: string
  deploy?: string
  notes?: string
}

// missing: no file; failed: the file is refused or unreadable, `note` says why.
export type Envs = {
  phase: 'loading' | 'ready' | 'missing' | 'failed'
  // The session's directory from session.start; the file is read beneath it.
  cwd?: string
  project?: string
  repository?: string
  rows: EnvRow[]
  note?: string
}

// A project's tab choice as `$.store` keeps it under `tabs:<project>`: the tab ids the person switched
// on and off there.
export type TabChoice = { on: string[]; off: string[] }

// The switchable tabs that are on for the session's project (`project`: the store key's folder).
export type Tabs = { project?: string; on: string[] }

// A JSON value as a module manifest holds it.
export type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

// Where a module tab's text comes from: an MCP tool, or a program (argv, `./` against the module root).
export type TabSource = { mcp: { server: string; tool: string; args: { [key: string]: Json } } } | { command: string[] }

// The regex sources a tab's text is cut into rows with (`rows` in the manifest).
export type RowRules = { pattern: string; detail?: string; url?: string; empty?: string }

// A picker over values one source lists; its value fills `{select:<id>}` in the tab's source.
export type SelectRule = {
  id: string
  label: string
  type: 'string' | 'number'
  source: TabSource
  pattern: string
  // A dot path into the session folder's `.claude/environments.json`.
  default?: string
}

// A tab a module declared in `panel.tabs` of its rigger-module.json, checked. `pane`: `m-<module>-<id>`.
export type ModuleTab = {
  pane: string
  module: string
  id: string
  title: string
  root: string
  source: TabSource
  rows: RowRules
  select: SelectRule[]
}

// The module tabs of this session, a line for each entry that was skipped, and `recheck`: the snapshot
// was missing at session start, so the first turn reads it again.
export type ModuleTabs = { tabs: ModuleTab[]; skipped: string[]; recheck: boolean }

export type ModuleRow = { title: string; key?: string; detail?: string; url?: string }

export type ModuleOption = { value: string; label: string }

// One module tab's rows. ready with `empty`: the empty state; failed: `note` is the failure line.
export type ModulePane = {
  phase: 'loading' | 'ready' | 'failed'
  rows: ModuleRow[]
  empty?: boolean
  capped?: boolean
  note?: string
  // Lines about options a select dropped.
  notices?: string[]
  // Keyed by select id: the options read and the value chosen.
  selects: { [id: string]: { options: ModuleOption[]; value: string } }
}

declare module 'claude-code' {
  interface PluginState {
    'rigger-panel': {
      snapshot: Shaped<Snapshot>
      issues: Shaped<Issues>
      plan: Shaped<PlanStep[]>
      planAge: Shaped<PlanAge>
      envs: Shaped<Envs>
      tabs: Shaped<Tabs>
      moduleTabs: Shaped<ModuleTabs>
      modulePanes: Shaped<{ [pane: string]: ModulePane }>
    }
  }
}
