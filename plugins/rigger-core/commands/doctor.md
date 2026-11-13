---
description: Check this machine for everything the Rigger plugins need and offer one block of install commands for what is missing
---

Check, reading only, then report. Install or write nothing until the user has seen the commands and
said yes.

## 1. Checks

- Native Windows:
  `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/tools/doctor/doctor.ps1"`.
  When it prints a `BASH` line, also run that `bash.exe` with
  `"${CLAUDE_PLUGIN_ROOT}/tools/doctor/doctor.sh"` as its argument.
  Without a `BASH` line only the `hook-shell`, `git` and `python` rows appear; the others run once Git for
  Windows is installed.
- macOS and Linux: `sh "${CLAUDE_PLUGIN_ROOT}/tools/doctor/doctor.sh"`.

Each `CHECK` line is name, status (OK, MISSING, WARN, or INFO: information only, never with a FIX),
detail, how checked; each `FIX` line is the command for a row that is MISSING or WARN. Neither
script needs Python.

Check two more rows yourself:

- `hooks`: this session's context holds SessionStart hook text with `Plugin root:` lines from Rigger
  plugins: OK. None: MISSING, "hooks did not run in this session", and the rows above say why; but
  after a compaction or a resume that text may be gone, so then WARN and suggest a run in a new
  session.
- `worktrees`, desktop app only (`CLAUDE_CODE_ENTRYPOINT` is `claude-desktop`): the worktree location,
  from the app's settings tool (`worktree_location`) where it exists, otherwise "set in Settings →
  Claude Code". Information, not a finding.

## 2. Report

One table, a row per check: check, status, detail, how checked. Under it one code block with every
`FIX` line that is a command, in the order printed, and below it the other `FIX` lines (a link, an
instruction, a setting) as plain steps. Inside `/rigger-core:harness-setup`, leave out a FIX that
names harness-setup itself. Then ask once, with AskUserQuestion where it exists: run all of
them, pick which, or none. Nothing missing: say so and stop.

## 3. Fix, only on the user's yes

Run the picked commands one at a time and show each result. A `sudo` command waits for a password the
tool cannot type: give it to the user to run in their own terminal. `xcode-select --install` and
`winget` open a system dialog the user must accept. The `CLAUDE_CODE_GIT_BASH_PATH` line: ask for the
path to that Git's `bin\bash.exe`, run `doctor.ps1` again with the variable set to it, and only when
the `hook-shell` row turns OK write it under `env` in the user `settings.json`, keeping every other
key. Then run section 1 again. A tool installed in this session may not be on this session's PATH yet:
report its row as "installed, restart to confirm", not MISSING. Tell the user to restart Claude Code
if anything was installed: Claude Code looks for Git Bash and starts the hooks once per session.
