"""Spec section 13 exit criteria: every assembled section reaches the first request, and discovery reads
RIGGER_MODULE_DIRS, not a scan of --plugin-dir.

Real `claude -p` sessions with a temp config, against a local stand-in for the API (rigger-rules'
tests/stub_api.py), so no login or cost. The expected text comes from `assemble.py --render` with the
same module roots and an empty config dir; each section's marker is its first non-blank line, counted in
the one request that carries the probe's unique word. The init event of `--output-format stream-json`
lists the plugins Claude Code loaded (spike S4), which proves a plugin left out of the roots still ran.

Phase 2 exit criterion 2 (SlotSessionTests): a command reviewer set in the person's config shows in `## Slots`
with the line that runs it, a stub model runs that line through the Bash tool (spike Q3's form,
`--allowedTools Bash`), and the `slot-run:` line reaches the next request as a tool_result. That a real model
then treats a refusal as a blocked review is not provable with a stub.

Phase 3b exit criteria (ShellwiseSessionTests, TurnGuardSessionTests): the shellwise module's hook denies a
break command and passes a warn command with its note, a removed binary leaves the session line and an
unguarded command, rigger-testing writes a commit row, and Task 5's guard holds a turn while a background gate
runs. The binary is installed by the module's own installer (`--child`) from a local server over the pinned
archive in the folder SHELLWISE_ASSETS names, so its SHA-256 is checked against pin.json and no test reaches the
network; without SHELLWISE_ASSETS the shellwise cases skip. The download from the pinned release itself is
docs/superpowers/spikes/phase-3b/exit-criteria.md's scratch run.
"""

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ASSEMBLE = HERE.parent
PLUGINS = ASSEMBLE.parent.parent.parent
sys.path.insert(0, str(ASSEMBLE))
sys.path.insert(0, str(PLUGINS / "rigger-rules" / "tests"))

import assemble  # noqa: E402
from discover import root_text  # noqa: E402
from stub_api import USAGE, QuietServer, StubApi  # noqa: E402

ALL = ("rigger-core", "rigger-lessons", "rigger-panel", "rigger-review", "rigger-rules", "rigger-talk",
       "rigger-platform", "rigger-testing", "rigger-wp")
LEFT_OUT = "rigger-wp"
MODULES = ("rigger-core", "rigger-review", "rigger-testing")
TESTING_TRIGGER = "writing code or tests, reviewing tests, a spike, port or rehearsal"
INSTALL_NOTICE = " is empty: install "
# D24/I1: each of these runs its own core_check.sh <name> at a startup SessionStart.
CHECKED = ("rigger-rules", "rigger-platform", "rigger-wp", "rigger-lessons", "rigger-talk")
NOT_LOADED = "Rigger rules not loaded"
ANCESTOR_SENTINEL = "ancestor-claude-md-sentinel"
TIMEOUT = 90
# Dropped from the child's environment: host auth, a base URL and every harness knob. The child-session
# flag stays: clearing it would let the child open a Remote Control connection on the owner's account.
DROPPED = ("CLAUDE_", "ANTHROPIC_", "HARNESS_", "RIGGER_")
KEPT = ("CLAUDE_CODE_CHILD_SESSION",)
CORE = PLUGINS / "rigger-core"
SLOT_RUN = CORE / "tools" / "slot-run" / "slot_run.py"
FAKE_REVIEWER = SLOT_RUN.parent / "tests" / "fake_reviewer.py"
# The ## Slots line (D36, spike Q3), anchored on the core root as the session prints it.
SLOT_LINE = re.compile(r"sh '{root}/bin/py\.sh' '{root}/tools/slot-run/slot_run\.py' <slot> --session (\S+) "
                       r"--base <base revision>".format(root=re.escape(root_text(CORE))))
TOOL_ID = "toolu_slot1"


def module_dirs(names):
    return os.pathsep.join(str(PLUGINS / name) for name in names)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def main_rules(name):
    with open(str(PLUGINS / name / "rigger-module.json"), encoding="utf-8") as handle:
        return [rule for rule in json.load(handle)["rules"] if rule["audience"] != "subagent"]


def first_line(text):
    return next(line for line in text.decode("utf-8").splitlines() if line.strip())


def child_env(base, **extra):
    env = {k: v for k, v in base.items() if not k.upper().startswith(DROPPED) or k.upper() in KEPT}
    env.update(extra)
    return env


