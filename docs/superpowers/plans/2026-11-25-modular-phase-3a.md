# Modular architecture, phase 3a: rigger-review, rigger-testing and the module gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The reviewer and the testing skill become two installable modules that fill their slots,
`--changed-modules` becomes the gate for a change inside module folders, full-risk changes get a
second review round, every hook's latency is measured on three systems, and the phase 2 backlog is
closed.

**Status:** draft for the owner's decision, written on `85ca652` (release 0.6.0 plus the phase 3
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
  - every refusal and exit path of `gate.py --changed-modules` (Task 2);
  - the identity check in `resolve_program` and `searchable` (Task 4: command execution);
  - the lookup, bound and fallback of the rules render in `lessons_batch.py` (Task 5: command
    execution);
  - every skip reason of `review_scope.py`, the test-file rule and the project-level trust rule of
    `review.paths` (Task 8: trust);
  - the new `coverage-test-skipped` refusal of `slot_check.py reviewer` (Task 9);
  - the wiring of the moved weak-test guard (Task 11).

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

**Real `claude` runs (Task 14)**
- A fresh temp `CLAUDE_CONFIG_DIR` per experiment; `--plugin-dir` runs also set
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to scratch, leave
  `RIGGER_LESSONS_BATCH` unset and name the same roots in `RIGGER_MODULE_DIRS`. The child drops
  `CLAUDE_*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`, keeps `CLAUDE_CODE_CHILD_SESSION`, then sets
  `ANTHROPIC_BASE_URL` to the stub. Prove the isolation with a canary first. Record
  `claude --version` and `command -v claude` before the first run; bounded timeout, `Popen`
  streaming stdout. Scratch under `<temp>/rigger-p3/<task>/`.
- The one real-model run (Task 14 Step 4) is a subagent of the builder's own session, not
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
3. `review_scope.py` marks binary, generated, vendored and lockfile paths `skip: <reason>`, never a
   test file without a path rule (D60).
4. `review.paths` in config has an effect: per-path rules files for the review skill, and named
   skips (D61). A project file's entries are ignored for a diff that changes them.
5. `slot_check.py reviewer` refuses a reply that skips a test file `review_scope.py` lists as
   `review` (D62).
6. `python -I scripts/gate.py --changed-modules` is a gate for a change inside module folders (D59).
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
   person expects the project's entries ignored for that review, with a line saying why. Test:
   Task 8, `test_project_entries_changed_in_the_diff_are_ignored`.
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
7. **A large binary file in the diff.** A person expects `skip: binary` without the file read whole.
   Test: Task 8, `test_binary_detection_reads_a_bounded_prefix`.

---

## Decisions this plan makes

Phase 2 used D25–D53.

