"""The turn-end guard of side_session_guard.py's Stop path (D89): hold the end of a turn while the session's
own gate or test command runs in the background.

The Stop event's `background_tasks` lists the session's live tasks. A `shell` entry with `status: running`
whose command matches GATE_COMMAND is waited for up to POLL_BOUND seconds (its output file gains an
`[exited with code N]` or `[killed]` trailer when it ends); one still running blocks the turn, with a
one-line reason naming the task. Never held: a `claude -p` run (`CLAUDE_CODE_ENTRYPOINT=sdk-cli`, which
waits for its tasks itself), a session whose state file says `coordinator: true`, a task already blocked
CAP times (the next Stop allows with one line), and every session when config `turn_guard.enabled` is
false (levels 2-4). No env variable marks a side session (docs/superpowers/spikes/phase-3b/
side-session-marker.md), so every other session is held too.

The block count lives in `<state dir>/<session id>.turn-guard.json`, written before the block; a count
that cannot be written allows, so the cap always holds. Fails open: a missing field, a task with no
output file or any error holds nothing.
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
CAP = 3                 # blocks per task id
POLL_BOUND = 4.0        # seconds one Stop waits for a task to end
POLL_STEP = 0.25
DESCRIPTION_MAX = 60
TAIL = 256              # bytes read from the end of an output file
TRAILER = re.compile(r"\[(?:exited with code -?\d+|killed)\]")
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# D89's gate and test list. A name must stand alone: `delegate.py` is not `gate.py`, `jest.config.js` and
# `npm run testing` are not test runs.
GATE_COMMAND = re.compile(
    r"(?<![\w.-])(?:gate\.py|run_tests\.py|safe_edit(?:\.py)?|pytest|vitest|jest|phpunit|playwright)(?![\w.-])"
    r"|-m\s+unittest(?![\w-])"
    r"|\bnpm\s+(?:run\s+)?test(?![\w:-])"
    r"|\b(?:go|cargo)\s+test(?![\w-])")
HERE = os.path.dirname(os.path.abspath(__file__))
ASSEMBLE = os.path.join(os.path.dirname(HERE), "assemble")
CORE_ROOT = os.path.dirname(os.path.dirname(HERE))


def running_gates(event):
    """(id, description, command) of each running shell task whose command is a gate or test run."""
    tasks = event.get("background_tasks")
    found = []
    for item in tasks if isinstance(tasks, list) else []:
        if not isinstance(item, dict) or item.get("type") != "shell" or item.get("status") != "running":
            continue
        task_id, command = item.get("id"), item.get("command")
        if isinstance(task_id, str) and TASK_ID.fullmatch(task_id) and isinstance(command, str) \
                and GATE_COMMAND.search(command):
            description = item.get("description")
            found.append((task_id, description if isinstance(description, str) else "", command))
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


def block_reason(task_id, description, count):
    return ('%s background run %s ("%s") is still running: wait for it to finish (read its output file, or '
            'wait for its completion notice) before you end the turn. Block %d of %d.'
            % (PREFIX, task_id, one_line(description), count, CAP))


def _load_counts(path):
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):   # rewritten whole by the next block, or a write that fails allows
        return {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, dict):
        return {}
    return {key: value for key, value in blocks.items()
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)}


def _save_counts(path, counts):
    """Write the counts atomically; False when they could not be written."""
    temporary = "%s.%d" % (path, os.getpid())
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"format": 1, "blocks": counts}, fh)
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
    gates = running_gates(event)
    session_id = event.get("session_id")
    if not gates or not isinstance(session_id, str) or not state.endswith(".md"):
        return None, None
    count_path = state[:-len(".md")] + COUNT_SUFFIX
    counts = _load_counts(count_path)
    note = None
    open_gates = []
    for task_id, description, command in gates:
        if counts.get(task_id, 0) < CAP:
            open_gates.append((task_id, description))
        elif counts[task_id] == CAP and note is None and _save_counts(count_path, dict(counts, **{task_id: CAP + 1})):
            counts[task_id] = CAP + 1
            note = ('%s background run %s ("%s") still runs after %d holds; the turn ends without it.'
                    % (PREFIX, task_id, one_line(description), CAP))
    if not open_gates:
        return None, note
    project = environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
    if not isinstance(project, str) or not enabled(config_dir, project, environ):
        return None, note
    paths = {}
    for task_id, _ in open_gates:
        path = output_file(session_id, task_id, environ)
        if path is not None:
            paths[task_id] = path
    still = poll(paths, clock=clock, sleep=sleep)
    for task_id, description in open_gates:
        if task_id in still:
            count = counts.get(task_id, 0) + 1
            if not _save_counts(count_path, dict(counts, **{task_id: count})):
                return None, note
            return block_reason(task_id, description, count), None
    return None, note
