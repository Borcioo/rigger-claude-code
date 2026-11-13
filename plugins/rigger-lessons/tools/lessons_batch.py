"""Background batches of the lessons inbox: start conditions, detached spawn, the run, restore.

python lessons_batch.py run [--inbox PATH]          one batch (started detached by the SessionStart hook)
python lessons_batch.py restore RUN_ID [--inbox PATH]  undo one run's moves, curated marks and queue blocks
--inbox defaults to the inbox lessons_start.py uses. A run triages up to 5 unparked entries with `claude -p` and
writes only the inbox, lessons-archive.md and lessons-proposals.md next to it, and its state dir
<config>/lessons-batch/: stamp, lock, failures, failed-log (log path of the last failed run), attempts.json, logs/,
runs/, backups/.
Env: batches are opt-in: RIGGER_LESSONS_BATCH=1 turns them on; HARNESS_LESSONS_BATCH (set in a batch's own claude child) starts nothing;
HARNESS_LESSONS_CLAUDE replaces `claude` in tests (see batch_model.py).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "hooks")]

import batch_apply  # noqa: E402
import batch_model  # noqa: E402
import inbox_lint  # noqa: E402
import lessons_start  # noqa: E402

SKILL = HERE.parent / "skills" / "curate-lessons" / "SKILL.md"
MIN_ENTRIES = 5
BATCH_SIZE = 5
THROTTLE_SECONDS = 7200
THROTTLE_AFTER_FAILURES = 86400
FAILURES_LIMIT = 3
STALE_LOCK_SECONDS = 1800
WALL_SECONDS = 900
CALL_TIMEOUT = 600
TRIAGE_MODEL, TRIAGE_BUDGET = "sonnet", 2.0
PROPOSAL_MODEL, PROPOSAL_BUDGET = "opus", 3.0
KEEP_BACKUPS, KEEP_LOGS = 10, 20
ATTEMPTS_LIMIT = 2
APPLY_TRIES = 3
SPAWN_LOG_LIMIT = 1 << 20
ARCHIVE_NAME, QUEUE_NAME = "lessons-archive.md", "lessons-proposals.md"
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]+")


def state_dir(environ) -> Path:
    return lessons_start.config_root(environ) / "lessons-batch"


def _read(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_strict(path: Path):
    """The UTF-8 text of path, None when it is missing; any other read or decode error raises."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _number(path: Path):
    try:
        return float(_read(path).strip())
    except ValueError:
        return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    batch_apply.write_atomic(path, text)


def maybe_start(environ, inbox: Path, data: bytes, now: float | None = None, spawn=None) -> str:
    now = time.time() if now is None else now
    if environ.get("HARNESS_LESSONS_BATCH"):
        return "inside a batch"
    if environ.get("RIGGER_LESSONS_BATCH") != "1":
        return "off"
    if batch_apply.count_unparked(data) < MIN_ENTRIES:
        return "too few entries"
    state = state_dir(environ)
    stamp = _number(state / "stamp")
    limit = THROTTLE_AFTER_FAILURES if (_number(state / "failures") or 0) >= FAILURES_LIMIT else THROTTLE_SECONDS
    if stamp is not None and 0 <= now - stamp < limit:
        return "throttled"
    try:
        if now - os.stat(state / "lock").st_mtime < STALE_LOCK_SECONDS:
            return "locked"
    except OSError:
        pass
    if batch_model.claude_argv(environ, TRIAGE_MODEL, batch_model.TRIAGE_SCHEMA, TRIAGE_BUDGET) is None:
        return "no claude"
    _write(state / "stamp", "%d" % now)
    argv = [sys.executable, "-I", "-X", "utf8", str(Path(__file__).resolve()), "run", "--inbox", str(inbox)]
    (spawn or spawn_detached)(argv, state / "logs" / "spawn.log", dict(environ))
    return "started"


