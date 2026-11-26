# Phase 3b exit criteria (stub)

Every session below is a real `claude -p` run against the local stub, not a real model. The one network
action is the installer's download of the pinned shellwise asset, once on Windows and once under WSL.

## CLI

Each version was read right before the runs (`command -v claude`, then `claude --version`):

- **Windows:** `<home>/.local/bin/claude`, `2.1.332 (Claude Code)`.
- **WSL:** `<home>/.local/bin/claude`, a link to `versions/2.1.323`, `2.1.323 (Claude Code)`.

## Setup

- **Isolation:** every experiment has a fresh folder `<temp>/rigger-p3b/C2-task28/runs/<name>/`
  with these parts:
  - a config, a plugin cache, a lessons inbox, `HOME` (and `USERPROFILE` on Windows) and `TEMP`;
  - a child env that drops `CLAUDE_*`, `ANTHROPIC_*`, `HARNESS_*`, `RIGGER_*` and `GIT_*`
    (`CLAUDE_CODE_CHILD_SESSION` stays, as in `test_stub_session.py`);
  - these variables set on purpose: `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_PLUGIN_CACHE_DIR`,
    `HARNESS_LESSONS_INBOX`, `RIGGER_MODULE_DIRS` (the same roots as `--plugin-dir`), a stub
    `ANTHROPIC_API_KEY`, `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`;
  - `RIGGER_LESSONS_BATCH` unset;
  - `ANTHROPIC_BASE_URL` pointing at a stub on `127.0.0.1`. Before each run the stub answered one
    known POST with its text.
- **Stub:** the n-th main-loop request (its tools hold `Bash`) gets the n-th scripted `Bash` tool_use,
  and every other request gets end_turn text. Every request body is kept.
- **Inherited env:** the dropped names are the desktop app's `CLAUDE_CODE_*` (the entrypoint, the session and
  host ids, the notes folder), `ANTHROPIC_BASE_URL`, `HARNESS_LESSONS_ARCHIVE`, `HARNESS_MAINTAINER` and
  `GIT_EDITOR`.
  Of the names a hook could read, `MSYSTEM`, `OPENSSL_CONF`, `PYTHONDONTWRITEBYTECODE`, `PYTHONPATH` and
  `PYTHONUTF8` pass through. The modules' Python hooks start through `bin/py.sh`, which runs `-I`, so
  `PYTHONPATH` is ignored. No `SHELLWISE`,
  `TEST_EVIDENCE` or proxy variable is set.
- **Plugin data folder:** a `--plugin-dir` plugin's data folder is `<cache>/data/<plugin>-inline/` under
  `CLAUDE_CODE_PLUGIN_CACHE_DIR`, not under the config. This was observed on both CLIs; see finding F1.

## Canary

`e0b-canary` (Windows) and `w0-canary` (WSL) ran rigger-core alone.

| Check | Windows | WSL |
|---|---|---|
| exit status | 0 | 0 |
| requests that reached the stub | 1 | 1 |
| init plugins besides rigger-core | 3, each `path: builtin`, `source: cc-plugin-*@builtin` | same |
| request texts holding host instruction or plugin text (3 probes) | 0 | 0 |
| this session's transcript in the scratch config (control) | 1 | 1 |
| this session's transcript in the real config | 0 | 0 |
| session id in the real `.claude.json` | no | no |

**Result:** PASS on both systems. The first Windows canary (`e0-canary`) was marked FAIL because it
counted the three CLI builtins as host plugins. The check now accepts only `path: builtin` entries.

## Criteria

| # | Criterion | Where it runs | Result |
|---|---|---|---|
| 1 | The pinned asset is downloaded into an empty data folder (Windows) | scratch `e1_download.py` | PASS |
| 2 | `RULES` equals the binary's `explain` set | scratch `e2_rules.py` | PASS |
| 3 | A break command is denied; a warn command runs and its note reaches the model | test + scratch | PASS |
| 4 | With the binary removed, the session line appears and the command runs | test + scratch | PASS |
| 5 | `Hooks: PreToolUse:Bash` appears for rigger-shellwise and rigger-testing | test + scratch | PASS |
| 6 | A commit row comes from the testing module | test + scratch | PASS |
| 7 | The turn guard holds a turn with a background gate | test + scratch | PASS (fixed in 5ccf220; it was FAIL in a fresh config, F2) |
| 8 | Doctor reports `closed` for the shell-guard slot | scratch `e8_doctor.py` | PASS: `fails closed` in both layouts; the `OK` state only in a hand-copied layout (F1) |
| 9 | The download case again under WSL | scratch `e1_download.py` | PASS |

