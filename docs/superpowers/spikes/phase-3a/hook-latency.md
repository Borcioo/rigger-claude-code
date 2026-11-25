# Task 3: hook latency on three systems

## Question

What does each of the 42 hook handlers in `plugins/*/hooks/hooks.json` cost a person per call? The
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
    With none found, the script fails with `no Git Bash: …`.
  - **Linux:** `/bin/sh -c <command>`.
  - **macOS:** assumed to be `/bin/sh -c <command>` (not observed). The `# launcher` line of the CI
    log records what `/bin/sh` is there.
- **Install:** every plugin runs from a temp copy laid out like the plugin cache.
  - The config dir lists every plugin as installed and enabled, and holds an 8 KB personal
    `rules.d/personal.md`. With the plugins' rules, that fills session parts 1 to 5; personal rules
    are capped at 9,000 bytes.
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
- **Handlers with no `non-match` case:** the SessionStart, SubagentStart, PreCompact and PostCompact
  handlers, `report_shadow.py`, and the two `env_check.py` handlers.
  - Claude Code's matcher already picks the event, and the hook does the same work on every call it
    gets.
  - `env_check.py` has an `if`, so its non-matching call is not started.
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
- **Side-session guard:** from 1 MiB of transcript it keeps an offset cache beside the state file.
  - `match-large` deletes the cache before each run, so every run reads the whole transcript (cold).
  - `match-large-warm` keeps it, so a run reads only what the transcript gained, which here is
    nothing.
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
    `compaction postponed`, `Context is at 300k tokens`, `Worktree root: `, or a shell-guard
    `"deny"`.
  - **Proof by a written row**, refused when the row holds an `error` verdict or contract:
    `report_shadow.py` writes `verdict: ok` with `contract: refused:json`; `stop_shadow.py` writes
    `verdict: flag` with `code_changes: 1`. The `stop_shadow.py` non-match must write no row.
  - **Proof by a written summary file:** `compact_guard.py post`.
  - **Proof by silence, with no output to check:** `core_check.sh`, and `part.sh` parts 6 and
    subagent 4. Those parts are silent only when the assembler exits 10, which is an empty part;
    every failure prints a `not loaded` line.
- **Launcher, read through `spawn`:**
  - Windows: `$0` is `/usr/bin/bash`, with `BASH_VERSION` `5.2.37(1)-release`.
  - Linux: `$0` is `/bin/sh`, with no `BASH_VERSION`; `/bin/sh` is `/usr/bin/dash`.
  - With the launcher swapped for Git's `usr\bin\sh.exe`, the test reads `/usr/bin/sh` and fails.
  - A PATH with no git, or with a `git.exe` that has no Git Bash above it, gives the `no Git Bash:`
    reason.
- **Live config:** untouched. During the runs, the only files that changed under `<home>/.claude`
  belonged to other sessions, and the worktree's `git status` showed only the new files.

## Observed

### Windows and Linux

Times are median / p95 in ms. Each line's floor is the start floor of its run. No quiet window came:
other parts' gates ran through the whole task.

| Run | Started | Load | Floor start | Floor end | Wall time |
|---|---|---|---|---|---|
| Win 1 | 02:46 | Loaded: another part's `gate.py --changed-modules`, and a third part's mutant runs | 44 / 47 | 44 / 47 | 238 s |
| Win 3 | 03:19 | Heavy: three gates of other parts, CPU 76 % at the end | 42 / 47 | 81 / 109 | 381 s |
| Win 4 | 03:33 | Loaded: one other gate started during the run, CPU 28–49 % | 51 / 54 | 52 / 55 | 368 s |
| Linux | 03:26 | WSL, load average 0.09 before and 0.80 after | 1 / 1 | 1 / 1 | 49 s |

- **Win 1 ran the script one revision earlier**, with these cases the same except two:
  - Its side-session `match-large` kept the offset cache, so it is listed as `match-large-warm`.
  - The Stop non-match cases did not exist yet.
- **Win 3, Win 4 and Linux ran the committed script.**
- Win 1 is the lightest-loaded reading. Win 3 and Win 4 show how far load moves the numbers.

