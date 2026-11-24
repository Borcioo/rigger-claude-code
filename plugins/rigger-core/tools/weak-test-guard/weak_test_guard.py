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
`-F -` has no other source: a message fed to stdin any other way is judged by the command text alone. A `-F` file
the command writes or removes before the commit, or one the hook cannot read, states no reason of its own.

The command is split into words and simple commands (quotes, PowerShell here-strings, bash heredocs, a bash
`"$(...)"` as one word, the body when it is `cat` of a heredoc, `$'...'`, `$((...))`; bash keywords, `env`,
`command`, `time`, `nohup` and `exec` in front of a program are dropped). A commit is a simple command whose
program is git and whose subcommand is `commit`; `cd`, `Set-Location` and `git -C` before it set the folder,
`pushd`/`popd` and `Push-Location`/`Pop-Location` push and pop it, and the `)` that closes a bash subshell (also
`$(...)`) restores the folder and stack it opened with (a PowerShell `(...)` or script block restores nothing). In a
folder or `-F` path, a variable the command sets to a literal before (bash `NAME=value` alone or after `export`,
PowerShell `$name = value`, `$env:NAME = value`) is replaced by it when every other appearance of the name is a
plain read (`$name`, `${name}`; quoted text of a commit, such as its message, is no appearance unless a `${`, `$[`
or `[` is open around the name or a `[` follows it; what runs in a commit and its unquoted words are) and it runs
whenever the git step does (top level, after `;`, a newline, or `&&` in
the step's own chain; not piped or in the background), a Git Bash `/<d>/` or `/tmp` path is read as Windows names
it. `wsl [options] git ...` is a git step in the Windows folder WSL starts in, its `--cd` or `-C` (`/mnt/<d>/...`
read as the drive, after `..` and a doubled leading slash are resolved). Every
commit of the command is read (a `--dry-run` is none): their staging adds up, and the index is diffed once against
HEAD (`HEAD~1` when the first one amends). When the command also stages (`git add`/`git rm` before a commit,
`commit -a`, `-i <paths>`), those are replayed on a copy of the index; a single `git commit <paths>` without `-i`
commits HEAD plus those paths, replayed on `read-tree HEAD`. Redirections are no words; in Bash a staged word with
`* ? [ {` is replayed as git reads it (see "cannot tell" below). The replay uses `GIT_INDEX_FILE` (`git rm` as
`rm --cached`) and that copy is diffed; `git add` writes the blobs it stages into the object store. The hook runs
before the command is approved, so git runs with `core.fsmonitor=false`,
`log` with `--no-show-signature`, the replay with every filter driver of its folder switched off, and a
reused ref that looks like an option is not read. A merge, cherry-pick or revert in progress is not checked. A
commit after a `git merge`, `pull`, `cherry-pick`, `revert` or `rebase` in the same command, when that is the only
step that changes the index, is diffed as the index stands when the hook runs: what an earlier call staged is
checked, the history step's content (checked when it was committed) is not there yet (`--abort` and `--quit` are
index writers; a history step's `--help`, `-h` and `--show-current-patch` before `--`, with no
`-m`/`-F`/`-X`/`-s`/`--strategy`, change nothing; a commit is never taken for help).

Denied as "cannot tell what this commit holds", with a hint to run that step in its own call (a stated reason still
passes): another index writer before the commit (`git checkout`, `switch`, `restore`, `reset`, `stash`, `mv`,
`apply`, `am`, `read-tree`, `update-index`, `--pathspec-from-file`; not `checkout -b`/`switch -c <name>` alone, and
`reset --soft <rev>` becomes the diff base), a history step with another change to the index, a file writer (`rm`,
`mv`, `cp` and `Copy-Item` by their destination, `sed -i`, `Remove-Item`, `Move-Item`, `Set-Content`,
`Add-Content`, `Out-File`, their PowerShell aliases, any redirection target) whose targets hold a tracked test
file, or a variable, `~`, backtick or brace, when a later `git add`, `-a` or pathspec picks them up, a replayed
step git rejects, a Bash glob or brace that, read as git does, weakens tests, commits in more than one
repository, a commit or staging step whose folder still holds a variable, substitution or leading `~`, or follows a
folder stack the guard cannot follow (a pop from an empty stack, a bare bash `pushd` with none, `+N`/`-N`, `-n`, a
named PowerShell stack or `-StackName` on `Set-Location`, any `dirs`, a push or pop piped or in the background, a
`)` no `(` or `case` opened, a `)` at the depth of a `case` that a subshell opened before, since that `case` may be
no keyword) until an absolute `cd` names it, a command
it cannot read (a `$(` or `$((` or `$'` nothing closes, a `case` inside `$(...)`), and
a `git` followed by the whole word `commit` (not `--no-commit`, `fix/commit-x`, `commit.gpgsign`, `post-commit`;
`commit-tree` counts) between two of `; & | ( ) { } !` or a newline, anywhere in the text (inside quotes too, at
any distance), unless it lies in the literal text of a commit the parse read: a substitution, backtick,
`$((...))` or unquoted heredoc part runs what it holds and covers nothing. So a commit in a string another shell
runs, after `sudo`, `eval` or `timeout`, inside a substitution, or only named in an `echo` or `git log --grep` is
denied. A staged path that names no file and no index entry yet
is a new file the command makes, and is left out of the replay.

