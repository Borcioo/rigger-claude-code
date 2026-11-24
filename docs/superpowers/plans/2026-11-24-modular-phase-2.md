# Modular architecture, phase 2: slots and contracts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slots get validated fillers, a trust filter and a `## Slots` table; reviewer, worker,
implementer and mapper output is checked against machine contracts; a command can fill the reviewer
slot; and the three skill-trigger tables become one that drops rows of absent plugins.

**Status:** approved 2026-11-24 after an independent plan review and re-check (revision 2), written
on `1f1e204` (release 0.5.0, which holds phase 1). The coordinator's rulings are under "Rulings on the plan's questions".

**Architecture:** New logic lands in new files. `plugins/rigger-core/tools/assemble/slots.py`
resolves and validates slots from the four config levels and applies the trust filter.
`tools/review-scope/review_scope.py` lists what a review must cover, `tools/slot-check/slot_check.py`
checks a reply against its slot's contract, and `tools/slot-run/slot_run.py` runs a command slot
with no shell and checks its output. One serial wiring task connects them to `assemble.py`, which
prints the `## Slots` section and the merged table. Two pull requests: the test mode with the
phase 1 backlog first, the slot work second.

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
- `scripts/run_tests.min` reads 71 today. Task 15 sets it to 74 (three new test files exist then)
  and Task 14 to 75, each to the count `run_tests.py` discovers at that commit.
- Gate (local): `python -I scripts/gate.py` in the background, started on Windows with the full
  path of the installed Python. Read the `status.txt` its first line names. Between fixes,
  `python -I scripts/run_tests.py --fix-round`, with `--no-claude` when no `claude` run is allowed.
  Before a pull request, `python -I scripts/gate.py --verify <status.txt>`; when the base moved,
  rerun the gate.
- Every task of both pull requests touches a file that `--changed-modules` calls shared, or
  rigger-core, on which five modules depend. Their gate is the full gate. `--changed-modules` is
  proved on its own tests in Task 1. Until phase 3 it is a fast local pre-check for every change:
  a pull request and a release still need the full gate (D26).

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
6. A rules part above 1 that fails prints one line of its own (D29).
7. `core_check.sh` also runs on `compact`, if spike Q1 shows its line arrives there (D31).
8. A config file may hold at most 50 trigger rows, and a `load` entry at most 200 characters (D28).
9. A deny of rigger-windows' shell guard names its rule: `shell-guard [<rule>]: …` (D48).
10. A session in which a project's command fills a slot prints that command, cut to 200 characters
    with its size and a hash, at `startup`, `clear` and `compact` (D53).

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
399, 347, 364, 345, 355 ms, no output
(a bare `sh -c 'exit 0'`: 82, 81, 92 ms here; the plan review measured 45–49 ms for it and 254–311 ms for the handler)
$ shell_guard.py --only-narrow on a Bash `ls` event, started directly (in a session its `if` filter starts no process)
327, 293, 331, 331, 320 ms
$ shell_guard.py:60-66 and :3334
class Finding: __slots__ = ("rule", "deny", "message")     deny = next((f for f in findings if f.deny), None)
$ shell_guard.py:3339-3343
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
`plugins/rigger-core/tools/assemble/tests/test_part_sh.py:95-96` (parts above 1 print nothing),
`plugins/rigger-rules/tests/test_core_check.py:266` (matcher `startup|clear`),
`tests/test_repo.py:548` (the same matcher), `test_config.py:243-259` and
`test_doctor_sh_plugins.py:369-373` (`slots` above level 1 is inactive),
`tests/test_assembly_golden.py` (three tables), `test_stub_session.py` and `test_reinject.py:117`
(19 sections), and `test_plugin_rules.py:409`
(`test_the_brainstorming_route_comes_from_the_process_slots`, the placeholders of `plans-main.md`).

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
   `test_finding_path_rows` and
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
11. **A cloned repository that holds a program named like the person's own reviewer command**
    (`reviewer-cli.exe` in its root), or a `PATH` with an empty or relative entry. A person expects
    their installed program to run, or a refusal, never the repository's file. Test: Task 14,
    `test_program_planted_in_the_repository_is_never_run`.

---

## Decisions this plan makes

Phase 1 used D1–D24. Each task below follows these.

