"""The turn-end guard of side_session_guard.py's Stop path (D89): hold the end of a turn while the session's
own background work still runs, so a session waits inside its turn instead of ending it on "waiting".

The Stop event's `background_tasks` lists the session's live tasks; a task stopped with TaskStop, finished or
failed is gone from it. Held: an entry with `status: running` of type `shell` (any command) or `subagent`. A
`monitor` is not held: it is a watcher that outlives turns by design (events, expiry, re-arm), and holding it
would only loop. A running shell task is waited for up to POLL_BOUND seconds (its output file gains an
`[exited with code N]` or `[killed]` trailer when it ends); a subagent's output file never gets one, so a
running subagent blocks at once and no shell task is polled with it. A task still running blocks the turn
with a one-line reason naming the task and how to wait for it. Never held: a `claude -p` run
(`CLAUDE_CODE_ENTRYPOINT=sdk-cli`, which waits for its tasks itself), a session whose state file says
`coordinator: true`, a task already blocked CAP times (the next Stop allows with one line), a turn
(`prompt_id`) already blocked TURN_CAP times, a Stop chain already blocked once when the event has no
`prompt_id`, and every session when config `turn_guard.enabled` is false (levels 2-4). No env variable marks
a side session (docs/superpowers/spikes/phase-3b/side-session-marker.md), so every other session is held too.

The counts live in `<state dir>/<session id>.turn-guard.json`, written before the block; the state folder is
made when it is missing. A count that cannot be written still holds the first Stop of a chain and allows the
next one (`stop_hook_active` true) with one line, so a Stop chain is held at most once without a count. Fails
open: a missing or malformed `background_tasks`, task or session id field, a shell task with no output file,
and an exception inside the check (side_session_guard.py then allows and writes one stderr line) hold
nothing; a config that does not load keeps the guard on.
"""
import glob
import json
import os
import re
import sys
import tempfile
import time

PREFIX = "turn-guard:"
COUNT_SUFFIX = ".turn-guard.json"
CAP = 2                 # blocks per task id
TURN_CAP = 2            # blocks per turn (prompt_id), across its tasks
POLL_BOUND = 4.0        # seconds one Stop waits for a shell task to end
POLL_STEP = 0.25
DESCRIPTION_MAX = 60
TAIL = 256              # bytes read from the end of an output file
TRAILER = re.compile(r"\[(?:exited with code -?\d+|killed)\]")
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
HELD_TYPES = ("shell", "subagent")
HERE = os.path.dirname(os.path.abspath(__file__))
ASSEMBLE = os.path.join(os.path.dirname(HERE), "assemble")
CORE_ROOT = os.path.dirname(os.path.dirname(HERE))


def running_tasks(event):
    """(id, type, description) of each running shell task (with a command) and running subagent."""
    tasks = event.get("background_tasks")
    found = []
    for item in tasks if isinstance(tasks, list) else []:
        if not isinstance(item, dict) or item.get("type") not in HELD_TYPES or item.get("status") != "running":
            continue
        task_id = item.get("id")
        if not (isinstance(task_id, str) and TASK_ID.fullmatch(task_id)):
            continue
        if item["type"] == "shell" and not isinstance(item.get("command"), str):
            continue
        description = item.get("description")
        found.append((task_id, item["type"], description if isinstance(description, str) else ""))
    return found


def output_file(session_id, task_id, environ):
    """The task's output file: beside the CLI's notes folder for the session, else under
    `<temp root>/<claude folder>/<project key>/<session id>/tasks/`; None when there is none. The temp root is
    `CLAUDE_CODE_TMPDIR`, else the platform's temp folder; the folder is `claude-<uid>` where the platform
    has a uid (`claude-` where it has none), and `claude` on Windows."""
    notes = environ.get("CLAUDE_CODE_CHILD_NOTES_DIR")
    if notes and os.path.basename(os.path.dirname(os.path.normpath(notes))) == session_id:
        path = os.path.join(os.path.dirname(os.path.normpath(notes)), "tasks", task_id + ".output")
        if os.path.isfile(path):
            return path
    getuid = getattr(os, "getuid", None)
    folders = ("claude", "claude-%s" % (getuid() if getuid else ""))
    roots = [environ.get(name) for name in ("CLAUDE_CODE_TMPDIR", "TMPDIR", "TEMP", "TMP")] \
        + [tempfile.gettempdir()]
    for root in dict.fromkeys(root for root in roots if root):
        for folder in folders:
            pattern = os.path.join(glob.escape(root), folder, "*", session_id, "tasks", task_id + ".output")
            for path in glob.glob(pattern):
                if os.path.isfile(path):
                    return path
    return None


