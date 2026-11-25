"""Latency of every hook handler in plugins/*/hooks/hooks.json, on a matching and a non-matching call.

Usage: python -I -X utf8 scripts/hook_latency.py [--runs N] [--json FILE]   (default 20 runs)

Each handler's command starts the way Claude Code starts a hook (spike Task 17 (c)): on Windows
`<Git>/bin/bash.exe -c <command>`, on Linux and macOS `/bin/sh -c <command>`, with the command text as
written, so the shell expands ${CLAUDE_PLUGIN_ROOT} (forward slashes on Windows). On Windows the Git
install is found from the `git` on PATH (an absolute entry outside this repository and the working
directory): the first of its folder and the two above it that holds `bin/bash.exe` and
`usr/bin/bash.exe`. How Claude Code itself finds that file was not observed.

Every plugin runs from a temp copy laid out like the plugin cache, with a config dir that lists the
plugins as installed and enabled, a scratch CLAUDE_PLUGIN_DATA per plugin, a fake HOME and a temp git
repository as the project. CASES gives each (plugin, script) its calls: `match` takes the hook's working
path, `non-match` is a call Claude Code starts but the hook has nothing to do for; a `-large` case has a
grown input (a transcript, an inbox, a diff or a command). A tool handler without an `if` needs a
non-match case; for a handler with an `if` the non-matching call is `not started`, since Claude Code
filters it before starting a process. One unmeasured run per case comes first, and its output must show
the case took the path it names; every run gets fresh session, agent and tool-use ids unless the case
pins its session. `exit 0` through the same launcher is the floor, timed at the start and at the end.

Prints:
  # launcher <argv0> ($0 <value>, bash <version or ->, /bin/sh <real path>)
  # floor start median <ms> p95 <ms>
  # hook <plugin> <event>:<matcher> <handler> <case> median <ms> p95 <ms> floor <ms>[ async]
  # hook <plugin> <event>:<matcher> <handler> non-match not started
  # floor end median <ms> p95 <ms>
  # python <interpreter py.sh chose>
  # candidate for a Rust port: <plugin> <event>:<matcher> <handler> median <ms>
  # hooks measured <n> of <total>
<handler> is the script's file name, then `:<arguments joined by ,>` and `@<if>` (spaces as %20) when
the handler has them. A candidate is a handler on the per-command path (PreToolUse and PostToolUse on
Bash, PowerShell, Write or Edit, and UserPromptSubmit) with a case median over 50 ms. A handler without a
case, a case whose proof fails, or n below total prints `# hook latency failed: <reason>` and exits 1.
"""

import argparse
import datetime
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True

MARKET = "rigger"
TIMEOUT = 60
CANDIDATE_MS = 50
PER_COMMAND_TOOLS = {"Bash", "PowerShell", "Write", "Edit"}
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
STRIPPED = ("CLAUDE_", "HARNESS_", "RIGGER_", "ANTHROPIC_", "GIT_")
COPY_IGNORE = ("__pycache__", "*.pyc", "tests", "evals", "node_modules")
ROOT_PATH = re.compile(r'\$\{CLAUDE_PLUGIN_ROOT\}/([^"\s]+)"?')
# Pinned sessions: a state file lives under these ids. PLAN_SESSION's state has an active plan.
SESSION = "0a1b2c3d-0000-4000-8000-000000000001"
PLAN_SESSION = "0a1b2c3d-0000-4000-8000-000000000002"
SIDE = "local_0aa11111-2222-4333-8444-555566667777"
PROMPT_ID = "prompt-latency-1"
LARGE_TRANSCRIPT = 4 << 20
LARGE_INBOX_ENTRIES = 3000
LARGE_DIFF_FILES = 300
LARGE_MESSAGE = 200 * 1024
LARGE_WORKTREE_FILES = 3000


class Handler(NamedTuple):
    plugin: str
    event: str
    matcher: Optional[str]
    condition: Optional[str]
    command: str
    script: str
    args: Tuple[str, ...]
    is_async: bool

    @property
    def where(self) -> str:
        return "%s:%s" % (self.event, self.matcher or "*")

    @property
    def label(self) -> str:
        text = self.script + (":" + ",".join(self.args) if self.args else "")
        return text + ("@" + self.condition.replace(" ", "%20") if self.condition else "")

    @property
    def name(self) -> str:
        return "%s %s %s" % (self.plugin, self.where, self.label)


class Variant(NamedTuple):
    case: str
    stdin: Callable[[], bytes]
    check: Callable[[bytes], Optional[str]]
    env: Dict[str, str] = {}
    prepare: Optional[Callable[[], None]] = None


Builder = Callable[["Context", Handler], List[Variant]]


