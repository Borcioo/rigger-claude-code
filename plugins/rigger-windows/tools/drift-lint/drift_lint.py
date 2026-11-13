"""Report references in the harness's rule files that point at things that no longer exist.

python -I tools/drift-lint/drift_lint.py [options]

One finding per line: `file:line  check  reference`. Checks: path (absolute, ~, $HOME and
$env:USERPROFILE paths), repo-path (tools/... and templates/... in the skill repos, the skill's own
folder or the plugin holding the file), skill, agent, command; read (a missing or unreadable input,
0 files scanned) and
stale-baseline (a baseline entry matching no finding). Findings listed in the baseline file are
suppressed. Findings in upstream copies (wp-*, wordpress-router, wpds, blueprint) are
listed in their own section and do not change the exit code. Exit 1 when the main section has a
finding, 0 otherwise. The summary line counts the plugins' rules files read (repo plugins and the
installed plugins of --plugins-dir, each file once); --plugins-dir given and 0 rules files read through
it (rules files of --repo do not count) is a `read` finding.
"""
import argparse
import bisect
import fnmatch
import glob
import json
import os
import re
import shutil
import stat
import sys

REPOS_ENV = "HARNESS_DRIFT_REPOS"   # skill repo roots, separated by os.pathsep; used when no --repo is given
DEFAULT_UPSTREAM = ["wp-*", "wordpress-router", "wpds", "blueprint"]
BUILTIN_AGENTS = {"general-purpose", "Explore", "Plan", "claude-code-guide", "statusline-setup", "claude"}

# PowerShell aliases and keywords, Bash and cmd.exe builtins: not files on PATH.
SHELL_WORDS = {
    "cd", "echo", "export", "source", "set", "unset", "if", "then", "else", "elif", "fi", "for",
    "while", "until", "do", "done", "case", "esac", "exit", "return", "test", "read", "eval", "exec",
    "trap", "alias", "type", "printf", "pwd", "true", "false", "local", "declare", "shift", "wait",
    "command", "builtin", "hash", "ulimit", "umask", "jobs", "fg", "bg", "time", "function",
    "dir", "del", "copy", "move", "ren", "rd", "md", "mklink", "start", "call", "cls", "ver",
    "foreach", "try", "catch", "finally", "param", "throw", "switch", "begin", "process", "end",
    "filter", "break", "continue", "class", "enum", "using",
    "ls", "cat", "rm", "cp", "mv", "gci", "gc", "sl", "iwr", "irm", "ni", "ri", "sleep", "kill",
    "ps", "man", "mkdir", "rmdir", "clear", "tee", "measure", "select", "sort", "where", "gcm",
    "gi", "gl", "gp", "sc", "si", "sp", "write", "group", "compare", "diff", "ft", "fl", "ogv",
}
CMDLETS = {
    "add-content", "clear-content", "compare-object", "convertfrom-json", "convertto-json",
    "copy-item", "export-csv", "foreach-object", "format-list", "format-table", "get-childitem",
    "get-command", "get-content", "get-date", "get-filehash", "get-item", "get-itemproperty",
    "get-location", "get-member", "get-nettcpconnection", "get-process", "get-winevent",
    "group-object", "import-csv", "invoke-restmethod", "invoke-webrequest", "join-path",
    "measure-object", "move-item", "new-item", "new-object", "out-file", "out-null", "read-host",
    "remove-item", "rename-item", "resolve-path", "select-object", "select-string", "set-content",
    "set-location", "set-strictmode", "sort-object", "split-path", "start-process", "start-sleep",
    "stop-process", "test-path", "where-object", "write-host", "write-output", "get-credential",
    "out-gridview", "set-itemproperty", "get-acl", "set-acl", "test-netconnection",
    "convertfrom-csv", "convertto-csv", "get-psdrive", "get-service", "restart-service",
    "start-job", "receive-job", "wait-job", "invoke-expression", "invoke-command",
    "get-volume", "new-psdrive", "get-ciminstance", "get-scheduledtask", "register-scheduledtask",
    "unregister-scheduledtask", "new-scheduledtasktrigger", "new-scheduledtaskaction",
}
# Skip class "code statement": a span or line that is source code, not a shell command.
CODE_KEYWORDS = {"import", "from", "require", "require_once", "include", "global", "const", "let",
                 "var", "await", "async", "def", "new", "raise", "lambda"}
