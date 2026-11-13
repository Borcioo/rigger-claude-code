"""Run every test file of the repo, each in its own isolated interpreter.

Usage: python -I scripts/run_tests.py [--no-claude] [--min-files N] [--jobs N] [--agent]
                                      [--fix-round [--base REV]] [--control]

Discovers tests/test_*.py, plugins/*/tests/test_*.py and plugins/*/tools/*/tests/test_*.py and
runs each as `<python> -I -X utf8 <file>` from the repo root, in an environment without CLAUDE_*
and HARNESS_* variables. A file passes only when it exits 0, prints a test summary (unittest's
"Ran N tests", or a custom runner's last "#" line with "N passed") and that summary reports no
failure (a custom runner's "M failed" on the same line). A custom runner's "#" line with
"skipped" and a reason after a colon counts as skipped checks (its number, else 1); a custom runner
with no count line is SKIP when it printed such a line, and one that ran 0 checks without it is FAIL.
Exit 1 on any failure, when no test was executed, or when fewer files are found than
scripts/run_tests.min says. The last line's "tests" counts executed tests: skipped ones are left out.

Also discovers *.tests.ps1 in the same test folders and in plugins/*/tools/*/, and runs each with
`pwsh -NoProfile -NonInteractive -File`. Such a file passes when it exits 0 and prints
"N passed, 0 failed" with N > 0. Without pwsh each one is reported as SKIP and counted as skipped.
A unittest file whose every test was skipped is reported as SKIP too, with each skip's reason.

A file still running after TIMEOUT seconds (its TIMEOUTS entry, where it has one) fails, and everything it
started is killed.

Files run in --jobs workers, then each SERIAL file alone. Results print as files end, each with its
seconds; the slowest SLOWEST files and the wall time print before the last line.

Then each failed file is rerun once, alone, one at a time (not in --fix-round, and not when more than
RERUN_LIMIT files failed). One that passes alone counts as passed: a `LOAD-ONLY <file>` line names it
and the `# files` line ends with `, load-only N`. One that fails again stays failed, both outputs shown.

--no-claude   pass HARNESS_NO_CLAUDE=1 to the tests (machines without the claude CLI; the tests
              that need it are skipped and listed).
--min-files   minimum number of test files, at least 1 (default: scripts/run_tests.min).
--jobs        files run at once, at least 1 (default: JOBS, at most the logical core count).
--agent       print only failed files, their failed tests and the totals; every file's whole
              output and the slowest list go to the log the first line names, under the temp
              folder, one per repo root, replaced by each run. The limit is still per file: a
              test inside a file has none of its own. A log that cannot be written stops the run.
--fix-round   --agent over the files a change can affect (scripts/select_tests.py), the files the
              last --agent run left failed first, stopping at the first failure. Never a gate:
              the full suite stays mandatory before merge, and --min-files does not apply.
--base        the change starts at the merge base with this revision (default: main).
--control     run only the planted failing file tests/control/failing_control.py; exit 0 only
              when it is reported failed.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = (
    "tests/test_*.py",
    "plugins/*/tests/test_*.py",
    "plugins/*/tools/*/tests/test_*.py",
    "tests/*.tests.ps1",
    "plugins/*/tests/*.tests.ps1",
    "plugins/*/tools/*/tests/*.tests.ps1",
    "plugins/*/tools/*/*.tests.ps1",
)
CONTROL = "tests/control/failing_control.py"
MIN_FILE = "scripts/run_tests.min"
TIMEOUT = 120
# A file that spawns hundreds of git and shell processes by design has its own limit: its quiet run takes 60-90 s
# and a loaded gate nearly doubles that. Add a file here only with the spawn count that shows it.
TIMEOUTS = {"plugins/rigger-windows/tools/shell-guard/tests/test_commit_gate.py": 300}
KILL_TIMEOUT = 10
# At most this many failed files are rerun alone; more is a real break, not load.
RERUN_LIMIT = 5
JOBS = 4
SLOWEST = 10
FAILED_TESTS_SHOWN = 5
LOG_FOLDER = "harness-tests"
# Wall-time budgets, live machine readings, or hundreds of process trees close to TIMEOUT: another
# file's load fails them.
SERIAL = (
    "plugins/rigger-rules/tests/test_reinject.py",
    "plugins/rigger-windows/tools/mem-check/mem-check.tests.ps1",
    "plugins/rigger-windows/tools/shell-guard/tests/test_rules.py",
    "tests/test_repo.py",
    "tests/test_sweep.py",
)
DROPPED_PREFIXES = ("CLAUDE_", "HARNESS_")

RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
RESULT_RE = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?[ \t]*$", re.M)
SKIP_RE = re.compile(r"^(\S+) \(([^)\n]*)\)(?:\n[^\n]*)? \.\.\. skipped ('.*'|\".*\")[ \t]*$", re.M)
CUSTOM_COUNT_RE = re.compile(r"(\d+) (?:checks )?passed")
CUSTOM_FAILED_RE = re.compile(r"(\d+) (?:checks )?failed")
# `# skipped 2 checks: <reason>`, `# tool: skipped on linux: <reason>`
CUSTOM_SKIP_RE = re.compile(r"^#.*?\bskipped\b(?: (\d+)\b)?[^:]*:[ \t]*\S")
PS1_SUMMARY_RE = re.compile(r"^.*?(\d+) passed, (\d+) failed\.?[ \t]*$", re.M)
FAILED_TEST_RE = re.compile(r"^(?:FAIL|ERROR): .*$", re.M)

RUNNING = set()


class FirstFailure(Exception):
    """Ends a fix round: run_files stops what its workers still run."""


def clean_env(environ, no_claude=False):
    env = {key: value for key, value in environ.items() if not key.upper().startswith(DROPPED_PREFIXES)}
    if no_claude:
        env["HARNESS_NO_CLAUDE"] = "1"
    return env


def discover(root):
    files = []
    for pattern in PATTERNS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in files:
                files.append(path)
    return files


def read_min(root):
    try:
        value = int((root / MIN_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def evaluate(returncode, output):
    """Judge one file's run: it fails on its exit code or on the failures its summary reports."""
    output = output.replace("\r\n", "\n")
    result = {
        "ok": False,
        "exit": returncode,
        "tests": 0,
        "executed": 0,
        "failed": 0,
        "reported_failures": 0,
        "skipped": 0,
        "skip_names": [],
        "unnamed_skips": 0,
        "file_skipped": False,
        "summary": "",
        "note": "",
        "seconds": 0.0,
    }
    ran = RAN_RE.findall(output)
    if ran:
        result["tests"] = int(ran[-1])
        outcomes = RESULT_RE.findall(output)
        counts = {}
        if outcomes:
            for part in outcomes[-1][1].split(","):
                key, _, value = part.strip().partition("=")
                if value.isdigit():
                    counts[key] = int(value)
        result["reported_failures"] = (
            counts.get("failures", 0) + counts.get("errors", 0) + counts.get("unexpected successes", 0)
        )
        result["failed"] = result["reported_failures"]
        result["skipped"] = counts.get("skipped", 0)
        result["skip_names"] = [
            "%s (%s): %s" % (name, where, reason[1:-1]) for name, where, reason in SKIP_RE.findall(output)
        ]
        result["unnamed_skips"] = max(0, result["skipped"] - len(result["skip_names"]))
        result["executed"] = max(0, result["tests"] - result["skipped"])
        result["summary"] = "%d tests" % result["tests"]
        if result["tests"] == 0:
            result["note"] = "ran 0 tests"
        # Every test skipped: the file ran none of its own, so it reads SKIP, never PASS.
        result["file_skipped"] = result["tests"] > 0 and result["executed"] == 0
    else:
        marks = [line.strip() for line in output.splitlines() if line.startswith("#")]
        skips = [(line, CUSTOM_SKIP_RE.match(line)) for line in marks]
        skips = [(line, match) for line, match in skips if match]
        result["skipped"] = sum(int(match.group(1)) if match.group(1) else 1 for _, match in skips)
        if marks:
            result["summary"] = marks[-1]
            counted = [(line, CUSTOM_COUNT_RE.search(line)) for line in marks]
            counted = [(line, match) for line, match in counted if match]
            if counted:
                result["summary"] = counted[-1][0]
                result["tests"] = result["executed"] = int(counted[-1][1].group(1))
                failed = CUSTOM_FAILED_RE.search(counted[-1][0])
                result["reported_failures"] = result["failed"] = int(failed.group(1)) if failed else 0
            if not counted and skips:
                result["file_skipped"] = True
                result["summary"] = "; ".join(line for line, _ in skips)
            elif result["tests"] == 0:
                result["note"] = "ran 0 checks and printed no skip reason"
            else:
                result["skip_names"] = [re.sub(r"^skipped\s+", "", line.lstrip("# ")) for line, _ in skips]
        else:
            result["note"] = "no test summary in output"
    result["ok"] = (
        returncode == 0 and bool(result["summary"]) and not result["note"] and not result["reported_failures"]
    )
    if returncode != 0 and not result["note"]:
        result["note"] = "exit %d" % returncode
    if not result["ok"]:
        result["failed"] = max(result["failed"], 1)
    return result