def spawn_detached(argv: list[str], log_path: Path, env=None) -> int:
    """Start argv with no console and no inherited handle, stdout and stderr to log_path; return its pid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if log_path.exists() and log_path.stat().st_size > SPAWN_LOG_LIMIT else "ab"
    if os.name == "nt":
        extra = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        extra = {"start_new_session": True}
    with open(log_path, mode) as log:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, env=env,
                                cwd=str(log_path.parent), **extra).pid


def _take_lock(lock: Path, owner: str, now: float) -> bool:
    for _ in range(2):
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            seen = _read(lock)
            try:
                stale = now - os.stat(lock).st_mtime >= STALE_LOCK_SECONDS
            except OSError:
                continue
            if not stale:
                return False
            aside = lock.with_name("%s.%s.stale" % (lock.name, owner))
            try:  # moved aside first, so a lock another run takes meanwhile is never removed
                os.rename(str(lock), str(aside))
            except OSError:
                return False
            if _read(aside) != seen:  # not the stale lock read above: give it back unless a newer one exists
                try:
                    os.link(str(aside), str(lock))
                except OSError:
                    pass
                os.remove(str(aside))
                return False
            os.remove(str(aside))
            continue
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(owner + "\n")
        return True
    return False


def _release_lock(lock: Path, owner: str) -> None:
    if _read(lock) == owner + "\n":
        os.remove(lock)


def _older(target: int, target_line: str, number: int, entry: str) -> bool:
    return (target_line[2:12], target) < (entry[2:12], number)


def _occurrence(number: int, entry: str, inbox_lines: dict[int, str]) -> int:
    return sum(1 for other, line in inbox_lines.items() if other < number and line == entry)


def _count_in(target: str, text: str) -> int:
    try:
        with open(target, encoding="utf-8") as handle:
            return handle.read().count(text) if text else 0
    except (OSError, UnicodeDecodeError, ValueError):
        return 0


def _repo_path(target: str, harness_roots) -> str:
    """A target inside an installed @rigger plugin as its path in the harness repo; others unchanged."""
    full = os.path.normcase(os.path.abspath(target))
    for name, root in harness_roots:
        base = os.path.normcase(os.path.abspath(str(root))) + os.sep
        if full.startswith(base):
            return "plugins/%s/%s" % (name, full[len(base):].replace(os.sep, "/"))
    return target


def decide(slice_, triage, proposals, inbox_lines, harness_roots=()) -> list[dict]:
    """One decision per slice entry that has a triage item (see batch_apply.apply_decisions)."""
    by_line = {item["line"]: item for item in triage}
    proposal_by_line = {item["line"]: item for item in proposals}
    decisions = []
    for number, entry in slice_:
        item = by_line.get(number)
        if item is None:
            continue
        found = dict(line=number, entry=entry, occurrence=_occurrence(number, entry, inbox_lines), action="park",
                     target_entry=None, target_occurrence=0, reason=item["reason"], proposal=None)
        action, fields = item["action"], inbox_lint.entry_fields(entry)
        if action == "merge":
            target = item["merge_into"]
            line = inbox_lines.get(target)
            if line is not None and _older(target, line, number, entry) and inbox_lint.entry_fields(line):
                found.update(action="merge", target_entry=line, target_occurrence=_occurrence(target, line,
                                                                                               inbox_lines))
            else:
                found["reason"] = "merge target %s is newer, missing or has no fields; %s" % (target, item["reason"])
        elif action == "covered" and not inbox_lint.has_evidence(fields):
            found.update(action="archive", reason="covered by %s: %s" % (item["covered_by"], item["reason"]))
        elif action in ("covered", "propose"):
            proposal = proposal_by_line.get(number)
            kind = proposal["kind"] if proposal else None
            if kind == "archive":
                found.update(action="archive", reason=proposal["reason"])
            elif (kind == "guard" and proposal["new_text"].strip()) or (
                    kind == "edit" and _count_in(proposal["target"], proposal["old_text"]) == 1):
                found.update(action="queue", reason=proposal["reason"],
                             proposal=dict(proposal, target=_repo_path(proposal["target"], harness_roots)))
            else:
                found["reason"] = "no usable proposal (%s); %s" % (kind or "none", item["reason"])
        decisions.append(found)
    return decisions


def _logger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(text: str) -> None:
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write("%s %s\n" % (time.strftime("%H:%M:%S"), batch_apply.one_line(text)))
    return log


def _log_call(log, name: str, result: dict) -> None:
    usage = "; ".join("%s in %s out %s cache-read %s cache-write %s" % (
        model, value.get("inputTokens"), value.get("outputTokens"), value.get("cacheReadInputTokens", 0),
        value.get("cacheCreationInputTokens", 0)) for model, value in result["usage"].items() if isinstance(value, dict))
    state = "ok" if result["ok"] else "failed (%s)" % result["error"]
    log("%s: %s in %.1f s, cost $%.4f, %s %s" % (name, state, result["seconds"], result["cost"] or 0, usage,
                                                "" if result["ok"] else "stderr: " + result["stderr"][-300:]))


def _call(log, name, environ, model, schema, budget, prompt, run_dir):
    argv = batch_model.claude_argv(environ, model, schema, budget)
    result = batch_model.call(argv, prompt, batch_model.child_env(environ), run_dir, CALL_TIMEOUT)
    _log_call(log, name, result)
    return result["value"] if result["ok"] else None


def _attempts(state: Path, slice_, decisions, inbox_lines):
    """Park decisions for slice entries left without a decision a second time, and the new attempts counts."""
    try:
        loaded = json.loads(_read(state / "attempts.json"))
    except (ValueError, RecursionError):
        loaded = {}
    present = set(inbox_lines.values())
    counts = {key: value for key, value in loaded.items() if key in present and isinstance(value, int)
              and not isinstance(value, bool)} if isinstance(loaded, dict) else {}
    decided, parks = {item["line"] for item in decisions}, []
    for number, entry in slice_:
        count = 0 if number in decided else counts.get(entry, 0) + 1
        counts.pop(entry, None)
        if count >= ATTEMPTS_LIMIT:
            parks.append(dict(line=number, entry=entry, occurrence=_occurrence(number, entry, inbox_lines),
                              action="park", target_entry=None, target_occurrence=0, proposal=None,
                              reason="batch could not triage"))
        elif count:
            counts[entry] = count
    return parks, counts


def _commit(log, inbox: Path, before: dict, build) -> bool:
    """Write build(inbox bytes) -> (new inbox text, {path: text}): the files first, the inbox last and only while it
    still holds the bytes build read (else build runs again, up to APPLY_TRIES times). Unless the inbox was written,
    on any error too, each path of `before` gets its text back ({path: text before, None when it was missing})."""
    done = False
    try:
        for _ in range(APPLY_TRIES):
            data = inbox.read_bytes()
            new_inbox, files = build(data)
            for path, text in files.items():
                _write(path, text)
            try:
                batch_apply.write_atomic(inbox, new_inbox, expected=data)
            except batch_apply.Changed:
                log("inbox changed during apply; applying again")
                continue
            done = True
            return True
        log("inbox kept changing; nothing applied")
        return False
    finally:
        if not done:
            for path, text in before.items():
                if text is None and path.exists():
                    os.remove(path)
                elif text is not None:
                    _write(path, text)


def _apply(log, inbox: Path, state: Path, run_id: str, decisions, today) -> bool:
    archive_path, queue_path = inbox.parent / ARCHIVE_NAME, inbox.parent / QUEUE_NAME
    record_path = state / "runs" / (run_id + ".json")
    before = {record_path: None}
    for path in (archive_path, queue_path):
        try:
            before[path] = _read_strict(path)
        except (OSError, ValueError) as exc:  # never rewritten or removed when it cannot be read
            log("cannot read %s (%s: %s); nothing applied" % (path, type(exc).__name__, exc))
            return False
    records = []

    def build(data):
        new_inbox, section, blocks, record = batch_apply.apply_decisions(data.decode("utf-8"), decisions, run_id,
                                                                         today)
        records.append(record)
        (state / "backups").mkdir(parents=True, exist_ok=True)
        (state / "backups" / (run_id + ".md")).write_bytes(data)
        files = {record_path: json.dumps(dict(record, inbox=str(inbox)), ensure_ascii=False, indent=1) + "\n"}
        if section:
            files[archive_path] = batch_apply.append_text(before[archive_path] or "", section)
        if blocks:
            files[queue_path] = batch_apply.append_text(before[queue_path] or "", blocks)
        return new_inbox, files

    if not _commit(log, inbox, before, build):
        return False
    record = records[-1]
    log("applied: %d archived, %d parked, %d merged, %d queued, %d skipped" % (
        len(record["archived"]), len(record["parked"]), len(record["merged"]), record["queued"],
        len(record["skipped"])))
    return True


def _prune(folder: Path, pattern: str, keep: int) -> None:
    for path in sorted(folder.glob(pattern))[:-keep]:
        path.unlink()


def _run_locked(environ, inbox: Path, state: Path, run_id: str, log, started: float):
    """True: applied; False: a failed run (counted); None: stopped before any call."""
    try:
        text = inbox.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log("inbox unreadable or not UTF-8 (%s); stopped" % type(exc).__name__)
        return None
    today = lessons_start.today(environ)
    slice_ = batch_apply.unparked_slice(text.encode("utf-8"), today, BATCH_SIZE)
    rules = batch_model.step2_rules(_read(SKILL))
    config = lessons_start.config_root(environ)
    if not slice_ or not rules or batch_model.claude_argv(environ, TRIAGE_MODEL, {}, 0) is None:
        log("stopped: %s" % ("no unparked entry" if not slice_ else "no Step 2 in %s" % SKILL if not rules
                             else "no claude"))
        return None
    run_dir = Path(tempfile.mkdtemp(prefix="lessons-batch-"))
    try:
        files = batch_model.write_corpus(run_dir, config)
        log("corpus: %d files; slice: lines %s" % (files, ", ".join(str(number) for number, _ in slice_)))
        if not files:
            return None
        value = _call(log, "triage", environ, TRIAGE_MODEL, batch_model.TRIAGE_SCHEMA, TRIAGE_BUDGET,
                      batch_model.triage_prompt(slice_, text), run_dir)
        triage = batch_model.parse_triage(value, {number for number, _ in slice_})
        failed = not triage  # a failed call still counts attempts and applies what needs no model answer
        if failed:
            log("triage gave no decision")
        by_line = dict(slice_)
        wanted = [dict(item, entry=by_line[item["line"]]) for item in triage if item["action"] == "propose" or (
            item["action"] == "covered" and inbox_lint.has_evidence(inbox_lint.entry_fields(by_line[item["line"]])))]
        proposals = []
        if wanted:
            if _past_wall(log, started):
                return False
            value = _call(log, "proposal", environ, PROPOSAL_MODEL, batch_model.PROPOSAL_SCHEMA, PROPOSAL_BUDGET,
                          batch_model.proposal_prompt(wanted, rules), run_dir)
            asked = {item["line"] for item in wanted}
            if value is None:
                failed, triage = True, [item for item in triage if item["line"] not in asked]
            else:
                proposals = batch_model.parse_proposals(value, asked)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    inbox_lines = {number: line.rstrip("\r") for number, line in enumerate(text.split("\n"), 1)
                   if inbox_lint.ENTRY.match(line)}
    roots = [(key.split("@")[0], path) for key, path in batch_model.install_paths(config)
             if key.endswith("@rigger")]
    decisions = decide(slice_, triage, proposals, inbox_lines, roots)
    parks, counts = _attempts(state, slice_, decisions, inbox_lines)
    for item in decisions + parks:
        log("line %d: %s; %s" % (item["line"], item["action"], item["reason"]))
    if _past_wall(log, started):
        return False
    if _read(state / "lock") != run_id + "\n":
        log("the lock is no longer ours; nothing applied")
        return False
    if (decisions or parks) and not _apply(log, inbox, state, run_id, decisions + parks, today):
        return False
    _write(state / "attempts.json", json.dumps(counts, ensure_ascii=False, indent=1) + "\n")
    _prune(state / "logs", "[0-9]*.log", KEEP_LOGS)
    _prune(state / "runs", "[0-9]*.json", KEEP_LOGS)
    _prune(state / "backups", "[0-9]*.md", KEEP_BACKUPS)
    return not failed


def _past_wall(log, started: float) -> bool:
    if time.monotonic() - started > WALL_SECONDS:
        log("wall limit of %d s passed; nothing applied" % WALL_SECONDS)
        return True
    return False


def run(environ, inbox: Path, now: float | None = None) -> int:
    now = time.time() if now is None else now
    started = time.monotonic()
    state = state_dir(environ)
    run_id = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), os.getpid())
    log_path = state / "logs" / (run_id + ".log")
    log = _logger(log_path)
    lock = state / "lock"
    if not _take_lock(lock, run_id, now):
        log("locked by another run; nothing to do")
        return 0
    try:
        _write(state / "stamp", "%d" % now)
        try:
            outcome = _run_locked(environ, inbox, state, run_id, log, started)
        except Exception as exc:  # a crash is a failed run: logged, counted, inbox replaced only as the last step
            log("crashed: %s: %s" % (type(exc).__name__, exc))
            outcome = False
        if outcome is not None:
            _write(state / "failures", "0" if outcome else "%d" % ((_number(state / "failures") or 0) + 1))
        if outcome is False:
            _write(state / "failed-log", str(log_path))
        log("done" if outcome else "stopped")
        return 0 if outcome else 1
    finally:
        _release_lock(lock, run_id)


def restore_run(environ, run_id: str, inbox: Path) -> int:
    state = state_dir(environ)
    try:
        record = json.loads(_read(state / "runs" / (run_id + ".json"))) if RUN_ID.fullmatch(run_id) else None
    except ValueError:
        record = None
    if not isinstance(record, dict) or record.get("run_id") != run_id:
        print("no record of run %s in %s" % (run_id, state / "runs"))
        return 1
    owner = "restore-%s-%d" % (run_id, os.getpid())
    if not _take_lock(state / "lock", owner, time.time()):
        print("a batch is running; try again later")
        return 1
    try:
        archive_path, queue_path = inbox.parent / ARCHIVE_NAME, inbox.parent / QUEUE_NAME
        before = {archive_path: _read_strict(archive_path), queue_path: _read_strict(queue_path)}
        counts = []

        def build(data):
            text, archive, queue, count = batch_apply.restore(data.decode("utf-8"), before[archive_path] or "",
                                                              before[queue_path] or "", record)
            counts.append(count)
            return text, {path: new for path, new in ((archive_path, archive), (queue_path, queue))
                          if before[path] is not None}  # a missing archive or queue is not created

        if not _commit(print, inbox, before, build):
            return 1
        print("restored %d changes of run %s" % (counts[-1], run_id))
        return 0
    finally:
        _release_lock(state / "lock", owner)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    environ = os.environ
    inbox = lessons_start.inbox_path(environ, lessons_start.config_root(environ))
    if "--inbox" in argv[:-1]:
        index = argv.index("--inbox")
        inbox, argv = Path(argv[index + 1]), argv[:index] + argv[index + 2:]
    if argv == ["run"]:
        return run(environ, inbox)
    if len(argv) == 2 and argv[0] == "restore":
        return restore_run(environ, argv[1], inbox)
    print("usage: lessons_batch.py run [--inbox PATH] | restore RUN_ID [--inbox PATH]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
