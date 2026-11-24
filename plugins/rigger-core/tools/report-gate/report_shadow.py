"""report_shadow: one row per subagent stop, saying whether its final report opens with `STATUS: <word>` and, for an
agent that fills a slot, whether the report meets that slot's contract (shape only: no git, and no file read but the slot table).

Shadow only: it never holds a subagent. Runs as an async SubagentStop hook, prints nothing and exits 0. One row file per
(session, agent) is created with O_EXCL and holds one JSON line; it never holds report text.

    report_shadow.py --summary --data <plugin data dir>   one line per agent_type (verdict counts, then `summary <n>`
                                                          rows with an opening `## Summary`, then `contract-ok <n>`
                                                          and `contract-refused <n>`), then `# rows N`
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_ROOT = os.path.dirname(TOOLS)
WAIT = 2.5          # seconds this process lives at most
ROW_CAP = 20000     # about 5 MB
TAIL_BYTES = 256 * 1024
TYPE_CAP = 60
VERDICTS = ("ok", "missing", "bad-value", "unread", "error")
STATUS_LINE = re.compile(r"STATUS: (DONE|PARTIAL|BLOCKED|NEEDS_CONTEXT)\s*")
CONTRACT_COUNTS = (("contract-ok", lambda value: value == "ok"),
                   ("contract-refused", lambda value: isinstance(value, str) and value.startswith("refused:")))
COLUMNS = VERDICTS + ("summary",) + tuple(column for column, _ in CONTRACT_COUNTS)


def classify(text):
    """(verdict, status word) of a report: its first non-empty line decides."""
    for line in text.splitlines():
        if line.strip():
            found = STATUS_LINE.fullmatch(line)
            if found:
                return "ok", found.group(1)
            return ("bad-value" if line.startswith("STATUS:") else "missing"), ""
    return "missing", ""


def has_summary(text):
    """True when a valid STATUS line, then `## Summary` (one blank line may follow it), then 3 to 5 `- ` lines (each may be continued by lines indented two
    spaces or more) open the report."""
    lines = text.splitlines()
    heading = [number for number, line in enumerate(lines) if line.strip()][1:2]
    if not heading or classify(text)[0] != "ok" or lines[heading[0]].rstrip() != "## Summary":
        return False
    first = heading[0] + 1
    if first < len(lines) and not lines[first].strip():   # one blank line may follow the heading
        first += 1
    bullets = 0
    for line in lines[first:]:
        if line.startswith("- "):
            bullets += 1
        elif not (bullets and line.startswith("  ") and line.strip()):   # an indented line continues its bullet
            break   # a blank line, a heading or other text ends the list
    return 3 <= bullets <= 5


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
    """(verdict, status, source, has_summary) of one SubagentStop event."""
    message = event.get("last_assistant_message")
    if isinstance(message, str):
        return classify(message) + ("event", has_summary(message))
    path = event.get("agent_transcript_path")
    if isinstance(path, str):   # an empty path fails in open
        try:
            text = read_transcript_tail(path)
        except OSError:
            text = None
        if text is not None:
            return classify(text) + ("transcript", has_summary(text))
    return "unread", "", "none", False


def _text(value):
    return value if isinstance(value, str) else ""


def _agent_slots(session):
    """(slot path, agent target) of each filled agent slot: the session's snapshot, else the level-1 defaults."""
    import slots
    import snapshot
    value = os.environ.get("CLAUDE_CONFIG_DIR", "")
    config_dir = Path(value) if value else Path.home() / ".claude"
    stored = snapshot.read_snapshot(config_dir, session)
    parsed = slots.from_snapshot(stored["slots"]) if stored is not None else None
    if parsed is not None:
        return [(row.path, row.target) for row in parsed[0] if row.filled and row.kind == "agent"]
    with open(os.path.join(CORE_ROOT, "rigger-defaults.json"), "rb") as handle:
        defaults = json.loads(handle.read().decode("utf-8"))["slots"]
    return [(path, entry.get("target")) for path, entry in slots.flatten(defaults).items()
            if isinstance(entry, dict) and entry.get("kind") == "agent"]


def contract(event):
    """(slot, contract) of one SubagentStop event; the imports live here, so a missing tool costs only this field."""
    for folder in ("slot-check", "assemble"):
        path = os.path.join(TOOLS, folder)
        if path not in sys.path:
            sys.path.insert(0, path)
    import slot_check
    agent_type = _text(event.get("agent_type"))
    named = [path for path, target in _agent_slots(_text(event.get("session_id"))) if target == agent_type]
    if len(named) != 1:   # no slot, or two slots name this agent: no verdict is claimed for an ambiguous row
        return "", "none"
    slot = named[0]
    message = event.get("last_assistant_message")
    if not isinstance(message, str):   # the event text only, never the transcript (spike Q2)
        return slot, "unread"
    verdict = slot_check.check(slot, message, shape_only=True)
    return slot, "ok" if verdict.ok else "refused:" + verdict.rule   # the rule id only: a reason may quote the report


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
        row["verdict"], row["status"], row["source"], row["has_summary"] = judge(event)
    except Exception:
        row.update(verdict="error", status="", source="none", has_summary=False)
    try:   # a missing or broken slot tool, defaults file or snapshot leaves the fields above as they are
        row["slot"], row["contract"] = contract(event)
    except Exception:
        row.update(slot="", contract="error")
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
    """The row's agent_type, verdict, has_summary (an absent or non-bool field reads False) and contract (None when
    absent); ValueError, OSError or UnicodeDecodeError when the file is not a row."""
    with open(path, "rb") as handle:
        row = json.loads(handle.read().decode("utf-8").splitlines()[0])
    if not isinstance(row, dict) or not isinstance(row.get("agent_type"), str) or row.get("verdict") not in VERDICTS:
        raise ValueError("not a row")
    return row["agent_type"], row["verdict"], row.get("has_summary") is True, row.get("contract")


def summary(data):
    """Prints one line per agent_type and the totals. Exit code 1 when no row could be read."""
    folder = os.path.join(data, "report-gate", "rows")
    counts, unreadable = {}, 0
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if not name.endswith(".json"):
            continue
        try:
            agent_type, verdict, summarised, verdict_of_contract = _read_row(os.path.join(folder, name))
        except (ValueError, IndexError, OSError):   # UnicodeDecodeError is a ValueError
            unreadable += 1
            continue
        counts.setdefault(agent_type, dict.fromkeys(COLUMNS, 0))[verdict] += 1
        counts[agent_type]["summary"] += summarised
        for column, counted in CONTRACT_COUNTS:
            counts[agent_type][column] += counted(verdict_of_contract)
    for agent_type in sorted(counts):
        print((agent_type or "-") + "".join(" %s %d" % (key, counts[agent_type][key]) for key in COLUMNS))
    total = sum(per[verdict] for per in counts.values() for verdict in VERDICTS)
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
