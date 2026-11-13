# rigger-core

The core plugin of Rigger: skills, agents, commands and hooks for running Claude Code with
subagents and long sessions.

Install: `claude plugin marketplace add Borcioo/rigger-claude-code`, then
`claude plugin install rigger-core@rigger`. Requirements: `sh` (Git Bash on Windows) and Python 3.9+
for the hooks; `/rigger-core:doctor` checks both.

## Skills

- `git-workflow`: branches, commits, pull requests and SemVer releases.
- `orchestrating-subagents`: briefs, agent types and models, parallel work, fix rounds.
- `stack-tooling`, `e2e-playwright-speed`, `web-ui-traps`: tool choices, fast Playwright suites,
  UI and accessibility traps.

## Agents

`rigger-core:mapper` (finds code locations), `rigger-core:implementer` (one mapped change),
`rigger-core:worker` (implementation from a brief), `rigger-core:reviewer` (review and
verification, no edits).

## Commands

- `/rigger-core:doctor`: checks the machine (Claude Code version, the hook shell, `git`, Python as
  the hooks find it, the Rigger plugins and settings) and offers one block of install commands, run
  only after your yes. It reports `rigger-rules` and `rigger-lessons` (and `rigger-windows` on
  Windows) as MISSING when they are not installed: they are the recommended set, not a hard
  dependency of this plugin.
- `/rigger-core:harness-setup`: runs doctor, then writes the Compact Instructions section into your
  user `CLAUDE.md` and, if you opt in, the `autoCompactWindow` setting and the worktree root. It
  asks before each change and changes nothing else.
- `/rigger-core:coordinate [plan]`: runs an approved plan with each independent part in its own
  session. Needs the Claude desktop app (its session tools) and the superpowers plugin; elsewhere it
  falls back to background subagents.
- `/rigger-core:environments`: fills `.claude/environments.json` from what the repo says, for the
  rigger-panel Environments pane.

## Hooks

| Event | What runs |
|---|---|
| SessionStart (startup, clear, compact) | the state-file rule for the main session (subagents never get it); `harness_start` warns about hooks that run twice (in both `settings.json` and a plugin) and about `## ` sections of your user `CLAUDE.md` that an enabled plugin already injects |
| UserPromptSubmit, PreCompact, PostCompact, SessionStart (compact) | the context guard: reminds a long session to keep its state file and holds automatic compaction until the state file says `safe_to_compact: true`, then puts the file back after compaction (`tools/context-guard/INSTALL.md`) |
| PostToolUse (Write, Edit of `.claude/environments.json`) | `tools/env-check` returns the reason the rigger-panel Environments pane would refuse the file |
| PreToolUse (`wrap_up`), Stop | the side-session guard, desktop app only: a main session cannot end its turn while a side session it started has delivered a result it has neither archived nor listed under `## Side sessions kept` in its state file; the denial says both ways out |
| SubagentStop | `report_shadow` records, without blocking, whether each subagent's report opens with `STATUS: <word>` |

## Tools

- `tools/token-audit/token_audit.py`: where tokens go, from local transcripts (`report`), the cost
  of an A/B run (`runs`) or of one job's agents (`job`).
- `tools/variant-run/variant_run.py`: runs a command or an eval with a modified copy of your global
  `CLAUDE.md` instead of the live one.
- `tools/session-watch/session_watch.py`: the background watcher `/rigger-core:coordinate` uses
  for side sessions.
