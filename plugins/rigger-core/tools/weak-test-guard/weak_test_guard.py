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

The command is split into words and simple commands (quotes, PowerShell here-strings, bash heredocs, a bash
`"$(...)"` as one word, the body when it is `cat` of a heredoc, `$'...'`, `$((...))`; bash keywords, `env`,
`command`, `time`, `nohup` and `exec` in front of a program are dropped). A commit is a simple command whose
program is git and whose subcommand is `commit`; `cd`, `Set-Location` and `git -C` before it set the folder. Every
commit of the command is read (a `--dry-run` is none): their staging adds up, and the index is diffed once against
HEAD (`HEAD~1` when the first one amends). When the command also stages (`git add`/`git rm` before a commit,
`commit -a`, `-i <paths>`), those are replayed on a copy of the index; a single `git commit <paths>` without `-i`
commits HEAD plus those paths, replayed on `read-tree HEAD`. Redirections are no words; in Bash a staged word with
`* ? [ {` is replayed as git reads it (see "cannot tell" below). The replay uses `GIT_INDEX_FILE` (`git rm` as
`rm --cached`) and that copy is diffed; `git add` writes the blobs it stages into the object store. The hook runs
before the command is approved, so git runs with `core.fsmonitor=false`,
`log` with `--no-show-signature`, the replay with every filter driver of its folder switched off, and a
reused ref that looks like an option is not read. A merge, cherry-pick or revert in progress is not checked,
nor a commit after a `git merge`, `pull`, `cherry-pick`, `revert` or `rebase` in the same command when that is the
only step that changes the index (`--abort` and `--quit` are index writers; `--help` and `--show-current-patch`
change nothing).

Denied as "cannot tell what this commit holds", with a hint to run that step in its own call (a stated reason still
passes): another index writer before the commit (`git checkout`, `switch`, `restore`, `reset`, `stash`, `mv`,
`apply`, `am`, `read-tree`, `update-index`, `--pathspec-from-file`; not `checkout -b`/`switch -c <name>` alone, and
`reset --soft <rev>` becomes the diff base), a history step with another change to the index, a file writer (`rm`,
`mv`, `cp` and `Copy-Item` by their destination, `sed -i`, `Remove-Item`, `Move-Item`, `Set-Content`,
`Add-Content`, `Out-File`, their PowerShell aliases, any redirection target) whose targets hold a tracked test
file, or a variable, `~`, backtick or brace, when a later `git add`, `-a` or pathspec picks them up, a replayed
step git rejects, a Bash glob or brace that, read as git does, weakens tests, commits in more than one
repository, a command it cannot read (a `$(` or `$((` or `$'` nothing closes, a `case` inside `$(...)`), and
`git [options] commit` in the text outside every commit the parse read (in a string another shell runs, after
`sudo`, `eval` or `timeout`, or only named in an `echo`). A staged path that names no file and no index entry yet
is a new file the command makes, and is left out of the replay.

Fails open: no commit, a `-F <file>` that cannot be read (the command may still write it), `cd` without a
folder, an interactive add, staging outside the repository, another program that writes files (`python x.py`),
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

UNCLEAR = "cannot tell what this commit holds:"
HISTORY = frozenset(("merge", "pull", "cherry-pick", "revert", "rebase"))   # content of commits already made
INDEX_WRITERS = frozenset(("checkout", "switch", "restore", "reset", "stash", "mv", "apply", "am", "read-tree",
                           "update-index"))
# Programs (and PowerShell aliases) that change files a later `git add`, `-a` or pathspec picks up: which of their
# words are targets. A redirection target of any program is one too.
FILE_WRITERS = {"rm": "all", "del": "all", "erase": "all", "ri": "all", "rd": "all", "rmdir": "all",
                "remove-item": "all", "mv": "all", "move": "all", "mi": "all", "move-item": "all", "cp": "copy",
                "copy": "copy", "cpi": "copy", "copy-item": "copy", "sed": "all", "set-content": "all", "sc": "all",
                "add-content": "all", "ac": "all", "out-file": "all"}
NO_FILE = frozenset(("/dev/null", "$null", "nul"))
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
    """One simple command: its words (a here-string is one word; a heredoc body is none), the targets of its
    redirections, and the spans of the text it covers (from the end of the separator before it, and its heredoc
    bodies)."""

    def __init__(self, start=0):
        self.words = []
        self.redirects = []
        self.spans = [[start, start]]


