"""Runs a command slot: the argv the session's snapshot holds, with no shell, inside its bounds.

  slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]

The slot is read only from the session's snapshot (<config dir>/rigger/state/<id>.resolved.json, the
config dir from CLAUDE_CONFIG_DIR); config is never resolved here, and a snapshot that is missing, of
another format or refused by `slots.from_snapshot` runs nothing. A project's command runs only when the
snapshot's trusted list names its slot.

The program is found by this script's own lookup, never by the operating system's, which searches the
working directory first on Windows and reads an empty or `.` PATH entry on POSIX: a bare name comes from
`review_scope.find_program` (absolute PATH entries outside the repository and the working directory), a
name with a separator must be an absolute path, and the real path of the result must lie outside both.
A batch file is refused: it always runs through cmd.exe, which re-parses its arguments. On Windows only
an `.exe` or `.com` file runs.

The command runs from the repository root, with every CLAUDE_*, HARNESS_* and GIT_* variable and
RIGGER_MODULE_DIRS removed, stdin closed, and stdout and stderr on one pipe that a thread reads and counts:
past CONSOLE_CAP bytes, or past TIMEOUT seconds, the whole process tree is stopped; so it is when the command
has exited but a process it started still holds the console after EXIT_GRACE seconds, and after every run. The console text kept
(at most CONSOLE_CAP bytes) is `console.txt` next to the output file. `{base}` and `{head}` are the commit
ids, `{out}` the output file and `{scope}` a file of `<verdict><TAB><path>` lines from
`review_scope.scope()`, all in a fresh folder in the temp directory that is kept for the caller.

The last line is `slot-run: ok <slot> <n> findings` (exit 0) or `slot-run: refused: <reason>` (exit 2); once
the folder exists, the line before it is `slot-run: output <output file>`.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(TOOLS, "assemble"))
sys.path.insert(2, os.path.join(TOOLS, "review-scope"))
sys.path.insert(3, os.path.join(TOOLS, "slot-check"))

import review_scope  # noqa: E402
import slot_check  # noqa: E402
import slots  # noqa: E402
import snapshot  # noqa: E402

TIMEOUT = 900
CONSOLE_CAP = 1_000_000
OUT_CAP = 5_000_000
KILL_TIMEOUT = 10
EXIT_GRACE = 5  # seconds a process the command started may hold the console after it exits
CHUNK = 65536
POLL = 0.05
NT = os.name == "nt"
PROGRAM_EXTS = (".exe", ".com")
BATCH_EXTS = review_scope.BATCH_EXTS
STRIPPED_PREFIXES = ("CLAUDE_", "HARNESS_", "GIT_")
STRIPPED_NAMES = ("RIGGER_MODULE_DIRS",)
SHOWN = 300
USAGE = "usage: slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]"

_FILL = re.compile(r"\{(base|head|out|scope)\}")


class Refusal(Exception):
    pass


def _shown(text) -> str:
    return slots._shown(text, SHOWN)


def strip_env(environ: Mapping[str, str]) -> Dict[str, str]:
    """`environ` without CLAUDE_*, HARNESS_*, GIT_* and RIGGER_MODULE_DIRS, names compared in upper case."""
    return {name: value for name, value in environ.items()
            if not name.upper().startswith(STRIPPED_PREFIXES) and name.upper() not in STRIPPED_NAMES}


def fill(argv: List[str], values: Dict[str, str]) -> List[str]:
    """The four placeholders replaced in one pass: a value that holds a placeholder is not filled again."""
    return [_FILL.sub(lambda match: values[match.group(1)], arg) for arg in argv]


def _inside(path: str, root: str) -> bool:
    """`path` lies in `root`, compared both as given and with links resolved on each side."""
    return any(review_scope.inside(candidate, base)
               for candidate in (os.path.abspath(path), os.path.realpath(path))
               for base in (os.path.abspath(root), os.path.realpath(root)))


def _is_batch(name: str) -> bool:
    # Windows drops trailing dots and spaces from a file name: `x.cmd ` starts x.cmd.
    return name.rstrip(". ").lower().endswith(BATCH_EXTS)


def _batch_refusal(name: str) -> Refusal:
    return Refusal("%s is a batch file; name the program it starts (for an npm tool: node <script>)" % _shown(name))


def _absolute(name: str) -> bool:
    return os.path.isabs(name) and (not NT or bool(os.path.splitdrive(name)[0]))


def _only_batch(name: str, repo: str, cwd: str) -> bool:
    """A batch file named `name` lies in a PATH entry the lookup searches."""
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isabs(entry) or _inside(entry, repo) or _inside(entry, cwd):
            continue
        if any(os.path.isfile(os.path.join(entry, name + ext)) for ext in BATCH_EXTS):
            return True
    return False


def resolve_program(name: str, repo: Path) -> str:
    """The absolute path of the program to start, from the PATH of this process; raises Refusal."""
    repo_text, cwd = str(repo), os.getcwd()
    if _is_batch(name):
        raise _batch_refusal(name)
    if "/" in name or "\\" in name:
        if not _absolute(name):
            raise Refusal("the program must be a bare name or an absolute path")
        found = os.path.abspath(name)
        if not os.path.isfile(found) or (not NT and not os.access(found, os.X_OK)):
            raise Refusal("%s is not a program file" % _shown(name))
    else:
        exts = PROGRAM_EXTS if NT and not name.lower().endswith(PROGRAM_EXTS) else ("",)
        try:
            found = review_scope.find_program(name, repo, exts)
        except review_scope.ScopeError:
            if _only_batch(name, repo_text, cwd):
                raise _batch_refusal(name)
            raise Refusal("%s not found on PATH (entries inside the repository are not searched)" % _shown(name))
    real = os.path.realpath(found)
    if _inside(found, repo_text) or _inside(found, cwd):
        raise Refusal("%s resolves inside the repository" % _shown(name))
    if NT and not all(path.lower().endswith(PROGRAM_EXTS) for path in (found, real)):
        raise Refusal("%s is not an .exe or .com program" % _shown(name))
    return found


def _repository_root(start: str) -> Path:
    """The nearest folder at or above `start` that holds `.git`, confirmed by git; found before any lookup,
    so the inside check covers the whole repository even when started from a subfolder."""
    folder = os.path.abspath(start)
    while not os.path.exists(os.path.join(folder, ".git")):
        parent = os.path.dirname(folder)
        if parent == folder:
            raise Refusal("not a git repository: %s" % _shown(start))
        folder = parent
    root = Path(folder)
    try:
        code, out = review_scope.git(root, ["rev-parse", "--show-toplevel"])
    except review_scope.ScopeError as exc:
        raise Refusal(str(exc))
    top = out.decode("utf-8", "replace").strip()
    if code != 0 or os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(folder)):
        raise Refusal("not a git repository: %s" % _shown(start))
    return root


class _Tree:
    """The command and every process it starts. On Windows a job object holds them from the first
    instruction (the command starts suspended and resumes inside the job), so the tree is reachable after
    its first process has exited, which `taskkill /T <pid>` is not. On POSIX it is the command's own
    process group (start_new_session)."""

    def __init__(self, argv: List[str], root: Path, env: Dict[str, str]):
        self.job = None
        if not NT:
            self.proc = subprocess.Popen(argv, shell=False, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            return
        self.job = _new_job()
        self.proc = subprocess.Popen(argv, shell=False, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=_CREATE_SUSPENDED)
        if not _enter_job(self.job, self.proc.pid):
            self.proc.kill()
            self.proc.wait(timeout=KILL_TIMEOUT)
            self.close()
            raise Refusal("the command could not be placed in a job object")

    def kill(self) -> None:
        """Stop every process of the tree, within KILL_TIMEOUT."""
        try:
            if NT:
                _KERNEL32.TerminateJobObject(self.job, 1)
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        self.proc.kill()
        try:
            self.proc.wait(timeout=KILL_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass

    def close(self) -> None:
        if self.job is not None:
            _KERNEL32.CloseHandle(self.job)  # kill-on-close: nothing of the tree outlives the handle
            self.job = None


_CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x00002000
_EXTENDED_LIMITS = 9  # JobObjectExtendedLimitInformation
_PROCESS_RIGHTS = 0x0001 | 0x0100 | 0x0800  # terminate, set quota, suspend/resume
_KERNEL32 = None
_NTDLL = None
if NT:
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _NTDLL = ctypes.WinDLL("ntdll")
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _KERNEL32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _KERNEL32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _KERNEL32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _NTDLL.NtResumeProcess.argtypes = (wintypes.HANDLE,)


def _new_job():
    job = _KERNEL32.CreateJobObjectW(None, None)
    if not job:
        raise Refusal("no job object for the command")
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not _KERNEL32.SetInformationJobObject(job, _EXTENDED_LIMITS, ctypes.byref(limits), ctypes.sizeof(limits)):
        _KERNEL32.CloseHandle(job)
        raise Refusal("no job object for the command")
    return job


def _enter_job(job, pid: int) -> bool:
    """Put the suspended process `pid` in `job`, then let it run; False when either step fails."""
    handle = _KERNEL32.OpenProcess(_PROCESS_RIGHTS, False, pid)
    if not handle:
        return False
    try:
        if not _KERNEL32.AssignProcessToJobObject(job, handle):
            return False
        return _NTDLL.NtResumeProcess(handle) == 0
    finally:
        _KERNEL32.CloseHandle(handle)


def _run(argv: List[str], root: Path, env: Dict[str, str], console_path: str):
    """Run `argv`; the reason it was stopped (None when it ran to its end) and its exit code. Whatever the
    outcome, no process of the command's tree is left when this returns."""
    tree = _Tree(argv, root, env)
    try:
        return _watch(tree, console_path)
    finally:
        tree.close()


