"""shell-guard: a Claude Code PreToolUse hook for the Bash and PowerShell tools on Windows.

It reads the hook event from stdin and, when a command is certain to fail for a known
mechanical reason (wrong shell's syntax, a path Git Bash will mangle, a command long enough
to be truncated, ...), denies it with a reason that names the problem and the fix, so the
model corrects the command instead of running it and reading a cryptic error.

Every rule was derived from real failures in Claude Code transcripts; see tests/. The hook
only ever denies (high-confidence rules) or stays silent; it never adds context to a call it
lets through.

Contract:
  * stdlib only, never raises: any internal error allows the command unchanged;
  * a command containing the marker `shell-guard: allow` (e.g. in a trailing comment)
    is always allowed — the escape hatch for a false positive;
  * SHELL_GUARD=off in the environment disables the hook.

Usage as a library (tests): `check(tool_name, command) -> list[Finding]`.
`--only-git` on the command line runs only the git rules (`check_git`), `--only-narrow` only the
recurring-class rules (`check_narrow`), and `--pwsh-all` both in one process (`check_installed`):
the hooks that are installed (see INSTALL.md).
"""
import json
import os
import re
import sys
import time


def _profile_dir(var, *parts):
    """A folder under the Windows folder in $var (USERPROFILE, APPDATA), or under `%VAR%` when it is unset."""
    return "\\".join([os.environ.get(var, "").rstrip("\\/") or "%" + var + "%"] + list(parts))


def _tool_file(var):
    """The file $var names, or "" when it is unset or no such file exists: its hint is then left out."""
    path = os.environ.get(var, "")
    return path if path and os.path.isfile(path) else ""


HERD_BIN_WIN = _profile_dir("USERPROFILE", ".config", "herd", "bin")
HERD_BIN_MSYS = "/" + HERD_BIN_WIN[0].lower() + HERD_BIN_WIN[2:].replace("\\", "/")
COMPOSER_BIN_WIN = _profile_dir("APPDATA", "Composer", "vendor", "bin")
# Optional tool on the user's machine, named in a fix only when its env var points at the file.
WPCLI_PHAR = _tool_file("RIGGER_WPCLI_PHAR")

# Bash tool commands are wrapped (single quotes escaped as '\'') and passed on a Windows
# command line; past this size the tail is cut off. Measured on 280 failures: no command
# below 7786 escaped chars was truncated, every one above ~7900 was.
LONG_DENY = 7790

ALLOW_MARKER = "shell-guard: allow"


def line_col(s, pos):
    return s.count("\n", 0, pos) + 1, pos - (s.rfind("\n", 0, pos) + 1) + 1


class Finding:
    __slots__ = ("rule", "deny", "message")

    def __init__(self, rule, deny, message):
        self.rule = rule
        self.deny = deny
        self.message = message

    def __repr__(self):
        return "Finding(%s, deny=%s)" % (self.rule, self.deny)


# --------------------------------------------------------------------------------------
# Bash lexer: enough of POSIX/bash quoting to find simple commands and their words.
# --------------------------------------------------------------------------------------

class Word:
    __slots__ = ("raw", "value", "quoted", "start", "unquoted_bs")

    def __init__(self, start):
        self.raw = ""
        self.value = ""
        self.quoted = False
        self.start = start
        self.unquoted_bs = False  # contains a backslash outside quotes


class LexError(Exception):
    def __init__(self, kind, pos):
        Exception.__init__(self, kind)
        self.kind = kind
        self.pos = pos


BASH_RESERVED = {
    "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "case", "esac",
    "in", "function", "{", "}", "!", "time", "select", "[[", "]]", "coproc",
}
BASH_OPS = ("&&", "||", ";;&", ";;", ";&", "|&", "|", "&", ";", "(", ")")
NULL_FILES = {"/dev/null", "nul", "$null"}


class BashLexer:
    """Splits a bash command into simple commands (lists of Words) and notes the separators."""

    def __init__(self, src):
        self.s = src
        self.n = len(src)
        self.commands = []          # list of (words, ops_before)
        self.heredoc_unterminated = False
        self.errors = []            # (kind, pos)
        self.empty_op = None        # (op, pos) of an operator with no command before it
        self.piped = set()          # start offsets of commands that a `|` feeds
        self.after = {}             # start offset of a command -> the operator before it
        self.writes = set()         # start offsets of commands that redirect output into a file
        self.bodies = []            # (first, end) in commands of each subshell or substitution body

    # -- helpers -------------------------------------------------------------------
    def _find_close_paren(self, i):
        """i points just after '$(' ; return index just after the matching ')'."""
        sub = BashLexer(self.s)
        end = sub._lex(i, stop=")")
        first = len(self.commands)
        self.commands.extend(sub.commands)
        self.bodies += [(a + first, b + first) for a, b in sub.bodies] + [(first, len(self.commands))]
        self.heredoc_unterminated |= sub.heredoc_unterminated
        self.errors.extend(sub.errors)
        self.piped |= sub.piped
        self.after.update(sub.after)
        self.writes |= sub.writes
        return end

    def _dq(self, i, w):
        """Parse a double-quoted string starting after the opening quote."""
        s, n = self.s, self.n
        start = i - 1
        while i < n:
            c = s[i]
            if c == "\\" and i + 1 < n:
                w.value += s[i + 1] if s[i + 1] in '"\\$`\n' else s[i:i + 2]
                i += 2
                continue
            if c == '"':
                return i + 1
            if c == "$" and i + 1 < n and s[i + 1] == "(":
                i = self._find_close_paren(i + 2)
                w.value += "$(...)"
                continue
            if c == "`":
                i = self._backtick(i + 1, quoted=True)
                w.value += "`...`"
                continue
            w.value += c
            i += 1
        raise LexError("double", start)

    def _backtick(self, i, quoted=False):
        """i after the opening tick; return the index after the closing one. The body is read like a `$()`, its
        offsets mapped back to self.s. A body that does not lex adds nothing: bash reports it and goes on, so it
        is no quoting error of the outer command."""
        s, n = self.s, self.n
        start = i - 1
        body, origin = [], []
        while i < n and s[i] != "`":
            if s[i] == "\\" and i + 1 < n and s[i + 1] in ('$`\\"' if quoted else "$`\\"):
                i += 1
            body.append(s[i])
            origin.append(i)
            i += 1
        if i >= n:
            raise LexError("backtick", start)
        sub = BashLexer("".join(body)).run()
        if not sub.errors:
            for words in sub.commands:
                for w in words:
                    w.start = origin[w.start]
            first = len(self.commands)
            self.commands.extend(sub.commands)
            self.bodies += [(a + first, b + first) for a, b in sub.bodies] + [(first, len(self.commands))]
            self.piped |= {origin[k] for k in sub.piped}
            self.after.update((origin[k], op) for k, op in sub.after.items())
            self.writes |= {origin[k] for k in sub.writes}
        return i + 1

    def _brace(self, i):
        """${...}: i after '${'."""
        s, n = self.s, self.n
        depth = 1
        start = i - 2
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise LexError("single", i)
                i = j + 1
                continue
            if c == '"':
                i = self._dq(i + 1, Word(i))
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise LexError("brace", start)

    def _heredoc_delimiter(self, i):
        """(the word at i joined as bash joins it, the index after it). It stops at a quote that stays open, which
        the main loop then reports."""
        s, n = self.s, self.n
        delim = ""
        while i < n and not s[i].isspace() and s[i] not in "<>|&;()":
            c = s[i]
            if c == "\\":
                delim += s[i + 1:i + 2].replace("\n", "")
                i += 2
            elif c in "'`":
                j = s.find(c, i + 1)
                if j < 0:
                    break
                delim += s[i + 1:j] if c == "'" else s[i:j + 1]
                i = j + 1
            elif c == '"':
                m = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S).match(s, i)
                if not m:
                    break
                delim += re.sub(r'\\\n|\\(["\\$`])', lambda x: x.group(1) or "", m.group(1))
                i = m.end()
            else:
                delim += c
                i += 1
        return delim, i

    def _skip_heredocs(self, i, pending):
        s, n = self.s, self.n
        for strip, delim in pending:
            while True:
                if i >= n:
                    self.heredoc_unterminated = True
                    return n
                j = s.find("\n", i)
                line = s[i:] if j < 0 else s[i:j]
                i = n if j < 0 else j + 1
                if line.endswith("\r"):
                    line = line[:-1]
                if (line.lstrip("\t") if strip else line) == delim:
                    break
        return i

    # -- main loop -------------------------------------------------------------------
    def _lex(self, i, stop=None):
        s, n = self.s, self.n
        words = []
        w = None
        pending = []
        depth = 0
        last_sig = None  # last significant token: 'word', an operator, or None
        opened = []      # where each open `(` starts in self.commands
        redirect_next = None   # the direction, `<` or `>`, of a redirect whose target is the next word
        wrote = False

        def end_word():
            nonlocal w, redirect_next, wrote
            if w is not None:
                if redirect_next:
                    wrote |= redirect_next == ">" and w.value.lower() not in NULL_FILES
                    redirect_next = None
                else:
                    words.append(w)
                w = None

        def end_cmd(op, pos):
            nonlocal words, last_sig, wrote
            end_word()
            if words:
                if last_sig in ("|", "|&"):
                    self.piped.add(words[0].start)
                if wrote:
                    self.writes.add(words[0].start)
                self.after[words[0].start] = last_sig
                self.commands.append(words)
            elif op in ("&&", "||", "|", "|&", "&", ";") and last_sig in (None, "&&", "||", "|", "|&", "&", ";", "("):
                if self.empty_op is None:
                    self.empty_op = (op, pos)
            words = []
            wrote = False
            if op != "\n" or last_sig not in ("&&", "||", "|", "|&"):
                last_sig = op

        while i < n:
            c = s[i]
            if c == "\n":
                end_cmd("\n", i)
                i += 1
                if pending:
                    i = self._skip_heredocs(i, pending)
                    pending = []
                continue
            if c in " \t\r":
                end_word()
                i += 1
                continue
            if c == "#" and w is None:
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if stop == ")" and c == ")" and depth == 0:
                end_cmd(")", i)
                return i + 1
            if s.startswith("\\\n", i):   # a line continuation joins words and starts none
                i += 2
                continue
            if w is None and c not in "|&;()<>":
                w = Word(i)
            if c == "\\":
                w.raw += s[i:i + 2]
                w.value += s[i + 1:i + 2]
                w.unquoted_bs = True
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise LexError("single", i)
                w.raw += s[i:j + 1]
                w.value += s[i + 1:j]
                w.quoted = True
                i = j + 1
                continue
            if c == '"':
                j = self._dq(i + 1, w)
                w.raw += s[i:j]
                w.quoted = True
                i = j
                continue
            if c == "$" and i + 1 < n:
                nx = s[i + 1]
                if nx == "'":
                    j = i + 2
                    while j < n and s[j] != "'":
                        j += 2 if s[j] == "\\" else 1
                    if j >= n:
                        raise LexError("single", i)
                    w.raw += s[i:j + 1]
                    w.value += s[i + 2:j]
                    w.quoted = True
                    i = j + 1
                    continue
                if nx == "(":
                    j = self._find_close_paren(i + 2)
                    w.raw += s[i:j]
                    w.value += "$(...)"
                    i = j
                    continue
                if nx == "{":
                    j = self._brace(i + 2)
                    w.raw += s[i:j]
                    w.value += s[i:j]
                    i = j
                    continue
            if c == "`":
                j = self._backtick(i + 1)
                w.raw += s[i:j]
                w.value += "`...`"
                i = j
                continue
            if c in "<>":
                if w is not None and w.raw.isdigit():
                    w = None  # fd number of a redirect such as 2>, not an argument
                end_word()
                if s.startswith("<<<", i):
                    i += 3
                    redirect_next = "<"
                    continue
                if s.startswith("<<", i):
                    j = i + 2
                    strip = False
                    if j < n and s[j] == "-":
                        strip = True
                        j += 1
                    while j < n and (s[j] in " \t" or s.startswith("\\\n", j)):
                        j += 2 if s[j] == "\\" else 1
                    delim, i = self._heredoc_delimiter(j)
                    if i > j:
                        pending.append((strip, delim))
                    continue
                # > >> < >& <& &> >| — the next word is a redirect target
                i += 1
                while i < n and s[i] in ">&|":
                    i += 1
                # '2>&1' / '>&2': the target is a fd number, swallow it here
                m = re.match(r"\d+|-", s[i:])
                if s[i - 1] == "&" and m:
                    i += m.end()
                else:
                    redirect_next = c
                continue
            if c in "|&;()":
                # fd number glued to a redirect, e.g. '2>' — handled by '<>' branch
                if c == "&" and i + 1 < n and s[i + 1] == ">":
                    end_word()
                    i += 2
                    if i < n and s[i] == ">":
                        i += 1
                    redirect_next = ">"
                    continue
                for op in BASH_OPS:
                    if s.startswith(op, i):
                        break
                if op == "(":
                    depth += 1
                elif op == ")":
                    depth -= 1
                end_cmd(op, i)
                if op == "(":
                    opened.append(len(self.commands))
                elif op == ")" and opened:   # a `case` pattern's `)` opened nothing
                    self.bodies.append((opened.pop(), len(self.commands)))
                i += len(op)
                continue
            # plain character
            if w is None:
                w = Word(i)
            w.raw += c
            w.value += c
            i += 1
        end_cmd("\n", n)
        if pending:
            self.heredoc_unterminated = True
        if stop == ")":
            raise LexError("subst", i)
        return n

    def run(self):
        try:
            self._lex(0)
        except LexError as e:
            self.errors.append((e.kind, e.pos))
        except Exception:  # lexer bug: report nothing rather than guess
            self.errors.append(("internal", -1))
        return self


def bash_command_words(words):
    """Strip leading assignments/reserved words; return (env_assignments, words_from_command)."""
    env = []
    k = 0
    while k < len(words):
        v = words[k].raw
        if v in BASH_RESERVED:
            k += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", v):
            env.append(v)
            k += 1
            continue
        break
    return env, words[k:]


# --------------------------------------------------------------------------------------
# PowerShell lexer
# --------------------------------------------------------------------------------------

PS_KEYWORDS = {
    "if", "else", "elseif", "foreach", "for", "while", "do", "switch", "try", "catch", "finally",
    "function", "filter", "param", "return", "throw", "until", "begin", "process", "end", "trap",
    "break", "continue", "exit", "class", "enum", "using", "data", "dynamicparam", "in",
}


def _closes_block(s, i):
    """True when the `#>` at s[i] closes a `<#` before it: one glued to a value (`$a = 1<# why #>`), which both
    PowerShell scanners here read as text. Such a `#>` starts no line comment; any other `#>` does."""
    return s.startswith("#>", i) and s.rfind("<#", 0, i) > s.rfind("#>", 0, i)


