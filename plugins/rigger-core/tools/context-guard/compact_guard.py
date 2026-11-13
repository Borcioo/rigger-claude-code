"""Compaction hooks: let automatic compaction run only at a safe point, and re-inject the session's
state file after it.

  pre    PreCompact (matcher auto): blocks while the state file says `safe_to_compact: false`, unless
         the context is below the floor, at the hard cap, or the session has been idle 45 min.
         A state file with `coordinator: true` allows on `safe_to_compact: true` (file not older than
         30 min before the last request) and otherwise blocks until the coordinator cap: no idle rule.
  post   PostCompact: saves the summary next to the state file and resets it to
         `safe_to_compact: false`.
  start  SessionStart (matcher compact): adds the state file to the context, its `Next step` and
         `Keep verbatim` sections first; a file too long for the context is cut and its first line says so.

State file: `<state dir>/<session_id>.md`, frontmatter `---` / `safe_to_compact: true|false` /
`updated: <ISO time>` / `---`, then the body. Fails open: on any error `pre` allows (no output),
`post` / `start` print nothing, and a state file they cannot parse is left as it is.

Usage: python -I -S compact_guard.py pre|post|start [--floor N] [--hard-cap N] [--coordinator-cap N]
       [--state-dir ABS]
"""
import datetime
import json
import os
import sys

DEFAULT_FLOOR = 200_000
DEFAULT_HARD_CAP = 450_000
DEFAULT_COORDINATOR_CAP = 950_000
IDLE_SECONDS = 45 * 60
STALE_SECONDS = 30 * 60
LOG_NAME = "compact-guard.log"
LOG_MAX = 1 << 20
CONTEXT_MAX = 9_500
KEY = "safe_to_compact"
COORDINATOR_KEY = "coordinator"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _options(argv):
    opts = {"floor": DEFAULT_FLOOR, "hard_cap": DEFAULT_HARD_CAP, "coordinator_cap": DEFAULT_COORDINATOR_CAP,
            "state_dir": None}
    for flag, key in (("--floor", "floor"), ("--hard-cap", "hard_cap"), ("--coordinator-cap", "coordinator_cap")):
        if flag in argv:
            try:
                value = int(argv[argv.index(flag) + 1])
            except (IndexError, ValueError):
                continue
            if value > 0:
                opts[key] = value
    if "--state-dir" in argv and argv.index("--state-dir") + 1 < len(argv):
        opts["state_dir"] = argv[argv.index("--state-dir") + 1]
    return opts


