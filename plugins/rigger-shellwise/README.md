# rigger-shellwise

A pinned [shellwise](https://github.com/Borcioo/shellwise) binary as the `shell-guard` filler on
Windows, macOS and Linux. It checks Bash and PowerShell commands before they run and denies the ones
its rules name.

- `pin.json` holds the pinned release: the version and, per target, the archive name and its
  SHA-256. The module downloads that release and trusts only these committed hashes.
- `tools/install/shellwise_install.py` downloads the pinned archive at session start in a detached
  process, checks its hash and the binary's version, and places it under the plugin data folder.
  Until then the session start prints one line and commands run unguarded.
- `shellwise.toml` is the integration config the guard runs with.
- `SHELLWISE=off` in the environment switches the guard off for a session.
