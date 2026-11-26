# rigger-shellwise

A pinned [shellwise](https://github.com/Borcioo/shellwise) binary as the `shell-guard` filler on
Windows, macOS and Linux. It checks Bash and PowerShell commands before they run and denies the ones
its rules name.

Install: `claude plugin install rigger-shellwise@rigger` (with the `rigger` marketplace added, as
rigger-core's README says). Nothing else to install: the binary comes with the first session. The module
fills rigger-core's `shell-guard` slot and fails closed (`failure_mode: closed`).

- `pin.json` holds the pinned release: the version and, per target, the archive name and its
  SHA-256. The module downloads that release and trusts only these committed hashes.
- `tools/install/shellwise_install.py` downloads the pinned archive at session start in a detached
  process, checks its hash and the binary's version, and places it under the plugin data folder.
  Until then the session start prints one line and commands run unguarded.
- `bin/shellwise.sh` is the PreToolUse launcher (Bash and PowerShell): it runs the binary named in
  `shellwise/current` as `hook claude-code --config shellwise.toml` and passes its reply through. With no
  binary yet it prints nothing and the command runs.
- `shellwise.toml` is the integration config the guard runs with: the release's example, with
  `gate_commands = ["safe-edit*", "safe_edit*"]`.
- `tools/rule-checker/rule_checker.py` answers `live:<rule>` lessons for rigger-lessons' lint through the
  binary's `check`.
- `SHELLWISE=off` in the environment switches the guard off for a session. A command that holds
  `# shellwise: allow` carries the marker that a danger finding's fix names. `shell_guard.shellwise_path` in your personal config
  replaces the downloaded binary (rigger-core's README).
- A gate started through a script host other than pwsh (`python -I scripts/gate.py | head`) is not matched by
  `gate-truncated-pipeline`.
- Bumping the pin: "Updating shellwise" in `CONTRIBUTING.md`.
