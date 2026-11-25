# Phase 3b design: what the second release of phase 3 builds

This file adds to `docs/superpowers/specs/2026-11-25-phase-3-decisions.md` (the decisions doc) and,
through it, to §13 "Phase 3" of `docs/superpowers/specs/2026-11-23-modular-architecture-design.md`.
Where they disagree, this file wins for 3b. The plan is
`docs/superpowers/plans/2026-11-25-modular-phase-3b.md`.

Spelling: where a `:` or a `plugins/` path follows, this file writes `rigger-SHELLWISE` and
`rigger-PLATFORM` (the two plugins do not exist yet, and `tests/test_plugin_refs.py` checks every
qualified name). In plain text they are "the shellwise module" and "the platform module".

## Intent

3b changes what a person has installed: the Windows-only plugin goes, a shell guard that works on
all three systems takes its slot, and the memory check that the orchestrator reads before a heavy
agent works on every system. Beside that, it closes four gaps that running 3a showed in the
harness itself: the coordinator splits plans by session instead of by lane, the state file is
edited by hand and grows without bound, a side session ends its turn while its own gate still
runs (the coordinator then gets an empty note), and the 3a backlog.

## Goals

1. The shellwise module fills `shell-guard` on Windows, macOS x64 and arm64, and Linux x64, with a
   pinned, hash-checked shellwise binary that nobody installs by hand (decisions doc D2).
2. The platform module carries the Windows and toolchain skills and the Windows shell rule.
3. `mem-check` is a Python tool in rigger-core for all three systems, with two config keys (D3).
4. `safe-edit` is a Python tool in the testing module (D4); the test-evidence shadow gates and
   gate-lab move there too.
5. rigger-windows leaves the marketplace, with a migration a person can follow in four steps.
6. The coordinate command runs one session per lane (the change already merged in the source
   harness this repository was split from), with three follow-ups fixed.
7. A `state` tool keeps the state file's work items and its folder tidy.
8. A side session cannot end its turn while its own background shell task still runs.
9. The 3a backlog.

## Non-goals

