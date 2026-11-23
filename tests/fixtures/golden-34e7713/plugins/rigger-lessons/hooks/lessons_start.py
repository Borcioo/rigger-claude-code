"""SessionStart hook of the lessons plugin: prints the inbox path and its entry count.

Inbox: $HARNESS_LESSONS_INBOX (a leading ~ is expanded), else lessons-inbox.md in the config dir
($CLAUDE_CONFIG_DIR, else ~/.claude); a missing inbox is created from templates/lessons-inbox.md. It reports
entries whose `review:` date is today ($HARNESS_LESSONS_TODAY overrides, for tests) or past, and starts a
background batch when one is due and the user turned batches on ($RIGGER_LESSONS_BATCH=1; tools/lessons_batch.py
maybe_start; it prints nothing). In every folder it names 5 or more proposals waiting in lessons-proposals.md next
to the inbox, and 3 or more batch runs failed in a row. Always exits 0.
"""

import datetime
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PLUGIN_ROOT / "tools"
TEMPLATE = PLUGIN_ROOT / "templates" / "lessons-inbox.md"
INBOX_NAME = "lessons-inbox.md"
QUEUE_NAME = "lessons-proposals.md"
PROPOSALS_WAITING = 5
FAILED_RUNS = 3
# Bytes, so an inbox that is not valid UTF-8 is still counted; [0-9] matches what grep counts.
ENTRY = re.compile(rb"^- [0-9]{4}-[0-9]{2}-[0-9]{2}", re.M)
# \xc2\xb7 is the UTF-8 field separator; prose that merely mentions `review:` lacks it.
DUE = re.compile(rb"^- [0-9]{4}-[0-9]{2}-[0-9]{2}\b.* \xc2\xb7 review: ([0-9]{4}-[0-9]{2}-[0-9]{2})", re.M)
PREFIX = "rigger: "

PROPOSALS_NOTICE = (
    "Lessons proposals waiting: {count} in {queue}; propose `/rigger-lessons:curate-lessons` in one line — only in "
    "a session the user drives; a session started by another session never proposes or runs curation."
)
FAILURE_NOTICE = "Lessons batch failed {n} runs in a row; last log: {path}."


def config_root(environ):
    value = environ.get("CLAUDE_CONFIG_DIR", "")
    return Path(value) if value else Path.home() / ".claude"


def inbox_path(environ, root):
    value = environ.get("HARNESS_LESSONS_INBOX", "")
    return Path(value).expanduser() if value else root / INBOX_NAME


def today(environ):
    return environ.get("HARNESS_LESSONS_TODAY", "") or datetime.date.today().isoformat()


def count_entries(inbox):
    """(count, None, bytes read), or (None, error text, None) when the inbox cannot be created or read."""
    try:
        if not os.path.lexists(inbox):
            inbox.parent.mkdir(parents=True, exist_ok=True)
            with open(TEMPLATE, encoding="utf-8", newline="") as handle:
                text = handle.read()
            with open(inbox, "x", encoding="utf-8", newline="") as handle:
                handle.write(text)
        with open(inbox, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc), None
    return len(ENTRY.findall(data)), None, data


def notices(environ, inbox):
    """Waiting proposals and repeated failed batch runs."""
    import batch_apply  # noqa: E402 (tools/ is on sys.path once build_output ran)
    import lessons_batch  # noqa: E402

    lines = []
    queue = inbox.parent / QUEUE_NAME
    try:
        with open(queue, encoding="utf-8", newline="") as handle:
            waiting = batch_apply.count_queued(handle.read())
    except (OSError, ValueError):
        waiting = 0
    if waiting >= PROPOSALS_WAITING:
        lines.append(PROPOSALS_NOTICE.format(count=waiting, queue=queue))
    state = lessons_batch.state_dir(environ)
    try:
        with open(state / "failures", encoding="utf-8") as handle:
            value = handle.read().strip()
    except (OSError, ValueError):
        value = ""
    failures = int(value) if re.fullmatch(r"[0-9]+", value) else 0
    if failures >= FAILED_RUNS:
        try:  # written by lessons_batch.py when a run fails; the newest log may be a run that found the lock taken
            with open(state / "failed-log", encoding="utf-8") as handle:
                last = handle.readline().strip()
        except (OSError, ValueError):
            last = ""
        lines.append(FAILURE_NOTICE.format(n=failures, path=last or state / "logs"))
    return lines


def build_output(environ):
    root = config_root(environ)
    inbox = inbox_path(environ, root)
    lines = []
    count, error, data = count_entries(inbox)
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    if count is None:
        lines.append("Lessons inbox: %s: count unknown (%s)" % (inbox, error))
    else:
        lines.append("Lessons inbox: %s: %d entries" % (inbox, count))
        due = [day for day in DUE.findall(data) if day.decode("ascii") <= today(environ)]
        if due:
            lines.append("Lessons due for review: %d" % len(due))
        try:  # imported here: inbox_guard.py and skill_lessons.py import this module and need none of it
            import lessons_batch  # noqa: E402

            lessons_batch.maybe_start(environ, inbox, data)
        except Exception:  # the session keeps its lines; the next session start tries again
            pass
    try:  # the inbox line stays when tools/ cannot be imported or read
        lines.extend(notices(environ, inbox))
    except Exception:
        pass
    return "\n".join(lines) + "\n"


def main():
    try:
        text = build_output(os.environ)
    except Exception as exc:  # a SessionStart hook must never block the session
        text = "%slessons hook failed (%s: %s)\n" % (PREFIX, type(exc).__name__, exc)
    sys.stdout.buffer.write(text.encode("utf-8", "backslashreplace"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
