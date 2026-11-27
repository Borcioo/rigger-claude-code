import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Entry, Identity } from '../../types/inbox'
import { configDir } from '../config-dir'

export const STATUS_PREFIX = 'rigger-panel inbox'
// The built-in tool and the desktop app's own: a session has one or the other.
export const WRAP_TOOLS: readonly string[] = ['WrapUp', 'mcp__ccd_turn__wrap_up']
const APP = 'ccd_session_mgmt'

// Read once per session; null until the app has answered.
const identity = atom({ plugin: 'rigger-panel', key: 'inboxIdentity' } as const, null as Identity | null, {
  shape: 'inbox-identity-1',
})

export async function inboxDir($: EngineInterface) {
  const config = await configDir($)
  return config ? `${config}/rigger-panel/inbox` : undefined
}

const textOf = (result: { content: { type: string; text?: string }[] }) =>
  result.content
    .filter(block => block.type === 'text')
    .map(block => block.text ?? '')
    .join('\n')

export const reasonOf = (error: unknown) => (error instanceof Error ? error.message : String(error)).slice(0, 160)

const CALL_TIMEOUT_MS = 5000
const NO_ANSWER = 'no answer after 5 s'

// A call to the app's session tools with its own bound: the hook budget is paused while an MCP call
// is in flight, so an app that never answers would otherwise hold the caller for good.
async function callApp($: EngineInterface, tool: string, args: Record<string, unknown>) {
  const stop = new AbortController()
  const late = $.clock.sleep(CALL_TIMEOUT_MS, { signal: stop.signal }).then(() => {
    throw new Error(NO_ANSWER)
  })
  // Aborted once the call settles first; that rejection is expected and unread.
  late.catch(() => undefined)
  try {
    return await Promise.race([$.mcp.call(APP, tool, args), late])
  } finally {
    stop.abort()
  }
}

// Without the desktop app (a plain terminal) get_session fails every time. A failed lookup is asked
// again at most once per main-loop turn, so a session without the app does not ask on every event.
let turnCount = 0
let failedTurn: number | undefined
let pending: Promise<Identity | null> | undefined
// Also kept here for the reader: a lookup is shared between hooks, and a hook already running when
// another hook's lookup wrote the state still read null from it (/inbox right after session start,
// `claude plugin test`, CLI 2.1.332), so its first read would count the own entry.
let known: Identity | undefined

export function nextTurn() {
  turnCount += 1
}

// The own session id as far as it is known, for the reader's filter.
export function knownSessionId() {
  return known?.sessionId
}

async function lookUp($: EngineInterface): Promise<Identity | null> {
  try {
    const result = await callApp($, 'get_session', { session_id: 'self' })
    const text = textOf(result)
    if (result.isError) throw new Error(text || 'get_session failed')
    const value = JSON.parse(text) as Record<string, unknown>
    const pick = (key: string) => (typeof value[key] === 'string' ? (value[key] as string) : '')
    if (!pick('sessionId').startsWith('local_')) throw new Error('get_session gave no local_ id')
    const found: Identity = { sessionId: pick('sessionId'), link: pick('link'), title: pick('title'), cwd: pick('cwd') }
    known = found
    await update($, identity, () => found)
    return found
  } catch {
    failedTurn = turnCount
    return null
  }
}

// The app's id, title and link for this session. The CLI's own session id is not the app's
// `local_…` id, so the app is asked. A failure is silent: no app means no inbox, not an error.
// `isRetry`: a person asked (/inbox), so a failure earlier in this turn does not hold.
export async function identityOf($: EngineInterface, isRetry = false): Promise<Identity | null> {
  const held = await read($, identity)
  if (held) {
    known = held
    return held
  }
  if (!(await inboxDir($))) return null
  if (!isRetry && failedTurn === turnCount) return null
  pending ??= lookUp($).finally(() => {
    pending = undefined
  })
  return pending
}

export async function writeEntry($: EngineInterface, entry: Entry) {
  const dir = await inboxDir($)
  if (dir) await $.fs.write(`${dir}/${entry.sessionId}.json`, JSON.stringify(entry))
}

// `$.fs` cannot delete, so an entry is closed by a marker holding the time it was closed at:
// every entry written before it is hidden, the session's next one shows again.
export async function closeAt($: EngineInterface, sessionId: string, at: number) {
  const dir = await inboxDir($)
  if (dir) await $.fs.write(`${dir}/${sessionId}.closed`, String(at))
}
