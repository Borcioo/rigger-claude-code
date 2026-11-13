"""rigger-core checks that span files: the launcher on a real interpreter, the real hook commands, the
state-file rule against compact_guard.py, the load rule of orchestrating-subagents and the finding
labels its fix rounds share with the reviewer agent."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent
HOOKS = CORE / "hooks" / "hooks.json"
INSTALL = CORE / "tools" / "context-guard" / "INSTALL.md"
RULE = CORE / "rules" / "state-file.md"
SKILL = CORE / "skills" / "orchestrating-subagents" / "SKILL.md"
REVIEWER = CORE / "agents" / "reviewer.md"
COMPACT_GUARD = CORE / "tools" / "context-guard" / "compact_guard.py"
SID = "1234d76d-5612-4458-8c62-41bf0710415f"
DROPPED_PREFIXES = ("CLAUDE_", "HARNESS_")
NOTICE = "rigger: Python 3.9+ not found; rigger-core hooks are off\n"
UNSAFE = "---\nsafe_to_compact: false\nupdated: 2026-11-03T10:00:00+00:00\n---\n## Goal\nship it\n"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


SH = find_sh()


def clean_env(**extra):
    """No CLAUDE_*/HARNESS_* and none of the variables that hide a code-page failure."""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(DROPPED_PREFIXES) and key.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(extra)
    return env


def harness_start_group():
    groups = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    found = [group for group in groups if any("harness_start.py" in hook["command"] for hook in group["hooks"])]
    assert len(found) == 1, found
    return found[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SH, "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="core wiring "))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class LauncherTests(Base):
    @unittest.skipUnless(os.name == "nt", "stdout defaults to UTF-8 outside Windows: the failure needs a code page")
    def test_python_under_a_non_ascii_folder_runs_and_is_cached(self):
        venv = self.tmp / "Grüße Ωμέγα 日本 folder" / "venv"
        subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120,
                       env=clean_env(), capture_output=True)
        script = self.tmp / "report.py"
        script.write_text("import sys\nprint('ran', sys.flags.utf8_mode, sys.executable)\n", encoding="utf-8")
        data = self.tmp / "data"
        env = clean_env(OS="Windows_NT", CLAUDE_PLUGIN_DATA=data.as_posix(),
                        PATH=os.pathsep.join((str(venv / "Scripts"), str(Path(SH).parent))))
        command = [SH, (CORE / "bin" / "py.sh").as_posix(), "--notice", script.as_posix()]
        for run in ("probe", "cache"):
            with self.subTest(run=run):
                proc = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
                self.assertEqual((proc.returncode, proc.stderr), (0, b""))
                expected = "ran 1 %s" % (venv / "Scripts" / "python.exe")
                self.assertEqual(proc.stdout.decode("utf-8").strip(), expected)
        self.assertEqual((data / "python-path.v2").read_bytes().decode("utf-8", "replace").strip(),
                         str(venv / "Scripts" / "python.exe"))


class HarnessStartHookTests(Base):
    def test_it_fires_at_startup_only(self):
        matcher = harness_start_group()["matcher"]
        fires = [source for source in ("startup", "resume", "clear", "compact") if re.fullmatch(matcher, source)]
        self.assertEqual(fires, ["startup"])

    def test_the_real_command_says_when_no_python_was_found(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "python-path.v2").write_bytes(b"none %d %s\n" % (int(time.time()), SID.encode("ascii")))
        command = harness_start_group()["hooks"][0]["command"]
        proc = subprocess.run([SH, "-c", command], stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                              env=clean_env(CLAUDE_PLUGIN_ROOT=CORE.as_posix(), CLAUDE_PLUGIN_DATA=data.as_posix(),
                                            CLAUDE_CODE_SESSION_ID=SID,
                                            PATH=os.pathsep.join((str(Path(SH).parent), os.environ["PATH"]))))
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        self.assertEqual(proc.stdout.decode("utf-8"), NOTICE)


def env_check_handlers():
    groups = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"].get("PostToolUse", [])
    return [(group.get("matcher"), hook) for group in groups for hook in group["hooks"] if "env_check.py" in hook["command"]]


class EnvCheckHookTests(Base):
    def test_it_fires_on_write_and_edit_of_the_environments_file_only(self):
        found = sorted((matcher, hook["if"], hook["timeout"]) for matcher, hook in env_check_handlers())
        self.assertEqual(found, [("Write|Edit", "Edit(//**/.claude/environments.json)", 10),
                                 ("Write|Edit", "Write(//**/.claude/environments.json)", 10)])

    def test_the_real_command_returns_the_panel_reason(self):
        env_file = self.tmp / ".claude" / "environments.json"
        env_file.parent.mkdir()
        event = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(self.tmp),
                            "tool_input": {"file_path": str(env_file)}}).encode("utf-8")
        env = clean_env(CLAUDE_PLUGIN_ROOT=CORE.as_posix(), CLAUDE_PLUGIN_DATA=(self.tmp / "data").as_posix(),
                        PATH=os.pathsep.join((str(Path(SH).parent), os.environ["PATH"])))
        reason = ('%s: the Environments pane will show "Environments unavailable: wrong shape: "version" must be 1". '
                  'Fix the file and write it again.' % env_file)
        handlers = env_check_handlers()
        self.assertEqual(len(handlers), 2)
        for _, hook in handlers:
            for content, want in (('{"version":2,"environments":{}}', {"decision": "block", "reason": reason}),
                                  ('{"version":1,"environments":{}}', None)):
                with self.subTest(handler=hook["if"], content=content):
                    env_file.write_text(content, encoding="utf-8")
                    proc = subprocess.run([SH, "-c", hook["command"]], input=event, capture_output=True, timeout=30, env=env)
                    self.assertEqual((proc.returncode, proc.stderr), (0, b""))
                    self.assertEqual(proc.stdout.decode("utf-8"), json.dumps(want) + "\n" if want else "")


class StateFileRuleTests(Base):
    """The session writes where the rule says; `pre` has to find it there."""

    def rule_path(self, home, config):
        """The state file the injected rule names, for this home and config dir."""
        text = subprocess.run([SH, (CORE / "rules" / "rules_start.sh").as_posix(), "state-file.md"], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=30, env=clean_env(CLAUDE_PLUGIN_ROOT=CORE.as_posix()))
        named = re.findall(r"`([^`\n]*/state/<CLAUDE_CODE_SESSION_ID>\.md)`", text.stdout.decode("utf-8"))
        self.assertEqual(len(named), 1, named)
        path = named[0].replace("<CLAUDE_CODE_SESSION_ID>", SID)
        path = path.replace("<config dir>", config or "~/.claude")
        return Path(str(home) + path[1:] if path.startswith("~") else path)

    def pre_decision(self, home, config):
        path = self.rule_path(home, config)
        path.parent.mkdir(parents=True)
        path.write_text(UNSAFE, encoding="utf-8", newline="")
        transcript = self.tmp / "t.jsonl"
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 259000}
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        transcript.write_text(json.dumps({
            "type": "assistant", "timestamp": stamp, "isSidechain": False,
            "message": {"role": "assistant", "model": "claude-haiku-5-5", "stop_reason": "tool_use", "usage": usage,
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}]}}) + "\n",
            encoding="utf-8")
        event = {"session_id": SID, "trigger": "auto", "transcript_path": str(transcript),
                 "hook_event_name": "PreCompact"}
        env = clean_env(HOME=str(home), USERPROFILE=str(home))
        if config:
            env["CLAUDE_CONFIG_DIR"] = config
        proc = subprocess.run([sys.executable, "-I", "-S", str(COMPACT_GUARD), "pre"], env=env, timeout=30,
                              input=json.dumps(event).encode("utf-8"), capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode("utf-8")

    def test_the_rule_path_blocks_with_a_config_dir(self):
        output = self.pre_decision(self.tmp / "home", str(self.tmp / "other config"))
        self.assertIn('"decision": "block"', output)

    def test_the_rule_path_blocks_without_a_config_dir(self):
        self.assertIn('"decision": "block"', self.pre_decision(self.tmp / "home", ""))

    def test_install_names_the_config_dir_too(self):
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("<config dir>/state/<session id>.md", text)
        self.assertEqual(re.findall(r"~/\.claude/state", text), [])


class CommandPathTests(unittest.TestCase):
    def test_every_plugin_root_path_in_a_command_names_a_file(self):
        written = set()
        for command in sorted((CORE / "commands").glob("*.md")):
            written.update(re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s\"'`)]+)", command.read_text(encoding="utf-8")))
        self.assertIn("tools/doctor/doctor.sh", written)
        missing = sorted(path for path in written if not (CORE / path).is_file())
        self.assertEqual(missing, [])


class LoadRuleTests(unittest.TestCase):
    def test_mem_check_is_guarded_and_the_job_limit_stays(self):
        text = SKILL.read_text(encoding="utf-8")
        bullets = [bullet for bullet in text.split("\n- ") if "mem-check" in bullet]
        self.assertEqual(len(bullets), 1, bullets)
        flat = " ".join(bullets[0].split())
        self.assertIn("starts only while `mem-check` exits 0 on Windows with rigger-windows installed", flat)
        self.assertIn("elsewhere check free memory with the OS's tool", flat)
        self.assertIn("run at most 2 at once", flat)
        self.assertNotIn("GiB", flat)


class FindingLabelTests(unittest.TestCase):
    def test_the_fix_rounds_route_on_the_labels_the_reviewer_gives(self):
        reviewer = " ".join(REVIEWER.read_text(encoding="utf-8").split())
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("## Fix rounds and progress", skill)
        rounds = " ".join(skill.split("## Fix rounds and progress", 1)[1].split())
        self.assertIn("give each finding a severity (Critical, Important, Minor)", reviewer)
        self.assertIn("mark one the base commit already had `Pre-existing`, with its evidence", reviewer)
        self.assertIn("A `Pre-existing` finding with evidence never enters the author's fix round", rounds)
        self.assertIn("A Critical finding on a Risk-full change", rounds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
