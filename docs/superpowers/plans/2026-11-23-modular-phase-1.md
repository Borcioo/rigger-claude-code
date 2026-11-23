# Modular architecture, phase 1: manifest, assembly and config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One assembler in rigger-core prints every Rigger rules section from module manifests and a
four-level config, and with no config file its output equals today's, section for section.

**Architecture:** A small Python package `plugins/rigger-core/tools/assemble/`. It splits rules into
sections with ids, discovers modules (enabled plugins with `rigger-module.json`, plus
`RIGGER_MODULE_DIRS`), resolves config levels 1–4 and renders placeholders and trigger tables. It
packs the text into K fixed part handlers of at most 9,000 bytes each and writes a per-session
snapshot that `compact` reuses. The rules handlers of five plugins are removed in one cutover
commit. A golden test runs a frozen copy of today's handlers next to the assembler on the same
layouts.

**Tech Stack:** Python 3.9+ stdlib only (run through `bin/py.sh` with `-I -X utf8`), POSIX `sh`
launchers, `unittest`, `plugins/rigger-rules/tests/stub_api.py` for `claude -p` runs.

**Spec:** `docs/superpowers/specs/2026-11-23-modular-architecture-design.md`. This plan covers phase
1 (§13 "Phase 1") with §6, §7, §8, §9 and §12 as far as phase 1 needs them. Phases 2–5 get their own
plans.

## Global Constraints

**Scope and promise**
- Phase 1 promises no behaviour change with no config file. The golden test (Task 15) is the
  measure. The only allowed differences are the ones listed in spec §13 "Exceptions": Python
  required for rules, `dependencies` on rigger-core, part handlers replacing the 19 rules handlers,
  and the accidental-override notice. This plan adds one, named in the release notes: the lone
  root line of D1.
- Base: `34e7713` (release 0.4.0). Executors branch from current `main` (`origin/main` is one commit
  ahead, at `403eed0`, which adds `.github/workflows/test.yml` and touches no rules or hooks file).
  Branch `feature/modular-phase-1`, PR to `main`. Rigger has no `develop`. Merging the PR is the
  owner's step.
- Everything under `plugins/`, `scripts/`, `tests/` and `docs/` is public. No company, client,
  person or private repository names, no absolute local paths. Examples stay generic.
- Code, identifiers, comments and commit messages are in English. Conventional Commits, with no
  attribution lines.

**Runtime**
- Python floor is 3.9, the floor `py.sh` accepts. Use no `match`, no `X | Y` type unions evaluated
  at runtime (`from __future__ import annotations` is fine) and no 3.10+ stdlib calls.
- Hooks run `py.sh` with `-I -X utf8`. `-I` implies `-P` on 3.11+, so the script folder is not on
  `sys.path`. `assemble.py` inserts its own folder first, as `compact_guard.py:354` does.
- Hook output is written as LF bytes through `sys.stdout.buffer`, never `print`. A Windows Python
  writing to a pipe emits CRLF, and Claude Code's 10,000-character cap counts CR bytes.
- Each part's body is at most 9,000 bytes, counted as `rules_extra.sh:67,93` counts today: the text
  as written, JSON-escaped in `--subagent` mode, without the fixed JSON wrapper. A test asserts the
  emitted handler output (body plus wrapper) stays under 9,500 bytes, below the 10,000-character cap.
- Launchers stay POSIX `sh` (dash on Linux, Git Bash `sh.exe` on Windows), with no bash syntax.
  Every hook handler always exits 0.
- Files are read as bytes and decoded as UTF-8 explicitly. Every write passes `encoding="utf-8"` or
  writes bytes.
- A plugin never reaches a sibling by relative path. Modules are found through
  `installed_plugins.json` `installPath` (or the S3 decision), `RIGGER_MODULE_DIRS` or the
  assembler's own `CLAUDE_PLUGIN_ROOT`.
- `bin/py.sh` is not changed. If a task needs it changed, every copy changes in one commit
  (`tests/test_repo.py` checks).

**Tests and runs**
- Tests are `unittest` files that `scripts/run_tests.py` discovers. The golden test and all
  assembler unit tests need no `claude`, so CI runs them on Linux (gate), macOS and Windows
  (`run_tests.py --no-claude`).
- Every new test gets one red run on its own assertion, named in the task's report. Security- and
  integrity-relevant checks (the shape of trigger rows from a project file, snapshot path,
  fail-closed visibility of missing rules) are broken once per branch.
- `run_tests.py` strips `CLAUDE_*` and `HARNESS_*`. From Task 16 it also clears
  `RIGGER_MODULE_DIRS`, and only that name (spec §12): `RIGGER_RULES_ROOT`, `RIGGER_COORDINATE_MD`
  and `RIGGER_CLI_HOST_MD` are red-run knobs of existing tests and must keep passing through. A
  test that needs `RIGGER_MODULE_DIRS` sets it itself.
- A test that starts `pwsh` in a fake home sets `POWERSHELL_TELEMETRY_OPTOUT=1`. A test that fakes
  the home for a Python child sets `HOME` alone: Store Python derives `sys.executable` from
  `USERPROFILE`, so faking it breaks the interpreter. Only a `claude` child gets both, as
  `test_reinject.py` does.
- Git Bash `sh.exe` started from Python re-parses its command line. Tests pass no quote or glob
  characters as arguments; they put them inside an `sh -c` string.
- A test that runs git on the real checkout under WSL sets `GIT_DIR`/`GIT_WORK_TREE` as
  `tests/test_repo.py` `git_env()` does.
- Gate (local): `python -I scripts/gate.py` in the background. On Windows, start it with the full
  path of the installed Python 3.12 and read the `status.txt` that its first line names. Between
  fixes, `python -I scripts/run_tests.py --fix-round`, never as the gate. Before the PR,
  `python -I scripts/gate.py --verify <status.txt>`. CI runs on the PR to `main`. The release gate
  `scripts/gate.py --release --denylist <file>` is the owner's.
- A test of `run_tests.py` or `gate.py` `main` patches the part that runs things, even when it
  expects an early exit. A mutant that passes the flag check would otherwise run the whole suite
  recursively. A red run that times out kills the process tree and checks for orphans.
- A change to a rules file or a skill also runs
  `plugins/rigger-rules/tests/test_plugin_rules.py` and
  `plugins/rigger-windows/tools/drift-lint/tests/test_drift_lint.py`.

**Real `claude` runs (spikes and Task 20)**
- Every `claude` command runs under a fresh temp `CLAUDE_CONFIG_DIR`, one per experiment, never the
  real one. That includes `claude plugin validate`, `marketplace add` and `install`.
- A `--plugin-dir` run also sets `CLAUDE_CODE_PLUGIN_CACHE_DIR` and `HARNESS_LESSONS_INBOX` to
  scratch folders and leaves `RIGGER_LESSONS_BATCH` unset.
- The child environment drops `CLAUDE_*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`, then sets
  `ANTHROPIC_BASE_URL` to the stub. Before the first run, check the isolation with a canary on a
  scratch config: a marker file written in the scratch config and none in the real one. Abort on
  any failure.
- Read `claude --version` and `command -v claude` right before the first run and record them in the
  spike result. Auto-update can move the CLI mid-task.
- Use a bounded timeout and `Popen` streaming stdout, as `claude -p` against a dead API retries for
  about 20 minutes. Stop at the init event when that is all a step needs.
- A hook proof reads `Hooks: <Event>:<matcher>` from `--debug-file`, or output that differs per
  firing, never an exit status or a request count. `claude -p --resume` drops resume-hook text, and
  identical hook text is deduplicated across firings.
- The stub finds a request by a unique word in the probe's prompt, never by recency. A stub handler
  recognises its trigger by the expanded `--plugin-dir` path, never by a nearby word.
- `claude plugin eval` always runs with `--no-publish`.
- Scratch goes to `<temp>/rigger-p1/<task>/`, outside the repository. A copied repository deletes
  its `.git` first.

**Docs and sweep**
- Before committing any doc with URLs or dotted code names, run
  `python -I scripts/sweep.py --no-denylist <file>`. The host rule matches `ALLOWED_DOMAINS`
  exactly and reads dotted attributes as domains. The command must print `# files 1, findings 0`.
- Spike result files are public: they record commands and observed values with temp paths written
  as `<temp>`, never a real home path.

## Review Focus

1. **A hook input whose `session_id` is missing, empty or holds path characters** (`../x`, `a\b`,
   300 characters). A person expects rules to print anyway and no file written outside
   `<config dir>/rigger/state/`. Test: Task 9, `test_unsafe_session_id_writes_nothing`.
2. **Rules files with CRLF endings, no final newline or a UTF-8 BOM** (a Windows checkout of a
   directory marketplace with `core.autocrlf`). A person expects the same text `rules_start.sh`
   printed: CR stripped at line end, the last line terminated, and a BOM kept, as today. Test:
   Task 7, `test_crlf_and_missing_final_newline`, and a CRLF layout in the golden matrix
   (Task 15).
3. **Broken or wrongly shaped Claude Code state** (`installed_plugins.json` not JSON, a list where
   an object is expected, a non-boolean `enabledPlugins` value, an `installPath` that is gone). A
   person expects one notice line and rules from everything still readable, never an empty block
   that looks like success. Test: Task 11, `test_wrong_shapes_give_one_notice_each`.
4. **Personal `rules.d` text that looks like a placeholder** (`{{slot:testing}}`,
   `{{reply_language}}`). Today it prints as written. A person expects it unchanged, so the personal
   layer is never rendered in phase 1. Test: Task 14, `test_personal_text_is_not_rendered`, and a
   golden `rules.d` fixture (Task 15).
5. **Module roots with spaces or non-ASCII letters** (a user profile folder named `Zażółć Ą`, the
   `;` separator on Windows). A person expects the `Plugin root:` line to show that path exactly as
   today and discovery to work. Test: Task 11, `test_module_dirs_with_space_and_non_ascii`, and the
   golden layout root under a folder named `rigger p1 zażółć` (Task 15).
6. **Plugins updated one at a time** (`claude plugin update rigger-rules@rigger` alone: the new
   rigger-rules has no rules handlers, the old rigger-core has no assembler). A person expects one
   visible line, never a session with no rules at all. Whether plugins of one marketplace can sit
   at different commits is recorded by S3 (Task 3, Step 6); if they can, Task 18 keeps one check
   handler in rigger-rules. Test: Task 18, `test_old_core_prints_one_visible_line`. The reverse
   (new core, old rigger-rules without a manifest) must not print `rules.d` twice. Test: Task 14,
   `test_personal_layer_needs_rigger_rules`.
7. **The assembler dies before `main` runs** (a syntax error under Python 3.9, a missing sibling
   module in a half-written cache, `assemble.py` gone). A person expects the same visible line as
   for any other failure, not an empty part. Test: Task 14, `test_part_sh.py`
   `test_assembler_exit_status_prints_visible_line`.
8. **A project `.claude/rigger.json` trigger row whose `before` or `load` holds a newline or `|`**
   (hand-written JSON, or a cloned repository). A person expects that row ignored with one notice
   and the table intact, never extra lines injected after the table. Test: Task 10,
   `test_trigger_row_shape`.

---

## Decisions this plan makes

Decisions the spec leaves to the builder, or that the no-behaviour-change promise forces. Each task
below follows them.