Unchecked by design: a commit in the arguments of `ssh`, or a `wsl` git commit surely in the Linux file system
(an absolute path outside `/mnt/<d>`, or `~`/`~/x` with no `..`; only options before the subcommand that choose no
repository; no GIT_* name in the command), also in a `wsl bash -c|-lc` script with no subshell and no `pushd`,
works on a repository the hook cannot read, so it passes unread. Any other `wsl` commit outside a Windows folder is
denied (a substitution in those arguments runs here and is denied too).

Fails open: no commit, `cd` without a folder (`cd -` too), a `cd` inside a bash pipeline (read as if it stayed),
an interactive add, staging outside the repository, another program that writes files (`python x.py`),
no repository, a git error, 8 s of git calls, a diff over MAX_DIFF bytes, or any exception prints nothing.
Loosening an assertion in place (`toBe(3)` to `toBeTruthy()`), a test replaced by a vacuous one, data
provider rows and snapshot updates are not detected.

Usage: python -I -S weak_test_guard.py   (event JSON on stdin)
"""
import json
import os
import posixpath
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
DEADLINE = 8.0   # all git calls of one event together; the hook's own timeout is 20 s
_deadline = [0.0]
SHOWN = 8   # findings listed in a denial
SYMBOL_MIN = 5   # shorter defined names (`get`, `user`, `init`) say nothing about which test covered them

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".php", ".go")
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
PUSH, POP = ("pushd", "push-location"), ("popd", "pop-location")
STACK_PLAIN = ("--", "-path", "-literalpath", "-passthru")   # options of a push or pop the stack still follows
READERS = frozenset(("cat", "get-content", "gc", "type"))   # a file piped into `git commit -F -`
# Every long option of `git commit` (`git commit --git-completion-helper-all`): git takes any unique prefix of one.
COMMIT_LONG = tuple(
    "quiet verbose file author date message reedit-message reuse-message fixup squash reset-author trailer signoff "
    "template edit cleanup status gpg-sign all include interactive patch unified inter-hunk-context only no-verify "
    "dry-run short branch ahead-behind porcelain long null amend no-post-rewrite untracked-files pathspec-from-file "
    "pathspec-file-nul allow-empty allow-empty-message verify post-rewrite no-quiet no-verbose no-file no-author "
    "no-date no-message no-reedit-message no-reuse-message no-fixup no-squash no-reset-author no-signoff no-template "
    "no-edit no-cleanup no-status no-gpg-sign no-all no-include no-interactive no-patch no-only no-dry-run no-short "
    "no-branch no-ahead-behind no-porcelain no-long no-null no-amend no-untracked-files no-pathspec-from-file "
    "no-pathspec-file-nul no-allow-empty no-allow-empty-message".split())
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
    symbols = {s for s in (defined_removed - defined_added) | gone_stems if len(re.sub(r"[^a-z0-9]", "", s)) >= SYMBOL_MIN}

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

    def __init__(self, start=0, before=""):
        self.words = []
        self.redirects = []
        self.spans = [[start, start]]
        self.runs = []   # spans inside it that run as commands: substitutions, backticks, expanding heredoc parts
        self.quoted = []   # spans of its quoted text: quotes, here-strings, `$'...'`, quoted heredoc bodies
        self.before = before   # the separators since the simple command before it (`;`, `&&`, `||`, `|`, `(`, ...)


HEREDOC_CAT = re.compile(r"\s*cat\s*<<-?[ \t]*(['\"]?)(\w+)\1[ \t]*\n")
# The loose cross-check: a `git` and a later `commit` between two of these separators.
SEGMENT = re.compile(r"[;&|(){}!\n]|\bgit\b|(?<![\w./-])commit(?![\w./])")   # a whole word: not `--no-commit`
SEGMENT_PS = re.compile(SEGMENT.pattern, re.I)
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


def _backtick_end(text, i):
    """The index just past the backtick that closes the one at i (a backslash escapes the next character)."""
    j = i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == "`":
            return j + 1
        else:
            j += 1
    raise Unparsed("an unclosed backtick")


def _runs_nothing(inner):
    """True when `$(<inner>)` is `cat` of one heredoc with a quoted delimiter: its body is text that runs nothing."""
    head = HEREDOC_CAT.match(inner)
    return bool(head and head.group(1)) and _substitution_word(inner) != "$(" + inner + ")"


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

    def end_simple(separator=""):
        nonlocal current, drop_next
        end_word()
        drop_next = False
        current.spans[0][1] = i
        if current.words or current.redirects:
            out.append(current)
            current = Simple(i, separator)
        else:
            current = Simple(i, current.before + separator)

    while i < n:
        c = text[i]
        if powershell and not has_word and text.startswith(("@'", '@"'), i) and text[i + 2:i + 3] in ("\n", "\r"):
            closer = "\n" + text[i + 1] + "@"
            end = text.find(closer, i + 2)
            start = text.index("\n", i) + 1
            body = text[start:end if end >= 0 else n]
            if text[i + 1] == '"' and "$(" in body:   # a subexpression runs
                current.runs.append([start + body.index("$("), start + len(body)])
            word.append(body)
            has_word = True
            current.quoted.append([i, end + len(closer) if end >= 0 else n])
            i = end + len(closer) if end >= 0 else n
            continue
        if c in " \t\r":
            end_word()
            i += 1
        elif c == "\n":
            end_simple("\n")
            i += 1
            for delimiter, owner, quoted in pending:
                end = text.find("\n" + delimiter + "\n", i - 1)
                stop = end if end >= 0 else n
                owner.spans.append([i, stop])
                if quoted:
                    owner.quoted.append([i, stop])
                runs = [k for k in (text.find("$(", i, stop), text.find("`", i, stop)) if k >= 0]
                if not quoted and runs:   # an unquoted body expands, and its substitutions run
                    owner.runs.append([min(runs), stop])
                i = stop + len(delimiter) + 2 if end >= 0 else n
            pending = []
            current = Simple(i, current.before)
        elif c == "#" and not has_word:
            end = text.find("\n", i)
            i = end if end >= 0 else n
        elif text.startswith(("&&", "||"), i):
            end_simple(text[i:i + 2])
            i += 2
        elif c in ";|&()" or (powershell and c in "{}"):
            end_simple(c)
            i += 1
        elif not powershell and text.startswith("<<", i) and not text.startswith("<<<", i):
            end_word()
            j = i + 2 + (text[i + 2:i + 3] == "-")
            while j < n and text[j] in " \t":
                j += 1
            k = j
            while k < n and text[k] not in " \t\n;|&<>":
                k += 1
            pending.append((text[j:k].strip("'\""), current, text[j:j + 1] in "'\"\\"))
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
            if text[i + 1] == "(":
                current.runs.append([i, end])   # arithmetic may hold a substitution
            else:
                current.quoted.append([i, end])
            word.append(text[i:end])
            has_word = True
            i = end
        elif not powershell and c == "`":
            end = _backtick_end(text, i)
            current.runs.append([i, end])
            word.append(text[i:end])
            has_word = True
            i = end
        elif c == "'":
            j, opened = i + 1, i
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
            current.quoted.append([opened, i])
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
                    if not _runs_nothing(text[j + 2:end - 1]):
                        current.runs.append([j, end])
                    word.append(_substitution_word(text[j + 2:end - 1]))
                    j = end
                    continue
                if not powershell and text[j] == "`":
                    end = _backtick_end(text, j)
                    current.runs.append([j, end])
                    word.append(text[j:end])
                    j = end
                    continue
                word.append(text[j])
                j += 1
            if powershell and "$(" in text[i:j]:   # a subexpression runs
                current.runs.append([text.index("$(", i), j])
            current.quoted.append([i, j + 1])
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
    """A Git Bash drive path (`/<letter>/...`) as a Windows one, and `/tmp` as the temp folder Git Bash mounts there,
    on Windows."""
    if os.name == "nt" and not powershell and re.match(r"/[A-Za-z](?:/|$)", path):
        return path[1] + ":" + (path[2:] or "/")
    if os.name == "nt" and not powershell and re.match(r"/tmp(?:/|$)", path):
        return tempfile.gettempdir() + path[4:]
    return path


def _assignments(words, powershell):
    """[(name, value)] a simple command sets for the commands after it: bash `NAME=value ...` alone or after
    `export`, PowerShell `$name = value` and `$env:NAME = value` (names folded to lower case)."""
    if powershell:
        if len(words) == 3 and words[1] == "=" and re.fullmatch(r"\$(?:env:)?\w+", words[0], re.I):
            return [(words[0][1:].lower(), words[2])]
        one = re.fullmatch(r"\$((?:env:)?\w+)=(.*)", words[0], re.I | re.S) if len(words) == 1 else None
        return [(one.group(1).lower(), one.group(2))] if one else []
    words = words[1:] if words[:1] == ["export"] else words
    pairs = [re.fullmatch(r"([A-Za-z_]\w*)=(.*)", word, re.S) for word in words]
    return [(pair.group(1), pair.group(2)) for pair in pairs] if pairs and all(pairs) else []


OPENS, CLOSES = ("if", "for", "while", "until", "case", "select", "{"), ("fi", "done", "esac", "}")
WORD_START = ("", ";", "&", "|", "(", ")", "{", "}", "'", '"', "=", ",", ":")   # a shell word may start after these
WORD_END = ("", ";", "&", "|", "(", ")", "{", "}", "'", '"', "=", "[", "+", ",")


def _bracketed(text):
    """True when `text` leaves a `${`, `$[` or `[` open."""
    open_ = []
    for token in re.finditer(r"\$\{|\[|\]|\}", text):
        mark = token.group(0)
        if mark in ("${", "["):
            open_.append("}" if mark == "${" else "]")
        elif open_ and open_[-1] == mark:
            open_.pop()
    return bool(open_)


def _inert(m, commits, command):
    """True when a name match `m` lies in quoted text of a git commit, outside what runs there, with no `${`, `$[`
    or `[` open around it and no `[` after it: message text."""
    at = m.start()
    for s in commits:
        if any(a <= at < b for a, b in s.spans) and not any(a <= at < b for a, b in s.runs):
            quote = next((a for a, b in s.quoted if a <= at < b), None)
            return (quote is not None and not _bracketed(command[quote:at])
                    and command[m.end():m.end() + 1] != "[")
    return False


def _only_read(command, name, powershell, own, commits=()):
    """True when `name` stands in the command text once as its assignment (bash `name=`, alone or after `export`;
    PowerShell `$name =`) and everywhere else only as a plain read (`$name` or `${name}`, in PowerShell an argument
    or part of a string). Any other appearance as a shell word (`[`, `+=`, `declare`, a scope prefix, `read`,
    `for`, `Remove-Variable`, the bare name) leaves it unresolved. Text inside the assignment's own value (`own`,
    the span from its `=`) and the name inside a longer word (`my-repo`, `x\\repo`) are no appearance, nor is the
    quoted text of a git commit (`commits`: its message) when no `${`, `$[` or `[` is open around the name and no
    `[` follows it. What runs inside a commit (a substitution, a backtick, a PowerShell `$(`), its unquoted words (a
    `name=v git commit` prefix) and a name after `$[`, `[` or a `${` it does not close at once count."""
    assigned = 0
    for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", command, re.I if powershell else 0):
        if own[0] <= m.start() < own[1]:
            continue
        if _inert(m, commits, command):
            continue
        before, after = command[:m.start()], command[m.end():]
        if before.endswith(("$[", "[")) or before.endswith("${") and not after.startswith("}"):
            return False   # arithmetic, a subscript or an expansion that may assign (`${name:=v}`)
        if before.endswith("${") and after.startswith("}"):
            lead, rest = before[:-2], after[1:]
        elif before.endswith("$"):
            lead, rest = before[:-1], after
        elif (before[-1:] not in WORD_START and not before[-1:].isspace()
              or after[:1] not in WORD_END and not after[:1].isspace()):
            continue   # inside a longer word: a path or a name, not the variable
        elif not powershell and after.startswith("=") and re.search(r"(?:^|[;&|\n])[ \t]*(?:export[ \t]+)?$", before):
            assigned += 1
            continue
        else:
            return False
        if powershell and re.match(r"\s*=(?!=)", rest) and lead[-1:] in ("", " ", "\t", "\n", ";"):
            assigned += 1
            continue
        if powershell and (lead[-1:] not in ("", " ", "\t", "\n", '"', ",", "=", ";")
                           or not re.match(r"(?:$|[\s\"',;)}|&\\/])", rest)
                           or re.match(r"\s*(?:[-+*/%]|\?\?)=", rest)):
            return False
    return assigned == 1


def _trusted(parsed, command, powershell):
    """{index of a simple command that assigns: its `&&` chain, or None} for the assignments the guard trusts: at the
    top level (no subshell, group, loop or if block), after the start, `;`, a newline or `&&`, not piped or sent to
    the background, and the name's one assignment, every other appearance a plain read (a value with `$` or a backtick is
    kept as written, so a folder holding it stays unresolved). An
    assignment after `&&` holds only for steps of the same `&&` chain, which run only when it ran. Also
    {index: chain} of every simple command."""
    trusted, chains, depth, chain = {}, {}, 0, 0

    def own(simple):
        """The span of an assignment's value: from its first `=` to the end of the simple command."""
        start, end = simple.spans[0]
        eq = command.find("=", start, end)
        return (eq, end) if eq >= 0 else (0, 0)

    commits = [s for s in parsed if (_git_parts(_command_words(s.words, powershell) or ["-"], ".", powershell)
                                     or "--")[1] == "commit"]
    for k, simple in enumerate(parsed):
        seps = re.sub(r"\s", "", simple.before.replace("&&", "A").replace("||", "O"))
        if seps != "A":
            chain += 1
        chains[k] = chain
        depth += sum(seps.count(c) for c in "({") - sum(seps.count(c) for c in ")}")
        first = simple.words[0] if simple.words else ""
        if not powershell and first in CLOSES:
            depth -= 1
        after = parsed[k + 1].before.replace("&&", "").replace("||", "") if k + 1 < len(parsed) else ""
        pairs = _assignments(simple.words, powershell)
        if (pairs and depth == 0 and set(seps) <= set("A;") and not set(after) & set("|&")
                and all(_only_read(command, name, powershell, own(simple), commits) for name, _ in pairs)):
            trusted[k] = chain if "A" in seps else None
        if not powershell and first in OPENS:
            depth += 1
    return trusted, chains


