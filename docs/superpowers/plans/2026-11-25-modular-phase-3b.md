# Modular architecture, phase 3b: shellwise, the platform module, mem-check and the removal of rigger-windows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pinned shellwise binary fills `shell-guard` on all three systems, the platform module
takes the Windows and toolchain knowledge, `mem-check` and `safe-edit` are Python tools for every
system, the test-evidence gates and gate-lab join the testing module, rigger-windows leaves the
marketplace; and the harness gets the lane rule, a state tool, a turn-end guard for side sessions
and the 3a backlog.

**Status:** revision 1, written on `1ead09a` (release 0.7.0), for the coordinator to review and
split into lanes.

**Architecture:** Three merge points. PR A changes only rigger-core, the two 3a modules and shared
test infrastructure (coordinate, state tool, turn-end guard, backlog). PR B adds new code that does
not touch rigger-windows: the shellwise module, Python mem-check in core, Python safe-edit in the
testing module. PR C moves rigger-windows' remaining parts with `git mv` (test-evidence and gate-lab
to testing, drift-lint to core, skills, rule and evals to the platform module), switches the
`shell-guard` default, deletes the rest and writes the migration. One release (0.8.0) after PR C.

**Tech Stack:** Python 3.9+ stdlib only (through `bin/py.sh` with `-I -X utf8`), POSIX `sh`
launchers, `unittest`, `git`, `gh` (optional at run time), `plugins/rigger-rules/tests/stub_api.py`
for `claude` runs, GitHub Actions for macOS and Linux.

**Spec:** `docs/superpowers/specs/2026-11-25-phase-3b-design.md` (the design; it wins for 3b), on top
of `docs/superpowers/specs/2026-11-25-phase-3-decisions.md` (D1–D4, D8) and §13 of
`docs/superpowers/specs/2026-11-23-modular-architecture-design.md`. Decisions here start at D74.

## Global Constraints

**Scope and promise**
- 3b changes behaviour only as listed under "Behaviour changes". No change to shellwise (its
  owner's repository); a later shellwise release is a pin bump (D77).
- Base: `1ead09a`. PR A, PR B and PR C each go to `main`, squash-merged after CI. PR B is cut from
  `main` after PR A merges, PR C after PR B. Merging and the release are the owner's steps.
- Public repository: nothing under `plugins/`, `scripts/`, `tests/`, `docs/` or `.github/` names a
  company, client, person or private repository, or holds an absolute local path. New manifests copy
  `license`, `homepage`, `repository` and `author` from rigger-core's `plugin.json`.
- **Spelling of the new plugins.** Where a `:` or a `plugins/` path follows, this plan writes
  `rigger-SHELLWISE` and `rigger-PLATFORM`, so `tests/test_plugin_refs.py` passes on this commit. The
  folders, manifests and every file the tasks write use the lower-case names.
- Code, identifiers, comments and commit messages in English. Conventional Commits, no attribution
  lines.

**Runtime** (unchanged from 3a)
- Python floor 3.9; hooks run `py.sh` with `-I -X utf8`; a hook handler that only reports exits 0;
  hook output is LF bytes; launchers stay POSIX `sh`.
- Files are read as bytes and decoded as UTF-8 explicitly; every write passes `encoding="utf-8"` or
  writes bytes; state and status files are written atomically (temp file in the same folder, then
  `os.replace`).
- A plugin never reaches a sibling by relative path: through `installed_plugins.json` `installPath`
  or an explicit flag; tests run from a copy laid out like the cache.
- `bin/py.sh` copies stay byte-identical (`tests/test_repo.py:197-202`); the shellwise and platform
  modules get one only if they run Python hooks (the shellwise module does; the platform module
  runs only `core_check.sh`).
- A path that only has to be absolute is made with `abspath`, never `Path.resolve()`.

**Tests and runs**
- Unit tests of `tools/<tool>/<name>.py` live in `tools/<tool>/tests/test_<name>.py`. Every new test
  gets one red run on its own assertion, named in the task's report.
- **Full-risk checks** (broken once per branch, table row, stage and wiring):
  - the shellwise installer: target table, hash check, archive member check, lock, atomic
    placement, override version check, the `current` file (Task 16: a downloaded binary is run on
    every command);
  - the launcher and its wiring: missing file, unreadable file, binary exit and stdout passed
    through (Task 17);
  - the state tool's `clean` (live-session refusal, the 14- and 60-day rules, log rotation keeping
    the newest lines) and every write keeping other sections byte for byte (Task 3: data deletion);
  - the turn-end guard's block, poll bound and loop cap (Task 5);
  - the newly-named rules file check (Task 9: trust);
  - the moved commit and Stop shadow handlers' wiring (Task 23).
  Shared infrastructure (the state SessionStart check, `mem-check`, `safe-edit`, `core_check`, the
  read-by narrowing) gets one mutant per branch up to 5 per check, the rest listed in the report.
- Python mutants run with `-X pycache_prefix=<dir per mutation>` on a scratch copy without `.git`.
- `run_tests.py` strips `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`; a test that needs one sets
  it itself. A test that needs a repository builds its own with `git init` in a temp folder.
- Git Bash `sh.exe` started from Python re-parses its command line: no quote or glob characters as
  arguments, or put them inside an `sh -c` string.
- A change to a rules file, a skill, an agent or a command file runs
  `plugins/rigger-rules/tests/test_plugin_rules.py` and the drift-lint test (at
  `plugins/rigger-windows/tools/drift-lint/tests/test_drift_lint.py` until Task 25 moves it) in the
  same round as the edit.
- A task that adds or removes a hook handler runs `tests/test_repo.py` (`DuplicateHookTests`) and
  updates `scripts/hook_latency.py` `CASES` with `tests/test_hook_latency.py`.
- `scripts/run_tests.min` reads 79. Only the three gate tasks (13, 22, 32) set it, each to the count
  `run_tests.discover` prints at that commit.
- Gate: `python -I scripts/gate.py` in the background, started on Windows with the full path of the
  installed Python (not the WindowsApps alias). Read `status.txt`. Between fixes
  `python -I scripts/run_tests.py --fix-round` (`--no-claude` when no `claude` run is allowed).
  Before a pull request, `gate.py --verify`. Every pull request here holds a shared path, so each
  takes the full gate.