### 1. The download (Windows)

**Command:** `e1_download.py <runs> e1-download-win <plugins> <Task 14 Windows zip>`. Session A loads
rigger-core, rigger-testing and rigger-shellwise from `--plugin-dir`, with an empty data folder.

**Known answer:**
- The SHA-256 of the Task 14 archive equals the `pin.json` value (`True`).
- That archive's `shellwise.exe` member hashes to `ac99c027…6075b2`.

**Observed:**
- Session A's first request:
  `SessionStart:startup hook success: shellwise: no binary (downloading); commands run unguarded`.
- `status.json`, read after session A exited:
  `{"version": "0.1.1", "target": "x86_64-pc-windows-msvc", "state": "ready", "reason": ""}`.
- `current` names `<cache>/data/rigger-shellwise-inline/shellwise/v0.1.1/x86_64-pc-windows-msvc/shellwise.exe`.
- `--version` prints `shellwise 0.1.1`.
- The binary's SHA-256 is `ac99c027…6075b2`, the same as the member's. The installer itself compared the
  archive with `pin.json` before it wrote `ready`.
- Session B on the same config prints `shellwise: v0.1.1 ready` once.

**Note:** the first attempt looked for the data folder under the config, found none and stopped. The
download itself had already finished. The resumed script (`E1_RESUME=1`) checked the same folder without
a second download.

### 2. Rule ids

**Command:** `e2_rules.py <data folder> <plugins> <Task 14 tree listing>`. The binary is the one
`rule_checker.find_data_dir` and `find_binary` find from `CLAUDE_PLUGIN_DATA`.

**Observed:**
- `# ids 54, nonzero 0`: `explain <id>` exits 0 for every id in `RULES`.
- **Control:** `explain no-such-rule-xyz` exits `64`.
- **The other direction (offline):** the release tree's `docs/rules/<id>.md` list, saved by Task 14,
  holds 54 ids. None is only in `RULES` and none is only in the release.
- The first run reported 53 ids. The harness regex dropped the capital in `rg-follow-L`. With the regex
  fixed, the sets are equal.

### 3. Break and warn

**Command:** the stub runs two Bash tool_uses:
- the break command `safe-edit --run x 2>&1 | head -n 30; echo ran > m3b.txt`, which only the module's
  `shellwise.toml` (`gate_commands`) makes a break;
- the warn command `rm -f "$NOPE_VAR"x.tmp; echo M3W`.

**Known answer:** the binary was probed directly with `hook claude-code --config shellwise.toml` (on both
systems):
- the first command is denied with `gate-truncated-pipeline`;
- the second gets `additionalContext` with `warn dynamic-target`.

**Observed (Windows):**
- **The next request, deny:** the tool_result (`is_error`) reads
  ``PreToolUse:Bash hook error: shellwise: break gate-truncated-pipeline: `safe-edit` is piped into `head`, …``.
  `m3b.txt` does not exist.
- **The next request, warn:** the tool_result is `M3W`. The note arrives as
  ``PreToolUse:Bash hook additional context: shellwise: warn dynamic-target: the guard could not read what `"$NOPE_VAR"x.tmp` deletes: …``.
- **Under WSL:** the same test passes. The hook analyses the command as `bash`. The Bash tool there ran
  it through the user's own shell, whose error text was not bash's (`(eval):1: parse error near '&'` for an
  earlier command).

An earlier POSIX choice failed under WSL: `& "/usr/bin/whoami"` is a parse error when run unguarded, and the
deny's `Fix` line repeats the command. So the test proves "did not run" with a marker file, never with the
reply text.

### 4. Binary removed

**Command:** the file that `current` names is deleted, and a fresh `shellwise/lock`
(`{"pid": 0, "start": <now>}`) is written. Then the stub runs the break command of criterion 3.

The lock makes the session start see a download in progress, so it starts no second network download.
`status.json` and the lock are byte-identical after the session, so no child ran.

**Observed:**
- The first request holds
  `SessionStart:startup hook success: shellwise: no binary (downloading); commands run unguarded`.
- The tool_result is `/usr/bin/bash: line 1: safe-edit: command not found`, with no `shellwise:` line,
  and `m3b.txt` exists. So the command ran unguarded.

### 5. `Hooks: PreToolUse:Bash` for both modules

**Observed (Windows debug log):**
- **rigger-shellwise:**
  `Hooks: PreToolUse:Bash [sh -c '[ -f "$0" ] || exit 0; . "$0"' "<plugins>/rigger-shellwise/bin/shellwise.sh"] (plugin rigger-shellwise@inline) finished with status 0`.
  It appears once per Bash call: 2 in the break-and-warn session, 1 in the commit session.
