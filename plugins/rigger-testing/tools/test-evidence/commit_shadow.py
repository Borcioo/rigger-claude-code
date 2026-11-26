"""commit_shadow: the commit rule's shadow row, a Claude Code PreToolUse hook for the Bash and PowerShell tools.

It reads the hook event from stdin and, for a `git commit` in a repo that opts in through `.claude/gates.json`,
writes one row saying whether a passing check came after the newest file the commit takes. It prints nothing
and never denies: the shell guard is rigger-shellwise.

Contract:
  * stdlib only, never raises, exits 0;
  * a command holding the marker `shellwise: allow` gets the row verdict `marker`;
  * TEST_EVIDENCE=off in the environment disables the hook.

Usage as a library (tests): `find_commit(tool_name, command, cwd)`, `commit_row(event, command, deadline)`.
"""
import json
import os
import re
import sys
import time

ALLOW_MARKER = "shellwise: allow"


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
            ended = bool(words)   # a newline after a whole command ends it; one right after `|` or `&&` does not
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
            if op != "\n" or ended or last_sig not in ("&&", "||", "|", "|&"):
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
            k += 2 if v == "function" else 1   # `function f { cd x; }`: the name is no command
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
    def __init__(self, src, nested=False):
        self.s = src
        self.n = len(src)
        self.commands = []   # list of lists of (raw, value, quoted)
        self.errors = []
        # start offsets of commands that may run never or more than once: inside `{ }` (a loop, a branch, a
        # function, a script block) or a `( )` / `$( )` (it can sit behind `-and`, in a loop's condition)
        self.nested = set()
        self._all_nested = nested
        self.piped = set()   # start offsets of commands that a single `|` feeds
        # start offset of a piped command -> start offset of the stage feeding it; a stage that
        # takes a script block (`ForEach-Object { ... }`) feeds the next `|`, not its block body
        self.feeds = {}
        self.after = {}      # start offset of a command -> the operator before it

    def _sub(self, i):
        """i just after '$(' or '@(' or '(' ; returns index after matching ')'."""
        sub = PsLexer(self.s, nested=True)
        j = sub._lex(i, stop=")")
        self.commands.extend(sub.commands)
        self.nested |= sub.nested
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
            ended = bool(words)   # a newline after a whole command ends it; one right after `|` or `&&` does not
            if words:
                if pipe_pending:
                    self.piped.add(words[0][3])
                    if pipe_src is not None:
                        self.feeds[words[0][3]] = pipe_src
                    pipe_pending = False
                self.after[words[0][3]] = last_sig
                self.commands.append(words)
                if brace or self._all_nested:
                    self.nested.add(words[0][3])
                last_stage = words[0][3]
            words = []
            if op != "\n" or ended or last_sig not in ("&&", "||", "|"):
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


LINE_END = re.compile(r"[\r\n]")   # either one ends a PowerShell line comment
REDIRECT_TOKEN = re.compile(r"^(\d|\*)?>>?|^<|^&$|^\|")


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


# Long options whose value is the next word, so a `--no-verify` there is text, not a flag. git accepts
# any unambiguous prefix of a long option (`--no-veri` skips hooks; `--no-ver` is ambiguous with
# `--no-verbose` and refused).
COMMIT_VALUE_LONG = ("--message", "--file", "--reuse-message", "--reedit-message", "--fixup", "--squash",
                     "--author", "--date", "--template", "--trailer", "--cleanup", "--pathspec-from-file")


def _long_prefix(opt, full, min_len):
    return len(opt) >= min_len and full.startswith(opt)


# Seconds the whole hook process may spend; log_commit waits at most what is left of it.
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