| # | Decision | Why |
|---|---|---|
| D25 | Two pull requests. (1) Tasks 1–5: `run_tests.py --changed-modules`, the phase 1 backlog and spike Q1, branch `feature/phase-2-opener`. (2) Slots and contracts, branch `feature/modular-phase-2`, cut from `main` after the first is merged. Spikes Q2–Q4 run while (1) is in review. | Coordinator ruling: the mode still opens the phase, but shares its pull request with the backlog, which saves one full gate and one review round. Task 3 edits `manifest.py` and `config.py`, which wave C also edits, so the first pull request merges before wave C starts. |
| D26 | `--changed-modules` maps each changed path this way. **Shared, exit 2:** `scripts/**`, `tests/**`, `plugins/*/rules/**`, `plugins/*/bin/py.sh`, `plugins/*/hooks/core_check.sh`, `plugins/rigger-core/rigger-defaults.json`, `.github/**`, `.claude-plugin/**`, any other path outside `plugins/` that is not documentation, and any path under `plugins/<name>/` whose folder holds no `rigger-module.json` in the working tree. **Documentation, no module:** `docs/**`, and a file directly in the repository root named `*.md`, `LICENSE*` or `NOTICE*`. **Module:** everything else under `plugins/<name>/`, a module's own Markdown included. The mode then runs every discovered test file under each changed module and under each module whose `plugin.json` `dependencies` reach a changed module, plus the always-run set (`tests/test_module_contracts.py`, `tests/test_repo.py`, `tests/test_plugin_refs.py`, `plugins/rigger-rules/tests/test_plugin_rules.py`) and the sweep with `--no-denylist`. A git failure is exit 2, never a silent full run. Until phase 3 the mode is a local pre-check, not a gate: exit 0 does not replace `gate.py` for a pull request or a release. | Tests of one module read files of another that it does not depend on (measured by the plan review with `git grep` over the module test folders): rigger-core's `test_stub_session.py:28` and `test_coordinate_host_stub.py:26` import `stub_api.py` from rigger-rules' tests; rigger-core's `test_doctor_sh_plugins.py:317,343` copies rigger-rules and rigger-lessons; rigger-rules' `test_core_check.py:291-293` copies rigger-windows. A change to one of those read files runs only its own module's tests, so the mode can exit 0 where the gate fails. Spec §12 lists `scripts/`, `tests/`, rules, the defaults file, `rules_start.sh` and `py.sh`. Added: `core_check.sh` (five identical copies, like `py.sh`), the workflow and the marketplace listing, which no module owns. `tests/test_repo.py` pins handler counts and reads every tracked file; it took 42 s here. `test_plugin_rules.py` reads every module's skills and trigger rows (`:311-322`), and rigger-wp and rigger-windows depend only on rigger-core, so a new skill folder there would otherwise pass the mode and fail the gate. Dependents are added because rigger-rules' tests run the assembler of rigger-core. The test set is discovery, since `test_tests_paths_cover_every_discovered_test_file` (`tests/test_repo.py:154`) already pins `tests.paths` to it. |
| D27 | CI stays as it is in this plan: the full gate on Linux and the full suite on macOS and Windows for every pull request. | Spec §12 has CI run the mode. Today's workflow is stricter and is what protects `main`. Ruling 1. |
| D28 | A config level (2, 3 or 4) may hold at most 50 trigger rows: the rows after the 50th in file order are ignored with one notice that gives the count. A `load` entry is at most 200 characters, in a manifest and in a config file; a longer one makes its row invalid with the reason `load entry over 200 characters`. Level 1 is exempt from the row cap. | Backlog item RV1 F10, with the values the phase 1 builder proposed. The longest `load` entry today is `rigger-core:orchestrating-subagents`, 35 characters. |
| D29 | A part above 1 prints its own line when its assembler exits non-zero, prints nothing, or raises: `Rigger rules part <i> of <K> not loaded: <reason> (run /rigger-core:doctor)`, wrapped as `additionalContext` with `--subagent`. The no-Python case stays one line from part 1, because no part can succeed then. A part killed by the 10 s hook timeout stays silent, as part 1 does today (open risk 11). | Backlog item RV4 M6. Each line differs by its part number, so identical-text deduplication cannot hide one. Coordinator ruling: no in-process watchdog; the six parts ran in about 0.55 s against the 10 s timeout (phase 1 review). |
| D30 | `core_check.sh` and `part.sh` derive their folder from `$0` by cutting at the last `/` or `\`. | Measured above: an all-backslash script path makes `core_check.sh` silent and makes `part.sh` print the failure line in place of the rules. `hooks.json` passes forward slashes today, so this is robustness only. `part.sh` has the same line and a worse effect, so it is fixed in the same pull request. |
| D31 | The five `core_check.sh` handlers get the matcher `startup|clear|compact` if spike Q1 shows that a line already printed at startup arrives again after `/compact`. If Q1 shows it is dropped, the matcher stays `startup|clear` and the release notes say why. | Backlog item RV4 M5. With an old rigger-core the rules are missing after a compaction as well, and the startup line is summarised away. The cost is 5 `sh` starts per compaction with no Python: 512–533 ms in sequence here, about 0.12–0.15 s each in parallel (phase 1 review), next to a compaction that already starts 8 or more handlers (6 parts, `compact_guard.py`, `worktree_root.sh`) and one model call. |
| D32 | `slots` at levels 2–4 and `trust` at level 3 become active. `review`, `shell_guard`, `company` and `leak_guard` stay recorded-only, and doctor keeps listing them as `config-inactive`. | Spec §13 phase 2. Their readers ship in phases 3 and 4. |
| D33 | The trust filter works on provenance. For each slot path (`reviewer`, `process.tdd`) it compares the entry merged from levels 1–3 with the entry merged from levels 1–4. If the second has `kind: command` and level 4 set `kind` or `target` for that path, and the path is not in level 3's `trust.project_commands`, the whole level-4 value for that path is dropped, `model` included, with the line of spec §7.4 carrying the real config path. A level-4 `null` and a level-4 `model` alone are allowed. `trust` set at level 4 is already refused by the level table. A level-2 command (a company module the person installed) is allowed. | Spec §7.4 says "may not set `kind: command`", but an L4 `target` alone would rewrite an L3 argv. Review Focus 1. |
| D34 | Validation walks down the levels: the entry merged from levels 1–4 (after D33), then 1–3, 1–2, then 1. The first that validates fills the slot. A failed entry set above level 1 gives one notice, `rigger: slot <path> from <file or module> ignored: <reason>; using <level name>` or `…; the slot is empty`. A level-1 default that does not validate because its plugin is not enabled leaves the slot empty with no session notice; doctor lists it as INFO. A `null` for one slot path empties that slot. A `null` for `slots` itself or for the `process` group, at any level above 1, is ignored with the notice `rigger: config <file> may not set <key> to null; set a single slot to null instead`. | Spec §7.3 step 5 gives one notice per fallback. Superpowers is optional, so four notices in every session without it would be noise about a supported setup (Review Focus 5). `config.py:130-131` passes a `null` through for any key, and `merge` then removes the key: one line in a cloned repository would empty every slot without a word. |
| D35 | A `hook` slot is filled by every enabled module whose manifest has `fills: <slot>`. rigger-windows gets `fills: "shell-guard"`, `failure_mode: "open"` and `provides.shell-guard.rule_checker: "tools/shell-guard/lessons_guard.py"`; rigger-lessons gets `fills: "lessons"`. The config entry is the expected filler: when it is set above level 1 and that plugin is not enabled, one notice names it. Two fillers give `rigger: slot <slot> is filled by <A> and <B>; disable one with claude plugin disable <name>`. The assembler prints that line as a notice at `startup`, `clear` and `compact`; `harness_start.py` is not changed. | Spec §10.2 places the two-filler check in `harness_start.py`, which runs at `startup` only and would resolve modules a second time. One resolver, one place for notices (phase 1's D13). |
| D36 | `## Slots` is a generated section, id `slots.slots`, printed for the main session only, under rigger-core's root line, after core's rules files. One row per slot path whose `requires` is met: `Slot`, `Kind`, `Target`, `Model`, `From` (`default`, `company:<module>`, `personal`, `project`). An empty slot shows `—` as its target. A hook row shows its fillers joined with ` + ` and the failure mode, `rigger-windows (fails open)`. One line follows the table only when a slot is a command: how to run `slot_run.py`, with the core root and the session id filled in (Q3 decides its exact form). A `rules.d` section with the id `slots.slots` does not replace it: it is a duplicate, with the usual notice. `HARNESS_RULES_DROP` drops it like any section. | Spec §10.1. The override ban keeps the table true: a personal text must not be able to show a filler that is not the one in effect. |
| D37 | No rules sentence names an optional slot. `plans-main.md:3-4` loses its two `{{slot:process.…}}` placeholders and reads "(load the brainstorm and plans skills the Skill triggers table names)". Rules text gets no conditional construct. `--check` reports a `{{slot:<path>}}` whose level-1 default plugin is outside the checked modules, as `{{slot:<path>}} in <file> names a slot an absent plugin fills`. A `{{skill:<plugin>:<skill>}}` renders its name whether or not the plugin is enabled, as today. | Spec §10.4 says an empty role drops its `{{slot:…}}` sentences. `git grep '{{slot:' -- 'plugins/*/rules/*.md'` finds one use, `plans-main.md:4`; with it reworded there is nothing to drop, and the table's row dropping covers a setup without superpowers. Coordinator ruling (cut of the `{{if}}` construct). |
| D38 | The merged table lives in `plugins/rigger-core/rules/skills.md` (audience `both`), moved from rigger-rules with `git mv`, so its id stays `skills.skill-triggers`. Its intro becomes `- Load the skill before the first step of its kind:`. `windows-skills.md` is deleted with its manifest entry. `wp-skills.md` keeps its two-line sentence under the new heading `## WordPress site copies` and loses its placeholder. `{{triggers:<id>}}` renders every row whatever the id, and `section` in a row is ignored but stays valid. A second `{{triggers:…}}` placeholder renders nothing, with a notice, and is a `--check` finding. Row order is spec §9.3: module rows in module order, then config rows. | With the table in rigger-rules, a setup with rigger-core and rigger-wp but no rigger-rules would lose the wp rows it prints today. Today's order (core 5, slot rows 3, windows 3, wp 2) becomes core 5, windows 3, wp 2, slot rows 3. Ruling 7. |
| D39 | A `load` entry is dropped when its plugin is not in `Discovery.enabled` or its slot is empty. A row is dropped when no entry is left or its `requires` is unmet. `Discovery.enabled` also gets the `plugin.json` name of a `RIGGER_MODULE_DIRS` entry that holds `.claude-plugin/plugin.json` but no `rigger-module.json`, with no notice; an entry with neither file keeps today's notice. | Spec §9.3. Without the second sentence a plugin loaded with `--plugin-dir` that is not a module (superpowers in a stub run, an eval or a developer session) can never count as enabled, and its rows would vanish in every such run. Spec §6.1 gives that entry a notice today. No eval case names a superpowers skill or a table heading (`git grep` over the 15 `plugins/*/evals/*/prompt.md` files: 0 hits), so the evals, which stage no superpowers, lose nothing they grade. Ruling 6. |
| D40 | The golden test keeps comparing with `34e7713` and gets a fixed list of allowed differences: the rigger-rules list loses the `## Skill triggers` piece; the rigger-core list gains one with the new intro and the merged table; the rigger-windows list loses its table piece; the rigger-wp piece is its sentence under the new heading; the rigger-core list gains `## Slots` (main session); the subagent root-line set gains rigger-core's root, because core now prints `skills.md` to subagents; and the `## Plans: main session` piece is the new `plans-main.md` wording. Every other piece stays byte-equal. The expected `## Slots` piece is written by hand in the test for each case it can meet, never taken from `slots.table`: Windows with rigger-windows (11 rows, `shell-guard` filled, `fails open`), Windows without it (11 rows, `shell-guard` shown `—`), and any other system (10 rows, no `shell-guard` row). A new test compares the merged table's rows, as a list of row lines, with the 13 rows cut from the frozen fixture files. The golden layout gains an enabled stub plugin named `superpowers` (a `plugin.json` only). | The brief's equality requirement and the exit criterion "renders every row of today's three tables". The expected rows come from the frozen fixture, never from `collect_rows`, so a bug in row collection cannot shrink both sides. |
| D41 | `slot_check.py <slot> <file>` prints one line, `slot-check: ok <slot>` (for the reviewer `slot-check: ok reviewer <n> findings`) or `slot-check: refused: <rule id>: <reason>`, and exits 0 or 2. Any exception is a refusal with the rule id `internal`. A reviewer agent ends its reply with the JSON in the last fenced `json` block; `slot_check.py` reads a file that is either a JSON object or a reply holding such a block, and does not accept a Markdown form (open point 3). Unknown keys in the JSON are ignored, so a reviewer may add keys the contract does not name. Fixtures live in the filling module at `tests/contract/<slot>/`, as `pass*` files and one `fail-<rule id>*` file per rule. `tests/test_module_contracts.py` runs every fixture of every manifest's `tests.contract` through rigger-core's `slot_check.py`. | Spec §10.4 and §12. A module's own tests cannot reach rigger-core by path, so the fixture run sits in the repository-level contract test. |
| D42 | `review_scope.scope()` is the one list of what a review covers: the `{scope}` file of a command reviewer, the contract's `coverage-scope` rule and the command `reviewer.md` names all use it. In phase 2 it lists every path of `git diff --name-status` between two commits and marks it `review` or `skip: deleted`. Binary, generated, vendored and lockfile reasons are phase 3 (spec §13); a reviewer may still skip a file with a reason of its own. A revision that starts with `-` is refused in Python; both revisions are then resolved to commit ids with `git rev-parse --verify <rev>^{commit}` before any diff, only those ids reach the diff, and the diff runs with `git -c core.quotepath=off --no-pager diff --no-ext-diff --no-textconv --no-color --no-renames --name-status -z`. It never skips a test file. | Spec §10.5 and phase 3's description. Without `--no-renames` git prints one `R100 d.py e.py` row where this list has `D d.py` and `A e.py` (review probe), so a reviewer who ran a plain `git diff --name-status` would be refused on every rename. Review Focus 4. |
| D43 | `slot_run.py <slot> --session <id> [--base REV] [--head REV] [--repo DIR]` reads the slot only from that session's snapshot and never resolves config itself. The program is found by `slot_run.py`'s own lookup, never by the operating system's: a bare name is searched in the absolute entries of the stripped `PATH` only (with `.exe` and `.com` on Windows), a name with a separator must be an absolute path, and the result must lie outside the repository and outside the working directory. `Popen` gets that absolute path and an explicit `cwd`. A target that resolves to `.cmd` or `.bat` is refused whatever Q4 shows; an npm tool is named as `node <script>`. It also refuses when the snapshot is missing or of another format, when the slot is not a command, and when a project command is not in the snapshot's trusted list. It writes the scope file (every path of `review_scope.scope()`, one `<verdict><TAB><path>` line each) and the output file under a fresh folder in the temp directory, runs the argv with no shell from the repository root with `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS` removed, bounds it at 900 s and 1 MB of console output, and checks the output file with `slot_check.py`. Its last line is `slot-run: ok <slot> <n> findings` or `slot-run: refused: <reason>`; the line before it names the output file. Exit 0 or 2. | Spec §9.4 and §10.3. On Windows, `shutil.which(name, path=…)` and a bare name given to `Popen` both search the working directory first unless `NoDefaultCurrentDirectoryInExePath` is set (review probe on Python 3.11.9 and 3.12.10), so a cloned repository holding `<name>.exe` would be run in place of the person's own reviewer, with no trust entry (Review Focus 11). A batch file always runs through `cmd.exe`, which re-parses its arguments: that is a shell. The session id comes on the command line because a shell tool process may not know it (Q3). `ANTHROPIC_*` stays, since an external reviewer may need its own key. |
| D44 | The snapshot's `slots` value becomes the resolved table (D36 rows plus `trusted`, the level-3 list), and `SNAPSHOT_FORMAT` becomes 2. | Phase 1's D21: a snapshot of another format is read as missing. Review Focus 8. |
| D45 | The report shadow checks shape only: it runs the contract of the slot whose target is the stopped agent's type with no git and no file read besides the report, inside the existing 2.5 s bound. Its row gains `slot` and `contract` (`ok`, `refused:<rule id>`, `none` when the type fills no slot, `unread`). The row still never holds report text. | Spec §13 asks for verdicts "before anything is enforced". Anchors and scope need git and the reviewed commit, which the orchestrator's own `slot_check.py` call has. Q2 decides where the slot table comes from. |
| D46 | `coordinate.md:16` stays as it is. The other ten references are reworded to name a slot or the Skill triggers table (Task 16 gives each text). | Line 16 names `rigger-core:orchestrating-subagents`, which no slot covers: the command and the skill are both fixed core (spec §1). A command file cannot hold a placeholder. |
| D47 | "Every row that loads `slot:process.tdd` loads `slot:testing` before it" becomes a `--check` finding and a doctor WARN row for config rows. `TestingSkillTriggerTests` reads row data, not table text. | Spec §10.4, the `testing` contract. A config row can break the rule without any module changing. The contract's other clause, that the filling module ships the commit-time test guards, becomes checkable only when phase 3 moves them out of rigger-core. |
| D48 | The `shell-guard` contract in phase 2 checks (a) a hook reply that is empty, or one JSON object with `permissionDecision: "deny"` whose reason holds a rule id in square brackets (`[<rule id>]`), or one with `additionalContext`; (b) the declared failure mode; and (d) that a named rule checker loads and has `RULES` and `rule_guard_problem`. rigger-windows' deny reason becomes `shell-guard [<rule>]: <message> (…)`, the id taken from `Finding.rule` (`shell_guard.py:60-66`). (c), the 50 ms bound, is not enforced and not printed by any test; the measured numbers go into the pull request body. The `lessons` contract checks (a), the `SessionStart` line; (b) stays `test_inbox_lint.py`; (c) arrives with the lesson targets of phase 4. | Coordinator rulings on questions 2 and 3. The guard's deny is already a `Finding` with a rule (`:3334`), so naming it costs one line, and the phase 3 filler inherits a real check. Its PowerShell handler, the only one without an `if` filter, took 345–399 ms here and 254–311 ms in the review's run. |
| D49 | Of the five review ideas, three enter phase 2: the coverage report (`coverage` and the rules on it), hardened `git diff` flags (in `review_scope.py` and one sentence of `reviewer.md`), and the behaviour-change question as one sentence of `reviewer.md` that asks for a Markdown `Behaviour changes` section. The contract gets no key for it. Strict finding dismissal and per-path rules are listed under phase 3. | The test was whether an idea needs a field or a check in `rigger.reviewer/1`. Coordinator ruling on question 4: an external tool's contract does not grow a key the approved spec lacks, and unknown keys are ignored anyway. |
| D50 | `RulesSizeTests` keeps one ceiling for rules-file text, set in Task 16 to the measured sum after the merge, which must not exceed 27,708. `## Slots` is counted on its own: at most 1,200 bytes for the default set. | Spec §3 fixes 27,708 for rules text and §13 expects about 600 bytes of slots. Eleven rows with five columns do not fit 600. Ruling 5. |
| D51 | The phase 2 spikes are Q1–Q4, recorded in `docs/superpowers/spikes/phase-2/`. S6 and S7 of the spec stay with phases 5 and 3. | Their names must not collide with the spec's. |
| D52 | The three raw readers of phase 1's D17 (`drift_lint.py`, `proposal_check.py`, `batch_model.py`) get one evidence task (Task 20): each is run on raw and on rendered rules text and the differences are listed. A tool whose result differs gets a `--rules-text <file>` input; one whose result is the same gets a test that pins it. | Phase 1 deferred this to phase 2. With D37 the merge adds no placeholder line that phase 1 did not already leave, so the work may be evidence only. |
| D53 | A row's `source` is the level that set its `kind` and `target`; a level that set only `model` does not change it. When a project's command is in effect for a slot, the session gets one notice: `rigger: slot <path> runs the project's command <shown argv> (trusted in <config file>)`. `<shown argv>` is the argv as one line of ASCII JSON, cut to its first 200 characters, followed by ` … (<n> bytes, sha256:<first 12 hex digits>)`, both taken from the whole JSON line. | `slot_run.py` refuses a `project` command that is not trusted, so a level-3 command with a level-4 `model` must stay `personal`. Trust names a slot, not an argv: after a pull the repository can change the whole command, and the `## Slots` cell shows only the program name. The hash covers the whole argv, so the notice text differs per argv and deduplication cannot hide a change. The cut is needed because an argv may hold 32 elements of 500 characters, about 16 KB of JSON, and notices print with part 1, whose output must stay under 9,500 bytes: past the cap the session would lose part 1's rules. Pinning an argv hash stays a phase 4 idea. |

## Rulings on the plan's questions

The first draft asked eight questions. The coordinator, under the owner's delegation, ruled on each
after the plan review, and on four decisions that change spec text.

1. **CI and `--changed-modules`:** CI stays on the full gate (D27).
2. **The 50 ms bound of the shell-guard contract:** not enforced in phase 2 (D48). It is restated
   for `rigger-SHELLWISE` in phase 3.
3. **A deny reason names a rule id:** the guard prints the id from `Finding.rule`, and the contract
   checks it (D48).
4. **A `behaviour_changes` key:** not added (D49).
5. **The size of `## Slots`:** its own cap of 1,200 bytes, outside the 27,708 ceiling (D50).
6. **A `RIGGER_MODULE_DIRS` entry without a manifest:** one with a `plugin.json` counts as an
   enabled plugin, with no notice (D39).
7. **Who owns the merged table:** rigger-core (D38).
8. **The raw readers of D17:** one evidence task (D52).
9. **Batch files:** `slot_run.py` refuses a `.cmd` or `.bat` target whatever spike Q4 shows (D43).
10. **D34, D35, D46 and the command line of D43:** accepted as written.

## Spec corrections

Each line is a change to the spec's text that this plan makes. The second pull request's body
carries the list, and Task 21 applies it to the spec file.

| Spec | Correction | Reason |
|---|---|---|
| §7.3 step 5 | A level-1 default whose plugin is not enabled leaves its slot empty with no notice; a fallback from a level above 1 keeps its one notice. `null` for `slots` or for a slot group is ignored with a notice. | Superpowers is optional; one line in a cloned file must not empty every slot (D34). |
| §7.4 | The filter drops a level-4 value that sets `kind` or `target` of a slot whose merged kind is `command`, not only one that writes `kind: command`. A project command in effect prints its argv, cut to 200 characters with its size and a hash, at `startup`, `clear` and `compact`. | Fields merge one by one, so a `target` alone would rewrite a level-3 argv (D33, D53). |
| §6.1 | A `RIGGER_MODULE_DIRS` entry with a `plugin.json` and no manifest counts as an enabled plugin, with no notice. | A non-module plugin loaded with `--plugin-dir` could otherwise never count as enabled (D39). |
| §9.2, §10.4 | No rules sentence names a slot an absent plugin fills; `--check` enforces it. There is no sentence dropping. | One use existed, and it is reworded (D37). |
| §9.3, §13 | The merged table is printed by rigger-core, so a setup without rigger-rules gets it. The rigger-windows table section goes, and the rigger-wp section is renamed. | A table owned by rigger-rules would lose rows that rigger-core with rigger-wp prints today (D38). |
| §10.2 | The two-filler line is a notice of the assembler at `startup`, `clear` and `compact`, not a check in `harness_start.py`. A missing expected filler is reported in the session only when it was set above level 1. | One resolver and one place for notices (D35). |
| §10.3 | The command is `slot_run.py <slot> --session <id> [--base REV] [--head REV] [--repo DIR]`, with no `--out`. The program is resolved to an absolute path outside the repository, and a batch file is refused. `{scope}` names a file of `<verdict><TAB><path>` lines. | A shell tool process may not know its session; the working directory must not take part in finding the program; `cmd.exe` is a shell (D43). |
| §10.4 `shell-guard` | (a) the rule id stands in square brackets in the reason. (c) the 50 ms bound applies from phase 3. | The default filler measures 254–399 ms (D48). |
| §10.4 `testing` | The clause on commit-time test guards applies from phase 3. | The guards live in rigger-core until then (D47). |
| §10.5 | `in_scope` is the list of `review_scope.py`, which runs `git diff` with `--no-renames`; an agent reviewer runs that command. | A plain `git diff --name-status` joins a rename into one row (D42). |
| §12 | Shared files also include `plugins/*/hooks/core_check.sh`, `.github/**` and `.claude-plugin/**`. The mode also runs `tests/test_repo.py`, `tests/test_plugin_refs.py` and `plugins/rigger-rules/tests/test_plugin_rules.py`, and the tests of dependent modules. CI keeps the full gate. | Files no module owns, and tests that read every module (D26, D27). |
| §12 | `--changed-modules` is a local pre-check until phase 3, not "the gate for a change inside one module": a pull request and a release need the full gate. | Four test files read another module's files without a dependency between the two, so the mode can pass where the gate fails (D26). |
| §13 phase 2, rewording | `coordinate.md:16` is not reworded. | It names a fixed core skill that no slot covers (D46). |
| §13 phase 2, behaviour | `## Slots` is capped at 1,200 bytes, not "about 600". | Eleven rows with five columns (D50). |
| §13 phase 2, estimate | About 40 h, 1,850 code lines and 3,450 test lines. | See Estimate. |