def _expand(word, variables, powershell):
    """`word` with each variable the command set to a literal replaced by its value; any other stays as written."""
    if powershell:
        pattern, fold = re.compile(r"\$(?:\{((?:env:)?\w+)\}|((?:env:)?\w+))", re.I), str.lower
    else:
        pattern, fold = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))"), str
    return pattern.sub(lambda m: variables.get(fold(m.group(1) or m.group(2)), m.group(0)), word)


def _unresolved(path):
    """True when a folder still holds a variable, a substitution or a leading `~`, which the guard cannot name."""
    return "$" in path or "`" in path or any(part.startswith("~") for part in re.split(r"[\\/]", path))


def _folder_refusal(path):
    """The Unclear for a git step in a folder `_unresolved` refuses."""
    return Unclear(STACK_WHY, STACK_HINT) if LOST in path else Unclear(FOLDER_WHY, FOLDER_HINT)


def _stack_step(words, folder, stack, seen, powershell):
    """The folder after a `pushd`/`popd` (PowerShell `Push-Location`/`Pop-Location`), updating `stack`; a folder
    holding LOST when the stack cannot be followed (a pop from an empty stack, a rotation, `-n`, a named stack)."""
    head, args = words[0].lower(), words[1:]
    targets = [w for w in args if not w.startswith("-")]
    odd = [w for w in args if w.startswith("+") or w.startswith("-") and w.lower() not in STACK_PLAIN]
    lost = os.path.join(folder, LOST)
    if odd or head in POP and targets:
        return lost
    if head in POP:
        return stack.pop() if stack else lost
    if targets:
        stack.append(folder)
        return os.path.join(folder, _local_path(_expand(targets[-1], seen, powershell), powershell))
    if powershell:   # a bare `Push-Location` pushes the folder and stays
        stack.append(folder)
        return folder
    if not stack:   # a bare bash `pushd` swaps the two top folders
        return lost
    folder, stack[-1] = stack[-1], folder
    return folder


