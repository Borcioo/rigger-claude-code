---
description: Run the approved plan as its coordinator, with each lane of the plan in its own side session (needs the Claude desktop app's session tools and the superpowers plugin)
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

1. First run `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" "${CLAUDE_PLUGIN_ROOT}/tools/coord-state/coord_state.py" host`.
   `desktop` (the app): this command as written. `cli` (the terminal): also read
   `${CLAUDE_PLUGIN_ROOT}/tools/coord-state/cli-host.md` and follow it wherever a step names an app tool.
   `none`: run the parts with background subagents, as the last paragraph says. Add `coordinator: true` and
   `plan: <plan path>` to this session's state file frontmatter (`state-file.md` rule), and
   refresh that file each time a part starts or finishes: running sessions (the output of the
   `show` call), heads, Next step. Part states live in a script, not in your notes. Create its file with
   `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" "${CLAUDE_PLUGIN_ROOT}/tools/coord-state/coord_state.py" init`,
   then call `add <part> [--waits-on a,b]` once per part of the plan; every call below uses the same
   prefix and names only its command (`show` prints the table). Output without a `coord-state: ok` line is a refusal: a refused move
   (`coord-state: refused: <reason>`, exit 2) stops that step; put the reason line into the state file.
   Only this orchestrator session sets `coordinator: true`, never a side session. A coordinator
   rewrites the file with `true` before every turn end in which it waits for a side session or an
   agent, and sets `false` only around such a step, back to `true` right after; its running
   sessions are the output of the `show` call, not memory. Copy these two sentences into the state
   file's `## Keep verbatim`: compaction brings back the state file, not this command.
2. Split the approved plan by real inputs into lanes: a lane is a chain of parts where each needs the
   one before, and runs in one session on one model with its own subagents; a part that needs no
   other is a lane of one, and several small light parts may share a lane. A stage on another model
   gets its own session (a session's model is fixed when it starts), and a lane never reviews its own
   work: reviews run in a review session, fix rounds in the lane's session. List every stage up front
   with its lane, model and whether it is heavy (starts a heavy job: WSL, Docker, wp-env or e2e, the
   list the 2-at-a-time limit below counts) or light (writes files and runs tests, the full gate a
   side session runs before it reports included, which starts only while `mem-check` allows, as
   every build or test does): running this command is the user's ask
   for each; a part that waits names the exact input it waits for. A lane's next part starts only
   after the previous part is merged: add it with `--waits-on <previous part>`, and `coord_state`
   refuses an earlier `move <part> started`.

   A turn an event starts cannot start sessions, so in this user's turn start every session the plan
   needs: one per lane, the review session and one per stage on another model, plus one spare
   standby session per model the plan uses for parts not in the plan yet; record each id under
   `## Side sessions kept` as it starts (`show` lists no standby). A session whose part has its
   inputs gets its brief now (`move <part> started --session <id>` or `--branch <branch>`); every
   other one, a lane whose first part waits included, is a standby session (step 3) and has no part
   until it gets one: add none for it. Later stages go with `SendMessage`, which works on any turn: a
   lane's next stage and fix rounds to its session, a review to the review session, a new part to a
   free standby session on its model. Only a spare is free: a standby kept for a lane, the review or
   a model stage waits for that work.

   Sessions have no cap of our own (the app's cap: "A refused start"), but every stage keeps the
   `rigger-core:orchestrating-subagents` load rule in its session: a build or test starts only while
   `mem-check` allows, and heavy jobs (WSL, Docker, wp-env, e2e) run at most 2 at a time across all
   lanes. You keep that count: send a heavy stage only while fewer than 2 are out. The e2e run lock
   (`rigger-core:e2e-playwright-speed` §9) and a project's gate lock only keep two runs of one kind
   apart. You plan, brief, help a stuck session, check results, merge and open the pull requests; you
   do not implement. A background agent never stands in for a session, except under host `none`,
   where sessions cannot start (last paragraph) and in "A refused start".
3. Every brief states: goal, files, what done looks like, which checks fit what the change can
   reach (the session loads the testing skill the Skill triggers table names and runs only those while
   it works; a side session runs the full gate on its branch before it reports, and you run the full
   gate only once per merge point), the model it runs on (passed as `model` to `start_session`: the skill's model rule covers
   sessions too, and a session without it inherits the strongest model), the parts it hands to a
   cheaper worker when its tasks mix risk, and that the session ends each turn with no ask and none
   while its own background run is going: it waits inside the turn and ends with a result or a
   question to you. A standby session's brief says only: end this turn at once with the line
   `standby: ready`, then take your first message from the coordinator as the brief (send it all this
   step lists but the model, fixed at its start); its `standby: ready` note needs no action. A
   session mid-turn receives your message only when that turn ends:
   everything it must know goes in the brief, and a rule found mid-wave is repeated in the next
   brief and checked in each report. A side session saves its final report as a file in the
   scratch folder its brief names, opening with the STATUS line and the Summary, and ends with five
   lines (head sha, its gate's exit line and counts, open questions, the file's path); read the
   file's head (up to the first `##` after Summary), the whole file only when a decision needs it,
   and hand it to the reviewer by path. Every question and decision goes to you,
   never to the user. Run each gate command once yourself before it goes in a brief. A part that
   designs or plans loads the brainstorm and plans skills the Skill triggers table names and sends its
   questions and design to you. Before each brief, check the project's MCP servers (your tool list,
   `.mcp.json`, `mcp` in `.claude/environments.json`) and name in the brief what the part may use.
4. Check each result against evidence before relaying it. For a side session's branch the
   evidence is: the pushed head equals the reported sha, `git status` in its worktree is
   clean, and the exit line and counts of its own gate log; plus a focused run of the one or
   two tests that carry the task's main risk. A side session runs the full gate on its branch
   before it reports; you run the full gate only once per merge point, never per branch: where
   the project has a gate lock, your run blocks every building session. Run it on a detached
   snapshot of the pushed commit (fetch first), never in a side session's worktree, where its edits change the
   tree mid-run: the project's snapshot option where it has one (`--snapshot <ref>`), else
   `git worktree add --detach <temp folder> <sha>`, removed afterwards, on a failure too. Answer questions; after a
   second failure on one step split the task or move it to a stronger model. A follow-up of a few
   lines for an idle session's files may go to the integrating session as its own commit when no
   other writer is active on those files. After the evidence check, call
   `move <part> built --head <sha> --evidence '<its gate pass line>'`; after a review with no
   finding for the part, `move <part> reviewed --evidence '<review result>'`; a fix round sends it
   back with `move <part> started --evidence '<finding>'`. For a Risk-full part,
   `move <part> reviewed` comes after the second round: a fresh agent of the `reviewer` slot
   reviews again after the first fix round (`orchestrating-subagents`, Fix rounds). With the
   `reviewer` slot empty, the review is blocked: report it as `BLOCKED` with the install line from
   the session notice, or the `slot reviewer` line `/rigger-core:doctor` prints when there is none;
   never skip the review or give it to an agent outside the slot. That part
   stays `built`; set `stop: goal-unreachable` and ask the user to fill the slot as that line says (step 6).
5. Merge each reviewed part into the integration branch (the approved plan is
   the user's OK; the default branch only on their explicit instruction, at
   `stop: main`, also when it is the integration branch), and call
   `move <part> merged --head <merge sha> --evidence '<text>'` with the evidence
   quoting the user's plan approval (for the default branch, their
   instruction) in quotes (`"…"`, `“…”` or `„…”`; `--evidence` goes in single quotes, so a straight `"…"` survives the shell). Its session removes its worktree once it merges (under `cli` you do); branches stay. Close a
   session once its result is checked and relayed, except: a
   builder session stays until the review of its part has no finding for it or its fix is verified
   and merged, and the review session until its re-review; list each under `## Side sessions kept`
   with that reason. A standby session stays there as `standby` until it gets a part; close the unused
   ones once every part is merged.
6. Ask the user only at a stop of the `plans-main.md` rule, with `stop:` set (clear it once they
   answer), in plain words, by what they get. When a side
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
   the review session: no start, so no refusal), else to a free standby session on its model, else
   set `stop: app-refused` and send the user the
   `wrap_up` question with a start button, so one click starts it.
