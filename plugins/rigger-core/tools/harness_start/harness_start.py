"""SessionStart hook of rigger-core (matcher startup): warns about hook handlers that run twice.

Handlers come from the user settings (settings.json, settings.local.json in $CLAUDE_CONFIG_DIR,
else ~/.claude) and from the hooks/hooks.json of every installed plugin that the user settings
enable (plugins/installed_plugins.json, user scope, its installPath). Two handlers are duplicates
when they share the event, the matcher, the `if` value, the script basename and the script's
arguments; the interpreter, its flags, the py.sh launcher and the paths are not compared, so
`python -I -S ~/hooks/x/x.py a` in settings.json and
`sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/x/x.py" a` in a plugin match.
Redirects (`2>/dev/null`) are not arguments, and in `cd x && python x.py` the script is found after
the `&&` or `;`. A handler without a script is compared by its whole command.

It also warns about a `## ` heading of CLAUDE.md that a `rules/*.md` file of the same enabled
plugins has too: the plugin injects that section, so CLAUDE.md should not repeat it. Headings are
compared the way rules_start.sh compares them for HARNESS_RULES_DROP: a line that starts with
`## `, trimmed of spaces, tabs and CR, matched exactly and case-sensitively. Lines inside fenced
code blocks of CLAUDE.md are not headings. This check is silent when a file is missing or unreadable.

Prints one line per duplicate, per overlapping heading and per settings or hooks file it cannot
read, nothing when all is clean. Always exits 0.
"""

import json
import os
import re
import shlex
import sys
from collections import namedtuple
from pathlib import Path