| # | Decision | Why |
|---|---|---|
| D54 | Two pull requests. (1) Tasks 1–7 on `feature/phase-3a-opener`: the read-by map, `gate.py --changed-modules`, the hook latency measurement and the phase 2 backlog. (2) Tasks 8–16 on `feature/modular-phase-3a`, cut from `main` after (1) merges: the two modules, the cutover and the second review round. | (1) touches only shared files and rigger-core internals, (2) changes what a person installs. Task 4 edits `review_scope.py`, which Task 8 edits again, so (1) merges first. |
| D55 | The testing module takes only rigger-core's parts in 3a: the `testing` skill, the weak-test guard with its two handlers, and the `testing-skill-loads` eval. rigger-windows' test-evidence shadow gates (`stop_shadow.py`, `turn_evidence.py`, the commit rule in `shell_guard.py`), `tools/gate-lab/` and `tools/safe-edit/` (with the Python port of decisions doc D4) move in 3b. | Decisions doc D1: 3a removes nothing and changes no installed plugin; anything sourced from rigger-windows changes what a rigger-windows user has (the shadow rows' data folder moves with `CLAUDE_PLUGIN_DATA`, spec §13 "Gates file"). The commit rule lives inside `shell_guard.py`, which 3b deletes; extracting it twice is waste. The safe-edit references (`tests.md:13`, `defects.md:37`, `testing/SKILL.md:85`, "needs rigger-windows") are reworded once, in 3b. |
| D56 | Layout. Both modules: `.claude-plugin/plugin.json` with `dependencies: ["rigger-core"]`, `rigger-module.json` (schema 1, layer `general`, no rules), `README.md`, a marketplace entry. Review module: `fills: "reviewer"`, `provides.reviewer: {kind: agent, target: rigger-REVIEW:reviewer, model: by-rule}`, one trigger row `rigger-review.review` (before: "reviewing a change, a branch or a PR, or verifying an agent's claim", load `rigger-REVIEW:review`), `tests: {paths: ["tests/"], contract: ["reviewer"]}`; `agents/reviewer.md`, `skills/review/SKILL.md`, `checklists/{python,typescript,php,shell}.md`, `tests/contract/reviewer/`. Testing module: `fills: "testing"`, `provides.testing: {kind: skill, target: rigger-TESTING:testing}`, no trigger row (core's `rigger-core.testing` row loads `slot:testing`), `tests: {paths: ["tools/*/tests/", "tests/"], contract: []}`; `skills/testing/`, `tools/weak-test-guard/`, `hooks/hooks.json`, `bin/py.sh`, `evals/testing-skill-loads/`. Neither gets `core_check.sh`: they print no rules. | Spec §6.3 and §13. The dependency makes `--changed-modules` run their tests on a core change and lets Claude Code install core with them. |
| D57 | `rigger-defaults.json` sets `reviewer` to `rigger-REVIEW:reviewer` and `testing` to `rigger-TESTING:testing`. `slots.py` gets `INSTALL_HINT_SLOTS = ("reviewer", "testing")`: when such a slot's entry comes from level 1 alone and its plugin is not enabled, the slot stays empty and the session gets one notice, `rigger: slot <path> is empty: install <plugin> (claude plugin install <plugin>@<marketplace>)`, at `startup`, `clear` and `compact` with part 1's notices; `<marketplace>` is rigger-core's own (`rigger` for a directory or `--plugin-dir` install). Doctor's slot row for it becomes `MISSING` with that command as its fix. Every other level-1 default keeps D34's silence. | The scratch render shows today's rule empties both slots and drops the testing row with no line. Superpowers is optional, these two are not. A slot set above level 1 already gets D34's fallback notice. |
| D58 | The read-by map is `tests/read_by.json`: `{"format": 1, "readers": {"<test file or test folder ending in />": ["<module>", …] or ["*"]}}`. A folder key covers every file in it, helpers included. A discovered `tests/test_*.py` file that the map does not list reads every module (`*`); a module's test file that the map does not list reads only its own module. `--changed-modules` runs, besides today's set (D26), every test file whose entry names a changed module or `*`. `tests/test_read_by.py` derives reads from the text of every `.py` file under a discovered test folder: R1 `plugins/<module>`; R2 a quoted module name; R3 `plugins/*`, or `module_dirs(`, `iterdir(` or `glob(` on a line that names `plugins` or `PLUGINS` (reads `*`); R4 a file inherits the reads of a helper it imports from its own folder or from `tests/`. Every derived read must be its own module or covered by the map; the test prints `# read-by: files <n>, reads <m>, undeclared <k>` and fails on `n` 0 or `k` above 0. Its controls: the four known readers of phase 2's list are derived, and a planted file per rule is flagged. | Phase 2's "Review ideas left for phase 3". Text matching over-approximates (a fixture named like a module counts as a read), which only runs more tests. A computed module name escapes it (open risk 1); CI's full gate on every pull request stays the backstop. One central file keeps the map reviewable and makes every change to it a shared change. |
| D59 | `gate.py --changed-modules [--base REV]` runs the legs `changed-modules` (`run_tests.py --changed-modules`, count `RUN_TESTS_COUNT`), `control`, `sweep` and `sweep-control` as today, then on Windows `wsl` running the same mode under WSL with `--no-claude`. The status file's second line is `scope: changed-modules base <rev>, modules <names>`; the pass line keeps its format; `--verify` on such a status prints `gate result matches: tree <id> (changed-modules)`. The mode's exit 2 fails the leg, and the status shows its `changed-modules: shared file …; run the gate` line. `--release` with `--changed-modules` is a usage error. When it is the gate: a pull request whose every changed path is a module path under D26 may take it as its local gate; a release, and any change the mode exits 2 on, take the full gate. CI keeps the full gate (D27). | Decisions doc D1. A gate needs a status file, a tree id and the POSIX leg; the mode alone has none of them. |
| D60 | `review_scope.py` verdicts, first match wins: `skip: deleted`; a test file (a path segment `test`, `tests`, `__tests__` or `spec`, or a file name `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`) is `review` unless a `review.paths` skip names it; a `review.paths` skip gives `skip: <its reason> (review.paths)`; `skip: binary` when the first 8,000 bytes of the head blob hold a NUL byte; `skip: lockfile` for the file names `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`, `composer.lock`, `Cargo.lock`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Gemfile.lock`, `go.sum`; `skip: vendored` for a path under a segment `vendor`, `node_modules`, `third_party` or `third-party` that already existed at `base`; `skip: generated` when the first 5 lines of the blob hold `@generated`, `DO NOT EDIT` or `auto-generated` at `head` and at `base` (`*.min.js` and `*.min.css` count as generated without a marker, when the path existed at base). Everything else is `review`. Blobs are read through one `git cat-file --batch` process with the hardened git of phase 2, at most 8,000 bytes kept per blob. | Spec §13 and open point 6, settled here. The "existed at base" rule closes Review Focus 2: a skip the reviewed diff creates would let the diff hide itself. `.gitattributes` `linguist-*` attributes are not read: the reviewed diff can change them. |
| D61 | `review.paths` (levels 1–4) maps a glob to a rules file (a string) or to `{"rules": <file>, "skip": <reason>}` with at least one key; at most 50 entries per level, a glob and a reason at most 200 characters; a bad entry is dropped with one notice. Globs match the repository-relative path by segment: `**` any number of segments, `*` and `?` inside one, case-sensitive. A rules file is relative to the repository root (level 4), to the personal config file's folder (level 3) or to the module root (level 2), and must stay inside it at levels 2 and 4. `config.ACTIVE_ABOVE_L1` gains `review`. `review_scope.py` reads the entries and their levels from the session snapshot (`config.effective` and `config.sources`), found by `--session <id>` or `CLAUDE_CODE_SESSION_ID`; with no snapshot it prints `# review.paths: none (no snapshot)` and applies none. Level-4 entries apply only when neither `.claude/rigger.json` nor the rules file they name changed in `base..head`; otherwise it prints `# review.paths: project entries ignored: <file> changed in this diff`. After the path lines it prints `# rules <glob> <absolute rules file> (<level>)` for each entry that matched an in-scope path. The `{scope}` file of `slot_run.py` keeps its format. | Spec §7.2 gives `{glob: rules file}`; the skip form is needed because spec §13 says a test file is skipped only when `review.paths` names it "with a reason". Phase 2's Q3 found `CLAUDE_CODE_SESSION_ID` in every shell tool. The unchanged-in-diff rule is Review Focus 3. |
| D62 | `slot_check.py reviewer` gains the refusal rule `coverage-test-skipped`: a path in `coverage.skipped` that `review_scope.scope()` (with the same `--session` entries) marks `review` and that is a test file. The contract stays `rigger.reviewer/1`; a reply's JSON stays the last fenced `json` block, with no Markdown form (D41, spec open point 3, settled). | Spec §13 "never skips a test file". A stricter check of existing keys, not a new key, so the id is unchanged; the release notes name the rule for external reviewers. |
| D63 | Checklists, first set (open point 8): `python.md` (`.py`), `typescript.md` (`.ts .tsx .js .jsx .mjs .cjs`), `php.md` (`.php`), `shell.md` (`.sh .bash`, and an extensionless file whose first line starts `#!` and names `sh` or `bash`). Each holds at most 25 bullets and none that a linter or `rigger-rules/rules/defects.md` already covers. The review skill loads at most 3, in order of how many `review` paths each matches, ties alphabetical. | Bounded tokens per review; the extension table lives in the skill and a test pins it to the files present. |
| D64 | `agents/reviewer.md` keeps its frontmatter, its edit ban, the report format, severities, `Pre-existing`, `SHORTCUTS` and the contract JSON ending, and gains "Load the `review` skill first". The new `review` skill holds the procedure: run rigger-core's `tools/review-scope/review_scope.py --base <base> --head <head>`; load the `# rules` files (a level-4 file read with `git show <base>:<path>`) and the checklists of D63; review every `review` path; the behaviour-change section; the closing JSON. | The agent stays the contract's filler; the procedure can grow without touching the agent the contract tests pin. |
| D65 | Decisions doc D5 in text: `orchestrating-subagents/SKILL.md:14` gains "a Risk-full change also gets a second fresh reviewer after the first review's fix round"; the Fix rounds section gains one bullet: the second round's agent of the `reviewer` slot gets the first review's findings with their outcomes and the fix diff, and is asked only for what the first did not find; its Critical and Important findings go through a fix round and a re-review, never a third fresh round. One more bullet, strict dismissal: a finding is marked `disputed` only with a quoted `file:line` that proves it wrong, and one about concurrency, bounds, behaviour or compatibility, authorisation, money or data goes to a fresh reviewer first. `coordinate.md` step 4: for a Risk-full part, `move <part> reviewed` comes after the second round. No `coord_state.py` check. | The owner's decision changes the one-review rule. Strict dismissal is phase 2's first review idea and costs one sentence. A state-machine check would need a risk field in every part (dropped, see Estimate). |
| D66 | `scripts/hook_latency.py [--runs N] [--json FILE]` (default 20 runs) times every handler of every `plugins/*/hooks/hooks.json`, started through `sh -c <command>` as Claude Code starts it, with `CLAUDE_PLUGIN_ROOT`, a scratch `CLAUDE_PLUGIN_DATA` and `CLAUDE_CONFIG_DIR`, from a temp git repository. A `CASES` table gives each (plugin, script) a matching stdin and, for a handler on a tool matcher without an `if`, a non-matching one; a handler with an `if` is reported `not started` for a non-matching call, which Claude Code filters before starting a process (phase 2 baselines). It also times `sh -c 'exit 0'` as the floor. One line per case: `# hook <plugin> <event>:<matcher> <script> <case> median <ms> p95 <ms> floor <ms>`, then `# hooks measured <n> of <total>`, and `# candidate for a Rust port: …` for a handler on the per-command path (PreToolUse and PostToolUse on `Bash`, `PowerShell`, `Write` or `Edit`, and UserPromptSubmit) whose median is over 50 ms. A handler without a case, or `n` below `total`, prints `# hook latency failed: …`. Always exit 0. CI runs it on the three systems with `continue-on-error`, like `startup_bench.py`; Task 3 records the numbers in `docs/superpowers/spikes/phase-3a/hook-latency.md`. | Decisions doc D6 and `lessons.md:46-51`. The median includes the floor, since the person pays it; the result file shows both, because the floor alone (53–62 ms on Windows here) is above 50 ms and a Rust port of a script cannot remove it. No local macOS: CI is the only macOS source. |
| D67 | `review_scope.py` gets the public `under_by_identity(path: str, roots: Sequence[os.stat_result]) -> bool` (today's `_same_folder_above`), and `slot_run.resolve_program` refuses a found program that it places under the repository or the cwd, as `_inside` does now. The Windows proof is a test on the admin-share spelling `\\localhost\<drive>$\…` of the temp repository, skipped with its reason only when that share cannot be opened; the macOS proof is the case-twin test, which fails instead of skipping on `darwin` (APFS is case-insensitive by default). | Decisions doc D7 items 1 and 2; P2-RVFIX2 open questions 1 and 2. |
| D68 | `lessons_batch.py` renders the rules before the corpus: it reads `<config dir>/plugins/installed_plugins.json`, takes under the first key starting `rigger-core@` the first `installPath` holding `tools/assemble/assemble.py`, runs `[sys.executable, "-I", "-X", "utf8", <that file>, "--render"]` with the batch's child environment, no shell, a 30 s timeout and 1,000,000 bytes of output read, writes the bytes to `<run dir>/rules-rendered.md` and passes it to `write_corpus`. Any failure (no list, no key, no file, exit not 0, timeout, over the cap, no output) logs `rendered rules: unavailable (<reason>)` and the batch goes on with raw text. | Decisions doc D7 item 3; P2-F20's open question. The same lookup as the interactive skill (`curate-lessons/SKILL.md:23-26`). |
| D69 | Timing (decisions doc D7 item 4): three full Windows suite runs; any file whose loaded time reaches 60% of its limit in one run is split by test class or sped up until it stays under 50% in three runs. A split keeps every test, gives the parts names that differ by more than case or extension, and leaves `tests.paths` matching. A `TIMEOUTS` entry is raised only with the spawn count that shows it. The `test_slot_run.py` cleanup error is diagnosed in the same task. | Measured: `test_slot_run.py` 79 s of 120 s in a loaded gate (66%), `test_weak_test_guard.py` 121 s of 300 s alone; and one cleanup error in three isolated runs. |
| D70 | The weak-test guard moves with `git mv` and no code change; its handlers keep their text, `if` filters and 20 s timeouts. Its full-risk proof is the wiring: a test runs both handlers from the testing module's `hooks.json` through `sh -c` on a weakening commit and expects the deny. | Moved code with its tests; only the place it is wired from is new. |
| D71 | The `testing` contract's commit-guard clause (D47) becomes a check in `tests/test_module_contracts.py` on the default filler only: its `hooks.json` has a `PreToolUse` handler on `Bash` and one on `PowerShell`, each behind an `if` naming `commit`. A filler chosen in config is not checked. | A filler from config may be a skill alone; doctor has no reason to refuse that. |

## Spec corrections

Task 15 applies each row to the spec file; the second pull request's body carries the table.

| Spec | Correction | Reason |
|---|---|---|
| §7.2 `review.paths` | The value is a rules file or `{rules, skip}`; project entries apply only when unchanged in the reviewed diff; rules files resolve per level. | D61 |
| §7.3 step 5 | `reviewer` and `testing` at their level-1 default give an install notice when their plugin is not enabled. | D57 |
| §10.4 `testing` | The commit-guard clause is checked on the default filler only. | D71 |
| §10.5 | New refusal `coverage-test-skipped`; contract id unchanged. | D62 |
| §12 "Gate or not" | `gate.py --changed-modules`, with the read-by map, is the local gate for a module-only change; a release and an exit-2 change take the full gate; CI is unchanged. | D58, D59 |
| §13 phase 3, rigger-testing | 3a moves only rigger-core's parts; rigger-windows' test-evidence, gate-lab and safe-edit move in 3b. The guard's handlers are `hooks.json:91-102`. | D55 |
| §13 phase 3, `review_scope.py` | Generated, vendored and minified skips need the path at base; the lists of D60. | D60 |
| §15 points 3, 6, 8 | Settled by D62, D60 and D63. | — |

## File map

| Path | Task | Responsibility |
|---|---|---|
| `tests/read_by.json`, `tests/test_read_by.py`, `scripts/run_tests.py` (selection only), `tests/test_run_tests.py` | 1 | the read-by map, its lint, the mode's selection |
| `scripts/gate.py`, `tests/test_gate.py`, `CLAUDE.md` (Gate bullet), `CONTRIBUTING.md` | 2 | the gate mode and its docs |
| `scripts/hook_latency.py`, `tests/test_hook_latency.py`, `.github/workflows/test.yml`, `docs/superpowers/spikes/phase-3a/hook-latency.md` | 3 | hook latency |
| `plugins/rigger-core/tools/review-scope/review_scope.py`, `tools/slot-run/slot_run.py`, both test files | 4 | identity check |
| `plugins/rigger-lessons/tools/lessons_batch.py`, `plugins/rigger-lessons/tests/test_lessons_batch.py` | 5 | rendered rules for the batch |
| the slow test files the measurement names, `scripts/run_tests.py` (`TIMEOUTS`), `plugins/rigger-core/tools/slot-run/tests/test_slot_run.py` | 6 | Windows leg timing |
| `scripts/run_tests.min` | 7, 16 | the minimum file count |
| `review_scope.py`, `test_review_scope.py`, `plugins/rigger-core/tools/assemble/config.py`, `test_config.py` | 8 | skip reasons, `review.paths` |
| `plugins/rigger-core/tools/slot-check/slot_check.py`, `test_slot_check.py`, one new fixture under the review module's `tests/contract/reviewer/` | 9 | `coverage-test-skipped` |
| `plugins/rigger-REVIEW/**` (new; agent and fixtures moved from rigger-core), `plugins/rigger-core/rigger-module.json` (`contract` list), `plugins/rigger-core/rigger-defaults.json` (`reviewer`), `plugins/rigger-core/tests/test_core_wiring.py` (reviewer part moves), `tests/test_assembly_golden.py`, `tests/golden_baseline.py`, the `test_assemble.py` and `test_stub_session.py` pins of the reviewer row | 10 | the review module |
| `plugins/rigger-TESTING/**` (new; skill, guard, eval moved), `plugins/rigger-core/hooks/hooks.json`, `rigger-defaults.json` (`testing`), `tests/test_repo.py`, `scripts/run_tests.py` (`TIMEOUTS` path), `plugins/rigger-rules/tests/test_plugin_rules.py`, `tests/test_module_contracts.py`, golden and `test_assemble.py` pins of the testing row | 11 | the testing module |
| `plugins/rigger-core/tools/assemble/slots.py`, `assemble.py` (`_doctor`), `test_slots.py`, `test_assemble.py`, `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `.claude-plugin/marketplace.json`, `plugins/rigger-core/README.md`, `skills/stack-tooling/SKILL.md` | 12 | install notice, doctor row, listing, wording |
| `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md`, `commands/coordinate.md`, `tests/test_core_wiring.py`, `tools/coord-state/tests/test_coordinate_wiring.py` | 13 | second review round |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py`, `docs/superpowers/spikes/phase-3a/release-review.md` | 14 | stub exit criteria, the real review |
| `CHANGELOG.md`, both new READMEs, `CONTRIBUTING.md`, the spec file | 15 | release notes, migration, spec corrections |

## Parallel waves

| Wave | Tasks | Runs after | Why they are file-disjoint |
|---|---|---|---|
| A | 1, 2, 3, 4, 5 | — | 1: `run_tests.py`'s selection, `tests/read_by.json` and two test files. 2: `gate.py`, `test_gate.py`, `CLAUDE.md`, `CONTRIBUTING.md`; it calls the mode by its command line, which Task 1 does not change. 3: two new files, the workflow, a result file. 4: `review_scope.py`, `slot_run.py` and their tests. 5: two rigger-lessons files. |
| B | 6 | 1 and 4 | It edits `run_tests.py` (`TIMEOUTS`) after Task 1, and `test_slot_run.py` after Task 4. |
| C | 7 | all of A and B | the gate and pull request (1) |
| — | pull request 1 merges; `feature/modular-phase-3a` is cut from `main` | 7 | — |
| D1 | 8, 10 | the merge above | 8: `review_scope.py`, `config.py` and their tests. 10: the new review folder, the core files moved out, the reviewer pins. |
| D2 | 9, 11, 13 | 9 after 8 and 10; 11 and 13 after 10 | 9: `slot_check.py`, its test, one fixture in the review module. 11: the testing folder, core's `hooks.json`, `test_repo.py`, `test_plugin_rules.py`, `test_module_contracts.py`, `run_tests.py`, and the testing row in the golden and `test_assemble.py` pins (Task 10 is done with both files). 13: two text files and two test files; `test_core_wiring.py` after Task 10 moved its reviewer part out. |
| E | 12 | 11 | `slots.py`, `_doctor`, the doctor test, the listing, two text files; it reads the defaults Task 11 wrote. |
| F | 14, then 15, then 16 | all | — |

Parallel: 1–5; then 8 and 10; then 9, 11 and 13. Serial: 6, 7, the merge, 12, 14, 15, 16. Files
with several owners, each in a different wave: `scripts/run_tests.py` (1, then 6, then 11),
`review_scope.py` (4, then 8), `rigger-defaults.json`, `tests/test_assembly_golden.py` and
`test_assemble.py` (10, then 11, then 12), `test_core_wiring.py` (10, then 13),
`scripts/run_tests.min` (7, then 16).

---

## Pull request 1: the module gate, the measurement and the backlog (waves A–C)

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
  - `tests/test_run_tests.py` on the D26 fixture repository with a `tests/read_by.json`: `test_known_readers_run_on_a_change_to_what_they_read` (Review Focus 5: a change in `plugins/r/tests/stub.py` runs `plugins/a/tests/test_s.py` declared as reading `r`); `test_a_wildcard_reader_runs_on_any_module_change`; `test_an_unlisted_repo_test_runs_on_any_module_change`; `test_a_broken_map_exits_two` (missing file, format 2, unknown module, dead key).
- [ ] **Step 3: Run** both files. Expected: `FAILED`, each new test on its own assertion (or `ImportError` for `read_by`).
- [ ] **Step 4: Implement** `read_by` and `selected_files`; `run_changed_modules` calls `selected_files`. Paths are matched by segments, never `fnmatch`.
- [ ] **Step 5: Run** both files and `python -I scripts/run_tests.py --changed-modules` on the tree. Expected: `OK`, and the mode exits 2 naming `scripts/run_tests.py` (a shared file).
- [ ] **Step 6: Red runs** (full): drop the map lookup (known-readers test red); ignore `"*"` (wildcard test red); treat unlisted repo tests as reading nothing (repo test red); accept a dead key (broken-map test red); drop each of R1–R4 from `derived_reads` (its planted control red, four mutants); drop R4's helper inheritance on the real tree (the declared-read test reports `undeclared` for the doctor folder, or the planted R4 file). Remove each map entry in turn and run `test_every_derived_read_is_declared`: each removal is red (one mutant per reader row; list the count).
- [ ] **Step 7: Commit.** `feat(tests): a read-by map so --changed-modules runs the tests that read a changed module`

### Task 2: `gate.py --changed-modules`

**Files:**
- Modify: `scripts/gate.py`, `tests/test_gate.py`, `CLAUDE.md` (the Gate bullet's `--changed-modules` sentence), `CONTRIBUTING.md:39-42`

**Interfaces:**
- Consumes: `run_tests.py --changed-modules [--base REV] [--no-claude]` (D26 lines and exit codes).
- Produces: CLI `--changed-modules` and `--base REV` (default `main`; `--base` alone is a usage error); `build_stages(args, root, out, release=None, claude=True)` builds the D59 legs; the scope line; `verify` prints the `(changed-modules)` suffix when the status holds the scope line.

- [ ] **Step 1: Write the failing tests** in `tests/test_gate.py`, with the leg runner patched (the pitfall of a recursive suite): `test_changed_modules_builds_its_legs` (names and commands, the WSL leg on `nt` only); `test_shared_file_fails_the_gate_with_the_mode_line` (a fake leg exiting 2 with that line); `test_scope_line_and_verify_suffix`; `test_release_with_changed_modules_is_refused`; `test_base_without_the_mode_is_refused`; `test_a_mode_leg_with_count_zero_fails`.
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_gate.py -v`. Expected: `FAILED` or `unrecognized arguments`.
- [ ] **Step 3: Implement.** The `wsl` leg command is today's plus `--changed-modules --base <rev>`.
- [ ] **Step 4: Docs.** `CLAUDE.md` Gate bullet: "Before the gate, a change inside one module's folder may run `python -I scripts/run_tests.py --changed-modules` as a fast pre-check; a pull request whose every changed path lies in module folders may take `python -I scripts/gate.py --changed-modules` as its gate; a release, and a change the mode exits 2 on, take the full gate." `CONTRIBUTING.md:39-42` says the same and names `tests/read_by.json` as the file to update when a test starts reading another module.
- [ ] **Step 5: Run** the file. Expected: `OK`.
- [ ] **Step 6: Red runs** (full): each refusal returns 0 instead (its test red); the scope line dropped (verify test red); the exit-2 leg judged as passed (shared-file test red); the WSL leg built without the mode flag (legs test red).
- [ ] **Step 7: Sweep and commit.** `python -I scripts/sweep.py --no-denylist CLAUDE.md CONTRIBUTING.md`. `feat(gate): gate.py --changed-modules for a change inside module folders`

### Task 3: Hook latency on three systems

**Files:**
- Create: `scripts/hook_latency.py`, `tests/test_hook_latency.py`, `docs/superpowers/spikes/phase-3a/hook-latency.md`
- Modify: `.github/workflows/test.yml` (a `hook latency` step with `continue-on-error: true` after the gate in the Linux job and after `startup bench` in the macOS and Windows jobs: `python -I -X utf8 scripts/hook_latency.py --runs 20`)

**Interfaces:**
- Produces: the D66 command line and lines; `CASES: Dict[Tuple[str, str], Tuple[bytes, Optional[bytes]]]` keyed by (plugin, script file name); `measure(command: str, stdin: bytes, env: dict, cwd: str, runs: int) -> List[float]`; `nearest_rank(values, pct) -> float`.

- [ ] **Step 1: Write the failing tests** with a fixture `plugins/` of two plugins: `test_slow_handler_reads_slower_than_fast` (a `sh -c 'exit 0'` handler and a Python handler that sleeps 0.2 s; known answer); `test_if_handler_non_match_is_not_started`; `test_handler_without_a_case_fails_the_run`; `test_no_hooks_file_fails_the_run`; `test_candidate_line_only_on_the_per_command_path`; `test_nearest_rank`; `test_every_real_handler_has_a_case` (the real tree: 42 handlers, `CASES` covers every (plugin, script)).
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_hook_latency.py -v`. Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.** The weak-test guard's matching case is a staged commit that deletes a test function in the temp repository; the rules parts and `core_check.sh` run with a config dir laid out as an install, as `startup_bench.py` lays it out.
- [ ] **Step 4: Run** the tests. Expected: `OK`. Then run the script locally on Windows and under WSL (Linux on its own file system, not `/mnt/<drive>`); the last line `# hooks measured 42 of 42` on both.
- [ ] **Step 5: Red runs** (shared infrastructure): count a non-match of an `if` handler as started (its test red); drop the missing-case failure (red); report the median of the floor run (slow-handler test red).
- [ ] **Step 6: Write the result file** with headings `## Question`, `## Setup`, `## Control` (the fixture's slow and fast handlers), `## Observed` (one table per system, the macOS and CI Linux rows from the CI log of pull request 1, filled in Task 7), `## Candidates` (handlers over 50 ms on the per-command path, for phase 4), `## Decision`. Temp paths as `<temp>`.
- [ ] **Step 7: Sweep and commit.** `feat(scripts): measure every hook's latency on matching and non-matching calls`

### Task 4: File identity in `resolve_program`

**Files:**
- Modify: `plugins/rigger-core/tools/review-scope/review_scope.py`, `plugins/rigger-core/tools/slot-run/slot_run.py`
- Test: `tools/review-scope/tests/test_review_scope.py`, `tools/slot-run/tests/test_slot_run.py`

**Interfaces:**
- Produces: `review_scope.under_by_identity(path: str, roots: Sequence[os.stat_result]) -> bool`; `searchable` calls it; `slot_run._inside(path, root)` also returns True when `under_by_identity` places `path` under `root`.

- [ ] **Step 1: Write the failing tests.** `test_slot_run.py`: `test_absolute_program_spelled_in_another_case_is_refused` (case twin of the repository on a case-insensitive file system); `test_absolute_program_through_the_admin_share_is_refused` (`nt` only). `test_review_scope.py`: `test_admin_share_entry_is_not_searched` (`nt`); in `case_twin`, `self.fail` instead of `skipTest` when `sys.platform == "darwin"`.
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

- [ ] **Step 1: Write the failing tests** with a fake `installed_plugins.json` and a fake core holding a stub `assemble.py`: `test_render_found_through_install_path_enters_the_corpus` (the corpus holds the stub's text under the not-an-edit-target mark); `test_render_failures_fall_back_to_raw_text_in_time` (Review Focus 6, one row per cause: no list, no `rigger-core@` key, no file, exit 3, a stub that sleeps past a patched 1 s timeout, output over a patched cap, empty output; each logs `rendered rules: unavailable (<reason>)`, the corpus holds the raw files only, and the call returns inside timeout plus 5 s); `test_render_runs_without_a_shell` (a `installPath` holding `;` and spaces).
- [ ] **Step 2: Run** the file. Expected: `FAILED`.
- [ ] **Step 3: Implement.** Read the output through one pipe with a counting reader thread, as `slot_run.py` does; stop the process on timeout or cap.
- [ ] **Step 4: Run** the file and `test_batch_model.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (full): one per fallback cause (return the path despite it); no timeout (the sleep row runs past its bound); `shell=True` (the shell row red).
- [ ] **Step 6: Commit.** `fix(rigger-lessons): the batch reads the rendered rules text`

### Task 6: Windows test leg timing

**Files:**
- Modify: the files Step 1 names, `scripts/run_tests.py` (`TIMEOUTS` only), `plugins/rigger-core/tools/slot-run/tests/test_slot_run.py` if Step 3 finds the cause in the test

- [ ] **Step 1: Measure.** Three runs of `python -I scripts/run_tests.py --no-claude` on Windows with the full Python; copy each run's slowest list from its log. Record every file at or above 60% of its limit (D69).
- [ ] **Step 2: Split or speed up** each such file: a split by test class into files named for what they test; a speed-up by sharing an expensive fixture per class. Show the test count before and after (equal).
- [ ] **Step 3: The cleanup error.** Load `superpowers:systematic-debugging`. Run `test_slot_run.py` alone 10 times and count the `WinError 32` errors. If the cause is a process the test started and does not wait for, fix the test and show 10 clean runs; if it is a process `slot_run.py` leaves running, fix nothing here and report it as a finding with its evidence.
- [ ] **Step 4: Re-measure** three runs. Expected: every file under 50% of its limit in all three.
- [ ] **Step 5: Commit** per file. `test: split <file> so the Windows leg keeps its per-file headroom`

### Task 7: Gate and pull request 1

- [ ] **Step 1:** Set `scripts/run_tests.min` to the discovered count.
- [ ] **Step 2:** Run `python -I scripts/gate.py` in the background; read `status.txt`. Expected: `# gate passed, …, tree <id>`. Then `gate.py --verify`.
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
  - `path_rules(snapshot: Optional[dict], repo: Path) -> List[PathRule]`;
  - `scope(repo, base, head, rules: Sequence[PathRule] = ()) -> List[Tuple[str, str]]` (D60 verdicts);
  - `is_test_file(path: str) -> bool`; `glob_match(pattern: str, path: str) -> bool`;
  - CLI `--session <id>` (default `CLAUDE_CODE_SESSION_ID`), the `# review.paths:` and `# rules` lines of D61.

- [ ] **Step 1: Write the failing tests** (fixture repositories built with `git init`):
  - one row per D60 reason and per lockfile name; `test_a_skip_the_diff_creates_is_reviewed` (Review Focus 2: a marker added in the diff, a marker on an added file, a new `vendor/` folder, an added `x.min.js`); `test_test_files_are_never_skipped_without_a_path_rule` (Review Focus 4); `test_binary_detection_reads_a_bounded_prefix` (Review Focus 7: a 20 MB file with a NUL at byte 10, the reader asked for 8,000 bytes, patched to count bytes kept);
  - `review.paths`: `test_glob_match_table` (`**` zero and many segments, `*` not crossing `/`, case); `test_project_entries_changed_in_the_diff_are_ignored` (Review Focus 3: the config file changed, and separately the named rules file changed); `test_rules_lines_name_matched_entries_only`; `test_no_snapshot_applies_no_rule`; `test_level_four_rules_file_outside_the_repository_is_dropped`;
  - `test_config.py`: `review` active above level 1; bad entries dropped one by one with their notice (not an object, both keys missing, glob over 200, 51 entries).
- [ ] **Step 2: Run** both files. Expected: `FAILED` on the new rows.
- [ ] **Step 3: Implement.** One `git cat-file --batch` process per call, fed the head (and, where D60 needs it, base) blob ids from `git ls-tree -z`; read exactly the header's size, keep the first 8,000 bytes.
- [ ] **Step 4: Run** both files on Windows and WSL. Expected: `OK`.
- [ ] **Step 5: Red runs** (full, trust): one mutant per D60 reason row (always skip, never skip), per "existed at base" clause, per test-file pattern, and on the order (a lockfile rule before the test rule); for `review.paths`, apply level-4 entries despite a changed config file, despite a changed rules file, and accept a `..` rules path (each red on its row).
- [ ] **Step 6: Commit.** `feat(rigger-core): review_scope skip reasons and review.paths`

### Task 9: `coverage-test-skipped`

**Files:**
- Modify: `plugins/rigger-core/tools/slot-check/slot_check.py`, `tools/slot-check/tests/test_slot_check.py`
- Create: `plugins/rigger-REVIEW/tests/contract/reviewer/fail-coverage-test-skipped.json` and the repository files it needs under that folder's `repo/`

**Interfaces:**
- Consumes: `review_scope.scope(repo, base, head, rules)`, `review_scope.path_rules`, `review_scope.is_test_file`.
- Produces: rule id `coverage-test-skipped` in `RULES["reviewer"]` and `GIT_RULES`; CLI `--session <id>`.

- [ ] **Step 1: Write the failing tests:** `test_a_skipped_test_file_is_refused`; `test_a_test_file_a_path_rule_skips_passes`; `test_a_skipped_generated_file_passes`. The fixture runs through `tests/test_module_contracts.py::test_every_contract_fixture`.
- [ ] **Step 2: Run** both. Expected: red on the new rows and on the fixture (no rule refuses it yet).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** both. Expected: `OK`.
- [ ] **Step 5: Red runs** (full): the rule never fires (refused test and fixture red); fires on every skip (generated row red); ignores path rules (path-rule row red).
- [ ] **Step 6: Commit.** `feat(rigger-core): the reviewer contract refuses a skipped test file`

### Task 10: The review module

**Files:**
- Create: `plugins/rigger-REVIEW/.claude-plugin/plugin.json`, `rigger-module.json`, `README.md`, `skills/review/SKILL.md`, `checklists/python.md`, `checklists/typescript.md`, `checklists/php.md`, `checklists/shell.md`, `tests/test_review_module.py`
- Move (`git mv`): `plugins/rigger-core/agents/reviewer.md` to the module's `agents/`, `plugins/rigger-core/tests/contract/reviewer/` to the module's `tests/contract/reviewer/`
- Modify: `plugins/rigger-core/rigger-module.json` (`contract` loses `reviewer`), `rigger-defaults.json:4`, `plugins/rigger-core/tests/test_core_wiring.py` (the reviewer tests at `:213-242` move to `test_review_module.py`), `tests/test_assembly_golden.py` and `tests/golden_baseline.py` (the layout gains the module; `SLOT_ROWS` reviewer row; one `ALLOWED` entry "the reviewer slot names the review module"), the reviewer-row pins of `test_assemble.py` and `test_stub_session.py:452`, `tests/read_by.json` if the lint derives a new read

**Interfaces:**
- Produces: the D56 manifest; agent `rigger-REVIEW:reviewer`; skill `rigger-REVIEW:review`; the checklist table of D63 inside the skill.

- [ ] **Step 1: Write the failing tests** in `test_review_module.py`: the moved reviewer pins, now reading the module's agent, plus "Load the `review` skill first"; `test_skill_names_the_scope_command_and_the_rules_lines`; `test_checklist_table_matches_the_files` (every row names an existing file, every file has a row, at most 25 bullets each); `test_manifest_fills_and_provides_the_reviewer`.
- [ ] **Step 2: Run** it. Expected: `FAILED`.
- [ ] **Step 3: Move and write** the files. The skill's text follows D64; the checklists hold only checks no linter or `defects.md` covers (a reviewer of each checks this in review).
- [ ] **Step 4: Run** `test_review_module.py`, `test_core_wiring.py`, `tests/test_module_contracts.py`, `tests/test_assembly_golden.py`, `test_assemble.py`, `tests/test_repo.py`, `tests/test_plugin_refs.py`, `tests/test_read_by.py`, `test_plugin_rules.py`, `test_drift_lint.py`, and `assemble.py --check` over 8 modules. Expected: `OK`, `findings 0`.
- [ ] **Step 5: Red runs** (low risk, one per new test): remove the skill's load sentence, a checklist file, the manifest's `fills`.
- [ ] **Step 6: Commit.** `feat(rigger-review): the review module fills the reviewer slot`

### Task 11: The testing module

**Files:**
- Create: `plugins/rigger-TESTING/.claude-plugin/plugin.json`, `rigger-module.json`, `README.md`, `hooks/hooks.json` (the two handlers of core's `hooks.json:91-102`, text unchanged), `bin/py.sh` (copy), `tests/test_testing_wiring.py`
- Move (`git mv`): rigger-core's `skills/testing/`, `tools/weak-test-guard/`, `evals/testing-skill-loads/`
- Modify: `plugins/rigger-core/hooks/hooks.json` (lose `:91-102`), `rigger-defaults.json:8`, `tests/test_repo.py:29` (`PLUGINS_WITH_PY_SH`), `scripts/run_tests.py:97` (the path), `plugins/rigger-rules/tests/test_plugin_rules.py:38` and `:445` (the rendered cell `rigger-TESTING:testing`), `tests/test_module_contracts.py` (D71), the testing-row pins of the golden test (one `ALLOWED` entry "the testing slot names the testing module") and `test_assemble.py`

**Interfaces:**
- Produces: skill `rigger-TESTING:testing`; the two commit handlers in the testing module.

- [ ] **Step 1: Write the failing tests:** `test_testing_wiring.py::test_both_commit_handlers_deny_a_weakening_commit` (D70: each handler run through `sh -c` from the module's `hooks.json`, with `CLAUDE_PLUGIN_ROOT` the module, on a temp repository whose staged commit deletes a test; expected one deny each, and nothing on a commit that adds a test); `test_module_contracts.py::test_testing_filler_ships_the_commit_guards` (D71).
- [ ] **Step 2: Run** them. Expected: `FAILED` (no module).
- [ ] **Step 3: Move and write.** `DuplicateHookTests` keeps `moved + 15` and two `weak_test_guard.py` handlers; check the `eval_stage.py` staging of the moved eval with `--keep-temp` (the case loads the module and rigger-core).
- [ ] **Step 4: Run** the moved `test_weak_test_guard.py`, the two new tests, `tests/test_repo.py`, `test_plugin_rules.py`, `test_drift_lint.py`, the golden test, `test_assemble.py`, `tests/test_read_by.py`, and `assemble.py --check` over 9 modules. Expected: `OK`, `findings 0`.
- [ ] **Step 5: Red runs** (full, wiring): drop the Bash handler, then the PowerShell handler, from the module's `hooks.json` (wiring test red each, and `test_repo.py`'s count); change one `if` to `Bash(*commmit*)` (wiring test red); empty `provides.testing` (contract test red).
- [ ] **Step 6: Commit.** `feat(rigger-testing): the testing module fills the testing slot and carries the commit guard`

### Task 12: The install notice, doctor and the listing

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/slots.py`, `assemble.py` (`_doctor` only), `tests/test_slots.py`, `tests/test_assemble.py`, `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `.claude-plugin/marketplace.json` (the two plugins after rigger-core), `plugins/rigger-core/README.md:21`, `plugins/rigger-core/skills/stack-tooling/SKILL.md:75` ("how to test is in the testing skill the Skill triggers table names")

**Interfaces:**
- Produces: `slots.INSTALL_HINT_SLOTS`; the D57 notice and doctor row.

- [ ] **Step 1: Write the failing tests:** `test_default_slot_without_its_plugin_names_the_install_command` (Review Focus 1: both slots, the exact line, at `startup`, `clear` and `compact`); `test_a_slot_set_above_level_one_keeps_its_own_notice` (no hint line); `test_superpowers_slots_stay_quiet`; the doctor `MISSING` row with its fix.
- [ ] **Step 2: Run** them. Expected: `FAILED`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the changed files, the golden test, `test_plugin_rules.py`, `test_drift_lint.py`, `tests/test_repo.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (shared): the hint for any level-1 default (superpowers test red); none at `compact` (compact row red); the doctor row `INFO` (doctor test red).
- [ ] **Step 6: Commit.** `feat(rigger-core): say how to install the review and testing modules when their slot is empty`

### Task 13: The second review round

**Files:**
- Modify: `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md:14` and its Fix rounds section, `plugins/rigger-core/commands/coordinate.md:74-76`, `plugins/rigger-core/tests/test_core_wiring.py`, `plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py`

- [ ] **Step 1: Write the failing pins:** `test_full_risk_changes_get_a_second_round` (the sentences of D65, whitespace-normalised); `test_a_finding_is_disputed_only_with_a_quoted_line`; `test_coordinate_marks_a_full_risk_part_reviewed_after_round_two`.
- [ ] **Step 2: Run** both files. Expected: `FAILED`.
- [ ] **Step 3: Edit** the two texts as D65 says; keep `:151-154` as they are.
- [ ] **Step 4: Run** both files, `test_plugin_rules.py`, `test_drift_lint.py`. Expected: `OK`.
- [ ] **Step 5: Red runs:** remove each new sentence (its pin red).
- [ ] **Step 6: Commit.** `docs(rigger-core): a second fresh review round for full-risk changes`

### Task 14: Stub exit criteria and a real review

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/tests/test_stub_session.py`
- Create: `docs/superpowers/spikes/phase-3a/release-review.md`

- [ ] **Step 1: Write the stub tests** (each starts `claude -p` against the stub with the D-constraints above): `test_new_modules_fill_their_slots` (with both modules in `--plugin-dir` and `RIGGER_MODULE_DIRS`, the first request's `## Slots` holds `rigger-REVIEW:reviewer` and `rigger-TESTING:testing`, and the trigger cell names the testing module); `test_without_them_the_session_names_the_install_commands` (both lines arrive).
- [ ] **Step 2: Run** them. Expected: red on unchanged `test_stub_session.py` expectations, then `OK` after writing them; the report says "stub".
- [ ] **Step 3: Measure again** with `scripts/hook_latency.py` on Windows and WSL: the testing module's two handlers are measured and match their Task 3 numbers within the run's spread.
- [ ] **Step 4: Real review** (spec §13 exit criterion): in the builder's session, dispatch the review module's `reviewer` agent on this repository's last release diff, `v0.5.0..v0.6.0` (146 files, 11,644 insertions; base and head as full commit ids) with a scratch folder; save its reply; run `slot_check.py reviewer <reply file> --base <base> --head <head>`. Expected: `slot-check: ok reviewer <n> findings`, every in-scope path covered, every quote anchored. Record the command, the counts and the result line in `release-review.md`; findings are listed by id and title only.
- [ ] **Step 5: Sweep and commit.** `test(rigger-core): stub exit criteria for the review and testing modules`

### Task 15: Docs, release notes, migration

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), both new `README.md`, `CONTRIBUTING.md` (`tests/read_by.json`; contract fixtures now under the review module), the spec file (the "Spec corrections" rows)

- [ ] **Step 1: Unreleased entries:** the eight behaviour changes; the migration, one step: `claude plugin install rigger-review@rigger rigger-testing@rigger`, then `/rigger-core:doctor`; for external reviewers, the new refusal rule and the `review.paths` lines; that 3b moves rigger-windows' test tools.
- [ ] **Step 2: Apply "Spec corrections"**, one Edit per row.
- [ ] **Step 3: Stale text:** `git grep -n -e "rigger-core:reviewer" -e "rigger-core:testing" -e "recorded only" -- '*.md' ':!docs/superpowers' ':!CHANGELOG.md'`; correct each hit.
- [ ] **Step 4: Sweep** each changed file and run `tests/test_plugin_refs.py`. Expected: `findings 0`, `OK`.
- [ ] **Step 5: Commit.** `docs: phase 3a review and testing modules`

### Task 16: Gate and pull request 2

- [ ] **Step 1:** Set `scripts/run_tests.min` to the discovered count. Run `python -I scripts/gate.py` in the background; read `status.txt`; `--verify`.
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
| Read-by map and lint (Task 1) | 3.5 | 160 (the map file 40) | 260 |
| `gate.py --changed-modules` (Task 2) | 2 | 70 | 160 |
| Hook latency (Task 3) | 2.5 | 230 | 120 |
| Identity in `resolve_program` (Task 4) | 1.5 | 30 | 90 |
| Batch renders the rules (Task 5) | 1.75 | 70 | 150 |
| Windows leg timing (Task 6) | 2 | 40 (splits move lines, not counted) | — |
| Skip reasons and `review.paths` (Task 8) | 4.5 | 300 | 420 |
| `coverage-test-skipped` (Task 9) | 1.25 | 50 | 90 |
| Review module: skill, 4 checklists, manifest, README (Task 10) | 4 | 450 (Markdown counted as code) | 150 |
| Testing module (Task 11) | 2 | 80 | 80 |
| Install notice, doctor, listing (Task 12) | 3 | 90 | 220 |
| Second review round (Task 13) | 1 | 25 | 40 |
| Stub run, real review (Task 14) | 2.5 | — | 150 |
| Docs, two gates, two pull requests (Tasks 7, 15, 16) | 3.5 | — | — |
| **Total** | **35** | **about 1,600** | **about 1,930** |

About 3,500 lines move unchanged with `git mv` and are not counted.

Against the decisions doc: its 42 h cover 3a and 3b together. The spec's phase 3 rows that land in
3a (the review module 750 and 900 lines, a share of the wording and the manifests) come to about
1,000 code and 1,050 test lines; with decisions doc D5–D7 (5 h) that is about 21 h for 3a. This
plan adds what neither priced: the read-by map and the gate mode (5.5 h), the trust rules of
`review.paths` and the "existed at base" clauses (2 h), the install notice (3 h), the measurement's
CI leg, and two pull requests with a full gate and two review rounds each. Phase 2 ran at 2.7
times the spec's hours.

**Simpler version, about 27 h, 1,300 code lines, 1,450 test lines:**
- **What it keeps:** both modules with the moves, the slot defaults and the install notice; the
  skip reasons; the read-by map with its lint; the second review round; the measurement; the
  backlog.
- **What it drops, with what each saves:**
  - `gate.py --changed-modules` (2 h, 70 and 160 lines): the mode with the map stays a pre-check,
    so decisions doc D1's "becomes the gate" is not met;
  - `review.paths` (2.5 h, 150 and 220 lines): no per-path rules and no named skips, so a test
    file is never skipped;
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
   at base, and changing it in the diff switches it off for that review (D61), but text present at
   base still steers the reviewer.
5. **`CLAUDE_CODE_SESSION_ID` was measured in phase 2's Q3 on that CLI version.** If a later CLI
   drops it, `review_scope.py` applies no path rules and says so, and `slot_check.py` fails closed
   on a skipped test file.
6. **People who update rigger-core and skip the migration** see two notice lines in every session
   until they install the modules or set the slots themselves. That is the intended cost of D57.
7. **The `test_slot_run.py` cleanup error** may come from a process `slot_run.py` leaves running on
   Windows (P2-E14 open question 1). Task 6 decides which; a product fix is then its own item.
