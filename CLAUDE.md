# Rigger

Claude Code plugin marketplace (`rigger`). Everything under `plugins/` ships to other machines
(macOS, Linux, Windows): no absolute paths, no personal or company names. How to test and propose a
change: `CONTRIBUTING.md`.

## Pitfalls

- **`claude plugin validate` and any other `claude` command write into the config dir** (`backups/`, `.claude.json`): run them under a temp `CLAUDE_CONFIG_DIR`, a fresh one per experiment (a plugin left by one run doubles the next run's log lines), never your real one. A `--plugin-dir` run also sets `CLAUDE_CODE_PLUGIN_CACHE_DIR` (where Claude Code keeps installed plugin copies) and `HARNESS_LESSONS_INBOX` to scratch folders and leaves `RIGGER_LESSONS_BATCH` unset, or `py.sh` and `lessons_start` write into your real config.
- **Git Bash `sh.exe` started from Python re-parses its Windows command line** (quotes, `*`
  globbing) before the script sees its arguments: tests pass no quote or glob characters as
  arguments, or pass them inside an `sh -c` string, as hooks do.
- **A Python child writing to a pipe on Windows emits CRLF:** normalise `\r\n` before parsing its
  output with `$`-anchored regexes.
- **`bin/py.sh` exists once per plugin and the copies are byte-identical** (`tests/test_repo.py`
  checks): change every copy in the same commit.
- **The sweep's host rule flags public documentation hosts** (`ALLOWED_DOMAINS` matches exactly, so
  a subdomain needs its own entry): run `python -I scripts/sweep.py --no-denylist <file>` on a
  reference doc with URLs before moving it. It also reads a dotted code attribute as a domain.
  **Dry-run the sweep on word-for-word rows** before committing them.
- **`plugins/rigger-windows/tools/gate-lab` drops events under the temp folder (`OUTSIDE`):** a test corpus under a temp folder reads 0 fires, not "nothing to find".
- **Transcript readers:** classify user lines by origin (`turnOrigin`, `isMeta`, `isCompactSummary`, body prefix) with one known-answer control per origin; `(Bash completed with no output)` is the text of an empty Bash result, so a "non-empty output" rule excludes it.
- **Transcript walks:** a walk limit that is also the normal end cannot signal incompleteness — stop on a record that proves the end; evidence stored truncated hides later writes, so reread the full command before trusting a name filter; transcripts used for timing comparisons are pinned by size in a saved list, never picked by percentile.
- **WSL git cannot open a worktree created on Windows** (its `.git` file names the git dir by a
  drive path): a test that runs git on the real checkout sets `GIT_DIR` (as `/mnt/<d>/…`) and
  `GIT_WORK_TREE` on POSIX, as `tests/test_repo.py` `git_env()` does.
