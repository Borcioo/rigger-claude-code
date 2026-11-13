"""Run the whole gate in one call, so it can run in the background and be read instead of rerun.

Usage: python -I scripts/gate.py [--denylist FILE] [--distro NAME] [--out DIR]
       python -I scripts/gate.py --release --denylist FILE [...]
       python -I scripts/gate.py --verify STATUS_FILE

Every input is optional. With no --denylist the sweep runs with --no-denylist and the status says
`denylist: none`. With no `claude` on PATH the suite runs with --no-claude and the status says so.
On Windows with no `wsl` on PATH the Linux leg prints `SKIP wsl: wsl not on PATH`; the pass line
counts skipped legs (`, skipped N`). --release requires --denylist and adds the leg
denylist-control: the sweep must find exactly one finding in a file holding one denylist term, read
from the denylist at run time (a missing, empty or unplantable denylist exits 2).

tests, control, sweep and its control run at once; then, on Windows, the Linux suite under WSL
alone, because two suites at once are two heavy jobs. A failed leg stops nothing. A PASS
line ends with `, load-only N` when the suite's `# files` line reports N files that passed only on
their rerun alone (their `LOAD-ONLY` lines are in the leg's log). Each
leg's whole output goes to <out>/<leg>.log. <out>/status.txt and stdout get a line as each leg
starts and ends, and last `# gate passed, <seconds> s, tree <id>` or `# gate failed: <legs>`; exit 0
only on the first. The first line is `TREE <id>`, the git tree id of the working tree's content (the
id `git rev-parse HEAD^{tree}` gives once that content is committed; ignored files stay out). It is
computed again after the last leg; a different id fails the gate (`tree changed during the gate`).
The Python running this file runs the legs. A leg still running after LEG_TIMEOUT seconds fails,
and everything it started is killed.

--denylist    the sweep's private denylist (optional).
--release     the release gate: --denylist required, plus the denylist control.
--distro      WSL distribution (default: Ubuntu).
--out         folder for the logs (default: gate/ beside the --agent log).
--verify      run no leg: exit 0 only when the last line of STATUS_FILE is a pass line whose tree id is
              the working tree's current one; else exit 1 with one reason line.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PureWindowsPath
from typing import List, NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402

STATUS = "status.txt"
PLANTED = "planted.md"
PLANTED_TERM = "planted-term.md"
NO_WSL = "wsl not on PATH"
NO_CLAUDE = "claude: not on PATH, the suite runs with --no-claude"
LEG_TIMEOUT = 1800
DEFAULT_DISTRO = "Ubuntu"
# The `# files` line of run_tests.py ends with this when files passed only on their rerun alone.
LOAD_ONLY_RE = re.compile(r"^# files .*, load-only (\d+)$", re.M)
# The tests run_tests.py executed, from its last `# files` line (after the reruns alone).
RUN_TESTS_COUNT = r"^# files \d+, tests (\d+), "
# A run_tests.py file that ran no test of its own and printed why.
SKIP_FILE_RE = re.compile(r"^SKIP \S", re.M)
TREE_INDEX = "gate-tree.index"
TREE_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
GIT_TIMEOUT = 300


class Leg(NamedTuple):
    name: str
    command: List[str]
    expected: int = 0
    marker: Optional[str] = None
    # A regex whose group is what the leg checked; its last match must read at least 1.
    count_re: Optional[str] = None
    skip_files: bool = False
    # Why the leg does not run: it prints `SKIP <name>: <reason>` and counts on the pass line.
    skip: Optional[str] = None
    # The count the leg must read exactly, instead of at least 1.
    exact: Optional[int] = None


class Status:
    def __init__(self, path):
        self.handle = open(path, "w", encoding="utf-8", newline="\n")
        self.lock = threading.Lock()

    def tell(self, line):
        with self.lock:
            print(line, flush=True)
            self.handle.write(line + "\n")
            self.handle.flush()


def last_count(pattern, text):
    """The number in the last match of `pattern`, or None when nothing matches."""
    found = re.findall(pattern, text, re.M)
    return int(found[-1]) if found else None


def judge(leg, returncode, seconds, log_path):
    """None when the leg ended as it should, else why not."""
    if returncode != leg.expected:
        return "exit %d, expected %d, %.1f s, see %s" % (returncode, leg.expected, seconds, log_path)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if leg.marker is not None and leg.marker not in text:
        return "no %r in its output, see %s" % (leg.marker, log_path)
    if leg.count_re is not None:
        count = last_count(leg.count_re, text)
        if count is None:
            return "printed no count matching %r, see %s" % (leg.count_re, log_path)
        if leg.exact is not None and count != leg.exact:
            return "count %d from %r, expected %d, see %s" % (count, leg.count_re, leg.exact, log_path)
        if count < 1:
            return "count 0 from %r, see %s" % (leg.count_re, log_path)
    return None


def pass_details(leg, text):
    """What a PASS line adds: the leg's count, its skipped files, its files passed only alone."""
    details = []
    if leg.count_re is not None:
        details.append("count %d" % last_count(leg.count_re, text))
    skipped = len(SKIP_FILE_RE.findall(text)) if leg.skip_files else 0
    if skipped:
        details.append("skipped files %d" % skipped)
    loaded = LOAD_ONLY_RE.findall(text)
    if loaded:
        details.append("load-only " + loaded[-1])
    return "".join(", " + detail for detail in details)