class PsLexer:
    def __init__(self, src):
        self.s = src
        self.n = len(src)
        self.commands = []   # list of lists of (raw, value, quoted)
        self.errors = []
        self.piped = set()   # start offsets of commands that a single `|` feeds
        # start offset of a piped command -> start offset of the stage feeding it; a stage that
        # takes a script block (`ForEach-Object { ... }`) feeds the next `|`, not its block body
        self.feeds = {}
        self.after = {}      # start offset of a command -> the operator before it

    def _sub(self, i):
        """i just after '$(' or '@(' or '(' ; returns index after matching ')'."""
        sub = PsLexer(self.s)
        j = sub._lex(i, stop=")")
        self.commands.extend(sub.commands)
        self.errors.extend(sub.errors)
        self.piped |= sub.piped
        self.feeds.update(sub.feeds)
        self.after.update(sub.after)
        return j

    def _dq(self, i):
        s, n = self.s, self.n
        start = i - 1
        while i < n:
            c = s[i]
            if c == "`":
                i += 2
                continue
            if c == '"':
                if i + 1 < n and s[i + 1] == '"':
                    i += 2
                    continue
                return i + 1
            if c == "$" and i + 1 < n and s[i + 1] == "(":
                i = self._sub(i + 2)
                continue
            i += 1
        raise LexError("double", start)

    def _sq(self, i):
        s, n = self.s, self.n
        start = i - 1
        while i < n:
            if s[i] == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        raise LexError("single", start)

    def _here(self, i, q):
        """i points at '@'; here-string ends at a line starting with q + '@'."""
        s = self.s
        m = re.compile(r"\n" + re.escape(q) + "@").search(s, i)
        if m:
            return m.end()
        m2 = re.compile(r"\n[ \t]+" + re.escape(q) + "@").search(s, i)
        raise LexError("herestring-indented" if m2 else "herestring", i)

    def _lex(self, i, stop=None):
        s, n = self.s, self.n
        words = []
        cur = None  # [raw, value, quoted, start]
        depth = 0
        brace = 0
        pipe_pending = False  # survives empty statements: `a |` + newline + `b` still pipes
        last_stage = None     # start offset of the latest command at this brace level
        pipe_src = None       # the stage the pending `|` reads from
        block_owners = []     # last_stage of each enclosing script block's owner
        last_sig = None       # the operator that ended the previous command

        def end_word():
            nonlocal cur
            if cur is not None:
                words.append((cur[0], cur[1], cur[2], cur[3]))
                cur = None

        def end_cmd(op):
            nonlocal words, pipe_pending, last_stage, last_sig
            end_word()
            if words:
                if pipe_pending:
                    self.piped.add(words[0][3])
                    if pipe_src is not None:
                        self.feeds[words[0][3]] = pipe_src
                    pipe_pending = False
                self.after[words[0][3]] = last_sig
                self.commands.append(words)
                last_stage = words[0][3]
            words = []
            if op != "\n" or last_sig not in ("&&", "||", "|"):
                last_sig = op

        while i < n:
            c = s[i]
            if c in "\r\n;":
                end_cmd(";" if c == ";" else "\n")
                i += 1
                continue
            if c in " \t":
                end_word()
                i += 1
                continue
            if c == "#" and cur is None and not _closes_block(s, i):
                m = LINE_END.search(s, i)
                i = m.start() if m else n
                continue
            if c == "<" and cur is None and s.startswith("<#", i):
                j = s.find("#>", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c == "`":
                if i + 1 < n and s[i + 1] in "\r\n":
                    i += 3 if s.startswith("\r\n", i + 1) else 2
                    continue
                if cur is None:
                    cur = ["", "", False, i]
                cur[0] += s[i:i + 2]
                cur[1] += s[i + 1:i + 2]
                i += 2
                continue
            if c == "@" and i + 1 < n and s[i + 1] in "'\"" and re.match(r"[ \t]*\r?\n", s[i + 2:i + 40]):
                j = self._here(i, s[i + 1])
                if cur is None:
                    cur = ["", "", False, i]
                cur[0] += s[i:j]
                cur[1] += "@here@"
                cur[2] = True
                i = j
                continue
            if c == "'":
                j = self._sq(i + 1)
                if cur is None:
                    cur = ["", "", False, i]
                cur[0] += s[i:j]
                cur[1] += s[i + 1:j - 1].replace("''", "'")
                cur[2] = True
                i = j
                continue
            if c == '"':
                j = self._dq(i + 1)
                if cur is None:
                    cur = ["", "", False, i]
                cur[0] += s[i:j]
                cur[1] += s[i + 1:j - 1]
                cur[2] = True
                i = j
                continue
            if c in "$@" and i + 1 < n and s[i + 1] == "(":
                j = self._sub(i + 2)
                if cur is None:
                    cur = ["", "", False, i]
                cur[0] += s[i:j]
                cur[1] += "$(...)"
                i = j
                continue
            if c == "(":
                if cur is None and words:
                    # a parenthesised argument (`-Value ($t + 'x')`) is one word of this command;
                    # the commands inside it are still lexed and listed
                    j = self._sub(i + 1)
                    cur = [s[i:j], "(...)", False, i]
                    i = j
                    continue
                end_cmd("(")
                depth += 1
                i += 1
                continue
            if c == ")":
                if stop == ")" and depth == 0:
                    end_cmd(")")
                    return i + 1
                depth -= 1
                end_cmd(")")
                i += 1
                continue
            if c in "{}":
                # script blocks: '{' starts a new statement context; '}' ends one.
                # Hashtable/type literals like @{ } or ${var} are glued to a word.
                if cur is not None and (cur[0].endswith("$") or cur[0].endswith("@")) and c == "{":
                    j = s.find("}", i)
                    j = n - 1 if j < 0 else j
                    cur[0] += s[i:j + 1]
                    cur[1] += s[i:j + 1]
                    i = j + 1
                    continue
                end_cmd(c)
                if c == "{":
                    block_owners.append(last_stage)
                    brace += 1
                else:
                    last_stage = block_owners.pop() if block_owners else None
                    brace -= 1
                i += 1
                continue
            if c == "|":
                if s.startswith("||", i):
                    end_cmd("||")
                    i += 2
                else:
                    end_cmd("|")
                    pipe_pending = True
                    pipe_src = last_stage
                    i += 1
                continue
            if c == "&":
                if s.startswith("&&", i):
                    end_cmd("&&")
                    i += 2
                    continue
                # call operator '&' at command start, or background '&' at end
                end_word()
                words.append(("&", "&", False, i))
                i += 1
                continue
            if cur is None:
                cur = ["", "", False, i]
            cur[0] += c
            cur[1] += c
            i += 1
        end_cmd("\n")
        if stop == ")":
            raise LexError("paren", i)
        return n

    def run(self):
        try:
            self._lex(0)
        except LexError as e:
            self.errors.append((e.kind, e.pos))
        except Exception:
            self.errors.append(("internal", -1))
        return self


# PowerShell reads these as string quotes; the lexer knows only the straight ones.
TYPOGRAPHIC_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"',
                                    "‘": "'", "’": "'", "‚": "'", "‛": "'"})


def ps_lex(cmd):
    """PsLexer on cmd with every quote straight, offsets kept, as PowerShell reads them: an apostrophe in curly
    double quotes is text. A word's raw text is straight too; the source text starts at its offset. The rules
    that take cmd itself translate it the same way. When that fails, the text as typed gets a second pass: a
    curly quote in a comment the lexer takes for code (`$a=#’`) opens nothing there."""
    lex = PsLexer(cmd.translate(TYPOGRAPHIC_QUOTES)).run()
    return PsLexer(cmd).run() if lex.errors else lex


PS_ASSIGN_TARGET = re.compile(r"^(?:\[[\w.\[\]]+\])*\$(?:\w+:)?\w+$")
PS_ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "??="}


def ps_command_name(words):
    """Return (index, name) of the command word of a PowerShell statement, or (None, None). A leading
    assignment (`$o = git …`, `[string]$o = & git …`) is skipped: the command after it still runs."""
    if not words:
        return None, None
    if len(words) > 2 and PS_ASSIGN_TARGET.match(words[0][0]) and words[1][0] in PS_ASSIGN_OPS:
        k, name = ps_command_name(words[2:])
        return (None, None) if k is None else (k + 2, name)
    k = 0
    if words[0][0] == "&" or words[0][0] == ".":
        k = 1
        if k >= len(words):
            return None, None
        raw, val, quoted, _ = words[k]
        return k, val
    raw, val, quoted, _ = words[0]
    if quoted or not raw:
        return None, None
    if raw[0] in "$[(@0123456789-+!'\"" or raw.lower() in PS_KEYWORDS:
        return None, None
    if "=" in raw:
        return None, None
    return 0, raw


# --------------------------------------------------------------------------------------
# Rules — each one backed by real failures in the transcripts (see tests/).
# Messages are kept to one or two sentences: they are fed back into the model's context.
# --------------------------------------------------------------------------------------

PS_CMDLET = re.compile(
    r"^(Get|Set|New|Remove|Select|Where|ForEach|Test|Write|Out|Invoke|Start|Stop|Copy|Move|Rename|"
    r"Join|Split|Measure|Sort|Format|Import|Export|ConvertTo|ConvertFrom|Add|Clear|Resolve|Expand|"
    r"Compress|Push|Pop|Wait|Group|Tee|Update|Read|Register|Unregister|Enable|Disable)-[A-Z][A-Za-z]+$"
)
WIN_PATH_BS = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\(?!\\)")
VERSIONED_PHP = re.compile(r"^php(7[0-4]|8[0-9])$")
CMD_META = re.compile(r"[|&<>^]")
CMD_PERCENT_VAR = re.compile(r"%([^%\s=]+)%")
LINE_END = re.compile(r"[\r\n]")   # either one ends a PowerShell line comment
PS_BRACED_VAR = re.compile(r"\$\{(?:`.|[^`}])*\}?", re.S)
REDIRECT_TOKEN = re.compile(r"^(\d|\*)?>>?|^<|^&$|^\|")
LINUX_ROOT = re.compile(r"^(-{1,2}[\w-]+=)?/(var|usr|etc|tmp|home|opt|bin|sbin|app|srv|data|root|mnt|run|lib|workspace|code)(/|$)")
NATIVE_PATHCONV = {"docker", "docker.exe", "docker-compose", "wsl", "wsl.exe", "kubectl", "podman"}

UNIX_TO_PS = {
    "tail": "Select-Object -Last N",
    "head": "Select-Object -First N",
    "grep": "Select-String or rg",
    "sed": "the -replace operator",
    "awk": "ForEach-Object with -split",
    "wc": "Measure-Object -Line",
    "which": "Get-Command",
    "touch": "New-Item -ItemType File",
    "xargs": "ForEach-Object",
    "cut": "ForEach-Object with -split",
    "less": "plain output",
    "export": "$env:NAME = 'value'",
    "source": "dot-sourcing (. .\\file.ps1)",
    "basename": "Split-Path -Leaf",
    "dirname": "Split-Path -Parent",
    "realpath": "Resolve-Path",
    "uniq": "Sort-Object -Unique",
}

PS_BAD_PARAMS = [
    # (command, bad flag, what to use instead) — all verified to raise on PowerShell 7.6
    (r"^(ls|dir|gci|Get-ChildItem)$", r"^-(la|al|lh|lah|alh|ltr)$", "Get-ChildItem -Force"),
    (r"^(rm|del|ri|Remove-Item)$", r"^-(rf|fr|Rf)$", "Remove-Item -Recurse -Force"),
    (r"^(Select-String|sls)$", r"^-(Recurse|B|C|i|n|E)$",
     "Get-ChildItem -Recurse | Select-String, -Context 2,2 for context lines, or rg"),
    (r"^(Where-Object|where|\?)$", r"^-(A|B|C)$", "Select-String -Context 2,2"),
    (r"^(cat|gc|type|Get-Content)$", r"^-n$", "Select-String -Pattern '' (LineNumber property)"),
    (r"^(cd|Set-Location|sl|chdir)$", r"^/[dD]$", "Set-Location '<drive>:\\path' (no /d)"),
]


def rule_ps_bad_parameter(name, args):
    """args are the PowerShell words after the command name: (raw, value, quoted, ...)."""
    for cmd_rx, flag_rx, instead in PS_BAD_PARAMS:
        if re.match(cmd_rx, name, re.I):
            bad = next((a for a in args if not a[2] and re.match(flag_rx, a[0])), None)
            if bad:
                return Finding(
                    "ps-bad-parameter", True,
                    "`%s %s` is a bash flag that PowerShell rejects; use %s." % (name, bad[0], instead))
    # `-l` is -LiteralPath: it runs with a path after it and fails with none
    if re.match(r"^(ls|dir|gci|Get-ChildItem)$", name, re.I):
        for i, a in enumerate(args):
            if not a[2] and a[0] == "-l" and (i + 1 == len(args) or args[i + 1][0].startswith("-")):
                return Finding(
                    "ps-bad-parameter", True,
                    "`%s -l` is -LiteralPath here and needs a path after it; for a long listing use "
                    "Get-ChildItem -Force." % name)
    # `-A` alone is -AllMatches and runs; grep's `-A <n>` leaves <n> as a positional nobody accepts
    if re.match(r"^(Select-String|sls)$", name, re.I):
        for a, b in zip(args, args[1:]):
            if not a[2] and a[0] == "-A" and re.match(r"^\d+$", b[1]):
                return Finding(
                    "ps-bad-parameter", True,
                    "`%s -A %s` is grep's after-context flag; PowerShell reads -A as -AllMatches and rejects %s; "
                    "use -Context 0,%s." % (name, b[1], b[1], b[1]))
    return None


def _msys(path_win):
    return "/" + path_win[0].lower() + path_win[2:].replace("\\", "/")


def default_php_exe(herd_bin=None):
    """The php.exe of the newest phpNN folder under Herd's bin folder ("" when none). `php`, `composer` and
    `wp` run Herd's global default, a setting this hook cannot read and that need not be the newest, so this is
    a stand-in: the fix it builds names a working PHP, not necessarily the one `php -v` reports."""
    herd_bin = herd_bin or HERD_BIN_WIN
    try:
        names = os.listdir(herd_bin)
    except OSError:
        return ""
    found = sorted(int(n[3:]) for n in names if re.match(r"php\d{2}$", n.lower())
                   and os.path.isfile(os.path.join(herd_bin, n, "php.exe")))
    return os.path.join(herd_bin, "php%d" % found[-1], "php.exe") if found else ""


