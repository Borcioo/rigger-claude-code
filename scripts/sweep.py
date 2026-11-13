"""Fail-closed sweep for private data: local paths, e-mails, IPs, tokens, hosts, denylisted terms.

Usage: python -I scripts/sweep.py [--denylist FILE | --no-denylist] [PATH ...]

Without PATH, scans the repo this script lives in as git stores it: every index blob of a tracked
file (a symlink's blob is its link text), plus the working-tree file when it differs from the
index, plus every untracked-not-ignored file. With PATH, scans those files and directories
instead, skipping `.git` entries; a path inside the repo is shown relative to the repo root.
Symlinks are never followed: their link text is scanned. The path of every file is scanned too.
A binary file (by extension or a NUL byte) is a finding unless allowed; a file that cannot be read
or is not UTF-8 is a finding.

The built-in rules are a floor. The host rule knows only the TLDS below (a host under .shop,
.online or .com.au passes) and the path rules only drive, /Users, /home, \\Users, /mnt and UNC
forms (a folder under ~/Projects or /var/www passes). The denylist stands behind them.

Output: one line per finding, `path:line: rule: excerpt` (line 0 = the file path itself), then
`# files N, findings M`. Exit 0: no findings in N > 0 files. Exit 1: findings. Exit 2: no files,
a usage, denylist, allowlist or git error, or any internal error.

Denylist: `--denylist FILE` or $HARNESS_SWEEP_DENYLIST, required unless --no-denylist. One
case-insensitive regex per line; `#` comments and blank lines are skipped. Terms and text are
compared casefolded, NFD with nonspacing marks and format characters removed; a term that cannot
be folded safely is an error. Terms are never printed except inside a finding's excerpt; keep the
file outside the repo.

Allowlist scripts/sweep-allow.txt: `path<TAB>exact text` lines, `#` comments. A line suppresses
findings lying inside that exact text in that one file; `path<TAB><binary>` allows that file to
be binary. Encoding findings cannot be allowed. A line is reported as `stale-allow` when its file
is not scanned (full-repo mode only), its text is gone, or it suppresses nothing. In repo mode an
index blob is judged only by the index copy of this file, and a working-tree or untracked file only
by the working-tree copy, so a finding in both needs both; an allow file with several index stages
is an error. Stage the allow file before re-sweeping.

Vendored folder: files under VENDORED paths are third-party documents copied unchanged; their
public hosts, e-mail addresses and regex-shaped UNC text (host, email, unc-path) are not findings.
Every other rule, the denylist, encoding and binary checks still apply there, and every rule applies
everywhere else.
"""

import argparse
import os
import re
import stat
import subprocess
import sys
import traceback
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = "scripts/sweep-allow.txt"
# (path prefix, rules not applied there): upstream skills vendored unchanged by rigger-wp's sync script.
VENDORED = (("plugins/rigger-wp/skills/wordpress-router/references/", frozenset(("host", "email", "unc-path"))),)
BINARY_TOKEN = "<binary>"
# The two copies of a tracked file; each holds its own findings and its own allow lines.
INDEX = "index"
WORKTREE = "worktree"
DENYLIST_ENV = "HARNESS_SWEEP_DENYLIST"
EXCERPT_MAX = 80
GIT_TIMEOUT = 60
LINK_MODE = "120000"
SUBMODULE_MODE = "160000"

# Files with these extensions are binary findings without reading their content.
BINARY_EXTENSIONS = frozenset(
    "avif eot gif gz ico jpeg jpg mo mp3 mp4 otf pdf phar png pyc ttf webm webp woff woff2 zip".split()
)

TLDS = ("com", "net", "org", "io", "dev", "info", "biz", "app", "me", "ai", "co")
TLDS += ("eu", "de", "uk", "fr", "nl", "it", "es", "cz", "pl")