## File map

| Path | Task | Responsibility |
|---|---|---|
| `scripts/run_tests.py`, `tests/test_run_tests.py`, `CONTRIBUTING.md`, `CLAUDE.md` (the Gate bullet) | 1 | the `--changed-modules` mode and its docs |
| `docs/superpowers/spikes/phase-2/Q1.md` … `Q4.md` | 2, 6, 7, 8 | recorded spike results and the decision taken |
| `plugins/rigger-core/tools/assemble/manifest.py`, `config.py` (+ `test_manifest.py`, `test_config.py`) | 3 | the row and entry caps |
| `plugins/rigger-core/tools/assemble/part.sh`, `assemble.py` (`main` only), `tests/test_part_sh.py`, `tests/test_assemble.py` | 4 | lost-part lines for parts above 1, the launcher folder |
| five `plugins/*/hooks/core_check.sh`, five `plugins/*/hooks/hooks.json`, `plugins/rigger-rules/tests/test_core_check.py`, `tests/test_repo.py:548` | 5 | the launcher folder and the `compact` matcher |
| `plugins/rigger-core/tools/assemble/manifest.py` (+ test), `plugins/rigger-windows/rigger-module.json`, `plugins/rigger-lessons/rigger-module.json`, `plugins/rigger-core/rigger-module.json`, `tests/test_repo.py` (`ManifestTests`) | 9 | `fills`, `failure_mode`, `provides`, `tests.contract` |
| `plugins/rigger-core/tools/assemble/slots.py`, `tests/test_slots.py` | 10 | slot table, validation, trust filter, fillers, the `## Slots` rows |
| `plugins/rigger-core/tools/assemble/render.py`, `discover.py` (+ `test_render.py`, `test_discover.py`) | 11 | the merged table, entry dropping, non-module roots |
| `plugins/rigger-core/tools/review-scope/review_scope.py`, `tests/test_review_scope.py` | 12 | the list of files a review covers |
| `plugins/rigger-core/tools/slot-check/slot_check.py`, `tests/test_slot_check.py`, `plugins/rigger-core/tests/contract/{reviewer,worker,implementer,mapper}/`, `plugins/rigger-windows/tests/contract/shell-guard/`, `plugins/rigger-lessons/tests/contract/lessons/`, `tests/test_module_contracts.py`, `plugins/rigger-windows/tools/shell-guard/shell_guard.py` (the deny reason) and the guard tests that pin it | 13 | contracts and their fixtures; the rule id in a deny |
| `plugins/rigger-core/tools/slot-run/slot_run.py`, `tests/test_slot_run.py`, `scripts/run_tests.min` | 14 | running a command slot |
| `plugins/rigger-core/tools/assemble/assemble.py`, `config.py`, `snapshot.py` (+ their tests), one assertion of `doctor/tests/test_doctor_sh_plugins.py`, `scripts/run_tests.min` | 15 | wiring: resolution, `## Slots`, notices, snapshot format 2, `--check` |
| rules files of rigger-core, rigger-rules, rigger-windows and rigger-wp, their manifests, `agents/worker.md`, `agents/reviewer.md`, `skills/orchestrating-subagents/SKILL.md`, `commands/coordinate.md`, `templates/implementer-brief.md`, `tests/test_assembly_golden.py`, `tests/golden_baseline.py`, `plugins/rigger-rules/tests/test_plugin_rules.py`, the wording tests named in Task 16 | 16 | the cutover |
| `plugins/rigger-core/tools/report-gate/report_shadow.py`, `INSTALL.md`, `tests/test_report_shadow.py` | 17 | contract verdicts in the shadow rows |
| `plugins/rigger-core/tools/assemble/assemble.py` (`_doctor` only), `plugins/rigger-core/tools/doctor/doctor.sh` (the row filter), `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`, `commands/doctor.md` | 18 | doctor rows |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` | 19 | stub exit criteria |
| `plugins/rigger-windows/tools/drift-lint/`, `plugins/rigger-lessons/tools/{proposal_check,batch_model}.py` and their tests | 20 | raw readers |
| `CHANGELOG.md`, `plugins/rigger-core/README.md`, `plugins/rigger-rules/README.md`, `plugins/rigger-wp/README.md`, `plugins/rigger-windows/README.md`, `CONTRIBUTING.md`, the spec file (its corrections) | 21 | release notes, migration, docs |

## Parallel waves

| Wave | Tasks | Runs after | Why they are file-disjoint |
|---|---|---|---|
| A | 1; 2 (spike Q1); 3; 4; 6, 7, 8 (spikes Q2–Q4, whose result files are committed on `feature/modular-phase-2` once it is cut) | — | Task 1 writes `scripts/run_tests.py`, its test and two docs. Task 3 writes `manifest.py`, `config.py` and their tests. Task 4 writes `part.sh`, `assemble.py` and their tests. Each spike writes its own result file and scratch folder and starts its own stub on port 0. |
| B | 5 | 2 | the `core_check.sh` copies, five `hooks.json` and two test files; nothing else touches them |
| — | pull request 1 merges; `feature/modular-phase-2` is cut from `main` | 1–5 | — |
| C1 | 9, 11, 12 | the merge above | 9: `manifest.py`, three manifests, `ManifestTests`. 11: `render.py`, `discover.py` and their tests. 12: two new files. None reads another's output. |
| C2 | 10, 13 | 9 and 12 | 10 needs Task 9's `SLOT_PATHS` and `failure_mode`. 13 imports Task 12's `scope` and reads Task 9's `tests.contract` lists. 10 writes two new files; 13 writes new files, `tests/test_module_contracts.py` and the deny reason in rigger-windows' `shell_guard.py`. |
| D | 15 (serial) | 9–13, and Q3 | It is the only task that edits `assemble.py`, `config.py` and `snapshot.py`, and it sets the snapshot format that Tasks 14 and 17 read. |
| E | 14, 16, 17 | 15; 14 also needs Q3 and Q4, 17 needs Q2 | 14 writes two new files under `slot-run/` and `scripts/run_tests.min`. 16 writes rules, manifests, agent and skill text and their tests. 17 writes only `report-gate/`. |
| F | 18, 19, 20 | 16 (the golden test, `test_plugin_rules.py` and the two tests that run `--check` on the repository are red between Tasks 15 and 16) | 18 writes `_doctor` in `assemble.py`, the filter in `doctor.sh` and the doctor tests. 19 writes one test file. 20 writes tools of two other plugins. |
| G | 21, then 22 | all | — |

Parallel: 1, 2, 3, 4, 6, 7 and 8; then 9, 11 and 12; then 10 and 13; then 14, 16 and 17; then 18,
19 and 20. Serial: 5 after 2, the merge of pull request 1, 15, 21 and 22. No two tasks of one wave
write the same file. Three files have several owners, each in a different wave:
`scripts/run_tests.min` (Task 15 sets 74, Task 14 sets 75), `tests/test_repo.py` (Task 5 at line
548, Task 9 in `ManifestTests`, Task 16 at line 130) and
`plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py` (Task 15 for one assertion,
Task 18 for the new rows).

---
## Pull request 1: the test mode and the phase 1 backlog (waves A–B)

Branch `feature/phase-2-opener`. Tasks 1–5 land here and share one gate run (Task 5, Step 7).

### Task 1: `run_tests.py --changed-modules`

**Files:**
- Modify: `scripts/run_tests.py`, `CONTRIBUTING.md` (a paragraph after the `--fix-round` one: "`python -I scripts/run_tests.py --changed-modules [--base REV]` is a fast local pre-check for a change inside one module's folder: it runs that module's tests, its dependents' and four repository-wide files, then the sweep. It is not a gate. A pull request and a release still need the full gate, and exit 2 means a shared file changed, so only the gate says anything."), `CLAUDE.md` (one sentence in the Gate bullet: "Before the gate, a change inside one module's folder may run `python -I scripts/run_tests.py --changed-modules` as a fast pre-check; it never replaces the gate for a pull request or a release, and exit 2 means a shared file changed.")
- Test: `tests/test_run_tests.py`

**Interfaces:**
- Consumes: `select_tests.changed_files(root, base)` (`scripts/select_tests.py:50`), `discover`, `run_suite`, `clean_env` of `run_tests.py`.
- Produces:
  - `ALWAYS_RUN = ("tests/test_module_contracts.py", "tests/test_repo.py", "tests/test_plugin_refs.py", "plugins/rigger-rules/tests/test_plugin_rules.py")`
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

- [ ] **Step 1: Write the failing tests.** They build a fixture repository in a temp folder with `git init`: `plugins/a` and `plugins/c` (each with `rigger-module.json`, `.claude-plugin/plugin.json` and one passing `tests/test_x.py`), `plugins/b` whose `plugin.json` depends on `a`, `plugins/zzz` without a manifest, the four always-run files as one-test files at their real paths (so `plugins/rigger-rules` is a fourth fixture module), and `scripts/sweep.py` as a stub that prints `# files 3, findings 0`. `main` is called with `root=<fixture>`.
  - `test_classify_table`: one row per line of D26.
    - `shared`: `scripts/x.py`, `tests/test_x.py`, `plugins/a/rules/r.md`, `plugins/a/bin/py.sh`, `plugins/a/hooks/core_check.sh`, `plugins/rigger-core/rigger-defaults.json`, `.github/workflows/test.yml`, `.claude-plugin/marketplace.json`, `pyproject.toml`, `plugins/zzz/x.py`.
    - `docs`: `docs/a.md`, `README.md`, `LICENSE`.
    - `("module", "a")`: `plugins/a/tools/t/t.py`, `plugins/a/hooks/hooks.json`, `plugins/a/skills/s/SKILL.md`, `plugins/a/README.md`, `plugins/a/agents/x.md`.
  - `test_shared_file_exits_two_and_runs_nothing`: with `run_file` patched to raise, a changed `scripts/x.py` gives exit 2 and the exact line.
  - `test_module_change_runs_the_module_its_dependents_and_the_always_files`: a change in `plugins/a` runs the test files of `a` and `b` and the four always-run files, and not `c`'s.
  - `test_a_new_skill_folder_runs_the_rules_tests`: an added `plugins/a/skills/x/SKILL.md` runs `plugins/rigger-rules/tests/test_plugin_rules.py`.
  - `test_docs_only_change_runs_the_always_files_and_the_sweep`.
  - `test_deleted_module_and_untracked_shared_file_exit_two` (Review Focus 10): `git rm -r plugins/c`, and separately an untracked `tests/new.py`.
  - `test_moved_file_runs_both_modules` (Review Focus 10): `git mv plugins/a/f.py plugins/c/f.py` runs `a`, `b` and `c`.
  - `test_sweep_with_findings_or_zero_files_fails`: the stub prints `# files 3, findings 2`, then `# files 0, findings 0`; each gives exit 1.
  - `test_git_failure_exits_two`: `--base no-such-rev`.
  - `test_a_failing_module_test_exits_one`.
  - `test_fix_round_next_to_it_is_refused`.
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_run_tests.py -v`. Expected: `FAILED`, each new test on its own assertion or on `unrecognized arguments: --changed-modules`.
- [ ] **Step 3: Implement.** Match paths by their segments, never with `fnmatch` (its `*` crosses `/`). The sweep runs as `[sys.executable, "-I", "-X", "utf8", "scripts/sweep.py", "--no-denylist"]` from `root` with the cleaned environment. Add the mode to the module docstring's usage, as a pre-check.
- [ ] **Step 4: Run** the file. Expected: `OK`. Then run the mode on the real repository with only this task's change in the tree: expected `changed-modules: shared file scripts/run_tests.py; run the gate` and exit 2 (`CLAUDE.md` and `CONTRIBUTING.md` are documentation under D26, so the first shared path in sorted order is the script).
- [ ] **Step 5: Red runs** (shared infrastructure, one per branch): remove the `plugins/*/bin/py.sh` row, and `test_classify_table` fails on that row; classify any `*.md` as documentation, and it fails on the `SKILL.md` row; return 0 for a shared file, and the exit-two test fails; drop `dependents`, and the module test fails on `b`; drop `test_plugin_rules.py` from `ALWAYS_RUN`, and the skill-folder test fails; skip the sweep's count check, and the sweep test fails on `# files 0`; turn a git failure into a full run, and the git test fails. That is 7 mutants over 5 checks.
- [ ] **Step 6: Sweep and commit.** `python -I scripts/sweep.py --no-denylist CONTRIBUTING.md CLAUDE.md`, expected `# files 2, findings 0`. `feat(tests): run_tests.py --changed-modules pre-checks a module-only change`

Spike result files have these headings, in order: `## Question`, `## CLI` (version and path, or
"no claude run"), `## Setup` (commands, temp paths as `<temp>`), `## Control` (the known answer and
whether it was observed), `## Observed`, `## Decision`. A spike whose control fails reruns once with
the cause fixed; if it fails twice it reports `BLOCKED`. The shared harness is a copy of
`plugins/rigger-rules/tests/stub_api.py` and of the isolation and debug-log code of
`plugins/rigger-rules/tests/test_reinject.py` in `<temp>/rigger-p2/<id>/harness.py`. No repository
file changes for a spike besides its result file. A result file holds variable names and yes-or-no
answers, never the value of an environment variable.

### Task 2: Spike Q1 — does a line repeated after a compaction arrive

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q1.md`
- Scratch: `<temp>/rigger-p2/Q1/{harness.py,plugin/.claude-plugin/plugin.json,plugin/hooks/hooks.json}`

**Interfaces:**
- Produces: whether a line already printed at startup arrives again after `/compact` (Task 5).

- [ ] **Step 1: Build the probe plugin** `spike-q1` with one `SessionStart` group, matcher `startup|compact`, timeout 10 on each handler. `SPIKE_RUN` is a fresh uuid per session in the child environment.
  - `same`: `printf 'Q1-SAME-%s\n' "$SPIKE_RUN"` (the same text at every firing of one session).
  - `firing`: reads the hook JSON and prints `Q1-FIRING-<source>-<epoch seconds>` (text that differs per firing).
- [ ] **Step 2: Known-answer control.** One session, `claude -p q1probe-<uuid> --plugin-dir <temp>/rigger-p2/Q1/plugin --debug-file …`. Expected: the request that carries the probe word holds `Q1-FIRING-startup-…` and `Q1-SAME-…` exactly once each. If not, fix the harness first.
- [ ] **Step 3: Compaction run, 3 sessions.** Each: the probe turn, then `claude -p "/compact" --resume <session>`, then a third turn with a second probe word, as `test_reinject.py:120-129` does. Record whether the request after the compaction holds `Q1-FIRING-compact-…` (the control for this step) and `Q1-SAME`, and that the debug log has `SessionStart:compact`.
- [ ] **Step 4: Write `Q1.md`.**
  - **`Q1-SAME` arrives after the compaction in all 3:** D31 adds `compact` to the five matchers.
  - **`Q1-SAME` is missing while `Q1-FIRING-compact` is present:** D31 keeps `startup|clear`, and Task 5 changes no `hooks.json`.
  - **`Q1-FIRING-compact` is missing:** the compaction did not fire the hook; fix the harness, and after two failures report `BLOCKED` for the `compact` half of Task 5 only.
- [ ] **Step 5: Sweep and commit.** `python -I scripts/sweep.py --no-denylist docs/superpowers/spikes/phase-2/Q1.md`, expected `# files 1, findings 0`. `docs(spikes): record Q1 repeated hook text after a compaction`

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

### Task 4: A failing rules part above 1 says so

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/part.sh`, `plugins/rigger-core/tools/assemble/assemble.py:839-858` (`main`, the `except` branch of `--part` only)
- Test: `plugins/rigger-core/tools/assemble/tests/test_part_sh.py`, `tests/test_assemble.py` in the same folder

**Interfaces:**
- Produces:
  - `assemble.LOST = "Rigger rules part {index} of {count} not loaded: {reason} (run /rigger-core:doctor)"`, and the same text built in `part.sh` for a part above 1.
  - Reasons: `the assembler exited <n>`, `the assembler printed nothing`, `<ErrorType>: <text>`.
  - `part.sh` and `assemble.py --part` keep exiting 0 (or 10 for nothing on purpose).

- [ ] **Step 1: Write the failing tests.**
  - `test_part_sh.py` `test_a_failing_part_above_one_prints_its_own_line`: for parts 2 and 3 and for `2 --subagent`, the rows "exit 3", "missing script", "empty file" and "output then exit 1" each give the exact `LOST` line, wrapped as `additionalContext` in subagent mode. The no-Python assertions at `:95-96` stay: parts above 1 print nothing there (D29).
  - `test_part_sh.py` `test_backslash_script_path` (Windows only, skipped elsewhere with the reason): `sh <the script path written with backslashes> 1`, run from another folder, prints the same bytes as the control.
  - `test_assemble.py` `test_a_crash_in_a_part_above_one_prints_its_line`: with `assemble.assemble` patched to raise `ValueError("x")`, `--part 3` writes `LOST` with `ValueError: x` and returns 0.
- [ ] **Step 2: Run both files.** Expected: `FAILED`; the backslash test fails on the `assembler exited 127` line measured above.
- [ ] **Step 3: Implement.**
  - `part.sh`: `case $0 in *[\\/]*) here=${0%[\\/]*} ;; *) here=. ;; esac` (D30). For an index above 1, a failure prints the `LOST` line, except the no-Python case.
  - `assemble.py`: the `except` branch builds `LOST` for an index above 1 and keeps `CRASH` for part 1.
- [ ] **Step 4: Run** both files and `plugins/rigger-rules/tests/test_plugin_rules.py`. Expected: `OK`.
- [ ] **Step 5: Red runs** (shared infrastructure): remove the index-above-1 branch of `part.sh` (the first test fails); print the line in the no-Python case too (the `:95-96` assertions fail); restore `here=${0%/*}` (the backslash test fails); keep the old `except` branch (the crash test fails).
- [ ] **Step 6: Commit.** `fix(rigger-core): a rules part above 1 that fails says so`. If Step 1 had to change an existing assertion, the commit carries a `Test-change:` trailer with the reason.

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
- [ ] **Step 7: Gate and pull request.** Full gate (Tasks 1 and 5 change shared files), `--verify`, push `feature/phase-2-opener`, open the pull request to `main`. Its body gives the classify table and the always-run set, Q1's decision, the five-start timing, the caps, the changed behaviour of a failing part, and the spec corrections for §12.

---

## Pull request 2: spikes (wave A)

### Task 6: Spike Q2 — what `SubagentStop` carries

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q2.md`
- Scratch: `<temp>/rigger-p2/Q2/{harness.py,q2.py,plugin/…}`

**Interfaces:**
- Produces: for Task 17, where the report text and the slot table come from, and the form of `agent_type` for a plugin's agent.

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
- Scratch: `<temp>/rigger-p2/Q3/{harness.py,q3.py,dump.sh,dump.ps1,echo_args.py,plugin/…}`

**Interfaces:**
- Produces: for Task 15, the exact command line under `## Slots`; for Task 19, how the stub makes a session run `slot_run.py`.

- [ ] **Step 1: Build the probe.** Plugin `spike-q3` has one `SessionStart` handler that reads the hook JSON and prints `Q3-SESSION-<session_id>`. The stub answers the request carrying `q3probe-<uuid>` with a tool_use of a shell tool it finds in the request's `tools` list (`Bash`; record whether `PowerShell` is listed too). Its command is `sh "<temp>/rigger-p2/Q3/dump.sh" <session id>`, the id cut from `Q3-SESSION-…` in the request. `dump.sh` writes to a file: the working directory; the **names** of all environment variables; for each name, `yes` or `no` for "its value equals the session id given as the argument"; and `yes` or `no` for "`CLAUDE_CONFIG_DIR` is set and equals the scratch config dir". It writes no other value. Then it prints `Q3-RAN-<run>`.
- [ ] **Step 2: Known-answer control, and the permission form.** The plan review reasoned, without a run, that `--permission-mode acceptEdits` cannot allow a shell command; this step measures it. Run with `--allowedTools Bash`. Expected: a later request holds a `tool_result` with `Q3-RAN-<run>`. If the tool call is refused, repeat with `--permission-mode bypassPermissions`, then with a `permissions.allow` entry for `Bash` in the scratch config's `settings.json`, and record which form works.
- [ ] **Step 3: Second run.** The stub answers with the command `sh "<core root>/bin/py.sh" "<temp>/rigger-p2/Q3/echo_args.py" reviewer --session <id> --base HEAD`, the form D36 plans. `echo_args.py` writes its argv to a file.
- [ ] **Step 4: PowerShell run** (only if `PowerShell` is in the tools list). The same two commands through a `PowerShell` tool_use, with `dump.ps1` writing what `dump.sh` writes. Record whether `sh` is found there and whether the argv of Step 3 arrives whole.
- [ ] **Step 5: Write `Q3.md`.** Record the environment names, the working directory, and the yes-or-no answers.
  - **The `tool_result` arrived (any form of Step 2):** Task 19 uses that form.
  - **No form works:** Task 19's second test starts `slot_run.py` itself with the stub session's id, and the report says the model-side call was not proved.
  - **`CLAUDE_CONFIG_DIR` reaches the shell process:** the `## Slots` line is `sh "<core root>/bin/py.sh" "<core root>/tools/slot-run/slot_run.py" <slot> --session <id> --base <rev>`.
  - **It does not:** the line also carries `--config-dir "<config dir>"`, and `slot_run.py` takes that option.
  - **The argv of Step 3 arrived changed, or the line fails in the PowerShell tool:** record the form that arrives whole in every shell tool listed on Windows and on Linux (the `py.sh` path quoted, `sh` named by its full path if that is what it takes), and Task 15 prints that form. No form works in PowerShell: the line names the Bash tool ("run with the Bash tool:").
- [ ] **Step 6: Sweep and commit.** `docs(spikes): record Q3 shell tool environment`

### Task 8: Spike Q4 — starting an external program with no shell

**Files:**
- Create: `docs/superpowers/spikes/phase-2/Q4.md`
- Scratch: `<temp>/rigger-p2/Q4/q4.py` and its probe programs. No `claude` run.

**Interfaces:**
- Produces: for Task 14, proof that its program lookup, tree kill and output bound work on Windows and Linux.

- [ ] **Step 1: Write `q4.py`.** Each case starts a child with `subprocess.Popen(argv, shell=False, env=<stripped>, cwd=<explicit>)`, where the child is a Python script that writes its argv as JSON to a file. The stripped environment is `os.environ` without `CLAUDE_*`, `HARNESS_*` and `RIGGER_MODULE_DIRS`. Run it on Windows and under WSL, with every Python from 3.9 to 3.12 that is installed; macOS is recorded as not measured.
- [ ] **Step 2: Known-answer control.** `[sys.executable, child.py, "a b", "x&y", '"q"', "%PATH%", "^z"]`: the child's file holds those five strings unchanged.
- [ ] **Step 3: Cases.**
  1. **The working directory in the lookup** (the plan review ran this on 3.11.9 and 3.12.10 only). A folder holds a copy of a real small program named `reviewer-cli.exe` (on Linux an executable script `reviewer-cli`). With the working directory set to that folder and `PATH` set to a system folder that has no such name, record for each Python: what `shutil.which("reviewer-cli", path=…)` returns and whether `Popen(["reviewer-cli"])` runs the planted file, once with `NoDefaultCurrentDirectoryInExePath` set to `1` and once with it removed (restored in `finally`). On Linux, repeat with an empty `PATH` entry and with a `.` entry.
  2. **The planned lookup:** absolute `PATH` entries only, `.exe` and `.com` on Windows, the result compared with the repository root by `os.path.realpath`. Same folder and settings as case 1: it must return nothing.
  3. A child that starts a grandchild sleeping 60 s, stopped with the tree kill of `scripts/run_tests.py:260-276`; then check that the grandchild's pid is gone.
  4. A child that writes 50 MB to stdout, with stdout sent to a temp file, stopped once the file passes 1 MB.
  5. A `.cmd` shim of the form package managers write, with the five arguments of the control: record what arrives. This is for the release notes only; `slot_run.py` refuses batch files whatever it shows (ruling 9).
- [ ] **Step 4: Write `Q4.md`.**
  - **Case 1 runs the planted file under some setting, and case 2 returns nothing under every setting:** Task 14 uses the planned lookup as written.
  - **Case 2 returns the planted file under any setting:** `BLOCKED`, with the output; the lookup of D43 is wrong.
  - **The grandchild survives, or the output bound does not stop the child:** `BLOCKED`, with the output.
- [ ] **Step 5: Sweep and commit.** `docs(spikes): record Q4 program lookup and bounds without a shell`

---
## Pull request 2: building blocks (waves C1–C2)

Branch `feature/modular-phase-2`, cut from `main` after pull request 1 is merged. Wave C1 holds
Tasks 9, 11 and 12. Wave C2 holds Tasks 10 and 13, which read what Tasks 9 and 12 produce.

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
  - `@dataclass class Row: path: str; kind: Optional[str]; target: object; model: Optional[str]; source: str; fillers: Tuple[str, ...]; failure_mode: Optional[str]; filled: bool`. `target` is a string, an argv list or None. `source` is `default`, `company:<module>`, `personal` or `project`: the level that set the row's `kind` and `target`. A level that set only `model` does not change it (D53).
  - `flatten(slots: dict) -> Dict[str, dict]`: keys are slot paths; a value under `process` that is not an object, and any unknown path, is returned under its path so the caller can report it.
  - `entry_problem(path: str, entry: dict, modules: List[Module], enabled: FrozenSet[str]) -> Optional[str]`, by the rule table below.
  - `trust_filter(lower: Dict[str, dict], project: Dict[str, object], trusted: FrozenSet[str], config_file: str) -> Tuple[Dict[str, object], List[str]]`: `lower` is the flattened merge of levels 1–3, `project` the flattened level-4 values (`None` for a removal). It returns the level-4 values that stay and the notices (D33). The notice is `rigger: .claude/rigger.json may not set a command for slot <path>; add it to trust.project_commands in <config_file> to allow it`.
  - `resolve_slots(levels: List[Tuple[str, dict]], modules: List[Module], enabled: FrozenSet[str], os_name: str, config_file: str) -> Tuple[List[Row], FrozenSet[str], List[str]]`. `levels` holds `(source, that level's cleaned config)` lowest first, with sources `L1:<file>`, `L2:<module>`, `L3:<file>`, `L4:<file>`. It returns the rows in `SLOT_PATHS` order without the paths whose `requires` is unmet, the trusted list, and the notices (D34, D35), among them one line for each slot whose command comes from the project: `rigger: slot <path> runs the project's command <shown argv> (trusted in <config_file>)` (D53). `<shown argv>` is the argv as one line of ASCII JSON, cut to its first 200 characters, followed by ` … (<n> bytes, sha256:<first 12 hex digits>)`, both taken from the whole JSON line; `slots.ARGV_SHOWN = 200`.
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
  - `test_l4_command_with_trust_applies`: `trust.project_commands: ["reviewer"]` at level 3 gives kind `command`, source `project`, and the one notice of D53. A level-3 command gives no such notice.
  - `test_project_command_notice_is_cut_and_hashed` (D53): for a three-element argv the notice holds the whole JSON, its byte count and 12 hex digits written by hand in the test from `hashlib.sha256`. For 32 arguments of 500 characters the notice is under 400 characters and holds the first 200 characters of the JSON. Two such argvs that differ only in the last character of the last argument give different notices.
  - `test_l4_model_on_an_l3_command_keeps_source_personal` (D53): level 3 sets the command, level 4 sets only `model`, and nothing is trusted. The row is the level-3 argv with the level-4 model and source `personal`.
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
- [ ] **Step 5: Red runs** (full). The trust filter, each branch once: ignore a level-4 `kind`; ignore a level-4 `target`; treat every path as trusted; compare the trusted list with the first path segment only; keep `model` of a dropped value; apply the filter to level 2. Validation: remove each rule of the table once and see its row fail. Fallback: start the walk at level 1. Quiet rule: print a notice for a level-1 default. Null rule: let `"slots": null` through, then `"process": null`. Fillers: report only the first of two. Source: take it from the highest level that set any field (the level-4 model test fails). Notice: drop it; print the whole JSON (the cut test fails on the length); hash only the shown 200 characters (the cut test fails on the two argvs that differ at the end).
- [ ] **Step 6: Commit.** `feat(rigger-core): resolve, validate and trust-filter slots`

### Task 11: The merged table, entry dropping and non-module roots

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/render.py`, `plugins/rigger-core/tools/assemble/discover.py:298-330`
- Test: `tests/test_render.py` and `tests/test_discover.py` in that tool's folder

**Interfaces:**
- Produces:
  - `RenderContext` gains three fields with defaults, so phase 1 callers keep working: `rows: Optional[List[TriggerRow]] = None`, `enabled: Optional[FrozenSet[str]] = None`, `table_seen: Optional[List[str]] = None`.
  - With `rows` set, the first `{{triggers:<id>}}` renders the one table from `rows` and appends the context's `module` to `table_seen` when that is a list. Every later one, in the same `render` call or in a later call that shares the list, renders nothing and is returned in the unknown list as `second trigger table`.
  - `visible(rows: List[TriggerRow], slots: dict, enabled: Optional[FrozenSet[str]]) -> List[Tuple[TriggerRow, List[str]]]`: each row with its resolved entries after D39; `enabled` None drops nothing.
  - `table(rows, slots, enabled=None) -> bytes` uses `visible`.
  - `discover`: a `RIGGER_MODULE_DIRS` entry without `rigger-module.json` whose `.claude-plugin/plugin.json` is an object with a string `name` adds that name to `Discovery.enabled`, with no notice and no module (D39).

- [ ] **Step 1: Write the failing tests.**
  - `test_one_table_holds_the_rows_of_every_section`: two module rows with different `section` values and one config row give one table in that order.
  - `test_second_table_placeholder_renders_nothing_and_is_reported`.
  - `test_entry_of_a_plugin_that_is_not_enabled_is_dropped`: a row loading `x:a` and `sup:b` with `enabled={"x"}` shows `` `x:a` `` alone; a row loading only `sup:b` is gone; a `slot:` entry of a slot missing from `slots` is dropped.
  - `test_callers_without_rows_render_as_in_phase_one`: the three phase 1 table tests still pass unchanged.
  - `test_dirs_entry_with_plugin_json_only_counts_as_enabled` (`test_discover.py`): the name is in `enabled`, `modules` does not hold it, and there is no notice. Rows: no `plugin.json` (today's notice), one that is a list, one whose `name` is a number (today's notice for both).
- [ ] **Step 2: Run both files.** Expected: `FAILED` on the new tests.
- [ ] **Step 3: Implement.** No new placeholder form: `PLACEHOLDER` in `render.py` stays as it is.
- [ ] **Step 4: Run** both files, `test_assemble.py` and `plugins/rigger-core/tools/harness_start/tests/test_harness_start.py`. Expected: `OK` with no change to the last two.
- [ ] **Step 5: Red runs.** Render the table at every placeholder; keep the entries of a plugin that is not enabled; keep an empty row; add a dirs entry without `plugin.json` to `enabled`.
- [ ] **Step 6: Commit.** `feat(rigger-core): one trigger table that drops what is not installed`

### Task 12: `review_scope.py`

**Files:**
- Create: `plugins/rigger-core/tools/review-scope/review_scope.py`
- Test: `plugins/rigger-core/tools/review-scope/tests/test_review_scope.py`

**Interfaces:**
- Produces:
  - `class ScopeError(Exception)`
  - `resolve_commit(repo: Path, rev: str) -> str`: the 40-character commit id. It refuses, before git runs, a `rev` that is empty, starts with `-`, or holds whitespace or a control character; then `git rev-parse --verify <rev>^{commit}`. Only the ids it returns are passed to `git diff`.
  - `scope(repo: Path, base: str, head: str) -> List[Tuple[str, str]]`: `(path, verdict)` sorted by path, the verdict being `review` or `skip: deleted` (D42). This is the one list: the `{scope}` file, the contract's `coverage-scope` rule and `reviewer.md` all use it.
  - `GIT_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames")`, used for the one `--name-status -z` call. Git runs with `-c core.quotepath=off --no-pager`, a 60 s bound, and an environment without `GIT_EXTERNAL_DIFF` and `GIT_DIFF_OPTS`.
  - CLI: `review_scope.py --base REV [--head REV] [--repo DIR]` (`--head` defaults to `HEAD`, `--repo` to the working directory). One line per path, `<verdict><TAB><path>`, then `# review-scope: files <n>, review <r>, skipped <s>`; exit 0. A refusal prints `review-scope: refused: <reason>` and exits 2: a revision that does not resolve, a folder that is not a repository, a git failure, or a path that holds a tab or a line break.

- [ ] **Step 1: Write the failing tests.** The fixture is a temp repository with two commits. The second one edits `a.py`, deletes `b.py`, adds `c.bin` (bytes with a NUL), renames `d.py` to `e.py`, adds `zażółć ą.py` and adds `tests/test_x.py`.
  - `test_lists_every_changed_path_with_its_verdict`: the expected list is written by hand: `a.py` review, `b.py` skip: deleted, `c.bin` review, `d.py` skip: deleted, `e.py` review, `tests/test_x.py` review, `zażółć ą.py` review.
  - `test_a_plain_name_status_would_join_the_rename`: the control for `--no-renames`. A `git diff --name-status` without the flag, on the same two commits, prints a row that starts with `R`; `scope()` holds `d.py` and `e.py` as two rows.
  - `test_a_test_file_is_never_skipped`.
  - `test_revision_that_looks_like_an_option_is_refused` (Review Focus 4): rows `--output=<tmp>/x`, `-p`, the empty string, `a b`, a revision holding a tab, one holding a control character, `no-such-rev` and a tag that points at a blob. Each gives exit 2 with a reason, and `<tmp>/x` does not exist afterwards. For the first six rows `subprocess.run` is patched to fail the test if git is started.
  - `test_drivers_of_the_repository_do_not_run`: the fixture's git config sets `diff.external` and a `textconv` driver to a script that writes a marker file. Control first: a plain `git diff <base> <head>` in that repository writes the marker. Then `scope()` gives the true list and writes no marker.
  - `test_drivers_of_the_environment_do_not_run`: `GIT_EXTERNAL_DIFF` names the marker script and `GIT_DIFF_OPTS` is `--unified=0`, set in the test and restored in `finally`. The same control, then `scope()` writes no marker, and the environment handed to git (read through a patched `subprocess.run` that calls the real one) holds neither name.
  - `test_a_path_with_a_tab_or_a_line_break_refuses` (skipped on Windows with the reason; it runs under WSL and in CI on Linux and macOS): a committed file named with a tab, and one with a line break, each give exit 2.
  - `test_same_commit_gives_zero_files`, `test_not_a_repository_refuses`, `test_cli_lines_and_summary`.
- [ ] **Step 2: Run.** Expected: `FAILED` (no module).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: `OK`, on Windows and under WSL.
- [ ] **Step 5: Red runs** (full, one per branch):
  - drop the leading-`-` refusal (measured above, `git rev-parse --verify` refuses `--output=x` by itself, so the row's own assertion is that git was not started, which the mutant fails);
  - drop the empty check, then the whitespace check, then the control-character check: each fails its own row on "git was started";
  - pass the given revision to `git diff` in place of the resolved id;
  - drop `^{commit}` (the blob tag row fails);
  - drop `--no-renames` (the list test fails on `d.py`);
  - drop the tab check, then the line-break check for paths (the Linux rows fail);
  - keep `GIT_EXTERNAL_DIFF` in the environment, then `GIT_DIFF_OPTS` (the environment test fails on the names handed to git);
  - drop `--no-ext-diff`, then `--no-textconv`. If one of these two stays green because `--name-status` starts no driver, report it as a surviving mutant with the control's output and keep the flag: spec §13 fixes it.
- [ ] **Step 6: Commit.** `feat(rigger-core): review_scope lists what a review must cover`

### Task 13: `slot_check.py`, the contract fixtures and the rule id in a deny

**Files:**
- Create: `plugins/rigger-core/tools/slot-check/slot_check.py`, `plugins/rigger-core/tools/slot-check/tests/test_slot_check.py`, `plugins/rigger-core/tests/contract/{reviewer,worker,implementer,mapper}/`, `plugins/rigger-windows/tests/contract/shell-guard/`, `plugins/rigger-lessons/tests/contract/lessons/`
- Modify: `tests/test_module_contracts.py`; `plugins/rigger-windows/tools/shell-guard/shell_guard.py:3342` (the deny reason), `plugins/rigger-windows/tools/shell-guard/INSTALL.md` (where it shows a reason), and the guard tests Step 1 finds

**Interfaces:**
- Consumes: `review_scope.scope` and `resolve_commit` (Task 12), imported through a `sys.path` entry built from `__file__`; `Module.tests["contract"]` (Task 9).
- Produces:
  - `@dataclass class Verdict: ok: bool; rule: str; reason: str; findings: Optional[int]`
  - `check(slot: str, text: str, repo: Optional[Path] = None, base: Optional[str] = None, head: Optional[str] = None, shape_only: bool = False) -> Verdict`. It never raises: any exception is `Verdict(False, "internal", <type and text>, None)`.
  - `RULES: Dict[str, Tuple[str, ...]]`, the rule ids per slot in checking order, and `GIT_RULES`, the ids that `shape_only` skips.
  - `reviewer_json(text: str) -> object`: the parsed object of a file that is JSON, else of the last fenced `json` block. A text over 1,000,000 characters is refused.
  - CLI: `slot_check.py <slot> <file> [--repo DIR] [--base REV] [--head REV] [--shape-only]`, with the lines and exit codes of D41. Without `--shape-only` the reviewer needs `--base` and `--head`.
  - The guard's deny reason: `shell-guard [<rule>]: <message> (False positive or intended? …)`, the rule from `Finding.rule` of the finding that denied.

  Rule ids:

  | Slot | Rule ids, in order |
  |---|---|
  | `reviewer` | `json` (no object found), `contract` (not `rigger.reviewer/1`), `status`, `finding-shape` (a finding without `id`, `file`, `line`, `severity`, `quote`, `title`; a repeated id; a line that is not a positive integer), `severity`, `path` (see the table below), `coverage-sum`, `skip-reason`, `shortcuts`, then the git rules `base-head` (the JSON's `base` and `head` resolve to other commits than the ones given), `coverage-scope`, `anchor` |
  | `worker` | `status-line`, `summary` |
  | `implementer` | `status-line`, `fields` (`FILES`, `ROOT CAUSE`, `GATE`, `RED RUNS` missing or out of order), `blocked-fields` (`QUESTION`, `CALL`, `OUTPUT`, `ALTERNATIVE` missing with BLOCKED or NEEDS_CONTEXT) |
  | `mapper` | `line-shape`, `words` (over 12 words of text), `not-found-alone`, then the git rule `path-exists` |
  | `shell-guard` | `reply-shape`, `deny-reason`: a deny's reason holds `[<rule id>]`, the id matching `[A-Za-z0-9][A-Za-z0-9._-]*` (D48) |
  | `lessons` | `inbox-line`: the first line matches `^Lessons inbox: .+: (\d+ entries|count unknown \(.+\))$` |

  The `path` rule refuses a finding's `file` on each of these rows, and each row is one fixture and one red run:

  | Row | Refused | Passes |
  |---|---|---|
  | empty | `""` | — |
  | absolute | `/etc/passwd` | `etc/passwd` |
  | parent segment | `../x.py`, `a/../x.py`, `a/..` | `a..b.py`, `a/..b/x.py`, `...py` |
  | backslash | `a\x.py` | — |
  | colon | a path with a drive letter, `a:b.py` | — |

  `..` is compared with whole segments after a split at `/`, never as a substring.

  The anchor rule reads the file at `head` with `git cat-file`, never from the working tree, decodes it as UTF-8 with replacement, and looks for the quote with every run of whitespace turned into one space, in lines `line - 3` to `end_line + 3` (`end_line` defaults to `line`). `coverage-sum` needs `in_scope` to equal `reviewed` plus the skipped files, with no file in both. `coverage-scope` needs `in_scope` to equal the paths of `review_scope.scope`, deleted ones included.

  Fixtures: `pass*` files and one `fail-<rule id>*` file per rule (for `path`, one per refused row). For the reviewer, `repo/base/**` and `repo/head/**` hold two states of a small tree with an edited file, a renamed file, a deleted file and an added binary file; the test builds a two-commit repository from them and replaces the tokens `{base}` and `{head}` in the JSON fixtures with the real ids. `pass-reply.md` holds the JSON as the last fenced block of a Markdown report. `pass-rename-delete.json` lists both names of the rename, the deleted file under `skipped` and the binary file under `reviewed`; `fail-coverage-scope-rename.json` holds the list a plain `git diff --name-status` gives (the new name only).

- [ ] **Step 1: Find the pins of the deny reason, then write the fixtures and the failing tests.** `git grep -n -e "permissionDecisionReason" -e "shell-guard: " -- plugins/rigger-windows tests scripts`: measured for this plan, `tools/shell-guard/tests/test_rules.py:1776`, `:1793` and `:1864` read the reason; the other hits are the `# shell-guard: allow` marker and the test summary line, which do not change. Read each of the three and list the assertions that compare the reason's start.
  - `tests/test_module_contracts.py` `test_every_contract_fixture`: for each manifest and each slot in its `tests.contract`, the folder exists and holds at least one `pass*` file. Every `pass*` file gives `ok`. Every `fail-<rule>` file is refused with exactly that rule id. Every id in `RULES[slot]` has a `fail-` file. The test fails when it read 0 fixtures.
  - The same file, `test_default_fillers_meet_their_contracts`:
    - rigger-windows' PowerShell handler is the `--pwsh-all` command read from its `hooks/hooks.json` (the one handler with no `if` filter), run through `sh`. On a `PowerShell` event whose command is `Get-ChildItem` it prints nothing, which passes `shell-guard`. On one whose command is `git commit --no-verify -m x` it prints a deny (measured for this plan on Windows with Python 3.12.10 and under WSL with 3.10.12: 349 bytes, reason `shell-guard: … skips the repository's hooks …`, from the one finding `('git-no-verify', deny=True)`), so the row runs on every system; the test asserts `permissionDecision` is `deny` (the control that the guard refused) and that the reply passes `shell-guard`, so the reason holds `[git-no-verify]`.
    - `lessons_start.py` run with a scratch inbox of two entries gives a text that passes `lessons` and whose first line ends `: 2 entries`.
    - The rule checker named in rigger-windows' manifest loads and has `RULES` and `rule_guard_problem`.
  - The guard's own tests: the assertions Step 1 listed, changed to the new start, plus one row that pins `shell-guard [git-no-verify]: ` for the command above.
  - `test_slot_check.py`:
    - `test_finding_path_rows` (Review Focus 2): one row per cell of the `path` table. Each refused row gives `path`, with `subprocess.run` patched to fail the test if git is called for that finding; each passing row gets past `path`.
    - `test_anchor_normalises_whitespace_and_line_endings` (Review Focus 2): a CRLF file, a tab-indented line quoted with spaces, a quote spanning two lines; and a file with a byte that is not UTF-8 gives `anchor`, not `internal`.
    - `test_anchor_window`: a quote 3 lines off passes and 4 lines off is refused.
    - `test_the_last_json_block_counts`, `test_no_block_and_a_cut_block_are_refused_as_json`, `test_text_over_the_size_cap_is_refused`.
    - `test_unknown_keys_are_ignored`: a `behaviour_changes` key of any shape, and a key nobody defined, both pass.
    - `test_shape_only_skips_the_git_rules`.
    - `test_an_exception_is_a_refusal`: `check` with a patched rule that raises gives `internal`, and the CLI exits 2.
    - `test_fence_and_whitespace_backtracking_probe`: 40 repeats of a fence opener, and of a space-tab pair, each under 0.5 s.
    - `test_cli_lines_and_exit_codes`.
- [ ] **Step 2: Run** both files and `plugins/rigger-windows/tools/shell-guard/tests/test_rules.py`. Expected: `FAILED`; the guard test fails on the missing `[git-no-verify]`.
- [ ] **Step 3: Implement.** Words are split and walked in code; the only regexes on reply text are the fence opener, the bracketed rule id and the inbox line. In `shell_guard.py` the one changed line builds the reason from `deny.rule` and `deny.message`.
- [ ] **Step 4: Run** both files and every test file under `plugins/rigger-windows/tools/shell-guard/tests/` on Windows, and the first two under WSL. Expected: `OK`, and the contract test's last line names the fixture count.
- [ ] **Step 5: Red runs** (full):
  - remove each rule's check once; its `fail-<rule>` fixture then passes and `test_every_contract_fixture` fails on that file;
  - the `path` rule, one mutant per row: drop the empty check, the absolute check, the backslash check and the colon check, and compare `..` as a substring (the `a..b.py` row fails);
  - read the anchor from the working tree (a test that edits the working copy after the commit fails);
  - make an exception propagate; let the CLI exit 0 on a refusal;
  - delete one `fail-` file (the "every rule has a fixture" assertion fails);
  - put the rule id back out of the guard's reason (the default-filler test fails on `deny-reason`); accept a deny without brackets in `deny-reason` (its `fail-` fixture passes).
- [ ] **Step 6: Sweep and commit.** Sweep the three fixture folders and `INSTALL.md`, expected `findings 0`. Two commits: `feat(rigger-windows): a shell-guard deny names its rule` and `feat(rigger-core): slot_check checks a reply against its slot's contract`.

---

## Pull request 2: running and wiring (waves D–E)

Task 15 runs first, alone (wave D). Task 14 follows it in wave E: its tests write a format-2
snapshot and expect a format-1 file to be refused, and Task 15 sets that format.

### Task 14: `slot_run.py`

**Files:**
- Create: `plugins/rigger-core/tools/slot-run/slot_run.py`
- Modify: `scripts/run_tests.min` (74 to 75)
- Test: `plugins/rigger-core/tools/slot-run/tests/test_slot_run.py`, with `tests/fake_reviewer.py` beside it

**Interfaces:**
- Consumes: `snapshot.read_snapshot`, `snapshot.SNAPSHOT_FORMAT` and `slots.from_snapshot` (Tasks 10 and 15 fix the shape: the snapshot key `slots` holds `as_snapshot`'s value); `review_scope.scope` and `resolve_commit`; `slot_check.check`; the decisions of Q3 and Q4.
- Produces:
  - `strip_env(environ: Mapping[str, str]) -> Dict[str, str]`: without names that start `CLAUDE_` or `HARNESS_` or equal `RIGGER_MODULE_DIRS`, compared in upper case.
  - `fill(argv: List[str], values: Dict[str, str]) -> List[str]`: one pass, the four placeholders only.
  - `resolve_program(name: str, path: str, repo: Path, cwd: Path) -> str`, raising `Refusal`. It returns an absolute path and never asks the operating system to search:
    - a name that holds `/` or `\` must be an absolute path to a file;
    - a bare name is looked up in the entries of `path` in order, skipping an entry that is empty, is not absolute, or whose real path lies inside `repo` or `cwd`; on Windows the candidates are `<name>.exe` and `<name>.com` (the name as given when it already ends with one of them), elsewhere `<name>` with the execute bit;
    - the real path of the result must lie outside `repo` and `cwd`;
    - a result or a name that ends `.cmd` or `.bat`, in any case, is refused; so is a bare name for which only a batch file exists in the searched entries.
  - `Popen` gets that absolute path as element 0, `shell=False` and `cwd=<repository root>`.
  - `TIMEOUT = 900`, `CONSOLE_CAP = 1_000_000`, `OUT_CAP = 5_000_000`.
  - `main(argv: Optional[List[str]] = None, environ: Optional[Mapping[str, str]] = None, stdout=None) -> int`, with the command line, lines and exit codes of D43 (and `--config-dir` if Q3 asks for it).
  - The `{scope}` file: one `<verdict><TAB><path>` line per path of `review_scope.scope()`, in its order.
  - Refusal reasons, each one line after `slot-run: refused: `: `no snapshot for this session; start a new session`, `session id is not usable`, `slot <slot> is <kind>; dispatch its target instead`, `slot <slot> is empty`, `the project's command for <slot> is not trusted`, `<name> not found on PATH (entries inside the repository are not searched)`, `<name> resolves inside the repository`, `the program must be a bare name or an absolute path`, `<name> is a batch file; name the program it starts (for an npm tool: node <script>)`, `nothing to review between <base> and <head>`, `timed out after <n> s`, `more than <n> bytes of console output`, `the command exited <n>`, `the command wrote no output file`, `the output file is over <n> bytes`, and `<rule id>: <reason>` from `slot_check`.

- [ ] **Step 1: Measure the lookup again on this machine.** The plan review ran its probe on Python 3.11.9 and 3.12.10 only. Measured for this plan on 3.12.10 with `NoDefaultCurrentDirectoryInExePath` removed and the planted copy as the only `reviewer-cli`: from the planted folder, `shutil.which("reviewer-cli", path=<system folder>)` returns `.\reviewer-cli.EXE` and a bare-name `Popen` starts the copy; with the parent's working directory elsewhere and only `cwd=` pointing at the folder, `Popen` raises `FileNotFoundError`. Run `q4.py`'s cases 1 and 2 (Task 8) with the Python this task's tests use and paste the output. Expected: the same two results, and the planned lookup returns nothing. Any other result stops the task with the output.
- [ ] **Step 2: Write the failing tests.** Each test writes a format-2 snapshot into a temp config dir and builds a two-commit repository (an edit, a rename, a delete). The command is `[sys.executable, fake_reviewer.py, <mode>, "--out", "{out}", "--scope", "{scope}", "--base", "{base}"]`: a real interpreter named by its absolute path, so no test depends on a shell. `fake_reviewer.py` writes its argv, working directory and environment names to a file, then acts by mode.
  - `test_ok_run_names_the_output_file_and_prints_ok` (mode `ok`): the last line is `slot-run: ok reviewer 1 findings`, exit 0, and the line before names a file that holds the JSON.
  - `test_a_reviewer_that_copies_the_scope_file_passes` (mode `copy-scope`): the fake reviewer builds `coverage` only from the `{scope}` file (every path into `in_scope`, the `review` lines into `reviewed`, the `skip: deleted` lines into `skipped` with that reason). The run prints `ok`, on the fixture with the rename and the delete.
  - `test_refused_output_is_refused` (mode `bad-status`): `slot-run: refused: status: …`, exit 2.
  - `test_program_planted_in_the_repository_is_never_run` (Review Focus 11). `NoDefaultCurrentDirectoryInExePath` is removed from the environment for the test and restored in `finally`. On Windows the planted program is a copy of the system folder's `whoami.exe` named `reviewer-cli.exe` (measured for this plan: the copy starts from a temp folder and exits 0); elsewhere it is an executable script. The test wraps `subprocess.Popen` with a function that records element 0 and the `cwd` it was given and then calls the real one, and the test process's own working directory is set to the repository root, as it is when a session's shell tool starts `slot_run.py`, and restored in `finally`. Rows:
    - planted in the repository root, nothing on `PATH`: `not found on PATH`, and `Popen` was not called;
    - planted in the repository root and a same-named copy in a temp folder that is on `PATH`: element 0 is the absolute path of the `PATH` copy and `cwd` is the repository root; the row asserts nothing about what that program then prints or how it exits;
    - `PATH` holds an empty entry, `.`, and a relative folder that holds the planted file: `not found on PATH`, and `Popen` was not called;
    - `PATH` holds the repository root itself as an absolute entry: `not found on PATH`;
    - the target is the planted file's absolute path: `resolves inside the repository`;
    - the target is `./reviewer-cli` and `sub/reviewer-cli`: `the program must be a bare name or an absolute path`.
    In every row that starts a program, element 0 is an absolute path outside the repository and `cwd` is the repository root. The control runs first: `subprocess.run(["reviewer-cli", "/?"])` from the repository root, with a `PATH` that holds only the system folder, starts the planted copy on Windows (exit 0); on other systems the control is the same call with `.` on `PATH`.
  - `test_batch_file_refuses` (Windows only, skipped elsewhere with the reason): an absolute path to a `.cmd` file, an absolute path ending `.BAT`, and a bare name for which the only file on `PATH` is `<name>.cmd`. Each refuses with the batch reason and starts nothing.
  - `test_timeout_kills_the_tree_and_refuses` (Review Focus 3; mode `hang`, which starts a grandchild and writes its pid): with `TIMEOUT` patched to 2, the refusal prints within 15 s and the grandchild's pid is gone.
  - `test_output_over_the_cap_refuses` (Review Focus 3; mode `flood`, `CONSOLE_CAP` patched to 10,000), and `test_output_file_over_the_cap_refuses` (mode `big-out`, `OUT_CAP` patched to 10,000).
  - `test_environment_is_stripped`: the parent holds `CLAUDE_X`, `claude_y`, `HARNESS_Z`, `RIGGER_MODULE_DIRS`, `RIGGER_OTHER` and `ANTHROPIC_Q`; the child sees only the last two of them, and `PATH`.
  - `test_no_shell_reads_the_arguments`: argv elements `; touch <tmp>/m1`, `$(touch <tmp>/m2)` and `&& x` arrive unchanged in the child's argv file, and neither marker exists.
  - `test_placeholders_are_filled_once`: `{base}` becomes the 40-character id even when `--base HEAD~1` was given; the `{scope}` file equals the lines of `review_scope.scope()`; a value that itself holds `{out}` is not filled again.
  - `test_old_or_missing_snapshot_refuses` (Review Focus 8): no file, a format-1 file, and the ids `../x`, `a/b`, the empty string and one of 300 characters. Each refuses with its reason and the fake reviewer's argv file does not exist.
  - `test_slot_that_is_not_a_command_refuses_with_the_dispatch_hint`, `test_empty_slot_refuses`.
  - `test_untrusted_project_command_in_the_snapshot_refuses`: source `project` with an empty trusted list.
  - `test_a_personal_command_with_a_project_model_runs`: the row a level-3 command with a level-4 `model` gives (source `personal`, D53), with an empty trusted list, runs.
  - `test_the_command_runs_from_the_repository_root`: started from a subfolder.
  - `test_same_base_and_head_refuses`, `test_nonzero_exit_refuses_even_with_a_valid_file`, `test_no_output_file_refuses`.
- [ ] **Step 3: Run.** Expected: `FAILED` (no module).
- [ ] **Step 4: Implement.** Console output goes to a temp file, not a pipe, and the wait loop polls its size. The tree kill follows `scripts/run_tests.py:260-276` (copied, since a plugin cannot import from `scripts/`). The work folder comes from `tempfile.mkdtemp(prefix="rigger-slot-run-")` and is kept, since the caller reads the output file. Set `scripts/run_tests.min` to the number discovery prints (expected 75).
- [ ] **Step 5: Run** on Windows and under WSL. Expected: `OK`.
- [ ] **Step 6: Red runs** (full, one per branch):
  - the lookup: hand the bare name to `Popen` (the planted row fails on its marker); use `shutil.which` (the same row); keep empty entries, then relative entries, then entries inside the repository (each fails its own row); skip the real-path check of the result (the absolute-path row fails); accept a relative name with a separator;
  - batch files: drop the extension check; compare the extension case-sensitively (the `.BAT` row fails); drop the "only a batch file exists" branch (that row reports `not found` and fails on the reason);
  - pass `shell=True`; drop the explicit `cwd` (the subfolder test fails);
  - keep `CLAUDE_*`, then `HARNESS_*`, then `RIGGER_MODULE_DIRS`; compare names case-sensitively (the `claude_y` row fails);
  - skip the session-id check (the `../x` row fails on the reason); skip the trusted check; treat source `personal` as untrusted (the level-4 model test fails); read a format-1 snapshot;
  - kill only the child; drop the console bound; drop `OUT_CAP`;
  - fill placeholders in two passes (the `{out}`-in-a-value row fails); pass `--base` through unresolved; write only the `review` lines into the `{scope}` file (the copy-scope test fails on `coverage-scope`);
  - accept a non-zero exit; skip `slot_check`; print `ok` when `slot_check` refused.
- [ ] **Step 7: Commit.** `feat(rigger-core): slot_run runs a command slot with no shell and checks its output`

### Task 15: Wiring (serial)

**Files:**
- Modify: `plugins/rigger-core/tools/assemble/config.py` (`ACTIVE_ABOVE_L1`, `Resolved`), `assemble.py` (`Inputs`, `assemble`, `_snapshot_data`, `check`), `snapshot.py` (`SNAPSHOT_FORMAT = 2`), `scripts/run_tests.min` (71 to 74)
- Test: `tests/test_config.py`, `tests/test_assemble.py` and `tests/test_snapshot.py` in the assembler's folder; `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py:362-376` (one assertion)

**Interfaces:**
- Consumes: everything Tasks 9–12 produce; Q3's command-line form.
- Produces:
  - `config.ACTIVE_ABOVE_L1 = frozenset(("schema", "triggers", "expects", "slots", "trust"))`
  - `Resolved.levels: List[Tuple[str, dict]]`: `(source, cleaned data)` per level, lowest first, level-2 conflicts already removed. `Resolved.effective["slots"]` is now the plain merge of all levels, before the trust filter: nothing but `slots.resolve_slots` may read slots from it.
  - `Inputs.session_id: str = ""`, set from the hook input and from `--session-id`.
  - `Assembly.slot_rows: List[slots.Row]`
  - Snapshot: `"slots": slots.as_snapshot(rows, trusted)`.
  - The block `("<core root>", slots.table(rows, command_line), "slots.slots")`, main audience only, after the last block of rigger-core's rules files, registered in the id index before the personal layer is placed (D36).
  - `--check` findings: `slot <path>: <reason>` for a level-1 default that fails `entry_problem` against the checked modules (a plugin outside the set is external, as in D18); `second trigger table in <file>`; `{{slot:<path>}} in <file> names a slot an absent plugin fills` when that slot's level-1 default plugin is outside the checked set (D37); `trigger <id> loads process.tdd without testing before it` (D47).

- [ ] **Step 1: Write the failing tests.**
  - `test_config.py`: `test_slots_and_trust_above_level_one_apply` replaces `test_slots_above_level_one_are_recorded_not_applied`; `review`, `shell_guard`, `company` and `leak_guard` stay in `inactive`. `test_levels_are_kept_in_order`.
  - `test_assemble.py`:
    - `test_slots_section_closes_core_and_is_main_only`, `test_slots_section_for_the_default_layout_is_under_1200_bytes`.
    - `test_a_personal_section_cannot_replace_slots` (the duplicate notice), `test_drop_by_heading_drops_slots`.
    - `test_default_install_without_superpowers_is_quiet` (Review Focus 5): no notice; the testing row's cell is `` `rigger-core:testing` ``; no row loads a `superpowers:` skill; and the four `process.*` rows show `—`.
    - `test_a_personal_slot_changes_the_row_the_cell_and_the_placeholder`: level 3 sets `slots.testing.target` to a skill of an enabled stub plugin; the `## Slots` row, the trigger cell and a `{{slot:testing}}` in a fixture rules file all show it.
    - `test_project_command_without_trust_prints_the_line_and_keeps_the_agent`, through real files in a temp project.
    - `test_trusted_project_command_prints_its_argv` (D53): the notice of D53 comes at `startup` and again after `compact`, and a second project file with another argv gives another text.
    - `test_project_command_notice_stays_inside_the_part_budget` (D53): a trusted project command of 32 arguments of 500 characters, the most validation allows. Part 1's emitted output, counted in bytes as the handler writes it, is under 9,500, and it still holds part 1's first rules section. Run it red first with the notice built from the whole JSON: it fails on the byte count.
    - `test_two_fillers_line_at_startup_and_after_compact` (Review Focus 6).
    - `test_format_one_snapshot_is_read_again` (Review Focus 8): a format-1 file, then `compact`; the output starts with `rigger: snapshot missing; rules read again`, and the file is format 2 afterwards.
    - `test_compact_without_a_snapshot_applies_the_trust_filter_again`.
    - `test_every_part_packs_the_same_text_with_slots`: the six parts run separately join to `--render`'s text.
    - `test_command_line_holds_the_session_id`, and `--render` without one prints `<session id>`.
    - `test_check_findings`: one planted fixture per new `--check` finding, each expecting its exact line, and a fixture without any gives `findings 0`.
  - `test_doctor_sh_plugins.py` `test_keys_without_effect_give_config_inactive_rows`: the expected rows lose `config-inactive slots.reviewer.model` and keep `config-inactive leak_guard.denylist`. Task 18 owns the rest of that file.
  - `test_snapshot.py`: the format constant is 2 and a format-1 file reads as None.
- [ ] **Step 2: Run the three files.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** `assemble()` calls `slots.resolve_slots(resolved.levels, modules, discovery.enabled, _this_os(), <level-3 path>)`, adds its notices, and passes `slots.slot_targets(rows)` wherever `slots` was used (`_requires_met`, `RenderContext.slots`). It builds each `RenderContext` with `rows` and `enabled`, shares one `table_seen` list across the assembly, drops the `NO_TABLE` notice and reports a second table as a notice. Then set `scripts/run_tests.min` to the number `python -I -c "import sys; sys.path.insert(0, 'scripts'); import run_tests; print(len(run_tests.discover(run_tests.ROOT)))"` prints (expected 74: 71 plus `test_slots.py`, `test_review_scope.py` and `test_slot_check.py`; Task 14 adds the 75th).
- [ ] **Step 4: Run** every test file under `plugins/rigger-core/tools/assemble/tests/` and under `plugins/rigger-core/tools/doctor/tests/`, `tests/test_module_contracts.py` and `test_harness_start.py`. Expected: `OK`. `assemble.py --check` on the repository is expected to print four findings until Task 16: `second trigger table` for `windows-skills.md` and for `wp-skills.md`, and the `{{slot:…}}` finding twice for `plans-main.md`. Two existing tests run `--check` on the repository and are red on exactly those until Task 16: `test_check_repo_is_clean` (`test_assemble.py:901`) and the check of `tests/test_module_contracts.py` (`run_check`, `:27`). Paste the findings; a fifth one stops the task. The golden test and `test_plugin_rules.py` are also expected to fail here, and only on these: the new `## Slots` piece, the three table pieces (the first placeholder now holds every row and the other two render nothing), and the two `second trigger table` notices. Task 16 owns them. Paste the failing piece names in the report; any other red piece stops the task.
- [ ] **Step 5: Red runs.** Full, for the trust wiring: remove the `resolve_slots` call's trust step by passing an all-trusting list (the project-command test fails); read slots from `Resolved.effective` (the same test fails); drop the notices `resolve_slots` returns (the argv test fails); print the whole argv in the notice (the part-budget test fails on the byte count). Others, one each: keep `slots` out of `ACTIVE_ABOVE_L1` (the doctor assertion fails); leave `SNAPSHOT_FORMAT` at 1; place `## Slots` in the subagent audience; register its id after the personal layer; keep `rows_by_section` mode (the default-install test fails on a `superpowers:` cell).
- [ ] **Step 6: Commit.** `feat(rigger-core): the assembler resolves slots and prints the Slots table`

---

## Pull request 2: the cutover and what reads it (waves E–F)

### Task 16: Cutover — one table, slot wording, the reviewer contract ending (serial)

**Files:**
- Modify:
  - `plugins/rigger-rules/rules/skills.md` moves with `git mv` to `plugins/rigger-core/rules/skills.md`. New body: the heading, a blank line, `- Load the skill before the first step of its kind:`, a blank line, `{{triggers:skills.skill-triggers}}`.
  - `plugins/rigger-core/rigger-module.json` (add `{"file": "rules/skills.md", "audience": "both"}` after `state-file.md`), `plugins/rigger-rules/rigger-module.json` (remove it).
  - `plugins/rigger-windows/rules/windows-skills.md` is deleted with its manifest entry.
  - `plugins/rigger-wp/rules/wp-skills.md`: heading `## WordPress site copies`, then today's lines 3–4, and no placeholder.
  - `plugins/rigger-rules/rules/plans-main.md:3-4` becomes
    `- A brief or full requirements never waive brainstorming or planning (load the brainstorm and` / `  plans skills the Skill triggers table names), the thinking pass (goal, reader,` (D37: no placeholder).
  - `plugins/rigger-core/agents/worker.md:8-10`: "… and any domain skill your task needs. Load the skills the Skill triggers table names for writing code or tests and for a bug or failing test, each before that step. Never load a brainstorming, planning or dispatching skill."
  - `plugins/rigger-core/agents/reviewer.md`:
    - line 17: "(procedure in the testing skill the Skill triggers table names)";
    - one sentence on reading diffs: "Read a diff with `git --no-pager diff --no-ext-diff --no-textconv --no-color <base>..<head>`, a large one written with `--output=<file in the scratch folder>`.";
    - one sentence on behaviour: "For each change, name every existing caller, hook or user flow that now behaves differently, as before → after with `file:line`, under a `Behaviour changes` heading of the report; an intended change is reported there and is not a finding." The JSON gets no key for it (D49);
    - the contract ending: "End the reply with one fenced `json` block holding the `rigger.reviewer/1` object: `status`, `base`, `head`, `findings` (each with `id`, `file`, `line`, `end_line`, `severity`, a `quote` copied from the file, `title`, `detail`, `pre_existing`, `evidence`), `coverage` (`in_scope`: every path printed by `tools/review-scope/review_scope.py --base <base> --head <head>` of the rigger-core plugin, whose root the rules' `Plugin root:` line gives, or, where that cannot run, by `git diff --no-renames --name-status <base>..<head>`, so a rename counts as its old and its new path; `reviewed`; `skipped`, each with a `reason`), `shortcuts` and `shortcuts_check`. A changed file that is neither reviewed nor skipped makes the status PARTIAL." The `SHORTCUTS:` block stays.
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
  - `plugins/rigger-rules/tests/test_plugin_rules.py`: `SkillTableTests`, `TestingSkillTriggerTests`, `RULES_CEILING_BYTES`, the count of table placeholders, and `test_the_brainstorming_route_comes_from_the_process_slots` (`:409`), which becomes a test that `plans-main.md` holds no `{{slot:` and names the table.
  - The five rules-file counts, 19 to 18: `tests/test_repo.py:130`, `plugins/rigger-core/tools/assemble/tests/test_sections.py:69`, `plugins/rigger-rules/tests/test_rules_hooks.py:318`, `RULES_MIN_FILES` (`test_plugin_rules.py:38`) and the pattern `rules files (1[9]|[2-9]\d)` of `test_check_repo_is_clean` (`test_assemble.py:905`). A phrase search does not find these.
  - The golden test's subagent root-line set and its per-case `## Slots` piece (D40).
  - `plugins/rigger-core/tools/coord-state/tests/test_coordinate_wiring.py:99`, `plugins/rigger-core/tests/test_core_wiring.py`, `plugins/rigger-rules/tests/test_reinject.py:117`, `plugins/rigger-wp/tests/test_outbound_block.py`, and the drift-lint baseline.

**Interfaces:**
- Consumes: Tasks 9–15 merged into the branch.
- Produces: the behaviour changes 1–5.

- [ ] **Step 1: Baseline.** Run the golden test, `test_plugin_rules.py`, `test_assemble.py` and `tests/test_module_contracts.py`. Expected: red only on what Task 15 reported (`## Slots`, the three tables, the two notices, and the four `--check` findings in the last two files).
- [ ] **Step 2: Find every pin.** For each old phrase above, `git grep -n -F "<phrase>" -- '*.py' '*.ps1' '*.json'`, and add any test not listed to this task's report before editing. For the counts, `git grep -n -E "\b19\b" -- 'tests/*.py' 'plugins/*/tests/*.py' 'plugins/*/tools/*/tests/*.py'` and read each hit: the five above count rules files; `test_reinject.py:117` and `test_stub_session.py` count main-session sections, which stay 19 (rigger-windows loses one, rigger-core gains `## Slots`; its `## Skill triggers` only moves) and are recounted from `--render`; `test_assembly_golden.py:402` counts the frozen baseline's handlers and does not change.
- [ ] **Step 3: Rewrite the tests first.**
  - `test_merged_table_holds_every_row_of_the_three_tables` (golden file): the expected list is the 13 row lines cut from the three frozen files under `tests/fixtures/golden-34e7713/` (8, 3 and 2, asserted). With all seven modules and the stub, the merged table's rows equal that list as a multiset, for the main session and for a `general-purpose` subagent. A second assertion pins the order of D38: the fixture's five rigger-core rows, the three windows rows, the two wp rows, then the fixture's three slot rows.
  - `test_overrides_of_merged_table_sections` (Review Focus 7): a `rules.d/skills.md` with `## Skill triggers` replaces the table and prints `rigger: rules.d/skills.md replaces rule skills.skill-triggers`; a `rules.d/windows-skills.md` with the old heading prints as a personal section.
  - `test_every_other_piece_is_unchanged`: with `ALLOWED` applied, each scenario's normalised output equals the baseline's. The test also asserts that every entry of `ALLOWED` was used at least once in the run.
  - `SkillTableTests`: every skill folder of rigger-core, rigger-windows and rigger-wp appears in the one rendered table exactly once by its installed name; exactly one rules file in the repository holds a `{{triggers:…}}` line.
  - `TestingSkillTriggerTests`: in the row data (manifests plus `rigger-defaults.json`), every row whose `load` has `slot:process.tdd` has `slot:testing` before it; the rendered cell with the stub enabled is today's; and `worker.md` holds the new sentence.
- [ ] **Step 4: Run them.** Expected: `FAILED` on the table shape and the wording.
- [ ] **Step 5: Edit** the rules, manifests and texts above with the Edit tool, one hit each unless stated.
- [ ] **Step 6: Set the ceiling.** Print the rendered rules-file total the way `RulesSizeTests` sums it, without the `slots.slots` block. Set `RULES_CEILING_BYTES` to that number. If it is over 27,708, stop and report (D50). Add `SLOTS_CEILING_BYTES = 1200` for the `slots.slots` block.
- [ ] **Step 7: Run** the golden test, `test_plugin_rules.py`, `test_drift_lint.py`, `test_coordinate_wiring.py`, `test_core_wiring.py`, `tests/test_repo.py`, `tests/test_module_contracts.py`, `tests/test_plugin_refs.py`, the rigger-wp tests and `test_stub_session.py` and `test_reinject.py` locally with `claude`. Expected: all `OK`; the two stub files report the new section count taken from `--render`. Then `assemble.py --check` on the repository: expected `findings 0` (the four of Task 15 are gone).
- [ ] **Step 8: Red runs.** Leave the old placeholder in `wp-skills.md` (the second-table check and the equality test fail); put one `{{slot:process.plans}}` back into `plans-main.md` (its test and `--check` fail); delete one windows row from its manifest (the equality test fails on the multiset); remove the stub from the layout (it fails on the three slot rows); swap two module blocks in `collect_rows` (the order assertion fails); remove one entry's effect from `ALLOWED` (its piece fails); add an unused entry to `ALLOWED` (the "used" assertion fails).
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
- Produces: the proof of exit criterion 2, labelled "stub". It proves that the refusal line reaches the session as a tool result; that a model then treats it as a blocked review is not provable with a stub and is stated so in the pull request body (open risk 2).

- [ ] **Step 1: Write the tests.** The scratch config's `rigger/config.json` sets `slots.reviewer` to the fake reviewer command (level 3, so no trust entry is needed).
  - `test_the_slots_section_reaches_the_first_request`: the probe request holds `## Slots`, a `reviewer` row of kind `command` and the command line with this session's id.
  - `test_a_stub_command_reviewer_is_run_and_a_refused_output_is_blocked`: the stub cuts the command line out of the probe request and answers with a shell tool_use that runs it with `--base <id of the fixture's first commit>`. With the fake reviewer in mode `bad-status`, the next request's `tool_result` ends with `slot-run: refused: status: …`, and the fake reviewer's argv file exists (the control that `slot_run.py` started it). With mode `ok`, it ends with `slot-run: ok reviewer 1 findings`. If Q3 found no working form, the test starts `slot_run.py` itself with the session's id, and its docstring says the model-side call is not proved.
  - `test_project_command_without_trust_never_runs`: the same command set in the project's `.claude/rigger.json` with no trust entry. The probe request holds the trust notice and an agent `reviewer` row, and a direct `slot_run.py reviewer --session <id>` refuses with the dispatch hint. A second session with `trust.project_commands: ["reviewer"]` in the scratch config holds the notice of D53 with its hash.
- [ ] **Step 2: Run** the file locally with `claude`. Expected: `OK`, 5 tests (2 from phase 1). The report says "stub" and gives the CLI version.
- [ ] **Step 3: Red runs.** Make `slot_run.py` print `ok` on a refusal (the second test fails on the `bad-status` case); remove the trust step (the third test fails on the notice); drop the D53 notice (it fails on the second session).
- [ ] **Step 4: Commit.** `test(rigger-core): stub session runs a command reviewer and blocks a refused output`

### Task 20: The raw readers of rules text

**Files:**
- Modify, only where Step 2 shows a difference: `plugins/rigger-windows/tools/drift-lint/drift_lint.py`, `plugins/rigger-lessons/tools/proposal_check.py`, `plugins/rigger-lessons/tools/batch_model.py`, and `plugins/rigger-lessons/skills/curate-lessons/SKILL.md` (how the rendered text is passed)
- Test: their existing test files

**Interfaces:**
- Produces: for each tool, either a test that pins its result on rules text with placeholders, or an input `--rules-text <file>` that takes the output of `assemble.py --render`.

- [ ] **Step 1: List what each tool reads.** `git grep -n "rules" -- <the three files>`; record the globs (`proposal_check.py:98`, `batch_model.py:121-123`, `drift_lint.py:391-399`).
- [ ] **Step 2: Measure.** Run each tool's rules-reading step on the repository twice: on the raw files, and on a temp copy whose rules files hold the rendered sections of `assemble.py --render`. Diff the two results and paste the diff.
- [ ] **Step 3: Decide per tool.** No difference: add one test row with a `{{triggers:…}}` line and a `{{skill:…}}` placeholder that pins today's result. A difference: add `--rules-text`, a failing test for the case the diff showed, and the skill sentence that passes the file.
- [ ] **Step 4: Run** the three test files, `test_drift_lint.py` and `test_plugin_rules.py`. Expected: `OK`.
- [ ] **Step 5: Red run** per new test, named in the report.
- [ ] **Step 6: Commit.** `test: rules readers outside the assembler handle placeholders`, or `feat:` with the tool named when an input was added.

---

## Docs, gate and pull request (wave G)

### Task 21: Docs, release notes and migration

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), `plugins/rigger-core/README.md` (slots, `## Slots`, the three tools, `trust.project_commands`, the caps, doctor rows), `plugins/rigger-rules/README.md` (the table moved), `plugins/rigger-wp/README.md`, `plugins/rigger-windows/README.md`, `CONTRIBUTING.md` (contract fixtures: where they live and that every rule needs a `fail-` file), `plugins/rigger-windows/tools/shell-guard/INSTALL.md` only if Task 13 left a reason example unchanged, and `docs/superpowers/specs/2026-11-23-modular-architecture-design.md` (the rows of "Spec corrections")

- [ ] **Step 1: Write the Unreleased entries:** the ten behaviour changes; the migration of spec §13 (update the plugins; a table copied into `rules.d` moves to `triggers`, and doctor prints the JSON); the two changed section ids; that an override of `slots.slots` is refused; the contract `rigger.reviewer/1`; and Q1–Q4's decisions in one line each. Three entries a person acts on get their own lines:
  - a command slot's program is found on `PATH` outside the repository and is never a `.cmd` or `.bat` file, so an npm tool is named as `node <script>` and a tool inside the repository as `<interpreter> <script>`, with the refusal lines quoted;
  - a shell-guard deny now starts `shell-guard [<rule>]:`, for anyone who matches on the old start;
  - a session prints a trusted project's command, cut to 200 characters with its size and a hash;
  - `--changed-modules` is a local pre-check, and the full gate is still required.
- [ ] **Step 2: Apply "Spec corrections".** Edit the spec file row by row with the Edit tool; each row of the table is one edit, and the commit body lists the fifteen.
- [ ] **Step 3: Find stale text.** `git grep -n -e "windows-skills" -e "three tables" -e "config-inactive" -e "no effect in this release" -- '*.md' ':!docs/superpowers'`, and correct each hit.
- [ ] **Step 4: Sweep** the changed files. Expected: `findings 0`.
- [ ] **Step 5: Commit.** `docs: phase 2 slots, contracts and the merged trigger table`

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
- [ ] **Step 4:** Push `feature/modular-phase-2` and open the pull request to `main`. The body covers the spike decisions, the behaviour changes, the gate pass line, the mutation list per task with every surviving mutant and its reason, the "Spec corrections" table, the shell guard's measured latency (254–399 ms against the spec's 50 ms), the note that "reported as blocked" is proved up to the tool result only, and the report-shadow summary line from a real session if one was run.
- [ ] **Step 5:** Wait for CI on the three systems. Merging is the owner's step.

---

## Review ideas left for phase 3

Phase 2 took the coverage report, the hardened diff flags and the behaviour-change question as one
sentence of `reviewer.md` (D49). The rest belong to the `rigger-REVIEW` module, one line each:

- **Strict finding dismissal:** the orchestrator may mark a finding `disputed` only with a quoted line that proves it wrong (the described code is not there, or the line says the opposite); protected topics (concurrency, bounds, behaviour or compatibility changes, authorisation, money, data) need a fresh reviewer first. Phase 2's quote anchor already rejects a finding about code that is not there.
- **Per-path review rules:** `review.paths` maps globs to rules files that the review skill loads only for matching files (spec §13 phase 3).
- **Per-language checklists:** `checklists/<language>.md`, loaded by the extensions in scope, without what the linters and `defects.md` already cover.
- **More skip reasons in `review_scope.py`:** binary, generated, vendored and lockfile, never test files. Each new reason changes the one list, so the `{scope}` file, the contract and `reviewer.md` change together.
- **A second round for full-risk changes:** a fresh reviewer gets the first one's findings and looks for others; it changes the one-review rule, so it needs the owner's decision.
- **A "read by" map for `--changed-modules`** (not a review idea; it belongs to phase 3's first module-only pull request): each test file that names `plugins/<name>` or joins a plugin folder with `"<name>"` becomes a reader of `<name>`, so a change to `<name>` runs it. The known readers are rigger-core's two stub tests (the `stub_api.py` import), its doctor tests (copies of rigger-rules and rigger-lessons) and rigger-rules' `test_core_check.py` (a copy of rigger-windows). With the map, one classify row and one mutant per reader, the mode can become the gate that spec §12 describes.
- **A risk list before the review** for large diffs: one sentence in the review skill, with its effect measured first.
- **Splitting a very large diff into groups** reviewed by separate agents: only as an orchestrator decision, and it multiplies usage.

Not planned for any phase: a `behaviour_changes` key in `rigger.reviewer/1` (ruling 4), and a
conditional construct in rules text (D37).

## Estimate

| Part | Agent hours | Code lines | Test lines |
|---|---|---|---|
| `--changed-modules` (Task 1) | 2.75 | 175 | 300 |
| Spikes Q1–Q4 (Tasks 2, 6, 7, 8) | 4.25 | 0 in the repository (about 400 in scratch) | — |
| Phase 1 backlog (Tasks 3, 4, 5) | 2.25 | 60 | 190 |
| Manifest fields and slot resolution (Tasks 9, 10) | 4.75 | 360 (`slots.py` 310, manifest 50) | 550 |
| Merged table, discovery (Task 11) | 1.25 | 60 | 120 |
| `review_scope.py` (Task 12) | 1.25 | 100 | 190 |
| `slot_check.py`, fixtures, the guard's reason (Task 13) | 5 | 400 | 400, plus about 450 lines of fixture files |
| `slot_run.py` (Task 14) | 3.5 | 260 | 440 |
| Wiring (Task 15) | 2.75 | 150 | 300 |
| Cutover (Task 16) | 4 | 90 (rules, manifests and wording) | 380 |
| Report shadow, doctor (Tasks 17, 18) | 3 | 170 | 330 |
| Stub run, raw readers (Tasks 19, 20) | 2.75 | 30 | 240 |
| Docs, gate, two pull requests (Tasks 21, 22, and the gate step of Task 5) | 2.25 | — | — |
| **Total** | **39.75** | **1,855** | **3,440** |

Revision 1 against the first draft (42.5 h, 1,960 and 3,600 lines): the cuts took out the watchdog,
the `{{if}}` construct, the binary pass, the `behaviour_changes` rule and one pull request; the
review's fixes added the program lookup with its tests, the rule id in the guard's deny, the
project-command notice and about twenty named red runs.

Against the spec's 12 h, 800 and 1,100 lines, the differences are:
- work the spec's phase 2 does not hold: `--changed-modules` (2.75 h), the phase 1 backlog with Q1 (3.25 h) and the raw readers (1 h);
- the three other spikes (3.25 h), which the spec's figure did not price, as in phase 1;
- the contracts: six slots with a fixture per rule, about 30 rules, where the spec priced the reviewer alone;
- the trust filter by provenance, the fallback walk, the filler rules and the program lookup, which the spec gives one line each or none;
- the cutover's golden rework with a named list of allowed differences;
- two pull requests, each with a full gate.

The spec-scope part alone (Tasks 6–19, 21 and 22) is about 32 h, 1,600 code lines and 2,900 test
lines: 2.7 times the spec's hours. Phase 1 ran at 1.8 times.

**Simpler version, about 24.5 h, 1,100 code lines, 2,100 test lines:**
- **What it keeps:** `--changed-modules`; the phase 1 backlog with spike Q1; slots of kind `skill`,
  `agent` and `hook` with validation, the fallback walk and the `## Slots` table; the merged table
  with row dropping; the rewording; the worker, implementer, mapper, shell-guard and lessons
  contracts with their fixtures, and the reviewer contract's shape rules; doctor rows.
- **What it drops, with what each saves:**
  - the `command` kind: `slot_run.py` (3.5 h, 260 and 440 lines), spikes Q3 and Q4 (2.25 h), the
    trust filter and the project-command notice (1.25 h, 80 and 150 lines; a project file can then
    only name agents and skills, and `kind: command` is refused at every level), their wiring
    (0.5 h) and the command half of Task 19 (1.75 h, 20 and 180 lines);
  - `review_scope.py` (1.25 h, 100 and 190 lines) and the git rules of the reviewer contract (2 h,
    180 and 150 lines), so coverage and anchors are not checked: the reviewer contract is shape
    only;
  - the report shadow's contract column and spike Q2 (2.5 h, 85 and 170 lines).
- It still meets "one table" and "validated fillers", but not exit criterion 2 (the stub command
  run) and not spec goal "swap the reviewer for an external tool". It saves about 15 h. The first
  draft put the saving at 21 h; counted part by part it is 15.

## Open risks

1. **Q3 may find no way for a `claude -p` session to run a stub-scripted shell command.** Exit
   criterion 2 is then proved with `slot_run.py` started by the test and the session's real
   snapshot, and the model-side call stays unproved until a manual run.
2. **"Reported as blocked" is proved only up to the tool result.** Task 19 shows that the
   `slot-run: refused` line reaches the session; what a model does with it rests on one bullet of
   the orchestrating skill, which no stub can test.
3. **The shell-guard contract has no latency bound** (D48). The default filler measured 254–399 ms
   against the spec's 50 ms; the bound is restated for phase 3's filler.
4. **`slot_run.py` keeps `ANTHROPIC_*` in the command's environment** (D43). An external reviewer
   named by the person or by a trusted project sees those variables. Removing them would break a
   tool that needs its own key; the trust filter is the control.
5. **A snapshot is trusted as the person's own file.** A process that can write
   `<config dir>/rigger/state/` can make `slot_run.py` run a command. That folder has the same
   protection as `settings.json`, which can already run hooks.
6. **A trusted project command is trusted for every later version of that repository's file.** The
   trust entry names a slot, not an argv. The notice of D53 shows the argv's first 200 characters and a hash of all of it in every session
   that would run it, and a changed argv is a changed notice. Pinning the argv hash in
   `trust.project_commands` is a possible phase 4 step; it is not in the spec.
7. **The program lookup refuses setups a person may have.** A reviewer installed only as an npm
   shim, or only inside the repository (a virtual environment in the working tree), is refused and
   must be named as `node <script>` or `<interpreter outside the repository> <script>`. The refusal
   line says so, and Task 21 documents it.
8. **macOS is not measured in Q4** and has no local leg here. CI runs Task 14's tests there, and a
   failure in the tree kill or the program lookup would first show in CI.
9. **The golden test's `ALLOWED` list could hide a real difference** in the pieces it names. Each
   entry is a whole expected piece, not a wildcard, and the "used at least once" assertion stops a
   stale entry from staying.
10. **The merged table changes row order** (D38). A person who learned the old order sees the slot
    rows last. No rule depends on the order.
11. **A rules part killed by the 10 s hook timeout stays silent** (D29). The lost-part line covers a
    part that fails or prints nothing, not one that never returns. Measured headroom today is
    0.55 s of 10 s. A person sees it through `/rigger-core:doctor`, as in phase 1.
12. **`--changed-modules` is a pre-check, not a gate, until phase 3** (D26). Every phase 2 change is
    shared or in rigger-core, and four test files read another module without a dependency, so the
    mode can pass where the gate fails. Its tests run on a fixture repository.
13. **Two rule checks duplicate `report_shadow.py`'s own `classify` and `has_summary`** (the worker
    contract's `status-line` and `summary`). Task 13 may import them from `report_shadow.py`
    instead of writing them again, as long as the fixtures stay the proof.
