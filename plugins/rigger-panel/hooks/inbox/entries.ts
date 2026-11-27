import type { Entry, EntryKind, Identity } from '../../types/inbox'

export const MAX_AGE_MS = 7 * 24 * 3600 * 1000
// Clocks of two sessions on one machine agree; an entry stamped further ahead would never age out or
// close (a close marker holds the time it was written).
export const MAX_AHEAD_MS = 5 * 60_000
export const ICONS: Record<EntryKind, string> = { question: '❓', review: '✅', error: '⚠️', permission: '⏳' }
const KINDS = new Set<string>(Object.keys(ICONS))

type WrapUp = { recap?: unknown; ask?: unknown; blocked?: unknown; actions?: unknown }

export const SESSION_ID = /^local_[A-Za-z0-9-]{1,64}$/u
const TEXT_MAX = 600
const LABEL_MAX = 60
const ACTIONS_MAX = 6

// Text from a session's file is drawn in a terminal: control characters (C0, DEL, C1) would reach it
// as escape sequences, and format characters (bidi marks, overrides and isolates) and line
// separators would reorder or split what is drawn, so they become spaces.
const CONTROL = new RegExp('[\\u0000-\\u001f\\u007f-\\u009f\\p{Cf}\\u2028\\u2029]+', 'gu')
export const clean = (value: string, max: number) => value.replace(CONTROL, ' ').trim().slice(0, max)

const cleanLabels = (labels: string[]) =>
  labels
    .map(label => clean(label, LABEL_MAX))
    .filter(label => label !== '')
    .slice(0, ACTIONS_MAX)

// Every text an entry holds, cleaned and capped: on the way to the file, so nothing raw sits on disk,
// and again on the way back, as the file may come from another version or another process.
function cleaned(e: Entry): Entry {
  return {
    v: 1,
    sessionId: e.sessionId,
    link: e.link,
    title: clean(e.title, LABEL_MAX),
    cwd: clean(e.cwd, TEXT_MAX),
    kind: e.kind,
    text: clean(e.text, TEXT_MAX),
    ...(e.ask !== undefined ? { ask: clean(e.ask, TEXT_MAX) } : {}),
    actions: cleanLabels(e.actions),
    at: e.at,
  }
}

export const entryOf = (id: Identity, kind: EntryKind, text: string, at: number): Entry =>
  cleaned({
    v: 1,
    sessionId: id.sessionId,
    link: id.link,
    title: id.title,
    cwd: id.cwd,
    kind,
    text,
    actions: [],
    at,
  })

const str = (value: unknown) => (typeof value === 'string' ? value.trim() : '')

// The fields come from the model's wrap_up call or the engine's next-step box, so every one is
// checked for its type; an app button (`{ action }`) has no label and is dropped.
export function entryFromWrapUp(id: Identity, e: WrapUp, at: number): Entry {
  const ask = str(e.ask)
  const actions = Array.isArray(e.actions)
    ? e.actions.map(action => str((action as { label?: unknown } | null)?.label)).filter(label => label !== '')
    : []
  const isQuestion = ask !== '' || e.blocked === true
  const base = entryOf(id, isQuestion ? 'question' : 'review', str(e.recap), at)
  return cleaned({
    ...base,
    ...(ask !== '' ? { ask } : {}),
    actions: isQuestion ? actions : [],
  })
}

// Another session's file may be half-written, from another version or hostile: anything off is no
// entry, and every text that is kept is cleaned and capped.
export function parseEntry(text: string): Entry | null {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const e = value as Record<string, unknown>
  const isText = (key: string) => typeof e[key] === 'string'
  if (e.v !== 1 || !isText('sessionId') || !isText('link') || !isText('title')) return null
  if (!isText('cwd') || !isText('text') || !KINDS.has(String(e.kind))) return null
  if (typeof e.at !== 'number' || !Number.isFinite(e.at)) return null
  if (!Array.isArray(e.actions) || !e.actions.every(action => typeof action === 'string')) return null
  if (e.ask !== undefined && !isText('ask')) return null
  if (!SESSION_ID.test(e.sessionId as string)) return null
  return cleaned(e as unknown as Entry)
}

export function visibleEntries(
  entries: Entry[],
  closed: Record<string, number>,
  ownId: string | undefined,
  now: number,
): Entry[] {
  return entries
    .filter(e => e.sessionId !== ownId && e.at >= now - MAX_AGE_MS && e.at <= now + MAX_AHEAD_MS)
    .filter(e => !((closed[e.sessionId] ?? -Infinity) >= e.at))
    .sort((a, b) => a.at - b.at)
}

export function ageText(ms: number) {
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h`
  return `${Math.floor(hours / 24)} d`
}

export function bandText(items: Entry[], now: number): string | undefined {
  const oldest = items[0]
  if (!oldest) return undefined
  return `📥 ${items.length} waiting · oldest: ${oldest.title}, ${ageText(now - oldest.at)}`
}