def _watch(tree: "_Tree", console_path: str):
    proc = tree.proc
    over = threading.Event()

    def read():
        count = 0
        with open(console_path, "wb") as sink:
            while True:
                data = proc.stdout.read1(CHUNK)
                if not data:
                    return
                if count < CONSOLE_CAP:
                    sink.write(data[:CONSOLE_CAP - count])
                count += len(data)
                if count > CONSOLE_CAP:
                    over.set()
                    return  # the child now blocks on a full pipe until the kill

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + TIMEOUT
    reason = None
    while reason is None:
        if over.is_set():
            reason = "more than %d bytes of console output" % CONSOLE_CAP
            break
        try:
            proc.wait(timeout=POLL)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                reason = "timed out after %d s" % TIMEOUT
    if reason is None:
        # The command has exited; a process it started may still hold the pipe. Wait EXIT_GRACE for it, never
        # past the deadline.
        reader.join(timeout=max(0.0, min(EXIT_GRACE, deadline - time.monotonic())))
        if reader.is_alive():
            reason = "child process left running" if time.monotonic() < deadline else "timed out after %d s" % TIMEOUT
        elif over.is_set():
            reason = "more than %d bytes of console output" % CONSOLE_CAP
    tree.kill()  # also after a clean exit: a process the command left behind does not outlive the run
    reader.join(timeout=KILL_TIMEOUT)
    if not reader.is_alive():
        proc.stdout.close()
    return reason, proc.returncode