def bat_fix(name, shell):
    """For a .bat wrapper (Herd php/composer/wp, Composer vendor/bin proxies), the real program to
    call; "" for a wrapper whose program is not known here or not on this machine, None for anything else."""
    low = name.lower().replace("/", "\\")
    if low.endswith(".bat"):
        low = low[:-4]
    base = low.rsplit("\\", 1)[-1]
    php = default_php_exe()
    extra = None
    project_file = False
    if base == "php" or VERSIONED_PHP.match(base):
        php = "\\".join((HERD_BIN_WIN, "php" + (base[3:] or "84"), "php.exe"))
    elif base == "composer":
        extra = HERD_BIN_WIN + "\\composer.phar"
    elif base == "wp":
        if not WPCLI_PHAR:
            return ""
        extra = WPCLI_PHAR
    elif base in ("phpcs", "phpcbf"):
        extra = COMPOSER_BIN_WIN + "\\" + base
    elif re.match(r"(?:\.\\)?vendor\\bin\\", low):
        extra, project_file = name.replace("\\", "/"), True
    else:
        return None
    if not all(os.path.isfile(p) for p in [php] + ([extra] if extra and not project_file else [])):
        return ""
    if shell == "Bash":
        return '"%s"' % _msys(php) + ("" if extra is None else ' "%s"' % (extra if project_file else _msys(extra)))
    return '& "%s"' % php + ("" if extra is None else ' "%s"' % extra)


def _launcher_file(name, cwd):
    """The file PowerShell starts for name, or None: the first PATH folder holding a match wins, and inside a
    folder .ps1 comes first, then PATHEXT in order, then the bare name. A name with a folder is looked up only
    there, relative to cwd."""
    if re.search(r"[\\/]", name):
        path = _abs(name, cwd)
        if path is None:
            return None
        folders, base = [os.path.dirname(path)], os.path.basename(path)
    else:
        folders, base = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d], name
    exts = [".ps1"] + [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e] + [""]
    for folder in folders:
        for ext in exts:
            candidate = os.path.join(folder, base + ext)
            if os.path.isfile(candidate):
                return candidate
    return None


def _runs_through_cmd(name, cwd, shell):
    """True when name reaches cmd.exe. In PowerShell a name with a folder that resolves to nothing stays True:
    the hook does not expand a variable or `~` or follow every `cd`, and the launchers are .bat files. Git Bash
    runs no .bat by a name without its extension, and a bare name found nowhere is no program: both False."""
    if name.lower().endswith((".bat", ".cmd")):
        return True
    found = _launcher_file(name, cwd)
    if found is None:
        return shell == "PowerShell" and bool(re.search(r"[\\/]", name))
    return found.lower().endswith((".bat", ".cmd"))


def _defined_percent_var(value):
    """The first `%NAME%` in value whose NAME is a defined environment variable (cmd.exe expands it)."""
    names = None
    for m in CMD_PERCENT_VAR.finditer(value):
        if names is None:
            names = {k.upper() for k in os.environ}
        if m.group(1).upper() in names:
            return m.group(0)
    return None


def _needs_quotes(value):
    """PowerShell's legacy rule for a native argument (applied to .bat files): quote it when empty or
    when whitespace sits outside the argument's own quotes."""
    if not value:
        return True
    quotes = 0
    for i, ch in enumerate(value):
        if ch == '"' and (i == 0 or value[i - 1] != "\\"):
            quotes += 1
        elif ch.isspace() and quotes % 2 == 0:
            return True
    return False


def _unquoted_meta(value):
    """The first cmd.exe metacharacter outside quotes once PowerShell has put value on a .bat command
    line: wrapped in "..." when it is empty or holds whitespace outside its own quotes (a `"` not after
    `\\` opens or closes them), its own `"` left unescaped, so each one toggles."""
    line = '"%s"' % value if _needs_quotes(value) else value
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            m = CMD_META.match(line, i)
            if m:
                return m
    return None


def rule_bat_metachar(name, args, shell, cwd=None):
    fix = bat_fix(name, shell)
    if fix is None:
        return None
    instead = "call `%s` instead" % fix if fix else "call the program the wrapper starts directly instead"
    for raw, value, quoted in args:
        if REDIRECT_TOKEN.match(raw) and not quoted:
            continue
        pct = _defined_percent_var(value) if shell == "PowerShell" else None
        m = _unquoted_meta(value) if shell == "PowerShell" else CMD_META.search(value)
        # Bash: an argument with whitespace or a double quote gets quoted on the Windows command line
        if not pct and not (m and (shell == "PowerShell" or not re.search(r"[\s\"]", value))):
            continue
        if not _runs_through_cmd(name, cwd, shell):
            return None
        if pct:
            return Finding(
                "bat-metachar", True,
                "`%s` is a .bat wrapper, so cmd.exe replaces `%s` in the argument %s with that variable's value "
                "before the program sees it, even inside quotes; %s." % (name, pct, raw, instead))
        return Finding(
            "bat-metachar", True,
            "`%s` is a .bat wrapper, so cmd.exe re-parses the argument %s and treats its `%s` as an "
            "operator (\"... is not recognized as an internal or external command\"); %s."
            % (name, raw, m.group(0), instead))
    return None


# git global options that take the next word as their value (`git -C <path> commit ...`).
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                       "--config-env", "--exec-path", "--attr-source", "--shallow-file"}


def git_subcommand(values):
    """values are the words after `git`; return (subcommand, its arguments) or (None, [])."""
    i = 0
    while i < len(values):
        v = values[i]
        if v in GIT_OPTS_WITH_VALUE:
            i += 2
            continue
        if v.startswith("-"):
            i += 1
            continue
        return v, values[i + 1:]
    return None, []


def git_commit_reads_stdin(args):
    for i, v in enumerate(args):
        if v == "--":
            return False
        nxt = args[i + 1] if i + 1 < len(args) else None
        if v == "--file=-" or (v == "--file" and nxt == "-"):
            return True
        if re.match(r"^-[A-Za-z]*F$", v) and nxt == "-":
            return True
        if re.match(r"^-[A-Za-z]*F-$", v):
            return True
    return False


