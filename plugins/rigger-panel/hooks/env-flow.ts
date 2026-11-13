import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Envs } from '../types'
import { ENV_FILE, parseEnvironments } from './envs'

export const EMPTY_ENVS: Envs = { phase: 'loading', rows: [] }

// The same reference as in register.tsx: the state scan reads an atom declared in the file that uses it.
const envs = atom({ plugin: 'rigger-panel', key: 'envs' } as const, EMPTY_ENVS, {
  shape: 'envs-1',
})

// Two presses in one tick make one read.
let loading = false

// Kept in state at session start: a pane render has no event with the directory.
export async function setEnvsCwd($: EngineInterface, cwd: string | undefined) {
  await update($, envs, last => ({ ...last, cwd }))
}

// Reads the file beneath the session's directory. Rows are cleared first, so a refused or missing
// file never leaves the previous list standing as current.
export async function loadEnvs($: EngineInterface) {
  if (loading) return
  loading = true
  try {
    // A session can change folder after it starts, so the stored one is only the fallback.
    const cwd = (await $.session.cwd().catch(() => undefined)) ?? (await read($, envs)).cwd
    // `cwd` in state is the folder the file is read from, so the pane can tell when the session has moved on.
    await update($, envs, (last): Envs => ({ phase: 'loading', cwd: cwd ?? last.cwd, rows: [] }))
    let next: Omit<Envs, 'cwd'>
    if (!cwd) {
      next = { phase: 'failed', rows: [], note: "the session's folder is not known" }
    } else {
      let text: string | undefined
      try {
        text = await $.fs.read(`${cwd.replace(/[\\/]+$/u, '')}/${ENV_FILE}`)
      } catch {
        text = undefined
      }
      if (text === undefined) {
        next = { phase: 'missing', rows: [] }
      } else {
        const parsed = parseEnvironments(text)
        next = parsed.ok
          ? { phase: 'ready', rows: parsed.rows, project: parsed.project, repository: parsed.repository }
          : { phase: 'failed', rows: [], note: parsed.reason }
      }
    }
    await update($, envs, last => ({ ...next, cwd: last.cwd }))
  } finally {
    loading = false
  }
}

// Called when the pane is drawn: reads again when the session is in another folder than the one last
// read, as when it was moved into a worktree after the first read. Not a timer, and a folder whose
// file is missing is read once, not on every draw.
export async function loadEnvsIfMoved($: EngineInterface, shown: Envs) {
  const here = await $.session.cwd().catch(() => undefined)
  if (here !== undefined && here !== shown.cwd) await loadEnvs($)
}
