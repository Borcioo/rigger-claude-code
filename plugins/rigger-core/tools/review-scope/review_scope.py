"""The one list of files a review covers: every path `git diff` names between two commits, and every rules
file the head's `.claude/rigger.json` names that its base's does not, listed as `review: rules file newly named`
when the diff leaves it unchanged (it has no hunk: review the whole file).

Each path gets a verdict, first match wins (D60): `review` for `.claude/rigger.json` and every rules file it
names at base or at head (always_reviewed), whatever a skip says; `skip: deleted`; a `review.paths` skip; a
file named like a test is `review`; `skip: binary` when the first 8,000 bytes of the head and the base blob
hold a NUL byte, `review: binary added` when only the head blob does; a file under a test folder is `review`;
`skip: lockfile`, `skip: vendored` and `skip: generated` only for a path that was a regular file at base;
everything else `review`. A verdict that starts with `review` is to be reviewed. A skip the reviewed diff
itself creates never applies.

A revision is checked in Python before git sees it and resolved to a commit id; only those ids reach git,
which runs with the repository's and the environment's diff drivers off. Blobs are read by object id through
one `git cat-file --batch` process per call, never by a path. Git itself is found by `find_git`, never by
the operating system's lookup, and runs without any GIT_* variable. GIT_CONFIG_NOSYSTEM is not set: nothing
a cloned repository controls lives in the system config, and git reads `safe.directory` there.

`review.paths` (D61): levels 2 and 3 come from the session snapshot, level 4 from `<base>:.claude/rigger.json`
only, so the reviewed diff cannot change what it is reviewed against. A level-4 rules file must be a tracked
file at base; its text is written to `--rules-out` and never read from the working tree.

  review_scope.py --base REV [--head REV] [--repo DIR] [--session ID] [--rules-out DIR]

prints `<verdict><TAB><path>` per path, then `# review.paths: ...` notices, `# rules <glob> <file> (level <n>)`
per entry that matched a path to review, then `# review-scope: files <n>, review <r>, skipped <s>`, and exits
0; a refusal prints `review-scope: refused: <reason>` and exits 2.
"""

import fnmatch
import os
import re
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assemble"))

import config  # noqa: E402
import snapshot  # noqa: E402

GIT_FLAGS = ("--no-ext-diff", "--no-textconv", "--no-color", "--no-renames")
GIT_OPTIONS = ("-c", "core.quotepath=off", "--no-pager")
GIT_EXTS = (".exe",) if os.name == "nt" else ("",)
BATCH_EXTS = (".cmd", ".bat")
GIT_TIMEOUT = 60
REVIEW = "review"
DELETED = "skip: deleted"
BINARY = "skip: binary"
BINARY_ADDED = "review: binary added"
NEWLY_NAMED = "review: rules file newly named"  # unchanged by the diff, so it has no hunk: read the whole file
LOCKFILE = "skip: lockfile"
VENDORED = "skip: vendored"
GENERATED = "skip: generated"
SNIFF = 8000  # bytes of a blob kept to look for a NUL byte or a generated marker
MARKER_LINES = 5
MARKERS = (b"@generated", b"DO NOT EDIT", b"auto-generated")
MINIFIED = (".min.js", ".min.css")
TEXT_CAP = 262144  # bytes of a project config or rules file read at base; a larger one is dropped, never cut
LOCKFILES = frozenset(("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
                       "composer.lock", "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock",
                       "go.sum"))
VENDOR_SEGMENTS = frozenset(("vendor", "node_modules", "third_party", "third-party"))
TEST_SEGMENTS = frozenset(("test", "tests", "__tests__", "spec"))  # compared in lower case
TEST_SEGMENT_ENDS = (".tests", ".test")  # a .NET test project, compared in lower case
TEST_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*", "*_spec.*", "*.tests.*", "test-*", "*Tests.*",
              "*Test.*")  # compared case-sensitively
