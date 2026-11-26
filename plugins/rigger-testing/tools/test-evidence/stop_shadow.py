"""stop_shadow: one row when a turn closes claiming work that no check followed. Shadow only: it never holds a turn.

Runs as an async Stop hook, prints nothing, since output on Stop would continue the conversation, and exits 0. Each row
file is created with O_EXCL and holds one JSON line per gated repo. An empty one is a run cancelled or out of time:
`claude -p` cancels async hooks at exit. The gate lab joins rows to closings by `sid8` and `key12`.
"""
import json
import os
import re
import sys
import threading
import time

STOP_WAIT = 2.5     # seconds this process lives at most
ROW_SPARE = 0.2     # of those, what a transcript read out of time leaves to write its row
ROW_CAP = 20000     # about 5 MB
ROW_FIELDS = ("ts", "sid8", "key12", "repo8", "verdict", "reason", "claim", "code_changes", "evidence", "fingerprint",
              "msg_in_tail", "ms")
LEAD = 300
LAST_PARAGRAPH = 400
NEGATION_REACH = 24

CLAIM_EN = (
    r"done|completed?|finished|fixed|implemented|all (?:tests )?(?:pass(?:ed|ing)?|green)|tests? pass(?:ed|ing)?"
    r"|works now|ready (?:to|for)|merged|shipped"
)
CLAIM = re.compile(r"\b(?:" + CLAIM_EN + r")\b", re.I)
NEGATED = re.compile(r"\b(?:not|isn't|aren't|never|still not)\s+(?:\w+\s+)?$", re.I)
DISCLOSED = re.compile(
    r"\b(?:(?:not|never|didn't|did not|haven't|have not) (?:\w+ )?(?:run|tested|verified|checked)"
    r"|untested|unverified|to be (?:checked|tested|verified))\b",
    re.I,
)
STATUS = re.compile(
    r"\b(?:still (?:running|working|pending|in progress)|in progress|in the background|waiting (?:for|on))(?!\w)",
    re.I,
)


def claims_done(text):
    """The first claim word in text with no negation just before it, else None."""
    for match in CLAIM.finditer(text):
        if not NEGATED.search(text[max(0, match.start() - NEGATION_REACH) : match.start()]):
            return match.group(0)
    return None


def last_paragraph(message):
    return re.split(r"\n\s*\n", message.strip())[-1][:LAST_PARAGRAPH]


def closing_claim(message):
    """The claim word of the closing's lead or last paragraph: a word elsewhere rarely speaks of this change."""
    return claims_done(message.strip()[:LEAD]) or claims_done(last_paragraph(message))


def disclosed(message):
    """True when the closing says the check is still open, or ends on what is still running."""
    return bool(DISCLOSED.search(message) or STATUS.search(last_paragraph(message)))


def _repo_root(path, cache):
    """Nearest folder above path holding .git, a file in a linked worktree; None when there is none."""
    folder = os.path.dirname(path)
    while True:
        if folder not in cache:
            cache[folder] = os.path.exists(os.path.join(folder, ".git"))
        if cache[folder]:
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def _read_gates(root):
    """root's gates.json when it opts in to the Stop gate, None when it does not, False when malformed."""
    try:
        with open(os.path.join(root, ".claude", "gates.json"), "rb") as fh:
            gates = json.loads(fh.read())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    if not isinstance(gates, dict):
        return False
    if "scope_stop" not in gates:
        return None
    lists = (gates.get("checks"), gates.get("scope_stop"))
    return gates if all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists) else False


def _gated_repos(changes, reader):
    """(root, gates, in-scope [(time, file)]) of each repo the changes touched that opts in and has a file in scope.
    Spellings of one root count as one. A malformed gates.json has no scope: its repo stays, with all its files."""
    repos, cache = {}, {}
    for stamp, path in changes:
        root = _repo_root(path, cache)
        if root is not None:
            repo = repos.setdefault(reader.norm(root), (root, _read_gates(root), []))
            repo[2].append((stamp, os.path.relpath(path, repo[0])))
    gated = []
    for root, gates, files in repos.values():
        if gates:
            files = [item for item in files if reader.scope_match(item[1], gates["scope_stop"])]
        if gates is False or (gates and files):
            gated.append((root, gates, files))
    return gated


def _msg_in_tail(stream, message, reader):
    """True when the stream's newest assistant text is the closing. Stop can fire before the transcript has it."""
    for line in reader.reversed_lines(stream):
        if b'"type":"assistant"' not in line or b'"type":"text"' not in line:
            continue
        try:
            content = json.loads(line)["message"]["content"]
            return [block["text"] for block in content if block.get("type") == "text"][-1] == message
        except (ValueError, LookupError, TypeError, AttributeError):
            continue
    return False


