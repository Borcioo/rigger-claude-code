# Modular architecture, phase 2: slots and contracts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slots get validated fillers, a trust filter and a `## Slots` table; reviewer, worker,
implementer and mapper output is checked against machine contracts; a command can fill the reviewer
slot; and the three skill-trigger tables become one that drops rows of absent plugins.

**Status:** draft for review, written 2026-11-24 on `1f1e204` (release 0.5.0, which holds phase 1).

**Architecture:** New logic lands in new files. `plugins/rigger-core/tools/assemble/slots.py`
resolves and validates slots from the four config levels and applies the trust filter.
`tools/review-scope/review_scope.py` lists what a review must cover, `tools/slot-check/slot_check.py`
checks a reply against its slot's contract, and `tools/slot-run/slot_run.py` runs a command slot
with no shell and checks its output. One serial wiring task connects them to `assemble.py`, which
prints the `## Slots` section and the merged table. Three pull requests: the test mode first, the
phase 1 backlog second, the slot work third.

**Tech Stack:** Python 3.9+ stdlib only (run through `bin/py.sh` with `-I -X utf8`), POSIX `sh`
launchers, `unittest`, `git`, `plugins/rigger-rules/tests/stub_api.py` for `claude -p` runs.

**Spec:** `docs/superpowers/specs/2026-11-23-modular-architecture-design.md`. This plan covers §13
"Phase 2" with §7.4, §9.3, §10 and §12. It also takes the phase 1 backlog and
`run_tests.py --changed-modules` (§12), which the phase 1 pull request named as the opener of
phase 2. Phases 3–5 get their own plans.

## Global Constraints

**Scope and promise**
- Phase 2 changes behaviour in exactly the ways listed under "Behaviour changes" below. Everything
  else stays byte-identical, and the golden test (Task 16) is the measure.
- Base: `1f1e204`. Rigger has no `develop`: each pull request goes to `main` and is squash-merged
  after CI passes. Merging and releases are the owner's steps.
- Rigger is a public repository. Nothing under `plugins/`, `scripts/`, `tests/` or `docs/` names a
  company, client, person or private repository, or holds an absolute local path. Examples stay
  generic. The review-module ideas are described by what they do, never by their source.
- Code, identifiers, comments and commit messages are in English. Conventional Commits, with no
  attribution lines.
- Planned plugins that do not exist yet are written `rigger-REVIEW`, `rigger-TESTING` and
  `rigger-SHELLWISE` wherever a `:` or a `plugins/` path follows, so `tests/test_plugin_refs.py`
  passes.

**Runtime**
- Python floor is 3.9: no `match`, no runtime `X | Y` unions, no 3.10+ stdlib calls.
- Hooks run `py.sh` with `-I -X utf8`. A script inserts its own folder into `sys.path` first, as
  `assemble.py:25-26` does. A tool that imports from another tool folder of rigger-core inserts
  that folder by a path built from its own `__file__`.
- Hook output is written as LF bytes through `sys.stdout.buffer`, never `print`: a Windows Python
  writing to a pipe emits CRLF, and the 10,000-character hook cap counts CR bytes.
- Each part's body stays at most 9,000 bytes and the emitted handler output under 9,500 bytes.
- Launchers stay POSIX `sh` (dash on Linux, Git Bash `sh.exe` on Windows). Every hook handler
  always exits 0.
- Files are read as bytes and decoded as UTF-8 explicitly. Every write passes `encoding="utf-8"` or
  writes bytes.
- A plugin never reaches a sibling plugin by relative path. A tool of rigger-core may import another
  tool of rigger-core. A repository-level test under `tests/` may read every plugin.
- `bin/py.sh` is not changed. `hooks/core_check.sh` exists once per plugin whose rules rigger-core
  prints, and the five copies are byte-identical (`tests/test_repo.py:182`): change every copy in
  one commit.
- A path that only has to be absolute is made with `abspath`, never `Path.resolve()`, which rewrites
  a symlinked temp folder on macOS.
- A `sh` scan of `installed_plugins.json` stays one awk pass that reads the file as one record, and
  a time bound is measured at two sizes (500 and 2,000 entries), under gawk, mawk and busybox awk.

**Tests and runs**
- Tests are `unittest` files that `scripts/run_tests.py` discovers. Unit tests of
  `tools/<tool>/<name>.py` live in `tools/<tool>/tests/test_<name>.py`.
- Every new test gets one red run on its own assertion, named in the task's report. The full-risk
  checks of this plan are broken once per branch, table row, stage and wiring:
  - the trust filter (Task 10);
  - slot validation of a project file's values (Task 10);
  - the trigger-row caps (Task 3);
  - revision and path handling in `review_scope.py` (Task 12);
  - every refusal rule of `slot_check.py reviewer` (Task 13);
  - argv building, environment stripping, the bounds and the refusal paths of `slot_run.py`
    (Task 14).

  Phase 2 adds no leak-guard code (spec §11.4 is phase 4). The shared-file exit of
  `--changed-modules` (Task 1) and the lost-part lines (Task 4) are shared infrastructure: one
  mutation per branch, up to 5 per check, the rest listed in the report.
- A Python mutant that keeps the file's size and mtime second can load a stale `.pyc`: run each
  mutant with `-X pycache_prefix=<dir per mutation>`.
- `run_tests.py` strips `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`. A test that needs one sets
  it itself.
- A test that starts `pwsh` in a fake home sets `POWERSHELL_TELEMETRY_OPTOUT=1`. A test that fakes
  the home for a Python child sets `HOME` alone.
- Git Bash `sh.exe` started from Python re-parses its command line: tests pass no quote or glob
  characters as arguments, or put them inside an `sh -c` string. A CR-sensitive awk mutant runs on
  the POSIX leg too, because Git Bash's gawk drops the CR of a CRLF line.
- A test that runs git on the real checkout under WSL sets `GIT_DIR` and `GIT_WORK_TREE` as
  `tests/test_repo.py` `git_env()` does. A test that needs a repository builds its own with
  `git init` in a temp folder.
- `tests/test_shellcheck.py` lints tracked files only: `git add` a new `.sh` file before its first
  run.
- A test of `run_tests.py` or `gate.py` `main` patches the part that runs things, even when it
  expects an early exit.
- A change to a rules file, a skill, an agent or a command file runs
  `plugins/rigger-rules/tests/test_plugin_rules.py` and
  `plugins/rigger-windows/tools/drift-lint/tests/test_drift_lint.py` in the same round as the edit.
  Text moved into a placeholder leaves a stale drift-lint baseline row, which is itself a finding.
- A "base unchanged" check names the `rules/` and `hooks/` paths it is about, never a whole plugin
  folder.
- A hook-wiring test needs a fixture whose sections each fill a part. A red run that mutates
  discovery pins the expected side to an independent source, such as the manifests.
- This plan adds no hook handler: `tests/test_repo.py` keeps `moved + 15`. A task that finds it
  needs one adds `tests/test_repo.py` (`DuplicateHookTests`) to its files and says so.
- `scripts/run_tests.min` reads 71 today. Only Task 15 changes it, once, to the count
  `run_tests.py` discovers after wave C (expected 75).
- Gate (local): `python -I scripts/gate.py` in the background, started on Windows with the full
  path of the installed Python. Read the `status.txt` its first line names. Between fixes,
  `python -I scripts/run_tests.py --fix-round`, with `--no-claude` when no `claude` run is allowed.
  Before a pull request, `python -I scripts/gate.py --verify <status.txt>`; when the base moved,
  rerun the gate.
- Every task of pull requests 2 and 3 touches a file that `--changed-modules` calls shared, or
  rigger-core, on which five modules depend. Their gate is the full gate. `--changed-modules` is
  proved on its own tests in Task 1 and first used for real in phase 3.

**Real `claude` runs (spikes and Task 19)**
- Every `claude` command runs under a fresh temp `CLAUDE_CONFIG_DIR`, one per experiment. A
  `--plugin-dir` run also sets `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to scratch
  folders, leaves `RIGGER_LESSONS_BATCH` unset, and names the same roots in `RIGGER_MODULE_DIRS`.
- The child environment drops `CLAUDE_*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`, keeps
  `CLAUDE_CODE_CHILD_SESSION`, then sets `ANTHROPIC_BASE_URL` to the stub. Before the first run,
  prove the isolation with a canary on a scratch config and abort on any failure.
- Read `claude --version` and `command -v claude` right before the first run and record both.
- Use a bounded timeout and `Popen` streaming stdout.
- A hook proof reads `Hooks: <Event>:<matcher>` from `--debug-file`, or output that differs per
  firing. It fires the exact trigger its matcher names (`/compact` fires `manual`). A compaction
  probe's first turn carries no marker. `claude -p --resume` drops resume-hook text, and `|| true`
  on a launcher hides a failure.
- The stub finds a request by a unique word in the probe's prompt, never by recency. A stub handler
  recognises its trigger by the expanded `--plugin-dir` path.
- A module loaded through `--plugin-dir` reads its options from `pluginConfigs["<plugin>@inline"]`.
- `claude -p` disables `AskUserQuestion` and rewrites `CLAUDE_CODE_ENTRYPOINT=cli`; no spike here
  depends on either.
- Scratch goes to `<temp>/rigger-p2/<task>/`, outside the repository. A copied repository deletes
  its `.git` first.

**Docs and sweep**
- Before committing a doc, run `python -I scripts/sweep.py --no-denylist <file>`. It must print
  `# files 1, findings 0`. The host rule reads a dotted code attribute as a domain.
- Spike result files are public: temp paths are written as `<temp>` and the home as `<home>`.
- A script that handles denylist terms masks every line it writes. No task here handles one.

## Behaviour changes

These are the differences a person sees after phase 2. The release notes list each one.

1. One `## Skill triggers` table instead of three. It is printed by rigger-core, so a setup
   without rigger-rules gets the table too (D38).
2. A row whose every skill belongs to a plugin that is not enabled disappears, and a row keeps only
   the skills of enabled plugins. Without superpowers the testing row reads `rigger-core:testing`
   alone.
3. The section `## Windows skills, loaded before the first step of their kind` is gone. The
   rigger-wp section keeps its sentence about site copies under the heading
   `## WordPress site copies`.
4. A new `## Slots` section in the main session.
5. `slots` and `trust` in a config file now have an effect.
6. A rules part that fails or runs out of time prints one line of its own (D29).
7. `core_check.sh` also runs on `compact`, if spike Q1 shows its line arrives there (D31).
8. A config file may hold at most 50 trigger rows, and a `load` entry at most 200 characters (D28).

## Baselines measured for this plan

Every number below comes from a command run on `1f1e204` on Windows 11 with Git Bash and Python
3.12. `<repo>` is the worktree root.

