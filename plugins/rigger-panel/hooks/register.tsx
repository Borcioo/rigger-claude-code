import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Envs, PlanEvidence, PlanStep, Snapshot } from '../types'
import { configDir } from './config-dir'
import { EMPTY_ENVS, loadEnvs, loadEnvsIfMoved, setEnvsCwd } from './env-flow'
import {
  BAR_HEIGHT,
  BAR_WIDTH,
  BLOCK_GAP,
  ENV_DETAILS,
  ENV_LABELS,
  GAP,
  barCells,
  barColor,
  barSvg,
  isProd,
  labelWidth,
  roleBadge,
  row,
  svgOf,
  textBar,
  textRow,
} from './look'
import {
  CLOSE_TOOL,
  EMPTY_AGE,
  PLAN_TOOL,
  ageLine,
  applyPlan,
  closePlan,
  isCommit,
  isInterim,
  nudgeText,
  planIcon,
  planMark,
  staleLine,
  touchAge,
  withEvidence,
} from './plan'
import type { PlanCall } from './plan'
import { EMPTY_ISSUES, askSession, detectStartTool, loadIssues, refreshIssues, startIssue } from './issue-flow'
import { cutLine, emptyText, shortTitle } from './issues'

const PANE = 'rigger-panel'
const ISSUES_PANE = 'rigger-issues'
const PLAN_PANE = 'rigger-plan'
const ENVS_PANE = 'rigger-envs'
const PANE_TITLES = [
  [PANE, 'Rigger'],
  [ISSUES_PANE, 'Issues'],
  [PLAN_PANE, 'Plan'],
  [ENVS_PANE, 'Environments'],
] as const
const REFRESH_MS = 30_000
const EMPTY: Snapshot = { window: 0, agents: 0, updatedAt: 0 }

const snapshot = atom({ plugin: 'rigger-panel', key: 'snapshot' } as const, EMPTY, {
  shape: 'snapshot-1',
})

// The same reference as in issue-flow.ts: the state scan reads an atom declared in the file that uses it.
const issues = atom({ plugin: 'rigger-panel', key: 'issues' } as const, EMPTY_ISSUES, {
  shape: 'issues-1',
})

const plan = atom({ plugin: 'rigger-panel', key: 'plan' } as const, [] as PlanStep[], {
  shape: 'plan-1',
})

const planAge = atom({ plugin: 'rigger-panel', key: 'planAge' } as const, EMPTY_AGE, {
  shape: 'plan-age-1',
})

const touchPlan = async ($: EngineInterface) => {
  const now = await $.clock.now()
  await update($, planAge, () => touchAge(now))
}

// A sign of progress the list may not show yet; `startedAt` is when the command behind a commit began.
// Resolves to the evidence when this call recorded it.
const noteEvidence = async ($: EngineInterface, kind: PlanEvidence['kind'], startedAt?: number) => {
  const [steps, now] = await Promise.all([read($, plan), $.clock.now()])
  let noted: PlanEvidence | undefined
  await update($, planAge, age => {
    const next = withEvidence(age, steps, kind, now, startedAt)
    noted = next === age ? undefined : next.evidence
    return next
  })
  return noted
}

// Agents in a turn of their own; one waiting on background work, idle or not started is not running. An
// unreadable list counts as none, so wrap_up still checks the list off.
const runningAgents = async ($: EngineInterface) => {
  try {
    return (await $.agent.list()).filter(agent => agent.status === 'running').length
  } catch {
    return 0
  }
}

const markNudged = ($: EngineInterface) => update($, planAge, age => ({ ...age, nudged: true }))

const envs = atom({ plugin: 'rigger-panel', key: 'envs' } as const, EMPTY_ENVS, {
  shape: 'envs-1',
})

const HARNESS_LABELS = ['Context', 'Session cost', 'Weekly limit', 'Agents in session', 'Rigger', 'Lessons in inbox', 'Compaction']

const thousands = (value?: number) => (value === undefined ? '?' : `${Math.round(value / 1000)}k`)

// Any file may be missing on another machine: a missing value draws as "?".
const readFile = async ($: EngineInterface, path: string | undefined) => {
  if (path === undefined) return undefined
  try {
    return await $.fs.read(path)
  } catch {
    return undefined
  }
}