def expect(marker: bytes) -> Callable[[bytes], Optional[str]]:
    def check(out: bytes) -> Optional[str]:
        return None if marker in out else "stdout lacks %r: %r" % (marker, out[:200])
    return check


def silent(out: bytes) -> Optional[str]:
    return None if not out.strip() else "stdout not empty: %r" % out[:200]


def nearest_rank(values: List[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(pct / 100.0 * len(ordered))) - 1]


# --- the launcher -----------------------------------------------------------------------------------------

_LAUNCHER = []  # type: List[str]


def _review_scope():
    folder = str(ROOT / "plugins" / "rigger-core" / "tools" / "review-scope")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import review_scope
    return review_scope


def find_git() -> str:
    """git from an absolute PATH entry outside this repository and the working directory."""
    return _review_scope().find_git(ROOT)


def git_bash() -> str:
    scope = _review_scope()
    try:
        git = Path(scope.find_program("git", ROOT, (".exe",)))
    except scope.ScopeError as error:
        raise RuntimeError("no Git Bash: %s" % error)
    for folder in (git.parent, git.parent.parent, git.parent.parent.parent):
        if (folder / "bin" / "bash.exe").is_file() and (folder / "usr" / "bin" / "bash.exe").is_file():
            return str(folder / "bin" / "bash.exe")
    raise RuntimeError("no Git Bash: no bin\\bash.exe with usr\\bin\\bash.exe in the folder of %s or the two above it"
                       % git)


def launcher() -> str:
    if not _LAUNCHER:
        _LAUNCHER.append(git_bash() if os.name == "nt" else "/bin/sh")
    return _LAUNCHER[0]


def launcher_argv(command: str) -> List[str]:
    return [launcher(), "-c", command]


