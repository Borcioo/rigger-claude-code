"""Tests for tools/proposal_check.py: the staleness check of the proposal queue. Queues are built with
batch_apply._queue_block, the writer the background batch uses; every file lives in a temp dir."""

from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(PLUGIN / "tools")]

import batch_apply  # noqa: E402
import proposal_check  # noqa: E402

RULE = "- Every rule line here is long enough to count as an added line.\n"
COVERED = "- This sentence already lives in another rules file of the repo.\n"


def entry(tag):
    return "- 2026-01-02 · global · demo · case %s · rule %s · signal_type: test-failure · source_ref: s#%s" % (
        tag, tag, tag)


def block(tag, target, old, new, run="20400615-120000-1"):
    return batch_apply._queue_block(entry(tag), {"target": target, "heading": "## X", "evidence": "ok",
                                                 "reason": "r", "old_text": old, "new_text": new}, run)


class ProposalCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="proposal-check-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        rules = self.repo / "plugins" / "rigger-rules" / "rules"
        rules.mkdir(parents=True)
        (rules / "a.md").write_text("## A\n\n- old line once\n- twice\n- twice\n- the target holds this long line too\n",
                                    encoding="utf-8")
        (rules / "b.md").write_text("## B\n\n" + COVERED + "|---|---|---|---|---|---|\n", encoding="utf-8")
        self.queue = self.tmp / "cfg" / "lessons-proposals.md"
        self.queue.parent.mkdir()
        # The --archive lock lives in the config dir: never the real one.
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.queue.parent)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lock = self.queue.parent / "lessons-batch" / "lock"

    def run_check(self, text, *args, newline="\n"):
        self.queue.write_bytes(text.replace("\n", newline).encode("utf-8"))
        out = io.StringIO()
        with redirect_stdout(out):
            code = proposal_check.main([str(self.queue)] + list(args))
        return code, out.getvalue()

    def verdicts(self, text, *args, newline="\n"):
        code, out = self.run_check(text, *args, newline=newline)
        self.assertEqual(code, 0, out)
        return [line.split(":", 1)[0] for line in out.splitlines()[:-1]]

    def test_each_verdict(self):
        target = "plugins/rigger-rules/rules/a.md"
        absolute = (self.repo / target).as_posix()
        cases = [
            ("old text once", block("a", target, "- old line once", "- old line once\n" + RULE), "live"),
            ("old text absent", block("b", target, "- not there", RULE), "stale"),
            ("old text twice", block("c", target, "- twice", RULE), "stale"),
            ("target missing", block("d", "plugins/rigger-rules/rules/gone.md", "- x", RULE), "stale"),
            ("no target named", block("e", "", "- x", RULE), "stale"),
            ("absolute target", block("f", absolute, "- old line once", RULE), "live"),
            ("absolute target missing", block("k", (self.tmp / "gone.md").as_posix(), "- x", RULE), "stale"),
            ("new text applied", block("g", target, "- not there", "- old line once\n- twice"), "applied"),
            ("added lines covered elsewhere", block("h", target, "- old line once", "- old line once\n" + COVERED),
             "covered"),
            ("guard: no old text", block("i", target, "", RULE), "live"),
            ("a deletion whose new text sits inside the old", block("n", target, "- old line once\n- twice",
                                                                    "- old line once"), "live"),
            ("guard already in the target", block("o", target, "", "- old line once"), "applied"),
            ("a line only in the target itself is not cover elsewhere",
             block("l", target, "- old line once", "- old line once\n- the target holds this long line too"), "live"),
            ("punctuation lines are not evidence of cover",
             block("m", target, "- old line once", "- old line once\n|---|---|---|---|---|---|"), "live"),
            ("short added lines are not evidence of cover", block("j", target, "- old line once",
                                                                         "- old line once\n## B"), "live"),
        ]
        for name, text, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(self.verdicts(text, "--repo", str(self.repo)), [expected])

    def test_a_relative_target_without_a_repo_is_stale_never_live(self):
        code, out = self.run_check(block("a", "plugins/rigger-rules/rules/a.md", "- old line once", RULE))
        self.assertEqual(out.splitlines()[0].split(" (")[1], "target not found: plugins/rigger-rules/rules/a.md)")

    def test_a_repo_without_plugins_or_an_archive_without_a_repo_exits_2_before_any_write(self):
        text = block("a", "plugins/rigger-rules/rules/a.md", "- old line once", RULE)
        code, out = self.run_check(text, "--repo", str(self.tmp / "nowhere"), "--archive")
        self.assertEqual(code, 2)
        self.assertIn("holds no plugins/ folder", out)
        code, out = self.run_check(text, "--archive")
        self.assertEqual(code, 2)
        self.assertIn("refused: 1 targets are relative to the harness repo; pass --repo <clone>", out)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), text)
        self.assertFalse((self.queue.parent / "lessons-archive.md").exists())
        self.assertEqual(self.run_check(text)[0], 0, "a check without --archive still reports")

    def test_a_corpus_file_counts_as_cover(self):
        extra = self.tmp / "CLAUDE.md"
        extra.write_text("# Global\n" + RULE, encoding="utf-8")
        text = block("a", "plugins/rigger-rules/rules/a.md", "- old line once", RULE)
        self.assertEqual(self.verdicts(text, "--repo", str(self.repo)), ["live"])
        self.assertEqual(self.verdicts(text, "--repo", str(self.repo), "--corpus", str(extra)), ["covered"])

    def test_crlf_queue_and_a_fence_inside_the_text_parse(self):
        new = "- old line once\n~~~\ninner fence\n~~~\n" + RULE
        text = "# Proposals\n\n" + block("a", "plugins/rigger-rules/rules/a.md", "- old line once", new)
        self.assertEqual(self.verdicts(text, "--repo", str(self.repo), newline="\r\n"), ["live"])
        self.assertEqual(proposal_check.parse_blocks(text)[0]["new"], new.rstrip("\n"))

    def test_the_count_line(self):
        text = block("a", "plugins/rigger-rules/rules/a.md", "- old line once", RULE) + block("b", "x.md", "- x", RULE)
        code, out = self.run_check(text, "--repo", str(self.repo))
        self.assertEqual(out.splitlines()[-1], "checked 2: live 1, stale 1, applied 0, covered 0")

    def test_missing_or_shapeless_queue_exits_2_with_a_reason(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(proposal_check.main([str(self.tmp / "none.md")]), 2)
        self.assertIn("proposal_check: cannot read", out.getvalue())
        code, out = self.run_check("# Proposals\n\nsome text but no block\n")
        self.assertEqual(code, 2)
        self.assertIn("holds text but no `## Run ` block", out)
        self.assertEqual(self.run_check(""), (0, "checked 0: live 0, stale 0, applied 0, covered 0\n"))

    def test_archive_moves_only_blocks_that_are_not_live(self):
        live = block("a", "plugins/rigger-rules/rules/a.md", "- old line once", RULE)
        stale = block("b", "plugins/rigger-rules/rules/a.md", "- not there", RULE)
        archive = self.queue.parent / "lessons-archive.md"
        archive.write_text("# Archive\n", encoding="utf-8")
        code, _ = self.run_check("# Proposals\n\n" + stale + live, "--repo", str(self.repo), "--archive")
        self.assertEqual(code, 0)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "# Proposals\n\n" + live)
        self.assertRegex(archive.read_text(encoding="utf-8"),
                         r"\A# Archive\n## Check \d{8}-\d{6}\n\n" + re.escape(entry("b"))
                         + r" · archived: stale: old text found 0 times in the target\n\n\Z")

    def test_archive_takes_applied_and_covered_blocks_and_creates_a_missing_archive(self):
        target = "plugins/rigger-rules/rules/a.md"
        live = block("a", target, "- old line once", RULE)
        applied = block("g", target, "- not there", "- old line once\n- twice")
        covered = block("h", target, "- old line once", "- old line once\n" + COVERED)
        code, out = self.run_check(applied + live + covered, "--repo", str(self.repo), "--archive")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), live)
        archived = (self.queue.parent / "lessons-archive.md").read_text(encoding="utf-8")
        self.assertIn(entry("g") + " · archived: applied: ", archived)
        self.assertIn(entry("h") + " · archived: covered: ", archived)
        self.assertNotIn(entry("a"), archived)

    def test_archive_waits_for_a_running_batch_and_leaves_its_lock(self):
        stale = block("b", "plugins/rigger-rules/rules/a.md", "- not there", RULE)
        self.lock.parent.mkdir()
        self.lock.write_text("batch-run\n", encoding="utf-8")
        code, out = self.run_check(stale, "--repo", str(self.repo), "--archive")
        self.assertEqual(code, 1)
        self.assertIn("proposal_check: refused: a lessons batch holds", out)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), stale)
        self.assertFalse((self.queue.parent / "lessons-archive.md").exists())
        self.assertEqual(self.lock.read_text(encoding="utf-8"), "batch-run\n")

    def test_archive_releases_its_own_lock(self):
        stale = block("b", "plugins/rigger-rules/rules/a.md", "- not there", RULE)
        self.assertEqual(self.run_check(stale, "--repo", str(self.repo), "--archive")[0], 0)
        self.assertTrue(self.lock.parent.is_dir())
        self.assertFalse(self.lock.exists())

    def test_archive_writes_nothing_when_the_queue_changed(self):
        stale = block("b", "plugins/rigger-rules/rules/a.md", "- not there", RULE)
        archive = self.queue.parent / "lessons-archive.md"
        real_verdict = proposal_check.verdict

        def changing(*args):
            self.queue.write_text(stale + "\n<!-- edited meanwhile -->\n", encoding="utf-8")
            return real_verdict(*args)

        with mock.patch.object(proposal_check, "verdict", changing):
            code, out = self.run_check(stale, "--repo", str(self.repo), "--archive")
        self.assertEqual(code, 1)
        self.assertIn("proposal_check: refused:", out)
        self.assertFalse(archive.exists())
        self.assertIn("edited meanwhile", self.queue.read_text(encoding="utf-8"))

    def test_a_queue_change_during_the_queue_write_restores_the_archive(self):
        stale = block("b", "plugins/rigger-rules/rules/a.md", "- not there", RULE)
        archive = self.queue.parent / "lessons-archive.md"
        real_write = batch_apply.write_atomic

        def write(path, text, expected=None):
            if Path(path) == self.queue:
                raise batch_apply.Changed(str(path))
            return real_write(path, text, expected)

        with mock.patch.object(batch_apply, "write_atomic", write):
            self.assertEqual(self.run_check(stale, "--repo", str(self.repo), "--archive")[0], 1)
        self.assertFalse(archive.exists(), "an archive created for the move must go again")
        archive.write_text("# Archive\n", encoding="utf-8")
        with mock.patch.object(batch_apply, "write_atomic", write):
            code, _ = self.run_check(stale, "--repo", str(self.repo), "--archive")
        self.assertEqual(code, 1)
        self.assertEqual(archive.read_text(encoding="utf-8"), "# Archive\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
