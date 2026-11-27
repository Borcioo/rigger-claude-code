import { atom, read } from 'claude-code'
import type { EngineInterface, On, PromptOrigin, ToolCallResult } from 'claude-code'

import type { Entry, Identity, OpenFailure } from '../../types/inbox'
import { ICONS, ageText, bandText, entryFromWrapUp, entryOf } from './entries'
import { EMPTY_VIEW, POLL_MS, closeEntry, openSession, refresh } from './reader'
import { STATUS_PREFIX, WRAP_TOOLS, closeAt, identityOf, nextTurn, reasonOf, writeEntry } from './writer'

// The same references as in reader.ts: the state scan reads an atom declared in the file that uses it.
const view = atom({ plugin: 'rigger-panel', key: 'inboxView' } as const, EMPTY_VIEW, { shape: 'inbox-view-1' })
const openFailures = atom(
  { plugin: 'rigger-panel', key: 'inboxOpenFailures' } as const,
  {} as Record<string, OpenFailure>,
  { shape: 'inbox-open-failures-1' },
)

// A matcher every value of a string field passes. A plugin holds one hook per event without a
// matcher, and register.tsx holds that one for the events both files hook, so each inbox hook names
// a field every event has.
const EVERY = /(?:)/u

const PANE = 'rigger-inbox'
const PANE_TITLE = 'Inbox'

const folderOf = (cwd: string) => cwd.split(/[\\/]/u).filter(Boolean).pop() ?? cwd

async function openInbox($: EngineInterface) {
  await identityOf($, true)
  await refresh($)
  await $.ui.open({ id: PANE, title: PANE_TITLE })
}

const ERROR_TEXT = {
  aborted: 'turn aborted',
  error: 'turn ended with an error',
  refusal: 'the model refused to answer',
} as const

type WrapFields = Parameters<typeof entryFromWrapUp>[1]

// The built-in WrapUp's result carries the engine's own cleaned next-step box (keys and tokens
// masked); a call that failed, or the app's wrap_up, has none, and its input is used.
function wrapFields(e: unknown, result: ToolCallResult): WrapFields {
  if (result.deny === undefined && result.isError !== true) {
    const box = (result.result as { nextStep?: unknown } | null | undefined)?.nextStep
    if (typeof box === 'object' && box !== null) return box as WrapFields
  }
  return e as WrapFields
}

// What the running main-loop turn has written: a wrap_up settles the turn, a permission entry is
// closed once a tool call runs after it (the approval was given) or the turn ends without wrap_up.
let turn = { wrapped: false, permission: false }

// Whether the last prompt submitted came from the owner: typed (`composer`; the desktop app's own
// input arrives as `sdk`), from a phone (`bridge`, `slack-ping`) or a click on the app's own buttons
// (`auto-continuation`). A message from another session, a task notification, a schedule or a
// plugin starts a turn too, but answers nothing the entry asked.
const OWNER_ORIGINS = new Set<PromptOrigin['kind']>(['composer', 'sdk', 'bridge', 'slack-ping', 'auto-continuation'])
let isOwnerPrompt = false

// Writer work never breaks the session: it runs after `next` and swallows its own failure. Without
// the app's identity nothing is written and nothing is said; a write that fails is named on the
// status line. On `tool.call` and `tool.check` it is not awaited, so the result and the permission
// dialog never wait on the app or the disk.
async function quietly($: EngineInterface, work: (id: Identity) => Promise<void>) {
  let id: Identity | null
  try {
    id = await identityOf($)
  } catch {
    return
  }
  if (!id) return
  try {
    await work(id)
  } catch (error) {
    $.ui.status(`${STATUS_PREFIX}: entry not saved (${reasonOf(error)})`)
  }
}

const write = ($: EngineInterface, make: (id: Identity, now: number) => Entry) =>
  quietly($, async id => writeEntry($, make(id, await $.clock.now())))