- **Before adding a `harness_start` warning,** check the existing rules headings against it.
- **A directory marketplace reports `installPath` as a cache snapshot,** stale until `plugin update`, while hooks run in place.
- **Store Python derives `sys.executable` from `USERPROFILE`:** in tests fake `HOME` alone; a test that starts `pwsh` in a fake home sets `POWERSHELL_TELEMETRY_OPTOUT=1` (several first starts at once hang 60 s+).
- **A POSIX read-only guard checks the mode bits** (`st_mode & 0o222`), not only `os.access(W_OK)`, which root always passes; its test also runs as root under WSL.
- **A compaction probe's first turn carries no marker:** the marker comes only from the hook under test, and the question asks for it after `/compact`; otherwise re-injection and a summary that copied it look the same.
- **A hook proof fires the exact trigger its matcher names** (`/compact` fires `manual`, not `auto`) and reads `Hooks: <Event>:<matcher>` back from the debug log; `claude -p --resume` runs resume hooks but drops their text, `|| true` on a launcher turns any failure into status 0, and identical hook text is deduplicated across firings — so the proof is the debug log or output that differs per firing, never exit status or a request count.
- **A path a PreToolUse/UserPromptSubmit hook executes is changed atomically, from outside Claude, with no session running:** python exits 2 on a missing script and blocks the very tools needed to restore it; to undo such a change, restore the script paths before the settings that call them.
- **Claude Code's 10,000-character hook-output cap counts CR bytes:** a Windows Python hook's `print` writes CRLF, so a 9,990-character text was persisted as a file instead of injected; hooks write LF and the size test counts the bytes emitted.
- **A script that handles denylist terms masks every line it writes, echoed command lines included,** and the sweep runs over every output file of a worker, not only the deliverable.
- **A plugin never reaches a sibling plugin by relative path** (installed, each is `cache/<marketplace>/<plugin>/<version>/`): resolve it via `installed_plugins.json` `installPath` or an explicit flag, and test from a copy laid out like the cache.
- **`claude -p` against a dead or unreachable API retries about 10 times at 2+ minutes each** and `subprocess.run(timeout=…)` drops its output: before a real-model run, one short `curl` to the API host from the same environment (an isolated config may also have no login), a bounded timeout, and `Popen` streaming stdout, stopped at the init event when that is all you need.
- **`rg -g '!<glob>'` with an absolute search path excludes nothing** (globs match the path as given): run exclusion globs from the repo root with `.` as the path and print the count of excluded files as the control.
- **"Ran under Windows PowerShell 5.1" holds only when the test spawns that shell** and the version is printed by the same invocation that runs the code under test (a separate probe call stays green when the install call hard-codes pwsh); red-run it by hard-coding the other shell in that invocation only.
- **JSON read from outside gets one test per wrong shape** (a list where a dict is expected, a number where a list is expected — a string iterates harmlessly and kills no `isinstance(…, list)` mutant); run the mutant to prove each.
- **Hook wiring is proven against `plugins/rigger-rules/tests/stub_api.py` behind `ANTHROPIC_BASE_URL`** in an isolated config (no cost, no real model); the report says "stub".
- **Test stubs:** a stub recognises a call by its content, not its argv position; a stub for an `.exe` is a real `.exe` (a `.cmd` changes the quoting the code sees, and `exit /b N` inside a nested block returns 0); check a stub's exit code directly.
- **Read the CLI version right before the first real run:** auto-update can move the `claude` on PATH mid-task, and a desktop app may run its own copy.
- **Before a stub URL or an isolation runs next to a real config,** list every inherited env override against each enabled hook with third-party plugins on, prove the isolation with a canary on a scratch config first, and abort on any FAIL.
- **A Python red run can load a stale `.pyc`** when the mutated file keeps its size and mtime second (`-I` ignores `PYTHONPYCACHEPREFIX`): run mutants with `-X pycache_prefix=<dir per mutation>` and read the failing case, not only the exit code.
- **`autoCompactWindow` at or above the compaction guard's caps silently disables `safe_to_compact`** (`compact_guard.py` runs with `--hard-cap 560000` and, for a session that coordinates others, `--coordinator-cap 650000`; 450k is the script's default): before changing any threshold, list every other limit compared against it.
- **A test of `gate.py`/`run_tests.py` `main` patches the part that runs things even when it expects an early exit:** a mutant that passes the flag check runs the whole suite recursively, and a red script that times out kills the process tree and checks for orphans.
- **Gate:** `python -I scripts/gate.py` in the background (every input optional; `CONTRIBUTING.md`
  lists them); its first line names `status.txt`, read that instead of rerunning. A leg whose output
  has no count, or a count of 0, fails ("printed no count matching …" / "count 0 from …"); a PASS
  line ends with its count and, for the suites, the number of SKIP files; a leg it cannot run
  prints `SKIP <leg>: <reason>`. Between fixes `python -I scripts/run_tests.py --fix-round`, never
  as the gate. A change to a rules file or a skill runs `test_plugin_rules` and `test_drift_lint`
  before the merge, even when you ran only some test files before; a commit chained after a test
  runs only on that test's exit code, with the full output kept in a file. The status file's
  `TREE <id>` line and the pass line's `tree <id>` name the content the gate ran on; before a merge
  run `python -I scripts/gate.py --verify <status.txt>`, and when the base moved, rerun the gate.