def spawn(command: str, stdin: bytes, env: dict, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(launcher_argv(command), input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, cwd=cwd, timeout=TIMEOUT)


def measure(command: str, stdin: bytes, env: dict, cwd: str, runs: int) -> List[float]:
    """Milliseconds per run, from the start of the launcher to the end of the last process it waits for."""
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        done = spawn(command, stdin, env, cwd)
        times.append((time.perf_counter() - started) * 1000.0)
        if done.returncode != 0:
            raise RuntimeError("%r exited %d: %r" % (command, done.returncode, done.stderr[:300]))
    return times


def launcher_identity(env: dict, cwd: str) -> str:
    done = spawn('printf "%s|%s" "$0" "${BASH_VERSION-}"', b"", env, cwd)
    zero, _, version = done.stdout.decode("utf-8", "replace").partition("|")
    sh = os.path.realpath("/bin/sh") if os.name != "nt" else "-"
    if sys.platform == "darwin" and os.path.exists("/private/var/select/sh"):
        sh += " (selected %s)" % os.path.realpath("/private/var/select/sh")
    return "# launcher %s ($0 %s, bash %s, /bin/sh %s)" % (launcher(), zero, version or "-", sh)


# --- the handlers -----------------------------------------------------------------------------------------

def parse(plugin: str, event: str, matcher: Optional[str], hook: dict) -> Handler:
    command = hook.get("command")
    if hook.get("type") != "command" or not isinstance(command, str):
        raise RuntimeError("%s %s: not a command handler: %r" % (plugin, event, hook))
    paths = list(ROOT_PATH.finditer(command))
    if paths:
        script = paths[-1].group(1).rsplit("/", 1)[-1]
        rest = command[paths[-1].end():].split("||")[0]
        args = tuple(rest.split())
    else:
        script, args = command.split()[0], ()
    return Handler(plugin, event, matcher, hook.get("if"), command, script, args, bool(hook.get("async")))


def handlers(plugins: Path) -> List[Handler]:
    found = []
    for path in sorted(plugins.glob("*/hooks/hooks.json")):
        events = json.loads(path.read_bytes().decode("utf-8")).get("hooks", {})   # rigger-panel's file has none
        for event, groups in events.items():
            for group in groups:
                for hook in group.get("hooks", []):
                    found.append(parse(path.parent.parent.name, event, group.get("matcher"), hook))
    return found


def on_command_path(handler: Handler) -> bool:
    if handler.event == "UserPromptSubmit":
        return True
    return handler.event in TOOL_EVENTS and bool(set((handler.matcher or "").split("|")) & PER_COMMAND_TOOLS)


def tool_of(handler: Handler) -> str:
    if handler.condition and "(" in handler.condition:
        return handler.condition.split("(", 1)[0]
    return (handler.matcher or "").split("|")[0]


# --- the scratch install ----------------------------------------------------------------------------------

def _now(minutes_ago: float = 0.0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _empty(folder: Path) -> Callable[[], None]:
    def prepare() -> None:
        shutil.rmtree(str(folder), True)
    return prepare


def _rows_written(folder: Path) -> Callable[[bytes], Optional[str]]:
    def check(out: bytes) -> Optional[str]:
        rows = [json.loads(line) for path in (folder.glob("*.json") if folder.is_dir() else [])
                for line in path.read_bytes().decode("utf-8").splitlines() if line.strip()]
        if out.strip():
            return "stdout not empty: %r" % out[:200]
        if not rows:
            return "no row written in %s" % folder
        failed = [row for row in rows if "error" in (row.get("verdict"), row.get("contract"))]
        return "the hook wrote an error row: %r" % failed[0] if failed else None
    return check


def _no_rows(folder: Path) -> Callable[[bytes], Optional[str]]:
    def check(out: bytes) -> Optional[str]:
        if out.strip():
            return "stdout not empty: %r" % out[:200]
        return "a row was written in %s" % folder if folder.is_dir() and any(folder.iterdir()) else None
    return check


class Context:
    """The temp install, the project repository and the inputs the cases share, built on first use."""

    def __init__(self, tmp: Path, plugins: Path, names: List[str]):
        self.tmp = tmp
        self.git_global = _write(tmp / "gitconfig", b"")
        self.home = tmp / "home"
        self.home.mkdir()
        self.project = self.repo("project")
        self.config = tmp / "config"
        # Personal rules near their 9,000-byte cap: with the plugins' rules they fill session parts 1-5.
        _write(self.config / "rigger" / "rules.d" / "personal.md",
               "## Personal rules\n\n" + "- Keep this personal rule in mind while working.\n" * 160)
        self.roots = {}  # type: Dict[str, Path]
        for name in names:
            self.roots[name] = tmp / "cache" / MARKET / name
            shutil.copytree(str(plugins / name), str(self.roots[name]), ignore=shutil.ignore_patterns(*COPY_IGNORE))
        installed = {"%s@%s" % (name, MARKET): [{"scope": "user", "installPath": str(root), "version": "latency"}]
                     for name, root in self.roots.items()}
        _write(self.config / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": installed}))
        self.settings({})
        self._built = {}  # type: Dict[str, object]

    def settings(self, extra: dict) -> None:
        data = {"enabledPlugins": {"%s@%s" % (name, MARKET): True for name in self.roots}}
        data.update(extra)
        _write(self.config / "settings.json", json.dumps(data))

    def git_env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.git_global), GIT_AUTHOR_NAME="latency",
                   GIT_AUTHOR_EMAIL="latency", GIT_COMMITTER_NAME="latency", GIT_COMMITTER_EMAIL="latency")
        return env

    def git(self, repo: Path, *args: str) -> None:
        subprocess.run([find_git(), "-C", str(repo)] + list(args), env=self.git_env(), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=TIMEOUT)

    def repo(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir()
        self.git(path, "init", "-q")
        return path

    def base_env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIPPED)}
        # HOME alone: a Store Python derives its own path from USERPROFILE.
        env.update(HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.config), CLAUDE_PROJECT_DIR=str(self.project),
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.git_global),
                   HARNESS_LESSONS_INBOX=str(self.inbox("small")), HARNESS_LESSONS_BATCH_OFF="1")
        return env

    def env(self, handler: Handler, extra: Dict[str, str]) -> dict:
        env = self.base_env()
        root = self.roots[handler.plugin]
        env["CLAUDE_PLUGIN_ROOT"] = root.as_posix() if os.name == "nt" else str(root)
        env["CLAUDE_PLUGIN_DATA"] = str(self.data(handler.plugin))
        env.update(extra)
        return env

    def data(self, plugin: str) -> Path:
        return self.tmp / "data" / plugin

    def once(self, key: str, build: Callable[[], object]):
        if key not in self._built:
            self._built[key] = build()
        return self._built[key]

    def stdin(self, handler: Handler, fields: dict, session: Optional[str] = None) -> Callable[[], bytes]:
        """Hook stdin as Claude Code writes it; ids are fresh per call unless the session is pinned."""
        def make() -> bytes:
            data = {"session_id": session or str(uuid.uuid4()), "transcript_path": str(self.transcript("small")),
                    "cwd": str(self.project), "permission_mode": "default", "hook_event_name": handler.event}
            if handler.event == "SessionStart":
                data["source"] = (handler.matcher or "startup").split("|")[0]
            elif handler.event in ("SubagentStart", "SubagentStop"):
                data.update(agent_id=uuid.uuid4().hex[:17], agent_type="general-purpose")
            elif handler.event in TOOL_EVENTS:
                data.update(tool_name=tool_of(handler), tool_input={}, tool_use_id="toolu_" + uuid.uuid4().hex[:24])
                if handler.event == "PostToolUse":
                    data["tool_response"] = {}
            elif handler.event in ("PreCompact", "PostCompact"):
                data["trigger"] = handler.matcher if handler.matcher in ("auto", "manual") else "auto"
            elif handler.event == "Stop":
                data.update(stop_hook_active=False, last_assistant_message="")
            elif handler.event == "UserPromptSubmit":
                data["prompt"] = "go on"
            data.update(fields)
            return json.dumps(data, separators=(",", ":")).encode("utf-8")
        return make

    # Inputs -----------------------------------------------------------------------------------------------

    def transcript(self, kind: str) -> Path:
        """`small` and `large` (LARGE_TRANSCRIPT of tool calls inside the last turn): a turn that edits x.py in
        the project, an open side session, and a last request at 300k tokens; `low`: the same turn at 1k tokens
        with no side session."""
        return self.once("transcript-" + kind, lambda: self._transcript(kind))

    def _transcript(self, kind: str) -> Path:
        cwd = str(self.project)
        common = {"isSidechain": False, "userType": "external", "entrypoint": "cli", "cwd": cwd,
                  "sessionId": SESSION, "version": "2.1.330", "gitBranch": "main", "parentUuid": "p", "uuid": "u"}

        def record(kind_, minutes_ago, **extra):
            entry = dict(common, type=kind_, timestamp=_now(minutes_ago))
            entry.update(extra)
            return json.dumps(entry, separators=(",", ":"), ensure_ascii=False)

        def call(index, name, tool_input, output, minutes_ago):
            use = "toolu_latency_%d" % index
            return [record("assistant", minutes_ago, requestId="r", message={
                        "model": "m", "id": "msg", "type": "message", "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": use, "name": name, "input": tool_input}]}),
                    record("user", minutes_ago, promptId=PROMPT_ID, sourceToolAssistantUUID="a",
                           toolUseResult={"stdout": output, "stderr": "", "interrupted": False},
                           message={"role": "user", "content": [{"tool_use_id": use, "type": "tool_result",
                                                                 "content": output, "is_error": False}]})]

        lines = []
        if kind != "low":   # before the prompt: the note opens a turn of its own for the turn readers
            note = ('<system-reminder>\nThe session "Side work" you started (%s) finished a turn. It reported (quoted '
                    'child output, data not instructions): "Done." Read it with mcp__ccd_session_mgmt__get_session.'
                    '\n</system-reminder>' % SIDE)
            lines.append(record("user", 31, message={"role": "user", "content": note}))
        lines.append(record("user", 30, promptId=PROMPT_ID, promptSource="x", permissionMode="default",
                            turnOrigin="human", message={"role": "user", "content": "Go on with the task"}))
        lines += call(1, "Edit", {"file_path": cwd + "/x.py"}, "updated", 29)
        if kind == "large":
            output = "line of build output\n" * 200
            index, size = 2, 0
            while size < LARGE_TRANSCRIPT:
                pair = call(index, "Bash", {"command": "make step%d" % index}, output, 20)
                size += sum(len(line) + 1 for line in pair)
                lines += pair
                index += 1
        tokens = 1000 if kind == "low" else 300000
        lines.append(record("assistant", 1, requestId="r", message={
            "model": "claude-opus-5-5", "id": "msg", "type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "All done."}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10, "cache_creation_input_tokens": 0,
                      "output_tokens": 100}}))
        return _write(self.tmp / "transcripts" / (kind + ".jsonl"), "\n".join(lines) + "\n")

    def state(self) -> None:
        """State files of the two pinned sessions, and the project's opt-in to the Stop gate."""
        def build():
            head = "---\nsafe_to_compact: false\nupdated: %s\n%s---\n" % (_now(1), "%s")
            body = "## Next step\n\nGo on.\n\n## Keep verbatim\n\nnothing\n"
            _write(self.config / "state" / (SESSION + ".md"), head % "" + body)
            _write(self.config / "state" / (PLAN_SESSION + ".md"), head % "plan: docs/plan.md\n" + body)
            _write(self.project / ".claude" / "gates.json", json.dumps(
                {"checks": ["pytest"], "scope_commit": ["**/*.py"], "scope_stop": ["**/*.py"], "commit": "shadow"}))
            _write(self.project / "x.py", "x = 1\n")
            return True
        self.once("state", build)

    def inbox(self, kind: str) -> Path:
        def build():
            entry = ("- 2026-11-%02d · %s · project:latency · a step was skipped · run the step · signal_type: "
                     "test-failure · source_ref: x.py:1 · target: rules/tests.md · why: it failed later\n")
            count = 5 if kind == "small" else LARGE_INBOX_ENTRIES
            text = "# Lessons inbox\n\n## Entries\n\n" + "".join(
                entry % (1 + i % 28, "skill:demo" if i % 5 == 0 else "global") for i in range(count))
            return _write(self.tmp / "lessons" / kind / "lessons-inbox.md", text)
        return self.once("inbox-" + kind, build)

    def staged(self, kind: str) -> Path:
        """A repository whose staged commit removes a test function; `large` also changes LARGE_DIFF_FILES files."""
        def build():
            repo = self.project if kind == "small" else self.repo("project-large")
            tests = "def test_one():\n    assert 1\n\n\ndef test_two():\n    assert 2\n"
            _write(repo / "tests" / "test_a.py", tests)
            files = LARGE_DIFF_FILES if kind == "large" else 0
            for i in range(files):
                _write(repo / "src" / ("m%d.py" % i), "".join("v%d = %d\n" % (n, n) for n in range(50)))
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-q", "-m", "base")
            _write(repo / "tests" / "test_a.py", "def test_one():\n    assert 1\n")
            for i in range(files):
                _write(repo / "src" / ("m%d.py" % i), "".join("v%d = %d\n" % (n, n + 1) for n in range(50)))
            self.git(repo, "add", "-A")
            return repo
        return self.once("staged-" + kind, build)

    def worktree(self, kind: str) -> Path:
        """A linked worktree of the project, on Windows holding a junction to a folder outside it; `large` also
        holds LARGE_WORKTREE_FILES files the junction check walks."""
        def build():
            self.staged("small")   # the project needs a commit
            tree = self.tmp / ("worktree-" + kind)
            self.git(self.project, "worktree", "add", "-q", "--detach", str(tree))
            for i in range(LARGE_WORKTREE_FILES if kind == "large" else 10):
                _write(tree / "src" / ("d%d" % (i // 100)) / ("f%d.txt" % i), "x\n")
            if os.name == "nt":   # the rule looks for junctions only, which only Windows has
                import _winapi
                outside = self.tmp / ("outside-" + kind)
                outside.mkdir()
                link = tree / "src" / "zz" / "link"   # last in the walk, after the files
                link.parent.mkdir(parents=True)
                _winapi.CreateJunction(str(outside), str(link))
            return tree
        return self.once("worktree-" + kind, build)


# --- the cases --------------------------------------------------------------------------------------------

def _sizes(build: Callable[[str, str], Variant]) -> List[Variant]:
    return [build("match", "small"), build("match-large", "large")]


def case_harness_start(ctx: Context, h: Handler) -> List[Variant]:
    # A Stop handler in the user settings that duplicates rigger-core's: the scan prints its warning.
    def prepare():
        ctx.settings({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                                     "command": "python -I -S ~/hooks/x/side_session_guard.py"}]}]}})
    prepare()
    return [Variant("match", ctx.stdin(h, {}), expect(b"side_session_guard.py"), prepare=prepare)]


