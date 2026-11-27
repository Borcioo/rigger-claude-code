"""PreToolUse hook on the app's `wrap_up` tool: a main session that ends its turn has to decide about
each side session it started whose result has arrived and that is still open.

The main session's own transcript says which. A side session counts as left open when its latest
"finished a turn" note is newer than the last `SendMessage` to it, the last `archive_session` of it and
this hook's last denial naming it; a session with an `archive_session` call is never open, whatever note
follows (the note answers the turn it was archived in). Each open session not listed in the state file section
`## Side sessions kept` as `<id>: <reason>` makes the hook deny the wrap_up, naming the sessions and
the two ways out: archive it, or add it to that section with the reason. A denial names a result once;
a newer note arms it again.

Also the Stop hook, for a turn that ends without wrap_up: the same decision as a block, and a block counts
as a denial. A stop_hook_active that is not false allows, so a block never repeats in a row. Before that,
turn_guard.py holds the turn while the session's own gate or test command runs in the background (D89),
with its own per-task cap; when it blocks, the transcript is not read.

Also PreToolUse on `AskUserQuestion`: while the state file frontmatter has a top-level `plan:` not in NO_PLAN and no `stop:`
from STOPS, that call and a `wrap_up` with a non-empty `ask` are denied, before the transcript is read.

State file: `<config dir>/state/<session id>.md`, as in compact_guard.py. Subagent events are ignored.
Fails open: on any error, a missing transcript or an unreadable state file the hook prints nothing.
It reads at most the last MAX_SCAN bytes of the transcript and calls no tool. From CACHE_FROM bytes up,
`<session id>.side-guard.json` beside the state file holds the tracker and the offset reached, so an event
reads only what the transcript gained. It only saves reading: a cache that does not fit is rebuilt.

Usage: python -I -S side_session_guard.py   (event JSON on stdin)
"""
import json
import os
import re
import sys

MAX_SCAN = 64 << 20      # bytes read from the end of the transcript
CACHE_FROM = 1 << 20     # below this a full read costs less than the cache; `claude -p` runs leave no file
CACHE_SUFFIX = ".side-guard.json"
PREFIX = "side-session-guard:"
PLAN_PREFIX = "plan-guard:"   # not PREFIX: a denial with PREFIX settles the side sessions its text names
NO_PLAN = frozenset(("", "null", "~", "false"))   # `plan:` values that mean no active plan
STOPS = frozenset(("main", "release", "force-push", "prod-data", "secrets", "goal-unreachable", "app-refused", "done"))
KEPT_HEADING = "side sessions kept"
TITLE_MAX = 60
NOTE = re.compile(r'The session "(.*?)" you started \((local_[\w-]+)\) finished a turn')
SESSION = re.compile(r"local_[\w-]+")
HEADING = re.compile(r"#{1,6}[ \t]+(.*)")
KEPT_LINE = re.compile(r"\s*(?:[-*][ \t]+)?`?(local_[\w-]+)`?[ \t]*:[ \t]*\S")
NEEDLES = (b"finished a turn", b"archive_session", b"SendMessage", PREFIX.encode("ascii"))   # a line without one is skipped unparsed
HERE = os.path.dirname(os.path.abspath(__file__))
CONTEXT_GUARD = os.path.join(os.path.dirname(HERE), "context-guard")


