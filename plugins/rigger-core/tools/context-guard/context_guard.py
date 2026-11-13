"""UserPromptSubmit hook: once the main session's context passes a threshold, remind the session to
keep its state file current, so that compaction (see compact_guard.py) can run at a safe point.

Reads the event JSON from stdin, takes the last main-chain assistant usage from the end of the
transcript and prints `additionalContext` when its prompt size is at or over the threshold. Prints
nothing otherwise, and nothing on any error: it is a reminder, not a guard.

Also the shared transcript reader and state-file path for compact_guard.py.

Usage: python -I -S context_guard.py [--threshold TOKENS]   (default 200000)
"""
import json
import os
import re
import sys

DEFAULT_THRESHOLD = 200_000
CHUNK = 1 << 20          # bytes read per step, from the end of the transcript
MAX_SCAN = 16 << 20      # stop looking after this many bytes
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
FALLBACK_NAME = "<CLAUDE_CODE_SESSION_ID>.md"
REMINDER = ("Context is at {k}k tokens. Keep this session's state file {path} current: rewrite it at the next "
            "task boundary with safe_to_compact: true, and keep working; compaction runs automatically at a "
            "safe point and the file is re-injected afterwards.")


def config_root():
    """Claude Code's config folder: `$CLAUDE_CONFIG_DIR`, else `~/.claude`."""
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude"))


def default_state_dir():
    return os.path.join(config_root(), "state")


def fallback_state_path():
    """The state file's path with the session id left as a placeholder (no usable session id)."""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return os.path.join(default_state_dir(), FALLBACK_NAME)
    return "~/.claude/state/" + FALLBACK_NAME


def state_path(state_dir, session_id):
    """`<state_dir>/<session_id>.md`, or None when the id is not a plain session id."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    return os.path.join(state_dir, session_id + ".md")


def _num(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def prompt_tokens(usage):
    return (_num(usage.get("input_tokens")) + _num(usage.get("cache_read_input_tokens"))
            + _num(usage.get("cache_creation_input_tokens")))


def _entry_of(line):
    """A main-chain assistant line as {tokens, timestamp, tool_use, stop_reason}, or None.
    `tokens` is the prompt size; a compaction boundary counts as 0 tokens (the context before it is
    gone, and the next request has not measured the new one yet). `tool_use`: the line holds a
    tool_use block. Lines Claude Code writes itself after an interrupt or an API error (`<synthetic>`,
    usage all zero) are not requests."""
    if b'"usage"' not in line and b'compact_boundary' not in line:
        return None
    try:
        entry = json.loads(line.decode("utf-8", "replace"))
    except ValueError:   # a line still being written, or cut by the chunk edge
        return None
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return None
    stamp = entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None
    if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
        return {"tokens": 0, "timestamp": stamp, "tool_use": False, "stop_reason": None}
    message = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(message, dict) or entry.get("isApiErrorMessage") \
            or message.get("model") == "<synthetic>":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    content = message.get("content")
    tool_use = isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" for block in content)
    return {"tokens": prompt_tokens(usage), "timestamp": stamp, "tool_use": tool_use,
            "stop_reason": message.get("stop_reason")}


def last_context(path):
    """Prompt tokens of the last main-chain assistant request in the transcript, or None."""
    entry = last_entry(path)
    return None if entry is None else entry["tokens"]


def last_entry(path):
    """The last main-chain assistant entry of the transcript (see _entry_of), or None."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        tail = b""
        scanned = 0
        while end > 0 and scanned < MAX_SCAN:
            start = max(0, end - CHUNK)
            fh.seek(start)
            tail = fh.read(end - start) + tail
            scanned += end - start
            end = start
            lines = tail.split(b"\n")
            # the first piece may be the end of a longer line; keep it for the next step
            complete, tail = (lines, b"") if start == 0 else (lines[1:], lines[0])
            for line in reversed(complete):
                entry = _entry_of(line)
                if entry is not None:
                    return entry
    return None


def main(argv):
    threshold = DEFAULT_THRESHOLD
    if "--threshold" in argv:
        try:
            threshold = int(argv[argv.index("--threshold") + 1])
        except (IndexError, ValueError):
            pass
        if threshold <= 0:
            threshold = DEFAULT_THRESHOLD
    event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    path = event.get("transcript_path") if isinstance(event, dict) else None
    if not isinstance(path, str) or not path:
        return 0
    tokens = last_context(path)
    if tokens is None or tokens < threshold:
        return 0
    state = state_path(default_state_dir(), event.get("session_id")) or fallback_state_path()
    text = REMINDER.format(k=tokens // 1000, path=state)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                        "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
