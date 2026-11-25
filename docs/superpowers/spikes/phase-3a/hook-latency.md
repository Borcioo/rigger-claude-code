# Task 3: hook latency on three systems

## Question

What does each of the 42 hook handlers in `plugins/*/hooks/hooks.json` cost a person, per call? The
cost is the time from the start of the launcher Claude Code uses to the end of the last process the
handler starts. It is measured on a matching call and on a non-matching one, at two input sizes where
the cost could grow, on Windows, Linux and macOS.

Which handlers on the per-command path cost more than 50 ms, and so are candidates for a Rust port in
phase 4? The per-command path is PreToolUse and PostToolUse on `Bash`, `PowerShell`, `Write` or
`Edit`, plus UserPromptSubmit.

## Setup

- **Script:** `python -I -X utf8 scripts/hook_latency.py --runs 20 --json <file>`.
  - Each case gets 20 measured runs after one unmeasured warm-up run.
  - It reports the median and the nearest-rank p95.
- **Launcher (Task 17 (c)):** each handler's command starts as written, so the shell expands
  `${CLAUDE_PLUGIN_ROOT}`.
  - **Windows:** `<Git>\bin\bash.exe -c <command>`. The script finds `<Git>` from the `git` on PATH:
    `review_scope.find_program`, then the first of git's folder and the two above it that holds both
    `bin\bash.exe` and `usr\bin\bash.exe`. How Claude Code itself finds that file was not observed.
    The script fails with `no Git Bash: …` when it finds none.
  - **Linux:** `/bin/sh -c <command>`.
  - **macOS:** assumed to be `/bin/sh -c <command>` (not observed). The `# launcher` line of the CI
    log records what `/bin/sh` is there.
- **Install:** every plugin runs from a temp copy laid out like the plugin cache.
  - The config dir lists every plugin as installed and enabled, and holds an 8 KB personal
    `rules.d/personal.md`. With the plugins' rules, that fills session parts 1 to 5.
  - Each plugin has its own scratch `CLAUDE_PLUGIN_DATA`, and `HOME` is fake.
  - The child environment drops `CLAUDE_*`, `HARNESS_*`, `RIGGER_*`, `ANTHROPIC_*` and `GIT_*`. It
    sets `HARNESS_LESSONS_INBOX` to scratch and `HARNESS_LESSONS_BATCH_OFF=1`.
  - Git runs with no system or global config.
  - The project is a temp git repository, and it is also the working directory.
- **Case names:**
  - `match` takes the hook's working path: a deny, a block, a reminder, a printed rules part or a
    written row.
  - `non-match` is a call Claude Code starts but the hook has nothing to do for.
  - A `-large` case has a grown input.
  - For a handler with an `if`, the non-matching call is `not started`: Claude Code filters it before
    starting a process.
- **Large inputs:**

  | Input | Small | Large |
  |---|---|---|
  | Transcript | 2.8 KB | 4.2 MB of Bash calls inside the last turn |
  | Lessons inbox | 5 entries | 3,000 entries, 545 KB |
  | Staged diff for the weak-test guard | one test file | that file plus 300 files of 50 changed lines |
  | Subagent report | 40 bytes | 200 KiB |
  | Worktree for `git worktree remove` | 10 files | 3,000 files |
  | Bash or PowerShell command | the bare command | 3.9 KB of padding before it, or a 1,200-line heredoc (9.6 KB) |
- **Ids:** every run gets fresh session, agent and tool-use ids, so a hook that writes one row per id
  never takes its "already done" path. The exception is a case whose state file pins the session.
- **Side-session guard:** it takes the cold path. With a fresh session there is no offset cache, so
  every run reads the whole transcript.
- **Machines:**

  | System | Machine | Shell | Python |
  |---|---|---|---|
  | Windows | Windows 11, 16 logical processors | Git for Windows, GNU bash 5.2.37 | 3.12.10, from the cache `py.sh` keeps |
  | Linux | WSL Ubuntu on the same machine, 6 processors, on its own file system | `/bin/sh` is dash | 3.10.12, git 2.34.1 |

## Control

- **The fixture in `tests/test_hook_latency.py`** has a fast handler (`exit 0`) and a Python handler
  that sleeps 0.2 s.
  - With 3 runs, the slow handler reads at least 200 ms. The fast one reads less than the slow one
    minus 150 ms.
  - The `if` handler's non-matching call reads `not started`.
  - Only the two PreToolUse `Bash` handlers give candidate lines; the SessionStart handler gives
    none.
