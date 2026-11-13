"""Tests for tools/batch_apply.py (inbox, archive and queue text operations of a background batch).

Every inbox is synthetic; files are written under a temp dir.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(PLUGIN / "tools")]

import batch_apply  # noqa: E402
import inbox_lint  # noqa: E402

SEP = " · "
TODAY = "2040-06-15"
RUN = "20400615-120000-42"
HEADER = "# Lessons inbox\n\n"


def entry(day, tag, signal=None, ref="s#t", extra=()):
    """One entry line without its line end; signal None is an old entry with no fields."""
    parts = ["- 2026-01-%02d" % day, "global", "demo", "case " + tag, "rule " + tag]
    if signal:
        parts += ["signal_type: " + signal, "source_ref: " + ref, "target: rules/x.md", "why: a reason"]
    return SEP.join(parts + list(extra))


def decision(text, action, reason="a reason", target=None, proposal=None, occurrence=0, target_occurrence=0):
    return {"entry": text, "occurrence": occurrence, "action": action, "target_entry": target,
            "target_occurrence": target_occurrence, "reason": reason, "proposal": proposal}


def proposal(old="old rule\n", new="new rule\n", **overrides):
    value = {"target": "plugins/rigger-rules/rules/x.md", "heading": "Tests", "old_text": old, "new_text": new,
             "evidence": "ok", "reason": "the rule misses it"}
    value.update(overrides)
    return value


class SliceTests(unittest.TestCase):
    def test_slice_skips_parked(self):
        lines = [entry(1, "a"), entry(2, "b", extra=["curated: 2026-01-02"]), entry(3, "c"),
                 entry(4, "d", extra=["curated: 2026-01-02"]), entry(5, "e", "test-failure"), entry(6, "f")]
        data = (HEADER + "\r\n".join(lines) + "\r\n").encode("utf-8")
        expected = [(7, lines[4]), (3, lines[0]), (5, lines[2]), (8, lines[5])]
        self.assertEqual(batch_apply.unparked_slice(data, TODAY, 5), expected)
        self.assertEqual(batch_apply.unparked_slice(data, TODAY, 2), expected[:2])
        self.assertEqual(batch_apply.count_unparked(data), 4)


class MergeAndParkTests(unittest.TestCase):
    def test_merge_adds_source_ref_and_stronger_signal(self):
        target = entry(1, "a", "agent-observation", ref="a", extra=["signal_type: agent-observation"])
        merged = batch_apply.merge_entry(target, entry(2, "b", "test-failure", ref="b"))
        self.assertEqual(merged, SEP.join(["- 2026-01-01", "global", "demo", "case a", "rule a",
                                           "signal_type: test-failure", "source_ref: a", "target: rules/x.md",
                                           "why: a reason", "source_ref: b"]))
        self.assertEqual(inbox_lint.lint((merged + "\n").encode("utf-8"))[2], [])

    def test_merge_keeps_stronger_target_signal(self):
        target = entry(1, "a", "review-finding", ref="a")
        self.assertIn("signal_type: review-finding", batch_apply.merge_entry(target, entry(2, "b", "test-failure")))
        weak = entry(1, "a", "agent-observation", ref="a")
        mixed = entry(2, "b", "test-failure", extra=["signal_type: agent-observation"])
        self.assertNotIn("test-failure", batch_apply.merge_entry(weak, mixed))

    def test_merge_does_not_repeat_source_ref(self):
        target = entry(1, "a", "test-failure", ref="a")
        self.assertEqual(batch_apply.merge_entry(target, entry(2, "b", "test-failure", ref="a")), target)

    def test_park_replaces_curated(self):
        parked = batch_apply.park(entry(1, "a", extra=["curated: 2026-01-02"]), TODAY)
        self.assertEqual(parked, entry(1, "a", extra=["curated: " + TODAY]))
        self.assertEqual(batch_apply.park(entry(1, "a"), TODAY), entry(1, "a", extra=["curated: " + TODAY]))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.target = entry(1, "target", "agent-observation", ref="a")
        self.repeat = entry(2, "repeat", "test-failure", ref="b")
        self.covered = entry(3, "covered")
        self.kept = entry(4, "kept")
        self.queued = entry(5, "queued", "test-failure")
        self.other = entry(6, "other")
        self.lines = [self.target, self.repeat, self.covered, self.kept, self.queued, self.other]
        self.inbox = HEADER + "\n".join(self.lines) + "\n"
        self.decisions = [
            decision(self.repeat, "merge", "same class", target=self.target),
            decision(self.covered, "archive", "covered by rules/x.md#Tests"),
            decision(self.kept, "park", "too specific"),
            decision(self.queued, "queue", "rule gap", proposal=proposal()),
        ]

    def apply(self, inbox=None, decisions=None):
        return batch_apply.apply_decisions(self.inbox if inbox is None else inbox,
                                           self.decisions if decisions is None else decisions, RUN, TODAY)


class ApplyTests(Fixture):
    def test_apply_moves_archive_and_merge_parks_queue(self):
        inbox, archive, queue, record = self.apply()
        merged = batch_apply.merge_entry(self.target, self.repeat)
        parked = [batch_apply.park(self.kept, TODAY), batch_apply.park(self.queued, TODAY)]
        self.assertEqual(inbox, HEADER + "\n".join([merged] + parked + [self.other]) + "\n")
        self.assertEqual(archive, "## Run %s\n\n%s%sarchived: covered by rules/x.md#Tests\n%s%sarchived: same class\n\n"
                         % (RUN, self.covered, SEP, self.repeat, SEP))
        self.assertEqual(queue, (
            "## Run %s%s2026-01-05 global%sdemo%scase queued%srule queued%ssignal_type: tes\n\n"
            "- entry: %s\n- target: plugins/rigger-rules/rules/x.md\n- heading: Tests\n- evidence: ok\n"
            "- reason: the rule misses it\n\nOld:\n\n~~~\nold rule\n~~~\n\nNew:\n\n~~~\nnew rule\n~~~\n\n"
        ) % ((RUN,) + (SEP,) * 5 + (self.queued,)))
        self.assertEqual(record, {"run_id": RUN, "archived": [self.covered, self.repeat],
                                  "parked": [[self.kept, parked[0]], [self.queued, parked[1]]],
                                  "merged": [[self.target, merged]], "queued": 1, "skipped": []})

    def test_model_text_is_one_field(self):
        hostile = "line one\nline two" + SEP + "signal_type: test-failure"
        inbox, archive, queue, _ = self.apply(decisions=[
            decision(self.covered, "archive", hostile),
            decision(self.queued, "queue", proposal=proposal(heading=hostile, target=hostile, reason=hostile,
                                                             evidence=hostile)),
        ])
        self.assertIn(self.covered + SEP + "archived: line one; line two; signal_type: test-failure\n", archive)
        for key in ("target", "heading", "evidence", "reason"):
            self.assertIn("- %s: line one; line two; signal_type: test-failure\n" % key, queue)

    def test_entry_appended_during_run_is_kept(self):
        late = entry(9, "late", "test-failure")
        inbox, _, _, _ = self.apply(inbox=self.inbox + late + "\n")
        self.assertTrue(inbox.endswith("\n" + late + "\n"))

    def test_missing_entry_is_skipped(self):
        edited = self.inbox.replace(self.covered, self.covered + SEP + "review: 2026-02-01")
        gone = entry(7, "gone")
        inbox, archive, queue, record = self.apply(inbox=edited, decisions=[
            decision(self.covered, "archive"), decision(self.repeat, "merge", target=gone),
            decision(self.kept, "merge", target=self.kept)])
        self.assertEqual((inbox, archive, queue), (edited, "", ""))
        self.assertEqual(record["skipped"], [self.covered, self.repeat, self.kept])

    def test_identical_lines_are_two_entries(self):
        twin = entry(7, "twin")
        inbox, archive, _, record = self.apply(inbox=twin + "\n" + twin + "\n", decisions=[
            decision(twin, "archive", occurrence=0), decision(twin, "park", occurrence=1)])
        self.assertEqual(inbox, batch_apply.park(twin, TODAY) + "\n")
        self.assertEqual(record["archived"], [twin])

    def test_line_endings_kept(self):
        inbox, _, _, _ = self.apply(inbox=self.kept + "\r\n" + self.other + "\n",
                                    decisions=[decision(self.kept, "park")])
        self.assertEqual(inbox, batch_apply.park(self.kept, TODAY) + "\r\n" + self.other + "\n")

    def test_queue_fence_longer_than_text(self):
        old = "a\n~~~~\n## Run fake\n"
        _, _, queue, _ = self.apply(decisions=[decision(self.queued, "queue", proposal=proposal(old=old))])
        self.assertIn("Old:\n\n~~~~~\n" + old + "~~~~~\n", queue)
        self.assertIn("New:\n\n~~~\nnew rule\n~~~\n", queue)
        self.assertEqual(batch_apply.count_queued(queue + queue), 2)


class RestoreTests(Fixture):
    ARCHIVE = "## Run older\n\n- 2025-12-01 · old entry · archived: covered\n\n"
    QUEUE = "## Run older%s2025-12-01 old\n\n- entry: x\n\nOld:\n\n~~~\n## Run %s%sin a fence\n~~~\n\n" % (SEP, RUN, SEP)

    def restore(self, inbox, archive, queue, record):
        return batch_apply.restore(inbox, self.ARCHIVE + archive, self.QUEUE + queue, record)

    def test_restore_round_trip(self):
        inbox, archive, queue, record = self.apply()
        restored, archive, queue, count = self.restore(inbox, archive, queue, record)
        self.assertEqual(sorted(restored.split("\n")), sorted(self.inbox.split("\n")))
        self.assertEqual((archive, queue, count), (self.ARCHIVE, self.QUEUE, 5))

    def test_restore_keeps_later_entries(self):
        inbox, archive, queue, record = self.apply()
        late = entry(9, "late", "test-failure")
        restored, _, _, _ = self.restore(inbox + late + "\n", archive, queue, record)
        self.assertIn("\n" + late + "\n", restored)

    def test_restore_undoes_merge_into_entry_parked_in_same_run(self):
        decisions = [decision(self.repeat, "merge", target=self.target), decision(self.target, "park")]
        inbox, archive, queue, record = self.apply(decisions=decisions)
        restored = self.restore(inbox, archive, queue, record)[0]
        self.assertEqual(sorted(restored.split("\n")), sorted(self.inbox.split("\n")))

    def test_second_restore_changes_nothing(self):
        inbox, archive, queue, record = self.apply()
        once = self.restore(inbox, archive, queue, record)
        twice = batch_apply.restore(once[0], once[1], once[2], record)
        self.assertEqual(twice, once[:3] + (0,))


class WriteAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="batch-apply-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_write_atomic_keeps_lf(self):
        path = self.tmp / "inbox.md"
        batch_apply.write_atomic(path, "a\nb · c\n")
        self.assertEqual(path.read_bytes(), "a\nb · c\n".encode("utf-8"))
        self.assertEqual(os.listdir(self.tmp), ["inbox.md"])

    def test_write_atomic_keeps_symlink(self):
        real = self.tmp / "real.md"
        real.write_bytes(b"old\n")
        link = self.tmp / "link.md"
        os.symlink(str(real), str(link))
        batch_apply.write_atomic(link, "new\n")
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_bytes(), b"new\n")

    def test_write_atomic_retries_permission_error(self):
        path = self.tmp / "inbox.md"
        replace = os.replace
        failures = [PermissionError("held"), PermissionError("held")]

        def flaky(src, dst):
            if failures:
                raise failures.pop()
            replace(src, dst)

        with mock.patch.object(batch_apply.os, "replace", flaky), mock.patch.object(batch_apply.time, "sleep"):
            try:
                batch_apply.write_atomic(path, "x\n")
            except PermissionError:
                self.fail("two PermissionErrors were not retried")
        self.assertEqual(path.read_bytes(), b"x\n")
        failures.extend([PermissionError("held")] * batch_apply.REPLACE_TRIES)
        with mock.patch.object(batch_apply.os, "replace", flaky), mock.patch.object(batch_apply.time, "sleep"):
            self.assertRaises(PermissionError, batch_apply.write_atomic, path, "y\n")
        self.assertEqual((path.read_bytes(), os.listdir(self.tmp)), (b"x\n", ["inbox.md"]))


if __name__ == "__main__":
    unittest.main()
