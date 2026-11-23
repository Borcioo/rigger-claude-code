"""Mechanical check of the lessons proposal queue against the current tree, before any model reads it.

Usage: python proposal_check.py <lessons-proposals.md> [--repo DIR]... [--corpus FILE]... [--archive]

For each `## Run ` block (the format batch_apply writes) it resolves `- target:` (absolute, or relative to a
--repo) and prints `<verdict>: <block heading> (<detail>)`, then `checked <n>: live .., stale .., applied ..,
covered ..`:
  applied  the target holds the New text and no longer the Old text (or the block has no Old text);
  stale    the target is missing, or its Old text does not occur exactly once;
  covered  every line the New text adds (lines of at least MIN_LINE characters with a word, whitespace collapsed) is
           already in a rules file, skill or CLAUDE.md of a --repo, or in a --corpus file, other than the target;
  live     none of these.
--archive moves every block that is not live into `## Check <stamp>` of lessons-archive.md next to the queue (the
entry line plus `archived: <verdict>: <detail>`) and removes it from the queue. Exit 2, before any check, when the
queue is missing or unreadable or holds text but no block, when a --repo holds no `plugins/` folder, or when
--archive meets a relative target with no --repo; exit 1 when --archive found a batch holding the lock or the queue
changed since it was read (nothing written); else 0.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import batch_apply  # noqa: E402
import lessons_batch  # noqa: E402

FENCE = re.compile(r"(~{3,}|`{3,})[ \t]*")
MIN_LINE = 20
VERDICTS = ("live", "stale", "applied", "covered")


def _norm(line: str) -> str:
    return " ".join(line.split())


def _fence_after(lines: list[str], label: str) -> str:
    """The fenced text after the `label` line ("Old:" or "New:"), without its last line end; "" when absent."""
    try:
        start = lines.index(label)
    except ValueError:
        return ""
    for index in range(start + 1, len(lines)):
        opened = FENCE.fullmatch(lines[index])
        if opened:
            fence, body = opened.group(1), []
            for line in lines[index + 1:]:
                closed = FENCE.fullmatch(line)
                if closed and closed.group(1)[0] == fence[0] and len(closed.group(1)) >= len(fence):
                    return "\n".join(body)
                body.append(line)
            return "\n".join(body)
    return ""


def _bullet(lines: list[str], key: str) -> str:
    prefix = "- %s: " % key
    return next((line[len(prefix):].strip() for line in lines if line.startswith(prefix)), "")


def parse_blocks(text: str) -> list[dict]:
    """One dict per `## Run ` section: heading, chunk (as read), entry, target, old, new."""
    blocks = []
    for heading, chunk in batch_apply._sections(text):
        if not heading.startswith("## Run "):
            continue
        lines = chunk.replace("\r\n", "\n").split("\n")
        blocks.append(dict(heading=heading.rstrip("\r")[3:], chunk=chunk, entry=_bullet(lines, "entry"),
                           target=_bullet(lines, "target"), old=_fence_after(lines, "Old:"),
                           new=_fence_after(lines, "New:")))
    return blocks


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


def resolve(target: str, repos: list[Path]) -> Path | None:
    if not target:
        return None
    path = Path(target)
    if path.is_absolute():
        return path if path.is_file() else None
    return next((repo / path for repo in repos if (repo / path).is_file()), None)


def corpus_files(repos: list[Path], extra: list[Path]) -> list[Path]:
    files = []
    for repo in repos:
        files += sorted(repo.glob("plugins/*/rules/*.md")) + sorted(repo.glob("plugins/*/skills/**/*.md"))
        files += [repo / "CLAUDE.md"] if (repo / "CLAUDE.md").is_file() else []
    return files + [path for path in extra if path.is_file()]


def corpus_lines(files: list[Path]) -> dict[str, set[str]]:
    """Normalised line -> the resolved paths of the files holding it."""
    found = {}
    for path in files:
        for line in _read(path).split("\n"):
            found.setdefault(_norm(line), set()).add(str(path.resolve()))
    return found


def _counts(line: str) -> bool:
    """A line long enough, and with a word in it, to be evidence of cover."""
    return len(line) >= MIN_LINE and re.search(r"[^\W_]{3}", line) is not None


def verdict(block: dict, repos: list[Path], corpus: dict[str, set[str]]) -> tuple[str, str]:
    path = resolve(block["target"], repos)
    if path is None:
        return "stale", "target not found: %s" % (block["target"] or "none named")
    text = _read(path)
    new = block["new"].strip("\n")
    count = text.count(block["old"]) if block["old"] else 0
    if new and new in text and count == 0:
        return "applied", "the target already holds the new text"
    if block["old"] and count != 1:
        return "stale", "old text found %d times in the target" % count
    old_lines = {_norm(line) for line in block["old"].split("\n")}
    added = [_norm(line) for line in new.split("\n") if _counts(_norm(line)) and _norm(line) not in old_lines]
    own = str(path.resolve())
    holders = [corpus.get(line, set()) - {own} for line in added]
    if added and all(holders):
        return "covered", "every added line is already in %s" % ", ".join(
            sorted(Path(name).as_posix() for found in holders for name in found))
    return "live", "old text found once" if block["old"] else "no old text; new text not in the target"


def archive(queue: Path, data: bytes, blocks: list[dict], results: list[tuple[str, str]]) -> bool:
    """Move the blocks that are not live; False (nothing written) when the queue no longer holds `data`."""
    gone = [(block, result) for block, result in zip(blocks, results) if result[0] != "live"]
    if not gone:
        return True
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = "".join("%s%sarchived: %s: %s\n" % (block["entry"], batch_apply.SEP, kind, batch_apply.one_line(detail))
                    for block, (kind, detail) in gone)
    archive_path = queue.parent / "lessons-archive.md"
    existed = archive_path.exists()
    before = archive_path.read_bytes() if existed else b""
    text = data.decode("utf-8")
    drop = {index for index, result in enumerate(results) if result[0] != "live"}
    kept, index = [], 0
    for heading, chunk in batch_apply._sections(text):
        if heading.startswith("## Run "):
            index += 1
            if index - 1 in drop:
                continue
        kept.append(chunk)
    kept = "".join(kept)
    batch_apply.write_atomic(archive_path, batch_apply.append_text(before.decode("utf-8"),
                                                                   "## Check %s\n\n%s\n" % (stamp, lines)),
                             before if existed else None)
    try:
        batch_apply.write_atomic(queue, kept, data)
    except batch_apply.Changed:
        if existed:
            batch_apply.write_atomic(archive_path, before.decode("utf-8"))
        else:
            archive_path.unlink()
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("queue", type=Path)
    parser.add_argument("--repo", type=Path, action="append", default=[])
    parser.add_argument("--corpus", type=Path, action="append", default=[])
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = args.queue.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("proposal_check: cannot read %s (%s)" % (args.queue, type(exc).__name__))
        return 2
    blocks = parse_blocks(text)
    if not blocks and text.strip():
        print("proposal_check: %s holds text but no `## Run ` block; format changed?" % args.queue)
        return 2
    wrong = [str(repo) for repo in args.repo if not (repo / "plugins").is_dir()]
    if wrong:
        print("proposal_check: --repo %s holds no plugins/ folder" % ", ".join(wrong))
        return 2
    relative = [block["heading"] for block in blocks if block["target"] and not Path(block["target"]).is_absolute()]
    if args.archive and relative and not args.repo:
        print("proposal_check: refused: %d targets are relative to the harness repo; pass --repo <clone>" % len(relative))
        return 2
    corpus = corpus_lines(corpus_files(args.repo, args.corpus))
    results = [verdict(block, args.repo, corpus) for block in blocks]
    for block, (kind, detail) in zip(blocks, results):
        print("%s: %s (%s)" % (kind, block["heading"], detail))
    counts = {kind: sum(1 for result in results if result[0] == kind) for kind in VERDICTS}
    print("checked %d: %s" % (len(blocks), ", ".join("%s %d" % (kind, counts[kind]) for kind in VERDICTS)))
    if not args.archive:
        return 0
    state = lessons_batch.state_dir(os.environ)
    state.mkdir(parents=True, exist_ok=True)
    owner = "proposal-check-%d" % os.getpid()
    if not lessons_batch._take_lock(state / "lock", owner, time.time()):
        print("proposal_check: refused: a lessons batch holds %s; nothing written" % (state / "lock"))
        return 1
    try:
        if not archive(args.queue, data, blocks, results):
            print("proposal_check: refused: %s changed while it was checked; nothing written" % args.queue)
            return 1
    finally:
        lessons_batch._release_lock(state / "lock", owner)
    return 0


if __name__ == "__main__":
    sys.exit(main())