def _git_is_commit(base, word):
    """True when `word` names a commit in the repository at base. A lookup that fails or runs out of time is
    False, so a word on disk then counts as the path it may be. Only the exit code is read and no stream is a
    pipe: the timeout kills git's launcher, and subprocess.run would wait past it for a child holding a pipe."""
    import subprocess
    if base is None:
        return False
    try:
        return subprocess.run(["git", "-C", base, "rev-parse", "--verify", "--quiet", word + "^{commit}"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=min(1.5, budget_left())).returncode == 0
    except Exception:
        return False


def _checkout_paths(args, literal, base):
    """The words of a `git checkout` without `--` that git reads as paths, in git's order: a first word naming
    a commit is that commit (a file of that name does not matter), a word on disk is a path. literal[i] is
    the literal text of args[i], None when unknown; base is where git runs, None when unknown."""
    if any(re.match(r"^-[qfl]*[bBd]", a) or a.startswith(("--orp", "--det")) for a in args):
        return []   # a branch operation: git takes no path here
    words, i = [], 0
    while i < len(args):
        a = args[i]
        if REDIRECT_TOKEN.match(a):
            i += 2 if a.endswith(">") else 1   # the redirect's target, when it is the next word
            continue
        if not a.startswith("-"):
            words.append((a, literal[i]))
        i += 1
    paths = []
    for n, (word, text) in enumerate(words):
        full = _abs(text, base)
        if full is not None and os.path.exists(full) and not (n == 0 and _git_is_commit(base, text)):
            paths.append(word)
    return paths


def _restore_targets(args):
    """(staged, worktree): what `git restore` writes, by the flags git accepts: `-S`, `-W`, a cluster of them
    (`-SW`), `--staged` down to `--st`, `--worktree` down to `--w`."""
    staged = worktree = False
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            staged |= _long_prefix(a, "--staged", 4)
            worktree |= _long_prefix(a, "--worktree", 3)
        elif a.startswith("-") and not a.startswith("-s"):   # `-s<rev>` holds a revision, not flags
            staged |= "S" in a
            worktree |= "W" in a
    return staged, worktree


def git_restore_paths(sub, args, literal=None, base=None):
    """The paths a `git checkout`/`git restore` would overwrite in the working tree, or []."""
    if any(a in ("--ours", "--theirs", "-p", "--patch", "-m", "--merge") for a in args):
        return []  # conflict resolution or interactive: chosen hunk by hunk
    if sub == "checkout":
        if "--" in args:
            return args[args.index("--") + 1:]
        if "." in args:
            return ["."]
        return _checkout_paths(args, literal or [None] * len(args), base)
    if sub == "restore":
        staged, worktree = _restore_targets(args)
        if staged and not worktree:
            return []
        paths, i = [], 0
        while i < len(args):
            a = args[i]
            if a in ("-s", "--source"):
                i += 2
                continue
            if a != "--" and not a.startswith("-"):
                paths.append(a)
            i += 1
        return paths
    return []


# Long options whose value is the next word, so a `--no-verify` there is text, not a flag. git accepts
# any unambiguous prefix of a long option (`--no-veri` skips hooks; `--no-ver` is ambiguous with
# `--no-verbose` and refused).
COMMIT_VALUE_LONG = ("--message", "--file", "--reuse-message", "--reedit-message", "--fixup", "--squash",
                     "--author", "--date", "--template", "--trailer", "--cleanup", "--pathspec-from-file")
PUSH_VALUE_LONG = ("--repo", "--receive-pack", "--exec", "--push-option")
MERGE_VALUE_LONG = ("--message", "--file", "--strategy-option", "--into-name", "--cleanup")
REBASE_VALUE_LONG = ("--onto", "--strategy-option", "--exec", "--whitespace", "--empty")
AM_VALUE_LONG = ("--directory", "--exclude", "--include", "--whitespace", "--patch-format", "--resolvemsg",
                 "--quoted-cr", "--empty")
# Per command, as git reads it: the shortest --no-verify and --verify it does not refuse as ambiguous (merge
# has --no-verify-signatures, so only the whole word), the short flags that mean --no-verify (-n is --no-stat
# on merge and rebase, --dry-run on push), those taking the next word, those holding an attached value, and
# the long options taking the next word. cherry-pick refuses --no-verify, and its -n makes no commit.
HOOK_FLAGS = {
    "commit": (9, 6, "n", "mFCct", "Su", COMMIT_VALUE_LONG),
    "push": (9, 6, "", "", "", PUSH_VALUE_LONG),
    "merge": (11, 8, "", "mFsX", "S", MERGE_VALUE_LONG),
    "rebase": (9, 6, "", "CxsX", "S", REBASE_VALUE_LONG),
    "am": (6, 3, "n", "Cp", "S", AM_VALUE_LONG),
}
HOOK_COMMANDS = set(HOOK_FLAGS) | {"cherry-pick"}
# In a config key: a variable, a substitution, an expression or an escape only the shell resolves.
UNREADABLE_KEY = re.compile(r"[$`(\\]")


def _long_prefix(opt, full, min_len):
    return len(opt) >= min_len and full.startswith(opt)


def git_skips_hooks(sub, args, literal):
    """The flag that turns hooks off in the args of a HOOK_FLAGS command, or None (the last of
    --no-verify/--verify wins). literal[i] is the literal text of args[i], None when unknown."""
    flag, i = None, 0
    no_verify, verify, skips, valued, attached, value_long = HOOK_FLAGS[sub]
    while i < len(args):
        a = args[i] if literal[i] is None else literal[i]
        if a == "--":
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if _long_prefix(name, "--no-verify", no_verify):
                flag = a
            elif _long_prefix(name, "--verify", verify):
                flag = None
            elif "=" not in a and any(_long_prefix(name, v, 4) for v in value_long):
                i += 1
        elif a.startswith("-") and len(a) > 1:
            if sub == "push":
                i += 1 if a == "-o" else 0
            else:
                for j, ch in enumerate(a[1:]):
                    if ch in skips:
                        flag = a
                    elif ch in valued:
                        i += 1 if j == len(a) - 2 else 0   # value in the next word unless attached
                        break
                    elif ch in attached:
                        break   # optional value, attached
        i += 1
    return flag


def _redirects_hooks(key):
    """True when git reads key as core.hooksPath, include.path (an included file can set it) or
    includeIf.<condition>.path: the section and the variable in any ASCII case, split at the first and last dot."""
    section, _, rest = key.translate(ASCII_LOWER).partition(".")
    _, dot, variable = rest.rpartition(".")
    if dot:
        return section == "includeif" and variable == "path"
    return (section, variable) in (("core", "hookspath"), ("include", "path"))


def git_config_override(args, literal):
    """The `-c` or `--config-env` value before the subcommand that can redirect hooks or that the hook cannot
    read, or None. git splits `-c` at the first `=`, `--config-env` at the last. args are the values after
    `git`, literal[i] the literal text of args[i], None when unknown."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        at = i + 1 if a in ("-c", "--config-env") else i if a.startswith("--config-env=") else None
        if at is not None:
            text = args[at] if literal[at] is None else literal[at]
            text = text[len("--config-env="):] if at == i else text
            key = text.partition("=")[0] if a == "-c" else text.rpartition("=")[0]
            if UNREADABLE_KEY.search(key) or _redirects_hooks(key):
                return text
        i += 2 if a in GIT_OPTS_WITH_VALUE else 1
    return None


def git_env_override(env):
    """The GIT_CONFIG_* variable in env that can redirect hooks, or None. env: upper-case name -> the value this
    command gave it, None when unknown; an empty one is unset. GIT_CONFIG_PARAMETERS is refused whole, as only
    git writes it. Otherwise git reads GIT_CONFIG_KEY_<n> below GIT_CONFIG_COUNT and stops at a missing one."""
    if env.get("GIT_CONFIG_PARAMETERS", "") != "":
        return "GIT_CONFIG_PARAMETERS"
    count = env.get("GIT_CONFIG_COUNT", "")
    if count == "":
        return None
    m = re.match(r"^\s*\+?(\d{1,10})$", count or "")
    if not m:
        return "GIT_CONFIG_COUNT"
    for n in range(int(m.group(1))):
        name = "GIT_CONFIG_KEY_%d" % n
        if name not in env:
            return None
        key = env[name]
        if key is None or _redirects_hooks(key):
            return name
    return None


# A junction or a volume mount point, the only reparse point `git worktree remove` deletes through; symbolic
# links keep their targets. Only Windows sets st_reparse_tag.
MOUNT_POINT_TAG = 0xA0000003
JUNCTION_SCAN_BUDGET = 6.0   # seconds, the most one walk may take; it never gets more than budget_left()
# Seconds the whole hook process may spend on walks and `git worktree list`, however many
# `worktree remove`s one command holds: the hook timeout is 10, the rest is startup and output.
HOOK_BUDGET = 8.0
_clock = time.monotonic
_deadline = None   # set once by start_budget() when the hook starts


def start_budget():
    """Starts the whole-hook budget: HOOK_BUDGET seconds from now."""
    global _deadline
    _deadline = _clock() + HOOK_BUDGET


def budget_left():
    """Seconds left of the hook budget; outside the hook (library use) every call gets HOOK_BUDGET."""
    return HOOK_BUDGET if _deadline is None else _deadline - _clock()


def _link_target(path):
    """The absolute target of a reparse point, None when unreadable. A `\\\\?\\` target without a drive
    letter (`\\\\?\\Volume{…}\\x`) is returned as read: it lies outside any tree walked by path."""
    try:
        t = os.readlink(path)
    except (OSError, ValueError):
        return None
    if t.startswith("\\\\?\\UNC\\"):
        t = "\\\\" + t[8:]
    elif t.startswith("\\\\?\\"):
        if not re.match(r"^[A-Za-z]:\\", t[4:]):
            return t
        t = t[4:]
    return os.path.normpath(os.path.join(os.path.dirname(path), t))


def outside_links(root, limit=3):
    """(links, finished): reparse points under root whose target is outside root (or unreadable),
    found without following any of them; finished is False when the walk ran out of time.

    The walk runs on one daemon worker thread and this thread waits for it at most
    min(JUNCTION_SCAN_BUDGET, budget_left()) seconds, so a call that blocks (a `scandir` on an offline
    share) cannot hold the hook past its budget, and a worker left blocked does not keep the process
    alive. The worker hands back its result in one assignment; no counter is shared."""
    import threading
    timeout = min(JUNCTION_SCAN_BUDGET, budget_left())   # spent (<= 0): the walk stops before its first folder
    deadline = _clock() + timeout
    result = [None]

    def work():
        try:
            result[0] = _walk(root, limit, deadline)
        except Exception:   # no traceback on stderr; result[0] stays None, so the caller denies
            pass

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    done = result[0]   # None: still walking when the time ran out, or the walk itself raised
    return ([], False) if done is None else done


def _walk(root, limit, deadline):
    """The walk outside_links runs on its worker thread: (links, finished), checking deadline (a _clock()
    value) between folders."""
    import collections
    root_n = os.path.normcase(os.path.abspath(root))
    inside = root_n.rstrip(os.sep) + os.sep

    def scan(d):
        subs, hits = [], []
        with os.scandir(d) as it:
            for e in it:
                if getattr(e.stat(follow_symlinks=False), "st_reparse_tag", 0) == MOUNT_POINT_TAG:
                    t = _link_target(e.path)
                    tn = None if t is None else os.path.normcase(t)
                    if tn is None or (tn != root_n and not tn.startswith(inside)):
                        hits.append((e.path, t))
                elif e.is_dir(follow_symlinks=False):
                    subs.append(e.path)
        return subs, hits

    # One thread, an explicit work list; first in, first out: links near the top are found before the
    # budget ends. The budget is checked between folders.
    todo, found = collections.deque([root]), []
    while todo:
        if _clock() >= deadline:
            return found[:limit], False
        d = todo.popleft()
        try:
            subs, hits = scan(d)
        except Exception:   # a folder this walk cannot read is a folder it cannot clear
            subs, hits = [], [(d, None)]
        found.extend(hits)
        if len(found) >= limit:
            return found[:limit], True
        todo.extend(subs)
    return found[:limit], True


def _abs(path, base):
    """path made absolute against base; None when either is unknown."""
    if path is None:
        return None
    if re.match(r"^[A-Za-z]:[\\/]|^[\\/]{2}", path):
        return os.path.normpath(path)
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, path))


def _git_base(args, literal, loc):
    """(the folder git runs in, the index of the subcommand in args); the folder is None when unknown."""
    base, i = loc, 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            base = _abs(literal[i + 1], base)
        i += 2 if args[i] in GIT_OPTS_WITH_VALUE else 1
    return base, i


ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _worktree_by_suffix(base, name):
    """git matches <worktree> against the end of each registered path first, at a folder boundary, as typed
    apart from ASCII case and `\\` for `/` (`foo/` and `.\\foo` match nothing); only without exactly one match does
    it take the path. (path, failure): path is None when no single worktree matches; failure is "unknown" when
    `git worktree list` raised, timed out or the hook budget is spent, "refused" when it exited non-zero (git
    will refuse the remove the same way), None when it answered. Its output goes to a file and no stream is a
    pipe: the timeout kills git's launcher, and subprocess.run would wait past it for a child holding a pipe."""
    import subprocess
    import tempfile
    if base is None or not os.path.isdir(base):
        return None, None
    try:   # a spent budget (timeout <= 0) raises TimeoutExpired at once
        with tempfile.TemporaryFile() as listing:
            proc = subprocess.run(["git", "-C", base, "worktree", "list", "--porcelain"], stdin=subprocess.DEVNULL,
                                  stdout=listing, stderr=subprocess.DEVNULL, timeout=min(1.5, budget_left()))
            listing.seek(0)
            text = listing.read().decode("utf-8", "replace")
    except Exception:
        return None, "unknown"
    if proc.returncode != 0:
        return None, "refused"
    want = name.replace("\\", "/").translate(ASCII_LOWER)
    hits = []
    for line in text.splitlines():
        path = line[9:].strip().translate(ASCII_LOWER) if line.startswith("worktree ") else ""
        if path.endswith(want) and path[:-len(want)].endswith("/"):
            hits.append(line[9:].strip())
    return (os.path.normpath(hits[0]) if len(hits) == 1 else None), None


def _missing(path):
    """True only when the system says path does not exist; a folder that cannot be read is not a missing one."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False


WORKTREE_FIX = ("Delete each junction itself first, `[IO.Directory]::Delete('<junction>')` (it removes the link, "
                "not the target), then remove the worktree.")


def rule_worktree_remove(args, literal, loc):
    """`git worktree remove [--force] <path>` deletes through junctions inside the tree (git 2.53 emptied
    a junction's target with and without --force). args are the values after `git`; literal[i] is the
    literal text of args[i] (None when it holds an unknown variable); loc is the shell's location."""
    base, i = _git_base(args, literal, loc)
    j = i + 2   # args[i:i + 2] == ["worktree", "remove"]
    while j < len(args) and args[j].startswith("-") and args[j] != "--":
        j += 1
    j += 1 if j < len(args) and args[j] == "--" else 0
    if j >= len(args):
        return None   # git prints its usage
    target = _abs(literal[j], base)
    if target is None:
        return Finding(
            "git-worktree-junction", True,
            "`git worktree remove` deletes through junctions inside the tree and empties their targets, and the "
            "hook cannot tell which folder `%s` is (a variable, or a relative path after a `cd` it cannot follow). "
            "Pass an absolute path, or list the junctions with `Get-ChildItem <dir> -Recurse -Force -Attributes "
            "ReparsePoint`. %s" % (args[j], WORKTREE_FIX))
    # git's order: the suffix first, the path only without one match. An absolute path is never a suffix.
    hit, failure = (None, None) if _abs(literal[j], None) else _worktree_by_suffix(base, literal[j])
    if failure == "unknown" or (failure and not os.path.isdir(target)):
        return Finding(
            "git-worktree-junction", True,
            "`git worktree remove` deletes through junctions inside the tree and empties their targets, and "
            "the hook cannot tell which folder `%s` is: `git worktree list` failed or timed out. Pass the "
            "worktree's absolute path, or list the junctions with `Get-ChildItem <dir> -Recurse -Force "
            "-Attributes ReparsePoint`. %s" % (args[j], WORKTREE_FIX))
    if hit is None and not os.path.isdir(target):
        return None   # not a worktree: git refuses on its own
    if hit is not None and _missing(hit):
        return None   # a prunable worktree: git drops its record, there is nothing to empty
    target = hit or target
    links, finished = outside_links(target)
    if links:
        return Finding(
            "git-worktree-junction", True,
            "`git worktree remove` deletes through junctions, with or without --force: %s holds %s, so git "
            "would empty %s. %s"
            % (target, ", ".join("`%s`" % p for p, _ in links),
               ", ".join("`%s`" % (t or "an unreadable target") for _, t in links), WORKTREE_FIX))
    if not finished:
        return Finding(
            "git-worktree-junction", True,
            "`git worktree remove` deletes through junctions and empties their targets, and %s is too large to "
            "check, or a folder in it did not answer, within the hook's %.0f s budget for the whole command. "
            "Only links whose target is outside the tree matter: list them with "
            "`Get-ChildItem '%s' -Recurse -Force -Attributes ReparsePoint | Select-Object FullName, Target` and "
            "delete each one whose Target is outside the tree first, `[IO.Directory]::Delete('<junction>')` (it "
            "removes the link, not the target). When there are none, add `# shell-guard: allow` to the command."
            % (target, HOOK_BUDGET, target))
    return None


def rule_git(tool, args, piped, literal=None, loc=None, env=None):
    """Rules for one `git ...` command; args are the argument values after `git`, literal their literal
    text (None for an unknown variable), loc the shell's location, and env what the same command put in the
    environment (git_env_override)."""
    sub, rest = git_subcommand(args)
    if sub in HOOK_COMMANDS:
        override = git_config_override(args, literal or [None] * len(args)) or git_env_override(env or {})
        if override:
            return Finding(
                "git-no-verify", True,
                "`%s` can point git at another hooks folder, so `git %s` may skip the repository's hooks, and "
                "skipping hooks needs the user's explicit request. Run it without the override and "
                "fix what the hook reports; when the user asked for it, add `# shell-guard: allow`."
                % (override, sub))
    if sub in HOOK_FLAGS:
        flag = git_skips_hooks(sub, rest, literal[len(args) - len(rest):])
        if flag:
            return Finding(
                "git-no-verify", True,
                "`git %s %s` skips the repository's hooks, which needs the user's explicit request. "
                "Run it without the flag and fix what the hook reports." % (sub, flag))
    if sub == "worktree" and rest[:1] == ["remove"]:
        f = rule_worktree_remove(args, literal or [None] * len(args), loc)
        if f:
            return f
    if sub == "commit" and tool == "PowerShell" and not piped and git_commit_reads_stdin(rest):
        return Finding(
            "ps-git-commit-stdin", True,
            "`git commit -F -` reads the message from stdin, but nothing is piped into it: PowerShell passes "
            "the here-string after it as a pathspec (\"error: pathspec ...\"), so no commit is made. Use "
            "`git commit -m @'...'@` (closing `'@` at column 0), or write the message with the Write tool and "
            "run `git commit -F <file>`.")
    paths = []
    if sub in ("checkout", "restore"):
        words = literal or [None] * len(args)
        base, i = _git_base(args, words, loc)
        paths = git_restore_paths(sub, rest, words[i + 1:], base)
    if paths:
        return Finding(
            "git-restore-discards", True,
            "`git %s` on %s throws away every uncommitted edit in those files, not only yours (a red run that "
            "ends this way loses unrelated edits). Check `git diff --stat -- %s` first; revert your own change with "
            "the Edit tool, or end a red run with `safe-edit.ps1 -Restore`. When discarding is intended (a "
            "throwaway worktree), add the marker below."
            % (sub, " ".join(paths[:3]) + (" ..." if len(paths) > 3 else ""), " ".join(paths[:3])))
    return None


CD_NAMES = {"cd", "chdir", "set-location", "sl", "push-location", "pushd"}
POP_NAMES = {"pop-location", "popd"}
LOCATION_NAMES = CD_NAMES | POP_NAMES
LOCATION_AND_GIT = LOCATION_NAMES | {"git", "git.exe"}
# `$name = 'literal'` / `name='literal'`: the one value a variable holds when nothing else assigns it
PS_ASSIGN = re.compile(r"\$(?:script:|global:|local:)?(\w+)\s*((?:[-+*/%]|\?\?)?)=(?!=)", re.I)
PS_LITERAL_RHS = re.compile(r"""\s*(?:'((?:[^']|'')*)'|"([^"$`]*)")\s*(?=[;\r\n|)}]|$)""")
BASH_ASSIGN = re.compile(r"""(?:^|[\s;&|(])(\w+)=(?:'([^']*)'|"([^"$`]*)"|([^\s;&|()'"$`<>]*))""")
BASH_WORD_END = " \t\r\n;&|)<>"
VAR_WORD = re.compile(r"""^"?\$\{?(\w+)\}?([^$`"]*)"?$""")
PS_ENV_ASSIGN = re.compile(r"^\$(?:\{env:(\w+)\}|env:(\w+))\s*((?:\+|\?\?)?=)\s*(.*)$", re.I | re.S)
ENV_NAME = re.compile(r"^(?:.*[\\/])?env(?:\.exe)?$", re.I)
ENV_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir"}
BASH_EXPORTS = {"export", "declare", "typeset"}


def _literal_vars(cmd, shell):
    """{lowercase name: value} for variables this command assigns only from one literal."""
    vals, other = {}, set()
    if shell == "PowerShell":
        for m in PS_ASSIGN.finditer(cmd):
            name, rhs = m.group(1).lower(), PS_LITERAL_RHS.match(cmd, m.end())
            if rhs is None or m.group(2):   # computed, or a compound assignment (`+=`, `??=`)
                other.add(name)
                continue
            v = rhs.group(1).replace("''", "'") if rhs.group(1) is not None else rhs.group(2)
            if vals.setdefault(name, v) != v:
                other.add(name)
    else:
        for m in BASH_ASSIGN.finditer(cmd):
            name = m.group(1).lower()
            v = next(g for g in m.groups()[1:] if g is not None)
            # A right side that goes on past the match (`$(`, a backtick, `a$b`) is unknown, not what matched.
            if vals.setdefault(name, v) != v or cmd[m.end():m.end() + 1] not in BASH_WORD_END:
                other.add(name)
    return {k: v for k, v in vals.items() if k not in other}


def _literal_word(raw, value, shell, vars_, loc):
    """The literal text a word passes, with `$PWD`/`$HOME`/literal variables filled in; None if unknown."""
    if shell == "PowerShell" and raw.startswith("'"):
        text = value
    elif raw.startswith(("(", "@(")):
        return None   # an expression: `(Join-Path ...)`
    elif "$" in raw or "`" in raw:
        m = VAR_WORD.match(raw)
        if not m:
            return None
        name = m.group(1).lower()
        head = loc if name == "pwd" else os.path.expanduser("~") if name == "home" else vars_.get(name)
        if head is None:
            return None
        text = head + m.group(2)
    else:
        text = value
    if text == "~" or text.startswith(("~/", "~\\")):
        text = os.path.expanduser("~") + text[1:]
    return _msys_to_win(text) if shell == "Bash" else text


def _msys_to_win(path):
    """A Git Bash drive path, `/<letter>/rest`, in its Windows drive form (the letter upper-cased, then
    `:` and a backslash before `rest`); anything else unchanged."""
    m = re.match(r"^/([A-Za-z])(/|$)", path)
    return m.group(1).upper() + ":\\" + path[3:] if m else path


class _Location:
    """The shell's location as `cd` / `Set-Location` / `Push-Location` / `Pop-Location` move it; None
    once it cannot be followed (a variable target, `cd -`, popping an empty stack)."""

    def __init__(self, cwd):
        self.loc = cwd
        self.stack = []

    def walk(self, commands, bodies):
        """Yields each command. A subshell or substitution body leaves the location as it found it: at bodies'
        `end`, which an outer body can share with an inner one, it returns to where the outermost `first` was."""
        back = {end: first for first, end in sorted(bodies, reverse=True) if first < end}
        marks = []
        for k, words in enumerate(commands):
            if k in back:
                self.loc, self.stack = marks[back[k]]
            marks.append((self.loc, list(self.stack)))
            yield words

    def step(self, name, words):
        """words: (raw, value, quoted, literal text or None) after the command name."""
        low = name.lower()
        if low in POP_NAMES:
            self.loc = self.stack.pop() if self.stack else None
            return
        if low not in CD_NAMES:
            return
        if low in ("push-location", "pushd"):
            self.stack.append(self.loc)
        pos = [w for i, w in enumerate(words) if not (w[0].startswith("-") and not w[2])
               and not (i and words[i - 1][0].lower() in ("-path", "-literalpath", "-lp", "-stackname"))]
        named = [words[i + 1] for i, w in enumerate(words[:-1]) if w[0].lower() in ("-path", "-literalpath", "-lp")]
        target = (named or pos or [None])[0]
        self.loc = None if target is None else _abs(target[3], self.loc)


def _assignment(raw, value, shell, vars_, loc):
    """(NAME in upper case, the value's literal text or None) of a `NAME=value` word. Upper case, since Windows
    environment names ignore case: git reads a `git_config_count` set from either shell."""
    text = _literal_word(raw, value, shell, vars_, loc)
    if text is not None:
        name, _, rhs = text.partition("=")
        return name.rstrip("+").upper(), rhs
    name, _, rhs = value.partition("=")
    return name.rstrip("+").upper(), _literal_word(raw.partition("=")[2], rhs, shell, vars_, loc)


def env_command(words, shell, vars_, loc):
    """What `env` starts: words are (raw, value) after its name. (index of the command's name or None, {name:
    value} of the assignments before it)."""
    i, found = 0, {}
    while i < len(words):
        value = words[i][1]
        if value in ENV_VALUE_OPTS:
            i += 2
        elif value.startswith("-"):
            i += 1
        elif "=" in value:
            name, text = _assignment(words[i][0], value, shell, vars_, loc)
            found[name] = text
            i += 1
        else:
            return i, found
    return None, found


def _bash_export(cw, assigned, exported, vars_, loc):
    """`export NAME[=value] ...`, `declare -x ...`: what they move into the environment of later commands."""
    if cw[0].value != "export" and not any(re.match(r"^-\w*x", w.value) for w in cw[1:]):
        return
    for w in cw[1:]:
        if "=" in w.value:
            name, text = _assignment(w.raw, w.value, "Bash", vars_, loc)
            exported[name] = text
        else:   # an unset name is exported too: a later plain assignment reaches the environment
            exported.setdefault(w.value.upper(), assigned.get(w.value.upper(), ""))


def _bash_fields(words, literal):
    """(values, literal text) as bash splits an unquoted `$NAME` of known value: one word per field, none if empty."""
    values, texts = [], []
    for w, text in zip(words, literal):
        split = text is not None and w.raw.startswith("$")
        values += text.split() if split else [w.value]
        texts += text.split() if split else [text]
    return values, texts


def git_findings_bash(lex, cmd="", cwd=None):
    vars_, where = _literal_vars(cmd, "Bash"), _Location(_msys_to_win(cwd) if cwd else None)
    assigned, exported = {}, {}
    for words in where.walk(lex.commands, lex.bodies):
        _, cw = bash_command_words(words)
        before = dict(_assignment(w.raw, w.value, "Bash", vars_, where.loc)
                      for w in words[:len(words) - len(cw)] if "=" in w.raw)
        if not cw:   # no environment of its own: only what is already exported changes
            assigned.update(before)
            exported.update({k: v for k, v in before.items() if k in exported})
            continue
        if cw[0].value in BASH_EXPORTS:
            _bash_export(cw, assigned, exported, vars_, where.loc)
            continue
        env = dict(exported, **before)
        if ENV_NAME.match(cw[0].value):
            at, found = env_command([(w.raw, w.value) for w in cw[1:]], "Bash", vars_, where.loc)
            if at is None:
                continue
            env.update(found)
            cw = cw[at + 1:]
        if cw[0].value.lower() not in LOCATION_AND_GIT:
            continue
        lit = [_literal_word(w.raw, w.value, "Bash", vars_, where.loc) for w in cw[1:]]
        where.step(cw[0].value, [(w.raw, w.value, w.quoted, l) for w, l in zip(cw[1:], lit)])
        if cw[0].value.lower() in ("git", "git.exe"):
            args, lit = _bash_fields(cw[1:], lit)
            f = rule_git("Bash", args, piped=False, literal=lit, loc=where.loc, env=env)
            if f:
                return [f]
    return []


def _ps_env_step(words, env, vars_, loc):
    """`$env:NAME = value` and `${env:NAME} = value`: env[NAME] is its literal text, "" for `$null`, None when
    the hook cannot read it."""
    m = PS_ENV_ASSIGN.match(" ".join(w[0] for w in words))
    if m is None:
        return
    rhs, text = m.group(4), None
    literal = PS_LITERAL_RHS.match(rhs)
    if m.group(3) != "=":
        pass   # `+=` and `??=` depend on what is there
    elif literal:
        text = literal.group(1) if literal.group(1) is not None else literal.group(2)
    elif rhs.lower() == "$null":
        text = ""
    elif "$" in rhs or re.match(r"^[\w.+-]+$", rhs):
        text = _literal_word(rhs, rhs, "PowerShell", vars_, loc)
    env[(m.group(1) or m.group(2)).upper()] = text


def git_findings_ps(lex, cmd="", cwd=None):
    vars_, where, env = _literal_vars(cmd, "PowerShell"), _Location(cwd), {}
    for words in lex.commands:
        _ps_env_step(words, env, vars_, where.loc)
        k, name = ps_command_name(words)
        if name is None:
            continue
        rest, seen = words[k + 1:], env
        if ENV_NAME.match(name):
            at, found = env_command([(w[0], w[1]) for w in rest], "PowerShell", vars_, where.loc)
            if at is None:
                continue
            seen = dict(env, **found)
            name, rest = rest[at][1], rest[at + 1:]
        if name.lower() not in LOCATION_AND_GIT:
            continue
        lit = [_literal_word(w[0], w[1], "PowerShell", vars_, where.loc) for w in rest]
        where.step(name, [(w[0], w[1], w[2], l) for w, l in zip(rest, lit)])
        if name.lower() in ("git", "git.exe"):
            f = rule_git("PowerShell", [w[1] for w in rest], piped=words[0][3] in lex.piped,
                         literal=lit, loc=where.loc, env=seen)
            if f:
                return [f]
    return []


# --------------------------------------------------------------------------------------
# Narrow rules — classes that recurred after a written rule was not enough (two or three
# occurrences each). Installed next to the git rules, without the rest of the guard.
# --------------------------------------------------------------------------------------

# Truncating any output is normal; truncating the evidence is not. A gate is a test runner, a
# red run, or any producer whose full output the same pipeline is trying to keep (Tee-Object).
# Deliberately narrow. Truncating a test run to see its last lines is an everyday idiom and
# succeeds; only two shapes are always wrong: cutting a pipeline that is teeing a full log, and
# cutting the red-run driver, whose own restore line comes last.
# The red-run driver as an executed path: `safe-edit.ps1`, `~/…/safe-edit.ps1`, `<drive>:\…\safe-edit.ps1`.
GATE_RUNNER = re.compile(r"^(?:[^|;&<>]*[\\/])?safe-edit(?:\.ps1)?$", re.I)
TEE_NAMES = {"tee-object", "tee"}
SCRIPT_HOSTS = {"pwsh", "powershell", "bash", "sh"}
# interpreter options that take a value, so the word after them is not the script path
HOST_VALUE_OPTS = {
    "-executionpolicy", "-ep", "-ex", "-workingdirectory", "-wd", "-outputformat", "-of", "-o",
    "-inputformat", "-inf", "-if", "-configurationname", "-config", "-settingsfile", "-windowstyle",
    "-w", "-custompipename", "-encodedcommand", "-e", "-ec", "-version", "-v", "-psconsolefile",
    "+o",
}
# Select-Object -First and its unambiguous prefixes, alone or with `:N` attached
FIRST_FLAG = re.compile(r"^-f(?:i(?:r(?:st?)?)?)?(?::.*)?$", re.I)


def _host_runs_gate(host, args):
    """args are the words after pwsh/powershell/bash/sh; True if the script they run is safe-edit."""
    for i, a in enumerate(args):
        low = a.lower()
        if low in ("-file", "-f"):
            return i + 1 < len(args) and bool(GATE_RUNNER.match(args[i + 1]))
        if low in ("-command", "-c"):
            rest = " ".join(args[i + 1:])
            lex = (BashLexer(rest) if host in ("bash", "sh") else PsLexer(rest)).run()
            return any(_is_gate(nm, a2) for nm, a2 in _named_commands(lex, host))
        if a.startswith(("-", "+")):
            continue
        if i > 0 and args[i - 1].lower() in HOST_VALUE_OPTS:
            continue
        return bool(GATE_RUNNER.match(a))   # the first positional word is the script
    return False


def _named_commands(lex, host):
    """(name, argument values) of every command a lexer found."""
    out = []
    for words in lex.commands:
        if host in ("bash", "sh"):
            _, cw = bash_command_words(words)
            if cw:
                out.append((cw[0].value, [w.value for w in cw[1:]]))
        else:
            k, nm = ps_command_name(words)
            if nm:
                out.append((nm, [w[1] for w in words[k + 1:]]))
    return out


def _is_gate(name, args):
    """The red-run driver, invoked by its path or as the script of an interpreter."""
    if GATE_RUNNER.match(name):
        return True
    host = re.split(r"[\\/]", name)[-1].lower()
    host = host[:-4] if host.endswith(".exe") else host
    return host in SCRIPT_HOSTS and _host_runs_gate(host, args)


TRUNCATE_MSG = (
    "`%s` is piped into `%s`, which stops the producer as soon as its limit is reached: the exit code "
    "then reads as success and the tee'd log or the red run's restore line is cut off. Capture the "
    "whole output first (`$out = ... 2>&1`, or a file), then filter what you captured; `-Last N` / "
    "`tail` read the whole stream and are fine.")


def _gate_in_chain(chain):
    """chain: (name, args) of the commands feeding a truncator, nearest first; the producer to blame."""
    for idx, (name, args) in enumerate(chain):
        if name.lower() in TEE_NAMES:
            upstream = [nm for nm, _ in chain[idx + 1:] if nm.lower() not in TEE_NAMES]
            return upstream[0] if upstream else name
        if _is_gate(name, args):
            return name
    return None


def rule_truncated_gate_ps(lex):
    by_start = {words[0][3]: words for words in lex.commands}
    for words in lex.commands:
        k, name = ps_command_name(words)
        if name is None or words[0][3] not in lex.piped:
            continue
        low = name.lower()
        if low in ("select-object", "select"):
            if not any(not a[2] and FIRST_FLAG.match(a[0]) for a in words[k + 1:]):
                continue
        elif low not in ("head", "head.exe"):
            continue
        chain, cur, seen = [], words[0][3], set()
        while cur in lex.feeds and cur not in seen:
            seen.add(cur)
            cur = lex.feeds[cur]
            kk, nm = ps_command_name(by_start.get(cur, []))
            if nm:
                chain.append((nm, [a[1] for a in by_start[cur][kk + 1:]]))
        src = _gate_in_chain(chain)
        if src:
            return Finding("gate-truncated-pipeline", True, TRUNCATE_MSG % (src, name))
    return None


def rule_truncated_gate_bash(lex):
    for i, words in enumerate(lex.commands):
        _, cw = bash_command_words(words)
        if not cw or cw[0].value != "head" or words[0].start not in lex.piped:
            continue
        chain, j = [], i
        while j > 0 and lex.commands[j][0].start in lex.piped:
            j -= 1
            _, prev = bash_command_words(lex.commands[j])
            if prev:
                chain.append((prev[0].value, [w.value for w in prev[1:]]))
        src = _gate_in_chain(chain)
        if src:
            return Finding("gate-truncated-pipeline", True, TRUNCATE_MSG % (src, "head"))
    return None


# PowerShell ends a string at any quote of the family that opened it, typographic ones included:
# `"Zmiana „x” gotowa"` reaches the program as two arguments, `-b"…"` loses its quotes. Straight
# quotes of the other family, newlines and here-strings arrive intact.
TORN_BY = {'"': "„“”", "'": "‘’‚‛"}
# flag -> the file flag that replaces it (None: the flag has no file form)
PROSE_FLAGS = {"--body": "--body-file", "-b": "--body-file", "--notes": "--notes-file",
               "-n": "--notes-file", "--title": None, "-t": None}


def _torn_quote(raw):
    """(opening quote, typographic quote) for the first string in raw that holds a quote of its own
    family, or None."""
    if raw.startswith(("@'", '@"')):
        return None   # a here-string ends only at a line starting with its closer
    # '' and "" inside a string need no case: the scan goes on in the second half
    i, n = 0, len(raw)
    while i < n:
        q = raw[i]
        if q not in TORN_BY:
            i += 1
            continue
        j = i + 1
        while j < n and raw[j] != q:
            if raw[j] in TORN_BY[q]:
                return q, raw[j]
            j += 2 if raw[j] == "`" and q == '"' else 1
        i = j + 1
    return None


def rule_inline_prose(name, args):
    """PowerShell `gh pr|issue|release ... --body <prose>`; args are (raw, value) pairs after the command.

    PowerShell only: bash does not read typographic quotes as string delimiters."""
    if name.lower() not in ("gh", "gh.exe"):
        return None
    positional = [v for _, v in args if not v.startswith("-")]
    if not positional or positional[0] not in ("pr", "issue", "release"):
        return None
    for i, (raw, value) in enumerate(args):
        flag = text = None
        if value in PROSE_FLAGS and i + 1 < len(args):
            flag, text = value, args[i + 1][0]
        elif "=" in value and value.split("=", 1)[0] in PROSE_FLAGS:
            flag, text = value.split("=", 1)[0], raw
        elif value[:2] in PROSE_FLAGS and len(value) > 2 and not value.startswith("--"):
            flag, text = value[:2], raw   # attached short form: -b"text"
        torn = text is not None and _torn_quote(text)
        if not torn:
            continue
        other = "'...'" if torn[0] == '"' else '"..." (mind `$` in it)'
        file_flag = PROSE_FLAGS[flag]
        return Finding(
            "cli-inline-prose", True,
            "PowerShell ends a string opened by `%s` at a typographic `%s` too, so the text after `%s` "
            "reaches gh torn into pieces or stripped of its quotes. Quote it with %s instead%s."
            % (torn[0], torn[1], flag, other,
               "" if file_flag is None else ", or write it with the Write tool and pass `%s <file>`" % file_flag))
    return None


# Shell state does not survive between tool calls: a variable read in an earlier call is empty
# here, so writing a whole file from it destroys the file (it destroyed a report's history once).
WRITE_ALL = re.compile(r"\[(?:System\.)?IO\.File\]::WriteAll\w*\s*\(", re.I)
PS_VAR = re.compile(r"(?<!`)\$(?!(?i:env):)(?:[A-Za-z_][A-Za-z0-9_]*:)?([A-Za-z_][A-Za-z0-9_]*)")
PS_ASSIGNED = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*:)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:[-+*/%]|\?\?)?=(?!=)")
PS_MULTI_ASSIGNED = re.compile(r"\$[\w:]+(?:\s*,\s*\$[\w:]+)+\s*=(?!=)")
PS_OUT_VARIABLE = re.compile(r"-(?:OutVariable|ov|PipelineVariable|pv)(?::|\s+)['\"]?\+?([A-Za-z_]\w*)", re.I)
# names a command binds without an assignment: function parameters, param(), foreach heads
PS_BOUND = re.compile(
    r"(?:function\s+[\w-]+\s*\(([^)]*)\)|param\s*\(([^)]*)\)|foreach\s*\(\s*\$([A-Za-z_]\w*)\s+in\b)",
    re.I)
