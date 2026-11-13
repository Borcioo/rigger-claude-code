---
name: windows-dev-env
description: >-
  Load BEFORE a shell script, loop or pipeline on native Windows (PowerShell 7, Git Bash, WSL),
  a git commit message from PowerShell, a script writing non-ASCII text or a file another program
  reads, or moving a repo or creating a link; and when a command fails like a syntax bug
  ("$'\r': command not found", mangled /path), a tool looks missing, a port stays busy, or the
  machine or Claude app crashed.
---

# Windows dev environment

## Shells

- **On native Windows use the PowerShell tool by default**; use the Bash tool only to run an
  existing POSIX script or a pipeline with no sane PowerShell equivalent.
- **PowerShell tool** = whatever its tool description says; check that first, and if it does not
  say, run `$PSVersionTable.PSVersion` once. On PowerShell 7 (pwsh, Core) `&&`/`||`, ternary, `??`,
  `?.` and UTF-8 default all work. It sees every tool on PATH (including `.bat` launchers such as
  Herd's `php83` and `composer`, which Git Bash cannot run) and has no MSYS path mangling or CRLF
  traps.
- **Bash tool** = Git Bash (MSYS2, GNU Bash 5.2). Unix syntax, forward slashes; keep it POSIX-only
  (no `$env:`, no cmdlets).
- **`bash` typed in PowerShell or CMD** resolves to `%SystemRoot%\System32\bash.exe` = **WSL**, not
  Git Bash: Windows paths and CRLF scripts break there (`$'\r'`). Run POSIX helper scripts (for
  example those of the separately installed superpowers plugin: `sdd-workspace`, `task-brief`,
  `review-package`) only through the Bash tool, by absolute path.
- **Windows PowerShell 5.1** (`powershell.exe`) is still installed. Only if you end up in it:
  no `&&`/`||` (chain with `;` and `if ($?) { … }`), no ternary/`??`/`?.`, no
  `ConvertFrom-Json -AsHashtable`, `Set-Content`/`Out-File` default to ANSI/UTF-16 (pass
  `-Encoding utf8`), `2>&1` on a native exe raises `NativeCommandError` even on exit 0. Do not
  call `pwsh -Command "…"` from 5.1 (quotes get mangled); write a `.ps1` and run
  `pwsh -NoProfile -File script.ps1`.

## PowerShell traps

- **PowerShell has no `sed` or `export`, and no `tail`/`head`/`grep`/`wc` unless coreutils is on
  PATH (see below)**: use `Select-Object -Last/-First`, `Select-String` or `rg`, `-replace`,
  `Measure-Object -Line`, `$env:X = 'v'`.
- **Bash flags on cmdlets fail**: `ls -la`, `rm -rf`, `Select-String -Recurse/-A/-C`,
  `cat -n`, `cd /d` → `Get-ChildItem -Force`, `Remove-Item -Recurse -Force`,
  `Get-ChildItem -Recurse | Select-String -Context 2,2`.
- **`| & < > ^` in an argument to a `.bat` wrapper** (`php`, `composer`, `wp`, `phpcs`,
  `vendor\bin\*`) is re-parsed by cmd.exe when the argument has no spaces:
  `--filter='A|B'` → "'B' is not recognized…", `^1.2` silently becomes `1.2`. Call the exe; on a machine with Herd:
  `& "$env:USERPROFILE\.config\herd\bin\php84\php.exe" vendor/bin/phpunit --filter 'A|B'`. `%name%` is expanded too,
  even inside quotes: in `wp db query` use `LOCATE()`/`REGEXP`, not `LIKE '%x%'`.
  After a `composer require` with a constraint, read `composer.json` and check the constraint is
  still the one you typed.
- **`ConvertFrom-Json` turns ISO-8601 strings into `DateTime`**, and `ConvertTo-Json` writes one
  with an offset back in local time (`+00:00` in, `+02:00` out). Look at the raw text, or use
  `ConvertFrom-Json -DateKind String` (7.5+), before reporting a date-format bug in an API.
- **`ConvertFrom-Json` refuses keys that differ only in case** (`~/.claude.json` project paths,
  plugin `marketplace.json`): use `ConvertFrom-Json -AsHashtable`, or `jq` for a one-shot read.
- **`Stop-Process` on a `pnpm`/`npm` wrapper leaves its `node` child running** and holding the
  port. Stop by port owner — `Get-NetTCPConnection -LocalPort <port> -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }` — or `taskkill /PID <pid> /T /F`
  for the tree, in a `finally`; an `OwningProcess` of 0 means no owner was read — never pass it to `taskkill /T`, and `-State Listen` can miss a live server (a second Python `HTTPServer` binds the same port without failing), so prove the stop with a request that fails; check the port is free before the next run. Stop or wait on a run
  by the PID you started, never by name (that kills other sessions' runs, and a Store Python runs
  as `python3.11.exe`, so a wait on `python.exe` ends at once; Git Bash has no `pkill`), and check
  it stopped before relaunching.
- **A native command piped into `Set-Content` arrives as lines**: `git show HEAD:f | Set-Content
  -NoNewline f` joins the whole file into one line. Put a committed blob back with
  `git restore --source HEAD -- f` (or `git cat-file blob HEAD:f > f` in the Bash tool).
- **The Claude Code tool guard reads text, not intent.** It refused `Remove-Item -Recurse` on a
  variable path ("system path '/' is blocked"), a `Remove-Item` whose path contained "LocalCache",
  an `ssh host '… rm -f ~/file'` string (the remote `rm` read as a local delete), a literal
  `Remove-Item -LiteralPath '<scratch root>\<own folder>' -Recurse` ("system path '<scratch root>' is blocked")
  and even a read-only `rg` whose pattern held "Remove-Item … wp-*", and a command whose text holds `cmd /c` beside the delete cmdlet ("system path '/c'"). Delete your own scratch folder
  with `[IO.Directory]::Delete('<absolute path>', $true)`; send remote commands with `rm` through
  the Bash tool or a script file on the server; search with the Grep tool.
- **Never assign to an automatic variable.** `$HOME`, `$PID`, `$Host`, `$PSHOME`, `$Error`,
  `$PSVersionTable` are read-only (`$home = …` fails too: names are case-insensitive); `$input`,
  `$args`, `$matches`, `$_`, `$PWD` are overwritten silently. Use `$homePage`, `$resp`. Text that
  contains `$` (commit messages, notes, code) goes in single quotes, a single-quoted here-string or
  the Write tool.
- **`@( @('a','b') )` flattens to two strings**, so a "list of pairs" loop with one pair iterates
  strings. Build a one-pair list as `,@('a','b')`, or make one replacement with the Edit tool.
- **A failed .NET call does not stop the next statement.** Chained read-then-write commands and every loop
  start with `$ErrorActionPreference = 'Stop'` and `Set-StrictMode -Version Latest`, so a throw or
  an unassigned variable stops the command instead of writing an empty file; the file a command
  reads exists before it runs; a loop in a sweep has an iteration bound and stops on the first error (a one-liner that errored per iteration wrote a 300 MB task output).
- **Never truncate a gate, test run or red run inside its own pipeline**: capture `$out = … 2>&1` or a file, then filter the variable.
- **Never pipe script text through PowerShell stdin** (`python -`, `php --`): non-ASCII characters
  arrive as U+FFFD, so an exact-match replace fails half-way through a file whose earlier
  replacements are already written. Write the script to a UTF-8 file and run it by path, or use the
  Edit tool.
- **An empty native argument is `''`, never `'""'`.** `ssh-keygen -N '""'` sets the passphrase to
  two quote characters. Run any command that may prompt (`ssh-add`, `ssh`, `git credential`)
  through `Start-Process … -PassThru` + `WaitForExit(<ms>)` so a prompt fails fast instead of
  hanging until the tool timeout.
- **`pwsh -File` passes `-X a,b` as one string** (a documented `-Exclude 'a','b'` let a real
  `rsync --delete` remove protected folders), and `ValueFromRemainingArguments` binds `-d` to
  `-Debug`: a script that takes a list runs once through `-File` before its doc ships, accepts a
  comma list or is called through `-Command`; forward raw args without an advanced-function binder.
- **Small traps:** `,` binds tighter than `+` (`@('a' + $nl + 'b', 'c')` is three strings);
  `[switch]$Flag` and `$flag` are one variable; `[math]::Min(30, 9.5)` returns 10 — cast `[double]`;
  `(a; b)` is a parse error (spike a shell shape before it enters a test table); under StrictMode an
  assert message that reads `$empty.Name` throws while being built — build it lazily.
- **Python's `select()` works only on sockets on Windows**, so a script that polls a subprocess pipe
  with it raises. Read the pipe in a thread, and patch such a tool only in a copy. In any probe,
  never send stderr to `DEVNULL`: an expired CLI session then reads as "the feature did not fire".
- **`HOME` is not the profile on Windows:** Windows OpenSSH and Node read `USERPROFILE`, Git Bash,
  Python and other ssh builds read `HOME`. A test that fakes a home sets the variable each program
  reads and prints which build ran.
- **If you do use the Bash tool: commands over ~7.8 KB are truncated** — `unexpected EOF`, or a
  heredoc cut short that writes a truncated file and exits 0. Write files with the Write tool.
- **In the Bash tool and in `bash.exe -c '…'` from PowerShell a `\\` collapses to `\` before bash
  parses the command** (measured: `printf '%s' 'a\\b'` and a `<<'EOF'` body both print one backslash;
  `\\n` in a Python string becomes a real newline), so quoting a heredoc delimiter does not help. A
  file holding backslashes goes through the Write tool, or a script file, and runs by path. A
  timed-out background batch outlives its shell: find the leftover by PID before restarting.
- **`$files.Length` on an array is its count, not bytes:** sum sizes with `($files | Measure-Object Length -Sum).Sum`.
- **`Set-Content` at the end of a pipeline creates its file at once:** wait on a final marker line, never on the file's existence.
- **pwsh wraps exception text to the console width:** match a long error message after collapsing whitespace.
- **`shutil.rmtree` over a git repo fails on read-only object files:** pass an error handler that `chmod`s the path and retries.

The shell-guard hook (this plugin's `tools/shell-guard/`) refuses `git commit -F -` from PowerShell
without a pipe and `git checkout -- <path>` / `git checkout <file>` / `git restore <path>` (they
discard every uncommitted edit in the file). Escape one command with a trailing `# shell-guard: allow` (a probe of the guard's own spellings goes in a script file, never inline); rules in its
`tools/shell-guard/INSTALL.md`.

## Git Bash (MSYS2) failure modes — recognise, don't debug

When a Bash command fails oddly, suspect these first and switch to PowerShell rather than fight:

- **`$'\r': command not found`** — CRLF line endings in the script or heredoc.
- **Path mangling** — an argument starting with `/` is rewritten to a Windows path
  (`--path=/api/v1` → `<Git install folder>/api/v1`). Use `MSYS_NO_PATHCONV=1 cmd …` or `//api/v1`.
- **Backslashes eaten** — `src\app\x` loses its separators. Use forward slashes.
- **Nested quotes / `;` in compound commands** re-parsed across the Windows→MSYS boundary →
  nonsensical "unexpected token" errors.
- **Interactive/TTY tools hang** instead of failing.
- **Start Git's shell as `bin\sh.exe` / `bin\bash.exe`, never `usr\bin\*`** (no `/usr/bin` on PATH:
  exit 127 in 30 ms looks like a fast pass — benchmarks print each call's exit code). `bin\bash.exe`
  prepends its own folders, so a stub `git` on PATH loses to the real one: stub with a shell
  function, and read the control before the results.

## Cost model: spawning processes, not doing work

Do not loop over files to search or read them; one `rg` / `Select-String` / Grep-tool call
replaces it (a Git Bash loop spawns a process per iteration, seconds for 18 files; one call takes
tens of milliseconds). A loop is fine when each iteration is a cmdlet, never one that starts a
program (git, php, node) per file. On native Windows, PowerShell beats Git Bash for file work;
inside WSL, POSIX tools are fine. A slow suite whose cases start Git `sh` gains little from running
cases side by side: cut the processes each case starts before pooling them.

Call Windows tools (`php`, `composer`, `wp`, `node`) directly, not through `bash -lc`.

## Text encoding

- Python's default `open()` encoding is the system ANSI code page (`locale.getpreferredencoding()`,
  e.g. **cp1250** on a Central European Windows), so a plain `open(path, 'w')` turns "…" into byte 0x85 and
  breaks non-ASCII letters. Always `open(path, 'w', encoding='utf-8')`; read with
  `encoding='utf-8'` too. `PYTHONUTF8=1` makes UTF-8 the default. It also hides bugs wherever it is set: code meant for other machines is tested with it cleared and a non-ASCII path (a launcher probe printed `sys.executable` in cp1250 and hooks exited 127); a launcher passes `-X utf8`, and under `python -I` `PYTHONIOENCODING` is ignored, so call `sys.stdout.reconfigure(encoding='utf-8')`.
