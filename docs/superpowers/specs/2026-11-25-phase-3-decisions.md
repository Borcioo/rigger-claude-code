# Phase 3 decisions: what changes against the modular architecture spec

This file amends §13 "Phase 3: split out the modules" of
`docs/superpowers/specs/2026-11-23-modular-architecture-design.md`. Where the two disagree, this file
wins. Everything §13 says that is not named here stands.

## Intent

Phase 3 splits review, testing and the shell guard out of rigger-core and rigger-windows into
modules a person can install, swap and test alone, and removes rigger-windows. Two goals beyond the
spec: nobody should have to install a shell guard by hand, and memory-aware agent scheduling must
work on every OS, not only Windows.

## Decisions

### D1. Two releases: 3a, then 3b

- **3a:** `rigger-review`, `rigger-testing`, the "read by" map that lets `--changed-modules` become
  the gate for a module-only change (§12), the second review round (D5), the hook latency
  measurement (D6) and the phase 2 backlog (D7). Nothing is removed, so 3a has no migration step
  beyond installing the two new plugins.
- **3b:** `rigger-shellwise`, `rigger-platform`, `mem-check` in core (D3), and the removal of
  rigger-windows with the migration of §13.
- **Why:** 3a adds modules; 3b changes what a person has installed. Each ships and can be rolled
  back on its own.

### D2. rigger-shellwise downloads a pinned shellwise binary

- **Approaches considered:**
  - (a) the person installs shellwise by hand (the spec's route);
  - (b) the plugin downloads a pinned release on first use;
  - (c) the binaries are committed to this repository.
- **Chosen: (b).** (a) leaves most people unguarded; (c) puts about 40 MB of binaries in every
  marketplace clone, for every user, and adds the same again to history with each shellwise update.
- **What the plugin pins:** one shellwise version and, per platform asset, its SHA-256. Assets come
  from the shellwise GitHub release of that version (`Borcioo/shellwise`, GPL-3.0, the same licence
  as Rigger). Platforms with an asset today: Windows x64, macOS arm64, macOS x64, Linux x64.
- **First session:** a SessionStart handler downloads the asset for this platform into the plugin's
  data folder (`CLAUDE_PLUGIN_DATA`), checks the SHA-256, unpacks it and records the version. A
  file whose hash differs is deleted and never run.
- **No binary yet:** offline, a failed download, a hash mismatch or a platform without an asset
  (Linux arm64, Windows arm64 today) leaves the slot unfilled. The session gets one line with the
  reason, doctor reports it, the hook allows and prints nothing, and the next session tries again.
  There is no Python fallback guard.
- **Override:** `shell_guard.shellwise_path` (personal config only, §7.4) still names a binary the
  person built or installed. Only that path needs spike S7, the version check; the downloaded
  binary's version is known from its pin.
- **Updating shellwise:** a Rigger release changes the pinned version and hashes. The SessionStart
  handler replaces the cached binary when the pin changes.
- **Migration:** the "install shellwise by hand" step of §13 goes. A hand-added shellwise hook in
  `settings.json` is still reported as a handler that runs twice.

### D3. mem-check moves to rigger-core and works on every OS

- **Where:** `rigger-core/tools/mem-check/`, in Python. The orchestrator (core) reads it before it
  starts a heavy agent, so it belongs with the orchestrator, not with a platform add-on.
- **What it measures:**
  - Windows: commit charge headroom (limit minus committed), as today;
  - Linux: `MemAvailable`, and commit headroom from `CommitLimit` and `Committed_AS`;
  - macOS: free, inactive and purgeable pages from `vm_stat`, with the page size from `sysctl`.
- **Config keys** (all four levels, schema 1):
  - `load.min_free_gb`: the headroom below which no new heavy agent starts; a default per OS;
  - `load.max_heavy_agents`: heavy agents at once, default 2.
- **Unknown:** when the numbers cannot be read, the tool says so and the orchestrator runs one
  heavy agent at a time.

### D4. safe-edit is ported to Python

`safe-edit` moves to `rigger-testing/tools/safe-edit/` as Python, so the red-run rule works the same
on all three OSes without PowerShell. The pwsh script goes; its behaviour (break one file, run the
command, always restore, check the restored hash) and its tests carry over.

### D5. A second review round for full-risk changes

- A change the plan marks full risk (trust, command execution, the leak guard, data) gets a second,
  fresh reviewer after the first review's fix round. It receives the first review's findings and
  the fix diff, and looks for what the first did not find.
- Other changes keep one review.
- This changes the one-review rule in `orchestrating-subagents` and the coordinate command.

### D6. Measure every hook before choosing a language

- 3a measures each Rigger hook's added latency on Windows, macOS and Linux: median and p95, on a
  call that matches and one that does not, as `rigger-lessons/rules/lessons.md` asks of any process
  guard.
- A hook on the per-command path with a median over 50 ms becomes a candidate for a Rust port in
  phase 4. Nothing is rewritten in Rust in phase 3.

### D7. Phase 2 backlog folded into 3a

- `resolve_program` compares by file identity on a case-insensitive POSIX file system, as
  `review_scope.searchable` already does.
- The file-identity check gets a proof on Windows and on macOS CI, not only WSL drvfs.
- `lessons_batch.py` reads the rendered rules text, as the interactive lessons tools do since
  Task 20.
- The Windows test leg's per-file timeout pressure (`test_assemble.py` 54 s against 120 s) is
  measured and the slowest files split or sped up.

### D8. Where rigger-windows' items land (changes to the §13 table only)

| Item | §13 said | Now |
|---|---|---|
| `tools/mem-check/` | rigger-platform | **rigger-core**, Python, every OS (D3) |
| `tools/safe-edit/safe-edit.ps1` | rigger-testing, port is the builder's call | **rigger-testing, ported to Python** (D4) |

All other rows of the §13 table stand.

## Non-goals

- No change to shellwise itself for Rigger. Adding Linux arm64 and Windows arm64 assets to the
  shellwise release is the shellwise owner's call, outside this plan.
- No Rust port in phase 3 (D6).

## Estimate

The spec's 28 h for phase 3 grows by about 14 h: the download and pin handler (3 h), cross-platform
mem-check (3 h), the safe-edit port (3 h), the hook measurement (2 h), the second review round
wording (1 h) and the backlog (2 h). About 42 h over two releases, roughly 2,400 lines of code and
2,600 of tests.

**Simpler version:** 3b keeps rigger-windows as the shell-guard filler for one more release and
ships only rigger-platform and mem-check; rigger-shellwise moves to a later release. It drops the
cross-platform guard for that release.