class TreeError(Exception):
    """The tree id could not be computed; the text is the reason line."""


def git_step(root, env, *args):
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except subprocess.SubprocessError as error:
        raise TreeError("cannot compute tree id: git %s: %s" % (args[0], error))
    if done.returncode != 0:
        detail = " ".join((done.stderr or done.stdout).split())
        raise TreeError("cannot compute tree id: git %s exit %d: %s" % (args[0], done.returncode, detail))
    return done.stdout.strip()


def tree_id(root, index_dir, skip=None):
    """The git tree id of `root`'s working tree content, through a temporary index in `index_dir`.

    The real index is never used. Ignored files stay out, tracked ones stay in. `skip` (a folder the
    gate writes into) stays out when it is inside `root`.
    """
    index = Path(index_dir) / TREE_INDEX
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    add = ["add", "-A"]
    if skip is not None:
        try:
            relative = Path(skip).resolve().relative_to(Path(root).resolve())
        except ValueError:
            relative = None
        if relative is not None:
            add += ["--", ".", ":(exclude)" + relative.as_posix()]
    try:
        index.unlink(missing_ok=True)
        git_step(root, env, "read-tree", "HEAD")
        git_step(root, env, *add)
        found = git_step(root, env, "write-tree")
    except OSError as error:
        raise TreeError("cannot compute tree id: %s" % error)
    finally:
        try:
            index.unlink(missing_ok=True)
        except OSError:
            pass
    if not TREE_RE.match(found):
        raise TreeError("cannot compute tree id: git write-tree printed %r" % found)
    return found


