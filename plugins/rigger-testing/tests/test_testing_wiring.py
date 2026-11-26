"""The weak-test guard (D70) and the commit rule's shadow handlers (D79) are wired from this module.


`CommitHandlerTests` runs both PreToolUse handlers of this module's hooks.json the way Claude Code starts a hook
command: on Windows `<Git>/bin/bash.exe -c <command>`, on Linux and macOS `/bin/sh -c <command>` (Task 17 (c)),
with `CLAUDE_PLUGIN_ROOT` this module. A command run never reads the handler's `if` filter; the contract test
`tests/test_module_contracts.py::test_testing_filler_ships_the_commit_guards` pins it, and `StubSessionTests`
shows that a real `claude -p` session (against rigger-rules' tests/stub_api.py stand-in for the API, a temp
config dir, no login or cost) loads this module's hooks.json, matches the `if` on a Bash `git commit` and
denies it.

python -I test_testing_wiring.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
PLUGINS = MODULE.parent
CORE = PLUGINS / "rigger-core"
HOOKS = MODULE / "hooks" / "hooks.json"
sys.path.insert(0, str(PLUGINS / "rigger-rules" / "tests"))

from stub_api import USAGE, QuietServer  # noqa: E402

GUARD = "weak_test_guard.py"
DENIED = "weak-test-guard: this commit weakens tests"
REMOVED = "removed test `test_rejects_garbage`"
TIMEOUT = 120
# Dropped from the child's environment: host auth, a base URL and every harness knob. The child-session flag
# stays: clearing it would let the child open a Remote Control connection on the owner's account.
DROPPED = ("CLAUDE_", "ANTHROPIC_", "HARNESS_", "RIGGER_", "GIT_")
KEPT = ("CLAUDE_CODE_CHILD_SESSION",)

CODE = "def parse(text):\n    return None\n"
TEST = ('import parser\n\n\ndef test_parses_nothing():\n    assert parser.parse("") is None\n\n\n'
        'def test_rejects_garbage():\n    assert parser.parse("x") is None\n')
WEAKER = TEST[:TEST.index("\n\n\ndef test_rejects_garbage")] + "\n"
STRONGER = TEST + '\n\ndef test_rejects_spaces():\n    assert parser.parse(" ") is None\n'


def launcher():
    """The shell Claude Code starts a hook command with, as an argv prefix."""
    if os.name != "nt":
        return ["/bin/sh", "-c"]
    git = shutil.which("git")
    assert git, "git is not on PATH"
    git = Path(git)
    for folder in (git.parent, git.parent.parent, git.parent.parent.parent):
        if (folder / "bin" / "bash.exe").is_file() and (folder / "usr" / "bin" / "bash.exe").is_file():
            return [str(folder / "bin" / "bash.exe"), "-c"]
    raise AssertionError("no Git Bash next to %s" % git)


def handlers():
    """(matcher, hook) of every PreToolUse handler of this module that runs the guard."""
    groups = json.loads(HOOKS.read_bytes().decode("utf-8"))["hooks"].get("PreToolUse", [])
    return [(group.get("matcher"), hook) for group in groups for hook in group.get("hooks", [])
            if GUARD in hook.get("command", "")]


class Repo:
    """A temp repository with one commit (a code file and its two tests) and a staged change."""

    def __init__(self, case, staged_test):
        self.case = case
        self.root = Path(tempfile.mkdtemp(prefix="testing wiring "))
        case.addCleanup(shutil.rmtree, str(self.root), True)
        self.gitconfig = self.root.parent / (self.root.name + ".gitconfig")
        self.gitconfig.write_bytes(b"")
        case.addCleanup(self.gitconfig.unlink)
        self.git("init", "-q")
        self.write("src/parser.py", CODE)
        self.write("tests/test_parser.py", TEST)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.head()
        self.write("tests/test_parser.py", staged_test)
        self.git("add", "-A")

    def env(self, base=None):
        """`base` (default os.environ) without any GIT_* variable, with no system or user config and an identity."""
        env = {k: v for k, v in (os.environ if base is None else base).items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.gitconfig),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t" + "@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t" + "@example.invalid")
        return env

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def git(self, *args):
        proc = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"] + list(args),
                              cwd=str(self.root), env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=60)
        self.case.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8", "replace").strip()

    def head(self):
        return self.git("rev-parse", "HEAD")


class CommitHandlerTests(unittest.TestCase):
    def run_handler(self, matcher, hook, repo):
        env = {k: v for k, v in repo.env().items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        data = Path(tempfile.mkdtemp(prefix="testing wiring data "))
        self.addCleanup(shutil.rmtree, str(data), True)
        env.update(CLAUDE_PLUGIN_ROOT=MODULE.as_posix(), CLAUDE_PLUGIN_DATA=data.as_posix())
        event = {"session_id": str(uuid.uuid4()), "hook_event_name": "PreToolUse", "tool_name": matcher,
                 "tool_input": {"command": "git commit -m change"}, "cwd": str(repo.root)}
        proc = subprocess.run(launcher() + [hook["command"]], input=json.dumps(event).encode("utf-8"), env=env,
                              cwd=str(repo.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""), proc.stdout)
        return proc.stdout

    def test_both_commit_handlers_deny_a_weakening_commit(self):
        found = handlers()
        self.assertEqual(sorted(matcher for matcher, _ in found), ["Bash", "PowerShell"])
        weaker, stronger = Repo(self, WEAKER), Repo(self, STRONGER)
        for matcher, hook in found:
            with self.subTest(matcher=matcher):
                printed = self.run_handler(matcher, hook, weaker)
                self.assertNotEqual(printed, b"", "the handler allowed a weakening commit")
                out = json.loads(printed.decode("ascii"))["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], "deny", out)
                self.assertIn(REMOVED, out["permissionDecisionReason"])
                self.assertEqual(self.run_handler(matcher, hook, stronger), b"")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def sse(model, block, stop):
    message = {"id": "msg_stub", "type": "message", "role": "assistant", "model": model, "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": USAGE}
    start = dict(block)
    if block["type"] == "text":
        start["text"] = ""
        delta = {"type": "text_delta", "text": block["text"]}
    else:
        start["input"] = {}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    events = [("message_start", {"type": "message_start", "message": message}),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 5}}),
              ("message_stop", {"type": "message_stop"})]
    return b"".join(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode("utf-8") for name, data in events)


class Handler(BaseHTTPRequestHandler):
    """Answers the request that carries the probe word with a Bash `git commit`, every other one with text."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.reply(404, "application/json", b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.reply(404, "application/json", b"{}")
            return
        body = json.loads(raw.decode("utf-8"))
        last = body["messages"][-1]
        content = last.get("content") if isinstance(last.get("content"), list) else []
        results = ["\n".join(strings(b.get("content"))) for b in content
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.server.results.extend(results)
        probed = any(self.server.probe in text for text in strings(body.get("messages")))
        if probed and not results:
            block, stop = {"type": "tool_use", "id": "toolu_commit1", "name": "Bash",
                           "input": {"command": "git commit -q -m change", "description": "commit"}}, "tool_use"
        else:
            block, stop = {"type": "text", "text": "STUB-DONE"}, "end_turn"
        self.reply(200, "text/event-stream", sse(body.get("model", "stub"), block, stop))


def stream_claude(command, cwd, env):
    """Run claude with stdout streamed line by line; kill it at the deadline. Returns (status, lines)."""
    proc = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    lines = []

    def read():
        for raw in proc.stdout:
            lines.append(raw.decode("utf-8", "replace").replace("\r\n", "\n").rstrip("\n"))
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        status = proc.wait(TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)
        status = None
    reader.join(10)
    proc.stdout.close()
    return status, lines


def init_plugins(lines):
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return sorted(p["name"] if isinstance(p, dict) else p for p in event.get("plugins", []))
    return None


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class StubSessionTests(unittest.TestCase):
    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="testing-stub-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"]}), encoding="utf-8")

    def session(self, repo, name):
        """One `claude -p` session in the repository whose stub model runs a Bash `git commit`; returns the
        tool_result texts and the debug log lines that ran a PreToolUse:Bash hook."""
        probe = "probe-" + uuid.uuid4().hex
        server = QuietServer(("127.0.0.1", 0), Handler)
        server.probe, server.results = probe, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = {k: v for k, v in os.environ.items() if not k.upper().startswith(DROPPED) or k.upper() in KEPT}
        env = repo.env(base)
        env.update(CLAUDE_CONFIG_DIR=str(self.tmp / ("cfg-" + name)),
                   CLAUDE_CODE_PLUGIN_CACHE_DIR=str(self.tmp / ("plugin-cache-" + name)),
                   HARNESS_LESSONS_INBOX=str(self.tmp / "lessons-inbox.md"),
                   ANTHROPIC_API_KEY="stub-key",
                   ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % server.server_address[1],
                   RIGGER_MODULE_DIRS=os.pathsep.join((str(CORE), str(MODULE))),
                   HOME=str(self.tmp), USERPROFILE=str(self.tmp))
        debug = self.tmp / ("debug-%s.log" % name)
        command = [self.claude, "-p", probe, "--model", "haiku", "--output-format", "stream-json", "--verbose",
                   "--allowedTools", "Bash", "--settings", str(self.settings), "--debug-file", str(debug),
                   "--plugin-dir", str(CORE), "--plugin-dir", str(MODULE)]
        status, out = stream_claude(command, repo.root, env)
        self.assertEqual(status, 0, "\n".join(out[-20:]))
        self.assertIn("rigger-testing", init_plugins(out) or [], "init event plugins")
        ran = [line for line in debug.read_text(encoding="utf-8", errors="replace").splitlines()
               if "Hooks: PreToolUse:Bash" in line and GUARD in line and "(plugin rigger-testing@" in line]
        return server.results, ran

    def test_a_real_session_denies_a_weakening_commit_from_this_module(self):
        weaker = Repo(self, WEAKER)
        results, ran = self.session(weaker, "weaker")
        self.assertEqual(len(results), 1, "tool results that reached the stub")
        self.assertIn(DENIED, results[0])
        self.assertIn(REMOVED, results[0])
        self.assertEqual(len(ran), 1, "debug lines of the guard's PreToolUse:Bash run")
        self.assertEqual(weaker.head(), weaker.base, "the denied commit was made")
        # Control: the same session on a commit that adds a test runs the guard and commits.
        stronger = Repo(self, STRONGER)
        results, ran = self.session(stronger, "stronger")
        self.assertEqual(len(results), 1, "tool results that reached the stub")
        self.assertNotIn("weak-test-guard", results[0])
        self.assertEqual(len(ran), 1, "debug lines of the guard's PreToolUse:Bash run")
        self.assertNotEqual(stronger.head(), stronger.base, "the allowed commit was not made")


if __name__ == "__main__":
    unittest.main(verbosity=2)
