# rigger-core

The core plugin of Rigger: skills, agents, commands and hooks for running Claude Code with
subagents and long sessions.

Install: `claude plugin marketplace add Borcioo/rigger-claude-code`, then
`claude plugin install rigger-core@rigger`. Requirements: `sh` (Git Bash on Windows) and Python 3.9+
for the hooks and the rules; `/rigger-core:doctor` checks both. Update every Rigger plugin together:
the others' rules are printed by this plugin.

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
  dependency of this plugin. With Python it also prints the assembler's rows: `rules-assembly` (OK
  with the module and section counts, or WARN with the first problem), one `rule-override` per
  `rules.d` section that replaces a Rigger one, one `expects` per plugin the project file expects
  that is not installed and enabled (FIX `claude plugin install <name>`), and one `config-inactive`
  per config key that has no effect in this release.
- `/rigger-core:harness-setup`: runs doctor, then writes the Compact Instructions section into your
  user `CLAUDE.md` and, if you opt in, the `autoCompactWindow` setting. It
  asks before each change and changes nothing else.
- `/rigger-core:coordinate [plan]`: runs an approved plan with each independent part in its own
  session. Needs the Claude desktop app (its session tools) and the superpowers plugin; elsewhere it
  falls back to background subagents.
- `/rigger-core:environments`: fills `.claude/environments.json` from what the repo says, for the
  rigger-panel Environments pane.

## Hooks

| Event | What runs |
|---|---|
| SessionStart (startup, clear, compact), SubagentStart | the rules of every Rigger plugin, through 6 and 4 part handlers (`tools/assemble/part.sh`; "Rules assembly" below); the state-file rule goes to the main session only. On `startup`, `harness_start` warns about hooks that run twice (in both `settings.json` and a plugin) and about `## ` sections of your user `CLAUDE.md` that an enabled plugin already injects |
| UserPromptSubmit, PreCompact, PostCompact, SessionStart (compact) | the context guard: reminds a long session to keep its state file and holds automatic compaction until the state file says `safe_to_compact: true`, then puts the file back after compaction (`tools/context-guard/INSTALL.md`) |
| PostToolUse (Write, Edit of `.claude/environments.json`) | `tools/env-check` returns the reason the rigger-panel Environments pane would refuse the file |
| PreToolUse (`wrap_up`, `AskUserQuestion`), Stop | the side-session guard, desktop app only: a main session cannot end its turn while a side session it started has delivered a result it has neither archived nor listed under `## Side sessions kept` in its state file; the denial says both ways out. While the state file frontmatter has `plan:` and no allowed `stop:`, it also denies `AskUserQuestion` and a `wrap_up` with an `ask`: an approved plan runs without mid-plan questions |
| SubagentStop | `report_shadow` records, without blocking, whether each subagent's report opens with `STATUS: <word>` |

## Rules assembly

`tools/assemble/assemble.py` prints the rules of every enabled plugin that holds a
`rigger-module.json` (its rules files, their audience and its skill-table rows): this plugin first,
then the marketplace's order, then the rest by name, then your `rules.d` (rigger-rules' README). It
packs them into parts of at most 9,000 bytes; each part starts with the `Plugin root:` line of its
first plugin. The rules print on `startup`, `clear` and `compact` (from a snapshot of the session's
startup); `resume` prints nothing.

- **Problems print as lines at the top of part 1:** `Rigger rules not loaded: … (run
  /rigger-core:doctor)` in place of the rules when Python 3.9+ is missing or the assembler fails,
  and lines starting `rigger:` for a skipped plugin, a bad config file, or a replaced or duplicate
  rule. Rules that fit in no part are named in `rigger: rules left out over the hook cap: <ids>` at
  the end of the last part.
- **rigger-core older than another Rigger plugin:** rigger-rules, rigger-windows, rigger-wp,
  rigger-lessons and rigger-talk each run `hooks/core_check.sh <name>` on `startup|clear`. When that
  plugin runs from its cache install and no installed rigger-core holds `tools/assemble/part.sh`, it
  prints `Rigger rules not loaded for <name>: rigger-core is older than <name> (run claude plugin update rigger-core)`.
  A plugin from a directory marketplace is not checked.
- **`RIGGER_MODULE_DIRS`:** plugin roots separated by the OS path separator (`;` on Windows, `:`
  elsewhere), each absolute and holding a `rigger-module.json`. Each is loaded in place of the
  installed copy of that plugin (one `rigger:` line says so when the copies differ). Set it to the same roots you
  pass with `--plugin-dir`: a `--plugin-dir` plugin is in no Claude Code state file, so without it
  its rules are missing and nothing says so. Plugin options of such a root are read from
  `pluginConfigs["<plugin>@inline"]` in `<config dir>/settings.json`, an installed plugin's from
  `<plugin>@<marketplace>`.
- **Config files**, JSON with `"schema": 1`: `rigger-defaults.json` in this plugin, then
  `<config dir>/rigger/config.json`, then `<project>/.claude/rigger.json`, the later one winning
  key by key (`null` removes a key). In this release `triggers` (rows of the skill tables: `before`,
  `load` with 1 to 4 `plugin:skill` or `slot:<name>` entries, and `section`) and, in the project
  file, `expects` (a list of plugin names) take effect. `slots`, `trust`, `review`, `shell_guard`,
  `company` and `leak_guard` are read and listed by doctor as `config-inactive`, with no effect yet.
- **`--render [--subagent]`** prints what a session or a subagent gets; **`--check [--modules
  <dir>...]`** fails on a duplicate id in one layer, an orphan override (a company section whose file
  stem matches a general file but whose id matches no general section there, or an empty one), a
  section over 9,000 bytes, an unknown placeholder, a trigger `load` that resolves to nothing, or 0
  rules files read. `rules.d` is not checked.

## Tools

- `tools/token-audit/token_audit.py`: where tokens go, from local transcripts (`report`), the cost
  of an A/B run (`runs`) or of one job's agents (`job`).
- `tools/variant-run/variant_run.py`: runs a command or an eval with a modified copy of your global
  `CLAUDE.md` instead of the live one.
- `tools/session-watch/session_watch.py`: the background watcher `/rigger-core:coordinate` uses
  for side sessions.
