"""Tests for part.sh: the two failures it turns into one visible line, run through a real POSIX sh."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[2]
NO_PYTHON = b"Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)\n"
EXITED = "Rigger rules not loaded: the assembler exited {} (run /rigger-core:doctor)\n"
COPIED = ("bin", ".claude-plugin", "rigger-module.json", "rigger-defaults.json", "agents", "skills")


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


SH = find_sh()


def posix(path):
    return Path(path).as_posix()


def wrapped(line):
    return json.loads(line.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]


class PartShTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SH, "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="part-sh-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.core = self.tmp / "rigger-core"
        for name in COPIED:
            source = CORE / name
            if source.is_dir():
                shutil.copytree(str(source), str(self.core / name))
            else:
                self.core.mkdir(exist_ok=True)
                shutil.copyfile(str(source), str(self.core / name))
        (self.core / "rules").mkdir()
        # A section ending in a blank line: part.sh must keep every trailing newline.
        (self.core / "rules" / "state-file.md").write_bytes(b"## State file\n\nkeep a state file\n\n")
        target = self.core / "tools" / "assemble"
        target.mkdir(parents=True)
        for path in (CORE / "tools" / "assemble").iterdir():
            if path.is_file() and path.suffix in (".py", ".sh"):
                shutil.copyfile(str(path), str(target / path.name))
        self.config = self.tmp / "config"
        self.config.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stdin = json.dumps({"cwd": str(self.tmp), "source": "startup", "agent_type": "general-purpose"}).encode()

    def env(self, path=None, data=None):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env.update({"CLAUDE_CONFIG_DIR": str(self.config), "CLAUDE_PLUGIN_ROOT": posix(self.core),
                    "HOME": str(self.home), "CLAUDE_PLUGIN_DATA": posix(self.tmp / "data") if data is None else data})
        if path is not None:
            env["PATH"] = path
        return env

    def run_part(self, *args, env=None):
        done = subprocess.run([SH, posix(self.core / "tools" / "assemble" / "part.sh")] + list(args),
                              input=self.stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env or self.env(), cwd=str(self.tmp), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_no_python_prints_visible_line(self):
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        for name in ("py", "python", "python3"):
            with open(str(stubs / name), "w", encoding="utf-8", newline="") as handle:
                handle.write("#!/bin/sh\nexit 1\n")
            (stubs / name).chmod(0o755)
        env = self.env(path=os.pathsep.join([str(stubs), str(Path(SH).parent)]), data="")
        self.assertEqual(self.run_part("1", env=env), NO_PYTHON)
        self.assertEqual(wrapped(self.run_part("1", "--subagent", env=env)), NO_PYTHON.decode())
        self.assertEqual(self.run_part("2", env=env), b"")
        self.assertEqual(self.run_part("2", "--subagent", env=env), b"")

    def test_assembler_exit_status_prints_visible_line(self):
        script = self.core / "tools" / "assemble" / "assemble.py"
        direct = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), "--part", "1"], input=self.stdin,
                                stdout=subprocess.PIPE, env=self.env(), cwd=str(self.tmp), timeout=60).stdout
        control = self.run_part("1")
        self.assertIn(b"Plugin root: ", control)
        self.assertTrue(control.endswith(b"keep a state file\n\n"), control[-40:])
        self.assertEqual(control, direct)
        real = script.read_bytes()
        for body, status in ((b"raise SystemExit(3)\n", 3), (None, 2)):
            with self.subTest(status=status):
                if body is None:
                    script.unlink()
                else:
                    script.write_bytes(body)
                try:
                    line = EXITED.format(status)
                    self.assertEqual(self.run_part("1"), line.encode())
                    self.assertEqual(wrapped(self.run_part("1", "--subagent")), line)
                    self.assertEqual(self.run_part("2"), b"")
                    self.assertEqual(self.run_part("2", "--subagent"), b"")
                finally:
                    script.write_bytes(real)


if __name__ == "__main__":
    unittest.main()
