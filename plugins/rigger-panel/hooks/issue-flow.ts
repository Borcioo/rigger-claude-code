import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Issues, StartState } from '../types'
import { REPO, askText, clip, hereText, isIssueNumber, listAssignedIssues, needsApproval, spawnArgs, startArgs } from './issues'

// The issue list is read with gh (`issues.ts`) and GitHub is never written. A session starts only
// from a press of a Start button, and nothing here runs on a timer.
const SESSIONS = 'ccd_session'
// Under the hook budget, so a gh that does not answer ends as a failed read.
const LOAD_TIMEOUT_MS = 15_000
const START_TIMEOUT_MS = 30_000
const TOOL_LIST_TIMEOUT_MS = 3_000
// The tool a started session is proposed with; a session that lacks it cannot start one. A WSL
// session has `spawn_task` instead, which proposes the session as a chip.
const START_TOOL = 'mcp__ccd_session__start_session'
const SPAWN_TOOL = 'mcp__ccd_session__spawn_task'
// A spawn_task result that names a task: the exact wording is not known, so only the id is looked for.
const TASK_ID = /\btask[_ ]?id\b\W{0,4}[\w-]+/iu

export const EMPTY_ISSUES: Issues = { phase: 'loading', rows: [], starts: {} }

const issues = atom({ plugin: 'rigger-panel', key: 'issues' } as const, EMPTY_ISSUES, {
  shape: 'issues-1',
})

// Claimed before the first await, so two presses in one tick start one session and two reloads
// make one read.
const claimed = new Set<string>()

const textOf = (result: { content: { type: string; text?: string }[] }) =>
  result.content
    .filter(block => block.type === 'text')
    .map(block => block.text ?? '')
    .join('\n')

const reasonOf = (error: unknown) => clip(error instanceof Error ? error.message : String(error))

type Outcome<T> = { isDone: true; value: T } | { isDone: false }

// A call with its own bound: the work, or the wait, whichever settles first.
async function bounded<T>($: EngineInterface, ms: number, work: Promise<T>): Promise<Outcome<T>> {
  const stop = new AbortController()
  const late = $.clock.sleep(ms, { signal: stop.signal }).then(
    () => ({ isDone: false }) as const,
    () => ({ isDone: false }) as const,
  )
  try {
    return await Promise.race([work.then(value => ({ isDone: true, value }) as const), late])
  } finally {
    stop.abort()
  }
}

function setStart($: EngineInterface, key: string, state: StartState) {
  return update($, issues, last => ({ ...last, starts: { ...last.starts, [key]: state } }))
}

// Whether this session has the tool a started session is proposed with, and the one that proposes it
// as a chip, stored in state: absent from `$.tool.list()` means false, a list that fails or does not
// answer in time means unknown, so Start stays. Not run at session.start: MCP servers connect after
// it, so the list is short then and a session that has the tool would read as lacking it. Run when
// the user opens the panes or presses Refresh, and at the start of each turn.
export async function detectStartTool($: EngineInterface) {
  let found: { start: boolean; spawn: boolean } | undefined
  try {
    const outcome = await bounded($, TOOL_LIST_TIMEOUT_MS, $.tool.list())
    found = outcome.isDone
      ? {
          start: outcome.value.some(entry => entry.name === START_TOOL),
          spawn: outcome.value.some(entry => entry.name === SPAWN_TOOL),
        }
      : undefined
  } catch {
    found = undefined
  }
  await update($, issues, last => {
    if (last.startTool === found?.start && last.spawnTool === found?.spawn) return last
    const { startTool: _start, spawnTool: _spawn, ...rest } = last
    return found === undefined ? rest : { ...rest, startTool: found.start, spawnTool: found.spawn }
  })
}

// The button's load: a press means the user has looked, so finished starts offer Start again.
export async function refreshIssues($: EngineInterface) {
  void detectStartTool($)
  await update($, issues, last => ({
    ...last,
    starts: Object.fromEntries(Object.entries(last.starts).filter(([, start]) => start.phase === 'running')),
  }))
  await loadIssues($)
}

// Loads the issues assigned to the user. Rows are cleared first, so a failed or slow read never
// leaves the previous list standing as current.
export async function loadIssues($: EngineInterface) {
  if (claimed.has('load')) return
  claimed.add('load')
  try {
    await update($, issues, (last): Issues => ({ phase: 'loading', rows: [], starts: last.starts, startTool: last.startTool, spawnTool: last.spawnTool }))
    let next: Omit<Issues, 'starts'>
    try {
      const outcome = await bounded($, LOAD_TIMEOUT_MS, listAssignedIssues($, null))
      if (!outcome.isDone) {
        next = { phase: 'failed', rows: [], note: `GitHub did not answer within ${LOAD_TIMEOUT_MS / 1000} s` }
      } else {
        const { rows, repo, empty, capped } = outcome.value
        next = { phase: 'ready', rows, ...(repo ? { repo } : {}), ...(empty ? { empty } : {}), ...(capped ? { capped } : {}) }
      }
    } catch (error) {
      next = { phase: 'failed', rows: [], note: reasonOf(error) || 'the read failed with no reason' }
    }
    await update($, issues, last => ({ ...next, starts: last.starts, startTool: last.startTool, spawnTool: last.spawnTool }))
  } finally {
    claimed.delete('load')
  }
}

