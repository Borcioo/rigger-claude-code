---
description: Run the approved plan as its coordinator, with each independent part in a separate session (needs the Claude desktop app's session tools and the superpowers plugin)
argument-hint: "[plan file]"
---

Plan: $ARGUMENTS

Needs: the Claude desktop app (Code tab), whose session tools (`start_session`, `list_sessions`,
`list_events`, `wrap_up`, `dismiss_task`) start and watch the side sessions, and the superpowers
plugin for the design and planning skills named below. Without the session tools, see the last
paragraph.

Read that file whole. With no file named, use the plan approved in this session or the newest plan
this session wrote; with neither, ask the user for its path and stop.

Do not implement the plan yourself: lead it. Load `rigger-core:orchestrating-subagents` first.

Words used below: the **integration branch** is the branch the parts' branches merge into before
the default branch; a **merge point** is each merge into it; a **gate** is the project's full check
command (tests, lint and any other check it requires); a **gate lock** is a lock file or script a project may use so only
one full gate runs on the machine at a time.

1. Add `coordinator: true` to this session's state file frontmatter (`state-file.md` rule), and
   refresh that file each time a part starts or finishes: running sessions, heads, Next step.
2. Split the approved plan by real inputs: parts that need nothing from each other start now, each
   in its own session; a part that waits names the exact input it waits for. List every stage up
   front as a step in its own session, review and fix rounds included: running this command is the
   user's ask for each. A review goes through a session, not a background agent, so the next turn
   may start the fix round. Each listed stage names the model it runs on.
3. Every brief states: goal, files, what done looks like, which checks fit what the change can
   reach, the model it runs on (passed as `model` to `start_session`: the skill's model rule covers
   sessions too, and a session without it inherits the strongest model), the parts it hands to a
   cheaper worker when its tasks mix risk, and that the session ends each turn with no ask and none
   while its own background run is going: it waits inside the turn and ends with a result or a
   question to you. A session mid-turn receives your message only when that turn ends:
   everything it must know goes in the brief, and a rule found mid-wave is repeated in the next
   brief and checked in each report. A side session saves its final report as a file in the
   scratch folder its brief names and ends with five lines (head sha, its gate's exit line and
   counts, open questions, the file's path); read the file only when a decision needs it, and
   hand it to the reviewer by path. Every question and decision goes to you,
   never to the user. Run each gate command once yourself before it goes in a brief. A part that
   designs or plans loads `superpowers:brainstorming` and `superpowers:writing-plans` and sends its
   questions and design to you. Before each brief, check the project's MCP servers (your tool list,
   `.mcp.json`, `mcp` in `.claude/environments.json`) and name in the brief what the part may use.
4. Check each result against evidence before relaying it. For a side session's branch the
   evidence is: the pushed head equals the reported sha, `git status` in its worktree is
   clean, and the exit line and counts of its own gate log; plus a focused run of the one or
   two tests that carry the task's main risk. A side session runs the full gate on its branch
   before it reports; you run the full gate only once per merge point, never per branch: where
   the project has a gate lock, your run blocks every building session. Answer questions; after a
   second failure on one step split the task or move it to a stronger model. A follow-up of a few
   lines for an idle session's files may go to the integrating session as its own commit when no
   other writer is active on those files.
5. Merge only on the user's OK. Close a session once its result is checked and relayed, except: a
   builder session stays until the review of its part has no finding for it or its fix is verified
   and merged, and the review session until its re-review; list each under `## Side sessions kept`
   with that reason.
6. Ask the user only what is theirs to decide, in plain words, by what they get. When a side
   session's report names an approval it needs, put that question first in your reply, in the
   session's words, with the session's link.
7. While the plan is open, every turn updates the task list, a turn a side session's note woke too:
   the list draws under the latest message, so a turn without it hides the list.
8. While a side session runs, keep the session watcher in the background (`run_in_background`):
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice "${CLAUDE_PLUGIN_ROOT}/tools/session-watch/session_watch.py"`.
   It costs no tokens while it waits and ends with one `session-watch:` line per thing to act on,
   all in one exit: a side session's note the app has held for 2 minutes (it delivers notes only
   once every side session is idle, and a turn the user drove there only with the user's next
   message), a side session mid-turn with no transcript record for 5 minutes (never for a shell
   command running in `bypassPermissions`), or 30 minutes passed. On its notice, read the whole
   output file (the app appends a blank line and an exit footer, so never only its last lines),
   call `list_sessions` with `{"linked": true}`, read a named session's report with `list_events`
   `limit: 12` (a smaller page holds only the closing result line; else page once with
   `before_uuid`), handle every line in this one turn and start the watcher again; a note that
   arrives later about a result you already acted on needs no action. A `held-note: user-driven:`
   line is a turn the user drove in that session's window: read its report only for a result or a
   commit you do not have yet. A note turn leaves the watcher running;
   stop it when nothing runs. On `session-watch: error`, tell the user the line and keep a
   heartbeat instead: a background command that only waits 1800 s (600 s while a side session runs
   in a permission mode other than `bypassPermissions`), started again on each notice. A turn a
   background notice began cannot start a session (the app refuses until the user writes): a
   stage due then goes with `SendMessage` to an open session that has its context (a re-review to
   the review session: no start, so no refusal), else to the user as the `wrap_up` question with
   a start button, so one click starts it.
9. On a `quiet` line, tell the user in one line, with the session's link, that it may wait for
   their approval or answer in its own window.

In a turn an event started, ask a decision that leads to a session start through the `wrap_up` box,
not AskUserQuestion: its button answer starts a user turn, where the start works.

A refused start: the app refuses a new session on a turn started by an automated event or another
session's message, apart from a `<side-sessions-event>` turn right after the user's turn, for a
step they asked to have apart. Then propose the session as a card (`initiation: "own_initiative"`)
and keep other work moving. Schedule a wake in 10 minutes (`ScheduleWakeup`, else `CronCreate`):
on it, withdraw the card with `dismiss_task`; if the result says it started, leave it, otherwise run
the step with a background agent and say so. Where `start_session` is missing, `spawn_task` proposes
the session as a chip, coordination goes on with `SendMessage`, and `dismiss_task` withdraws it on
the 10 minute wake.

Where this environment cannot start sessions (the CLI, or no session tools), tell the user and run
the parts with background subagents instead; a part that designs or plans stays in this session.