REGULAR = (b"100644", b"100755")
LINK = b"120000"
PROJECT_CONFIG = ".claude/rigger.json"
OBJECT_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
LEVEL_NAMES = {2: "company", 3: "personal", 4: "project"}

PathRule = NamedTuple("PathRule", [("glob", str), ("rules", Optional[str]), ("skip", Optional[str]),
                                   ("level", int), ("source", str)])


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


class _Blobs:
    """One `git cat-file --batch` process. Blobs are asked for by object id one at a time and each answer is
    read whole, the first `cap` bytes kept; the process is killed once GIT_TIMEOUT has passed."""

    def __init__(self, repo):
        self.repo = repo
        self.proc = None
        self.timer = None
        self.expired = False

    def __enter__(self):
        try:
            self.proc = subprocess.Popen(
                [find_git(self.repo)] + list(GIT_OPTIONS) + ["cat-file", "--batch"],
                cwd=str(self.repo),
                env=_git_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ScopeError("git could not start: %s" % exc)
        self.timer = threading.Timer(GIT_TIMEOUT, self._expire)
        self.timer.daemon = True
        self.timer.start()
        return self

    def _expire(self):
        self.expired = True
        try:
            self.proc.kill()
        except OSError:
            pass

    def _fail(self, what):
        if self.expired:
            raise ScopeError("git cat-file took over %d s" % GIT_TIMEOUT)
        raise ScopeError("git cat-file %s" % what)

    def read(self, oid, cap):
        # type: (str, int) -> Tuple[int, bytes]
        """(size, the first `cap` bytes) of blob `oid`."""
        if not OBJECT_ID.fullmatch(oid):
            raise ScopeError("not an object id: %r" % oid)
        try:
            self.proc.stdin.write(oid.encode("ascii") + b"\n")
            self.proc.stdin.flush()
        except OSError:
            self._fail("stopped reading its input")
        header = self.proc.stdout.readline(200)
        fields = header[:-1].split(b" ") if header.endswith(b"\n") else []
        if len(fields) != 3 or fields[0] != oid.encode("ascii") or fields[1] != b"blob" or not fields[2].isdigit():
            self._fail("printed an unexpected header: %r" % header[:100])
        left = size = int(fields[2])
        kept = bytearray()
        while left:
            chunk = self.proc.stdout.read(min(65536, left))
            if not chunk:
                self._fail("stopped inside a blob")
            if len(kept) < cap:
                kept += chunk[:cap - len(kept)]
            left -= len(chunk)
        if self.proc.stdout.read(1) != b"\n":
            self._fail("printed no line end after a blob")
        return size, bytes(kept)

    def __exit__(self, *_exc):
        self.timer.cancel()
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        return False


TreeEntry = Tuple[bytes, str, int]  # mode, object id, size (-1 for an entry that is not a blob)


def _tree(repo, commit):
    # type: (Path, str) -> Dict[str, TreeEntry]
    """Every entry of `commit`'s whole tree by repository-relative path; a path that is not UTF-8 is left out."""
    code, out = git(repo, ["ls-tree", "-r", "-z", "-l", "--full-tree", commit, "--"])
    if code != 0:
        raise ScopeError("git ls-tree failed with exit %d" % code)
    entries = {}  # type: Dict[str, TreeEntry]
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, tab, name = record.partition(b"\t")
        fields = meta.split()
        if not tab or len(fields) != 4:
            raise ScopeError("git ls-tree printed an unexpected entry: %r" % record[:100])
        try:
            path = name.decode("utf-8")
        except UnicodeDecodeError:
            continue
        size = int(fields[3]) if fields[3].isdigit() else -1
        entries[path] = (fields[0], fields[2].decode("ascii", "replace"), size)
    return entries


def _diff(repo, base_id, head_id):
    # type: (Path, str, str) -> List[Tuple[str, str]]
    code, out = git(repo, ["diff"] + list(GIT_FLAGS) + ["--name-status", "-z", base_id, head_id])
    if code != 0:
        raise ScopeError("git diff failed with exit %d" % code)
    fields = out.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ScopeError("git diff printed an odd number of fields")
    changes = []
    for index in range(0, len(fields), 2):
        status = fields[index].decode("ascii", "replace")
        if status[:1] not in ("A", "D", "M", "T"):
            raise ScopeError("git diff printed an unexpected status: %s" % status)
        try:
            path = fields[index + 1].decode("utf-8")
        except UnicodeDecodeError:
            raise ScopeError("path is not UTF-8: %r" % fields[index + 1])
        _check_line_path(path)
        changes.append((path, status))
    return changes


def _check_line_path(path):
    # type: (str) -> None
    """Refuse a path that would break the one-line `<verdict><TAB><path>` output."""
    if "\t" in path or "\n" in path or "\r" in path:
        raise ScopeError("path holds a tab or a line break: %r" % path)


def _segment_match(pattern, text):
    # type: (str, str) -> bool
    """`*` matches any run of characters and `?` one; a walk over (pattern, text) positions, each visited once."""
    seen = set()
    stack = [(0, 0)]
    while stack:
        state = stack.pop()
        if state in seen:
            continue
        seen.add(state)
        i, j = state
        if i == len(pattern):
            if j == len(text):
                return True
            continue
        if pattern[i] == "*":
            stack.append((i + 1, j))
            if j < len(text):
                stack.append((i, j + 1))
        elif j < len(text) and pattern[i] in ("?", text[j]):
            stack.append((i + 1, j + 1))
    return False


def glob_match(pattern, path):
    # type: (str, str) -> bool
    """`pattern` matches the repository-relative `path` by `/` segment, case-sensitively: `**` is any number of
    segments, `*` and `?` stay inside one. The walk visits each (glob segment, path segment) pair once."""
    globs = pattern.split("/")
    parts = path.split("/")
    seen = set()
    stack = [(0, 0)]
    while stack:
        state = stack.pop()
        if state in seen:
            continue
        seen.add(state)
        i, j = state
        if i == len(globs):
            if j == len(parts):
                return True
            continue
        if globs[i] == "**":
            stack.append((i + 1, j))
            if j < len(parts):
                stack.append((i, j + 1))
        elif j < len(parts) and _segment_match(globs[i], parts[j]):
            stack.append((i + 1, j + 1))
    return False


def _test_name(path):
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in TEST_NAMES)