HEREDOC_CAT = re.compile(r"\s*cat\s*<<-?[ \t]*(['\"]?)(\w+)\1[ \t]*\n")
COMMIT_WORD = re.compile(r"(?<=[ \t])commit(?=[\s;&|)\"'`]|$)")
COMMIT_WORD_PS = re.compile(COMMIT_WORD.pattern, re.I)
TOKEN = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|\S+")
LOOKBACK = 24   # tokens between `git` and `commit`: options and the values of -C and -c
KEYWORDS = frozenset(("then", "do", "else", "elif", "if", "while", "until", "!", "{", "time", "command", "nohup",
                      "exec"))


class Unparsed(Exception):
    """A bash construct the guard does not model, or one that does not end."""


def _arith_end(text, i):
    """The index just past the `))` closing the bash `$((` at i."""
    depth, j = 0, i + 1
    while j < len(text):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        j += 1
        if depth == 0:
            return j
    raise Unparsed("an unclosed `$((`")


def _ansi_end(text, i):
    """The index just past the `'` closing the bash `$'` at i (a backslash escapes the next character)."""
    j = i + 2
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == "'":
            return j + 1
        else:
            j += 1
    raise Unparsed("an unclosed `$'`")


def _substitution_end(text, i):
    """The index just past the `)` that closes the bash `$(` at i. Quotes, `$'...'`, `$((...))`, comments, nested
    substitutions and heredoc bodies inside it are skipped as bash reads them, so a `"` or `)` there ends nothing.
    Raises Unparsed when nothing closes it or it holds a `case` (whose patterns end in `)`)."""
    if text.startswith("$((", i):
        return _arith_end(text, i)
    stack, j, n, pending = ["("], i + 2, len(text), []
    while j < n and stack:
        c = text[j]
        if stack[-1] == '"':
            if c == "\\":
                j += 2
            elif c == '"':
                stack.pop()
                j += 1
            elif text.startswith("$((", j):
                j = _arith_end(text, j)
            elif text.startswith("$(", j):
                stack.append("(")
                j += 2
            else:
                j += 1
        elif c == "\\":
            j += 2
        elif text.startswith("$((", j):
            j = _arith_end(text, j)
        elif text.startswith("$'", j):
            j = _ansi_end(text, j)
        elif c == "'":
            end = text.find("'", j + 1)
            j = n if end < 0 else end + 1
        elif c == "#" and text[j - 1] in " \t\n;|&(":
            end = text.find("\n", j)
            j = n if end < 0 else end
        elif re.match(r"case\s", text[j:j + 5]) and text[j - 1] in " \t\n;|&(":
            raise Unparsed("a `case` inside `$(...)`")
        elif c in '"(':
            stack.append(c)
            j += 1
        elif c == ")":
            stack.pop()
            j += 1
        elif text.startswith("<<", j) and not text.startswith("<<<", j):
            k = j + 2 + (text[j + 2:j + 3] == "-")
            while k < n and text[k] in " \t":
                k += 1
            start = k
            while k < n and text[k] not in " \t\n;|&<>()":
                k += 1
            pending.append(text[start:k].strip("'\""))
            j = k
        elif c == "\n" and pending:
            for delimiter in pending:
                line = re.compile(r"^[ \t]*%s[ \t]*$" % re.escape(delimiter), re.M).search(text, j + 1)
                j = line.end() if line else n
            pending = []
        else:
            j += 1
    if stack:
        raise Unparsed("an unclosed `$(`")
    return j


def _substitution_word(inner):
    """The word bash makes of `$(<inner>)` when it is `cat` of one heredoc (its body without the trailing newlines),
    else the text itself."""
    head = HEREDOC_CAT.match(inner)
    if head:
        lines = inner[head.end():].split("\n")
        for k, line in enumerate(lines):
            if line.strip(" \t") == head.group(2):
                if not "".join(lines[k + 1:]).strip():
                    return "\n".join(lines[:k]).rstrip("\n")
                break
    return "$(" + inner + ")"


