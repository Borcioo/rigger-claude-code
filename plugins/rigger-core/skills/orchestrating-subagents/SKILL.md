---
name: orchestrating-subagents
description: >-
  Load BEFORE dispatching a subagent (Agent/Task tool) or running several agents at once: writing
  a brief, choosing agent type or model, fanning a plan out in parallel (worktrees, ports, shared
  database), a fix round after a review, replacing a silent agent, or following superpowers'
  subagent-driven-development or dispatching-parallel-agents, which these rules override.
---

# Orchestrating subagents

Overrides superpowers' subagent-driven-development and dispatching-parallel-agents where they
differ: implementation agents may run in parallel in separate worktrees; a fresh implementation
agent after the first fix round; one review per plan, plus a per-task review for security, data, money and shared infrastructure; a silent
agent is checked after 20 minutes.

Words used below. **Risk** is the mark a brief gives its change, `full` or `low`, as the tests
rule defines it (rigor follows risk, check by check); a full change gets a red run per new guard
(the guard's code mutated once, the failing test shown) and a fresh reviewer. A **gate** is the project's full check command. A **merge point** is a
merge of finished work into the integration branch (the branch parts merge into before the default
branch). A **phase** is a group of plan tasks that merge together; its **backlog** holds the Minor
findings fixed once at the phase's end.

## Parallel work

- "A separate agent" means a subagent. Start a separate session only when the user asks for one,
  or the work ends in its own branch, PR or repo.
- Run independent areas in parallel, each with its own worktree (a path outside the repo), port and
  scratch subfolder (`<scratch>/<task>`), all named in the brief. Stay sequential where files overlap. The
  same holds for separate sessions: one that writes works in its own worktree, not the shared
  checkout (except under the shared-tree bullet below). First `git worktree list` shows which
  branches others hold and `git -C <path> status --short` on each the files they touch; stay out of
  those.
- A plan names for each task what it needs (a sha, a file, a decision) and which files it writes;
  tasks with disjoint files start as soon as their inputs exist, up to the load limit. An "A → B"
  order needs a reason. Split a worker that spans several plugins or folders into one per folder.
- Settle an architecture fork (two designs with different build work) with the user before
  building on either; offer the simpler one first.
- While a question waits for the user, start the read-only work that follows; ask independent
  questions together in one card.
- Keep to the machine's load rule (on Windows with rigger-windows installed:
  `rigger-windows:windows-dev-env` "Machine load") and the e2e lock rules in
  `rigger-core:e2e-playwright-speed` §9. Light agents (reviews, Python or pwsh unit tests, docs)
  have no count limit; a build or test agent starts only while `mem-check` exits 0 on Windows with
  rigger-windows installed (it prints the minimum); elsewhere check free memory with the OS's tool.
  Heavy jobs (WSL, Docker, wp-env, e2e) run at most 2 at once.
  Without a lock, count the heavy jobs you dispatched that are still running, and wait while the
  limit is reached. The plan names the suites that assert on timing: their final tally runs alone,
  or the brief states the N-of-10 pass threshold.
- The site the user clicks through runs a pinned checkout of reviewed commits, never a tree agents
  edit.
