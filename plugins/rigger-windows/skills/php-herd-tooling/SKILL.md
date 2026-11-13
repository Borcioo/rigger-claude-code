---
name: php-herd-tooling
description: >-
  Load BEFORE running php, composer, wp (WP-CLI), phpcs/phpcbf or phpstan on Windows (Laravel
  Herd), any `herd` command or a Herd update, or when a project needs a PHP version other than the
  default (php74…php86); and on `php83: command not found`, a PharException, or sites returning
  500 after "Starting MariaDB".
---

# PHP tooling (Herd on Windows)

Prefer PowerShell. Git Bash does not start a `.bat` by a name without its extension, so a Bash
script calls the launcher by full path, e.g. `"$HOME/.config/herd/bin/php83.bat"`.

| Command | Location |
|---|---|
| `php`, `composer`, `herd`, `php74` … `php86` | `%USERPROFILE%\.config\herd\bin\*.bat` |
| `wp` | `<WP-CLI folder>\wp.bat` → `<WP-CLI folder>\wp-cli.phar` (find it with `where.exe wp`) |
| `phpcs`, `phpcbf`, `tailpress`, `laravel` | `%APPDATA%\Composer\vendor\bin\*.bat` |
| `phpstan` | project-local: `vendor\bin\phpstan` from the project root |

phpcs: always `--cache=<scratch root>\phpcs-cache-<project>` (`<scratch root>` is defined in
`rigger-windows:windows-dev-env`), a folder outside the repo that keeps the working tree clean; a
project may instead set `<arg name="cache" value=".phpcs-cache"/>` and gitignore it. Timings and tool choice, if installed: `rigger-core:stack-tooling`.

## Picking a PHP version

- `php`, `composer` and `wp` on the command line always run Herd's global default PHP: `php.bat`
  is hard-wired to one `php8N\php.exe` (`php -v` shows which), `wp.bat` calls `php.bat`,
  `composer.bat` calls bare `php`. A site's isolated PHP version applies to web requests only. To match it on the CLI use `herd php` /
  `herd composer` or a `php8N` launcher; read the site's version with `herd which-php` or
  `herd site:info`.
- Sites are served at `<site>.test`; `<site>.localhost` also resolves.
- `herd init` + `herd.yml` pins PHP version and services per project.
- Composer on a chosen version: `php83 "$env:USERPROFILE\.config\herd\bin\composer.phar" <args>`
- WP-CLI on a chosen version: `php83 <WP-CLI folder>\wp-cli.phar <args>`
- Pin `composer config platform.php <lowest supported version>` in projects that support older
  PHP (every composer.json in the repo, tools and fixtures included), so dev tools resolve to
  versions that actually run there.

## WP-CLI gotchas

- A `wp-cli.phar` of a few KB in the Herd bin folder is a stub (`PharException: truncated manifest
  header`): use the full phar that `wp.bat` (found by `where.exe wp`) runs.
- WP-CLI on php85 prints `react/promise` deprecations into the output; run scripted `wp` on php83
  or php84 (`php84 <WP-CLI folder>/wp-cli.phar <args>`).
- **`wp eval` from PowerShell breaks on a double quote in the PHP.** PowerShell passes `"` as `\"`,
  cmd.exe splits the argument there (`Too many positional arguments: …`) or strips the quotes
  (`Undefined constant "x"`). Use single-quoted PHP
  strings, or put the code in a scratchpad `.php` and run `wp eval-file` (`--use-include` when it
  needs `declare` or `global`).
- For WP-CLI usage itself (search-replace, db, multisite), if the WordPress agent skills are
  installed: `wp-wpcli-and-ops`.

## Herd traps

- **PATH order for database tools:** if a separately installed MariaDB or MySQL `bin` folder comes
  before `%USERPROFILE%\.config\herd\bin` (Machine PATH entries come before User PATH ones),
  `mysqldump`/`mysql` resolve to that client instead of Herd's `.bat` wrappers. Symptoms: sites
  answering 500 with "Starting MariaDB … `ib_logfile0 not found`" in the MySQL service log, or
  `wp db export` writing a near-empty dump without an error (a MariaDB client cannot authenticate
  `caching_sha2_password`). Check with `(Get-Command mysqldump).Source` (PowerShell) or
  `type mysqldump` (Git Bash). If it is not Herd's: stop and give the user the exact PATH change
  (a Machine PATH change needs an administrator), then Herd and the Claude session need a
  restart; never prepend PATH per call. For the task in hand, one call by full path to
  `herd\bin\services\mysql\<ver>\bin\mysqldump.exe` is fine. Git Bash ignores `.bat` on PATH:
  it needs its own `mysql`/`mysqldump` shims.
