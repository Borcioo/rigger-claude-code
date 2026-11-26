# rigger-windows

For Claude Code on native Windows. It ships:

- **`safe-edit.ps1`**: a "red run" tool. A red run breaks one file on purpose, runs the tests to prove they fail, and always puts the file back.
- **`mem-check`**: a memory-headroom gate for heavy jobs; it prints the commit charge against a ceiling and exits 1 when the headroom is too low. Read its `INSTALL.md` for usage and exit codes.

Install:

```
claude plugin install rigger-windows@rigger
```

The Windows shell rule and the `windows-dev-env`, `php-herd-tooling` and `node-versions` skills are in rigger-platform; the shell guard is rigger-shellwise; the test-evidence gates and gate lab are in rigger-testing.
