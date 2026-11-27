import { expect, test } from 'claude-code/testing'

import type { Entry } from '../../types/inbox'
import { MAX_AGE_MS, ageText, bandText, entryFromWrapUp, entryOf, parseEntry, visibleEntries } from './entries'

const ID = { sessionId: 'local_abc', link: 'claude://claude.ai/epitaxy/local_abc', title: 'Alpha', cwd: '/w/alpha' }
const NOW = 10 * 86_400_000

const entry = (sessionId: string, at: number, title = sessionId): Entry => ({
  ...entryOf({ ...ID, sessionId, title }, 'review', 'done', at),
})

test('a wrap_up with a question is a question with its button labels', () => {
  const e = entryFromWrapUp(ID, { recap: 'r', ask: 'Which?', actions: [{ label: 'Yes' }, { label: '' }, {}, { label: 'No' }] }, 5)
  expect(e.kind).toBe('question')
  expect(e.ask).toBe('Which?')
  expect(e.actions).toEqual(['Yes', 'No'])
  expect(e.text).toBe('r')
  expect(e.at).toBe(5)
  expect(e.sessionId).toBe('local_abc')
})

test('a blocked wrap_up without a question is still a question', () => {
  expect(entryFromWrapUp(ID, { recap: 'r', blocked: true }, 1).kind).toBe('question')
})

test('a wrap_up with neither is a result to check', () => {
  const e = entryFromWrapUp(ID, { recap: 'r' }, 1)
  expect(e.kind).toBe('review')
  expect(e.actions).toEqual([])
  expect(e.ask).toBeUndefined()
})

test("a wrap_up's app buttons ({ action }) are dropped, its reply labels kept", () => {
  const e = entryFromWrapUp(ID, { recap: 'r', ask: 'Go?', actions: [{ action: 'verify' }, { label: 'Go' }, { action: 'fix_ci' }] }, 1)
  expect(e.actions).toEqual(['Go'])
})

test('a wrap_up entry is cleaned and capped before it is written', () => {
  const esc = String.fromCharCode(27)
  const rlo = String.fromCharCode(0x202e)
  const e = entryFromWrapUp(
    { ...ID, title: `Al${esc}[2Jpha`, cwd: `/w/a${rlo}b` },
    { recap: `done${esc}]0;x\nnext`, ask: 'q'.repeat(700), actions: [{ label: `Y${rlo}es` }, { label: 'n'.repeat(80) }] },
    1,
  )
  expect(e.title).toBe('Al [2Jpha')
  expect(e.cwd).toBe('/w/a b')
  expect(e.text).toBe('done ]0;x next')
  expect(e.ask).toHaveLength(600)
  expect(e.actions).toEqual(['Y es', 'n'.repeat(60)])
})

test('a wrap_up entry keeps at most six reply labels', () => {
  const actions = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map(label => ({ label }))
  expect(entryFromWrapUp(ID, { recap: 'r', ask: 'q', actions }, 1).actions).toEqual(['a', 'b', 'c', 'd', 'e', 'f'])
})

test('an error or permission entry is cleaned before it is written', () => {
  const bell = String.fromCharCode(7)
  expect(entryOf(ID, 'permission', `waiting for approval: Ba${bell}sh`, 1).text).toBe('waiting for approval: Ba sh')
})

test('parseEntry reads what entryOf wrote and refuses every other shape', () => {
  const good = entryOf(ID, 'error', 'turn aborted', 3)
  expect(parseEntry(JSON.stringify(good))).toEqual(good)
  for (const bad of ['{', '[]', '7', 'null', JSON.stringify({ ...good, v: 2 }), JSON.stringify({ ...good, sessionId: undefined }),
    JSON.stringify({ ...good, kind: 'other' }), JSON.stringify({ ...good, at: '3' }), JSON.stringify({ ...good, actions: 'Yes' }),
    JSON.stringify({ ...good, link: 7 }), JSON.stringify({ ...good, title: null }), JSON.stringify({ ...good, cwd: [] }),
    JSON.stringify({ ...good, text: {} }), JSON.stringify({ ...good, ask: 1 }), JSON.stringify({ ...good, actions: [1] })]) {
    expect([bad, parseEntry(bad)]).toEqual([bad, null])
  }
})