def _test_folder(path):
    return any(segment.lower() in TEST_SEGMENTS or segment.lower().endswith(TEST_SEGMENT_ENDS)
               for segment in path.split("/")[:-1])


def is_test_file(path):
    # type: (str) -> bool
    """A file named like a test, or under a test folder."""
    return _test_name(path) or _test_folder(path)


def _marked(prefix):
    return any(marker in line for line in prefix.split(b"\n")[:MARKER_LINES] for marker in MARKERS)


def _prefix(blobs, entry):
    # type: (_Blobs, Optional[TreeEntry]) -> Optional[bytes]
    """The first SNIFF bytes of a regular file's blob; None for no entry, a link or a submodule."""
    if entry is None or entry[0] not in REGULAR:
        return None
    return blobs.read(entry[1], SNIFF)[1]


def _named_skip(rules, path):
    # type: (Sequence[PathRule], str) -> Optional[str]
    for item in sorted(rules, key=lambda item: -item.level):
        if item.skip is not None and glob_match(item.glob, path):
            return item.skip
    return None


def _named_rules_files(blobs, entry):
    # type: (_Blobs, Optional[TreeEntry]) -> set
    """Every rules file a project config blob's `review.paths` entries name, whether or not the entry would
    apply; none for a config that a review would drop (not a regular file, over TEXT_CAP, not schema 1)."""
    if entry is None or entry[0] not in REGULAR or entry[2] > TEXT_CAP:
        return set()
    data, _found = config.parse_level(blobs.read(entry[1], TEXT_CAP)[1], PROJECT_CONFIG)
    review = data.get("review")
    paths = review.get("paths") if isinstance(review, dict) else None
    names = set()
    for value in (paths.values() if isinstance(paths, dict) else ()):
        name = value.get("rules") if isinstance(value, dict) else value
        if isinstance(name, str):
            names.add(name)
    return names


