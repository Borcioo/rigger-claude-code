"""Tests for tools/lessons_batch.py (start conditions, detached spawn, the run, restore, CLI).

Config, home and inbox live under a temp dir; every model call goes to tests/stub_claude.py; a test that starts a
process waits for it and checks it is gone.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(PLUGIN / "tools"), str(PLUGIN / "tests")]

import batch_apply  # noqa: E402
import lessons_batch  # noqa: E402
import stub_claude  # noqa: E402
from stub_claude import wait_for  # noqa: E402

SCRIPT = PLUGIN / "tools" / "lessons_batch.py"
SEP = " · "
TODAY = "2040-06-15"
NOW = 2000000000.0


def entry(day, tag, signal=None, ref="s#t"):
    parts = ["- 2026-01-%02d" % day, "global", "demo", "case " + tag, "rule " + tag]
    if signal:
        parts += ["signal_type: " + signal, "source_ref: " + ref, "target: rules/x.md", "why: a reason"]
    return SEP.join(parts)


def write(path: Path, text, mtime=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    if mtime is not None:
        os.utime(str(path), (mtime, mtime))
    return path


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def tri(line, action, **extra):
    return dict({"line": line, "action": action, "merge_into": None, "covered_by": "", "quote": "", "reason": "why"},
                **extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lessons-batch-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.config = self.home / ".claude"
        self.state = self.config / "lessons-batch"
        self.lock = self.state / "lock"
        self.root = self.tmp / "cache" / "rigger-rules" / "1.0"
        self.rule = write(self.root / "rules" / "x.md", "# X\n\n## H\n\nold rule text\n")
        write(self.config / "plugins" / "installed_plugins.json",
              json.dumps({"plugins": {"rigger-rules@rigger": [{"installPath": str(self.root)}]}}))
        self.lines = [entry(1, "target", "agent-observation", "a"), entry(2, "repeat", "test-failure", "b"),
                      entry(3, "covered", "agent-observation"), entry(4, "covered-evidence", "review-finding"),
                      entry(5, "propose", "agent-observation"), entry(6, "six"), entry(7, "seven")]
        self.text = "# Lessons inbox\n\n" + "\n".join(self.lines) + "\n"  # lines 3..9; slice 4, 6, 3, 5, 7
        self.inbox = write(self.config / "lessons-inbox.md", self.text)
        self.plan, self.calls = self.tmp / "plan.json", self.tmp / "calls.jsonl"
        self.env = stub_claude.clean_env(self.home, HARNESS_LESSONS_CLAUDE=json.dumps(stub_claude.stub_argv()),
                                         LESSONS_STUB_PLAN=str(self.plan), HARNESS_LESSONS_TODAY=TODAY)
        self.stub()

    def stub(self, triage=None, proposal=None):
        if triage is None:
            triage = {"structured": {"entries": [
                tri(4, "merge", merge_into=3, reason="same class"), tri(6, "covered", covered_by="x.md#H"),
                tri(3, "keep", reason="too specific"), tri(5, "covered", covered_by="x.md#H"), tri(7, "propose")]}}
        if proposal is None:
            proposal = {"structured": {"proposals": [
                {"line": 6, "kind": "edit", "target": self.rule.as_posix(), "heading": "H", "old_text": "old rule text",
                 "new_text": "new rule text", "evidence": "ok", "reason": "sharper"},
                {"line": 7, "kind": "guard", "target": "", "heading": "", "old_text": "", "new_text": "a lint",
                 "evidence": "needs-evidence", "reason": "a guard"}]}}
        write(self.plan, json.dumps({"log": str(self.calls), "triage": triage, "proposal": proposal}))

    def stub_calls(self):
        return [json.loads(line) for line in read(self.calls).splitlines()]

    def run_batch(self):
        return lessons_batch.run(self.env, self.inbox)

    def logs(self):
        return sorted((self.state / "logs").glob("2*.log"))

    def assert_untouched(self, failures="1"):
        self.assertEqual(read(self.inbox), self.text)
        self.assertFalse((self.config / "lessons-archive.md").exists() or (self.config / "lessons-proposals.md").exists())
        self.assertEqual((self.lock.exists(), read(self.state / "failures")), (False, failures))


class MaybeStartTests(Base):
    def start(self, **env):
        calls = []
        reason = lessons_batch.maybe_start(dict(self.env, **dict({"RIGGER_LESSONS_BATCH": "1"}, **env)), self.inbox, self.inbox.read_bytes(), now=NOW,
                                           spawn=lambda *args: calls.append(args))
        return reason, calls

    def test_started_spawns_run_argv(self):
        reason, calls = self.start()
        self.assertEqual(reason, "started")
        argv, log, env = calls[0]
        self.assertEqual(argv, [sys.executable, "-I", "-X", "utf8", str(SCRIPT), "run", "--inbox", str(self.inbox)])
        self.assertEqual((log, env["CLAUDE_CONFIG_DIR"]), (self.state / "logs" / "spawn.log", str(self.config)))
        self.assertEqual(read(self.state / "stamp"), "%d" % NOW)

    def test_inside_a_batch_and_off(self):
        self.assertEqual(self.start(HARNESS_LESSONS_BATCH="1"), ("inside a batch", []))
        self.assertEqual(self.start(RIGGER_LESSONS_BATCH="0"), ("off", []))
        self.assertEqual(self.start(RIGGER_LESSONS_BATCH=""), ("off", []))

    def test_off_by_default(self):
        env = {k: v for k, v in self.env.items() if k != "RIGGER_LESSONS_BATCH"}
        calls = []
        reason = lessons_batch.maybe_start(env, self.inbox, self.inbox.read_bytes(), now=NOW,
                                           spawn=lambda *args: calls.append(args))
        self.assertEqual((reason, calls, (self.state / "stamp").exists()), ("off", [], False))

    def test_too_few_entries(self):
        write(self.inbox, "\n".join(self.lines[:4] + [line + SEP + "curated: 2026-02-01" for line in self.lines[4:]]))
        self.assertEqual(self.start(), ("too few entries", []))

    def test_throttle(self):
        for stamp, failures, reason in ((NOW - 100, "0", "throttled"), (NOW - 7300, "0", "started"),
                                        (NOW + 100, "0", "started"), (NOW - 3 * 3600, "3", "throttled"),
                                        (NOW - 3 * 3600, "2", "started")):
            write(self.state / "stamp", "%d" % stamp)
            write(self.state / "failures", failures)
            self.assertEqual(self.start()[0], reason, (stamp - NOW, failures))

    def test_locked(self):
        write(self.lock, "other\n", mtime=NOW - 60)
        self.assertEqual(self.start(), ("locked", []))
        write(self.lock, "other\n", mtime=NOW - 1900)
        self.assertEqual(self.start()[0], "started")

    def test_no_claude(self):
        self.assertEqual(self.start(HARNESS_LESSONS_CLAUDE="[]"), ("no claude", []))


class DecideTests(Base):
    def decide(self, triage, proposals=(), numbers=(4,), lines=None):
        lines = lines or {number: line for number, line in enumerate(self.lines, 3)}
        roots = [("rigger-rules", self.root)]
        return lessons_batch.decide([(n, lines[n]) for n in numbers], triage, list(proposals), lines, roots)

    def edit(self, line=6, **extra):
        return dict({"line": line, "kind": "edit", "target": self.rule.as_posix(), "heading": "H",
                     "old_text": "old rule text", "new_text": "new", "evidence": "ok", "reason": "r"}, **extra)

    def test_merge_into_newer_entry_parks_instead(self):
        merged = self.decide([tri(4, "merge", merge_into=3)])[0]
        self.assertEqual((merged["action"], merged["target_entry"]), ("merge", self.lines[0]))
        for target in (5, 99, 4):
            self.assertEqual(self.decide([tri(4, "merge", merge_into=target)])[0]["action"], "park", target)
        same_day = {3: entry(2, "same day", "test-failure"), 4: self.lines[1]}
        self.assertEqual(self.decide([tri(3, "merge", merge_into=4)], numbers=(3,), lines=same_day)[0]["action"], "park")
        self.assertEqual(self.decide([tri(4, "merge", merge_into=3)], lines=same_day)[0]["action"], "merge")
        no_fields = {3: entry(1, "old"), 4: self.lines[1]}
        self.assertEqual(self.decide([tri(4, "merge", merge_into=3)], lines=no_fields)[0]["action"], "park")

    def test_covered_with_evidence_goes_to_proposals(self):
        self.assertEqual(self.decide([tri(5, "covered")], numbers=(5,))[0]["action"], "archive")
        queued = self.decide([tri(6, "covered")], [self.edit()], numbers=(6,))[0]
        self.assertEqual((queued["action"], queued["proposal"]["target"]), ("queue", "plugins/rigger-rules/rules/x.md"))
        self.assertEqual(self.decide([tri(6, "covered")], numbers=(6,))[0]["action"], "park")

    def test_edit_needs_old_text_once(self):
        for extra in ({"old_text": "absent"}, {"old_text": ""}, {"target": (self.tmp / "none.md").as_posix()}):
            self.assertEqual(self.decide([tri(6, "propose")], [self.edit(**extra)], numbers=(6,))[0]["action"], "park")
        write(self.rule, "old rule text\nold rule text\n")
        self.assertEqual(self.decide([tri(6, "propose")], [self.edit()], numbers=(6,))[0]["action"], "park")
        write(self.rule, "")  # "".count("") == 1: an empty old text in an empty file is no edit either
        self.assertEqual(self.decide([tri(6, "propose")], [self.edit(old_text="")], numbers=(6,))[0]["action"], "park")
        other = write(self.tmp / "elsewhere" / "y.md", "old rule text\n").as_posix()
        self.assertEqual(self.decide([tri(6, "propose")], [self.edit(target=other)], numbers=(6,))[0]["proposal"]
                         ["target"], other)

    def test_proposal_kinds_and_keep(self):
        guard = self.edit(kind="guard", old_text="", new_text="a lint")
        cases = [(tri(6, "propose"), guard, "queue"), (tri(6, "propose"), dict(guard, new_text=" "), "park"),
                 (tri(6, "propose"), self.edit(kind="archive"), "archive"), (tri(6, "keep"), None, "park")]
        for triage, proposal, action in cases:
            found = self.decide([triage], [proposal] if proposal else [], numbers=(6,))
            self.assertEqual(found[0]["action"], action, (triage, proposal))

    def test_entry_without_triage_gets_no_decision_and_twins_get_occurrence(self):
        self.assertEqual([item["line"] for item in self.decide([tri(6, "keep")], numbers=(4, 6))], [6])
        twins = {3: self.lines[0], 4: self.lines[0]}
        self.assertEqual(self.decide([tri(4, "keep")], numbers=(4,), lines=twins)[0]["occurrence"], 1)


class RunTests(Base):
    def test_run_end_to_end(self):
        write(self.state / "failures", "2")
        self.assertEqual(self.run_batch(), 0)
        lines = self.lines
        merged = batch_apply.merge_entry(batch_apply.park(lines[0], TODAY), lines[1])
        kept = [merged, batch_apply.park(lines[3], TODAY), batch_apply.park(lines[4], TODAY), lines[5], lines[6]]
        self.assertEqual(read(self.inbox), "# Lessons inbox\n\n" + "\n".join(kept) + "\n")
        run_id = self.logs()[-1].stem
        archive, queue = read(self.config / "lessons-archive.md"), read(self.config / "lessons-proposals.md")
        self.assertTrue(archive.startswith("## Run %s\n\n%s%sarchived: covered by x.md#H" % (run_id, lines[2], SEP)))
        self.assertIn("\n" + lines[1] + SEP + "archived: same class\n", archive)
        self.assertEqual(batch_apply.count_queued(queue), 2)
        self.assertIn("- target: plugins/rigger-rules/rules/x.md\n", queue)
        record = json.loads(read(self.state / "runs" / (run_id + ".json")))
        self.assertEqual((record["archived"], record["queued"]), ([lines[2], lines[1]], 2))
        self.assertEqual(read(self.state / "backups" / (run_id + ".md")), self.text)
        log = read(self.logs()[-1])
        for text in ("corpus: 1 files", "triage: ok", "proposal: ok", "cost $0.0100", "stub-model in 10 out 5", "done"):
            self.assertIn(text, log)
        self.assertEqual((self.lock.exists(), read(self.state / "failures")), (False, "0"))
        calls = self.stub_calls()
        self.assertEqual([call["stage"] for call in calls], ["triage", "proposal"])
        self.assertEqual([call["argv"][call["argv"].index("--model") + 1] for call in calls], ["sonnet", "opus"])
        self.assertEqual([call["argv"][-1] for call in calls], ["2.0", "3.0"])
        self.assertEqual({(c["env"]["HARNESS_LESSONS_BATCH"], c["env"]["CLAUDE_CODE_CHILD_SESSION"]) for c in calls},
                         {("1", "1")})
        self.assertFalse(Path(calls[0]["cwd"]).exists())
        self.assertIn("## Step 2", calls[1]["prompt"])
        self.assertEqual([line in calls[1]["prompt"] for line in lines[:5]], [False, False, False, True, True])

    def test_failed_call_leaves_inbox_and_releases_lock(self):
        self.stub(triage={"structured": {"entries": []}, "exit": 1})
        self.assertEqual(self.run_batch(), 1)
        self.assert_untouched()
        self.assertIn("triage: failed (exit 1)", read(self.logs()[-1]))
        self.assertEqual(read(self.state / "failed-log"), str(self.logs()[-1]))
        (self.state / "attempts.json").unlink()  # a first failure for these entries: a second one parks them
        self.stub(triage={"structured": {"entries": [tri(9, "keep")]}})
        self.assertEqual(self.run_batch(), 1)
        self.assert_untouched("2")

    def test_failed_triage_counts_attempts_and_second_failure_parks(self):
        self.stub(triage={"structured": {"entries": []}, "exit": 1})
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual(json.loads(read(self.state / "attempts.json") or "{}"), {line: 1 for line in self.lines[:5]})
        self.assertEqual(read(self.inbox), self.text)
        self.assertEqual(self.run_batch(), 1)
        parked = [batch_apply.park(line, TODAY) for line in self.lines[:5]] + self.lines[5:]
        self.assertEqual(read(self.inbox), "# Lessons inbox\n\n" + "\n".join(parked) + "\n")
        self.assertEqual((json.loads(read(self.state / "attempts.json")), read(self.state / "failures")), ({}, "2"))
        self.assertIn("batch could not triage", read(self.logs()[-1]))

    def test_failed_proposal_call_applies_decisions_without_proposal(self):
        self.stub(proposal={"structured": {"proposals": []}, "exit": 1})
        self.assertEqual(self.run_batch(), 1)
        lines = self.lines
        merged = batch_apply.merge_entry(batch_apply.park(lines[0], TODAY), lines[1])
        self.assertEqual(read(self.inbox), "# Lessons inbox\n\n" + "\n".join([merged] + lines[3:]) + "\n")
        self.assertEqual(json.loads(read(self.state / "attempts.json")), {lines[3]: 1, lines[4]: 1})
        self.assertFalse((self.config / "lessons-proposals.md").exists())
        self.assertEqual(read(self.state / "failures"), "1")

    def test_unreadable_archive_or_queue_stops_before_apply(self):
        bad = "## Run 20400101-000000-1\n\n- old \xb7 entry\n".encode("cp1250") + "- still é here\n".encode("utf-8")
        for number, name in enumerate((lessons_batch.ARCHIVE_NAME, lessons_batch.QUEUE_NAME), 1):
            with self.subTest(name=name):
                for other in (lessons_batch.ARCHIVE_NAME, lessons_batch.QUEUE_NAME):
                    (self.config / other).unlink() if (self.config / other).exists() else None
                write(self.config / name, bad)
                code = self.run_batch()
                self.assertEqual((self.config / name).read_bytes(), bad)
                self.assertEqual((code, read(self.inbox), read(self.state / "failures")), (1, self.text, str(number)))
                self.assertIn("cannot read", read(self.logs()[-1]))

    def test_failed_inbox_replace_undoes_archive_queue_and_record(self):
        old = "## Run 20400101-000000-1\n\n- 2026-01-01 · old · archived: x\n"
        write(self.config / "lessons-archive.md", old)
        real = batch_apply.write_atomic

        def failing(path, *args, **kwargs):
            if Path(path) == self.inbox:
                raise PermissionError(13, "sharing violation (test)")
            return real(path, *args, **kwargs)

        with mock.patch.object(batch_apply, "write_atomic", failing):
            code = self.run_batch()
        self.assertEqual((read(self.config / "lessons-archive.md"), (self.config / "lessons-proposals.md").exists(),
                          list((self.state / "runs").glob("*.json"))), (old, False, []))
        self.assertEqual((code, read(self.inbox)), (1, self.text))
        self.assertIn("PermissionError", read(self.logs()[-1]))

    def test_inbox_changed_while_replace_is_retried_is_redone(self):
        late, replace, fired = entry(9, "late", "test-failure"), os.replace, []

        def held_once(src, dst):  # the inbox is held at the first replace and gets an entry meanwhile
            if Path(dst) == Path(os.path.realpath(str(self.inbox))) and not fired:
                fired.append(1)
                write(self.inbox, read(self.inbox) + late + "\n")
                raise PermissionError(13, "held (test)")
            return replace(src, dst)

        with mock.patch.object(batch_apply.os, "replace", held_once):
            self.assertEqual(self.run_batch(), 0)
        self.assertTrue(read(self.inbox).endswith("\n" + late + "\n"))
        self.assertNotIn(self.lines[2], read(self.inbox))

    def test_wall_limit_checked_before_proposal_call(self):
        with mock.patch.object(lessons_batch, "WALL_SECONDS", -1):
            self.assertEqual(self.run_batch(), 1)
        self.assertEqual([call["stage"] for call in self.stub_calls()], ["triage"])
        self.assertIn("wall limit", read(self.logs()[-1]))

    def test_stale_lock_replaced_before_removal_is_kept(self):
        write(self.lock, "other\n", mtime=NOW - 3600)
        fired = []

        def competitor(real):
            def wrapped(src, *args, **kwargs):  # another run takes the stale lock just before this one moves it
                if Path(src) == self.lock and not fired:
                    fired.append(1)
                    write(self.lock, "newer\n", mtime=NOW - 3600)
                return real(src, *args, **kwargs)
            return wrapped

        with mock.patch.object(lessons_batch.os, "remove", competitor(os.remove)), \
                mock.patch.object(lessons_batch.os, "rename", competitor(os.rename)):
            taken = lessons_batch._take_lock(self.lock, "mine", NOW)
        self.assertEqual((taken, read(self.lock), fired), (False, "newer\n", [1]))

    def test_restore_redone_when_inbox_changes(self):
        self.assertEqual(self.run_batch(), 0)
        run_id, late, restore = self.logs()[-1].stem, entry(9, "late", "test-failure"), batch_apply.restore

        def appending(*args, times=[1]):  # an entry is added after restore read the inbox
            result = restore(*args)
            if times:
                times.pop()
                write(self.inbox, read(self.inbox) + late + "\n")
            return result

        with mock.patch.object(batch_apply, "restore", appending), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(lessons_batch.restore_run(self.env, run_id, self.inbox), 0)
        self.assertEqual(sorted(read(self.inbox).split("\n")), sorted(self.text.split("\n") + [late]))

    def test_stale_lock_is_taken(self):
        write(self.lock, "other\n", mtime=os.path.getmtime(str(self.inbox)) - 3600)
        self.assertEqual(self.run_batch(), 0)
        self.assertNotEqual(read(self.inbox), self.text)

    def test_stale_lock_changed_since_read_is_kept(self):
        write(self.lock, "other\n", mtime=NOW - 3600)
        real = lessons_batch._read

        def replaced_after_read(path):  # another run takes the stale lock right after this one read it
            text = real(path)
            if path == self.lock and text == "other\n":
                write(self.lock, "newer\n", mtime=NOW - 3600)
            return text

        with mock.patch.object(lessons_batch, "_read", replaced_after_read):
            self.assertFalse(lessons_batch._take_lock(self.lock, "mine", NOW))
        self.assertEqual(read(self.lock), "newer\n")

    def test_lock_released_when_run_is_interrupted(self):
        def interrupted(*args):
            raise KeyboardInterrupt

        with mock.patch.object(lessons_batch, "_run_locked", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_batch()
        self.assertFalse(self.lock.exists())

    def test_only_harness_plugin_targets_are_rewritten(self):
        other = self.tmp / "cache" / "elsewhere" / "other" / "1.0"
        write(self.config / "plugins" / "installed_plugins.json", json.dumps({"plugins": {
            "rigger-rules@rigger": [{"installPath": str(self.root)}],
            "other@elsewhere": [{"installPath": str(other)}]}}))
        target = (other / "rules" / "y.md").as_posix()
        self.stub(triage={"structured": {"entries": [tri(7, "propose")]}},
                  proposal={"structured": {"proposals": [
                      {"line": 7, "kind": "guard", "target": target, "heading": "", "old_text": "", "new_text": "a lint",
                       "evidence": "ok", "reason": "a guard"}]}})
        self.assertEqual(self.run_batch(), 0)
        self.assertIn("- target: %s\n" % target, read(self.config / "lessons-proposals.md"))

    def test_fresh_lock_stops_run(self):
        write(self.lock, "other\n")
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual((read(self.inbox), read(self.lock), self.stub_calls()), (self.text, "other\n", []))
        self.assertIn("locked", read(self.logs()[-1]))

    def test_stops_before_any_call(self):
        write(self.inbox, self.text.encode("utf-8") + b"- 2026-02-01 \xff\n")
        self.assertEqual(self.run_batch(), 1)
        self.assertIn("not UTF-8", read(self.logs()[-1]))
        write(self.inbox, self.text)
        with mock.patch.object(lessons_batch, "SKILL", self.tmp / "missing.md"):
            self.assertEqual(self.run_batch(), 1)
        self.assertIn("Step 2", read(self.logs()[-1]))
        self.assertEqual((self.stub_calls(), read(self.state / "failures")), ([], ""))

    def test_wall_limit_and_lost_lock_stop_before_apply(self):
        with mock.patch.object(lessons_batch, "WALL_SECONDS", -1):
            self.assertEqual(self.run_batch(), 1)
        self.assert_untouched()
        self.stub(triage={"structured": {"entries": [tri(3, "keep")]}, "started": str(self.lock)})
        self.assertEqual(self.run_batch(), 1)
        self.assertEqual(read(self.inbox), self.text)
        self.assertIn("lock", read(self.logs()[-1]))
        self.assertTrue(self.lock.exists())  # not ours: left for its owner

    def test_inbox_changed_during_apply_is_redone(self):
        apply, late = batch_apply.apply_decisions, entry(9, "late", "test-failure")

        def appending(*args, times=[1]):
            if times:
                times.pop()
                write(self.inbox, read(self.inbox) + late + "\n")
            return apply(*args)

        with mock.patch.object(batch_apply, "apply_decisions", appending):
            self.assertEqual(self.run_batch(), 0)
        self.assertTrue(read(self.inbox).endswith("\n" + late + "\n"))
        self.assertNotIn(self.lines[2], read(self.inbox))
        self.assertIn("inbox changed", read(self.logs()[-1]))

    def test_inbox_changing_every_time_applies_nothing(self):
        apply = batch_apply.apply_decisions

        def appending(*args):
            write(self.inbox, read(self.inbox) + entry(9, "late") + "\n")
            return apply(*args)

        with mock.patch.object(batch_apply, "apply_decisions", appending):
            self.assertEqual(self.run_batch(), 1)
        self.assertEqual((read(self.config / "lessons-archive.md"), read(self.config / "lessons-proposals.md")), ("", ""))
        self.assertEqual(list((self.state / "runs").glob("*.json")), [])

    def test_attempts_park_after_two(self):
        write(self.state / "attempts.json", json.dumps({self.lines[4]: 1, "gone": 1, self.lines[0]: "x"}))
        self.stub(triage={"structured": {"entries": [tri(4, "keep"), tri(6, "keep"), tri(5, "keep")]}})
        self.assertEqual(self.run_batch(), 0)
        self.assertIn(batch_apply.park(self.lines[4], TODAY), read(self.inbox))
        self.assertEqual(json.loads(read(self.state / "attempts.json")), {self.lines[0]: 1})
        self.assertIn("batch could not triage", read(self.logs()[-1]))
        write(self.state / "attempts.json", "[1]")
        self.stub(triage={"structured": {"entries": [tri(3, "keep")]}})
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(json.loads(read(self.state / "attempts.json")), {self.lines[5]: 1, self.lines[6]: 1})

    def test_prune_keeps_20_logs_10_backups(self):
        for number in range(25):
            for folder, suffix in (("logs", ".log"), ("runs", ".json"), ("backups", ".md")):
                write(self.state / folder / ("20000101-0000%02d-1%s" % (number, suffix)), "{}")
        write(self.state / "logs" / "spawn.log", "kept")
        self.assertEqual(self.run_batch(), 0)
        counts = [len(list((self.state / folder).glob("2*" + suffix)))
                  for folder, suffix in (("logs", ".log"), ("runs", ".json"), ("backups", ".md"))]
        self.assertEqual((counts, read(self.state / "logs" / "spawn.log")), ([20, 20, 10], "kept"))


class ProcessTests(Base):
    def popen_run(self, out):
        handle = open(str(out), "wb")
        self.addCleanup(handle.close)
        return subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(SCRIPT), "run", "--inbox", str(self.inbox)],
                                stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=self.env)

    def test_two_starts_one_run(self):
        started, release = self.tmp / "started", self.tmp / "release"
        self.stub(triage={"structured": {"entries": [tri(3, "keep")]}, "started": str(started), "release": str(release)})
        first = self.popen_run(self.tmp / "first.out")
        self.addCleanup(first.kill)
        self.assertTrue(wait_for(started.exists), read(self.tmp / "first.out"))
        second = self.popen_run(self.tmp / "second.out")
        self.assertEqual(second.wait(60), 0)
        release.write_text("go", encoding="utf-8")
        self.assertEqual(first.wait(60), 0)
        logs = [read(path) for path in self.logs()]
        self.assertEqual(sorted("locked" in log for log in logs), [False, True])
        self.assertEqual(sorted("done" in log for log in logs), [False, True])
        self.assertFalse(stub_claude.alive(int(read(started))))

    def test_hook_returns_before_child_finishes(self):
        release, marker = self.tmp / "release", self.tmp / "marker"
        child = [sys.executable, "-I", "-c", "import os, time\nwhile not os.path.exists(%r): time.sleep(0.05)\n"
                 "open(%r, 'w').close()" % (str(release), str(marker))]
        hook = ("import sys; from pathlib import Path; sys.path.insert(0, %r); import lessons_batch; "
                "print(lessons_batch.spawn_detached(%r, Path(%r), None))" % (str(PLUGIN / "tools"), child,
                                                                             str(self.tmp / "spawn.log")))
        proc = subprocess.Popen([sys.executable, "-I", "-c", hook], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env)
        self.addCleanup(proc.kill)
        output = []
        reader = threading.Thread(target=lambda: output.append(proc.communicate()), daemon=True)
        reader.start()
        reader.join(30)  # Claude Code reads the hook's output until every holder of the pipe closes it
        output_open, marker_early = reader.is_alive(), marker.exists()
        release.write_text("go", encoding="utf-8")  # always: a child that held the pipe ends too
        reader.join(30)
        self.assertFalse(output_open, "the hook's output stayed open until the detached child ended")
        self.assertFalse(marker_early)
        stdout, stderr = output[0]
        self.assertEqual((proc.returncode, stderr), (0, b""))
        pid = int(stdout)
        self.assertTrue(wait_for(marker.exists))
        self.assertTrue(wait_for(lambda: not stub_claude.alive(pid)), "child left running")

    def test_detached_child_has_no_console_or_session(self):
        marker = self.tmp / "marker"
        # Windows: GetConsoleCP() is 0 without a console. POSIX: the child leads its own session.
        probe = ("import os\nif os.name == 'nt':\n    import ctypes\n    value = ctypes.windll.kernel32.GetConsoleCP()\n"
                 "else:\n    value = int(os.getsid(0) == os.getpid())\n"
                 "open(%r, 'w').write(str(value))\nos.replace(%r, %r)" % (str(marker) + ".tmp", str(marker) + ".tmp",
                                                                           str(marker)))
        pid = lessons_batch.spawn_detached([sys.executable, "-I", "-c", probe], self.tmp / "spawn.log", self.env)
        self.assertTrue(wait_for(marker.exists), read(self.tmp / "spawn.log"))
        self.assertEqual(read(marker), "0" if os.name == "nt" else "1")
        self.assertTrue(wait_for(lambda: not stub_claude.alive(pid)), "child left running")

    def test_spawned_run_handles_non_ascii_entry(self):
        lines = [entry(day, "αβγδε %d" % day) for day in range(1, 6)]
        write(self.inbox, "\n".join(lines) + "\n")
        self.stub(triage={"structured": {"entries": [tri(number, "keep") for number in range(1, 6)]}})
        self.assertEqual(lessons_batch.maybe_start(dict(self.env, RIGGER_LESSONS_BATCH="1"), self.inbox,
                                                   self.inbox.read_bytes()), "started")
        self.assertTrue(wait_for(lambda: list((self.state / "runs").glob("*.json")) and not self.lock.exists(), 60),
                        read(self.state / "logs" / "spawn.log"))
        self.assertEqual(read(self.inbox), "\n".join(batch_apply.park(line, TODAY) for line in lines) + "\n")
        pid = int(self.logs()[-1].stem.rsplit("-", 1)[1])
        self.assertTrue(wait_for(lambda: not stub_claude.alive(pid)), "batch left running")

    def test_restore_cli(self):
        self.assertEqual(self.run_batch(), 0)
        run_id = self.logs()[-1].stem

        def restore(run):
            proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "restore", run], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env, timeout=60)
            return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n")

        self.assertEqual(restore(run_id), (0, "restored 6 changes of run %s\n" % run_id))
        self.assertEqual(sorted(read(self.inbox).split("\n")), sorted(self.text.split("\n")))
        self.assertEqual((read(self.config / "lessons-archive.md"), read(self.config / "lessons-proposals.md")), ("", ""))
        self.assertEqual(restore(run_id), (0, "restored 0 changes of run %s\n" % run_id))
        self.assertEqual(restore("../runs/x")[0], 1)


if __name__ == "__main__":
    unittest.main()