| Plugin | Event:matcher | Handler | Case | Win 1 | Win 3 | Win 4 | Linux |
|---|---|---|---|---|---|---|---|
| rigger-core | SessionStart:startup | `harness_start.py` | match | 149 / 156 | 146 / 162 | 173 / 178 | 24 / 27 |
| rigger-core | SessionStart:compact | `compact_guard.py:start` | match | 139 / 170 | 136 / 157 | 145 / 160 | 17 / 19 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:1` | match | 248 / 285 | 348 / 489 | 255 / 289 | 53 / 68 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:2` | match | 237 / 268 | 372 / 508 | 233 / 255 | 59 / 80 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:3` | match | 217 / 242 | 289 / 498 | 258 / 305 | 56 / 70 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:4` | match | 212 / 252 | 285 / 321 | 246 / 268 | 55 / 70 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:5` | match | 242 / 269 | 283 / 396 | 232 / 270 | 53 / 66 |
| rigger-core | SessionStart:startup\|clear\|compact | `part.sh:6` | match (empty part) | 210 / 252 | 413 / 635 | 240 / 308 | 45 / 50 |
| rigger-core | SubagentStart:* | `part.sh:1,--subagent` | match | 215 / 260 | 285 / 495 | 291 / 453 | 48 / 53 |
| rigger-core | SubagentStart:* | `part.sh:2,--subagent` | match | 208 / 237 | 274 / 294 | 408 / 482 | 51 / 55 |
| rigger-core | SubagentStart:* | `part.sh:3,--subagent` | match | 202 / 237 | 306 / 330 | 333 / 455 | 47 / 50 |
| rigger-core | SubagentStart:* | `part.sh:4,--subagent` | match (empty part) | 199 / 226 | 281 / 311 | 312 / 359 | 43 / 47 |
| rigger-core | PreCompact:auto | `compact_guard.py:pre,…` | match | 130 / 148 | 176 / 185 | 192 / 224 | 16 / 18 |
| rigger-core | PreCompact:auto | `compact_guard.py:pre,…` | match-large | 129 / 156 | 182 / 201 | 204 / 223 | 17 / 19 |
| rigger-core | PostCompact:* | `compact_guard.py:post` | match | 123 / 139 | 165 / 178 | 269 / 428 | 15 / 17 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | match | 114 / 126 | 159 / 184 | 166 / 190 | 13 / 15 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | match-large | 114 / 135 | 160 / 205 | 166 / 202 | 15 / 16 |
| rigger-core | UserPromptSubmit:* | `context_guard.py` | non-match | 113 / 139 | 165 / 183 | 180 / 204 | 13 / 17 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | match | 119 / 136 | 172 / 213 | 181 / 207 | 14 / 15 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | match-large (cold) | — | 189 / 235 | 183 / 193 | 21 / 24 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | match-large-warm | 119 / 134 | 244 / 371 | 182 / 192 | 17 / 20 |
| rigger-core | PreToolUse:mcp__ccd_turn__wrap_up | `side_session_guard.py` | non-match | 117 / 137 | 290 / 332 | 172 / 191 | 17 / 19 |
| rigger-core | PreToolUse:Bash | `weak_test_guard.py@Bash(*commit*)` | match | 197 / 225 | 346 / 397 | 294 / 323 | 42 / 50 |
| rigger-core | PreToolUse:Bash | `weak_test_guard.py@Bash(*commit*)` | match-large | 345 / 391 | 508 / 547 | 514 / 619 | 109 / 120 |
| rigger-core | PreToolUse:PowerShell | `weak_test_guard.py@PowerShell(*commit*)` | match | 201 / 225 | 277 / 291 | 391 / 734 | 40 / 44 |
| rigger-core | PreToolUse:PowerShell | `weak_test_guard.py@PowerShell(*commit*)` | match-large | 408 / 430 | 497 / 579 | 833 / 1093 | 143 / 193 |
| rigger-core | PreToolUse:AskUserQuestion | `side_session_guard.py` | match | 136 / 141 | 165 / 182 | 241 / 290 | 18 / 22 |
| rigger-core | PreToolUse:AskUserQuestion | `side_session_guard.py` | non-match | 123 / 139 | 175 / 220 | 273 / 321 | 18 / 25 |
| rigger-core | PostToolUse:Write\|Edit | `env_check.py:--hook@Write(…environments.json)` | match | 119 / 140 | 175 / 232 | 190 / 290 | 16 / 17 |
| rigger-core | PostToolUse:Write\|Edit | `env_check.py:--hook@Edit(…environments.json)` | match | 122 / 140 | 156 / 175 | 177 / 196 | 14 / 16 |
| rigger-core | SubagentStop:* | `report_shadow.py` (async) | match | 185 / 200 | 217 / 247 | 256 / 377 | 31 / 43 |
| rigger-core | SubagentStop:* | `report_shadow.py` (async) | match-large | 169 / 182 | 219 / 234 | 264 / 296 | 31 / 41 |
| rigger-core | Stop:* | `side_session_guard.py` | match | 121 / 137 | 165 / 174 | 188 / 216 | 14 / 16 |
| rigger-core | Stop:* | `side_session_guard.py` | match-large (cold) | — | 276 / 380 | 206 / 275 | 19 / 21 |
| rigger-core | Stop:* | `side_session_guard.py` | match-large-warm | 126 / 151 | 260 / 335 | 194 / 251 | 14 / 15 |
| rigger-core | Stop:* | `side_session_guard.py` | non-match | — | 280 / 341 | 176 / 189 | 14 / 15 |
| rigger-lessons | SessionStart:* | `lessons_start.py` | match | 133 / 160 | 223 / 287 | 211 / 229 | 25 / 27 |
| rigger-lessons | SessionStart:* | `lessons_start.py` | match-large | 160 / 172 | 373 / 451 | 219 / 241 | 31 / 34 |
| rigger-lessons | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-lessons` | match | 114 / 127 | 291 / 428 | 179 / 202 | 3 / 4 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Edit(*lessons-inbox.md)` | match | 125 / 146 | 306 / 359 | 186 / 207 | 17 / 19 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Edit(*lessons-inbox.md)` | match-large | 134 / 153 | 302 / 470 | 236 / 368 | 19 / 20 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Write(*lessons-inbox.md)` | match | 130 / 148 | 230 / 450 | 298 / 385 | 16 / 17 |
| rigger-lessons | PreToolUse:Edit\|Write | `inbox_guard.py@Write(*lessons-inbox.md)` | match-large | 129 / 153 | 200 / 242 | 315 / 418 | 21 / 25 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | match | 130 / 149 | 178 / 238 | 271 / 349 | 15 / 16 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | match-large | 145 / 159 | 200 / 220 | 196 / 244 | 19 / 21 |
| rigger-lessons | PostToolUse:Skill | `skill_lessons.py` | non-match | 124 / 143 | 179 / 187 | 193 / 213 | 16 / 18 |
| rigger-rules | SessionStart:startup\|clear\|compact | `worktree_root.sh` | match | 53 / 60 | 76 / 80 | 82 / 95 | 1 / 1 |
| rigger-rules | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-rules` | match | 111 / 127 | 166 / 174 | 167 / 189 | 3 / 4 |
| rigger-talk | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-talk` | match | 110 / 121 | 169 / 191 | 157 / 167 | 3 / 4 |
| rigger-windows | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-windows` | match | 121 / 150 | 164 / 193 | 168 / 176 | 3 / 3 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | match | 148 / 168 | 199 / 204 | 207 / 218 | 35 / 38 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | match-large | 146 / 179 | 215 / 249 | 205 / 215 | 41 / 43 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | non-match | 168 / 183 | 206 / 314 | 202 / 219 | 35 / 36 |
| rigger-windows | PreToolUse:PowerShell | `shell_guard.py:--pwsh-all` | non-match-large | 174 / 186 | 307 / 427 | 200 / 221 | 38 / 44 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*git*)` | match | 143 / 161 | 346 / 418 | 188 / 201 | 36 / 40 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*git*)` | match-large | 163 / 174 | 265 / 436 | 189 / 198 | 38 / 42 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*worktree%20remove%20*)` | match | 165 / 178 | 203 / 218 | 188 / 214 | 41 / 48 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*worktree%20remove%20*)` | match-large | 169 / 183 | 210 / 227 | 192 / 202 | 48 / 51 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*push%20*--no-veri*)` | match | 162 / 177 | 207 / 229 | 184 / 198 | 36 / 42 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-git@Bash(*push%20*--no-veri*)` | match-large | 166 / 174 | 201 / 218 | 186 / 205 | 39 / 45 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*tee%20*)` | match | 161 / 170 | 198 / 206 | 182 / 194 | 33 / 37 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*tee%20*)` | match-large | 141 / 155 | 200 / 212 | 182 / 220 | 34 / 38 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*safe-edit*)` | match | 138 / 156 | 208 / 224 | 193 / 230 | 32 / 35 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*safe-edit*)` | match-large | 141 / 147 | 200 / 229 | 200 / 226 | 34 / 37 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*<<*)` | match | 144 / 158 | 196 / 207 | 190 / 203 | 33 / 38 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*<<*)` | match-large | 142 / 165 | 197 / 212 | 185 / 199 | 33 / 36 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*\\\\\\\\*)` | match | 147 / 165 | 198 / 203 | 189 / 211 | 34 / 41 |
| rigger-windows | PreToolUse:Bash | `shell_guard.py:--only-narrow@Bash(*\\\\\\\\*)` | match-large | 147 / 162 | 199 / 217 | 187 / 195 | 36 / 44 |
| rigger-windows | Stop:* | `stop_shadow.py` (async) | match | 132 / 145 | 183 / 189 | 176 / 187 | 19 / 21 |
| rigger-windows | Stop:* | `stop_shadow.py` (async) | match-large | 229 / 251 | 479 / 533 | 328 / 1374 | 117 / 134 |
| rigger-windows | Stop:* | `stop_shadow.py` (async) | non-match (large transcript) | — | 244 / 291 | 155 / 163 | 13 / 14 |
| rigger-wp | SessionStart:startup\|clear\|compact | `core_check.sh:rigger-wp` | match | 128 / 139 | 231 / 316 | 146 / 155 | 3 / 3 |

- Every `if` handler's non-matching call is `not started`.
- The script prints `%20` for a space inside an `if` value.
- On Linux, `git worktree remove` takes the allow path. The rule looks for junctions, which only
  Windows has, so the check walks the whole tree and finds nothing. On Windows the worktree holds a
  junction, last in the walk, and the hook denies.

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

These are the handlers on the per-command path with a case median over 50 ms. The Windows range
covers runs Win 1 to Win 4:

| Handler | Windows median, max over its cases | Linux median, max |
|---|---|---|
| `weak_test_guard.py` (`Bash` and `PowerShell`, `if *commit*`) | 345–833 (large diff), 197–391 (small) | 109–143 (large), 40–42 (small) |
| `shell_guard.py --pwsh-all` (every `PowerShell` call) | 174–307 | 41 |
| `shell_guard.py --only-git` (3 `if` handlers) | 163–346 | 38–48 |
| `shell_guard.py --only-narrow` (4 `if` handlers) | 141–208 | 33–36 |
| `inbox_guard.py` (2 `if` handlers) | 130–315 | 19–21 |
| `env_check.py --hook` (2 `if` handlers) | 119–190 | 14–16 |
| `context_guard.py` (every prompt) | 114–180 | 15 |

- **On Windows every per-command handler is over 50 ms.** The launcher and the hook's own `sh` take
  60 ms before Python starts. A non-matching call costs as much as a matching one: in Win 1,
  `shell_guard.py --pwsh-all` reads 148 ms on a match and 168 ms on a non-match, and
  `context_guard.py` 114 and 113 ms. So the cost is process starts, not script work.
- **On Linux only two handlers are over 50 ms:**
  - the weak-test guard on a 300-file diff (109–143 ms);
  - `git worktree remove` on a 3,000-file tree, at 48–54 ms across two runs.
- **Only three handlers grow with input size:**
  - the weak-test guard, with the diff: about 1.7 times on Windows and 2.6–3.6 times on Linux;
  - `stop_shadow.py`, with the transcript: 19 to 117 ms on Linux. It is async, so the person does
    not wait for it.
  - `git worktree remove` on Linux, with the tree.
- **Inputs that cost little or nothing:**
  - The side-session guard's cold read of 4.2 MB costs about 5–7 ms on Linux over its warm read
    (21 against 17, 19 against 14). On Windows that difference is inside the load noise.
  - The other transcript readers read from the end or within a bound, and stay flat.
  - So do the inbox readers at 3,000 entries and the shell-guard rules at 3.9 KB commands.
- **Off the per-command path:**
  - `core_check.sh` costs 110–291 ms on Windows against 3 ms on Linux. It is pure `sh` and `awk`,
    so Git Bash's process emulation is the cost.
  - Each `part.sh` costs about 200–400 ms on Windows. A session start runs six of them in
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
  - `harness_start.py` with more plugins than these seven.
- **Windows numbers come from a loaded machine.** A quiet rerun, or the windows-latest CI run, gives
  the cleaner reading.