def evaluate_ps1(returncode, output):
    """Judge one PowerShell test file by its exit code and its last "N passed, M failed" line."""
    result = evaluate(returncode, "")
    result["note"] = ""
    summaries = list(PS1_SUMMARY_RE.finditer(output.replace("\r\n", "\n")))
    if not summaries:
        result["note"] = "no test summary in output"
        return result
    last = summaries[-1]
    result["tests"], reported = int(last.group(1)), int(last.group(2))
    result["executed"] = result["tests"]
    result["reported_failures"] = reported
    result["summary"] = last.group(0).strip()
    result["ok"] = returncode == 0 and result["tests"] > 0 and reported == 0
    if result["tests"] == 0:
        result["note"] = "ran 0 tests"
    elif returncode != 0:
        result["note"] = "exit %d" % returncode
    result["failed"] = 0 if result["ok"] else max(reported, 1)
    return result


def skipped_ps1():
    result = evaluate(0, "# skipped 1 PowerShell test file: pwsh not found\n")
    result["output"] = ""
    return result


def find_pwsh():
    return shutil.which("pwsh")


def kill_tree(proc):
    """Stop a process and everything it started, within KILL_TIMEOUT per step."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=KILL_TIMEOUT,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    proc.kill()
    proc.wait(timeout=KILL_TIMEOUT)


def stop_tree(proc):
    """kill_tree that reports a process surviving the kill instead of raising: False when it did."""
    try:
        kill_tree(proc)
    except subprocess.TimeoutExpired:
        return False
    return True


def time_limit(path, root):
    """TIMEOUT, or the file's own limit from TIMEOUTS."""
    try:
        return TIMEOUTS.get(path.relative_to(root).as_posix(), TIMEOUT)
    except ValueError:
        return TIMEOUT