| # | Decision | Why |
|---|---|---|
| D1 | The golden test compares, per module, the ordered list of `(heading line, bytes)` pieces, plus the **set** of `Plugin root:` lines, with subagent output compared as decoded `additionalContext`. It does not compare whole-output bytes. Every rules and trigger-table byte is still compared. | rigger-rules prints 12 identical root lines today (one per handler). The assembler prints one root line per module per part. JSON escaping differs (`rules_start.sh` escapes only `\`, `"` and tab). Spec §13 defines the measure this way. One root-set difference is decided, not found: a handler whose every section is dropped by heading prints a lone root line today (`HARNESS_RULES_DROP='## Talking to me'` leaves only rigger-talk's root line); the assembler prints no root line for a module with no printed section. The golden test asserts that case by name (Task 15, Step 3). |
| D2 | Today's behaviour is frozen as a committed fixture copy, `tests/fixtures/golden-34e7713/`, holding `plugins/<p>/{rules,hooks}/**` and `rigger-talk/hooks/talk_start.sh`. The golden test reads no git history. | It works the same under WSL, in CI and in a tarball. Rules text changes to placeholders in Task 18, so today's handlers cannot run on the new files. |
| D3 | Every part starts with the `Plugin root:` line of the first module whose section it carries, and repeats it whenever the module changes. Personal text and notices carry no root line. A personal section that replaces a module's section keeps that module's root line and position. | This is correct whether or not S1 finds a stable order, so no code waits on S1's order result. |
| D4 | The personal layer (`rules.d`) is loaded only when `rigger-rules` is among the discovered modules (enabled and holding a manifest). It keeps today's rules: one 9,000-byte budget over the files in byte order; the file that would go over and every file after it are left out and named in `rules_extra: left out over the hook cap: <names>`; the control-character filter; the trailing-newline rule; `*-main.md` main-only. It is never rendered. `HARNESS_RULES_DROP` does not apply to personal text, including a personal section that replaced a general one. | Today `rules_extra.sh` ships in rigger-rules, ignores `HARNESS_RULES_DROP` and prints raw text, and `plugins/rigger-rules/README.md:50-51` documents that. An old rigger-rules without a manifest still runs `rules_extra.sh`, so loading `rules.d` for it too would print it twice. Spec §8.2 says the drop applies to every layer; review verdict: keep today's rule (open risk 1). |
| D5 | The section ids of an override are matched on raw (unrendered) heading text. The slug is cut at ` (`, `:`, ` — ` or ` - ` before anything else. A `HARNESS_RULES_DROP` heading entry is compared with the rendered heading line, an id entry with the id. | `git.md:1` holds a placeholder after ` (`, so its id stays `git.git`. A person's existing drop value holds the heading as it is printed, `` ## Git (procedure and naming: `rigger-core:git-workflow`) ``. |
| D6 | Placeholders replace only backticked `<plugin>:<skill>` skill names and the three trigger tables: `git.md` (2), `plans-main.md` (2), `skills.md`, `windows-skills.md` and `wp-skills.md`, plus talk's `{{var:…}}`. Slash commands (`/rigger-core:coordinate`, `/rigger-core:environments`, `/rigger-lessons:curate-lessons`) and `safe-edit.ps1` stay literal. `{{tool:…}}` is not implemented in phase 1. | `{{tool:}}` renders an install path, but today's text says `safe-edit.ps1` with no path, so byte identity forbids it. G2 is about skills. The three slash commands are not swappable names: `coordinate` is fixed core (spec §1), `environments` is a core command, and `curate-lessons` is named by its own module's rules file. Spec §13 phase 3 already owns the `safe-edit.ps1` references ("become `{{tool:…}}` with `requires: plugin:rigger-testing`"). Until then `--check` reports a `{{tool:…}}` as an unknown placeholder. |
| D7 | `{{triggers:<id>}}` renders the header `\| Before \| Skill \|` plus `\|---\|---\|` plus one row per trigger, or nothing when the section has no rows. In phase 1 every row renders, whether or not its plugin is enabled. The placeholder stands alone on its line, and the whole line, newline included, is replaced by the table. A config row without `section` belongs to `skills.skill-triggers`. A row whose section has no placeholder in a discovered module is not rendered; from L3 or L4 it also gives one notice. | Spec §9.3: phase 1 keeps the text as today. Spec §7.2 gives config rows no `section`, and the level-3 example has none. Module and L1 rows stay silent because today, too, core's rows do not print without rigger-rules. |
| D8 | Core's slot-named rows (brainstorm and plans, testing and TDD, debugging) live in `rigger-defaults.json`. Rows naming rigger-core skills directly live in rigger-core's manifest. Module rows come first, then config rows. | That order reproduces today's 8-row table order exactly (§9.3 "ordered by first appearance"). |
| D9 | Module order: rigger-core first; then modules whose marketplace manifest is readable, in its listed order; then the rest by name. The personal layer is last. | Spec §8.2. Byte identity is per module (D1), so cross-module order only decides duplicates. |
| D10 | The project directory is the hook input's `cwd`, else `CLAUDE_PROJECT_DIR`, else the process cwd. | SessionStart and SubagentStart inputs carry `cwd`. |
| D11 | In phase 1 the config keys with an effect are `triggers` (L1–L4) and `expects` (L4: one notice line per missing or disabled module, plus doctor). `company` and `leak_guard` are type-checked and recorded with no effect; their consumers ship in phase 4. `slots` at L2–L4, `trust`, `review` and `shell_guard` are recorded only. `{{slot:…}}` and `slot:` load entries resolve from level-1 `slots` alone, whatever L2–L4 hold. Every key set above L1 that has no effect yet is listed by doctor as one INFO row `config-inactive` naming the key, its file and "no effect in this release" (Task 19); the session itself gets no notice for it. | Spec §13 Phase 1, "Config", read with phase 4's scope and with §7.5 ("Phase 1 does not … let levels 2–4 change a slot"). A person who sets `leak_guard.denylist` or `slots.reviewer` must be able to find out that nothing reads it yet. Review verdict: keep (open risk 2). |
| D12 | K = 6 `SessionStart` parts and K = 4 `SubagentStart` parts. Parts 2..K compute the whole assembly themselves; there is no cache between parts. Snapshots older than 14 days are deleted by part 1 on `startup`. | Open points 1 and 2. Task 21 measures the startup cost, and the cache option opens only if the +300 ms budget is missed. |
| D13 | Notices go at the top of part 1 only. With no config file and a clean layout there are none. | Byte identity, and one place for a person to look. |
| D14 | An empty slug (a heading such as `## —`) is a `--check` finding, `empty heading slug in <file>:<line>`. At session start that section gets the id `<stem>._line<N>`. Two equal slugs in one file are a duplicate. | Open point 9. |
| D15 | `tests/test_module_contracts.py` (manifest schema, `assemble.py --check` over the repository) ships in phase 1. `run_tests.py --changed-modules` does not: it is the first task of the phase-2 plan, as its own pull request before any slot work (open risk 3). Phase 1 delivers what it reads: `tests.paths` in every manifest, proved complete by `test_tests_paths_cover_every_discovered_test_file` (Task 13). | §6.2 and §8.4 require the contract test to run the check. Every phase-1 commit touches a shared file of spec §12 (`scripts/`, `tests/`, `plugins/*/rules/**`, `rigger-defaults.json`), so the mode would exit 2 on each and gate nothing here. It has to exist before phase 3 splits modules, and the contract fixtures it runs arrive in phase 2. |
| D17 | `drift_lint.py`, `proposal_check.py` and `batch_model.py` keep reading raw rules files in phase 1, so 8 lines in 5 files show placeholders to them: `git.md:1,4`, `plans-main.md:4`, the three tables and `talk.md`. Only `harness_start.py` renders (Task 17), because its heading warning is user-visible and would go silent. | The other three are maintainer and curation tools. Rendering them is phase 2 work, when the tables merge (open risk 6). |
| D18 | `--check` resolves `<plugin>:<skill>` only when `<plugin>` is a module in the checked set: `skills/<skill>/SKILL.md` must exist. Any other plugin (superpowers) counts as external: one info line `# external: superpowers`, not a finding. | The check cannot see a plugin outside the repository. §8.4's "resolves to nothing" is read as "a module skill that does not exist". |
| D16 | S6 (panel writes, phase 5) and S7 (shellwise version, phase 3) are not run. Phase 1 adds spike E1, "how an eval child gets its module roots", because rigger-rules' five evals lose their rules without it. | E1 is a phase-1 dependency the spec did not list. |
| D19 | The exit-criterion run with one root left out of `RIGGER_MODULE_DIRS` (Task 20) asserts that module's sections and root line are absent. It asserts no notice. | Spec §13 asks for "one notice", but a plugin loaded with `--plugin-dir` is in neither Claude Code state file, so the assembler cannot know it exists. The PR body names this as a spec correction. |
| D20 | `RulesSizeTests` measures the rendered general layer with module options at their defaults, and its ceiling becomes 27,708 bytes. | Today's 27,681 is the sum of the raw files, with `talk.md` unrendered (2,539 bytes). Rendered with the defaults `talk.md` is 2,566 bytes, so the same text measures 27,681 − 2,539 + 2,566 = 27,708. Keeping 27,681 would fail on unchanged content. |
| D21 | A snapshot carries `"format": 1`. One with another value or a missing top-level key counts as unreadable: a fresh read with the notice, then a rewrite. | A plugin update between two compactions of one session must not feed an old snapshot shape to new code. |

## File map

| Path | Task | Responsibility |
|---|---|---|
| `docs/superpowers/spikes/phase-1/S1.md` … `S5.md`, `E1.md` | 1–6 | recorded spike results and the decision taken |
| `plugins/rigger-core/tools/assemble/sections.py` | 7 | split a rules file into sections; slug; ids |
| `plugins/rigger-core/tools/assemble/manifest.py` | 8 | load and validate `rigger-module.json` |
| `plugins/rigger-core/tools/assemble/config.py` | 10 | four-level resolver, key table, notices, sources |
| `plugins/rigger-core/rigger-defaults.json` | 10 | level 1: `schema`, `slots`, core trigger rows |
| `plugins/rigger-core/tools/assemble/parts.py`, `snapshot.py` | 9 | packing into parts; snapshot read, write and cleanup |
| `plugins/rigger-core/tools/assemble/discover.py` | 11 | installed and enabled plugins, `RIGGER_MODULE_DIRS`, personal module |
| `plugins/rigger-core/tools/assemble/render.py` | 12 | placeholders and trigger tables |
| `plugins/*/rigger-module.json` (7), `plugins/{rigger-lessons,rigger-talk,rigger-wp,rigger-windows}/.claude-plugin/plugin.json`, `plugins/rigger-windows/evals/*/prompt.md` | 13 | manifests, `dependencies`, eval `plugins:` lines |
| `plugins/rigger-core/tools/assemble/assemble.py`, `part.sh` | 14 | layering, audience, drop, personal layer, subagent skip, CLI modes |
| `tests/fixtures/golden-34e7713/**`, `tests/golden_baseline.py`, `tests/test_assembly_golden.py` | 15 | frozen handlers and the golden test |
| `tests/test_module_contracts.py`, `scripts/run_tests.py`, `scripts/run_tests.min`, `scripts/eval_stage.py`, `tests/test_run_tests.py`, `tests/test_eval_stage.py` | 16 | contract test, env strip, eval module roots |
| `plugins/rigger-core/tools/harness_start/harness_start.py` (+ its test) | 17 | heading-overlap check reads rendered headings |
| six `hooks/hooks.json`, rules text, `talk_start.sh`, wiring tests; `plugins/rigger-rules/hooks/core_check.sh` and its test if S3 Step 6 asks for it | 18 | the cutover |
| `plugins/rigger-core/tools/doctor/doctor.sh` (+ tests) | 19 | doctor rows |
| `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` | 20 | stub exit criteria |
| `scripts/startup_bench.py`, `.github/workflows/test.yml` | 21 | startup-cost measurement |
| `CHANGELOG.md`, `CONTRIBUTING.md`, `plugins/rigger-rules/README.md`, `plugins/rigger-core/README.md` | 22 | release notes, migration, docs |

Unit tests for `tools/assemble/*.py` live in `plugins/rigger-core/tools/assemble/tests/test_<module>.py`.

## Parallel waves

| Wave | Tasks | Runs after | Why they are file-disjoint |
|---|---|---|---|
| A | 1, 2, 3, 4, 5, 6 (spikes) | — | each writes its own `spikes/phase-1/<id>.md` and its own scratch folder; each starts its own stub on port 0 |
| B | 7, 8, 9, 10 | wave A recorded, no spike BLOCKED | separate files |
| C | 11, 12, 13 | 7, 8, 10 (11 also needs S3, S4 and E1; 13's tests call Task 8's `load_manifest`) | separate files; 13 writes only manifests, `plugin.json`, eval prompts and `ManifestTests` in `tests/test_repo.py` |
| D | 14, 17 | 7–13 | 14 writes `assemble.py` and `part.sh`; 17 writes only `harness_start` |
| E | 15, 16, 19 | 14 | golden under `tests/`; 16 under `scripts/` and its tests; 19 under `doctor/` |
| F | 18 (cutover, serial) | 15 green on unchanged rules, and 17 | touches many shared files |
| G | 20, 21 | 18 | one new test file; bench and workflow |
| H | 22 (docs, needs 21's numbers), then 23 (gate, PR) | all | — |

Parallel tasks: 1–6; 7, 8, 9 and 10; 11, 12 and 13; 14 and 17; 15, 16 and 19; 20 and 21. Serial:
18, 22 and 23.

---

## Spikes (wave A)

Each spike's result file has these headings, in order:

- `## Question`
- `## CLI` (version and path)
- `## Setup` (commands, temp paths as `<temp>`)
- `## Control` (the known answer and whether it was observed)
- `## Observed`
- `## Decision`

A spike whose control fails reruns once with the cause fixed. A spike whose control fails twice
reports `BLOCKED` and stops wave B. Shared harness pattern: copy `plugins/rigger-rules/tests/stub_api.py` and the env-isolation and debug-log code of
`plugins/rigger-rules/tests/test_reinject.py` (`claude_turn`, `copies`) into
`<temp>/rigger-p1/<id>/harness.py`. The spikes never import them from the repository, so no
repository file changes.

### Task 1: Spike S1 — parallel part handlers all reach the context

**Files:**
- Create: `docs/superpowers/spikes/phase-1/S1.md`
- Scratch: `<temp>/rigger-p1/S1/{harness.py,plugin/.claude-plugin/plugin.json,plugin/hooks/hooks.json}`

**Interfaces:**
- Produces: the observed order of 6 handlers of one `SessionStart` group, and whether each arrives
  as its own `SessionStart:startup hook success:` block. Task 14 and Task 20 use both.

- [ ] **Step 1: Build the probe plugin.** `plugin.json` holds `{"name": "spike-parts"}`. `hooks.json` has one `SessionStart` group, matcher `startup|clear|compact`, with 6 handlers. Handler `i` is `printf 'S1-PART-%s-i\n' "$SPIKE_RUN"`, where `SPIKE_RUN` is a fresh uuid per run in the child env.
- [ ] **Step 2: Known-answer control.** Run once with a seventh handler, `printf 'S1-CONTROL-%s\n' "$SPIKE_RUN"`, in a second group. Expected: the stub request whose messages hold the probe word `s1probe-<uuid>` contains `S1-CONTROL-<run>` exactly once. If it does not, the harness is broken; fix it before Step 3.
- [ ] **Step 3: Run 5 sessions** with `claude -p s1probe-<uuid> --plugin-dir <temp>/rigger-p1/S1/plugin --output-format json --debug-file <temp>/rigger-p1/S1/debug-<n>.log`. Record in each the order of `S1-PART-<run>-<i>` lines in the first request and the count of `Hooks: SessionStart:startup` lines in the debug log.
- [ ] **Step 4: Size run.** Today one plugin prints at most about 27,000 bytes at session start (rigger-rules with a full `rules.d`), and only `rules_extra.sh` comes near 9,000 in one handler. The assembler prints up to 6 × 9,000 from one group. Run once more with each handler printing its marker line, 8,900 bytes of filler lines and a last line `S1-END-<run>-<i>`. Record for each `i` whether both its marker and its end line are in the first request.
- [ ] **Step 5: Write `S1.md`.** Pass: all 6 markers exactly once in each of the 5 runs, and all 6 end lines in the size run.
  - **Decision if pass:** K handlers stay, per D3 and D12. Record "order stable" (5 of 5 in `hooks.json` order) or "order varies". Neither changes code (D3).
  - **Decision if any marker is missing or doubled:** status `BLOCKED`. Report the counts to the owner. The part-handler design (spec §9.1) cannot hold, and no wave-B task starts.
  - **Decision if only the size run loses an end line** (text persisted to a file or cut): repeat it at 6,000 and 4,000 bytes per handler, record the largest size that arrives whole, and report `BLOCKED` with that number. `PART_CAP` and K are the owner's to change (D12).
- [ ] **Step 6: Sweep and commit.**
  Run: `python -I scripts/sweep.py --no-denylist docs/superpowers/spikes/phase-1/S1.md`. Expected: `# files 1, findings 0`.
  `git add docs/superpowers/spikes/phase-1/S1.md && git commit -m "docs(spikes): record S1 part handler order"`

### Task 2: Spike S2 — where plugin option values live

**Files:**
- Create: `docs/superpowers/spikes/phase-1/S2.md`
- Scratch: `<temp>/rigger-p1/S2/`

**Interfaces:**
- Produces: either a readable store, recorded as file, JSON key path and value encoding (branch A),
  or "not readable outside the plugin's own hook" (branch B). Task 14 Step 5 implements the branch
  chosen here.

- [ ] **Step 1: Find the documented way to set a `userConfig` value** non-interactively, from Claude Code's plugin documentation (WebFetch of the docs host) and `claude plugin --help`. Record the exact command or settings key.
- [ ] **Step 2: Set `reply_language` to `S2-LANG-<uuid>`** for rigger-talk in a fresh config. Load the plugin from a scratch copy of `plugins/rigger-talk` (`.git` removed) through `--plugin-dir`, or through a directory marketplace if `--plugin-dir` takes no option values; record which.
- [ ] **Step 3: Known-answer control.** The scratch talk copy's own `SessionStart` hook prints `CLAUDE_PLUGIN_OPTION_REPLY_LANGUAGE`. Expected: `S2-LANG-<uuid>` in the first request. If it is absent, the value was never set, and Step 2 is redone.
- [ ] **Step 4: Probe from core.** Add a scratch `SessionStart` hook to a scratch rigger-core copy that prints every env var whose name starts with `CLAUDE_PLUGIN_OPTION_`. Then search the whole temp config dir and the project dir for `S2-LANG-<uuid>` with `rg -uu -l`.
- [ ] **Step 5: Write `S2.md`.**
  - **Branch A** (the marker sits in a plain-text file the assembler can read, for example a settings key): record the file, the key path, how numbers and empty strings are stored, and which scopes can hold it. Decision: the assembler reads it read-only (spec §9.6 default) and falls back to the `userConfig` defaults of `plugin.json`.
  - **Branch B** (only the plugin's own env holds it, for example a keychain store): decision per spec §9.6 fallback. rigger-talk keeps one `SessionStart` handler that writes `<config dir>/rigger/state/<session id>.options.rigger-talk.json`, and the assembler waits up to 1 s for it.
  - Either branch also records whether the core probe saw the variable. If it did, the env var is the source, which is the simplest case of A.
- [ ] **Step 6: Sweep and commit** as in Task 1, message `docs(spikes): record S2 plugin option storage`.

### Task 3: Spike S3 — directory marketplace `installPath` lag and the root a hook sees

**Files:**
- Create: `docs/superpowers/spikes/phase-1/S3.md`
- Scratch: `<temp>/rigger-p1/S3/` (a copy of the repository without `.git`)

**Interfaces:**
- Produces: for a directory marketplace, whether `installPath` content lags the source, and the
  exact `CLAUDE_PLUGIN_ROOT` string a hook receives, compared with `installPath` and with the
  marketplace source. Task 11 `module_root()` uses it. Also whether one plugin can be updated
  without its dependency (Step 6); Task 18 uses that.

- [ ] **Step 1:** `claude plugin marketplace add <temp>/rigger-p1/S3/repo`, then `claude plugin install rigger-rules@rigger` in a fresh config. Read `installed_plugins.json` and `known_marketplaces.json`, and record `installPath` and the marketplace `source`.
- [ ] **Step 2: Known-answer control.** Before any edit, `installPath/rules/tests.md` equals `<repo>/plugins/rigger-rules/rules/tests.md` byte for byte.
- [ ] **Step 3:** Append the line `S3-EDIT-<uuid>` to `<repo>/plugins/rigger-rules/rules/tests.md`. Add a scratch handler to the copy's `hooks.json` that prints `S3-ROOT=${CLAUDE_PLUGIN_ROOT}`; install before editing so both exist in the snapshot. Start a stub session.
- [ ] **Step 4: Record.**
  - Does the first request carry `S3-EDIT-<uuid>`? (Hooks read from where?)
  - Does `installPath/rules/tests.md` carry it?
  - What is the `S3-ROOT` string: separators, drive-letter case, and whether it equals `installPath` or the source path?
- [ ] **Step 5: Write `S3.md`.**
  - **If `installPath` lags:** discovery reads a directory marketplace's module root from the marketplace `source` path joined with the plugin's `source`, and prints that path in the root line.
  - **If it does not lag:** `installPath`.
  - **In both cases:** the printed root line uses the exact string form `CLAUDE_PLUGIN_ROOT` showed. Record any transformation needed (for example `\` to `/`).
- [ ] **Step 6: Version skew (Review Focus 6).** In the same config also install `rigger-core@rigger`. Append `S3-SKEW-<uuid>` to one file of each of the two plugins in the repository copy, then run `claude plugin update rigger-rules@rigger` alone. Control: rigger-rules' `installPath` now holds the marker (the update ran). Record whether rigger-core's `installPath` holds it too. Record from `claude plugin update --help` and the plugin documentation whether a marketplace update or auto-update moves every plugin of the marketplace at once, and whether `dependencies` accepts a minimum version.
  - **If rigger-core moved with it,** or a dependency version floor exists and this repository can use it: no check handler; Task 18 skips its conditional step, and Task 22 records the mechanism.
  - **If rigger-core stayed behind:** Task 18 keeps the check handler in rigger-rules.
- [ ] **Step 7: Sweep and commit**, message `docs(spikes): record S3 directory marketplace roots`.

### Task 4: Spike S4 — which settings scopes enable a plugin

**Files:**
- Create: `docs/superpowers/spikes/phase-1/S4.md`
- Scratch: `<temp>/rigger-p1/S4/`

**Interfaces:**
- Produces: a table `scenario → plugins array of the init event`, taken from the real CLI. Task 11
  copies every row into `test_discover.py` as a known-answer case.

- [ ] **Step 1:** Make 7 scratch plugins `s4-a` … `s4-g` in one directory marketplace and install all of them in user scope. Install `s4-g` with project scope for a different project dir.
- [ ] **Step 2: Scenarios**, one fresh config each:

  | Scenario | Setting |
  |---|---|
  | 1 | user `settings.json` `s4-a: true` |
  | 2 | user `settings.local.json` `s4-b: true` (record whether Claude Code reads that file at all) |
  | 3 | project `.claude/settings.json` `s4-c: true` |
  | 4 | project `.claude/settings.local.json` `s4-d: true` |
  | 5 | user `s4-e: true` plus project `s4-e: false` |
  | 6 | project `s4-f: true` plus local `s4-f: false` |
  | 7 | `s4-g` enabled in user settings while the cwd is not its `projectPath` |
  | 8 | `--plugin-dir` of an eighth plugin `s4-h` that is not installed |

  Read the `plugins` array from the `stream-json` init event, stopping the stream at init.
- [ ] **Step 3: Known-answer control.** Scenario 1 must list `s4-a` and no other `s4-*`. If it does not, the harness reads the wrong event.
- [ ] **Step 4: Managed settings** are not written: the location is a system folder, and changing system settings is out of bounds. Record "managed scope: not probed".
- [ ] **Step 5: Write `S4.md`** with the table and the precedence rule observed (which scope wins on a conflict).
  - **Decision if the scopes match spec §6.1 S4:** discovery reads exactly those files, with that precedence.
  - **Decision if any scope behaves otherwise** (for example user `settings.local.json` ignored, or `projectPath` honoured): discovery follows the observed table, and the spec's S4 row is corrected in the PR body.
- [ ] **Step 6: Sweep and commit**, message `docs(spikes): record S4 enabled-plugin scopes`.

### Task 5: Spike S5 — what `SubagentStart` input carries, and whether 4 subagent parts arrive

**Files:**
- Create: `docs/superpowers/spikes/phase-1/S5.md`
- Scratch: `<temp>/rigger-p1/S5/stub_tool.py` (a copy of `stub_api.py` whose first reply to the probe word is an `Agent` `tool_use` with `subagent_type: "general-purpose"` and a prompt carrying `s5sub-<uuid>`, followed by text replies)

**Interfaces:**
- Produces: the `SubagentStart` stdin keys (`session_id`, `agent_type`, `cwd`, `hook_event_name`,
  and whether `session_id` equals the parent's), and the count of 4 part markers in the subagent's
  first request. Task 14 uses both.

- [ ] **Step 1:** In a scratch plugin, a `SessionStart` hook and a `SubagentStart` hook each copy stdin to `<temp>/rigger-p1/S5/in-<event>-<n>.json`. Four `SubagentStart` handlers each print JSON `additionalContext` holding `S5-SUB-<run>-<i>`, 8,800 bytes of filler and a last line `S5-END-<run>-<i>` (the size the assembler's subagent parts reach).
- [ ] **Step 2: Known-answer control.** The stub's request that carries `s5sub-<uuid>` exists. That proves a subagent ran. Its absence means the stub's `tool_use` shape is wrong; fix it first.
- [ ] **Step 3: Record** the keys of both stdin files, whether the `session_id` values are equal, the `agent_type` string, and the count of each `S5-SUB` and `S5-END` marker in the subagent request.
- [ ] **Step 4: Write `S5.md`.**
  - **If the parent `session_id` arrives:** subagent parts read the parent snapshot and recompute only when it is missing.
  - **If it does not:** subagent parts always recompute from files.
  - **If any of the 4 markers or end lines is missing:** status `BLOCKED`, as in S1, with the largest size that arrives whole when only end lines are lost.
- [ ] **Step 5: Sweep and commit**, message `docs(spikes): record S5 subagent hook input`.

### Task 6: Spike E1 — how an eval child learns its module roots

**Files:**
- Create: `docs/superpowers/spikes/phase-1/E1.md`
- Scratch: `<temp>/rigger-p1/E1/`

**Interfaces:**
- Produces: whether `claude plugin eval` passes the parent's environment (for example
  `RIGGER_MODULE_DIRS`) to a case's child session, and whether a case's front matter can set env.
  Task 16 `eval_stage.py` implements the chosen branch.

- [ ] **Step 1:** Stage a scratch plugin with one eval case (`prompt.md` with `plugins: ['../..']`, one regex grader) and a `SessionStart` hook that prints `E1-ENV=${RIGGER_MODULE_DIRS:-unset}`.
- [ ] **Step 2: Known-answer control.** With `ANTHROPIC_BASE_URL` set to the stub, run `claude plugin eval <staged> --no-publish --keep-temp`. The stub receives a request whose text holds `E1-ENV=`. Its absence means the hook never ran in the child; fix that first.
- [ ] **Step 3:** Run again with `RIGGER_MODULE_DIRS=E1-MARK-<uuid>` in the parent env. Record whether the request shows `E1-ENV=E1-MARK-<uuid>`. Check the eval documentation and `claude plugin eval --help` for a per-case env key, and record the result: `scripts/eval_suite_check.py` `FRONTMATTER_KEYS` already accepts `env`, so also run one case with `env` setting `RIGGER_MODULE_DIRS=E1-CASE-<uuid>` and record whether the hook sees it. A per-case value is written before staging, so it can only hold relative roots; record what a relative entry would resolve against (the hook's cwd).
- [ ] **Step 4: Write `E1.md`.**
  - **Branch A (env inherited):** `eval_stage.py` prints `RIGGER_MODULE_DIRS=<staged roots joined by os.pathsep>` as its last line, and `CONTRIBUTING.md` says to export it.
  - **Branch B (not inherited):** `eval_stage.py` writes `<staged rigger-core>/.rigger-module-dirs`, one staged root per line, and discovery reads that file next to its own root when present. A shipped install never holds the file. Branch B is also the choice when only the per-case `env` works: discovery takes absolute roots only, and a case file cannot know them.
- [ ] **Step 5: Sweep and commit**, message `docs(spikes): record E1 eval child environment`.

---

## Assembler (waves B–D)

Unit-test command pattern, run from the repository root:
`python -I -X utf8 plugins/rigger-core/tools/assemble/tests/test_<module>.py -v <TestClass>`.
Expected red output: `FAILED (failures=N)` or `(errors=N)` from an `ImportError` only on the very
first run of a new module. The red run that counts is on an assertion, after the stub signature
exists. Expected green: `Ran N tests` … `OK`.

### Task 7: Sections, slugs and ids

**Files:**
- Create: `plugins/rigger-core/tools/assemble/sections.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_sections.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Section: id: str; stem: str; heading: Optional[str]; text: bytes; line: int`.
    `text` is the section's lines exactly as `rules_start.sh` prints them: CR stripped at line end
    only, each line ending in `\n`.
  - `slug(heading_line: str) -> str`
  - `split(stem: str, data: bytes) -> List[Section]`
  - `normalize_module_bytes(data: bytes) -> bytes` (the per-line rule of `rules_start.sh:128-142`)

- [ ] **Step 1: Write the failing tests.**
  - `test_slug_table`: rows taken from spec §8.1 and §9.2.

    | Heading | Expected slug |
    |---|---|
    | `## Git (procedure and naming: {{skill:rigger-core:git-workflow}})` | `git` |
    | `## Tests` | `tests` |
    | `## Plans: main session` | `plans` |
    | `## Recurring defect classes — check while writing, not after review` | `recurring-defect-classes` |
    | `## Talking to me` | `talking-to-me` |
    | `## Verify in the real environment: main session` | `verify-in-the-real-environment` |
    | `## Learn continuously, without being asked (hard rule)` | `learn-continuously-without-being-asked` |
    | `## Windows skills, loaded before the first step of their kind` | `windows-skills-loaded-before-the-first-step-of-their-kind` |
    | `## Zażółć gęślą` | `zazo-c-gesla` (NFD leaves `ł` whole, so it becomes `-`) |
    | `## A - b` | `a` |
    | `## —` | `` (empty) |

  - `test_preamble_and_ids`: `split("windows", b"The root.\n\n## Windows shell\n\n- x\n")` gives ids `["windows._preamble", "windows.windows-shell"]` and texts `[b"The root.\n\n", b"## Windows shell\n\n- x\n"]`.
  - `test_heading_only_file_has_no_preamble`: `split("tests", b"## Tests\n")` has one section.
  - `test_crlf_and_missing_final_newline` (Review Focus 2): `b"## A\r\nx\r\ny"` gives the text `b"## A\nx\ny\n"`. A mid-line `\r` is kept. A leading BOM stays in the preamble text.
  - `test_empty_slug_id`: `split("s", b"## \xe2\x80\x94\nx\n")[0].id == "s._line1"`.
  - `test_split_is_lossless_for_every_repo_rules_file`: for each `plugins/*/rules/*.md`, the joined section texts equal `normalize_module_bytes(file)`. It also asserts that at least 19 files were read.
- [ ] **Step 2: Run** `... test_sections.py -v`. Expected: `FAILED`, with assertion errors once the stubs exist.
- [ ] **Step 3: Implement** in `sections.py`. A heading is a line starting `## `, the test `rules_start.sh:132` uses. The heading text is the line trimmed of space, tab and CR. Slug steps: cut at the earliest of ` (`, `:`, ` — `, ` - `; NFD; drop combining marks; lowercase; replace each `[^a-z0-9]+` run with `-`; strip `-`.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Mutate the cut list (drop `:`): `test_slug_table` fails on `plans`. Drop CR stripping: `test_crlf…` fails.
- [ ] **Step 6: Commit.** `git add plugins/rigger-core/tools/assemble/sections.py plugins/rigger-core/tools/assemble/tests/test_sections.py && git commit -m "feat(rigger-core): split rules into sections with ids"`

### Task 8: Manifest loader and validation

**Files:**
- Create: `plugins/rigger-core/tools/assemble/manifest.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_manifest.py`

**Interfaces:**
- Produces:
  - `class ManifestError(Exception)`; `str(e)` is the reason used in `rigger: module <name> skipped: <reason>`.
  - `@dataclass class RuleEntry: file: str; layer: str; audience: str; requires: Tuple[str, ...]`
  - `@dataclass class TriggerRow: id: str; before: str; load: Tuple[str, ...]; section: Optional[str]; requires: Tuple[str, ...]`
  - `@dataclass class Module: name: str; root: Path; layer: str; fills: Optional[str]; provides: dict; rules: List[RuleEntry]; triggers: List[TriggerRow]; subagents: Dict[str, str]; config: Optional[dict]; lessons: Optional[dict]; tests: dict; marketplace: Optional[str]; origin: str`.
    `origin` is one of `"self"`, `"installed"`, `"dirs"` or `"personal"`.
  - `load_manifest(root: Path, marketplace: Optional[str], origin: str) -> Module`
  - `MANIFEST = "rigger-module.json"`, `PUBLIC_MARKETPLACE = "rigger"`, `CORE = "rigger-core"`

- [ ] **Step 1: Write the failing tests.**
  - One parameterised `test_refusals` over fixture manifests built in a temp dir. Each row expects `ManifestError` with this text fragment:

    | Fixture | Expected fragment |
    |---|---|
    | `schema: 2` | `schema 2` |
    | `name` ≠ `plugin.json` name | `name` |
    | a missing rules `file` | `missing rules file` |
    | `layer: company` with marketplace `rigger` | `company layer in the public marketplace` |
    | `config` in a general module that is not rigger-core | `config` |
    | duplicate `triggers[].id` | `duplicate trigger id` |
    | a rule entry `layer: company` in a general module | `above its module` |
    | `audience: "all"` | `audience` |
    | `requires: ["os:beos"]` | `requires` |
    | `rules` as an object | `rules must be a list` |
    | `load` as a string | `load must be a list` |
    | invalid JSON | `not valid JSON` |
    | a BOM-prefixed file | loads (UTF-8 with BOM accepted) |

  - `test_defaults`: a rule entry with no `layer` or `requires` gets the module layer and `()`. `audience` is required. An unknown top-level key (a later phase's `failure_mode`) is ignored, so a newer manifest still loads.
  - `test_valid_manifest_round_trip`: spec §6.3's `rigger-review` example, with a `plugin.json` named `rigger-review`, loads with one trigger row.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement** `load_manifest`. Validation is explicit `isinstance` checks, one reason per failure, the first failure wins. `os:` values are `windows`, `macos` and `linux`. Requires forms are `slot:<x>`, `plugin:<name>` and `os:<x>`.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Remove the public-marketplace company check: its row fails. Remove the `isinstance(rules, list)` check: its row fails. One mutation per row; this is an integrity check, so each row is broken once.
- [ ] **Step 6: Commit.** `feat(rigger-core): load and validate rigger-module.json`

### Task 9: Parts and snapshot

**Files:**
- Create: `plugins/rigger-core/tools/assemble/parts.py`, `plugins/rigger-core/tools/assemble/snapshot.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_parts.py`, `.../tests/test_snapshot.py`

**Interfaces:**
- Produces:
  - `Block = Tuple[str, bytes, str]`: `(module root, section bytes, section id)`. The root is `""`
    for personal text and for the notices block, which then get no root line.
  - `pack(blocks: List[Block], cap: int, count: int, encode: Callable[[bytes], bytes]) -> Tuple[List[bytes], List[str]]`.
    It returns `count` part bodies (empty bytes for unused ones) and the ids left out. Each part
    starts with `Plugin root: <root>\n` before its first block with a root and again at each change
    of root (D3). `encode` turns a body into its escaped form (identity, or JSON string escaping
    without the wrapper), and the cap applies to the escaped size (Global Constraints, Runtime).
    Notices travel as the first block, id `_notices`, so they count toward part 1's cap.
  - `SESSION_PARTS = 6`, `SUBAGENT_PARTS = 4`, `PART_CAP = 9000`
  - `OVERFLOW_LINE = "rigger: rules left out over the hook cap: {ids}"`
  - `snapshot_path(config_dir: Path, session_id: str) -> Optional[Path]` (None when the id fails `^[A-Za-z0-9_-]{1,128}$`)
  - `write_snapshot(config_dir: Path, session_id: str, data: dict) -> Optional[Path]`: temp name `<path>.<pid>.tmp` with `os.replace`, UTF-8, LF.
  - `read_snapshot(config_dir: Path, session_id: str) -> Optional[dict]`: None on a missing or unparseable file, and on one whose `format` is not `SNAPSHOT_FORMAT` or that lacks a key of `SNAPSHOT_KEYS` (D21).
  - `SNAPSHOT_FORMAT = 1`; `SNAPSHOT_KEYS = ("format", "parts", "modules", "config", "sections", "rows", "slots", "notices")`
  - `cleanup(config_dir: Path, now: float, max_age_days: int = 14) -> int`: deletes regular files in the state dir named `*.resolved.json`, `*.options.*.json` or `*.tmp` whose mtime is older, and nothing else.

- [ ] **Step 1: Write the failing tests.**
  - `test_pack_fills_in_order_at_section_boundaries`: three blocks of 4,000 bytes from one module with cap 9,000 give parts `[root+A+B, root+C, b"", …]`.
  - `test_root_line_repeats_at_module_change`.
  - `test_overflow_names_ids_and_ends_last_part`: a block that does not fit in K parts gives its id in the second return value, and the last part ends with the `OVERFLOW_LINE` text.
  - `test_cap_counts_encoded_bytes`: with `encode=lambda b: b * 2` (an encoding twice the size), a 5,000-byte block lands alone in a part.
  - `test_unsafe_session_id_writes_nothing` (Review Focus 1): ids `""`, `"../x"`, `"a\\b"`, `"a/b"` and `"x" * 129`. For each, `write_snapshot` returns None and the temp config dir holds no file afterwards.
  - `test_round_trip_and_atomic_name`: no `*.tmp` remains after a write.
  - `test_old_format_reads_as_missing` (D21): a file with `"format": 0`, and one without `parts`, both give None.
  - `test_cleanup_age`: files at 15 and 13 days, with a pinned `now`, give 1 deleted. A 15-day-old `notes.txt` in the state dir stays.
  - `test_root_free_blocks_get_no_root_line`: a notices block and a personal block (root `""`) around a module block give exactly one root line, before the module block.
- [ ] **Step 2: Run both files.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** Snapshot JSON is written with `ensure_ascii=False`, `sort_keys=True` and `indent=1`. The state dir is created with `exist_ok=True`.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Loosen the id regex to `.+`: the `../x` row fails. Remove `os.replace` and write directly: the atomic test fails on the `.tmp` check. Do not mutate `pack`'s module-change root line yet; Task 15 covers its effect.
- [ ] **Step 6: Commit.** `feat(rigger-core): pack rules into parts and keep a session snapshot`

### Task 10: Config resolver and level-1 defaults

**Files:**
- Create: `plugins/rigger-core/tools/assemble/config.py`, `plugins/rigger-core/rigger-defaults.json`
- Test: `plugins/rigger-core/tools/assemble/tests/test_config.py`

**Interfaces:**
- Produces:
  - `KEYS: Dict[str, Tuple[type, FrozenSet[int]]]`: the key-to-type-and-allowed-levels table of spec §7.2.
    `schema` (int, all), `slots` (dict, 1–4), `triggers` (dict, 1–4), `expects` (list, {4}),
    `company` (str, {4}), `leak_guard` (dict, {3}), `trust` (dict, {3}), `review` (dict, 1–4),
    `shell_guard` (dict, {3}).
  - `load_level(path: Path, level: int) -> Tuple[dict, List[str]]`: a missing file gives `{}` with
    no notice. A parse error gives `{}` and `rigger: config <path> ignored: <reason>`. A missing
    `schema`, or a value other than 1, gives `{}` and one notice. An L2 block (a manifest's
    `config`) is not a file and needs no `schema`.
  - `@dataclass class Resolved: effective: dict; sources: Dict[Tuple[str, ...], str]; inactive: Dict[Tuple[str, ...], str]; notices: List[str]`.
    `sources` maps each leaf's key path to `"L<n>:<file or module>"`. The path is a tuple because
    trigger ids hold dots. `inactive` holds the keys set above L1 that have no effect in phase 1
    (D11), with the same source strings; `effective["slots"]` always equals L1's `slots`.
  - `resolve(l1: dict, l2: List[Tuple[str, dict]], l3: dict, l4: dict, paths: Dict[int, str]) -> Resolved`
  - A trigger row object may hold `before` (str), `load` (list of str), `requires` (list of str)
    and `section` (str). `before` and each `load` entry are one line without `|` or control
    characters, `before` at most 300 characters, `load` 1 to 4 entries. A row that breaks this is
    ignored at that level with one notice naming file and id, and the lower level's row stands.
  - `merge(base: dict, over: dict) -> dict` (spec §7.3, no mutation of its inputs)
  - `defaults_path(core_root: Path) -> Path`

- [ ] **Step 1: Write the failing tests.**
  - `test_no_files_is_level_one`: `resolve(L1, [], {}, {}, paths)` gives `effective == L1`, an empty `inactive` and no notices.
  - `test_schema_missing_or_other_counts_as_absent`: a level-3 file without `schema`, and one with `schema: 2`, each give `{}` and one notice.
  - `test_trigger_row_shape` (Review Focus 8): L4 rows with `before: "a\nb"`, `before: "a | b"`, `load: "x:y"`, `load: []`, `load: ["x:y\n## Injected"]` and a 301-character `before` are each ignored with one notice naming the file and the id. An L4 row that replaces `rigger-core.testing` with a broken shape leaves the L1 row in place.
  - `test_slots_above_level_one_are_recorded_not_applied` (D11): L3 `slots.testing.target = "x:y"` leaves `effective["slots"]["testing"]["target"] == "rigger-core:testing"` and gives `inactive[("slots", "testing", "target")] == "L3:<path>"`, with no notice. `trust`, `review`, `shell_guard`, `company` and `leak_guard` land in `inactive` the same way.
  - `test_merge_rules`: null removes, objects merge, lists replace whole.
  - `test_l2_conflict_neither_applies`: two company modules set `slots.reviewer.model` to `opus` and `haiku`. The L1 value stands, and one notice names both modules and the key.
  - `test_unknown_key_and_wrong_type_notices`: an unknown key is ignored with a notice. `triggers: []` at L3 is ignored with a notice, and the L1 triggers stand.
  - `test_level_restricted_keys`: `expects` at L3 and `leak_guard` at L4 are ignored, each with a notice naming file and key.
  - `test_sources_name_the_level_file`: `sources[("triggers", "personal.notes", "before")] == "L3:<path>"`.
  - `test_trigger_null_removes_row`: L4 `{"triggers": {"rigger-core.debugging": null}}` removes the row.
  - `test_defaults_file_matches_spec`: `rigger-defaults.json` has `schema: 1`, the 9 slot entries of spec §7.5 (process with 4 roles), and trigger ids `["rigger-core.plans", "rigger-core.testing", "rigger-core.debugging"]` in this order, each with `section: "skills.skill-triggers"`.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement** `config.py`, and write `rigger-defaults.json` with today's texts copied from `skills.md`:
  - `rigger-core.plans`: before `new behaviour, a spec or a plan, in a side session too`, load `["slot:process.brainstorm", "slot:process.plans"]`.
  - `rigger-core.testing`: before `writing code or tests, reviewing tests, a spike, port or rehearsal`, load `["slot:testing", "slot:process.tdd"]`.
  - `rigger-core.debugging`: before `a bug, a failing test or unexpected behaviour`, load `["slot:process.debug"]`.
  - The `slots` block exactly as spec §7.5.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Make L2 last-wins: the conflict test fails. Remove the level check: the restricted-keys test fails. Let L3 `slots` merge into `effective`: the recorded-not-applied test fails. The row-shape check guards what a cloned repository can print into a session, so each of its branches (newline, `|`, `load` type, `load` count, length) is broken once.
- [ ] **Step 6: Commit.** `feat(rigger-core): resolve the four config levels`

### Task 11: Discovery

**Files:**
- Create: `plugins/rigger-core/tools/assemble/discover.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_discover.py`

**Interfaces:**
- Consumes: `load_manifest`, `Module`, `ManifestError` (Task 8); S3, S4 and E1 results.
- Produces:
  - `@dataclass class Discovery: modules: List[Module]; enabled: FrozenSet[str]; notices: List[str]`.
    `enabled` holds every enabled plugin name, module or not.
  - `discover(config_dir: Path, project_dir: Path, environ: Mapping[str, str], self_root: Path) -> Discovery`.
    Order follows D9. rigger-core is always `self_root`, unless `RIGGER_MODULE_DIRS` names a
    rigger-core.
  - `module_root(entry: dict, marketplaces: dict) -> Path` (follows S3)
  - `personal_module(config_dir: Path) -> Optional[Module]`: present when `<config dir>/rigger/rules.d` exists.
  - `MODULE_DIRS = "RIGGER_MODULE_DIRS"`

- [ ] **Step 1: Write the failing tests.**
  - `test_s4_scenarios`: one row per S4 scenario, built as the same files in a temp config, with the expected enabled set copied from `S4.md`. These are known answers from the real CLI.
  - `test_module_dirs_override_installed_with_notice`: the same name installed and in dirs gives the dirs root and one notice naming it.
  - `test_module_dirs_entry_not_a_dir_or_without_manifest`: one notice each, then skipped.
  - `test_module_dirs_with_space_and_non_ascii` (Review Focus 5): a root under `zażółć ą/` joined by `os.pathsep` is discovered, and `Module.root` keeps the exact string.
  - `test_wrong_shapes_give_one_notice_each` (Review Focus 3): rows for `installed_plugins.json` not JSON, `plugins` as a list, an entry `installPath` missing, `enabledPlugins` as a list, an `enabledPlugins` value `"yes"`, and a settings file holding a JSON string. Each row gives exactly one notice. The modules that remain readable are still listed, and the call never raises.
  - `test_invalid_manifest_skips_module_with_reason`: `rigger: module x skipped: <reason>`.
  - `test_order_core_first_then_marketplace_order`.
  - `test_directory_marketplace_root` following the S3 decision.
  - `test_eval_module_dirs_file` (E1 branch B only): `<self_root>/.rigger-module-dirs` lines are read like dirs entries.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** Read `installed_plugins.json` in the shape `harness_start.py` `collect()` (`:175-200`) reads: `plugins` maps `<name>@<marketplace>` to a list of entries with `scope` and `installPath`. Any other shape is one notice (Review Focus 3). Which `scope` values count follows S4 scenario 7; `collect()` takes user scope only. Read the settings scopes S4 recorded, with S4's precedence. Read the marketplace order from `<config>/plugins/marketplaces/<name>/.claude-plugin/marketplace.json` when present.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Drop the project-local scope: its S4 row fails. Make dirs not override installed: the override test fails. Let a non-boolean count as enabled: its shape row fails.
- [ ] **Step 6: Commit.** `feat(rigger-core): discover modules from Claude Code state and RIGGER_MODULE_DIRS`

### Task 12: Rendering placeholders and trigger tables

**Files:**
- Create: `plugins/rigger-core/tools/assemble/render.py`
- Test: `plugins/rigger-core/tools/assemble/tests/test_render.py`

**Interfaces:**
- Consumes: `TriggerRow` (Task 8), `Resolved.effective` (Task 10).
- Produces:
  - `@dataclass class RenderContext: slots: dict; rows_by_section: Dict[str, List[TriggerRow]]; vars: Dict[str, Dict[str, str]]; module: str`
  - `render(text: bytes, ctx: RenderContext) -> Tuple[bytes, List[str]]`: returns the rendered bytes and the unknown placeholders, printed as written.
  - `collect_rows(modules: List[Module], effective: dict) -> List[TriggerRow]` (spec §9.3: module rows, then config add, replace or remove). A config row without `section` gets `DEFAULT_SECTION = "skills.skill-triggers"` (D7).
  - `RenderContext.slots` is always level-1 `slots` (D11); the caller passes `Resolved.effective["slots"]`, which Task 10 keeps equal to L1.
  - `table(rows: List[TriggerRow], slots: dict) -> bytes` (D7)
  - `PLACEHOLDER = re.compile(r"\{\{([a-z]+):([^{}\s]+)\}\}")`; legacy `\{\{([a-z_]+)\}\}` is resolved only from `ctx.vars[ctx.module]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_slot_and_skill`: `{{slot:process.brainstorm}}` gives `` `superpowers:brainstorming` ``, and `{{skill:rigger-core:git-workflow}}` gives `` `rigger-core:git-workflow` ``.
  - `test_table_rows_join_and_order`: two module rows and one config row with `load: ["slot:testing", "slot:process.tdd"]` render as `b"| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `x:b` |\n| c | `rigger-core:testing`, then `superpowers:test-driven-development` |\n"`. The expected bytes are written from today's `skills.md:6-15` row shape. The full-table identity is the golden test's job (Task 15).
  - `test_triggers_line_is_replaced_whole` (D7): `b"intro\n\n{{triggers:s.t}}\nafter\n"` with two rows renders `b"intro\n\n| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `x:b` |\nafter\n"`, with no blank line added.
  - `test_empty_section_renders_nothing`: the same text with no rows renders `b"intro\n\nafter\n"`.
  - `test_unknown_placeholder_printed_and_reported`: `{{tool:x:y}}` stays and is listed.
  - `test_var_and_legacy_var`. A value that itself holds `{{slot:testing}}` prints as given: inserted text is not scanned again, as `rules_start.sh:44` promises today.
  - `test_config_row_without_section_goes_to_the_default_table` (D7).
  - `test_config_row_replace_and_remove`: an L4 row with the same id replaces the row in place; null removes it.
  - `test_backtracking_probe`: rendering `"{{" * 40 + "slot:" * 40` finishes in under 0.5 s.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** Entries are joined with `, then `, each in backticks. A row line is `| <before> | <entries> |`.
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Change the joiner to `, `: `test_table_rows_join_and_order` fails. Put config rows before module rows: the same test fails on row order.
- [ ] **Step 6: Commit.** `feat(rigger-core): render rule placeholders and trigger tables`

### Task 13: Manifests, dependencies and eval plugin lines

**Files:**
- Create: `plugins/{rigger-core,rigger-rules,rigger-lessons,rigger-talk,rigger-wp,rigger-windows,rigger-panel}/rigger-module.json`
- Modify: `plugins/{rigger-lessons,rigger-talk,rigger-wp,rigger-windows}/.claude-plugin/plugin.json` (add `"dependencies": ["rigger-core"]`); `plugins/rigger-windows/evals/*/prompt.md` (the front matter has no `plugins:` key today; add the line `plugins: ['../..', '../../deps/rigger-core']`, as the five rigger-rules cases have)
- Test: `tests/test_repo.py` (extend `ManifestTests`)

**Interfaces:**
- Produces the manifests the assembler reads. All are `schema: 1`, `layer: general`, `fills: null`.

  | Module | `rules` (file → audience), in today's `hooks.json` print order | `triggers` | `subagents` |
  |---|---|---|---|
  | rigger-core | `state-file.md` → main | 5 rows: orchestrating-subagents, git-workflow, web-ui-traps, e2e-playwright-speed, stack-tooling; ids `rigger-core.<skill>`; section `skills.skill-triggers`; `before` text copied from `skills.md:8-12` | `{}` (the omitClaudeMd frontmatter covers implementer and mapper) |
  | rigger-rules | `00-scope.md`, `english.md`, `scope.md`, `git.md`, `tests.md` → both; `verify.md` → both; `verify-main.md` → main; `plans.md` → both; `plans-main.md` → main; `defects.md` → both; `environment.md` → main; `skills.md` → both | none | — |
  | rigger-lessons | `lessons.md` → main; `lessons-subagent.md` → subagent | none | — |
  | rigger-talk | `talk.md` → main | none | — |
  | rigger-wp | `wp-skills.md` → both | 2 rows, section `wp-skills.wordpress-skill-loaded-before-the-first-step-of-its-kind` | — |
  | rigger-windows | `windows.md` → both; `windows-skills.md` → both | 3 rows, section `windows-skills.windows-skills-loaded-before-the-first-step-of-their-kind` | — |
  | rigger-panel | `[]` | none | — |

  The subagent print order of rigger-rules today is `00-scope, english, scope, git, tests`, then
  `verify, plans, defects`, then `skills`. That equals the session order with the main-only files
  removed, so one list serves both. No `requires` anywhere in phase 1: the Windows rules run on
  Linux in `test_plugin_rules` today, and no OS condition exists there today.
  `tests.paths`: `["tests/", "tools/*/tests/"]` where they exist; `contract: []`. rigger-windows
  also lists `"tools/mem-check/"`: `mem-check.tests.ps1` sits next to its tool, which
  `run_tests.py` `PATTERNS` finds through `plugins/*/tools/*/*.tests.ps1`.

- [ ] **Step 1: Write the failing tests** in `tests/test_repo.py` `ManifestTests`:
  - `test_every_plugin_has_a_valid_module_manifest`: `load_manifest(root, "rigger", "installed")` succeeds for all 7 plugins.
  - `test_rules_lists_every_rules_file_once`: the set of manifest `file` values equals the `rules/*.md` set per plugin.
  - `test_modules_with_rules_depend_on_core`.
  - `test_no_version_anywhere` updated to also scan `rigger-module.json` (8 + 7 files).
  - `test_eval_cases_load_their_dependencies`: calls `scripts/eval_suite_check.py` `check_suite(plugin_root, cases_md)` (it already reports `does not load dependency`) for every plugin with `evals/` and asserts no such problem and at least 15 cases read. No second parser of the front matter.
  - `test_tests_paths_cover_every_discovered_test_file` (D15): every file `run_tests.py` discovers under `plugins/<p>/` lies under one of that module's `tests.paths`, and every listed path exists. It asserts at least 40 files were matched.
- [ ] **Step 2: Run** `python -I -X utf8 tests/test_repo.py -v ManifestTests`. Expected: `FAILED`.
- [ ] **Step 3: Write the 7 manifests and the 4 `plugin.json` edits.** Add the `plugins:` line to the 5 windows eval `prompt.md` files.
- [ ] **Step 4: Run.** Expected: `OK`. Also run `python -I -X utf8 tests/test_eval_stage.py -v`. Expected: `OK` (staging now copies rigger-core for windows and wp).
- [ ] **Step 5: Red run.** Drop `environment.md` from the rigger-rules manifest: `test_rules_lists_every_rules_file_once` fails.
- [ ] **Step 6: Validate** with `claude plugin validate plugins/<p>` for each changed plugin, under a fresh temp `CLAUDE_CONFIG_DIR`. Expected: no error. A validator that rejects `dependencies` in rigger-talk is a finding for the owner, not a workaround.
- [ ] **Step 7: Commit.** `feat: add rigger-module.json to every plugin`

### Task 14: The assembler

**Files:**
- Create: `plugins/rigger-core/tools/assemble/assemble.py`, `plugins/rigger-core/tools/assemble/part.sh`
- Test: `plugins/rigger-core/tools/assemble/tests/test_assemble.py`, `.../tests/test_part_sh.py`

**Interfaces:**
- Consumes: Tasks 7–12.
- Produces the CLI, whose hook modes always exit 0:
  - `assemble.py --part I [--subagent]`: reads hook JSON from stdin and prints part I.
  - `assemble.py --render [--subagent] [--agent-type T] [--source S] [--session-id ID] [--mark-personal]`: whole text, no parts. Used by the golden test and the wiring tests. `--mark-personal` prints `Plugin root: personal` before personal text; it exists for the golden test (Task 15) and no hook passes it.
  - Every mode takes its inputs from the environment and the hook input: `CLAUDE_CONFIG_DIR` (else `~/.claude`), `RIGGER_MODULE_DIRS`, `HARNESS_RULES_DROP`, the project directory per D10, and its own root from `CLAUDE_PLUGIN_ROOT`, else the script's location.
  - `assemble.py --check [--modules DIR ...]`: exit 1 and one line per finding; exit 0 with `# check: modules N, rules files M, findings 0`.
  - `assemble.py --doctor`: tab-separated `CHECK` lines in doctor.sh's format.
  - Library: `assemble(inputs: Inputs, audience: str, agent_type: Optional[str]) -> Assembly`.
    - `Inputs` fields: `config_dir`, `project_dir`, `environ`, `self_root`.
    - `Assembly` fields: `blocks: List[Block]`, `notices: List[str]`, `sections: List[dict]`
      (id, layer, module, file, audience), `rows: List[TriggerRow]`, `resolved: Resolved`,
      `discovery: Discovery`.
  - `part.sh <i> [--subagent]`: runs `sh "$root/bin/py.sh" [--notice] -S "$root/tools/assemble/assemble.py" --part <i> [--subagent]` with stdin passed through, and always exits 0. Only part 1 passes `--notice`, and it turns two failures into one visible line each, wrapped as `additionalContext` JSON in subagent mode:
    - output starting `rigger: Python 3.9+ not found;` (py.sh's notice; the plugin name after it comes from `plugin.json` and is not matched) becomes `Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)`;
    - a non-zero exit status of the interpreter with empty stdout (Review Focus 7) becomes `Rigger rules not loaded: the assembler exited <status> (run /rigger-core:doctor)`.

    Parts 2..K print nothing in both cases.

- [ ] **Step 1: Write the failing tests** in `test_assemble.py`. They build a temp layout of fixture modules and run the library and the CLI through `sys.executable -I -X utf8`, reading stdout bytes.
  - `test_layer_replace_in_place_and_append`: a company section with a general id replaces it in place; a new id is appended after the general sections.
  - `test_empty_replacement_removes_section`.
  - `test_duplicate_in_one_layer_keeps_first_with_notice`: `rigger: rule id <id> in <A> and <B>; kept <A>`.
  - `test_personal_override_notice`: `rigger: <file> replaces rule tests.tests`.
  - `test_personal_text_is_not_rendered` (Review Focus 4).
  - `test_personal_override_inherits_audience` (spec §8.2): `rules.d/state-file.md` replacing rigger-core's main-only section is absent from the subagent text; `rules.d/tests.md` replacing `tests.tests` is in both; `rules.d/tests-main.md` adds a main-only section.
  - `test_personal_layer_needs_rigger_rules` (D4, Review Focus 6): with rigger-rules disabled, `rules.d` prints nothing. With rigger-rules enabled but without `rigger-module.json` (an old copy that still runs `rules_extra.sh`), it prints nothing either.
  - `test_personal_cap_and_left_out_line`: files `a.md` (5,000 bytes), `b.md` (5,000) and `c.md` (10) give `a.md` and `rules_extra: left out over the hook cap: b.md, c.md`: every file after the first one left out is left out too. In subagent mode a file of 8,000 bytes that is half `"` characters is left out, because the budget counts escaped bytes (`rules_extra.sh:92-93`), while the session prints it.
  - `test_requires_unmet_drops_file_and_row` (spec §8.3): a rules entry with `requires: ["os:<another OS>"]` or `["plugin:not-enabled"]` is not printed and its section ids are not in `Assembly.sections`; a trigger row with the same `requires` is not in the table. `slot:<x>` is met when level-1 `slots` names `x`.
  - `test_emitted_part_stays_under_the_hook_cap`: with the full-repository layout and a full `rules.d`, every `--part` stdout, subagent JSON wrapper included, is under 9,500 bytes.
  - `test_personal_filter`: `\x01` and `\r` are removed, tab is kept, trailing newlines collapse to one, an empty file is skipped, and a `x.md` directory gives `rules_extra: cannot read x.md`.
  - `test_drop_by_basename_heading_and_id`: `HARNESS_RULES_DROP="tests.md;## Plans;git.git"` removes those three and nothing in `rules.d` (D4). A heading entry matches the rendered line (D5): a fixture heading `## Git ({{skill:x:y}})` is dropped by ``## Git (`x:y`)`` and not by its raw text. A personal section that replaced `tests.tests` survives `tests.md`, `## Tests` and `tests.tests`.
  - `test_subagent_audience_and_skip`: `Explore`, `Plan`, `rigger-core:implementer` and `rigger-core:mapper` get empty output. The last two come from frontmatter: a fixture agent with `omitClaudeMd: true` is skipped and one without it is not. A manifest `subagents: {"x:y": {"rules": "none"}}` is skipped. Unparseable stdin prints the rules.
  - `test_subagent_json_is_valid_and_decodes_to_text`: `json.loads(part)["hookSpecificOutput"]["additionalContext"]` equals the main-format text of the subagent audience.
  - `test_resume_and_unknown_source_print_nothing`.
  - `test_compact_reads_snapshot`: after `--part 1` on `startup`, edit a rules file. `--part 1` on `compact` prints the old text. Delete the snapshot: `compact` prints the new text plus `rigger: snapshot missing; rules read again`, and part 1 writes the snapshot again, so a subagent started afterwards reads the same text.
  - `test_parts_concatenate_to_render`: for `i` in 1..6, the joined bodies with root lines removed equal `--render` with root lines removed, and each part is at most 9,000 bytes.
  - `test_full_repo_layout_fits_with_full_rules_d`: all 7 repository modules plus a 9,000-byte `rules.d` produce no overflow in 6 session parts or 4 subagent parts.
  - `test_check_findings`: one fixture per §8.4 finding, plus `empty heading slug` (D14), plus `0 rules files read`. Each gives exit 1 and its line.
  - `test_check_repo_is_clean`: `--check` over the repository gives exit 0 and `findings 0`.
  - `test_expects_notice` (D11): an L4 `expects: ["rigger-wp", "nope"]` with rigger-wp enabled gives one notice naming `nope` and `claude plugin install nope`.
  - `test_output_is_lf_bytes`: no `\r` in any part's stdout.
  - `test_crash_prints_one_visible_line` (spec anti-goal "never a silent fallback"). It calls `assemble.main(["--part", "1"])` with `assemble.assemble` patched to raise `RuntimeError("boom")`. Expected: return 0 and stdout exactly `rigger: rules not assembled: RuntimeError: boom (run /rigger-core:doctor)\n`. In `--subagent` mode the same line arrives as `additionalContext`. `--part 2` under the same patch prints nothing. The patch is restored in `finally`.
- [ ] **Step 2: Write** `test_part_sh.py` `test_no_python_prints_visible_line`. It runs `part.sh 1` and `part.sh 2` through real `sh` with a `PATH` holding only the sh folder and a fake `py`, `python` and `python3` that exit 1. Expected: part 1 prints the exact line and part 2 prints nothing. It uses `HOME` pointed at a temp dir and an empty `CLAUDE_PLUGIN_DATA`.
  Also `test_assembler_exit_status_prints_visible_line` (Review Focus 7): in a temp copy of rigger-core whose `assemble.py` is replaced by `raise SystemExit(3)`, and in one where the file is missing, `part.sh 1` prints `Rigger rules not loaded: the assembler exited <status> (run /rigger-core:doctor)` and exits 0, `part.sh 1 --subagent` prints the same line as valid `additionalContext` JSON, and `part.sh 2` prints nothing. Control: with the real `assemble.py` the same call prints a `Plugin root:` line.
- [ ] **Step 3: Run both.** Expected: `FAILED`.
- [ ] **Step 4: Implement** `assemble.py`, following spec §8.3: general modules in order, then company, then personal; drop; requires; render module sections only. One order differs from §8.3 as written: a heading drop entry is compared with the rendered heading line (D5), so headings are rendered before the drop is applied. On `startup` and `clear`, part 1 writes the snapshot (sections, rendered parts for both audiences, modules, effective config with sources, rows, slots, notices) and runs `cleanup`. On `compact`, parts read the snapshot. Subagent parts read the parent snapshot or recompute (S5 decision).
- [ ] **Step 5: Option values (S2 decision).**
  - **Branch A:** `vars["rigger-talk"]` is read from the store S2 recorded. A number that is not a positive integer, or an empty or multi-line language, falls back to the `talk_start.sh` defaults: `5`, `15` and the language sentence copied from `talk_start.sh:12`.
  - **Branch B:** sections of a module whose text depends on an options file are packed alone into the last session part (part 6). Parts 1–5 then never depend on option values, so parallel parts cannot disagree on packing. Only part 6 waits up to 1 s, polling every 50 ms, for `<state>/<session>.options.rigger-talk.json`. Without it, part 6 uses the defaults and appends `rigger: rigger-talk options not ready; defaults used`.
  - Add one test for the chosen branch: `test_talk_options_from_store` (A), or for B `test_talk_options_file_and_timeout` plus `test_option_sections_never_move_parts_1_to_5`. The never-arriving-file case stubs the clock and must return within its 1 s bound.
- [ ] **Step 6: Run.** Expected: `OK`.
- [ ] **Step 7: Red runs**, one per behaviour, named in the report:
  - replace in place → append;
  - remove the skip list;
  - remove the snapshot read on compact;
  - remove the D4 rigger-rules gate;
  - remove each `--check` finding branch (shared infrastructure: up to 5, chosen as duplicate, orphan, oversize section, unknown placeholder, 0 files);
  - remove each of `part.sh`'s two substitutions (fail-closed visibility: one red run per branch);
  - let the personal layer load for a rigger-rules without a manifest;
  - count the personal budget on raw bytes in subagent mode;
  - remove the top-level `try`/`except` in `main`: `test_crash_prints_one_visible_line` fails with a traceback instead of the line.
- [ ] **Step 8: Commit.** `feat(rigger-core): assemble rules from modules into part handlers`

---

## Golden, contracts and the cutover (waves E–F)

### Task 15: Frozen baseline and the golden test

**Files:**
- Create: `tests/fixtures/golden-34e7713/plugins/<p>/rules/*`, `tests/fixtures/golden-34e7713/plugins/<p>/hooks/hooks.json` for the six plugins with rules, `tests/fixtures/golden-34e7713/plugins/rigger-talk/hooks/talk_start.sh`, `tests/golden_baseline.py`, `tests/test_assembly_golden.py`

**Interfaces:**
- Consumes: `assemble.py --render` (Task 14).
- Produces:
  - `run_baseline(layout: Path, plugins: List[str], event: str, source: str, agent_type: Optional[str], env: dict) -> Dict[str, List[bytes]]`.
    For each plugin it runs every frozen rules handler (commands that name `rules_start.sh`,
    `rules_extra.sh` or `talk_start.sh`, in `hooks.json` order, for groups whose matcher fires on
    `source`) through real `sh` with `CLAUDE_PLUGIN_ROOT=<layout>/<plugin>`. It returns each
    handler's stdout. `rules_extra.sh` output is filed under `personal`.
  - `normalize(outputs: Dict[str, List[bytes]]) -> Tuple[Set[str], Dict[str, List[Tuple[str, bytes]]]]`.
    Neither handler output nor `--render` text carries file boundaries (one `--subagent` handler
    prints five files as one string), so the key is the heading line, not a section id.
    1. Decode subagent JSON first.
    2. Collect every `Plugin root: …` line into the root set and remove it.
    3. Concatenate the rest per module and cut it before each line starting `## `.
    4. Key each piece by its heading line trimmed of space, tab and CR. Text before the first heading
       of a module's stream is keyed `_preamble`. Empty pieces are dropped.

    The result is per module an ordered list of `(heading key, bytes)` (D1).
  - The new side uses `assemble.py --render` output, split per module by its root lines with
    `normalize`. A personal block has no root line, so the new side passes `--mark-personal`
    (Task 14), and `normalize` files what follows `Plugin root: personal` under `personal` and
    keeps that line out of the root set.
  - The new side discovers its modules the way a session does: the test writes a temp config dir
    with `plugins/installed_plugins.json` and `settings.json` `enabledPlugins` naming the layout
    folders, and runs `<layout>/rigger-core/tools/assemble/assemble.py` with
    `CLAUDE_PLUGIN_ROOT=<layout>/rigger-core`. One scenario names the same roots through
    `RIGGER_MODULE_DIRS` with an empty `installed_plugins.json` instead, and must give the same
    result.
  - `GOLDEN_BASE = "34e7713"`

- [ ] **Step 1: Freeze, bytes kept.** Use a pipe that keeps bytes and has no `rev:path` argument, since Git Bash rewrites the colon form: `git archive 34e7713 -- plugins/rigger-core/rules plugins/rigger-core/hooks plugins/rigger-rules/rules plugins/rigger-rules/hooks plugins/rigger-lessons/rules plugins/rigger-lessons/hooks plugins/rigger-talk/rules plugins/rigger-talk/hooks plugins/rigger-wp/rules plugins/rigger-wp/hooks plugins/rigger-windows/rules plugins/rigger-windows/hooks | tar -x -C tests/fixtures/golden-34e7713`. Never redirect through PowerShell `>`.
  Then check that the base still holds today's text: `git diff --quiet 34e7713 HEAD -- plugins/rigger-core/rules plugins/rigger-rules/rules plugins/rigger-lessons/rules plugins/rigger-talk plugins/rigger-wp/rules plugins/rigger-windows/rules && echo unchanged`. Expected: `unchanged`. Rules have not moved yet; if they have, stop and report.
  The frozen `*.sh` files are tracked, so `tests/test_shellcheck.py` lints them too. They are today's files, which pass. `.gitattributes` (`* text=auto eol=lf`) keeps the fixture LF; the CRLF scenario converts at test time.
- [ ] **Step 2: Write `test_assembly_golden.py`**, one parameterised test over a scenario list. The layout is built under `<tmp>/rigger p1 zażółć/cache/rigger/<plugin>/`, first from the fixture and then from the working tree at the same paths (D1).
  The list is not the cross product of the axes below (800 scenarios of about 20 `sh` starts each would not fit the 120 s file limit of `run_tests.py`). It is: the base scenario (`windows`, LF, no `rules.d`, no drop, talk unset) for every event value; then each other axis value alone against the base, for `startup` and for `SubagentStart` `general-purpose`; then three named combinations: CRLF with the hostile `rules.d`, a heading drop followed by `compact`, and `macos` with talk options set. That is about 35 scenarios. Baseline outputs are computed once per distinct input set.

  | Axis | Values |
  |---|---|
  | layout | `windows` (all 7), `macos` (without rigger-windows) |
  | line endings | LF, CRLF (every rules file converted in both trees) |
  | `rules.d` | absent; the hostile set (`a.md` with CRLF and `\x01`, `b-main.md`, `c.md` holding `{{slot:testing}}` and `{{reply_language}}`, an empty `d.md`, a directory `e.md`, a 9,500-byte `f.md`, then a 10-byte `g.md` that must be left out with `f.md`) |
  | drop | none; `tests.md`; `## Tests`; the `git.md` heading line as written today; `windows.md` |
  | event | `SessionStart` with `startup`, `clear` and `compact` (compact after startup in the same session id); `resume` (both empty); `SubagentStart` for `general-purpose`, `rigger-core:worker`, `Explore`, `Plan`, `rigger-core:implementer`, `rigger-core:mapper` |
  | talk options | unset; set (env for the baseline; the S2 store or the options file for the new run) |

  Assertions per scenario: `normalize(new) == normalize(baseline)`, for both the root-line set and every module's list.
- [ ] **Step 3: Add `test_accidental_override_is_the_only_difference`.** `rules.d/tests.md` holds `## Tests\n- mine\n`. The expected value is the normalized baseline with these changes:
  - rigger-rules' `## Tests` piece replaced by `## Tests\n- mine\n`;
  - the `personal` list without that piece;
  - the notice `rigger: tests.md replaces rule tests.tests` first in part 1.

  Add `test_lone_root_line_is_the_only_root_difference` (D1): with `HARNESS_RULES_DROP='## Talking to me'` the baseline root set holds rigger-talk's root and the new one does not; the root sets are otherwise equal and every module's list is equal.
- [ ] **Step 4: Run** `python -I -X utf8 tests/test_assembly_golden.py -v`. Expected on unchanged rules text: `OK`, with `Ran N tests` and N equal to the scenario count printed by the test's own `# scenarios <N>` line. Record the wall time on Windows in the report. Over 60 s, report it to Task 16, which adds the file to `run_tests.py` `TIMEOUTS` with the count of `sh` starts next to it, as that table's comment asks.
- [ ] **Step 5: Red runs.**
  - Mutate `pack`'s root line at module change (D3): a root-set mismatch must fail in a multi-module part scenario.
  - Mutate the personal trailing-newline rule: the hostile `rules.d` scenario fails.
  - Mutate `rules_start`'s CR rule in `sections.py`: the CRLF scenario fails.
  - Control that the baseline ran: assert that the `windows` layout baseline holds 19 session handler outputs and that rigger-rules has 12 identical root lines. A baseline that ran nothing cannot pass.
- [ ] **Step 6: Sweep** `python -I scripts/sweep.py --no-denylist tests/fixtures/golden-34e7713`. Expected: `findings 0`. The fixture is today's public text.
- [ ] **Step 7: Commit.** `test: golden test pins assembled rules to release 0.4.0`

### Task 16: Contract test, environment strip and eval module roots

**Files:**
- Create: `tests/test_module_contracts.py`
- Modify: `scripts/run_tests.py` (a new `DROPPED_NAMES = ("RIGGER_MODULE_DIRS",)` next to `DROPPED_PREFIXES`, which stays as it is; a `TIMEOUTS` entry for `tests/test_assembly_golden.py` only if Task 15 reported over 60 s), `scripts/run_tests.min` (raise to the file count at this commit; Tasks 20 and 21 raise it by one each), `scripts/eval_stage.py` (E1 branch), `tests/test_run_tests.py`, `tests/test_eval_stage.py`

**Interfaces:**
- Consumes: `assemble.py --check`; the E1 decision.
- Produces: `eval_stage.py` output. Branch A appends the line `RIGGER_MODULE_DIRS=<roots>`.
  Branch B writes `<staged>/deps/rigger-core/.rigger-module-dirs`, or `<staged>/.rigger-module-dirs`
  when rigger-core is the target.

- [ ] **Step 1: Write the failing tests.**
  - `test_module_contracts.py`:
    - `test_check_over_repository`: runs `--check --modules <each plugins/* with a manifest>` and asserts exit 0 and `modules 7`.
    - `test_check_catches_a_planted_duplicate`: a positive control. It copies one module to temp, adds a section with an existing id and passes `--modules`; expect exit 1 and the duplicate line.
    - `test_no_public_manifest_names_a_denylist`.
  - `test_run_tests.py`: `test_module_dirs_is_dropped_and_other_rigger_names_pass` sets `RIGGER_MODULE_DIRS` and `RIGGER_RULES_ROOT` in the parent and asserts that the child env from `run_tests` has the second and not the first.
  - `test_eval_stage.py`: `test_staged_module_roots` for the E1 branch.
- [ ] **Step 2: Run the three files.** Expected: `FAILED`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: `OK`.
- [ ] **Step 5: Red runs.** Empty `DROPPED_NAMES`: the run_tests test fails. Add `"RIGGER_"` to `DROPPED_PREFIXES` instead: the same test fails on `RIGGER_RULES_ROOT`. Remove the duplicate finding: the planted control fails.
- [ ] **Step 6: Commit.** `test: module contract test and RIGGER_MODULE_DIRS isolation`

### Task 17: harness_start reads rendered headings

**Files:**
- Modify: `plugins/rigger-core/tools/harness_start/harness_start.py:246-270`
- Test: `plugins/rigger-core/tools/harness_start/tests/test_harness_start.py`

**Interfaces:**
- Consumes: `render.render` and `sections.split` through `sys.path.insert` of `../assemble` (same plugin).
- Produces: unchanged warnings, with headings compared after rendering module placeholders.

- [ ] **Step 1: Write the failing test** `test_overlap_found_through_a_placeholder_heading`. A fixture plugin's `rules/git.md` holds `## Git (procedure and naming: {{skill:rigger-core:git-workflow}})`, and the project `CLAUDE.md` holds the rendered heading. Expected: one overlap warning.
- [ ] **Step 2: Run.** Expected: `FAILED` (raw comparison).
- [ ] **Step 3: Implement.** Render each rules file with an L1-only `RenderContext` before reading headings. A render error falls back to raw text, matching today's silence on unreadable files.
- [ ] **Step 4: Run** the whole `test_harness_start.py`. Expected: `OK`.
- [ ] **Step 5: Red run.** Skip the render: the new test fails.
- [ ] **Step 6: Commit.** `fix(rigger-core): compare CLAUDE.md headings with rendered rules`

### Task 18: Cutover (serial)

**Files:**
- Modify:
  - `plugins/rigger-core/hooks/hooks.json`: remove the `state-file.md` rules group. Add one `SessionStart` group, matcher `startup|clear|compact`, with 6 handlers `sh "${CLAUDE_PLUGIN_ROOT}/tools/assemble/part.sh" <i>` (timeout 10), and one `SubagentStart` group with 4 handlers `… part.sh <i> --subagent` (timeout 10).
  - The `hooks.json` of rigger-rules, rigger-lessons, rigger-wp and rigger-windows: remove every `rules_start.sh` and `rules_extra.sh` handler. Keep `worktree_root.sh`, `lessons_start.py` and all non-rules handlers.
  - `plugins/rigger-talk/hooks/hooks.json` and `talk_start.sh`: branch A removes the group (the file stays one release, unused); branch B rewrites `talk_start.sh` to write the options file.
  - **Only if S3 Step 6 found that rigger-core can stay behind (Review Focus 6):** create `plugins/rigger-rules/hooks/core_check.sh` and add it as one more `SessionStart` group of rigger-rules (matcher `startup|clear`, timeout 10). POSIX `sh` only, because rigger-rules ships no `py.sh`. It reads `<config dir>/plugins/installed_plugins.json`, collects every `installPath` value whose last or second-to-last path element is `rigger-core` (the folder itself in a directory marketplace, `rigger-core/<version>` in the cache; JSON `\\` read as `\`), and prints `Rigger rules not loaded: rigger-core is older than rigger-rules (run claude plugin update for rigger-core)` when it found at least one such path and none of them holds `tools/assemble/part.sh`. In every other case (no file, no rigger-core entry, a shape it cannot read, rigger-core loaded with `--plugin-dir`) it prints nothing. It always exits 0.
  - Rules text per D6: `git.md:1,4` gets `{{skill:rigger-core:git-workflow}}`; `plans-main.md:4` gets `{{slot:process.brainstorm}}, then {{slot:process.plans}}`; `skills.md`, `windows-skills.md` and `wp-skills.md` replace the header and rows with `{{triggers:<own section id>}}`; `talk.md` gets `{{var:reply_language}}`, `{{var:reply_lines}}` and `{{var:report_lines}}`.
- Modify tests:
  - `plugins/rigger-rules/tests/test_plugin_rules.py`:
    - `WiringTests` reads manifests and the core part handlers.
    - `SkillTableTests` and `TestingSkillTriggerTests` read `--render` output.
    - `CrossPluginWordingTests`, `StateFileTests` and `PlanStopTests` read rendered text.
    - `RulesSizeTests` sums the rendered general layer of all 7 modules with module options at their defaults, ceiling 27,708 (D20), minimum 19 files. `RIGGER_RULES_ROOT` keeps pointing the test at another plugins folder.
  - `plugins/rigger-rules/tests/test_rules_hooks.py`: hooks.json wiring assertions move to `WiringTests`. The direct `rules_start.sh` script tests stay, as the script ships one more release.
  - `plugins/rigger-rules/tests/test_rules_extra.py`: its behaviour cases move to `test_assemble.py` personal-layer tests. It keeps only the cases that run the still-shipped script.
  - `plugins/rigger-lessons/tests/test_lessons_rules.py:57-63`: wiring comes from the manifest; text from `--render`.
  - `plugins/rigger-talk/tests/test_talk.py`: through the assembler (the S2 branch).
  - `plugins/rigger-core/tests/test_core_wiring.py:139`: `part.sh` instead of `rules_start.sh`.
  - `tests/test_repo.py`: `RULES_SCRIPTS` gains `part.sh`. `test_plugins_hold_no_handler_besides_the_table_and_the_known_ones` keeps `moved + 11`, because `part.sh` is excluded like the other rules scripts. A new assertion counts 10 `part.sh` handlers in rigger-core and no `rules_start.sh` or `rules_extra.sh` handler in any plugin; `talk_start.sh` has 0 handlers in branch A and 1 in branch B. With `core_check.sh` the count becomes `moved + 12`, and `names.count("core_check.sh") == 1` joins the list.
  - `plugins/rigger-rules/tests/test_core_check.py` (only with `core_check.sh`): `test_old_core_prints_one_visible_line` builds a temp config whose `installed_plugins.json` names a rigger-core folder without `tools/assemble/part.sh` and expects the exact line; rows for a core that has `part.sh`, no rigger-core entry, a missing file, a file that is not JSON, a minified one-line file and a Windows path written with `\\` (run on Windows only) expect the line or silence as the rule above says. Each row runs the script through real `sh` with `HOME` and `CLAUDE_CONFIG_DIR` set to the temp dir.
  - `plugins/rigger-rules/tests/test_reinject.py`: sets `RIGGER_MODULE_DIRS` to the `INJECTED` roots; markers are the first lines of rendered sections.
  - `tests/test_shellcheck.py`: add `part.sh`.

**Interfaces:**
- Consumes: Tasks 1–16 and Task 17 (already merged into the branch).
- Produces: no rules handler outside rigger-core.

- [ ] **Step 1: Baseline green.** Run `python -I -X utf8 tests/test_assembly_golden.py -v` and `python -I -X utf8 plugins/rigger-rules/tests/test_plugin_rules.py -v`. Expected: both `OK` before any edit.
- [ ] **Step 2: Rewrite the tests listed above first.** Run them. Expected: `FAILED` on wiring (handlers still old) and on rendered-text reads where placeholders are absent.
- [ ] **Step 3: Edit the six `hooks.json` files and the rules text** in one change. Use the Edit tool for each edit. Hit counts: `git.md` 2, `plans-main.md` 1, each table file 1, `talk.md` 4.
- [ ] **Step 4: Run** the golden test, `test_plugin_rules.py`, `test_rules_hooks.py`, `test_rules_extra.py`, `test_lessons_rules.py`, `test_talk.py`, `test_core_wiring.py`, `tests/test_repo.py`, `test_shellcheck.py`, `test_drift_lint.py` and `test_harness_start.py`. Expected: all `OK`. `test_reinject.py` runs locally with `claude` (it is skipped under `--no-claude`). Expected: `OK`, with the 19-marker count unchanged.
- [ ] **Step 5: Red runs.**
  - Leave `skills.md`'s old literal table in place next to the placeholder: the golden test fails on a doubled table.
  - Remove one part handler (6 → 5) with a full `rules.d`: `WiringTests` and the overflow check fail.
  - With `core_check.sh`: make it print nothing when no path holds `part.sh`: `test_old_core_prints_one_visible_line` fails. Make it print when the file is missing: the silence row fails.
- [ ] **Step 6: Commit.** `feat!: rigger-core assembles every rules section` with the body listing the §13 exceptions, and the trailer `BREAKING-CHANGE: rules need Python 3.9+ and rigger-core; the 19 rules handlers become rigger-core part handlers.`

### Task 19: Doctor rows

**Files:**
- Modify: `plugins/rigger-core/tools/doctor/doctor.sh`
- Test: `plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py`

**Interfaces:**
- Consumes: `assemble.py --doctor` (Task 14).
- Produces these doctor rows: `rules-assembly` (OK with `modules N, sections M`, or WARN with the first finding); `rule-override` (INFO, one per override); `expects` (MISSING, one per missing module, with a FIX line `claude plugin install <name>`); `config-inactive` (INFO, one per key in `Resolved.inactive`: the key, its file and `no effect in this release`, D11); `python` on macOS, whose FIX names both `xcode-select --install` and `brew install python`.

- [ ] **Step 1: Write failing tests** in the style of the existing `doctor_sh_harness.py` fixtures: a clean layout gives `rules-assembly OK`; a `rules.d/tests.md` gives `rule-override INFO … tests.tests`; a planted duplicate gives `rules-assembly WARN`; a personal `config.json` with `leak_guard.denylist` and `slots.reviewer.model` gives two `config-inactive INFO` rows and a clean layout gives none; and macOS without Python gives both FIX lines.
- [ ] **Step 2: Run.** Expected: `FAILED`.
- [ ] **Step 3: Implement.** doctor.sh calls `sh "$root/bin/py.sh" -S "$root/tools/assemble/assemble.py" --doctor` and appends its lines.
- [ ] **Step 4: Run** all `doctor/tests/*.py`. Expected: `OK`.
- [ ] **Step 5: Red run.** Drop the call: the first test fails.
- [ ] **Step 6: Commit.** `feat(rigger-core): doctor checks rules assembly, overrides and expected modules`

---

## After the cutover (waves G–H)

### Task 20: Stub exit criteria

**Files:**
- Create: `plugins/rigger-core/tools/assemble/tests/test_stub_session.py` (skipped under `HARNESS_NO_CLAUDE=1`, like `test_reinject.py`)
- Modify: `scripts/run_tests.py` (`SERIAL`), `scripts/run_tests.min` (+1). Task 21 edits `run_tests.min` too, so the two tasks commit one after the other, not at once.

**Interfaces:**
- Consumes: `StubApi` (below), `assemble.py --render` (Task 14) for the expected first lines, and
  the S4 init-event shape.
- Produces: the two exit-criterion proofs of spec §13, run locally and labelled "stub".
- `StubApi` comes from `plugins/rigger-rules/tests/stub_api.py` (test-only import by path, as
  `test_reinject.py` does). The file joins `run_tests.py` `SERIAL`, next to `test_reinject.py`.

- [ ] **Step 1: Write the tests.**
  - `test_every_section_reaches_the_first_request`: `--plugin-dir` for the 7 plugins and the same roots in `RIGGER_MODULE_DIRS`. Every section id's first line from `--render` appears exactly once in the request that carries the probe word.
  - `test_root_left_out_of_module_dirs_drops_that_module`: rigger-wp stays loaded with `--plugin-dir` but is left out of `RIGGER_MODULE_DIRS`. The wp sections are absent and the request holds no wp root line. The rest are present. No notice is asserted (D19). The control proves the plugin did load: the run uses `--output-format stream-json --verbose`, and its init event's `plugins` array (the S4 ground truth) lists rigger-wp.
- [ ] **Step 2: Run** `python -I -X utf8 plugins/rigger-core/tools/assemble/tests/test_stub_session.py -v`, locally with `claude`. Expected: `OK`, 2 tests. The report says "stub".
- [ ] **Step 3: Red run.** Make discovery ignore `RIGGER_MODULE_DIRS`: the first test fails with sections missing.
- [ ] **Step 4: Commit.** `test(rigger-core): stub session proves discovery by RIGGER_MODULE_DIRS`

### Task 21: Startup cost

**Files:**
- Create: `scripts/startup_bench.py`, `tests/test_startup_bench.py`
- Modify: `scripts/run_tests.min` (+1), `.github/workflows/test.yml` (one step in the `tests` job, `continue-on-error: true`: `python -I -X utf8 scripts/startup_bench.py --runs 20`)

**Interfaces:**
- Produces one line, `# startup median old <ms> new <ms> delta <ms> max new <ms> runs <n>`. "Old" runs the golden fixture's 19 session handlers in parallel through `sh`. "New" runs the 6 `part.sh` handlers in parallel, on the `windows` layout with an empty `rules.d`, timing the slowest handler of each run. The script exits 0 always and prints `# startup bench failed: <reason>` on error.

- [ ] **Step 1: Write the script** and a smoke test `tests/test_startup_bench.py`, `test_prints_the_summary_line` with `--runs 2`. Its red run removes the print.
- [ ] **Step 2: Run locally on Windows** with 20 runs. Record the line in the PR body.
- [ ] **Step 3: Read the macOS and Windows CI step lines** from the PR's checks. Expected: delta at most +300 ms at the median on both, and `max new` under 5,000 ms: a part handler that reaches its 10 s timeout is killed, and that part's rules are then missing with no line to say so. If either number is exceeded, stop and report. The builder's option (spec §14) is that parts 2..K read part 1's cache when it is newer than every input; that needs the owner's go-ahead as a plan change.
- [ ] **Step 4: Commit.** `ci: measure rules startup cost on each OS`

### Task 22: Docs, release notes and migration

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`), `CONTRIBUTING.md` (the "Each rules file is printed by one `SessionStart` handler" rule becomes "rigger-core's assembler prints every section; each section stays under 9,000 bytes"), `plugins/rigger-rules/README.md` (rules.d override by id, `HARNESS_RULES_DROP` ids, D4), `plugins/rigger-core/README.md` (assembler, `RIGGER_MODULE_DIRS`, config files, doctor rows)

- [ ] **Step 1:** Write the Unreleased entries: the four §13 exceptions word for word in meaning, the Windows and macOS migration commands from spec §13 "Migration", and the measured startup delta from Task 21. Also:
  - update every Rigger plugin in one go, with what S3 Step 6 recorded: either the mechanism that keeps them together, or the line a session prints when rigger-core is older;
  - a module whose every section is dropped by heading no longer prints a lone `Plugin root:` line (D1);
  - `slots`, `trust`, `review`, `shell_guard`, `company` and `leak_guard` in a config file are read and listed by doctor but have no effect in this release (D11);
  - `HARNESS_RULES_DROP` now also takes section ids, and still does not apply to `rules.d` (D4).
- [ ] **Step 2:** `python -I scripts/sweep.py --no-denylist CHANGELOG.md CONTRIBUTING.md plugins/rigger-rules/README.md plugins/rigger-core/README.md`. Expected: `# files 4, findings 0`.
- [ ] **Step 3: Commit.** `docs: phase 1 assembly, overrides and migration`

### Task 23: Gate and pull request

**Files:**
- No new files.

**Interfaces:**
- Consumes: every task's report (mutation list, spike decisions) and Task 21's bench line.
- Produces: the PR, plus the gate `status.txt` pass line with its `tree <id>`.

- [ ] **Step 1:** Run `python -I scripts/gate.py` in the background, using the full path of the installed Python on Windows. Read `status.txt`. Expected: the last line is `# gate passed, <s> s[, skipped N], tree <id>`.
- [ ] **Step 2:** Run `python -I scripts/gate.py --verify <status.txt>`. Expected: exit 0.
- [ ] **Step 3:** Push `feature/modular-phase-1` and open the PR to `main`. The body covers:
  - the S1–S5 and E1 decisions;
  - the startup numbers;
  - the gate pass line;
  - the stub run (labelled "stub");
  - the mutation list per task;
  - the D1 note that equality is per section plus the root-line set;
  - the spec departures, one line each with its reason: D4 (`HARNESS_RULES_DROP` and `rules.d`, §8.2), D11 (`company` and `leak_guard`, §13), D19 (the exit-criterion notice, §13), D20 (the size ceiling, §3), whatever S4 corrected in §9.1, and that `--changed-modules` (§12) opens the phase-2 plan.
- [ ] **Step 4:** Wait for CI: the ubuntu gate, then macOS and Windows tests. Expected: all green, and the golden test listed as run (not skipped) in all three logs. Merging is the owner's step.

---

## Estimate

| Part | Agent hours | Code lines | Test lines |
|---|---|---|---|
| Spikes S1–S5, E1 (Tasks 1–6) | 5 | 0 in repo (about 300 in scratch) | — |
| Assembler package (Tasks 7–9, 11, 12, 14) | 9.5 | 970 (sections 70, manifest 150, discover 170, render 90, parts and snapshot 90, assemble and part.sh 400) | 1,100 |
| Config resolver, defaults, manifests, plugin.json (Tasks 10, 13) | 2 | 480 (config 220, defaults 60, 7 manifests 200) | 200 |
| Golden (Task 15) | 3 | — | 450 (plus about 700 lines of frozen fixture, copied) |
| Contracts, env, eval_stage (Task 16) | 1 | 40 | 120 |
| Cutover and test rewrites (Task 18) | 4.5 | 110 (hooks and rules edits 80, `core_check.sh` 30 if S3 asks for it) | 500 |
| harness_start, doctor, bench, docs (Tasks 17, 19, 21, 22) | 2.5 | 150 | 160 |
| Stub run, gate, PR (Tasks 20, 23) | 1.5 | — | 120 |
| **Total** | **29** | **1,750** | **2,650** |

The review raised the first draft's 25 h, 1,550 and 2,390:
- `assemble.py` and `part.sh` from 250 to 400 lines: they hold layering, audience, drop, the port of `rules_extra.sh` (113 lines of `sh` today), the subagent skip with front-matter reading, option values, the snapshot flow and four CLI modes;
- the cutover from 3 h to 4.5 h: it rewrites tests in files that hold about 1,300 lines today (`test_plugin_rules` 318, `test_rules_hooks` 375, `test_rules_extra` 205, `test_lessons_rules` 158, `test_talk` 127, `test_reinject` 148);
- the spikes from 4 h to 5 h for the two size runs and the version-skew step;
- about 260 test lines for the failure modes of Review Focus 6–8 and the D4, D7 and D11 cases.

Against the spec's 16 h, 1,250 and 1,600, the differences are:
- the spikes, which the spec's figure did not price;
- spike E1;
- the cutover touching 10 test files rather than the 5 classes the spec lists (`CrossPluginWordingTests`, `test_rules_hooks`, `test_rules_extra`, `test_lessons_rules`, `test_talk`, `test_core_wiring`, `test_reinject`, `test_shellcheck`, `test_repo`, `harness_start`);
- the golden list covering CRLF, hostile `rules.d` and talk options;
- the port of `rules_extra.sh` and the visible-failure paths, which the spec's 600 assembler lines did not count.

**Simpler version, about 23 h, 1,400 code lines, 2,150 test lines:**
- **What it keeps:** manifests, sections, discovery, level-1 defaults only, rendering, parts, the
  cutover and the golden test.
- **What it drops:**
  - the level 2–4 resolver (moved to phase 2), so no config file has any effect in phase 1;
  - the snapshot, so `compact` re-reads files and "read once" is lost;
  - the stub exit-criterion test file (one manual stub run instead);
  - the CI bench step (Windows-only local measurement).
- It still meets "no behaviour change", but not the spec's G6 and §9.4 for this release.
- It saves about 6 h and 350 code lines (resolver levels 2–4 about 180, snapshot and the compact
  path about 80, bench 60, the doctor rows for `expects` and inactive keys 30), not half: the
  spikes, the cutover and the golden test are the cost, and both versions need all three.

## Open risks

1. **Decided in review, keep.** D4 keeps `HARNESS_RULES_DROP` off `rules.d`, which departs from
   spec §8.2 ("every layer"). The variable is an experiment switch for the rules Rigger ships,
   `plugins/rigger-rules/README.md:50-51` documents that it skips `rules.d`, and spec §13
   "Migration" promises both keep working unchanged. The spec sentence is corrected to "every
   module layer" in the PR body. If the owner wants personal text droppable, that is a behaviour
   change with its own release note, in phase 4 next to the company layer.
2. **Decided in review, keep.** D11 reads `company` and `leak_guard` as record-only in phase 1.
   The spec's sentence can also be read as "these four have effect", but their only readers are
   the client-context rule (§11.3) and the leak guard (§11.4), both phase-4 scope, so there is
   nothing for them to change yet. The gap a person would hit is closed another way: doctor
   lists every key that is set and not read (`config-inactive`).
3. **Decided in review.** `run_tests.py --changed-modules` (§12) is the first task of the phase-2
   plan, as its own pull request (D15). Phase 1 only proves the manifests' `tests.paths`.
4. S2 branch B adds a wait of up to 1 s at startup. Parts that read the options file at different
   moments could pack differently, so a section could appear twice or not at all. Branch B
   therefore packs option-dependent sections alone into the last part (Task 14, Step 5). That costs
   one part of headroom.
5. Managed settings scopes are not probed (S4).
6. If the validator rejects `dependencies` on talk or wp, the §13 exception cannot ship as written.
   The three raw readers of D17 see placeholders until phase 2.
7. Version skew (Review Focus 6). `core_check.sh` covers only rigger-rules next to an old
   rigger-core that sits in `installed_plugins.json`. A new rigger-talk, rigger-wp, rigger-lessons
   or rigger-windows next to an old rigger-core, without rigger-rules, loses its sections with no
   line. The release notes say to update every plugin together.
8. A part handler killed at its 10 s timeout leaves that part's rules out with no line; nothing
   inside the handler can report its own kill. Task 21 measures the slowest handler against half
   the timeout, and that is the only guard.
9. A `rules.d` file whose stem and heading slug equal a shipped section's now replaces it (spec
   §13 exception). `rules.d/lessons.md`, which rigger-lessons writes, shares its stem with
   `rigger-lessons/rules/lessons.md`; only a topic heading that slugs to
   `learn-continuously-without-being-asked` would collide, and the override notice names it.