CD_NAMES = {"cd", "chdir", "set-location", "sl", "push-location", "pushd"}
POP_NAMES = {"pop-location", "popd"}
LOCATION_NAMES = CD_NAMES | POP_NAMES
PS_LITERAL_ASSIGN = re.compile(r"""\$(\w+)[ \t]*=[ \t]*(?:'((?:[^']|'')*)'|"([^"$`]*)")""")
BASH_ASSIGN_WORD = re.compile(r"^([A-Za-z_]\w*)=([^$`]*)$")
# A word that is one variable, alone or before a literal tail: `$w`, `"${w}"`, `"$w\x"`. The tail must start
# with a character the shell keeps as text there (TAIL_START, read in the spike: unquoted PowerShell splits
# `$w=x` into two arguments and reads `$w.x` as a member, `"$w:x"` names another variable); anything else
# (`$w[0]`, `${w:-x}`, `"${w}..\x"`) makes the word unknown.
VAR_WORD = re.compile(r"""^("?)\$(?:\{(\w+)\}|(\w+))([^$`"]*)\1$""")
TAIL_START = {("PowerShell", ""): "\\/+@%^~-", ("PowerShell", '"'): "\\/+@%^~-=,.",
              ("Bash", ""): "/=,+@%^~-.:", ("Bash", '"'): "\\/=,+@%^~-.:"}
# Assigning these does not leave the value read back: read-only, constant or set by the shell itself.
BASH_SPECIAL_VARS = {
    "BASHOPTS", "BASHPID", "BASH_ARGC", "BASH_ARGV", "BASH_ARGV0", "BASH_COMMAND", "BASH_LINENO", "BASH_SOURCE",
    "BASH_SUBSHELL", "BASH_VERSINFO", "EPOCHREALTIME", "EPOCHSECONDS", "EUID", "FUNCNAME", "GROUPS", "HISTCMD",
    "LINENO", "OLDPWD", "PPID", "PWD", "RANDOM", "SECONDS", "SHELLOPTS", "SRANDOM", "UID", "_",
    # written by builtins that do not name them: read, select, mapfile, getopts, coproc, [[ =~ ]], a pipeline
    "IFS", "REPLY", "MAPFILE", "OPTARG", "OPTIND", "COPROC", "BASH_REMATCH", "PIPESTATUS"}
BASH_OPENERS = {"if", "while", "until", "for", "select", "case", "{"}
BASH_CLOSERS = {"fi", "done", "esac", "}"}
BASH_REDIRECT = re.compile(r"(?:\d+|&)?(?:>>?|<<<|<<-?|<>|<|>&|<&|>\|)[ \t]*(?:[^\s;&|()<>]+)?")
NEXT_OP = ("&&", "||", "|&", ";;", "\r\n", "|", "&", ";", "\n", ")", "}")
STRAIGHT_BEFORE = (None, ";", "\n", "(")   # the operator before a command that runs exactly once, in order


def _var_key(name, shell):
    """A variable's name as the shell compares it: PowerShell ignores case, bash does not."""
    return name.lower() if shell == "PowerShell" else name


def _word_end(lex, word):
    """Offset just after a word in the lexed text, or None when its raw text is not that span (a line
    continuation inside it, a backtick body's mapped offsets)."""
    raw, start = (word[0], word[3]) if isinstance(word, tuple) else (word.raw, word.start)
    return start + len(raw) if lex.s[start:start + len(raw)] == raw else None


def _op_after(lex, words, shell):
    """The operator that ends a command (`&&`, `;`, a newline, `)`, ...), None at the end of the text, "?" when
    the text after its last word cannot be read."""
    s, i = lex.s, _word_end(lex, words[-1])
    if i is None:
        return "?"
    while True:
        while i < len(s) and s[i] in " \t":
            i += 1
        m = BASH_REDIRECT.match(s, i) if shell == "Bash" else None
        if m and m.end() > i:   # the bash lexer keeps no redirect in the words
            i = m.end()
            continue
        break
    if i >= len(s):
        return None
    if s[i] == "#":
        return "\n"
    return next((op for op in NEXT_OP if s.startswith(op, i)), "?")


def _bash_depths(lex):
    """Per command, True when it sits inside a compound command (`if`/`while`/`until`/`for`/`select`/`case`,
    a function body) opened inside its innermost subshell or substitution body: it may run never or more than
    once. A plain `{ }` group runs once and does not count."""
    out = []
    stack, entering, after = [], [], []   # stack: True for a block, False for a plain `{ }` group (runs once)
    for words in lex.commands:
        entering.append(sum(stack))
        for i, w in enumerate(words):
            if w.raw in BASH_OPENERS:
                stack.append(not (w.raw == "{" and i == 0 and lex.after.get(words[0].start) != ")"))
            elif w.raw in BASH_CLOSERS:
                stack = stack[:-1]
            elif w.raw not in BASH_RESERVED and not (i == 1 and words[0].raw == "function"):
                break
        after.append(sum(stack))
    # a subshell or substitution body starts from the same place each time it runs: count from its start
    for k in range(len(lex.commands)):
        inner = max((first for first, end in lex.bodies if first <= k < end), default=None)
        out.append(after[k] > (entering[inner] if inner is not None else 0))
    return out