def run_file(path, env, root=ROOT, timeout=None):
    if timeout is None:
        timeout = time_limit(path, root)
    is_ps1 = path.suffix == ".ps1"
    if is_ps1:
        pwsh = find_pwsh()
        if pwsh is None:
            return skipped_ps1()
        command = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(path)]
    else:
        command = [sys.executable, "-I", "-X", "utf8", str(path)]
    judge = evaluate_ps1 if is_ps1 else evaluate
    started = time.perf_counter()
    # A file, not a pipe: a grandchild holding a pipe open would outlast the timeout.
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            command,
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        RUNNING.add(proc)
        survived = False
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            returncode = None
            survived = not stop_tree(proc)
        except KeyboardInterrupt:
            # The child runs in its own session or group, so Ctrl-C does not reach it.
            stop_tree(proc)
            raise
        finally:
            RUNNING.discard(proc)
        log.seek(0)
        output = log.read().decode("utf-8", "replace")
    result = judge(1 if returncode is None else returncode, output)
    if returncode is None:
        result["note"] = "timeout after %d s" % timeout
        if survived:
            result["note"] += "; the process survived the kill"
        result["failed"] = max(result["failed"], 1)
    result["output"] = output
    result["seconds"] = time.perf_counter() - started
    return result


def stop_running(futures):
    """Stop every running file until no worker is left: one may start a child after the first pass."""
    while not all(future.done() for future in futures):
        for proc in list(RUNNING):
            stop_tree(proc)
        time.sleep(0.1)