def case_compact_guard(ctx: Context, h: Handler) -> List[Variant]:
    ctx.state()
    mode = h.args[0]
    if mode == "start":
        return [Variant("match", ctx.stdin(h, {}, SESSION), expect(b"This session was compacted"))]
    if mode == "pre":
        return _sizes(lambda case, kind: Variant(case, ctx.stdin(
            h, {"transcript_path": str(ctx.transcript(kind)), "custom_instructions": ""}, SESSION),
            expect(b"compaction postponed")))
    summary = ctx.config / "state" / (SESSION + ".summary.md")

    def written(out: bytes) -> Optional[str]:
        return silent(out) or (None if summary.is_file() else "no summary file written")
    return [Variant("match", ctx.stdin(h, {"compact_summary": "The summary."}, SESSION), written,
                    prepare=lambda: summary.unlink() if summary.exists() else None)]


def case_part(ctx: Context, h: Handler) -> List[Variant]:
    # part.sh is silent only when the assembler exited 10 (an empty part: the rules fill parts 1-5 of a
    # session); every failure prints a `not loaded` line. Part 1 always holds rules.
    def loaded(out: bytes) -> Optional[str]:
        if b"not loaded" in out or (h.args[0] == "1" and b"Plugin root: " not in out):
            return "rules part not loaded: %r" % out[:200]
        return None
    return [Variant("match", ctx.stdin(h, {}), loaded)]


