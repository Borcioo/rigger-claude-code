"""Wait without model tokens until the coordinator has something to act on, then print it.

Started by a `/rigger-core:coordinate` session with `run_in_background`; its exit wakes the
coordinator. Reads the desktop app's session files (`<app>/claude-code-sessions/<account>/<org>/
local_<id>.json`) and the side sessions' transcripts. Exit lines (stdout), exit 0 unless noted;
one exit prints a line for every held note and quiet session true at that moment:

  session-watch: held-note: ...   a side session's note the app has queued for 120 s or more
  session-watch: held-note: user-driven: ...   the same, for a turn the user started in that
                                  session's window
  session-watch: quiet: ...       a side session mid-turn with no transcript record for 300 s;
                                  not for a shell command running in bypassPermissions
  session-watch: cap: ...         1800 s passed
  session-watch: nothing to watch: ...
  session-watch: error: ...       exit 2: a file it cannot read or a shape it does not know

Each held note and quiet state is reported once (state file under --state-dir), so a restart
does not wake the coordinator again for the same item.
"""
import argparse
import datetime
import glob
import itertools
import json
import os
import sys
import tempfile
import time

POLL = 15
HELD_AFTER = 120
QUIET_AFTER = 300
CAP = 1800
SETTLE = 10
NO_TRANSCRIPT_AFTER = 120
UNKNOWN_AFTER = 60
BAD_FILE_POLLS = 3
CHUNK = 65536
TURN_END_STOPS = ("end_turn", "stop_sequence", "max_tokens")
BUSY_SYSTEM = ("compact_boundary", "api_error", "informational", "model_refusal_fallback")
CONVERSATION = ("user", "assistant", "system")
SHELL_TOOLS = ("Bash", "PowerShell")  # never ask in bypassPermissions; every other tool may
NOTE_AFTER_END = 10  # the app stamps a note within 5 s of the turn's last record


class Unreadable(Exception):
    """A file the watcher cannot read or whose shape it does not know; the message names it."""


def iter_back(path):
    """Conversation records of a transcript, newest first; an unterminated last line is skipped."""
    with open(path, "rb") as f:
        pos = f.seek(0, os.SEEK_END)
        carry = None
        while pos > 0:
            start = max(0, pos - CHUNK)
            f.seek(start)
            data = f.read(pos - start)
            if carry is None:
                parts = data.split(b"\n")
                parts.pop()  # the text after the last newline: empty, or a line still being written
            else:
                parts = (data + carry).split(b"\n")
            pos = start
            carry = parts.pop(0) if pos > 0 else None
            for raw in reversed(parts):
                record = parse_line(raw, path)
                if record is not None:
                    yield record
            if carry is None:
                carry = b""
        if carry:
            record = parse_line(carry, path)
            if record is not None:
                yield record


def parse_line(raw, path):
    if not raw.strip():
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Unreadable("corrupt transcript line (%s)" % path)
    if not isinstance(record, dict):
        raise Unreadable("transcript line is not a JSON object (%s)" % path)
    if record.get("type") not in CONVERSATION or record.get("isSidechain"):
        return None
    return record


def timestamp(record):
    try:
        return datetime.datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def blocks(record, kind):
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def prompt_text(record):
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in blocks(record, "text"))


def shape(record):
    """'system:<subtype>', 'assistant', 'result' (tool results), 'prompt', or None when unknown."""
    kind = record.get("type")
    if kind == "system":
        return "system:%s" % record.get("subtype")
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, (str, list)):
        return None
    if kind == "assistant":
        return "assistant"
    return "result" if blocks(record, "tool_result") else "prompt"