```text
$ python -I -X utf8 plugins/rigger-core/tools/assemble/assemble.py --render   (7 roots in RIGGER_MODULE_DIRS, empty config dir)
27945 bytes; "| Before | Skill |" headers: 3; table rows: 13 (rigger-rules table 8, windows 3, wp 2)
$ ... --render --subagent | grep -c '^| .* | `'
13
$ ... --check --modules plugins/*/
# external: superpowers
# check: modules 7, rules files 19, findings 0
$ grep -c '^| .* | `' tests/fixtures/golden-34e7713/plugins/{rigger-rules/rules/skills.md,rigger-windows/rules/windows-skills.md,rigger-wp/rules/wp-skills.md}
8, 3, 2
$ cat scripts/run_tests.min
71
$ run_tests.discover(ROOT) per folder
tests/ 15, rigger-core 29, rigger-windows 10, rigger-lessons 8, rigger-rules 5, rigger-wp 2, rigger-talk 1, rigger-panel 1
$ HARNESS_NO_CLAUDE=1 python -I -X utf8 tests/test_repo.py
Ran 106 tests in 41.583s   OK (skipped=2)
$ python -I -X utf8 tests/test_module_contracts.py ; python -I -X utf8 tests/test_plugin_refs.py
Ran 3 tests in 0.374s OK ; Ran 2 tests in 0.427s OK
$ git ls-files | wc -l
481
```

Phase 1 backlog, reproduced:

```text
$ sh "<temp>/cc/cache/rigger/rigger-rules/0.5.0/hooks/core_check.sh" rigger-rules      (old core in the listing)
Rigger rules not loaded for rigger-rules: rigger-core is older than rigger-rules (run claude plugin update rigger-core)
$ sh "<temp>\cc\cache\rigger\rigger-rules\0.5.0\hooks\core_check.sh" rigger-rules      (same file, all backslashes, another cwd)
(no output, exit 0)
$ sh "<repo>\plugins\rigger-core\tools\assemble\part.sh" 1                             (all backslashes, another cwd)
sh: ./../../bin/py.sh: No such file or directory
Rigger rules not loaded: the assembler exited 127 (run /rigger-core:doctor)
$ part.sh 1 / 2 / 3 / 2 --subagent with an assemble.py that exits 3
part 1: Rigger rules not loaded: the assembler exited 3 (run /rigger-core:doctor)
part 2: (no output)   part 3: (no output)   part 2 --subagent: (no output)
$ five core_check.sh starts in sequence, 3 rounds
523 ms, 533 ms, 512 ms
$ the planned folder line, `case $0 in *[\\/]*) here=${0%[\\/]*} ;; *) here=. ;; esac`, in a probe script
Git Bash sh: forward-slash path -> its folder; all-backslash path -> its folder; bare name -> .
dash (WSL):  ./name -> . ; bare name -> . ; absolute path -> its folder
```

Slot contracts against today's default fillers:

```text
$ grep -n '"if"' plugins/rigger-windows/hooks/hooks.json
7 Bash handlers, each behind an `if` filter; the PowerShell handler (--pwsh-all) has none
$ shell_guard.py --pwsh-all on {"tool_name":"PowerShell","tool_input":{"command":"Get-ChildItem"}}, 5 runs through py.sh
399, 347, 364, 345, 355 ms, no output      (a bare `sh -c 'exit 0'` on the same machine: 82, 81, 92 ms)
$ shell_guard.py --only-narrow on a Bash `ls` event, started directly (in a session its `if` filter starts no process)
327, 293, 331, 331, 320 ms
$ shell_guard.py:3338-3343
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
 "permissionDecisionReason": "shell-guard: <message> (False positive or intended? Add a trailing `# <marker>`.)"}}
$ lessons_start.py:108
"Lessons inbox: %s: %d entries"
$ git -c core.quotepath=off --no-pager diff --no-ext-diff --no-textconv --no-color --no-renames --name-status -z 42ae774 1f1e204
M\0CHANGELOG.md\0        (git version 2.53.0.windows.1)
$ git rev-parse --verify "<rev>^{commit}" in a one-commit repository with a tag on a blob
HEAD -> the commit id, exit 0
--output=x -> fatal: Needed a single revision, exit 128, no file x written
-p -> fatal: Needed a single revision, exit 128
blobtag -> error: blobtag^{commit}: expected commit type, but the object dereferences to blob type, exit 128
no-such-rev -> fatal: Needed a single revision, exit 128
```

The lines the spec asks to reword, checked with `cat -n`:

| Spec reference | What the line holds today |
|---|---|
| `agents/worker.md:8-10` | "Load `rigger-core:testing` before writing code or tests and, where superpowers is installed, …" |
| `agents/reviewer.md:17` | "(procedure in `rigger-core:testing`)" |
| `skills/orchestrating-subagents/SKILL.md:86` | "`rigger-core:mapper` (locations only)" |
| `SKILL.md:89-93` | "Agent type: `rigger-core:worker` for implementation, `rigger-core:reviewer` for review …" |
| `SKILL.md:122` | "lessons inbox (rigger-lessons: `lessons-inbox.md` in the config dir)" |
| `SKILL.md:147` | "two fresh read-only `rigger-core:reviewer` agents" |
| `commands/coordinate.md:16` | "Load `rigger-core:orchestrating-subagents` first." |
| `commands/coordinate.md:49` | "(the session loads `rigger-core:testing` and runs only those; …" |
| `commands/coordinate.md:62` | "loads `superpowers:brainstorming` and `superpowers:writing-plans`" |
| `templates/implementer-brief.md:3` | "(`rigger-core:worker`, `rigger-core:implementer`, `general-purpose`)" |
| `templates/implementer-brief.md:50-51` | "`rigger-core:worker` and `rigger-core:reviewer` load them themselves, `rigger-core:implementer` gets none" |

Tests that pin text this plan changes (`git grep`):
`plugins/rigger-rules/tests/test_plugin_rules.py:310-333` (`SkillTableTests`), `:420-436`
(`TestingSkillTriggerTests`, which also pins the `worker.md` sentence), `:37`
(`RULES_CEILING_BYTES = 27708`),
`plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py:99` (the `coordinate.md:49`
sentence), `plugins/rigger-core/tests/test_core_wiring.py` (reads `reviewer.md`),
`plugins/rigger-core/tools/assemble/tests/test_part_sh.py:93-94` (parts above 1 print nothing),
`plugins/rigger-rules/tests/test_core_check.py:266` (matcher `startup|clear`),
`tests/test_repo.py:548` (the same matcher), `test_config.py:243-259` and
`test_doctor_sh_plugins.py:369-373` (`slots` above level 1 is inactive),
`tests/test_assembly_golden.py` (three tables), `test_stub_session.py` and `test_reinject.py:117`
(19 sections).

Tests that pin the count of rules files, 19 today and 18 once `windows-skills.md` is deleted:
`tests/test_repo.py:130` (`assertEqual(checked, 19)`, the sum of manifest rules entries),
`plugins/rigger-core/tools/assemble/tests/test_sections.py:69` and
`plugins/rigger-rules/tests/test_rules_hooks.py:318` (`assertGreaterEqual(len(files), 19)` over
`plugins/*/rules/*.md`), and `RULES_MIN_FILES = 19` at `test_plugin_rules.py:38`.

## Review Focus

1. **A project file that sets only `target` on a slot the person made a command at level 3.** A
   person expects the cloned repository to be unable to change which program runs. Fields merge one
   by one (spec §7.3), so the filter looks at where `kind` and `target` come from, not at whether
   level 4 wrote `kind: command`. Test: Task 10, `test_l4_target_alone_cannot_rewrite_a_command`.
2. **A reviewer finding whose `file` leaves the repository** (`../../secrets`, an absolute path, a
   drive path, a symlink out of the tree), or whose file has CRLF endings, tabs or bytes that are
   not UTF-8. A person expects the first refused without the file being read, and the others
   anchored on whitespace-normalised text. Test: Task 13,
   `test_finding_path_outside_the_repository_is_refused_unread` and
   `test_anchor_normalises_whitespace_and_line_endings`.
3. **A reviewer command that never exits, prints without end, or leaves a child running.** A
   person expects a bounded wait, the whole process tree stopped, and a refusal, never a hung
   session. Test: Task 14, `test_timeout_kills_the_tree_and_refuses` and
   `test_output_over_the_cap_refuses`.
4. **A base revision that reads like an option** (`--output=x`, `-p`), names nothing, or names a
   tag object. A person expects a refusal before any diff runs. Test: Task 12,
   `test_revision_that_looks_like_an_option_is_refused`.
5. **No superpowers plugin.** A person expects a quiet session: no notice per empty process slot,
   no row that names a skill they do not have, and no sentence that tells them to load one. Test:
   Task 15, `test_default_install_without_superpowers_is_quiet`.
6. **Two enabled plugins that both fill `shell-guard`.** A person expects one line naming both at
   every start and after every compaction, and both names in the `## Slots` row. Test: Task 10,
   `test_two_fillers_give_one_line_naming_both`, and Task 15 for the compaction path.
7. **A personal `rules.d` file that overrides a section the merge moves or removes**
   (`skills.md` with `## Skill triggers`, or `windows-skills.md`). A person expects the first to
   keep replacing the table with the override notice, and the second to print as their own
   section, never to vanish. Test: Task 16, `test_overrides_of_merged_table_sections`.
8. **A snapshot written by release 0.5.0, read after the plugins were updated in the middle of a
   session.** A person expects compaction to read the rules again with one notice, and
   `slot_run.py` to refuse with a line that says to start a new session. Test: Task 15,
   `test_format_one_snapshot_is_read_again`, and Task 14,
   `test_old_or_missing_snapshot_refuses`.
9. **A subagent report of 300 KB, one with two JSON blocks, or one whose JSON is cut off.** A
   person expects one shadow row inside the 2.5 s bound and no traceback. Test: Task 17,
   `test_long_and_broken_reports_get_a_row_in_time`.
10. **A branch that deletes a module folder, adds an untracked file under `tests/`, or moves a
    file between two modules.** A person expects `--changed-modules` to exit 2 for the first two
    and to run both modules' tests for the third. Test: Task 1,
    `test_deleted_module_and_untracked_shared_file_exit_two` and `test_moved_file_runs_both_modules`.

---

## Decisions this plan makes

Phase 1 used D1–D24. Each task below follows these.

| # | Decision | Why |
|---|---|---|
| D25 | Three pull requests, in this order: (1) `run_tests.py --changed-modules`, branch `feature/changed-modules`; (2) the phase 1 backlog, branch `fix/phase-1-backlog`; (3) slots and contracts, branch `feature/modular-phase-2`, cut from `main` after the first two are merged. Spikes Q2–Q4 run while (1) and (2) are in review. | Phase 1's D15 asked for the mode as its own pull request. The backlog fixes are small, independent of slots, and Task 3 edits `manifest.py` and `config.py`, which wave C also edits. |
| D26 | `--changed-modules` maps each changed path this way. **Shared, exit 2:** `scripts/**`, `tests/**`, `plugins/*/rules/**`, `plugins/*/bin/py.sh`, `plugins/*/hooks/core_check.sh`, `plugins/rigger-core/rigger-defaults.json`, `.github/**`, `.claude-plugin/**`, any other path outside `plugins/` that is not documentation, and any path under `plugins/<name>/` whose folder holds no `rigger-module.json` in the working tree. **Documentation, no module:** `docs/**` and `*.md`, `LICENSE*` or `NOTICE*` at the repository root. **Module:** everything else under `plugins/<name>/`. The mode then runs every discovered test file under each changed module and under each module whose `plugin.json` `dependencies` reach a changed module, plus `tests/test_module_contracts.py`, `tests/test_repo.py`, `tests/test_plugin_refs.py` and the sweep with `--no-denylist`. A git failure is exit 2, never a silent full run. | Spec §12 lists `scripts/`, `tests/`, rules, the defaults file, `rules_start.sh` and `py.sh`. Added: `core_check.sh` (five identical copies, like `py.sh`), the workflow and the marketplace listing, which no module owns. `tests/test_repo.py` pins handler counts and reads every tracked file, so a `hooks.json` edit needs it; it took 42 s here. Dependents are added because rigger-rules' tests run the assembler of rigger-core. The test set is discovery, since `test_tests_paths_cover_every_discovered_test_file` (`tests/test_repo.py:154`) already pins `tests.paths` to it. |
| D27 | CI stays as it is in this plan: the full gate on Linux and the full suite on macOS and Windows for every pull request. | Spec §12 has CI run the mode. Today's workflow is stricter and is what protects `main`. Question 1. |
| D28 | A config level (2, 3 or 4) may hold at most 50 trigger rows: the rows after the 50th in file order are ignored with one notice that gives the count. A `load` entry is at most 200 characters, in a manifest and in a config file; a longer one makes its row invalid with the reason `load entry over 200 characters`. Level 1 is exempt from the row cap. | Backlog item RV1 F10, with the values the phase 1 builder proposed. The longest `load` entry today is `rigger-core:orchestrating-subagents`, 35 characters. |
| D29 | A part above 1 prints its own line when its assembler fails: `Rigger rules part <i> of <K> not loaded: <reason> (run /rigger-core:doctor)`, wrapped as `additionalContext` with `--subagent`. The no-Python case stays one line from part 1, because no part can succeed then. `assemble.py --part` also starts a watchdog: after 8 s (the hook timeout is 10 s) it prints the same line with the reason `timed out after 8 s` and exits 0. | Backlog item RV4 M6. Each line differs by its part number, so identical-text deduplication cannot hide one. A process killed by the hook timeout cannot print, so the bound has to sit inside it. Q1 proves a line printed at 8 s arrives. |
| D30 | `core_check.sh` and `part.sh` derive their folder from `$0` by cutting at the last `/` or `\`. | Measured above: an all-backslash script path makes `core_check.sh` silent and makes `part.sh` print the failure line in place of the rules. `hooks.json` passes forward slashes today, so this is robustness only. `part.sh` has the same line and a worse effect, so it is fixed in the same pull request. |
| D31 | The five `core_check.sh` handlers get the matcher `startup|clear|compact` if spike Q1 shows that a line already printed at startup arrives again after `/compact`. If Q1 shows it is dropped, the matcher stays `startup|clear` and the release notes say why. | Backlog item RV4 M5. With an old rigger-core the rules are missing after a compaction as well, and the startup line is summarised away. The cost is 5 `sh` starts per compaction with no Python: 512–533 ms in sequence here, about 0.12–0.15 s each in parallel (phase 1 review), next to a compaction that already starts 8 or more handlers (6 parts, `compact_guard.py`, `worktree_root.sh`) and one model call. |
| D32 | `slots` at levels 2–4 and `trust` at level 3 become active. `review`, `shell_guard`, `company` and `leak_guard` stay recorded-only, and doctor keeps listing them as `config-inactive`. | Spec §13 phase 2. Their readers ship in phases 3 and 4. |
| D33 | The trust filter works on provenance. For each slot path (`reviewer`, `process.tdd`) it compares the entry merged from levels 1–3 with the entry merged from levels 1–4. If the second has `kind: command` and level 4 set `kind` or `target` for that path, and the path is not in level 3's `trust.project_commands`, the whole level-4 value for that path is dropped, `model` included, with the line of spec §7.4 carrying the real config path. A level-4 `null` and a level-4 `model` alone are allowed. `trust` set at level 4 is already refused by the level table. A level-2 command (a company module the person installed) is allowed. | Spec §7.4 says "may not set `kind: command`", but an L4 `target` alone would rewrite an L3 argv. Review Focus 1. |
| D34 | Validation walks down the levels: the entry merged from levels 1–4 (after D33), then 1–3, 1–2, then 1. The first that validates fills the slot. A failed entry set above level 1 gives one notice, `rigger: slot <path> from <file or module> ignored: <reason>; using <level name>` or `…; the slot is empty`. A level-1 default that does not validate because its plugin is not enabled leaves the slot empty with no session notice; doctor lists it as INFO. A `null` for one slot path empties that slot. A `null` for `slots` itself or for the `process` group, at any level above 1, is ignored with the notice `rigger: config <file> may not set <key> to null; set a single slot to null instead`. | Spec §7.3 step 5 gives one notice per fallback. Superpowers is optional, so four notices in every session without it would be noise about a supported setup (Review Focus 5). `config.py:130-131` passes a `null` through for any key, and `merge` then removes the key: one line in a cloned repository would empty every slot without a word. |
| D35 | A `hook` slot is filled by every enabled module whose manifest has `fills: <slot>`. rigger-windows gets `fills: "shell-guard"`, `failure_mode: "open"` and `provides.shell-guard.rule_checker: "tools/shell-guard/lessons_guard.py"`; rigger-lessons gets `fills: "lessons"`. The config entry is the expected filler: when it is set above level 1 and that plugin is not enabled, one notice names it. Two fillers give `rigger: slot <slot> is filled by <A> and <B>; disable one with claude plugin disable <name>`. The assembler prints that line as a notice at `startup`, `clear` and `compact`; `harness_start.py` is not changed. | Spec §10.2 places the two-filler check in `harness_start.py`, which runs at `startup` only and would resolve modules a second time. One resolver, one place for notices (phase 1's D13). |
| D36 | `## Slots` is a generated section, id `slots.slots`, printed for the main session only, under rigger-core's root line, after core's rules files. One row per slot path whose `requires` is met: `Slot`, `Kind`, `Target`, `Model`, `From` (`default`, `company:<module>`, `personal`, `project`). An empty slot shows `—` as its target. A hook row shows its fillers joined with ` + ` and the failure mode, `rigger-windows (fails open)`. One line follows the table only when a slot is a command: how to run `slot_run.py`, with the core root and the session id filled in (Q3 decides its exact form). A `rules.d` section with the id `slots.slots` does not replace it: it is a duplicate, with the usual notice. `HARNESS_RULES_DROP` drops it like any section. | Spec §10.1. The override ban keeps the table true: a personal text must not be able to show a filler that is not the one in effect. |
| D37 | Rules text gets one new construct: `{{if <items>}}` … `{{end}}`, where `<items>` is one or more `requires` items separated by one space (`slot:<path>`, `plugin:<name>`, `os:<name>`). The region may span lines, cannot nest, and is removed with its markers unless every item is met (a slot counts as met when it is filled). An unclosed or nested marker prints as written and is a `--check` finding. A `{{slot:<path>}}` of an empty slot outside a region prints as written and is a `--check` finding; a `{{skill:<plugin>:<skill>}}` renders its name whether or not the plugin is enabled, as today. | Spec §9.2 says a sentence's `requires` drops it, and §10.4 that an empty role drops its `{{slot:…}}` sentences, but rules text has no way to mark a sentence. One use today: `plans-main.md:3-4`. |
| D38 | The merged table lives in `plugins/rigger-core/rules/skills.md` (audience `both`), moved from rigger-rules with `git mv`, so its id stays `skills.skill-triggers`. Its intro becomes `- Load the skill before the first step of its kind:`. `windows-skills.md` is deleted with its manifest entry. `wp-skills.md` keeps its two-line sentence under the new heading `## WordPress site copies` and loses its placeholder. `{{triggers:<id>}}` renders every row whatever the id, and `section` in a row is ignored but stays valid. A second `{{triggers:…}}` placeholder renders nothing, with a notice, and is a `--check` finding. Row order is spec §9.3: module rows in module order, then config rows. | With the table in rigger-rules, a setup with rigger-core and rigger-wp but no rigger-rules would lose the wp rows it prints today. Today's order (core 5, slot rows 3, windows 3, wp 2) becomes core 5, windows 3, wp 2, slot rows 3. Question 7. |
| D39 | A `load` entry is dropped when its plugin is not in `Discovery.enabled` or its slot is empty. A row is dropped when no entry is left or its `requires` is unmet. `Discovery.enabled` also gets the `plugin.json` name of a `RIGGER_MODULE_DIRS` entry that holds `.claude-plugin/plugin.json` but no `rigger-module.json`, with no notice; an entry with neither file keeps today's notice. | Spec §9.3. Without the second sentence a plugin loaded with `--plugin-dir` that is not a module (superpowers in a stub run, an eval or a developer session) can never count as enabled, and its rows would vanish in every such run. Spec §6.1 gives that entry a notice today. No eval case names a superpowers skill or a table heading (`git grep` over the 15 `plugins/*/evals/*/prompt.md` files: 0 hits), so the evals, which stage no superpowers, lose nothing they grade. Question 6. |
| D40 | The golden test keeps comparing with `34e7713` and gets a fixed list of allowed differences: the rigger-rules list loses the `## Skill triggers` piece; the rigger-core list gains one with the new intro and the merged table; the rigger-windows list loses its table piece; the rigger-wp piece is its sentence under the new heading; the rigger-core list gains `## Slots` (main session); the subagent root-line set gains rigger-core's root, because core now prints `skills.md` to subagents; and the `## Plans: main session` piece is the new `plans-main.md` wording. Every other piece stays byte-equal. The expected `## Slots` piece is written by hand in the test for each case it can meet, never taken from `slots.table`: Windows with rigger-windows (11 rows, `shell-guard` filled, `fails open`), Windows without it (11 rows, `shell-guard` shown `—`), and any other system (10 rows, no `shell-guard` row). A new test compares the merged table's rows, as a list of row lines, with the 13 rows cut from the frozen fixture files. The golden layout gains an enabled stub plugin named `superpowers` (a `plugin.json` only). | The brief's equality requirement and the exit criterion "renders every row of today's three tables". The expected rows come from the frozen fixture, never from `collect_rows`, so a bug in row collection cannot shrink both sides. |
| D41 | `slot_check.py <slot> <file>` prints one line, `slot-check: ok <slot>` (for the reviewer `slot-check: ok reviewer <n> findings`) or `slot-check: refused: <rule id>: <reason>`, and exits 0 or 2. Any exception is a refusal with the rule id `internal`. A reviewer agent ends its reply with the JSON in the last fenced `json` block; `slot_check.py` reads a file that is either a JSON object or a reply holding such a block, and does not accept a Markdown form (open point 3). Unknown keys in the JSON are ignored. Fixtures live in the filling module at `tests/contract/<slot>/`, as `pass*` files and one `fail-<rule id>*` file per rule. `tests/test_module_contracts.py` runs every fixture of every manifest's `tests.contract` through rigger-core's `slot_check.py`. | Spec §10.4 and §12. A module's own tests cannot reach rigger-core by path, so the fixture run sits in the repository-level contract test. |
| D42 | `review_scope.py` in phase 2 lists every path of `git diff --name-status` between two commits and marks it `review`, `skip: deleted` or `skip: binary`. Generated, vendored and lockfile reasons are phase 3 (spec §13). A revision that starts with `-` is refused in Python; both revisions are then resolved to commit ids with `git rev-parse --verify <rev>^{commit}` before any diff, only those ids reach the diff, and the diff runs with `git -c core.quotepath=off --no-pager diff --no-ext-diff --no-textconv --no-color --no-renames --name-status -z`. It never skips a test file. | Spec §10.5 and phase 3's description. `--no-renames` lists a rename as a deleted and an added path, so the new path is always reviewed. Review Focus 4. |
| D43 | `slot_run.py <slot> --session <id> [--base REV] [--head REV] [--repo DIR]` reads the slot only from that session's snapshot and never resolves config itself. It refuses when the snapshot is missing or of another format, when the slot is not a command, when a level-4 command is not in the snapshot's trusted list, when `argv[0]` does not resolve on the stripped `PATH`, and (per Q4) when it resolves to a batch file. It writes the scope list and the output file under a fresh folder in the temp directory, runs the argv with no shell from the repository root with `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS` removed, bounds it at 900 s and 1 MB of console output, and checks the output file with `slot_check.py`. Its last line is `slot-run: ok <slot> <n> findings` or `slot-run: refused: <reason>`; the line before it names the output file. Exit 0 or 2. | Spec §9.4 ("hooks that need a slot read the snapshot") and §10.3. The session id comes on the command line because a Bash tool process may not know it (Q3). The 900 s and 1 MB bounds are the reviewer contract's; `ANTHROPIC_*` stays, since an external reviewer may need its own key. |
| D44 | The snapshot's `slots` value becomes the resolved table (D36 rows plus `trusted`, the level-3 list), and `SNAPSHOT_FORMAT` becomes 2. | Phase 1's D21: a snapshot of another format is read as missing. Review Focus 8. |
| D45 | The report shadow checks shape only: it runs the contract of the slot whose target is the stopped agent's type with no git and no file read besides the report, inside the existing 2.5 s bound. Its row gains `slot` and `contract` (`ok`, `refused:<rule id>`, `none` when the type fills no slot, `unread`). The row still never holds report text. | Spec §13 asks for verdicts "before anything is enforced". Anchors and scope need git and the reviewed commit, which the orchestrator's own `slot_check.py` call has. Q2 decides where the slot table comes from. |
| D46 | `coordinate.md:16` stays as it is. The other ten references are reworded to name a slot or the Skill triggers table (Task 16 gives each text). | Line 16 names `rigger-core:orchestrating-subagents`, which no slot covers: the command and the skill are both fixed core (spec §1). A command file cannot hold a placeholder. |
| D47 | "Every row that loads `slot:process.tdd` loads `slot:testing` before it" becomes a `--check` finding and a doctor WARN row for config rows. `TestingSkillTriggerTests` reads row data, not table text. | Spec §10.4, the `testing` contract. A config row can break the rule without any module changing. The contract's other clause, that the filling module ships the commit-time test guards, becomes checkable only when phase 3 moves them out of rigger-core. |
| D48 | The `shell-guard` contract in phase 2 checks (a) a hook reply that is empty, or one JSON object with `permissionDecision: "deny"` and a non-empty reason, or one with `additionalContext`; (b) the declared failure mode; and (d) that a named rule checker loads and has `RULES` and `rule_guard_problem`. (c), the 50 ms bound, is measured and printed by the contract test and not enforced. The `lessons` contract checks (a), the `SessionStart` line; (b) stays `test_inbox_lint.py`; (c) arrives with the lesson targets of phase 4. | Measured above: today's default filler has no rule id in its deny reason, and its PowerShell handler, the only one without an `if` filter, took 345–399 ms on a command that matches no rule, of which a bare `sh` start is about 85 ms. Questions 2 and 3. |
| D49 | Of the five review ideas, three enter phase 2 because the reviewer contract carries them: the coverage report (`coverage` and the rules on it), hardened `git diff` flags (in `review_scope.py` and one sentence of `reviewer.md`), and the behaviour-change question (one sentence of `reviewer.md` and an optional `behaviour_changes` key that is shape-checked only when present). Strict finding dismissal and per-path rules are listed under phase 3. | The test was whether an idea needs a field or a check in `rigger.reviewer/1`. Question 4 covers the optional key. |
| D50 | `RulesSizeTests` keeps one ceiling for rules-file text, set in Task 16 to the measured sum after the merge, which must not exceed 27,708. `## Slots` is counted on its own: at most 1,200 bytes for the default set. | Spec §3 fixes 27,708 for rules text and §13 expects about 600 bytes of slots. Eleven rows with five columns do not fit 600. Question 5. |
| D51 | The phase 2 spikes are Q1–Q4, recorded in `docs/superpowers/spikes/phase-2/`. S6 and S7 of the spec stay with phases 5 and 3. | Their names must not collide with the spec's. |
| D52 | The three raw readers of phase 1's D17 (`drift_lint.py`, `proposal_check.py`, `batch_model.py`) get one evidence task (Task 20): each is run on raw and on rendered rules text and the differences are listed. A tool whose result differs gets a `--rules-text <file>` input; one whose result is the same gets a test that pins it. | Phase 1 deferred this to phase 2. The merge adds no placeholder line that phase 1 did not already leave, so the work may be evidence only. Question 8. |

## Questions for the coordinator

Each has a default, and the plan continues on it.

1. **CI and `--changed-modules`.** Spec §12 says CI runs `--changed-modules --base origin/main`, the
   contract test and the sweep. `.github/workflows/test.yml` runs the full gate on Linux and the
   full suite on macOS and Windows. **Default:** leave CI alone (D27). The mode is the local gate
   for a module-only change and gets its CI use when phase 3 has module-only pull requests.
2. **The 50 ms bound of the shell-guard contract (§10.4 (c)).** The default filler's PowerShell
   handler (`--pwsh-all`, started for every PowerShell command) measured 345–399 ms here on a
   command that matches no rule. A bare `sh` start is 81–92 ms, so no hook started through `sh` can
   meet 50 ms on Windows. The Bash handlers sit behind `if` filters and add nothing to a command
   that matches none. **Default:** measure and report, do not enforce (D48). The bound is restated for
   `rigger-SHELLWISE` in phase 3, where a native binary can meet it.
3. **"A deny reason names a rule id and a fix" (§10.4 (a)).** rigger-windows' reason is prose with
   an allow marker and no rule id. **Default:** the phase 2 contract asks for a non-empty reason
   only (D48), and the difference is noted in the release notes. Adding the id to a guard that
   phase 3 retires is not planned.
4. **An optional `behaviour_changes` key in `rigger.reviewer/1`.** The approved contract has no
   such key. **Default:** add it as optional (D49): a list of `{file, line, before, after,
   intended}`, checked for shape only when present. An external tool that omits it passes.
5. **The size of `## Slots`.** Spec §13 says about 600 bytes; the default table is estimated at
   900–1,100. **Default:** its own cap of 1,200 bytes, outside the 27,708 ceiling (D50). The
   alternative is a three-column table (slot, target, source) at about 650 bytes, which hides kind
   and model.
6. **A `RIGGER_MODULE_DIRS` entry without a manifest.** Spec §6.1 and the phase 1 code print a
   notice and skip it. **Default:** an entry with a `plugin.json` counts as an enabled plugin, with
   no notice (D39). Without this, the superpowers rows are absent from every stub, eval and golden
   run, and the tests would have to prove a table no real session shows.
7. **Who owns the merged table.** The spec does not say. **Default:** rigger-core (D38), which
   means a setup without rigger-rules starts printing the table.
8. **The raw readers of D17.** The brief's backlog does not list them. **Default:** one evidence
   task of about 1 h (D52). Dropping it leaves phase 1's open risk 6 as it is.

## File map

| Path | Task | Responsibility |
|---|---|---|
| `scripts/run_tests.py`, `tests/test_run_tests.py`, `CONTRIBUTING.md`, `CLAUDE.md` (the Gate bullet) | 1 | the `--changed-modules` mode and its docs |
| `docs/superpowers/spikes/phase-2/Q1.md` … `Q4.md` | 2, 6, 7, 8 | recorded spike results and the decision taken |
| `plugins/rigger-core/tools/assemble/manifest.py`, `config.py` (+ `test_manifest.py`, `test_config.py`) | 3 | the row and entry caps |
| `plugins/rigger-core/tools/assemble/part.sh`, `assemble.py` (`main` only), `tests/test_part_sh.py`, `tests/test_assemble.py` | 4 | lost-part lines, the watchdog, the launcher folder |
| five `plugins/*/hooks/core_check.sh`, five `plugins/*/hooks/hooks.json`, `plugins/rigger-rules/tests/test_core_check.py`, `tests/test_repo.py:548` | 5 | the launcher folder and the `compact` matcher |
| `plugins/rigger-core/tools/assemble/manifest.py` (+ test), `plugins/rigger-windows/rigger-module.json`, `plugins/rigger-lessons/rigger-module.json`, `plugins/rigger-core/rigger-module.json`, `tests/test_repo.py` (`ManifestTests`) | 9 | `fills`, `failure_mode`, `provides`, `tests.contract` |
| `plugins/rigger-core/tools/assemble/slots.py`, `tests/test_slots.py` | 10 | slot table, validation, trust filter, fillers, the `## Slots` rows |
| `plugins/rigger-core/tools/assemble/render.py`, `discover.py` (+ `test_render.py`, `test_discover.py`) | 11 | `{{if}}` regions, the merged table, entry dropping, non-module roots |
| `plugins/rigger-core/tools/review-scope/review_scope.py`, `tests/test_review_scope.py` | 12 | the list of files a review covers |
| `plugins/rigger-core/tools/slot-check/slot_check.py`, `tests/test_slot_check.py`, `plugins/rigger-core/tests/contract/{reviewer,worker,implementer,mapper}/`, `plugins/rigger-windows/tests/contract/shell-guard/`, `plugins/rigger-lessons/tests/contract/lessons/`, `tests/test_module_contracts.py` | 13 | contracts and their fixtures |
| `plugins/rigger-core/tools/slot-run/slot_run.py`, `tests/test_slot_run.py` | 14 | running a command slot |
| `plugins/rigger-core/tools/assemble/assemble.py`, `config.py`, `snapshot.py` (+ their tests), `scripts/run_tests.min` | 15 | wiring: resolution, `## Slots`, notices, snapshot format 2, `--check` |
| rules files of rigger-core, rigger-rules, rigger-windows and rigger-wp, their manifests, `agents/worker.md`, `agents/reviewer.md`, `skills/orchestrating-subagents/SKILL.md`, `commands/coordinate.md`, `templates/implementer-brief.md`, `tests/test_assembly_golden.py`, `tests/golden_baseline.py`, `plugins/rigger-rules/tests/test_plugin_rules.py`, the wording tests named in Task 16 | 16 | the cutover |
| `plugins/rigger-core/tools/report-gate/report_shadow.py`, `INSTALL.md`, `tests/test_report_shadow.py` | 17 | contract verdicts in the shadow rows |
| `plugins/rigger-core/tools/assemble/assemble.py` (`_doctor` only), `plugins/rigger-core/tools/doctor/doctor.sh` (the row filter), `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `commands/doctor.md` | 18 | doctor rows |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` | 19 | stub exit criteria |
| `plugins/rigger-windows/tools/drift-lint/`, `plugins/rigger-lessons/tools/{proposal_check,batch_model}.py` and their tests | 20 | raw readers |
| `CHANGELOG.md`, `plugins/rigger-core/README.md`, `plugins/rigger-rules/README.md`, `plugins/rigger-wp/README.md`, `plugins/rigger-windows/README.md`, `CONTRIBUTING.md` | 21 | release notes, migration, docs |

## Parallel waves

| Wave | Tasks | Runs after | Why they are file-disjoint |
|---|---|---|---|
| A | 1; 2 (spike Q1); 3; 4 (its watchdog step waits for Q1); 6, 7, 8 (spikes Q2–Q4, whose result files are committed on `feature/modular-phase-2` once it is cut) | — | Task 1 writes `scripts/run_tests.py`, its test and two docs. Task 3 writes `manifest.py`, `config.py` and their tests. Task 4 writes `part.sh`, `assemble.py` and their tests. Each spike writes its own result file and scratch folder and starts its own stub on port 0. |
| B | 5 | 2 | the `core_check.sh` copies, five `hooks.json` and two test files; nothing else touches them |
| — | pull requests 1 and 2 merge; `feature/modular-phase-2` is cut from `main` | 1–5 | — |
| C | 9, 10, 11, 12, 13 | the merge above | 9: `manifest.py`, three manifests, `ManifestTests`. 10: two new files. 11: `render.py`, `discover.py` and their tests. 12: two new files. 13: new files, plus `tests/test_module_contracts.py`. Tasks 10 and 13 read Task 9's manifest fields only through the Interfaces block. |
| D | 15 (serial) | 9–13, and Q3 | It is the only task that edits `assemble.py`, `config.py` and `snapshot.py`, and it sets the snapshot format that Tasks 14 and 17 read. |
| E | 14, 16, 17 | 15; 14 also needs Q3 and Q4, 17 needs Q2 | 14 writes two new files under `slot-run/`. 16 writes rules, manifests, agent and skill text and their tests. 17 writes only `report-gate/`. |
| F | 18, 19, 20 | 16 (the tree is red on the table pieces between Tasks 15 and 16) | 18 writes `_doctor` in `assemble.py`, the filter in `doctor.sh` and the doctor tests. 19 writes one test file. 20 writes tools of two other plugins. |
| G | 21, then 22 | all | — |

Parallel: 1, 2, 3, 4, 6, 7 and 8; then 9–13; then 14, 16 and 17; then 18, 19 and 20. Serial: 5
after 2, the merge of pull requests 1 and 2, 15, 21 and 22. No two tasks of one wave write the same
file, `scripts/run_tests.min` and `tests/test_repo.py` included: the first belongs to Task 15, the
second to Task 5 (line 548), then Task 9 (`ManifestTests`), then Task 16 (line 130), which are in
different waves.

---
## Pull request 1: the test mode (wave A)

### Task 1: `run_tests.py --changed-modules`

**Files:**
- Modify: `scripts/run_tests.py`, `CONTRIBUTING.md` (a paragraph after the `--fix-round` one), `CLAUDE.md` (one sentence in the Gate bullet: "A change inside one module's folder may use `python -I scripts/run_tests.py --changed-modules` as its pull-request gate; exit 2 means a shared file changed and the full gate is required.")
- Test: `tests/test_run_tests.py`

**Interfaces:**
- Consumes: `select_tests.changed_files(root, base)` (`scripts/select_tests.py:50`), `discover`, `run_suite`, `clean_env` of `run_tests.py`.
- Produces:
  - `classify(path: str, root: Path) -> Tuple[str, Optional[str]]`: `("shared", None)`, `("docs", None)` or `("module", <name>)`, by the table of D26. `path` is repository-relative with `/`.
  - `dependents(root: Path, names: Set[str]) -> Set[str]`: the modules whose `.claude-plugin/plugin.json` `dependencies` reach a name, followed transitively. An unreadable `plugin.json` counts as depending on everything.
  - `changed_modules(root: Path, changed: List[str]) -> Tuple[List[str], Optional[str]]`: the module names with their dependents, sorted, and the first shared path or None.
  - `run_changed_modules(root: Path, base: str, env: dict, jobs: int, log) -> int`.
  - CLI: `--changed-modules`, with `--base REV` (default `main`). It refuses `--fix-round` and `--control` next to it through `parser.error`.
  - Lines and exit codes:
    - `changed-modules: shared file <path>; run the gate`, exit 2, nothing run;
    - `changed-modules: git failed: <error>; run the gate`, exit 2;
    - `# changed-modules: base <rev>, changed <n>, modules <names, or none>` before the run;
    - then the usual `# files …` line of `run_suite` with a minimum of 1 file;
    - then `# changed-modules: sweep files <n>, findings <m>`;
    - exit 0 only when the suite passed and the sweep printed `# files N, findings 0` with N at least 1, else 1.

- [ ] **Step 1: Write the failing tests.** They build a fixture repository in a temp folder with `git init`: `plugins/a` and `plugins/c` (each with `rigger-module.json`, `.claude-plugin/plugin.json` and one passing `tests/test_x.py`), `plugins/b` whose `plugin.json` depends on `a`, `plugins/zzz` without a manifest, the three always-run files under `tests/` as one-test files, and `scripts/sweep.py` as a stub that prints `# files 3, findings 0`. `main` is called with `root=<fixture>`.
  - `test_classify_table`: one row per line of D26 (`scripts/x.py`, `tests/test_x.py`, `plugins/a/rules/r.md`, `plugins/a/bin/py.sh`, `plugins/a/hooks/core_check.sh`, `plugins/rigger-core/rigger-defaults.json`, `.github/workflows/test.yml`, `.claude-plugin/marketplace.json`, `pyproject.toml`, `plugins/zzz/x.py` give `shared`; `docs/a.md`, `README.md`, `LICENSE` give `docs`; `plugins/a/tools/t/t.py` and `plugins/a/hooks/hooks.json` give `("module", "a")`).
  - `test_shared_file_exits_two_and_runs_nothing`: with `run_file` patched to raise, a changed `scripts/x.py` gives exit 2 and the exact line.
  - `test_module_change_runs_the_module_its_dependents_and_the_always_files`: a change in `plugins/a` runs the test files of `a` and `b` and the three always-run files, and not `c`'s.
  - `test_docs_only_change_runs_the_always_files_and_the_sweep`.
  - `test_deleted_module_and_untracked_shared_file_exit_two` (Review Focus 10): `git rm -r plugins/c`, and separately an untracked `tests/new.py`.
  - `test_moved_file_runs_both_modules` (Review Focus 10): `git mv plugins/a/f.py plugins/c/f.py` runs `a`, `b` and `c`.
  - `test_sweep_with_findings_or_zero_files_fails`: the stub prints `# files 3, findings 2`, then `# files 0, findings 0`; each gives exit 1.
  - `test_git_failure_exits_two`: `--base no-such-rev`.
  - `test_a_failing_module_test_exits_one`.
  - `test_fix_round_next_to_it_is_refused`.
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_run_tests.py -v`. Expected: `FAILED`, each new test on its own assertion or on `unrecognized arguments: --changed-modules`.
- [ ] **Step 3: Implement.** Match paths by their segments, never with `fnmatch` (its `*` crosses `/`). The sweep runs as `[sys.executable, "-I", "-X", "utf8", "scripts/sweep.py", "--no-denylist"]` from `root` with the cleaned environment. Add the mode to the module docstring's usage.
- [ ] **Step 4: Run** the file. Expected: `OK`. Then run the mode on the real repository with only this task's change in the tree: expected `changed-modules: shared file scripts/run_tests.py; run the gate` and exit 2 (`CLAUDE.md` and `CONTRIBUTING.md` are documentation under D26, so the first shared path in sorted order is the script).
- [ ] **Step 5: Red runs** (shared infrastructure, one per branch): remove the `plugins/*/bin/py.sh` row, and `test_classify_table` fails on that row; return 0 for a shared file, and the exit-two test fails; drop `dependents`, and the module test fails on `b`; skip the sweep's count check, and the sweep test fails on `# files 0`; turn a git failure into a full run, and the git test fails.
- [ ] **Step 6: Sweep and commit.** `python -I scripts/sweep.py --no-denylist CONTRIBUTING.md CLAUDE.md`, expected `# files 2, findings 0`. `feat(tests): run_tests.py --changed-modules gates a module-only change`
- [ ] **Step 7: Gate and pull request.** Run the full gate (this task changes `scripts/`), `--verify` it, push `feature/changed-modules` and open the pull request to `main`. Its body gives the classify table, the gate pass line and question 1.

