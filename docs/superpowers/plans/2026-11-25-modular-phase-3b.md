# Modular architecture, phase 3b: shellwise, the platform module, mem-check and the removal of rigger-windows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pinned shellwise binary fills `shell-guard` on all three systems, the platform module
takes the Windows and toolchain knowledge, `mem-check` and `safe-edit` are Python tools for every
system, the test-evidence gates and gate-lab join the testing module, rigger-windows leaves the
marketplace; and the harness gets the lane rule, a state tool, a turn-end guard for side sessions
and the 3a backlog.

**Status:** revision 2, after an independent plan review (12 Important findings, fixed here; the
review's questions are under "Questions for the coordinator"). Written on `1ead09a` (release 0.7.0).

**Architecture:** Three merge points. PR A changes only rigger-core, the two 3a modules and shared
test infrastructure. PR B adds new code beside rigger-windows and changes none of its files. PR C
hands the shell guard over and empties rigger-windows in four steps, each of which leaves the
remaining plugin consistent (its manifest, `hooks.json` and README name only files it still has),
then deletes it and writes the migration. One release (0.8.0) after PR C.

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
- **A task that moves a file out of rigger-windows** removes every reference to it from
  rigger-windows' `rigger-module.json`, `hooks/hooks.json`, `README.md`, `tests.paths` and
  `scripts/run_tests.py` in the same commit, and runs the full `run_tests.py --fix-round` before it
  reports. Before the move it lists every reference to the moved path in the whole tree
  (`git grep -n <path>`), not only the plugin's own.
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
- `bin/py.sh` copies stay byte-identical (`tests/test_repo.py:197-202`); the shellwise module gets
  one (its SessionStart handler is Python) and joins `PLUGINS_WITH_PY_SH`; the platform module runs
  only `core_check.sh` and gets none.
- A path that only has to be absolute is made with `abspath`, never `Path.resolve()`.
- **A PreToolUse command must never exit 2 by accident.** Exit 2 denies the tool call. Every
  PreToolUse command of this plan guards a missing script (`[ -f <script> ] || exit 0` inside its
  `sh -c` string), and `tests/test_repo.py:427`
  `test_commands_with_a_missing_script_exit_zero` covers each new handler.

**Tests and runs**
- Unit tests of `tools/<tool>/<name>.py` live in `tools/<tool>/tests/test_<name>.py`. Every new test
  gets one red run on its own assertion (a new module is first written as stubs that return a wrong
  value, such as `None` or an empty result, so the red run fails on assertions, not on an import or
  an error), named in the task's report.
- A task that splits, moves or deletes test files prints the set of test ids before and after
  (`unittest` discovery `id()` list, sorted) and shows the sets equal, or lists each removed id with
  its reason.
- **Full-risk checks** (broken once per branch, table row, stage and wiring):
  - the shellwise installer: target table, hash check, archive member check, exec bit, lock,
    atomic placement, override version check, the `current` file (Task 16: a downloaded binary is
    run on every command);
  - the launcher and its wiring: missing launcher, missing or empty `current`, binary output passed
    through (Task 17);
  - the state tool's `clean` (live-session refusal, the 14- and 60-day rules, the restore on resume,
    the lock, log rotation keeping the newest lines) and every write keeping other sections byte for
    byte (Task 3: data deletion);
  - the turn-end guard's block, poll bound, loop cap, command filter and session filter (Task 5);
  - the newly-named rules file check (Task 9: trust);
  - the guard hand-over: the default switch, the moved commit and Stop shadow handlers' wiring
    (Task 23).
  Shared infrastructure (the state SessionStart wiring, `mem-check`, `safe-edit`, `core_check`, the
  read-by narrowing) gets one mutant per branch up to 5 per check, the rest listed in the report.
- Python mutants run with `-X pycache_prefix=<dir per mutation>` on a scratch copy without `.git`.
- `run_tests.py` strips `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`; a test that needs one sets
  it itself. A test that needs a repository builds its own with `git init` in a temp folder.
- Git Bash `sh.exe` started from Python re-parses its command line: no quote or glob characters as
  arguments, or put them inside an `sh -c` string.
- A change to a rules file, a skill, an agent or a command file runs
  `plugins/rigger-rules/tests/test_plugin_rules.py` and the drift-lint test (under rigger-windows
  until Task 24 moves it to rigger-core) in the same round as the edit.
- A task that adds or removes a hook handler runs `tests/test_repo.py` (`DuplicateHookTests`, the
  timeout pins at `:405-411`) and updates `scripts/hook_latency.py` `CASES` with
  `tests/test_hook_latency.py`.
- `scripts/run_tests.min` reads 79. Only the three gate tasks (13, 22, 31) set it, each to the count
  `run_tests.discover` prints at that commit.
- Gate: `python -I scripts/gate.py` in the background, started on Windows with the full path of the
  installed Python (not the WindowsApps alias). Read `status.txt`. Between fixes
  `python -I scripts/run_tests.py --fix-round` (`--no-claude` when no `claude` run is allowed).
  Before a pull request, `gate.py --verify`. Every pull request here holds a shared path, so each
  takes the full gate.