PS_BUILTIN_VARS = {
    "_", "null", "true", "false", "args", "pwd", "psscriptroot", "pscommandpath", "input",
    "matches", "host", "error", "lastexitcode", "home", "profile", "psitem", "this", "foreach",
    "psversiontable", "iswindows", "islinux", "ismacos", "iscoreclr", "myinvocation",
    "psboundparameters", "executioncontext", "pshome", "pid", "psedition", "psculture",
    "psuiculture", "shellid", "stacktrace", "nestedpromptlevel", "psstyle", "event", "eventargs",
    "sender", "errorview", "ofs",
}
# Set-Content / Out-File parameters (and common ones) that take no value
WRITE_SWITCHES = ("passthru", "force", "whatif", "confirm", "nonewline", "asbytestream", "verbose",
                  "debug", "usetransaction", "append", "noclobber")
WRITE_VALUE_PARAMS = ("path", "literalpath", "pspath", "lp", "filepath", "value", "inputobject",
                      "encoding", "filter", "include", "exclude", "credential", "stream", "width",
                      "erroraction", "warningaction", "informationaction", "errorvariable",
                      "warningvariable", "informationvariable", "outvariable", "outbuffer",
                      "pipelinevariable", "progressaction")
STALE_MSG = (
    "`$%s` is written into the file but never assigned in this command, and shell state does not survive "
    "between tool calls: it reads as `$null` and overwrites the file with nothing. Read the value in the "
    "same command, or append with the Edit tool / `Add-Content`.")