# Generic public domains only, matched exactly (a subdomain needs its own entry).
ALLOWED_DOMAINS = frozenset(
    (
        "example.com",
        "github.com",
        "code.claude.com",
        "docs.claude.com",
        "anthropic.com",
        "python.org",
        "git-scm.com",
        "npmjs.com",
        "playwright.dev",
        "wordpress.org",
        # The GPL text in LICENSE links to these two.
        "fsf.org",
        "www.gnu.org",
        # Public documentation hosts cited by plugins/rigger-core/skills/stack-tooling/references.
        "ast-grep.github.io",
        "biomejs.dev",
        "carthage-software.github.io",
        "devblogs.microsoft.com",
        "developer.wordpress.org",
        "docs.phpunit.de",
        "knip.dev",
        "laravel.com",
        "make.wordpress.org",
        "pestphp.com",
        "php.net",
        "phpstan.org",
        "phpunit.de",
        "raw.githubusercontent.com",
        "registry.npmjs.org",
        "repo.packagist.org",
        "vitest.dev",
        "www.php.net",
        "xdebug.org",
    )
)
ALLOWED_EMAIL_DOMAINS = frozenset(("example.com",))
ALLOWED_IPS = frozenset(("127.0.0.1", "0.0.0.0"))

# Letters NFD does not decompose, mapped after casefolding.
EXTRA_FOLD = {ord(letter): base for letter, base in (("\u0142", "l"), ("\u00f8", "o"), ("\u0111", "d"), ("\u0127", "h"))}
CONTROL_CHARS = {code: " " for code in list(range(32)) + [127]}

# A path rule's match runs to the end of the path token, so an allow line must hold all of it.
PATH_TAIL = r"""[^\s"'`)>,]"""


def _ip_is_finding(text):
    return all(int(part) <= 255 for part in text.split(".")) and text not in ALLOWED_IPS


def _email_is_finding(text):
    return text.rpartition("@")[2].lower() not in ALLOWED_EMAIL_DOMAINS


def _host_is_finding(text):
    return text.lower() not in ALLOWED_DOMAINS


RULES = (
    ("drive-path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]" + PATH_TAIL + "*"), None),
    # A backslash \Users counts only where a path starts (line start, whitespace, a quote, ( = :);
    # one after a drive letter is the drive-path rule's. Either slash may separate the parts.
    (
        "user-path",
        re.compile(
            r"(?:/(?:Users|home)[\\/]|(?<![^\s\"'(=:])(?<!(?<![A-Za-z0-9])[A-Za-z]:)\\{1,2}Users[\\/]{1,2})"
            + PATH_TAIL
            + "+",
            re.I,
        ),
        None,
    ),
    ("wsl-path", re.compile(r"/mnt/[a-z]/" + PATH_TAIL + "*", re.I), None),
    ("unc-path", re.compile(r"(?<![\\\w])\\{2,}[A-Za-z0-9._$-]+\\{1,2}[A-Za-z0-9._$-]+" + PATH_TAIL + "*"), None),
    (
        "email",
        re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
        _email_is_finding,
    ),
    ("ipv4", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)"), _ip_is_finding),
    (
        "token",
        re.compile(
            r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|glpat-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{35}"
            r"|AKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9-]{10,})|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY"
        ),
        None,
    ),
    (
        "host",
        re.compile(r"(?<![A-Za-z0-9.-])(?:[a-z0-9-]+\.)*[a-z0-9-]+\.(?:%s)(?!\.?[a-z0-9-])" % "|".join(TLDS), re.I),
        _host_is_finding,
    ),
)


class SweepError(Exception):
    """A usage, denylist, allowlist or git error: exit 2."""


class UnsafeTerm(Exception):
    """A denylist term whose folded form would not mean the same."""


class Target:
    def __init__(self, display, name_text, path=None, blobs=()):
        self.display = display  # path printed in findings and matched by allow lines
        self.name_text = name_text  # part of the path scanned as text
        self.path = path  # filesystem path, read without following links (None: index only)
        self.blobs = list(blobs)  # (git mode, content) of each index entry


class Finding:
    def __init__(self, path, line, rule, text, start, end):
        self.path = path
        self.line = line
        self.rule = rule
        self.text = text
        self.start = start
        self.end = end
        self.copies = {WORKTREE}  # copies of the file that hold it; scan_file sets them

    def key(self):
        return (self.rule, self.text, self.start, self.end)

    def render(self):
        return "%s:%d: %s: %s" % (self.path, self.line, self.rule, excerpt(self.text, self.start, self.end))