def _views(lex, shell):
    """(index, start, op before, nested, assignment words, name or None, argument words) per command; a word
    is (raw, value, start). Nested: it may run never or more than once."""
    if shell == "PowerShell":
        for k, words in enumerate(lex.commands):
            start = words[0][3]
            n, name = ps_command_name(words)
            args = [] if name is None else [(w[0], w[1], w[3]) for w in words[n + 1:]]
            yield k, start, lex.after.get(start), start in lex.nested, [], name, args
        return
    depths = _bash_depths(lex)
    for k, words in enumerate(lex.commands):
        env, cw = bash_command_words(words)
        assigns = [(w.raw, w.value, w.start) for w in words[:len(words) - len(cw)] if w.raw not in BASH_RESERVED]
        args = [(w.raw, w.value, w.start) for w in cw[1:]]
        yield k, words[0].start, lex.after.get(words[0].start), depths[k], assigns, cw[0].value if cw else None, args


PS_ASSIGN_AFTER = re.compile(r"(?:\s*,\s*\$[\w:{}]+)*\s*(?:[-+*/%]|\?\?)?=(?!=)|\+\+|--")
MAX_MENTIONS = 400   # past this many `$`, no variable is read: the scans below grow with the square of the text


def _plain_reads(cmd, shell):
    """Offsets of the names in `$name` / `${name}` mentions that only read the variable: not after `]` (a cast,
    `[ref]`) or an escape, not before an assignment or `++`/`--` (also as one of `$a, $b = ...`), and in bash
    not a `${name...}` with an operator."""
    reads = set()
    for m in re.finditer(r"(?<![`\\])\$(\{)?(\w+)(\})?", cmd):
        if m.group(1) and not m.group(3):
            continue   # `${w:=x}`, `${w=x}`: an operator inside the braces
        j = m.start() - 1
        while j >= 0 and cmd[j] in " \t":
            j -= 1
        if j >= 0 and cmd[j] == "]":
            continue   # a cast: `[ref]$w`, `[ref] $w`
        if shell == "PowerShell" and m.group(1) is None and cmd[m.end():m.end() + 1] == ":":
            continue   # `$w:x` is the variable `x` of drive or scope `w`
        if shell == "PowerShell" and PS_ASSIGN_AFTER.match(cmd, m.end()):
            continue   # bash never assigns through a `$` mention
        reads.add(m.start(2))
    return reads


PS_VAR_WRITERS = {"set-variable", "sv", "set", "new-variable", "nv", "clear-variable", "clv", "remove-variable", "rv",
                  "get-variable", "gv", "invoke-expression", "iex"}
BASH_CODE_RUNNERS = {"eval", "source", ".", "trap", "let"}
BASH_VAR_WRITERS = {"declare", "typeset", "local", "readonly", "export", "read", "mapfile", "readarray", "getopts",
                    "unset"}
COMPUTED_WORD = re.compile(r"""^["']?(?:\$|`)""")


def _writes_vars_by_name(lex, shell):
    """True when the command may set a variable without naming it as a word: it runs text as code in this shell
    (`Invoke-Expression`, dot-sourcing, `eval`, `source`, `trap`), uses PowerShell's variable cmdlets, drive or
    API (names may be wildcards or built at run time), or gives a bash builtin that sets variables a name it
    computes (`printf -v "$n"`)."""
    if shell == "PowerShell":
        if re.search(r"(?i)variable:|psvariable|sessionstate", lex.s):
            return True
        return any(words[0][0] == "." or (ps_command_name(words)[1] or "").lower() in PS_VAR_WRITERS
                   for words in lex.commands)
    for words in lex.commands:
        _, cw = bash_command_words(words)
        if not cw:
            continue
        name, args = cw[0].value, cw[1:]
        if name in BASH_CODE_RUNNERS or (name in BASH_VAR_WRITERS and any(COMPUTED_WORD.match(w.raw) for w in args)):
            return True
        if name == "printf" and any(w.value == "-v" and (i + 1 >= len(args) or COMPUTED_WORD.match(args[i + 1].raw))
                                    for i, w in enumerate(args)):
            return True
    return False