---

## Pull request 2: the phase 1 backlog (waves A–B)

Spike result files have these headings, in order: `## Question`, `## CLI` (version and path, or
"no claude run"), `## Setup` (commands, temp paths as `<temp>`), `## Control` (the known answer and
whether it was observed), `## Observed`, `## Decision`. A spike whose control fails reruns once with
the cause fixed; if it fails twice it reports `BLOCKED`. The shared harness is a copy of
`plugins/rigger-rules/tests/stub_api.py` and of the isolation and debug-log code of
`plugins/rigger-rules/tests/test_reinject.py` in `<temp>/rigger-p2/<id>/harness.py`. No repository
file changes for a spike besides its result file.

### Task 2: Spike Q1 — late, cut-off and repeated handler text

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q1.md`
- Scratch: `<temp>/rigger-p2/Q1/{harness.py,plugin/.claude-plugin/plugin.json,plugin/hooks/hooks.json}`

**Interfaces:**
- Produces: whether a line printed 8 s into a handler arrives (Task 4), and whether a line already printed at startup arrives again after `/compact` (Task 5).

- [ ] **Step 1: Build the probe plugin** `spike-q1` with one `SessionStart` group, matcher `startup|compact`, timeout 10 on each handler. `SPIKE_RUN` is a fresh uuid per session in the child environment.
  - `late`: `sleep 8; printf 'Q1-LATE-%s\n' "$SPIKE_RUN"`.
  - `same`: `printf 'Q1-SAME-%s\n' "$SPIKE_RUN"` (the same text at every firing of one session).
  - `firing`: reads the hook JSON and prints `Q1-FIRING-<source>-<epoch seconds>` (text that differs per firing).
  - `cut`: `printf 'Q1-CUT-%s\n' "$SPIKE_RUN"; sleep 30` (killed by the timeout after it printed).
- [ ] **Step 2: Known-answer control.** One session, `claude -p q1probe-<uuid> --plugin-dir <temp>/rigger-p2/Q1/plugin --debug-file …`. Expected: the request that carries the probe word holds `Q1-FIRING-startup-…` exactly once. If not, fix the harness first.
- [ ] **Step 3: Startup run, 3 sessions.** Record per session whether `Q1-LATE` and `Q1-CUT` are in the probe request, and the debug-log line of each of the four handlers.
- [ ] **Step 4: Compaction run.** One session, then `claude -p "/compact" --resume <session>`, then a third turn with a second probe word, as `test_reinject.py:120-129` does. Record whether the request after the compaction holds `Q1-FIRING-compact-…` (the control for this step), `Q1-SAME` and `Q1-LATE`, and that the debug log has `SessionStart:compact`.
- [ ] **Step 5: Write `Q1.md`.**
  - **`Q1-LATE` arrives in all runs:** the watchdog of D29 stays at 8 s.
  - **`Q1-LATE` is missing:** repeat with `sleep 5` and `sleep 2`, record the longest delay that arrives, and Task 4 uses that value less 1 s. None arrives: `BLOCKED` for Task 4's watchdog step only; its failure lines still ship.
  - **`Q1-SAME` arrives after the compaction:** D31 adds `compact` to the five matchers.
  - **`Q1-SAME` is missing while `Q1-FIRING-compact` is present:** D31 keeps `startup|clear`, and Task 5 changes no `hooks.json`.
  - **`Q1-CUT`:** recorded only. It says whether a part killed at the timeout still delivers what it had printed.
- [ ] **Step 6: Sweep and commit.** `python -I scripts/sweep.py --no-denylist docs/superpowers/spikes/phase-2/Q1.md`, expected `# files 1, findings 0`. `docs(spikes): record Q1 late and repeated hook text`