const close = ($: EngineInterface) => quietly($, async id => closeAt($, id.sessionId, await $.clock.now()))

export function registerInbox(on: On) {
  // Recorded before `next`, which resolves only once the prompt's turn has started.
  on('prompt.submit', { text: EVERY }, async (_$, e, next) => {
    isOwnerPrompt = OWNER_ORIGINS.has(e.origin.kind)
    return next(e)
  })

  on('turn.start', { turnId: EVERY }, async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined) return result
    nextTurn()
    turn = { wrapped: false, permission: false }
    const isOwner = isOwnerPrompt
    isOwnerPrompt = false
    // The owner wrote in the session itself: whatever its entry asked is answered there.
    if (isOwner && e.text.trim() !== '') await close($)
    return result
  })

  // One hook for every tool, so a wrap_up never also counts as the approved call that closes the
  // permission entry (a close at the same time would hide the wrap_up's own entry).
  on('tool.call', { tool: EVERY }, async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined) return result
    if (WRAP_TOOLS.includes(e.tool)) {
      turn = { ...turn, wrapped: true, permission: false }
      const fields = wrapFields(e, result)
      void write($, (id, now) => entryFromWrapUp(id, fields, now))
    } else if (turn.permission) {
      turn = { ...turn, permission: false }
      void close($)
    }
    return result
  })

  on('tool.check', async ($, e, next) => {
    const verdict = await next(e)
    if (e.agentId !== undefined || verdict.decision !== 'ask') return verdict
    turn = { ...turn, permission: true }
    void write($, (id, now) => entryOf(id, 'permission', `waiting for approval: ${e.tool}`, now))
    return verdict
  })

  on('turn.complete', { turnId: EVERY }, async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined || turn.wrapped) return result
    if (e.reason !== 'answer') {
      const text = ERROR_TEXT[e.reason]
      await write($, (id, now) => entryOf(id, 'error', text, now))
    } else if (turn.permission) {
      await close($)
    }
    return result
  })

  on('session.start', { cwd: EVERY }, async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'inbox', description: 'Inbox: what every session waits on' })
    // The first read waits for the identity, so the band never counts this session's own entry; the
    // start itself waits for neither (the lookup gives up after 5 s).
    void identityOf($)
      .then(() => refresh($))
      .catch(() => undefined)
    $.clock.every(POLL_MS, () => void refresh($).catch(() => undefined))
    return result
  })

  on('command.run', { command: 'inbox' }, async $ => {
    await openInbox($)
    return { text: 'Inbox opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const { items, checkedAt } = await read($, view)
    const failures = await read($, openFailures)
    if (items.length === 0) return <Text dimColor>Nothing waiting.</Text>
    return (
      <Box flexDirection="column" gap={1}>
        {items.map(item => {
          const id = item.sessionId
          const failure = failures[id]?.at === item.at ? failures[id] : undefined
          return (
            <Box key={`item-${id}`} flexDirection="column" gap={1} borderStyle="round" paddingX={1} paddingY={1}>
              <Text bold>
                {ICONS[item.kind]} {item.title} · {folderOf(item.cwd)} · {ageText(checkedAt - item.at)}
              </Text>
              {item.text !== '' && <Text>{item.text}</Text>}
              {item.ask !== undefined && <Text color="yellow">{item.ask}</Text>}
              {failure !== undefined && <Text dimColor>{failure.text}</Text>}
              <Box flexDirection="row" flexWrap="wrap" gap={1}>
                <Button key={`open-${id}`} label="Open" variant="primary" onPress={() => openSession($, item)} />
                <Button key={`close-${id}`} label="Close" onPress={() => closeEntry($, item)} />
              </Box>
            </Box>
          )
        })}
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const { items, checkedAt } = await read($, view)
    const text = bandText(items, checkedAt)
    if (text === undefined) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text>{text} </Text>
        <Button key="open-inbox" label={PANE_TITLE} onPress={() => openInbox($)} />
      </Box>
    )
  })
}