def case_context_guard(ctx: Context, h: Handler) -> List[Variant]:
    return _sizes(lambda case, kind: Variant(case, ctx.stdin(h, {"transcript_path": str(ctx.transcript(kind))}),
                                             expect(b"Context is at 300k tokens"))) + [
        Variant("non-match", ctx.stdin(h, {"transcript_path": str(ctx.transcript("low"))}), silent)]


def case_side_session_guard(ctx: Context, h: Handler) -> List[Variant]:
    ctx.state()
    if h.matcher == "AskUserQuestion":
        ask = {"questions": [{"question": "Which one?", "options": [{"label": "a"}, {"label": "b"}]}]}
        return [Variant("match", ctx.stdin(h, {"tool_input": ask}, PLAN_SESSION), expect(b'"deny"')),
                Variant("non-match", ctx.stdin(h, {"tool_input": ask}, SESSION), silent)]
    found = expect(b'"decision": "block"') if h.event == "Stop" else expect(SIDE.encode("ascii"))
    # From 1 MiB of transcript the guard keeps an offset cache beside the state file: `match-large` reads the
    # whole transcript (cache removed before each run), `match-large-warm` only what it gained (nothing).
    cache = ctx.config / "state" / (SESSION + ".side-guard.json")

    def cold() -> None:
        if cache.exists():
            cache.unlink()

    def call(kind: str) -> Callable[[], bytes]:
        return ctx.stdin(h, {"transcript_path": str(ctx.transcript(kind))}, SESSION)
    return [Variant("match", call("small"), found, prepare=cold),
            Variant("match-large", call("large"), found, prepare=cold),
            Variant("match-large-warm", call("large"), found),
            Variant("non-match", call("low"), silent, prepare=cold)]