def split_command(text, powershell):
    """[Simple] of a shell command. A redirection and its target are no words; a bash `"$(...)"` is one word."""
    out, current = [], Simple()
    word, has_word, drop_next = [], False, False
    pending = []   # bash heredoc delimiters waiting for the end of the line
    i, n = 0, len(text)

    def end_word():
        nonlocal word, has_word, drop_next
        if has_word:
            if drop_next:
                drop_next = False
                current.redirects.append("".join(word))
            else:
                current.words.append("".join(word))
        word, has_word = [], False

    def end_simple():
        nonlocal current, drop_next
        end_word()
        drop_next = False
        current.spans[0][1] = i
        if current.words or current.redirects:
            out.append(current)
        current = Simple(i)

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
            for delimiter, owner in pending:
                end = text.find("\n" + delimiter + "\n", i - 1)
                stop = end if end >= 0 else n
                owner.spans.append([i, stop])
                i = stop + len(delimiter) + 2 if end >= 0 else n
            pending = []
            current = Simple(i)
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
            pending.append((text[j:k].strip("'\""), current))
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
        elif not powershell and text.startswith(("$'", "$(("), i):
            end = (_ansi_end if text[i + 1] == "'" else _arith_end)(text, i)
            word.append(text[i:end])
            has_word = True
            i = end
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
                if not powershell and text.startswith("$(", j):
                    end = _substitution_end(text, j)
                    word.append(_substitution_word(text[j + 2:end - 1]))
                    j = end
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
        self.unclear = None         # why a step before the commit cannot be replayed
        self.touched = []           # (folder, program, targets) of file writes a later staging step picks up
        self.base = None            # the commit a `git reset --soft <rev>` before it moves HEAD to
        self.parts = [self]         # every commit of the command this one adds up


class Unclear(Exception):
    """The guard cannot tell what the commit holds: the reason, and the hint the denial gives."""

    def __init__(self, why, hint=None):
        Exception.__init__(self, why)
        self.hint = hint or ("Run that step in its own call and commit in the next one, so the guard reads what it "
                             "left")


READ_HINT = "Commit in a command of its own, with the message in `-m` or in a file read by `-F`"
RUN_HINT = "Run `git commit` itself in the command, not in a string, a function or another shell"


def _copy_targets(args):
    """The destination of a `cp` or `Copy-Item`: the value of `-t`, `--target-directory` or `-Destination`, else the
    last word that is no option."""
    for k, arg in enumerate(args):
        name, eq, value = arg.partition("=")
        if name in ("-t", "--target-directory") or name.lower().startswith("-dest"):
            return [value] if eq else args[k + 1:k + 2]
    return [a for a in args if not a.startswith("-")][-1:]


def _command_words(words, powershell):
    """The words from the program on: bash assignments, keywords and `env` with its options in front dropped."""
    while words and not powershell:
        if re.match(r"[A-Za-z_]\w*=", words[0]) or words[0] in KEYWORDS:
            words = words[1:]
        elif words[0] == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or re.match(r"[A-Za-z_]\w*=", words[0])):
                words = words[1:]
        else:
            break
    return words


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
    """The Commit the command's `git commit`s add up to (the last one, holding the others in `parts`), None when
    there is none, a commit's folder is unknown, or they follow a history step. Raises Unclear as the docstring above
    says."""
    try:
        parsed = split_command(command, powershell)
    except Unparsed as why:
        if re.search(r"\bgit\b[\s\S]*\bcommit\b", command):
            raise Unclear("the guard cannot read the command (%s)" % why, READ_HINT)
        return None
    simples = [(_command_words(simple.words, powershell), simple) for simple in parsed]
    _unread_commit(command, [s for words, s in simples
                             if words and (_git_parts(words, cwd, powershell) or "--")[1] == "commit"], powershell)
    folder = cwd
    stages, readers, commits = [], [], []
    unclear, base, writes, touched, history, dirty = None, None, [], [], None, False
    for words, simple in simples:
        targets = [t for t in simple.redirects if t.lower() not in NO_FILE]
        if targets:
            writes.append((folder, (words or ["a redirection"])[0], targets))
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
        program = re.sub(r"\.exe$", "", head.replace("\\", "/").rsplit("/", 1)[-1])
        kind = FILE_WRITERS.get(program)
        if kind and (program != "sed" or any(w.startswith("--in-place") or re.match(r"-[^-]*i", w) for w in words[1:])):
            names = [w for w in words[1:] if not w.startswith("-")]
            writes.append((folder, words[0], _copy_targets(words[1:]) if kind == "copy" else names))
            continue
        parts = _git_parts(words, folder, powershell)
        if parts is None:
            continue
        git_folder, sub, args = parts
        if any(a in ("--help", "-h", "--show-current-patch") for a in args):
            continue
        if any(a.startswith("--pathspec-from-file") for a in args) and sub in ("add", "rm", "commit"):
            unclear = unclear or "`--pathspec-from-file` names its paths in a file"
        if sub in HISTORY and not any(a in ("--abort", "--quit") for a in args):
            history = history or sub   # what follows commits content the guard read when it was committed
            continue
        if sub in INDEX_WRITERS or sub in HISTORY:
            revs = [a for a in args if not a.startswith("-")]
            loud = [a for a in args if a not in ("-q", "--quiet")]
            if sub == "reset" and "--soft" in args and len(revs) <= 1:
                base = revs[0] if revs else base
            elif not (sub in ("checkout", "switch") and len(loud) == 2 and loud[0] in ("-b", "-B", "-c", "-C")):
                unclear, dirty = unclear or "`git %s` runs before it" % sub, True
        if sub in ("add", "rm"):
            stages.append((sub, git_folder, args))
            touched, writes, dirty = touched + writes, [], True
        elif sub == "commit":
            commit = Commit()
            _commit_options(commit, args)
            if commit.dry_run:
                continue
            if git_folder is None:
                return None
            if commit.all or commit.pathspecs:
                touched, writes, dirty = touched + writes, [], True
            if history:
                if dirty:
                    raise Unclear("`git %s` and another change to the index run before it" % history)
                continue
            commit.folder, commit.stages, commit.powershell = git_folder, list(stages), powershell
            commit.message_sources, commit.touched, dirty = readers, list(touched), False
            commit.unclear, commit.base = unclear, base   # a step after the last commit changes nothing it holds
            commits.append(commit)
            if commit.all:
                stages.append(("add", git_folder, ["-u"]))
            if commit.pathspecs:
                stages.append(("add", git_folder, ["--"] + commit.pathspecs))
    if not commits:
        return None
    commit = commits[-1]
    commit.parts, commit.amend = commits, commits[0].amend
    if len(commits) > 1:
        commit.include = True   # the paths join what the earlier commits staged
    return commit