ASSIGNMENTS = {"=", "=>", ":=", "+=", "==", "->"}
# Skip class "server command": a helper a doc shows for a remote host, never run on this machine.
REMOTE_COMMANDS = {"htpasswd", "rrsync"}
# Skip class "project-local tool": installed per project (npm/composer bin), run through npx or vendor/bin.
PROJECT_BINS = {"wp-env", "biome", "astro", "knip", "mago", "vue-tsc", "playwright"}
SHELL_LANGS = {"", "bash", "sh", "shell", "console", "powershell", "pwsh", "ps1", "ps", "cmd", "bat", "zsh"}

DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:(?!\s[A-Za-z]:[\\/])[^`'\"<>|*?;\n])*")
HOME_PATH = re.compile(r"(?<![\w~$])(~|\$(?i:home|env:userprofile))[\\/][^\s`'\"<>|*?;]*")
GLOB_TAIL = re.compile(r"[^\s\\/`'\"<>|;]*")
ELLIPSIS = re.compile(r"…|\.\.\.")
REPO_PATH = re.compile(r"(?<![\w.\\/:~$%-])(?:tools|templates)[\\/][^\s`'\"<>|*?;]*")
SSH = re.compile(r"\bssh\b")
REMOTE = re.compile(r"\b(?:remote|servers?)\b", re.I)  # a heading
REMOTE_LINE = re.compile(r"\bremote\b|\b(?:on|to|from)\s+(?:the\s+|a\s+|that\s+|this\s+)?servers?\b", re.I)  # a line
SHELL_DEFAULT = re.compile(r"\$\{\w+:-$")
HEADING = re.compile(r"^#{1,6}\s+\S")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
PLAIN_WORDS = re.compile(r"^[A-Za-z]+(?:\s+[A-Za-z]+)+$")
SKILL_COLON = re.compile(r"(?<![\w-])skill:([A-Za-z0-9][\w:.-]*)")
SKILL_PHRASE = re.compile(
    r"(?<![\w-])(?:[Ll]oad(?:s|ed|ing)?|[Ss]ee|[Ss]kill)\s+(?:the\s+)?(?:skill\s+)?`([A-Za-z0-9][\w:-]*)`"
    r"|`([A-Za-z0-9][\w:-]*)`\s+skill\b")
AGENT_PHRASE = re.compile(
    r"(?<![\w-])subagent_type[\"'`]?\s*[:=]\s*[\"'`]?([A-Za-z0-9][\w:-]*)"
    r"|`([A-Za-z0-9][\w:-]*)`\s+(?:sub)?agent\b"
    r"|(?<![\w-])[Aa]gent type:?\s+`([A-Za-z0-9][\w:-]*)`")
CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
PROMPT = re.compile(r"^\s*(?:PS[^>]*>|\$|>)\s+")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
COMMAND_TOKEN = re.compile(r"^[A-Za-z][\w.+-]*$")
VERB_NOUN = re.compile(r"^[A-Z][a-z]+-[A-Z][A-Za-z]+$")
FILE_NAME = re.compile(r"\.(?!(?:exe|cmd|bat|com)$)[A-Za-z0-9]{1,5}$", re.I)
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
TRAILING = ".,;:)]}'\"*"
SKILL_HEADER = re.compile(r"^skills?$", re.I)
AGENT_HEADER = re.compile(r"^(?:agents?|agent type|subagent_type|subagent)$", re.I)


def is_link(path):
    """A junction or a symlink: its target is scanned through its own root, not through the link."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def read_lines(path, lenient=False):
    """Lines of a UTF-8 file; invalid bytes raise UnicodeDecodeError unless lenient (upstream copies)."""
    with open(path, encoding="utf-8", errors="replace" if lenient else "strict") as fh:
        return fh.read().splitlines()


def skill_files(root, onerror):
    """Every SKILL.md under root, without following links or entering dot folders and node_modules.

    A skill's own references/ folder is not entered: a SKILL.md there is a copied document (the
    vendored upstream skills of rigger-wp), not a skill the harness lists."""
    found = []
    for folder, dirs, files in os.walk(root, onerror=onerror):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"
                   and not is_link(os.path.join(folder, d))
                   and not (d == "references" and "SKILL.md" in files)]
        if "SKILL.md" in files:
            found.append(os.path.join(folder, "SKILL.md"))
    return found


def baseline_key(path, source):
    """path relative to the grandparent of the input it was found through, with / separators:
    `.claude/CLAUDE.md`, `<repo>/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md`."""
    anchor = os.path.dirname(os.path.dirname(os.path.abspath(source)))
    try:
        rel = os.path.relpath(os.path.abspath(path), anchor)
    except ValueError:
        rel = os.path.abspath(path)
    return rel.replace("\\", "/")


def load_baseline(path):
    """{entry line: line number}; an entry is `relative-file<TAB>check<TAB>reference`; # and blank lines skipped.

    A malformed line is kept as it is: it matches no finding, so it is reported as stale.
    """
    entries = {}
    for n, line in enumerate(read_lines(path), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            entries[line] = n
    return entries


def frontmatter_name(path, lenient=False):
    try:
        lines = read_lines(path, lenient)
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:40]:
        if line.strip() == "---":
            break
        m = re.match(r"name:\s*['\"]?([^'\"\s]+)", line)
        if m:
            return m.group(1)
    return None


def plugin_inventory(plugins_dir):
    """(skills, agents, folders) of installed plugins: skills bare and as `plugin:name`, agents as
    `plugin:name`, and each plugin's folder: installPath, else cache/<market>/<plugin>/<version>; also
    every <dir>/<plugin> (rules only)."""
    skills, agents = set(), set()
    installs = []
    try:
        with open(os.path.join(plugins_dir, "installed_plugins.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        for key, entries in data.get("plugins", {}).items():
            for entry in entries:
                if entry.get("installPath"):
                    installs.append((key.split("@")[0], entry["installPath"]))
    except (OSError, ValueError, AttributeError):
        cache = os.path.join(plugins_dir, "cache")
        for market in os.listdir(cache) if os.path.isdir(cache) else []:
            for plugin in os.listdir(os.path.join(cache, market)):
                pdir = os.path.join(cache, market, plugin)
                for version in os.listdir(pdir) if os.path.isdir(pdir) else []:
                    installs.append((plugin, os.path.join(pdir, version)))
    for plugin, path in installs:
        add_plugin(plugin, path, skills, agents)
    folders = [path for _, path in installs]
    try:  # the flat layout <dir>/<plugin>: rules only, as in a marketplace's plugins folder
        flat = sorted(n for n in os.listdir(plugins_dir) if n != "cache" and not n.startswith("."))
    except OSError:
        flat = []
    return skills, agents, folders + [os.path.join(plugins_dir, n) for n in flat
                                      if os.path.isdir(os.path.join(plugins_dir, n))]


def add_plugin(plugin, path, skills, agents):
    """One plugin folder's skills, bare and as `plugin:name`, and its agents as `plugin:name`."""
    sdir = os.path.join(path, "skills")
    for name in os.listdir(sdir) if os.path.isdir(sdir) else []:
        if os.path.isfile(os.path.join(sdir, name, "SKILL.md")):
            skills.update({name, f"{plugin}:{name}"})
    adir = os.path.join(path, "agents")
    for name in os.listdir(adir) if os.path.isdir(adir) else []:
        if name.endswith(".md"):
            agents.add(f"{plugin}:{name[:-3]}")


