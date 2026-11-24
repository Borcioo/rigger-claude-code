"""The one list of files a review covers: every path `git diff` names between two commits.

Each path is `review`, or `skip: deleted` when the head commit no longer holds it. A test file is
never skipped. A revision is checked in Python before git sees it and resolved to a commit id; only
those ids reach the diff, which runs with the repository's and the environment's diff drivers off.

  review_scope.py --base REV [--head REV] [--repo DIR]

prints `<verdict><TAB><path>` per path, then `# review-scope: files <n>, review <r>, skipped <s>`,
and exits 0; a refusal prints `review-scope: refused: <reason>` and exits 2.
"""

import os
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

GIT_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames")
GIT_PREFIX = ("git", "-c", "core.quotepath=off", "--no-pager")
GIT_TIMEOUT = 60
DROPPED_ENV = ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS")
REVIEW = "review"
DELETED = "skip: deleted"


class ScopeError(Exception):
    pass


def _git_env():
    return {key: value for key, value in os.environ.items() if key.upper() not in DROPPED_ENV}


def _git(repo, args):
    """Run one git command in `repo`; any way it fails is a ScopeError."""
    try:
        proc = subprocess.run(
            list(GIT_PREFIX) + list(args),
            cwd=str(repo),
            env=_git_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ScopeError("git %s took over %d s" % (args[0], GIT_TIMEOUT))
    except OSError as exc:
        raise ScopeError("git could not start: %s" % exc)
    return proc.returncode, proc.stdout


def _check_revision(rev):
    if not rev:
        raise ScopeError("revision is empty")
    if rev.startswith("-"):
        raise ScopeError("revision starts with '-': %s" % rev)
    if any(char.isspace() for char in rev):
        raise ScopeError("revision holds whitespace: %r" % rev)
    if any(unicodedata.category(char) == "Cc" for char in rev):
        raise ScopeError("revision holds a control character: %r" % rev)


def _check_repository(repo):
    if not os.path.isdir(str(repo)):
        raise ScopeError("not a git repository: %s" % repo)
    code, _out = _git(repo, ["rev-parse", "--git-dir"])
    if code != 0:
        raise ScopeError("not a git repository: %s" % repo)


def _resolve(repo, rev):
    code, out = _git(repo, ["rev-parse", "--verify", "--quiet", rev + "^{commit}"])
    if code != 0:
        raise ScopeError("revision does not name a commit: %s" % rev)
    return out.decode("ascii", "replace").strip()


def resolve_commit(repo, rev):
    # type: (Path, str) -> str
    """The full commit id `rev` names in `repo`; a revision that could read as an option never reaches git."""
    _check_revision(rev)
    repo = Path(os.path.abspath(str(repo)))
    _check_repository(repo)
    return _resolve(repo, rev)


def scope(repo, base, head):
    # type: (Path, str, str) -> List[Tuple[str, str]]
    """`(path, verdict)` for every path changed between `base` and `head`, sorted by path."""
    _check_revision(base)
    _check_revision(head)
    repo = Path(os.path.abspath(str(repo)))
    _check_repository(repo)
    base_id = _resolve(repo, base)
    head_id = _resolve(repo, head)
    code, out = _git(repo, ["diff"] + list(GIT_FLAGS) + ["--name-status", "-z", base_id, head_id])
    if code != 0:
        raise ScopeError("git diff failed with exit %d" % code)
    fields = out.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ScopeError("git diff printed an odd number of fields")
    rows = []
    for index in range(0, len(fields), 2):
        status = fields[index].decode("ascii", "replace")
        if status[:1] not in ("A", "D", "M", "T"):
            raise ScopeError("git diff printed an unexpected status: %s" % status)
        try:
            path = fields[index + 1].decode("utf-8")
        except UnicodeDecodeError:
            raise ScopeError("path is not UTF-8: %r" % fields[index + 1])
        if "\t" in path or "\n" in path or "\r" in path:
            raise ScopeError("path holds a tab or a line break: %r" % path)
        rows.append((path, DELETED if status == "D" else REVIEW))
    return sorted(rows)


USAGE = "usage: review_scope.py --base REV [--head REV] [--repo DIR]"


def _parse(argv):
    """Each option takes the next token verbatim, so `--base -p` reaches the revision check."""
    values = {}  # type: dict
    index = 0
    while index < len(argv):
        name = argv[index]
        if name not in ("--base", "--head", "--repo") or name in values or index + 1 >= len(argv):
            return None
        values[name] = argv[index + 1]
        index += 2
    if "--base" not in values:
        return None
    return values["--base"], values.get("--head", "HEAD"), values.get("--repo", os.getcwd())


def _emit(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parsed = _parse(list(sys.argv[1:] if argv is None else argv))
    if parsed is None:
        _emit("review-scope: refused: %s\n" % USAGE)
        return 2
    base, head, repo = parsed
    try:
        rows = scope(Path(repo), base, head)
    except ScopeError as exc:
        _emit("review-scope: refused: %s\n" % exc)
        return 2
    skipped = sum(1 for _path, verdict in rows if verdict != REVIEW)
    lines = ["%s\t%s\n" % (verdict, path) for path, verdict in rows]
    lines.append("# review-scope: files %d, review %d, skipped %d\n" % (len(rows), len(rows) - skipped, skipped))
    _emit("".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
