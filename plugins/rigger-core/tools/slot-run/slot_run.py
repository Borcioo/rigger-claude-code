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
past CONSOLE_CAP bytes, or past TIMEOUT seconds, the whole process tree is stopped. The console text kept
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
    return any(review_scope._inside(candidate, base)
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
        code, out = review_scope._git(root, ["rev-parse", "--show-toplevel"])
    except review_scope.ScopeError as exc:
        raise Refusal(str(exc))
    top = out.decode("utf-8", "replace").strip()
    if code != 0 or os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(folder)):
        raise Refusal("not a git repository: %s" % _shown(start))
    return root


def kill_tree(proc, taskkill: Optional[str]) -> None:
    """Stop a process and everything it started, within KILL_TIMEOUT per step (scripts/run_tests.py)."""
    try:
        if NT:
            subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=KILL_TIMEOUT)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    proc.kill()
    try:
        proc.wait(timeout=KILL_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass


def _run(argv: List[str], root: Path, env: Dict[str, str], console_path: str, taskkill: Optional[str]):
    """Run `argv`; the reason it was stopped (None when it ran to its end) and its exit code."""
    proc = subprocess.Popen(argv, shell=False, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=not NT)
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
        # A grandchild may still hold the pipe: wait for its end only until the deadline.
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if reader.is_alive():
            reason = "timed out after %d s" % TIMEOUT
        elif over.is_set():
            reason = "more than %d bytes of console output" % CONSOLE_CAP
    if reason is not None:
        kill_tree(proc, taskkill)
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
    taskkill = review_scope.find_program("taskkill", root, (".exe",)) if NT else None
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
        reason, code = _run(command, root, strip_env(os.environ), os.path.join(work, "console.txt"), taskkill)
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
