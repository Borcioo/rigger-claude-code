# Changelog

All notable changes to this marketplace are listed here, newest first, in the Keep a Changelog
format. Releases are tagged `v<version>`.

## [Unreleased]

### Migration

- Install the new plugins one per call (`claude plugin install` installs only the first plugin it is
  given, exits 0 and says nothing about the rest), update rigger-core (an install adds or re-enables it
  but leaves an installed one at its old version), then run `/rigger-core:doctor`:

  ```text
  claude plugin install rigger-shellwise@rigger
  claude plugin install rigger-platform@rigger
  claude plugin install rigger-testing@rigger
  claude plugin install rigger-review@rigger
  claude plugin update rigger-core@rigger
  ```

  `rigger-testing` and `rigger-review` are already there if you took 0.7.0. `rigger-platform` matters
  on Windows only (its rule and skills require it); macOS and Linux may skip it.
- **From rigger-windows:** while it is enabled, every session start prints
  `rigger: rigger-windows is still enabled; disable it (claude plugin disable rigger-windows@rigger)`.
  Run that command, then uninstall the plugin. If you had added its shell-guard hook to
  `settings.json` by hand, remove that entry; with rigger-shellwise installed it would run twice.
- **No binary to install by hand.** rigger-shellwise downloads its pinned binary in the background at
  its first session start (see "Updating shellwise" in `CONTRIBUTING.md`). Until the download is ready,
  and on a system with no asset for it, that session prints
  `shellwise: no binary (<reason>); commands run unguarded` and commands run. Doctor shows the
  `shell-guard` slot with its state and `fails closed`. `SHELLWISE=off` in the environment switches the
  guard off; `shell_guard.shellwise_path` (personal config, an absolute path) replaces the downloaded
  binary when its `--version` is at or above the pin.
- **Gate rows:** a repository's `.claude/gates.json` keeps its format. Shadow rows and gate-lab data now
  start in rigger-testing's data folder; rows written before stay in rigger-windows' data folder, and
  `replay.py` and `report.py` read them when given that folder with `--data` (and `--from-shadow`).
- **Herd docs:** move `<config dir>/rigger-windows/herd-docs/` to `<config dir>/rigger-platform/herd-docs/`;
  the old folder is still read for one release when the new one is absent.
- **Allow marker:** a command that must pass the guard carries `# shellwise: allow`. The old
  `# shell-guard: allow` marker is not accepted.

### Changed

- **Shell guard.** The `shell-guard` slot defaults to `rigger-shellwise` on every system, so macOS and
  Linux get a shell guard for the first time. The Python guard and its 8 PreToolUse handlers are gone.
  Commands are checked by shellwise `v0.1.1`: all 24 old rule ids have a counterpart, and
  `bash-ps-call-operator`, which the rule map listed as dropped, is a rule in the release (PowerShell's
  call operator `& "path"` typed in a Bash command). `shellwise.toml` is the release's example config with
  `gate_commands = ["safe-edit*", "safe_edit*"]`; it raises 11 rules and turns on `git-no-verify`.
  Where the real binary differs from the rule map: on Windows only `bash-ps-call-operator` (denied now,
  the map said dropped); on Linux 12 rows answer differently, because the Bash tool is `bash` there and
  the Git Bash rules (`bash-backslash-collapse`, `bash-cd-slash-d`, `bash-msys-path-mangle`,
  `bash-powershell-syntax`, `bash-unquoted-backslash-path`, `bash-vendor-bin-shebang`,
  `bash-versioned-php`) do not apply, `bash-too-long` is `inline-script-too-long`, and `bat-metachar`,
  `ps-bad-parameter` (`ls`) and `ps-bare-bash` are Windows facts.
- **Known gap.** `gate-truncated-pipeline` fires in Bash for `| tee log | head` always, and without
  `tee` only when the producer's program name matches `gate_commands`. A gate started as
  `python -I scripts/gate.py | head` or `python safe_edit.py … | head` is not caught: a script host
  other than pwsh is not matched.