def _paren_args(cmd, open_idx):
    """Split the parenthesised argument list starting at cmd[open_idx] == '(' at top level."""
    depth, i, start, out = 0, open_idx, open_idx + 1, []
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c in "'\"":
            q = c
            i += 1
            while i < n and cmd[i] != q:
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                out.append(cmd[start:i])
                return out
        elif c == "," and depth == 1:
            out.append(cmd[start:i])
            start = i + 1
        i += 1
    return out


def _expandable(expr):
    """expr without its single-quoted strings, which expand nothing ('$x' is literal text)."""
    out, i, n, dq = [], 0, len(expr), False
    while i < n:
        c = expr[i]
        if c == "`":
            out.append(expr[i:i + 2])
            i += 2
            continue
        if c == "@" and expr.startswith("@'", i) and not dq:
            j = expr.find("\n'@", i)
            i = n if j < 0 else j + 3
            continue
        if c == '"':
            dq = not dq
        elif c == "'" and not dq:
            j = i + 1
            while j < n and not (expr[j] == "'" and expr[j + 1:j + 2] != "'"):
                j += 2 if expr[j] == "'" else 1
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _code_only(cmd):
    """cmd with every comment and the text of every string literal (quotes, here-strings) blanked, offsets
    kept: a call found in it is code, not a commit message, a comment or a here-string that mentions it, and
    an apostrophe in a comment opens no string. cmd holds straight quotes only (TYPOGRAPHIC_QUOTES).

    As in PowerShell, `#` and `<#` start a comment only where a word can start: at the start of a line or after
    whitespace or a statement separator, not after a backtick and not inside `${...}`, nor at a `#>` that
    _closes_block."""
    # '' and "" inside a string need no case: the two halves are blanked back to back
    out, i, n = list(cmd), 0, len(cmd)
    word_start = True
    while i < n:
        c = cmd[i]
        if c == "`":
            i += 2
            word_start = word_start and cmd[i - 1:i] in "\r\n"   # a line continuation changes nothing
            continue
        if cmd.startswith("${", i):
            i = PS_BRACED_VAR.match(cmd, i).end()
            word_start = False
            continue
        line_comment = word_start and c == "#" and not _closes_block(cmd, i)
        block_comment = word_start and cmd.startswith("<#", i)
        if not (line_comment or block_comment) and (
                c not in "'\"@" or (c == "@" and not re.match(r"@['\"][ \t]*\r?\n", cmd[i:i + 40]))):
            word_start = c in " \t\r\n;|&(){}"
            i += 1
            continue
        if line_comment:
            m = LINE_END.search(cmd, i)
            j = m.start() if m else n
        elif block_comment:
            j = cmd.find("#>", i + 2)
            j = n if j < 0 else j + 2
        elif c == "@":
            j = cmd.find("\n" + cmd[i + 1] + "@", i)
            j = n if j < 0 else j + 3
        else:
            j = i + 1
            while j < n and cmd[j] != c:
                j += 2 if cmd[j] == "`" and c == '"' else 1
            j += 1
        for k in range(i, min(j, n)):
            if out[k] != "\n":
                out[k] = " "
        i = j
    return "".join(out)


def _stale_var(expr, assigned):
    for m in PS_VAR.finditer(_expandable(expr)):
        name = m.group(1)
        low = name.lower()
        if low not in PS_BUILTIN_VARS and low not in assigned:
            return name
    return None


def _bound_vars(cmd):
    names = {m.group(1).lower() for m in PS_ASSIGNED.finditer(cmd)}
    for m in PS_MULTI_ASSIGNED.finditer(cmd):
        names.update(v.lower() for v in PS_VAR.findall(m.group(0)))
    names.update(m.group(1).lower() for m in PS_OUT_VARIABLE.finditer(cmd))
    for m in PS_BOUND.finditer(cmd):
        if m.group(3):
            names.add(m.group(3).lower())
        for group in (m.group(1), m.group(2)):
            if group:
                names.update(v.lower() for v in re.findall(r"\$([A-Za-z_]\w*)", group))
    return names


def _is_param(low, names, min_len=1):
    """True if `-low` is an accepted abbreviation of one of names."""
    return len(low) >= min_len and any(n.startswith(low) for n in names)


def _written_content(nm, rest):
    """The raw words a Set-Content / Out-File call writes: -Value / -InputObject, or Set-Content's
    second positional argument (the first when -Path is named)."""
    out, positional, path_named, value_named = [], [], False, False
    i = 0
    while i < len(rest):
        raw, _value, quoted, _pos = rest[i]
        if quoted or not re.match(r"^-[A-Za-z]", raw):
            positional.append(raw)
            i += 1
            continue
        flag, sep, attached = raw[1:].partition(":")
        low = flag.lower()
        is_content = _is_param(low, ("value",), 2) or _is_param(low, ("inputobject",), 3)
        value_named |= is_content
        path_named |= low in ("pat", "path", "pspath", "lp") or _is_param(low, ("literalpath",))
        takes_value = not sep and not (_is_param(low, WRITE_SWITCHES)
                                       and not _is_param(low, WRITE_VALUE_PARAMS))
        arg = attached if sep else (rest[i + 1][0] if takes_value and i + 1 < len(rest) else None)
        if is_content and arg:
            out.append(arg)
        i += 2 if takes_value else 1
    if nm in ("set-content", "sc") and not value_named:
        idx = 0 if path_named else 1
        if idx < len(positional):
            out.append(positional[idx])
    return out


def _appends(args):
    """True if Out-File's args hold -Append (any accepted prefix), not switched off by `:$false`."""
    for raw, _value, quoted, _pos in args:
        flag, _sep, attached = raw[1:].partition(":")
        if not quoted and raw.startswith("-") and _is_param(flag.lower(), ("append",)) \
                and attached.lower() != "$false":
            return True
    return False


def rule_stale_write_var(cmd, lex):
    cmd = cmd.translate(TYPOGRAPHIC_QUOTES)
    assigned = _bound_vars(cmd)
    for m in WRITE_ALL.finditer(_code_only(cmd)):
        for expr in _paren_args(cmd, m.end() - 1)[1:2]:   # [0] is the path, [2] the encoding
            name = _stale_var(expr, assigned)
            if name:
                return Finding("stale-write-var", True, STALE_MSG % name)
    by_start = {words[0][3]: words for words in lex.commands}
    for words in lex.commands:
        k, nm = ps_command_name(words)
        if nm is None or nm.lower() not in ("set-content", "sc", "out-file"):
            continue
        if nm.lower() == "out-file" and _appends(words[k + 1:]):
            continue   # appending $null leaves the file as it was
        exprs = _written_content(nm.lower(), words[k + 1:])
        # `$t | Set-Content f`: the stage feeding it is a bare expression, not a command
        src = by_start.get(lex.feeds.get(words[0][3]))
        if src and ps_command_name(src)[0] is None and not any(w[0] == "=" for w in src):
            exprs.append(" ".join(w[0] for w in src))
        for expr in exprs:
            name = _stale_var(expr, assigned)
            if name:
                return Finding("stale-write-var", True, STALE_MSG % name)
    return None


BARE_BASH_MSG = (
    "`bash` typed in PowerShell runs the System32 `bash.exe`, which is WSL, not Git Bash: Windows paths "
    "and CRLF scripts break there%s. Use the Bash tool, or Git Bash by its full path, "
    "`& \"$env:ProgramFiles\\Git\\bin\\bash.exe\" ...`; for WSL on purpose, call `wsl`.")


def _bare_bash(lex):
    for words in lex.commands:
        _, nm = ps_command_name(words)
        if nm is not None and nm.lower() in ("bash", "bash.exe"):
            return True
    return False


def _run_string(args):
    """The value of safe-edit's -Run (or -Ru, -Run:'...') among args (raw, value, quoted, pos), or None."""
    for i, (raw, value, _quoted, _pos) in enumerate(args):
        if not raw.startswith("-"):
            continue
        flag, sep, attached = value[1:].partition(":")
        if flag.lower() in ("ru", "run"):
            return attached if sep else (args[i + 1][1] if i + 1 < len(args) else None)
    return None


def rule_bare_bash_ps(lex):
    """A bare `bash` (any statement, pipeline stage or block) and one inside safe-edit's -Run string,
    which safe-edit runs with `pwsh -Command`."""
    if _bare_bash(lex):
        return Finding("ps-bare-bash", True, BARE_BASH_MSG % "")
    for words in lex.commands:
        k, nm = ps_command_name(words)
        if nm is None or not _is_gate(nm, [w[1] for w in words[k + 1:]]):
            continue
        run = _run_string(words[k + 1:])
        if run:
            inner = PsLexer(run).run()
            if not inner.errors and _bare_bash(inner):
                return Finding("ps-bare-bash", True,
                               BARE_BASH_MSG % " (safe-edit runs its -Run string with `pwsh -Command`)")
    return None


# .NET resolves a relative path against the process directory, which `cd` / Set-Location never moves
DOTNET_PATH_CALL = re.compile(r"\[(?:System\.)?IO\.(?:File\]::(?:ReadAll|WriteAll|AppendAll)\w*|Directory\]::(\w+))\s*\(",
                              re.I)
ROOTED_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")


def _literal_path_head(arg):
    """The literal text a path argument starts with: all of a '...' string, a "..." string up to its first
    `$`; None for a variable, a call or an expression."""
    a = arg.strip()
    if len(a) >= 2 and a[0] == a[-1] == "'":
        return a[1:-1].replace("''", "'")
    if len(a) >= 2 and a[0] == a[-1] == '"':
        return re.split(r"[$`]", a[1:-1], maxsplit=1)[0]
    return None


def rule_dotnet_relative_path(cmd):
    cmd = cmd.translate(TYPOGRAPHIC_QUOTES)
    code = _code_only(cmd)
    for m in DOTNET_PATH_CALL.finditer(code):
        args = _paren_args(cmd, m.end() - 1)
        for idx in ((0, 1) if (m.group(1) or "").lower() == "move" else (0,)):
            head = _literal_path_head(args[idx]) if idx < len(args) else None
            if head and not ROOTED_PATH.match(head):
                return Finding(
                    "ps-dotnet-relative-path", True,
                    "`%s` resolves the relative path `%s` against the process directory "
                    "(`[Environment]::CurrentDirectory`), not the location `cd` / Set-Location set, so it reads "
                    "or writes a file in another folder, often another checkout. Pass an absolute path, e.g. "
                    "`(Join-Path $PWD '%s')`." % (code[m.start():m.end() - 1].strip(), head, head))
    return None


# Aliases of cmdlets: their output is objects (or one string), not a native program's lines
PS_CMDLET_ALIASES = {
    "%", "?", "foreach", "where", "select", "sort", "group", "measure", "tee", "cat", "gc", "type", "echo",
    "write", "gci", "ls", "dir", "gi", "gp", "gm", "irm", "iwr", "iex", "sls", "oh", "ft", "fl", "fw", "pwd",
    "gl", "gal", "gcm", "h", "history", "rvpa", "gu", "compare", "diff", "ps", "gps", "gsv", "gdr", "gv",
}
# Every verb `Get-Verb` lists on PowerShell 7.6, plus the cmdlets outside that list (ForEach, Sort, Tee, Where)
PS_VERB_NOUN = re.compile(
    r"^(Add|Approve|Assert|Backup|Block|Build|Checkpoint|Clear|Close|Compare|Complete|Compress|Confirm|Connect|"
    r"Convert|ConvertFrom|ConvertTo|Copy|Debug|Deny|Deploy|Disable|Disconnect|Dismount|Edit|Enable|Enter|Exit|"
    r"Expand|Export|Find|ForEach|Format|Get|Grant|Group|Hide|Import|Initialize|Install|Invoke|Join|Limit|Lock|"
    r"Measure|Merge|Mount|Move|New|Open|Optimize|Out|Ping|Pop|Protect|Publish|Push|Read|Receive|Redo|Register|"
    r"Remove|Rename|Repair|Request|Reset|Resize|Resolve|Restart|Restore|Resume|Revoke|Save|Search|Select|Send|"
    r"Set|Show|Skip|Sort|Split|Start|Step|Stop|Submit|Suspend|Switch|Sync|Tee|Test|Trace|Unblock|Undo|Uninstall|"
    r"Unlock|Unprotect|Unpublish|Unregister|Update|Use|Wait|Watch|Where|Write)-[A-Z][A-Za-z]+$", re.I)