def stream_claude(command, cwd, env, timeout=TIMEOUT):
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
        status = proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)
        status = None
    reader.join(10)
    proc.stdout.close()
    return status, lines


def init_plugins(lines):
    """Plugin names of the stream-json init event."""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return sorted(p["name"] if isinstance(p, dict) else p for p in event.get("plugins", []))
    return None


def init_session_id(lines):
    """session_id of the stream-json init event."""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("session_id")
    return None


def slots_lines(lines):
    """The ## Slots section of one request: its heading up to the next heading or the end."""
    if "## Slots" not in lines:
        return []
    start = lines.index("## Slots")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return lines[start:end]


def notices(lines):
    """Lines a hook printed: the first arrives as "SessionStart:<source> hook success: <line>"."""
    return [line.split("hook success: ", 1)[-1] for line in lines if line.startswith("rigger: ")
            or "hook success: rigger: " in line]


def tool_results(body):
    """Text of each tool_result block the request's last message carries, by tool_use id."""
    last = body["messages"][-1]
    found = {}
    for block in last.get("content") if isinstance(last.get("content"), list) else ():
        if isinstance(block, dict) and block.get("type") == "tool_result":
            found[block.get("tool_use_id")] = "\n".join(strings(block.get("content")))
    return found


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


class SlotHandler(BaseHTTPRequestHandler):
    """Answers the probe request with a Bash tool_use running the ## Slots line cut from that request, with
    `<slot>` and `<base revision>` filled from the server, when the server has a base; every other request,
    and the one that carries the tool_result, gets text."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.send_body(404, "application/json", b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.send_body(404, "application/json", b"{}")
            return
        body = json.loads(raw.decode("utf-8"))
        server = self.server
        server.requests.append(body)
        texts = list(strings(body.get("messages")))
        line = None
        if server.base and not tool_results(body) and any(server.probe in text for text in texts):
            line = next((found.group(0) for found in map(SLOT_LINE.search, texts) if found), None)
        if line is None:
            block, stop = {"type": "text", "text": "STUB-DONE"}, "end_turn"
        else:
            command = line.replace("<slot>", "reviewer").replace("<base revision>", server.base)
            block, stop = {"type": "tool_use", "id": TOOL_ID, "name": "Bash",
                           "input": {"command": command, "description": "run the reviewer slot"}}, "tool_use"
        self.send_body(200, "text/event-stream", sse(body.get("model", "stub"), block, stop))


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class StubSessionTests(unittest.TestCase):
    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="stub-session-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        # Claude Code reads CLAUDE.md from every folder above the cwd; the sentinel proves the exclusion.
        (self.tmp / "CLAUDE.md").write_text(ANCESTOR_SENTINEL + "\n", encoding="utf-8")
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"]}), encoding="utf-8")

    def rendered(self, names):
        """{section id: first line} and the whole text --render prints for these roots, empty config dir."""
        config = self.tmp / ("render-config-%d" % len(names))
        config.mkdir()
        env = child_env(os.environ, CLAUDE_CONFIG_DIR=str(config), RIGGER_MODULE_DIRS=module_dirs(names),
                        HOME=str(self.tmp))
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ASSEMBLE / "assemble.py"), "--render"],
                              cwd=str(self.cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        inputs = assemble.Inputs(config_dir=config, project_dir=self.cwd, environ=env,
                                 self_root=PLUGINS / "rigger-core")
        found = assemble.assemble(inputs, "main")
        # The ids come from the same assembly: it must print what --render printed.
        self.assertEqual(assemble.whole_text(found), proc.stdout)
        markers = {block[2]: first_line(block[1]) for block in found.blocks}
        modules = {section["id"]: section["module"] for section in found.sections}
        self.assertEqual(found.notices, [])
        # Read from the manifests, not from discovery: a discovery that drops a root drops it on both sides.
        self.assertEqual(sorted(set(modules.values())), sorted(name for name in names if main_rules(name)),
                         "modules with main-session rules that have no section")
        repeated = [line for line, n in Counter(markers.values()).items() if n > 1]
        self.assertEqual(repeated, [], "two sections share a first line; it cannot mark one of them")
        return markers, modules

    def session(self, names, plugins=ALL):
        """Run one session with `plugins` loaded (default every plugin) and `names` as the module roots; return
        the request that carries the probe word, as lines, and the init event's plugin names."""
        probe = "probe-" + uuid.uuid4().hex
        env = child_env(os.environ,
                        CLAUDE_CONFIG_DIR=str(self.tmp / "cfg"),
                        CLAUDE_CODE_PLUGIN_CACHE_DIR=str(self.tmp / "plugin-cache"),
                        HARNESS_LESSONS_INBOX=str(self.tmp / "lessons-inbox.md"),
                        ANTHROPIC_API_KEY="stub-key",
                        RIGGER_MODULE_DIRS=module_dirs(names),
                        HOME=str(self.tmp),
                        USERPROFILE=str(self.tmp))
        command = [self.claude, "-p", probe, "--model", "haiku", "--output-format", "stream-json", "--verbose",
                   "--settings", str(self.settings), "--debug-file", str(self.tmp / "debug.log")]
        for plugin in plugins:
            command += ["--plugin-dir", str(PLUGINS / plugin)]
        with StubApi() as api:
            env["ANTHROPIC_BASE_URL"] = api.url
            status, out = stream_claude(command, self.cwd, env)
        self.assertEqual(status, 0, "\n".join(out[-20:]))
        requests = [body for body in api.requests if any(probe in text for text in strings(body.get("messages")))]
        self.assertEqual(len(requests), 1, "requests that carry the probe word")
        texts = list(strings(requests[0]))
        self.assertFalse([text for text in texts if ANCESTOR_SENTINEL in text], "a CLAUDE.md above the cwd leaked in")
        self.debug = (self.tmp / "debug.log").read_text(encoding="utf-8", errors="replace")
        return [line for text in texts for line in text.splitlines()], init_plugins(out)

    @staticmethod
    def count(lines, marker):
        # A hook's first line arrives as "SessionStart:<source> hook success: <line>".
        return sum(1 for line in lines if line == marker or line.endswith("hook success: " + marker))

    def test_every_section_reaches_the_first_request(self):
        markers, _ = self.rendered(ALL)
        lines, plugins = self.session(ALL)
        # The CLI adds built-in plugins of its own; all 9 of ours must be among them.
        self.assertEqual(sorted(set(ALL) - set(plugins or [])), [], "init event plugins")
        # Each plugin's own hooks.json loaded and ran its check: the init list alone shows a plugin whose
        # hooks.json is broken. The plugin name argument tells the five copies apart.
        ran = {name: sum(1 for line in self.debug.splitlines() if "Hooks: SessionStart:startup [" in line
                         and ('core_check.sh" %s] (plugin %s@' % (name, name)) in line) for name in CHECKED}
        self.assertEqual({name: n for name, n in ran.items() if n != 1}, {}, "core_check handlers that ran")
        # D19: with a current core no part handler and no core_check says the rules are missing.
        self.assertEqual([line for line in lines if NOT_LOADED in line], [])
        self.assertEqual({sid: n for sid, n in ((sid, self.count(lines, m)) for sid, m in markers.items())
                          if n != 1}, {})

    def test_root_left_out_of_module_dirs_drops_that_module(self):
        everything, modules = self.rendered(ALL)
        rest = [name for name in ALL if name != LEFT_OUT]
        kept, _ = self.rendered(rest)
        dropped = {sid: m for sid, m in everything.items() if modules[sid] == LEFT_OUT}
        self.assertTrue(dropped, "the left-out module has no sections to miss")
        lines, plugins = self.session(rest)
        self.assertIn(LEFT_OUT, plugins or [], "init event plugins: the left-out module did not load")
        self.assertEqual({sid: n for sid, n in ((sid, self.count(lines, m)) for sid, m in dropped.items()) if n},
                         {})
        root_line = "Plugin root: " + root_text(PLUGINS / LEFT_OUT)
        self.assertEqual([line for line in lines if line.endswith(root_line)], [])
        self.assertEqual({sid: n for sid, n in ((sid, self.count(lines, m)) for sid, m in kept.items()) if n != 1},
                         {})

    def test_new_modules_fill_their_slots(self):
        # D57: with both modules loaded and named as roots, the two slots take their level-1 defaults.
        lines, plugins = self.session(MODULES, plugins=MODULES)
        self.assertEqual(sorted(set(MODULES) - set(plugins or [])), [], "init event plugins")
        self.assertIn("| reviewer | agent | rigger-review:reviewer | by-rule | default |", slots_lines(lines))
        self.assertIn("| testing | skill | rigger-testing:testing | — | default |", slots_lines(lines))
        self.assertIn("| %s | `rigger-testing:testing` |" % TESTING_TRIGGER, lines)
        self.assertEqual([line for line in notices(lines) if INSTALL_NOTICE in line], [])

    def test_without_them_the_session_names_the_install_commands(self):
        # D57: a --plugin-dir core names the public marketplace; the testing line says the guard is off.
        lines, _ = self.session(("rigger-core",), plugins=("rigger-core",))
        self.assertEqual([line for line in notices(lines) if INSTALL_NOTICE in line],
                         ["rigger: slot reviewer is empty: install rigger-review "
                          "(claude plugin install rigger-review@rigger)",
                          "rigger: slot testing is empty: install rigger-testing "
                          "(claude plugin install rigger-testing@rigger); the commit-time weak-test guard is off"])


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class SlotSessionTests(unittest.TestCase):
    """rigger-core and the review module (whose agent is the reviewer default), in a cwd that is a two-commit
    repository; the reviewer slot set to a command."""

    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="stub-slots-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        (self.tmp / "CLAUDE.md").write_text(ANCESTOR_SENTINEL + "\n", encoding="utf-8")
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"]}), encoding="utf-8")
        self.config = self.tmp / "cfg"
        self.personal = self.config / "rigger" / "config.json"
        self.record = str(self.tmp / "record.json")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        empty = self.tmp / "gitconfig"
        empty.write_bytes(b"")
        self.git_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty), GIT_AUTHOR_NAME="test",
                            GIT_AUTHOR_EMAIL="test", GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "a.py").write_bytes(b"x = 1\n")
        self.base = self.commit("one")
        (self.repo / "a.py").write_bytes(b"x = 2\n")
        self.commit("two")

    def git(self, *args):
        proc = subprocess.run(["git"] + list(args), cwd=str(self.repo), env=self.git_env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8").strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def command(self, mode):
        """The fake reviewer's argv (Task 14's form): a real interpreter by its absolute path, no shell."""
        return [sys.executable, str(FAKE_REVIEWER), mode, "--record", self.record, "--out", "{out}",
                "--scope", "{scope}", "--base", "{base}", "--head", "{head}"]

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(data, schema=1)), encoding="utf-8")

    def session(self, run_line=False):
        """One session; returns (the first request carrying the probe, as lines; every request carrying it;
        the init event's session id). With run_line the stub runs the ## Slots line with --base <first commit>."""
        probe = "probe-" + uuid.uuid4().hex
        env = child_env(os.environ,
                        CLAUDE_CONFIG_DIR=str(self.config),
                        CLAUDE_CODE_PLUGIN_CACHE_DIR=str(self.tmp / "plugin-cache"),
                        HARNESS_LESSONS_INBOX=str(self.tmp / "lessons-inbox.md"),
                        ANTHROPIC_API_KEY="stub-key",
                        RIGGER_MODULE_DIRS=module_dirs(["rigger-core", "rigger-review"]),
                        HOME=str(self.tmp))  # USERPROFILE stays: Store Python derives sys.executable from it
        command = [self.claude, "-p", probe, "--model", "haiku", "--output-format", "stream-json", "--verbose",
                   "--settings", str(self.settings), "--allowedTools", "Bash", "--plugin-dir", str(CORE),
                   "--plugin-dir", str(PLUGINS / "rigger-review")]
        server = QuietServer(("127.0.0.1", 0), SlotHandler)
        server.requests, server.probe, server.base = [], probe, self.base if run_line else None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:%d" % server.server_address[1]
            status, out = stream_claude(command, self.repo, env)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
        self.assertEqual(status, 0, "\n".join(out[-20:]))
        requests = [body for body in server.requests if any(probe in text for text in strings(body.get("messages")))]
        self.assertTrue(requests, "no request carries the probe word")
        texts = list(strings(requests[0]))
        self.assertFalse([text for text in texts if ANCESTOR_SENTINEL in text], "a CLAUDE.md above the cwd leaked in")
        session_id = init_session_id(out)
        self.assertTrue(session_id, "the init event names no session id")
        return [line for text in texts for line in text.splitlines()], requests, session_id

    def test_the_slots_section_reaches_the_first_request(self):
        self.write_json(self.personal, {"slots": {"reviewer": {"kind": "command", "target": self.command("ok")}}})
        lines, _, session_id = self.session()
        self.assertTrue(slots_lines(lines), "no ## Slots section")
        # D36: a command shows its program and an ellipsis; a level-3 value is `personal`. Fields merge one by
        # one (spec 7.3), so the model is level 1's.
        self.assertIn("| reviewer | command | %s … | by-rule | personal |" % sys.executable, slots_lines(lines))
        runs = [found.group(1) for found in map(SLOT_LINE.search, lines) if found]
        self.assertEqual(runs, [session_id], "the ## Slots command line, with this session's id")

    def test_a_stub_command_reviewer_is_run_and_a_refused_output_is_blocked(self):
        for mode, check in (("bad-status", lambda last: last.startswith("slot-run: refused: status: ")),
                            ("ok", lambda last: last == "slot-run: ok reviewer 1 findings")):
            with self.subTest(mode=mode):
                if os.path.exists(self.record):
                    os.remove(self.record)
                self.write_json(self.personal, {"slots": {"reviewer": {"kind": "command",
                                                                       "target": self.command(mode)}}})
                _, requests, _ = self.session(run_line=True)
                results = [tool_results(body)[TOOL_ID] for body in requests if TOOL_ID in tool_results(body)]
                self.assertEqual(len(results), 1, "requests carrying the slot run's tool_result")
                # The CLI may add a line of its own ("Shell cwd was reset to ..."), so the last slot-run line.
                ran = [line for line in results[0].splitlines() if line.startswith("slot-run: ")]
                self.assertTrue(ran, results[0])
                outputs = [line[len("slot-run: output "):] for line in ran if line.startswith("slot-run: output ")]
                for path in outputs:  # slot_run.py keeps its work folder for the caller
                    self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
                self.assertTrue(check(ran[-1]), results[0])
                # The control that slot_run.py started the fake reviewer.
                self.assertTrue(os.path.exists(self.record), "the fake reviewer's argv file")

    def test_project_command_needs_trust_and_shows_its_hash(self):
        argv = self.command("ok")
        self.write_json(self.repo / ".claude" / "rigger.json",
                        {"slots": {"reviewer": {"kind": "command", "target": argv}}})
        lines, _, _ = self.session()
        # D33: dropped with the notice naming the person's config; the level-1 agent fills the slot.
        self.assertIn("rigger: .claude/rigger.json may not set a command for slot reviewer; add it to "
                      "trust.project_commands in %s to allow it" % self.personal, notices(lines))
        self.assertIn("| reviewer | agent | rigger-review:reviewer | by-rule | default |", slots_lines(lines))
        self.assertEqual([line for line in lines if SLOT_LINE.search(line)], [], "a command line with no command")

        self.write_json(self.personal, {"trust": {"project_commands": ["reviewer"]}})
        lines, _, _ = self.session()
        self.assertIn("| reviewer | command | %s … | by-rule | project |" % sys.executable, slots_lines(lines))
        # D53: the argv as one line of ASCII JSON, cut to 200 characters, its size and a hash of the whole line.
        whole = json.dumps(argv)
        shown = "%s … (%d bytes, sha256:%s)" % (whole[:200], len(whole), hashlib.sha256(whole.encode()).hexdigest()[:12])
        self.assertIn("rigger: slot reviewer runs the project's command %s (trusted in %s)" % (shown, self.personal),
                      notices(lines))

    def test_project_command_without_trust_never_runs(self):
        self.write_json(self.repo / ".claude" / "rigger.json",
                        {"slots": {"reviewer": {"kind": "command", "target": self.command("ok")}}})
        _, _, session_id = self.session()
        env = child_env(os.environ, CLAUDE_CONFIG_DIR=str(self.config), HOME=str(self.tmp))
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SLOT_RUN), "reviewer", "--session", session_id,
                               "--base", self.base], cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        lines = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n").splitlines()
        self.assertEqual((proc.returncode, lines[-1:]),
                         (2, ["slot-run: refused: slot reviewer is agent; dispatch its target instead"]))
        self.assertFalse(os.path.exists(self.record), "the project's command ran")