- PowerShell 7 writes UTF-8 (no BOM) by default; 5.1 does not — pass `-Encoding utf8`, or use
  `[System.IO.File]::WriteAllText($p, $s, (New-Object System.Text.UTF8Encoding($false)))` when a
  BOM would break the consumer.
- After a scripted edit of a file with non-ASCII text, check the diff for mojibake before moving on.
- Source edits use the Edit tool or `safe-edit.ps1` (the "Text encoding" rule); a content script
  that matches escapes uses raw strings (`r"""…"""`). After a scripted edit,
  `rg -n '[\x00-\x08\x0B\x0C\x0E-\x1F]' <file>` finds stray control bytes (`"\b"` in a non-raw string is a backspace, `"\'"` a bare quote).
- PowerShell, .NET, node `Intl` and jsdom format by the Windows culture (with a culture such as
  **de-DE**, 1.5 as "1,5"). Tests that format numbers or dates pin the locale.

## Line endings

The Edit/Write tools and Windows programs can save an LF file as CRLF. The fix that costs
nothing is per repo: `.gitattributes` with `* text=auto eol=lf` plus `binary` lines for images,
fonts and `.mo`, at the first commit (template, if rigger-core is installed: its
`<installPath>/templates/gitattributes`, copied as `.gitattributes`).
For an existing repo, propose it once; with the user's OK land it as its own commit with
`git add --renormalize .`.
Until a repo has it, run `git diff --stat` after editing a tracked file; a diff far larger than
the change is line-ending churn — restore LF before committing.
A clone does not reproduce the working tree's bytes (a file with mixed line ends looks clean to git
through the index stat): before a script edits a tracked file by exact match, run
`git ls-files --eol <file>` on the real tree (`w/crlf` means a match on `\n` fails) and rehearse on
a byte copy of that file.