def _bare_text(lex, shell, skip=()):
    """The unquoted text of every word but those starting at an offset in skip, one word per line, with each
    `$name` / `${...}` mention blanked: where a bare variable name can stand as a word."""
    if shell == "PowerShell":
        quoted = re.compile(r"""@'.*?\n'@|@".*?\n"@|'(?:[^']|'')*'|"(?:[^"`]|`.|"")*"|`.""", re.S)
        words = [(w[0], w[1]) for c in lex.commands for w in c if w[3] not in skip]
    else:
        quoted = re.compile(r"""\$'(?:[^'\\]|\\.)*'|'[^']*'|"(?:[^"\\]|\\.)*"|\\.""", re.S)
        words = [(w.raw, w.value) for c in lex.commands for w in c if w.start not in skip]
    lines = [re.sub(r"\$\{[^}]*\}?|\$[\w:]+", " ", quoted.sub(" ", raw)) for raw, _ in words]
    # a quoted word that is a name and nothing else can name the variable: `-OutVariable 'w'`, `Set-Variable "w"`
    lines += [value for raw, value in words if raw[:1] in "'\"" and re.match(r"^\+?[A-Za-z_]\w*$", value)]
    return "\n".join(lines)


def _literal_vars(cmd, shell, lex):
    """{name as the shell compares it: (value, offset after its assignment)} for each variable that holds one
    literal for certain. Every mention of its name in the text (as a word anywhere: `$w`, `${w}`, `w` in
    `-OutVariable w`, `foreach ($w in`, a string, a comment) is either a certain assignment or a plain read
    (_plain_reads), and PowerShell binds it nowhere (`foreach ($w in`, `param($w)`). A certain assignment is a
    statement of its own from a literal, outside every block, run exactly once in order (after the start, `;`
    or a newline; before `;`, a newline, the end or, in bash, `&&`), to a name the shell does not own; in
    PowerShell with no `#` or `--%` before it on its line and no bare CR in the command, where the hook's lexer
    and PowerShell may read the text apart. `$PWD` and `$HOME` count when never assigned."""
    if cmd.count("$") > MAX_MENTIONS:
        return {}
    cands, reads = {}, _plain_reads(cmd, shell)
    bound = set()
    if shell == "PowerShell":
        for m in PS_BOUND.finditer(cmd):
            bound |= {v.lower() for g in m.groups() if g for v in re.findall(r"\$(\w+)", "$" + g if "$" not in g else g)}
    bare_cr = shell == "PowerShell" and re.search(r"\r(?!\n)", lex.s)
    for k, start, before, nested, assigns, name, _ in _views(lex, shell):
        if nested or before not in (None, ";", "\n") or bare_cr:
            continue
        words = lex.commands[k]
        after = _op_after(lex, words, shell)
        if shell == "PowerShell":
            line = lex.s[lex.s.rfind("\n", 0, start) + 1:start]
            if "#" in line or "--%" in line:
                continue
            end = _word_end(lex, words[-1])
            m = PS_LITERAL_ASSIGN.fullmatch(lex.s, start, end) if end is not None else None
            if m and after in (None, ";", "\n", "\r\n") and m.group(1).lower() not in PS_BUILTIN_VARS:
                value = m.group(2).replace("''", "'") if m.group(2) is not None else m.group(3)
                cands.setdefault(m.group(1).lower(), []).append((value, start + 1, end))
        elif name is None and assigns and after in (None, ";", "\n", "\r\n", "&&") \
                and not any(first <= k < end for first, end in lex.bodies):   # a subshell keeps its own
            for raw, value, at in assigns:
                m = BASH_ASSIGN_WORD.match(raw)
                if m and m.group(1) not in BASH_SPECIAL_VARS and m.group(1) not in ("HOME",):
                    cands.setdefault(m.group(1), []).append((value.partition("=")[2], at, at + len(raw)))
    if _writes_vars_by_name(lex, shell):
        return {}
    flags = re.I if shell == "PowerShell" else 0
    bare = _bare_text(lex, shell, {at for entries in cands.values() for _, at, _ in entries})
    found = {"\0IFS": (None, None)} if shell == "Bash" and re.search(r"(?<!\w)IFS(?!\w)", cmd) else {}
    names = list(cands) + (["pwd", "home"] if shell == "PowerShell" else ["PWD", "HOME"])
    for key in names:
        entries = cands.get(key, [])
        if key in bound:
            continue
        allowed = reads | {at for _, at, _ in entries}
        name = re.escape(key)
        if any(m.group(1) or m.start(2) not in allowed
               for m in re.finditer(r"\$\{?(\w+:)?(%s)(?!\w)" % name, cmd, flags)):
            continue   # a `$` mention that is no plain read, or one with a scope or drive (`$script:w`)
        if re.search(r"(?:^|(?<=[\s:{(,=+]))%s(?![\w/\\.]|-\w)" % name, bare, flags):
            continue   # the bare name as a word: `-OutVariable w`, `read w`, `for w in`, `unset w`, `variable:w`
        named = r"(['\"])\+?%s\1|:%s(?![\w/\\.])|`%s(?!\w)" % (name, name, name)
        if shell == "Bash":
            named += r"|(?<!\S)-[A-Za-z]+%s(?!\w)" % name
        if re.search(named, cmd, flags):
            continue   # named inside a string, a drive path or an option: `='w'`, `'Vari'+'able:w'`, `` `w ``, `-vw`
        if not entries:
            found[key] = (None, -1)   # `$PWD` / `$HOME`, never assigned: what the shell sets
        elif len({v for v, _, _ in entries}) == 1 and entries[0][2] is not None:
            found[key] = (entries[0][0], entries[0][2])
    return found