def run_leg(leg, root, out, status):
    if leg.skip is not None:
        status.tell("SKIP %s: %s" % (leg.name, leg.skip))
        return None
    status.tell("START %s" % leg.name)
    log_path = out / (leg.name + ".log")
    started = time.perf_counter()
    with open(log_path, "wb") as log:
        try:
            proc = subprocess.Popen(
                leg.command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            status.tell("FAIL %s: cannot start: %s" % (leg.name, error))
            return leg.name
        run_tests.RUNNING.add(proc)
        try:
            returncode = proc.wait(timeout=LEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            run_tests.stop_tree(proc)
            status.tell("FAIL %s: timeout after %d s, see %s" % (leg.name, LEG_TIMEOUT, log_path))
            return leg.name
        finally:
            run_tests.RUNNING.discard(proc)
    seconds = time.perf_counter() - started
    problem = judge(leg, returncode, seconds, log_path)
    if problem:
        status.tell("FAIL %s: %s" % (leg.name, problem))
        return leg.name
    text = log_path.read_text(encoding="utf-8", errors="replace")
    status.tell("PASS %s %.1f s%s" % (leg.name, seconds, pass_details(leg, text)))
    return None


def collect(futures):
    return [future.result() for future in futures]


def run_stage(legs, root, out, status):
    with ThreadPoolExecutor(max_workers=len(legs)) as pool:
        futures = [pool.submit(run_leg, leg, root, out, status) for leg in legs]
        try:
            return collect(futures)
        except BaseException:
            # A worker thread never sees Ctrl-C, so its child has to be stopped from here.
            pool.shutdown(wait=False, cancel_futures=True)
            run_tests.stop_running(futures)
            raise


def run_gate(stages, root, out, notes=()):
    """True only when at least one leg ran and every leg ended as it should.

    Each of `notes` is a status line printed after the TREE line.
    """
    status = Status(out / STATUS)
    skipped = sum(1 for stage in stages for leg in stage if leg.skip is not None)
    ran = sum(1 for stage in stages for leg in stage if leg.skip is None)
    started = time.perf_counter()
    try:
        try:
            before = tree_id(root, out, skip=out)
        except TreeError as error:
            status.tell(str(error))
            status.tell("# gate failed: tree, %.1f s" % (time.perf_counter() - started))
            return False
        status.tell("TREE " + before)
        for note in notes:
            status.tell(note)
        failed =[name for stage in stages for name in run_stage(stage, root, out, status) if name]
        try:
            after = tree_id(root, out, skip=out)
        except TreeError as error:
            status.tell(str(error))
            failed.append("tree")
        else:
            if after != before:
                status.tell("tree changed during the gate: %s -> %s" % (before, after))
                failed.append("tree")
        seconds = time.perf_counter() - started
        passed = ran > 0 and not failed
        if passed:
            counted = ", skipped %d" % skipped if skipped else ""
            status.tell("# gate passed, %.1f s%s, tree %s" % (seconds, counted, before))
        else:
            status.tell("# gate failed: %s, %.1f s" % (", ".join(failed) or "no leg ran", seconds))
        return passed
    finally:
        status.handle.close()


def verify(status_file, root):
    """(exit code, the one line to print) for `--verify`: is the file's pass line about this tree."""
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return 1, "cannot read %s" % status_file
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not re.match(r"^# gate passed(,|$)", lines[-1]):
        return 1, "no pass line in %s" % status_file
    found = re.search(r", tree ([0-9a-f]{40}|[0-9a-f]{64})$", lines[-1])
    if not found:
        return 1, "pass line has no tree id"
    work = tempfile.mkdtemp(prefix="gate-verify-")
    try:
        current = tree_id(root, work)
    except TreeError as error:
        return 1, str(error)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if current != found.group(1):
        return 1, "gate ran on tree %s, current tree is %s" % (found.group(1), current)
    return 0, "gate result matches: tree %s" % current


def plant(out):
    """A file the sweep must report. Built here: a literal path in this file would be a finding."""
    path = out / PLANTED
    path.write_text("x " + "C" + ":" + "/planted/path\n", encoding="utf-8", newline="\n")
    return path


def wsl_path(root):
    path = PureWindowsPath(root)
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


class ReleaseError(Exception):
    """The release denylist cannot be used; the text is the reason line."""


def release_term(denylist):
    """The first denylist line that the sweep finds exactly once when planted on its own.

    Read at run time, so no term is written in this repository. Raises ReleaseError when the
    denylist is missing, unreadable, empty or has no such line.
    """
    import sweep

    try:
        terms = sweep.load_denylist(denylist)
        text = sweep.read_utf8(denylist, "denylist")
    except sweep.SweepError as error:
        raise ReleaseError(str(error))
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and len(sweep.scan_text(planted_text(line), terms)) == 1:
            return line
    raise ReleaseError("denylist has no term to plant (none is found exactly once as written): %s" % denylist)


def planted_text(term):
    return "x " + term + "\n"


def plant_term(out, term):
    """The file the release's denylist control must find exactly once."""
    path = out / PLANTED_TERM
    path.write_text(planted_text(term), encoding="utf-8", newline="\n")
    return path


def build_stages(args, root, out, release=None, claude=True):
    """The legs; `release` is the denylist term the release plants (see release_term), or None.

    `claude` False runs the suite with --no-claude (no `claude` on PATH).
    """
    python = [sys.executable, "-I", "-X", "utf8"]
    run = python + ["scripts/run_tests.py"]
    denylist = ["--denylist", args.denylist] if args.denylist else ["--no-denylist"]
    sweep = python + ["scripts/sweep.py"] + denylist
    caught = "# control: caught"
    findings = r"^# files \d+, findings (\d+)$"
    first = [
        Leg("tests", run + ([] if claude else ["--no-claude"]), count_re=RUN_TESTS_COUNT, skip_files=True),
        Leg("control", run + ["--control"], marker=caught, count_re=r"^FAIL tests/control/failing_control\.py: (\d+) tests"),
        Leg("sweep", sweep, count_re=r"^# files (\d+), findings \d+$"),
        Leg("sweep-control", sweep + [str(plant(out))], expected=1, marker=PLANTED, count_re=findings),
    ]
    if release is not None:
        planted = plant_term(out, release)
        first.append(Leg("denylist-control", sweep + [str(planted)], expected=1, marker=PLANTED_TERM, count_re=findings, exact=1))
    stages = [first]
    if os.name == "nt":
        if shutil.which("wsl") is None:
            stages.append([Leg("wsl", [], skip=NO_WSL)])
        else:
            command = ["wsl", "-d", args.distro, "--cd", wsl_path(root), "-e", "python3", "-I", "scripts/run_tests.py"]
            stages.append([Leg("wsl", command + ["--no-claude"], count_re=RUN_TESTS_COUNT, skip_files=True)])
    return stages


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description="Run the whole gate; read its status file instead of rerunning.")
    parser.add_argument("--denylist", help="the sweep's denylist (optional; --release requires it)")
    parser.add_argument("--release", action="store_true", help="also prove the denylist catches a planted term")
    parser.add_argument("--distro", default=DEFAULT_DISTRO, help="WSL distribution")
    parser.add_argument("--out", default=None, help="folder for the logs")
    parser.add_argument("--verify", metavar="STATUS_FILE", help="run no leg; check a status file against the current tree")
    args = parser.parse_args(argv)
    if args.verify is not None:
        code, line = verify(args.verify, root)
        print(line, flush=True)
        return code
    term = None
    if args.release:
        if args.denylist is None:
            parser.error("--release requires --denylist")
        try:
            term = release_term(args.denylist)
        except ReleaseError as error:
            parser.error("--release: %s" % error)
    notes = [] if args.denylist else ["denylist: none"]
    claude = shutil.which("claude") is not None
    if not claude:
        notes.append(NO_CLAUDE)
    out = Path(args.out) if args.out else run_tests.log_path(root).with_name("gate")
    out.mkdir(parents=True, exist_ok=True)
    print("# status %s" % (out / STATUS), flush=True)
    return 0 if run_gate(build_stages(args, root, out, release=term, claude=claude), root, out, notes=notes) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