def finished(path):
    """True when the output file's last line is a trailer (CR stripped); False while it runs."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL))
        tail = fh.read().decode("utf-8", "replace").replace("\r\n", "\n")
    lines = [line for line in tail.split("\n") if line.strip()]
    return bool(lines) and TRAILER.fullmatch(lines[-1].strip()) is not None


def poll(paths, bound=POLL_BOUND, clock=time.monotonic, sleep=time.sleep):
    """The ids of `paths` ({id: output file}) still running after waiting up to `bound` seconds; an
    unreadable file counts as ended."""
    deadline = clock() + bound
    running = set(paths)
    while True:
        for task_id in sorted(running):
            try:
                ended = finished(paths[task_id])
            except OSError:
                ended = True
            if ended:
                running.discard(task_id)
        if not running or clock() >= deadline:
            return running
        sleep(min(POLL_STEP, max(0.0, deadline - clock())))


def enabled(config_dir, project_dir, environ):
    """Config `turn_guard.enabled` from levels 2-4; True when no level sets it or the config does not load."""
    if ASSEMBLE not in sys.path:
        sys.path.insert(0, ASSEMBLE)
    try:
        from pathlib import Path
        import config
        import discover
        found = discover.discover(Path(config_dir), Path(project_dir), environ, Path(CORE_ROOT))
        paths = {3: os.path.join(config_dir, "rigger", "config.json"),
                 4: os.path.join(project_dir, ".claude", "rigger.json")}
        levels = {level: _only_turn_guard(config.load_level(Path(path), level)[0]) for level, path in paths.items()}
        company = [(m.name, _only_turn_guard(m.config)) for m in found.modules
                   if m.layer == "company" and isinstance(m.config, dict)]
        resolved = config.resolve({}, company, levels[3], levels[4], paths)
    except Exception:   # a config that does not load keeps the default
        return True
    block = resolved.effective.get("turn_guard")
    return not (isinstance(block, dict) and block.get("enabled") is False)


def _only_turn_guard(data):
    return {"turn_guard": data["turn_guard"]} if isinstance(data, dict) and "turn_guard" in data else {}


def one_line(text):
    text = " ".join(text.split())
    return text if len(text) <= DESCRIPTION_MAX else text[:DESCRIPTION_MAX - 3] + "..."


def block_reason(task_id, kind, description, path, count):
    until = ("a shell loop under `timeout` of a few minutes; its completion notice arrives after that call, "
             "and you repeat while it runs" if kind == "subagent"
             else "until %s ends with an [exited with code N] line" % path)
    return ('%s background task %s ("%s") is still running. Wait for it inside this turn with one bounded '
            'foreground wait (%s), then continue; end the turn only with a result or a question. If it is '
            'meant to outlive the turn (a server, a watcher), say so and end the turn. Block %d of %d.'
            % (PREFIX, task_id, one_line(description), until, count, CAP))


def _load_counts(path):
    """({task id: blocks}, {"prompt": id, "blocks": n} of the last held turn, or {})."""
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):   # rewritten whole by the next block
        return {}, {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    turn = data.get("turn") if isinstance(data, dict) else None
    counts = {key: value for key, value in blocks.items()
              if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)} \
        if isinstance(blocks, dict) else {}
    valid_turn = isinstance(turn, dict) and isinstance(turn.get("prompt"), str) \
        and isinstance(turn.get("blocks"), int) and not isinstance(turn.get("blocks"), bool)
    return counts, (turn if valid_turn else {})


def _save_counts(path, counts, turn):
    """Write the counts atomically; False when they could not be written."""
    temporary = "%s.%d" % (path, os.getpid())
    data = {"format": 1, "blocks": counts}
    if turn:
        data["turn"] = turn
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)   # a fresh config has no state folder
        with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass
        return False


def check(event, state, coordinator, environ, config_dir, clock=time.monotonic, sleep=time.sleep):
    """(block reason or None, note or None) for a Stop event; `state` is the session's state file path."""
    if event.get("hook_event_name") != "Stop" or environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli" or coordinator:
        return None, None
    tasks = running_tasks(event)
    session_id = event.get("session_id")
    if not tasks or not isinstance(session_id, str) or not state.endswith(".md"):
        return None, None
    count_path = state[:-len(".md")] + COUNT_SUFFIX
    counts, turn = _load_counts(count_path)
    prompt = event.get("prompt_id") if isinstance(event.get("prompt_id"), str) else None
    turn_blocks = turn["blocks"] if prompt is not None and turn.get("prompt") == prompt else 0
    note = None
    open_tasks = []
    for task_id, kind, description in tasks:
        if counts.get(task_id, 0) < CAP:
            open_tasks.append((task_id, kind, description))
        elif counts[task_id] == CAP and note is None and _save_counts(
                count_path, dict(counts, **{task_id: CAP + 1}), turn):
            counts[task_id] = CAP + 1
            note = ('%s background task %s ("%s") still runs after %d holds; the turn ends without it.'
                    % (PREFIX, task_id, one_line(description), CAP))
    if not open_tasks:
        return None, note
    if prompt is None and event.get("stop_hook_active") is True:
        return None, note   # no turn to count in: one hold per Stop chain
    if turn_blocks >= TURN_CAP:
        return None, note or ('%s %d holds this turn; it ends with background task %s ("%s") still running.'
                              % (PREFIX, TURN_CAP, open_tasks[0][0], one_line(open_tasks[0][2])))
    project = environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
    if not isinstance(project, str) or not enabled(config_dir, project, environ):
        return None, note
    paths = {}
    for task_id, kind, _ in open_tasks:
        path = output_file(session_id, task_id, environ) if kind == "shell" else None
        if path is not None:
            paths[task_id] = path
    agents = any(kind == "subagent" for _, kind, _ in open_tasks)
    still = poll(paths, bound=0.0 if agents else POLL_BOUND, clock=clock, sleep=sleep)
    for task_id, kind, description in open_tasks:
        if kind == "subagent" or task_id in still:
            count = counts.get(task_id, 0) + 1
            held_turn = {"prompt": prompt, "blocks": turn_blocks + 1} if prompt is not None else {}
            if not _save_counts(count_path, dict(counts, **{task_id: count}), held_turn) \
                    and event.get("stop_hook_active") is True:
                return None, ("%s background task %s (\"%s\") still runs, but its block count cannot be written "
                              "(%s); the turn ends without holding again."
                              % (PREFIX, task_id, one_line(description), count_path))
            return block_reason(task_id, kind, description, paths.get(task_id), count), note
    return None, note
