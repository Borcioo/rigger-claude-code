# context-guard and compact-guard — install / disable

Long main sessions keep working through automatic compaction. The session keeps a state file; compaction waits until the file says it is safe, and the file is put back into
the context afterwards.

| Script | Hook | What it does |
|---|---|---|
| `context_guard.py` | UserPromptSubmit | From 200k prompt tokens, adds a reminder naming this session's state file: rewrite it at the next task boundary with `safe_to_compact: true` and keep working. Prints nothing below the threshold or on any error. |
| `compact_guard.py pre` | PreCompact, matcher `auto` | Blocks an automatic compaction while the state file says `safe_to_compact: false`; a state file with `coordinator: true` follows `safe_to_compact` too but fails closed until the coordinator cap. Logs every decision. |
| `compact_guard.py post` | PostCompact | Saves the compaction summary to `<sid>.summary.md`, then resets the state file to `safe_to_compact: false` (body untouched). |
| `compact_guard.py start` | SessionStart, matcher `compact` | Adds the state file to the post-compaction context: `Next step` section first, `Keep verbatim` second, both as written, then the rest; at most 9 500 characters (the hook cap is 10 000), cut at the end (the rest goes first) with `[truncated: read <path>]`; a cut is announced in the first line with the file's size. |

Both read the transcript from the end (at most 16 MB) and take the last main-chain assistant
request; `<synthetic>` and API-error lines are skipped, and a compaction boundary after it counts as
0 tokens.

## `pre` decisions, in order

| # | Condition | Decision (log reason) |
|---|---|---|
| 1 | trigger is not `auto` | allow (`manual`) |
| 2 | `agent_id` in the event (a subagent) | allow (`agent`) |
| 3 | no readable transcript or no assistant request in it | allow (`no-transcript`) |
| 4 | prompt tokens < floor (`--floor`, default 200 000): 200k-window models, the 0 after a boundary | allow (`below-floor`) |
| 5 | valid session id (`^[A-Za-z0-9_-]{1,128}$`) and the state file's frontmatter says `coordinator: true`: prompt tokens >= coordinator cap (`--coordinator-cap`, default 950 000) | allow (`coordinator-cap`) |
| 5a | the same, below the cap: `safe_to_compact: true` and the state file not written more than 30 min before the last request | allow (`coordinator-safe`) |
| 5b | the same: `true` but the file is older than that, or the last request has no usable timestamp / `false` / key missing or any other value | **block** (`coordinator-stale` / `coordinator` / `coordinator-bad-state`); rows 6 to 9 do not apply: no hard cap, no idle rule |
| 6 | prompt tokens >= hard cap (`--hard-cap`, script default 450 000, the plugin passes 560 000) | allow (`hard-cap`) |
| 7 | last request ended the turn (`stop_reason: end_turn`, no `tool_use` block) more than 45 min ago: idle compaction | allow (`idle`) |
| 8 | session id not `^[A-Za-z0-9_-]{1,128}$` | allow (`bad-sid`) |
| 9 | state file missing, unreadable, not UTF-8, no frontmatter or no key / `true` / `false` / other value | allow (`no-state`) / allow (`safe`) / **block** (`unsafe`) / allow (`bad-value`) |

Any error allows (fail open): a compaction gate must never wedge the session. A blocked compaction
is skipped, the request goes out uncompacted, and PreCompact runs again before the next request.
The block reason never reaches the model; the reminder and the `state-file.md` rule are what make it write `true`,
and the hard cap is the backstop for a long turn that never does. A coordinator has no idle rule:
it compacts at its pauses because it writes `true` before each waiting turn end, and only its cap
ends a block it caused by leaving `false`.

`coordinator` counts only as `true` (same parsing as `safe_to_compact`); a missing file, a missing
or malformed frontmatter, a missing key or any other value takes rows 6 to 9 unchanged (the guard
cannot tell such a session from any other). Once `coordinator: true` is read, every doubt about
`safe_to_compact` blocks.

Caps:

- A cap at or below the fire point (window − 33k, see Install) makes `pre` always allow, because it
  compares with the last finished request, which is already past the fire point. With
  `autoCompactWindow` 533000 (fires at about 500k) the plugin's `--hard-cap 560000` keeps
  `safe_to_compact` in force between 500k and 560k.
- Keep each cap at least ~30k below the window of the model the session runs: a request can grow
  past the last measured size before the next check, and a compaction that then runs into a
  context-limit error would be blocked by PreCompact too (that request then fails). The limit is the
  model's window, not `autoCompactWindow`. The 650 000 coordinator cap is safe only
  on a 1M window; a coordinator on any other model or a 200k / 283k window needs a lower
  `--coordinator-cap`. It gives a coordinator 150k above the fire point to finish a step it
  marked `false`; a higher cap lets a forgotten `false` keep a huge context.

## State file contract

`<config dir>/state/<session id>.md` (`<config dir>` = the `CLAUDE_CONFIG_DIR` environment variable,
else `~/.claude`; `<session id>` = `CLAUDE_CODE_SESSION_ID` in the session's shell tools, the same
value as the hook `session_id`):

```
---
safe_to_compact: true
updated: 2026-11-03T12:00:00+00:00
---
## Next step
…
## Keep verbatim
…
## Goal
…
```

- `safe_to_compact`: `true` / `false`, case-insensitive, quotes allowed; UTF-8 (BOM and CRLF are
  fine). Other frontmatter keys are kept by `post`.
