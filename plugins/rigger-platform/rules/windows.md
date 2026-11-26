## Windows shell

- On native Windows (not WSL), **the PowerShell tool is the default shell** (missing from your tools:
  `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` in `env` adds it; until then use Bash); the Bash tool only for
  a POSIX script or pipeline that already exists or has no PowerShell equivalent.
- A script over ~10 lines, or one with nested quotes, goes through the Write tool into the scratch
  folder and runs by path, both calls in one message; a heredoc saves nothing and breaks on quoting.
