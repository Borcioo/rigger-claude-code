"""Spec section 13 exit criteria: every assembled section reaches the first request, and discovery reads
RIGGER_MODULE_DIRS, not a scan of --plugin-dir.

Real `claude -p` sessions with a temp config, against a local stand-in for the API (rigger-rules'
tests/stub_api.py), so no login or cost. The expected text comes from `assemble.py --render` with the
same module roots and an empty config dir; each section's marker is its first non-blank line, counted in
the one request that carries the probe's unique word. The init event of `--output-format stream-json`
lists the plugins Claude Code loaded (spike S4), which proves a plugin left out of the roots still ran.
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
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ASSEMBLE = HERE.parent
PLUGINS = ASSEMBLE.parent.parent.parent
sys.path.insert(0, str(ASSEMBLE))
sys.path.insert(0, str(PLUGINS / "rigger-rules" / "tests"))

import assemble  # noqa: E402
from discover import root_text  # noqa: E402
from stub_api import StubApi  # noqa: E402

ALL = ("rigger-core", "rigger-lessons", "rigger-panel", "rigger-rules", "rigger-talk", "rigger-windows",
       "rigger-wp")
LEFT_OUT = "rigger-wp"
# D24/I1: each of these runs its own core_check.sh <name> at a startup SessionStart.
CHECKED = ("rigger-rules", "rigger-windows", "rigger-wp", "rigger-lessons", "rigger-talk")
NOT_LOADED = "Rigger rules not loaded"
ANCESTOR_SENTINEL = "ancestor-claude-md-sentinel"
TIMEOUT = 90
# Dropped from the child's environment: host auth, a base URL and every harness knob. The child-session
# flag stays: clearing it would let the child open a Remote Control connection on the owner's account.
DROPPED = ("CLAUDE_", "ANTHROPIC_", "HARNESS_", "RIGGER_")
KEPT = ("CLAUDE_CODE_CHILD_SESSION",)


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

    def session(self, names):
        """Run one session with every plugin loaded and `names` as the module roots; return the request
        that carries the probe word, as lines, and the init event's plugin names."""
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
        for plugin in ALL:
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
        # The CLI adds built-in plugins of its own; all 7 of ours must be among them.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