def _always_reviewed(base_tree, head_tree, blobs):
    # type: (Dict[str, TreeEntry], Dict[str, TreeEntry], _Blobs) -> set
    return ({PROJECT_CONFIG} | _named_rules_files(blobs, base_tree.get(PROJECT_CONFIG))
            | _named_rules_files(blobs, head_tree.get(PROJECT_CONFIG)))


def always_reviewed(repo, base, head):
    # type: (Path, str, str) -> set
    """The paths whose verdict is always `review`: PROJECT_CONFIG and every rules file it names at `base` or
    at `head`, since a later review reads them at its own base."""
    _check_revision(base)
    _check_revision(head)
    repo = Path(os.path.abspath(str(repo)))
    _check_repository(repo)
    base_tree = _tree(repo, _resolve(repo, base))
    head_tree = _tree(repo, _resolve(repo, head))
    with _Blobs(repo) as blobs:
        return _always_reviewed(base_tree, head_tree, blobs)


def _verdict(path, status, at_base, at_head, blobs, rules, trusted):
    if path in trusted:
        return REVIEW
    if status == "D":
        return DELETED
    reason = _named_skip(rules, path)
    if reason is not None:
        return "skip: %s (review.paths)" % reason
    if _test_name(path):
        return REVIEW
    head_prefix = _prefix(blobs, at_head)
    base_prefix = None
    if head_prefix is not None and (b"\0" in head_prefix or _marked(head_prefix)):
        base_prefix = _prefix(blobs, at_base)
    if head_prefix is not None and b"\0" in head_prefix:
        if base_prefix is not None and b"\0" in base_prefix:
            return BINARY
        return BINARY_ADDED
    if _test_folder(path):
        return REVIEW
    name = path.rsplit("/", 1)[-1]
    regular_at_base = at_base is not None and at_base[0] in REGULAR
    if regular_at_base and name in LOCKFILES:
        return LOCKFILE
    if regular_at_base and any(segment in VENDOR_SEGMENTS for segment in path.split("/")[:-1]):
        return VENDORED
    if regular_at_base and name.endswith(MINIFIED):
        return GENERATED
    # The base blob is read only for a head blob with a NUL byte (returned above) or a marker.
    if base_prefix is not None and _marked(base_prefix):
        return GENERATED
    return REVIEW


def scope(repo, base, head, rules=()):
    # type: (Path, str, str, Sequence[PathRule]) -> List[Tuple[str, str]]
    """`(path, verdict)` for every path changed between `base` and `head`, and NEWLY_NAMED for every rules file
    the head config names, the base config does not and the diff leaves unchanged, sorted by path; `rules` are the
    `review.paths` entries whose skips apply (path_rules)."""
    _check_revision(base)
    _check_revision(head)
    repo = Path(os.path.abspath(str(repo)))
    _check_repository(repo)
    base_id = _resolve(repo, base)
    head_id = _resolve(repo, head)
    changes = _diff(repo, base_id, head_id)
    if not changes:
        return []
    base_tree = _tree(repo, base_id)
    head_tree = _tree(repo, head_id)
    with _Blobs(repo) as blobs:
        trusted = _always_reviewed(base_tree, head_tree, blobs)
        rows = [(path, _verdict(path, status, base_tree.get(path), head_tree.get(path), blobs, rules, trusted))
                for path, status in changes]
        changed = set(path for path, _status in changes)
        newly = (_named_rules_files(blobs, head_tree.get(PROJECT_CONFIG))
                 - _named_rules_files(blobs, base_tree.get(PROJECT_CONFIG)))
        listed = sorted(name for name in newly if name not in changed and name in head_tree)
        for name in listed:
            _check_line_path(name)
        rows += [(name, _newly_named(head_tree[name])) for name in listed]
    return sorted(rows)


