"""Check the provenance fields of lessons inbox entries.

python inbox_lint.py INBOX
    An entry is a line starting `- YYYY-MM-DD`; a line starting `YYYY-MM-DD · ` without the `- ` is a problem,
    because the skill and this lint would never see it. One that carries any of the fields signal_type, source_ref,
    target, why or review (` · key: value`, after the proposed rule) must have a known signal_type and a
    non-empty source_ref, target and why; review is optional but must be a real YYYY-MM-DD date. An entry
    with none of the fields is an old entry: counted, never failed. curated (see --slice) is outside these
    fields: it must be a real date on any entry.
    Prints one `INBOX:line: problem` per problem and a summary line.
    Exit 0: at least one entry and no problem. Exit 1: any problem, or no entry. Exit 2: INBOX is unreadable.

python inbox_lint.py --slice INBOX
    Prints what one curation run reviews, up to 15 entries as `line: entry`, then a summary line: entries whose
    `review:` date is today ($HARNESS_LESSONS_TODAY overrides, for tests) or past, then the oldest entries with
    evidence (every signal_type known and none agent-observation), then the oldest of the rest.
    An entry whose ` · curated: YYYY-MM-DD` field is a real date was left in the inbox by an earlier run: it
    comes after all others, longest parked first, so rejected or deferred entries never block the next slice;
    only a `review:` date later than that curated date still ranks it first.
    Exit 0: at least one entry. Exit 1: no entry. Exit 2: INBOX is unreadable.
"""

import datetime
import os
import re
import sys

SIGNAL_TYPES = ("user-correction", "test-failure", "review-finding", "agent-observation")
REQUIRED = ("signal_type", "source_ref", "target", "why")
FIELDS = REQUIRED + ("review",)
ENTRY = re.compile(r"^- [0-9]{4}-[0-9]{2}-[0-9]{2}")
DASHLESS = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} · ")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
FIELD = re.compile(r"^(%s):(.*)$" % "|".join(FIELDS), re.S)
SEPARATOR = " · "
CURATED = "curated:"
SLICE_SIZE = 15


def entry_fields(line):
    """{field: [values]} of one entry line, from the parts after the separator."""
    found = {}
    for part in line.split(SEPARATOR)[1:]:
        match = FIELD.match(part)
        if match:
            found.setdefault(match.group(1), []).append(match.group(2).strip())
    return found


def curated_value(line):
    """The value of the entry's curated field, None without one."""
    for part in line.split(SEPARATOR)[1:]:
        if part.startswith(CURATED):
            return part[len(CURATED):].strip()
    return None


def real_date(value):
    if not DATE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parked_date(line):
    """The entry's curated date when it is a real date, else ""."""
    curated = curated_value(line)
    return curated if curated is not None and real_date(curated) else ""


def entry_problems(fields):
    problems = []
    for name in REQUIRED:
        values = fields.get(name, [])
        if not values or not all(values):
            problems.append("%s is missing or empty" % name)
    for value in fields.get("signal_type", []):
        if value and value not in SIGNAL_TYPES:
            problems.append("signal_type must be one of %s, got %r" % (", ".join(SIGNAL_TYPES), value))
    for value in fields.get("review", []):
        if not real_date(value):
            problems.append("review is not a YYYY-MM-DD date: %r" % value)
    return problems


def lint(data):
    """(entries, entries with fields, [(line number, problem)]) of the inbox bytes."""
    entries = with_fields = 0
    problems = []
    for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
        if DASHLESS.match(line):
            problems.append((number, "entry without `- `"))
        if not ENTRY.match(line):
            continue
        entries += 1
        curated = curated_value(line)
        if curated is not None and not real_date(curated):
            problems.append((number, "curated is not a YYYY-MM-DD date: %r" % curated))
        fields = entry_fields(line)
        if not fields:
            continue
        with_fields += 1
        problems += [(number, problem) for problem in entry_problems(fields)]
    return entries, with_fields, problems


def has_evidence(fields):
    values = fields.get("signal_type", [])
    return bool(values) and all(value in SIGNAL_TYPES and value != "agent-observation" for value in values)


def review_order(data, today):
    """[(not past its review date, group, entry or curated date, line number, entry)] sorted: oldest first in each
    group, 0 evidence, 1 other, 2 curated."""
    order = []
    for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
        line = line.rstrip("\r")
        if not ENTRY.match(line):
            continue
        fields = entry_fields(line)
        parked = parked_date(line)
        due = any(parked < value <= today for value in fields.get("review", []))
        if parked:
            order.append((not due, 2, parked, number, line))
        else:
            order.append((not due, 0 if has_evidence(fields) else 1, line[2:12], number, line))
    return sorted(order)


def print_slice(data, inbox):
    order = review_order(data, os.environ.get("HARNESS_LESSONS_TODAY", "") or datetime.date.today().isoformat())
    if not order:
        print("# no entries in %s" % inbox)
        return 1
    for _, _, _, number, line in order[:SLICE_SIZE]:
        print("%d: %s" % (number, line))
    groups = [item[1] for item in order]
    print("# slice: %d of %d entries, %d with evidence, %d without, %d curated before"
          % (min(len(order), SLICE_SIZE), len(order), groups.count(0), groups.count(1), groups.count(2)))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    show_slice = argv[:1] == ["--slice"]
    paths = argv[1:] if show_slice else argv
    if len(paths) != 1:
        print("usage: inbox_lint.py [--slice] INBOX")
        return 2
    try:
        with open(paths[0], "rb") as handle:
            data = handle.read()
    except OSError as exc:
        print("# cannot read %s (%s: %s)" % (paths[0], type(exc).__name__, exc))
        return 2
    if show_slice:
        return print_slice(data, paths[0])
    entries, with_fields, problems = lint(data)
    for number, problem in problems:
        print("%s:%d: %s" % (paths[0], number, problem))
    if not entries:
        print("# no entries in %s" % paths[0])
        return 1
    print("# %d entries checked, %d with fields, %d problems" % (entries, with_fields, len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