def _parse(argv: List[str]):
    if not argv or argv[0].startswith("-"):
        return None
    values = {}  # type: dict
    index = 1
    while index < len(argv):
        name = argv[index]
        if name not in ("--session", "--base", "--head", "--repo") or name in values or index + 1 >= len(argv):
            return None
        values[name] = argv[index + 1]
        index += 2
    if "--session" not in values or "--base" not in values:
        return None
    return argv[0], values["--session"], values["--base"], values.get("--head", "HEAD"), values.get("--repo")


def _row(slot: str, session: str):
    value = os.environ.get("CLAUDE_CONFIG_DIR", "")
    config_dir = Path(value) if value else Path.home() / ".claude"
    if snapshot.snapshot_path(config_dir, session) is None:
        raise Refusal("session id is not usable")
    data = snapshot.read_snapshot(config_dir, session)
    table = slots.from_snapshot(data["slots"]) if data is not None else None
    if table is None:
        raise Refusal("no snapshot for this session; start a new session")
    rows, trusted = table
    row = next((item for item in rows if item.path == slot), None)
    if row is None or not row.filled:
        raise Refusal("slot %s is empty" % _shown(slot))
    if row.kind != "command":
        raise Refusal("slot %s is %s; dispatch its target instead" % (slot, row.kind))
    if row.source == "project" and slot not in trusted:
        raise Refusal("the project's command for %s is not trusted" % slot)
    return row