def repo_plugins(repo):
    """{name: folder} of <repo>/plugins/*, the marketplace layout."""
    parent = os.path.join(repo, "plugins")
    names = sorted(os.listdir(parent)) if os.path.isdir(parent) else []
    return {name: os.path.join(parent, name) for name in names if os.path.isdir(os.path.join(parent, name))}


def default_skills_roots(repo):
    """<repo>/plugins/*/skills, plus <repo>/skills when it exists or there is nothing else to report as missing."""
    roots = [os.path.join(path, "skills") for path in repo_plugins(repo).values()
             if os.path.isdir(os.path.join(path, "skills"))]
    own = os.path.join(repo, "skills")
    return roots + [own] if os.path.isdir(own) or not roots else roots


class CommandIndex:
    """Command lookup on a PATH: a listing of each folder first, shutil.which for every miss.

    Git's usr\\bin is added after a folder holding git.exe, as the Bash tool sees it.
    """

    def __init__(self, path):
        dirs = [d for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep) if d]
        extra = []
        for d in dirs:
            if os.path.isfile(os.path.join(d, "git.exe")):
                usr = os.path.join(os.path.dirname(os.path.normpath(d)), "usr", "bin")
                if os.path.isdir(usr):
                    extra.append(usr)
        self.path = os.pathsep.join(dirs + extra)
        self.exts = [e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
        self.names = set()
        for d in dirs + extra:
            try:
                self.names.update(n.lower() for n in os.listdir(d))
            except OSError:
                pass
        self.cache = {}

    def found(self, name):
        if name not in self.cache:
            low = name.lower()
            hit = low in self.names or any(low + e in self.names for e in self.exts)
            self.cache[name] = hit or shutil.which(name, path=self.path) is not None
        return self.cache[name]


def split_commands(text):
    """Split on |, ||, &&, ; outside quotes and braces; a bare `a|b` is one word (alternatives)."""
    parts, cur, quote, depth, i = [], [], None, 0, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "{(":
            depth += 1
        elif ch in "})":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "|;&":
            op = 2 if text[i + 1:i + 2] == ch else 1
            before = text[i - 1:i].isspace() if i else True
            after = text[i + op:i + op + 1].isspace() or i + op >= len(text)
            if (ch != "&" or op == 2) and (before or after):
                parts.append("".join(cur))
                cur = []
                i += op
                continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def scan_quotes(text):
    """(code before any comment, quote still open at its end); a # after whitespace starts a comment."""
    quote, i = None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i], None
        elif ch in "'\"":
            quote = ch
        i += 1
    return text, quote


