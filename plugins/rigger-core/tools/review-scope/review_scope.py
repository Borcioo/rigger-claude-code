"""The one list of files a review covers: every path `git diff` names between two commits.

Each path is `review`, or `skip: deleted` when the head commit no longer holds it. A test file is
never skipped. A revision is checked in Python before git sees it and resolved to a commit id; only
those ids reach the diff, which runs with the repository's and the environment's diff drivers off.
Git itself is found by `find_git`, never by the operating system's lookup, and runs without any
GIT_* variable. GIT_CONFIG_NOSYSTEM is not set: nothing a cloned repository controls lives in the
system config, and git reads `safe.directory` there.

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
GIT_OPTIONS = ("-c", "core.quotepath=off", "--no-pager")
GIT_EXTS = (".exe",) if os.name == "nt" else ("",)
BATCH_EXTS = (".cmd", ".bat")
GIT_TIMEOUT = 60
REVIEW = "review"
DELETED = "skip: deleted"


class ScopeError(Exception):
    pass


def inside(folder, root):
    folder = os.path.normcase(os.path.abspath(folder))
    root = os.path.normcase(os.path.abspath(str(root)))
    return folder == root or folder.startswith(root.rstrip(os.sep) + os.sep)


_PREFIXES = (("\\\\?\\UNC\\", "\\\\"), ("\\\\.\\UNC\\", "\\\\"), ("\\\\?\\", ""), ("\\\\.\\", ""))


def _plain(entry):
    # type: (str) -> Optional[str]
    """On Windows `entry` without an extended (`\\\\?\\`) or device (`\\\\.\\`) prefix, or None when what is left
    has no drive or UNC root: a root-relative or drive-relative entry, a volume name or a device."""
    if os.name != "nt":
        return entry
    text = entry.replace("/", "\\")
    for prefix, put in _PREFIXES:
        if text[:len(prefix)].upper() == prefix:
            text = put + text[len(prefix):]
            break
    drive = os.path.splitdrive(text)[0]
    if drive[1:2] == ":" or (drive.startswith("\\\\") and drive[2:3] not in ("?", ".")):
        return text
    return None


def under_by_identity(path, roots):
    # type: (str, Sequence[os.stat_result]) -> bool
    """`path` or a folder above it is one of `roots` by file identity, which holds where a case-insensitive file
    system lets another spelling name the same folder; a level that cannot be read is passed over."""
    folder = path
    while True:
        try:
            if any(os.path.samestat(os.stat(folder), root) for root in roots):
                return True
        except OSError:
            pass
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def searchable(entry, repo, cwd):
    # type: (str, Path, str) -> bool
    """`entry` is an absolute PATH entry outside `repo` and `cwd`, compared as spelled, with links resolved on
    each side and by file identity, so a junction, a symlink, a linked checkout or another spelling of a folder
    cannot lead into either. On Windows a prefixed entry is compared without its prefix and one with no drive or
    UNC root is not absolute. When `repo` or `cwd` cannot be read, no entry is searched."""
    entry = _plain(entry)
    if entry is None or not os.path.isabs(entry):
        return False
    try:
        roots = [os.stat(root) for root in (str(repo), cwd)]
    except OSError:
        return False
    real = os.path.realpath(entry)
    return not (any(inside(path, base)
                    for path in (entry, real)
                    for root in (str(repo), cwd)
                    for base in (root, os.path.realpath(root)))
                or under_by_identity(real, roots))


def find_program(name, repo, exts):
    # type: (str, Path, Sequence[str]) -> str
    """The absolute path of program `name` + one of `exts`, from the absolute PATH entries outside `repo`
    and the working directory, so a file a cloned repository plants is never run. A batch file is refused:
    it always runs through cmd.exe, which re-parses its arguments."""
    if not name or "/" in name or "\\" in name:
        raise ScopeError("not a bare program name: %r" % name)
    if any(ext.lower() in BATCH_EXTS for ext in exts):
        raise ScopeError("a batch file is never run: %s" % ", ".join(exts))
    cwd = os.getcwd()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not searchable(entry, repo, cwd):
            continue
        for ext in exts:
            candidate = os.path.join(os.path.abspath(entry), name + ext)
            if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
                return candidate
    raise ScopeError("%s not found in an absolute PATH entry outside the repository and the working directory" % name)


def find_git(repo):
    # type: (Path) -> str
    return find_program("git", repo, GIT_EXTS)


def _git_env():
    """The caller's environment without any GIT_* variable, so none can move the repository or its config."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def git(repo, args):
    """(exit code, stdout bytes) of one git command in `repo`, found by find_git and run without any GIT_*
    variable; any way it fails to run is a ScopeError. The one way other tools start git through this module."""
    try:
        proc = subprocess.run(
            [find_git(repo)] + list(GIT_OPTIONS) + list(args),
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
    code, _out = git(repo, ["rev-parse", "--git-dir"])
    if code != 0:
        raise ScopeError("not a git repository: %s" % repo)


def _resolve(repo, rev):
    code, out = git(repo, ["rev-parse", "--verify", "--quiet", rev + "^{commit}"])
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
    code, out = git(repo, ["diff"] + list(GIT_FLAGS) + ["--name-status", "-z", base_id, head_id])
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
