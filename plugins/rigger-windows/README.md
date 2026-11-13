# rigger-windows

For Claude Code on native Windows. It ships:

- **shell-guard**: hook rules that deny commands certain to fail in PowerShell or Git Bash, with the fix in the message.
- **`safe-edit.ps1`**: a "red run" tool. A red run breaks one file on purpose, runs the tests to prove they fail, and always puts the file back.
- **`drift-lint`**: finds rule and skill files that point at things that no longer exist.
- **`mem-check`**: a memory-headroom gate for heavy jobs; it prints the commit charge against a ceiling and exits 1 when the headroom is too low. Read its `INSTALL.md` for usage and exit codes.
- the `windows-dev-env`, `php-herd-tooling` and `node-versions` skills.
- optional tooling in `tools/gate-lab/` for measuring the two gates described below. Its shell-guard folder also holds the rule checker the rigger-lessons lint uses for shell hook guards.

Install:

```
claude plugin install rigger-windows@rigger
```

## Rules

The plugin injects rules on startup, clear and compact: the meaning of `<rigger-windows root>` (the folder this plugin is installed in), the default shell (PowerShell) and the skill triggers. Subagents get the same two files through `SubagentStart`, except the built-in Explore and Plan agents and rigger-core's implementer and mapper, which get nothing.

## Hooks that read your session

- `PreToolUse` on PowerShell and Bash runs shell-guard on the command text (the Bash hooks fire only for git, `tee`, `safe-edit`, heredocs and doubled backslashes).
- An asynchronous `Stop` hook (`stop_shadow.py`) is **shadow only**: when a turn ends with a "done" claim and no check ran after the last edit, it writes one row under the plugin's data folder. It prints nothing and never blocks a turn. It reads that session's transcript, and only acts in a repo that has a `.claude/gates.json` with `scope_stop`; see `tools/shell-guard/INSTALL.md`.
- Commit rows (`commit-gate/rows/`) are written the same way for the commit gate, when `.claude/gates.json` turns it on.

Remove the `Stop` entry from `hooks/hooks.json` (or disable the plugin) to stop all of it.

## Gate lab (optional)

`tools/gate-lab/` replays the two gates offline over your own transcripts (`replay.py`) and joins the rows to labels you write (`report.py`: one JSON line per label, `{"id", "gate", "real", "source": "user|model", "note"}`). It exists to measure whether a gate is worth keeping; nothing in the plugin needs it. `trials.template.json` is a template for the trial table you keep: the first `report.py` run copies it to `trials.json` in the data folder, and the trial dates are yours. See the docstrings of both scripts for the exact flags.