class Linter:
    def __init__(self, args):
        self.args = args
        self.findings = []
        self.known_skills, self.known_agents, self.installed_roots = plugin_inventory(args.plugins_dir)
        self.known_agents |= BUILTIN_AGENTS
        self.commands = CommandIndex(args.path)
        self.plugin_roots = []
        for repo in args.repo:
            for name, path in repo_plugins(repo).items():
                add_plugin(name, path, self.known_skills, self.known_agents)
                self.plugin_roots.append(path)
        self.file, self.upstream, self.plugin_root, self.heading = None, False, None, ""
        self.keys = {}  # scanned file -> its baseline key
        self.rules, self.rules_read = set(), 0  # realpaths of the plugins' rules files; how many were scanned
        self.plugins_rules = set()  # the part of them found through --plugins-dir

    # ----- inventory -------------------------------------------------------------------------
    def collect(self):
        """Files to scan as (path, upstream); fills the known skill and agent names.

        A missing input, a folder that cannot be listed, or 0 files is a `read` finding.
        """
        files = []
        for p in self.args.claude_md:
            if os.path.isfile(p):
                files.append((p, False, p))
            elif p in self.args.required:
                self.read_error(p, "missing file")
        for root in self.args.skills_root:
            if not os.path.isdir(root):
                self.read_error(root, "missing folder")
                continue
            for path in skill_files(root, self.walk_error(False)):
                files.append((path, False, root))
                self.add_skill(path)
        for plugin, via_plugins_dir in ([(p, False) for p in self.plugin_roots]
                                        + [(p, True) for p in self.installed_roots]):
            folder = os.path.join(plugin, "rules")
            for path in self.rule_files(folder):
                files.append((path, False, folder))
                key = os.path.normcase(os.path.realpath(path))
                self.rules.add(key)
                if via_plugins_dir:
                    self.plugins_rules.add(key)
        for folder in (self.args.user_skills, self.args.agents_dir, self.args.plugins_dir):
            if not os.path.isdir(folder) and folder in self.args.required:  # plugins: the fallback cache lives here
                self.read_error(folder, "missing folder")
        user = self.args.user_skills
        for name in sorted(os.listdir(user)) if os.path.isdir(user) else []:
            entry = os.path.join(user, name)
            if name.startswith(".") or not os.path.isdir(entry):
                continue
            if is_link(entry):
                self.known_skills.add(name)
                continue
            upstream = any(fnmatch.fnmatch(name, p) for p in self.args.upstream)
            for path in skill_files(entry, self.walk_error(upstream)):
                files.append((path, upstream, user))
                self.add_skill(path, upstream)
        adir = self.args.agents_dir
        for name in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
            path = os.path.join(adir, name)
            if name.endswith(".md") and os.path.isfile(path):
                files.append((path, False, adir))
                self.known_agents.add(name[:-3])
                self.known_agents.add(frontmatter_name(path) or name[:-3])
        seen, unique = set(), []
        for path, upstream, source in files:
            key = os.path.normcase(os.path.realpath(path))
            if key not in seen:
                seen.add(key)
                unique.append((path, upstream))
                self.keys[path] = baseline_key(path, source)
        if not unique:
            self.read_error("drift-lint", "0 files scanned")
        self.rules_read = len(self.rules & seen)
        if self.args.plugins_given and not self.plugins_rules:  # rules files of --repo do not count
            self.read_error(self.args.plugins_dir, "0 rules files read")
        return unique

    def rule_files(self, folder):
        """*.md directly in a plugin's rules folder, which a plugin may not have."""
        try:
            names = sorted(os.listdir(folder))
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.walk_error(False)(exc)
            return []
        return [os.path.join(folder, n) for n in names if n.endswith(".md") and os.path.isfile(os.path.join(folder, n))]

    def read_error(self, path, reason, upstream=False):
        self.findings.append((upstream, path, 0, "read", reason))

    def walk_error(self, upstream):
        return lambda exc: self.read_error(exc.filename, f"cannot list folder ({type(exc).__name__})", upstream)

    def add_skill(self, path, upstream=False):
        self.known_skills.add(os.path.basename(os.path.dirname(path)))
        self.known_skills.add(frontmatter_name(path, upstream) or os.path.basename(os.path.dirname(path)))

    def hit(self, lineno, check, ref):
        self.findings.append((self.upstream, self.file, lineno, check, ref))

    # ----- scanning --------------------------------------------------------------------------
    def holding_plugin(self, path):
        """The repo plugin folder that holds path, or None."""
        real = os.path.normcase(os.path.abspath(path))
        for root in self.plugin_roots + self.installed_roots:
            if real.startswith(os.path.normcase(os.path.abspath(root)) + os.sep):
                return root
        return None

    def scan(self, path, upstream):
        self.file, self.upstream, self.plugin_root = path, upstream, self.holding_plugin(path)
        try:
            lines = read_lines(path, upstream)
        except (OSError, UnicodeDecodeError) as exc:
            self.hit(0, "read", str(exc))
            return
        base = os.path.dirname(path)
        fence, lang, state, table, para = None, "", None, None, []
        self.heading = ""
        for lineno, line in enumerate(lines, 1):
            m = FENCE.match(line)
            if m:
                self.paragraph(para)
                para, table = [], None
                if fence is None:
                    fence, lang, state = m.group(1)[0], m.group(2).lower(), None
                elif m.group(1)[0] == fence and not m.group(2):
                    fence, lang = None, ""
                continue
            if fence is None and HEADING.match(line):
                self.heading = line
            self.check_paths(line, base, lineno)
            if fence is not None:
                self.check_names(line, [(0, lineno)])
                if lang in SHELL_LANGS:
                    state = self.block_line(line, lineno, state, track=bool(lang))
                continue
            if line.lstrip().startswith("|"):
                self.paragraph(para)
                para = []
                table = self.check_table(line, lines, lineno, table)
                self.paragraph([(lineno, line)])
                continue
            table = None
            if not line.strip():
                self.paragraph(para)
                para = []
            else:
                para.append((lineno, line))
        self.paragraph(para)

    def paragraph(self, para):
        """Names and inline code spans of consecutive prose lines, so a span may cross a line break."""
        if not para:
            return
        starts, text = [], ""
        for lineno, line in para:
            starts.append((len(text), lineno))
            text += line + "\n"
        self.check_names(text, starts)
        offsets = [s for s, _ in starts]
        for span in CODE_SPAN.finditer(text):
            raw = span.group(2)
            inner = raw.replace("\n", " ").strip()
            # not a command: the prose between two spans that a stray backtick paired wrongly (it starts or
            # ends in whitespace), or a phrase of plain words (an error message)
            if raw != raw.strip() or PLAIN_WORDS.match(inner):
                continue
            if len(inner.split()) >= 2:
                lineno = starts[bisect.bisect_right(offsets, span.start(2)) - 1][1]
                self.check_command_text(inner, lineno)

    def block_line(self, line, lineno, state, track):
        """Check one line of a shell block; skip heredoc bodies and multi-line quoted strings."""
        if state:
            kind, tag = state
            if kind == "heredoc":
                return None if line.strip() == tag else state
            if kind == "herestring":
                return None if line.lstrip().startswith(tag + "@") else state
            idx = line.find(tag)
            return state if idx < 0 else self.after_line(line[idx + 1:], track)
        self.check_command_text(line, lineno)
        return self.after_line(line, track)

    @staticmethod
    def after_line(line, track):
        if not track:
            return None
        m = HEREDOC.search(line)
        if m:
            return ("heredoc", m.group(2))
        tail = line.rstrip()
        if tail.endswith(("@'", '@"')):
            return ("herestring", tail[-1])
        quote = scan_quotes(line)[1]
        return ("quote", quote) if quote else None

    def check_table(self, line, lines, lineno, table):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if table is None:
            following = lines[lineno] if lineno < len(lines) else ""
            if not TABLE_RULE.match(following):
                return None
            heads = [c.strip("`* ") for c in cells]
            return ([i for i, h in enumerate(heads) if SKILL_HEADER.match(h)],
                    [i for i, h in enumerate(heads) if AGENT_HEADER.match(h)])
        if TABLE_RULE.match(line):
            return table
        for cols, check in ((table[0], "skill"), (table[1], "agent")):
            for i in cols:
                if i < len(cells):
                    names = re.findall(r"`([A-Za-z0-9][\w:-]*)`", cells[i])
                    if not names and re.fullmatch(r"[A-Za-z0-9][\w:-]*", cells[i]):
                        names = [cells[i]]
                    for name in names:
                        self.check_name(check, name, lineno)
        return table

    def check_names(self, text, starts):
        offsets = [s for s, _ in starts]

        def line_of(pos):
            return starts[bisect.bisect_right(offsets, pos) - 1][1]

        for m in SKILL_COLON.finditer(text):
            self.check_name("skill", m.group(1).rstrip(".:"), line_of(m.start()))
        for m in SKILL_PHRASE.finditer(text):
            name = m.group(1) or m.group(2)
            self.check_name("skill", name, line_of(m.start(1) if m.group(1) else m.start(2)))
        for m in AGENT_PHRASE.finditer(text):
            g = 1 if m.group(1) else 2 if m.group(2) else 3
            self.check_name("agent", m.group(g), line_of(m.start(g)))

    def check_name(self, check, name, lineno):
        known = self.known_skills if check == "skill" else self.known_agents
        if name not in known:
            self.hit(lineno, check, name)

    # ----- paths -----------------------------------------------------------------------------
    def check_paths(self, line, base, lineno):
        text = line.replace("\\\\", "\\")
        for m in DRIVE_PATH.finditer(text):
            ref = self.resolve(m, text, [""])
            if ref:
                self.hit(lineno, "path", ref)
        # skip class "remote": ~ and $HOME are a server's home on a line that mentions ssh, remote or
        # "on/to/from the server", or in a section whose heading mentions remote or server
        if not (SSH.search(text) or REMOTE_LINE.search(text) or REMOTE.search(self.heading)):
            for m in HOME_PATH.finditer(text):
                if SHELL_DEFAULT.search(text, 0, m.start()):  # skip class "shell default": ${VAR:-~/...}
                    continue
                ref = self.resolve(m, text, [self.args.home], strip=len(m.group(1)) + 1)
                if ref:
                    self.hit(lineno, "path", ref)
        roots = list(self.args.repo) + [base] + ([self.plugin_root] if self.plugin_root else [])
        for m in REPO_PATH.finditer(text):
            ref = self.resolve(m, text, roots)
            if ref:
                self.hit(lineno, "repo-path", ref)

    @staticmethod
    def resolve(m, text, roots, strip=0):
        """None when the reference exists under one of roots or is skipped, else the reference."""
        run = m.group(0)
        nxt = text[m.end():m.end() + 1]

        def under_roots(ref):
            return [os.path.join(root, ref[strip:]) if root else ref[strip:] for root in roots]

        def folder_of(ref):
            return ref[:max(ref.rfind("\\"), ref.rfind("/")) + 1]

        words = run.split(" ")[:8]
        first = words[0].rstrip(TRAILING)
        # between a pair of quotes or backticks the run is one reference, spaces included
        opener = text[m.start() - 1:m.start()]
        quoted = bool(opener) and opener in "`'\"" and nxt == opener
        if nxt and nxt in "*?" and not run[-1:].isspace():
            # a glob: some path must match it up to the end of its segment
            tail = GLOB_TAIL.match(text, m.end()).group(0).rstrip(TRAILING.replace("*", ""))
            if any(glob.glob(glob.escape(base) + tail) for base in under_roots(run)):
                return None
            return run + tail
        mark = ELLIPSIS.search(run if quoted else words[0])
        if mark:
            # the text before an ellipsis starts a name in its folder; an ellipsis ending a segment elides the
            # parent of what follows, so only the folder before it is known. Otherwise it may follow arguments.
            head = run[:mark.start()].rstrip()
            start = folder_of(head) if run[mark.end():mark.end() + 1] in ("\\", "/") else head
            if any(os.path.exists(p) or glob.glob(glob.escape(p) + "*") for p in under_roots(start)):
                return None
            run = head
        elif nxt == "<" and not run[-1:].isspace():
            # a placeholder: the folder before it must exist
            prefix = folder_of(run)
            return None if any(os.path.exists(p) for p in under_roots(prefix)) else prefix
        if len(first) <= 3:
            return None
        for k in range(1, len(words) + 1):
            candidate = " ".join(words[:k]).rstrip(TRAILING)[strip:]
            if any(os.path.exists(os.path.join(root, candidate) if root else candidate) for root in roots):
                return None
        return run.rstrip(TRAILING) if quoted else first

    # ----- commands --------------------------------------------------------------------------
    def check_command_text(self, text, lineno):
        if LIST_ITEM.match(text):
            return  # a markdown list item, not a command line
        text = scan_quotes(PROMPT.sub("", text))[0]
        for segment in split_commands(text):
            tokens = segment.split()
            if not tokens or not COMMAND_TOKEN.match(tokens[0]):
                continue
            name = tokens[0]
            if not self.skipped(name, tokens) and not self.commands.found(name):
                self.hit(lineno, "command", name)

    @staticmethod
    def skipped(name, tokens):
        low = name.lower()
        if low in SHELL_WORDS or low in CMDLETS:
            return True
        if VERB_NOUN.match(name):
            return False  # a cmdlet the allowlist does not know is reported
        if name[0].isupper():
            return True  # skip class "capitalised word": prose, error text, SQL, config directive
        if "_" in name or low in CODE_KEYWORDS or (len(tokens) > 1 and tokens[1] in ASSIGNMENTS):
            return True  # skip class "code statement"
        if FILE_NAME.search(name):
            return True  # skip class "file name": a script named, not a command on PATH
        return low in REMOTE_COMMANDS or low in PROJECT_BINS