`[IO.File]::WriteAllLines` and Python's `Path.write_text` / `open(…, 'w')` write CRLF on Windows,
and a later replace on "`n" then silently matches nothing. Write with
`WriteAllText` and an LF-joined string, Python with `newline=''`, or the Write tool.

## .NET file calls ignore the PowerShell location

`[IO.File]::WriteAllText('x.js', …)` and other `[IO.*]` calls resolve a relative path against the
process working directory, not `Get-Location`. In agent sessions that is often a different checkout,
so the file silently lands in the wrong repo. Always pass an
absolute path: `[IO.File]::WriteAllText((Join-Path $PWD 'x.js'), $text, [Text.UTF8Encoding]::new($false))`.
Shell state does not survive between tool calls: read and rewrite in one call under the preamble above, or
append with the Edit tool or `Add-Content`.

## Commit messages from PowerShell

`git commit -F - @'…'@` does not feed the here-string to stdin: PowerShell passes it as an
argument (a pathspec), so the commit is not created — or a later commit sweeps up everything
staged — and `git push` then says "Everything up-to-date". The hook refuses it unless a pipe feeds
it. Use `git commit -m @'…'@` (closing `'@` at column 0), or write the message with the Write
tool and run `git commit -F <file>`. Confirm with `git log -1 --oneline`.