def _newly_named(entry):
    # type: (TreeEntry) -> str
    """NEWLY_NAMED, or a variant that says why the file is not to be read: a link leads wherever its target
    points, any other entry that is not a regular file (a submodule commit) holds no rules text, a blob missing
    from the object store has no size to check, and a file over TEXT_CAP is one a later review drops."""
    if entry[0] == LINK:
        return NEWLY_NAMED + " (link)"
    if entry[0] not in REGULAR:
        return NEWLY_NAMED + " (not a regular file)"
    if entry[2] < 0:
        return NEWLY_NAMED + " (object missing)"
    if entry[2] > TEXT_CAP:
        return NEWLY_NAMED + " (over %d bytes)" % TEXT_CAP
    return NEWLY_NAMED


# --- review.paths (D61) --------------------------------------------------------------------------------

def _level(source):
    match = re.match(r"L([1-4]):", source) if isinstance(source, str) else None
    return int(match.group(1)) if match else None


def _entry_parts(value):
    # type: (object) -> Tuple[Optional[str], Optional[str]]
    if isinstance(value, str):
        return value, None
    return value.get("rules"), value.get("skip")


def _notice(notice):
    return notice[len("rigger: "):] if notice.startswith("rigger: ") else notice


def _project_file_problem(name, tree):
    # type: (str, Dict[str, TreeEntry]) -> Optional[str]
    """Why a level-4 rules file cannot be read at base: it must be one of base's tracked files, spelled as
    git lists it."""
    parts = name.split("/")
    if name.startswith("/") or ":" in parts[0] or ".." in parts:
        return "rules file outside the repository"
    if "\\" in name or any(part in ("", ".") for part in parts):
        return "rules file not a plain repository path"
    entry = tree.get(name)
    if entry is None:
        return "rules file not tracked at base"
    if entry[0] not in REGULAR:
        return "rules file not a regular file at base"
    if entry[2] > TEXT_CAP:
        return "rules file over %d bytes" % TEXT_CAP
    return None


def _project_rules(repo, tree, notices):
    # type: (Path, Dict[str, TreeEntry], List[str]) -> Tuple[List[PathRule], set]
    """Level-4 entries of `<base>:.claude/rigger.json`, and every glob it names (a null one included)."""
    entry = tree.get(PROJECT_CONFIG)
    if entry is None:
        return [], set()
    where = "base:" + PROJECT_CONFIG
    if entry[0] not in REGULAR:
        notices.append("project config %s dropped: not a regular file at base" % PROJECT_CONFIG)
        return [], set()
    if entry[2] > TEXT_CAP:
        notices.append("project config %s dropped: over %d bytes" % (PROJECT_CONFIG, TEXT_CAP))
        return [], set()
    with _Blobs(repo) as blobs:
        _size, raw = blobs.read(entry[1], TEXT_CAP)
    data, found = config.parse_level(raw, where)
    notices.extend(_notice(notice) for notice in found)
    if "review" not in data:
        return [], set()
    clean, found = config.validate_level({"review": data["review"]}, 4, where)
    notices.extend(_notice(notice) for notice in found)
    review = clean.get("review")
    paths = review.get("paths") if isinstance(review, dict) else None
    if not isinstance(paths, dict):
        return [], set()
    rules = []
    for glob, value in paths.items():
        if value is None:
            continue
        rules_file, skip = _entry_parts(value)
        if rules_file is not None:
            problem = _project_file_problem(rules_file, tree)
            if problem:
                notices.append("project entry %s dropped: %s" % (config._shown(glob), problem))
                continue
        rules.append(PathRule(glob, rules_file, skip, 4, "L4:" + where))
    return rules, set(paths)