class Commit:
    def __init__(self):
        self.folder = None
        self.message_file = None
        self.message_written = False   # the `-F` file is one the command writes before the commit
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
RUN_HINT = ("Run `git commit` itself in the command, not in a string, a function or another shell; if the command "
            "does not commit, split it so no `git` and `commit` share a part between `;`, `&`, `|` and newlines, "
            "write that text to a file with the Write tool, or pass an argument such as `--grep commit` from a "
            "substitution (bash `\"$(printf commit)\"`, PowerShell `\"$('commit')\"`)")
FOLDER_WHY = "the folder of a git step is named by a variable or `~` the command does not set to a literal"
FOLDER_HINT = ("Name the folder literally in `cd` or `git -C`, or set the variable once, to a literal path, at the "
               "start of the same command (`;`, `&&` or a newline before the git step, not inside a block)")
LOST = "$(lost folder)"   # a folder part the guard cannot follow; `_unresolved` refuses any folder that holds it
STACK_WHY = ("the guard cannot follow the folder of a git step past a `pushd`, `popd`, `dirs`, `Push-Location`, "
             "`Pop-Location`, a stack name or `)`")
STACK_HINT = ("Name the folder with an absolute path in `cd`, `Set-Location` or `git -C` after the last `popd`, "
              "`Pop-Location` or `)`, or run the steps in another folder in a call of their own")
