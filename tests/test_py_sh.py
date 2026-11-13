"""Tests for plugins/*/bin/py.sh, run through a real POSIX sh with stub interpreters on PATH.

Stubs are sh scripts named python/python3/py. Each logs to a counter file: "probe <name>" when
called with -c (the version probe), "run <name> <arg>..." otherwise (then it echoes stdin).
Spies named date and mkdir log "tool <name>" and exec the real tool.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT =Path(__file__).resolve().parent.parent
PY_SH = ROOT / "plugins" / "rigger-lessons" / "bin" / "py.sh"
SPIED_TOOLS = ("date", "mkdir")
POSIX_TOOLS = ("cat", "date", "mkdir", "mv", "rm")
NOTICE = "rigger: Python 3.9+ not found; {} hooks are off"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        if git_sh.is_file():
            return str(git_sh)
        return shutil.which("sh")
    return "/bin/sh"


SH = find_sh()


def sh_path(path):
    """Path in the form a hook passes it (forward slashes, as CLAUDE_PLUGIN_ROOT on Windows)."""
    return Path(path).as_posix()


def sh_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


class PyShTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SH, "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="py-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.data = self.tmp / "data"
        self.log = self.tmp / "counter.log"
        self.log.write_bytes(b"")
        self.tools = self.make_tools_dir()
        self.spies = self.tmp / "spies"
        self.spies.mkdir()

    def spy_on_tools(self):
        """Put the date and mkdir spies first on PATH. Each is a shell of its own, so only the tests that read
        tool_calls() have them."""
        for name in SPIED_TOOLS:
            real = shutil.which(name, path=self.tools)
            self.assertIsNotNone(real, "tool %s not found in %s" % (name, self.tools))
            self.write_sh(
                self.spies / name,
                "printf 'tool %s\\n' {name} >>{log}\nexec {real} \"$@\"\n".format(
                    name=sh_quote(name), log=sh_quote(sh_path(self.log)), real=sh_quote(sh_path(real))
                ),
            )

    def make_tools_dir(self):
        """A PATH entry with the tools py.sh and the stubs need, and no Python."""
        if os.name == "nt":
            return str(Path(SH).parent)
        tools = self.tmp / "tools"
        tools.mkdir()
        for name in POSIX_TOOLS:
            real = shutil.which(name)
            self.assertIsNotNone(real, "tool %s not found" % name)
            (tools / name).symlink_to(real)
        return str(tools)

    def write_sh(self, path, body):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def stub(self, name, mode="ok", directory=None):
        """mode: ok = Python 3.9+, 9009 = Windows Store alias without Python, old = Python 3.8,
        empty = exits 0 with an empty sys.executable."""
        directory = directory or self.stubs
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        code = {"ok": 0, "9009": 9009, "old": 1, "empty": 0}[mode]
        body = (
            "name={name}\nlog={log}\n"
            'if [ "$1" = -c ] || {{ [ "$1" = -3 ] && [ "$2" = -c ]; }}; then\n'
            '  printf \'probe %s\' "$name" >>"$log"\n'
            '  [ "$1" != -3 ] || printf \' -3\' >>"$log"\n'
            "  printf '\\n' >>\"$log\"\n"
            "  while IFS= read -r line || [ -n \"$line\" ]; do :; done\n"
            "  printf '%s\\n' {self_path}\n"
            "  exit {code}\n"
            "fi\n"
            'printf \'run %s\' "$name" >>"$log"\n'
            'for a in "$@"; do printf \' <%s>\' "$a" >>"$log"; done\n'
            "printf '\\n' >>\"$log\"\n"
            "while IFS= read -r line || [ -n \"$line\" ]; do printf '%s\\n' \"$line\"; done\n"
        ).format(
            name=sh_quote(name),
            log=sh_quote(sh_path(self.log)),
            self_path=sh_quote("" if mode == "empty" else str(path)),
            code=code,
        )
        self.write_sh(path, body)
        return path

    def run_py_sh(
        self, *args, py_sh=PY_SH, path=None, data=None, stdin=b"", windows=None, cwd=None, home=None, session=None
    ):
        env = dict(os.environ)
        for key in list(env):
            if key.upper().startswith(("CLAUDE_", "HARNESS_")):
                del env[key]
        if session is not None:
            env["CLAUDE_CODE_SESSION_ID"] = session
        entries = path if path is not None else [str(self.stubs)]
        env["PATH"] = os.pathsep.join([str(self.spies)] + list(entries) + [self.tools])
        env["CLAUDE_PLUGIN_DATA"] = sh_path(self.data) if data is None else data
        if windows is None:
            windows = os.name == "nt"
        env.pop("OS", None)
        if windows:
            env["OS"] = "Windows_NT"
        if home is not None:
            env["HOME"] = str(home)
        proc = subprocess.run(
            [SH, sh_path(py_sh)] + list(args),
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(cwd or self.tmp),
            timeout=60,
        )
        return proc

    def events(self):
        return self.log.read_text(encoding="utf-8").splitlines()

    def probes(self):
        return [line for line in self.events() if line.startswith("probe ")]

    def runs(self):
        return [line for line in self.events() if line.startswith("run ")]

    def tool_calls(self):
        return [line for line in self.events() if line.startswith("tool ")]

    def cache_file(self):
        return self.data / "python-path.v2"

    def write_cache(self, text):
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file(), "w", encoding="utf-8", newline="") as handle:
            handle.write(text + "\n")

    def read_cache(self):
        return self.cache_file().read_text(encoding="utf-8")

    def skip_if_system_python_candidates(self):
        for candidate in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
            if os.name != "nt" and os.path.exists(candidate):
                self.skipTest("%s exists, so py.sh finds a real Python here" % candidate)

    # -- cache ------------------------------------------------------------------------------

    def test_cached_path_used_without_probe(self):
        self.spy_on_tools()
        self.stub("python")
        self.stub("python3")
        cached = self.stub("python", directory=self.tmp / "cached")
        self.write_cache(str(cached))
        proc = self.run_py_sh("script.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), [])
        self.assertEqual(self.runs(), ["run python <-I> <-X> <utf8> <script.py>"])
        self.assertEqual(self.tool_calls(), [], "a positive cache hit spawns no date or mkdir")

    def test_stale_cache_reprobes_and_rewrites(self):
        stub = self.stub("python3" if os.name != "nt" else "python")
        self.write_cache(str(self.tmp / "gone" / "python"))
        proc = self.run_py_sh("script.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.read_cache(), str(stub) + "\n")

    def test_probe_result_cached_for_next_run(self):
        self.spy_on_tools()
        self.stub("python3" if os.name != "nt" else "python")
        self.run_py_sh("a.py")
        proc = self.run_py_sh("b.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1, "second run must use the cache")
        self.assertEqual(len(self.runs()), 2)
        self.assertEqual([c for c in self.tool_calls() if c == "tool date"], [])

    def test_negative_cache_prevents_reprobe_within_ten_minutes(self):
        # 540 s leaves a minute for a stalled spawn before the entry turns 600 s old.
        self.stub("python")
        self.stub("python3")
        self.write_cache("none %d S1" % (int(time.time()) - 540))
        proc = self.run_py_sh("--notice", "script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), [])
        self.assertEqual(self.runs(), [])
        self.assertEqual(proc.stdout.decode().splitlines(), [NOTICE.format("rigger-lessons")])

    def test_negative_cache_of_the_same_session_expires_at_ten_minutes(self):
        # A resumed session keeps its id, so a Python installed meanwhile must be found within ten minutes.
        stub = self.stub("python3" if os.name != "nt" else "python")
        self.write_cache("none %d S1" % (int(time.time()) - 600))
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.read_cache(), str(stub) + "\n")

    def test_negative_cache_same_session_prevents_reprobe(self):
        self.stub("python")
        self.stub("python3")
        self.write_cache("none %d S1" % (int(time.time()) - 60))
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), [])
        self.assertEqual(self.runs(), [])

    def test_negative_cache_other_session_reprobes(self):
        self.skip_if_system_python_candidates()
        self.stub("python3", mode="old")
        self.write_cache("none %d S1" % (int(time.time()) - 60))
        proc = self.run_py_sh("script.py", session="S2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python3"])
        match = re.fullmatch(r"none (\d+) S2\n", self.read_cache())
        self.assertIsNotNone(match, self.read_cache())
        self.assertLess(abs(int(match.group(1)) - time.time()), 600)

    def test_negative_cache_without_session_var_reprobes(self):
        self.skip_if_system_python_candidates()
        self.stub("python3", mode="old")
        stamp = int(time.time()) - 60
        # The second entry has an empty third field, which an empty variable must not match.
        for session in (None, ""):
            for entry in ("none %d S1" % stamp, "none %d " % stamp):
                with self.subTest(session=session, entry=entry):
                    self.log.write_bytes(b"")
                    self.write_cache(entry)
                    proc = self.run_py_sh("script.py", session=session)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(self.probes(), ["probe python3"])
                    self.assertEqual(self.read_cache(), entry + "\n", "no negative entry without a session id")

    def test_old_format_negative_cache_reprobes(self):
        stub = self.stub("python3" if os.name != "nt" else "python")
        self.write_cache("none %d" % (int(time.time()) - 60))
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.read_cache(), str(stub) + "\n")

    def test_negative_cache_records_session(self):
        self.skip_if_system_python_candidates()
        self.stub("python3", mode="old")
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(self.read_cache(), r"\Anone \d+ S1\n\Z")

    def test_expired_negative_cache_reprobes(self):
        stub = self.stub("python3" if os.name != "nt" else "python")
        self.write_cache("none %d S1" % (int(time.time()) - 25 * 3600))
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.read_cache(), str(stub) + "\n")

    def test_corrupt_cache_reprobes(self):
        name = "python3" if os.name != "nt" else "python"
        stub = self.stub(name)
        future = "none %d S1" % (int(time.time()) + 10 * 86400)
        for entry in ("none abc S1", "none  S1", "none ", "", future):
            with self.subTest(entry=entry):
                self.log.write_bytes(b"")
                self.write_cache(entry)
                proc = self.run_py_sh("script.py", session="S1")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stderr, b"")
                self.assertEqual(len(self.probes()), 1)
                self.assertEqual(len(self.runs()), 1)
                self.assertEqual(self.read_cache(), str(stub) + "\n")

    def test_failing_date_reprobes(self):
        self.stub("python3" if os.name != "nt" else "python")
        self.write_sh(self.spies / "date", "exit 1\n")
        self.write_cache("none %d S1" % int(time.time()))
        proc = self.run_py_sh("script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(len(self.runs()), 1)

    def test_unwritable_data_dir_still_runs(self):
        self.stub("python3" if os.name != "nt" else "python")
        blocker = self.tmp / "data-is-a-file"
        blocker.write_bytes(b"")
        proc = self.run_py_sh("script.py", data=sh_path(blocker))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(len(self.runs()), 1)

    def test_empty_plugin_data_writes_nothing(self):
        self.spy_on_tools()
        self.stub("python3" if os.name != "nt" else "python")
        work = self.tmp / "cwd"
        work.mkdir()
        home = self.tmp / "home"
        home.mkdir()
        for _ in range(2):
            proc = self.run_py_sh("script.py", data="", cwd=work, home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.probes()), 2, "without a data dir every run probes")
        self.assertEqual(self.tool_calls(), [], "no mkdir or date without a data dir")
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(list(home.iterdir()), [])
        self.assertFalse(self.data.exists())
        self.assertEqual(list(PY_SH.parent.glob("python-path*")), [])

    # -- probing ------------------------------------------------------------------------------

    def test_probe_order_windows(self):
        self.stub("python")
        py = self.stub("py")
        self.stub("python3")
        proc = self.run_py_sh("script.py", windows=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe py -3"])
        self.assertEqual(len(self.runs()), 1)
        self.assertTrue(self.runs()[0].startswith("run py "))
        self.assertEqual(self.read_cache(), str(py) + "\n")

    def test_windows_without_py_uses_python(self):
        python = self.stub("python")
        self.stub("python3")
        proc = self.run_py_sh("script.py", windows=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python"])
        self.assertTrue(self.runs()[0].startswith("run python "))
        self.assertEqual(self.read_cache(), str(python) + "\n")

    def test_failing_py_falls_back_to_python(self):
        for mode in ("9009", "old", "empty"):
            with self.subTest(mode=mode):
                self.log.write_bytes(b"")
                self.cache_file().unlink(missing_ok=True)
                self.stub("py", mode=mode)
                self.stub("python")
                proc = self.run_py_sh("script.py", windows=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.probes(), ["probe py -3", "probe python"])
                self.assertTrue(self.runs()[0].startswith("run python "))

    def test_cache_of_the_old_search_order_is_ignored(self):
        self.stub("py")
        store = self.stub("python", directory=self.tmp / "store")
        self.write_cache(str(store))
        self.cache_file().rename(self.data / "python-path")
        proc = self.run_py_sh("script.py", windows=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe py -3"])
        self.assertTrue(self.runs()[0].startswith("run py "))
        self.assertEqual(self.read_cache(), str(self.stubs / "py") + "\n")
        self.assertEqual((self.data / "python-path").read_text(encoding="utf-8"), str(store) + "\n")

    def test_probe_order_posix(self):
        self.stub("python")
        self.stub("python3")
        proc = self.run_py_sh("script.py", windows=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python3"])
        self.assertTrue(self.runs()[0].startswith("run python3 "))

    def test_store_alias_9009_skipped(self):
        self.stub("python", mode="9009")
        self.stub("python3")
        proc = self.run_py_sh("script.py", windows=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python", "probe python3"])
        self.assertEqual(len(self.runs()), 1)
        self.assertTrue(self.runs()[0].startswith("run python3 "))

    def test_empty_executable_skipped(self):
        self.stub("python", mode="empty")
        self.stub("python3")
        proc = self.run_py_sh("script.py", windows=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python", "probe python3"])
        self.assertTrue(self.runs()[0].startswith("run python3 "))

    def test_python_38_rejected(self):
        self.skip_if_system_python_candidates()
        self.stub("python3", mode="old")
        self.stub("python")
        proc = self.run_py_sh("script.py", windows=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.probes(), ["probe python3", "probe python"])
        self.assertTrue(self.runs()[0].startswith("run python "))
        self.assertEqual(self.read_cache(), str(self.stubs / "python") + "\n")

    def test_none_found_with_notice_prints_one_line(self):
        self.skip_if_system_python_candidates()
        self.stub("python3", mode="old")
        proc = self.run_py_sh("--notice", "script.py", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.decode().splitlines(), [NOTICE.format("rigger-lessons")])
        self.assertEqual(self.runs(), [])
        entry = self.read_cache().split()
        self.assertEqual(entry[0], "none")
        self.assertLess(abs(int(entry[1]) - time.time()), 600)

    def test_none_found_without_notice_is_silent(self):
        self.skip_if_system_python_candidates()
        proc = self.run_py_sh("script.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")
        self.assertEqual(self.runs(), [])

    # -- running ------------------------------------------------------------------------------

    def test_stdin_reaches_script_on_probe_path(self):
        self.stub("python3" if os.name != "nt" else "python")
        payload = b'{"hook_event_name": "SessionStart"}\n'
        proc = self.run_py_sh("script.py", stdin=payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(len(self.probes()), 1)
        self.assertEqual(proc.stdout, payload)

    def test_args_with_spaces_preserved_and_flags_forwarded(self):
        name = "python3" if os.name != "nt" else "python"
        self.stub(name)
        # No quote characters: Git Bash re-parses them in a Windows command line before sh sees argv.
        proc = self.run_py_sh("dir with space/script.py", "a b", "", "x *", "--notice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.runs(),
            ["run %s <-I> <-X> <utf8> <dir with space/script.py> <a b> <> <x *> <--notice>" % name],
        )

    def test_plugin_root_with_space(self):
        root = self.tmp / "plugin root" / "spaced"
        (root / "bin").mkdir(parents=True)
        (root / ".claude-plugin").mkdir()
        shutil.copyfile(PY_SH, root / "bin" / "py.sh")
        with open(root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8", newline="") as handle:
            handle.write('{\n  "name": "spaced-plugin",\n  "author": {"name": "Someone"}\n}\n')
        self.skip_if_system_python_candidates()
        proc = self.run_py_sh("--notice", "x.py", py_sh=root / "bin" / "py.sh", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.decode().splitlines(), [NOTICE.format("spaced-plugin")])
        self.cache_file().unlink()
        name = "python3" if os.name != "nt" else "python"
        self.stub(name)
        script = sh_path(root / "hooks" / "start.py")
        proc = self.run_py_sh("--notice", script, py_sh=root / "bin" / "py.sh", session="S1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.runs(), ["run %s <-I> <-X> <utf8> <%s>" % (name, script)])

    def test_real_interpreter_probe_then_cache(self):
        script = self.tmp / "real script.py"
        with open(script, "w", encoding="utf-8", newline="") as handle:
            handle.write(
                "import sys\n"
                "print(sys.flags.isolated, sys.flags.utf8_mode, sys.argv[1:], sys.stdin.read().strip())\n"
            )
        interpreter_dir = str(Path(sys.executable).parent)
        expected = "1 1 ['a b', 'é'] {\"x\": 1}"
        first = self.run_py_sh(sh_path(script), "a b", "é", path=[interpreter_dir], stdin=b'{"x": 1}\n')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout.decode("utf-8").strip(), expected)
        cached = self.read_cache().strip()
        self.assertTrue(cached and cached != "none" and os.path.exists(cached), cached)
        second = self.run_py_sh(sh_path(script), "a b", "é", path=[], stdin=b'{"x": 1}\n')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.decode("utf-8").strip(), expected, "second run (no Python on PATH) uses the cache")


class SystemPythonSkipTests(unittest.TestCase):
    def test_tests_that_rule_out_every_python_skip_on_a_machine_with_a_system_one(self):
        # py.sh falls back to fixed paths outside PATH, so a real interpreter there beats the stubs.
        names = (
            "test_python_38_rejected",
            "test_none_found_with_notice_prints_one_line",
            "test_none_found_without_notice_is_silent",
            "test_plugin_root_with_space",
            "test_negative_cache_other_session_reprobes",
            "test_negative_cache_without_session_var_reprobes",
            "test_negative_cache_records_session",
        )
        for name in names:
            with self.subTest(name):
                result = unittest.TestResult()
                skip = unittest.SkipTest("system python")
                with mock.patch.object(PyShTests, "skip_if_system_python_candidates", side_effect=skip):
                    PyShTests(name).run(result)
                self.assertEqual([reason for _, reason in result.skipped], ["system python"])


class Recorder(unittest.TestResult):
    """Keeps every outcome call, with its arguments, to be replayed on the real result."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _keep(name):
        def keep(self, *args):
            self.calls.append((name, args))

        return keep

    addSuccess = _keep("addSuccess")
    addError = _keep("addError")
    addFailure = _keep("addFailure")
    addSkip = _keep("addSkip")
    addExpectedFailure = _keep("addExpectedFailure")
    addUnexpectedSuccess = _keep("addUnexpectedSuccess")
    addSubTest = _keep("addSubTest")


class PooledSuite(unittest.TestSuite):
    """Runs its tests on a thread pool, each against a Recorder, then replays the records to the real result in
    suite order, so the report reads like a serial run. Every test spawns shells that wait on process creation, which
    overlaps; the tests share nothing, each builds its own temp folder."""

    WORKERS = 6

    @staticmethod
    def record(test):
        recorder = Recorder()
        test.run(recorder)
        return recorder.calls

    def run(self, result, debug=False):
        tests = list(self)
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self.record, test) for test in tests]
            for test, future in zip(tests, futures):
                result.startTest(test)
                try:
                    for name, args in future.result():
                        getattr(result, name)(*args)
                finally:
                    result.stopTest(test)
        return result


def load_tests(loader, standard_tests, pattern):
    # SystemPythonSkipTests patches a PyShTests method, so it runs after the pool has ended.
    return unittest.TestSuite(
        [PooledSuite(loader.loadTestsFromTestCase(PyShTests)), loader.loadTestsFromTestCase(SystemPythonSkipTests)]
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
