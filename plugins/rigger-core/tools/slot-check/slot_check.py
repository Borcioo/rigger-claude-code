"""Checks a slot filler's reply against its slot's output contract (spec section 10.4 and 10.5).

  slot_check.py <slot> <file> [--repo DIR] [--base REV] [--head REV] [--shape-only]

prints `slot-check: ok <slot>` (`slot-check: ok reviewer <n> findings`) and exits 0, or prints
`slot-check: refused: <rule id>: <reason>` and exits 2. Rules run in the order of RULES; the first that
fails is the refusal, and any exception is the rule `internal`. `--shape-only` skips GIT_RULES; without it
the reviewer needs `--base` and `--head`, and the mapper `--head`. `--repo` defaults to the working directory.

A reviewer reply is a JSON object, or a text whose last fenced `json` block holds one. Unknown keys are
ignored. Git is found and run by review_scope (absolute PATH entries outside the repository, no GIT_*
variable), and only commit ids it resolved reach git: an anchor is read from the head commit, never from
the working tree. Reply text is split and walked in code; the only regexes on it are the fence opener,
the bracketed rule id and the inbox line.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "review-scope"))

import review_scope  # noqa: E402

RULES = {
    "reviewer": ("json", "contract", "status", "finding-shape", "severity", "path", "coverage-sum", "skip-reason",
                 "shortcuts", "base-head", "coverage-scope", "anchor"),
    "worker": ("status-line", "summary"),
    "implementer": ("status-line", "fields", "blocked-fields"),
    "mapper": ("line-shape", "words", "not-found-alone", "path-exists"),
    "shell-guard": ("reply-shape", "deny-reason"),
    "lessons": ("inbox-line",),
}  # type: Dict[str, Tuple[str, ...]]
GIT_RULES = frozenset(("base-head", "coverage-scope", "anchor", "path-exists"))

CONTRACT = "rigger.reviewer/1"
STATUSES = ("DONE", "PARTIAL", "BLOCKED", "NEEDS_CONTEXT")
SEVERITIES = ("Critical", "Important", "Minor")
FINDING_TEXT_KEYS = ("id", "file", "severity", "quote", "title")
IMPLEMENTER_FIELDS = ("FILES", "ROOT CAUSE", "GATE", "RED RUNS")
BLOCKED_FIELDS = ("QUESTION", "CALL", "OUTPUT", "ALTERNATIVE")
SUMMARY_BULLETS = (3, 5)
MAPPER_WORDS = 12
NOT_FOUND = "NOT FOUND:"
ANCHOR_SLACK = 3
TEXT_CAP = 1000000

FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
RULE_ID = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9._-]*)\]")
INBOX_LINE = re.compile(r"Lessons inbox: .+: (\d+ entries|count unknown \(.+\))")

_out = None  # the CLI's byte sink; None is sys.stdout.buffer


@dataclass
class Verdict:
    ok: bool
    rule: str
    reason: str
    findings: Optional[int]


class Refused(ValueError):
    pass


class _Context:
    def __init__(self, text, repo, base, head):
        self.text = text
        self.repo = repo
        self.base = base
        self.head = head
        self.obj = None  # the reviewer's parsed object
        self.base_id = None
        self.head_id = None
        self.lines = None


def normalise(text):
    # type: (str) -> str
    """Every run of whitespace as one space, no space at either end."""
    return " ".join(text.split())


def _lines(text):
    """Lines split at LF with a trailing CR dropped, so a CRLF reply reads as an LF one."""
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


# ---- the reviewer's JSON -------------------------------------------------------------------------

def _fence(line):
    """(char, length, info) of a fence line, else None."""
    match = FENCE.fullmatch(line)
    if match is None:
        return None
    fence, info = match.group(1), match.group(2)
    if fence[0] == "`" and "`" in info:
        return None
    return fence[0], len(fence), info.strip()


def reviewer_json(text):
    # type: (str) -> object
    """The parsed value of a text that is JSON, else of its last fenced `json` block; raises Refused."""
    if len(text) > TEXT_CAP:
        raise Refused("text over %d characters" % TEXT_CAP)
    if text.lstrip()[:1] in ("{", "["):
        try:
            return json.loads(text)
        except ValueError:
            pass
    blocks = []  # type: List[str]
    open_fence = None
    body = []  # type: List[str]
    for line in _lines(text):
        if open_fence is None:
            fence = _fence(line)
            if fence is not None:
                open_fence, body = fence, []
            continue
        fence = _fence(line)
        if fence is not None and fence[0] == open_fence[0] and fence[1] >= open_fence[1] and not fence[2]:
            if open_fence[2].split(" ")[0].lower() == "json":
                blocks.append("\n".join(body))
            open_fence = None
            continue
        body.append(line)
    if open_fence is not None and open_fence[2].split(" ")[0].lower() == "json":
        raise Refused("the last json block is not closed")
    if not blocks:
        raise Refused("no JSON object and no fenced json block")
    try:
        return json.loads(blocks[-1])
    except ValueError as exc:
        raise Refused("the last json block is not JSON: %s" % exc)


def _is_line(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def path_problem(path):
    # type: (str) -> Optional[str]
    """Why `path` is not a plain repository-relative path, or None; `..` is a whole segment, never a substring."""
    if path == "":
        return "empty"
    if path.startswith("/"):
        return "absolute"
    if ".." in path.split("/"):
        return "parent segment"
    if "\\" in path:
        return "backslash"
    if ":" in path:
        return "colon"
    return None


def rule_reviewer_json(ctx):
    try:
        ctx.obj = reviewer_json(ctx.text)
    except Refused as exc:
        return str(exc)
    if not isinstance(ctx.obj, dict):
        return "the JSON is not an object"
    return None


def rule_reviewer_contract(ctx):
    if ctx.obj.get("contract") != CONTRACT:
        return "contract is not %s" % CONTRACT
    return None


def rule_reviewer_status(ctx):
    status = ctx.obj.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        return "status is not one of %s" % ", ".join(STATUSES)
    return None


def rule_reviewer_finding_shape(ctx):
    findings = ctx.obj.get("findings")
    if not isinstance(findings, list):
        return "findings is not a list"
    seen = set()
    for index, row in enumerate(findings):
        where = "finding %d" % (index + 1)
        if not isinstance(row, dict):
            return "%s is not an object" % where
        for key in FINDING_TEXT_KEYS:
            if not isinstance(row.get(key), str):
                return "%s has no %s" % (where, key)
        if not _is_line(row.get("line")):
            return "%s: line is not a positive integer" % where
        if "end_line" in row and not (_is_line(row["end_line"]) and row["end_line"] >= row["line"]):
            return "%s: end_line is not an integer at or after line" % where
        if not normalise(row["quote"]):
            return "%s: quote is empty" % where
        if not row["id"] or row["id"] in seen:
            return "%s: id %r is empty or repeated" % (where, row["id"])
        seen.add(row["id"])
    return None


def rule_reviewer_severity(ctx):
    for row in ctx.obj["findings"]:
        if row["severity"] not in SEVERITIES:
            return "%s: severity %r is not one of %s" % (row["id"], row["severity"], ", ".join(SEVERITIES))
    return None


def rule_reviewer_path(ctx):
    for row in ctx.obj["findings"]:
        problem = path_problem(row["file"])
        if problem:
            return "%s: file %r: %s" % (row["id"], row["file"], problem)
    return None


def _coverage(obj):
    """(in_scope, reviewed, skipped files) of a well-shaped coverage, else raises Refused."""
    coverage = obj.get("coverage")
    if not isinstance(coverage, dict):
        raise Refused("coverage is not an object")
    lists = []
    for key in ("in_scope", "reviewed", "skipped"):
        value = coverage.get(key)
        if not isinstance(value, list):
            raise Refused("coverage.%s is not a list" % key)
        lists.append(value)
    in_scope, reviewed, skipped = lists
    for key, value in (("in_scope", in_scope), ("reviewed", reviewed)):
        if not all(isinstance(item, str) for item in value):
            raise Refused("coverage.%s holds a value that is not a path" % key)
    if not all(isinstance(item, dict) and isinstance(item.get("file"), str) for item in skipped):
        raise Refused("coverage.skipped holds an entry without a file")
    return in_scope, reviewed, [item["file"] for item in skipped]


def rule_reviewer_coverage_sum(ctx):
    try:
        in_scope, reviewed, skipped = _coverage(ctx.obj)
    except Refused as exc:
        return str(exc)
    for key, value in (("in_scope", in_scope), ("reviewed", reviewed), ("skipped", skipped)):
        if len(set(value)) != len(value):
            return "coverage.%s repeats a file" % key
    both = sorted(set(reviewed) & set(skipped))
    if both:
        return "reviewed and skipped both hold %s" % both[0]
    if set(in_scope) != set(reviewed) | set(skipped):
        return "in_scope is not reviewed plus skipped"
    return None


def rule_reviewer_skip_reason(ctx):
    for item in ctx.obj["coverage"]["skipped"]:
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return "skipped file %s has no reason" % item["file"]
    return None


def rule_reviewer_shortcuts(ctx):
    shortcuts = ctx.obj.get("shortcuts", [])
    check = ctx.obj.get("shortcuts_check", "")
    if not isinstance(shortcuts, list):
        return "shortcuts is not a list"
    if not isinstance(check, str):
        return "shortcuts_check is not a string"
    if not shortcuts and not check.strip():
        return "shortcuts and shortcuts_check are both empty"
    return None


def _repo(ctx):
    return Path(os.path.abspath(str(ctx.repo if ctx.repo is not None else os.getcwd())))


def rule_reviewer_base_head(ctx):
    if ctx.base is None or ctx.head is None:
        return "the reviewer needs the reviewed base and head (or --shape-only)"
    repo = _repo(ctx)
    try:
        ctx.base_id = review_scope.resolve_commit(repo, ctx.base)
        ctx.head_id = review_scope.resolve_commit(repo, ctx.head)
    except review_scope.ScopeError as exc:
        return "the reviewed revisions: %s" % exc
    for key, given in (("base", ctx.base_id), ("head", ctx.head_id)):
        value = ctx.obj.get(key)
        if not isinstance(value, str):
            return "the reply's %s is not a revision" % key
        try:
            named = review_scope.resolve_commit(repo, value)
        except review_scope.ScopeError as exc:
            return "the reply's %s: %s" % (key, exc)
        if named != given:
            return "the reply's %s %s is not the reviewed %s %s" % (key, named, key, given)
    return None


def rule_reviewer_coverage_scope(ctx):
    try:
        listed = review_scope.scope(_repo(ctx), ctx.base_id, ctx.head_id)
    except review_scope.ScopeError as exc:
        return "review_scope: %s" % exc
    want = set(path for path, _verdict in listed)
    got = set(ctx.obj["coverage"]["in_scope"])
    if got != want:
        missing, extra = sorted(want - got), sorted(got - want)
        return "in_scope differs from review_scope: missing %s, extra %s" % (missing[:3], extra[:3])
    return None


def _show(repo, commit, path):
    """The bytes of `path` at `commit`, or None when the commit holds no file there."""
    try:  # `cat-file blob` refuses a folder
        code, out = review_scope._git(repo, ["cat-file", "blob", "%s:%s" % (commit, path)])
    except review_scope.ScopeError:
        return None
    return out if code == 0 else None


def rule_reviewer_anchor(ctx):
    repo = _repo(ctx)
    for row in ctx.obj["findings"]:
        data = _show(repo, ctx.head_id, row["file"])
        if data is None:
            return "%s: %s is not a file at head" % (row["id"], row["file"])
        lines = _lines(data.decode("utf-8", "replace"))
        first = row["line"]
        last = row.get("end_line", first)
        window = normalise(" ".join(lines[max(0, first - 1 - ANCHOR_SLACK):last + ANCHOR_SLACK]))
        if normalise(row["quote"]) not in window:
            return "%s: the quote is not in %s lines %d-%d at head" % (
                row["id"], row["file"], max(1, first - ANCHOR_SLACK), last + ANCHOR_SLACK)
    return None


# ---- worker and implementer ----------------------------------------------------------------------

def _status(lines):
    first = lines[0] if lines else ""
    if not first.startswith("STATUS: "):
        return None
    word = first[len("STATUS: "):].rstrip()
    return word if word in STATUSES else None


def _rule_status_line(ctx):
    ctx.lines = _lines(ctx.text)
    if _status(ctx.lines) is None:
        return "the first line is not STATUS: %s" % " | ".join(STATUSES)
    return None


rule_worker_status_line = _rule_status_line
rule_implementer_status_line = _rule_status_line


def rule_worker_summary(ctx):
    rest = ctx.lines[1:]
    index = 0
    while index < len(rest) and not rest[index].strip():
        index += 1
    if index == len(rest) or rest[index].rstrip() != "## Summary":
        return "the line after STATUS is not ## Summary"
    bullets = 0
    for line in rest[index + 1:]:
        if line.startswith("#"):
            break
        if line.startswith("- ") or line.startswith("* "):
            bullets += 1
    low, high = SUMMARY_BULLETS
    if not low <= bullets <= high:
        return "## Summary has %d bullets, not %d to %d" % (bullets, low, high)
    return None


def _field_lines(lines, names):
    """The index of the first line starting `<name>:` for each name, or None."""
    found = []
    for name in names:
        found.append(next((i for i, line in enumerate(lines) if line.startswith(name + ":")), None))
    return found


def rule_implementer_fields(ctx):
    found = _field_lines(ctx.lines, IMPLEMENTER_FIELDS)
    for name, index in zip(IMPLEMENTER_FIELDS, found):
        if index is None:
            return "%s missing" % name
    for position in range(1, len(found)):
        if found[position] < found[position - 1]:
            return "%s comes before %s" % (IMPLEMENTER_FIELDS[position], IMPLEMENTER_FIELDS[position - 1])
    return None


def rule_implementer_blocked_fields(ctx):
    status = _status(ctx.lines)
    if status not in ("BLOCKED", "NEEDS_CONTEXT"):
        return None
    for name, index in zip(BLOCKED_FIELDS, _field_lines(ctx.lines, BLOCKED_FIELDS)):
        if index is None:
            return "%s missing with %s" % (name, status)
    return None


# ---- mapper --------------------------------------------------------------------------------------

def _location(line):
    """(path, text) of a `path:START[-END] - text` line, else None."""
    head, sep, text = line.partition(" - ")
    if not sep or not text.strip():
        return None
    path, colon, lines = head.rpartition(":")
    if not colon or not path:
        return None
    start, dash, end = lines.partition("-")
    if not start.isdigit() or (dash and not end.isdigit()):
        return None
    if not start.isascii() or not end.isascii():
        return None
    if int(start) < 1 or (dash and int(end) < int(start)):
        return None
    return path, text


def _mapper_lines(ctx):
    return [line for line in _lines(ctx.text) if line.strip()]


def rule_mapper_line_shape(ctx):
    lines = _mapper_lines(ctx)
    if not lines:
        return "no lines"
    for line in lines:
        if line.startswith(NOT_FOUND):
            if not line[len(NOT_FOUND):].strip():
                return "NOT FOUND names no terms"
            continue
        if _location(line) is None:
            return "not a path:START[-END] - text line: %s" % line[:80]
    return None


def rule_mapper_words(ctx):
    for line in _mapper_lines(ctx):
        if line.startswith(NOT_FOUND):
            continue
        words = len(_location(line)[1].split())
        if words > MAPPER_WORDS:
            return "%d words, over %d: %s" % (words, MAPPER_WORDS, line[:80])
    return None


def rule_mapper_not_found_alone(ctx):
    lines = _mapper_lines(ctx)
    if any(line.startswith(NOT_FOUND) for line in lines) and len(lines) > 1:
        return "NOT FOUND is not the only line"
    return None


def rule_mapper_path_exists(ctx):
    if ctx.head is None:
        return "the mapper needs the reviewed head (or --shape-only)"
    repo = _repo(ctx)
    paths = [_location(line)[0] for line in _mapper_lines(ctx) if not line.startswith(NOT_FOUND)]
    for path in paths:
        problem = path_problem(path)
        if problem:
            return "%s: %s" % (path, problem)
    if not paths:
        return None
    try:
        head = review_scope.resolve_commit(repo, ctx.head)
    except review_scope.ScopeError as exc:
        return "the reviewed head: %s" % exc
    for path in paths:
        if _show(repo, head, path) is None:
            return "%s is not a file at head" % path
    return None


# ---- hook slots ----------------------------------------------------------------------------------

def rule_shell_guard_reply_shape(ctx):
    if not ctx.text.strip():
        return None
    try:
        obj = json.loads(ctx.text)
    except ValueError:
        return "the reply is not one JSON object"
    output = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
    if not isinstance(output, dict):
        return "the reply has no hookSpecificOutput object"
    if "permissionDecision" in output:
        if output["permissionDecision"] != "deny" or not isinstance(output.get("permissionDecisionReason"), str):
            return "a decision other than a deny with a reason"
        ctx.obj = output
        return None
    if not isinstance(output.get("additionalContext"), str):
        return "neither a deny nor additionalContext"
    return None


def rule_shell_guard_deny_reason(ctx):
    if ctx.obj is None:
        return None
    if RULE_ID.search(ctx.obj["permissionDecisionReason"]) is None:
        return "the deny reason names no [rule id]"
    return None


def rule_lessons_inbox_line(ctx):
    first = _lines(ctx.text)[0]
    if INBOX_LINE.fullmatch(first) is None:
        return "the first line is not `Lessons inbox: <path>: <n> entries` or `...: count unknown (<error>)`"
    return None


# ---- check and CLI -------------------------------------------------------------------------------

def _rule_function(slot, rule):
    return getattr(sys.modules[__name__], "rule_%s_%s" % (slot.replace("-", "_"), rule.replace("-", "_")))


def check(slot, text, repo=None, base=None, head=None, shape_only=False):
    # type: (str, str, Optional[Path], Optional[str], Optional[str], bool) -> Verdict
    """The verdict of `text` against `slot`'s contract; never raises."""
    try:
        if slot not in RULES:
            return Verdict(False, "slot", "no output contract for slot %r" % slot, None)
        if len(text) > TEXT_CAP:
            return Verdict(False, RULES[slot][0], "text over %d characters" % TEXT_CAP, None)
        ctx = _Context(text, repo, base, head)
        for rule in RULES[slot]:
            if shape_only and rule in GIT_RULES:
                continue
            reason = _rule_function(slot, rule)(ctx)
            if reason is not None:
                return Verdict(False, rule, reason, None)
        findings = len(ctx.obj["findings"]) if slot == "reviewer" else None
        return Verdict(True, "", "", findings)
    except Exception as exc:  # any failure is a refusal, never a pass
        return Verdict(False, "internal", "%s: %s" % (type(exc).__name__, exc), None)