def classify(path, now):
    """(state, detail, newest timestamp) of a transcript: state is idle, busy, tool, unknown or empty."""
    records = iter_back(path)
    ahead = [r for r, _ in zip(records, range(2))]  # record timestamps are not always in order
    stamps = [timestamp(r) for r in ahead]
    if None in stamps:
        return ("unknown", "%s without a timestamp" % ahead[stamps.index(None)].get("type"), None)
    newest = max(stamps) if stamps else None
    results = set()
    pending = []
    first = None
    for record in itertools.chain(ahead, records):
        ts = timestamp(record)
        if ts is None:
            return ("unknown", "%s without a timestamp" % record.get("type"), newest)
        newest = max(newest, ts)
        kind = shape(record)
        if first is None:
            first = (record, kind)
            if kind is None:
                return ("unknown", "%s content" % record.get("type"), newest)
            if kind == "system:stop_hook_summary":
                prev = ahead[1] if len(ahead) == 2 else None
                if prev is not None and shape(prev) == "prompt":
                    return ("busy", "stop hook feedback", newest)
                return ("idle", "turn ended", newest)
            if kind == "system:local_command":
                return ("idle", "local command", newest)
            if kind.startswith("system:"):
                if kind[len("system:"):] in BUSY_SYSTEM:
                    return ("busy", kind, newest)
                return ("unknown", kind, newest)
            if kind == "prompt":
                if prompt_text(record).startswith("[Request interrupted by user"):
                    return ("idle", "interrupted", newest)
                return ("busy", "prompt", newest)
            if kind == "assistant" and not blocks(record, "tool_use"):
                stop = (record.get("message") or {}).get("stop_reason")
                if stop in TURN_END_STOPS and now - newest >= SETTLE:
                    return ("idle", "turn ended", newest)
                return ("busy", "assistant", newest)
        if kind == "result":
            if pending:
                break  # results before the last assistant message: its pending set is complete
            results.update(b.get("tool_use_id") for b in blocks(record, "tool_result"))
            continue
        if kind == "assistant":
            pending = [b for b in blocks(record, "tool_use") if b.get("id") not in results] + pending
            continue
        break  # a prompt or system record before the last assistant message ends that message
    if first is None:
        return ("empty", "no conversation record", newest)
    names = [b.get("name") or "a tool" for b in pending]
    if names:
        return ("tool", ", ".join(dict.fromkeys(names)), newest)
    return ("busy", "tool results", newest)


def turn_origin(path, at):
    """turnOrigin of the prompt that started the turn ending at `at` (epoch seconds), or None when unknown."""
    ended = False
    for record in iter_back(path):
        if ended:
            if record.get("type") == "user" and "turnOrigin" in record:
                return record["turnOrigin"]
            continue
        ts = timestamp(record)
        if ts is None or ts > at:
            continue
        kind = shape(record)
        if kind == "system:stop_hook_summary" or (
                kind == "assistant" and (record.get("message") or {}).get("stop_reason") in TURN_END_STOPS):
            if at - ts > NOTE_AFTER_END:
                return None
            ended = True
    return None


def sessions_dir_default(env):
    if sys.platform == "win32":
        base = env.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "Claude", "claude-code-sessions")


def subagent_write(transcript):
    folder = os.path.join(transcript[:-len(".jsonl")], "subagents")
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    times = []
    for name in names:
        try:
            times.append(os.stat(os.path.join(folder, name)).st_mtime)
        except OSError:
            pass
    return max(times) if times else None


