import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'

import type { Entry, Identity, InboxView, OpenFailure } from '../../types/inbox'
import { parseEntry, visibleEntries } from './entries'
import { closeAt, inboxDir, knownSessionId, reasonOf } from './writer'

export const POLL_MS = 5000

export const EMPTY_VIEW: InboxView = { phase: 'idle', items: [], checkedAt: 0 }

// The same references as in writer.ts and view.tsx: the state scan reads an atom declared in the
// file that uses it.
const identity = atom({ plugin: 'rigger-panel', key: 'inboxIdentity' } as const, null as Identity | null, {
  shape: 'inbox-identity-1',
})
const view = atom({ plugin: 'rigger-panel', key: 'inboxView' } as const, EMPTY_VIEW, { shape: 'inbox-view-1' })
// The last failed Open per session, with the entry time it failed for: view.tsx shows it only on
// that entry, so it never carries over to the session's next one.
const openFailures = atom(
  { plugin: 'rigger-panel', key: 'inboxOpenFailures' } as const,
  {} as Record<string, OpenFailure>,
  { shape: 'inbox-open-failures-1' },
)

// Reads every session's entry and close marker. A missing folder is an empty inbox; a file that
// cannot be read or parsed is skipped, so one bad file never hides the rest.
export async function loadView($: EngineInterface): Promise<InboxView> {
  const now = await $.clock.now()
  const dir = await inboxDir($)
  if (!dir) return { phase: 'ready', items: [], checkedAt: now, note: 'no config folder' }
  let names: string[]
  try {
    names = (await $.fs.list(dir)).filter(entry => entry.kind === 'file').map(entry => entry.name)
  } catch {
    return { phase: 'ready', items: [], checkedAt: now }
  }
  const entries: Entry[] = []
  const closed: Record<string, number> = {}
  for (const name of names) {
    const isEntry = name.endsWith('.json')
    if (!isEntry && !name.endsWith('.closed')) continue
    let text: string
    try {
      text = await $.fs.read(`${dir}/${name}`)
    } catch {
      continue
    }
    if (isEntry) {
      const entry = parseEntry(text)
      if (entry && `${entry.sessionId}.json` === name) entries.push(entry)
    } else {
      const at = Number(text.trim())
      if (text.trim() !== '' && Number.isFinite(at)) closed[name.slice(0, -'.closed'.length)] = at
    }
  }
  const own = knownSessionId() ?? (await read($, identity))?.sessionId
  return { phase: 'ready', items: visibleEntries(entries, closed, own, now), checkedAt: now }
}

export async function refresh($: EngineInterface) {
  const next = await loadView($)
  await update($, view, () => next)
}

function setOpenFailure($: EngineInterface, entry: Entry, text: string) {
  return update($, openFailures, last => ({ ...last, [entry.sessionId]: { at: entry.at, text } }))
}

export async function closeEntry($: EngineInterface, entry: Entry) {
  await closeAt($, entry.sessionId, entry.at)
  await refresh($)
}

// Only the link to the entry's own session is opened: the entry file is written by another process,
// and on Windows `cmd` re-parses its command line, so anything else is refused before it runs
// (parseEntry has already held the session id to SESSION_ID).
const SESSION_LINK_PREFIX = 'claude://claude.ai/epitaxy/'
const isOwnLink = (entry: Entry) => entry.link === SESSION_LINK_PREFIX + entry.sessionId

export function openCommand(sysname: string, link: string): string[] {
  if (sysname === 'Darwin') return ['open', link]
  if (/^Windows/u.test(sysname)) return ['cmd', '/c', 'start', '', link]
  return ['xdg-open', link]
}

// The engine says which system it runs on; where it cannot (the call rejects), the system is asked.
async function sysnameOf($: EngineInterface) {
  try {
    return (await $.session.machine()).sysname
  } catch {
    if ((await $.env.get('OS')) === 'Windows_NT') return 'Windows_NT'
    return (await $.process.run(['uname', '-s'], { timeoutMs: 5000 })).stdout.trim()
  }
}

// The desktop app draws a non-https Link as plain text, so the session is opened by the
// operating system's URL handler, which hands `claude://` links to the app.
export async function openSession($: EngineInterface, entry: Entry) {
  if (!isOwnLink(entry)) {
    await setOpenFailure($, entry, 'not opened: the link is not a session link')
    return
  }
  try {
    const ran = await $.process.run(openCommand(await sysnameOf($), entry.link), { timeoutMs: 10_000 })
    if (ran.exitCode !== 0) throw new Error(ran.stderr.trim() || `exit code ${ran.exitCode}`)
  } catch (error) {
    await setOpenFailure($, entry, `not opened: ${reasonOf(error)}`)
  }
}
