"""PostToolUse hook on Skill: adds the inbox entries scoped `skill:<name>` to the context of the skill just loaded.

The name matches with or without a plugin prefix, in the tool call and in the scope. An entry with a real
` · curated:` date was rejected or deferred by a curation run and is left out. Prints nothing on no match,
and on any error: a missing inbox is never created here. Output is capped in emitted bytes, under Claude Code's
10,000-character hook limit, keeping whole entries, newest first. A skill typed as a slash command never calls the
Skill tool, so this hook does not run for it.
"""

import json
import os
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path[:0] = [str(HOOKS), str(HOOKS.parent / "tools")]

import inbox_lint  # noqa: E402
import lessons_start  # noqa: E402

MAX_OUTPUT_BYTES = 8000
SCOPE_PREFIX = "skill:"


def bare_name(name):
    return name.rpartition(":")[2]


def skill_entries(text, name):
    lines = []
    for line in text.split("\n"):
        parts = line.rstrip("\r").split(inbox_lint.SEPARATOR, 2)
        if (inbox_lint.ENTRY.match(line) and len(parts) > 1 and parts[1].startswith(SCOPE_PREFIX)
                and bare_name(parts[1][len(SCOPE_PREFIX):]) == name and not inbox_lint.parked_date(line)):
            lines.append(line.rstrip("\r"))
    return lines


def encoded(name, entries, older, inbox):
    """The bytes this hook prints for these entries, `older` being the matches left out."""
    lines = ["Inbox lessons for skill %s, not yet in its text:" % name] + entries
    if older:
        lines.append("and %d older in %s" % (older, inbox))
    output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(lines)}}
    return json.dumps(output, ensure_ascii=False).encode("utf-8", "backslashreplace")


def build_output(event, environ):
    name = bare_name(event["tool_input"]["skill"])
    inbox = lessons_start.inbox_path(environ, lessons_start.config_root(environ))
    with open(inbox, "rb") as handle:
        entries = skill_entries(handle.read().decode("utf-8", "replace"), name)
    if not entries:
        return b""
    kept = []
    for line in reversed(entries):
        older = len(entries) - len(kept) - 1
        if len(encoded(name, [line] + kept, older, inbox)) >= MAX_OUTPUT_BYTES:
            break
        kept.insert(0, line)
    return encoded(name, kept, len(entries) - len(kept), inbox)


def main():
    try:
        sys.stdout.buffer.write(build_output(json.loads(sys.stdin.buffer.read().decode("utf-8")), os.environ))
        sys.stdout.flush()
    except Exception:  # lessons are an extra: a failure here never touches the skill load
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