def hhmmss(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%H:%M:%SZ")


class Watch:
    def __init__(self, host, sessions_dir, projects, state_path, clock):
        self.host = host
        self.sessions_dir = sessions_dir
        self.projects = projects
        self.state_path = state_path
        self.clock = clock
        self.bad = {}
        self.cache = {}
        self.unknown_since = {}
        self.reported = self.load_state()

    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"notes": [], "quiet": []}
        if not isinstance(data, dict):
            return {"notes": [], "quiet": []}
        return {k: list(data.get(k) or []) for k in ("notes", "quiet")}

    def remember(self, kind, keys):
        self.reported[kind].extend(keys)
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.reported, f)
        os.replace(tmp, self.state_path)

    def read_app_file(self, path):
        """The parsed object, or None while the file fails to parse fewer than BAD_FILE_POLLS times."""
        try:
            st = os.stat(path)
            key = (st.st_mtime_ns, st.st_size)
            if path in self.cache and self.cache[path][0] == key:
                return self.cache[path][1]
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            self.bad[path] = self.bad.get(path, 0) + 1
            if self.bad[path] >= BAD_FILE_POLLS:
                raise Unreadable("cannot parse %d times in a row (%s)" % (BAD_FILE_POLLS, path))
            return None
        self.bad.pop(path, None)
        if not isinstance(doc, dict):
            raise Unreadable("not a JSON object (%s)" % path)
        self.cache[path] = (key, doc)
        return doc

    def host_file(self):
        if not os.path.isdir(self.sessions_dir):
            raise Unreadable("no desktop app session folder (%s)" % self.sessions_dir)
        found = glob.glob(os.path.join(glob.escape(self.sessions_dir), "*", "*", glob.escape(self.host) + ".json"))
        if len(found) != 1:
            raise Unreadable("%d session files for %s (%s)" % (len(found), self.host, self.sessions_dir))
        return found[0]

    def poll(self):
        """One look at every file: the exit lines, none to keep waiting; raises Unreadable."""
        now = self.clock()
        host_path = self.host_file()
        host = self.read_app_file(host_path)
        if host is None:
            return [], []
        notes = self.queued(host, host_path)
        children = []
        for path in sorted(glob.glob(os.path.join(glob.escape(os.path.dirname(host_path)), "local_*.json"))):
            if path == host_path:
                continue
            doc = self.read_app_file(path)
            if doc is None:
                continue
            origin = doc.get("spawnedFrom")
            if not isinstance(origin, dict) or origin.get("sessionId") != self.host:
                continue
            if doc.get("isArchived") is True or origin.get("userFork") is True:
                continue
            children.append(doc)
        states = [(child, self.child_state(child, now)) for child in children]
        self.last_states = states
        by_id = {child.get("sessionId"): (child, state) for child, state in states}
        lines = []
        held = sorted(((at, sid, title) for sid, at, title in notes
                       if now * 1000 - at >= HELD_AFTER * 1000 and "%s@%d" % (sid, at) not in self.reported["notes"]))
        for at, sid, title in held:
            child, state = by_id.get(sid) or (None, None)
            who = "%s (%s) finished a turn at %s" % (self.name(child) if child else title, sid, hhmmss(at))
            if state and state[3] and turn_origin(state[3], at / 1000) == "human":
                lines.append("held-note: user-driven: %s the user drove in its window; the app delivers the "
                             "note with the user's next message here" % who)
            else:
                lines.append("held-note: %s; the app holds the note while other side sessions work" % who)
        quiet = []
        for child, (state, detail, newest, transcript) in states:
            if state not in ("busy", "tool") or newest is None:
                continue
            mode = child.get("permissionMode") or "unknown"
            if mode == "bypassPermissions" and all(name in SHELL_TOOLS for name in detail.split(", ")):
                continue  # nothing can ask there: the command is running
            last = max(newest, subagent_write(transcript) or newest)
            key = "%s@%s" % (child["sessionId"], newest)
            if now - last >= QUIET_AFTER and key not in self.reported["quiet"]:
                quiet.append(key)
                minutes = int((now - last) // 60)
                who = "%s (%s)" % (self.name(child), child["sessionId"])
                if state == "tool":
                    lines.append("quiet: %s mid-turn for %d min, waiting on %s (mode %s): possibly a permission "
                                 "prompt or a question in its window" % (who, minutes, detail, mode))
                else:
                    lines.append("quiet: %s mid-turn with no transcript record for %d min (mode %s)" % (
                        who, minutes, mode))
        if held:
            self.remember("notes", ["%s@%d" % (sid, at) for at, sid, _ in held])
        if quiet:
            self.remember("quiet", quiet)
        if not lines and not children:
            lines.append("nothing to watch: no open side session of %s" % self.host)
        return lines, children

    def queued(self, host, path):
        notes = host.get("queuedSideSessionNotes")
        if notes is None:
            return []
        if not isinstance(notes, list):
            raise Unreadable("queuedSideSessionNotes is not a list (%s)" % path)
        out = []
        for note in notes:
            if not isinstance(note, dict) or not isinstance(note.get("sessionId"), str) \
                    or type(note.get("at")) is not int:
                raise Unreadable("queuedSideSessionNotes holds a note without sessionId and a numeric at (%s)" % path)
            out.append((note["sessionId"], note["at"], str(note.get("title") or "")))
        return out

    @staticmethod
    def name(child):
        origin = child.get("spawnedFrom") or {}
        return origin.get("name") or child.get("title") or child.get("sessionId")

    def child_state(self, child, now):
        sid = child.get("sessionId")
        cli = child.get("cliSessionId")
        found = glob.glob(os.path.join(glob.escape(self.projects), "*", glob.escape(cli) + ".jsonl")) \
            if isinstance(cli, str) and cli else []
        if len(found) != 1:
            created = child.get("createdAt")
            if type(created) is not int:
                raise Unreadable("session file without a numeric createdAt (%s)" % sid)
            if now - created / 1000 >= NO_TRANSCRIPT_AFTER:
                raise Unreadable("no transcript for %s %d s after it started (%s)" % (
                    sid, NO_TRANSCRIPT_AFTER, os.path.join(self.projects, "*", "%s.jsonl" % cli)))
            return ("empty", "no transcript yet", None, None)
        state, detail, newest = classify(found[0], now)
        if state == "unknown":
            since = self.unknown_since.setdefault((sid, detail), now)
            if now - since >= UNKNOWN_AFTER:
                raise Unreadable("unknown transcript record %s for %d s (%s)" % (detail, UNKNOWN_AFTER, found[0]))
        else:
            self.unknown_since = {k: v for k, v in self.unknown_since.items() if k[0] != sid}
        return (state, detail, newest, found[0])

    def summary(self):
        labels = {"idle": "idle", "busy": "working", "empty": "starting"}
        parts = []
        for child, (state, detail, _, _) in getattr(self, "last_states", []):
            if state == "tool":
                label = "waiting on %s" % detail
            elif state == "unknown":
                label = "unknown %s" % detail
            else:
                label = labels[state]
            parts.append("%s (%s)" % (self.name(child), label))
        return ", ".join(parts) or "no state read"


def main(argv=None, clock=time.time, sleep=time.sleep, out=sys.stdout, env=os.environ):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-dir", dest="sessions_dir", help="the desktop app's claude-code-sessions folder")
    parser.add_argument("--state-dir", help="where reported items are kept")
    args = parser.parse_args(argv)

    def say(text, code):
        out.write("session-watch: %s\n" % text)
        out.flush()
        return code

    host = env.get("CLAUDE_CODE_HOST_SESSION_ID")
    if not host:
        return say("error: CLAUDE_CODE_HOST_SESSION_ID is not set", 2)
    config = env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    state_dir = args.state_dir or os.path.join(tempfile.gettempdir(), "rigger-session-watch")
    watch = Watch(host, args.sessions_dir or sessions_dir_default(env), os.path.join(config, "projects"),
                  os.path.join(state_dir, host + ".json"), clock)
    start = clock()
    while True:
        try:
            lines, _ = watch.poll()
        except Unreadable as e:
            return say("error: %s" % e, 2)
        for line in lines:
            say(line, 0)
        if lines:
            return 0
        if clock() - start >= CAP:
            return say("cap: %d min; %s" % (CAP // 60, watch.summary()), 0)
        sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