- **Proof of the path:** each case's warm-up run must show that it took the path it names, or the
  run fails.
  - A case whose proof is missing fails the run (`test_a_case_that_misses_its_proof_fails_the_run`).
  - On the real tree every case passed its proof on both systems: `# hooks measured 42 of 42`.
  - **Proof by output:** for example `weak-test-guard: this commit weakens tests`,
    `compaction postponed`, `Context is at 300k tokens`, `Worktree root: `, or a shell-guard `"deny"`.
  - **Proof by a written row:** `report_shadow.py` and `stop_shadow.py`.
  - **Proof by a written summary file:** `compact_guard.py post`.
  - **Proof by silence, with no output to check:** `core_check.sh`, and `part.sh` parts 6 and
    subagent 4. Those parts are silent only when the assembler exits 10, which is an empty part;
    every failure prints a `not loaded` line.
- **Launcher, read through `spawn`:**
  - Windows: `$0` is `/usr/bin/bash`, with `BASH_VERSION` `5.2.37(1)-release`.
  - Linux: `$0` is `/bin/sh`, with no `BASH_VERSION`; `/bin/sh` is `/usr/bin/dash`.
  - With the launcher swapped for Git's `usr\bin\sh.exe`, the test reads `/usr/bin/sh` and fails.
- **Live config:** untouched. During the runs, the only files that changed under `<home>/.claude`
  belonged to other sessions, and the worktree's `git status` showed only the new files.

## Observed

### Windows

Times are median / p95 in ms. Each line's floor is the start floor of its run.

| Run | Started | Load | Floor start | Floor end | Wall time |
|---|---|---|---|---|---|
| 1 | 02:46 | Loaded: another part's `gate.py --changed-modules` and a third part's mutant runs | 44 / 47 | 44 / 47 | 238 s |
| 2 | 02:55 | More loaded: two other gates running (`--changed-modules` and a full gate), CPU 35–49 % | 63 / 102 | 47 / 49 | 305 s |

No quiet window came: other parts' gates ran through the whole task. Run 1 is the lighter-loaded
reading; run 2 shows how much load adds. Linux, measured under WSL right after run 1, is in the last
column.

