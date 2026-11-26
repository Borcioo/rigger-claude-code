# rigger-windows

For Claude Code on native Windows. It ships:

- **`safe-edit.ps1`**: a "red run" tool. A red run breaks one file on purpose, runs the tests to prove they fail, and always puts the file back.
- **`drift-lint`**: finds rule and skill files that point at things that no longer exist.
- **`mem-check`**: a memory-headroom gate for heavy jobs; it prints the commit charge against a ceiling and exits 1 when the headroom is too low. Read its `INSTALL.md` for usage and exit codes.
- the `windows-dev-env`, `php-herd-tooling` and `node-versions` skills.

Install:

```
claude plugin install rigger-windows@rigger
```

## Rules

rigger-core prints this plugin's rules on startup, clear and compact (`rules/windows.md`, which `rigger-module.json` lists): the meaning of `<rigger-windows root>` (the folder this plugin is installed in) and the default shell (PowerShell). Its three skill rows (`triggers` in `rigger-module.json`) appear in rigger-core's one Skill triggers table. Subagents get the same, except the built-in Explore and Plan agents and rigger-core's implementer and mapper, which get nothing.

The shell guard is rigger-shellwise; the test-evidence gates and gate lab are in rigger-testing.