9. On a `quiet` line, tell the user in one line, with the session's link, that it may wait for
   their approval or answer in its own window.
10. When every part is merged, write one final summary (decisions taken, follow-ups, estimate
   overruns), set `stop: done` and ask once whether to delete the merged branches; after the
   answer, or when the user says stop, remove `plan:` and `stop:` from the frontmatter.

In a turn an event started, ask a decision that leads to a session start through the `wrap_up` box,
not AskUserQuestion: its button answer starts a user turn, where the start works.

A refused start: the app refuses a new session on a turn started by an automated event or another
session's message, apart from a `<side-sessions-event>` turn right after the user's turn, for a
step they asked to have apart. Then give the step to a free standby session on its model with
`SendMessage`; with none free, propose the session as a card (`initiation: "own_initiative"`)
and keep other work moving. Schedule a wake in 10 minutes (`ScheduleWakeup`, else `CronCreate`):
on it, withdraw the card with `dismiss_task`; if the result says it started, leave it, otherwise run
the step with a background agent and say so. A start refused past the app's cap of sessions working
at once is the user's card to click: tell them in one line what waits on it; once a session
ends, give the step to a free standby session on its model and withdraw the card (`dismiss_task`);
with none, the card stays the user's; give it never to a background agent without the user's OK. Where `start_session` is missing, `spawn_task` proposes
the session as a chip, coordination goes on with `SendMessage`, and `dismiss_task` withdraws it on
the 10 minute wake.

Where this environment cannot start sessions (the CLI, or no session tools), tell the user and run
the parts with background subagents instead; a part that designs or plans stays in this session.