The same trap catches any prose given to a CLI (`gh pr create --body`). Prose that contains quotes goes in a single-quoted here-string,
`--body-file`, `-F <file>` or a Bash heredoc — never in a double-quoted inline argument.

## Links

`New-Item -ItemType Junction` works without admin rights (directories only). Symbolic links need
admin or Developer Mode — prefer junctions for directories. In Windows PowerShell 5.1 `New-Item -ItemType SymbolicLink` needs elevation even in Developer Mode; `cmd /c mklink` works in both shells.

## Deleting a folder that contains junctions deletes through them

`git worktree remove --force`, `Remove-Item -Recurse` and Explorer can follow a junction inside the
folder and empty its target. Before removing, list the junctions with
`Get-ChildItem <dir> -Recurse -Force -Attributes ReparsePoint`, delete each junction itself first
(`[IO.Directory]::Delete('<absolute junction path>')` removes the link, not the target), then remove
the folder.

## Long paths and local files in the browser pane

The Claude scratchpad path is so deep that a file git reads or writes there (clone, commit -F)
or an archive extract fails with "Filename too long": put those files in a short scratch folder
outside any repo, `<scratch root>\<task>\`. `<scratch root>` is a short folder you create once
(a folder directly under a drive root, a few characters long) and use wherever this skill says `<scratch root>`. The
browser pane also refuses `file:///` URLs — serve the folder instead, `python -m http.server
<port> --bind 127.0.0.1 --directory <dir>` in the background, and open `http://127.0.0.1:<port>/…`.