test('visibleEntries drops old, closed and own entries and sorts oldest first', () => {
  const list = [
    entry('local_new', NOW - 1000),
    entry('local_old', NOW - MAX_AGE_MS - 1),
    entry('local_closed', NOW - 5000),
    entry('local_reopened', NOW - 4000),
    entry('local_own', NOW - 3000),
    entry('local_first', NOW - 9000),
  ]
  const closed = { local_closed: NOW - 5000, local_reopened: NOW - 4001 }
  const ids = visibleEntries(list, closed, 'local_own', NOW).map(e => e.sessionId)
  expect(ids).toEqual(['local_first', 'local_reopened', 'local_new'])
})

test('visibleEntries drops an entry stamped more than 5 minutes ahead, which would never age out', () => {
  const list = [entry('local_soon', NOW + 5 * 60_000), entry('local_future', NOW + 5 * 60_000 + 1)]
  expect(visibleEntries(list, {}, undefined, NOW).map(e => e.sessionId)).toEqual(['local_soon'])
})

test('ageText rounds to minutes, hours and days', () => {
  expect(ageText(30_000)).toBe('now')
  expect(ageText(8 * 60_000)).toBe('8 min')
  expect(ageText(3 * 3_600_000)).toBe('3 h')
  expect(ageText(2 * 86_400_000)).toBe('2 d')
})

test('bandText names the count and the oldest entry, and nothing when empty', () => {
  expect(bandText([], NOW)).toBeUndefined()
  const items = [entry('a', NOW - 8 * 60_000, 'Alpha'), entry('b', NOW - 60_000, 'Beta')]
  expect(bandText(items, NOW)).toBe('📥 2 waiting · oldest: Alpha, 8 min')
})

test('parseEntry refuses a session id that is not a local_ id', () => {
  // Junk before (`^`) or after (`$`) a valid id would reach `cmd /c start` through the session link.
  for (const sessionId of ['local_', 'abc', 'local_a b', 'local_x"; rm', `local_${'a'.repeat(65)}`, 'x&calc&local_a', 'local_a&calc']) {
    expect(parseEntry(JSON.stringify(entryOf({ ...ID, sessionId }, 'review', 't', 1)))).toBeNull()
  }
  expect(parseEntry(JSON.stringify(entryOf({ ...ID, sessionId: `local_${'a'.repeat(64)}` }, 'review', 't', 1)))?.sessionId).toHaveLength(70)
})

test('parseEntry turns control characters into spaces and caps the texts', () => {
  const esc = String.fromCharCode(27)
  const c1 = String.fromCharCode(0x9b)
  const raw = {
    ...entryOf(ID, 'question', 'x', 1),
    title: `Al${esc}[2Jpha`,
    text: `a${c1}b\nc`,
    ask: 'x'.repeat(700),
    actions: [`Ye${esc}]0;s`, '   ', 'y'.repeat(80)],
  }
  const e = parseEntry(JSON.stringify(raw))
  expect(e?.title).toBe('Al [2Jpha')
  expect(e?.text).toBe('a b c')
  expect(e?.ask).toHaveLength(600)
  expect(e?.actions).toEqual(['Ye ]0;s', 'y'.repeat(60)])
})

test('parseEntry turns format characters and line separators into spaces', () => {
  for (const code of [0x202e, 0x2066, 0x200f, 0x2028, 0x2029]) {
    const mark = String.fromCharCode(code)
    const raw = { ...entryOf(ID, 'review', 'x', 1), title: `Al${mark}pha`, text: `a${mark}b` }
    const e = parseEntry(JSON.stringify(raw))
    expect([code, e?.title, e?.text]).toEqual([code, 'Al pha', 'a b'])
  }
})