def _snapshot_file(rules_file, source, modules):
    # type: (str, str, Dict[str, str]) -> Tuple[Optional[str], Optional[str]]
    """(the rules file on disk, None) for a level-2 or level-3 entry, or (None, why it is dropped)."""
    level = _level(source)
    if level == 2:
        root = modules.get(source[3:])
        if root is None:
            return None, "module root unknown"
        path = os.path.abspath(os.path.join(root, rules_file))
        if not inside(os.path.realpath(path), os.path.realpath(root)):
            return None, "rules file outside the module"
    elif level == 3:
        path = os.path.abspath(os.path.join(os.path.dirname(source[3:]), rules_file))
    else:
        return None, "rules file of an unknown level"
    if not os.path.isfile(path):
        return None, "rules file not found"
    return path, None


def _snapshot_rules(data, notices):
    # type: (Optional[dict], List[str]) -> List[PathRule]
    """Level-2 and level-3 entries of the session snapshot. An entry's level is the highest level among its
    leaves; one with a level-4 leaf is left to the read at base."""
    held = data.get("config") if isinstance(data, dict) else None
    effective = held.get("effective") if isinstance(held, dict) else None
    review = effective.get("review") if isinstance(effective, dict) else None
    paths = review.get("paths") if isinstance(review, dict) else None
    if not isinstance(paths, dict):
        return []
    leaves = {}  # type: Dict[str, List[Tuple[tuple, str]]]
    for row in held.get("sources") or []:
        if (isinstance(row, list) and len(row) == 2 and isinstance(row[0], list) and len(row[0]) >= 3
                and row[0][:2] == ["review", "paths"] and isinstance(row[1], str)):
            leaves.setdefault(row[0][2], []).append((tuple(row[0][3:]), row[1]))
    modules = {item.get("name"): item.get("root") for item in data.get("modules") or []
               if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("root"), str)}
    rules = []
    for glob, value in paths.items():
        found = leaves.get(glob, [])
        levels = [_level(source) for _leaf, source in found]
        if not levels or None in levels:
            notices.append("snapshot entry %s dropped: its level is unknown" % config._shown(glob))
            continue
        level = max(levels)
        if level not in (2, 3):
            continue
        _clean, problems = config.validate_level({"review": {"paths": {glob: value}}}, level, "snapshot")
        if problems:
            notices.extend(_notice(notice) for notice in problems)
            continue
        if value is None:
            continue
        rules_file, skip = _entry_parts(value)
        source = next(source for _leaf, source in found if _level(source) == level)
        if rules_file is not None:
            rules_source = next((source for leaf, source in found if leaf in ((), ("rules",))), source)
            rules_file, problem = _snapshot_file(rules_file, rules_source, modules)
            if problem:
                notices.append("%s entry %s dropped: %s"
                               % (LEVEL_NAMES[_level(rules_source)], config._shown(glob), problem))
                continue
        rules.append(PathRule(glob, rules_file, skip, level, source))
    return sorted(rules, key=lambda item: -item.level)


def path_rules(snapshot_data, repo, base, notices=None):
    # type: (Optional[dict], Path, str, Optional[List[str]]) -> List[PathRule]
    """The `review.paths` entries for a review of `base`: level 4 from `<base>:.claude/rigger.json`, which
    replaces a lower entry of the same glob, then levels 3 and 2 from `snapshot_data` (none when it is None).
    Each entry dropped on the way adds a line to `notices`."""
    notices = [] if notices is None else notices
    _check_revision(base)
    repo = Path(os.path.abspath(str(repo)))
    _check_repository(repo)
    tree = _tree(repo, _resolve(repo, base))
    project, named = _project_rules(repo, tree, notices)
    return project + [item for item in _snapshot_rules(snapshot_data, notices) if item.glob not in named]