def _newest_in_scope(edits, root, gates, reader):
    """Time of the newest of the turn's edits, in any stream, that lies in root and matches scope_stop."""
    cache, wanted, stamps = {}, reader.norm(root), []
    for stamp, path in edits:
        folder = _repo_root(path, cache)   # norm(None) is "", never a root
        if reader.norm(folder) == wanted and reader.scope_match(os.path.relpath(path, folder), gates["scope_stop"]):
            stamps.append(stamp)
    return max(stamps)


def _judge(line, root, gates, files, edits, streams, reader, deadline):
    """(verdict, reason) of one repo, filling line's evidence on the way. edits is every stream's, None past the
    budget."""
    line["code_changes"] = len(files)
    if edits is None:
        return "unknown", "budget"
    last = _newest_in_scope(edits, root, gates, reader)
    line["fingerprint"] = reader.row_key(last)
    passed = reader.last_pass(streams, root, gates["checks"], deadline - time.monotonic() - ROW_SPARE)
    if passed.ts > last:
        line["evidence"] = "check"
        return "verified", None
    evidence = reader.evidence_after(streams, last, deadline - time.monotonic() - ROW_SPARE)
    if evidence == "unknown":
        return "unknown", "budget"
    line["evidence"] = evidence
    if evidence:
        return "verified", None
    if passed.hidden > last:
        return "unknown", "hidden"
    return ("flag" if passed.reason == "ok" else "unknown"), passed.reason


def _repo_line(row, repo, edits, streams, reader, deadline):
    root, gates, files = repo
    line = dict(row, repo8=reader.row_key(reader.norm(root))[:8])
    try:
        if gates is False:
            line.update(verdict="error", reason="gates")
        else:
            line["verdict"], line["reason"] = _judge(line, root, gates, files, edits, streams, reader, deadline)
    except OSError:
        line.update(verdict="unknown", reason="unreadable")
    except Exception as exc:
        line.update(verdict="error", reason=type(exc).__name__)
    return line


def _turn_lines(row, event, message, reader, deadline):
    """The row file's lines, None when no gated repo took part in the turn."""
    streams = reader.stream_paths(event.get("transcript_path"), event.get("agent_id"))
    if not streams:
        raise OSError("no transcript")
    start = reader.turn_start_ts(streams[0])
    repos = _gated_repos(reader.turn_changes(streams[:1], start), reader)
    if not repos:
        return None
    if event.get("background_tasks"):
        return [dict(row, verdict="deferred")]
    if disclosed(message):
        return [dict(row, verdict="disclosed")]
    row["msg_in_tail"] = _msg_in_tail(streams[0], message, reader)
    edits = reader.turn_edits(streams, start, deadline - time.monotonic() - ROW_SPARE)
    return [_repo_line(row, repo, edits, streams, reader, deadline) for repo in repos]


def _write(fd, lines, started):
    for line in lines:
        line["ms"] = int((time.monotonic() - started) * 1000)
    os.write(fd, "".join(json.dumps(line) + "\n" for line in lines).encode("ascii"))
    os.close(fd)


def _drop(fd, path, row, started):
    """Takes the row file back. Windows refuses while anyone holds it open, and `ungated` keeps what stays from
    reading as a cancelled run."""
    os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        fd = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        _write(fd, [dict(row, verdict="ungated")], started)


def log_stop(event, deadline):
    """Claims and writes the row of the turn in event. deadline is a time.monotonic() value. Up to the claim anything
    that goes wrong ends silently, since there is no file yet to say so in."""
    if event.get("stop_hook_active"):
        return
    message = event.get("last_assistant_message")
    claim = closing_claim(message)
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if claim is None or not data:
        return
    started = time.monotonic()
    sys.pycache_prefix = os.path.join(data, "pycache")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the launcher's -I drops the script folder
    import turn_evidence as reader
    rows = os.path.join(data, "turn-gate", "rows")
    os.makedirs(rows, exist_ok=True)
    if len(os.listdir(rows)) >= ROW_CAP:
        return
    session = str(event.get("session_id") or "")
    key12 = reader.row_key(session, str(event.get("prompt_id") or ""), message)
    path = os.path.join(rows, key12 + ".json")
    # FileExistsError here means another Stop has this closing: the worker ends on it.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    row = dict.fromkeys(ROW_FIELDS)
    row.update(ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), sid8=session[:8], key12=key12, claim=claim)
    try:
        lines = _turn_lines(row, event, message, reader, deadline)
    except OSError:
        lines = [dict(row, verdict="unknown", reason="unreadable")]
    except Exception as exc:
        lines = [dict(row, verdict="error", reason=type(exc).__name__)]
    if lines is None:
        _drop(fd, path, row, started)
    else:
        _write(fd, lines, started)


def main():
    """A hung read cannot hold the process: the wait is bounded and os._exit ends it with the worker still blocked."""
    deadline = time.monotonic() + STOP_WAIT

    def work():
        try:
            log_stop(json.loads(sys.stdin.buffer.read().decode("utf-8", "replace")), deadline)
        except Exception:   # no traceback on stderr
            pass

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(STOP_WAIT)
    os._exit(0)


if __name__ == "__main__":
    main()
