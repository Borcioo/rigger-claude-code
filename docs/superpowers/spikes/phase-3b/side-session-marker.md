# Spike: the side-session marker and the Stop event's background tasks

## Question

D89's turn-end guard holds a side session's turn while its own gate or test command runs in the
background. Two facts decide its shape on the current CLI:

1. Does the Stop stdin still carry `background_tasks` with the shape the earlier stub spike saw, and
   does `claude -p` still report `CLAUDE_CODE_ENTRYPOINT=sdk-cli`?
2. Is there an env variable or stdin key that a desktop side session's hooks see and a top-level
   session's hooks do not (the "side-session marker")? If none, D89 runs on its other filters alone.

## CLI

- `command -v claude`: `<home>/.local/bin/claude`, `claude --version`: `2.1.327 (Claude Code)`.
- The desktop app runs its own copy: `CLAUDE_CODE_EXECPATH` names
  `<appdata>/Claude/claude-code/2.1.330/<hash>/claude.exe`, `--version` `2.1.330 (Claude Code)`.
  Step 2 ran that binary (the CLI the desktop sessions use); the earlier stub spike covered 2.1.327.
- Each run's driver reads `--version` from the binary it starts; all runs below read `2.1.330`.

## Setup

- **Stub runs (Step 2):** the earlier spike's driver, stub and probe plugin, copied to
  `<temp>/rigger-p3b/task1/stub/`, with one change: the binary comes from `SPIKE_CLAUDE_BIN`
  (else `claude` on PATH). Isolation as before: a fresh config, plugin cache, lessons inbox, `HOME`
  and `TEMP` per run; the child drops `CLAUDE*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`, then
  sets `ANTHROPIC_BASE_URL` to the local stub; `RIGGER_LESSONS_BATCH` unset; `DISABLE_AUTOUPDATER=1`.
  No network, no real model.

| Run | Scripted model actions | Extra env |
|---|---|---|
| `e0-canary-330` | none | — |
| `e1-bg-330` | Bash `run_in_background` `sleep 20; echo done` | — |
| `e7-entry-desktop-330` | none | `CLAUDE_CODE_ENTRYPOINT=claude-desktop` |
| `e6-inherit-host-330` | none | `CLAUDE_CODE_HOST_SESSION_ID=local_spike-0000` |

- **Desktop probe (Step 3, ruling 1):** a scratch project `<temp>/rigger-p3b/task1/project/` with
  its own `.claude/settings.local.json` that runs `probe-hook.py` (outside the project) on
  SessionStart and Stop. The probe appends one line per firing to `probe-log.jsonl`: every env
  name, the values of `CLAUDE_CODE_HOST_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`,
  `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SESSION_ID` and a few non-secret others, the stdin keys,
  `session_id`, `stop_hook_active` and `background_tasks`. Known-answer check: a hand-fed stdin with
  `CLAUDE_CODE_HOST_SESSION_ID=local_known` logged exactly those values, exit 0.

## Control

- **Canary (`e0`):** SessionStart and one Stop fired; Stop `background_tasks: []`.
- **Positive (`e1`):** the same probe saw the running task (Observed).
- **Isolation:** a search of the real config's `projects/`, `backups/` and `.claude.json` for each
  run's session id hits only the requesting session's own transcript, which quotes the ids from
  tool output.

## Observed

### Step 2: the Stop stdin on 2.1.330

- First Stop of `e1`, task running:
  `"background_tasks": [{"id": "bp0w1jw7v", "type": "shell", "status": "running", "description": "x", "command": "sleep 20; echo done"}]`,
  `stop_hook_active: false`. The Stop of the notification turn: `[]`.
- Stop stdin keys, unchanged from 2.1.327: `background_tasks`, `cwd`, `effort`, `hook_event_name`,
  `last_assistant_message`, `permission_mode`, `prompt_id`, `session_crons`, `session_id`,
  `stop_hook_active`, `transcript_path`.
- `claude -p` waited for the task and exited 0 at 24.4 s (3 model requests) for a 20-s task.
- `CLAUDE_CODE_ENTRYPOINT`: `sdk-cli` in every hook of a plain `-p` run; an inherited
  `claude-desktop` passes through unchanged (`e7`). So the `sdk-cli` filter skips plain `claude -p`
  runs, not a `claude -p` started from a desktop session's tool without env filtering.
- `CLAUDE_CODE_CHILD_SESSION=1` and `CLAUDE_CODE_CHILD_NOTES_DIR` are set by the CLI for every hook,
  as on 2.1.327. `CLAUDE_CODE_HOST_SESSION_ID` is absent unless the parent passes it, and then
  passes through as given (`e6`).

### Step 3: what a desktop session carries

- **This side session's own tool env** (2.1.330, desktop entrypoint): the app's session record
  (`get_session self`) gives `sessionId: local_ce765845-…` and `parentSessionId: local_5025648a-…`.
  The env holds `CLAUDE_CODE_HOST_SESSION_ID=local_ce765845-…`: **the session's own app id, not its
  parent's.** No env value contains the parent's id. This also settles the earlier spike's open
  point (its `HOST_SESSION_ID` differed from the notes-folder id because the notes folder is named
  after the parent).
- So the value of `CLAUDE_CODE_HOST_SESSION_ID` cannot tell a side session from a top-level one;
  only its absence in a top-level session could.
- **Hook readings: not taken.** A side session cannot start a session in another folder (the app
  answers "it can only start sessions where this one runs, in this project"), so the probe needed
  a session opened by the coordinator. The coordinator dropped the probe: no env value carries the
  parent's id, so a top-level reading could at most show the variable absent there, which a
  `claude` started without the desktop (a terminal, `-p`) shows too and which a desktop child
  inherits. The scratch project, probe and its settings were deleted, and a listing shows them gone.

## Decision

**Marker: none.** No env variable or stdin key observed marks a side session:
`CLAUDE_CODE_CHILD_SESSION` and `CLAUDE_CODE_CHILD_NOTES_DIR` are set for every hook,
`CLAUDE_CODE_HOST_SESSION_ID` holds the session's own app id and is inherited by children, and the
Stop stdin keys carry no parent. D89 runs on its other filters alone:

- a `shell` entry of `background_tasks` with `status: running` whose command matches the gate and
  test list (the field's shape is unchanged on 2.1.330);
- never in a session whose state file says `coordinator: true`;
- never under `CLAUDE_CODE_ENTRYPOINT=sdk-cli` (plain `claude -p` waits for its tasks itself);
- at most 3 blocks per task id;
- `turn_guard.enabled: false` (levels 2–4) turns it off.

So a top-level session that runs its own gate in the background and ends its turn is held too.
Ruling 2 accepts that under the cap and the switch. Task 5's `test_top_level_session_is_not_held`
asserts this documented fallback: with no marker, a non-coordinator session passing the filters
is held.
