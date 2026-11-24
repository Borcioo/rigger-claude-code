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
sys.path.insert(0, str(HERE.parent))

import parts  # noqa: E402  the part counts part.sh names in its lines
NO_PYTHON = b"Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)\n"
EXITED = "Rigger rules not loaded: the assembler exited {} (run /rigger-core:doctor)\n"
PRINTED_NOTHING = "Rigger rules not loaded: the assembler printed nothing (run /rigger-core:doctor)\n"
LOST = "Rigger rules part {} of {} not loaded: {} (run /rigger-core:doctor)\n"
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
        # part.sh calls `sh` by name, as hook commands do: the sh folder must be on PATH.
        env["PATH"] = path if path is not None else os.pathsep.join([str(Path(SH).parent), env.get("PATH", "")])
        return env

    def run_part(self, *args, env=None, stdin=None):
        done = subprocess.run([SH, posix(self.core / "tools" / "assemble" / "part.sh")] + list(args),
                              input=self.stdin if stdin is None else stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env or self.env(), cwd=str(self.tmp), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_no_python_prints_visible_line(self):
        for candidate in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
            if os.name != "nt" and os.path.exists(candidate):
                self.skipTest("%s exists, so py.sh finds a real Python here" % candidate)
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
        resume = json.dumps({"cwd": str(self.tmp), "source": "resume"}).encode()
        self.assertEqual(self.run_part("1", stdin=resume), b"")  # exit 10: nothing on purpose
        real = script.read_bytes()
        partial = b"import sys\nsys.stdout.write('half')\nsys.stdout.flush()\nraise SystemExit(1)\n"
        rows = (
            ("exit 3", b"raise SystemExit(3)\n", EXITED.format(3)),
            ("missing", None, EXITED.format(2)),
            ("empty file", b"", PRINTED_NOTHING),  # I3: a half-written cache exits 0 with no output
            ("partial output then exit 1", partial, EXITED.format(1)),  # M2
        )
        for name, body, line in rows:
            with self.subTest(name):
                if body is None:
                    script.unlink()
                else:
                    script.write_bytes(body)
                try:
                    self.assertEqual(self.run_part("1"), line.encode())
                    self.assertEqual(wrapped(self.run_part("1", "--subagent")), line)
                finally:
                    script.write_bytes(real)

    def test_a_failing_part_above_one_prints_its_own_line(self):
        # D29: each lost part says so with its own number; an empty part on purpose (exit 10) stays silent.
        self.assertEqual(self.run_part("2"), b"")
        self.assertEqual(self.run_part("2", "--subagent"), b"")
        script = self.core / "tools" / "assemble" / "assemble.py"
        real = script.read_bytes()
        partial = b"import sys\nsys.stdout.write('half')\nsys.stdout.flush()\nraise SystemExit(1)\n"
        rows = (
            ("exit 3", b"raise SystemExit(3)\n", "the assembler exited 3"),
            ("missing script", None, "the assembler exited 2"),
            ("empty file", b"", "the assembler printed nothing"),
            ("output then exit 1", partial, "the assembler exited 1"),
        )
        for name, body, reason in rows:
            if body is None:
                script.unlink()
            else:
                script.write_bytes(body)
            try:
                session, subagent = parts.SESSION_PARTS, parts.SUBAGENT_PARTS
                for args, count in ((("2",), session), (("3",), session), (("2", "--subagent"), subagent)):
                    with self.subTest(name, args=args):
                        line = LOST.format(args[0], count, reason)
                        out = self.run_part(*args)
                        if "--subagent" in args:
                            self.assertTrue(out.endswith(b"}\n"), out)
                            self.assertEqual(wrapped(out), line)
                        else:
                            self.assertEqual(out, line.encode())
            finally:
                script.write_bytes(real)

    @unittest.skipUnless(os.name == "nt", "a backslash separates folders only on Windows")
    def test_backslash_script_path(self):
        control = self.run_part("1")
        self.assertIn(b"keep a state file", control)
        path = str(self.core / "tools" / "assemble" / "part.sh")
        self.assertNotIn("/", path)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        done = subprocess.run([SH, path, "1"], input=self.stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=self.env(), cwd=str(elsewhere), timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, control), done.stderr)

    def test_bare_script_name(self):
        # Started as `sh part.sh 1` from its own folder: $0 has no separator, so the folder is `.`.
        control = self.run_part("1")
        self.assertIn(b"keep a state file", control)
        done = subprocess.run([SH, "part.sh", "1"], input=self.stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=self.env(), cwd=str(self.core / "tools" / "assemble"), timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, control), done.stderr)


if __name__ == "__main__":
    unittest.main()