def _blocks(content):
    """The content blocks of a message or an attachment prompt; a plain string is one text block."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _text(content):
    return "\n".join(block["text"] for block in _blocks(content) if isinstance(block.get("text"), str))


class Tracker:
    """Feed the transcript lines in order; `pending()` then lists the side sessions left open."""

    def __init__(self):
        self.clock = 0
        self.noted = {}     # session id -> (clock of its latest note, title)
        self.settled = {}   # session id -> clock of its latest SendMessage, archive_session or denial
        self.archived = set()   # session ids with an archive_session call: final, whatever note follows

    def _settle(self, session):
        if isinstance(session, str):
            self.clock += 1
            self.settled[session] = self.clock

    def _notes(self, text):
        if isinstance(text, str) and text.lstrip().startswith("<system-reminder>"):
            for title, session in NOTE.findall(text):
                self.clock += 1
                self.noted[session] = (self.clock, title)

    def feed(self, line):
        if not any(needle in line for needle in NEEDLES):
            return
        try:
            entry = json.loads(line.decode("utf-8", "replace"))
        except ValueError:   # a line still being written, or cut by the start of the window
            return
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            return
        kind = entry.get("type")
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if kind == "assistant":
            for block in _blocks(message.get("content")):
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                name = block.get("name")
                if name == "SendMessage":
                    self._settle(args.get("to"))
                elif isinstance(name, str) and name.endswith("archive_session"):
                    self._settle(args.get("session_id"))
                    if isinstance(args.get("session_id"), str):
                        self.archived.add(args["session_id"])
        elif kind == "user":
            for block in _blocks(message.get("content")):
                if block.get("type") == "text":
                    self._notes(block.get("text"))
                elif block.get("type") == "tool_result" and block.get("is_error") is True:
                    text = _text(block.get("content"))
                    if PREFIX in text:
                        for session in SESSION.findall(text):
                            self._settle(session)
        elif kind == "attachment" and isinstance(entry.get("attachment"), dict):
            attachment = entry["attachment"]
            for block in _blocks(attachment.get("prompt")):
                if block.get("type") == "text":
                    self._notes(block.get("text"))
            error = attachment.get("blockingError")
            text = error.get("blockingError") if isinstance(error, dict) else None
            if attachment.get("type") == "hook_blocking_error" and isinstance(text, str) and text.startswith(PREFIX):
                for session in SESSION.findall(text):
                    self._settle(session)

    def pending(self):
        """(session id, title) of each open session, oldest note first."""
        found = [(clock, session, title) for session, (clock, title) in self.noted.items()
                 if clock > self.settled.get(session, 0) and session not in self.archived]
        return [(session, title) for _, session, title in sorted(found)]


def _fits(saved, transcript, size):
    return (isinstance(saved, dict) and saved.get("path") == transcript and isinstance(saved.get("offset"), int)
            and 0 <= saved["offset"] <= size and isinstance(saved.get("clock"), int)
            and isinstance(saved.get("noted"), dict) and isinstance(saved.get("settled"), dict)
            and isinstance(saved.get("archived"), list)
            and all(isinstance(item, list) and len(item) == 2 and isinstance(item[0], int) and isinstance(item[1], str)
                    for item in saved["noted"].values())
            and all(isinstance(item, int) for item in saved["settled"].values())
            and all(isinstance(item, str) for item in saved["archived"]))


def restore(cache, transcript, size):
    """(tracker, offset to read from): the cached ones, or a new tracker and 0. MAX_SCAN bounds the read."""
    tracker, offset = Tracker(), 0
    try:
        with open(cache, "rb") as fh:
            saved = json.loads(fh.read())
    except (OSError, ValueError):
        saved = None
    if _fits(saved, transcript, size):
        offset = saved["offset"]
        tracker.clock, tracker.settled = saved["clock"], saved["settled"]
        tracker.noted = {session: tuple(item) for session, item in saved["noted"].items()}
        tracker.archived = set(saved["archived"])
    return tracker, max(offset, size - MAX_SCAN)


def save(cache, transcript, tracker, offset):
    data = {"path": transcript, "offset": offset, "clock": tracker.clock, "noted": tracker.noted,
            "settled": tracker.settled, "archived": sorted(tracker.archived)}
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        temporary = "%s.%d" % (cache, os.getpid())
        with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        os.replace(temporary, cache)
    except OSError:   # the next event reads more, and decides the same
        pass


def scan(path, tracker, offset):
    """Feed the complete lines from `offset` on and return the offset after the last one: a line still being
    written is read whole next time. A line the MAX_SCAN window cuts does not parse."""
    with open(path, "rb") as fh:
        fh.seek(offset)
        for line in fh:
            if not line.endswith(b"\n"):
                break
            tracker.feed(line)
            offset += len(line)
    return offset


def kept_sessions(path):
    """Ids listed under `## Side sessions kept` with a reason: a set, empty when the file does not exist,
    None when it cannot be read."""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig")
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeDecodeError):
        return None
    kept, inside = set(), False
    for line in text.splitlines():
        heading = HEADING.fullmatch(line)
        if heading:
            inside = heading.group(1).strip().rstrip(":").lower() == KEPT_HEADING
        elif inside:
            found = KEPT_LINE.match(line)
            if found:
                kept.add(found.group(1))
    return kept


def deny_reason(open_sessions, state):
    names = "; ".join('%s ("%s")' % (session, title[:TITLE_MAX]) for session, title in open_sessions)
    return ("%s you started side sessions whose result arrived and that are still open: %s. Decide now: "
            "archive each with archive_session, or keep it by adding a line `<session id>: <why it "
            "stays open>` under `## Side sessions kept` in %s. This check does not repeat for the same result."
            % (PREFIX, names, state))


def frontmatter(path):
    """`key: value` pairs between the state file's opening `---` and the next one, surrounding quotes
    stripped, case kept; {} for None, no frontmatter or any error."""
    try:
        with open(path, "rb") as fh:
            lines = fh.read().decode("utf-8-sig").splitlines()
    except (OSError, TypeError, UnicodeDecodeError):
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    pairs = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return pairs
        if line[:1] in (" ", "\t"):
            continue   # a nested value, not a top-level key
        key, colon, value = line.partition(":")
        value = value.strip()
        if colon and len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if colon:
            pairs[key.strip()] = value
    return {}   # no closing `---`: no frontmatter


def plan_deny_reason(state):
    return ("%s the plan is active, so do not ask the user: decide, continue and list the decision in the final "
            "summary. Only if this is `main` (default branch), a release, a force-push, live data, secrets, an unreachable goal, "
            "an app refusal only the user can click through, or the final summary, set `stop: <%s>` in the "
            "frontmatter of %s and retry; remove it after the user answers."
            % (PLAN_PREFIX, "|".join(sorted(STOPS)), state))


def _asks(event):
    """True for a call that puts a question to the user (a Stop event names no tool)."""
    if event.get("tool_name") == "AskUserQuestion":
        return True
    tool_input = event.get("tool_input")
    ask = tool_input.get("ask") if isinstance(tool_input, dict) else None
    return event.get("tool_name") == "mcp__ccd_turn__wrap_up" and (
        (isinstance(ask, str) and ask != "") or (isinstance(ask, dict) and any(ask.values()))
        or (isinstance(ask, list) and ask != []) or (isinstance(ask, (int, float)) and ask != 0))


def turn_end(event, state, guard):
    """turn_guard.check's (block reason, note) for a Stop event; (None, None) on any error."""
    try:
        sys.path.insert(0, HERE)
        import turn_guard
        if state is None:
            return None, None
        coordinator = frontmatter(state).get("coordinator", "").strip().lower() == "true"
        return turn_guard.check(event, state, coordinator, os.environ, guard.config_root())
    except Exception as error:   # fails open; the line lands in the debug log
        sys.stderr.write("turn-guard: check failed, the turn is not held: %s\n" % " ".join(repr(error).split()))
        return None, None