- `coordinator: true` (optional, same parsing): only the session the user drives as orchestrator
  sets it, never a side session. `post` keeps it.
- `## Next step`: what to do right after the compaction. Injected first.
- `## Keep verbatim`: what the summary must not paraphrase: exact error text, file:line, commands,
  ids, decisions with their reason. Injected second. Automatic compaction gets no summarizer
  instructions, so anything that must survive word for word goes here.
- Both: any heading level (`Next steps` too); a section ends at the next heading of the same or a
  higher level; `#` lines inside ``` or ~~~ fences are not headings; injected as written; the cap cuts the
  rest of the body first, and these two only when they alone exceed it.
- Main session only: a subagent's events (`agent_id`) are ignored by all three subcommands.
- `--fork-session` / `/fork` / `/branch` give the new session a new id: its state does not carry over.

## Files

All under the state dir (`<config dir>/state`, created when missing; `--state-dir <abs>` for tests),
written UTF-8, rewrites atomic (temp file in the same folder, then replace):

| File | Written by | Content |
|---|---|---|
| `<sid>.md` | the session; `post` resets its frontmatter | state |
| `<sid>.log.md` | the session | history it moves out of the state file; no hook reads it |
| `<sid>.summary.md` | `post` (overwrite) | first line `<ISO time> <trigger>`, then the compaction summary |
| `compact-guard.log` | `pre` (append) | `<ISO time> <sid or -> <tokens or -> allow|block <reason>`; over 1 MB it is cut to its last half before the next line |

A failed log write never changes a decision.

## Install

`~/.claude/settings.json`, top level:

```json
"autoCompactWindow": 533000
```

That is the default `/rigger-core:harness-setup` recommends (fires at about 500k, for a model with
a 1M context); `283000` (about 250k) suits shorter sessions and cheaper turns. Stay below 560000.

Compaction fires when the next request's estimated prompt reaches about window − 33k (measured on
haiku-5-5 at window 150k: fire at ≈ 117–118k), so 283 000 fires at about 250k. Re-check the fire
point on the real model with the `preTokens` of the `compact_boundary` lines after an allow in the
log. Precedence, highest first: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` > `--autocompact` > the
per-model `modelSettings` written by `/autocompact` > the top-level key. The window probably applies
to subagents too (unverified).

`/rigger-core:harness-setup` adds this key if you opt in.

The rigger-core plugin installs the hooks in its `hooks/hooks.json`; there is nothing to add to
settings.json. A copy of these handlers left in settings.json runs each hook twice, and
`harness_start` names it at session start.

| Event | Matcher | Script | Timeout |
|---|---|---|---|
| `UserPromptSubmit` | none | `context_guard.py` | 5 |
| `PreCompact` | `auto` | `compact_guard.py pre` | 5 |
| `PostCompact` | none, so it runs after `auto` and `manual` | `compact_guard.py post` | 5 |
| `SessionStart` | `compact` | `compact_guard.py start` | 5 |

Each handler runs `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "<script>" <args> || true`. `bin/py.sh`
runs the first Python 3.9+ it finds with `-I` and exits 0 when there is none, so the guards fail
open. Keep the `|| true`: Python exits 2 on a missing script, and exit 2 blocks the prompt or the
compaction. A block is JSON on stdout with exit 0, so it passes through.

The PreCompact command passes `pre --hard-cap 560000 --coordinator-cap 650000`; the other plugin
hooks pass no limits, so the defaults apply. `context_guard.py --threshold <tokens>` and
`compact_guard.py pre --floor <tokens>` take effect only when you run a script by hand, as the
tests do; to change a cap, edit that command in `hooks/hooks.json`.

## Cost

Median ms of 20 calls per event; "worst" puts the last request at the start of a 14 MB transcript,
so all of it is read. A call without a `py.sh` cache entry probes Python first (Windows: 9
processes, about 330 ms; Linux: 6 processes).

| Command | Git Bash: match / no match / worst | WSL dash: match / no match / worst | Processes (Git Bash / WSL) |
|---|---|---|---|
| `context_guard.py` (each prompt) | 146 / 149 / 161 | 14 / 13 / 33 | 3 / 2 |
| `compact_guard.py pre` (each request while compaction is blocked) | 155 / 161 / 173 | 15 / 15 / 36 | 3 / 2 |
| `compact_guard.py post` (each compaction) | 155 / 154 / – | 13 / 14 / – | 3 / 2 |
| `compact_guard.py start` (each compaction) | 152 / 149 / – | 14 / 14 / – | 3 / 2 |

Method: `sh -c '<hook command above>'` with the `py.sh` cache warm (Windows: Git's `bin\sh.exe`, processes counted by a Job object; WSL: `/bin/sh` on a Linux-native copy, processes by `strace -f`); match = 260k tokens, state `false` or the event the hook acts on; no match = 50k tokens or a subagent event.

The reminder is about 300 characters per prompt while over 200k; the post-compaction injection is
the state file, at most 9 500 characters.

## Disable

- The hooks have no separate switches: disable the rigger-core plugin to stop all four, and remove
  `autoCompactWindow` to go back to the default window.
- State files and the log stay in `<config dir>/state`; delete them by hand if wanted.

## Test

```
python -I tools/context-guard/tests/test_context_guard.py
python -I tools/context-guard/tests/test_compact_guard.py
```