- **rigger-testing:**
  `Hooks: PreToolUse:Bash [sh "<plugins>/rigger-testing/bin/py.sh" -S "<plugins>/rigger-testing/tools/test-evidence/commit_shadow.py" || true] (plugin rigger-testing@inline)`,
  and the same for `weak_test_guard.py`. That is 2 lines for the commit.
- **Control:** for the break command the log says `Skipping hook due to if condition "Bash(*commit*)" not matching`,
  twice.

### 6. Commit row

**Command:** a temp repository with `.claude/gates.json`
(`{"commit": "shadow", "checks": ["pytest"], "scope_commit": ["*.py"]}`). The stub runs
`git add -A && git commit -q -m one`, with git config isolated (`GIT_CONFIG_NOSYSTEM`, an empty
`GIT_CONFIG_GLOBAL`).

**Observed:**
- The repository has 1 commit.
- `<cache>/data/rigger-testing-inline/commit-gate/rows/` holds 1 row:
  `{"op": "commit", "verdict": "stale", "reason": "ok", "use12": "f2b458a3a320"}`.
- **Control:** the same commit in a repository without `gates.json` writes 0 rows.

### 7. Turn guard

**Command:** rigger-core alone, run with `CLAUDE_CODE_ENTRYPOINT=claude-desktop`. The `-p` default `sdk-cli`
is never held. The stub's first reply is
`Bash {"command": "\"<python>\" run_tests.py", "run_in_background": true}`, where `run_tests.py` sleeps 9 s.
Every later reply is end_turn text.

**Result (2026-11-26, after the fix):** PASS. Commit 5ccf220 makes the turn guard create
`<config>/state/` itself, and an unwritable count holds the first Stop of a chain. `TurnGuardSessionTests`
no longer creates the folder and asserts that the count file exists.

**Result as first recorded (before 5ccf220):** FAIL in the setup the criterion names (a fresh config),
because of F2. PASS once `<config>/state/` existed, which the harness and the test then created before the session.

**Observed (`e7b`, with `<config>/state/` created first):**
- **Debug log:** `Hooks: Stop [sh "<plugins>/rigger-core/bin/py.sh" -S "<plugins>/rigger-core/tools/side-session-guard/side_session_guard.py" || true] (plugin rigger-core@inline)`.
  Then
  `Hooks: Parsed initial response: {"decision":"block","reason":"turn-guard: background run bhzcz08l6 (\"run the tests\") is still running: … Block 1 of 3."}`,
  and Block 2 at the next Stop.
- **The next request carries:**
  `Stop hook blocking error from command: …: turn-guard: background run bhzcz08l6 ("run the tests") is still running: … Block 1 of 3.`
- **Control (`e7c`, `sdk-cli`):** 2 Stop firings, no `turn-guard:` text, an empty state folder.
- **Without the state folder (`e7`):** the first Stop took 4208 ms, so the guard polled, then allowed the
  turn. See F2.

### 8. Doctor

**Command:** `assemble.py --doctor`, and `doctor.sh` through Git `sh`. Both get the session's env
(config, plugin cache, `RIGGER_MODULE_DIRS` with the three modules).

**Observed:**
- **The layout doctor reads** (`<config>/plugins/data/rigger-shellwise-inline/`, a copy of the
  downloaded folder):
  `CHECK  slot shell-guard  OK  rigger-shellwise (fails closed); state ready, v0.1.1  assemble.py --doctor`.
  `doctor.sh` prints the same row.
- **The stub layout** (data under `<cache>/data/`):
  `CHECK  slot shell-guard  WARN  rigger-shellwise (fails closed); state missing: no shellwise status.json; commands run unguarded`.
  See F1.

### 9. The download under WSL

**Command:** `e1_download.py` inside WSL, run by the WSL Python 3.10 and claude 2.1.323, with the plugins
reached through the `/mnt/<drive>` path.

**Before the run:**
- a copied `/bin/true` ran from the scratch mount;
- an installer `--child` rehearsal from a local server over the Task 14 Linux archive reached `ready`.

**Observed:**
- Session A: `shellwise: no binary (downloading); commands run unguarded`.
- `status.json`:
  `{"version": "0.1.1", "target": "x86_64-unknown-linux-gnu", "state": "ready", "reason": ""}`.