| Plugin | Event:matcher | Handler | Case | Windows run 1 | Windows run 2 | Linux (WSL) |
|---|---|---|---|---|---|---|
| rigger-core | SessionStart:startup | `harness_start.py` | match | 149 / 156 | 165 / 177 | 18 / 23 |
| rigger-core | SessionStart:compact | `compact_guard.py:start` | match | 139 / 170 | 154 / 206 | 16 / 21 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:1` | match | 248 / 285 | 278 / 327 | 73 / 81 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:2` | match | 237 / 268 | 266 / 400 | 44 / 68 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:3` | match | 217 / 242 | 297 / 598 | 44 / 59 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:4` | match | 212 / 252 | 516 / 704 | 45 / 56 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:5` | match | 242 / 269 | 308 / 476 | 43 / 54 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:6` | match (empty part) | 210 / 252 | 260 / 421 | 42 / 49 |
| rigger-core | SubagentStart:* | `part.sh:1,--subagent` | match | 215 / 260 | 294 / 332 | 48 / 70 |
| rigger-core | SubagentStart:* | `part.sh:2,--subagent` | match | 208 / 237 | 277 / 321 | 46 / 56 |
| rigger-core | SubagentStart:* | `part.sh:3,--subagent` | match | 202 / 237 | 289 / 310 | 46 / 58 |
| rigger-core | SubagentStart:* | `part.sh:4,--subagent` | match (empty part) | 199 / 226 | 320 / 414 | 42 / 47 |
| rigger-core | PreCompact:auto | `compact_guard.py:pre,…` | match | 130 / 148 | 167 / 192 | 14 / 20 |
| rigger-core | PreCompact:auto | `compact_guard.py:pre,…` | match-large | 129 / 156 | 165 / 176 | 17 / 19 |
| rigger-core | PostCompact:* | `compact_guard.py:post` | match | 123 / 139 | 154 / 165 | 15 / 16 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | match | 114 / 126 | 146 / 156 | 15 / 17 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | match-large | 114 / 135 | 149 / 155 | 16 / 18 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | non-match | 113 / 139 | 146 / 155 | 15 / 18 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | match | 119 / 136 | 164 / 216 | 16 / 18 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | match-large | 119 / 134 | 222 / 248 | 15 / 16 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | non-match | 117 / 137 | 219 / 281 | 16 / 18 |
| rigger-core | PreToolUse:Bash | `weak_test_guard.py@Bash(*commit*)` | match | 197 / 225 | 326 / 400 | 36 / 47 |
| rigger-core | PreToolUse:Bash | `weak_test_guard.py@Bash(*commit*)` | match-large | 345 / 391 | 446 / 457 | 93 / 112 |
| rigger-core | PreToolUse:PowerShell | `weak_test_guard.py@PowerShell(*commit*)` | match | 201 / 225 | 263 / 320 | 34 / 42 |
| rigger-core | PreToolUse:PowerShell | `weak_test_guard.py@PowerShell(*commit*)` | match-large | 408 / 430 | 417 / 444 | 89 / 120 |
| rigger-core | PreToolUse:AskUserQuestion | `side_session_guard.py` | match | 136 / 141 | 142 / 151 | 14 / 17 |
| rigger-core | PreToolUse:AskUserQuestion | `side_session_guard.py` | non-match | 123 / 139 | 142 / 151 | 22 / 28 |
| rigger-core | PostToolUse:Write\|Edit | `env_check.py:--hook@Write(…environments.json)` | match | 119 / 140 | 136 / 145 | 17 / 22 |
| rigger-core | PostToolUse:Write\|Edit | `env_check.py:--hook@Edit(…environments.json)` | match | 122 / 140 | 138 / 148 | 17 / 22 |
| rigger-core | SubagentStop:* | `report_shadow.py` (async) | match | 185 / 200 | 188 / 194 | 42 / 49 |
| rigger-core | SubagentStop:* | `report_shadow.py` (async) | match-large | 169 / 182 | 190 / 195 | 44 / 49 |
| rigger-core | Stop:* | `side_session_guard.py` | match | 121 / 137 | 138 / 168 | 22 / 25 |
| rigger-core | Stop:* | `side_session_guard.py` | match-large | 126 / 151 | 145 / 275 | 18 / 21 |
| rigger-lessons | SessionStart:* | `lessons_start.py` | match | 133 / 160 | 155 / 161 | 33 / 42 |
| rigger-lessons | SessionStart:* | `lessons_start.py` | match-large | 160 / 172 | 168 / 179 | 35 / 42 |
| rigger-lessons | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-lessons` | match | 114 / 127 | 126 / 137 | 3 / 4 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Edit(*lessons-inbox.md)` | match | 125 / 146 | 143 / 152 | 18 / 20 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Edit(*lessons-inbox.md)` | match-large | 134 / 153 | 148 / 153 | 24 / 31 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Write(*lessons-inbox.md)` | match | 130 / 148 | 146 / 155 | 17 / 19 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Write(*lessons-inbox.md)` | match-large | 129 / 153 | 152 / 160 | 23 / 30 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | match | 130 / 149 | 145 / 148 | 18 / 22 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | match-large | 145 / 159 | 152 / 164 | 20 / 25 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | non-match | 124 / 143 | 147 / 153 | 20 / 23 |
| rigger-rules | SessionStart:startup\|clear\|compact | `worktree_root.sh` | match | 53 / 60 | 60 / 84 | 1 / 1 |
| rigger-rules | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-rules` | match | 111 / 127 | 125 / 134 | 3 / 4 |
| rigger-talk | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-talk` | match | 110 / 121 | 130 / 140 | 3 / 4 |
| rigger-windows | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-windows` | match | 121 / 150 | 126 / 138 | 3 / 4 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | match | 148 / 168 | 168 / 176 | 40 / 47 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | match-large | 146 / 179 | 190 / 439 | 46 / 60 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | non-match | 168 / 183 | 237 / 299 | 43 / 68 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | non-match-large | 174 / 186 | 233 / 267 | 43 / 47 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*git*)` | match | 143 / 161 | 207 / 271 | 39 / 43 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*git*)` | match-large | 163 / 174 | 201 / 218 | 40 / 46 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*worktree remove *)` | match | 165 / 178 | 198 / 238 | 42 / 48 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*worktree remove *)` | match-large | 169 / 183 | 202 / 228 | 54 / 63 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*push *--no-veri*)` | match | 162 / 177 | 195 / 265 | 39 / 47 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*push *--no-veri*)` | match-large | 166 / 174 | 253 / 393 | 42 / 48 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*tee *)` | match | 161 / 170 | 199 / 267 | 34 / 45 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*tee *)` | match-large | 141 / 155 | 191 / 212 | 35 / 46 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*safe-edit*)` | match | 138 / 156 | 186 / 201 | 34 / 47 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*safe-edit*)` | match-large | 141 / 147 | 186 / 207 | 34 / 39 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*<<*)` | match | 144 / 158 | 185 / 206 | 33 / 40 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*<<*)` | match-large | 142 / 165 | 173 / 188 | 33 / 37 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*\\\\\\\\*)` | match | 147 / 165 | 171 / 179 | 32 / 37 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*\\\\\\\\*)` | match-large | 147 / 162 | 172 / 184 | 33 / 40 |
| rigger-windows | Stop:* | `stop_shadow.py` (async) | match | 132 / 145 | 155 / 175 | 18 / 23 |
| rigger-windows | Stop:* | `stop_shadow.py` (async) | match-large | 229 / 251 | 302 / 324 | 109 / 114 |
| rigger-wp | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-wp` | match | 128 / 139 | 133 / 144 | 3 / 4 |

The script prints `%20` for a space inside an `if` value (the table shows the space). Every `if`
handler's non-matching call is `not started`. The `env_check.py` handlers have only an `if` case:
their non-matching call is not started.

On Linux, `git worktree remove` takes the allow path. The rule looks for junctions, which only Windows
has, so the check walks the whole tree and finds nothing. On Windows the worktree holds a junction,
last in the walk, and the hook denies.

### The floor and the launcher on Windows

One process, 20 runs each, at a CPU load of 26 %:

| Command | Median | p95 |
|---|---|---|
| `<Git>\bin\bash.exe -c 'exit 0'`: the launcher, and the script's floor | 46 | 48 |
| `<Git>\usr\bin\bash.exe -c 'exit 0'` | 29 | 43 |
| `<Git>\usr\bin\sh.exe -c 'exit 0'` | 30 | 50 |
| `<Git>\usr\bin\sh.exe -c 'sh -c "exit 0"'`: the plan's baseline form | 43 | 46 |
| `<Git>\bin\bash.exe -c 'sh -c "exit 0"'`: the launcher plus a hook's own `sh` | 60 | 64 |

The `bin\bash.exe` wrapper costs about 16 ms over `usr\bin\bash.exe`. Every rigger hook starts its
own `sh`, so a handler that does nothing costs about 60 ms on Windows before any script runs. On
Linux the floor is 1 ms.

### macOS and CI Linux

These are filled in Task 7, from the `hook latency` step of pull request 1's CI run (macOS-latest,
ubuntu-latest, windows-latest). Each `# launcher` line there names `/bin/sh` and, on macOS, the
shell it selects.