**Heavy and light** (for the coordinator's count of 2 heavy jobs)
- **Heavy:** the task runs the full gate or the full `run_tests.py --fix-round`, the WSL leg, a stub
  `claude` session, a download, or a suite file that takes more than 2 minutes.
- **Light:** the task writes files and runs the unit-test files it touches and the rules tests.

**Real `claude` runs (Tasks 1, 14, 28)**
- A fresh temp `CLAUDE_CONFIG_DIR` per experiment; `--plugin-dir` runs set
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to scratch, leave `RIGGER_LESSONS_BATCH`
  unset and name the same roots in `RIGGER_MODULE_DIRS`. The child drops `CLAUDE_*`, `ANTHROPIC_*`,
  `HARNESS_*` and `RIGGER_*`, then sets `ANTHROPIC_BASE_URL` to the stub. Canary first. Record
  `claude --version` and `command -v claude` before the first run; bounded timeout, `Popen` streaming.
  Never `claude plugin eval`. Scratch under `<temp>/rigger-p3b/<task>/`.
- Task 1 Step 3 is the one probe in the desktop app; it runs only after the coordinator's OK
  (question 1).

**Downloads (Tasks 14 and 28)**
- Only the shellwise release assets named in `pin.json`, from
  `https://github.com/Borcioo/shellwise/releases/download/v<ver>/<asset>`, into a new empty folder;
  never run before the hash matches. Unit tests use a local `http.server` on `127.0.0.1` and archives
  built in the test; no unit test reaches the network.

**Docs and sweep**
- Before committing a doc, `python -I scripts/sweep.py --no-denylist <file>` prints
  `# files 1, findings 0`, and `python -I -X utf8 tests/test_plugin_refs.py` passes. Result files
  write temp paths as `<temp>` and the home as `<home>`.

## Behaviour changes

The release notes list each one.

1. The `shell-guard` slot defaults to the shellwise module on every system (D75). Its first session
   downloads the pinned binary; until it is ready, and on a system with no asset, commands run
   unguarded and the session gets one line (D76).
2. Commands are checked by shellwise rules, not the Python guard. The shipped config sets 11 levels
   (8 warn-by-default rules raised to break, 3 mixed rules raised) and turns on `git-no-verify`;
   `bash-ps-call-operator` and the Bash-tool `tee … | head` check are gone until a pin bump (D77). The
   allow marker is `# shellwise: allow`; the old marker is not accepted by the guard.
3. macOS and Linux get a shell guard for the first time.
4. The commit shadow row is written by its own handlers, also for a command the guard denies; its
   switch is `TEST_EVIDENCE=off` (D79). Shadow rows and gate-lab data start in the testing module's
   data folder; old rows stay where they were.
5. `mem-check` runs on all three systems and reads `load.min_free_gb` and `load.max_heavy_agents`
   (D80).
6. `safe-edit` is `python safe_edit.py` with the same options as long flags, the same exit codes and
   messages (D81); the rules name it through a placeholder.
7. rigger-windows is gone from the marketplace; while it is still enabled every session gets one
   line (D84).
8. `/rigger-core:coordinate` runs one session per lane with standby sessions (D86).
9. The state file's work items are changed through `state.py`; idle sessions' state files are
   archived and later deleted, a resumed session gets its files back, logs rotate; a session start
   names stale items (D87, D88).
10. A side session's turn end is held while its own background gate or test command runs (D89).
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
shell_guard.py 3352 (commit rule 2926-3318; its name-reference closure 1,467 lines, by script), stop_shadow.py 262, turn_evidence.py 645,
test_commit_gate.py 1323 (rule half 212-981), test_stop_shadow.py 1008, test_turn_evidence.py 1814,
gate-lab 2166 (6 files), drift-lint 1594 (4 files), skills 469 (5 files), rules/windows.md 9, evals 80 (10 files)
$ wc -l (rewritten)
mem-check.ps1 126 (+ tests 211, 13 cases), safe-edit.ps1 189 (+ tests 185, 15 cases), lessons_guard.py 73
$ rigger-windows hooks.json handlers
10: SessionStart core_check; PreToolUse PowerShell --pwsh-all; 7 Bash (--only-git x3, --only-narrow x4); Stop stop_shadow (async)
$ couplings of the moved test tools
test_stop_shadow.py:16-21 imports replay, report, lab_fixtures from a sibling gate-lab folder; replay.py:31 and lab_fixtures.py:11 import from ../shell-guard; test_rules.py:56 imports rules_common
$ tests/test_repo.py:405-411   4 PreToolUse handlers in rigger-core + rigger-testing pinned at timeout 20; :427 missing-script commands exit 0
$ token_audit.py:675   ALLOW_COMMENT counts "# shell-guard: allow"
$ coordinate.md 142 lines; steps 2 = 39-47, 3 = 48-64, 4 = 65-82
$ the source harness commit 30a9d2e: 4 files, +129 -23 (coordinate.md, orchestrating-subagents SKILL.md, cli-host.md, test_coordinate_wiring.py 23 -> 32 tests there; this tree's file has 26)
$ coord_state.py 374 lines (tests 600 + 172 + 153); a move into started needs every waits_on part merged (:255-259)
$ state-file.md 16 lines; compact_guard.py 374 (reads safe_to_compact and coordinator; staleness by mtime, STALE 30 min); nothing cleans <config dir>/state/
$ Stop handlers: rigger-core side_session_guard.py (sync, timeout 10; allows when stop_hook_active is not false, :261; cache only for transcripts >= 1 MiB, :20-22, :278), report_shadow.py (SubagentStop, async), rigger-windows stop_shadow.py (async)
$ stub spike (10 isolated runs): Stop stdin background_tasks = [{"id","type":"shell","status":"running","description","command"}] while a task runs, [] otherwise; output file trailer "[exited with code N]" or "[killed]"; 3 blocks in a row < 1 s; CLAUDE_CODE_CHILD_SESSION=1 and CLAUDE_CODE_CHILD_NOTES_DIR set by the CLI for every hook; claude -p waits for background tasks itself
$ test_review_scope.py 1502 lines, 57 tests (55 build a repository in setUp, by read); no TIMEOUTS entry (120 s)
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
2. **The launcher script or the binary gone after a session start** (half an update, antivirus). A
   person expects commands to run with no output, never every command denied. Test: Task 17,
   `test_launcher_allows_silently_without_a_binary` and `test_missing_launcher_exits_zero`.
3. **An override path to an old or broken shellwise.** A person expects the slot unfilled with the
   version found, never a binary below the pin running. Test: Task 16,
   `test_override_below_the_pin_is_refused`.
4. **`state.py clean` next to a live session whose state file is old but whose transcript is
   fresh, and a session resumed after its files were archived.** A person expects nothing of the
   first moved and the second's files back. Test: Task 3,
   `test_clean_never_touches_a_live_session` and `test_resume_restores_archived_files`.
5. **A side session with a gate running in the background that ends its turn; a dev server left
   running.** A person expects the first held with a reason naming the task, the second never held,
   and no loop. Test: Task 5, `test_block_names_the_task_and_stops_after_three` and
   `test_non_gate_commands_are_not_held`.
6. **A diff that names a new `review.paths` rules file that is a link to a file outside the
   repository.** A person expects it not handed to the reviewer. Test: Task 9,
   `test_newly_named_link_is_not_handed_over`.
7. **rigger-windows still enabled after the update.** A person expects one line naming the disable
   command and the double-filler line, not two guards in silence. Test: Task 26,
   `test_enabled_rigger_windows_gets_one_line`.
8. **A `live:<rule>` lesson for an old guard id.** A person expects it checked under the new id, or
   reported `no checker`, never dropped. Test: Task 18, `test_old_ids_route_through_aliases`.
9. **PR C stopped half way** (Task 23 merged, Task 26 not). A person expects one shell guard, the
   commit rows written once, and a rigger-windows that names only files it has. Test: Task 23,
   `test_rigger_windows_manifest_names_only_its_files`, run again in Tasks 24 and 25.

---

## Decisions this plan makes

3a used D54–D73.

| # | Decision | Why |
|---|---|---|
| D74 | Three pull requests: A (Tasks 1–13) harness and backlog; B (14–22) new code beside rigger-windows, changing none of its files; C (23–31) the guard hand-over, the moves, the removal, the migration. One release after C. | A touches no plugin a person installs new. B is revertible alone. C changes what is installed (decisions doc D1). |
| D75 | In PR B the shellwise module is a second `shell-guard` filler (doctor's double-filler warning, accepted between B and C, never released). Task 23 switches `rigger-defaults.json` `shell-guard` to `{kind: hook, target: rigger-shellwise}` with no `requires` and deletes the Python guard's deny half and its 8 PreToolUse handlers in the same commit. | One guard at every merge point after C's first task. |
| D76 | The installer of design §1: download from the SessionStart handler (detached when Task 14 Step 6 shows a detached child survives the hook on Windows and Linux; else synchronous with a 7 s bound and a retry next session), pinned SHA-256 in `pin.json`, one expected member unpacked, exec bit set on POSIX, `--version` check, atomic placement, `current` file, `status.json`, a lock with a 10-minute takeover, `shell_guard.shellwise_path` active at level 3 with the same version check. No Python fallback guard. | Decisions doc D2; design approach A; the review's finding that the detached child was not observed. |
| D77 | Pin `v0.1.0`. A pin bump is one edit of `pin.json` (version, four names, four hashes) and of `RULES` in the rule checker; `CONTRIBUTING.md` gets "Updating shellwise" (download the four `.sha256` files, compare, run Task 28's real-download stub case). shellwise PR #5's rules arrive with that bump. | shellwise PR #5 is not merged; the plan pins what is released. |
| D78 | `shellwise.toml` is the delivered integration config with `gate_commands = ["safe-edit*", "safe_edit*"]`. Task 14 proves on `v0.1.0` that every key is accepted (no `config-invalid`), that each raised rule fires, and which rule-map rows differ from the later head. A key `v0.1.0` refuses is dropped and listed in the release notes. | The rule map was probed on a later head. |
| D79 | The commit shadow rule moves with its closure (design approach B): `git mv` of `shell_guard.py` to `plugins/rigger-TESTING/tools/test-evidence/commit_shadow.py`, deny half deleted, `main` reduced to `log_commit`. Handlers `PreToolUse` `Bash` `if Bash(*commit*)` and `PowerShell` `if PowerShell(*commit*)`, timeout 20 (the pin at `tests/test_repo.py:405-411` extends to 6 handlers), `|| true` and the missing-script guard. `ALLOW_MARKER` becomes `shellwise: allow`; `token_audit.py:675` counts both markers (old transcripts carry the old one). Switch `TEST_EVIDENCE=off`. | Keeps the measurement gate-lab replays; moved lines are not new code; a busy Windows machine timed out at 10 s. |
| D80 | `mem_check.py` per design §3. `load.min_free_gb` and `load.max_heavy_agents` are read from config levels 1–4 (`config.py` gains `load`, `ACTIVE_ABOVE_L1`); the defaults per system live in `mem_check.py` (Windows: `min(30 GiB, 25% of the ceiling)`; Linux and macOS: 4 GiB; heavy agents 2), used when no level sets the key; level 4 (the project) may only make a limit stricter (a higher minimum, fewer agents), a looser value is dropped with one notice. Task 20 Step 1 spikes the Windows reading without `pwsh` against today's `.ps1` output on this machine. | Decisions doc D3; level 1 is static config with no per-system selection; a project must not loosen a machine's limit. |
| D81 | `safe_edit.py` keeps every option as a long flag (`--path --find --replace --count --run --expect-ran --working-directory --restore`), exit codes 0–4 and the messages, em dash included; `--run` runs through the platform shell (`pwsh -NoProfile -Command` on Windows when present, else `sh -c`), recorded in the backup file. The 15 cases become `unittest` tests. | Decisions doc D4; the shellwise `gate_commands` glob needs the file name. |
| D82 | drift-lint moves to `plugins/rigger-core/tools/drift-lint/` unchanged, then gains one check, `trigger-load`: a trigger row's `load` names a skill that no module has. | Spec §13 row. |
| D83 | The platform module: skills, rule, evals, triggers and `core_check.sh` of design §2; trigger ids `rigger-platform.<skill>`; `evals/CASES.md` section renamed. The guard's old marker text in `windows-dev-env` is rewritten to the new marker. Cutting the sentences the redundant-prose survey marks "can be dropped" is Task 25 Step 3, done only with the coordinator's OK (question 3). | Spec §13 rows, design §2; the cuts go beyond spec §13. |
| D84 | `harness_start.py` prints `rigger: rigger-windows is still enabled; disable it (claude plugin disable rigger-windows@<marketplace>)` once per startup while `installed_plugins.json` lists it enabled. Doctor's line 495 becomes a recommendation of the platform module and a `shell-guard` filler. | Spec §13 migration. |
| D85 | The five `tests/contract/shell-guard/` fixtures are copied to the shellwise module in Task 15 and deleted from rigger-windows with it in Task 26; `test_module_contracts.py` runs the copies against the shellwise launcher with a stub binary. | They follow the filler; PR B changes no rigger-windows file. |
| D86 | coordinate: port the source commit with this repository's words, plus three fixes (design §7). The heavy-job sentence stays inside the one `mem-check` bullet. | Owner's scope item 1. |
| D87 | `state.py` per design §8; one new SessionStart handler `state.py hook` on `startup|clear|compact|resume`, timeout 10: `startup` runs `clean`; `resume` restores the session's archived files first; `clear`, `compact` and `resume` run `check` on the current session's file only. `DuplicateHookTests` counts rise by one. | One process; on `startup` a new session has no state file yet. |
| D88 | `state.py check` reads PR state with `gh pr view <n> --repo <owner/name>` (the item's repository, recorded at `add` from the cwd's `origin`), 3 s per call and 5 s per run, caching only `MERGED` keyed by repository and number; it never fails the session start. | Offline and `gh`-less machines must not slow or break startup; a PR number alone names no repository. |
| D89 | The turn-end guard joins `side_session_guard.py`'s Stop path (design §9) and runs before its transcript scan. It holds a turn only for a `shell` entry of `background_tasks` whose command matches the gate and test list (`gate.py`, `run_tests.py`, `pytest`, `python -m unittest`, `npm test`, `npm run test`, `vitest`, `jest`, `phpunit`, `playwright`, `go test`, `cargo test`, `safe_edit`), never in a session whose state file says `coordinator: true` and never under `CLAUDE_CODE_ENTRYPOINT=sdk-cli` (`claude -p` waits for its tasks itself). Side session = the marker Task 1 measures; with no marker, every session that passes those filters. Poll bound 4 s; at most 3 blocks per task id, counted in its own always-written file `<state dir>/<session id>.turn-guard.json`; the Stop handler's timeout rises from 10 to 15 s. | Design approach C; the owner's scope is the session's own gate or test run; the existing cache is written only for large transcripts. |
| D90 | `slot_run.py` passes the session to `review_scope.scope()` (its own `--session` argument, else `CLAUDE_CODE_SESSION_ID`). | 3a CHANGELOG known limit. |
| D91 | The review module gets `hooks/hooks.json` with one `SessionStart` handler `sh core_check.sh rigger-review`; the testing module adds `core_check.sh rigger-testing` to its file. Both copies byte-identical to the five others; `tests/read_by.json:24` (`test_core_check.py`) gains both modules. | Owner's scope item 4; core_check prints nothing when rigger-core is present. |
| D92 | WinError 32 and the `test_report_shadow.py` flake did not reproduce (48 runs). Task 11 adds a diagnostic to `test_slot_run.py`'s cleanup (on `PermissionError`: the path and the alive child processes of the test process to stderr, one retry after 0.5 s; a retry that succeeds still prints `# cleanup retried: <path>` so a run report shows it; a second failure re-raises with that text) and splits `test_slot_run.py` (70–114 s against 120 s). No product change without a reproduction. | A fix without the failure is a guess; the time limit is a measured risk. |
| D93 | Read-by refinement: each of the nine `*` entries whose file the lint derives as reading a finite set of modules gets that set; `*` stays only for a file that iterates `plugins/*`. | Fewer tests per module change, the same coverage. |

## Rulings on the plan's questions

None yet; the coordinator's rulings go here.

## Questions for the coordinator

1. **Task 1 Step 3** needs one probe Stop hook in the desktop app (a scratch project's
   `.claude/settings.local.json`, removed after), in a top-level session and in a side session it
   starts: the only way to see the hook's own environment there. OK to run it? Default if not: no
   marker, D89's filters alone.
2. With no marker, D89 holds a top-level session's turn too, for its own gate or test command only,
   never a coordinator's. Accept, or require a marker and ship the guard off until one exists?
3. D83's sentence cuts in `windows-dev-env` (the redundant-prose survey's "can be dropped" rows):
   in 3b, or later? Default: later; only the marker text changes in 3b.
4. The source harness clone for Task 2 (commit `30a9d2e`): the brief names its local path.

## Spec corrections

Line numbers that moved since the spec was written (the design uses the new ones): `hooks.json:20-77`
is `:11-69`; `run_tests.py:89` is `:118`; `CONTRIBUTING.md:36` is `:37` and `:86-90` is `:109-113`;
`doctor.sh:489` is `:495`; `tests/test_repo.py:270` is `:376-385` and `:101` is `:197-202`;
`curate-lessons/SKILL.md:92-99` is `:94-104`; `orchestrating-subagents/SKILL.md:47-51` is `:48-52`.
`rules/windows-skills.md` no longer exists (its rows are triggers in `rigger-module.json`). Task 29
writes these into the spec.

## File map

| Path | Task | Responsibility |
|---|---|---|
| `docs/superpowers/spikes/phase-3b/side-session-marker.md` | 1 | the side-session marker and `background_tasks` on the current CLI |
| `plugins/rigger-core/commands/coordinate.md`, `skills/orchestrating-subagents/SKILL.md`, `tools/coord-state/cli-host.md`, `tools/coord-state/tests/test_coordinate_wiring.py`, `tests/test_core_wiring.py`, `plugins/rigger-core/README.md:52-54` | 2 | lane rule |
| `plugins/rigger-core/tools/state/state.py`, `tools/state/tests/test_state.py` | 3 | state tool |
| `plugins/rigger-core/hooks/hooks.json`, `rules/state-file.md`, `tests/test_core_wiring.py` (state-file pin), `tests/test_repo.py` (hook counts), `scripts/hook_latency.py` `CASES`, `tests/test_hook_latency.py` | 4 | state wiring and rule |
| `plugins/rigger-core/tools/side-session-guard/side_session_guard.py`, its tests, `plugins/rigger-core/hooks/hooks.json` (Stop timeout), `tests/test_repo.py` (timeout pin if any) | 5 | turn-end guard |
| `plugins/rigger-core/tools/slot-run/slot_run.py`, `tests/test_slot_run.py` (one class) | 6 | session |
| `plugins/rigger-REVIEW/hooks/{hooks.json, core_check.sh}` (new), `plugins/rigger-TESTING/hooks/{hooks.json, core_check.sh}`, `rigger-module.json` of both if `hooks` is declared there, `tests/test_repo.py`, `tests/test_shellcheck.py`, `plugins/rigger-rules/tests/test_core_check.py`, `tests/read_by.json:24`, `hook_latency.py` `CASES` | 7 | core_check |
| `plugins/rigger-core/tools/review-scope/tests/test_review_scope*.py`, `tests/read_by.json` (keys), `scripts/sweep-allow.txt` (rows keyed by the file, if any) | 8 | split |
| `review_scope.py`, one test file of Task 8's split | 9 | newly named rules file |
| `plugins/rigger-core/tools/assemble/assemble.py:992-994`, its `test_assemble.py` row | 10 | doctor INFO row |
| `test_slot_run.py` (cleanup and split), `tests/read_by.json` (keys), `scripts/run_tests.py` `TIMEOUTS` only with a measured reason | 11 | diagnostic, split |
| `tests/read_by.json`, `tests/test_read_by.py` | 12 | narrowing |
| `scripts/run_tests.min`, PR A body | 13 | gate A |
| `docs/superpowers/spikes/phase-3b/shellwise-v0.1.0.md` | 14 | pin facts |
| `plugins/rigger-SHELLWISE/{.claude-plugin/plugin.json, rigger-module.json, README.md, pin.json, shellwise.toml, bin/py.sh, tests/contract/shell-guard/* (copies), tests/test_pin.py}`, `.claude-plugin/marketplace.json`, `tests/test_repo.py` (listing, `PLUGINS_WITH_PY_SH`, counts) | 15 | skeleton |
| `plugins/rigger-SHELLWISE/tools/install/shellwise_install.py`, its tests | 16 | installer |
| `plugins/rigger-SHELLWISE/bin/shellwise.sh`, `hooks/hooks.json`, launcher tests, `tests/test_module_contracts.py`, `tests/test_repo.py` (counts, `:427`), `tests/test_shellcheck.py`, `hook_latency.py` `CASES` | 17 | launcher, wiring |
| `plugins/rigger-SHELLWISE/tools/rule-checker/rule_checker.py`, its tests, `plugins/rigger-lessons/tools/lessons_lint.py:55`, `plugins/rigger-lessons/tests/test_lessons_lint.py:504-567`, `curate-lessons/SKILL.md:94-104`, `templates/archive/README.md`, `plugins/rigger-core/tools/token-audit/tests/test_archive.py` | 18 | rule checker |
| `plugins/rigger-core/tools/assemble/{config.py, slots.py, assemble.py (_doctor)}`, `test_config.py`, `test_slots.py`, `test_assemble.py`, doctor tests | 19 | override active, doctor rows |
| `plugins/rigger-core/tools/mem-check/` (new), `config.py` `load`, `test_config.py`, `scripts/run_tests.py` (discovery of the new tests only) | 20 | mem-check |
| `plugins/rigger-TESTING/tools/safe-edit/` (new) | 21 | safe-edit |
| `scripts/run_tests.min`, PR B body | 22 | gate B |
| `plugins/rigger-TESTING/tools/{test-evidence,gate-lab}/` (moved), `plugins/rigger-TESTING/hooks/hooks.json`, `plugins/rigger-windows/{hooks/hooks.json, rigger-module.json, README.md, tools/shell-guard/*}`, `plugins/rigger-core/rigger-defaults.json:9`, `tests/test_repo.py` (`HAND_MADE_HOOKS`, counts, `:405-411`, `:469`, `FAIL_OPEN_PLUGINS`), `scripts/sweep-allow.txt` (31 `test_rules.py` rows go, 10 turn_evidence rows re-pathed), `tests/read_by.json:31`, `scripts/run_tests.py:101,119`, `hook_latency.py:668-670` `CASES`, `plugins/rigger-core/tools/token-audit/{token_audit.py:675, tests/test_archive.py}`, `test_slots.py`, `test_assembly_golden.py` (`SHELL_GUARD_FILLED`), `CLAUDE.md:21`, `CONTRIBUTING.md:109-113` | 23 | guard hand-over |
| `plugins/rigger-core/tools/drift-lint/` (moved), `plugins/rigger-windows/{rigger-module.json, README.md}`, `CONTRIBUTING.md:37`, `tests/read_by.json:30`, `scripts/sweep-allow.txt` (10 rows) | 24 | drift-lint |
| `plugins/rigger-PLATFORM/**` (new; moved skills, rule, evals, `core_check.sh`), `plugins/rigger-windows/{rigger-module.json, hooks/hooks.json, README.md}`, `.claude-plugin/marketplace.json`, `tests/test_repo.py` (listing, `PLUGINS_WITH_CORE_CHECK`), `tests/read_by.json` (`test_core_check.py` modules), `evals/CASES.md`, drift baseline rows, `test_plugin_rules.py:27-28,298-321`, `test_assembly_golden.py` (trigger rows) | 25 | platform |
| `plugins/rigger-windows/**` (deleted), `.claude-plugin/marketplace.json`, `harness_start.py`, `doctor.sh:495`, the remaining test files of the reference map (`test_repo.py`, `test_assembly_golden.py`, `golden_baseline.py`, `test_slots.py`, `test_render.py`, `test_stub_session.py`, `test_config.py`, doctor tests and harness, `test_core_check.py`, `test_reinject.py`, `test_shellcheck.py`, `test_module_contracts.py`, `startup_bench.py`), `scripts/run_tests.py:118`, `hook_latency.py:668` | 26 | removal |
| `plugins/rigger-rules/rules/{tests.md:13, defects.md:37}`, `test_plugin_rules.py:349-355`, `testing/SKILL.md:85-87`, `implementer-brief.md:61-69`, `orchestrating-subagents/SKILL.md:48-52`, `e2e-playwright-speed/SKILL.md:152`, `git-workflow/SKILL.md:21-22`, `stack-tooling/SKILL.md:72-73`, `test_core_wiring.py:199-208`, `README.md`, `plugins/rigger-core/README.md:31,82` | 27 | wording |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` (shellwise and guard cases), `docs/superpowers/spikes/phase-3b/exit-criteria.md` | 28 | stub exit criteria |
| `CHANGELOG.md`, module READMEs, `CONTRIBUTING.md`, the spec file | 29 | docs, migration |
| `scripts/run_tests.min`, PR C body | 31 | gate C |

## Lanes

A lane is a chain where each part needs the one before. A "group" is independent light parts that
share one session. "Shared files" names the registries two lanes both touch; the coordinator merges
them in the order under the table.

| Lane | Parts (in order) | Model | Heavy | Shared files |
|---|---|---|---|---|
| A1 coordinate | 2 | opus | light | `test_core_wiring.py` |
| A2 state | 3 → 4 → 7 | opus, sonnet, sonnet | light | core `hooks.json`, `test_repo.py`, `hook_latency.py`, `read_by.json` |
| A3 turn-end | 1 → 5 | opus, opus | heavy (1), light (5) | core `hooks.json` (after A2's Task 4) |
| A4 review scope | 8 → 9 → 12 | sonnet, opus, sonnet | light | `read_by.json` |
| A5 slot run | 6 → 11 | sonnet, sonnet | light (6), heavy (11: suite > 2 min) | `read_by.json` |
| A group | 10 | sonnet | light | — |
| A gate | 13 | sonnet | heavy | — |
| B1 shellwise | 14 → 15 → 16 → 17 → 18 → 19 | opus (14, 16, 17, 18), sonnet (15, 19) | heavy (14), light (15–19) | `marketplace.json`, `test_repo.py`, `hook_latency.py`, `config.py` (19 after 20) |
| B2 mem-check | 20 | opus | light | `config.py`, `test_config.py` |
| B3 safe-edit | 21 | sonnet | light | — |
| B gate | 22 | sonnet | heavy | — |
| C1 hand-over | 23 → 24 → 25 → 26 → 27 | opus, sonnet, sonnet, opus, sonnet | heavy (23–26 run the full `--fix-round`, 26 also the WSL leg), light (27) | every registry; one lane, so one writer |
| C2 proof and docs | 28 → 29 → 30 | opus, sonnet, opus | heavy, light, heavy | starts after 27 |
| C gate | 31 | sonnet | heavy | — |

Merge order where lanes share a file: A2's Task 4 before A3's Task 5 (core `hooks.json`); A2's
Task 7 and A4's Task 12 and A5's Task 11 each rebase on the last `read_by.json` before merging; B2
before B1's Task 19 (`config.py`). Lanes at once: A starts A1, A2, A3 (Task 1), A4, A5 and the
group: 6 sessions, 2 of them heavy at most (Tasks 1 and 11). B starts 3. C runs as one lane because
every task in it edits the same registries.

---

## Pull request A: lanes, state, turn end and the backlog

### Task 1: Spike — the side-session marker and `background_tasks` on the current CLI

**Heavy, opus. 1.5 h.** Inputs: the stub spike (Baselines); question 1's answer. Output: the
marker D89 uses, or "none".

**Files:** Create `docs/superpowers/spikes/phase-3b/side-session-marker.md`.

- [ ] **Step 1:** Record `claude --version` on PATH and the desktop app's CLI version (from `CLAUDE_CODE_EXECPATH`).
- [ ] **Step 2:** Repeat the stub spike's run with one background `sleep` on the current CLI: `background_tasks` while running and after; control with no task; one run with `CLAUDE_CODE_ENTRYPOINT` as the CLI sets it under `-p` (expected `sdk-cli`).
- [ ] **Step 3 (after question 1's OK):** A probe Stop hook in a scratch project's `.claude/settings.local.json` logs, to a scratch file, the env names and the values of `CLAUDE_CODE_HOST_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_ENTRYPOINT`, plus the stdin keys, in a top-level desktop session on that project and in a side session it starts. Known answer: the session ids the app's session list shows. Remove the probe and check the file is gone.
- [ ] **Step 4:** Write the result file (`## Question`, `## CLI`, `## Setup`, `## Control`, `## Observed`, `## Decision`); sweep; commit `docs(spikes): record the side-session marker and the Stop event's background tasks`.

### Task 2: coordinate — one session per lane

**Light, opus. 2.5 h.** Inputs: commit `30a9d2e` of the source harness (read-only `git show` in the
clone the brief names; its plugin names are written `<prefix>-core` in any note). Output: the three
text files and their pins.

**Files:** file map row 2.

- [ ] **Step 1: Port the source commit's tests** into `test_coordinate_wiring.py` with this repository's words ("user", "integration branch", a `RIGGER_ORCHESTRATING_MD` override next to `RIGGER_COORDINATE_MD`): `test_split_step_defines_session_granularity_once`, `test_split_step_keeps_reviews_and_other_models_out_of_the_lane`, `test_split_step_starts_every_session_in_the_users_turn`, `test_split_step_has_no_session_cap_of_its_own_and_keeps_the_load_rule`, `test_the_coordinator_does_not_implement_and_names_every_agent_exception`, `test_a_refused_start_tries_a_standby_session_before_a_card_or_an_agent`, `test_standby_sessions_have_a_brief_and_a_close`, `test_cli_host_starts_no_standby_sessions`, `test_skill_allows_coordinator_sessions_and_shares_the_heavy_slots`, `test_skill_and_command_cap_the_same_heavy_jobs_at_two`, and the extended over-the-cap test; remove `test_split_step_runs_heavy_parts_two_at_once_and_groups_light_ones`. Add three: `test_step_two_has_lane_start_and_load_paragraphs`, `test_a_lanes_next_part_waits_for_the_merge` (step 2 names the merge and `coord_state` refusing an earlier start), `test_one_owner_of_the_full_gate` (step 3 and step 4 both say the side session runs the full gate on its branch before it reports and the coordinator once per merge point; neither holds "the gate are yours, at checkpoints").
- [ ] **Step 2: Run** the wiring tests. Expected: each new test `FAILED` on its assertion.
- [ ] **Step 3: Write the text** per design §7. The skill's heavy-job sentence goes inside the one bullet that names `mem-check`; `test_core_wiring.py:199-208` keeps passing unchanged.
- [ ] **Step 4: Run** the wiring tests, `test_core_wiring.py`, `test_plugin_rules.py`, the drift-lint test. Expected: `OK`.
- [ ] **Step 5: Red runs:** the old step 2 back (the ported tests red); the merge sentence dropped; "the gate are yours" back; a second `mem-check` bullet (`LoadRuleTests` red).
- [ ] **Step 6: Commit.** `feat(rigger-core): coordinate opens a session per lane and caps only heavy jobs`

### Task 3: The state tool

**Light, opus. Full risk (data deletion). 5 h.** Inputs: design §8. Output: `state.py` with the
interface below, used by Task 4.

**Files:** Create `plugins/rigger-core/tools/state/state.py`, `plugins/rigger-core/tools/state/tests/test_state.py`.

**Interfaces:**
- CLI: `state.py [--state-dir D] [--session ID] [--projects-dir P] <command>`; session from `CLAUDE_CODE_SESSION_ID`; state dir `<CLAUDE_CONFIG_DIR or ~/.claude>/state`; transcripts under `<config dir>/projects/*/<session id>.jsonl`.
- Commands of design §8: `add <id> <title> [--branch B] [--repo owner/name]` (repository from `git remote get-url origin` in the cwd when not given; none → `—`), `set`, `note`, `done`, `show`, `check [--now ISO]`, `clean [--now ISO]`, `hook` (reads the SessionStart stdin `source`; D87; prints at most 6 lines; always exits 0).
- A session's files are every `<id>.*` file of the state dir (`.md`, `.log.md`, `.parts.json`, `.summary.md`, `.side-guard.json`, `.turn-guard.json`); `compact-guard.log`, `pr-cache.json`, `archive/` and `.clean.lock` are never moved.
- Idle: the session's newest state file and its transcript (when one exists) are both older than 14 days. Archiving sets each moved file's mtime to the move time (`os.utime`), so the 60 days count from the archive. `clean` holds `.clean.lock` (pid and time; one older than 10 minutes is taken over) and skips when another run holds it.

- [ ] **Step 1: Stubs and failing tests.** Stubs that return wrong values (Global Constraints). Fixtures: a live session (fresh transcript, 20-day-old state file), a dead one (all files 20 days old, transcript too), one with no transcript (state files 20 days old), an archived one at 70 days, a `.log.md` of 60 kB, the current session (old files). Tests: `test_add_set_note_done_round_trip_keeps_other_sections_byte_for_byte` (CRLF and non-ASCII sections), `test_done_moves_detail_to_the_log`, `test_refusals` (bad id, unknown id, unknown status, `done` without `--pr`, a second `## Items`), `test_check_flags_merged_pr_deleted_branch_and_stale_false` (stub `gh` on `PATH` printing `MERGED` only for the item's `--repo`; a branch not in the temp repo; `safe_to_compact: false` with `updated` 31 minutes old; each fires on its fixture and nothing fires on a clean file), `test_check_is_bounded_and_offline_safe` (a `gh` that sleeps 10 s: the run ends within 6 s with one `unchecked` line; no `gh`: one line), `test_pr_cache_is_keyed_by_repository`, `test_clean_moves_idle_sessions_and_deletes_after_sixty_days`, `test_archive_resets_the_clock`, `test_clean_never_touches_a_live_session`, `test_clean_never_touches_the_current_session`, `test_clean_skips_while_another_holds_the_lock`, `test_resume_restores_archived_files`, `test_log_rotation_keeps_the_newest_lines`, `test_writes_are_atomic`.
- [ ] **Step 2: Run.** Expected: `FAILED` on assertions, no errors.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: `OK`, with `# clean: moved <n>, deleted <m>` lines in the clean tests.
- [ ] **Step 5: Red runs (full):** each day rule off by one; mtime not reset; the live check removed; the transcript ignored; the current-session check removed; the lock ignored; resume restoring nothing; rotation keeping the oldest half; `done` leaving the detail; a write that decodes and re-encodes the whole file (byte test red); the `gh` bound removed; the cache keyed by number only.
- [ ] **Step 6: Commit.** `feat(rigger-core): a state tool for work items, folder cleanup and log rotation`

### Task 4: State wiring and the rule

**Light, sonnet. 1 h.** Inputs: Task 3. Output: the handler of D87, the rule text.

**Files:** file map row 4.

- [ ] **Step 1:** Tests first: `DuplicateHookTests` count +1; a pin that `state-file.md` names `state.py` for items and keeps the 9 000-character and Next-step rules; a hook-latency case with a matching (`startup`) and a non-matching (`clear` with no state file) stdin.
- [ ] **Step 2:** Red; add the handler (`sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/state/state.py" hook || true`, matcher `startup|clear|compact|resume`, timeout 10) and the rule text; green; `test_plugin_rules.py` and the drift-lint test.
- [ ] **Step 3:** Measure the handler with `hook_latency.py` on Windows; median and p95 in the report.
- [ ] **Step 4: Commit.** `feat(rigger-core): run the state check at session start and point the rule at the tool`

### Task 5: The turn-end guard for side sessions

**Light, opus. Full risk. 3.5 h.** Inputs: Task 1's marker; Task 4 merged (core `hooks.json`).
Output: D89 in `side_session_guard.py`.

**Files:** file map row 5.

- [ ] **Step 1: Failing tests** (Stop stdin fixtures from the spike; output files written by the test; a state dir in temp): `test_running_gate_task_blocks_with_its_id_and_description`, `test_non_gate_commands_are_not_held` (`npm run dev`, `python -m http.server`), `test_task_that_ends_during_the_poll_allows` (the file gets `[exited with code 0]` after 1 s), `test_killed_trailer_allows`, `test_subagent_entries_are_ignored`, `test_block_names_the_task_and_stops_after_three` (`stop_hook_active` true, same id: blocks 2 and 3, then allows with a line; the count survives a transcript under 1 MiB), `test_poll_is_bounded` (never-ending task: the hook returns within 5 s), `test_coordinator_session_is_not_held`, `test_print_mode_is_not_held` (`CLAUDE_CODE_ENTRYPOINT=sdk-cli`), `test_top_level_session_is_not_held` (by Task 1's marker; with "none" this test asserts the documented fallback instead), `test_missing_field_or_file_allows`; the existing guard's tests still pass.
- [ ] **Step 2:** Red; implement (the check runs before the `stop_hook_active` early return at `side_session_guard.py:261` and before the transcript scan, which it skips when it blocks); the Stop handler's timeout to 15 with its pin; green.
- [ ] **Step 3: Red runs (full):** no poll; no cap; the count in the old cache; cap per session instead of per task; a subagent counted; the command filter off; the coordinator check off; the `sdk-cli` check off; the marker check off.
- [ ] **Step 4: Commit.** `feat(rigger-core): hold a side session's turn end while its own gate runs`

### Task 6: `slot_run` passes the session

**Light, sonnet. 1 h.** Inputs: none. Output: D90. **Files:** file map row 6.

- [ ] **Step 1:** Test `test_scope_file_carries_review_paths_of_the_session` (a level-3 skip from a session snapshot appears in `{scope}`); red; implement; green; red run (session dropped). Commit `fix(rigger-core): slot_run reads review.paths for the session`.

### Task 7: core_check in the review and testing modules

**Light, sonnet. 1.5 h.** Inputs: Task 4 merged (hook counts). Output: D91. **Files:** file map row 7.

- [ ] **Step 1:** Tests: `PLUGINS_WITH_CORE_CHECK` gains both; `DuplicateHookTests` counts; `test_shellcheck.py` lists both; `test_core_check.py` runs each copy from a cache-like layout without rigger-core and expects its line, with rigger-core and expects nothing. `git add` the new `.sh` before the shellcheck run.
- [ ] **Step 2:** Red; copy `core_check.sh` byte for byte; add the handlers and the `read_by.json:24` modules; green; red run (handler without the plugin name). Commit `feat: the review and testing modules say when rigger-core is missing`.

### Task 8: Split `test_review_scope.py`

**Light, sonnet. 2 h.** Inputs: none. Output: the split files Task 9 edits. **Files:** file map row 8.

- [ ] **Step 1:** Time the file alone three times; print the test-id set. Split by class, each file under 400 lines, names that differ by more than case; read-only classes share one repository from `setUpClass`. Print the id set again: equal. Time each part three times: each under 50% of its limit. Commit `test(rigger-core): split the review-scope tests`.

### Task 9: A newly named rules file that is a link or over the cap

**Light, opus. Full risk (trust). 1.5 h.** Inputs: Task 8. Output: the two new verdicts of design
§10. **Files:** file map row 9.

- [ ] **Step 1:** Tests: `test_newly_named_link_is_not_handed_over` and `test_newly_named_over_cap_is_not_handed_over` (each asserts the exact row text `review: rules file newly named (link)` / `(over <n> bytes)` and that no `# rules` line names the file), `test_newly_named_regular_file_is_handed_over` (control). Red; implement (blob mode `120000` from `ls-tree`, size from `cat-file --batch-check`, the existing cap); green. Red runs: mode check off, size check off, `<` for `<=`. Commit `fix(rigger-core): a newly named rules file that is a link or too large is not handed over`.

### Task 10: Doctor's `rules-d-table` row names the cause

**Light, sonnet. 0.5 h.** Inputs: none. Output: an INFO row whose text is the cause (a rules file
still holds a skill-trigger table) and no FIX (`commands/doctor.md:18`). **Files:** file map row 10.
One test on the row text and its empty fix; red; fix; commit `fix(rigger-core): doctor names the cause of the rules-d-table row`.

### Task 11: `test_slot_run.py`: cleanup diagnostic and split

**Heavy, sonnet. 2 h.** Inputs: Task 6. Output: D92. **Files:** file map row 11.

- [ ] **Step 1:** D92's diagnostic in the cleanup helper; a test holds a handle open on a temp file (Windows; skipped elsewhere with its reason) and sees `# cleanup retried:` on a retry that succeeds and the diagnostic text in the error on one that does not.
- [ ] **Step 2:** Split by class with the id sets compared, until each part stays under 50% of its limit in three runs; record the times.
- [ ] **Step 3:** Run `test_report_shadow.py` 10 times under the gate's load; on a failure record the assertion and stop for a ruling; else record the count. Commit `test(rigger-core): split the slot-run tests and explain a locked cleanup`.

### Task 12: Read-by narrowing

**Light, sonnet. 1.5 h.** Inputs: Task 8 (keys). Output: D93. **Files:** file map row 12.

- [ ] **Step 1:** Print each `*` entry's derived reads; narrow per D93; `test_wildcards_only_for_files_that_iterate_plugins`. Red run: a new module literal in a narrowed entry's file (the declared-read test red). Commit `test: narrow the read-by wildcards`.

### Task 13: Gate and pull request A

**Heavy, sonnet. 1.5 h.** Set `run_tests.min`; full gate in the background; `--verify` (`(full)`);
push; PR body with the mutation list per task and survivors, the state hook latency, Task 1's result.
Review round; Tasks 3 and 5 get the second round (D65).

---

## Pull request B: the shellwise module, mem-check and safe-edit

### Task 14: Spike — the pinned release

**Heavy (downloads, WSL, stub), opus. 3.5 h.** Inputs: none. Output: the values `pin.json` and
`RULES` take, the hook's observed reply form, the detached-child answer for D76.

**Files:** Create `docs/superpowers/spikes/phase-3b/shellwise-v0.1.0.md`.

- [ ] **Step 1:** Download the four archives and their `.sha256` files into a new empty folder; compute SHA-256 of each archive and compare with its file (known answer: the release's own files). Record names, sizes, hashes.
- [ ] **Step 2:** Unpack (Windows `.zip`; Linux `.tar.gz` under WSL); list members (expected `shellwise-v0.1.0-<target>/shellwise[.exe]` plus licence files); `--version` (expected `shellwise 0.1.0`).
- [ ] **Step 3:** `explain <id>` for the 53 documented ids (exit 0 each) and for `bash-ps-call-operator` (expected 64): the `RULES` list.
- [ ] **Step 4:** Rerun every probe of the rule map's Table 1 at defaults and with `shellwise.toml` (D78) on Windows (Git Bash and pwsh) and Linux; mark each row that differs from the map. Probe `python safe_edit.py --run x 2>&1 | Select-Object -First 30` with the config (expected break). Check the output has no `config-invalid`.
- [ ] **Step 5:** `hook claude-code` with a Claude Code PreToolUse stdin for one `break`, one `warn` and one clean command; record stdout and the exit code of each (this decides how Task 17's launcher passes the decision on).
- [ ] **Step 6:** One stub session per system (Windows, WSL) whose SessionStart hook starts a detached child (`subprocess.Popen` with `start_new_session` on POSIX, `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` on Windows, no inherited handles) that writes a marker 15 s later; control: a non-detached child. Record whether the marker appears after the hook returned and after the session ended.
- [ ] **Step 7:** Write the result file; sweep; commit `docs(spikes): record the pinned shellwise release`.

### Task 15: The shellwise module's skeleton

**Light, sonnet. 1 h.** Inputs: Task 14 (names, hashes). Output: the folder Tasks 16–19 fill.
**Files:** file map row 15.

- [ ] **Step 1:** Tests in `tests/test_repo.py` (listing, marketplace order, `PLUGINS_WITH_PY_SH`, counts) and `tests/test_module_contracts.py` (manifest: `fills shell-guard`, `failure_mode closed`, `rule_checker` path); `tests/test_pin.py` checks `pin.json` against a copy of Task 14's recorded table in the test file (four targets, names, 64-hex hashes, the version). Red; write the files; copy the five contract fixtures; green. Commit `feat(rigger-shellwise): the module skeleton and the pin`.

### Task 16: The installer

**Light, opus. Full risk. 5 h.** Inputs: Task 15, Task 14 Step 6. Output: D76.
**Files:** file map row 16.

**Interfaces:** `resolve(data_dir, pin, override) -> Status`; `ensure(data_dir, pin, override, *, spawn) -> Status`; `download(data_dir, pin, target, base_url)`; `main` for `--session-start` (prints at most one line) and `--child`.

- [ ] **Step 1: Stubs and failing tests** (local `http.server`; archives built in the test): `test_target_table` (each row of design §1 and two with no asset), `test_good_archive_is_placed_and_current_written`, `test_exec_bit_is_set_on_posix`, `test_hash_mismatch_deletes_and_fails`, `test_truncated_download_fails`, `test_only_a_verified_member_is_placed` (`../x`, absolute name, a symlink member, a second binary: each refused), `test_version_mismatch_after_unpack_fails`, `test_two_sessions_share_one_download` (lock; a lock 11 minutes old is taken over), `test_override_used_when_at_or_above_the_pin`, `test_override_below_the_pin_is_refused`, `test_override_that_hangs_is_refused_within_its_bound`, `test_pin_change_replaces_the_cached_binary`, `test_no_network_is_one_line`, `test_session_start_is_bounded` (returns within 8 s while the server delays 30 s, in both D76 modes).
- [ ] **Step 2:** Red; implement; green.
- [ ] **Step 3: Red runs (full):** hash compare skipped; compare against the `.sha256` file instead of the pin; member filter off for each refused kind; exec bit not set; lock ignored; takeover never; version check skipped; `current` written before the hash; override version compared as strings (`0.10.0 < 0.9.0`).
- [ ] **Step 4: Commit.** `feat(rigger-shellwise): download, verify and place the pinned shellwise`

### Task 17: The launcher and the hooks

**Light, opus. Full risk (wiring). 2.5 h.** Inputs: Task 16; Task 14 Step 5. Output: the two
handlers. **Files:** file map row 17.

- [ ] **Step 1:** Tests: `test_launcher_execs_current_with_the_config` (a stub binary prints its argv and stdin; its stdout and exit code reach the caller in the form Task 14 Step 5 observed), `test_launcher_allows_silently_without_a_binary` (no `current`; `current` naming a missing file; an empty `current`), `test_missing_launcher_exits_zero` (the hook command with `bin/shellwise.sh` deleted, and the row in `tests/test_repo.py:427`, whose `can't open file` assertion at `:443-445` takes a second form for a guarded `sh -c` command: exit 0, empty stdout and stderr), `test_launcher_paths_with_spaces`, `test_hooks_json_wiring` (SessionStart `startup|clear|compact` runs the installer `--session-start`; PreToolUse `Bash|PowerShell` runs `sh -c '[ -f "$0" ] || exit 0; exec sh "$0"' <launcher>`-style guarded command, timeout 5, no `|| true`), the contract fixtures pass against the stub, `DuplicateHookTests`, `test_shellcheck.py` (`git add` first), `hook_latency.py` `CASES` (matching and non-matching).
- [ ] **Step 2:** Red; implement; green. Measure the launcher's added latency over the stub binary (median and p95 against the `sh` floor).
- [ ] **Step 3: Red runs:** launcher without `--config`; the missing-script guard removed (`test_missing_launcher_exits_zero` red); a missing `current` that exits 1; a deny reply swallowed (per Task 14 Step 5's form, the contract deny fixture red).
- [ ] **Step 4: Commit.** `feat(rigger-shellwise): the hook launcher and session-start wiring`

### Task 18: The rule checker

**Light, opus. 3 h.** Inputs: Task 14 (`RULES`, probes), Task 17. Output: the checker the lessons
lint and curation use. **Files:** file map row 18.

- [ ] **Step 1:** Tests: `test_rules_match_the_pin` (against Task 14's table copied into the test; Task 28 runs the same check against the real binary's `explain`), `test_old_ids_route_through_aliases`, `test_probe_denied_at_break_is_none`, `test_warn_only_is_a_problem`, `test_no_binary_is_no_checker`; `test_lessons_lint.py` finds `rule_checker.py` by glob and by `--rule-checker` (its glob asserts at `:504-567` extended); `test_archive.py` rewritten against a stub checker module; the curate-lessons text takes the checker from the shell-guard filler's manifest.
- [ ] **Step 2:** Red; implement; green; red runs (alias table empty; `warn` counted as a deny). Run `test_plugin_rules.py` and the drift-lint test. Commit `feat(rigger-shellwise): a rule checker built on explain and check`.

### Task 19: Override active, doctor and slot rows

**Light, sonnet. 2 h.** Inputs: Tasks 17 and 20 (`config.py`). Output: the rows of design §1.
**Files:** file map row 19.

- [ ] **Step 1:** Tests: `shell_guard.shellwise_path` leaves `inactive` and is read only at level 3; doctor `slot shell-guard` rows for each `status.json` state with failure mode `closed`; the session line forms. Red; implement; green; one red run per state row. Commit `feat(rigger-core): doctor and slot rows for the shellwise module`.

### Task 20: mem-check in Python

**Light, opus. 4 h.** Inputs: none. Output: D80. **Files:** file map row 20.

- [ ] **Step 1: Spike** (no commit): on this machine, the Windows reading by `ctypes` (`GetPerformanceInfo`) against `mem-check.ps1`'s line (committed, limit, available: equal within 1%), and the Event 2004 read with and without `pwsh`; record in the task report.
- [ ] **Step 2: Tests** (readings through a `--reading-json` test seam, as the `.ps1` has): the 13 `.ps1` cases ported; Linux `/proc/meminfo` fixtures (the smaller of the two counts); macOS `vm_stat` and `sysctl` fixtures; the `load.*` keys at each level, the per-system default when none sets them, a project value that loosens a limit dropped with its notice; exit 2 and its line on an unreadable source; the live read (skipped with its reason only where the system has no reading tool).
- [ ] **Step 3:** Red; implement; green; red runs (equal passes and one byte less fails; Linux taking the larger count; macOS without purgeable; the project loosening accepted).
- [ ] **Step 4:** The `.ps1` stays in rigger-windows until Task 26. Commit `feat(rigger-core): mem-check on every system`.

### Task 21: safe-edit in Python

**Light, sonnet. 3 h.** Inputs: none. Output: D81. **Files:** file map row 21.

- [ ] **Step 1:** Port the 15 cases as `unittest` tests (messages byte for byte, em dash included), plus `test_platform_shell_is_recorded_in_the_backup`. Red; implement; green; red runs (restore skipped on a failing command; hash check skipped; `--count` off by one; CRLF normalisation off). Commit `feat(rigger-testing): safe-edit in Python`.

### Task 22: Gate and pull request B

**Heavy, sonnet. 1.5 h.** Set `run_tests.min`; full gate; `--verify`; push; PR body with Task 14's
result, the mutation list, the launcher latency. Review round; Tasks 16 and 17 get the second round.

---

## Pull request C: the guard hand-over, the moves and the removal

One lane: every task edits the same registries, and each leaves rigger-windows consistent
(`test_rigger_windows_manifest_names_only_its_files`, added in Task 23, runs in each).

### Task 23: The guard hand-over

**Heavy, opus. Full risk (wiring). 6 h.** Inputs: PR B merged. Output: one shell guard (shellwise),
the test-evidence gates and gate-lab in the testing module, a rigger-windows with skills, rule,
evals, drift-lint and the two `.ps1` tools only. **Files:** file map row 23.

- [ ] **Step 1:** List every reference to each path below (`git grep -n`), in the report.
- [ ] **Step 2: Move unchanged, one commit:** `git mv` `shell_guard.py` → `plugins/rigger-TESTING/tools/test-evidence/commit_shadow.py`; `stop_shadow.py`, `turn_evidence.py`, `tests/test_stop_shadow.py`, `tests/test_turn_evidence.py`, `tests/test_commit_gate.py`, `tests/rules_common.py` into `test-evidence/`; the six gate-lab files into `plugins/rigger-TESTING/tools/gate-lab/`. In the same commit delete `test_rules.py`, `corpus_check.py`, `fixtures/corpus.jsonl`, `test_walk_portable.py`, `lessons_guard.py`, the 8 PreToolUse handlers and the Stop handler of rigger-windows' `hooks.json`, its `fills`, `failure_mode`, `provides.shell-guard` and `tests.contract`, and switch `rigger-defaults.json:9` (D75). Fix the imports so the moved suites run: `replay.py:31` and `lab_fixtures.py:11` name `test-evidence`, `test_stop_shadow.py:16-21` finds `gate-lab` as a sibling.
- [ ] **Step 3: Prune, second commit:** delete from `commit_shadow.py` everything outside the closure (map: the name-reference closure of `log_commit` and `commit_row`), and from `test_commit_gate.py` lines 982-1290; `main` calls `log_commit` only; D79's marker, switch and `token_audit.py` count. Line counts before and after.
- [ ] **Step 4: Wiring, third commit:** D79's handlers and the async Stop row in the testing module's `hooks.json`; `HAND_MADE_HOOKS` fates, counts, `:405-411` (6 handlers at 20), `:469`, `FAIL_OPEN_PLUGINS` (rigger-windows out), `TIMEOUTS` path (`run_tests.py:101`), `run_tests.py:119` (`test_rules.py` out of `SERIAL`), the 31 `test_rules.py` rows out of `sweep-allow.txt` and the 10 turn-evidence rows re-pathed, `read_by.json:31`, `hook_latency.py:669-670` `CASES`, `test_slots.py` and `test_assembly_golden.py` (`SHELL_GUARD_FILLED`) for the new default; the "Shadow gates" text becomes `tools/test-evidence/README.md` and `CONTRIBUTING.md:109-113` points at it; `CLAUDE.md:21`'s gate-lab path; replay's `--data` accepts the rigger-windows data folder.
- [ ] **Step 5: Tests:** the moved suites pass; `test_rigger_windows_manifest_names_only_its_files` (every path in its manifest, `hooks.json` commands and README exists); a wiring test runs both commit handlers' commands from the testing module's `hooks.json` on a commit in a temp repo with `.claude/gates.json` and finds one row; `TEST_EVIDENCE=off` writes none; the slot rows show the shellwise default on all three systems. Red runs: a handler's `if` text; the marker text; the switch; a manifest path left behind; the default switch reverted. Full `run_tests.py --fix-round`. Commit `refactor(rigger-testing): take over the test-evidence gates and gate-lab; shellwise fills the shell guard`.

### Task 24: drift-lint moves to rigger-core

**Heavy, sonnet. 2 h.** Inputs: Task 23. Output: D82. **Files:** file map row 24.
`git mv` the four files; remove them from rigger-windows' manifest and README; `CONTRIBUTING.md:37`,
`read_by.json:30`, the 10 `sweep-allow.txt` rows; full `--fix-round`; commit. Then D82's
`trigger-load` check with a test and a planted control (a trigger loading a missing skill); commit
`feat(rigger-core): drift-lint checks trigger loads`.

### Task 25: The platform module

**Heavy, sonnet. 3 h.** Inputs: Task 24. Output: D83. **Files:** file map row 25.

- [ ] **Step 1:** Skeleton (manifest, `rigger-module.json` with the three triggers and the rule with `requires: os:windows`, README, marketplace entry, listing tests); `git mv` the skills, `rules/windows.md`, the evals and `core_check.sh` (the copy stays byte-identical; its argument becomes `rigger-platform`); remove them from rigger-windows' manifest, `hooks.json` and README; trigger ids, loads and self-references renamed; `evals/CASES.md` heading; drift baseline rows re-keyed; `read_by.json` modules of `test_core_check.py`; the herd-docs old path read for one release; the guard marker text in `windows-dev-env`.
- [ ] **Step 2:** `test_plugin_rules.py`, drift-lint, `eval_suite_check.py`, golden and assemble tests for the triggers, full `--fix-round`.
- [ ] **Step 3 (only with question 3's OK):** the redundant-prose cuts, each listed with its probe from Task 14.
- [ ] **Step 4: Commit.** `feat(rigger-platform): the Windows and toolchain skills, rule and evals`

### Task 26: Remove rigger-windows

**Heavy (WSL leg), opus. 4.5 h.** Inputs: Task 25. Output: the plugin gone, D84.
**Files:** file map row 26.

- [ ] **Step 1:** Tests first: `test_enabled_rigger_windows_gets_one_line` (D84 with a fake `installed_plugins.json`; control: not listed, no line); doctor's recommendation row; the golden test's Windows layout without rigger-windows.
- [ ] **Step 2:** Delete the folder (the `.ps1` tools and their tests, `rules_start.sh`, manifests, contract fixtures); `marketplace.json`; `run_tests.py:118`; `hook_latency.py:668`; every remaining live reference of the reference map, re-pointed or removed. Print `git grep -c rigger-windows -- ':!docs' ':!CHANGELOG.md' ':!tests/fixtures'`: only the D84 notice, its test and the migration text remain.
- [ ] **Step 3:** Full `--fix-round` with the id sets of the deleted suites listed; then the WSL leg once. Commit `feat!: remove rigger-windows`.

### Task 27: Wording that named rigger-windows

**Light, sonnet. 1.5 h.** Inputs: Task 26. Output: placeholders `{{tool:safe-edit}}` and
`{{tool:mem-check}}` with `requires: plugin:rigger-testing` where the spec says. **Files:** file map
row 27. The frozen golden fixture stays. `test_plugin_rules.py`, `test_core_wiring.py`, drift-lint.
Commit `docs: name the platform and testing modules instead of rigger-windows`.

### Task 28: Stub exit criteria and the real pin

**Heavy (download, stub), opus. 3.5 h.** Inputs: Task 27. **Files:** file map row 28.

- [ ] **Step 1:** Stub sessions (one isolated config each) with rigger-core, rigger-testing and the shellwise module from `--plugin-dir` and an empty scratch data folder: the installer downloads the real pinned asset for this system (the one end-to-end check of `pin.json`), `rule_checker.RULES` against the real binary's `explain`; a `break` command denied (the stub answers with that `Bash` tool_use; the next request carries the deny), a `warn` command run with its note; binary removed: the session line and the command runs; `Hooks: PreToolUse:Bash` in the debug log for both modules; a commit row from the testing module; Task 5's guard holding a turn with a background gate-like command (Stop read back from the debug log). Doctor reports `closed`. Repeat the download case under WSL.
- [ ] **Step 2:** Write the result file labelled "stub"; commit `test(rigger-core): stub exit criteria for phase 3b`.

### Task 29: Docs, release notes, migration

**Light, sonnet. 2 h.** `CHANGELOG.md` (the behaviour changes; the migration of design §6 with one
install call per plugin; the shellwise pin and how to bump it; the Task 14 rows that differ from the
rule map), module READMEs, `CONTRIBUTING.md` ("Updating shellwise", the gates path), the spec
corrections. Sweep each file. Commit `docs: phase 3b release notes and migration`.

### Task 30: Release review

**Heavy, opus. 2 h.** The review module reviews PR C's diff with every in-scope file covered and
every quote anchored (spec §13 exit criterion); findings go through a fix round.

### Task 31: Gate and pull request C

**Heavy, sonnet. 1.5 h.** Set `run_tests.min`; full gate; `--verify`; push; PR body with the
mutation list, the stub result, the release review line. Review round; Tasks 23 and 26 get the
second round. Merging and the release are the owner's steps.

---

## Left for later

- A shellwise pin bump to the release that carries shellwise PR #5 (`bash-ps-call-operator`, the
  wait-loop and follow bounds): one `pin.json` and `RULES` edit (D77).
- Linux arm64 and Windows arm64 assets: the shellwise owner's call.
- The redundant-prose cuts, if question 3 defers them.
- The hook-process merge, argv-hash pinning of `trust.project_commands`, a Go or Rust port: out of
  scope for phase 3.
- A WinError 32 fix, once Task 11's diagnostic catches one.

## Estimate

| Task | Agent hours | Code lines | Test lines |
|---|---|---|---|
| 1 spike: marker | 1.5 | — | — |
| 2 coordinate | 2.5 | 60 (Markdown) | 150 |
| 3 state tool | 5 | 400 | 620 |
| 4 state wiring | 1 | 30 | 60 |
| 5 turn-end guard | 3.5 | 120 | 260 |
| 6 slot_run session | 1 | 15 | 60 |
| 7 core_check | 1.5 | 25 | 80 |
| 8 split review-scope tests | 2 | 30 | — (moved) |
| 9 newly named rules file | 1.5 | 30 | 90 |
| 10 doctor row | 0.5 | 5 | 15 |
| 11 slot-run diagnostic and split | 2 | 30 | 40 |
| 12 read-by narrowing | 1.5 | 30 | 40 |
| 13 gate A | 1.5 | — | — |
| 14 spike: pinned release | 3.5 | — | — |
| 15 shellwise skeleton | 1 | 80 | 40 |
| 16 installer | 5 | 340 | 560 |
| 17 launcher, hooks | 2.5 | 70 | 200 |
| 18 rule checker | 3 | 150 | 230 |
| 19 override, rows | 2 | 60 | 150 |
| 20 mem-check | 4 | 280 | 360 |
| 21 safe-edit | 3 | 220 | 300 |
| 22 gate B | 1.5 | — | — |
| 23 guard hand-over | 6 | 160 | 180 |
| 24 drift-lint | 2 | 60 | 80 |
| 25 platform module | 3 | 120 | 60 |
| 26 removal | 4.5 | 150 | 200 |
| 27 wording | 1.5 | 60 | 60 |
| 28 stub, real pin | 3.5 | — | 220 |
| 29 docs | 2 | — | — |
| 30 release review | 2 | — | — |
| 31 gate C | 1.5 | — | — |
| **Total** | **76.5** | **about 2,525** | **about 4,055** |

About 9,000 lines move with `git mv` and are not counted; about 4,500 lines are deleted (the deny
half of the guard, its tests, the `.ps1` files).

Against the decisions doc: its 42 h covered 3a and 3b, and 3a's plan priced 40.25 h, so the doc left
about 21 h of D2–D4 and D8 work for 3b. The same items price here at about 43 h (Tasks 14–28 without
the backlog: the installer's trust checks, the commit-rule move the spec did not price, the rule
checker on `explain`/`check`, the platform module, the hand-over kept consistent at every step); the
owner's added scope (coordinate, state, turn-end, backlog) adds about 24 h; the gates, spikes and
the release review about 10 h. Phase 2's plan ran at 2.7 times the spec's hours and phase 3a's at
1.9.

**Simpler version, about 46 h, 1,515 code lines, 2,435 test lines:**
- **What it keeps:** PR A whole; mem-check and safe-edit in Python (the `.ps1` tools stay in
  rigger-windows beside them); drift-lint to core (Task 24) and the platform module (Task 25), each
  with its rigger-windows manifest edit; the wording (Task 27, after 25); Task 28 for the turn-end
  guard only. rigger-windows keeps only the guard, the test-evidence gates, gate-lab and the `.ps1`
  tools.
- **What it drops, with what each saves:**
  - the shellwise module and its spike (Tasks 14–19, 17 h, 700 and 1,180 lines): no guard on macOS
    and Linux; the Python guard stays the Windows filler;
  - the guard hand-over (Task 23, 6 h, 160 and 180 lines): the test-evidence gates and gate-lab stay
    in rigger-windows with the guard they live in;
  - the removal (Task 26, 4.5 h, 150 and 200 lines) and half of the docs and stub work (3 h, 60
    test lines): rigger-windows stays one more release.
- It keeps more than the decisions doc's simpler version (platform module and mem-check only): the
  harness work is the owner's added scope and does not depend on the guard.

## Open risks

1. **The side-session marker may not exist** (Task 1). The guard then holds any non-coordinator
   session's turn end while its own gate or test command runs (question 2).
2. **`background_tasks` is an undocumented field.** A CLI that drops it makes the guard allow (fail
   open); Task 5's tests pin the field so the next CLI update shows it.
3. **The pinned rule set is older than the rule map.** Task 14 lists every row that differs; a weaker
   row is in the release notes until the pin bump.
4. **The first session after the update runs unguarded until the download ends.** It says so.
5. **GitHub release downloads can be blocked** on a company network. The slot stays unfilled with the
   reason; the override names a binary installed another way.
6. **`gh` may be slow or absent.** `state.py check` is bounded and caches only merged PRs; without
   `gh` the PR check is skipped with one line.
7. **The commit row now logs denied commands too** (D79). gate-lab's numbers before and after the
   move are not comparable for commits the guard denied; the release notes say so.
8. **WinError 32 is not fixed**, only made explainable (D92).
9. **Task 23 is large** (six moves, a default switch, a deletion and the wiring) because a smaller
   cut leaves rigger-windows running a moved script. It commits in three steps, and the coordinator
   may give it two sessions in a row (Steps 1–2, then 3–5) on the same branch.