- **Commit shadow.** The commit rule moved to rigger-testing (`tools/test-evidence/commit_shadow.py`,
  one handler for Bash and one for PowerShell on `*commit*`). It writes its row also for a command the
  guard denies. `TEST_EVIDENCE=off` turns the two handlers off.
- **mem-check** is a rigger-core tool and runs on Windows, Linux and macOS. `load.min_free_gb` and
  `load.max_heavy_agents` (config levels 1 to 4) set its limits; a project file may only make them
  stricter.
- **safe-edit** is `python safe_edit.py` in rigger-testing, with the same options as long flags
  (`--path --find --replace --count --run --expect-ran --working-directory --restore`), the same exit
  codes and messages; the rules name it through a placeholder.
- **drift-lint** is a rigger-core tool and gains the check `trigger-load` (a trigger row whose `load`
  names a skill no module has). The stop-shadow word lists are data in
  `rigger-testing/tools/test-evidence/phrases.json`.
- **Config keys:** `test_evidence.phrases` and `drift_lint.remote_commands` / `drift_lint.project_bins`
  (levels 2 and 3) add to those lists; `lessons.allowed_paths` (levels 2 and 3) adds allowed paths to
  the lessons lint; `turn_guard.enabled` (levels 2 to 4, default `true`) is below.
- **Lanes and state.** `/rigger-core:coordinate` runs one session per lane, with standby sessions for
  later parts. The state file's work items are changed through `tools/state/state.py`; a session start
  archives idle sessions' state files (14 days), deletes archived ones (60 days), rotates logs, gives a
  resumed session its files back and names stale items (a merged PR, a branch that is gone).
- **Turn guard.** A session's turn end is held while its own background gate or test command runs
  (`gate.py`, `run_tests.py`, `pytest`, `python -m unittest`, `npm test`, `go test`, …): the Stop hook
  waits up to 4 s, then blocks with one line naming the run, at most 3 times per task. It never holds a
  coordinator, a `claude -p` run or a session with `turn_guard.enabled` set to `false`. In a config
  with no state folder it makes the folder for its block count; when the count still cannot be written,
  it holds the first Stop of a chain, and allows the chained one with a one-line note that the block count
  cannot be written and the turn ends without holding again, so a turn is held at most once.
- **Review and testing modules.** A command reviewer's `{scope}` carries the `review.paths` results; both
  modules print the install line above when rigger-core is missing. `--check` and doctor report a
  company or personal section that replaces an id the lower layer no longer has (`stale override`), and
  a plugin whose dependency is not installed or not allowed across marketplaces (`dependency` row).
- **Rules text.** The lo-fi trap moved into `web-ui-traps`; three testing and orchestration sentences, one
  verification sentence and the WordPress hunk-list sentence were added.
- **Verification.** The exit criteria ran against a stub, not a real model:
  `docs/superpowers/spikes/phase-3b/exit-criteria.md`. The one download of the pinned asset ran on
  Windows and under WSL.
- `rigger-windows` is gone (phase 3b): its Windows rule and skills are `rigger-platform`, its shell guard is
  `rigger-shellwise`, `mem-check` is a `rigger-core` tool, and `safe-edit.ps1` is `safe_edit.py` in
  `rigger-testing`. The rules and skills name those modules.
- Every plugin that needs rigger-core, the review and testing modules included, prints one line at session
  start when it is installed from the marketplace and no rigger-core is installed:
  `Rigger rules not loaded for <plugin>: rigger-core is not installed (run claude plugin install rigger-core)`.
  A `--plugin-dir` or directory-marketplace run stays silent.

## [0.7.0] - 2026-11-25

### Migration

- Update every Rigger plugin in one go, as for 0.5.0, and install the two new modules, one plugin
  per call (`claude plugin install` installs only the first plugin it is given, exits 0 and says
  nothing about the rest):

  ```text
  claude plugin install rigger-review@rigger
  claude plugin install rigger-testing@rigger
  claude plugin update rigger-core@rigger
  ```

  Then run `/rigger-core:doctor`. Both modules declare `dependencies: ["rigger-core"]`, so an install
  adds or re-enables rigger-core, but it leaves an installed rigger-core at its old version: the
  update line is what brings it up to date.
