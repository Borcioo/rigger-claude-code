"""report_shadow: one row per subagent stop, saying whether its final report opens with `STATUS: <word>`.

Shadow only: it never holds a subagent. Runs as an async SubagentStop hook, prints nothing and exits 0. One row file per
(session, agent) is created with O_EXCL and holds one JSON line; it never holds report text.

    report_shadow.py --summary --data <plugin data dir>   one line per agent_type, then `# rows N`
"""
import hashlib
import json
import os
import re
import sys
import threading
import time

WAIT = 2.5          # seconds this process lives at most
ROW_CAP = 20000     # about 5 MB
TAIL_BYTES = 256 * 1024
TYPE_CAP = 60
VERDICTS = ("ok", "missing", "bad-value", "unread", "error")
STATUS_LINE = re.compile(r"STATUS: (DONE|PARTIAL|BLOCKED|NEEDS_CONTEXT)\s*")


def classify(text):
    """(verdict, status word) of a report: its first non-empty line decides."""
    for line in text.splitlines():
        if line.strip():
            found = STATUS_LINE.fullmatch(line)
            if found:
                return "ok", found.group(1)
            return ("bad-value" if line.startswith("STATUS:") else "missing"), ""
    return "missing", ""


def _assistant_text(raw_line):
    """The last text block of an assistant transcript line, None when the line has none."""
    try:
        record = json.loads(raw_line.decode("utf-8", "replace"))
        content = record["message"]["content"]
        if record["type"] != "assistant":
            return None
        texts = [block["text"] for block in content
                 if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)]
    except (ValueError, LookupError, TypeError, AttributeError):
        return None
    return texts[-1] if texts else None


def read_transcript_tail(path):
    """The last assistant text within the last TAIL_BYTES of the file at path, None when there is none."""
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - TAIL_BYTES))
        chunk = handle.read(TAIL_BYTES)
    for raw_line in reversed(chunk.split(b"\n")):   # a line cut at the window's edge is not valid JSON
        text = _assistant_text(raw_line)
        if text is not None:
            return text
    return None


def judge(event):
    """(verdict, status, source) of one SubagentStop event."""
    message = event.get("last_assistant_message")
    if isinstance(message, str):
        return classify(message) + ("event",)
    path = event.get("agent_transcript_path")
    if isinstance(path, str):   # an empty path fails in open
        try:
            text = read_transcript_tail(path)
        except OSError:
            text = None
        if text is not None:
            return classify(text) + ("transcript",)
    return "unread", "", "none"


def _text(value):
    return value if isinstance(value, str) else ""


def log_stop(event, started):
    """Writes the row of one stop. started is a time.monotonic() value."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data:
        return
    rows = os.path.join(data, "report-gate", "rows")
    os.makedirs(rows, exist_ok=True)
    if len(os.listdir(rows)) >= ROW_CAP:
        return
    fields = event if isinstance(event, dict) else {}
    session, agent = _text(fields.get("session_id")), _text(fields.get("agent_id"))
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sid8": session[:8], "agent8": agent[:8],
           "agent_type": _text(fields.get("agent_type"))[:TYPE_CAP]}
    try:   # an event that is not an object fails in judge, on `.get`
        row["verdict"], row["status"], row["source"] = judge(event)
    except Exception:
        row.update(verdict="error", status="", source="none")
    if session or agent:
        key = hashlib.sha256((session + "\0" + agent).encode("utf-8", "replace")).hexdigest()[:16]
    else:
        key = "noid-%d" % os.getpid()
    row["ms"] = int((time.monotonic() - started) * 1000)
    # FileExistsError: another stop of this agent already has its row, and this worker ends on it.
    fd = os.open(os.path.join(rows, key + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        os.write(fd, (json.dumps(row) + "\n").encode("ascii"))
    finally:
        os.close(fd)


def _read_row(path):
    """The row's agent_type and verdict; ValueError, OSError or UnicodeDecodeError when the file is not a row."""
    with open(path, "rb") as handle:
        row = json.loads(handle.read().decode("utf-8").splitlines()[0])
    if not isinstance(row, dict) or not isinstance(row.get("agent_type"), str) or row.get("verdict") not in VERDICTS:
        raise ValueError("not a row")
    return row["agent_type"], row["verdict"]


def summary(data):
    """Prints one line per agent_type and the totals. Exit code 1 when no row could be read."""
    folder = os.path.join(data, "report-gate", "rows")
    counts, unreadable = {}, 0
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if not name.endswith(".json"):
            continue
        try:
            agent_type, verdict = _read_row(os.path.join(folder, name))
        except (ValueError, IndexError, OSError):   # UnicodeDecodeError is a ValueError
            unreadable += 1
            continue
        counts.setdefault(agent_type, dict.fromkeys(VERDICTS, 0))[verdict] += 1
    for agent_type in sorted(counts):
        print((agent_type or "-") + "".join(" %s %d" % (verdict, counts[agent_type][verdict]) for verdict in VERDICTS))
    total = sum(sum(per.values()) for per in counts.values())
    print("# rows %d" % total)
    if unreadable:
        print("# unreadable %d" % unreadable)
    return 0 if total else 1


def main():
    """A hung read cannot hold the process: the wait is bounded and os._exit ends it with the worker still blocked."""
    args = sys.argv[1:]
    if "--summary" in args:
        if "--data" not in args[:-1]:
            sys.stderr.write("usage: report_shadow.py --summary --data <plugin data dir>\n")
            sys.exit(2)
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.exit(summary(args[args.index("--data") + 1]))
    started = time.monotonic()

    def work():
        try:
            try:
                event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
            except ValueError:
                event = None
            log_stop(event, started)
        except Exception:   # no traceback on stderr
            pass

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(WAIT)
    os._exit(0)


if __name__ == "__main__":
    main()