REMOTE = ("ssh",)   # a commit it runs works on a repository the hook cannot read: unchecked
WSL_WHY = "a `wsl` git commit works in a repository the guard cannot place"
WSL_HINT = ("Name the WSL folder literally: `/mnt/<d>/...` for a Windows repository, an absolute Linux path or "
            "`~/...` without `..` for a Linux one, with no `--git-dir`, `--work-tree`, `--namespace`, other `-c` "
            "settings or GIT_* variables")
LINUX_ONLY = object()   # a `wsl` git step in the Linux file system, which the hook cannot read: unchecked
WSL_VALUE = ("-d", "--distribution", "-u", "--user", "--shell-type")


def _program(word):
    """A program word as a name: no folder, no `.exe`, lower case."""
    return re.sub(r"\.exe$", "", word.replace("\\", "/").rsplit("/", 1)[-1].lower())


class LinuxPath(str):
    """A folder in the WSL Linux file system, as WSL resolves it."""


UNPLACED = object()   # a WSL folder the guard cannot place
SAFE_GLOBAL = ("--no-pager", "-P", "-p", "--paginate", "--no-optional-locks", "--literal-pathspecs",
               "--no-replace-objects")   # git options before the subcommand that never choose the repository
SAFE_CONFIG = ("user.", "commit.", "color.", "advice.", "gpg.", "i18n.", "pager.")   # `-c` keys that choose none