def write_rules(repo, base, rules, out_dir):
    # type: (Path, str, Sequence[PathRule], str) -> List[str]
    """Write the base text of each level-4 rule's rules file, read by object id, to `<out_dir>/rules-<n>.md`;
    the written paths, in order."""
    repo = Path(os.path.abspath(str(repo)))
    tree = _tree(repo, resolve_commit(repo, base))
    written = []
    try:
        os.makedirs(out_dir, exist_ok=True)
        with _Blobs(repo) as blobs:
            for number, item in enumerate(rules, 1):
                problem = _project_file_problem(item.rules or "", tree)
                if problem:
                    raise ScopeError("%s: %s" % (problem, item.rules))
                _size, text = blobs.read(tree[item.rules][1], TEXT_CAP)
                path = os.path.join(out_dir, "rules-%d.md" % number)
                with open(path, "wb") as handle:
                    handle.write(text)
                written.append(path)
    except OSError as exc:
        raise ScopeError("rules could not be written to %s: %s" % (out_dir, exc))
    return written


def _snapshot(session):
    # type: (str) -> Optional[dict]
    if not session:
        return None
    value = os.environ.get("CLAUDE_CONFIG_DIR", "")
    return snapshot.read_snapshot(Path(value) if value else Path.home() / ".claude", session)


USAGE = "usage: review_scope.py --base REV [--head REV] [--repo DIR] [--session ID] [--rules-out DIR]"
OPTIONS = ("--base", "--head", "--repo", "--session", "--rules-out")


def _parse(argv):
    """Each option takes the next token verbatim, so `--base -p` reaches the revision check."""
    values = {}  # type: dict
    index = 0
    while index < len(argv):
        name = argv[index]
        if name not in OPTIONS or name in values or index + 1 >= len(argv):
            return None
        values[name] = argv[index + 1]
        index += 2
    if "--base" not in values:
        return None
    rules_out = values.get("--rules-out")
    return (values["--base"], values.get("--head", "HEAD"), values.get("--repo", os.getcwd()),
            values.get("--session", os.environ.get("CLAUDE_CODE_SESSION_ID", "")),
            os.path.abspath(rules_out) if rules_out else None)


def _emit(text):
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _report(repo, base, head, session, rules_out):
    # type: (Path, str, str, str, Optional[str]) -> List[str]
    data = _snapshot(session)
    notices = [] if data is not None else ["none (no snapshot)"]
    rules = path_rules(data, repo, base, notices)
    rows = scope(repo, base, head, rules)
    changed = set(path for path, _verdict in rows)
    for name in [PROJECT_CONFIG] + sorted(set(item.rules for item in rules if item.level == 4 and item.rules)):
        if name in changed:
            notices.append("project entries read at base; %s changed in this diff" % name)
    to_review = [path for path, verdict in rows if verdict.startswith(REVIEW)]
    matched = [item for item in rules
               if item.rules is not None and any(glob_match(item.glob, path) for path in to_review)]
    project = [item for item in matched if item.level == 4]
    written = iter(write_rules(repo, base, project, rules_out) if rules_out and project else [])
    lines = ["%s\t%s\n" % (verdict, path) for path, verdict in rows]
    lines.extend("# review.paths: %s\n" % notice for notice in notices)
    for item in matched:
        if item.level == 4:
            target = next(written) if rules_out else "(not written: no --rules-out)"
        else:
            target = item.rules
        lines.append("# rules %s %s (level %d)\n" % (item.glob, target, item.level))
    skipped = sum(1 for _path, verdict in rows if not verdict.startswith(REVIEW))
    lines.append("# review-scope: files %d, review %d, skipped %d\n" % (len(rows), len(rows) - skipped, skipped))
    return lines


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parsed = _parse(list(sys.argv[1:] if argv is None else argv))
    if parsed is None:
        _emit("review-scope: refused: %s\n" % USAGE)
        return 2
    base, head, repo, session, rules_out = parsed
    try:
        lines = _report(Path(repo), base, head, session, rules_out)
    except ScopeError as exc:
        _emit("review-scope: refused: %s\n" % exc)
        return 2
    _emit("".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
