"""PreToolUse hook on Bash and PowerShell `git commit`: a commit that weakens tests needs a stated reason.

It reads the diff the commit will hold, limited to code files, and finds in test files:
- removed tests: test functions (Python `def test_*`, PHP `function test*` or a method marked `@test` /
  `#[Test]`, Go `func Test*`, JS/TS and Pest `it('…')` / `test('…')`) whose defining line is removed, more of
  them in a file than new test names it gains (fewer: a rename or rewrite), not added back anywhere in the
  commit, and not covering removed code (a name defined on a removed line of a non-test file and defined on
  no added line, or the stem of a deleted code file, is a word of the removed test or, letters and digits
  only, part of its name; a deleted test file whose stem without its test affix is a deleted code file's stem);
- an added skip: `skip`/`only`/`xfail`/`fixme`/`todo` markers of pytest, unittest, Jest/Vitest/Mocha,
  Playwright, PHPUnit, Pest and Go, in a file that existed before and outside a test the commit adds, unless
  the same line is removed elsewhere in the commit (moved);
- fewer assertions: assertion calls on removed lines outnumber those on added lines, across the commit,
  leaving out the lines of removed tests the first point lets through; a commented-out assertion counts as
  removed.
A test file is a code file named like a test (`test_*.py`, `*_test.py|go`, `*.test|spec|cy.<js/ts>`,
`*Test.php`) or under a `test`, `tests`, `__tests__`, `spec`, `specs` or `e2e` folder, outside `vendor`,
`node_modules` and other third-party folders.

Also a removed `@test`/`#[Test]` marker, and a test file renamed to a name no runner collects. A file that
gains a parametrized or data-provider test (`parametrize`, `.each`, Pest `->with(`, `@dataProvider`, Go
`t.Run`) covers the tests it loses: folding cases into a table is no weakening.

With a finding, the commit passes only when a `Test-change: <reason>` line (or `Test-change=`, as `--trailer`
takes it) with at least REASON_MIN characters stands in the command text (up to a quote), the `-F`/`--file`
file, a file a `cat`/`Get-Content` before `git commit -F -` reads, the message `-C`/`-c` reuses, or HEAD's
message on `--amend`. Otherwise the hook denies it and lists the findings. `--dry-run` is no commit.
`-F -` has no other source: a message fed to stdin any other way is judged by the command text alone.

The command is split into words and simple commands (quotes, PowerShell here-strings, bash heredocs). The
commit is the first simple command whose program is git and whose subcommand is `commit`; `cd`,
`Set-Location` and `git -C` before it set the folder. The diff is the index against HEAD (`HEAD~1` with
`--amend`). When the command also stages (`git add`/`git rm` before the commit, `commit -a`, `-i <paths>`),
those are replayed on a copy of the index; `git commit <paths>` without `-i` commits HEAD plus those paths,
replayed on `read-tree HEAD`. Redirections are no words; in Bash a staged word with `* ? [ {` fails open
(the shell and git expand it differently). The replay uses `GIT_INDEX_FILE` (`git rm` as `rm --cached`) and
that copy is diffed. The hook runs before the command is approved, so git runs with `core.fsmonitor=false`,
`log` with `--no-show-signature`, the replay with every filter driver of its folder switched off, and a
reused ref that looks like an option is not read. A merge, cherry-pick or revert in progress is not checked.

Fails open: no commit, a `-F <file>` that cannot be read (the command may still write it), `cd` without a
folder, `git mv`, an interactive add, staging outside the repository,
no repository (a folder named by a variable or `~` is read literally and fails here), a git error, 8 s of
git calls, a diff over MAX_DIFF bytes, or any exception prints nothing.
Loosening an assertion in place (`toBe(3)` to `toBeTruthy()`), a test replaced by a vacuous one, data
provider rows and snapshot updates are not detected.

Usage: python -I -S weak_test_guard.py   (event JSON on stdin)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

PREFIX = "weak-test-guard:"
TRAILER = "Test-change:"
REASON_MIN = 10
MAX_DIFF = 32 << 20
GIT_TIMEOUT = 4.0
DEADLINE = 8.0   # all git calls of one event together; the hook's own timeout is 10 s
_deadline = [0.0]
SHOWN = 8   # findings listed in a denial
SYMBOL_MIN = 5   # shorter defined names (`get`, `user`, `init`) say nothing about which test covered them

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".php", ".go")
TEST_DIRS = frozenset(("test", "tests", "__tests__", "spec", "specs", "e2e"))
THIRD_PARTY = frozenset(("vendor", "node_modules", "vendor_prefixed", "third_party", "third-party", "bower_components"))
TEST_FILE = re.compile(r"^test_.*\.py$|_test\.(?:py|go)$|\.(?:test|spec|cy)\.[cm]?[jt]sx?$|Test\.php$")
TEST_AFFIX = re.compile(r"^test_|_test$|\.(?:test|spec|cy)$|Test$")

TEST_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("                                    # Python
    r"|\bfunction\s+(test\w*)\s*\("                                             # PHPUnit
    r"|^\s*func\s+(Test\w*)\s*\("                                               # Go
    r"|(?:^|[^\w.$])(?:it|test)(?:\.(?:only|skip|concurrent|fixme))?\s*\(\s*(['\"`])(.+?)\4")  # JS/TS, Pest
PHP_METHOD = re.compile(r"\bfunction\s+(\w+)\s*\(")
PHP_TEST_MARK = re.compile(r"@test\b|#\[(?:\\?PHPUnit\\Framework\\Attributes\\)?Test\b")
SKIP = re.compile(
    r"\b(?:pytest\.)?mark\.(?:skip|skipif|xfail)\b|@(?:unittest\.)?(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\bpytest\.(?:skip|xfail)\s*\(|\.skipTest\s*\(|\braise\s+(?:unittest\.)?SkipTest\b"
    r"|\b(?:it|test|describe|context|suite)\.(?:skip|only|todo|fixme|skipIf|runIf)\b"
    r"|(?:^|[^\w.$])(?:xit|xtest|xdescribe|xcontext|fit|fdescribe)\s*\("
    r"|\bmarkTest(?:Skipped|Incomplete)\s*\("
    r"|\)\s*->(?:skip\s*\((?!\s*\d+\s*\))|todo\s*\(|only\s*\(\s*\))"           # Pest; not Collection skip(1)/only([…])
    r"|\bt\.Skip(?:f|Now)?\s*\(")
ASSERT = re.compile(
    r"(?:->|::|\.)assert\w*\s*\(|(?:^|[^\w.$>:])assert\b|(?:^|[^\w$.])expect\w*\s*\((?!\s*\))"
    r"|\bpytest\.raises\s*\(|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(|\brequire\.\w+\s*\(")
DEFINES = re.compile(
    r"\b(?:def|function|class|interface|trait|enum)\s+&?([A-Za-z_]\w*)"
    r"|\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"
    r"|^[ \t]*(?:export[ \t]+)?(?:const|let|var)[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*(?:async\b|function\b|\()")
FOLDS = re.compile(   # a test that runs over a table of cases
    r"\bmark\.parametrize\b|@parameterized\b|\b(?:it|test|describe)\.each\s*[(`\[]|\}\s*\)\s*->with\s*\("
    r"|@dataProvider\b|#\[(?:\w+\\)*DataProvider\b|\bt\.Run\s*\(")
DECORATOR = re.compile(r"\s*(?:@|#\[|/\*\*|\*|$)")   # decorators, attributes, a docblock
COMMENT = re.compile(r"\s*(?:#(?!\[)|//|/\*|\*|<!--|\|(?!\|))")   # `|` opens a Laravel docblock line
STRING = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\\n]|\\.)*`")

CD = frozenset(("cd", "chdir", "pushd", "set-location", "sl", "push-location"))
READERS = frozenset(("cat", "get-content", "gc", "type"))   # a file piped into `git commit -F -`
COMMIT_VALUE_LONG = frozenset(("message", "file", "reuse-message", "reedit-message", "author", "date", "template",
                               "cleanup", "trailer", "fixup", "squash", "pathspec-from-file"))
# The hook runs before the command is approved: no fsmonitor program, and a ref from the command is never an option.
GIT = ["git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false"]
GIT_VALUE_GLOBAL = frozenset(("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"))


def is_test_path(path):
    parts = path.replace("\\", "/").split("/")
    if not parts[-1].endswith(CODE_EXT) or any(part.lower() in THIRD_PARTY for part in parts[:-1]):
        return False
    return bool(TEST_FILE.search(parts[-1])) or any(part.lower() in TEST_DIRS for part in parts[:-1])


def stem(path, affix=None):
    """The file name without its extension (and `affix`, matched before folding case), lowercase."""
    name = os.path.splitext(path.replace("\\", "/").rsplit("/", 1)[-1])[0]
    return (affix.sub("", name) if affix else name).lower()


def test_name(line):
    match = TEST_DEF.search(line)
    if not match:
        return None
    return next(group for group in (match.group(1), match.group(2), match.group(3), match.group(5)) if group)


def code_part(line):
    """The line with its one-line string literals emptied, or "" when it is a comment."""
    return "" if COMMENT.match(line) else STRING.sub("''", line)


class FileDiff:
    def __init__(self, path):
        self.path = path
        self.deleted = False
        self.new = False
        self.old_path = None   # a rename's source
        self.hunks = []   # each a list of (sign, text)


def _strip_prefix(path):
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse_diff(text):
    """Unified diff text (git diff -M) -> [FileDiff]. A renamed file keeps its new path, a deleted one its old."""
    files, current, hunk = [], None, None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("diff --git "):
            rest = line[len("diff --git "):]
            half = rest.find(" b/")
            current, hunk = FileDiff(_strip_prefix(rest[half + 1:] if half >= 0 else rest)), None
            files.append(current)
        elif current is None:
            continue
        elif hunk is None and line.startswith("deleted file mode"):
            current.deleted = True
        elif hunk is None and line.startswith("new file mode"):
            current.new = True
        elif hunk is None and line.startswith("rename from "):
            current.old_path = line[len("rename from "):]
        elif hunk is None and line.startswith("+++ "):
            if line[4:] != "/dev/null":
                current.path = _strip_prefix(line[4:])
        elif line.startswith("@@"):
            hunk = []
            current.hunks.append(hunk)
        elif hunk is not None and line[:1] in ("+", "-", " "):
            hunk.append((line[0], line[1:]))
    return files


def _name_at(hunk, i, php):
    """The test name the line at i defines, or None. In PHP a method counts when `@test`/`#[Test]` is among the
    three lines above it."""
    name = test_name(hunk[i][1])
    if name is None and php:
        method = PHP_METHOD.search(hunk[i][1])
        if method and any(PHP_TEST_MARK.search(text) for _, text in hunk[max(0, i - 3):i]):
            name = method.group(1)
    return name


def _runs(hunk, sign, php):
    """(name, lines) of each test whose defining line has `sign`: the decorator or attribute lines just above it,
    then its lines up to the next test or sign change."""
    found = []
    for i, (line_sign, text) in enumerate(hunk):
        name = _name_at(hunk, i, php) if line_sign == sign else None
        if name is None:
            continue
        block = [text]
        for earlier_sign, earlier in reversed(hunk[:i]):
            if earlier_sign != sign or not DECORATOR.match(earlier):
                break
            block.insert(0, earlier)
        for j in range(i + 1, len(hunk)):
            if hunk[j][0] != sign or _name_at(hunk, j, php) is not None:
                break
            block.append(hunk[j][1])
        found.append((name, block))
    return found


def findings(diff_text):
    """Findings of a unified diff, as short English lines."""
    files = [f for f in parse_diff(diff_text) if f.path.endswith(CODE_EXT)]
    tests = [f for f in files if is_test_path(f.path)]
    code = [f for f in files if not is_test_path(f.path)
            and not any(part.lower() in THIRD_PARTY for part in f.path.split("/"))]

    defined_removed, defined_added = set(), set()
    for f in files:
        for hunk in f.hunks:
            for sign, text in hunk:
                if sign == "+" or (sign == "-" and f in code):
                    for match in DEFINES.finditer(text):
                        name = next(group for group in match.groups() if group).lower()
                        (defined_added if sign == "+" else defined_removed).add(name)
    gone_stems = {stem(f.path) for f in code if f.deleted}
    symbols = {s for s in (defined_removed - defined_added) | gone_stems if len(s) >= SYMBOL_MIN}

    per_file, added_names = [], set()
    removed_lines, added_lines = [], []
    for f in tests:
        php = f.path.endswith(".php")
        gone, born = [], []
        for hunk in f.hunks:
            gone.extend(_runs(hunk, "-", php))
            born.extend(_runs(hunk, "+", php))
            removed_lines.extend(text for sign, text in hunk if sign == "-")
        new = {name for name, _ in born}
        old_names = {name for name, _ in gone}
        new_test_lines = {line for name, block in born if name not in old_names for line in block}
        for hunk in f.hunks:
            added_lines.extend((f, text, text in new_test_lines) for sign, text in hunk if sign == "+")
        added_names |= new
        per_file.append((f, gone, new))

    out, covered_lines = [], set()
    squashed_symbols = {re.sub(r"[^a-z0-9]", "", s) for s in symbols}
    for f, gone, new in per_file:
        kept = []
        file_covers = f.deleted and stem(f.path, TEST_AFFIX) in gone_stems
        folds = any(FOLDS.search(code_part(text)) for g, text, _ in added_lines if g is f)
        for name, block in gone:
            squashed = re.sub(r"[^a-z0-9]", "", name.lower())
            if (name in new or name in added_names or file_covers or folds
                    or any(symbol in squashed for symbol in squashed_symbols)):
                if name not in new and name not in added_names:
                    covered_lines.update(line.strip() for line in block)
                continue
            kept.append((name, block))
        fresh = len(new - {name for name, _ in gone})
        if len(kept) > fresh:
            names = ", ".join("`%s`" % name for name, _ in kept[:3]) + (", ..." if len(kept) > 3 else "")
            out.append("removed test%s %s in %s%s" % ("s" if len(kept) > 1 else "", names, f.path,
                                                      " (file deleted)" if f.deleted else ""))
        in_blocks = {line.strip() for _, block in gone for line in block}
        re_added = {text.strip() for g, text, _ in added_lines if g is f}
        for hunk in f.hunks:
            for sign, text in hunk:
                if (sign == "-" and f.path.endswith(".php") and PHP_TEST_MARK.search(text)
                        and text.strip() not in in_blocks
                        and text.strip() not in re_added):
                    out.append("removed test marker in %s: %s" % (f.path, text.strip()[:80]))
    for f in files:
        if f.old_path and is_test_path(f.old_path) and not is_test_path(f.path):
            out.append("test file renamed to a name no runner collects: %s -> %s" % (f.old_path, f.path))

    removed_texts = {text.strip() for text in removed_lines}
    for f, text, in_new_test in added_lines:
        code_text = code_part(text)
        if (code_text and not f.new and not in_new_test and SKIP.search(code_text)
                and text.strip() not in removed_texts):
            out.append("added skip in %s: %s" % (f.path, text.strip()[:80]))

    gone = sum(len(ASSERT.findall(code_part(text))) for text in removed_lines if text.strip() not in covered_lines)
    kept = sum(len(ASSERT.findall(code_part(text))) for _, text, _ in added_lines)
    if gone > kept:
        out.append("assertions in test files: %d removed, %d added" % (gone, kept))
    return out


# --- the command -----------------------------------------------------------------------------------------------

class Simple:
    """One simple command: its words (a here-string is one word; a heredoc body is none)."""

    def __init__(self):
        self.words = []


def split_command(text, powershell):
    """[Simple] of a shell command. A redirection and its target are no words."""
    out, current = [], Simple()
    word, has_word, drop_next = [], False, False
    pending = []   # bash heredoc delimiters waiting for the end of the line
    i, n = 0, len(text)

    def end_word():
        nonlocal word, has_word, drop_next
        if has_word:
            if drop_next:
                drop_next = False
            else:
                current.words.append("".join(word))
        word, has_word = [], False

    def end_simple():
        nonlocal current, drop_next
        end_word()
        drop_next = False
        if current.words:
            out.append(current)
        current = Simple()

    while i < n:
        c = text[i]
        if powershell and not has_word and text.startswith(("@'", '@"'), i) and text[i + 2:i + 3] in ("\n", "\r"):
            closer = "\n" + text[i + 1] + "@"
            end = text.find(closer, i + 2)
            body = text[text.index("\n", i) + 1:end if end >= 0 else n]
            word.append(body)
            has_word = True
            i = end + len(closer) if end >= 0 else n
            continue
        if c in " \t\r":
            end_word()
            i += 1
        elif c == "\n":
            end_simple()
            i += 1
            for delimiter in pending:
                end = text.find("\n" + delimiter + "\n", i - 1)
                stop = end if end >= 0 else n
                i = stop + len(delimiter) + 2 if end >= 0 else n
            pending = []
        elif c == "#" and not has_word:
            end = text.find("\n", i)
            i = end if end >= 0 else n
        elif text.startswith(("&&", "||"), i):
            end_simple()
            i += 2
        elif c in ";|&()" or (powershell and c in "{}"):
            end_simple()
            i += 1
        elif not powershell and text.startswith("<<", i) and not text.startswith("<<<", i):
            end_word()
            j = i + 2 + (text[i + 2:i + 3] == "-")
            while j < n and text[j] in " \t":
                j += 1
            k = j
            while k < n and text[k] not in " \t\n;|&<>":
                k += 1
            pending.append(text[j:k].strip("'\""))
            i = k
        elif c in "<>":
            if has_word and ("".join(word).isdigit() or "".join(word) == "*"):
                word, has_word = [], False   # the stream number of `2>`, `*>`
            end_word()
            j = i
            while j < n and text[j] in "<>":
                j += 1
            if j < n and text[j] == "&":
                j += 1
                while j < n and (text[j].isdigit() or text[j] == "-"):
                    j += 1
            else:
                drop_next = True
            i = j
        elif c == "'":
            j = i + 1
            while True:
                end = text.find("'", j)
                if end < 0:
                    word.append(text[j:])
                    i = n
                    break
                if powershell and text.startswith("''", end):
                    word.append(text[j:end + 1])
                    j = end + 2
                    continue
                word.append(text[j:end])
                i = end + 1
                break
            has_word = True
        elif c == '"':
            escape = "`" if powershell else "\\"
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == escape and j + 1 < n and (powershell or text[j + 1] in "$`\"\\\n"):
                    word.append(text[j + 1])
                    j += 2
                    continue
                word.append(text[j])
                j += 1
            i = j + 1
            has_word = True
        elif c == ("`" if powershell else "\\") and i + 1 < n:
            if text[i + 1] != "\n":
                word.append(text[i + 1])
                has_word = True
            i += 2
        else:
            word.append(c)
            has_word = True
            i += 1
    end_simple()
    return out


def _local_path(path, powershell):
    """A Git Bash drive path (`/<letter>/...`) as a Windows one, on Windows."""
    if os.name == "nt" and not powershell and re.match(r"/[A-Za-z](?:/|$)", path):
        return path[1] + ":" + (path[2:] or "/")
    return path


class Commit:
    def __init__(self):
        self.folder = None
        self.message_file = None
        self.message_sources = []   # files a `cat`/`Get-Content` before `-F -` reads
        self.reuse = None           # -C / -c commit
        self.amend = False
        self.all = False
        self.include = False        # -i: pathspecs join the index; without it a pathspec commit is --only
        self.dry_run = False
        self.new_message = False   # -m / -F: an --amend does not keep HEAD's message
        self.pathspecs = []
        self.stages = []            # (subcommand, folder, args) before the commit
        self.powershell = False


def _git_parts(words, folder, powershell):
    """(folder, subcommand, args) of a git command, folder None when it cannot be read; None when not git."""
    head = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if head not in ("git", "git.exe"):
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):
        option = words[i]
        if option == "-C":
            if i + 1 >= len(words) or folder is None:
                return None, None, []
            folder = os.path.join(folder, _local_path(words[i + 1], powershell))
            i += 2
        elif option in GIT_VALUE_GLOBAL:
            i += 2
        else:
            i += 1
    if i >= len(words):
        return None
    return folder, words[i], words[i + 1:]


def _commit_options(commit, args):
    i = 0
    while i < len(args):
        word = args[i]
        value = args[i + 1] if i + 1 < len(args) else ""
        if word == "--":
            commit.pathspecs.extend(args[i + 1:])
            return
        if word.startswith("--"):
            name, eq, attached = word[2:].partition("=")
            if name in COMMIT_VALUE_LONG and not eq:
                attached, i = value, i + 1
            if name in ("message", "file"):
                commit.new_message = True
            if name == "file":
                commit.message_file = attached
            elif name in ("reuse-message", "reedit-message"):
                commit.reuse = attached
            elif name == "amend":
                commit.amend = True
            elif name == "all":
                commit.all = True
            elif name == "include":
                commit.include = True
            elif name == "dry-run":
                commit.dry_run = True
        elif word.startswith("-") and len(word) > 1:
            for k, flag in enumerate(word[1:], 1):
                if flag in "mFCct":
                    attached = word[k + 1:]
                    if not attached:
                        attached, i = value, i + 1
                    if flag in "mF":
                        commit.new_message = True
                    if flag == "F":
                        commit.message_file = attached
                    elif flag in "Cc":
                        commit.reuse = attached
                    break
                if flag == "S":
                    break   # -S[<keyid>]: the rest is its value
                if flag == "a":
                    commit.all = True
                elif flag == "i":
                    commit.include = True
        else:
            commit.pathspecs.append(word)
        i += 1


def read_command(command, cwd, powershell):
    """The Commit of the first `git commit` in the command, None when there is none or its folder is unknown."""
    folder = cwd
    stages, readers = [], []
    for simple in split_command(command, powershell):
        words = simple.words
        while not powershell and words and re.match(r"[A-Za-z_]\w*=", words[0]):
            words = words[1:]
        if not words:
            continue
        head = words[0].lower()
        if head in CD:
            targets = [w for w in words[1:] if not w.startswith("-")]
            if not targets:
                folder = None
            elif folder is not None:
                folder = os.path.join(folder, _local_path(targets[-1], powershell))
            continue
        if head in READERS and folder is not None:
            readers = [os.path.join(folder, w) for w in words[1:] if not w.startswith("-")]
            continue
        parts = _git_parts(words, folder, powershell)
        if parts is None:
            continue
        git_folder, sub, args = parts
        if sub in ("add", "rm", "mv"):
            stages.append((sub, git_folder, args))
        elif sub == "commit":
            if git_folder is None:
                return None
            commit = Commit()
            commit.folder, commit.stages, commit.powershell = git_folder, stages, powershell
            commit.message_sources = readers
            _commit_options(commit, args)
            return commit
    return None


def has_reason(text):
    """True when a line holds `Test-change:` (or `Test-change=`, as `--trailer` takes it) and at least
    REASON_MIN characters after it, up to a quote that ends the value it stands in."""
    for line in (text or "").splitlines():
        at = max(line.find(TRAILER), line.find(TRAILER[:-1] + "="))
        if at < 0:
            continue
        reason = line[at + len(TRAILER):]
        cut = min([k for k in (reason.find('"'), reason.find("'"), reason.find("`")) if k >= 0] or [len(reason)])
        if len(reason[:cut].strip()) >= REASON_MIN:
            return True
    return False


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(1 << 20)
    except OSError:
        return None


def _git_env(extra=None):
    """The environment of every git call, which runs before the command is approved: no editor, no prompt, and no
    lazy fetch of a missing blob from a promisor remote (git 2.45+; an older git ignores the variable)."""
    env = dict(os.environ, GIT_EDITOR="false", GIT_SEQUENCE_EDITOR="false", GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1")
    env.update(extra or {})
    return env


def _interactive(args):
    """True when a `git add` argument list asks for patch, interactive or edit mode in any spelling git accepts:
    a short cluster holding p, i or e, or a long option abbreviated to a unique prefix (`--ed`)."""
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if len(name) > 2 and any(option.startswith(name) for option in ("--patch", "--interactive", "--edit")):
                return True
        elif arg.startswith("-") and any(ch in "pie" for ch in arg[1:]):
            return True
    return False


def _git(args, cwd, env=None):
    left = _deadline[0] - time.monotonic()
    if left <= 0:
        raise RuntimeError("out of time")
    proc = subprocess.run(GIT + args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=min(GIT_TIMEOUT, left), env=_git_env(env))
    if proc.returncode != 0:
        raise RuntimeError("git %s failed" % args[0])
    return proc.stdout


def _no_filters(folder):
    """`-c` options that switch off every filter driver configured for `folder` (git runs no filter with an empty
    command), so replaying `git add` starts none of their programs; git-lfs installs one globally."""
    left = _deadline[0] - time.monotonic()
    if left <= 0:
        raise RuntimeError("out of time")
    proc = subprocess.run(GIT + ["config", "--name-only", "--get-regexp", r"^filter\."], cwd=folder,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=min(GIT_TIMEOUT, left), env=_git_env())
    if proc.returncode not in (0, 1):
        raise RuntimeError("git config failed")
    drivers = {line.rsplit(".", 1)[0] for line in proc.stdout.decode("utf-8", "replace").splitlines() if "." in line}
    if any("=" in driver for driver in drivers):
        raise RuntimeError("a filter name -c cannot spell")
    options = []
    for driver in sorted(drivers):
        options += ["-c", driver + ".clean=", "-c", driver + ".process=", "-c", driver + ".required=false"]
    return options


def _stated(commit, command):
    """True when the commit's message states a reason (read without git where it can be)."""
    if has_reason(command):
        return True
    if commit.message_file == "-":
        return any(has_reason(_read(path)) for path in commit.message_sources)
    if commit.message_file:
        text = _read(os.path.join(commit.folder, commit.message_file))
        if text is None:
            raise RuntimeError("message file unreadable")   # the same command may still write it: fail open
        return has_reason(text)
    ref = commit.reuse or ("HEAD" if commit.amend and not commit.new_message else None)
    if ref and not ref.startswith("-"):
        return has_reason(_git(["log", "-1", "--no-show-signature", "--format=%B", ref, "--"],
                               commit.folder).decode("utf-8", "replace"))
    return False