def parse_args(argv):
    home = os.path.expanduser("~")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude-md", action="append", help="rule file to scan (repeatable)")
    ap.add_argument("--skills-root", action="append", help="folder whose SKILL.md files are scanned (repeatable)")
    ap.add_argument("--user-skills", help="~/.claude/skills: non-link folders scanned, every entry a known skill")
    ap.add_argument("--upstream", action="append", help="user-skills folder pattern reported separately (repeatable)")
    ap.add_argument("--agents-dir", help="folder of agent definitions (*.md)")
    ap.add_argument("--plugins-dir", help="~/.claude/plugins: installed plugins' skills and agents")
    ap.add_argument("--repo", action="append", help="repo root for tools/... and templates/... (repeatable)")
    ap.add_argument("--home", default=home, help="what ~ expands to")
    ap.add_argument("--path", help="PATH used to find commands (default: the process PATH)")
    ap.add_argument("--baseline", help="accepted findings, one `file<TAB>check<TAB>reference` per line "
                                       "(default: none)")
    args = ap.parse_args(argv)
    args.repo = args.repo or [p for p in os.environ.get(REPOS_ENV, "").split(os.pathsep) if p]
    if not args.repo:
        sys.stderr.write(f"drift-lint: no repos; pass --repo or set {REPOS_ENV}\n")
        raise SystemExit(2)
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(args.home, ".claude")
    # A plugin-only config has no CLAUDE.md, skills or agents: only an input named on the command line must exist.
    args.required = {p for p in (args.claude_md or []) + [args.user_skills, args.agents_dir, args.plugins_dir] if p}
    args.claude_md = args.claude_md or [os.path.join(claude, "CLAUDE.md")]
    args.skills_root = args.skills_root or [root for r in args.repo for root in default_skills_roots(r)]
    args.user_skills = args.user_skills or os.path.join(claude, "skills")
    args.upstream = args.upstream or DEFAULT_UPSTREAM
    args.agents_dir = args.agents_dir or os.path.join(claude, "agents")
    args.plugins_given = bool(args.plugins_dir)
    args.plugins_dir = args.plugins_dir or os.path.join(claude, "plugins")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    linter = Linter(args)
    files = linter.collect()
    for path, upstream in files:
        linter.scan(path, upstream)
    findings, baselined = linter.findings, 0
    if args.baseline:
        try:
            baseline = load_baseline(args.baseline)
        except (OSError, UnicodeDecodeError) as exc:
            baseline = {}
            reason = "missing baseline" if isinstance(exc, OSError) else "baseline is not valid UTF-8"
            findings.append((False, args.baseline, 0, "read", reason))
        used, kept = set(), []
        for f in findings:
            entry = f"{linter.keys.get(f[1], f[1])}\t{f[3]}\t{f[4]}"
            if entry in baseline:
                used.add(entry)
                baselined += 1
            else:
                kept.append(f)
        findings = kept + [(False, args.baseline, n, "stale-baseline", entry.replace("\t", "  "))
                           for entry, n in baseline.items() if entry not in used]
    main_hits = sorted((f for f in findings if not f[0]), key=lambda f: (f[1], f[2]))
    upstream_hits = sorted((f for f in findings if f[0]), key=lambda f: (f[1], f[2]))
    for _, path, lineno, check, ref in main_hits:
        print(f"{path}:{lineno}  {check}  {ref}")
    if upstream_hits:
        print("## upstream copies")
        for _, path, lineno, check, ref in upstream_hits:
            print(f"{path}:{lineno}  {check}  {ref}")
    print(f"# drift-lint: {len(files)} files, {len(main_hits)} findings, {baselined} baselined, "
          f"{len(upstream_hits)} in upstream copies, {linter.rules_read} rules files")
    return 1 if main_hits else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