- **Without the modules**, every session start, `/clear` and compaction prints one line per module:
  `rigger: slot reviewer is empty: install rigger-review (claude plugin install rigger-review@rigger)`
  and `rigger: slot testing is empty: install rigger-testing (claude plugin install rigger-testing@rigger); the commit-time weak-test guard is off`.
  Doctor shows `slot reviewer` and `slot testing` as `MISSING` with the same command as the FIX.
  A personal or project slot entry that still names `rigger-core:reviewer` or `rigger-core:testing`
  now falls back with `rigger: slot <slot> from <file> ignored: …; the slot is empty`, followed by
  `; install <plugin> (<command>)`: point it at `rigger-review:reviewer` or
  `rigger-testing:testing`, or remove it.
- **Version skew:** the new testing module next to an old rigger-core runs the weak-test guard twice
  (both plugins register it). At startup the old rigger-core prints
  `rigger: hook runs 2 times (…): from plugin rigger-core@rigger, plugin rigger-testing@rigger; disable one of the plugins`.
  Update rigger-core instead of disabling either plugin; the line then goes away.
- **An external reviewer command** (a `reviewer` slot of kind `command`) reads `{scope}` lines whose
  verdict may now be `review: binary added` or `review: rules file newly named`: treat every verdict
  that starts with `review` as a path to review. A `review: rules file newly named` path is a rules
  file the head's `.claude/rigger.json` names and the base's does not; it is listed even when the diff
  leaves it unchanged, so `git diff` gives no hunk for it: read the whole file. A `review` path may be one the diff deletes: `.claude/rigger.json` and the rules files
  it names are reviewed even when deleted. Its reply is refused under the new rule
  `coverage-test-skipped` when `coverage.skipped` holds a test file, `.claude/rigger.json` or a rules
  file it names that `review_scope.py` lists for review; the contract id stays `rigger.reviewer/1`.

### Changed

- **Command slots run with `PYTHONSAFEPATH=1`, and `PYTHONPATH` cut like `PATH`.** A slot command
  started as `python -m <tool>` or `python -c` no longer imports a module a repository plants at its
  root (Python 3.11+), through the working directory or an inherited `PYTHONPATH` entry: empty, `.`,
  relative and in-repository entries are dropped, and the variable is removed when none is left.
  `PYTHONUSERBASE` and `PYTHONPYCACHEPREFIX` are removed when relative or inside the repository or
  the working directory, so a Python child runs no `usercustomize` and loads no cached bytecode from
  there. A
  script's own folder is off `sys.path` too: a reviewer script that imports a sibling module adds its
  folder to `sys.path` itself. Known limits: `-E` written in the slot's argv makes Python ignore
  `PYTHONSAFEPATH` (`-I` keeps the working directory off `sys.path` by itself); Python before 3.11 and
  other interpreters that resolve modules from the working directory can still load the repository's
  code; on Windows the DLL search of the command's process can still read the working directory.
- **A module plugin now checks for a rigger-core that holds `rules/skills.md`.** An 0.5.0 core next
  to newer modules lost the Skill triggers table without a notice; it now gets the "rigger-core is
  older than" line.
- **A slot dropped by its own `requires` no longer satisfies another slot's `slot:` requirement.**
- **The reviewer and the testing skill moved out of rigger-core.** The `reviewer` slot defaults to
  `rigger-review:reviewer` and the `testing` slot to `rigger-testing:testing`. The reviewer agent and
  its contract fixtures come with the review module; the `testing` skill, the commit-time weak-test
  guard (its two `PreToolUse` handlers on `Bash(*commit*)` and `PowerShell(*commit*)`, unchanged)
  and the `testing-skill-loads` eval with its control come with the testing module. rigger-core no
  longer ships either.
