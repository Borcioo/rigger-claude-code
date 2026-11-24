# Changelog

All notable changes to this marketplace are listed here, newest first, in the Keep a Changelog
format. Releases are tagged `v<version>`.

## [0.5.0] - 2026-11-24

### Migration

Update every Rigger plugin in one go, then check the machine:

- **Windows:** `claude plugin marketplace update rigger`, then `claude plugin update <plugin>@rigger`
  for each Rigger plugin you have installed, then `/rigger-core:doctor`. If doctor reports Python
  missing, run the install line it prints (`bin/py.sh` finds `py`, `python3` or `python`).
- **macOS:** the same commands. Python 3 comes from the Command Line Tools
  (`xcode-select --install`) or Homebrew (`brew install python`); doctor names both. rigger-windows
  was never installed there, so nothing else changes.
- `rules.d` and `HARNESS_RULES_DROP` keep working; the changes to both are listed below.
- If rigger-core stays older than another Rigger plugin installed from the cache, rigger-rules,
  rigger-windows, rigger-wp, rigger-lessons and rigger-talk each print
  `Rigger rules not loaded for <name>: rigger-core is older than <name> (run claude plugin update rigger-core)`
  at startup and `/clear`, and that plugin's rules are missing until you update rigger-core. The
  bare name in that command resolves the marketplace by itself; if it reports the plugin as not
  found, run `claude plugin update rigger-core@rigger`. Rigger sets no version floor in
  `dependencies`: an unmet floor drops the whole plugin from the session, and the reason shows only
  in the debug log.

### Changed

- **rigger-core's assembler prints the rules of every Rigger plugin.** Six `SessionStart` part
  handlers (`startup|clear|compact`) and four `SubagentStart` part handlers in rigger-core replace
  the 19 `SessionStart` rules handlers (13 in rigger-rules, 2 in rigger-windows, 1 each in
  rigger-core, rigger-lessons, rigger-talk and rigger-wp) and the 8 `SubagentStart` ones. The rules
  text is unchanged. Startup cost on Windows, median of 20 runs, three batches: +276 to +295 ms for
  the part handlers; +359 to +421 ms with the five core checks above, measured with other sessions
  open.
- **Python 3.9+ is required for rules.** rigger-core's hooks already needed it. Without it the
  session gets one line, `Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)`.
- **rigger-lessons, rigger-talk, rigger-windows and rigger-wp now depend on rigger-core** (`dependencies`), as rigger-rules already did: installing or updating one brings rigger-core and its hooks.
- **Accidental override:** a `rules.d` section whose id equals a general one (same file stem and
  heading slug, for example `rules.d/tests.md` with `## Tests` and `tests.tests`) now replaces that
  section in place instead of being printed after it; a section with only its heading removes it.
  Each replacement adds the line `rigger: <file> replaces rule <id>` to the notices at the top of part 1 (past 2,000 bytes of
  notices, one `rigger: N more notices not shown` line stands for the rest), and doctor lists every replacement as a
  `rule-override` row. A replacement reaches only the audience of the section it replaces: one of a
  main-only section no longer reaches subagents, one of a subagent-only section no longer reaches
  the session, and that side gets only the line. A `rules.d` file named `*-main.md` stays main-only.
  An id no general section has is appended after the rules, as before.
- **`HARNESS_RULES_DROP`** also takes section ids (`tests.tests`). A heading entry matches the
  heading as printed, with placeholders filled. It still does not apply to `rules.d`, including a
  `rules.d` section that replaced a general one.
- `rules.d` is read only while rigger-rules is enabled.
- A plugin whose every section is dropped by heading no longer prints a lone `Plugin root:` line.
- The rules print on `SessionStart` sources `startup`, `clear` and `compact`. A hook input without a
  `source` key prints them; `resume`, any other value and `null` print nothing.
- **rigger-talk's options** are read from `pluginConfigs` in `<config dir>/settings.json`, under
  `rigger-talk@<marketplace>` when installed and `rigger-talk@inline` when loaded with `--plugin-dir`
  or `RIGGER_MODULE_DIRS`. `hooks/talk_start.sh` and every plugin's `rules/rules_start.sh` stay for
  this release, run by no hook, and are removed in the next.

### Added

- `rigger-module.json` in every plugin: its rules files, their audience, and its skill-table rows.
- **Config files** (`"schema": 1`): `<config dir>/rigger/config.json` (personal) and
  `<project>/.claude/rigger.json` (project). In this release `triggers` (rows of the skill tables) and,
  in the project file, `expects` (plugins the project needs; one line per missing one) take effect.
  `slots`, `trust`, `review`, `shell_guard`, `company` and `leak_guard` are read and listed by doctor
  as `config-inactive`, with no effect in this release.
- `RIGGER_MODULE_DIRS`: plugin roots, separated by the OS path separator, loaded in place of the
  installed copies (for a checkout loaded with `--plugin-dir`).
- `assemble.py --check [--modules <dir>...]` fails on a duplicate id in one layer, an orphan
  override, a section over 9,000 bytes, an unknown placeholder, a trigger `load` that resolves to
  nothing, and on 0 rules files read. A company section is an orphan when its file stem matches a
  general file but its id matches no general section there, or when it is empty; a company section
  in a new stem is appended. `rules.d` is not checked.