def _bash_expands(raw):
    """True when bash expands the unquoted text of a word into other text: a glob (`*`, `?`, `[`), a brace
    list or range (`{a,b}`, `{1..3}`), or a `~` that is not alone or before a slash."""
    if raw.startswith("~") and not (raw == "~" or raw.startswith("~/")):
        return True
    i, n, plain = 0, len(raw), []
    while i < n:
        c = raw[i]
        if c == "\\":
            plain.append(" ")
            i += 2
        elif c == "'":
            j = raw.find("'", i + 1)
            plain.append(" ")
            i = n if j < 0 else j + 1
        elif c == '"':
            j = i + 1
            while j < n and raw[j] != '"':
                j += 2 if raw[j] == "\\" else 1
            plain.append(" ")
            i = j + 1
        else:
            plain.append(c)
            i += 1
    text = "".join(plain)
    return bool(re.search(r"[*?\[]", text) or re.search(r"(?<!\$)\{[^{}]*(?:,|\.\.)[^{}]*\}", text))


def _literal_word(raw, value, shell, vars_, loc, at=None, pathspec=False):
    """The literal text a word passes, with `$PWD`/`$HOME`/literal variables filled in; None if unknown.
    at: where the command holding the word starts; a variable counts only when assigned before it.
    pathspec: a bash glob counts as typed (the commit shadow reads `git add src/*.py` as git's pathspec)."""
    if shell == "PowerShell" and raw.startswith("'"):
        text = value
    elif raw.startswith("(") or (shell == "PowerShell" and raw.startswith("@") and not raw.startswith(("@'", '@"'))):
        return None   # an expression `(Join-Path ...)`, `@(...)`, a splat `@a` (any number of arguments)
    elif "$" in raw or "`" in raw:
        m = VAR_WORD.match(raw)
        tail = m.group(4) if m else ""
        # after `${w}` the braces end the name: any tail is text, but unquoted PowerShell still reads `.` and `[`
        braced_ok = m and m.group(2) and (shell == "Bash" or m.group(1) or re.match(r"[\w:]", tail))
        if not m or (tail and not braced_ok and tail[0] not in TAIL_START[(shell, m.group(1))]):
            return None
        if shell == "Bash" and ("\\" in tail if not m.group(1) else "\\\\" in tail):
            return None   # bash drops an unquoted backslash, and `\\` in double quotes is one
        name = m.group(2) or m.group(3)
        key = _var_key(name, shell)
        held, end = vars_.get(key, (None, None))
        if end is None or at is None or end > at:
            return None
        head = held if end >= 0 else (loc if key in ("pwd", "PWD") else os.path.expanduser("~"))
        if head is None:
            return None
        if shell == "Bash" and not m.group(1) and ("\0IFS" in vars_ or not pathspec and (
                re.search(r"[*?\[]", head) or _bash_expands(m.group(4)))):
            return None   # unquoted: bash expands a glob in the value or in the tail, and splits on IFS
            # (_bash_fields splits on whitespace; an IFS set anywhere in the command makes that unknown)
        text = head + m.group(4)
    else:
        if shell == "Bash" and not pathspec and _bash_expands(raw):
            return None
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
    """The shell's location as `cd` / `Set-Location` / `Push-Location` / `Pop-Location` move it. It is a set of
    candidates, each (folder or None, push stack): a change that may not run (after `&&` or `||`, before `||`,
    in a pipeline stage or the background, to a folder that does not exist yet) adds where the shell would be
    without it. A candidate folder is None once it cannot be followed (a variable target, `cd -`, popping an
    empty stack); a change inside a block (a loop, a branch, a function, a script block, a PowerShell `( )`)
    leaves the location unknown from the block's first command on, as the block may run never or more than once."""

    MAX = 4   # candidates kept; past that the location is unknown

    def __init__(self, cwd, lex=None, shell=None):
        self.lex, self.shell = lex, shell
        self.freeze_at = None if lex is None else _location_in_block(lex, shell)
        self.frozen = False
        self.states = [(cwd, ())]
        self.pending = []   # where the shell is if an `&&` chain stopped before its location change

    @property
    def loc(self):
        """The one folder, None when unknown or when there are several candidates."""
        return self.states[0][0] if len(self.states) == 1 else None

    def locs(self):
        """Every candidate folder (None among them when one is unknown)."""
        return list(dict.fromkeys(loc for loc, _ in self.states))

    def _union(self, extra):
        merged = list(dict.fromkeys(self.states + extra))
        self.states = merged if len(merged) <= self.MAX else [(None, ())]

    def walk(self, commands, bodies):
        """Yields each command. A subshell or substitution body leaves the location as it found it: at bodies'
        `end`, which an outer body can share with an inner one, it returns to where the outermost `first` was."""
        back = {end: first for first, end in sorted(bodies, reverse=True) if first < end}
        marks = []
        for k, words in enumerate(commands):
            if self.freeze_at is not None and k >= self.freeze_at:
                self.frozen, self.states, self.pending = True, [(None, ())], []
            elif k in back:
                self.states, self.pending = marks[back[k]], []
            if self.pending and self.lex.after.get(_start(words)) not in ("&&", "|", "|&"):
                self._union(self.pending)   # past the end of the `&&` chain
                self.pending = []
            marks.append(list(self.states))
            yield words

    @staticmethod
    def _move(low, words, loc, stack):
        if low in POP_NAMES:   # `popd +1` drops another entry: unknown
            return ((stack[-1], stack[:-1]) if stack else (None, ())) if not words else (None, ())
        if not words and low not in ("push-location", "pushd"):
            return os.path.expanduser("~"), stack   # `cd` alone goes home, in bash and in PowerShell 7
        if low in ("push-location", "pushd"):
            stack = stack + (loc,)
        pos = [w for i, w in enumerate(words) if not (w[0].startswith("-") and not w[2])
               and not (i and words[i - 1][0].lower() in ("-path", "-literalpath", "-lp", "-stackname"))]
        named = [words[i + 1] for i, w in enumerate(words[:-1]) if w[0].lower() in ("-path", "-literalpath", "-lp")]
        target = (named or pos or [None])[0]
        if target is None or (low == "pushd" and re.match(r"^[+-]\d+$", target[1])):
            return None, stack   # no folder, or `pushd +1` rotating the stack
        return _abs(target[3], loc), stack

    def lose(self):
        """A command that may move the location in a way the hook does not follow: unknown from here."""
        if not self.frozen:
            self.states, self.pending = [(None, ())], []

    def step(self, name, words, command=None):
        """words: (raw, value, quoted, literal text or None) after the command name; command: the lexed words of
        the whole command, to read the operators around it."""
        low = _location_key(name)
        if self.frozen or low not in LOCATION_NAMES:
            return
        old = list(self.states)
        if not words and command is not None and self.lex is not None \
                and self.lex.after.get(_start(command)) in ("|", "|&"):
            self.states = [(None, ())]   # `'src' | Set-Location`: the folder comes down the pipeline
        else:
            self.states = list(dict.fromkeys(self._move(low, words, loc, stack) for loc, stack in old))
        if command is None or self.lex is None:
            return
        before, after = self.lex.after.get(_start(command)), _op_after(self.lex, command, self.shell)
        fails = any(loc is not None and not os.path.isdir(loc) for loc, _ in self.states)
        if before not in STRAIGHT_BEFORE + ("&&",) or after in ("||", "|", "|&", "&", "?"):
            self._union(old)   # it may run or not, or run in a subshell of a pipeline stage or the background
        elif before == "&&" or (after == "&&" and fails):
            self.pending = list(dict.fromkeys(self.pending + old))   # past the chain it may not have run
        elif fails:
            self._union(old)   # a folder that does not exist yet: the change fails unless an earlier step makes it