**Heavy and light** (for the coordinator's count of 2 heavy jobs)
- **Heavy:** the task runs the full gate, the WSL leg, a stub `claude` session, a download, or a
  suite file that takes more than 2 minutes.
- **Light:** the task writes files and runs the unit-test files it touches and the rules tests.

**Real `claude` runs (Tasks 1, 14, 29)**
- A fresh temp `CLAUDE_CONFIG_DIR` per experiment; `--plugin-dir` runs set
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to scratch, leave `RIGGER_LESSONS_BATCH`
  unset and name the same roots in `RIGGER_MODULE_DIRS`. The child drops `CLAUDE_*`, `ANTHROPIC_*`,
  `HARNESS_*` and `RIGGER_*`, then sets `ANTHROPIC_BASE_URL` to the stub. Canary first. Record
  `claude --version` and `command -v claude` before the first run; bounded timeout, `Popen` streaming.
  Never `claude plugin eval`. Scratch under `<temp>/rigger-p3b/<task>/`.

**Downloads (Tasks 14 and 16)**
- Only the shellwise release assets named in `pin.json`, from
  `https://github.com/Borcioo/shellwise/releases/download/v<ver>/<asset>`, into a new empty folder;
  never run before the hash matches. Tests use a local `http.server` on `127.0.0.1` and archives built
  in the test; no test reaches the network.

**Docs and sweep**
- Before committing a doc, `python -I scripts/sweep.py --no-denylist <file>` prints
  `# files 1, findings 0`, and `python -I -X utf8 tests/test_plugin_refs.py` passes. Result files
  write temp paths as `<temp>` and the home as `<home>`.

## Behaviour changes

The release notes list each one.

1. The `shell-guard` slot defaults to the shellwise module on every system (D75). Its first session
   downloads the pinned binary; until it is ready, and on a system with no asset, commands run
   unguarded and the session gets one line (D76).
2. Commands are checked by shellwise rules, not the Python guard: 8 rules raised by the shipped
   config, `git-no-verify` on as a policy; `bash-ps-call-operator` and the Bash-tool `tee … | head`
   check are gone until a pin bump (D77). The allow marker is `# shellwise: allow`; the old marker is
   not accepted.
3. macOS and Linux get a shell guard for the first time.
4. The commit shadow row is written by its own handlers, also for a command the guard denies; its
   switch is `TEST_EVIDENCE=off` (D79). Shadow rows and gate-lab data start in the testing module's
   data folder; old rows stay where they were.
5. `mem-check` runs on all three systems and reads `load.min_free_gb` and `load.max_heavy_agents`
   (D80).
6. `safe-edit` is `python safe_edit.py` with the same options, exit codes and messages (D81); the
   rules name it through a placeholder.
7. rigger-windows is gone from the marketplace; while it is still enabled every session gets one
   line (D84).
8. `/rigger-core:coordinate` runs one session per lane with standby sessions (D86).
9. The state file's work items are changed through `state.py`; the state folder is cleaned and its
   logs rotated; a session start names stale items (D87, D88).
10. A side session's turn end is held while its own background shell task runs (D89).
11. A command reviewer's `{scope}` carries `review.paths` results (D90); the review and testing
    modules say when rigger-core is missing (D91).

## Baselines measured for this plan

Every number comes from a command run for this plan on `1ead09a`, Windows 11, CLI 2.1.327 for the
stub runs, unless named.

```text
$ gh release view -R Borcioo/shellwise --json tagName,assets
v0.1.0: shellwise-v0.1.0-{aarch64-apple-darwin,x86_64-apple-darwin,x86_64-unknown-linux-gnu}.tar.gz, shellwise-v0.1.0-x86_64-pc-windows-msvc.zip, each with .sha256
$ gh api repos/Borcioo/shellwise/git/trees/v0.1.0?recursive=1 | grep -c docs/rules/
53   (no bash-ps-call-operator.md; the rule map's 23 mapped ids were probed on a later branch head)
$ git ls-files plugins/rigger-windows | wc -l; sum of wc -l
56 files, 16,793 lines
$ git grep -c rigger-windows   (outside the plugin, live files)
197 lines in 42 files; plus 124 in history docs and CHANGELOG, 6 in the frozen golden fixture
$ wc -l (rigger-windows parts that move)
shell_guard.py 3352 (commit rule 2926-3318; its closure 1,467 lines), stop_shadow.py 262, turn_evidence.py 645,
test_commit_gate.py 1323 (rule half 212-981), test_stop_shadow.py 1008, test_turn_evidence.py 1814,
gate-lab 2166 (6 files), drift-lint 1594 (4 files), skills 469 (5 files), rules/windows.md 9, evals 80 (10 files)
$ wc -l (rewritten)
mem-check.ps1 126 (+ tests 211, 13 cases), safe-edit.ps1 189 (+ tests 185, 15 cases), lessons_guard.py 73
$ rigger-windows hooks.json handlers
10: SessionStart core_check; PreToolUse PowerShell --pwsh-all; 7 Bash (--only-git x3, --only-narrow x4); Stop stop_shadow (async)
$ coordinate.md 142 lines; steps 2 = 39-47, 3 = 48-64, 4 = 65-82
$ the source harness commit for the lane rule: 4 files, +129 -23 (coordinate.md, orchestrating-subagents SKILL.md, cli-host.md, test_coordinate_wiring.py 23 -> 32 tests)
$ coord_state.py 374 lines (tests 600 + 172 + 153); a move into started needs every waits_on part merged (:255-259)
$ state-file.md 16 lines; compact_guard.py 374 (reads safe_to_compact and coordinator; staleness by mtime, STALE 30 min); nothing cleans <config dir>/state/
$ Stop handlers: rigger-core side_session_guard.py (sync, timeout 10), report_shadow.py (SubagentStop, async), rigger-windows stop_shadow.py (async)
$ stub spike (10 isolated runs): Stop stdin background_tasks = [{"id","type":"shell","status":"running","description","command"}] while a task runs, [] otherwise; output file trailer "[exited with code N]" or "[killed]"; 3 blocks in a row < 1 s; CLAUDE_CODE_CHILD_SESSION=1 set for every hook
$ test_review_scope.py 1502 lines, 57 tests, 55 with a git init in setUp; no TIMEOUTS entry (120 s)
$ test_slot_run.py: 49 tests, 70-114 s per run (15 runs, 0 failures; 5 under load); limit 120 s
$ test_report_shadow.py: 55 tests, about 15 s; 33 runs, 0 failures (20 alone, 13 under load)
$ tests/read_by.json: 28 entries, 9 of them "*"
$ core_check.sh: 5 byte-identical copies (lessons, rules, talk, windows, wp), plugin name as $1
$ scripts/run_tests.min: 79
```

The WinError 32 cleanup error of `test_slot_run.py` (one in three runs during 3a planning) and the
`test_report_shadow.py` flake did not reproduce in 48 runs; Task 11 instruments them (D92).

## Review Focus

1. **A hash mismatch, a truncated download, an archive with a `../` member or a link, a second
   session downloading at the same time.** A person expects no file run, the slot unfilled with the
   reason, and the next session to try again. Test: Task 16,
   `test_only_a_verified_member_is_placed` and `test_two_sessions_share_one_download`.
2. **The binary gone after a session start** (deleted, antivirus). A person expects commands to run
   with no output, not every command denied. Test: Task 17,
   `test_launcher_allows_silently_without_a_binary`.
3. **An override path to an old or broken shellwise.** A person expects the slot unfilled with the
   version found, never a binary below the pin running. Test: Task 16,
   `test_override_below_the_pin_is_refused`.
4. **`state.py clean` next to a live session whose state file is old but whose transcript is
   fresh.** A person expects nothing of it moved. Test: Task 3, `test_clean_never_touches_a_live_session`.
5. **A side session with a gate running in the background that ends its turn.** A person expects the
   turn held with a reason naming the task, and never a loop with no end. Test: Task 5,
   `test_block_names_the_task_and_stops_after_three`.
6. **A diff that names a new `review.paths` rules file that is a link to a file outside the
   repository.** A person expects it not handed to the reviewer. Test: Task 9,
   `test_newly_named_link_is_not_handed_over`.
7. **rigger-windows still enabled after the update.** A person expects one line naming the disable
   command and the double-filler line, not two guards in silence. Test: Task 27,
   `test_enabled_rigger_windows_gets_one_line`.
8. **A `live:<rule>` lesson for an old guard id.** A person expects it checked under the new id, or
   reported `no checker`, never dropped. Test: Task 18, `test_old_ids_route_through_aliases`.

---

## Decisions this plan makes

3a used D54–D73.

| # | Decision | Why |
|---|---|---|
| D74 | Three pull requests: A (Tasks 1–13) harness and backlog; B (14–22) new code beside rigger-windows; C (23–32) moves, removal, migration. One release after C. | A touches no plugin a person installs new. B adds the shellwise module while rigger-windows still fills the slot, so B is revertible alone. C changes what is installed (decisions doc D1). |
| D75 | `rigger-defaults.json` `shell-guard` becomes `{kind: hook, target: rigger-shellwise}` with no `requires` in Task 27; until then rigger-windows stays the default and the shellwise module is a second filler (doctor's double-filler warning, accepted between B and C, never released). | One switch, in the PR that removes the old filler. |
| D76 | The installer of design §1: detached download from the SessionStart handler, pinned SHA-256 in `pin.json`, one expected member unpacked, `--version` check, atomic placement, `current` file, `status.json`, a lock with a 10-minute takeover, `shell_guard.shellwise_path` active at level 3 with the same version check. No Python fallback guard. | Decisions doc D2; design approach A. |
| D77 | Pin `v0.1.0`. The pin bump is one edit of `pin.json` (version, four names, four hashes) plus `RULES` in the rule checker, both checked by `test_pin.py` against the release's `.sha256` files in a manual step written into `CONTRIBUTING.md` ("Updating shellwise"). shellwise PR #5's two rules arrive with that bump. | shellwise PR #5 is not merged; the plan pins what is released. |
| D78 | `shellwise.toml` is the delivered integration config with `gate_commands = ["safe-edit*", "safe_edit*"]`; Task 14 proves on `v0.1.0` that every key is accepted (no `config-invalid`), that each raised rule fires, and which rule-map rows differ from the later head. A key `v0.1.0` refuses is dropped and listed in the release notes. | The rule map was probed on a later head. |
| D79 | The commit shadow rule moves with its closure (design approach B): `git mv` of `shell_guard.py` to `plugins/rigger-TESTING/tools/test-evidence/commit_shadow.py`, deny half deleted, entry `main` reduced to `log_commit`. Handlers `PreToolUse` `Bash` `if Bash(*commit*)` and `PowerShell` `if PowerShell(*commit*)`, timeout 10, `|| true`. `ALLOW_MARKER` becomes `shellwise: allow`. Switch `TEST_EVIDENCE=off`. | Keeps the measurement gate-lab replays; moved lines are not new code. |
| D80 | `mem_check.py` per design §3; `load.min_free_gb` and `load.max_heavy_agents` in `config.py` (levels 1–4, `ACTIVE_ABOVE_L1`), defaults in `rigger-defaults.json` (Windows: today's `min(30 GiB, 25% of the ceiling)` when the key is absent; Linux and macOS: 4 GiB; heavy agents 2). Task 20 Step 1 spikes the Windows reading without `pwsh` (`ctypes` `GetPerformanceInfo` and the Event 2004 read) against today's `.ps1` output on this machine. | Decisions doc D3. |
| D81 | `safe_edit.py` keeps every option as a long flag (`--path --find --replace --count --run --expect-ran --working-directory --restore`), exit codes 0–4 and the messages, em dash included; `--run` runs through the platform shell (`pwsh -NoProfile -Command` on Windows when present, else `sh -c`), recorded in the backup file. The 15 cases become `unittest` tests. | Decisions doc D4; the shellwise `gate_commands` glob needs the file name. |
| D82 | drift-lint moves to `plugins/rigger-core/tools/drift-lint/` unchanged, then gains one check, `trigger-load`: a trigger row's `load` names a skill that no module has. Its baseline rows are re-keyed in the task that moves the skills (Task 26). | Spec §13 row. |
| D83 | The platform module: skills, rule, evals, triggers and `core_check.sh` of design §2; trigger ids `rigger-platform.<skill>`; `evals/CASES.md` section renamed; the redundant-prose survey's "can be dropped" rows cut, each cut listed in the task report with its probe at the shipped config. | Spec §13 rows, design §2. |
| D84 | `harness_start.py` prints `rigger: rigger-windows is still enabled; disable it (claude plugin disable rigger-windows@<marketplace>)` once per startup while `installed_plugins.json` lists it enabled. Doctor's line 495 becomes a recommendation of the platform module and a `shell-guard` filler. | Spec §13 migration. |
| D85 | The five `tests/contract/shell-guard/` fixtures move to the shellwise module in Task 15; `test_module_contracts.py` runs them against the shellwise launcher with a stub binary. | They follow the filler. |
| D86 | coordinate: port the source change with this repository's words, plus three fixes (design §7). The heavy-job sentence stays inside the one `mem-check` bullet. | Owner's scope item 1. |
| D87 | `state.py` per design §8; its SessionStart `check` and `clean` run in one new handler `state.py hook` on `startup|clear|compact` (`clean` only on `startup`), timeout 10; `DuplicateHookTests` counts rise by one. | One process; the existing handlers belong to other tools. |
| D88 | `state.py check` reads PR state with `gh` only, 3 s per call and 5 s per run, caching only `MERGED`; it never fails the session start. | Offline and `gh`-less machines must not slow or break startup. |
| D89 | The turn-end guard joins `side_session_guard.py`'s Stop path (design §9); side session = the marker Task 1 measures; with no separating marker, every session. Poll bound 6 s, at most 3 blocks per task id (cached in the guard's existing cache file). | Design approach C. |
| D90 | `slot_run.py` passes `--session` to `review_scope.scope()` (its own `--session` argument, else `CLAUDE_CODE_SESSION_ID`). | 3a CHANGELOG known limit. |
| D91 | The review module gets `hooks/hooks.json` with one `SessionStart` handler `sh core_check.sh rigger-review`; the testing module adds `core_check.sh rigger-testing` to its file. Both `core_check.sh` copies byte-identical to the five others. | Owner's scope item 4; core_check prints nothing when rigger-core is present. |
| D92 | WinError 32 and the `test_report_shadow.py` flake did not reproduce (48 runs). Task 11 adds a diagnostic to `test_slot_run.py`'s cleanup (on `PermissionError`: the path, the alive child processes of the test process, one retry after 0.5 s, and the error re-raised with that text) and splits `test_slot_run.py` (70–114 s against 120 s). No product change without a reproduction. | A fix without the failure is a guess; the time limit is a measured risk. |
| D93 | Read-by refinement: each of the nine `*` entries whose file the lint derives as reading a finite set of modules gets that set; `*` stays only for a file that iterates `plugins/*`. | Fewer tests per module change, the same coverage. |

## Rulings on the plan's questions

None yet; the coordinator's rulings go here.

## Spec corrections

Line numbers that moved since the spec was written (the design uses the new ones): `hooks.json:20-77`
is `:11-69`; `run_tests.py:89` is `:118`; `CONTRIBUTING.md:36` is `:37` and `:86-90` is `:109-113`;
`doctor.sh:489` is `:495`; `tests/test_repo.py:270` is `:376-385` and `:101` is `:197-202`;
`curate-lessons/SKILL.md:92-99` is `:94-104`; `orchestrating-subagents/SKILL.md:47-51` is `:48-52`.
`rules/windows-skills.md` no longer exists (its rows are triggers in `rigger-module.json`). Task 30
writes these into the spec.

## File map

| Path | Task | Responsibility |
|---|---|---|
| `docs/superpowers/spikes/phase-3b/side-session-marker.md` | 1 | the side-session marker and `background_tasks` on the current CLI |
| `plugins/rigger-core/commands/coordinate.md`, `skills/orchestrating-subagents/SKILL.md`, `tools/coord-state/cli-host.md`, `tools/coord-state/tests/test_coordinate_wiring.py`, `tests/test_core_wiring.py`, `plugins/rigger-core/README.md:52-54` | 2 | lane rule |
| `plugins/rigger-core/tools/state/state.py`, `tools/state/tests/test_state.py` | 3 | state tool |
| `plugins/rigger-core/hooks/hooks.json`, `plugins/rigger-core/rules/state-file.md`, `tests/test_repo.py` (hook counts), `scripts/hook_latency.py` `CASES`, `tests/test_hook_latency.py` | 4 | state wiring and rule |
| `plugins/rigger-core/tools/side-session-guard/side_session_guard.py`, its tests | 5 | turn-end guard |
| `plugins/rigger-core/tools/slot-run/slot_run.py`, `tests/test_slot_run.py` (session rows only) | 6 | `--session` |
| `plugins/rigger-REVIEW/hooks/hooks.json` (new), `hooks/core_check.sh` (new copy), `plugins/rigger-TESTING/hooks/hooks.json`, `hooks/core_check.sh`, `tests/test_repo.py`, `tests/test_shellcheck.py`, `plugins/rigger-rules/tests/test_core_check.py`, `hook_latency.py` `CASES` | 7 | core_check |
| `plugins/rigger-core/tools/review-scope/tests/test_review_scope*.py`, `tests/read_by.json` (keys) | 8 | split |
| `review_scope.py`, one new test file of Task 8's split | 9 | newly named rules file |
| `plugins/rigger-core/tools/assemble/assemble.py:992-994`, `test_assemble.py` row | 10 | doctor INFO row |
| `test_slot_run.py` (cleanup and split), `scripts/run_tests.py` `TIMEOUTS` if needed | 11 | diagnostic, split |
| `tests/read_by.json`, `tests/test_read_by.py` | 12 | narrowing |
| `scripts/run_tests.min`, PR A body | 13 | gate A |
| `docs/superpowers/spikes/phase-3b/shellwise-v0.1.0.md` | 14 | pin facts |
| `plugins/rigger-SHELLWISE/{.claude-plugin/plugin.json, rigger-module.json, README.md, pin.json, shellwise.toml, bin/py.sh, tests/contract/shell-guard/*}`, `.claude-plugin/marketplace.json`, `tests/test_repo.py` (listing) | 15 | skeleton |
| `plugins/rigger-SHELLWISE/tools/install/shellwise_install.py`, its tests | 16 | installer |
| `plugins/rigger-SHELLWISE/bin/shellwise.sh`, `hooks/hooks.json`, launcher tests, `tests/test_module_contracts.py`, `tests/test_repo.py` (counts), `hook_latency.py` `CASES` | 17 | launcher, wiring |
| `plugins/rigger-SHELLWISE/tools/rule-checker/rule_checker.py`, its tests, `plugins/rigger-lessons/tools/lessons_lint.py:55`, `curate-lessons/SKILL.md:94-104`, `templates/archive/README.md`, `token-audit/tests/test_archive.py` | 18 | rule checker |
| `plugins/rigger-core/tools/assemble/{config.py, slots.py, assemble.py (_doctor)}` and tests | 19 | override active, doctor rows |
| `plugins/rigger-core/tools/mem-check/` (new), `config.py` `load`, `rigger-defaults.json` `load`, `scripts/run_tests.py:118` | 20 | mem-check |
| `plugins/rigger-TESTING/tools/safe-edit/` (new) | 21 | safe-edit |
| `scripts/run_tests.min`, PR B body | 22 | gate B |
| `plugins/rigger-TESTING/tools/test-evidence/` (moved), `plugins/rigger-TESTING/hooks/hooks.json`, `tests/test_repo.py` (`HAND_MADE_HOOKS`, counts), `scripts/sweep-allow.txt` (turn_evidence rows), `tests/read_by.json`, `run_tests.py` `TIMEOUTS`, `hook_latency.py` `CASES` | 23 | test-evidence |
| `plugins/rigger-TESTING/tools/gate-lab/` (moved), `CLAUDE.md:21` | 24 | gate-lab |
| `plugins/rigger-core/tools/drift-lint/` (moved), `CONTRIBUTING.md:37`, `tests/read_by.json`, `scripts/sweep-allow.txt` (10 rows) | 25 | drift-lint |
| `plugins/rigger-PLATFORM/**` (new; moved skills, rule, evals), `evals/CASES.md`, drift baseline rows | 26 | platform |
| `plugins/rigger-windows/**` (deleted), `rigger-defaults.json:9`, `marketplace.json`, `harness_start.py`, `doctor.sh:495`, the test files of map §8.2 (`test_repo.py`, `test_assembly_golden.py`, `golden_baseline.py`, `test_slots.py`, `test_render.py`, `test_stub_session.py`, `test_config.py`, doctor tests and harness, `test_plugin_rules.py`, `test_core_check.py`, `test_reinject.py`, `test_shellcheck.py`, `test_module_contracts.py`, `startup_bench.py`), `scripts/sweep-allow.txt` (31 `test_rules.py` rows), `run_tests.py:101,118,119` | 27 | removal |
| `plugins/rigger-rules/rules/{tests.md:13, defects.md:37}`, `test_plugin_rules.py:349-355`, `testing/SKILL.md:85-87`, `implementer-brief.md:61-69`, `orchestrating-subagents/SKILL.md:48-52`, `e2e-playwright-speed/SKILL.md:152`, `git-workflow/SKILL.md:21-22`, `stack-tooling/SKILL.md:72-73`, `test_core_wiring.py:199-208` | 28 | wording |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` (shellwise and guard cases), `docs/superpowers/spikes/phase-3b/exit-criteria.md` | 29 | stub exit criteria |
| `CHANGELOG.md`, `README.md`, module READMEs, `CONTRIBUTING.md`, the spec file | 30 | docs, migration |
| `scripts/run_tests.min`, PR C body | 32 | gate C |

## Lanes

A lane is a chain where each part needs the one before. Parts in different lanes may run at once;
the "Shared files" column names the registries two lanes both touch, which the coordinator merges in
the order given.

| Lane | Parts (in order) | Model | Heavy | Shared files |
|---|---|---|---|---|
| A1 coordinate | 2 | opus | light | `test_core_wiring.py` (before 28) |
| A2 state | 3 → 4 | opus, sonnet | light | `hooks.json` (core), `test_repo.py`, `hook_latency.py` |
| A3 turn-end | 1 → 5 | opus, opus | heavy (1), light (5) | — |
| A4 review scope | 8 → 9 | sonnet, opus | light | `read_by.json` |
| A5 slot run | 6 → 11 | sonnet, sonnet | heavy (11: suite > 2 min) | `run_tests.py` `TIMEOUTS` |
| A6 small | 7, 10, 12 | sonnet | light | `test_repo.py`, `hook_latency.py` (7 after 4), `read_by.json` (12 after 8) |
| A gate | 13 | sonnet | heavy | — |
| B1 shellwise | 14 → 15 → 16 → 17 → 18 → 19 | opus (14, 16, 17, 18), sonnet (15, 19) | heavy (14), light (15–19) | `marketplace.json`, `test_repo.py`, `hook_latency.py`, `config.py` (19 after 20) |
| B2 mem-check | 20 | opus | light (Windows spike in Step 1) | `config.py`, `rigger-defaults.json` |
| B3 safe-edit | 21 | sonnet | light | — |
| B gate | 22 | sonnet | heavy | — |
| C1 test tools | 23 → 24 | opus, sonnet | light | `hooks.json` (testing), `test_repo.py`, `read_by.json`, `sweep-allow.txt` |
| C2 drift and platform | 25 → 26 | sonnet, sonnet | light | `read_by.json`, `sweep-allow.txt`, `evals/CASES.md` |
| C3 removal | 27 → 28 → 29 → 30 | opus, sonnet, opus, sonnet | light, light, heavy, light | everything above; starts after C1 and C2 merge |
| C gate | 31 (release review), 32 | opus, sonnet | heavy | — |

Order of merges into each integration branch where two lanes share a file: A2 before A6's Task 7
(both change hook counts); A4's Task 8 before A6's Task 12; B2 before B1's Task 19 (`config.py`);
C1 before C2 (`read_by.json`, `sweep-allow.txt`). The proposed lane count at once: A 6, B 3, C 2
then 1.

---

## Pull request A: lanes, state, turn end and the backlog

### Task 1: Spike — the side-session marker and `background_tasks` on the current CLI

**Heavy, opus.** Inputs: the stub spike's result (Baselines). Output: the marker D89 uses.

**Files:** Create `docs/superpowers/spikes/phase-3b/side-session-marker.md`.

- [ ] **Step 1:** Record `claude --version` of the CLI on PATH and the version the desktop app runs (`CLAUDE_CODE_EXECPATH`).
- [ ] **Step 2:** Repeat the stub spike's run with one background `sleep` on the current CLI: `background_tasks` shape while running and after; control with no task.
- [ ] **Step 3:** The stub spike showed a hook inherits `CLAUDE_CODE_HOST_SESSION_ID` unchanged from the session's environment, so the session's own shell shows what its hooks see: record `printenv CLAUDE_CODE_HOST_SESSION_ID CLAUDE_CODE_ENTRYPOINT` from the Bash tool of a top-level desktop session (the coordinator's) and of a side session it starts, with no hook and no settings change. Known answer for the side session: a `local_<uuid>` value (this plan's planning session showed one). The marker is usable only if the top-level value is absent or differs in a way the side session's is not.
- [ ] **Step 4:** Write the result file (`## Question`, `## CLI`, `## Setup`, `## Control`, `## Observed`, `## Decision`), naming the marker or "none"; sweep; commit `docs(spikes): record the side-session marker and the Stop event's background tasks`.

### Task 2: coordinate — one session per lane

**Light, opus.** Inputs: the source harness commit (read with `git show` on a local clone of the
source harness, read-only; its plugin names are written `<prefix>-core` in any note). Output: the
three text files and their pins.

**Files:** Modify `plugins/rigger-core/commands/coordinate.md`, `skills/orchestrating-subagents/SKILL.md` (lines 27-28 and the heavy-job bullet 48-56), `tools/coord-state/cli-host.md:47-49`, `tools/coord-state/tests/test_coordinate_wiring.py`, `plugins/rigger-core/tests/test_core_wiring.py` (only if a pinned sentence moves), `plugins/rigger-core/README.md:52-54`.

- [ ] **Step 1: Port the ten tests** with this repository's words ("user", "integration branch", `RIGGER_ORCHESTRATING_MD` override next to `RIGGER_COORDINATE_MD`), remove `test_split_step_runs_heavy_parts_two_at_once_and_groups_light_ones`, and add three: `test_step_two_has_lane_start_and_load_paragraphs` (three paragraphs opening with the words the text uses), `test_a_lanes_next_part_waits_for_the_merge` (step 2 names the merge and `coord_state` refusing an earlier start), `test_one_owner_of_the_full_gate` (step 3 and step 4 both say the side session runs the full gate on its branch before it reports and the coordinator once per merge point; neither says "the gate are yours, at checkpoints").
- [ ] **Step 2: Run** the wiring tests. Expected: the new ones `FAILED` on their assertions.
- [ ] **Step 3: Write the text.** Step 2 as three paragraphs (lanes; starting every session in the user's turn with one spare standby per model, standby brief and close; load: no session cap of our own, heavy jobs (WSL, Docker, wp-env, e2e) at most 2 across lanes, the coordinator counts them). Step 3's gate sentence replaced; step 4 unchanged in meaning. The skill's heavy-job sentence goes inside the one bullet that names `mem-check`.
- [ ] **Step 4: Run** the wiring tests, `test_core_wiring.py`, `test_plugin_rules.py`, the drift-lint test. Expected: `OK`.
- [ ] **Step 5: Red runs:** restore the old step 2 (four new tests red), drop the merge sentence, put "the gate are yours" back, add a second `mem-check` bullet (`LoadRuleTests` red).
- [ ] **Step 6: Commit.** `feat(rigger-core): coordinate opens a session per lane and caps only heavy jobs`

### Task 3: The state tool

**Light, opus. Full risk (data deletion).** Output: `state.py` with the interface below.

**Files:** Create `plugins/rigger-core/tools/state/state.py`, `plugins/rigger-core/tools/state/tests/test_state.py`.

**Interfaces:**
- CLI: `state.py [--state-dir D] [--session ID] <command>`; session from `CLAUDE_CODE_SESSION_ID`; state dir from `CLAUDE_CONFIG_DIR` (else `~/.claude`) `/state` as `context_guard.default_state_dir` does (import it through the plugin root, not a relative sibling path outside the plugin).
- Commands of design §8: `add`, `set`, `note`, `done`, `show`, `check [--now ISO]`, `clean [--now ISO]`, `hook` (reads the SessionStart stdin: `source` `startup` runs `check` then `clean`, others `check`; prints at most 6 lines; always exits 0).
- Item line regex and statuses of design §8; every refusal `state: refused: <reason>`, exit 2.

- [ ] **Step 1: Write the failing tests** (fixtures: a state dir with a live session (fresh transcript under a fake `projects/` dir, old state file), a dead one (all files 20 days old), an archived one at 70 days, a `.log.md` of 60 kB):
  `test_add_set_note_done_round_trip_keeps_other_sections_byte_for_byte`, `test_done_moves_detail_to_the_log`, `test_refusals` (bad id, unknown id, unknown status, `done` without `--pr`, a second `## Items`), `test_check_flags_merged_pr_deleted_branch_and_stale_false` (stub `gh` on `PATH` printing `MERGED`; a branch not in the temp repo; `safe_to_compact: false` with `updated` 31 minutes old; positive control: each fires on its fixture and nothing fires on a clean file), `test_check_is_bounded_and_offline_safe` (a `gh` that sleeps 10 s: the run ends within 6 s with one `unchecked` line), `test_clean_moves_idle_sessions_and_deletes_after_sixty_days`, `test_clean_never_touches_a_live_session`, `test_clean_never_touches_the_current_session`, `test_log_rotation_keeps_the_newest_lines`, `test_writes_are_atomic` (a write interrupted after the temp file leaves the old file intact).
- [ ] **Step 2: Run.** Expected: `FAILED` / `ImportError` on the new module.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: `OK`, with `# clean: moved <n>, deleted <m>` lines in the clean tests.
- [ ] **Step 5: Red runs (full):** each clean rule off by one day; the live check removed; the current-session check removed; rotation keeping the oldest half; `done` leaving the detail; a write that rewrites the whole file through `str` (byte test red on a CRLF section); `gh` bound removed (bounded test red); cache of a non-merged state.
- [ ] **Step 6: Commit.** `feat(rigger-core): a state tool for work items, folder cleanup and log rotation`

### Task 4: State wiring and the rule

**Light, sonnet.** Inputs: Task 3.

**Files:** Modify `plugins/rigger-core/hooks/hooks.json` (one SessionStart handler `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/state/state.py" hook || true`, matcher `startup|clear|compact`, timeout 10), `plugins/rigger-core/rules/state-file.md`, `tests/test_repo.py`, `scripts/hook_latency.py` `CASES`, `tests/test_hook_latency.py`.

- [ ] **Step 1:** Tests first: `DuplicateHookTests` count +1; a `test_core_wiring.py`-style pin that `state-file.md` names `state.py` for items and keeps the 9 000-character and Next-step rules; a hook-latency case.
- [ ] **Step 2:** Red, implement, green; run `test_plugin_rules.py` and the drift-lint test.
- [ ] **Step 3:** Measure the handler with `hook_latency.py` on Windows; record median and p95 in the report.
- [ ] **Step 4: Commit.** `feat(rigger-core): run the state check at session start and point the rule at the tool`

### Task 5: The turn-end guard for side sessions

**Light, opus. Full risk.** Inputs: Task 1's marker.

**Files:** Modify `plugins/rigger-core/tools/side-session-guard/side_session_guard.py`, its tests.

- [ ] **Step 1: Write the failing tests** (Stop stdin fixtures from the spike; output files written by the test):
  `test_running_shell_task_blocks_with_its_id_and_description`, `test_task_that_ends_during_the_poll_allows` (the file gets `[exited with code 0]` after 1 s), `test_killed_trailer_allows`, `test_subagent_entries_are_ignored`, `test_block_names_the_task_and_stops_after_three` (`stop_hook_active` true, same id: blocks 2 and 3, then allows with a line), `test_poll_is_bounded` (never-ending task: the hook returns within 7 s), `test_top_level_session_is_not_held` (per Task 1's marker; skipped with the reason "no marker" only if Task 1 found none), `test_missing_field_or_file_allows`, plus a control: the existing guard's tests still pass.
- [ ] **Step 2:** Red; implement; green.
- [ ] **Step 3: Red runs (full):** no poll (the ending-task test red), no cap, cap per session instead of per task, a subagent counted, the marker check removed.
- [ ] **Step 4: Commit.** `feat(rigger-core): hold a side session's turn end while its own background task runs`

### Task 6: `slot_run` passes the session

**Light, sonnet.** **Files:** `plugins/rigger-core/tools/slot-run/slot_run.py`, `tests/test_slot_run.py` (one test class).

- [ ] **Step 1:** Test `test_scope_file_carries_review_paths_of_the_session` (a level-3 skip from a session snapshot appears in `{scope}`), red, implement (D90), green, one red run (session dropped). Commit `fix(rigger-core): slot_run reads review.paths for the session`.

### Task 7: core_check in the review and testing modules

**Light, sonnet.** Inputs: Task 4 merged (hook counts). **Files:** of the file map row 7.

- [ ] **Step 1:** Tests: `PLUGINS_WITH_CORE_CHECK` gains both; `DuplicateHookTests` counts; `test_shellcheck.py` lists both; `test_core_check.py` runs each copy from a cache-like layout without rigger-core and expects its line, with rigger-core and expects nothing. `git add` the new `.sh` before the shellcheck run.
- [ ] **Step 2:** Red, copy `core_check.sh` byte for byte, add the handlers, green, one red run (handler without the plugin name). Commit `feat: the review and testing modules say when rigger-core is missing`.

### Task 8: Split `test_review_scope.py`

**Light, sonnet.** **Files:** `plugins/rigger-core/tools/review-scope/tests/test_review_scope.py` → files by class (names differ by more than case), `tests/read_by.json` keys.

- [ ] **Step 1:** Time the file alone three times; record. Split by class, each file under 400 lines; read-only classes share one repository from `setUpClass`. Every test kept (count before and after printed). Time each part three times: each under 50% of its limit. Commit `test(rigger-core): split the review-scope tests`.

### Task 9: A newly named rules file that is a link or over the cap

**Light, opus. Full risk (trust).** Inputs: Task 8. **Files:** `review_scope.py`, one test file of Task 8.

- [ ] **Step 1:** Tests: `test_newly_named_link_is_not_handed_over`, `test_newly_named_over_cap_is_not_handed_over`, `test_newly_named_regular_file_is_handed_over` (control). Red; implement (design §10: blob mode `120000` from `ls-tree`, size from `cat-file --batch-check`, the existing cap); green. Red runs: mode check off, size check off, `<` for `<=`. Commit `fix(rigger-core): a newly named rules file that is a link or too large is not handed over`.

### Task 10: Doctor's `rules-d-table` row names the cause

**Light, sonnet.** **Files:** `assemble.py:992-994`, its `test_assemble.py` row. One test, red, fix, commit `fix(rigger-core): doctor names the cause of the rules-d-table row`.

### Task 11: `test_slot_run.py`: cleanup diagnostic and split

**Heavy, sonnet.** Inputs: Task 6. **Files:** `test_slot_run.py`, `run_tests.py` `TIMEOUTS` only with a measured reason.

- [ ] **Step 1:** Add D92's diagnostic to the cleanup helper; a test that forces a `PermissionError` (an open handle held by the test on Windows; skipped elsewhere with its reason) sees the diagnostic text in the raised error.
- [ ] **Step 2:** Split the file by class until each part stays under 50% of its limit in three runs; record the times.
- [ ] **Step 3:** Run `test_report_shadow.py` 10 times under the gate's load; if it fails, record the assertion and stop for a ruling; if not, record the count. Commit `test(rigger-core): split the slot-run tests and explain a locked cleanup`.

### Task 12: Read-by narrowing

**Light, sonnet.** Inputs: Task 8. **Files:** `tests/read_by.json`, `tests/test_read_by.py`.

- [ ] **Step 1:** For each `*` entry print its derived reads; narrow per D93; a new test `test_wildcards_only_for_files_that_iterate_plugins`. Red run: widen one narrowed entry's file with a new module literal (declared-read test red). Commit `test: narrow the read-by wildcards`.

### Task 13: Gate and pull request A

- [ ] Set `run_tests.min`; full gate in the background; `--verify` (`(full)`); push; PR body with the mutation list per task and survivors, the state hook latency, Task 1's result. Review round; Tasks 3 and 5 get the second round (D65).

---

## Pull request B: the shellwise module, mem-check and safe-edit

### Task 14: Spike — the pinned release

**Heavy (downloads, WSL), opus.** Output: the numbers `pin.json` and `RULES` take.

**Files:** Create `docs/superpowers/spikes/phase-3b/shellwise-v0.1.0.md`.

- [ ] **Step 1:** Download the four archives and their `.sha256` files into a new empty folder; compute SHA-256 of each archive and compare with its file (known answer: the release's own files). Record names, sizes, hashes.
- [ ] **Step 2:** Unpack (Windows `.zip`; Linux `.tar.gz` under WSL); list members (expected `shellwise-v0.1.0-<target>/shellwise[.exe]` plus licence files); `--version` (expected `shellwise 0.1.0`).
- [ ] **Step 3:** `explain <id>` for the 53 documented ids (exit 0 each) and for `bash-ps-call-operator` (expected 64): the `RULES` list.
- [ ] **Step 4:** Rerun every probe of the rule map's Table 1 at defaults and with `shellwise.toml` (D78) on Windows (Git Bash and pwsh shells) and Linux; mark each row that differs from the map. Probe `python safe_edit.py --run x 2>&1 | Select-Object -First 30` with the config (expected break). Check the output has no `config-invalid`.
- [ ] **Step 5:** `hook claude-code` with a Claude Code PreToolUse stdin for one `break`, one `warn` and one clean command; record stdout and exit codes.
- [ ] **Step 6:** Write the result file; sweep; commit `docs(spikes): record the pinned shellwise release`.

### Task 15: The shellwise module's skeleton

**Light, sonnet.** Inputs: Task 14. **Files:** file map row 15.

- [ ] **Step 1:** Tests in `tests/test_repo.py` (listing, marketplace order, counts) and `tests/test_module_contracts.py` (manifest: `fills shell-guard`, `failure_mode closed`, `rule_checker` path); a `test_pin.py` that `pin.json` names four targets with 64-hex hashes and the version the spike recorded. Red, write the files (`git mv` the five contract fixtures), green. Commit `feat(rigger-shellwise): the module skeleton and the pin`.

### Task 16: The installer

**Light, opus. Full risk.** Inputs: Task 15. **Files:** `tools/install/shellwise_install.py`, its tests.

**Interfaces:** `resolve(data_dir, pin, override) -> Status`; `ensure(data_dir, pin, override, *, spawn=True) -> Status` (starts the detached download when needed); `download(data_dir, pin, target, base_url)` (the child's entry); `main` for `--session-start` (prints at most one line) and `--child`.

- [ ] **Step 1: Write the failing tests** (local `http.server`; archives built in the test):
  `test_target_table` (each row of design §1 and two with no asset), `test_good_archive_is_placed_and_current_written`, `test_hash_mismatch_deletes_and_fails`, `test_truncated_download_fails`, `test_only_a_verified_member_is_placed` (`../x`, absolute name, a symlink member, a second binary: each refused), `test_version_mismatch_after_unpack_fails`, `test_two_sessions_share_one_download` (lock; a lock 11 minutes old is taken over), `test_override_used_when_at_or_above_the_pin`, `test_override_below_the_pin_is_refused`, `test_override_that_hangs_is_refused_within_its_bound`, `test_pin_change_replaces_the_cached_binary`, `test_no_network_is_one_line`, `test_session_start_never_waits_for_the_download` (returns within 2 s while the server delays 10 s).
- [ ] **Step 2:** Red; implement; green.
- [ ] **Step 3: Red runs (full):** hash compare skipped; compare on the `.sha256` file instead of the pin; member filter off for each refused kind; lock ignored; takeover never; version check skipped; `current` written before the hash; override version compare as strings (`0.10.0 < 0.9.0`).
- [ ] **Step 4: Commit.** `feat(rigger-shellwise): download, verify and place the pinned shellwise`

### Task 17: The launcher and the hooks

**Light, opus. Full risk (wiring).** Inputs: Task 16. **Files:** file map row 17.

- [ ] **Step 1:** Tests: `test_launcher_execs_current_with_the_config` (stub binary prints its argv and stdin; exit code and stdout passed through), `test_launcher_allows_silently_without_a_binary` (no `current`; `current` naming a missing file; empty `current`), `test_launcher_paths_with_spaces`, `test_hooks_json_wiring` (SessionStart `startup|clear|compact` runs the installer `--session-start`; PreToolUse `Bash|PowerShell` runs the launcher, timeout 5, no `|| true`), contract fixtures pass against the stub, `DuplicateHookTests`, `test_shellcheck.py`, `hook_latency.py` `CASES` (the launcher's matching and non-matching cases).
- [ ] **Step 2:** Red; implement; green. Measure the launcher's added latency over the stub binary with `hook_latency.py` (report median and p95 against the `sh` floor).
- [ ] **Step 3: Red runs:** launcher without the `--config` argument; `|| true` added (contract deny fixture red); a missing `current` that exits 1.
- [ ] **Step 4: Commit.** `feat(rigger-shellwise): the hook launcher and session-start wiring`

### Task 18: The rule checker

**Light, opus.** Inputs: Task 14 (`RULES`), Task 17. **Files:** file map row 18.

- [ ] **Step 1:** Tests: `test_rules_match_the_pin` (against Task 14's list; with the real binary when `current` exists, else a recorded list), `test_old_ids_route_through_aliases`, `test_probe_denied_at_break_is_none`, `test_warn_only_is_a_problem`, `test_no_binary_is_no_checker`, `lessons_lint` finds `rule_checker.py` by glob and by `--rule-checker`; `test_archive.py` rewritten against a stub checker module (spec §13 row); curate-lessons text takes the checker from the shell-guard filler's manifest.
- [ ] **Step 2:** Red; implement; green; red runs (alias table empty; level compare `>=` warn). Run `test_plugin_rules.py` and the drift-lint test (skill text changed). Commit `feat(rigger-shellwise): a rule checker built on explain and check`.

### Task 19: Override active, doctor and slot rows

**Light, sonnet.** Inputs: Tasks 17 and 20 (`config.py`). **Files:** file map row 19.

- [ ] **Step 1:** Tests: `shell_guard.shellwise_path` leaves `inactive` and is read only at level 3; doctor `slot shell-guard` rows for each `status.json` state with failure mode `closed`; the session line forms of design §1. Red; implement; green; one red run per state row. Commit `feat(rigger-core): doctor and slot rows for the shellwise module`.

### Task 20: mem-check in Python

**Light, opus.** **Files:** file map row 20.

- [ ] **Step 1: Spike** (no commit): on this machine, the Windows reading by `ctypes` against `mem-check.ps1`'s line (committed, limit, available: equal within 1%), and the Event 2004 read without `pwsh` (or with `pwsh` when present, and the fallback line otherwise); record in the task report.
- [ ] **Step 2: Tests** (readings injected through a `--reading-json` test seam, as the `.ps1` has): the 13 `.ps1` cases ported; Linux `/proc/meminfo` fixtures (the smaller of the two counts); macOS `vm_stat` and `sysctl` fixtures; `load.*` config read and validated (out of range dropped with one notice); exit 2 and its line on an unreadable source; the live read on the running system (skipped with its reason only where the system has no reading tool).
- [ ] **Step 3:** Red; implement; green; red runs (boundary equal passes, one byte less fails; Linux taking the larger count; macOS without purgeable).
- [ ] **Step 4:** `run_tests.py:118` stays on the `.ps1` until Task 27 deletes it; add the new tests to discovery. Commit `feat(rigger-core): mem-check on every system`.

### Task 21: safe-edit in Python

**Light, sonnet.** **Files:** file map row 21.

- [ ] **Step 1:** Port the 15 cases as `unittest` tests (messages byte for byte, em dash included), plus `test_platform_shell_is_recorded_in_the_backup`. Red; implement D81; green; red runs (restore skipped on a failing command; hash check skipped; `--count` off by one; CRLF normalisation off). Commit `feat(rigger-testing): safe-edit in Python`.

### Task 22: Gate and pull request B

- [ ] Set `run_tests.min`; full gate; `--verify`; push; PR body with Task 14's result, the mutation list, the launcher latency. Review round; Tasks 16 and 17 get the second round.

---

## Pull request C: moves, removal and migration

### Task 23: test-evidence moves to the testing module

**Light, opus. Full risk (wiring).** **Files:** file map row 23.

- [ ] **Step 1:** `git mv` `shell_guard.py` → `tools/test-evidence/commit_shadow.py`, `stop_shadow.py`, `turn_evidence.py`, `tests/test_stop_shadow.py`, `tests/test_turn_evidence.py`, `tests/test_commit_gate.py`, `tests/rules_common.py` (commit first, unchanged, so history follows).
- [ ] **Step 2:** Delete from `commit_shadow.py` everything outside the closure (map §2.1), and from `test_commit_gate.py` lines 982-1290 (the bypass half); `main` calls `log_commit` only; D79's marker and switch. Count lines before and after.
- [ ] **Step 3:** Handlers in the testing module's `hooks.json` (D79 and the async Stop row); `HAND_MADE_HOOKS` fates, counts, `FAIL_OPEN_PLUGINS`, `TIMEOUTS` path, `sweep-allow.txt` turn_evidence rows, `read_by.json` key, `hook_latency.py` `CASES`; the "Shadow gates" section of `INSTALL.md` becomes `tools/test-evidence/README.md`; `CONTRIBUTING.md:109-113` points at it.
- [ ] **Step 4:** Tests: the moved suites pass; a wiring test runs both commit handlers' commands from the testing module's `hooks.json` on a commit in a temp repo with `.claude/gates.json` and finds one row; `TEST_EVIDENCE=off` writes none. Red runs: handler `if` text changed; marker text; the switch.
- [ ] **Step 5: Commit** in two commits (the move, then the pruning and wiring). `refactor(rigger-testing): move the test-evidence gates from rigger-windows`

### Task 24: gate-lab moves

**Light, sonnet.** Inputs: Task 23. `git mv` the six files; the three sibling-folder imports name `test-evidence`; `replay.py:3-5` names the testing module's data folder and `--data` accepts the old one; `CLAUDE.md:21` path. Tests pass; one red run (import path). Commit `refactor(rigger-testing): move gate-lab`.

### Task 25: drift-lint moves to rigger-core

**Light, sonnet.** `git mv` the four files; `CONTRIBUTING.md:37`, `read_by.json:30`, the 10 `sweep-allow.txt` rows; then D82's `trigger-load` check with a test and a planted control (a trigger loading a missing skill). Commit twice (move; check). `feat(rigger-core): drift-lint moves to core and checks trigger loads`

### Task 26: The platform module

**Light, sonnet.** Inputs: Task 25. **Files:** file map row 26.

- [ ] **Step 1:** Skeleton (manifest, `rigger-module.json` with the three triggers and the rule with `requires: os:windows`, README, marketplace entry, listing tests); `git mv` the skills, `rules/windows.md`, the evals, `core_check.sh`; trigger ids, loads and self-references renamed; `evals/CASES.md` heading; drift baseline rows re-keyed; herd-docs old path read for one release.
- [ ] **Step 2:** The redundant-prose cuts (D83), each listed with its probe result from Task 14.
- [ ] **Step 3:** `test_plugin_rules.py`, drift-lint, `eval_suite_check.py`, golden and assemble tests for the triggers. Commit `feat(rigger-platform): the Windows and toolchain skills, rule and evals`.

### Task 27: Remove rigger-windows and switch the default

**Light, opus.** Inputs: Tasks 19, 23–26 merged. **Files:** file map row 27.

- [ ] **Step 1:** Tests first: `test_enabled_rigger_windows_gets_one_line` (D84, `harness_start.py` with a fake `installed_plugins.json`; control: not listed, no line); doctor's recommendation row; the golden test's Windows layout without rigger-windows (`## Windows shell` now from the platform module); slot rows with the shellwise default on all three systems.
- [ ] **Step 2:** Delete the remaining files of the plugin (guard, its tests, `rules_start.sh`, manifests, `mem-check` `.ps1`, `safe-edit` `.ps1`); D75's default; every live reference of map §8 re-pointed or removed (the count goes from 197 to the history-only lines; print `git grep -c rigger-windows -- ':!docs' ':!CHANGELOG.md' ':!tests/fixtures'` = only the D84 notice, its test and the migration text).
- [ ] **Step 3:** Full `run_tests.py --fix-round`; then the WSL leg once. Commit `feat!: remove rigger-windows; shellwise fills the shell guard on every system`.

### Task 28: Wording that named rigger-windows

**Light, sonnet.** Inputs: Task 27. **Files:** file map row 28. Placeholders `{{tool:safe-edit}}` and `{{tool:mem-check}}` with `requires: plugin:rigger-testing` where the spec says; the frozen golden fixture stays. `test_plugin_rules.py`, `test_core_wiring.py`, drift-lint. Commit `docs: name the platform and testing modules instead of rigger-windows`.

### Task 29: Stub exit criteria

**Heavy, opus.** Inputs: Task 28. **Files:** file map row 29.

- [ ] **Step 1:** Stub sessions (one isolated config each) with rigger-core, rigger-testing and the shellwise module from `--plugin-dir`, the binary placed from Task 14's download into the scratch data folder: a `break` command denied (the stub answers with that `Bash` tool_use; the next request carries the deny), a `warn` command run with its note; binary removed: the session line and the command runs; `Hooks: PreToolUse:Bash` in the debug log for both modules; a commit row from the testing module; Task 5's guard holding a turn with a background `sleep` (`Stop` read back from the debug log). Doctor reports `closed`.
- [ ] **Step 2:** Write the result file labelled "stub"; commit `test(rigger-core): stub exit criteria for phase 3b`.

### Task 30: Docs, release notes, migration

**Light, sonnet.** `CHANGELOG.md` (the behaviour changes, the migration of design §6 with one install call per plugin, the shellwise pin and how to bump it), `README.md` tables, module READMEs, `CONTRIBUTING.md` ("Updating shellwise", the gates path), the spec corrections. Sweep each file. Commit `docs: phase 3b release notes and migration`.

### Task 31: Release review

**Heavy, opus.** The review module reviews PR C's diff with every in-scope file covered and every quote anchored (spec §13 exit criterion); findings go through a fix round.

### Task 32: Gate and pull request C

- [ ] Set `run_tests.min`; full gate; `--verify`; push; PR body with the mutation list, the stub result, the release review line. Review round; Tasks 23 and 27 get the second round. Merging and the release are the owner's steps.

---

## Left for later

- A shellwise pin bump to the release that carries shellwise PR #5 (`bash-ps-call-operator`, the
  wait-loop and follow bounds): one `pin.json` and `RULES` edit (D77).
- Linux arm64 and Windows arm64 assets: the shellwise owner's call.
- The hook-process merge, argv-hash pinning of `trust.project_commands`, a Go or Rust port: out of
  scope for phase 3.
- A WinError 32 fix, once Task 11's diagnostic catches one.

## Estimate

| Part | Agent hours | Code lines | Test lines |
|---|---|---|---|
| Spikes: side-session marker, pinned release (Tasks 1, 14) | 4.5 | 0 | — |
| coordinate lane rule (Task 2) | 2.5 | 60 (Markdown) | 150 |
| State tool and wiring (Tasks 3, 4) | 6 | 420 | 600 |
| Turn-end guard (Task 5) | 3 | 90 | 200 |
| Backlog (Tasks 6–12) | 9.5 | 130 | 325 |
| Shellwise module: skeleton, installer, launcher, rule checker, rows (Tasks 15–19) | 13 | 670 | 1,100 |
| mem-check (Task 20) | 3.5 | 260 | 330 |
| safe-edit (Task 21) | 3 | 220 | 300 |
| test-evidence and gate-lab moves (Tasks 23, 24) | 4.5 | 135 | 140 |
| drift-lint and the platform module (Tasks 25, 26) | 5 | 180 | 140 |
| Removal and wording (Tasks 27, 28) | 6 | 210 | 260 |
| Stub exit criteria, release review (Tasks 29, 31) | 5 | — | 200 |
| Docs, three gates and pull requests (Tasks 13, 22, 30, 32) | 6.5 | — | — |
| **Total** | **72** | **about 2,375** | **about 3,745** |

About 9,000 lines move with `git mv` and are not counted; about 4,500 lines are deleted (the deny
half of the guard, its tests, the `.ps1` files).

Against the decisions doc: its 42 h covered 3a and 3b, and 3a's plan priced 40.25 h, so the doc
left about 21 h of D2–D4 and D8 work for 3b. The same items price here at about 40 h (Tasks 14–28
without the backlog: the installer's trust checks, the commit-rule move the spec did not price, the
rule checker on `explain`/`check`, the platform module); the owner's added scope (coordinate, state,
turn-end, backlog) adds about 21 h, and the gates, spikes and stub runs about 11 h. Phase 2's plan
ran at 2.7 times the spec's hours and phase 3a's at 1.9.

**Simpler version, about 44 h, 1,450 code lines, 2,250 test lines:**
- **What it keeps:** PR A whole; mem-check in Python; safe-edit in Python; drift-lint to core; the
  platform module (rigger-windows keeps only the guard, the test-evidence gates and gate-lab).
- **What it drops, with what each saves:**
  - the shellwise module and its spike (Tasks 14–19, 16 h, 670 and 1,100 lines): no guard on macOS
    and Linux, the Python guard stays the Windows filler (the decisions doc's simpler version);
  - the test-evidence and gate-lab moves (Tasks 23, 24, 4.5 h): they stay in rigger-windows with the
    guard they live in;
  - the removal and the migration (Task 27 and half of 30, 5 h): rigger-windows stays one more
    release.

## Open risks

1. **The side-session marker may not exist** (Task 1). The guard then holds every session's turn end
   while its own background shell task runs; a top-level session loses the chance to end a turn
   early while a gate runs, which it rarely needs.
2. **`background_tasks` is an undocumented field.** A CLI that drops it makes the guard allow (fail
   open), and Task 5's test pins the field so the next CLI update shows it.
3. **The pinned rule set is older than the rule map.** Task 14 lists every row that differs; a weaker
   row is in the release notes until the pin bump.
4. **The first session after the update runs unguarded until the download ends.** It says so; the
   download is about 10 MB per asset.
5. **GitHub release downloads can be blocked** on a company network. The slot stays unfilled with the
   reason; the override names a binary installed another way.
6. **`gh` may be slow or absent.** `state.py check` is bounded and caches only merged PRs; without
   `gh` the PR check is skipped with one line.
7. **The commit row now logs denied commands too** (D79). gate-lab's numbers before and after the
   move are not comparable for commits the guard denied; the release notes say so.
8. **WinError 32 is not fixed**, only made explainable (D92).
9. **The removal touches 42 live files.** Task 27 runs after every other C lane merges, alone, so no
   other writer holds those files.
