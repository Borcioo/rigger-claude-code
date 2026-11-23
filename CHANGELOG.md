# Changelog

All notable changes to this marketplace are listed here, newest first, in the Keep a Changelog
format. Releases are tagged `v<version>`.

## [Unreleased]

### Added

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