- **An empty `reviewer` or `testing` slot at its default names the install command** at `startup`,
  `clear` and `compact`, and doctor reports it as `MISSING` (see Migration). Every other level-1
  default whose plugin is not enabled stays silent, as before.
- **`review_scope.py` gives skip reasons.** First match wins: `review` for `.claude/rigger.json`
  and every rules file it names at base or at head, whatever a skip says; `skip: deleted`; a
  `review.paths` skip, `skip: <reason> (review.paths)`; a file named like a test (`test_*`,
  `test-*`, `*_test.*`, `*.test.*`, `*.tests.*`, `*.spec.*`, `*_spec.*`, `*Test.*`, `*Tests.*`,
  case-sensitive) is `review`; `skip: binary` when the first 8,000 bytes hold a NUL byte at head and
  at base; `review: binary added` when only the head holds one (a binary file the diff adds or
  creates is reviewed); a file under a test folder (`test`, `tests`, `__tests__`, `spec`, or a
  folder ending in `.Test` or `.Tests`, in any case) is `review`; `skip: lockfile`,
  `skip: vendored` (under `vendor`, `node_modules`, `third_party` or `third-party`) and
  `skip: generated` (`@generated`, `DO NOT EDIT` or `auto-generated` in the first 5 lines, or
  `*.min.js` and `*.min.css`) only for a path that was a regular file at base; everything else
  `review`. A skip the reviewed diff itself would create never applies. A rules file the head config
  names and the base config does not is listed as `review: rules file newly named` even when the diff
  leaves it unchanged.
- **`review.paths` in config takes effect** and is no longer listed as `config-inactive`. Each entry
  maps a glob (`**`, `*` and `?` by path segment, case-sensitive) to a rules file, or to
  `{"rules": <file>, "skip": <reason>}`; at most 50 entries per file, a glob and a reason at most 200
  characters, a bad entry dropped with one notice. Personal and company entries come from the
  session's snapshot; project entries, and the rules files they name, are read from
  `.claude/rigger.json` at the reviewed diff's base, never from the working tree.
  `review_scope.py --base REV [--head REV] [--repo DIR] [--session ID] [--rules-out DIR]` prints,
  after the path lines, `# review.paths: …` notices (`none (no snapshot)` without a session snapshot;
  `project entries read at base; <file> changed in this diff`), then one
  `# rules <glob> <file> (level <n>)` line per entry that matched a path to review. A project rules
  file is written to `--rules-out` (`(not written: no --rules-out)` without it). `--session`
  defaults to `CLAUDE_CODE_SESSION_ID`.
- **`slot_check.py reviewer` refuses a skipped test file** (`coverage-test-skipped`): a test file,
  `.claude/rigger.json` or a rules file it names, in `coverage.skipped`, whose `review_scope.py`
  verdict starts with `review`. It takes
  `--session ID` (default `CLAUDE_CODE_SESSION_ID`) to read the same personal and company
  `review.paths` entries; without a session, a test file only those entries skip is refused.
  `slot_run.py` checks a command reviewer's output without a session. A command reviewer's
  `{scope}` carries no `review.paths` result (no named skip and no rules file), and since its output
  is checked without a session, a test file that only a personal or company entry skips is refused.
- **A Risk-full change gets a second fresh review round** after the first review's fix round
  (`orchestrating-subagents`, `/rigger-core:coordinate`). The second reviewer gets the first
  review's findings with their outcomes and the fix diff and looks only for what the first missed;
  its Critical and Important findings go through a fix round and a re-review, never a third fresh
  round. A finding is marked `disputed` only with a quoted `file:line` that proves it wrong. With the
  `reviewer` slot empty, the review is blocked and reported as `BLOCKED` with the install line from
  the session notice, or the `slot reviewer` line `/rigger-core:doctor` prints when there is none; it is never skipped or given to an agent outside the slot.
- **The background lessons batch reads the rendered rules**, as `curate-lessons` does: it runs
  rigger-core's `assemble.py --render` from the `installPath` in `installed_plugins.json` (30 s,
  1,000,000 bytes) and falls back to raw text with one `rendered rules: unavailable (<reason>)` log
  line.