def run_files(files, env, root, jobs, on_result):
    """Run `files` in `jobs` workers, then each SERIAL file alone; on_result gets each as it ends."""
    alone = [path for path in files if path.relative_to(root).as_posix() in SERIAL]
    shared = [path for path in files if path not in alone]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_file, path, env, root=root): path for path in shared}
        try:
            for future in as_completed(futures):
                on_result(futures[future], future.result())
        except BaseException:
            # A worker thread never sees Ctrl-C, so its child has to be stopped from here.
            pool.shutdown(wait=False, cancel_futures=True)
            stop_running(futures)
            raise
    for path in alone:
        on_result(path, run_file(path, env, root=root))


def status_line(rel, result):
    status = ("SKIP" if result["file_skipped"] else "PASS") if result["ok"] else "FAIL"
    details = [result["summary"]] if result["summary"] else []
    if result["skipped"]:
        details.append("%d skipped" % result["skipped"])
    if result["note"]:
        details.append(result["note"])
    return "%s %s: %s [%.1f s]" % (status, rel, ", ".join(details), result["seconds"])


def report(rel, result):
    print(status_line(rel, result))
    for name in result["skip_names"]:
        print("  skipped %s" % name)
    if result["unnamed_skips"] > 0:
        print("  skipped %d more (names not found in the output)" % result["unnamed_skips"])
    if not result["ok"]:
        for line in result["output"].rstrip().splitlines()[-40:]:
            print("  | " + line)


def report_agent(rel, result, log):
    """Failures in short on stdout, the whole file in the log, flushed so a killed run keeps it."""
    if not result["ok"]:
        print(status_line(rel, result))
        for line in FAILED_TEST_RE.findall(result["output"].replace("\r\n", "\n"))[:FAILED_TESTS_SHOWN]:
            print("  " + line)
    with contextlib.redirect_stdout(log):
        report(rel, result)
    log.write(result["output"].rstrip() + "\n")
    log.flush()


def log_path(root):
    name = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / LOG_FOLDER / name / "last.log"


def failed_path(root):
    return log_path(root).with_name("failed.txt")


def read_failed(root):
    try:
        return failed_path(root).read_text(encoding="utf-8").split("\n")
    except OSError:
        return []


def tell(line, log):
    print(line)
    if log is not None:
        log.write(line + "\n")


def revision(root):
    """The checkout a log belongs to; "unknown" when git cannot tell, as for a worktree under WSL."""
    try:
        done = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            timeout=KILL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def fix_round_groups(files, root, base, log):
    """The last run's failed files and the other files to run: all of them when selecting fails."""
    spec = importlib.util.spec_from_file_location("select_tests", Path(__file__).with_name("select_tests.py"))
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    rels = {path: path.relative_to(root).as_posix() for path in files}
    try:
        changed, selected, unmapped = selector.selection(root, list(rels.values()), base)
    except (OSError, subprocess.SubprocessError) as error:
        tell("# fix round: full suite: git failed: %s" % error, log)
        selected = None
    else:
        if selected is None:
            tell("# fix round: full suite: no test reaches %s" % ", ".join(unmapped[:5]), log)
        else:
            tell("# fix round: %d changed, %d of %d test files" % (len(changed), len(selected), len(files)), log)
    failed = set(read_failed(root))
    first = [path for path in files if rels[path] in failed]
    rest = [path for path in files if path not in first and (selected is None or rels[path] in selected)]
    return [first, rest]