def main():
    sys.path.insert(0, CONTEXT_GUARD)
    import context_guard as guard
    # Fail open by raising: stdin that is not a JSON object raises here, and the caller's try allows.
    event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    stop = event.get("hook_event_name") == "Stop"
    if event.get("agent_id") is not None:
        return 0
    state = guard.state_path(guard.default_state_dir(), event.get("session_id"))
    note = None
    if stop:
        reason, note = turn_end(event, state, guard)
        if reason:
            block = {"decision": "block", "reason": reason}
            if note:
                block["systemMessage"] = note
            sys.stdout.write(json.dumps(block))
            return 0
    try:
        output = side_sessions(event, state, stop)
    except Exception:
        output = None
    if note:
        output = dict(output or {}, systemMessage=note)
    if output:
        sys.stdout.write(json.dumps(output))
    return 0


def side_sessions(event, state, stop):
    """The hook output of the side-session and plan checks, or None. Fails open by raising: a missing or
    non-string transcript path and a session id with no state file path (None) raise below."""
    if stop and event.get("stop_hook_active") is not False:
        return None
    if _asks(event):
        keys = frontmatter(state)
        if keys.get("plan", "") not in NO_PLAN and keys.get("stop") not in STOPS:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": plan_deny_reason(state)}}
    if event.get("tool_name") == "AskUserQuestion":
        return None
    cache = state[:-len(".md")] + CACHE_SUFFIX
    transcript = event.get("transcript_path")
    size = os.path.getsize(transcript)
    tracker, offset = restore(cache, transcript, size)
    offset = scan(transcript, tracker, offset)
    if size >= CACHE_FROM:
        save(cache, transcript, tracker, offset)
    open_sessions = tracker.pending()
    kept = kept_sessions(state) if open_sessions else None   # no open session: nothing to look up
    if kept is None:
        return None
    unkept = [item for item in open_sessions if item[0] not in kept]
    if unkept and stop:
        return {"decision": "block", "reason": deny_reason(unkept, state)}
    if unkept:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": deny_reason(unkept, state)}}
    return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