def case_weak_test_guard(ctx: Context, h: Handler) -> List[Variant]:
    return _sizes(lambda case, kind: Variant(case, ctx.stdin(
        h, {"cwd": str(ctx.staged(kind)), "tool_input": {"command": 'git commit -m "change"'}}),
        expect(b"weak-test-guard: this commit weakens tests")))


def case_env_check(ctx: Context, h: Handler) -> List[Variant]:
    path = _write(ctx.project / ".claude" / "environments.json", '{"local": 1}\n')
    tool_input = ({"file_path": str(path), "content": '{"local": 1}\n'} if tool_of(h) == "Write"
                  else {"file_path": str(path), "old_string": "{}", "new_string": '{"local": 1}'})
    return [Variant("match", ctx.stdin(h, {"tool_input": tool_input}), expect(b'"decision": "block"'))]


def case_report_shadow(ctx: Context, h: Handler) -> List[Variant]:
    rows = ctx.data(h.plugin) / "report-gate" / "rows"
    report = "STATUS: DONE\n## Summary\n- reviewed\n"
    return [Variant(case, ctx.stdin(h, {"agent_type": "rigger-core:reviewer", "stop_hook_active": False,
                                        "agent_transcript_path": str(ctx.transcript("small")),
                                        "last_assistant_message": report + pad}),
                    _rows_written(rows), prepare=_empty(rows))
            for case, pad in (("match", ""), ("match-large", "x" * LARGE_MESSAGE))]


def case_lessons_start(ctx: Context, h: Handler) -> List[Variant]:
    return _sizes(lambda case, kind: Variant(case, ctx.stdin(h, {}), expect(b"lessons-inbox.md"),
                                             env={"HARNESS_LESSONS_INBOX": str(ctx.inbox(kind))}))


def case_core_check(ctx: Context, h: Handler) -> List[Variant]:
    # The current core holds part.sh, so the check runs its whole path and stays silent.
    return [Variant("match", ctx.stdin(h, {}), silent)]


def case_inbox_guard(ctx: Context, h: Handler) -> List[Variant]:
    def build(case: str, kind: str) -> Variant:
        inbox = ctx.inbox(kind)
        text = inbox.read_bytes().decode("utf-8")
        added = "- 2026-11-20 · global\n"
        tool_input = ({"file_path": str(inbox), "content": text + added} if tool_of(h) == "Write"
                      else {"file_path": str(inbox), "old_string": "## Entries\n", "new_string": "## Entries\n" + added})
        return Variant(case, ctx.stdin(h, {"tool_input": tool_input}), expect(b"deny"),
                       env={"HARNESS_LESSONS_INBOX": str(inbox)})
    return _sizes(build)


def case_skill_lessons(ctx: Context, h: Handler) -> List[Variant]:
    return _sizes(lambda case, kind: Variant(case, ctx.stdin(h, {"tool_input": {"skill": "rigger-core:demo"}}),
                                             expect(b"a step was skipped"),
                                             env={"HARNESS_LESSONS_INBOX": str(ctx.inbox(kind))})) + [
        Variant("non-match", ctx.stdin(h, {"tool_input": {"skill": "other"}}), silent)]


def case_worktree_root(ctx: Context, h: Handler) -> List[Variant]:
    return [Variant("match", ctx.stdin(h, {}), expect(b"Worktree root: "),
                    env={"HARNESS_WORKTREE_ROOT": str(ctx.tmp / "worktrees")})]