def run_suite(groups, env, root, jobs, minimum, log, stop_at_failure=False):
    totals = {"executed": 0, "failed": 0, "skipped": 0}
    timings = []
    failed_rels = []
    failures = {}
    load_only = []
    files = [path for group in groups for path in group]
    started = time.perf_counter()

    def on_result(path, result):
        rel = path.relative_to(root).as_posix()
        if log is None:
            report(rel, result)
        else:
            report_agent(rel, result, log)
        timings.append((result["seconds"], rel))
        for key in totals:
            totals[key] += result[key]
        if not result["ok"]:
            failed_rels.append(rel)
            failures[rel] = (path, result)
            if stop_at_failure:
                raise FirstFailure

    try:
        for group in groups:
            run_files(group, env, root, jobs, on_result)
    except FirstFailure:
        tell("# stopped at the first failure", log)
    # A few files failing is machine load until they fail alone too; many is a real break.
    if not stop_at_failure and 0 < len(failures) <= RERUN_LIMIT:
        for rel, (path, first) in failures.items():
            again = run_file(path, env, root=root)
            if again["ok"]:
                tell("LOAD-ONLY %s: failed in the shared run, passed alone [%.1f s]" % (rel, again["seconds"]), log)
                for key in totals:
                    totals[key] += again[key] - first[key]
                failed_rels.remove(rel)
                load_only.append(rel)
            elif log is None:
                report(rel + " (alone)", again)
            else:
                report_agent(rel + " (alone)", again, log)
    if log is not None:
        ran = {rel for _, rel in timings}
        left = [rel for rel in read_failed(root) if rel not in ran and (root / rel).is_file()]
        failed_path(root).write_text("\n".join(failed_rels + left), encoding="utf-8", newline="\n")
    for seconds, rel in sorted(timings, key=lambda row: row[0], reverse=True)[:SLOWEST]:
        print("# slow %.1f s %s" % (seconds, rel), file=log or sys.stdout)
    tell(
        "# time %.1f s, %.1f s in files, jobs %d"
        % (time.perf_counter() - started, sum(row[0] for row in timings), jobs),
        log,
    )
    tell(
        "# files %d, tests %d, failed %d, skipped %d%s"
        % (
            len(files),
            totals["executed"],
            totals["failed"],
            totals["skipped"],
            ", load-only %d" % len(load_only) if load_only else "",
        ),
        log,
    )
    if len(files) < minimum:
        tell("# found %d test files, fewer than the minimum %d (%s)" % (len(files), minimum, MIN_FILE), log)
        return 1
    if totals["executed"] == 0:
        tell("# no test was executed", log)
        return 1
    return 1 if failed_rels else 0


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description="Run the repo's test files in isolated interpreters.")
    parser.add_argument("--no-claude", action="store_true", help="pass HARNESS_NO_CLAUDE=1 to the tests")
    parser.add_argument("--min-files", type=int, default=None, help="minimum number of test files")
    parser.add_argument("--jobs", type=int, default=None, help="files run at once")
    parser.add_argument("--agent", action="store_true", help="short output, whole output in a log file")
    parser.add_argument("--fix-round", action="store_true", help="affected files, failed first, stop at a failure")
    parser.add_argument("--base", default="main", help="the change starts at the merge base with this revision")
    parser.add_argument("--control", action="store_true", help="run only the planted failing test file")
    args = parser.parse_args(argv)
    env = clean_env(os.environ, no_claude=args.no_claude)

    if args.control:
        result = run_file(root / CONTROL, env, root=root)
        report(CONTROL, result)
        if not result["ok"] and result["reported_failures"] >= 1:
            print("# control: caught")
            return 0
        print("# control: missed (the planted failure was not reported)")
        return 1

    if args.min_files is not None and args.min_files < 1:
        print("# --min-files must be at least 1")
        return 1
    if args.jobs is not None and args.jobs < 1:
        print("# --jobs must be at least 1")
        return 1
    jobs = args.jobs if args.jobs is not None else min(JOBS, os.cpu_count() or 1)
    minimum = args.min_files if args.min_files is not None else read_min(root)
    if minimum is None and not args.fix_round:
        print("# cannot read a positive integer from %s" % MIN_FILE)
        return 1
    files = discover(root)
    if not (args.agent or args.fix_round):
        return run_suite([files], env, root, jobs, minimum, None)
    path = log_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        log = path.open("w", encoding="utf-8", errors="backslashreplace", newline="\n")
    except OSError as error:
        print("# cannot write %s: %s" % (path, error))
        return 1
    with log:
        print("# log %s" % path)
        log.write("# root %s\n# revision %s\n" % (root, revision(root)))
        if not args.fix_round:
            return run_suite([files], env, root, jobs, minimum, log)
        groups = fix_round_groups(files, root, args.base, log)
        code = run_suite(groups, env, root, jobs, 1, log, stop_at_failure=True)
        tell("# fix round only: the full suite stays mandatory before merge", log)
        return code


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
