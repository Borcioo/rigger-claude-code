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
- **A compaction probe's first turn carries no marker:** the marker comes only from the hook under test, and the question asks for it after `/compact`; otherwise re-injection and a summary that copied it look the same.
- **A hook proof fires the exact trigger its matcher names** (`/compact` fires `manual`, not `auto`) and reads `Hooks: <Event>:<matcher>` back from the debug log; `claude -p --resume` runs resume hooks but drops their text, `|| true` on a launcher turns any failure into status 0, and identical hook text is deduplicated across firings — so the proof is the debug log or output that differs per firing, never exit status or a request count.
- **A path a PreToolUse/UserPromptSubmit hook executes is changed atomically, from outside Claude, with no session running:** python exits 2 on a missing script and blocks the very tools needed to restore it; to undo such a change, restore the script paths before the settings that call them.
- **Claude Code's 10,000-character hook-output cap counts CR bytes:** a Windows Python hook's `print` writes CRLF, so a 9,990-character text was persisted as a file instead of injected; hooks write LF and the size test counts the bytes emitted.
- **A script that handles denylist terms masks every line it writes, echoed command lines included,** and the sweep runs over every output file of a worker, not only the deliverable.
- **A plugin never reaches a sibling plugin by relative path** (installed, each is `cache/<marketplace>/<plugin>/<version>/`): resolve it via `installed_plugins.json` `installPath` or an explicit flag, and test from a copy laid out like the cache.
- **`claude -p` against a dead or unreachable API retries about 10 times at 2+ minutes each** and `subprocess.run(timeout=…)` drops its output: before a real-model run, one short `curl` to the API host from the same environment (an isolated config may also have no login), a bounded timeout, and `Popen` streaming stdout, stopped at the init event when that is all you need.
- **`claude plugin eval` publishes its HTML report online to the signed-in account unless `--no-publish`:** every eval run passes it.
- **A regex grader copied from the plugin's own sentence measures copying, not the rule:** before committing one, grade two paraphrased correct answers and one wrong answer that uses its key word in another sense.
- **A plan that adds or removes a hook handler lists `tests/test_repo.py`** (`DuplicateHookTests` pins the handler count per plugin).
- **`claude -p` disables `AskUserQuestion`:** a hook proof on it runs in SDK mode (`--input-format stream-json --output-format stream-json --permission-prompt-tool stdio`) and first checks the tool reached the stub's request.
- **Hook wiring is proven against `plugins/rigger-rules/tests/stub_api.py` behind `ANTHROPIC_BASE_URL`** in an isolated config (no cost, no real model); the report says "stub". An eval suite's wiring too: the stub's requests show what reached each case, and a stub that answers with a `Skill` tool_use runs the `tool_used` graders both ways. A `--bg` session cannot run through the stub (it blocks).
- **A stub handler finds its trigger by the expanded `--plugin-dir` path,** never by a word near it: a slash command's body is its own text block, and a word that only the worktree path carried passed the gate.
- **`claude -p` rewrites an inherited `CLAUDE_CODE_ENTRYPOINT=cli` to `sdk-cli`** (`claude-desktop` stays): `cli` cannot be proved through `-p`.
- **`claude rm` deletes a `--bg` session's job dir (`CLAUDE_JOB_DIR`):** copy its report out first.
- **A `tool_used` grader with `max: 0` also needs `min: 0`** (the default `min: 1` makes it "expected 1..0", never passing).
- **Read the CLI version right before the first real run:** auto-update can move the `claude` on PATH mid-task, and a desktop app may run its own copy.
- **Before a stub URL or an isolation runs next to a real config,** list every inherited env override against each enabled hook with third-party plugins on, prove the isolation with a canary on a scratch config first, and abort on any FAIL.
- **`autoCompactWindow` at or above the compaction guard's caps silently disables `safe_to_compact`** (`compact_guard.py` runs with `--hard-cap 560000` and, for a session that coordinates others, `--coordinator-cap 650000`; 450k is the script's default): before changing any threshold, list every other limit compared against it.
- **A test of `gate.py`/`run_tests.py` `main` patches the part that runs things even when it expects an early exit:** a mutant that passes the flag check runs the whole suite recursively, and a red script that times out kills the process tree and checks for orphans.
- **A time bound is measured at two sizes with realistic entries,** so growth shows: one at 100 short entries hid a quadratic awk scan (3.4 s at 500).
- **A `sh` hook over `installed_plugins.json` scans it in one awk pass that reads the file as one record** (`RS` set to a byte JSON never holds) and spawns nothing per unrelated plugin; time it under mawk and busybox too: a subshell per entry cost 6.3 s at 100 plugins under Git Bash, and joining lines into one string is quadratic in mawk (3.9 s) and busybox (28 s) at 2,000 entries while gawk stays linear.
- **Git Bash's gawk drops the CR of a CRLF line it reads:** a mutant that removes `\r` from an awk regex dies only on the POSIX leg, so run CR-sensitive mutants on both.
- **`tests/test_shellcheck.py` lints tracked files only:** `git add` a new `.sh` file before its first run.
- **One hook script copied into several plugins passes each plugin's name as an argument** (`core_check.sh <plugin>`): identical commands trip rigger-core's duplicate-hook warning, and the name keeps the lines apart.
- **A module loaded through `--plugin-dir` or `RIGGER_MODULE_DIRS` reads its options from `pluginConfigs["<plugin>@inline"]`,** an installed one from `<plugin>@<marketplace>`: a test that writes `@rigger` for a dirs module silently gets the defaults.
- **A hook-wiring test needs a fixture whose sections each fill a part:** joining the parts and comparing with `--render` passes with the last handler missing when the real rules fit in fewer parts.
- **A red run that mutates discovery also mutates an expectation computed by the same code** (`--render`): pin the expected side to an independent source, such as the manifests.
- **An invalid plugin `hooks.json` leaves the plugin in the init event's `plugins` list:** a hook-load proof reads the `Hooks: <Event>:<matcher>` debug lines.
- **With `CLAUDE_CODE_PLUGIN_CACHE_DIR` set, `claude plugin install` writes `installed_plugins.json` under that cache dir,** not `<config>/plugins/`, so `core_check.sh` stays silent there.
- **A plan's "base unchanged" check names the `rules/` and `hooks/` paths it is about,** never a whole plugin folder that an earlier task may add a file to.
- **A path that only has to be absolute is made with `abspath`, not a Path's `resolve()`,** which rewrites a symlinked temp folder (`/var` to `/private/var` on macOS) and breaks a comparison with the temp path only there.
- **A doctor test with a stub interpreter cannot see what the real one writes:** run the real interpreter once on a copy of the plugin and assert the copy is unchanged (`__pycache__` appeared where doctor promises no file).
- **Gate:** `python -I scripts/gate.py` in the background (every input optional; `CONTRIBUTING.md`
  lists them); its first line names `status.txt`, read that instead of rerunning. A leg whose output
  has no count, or a count of 0, fails ("printed no count matching …" / "count 0 from …"); a PASS
  line ends with its count and, for the suites, the number of SKIP files; a leg it cannot run
  prints `SKIP <leg>: <reason>`. Between fixes `python -I scripts/run_tests.py --fix-round`, never
  as the gate; add `--no-claude` when no `claude` run is allowed, or `test_reinject.py` starts
  `claude -p`. Before the gate, a change inside one module's folder may run
  `python -I scripts/run_tests.py --changed-modules` as a fast pre-check; it never replaces the gate
  for a pull request or a release, and exit 2 means a shared file changed. A change to a rules file or a skill runs `test_plugin_rules` and `test_drift_lint` in
  the same round as the edit (text moved into a placeholder leaves a stale drift-lint baseline row,
  itself a finding) and again before the merge; a commit chained after a test
  runs only on that test's exit code, with the full output kept in a file. The status file's
  `TREE <id>` line and the pass line's `tree <id>` name the content the gate ran on; before a merge
  run `python -I scripts/gate.py --verify <status.txt>`, and when the base moved, rerun the gate.