def message_finding(path, line, rule, message):
    return Finding(path, line, rule, message, 0, len(message))


class AllowEntry:
    def __init__(self, number, path, text):
        self.number = number
        self.path = path
        self.text = text


@lru_cache(maxsize=None)
def fold_char(char):
    decomposed = unicodedata.normalize("NFD", char.casefold())
    kept = "".join(part for part in decomposed if unicodedata.category(part) not in ("Mn", "Cf"))
    return kept.translate(EXTRA_FOLD)


def fold(text):
    """Return the folded text and a map from each folded position to its original position."""
    if text.isascii():
        return text.lower(), None
    chars = []
    index = []
    for position, char in enumerate(text):
        folded = fold_char(char)
        chars.append(folded)
        index.extend([position] * len(folded))
    return "".join(chars), index


def unfold_span(index, start, end, length):
    if index is None:
        return start, end
    if end > start:
        return index[start], index[end - 1] + 1
    position = index[start] if start < len(index) else length
    return position, position


# Pattern folding: literals are folded like the text; escapes and group syntax are kept.
CODE_ESCAPES = {"x": 2, "u": 4, "U": 8}
GROUP_PREFIXES = ("(?:", "(?=", "(?!", "(?<=", "(?<!")
FLAG_GROUP_RE = re.compile(r"\(\?[aiLmsux]*(?:-[imsx]+)?[:)]")


def _escape(term, position):
    """Read the escape at term[position]; return (literal char or None, raw text, next position)."""
    if position + 1 >= len(term):
        raise UnsafeTerm()
    code = term[position + 1]
    if code in CODE_ESCAPES:
        digits = term[position + 2 : position + 2 + CODE_ESCAPES[code]]
        if len(digits) != CODE_ESCAPES[code] or not all(c in "0123456789abcdefABCDEF" for c in digits):
            raise UnsafeTerm()
        return chr(int(digits, 16)), None, position + 2 + len(digits)
    if code == "N":
        close = term.find("}", position + 3)
        if term[position + 2 : position + 3] != "{" or close == -1:
            raise UnsafeTerm()
        try:
            return unicodedata.lookup(term[position + 3 : close]), None, close + 1
        except KeyError:
            raise UnsafeTerm()
    octal = term[position + 1 : position + 4]
    if len(octal) == 3 and all(c in "01234567" for c in octal):
        return chr(int(octal, 8)), None, position + 4
    if code.isascii() and code.isalnum():
        # Class and anchor escapes (\w, \B, \d …) and backreferences keep their exact form.
        return None, term[position : position + 2], position + 2
    return code, None, position + 2


def _fold_literal(char):
    folded = fold_char(char)
    return re.escape(folded) if len(folded) == 1 else "(?:%s)" % re.escape(folded)


def _fold_class_char(char):
    folded = fold_char(char)
    if len(folded) > 1:
        raise UnsafeTerm()
    return re.escape(folded)


def _fold_range(low, high):
    for test in (str.isdigit, str.islower, str.isupper):
        if low.isascii() and high.isascii() and test(low) and test(high):
            return "%s-%s" % (low.lower(), high.lower())
    if low.isascii() and high.isascii() and not low.isalnum() and not high.isalnum():
        return "%s-%s" % (re.escape(low), re.escape(high))
    raise UnsafeTerm()


def _class_atom(term, position):
    if term[position] == "\\":
        return _escape(term, position)
    return term[position], None, position + 1


def _fold_class(term, position):
    """Fold the character class starting at term[position] == '['; return (text, next position)."""
    out = ["["]
    position += 1
    if term[position : position + 1] == "^":
        out.append("^")
        position += 1
    first = True
    while True:
        if position >= len(term):
            raise UnsafeTerm()
        if term[position] == "]" and not first:
            out.append("]")
            return "".join(out), position + 1
        first = False
        char, raw, position = _class_atom(term, position)
        if term[position : position + 1] == "-" and term[position + 1 : position + 2] not in ("]", ""):
            high, _, position = _class_atom(term, position + 1)
            if char is None or high is None:
                raise UnsafeTerm()
            out.append(_fold_range(char, high))
        elif char is None:
            out.append(raw)
        elif char.isascii():
            out.append(re.escape(char.lower()))
        else:
            out.append(_fold_class_char(char))