const readText = ($: EngineInterface, dir: string | undefined, name: string) =>
  readFile($, dir === undefined ? undefined : `${dir}/${name}`)

// The lessons hook honours this variable, so sessions that run in different shells or config dirs can
// share one inbox.
const inboxPath = async ($: EngineInterface, dir: string | undefined) =>
  (await $.env.get('HARNESS_LESSONS_INBOX'))?.trim() || (dir === undefined ? undefined : `${dir}/lessons-inbox.md`)

const harnessVersion = (text?: string) => {
  const entry = text?.match(/"rigger-core@rigger"[\s\S]*?"version":\s*"([0-9a-f]{7})/)
  return entry?.[1]
}

const lessonCount = (text?: string) => text?.match(/^- \d{4}-\d{2}-\d{2}/gm)?.length

// A log line reads `<time> <session> <tokens> allow|block <reason>`.
const lastGuardLine = (text?: string) => {
  const parts = text?.trim().split('\n').pop()?.trim().split(' ')
  return parts && parts.length >= 5 ? `${parts[3]} ${parts[4]} at ${thousands(Number(parts[2]))}` : undefined
}

const refresh = async ($: EngineInterface) => {
  const usage = await $.session.usage()
  const weekly = usage.rateLimits.find(limit => limit.kind === 'seven_day')
  const dir = await configDir($)
  const [installed, inbox, guard] = await Promise.all([
    readText($, dir, 'plugins/installed_plugins.json'),
    readFile($, await inboxPath($, dir)),
    readText($, dir, 'state/compact-guard.log'),
  ])
  const now = await $.clock.now()
  await update($, snapshot, last => ({
    tokens: usage.context.tokens,
    window: usage.context.window,
    percent: usage.context.percent,
    usd: usage.cost?.usd,
    weekly: weekly?.percentUsed,
    version: harnessVersion(installed),
    lessons: lessonCount(inbox),
    guard: lastGuardLine(guard),
    agents: last.agents,
    updatedAt: now,
  }))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'harness', description: 'Show the Rigger panel' })
    for (const [id, title] of PANE_TITLES) void $.ui.open({ id, title })
    void refresh($)
    // GitHub and the environments file are read when the pane opens and on Refresh, never on this timer.
    void loadIssues($)
    void setEnvsCwd($, e.cwd).then(() => loadEnvs($))
    $.clock.every(REFRESH_MS, () => void refresh($))

    return next(e)
  })

  on('command.run', { command: 'harness' }, async $ => {
    await Promise.all(PANE_TITLES.map(([id, title]) => $.ui.open({ id, title })))
    await Promise.all([refresh($), loadIssues($), loadEnvs($), detectStartTool($)])

    return { text: 'Rigger panel opened.' }
  })

  // The tool list is short at session.start, so it is read again once the session is running.
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      void detectStartTool($)
      await update($, planAge, age => ({ ...age, touched: false }))
    }

    return next(e)
  })

  // A commit the session made is progress the list may not show yet. Its own result never carries
  // the reminder: the list update sent in the same message runs after the commit. The list is the
  // main loop's, so its next shell call names what the list is still behind.
  on('tool.call', { tool: ['Bash', 'PowerShell'] }, async ($, e, next) => {
    const commit = isCommit(e.command)
    const age = e.agentId === undefined ? await read($, planAge) : undefined
    const owed = age?.nudged === false ? age.evidence : undefined
    if (!commit && owed === undefined) return next(e)
    const startedAt = commit ? await $.clock.now() : 0
    const done = await next(e)
    if (commit && done.deny === undefined && done.isError !== true) await noteEvidence($, 'commit', startedAt)
    if (owed === undefined || done.deny !== undefined) return done
    await markNudged($)

    return { ...done, context: [...(done.context ?? []), nudgeText(owed)] }
  })

  // So is a background task that finished. A list that is behind is named once at the next prompt.
  on('prompt.submit', async ($, e, next) => {
    if (e.origin.kind === 'task-notification' && !isInterim(e.text)) await noteEvidence($, 'task')
    const age = await read($, planAge)
    if (age.evidence === undefined || age.nudged) return next(e)
    const sent = await next({ ...e, context: [...(e.context ?? []), nudgeText(age.evidence)] })
    // A prompt that did not enter was not read: the reminder waits for the next one.
    if (sent.drop === undefined) await markNudged($)

    return sent
  })

  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    await update($, snapshot, last => ({ ...last, agents: last.agents + 1 }))

    return next(e)
  })

  on('tool.call', { tool: PLAN_TOOL }, async ($, e, next) => {
    await update($, plan, steps => applyPlan(steps, e as unknown as PlanCall))
    await touchPlan($)

    return next(e)
  })

  on('tool.call', { tool: CLOSE_TOOL }, async ($, e, next) => {
    const running = await runningAgents($)
    await update($, plan, steps => closePlan(steps, running))
    await touchPlan($)

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    // A turn that answered and never touched the list leaves a running step unconfirmed.
    if (e.agentId === undefined && e.reason === 'answer' && !(await read($, planAge)).touched) await noteEvidence($, 'turn')
    await refresh($)

    return done
  })

  on('ui.render', { component: 'Pane', requestId: PLAN_PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Text } = elements
    const Svg = svgOf(elements, e.surface)
    const steps = await read($, plan)
    const age = await read($, planAge)
    // Read for its 30-second refresh alone: the age line is redrawn with it.
    await read($, snapshot)
    const updated = steps.length === 0 ? undefined : ageLine(age, await $.clock.now())
    const stale = steps.length === 0 ? undefined : staleLine(age)

    return (
      <Box flexDirection="column" rowGap={Svg ? BLOCK_GAP : 0}>
        {steps.length === 0 && <Text dimColor>No plan.</Text>}
        {updated && (
          <Box flexDirection="column">
            <Text dimColor>{updated}</Text>
            {stale && <Text color="yellow">{stale}</Text>}
          </Box>
        )}
        {steps.map(step => (
          <Box flexDirection="row" columnGap={GAP}>
            {Svg ? (
              <Svg alt={planMark(step.status)} width={14} height={14} source={planIcon(step.status, stale !== undefined)} />
            ) : (
              <Text dimColor={step.status === 'completed'}>{planMark(step.status)}</Text>
            )}
            <Box flexDirection="column" flexShrink={1}>
              <Text dimColor={step.status === 'completed'}>{step.content}</Text>
              {step.session && <Text dimColor>{step.session}</Text>}
            </Box>
          </Box>
        ))}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: ENVS_PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Text, Button, Link } = elements
    const now: Envs = await read($, envs)
    // Drawing is the event for a pane being shown: if the session has moved since the last read, read again.
    void loadEnvsIfMoved($, now)
    const width = labelWidth(Object.values(ENV_LABELS))

    return (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between" alignItems="center">
          <Text bold>{now.project ?? ''}</Text>
          <Button key="refresh" onPress={() => void loadEnvs($)}>
            Refresh
          </Button>
        </Box>
        {now.repository && <Link href={now.repository} label={now.repository} />}
        {now.phase === 'loading' && <Text dimColor>Reading .claude/environments.json…</Text>}
        {now.phase === 'missing' && <Text dimColor>No `.claude/environments.json` in this project. Create one with /rigger-core:environments.</Text>}
        {now.phase === 'failed' && <Text color="red">Environments unavailable: {now.note ?? 'unknown reason'}</Text>}
        {now.rows.map(env => {
          const loud = isProd(env.role ?? env.name)
          const links = (['url', 'admin'] as const).filter(field => env[field])
          const details = ENV_DETAILS.filter(field => env[field])
          return (
            <Box
              flexDirection="column"
              marginTop={BLOCK_GAP}
              paddingX={1}
              borderStyle="round"
              {...(loud ? { borderColor: 'red' } : { borderDimColor: true })}
            >
              <Box flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={GAP}>
                <Box flexShrink={1}>
                  <Text bold>{env.name}</Text>
                </Box>
                {env.role && <Box flexShrink={0}>{roleBadge(elements, env.role)}</Box>}
              </Box>
              {links.map(field =>
                row(
                  elements,
                  width,
                  ENV_LABELS[field],
                  <Text>
                    <Link href={env[field] ?? ''} label={env[field]} />
                  </Text>,
                ),
              )}
              {links.length > 0 && details.length > 0 && <Box height={BLOCK_GAP} />}
              {details.map(field => textRow(elements, width, ENV_LABELS[field], env[field] ?? ''))}
            </Box>
          )
        })}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: ISSUES_PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Text, Button } = elements
    const list = await read($, issues)

    return (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={GAP}>
          <Box flexDirection="column" flexShrink={1}>
            <Text bold>Issues</Text>
            {list.repo && <Text dimColor>{list.repo}</Text>}
          </Box>
          <Button key="refresh" onPress={() => void refreshIssues($)}>
            Refresh
          </Button>
        </Box>
        {list.phase === 'loading' && <Text dimColor>Loading issues…</Text>}
        {list.phase === 'failed' && <Text color="red">Issues unavailable: {list.note ?? 'unknown reason'}</Text>}
        {list.phase === 'ready' && list.rows.length === 0 && <Text dimColor>{emptyText(list.empty ?? 'none', list.repo)}</Text>}
        {list.rows.map(issue => {
          const key = String(issue.number)
          const start = list.starts[key]
          const canStart = start === undefined || start.phase === 'failed'
          // A session without the start tool proposes the session with spawn_task, else works on the issue itself.
          const noStart = list.startTool === false
          const spawn = noStart && list.spawnTool === true
          const here = noStart && !spawn
          return (
            <Box flexDirection="column" marginTop={BLOCK_GAP}>
              <Box flexDirection="row" justifyContent="space-between" alignItems="center" columnGap={GAP}>
                <Box flexDirection="column" flexGrow={1} flexShrink={1}>
                  <Box flexDirection="row" columnGap={GAP}>
                    <Text bold>{`#${key}`}</Text>
                    <Text>{shortTitle(issue.title)}</Text>
                  </Box>
                  <Text dimColor>{issue.state}</Text>
                </Box>
                <Box flexDirection="row" columnGap={GAP} flexShrink={0}>
                  {canStart && !here && (
                    <Button key={`start-${key}`} onPress={() => void startIssue($, issue.number, issue.title)}>
                      Start
                    </Button>
                  )}
                  {(here ? canStart : start?.phase === 'failed') && (
                    <Button key={`ask-${key}`} onPress={() => void askSession($, issue.number, issue.title)}>
                      {here || spawn ? 'Do it here' : 'Ask this session'}
                    </Button>
                  )}
                </Box>
              </Box>
              {start && <Text dimColor={start.phase !== 'failed'}>{start.text}</Text>}
            </Box>
          )
        })}
        {list.phase === 'ready' && list.capped && <Text dimColor>{cutLine()}</Text>}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const elements = $.ui.resolve(e)
    const { Box, Text } = elements
    const Svg = svgOf(elements, e.surface)
    const now = await read($, snapshot)
    const width = labelWidth(HARNESS_LABELS)
    const line = (label: string, value: string) => textRow(elements, width, label, value)
    // The context figures and, under them, a bar of how full the window is.
    const percent = now.percent
    const bar =
      percent === undefined ? undefined : Svg ? (
        <Svg alt={`Context ${percent}% full`} width={BAR_WIDTH} height={BAR_HEIGHT} source={barSvg(percent)} />
      ) : (
        <Text color={barColor(percent)}>{textBar(percent, barCells(e.props.bodyColumns, width))}</Text>
      )
    const context = (
      <Box flexDirection="column">
        <Text>{`${thousands(now.tokens)} / ${thousands(now.window)} (${percent ?? '?'}%)`}</Text>
        {bar}
      </Box>
    )

    return (
      <Box flexDirection="column">
        {row(elements, width, 'Context', context)}
        {line('Session cost', now.usd === undefined ? '?' : `$${now.usd.toFixed(2)}`)}
        {line('Weekly limit', now.weekly === undefined ? '?' : `${now.weekly}%`)}
        {line('Agents in session', String(now.agents))}
        {line('Rigger', now.version ?? '?')}
        {line('Lessons in inbox', now.lessons === undefined ? '?' : String(now.lessons))}
        {line('Compaction', now.guard ?? '?')}
      </Box>
    )
  })
}