- **`slot_run.py` refuses a program it finds under the repository or the working directory by file
  identity** too, not only by spelling, so another spelling of the same folder (an admin share, a
  case twin) is caught.

### Added

- **`python -I scripts/gate.py --changed-modules [--base REV]`** is the gate of a change whose every
  path lies in module folders: the changed modules' tests, their dependents' and the tests that read
  them, here and under WSL from one changed-file list the host writes (`run_tests.py
  --changed-from FILE`, which runs no git). `status.txt` records `mode: full` or
  `mode: changed-modules …`; `gate.py --verify` prints `gate result matches: tree <id> (full)` or
  `(changed-modules: <names>)`, and refuses a changed-modules status with
  `verify: changed-modules gate, but <path> is shared; run the full gate`. A release and a change with
  a shared path take the full gate; CI is unchanged.
- **`tests/read_by.json`** names the modules each test file reads besides its own, and
  `tests/test_read_by.py` fails on a read the map does not declare.
- **`scripts/hook_latency.py [--runs N] [--json FILE]`** times every hook handler the way Claude Code
  starts it (Git Bash `bash.exe -c` on Windows, `/bin/sh -c` elsewhere) against that shell's floor.
- **Review module (`rigger-review`):** the `review` skill (the procedure the agent loads first: run
  `review_scope.py`, read the rules files its `# rules` lines name, load up to three checklists) and
  `checklists/` for Python, TypeScript and JavaScript, PHP and shell.

Phase 3b moves rigger-windows' test tools (the test-evidence shadow gates, gate-lab and safe-edit)
into the testing module; until then they stay in rigger-windows, unchanged.

## [0.6.0] - 2026-11-24

### Migration

- Update every Rigger plugin in one go, as for 0.5.0, then run `/rigger-core:doctor`.
- **A trigger table copied into `rules.d`** (text taken from the old `skills.md`) moves to `triggers`
  in `<config dir>/rigger/config.json`. Doctor prints one `rules-d-table rules.d/<file>` row per such
  file, ending with the JSON of its rows: a row whose `before` equals a shipped row keeps that row's
  id, every other row gets `personal.<file stem>-<n>`. Merge the JSON into `triggers`, then delete the
  table from the `rules.d` file.
- **Two section ids changed:** `windows-skills.windows-skills-loaded-before-the-first-step-of-their-kind`
  is gone (its rows are in `skills.skill-triggers`), and
  `wp-skills.wordpress-skill-loaded-before-the-first-step-of-its-kind` is now
  `wp-skills.wordpress-site-copies`. A `rules.d` section or a `HARNESS_RULES_DROP` entry with an old
  id now matches nothing.
- **`## Slots` (`slots.slots`) cannot be overridden:** a `rules.d` section with that id is a duplicate,
  and the session gets `rigger: rule id slots.slots in … ; kept …` with the table unchanged.
- **A command slot's program** is found on `PATH` outside the repository and is never a `.cmd` or
  `.bat` file: name an npm tool as `node <script>` and a tool inside the repository (a virtual
  environment in the working tree, say) as `<interpreter outside the repository> <script>`. The
  refusals read `slot-run: refused: <name> is a batch file; name the program it starts (for an npm tool: node <script>)`,
  `slot-run: refused: <name> not found on PATH (entries inside the repository are not searched)` and
  `slot-run: refused: <name> resolves inside the repository`; the last two apply to the working
  directory as well.
- **A shell-guard deny now starts `shell-guard [<rule>]:`**, not `shell-guard:`. Update anything that
  matches on the old start.
- **A trusted project command is printed:** a session in which a project's command fills a slot gets
  `rigger: slot <slot> runs the project's command <argv> … (<n> bytes, sha256:<12 hex digits>) (trusted in <config file>)`,
  the argv as one line of ASCII JSON cut to 200 characters, at `startup`, `clear` and `compact`. A
  changed command gives a changed hash.