def _inside(folder, top):
    try:
        return os.path.commonpath([os.path.abspath(folder), os.path.abspath(top)]) == os.path.abspath(top)
    except ValueError:   # another drive
        return False


def _replay(commit, index, top):
    """Apply the command's staging to the index copy at `index`; False when it cannot be replayed."""
    env = {"GIT_INDEX_FILE": index}
    off = {}

    def git_off(args, folder):
        if folder not in off:
            off[folder] = _no_filters(folder)
        _git(off[folder] + args, folder, env)

    only = bool(commit.pathspecs) and not commit.include
    words = [a for _, _, args in ([] if only else commit.stages) for a in args] + commit.pathspecs
    if not commit.powershell and any(ch in a for a in words for ch in "*?[{"):
        return False   # the shell and git would expand these differently
    if only:
        _git(["read-tree", "HEAD"], top, env)   # with --amend too: HEAD's tree plus the paths, diffed against HEAD~1
    else:
        for sub, folder, args in commit.stages:
            if (folder is None or not _inside(folder, top) or sub == "mv"
                    or _interactive(args)):
                return False
            if sub == "add":
                if any(a in ("-n", "--dry-run") for a in args):
                    continue
                git_off(["add"] + args, folder)
            else:
                paths = [a for a in args if not a.startswith("-") or a == "--"]
                git_off(["rm", "--cached", "-r", "-q", "--ignore-unmatch"] + paths, folder)
        if commit.all:
            git_off(["add", "-u"], top)
    if commit.pathspecs:
        git_off(["add", "--"] + commit.pathspecs, commit.folder)
    return True