def _group_prefix(term, position):
    """Length of the group syntax at term[position:] ('(?…'), kept verbatim."""
    for opener, closer in (("(?P<", ">"), ("(?P=", ")"), ("(?#", ")"), ("(?(", ")")):
        if term.startswith(opener, position):
            close = term.find(closer, position + len(opener))
            if close == -1:
                raise UnsafeTerm()
            return close + 1 - position
    for prefix in GROUP_PREFIXES:
        if term.startswith(prefix, position):
            return len(prefix)
    match = FLAG_GROUP_RE.match(term, position)
    if match is None:
        raise UnsafeTerm()
    return len(match.group(0))


def fold_pattern(term):
    """Fold a regex term the way the text is folded; raise UnsafeTerm when that is not safe."""
    out = []
    position = 0
    while position < len(term):
        char = term[position]
        if char == "\\":
            literal, raw, position = _escape(term, position)
            if literal is None:
                out.append(raw)
            elif literal.isascii() and not literal.isalnum():
                out.append(re.escape(literal))
            else:
                out.append(_fold_literal(literal))
        elif char == "[":
            text, position = _fold_class(term, position)
            out.append(text)
        elif term.startswith("(?", position):
            length = _group_prefix(term, position)
            out.append(term[position : position + length])
            position += length
        elif char.isascii():
            out.append(char.lower())
            position += 1
        else:
            out.append(_fold_literal(char))
            position += 1
    return "".join(out)