- **Database services:** two database services on the same port (Herd's MySQL and another MariaDB
  or MySQL service) share it: start one at a time; `herd status` shows which is active.
- **Junction:** after re-pointing a site's plugin/theme junction, `herd restart` (php-cgi caches
  the old realpath; asset URLs become `/wp-content/plugins/<drive>:/…`).
- **Herd Studio** ("Enable Herd Developer Tools") injects an "Edit with Herd" badge into every page
  of every .test site: keep it OFF for screenshots, overlays and axe scans.
- **Updates:** never through Herd's in-app updater. Herd is installed per-machine, but its updater
  runs the setup without elevation; the update fails half-way and Herd then starts from
  `%TEMP%\ns*.tmp\old-install`: the processes run but no window opens, and `main.log` stops at
  "Copying binaries" with `ENOENT … old-install … openssl.cnf`, no service started. Update by closing the Claude app (if its MCP servers run on Herd's Node, the installer cannot
  close Herd) and Herd, and running the setup as administrator.
- **Symptoms of a half-done update:** PHP prints a startup warning on every call about a missing
  `…\Temp\ns*.tmp\old-install\…\php_herd-X.Y.dll` (the update appended a dead `extension=` line);
  Herd starts from temp. Expected state: one `extension = '…\phpherd\php_herd-X.Y.dll'` line under
  `%ProgramFiles%\Herd\resources\app.asar.unpacked\resources\bin\phpherd\` in each installed
  `phpNN` `php.ini` that Herd ships a DLL for, and no line pointing into `Temp\ns`.
- **After any update or a Herd that will not start:** check `php -v` and `herd status`, remove dead
  `extension=` lines from `php.ini` by hand, and delete `old-install` folders left in `%TEMP%`
  (a folder created by an elevated installer needs an administrator shell).

## Herd CLI for agents (Herd Pro)

**CLI first.** Most commands take `--json` (`herd list --json` does not); `herd help <cmd>` shows
the flags. `herd sites --json` prints each site's full `.env`, DB and SMTP passwords included:
never paste that output into a reply, PR, log or file.

| Command | Use | Caveat |
|---|---|---|
| `herd status` | health, active services | Herd app must run |
| `herd db:connection`, `db:list`, `db:use <db> --create` | database of a site | creates databases: ask first |
| `herd mail:wait --to=… --subject=…`, then `herd mail:links <id>` | assert real outgoing mail in tests | only mail sent after the command started counts |
| `herd php`, `herd composer` | run with the site's PHP version | |
| `herd insights:*`, `herd site:services` | dumps, queries, N+1; per-site DB/queue/cache | **Laravel-only** (`dump()`, `.env` keys) |
| `herd workspace <name> --database=shared\|clone\|empty [--local]` | parallel agents on one git site: full copy on its own branch at `<name>.<site>.test`, rewrites APP_URL/DB_DATABASE | creates sites and databases: ask first; `workspace:merge --delete`, `workspace:delete`; config in `herd.yml` (`workspaces:`) |

- Workspaces inside an existing `git worktree` serve it. For WordPress the cloned DB still holds
  the parent's `siteurl`/`home` → `wp search-replace` or WP_HOME/WP_SITEURL per copy.
- **Herd MCP:** `herd mcp` (stdio); `herd mcp:install --project --agent claude` edits `.mcp.json`.
  Its tools (site, env, db, mail, insights, logs, processes, services, workspaces, runtime) are
  mostly Laravel-shaped; on other stacks, WordPress included, the CLI covers the same. Ask before
  installing.

## Herd docs (offline)

The Herd doc pages are not shipped with this plugin (third-party docs). If
`${CLAUDE_CONFIG_DIR:-~/.claude}/rigger-windows/herd-docs/` or
`${CLAUDE_SKILL_DIR}/references/herd-docs/` exists (a local copy of the 60 Windows doc pages, with an
`INDEX.md` that lists path and "use when" per page): for any Herd question this body does not
answer, `rg -n "<term>" <that folder>`, then read only the page needed.
Otherwise read Herd's online documentation.