## Tools on PATH (no wrapper needed)

Where installed (`Get-Command <name>` tells): node, npm, npx, git, gh, docker, mysql/mysqldump (on a
machine with Herd its bundled MySQL client, in Git Bash through shims in `~/bin` if you made them; PATH
order in `rigger-windows:php-herd-tooling`), python (possibly the Microsoft Store build), pip, curl,
ssh (Windows OpenSSH), rg, jq. PHP tooling: see
`rigger-windows:php-herd-tooling`; Node versions: see `rigger-windows:node-versions`.

**jq** (winget `jqlang.jq`), where installed, is on PATH in both shells: before using it, read `references/jq.md`.

Also on PATH in both shells where installed (winget):
- **yq** (`MikeFarah.yq`): jq syntax for YAML (workflows, frontmatter, `pnpm-workspace.yaml`):
  `yq -r '[.jobs[].steps[] | select(.uses) | .uses] | unique | join(", ")' .github/workflows/ci.yml`.
- **actionlint** (`rhysd.actionlint`): run `actionlint -no-color` in a repo before committing a
  workflow change. It checks syntax, keys, `${{ }}` expressions and runs shellcheck on `run:`
  scripts, but checks an action's inputs only for versions in its own data: silence on a new major
  is not a pass.
- **shellcheck** (`koalaman.shellcheck`): `shellcheck -f gcc script.sh` for bash hooks and
  helper scripts; variables set through `eval` show up as SC2154 false positives.