- **`run_tests.py --changed-modules` is a local pre-check:** a pull request and a release still need
  the full gate.

### Changed

- **One `## Skill triggers` table instead of three**, printed by rigger-core (`skills.md` moved there
  from rigger-rules and keeps the id `skills.skill-triggers`), to the session and to subagents. A
  setup without rigger-rules gets it too. Row order: rigger-core's, rigger-windows', rigger-wp's, the
  slot rows (plans, testing, debugging), then config rows.
- **Rows of plugins that are not enabled are dropped:** a `load` entry whose plugin is not enabled,
  or whose slot is empty, leaves the row, and a row with no entry left disappears. Without
  superpowers the testing row reads `rigger-core:testing` alone. A `RIGGER_MODULE_DIRS` entry that
  holds only `.claude-plugin/plugin.json` counts as an enabled plugin, with no notice.
- `## Windows skills, loaded before the first step of their kind` is gone. rigger-wp's section is
  `## WordPress site copies` and keeps only its sentence about site copies.
- **New `## Slots` section** in the main session, after rigger-core's rules: one row per slot
  (`Slot`, `Kind`, `Target`, `Model`, `From`: `default`, `company:<module>`, `personal` or
  `project`). An empty slot shows `—`; a hook slot lists its fillers and their failure mode
  (`rigger-windows (fails open)`), and fillers that declare both `open` and `closed` read
  `failure mode: closed (fillers disagree)`. When a slot is a command, one line follows for the Bash
  tool: `sh '<core root>/bin/py.sh' '<core root>/tools/slot-run/slot_run.py' <slot> --session <id> --base <base revision>`.
  The default set measures 717 bytes on Windows (cap 1,200).