- `current` names `<cache>/data/rigger-shellwise-inline/shellwise/v0.1.1/x86_64-unknown-linux-gnu/shellwise`.
- `--version` prints `shellwise 0.1.1`.
- The binary's SHA-256 is `fc6adea5…8183ed`. That equals the member of the Task 14 Linux archive, whose
  SHA-256 equals `pin.json` (`True`).
- Session B: `shellwise: v0.1.1 ready`.

## Automated and scratch

**`plugins/rigger-core/tools/assemble/tests/test_stub_session.py`**:
- `ShellwiseSessionTests`:
  - `test_break_is_denied_and_warn_runs_with_its_note` covers criteria 3 and 5 for rigger-shellwise;
  - `test_commit_writes_one_row_from_the_testing_module` covers criteria 5 and 6, with the control
    without `gates.json`;
  - `test_removed_binary_leaves_the_line_and_the_command_runs` covers criterion 4.
- `TurnGuardSessionTests.test_a_background_gate_holds_the_turn` covers criterion 7, with the `sdk-cli`
  control.

**How the shellwise cases get the binary without the network:** the module's own installer
(`--child --base-url`) installs it from a local server. The server holds the pinned archive in the folder
`SHELLWISE_ASSETS` names, so the installer's `pin.json` hash check runs. Without `SHELLWISE_ASSETS`
those three cases skip with that reason.

**Network fence:** the sessions also set `HTTPS_PROXY` to a dead `127.0.0.1` port, with `NO_PROXY` set for
`127.0.0.1`. An offline control (`proxy_control.py`, no request sent) checked how `urllib` reads that env on
Windows and WSL:
- the `https` proxy is `http://127.0.0.1:9`;
- `github.com` is not bypassed, and `127.0.0.1` is;
- without the env the `https` proxy is `None`.

No session in the tests started a download child, so the fence itself was never exercised.

**Scratch harness only:**
- criteria 1 and 9: the download is the one allowed network action, and no test may reach the network;
- criterion 2: reads the downloaded binary and Task 14's saved tree listing;
- criterion 8: needs the downloaded data folder.

The harness is `<temp>/rigger-p3b/C2-task28/harness/`.

**Runs:**
- The whole file on Windows took 95 s for 12 tests: `OK`, with `SHELLWISE_ASSETS` set.
- The four new cases under WSL took 30 s: `OK`.
- Without `SHELLWISE_ASSETS`, the three shellwise cases skip and the guard case runs.

**Red runs** (each on a scratch copy of `plugins/`, one test):

| Mutation | Test | Failing assertion |
|---|---|---|
| Launcher `exit 0` before `exec` | break/warn | `'shellwise: break gate-truncated-pipeline: ' not found in '…safe-edit: command not found…'` |
| shellwise PreToolUse matcher `Bash\|PowerShell` → `PowerShell` | break/warn | same |
| `_read_gates` returns None | commit row | `0 != 1` (gates_json=True) |
| `resolve` drops `os.path.isfile(path)` | binary removed | `'…shellwise: no binary (downloading); commands run unguarded' not found` |
| `run_tests\.py` removed from `GATE_COMMAND` | turn guard | `False != True : a turn-guard block reached the next request` |

## Findings (not fixed here)

- **F1. Doctor misses the data folder under `CLAUDE_CODE_PLUGIN_CACHE_DIR`.**
  - The CLI puts plugin data under `<cache>/data/`, but `_shellwise_state` looks only in
    `<config>/plugins/data/`. So with that variable set, doctor reports `state missing` while the slot is
    ready.
  - `rule_checker.find_data_dir` has the same fallback, but only when `CLAUDE_PLUGIN_DATA` is unset (inside
    a session it is set).
  - It is visible only with the variable set (stub runs, a custom cache).
- **F2. The turn guard fails open in a config with no `state/` folder.** Fixed in 5ccf220 (2026-11-26); the text below is the observation before the fix.
  - `_save_counts` writes `<config>/state/<session>.turn-guard.json`, and nothing creates the folder first.
    A failed count write allows the turn, as the guard's docstring says.
  - So in a fresh config the guard polls 4 s at each Stop and never holds the turn (`e7`). With the folder
    present it holds (`e7b`).
  - The test created the folder and said why; since 5ccf220 it no longer does.
- **Observation:** rigger-testing's `if: "Bash(*commit*)"` handlers ran for `rm -f "$NOPE_VAR"x.tmp; echo M3W`,
  which holds no `commit` (2 handler lines). They were skipped for the break command. The CLI appears to
  run an `if` hook when the command holds an expansion it cannot read. `commit_shadow.py` then finds no
  commit and writes nothing.