def _native_name(name, functions):
    low = name.lower()
    if not name or name[0] in "$({@" or low.endswith(".ps1") or low in functions:
        return False
    return low not in PS_CMDLET_ALIASES and not PS_VERB_NOUN.match(name)


def rule_native_nonewline(cmd, lex):
    """`git show HEAD:f | Set-Content -NoNewline f`: native output arrives as lines, -NoNewline joins them."""
    functions = {m.group(1).lower() for m in re.finditer(r"\bfunction\s+([\w-]+)", cmd, re.I)}
    by_start = {words[0][3]: words for words in lex.commands}
    for words in lex.commands:
        k, nm = ps_command_name(words)
        if nm is None or nm.lower() != "set-content":
            continue
        if not any(not q and raw.startswith("-") and _is_param(raw[1:].partition(":")[0].lower(), ("nonewline",))
                   and raw.partition(":")[2].lower() != "$false" for raw, _v, q, _p in words[k + 1:]):
            continue
        src = by_start.get(lex.feeds.get(words[0][3]))
        sk, sn = ps_command_name(src) if src else (None, None)
        if sn and _native_name(sn, functions):
            return Finding(
                "ps-native-nonewline", True,
                "`%s` output reaches Set-Content as separate lines, and -NoNewline joins them into one line (a "
                "restored file loses every line break). To put back a committed file use `git restore --source "
                "<rev> -- <path>`, or `git cat-file blob <rev>:<path> > <file>` in the Bash tool; otherwise drop "
                "-NoNewline or join the lines yourself (`-join \"`n\"`)." % sn)
    return None


def rule_bash_too_long(cmd):
    esc_len = len(cmd.replace("'", "'\\''"))
    if esc_len >= LONG_DENY:
        return Finding(
            "bash-too-long", True,
            "Bash commands over ~7.8 KB are truncated on Windows (this one is %d chars) — the result is "
            "`unexpected EOF` or a silently truncated heredoc file; write the content with the Write tool and "
            "run the file instead." % esc_len)
    return None


def rule_bash_backslash_collapse(cmd):
    """Git Bash's runtime turns every pair of backslashes into one when it reads the command line the Bash tool
    starts it with, before bash sees any quote or heredoc."""
    pos = cmd.find("\\\\")
    if pos < 0:
        return None
    start = cmd.rfind("\n", 0, pos) + 1
    end = cmd.find("\n", pos)
    line = cmd[start:len(cmd) if end < 0 else end]
    line = line.replace("\\\\", "\\")
    first = max(0, min(pos - start - 30, len(line) - 60))   # nothing before the first pair collapses
    return Finding(
        "bash-backslash-collapse", True,
        "Git Bash turns every `\\\\` in a Bash tool command into `\\` before bash reads it, inside quotes and "
        "`<<'EOF'` bodies too: line %d reaches bash as `%s`. Write code that holds backslashes with the Write tool "
        "and run it by path, and edit source with the Edit tool." % (cmd.count("\n", 0, pos) + 1, line[first:first + 60]))


def check_narrow(tool, cmd, cwd=None):
    """The recurring-class rules installed as their own filtered handlers (`--only-narrow`). cwd: the hook
    event's `cwd`, for a launcher given by a relative path."""
    if not isinstance(cmd, str) or not cmd.strip() or ALLOW_MARKER in cmd:
        return []
    if tool == "Bash":
        f = rule_bash_too_long(cmd) or rule_bash_backslash_collapse(cmd)
        if f:
            return [f]
        lex = BashLexer(cmd).run()
        if lex.errors:
            return []
        f = rule_truncated_gate_bash(lex)
        return [f] if f else []
    if tool == "PowerShell":
        lex = ps_lex(cmd)
        if lex.errors:
            return []
        for rule in (lambda: rule_truncated_gate_ps(lex), lambda: rule_stale_write_var(cmd, lex),
                     lambda: rule_bare_bash_ps(lex), lambda: rule_dotnet_relative_path(cmd),
                     lambda: rule_native_nonewline(cmd, lex)):
            f = rule()
            if f:
                return [f]
        moved = False   # after a location change the cwd does not place a launcher's folder
        for words in lex.commands:
            k, nm = ps_command_name(words)
            if nm:
                f = (rule_inline_prose(nm, [(cmd[w[3]:w[3] + len(w[0])], w[1]) for w in words[k + 1:]])
                     or rule_ps_bad_parameter(nm, words[k + 1:])
                     or rule_bat_metachar(nm, [(w[0], w[1], w[2]) for w in words[k + 1:]], "PowerShell",
                                          None if moved else cwd))
                if f:
                    return [f]
                moved = moved or nm.lower() in LOCATION_NAMES
        return []
    return []


def check_git(tool, cmd, cwd=None):
    """Only the git rules: what the narrow hook (`--only-git`) runs. cwd: the shell's location when the
    command starts (the hook event's `cwd`), for relative paths."""
    if not isinstance(cmd, str) or not cmd.strip() or ALLOW_MARKER in cmd:
        return []
    if tool == "Bash":
        f = rule_bash_too_long(cmd)
        if f:
            return [f]
        lex = BashLexer(cmd).run()
        return [] if lex.errors else git_findings_bash(lex, cmd, cwd)
    if tool == "PowerShell":
        lex = ps_lex(cmd)
        return [] if lex.errors else git_findings_ps(lex, cmd, cwd)
    return []


def check_installed(tool, cmd, cwd=None):
    """The git rules, then the narrow ones, in one process (`--pwsh-all`); the first deny wins."""
    return check_git(tool, cmd, cwd) or check_narrow(tool, cmd, cwd)


def check_bash(cmd, cwd=None):
    f = rule_bash_too_long(cmd)
    if f:
        return [f]
    lex = BashLexer(cmd).run()
    if lex.errors:
        kind, pos = lex.errors[0]
        if kind not in ("single", "double", "backtick"):
            return []
        line, col = line_col(cmd, pos)
        m = re.search(r'[A-Za-z]:\\[^"\n]*\\"', cmd) if kind == "double" else None
        if m:
            why = "`%s` ends in `\\\"`, which bash reads as an escaped quote; drop the trailing backslash or use forward slashes" % m.group(0)
        elif kind == "single" and "\\'" in cmd:
            why = "`\\'` does not escape inside '...' in bash; use '\\'' or double quotes, or put the code in a file"
        else:
            why = "check for an apostrophe inside '...', or put the code in a file with the Write tool"
        return [Finding(
            "bash-unterminated-quote", True,
            "The %s quote opened at line %d col %d never closes, so bash fails with `unexpected EOF`: %s."
            % ({"single": "single", "double": "double", "backtick": "backtick"}[kind], line, col, why))]
    if lex.empty_op:
        op, pos = lex.empty_op
        nxt = cmd[pos + len(op):].lstrip()
        if (op == "&" and nxt[:1] in ("'", '"')) or (op == "&&" and nxt.startswith("& ")):
            return [Finding(
                "bash-ps-call-operator", True,
                "`& \"path\"` is PowerShell's call operator and a syntax error in bash; drop the `&` and use a "
                "forward-slash path, or use the PowerShell tool.")]

    moved = False   # after a location change the cwd does not place a launcher's folder
    for words in lex.commands:
        env, cw = bash_command_words(words)
        if not cw:
            continue
        name_w, args = cw[0], cw[1:]
        name = name_w.value
        if PS_CMDLET.match(name):
            return [Finding(
                "bash-powershell-syntax", True,
                "`%s` is a PowerShell cmdlet, but this is the Bash tool (Git Bash); run the command with the "
                "PowerShell tool instead." % name)]
        moved = moved or name.lower() in LOCATION_NAMES
        if name == "cd" and len(args) >= 2 and args[0].value.lower() == "/d":
            return [Finding(
                "bash-cd-slash-d", True,
                "`cd /d` is cmd.exe syntax (bash: `cd: too many arguments`); use `cd \"%s\"`."
                % args[1].value.replace("\\", "/"))]
        bad = [w for w in [name_w] + args if w.unquoted_bs and WIN_PATH_BS.search(w.raw)]
        if bad:
            return [Finding(
                "bash-unquoted-backslash-path", True,
                "Bash strips the backslashes from the unquoted path `%s` (it becomes `%s`); write it as `%s`."
                % (bad[0].raw, bad[0].raw.replace("\\", ""), bad[0].raw.replace("\\", "/")))]
        if VERSIONED_PHP.match(name):
            return [Finding(
                "bash-versioned-php", True,
                "`%s` is a Herd .bat launcher that is not on the Git Bash PATH; use `\"%s/%s/php.exe\"` here, or "
                "the PowerShell tool where `%s` works." % (name, HERD_BIN_MSYS, name, name))]
        if re.match(r"^(\./)?vendor/bin/[A-Za-z0-9_.-]+$", name) and not name.endswith(".bat"):
            return [Finding(
                "bash-vendor-bin-shebang", True,
                "`%s` has a `#!/usr/bin/env php` shebang and Git Bash has no php binary on PATH; run "
                "`php %s ...` or use the PowerShell tool." % (name, name))]
        if name.lower() in NATIVE_PATHCONV and not any(e.startswith(("MSYS_NO_PATHCONV=", "MSYS2_ARG_CONV_EXCL=")) for e in env) \
                and "MSYS_NO_PATHCONV=1" not in cmd and "MSYS2_ARG_CONV_EXCL" not in cmd:
            hit = next((a for a in args if LINUX_ROOT.match(a.value)), None)
            if hit:
                return [Finding(
                    "bash-msys-path-mangle", True,
                    "Git Bash rewrites `%s` into a path under Git's install folder (`.../Program Files/Git/...`) "
                    "before %s sees it; prefix the command "
                    "with `MSYS_NO_PATHCONV=1`." % (hit.value, name))]
        f = rule_bat_metachar(name, [(a.raw, a.value, a.quoted) for a in args], "Bash", None if moved else cwd)
        if f:
            return [f]
    return []


def check_powershell(cmd, cwd=None):
    lex = ps_lex(cmd)
    if lex.errors:
        return []  # do not guess about a command the lexer could not follow
    for words in lex.commands:
        k, name = ps_command_name(words)
        if name is None:
            continue
        args = words[k + 1:]
        low = name.lower()
        if low in UNIX_TO_PS:
            return [Finding(
                "ps-unix-command", True,
                "`%s` does not exist in PowerShell here; use %s, or run the command with the Bash tool."
                % (name, UNIX_TO_PS[low]))]
        f = rule_ps_bad_parameter(name, args) or rule_bat_metachar(name, [(a[0], a[1], a[2]) for a in args], "PowerShell", cwd)
        if f:
            return [f]
    return []


def check(tool, cmd, cwd=None):
    if not isinstance(cmd, str) or not cmd.strip() or ALLOW_MARKER in cmd:
        return []
    if tool == "Bash":
        return check_bash(cmd, cwd) or check_narrow(tool, cmd, cwd) or check_git(tool, cmd, cwd)
    if tool == "PowerShell":
        return check_powershell(cmd, cwd) or check_narrow(tool, cmd, cwd) or check_git(tool, cmd, cwd)
    return []


# --------------------------------------------------------------------------------------
# Commit rule, shadow: one row per `git commit` in a repo that opts in through .claude/gates.json,
# saying whether a passing check came after the newest file it commits. It never denies. Each row is
# its own file, created with O_EXCL: one commit can run several handlers, and O_APPEND tears on Windows.
# --------------------------------------------------------------------------------------

FRESH_WORDS = re.compile(r"\bcommit\b")
COMMIT_WAIT = 2.5   # seconds a commit waits for its row at most; a git slower than that leaves the file empty
ROW_SPARE = 0.2     # of those, what a transcript read out of time leaves to write its row
ROW_CAP = 20000     # about 5 MB
GIT_NAMES = ("git", "git.exe")
ROW_FIELDS = ("ts", "sid8", "use12", "repo8", "op", "verdict", "reason", "staged", "deleted", "chained",
              "newest_age_s", "check_age_s", "reflog_age_s", "git_op", "ms")
GIT_OPERATIONS = ("MERGE_HEAD", "rebase-merge", "CHERRY_PICK_HEAD")
STAGED_NAMES = ("diff", "--cached", "--name-only", "--no-renames", "-z")
# Not `git diff`: on a stat-dirty file it rewrites the index, and --no-optional-locks does not stop that.
CHANGED_NAMES = ("ls-files", "-m", "-z")
UNTRACKED_NAMES = ("ls-files", "-o", "--exclude-standard", "-z")
PATHSPEC_MAGIC = re.compile(r"[*?\[]")
INDEX_NAMES = ("ls-files", "-z", "--")
PS_REDIRECT_OUT = re.compile(r"^[\d*]?>>?(.*)$")
# A stage before a commit changes no file only if it is one of these. Anything else can, and the rule reads the index
# and the mtimes before the command starts.
READ_ONLY = LOCATION_NAMES | {
    "cat", "grep", "rg", "echo", "printf", "tail", "head", "wc", "ls", "pwd", "test", "sort", "diff", "date",
    "select-object", "select", "select-string", "out-null", "out-string", "get-content", "get-childitem", "get-item",
    "get-location", "test-path", "resolve-path", "join-path", "split-path", "write-host", "write-output", "where-object",
    "foreach-object", "%", "measure-object", "new-object", "get-date"}
READ_ONLY_GIT = {"add", "status", "diff", "log", "show", "rev-parse", "ls-files", "branch", "config", "fetch"}


class _NoVerdict(Exception):
    """The files or the check cannot be judged; args[0] is the row's reason."""


class _Commit:
    """The first real `git commit` of a command: stages holds the commands before it, added their `git add`s
    as (folder, every change, literal paths), again says that a second commit follows it."""
    __slots__ = ("start", "base", "dry_run", "whole", "paths", "added", "stages", "lex", "again")

    def __init__(self, start, base, options, added, stages, lex):
        self.start = start
        self.base = base
        self.dry_run, self.whole, self.paths = options
        self.added = added
        self.stages = stages
        self.lex = lex
        self.again = False


def _ps_arguments(words):
    """A PowerShell command's (raw, value, quoted) without redirects. The lexer splits `2>&1` into three words."""
    kept, i = [], 0
    while i < len(words):
        raw = words[i][0]
        if not REDIRECT_TOKEN.match(raw):
            kept.append(words[i][:3])
            i += 1
        elif raw.endswith(">"):
            i += 3 if i + 1 < len(words) and words[i + 1][0] == "&" else 2
        else:
            i += 1
    return kept


