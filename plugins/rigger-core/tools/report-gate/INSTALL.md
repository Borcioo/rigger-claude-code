# report-gate — install / disable

Measures how often a subagent's final report opens with `STATUS: DONE`, `STATUS: PARTIAL`,
`STATUS: BLOCKED` or `STATUS: NEEDS_CONTEXT`, and how often an agent that fills a slot meets that
slot's contract. It only records; it never holds a subagent.

| Script | Hook | What it does |
|---|---|---|
| `report_shadow.py` | SubagentStop, no matcher, `async`, timeout 10 | Writes one row file per (session, agent) and prints nothing. |

Disable: remove the `SubagentStop` entry from the plugin's `hooks/hooks.json`.

## Rows

`$CLAUDE_PLUGIN_DATA/report-gate/rows/<key>.json`, one JSON line each; nothing is written while
`CLAUDE_PLUGIN_DATA` is unset, and at 20000 files no more are added. A report's text is never stored.

| Field | Value |
|---|---|
| `ts` | UTC time, `%Y-%m-%dT%H:%M:%SZ` |
| `sid8`, `agent8` | first 8 characters of the session and agent ids |
| `agent_type` | as given, at most 60 characters, `""` when absent |
| `verdict` | `ok`: the first non-empty line is `STATUS: <word>`; `bad-value`: it starts with `STATUS:` but holds another word or trailing text; `missing`: any other first line; `unread`: no report text was found; `error`: the row could not be judged |
| `status` | the word when `ok`, else `""` |
| `source` | `event` (`last_assistant_message`), `transcript` (last assistant text in the last 256 KB of `agent_transcript_path`), `none` |
| `has_summary` | `true` when `## Summary` with 3 to 5 bullets follows the STATUS line |
| `slot` | the slot whose agent target is `agent_type`, `""` when none is |
| `contract` | `ok` or `refused:<rule id>`: `slot_check.py --shape-only` on `last_assistant_message` (no git, so a reviewer's anchors and scope are not checked); `none`: the agent fills no slot; `unread`: the event holds no report text (the transcript is not read for this field); `error`: the check could not run |
| `ms` | milliseconds the run took |

The slot table is the session's snapshot (`<config dir>/rigger/state/<session id>.resolved.json`);
when it is missing, of another format or unreadable, the level-1 defaults in `rigger-defaults.json`.
Only filled slots of kind `agent` take part. If two slots name the same agent, the first in the
table counts.

## Summary

`sh "<plugin root>/bin/py.sh" "<plugin root>/tools/report-gate/report_shadow.py" --summary --data <plugin data dir>`
prints one line per `agent_type` (`-` when empty):
`<agent_type> ok N missing N bad-value N unread N error N summary N contract-ok N contract-refused N`
(a row written before a field existed counts 0 in it), then `# rows N` and, when any row file could not be read, `# unreadable N`. With no readable row it prints
`# rows 0` and exits 1.