def _check_output(slot: str, out_path: str, root: Path, base_id: str, head_id: str) -> int:
    if not os.path.isfile(out_path):
        raise Refusal("the command wrote no output file")
    with open(out_path, "rb") as handle:
        data = handle.read(OUT_CAP + 1)
    if len(data) > OUT_CAP:
        raise Refusal("the output file is over %d bytes" % OUT_CAP)
    verdict = slot_check.check(slot, data.decode("utf-8", "replace"), repo=root, base=base_id, head=head_id)
    if not verdict.ok:
        raise Refusal("%s: %s" % (verdict.rule, " ".join(verdict.reason.splitlines())))
    return verdict.findings if verdict.findings is not None else 0


def _main(argv: List[str], emit) -> int:
    parsed = _parse(argv)
    if parsed is None:
        raise Refusal(USAGE)
    slot, session, base, head, repo_arg = parsed
    row = _row(slot, session)
    root = _repository_root(repo_arg if repo_arg is not None else os.getcwd())
    program = resolve_program(row.target[0], root)
    try:
        base_id = review_scope.resolve_commit(root, base)
        head_id = review_scope.resolve_commit(root, head)
        rows = review_scope.scope(root, base_id, head_id)
    except review_scope.ScopeError as exc:
        raise Refusal(str(exc))
    if not rows:
        raise Refusal("nothing to review between %s and %s" % (_shown(base), _shown(head)))
    work = tempfile.mkdtemp(prefix="rigger-slot-run-")
    out_path = os.path.join(work, "out.json")
    scope_path = os.path.join(work, "scope.txt")
    emit("slot-run: output %s\n" % out_path)
    with open(scope_path, "wb") as handle:
        handle.write("".join("%s\t%s\n" % (verdict, path) for path, verdict in rows).encode("utf-8"))
    values = {"base": base_id, "head": head_id, "out": out_path, "scope": scope_path}
    command = [program] + fill(row.target[1:], values)
    try:
        reason, code = _run(command, root, strip_env(os.environ), os.path.join(work, "console.txt"))
    except OSError as exc:
        raise Refusal("the command could not start: %s" % _shown(exc))
    if reason is not None:
        raise Refusal(reason)
    if code != 0:
        raise Refusal("the command exited %d" % code)
    findings = _check_output(slot, out_path, root, base_id, head_id)
    emit("slot-run: ok %s %d findings\n" % (slot, findings))
    return 0


def main(argv: Optional[List[str]] = None, stdout=None) -> int:
    sink = stdout if stdout is not None else sys.stdout.buffer
    held = []  # the output line is printed with the last one, so a refusal never splits them

    def emit(text):
        held.append(text)

    try:
        code = _main(list(sys.argv[1:] if argv is None else argv), emit)
    except Refusal as exc:
        held.append("slot-run: refused: %s\n" % exc)
        code = 2
    except Exception as exc:  # any failure is a refusal, never a traceback
        held.append("slot-run: refused: internal: %s\n" % _shown("%s: %s" % (type(exc).__name__, exc)))
        code = 2
    sink.write("".join(held).encode("utf-8"))
    sink.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