USAGE = "usage: slot_check.py <slot> <file> [--repo DIR] [--base REV] [--head REV] [--shape-only]"


def _parse(argv):
    """(slot, file, repo, base, head, shape_only), or None; each option takes the next token verbatim."""
    if len(argv) < 2 or argv[0].startswith("-") or argv[1].startswith("-"):
        return None
    values = {}  # type: dict
    shape_only = False
    index = 2
    while index < len(argv):
        name = argv[index]
        if name == "--shape-only" and not shape_only:
            shape_only = True
            index += 1
            continue
        if name not in ("--repo", "--base", "--head") or name in values or index + 1 >= len(argv):
            return None
        values[name] = argv[index + 1]
        index += 2
    repo = values.get("--repo")
    return argv[0], argv[1], Path(repo) if repo is not None else None, values.get("--base"), \
        values.get("--head"), shape_only


def _emit(text):
    sink = _out if _out is not None else sys.stdout.buffer
    sink.write(text.encode("utf-8"))
    sink.flush()


def _one_line(text):
    return " ".join(text.splitlines())


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    try:
        parsed = _parse(list(sys.argv[1:] if argv is None else argv))
        if parsed is None:
            _emit("slot-check: refused: usage: %s\n" % USAGE)
            return 2
        slot, name, repo, base, head, shape_only = parsed
        try:
            with open(name, "rb") as handle:
                data = handle.read(TEXT_CAP * 4 + 1)
        except OSError as exc:
            _emit("slot-check: refused: usage: cannot read %s: %s\n" % (name, _one_line(str(exc))))
            return 2
        verdict = check(slot, data.decode("utf-8", "replace"), repo, base, head, shape_only)
        if not verdict.ok:
            _emit("slot-check: refused: %s: %s\n" % (verdict.rule, _one_line(verdict.reason)))
            return 2
        if verdict.findings is not None:
            _emit("slot-check: ok %s %d findings\n" % (slot, verdict.findings))
        else:
            _emit("slot-check: ok %s\n" % slot)
        return 0
    except Exception as exc:
        _emit("slot-check: refused: internal: %s\n" % _one_line("%s: %s" % (type(exc).__name__, exc)))
        return 2


if __name__ == "__main__":
    sys.exit(main())