def _wsl_folder(path, folder):
    """The folder a WSL path names from `folder`: a Windows folder for `/mnt/<d>/...` or a Windows or relative path
    from one; a LinuxPath for an absolute path outside `/mnt/<d>` (read as WSL does: `..` resolved, a doubled
    leading slash collapsed) or `~`/`~/x` with no `..`; UNPLACED for anything else. Off Windows an absolute path
    is local as written."""
    if folder is UNPLACED or "$" in path or "`" in path:
        return UNPLACED
    if isinstance(folder, LinuxPath) and not path.startswith(("/", "~")):
        path = posixpath.join(folder, path)
    if path == "~" or path.startswith("~/"):
        return LinuxPath(path) if ".." not in path.split("/") else UNPLACED
    if path.startswith("/"):
        norm = "/" + posixpath.normpath(path).lstrip("/")
        if os.name != "nt":
            return norm
        mount = re.match(r"/mnt/([A-Za-z])(?=/|$)(.*)", norm)
        return mount.group(1) + ":" + (mount.group(2) or "/") if mount else LinuxPath(norm)
    return path if folder is None else os.path.join(folder, path)


def _wsl_git(words, base):
    """For git words WSL runs from `base`: (the folder they choose, whether every option before the subcommand is
    one that chooses no repository, the index of the subcommand)."""
    j, sure = 1, True
    while j < len(words) and words[j].startswith("-"):
        if words[j] == "-C" and j + 1 < len(words):
            base = _wsl_folder(words[j + 1], base)
        elif words[j] == "-c" and j + 1 < len(words):
            sure = sure and words[j + 1].split("=", 1)[0].lower().startswith(SAFE_CONFIG)
        elif words[j] not in SAFE_GLOBAL:
            sure = False
        j += 2 if words[j] == "-C" or words[j] in GIT_VALUE_GLOBAL else 1
    return base, sure, j


def _linux_only(base, sure, command):
    """True when a WSL git step surely works in the Linux file system: its folder is there, no option before the
    subcommand can choose another repository, and the command names no GIT_* variable."""
    return isinstance(base, LinuxPath) and sure and not re.search("GIT_", command, re.I)


def _through_wsl(words, folder, command):
    """`wsl [options] git ...` as the git words it runs, with the Windows folder as `-C` (WSL starts in the Windows
    folder); LINUX_ONLY when that repository is surely in the Linux file system; (UNPLACED, subcommand) when the
    guard cannot place it; None when it runs no plain git."""
    i, base = 1, folder
    while i < len(words) and words[i].startswith("-"):
        if words[i] in WSL_VALUE:
            i += 2
        elif words[i] == "--cd" and i + 1 < len(words):
            base, i = _wsl_folder(words[i + 1], folder), i + 2
        elif words[i] in ("-e", "--exec", "--"):
            i += 1
            break
        elif words[i] == "--system":
            i += 1
        else:
            return None
    rest = words[i:]
    if len(rest) > 2 and _program(rest[0]) in ("bash", "sh") and rest[1] in ("-c", "-lc"):
        return LINUX_ONLY if _linux_only_script(rest[2], base, command) else None
    if not rest or _program(rest[0]) != "git":
        return None
    base, sure, j = _wsl_git(rest, base)
    if _linux_only(base, sure, command):
        return LINUX_ONLY
    if base is UNPLACED or isinstance(base, LinuxPath):
        return UNPLACED, (rest[j:j + 1] or [""])[0]
    out, k = [], 1
    while k < j:
        width = 2 if rest[k] == "-C" or rest[k] in GIT_VALUE_GLOBAL else 1
        out += [] if rest[k] == "-C" else rest[k:k + width]
        k += width
    return ["git"] + ([] if base is None else ["-C", base]) + out + rest[j:]