def _start(words):
    return words[0][3] if isinstance(words[0], tuple) else words[0].start


LOCATION_RUNNERS_BASH = {"builtin", "command", "eval", "source", ".", "trap"}
LOCATION_RUNNERS_PS = {"invoke-expression", "iex"}


def _location_key(name):
    """A command name as the location rules compare it: `Microsoft.PowerShell.Management\\Set-Location` is
    `set-location`."""
    return name.rsplit("\\", 1)[-1].lower()


def _runs_unknown(name, words, shell):
    """True for a command that may move the location in a way the hook does not follow: text run as code
    (`eval`, `source`, `.`, `Invoke-Expression`, dot-sourcing) or a builtin run through `builtin`/`command`."""
    if shell == "PowerShell":
        return words[0][0] == "." or _location_key(name) in LOCATION_RUNNERS_PS
    return name in LOCATION_RUNNERS_BASH or words[0].raw == "coproc"   # a coprocess runs in a subshell


def _location_in_block(lex, shell):
    """The index of the first command of the earliest block (a loop, a branch, a function, a script block, a
    PowerShell `( )`/`$( )`) that holds a location change, None when no block does: from there on the location
    is unknown, as the block may run never or more than once."""
    run = None   # where the current run of nested commands started
    for k, _, _, nested, _, name, _ in _views(lex, shell):
        run = (k if run is None else run) if nested else None
        if nested and name is not None and (_location_key(name) in LOCATION_NAMES
                                            or _runs_unknown(name, lex.commands[k], shell)):
            return run
    return None