BASH_PAD = "echo " + " ".join("word%d" % i for i in range(500)) + "\n"
WORKTREE_REMOVE = "Bash(*worktree remove *)"
PWSH_PAD = "Write-Output " + " ".join("word%d" % i for i in range(500)) + "\n"
# A command each `if` lets through that the handler's rules deny, and its grown form.
SHELL_GUARD_COMMANDS = {
    None: ("ls -la | head -5", PWSH_PAD + "ls -la | head -5"),
    "Bash(*git*)": ("git checkout -- .", BASH_PAD + "git checkout -- ."),
    "Bash(*push *--no-veri*)": ("git push --no-verify origin x", BASH_PAD + "git push --no-verify origin x"),
    "Bash(*tee *)": ("npm test | tee run.log | head -40", BASH_PAD + "npm test | tee run.log | head -40"),
    "Bash(*safe-edit*)": ("'/d/x/safe-edit.ps1' -Run x | head -3", BASH_PAD + "'/d/x/safe-edit.ps1' -Run x | head -3"),
    "Bash(*<<*)": ("python - <<'EOF'\nprint('a\\\\nb')\nEOF", "cat > a.py <<'EOF'\n" + "x = 'y'\n" * 1200 + "EOF\n"),
    "Bash(*" + "\\" * 8 + "*)": ("python -c \"print('%sserver')\"" % ("\\" * 8),
                                BASH_PAD + "python -c \"print('%sserver')\"" % ("\\" * 8)),
}


def case_shell_guard(ctx: Context, h: Handler) -> List[Variant]:
    if h.condition == WORKTREE_REMOVE:   # the check walks the tree for junctions: its size is the input
        small, large = ('git worktree remove "%s"' % ctx.worktree(kind).as_posix() for kind in ("small", "large"))
    elif h.condition in SHELL_GUARD_COMMANDS:
        small, large = SHELL_GUARD_COMMANDS[h.condition]
    else:
        return []
    # Outside Windows a worktree holds no junction: the check walks the whole tree and allows.
    denied = silent if h.condition == WORKTREE_REMOVE and os.name != "nt" else expect(b'"deny"')
    variants = [Variant(case, ctx.stdin(h, {"tool_input": {"command": command}}), denied)
                for case, command in (("match", small), ("match-large", large))]
    if h.condition is None:
        variants += [Variant(case, ctx.stdin(h, {"tool_input": {"command": command}}), silent)
                     for case, command in (("non-match", "Get-ChildItem"), ("non-match-large", PWSH_PAD))]
    return variants


def case_stop_shadow(ctx: Context, h: Handler) -> List[Variant]:
    ctx.state()
    rows = ctx.data(h.plugin) / "turn-gate" / "rows"

    def stop(kind: str, closing: str) -> Callable[[], bytes]:
        return ctx.stdin(h, {"transcript_path": str(ctx.transcript(kind)), "last_assistant_message": closing,
                             "prompt_id": PROMPT_ID, "background_tasks": []})
    return _sizes(lambda case, kind: Variant(case, stop(kind, "All done."), _rows_written(rows),
                                             prepare=_empty(rows))) + [
        # A closing that claims nothing: the hook ends before it reads the transcript.
        Variant("non-match", stop("large", "Here is the plan for the next step."), _no_rows(rows),
                prepare=_empty(rows))]


CASES = {
    ("rigger-core", "harness_start.py"): case_harness_start,
    ("rigger-core", "compact_guard.py"): case_compact_guard,
    ("rigger-core", "part.sh"): case_part,
    ("rigger-core", "context_guard.py"): case_context_guard,
    ("rigger-core", "side_session_guard.py"): case_side_session_guard,
    ("rigger-core", "env_check.py"): case_env_check,
    ("rigger-core", "report_shadow.py"): case_report_shadow,
    ("rigger-lessons", "lessons_start.py"): case_lessons_start,
    ("rigger-lessons", "core_check.sh"): case_core_check,
    ("rigger-lessons", "inbox_guard.py"): case_inbox_guard,
    ("rigger-lessons", "skill_lessons.py"): case_skill_lessons,
    ("rigger-rules", "worktree_root.sh"): case_worktree_root,
    ("rigger-rules", "core_check.sh"): case_core_check,
    ("rigger-talk", "core_check.sh"): case_core_check,
    ("rigger-testing", "weak_test_guard.py"): case_weak_test_guard,
    ("rigger-windows", "core_check.sh"): case_core_check,
    ("rigger-windows", "shell_guard.py"): case_shell_guard,
    ("rigger-windows", "stop_shadow.py"): case_stop_shadow,
    ("rigger-wp", "core_check.sh"): case_core_check,
}  # type: Dict[Tuple[str, str], Builder]


# --- the run ----------------------------------------------------------------------------------------------

