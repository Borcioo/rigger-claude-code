// question: the session asked something or is blocked on the owner; review: it finished and the
// result waits to be checked; error: the turn was aborted, refused or died; permission: a tool call
// waits for the owner's approval.
export type EntryKind = 'question' | 'review' | 'error' | 'permission'

// One session's current entry, as its file `<config>/rigger-panel/inbox/<sessionId>.json` holds it.
export type Entry = {
  v: 1
  sessionId: string
  link: string
  title: string
  cwd: string
  kind: EntryKind
  text: string
  ask?: string
  actions: string[]
  at: number
}

// The app's own view of this session (`get_session self`): `sessionId` is the `local_…` id.
export type Identity = { sessionId: string; link: string; title: string; cwd: string }

// `checkedAt`: when the folder was last read, the clock the ages are drawn against.
export type InboxView = { phase: 'idle' | 'ready'; items: Entry[]; checkedAt: number; note?: string }

// A failed Open: `at` is the time of the entry it failed for.
export type OpenFailure = { at: number; text: string }
