"""Shared fixtures of the slot_run tests: a temp config dir, a two-commit repository, a snapshot writer."""
import ctypes
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
TOOLS = os.path.dirname(TOOL_DIR)
SCRIPT = os.path.join(TOOL_DIR, "slot_run.py")
FAKE = os.path.join(HERE, "fake_reviewer.py")
sys.path.insert(0, TOOL_DIR)
sys.path.insert(1, os.path.join(TOOLS, "assemble"))

import slot_run  # noqa: E402
import slots  # noqa: E402
import snapshot  # noqa: E402

NT = os.name == "nt"
REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen
SESSION = "session-1"
SETTING = "NoDefaultCurrentDirectoryInExePath"
SAFE_PATH = "PYTHONSAFEPATH"
NOT_FOUND = "slot-run: refused: reviewer-cli not found on PATH (entries inside the repository are not searched)"
BARE_OR_ABSOLUTE = "slot-run: refused: the program must be a bare name or an absolute path"
# The scope of the fixture: a.py edited, b.py renamed to d.py (a delete and an add), c.py deleted.
SCOPE_TEXT = "review\ta.py\nskip: deleted\tb.py\nskip: deleted\tc.py\nreview\td.py\n"
_TEMPLATE = []  # [repository, base id, head id] of the fixture, built by the first test that needs it


def _children_windows(pid):
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", ctypes.c_uint32),
                    ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot_handle = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot_handle in (None, ctypes.c_void_p(-1).value):
        raise OSError("process snapshot failed")
    found = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel.Process32FirstW(ctypes.c_void_p(snapshot_handle), ctypes.byref(entry))
        while more:
            if entry.th32ParentProcessID == pid:
                found.append("%d %s" % (entry.th32ProcessID, entry.szExeFile))
            more = kernel.Process32NextW(ctypes.c_void_p(snapshot_handle), ctypes.byref(entry))
    finally:
        kernel.CloseHandle(ctypes.c_void_p(snapshot_handle))
    return found


def _children_posix(pid):
    proc = REAL_RUN(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "command="], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    found = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 2 and fields[1] == str(pid):
            found.append(" ".join([fields[0]] + fields[2:]))
    return found


def alive_children(pid=None):
    """The processes now alive that the process `pid` (default: this one) started, as `<pid> <program>` entries
    joined by "; ", or `none`. A lookup that fails says so instead of raising, so a diagnostic never hides the
    error it explains."""
    pid = os.getpid() if pid is None else pid
    try:
        found = _children_windows(pid) if NT else _children_posix(pid)
    except (OSError, subprocess.SubprocessError, AttributeError) as exc:
        return "unavailable (%s)" % exc
    return "; ".join(found) if found else "none"


def remove_tree(path, sleep=time.sleep):
    """Remove a folder tree. A PermissionError (on Windows, a handle a child still holds: WinError 32) is
    reported on stderr with the alive child processes and retried once after 0.5 s: a retry that works prints
    `# cleanup retried: <path>`, a second failure raises with the same text."""
    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    try:
        shutil.rmtree(str(path), onerror=onerror)
        return
    except PermissionError as first:
        note = "# cleanup failed: %s: %s; children: %s" % (path, first, alive_children())
    sys.stderr.write(note + "\n")
    sleep(0.5)
    try:
        shutil.rmtree(str(path), onerror=onerror)
    except PermissionError as second:
        raise PermissionError(second.errno, "%s [%s]" % (second.strerror, note), second.filename) from second
    sys.stderr.write("# cleanup retried: %s\n" % path)


def same_path(left, right):
    return os.path.normcase(os.path.realpath(str(left))) == os.path.normcase(os.path.realpath(str(right)))


def inside(path, root):
    path = os.path.normcase(os.path.realpath(str(path)))
    root = os.path.normcase(os.path.realpath(str(root)))
    return path == root or path.startswith(root + os.sep)


def pid_alive(pid):
    if NT:
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is gone; without /proc (macOS) the signal answer stands
        with open("/proc/%d/stat" % pid, "rb") as handle:
            return handle.read().split(b") ", 1)[1][:1] != b"Z"
    except OSError:
        return True


def command_row(argv, source="personal", model=None, kind="command", filled=True):
    return slots.Row("reviewer", kind, argv, model, source, (), None, filled)