### Task 3: Caps on trigger rows and `load` entries

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/manifest.py:143-175` (`row_problem`), `plugins/rigger-core/tools/assemble/config.py:119-151` (`_validate`)
- Test: `plugins/rigger-core/tools/assemble/tests/test_manifest.py`, `tests/test_config.py` in the same folder

**Interfaces:**
- Produces: `manifest.LOAD_ENTRY_MAX = 200`, `config.ROWS_MAX = 50`; the reason `load entry over 200 characters`; the notice `rigger: config <where> holds <n> trigger rows; the last <n - 50> ignored (at most 50 per file)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_load_entry_length` (both files): a 200-character entry loads; a 201-character one makes the manifest refuse with `trigger <id>: load entry over 200 characters`, and makes a level-4 row ignored with one notice naming the file and the id while the lower level's row with that id stands.
  - `test_row_cap_per_level` (`test_config.py`): 51 rows at level 4 keep the first 50 in file order and give the one notice with `51` and `1`; 50 rows at level 3 and 50 at level 4 all stand; a `null` row counts toward the 50; 51 rows at level 1 all stand; a level-2 block of 51 is capped and its notice names the module.
- [ ] **Step 2: Run both files.** Expected: `FAILED` on the new tests.
- [ ] **Step 3: Implement.** The cap applies after the per-row shape check, to the rows of that one level, at levels 2 to 4.
- [ ] **Step 4: Run both files.** Expected: `OK`.
- [ ] **Step 5: Red runs** (full: a cloned repository ships these rows): `>` to `>=` in the row cap; count the rows of all levels together; apply the cap at level 1; drop the length check in the config path; drop it in the manifest path. Each fails its own row.
- [ ] **Step 6: Commit.** `fix(rigger-core): cap trigger rows per config file and the length of a load entry`

### Task 4: A lost rules part says so

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/part.sh`, `plugins/rigger-core/tools/assemble/assemble.py:839-858` (`main`, the `--part` branch only)
- Test: `plugins/rigger-core/tools/assemble/tests/test_part_sh.py`, `tests/test_assemble.py` in the same folder