def _write_atomic(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    temp = os.path.join(folder, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    try:
        with open(temp, "wb") as fh:
            fh.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _split_frontmatter(text):
    """(lines of the frontmatter including both `---` lines, body) or None. Lines keep their endings."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[:index + 1], "".join(lines[index + 1:])
    return None


def _key_of(line):
    return line.split(":", 1)[0].strip() if ":" in line else None


def _safe_value(path, key=KEY):
    """True / False from the state file's frontmatter `key` (default `safe_to_compact`), a string for
    anything else, None when there is no readable state or no such key."""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    parts = _split_frontmatter(text)
    if parts is None:
        return None
    for line in parts[0][1:-1]:
        if _key_of(line) == key:
            value = line.split(":", 1)[1].strip().strip("\"'").strip().lower()
            return {"true": True, "false": False}.get(value, value)
    return None


def _idle(entry, now):
    if entry["tool_use"] or entry["stop_reason"] != "end_turn" or not entry["timestamp"]:
        return False
    try:
        moment = datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        return False
    return (now - moment).total_seconds() > IDLE_SECONDS


def _stale(state, entry):
    """True when the state file was written more than STALE_SECONDS before the last request (the session
    kept working after it wrote `true`), or when that cannot be told."""
    try:
        written = os.stat(state).st_mtime
        moment = datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    except (OSError, ValueError, AttributeError):
        return True
    if moment.tzinfo is None:
        return True
    return moment.timestamp() - written > STALE_SECONDS


def _decide(event, opts, guard, state):
    """(block reason or None, log reason, tokens or None)."""
    if event.get("trigger") != "auto":
        return None, "manual", None
    if event.get("agent_id") is not None:
        return None, "agent", None
    path = event.get("transcript_path")
    try:
        entry = guard.last_entry(path) if isinstance(path, str) and path else None
    except OSError:
        entry = None
    if entry is None:
        return None, "no-transcript", None
    tokens = entry["tokens"]
    if tokens < opts["floor"]:
        return None, "below-floor", tokens
    if state is not None and _safe_value(state, COORDINATOR_KEY) is True:   # no idle rule; fails closed
        if tokens >= opts["coordinator_cap"]:
            return None, "coordinator-cap", tokens
        value = _safe_value(state)
        if value is True:
            if _stale(state, entry):
                return ("compaction postponed at %dk: %s has %s: true but was written over %d min before the "
                        "last request, forced at %dk" % (tokens // 1000, state, KEY, STALE_SECONDS // 60,
                                                         opts["coordinator_cap"] // 1000)), "coordinator-stale", tokens
            return None, "coordinator-safe", tokens
        reason = "coordinator" if value is False else "coordinator-bad-state"
        return ("compaction postponed at %dk: %s has %s: %s (coordinator), forced at %dk"
                % (tokens // 1000, state, KEY, "false" if value is False else "missing or not true/false",
                   opts["coordinator_cap"] // 1000)), reason, tokens
    if tokens >= opts["hard_cap"]:
        return None, "hard-cap", tokens
    if _idle(entry, _now()):
        return None, "idle", tokens
    if state is None:
        return None, "bad-sid", tokens
    value = _safe_value(state)
    if value is True:
        return None, "safe", tokens
    if value is False:
        return "compaction postponed at %dk: %s has %s: false" % (tokens // 1000, state, KEY), "unsafe", tokens
    return None, ("no-state" if value is None else "bad-value"), tokens


def _append(path, line):
    """Appends one line. On Windows `open(path, "a")` seeks to the end and then writes, so overlapping
    hooks overwrite each other's lines; a handle with FILE_APPEND_DATA and no FILE_WRITE_DATA (GENERIC_WRITE
    includes it and loses lines) makes the system choose the end of file for every write."""
    if os.name != "nt":
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    file_append_data, share_all, open_always, attribute_normal = 0x4, 0x7, 4, 0x80
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(path, file_append_data, share_all, None, open_always, attribute_normal, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_BINARY)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _log(state_dir, sid, tokens, decision, reason):
    path = os.path.join(state_dir, LOG_NAME)
    try:
        if os.path.getsize(path) > LOG_MAX:
            with open(path, "rb") as fh:
                data = fh.read()
            half = data[len(data) // 2:]
            _write_atomic(path, half[half.find(b"\n") + 1:])
    except OSError:
        pass
    line = "%s %s %s %s %s\n" % (_iso(_now()), sid, "-" if tokens is None else tokens, decision, reason)
    try:
        os.makedirs(state_dir, exist_ok=True)
        _append(path, line)
    except OSError:
        pass


def pre(event, opts, guard, state_dir):
    sid = event.get("session_id")
    state = guard.state_path(state_dir, sid)
    block, reason, tokens = _decide(event, opts, guard, state)
    _log(state_dir, sid if state else "-", tokens, "block" if block else "allow", reason)
    if block:
        sys.stdout.write(json.dumps({"decision": "block", "reason": block}))


def post(event, guard, state_dir):
    state = guard.state_path(state_dir, event.get("session_id"))
    if state is None or event.get("agent_id") is not None:
        return
    summary = event.get("compact_summary")
    trigger = event.get("trigger") if event.get("trigger") in ("auto", "manual") else "-"
    if isinstance(summary, str) and summary:
        head = "%s %s\n" % (_iso(_now()), trigger)
        try:
            _write_atomic(state[:-3] + ".summary.md",
                          (head + summary + ("" if summary.endswith("\n") else "\n")).encode("utf-8"))
        except OSError:   # the summary is for auditing; the reset still has to happen
            pass
    _reset(state)


def _reset(state):
    """Rewrites the frontmatter to `safe_to_compact: false` and a fresh `updated`; body untouched.
    A state file that is missing, not UTF-8, without frontmatter (unpacking None) or read-only raises
    here; the caller's fail-open try then leaves it as it is (the summary is already saved).
    The explicit check makes POSIX match Windows, where a rename over a read-only file fails but
    POSIX would replace it; the mode bits cover root, for whom os.access always grants W_OK."""
    if not os.access(state, os.W_OK) or not os.stat(state).st_mode & 0o222:
        raise PermissionError(state)
    with open(state, "rb") as fh:
        data = fh.read()
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    text = data[len(bom):].decode("utf-8")
    head, body = _split_frontmatter(text)
    newline = "\r\n" if head[0].endswith("\r\n") else "\n"
    wanted = {KEY: "false", "updated": _iso(_now())}
    lines = [head[0]]
    for line in head[1:-1]:
        key = _key_of(line)
        if key in wanted:
            ending = line[len(line.rstrip("\r\n")):] or newline
            lines.append("%s: %s%s" % (key, wanted.pop(key), ending))
        else:
            lines.append(line)
    lines.extend("%s: %s%s" % (key, value, newline) for key, value in wanted.items())
    lines.append(head[-1])
    _write_atomic(state, bom + "".join(lines + [body]).encode("utf-8"))


def _headings(lines):
    """(index, level, lowercased title) of each Markdown heading outside fenced code blocks."""
    found, fence = [], None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence) and not stripped.strip(fence[0]):
                fence = None
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        hashes = len(line) - len(line.lstrip("#"))
        if 1 <= hashes <= 6 and line[hashes:hashes + 1] in (" ", "\t"):
            found.append((index, hashes, line[hashes:].strip().rstrip(":").strip().lower()))
    return found


def _section(lines, headings, names, outside=(0, 0)):
    """(start, end) line range of the first heading named in `names` and what follows it up to the
    next heading of the same or a higher level; a section overlapping `outside` does not count."""
    for position, (index, level, title) in enumerate(headings):
        if title not in names:
            continue
        end = next((i for i, lvl, _ in headings[position + 1:] if lvl <= level), len(lines))
        if index < outside[1] and outside[0] < end:   # nested in or around the other section
            continue
        return index, end
    return None


def _ordered_body(body):
    """The body with its `Next step` section first and its `Keep verbatim` section second, both as
    written, then the rest; a cap that cuts the end cuts the rest first."""
    lines = body.splitlines(keepends=True)
    headings = _headings(lines)
    first = _section(lines, headings, ("next step", "next steps"))
    second = _section(lines, headings, ("keep verbatim",), first or (0, 0))
    moved = [part for part in (first, second) if part]
    rest = [line for index, line in enumerate(lines) if not any(a <= index < b for a, b in moved)]
    parts = ["".join(lines[a:b]).rstrip() for a, b in moved] + ["".join(rest).strip()]
    return "\n\n".join(part for part in parts if part)


def _utf16_units(text):
    return len(text.encode("utf-16-le")) // 2


def _cut(text, units):
    """The longest prefix of `text` that is at most `units` UTF-16 code units long."""
    return text.encode("utf-16-le")[:2 * units].decode("utf-16-le", "ignore")


def start(event, guard, state_dir):
    state = guard.state_path(state_dir, event.get("session_id"))
    if state is None or event.get("agent_id") is not None or event.get("source") != "compact":
        return
    first = "This session was compacted."
    try:
        with open(state, "rb") as fh:
            text = fh.read().decode("utf-8-sig", "replace")
    except OSError:
        text = None
    if text is None:
        context = "%s No state file exists at %s." % (first, state)
    else:
        parts = _split_frontmatter(text)
        body = _ordered_body(parts[1] if parts else text)
        context = "%s Its state file is %s; its content follows.\n\n%s" % (first, state, body)
        if _utf16_units(context) > CONTEXT_MAX:
            marker = "\n[truncated: read %s]" % state
            context = ("%s Its state file is %s; its %d characters do not fit here (%s), so only its head "
                       "follows: read the file, then move history to %s.log.md until it fits.\n\n%s"
                       % (first, state, _utf16_units(body), "{:,}".format(CONTEXT_MAX).replace(",", " "),
                          os.path.basename(state)[:-3], body))
            context = _cut(context, CONTEXT_MAX - _utf16_units(marker)) + marker
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                        "additionalContext": context}}))


def main(argv):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import context_guard as guard
    command = argv[0] if argv else None
    opts = _options(argv[1:])
    state_dir = opts["state_dir"] or guard.default_state_dir()
    # stdin that is not a JSON object raises here or at the first event.get: the caller's try allows
    event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if command == "pre":
        pre(event, opts, guard, state_dir)
    elif command == "post":
        post(event, guard, state_dir)
    elif command == "start":
        start(event, guard, state_dir)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
