# Modular architecture, phase 3a: rigger-review, rigger-testing and the module gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The reviewer and the testing skill become two installable modules that fill their slots,
`--changed-modules` becomes the gate for a change inside module folders, full-risk changes get a
second review round, every hook's latency is measured on three systems, and the phase 2 backlog is
closed.

**Status:** revision 2, after an independent plan review and the coordinator's rulings (under
"Rulings on the plan's questions"); written on `85ca652` (release 0.6.0 plus the phase 3
decisions doc).

**Architecture:** Two new plugin folders take files moved from rigger-core with `git mv`: the
reviewer agent, its contract fixtures and a new `review` skill with checklists go to the review
module; the testing skill, the weak-test guard, its two hook handlers and its eval go to the testing
module. rigger-core keeps the tools that define the contracts (`review_scope.py`, `slot_check.py`,
`slot_run.py`); `review_scope.py` gains skip reasons and per-path rules. A committed "read by" map
lets `run_tests.py --changed-modules` see which tests read another module, and `gate.py
--changed-modules` turns that mode into a gate with a status file. Two pull requests: the gate,
the measurement and the backlog first; the modules second.

**Tech Stack:** Python 3.9+ stdlib only (through `bin/py.sh` with `-I -X utf8`), POSIX `sh`
launchers, `unittest`, `git`, `plugins/rigger-rules/tests/stub_api.py` for `claude -p` runs, GitHub
Actions for the macOS and Linux latency numbers.

**Spec:** `docs/superpowers/specs/2026-11-23-modular-architecture-design.md` §13 "Phase 3" with
§7.2, §10.4, §10.5, §12 and §15, as amended by
`docs/superpowers/specs/2026-11-25-phase-3-decisions.md` (the decisions doc; it wins over the spec).
This plan covers the decisions doc's 3a scope: its D1 first bullet, D5, D6 and D7. Its D2, D3, D4
and D8, rigger-shellwise, rigger-platform, mem-check and the removal of rigger-windows are 3b.
The owner's decisions are cited as "decisions doc D<n>"; this plan's own start at D54.

## Global Constraints

**Scope and promise**
- 3a changes behaviour only as listed under "Behaviour changes". Nothing is removed from the
  marketplace. rigger-windows' code, hooks and rules are not changed; Task 6 may split one of its
  test files.
- Base: `85ca652`. Each pull request goes to `main` and is squash-merged after CI passes. Merging
  and the release are the owner's steps.
- Public repository: nothing under `plugins/`, `scripts/`, `tests/`, `docs/` or `.github/` names a
  company, client, person or private repository, or holds an absolute local path. New plugin
  manifests copy `license`, `homepage`, `repository` and `author` from rigger-core's `plugin.json`.
- **Spelling of the new plugins.** Wherever a `:` or a `plugins/` path follows, this plan writes
  `rigger-REVIEW` and `rigger-TESTING`, so `tests/test_plugin_refs.py` passes on this commit. The
  folders, manifests and every file the tasks write use the lower-case name. In plain text the
  plugins are "the review module" and "the testing module".
- Code, identifiers, comments and commit messages in English. Conventional Commits, no attribution
  lines.

**Runtime** (unchanged from phase 2)
- Python floor 3.9: no `match`, no runtime `X | Y` unions, no 3.10+ stdlib calls.
- Hooks run `py.sh` with `-I -X utf8`; every hook handler exits 0; hook output is LF bytes through
  `sys.stdout.buffer`. Launchers stay POSIX `sh`.
- Files are read as bytes and decoded as UTF-8 explicitly; every write passes `encoding="utf-8"`
  or writes bytes.
- A plugin never reaches a sibling plugin by relative path. The review module reaches rigger-core's
  tools through the `Plugin root:` line of rigger-core that the rules print, or through
  `installed_plugins.json` `installPath`. A repository-level test under `tests/` may read every
  plugin.
- `bin/py.sh` copies stay byte-identical (`tests/test_repo.py:191`). The testing module gets a copy
  (its hooks run `py.sh`); the review module ships no hook and gets none.
- A path that only has to be absolute is made with `abspath`, never `Path.resolve()`.

**Tests and runs**
- Unit tests of `tools/<tool>/<name>.py` live in `tools/<tool>/tests/test_<name>.py`. Every new
  test gets one red run on its own assertion, named in the task's report.
- **Full-risk checks** of this plan, broken once per branch, table row, stage and wiring:
  - the read-by selection and its lint (Task 1);
  - every refusal and exit path of `gate.py --changed-modules`, the WSL leg's handed-over file
    list, and the mode check of `gate.py --verify` (Task 2);
  - the identity check in `resolve_program` and `searchable` (Task 4: command execution);
  - the lookup, bound and fallback of the rules render in `lessons_batch.py` (Task 5: command
    execution);
  - every skip reason of `review_scope.py`, the test-file rule, the validation of `review.paths` in
    `config.py`, the project-level trust rule and the read of project rules files at base
    (Task 8: trust, and git run on paths from config);
  - the new `coverage-test-skipped` refusal of `slot_check.py reviewer` (Task 9);
  - the wiring of the moved weak-test guard (Task 11, with its real-session proof in Task 14).

  3a adds no leak-guard code. Shared infrastructure (the latency script, the timing work, the
  install-hint notice) gets one mutant per branch up to 5 per check, the rest listed in the report.
- Run each Python mutant with `-X pycache_prefix=<dir per mutation>`, on a scratch copy without
  `.git`.
- `run_tests.py` strips `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`; a test that needs one sets
  it itself. A test that needs a repository builds its own with `git init` in a temp folder.
- Git Bash `sh.exe` started from Python re-parses its command line: no quote or glob characters as
  arguments, or put them inside an `sh -c` string.
- A change to a rules file, a skill, an agent or a command file runs
  `plugins/rigger-rules/tests/test_plugin_rules.py` and
  `plugins/rigger-windows/tools/drift-lint/tests/test_drift_lint.py` in the same round as the edit.
- A test that adds or removes a hook handler lists `tests/test_repo.py` (`DuplicateHookTests`).
- `scripts/run_tests.min` reads 75. Only Task 7 and Task 16 raise it, each to the count
  `run_tests.discover` prints at that commit (the file is a minimum, so a task that adds a test file
  does not need to touch it).
- Gate: `python -I scripts/gate.py` in the background, started on Windows with the full path of the
  installed Python (the Store alias fails two `test_slot_run.py` tests, see Baselines). Read the
  `status.txt` its first line names. Between fixes `python -I scripts/run_tests.py --fix-round`,
  with `--no-claude` when no `claude` run is allowed. Before a pull request, `gate.py --verify`.
  Every task here touches a shared path or rigger-core, so both pull requests take the full gate;
  `gate.py --changed-modules` is first usable by the pull request after 3a.

**Real `claude` runs (Tasks 17 and 14)**
- A fresh temp `CLAUDE_CONFIG_DIR` per experiment; `--plugin-dir` runs also set
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to scratch, leave
  `RIGGER_LESSONS_BATCH` unset and name the same roots in `RIGGER_MODULE_DIRS`. The child drops
  `CLAUDE_*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`, keeps `CLAUDE_CODE_CHILD_SESSION`, then sets
  `ANTHROPIC_BASE_URL` to the stub. Prove the isolation with a canary first. Record
  `claude --version` and `command -v claude` before the first run; bounded timeout, `Popen`
  streaming stdout. Scratch under `<temp>/rigger-p3/<task>/`.
- A `claude plugin …` command (Task 17) runs under its own temp `CLAUDE_CONFIG_DIR` with
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` in scratch, one config dir per question, never the real one.
- Any eval run passes `--no-publish`, runs on the copy `scripts/eval_stage.py` makes (a case of a
  plugin with `dependencies` loads them in `plugins:`), with `--keep-temp`, and is scored only
  through `scripts/eval_summary.py`.
- The one real-model run (Task 14 Step 5) is a subagent of the builder's own session, not
  `claude -p`, and costs plan usage only.

**Docs and sweep**
- Before committing a doc, `python -I scripts/sweep.py --no-denylist <file>` prints
  `# files 1, findings 0`, and `python -I -X utf8 tests/test_plugin_refs.py` passes. Result files
  write temp paths as `<temp>` and the home as `<home>`.

## Behaviour changes

The release notes list each one.

1. The `reviewer` slot defaults to `rigger-REVIEW:reviewer` and the `testing` slot to
   `rigger-TESTING:testing` (D57). The reviewer agent, the testing skill and the commit-time
   weak-test guard now come with those two plugins.
2. With either plugin not enabled while its slot is at its default, every session start, clear and
   compaction prints one line naming the install command, and doctor shows a `MISSING` row (D57).
   With the `reviewer` slot empty, the orchestrator reports the review as blocked with that
   command; it never skips the review (D65).
3. `review_scope.py` marks binary, generated, vendored and lockfile paths `skip: <reason>`, never a
   test file without a path rule; a file added or turned binary in the diff reads
   `review: binary added` (D60).
4. `review.paths` in config has an effect: per-path rules files for the review skill, and named
   skips (D61). A project file's entries are read at the reviewed diff's base revision.
5. `slot_check.py reviewer` refuses a reply that skips a test file `review_scope.py` lists as
   `review` (D62).
6. `python -I scripts/gate.py --changed-modules` is a gate for a change inside module folders, and
   `gate.py --verify` names the mode and refuses a changed-modules status for a change that holds a
   shared path (D59).
7. A full-risk change gets a second, fresh review round after the first fix round (D65).
8. The background lessons batch reads the rendered rules text, as the interactive tools do (D68).

## Baselines measured for this plan

Every number below comes from a command run for this plan on `85ca652`, Windows 11, Git Bash,
Python 3.12.10 (full install) unless named, git 2.53.0.windows.1. `<repo>` is the worktree.