- **Before dispatching agents that test against one database or site, read what the shared test
  setup and cleanup delete** (a purge by project prefix wipes other agents' data). Serialise DB
  tests with a lock, or give each agent data of its own (`rigger-core:e2e-playwright-speed` §2), and keep
  scripts that write real data out of agents' hands while others compare screens. Before
  fanning out over one database server, run the same database test from two worktrees at once:
  a framework that names test databases by test path (sqlx) or uses one fixed test database
  needs a per-worktree namespace or a lock around every test run, not only around the gate.
- **Where a tree has to be shared anyway** (a mounted environment, a checkout nobody could split),
  every edit leaves the code runnable: add the callee before the call, or make the whole change in
  one edit. When an HTTP or browser check goes red there, read the server log for a fatal in a file
  outside your own list before debugging your own code, and rerun after the other agent's commit.

## Briefs

The main session is the orchestrator: it assigns each task to a fitting agent and model, checks
results against evidence, answers questions and helps when a task is too hard; it never only
relays.

Contact runs both ways. An agent or side session that is unsure, or has failed the same step
twice, stops and asks whoever started it, stating what it tried, what it saw and what it needs. A
subagent without SendMessage does this as a blocked report and is resumed with the answer. A side
session never asks the user unless the orchestrator says so.

The orchestrator answers, splits the task or moves it to a stronger model with the last report. A
failure on the stronger model goes to the user with the diagnosis.

- **Main-session context is the scarce budget.** While planning or orchestrating, send reading whose
  conclusion you need, not its text (a whole file over ~200 lines, a `--help` or directory dump, an
  unfamiliar repo), to an `Explore` subagent (a conclusion) or `rigger-core:mapper` (locations only). A brief
  that starts a session or a multi-task agent repeats this, names each skill with the step it
  covers and never says to read skills up front.
- Agent type: `rigger-core:worker` for implementation, `rigger-core:reviewer` for review or
  verification (their descriptions say when another type fits). Both load skills themselves and can
  use a browser: the brief names the skills the task needs, the browser, if any, and the Risk mark. Set `model` on every dispatch
  except `rigger-core:implementer` and `rigger-core:mapper`, which pin their own; an agent
  dispatched without one inherits yours.
- Any skill or MCP server the task needs, a project's own included: name it in the brief.
- Follow `templates/implementer-brief.md` at the rigger-core plugin root (two folders above this
  skill's folder). Every brief
  carries the project's pitfalls list (project CLAUDE.md; create the section if missing), every
  lesson captured this session, and the exact gate commands. A brief has no Context or Background
  section: every line is an instruction, an input or an output (the template's Plan context field
  carries constraints and stays).
- Strongest model for security-sensitive work, design and every final review; `sonnet` for
  mechanical edits and for re-reviews inside a fix round. Never `haiku` for review or
  verification: it reports false findings.
- To know what one piece of work cost across its agents, make `Job: <id>` the first line of each of
  its briefs (any label: an issue number, a branch name); the final report then ends with the output
  of `tools/token-audit/token_audit.py job --job <id> --session <session id>`, run from the
  rigger-core plugin root.
- Run each gate command once yourself before it goes into a brief; pilot one unit the whole way
  (full gates, every test, registry and pinned list it edits) before fanning out or calling units
  file-disjoint. Every count, line number, baseline or code value a plan or brief
  names comes from a command run now and pasted (`git ls-files` for file counts, the gate's own
  scanner for lines), or from reading every route that sets the value, never from a summary.
- After the first fix round, dispatch a fresh implementation agent (brief + last report) instead of
  resuming.
- A report whose first line is `STATUS: PARTIAL` or `STATUS: BLOCKED` is accepted only with the exact call, its raw output and one
  alternative route with its raw output. Re-run the failing call once, in the agent's worktree with
  the brief's environment. Still red: the blocker stands; fix it at the source or ask the user.
  Green after one command or setting: a shortcut or a brief gap; append it to the
  lessons inbox (rigger-lessons: `lessons-inbox.md` in the config dir) and put it in the next briefs.
- Check any agent silent for 20+ minutes; when one reports waiting on background work, check at
  once that the process runs and its output is fresh, and again within 20 minutes. A session that
  waits runs one background check that exits on the condition (a Monitor prints only that line)
  and ends a turn only with a result or a decision, never "still waiting".
- Work inherited from an agent that stopped mid-task is unverified: run it before reading it as
  done. Compare each agent's reported changes (Commits, `git diff --stat`) with
  `git status --porcelain` before trusting its report.
- A status over a side session's unattended period reads every message of that period and searches
  them for breaches, irreversible steps and scope changes before reporting; bad news leads.
- A clone whose licence forbids sending its source to the model is deleted once its metadata is read,
  or kept outside any folder a later agent searches; every later brief repeats the exclusion.

## Fix rounds and progress

- Fix rounds take Critical and Important findings, and each entry of the reviewer's `SHORTCUTS:` line counts as Important;
  Minor findings go to the phase backlog, fixed once per phase. A finding that needs a design
  decision (new mechanism, global side effect, data deletion) gets a short plan, reviewed before any
  implementer starts; a second fix round on one task means re-plan. A fix-round or re-review brief carries the findings and nothing else (no "make it cheaper if safe"), naming each finding moved to another task with the task that takes it; a performance change in a safety check
  is its own task.
- A `Pre-existing` finding with evidence never enters the author's fix round: the orchestrator
  proposes a separate session for it (one per bug, or one for a group of Minor ones).
- A Critical finding on a Risk-full change goes first to two fresh read-only `rigger-core:reviewer`
  agents that check it in the code; it is fixed when both confirm, and on a split the orchestrator
  decides from their evidence.
- A re-review gets the fix diff, the findings it answers and the author's red-run output, and re-runs only the red runs (mutations) on changed lines, against every test file that covers them, never every red run of the change; it reviews the whole change again only when the fix touches files the first review did not see, or on a Risk-full change.
- The orchestrator records each finding's outcome in the state file under `## Findings`, one line
  each: `<finding> · <severity> · <outcome>`, the outcome being fixed, disputed, intentional or
  pre-existing-deferred.
- The orchestrator runs the full gate once per merge point, in the background, not after every
  agent.
- Update the visible task list when each subagent returns and say how many tasks remain: the user
  follows progress there.