**Interfaces:**
- Consumes: Q1's delay result.
- Produces:
  - `assemble.LOST = "Rigger rules part {index} of {count} not loaded: {reason} (run /rigger-core:doctor)"`, and the same text built in `part.sh` for a part above 1.
  - `assemble.PART_DEADLINE = 8.0` (or Q1's value) and `main(argv=None, stdin=None, stdout=None, deadline=PART_DEADLINE)`.
  - Reasons: `the assembler exited <n>`, `the assembler printed nothing`, `<ErrorType>: <text>`, `timed out after <n> s`.
  - `part.sh` and `assemble.py --part` keep exiting 0 (or 10 for nothing on purpose).

- [ ] **Step 1: Write the failing tests.**
  - `test_part_sh.py` `test_a_failing_part_above_one_prints_its_own_line`: for parts 2 and 3 and for `2 --subagent`, the rows "exit 3", "missing script", "empty file" and "output then exit 1" each give the exact `LOST` line, wrapped as `additionalContext` in subagent mode. The no-Python assertions at `:93-94` stay: parts above 1 print nothing there (D29).
  - `test_part_sh.py` `test_backslash_script_path` (Windows only, skipped elsewhere with the reason): `sh <the script path written with backslashes> 1`, run from another folder, prints the same bytes as the control.
  - `test_assemble.py` `test_a_crash_in_a_part_above_one_prints_its_line`: with `assemble.assemble` patched to raise `ValueError("x")`, `--part 3` writes `LOST` with `ValueError: x` and returns 0.
  - `test_assemble.py` `test_watchdog_prints_the_line_and_exits_zero`: a child Python imports `assemble`, patches `assemble.assemble` to sleep 30 s and calls `main(["--part", "2"], stdin=b"{}", deadline=0.3)`. Expected: stdout is exactly the `LOST` line with `timed out after 0.3 s`, exit status 0, and the child ends in under 5 s.
  - `test_assemble.py` `test_watchdog_stays_quiet_after_a_part_was_written`: `deadline=0.3`, a normal run, then a 1 s sleep in the child: stdout holds the part and no `LOST` line.
- [ ] **Step 2: Run both files.** Expected: `FAILED`; the backslash test fails on the `assembler exited 127` line measured above.
- [ ] **Step 3: Implement.**
  - `part.sh`: `case $0 in *[\\/]*) here=${0%[\\/]*} ;; *) here=. ;; esac` (D30). For an index above 1, a failure prints the `LOST` line, except the no-Python case.
  - `assemble.py`: a daemon `threading.Timer(deadline, …)` started before stdin is read. It takes a lock, checks a `written` flag, writes the line through the same `out` and calls `os._exit(0)`. The normal path sets the flag under the same lock before it writes. The `except` branch builds `LOST` for an index above 1 and keeps `CRASH` for part 1.
- [ ] **Step 4: Run** both files and `plugins/rigger-rules/tests/test_plugin_rules.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (shared infrastructure): remove the index-above-1 branch of `part.sh` (the first test fails); restore `here=${0%/*}` (the backslash test fails); make the timer return without exiting (the watchdog test fails on its 5 s bound); drop the `written` flag (the quiet test fails on a doubled output); keep the old `except` branch (the crash test fails).
- [ ] **Step 6: Commit.** `fix(rigger-core): a rules part that fails or runs out of time says so`. No assertion was removed, so the commit carries no `Test-change:` trailer; if Step 1 had to change an existing assertion, add the trailer with the reason.

### Task 5: `core_check.sh`: backslash script path, and `compact` (wave B)

**Files:**
- Modify: the five `plugins/{rigger-rules,rigger-lessons,rigger-talk,rigger-wp,rigger-windows}/hooks/core_check.sh` (identical bytes); the same five `hooks/hooks.json` only if Q1 says so; `tests/test_repo.py:548`
- Test: `plugins/rigger-rules/tests/test_core_check.py`

**Interfaces:**
- Consumes: Q1's decision on the repeated line.
- Produces: the same line as today from a script path written with `\`; the matcher `startup|clear|compact` (D31) or unchanged wiring.

- [ ] **Step 1: Write the failing test** `test_backslash_script_path` (Windows only): the old-core layout of `test_old_core_prints_one_visible_line`, the script path written with backslashes, run from another folder. Expected: the exact line. If Q1 chose `compact`, also change `test_wiring` (`:266`) and `tests/test_repo.py:548` to `startup|clear|compact`.
- [ ] **Step 2: Run** `test_core_check.py` and `tests/test_repo.py`. Expected: `FAILED` on the new row (no output, as measured above) and, with `compact`, on the two wiring assertions.
- [ ] **Step 3: Implement.** The `case` of Task 4, Step 3, in all five copies. With `compact`: the five matchers.
- [ ] **Step 4: Run** `test_core_check.py`, `tests/test_repo.py`, `tests/test_shellcheck.py`, and `wsl … test_core_check.py` for dash. Expected: `OK`; `test_core_check_copies_identical` passes. With `compact`, time five starts on Windows and under WSL and paste both in the report.
- [ ] **Step 5: Red runs.** Restore the old `here` line in one copy (the identity test fails) and in all five (the backslash row fails). With `compact`: drop it from one `hooks.json` (the wiring test fails for that plugin).
- [ ] **Step 6: Commit.** `fix: core_check finds its folder from a backslash path` and, with `compact`, `and runs after a compaction`.
- [ ] **Step 7: Gate and pull request.** Full gate, `--verify`, push `fix/phase-1-backlog`, open the pull request. Its body gives Q1's decisions, the five-start timing, the caps, and the changed lost-part behaviour.

---

## Pull request 3: spikes (wave A)

### Task 6: Spike Q2 — what `SubagentStop` carries

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q2.md`
- Scratch: `<temp>/rigger-p2/Q2/{harness.py,q2.py,plugin/…}`

**Interfaces:**
- Produces: for Task 17, where the report text and the slot table come from; for Task 13, the form of `agent_type` for a plugin's agent.

- [ ] **Step 1: Build the probe.** Plugin `spike-q2` has an agent `agents/echo.md` and two handlers, `SubagentStart` and `SubagentStop`, each `cat > "$Q2_OUT/<event>-$$.json"`. The stub answers the request carrying `q2probe-<uuid>` with an `Agent` tool_use whose `subagent_type` is the run's type and whose prompt is `q2sub-<uuid>` (the pattern of spike S5). It answers the request carrying `q2sub-<uuid>` with a text of the run's size: a first line `STATUS: DONE`, filler lines, and a last fenced `json` block holding `{"marker": "Q2-END-<run>"}`.
- [ ] **Step 2: Known-answer control.** One run with `general-purpose` and a 2 KB reply. Expected: one `SubagentStop-*.json` file, and a debug-log line `Hooks: SubagentStop`.
- [ ] **Step 3: Runs.** `general-purpose` at 2 KB, 40 KB and 300 KB; `spike-q2:echo` at 2 KB. Record for each: the stdin keys; whether `session_id` equals the parent's (the `-p` result's); `agent_type` as written; the length of `last_assistant_message` and whether it ends with the marker block; whether `agent_transcript_path` exists and its last assistant text ends with the marker block; and how long the harness takes to find and parse the last fenced block of the 300 KB text (the shadow's own bound is 2.5 s).
- [ ] **Step 4: Write `Q2.md`.**
  - **`session_id` is the parent's:** Task 17 reads the slot table from that session's snapshot. **It is not:** Task 17 reads level-1 defaults only and its row says `table: defaults`.
  - **`last_assistant_message` ends with the marker at every size:** Task 17 checks the event text. **It is cut at some size:** Task 17 uses the transcript tail above that size, and a report longer than the 256 KB tail window gets `unread`.
  - **`agent_type` for the plugin agent is `spike-q2:echo`:** a slot is found by comparing `agent_type` with the slot target. **It is another form:** record the form and the mapping, and Task 17 applies it.
- [ ] **Step 5: Sweep and commit.** `docs(spikes): record Q2 SubagentStop input`

### Task 7: Spike Q3 — what a model-started shell command can see, and whether a stub can script one

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q3.md`
- Scratch: `<temp>/rigger-p2/Q3/{harness.py,q3.py,dump.sh,echo_args.py,plugin/…}`

**Interfaces:**
- Produces: for Task 15, the exact command line under `## Slots`; for Task 19, how the stub makes a session run `slot_run.py`.

- [ ] **Step 1: Build the probe.** Plugin `spike-q3` has one `SessionStart` handler that reads the hook JSON and prints `Q3-SESSION-<session_id>`. The stub answers the request carrying `q3probe-<uuid>` with a tool_use of the shell tool it finds in the request's `tools` list (`Bash`; record whether `PowerShell` is listed too). Its command is `sh "<temp>/rigger-p2/Q3/dump.sh"`. `dump.sh` writes the working directory, the names of all environment variables, and the values of those starting `CLAUDE_` or `RIGGER_` to a file, then prints `Q3-RAN-<run>`.
- [ ] **Step 2: Known-answer control.** Run with `--allowedTools Bash`. Expected: a later request holds a `tool_result` with `Q3-RAN-<run>`. If the tool call is refused, repeat with `--permission-mode acceptEdits`, then with a `permissions.allow` entry for `Bash` in the scratch config's `settings.json`, and record which form works.
- [ ] **Step 3: Second run.** The stub reads `Q3-SESSION-<id>` out of the probe request and answers with the command `sh "<core root>/bin/py.sh" "<temp>/rigger-p2/Q3/echo_args.py" reviewer --session <id> --base HEAD`, the form D36 plans. `echo_args.py` writes its argv and `CLAUDE_CONFIG_DIR` to a file.
- [ ] **Step 4: Write `Q3.md`.** Record the environment names, the working directory, and whether any variable holds the session id.
  - **The `tool_result` arrived (any form of Step 2):** Task 19 uses that form.
  - **No form works:** Task 19's second test starts `slot_run.py` itself with the stub session's id, and the report says the model-side call was not proved.
  - **`CLAUDE_CONFIG_DIR` reaches the shell process:** the `## Slots` line is `sh "<core root>/bin/py.sh" "<core root>/tools/slot-run/slot_run.py" <slot> --session <id> --base <rev>`.
  - **It does not:** the line also carries `--config-dir "<config dir>"`, and `slot_run.py` takes that option.
  - **The argv of Step 3 arrived changed** (quotes or a path rewritten by the shell tool): record the form that arrives whole on Windows and on Linux, and Task 15 prints that form.
- [ ] **Step 5: Sweep and commit.** `docs(spikes): record Q3 shell tool environment`

### Task 8: Spike Q4 — starting an external command with no shell

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q4.md`
- Scratch: `<temp>/rigger-p2/Q4/q4.py` and its probe programs. No `claude` run.

**Interfaces:**
- Produces: for Task 14, which program files `slot_run.py` may start, and that its tree kill and output bound work.

- [ ] **Step 1: Write `q4.py`.** Each case starts a child with `subprocess.Popen(argv, shell=False, env=<stripped>)`, where the child is a Python script that writes its argv as JSON to a file. The stripped environment is `os.environ` without `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`. Run it on Windows and under WSL; macOS is recorded as not measured.
- [ ] **Step 2: Known-answer control.** `[sys.executable, child.py, "a b", "x&y", '"q"', "%PATH%", "^z"]`: the child's file holds those five strings unchanged.
- [ ] **Step 3: Cases.**
  1. A bare program name found with `shutil.which(name, path=env["PATH"])`: an `.exe`, a `.cmd` shim of the form package managers write, a `.bat`, and on Linux a script with a `#!` line.
  2. The five hostile arguments of the control through the `.cmd` shim and the `.bat`.
  3. A child that starts a grandchild sleeping 60 s, stopped with the tree kill of `scripts/run_tests.py:258-274`; then check that the grandchild's pid is gone.
  4. A child that writes 50 MB to stdout, with stdout sent to a temp file, stopped once the file passes 1 MB.
  5. A revision argument `--output=x` passed as one argv element after `--base`.
- [ ] **Step 4: Write `Q4.md`.**
  - **Any hostile argument arrives changed through a batch file, or a batch file does not start without a shell:** `slot_run.py` refuses a program that resolves to `.cmd` or `.bat` with `argv[0] resolves to a batch file (<name>); name its interpreter and script instead` (D43).
  - **All five arrive unchanged on both:** batch files are allowed, and Task 14 drops that refusal and its test.
  - **The grandchild survives, or the output bound does not stop the child:** `BLOCKED`, with the output.
- [ ] **Step 5: Sweep and commit.** `docs(spikes): record Q4 command start without a shell`

---
## Pull request 3: building blocks (wave C)

Wave C tasks merge into `feature/modular-phase-2` in task order, so Task 13 lands after Task 9.

### Task 9: Manifest fields for fillers

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/manifest.py`, `plugins/rigger-windows/rigger-module.json`, `plugins/rigger-lessons/rigger-module.json`, `plugins/rigger-core/rigger-module.json`, `tests/test_repo.py` (`ManifestTests`)
- Test: `plugins/rigger-core/tools/assemble/tests/test_manifest.py`

**Interfaces:**
- Produces:
  - `manifest.SLOT_PATHS = ("reviewer", "worker", "implementer", "mapper", "testing", "shell-guard", "lessons", "process.brainstorm", "process.plans", "process.tdd", "process.debug")`
  - `Module.failure_mode: Optional[str]` (`"open"`, `"closed"` or None when the key is absent).
  - `Module.fills` is None or a member of `SLOT_PATHS`. `Module.provides` keys are members of `SLOT_PATHS` and its values objects. `provides["shell-guard"]["rule_checker"]`, when present, is a path inside the module that names a file.
  - `Module.tests["contract"]`, when present, is a list of `SLOT_PATHS` members.
  - Manifests: rigger-windows gets `"fills": "shell-guard"`, `"failure_mode": "open"`, `"provides": {"shell-guard": {"kind": "hook", "target": "rigger-windows", "rule_checker": "tools/shell-guard/lessons_guard.py"}}` and `"contract": ["shell-guard"]`. rigger-lessons gets `"fills": "lessons"` and `"contract": ["lessons"]`. rigger-core gets `"contract": ["reviewer", "worker", "implementer", "mapper"]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_fills_must_name_a_slot`: `"fills": "reviewers"` gives `fills reviewers is not a slot`; `null` and `"process.tdd"` load.
  - `test_failure_mode_values`: `"open"` and `"closed"` load, `"maybe"` and `1` give `failure_mode must be open or closed`, an absent key gives None.
  - `test_rule_checker_is_a_file_inside_the_module`: rows `../x.py`, `/abs/x.py`, `tools\\x.py`, a missing file and a number each refuse with a reason that names `rule_checker`.
  - `test_provides_keys_and_tests_contract_name_slots`.
  - `ManifestTests` in `tests/test_repo.py`: the three manifests hold the values above and the other four keep `fills: null`.
- [ ] **Step 2: Run** `test_manifest.py` and `tests/test_repo.py`. Expected: `FAILED` on the new tests.
- [ ] **Step 3: Implement** the checks with the existing `_inside` and `ManifestError`, then edit the three manifests.
- [ ] **Step 4: Run** both files and `tests/test_module_contracts.py`. Expected: `OK`.
- [ ] **Step 5: Red runs.** Accept any string as `fills`; accept any `failure_mode`; skip `_inside` for `rule_checker` (the `../x.py` row fails); skip the file check.
- [ ] **Step 6: Commit.** `feat(rigger-core): manifests declare the slot they fill and its failure mode`

### Task 10: Slot resolution, validation and the trust filter

**Files:**
- Create: `plugins/rigger-core/tools/assemble/slots.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_slots.py`

**Interfaces:**
- Consumes: `Module` with `fills`, `failure_mode`, `root` (Task 9); `manifest.SLOT_PATHS`, `manifest.UNSAFE`, `manifest.check_requires`; `config.merge` and `config._shown`.
- Produces:
  - `KINDS: Dict[str, Tuple[str, ...]]`: `reviewer` is `("agent", "command")`; `worker`, `implementer` and `mapper` are `("agent",)`; `testing` and the four `process.*` paths are `("skill",)`; `shell-guard` and `lessons` are `("hook",)`.
  - `PLACEHOLDERS = ("base", "head", "out", "scope")`
  - `@dataclass class Row: path: str; kind: Optional[str]; target: object; model: Optional[str]; source: str; fillers: Tuple[str, ...]; failure_mode: Optional[str]; filled: bool`. `target` is a string, an argv list or None. `source` is `default`, `company:<module>`, `personal` or `project`.
  - `flatten(slots: dict) -> Dict[str, dict]`: keys are slot paths; a value under `process` that is not an object, and any unknown path, is returned under its path so the caller can report it.
  - `entry_problem(path: str, entry: dict, modules: List[Module], enabled: FrozenSet[str]) -> Optional[str]`, by the rule table below.
  - `trust_filter(lower: Dict[str, dict], project: Dict[str, object], trusted: FrozenSet[str], config_file: str) -> Tuple[Dict[str, object], List[str]]`: `lower` is the flattened merge of levels 1–3, `project` the flattened level-4 values (`None` for a removal). It returns the level-4 values that stay and the notices (D33). The notice is `rigger: .claude/rigger.json may not set a command for slot <path>; add it to trust.project_commands in <config_file> to allow it`.
  - `resolve_slots(levels: List[Tuple[str, dict]], modules: List[Module], enabled: FrozenSet[str], os_name: str, config_file: str) -> Tuple[List[Row], FrozenSet[str], List[str]]`. `levels` holds `(source, that level's cleaned config)` lowest first, with sources `L1:<file>`, `L2:<module>`, `L3:<file>`, `L4:<file>`. It returns the rows in `SLOT_PATHS` order without the paths whose `requires` is unmet, the trusted list, and the notices (D34, D35).
  - `slot_targets(rows: List[Row]) -> dict`: a nested dict shaped like level-1 `slots`, holding filled agent and skill slots with their `target`, and filled hook slots as `{"kind": "hook"}`. `render` and `_requires_met` read it.
  - `table(rows: List[Row], command_line: Optional[str]) -> bytes`: the `## Slots` section of D36, heading included.
  - `as_snapshot(rows: List[Row], trusted: FrozenSet[str]) -> dict` and `from_snapshot(data: object) -> Optional[Tuple[List[Row], FrozenSet[str]]]` (None on any wrong shape).

  Rule table of `entry_problem` (each row is one test row and one red run):

  | Field | Rule | Reason text |
  |---|---|---|
  | keys | only `kind`, `target`, `model`, `requires` | `unknown key <key>` |
  | `kind` | a string in `KINDS[path]` | `kind <kind> not allowed for <path>` |
  | `target`, agent or skill | a string of at most 200 characters matching `^[A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*$` | `target must be <plugin>:<name>` |
  | same | its plugin is in `enabled` | `plugin <plugin> is not enabled` |
  | same | when the plugin is a module, `agents/<name>.md` or `skills/<name>/SKILL.md` exists under its root | `<plugin> has no agent <name>` or `… no skill <name>` |
  | `target`, command | a list of 1 to 32 strings | `target must be a list of 1 to 32 strings` |
  | same | each element has 1 to 500 characters and none of category `manifest.UNSAFE` | `argument <n> is empty, too long or holds a control character` |
  | same | every `{word}` in an element is one of `PLACEHOLDERS` | `unknown placeholder {<word>}` |
  | same | some element holds `{out}` | `no {out} placeholder` |
  | same | element 0 holds no placeholder | `the program name holds a placeholder` |
  | `target`, hook | a string matching `^[A-Za-z0-9][A-Za-z0-9._-]*$` | `target must be a plugin name` |
  | `model` | absent, or a string of 1 to 60 characters with no `|`, backtick or `UNSAFE` character | `model must be a short one-line string` |
  | `requires` | passes `manifest.check_requires` | its message |

- [ ] **Step 1: Write the failing tests.** Level 1 in every test is a copy of `rigger-defaults.json`'s `slots`; modules are built in a temp folder with real `agents/` and `skills/` files.
  - `test_defaults_give_eleven_rows_on_windows_and_ten_elsewhere` (`shell-guard` requires `os:windows`).
  - `test_entry_rules`: one row per line of the rule table, each expecting its reason.
  - `test_fallback_walks_down_with_one_notice` (D34): level 4 sets `worker.target` to a missing agent and level 3 sets it to a real one. The row comes from `personal`, and the one notice names the level-4 file and ends `; using personal`.
  - `test_level_one_default_of_a_plugin_that_is_not_enabled_is_empty_and_quiet`: without `superpowers` in `enabled`, the four `process.*` rows have `filled False` and there is no notice.
  - `test_l4_command_without_trust_is_dropped_with_the_line`: the exact notice, and the row stays `rigger-core:reviewer` from `default`.
  - `test_l4_command_with_trust_applies`: `trust.project_commands: ["reviewer"]` at level 3 gives kind `command`, source `project`.
  - `test_l4_target_alone_cannot_rewrite_a_command` (Review Focus 1): level 3 sets the command, level 4 sets only `target`; the level-3 argv stays and the notice prints.
  - `test_l4_model_alone_and_null_are_allowed`: no notice for either; the `null` leaves `reviewer` empty.
  - `test_null_for_all_slots_or_for_a_group_is_ignored_with_a_notice` (D34): level 4 with `"slots": null`, and level 4 with `"slots": {"process": null}`. Every slot stays as the lower levels set it, and each case gives the one notice naming the file and the key.
  - `test_dropped_l4_value_takes_its_model_with_it`.
  - `test_trust_names_the_slot_path`: trusting `reviewer` does not trust `process.tdd`, and `trust.project_commands` holding a number or an unknown path is ignored with one notice.
  - `test_l2_command_is_allowed`.
  - `test_hook_slot_is_filled_by_fills`: a module with `fills: "lessons"` gives fillers `("rigger-lessons",)` and `filled True`; no such module gives `filled False`.
  - `test_two_fillers_give_one_line_naming_both` (Review Focus 6): the exact line of D35, and fillers in module order.
  - `test_expected_filler_missing_above_level_one_gives_a_notice`, and none for the level-1 default.
  - `test_table_bytes`: a fixed list of five rows (an agent, a skill, an empty slot, a hook with two fillers and `fails open`, a command) gives bytes written by hand in the test from D36.
  - `test_command_line_only_with_a_command_slot`.
  - `test_cells_cannot_break_the_table`: a program name holding `|`, a backtick or a line break shows with `?` in its place, and the table keeps five cells per row.
  - `test_slot_targets_holds_filled_slots_only`.
  - `test_snapshot_round_trip_and_wrong_shapes`: `from_snapshot` gives None for a list, a number, a row without `path` and a `trusted` that is a string.
- [ ] **Step 2: Run** `test_slots.py`. Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.** A command cell shows the program name followed by ` …`, never the whole argv. `os_name` is passed in, so no test depends on the machine it runs on.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs** (full). The trust filter, each branch once: ignore a level-4 `kind`; ignore a level-4 `target`; treat every path as trusted; compare the trusted list with the first path segment only; keep `model` of a dropped value; apply the filter to level 2. Validation: remove each rule of the table once and see its row fail. Fallback: start the walk at level 1. Quiet rule: print a notice for a level-1 default. Null rule: let `"slots": null` through, then `"process": null`. Fillers: report only the first of two.
- [ ] **Step 6: Commit.** `feat(rigger-core): resolve, validate and trust-filter slots`

### Task 11: `{{if}}` regions, the merged table and non-module roots

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/render.py`, `plugins/rigger-core/tools/assemble/discover.py:298-330`
- Test: `tests/test_render.py` and `tests/test_discover.py` in that tool's folder

**Interfaces:**
- Produces:
  - `RenderContext` gains three fields with defaults, so phase 1 callers keep working: `rows: Optional[List[TriggerRow]] = None`, `enabled: Optional[FrozenSet[str]] = None`, `met: Optional[Callable[[str], bool]] = None`.
  - A fourth field, `table_seen: Optional[List[str]] = None`. With `rows` set, the first `{{triggers:<id>}}` renders the one table from `rows` and appends the context's `module` to `table_seen` when that is a list. Every later one, in the same `render` call or in a later call that shares the list, renders nothing and is returned in the unknown list as `second trigger table`.
  - `visible(rows: List[TriggerRow], slots: dict, enabled: Optional[FrozenSet[str]]) -> List[Tuple[TriggerRow, List[str]]]`: each row with its resolved entries after D39; `enabled` None drops nothing.
  - `table(rows, slots, enabled=None) -> bytes` uses `visible`.
  - `IF = re.compile(r"\{\{if ((?:[a-z]+:[^{}\s]+)(?: [a-z]+:[^{}\s]+)*)\}\}")` and the literal `{{end}}`. A region is kept when `met` is None or returns True for every item, with its two markers removed either way. A marker without its partner, or an `{{if}}` inside a region, prints as written and is returned in the unknown list.
  - `discover`: a `RIGGER_MODULE_DIRS` entry without `rigger-module.json` whose `.claude-plugin/plugin.json` is an object with a string `name` adds that name to `Discovery.enabled`, with no notice and no module (D39).

- [ ] **Step 1: Write the failing tests.**
  - `test_if_region_kept_when_met_and_removed_when_not`: `b"a{{if slot:process.brainstorm slot:process.plans}} (load\n  {{slot:process.brainstorm}}, then {{slot:process.plans}}){{end}}, b\n"` renders ``b"a (load\n  `x:b`, then `x:p`), b\n"`` when both are met and `b"a, b\n"` when one is not.
  - `test_if_markers_alone_or_nested_print_as_written_and_are_reported`.
  - `test_if_backtracking_probe`: `"{{if " * 40 + "slot:a " * 40` renders in under 0.5 s.
  - `test_one_table_holds_the_rows_of_every_section`: two module rows with different `section` values and one config row give one table in that order.
  - `test_second_table_placeholder_renders_nothing_and_is_reported`.
  - `test_entry_of_a_plugin_that_is_not_enabled_is_dropped`: a row loading `x:a` and `sup:b` with `enabled={"x"}` shows `` `x:a` `` alone; a row loading only `sup:b` is gone; a `slot:` entry of a slot missing from `slots` is dropped.
  - `test_callers_without_rows_render_as_in_phase_one`: the three phase 1 table tests still pass unchanged.
  - `test_dirs_entry_with_plugin_json_only_counts_as_enabled` (`test_discover.py`): the name is in `enabled`, `modules` does not hold it, and there is no notice. Rows: no `plugin.json` (today's notice), one that is a list, one whose `name` is a number (today's notice for both).
- [ ] **Step 2: Run both files.** Expected: `FAILED` on the new tests.
- [ ] **Step 3: Implement.** Regions are cut on the decoded text before placeholders are replaced, so text removed with a region is never scanned.
- [ ] **Step 4: Run** both files, `test_assemble.py` and `plugins/rigger-core/tools/harness_start/tests/test_harness_start.py`. Expected: `OK` with no change to the last two.
- [ ] **Step 5: Red runs.** Keep a region whose item is unmet; require any item where all are needed; render the table at every placeholder; keep the entries of a plugin that is not enabled; keep an empty row; add a dirs entry without `plugin.json` to `enabled`.
- [ ] **Step 6: Commit.** `feat(rigger-core): conditional rules regions and one trigger table`

### Task 12: `review_scope.py`

**Files:**
- Create: `plugins/rigger-core/tools/review-scope/review_scope.py`
- Test: `plugins/rigger-core/tools/review-scope/tests/test_review_scope.py`

**Interfaces:**
- Produces:
  - `class ScopeError(Exception)`
  - `resolve_commit(repo: Path, rev: str) -> str`: the 40-character commit id. It refuses, before git runs, a `rev` that is empty, starts with `-`, or holds whitespace or a control character; then `git rev-parse --verify <rev>^{commit}`. Only the ids it returns are passed to `git diff`.
  - `scope(repo: Path, base: str, head: str) -> List[Tuple[str, str]]`: `(path, verdict)` sorted by path, the verdict being `review`, `skip: deleted` or `skip: binary` (D42).
  - `GIT_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames")`, used for the `--name-status -z` call and for the `--numstat -z` call that finds binary files. Git runs with `-c core.quotepath=off --no-pager`, a 60 s bound, and an environment without `GIT_EXTERNAL_DIFF` and `GIT_DIFF_OPTS`.
  - CLI: `review_scope.py --base REV [--head REV] [--repo DIR]` (`--head` defaults to `HEAD`, `--repo` to the working directory). One line per path, `<verdict><TAB><path>`, then `# review-scope: files <n>, review <r>, skipped <s>`; exit 0. A refusal prints `review-scope: refused: <reason>` and exits 2: a revision that does not resolve, a folder that is not a repository, a git failure, or a path that holds a tab or a line break.

- [ ] **Step 1: Write the failing tests.** The fixture is a temp repository with two commits. The second one edits `a.py`, deletes `b.py`, adds `c.bin` (bytes with a NUL), renames `d.py` to `e.py`, adds `zażółć ą.py` and adds `tests/test_x.py`.
  - `test_lists_every_changed_path_with_its_verdict`: the expected list is written by hand: `a.py` review, `b.py` skip: deleted, `c.bin` skip: binary, `d.py` skip: deleted, `e.py` review, `tests/test_x.py` review, `zażółć ą.py` review.
  - `test_a_test_file_is_never_skipped`.
  - `test_revision_that_looks_like_an_option_is_refused` (Review Focus 4): rows `--output=<tmp>/x`, `-p`, the empty string, `a b`, `no-such-rev` and a tag that points at a blob. Each gives exit 2 with a reason, and `<tmp>/x` does not exist afterwards. For the first four rows `subprocess.run` is patched to fail the test if git is started.
  - `test_drivers_of_the_repository_do_not_run`: the fixture's git config sets `diff.external` and a `textconv` driver to a script that writes a marker file. Control first: a plain `git diff <base> <head>` in that repository writes the marker. Then `scope()` gives the true list and writes no marker.
  - `test_same_commit_gives_zero_files`, `test_not_a_repository_refuses`, `test_cli_lines_and_summary`.
- [ ] **Step 2: Run.** Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: `OK`, on Windows and under WSL.
- [ ] **Step 5: Red runs** (full): drop the leading-`-` refusal (measured above, `git rev-parse --verify` refuses `--output=x` by itself, so this mutant may stay green: the test then also asserts that git was not started for that row, which the mutant fails); pass the given revision to `git diff` in place of the resolved id; drop `^{commit}` (the blob tag row fails); drop `--no-renames` (the rename test fails on `d.py`); drop the numstat pass; drop `--no-ext-diff`, then `--no-textconv`. If one of the last two stays green because `--name-status` and `--numstat` start no driver, report it as a surviving mutant with the control's output and keep the flag: spec §13 fixes it.
- [ ] **Step 6: Commit.** `feat(rigger-core): review_scope lists what a review must cover`

### Task 13: `slot_check.py` and the contract fixtures

**Files:**
- Create: `plugins/rigger-core/tools/slot-check/slot_check.py`, `plugins/rigger-core/tools/slot-check/tests/test_slot_check.py`, `plugins/rigger-core/tests/contract/{reviewer,worker,implementer,mapper}/`, `plugins/rigger-windows/tests/contract/shell-guard/`, `plugins/rigger-lessons/tests/contract/lessons/`
- Modify: `tests/test_module_contracts.py`

**Interfaces:**
- Consumes: `review_scope.scope` and `resolve_commit` (Task 12), imported through a `sys.path` entry built from `__file__`; `Module.tests["contract"]` (Task 9).
- Produces:
  - `@dataclass class Verdict: ok: bool; rule: str; reason: str; findings: Optional[int]`
  - `check(slot: str, text: str, repo: Optional[Path] = None, base: Optional[str] = None, head: Optional[str] = None, shape_only: bool = False) -> Verdict`. It never raises: any exception is `Verdict(False, "internal", <type and text>, None)`.
  - `RULES: Dict[str, Tuple[str, ...]]`, the rule ids per slot in checking order, and `GIT_RULES`, the ids that `shape_only` skips.
  - `reviewer_json(text: str) -> object`: the parsed object of a file that is JSON, else of the last fenced `json` block. A text over 1,000,000 characters is refused.
  - CLI: `slot_check.py <slot> <file> [--repo DIR] [--base REV] [--head REV] [--shape-only]`, with the lines and exit codes of D41. Without `--shape-only` the reviewer needs `--base` and `--head`.

  Rule ids:

  | Slot | Rule ids, in order |
  |---|---|
  | `reviewer` | `json` (no object found), `contract` (not `rigger.reviewer/1`), `status`, `finding-shape` (a finding without `id`, `file`, `line`, `severity`, `quote`, `title`; a repeated id; a line that is not a positive integer), `severity`, `path` (a `file` that is absolute, holds `..`, `\` or `:`, or is empty), `behaviour-shape` (only when the key is present), `coverage-sum`, `skip-reason`, `shortcuts`, then the git rules `base-head` (the JSON's `base` and `head` resolve to other commits than the ones given), `coverage-scope`, `anchor` |
  | `worker` | `status-line`, `summary` |
  | `implementer` | `status-line`, `fields` (`FILES`, `ROOT CAUSE`, `GATE`, `RED RUNS` missing or out of order), `blocked-fields` (`QUESTION`, `CALL`, `OUTPUT`, `ALTERNATIVE` missing with BLOCKED or NEEDS_CONTEXT) |
  | `mapper` | `line-shape`, `words` (over 12 words of text), `not-found-alone`, then the git rule `path-exists` |
  | `shell-guard` | `reply-shape`, `deny-reason` (D48) |
  | `lessons` | `inbox-line`: the first line matches `^Lessons inbox: .+: (\d+ entries|count unknown \(.+\))$` |

  The anchor rule reads the file at `head` with `git cat-file`, never from the working tree, decodes it as UTF-8 with replacement, and looks for the quote with every run of whitespace turned into one space, in lines `line - 3` to `end_line + 3` (`end_line` defaults to `line`). `coverage-sum` needs `in_scope` to equal `reviewed` plus the skipped files, with no file in both. `coverage-scope` needs `in_scope` to equal the paths of `review_scope.scope`.

  Fixtures: `pass*` files and one `fail-<rule id>*` file per rule. For the reviewer, `repo/base/**` and `repo/head/**` hold two states of a small tree; the test builds a two-commit repository from them and replaces the tokens `{base}` and `{head}` in the JSON fixtures with the real ids. `pass-reply.md` holds the JSON as the last fenced block of a Markdown report.

- [ ] **Step 1: Write the fixtures and the failing tests.**
  - `tests/test_module_contracts.py` `test_every_contract_fixture`: for each manifest and each slot in its `tests.contract`, the folder exists and holds at least one `pass*` file. Every `pass*` file gives `ok`. Every `fail-<rule>` file is refused with exactly that rule id. Every id in `RULES[slot]` has a `fail-` file. The test fails when it read 0 fixtures.
  - The same file, `test_default_fillers_meet_their_contracts`: rigger-windows' PowerShell handler, the `--pwsh-all` command read from its `hooks/hooks.json` (the one handler with no `if` filter), run through `sh` on a `PowerShell` event whose command is `Get-ChildItem`, gives a reply that passes `shell-guard`; `lessons_start.py` run with a scratch inbox gives a text that passes `lessons`; the rule checker named in rigger-windows' manifest loads and has `RULES` and `rule_guard_problem`. It prints `# shell-guard latency median <ms> ms over 5 runs` and asserts no bound (D48).
  - `test_slot_check.py`:
    - `test_finding_path_outside_the_repository_is_refused_unread` (Review Focus 2): rows `../outside.txt`, an absolute POSIX path, a path with a drive letter and one with a backslash; each gives `path`, with `subprocess.run` patched to fail the test if git is called for that finding.
    - `test_anchor_normalises_whitespace_and_line_endings` (Review Focus 2): a CRLF file, a tab-indented line quoted with spaces, a quote spanning two lines; and a file with a byte that is not UTF-8 gives `anchor`, not `internal`.
    - `test_anchor_window`: a quote 3 lines off passes and 4 lines off is refused.
    - `test_the_last_json_block_counts`, `test_no_block_and_a_cut_block_are_refused_as_json`, `test_text_over_the_size_cap_is_refused`.
    - `test_unknown_keys_are_ignored`, `test_behaviour_changes_is_optional_and_shape_checked`.
    - `test_shape_only_skips_the_git_rules`.
    - `test_an_exception_is_a_refusal`: `check` with a patched rule that raises gives `internal`, and the CLI exits 2.
    - `test_fence_and_whitespace_backtracking_probe`: 40 repeats of a fence opener, and of a space-tab pair, each under 0.5 s.
    - `test_cli_lines_and_exit_codes`.
- [ ] **Step 2: Run** both files. Expected: `FAILED`.
- [ ] **Step 3: Implement.** Words are split and walked in code; the only regexes on reply text are the fence opener and the inbox line.
- [ ] **Step 4: Run** both files on Windows and under WSL. Expected: `OK`, and the contract test's last lines name the fixture count and the latency.
- [ ] **Step 5: Red runs** (full): remove each rule's check once; its `fail-<rule>` fixture then passes and `test_every_contract_fixture` fails on that file. Also: read the anchor from the working tree (a test that edits the working copy after the commit fails); make an exception propagate; let the CLI exit 0 on a refusal; delete one `fail-` file (the "every rule has a fixture" assertion fails).
- [ ] **Step 6: Sweep and commit.** Sweep the three fixture folders, expected `findings 0`. `feat(rigger-core): slot_check checks a reply against its slot's contract`

---

## Pull request 3: running and wiring (waves D–E)

Task 15 runs first, alone (wave D). Task 14 follows it in wave E: its tests write a format-2
snapshot and expect a format-1 file to be refused, and Task 15 sets that format.

### Task 14: `slot_run.py`

**Files:**
- Create: `plugins/rigger-core/tools/slot-run/slot_run.py`
- Test: `plugins/rigger-core/tools/slot-run/tests/test_slot_run.py`, with `tests/fake_reviewer.py` beside it

**Interfaces:**
- Consumes: `snapshot.read_snapshot`, `snapshot.SNAPSHOT_FORMAT` and `slots.from_snapshot` (Tasks 10 and 15 fix the shape: the snapshot key `slots` holds `as_snapshot`'s value); `review_scope.scope` and `resolve_commit`; `slot_check.check`; the decisions of Q3 and Q4.
- Produces:
  - `strip_env(environ: Mapping[str, str]) -> Dict[str, str]`: without names that start `CLAUDE_` or `HARNESS_` or equal `RIGGER_MODULE_DIRS`, compared in upper case.
  - `fill(argv: List[str], values: Dict[str, str]) -> List[str]`: one pass, the four placeholders only.
  - `resolve_program(name: str, path: str) -> str`, raising `Refusal`.
  - `TIMEOUT = 900`, `CONSOLE_CAP = 1_000_000`, `OUT_CAP = 5_000_000`.
  - `main(argv: Optional[List[str]] = None, environ: Optional[Mapping[str, str]] = None, stdout=None) -> int`, with the command line, lines and exit codes of D43 (and `--config-dir` if Q3 asks for it).
  - Refusal reasons, each one line after `slot-run: refused: `: `no snapshot for this session; start a new session`, `session id is not usable`, `slot <slot> is <kind>; dispatch its target instead`, `slot <slot> is empty`, `the project's command for <slot> is not trusted`, `<name> not found on PATH`, the batch-file reason of Q4, `nothing to review between <base> and <head>`, `timed out after <n> s`, `more than <n> bytes of console output`, `the command exited <n>`, `the command wrote no output file`, `the output file is over <n> bytes`, and `<rule id>: <reason>` from `slot_check`.

- [ ] **Step 1: Write the failing tests.** Each test writes a format-2 snapshot into a temp config dir and builds a two-commit repository. The command is `[sys.executable, fake_reviewer.py, <mode>, "--out", "{out}", "--scope", "{scope}", "--base", "{base}"]`: a real interpreter with a script, so no test depends on a shell. `fake_reviewer.py` writes its argv, working directory and environment names to a file, then acts by mode.
  - `test_ok_run_names_the_output_file_and_prints_ok` (mode `ok`): the last line is `slot-run: ok reviewer 1 findings`, exit 0, and the line before names a file that holds the JSON.
  - `test_refused_output_is_refused` (mode `bad-status`): `slot-run: refused: status: …`, exit 2.
  - `test_timeout_kills_the_tree_and_refuses` (Review Focus 3; mode `hang`, which starts a grandchild and writes its pid): with `TIMEOUT` patched to 2, the refusal prints within 15 s and the grandchild's pid is gone.
  - `test_output_over_the_cap_refuses` (Review Focus 3; mode `flood`, `CONSOLE_CAP` patched to 10,000).
  - `test_environment_is_stripped`: the parent holds `CLAUDE_X`, `claude_y`, `HARNESS_Z`, `RIGGER_MODULE_DIRS`, `RIGGER_OTHER` and `ANTHROPIC_Q`; the child sees only the last two of them, and `PATH`.
  - `test_no_shell_reads_the_arguments`: argv elements `; touch <tmp>/m1`, `$(touch <tmp>/m2)` and `&& x` arrive unchanged in the child's argv file, and neither marker exists.
  - `test_placeholders_are_filled_once`: `{base}` becomes the 40-character id even when `--base HEAD~1` was given; the `{scope}` file lists the paths that `review_scope` marks `review`; a value that itself holds `{out}` is not filled again.
  - `test_old_or_missing_snapshot_refuses` (Review Focus 8): no file, a format-1 file, and the ids `../x` and `a/b`. Each refuses with its reason and the fake reviewer's argv file does not exist.
  - `test_slot_that_is_not_a_command_refuses_with_the_dispatch_hint`, `test_empty_slot_refuses`.
  - `test_untrusted_project_command_in_the_snapshot_refuses`: source `project` with an empty trusted list.
  - `test_program_not_found_refuses`; `test_batch_file_refuses` (Windows only, kept or dropped by Q4).
  - `test_the_command_runs_from_the_repository_root`: started from a subfolder.
  - `test_same_base_and_head_refuses`, `test_nonzero_exit_refuses_even_with_a_valid_file`, `test_no_output_file_refuses`.
- [ ] **Step 2: Run.** Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.** Console output goes to a temp file, not a pipe, and the wait loop polls its size. The tree kill follows `scripts/run_tests.py:258-274` (copied, since a plugin cannot import from `scripts/`). The work folder comes from `tempfile.mkdtemp(prefix="rigger-slot-run-")` and is kept, since the caller reads the output file.
- [ ] **Step 4: Run** on Windows and under WSL. Expected: `OK`.
- [ ] **Step 5: Red runs** (full): pass `shell=True`; keep `CLAUDE_*`, then `HARNESS_*`, then `RIGGER_MODULE_DIRS`; compare names case-sensitively (the `claude_y` row fails); skip the trusted check; read a format-1 snapshot; kill only the child; drop the console bound; pass `--base` through unresolved; accept a non-zero exit; skip `slot_check`; print `ok` when `slot_check` refused.
- [ ] **Step 6: Commit.** `feat(rigger-core): slot_run runs a command slot with no shell and checks its output`

### Task 15: Wiring (serial)

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/config.py` (`ACTIVE_ABOVE_L1`, `Resolved`), `assemble.py` (`Inputs`, `assemble`, `_snapshot_data`, `check`), `snapshot.py` (`SNAPSHOT_FORMAT = 2`), `scripts/run_tests.min`
- Test: `tests/test_config.py`, `tests/test_assemble.py` and `tests/test_snapshot.py` in the assembler's folder

**Interfaces:**
- Consumes: everything Tasks 9–12 produce; Q3's command-line form.
- Produces:
  - `config.ACTIVE_ABOVE_L1 = frozenset(("schema", "triggers", "expects", "slots", "trust"))`
  - `Resolved.levels: List[Tuple[str, dict]]`: `(source, cleaned data)` per level, lowest first, level-2 conflicts already removed. `Resolved.effective["slots"]` is now the plain merge of all levels, before the trust filter: nothing but `slots.resolve_slots` may read slots from it.
  - `Inputs.session_id: str = ""`, set from the hook input and from `--session-id`.
  - `Assembly.slot_rows: List[slots.Row]`
  - Snapshot: `"slots": slots.as_snapshot(rows, trusted)`.
  - The block `("<core root>", slots.table(rows, command_line), "slots.slots")`, main audience only, after the last block of rigger-core's rules files, registered in the id index before the personal layer is placed (D36).
  - `--check` findings: `slot <path>: <reason>` for a level-1 default that fails `entry_problem` against the checked modules (a plugin outside the set is external, as in D18); `unclosed or nested {{if}} in <file>`; `second trigger table in <file>`; `{{slot:<path>}} outside {{if}} in <file>` when that slot's default plugin is outside the checked set; `trigger <id> loads process.tdd without testing before it` (D47).

- [ ] **Step 1: Write the failing tests.**
  - `test_config.py`: `test_slots_and_trust_above_level_one_apply` replaces `test_slots_above_level_one_are_recorded_not_applied`; `review`, `shell_guard`, `company` and `leak_guard` stay in `inactive`. `test_levels_are_kept_in_order`.
  - `test_assemble.py`:
    - `test_slots_section_closes_core_and_is_main_only`, `test_slots_section_for_the_default_layout_is_under_1200_bytes`.
    - `test_a_personal_section_cannot_replace_slots` (the duplicate notice), `test_drop_by_heading_drops_slots`.
    - `test_default_install_without_superpowers_is_quiet` (Review Focus 5): no notice; the testing row's cell is `` `rigger-core:testing` ``; no row loads a `superpowers:` skill; the four `process.*` rows show `—`; and a `{{if slot:process.brainstorm …}}` region in a fixture rules file is gone.
    - `test_a_personal_slot_changes_the_row_the_cell_and_the_placeholder`: level 3 sets `slots.testing.target` to a skill of an enabled stub plugin; the `## Slots` row, the trigger cell and a `{{slot:testing}}` in a fixture rules file all show it.
    - `test_project_command_without_trust_prints_the_line_and_keeps_the_agent`, through real files in a temp project.
    - `test_two_fillers_line_at_startup_and_after_compact` (Review Focus 6).
    - `test_format_one_snapshot_is_read_again` (Review Focus 8): a format-1 file, then `compact`; the output starts with `rigger: snapshot missing; rules read again`, and the file is format 2 afterwards.
    - `test_compact_without_a_snapshot_applies_the_trust_filter_again`.
    - `test_every_part_packs_the_same_text_with_slots`: the six parts run separately join to `--render`'s text.
    - `test_command_line_holds_the_session_id`, and `--render` without one prints `<session id>`.
    - `test_check_findings`: one planted fixture per new `--check` finding, each expecting its exact line, and the unchanged repository gives `findings 0`.
  - `test_snapshot.py`: the format constant is 2 and a format-1 file reads as None.
- [ ] **Step 2: Run the three files.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** `assemble()` calls `slots.resolve_slots(resolved.levels, modules, discovery.enabled, _this_os(), <level-3 path>)`, adds its notices, and passes `slots.slot_targets(rows)` wherever `slots` was used (`_requires_met`, `RenderContext.slots`). It builds each `RenderContext` with `rows`, `enabled` and `met`, shares one `table_seen` list across the assembly, drops the `NO_TABLE` notice and reports a second table as a notice. Then set `scripts/run_tests.min` to the number `python -I -c "import sys; sys.path.insert(0, 'scripts'); import run_tests; print(len(run_tests.discover(run_tests.ROOT)))"` prints (expected 75: 71 plus `test_slots.py`, `test_review_scope.py`, `test_slot_check.py`, `test_slot_run.py`).
- [ ] **Step 4: Run** every test file under `plugins/rigger-core/tools/assemble/tests/`, `tests/test_module_contracts.py` and `test_harness_start.py`. Expected: `OK`. The golden test and `test_plugin_rules.py` are expected to fail here, and only on these: the new `## Slots` piece, the three table pieces (the first placeholder now holds every row and the other two render nothing), and the two `second trigger table` notices. Task 16 owns them. Paste the failing piece names in the report; any other red piece stops the task.
- [ ] **Step 5: Red runs.** Full, for the trust wiring: remove the `resolve_slots` call's trust step by passing an all-trusting list (the project-command test fails); read slots from `Resolved.effective` (the same test fails). Others, one each: leave `SNAPSHOT_FORMAT` at 1; place `## Slots` in the subagent audience; register its id after the personal layer; keep `rows_by_section` mode (the default-install test fails on a `superpowers:` cell).
- [ ] **Step 6: Commit.** `feat(rigger-core): the assembler resolves slots and prints the Slots table`

---

## Pull request 3: the cutover and what reads it (waves E–F)

### Task 16: Cutover — one table, slot wording, the reviewer contract ending (serial)

**Files:**
- Modify:
  - `plugins/rigger-rules/rules/skills.md` moves with `git mv` to `plugins/rigger-core/rules/skills.md`. New body: the heading, a blank line, `- Load the skill before the first step of its kind:`, a blank line, `{{triggers:skills.skill-triggers}}`.
  - `plugins/rigger-core/rigger-module.json` (add `{"file": "rules/skills.md", "audience": "both"}` after `state-file.md`), `plugins/rigger-rules/rigger-module.json` (remove it).
  - `plugins/rigger-windows/rules/windows-skills.md` is deleted with its manifest entry.
  - `plugins/rigger-wp/rules/wp-skills.md`: heading `## WordPress site copies`, then today's lines 3–4, and no placeholder.
  - `plugins/rigger-rules/rules/plans-main.md:3-4` becomes
    `- A brief or full requirements never waive brainstorming or planning{{if slot:process.brainstorm slot:process.plans}} (load` / `  {{slot:process.brainstorm}}, then {{slot:process.plans}}){{end}}, the thinking pass (goal, reader,`.
  - `plugins/rigger-core/agents/worker.md:8-10`: "… and any domain skill your task needs. Load the skills the Skill triggers table names for writing code or tests and for a bug or failing test, each before that step. Never load a brainstorming, planning or dispatching skill."
  - `plugins/rigger-core/agents/reviewer.md`:
    - line 17: "(procedure in the testing skill the Skill triggers table names)";
    - one sentence on reading diffs: "Read a diff with `git --no-pager diff --no-ext-diff --no-textconv --no-color <base>..<head>`, a large one written with `--output=<file in the scratch folder>`.";
    - one sentence on behaviour: "For each change, name every existing caller, hook or user flow that now behaves differently, as before → after with `file:line`, under `Behaviour changes`; an intended change is reported there and is not a finding.";
    - the contract ending: "End the reply with one fenced `json` block holding the `rigger.reviewer/1` object: `status`, `base`, `head`, `findings` (each with `id`, `file`, `line`, `end_line`, `severity`, a `quote` copied from the file, `title`, `detail`, `pre_existing`, `evidence`), `coverage` (`in_scope`: every path of `git diff --name-status <base>..<head>`; `reviewed`; `skipped`, each with a `reason`), `shortcuts` and `shortcuts_check`. A changed file that is neither reviewed nor skipped makes the status PARTIAL." The `SHORTCUTS:` block stays.
  - `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md`:
    - line 86: "the `mapper` slot's agent (locations only)";
    - lines 89–93: "Agent type: the `worker` slot for implementation, the `reviewer` slot for review or verification (table `## Slots`; an agent's description says when another type fits). … Set `model` on every dispatch except a slot whose Model is `pinned`; an agent dispatched without one inherits yours.";
    - a new bullet after it: "A reviewer slot of kind `command` is run with the command under `## Slots`, not dispatched: its last line is the result, and `slot-run: refused` is handled like a `STATUS: BLOCKED` report, never as no findings. Check an agent reviewer's closing JSON with `tools/slot-check/slot_check.py reviewer <reply file> --base <base> --head <head>` from the rigger-core plugin root; a refusal is a failed review.";
    - line 122: "append it to the lessons inbox where the `lessons` slot is filled (`lessons-inbox.md` in the config dir) and put it in the next briefs";
    - line 147: "two fresh read-only agents of the `reviewer` slot".
  - `plugins/rigger-core/commands/coordinate.md:49`: "(the session loads the testing skill the Skill triggers table names and runs only those; …"; `:62`: "loads the brainstorm and plans skills the Skill triggers table names and sends its questions and design to you". Line 16 stays (D46).
  - `plugins/rigger-core/templates/implementer-brief.md:3`: "(the `worker` or `implementer` slot's agent, or `general-purpose`)"; `:50-51`: "the `worker` and `reviewer` slots' agents load them themselves, the `implementer` slot's agent gets none"; `:56` gets the same wording for its `rigger-core:implementer`.
- Modify tests:
  - `tests/test_assembly_golden.py` and `tests/golden_baseline.py`: the allowed differences of D40 as one named table `ALLOWED`, applied to the baseline side before the comparison; the layout gains the `superpowers` stub (installed and enabled, and as a dirs entry in the `RIGGER_MODULE_DIRS` scenario).
  - `plugins/rigger-rules/tests/test_plugin_rules.py`: `SkillTableTests`, `TestingSkillTriggerTests`, `RULES_CEILING_BYTES`, and the count of table placeholders.
  - The four rules-file counts, 19 to 18: `tests/test_repo.py:130`, `plugins/rigger-core/tools/assemble/tests/test_sections.py:69`, `plugins/rigger-rules/tests/test_rules_hooks.py:318` and `RULES_MIN_FILES` (`test_plugin_rules.py:38`). A phrase search does not find these.
  - The golden test's subagent root-line set and its per-case `## Slots` piece (D40).
  - `plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py:99`, `plugins/rigger-core/tests/test_core_wiring.py`, `plugins/rigger-rules/tests/test_reinject.py:117`, `plugins/rigger-wp/tests/test_outbound_block.py`, and the drift-lint baseline.

**Interfaces:**
- Consumes: Tasks 9–15 merged into the branch.
- Produces: the behaviour changes 1–5.

- [ ] **Step 1: Baseline.** Run the golden test and `test_plugin_rules.py`. Expected: red only on the pieces Task 15 reported (`## Slots`, the three tables, the two notices).
- [ ] **Step 2: Find every pin.** For each old phrase above, `git grep -n -F "<phrase>" -- '*.py' '*.ps1' '*.json'`, and add any test not listed to this task's report before editing. For the counts, `git grep -n -E "\b19\b" -- 'tests/*.py' 'plugins/*/tests/*.py' 'plugins/*/tools/*/tests/*.py'` and read each hit: the four above count rules files; `test_reinject.py:117` and `test_stub_session.py` count main-session sections, which stay 19 (rigger-windows loses one, rigger-core gains `## Slots`; its `## Skill triggers` only moves) and are recounted from `--render`; `test_assembly_golden.py:402` counts the frozen baseline's handlers and does not change.
- [ ] **Step 3: Rewrite the tests first.**
  - `test_merged_table_holds_every_row_of_the_three_tables` (golden file): the expected list is the 13 row lines cut from the three frozen files under `tests/fixtures/golden-34e7713/` (8, 3 and 2, asserted). With all seven modules and the stub, the merged table's rows equal that list as a multiset, for the main session and for a `general-purpose` subagent. A second assertion pins the order of D38: the fixture's five rigger-core rows, the three windows rows, the two wp rows, then the fixture's three slot rows.
  - `test_overrides_of_merged_table_sections` (Review Focus 7): a `rules.d/skills.md` with `## Skill triggers` replaces the table and prints `rigger: rules.d/skills.md replaces rule skills.skill-triggers`; a `rules.d/windows-skills.md` with the old heading prints as a personal section.
  - `test_every_other_piece_is_unchanged`: with `ALLOWED` applied, each scenario's normalised output equals the baseline's. The test also asserts that every entry of `ALLOWED` was used at least once in the run.
  - `SkillTableTests`: every skill folder of rigger-core, rigger-windows and rigger-wp appears in the one rendered table exactly once by its installed name; exactly one rules file in the repository holds a `{{triggers:…}}` line.
  - `TestingSkillTriggerTests`: in the row data (manifests plus `rigger-defaults.json`), every row whose `load` has `slot:process.tdd` has `slot:testing` before it; the rendered cell with the stub enabled is today's; and `worker.md` holds the new sentence.
- [ ] **Step 4: Run them.** Expected: `FAILED` on the table shape and the wording.
- [ ] **Step 5: Edit** the rules, manifests and texts above with the Edit tool, one hit each unless stated.
- [ ] **Step 6: Set the ceiling.** Print the rendered rules-file total the way `RulesSizeTests` sums it, without the `slots.slots` block. Set `RULES_CEILING_BYTES` to that number. If it is over 27,708, stop and report (D50). Add `SLOTS_CEILING_BYTES = 1200` for the `slots.slots` block.
- [ ] **Step 7: Run** the golden test, `test_plugin_rules.py`, `test_drift_lint.py`, `test_coordinate_wiring.py`, `test_core_wiring.py`, `tests/test_repo.py`, `tests/test_module_contracts.py`, `tests/test_plugin_refs.py`, the rigger-wp tests and `test_stub_session.py` and `test_reinject.py` locally with `claude`. Expected: all `OK`; the two stub files report the new section count taken from `--render`.
- [ ] **Step 8: Red runs.** Leave the old placeholder in `wp-skills.md` (the second-table check and the equality test fail); delete one windows row from its manifest (the equality test fails on the multiset); remove the stub from the layout (it fails on the three slot rows); swap two module blocks in `collect_rows` (the order assertion fails); remove one entry's effect from `ALLOWED` (its piece fails); add an unused entry to `ALLOWED` (the "used" assertion fails).
- [ ] **Step 9: Commit.** `feat!: one skill-trigger table, slot wording and the reviewer contract ending`, with the behaviour changes 1–5 in the body and a `Test-change:` trailer naming the rewritten assertions.

### Task 17: Contract verdicts in the report shadow

**Files:**
- Modify: `plugins/rigger-core/tools/report-gate/report_shadow.py`, `plugins/rigger-core/tools/report-gate/INSTALL.md`
- Test: `plugins/rigger-core/tools/report-gate/tests/test_report_shadow.py`

**Interfaces:**
- Consumes: `slot_check.check(slot, text, shape_only=True)`; `snapshot.read_snapshot` and `slots.from_snapshot`; Q2's decisions.
- Produces: row fields `slot` (a slot path or `""`) and `contract` (`ok`, `refused:<rule id>`, `none`, `unread`, `error`); `--summary` lines gain ` contract-ok N contract-refused N`. `WAIT` stays 2.5.

- [ ] **Step 1: Write the failing tests.**
  - `test_worker_report_gets_its_contract_verdict`: a good report gives `slot worker`, `contract ok`; one with two summary bullets gives `refused:summary`.
  - `test_reviewer_reply_is_checked_for_shape_only`: a reply whose JSON has a bad severity gives `refused:severity`; one whose quote matches nothing still gives `ok`.
  - `test_an_agent_that_fills_no_slot_gets_none`, `test_an_unread_report_gets_unread`.
  - `test_long_and_broken_reports_get_a_row_in_time` (Review Focus 9): 300 KB of text, two JSON blocks, a JSON cut in the middle. Each writes one row in under 2.5 s and nothing to stderr.
  - `test_the_row_never_holds_report_text`: a planted word from the report is not in the row file.
  - `test_slot_table_source`: per Q2, the session's snapshot, else level-1 defaults; a format-1 snapshot falls back to the defaults.
  - `test_a_missing_slot_check_still_writes_the_row`: with the import failing, `contract` is `error` and the old fields are unchanged.
  - `test_summary_counts_contract_verdicts`.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** The imports of `slot_check`, `snapshot` and `slots` happen inside the worker thread, inside its `try`.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Call `check` without `shape_only`; map every agent type to `worker`; store the refusal reason text in the row (the planted-word test fails); import at module level and break the import (the row test fails).
- [ ] **Step 6: Commit.** `feat(rigger-core): the report shadow records contract verdicts per slot`

### Task 18: Doctor rows

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/assemble.py` (`_doctor` only), `plugins/rigger-core/tools/doctor/doctor.sh:583-588` (the row filter), `plugins/rigger-core/commands/doctor.md`
- Test: `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`

**Interfaces:**
- Consumes: `Assembly.slot_rows` (Task 15).
- Produces these rows, each one whole `CHECK` or `FIX` line that `doctor.sh` lets through:
  - `slot <path>`: OK `<target> (<source>)`; for a hook slot OK `<fillers> (fails open)`, `(fails closed)` or `(failure mode: undeclared)`; INFO `empty: <reason>` for a level-1 default; WARN `filled by <A> and <B>` with `FIX … claude plugin disable <B>`; MISSING `expected <plugin> is not enabled` with `FIX … claude plugin install <plugin>` for a filler set above level 1.
  - `slot-checker shell-guard`: OK with the checker's path, or INFO `no checker: live:<rule> lessons are reported as no checker`.
  - `slot-order <trigger id>`: WARN `loads process.tdd without testing before it` (D47).
  - `rules-d-table <file>`: INFO `holds a trigger table; move its rows to triggers in <config file>: <JSON>`, the JSON being one line built from the table's rows, cut at 1,500 characters with ` …` (spec §13 "Migration").

- [ ] **Step 1: Write the failing tests** in the style of `doctor_sh_harness.py`: a clean layout gives one `slot` row per slot and no WARN; two fillers give the WARN and its FIX; a level-3 expected filler that is absent gives MISSING and its FIX; a manifest without `failure_mode` gives `undeclared`; a `rules.d/mine.md` with a two-row table gives the `rules-d-table` row whose JSON parses and holds both rows; a level-4 row with TDD first gives `slot-order`; and `slots.reviewer.model` at level 3 no longer gives a `config-inactive` row while `leak_guard.denylist` still does. One row forged by a config value (a tab and `CHECK` inside a slot target) does not get through `doctor.sh`.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** A name goes into a FIX line only when it matches `_FIX_NAME`.
- [ ] **Step 4: Run** all files under `doctor/tests/`. Expected: `OK`.
- [ ] **Step 5: Red runs.** Drop the new names from the `doctor.sh` filter (the rows vanish and the tests fail); put an unchecked name into a FIX line; report only one of two fillers.
- [ ] **Step 6: Commit.** `feat(rigger-core): doctor lists slot fillers, double fillers and trigger tables in rules.d`

### Task 19: Stub exit criteria

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/tests/test_stub_session.py`

**Interfaces:**
- Consumes: Q3's working permission form and command-line form; `slot_run.py`; `tests/fake_reviewer.py` of Task 14.
- Produces: the proof of exit criterion 2, labelled "stub".

- [ ] **Step 1: Write the tests.** The scratch config's `rigger/config.json` sets `slots.reviewer` to the fake reviewer command (level 3, so no trust entry is needed).
  - `test_the_slots_section_reaches_the_first_request`: the probe request holds `## Slots`, a `reviewer` row of kind `command` and the command line with this session's id.
  - `test_a_stub_command_reviewer_is_run_and_a_refused_output_is_blocked`: the stub cuts the command line out of the probe request and answers with a shell tool_use that runs it with `--base <id of the fixture's first commit>`. With the fake reviewer in mode `bad-status`, the next request's `tool_result` ends with `slot-run: refused: status: …`, and the fake reviewer's argv file exists (the control that `slot_run.py` started it). With mode `ok`, it ends with `slot-run: ok reviewer 1 findings`. If Q3 found no working form, the test starts `slot_run.py` itself with the session's id, and its docstring says the model-side call is not proved.
  - `test_project_command_without_trust_never_runs`: the same command set in the project's `.claude/rigger.json` with no trust entry. The probe request holds the trust notice and an agent `reviewer` row, and a direct `slot_run.py reviewer --session <id>` refuses with the dispatch hint.
- [ ] **Step 2: Run** the file locally with `claude`. Expected: `OK`, 5 tests (2 from phase 1). The report says "stub" and gives the CLI version.
- [ ] **Step 3: Red runs.** Make `slot_run.py` print `ok` on a refusal (the second test fails on the `bad-status` case); remove the trust step (the third test fails on the notice).
- [ ] **Step 4: Commit.** `test(rigger-core): stub session runs a command reviewer and blocks a refused output`

### Task 20: The raw readers of rules text

**Files:**
- Modify, only where Step 2 shows a difference: `plugins/rigger-windows/tools/drift-lint/drift_lint.py`, `plugins/rigger-lessons/tools/proposal_check.py`, `plugins/rigger-lessons/tools/batch_model.py`, and `plugins/rigger-lessons/skills/curate-lessons/SKILL.md` (how the rendered text is passed)
- Test: their existing test files

**Interfaces:**
- Produces: for each tool, either a test that pins its result on rules text with placeholders, or an input `--rules-text <file>` that takes the output of `assemble.py --render`.

- [ ] **Step 1: List what each tool reads.** `git grep -n "rules" -- <the three files>`; record the globs (`proposal_check.py:98`, `batch_model.py:121-123`, `drift_lint.py:391-399`).
- [ ] **Step 2: Measure.** Run each tool's rules-reading step on the repository twice: on the raw files, and on a temp copy whose rules files hold the rendered sections of `assemble.py --render`. Diff the two results and paste the diff.
- [ ] **Step 3: Decide per tool.** No difference: add one test row with a `{{triggers:…}}` line, an `{{if …}}` region and a `{{skill:…}}` placeholder that pins today's result. A difference: add `--rules-text`, a failing test for the case the diff showed, and the skill sentence that passes the file.
- [ ] **Step 4: Run** the three test files, `test_drift_lint.py` and `test_plugin_rules.py`. Expected: `OK`.
- [ ] **Step 5: Red run** per new test, named in the report.
- [ ] **Step 6: Commit.** `test: rules readers outside the assembler handle placeholders`, or `feat:` with the tool named when an input was added.

---

## Docs, gate and pull request (wave G)

### Task 21: Docs, release notes and migration

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), `plugins/rigger-core/README.md` (slots, `## Slots`, the three tools, `trust.project_commands`, the caps, doctor rows), `plugins/rigger-rules/README.md` (the table moved), `plugins/rigger-wp/README.md`, `plugins/rigger-windows/README.md`, `CONTRIBUTING.md` (contract fixtures: where they live and that every rule needs a `fail-` file)

- [ ] **Step 1: Write the Unreleased entries:** the eight behaviour changes; the migration of spec §13 (update the plugins; a table copied into `rules.d` moves to `triggers`, and doctor prints the JSON); the two changed section ids; that an override of `slots.slots` is refused; the contract `rigger.reviewer/1` with its optional `behaviour_changes` key; the differences from the spec (D33, D35, D38, D39, D48, D50); and Q1–Q4's decisions in one line each.
- [ ] **Step 2: Find stale text.** `git grep -n -e "windows-skills" -e "three tables" -e "config-inactive" -e "no effect in this release" -- '*.md' ':!docs/superpowers'`, and correct each hit.
- [ ] **Step 3: Sweep** the changed files. Expected: `findings 0`.
- [ ] **Step 4: Commit.** `docs: phase 2 slots, contracts and the merged trigger table`

### Task 22: Gate and pull request

**Files:**
- No new files.

- [ ] **Step 1:** Run `python -I scripts/gate.py` in the background and read `status.txt`. Expected: `# gate passed, <s> s[, skipped N], tree <id>`.
- [ ] **Step 2:** `python -I scripts/gate.py --verify <status.txt>`. Expected: exit 0.
- [ ] **Step 3:** Check the exit criteria of spec §13 one by one and paste the evidence:
  - every default filler passes its fixtures and each failing fixture fails on its own rule: the `test_every_contract_fixture` and `test_default_fillers_meet_their_contracts` lines;
  - the stub run: Task 19's output, labelled "stub";
  - the merged table: `test_merged_table_holds_every_row_of_the_three_tables`;
  - the full gate: the pass line.
- [ ] **Step 4:** Push `feature/modular-phase-2` and open the pull request to `main`. The body covers the spike decisions, the behaviour changes, the gate pass line, the mutation list per task with every surviving mutant and its reason, the answers used for questions 1–8, and the report-shadow summary line from a real session if one was run.
- [ ] **Step 5:** Wait for CI on the three systems. Merging is the owner's step.

---

## Review ideas left for phase 3

Phase 2 took the coverage report, the hardened diff flags and the behaviour-change question (D49).
The rest belong to the `rigger-review` module, one line each:

- **Strict finding dismissal:** the orchestrator may mark a finding `disputed` only with a quoted line that proves it wrong (the described code is not there, or the line says the opposite); protected topics (concurrency, bounds, behaviour or compatibility changes, authorisation, money, data) need a fresh reviewer first. Phase 2's quote anchor already rejects a finding about code that is not there.
- **Per-path review rules:** `review.paths` maps globs to rules files that the review skill loads only for matching files (spec §13 phase 3).
- **Per-language checklists:** `checklists/<language>.md`, loaded by the extensions in scope, without what the linters and `defects.md` already cover.
- **More skip reasons in `review_scope.py`:** generated, vendored and lockfile, never test files.
- **A second round for full-risk changes:** a fresh reviewer gets the first one's findings and looks for others; it changes the one-review rule, so it needs the owner's decision.
- **A risk list before the review** for large diffs: one sentence in the review skill, with its effect measured first.
- **Splitting a very large diff into groups** reviewed by separate agents: only as an orchestrator decision, and it multiplies usage.

## Estimate

| Part | Agent hours | Code lines | Test lines |
|---|---|---|---|
| `--changed-modules` (Task 1) | 2.5 | 170 | 280 |
| Spikes Q1–Q4 (Tasks 2, 6, 7, 8) | 4.5 | 0 in the repository (about 400 in scratch) | — |
| Phase 1 backlog (Tasks 3, 4, 5) | 3 | 90 | 260 |
| Manifest fields and slot resolution (Tasks 9, 10) | 4.5 | 350 (`slots.py` 300, manifest 50) | 520 |
| Regions, merged table, discovery (Task 11) | 2.5 | 130 | 260 |
| `review_scope.py` (Task 12) | 1.5 | 130 | 220 |
| `slot_check.py` and fixtures (Task 13) | 4.5 | 400 | 380, plus about 450 lines of fixture files |
| `slot_run.py` (Task 14) | 3 | 230 | 380 |
| Wiring (Task 15) | 3 | 170 | 330 |
| Cutover (Task 16) | 4 | 90 (rules, manifests and wording) | 380 |
| Report shadow, doctor (Tasks 17, 18) | 3 | 170 | 330 |
| Stub run, raw readers (Tasks 19, 20) | 3 | 30 | 260 |
| Docs, gate, three pull requests (Tasks 21, 22, and the gate steps of 1 and 5) | 3 | — | — |
| **Total** | **42.5** | **1,960** | **3,600** |

Against the spec's 12 h, 800 and 1,100 lines, the differences are:
- work the spec's phase 2 does not hold: `--changed-modules` (2.5 h), the phase 1 backlog (3 h) and the raw readers (1 h);
- the four spikes (4.5 h), which the spec's figure did not price, as in phase 1;
- the contracts: six slots with a fixture per rule, about 30 rules, where the spec priced the reviewer alone;
- the trust filter by provenance, the fallback walk and the filler rules, which the spec gives one line each;
- the cutover's golden rework with a named list of allowed differences;
- three pull requests, each with a full gate.

The spec-scope part alone (Tasks 6–19, 21 and 22 without the backlog and the test mode) is about
34 h, 1,700 code lines and 3,000 test lines: 2.8 times the spec's hours. Phase 1 ran at 1.8 times.

**Simpler version, about 21 h, 900 code lines, 1,700 test lines:**
- **What it keeps:** `--changed-modules`; the phase 1 backlog with spike Q1; slots of kind `skill`,
  `agent` and `hook` with validation, the fallback walk and the `## Slots` table; the merged table
  with row dropping and `{{if}}`; the rewording; the worker, implementer and mapper contracts with
  their fixtures; doctor rows.
- **What it drops:**
  - the `command` kind, so `slot_run.py`, spikes Q3 and Q4, the trust filter (a project file can
    then only name agents and skills, and `kind: command` is refused at every level) and the
    command half of Task 19;
  - `review_scope.py` and the git rules of the reviewer contract, so coverage and anchors are not
    checked: the reviewer contract is shape only;
  - the report shadow's contract column and spike Q2.
- It still meets "one table" and "validated fillers", but not exit criterion 2 (the stub command
  run) and not spec goal "swap the reviewer for an external tool". It saves about 21 h, because
  the command path and the git-backed checks are where the full-risk red runs are.

## Open risks

1. **Q3 may find no way for a `claude -p` session to run a stub-scripted shell command.** Exit
   criterion 2 is then proved with `slot_run.py` started by the test and the session's real
   snapshot, and the model-side call stays unproved until a manual run.
2. **The shell-guard contract is weaker than the spec's** (D48, questions 2 and 3): no rule id in
   the reason and no latency bound. A filler that denies with an empty-looking reason would pass.
3. **`slot_run.py` keeps `ANTHROPIC_*` in the command's environment** (D43). An external reviewer
   named by the person or by a trusted project sees those variables. Removing them would break a
   tool that needs its own key; the trust filter is the control.
4. **A snapshot is trusted as the person's own file.** A process that can write
   `<config dir>/rigger/state/` can make `slot_run.py` run a command. That folder has the same
   protection as `settings.json`, which can already run hooks.
5. **A trusted project command is trusted for every later version of that repository's file.** The
   trust entry names a slot, not an argv. Pinning the argv hash in `trust.project_commands` is a
   possible phase 4 step; it is not in the spec.
6. **macOS is not measured in Q4** and has no local leg here. CI runs Task 14's tests there, and a
   failure in the tree kill or the program lookup would first show in CI.
7. **The golden test's `ALLOWED` list could hide a real difference** in the pieces it names. Each
   entry is a whole expected piece, not a wildcard, and the "used at least once" assertion stops a
   stale entry from staying.
8. **The merged table changes row order** (D38). A person who learned the old order sees the slot
   rows last. No rule depends on the order.
9. **`--changed-modules` has no real use until phase 3**, because every phase 2 change is shared or
   in rigger-core. Its tests run on a fixture repository; the first module-only pull request is
   its real proof.
10. **Two rule checks duplicate `report_shadow.py`'s own `classify` and `has_summary`** (the worker
    contract's `status-line` and `summary`). Task 13 may import them from `report_shadow.py`
    instead of writing them again, as long as the fixtures stay the proof.
