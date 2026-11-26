# rigger-platform

The platform module of Rigger: the Windows shell rule and the `windows-dev-env`, `php-herd-tooling` and
`node-versions` skills. It depends on `rigger-core`.

Install: `claude plugin install rigger-platform@rigger` (with the `rigger` marketplace added, as
rigger-core's README says). It was part of rigger-windows; coming from there, install this plugin and
run `claude plugin update rigger-core@rigger`, then `/rigger-core:doctor`.

## Rules

rigger-core prints `rules/windows.md` on startup, clear and compact, on Windows only (the entry in
`rigger-module.json` has `requires: os:windows`): the default shell (PowerShell). The three skill
rows (`triggers` in `rigger-module.json`, each with `requires: os:windows`, ids `rigger-platform.<skill>`)
appear in rigger-core's one Skill triggers table. Subagents get the same, except the built-in Explore
and Plan agents and rigger-core's implementer and mapper, which get nothing.

## Skills

- `rigger-platform:windows-dev-env`: shells, PowerShell and Git Bash traps, text encoding, line
  endings, links and junctions, machine load.
- `rigger-platform:php-herd-tooling`: PHP, Composer and WP-CLI through Herd, PHP versions.
- `rigger-platform:node-versions`: nvm-windows through Herd.

The Herd doc pages are read from `<config dir>/rigger-platform/herd-docs/`. The folder of the same
name under `rigger-windows/` is still read for one release when the new one is absent: move it.

## Evals

Five cases in `evals/` (`evals/CASES.md` at the repository root lists them under `rigger-platform`).
