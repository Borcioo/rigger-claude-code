"""Text operations of a background lessons batch on the inbox, the archive and the proposals queue.

Pure functions on text, plus write_atomic. An entry is a whole inbox line (inbox_lint.ENTRY) without its line end;
a decision names it by its text and its occurrence index among identical lines (0 for the first), so two identical
lines are two entries. Each inbox line keeps its own line end (CRLF or LF).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

import inbox_lint

SEP = inbox_lint.SEPARATOR
FENCE_OPEN = re.compile(r"(~{3,}|`{3,})")
FENCE_CLOSE = re.compile(r"(~{3,}|`{3,})[ \t]*")
REPLACE_TRIES = 5
REPLACE_PAUSE = 0.2


def unparked_slice(data: bytes, today: str, size: int = 5) -> list[tuple[int, str]]:
    """(line number, entry) of the first `size` entries in review order that no earlier run parked."""
    return [(number, line) for _, group, _, number, line in inbox_lint.review_order(data, today) if group != 2][:size]


def count_unparked(data: bytes) -> int:
    return sum(1 for item in inbox_lint.review_order(data, "") if item[1] != 2)


def one_line(text) -> str:
    """Model text as one field value: newlines and the field separator become `; `."""
    text = str(text or "").replace("\r\n", "\n").replace(SEP, "; ")
    return "; ".join(part.strip() for part in text.split("\n") if part.strip())


def merge_entry(target: str, repeat: str) -> str:
    """The target with each source_ref of the repeat it lacks; with the repeat's signal_type when only that one
    has evidence."""
    target_fields = inbox_lint.entry_fields(target)
    repeat_fields = inbox_lint.entry_fields(repeat)
    parts = target.split(SEP)
    if inbox_lint.has_evidence(repeat_fields) and not inbox_lint.has_evidence(target_fields):
        signal = "signal_type: " + repeat_fields["signal_type"][0]
        kept = parts[:1]
        for part in parts[1:]:
            if not part.startswith("signal_type:"):
                kept.append(part)
            elif signal:
                kept.append(signal)
                signal = ""
        parts = kept
    for ref in repeat_fields.get("source_ref", []):
        if ref and ref not in target_fields.get("source_ref", []):
            parts.append("source_ref: " + ref)
            target_fields.setdefault("source_ref", []).append(ref)
    return SEP.join(parts)


def park(entry: str, today: str) -> str:
    """The entry with ` · curated: <today>`, replacing any curated part it had."""
    parts = entry.split(SEP)
    kept = [parts[0]] + [part for part in parts[1:] if not part.startswith(inbox_lint.CURATED)]
    return SEP.join(kept + ["curated: " + today])


def _lines(text: str) -> list[str]:
    """Lines with their line ends; the last one may lack it."""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _sections(text: str) -> list[tuple[str, str]]:
    """[(heading, chunk)]: the text cut before every `## ` line outside a fence; heading "" before the first."""
    sections, heading, chunk, fence = [], "", [], ""
    for raw in _lines(text):
        line = raw.rstrip("\r\n")
        if fence:
            close = FENCE_CLOSE.fullmatch(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                fence = ""
        elif FENCE_OPEN.match(line):
            fence = FENCE_OPEN.match(line).group(1)
        elif line.startswith("## "):
            if chunk:
                sections.append((heading, "".join(chunk)))
            heading, chunk = line, []
        chunk.append(raw)
    if chunk:
        sections.append((heading, "".join(chunk)))
    return sections


def count_queued(queue_text: str) -> int:
    """Number of proposal blocks: `## Run ` headings outside fences."""
    return sum(1 for heading, _ in _sections(queue_text) if heading.startswith("## Run "))


def _fenced(text: str) -> str:
    size = max([3] + [len(run) + 1 for run in re.findall(r"~+", text)])
    body = text if not text or text.endswith("\n") else text + "\n"
    return "~" * size + "\n" + body + "~" * size + "\n"


def _queue_block(entry: str, proposal: dict, run_id: str) -> str:
    start = entry[12:].lstrip(" " + SEP.strip())[:60]
    bullets = "".join("- %s: %s\n" % (key, one_line(proposal.get(key))) for key in ("target", "heading", "evidence",
                                                                                     "reason"))
    return ("## Run %s%s%s %s\n\n- entry: %s\n%s\nOld:\n\n%s\nNew:\n\n%s\n"
            % (run_id, SEP, entry[2:12], start, entry, bullets, _fenced(str(proposal.get("old_text") or "")),
               _fenced(str(proposal.get("new_text") or ""))))


def apply_decisions(inbox: str, decisions: list[dict], run_id: str, today: str) -> tuple[str, str, str, dict]:
    """(new inbox, archive section, queue blocks, record) of the decisions applied to the inbox text.

    Merges come last, so a target parked in the same run is merged in its parked form and restore undoes the merge
    first. A decision whose entry (or merge target) is not in the inbox is skipped and listed in the record."""
    lines = inbox.split("\n")
    positions = {}
    for index, raw in enumerate(lines):
        if inbox_lint.ENTRY.match(raw):
            positions.setdefault(raw.rstrip("\r"), []).append(index)
    removed = set()
    archive, queue = [], []
    record = {"run_id": run_id, "archived": [], "parked": [], "merged": [], "queued": 0, "skipped": []}

    def find(text, occurrence):
        found = positions.get(text, [])
        index = found[occurrence] if 0 <= occurrence < len(found) else None
        return None if index is None or index in removed else index

    def text_at(index):
        return lines[index].rstrip("\r")

    def put(index, text):
        lines[index] = text + ("\r" if lines[index].endswith("\r") else "")

    def move(index, reason):
        record["archived"].append(text_at(index))
        archive.append("%s%sarchived: %s\n" % (text_at(index), SEP, one_line(reason)))
        removed.add(index)

    ordered = [item for item in decisions if item["action"] != "merge"]
    ordered += [item for item in decisions if item["action"] == "merge"]
    for item in ordered:
        index = find(item["entry"], item.get("occurrence", 0))
        action = item["action"]
        target = None
        if action == "merge" and index is not None:
            target = find(item.get("target_entry") or "", item.get("target_occurrence", 0))
        if index is None or (action == "merge" and target in (None, index)):
            record["skipped"].append(item["entry"])
            continue
        if action == "archive":
            move(index, item.get("reason"))
        elif action in ("park", "queue"):
            original = text_at(index)
            put(index, park(original, today))
            record["parked"].append([original, text_at(index)])
            if action == "queue":
                queue.append(_queue_block(original, item.get("proposal") or {}, run_id))
                record["queued"] += 1
        elif action == "merge":
            before = text_at(target)
            put(target, merge_entry(before, text_at(index)))
            record["merged"].append([before, text_at(target)])
            move(index, item.get("reason"))
    new_inbox = "\n".join(line for index, line in enumerate(lines) if index not in removed)
    if new_inbox and inbox.endswith("\n") and not new_inbox.endswith("\n"):
        new_inbox += "\n"
    section = "## Run %s\n\n%s\n" % (run_id, "".join(archive)) if archive else ""
    return new_inbox, section, "".join(queue), record


def _replace_line(text: str, old: str, new: str) -> tuple[str, bool]:
    lines = text.split("\n")
    for index, raw in enumerate(lines):
        if raw.rstrip("\r") == old:
            lines[index] = new + ("\r" if raw.endswith("\r") else "")
            return "\n".join(lines), True
    return text, False


def append_text(text: str, addition: str) -> str:
    """The addition after the text, on a line of its own."""
    if text and not text.endswith("\n") and addition:
        text += "\n"
    return text + addition


def restore(inbox: str, archive: str, queue: str, record: dict) -> tuple[str, str, str, int]:
    """Undo one run: its archived entries back at the inbox end (only while its archive section exists), merged and
    parked lines back to their text before the run where they still occur, its archive section and queue blocks
    removed. Returns (inbox, archive, queue, count restored); a second restore of the same run changes nothing."""
    run_id = record["run_id"]
    count = 0
    sections = _sections(archive)
    own = "## Run " + run_id
    if any(heading == own for heading, _ in sections):
        for line in record.get("archived", []):
            inbox = append_text(inbox, line + "\n")
            count += 1
        archive = "".join(chunk for heading, chunk in sections if heading != own)
    for before, after in list(reversed(record.get("merged", []))) + list(reversed(record.get("parked", []))):
        inbox, done = _replace_line(inbox, after, before)
        count += done
    prefix = own + SEP
    queue = "".join(chunk for heading, chunk in _sections(queue) if not heading.startswith(prefix))
    return inbox, archive, queue, count


class Changed(Exception):
    """write_atomic found the file no longer holding the expected bytes; nothing was replaced."""


def write_atomic(path: Path, text: str, expected: bytes | None = None) -> None:
    """Write UTF-8 text as given (no newline translation) through a temp file in the same folder and os.replace;
    a symlinked path stays a link. A PermissionError on replace (a reader holding the file on Windows) is retried.
    With expected, every replace attempt first checks the file still holds those bytes and raises Changed if not."""
    real = os.path.realpath(str(path))
    handle, temp = tempfile.mkstemp(dir=os.path.dirname(real), prefix="." + os.path.basename(real) + ".")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
        for attempt in range(REPLACE_TRIES):
            if expected is not None and Path(real).read_bytes() != expected:
                raise Changed(real)
            try:
                os.replace(temp, real)
                return
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_PAUSE)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