```text
$ git ls-files | wc -l
545
$ run_tests.discover(ROOT), per folder
75 files: tests/ 15, rigger-core 33, rigger-windows 10, rigger-lessons 8, rigger-rules 5, rigger-wp 2, rigger-talk 1, rigger-panel 1
$ cat scripts/run_tests.min
75
$ hook handlers per plugins/*/hooks/hooks.json (rigger-panel's file is a `modules` list)
rigger-core 23, rigger-windows 10, rigger-lessons 5, rigger-rules 2, rigger-talk 1, rigger-wp 1, rigger-panel 0: 42
$ grep -n weak_test_guard plugins/rigger-core/hooks/hooks.json
94 (Bash, if Bash(*commit*)), 100 (PowerShell, if PowerShell(*commit*)); the spec's hooks.json:76-87 is now :91-102
$ assemble.py --check --modules plugins/*/   (7 roots in RIGGER_MODULE_DIRS, empty config dir)
# external: superpowers
# check: modules 7, rules files 18, findings 0
$ assemble.py --render
28184 bytes; 11 trigger rows; "| reviewer | agent | rigger-core:reviewer | by-rule | default |", "| testing | skill | rigger-core:testing | — | default |"
$ the same on a scratch copy whose rigger-defaults.json names the two new plugins (not present)
"| reviewer | — | — | — | default |", "| testing | — | — | — | default |", 0 "rigger: " notices, the testing trigger row gone
--check: "# external: rigger-testing", "# external: superpowers", findings 0
$ wc -l (files that move)
skills/testing/SKILL.md 159; tools/weak-test-guard/weak_test_guard.py 1582, tests/test_weak_test_guard.py 1727; agents/reviewer.md 30;
tests/contract/reviewer/ 26 files; evals/testing-skill-loads/ 4 files
$ grep -c CLAUDE_PLUGIN_DATA plugins/rigger-core/tools/weak-test-guard/weak_test_guard.py
0   (the guard keeps no data folder, so the move needs no data migration)
$ wc -l (files that change)
review_scope.py 260 (tests 625), run_tests.py 786, gate.py 417, orchestrating-subagents/SKILL.md 161, coordinate.md 136
$ timings, each file alone, HARNESS_NO_CLAUDE=1
test_assemble.py           84 tests, 45.3 s (limit 120; the decisions doc quotes 54 s in a loaded leg)
test_weak_test_guard.py    89 tests, 120.9 s (limit 300)
test_slot_run.py           41 tests, 33.2 s FAILED (errors=1: cleanup PermissionError, WinError 32, on the temp repo), then 30.6 s OK, 32.1 s OK
                           (P2-E14 measured 79 s for it in a loaded gate)
test_slot_run.py, Store Python 3.11.9 alias     FAILED (errors=2: OSError Errno 22 starting the WindowsApps python.exe)
test_review_scope.py       23 tests, 8.8 s OK (skipped=1)
$ sh -c 'sh -c "exit 0"' timed 5 times (Git Bash)
62, 61, 60, 57, 53 ms
$ wsl -e git -C /mnt/<d>/<repo> merge-base main HEAD      (this worktree; its .git file names the git dir by a drive path)
fatal: not a git repository: <repo>/<main checkout>/.git/worktrees/wt-design   exit 128
$ cross-module text in test folders (65 .py files under plugins/*/tests and plugins/*/tools/*/tests)
23 hold a "plugins" literal, `plugins/*` or PLUGINS; 12 of the 15 tests/test_*.py name a module or iterate plugins;
helpers read other modules too: doctor_sh_harness.py (rigger-windows, rigger-rules, rigger-lessons), stub_api.py imported from rigger-rules' tests
```

Tests that pin what this plan moves or rewords (`git grep`):
`tests/test_repo.py:29` (`PLUGINS_WITH_PY_SH`), `:553-555` (`moved + 15`, two
`weak_test_guard.py` handlers), `scripts/run_tests.py:97` (the guard's 300 s limit),
`plugins/rigger-rules/tests/test_plugin_rules.py:38` (`TESTING_SKILL`) and `:424-451`
(`TestingSkillTriggerTests`, the rendered cell `rigger-core:testing`),
`plugins/rigger-core/tests/test_core_wiring.py:21` (`REVIEWER`), `:213-242` (reviewer text),
`:221` and `:258` (the Risk-full and two-reviewer sentences),
`plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py:68-89` (step 4 and 5),
`tests/test_assembly_golden.py:63-75` (`SLOT_ROWS`) and `:172` (`ALLOWED`),
`plugins/rigger-core/tools/assemble/tests/test_assemble.py:256,844,884-909,1013-1062,1148,1166`
and `test_stub_session.py:452` (default slot rows read from the real defaults file). The fixture
dictionaries in `test_config.py:20-24`, `test_render.py:20`, `test_slots.py` and
`test_doctor_sh_plugins.py:395` carry their own defaults and stay.

Text that names the moved items: `plugins/rigger-core/README.md:21`,
`skills/stack-tooling/SKILL.md:75` ("how to test is in `rigger-core:testing`"),
`rigger-defaults.json:4,8`. `orchestrating-subagents/SKILL.md:14` holds "one review per plan, plus a
per-task review for security, data, money and shared infrastructure", `:151-153` the two fresh
reviewers for a Critical on a Risk-full change, `:154` the re-review; `coordinate.md:74-76` the
`move <part> reviewed` step.

## Review Focus

1. **rigger-core updated, the two new plugins not installed.** A person expects to be told, not to
   lose the reviewer, the testing row and the commit guard in silence; the scratch render above
   shows that today's D34 gives silence. Test: Task 12,
   `test_default_slot_without_its_plugin_names_the_install_command`.
2. **A diff that adds a generated marker to a hand-written file, or drops new code into a new
   `vendor/` folder.** A person expects those files reviewed: a skip that the reviewed diff itself
   creates would hide it. Test: Task 8, `test_a_skip_the_diff_creates_is_reviewed`.
3. **A project `.claude/rigger.json` changed in the reviewed diff to skip or re-rule a path.** A
   person expects the review to use the base revision's entries, with a line saying the file
   changed. Test: Task 8, `test_project_entries_come_from_base`.
4. **A test file that also looks generated, vendored or like a lockfile**
   (`vendor/pkg/test_x.py`, `tests/fixtures/package-lock.json`). A person expects it reviewed unless
   a path rule names it with a reason. Test: Task 8,
   `test_test_files_are_never_skipped_without_a_path_rule`; Task 9,
   `test_a_skipped_test_file_is_refused`.
5. **A module-only change to a file another module's test reads** (rigger-rules'
   `tests/stub_api.py`, read by two rigger-core tests). A person expects `--changed-modules` to run
   those readers, or the gate it backs to fail. Test: Task 1,
   `test_known_readers_run_on_a_change_to_what_they_read`.
6. **`lessons_batch.py` with no rigger-core `installPath`, a stale one, or an assembler that
   hangs.** A person expects the batch to go on with raw text and one log line, inside its bound.
   Test: Task 5, `test_render_failures_fall_back_to_raw_text_in_time`.
7. **A large binary file in the diff, or a source file the diff gives a NUL byte.** A person
   expects an existing binary skipped without being read whole, and a file that became binary in
   this diff listed for review. Test: Task 8, `test_binary_detection_reads_a_bounded_prefix` and
   `test_a_skip_the_diff_creates_is_reviewed`.
8. **`gate.py --changed-modules` started from a worktree on Windows.** WSL git cannot open it
   (Baselines). A person expects the WSL leg to run from the host's file list, never to call git.
   Test: Task 2, `test_wsl_leg_gets_the_list_and_runs_no_git`, and Task 2 Step 5's real run.
9. **An orchestrator with an empty `reviewer` slot.** A person expects the review step reported
   blocked with the install command, never skipped. Test: Task 13,
   `test_an_empty_reviewer_slot_blocks_the_review`.

---

## Decisions this plan makes

Phase 2 used D25–D53.

| # | Decision | Why |
|---|---|---|
| D54 | Two pull requests. (1) Tasks 17 and 1–7 on `feature/phase-3a-opener`: the spikes, the read-by map, `gate.py --changed-modules`, the hook latency measurement and the phase 2 backlog. (2) Tasks 8–16 on `feature/modular-phase-3a`, cut from `main` after (1) merges: the two modules, the cutover and the second review round. | (1) touches only shared files and rigger-core internals, (2) changes what a person installs. Task 4 edits `review_scope.py`, which Task 8 edits again, so (1) merges first. |
| D55 | The testing module takes only rigger-core's parts in 3a: the `testing` skill, the weak-test guard with its two handlers, and the `testing-skill-loads` eval. rigger-windows' test-evidence shadow gates (`stop_shadow.py`, `turn_evidence.py`, the commit rule in `shell_guard.py`), `tools/gate-lab/` and `tools/safe-edit/` (with the Python port of decisions doc D4) move in 3b. | Decisions doc D1: 3a removes nothing and changes no installed plugin; anything sourced from rigger-windows changes what a rigger-windows user has (the shadow rows' data folder moves with `CLAUDE_PLUGIN_DATA`, spec §13 "Gates file"). The commit rule lives inside `shell_guard.py`, which 3b deletes; extracting it twice is waste. The safe-edit references (`tests.md:13`, `defects.md:37`, `testing/SKILL.md:85`, "needs rigger-windows") are reworded once, in 3b. |
| D56 | Layout. Both modules: `.claude-plugin/plugin.json` with `dependencies: ["rigger-core"]`, `rigger-module.json` (schema 1, layer `general`, no rules), `README.md`, a marketplace entry. Review module: `fills: "reviewer"`, `provides.reviewer: {kind: agent, target: rigger-REVIEW:reviewer, model: by-rule}`, one trigger row `rigger-review.review` (before: "reviewing a change, a branch or a PR, or verifying an agent's claim", load `rigger-REVIEW:review`), `tests: {paths: ["tests/"], contract: ["reviewer"]}`; `agents/reviewer.md`, `skills/review/SKILL.md`, `checklists/{python,typescript,php,shell}.md`, `tests/contract/reviewer/`. Testing module: `fills: "testing"`, `provides.testing: {kind: skill, target: rigger-TESTING:testing}`, no trigger row (core's `rigger-core.testing` row loads `slot:testing`), `tests: {paths: ["tools/*/tests/", "tests/"], contract: []}`; `skills/testing/`, `tools/weak-test-guard/`, `hooks/hooks.json`, `bin/py.sh`, `evals/testing-skill-loads/`. Neither gets `core_check.sh`: they print no rules. | Spec §6.3 and §13. The dependency makes `--changed-modules` run their tests on a core change. Whether Claude Code also installs rigger-core with them is spike question (b) of Task 17; the migration text follows its answer. |
| D57 | `rigger-defaults.json` sets `reviewer` to `rigger-REVIEW:reviewer` and `testing` to `rigger-TESTING:testing`. `slots.py` gets `INSTALL_HINT_SLOTS = ("reviewer", "testing")`: when such a slot's entry comes from level 1 alone and its plugin is not enabled, the slot stays empty and the session gets one notice, `rigger: slot <path> is empty: install <plugin> (<install command>)`, for `testing` followed by `; the commit-time weak-test guard is off`, at `startup`, `clear` and `compact` with part 1's notices. `<install command>` is the form Task 17 (a) observed, with `<plugin>@<marketplace>`, `<marketplace>` being rigger-core's own (`rigger` for a directory or `--plugin-dir` install). Doctor's slot row for it becomes `MISSING` with that command as its fix. When D34's fallback walk from a level above 1 lands on such a default whose plugin is not enabled (a stale `rigger-core:reviewer` override), D34's line gets `; install <plugin> (<install command>)` appended. Every other level-1 default keeps D34's silence. | The scratch render shows today's rule empties both slots and drops the testing row with no line. Superpowers is optional, these two are not. A stale override would otherwise get only "the slot is empty". |
| D58 | The read-by map is `tests/read_by.json`: `{"format": 1, "readers": {"<test file or test folder ending in />": ["<module>", …] or ["*"]}}`. A folder key covers every file in it, helpers included. A discovered `tests/test_*.py` file that the map does not list reads every module (`*`); a module's test file that the map does not list reads only its own module. `--changed-modules` runs, besides today's set (D26), every test file whose entry names a changed module or `*`. `tests/test_read_by.py` derives reads from the text of every `.py` file under a discovered test folder: R1 `plugins/<module>`; R2 a quoted module name; R3 `plugins/*`, or `module_dirs(`, `iterdir(` or `glob(` on a line that names `plugins` or `PLUGINS` (reads `*`); R4 a file inherits the reads of a helper it imports from its own folder or from `tests/`. Map keys and test paths are compared by path segments (a key `tests/` never matches `tests_x/`). Every derived read must be its own module or covered by the map; the test prints `# read-by: files <n>, reads <m>, undeclared <k>` and fails on `n` 0 or `k` above 0. Its controls: the four known readers of phase 2's list are derived, and a planted file per rule is flagged. | Phase 2's "Review ideas left for phase 3". Text matching over-approximates (a fixture named like a module counts as a read), which only runs more tests. A computed module name escapes it (open risk 1); CI's full gate on every pull request stays the backstop. One central file keeps the map reviewable and makes every change to it a shared change. |
| D59 | `gate.py --changed-modules [--base REV]`: before any leg, the host computes the changed-file list once (`select_tests.changed_files`, host git) and writes it to `<out>/changed-files.txt`, one repository-relative path per line, NUL-free and UTF-8. Legs: `changed-modules` (`run_tests.py --changed-modules --changed-from <that file>`, count `RUN_TESTS_COUNT`), `control`, `sweep` and `sweep-control` as today, then on Windows `wsl` running `run_tests.py --changed-modules --changed-from <the file's /mnt path> --no-claude`. `run_tests.py --changed-from FILE` takes the list from the file and runs no git; a missing, empty-line-only or unreadable file is exit 2. The status records the mode: line 2 is `mode: changed-modules base <rev> merge-base <sha>, modules <names>`, or `mode: full` for a full gate. `--verify` prints the mode (`gate result matches: tree <id> (full)` or `(changed-modules: <names>)`). On a changed-modules status it also recomputes, with host git, the changed paths from the recorded merge base to the working tree and exits 1 with `verify: changed-modules gate, but <path> is shared; run the full gate` when any path is shared (D26). A status with no `mode:` line was written by a full gate before 3a and reads as `full`; a `mode:` line naming anything else exits 1. The mode's exit 2 fails its leg, and the status shows the `changed-modules: shared file …; run the gate` line. `--release` with `--changed-modules` is a usage error. When it is the gate: a pull request whose every changed path is a module path under D26 may take it as its local gate; a release, and any change with a shared path, take the full gate, and `--verify` enforces that before a merge. CI keeps the full gate (D27). | Decisions doc D1. A gate needs a status file, a tree id and the POSIX leg. WSL git cannot open a worktree created on Windows (Baselines), so the WSL leg must not call git (coordinator ruling on review finding I1). A weaker gate must not verify where the full one is required (ruling on I2). |
| D60 | `review_scope.py` verdicts, first match wins: `skip: deleted`; a test file (a path segment `test`, `tests`, `__tests__` or `spec`, or a file name `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`) is `review` unless a `review.paths` skip names it; a `review.paths` skip gives `skip: <its reason> (review.paths)`; `skip: binary` when the first 8,000 bytes of the head blob hold a NUL byte and so did the base blob of the same path; a path added in the diff, or one whose base blob held no NUL byte there, whose head blob does, is `review: binary added` (listed for review, never skipped; a consumer treats every verdict that starts with `review` as to review). The test-file rule above covers only a file whose name matches a test-name pattern; a file that is only under a test folder (`tests/fixtures/logo.png`) is checked for `skip: binary` before it is called a test file; `skip: lockfile` for the file names `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`, `composer.lock`, `Cargo.lock`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Gemfile.lock`, `go.sum`; `skip: vendored` for a path under a segment `vendor`, `node_modules`, `third_party` or `third-party` that already existed at `base`; `skip: generated` when the first 5 lines of the blob hold `@generated`, `DO NOT EDIT` or `auto-generated` at `head` and at `base` (`*.min.js` and `*.min.css` count as generated without a marker, when the path existed at base). Everything else is `review`. Blobs are read through one `git cat-file --batch` process with the hardened git of phase 2, at most 8,000 bytes kept per blob. | Spec §13 and open point 6, settled here. The "existed at base" rule closes Review Focus 2: a skip the reviewed diff creates would let the diff hide itself; the binary skip follows the same rule (coordinator ruling on review finding I6). A binary fixture under `tests/` would otherwise be refused by D62 in every project that has one. `.gitattributes` `linguist-*` attributes are not read: the reviewed diff can change them. |
| D61 | `review.paths` (levels 1–4) maps a glob to a rules file (a string) or to `{"rules": <file>, "skip": <reason>}` with at least one key; at most 50 entries per level, a glob and a reason at most 200 characters; a bad entry is dropped with one notice. Globs match the repository-relative path by segment: `**` any number of segments, `*` and `?` inside one, case-sensitive; the matcher is iterative over (glob segment, path segment) pairs with a visited set, so its work is bounded by their product. A rules file is relative to the repository root (level 4), to the personal config file's folder (level 3) or to the module root (level 2), and must stay inside it at levels 2 and 4. `config.ACTIVE_ABOVE_L1` gains `review`, and `config.validate_level(data, level, where)` becomes the public name of `_validate`. `review_scope.py` reads levels 2 and 3 from the session snapshot (`config.effective` and `config.sources`), found by `--session <id>` or `CLAUDE_CODE_SESSION_ID`; an entry's level is the highest level among its leaves in `config.sources` (measured: a glob set as a string at level 3 and given a `skip` at level 4 has leaves from both). With no snapshot it prints `# review.paths: none (no snapshot)` for those levels. Level 4 is never taken from the snapshot: `review_scope.py` reads `<base>:.claude/rigger.json` with the hardened git and validates it as level 4. A level-4 rules file is read only by `review_scope.py`: its path must be one of the tracked files at base (`git ls-tree -r -z --name-only <base> --`, compared exactly), and its blob is read by object id through the same `git cat-file --batch` process, never by building a command from the path; a path not in that list drops the entry with a notice. When the config file or a rules file changed in `base..head` it prints `# review.paths: project entries read at base; <file> changed in this diff`. With `--rules-out <dir>` (a folder the caller gives), it writes each matched level-4 rules text to `<dir>/rules-<n>.md`; levels 2 and 3 are files on disk. After the path lines it prints `# rules <glob> <file to read> (<level>)` for each entry that matched an in-scope path: the written file for level 4, the file on disk otherwise. Every git call puts `--` before path arguments. The `{scope}` file of `slot_run.py` keeps its format. | Spec §7.2 gives `{glob: rules file}`; the skip form is needed because spec §13 says a test file is skipped only when `review.paths` names it "with a reason". Phase 2's Q3 found `CLAUDE_CODE_SESSION_ID` in every shell tool of a main session. Reading the project file at base means the reviewed diff cannot change what it is reviewed against, whichever folder the session runs in (Review Focus 3); the snapshot holds the session folder's working-tree copy, which need not be base's. The review skill never builds a git command from a config path; the text reaches it as a file (coordinator ruling on review finding I5). |
| D62 | `slot_check.py reviewer` gains the refusal rule `coverage-test-skipped`: a path in `coverage.skipped` that `review_scope.scope()` (with the same `--session` entries) marks `review` and that is a test file. The contract stays `rigger.reviewer/1`; a reply's JSON stays the last fenced `json` block, with no Markdown form (D41, spec open point 3, settled). | Spec §13 "never skips a test file". A stricter check of existing keys, not a new key, so the id is unchanged; the release notes name the rule for external reviewers. |
| D63 | Checklists, first set (open point 8): `python.md` (`.py`), `typescript.md` (`.ts .tsx .js .jsx .mjs .cjs`), `php.md` (`.php`), `shell.md` (`.sh .bash`, and an extensionless file whose first line starts `#!` and names `sh` or `bash`). Each holds at most 25 bullets and none that a linter or `rigger-rules/rules/defects.md` already covers. The review skill loads at most 3, in order of how many `review` paths each matches, ties alphabetical. | Bounded tokens per review; the extension table lives in the skill and a test pins it to the files present. |
| D64 | `agents/reviewer.md` keeps its frontmatter, its edit ban, the report format, severities, `Pre-existing`, `SHORTCUTS` and the contract JSON ending, and gains "Load the `review` skill first". The new `review` skill holds the procedure: run rigger-core's `tools/review-scope/review_scope.py --base <base> --head <head> --rules-out <scratch folder>`; read the files the `# rules` lines name, with the Read tool, and never run git on a path from config; load the checklists of D63; review every `review` path; the behaviour-change section; the closing JSON. | The agent stays the contract's filler; the procedure can grow without touching the agent the contract tests pin. |
| D65 | Decisions doc D5 in text: `orchestrating-subagents/SKILL.md:14` gains "a Risk-full change also gets a second fresh reviewer after the first review's fix round"; the Fix rounds section gains one bullet: the second round's agent of the `reviewer` slot gets the first review's findings with their outcomes and the fix diff, and is asked only for what the first did not find; its Critical and Important findings go through a fix round and a re-review, never a third fresh round. One more bullet, strict dismissal: a finding is marked `disputed` only with a quoted `file:line` that proves it wrong, and one about concurrency, bounds, behaviour or compatibility, authorisation, money or data goes to a fresh reviewer first. `coordinate.md` step 4: for a Risk-full part, `move <part> reviewed` comes after the second round. An empty `reviewer` slot (its `## Slots` row shows `—`): `orchestrating-subagents/SKILL.md` and `coordinate.md` each gain "With the `reviewer` slot empty, the review is blocked: report it as `BLOCKED` with the install line from the session notice; never skip the review or give it to an agent outside the slot." rigger-core ships no reviewer of its own after 3a, so there is no fallback. No `coord_state.py` check. | The owner's decision changes the one-review rule. Strict dismissal is phase 2's first review idea and costs one sentence. The empty-slot rule is the coordinator's ruling on review finding I9: the migration case must not fail open on the step D5 makes stricter. A state-machine check would need a risk field in every part (dropped, see Estimate). |
| D66 | `scripts/hook_latency.py [--runs N] [--json FILE]` (default 20 runs) times every handler of every `plugins/*/hooks/hooks.json`, started the way Task 17 (c) observed Claude Code start a hook on each system (until then the plan assumes `sh -c <command>`; Task 3 uses the observed form), with `CLAUDE_PLUGIN_ROOT`, a scratch `CLAUDE_PLUGIN_DATA` and `CLAUDE_CONFIG_DIR`, from a temp git repository. A `CASES` table gives each (plugin, script) a matching stdin and, for a handler on a tool matcher without an `if`, a non-matching one; a handler with an `if` is reported `not started` for a non-matching call, which Claude Code filters before starting a process (phase 2 baselines). It also times `sh -c 'exit 0'` as the floor. One line per case: `# hook <plugin> <event>:<matcher> <script> <case> median <ms> p95 <ms> floor <ms>`, then `# hooks measured <n> of <total>`, and `# candidate for a Rust port: …` for a handler on the per-command path (PreToolUse and PostToolUse on `Bash`, `PowerShell`, `Write` or `Edit`, and UserPromptSubmit) whose median is over 50 ms. A handler without a case, or `n` below `total`, prints `# hook latency failed: …` and exits 1; otherwise exit 0. `CASES` is keyed by (plugin, script), so moving a handler to another plugin moves its key in the same task. CI runs it on the three systems with `continue-on-error`, like `startup_bench.py`; Task 3 records the numbers in `docs/superpowers/spikes/phase-3a/hook-latency.md`. | Decisions doc D6 and `lessons.md:46-51`. The median includes the floor, since the person pays it; the result file shows both, because the floor alone (53–62 ms on Windows here) is above 50 ms and a Rust port of a script cannot remove it. No local macOS: CI is the only macOS source. |
| D67 | `review_scope.py` gets the public `under_by_identity(path: str, roots: Sequence[os.stat_result]) -> bool` (today's `_same_folder_above`), and `slot_run.resolve_program` refuses a found program that it places under the repository or the cwd, as `_inside` does now. The Windows proof is a test on the admin-share spelling `\\localhost\<drive>$\…` of the temp repository, skipped with its reason only when that share cannot be opened and `CI` is unset (with `CI` set it fails instead, so the Windows CI runner cannot skip it unseen); the macOS proof is the case-twin test, which fails instead of skipping on `darwin` (APFS is case-insensitive by default). | Decisions doc D7 items 1 and 2; P2-RVFIX2 open questions 1 and 2. |
| D68 | `lessons_batch.py` renders the rules before the corpus: it reads `<config dir>/plugins/installed_plugins.json`, takes under the first key starting `rigger-core@` the first `installPath` holding `tools/assemble/assemble.py`, runs `[sys.executable, "-I", "-X", "utf8", <that file>, "--render"]` with the batch's child environment, no shell, a 30 s timeout and 1,000,000 bytes of output read, writes the bytes, `\r\n` normalised to `\n`, to `<run dir>/rules-rendered.md` and passes it to `write_corpus`. On timeout or cap it stops the whole process tree (the `kill_tree` way of `run_tests.py`). Any failure (no list, no key, no file, exit not 0, timeout, over the cap, no output) logs `rendered rules: unavailable (<reason>)` and the batch goes on with raw text. | Decisions doc D7 item 3; P2-F20's open question. The same lookup as the interactive skill (`curate-lessons/SKILL.md:23-26`). |
| D69 | Timing (decisions doc D7 item 4): three full Windows suite runs; any file whose loaded time reaches 60% of its limit in one run is split by test class or sped up until it stays under 50% in three runs. A split keeps every test, gives the parts names that differ by more than case or extension, and leaves `tests.paths` matching. A `TIMEOUTS` entry is raised only with the spawn count that shows it. The `test_slot_run.py` cleanup error is diagnosed in the same task. | Measured: `test_slot_run.py` 79 s of 120 s in a loaded gate (66%), `test_weak_test_guard.py` 121 s of 300 s alone; and one cleanup error in three isolated runs. |
| D70 | The weak-test guard moves with `git mv` and no code change; its handlers keep their text, `if` filters and 20 s timeouts. Its full-risk proof is the wiring, in three parts: a test runs both handlers' commands from the testing module's `hooks.json` on a weakening commit and expects the deny (the command and `CLAUDE_PLUGIN_ROOT`); the D71 contract test pins the `if` filters (which no command run reads); and a stub session in Task 14 shows a real `Bash` `git commit` denied by the module, with `Hooks: PreToolUse:Bash` in the debug log (that Claude Code loads the new `hooks.json` and matches the `if`). | Moved code with its tests; only the place it is wired from is new, and a command run cannot see the `if` filter (review finding I8). |
| D71 | The `testing` contract's commit-guard clause (D47) becomes a check in `tests/test_module_contracts.py` on the default filler only: its `hooks.json` has a `PreToolUse` handler on `Bash` with `if` exactly `Bash(*commit*)` and one on `PowerShell` with `if` exactly `PowerShell(*commit*)`, each running `weak_test_guard.py`. A filler chosen in config is not checked. | A filler from config may be a skill alone; doctor has no reason to refuse that. |
| D72 | The eval control moves with the case it controls. `testing-skill-loads` moves to the testing module's `evals/`. `add-commit-push-chain` stays in rigger-core, and its `no-testing-skill` grader (`max: 0`) moves, with `git mv`, into a new testing-module case `git-question-no-testing/` whose `prompt.md` is that case's prompt. `evals/CASES.md` gets a `## rigger-testing` section with both rows and the control paragraph (the liveness proof is `testing-skill-loads` loading `testing` in the same suite run, which now stages rigger-core as a dependency); the `## rigger-core` section loses them. `scripts/eval_suite_check.py --cases evals/CASES.md` passes for both plugins. | Review finding I4. Left in rigger-core's suite, the control would pass because the skill no longer exists there: a control that cannot fail. |
| D73 | One spike task (Task 17) under isolated config dirs answers, each with a known-answer control: (a) does `claude plugin install` take two plugins in one call; (b) does a plugin's `dependencies` entry install rigger-core with it; (c) how Claude Code on Windows starts a hook command (through `sh -c`, which `sh`, or not). Task 3 uses (c), D57's notice and Task 15's migration text use (a) and (b). | Coordinator ruling on review shortcuts 1, 2 and 4: a command's behaviour enters the plan only after it is observed. |

## Rulings on the plan's questions

The coordinator ruled on 2026-11-25, under the owner's delegation, after an independent plan
review (9 Important, 10 Minor, 4 shortcuts). It accepted all eleven defaults and added rulings on
five findings and the spikes.

1. **rigger-core updated without the two new plugins:** an install line per session event and a
   doctor `MISSING` row, for `reviewer` and `testing` only (D57). Accepted.
2. **The shape of `review.paths`:** a rules-file string or `{rules, skip}` (D61). Accepted.
3. **Project entries of `review.paths`:** read at the reviewed diff's base, never from the session
   snapshot (D61). Accepted.
4. **Skips the reviewed diff creates:** generated, vendored and minified skips need the path, or
   its marker, at base (D60). Accepted.
5. **`coverage-test-skipped` under `rigger.reviewer/1`:** no version bump; the release notes name
   the rule (D62). Accepted.
6. **The safe-edit port and rigger-windows' test tools:** 3b (D55). Accepted.
7. **macOS latency numbers:** a `continue-on-error` CI step on the three systems, in pull request 1
   (D66). Accepted.
8. **Repo-level tests the read-by map does not list:** they read every module (D58). Accepted.
9. **The second review round:** no third fresh round, and no `coord_state.py` check (D65).
   Accepted.
10. **The `test_slot_run.py` cleanup error:** diagnosed in Task 6; a product cause becomes its own
    item outside 3a (D69). Accepted.
11. **`CLAUDE_CODE_SESSION_ID` in a subagent's shell:** measured in Task 14 Step 1, with the
    `--session` sentence as the fallback (D61). Accepted.
12. **Review finding I1, the WSL leg in a Windows worktree:** the WSL leg never runs git; the host
    computes the changed-file list once and hands it over (D59, Task 2).
13. **Review finding I2, verify before a merge:** the status records its mode; `--verify` reports
    it and accepts a changed-modules status only for a change whose every path maps to a module
    (D59, Task 2).
14. **Review finding I5, rules files from project config:** the review skill never builds a git
    command from a config path; `review_scope.py` reads the rules at base and hands them over as
    files (D61, D64, Task 8).
15. **Review finding I6, the binary skip:** needs the file binary at base too; a file added or
    turned binary in the diff is `review: binary added` (D60, Task 8).
16. **Review finding I9, an empty `reviewer` slot:** the review is blocked and reported with the
    install line; there is no fallback reviewer (D65, Task 13).
17. **Spikes:** Task 17, before Tasks 3 and 15, answers the two-plugin install, the dependency
    install and the Windows hook launcher (D73).

## Spec corrections

Task 15 applies each row to the spec file; the second pull request's body carries the table.

| Spec | Correction | Reason |
|---|---|---|
| §7.2 `review.paths` | The value is a rules file or `{rules, skip}`; project entries and their rules files are read at the reviewed diff's base by `review_scope.py`, which hands the rules over as files; rules files resolve per level. | D61 |
| §7.3 step 5 | `reviewer` and `testing` at their level-1 default give an install notice when their plugin is not enabled; a fallback that lands on such a default appends the install command. | D57 |
| §10.3, §10.5 | A verdict may read `review: binary added`; every verdict starting `review` is to be reviewed. New refusal `coverage-test-skipped`; contract id unchanged. | D60, D62 |
| §10.4 `testing` | The commit-guard clause is checked on the default filler only, with its exact `if` filters. | D71 |
| §12 "Gate or not" | `gate.py --changed-modules`, with the read-by map, is the local gate for a module-only change; its WSL leg runs from the host's file list; `--verify` names the mode and refuses a changed-modules status for a change with a shared path; a release takes the full gate; CI is unchanged. | D58, D59 |
| §13 phase 3, rigger-testing | 3a moves only rigger-core's parts; rigger-windows' test-evidence, gate-lab and safe-edit move in 3b. The guard's handlers are `hooks.json:91-102`. The eval control moves with its case. | D55, D72 |
| §13 phase 3, `review_scope.py` | Binary, generated, vendored and minified skips need the path at base; the lists of D60. | D60 |
| §13 phase 3, migration | The install command is the form Task 17 observed. | D73 |
| §15 points 3, 6, 8 | Settled by D62, D60 and D63. | — |

## File map

| Path | Task | Responsibility |
|---|---|---|
| `docs/superpowers/spikes/phase-3a/install-and-launch.md` | 17 | the three spike answers |
| `tests/read_by.json`, `tests/test_read_by.py`, `scripts/run_tests.py` (selection only), `tests/test_run_tests.py` | 1 | the read-by map, its lint, the mode's selection |
| `scripts/gate.py`, `tests/test_gate.py`, `scripts/run_tests.py` and `tests/test_run_tests.py` (`--changed-from` only, after Task 1), `CLAUDE.md` (Gate bullet), `CONTRIBUTING.md` | 2 | the gate mode, the handed-over file list, verify by mode, docs |
| `scripts/hook_latency.py` (with `CASES`), `tests/test_hook_latency.py`, `.github/workflows/test.yml`, `docs/superpowers/spikes/phase-3a/hook-latency.md` | 3 | hook latency |
| `plugins/rigger-core/tools/review-scope/review_scope.py`, `tools/slot-run/slot_run.py`, both test files | 4 | identity check |
| `plugins/rigger-lessons/tools/lessons_batch.py`, `plugins/rigger-lessons/tests/test_lessons_batch.py` | 5 | rendered rules for the batch |
| the test files at or above 60% of their limit (Step 1 names them; candidates from Baselines: `test_slot_run.py`, `test_weak_test_guard.py`), `scripts/run_tests.py` (`TIMEOUTS`), `tests/read_by.json` (a split key follows its file) | 6 | Windows leg timing |
| `tests/read_by.json` (reads added by wave A), `scripts/run_tests.min` | 7 | map catch-up, minimum count |
| `review_scope.py`, `test_review_scope.py`, `plugins/rigger-core/tools/assemble/config.py`, `test_config.py` | 8 | skip reasons, `review.paths`, rules handed over as files |
| `plugins/rigger-core/tools/slot-check/slot_check.py`, `test_slot_check.py`, one new fixture under the review module's `tests/contract/reviewer/` | 9 | `coverage-test-skipped` |
| `plugins/rigger-REVIEW/**` (new; agent and fixtures moved from rigger-core), `plugins/rigger-core/rigger-module.json` (`contract` list), `plugins/rigger-core/rigger-defaults.json` (`reviewer`), `plugins/rigger-core/tests/test_core_wiring.py` (every `REVIEWER` use; `FindingLabelTests` split), `tests/test_assembly_golden.py`, `tests/golden_baseline.py`, the `test_assemble.py` and `test_stub_session.py` pins of the reviewer row, `.claude-plugin/marketplace.json` and the listing in `tests/test_repo.py`, `tests/read_by.json` | 10 | the review module |
| `plugins/rigger-TESTING/**` (new; skill, guard, evals moved), `plugins/rigger-core/hooks/hooks.json`, `plugins/rigger-core/evals/add-commit-push-chain/graders/` (one grader moves out), `evals/CASES.md`, `rigger-defaults.json` (`testing`), `tests/test_repo.py`, `scripts/run_tests.py` (`TIMEOUTS` path), `scripts/hook_latency.py` (`CASES` key), `tests/test_hook_latency.py`, `plugins/rigger-rules/tests/test_plugin_rules.py`, `tests/test_module_contracts.py`, golden and `test_assemble.py` pins of the testing row, `.claude-plugin/marketplace.json`, `tests/read_by.json` | 11 | the testing module |
| `plugins/rigger-core/tools/assemble/slots.py`, `assemble.py` (`_doctor`), `test_slots.py`, `test_assemble.py`, `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `plugins/rigger-core/README.md`, `skills/stack-tooling/SKILL.md` | 12 | install notice, doctor row, wording |
| `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md`, `commands/coordinate.md`, `tests/test_core_wiring.py`, `tools/coord-state/tests/test_coordinate_wiring.py` | 13 | second review round, empty reviewer slot |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py`, `docs/superpowers/spikes/phase-3a/release-review.md` | 14 | stub exit criteria, the guard in a real session, the real review |
| `CHANGELOG.md`, both new READMEs, `CONTRIBUTING.md`, the spec file | 15 | release notes, migration, spec corrections |

## Parallel waves

| Wave | Tasks | Runs after | Why they are file-disjoint |
|---|---|---|---|
| A0 | 17 | — | It writes one result file and scratch config dirs only. |
| A | 1, 2, 3, 4, 5 | 3 after 17 | 1: `run_tests.py`'s selection, `tests/read_by.json`, two test files. 2: `gate.py`, `test_gate.py`, `CLAUDE.md`, `CONTRIBUTING.md`, and `run_tests.py`'s argument parsing for `--changed-from` with two rows in `tests/test_run_tests.py` (other functions and test classes than Task 1's; Task 2 rebases on Task 1 before its commit, the one coupling in this wave). 3: the latency script with `CASES`, its test, the workflow, a result file. 4: `review_scope.py`, `slot_run.py`, their tests. 5: two rigger-lessons files; a read its tests add is declared in Task 7, not here. |
| B | 6 | 1 and 4 | It edits `run_tests.py` (`TIMEOUTS`) after Task 1, `test_slot_run.py` after Task 4, and `tests/read_by.json` for a split file after Task 1. |
| C | 7 | all of A and B | It reruns `tests/test_read_by.py` and declares the reads wave A added (Task 5's tests), then the gate and pull request (1). |
| — | pull request 1 merges; `feature/modular-phase-3a` is cut from `main` | 7 | — |
| D1 | 8, 10 | the merge above | 8: `review_scope.py`, `config.py` and their tests. 10: the new review folder, the core files moved out, the reviewer pins, the listing, `tests/read_by.json`. |
| D2 | 9, 11, 13 | 9 after 8 and 10; 11 and 13 after 10 | 9: `slot_check.py`, its test (which builds its own repository and reads no fixture folder, so it needs no map entry), one fixture in the review module. 11: the testing folder, core's `hooks.json` and one eval grader, `evals/CASES.md`, `hook_latency.py` `CASES` with its test, `test_repo.py`, `test_plugin_rules.py`, `test_module_contracts.py`, `run_tests.py`, `tests/read_by.json`, the listing, and the testing row in the golden and `test_assemble.py` pins (Task 10 is done with all of them). 13: two text files and two test files; `test_core_wiring.py` after Task 10. |
| E | 12 | 11 | `slots.py`, `_doctor`, the doctor test, two text files; it reads the defaults Task 11 wrote. |
| F | 14, then 15, then 16 | all, and 15 after 17 | — |

Parallel: 1–5 (3 after 17); then 8 and 10; then 9, 11 and 13. Serial: 17, 6, 7, the merge, 12,
14, 15, 16. Files with several owners, each in a different wave unless named:
- `scripts/run_tests.py` and `tests/test_run_tests.py`: 1 and 2 in wave A (selection, then
  `--changed-from` parsing, Task 2 rebased on Task 1), then 6 and 11 for `TIMEOUTS`;
- `tests/read_by.json`: 1, then 6, then 7, then 10, then 11 (each task that moves, splits or adds a
  test file keeps its keys alive; a dead key exits the mode with 2);
- `scripts/hook_latency.py` `CASES` and `tests/test_hook_latency.py`: 3, then 11 (the guard's key
  moves to the testing module);
- `evals/CASES.md`: 11 only;
- `review_scope.py`: 4, then 8;
- `rigger-defaults.json`, `tests/test_assembly_golden.py` and `test_assemble.py`: 10, then 11, then
  12;
- `test_core_wiring.py`: 10, then 13, then 14 only on its fallback;
- `.claude-plugin/marketplace.json` and `tests/test_repo.py`'s listing: 10, then 11;
- `scripts/run_tests.min`: 7, then 16.

---

## Pull request 1: the module gate, the measurement and the backlog (waves A0–C)

### Task 17: Spikes — two-plugin install, dependency install, the Windows hook launcher

Run first (wave A0); numbered after the others so earlier task numbers stay as reviewed.

**Files:**
- Create: `docs/superpowers/spikes/phase-3a/install-and-launch.md`
- Scratch: `<temp>/rigger-p3/S17/{a,b,c}/` (one `CLAUDE_CONFIG_DIR` per question, each with its own `CLAUDE_CODE_PLUGIN_CACHE_DIR`)

**Interfaces:**
- Produces: (a) the install command form D57 and Task 15 print; (b) whether the migration needs `rigger-core` named; (c) the launcher `hook_latency.py` uses on Windows.

- [ ] **Step 1: Record** `claude --version` and `command -v claude`; build a scratch directory marketplace with three minimal plugins `s17-base`, `s17-one`, `s17-two` (`s17-two` declares `dependencies: ["s17-base"]`).
- [ ] **Step 2: (a) Two plugins in one call.** Control: `claude plugin install s17-one@<market>` alone, then `installed_plugins.json` lists it. Then, in a fresh config dir, `claude plugin install s17-one@<market> s17-two@<market>`; record the exit code, the output and which plugins the listing holds. If not both, record that one command per plugin is the form.
- [ ] **Step 3: (b) Dependencies.** Fresh config dir: `claude plugin install s17-two@<market>` alone; control: the listing before holds nothing. Record whether `s17-base` is installed and enabled, and the output line that says so.
- [ ] **Step 4: (c) The Windows launcher.** One stub session (`ANTHROPIC_BASE_URL` to `plugins/rigger-rules/tests/stub_api.py`, isolation canary first) with a probe plugin whose `SessionStart` hook prints `$0`, `$BASH_VERSION`, the parent command line (`/proc/$PPID/cmdline` under Git Bash, `ps -p $PPID -o args=`) and whether `SHELL` is set; control: the same probe run by hand through `sh -c` prints `sh`. Record the observed form and read `Hooks: SessionStart` from the debug log. Repeat under WSL for Linux.
- [ ] **Step 5: Write the result file** (`## Question`, `## CLI`, `## Setup`, `## Control`, `## Observed`, `## Decision`; temp paths as `<temp>`), sweep it, commit. `docs(spikes): record plugin install forms and the Windows hook launcher`

### Task 1: The read-by map

**Files:**
- Create: `tests/read_by.json`, `tests/test_read_by.py`
- Modify: `scripts/run_tests.py` (`run_changed_modules`, new `read_by`), `tests/test_run_tests.py`

**Interfaces:**
- Consumes: `classify`, `changed_modules`, `discover`, `module_names` of `run_tests.py`.
- Produces:
  - `READ_BY = "tests/read_by.json"`;
  - `read_by(root: Path) -> Dict[str, FrozenSet[str]]`: test file (repository-relative, `/`) to the modules it reads, `"*"` for all, after folder keys are expanded over `discover(root)`; raises `ValueError` on a missing file, another format, an unknown module or a key that matches no file;
  - `selected_files(root, names: Set[str]) -> List[Path]`: today's selection plus every file whose reads meet `names` or hold `"*"`, plus every unlisted `tests/test_*.py`;
  - in `tests/test_read_by.py`: `derived_reads(root) -> Dict[str, Set[str]]` (R1–R4 of D58).
  - A `read_by` error makes the mode print `changed-modules: read-by map: <error>; run the gate` and exit 2.

- [ ] **Step 1: Write the map.** Run the lint once in a scratch script over the tree and declare every derived read that is not the file's own module. Expected first entries include `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` and `plugins/rigger-core/tools/coord-state/tests/test_coordinate_host_stub.py` reading `rigger-rules`, `plugins/rigger-core/tools/doctor/tests/` reading `rigger-rules`, `rigger-lessons` and `rigger-windows`, `plugins/rigger-rules/tests/test_core_check.py` reading `rigger-windows`. Record the counts `files`, `reads` in the report.
- [ ] **Step 2: Write the failing tests.**
  - `tests/test_read_by.py`: `test_every_derived_read_is_declared` (prints the count line; fails on 0 files); `test_the_known_readers_are_derived` (the four pairs above); `test_each_rule_flags_a_planted_file` (one temp file per R1–R4, each must be derived, and a file with none of them derives nothing); `test_map_names_only_existing_files_and_modules`.
  - `tests/test_run_tests.py` on the D26 fixture repository with a `tests/read_by.json`: `test_known_readers_run_on_a_change_to_what_they_read` (Review Focus 5: a change in `plugins/r/tests/stub.py` runs `plugins/a/tests/test_s.py` declared as reading `r`); `test_a_wildcard_reader_runs_on_any_module_change`; `test_an_unlisted_repo_test_runs_on_any_module_change`; `test_folder_key_matches_by_segment`; `test_a_broken_map_exits_two` (missing file, format 2, unknown module, dead key).
- [ ] **Step 3: Run** both files. Expected: `FAILED`, each new test on its own assertion (or `ImportError` for `read_by`).
- [ ] **Step 4: Implement** `read_by` and `selected_files`; `run_changed_modules` calls `selected_files`. Paths are matched by segments, never `fnmatch`.
- [ ] **Step 5: Run** both files and `python -I scripts/run_tests.py --changed-modules` on the tree. Expected: `OK`, and the mode exits 2 naming `scripts/run_tests.py` (a shared file).
- [ ] **Step 6: Red runs** (full): drop the map lookup (known-readers test red); ignore `"*"` (wildcard test red); treat unlisted repo tests as reading nothing (repo test red); accept a dead key (broken-map test red); compare keys with `str.startswith` (a row `test_folder_key_matches_by_segment` in the run-tests file, key `plugins/a/tests/` against a file under `plugins/a/tests_x/`, goes red); drop each of R1–R4 from `derived_reads` (its planted control red, four mutants); drop R4's helper inheritance on the real tree (the declared-read test reports `undeclared` for the doctor folder, or the planted R4 file). Remove each map entry in turn and run `test_every_derived_read_is_declared`: each removal is red (one mutant per reader row; list the count).
- [ ] **Step 7: Commit.** `feat(tests): a read-by map so --changed-modules runs the tests that read a changed module`

### Task 2: `gate.py --changed-modules`

**Files:**
- Modify: `scripts/gate.py`, `tests/test_gate.py`, `scripts/run_tests.py` (`--changed-from` parsing only; rebase on Task 1 before the commit), `tests/test_run_tests.py` (the `--changed-from` rows), `CLAUDE.md` (the Gate bullet's `--changed-modules` sentence and its "before a merge" sentence), `CONTRIBUTING.md:39-42` and its `--verify` row (`:72`)

**Interfaces:**
- Consumes: `run_tests.py --changed-modules [--base REV] [--no-claude]` (D26 lines and exit codes), `select_tests.changed_files(root, base)`.
- Produces:
  - `run_tests.py --changed-from FILE` (only with `--changed-modules`): the changed list from `FILE`, no git call; exit 2 with `changed-modules: changed list <file>: <reason>; run the gate` on a missing or unreadable file;
  - `gate.py` CLI `--changed-modules` and `--base REV` (default `main`; `--base` alone is a usage error); `build_stages(args, root, out, release=None, claude=True)` builds the D59 legs after writing `<out>/changed-files.txt`;
  - the `mode:` status line; `verify(status_file, root) -> Tuple[int, str]` names the mode and runs the shared-path check of D59.

- [ ] **Step 1: Write the failing tests**, with the leg runner patched (the pitfall of a recursive suite):
  - `tests/test_gate.py`: `test_changed_modules_builds_its_legs` (names and commands; the WSL leg on `nt` only); `test_wsl_leg_gets_the_list_and_runs_no_git` (Review Focus 8: the WSL leg's argv holds `--changed-from` with the `/mnt` path of `<out>/changed-files.txt`, the file holds the host's list, and with `select_tests.changed_files` patched to raise in the leg's run nothing calls it); `test_shared_file_fails_the_gate_with_the_mode_line`; `test_status_records_the_mode` (`mode: full` and `mode: changed-modules …`); `test_verify_names_the_mode`; `test_verify_refuses_a_changed_modules_status_for_a_shared_change` (a fixture repository where the recorded merge base to the tree holds `scripts/x.py`); `test_verify_accepts_a_changed_modules_status_for_a_module_change`; `test_verify_reads_a_status_without_mode_as_full`; `test_verify_refuses_an_unknown_mode`; `test_release_with_changed_modules_is_refused`; `test_base_without_the_mode_is_refused`; `test_a_mode_leg_with_count_zero_fails`.
  - `tests/test_run_tests.py`: `test_changed_from_runs_no_git` (`changed_files` patched to raise, a list file naming `plugins/a/x.py`: module `a` selected, exit 0); `test_changed_from_missing_file_exits_two`.
- [ ] **Step 2: Run** both files. Expected: `FAILED` or `unrecognized arguments`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Docs.** `CLAUDE.md` Gate bullet: "Before the gate, a change inside one module's folder may run `python -I scripts/run_tests.py --changed-modules` as a fast pre-check; a pull request whose every changed path lies in module folders may take `python -I scripts/gate.py --changed-modules` as its gate; a release, and a change with a shared path, take the full gate." Its merge sentence: "before a merge run `python -I scripts/gate.py --verify <status.txt>`: it names the gate's mode and refuses a changed-modules result once the change holds a shared path." `CONTRIBUTING.md:39-42` says the same and names `tests/read_by.json` as the file to update when a test starts reading another module; its `--verify` row names the mode check.
- [ ] **Step 5: Run** both files. Expected: `OK`. Then the real mode once from this worktree on Windows, with a scratch commit that touches only `plugins/rigger-talk/README.md` on a throwaway branch (deleted after): the status holds `PASS wsl` and `mode: changed-modules`; record the PASS lines. Remove the branch.
- [ ] **Step 6: Red runs** (full): each refusal returns 0 instead (its test red); the WSL leg without `--changed-from` (no-git test red); `--changed-from` falling back to git when the file is missing (missing-file test red); the mode line dropped (status test red); `verify` skipping the shared-path check (shared-change test red); `verify` treating an unknown mode as full (unknown-mode test red); the exit-2 leg judged as passed (shared-file test red); a count of 0 judged as passed (count test red).
- [ ] **Step 7: Sweep and commit.** `python -I scripts/sweep.py --no-denylist CLAUDE.md CONTRIBUTING.md`. `feat(gate): gate.py --changed-modules for a change inside module folders`

### Task 3: Hook latency on three systems

**Files:**
- Create: `scripts/hook_latency.py`, `tests/test_hook_latency.py`, `docs/superpowers/spikes/phase-3a/hook-latency.md`
- Modify: `.github/workflows/test.yml` (a `hook latency` step with `continue-on-error: true` after the gate in the Linux job and after `startup bench` in the macOS and Windows jobs: `python -I -X utf8 scripts/hook_latency.py --runs 20`)

**Interfaces:**
- Consumes: Task 17 (c), the launcher Claude Code uses on each system.
- Produces: the D66 command line and lines; `CASES: Dict[Tuple[str, str], Tuple[bytes, Optional[bytes]]]` keyed by (plugin, script file name); `measure(command: str, stdin: bytes, env: dict, cwd: str, runs: int) -> List[float]`; `nearest_rank(values, pct) -> float`.

- [ ] **Step 1: Write the failing tests** with a fixture `plugins/` of two plugins: `test_slow_handler_reads_slower_than_fast` (a `sh -c 'exit 0'` handler and a Python handler that sleeps 0.2 s; known answer); `test_if_handler_non_match_is_not_started`; `test_handler_without_a_case_fails_the_run` and `test_no_hooks_file_fails_the_run` (each prints the failed line and exits 1); `test_handlers_start_through_the_observed_launcher` (the argv `measure` builds matches Task 17 (c)); `test_candidate_line_only_on_the_per_command_path`; `test_nearest_rank`; `test_every_real_handler_has_a_case` (the real tree: 42 handlers, `CASES` covers every (plugin, script)).
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_hook_latency.py -v`. Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.** The weak-test guard's matching case is a staged commit that deletes a test function in the temp repository; the rules parts and `core_check.sh` run with a config dir laid out as an install, as `startup_bench.py` lays it out.
- [ ] **Step 4: Run** the tests. Expected: `OK`. Then run the script locally on Windows and under WSL (Linux on its own file system, not `/mnt/<drive>`); the last line `# hooks measured 42 of 42` on both.
- [ ] **Step 5: Red runs** (shared infrastructure): count a non-match of an `if` handler as started (its test red); drop the missing-case failure (red); exit 0 on a failed run (both failure tests red); report the median of the floor run (slow-handler test red).
- [ ] **Step 6: Write the result file** with headings `## Question`, `## Setup`, `## Control` (the fixture's slow and fast handlers), `## Observed` (one table per system, the macOS and CI Linux rows from the CI log of pull request 1, filled in Task 7), `## Candidates` (handlers over 50 ms on the per-command path, for phase 4), `## Decision`. Temp paths as `<temp>`.
- [ ] **Step 7: Sweep and commit.** `feat(scripts): measure every hook's latency on matching and non-matching calls`

### Task 4: File identity in `resolve_program`

**Files:**
- Modify: `plugins/rigger-core/tools/review-scope/review_scope.py`, `plugins/rigger-core/tools/slot-run/slot_run.py`
- Test: `tools/review-scope/tests/test_review_scope.py`, `tools/slot-run/tests/test_slot_run.py`

**Interfaces:**
- Produces: `review_scope.under_by_identity(path: str, roots: Sequence[os.stat_result]) -> bool`; `searchable` calls it; `slot_run._inside(path, root)` also returns True when `under_by_identity` places `path` under `root`.

- [ ] **Step 1: Write the failing tests.** `test_slot_run.py`: `test_absolute_program_spelled_in_another_case_is_refused` (case twin of the repository on a case-insensitive file system); `test_absolute_program_through_the_admin_share_is_refused` (`nt` only). `test_review_scope.py`: `test_admin_share_entry_is_not_searched` (`nt`); in `case_twin`, `self.fail` instead of `skipTest` when `sys.platform == "darwin"`; the admin-share rows `self.fail` instead of skipping when the share cannot be opened and the `CI` variable is set.
- [ ] **Step 2: Run** both files on Windows and under WSL with `TMPDIR` on `/mnt/<drive>`. Expected: the new rows red on their assertions where they run.
- [ ] **Step 3: Implement** (rename `_same_folder_above` to `under_by_identity`, update its one caller and the slot_check private-use test if it names it).
- [ ] **Step 4: Run** both files. Expected: `OK` on Windows (admin-share rows ran, not skipped) and WSL drvfs.
- [ ] **Step 5: Red runs** (full): `under_by_identity` returns False (both admin-share rows red on Windows, case rows red on WSL drvfs); `_inside` without it (the slot_run rows red); `searchable` without it (the review_scope row red).
- [ ] **Step 6: Commit.** `fix(rigger-core): compare a command's program with the repository by file identity`

### Task 5: The lessons batch reads the rendered rules

**Files:**
- Modify: `plugins/rigger-lessons/tools/lessons_batch.py` (`_run_locked` and a new `render_rules`)
- Test: `plugins/rigger-lessons/tests/test_lessons_batch.py`

**Interfaces:**
- Consumes: `batch_model.write_corpus(run_dir, config, rules_text)`.
- Produces: `render_rules(config: Path, run_dir: Path, env: dict, log) -> Optional[Path]` (D68); `RENDER_TIMEOUT = 30`, `RENDER_CAP = 1_000_000`.

- [ ] **Step 1: Write the failing tests** with a fake `installed_plugins.json` and a fake core holding a stub `assemble.py`: `test_render_found_through_install_path_enters_the_corpus` (the corpus holds the stub's text under the not-an-edit-target mark); `test_render_failures_fall_back_to_raw_text_in_time` (Review Focus 6, one row per cause: no list, no `rigger-core@` key, no file, exit 3, a stub that sleeps past a patched 1 s timeout, output over a patched cap, empty output; each logs `rendered rules: unavailable (<reason>)`, the corpus holds the raw files only, and the call returns inside timeout plus 5 s); `test_render_runs_without_a_shell` (a `installPath` holding `;` and spaces); `test_rendered_file_holds_no_carriage_return` (a stub that writes `\r\n` through a text-mode pipe); in the timeout row, the stub starts a grandchild that sleeps, and the test checks by its pid that it is gone after the call.
- [ ] **Step 2: Run** the file. Expected: `FAILED`.
- [ ] **Step 3: Implement.** Read the output through one pipe with a counting reader thread, as `slot_run.py` does; stop the process tree on timeout or cap.
- [ ] **Step 4: Run** the file and `test_batch_model.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (full): one per fallback cause (return the path despite it); no timeout (the sleep row runs past its bound); kill only the child (the grandchild check red); no `\r\n` normalisation (the carriage-return row red); `shell=True` (the shell row red).
- [ ] **Step 6: Commit.** `fix(rigger-lessons): the batch reads the rendered rules text`

### Task 6: Windows test leg timing

**Files:**
- Modify: the test files Step 1 finds at or above 60% of their limit (from Baselines, the candidates are `test_slot_run.py` and `test_weak_test_guard.py`; a file below the line is not touched), `scripts/run_tests.py` (`TIMEOUTS` only), `tests/read_by.json` (a key for a split file names the new files), `plugins/rigger-core/tools/slot-run/tests/test_slot_run.py` if Step 3 finds the cause in the test

- [ ] **Step 1: Measure.** Three runs of `python -I scripts/run_tests.py --no-claude` on Windows with the full Python; copy each run's slowest list from its log. Record every file at or above 60% of its limit (D69).
- [ ] **Step 2: Split or speed up** each such file: a split by test class into files named for what they test; a speed-up by sharing an expensive fixture per class. Show the test count before and after (equal), and run `tests/test_read_by.py` (no dead key).
- [ ] **Step 3: The cleanup error.** Load `superpowers:systematic-debugging`. Run `test_slot_run.py` alone 10 times and count the `WinError 32` errors. If the cause is a process the test started and does not wait for, fix the test and show 10 clean runs; if it is a process `slot_run.py` leaves running, fix nothing here and report it as a finding with its evidence.
- [ ] **Step 4: Re-measure** three runs. Expected: every file under 50% of its limit in all three.
- [ ] **Step 5: Commit** per file. `test: split <file> so the Windows leg keeps its per-file headroom`

### Task 7: Gate and pull request 1

**Files:**
- Modify: `tests/read_by.json`, `scripts/run_tests.min`

- [ ] **Step 0:** Run `python -I -X utf8 tests/test_read_by.py` on the merged wave A and B; declare in `tests/read_by.json` each read it reports `undeclared` (Task 5's new rows are expected to name rigger-core), and record the count line.
- [ ] **Step 1:** Set `scripts/run_tests.min` to the discovered count.
- [ ] **Step 2:** Run `python -I scripts/gate.py` in the background; read `status.txt`. Expected: `mode: full` and `# gate passed, …, tree <id>`. Then `gate.py --verify`, expected `(full)`.
- [ ] **Step 3:** Push and open the pull request. Body: the read-by counts, the mutation list per task with survivors and reasons, the local latency tables.
- [ ] **Step 4:** Wait for CI; copy the macOS and Linux `hook latency` output into the result file of Task 3 as a follow-up commit on the branch; CI runs again.
- [ ] **Step 5:** Review round one, fix round, then the second round of D65 for Tasks 1, 2, 4 and 5. Merging is the owner's step.

## Pull request 2: the modules (waves D–F)

### Task 8: Skip reasons and `review.paths` in `review_scope.py`

**Files:**
- Modify: `plugins/rigger-core/tools/review-scope/review_scope.py`, `plugins/rigger-core/tools/assemble/config.py` (`ACTIVE_ABOVE_L1`, `review.paths` validation)
- Test: `test_review_scope.py`, `plugins/rigger-core/tools/assemble/tests/test_config.py`

**Interfaces:**
- Produces:
  - `PathRule = NamedTuple("PathRule", [("glob", str), ("rules", Optional[str]), ("skip", Optional[str]), ("level", int), ("source", str)])`;
  - `path_rules(snapshot: Optional[dict], repo: Path, base: str) -> List[PathRule]` (levels 2–3 from the snapshot, level 4 from `<base>:.claude/rigger.json`);
  - `scope(repo, base, head, rules: Sequence[PathRule] = ()) -> List[Tuple[str, str]]` (D60 verdicts);
  - `is_test_file(path: str) -> bool`; `glob_match(pattern: str, path: str) -> bool`;
  - `config.validate_level(data: dict, level: int, where: str) -> Tuple[dict, List[str]]` (today's `_validate`, one caller at `config.py:173`);
  - CLI `--session <id>` (default `CLAUDE_CODE_SESSION_ID`), `--rules-out <dir>`, the `# review.paths:` and `# rules` lines of D61.

- [ ] **Step 1: Write the failing tests** (fixture repositories built with `git init`):
  - one row per D60 reason and per lockfile name; `test_a_skip_the_diff_creates_is_reviewed` (Review Focus 2 and 7: a marker added in the diff, a marker on an added file, a new `vendor/` folder, an added `x.min.js`, an added file with a NUL byte and a `.js` file the diff gives one, the last two `review: binary added`); `test_test_files_are_never_skipped_without_a_path_rule` (Review Focus 4); `test_a_binary_fixture_under_tests_is_skipped` (`tests/fixtures/logo.png`, binary at base and head, reads `skip: binary`; `tests/test_x.py` with a NUL at base and head reads `review`); `test_binary_detection_reads_a_bounded_prefix` (Review Focus 7: a 20 MB file with a NUL at byte 10 at base and head, the reader asked for 8,000 bytes, patched to count bytes kept);
  - `review.paths`: `test_glob_match_table` (`**` zero and many segments, `*` not crossing `/`, case); `test_glob_match_is_bounded` (`"**/" * 40 + "x"` against a 40-segment path that does not match, under 1 s); `test_project_entries_come_from_base` (Review Focus 3: the config file changed in the diff, and separately the named rules file; the head versions are never used, and the line names the file); `test_project_entries_apply_without_a_snapshot`; `test_an_entry_with_a_level_four_leaf_counts_as_level_four`; `test_rules_lines_name_matched_entries_only`; `test_no_snapshot_applies_no_personal_or_company_rule`; `test_level_four_rules_file_outside_the_repository_is_dropped`; `test_level_four_rules_file_must_be_tracked_at_base` (a path not in the base tree, and one holding `$(x)`, `;` and a space that is tracked: the first dropped with its notice, the second read by object id, with `git` patched to record argv: no argv element holds the path except after `--`); `test_rules_out_holds_the_base_text` (the skill reads only the written file);
  - `test_config.py`: `review` active above level 1; bad entries dropped one by one with their notice (not an object, both keys missing, `{}`, glob over 200, reason over 200, 51 entries); `validate_level` is what `review_scope` calls for level 4.
- [ ] **Step 2: Run** both files. Expected: `FAILED` on the new rows.
- [ ] **Step 3: Implement.** One `git cat-file --batch` process per call, fed the head (and, where D60 needs it, base) blob ids from `git ls-tree -z`; read exactly the header's size, keep the first 8,000 bytes.
- [ ] **Step 4: Run** both files on Windows and WSL. Expected: `OK`.
- [ ] **Step 5: Red runs** (full, trust): one mutant per D60 reason row (always skip, never skip), per "existed at base" clause, per test-file pattern, and on the order (a lockfile rule before the test rule); the binary skip without its base clause (the NUL-added rows red); the test-file rule before the binary check for a fixture (the logo row red); for `review.paths`, read the level-4 config at `head`, read a level-4 rules file at `head`, take level 4 from the snapshot, use the lowest leaf's level, accept a `..` rules path, accept an untracked rules path, read a rules file by path instead of by object id (the argv row red); in `config.py`, accept 51 entries, a 201-character glob, a 201-character reason and `{}`, leave `review` out of `ACTIVE_ABOVE_L1`, and make level-4 validation a no-op in `review_scope`; the blob reader keeping the whole blob (the bounded-prefix row red); `*` matching `/` and case-insensitive matching (glob table rows red); a recursive matcher without the visited set (the bound row red). Each red on its own row.
- [ ] **Step 6: Commit.** `feat(rigger-core): review_scope skip reasons and review.paths`

### Task 9: `coverage-test-skipped`

**Files:**
- Modify: `plugins/rigger-core/tools/slot-check/slot_check.py`, `tools/slot-check/tests/test_slot_check.py`
- Create: `plugins/rigger-REVIEW/tests/contract/reviewer/fail-coverage-test-skipped.json` and the repository files it needs under that folder's `repo/`

**Interfaces:**
- Consumes: `review_scope.scope(repo, base, head, rules)`, `review_scope.path_rules`, `review_scope.is_test_file`.
- Produces: rule id `coverage-test-skipped` in `RULES["reviewer"]` and `GIT_RULES`; CLI `--session <id>`.

- [ ] **Step 1: Write the failing tests:** `test_a_skipped_test_file_is_refused`; `test_a_test_file_a_path_rule_skips_passes`; `test_a_skipped_generated_file_passes`. These build their own repository with `git init` and read no fixture folder of the review module (so `test_slot_check.py` gains no cross-module read). The fixture runs through `tests/test_module_contracts.py::test_every_contract_fixture`.
- [ ] **Step 2: Run** both. Expected: red on the new rows and on the fixture (no rule refuses it yet).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both. Expected: `OK`.
- [ ] **Step 5: Red runs** (full): the rule never fires (refused test and fixture red); fires on every skip (generated row red); ignores path rules (path-rule row red).
- [ ] **Step 6: Commit.** `feat(rigger-core): the reviewer contract refuses a skipped test file`

### Task 10: The review module

**Files:**
- Create: `plugins/rigger-REVIEW/.claude-plugin/plugin.json`, `rigger-module.json`, `README.md`, `skills/review/SKILL.md`, `checklists/python.md`, `checklists/typescript.md`, `checklists/php.md`, `checklists/shell.md`, `tests/test_review_module.py`
- Move (`git mv`): `plugins/rigger-core/agents/reviewer.md` to the module's `agents/`, `plugins/rigger-core/tests/contract/reviewer/` to the module's `tests/contract/reviewer/`
- Modify: `plugins/rigger-core/rigger-module.json` (`contract` loses `reviewer`), `rigger-defaults.json:4`, `plugins/rigger-core/tests/test_core_wiring.py` (every `REVIEWER` use: `:21`, `:214` in `FindingLabelTests`, `:229`, `:241` with its `slot_check` import, `:261` in `test_dispatch_names_slots`; `FindingLabelTests` splits into a reviewer half that moves and a skill half that stays; the reviewer-only tests move to `test_review_module.py`, which imports rigger-core's `slot_check` and so reads rigger-core in the map), `tests/test_assembly_golden.py` and `tests/golden_baseline.py` (the layout gains the module; `SLOT_ROWS` reviewer row; one `ALLOWED` entry "the reviewer slot names the review module"), the reviewer-row pins of `test_assemble.py` and `test_stub_session.py:452`, `.claude-plugin/marketplace.json` (the module listed after rigger-core) with the list in `tests/test_repo.py:59-70`, `tests/read_by.json` (a key for a moved file follows it, and a new derived read is declared)

**Interfaces:**
- Produces: the D56 manifest; agent `rigger-REVIEW:reviewer`; skill `rigger-REVIEW:review`; the checklist table of D63 inside the skill.

- [ ] **Step 1: Write the failing tests** in `test_review_module.py`: the moved reviewer pins, now reading the module's agent, plus "Load the `review` skill first"; `test_skill_names_the_scope_command_and_the_rules_lines` (the command holds `--rules-out`, and the skill says to read the named files and never run git on a path from config; it holds no `git show`); `test_checklist_table_matches_the_files` (every row names an existing file, every file has a row, at most 25 bullets each); `test_manifest_fills_and_provides_the_reviewer`.
- [ ] **Step 2: Run** it. Expected: `FAILED`.
- [ ] **Step 3: Move and write** the files. The skill's text follows D64; the checklists hold only checks no linter or `defects.md` covers; the Task 10 reviewer checks each checklist bullet against `plugins/rigger-rules/rules/defects.md` and names any overlap as a finding.
- [ ] **Step 4: Run** `test_review_module.py`, `test_core_wiring.py`, `tests/test_module_contracts.py`, `tests/test_assembly_golden.py`, `test_assemble.py`, `tests/test_repo.py`, `tests/test_plugin_refs.py`, `tests/test_read_by.py`, `test_plugin_rules.py`, `test_drift_lint.py`, and `assemble.py --check` over 8 modules. Expected: `OK`, `findings 0`.
- [ ] **Step 5: Red runs** (low risk, one per new test): remove the skill's load sentence, a checklist file, the manifest's `fills`.
- [ ] **Step 6: Commit.** `feat(rigger-review): the review module fills the reviewer slot`

### Task 11: The testing module

**Files:**
- Create: `plugins/rigger-TESTING/.claude-plugin/plugin.json`, `rigger-module.json`, `README.md`, `hooks/hooks.json` (the two handlers of core's `hooks.json:91-102`, text unchanged), `bin/py.sh` (copy), `tests/test_testing_wiring.py`
- Move (`git mv`): rigger-core's `skills/testing/`, `tools/weak-test-guard/`, `evals/testing-skill-loads/`, and `evals/add-commit-push-chain/graders/no-testing-skill.md` into the new case `evals/git-question-no-testing/graders/` (D72; its `prompt.md` copies that case's prompt)
- Modify: `plugins/rigger-core/hooks/hooks.json` (lose `:91-102`), `evals/CASES.md` (D72), `scripts/hook_latency.py` (`CASES`: the key `("rigger-core", "weak_test_guard.py")` becomes `("rigger-testing", "weak_test_guard.py")`), `tests/test_hook_latency.py`, `rigger-defaults.json:8`, `tests/test_repo.py:29` (`PLUGINS_WITH_PY_SH`) and `:59-70` (the listing), `.claude-plugin/marketplace.json` (the module listed after the review module), `tests/read_by.json` (as in Task 10), `scripts/run_tests.py:97` (the path), `plugins/rigger-rules/tests/test_plugin_rules.py:38` and `:445` (the rendered cell `rigger-TESTING:testing`), `tests/test_module_contracts.py` (D71), the testing-row pins of the golden test (one `ALLOWED` entry "the testing slot names the testing module") and `test_assemble.py`

**Interfaces:**
- Produces: skill `rigger-TESTING:testing`; the two commit handlers in the testing module.

- [ ] **Step 1: Write the failing tests:** `test_testing_wiring.py::test_both_commit_handlers_deny_a_weakening_commit` (D70: each handler's command run through the launcher Task 17 (c) observed, from the module's `hooks.json`, with `CLAUDE_PLUGIN_ROOT` the module, on a temp repository whose staged commit deletes a test; expected one deny each, and nothing on a commit that adds a test); `test_module_contracts.py::test_testing_filler_ships_the_commit_guards` (D71, exact `if` strings). `test_hook_latency.py::test_every_real_handler_has_a_case` is already red once the handlers move.
- [ ] **Step 2: Run** them. Expected: `FAILED` (no module).
- [ ] **Step 3: Move and write.** `DuplicateHookTests` keeps `moved + 15` and two `weak_test_guard.py` handlers; stage the testing module's evals with `scripts/eval_stage.py` (each case's `plugins:` loads the module and `../../deps/rigger-core`) and check them with `python -I scripts/eval_suite_check.py --cases evals/CASES.md plugins/rigger-TESTING`, then the same for `plugins/rigger-core`. An eval run, if one is made, follows "Real `claude` runs" (`--no-publish`, `--keep-temp`, `eval_summary.py --with-only`), and the control counts only when `testing-skill-loads | skill` is above 0 in the same output.
- [ ] **Step 4: Run** the moved `test_weak_test_guard.py`, the two new tests, `tests/test_hook_latency.py`, `tests/test_eval_suite_check.py`, `tests/test_repo.py`, `test_plugin_rules.py`, `test_drift_lint.py`, the golden test, `test_assemble.py`, `tests/test_read_by.py`, and `assemble.py --check` over 9 modules. Expected: `OK`, `findings 0`.
- [ ] **Step 5: Red runs** (full, wiring): drop the Bash handler, then the PowerShell handler, from the module's `hooks.json` (wiring test red each, and `test_repo.py`'s count); change one `if` to `Bash(*commmit*)` (`test_testing_filler_ships_the_commit_guards` red: no command run reads the `if`); empty `provides.testing` (contract test red); leave the old `CASES` key (latency test red); leave the moved grader in rigger-core's suite (`eval_suite_check.py` on the testing module red). The real-session proof is Task 14.
- [ ] **Step 6: Commit.** `feat(rigger-testing): the testing module fills the testing slot and carries the commit guard`

### Task 12: The install notice and doctor

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/slots.py`, `assemble.py` (`_doctor` only), `tests/test_slots.py`, `tests/test_assemble.py`, `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `plugins/rigger-core/README.md:21`, `plugins/rigger-core/skills/stack-tooling/SKILL.md:75` ("how to test is in the testing skill the Skill triggers table names")

**Interfaces:**
- Produces: `slots.INSTALL_HINT_SLOTS`; the D57 notice and doctor row.

- [ ] **Step 1: Write the failing tests:** `test_default_slot_without_its_plugin_names_the_install_command` (Review Focus 1: both slots, the exact line, at `startup`, `clear` and `compact`); `test_a_slot_set_above_level_one_keeps_its_own_notice` (no separate hint line); `test_testing_notice_says_the_commit_guard_is_off`; `test_a_stale_override_falls_back_with_the_install_command` (a personal `slots.reviewer.target: rigger-core:reviewer`: D34's line ends with `; install …`); `test_superpowers_slots_stay_quiet`; the doctor `MISSING` row with its fix.
- [ ] **Step 2: Run** them. Expected: `FAILED`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the changed files, the golden test, `test_plugin_rules.py`, `test_drift_lint.py`, `tests/test_repo.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (shared): the hint for any level-1 default (superpowers test red); none at `compact` (compact row red); the doctor row `INFO` (doctor test red); no suffix on the fallback line (stale-override row red); the testing notice without its guard clause (its row red).
- [ ] **Step 6: Commit.** `feat(rigger-core): say how to install the review and testing modules when their slot is empty`

### Task 13: The second review round

**Files:**
- Modify: `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md:14` and its Fix rounds section, `plugins/rigger-core/commands/coordinate.md:74-76`, `plugins/rigger-core/tests/test_core_wiring.py`, `plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py`

- [ ] **Step 1: Write the failing pins:** `test_full_risk_changes_get_a_second_round` (the sentences of D65, whitespace-normalised); `test_a_finding_is_disputed_only_with_a_quoted_line`; `test_coordinate_marks_a_full_risk_part_reviewed_after_round_two`; `test_an_empty_reviewer_slot_blocks_the_review` (Review Focus 9: the empty-slot sentence of D65 in both `SKILL.md` and `coordinate.md`, holding "blocked", "install line" and "never skip").
- [ ] **Step 2: Run** both files. Expected: `FAILED`.
- [ ] **Step 3: Edit** the two texts as D65 says; keep `:151-154` as they are.
- [ ] **Step 4: Run** both files, `test_plugin_rules.py`, `test_drift_lint.py`. Expected: `OK`.
- [ ] **Step 5: Red runs:** remove each new sentence (its pin red).
- [ ] **Step 6: Commit.** `docs(rigger-core): a second fresh review round for full-risk changes`

### Task 14: Stub exit criteria and a real review

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/tests/test_stub_session.py`; only if Step 1 finds no session id in the subagent's shell, the review module's `skills/review/SKILL.md`, `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md` and their pins in `test_review_module.py` and `test_core_wiring.py`
- Create: `docs/superpowers/spikes/phase-3a/release-review.md`

- [ ] **Step 1: Known answer for D61's session id.** Before any test: one stub session whose scripted reply starts an agent of the review module's `reviewer` type, whose own scripted reply runs a Bash command that prints whether `CLAUDE_CODE_SESSION_ID` is set and equal to the parent's id (the stub logs the parent's id from the first request). Control: the same command in the main session prints `set, equal`. Record the answer; if the subagent's shell lacks it, the review skill's text tells the agent to pass the id the brief gives as `--session`, and the orchestrating skill's review brief line names that id (one sentence each, added in this task with a pin).
- [ ] **Step 2: Write the stub tests** (each starts `claude -p` against the stub under "Real `claude` runs" in Global Constraints): `test_new_modules_fill_their_slots` (with both modules in `--plugin-dir` and `RIGGER_MODULE_DIRS`, the first request's `## Slots` holds `rigger-REVIEW:reviewer` and `rigger-TESTING:testing`, and the trigger cell names the testing module); `test_without_them_the_session_names_the_install_commands` (both lines arrive); `test_commit_guard_denies_from_the_testing_module` (D70: the stub's scripted reply runs `git commit` through the `Bash` tool on a temp repository whose staged commit deletes a test; the tool result holds the guard's deny, and the debug log holds `Hooks: PreToolUse:Bash`; control: the same session on a commit that adds a test gets no deny). Run them. Expected: `OK`; the report says "stub".
- [ ] **Step 3: Red runs:** set the `reviewer` default back to rigger-core's agent on a scratch copy (the fill test red on its `## Slots` line); empty `INSTALL_HINT_SLOTS` (the install-lines test red); leave the testing module out of `--plugin-dir` (the commit-guard test red: no deny).
- [ ] **Step 4: Measure again** with `scripts/hook_latency.py` on Windows and WSL: the testing module's two handlers are measured and match their Task 3 numbers within the run's spread.
- [ ] **Step 5: Real review** (spec §13 exit criterion): in the builder's session, dispatch the review module's `reviewer` agent on this repository's last release diff, `v0.5.0..v0.6.0` (146 files, 11,644 insertions; base and head as full commit ids) with a scratch folder; save its reply; run `slot_check.py reviewer <reply file> --base <base> --head <head>`. Expected: `slot-check: ok reviewer <n> findings`, every in-scope path covered, every quote anchored. Record the command, the counts and the result line in `release-review.md`; findings are listed by id and title only.
- [ ] **Step 6: Sweep and commit.** `test(rigger-core): stub exit criteria for the review and testing modules`

### Task 15: Docs, release notes, migration

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), both new `README.md`, `CONTRIBUTING.md` (`tests/read_by.json`; contract fixtures now under the review module), the spec file (the "Spec corrections" rows)
- Consumes: Task 17 (a) and (b).

- [ ] **Step 1: Unreleased entries:** the eight behaviour changes; the migration in the form Task 17 observed: one `claude plugin install` call with both `rigger-review@rigger` and `rigger-testing@rigger` if (a) showed that works, else one call per plugin; rigger-core named too only if (b) showed a dependency is not installed with them; then `/rigger-core:doctor`; the version-skew note (an old rigger-core with the new testing module runs the guard twice, and `harness_start.py` names the duplicate until rigger-core is updated); for external reviewers, the new refusal rule and the `review.paths` lines; that 3b moves rigger-windows' test tools.
- [ ] **Step 2: Apply "Spec corrections"**, one Edit per row.
- [ ] **Step 3: Stale text:** `git grep -n -e "rigger-core:reviewer" -e "rigger-core:testing" -e "recorded only" -- '*.md' ':!docs/superpowers' ':!CHANGELOG.md'`; correct each hit.
- [ ] **Step 4: Sweep** each changed file and run `tests/test_plugin_refs.py`. Expected: `findings 0`, `OK`.
- [ ] **Step 5: Commit.** `docs: phase 3a review and testing modules`

### Task 16: Gate and pull request 2

- [ ] **Step 1:** Set `scripts/run_tests.min` to the discovered count. Run `python -I scripts/gate.py` in the background; read `status.txt` (`mode: full`); `--verify`, expected `(full)`.
- [ ] **Step 2:** Check the exit criteria: both modules' tests and contracts (`test_every_contract_fixture`, `test_default_fillers_meet_their_contracts`, `test_testing_filler_ships_the_commit_guards`); the stub run of Task 14 labelled "stub"; the release review's result line; the full gate's pass line. CI supplies macOS and Linux.
- [ ] **Step 3:** Push and open the pull request; the body holds the spec corrections, the mutation list per task with survivors, the latency tables of both pull requests and the release-review result.
- [ ] **Step 4:** Review round one, fix round, then the second round of D65 for Tasks 8, 9 and 11. Merging and the release are the owner's steps.

---

## Left for 3b and later

From phase 2's "Review ideas left for phase 3": per-path rules, checklists, skip reasons, the second
round and the read-by map are in this plan, and strict dismissal is one sentence of D65. Not here:
- **A risk list before the review** for large diffs: phase 4, measured first.
- **Splitting a very large diff** into groups for several reviewers: phase 4, an orchestrator decision.
- **3b:** rigger-shellwise (decisions doc D2), rigger-platform, mem-check (D3), the safe-edit port (D4), rigger-windows' test-evidence and gate-lab moving into the testing module, the removal of rigger-windows.
- **Phase 4:** Rust ports for the D66 candidates.

## Estimate

| Part | Agent hours | Code lines | Test lines |
|---|---|---|---|
| Spikes: install forms, hook launcher (Task 17) | 1.5 | 0 in the repository | — |
| Read-by map and lint (Task 1) | 3.5 | 160 (the map file 40) | 260 |
| `gate.py --changed-modules`, the handed-over list, verify by mode (Task 2) | 3 | 110 | 280 |
| Hook latency (Task 3) | 2.75 | 230 | 130 |
| Identity in `resolve_program` (Task 4) | 1.5 | 30 | 100 |
| Batch renders the rules (Task 5) | 2 | 80 | 190 |
| Windows leg timing (Task 6) | 2 | 40 (splits move lines, not counted) | — |
| Skip reasons, `review.paths`, rules handed over as files (Task 8) | 5.25 | 340 | 520 |
| `coverage-test-skipped` (Task 9) | 1.25 | 50 | 90 |
| Review module: skill, 4 checklists, manifest, README (Task 10) | 4 | 450 (Markdown counted as code) | 160 |
| Testing module and the eval control (Task 11) | 2.5 | 90 | 110 |
| Install notice, doctor (Task 12) | 3.25 | 100 | 260 |
| Second review round, empty reviewer slot (Task 13) | 1.25 | 30 | 55 |
| Stub run, the guard in a session, real review (Task 14) | 3 | — | 210 |
| Docs, two gates, two pull requests (Tasks 7, 15, 16) | 3.5 | — | — |
| **Total** | **40.25** | **about 1,710** | **about 2,370** |

About 3,500 lines move unchanged with `git mv` and are not counted.

Against the decisions doc: its 42 h cover 3a and 3b together. The spec's phase 3 rows that land in
3a (the review module 750 and 900 lines, a share of the wording and the manifests) come to about
1,000 code and 1,050 test lines; with decisions doc D5–D7 (5 h) that is about 21 h for 3a. This
plan adds what neither priced: the read-by map and the gate mode (5.5 h), the trust rules of
`review.paths` and the "existed at base" clauses (2 h), the install notice (3 h), the measurement's
CI leg, and two pull requests with a full gate and two review rounds each. The plan review's fixes
(the spike, the handed-over list and verify by mode, rules handed over as files, the base clause of
the binary skip, the eval control, the guard's real-session proof, the empty-slot rule) add about
5 h, 110 code and 440 test lines. For comparison, phase 2's plan priced its spec-scope part at 2.7
times the spec's hours, and phase 1 ran at 1.8 times.

**Simpler version, about 33 h, 1,360 code lines, 1,680 test lines:**
- **What it keeps:** both modules with the moves, the slot defaults and the install notice; the
  skip reasons; the read-by map with its lint; the second review round; the measurement; the
  backlog.
- **What it drops, with what each saves:**
  - `gate.py --changed-modules` with the handed-over list and verify by mode (3 h, 110 and 280
    lines): the mode with the map stays a pre-check, so decisions doc D1's "becomes the gate" is
    not met;
  - `review.paths` with the rules handed over as files (3.25 h, 190 and 320 lines): no per-path
    rules and no named skips, so a test file is never skipped;
  - `coverage-test-skipped` (1.25 h, 50 and 90 lines): a reviewer may skip a test file unrefused;
  - a `coord_state.py` check of the second round, already left out of the full plan (D65).

## Open risks

1. **The read-by lint reads text.** A test that builds a module name at run time is not seen; the
   mode can then pass where the full gate fails. CI's full gate on every pull request to `main`
   remains, and a release takes the full gate.
2. **Hook latency on macOS comes only from CI runners,** which are shared and noisy. The result
   file says so; the 50 ms candidate line is read from medians of 20 runs, not from one run.
3. **The generated-marker rule trusts the base revision.** A file that already carried a marker at
   base and is hand-edited in the diff is skipped. It shows in coverage with its reason.
4. **A project's `review.paths` rules file is reviewer guidance from the repository.** It is read
   at base (D61), so the reviewed diff cannot change it, but text present at base still steers the
   reviewer.
5. **`CLAUDE_CODE_SESSION_ID` was measured in phase 2's Q3 in a main session's shell tools only.**
   An agent reviewer's shell is unmeasured until Task 14 Step 1, which also carries the fallback. Without it, `review_scope.py`
   applies no personal or company path rule and says so (project entries still apply, read at
   base), and `slot_check.py`, run by the orchestrator in the main session, fails closed on a
   skipped test file.
6. **People who update rigger-core and skip the migration** see two notice lines in every session
   until they install the modules or set the slots themselves. That is the intended cost of D57.
7. **The `test_slot_run.py` cleanup error** may come from a process `slot_run.py` leaves running on
   Windows (P2-E14 open question 1). Task 6 decides which; a product fix is then its own item.
8. **Version skew:** a new testing module next to an old rigger-core runs the weak-test guard
   twice. `harness_start.py` names it as a duplicate (same event, matcher, `if`, script and
   arguments), so it is noisy, not unsafe; the release notes say to update rigger-core too.
9. **`review: binary added` is a new verdict form** in the `{scope}` file. An external reviewer
   command that matches the verdict `review` exactly would miss it; the release notes say every
   verdict starting `review` is to review.
10. **Task 17's answers may differ from what the plan assumes** (one install call, `sh -c`). The
    tasks that consume them (3, 11, 15 and D57's notice) are worded to use the observed form; a
    launcher other than `sh` changes the floor D66 reports, not the method.