def excerpt(text, start, end):
    text = text.translate(CONTROL_CHARS)
    if len(text) <= EXCERPT_MAX:
        return text.strip()
    if end - start >= EXCERPT_MAX:
        return text[start : start + EXCERPT_MAX]
    left = max(0, min(start - (EXCERPT_MAX - (end - start)) // 2, len(text) - EXCERPT_MAX))
    return text[left : left + EXCERPT_MAX]


def read_utf8(path, what):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SweepError("%s unreadable: %s (%s)" % (what, path, type(exc).__name__))
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SweepError("%s is not UTF-8: %s" % (what, path))


def load_denylist(path):
    text = read_utf8(path, "denylist")
    if text is None:
        raise SweepError("denylist not found: %s" % path)
    terms = []
    for number, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            folded = fold_pattern(line)
        except UnsafeTerm:
            raise SweepError("denylist line %d: cannot fold this term safely" % number)
        try:
            terms.append(re.compile(folded))
        except re.error:
            raise SweepError("denylist line %d: invalid regex" % number)
    if not terms:
        raise SweepError("denylist has no terms: %s" % path)
    return terms


def load_allow(path):
    text = read_utf8(path, "allowlist")
    return [] if text is None else parse_allow(text)


def index_allow(targets):
    """Entries of the allow file's index copy; none when it is not in the index."""
    stages = next((target.blobs for target in targets if target.display == ALLOW_FILE), [])
    if len(stages) > 1:
        raise SweepError("%s has %d index stages: resolve the merge first" % (ALLOW_FILE, len(stages)))
    if not stages:
        return []
    try:
        return parse_allow(stages[0][1].decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise SweepError("allowlist is not UTF-8: index copy of %s" % ALLOW_FILE)


def parse_allow(text):
    entries = []
    for number, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rel, tab, exact = line.partition("\t")
        if not tab or not rel or not exact:
            raise SweepError("%s:%d: expected path<TAB>exact text" % (ALLOW_FILE, number))
        entries.append(AllowEntry(number, rel, exact))
    return entries


def git(root, args, data=None):
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(root),
            input=data,
            stdin=None if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SweepError("git %s failed: %s" % (args[0], type(exc).__name__))
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SweepError("git %s failed (exit %d): %s" % (args[0], proc.returncode, message[0] if message else ""))
    return proc.stdout


def git_names(raw):
    try:
        return [name for name in raw.decode("utf-8").split("\0") if name]
    except UnicodeDecodeError:
        raise SweepError("git ls-files printed a path that is not UTF-8")


def read_blobs(root, oids):
    """Read blobs with one `git cat-file --batch` call; return {oid: bytes}."""
    if not oids:
        return {}
    out = git(root, ["cat-file", "--batch"], ("\n".join(oids) + "\n").encode("ascii"))
    blobs = {}
    position = 0
    for oid in oids:
        newline = out.find(b"\n", position)
        header = out[position:newline].decode("ascii", "replace").split() if newline != -1 else []
        if len(header) != 3 or header[0] != oid or not header[2].isdigit():
            raise SweepError("git cat-file: unexpected output for object %s" % oid)
        size = int(header[2])
        if out[newline + 1 + size : newline + 2 + size] != b"\n":
            raise SweepError("git cat-file: unexpected output for object %s" % oid)
        blobs[oid] = out[newline + 1 : newline + 1 + size]
        position = newline + 1 + size + 1
    return blobs


def repo_targets(root):
    entries = {}
    for record in git_names(git(root, ["ls-files", "-s", "-z"])):
        info, tab, name = record.partition("\t")
        fields = info.split()
        if not tab or len(fields) != 3:
            raise SweepError("git ls-files -s: unexpected record")
        entries.setdefault(name, []).append((fields[0], fields[1]))
    untracked = git_names(git(root, ["ls-files", "-o", "--exclude-standard", "-z"]))
    oids = sorted({oid for stages in entries.values() for mode, oid in stages if mode != SUBMODULE_MODE})
    blobs = read_blobs(root, oids)
    targets = []
    for name, stages in entries.items():
        content = [(mode, blobs.get(oid)) for mode, oid in stages]
        targets.append(Target(name, name, root / name, content))
    for name in untracked:
        if name not in entries:
            targets.append(Target(name, name, root / name))
    return targets


def _raise(error):
    raise error


def display_path(arg):
    """The argument as allow lines name it: relative to the repo root inside it, else as given."""
    try:
        return Path(os.path.abspath(arg)).relative_to(ROOT).as_posix()
    except ValueError:
        return arg


def path_targets(args):
    targets = []
    for arg in args:
        base = Path(arg)
        shown = display_path(arg)
        if base.is_dir() and not base.is_symlink():
            prefix = "" if shown == "." else shown.rstrip("/\\") + "/"
            for dirpath, dirnames, filenames in os.walk(str(base), onerror=_raise):
                dirnames[:] = sorted(name for name in dirnames if name != ".git")
                # os.walk does not descend into linked directories: scan them as links.
                linked = [name for name in dirnames if os.path.islink(os.path.join(dirpath, name))]
                # A worktree's or submodule's `.git` is a file.
                for name in sorted(name for name in filenames + linked if name != ".git"):
                    full = Path(dirpath) / name
                    rel = full.relative_to(base).as_posix()
                    targets.append(Target(prefix + rel, rel, full))
        elif os.path.lexists(arg):
            targets.append(Target(shown, base.name, base))
        else:
            raise SweepError("no such file or directory: %s" % arg)
    return targets


def read_worktree(path):
    """Return (kind, bytes) for a path without following a link; None when it does not exist."""
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "link", os.fsencode(os.readlink(str(path)))
    if stat.S_ISDIR(info.st_mode):
        return "dir", b""
    if not stat.S_ISREG(info.st_mode):
        return "other", b""
    with open(str(path), "rb") as handle:
        return "file", handle.read()


def scan_text(text, terms):
    hits = []
    for name, regex, is_finding in RULES:
        for match in regex.finditer(text):
            if is_finding is None or is_finding(match.group(0)):
                hits.append((name, match.start(), match.end()))
    if terms:
        folded, index = fold(text)
        for term in terms:
            for match in term.finditer(folded):
                start, end = unfold_span(index, match.start(), match.end(), len(text))
                hits.append(("denylist", start, end))
    return hits


def scan_version(target, kind, data, terms, texts, skip_lines=frozenset()):
    """Findings of one version of a file's content; its decoded text is appended to texts.

    Lines whose text is in skip_lines are not scanned.
    """
    if kind == "file":
        name = target.name_text.rstrip("/").rpartition("/")[2]
        extension = name.rpartition(".")[2].lower() if "." in name else ""
        if extension in BINARY_EXTENSIONS:
            return [message_finding(target.display, 0, "binary", "binary file (.%s)" % extension)]
        nul = data.find(b"\0")
        if nul != -1:
            message = "binary content: NUL byte at offset %d" % nul
            return [message_finding(target.display, data.count(b"\n", 0, nul) + 1, "binary", message)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        message = "not UTF-8: byte 0x%02x at offset %d" % (data[exc.start], exc.start)
        return [message_finding(target.display, data.count(b"\n", 0, exc.start) + 1, "encoding", message)]
    texts.append(text)
    findings = []
    for number, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if (line[1:] if number == 1 and line.startswith("\ufeff") else line) in skip_lines:
            continue
        for rule, start, end in scan_text(line, terms):
            findings.append(Finding(target.display, number, rule, line, start, end))
    return findings


def scan_file(target, terms, skip_lines=None):
    """Return the findings of one file and {copy: decoded text of each version scanned}.

    skip_lines is {copy: lines of that copy not to scan}.
    """
    findings = [Finding(target.display, 0, rule, target.name_text, s, e) for rule, s, e in scan_text(target.name_text, terms)]
    versions = []  # (kind, data, copies that hold it)
    for mode, data in target.blobs:
        if mode == SUBMODULE_MODE:
            findings.append(message_finding(target.display, 0, "encoding", "unreadable (submodule, not scanned)"))
        else:
            versions.append(("link" if mode == LINK_MODE else "file", data, {INDEX}))
    current = None
    if target.path is not None:
        try:
            current = read_worktree(target.path)
        except OSError as exc:
            current = None
            findings.append(message_finding(target.display, 0, "encoding", "unreadable (%s)" % type(exc).__name__))
        else:
            # A tracked file missing from the worktree is still scanned through its index blob.
            if current is None and not target.blobs:
                findings.append(message_finding(target.display, 0, "encoding", "unreadable (vanished)"))
        if current is not None and current[0] in ("dir", "other"):
            if not target.blobs:
                message = "unreadable (%s, not scanned)" % ("directory" if current[0] == "dir" else "not a regular file")
                findings.append(message_finding(target.display, 0, "encoding", message))
        elif current is not None:
            same = next((version for version in versions if version[:2] == current), None)
            if same is not None:
                same[2].add(WORKTREE)
            else:
                versions.append(current + ({WORKTREE},))
    for finding in findings:
        finding.copies = ({INDEX} if target.blobs else set()) | ({WORKTREE} if current or not target.blobs else set())
    texts = {INDEX: [], WORKTREE: []}
    seen = {finding.key(): finding for finding in findings}
    for kind, data, copies in versions:
        skip = frozenset().union(*((skip_lines or {}).get(copy, ()) for copy in copies))
        version_texts = []
        for finding in scan_version(target, kind, data, terms, version_texts, skip):
            known = seen.get(finding.key())
            if known is None:
                finding.copies = set(copies)
                seen[finding.key()] = finding
                findings.append(finding)
            else:
                known.copies |= copies
        for copy in copies:
            texts[copy].extend(version_texts)
    return findings, texts


def is_vendored(finding):
    return any(finding.path.startswith(prefix) and finding.rule in rules for prefix, rules in VENDORED)


def allowing_entry(finding, entries):
    if finding.rule == "binary":
        return next((entry for entry in entries if entry.text == BINARY_TOKEN), None)
    for entry in entries:
        position = finding.text.find(entry.text)
        while position != -1:
            if position <= finding.start and finding.end <= position + len(entry.text):
                return entry
            position = finding.text.find(entry.text, position + 1)
    return None


def apply_allowlist(findings, allow, targets, texts, full_repo):
    """allow is {copy: entries}, texts {copy: {path: texts}}. A finding goes only when each copy of
    the file holding it is allowed by the same copy of the allow file."""
    by_path = {copy: {} for copy in allow}
    for copy, entries in allow.items():
        for entry in entries:
            by_path[copy].setdefault(entry.path, []).append(entry)
    used = {copy: set() for copy in allow}
    kept = []
    for finding in findings:
        if finding.rule == "encoding":
            kept.append(finding)
            continue
        allowed = True
        for copy in finding.copies:
            entry = allowing_entry(finding, by_path[copy].get(finding.path, ()))
            if entry is None:
                allowed = False
            else:
                used[copy].add(entry.number)
        if not allowed:
            kept.append(finding)
    names = {target.display: target.name_text for target in targets}
    reported = set()
    for copy, entries in allow.items():
        for entry in entries:
            if entry.number in used[copy] or (entry.path, entry.text) in reported:
                continue
            if entry.path not in names:
                if not full_repo:
                    continue
                reason = "file not scanned"
            elif (
                entry.text != BINARY_TOKEN
                and not any(entry.text in text for text in texts[copy].get(entry.path, ()))
                and entry.text not in names[entry.path]
            ):
                reason = "text not found"
            else:
                reason = "suppresses no finding"
            reported.add((entry.path, entry.text))
            kept.append(message_finding(ALLOW_FILE, entry.number, "stale-allow", "%s: %s" % (reason, entry.path)))
    return kept


def run(argv):
    parser = argparse.ArgumentParser(description="Sweep files for private data (fail closed).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--denylist", metavar="FILE", help="denylist file (default: $%s)" % DENYLIST_ENV)
    group.add_argument("--no-denylist", action="store_true", help="scan without a denylist")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="files or directories (default: the repo)")
    args = parser.parse_args(argv)

    if args.no_denylist:
        terms = []
        print("# denylist terms 0 (--no-denylist)")
    else:
        source = args.denylist if args.denylist is not None else os.environ.get(DENYLIST_ENV, "")
        if not source:
            raise SweepError("no denylist: pass --denylist FILE, set %s, or pass --no-denylist" % DENYLIST_ENV)
        terms = load_denylist(source)
        print("# denylist terms %d" % len(terms))

    worktree_entries = load_allow(ROOT / ALLOW_FILE)
    full_repo = not args.paths
    targets = repo_targets(ROOT) if full_repo else path_targets(args.paths)
    allow = {INDEX: index_allow(targets), WORKTREE: worktree_entries} if full_repo else {WORKTREE: worktree_entries}
    allow_paths = {entry.path for entries in allow.values() for entry in entries}
    scanned = {target.display for target in targets}
    # The allow file's own entry lines hold the allowed texts, so a copy of it does not scan the
    # lines it loaded; apply_allowlist reports each one in use or stale. Path mode checks no entry
    # whose file it does not scan, so those lines are scanned.
    entry_lines = {
        copy: frozenset(entry.path + "\t" + entry.text for entry in entries if full_repo or entry.path in scanned)
        for copy, entries in allow.items()
    }
    findings = []
    texts = {copy: {} for copy in allow}
    for target in targets:
        file_findings, file_texts = scan_file(target, terms, entry_lines if target.display == ALLOW_FILE else None)
        findings.extend(file_findings)
        if target.display in allow_paths:
            for copy in allow:
                texts[copy][target.display] = file_texts[copy]
    findings = [finding for finding in findings if not is_vendored(finding)]
    findings = apply_allowlist(findings, allow, targets, texts, full_repo)
    findings.sort(key=lambda finding: (finding.path, finding.line, finding.start))
    for finding in findings:
        print(finding.render())
    print("# files %d, findings %d" % (len(targets), len(findings)))
    if not targets:
        return 2
    return 1 if findings else 0


def main(argv=None):
    try:
        return run(argv)
    except SweepError as exc:
        print("# error: %s" % exc)
        return 2
    except Exception as exc:  # fail closed: never exit 0 after an unexpected error
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print("# internal error: %s in %s line %d" % (type(exc).__name__, frame.name, frame.lineno))
        return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        sys.exit(2)
    sys.exit(main())
