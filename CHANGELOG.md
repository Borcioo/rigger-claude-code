# Changelog

All notable changes to this marketplace are listed here, newest first, in the Keep a Changelog
format. Releases are tagged `v<version>`.

## [Unreleased]

## [0.1.0] - 2026-11-13

First public release.

### Added

- Seven plugins: `rigger-core`, `rigger-rules`, `rigger-lessons`, `rigger-talk`, `rigger-windows`,
  `rigger-wp` and `rigger-panel`.
- `rigger-rules` loads your own rules from `<config dir>/rigger/rules.d/*.md` at session start and
  into subagents (`*-main.md` files into the main session only).
- `rigger-lessons` writes global lessons into `rules.d/lessons.md`; its background curation
  batches are off until you set `RIGGER_LESSONS_BATCH=1`.
- `rigger-panel` lists the GitHub issues assigned to you, read-only, through `gh`.
- `scripts/gate.py` needs no input: without a denylist, `claude` or `wsl` it says so or prints a
  `SKIP` line, and `--release` proves your denylist catches a planted term.