def check(command, cwd, powershell):
    """The denial reason for a commit command, or None."""
    _deadline[0] = time.monotonic() + DEADLINE
    if not cwd:
        return None
    commit = read_command(command, cwd, powershell)
    if commit is None or commit.dry_run or _stated(commit, command):
        return None
    paths = _git(["rev-parse", "--show-toplevel", "--git-path", "index", "--git-path", "MERGE_HEAD",
                  "--git-path", "CHERRY_PICK_HEAD", "--git-path", "REVERT_HEAD"],
                 commit.folder).decode("utf-8", "replace").splitlines()
    top = paths[0]
    if any(os.path.exists(os.path.join(commit.folder, p)) for p in paths[2:]):
        return None
    base = "HEAD~1" if commit.amend else "HEAD"
    args = ["diff", "-M", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/",
            "--dst-prefix=b/", "--cached", base, "--"]
    args += ["*" + ext for ext in CODE_EXT]
    if commit.stages or commit.all or commit.pathspecs:
        scratch = tempfile.mkdtemp(prefix="weak-test-guard-")
        try:
            index = os.path.join(scratch, "index")
            shutil.copyfile(os.path.join(commit.folder, paths[1]), index)
            if not _replay(commit, index, top):
                return None
            diff = _git(args, top, {"GIT_INDEX_FILE": index})
        finally:
            shutil.rmtree(scratch, True)
    else:
        diff = _git(args, top)
    if len(diff) > MAX_DIFF:
        return None
    found = findings(diff.decode("utf-8", "replace"))
    if not found:
        return None
    listed = "; ".join(found[:SHOWN]) + ("; and %d more" % (len(found) - SHOWN) if len(found) > SHOWN else "")
    return ("%s this commit weakens tests: %s. If that is intended (the code a test covered is gone, cases folded "
            "into a table, the test was wrong, a flaky test has a task), add a `%s <why>` line to the commit "
            "message as a trailer and commit again; otherwise restore the tests and fix the code."
            % (PREFIX, listed, TRAILER))


def main():
    event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    tool = event.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        return 0
    try:
        reason = check(event["tool_input"]["command"], event.get("cwd"), tool == "PowerShell")
    except RuntimeError:   # a git call failed or ran out of time (a blob a partial clone lacks): fail open
        return 0
    if reason:
        sys.stdout.buffer.write((json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}) + "\n").encode("ascii"))   # ASCII JSON, LF: a text-mode stdout writes CRLF on Windows
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:   # fail open: an event of another shape, a git error or timeout
        sys.exit(0)