# names a command binds without an assignment: function parameters, param(), foreach heads
PS_BOUND = re.compile(
    r"(?:function\s+[\w-]+\s*\(([^)]*)\)|param\s*\(([^)]*)\)|foreach\s*\(\s*\$\{?([A-Za-z_]\w*)\}?\s+in\b)",
    re.I)
PS_BUILTIN_VARS = {
    "_", "null", "true", "false", "args", "pwd", "psscriptroot", "pscommandpath", "input",
    "matches", "host", "error", "lastexitcode", "home", "profile", "psitem", "this", "foreach",
    "psversiontable", "iswindows", "islinux", "ismacos", "iscoreclr", "myinvocation",
    "psboundparameters", "executioncontext", "pshome", "pid", "psedition", "psculture",
    "psuiculture", "shellid", "stacktrace", "nestedpromptlevel", "psstyle", "event", "eventargs",
    "sender", "errorview", "ofs",
}


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
    vars_, where = _literal_vars(cmd, tool, lex), _Location(_msys_to_win(cwd) if bash and cwd else cwd, lex, tool)
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
        literal = [_literal_word(raw, value, tool, vars_, where.loc, start, pathspec=True) for raw, value, _ in parts]
        if name:
            where.step(name, [part + (text_,) for part, text_ in zip(parts, literal)], words)
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
    """Runs commit_row on a daemon worker and waits at most COMMIT_WAIT, so a slow git or
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
    if os.environ.get("TEST_EVIDENCE", "").lower() in ("off", "0", "false"):
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        return 0
    log_commit(event)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

