# Coordinate in the terminal CLI (`host cli`)

Read by `/rigger-core:coordinate` when `host` prints `cli`. The app's session tools do not exist here; each
step of the command that names one uses the matching item below instead. `<prefix>` is the coord_state.py
call prefix from step 1 of the command; `<config dir>` is `CLAUDE_CONFIG_DIR`, else `~/.claude`.

- **After compaction.** Write `host cli: read cli-host.md again before the next session step` into the state
  file's `## Keep verbatim` when you start the first part.
- **Mode.** Before each start run `<prefix> host` again: its last word is this session's permission mode (the
  owner may have switched it). A child does not inherit it, and messages between a bypassing and a prompting
  session are held, so every child gets that mode. Under `default` a child stops at each prompt and shows
  `blocked`; under `plan` it cannot write, so set `stop: app-refused` and ask the user to leave plan mode;
  `unknown`: read the mode off the terminal's mode line before passing one.
- **Start.** Create the part's worktree yourself: `git worktree add -b <branch> <worktree root>/<part> develop`.
  Save the brief in the scratch folder, under 20 000 characters (the Windows command line caps near 32 767;
  longer material goes in files the brief names). Then, with the Bash tool (PowerShell mangles the quoting),
  from that worktree: `claude --bg --name <plan slug>-<part> --model <model> --permission-mode <mode>
  "$(cat <brief file>)"`. A start counts only with exit 0 and its `backgrounded · <id> · <name>` line;
  anything else is a refusal: put its output line into the state file and stop that step
  (`--bg with bypassPermissions requires accepting the disclaimer first` needs the user to run
  `claude --dangerously-skip-permissions` once: set `stop: app-refused`). Then `move <part> started --session
  <id> --branch <branch>`, and under `## Side sessions kept` a line `<id>: <part>, <sessionId>, <worktree path>`
  (the `sessionId` from `claude agents --json --all`). A review or re-review session starts the same way
  but in the part's existing worktree, with no new worktree or branch.
- **Brief.** Instead of a scratch report path, the brief says: save the final report with the Write tool as
  `$CLAUDE_JOB_DIR/tmp/report.md` (no prompt there). You read it as `<config dir>/jobs/<id>/tmp/report.md`.
  A question to you comes as a report whose first line is `STATUS: BLOCKED`.
- **Wait.** Instead of the session watcher, run `<prefix> wait <id>[,<id>…]` in the background
  (`run_in_background`) with every part whose session works or is blocked; it costs no tokens and exits
  with one `coord-state: ok wait <id> <state>` line per part whose state changed from what its first poll
  saw and then stopped working, or `coord-state: ok wait cap` after 30 minutes. So a part already `done`
  whose report you have not read is read now, not waited on. `done`: copy its report to
  `<scratch>/<part>-report-<n>.md` (`n` counts that part's rounds; `claude rm` deletes the job folder),
  work from the copy and hand that path to the reviewer, then go on as step 4 says. `blocked`: it waits on
  a prompt; tell the user in one line to answer it with `claude attach <id>`, and keep it in `wait`.
  `failed`, `stopped` or `gone`: resume it (below) once, then treat a second one as a failed step. Start
  `wait` again while any part works or is blocked.
- **Follow-up, fix round, re-review.** `SendMessage` to `<plan slug>-<part>` with the text; when
  `claude agents --json --all` shows that session without a `pid`, `claude --bg --resume <sessionId>
  "<text>"` (it keeps its name, mode and model). Then `wait` again.
- **Close** instead of `archive_session`, on every end state (merged, dropped, given up): `claude stop <id>`,
  `claude rm <id>`, then `git worktree remove <worktree path>` (`rm` keeps a worktree it did not create;
  here you remove it, not the part's session) and delete its kept line; the branch stays. A review session
  in a builder's worktree gets `stop` and `rm` only. Sessions outlive
  this one: after compaction or a restart, compare the kept lines with `claude agents --json --all` and close
  any orphan, a session no part still needs.
- **Not here:** `list_sessions`, `list_events`, `quiet` lines, `wrap_up` buttons and the refused-start
  paragraphs, and no standby sessions: a terminal start is never refused for the turn that asks for it, so
  start each session when its stage is due (an idle standby would read as `done` in `wait`). Where a step says to give a
  session's link, give its name and `claude attach <id>`.
