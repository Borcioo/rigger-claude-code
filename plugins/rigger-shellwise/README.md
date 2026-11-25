# rigger-shellwise

A pinned [shellwise](https://github.com/Borcioo/shellwise) binary as the `shell-guard` filler on
Windows, macOS and Linux. It checks Bash and PowerShell commands before they run and denies the ones
its rules name.

- `pin.json` holds the pinned release: the version and, per target, the archive name and its
  SHA-256. The module downloads that release and trusts only these committed hashes. The installer
  arrives with a later change.
- `shellwise.toml` is the integration config the guard runs with.
- `SHELLWISE=off` in the environment switches the guard off for a session.