SHELLWISE = PLUGINS / "rigger-shellwise"
GUARDED = ("rigger-core", "rigger-testing", "rigger-shellwise")
# A --plugin-dir plugin's data folder under CLAUDE_CODE_PLUGIN_CACHE_DIR (observed on 2.1.332 and 2.1.323).
DATA = ("data", "%s-inline")
NO_BINARY_LINE = "shellwise: no binary (downloading); commands run unguarded"
# A break only under the module's shellwise.toml (its gate_commands), on every host shell; run unguarded it writes
# its marker file. A warn command that prints its marker.
BREAK = ("safe-edit --run x 2>&1 | head -n 30; echo ran > m3b.txt", "gate-truncated-pipeline", "m3b.txt")
WARN = ('rm -f "$NOPE_VAR"x.tmp; echo M3W', "dynamic-target")
# The detached download child must fail closed if the session ever starts one: https goes to a dead port.
NO_NETWORK = {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
              "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}


def bash_use(tool_id, command, **extra):
    return {"type": "tool_use", "id": tool_id, "name": "Bash",
            "input": dict({"command": command, "description": tool_id}, **extra)}


def tool_result(bodies, tool_id):
    """Text of the first tool_result for tool_id in any message of any request, or None."""
    for body in bodies:
        for message in body.get("messages", []):
            content = message.get("content")
            for block in content if isinstance(content, list) else ():
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == tool_id:
                    return "\n".join(strings(block.get("content")))
    return None


def plugin_hook_lines(debug, event, plugin):
    """Debug-log lines `Hooks: <event> [<command>] (plugin <plugin>@inline) ...`."""
    return [line for line in debug.splitlines()
            if "Hooks: %s [" % event in line and "(plugin %s@inline)" % plugin in line]


class ScriptHandler(BaseHTTPRequestHandler):
    """The n-th main-loop request (one whose tools hold Bash) gets the server's script[n] (one tool_use block),
    every other request end_turn text."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.send_body(404, "application/json", b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.send_body(404, "application/json", b"{}")
            return
        body = json.loads(raw.decode("utf-8"))
        server = self.server
        block = None
        if "Bash" in [tool.get("name") for tool in body.get("tools") or []]:
            with server.lock:
                n = len(server.requests)
                server.requests.append(body)
            block = server.script[n] if n < len(server.script) else None
        if block is None:
            block, stop = {"type": "text", "text": "STUB-DONE"}, "end_turn"
        else:
            stop = "tool_use"
        self.send_body(200, "text/event-stream", sse(body.get("model", "stub"), block, stop))


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def scripted_session(test, tmp, label, names, script, cwd, extra=None, timeout=TIMEOUT):
    """One session with `names` from --plugin-dir and as module roots; returns (main-loop request bodies, debug
    log text). Config and plugin cache are tmp/cfg-<label> and tmp/cache-<label>."""
    probe = "probe-" + uuid.uuid4().hex
    settings = tmp / "settings.json"
    settings.write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"]}), encoding="utf-8")
    debug = tmp / ("debug-%s.log" % label)
    env = child_env({k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")
                     and not k.upper().endswith("_PROXY")},
                    CLAUDE_CONFIG_DIR=str(tmp / ("cfg-" + label)),
                    CLAUDE_CODE_PLUGIN_CACHE_DIR=str(tmp / ("cache-" + label)),
                    HARNESS_LESSONS_INBOX=str(tmp / "lessons-inbox.md"),
                    ANTHROPIC_API_KEY="stub-key",
                    RIGGER_MODULE_DIRS=module_dirs(names),
                    HOME=str(tmp), **NO_NETWORK)  # USERPROFILE stays: Store Python derives sys.executable from it
    env.update(extra or {})
    command = [test.claude, "-p", probe, "--model", "haiku", "--output-format", "stream-json", "--verbose",
               "--settings", str(settings), "--debug-file", str(debug), "--allowedTools", "Bash"]
    for name in names:
        command += ["--plugin-dir", str(PLUGINS / name)]
    server = QuietServer(("127.0.0.1", 0), ScriptHandler)
    server.requests, server.script, server.lock = [], script, threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:%d" % server.server_address[1]
        status, out = stream_claude(command, cwd, env, timeout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
    test.assertEqual(status, 0, "\n".join(out[-20:]))
    requests = [body for body in server.requests if any(probe in text for text in strings(body.get("messages")))]
    test.assertTrue(requests, "no request carries the probe word")
    test.assertFalse([t for t in strings(requests[0]) if ANCESTOR_SENTINEL in t], "a CLAUDE.md above the cwd leaked in")
    return requests, debug.read_text(encoding="utf-8", errors="replace")


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class ShellwiseSessionTests(unittest.TestCase):
    """rigger-core, rigger-testing and rigger-shellwise from --plugin-dir; the shellwise data folder filled by the
    module's installer from the pinned archive on a local server."""

    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        assets = os.environ.get("SHELLWISE_ASSETS")
        if not assets:
            self.skipTest("SHELLWISE_ASSETS unset: no pinned shellwise archive to install without the network")
        sys.path.insert(0, str(SHELLWISE / "tools" / "install"))
        try:
            import shellwise_install
        finally:
            sys.path.remove(str(SHELLWISE / "tools" / "install"))
        self.pin = json.loads((SHELLWISE / "pin.json").read_text(encoding="utf-8"))
        self.target = shellwise_install.host_target()
        self.assertIn(self.target, self.pin["assets"], "no pinned asset for this host")
        archive = Path(assets) / self.pin["assets"][self.target]["name"]
        self.assertTrue(archive.is_file(), "SHELLWISE_ASSETS holds no %s" % archive.name)
        self.tmp = Path(tempfile.mkdtemp(prefix="stub-shellwise-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        (self.tmp / "CLAUDE.md").write_text(ANCESTOR_SENTINEL + "\n", encoding="utf-8")
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        files = QuietServer(("127.0.0.1", 0), functools.partial(QuietFiles, directory=str(assets)))
        thread = threading.Thread(target=files.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(files.server_close)
        self.addCleanup(files.shutdown)
        self.base_url = "http://127.0.0.1:%d/" % files.server_address[1]

    def install(self, label):
        """The installer's --child into the data folder the session of `label` reads; the folder's shellwise/."""
        data = self.tmp / ("cache-" + label) / DATA[0] / (DATA[1] % "rigger-shellwise")
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SHELLWISE / "tools" / "install" /
                                                                       "shellwise_install.py"),
                               "--child", "--data", str(data), "--base-url", self.base_url],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))
        status = json.loads((data / "shellwise" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["version"], status["target"]),
                         ("ready", self.pin["version"], self.target), status)
        return data / "shellwise"

    def session(self, label, script, cwd=None, extra=None):
        return scripted_session(self, self.tmp, label, GUARDED, script, cwd or self.cwd, extra)

    def test_break_is_denied_and_warn_runs_with_its_note(self):
        self.install("deny")
        command, rule, marker = BREAK
        requests, debug = self.session("deny", [bash_use("toolu_break", command), bash_use("toolu_warn", WARN[0])])
        # The control that this session read the installed folder: its first request names the ready binary.
        first = [line for text in strings(requests[0]) for line in text.splitlines()]
        self.assertIn("SessionStart:startup hook success: shellwise: v%s ready" % self.pin["version"], first)
        denied = tool_result(requests, "toolu_break")
        self.assertIsNotNone(denied, "no request carries the break command's reply")
        self.assertIn("shellwise: break %s: " % rule, denied)
        self.assertFalse((self.cwd / marker).exists(), "the denied command ran")
        ran = tool_result(requests, "toolu_warn")
        self.assertIn("M3W", (ran or "").splitlines(), "the warn command did not run")
        after = [body for body in requests if tool_result([body], "toolu_warn") is not None]
        self.assertTrue([t for t in strings(after[0]) if "shellwise: warn %s: " % WARN[1] in t], "the warn note")
        self.assertEqual(len(plugin_hook_lines(debug, "PreToolUse:Bash", "rigger-shellwise")), 2,
                         "shellwise PreToolUse:Bash handler runs")

    def test_commit_writes_one_row_from_the_testing_module(self):
        empty = self.tmp / "gitconfig"
        empty.write_bytes(b"")
        git = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(empty), "GIT_AUTHOR_NAME": "test",
               "GIT_AUTHOR_EMAIL": "test", "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test"}
        git_env = dict({k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}, **git)
        for opted, rows_expected in ((True, 1), (False, 0)):
            with self.subTest(gates_json=opted):
                label = "commit-%s" % opted
                self.install(label)
                repo = self.tmp / ("repo-%s" % opted)
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=str(repo), env=git_env, check=True, timeout=60)
                (repo / "a.py").write_bytes(b"x = 1\n")
                if opted:
                    (repo / ".claude").mkdir()
                    (repo / ".claude" / "gates.json").write_text(json.dumps(
                        {"commit": "shadow", "checks": ["pytest"], "scope_commit": ["*.py"]}), encoding="utf-8")
                requests, debug = self.session(label, [bash_use("toolu_commit", "git add -A && git commit -q -m one")],
                                               cwd=repo, extra=git)
                log = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=str(repo), env=git_env,
                                     stdout=subprocess.PIPE, timeout=60)
                self.assertEqual(log.stdout.decode().strip(), "1", tool_result(requests, "toolu_commit"))
                rows = self.tmp / ("cache-" + label) / DATA[0] / (DATA[1] % "rigger-testing") / "commit-gate" / "rows"
                found = sorted(rows.iterdir()) if rows.is_dir() else []
                self.assertEqual(len(found), rows_expected, [p.name for p in found])
                for path in found:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(row["op"], "commit")
                    self.assertNotEqual(row["verdict"], "error", row)
                # Both modules' PreToolUse:Bash handlers ran on the commit.
                self.assertTrue(plugin_hook_lines(debug, "PreToolUse:Bash", "rigger-shellwise"))
                self.assertTrue(plugin_hook_lines(debug, "PreToolUse:Bash", "rigger-testing"))

    def test_removed_binary_leaves_the_line_and_the_command_runs(self):
        root = self.install("removed")
        binary = Path((root / "current").read_text(encoding="utf-8").strip())
        binary.unlink()
        # A fresh lock: the session start sees another download running and starts none (no network).
        (root / "lock").write_text(json.dumps({"pid": 0, "start": time.time()}), encoding="utf-8")
        before = {name: (root / name).read_bytes() for name in ("status.json", "lock")}
        command, _, marker = BREAK
        requests, _ = self.session("removed", [bash_use("toolu_break", command)])
        first = [line for text in strings(requests[0]) for line in text.splitlines()]
        self.assertIn("SessionStart:startup hook success: " + NO_BINARY_LINE, first)
        self.assertIsNotNone(tool_result(requests, "toolu_break"), "no request carries the command's reply")
        self.assertNotIn("shellwise:", tool_result(requests, "toolu_break"))
        self.assertTrue((self.cwd / marker).exists(), "the unguarded command did not run")
        self.assertEqual({name: (root / name).read_bytes() for name in before}, before, "a download child ran")


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class TurnGuardSessionTests(unittest.TestCase):
    """Task 5's turn guard (D89) in a real session: the stub starts `<python> run_tests.py` in the background and
    then ends its turn while the script sleeps."""
    SLEEP = 9

    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="stub-guard-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        (self.tmp / "CLAUDE.md").write_text(ANCESTOR_SENTINEL + "\n", encoding="utf-8")
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        (self.cwd / "run_tests.py").write_text("import time\ntime.sleep(%d)\nprint('tests done')\n" % self.SLEEP,
                                               encoding="utf-8")

    def test_a_background_gate_holds_the_turn(self):
        use = bash_use("toolu_gate", '"%s" run_tests.py' % Path(sys.executable).as_posix(), run_in_background=True)
        # `claude -p` reports sdk-cli, which the guard never holds (its control); the desktop app's entrypoint is held.
        for entrypoint, held in (("claude-desktop", True), (None, False)):
            with self.subTest(entrypoint=entrypoint):
                label = "guard-%s" % (entrypoint or "sdk-cli")
                extra = {"CLAUDE_CODE_ENTRYPOINT": entrypoint} if entrypoint else {}
                requests, debug = scripted_session(self, self.tmp, label, ("rigger-core",), [use], self.cwd, extra)
                self.assertIsNotNone(tool_result(requests, "toolu_gate"), "the background run did not start")
                said = [line for body in requests for text in strings(body.get("messages")) for line in text.splitlines()
                        if line.startswith("turn-guard: background run ") and line.endswith("Block 1 of 3.")]
                self.assertEqual(bool(said), held, "a turn-guard block reached the next request")
                stops = plugin_hook_lines(debug, "Stop", "rigger-core")
                self.assertTrue([line for line in stops if "side_session_guard.py" in line], "Stop handler runs")
                blocked = [line for line in debug.splitlines() if '"decision":"block"' in line and "turn-guard:" in line]
                self.assertEqual(bool(blocked), held, "the Stop block in the debug log")
                counts = list((self.tmp / ("cfg-" + label) / "state").glob("*.turn-guard.json"))
                self.assertEqual(bool(counts), held, "the block count in the fresh config's state folder")


if __name__ == "__main__":
    unittest.main(verbosity=2)