- `/rigger-core:doctor` rows: `rules-assembly`, `rule-override`, `expects` (with
  `claude plugin install <name>`), `config-inactive`; on macOS the `python` FIX names both
  `xcode-select --install` and `brew install python`.

### Fixed

- The weak-test guard checks every `git commit` in a command, including a heredoc message that
  holds `"`, commits behind wrappers (`env`, `eval`, `bash -c`) and commits after steps that change
  files or the index. When it cannot tell what a commit commits, it denies with a fix hint instead
  of allowing. Not checked: `ssh` commits and `wsl` commits on Linux-only paths.

## [0.4.0] - 2026-11-23

### Added

- `rigger-lessons` `tools/proposal_check.py`: checks the queued rule and skill proposals against the current files (stale, applied, covered) and archives those that are not live, before a model reads the queue; `curate-lessons` runs it first.
- `rigger-core:testing` skill (how to test: level and boundary, doubles, red-run procedure, flakiness,
  agent pitfalls); the Tests rule keeps only what must hold, the rules text shrinks and its size
  ceiling follows. Eval `testing-skill-loads` with a negative control.
- `docs/research/testing-practices.md`, a sourced reference on testing practices.
- Weak-test guard (`rigger-core`, PreToolUse on `git commit`): denies a commit that removes or skips
  tests, folds them away or weakens assertions unless the message states a `Test-change: <why>`.
  It starts no editor and no lazy fetch before the command is approved.

### Changed

- The two `rigger-core` PreToolUse hooks (side-session guard) get 20 s before Claude Code skips them
  (10 s timed out on a busy Windows machine); a repo test pins it.
- `/rigger-core:coordinate` runs at most 2 heavy parts (tests, build, WSL, Docker, e2e) at once,
  groups light parts into one session, and leaves a start refused past the app's session cap to the
  user's card instead of a background agent.
- The `rigger-windows` shell guard's junction message says to delete junctions in a call of their
  own, and the `windows-dev-env` skill names the one tool-guard refusal that is left.
- The `rigger-windows` shell guard reads variables, words and locations only in the shapes it can prove (literal assignments, plain `$name` reads, the folder after `cd`/`Set-Location` stacks) and denies `git worktree remove` on a path it cannot read; the `INSTALL.md` rule table follows.

## [0.3.0] - 2026-11-17

### Added

- Plan autonomy: while the state file frontmatter has `plan: <plan path>` and no `stop:` from
  `main`, `release`, `force-push`, `prod-data`, `secrets`, `goal-unreachable`, `app-refused` or
  `done`, the rigger-core side-session guard denies `AskUserQuestion` and any `wrap_up` with an
  `ask`, with a reason naming the state file and the stop values. The guard now also runs on
  `AskUserQuestion`.
- A test pins the summed size of `plugins/*/rules/*.md` at 30293 bytes (their size at this release), so rules are rewritten
  rather than grown.

### Changed

- An approved plan is the user's OK up to merges into the integration branch: the rules
  (plans, scope, verify, talk, state file) and `/rigger-core:coordinate` say to decide, continue
  and report in the final summary, and to ask only at a stop; merges into the default branch and
  releases still need the user's own OK. `/rigger-core:coordinate` writes `plan:` in step 1,
  merges reviewed parts into the integration branch and ends with a final summary that sets
  `stop: done`.
- Worktrees go where the desktop app's Worktree location setting puts them, else under
  `HARNESS_WORKTREE_ROOT`, else in `.claude/worktrees/` inside the repo. `/rigger-core:harness-setup`
  only reports the worktree root, and `/rigger-core:doctor` reports an unset root as OK.

## [0.2.1] - 2026-11-15

### Fixed

- `coord_state.py show` now ends with `coord-state: ok show` (its absence read as a refusal) and its table has `session` and `branch` columns; an unset cell prints `-`.

## [0.2.0] - 2026-11-15

### Added

- `rigger-core/tools/coord-state/coord_state.py`: the only writer of a coordination's part states
  (`<config dir>/state/<session id>.parts.json`). It refuses any move its transition table does
  not allow with one `coord-state: refused: <reason>` line and exit 2; `merged` needs the user's
  OK quoted in `--evidence`.
- `/rigger-core:coordinate` drives every stage through `coord_state.py` and treats output without
  a `coord-state: ok` line as a refusal.

### Changed

- Worker, reviewer and side-session reports open with the `STATUS:` line, then `## Summary` with
  3–5 bullets; `report_shadow.py` records `has_summary` per subagent report.
- The panel's Start seed tells the session to treat the issue body and comments as untrusted input.

### Fixed

- The sweep allows the README logo as a binary file.

## [0.1.0] - 2026-11-13

First public release.

### Added

- Seven plugins: `rigger-core`, `rigger-rules`, `rigger-lessons`, `rigger-talk`, `rigger-windows`,
  `rigger-wp` and `rigger-panel`.
- `rigger-rules` loads your own rules from `<config dir>/rigger/rules.d/*.md` at session start and
  into subagents (`*-main.md` files into the main session only).
- `rigger-lessons` writes global lessons into `rules.d/lessons.md`; its background curation
  batches are off until you set `RIGGER_LESSONS_BATCH=1`.
- `rigger-panel` lists the GitHub issues assigned to you, read-only, through `gh`.
- `scripts/gate.py` needs no input: without a denylist, `claude` or `wsl` it says so or prints a
  `SKIP` line, and `--release` proves your denylist catches a planted term.