def _linux_only_script(script, folder, command):
    """True when a bash script `wsl` runs from `folder` commits only where `_linux_only` holds (after its `cd` and
    `-C`) and holds no other `git ... commit` the parse cannot place."""
    try:
        parsed = split_command(script, False)
    except Unparsed:
        return False
    read, here = [], folder
    for simple in parsed:
        words = _command_words(simple.words, False)
        if set(simple.before) & set("()") or words and words[0] in PUSH:
            return False   # a subshell or a folder stack this walk does not follow (a fresh shell has none to pop)
        if words and words[0] in CD:
            targets = [w for w in words[1:] if not w.startswith("-")]
            here = _wsl_folder(targets[-1], here) if targets else LinuxPath("~")   # a bare `cd` goes home
        elif words and _program(words[0]) == "git":
            base, sure, j = _wsl_git(words, here)
            if words[j:j + 1] == ["commit"]:
                if not _linux_only(base, sure, command):
                    return False
                read.append(simple)
    try:
        _unread_commit(script, read, False)
    except Unclear:
        return False
    return True


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


def _git_parts(words, folder, powershell, variables=None):
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
            folder = os.path.join(folder, _local_path(_expand(words[i + 1], variables or {}, powershell), powershell))
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
            named = [option for option in COMMIT_LONG if option.startswith(name)]
            name = name if name in COMMIT_LONG or len(named) != 1 else named[0]
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

    def runs_git(words, folder):
        """The words of a `wsl` git step as git runs them, LINUX_ONLY, (UNPLACED, subcommand), or the words as
        they stand."""
        if words and _program(words[0]) == "wsl":
            return _through_wsl(words, folder, command) or words
        return words

    read = []
    for words, simple in simples:
        words = runs_git(words, cwd)
        if words is LINUX_ONLY or isinstance(words, tuple) or words and (
                _program(words[0]) in REMOTE or (_git_parts(words, cwd, powershell) or "--")[1] == "commit"):
            read.append(simple)   # an unplaced `wsl` step is refused below, with its own reason
    _unread_commit(command, read, powershell)
    trusted, chains = _trusted(parsed, command, powershell)
    folder = cwd
    stages, readers, commits, variables, written = [], [], [], {}, set()
    unclear, base, writes, touched, history, dirty, vague = None, None, [], [], None, False, None
    stack, subshells = [], []   # the `pushd` folders; (folder, stack) where each open bash subshell started
    cases = []   # the subshell depth of each open bash `case`, whose patterns end in a `)` that closes nothing
    for k, (words, simple) in enumerate(simples):
        seen = {name: value for name, (value, chain) in variables.items() if chain is None or chain == chains[k]}
        for separator in "" if powershell else simple.before:   # a bash subshell's `cd` ends at its `)`
            if separator == "(":
                subshells.append((folder, list(stack)))
            elif separator == ")" and cases and cases[-1] == len(subshells):
                if subshells and folder is not None:   # a pattern's end, or a subshell's if `case` was no keyword
                    folder = os.path.join(folder, LOST)
            elif separator == ")" and subshells:
                folder, stack = subshells.pop()
            elif separator == ")" and folder is not None:
                folder = os.path.join(folder, LOST)
        if not powershell and words[:1] == ["case"]:
            cases.append(len(subshells))
        elif not powershell and words[:1] == ["esac"] and cases:
            cases.pop()
        targets = [t for t in simple.redirects if t.lower() not in NO_FILE]
        if targets:
            writes.append((folder, (words or ["a redirection"])[0], targets))
            written.update(_written(folder, targets, seen, powershell))
        assigned = _assignments(simple.words, powershell)
        if assigned:
            if k in trusted:   # an untrusted one sets a name no trusted one sets: the name stays unresolved
                variables.update((name, (value, trusted[k])) for name, value in assigned)
            continue
        words = runs_git(words, folder)
        if isinstance(words, tuple):   # a `wsl` git step the guard cannot place
            if words[1] == "commit":
                raise Unclear(WSL_WHY, WSL_HINT)
            if vague is None and words[1] in ("add", "rm"):
                vague = ""   # a staging step in a folder the guard cannot place
            continue
        if not words or words is LINUX_ONLY:
            continue
        head = words[0].lower()
        near = (simple.before + (simples[k + 1][1].before if k + 1 < len(simples) else "")).replace(
            "&&", "").replace("||", "")
        if folder is not None and (head == "dirs" or powershell and head in CD and any(
                len(w) > 1 and "-stackname".startswith(w.lower()) for w in words[1:])):
            folder = os.path.join(folder, LOST)   # the stack edited, or a default stack named
            continue
        if head in PUSH + POP:
            if folder is not None and set(near) & set("|&"):   # piped or in the background: it may not stay
                folder = os.path.join(folder, LOST)
            elif folder is not None:
                folder = _stack_step(words, folder, stack, seen, powershell)
            continue
        if head in CD:
            targets = [w for w in words[1:] if not w.startswith("-")]
            if not targets:
                folder = None
            elif folder is not None:
                folder = os.path.join(folder, _local_path(_expand(targets[-1], seen, powershell), powershell))
            continue
        if head in READERS and folder is not None:
            readers = [os.path.join(folder, w) for w in words[1:] if not w.startswith("-")]
            continue
        program = re.sub(r"\.exe$", "", head.replace("\\", "/").rsplit("/", 1)[-1])
        kind = FILE_WRITERS.get(program)
        if kind and (program != "sed" or any(w.startswith("--in-place") or re.match(r"-[^-]*i", w) for w in words[1:])):
            names = [w for w in words[1:] if not w.startswith("-")]
            writes.append((folder, words[0], _copy_targets(words[1:]) if kind == "copy" else names))
            written.update(_written(folder, writes[-1][2], seen, powershell))
            continue
        parts = _git_parts(words, folder, powershell, seen)
        if parts is None:
            continue
        git_folder, sub, args = parts
        options = args[:args.index("--")] if "--" in args else args
        if sub in HISTORY and any(a in ("--help", "-h", "--show-current-patch") for a in options) \
                and not any(a in ("-m", "-F", "-X", "-s", "--strategy") for a in options):
            continue   # help changes nothing; `-m -h` gives a message
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
            if vague is None and git_folder is not None and _unresolved(git_folder):
                vague = git_folder
        elif sub == "commit":
            commit = Commit()
            _commit_options(commit, args)
            if commit.dry_run:
                continue
            if git_folder is None:
                return None
            if vague is not None or _unresolved(git_folder):
                raise _folder_refusal(git_folder if vague is None else vague)
            if commit.all or commit.pathspecs:
                touched, writes, dirty = touched + writes, [], True
            if history and dirty:
                raise Unclear("`git %s` and another change to the index run before it" % history)
            if commit.message_file and commit.message_file != "-":
                commit.message_file = _local_path(_expand(commit.message_file, seen, powershell), powershell)
                commit.message_written = bool(_written(git_folder, [commit.message_file], {}, powershell) & written)
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