PREFIX = "rigger: "
SETTINGS_FILES = ("settings.json", "settings.local.json")
INSTALLED = Path("plugins") / "installed_plugins.json"
SCRIPT_SUFFIXES = (".py", ".sh", ".ps1", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php")
LAUNCHERS = ("py.sh",)
LIST_OPERATORS = ("&&", ";")
# Matchers Claude Code treats as "every event of this kind".
MATCH_ALL = ("", "*")
CLAUDE_MD = "CLAUDE.md"
HEADING_PREFIX = "## "
BLANK = " \t\r"  # what rules_start.sh trims from a heading line
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

Handler = namedtuple("Handler", "source event matcher condition identity")


def config_root(environ):
    value = environ.get("CLAUDE_CONFIG_DIR", "")
    return Path(value) if value else Path.home() / ".claude"


def words(handler):
    """The argv the handler runs: `command` plus `args`, or `command` split the way sh splits it."""
    command = handler["command"]
    args = handler.get("args")
    if isinstance(args, list):
        return [command] + [str(arg) for arg in args]
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    found = []
    try:
        for word in lexer:
            # `2>` and `1>&2` split the fd off; `5 > out` keeps its 5: the lexer has read one character past
            # the word, which is the redirect only when nothing separates them.
            end = lexer.instream.tell()
            if word.isdigit() and command[end - 1] in "<>" and command[end - 1 - len(word):end - 1] == word:
                continue
            found.append(word)
    except ValueError:
        return None
    return found


def is_operator(word):
    return bool(word) and all(char in "();<>|&" for char in word)


def is_redirect(word):
    return is_operator(word) and ("<" in word or ">" in word)


def simple_commands(argv):
    """The words of each command of a `&&` or `;` list, without redirects; `|`, `||` or `&` ends the list."""
    commands, current, target = [], [], False
    for word in argv:
        if target:
            target = False
        elif is_redirect(word):
            target = True
        elif is_operator(word):
            commands.append(current)
            current = []
            if word not in LIST_OPERATORS:
                return commands
        else:
            current.append(word)
    return commands + [current]


def basename(word):
    return re.split(r"[\\/]", word)[-1]


def identity(handler):
    """("script", basename, args) for a handler that runs a script, else ("command", text)."""
    argv = words(handler)
    if argv is None:
        return ("command", handler["command"].strip())
    for command in simple_commands(argv):
        for index, word in enumerate(command):
            name = basename(word).lower()
            if name.endswith(SCRIPT_SUFFIXES) and name not in LAUNCHERS:
                return ("script", name, tuple(command[index + 1:]))
    return ("command", " ".join(argv))


def handlers(hooks, source):
    """Every command handler of a `hooks` object, labelled with `source`."""
    found = []
    if not isinstance(hooks, dict):
        return found
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            matcher = "" if matcher is None or matcher in MATCH_ALL else str(matcher)
            entries = group.get("hooks")
            for handler in entries if isinstance(entries, list) else []:
                if not isinstance(handler, dict) or handler.get("type", "command") != "command":
                    continue
                if not isinstance(handler.get("command"), str):
                    continue
                condition = handler.get("if") or ""
                found.append(Handler(source, str(event), matcher, str(condition), identity(handler)))
    return found


def describe(handler):
    parts = [handler.event]
    if handler.matcher:
        parts.append("matcher %s" % handler.matcher)
    if handler.condition:
        parts.append("if %s" % handler.condition)
    if handler.identity[0] == "script":
        parts.append(" ".join((handler.identity[1],) + handler.identity[2]))
    else:
        parts.append("`%s`" % handler.identity[1])
    return ", ".join(parts)


def duplicates(found):
    """[(first handler, [sources])] for every handler key seen more than once, in input order."""
    groups = {}
    for handler in found:
        key = (handler.event, handler.matcher, handler.condition, handler.identity)
        groups.setdefault(key, []).append(handler)
    return [(group[0], [h.source for h in group]) for group in groups.values() if len(group) > 1]


def read_json(path, problems):
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        problems.append("%scannot read %s (%s: %s); duplicate-hook check incomplete"
                        % (PREFIX, path, type(exc).__name__, exc))
        return None


def collect(root, problems):
    """(handlers, [(plugin key, installPath)]) from the user settings and the enabled installed plugins."""
    found = []
    installs = []
    enabled = {}
    for name in SETTINGS_FILES:
        path = root / name
        settings = read_json(path, problems)
        if not isinstance(settings, dict):
            continue
        found.extend(handlers(settings.get("hooks"), str(path)))
        flags = settings.get("enabledPlugins")
        if isinstance(flags, dict):
            enabled.update(flags)
    installed = read_json(root / INSTALLED, problems)
    plugins = installed.get("plugins") if isinstance(installed, dict) else None
    for key, entries in (plugins.items() if isinstance(plugins, dict) else ()):
        if enabled.get(key) is not True or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("scope", "user") != "user":
                continue
            install_path = entry.get("installPath")
            if not isinstance(install_path, str) or not install_path:
                continue
            installs.append((key, install_path))
            data = read_json(Path(install_path) / "hooks" / "hooks.json", problems)
            if isinstance(data, dict):
                found.extend(handlers(data.get("hooks"), "plugin " + key))
    return found, installs


def duplicate_warnings(found):
    lines = []
    for handler, sources in duplicates(found):
        settings = [source for source in sources if not source.startswith("plugin ")]
        advice = ("remove the entry from %s" % " and ".join(settings)) if settings else "disable one of the plugins"
        lines.append("%shook runs %d times (%s): from %s; %s"
                     % (PREFIX, len(sources), describe(handler), ", ".join(sources), advice))
    return lines


def read_text(path, encoding):
    """The text of a file with its line endings untouched, or None: the heading check stays silent."""
    try:
        with open(path, encoding=encoding, newline="") as handle:
            return handle.read()
    except (OSError, ValueError):  # ValueError covers UnicodeDecodeError and a NUL in the path
        return None


def headings(text, skip_fences):
    """The `## ` heading lines of `text`, trimmed, in order; with skip_fences, not those in fenced code."""
    found = []
    fence = None
    for line in text.split("\n"):
        if skip_fences:
            match = FENCE.match(line)
            if fence:
                if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                        and not match.group(2).strip():
                    fence = None
                continue
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = match.group(1)
                continue
        if line.startswith(HEADING_PREFIX):
            found.append(line.strip(BLANK))
    return found


def rules_files(rules_dir):
    """Names of the `*.md` entries directly in rules_dir; read_text skips one that is not a readable file."""
    try:
        with os.scandir(rules_dir) as entries:
            return [entry.name for entry in entries if entry.name.endswith(".md")]
    except (OSError, ValueError):
        return []


def overlap_warnings(root, installs):
    """One line per `## ` heading of CLAUDE.md that a rules file of an installed plugin also has."""
    text = read_text(root / CLAUDE_MD, "utf-8-sig")
    wanted = list(dict.fromkeys(headings(text, True))) if text is not None else []
    sources = {}
    for key, install_path in installs:
        rules_dir = Path(install_path) / "rules"
        for name in rules_files(rules_dir):
            content = read_text(rules_dir / name, "utf-8")
            if content is None:
                continue
            source = "%s/rules/%s" % (key.split("@")[0], name)
            for heading in headings(content, False):
                if heading in wanted and (heading not in sources or source < sources[heading]):
                    sources[heading] = source
    return ["%sCLAUDE.md section \"%s\" is also injected by %s; delete that section from %s because the plugin injects it"
            % (PREFIX, heading, sources[heading], root / CLAUDE_MD) for heading in wanted if heading in sources]


def build_output(environ):
    root = config_root(environ)
    problems = []
    found, installs = collect(root, problems)
    lines = problems + duplicate_warnings(found) + overlap_warnings(root, installs)
    return "".join(line + "\n" for line in lines)


def main():
    try:
        text = build_output(os.environ)
    except Exception as exc:  # a SessionStart hook must never block the session
        text = "%sharness_start failed (%s: %s)\n" % (PREFIX, type(exc).__name__, exc)
    sys.stdout.buffer.write(text.encode("utf-8", "backslashreplace"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