## Candidates

These are the handlers on the per-command path with a case median over 50 ms (run 1, Windows):

| Handler | Windows median, max over its cases | Linux median, max |
|---|---|---|
| `weak_test_guard.py` (`Bash` and `PowerShell`, `if *commit*`) | 345–408 (large diff), 197–201 (small) | 89–93 (large), 34–36 (small) |
| `shell_guard.py --pwsh-all` (every `PowerShell` call) | 174 | 46 |
| `shell_guard.py --only-git` (3 `if` handlers) | 163–169 | 40–54 |
| `shell_guard.py --only-narrow` (4 `if` handlers) | 141–161 | 33–35 |
| `inbox_guard.py` (2 `if` handlers) | 130–134 | 23–24 |
| `env_check.py --hook` (2 `if` handlers) | 119–122 | 17 |
| `context_guard.py` (every prompt) | 114 | 16 |

- **On Windows every per-command handler is over 50 ms.** The launcher and the hook's own `sh` take
  60 ms before Python starts. A non-matching call costs as much as a matching one:
  `shell_guard.py --pwsh-all` reads 148 ms on a match and 168 ms on a non-match, and
  `context_guard.py` 114 and 113 ms. So the cost is process starts, not script work.
- **On Linux only two handlers are over 50 ms:**
  - the weak-test guard on a 300-file diff (89–93 ms);
  - `git worktree remove` on a 3,000-file tree (54 ms).
- **Only three handlers grow with input size:**
  - the weak-test guard, with the diff: 197 to 345 ms on Windows, 36 to 93 ms on Linux;
  - `stop_shadow.py`, with the transcript: 132 to 229 ms on Windows, 18 to 109 ms on Linux. It is
    async, so the person does not wait for it.
  - `git worktree remove` on Linux, with the tree: 42 to 54 ms.

  The transcript readers stay flat at 4.2 MB, because they read from the end or within a bound. So
  do the inbox readers at 3,000 entries and the shell-guard rules at 3.9 KB commands.
- **Off the per-command path:**
  - `core_check.sh` costs 110–128 ms on Windows against 3 ms on Linux. It is pure `sh` and `awk`,
    so Git Bash's process emulation is the cost.
  - Each `part.sh` costs about 200–250 ms on Windows. A session start runs six of them in
    parallel.

## Decision

- **For phase 4:**
  - On Windows, a Rust port of a per-command script would save the Python start and the script's
    own work: roughly the median minus 60 ms. The launcher's 60 ms stays unless the hook command
    stops starting `sh` itself.
  - The first candidates are `shell_guard.py --pwsh-all` (every PowerShell call) and the weak-test
    guard (it grows with the diff). After them come `context_guard.py` (every prompt) and the other
    shell-guard handlers.
  - On Linux only the weak-test guard on large diffs is a candidate.
- **`hook_latency.py` stays a measurement:** CI runs it on the three systems with
  `continue-on-error`. It fails, with exit 1, only when a handler has no case or a case misses its
  proof, so a new handler cannot go unmeasured.
- **Not measured:**
  - shell-guard's commit-row path: a `git commit` in a repository with `.claude/gates.json`, which
    starts git and reads the transcript within a 2.5 s bound;
  - the side-session guard's warm offset cache;
  - `harness_start.py` with more plugins than these seven.
