# shell-guard — install / disable

A PreToolUse hook that denies Bash/PowerShell commands that are certain to fail or do damage (rules
derived from real failures in Claude Code transcripts on Windows; cases in `tests/`). Allowed
commands get no output, so they add no context.

Optional tools named in a fix, only when their environment variable points at the file (set them in
the `env` of your settings.json; unset, the deny says to call the program directly instead):

| Variable | File | Used by |
|---|---|---|
| `RIGGER_WPCLI_PHAR` | `wp-cli.phar` | `bat-metachar` on `wp` |

Herd and Composer paths come from `%USERPROFILE%` and `%APPDATA%`.

**Cost:** each handler that runs spawns Python: median 64 ms per call (20 runs of the `--pwsh-all`
handler below on `Get-Date`, Store Python 3.11). A deny reason is one or two sentences.

For PowerShell, a handler's `if` does not filter on the command text: every PowerShell handler
spawns on every PowerShell call. For Bash, `if` does filter. So PowerShell gets one handler that runs
all the installed rules, and Bash keeps one `if`-filtered handler per pattern.

## To install: the narrow hooks

The git rules (`check_git`) and the recurring-class rules (`check_narrow`), without the rest of the
guard. `--pwsh-all` runs both in one process, git first; the first deny wins.

| Rule | Denies |
|---|---|
| `ps-git-commit-stdin` | PowerShell `git commit -F -` / `--file=-` not fed by a pipe: the here-string after it becomes a pathspec and no commit is made |
| `git-restore-discards` | `git checkout -- <paths>`, `git checkout .`, `git checkout <words>` for each word that exists on disk (file or folder, against `git -C`, the location `cd` left, the event's `cwd`) unless it is the first word and names a commit, as in git (`git checkout feature` with a file `feature` switches branch; one `git rev-parse` runs, and a failed or timed-out one counts as a path); not `-b`/`-B`/`-d`/`--orphan`/`--detach`, and a redirect's target is no path. `git restore <paths>` (not `--staged` in any spelling git accepts down to `--st`, unless `-W`, `-SW`, `--w…` joins it; not `--ours`/`--theirs`/`-p`/`-m`): they discard every uncommitted edit in those files; intended ones (throwaway worktrees) pass with the marker |
| `gate-truncated-pipeline` | a pipeline that tees a full log, or executes `safe-edit.ps1` (by path, a quoted path with spaces too, `& "…"`, or as the script of `pwsh`/`powershell`/`bash`/`sh` `-File`/`-Command`/positional), cut with `Select-Object -First` (`-First:N`, `-f N`) or `head`; a script-block stage in between does not hide it |
| `bash-backslash-collapse` | a Bash tool command holding `\\` anywhere: Git Bash's runtime turns it into `\` when it reads the command line the tool starts it with, before bash sees quotes or a heredoc, so a script or a `sed -i` edit runs with other text than written. The reason names the line and shows it as bash gets it. A single backslash passes |
| `cli-inline-prose` | PowerShell `gh pr\|issue\|release … --body/-b/--notes/-n/--title/-t <text>` (also `--body=…`, `-b"…"`) whose text is a string opened by `"` holding `„“”`, or opened by `'` holding `‘’‚‛`: PowerShell ends the string there too, so gh gets it torn or without its quotes. Straight quotes, newlines, the other family and here-strings arrive intact and pass |
| `stale-write-var` | a `$var` the same command never assigns, written by `[IO.File]::WriteAll*` (content argument; the call must be code, not text inside a string or here-string), `Set-Content` (`-Value`, `-Value:`, quoted or parenthesised, or positional) or `Out-File -InputObject`, or piped into either (`$t \| Set-Content f`); automatic variables, `$env:`, `-OutVariable`, `$a, $b = …` and compound assignments (`+=`, `??=`) count as assigned; `Out-File -Append` is allowed (appending `$null` leaves the file as it was); comments are no code: an apostrophe in one opens no string, a call in one is not judged |
| `ps-bare-bash` | PowerShell `bash …` / `bash.exe …` as any statement, pipeline stage or block (`; bash`, `&& bash`, `\| bash`, `& bash`), and inside safe-edit's `-Run '…'` string (run with `pwsh -Command`): it starts WSL, not Git Bash. Git Bash or WSL by full path, `wsl …`, and bash as an argument or inside a string (`rg bash`, `ssh h "bash x"`) pass |
| `ps-dotnet-relative-path` | `[IO.File]::ReadAll*` / `WriteAll*` / `AppendAll*` and `[IO.Directory]::*` whose path (both paths for `Directory::Move`) is a relative literal (`'tools\x.php'`, `"out\$n.txt"`, `'~/x'`): .NET resolves it against the process directory, not the location `cd` set. Absolute paths, `$PWD`, `$env:`, `(Join-Path …)` and variables pass |
| `ps-native-nonewline` | a native program (not a cmdlet of any `Get-Verb` verb, its alias, or a function of the same command) piped straight into `Set-Content -NoNewline` (any prefix, not `:$false`), also `(git show …) \| Set-Content -NoNewline`: its lines are joined into one. `Get-Content -Raw`, `Out-String`, `-join "`n"` in between pass |
| `bat-metachar` | an argument to a launcher (`php`, `php8N`, `composer`, `wp`, `phpcs`, `vendor\bin\*`) that resolves to a `.bat`/`.cmd`, looked up as PowerShell does (the first `PATH` folder with a match wins; inside it `.ps1`, then `PATHEXT` in order, so a real `php.exe` passes; a bare name found nowhere passes; a name with a folder resolves against the event's `cwd`, and is unresolved once the command changed location before the call; in PowerShell an unresolved one still counts, as the launchers are `.bat` and the hook does not expand a variable or `~` or follow every `cd`: `& "$env:USERPROFILE\…\php84"`, `~\…\php`, `.\composer` after a `Set-Location`, `vendor\bin\*`; in Bash it does not, as Git Bash starts no `.bat` by a name without its extension: `~/bin/php`, `./composer`), which cmd.exe re-parses: `^ \| & < >` outside quotes once PowerShell has put the argument on the command line (wrapped in `"…"` when it is empty or holds whitespace outside its own quotes, a `\"` not counted; its own `"` unescaped, so each `"` toggles: `'[\"a\"=>1]'` writes a file `1]`, `'a "b\|c" d'` runs `c" d"`, `'"a b"\|c'` runs `c`), or a `%NAME%` of a defined environment variable (expanded even inside quotes). The fix names the program only when that file exists here |
| `ps-bad-parameter` | a bash flag a cmdlet rejects: `Select-String -Recurse/-B/-C/-i/-n/-E` and `-A <n>`, `ls -la`, `rm -rf`, `Where-Object -A`, `Get-Content -n`, `cd /d`, and `ls -l` with no path after it. `ls -l <path>` (`-LiteralPath`) and a bare `Select-String -A` (`-AllMatches`) run and pass |
| `git-no-verify` | The flag: `git commit` with `--no-verify` (or a prefix down to `--no-veri`), `-n` or a cluster holding it (`-nm`, `-anm`); `git push` and `git rebase` with `--no-verify` (down to `--no-veri`); `git merge --no-verify` (the whole word: git refuses a prefix there); `git am` with `--no-verify` (down to `--no-v`), `-n` or a cluster holding it (`-3n`); the last of `--no-verify`/`--verify` winning. The word as an option's value (`-m`, `--message`, `--author`, `git rebase -x`, `git am -p`…) and `-n` on push (dry run), merge and rebase (no diffstat) pass. `git cherry-pick --no-verify` passes: git refuses it. The override, on `commit`, `push`, `merge`, `rebase`, `cherry-pick` and `am`: `git -c <key>=…` or `git --config-env[=| ]<key>=…` with `core.hooksPath`, `include.path` or `includeIf.<condition>.path`, the key compared as git compares it (section and name in any case), a path inside the repo included, and a `-c` key the hook cannot read; `GIT_CONFIG_PARAMETERS` set to anything but empty, or `GIT_CONFIG_COUNT` with a `GIT_CONFIG_KEY_<n>` below it naming such a key or unreadable, or a count that is no literal number of up to 10 digits, set in the same command (`VAR=x git`, `env VAR=x git`, `export VAR=x; git`, `$env:VAR = 'x'; git`, the name in any case). `GIT_CONFIG_PARAMETERS` is not parsed, so a harmless value is refused too. `core.hooksPath` set in repository config (`git config core.hooksPath .husky`, Husky) is not this rule's business and passes |
| `git-worktree-junction` | `git worktree remove [--force] <path>` (git 2.53 emptied a junction's target with and without `--force`) when the tree holds a junction or volume mount point whose target is outside the tree (a `\\?\Volume{…}` target counts as outside, an unreadable one too; symbolic links do not count, git keeps their targets); found without following any, breadth first on one worker thread. A folder or entry the walk cannot read counts as an unreadable target. Also denies when the hook cannot tell the folder (a variable it cannot resolve, a relative path after a `cd` it cannot follow, no `cwd`, `git worktree list` timing out, or failing on a name that is no folder) and when the walks do not finish within the hook's budget (a folder that never answers, such as an offline share, counts as not finished; a tree of 37,760 folders takes 2.7 s through the hook): only links whose target is outside the tree matter, so list them with `Get-ChildItem <dir> -Recurse -Force -Attributes ReparsePoint \| Select-Object FullName, Target`, delete those pointing outside first, and when there are none add `# shell-guard: allow`. The path is resolved as git does: against `git -C`, the location `cd`/`Set-Location`/`Push-Location`/`Pop-Location` left in the same command, `$w = '<literal>'`, then the event's `cwd`; a relative name is first looked up among `git worktree list` by its last path components, as typed apart from ASCII case and with `\` read as `/`, in git's order, so a registered worktree wins over a plain folder of that name; one match is the tree judged (its folder gone, a prunable worktree: nothing to empty, so the hook allows), otherwise the path is (no such folder: git refuses, so the hook allows) |

`tests/test_rules.py` proves each rule on hand-written cases and on the synthetic
`tests/fixtures/corpus.jsonl`; `tests/test_commit_gate.py` proves the commit shadow rule on real
repositories and the `--no-verify` rows against real git. Check your own corpus with
`python -I tests/corpus_check.py --corpus <file>` (format in its docstring).

A leading assignment of the form `$name = …`, `[type]$name = …` or `$name += …`, with spaces around
the operator, does not hide the command: `$o = git commit -nm x`, `$r = bash …` and
`$r = & '…/safe-edit.ps1' … | Select-Object -First N` are judged as the command after `=`. The other
assignment forms still hide it (see the known gaps).

Only those two shapes of truncation are denied. `Select-Object -Last N` / `tail` read the whole
stream, so the exit code and the red run's restore line survive; cutting a test run to read its last
lines is an everyday idiom and stays allowed. "safe-edit" as a plain argument (`rg -n safe-edit docs`,
`Get-Content safe-edit.ps1`) is not the gate.

Known gaps (allowed today):
- G5 time limits: `git rev-parse` and `git worktree list` run through Git's `cmd\git.exe` launcher, and a
  timeout kills only the launcher, so a hanging real git runs on after the hook has ended. Neither call
  holds a pipe to it (`rev-parse` uses the exit code, `worktree list` writes to a temp file), so the hook
  does not wait for it. `os.path.isdir` and `os.lstat` on the target and `os.path.exists` on a checkout
  word run on the main thread, so a folder on an offline share can outlast the hook timeout, and the
  command then runs unchecked.
- Another way to start git: `& '…\git.exe' commit -n` (any path to git), `cmd /c git …`, `pwsh -c '…'`,
  `Invoke-Expression`, `bash -c '…'`.
- `git-restore-discards`: a checkout word the hook cannot place (a variable, a relative word after a
  `cd` it cannot follow, no `cwd`), a pathspec glob (`git checkout '*.txt'`), `--work-tree`, pathspec
  magic (`git checkout :/`, `':(top)README.md'`, `':/README.md'`, `HEAD ':/'`), `--pathspec-from-file=f`.
- `bat-metachar`: `$env:PATH = …` in the same command, and a PowerShell alias or function of the
  launcher's name, are not seen by the lookup.
- `cli-inline-prose`: `gh -R o/r pr create …` (a global flag before the subcommand hides it).
- `stale-write-var` denies though the variable is set: `Tee-Object -Variable d`, `-ErrorVariable e`,
  `Set-Variable`, `[ref]$n`, a dot-sourced script, a `param()` holding `[Parameter(Mandatory)]`; add the
  marker. It misses `$report > out.md`, `Set-Content f ${report}`, and an assignment that sits in a
  comment counts as one.
- A command the lexer cannot follow is not judged by any rule, and PowerShell may still run it: an
  apostrophe in one of the forms below leaves the lexer an unclosed string while PowerShell reads a
  comment. Typographic quotes are read as PowerShell reads them; when that fails, the text as typed gets a
  second pass. `$w = ‘literal’` is no literal for the git rules (a `git checkout $w` is not judged, a
  `git worktree remove $w` is denied as unknown).
- A comment glued to a value or an operator is read as code: `$a = 1# don't`, `$b[0]# don't`, `[int]# don't`,
  `$(x)# don't`, `@(1)# don't`, `Write-Output ('a')# don't`, `$a=#don't`, `$a=1,#don't`, `'a'#don't`,
  `$a = 1<# don't #>`: its apostrophe hides the rest of the command.
- A `#>` after a `<#` glued to a word: PowerShell reads a line comment there, the hook reads code.
  `Write-Output a<# why #>; git commit -nm x` is denied though git never runs, and
  `Write-Output a<# why #>'` hides the next line.
- U+00A0, `\f` and U+3000 are whitespace to PowerShell and word characters here: `Get-Date;<U+00A0>git …`
  names no git, and a comment after one is read as code.
- A here-string with CR-only line ends (`@'` + CR … CR + `'@`) is no here-string here: an apostrophe in it
  hides the rest of the command.
- Stop-parsing `--%`: the rest of its line is one raw argument to PowerShell and code here:
  `Write-Output --% don't` hides the next line.
- A command behind an assignment of one of these forms is not judged by any rule: `$o=git …` (no
  spaces), `$a.b = …`, `$a[0] = …`, `$a, $b = …`, `[string] $o = …` (a space after the type) and
  `${o} = …`.
- Bash: a gate inside `{ …; }`, inside a `while` loop, or not the last command of `( … )`, piped into
  `head`.
- PowerShell: the red-run driver called through an expression, `& (Join-Path … 'safe-edit.ps1') … |
  Select-Object -First N`.
- `ps-dotnet-relative-path`: a variable holding a relative literal, `$f = 'tools\x.php';
  [IO.File]::ReadAllText($f)` (the shape of both real failures; following it would also deny 6
  corpus_ok calls that worked only because the process directory happened to match); the other
  `[IO.File]` methods with a relative path (`Copy`, `Delete`, `Move`, `Exists`, `Open*`).
- `ps-native-nonewline`: `Out-File -NoNewline` / `Add-Content -NoNewline` (the same join),
  `Get-Content` without `-Raw` as the feeder, a pass-through cmdlet in between (`git show … |
  Select-Object -Skip 1 | Set-Content -NoNewline`), and native output reaching the pipe as an
  expression, `$(git show …) | Set-Content -NoNewline` or `$t = git show …; $t | Set-Content -NoNewline`.
  Over-blocks a native program that prints one line, `git rev-parse HEAD | Set-Content -NoNewline f`
  (harmless there; the line count depends on the arguments, so add the marker).
- `git-no-verify`: hooks switched off another way, `HUSKY=0 git commit …` / `$env:HUSKY = '0'`.
- `git-no-verify` in the Bash hook: an `if` pattern compares case, so `Git merge …` and `GIT commit …` never
  reach the rule (only `GIT push --no-verify` does, through `*push *--no-veri*`). The PowerShell hook reads every
  command.
- The Bash lexer does not read a command in an unquoted heredoc body, or in `${x:-…}` outside double quotes,
  whether in `$(…)` or backticks, and a backtick body that does not lex stays unread although bash runs the lines
  before the broken one.
- A heredoc delimiter holding `$'…'`, `$"…"`, `$(…)`, `$((…))` or `${x:-a b}` (`<<$'EOF'`, `<<$(x)`) is read
  otherwise than bash reads it: the commands after the heredoc are taken for its body and not judged.
- The Bash lexer takes `git` for a redirect's target in a process substitution: `cat <(git …)`, `cat <<(git …)`,
  `done < <(git …)`. No rule judges that command.
- `git-no-verify` does not see: `alias.*` through `-c` (the subcommand reads as the alias), a wrapper other
  than `env` (`command git`, `exec git`, `nohup git`), `GIT_CONFIG_GLOBAL=<file>` / `GIT_CONFIG_SYSTEM=<file>`,
  `git config core.hooksPath x && git commit`, Bash `set -a`, a splat (`git @a commit`), and PowerShell's
  other ways to set a variable (`Set-Item env:`, `New-Item env:`, `Set-Content env:`,
  `[Environment]::SetEnvironmentVariable`). `git pull --no-verify`, `git revert` and any other subcommand are
  not protected.
- `git-no-verify` does not read a flag in a word only the shell resolves: `--no-${x:-verify}`, a backtick inside a
  word (Bash `` --no-`echo verify` ``, PowerShell ``"--no-ver`ify"``), a line continuation inside double quotes
  (`"--no-\` + newline + `verify"`) or brace expansion (`git commit {--no-verify,-q}`). Nor `time -p git …`, an
  `env` option cluster that holds a value option (`env -iu X GIT_CONFIG_PARAMETERS=…`), or a Bash `k+=` after a
  literal `k=` (`k=core; k+=.hooksPath; git -c "$k=…" commit`), read as the first literal.
- `git-no-verify` denies though git would run the hooks: a variable cleared before the call by `unset`,
  `env -u` or `Remove-Item env:`, `--no-verify --no-no-verify`, read as the flag, and the flag as the value of an
  option the rule does not expand (`git commit --m --no-verify`, `git push -fo --no-verify origin main`); add the
  marker.
- The git rules in Bash: a `cd` in a pipeline stage or a background job (`cd x | cat`, `cd x &`) moves the
  location they read, though it does not outlast that stage.
- `bat-metachar`: in a double-quoted PowerShell argument the lexer keeps escapes raw, so `""` counts
  as two quotes (`php83 "a ""b|c"" d"` fails but passes), and `` `t ``, `` `n ``, `` `r `` are not
  whitespace, so an argument PowerShell wraps is judged unwrapped (`php83 "x`ty>c9b"` runs but is
  denied).
- `ps-bare-bash`: `pwsh -Command '… bash …'` outside safe-edit, and a safe-edit `-Run` string sent
  from the Bash tool.

The rigger-windows plugin installs these handlers in its `hooks/hooks.json`, each running
`sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/shell-guard/shell_guard.py" <flag> || true`;
there is nothing to add to settings.json (no `Bash(*gh …)` handlers: `cli-inline-prose` is
PowerShell-only). Keep the `|| true`: Python exits 2 on a missing script, and exit 2 blocks the tool
call. A deny is JSON on stdout with exit 0, so it passes through:

| Matcher | `if` | Flag | Timeout |
|---|---|---|---|
| `PowerShell` | none | `--pwsh-all` | 10 |
| `Bash` | `Bash(*git*)` | `--only-git` | 10 |
| `Bash` | `Bash(*worktree remove *)` | `--only-git` | 10 |
| `Bash` | `Bash(*push *--no-veri*)` | `--only-git` | 10 |
| `Bash` | `Bash(*tee *)` | `--only-narrow` | 5 |
| `Bash` | `Bash(*safe-edit*)` | `--only-narrow` | 5 |
| `Bash` | `Bash(*<<*)` | `--only-narrow` | 5 |
| `Bash` | `Bash(*\\\\\\\\*)` (16 backslashes in the JSON) | `--only-narrow` | 5 |
| `Stop` | none | `stop_shadow.py`, `async` | 10 |

One wide `*git*` pattern starts the git rules on any command with `git` in it, wherever it stands in a compound
command (`git`, `git.exe`, `git -c x commit`, `cd <dir> && git …`), and on `digit` or `github.com` too, where the rules
allow it. A pattern with a space (`*git *`) misses `git.exe`; a pattern per subcommand starts one Python process
per pattern on a command the host cannot split, 15 for one call. `*worktree remove *` stays for
`GIT worktree remove`: a leading `*` matches anywhere, so it covers `git -C <path> worktree remove` and
`cd <dir> && git worktree remove …` (the hook follows that `cd`), and the worktree rule resolves a relative path
against the event's `cwd`. `*push *--no-veri*` stays for `GIT push --no-verify`. An `if` compares case, so neither
reaches `Git merge …`. The host joins continuations and tabs, then matches each simple command on its own; a command
it cannot split (a bare `$(…)`, backticks, `${…}`, `for`, some heredocs) starts every handler that has an `if`, one
Python process each. An `if` is unescaped twice, as rule text and then as a pattern: eight backslashes in it match
`\\`, four match any single `\`. No `if` sees a heredoc body, so a `\\` there reaches the rule through `*<<*`.

The hook process has an 8 s budget for all its folder walks and `git worktree list` calls together,
however many `worktree remove`s one command holds; a walk not done by then is denied. `git rev-parse`
draws on the same budget. Every handler that runs the git rules (`--pwsh-all`, `--only-git`, the full
guard) therefore has `"timeout": 10`: a hook killed by its timeout lets the command run unchecked. The
file system calls on the main thread are not bounded by the budget (see Known gaps).

## Shadow gates

Two rules only write rows, they never deny: one at `git commit` (was a check passed after the newest committed file?), one at
Stop (does a closing claim rest on a check after the last edit?). A repo opts in with `.claude/gates.json`. With no file the
commit rule reads only the repo's `.git` and that path. The Stop rule learns the repos from the transcript, so on a closing
with a claim it opens the transcript and removes its row again, in the background, about 50 ms, no process started. Example:

```json
{
  "commit": "shadow",
  "checks": ["python -I scripts[\\\\/]run_tests\\.py"],
  "scope_commit": ["*.py", "src/**/*"],
  "scope_stop": ["*.py", "src/**/*"]
}
```

- `checks` are regexes on the command. Write every path separator as `[\\/]` (doubled in JSON): an agent types either.
- A glob with no slash matches the basename; `**/` spans folders, a bare `**` does not. `"commit": "shadow"` turns on the commit rule, `scope_stop` the Stop rule.
- Rows land under `CLAUDE_PLUGIN_DATA`: `commit-gate/rows/`, `turn-gate/rows/`. A commit waits for its row 2.5 s at most.
- Optional, to measure the gates: `python -I "<rigger-windows root>/tools/gate-lab/report.py" --data <the plugin's data folder>`
  copies `trials.template.json` to `trials.json` on its first run; `--due` lists the trials whose review date has come. The
  trials are yours to define; see `tools/gate-lab/replay.py` and `report.py` for the commands.
- `same_call` and `unknown/hidden` are not credit. Credit is `fresh` and `verified`.
- Known gaps: the reader parses shell text only as far as the rules need it. A zero-test run with exit 0, a failure in an
  earlier stage of a compound, a script a `checks` regex matches by name, a failed `cd`, curl exit 0 with an error body, or a
  shell edit after the check can read as credit. A hands-on check has no root filter. A subagent's closing writes no row.

## Install (the full guard)

Not installed: with PowerShell as the default shell, most of the failures it catches no longer
happen. Install it if you go back to using the Bash tool heavily, or for a short period to check.

The full guard's `check()` already runs `check_narrow` and `check_git`, so with it installed the
narrow handlers above are redundant; remove them.

Add this to your settings.json under `"hooks"` (merge with any existing `PreToolUse` list), with
`<rigger-windows root>` written out as the plugin's folder (forward slashes). The `args` form spawns
Python directly, without Git Bash:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["-I", "-S", "<rigger-windows root>/tools/shell-guard/shell_guard.py"],
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

If your Claude Code version rejects `args`, use the shell form instead (slower: it starts Git Bash
first):

```json
{ "type": "command", "command": "python -I -S <rigger-windows root>/tools/shell-guard/shell_guard.py", "timeout": 10 }
```

The hook reads the event JSON on stdin. It prints a `permissionDecision: "deny"` (ASCII JSON) only on
a match and exits 0 on anything else, including its own errors (fail-open).

## Disable

- One command: put `# shell-guard: allow` in it (a trailing comment works in both shells).
- Everywhere, temporarily: set `"env": { "SHELL_GUARD": "off" }` in settings.json.
- Permanently: disable the rigger-windows plugin (and delete any full-guard entry you added).

## Check after editing rules

Before a rule denies a command shape, print what the callee receives in the real shell for the
failing form and its nearest working forms (the trigger character inside and outside quotes), and
keep those as the rule's cases. Fix a false positive by narrowing the rule to the form that still
fails, never by dropping the whole flag, and give each new allow case a deny twin.

```powershell
python -I "<rigger-windows root>/tools/shell-guard/tests/test_rules.py"     # cases, git-only, narrow, worktree, checkout, walk, link kinds, budget, race, launchers, hook runs, synthetic corpus; ends "# shell-guard: <n> passed, <m> failed" (~45 s; makes junctions under a tempfile.mkdtemp() folder, fake home and PATH)
python -I "<rigger-windows root>/tools/shell-guard/tests/test_commit_gate.py"     # the commit shadow rule on real repositories and the hook-skipping rows through real git, 4 rows at a time; ends "# commit-gate: <n> passed, <m> failed" (~85 s; fake home)
python -I "<rigger-windows root>/tools/shell-guard/tests/test_walk_portable.py"   # the walk on this OS's own stat; the POSIX gate runs it
python -I "<rigger-windows root>/tools/shell-guard/tests/corpus_check.py" --corpus <your corpus.jsonl>   # your own mined calls; exit 2 without --corpus
```

- A change to a lexer runs the differential fuzz against the real parser (`Parser::ParseInput` for
  PowerShell, bash for Bash) before the commit; an exception in the differential is written by
  mechanism, never as one case; a new input shape gets one row per stateful consumer (the location
  tracker); a fuzz hit is replayed on the real tool before it is reported (a stub log counts echoes).
- To time a PreToolUse handler on real commands, add a catch-all deny in a temp config: the host
  waits for every started `if` handler even after a deny. Before fixing a host `if` pattern, fire
  each candidate spelling (`git`, `git.exe`, bare) as a probe handler over the command shapes and
  compare what starts.
