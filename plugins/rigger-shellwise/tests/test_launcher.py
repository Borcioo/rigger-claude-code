"""Tests of bin/shellwise.sh and hooks/hooks.json: the PreToolUse command from hooks.json runs through a real
POSIX sh, as Claude Code starts it, against a stub binary (a `#!/bin/sh` script that prints its argv and stdin, or
a set reply) named in `<plugin data>/shellwise/current`.

The shell: on Windows Git's usr/bin/sh.exe (its folder first on PATH, so the inner `sh` is the same one); elsewhere
each shell in RIGGER_TEST_SHELLS (absolute paths separated by spaces, default /bin/sh), linked as `sh` in a folder
put first on PATH, so both the outer and the inner `sh` are that shell. Each test runs once per shell.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks" / "hooks.json"
INSTALL_DIR = PLUGIN / "tools" / "install"
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
GUARDED = """sh -c '[ -f "$0" ] || exit 0; . "$0"' "${CLAUDE_PLUGIN_ROOT}/bin/shellwise.sh\""""
SESSION_START = ('sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/install/shellwise_install.py"'
                 " --session-start")
EVENT = json.dumps({"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                    "tool_input": {"command": "ls -la"}, "cwd": "."}).encode("utf-8")
# The deny reply in the form the release spike recorded for `hook claude-code`.
DENY = (b'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":'
        b'"shellwise: break bash-cd-slash-d: `cd /d` is cmd.exe syntax\\nFix: cd \\"x\\""}}\n')
WINDOWS = os.name == "nt"


def git_sh():
    path = Path(os.environ.get("ProgramFiles", ""), "Git", "usr", "bin", "sh.exe")
    return str(path) if path.is_file() else None


def shells():
    if WINDOWS:
        return [git_sh()]
    return os.environ.get("RIGGER_TEST_SHELLS", "/bin/sh").split()


SHELLS = shells()


def write_stub(path, reply=None, code=0):
    """A stub binary: prints `argv:<arg>` per argument and `stdin:<stdin>`, or reads stdin and prints `reply`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if reply is None:
        body = "for arg in \"$@\"; do printf 'argv:%s\\n' \"$arg\"; done\nprintf 'stdin:'\ncat\n"
    else:
        reply_file = path.parent / (path.name + ".reply")
        reply_file.write_bytes(reply)
        body = "cat >/dev/null\ncat '%s'\n" % reply_file.as_posix()
    path.write_bytes(("#!/bin/sh\n%sexit %d\n" % (body, code)).encode("utf-8"))
    os.chmod(str(path), 0o755)
    return path


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SHELLS and all(SHELLS), "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="shellwise-launcher-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def command(self):
        self.assertTrue(HOOKS.is_file(), "no hooks/hooks.json")
        hooks = json.loads(HOOKS.read_bytes().decode("utf-8"))["hooks"]
        commands = [hook["command"] for group in hooks.get("PreToolUse", []) for hook in group["hooks"]]
        self.assertEqual(len(commands), 1, commands)
        return commands[0]

    def fresh(self, name):
        """A new folder under the test's temp folder whose path ends in `name`."""
        return Path(tempfile.mkdtemp(prefix="t", dir=str(self.tmp))) / name

    def plugin_root(self, name="plugin"):
        """A copy of the plugin without its tests and tools (the launcher, the config), as the cache lays it out."""
        root = self.fresh(name)
        shutil.copytree(str(PLUGIN), str(root), ignore=shutil.ignore_patterns("tests", "tools", "__pycache__"))
        return root

    def stub(self, folder, name="shellwise", **kwargs):
        return write_stub(self.fresh(folder) / name, **kwargs)

    def data_dir(self, current=None, name="data"):
        """A plugin data folder; `current` (bytes) is written as shellwise/current when given."""
        data = self.fresh(name)
        (data / "shellwise").mkdir(parents=True)
        if current is not None:
            (data / "shellwise" / "current").write_bytes(current)
        return data

    def shell_env(self, shell, root, data):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_", "SHELLWISE"))}
        if WINDOWS:
            folder = os.path.dirname(shell)
            outer = shell
        else:
            folder = str(self.tmp / ("shell-" + str(SHELLS.index(shell))))
            outer = os.path.join(folder, "sh")
            if not os.path.exists(outer):
                os.makedirs(folder)
                os.symlink(shell, outer)
        env["PATH"] = folder + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PLUGIN_ROOT"] = Path(root).as_posix() if WINDOWS else str(root)
        if data is not None:
            env["CLAUDE_PLUGIN_DATA"] = str(data)
        return outer, env

    def run_hook(self, shell, root, data, stdin=EVENT, command=None):
        outer, env = self.shell_env(shell, root, data)
        proc = subprocess.run([outer, "-c", command or self.command()], input=stdin, env=env, cwd=str(self.tmp),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return proc.returncode, proc.stdout, proc.stderr

    def stub_lines(self, stdout):
        """(argv, stdin) the argv-printing stub reported."""
        head, sep, stdin = stdout.partition(b"stdin:")
        self.assertEqual(sep, b"stdin:", stdout)
        return [line[len("argv:"):] for line in head.decode("utf-8").splitlines()], stdin

    def assert_config(self, argv, root):
        self.assertEqual(argv[:3], ["hook", "claude-code", "--config"], argv)
        self.assertEqual(len(argv), 4, argv)
        self.assertTrue(os.path.isfile(argv[3]), argv)
        self.assertTrue(os.path.samefile(argv[3], str(root / "shellwise.toml")), argv)


class LauncherTests(LauncherCase):
    def test_launcher_execs_current_with_the_config(self):
        rows = [("lf", b"\n", 0), ("crlf", b"\r\n", 0), ("no newline", b"", 0), ("exit code", b"\n", 3)]
        for shell in SHELLS:
            for label, ending, code in rows:
                with self.subTest(shell=shell, current=label):
                    root = self.plugin_root()
                    stub = self.stub("bin", code=code)
                    data = self.data_dir(str(stub).encode("utf-8") + ending)
                    rc, out, err = self.run_hook(shell, root, data)
                    self.assertEqual((rc, err), (code, b""), out)
                    argv, stdin = self.stub_lines(out)
                    self.assert_config(argv, root)
                    self.assertEqual(stdin, EVENT)

    def test_launcher_passes_the_reply_through_unchanged(self):
        for shell in SHELLS:
            for label, reply in (("deny", DENY), ("clean", b"")):
                with self.subTest(shell=shell, reply=label):
                    stub = self.stub("bin", reply=reply)
                    data = self.data_dir(str(stub).encode("utf-8") + b"\n")
                    self.assertEqual(self.run_hook(shell, self.plugin_root(), data), (0, reply, b""))

    def test_launcher_allows_silently_without_a_binary(self):
        missing = str(self.tmp / "gone" / "shellwise").encode("utf-8")
        folder = self.fresh("a-folder")
        folder.mkdir()
        plain = self.fresh("plain") / "shellwise"
        plain.parent.mkdir()
        plain.write_bytes(b"not a program\n")
        os.chmod(str(plain), 0o644)
        # Git Bash reads a file as executable by its name and first bytes: `plain` is neither a .exe nor a #! script
        rows = [("no data folder", None), ("no current", "absent"), ("missing file", missing + b"\n"),
                ("empty current", b""), ("blank line", b"\n"), ("a folder", str(folder).encode("utf-8") + b"\n"),
                ("not executable", str(plain).encode("utf-8") + b"\n")]
        if not WINDOWS and os.geteuid() != 0:   # mode bits stop neither Windows nor root from reading
            unreadable = self.data_dir(str(self.stub("bin")).encode("utf-8") + b"\n")
            os.chmod(str(unreadable / "shellwise" / "current"), 0)
            rows.append(("unreadable current", unreadable))
        root = self.plugin_root()
        for shell in SHELLS:
            for label, current in rows:
                with self.subTest(shell=shell, case=label):
                    data = current if isinstance(current, Path) else None
                    if isinstance(current, (bytes, str)):
                        data = self.data_dir(None if current == "absent" else current)
                    self.assertEqual(self.run_hook(shell, root, data), (0, b"", b""))

    def test_missing_launcher_exits_zero(self):
        stub = self.stub("bin", reply=DENY)
        data = self.data_dir(str(stub).encode("utf-8") + b"\n")
        root = self.plugin_root()
        self.assertTrue((root / "bin" / "shellwise.sh").is_file(), "the copy has no launcher to delete")
        (root / "bin" / "shellwise.sh").unlink()
        for shell in SHELLS:
            with self.subTest(shell=shell):
                self.assertEqual(self.run_hook(shell, root, data), (0, b"", b""))

    def test_launcher_paths_with_spaces(self):
        root = self.plugin_root("plugin root with spaces")
        stub = self.stub("binary folder", name="shell wise")
        data = self.data_dir(str(stub).encode("utf-8") + b"\n", "plugin data")
        for shell in SHELLS:
            with self.subTest(shell=shell):
                rc, out, err = self.run_hook(shell, root, data)
                self.assertEqual((rc, err), (0, b""), out)
                argv, stdin = self.stub_lines(out)
                self.assert_config(argv, root)
                self.assertEqual(stdin, EVENT)


class WiringTests(LauncherCase):
    def test_hooks_json_wiring(self):
        self.assertTrue(HOOKS.is_file(), "no hooks/hooks.json")
        hooks = json.loads(HOOKS.read_bytes().decode("utf-8"))
        self.assertEqual(hooks, {"hooks": {
            "SessionStart": [{"matcher": "startup|clear|compact|resume", "hooks": [
                {"type": "command", "command": SESSION_START, "timeout": 10}]}],
            "PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
                {"type": "command", "command": GUARDED, "timeout": 5}]}],
        }})
        self.assertNotIn("|| true", GUARDED)

    def test_shipped_launcher_and_hooks_hold_no_cr(self):
        """A CRLF launcher fails open under dash (`: not found`, exit 0): the shipped files are LF only."""
        for path in (PLUGIN / "bin" / "shellwise.sh", HOOKS):
            with self.subTest(path=path.name):
                data = path.read_bytes()
                self.assertGreater(len(data), 0, "read no bytes from %s" % path)
                self.assertTrue(b"\r" not in data, "%s holds a CR byte" % path)

    def test_session_start_command_reports_a_ready_binary(self):
        """The SessionStart command reaches the installer's --session-start through py.sh: with the pinned binary
        in place it prints the ready line."""
        sys.path.insert(0, str(INSTALL_DIR))
        try:
            import shellwise_install as install
        finally:
            sys.path.remove(str(INSTALL_DIR))
        target = install.host_target()
        if target is None:
            self.skipTest("no pinned asset for this host")
        version = json.loads((PLUGIN / "pin.json").read_bytes().decode("utf-8"))["version"]
        data = self.data_dir()
        binary = Path(install._binary_path(install._root(str(data)), version, target))
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"placed\n")
        (data / "shellwise" / "current").write_bytes(str(binary).encode("utf-8") + b"\n")
        # A fresh lock as a net: had the binary read as missing, the handler would start no download either.
        (data / "shellwise" / "lock").write_bytes(json.dumps({"pid": 1, "start": time.time()}).encode("utf-8"))
        self.assertTrue(HOOKS.is_file(), "no hooks/hooks.json")
        hooks = json.loads(HOOKS.read_bytes().decode("utf-8"))["hooks"]
        command = hooks["SessionStart"][0]["hooks"][0]["command"]
        for shell in SHELLS:
            with self.subTest(shell=shell):
                announced = data / "shellwise" / "announced"
                if announced.exists():
                    announced.unlink()
                rc, out, err = self.run_hook(shell, PLUGIN, data, stdin=b"{}", command=command)
                self.assertEqual((rc, out.replace(b"\r\n", b"\n"), err),
                                 (0, ("shellwise: v%s ready\n" % version).encode("utf-8"), b""))


if __name__ == "__main__":
    for item in SHELLS:
        print("# shell %s -> %s" % (item, os.path.realpath(item) if item else None), flush=True)
    unittest.main(verbosity=2)