const isValid = (repo: unknown, number: unknown): repo is string => typeof repo === 'string' && REPO.test(repo) && isIssueNumber(number)

// A press of Start. The repo and number are checked before they enter any text; only an app answer
// that says a session started counts as started. A session with `spawn_task` and no `start_session`
// proposes the session as a chip: only a result that names a task id counts, anything else is
// unconfirmed.
export async function startIssue($: EngineInterface, number: number, title: string) {
  const key = String(number)
  if (claimed.has(key)) return
  claimed.add(key)
  try {
    const state = await read($, issues)
    const held = state.starts[key]
    if (held && held.phase !== 'failed') return
    const repo = state.repo
    if (!isValid(repo, number)) {
      await setStart($, key, { phase: 'failed', text: 'not a valid issue, nothing was sent' })
      return
    }
    const spawn = state.startTool === false && state.spawnTool === true
    await setStart($, key, { phase: 'running', text: 'preparing the session card…' })
    try {
      const call = spawn
        ? $.mcp.call(SESSIONS, 'spawn_task', spawnArgs(repo, number, title))
        : $.mcp.call(SESSIONS, 'start_session', startArgs(repo, number, title))
      const outcome = await bounded($, START_TIMEOUT_MS, call)
      if (!outcome.isDone) {
        await setStart($, key, {
          phase: 'unconfirmed',
          text: `no answer after ${START_TIMEOUT_MS / 1000} s; whether the session started is not known, check the session list`,
        })
        return
      }
      const text = textOf(outcome.value)
      if (spawn) {
        await setStart(
          $,
          key,
          !outcome.value.isError && TASK_ID.test(text)
            ? { phase: 'started', text: 'ready: click the card in the conversation' }
            : { phase: 'unconfirmed', text: `answer with no start confirmation: ${clip(text) || 'no text'}` },
        )
      } else if (outcome.value.isError) {
        if (needsApproval(text)) await handOver($, repo, number, title)
        else await setStart($, key, { phase: 'failed', text: `refused: ${clip(text) || 'an error with no text'}` })
      } else if (/^Proposed\b/u.test(text)) {
        await setStart($, key, { phase: 'started', text: 'ready: click the New task card in the conversation' })
      } else {
        await setStart($, key, { phase: 'unconfirmed', text: `answer with no start confirmation: ${clip(text)}` })
      }
    } catch (error) {
      if (!spawn && needsApproval(reasonOf(error))) await handOver($, repo, number, title)
      else await setStart($, key, { phase: 'failed', text: `refused: ${reasonOf(error)}` })
    }
  } finally {
    claimed.delete(key)
  }
}

// The app refused the mod's own call because it needs a person's approval: this session gets the same
// ask the fallback button sends, and can obtain that approval. The row says it was handed over, never
// that a session started; a prompt that cannot be submitted leaves Start and the fallback button.
async function handOver($: EngineInterface, repo: string, number: number, title: string) {
  const key = String(number)
  try {
    await $.prompt.submit({ text: askText(repo, number, title) })
    await setStart($, key, { phase: 'asked', text: 'handed to this session: approve it if it asks' })
  } catch (error) {
    await setStart($, key, { phase: 'failed', text: `could not ask this session: ${reasonOf(error)}` })
  }
}

// The fallback after a refusal: this session is asked to start it. A start is not claimed here.
// With no start tool in this session (`startTool` false) it is the first and only button: the
// session is asked to work on the issue itself.
export async function askSession($: EngineInterface, number: number, title: string) {
  const key = String(number)
  const ask = `ask:${key}`
  if (claimed.has(ask) || claimed.has(key)) return
  claimed.add(ask)
  try {
    const state = await read($, issues)
    const held = state.starts[key]
    const here = state.startTool === false
    const repo = state.repo
    if (!isValid(repo, number) || (held === undefined ? !here : held.phase !== 'failed')) return
    if (here) {
      await $.prompt.submit({ text: hereText(repo, number, title) })
      await setStart($, key, { phase: 'asked', text: 'asked this session to work on it; not confirmed that it did' })
      return
    }
    await $.prompt.submit({ text: askText(repo, number, title) })
    await setStart($, key, { phase: 'asked', text: 'asked this session to start it; not confirmed that it did' })
  } catch (error) {
    await setStart($, key, { phase: 'failed', text: `could not ask this session: ${reasonOf(error)}` })
  } finally {
    claimed.delete(ask)
  }
}