def _unread_commit(command, read, powershell):
    """Raise Unclear when the text holds `git [options] commit` outside every commit the parse read. Loose on
    purpose: it knows no wrapper, so text that only names a commit (`echo git commit`) is refused too."""
    covered = [span for simple in read for span in simple.spans]
    for match in (COMMIT_WORD_PS if powershell else COMMIT_WORD).finditer(command):
        at = match.start()
        if any(start <= at < end for start, end in covered):
            continue
        line = command[max(command.rfind("\n", 0, at) + 1, at - 40 * LOOKBACK):at]
        tokens = TOKEN.findall(line)[-LOOKBACK:]
        k = len(tokens) - 1
        while k >= 0:
            name = tokens[k].strip("\"'`($").replace("\\", "/").rsplit("/", 1)[-1]
            if (name.lower() if powershell else name) in ("git", "git.exe"):
                raise Unclear("a git commit the guard could not parse stands in it", RUN_HINT)
            if tokens[k].startswith("-"):
                k -= 1
            elif k >= 1 and tokens[k - 1] in ("-C", "-c"):
                k -= 2
            else:
                break


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


class GitFailed(RuntimeError):
    pass


def _git(args, cwd, env=None):
    left = _deadline[0] - time.monotonic()
    if left <= 0:
        raise RuntimeError("out of time")
    proc = subprocess.run(GIT + args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=min(GIT_TIMEOUT, left), env=_git_env(env))
    if proc.returncode != 0:
        raise GitFailed("git %s failed" % args[0])
    return proc.stdout


def _holds_tests(folder, targets, top):
    """True when a target names a tracked test file or a folder or glob that holds one, or the shell expands it
    (a variable, `~`, a backtick, a brace) to a name the guard cannot see."""
    if any(ch in t for t in targets for ch in "$~`{"):
        return True
    specs = [t if any(ch in t for ch in "*?[") else ":(literal)" + t for t in targets
             if any(ch in t for ch in "*?[") or _inside(os.path.join(folder, t), top)]
    if not specs:
        return False
    try:
        listed = _git(["ls-files", "-z", "--"] + specs, folder).decode("utf-8", "replace")
    except GitFailed:
        return True   # a target git cannot read (a glob outside the repository): it may hold tests
    return any(is_test_path(path) for path in listed.split("\0") if path)


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
    """True when the message of the command or of one of its commits states a reason."""
    return has_reason(command) or any(_stated_one(part) for part in commit.parts)


def _stated_one(commit):
    """True when the commit's message states a reason (read without git where it can be)."""
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
    """True when `folder` is `top` or under it, compared by file identity: git prints `top` resolved, while the folder
    may come through a link (macOS /var), in another case or as a Windows 8.3 short name."""
    try:
        top_stat = os.stat(top)
        path = os.path.realpath(folder)
        while True:
            if os.path.samestat(os.stat(path), top_stat):
                return True
            up = os.path.dirname(path)
            if up == path:
                return False
            path = up
    except OSError:
        return False