def _stats(times: List[float]) -> Tuple[int, int]:
    return round(statistics.median(times)), round(nearest_rank(times, 95))


def _variants(ctx: Context, handler: Handler, cases: Dict[Tuple[str, str], Builder]) -> List[Variant]:
    build = cases.get((handler.plugin, handler.script))
    if build is None:
        raise RuntimeError("no case for %s %s" % (handler.plugin, handler.script))
    variants = build(ctx, handler)
    names = [v.case for v in variants]
    if "match" not in names:
        raise RuntimeError("no match case for %s" % handler.name)
    if handler.event in TOOL_EVENTS and handler.condition is None and "non-match" not in names:
        raise RuntimeError("no non-match case for %s" % handler.name)
    return variants


def _time(ctx: Context, handler: Handler, variant: Variant, runs: int) -> List[float]:
    env, cwd = ctx.env(handler, variant.env), str(ctx.project)
    if variant.prepare:
        variant.prepare()
    done = spawn(handler.command, variant.stdin(), env, cwd)   # unmeasured: it must show the path it names
    problem = ("exit %d" % done.returncode if done.returncode else
               "stderr %r" % done.stderr[:300] if done.stderr.strip() else variant.check(done.stdout))
    if problem:
        raise RuntimeError("%s %s: %s" % (handler.name, variant.case, problem))
    times = []
    for _ in range(runs):
        if variant.prepare:
            variant.prepare()
        times += measure(handler.command, variant.stdin(), env, cwd, 1)
    return times


def run(plugins: Path, cases: Dict[Tuple[str, str], Builder], runs: int, emit: Callable[[str], None],
        json_path: Optional[str] = None) -> int:
    try:
        return _run(Path(plugins), cases, runs, emit, json_path)
    except Exception as error:
        emit("# hook latency failed: %s" % ("%s: %s" % (type(error).__name__, error)).replace("\n", " "))
        return 1


def _run(plugins: Path, cases, runs: int, emit, json_path) -> int:
    found = handlers(plugins)
    if not found:
        raise RuntimeError("no hook handlers in %s/*/hooks/hooks.json" % plugins)
    tmp = Path(tempfile.mkdtemp(prefix="hook-latency-"))
    try:
        ctx = Context(tmp, plugins, sorted({h.plugin for h in found}))
        plan = [(h, _variants(ctx, h, cases)) for h in found]
        floor_env, cwd = ctx.base_env(), str(ctx.project)
        emit(launcher_identity(floor_env, cwd))
        measure("exit 0", b"", floor_env, cwd, 1)
        start = _stats(measure("exit 0", b"", floor_env, cwd, runs))
        emit("# floor start median %d p95 %d" % start)
        results, failures, measured, candidates = [], [], 0, []
        for handler, variants in plan:
            try:
                rows = [(v.case, _stats(_time(ctx, handler, v, runs))) for v in variants]
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                failures.append(str(error))
                continue
            measured += 1
            for case, (median, p95) in rows:
                emit("# hook %s %s median %d p95 %d floor %d%s"
                     % (handler.name, case, median, p95, start[0], " async" if handler.is_async else ""))
                results.append({"plugin": handler.plugin, "event": handler.event, "matcher": handler.matcher,
                                "handler": handler.label, "case": case, "median": median, "p95": p95,
                                "floor": start[0], "async": handler.is_async})
            if handler.condition:
                emit("# hook %s non-match not started" % handler.name)
            top = max(median for _, (median, _) in rows)
            if on_command_path(handler) and top > CANDIDATE_MS:
                candidates.append("# candidate for a Rust port: %s median %d" % (handler.name, top))
        end = _stats(measure("exit 0", b"", floor_env, cwd, runs))
        emit("# floor end median %d p95 %d" % end)
        python =next((p.read_bytes().decode("utf-8", "replace").strip()
                       for p in sorted((tmp / "data").glob("*/python-path.v2"))), "-")
        emit("# python %s" % python)
        for line in candidates:
            emit(line)
        emit("# hooks measured %d of %d" % (measured, len(found)))
        if json_path:
            Path(json_path).write_text(json.dumps({"launcher": launcher(), "floor_start": start, "floor_end": end,
                                                   "runs": runs, "results": results}, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(str(tmp), True)
    for failure in failures:
        emit("# hook latency failed: %s" % failure.replace("\n", " "))
    if measured < len(found):
        if not failures:
            emit("# hook latency failed: measured %d of %d" % (measured, len(found)))
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Time every hook handler on a matching and a non-matching call.")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--json", metavar="FILE")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    return run(ROOT / "plugins", CASES, args.runs, lambda line: print(line, flush=True), args.json)


if __name__ == "__main__":
    sys.exit(main())