def _ps_writes(words):
    """True when a PowerShell command sends output into a file. The lexer splits `2>&1` into three words."""
    for i, word in enumerate(words):
        found = PS_REDIRECT_OUT.match(word[0])
        target = found and (found.group(1) or (words[i + 1][1] if i + 1 < len(words) else "?"))
        if target and target != "&" and target.lower() not in NULL_FILES:
            return True
    return False


def _stages(tool, lex, cmd, cwd):
    """One (start offset, text, lower-case name, argument values, their literal text, location, redirects into a
    file) per command. The name is empty where nothing runs: an assignment, a bare value, a keyword."""
    bash = tool == "Bash"
    vars_, where = _literal_vars(cmd, tool), _Location(_msys_to_win(cwd) if bash and cwd else cwd)
    # PowerShell runs `( )` and `$( )` in the same process: a location set there stays.
    for words in where.walk(lex.commands, lex.bodies if bash else []):
        if bash:
            _, named = bash_command_words(words)
            name = named[0].value if named else None
            parts = [(w.raw, w.value, w.quoted) for w in named[1:]]
            start, text, writes = words[0].start, " ".join(w.raw for w in words), words[0].start in lex.writes
        else:
            k, name = ps_command_name(words)
            parts = [] if name is None else _ps_arguments(words[k + 1:])
            start, text, writes = words[0][3], " ".join(w[0] for w in words), _ps_writes(words)
            if name is None and words[0][0].startswith("[") and not PS_ASSIGN_TARGET.match(words[0][0]):
                name = words[0][0]   # a .NET call, `[IO.File]::WriteAllText(...)`
        literal = [_literal_word(raw, value, tool, vars_, where.loc) for raw, value, _ in parts]
        if name:
            where.step(name, [part + (text_,) for part, text_ in zip(parts, literal)])
        yield start, text, (name or "").lower(), [part[1] for part in parts], literal, where.loc, writes


def _commit_options(args, literal):
    """(dry run, every tracked change, literal paths) of `git commit` args, read as git reads them."""
    dry_run = whole = False
    paths, i = [], 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            paths.extend(literal[i:])
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            whole |= name == "--all"
            dry_run |= _long_prefix(name, "--dry-run", 4)
            if "=" not in a and any(_long_prefix(name, v, 4) for v in COMMIT_VALUE_LONG):
                i += 1
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:]):
                whole |= ch == "a"
                if ch in "mFCct":
                    i += 1 if j == len(a) - 2 else 0   # value in the next word unless attached
                    break
                if ch in "Su":
                    break   # optional value, attached
        else:
            paths.append(literal[i - 1])
    return dry_run, whole, paths


def _add_targets(args, literal):
    """(every change, literal paths) of `git add` args. A path of None is what the rule cannot read: an unknown
    word, or an option that picks the files another way (`-p`, `--pathspec-from-file`)."""
    whole, paths, only_paths = False, [], False
    for a, text in zip(args, literal):
        if only_paths or not a.startswith("-"):
            paths.append(text)
        elif a == "--":
            only_paths = True
        elif a.startswith("--"):
            every = _long_prefix(a, "--all", 3) or _long_prefix(a, "--update", 3)
            whole |= every
            if not every and not _long_prefix(a, "--verbose", 3) and not _long_prefix(a, "--force", 3):
                paths.append(None)
        elif set(a[1:]) <= set("Auvf"):
            whole |= "A" in a or "u" in a
        else:
            paths.append(None)
    return whole, paths


def find_commit(tool, cmd, cwd):
    """The first real `git commit` in cmd as a _Commit, None when there is none or the lexer gave up."""
    lex = BashLexer(cmd).run() if tool == "Bash" else ps_lex(cmd) if tool == "PowerShell" else None
    if lex is None or lex.errors:
        return None
    stages, added, found = [], [], None
    for stage in _stages(tool, lex, cmd, cwd):
        start, _, name, args, literal, loc, _ = stage
        sub, rest = git_subcommand(args) if name in GIT_NAMES else (None, [])
        if found:
            found.again |= sub == "commit"
            continue
        if sub == "add":
            base, i = _git_base(args, literal, loc)
            added.append((base,) + _add_targets(rest, literal[i + 1:]))
        elif sub == "commit":
            base, i = _git_base(args, literal, loc)
            found = _Commit(start, base, _commit_options(rest, literal[i + 1:]), added, stages, lex)
            continue
        elif "git" in args and name not in GIT_NAMES and git_subcommand(args[args.index("git") + 1:])[0] == "add":
            added.append((None, False, []))   # another program runs it: xargs, find -exec
        stages.append(stage)
    return found


def _find_repo(folder):
    """(worktree root, git dir) of the nearest `.git` at or above folder, (None, None) when there is none or a
    `.git` file names no git dir. `.git` is a file in a linked worktree or a submodule, its path often relative."""
    while True:
        dot = os.path.join(folder, ".git")
        if os.path.isdir(dot):
            return folder, dot
        if os.path.isfile(dot):
            with open(dot, encoding="utf-8", errors="surrogateescape") as fh:
                found = re.match(r"gitdir:\s*(.+?)\s*$", fh.readline())
            return (folder, os.path.normpath(os.path.join(folder, found.group(1)))) if found else (None, None)
        parent = os.path.dirname(folder)
        if parent == folder:
            return None, None
        folder = parent


def _read_gates(root):
    """root's gates.json when it opts in to the commit rule, None when it does not, False when malformed."""
    try:
        with open(os.path.join(root, ".claude", "gates.json"), "rb") as fh:
            gates = json.loads(fh.read())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    if not isinstance(gates, dict):
        return False
    if gates.get("commit") != "shadow":
        return None
    lists = (gates.get("checks"), gates.get("scope_commit"))
    return gates if all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists) else False


def _git_names(root, *args):
    """The names one git call prints. It has no timeout of its own: commit_row runs on a worker that the hook
    stops waiting for, and its output has a pipe of its own, so a hung git holds neither the commit nor stdout."""
    import subprocess
    proc = subprocess.run(["git", "--no-optional-locks", "-C", root] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise _NoVerdict("git")
    return [name for name in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def _commit_files(commit, root):
    """Every repo-relative file this commit takes: the index, plus what the command stages first. A `git add` counts
    from its own folder and only inside this repo; one the rule cannot read raises _NoVerdict."""
    def relative(text, base):
        full = None if text is None or text.startswith(":") else _abs(text, base)   # `:` opens pathspec magic
        if full is None:
            raise _NoVerdict("paths")
        return os.path.relpath(full, root).replace(os.sep, "/")

    files = set(_git_names(root, *STAGED_NAMES))
    if commit.whole:
        files.update(_git_names(root, *CHANGED_NAMES))
    if commit.paths:
        files.update(_git_names(root, *CHANGED_NAMES, "--", *[relative(p, commit.base) for p in commit.paths]))
    for base, whole, paths in commit.added:
        if base is None or not os.path.isdir(base):   # PowerShell's `$PWD.Path` reads as a folder that is not there
            raise _NoVerdict("paths")
        here = _find_repo(base)[0]
        if here is None or not os.path.samefile(here, root):
            continue
        paths = [relative(path, base) for path in paths]
        if whole:
            files.update(_git_names(root, *CHANGED_NAMES))
            files.update(_git_names(root, *UNTRACKED_NAMES))
            continue
        wide = [p for p in paths if PATHSPEC_MAGIC.search(p) or os.path.isdir(os.path.join(root, p))]
        if wide:
            files.update(_git_names(root, *CHANGED_NAMES, "--", *wide))
            files.update(_git_names(root, *UNTRACKED_NAMES, "--", *wide))
        literal = [p for p in paths if p not in wide]
        gone = {p for p in literal if not os.path.lexists(os.path.join(root, p))}
        if gone and gone - set(_git_names(root, *INDEX_NAMES, *gone)):   # neither on disk nor in the index
            raise _NoVerdict("paths")
        files.update(literal)
    return files


def _mtimes(root, files):
    """The mtimes of those files that exist; a deletion has none."""
    found = []
    for name in files:
        try:
            found.append(os.stat(os.path.join(root, name)).st_mtime)
        except OSError:
            pass
    return found


def _writes(commit, checks, reader):
    """For each stage before the commit, whether it may change a file. A redirect counts on any command but a check."""
    def writes(stage):
        name, args = stage[2], stage[3]
        reads = not name or name in READ_ONLY or name in GIT_NAMES and git_subcommand(args)[0] in READ_ONLY_GIT
        return (stage[6] or not reads) and reader.classify_check(stage[1], "", False, checks) is None

    return [writes(stage) for stage in commit.stages]


def _check_before(commit, checks, reader, writes):
    """(a check runs earlier in this command, only `&&` links it to the commit and nothing after it writes). The
    second is `pending`: the check has not run yet, and if it fails the commit does not happen. A pipe in between
    hides its exit status. The lexer lists a `$()` before the command that holds it, so its offset is the later one."""
    runs = [reader.classify_check(stage[1], "", False, checks) is not None for stage in commit.stages]
    start = commit.start
    for stage, is_check, write in zip(reversed(commit.stages), reversed(runs), reversed(writes)):
        if stage[0] > start:
            continue
        if commit.lex.after.get(start) != "&&" or write:
            return any(runs), False
        if is_check:
            return True, True
        start = stage[0]
    return any(runs), False


def _judge(row, commit, cmd, event, root, gates, reader, deadline):
    """(verdict, reason) of one commit, filling row's counts on the way."""
    files = _commit_files(commit, root)
    scoped = [name for name in files if reader.scope_match(name, gates["scope_commit"])]
    mtimes = _mtimes(root, scoped)
    row["staged"], row["deleted"] = len(files), len(scoped) - len(mtimes)
    writes = _writes(commit, gates["checks"], reader)
    same_call = commit.again or any(writes)   # the files and mtimes above are from before the command
    if not scoped and not same_call:
        return "out-of-scope", None
    if commit.dry_run:
        return "dry-run", None
    if ALLOW_MARKER in cmd:
        return "marker", None
    if not mtimes:
        return "unknown", "same_call" if same_call else "deleted"
    now, newest = time.time(), max(mtimes)
    row["newest_age_s"] = round(now - newest, 1)
    row["chained"], pending = _check_before(commit, gates["checks"], reader, writes)
    if pending:
        return "pending", None
    streams = reader.stream_paths(event.get("transcript_path"), event.get("agent_id"))
    passed = reader.last_pass(streams, root, gates["checks"], deadline - _clock() - ROW_SPARE)
    t_pass = reader.to_epoch(passed.ts) if passed.ts else None
    row["check_age_s"] = None if t_pass is None else round(now - t_pass, 1)
    if t_pass is not None and newest < t_pass:
        return ("unknown", "same_call") if same_call else ("fresh", passed.reason)
    if passed.hidden and newest < reader.to_epoch(passed.hidden):
        return "unknown", "same_call" if same_call else "hidden"
    return ("stale" if passed.reason == "ok" else "unknown"), passed.reason


def _git_history(git_dir):
    """(seconds since the reflog last moved, the operation in progress): a rebase or a checkout resets mtimes."""
    try:
        age = round(time.time() - os.stat(os.path.join(git_dir, "logs", "HEAD")).st_mtime, 1)
    except OSError:
        age = None
    return age, next((name for name in GIT_OPERATIONS if os.path.exists(os.path.join(git_dir, name))), None)


def commit_row(event, command, deadline):
    """Claims and writes the row of the call in event. deadline is a _clock() value. Up to the claim anything
    that goes wrong ends silently, since there is no file yet to say so in."""
    started = _clock()
    commit = find_commit(event.get("tool_name"), command, event.get("cwd"))
    root, git_dir = _find_repo(commit.base) if commit and commit.base else (None, None)
    gates = _read_gates(root) if root else None
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if gates is None or not data:
        return
    sys.pycache_prefix = os.path.join(data, "pycache")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the launcher's -I drops the script folder
    import turn_evidence as reader
    rows = os.path.join(data, "commit-gate", "rows")
    os.makedirs(rows, exist_ok=True)
    if len(os.listdir(rows)) >= ROW_CAP:
        return
    use_id = event.get("tool_use_id")
    use12 = reader.row_key(use_id) if use_id and isinstance(use_id, str) else None
    name = "%s.json" % use12 if use12 else "noid-%d.json" % os.getpid()
    # FileExistsError here means another handler has this commit: log_commit's worker ends on it.
    fd = os.open(os.path.join(rows, name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    row = dict.fromkeys(ROW_FIELDS)
    row.update(ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), sid8=str(event.get("session_id") or "")[:8],
               use12=use12, repo8=reader.row_key(reader.norm(root))[:8], op="commit")
    try:
        if use12 is None:
            row.update(verdict="error", reason="use_id")
        elif gates is False:
            row.update(verdict="error", reason="gates")
        else:
            row["reflog_age_s"], row["git_op"] = _git_history(git_dir)
            row["verdict"], row["reason"] = _judge(row, commit, command, event, root, gates, reader, deadline)
    except _NoVerdict as exc:
        row.update(verdict="unknown", reason=exc.args[0])
    except Exception as exc:
        row.update(verdict="error", reason=type(exc).__name__)
    row["ms"] = int((_clock() - started) * 1000)
    os.write(fd, (json.dumps(row) + "\n").encode("ascii"))
    os.close(fd)


def log_commit(event):
    """Runs commit_row on a daemon worker and waits at most COMMIT_WAIT, as outside_links does, so a slow git or
    a large transcript costs a commit no more than that. Never raises."""
    try:
        command = (event.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or not FRESH_WORDS.search(command):
            return
        import threading
        timeout = min(COMMIT_WAIT, budget_left())
        deadline = _clock() + timeout

        def work():
            try:
                commit_row(event, command, deadline)
            except Exception:   # no traceback on stderr
                pass

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(timeout)
    except Exception:
        pass


def main():
    start_budget()
    if os.environ.get("SHELL_GUARD", "").lower() in ("off", "0", "false"):
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        argv = sys.argv[1:]
        run = (check_installed if "--pwsh-all" in argv else check_narrow if "--only-narrow" in argv
               else check_git if "--only-git" in argv else check)
        tool, command = event.get("tool_name"), (event.get("tool_input") or {}).get("command")
        findings = run(tool, command, event.get("cwd"))
    except Exception:
        return 0
    deny = next((f for f in findings if f.deny), None)
    if deny is None and run is not check_narrow:
        log_commit(event)
    if deny is None:
        return 0
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "shell-guard: %s (False positive or intended? Add a trailing `# %s`.)" % (deny.message, ALLOW_MARKER),
    }}))   # ASCII JSON: stdout may be a legacy code-page pipe, and a raw „ would reach the reader as invalid UTF-8
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

