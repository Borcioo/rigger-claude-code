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

declare module 'claude-code' {
  interface PluginState {
    'rigger-panel': {
      snapshot: Shaped<Snapshot>
      issues: Shaped<Issues>
      plan: Shaped<PlanStep[]>
      planAge: Shaped<PlanAge>
      envs: Shaped<Envs>
    }
  }
}