class SlotRunCase(unittest.TestCase):
    """A temp config dir, a two-commit repository and a git environment free of the host's config."""

    def setUp(self):
        self.tmp = Path(os.path.abspath(tempfile.mkdtemp(prefix="slot-run-test-")))
        self.addCleanup(remove_tree, self.tmp)
        self.config = self.tmp / "config"
        empty_config = self.tmp / "gitconfig"
        empty_config.write_bytes(b"")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(
            CLAUDE_CONFIG_DIR=str(self.config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
            GIT_CEILING_DIRECTORIES=str(self.tmp),
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test",
        )
        for name in [name for name in env if name.upper() == SETTING.upper()]:  # Windows keeps names upper case
            del env[name]
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        template, self.base, self.head = self.template()  # before tempdir moves into this test's folder
        work = self.tmp / "work"
        work.mkdir()
        temp_patch = mock.patch.object(tempfile, "tempdir", str(work))
        temp_patch.start()
        self.addCleanup(temp_patch.stop)
        self.record = str(self.tmp / "record.json")
        self.repo = self.tmp / "repo"
        shutil.copytree(template, str(self.repo))  # each test owns a copy; git runs once per file, not per test

    def template(self):
        """The fixture repository, built once per test file: a.py edited, b.py renamed to d.py, c.py deleted."""
        if not _TEMPLATE:
            folder = Path(os.path.abspath(tempfile.mkdtemp(prefix="slot-run-template-")))
            unittest.addModuleCleanup(remove_tree, folder)
            repo = folder / "repo"
            repo.mkdir()
            git = self.git_in(repo, folder)
            git("init", "-q")
            git("config", "core.autocrlf", "false")
            for name, data in (("a.py", b"x = 1\n"), ("b.py", b"b\n"), ("c.py", b"c\n")):
                (repo / name).write_bytes(data)
            git("add", "-A")
            git("commit", "-q", "-m", "one")
            base = git("rev-parse", "HEAD")
            (repo / "a.py").write_bytes(b"x = 2\n")
            git("mv", "b.py", "d.py")
            git("rm", "-q", "c.py")
            git("add", "-A")
            git("commit", "-q", "-m", "two")
            _TEMPLATE.extend([str(repo), base, git("rev-parse", "HEAD")])
        return _TEMPLATE

    def git_in(self, repo, ceiling):
        def git(*args):
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(ceiling))
            proc = REAL_RUN(["git"] + list(args), cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            return proc.stdout.decode("utf-8").strip()
        return git

    def command(self, mode, *extra, program=None):
        return [program or sys.executable, FAKE, mode, "--record", self.record, "--out", "{out}",
                "--scope", "{scope}", "--base", "{base}", "--head", "{head}"] + list(extra)

    def write_snapshot(self, rows, trusted=(), session=SESSION, config=None):
        data = {"format": snapshot.SNAPSHOT_FORMAT, "parts": [], "modules": [], "config": config or {}, "sections": [],
                "rows": [], "slots": slots.as_snapshot(rows, frozenset(trusted)), "notices": []}
        self.assertIsNotNone(snapshot.write_snapshot(self.config, session, data))

    def run_slot(self, args=None, cwd=None, slot="reviewer"):
        """slot_run.main from `cwd` (the repository root by default), as a session's shell tool starts it."""
        if args is None:
            args = ["--session", SESSION, "--base", self.base, "--head", self.head]
        out = io.BytesIO()
        old = os.getcwd()
        os.chdir(str(cwd or self.repo))
        try:
            code = slot_run.main([slot] + list(args), stdout=out)
        finally:
            os.chdir(old)
        return code, out.getvalue().decode("utf-8").splitlines()

    def recorded(self):
        with open(self.record, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def output_path(self, lines):
        prefix = "slot-run: output "
        self.assertGreaterEqual(len(lines), 2, lines)
        self.assertTrue(lines[-2].startswith(prefix), lines)
        return lines[-2][len(prefix):]


def make_link(link, target):
    """A junction on Windows, a symlink on POSIX: `link` leads to the folder `target`."""
    if NT:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link))
    return link


NODE_REVIEWER = (
    "const r = require('child_process').spawnSync('git', ['--version']);\n"
    "require('fs').writeFileSync(process.argv[2], JSON.stringify(r.error ? {error: String(r.error.code)}\n"
    "    : {rc: r.status, stdout: String(r.stdout)}));\n")