- **`slots` (personal, project and company config) and `trust` (personal config) take effect.** A
  slot entry that does not validate falls back level by level, with
  `rigger: slot <slot> from <file> ignored: <reason>; using <level>` (or `; the slot is empty`). A
  default whose plugin is not enabled leaves its slot empty without a notice (doctor lists it). A
  `null` for one slot empties it (a hook slot stays filled while its plugin's hooks run); a `null` for `slots` or `slots.process` is ignored with a notice. A
  project file may not set `kind` or `target` of a slot whose merged entry is a command unless the
  personal file lists that slot in `trust.project_commands`. A hook slot is filled by every enabled
  plugin whose manifest says `fills: <slot>`; two fillers, or an expected filler set in config that is
  not enabled, give one notice line each. `review`, `shell_guard`, `company` and `leak_guard` are
  still listed by doctor as `config-inactive`.
- A rules part above 1 that fails prints `Rigger rules part <i> of <K> not loaded: <reason> (run /rigger-core:doctor)`.
- The five `core_check.sh` handlers run on `startup|clear|compact`: a line printed at startup is gone
  after a compaction unless a `compact` handler prints it again (spike Q1).
- **Trigger-row caps:** at most 50 trigger rows per config file or company module; the rest are
  ignored with `rigger: config <file> holds <n> trigger rows; the last <m> ignored (at most 50 per file)`.
  `rigger-defaults.json` is exempt. A `load` entry holds at most 200 characters, in a manifest and in
  a config file; a longer one makes its row invalid (`load entry over 200 characters`). When the
  merged table would not fit one hook part, config rows leave it, project rows first, then personal,
  then company, each from its last row up; a dropped override brings back the row it replaced, and one
  line names the count and the files. Module rows always stay.
- Rules text names slots, not fixed agents: `orchestrating-subagents`, the `worker` agent,
  `/rigger-core:coordinate` and the implementer brief point at the `## Slots` and Skill triggers
  tables, and the `reviewer` agent ends its reply with the `rigger.reviewer/1` JSON in a fenced
  `json` block. In module rules text, a sentence that names an empty slot (`{{slot:<path>}}`) is left
  out; `rules.d` text keeps placeholders as written.
- The resolved snapshot is format 2 and holds the slot rows and the trusted list; a snapshot of
  format 1 reads as missing.

### Added

- `tools/slot-run/slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]` runs a
  command slot from the session's snapshot only, with no shell, from the repository root, with
  `CLAUDE_*`, `HARNESS_*`, `GIT_*` and `RIGGER_MODULE_DIRS` removed and stdin closed. Bounds: 900 s,
  1,000,000 bytes of console output (kept in `console.txt`) and a 5,000,000-byte output file. A
  command that exits while a process it started still holds the console after 5 s is refused with
  `child process left running`, and every run ends by stopping the whole process tree. The output is
  checked with `slot_check.py`; the last line is `slot-run: ok <slot> <n> findings` or
  `slot-run: refused: <reason>`. An untrusted project command, a missing snapshot and a slot that is
  not a command are refused.
- `tools/slot-check/slot_check.py <slot> <file>` checks a filler's reply against its slot's contract
  (reviewer, worker, implementer, mapper, shell-guard, lessons) and prints `slot-check: ok <slot>` or
  `slot-check: refused: <rule id>: <reason>`. The reviewer contract `rigger.reviewer/1` is a JSON
  object, alone or in the reply's last fenced `json` block: status, findings with a quote anchored at
  the head commit, coverage equal to `review_scope.py`'s list, skip reasons and shortcuts. Unknown
  keys are ignored.
- `tools/review-scope/review_scope.py --base REV [--head REV] [--repo DIR]`: the one list of files a
  review covers (`git diff --no-renames` between two commit ids), each `review` or `skip: deleted`.
  Git is found on absolute `PATH` entries outside the repository and the working directory and runs
  without any `GIT_*` variable.
- Contract fixtures in `tests/contract/<slot>/` of the filling plugin, run by
  `tests/test_module_contracts.py`, with the default fillers.
- Manifest fields `fills`, `failure_mode` (`open` or `closed`), `provides` (`shell-guard.rule_checker`)
  and `tests.contract`. rigger-windows fills `shell-guard` (fails open), rigger-lessons `lessons`.
- `/rigger-core:doctor` rows: `slot <path>` (OK with its target, WARN for two fillers with FIX
  `claude plugin disable <name>`, MISSING for an expected filler that is not enabled, INFO for an empty
  slot and why), `slot-checker shell-guard` (the filler's rule checker), `slot-order <id>` (WARN: a
  config row loads `process.tdd` without `testing` before it) and `rules-d-table rules.d/<file>`.
- `assemble.py --check` also fails on a slot default that does not validate, a second trigger table,
  a `{{slot:<path>}}` whose default plugin is absent, and a row that loads `process.tdd` without
  `testing` before it.
- The report shadow's rows gain `slot` and `contract` (`ok`, `refused:<rule id>`, `none`, `unread`,
  `error`: the slot's contract checked on the reply's shape only), and `--summary` lines end with
  `contract-ok N contract-refused N`.
- rigger-lessons `proposal_check.py --rules-text <file>` reads rendered rules text
  (`assemble.py --render`); `curate-lessons` passes it. The background batch still reads raw text.
- `run_tests.py --changed-modules [--base REV]`: runs the tests of the changed plugins, of the plugins
  that depend on them and four repository-wide files, then the sweep; a shared file exits 2.
- Spikes Q1–Q4 (`docs/superpowers/spikes/phase-2/`):
  - Q1: a startup line does not survive `/compact` unless reprinted, so `core_check.sh` runs on
    `compact`.
  - Q2: `SubagentStop` carries the parent's session id and the whole reply, so the shadow reads the
    parent's snapshot and the event text, never the transcript, for its contract verdict.
  - Q3: a command the model starts in the Bash tool sees `CLAUDE_CONFIG_DIR` and the session id, and
    the `sh` line arrives whole there, so the `## Slots` line names the Bash tool and carries the id.
  - Q4: the operating system's lookup can run a program planted in the repository, a `.cmd` shim
    re-parses `&`, and a polled output file does not bound output on Windows, so `slot_run.py` has its
    own lookup, refuses batch files and counts console bytes on a pipe.

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