- No change to shellwise (its owner's repository). A later shellwise release is taken by changing
  the pin; that is a Rigger release step.
- No argv-hash pinning of `trust.project_commands`, no merge of hook processes, no Go or Rust port.
- No company overlay (phase 4, planned elsewhere).

## Anti-goals

- A download that runs an unverified file. A hash mismatch deletes the file; nothing else runs it.
- A shell guard that denies every command when its binary is missing: the slot is then unfilled,
  the session says so once, and commands run (D2). The binary itself fails closed when its analysis
  cannot finish.
- A cleanup that deletes a live session's state, or a log rotation that loses the newest lines.
- A turn-end guard that can hold a session forever: every wait has a bound and says why it stopped.

## Facts measured for this design

| Fact | Source |
|---|---|
| shellwise has one release, `v0.1.0` (2026-11-20), with 4 archives and 4 `.sha256` files: `x86_64-pc-windows-msvc.zip`, `x86_64-unknown-linux-gnu.tar.gz`, `aarch64-apple-darwin.tar.gz`, `x86_64-apple-darwin.tar.gz` | `gh release view -R Borcioo/shellwise` |
| `v0.1.0` documents 53 rule ids; `bash-ps-call-operator` is not among them (it comes with the unmerged shellwise PR #5) | `gh api …/git/trees/v0.1.0?recursive=1` |
| The old-to-new rule map and the integration config were probed on a later shellwise branch head, not on `v0.1.0` | the rule map's header |
| The Stop hook's stdin carries `background_tasks`: one entry per live task, `{"id", "type": "shell", "status": "running", "description", "command"}` or `{…, "type": "subagent", "agent_type"}`; a task leaves the list when it ends, fails or is killed; the field is `[]` with none, and present under `claude -p` (CLI 2.1.327) | stub spike, ten isolated runs |
| A finished task's output file ends `[exited with code N]`, a killed one `[killed]`; a killed task gets no notification record in the transcript | same spike |
| Blocking a Stop returns the reason to the model as `Stop hook feedback:`; three blocks in a row took under 1 s, so a block alone never waits; a bounded poll inside the hook held the turn | same spike |
| The CLI sets `CLAUDE_CODE_CHILD_SESSION=1` and `CLAUDE_CODE_CHILD_NOTES_DIR` for every hook process, so neither marks a side session; `CLAUDE_CODE_HOST_SESSION_ID` is passed through only when the app sets it | same spike |
| The commit shadow rule (`shell_guard.py:2926-3318`, 393 lines) needs 1,467 of the file's 3,352 lines (both lexers and the location code) | name-reference closure over the file |
| Nothing cleans `<config dir>/state/`; `compact_guard.py` reads `safe_to_compact` and `coordinator` and judges staleness by the file's mtime | read of `compact_guard.py` |

## Approaches and choices

### A. How the shellwise module finds and runs its binary

- (a) A Python hook that reads config and runs the binary on every command: one Python start per
  command (3a measured the `sh` floor alone at 53–62 ms on Windows).
- (b) **Chosen:** a SessionStart handler in Python resolves the binary (the personal override, else
  the downloaded pin), checks it, and writes its absolute path into `<plugin data>/current`. The
  PreToolUse handler is a POSIX `sh` launcher that reads that one line and `exec`s the binary with
  `hook claude-code --config <module>/shellwise.toml`, or exits 0 with no output when the file is
  missing. No Python on the per-command path.
- (c) Download on the first command: the first command waits on the network.

The download runs in a detached child of the SessionStart handler, so the session start is not held
by the network, if the plan's release spike shows such a child outlives the hook process on Windows
and Linux; otherwise it runs inside the handler with a 7 s bound and the next session retries. The
session gets `shellwise: downloading v<ver>; commands run unguarded until it is
ready` and, from the next session, the slot is filled. The expected SHA-256 per asset is pinned in
the module (`pin.json`); the release's `.sha256` files only serve the person who updates the pin.
The archive is unpacked from a temp folder, only its one expected member, never a path with `..`, a
link or an absolute name; the result is renamed into place after the hash and `--version` check.

### B. The commit shadow rule, which needs half of the deleted guard

- (a) **Chosen:** move it with its closure. `shell_guard.py` is moved with `git mv` to the testing
  module as `tools/test-evidence/commit_shadow.py`, and everything outside the closure is deleted.
  The rule keeps its behaviour and its tests (the rule half of `test_commit_gate.py`).
- (b) Rewrite `find_commit` on a small tokenizer: about 250 new lines and a measurement that drifts
  from the one gate-lab replays.
- (c) Drop the commit row and keep only the Stop row: gate-lab loses half of its data.

Two behaviours change with the move, and the release notes say so: the row is now written by its
own `Bash(*commit*)` and `PowerShell(*commit*)` handlers, also when shellwise denies the command
(before, a denied command logged nothing), and the switch is `TEST_EVIDENCE=off` instead of
`SHELL_GUARD=off`.

### C. Seeing a side session's background task at turn end

- (a) Parse the transcript: a killed task never gets a completion record, so it would count as
  running forever.
- (b) **Chosen:** read `background_tasks` from the Stop event. While a `shell` entry is listed, the
  hook polls that task's output file for its trailer, up to a bound inside the hook's own timeout,
  then blocks with a reason that names the task and tells the session to wait inside the turn.
  `subagent` entries are left to the existing rules.
- (c) A new hook process: rigger-core already runs a synchronous Stop handler,
  `side_session_guard.py`; the check joins it, so the hook count does not change.

Which sessions it applies to is the one open fact: the spike could not see a top-level desktop
session. The plan's first task of that lane reads the hook's own environment from a probe Stop hook
in the app, in a top-level and in a side session, before the code is written
(`CLAUDE_CODE_HOST_SESSION_ID` is the candidate). If no marker separates them, the guard applies to
every session that is not a coordinator and not a `claude -p` run, and only to gate and test
commands: a session that ends a turn with its own gate running loses little by waiting.

### D. The state tool

- (a) Keep hand edits and add a lint: the file still grows, nothing cleans the folder.
- (b) **Chosen:** `rigger-core/tools/state/state.py`, like `coord_state.py`: the model calls it
  for work items; it owns the `## Items` section, the folder cleanup and the log rotation, and a
  SessionStart check reports what went stale. The rest of the file stays the model's prose.

## The design

### 1. rigger-SHELLWISE

- `rigger-module.json`: `fills: shell-guard`, `failure_mode: closed`, `provides.shell-guard =
  {kind: hook, target: rigger-shellwise, rule_checker: tools/rule-checker/rule_checker.py}`, no
  rules files, `tests.contract: ["shell-guard"]` (the five contract fixtures are copied here and
  leave rigger-windows with it).
- `rigger-defaults.json` sets the `shell-guard` default to the shellwise module with no `requires`
  (all systems); a system with no asset leaves it unfilled with its line (D2).
- `pin.json`: `{"format": 1, "version": "0.1.0", "assets": {"<target>": {"name", "sha256"}}}`
  for the four targets. Target from `sys.platform` and `platform.machine()`: `win32` + `AMD64` →
  `x86_64-pc-windows-msvc`; `linux` + `x86_64` → `x86_64-unknown-linux-gnu`; `darwin` + `arm64` →
  `aarch64-apple-darwin`; `darwin` + `x86_64` → `x86_64-apple-darwin`; anything else has no asset.
- `<plugin data>/shellwise/v<ver>/<target>/shellwise[.exe]`, `current` (one line, the path),
  `status.json` (`{"version", "target", "state": "ready|downloading|failed|no-asset|override",
  "reason", "at"}`) and a lock file with its pid and start time (a lock older than 10 minutes is
  taken over).
- Override: `shell_guard.shellwise_path` (level 3 only, today recorded only) becomes active. Its
  binary must exist, run `--version` within 3 s and print `shellwise <x.y.z>` at or above the pin;
  otherwise the slot is unfilled with the reason. The decisions doc's spike S7 is answered by the
  observed `--version` output.
- Hook: `PreToolUse` on `Bash|PowerShell`, running `bin/shellwise.sh` through a command that exits
  0 when the launcher file is missing (exit 2 would deny every call), timeout 5 s
  (`hook_timeout_ms = 5000` in the shipped config), no `|| true` (the binary's reply is the
  decision; the release spike records its form). The exec bit is set on POSIX after unpacking.
  `SHELLWISE=off` is the binary's own switch.
- `shellwise.toml` is the integration config delivered with the rule map, plus
  `gate_commands` entries for the Python safe-edit's file name; a probe proves each key on the
  pinned binary.
- Session lines (one at most): `shellwise: v<ver> ready`, nothing when ready and unchanged;
  `shellwise: no binary (<reason>); commands run unguarded` otherwise. Doctor row
  `slot shell-guard` names the state and the failure mode `closed`.
- `tools/rule-checker/rule_checker.py`: `RULES` is the pinned version's rule id list (a test
  compares it with `explain <id>` for every id), `ALIASES` maps old guard ids to new ones
  (`bash-powershell-syntax` → `bash-ps-cmdlet`, `bash-too-long` → `too-long`,
  `bash-unterminated-quote` → `parse-error`), and `rule_guard_problem(rule, meta)` runs `check
  --shell <s> --json --config <shellwise.toml>` with the probe on stdin and returns `None` when a
  finding names the rule at `break` or above. `lessons_lint.py` also globs
  `plugins/*/tools/*/rule_checker.py`; curate-lessons takes the checker from the filler's manifest.
- A `live:<rule>` lesson for `bash-ps-call-operator`, or for a Bash-tool `tee … | head`, is
  reported `no checker` until a pin bump brings the rule.

### 2. rigger-PLATFORM

Skills `windows-dev-env`, `php-herd-tooling`, `node-versions` (moved), their trigger rows (ids and
loads renamed; `windows-dev-env` with `requires: os:windows`), `rules/windows.md` with `requires:
os:windows` and without its `Plugin root:` preamble, the five evals, `hooks/core_check.sh`. The
herd-docs folder becomes `<config dir>/rigger-platform/herd-docs/`, and the skill reads the old
path for one release. Sentences the shellwise rules now enforce (the "can be dropped" rows of the
redundant-prose survey, at the shipped config) are cut; the guard's old marker text is replaced by
`# shellwise: allow`.

### 3. mem-check in rigger-core

`rigger-core/tools/mem-check/mem_check.py [--min-free-gb N] [--json]`, stdlib only. Readings:
Windows commit headroom (CIM through `pwsh` or `wmic`-free `ctypes` `GetPerformanceInfo`, ceiling
from Event 2004 as today; the builder picks the call after a spike), Linux `/proc/meminfo`
(`MemAvailable`, `CommitLimit − Committed_AS`; the smaller counts), macOS `vm_stat` (free +
inactive + purgeable) × `sysctl hw.pagesize`. Output keeps today's line shape
`mem-check: <measure>, headroom <n> GiB (min <m> GiB), max heavy agents <k>: OK|BUSY` and exits
0 OK, 1 BUSY, 2 no reading (one heavy agent at a time). Config `load.min_free_gb` (number,
0–1024) and `load.max_heavy_agents` (integer, 1–8), levels 1–4, schema 1; the defaults per system
live in `mem_check.py` and apply when no level sets a key; the project level may only make a limit
stricter. The `.ps1` and its tests go with rigger-windows.

### 4. The testing module takes the test tools

`tools/safe-edit/safe_edit.py` (same flags in Python form, exit codes 0–4, messages kept, hash
check of the restore, stale-backup handling), `tools/test-evidence/` (`commit_shadow.py`,
`stop_shadow.py`, `turn_evidence.py`, their tests, the "Shadow gates" install text),
`tools/gate-lab/` (sibling-folder imports fixed, `--data` takes either data folder). The commit
rows' data folder changes with the plugin; old rows stay where they were (spec §13 "Gates file").

### 5. rigger-core takes drift-lint

Moved unchanged, with its baseline rows re-keyed to the new folders; it also reads each module's
trigger `load` names (spec §13 row). The repository pitfall and `CONTRIBUTING.md` paths follow.

### 6. Removal and migration

rigger-windows leaves `marketplace.json`; every reference is re-pointed or removed (197 lines in 42
live files at `1ead09a`; history documents and the frozen golden fixture stay). `harness_start.py`
prints `rigger: rigger-windows is still enabled; disable it (claude plugin disable
rigger-windows@<marketplace>)` while it is enabled. Doctor's Windows requirement becomes "recommends
the platform module and a `shell-guard` filler". The migration in the release notes: install the
two new plugins one call each and update rigger-core; disable and uninstall rigger-windows; remove
a hand-added shellwise hook; run doctor. macOS and Linux gain the guard, a behaviour change listed
in the release notes.

### 7. coordinate: one session per lane

Port the merged source change (step 2's lane rule, standby sessions started in the user's turn,
later stages by `SendMessage`, the coordinator does not implement, no session cap of our own, heavy
jobs at most 2 across lanes, the skill's and `cli-host.md`'s sentences, the ten wiring tests) with
this repository's words ("user", "integration branch", `RIGGER_*` overrides). Three fixes at once:
- step 3 and step 4 agree: a side session runs the checks its change reaches and the full gate on
  its branch before it reports; the coordinator runs the full gate once per merge point;
- step 2 becomes three paragraphs: lanes, starting the sessions, load;
- step 2 says a lane's next part starts only after the previous part is merged, which `coord_state`
  already enforces (`waits_on` must be `merged`).
The heavy-job sentence stays inside the one `mem-check` bullet of the skill
(`test_core_wiring.py:199-208`).

### 8. The state tool

- Items live in `## Items` of `<config dir>/state/<session id>.md`, one line each:
  `- <id> · <status> · <PR or —> · <branch or —> · <title>`, optional indented detail lines below.
  Statuses: `open`, `doing`, `blocked`, `done`. Ids match `[a-z0-9][a-z0-9-]{0,39}`.
- Commands: `add <id> <title> [--branch B]`, `set <id> <status> [--pr N] [--branch B]`,
  `note <id> <text>`, `done <id> --pr <N>` (the item becomes one line; its detail lines move to
  `<session id>.log.md` under `## <id> (<ISO time>)`), `show`, `check`, `clean`. Writes are atomic
  (temp file and replace) and keep every other section byte for byte. Exit 0 `state: ok …`, 2
  `state: refused: <reason>`.
- Each item records its repository (`owner/name`, from the cwd's `origin` at `add`, or `--repo`).
- `check` (SessionStart `clear|compact|resume`, on the current session's file only; a new session
  at `startup` has none yet): items whose PR is merged (`gh pr view <N> --repo <owner/name> --json
  state` with a 3 s bound per call, 5 s in all, merged answers cached in `<state dir>/pr-cache.json`
  by repository and number; no `gh` or offline → one `unchecked` line), items whose branch no
  longer exists locally or as a remote-tracking ref, and `safe_to_compact: false` with `updated:`
  (else mtime) older than 30 minutes. One line per finding, at most 5 and a count.
- `clean` (at `startup`, under a lock file): a session is idle when its newest state file and its
  transcript (when one exists) are older than 14 days; all its `<id>.*` files move to
  `<state dir>/archive/` with their mtime set to the move time; archived files older than 60 days
  are deleted. Never the current session, never one whose transcript or any state file changed in
  14 days; shared files (`compact-guard.log`, `pr-cache.json`) never. A session resumed after its
  files were archived gets them back at `resume`, with one line. `<id>.log.md` over 50 kB keeps its
  newest 25 kB and moves the rest to `archive/<id>.log.<UTC stamp>.md`, cut at a line start.
- `rules/state-file.md` tells the model to change items only through the tool.

### 9. The side-session turn-end guard

`side_session_guard.py`'s Stop path gains, before its transcript scan: when the session is a side
session (the marker the lane's first task measures; with none, any session), is not a coordinator
(`coordinator: true` in its state file) and is not a `claude -p` run (`CLAUDE_CODE_ENTRYPOINT`
`sdk-cli`, which waits for its tasks itself), and `background_tasks` lists a `shell` task whose
command is a gate or test command (a fixed list: `gate.py`, `run_tests.py`, `pytest`, `unittest`,
`npm test`, `vitest`, `jest`, `phpunit`, `playwright`, `go test`, `cargo test`, `safe_edit`), poll
its output file for `[exited with code` or `[killed]` up to 4 s; still running → block with
`side session: your background task <id> (<description>) is still running; wait for it inside this
turn (a bounded wait on its end), then report`. It blocks at most 3 times per task id, counted in
its own always-written file `<session id>.turn-guard.json`, then allows with a line naming the task.
The Stop handler's timeout rises from 10 to 15 s. A missing field or unreadable file allows.

### 10. The 3a backlog

- `slot_run.py` passes the session id to `review_scope.scope()`, so a command reviewer's `{scope}`
  carries `review.paths` results.
- The review and testing modules ship `hooks/core_check.sh <plugin>` on `SessionStart`, so a
  person who has them without rigger-core is told.
- `test_review_scope.py` is split by test class into files under 400 lines, with one shared
  repository per read-only class.
- `review_scope.py`: a rules file newly named in the diff that is a link or over the size cap gets
  `review: rules file newly named (link)` / `(over <n> bytes)` and is not handed to the reviewer.
- Doctor's `rules-d-table` INFO row names the cause (a rules file still holds a skill-trigger
  table), with no FIX, as `commands/doctor.md` requires of an INFO row.
- `test_slot_run.py`'s WinError 32 and `test_report_shadow.py`'s flake did not reproduce in 48
  runs: the cleanup gets a diagnostic that reports even a retry that succeeds, and the 70–114 s file
  is split; no product change without a reproduction.
- Read-by map: the nine `*` entries are narrowed to the modules their files read where the lint can
  derive a finite set.

## Left to the builder

The Windows memory call (after a spike), file and class names inside the moved test files, the
exact wording of notices that no test pins, the order of the platform skill's cut sentences.

## Estimate and simpler version

The plan prices it: about 76.5 agent hours, 2,525 lines of code and 4,055 of tests, about 9,000
lines moved unchanged and 4,500 deleted. Simpler version, about 46 hours: rigger-windows stays as
the Windows shell-guard filler for one more release (the decisions doc's simpler version), and the
shellwise module, the test-evidence and gate-lab moves and the removal wait; it keeps the platform
module, mem-check, safe-edit, drift-lint in core, the coordinate, state and turn-end work and the
backlog. It drops the guard on macOS and Linux for that release.
