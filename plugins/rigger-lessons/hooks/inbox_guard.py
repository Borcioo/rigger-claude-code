"""PreToolUse hook on Edit and Write: refuses an entry added to the lessons inbox without its fields.

hooks.json starts it only for a path ending `lessons-inbox.md`, so an edit of any other file spawns nothing; an
inbox override with another file name is not guarded. An added entry with no fields is refused too: only
inbox_lint.py's old entries, already in the file, are exempt. An Edit is applied to the file text and whole lines
are compared: each entry line it removes excuses one added line with problems, so an entry stays unchecked when
it is changed in one Edit, and one removed by an Edit and added back by another is new. A Write names no removed
text: every entry line the file does not hold counts as added, so a Write stays refused. An Edit whose old_string
is not in the file changes nothing and is allowed. Every internal error allows: this hook must never block edits.
"""

import json
import os
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path[:0] = [str(HOOKS), str(HOOKS.parent / "tools")]

import inbox_lint  # noqa: E402
import lessons_start  # noqa: E402

FORMAT = (
    "`- YYYY-MM-DD · scope · project · what happened · proposed rule · signal_type: <type> · source_ref: <ref> · "
    "target: <rules file or skill> · why: <reason>`"
)
NAMED_ENTRIES = 3
SHOWN_CHARS = 100


def is_inbox(path, inbox):
    try:
        return os.path.samefile(path, inbox)
    except OSError:
        return os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(inbox))


def entry_lines(text):
    lines = text.lstrip(chr(0xFEFF)).split("\n")
    return [line.rstrip("\r") for line in lines if inbox_lint.ENTRY.match(line) or inbox_lint.DASHLESS.match(line)]


def added_entries(before, after):
    """(entry lines of `after` that `before` lacks, how many entry lines of `before` are gone from `after`)."""
    lines = entry_lines(after)
    known = set(entry_lines(before))
    return [line for line in lines if line not in known], len(known - set(lines))


def read_inbox(inbox):
    if not os.path.lexists(inbox):
        return ""
    with open(inbox, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def refusal(entries, changed):
    """The deny reason of the added entries with problems, after `changed` of them; None when none is left."""
    bad = []
    for line in entries:
        problems = inbox_lint.entry_problems(inbox_lint.entry_fields(line))
        if inbox_lint.DASHLESS.match(line):
            problems.insert(0, "entry without `- `")
        if problems:
            bad.append("%s: %s" % (line[:SHOWN_CHARS], "; ".join(problems)))
    bad = bad[changed:]
    if not bad:
        return None
    shown = bad[:NAMED_ENTRIES]
    if len(bad) > NAMED_ENTRIES:
        shown.append("and %d more" % (len(bad) - NAMED_ENTRIES))
    return ("Lessons inbox: a new entry needs its fields.\n%s\nFormat: %s\nsignal_type is one of %s.\n"
            "An entry already in the file stays unchecked when one Edit changes it; a Write that changes it stays refused." % (
                "\n".join(shown), FORMAT, ", ".join(inbox_lint.SIGNAL_TYPES)))


def check(event):
    """The deny reason for this tool call, None to allow."""
    tool_input = event["tool_input"]
    environ = os.environ
    inbox = lessons_start.inbox_path(environ, lessons_start.config_root(environ))
    if not is_inbox(tool_input["file_path"], inbox):
        return None
    held = read_inbox(inbox)
    if event["tool_name"] == "Write":
        return refusal(added_entries(held, tool_input["content"])[0], 0)
    text, old, new = (value.replace("\r\n", "\n") for value in (held, tool_input["old_string"], tool_input["new_string"]))
    return refusal(*added_entries(text, text.replace(old, new)))


def main():
    try:
        reason = check(json.loads(sys.stdin.buffer.read().decode("utf-8")))
        if reason:
            output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}
            sys.stdout.buffer.write(json.dumps(output).encode("utf-8"))
            sys.stdout.flush()
    except Exception:  # an edit is never blocked by this hook's own failure
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