def _written(folder, targets, variables, powershell):
    """The paths, as compared, of files a step in `folder` writes or removes."""
    if folder is None:
        return set()
    return {os.path.normcase(os.path.abspath(os.path.join(folder, _local_path(_expand(t, variables, powershell),
                                                                              powershell)))) for t in targets}


def _unread_commit(command, read, powershell):
    """Raise Unclear when a `commit` word follows a `git` word between two separators (`;&|(){}!`, a newline)
    outside the text of every commit the parse read, where a substitution, backtick or expanding heredoc part inside
    a commit is no text of it. Loose on purpose: it knows no wrapper, so text that only names a commit
    (`echo git commit`) is refused too."""
    git_seen = False
    for token in (SEGMENT_PS if powershell else SEGMENT).finditer(command):
        word = token.group(0).lower()
        if word == "git":
            git_seen = True
        elif word != "commit":
            git_seen = False   # a separator ends the segment
        elif git_seen and not any(
                any(start <= token.start() < end for start, end in simple.spans)
                and not any(start <= token.start() < end for start, end in simple.runs) for simple in read):
            raise Unclear("a git commit the guard could not parse stands in it", RUN_HINT)


def has_reason(text, quoted=True):
    """True when a line holds `Test-change:` (or `Test-change=`, as `--trailer` takes it) and at least
    REASON_MIN characters after it; in command text (`quoted`) up to a quote that ends the value it stands in, where an
    apostrophe inside a word (`it's`) ends nothing."""
    for line in (text or "").splitlines():
        at = max(line.find(TRAILER), line.find(TRAILER[:-1] + "="))
        if at < 0:
            continue
        reason = line[at + len(TRAILER):]
        end = re.search(r"[\"`]|'(?!\w)", reason) if quoted else None
        if len(reason[:end.start() if end else len(reason)].strip()) >= REASON_MIN:
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
        return any(has_reason(_read(path), quoted=False) for path in commit.message_sources)
    if commit.message_file:
        if commit.message_written:
            return False   # what the command writes there stands in the command text, which is read already
        text = _read(os.path.join(commit.folder, commit.message_file))
        return text is not None and has_reason(text, quoted=False)   # a file it cannot read states no reason
    ref = commit.reuse or ("HEAD" if commit.amend and not commit.new_message else None)
    if ref and not ref.startswith("-"):
        return has_reason(_git(["log", "-1", "--no-show-signature", "--format=%B", ref, "--"],
                               commit.folder).decode("utf-8", "replace"), quoted=False)
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
        if folder is None or _unresolved(folder) or _holds_tests(folder, targets, top):
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