def _replay(commit, index, top):
    """Apply the command's staging to the index copy at `index`; False when it cannot be replayed. Raises Unclear
    when git rejects a replayed step."""
    env = {"GIT_INDEX_FILE": index}
    off = {}

    def git_off(args, folder, step):
        if folder not in off:
            off[folder] = _no_filters(folder)
        try:
            _git(off[folder] + args, folder, env)
        except GitFailed:
            raise Unclear("replaying `git %s` failed" % " ".join(step)[:80])

    only = bool(commit.pathspecs) and not commit.include
    if only:
        _git(["read-tree", "HEAD"], top, env)   # with --amend too: HEAD's tree plus the paths, diffed against HEAD~1
    else:
        for sub, folder, args in commit.stages:
            if folder is None or not _inside(folder, top) or _interactive(args):
                return False
            if sub == "add":
                if any(a in ("-n", "--dry-run") for a in args):
                    continue
                new = _not_yet(args, folder, env)
                if new and all(a in new or a.startswith("-") for a in args):
                    continue
                git_off(["add"] + [a for a in args if a not in new], folder, [sub] + args)
            else:
                paths = [a for a in args if not a.startswith("-") or a == "--"]
                git_off(["rm", "--cached", "-r", "-q", "--ignore-unmatch"] + paths, folder, [sub] + args)
        if commit.all:
            git_off(["add", "-u"], top, ["commit", "-a"])
    if commit.pathspecs:
        git_off(["add", "--"] + commit.pathspecs, commit.folder, ["commit", "--"] + commit.pathspecs)
    return True


def _not_yet(args, folder, env):
    """The literal paths of a `git add` that name nothing yet, no file and no index entry: the same command makes
    them, so they are new files, which remove no test. A magic pathspec (`:(...)`) is never one."""
    paths = [a for a in args if not a.startswith(("-", ":")) and not any(ch in a for ch in "*?[{")
             and not os.path.lexists(os.path.join(folder, a))]
    if not paths:
        return set()
    listed = _git(["ls-files", "-z", "--"] + [":(literal)" + p for p in paths], folder, env).decode("utf-8", "replace")
    tracked = [path for path in listed.split("\0") if path]
    return {p for p in paths
            if not any(t == p.replace("\\", "/").rstrip("/") or t.startswith(p.replace("\\", "/").rstrip("/") + "/")
                       for t in tracked)}


def _globbed(commit):
    """True when a staged word holds a Bash glob or brace, which the shell and git expand differently."""
    only = bool(commit.pathspecs) and not commit.include
    words = [a for _, _, args in ([] if only else commit.stages) for a in args] + commit.pathspecs
    return not commit.powershell and any(ch in a for a in words for ch in "*?[{")


def check(command, cwd, powershell):
    """The denial reason for a commit command, or None."""
    _deadline[0] = time.monotonic() + DEADLINE
    if not cwd:
        return None
    try:
        commit = read_command(command, cwd, powershell)
    except Unclear:
        if has_reason(command):
            return None
        raise
    if commit is None or commit.dry_run or _stated(commit, command):
        return None
    paths = _git(["rev-parse", "--show-toplevel", "--git-path", "index", "--git-path", "MERGE_HEAD",
                  "--git-path", "CHERRY_PICK_HEAD", "--git-path", "REVERT_HEAD"],
                 commit.folder).decode("utf-8", "replace").splitlines()
    top = paths[0]
    if any(os.path.exists(os.path.join(commit.folder, p)) for p in paths[2:]):
        return None
    if commit.unclear:
        raise Unclear(commit.unclear)
    for folder in {os.path.normcase(os.path.abspath(part.folder)) for part in commit.parts} - {
            os.path.normcase(os.path.abspath(commit.folder))}:
        try:
            other = _git(["rev-parse", "--show-toplevel"], folder).decode("utf-8", "replace").strip()
        except (GitFailed, OSError):
            other = None
        if other is None or not _inside(other, top) or not _inside(top, other):
            raise Unclear("commits in more than one repository")
    for folder, program, targets in commit.touched:
        if folder is None or _holds_tests(folder, targets, top):
            raise Unclear("`%s` changes test files a later step stages" % program)
    base = (commit.base or "HEAD") + ("~1" if commit.amend else "")
    args =["diff", "-M", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/",
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
    if _globbed(commit):
        raise Unclear("a glob or brace in the staged paths is expanded by the shell and by git differently; read "
                      "as git does, the commit weakens tests: " + listed)
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
    except Unclear as why:
        reason = ("%s %s %s. %s; if the commit removes or skips tests on purpose, add a `%s <why>` line to its "
                  "message." % (PREFIX, UNCLEAR, why, why.hint, TRAILER))
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