Where Python is the Microsoft Store build (MSIX), its writes under `%LOCALAPPDATA%\<folder>` and
`%APPDATA%` land in `Packages\PythonSoftwareFoundation.Python.<version>_…\LocalCache\` instead, where
other programs do not see them. `AppData\Local\Temp` (the Claude scratchpad) and programs Python
starts are not redirected. Files Python shares with another program, and
headless browser profiles (`--user-data-dir`), go in `<scratch root>\<task>`; after a Python write that
another program reads, `Test-Path` it from PowerShell once.

## Coreutils in PowerShell

`Microsoft.Coreutils` (winget) puts `grep`, `head`, `tail` and other GNU-style tools in
`%ProgramFiles%\coreutils\bin` (on PATH; a new terminal/session is needed to see it). They work
in pipelines from PowerShell (`... | grep -n x`). The shell-guard hook still denies such a command (`ps-unix-command`); end the command with `# shell-guard: allow` when you mean it. **Not included: `sed`** — use
`-replace` / `rg -r` instead. **Aliases win:** `ls`, `cat`, `rm`, `cp`, `mv` still resolve to
PowerShell cmdlets (`Get-ChildItem`…); call `ls.exe` / `cat.exe` when you need GNU flags.
For searching code prefer `rg` (where installed) or the Grep tool.

## Missing tool or unknown version

Look in this order: the PATH list above, `rigger-windows:node-versions`,
`rigger-windows:php-herd-tooling`; then
`winget search <name>` / `winget show <id>` or the official source; only then a workaround.
Versions: `npm view <pkg> version`, `composer show -a <pkg>`, `pip index versions <pkg>`; library
docs: the Context7 MCP server, if you have installed it (`resolve-library-id` → `query-docs`); a version from memory is a labelled
fallback. Before installing, `Get-Command <name>` and
`where.exe <name>`: a "missing" tool is usually a `.bat` launcher Git Bash would not run by that
name; "not installed" is claimed only after the folders the tool itself searches (an SDK path, its own config) were checked.

## Moving a repo to another path

A move silently drops local-scope MCP servers and folder trust: before moving, read `references/moving-a-repo.md`.

## Machine load

- Light agents (reviews, Python or pwsh unit tests, docs) have no count limit.
- An agent that builds or tests starts only while (ceiling − committed) ≥ the minimum, min(30 GiB,
  25% of the ceiling), which mem-check prints. Before dispatching one, run
  `pwsh -NoProfile -File "<rigger-windows root>/tools/mem-check/mem-check.ps1"` and wait while it
  exits 1 (re-check every 2 min); after 30 minutes report it as a blocker. Exit 2 = no reading,
  report it. The ceiling is the largest SystemCommitLimit among the Event 2004 entries, the current CommitLimit if there are
  none: the system-managed pagefile grows, so the current limit under-states it.
- Heavy jobs (WSL, Docker, wp-env, e2e) run at most 2 at once, and e2e one at a time
  (`rigger-core:e2e-playwright-speed` §9, if installed). Keep node and Playwright processes at BelowNormal
  priority.
- After a crash or a hang, read the event log before guessing — free RAM afterwards says nothing,
  the limit is hit by commit-charge spikes. Event 2004 (Resource-Exhaustion-Detector) names the
  biggest processes:
  `Get-WinEvent -FilterHashtable @{LogName='System'; Id=2004} -MaxEvents 3 | Format-List TimeCreated, Message`. For the raw record pipe `Get-WinEvent … -MaxEvents 3` into ` | ForEach-Object { $_.ToXml() }` instead (the localised Message drops fields the raw XML carries; compare at a precision where twin fields differ).
- `--max-old-space-size` caps only the V8 heap; jsdom and native buffers grow outside it. Give test
  runners a per-file timeout (`node --test
  --test-timeout=<ms>`), give queue or timer loops in tests an iteration guard that throws, and
  while parallel agents run tests, watch for a runaway `node.exe` yourself (`Get-Process node`
  shows its working set) and stop it above a limit you choose.
- `node:test` prints a failed `assert.equal`/`deepEqual` diff with `util.inspect` at depth 1000,
  which for a DOM node inside a mounted React tree allocates outside the V8 heap. Compare nodes by
  identity (`assert.ok(a === b, 'why')`). A shared setup module imported in each test file can
  wrap the equality assertions and stop the process at 3 GB RSS.
