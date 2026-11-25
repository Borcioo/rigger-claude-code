"""slot_check: a reply checked against its slot's contract; the refusal rules of the reviewer contract.

python -I -X utf8 test_slot_check.py
"""
import contextlib
import copy
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
SCRIPT = os.path.join(TOOL_DIR, "slot_check.py")
sys.path.insert(0, TOOL_DIR)

import slot_check  # noqa: E402
import config  # noqa: E402  (on the path through review_scope)
import snapshot  # noqa: E402

REAL_RUN = subprocess.run


def remove_tree(path):
    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onerror=onerror)


def no_git(*args, **kwargs):
    raise AssertionError("git was started: %r" % (args[0] if args else kwargs.get("args"),))


def reviewer_doc(base="b" * 40, head="c" * 40, findings=None, in_scope=None, reviewed=None, skipped=None):
    """A reviewer reply that meets every shape rule; `findings` defaults to one anchored finding."""
    if findings is None:
        findings = [{"id": "F1", "file": "src/a.py", "line": 2, "severity": "Important",
                     "quote": "x = 1", "title": "a title"}]
    in_scope = ["src/a.py"] if in_scope is None else in_scope
    return {
        "contract": "rigger.reviewer/1",
        "status": "DONE",
        "base": base,
        "head": head,
        "findings": findings,
        "coverage": {"in_scope": in_scope, "reviewed": list(in_scope) if reviewed is None else reviewed,
                     "skipped": [] if skipped is None else skipped},
        "shortcuts": [],
        "shortcuts_check": "git diff --stat base..head -- tests/",
    }


def finding(file="src/a.py", line=2, quote="x = 1", **extra):
    row = {"id": "F1", "file": file, "line": line, "severity": "Minor", "quote": quote, "title": "t"}
    row.update(extra)
    return row


def as_text(obj):
    return json.dumps(obj)


class GitCase(unittest.TestCase):
    """A temp repository and a git environment free of the host's config for the test's own git calls."""

    def setUp(self):
        self.tmp = Path(os.path.abspath(tempfile.mkdtemp(prefix="slot-check-test-")))
        self.addCleanup(remove_tree, self.tmp)
        empty_config = self.tmp / "gitconfig"
        empty_config.write_bytes(b"")
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_") and key.upper() != "CLAUDE_CODE_SESSION_ID"}
        self.config_dir = self.tmp / "config"
        env.update(
            CLAUDE_CONFIG_DIR=str(self.config_dir),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
            GIT_CEILING_DIRECTORIES=str(self.tmp),
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test",
        )
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        proc = REAL_RUN(["git"] + list(args), cwd=str(self.repo), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8").strip()

    def write(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def check(self, obj, base, head, slot="reviewer", session=None):
        return slot_check.check(slot, as_text(obj), repo=self.repo, base=base, head=head, session=session)


class PathRuleTests(unittest.TestCase):
    def test_finding_path_rows(self):
        """Review Focus 2: each refused row gives `path` with no git started; each passing row gets past it."""
        refused = [
            ("empty", ""),
            ("absolute", "/etc/passwd"),
            ("parent segment", "../x.py"),
            ("parent segment", "a/../x.py"),
            ("parent segment", "a/.."),
            ("backslash", "a\\x.py"),
            ("colon", "C:" + "/x.py"),
            ("colon", "C:x.py"),
            ("colon", "a:b.py"),
        ]
        for row, value in refused:
            with self.subTest(row=row, value=value), mock.patch("subprocess.run", no_git):
                verdict = slot_check.check("reviewer", as_text(reviewer_doc(findings=[finding(file=value)])),
                                           repo=Path(HERE), base="b" * 40, head="c" * 40)
                self.assertEqual((verdict.ok, verdict.rule), (False, "path"), verdict)
        passing = ["etc/passwd", "a..b.py", "a/..b/x.py", "...py"]
        for value in passing:
            with self.subTest(row="passes", value=value), mock.patch("subprocess.run", no_git):
                verdict = slot_check.check("reviewer", as_text(reviewer_doc(findings=[finding(file=value)])),
                                           shape_only=True)
                self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)


class ShapeTests(unittest.TestCase):
    def shape(self, slot, text):
        with mock.patch("subprocess.run", no_git):
            return slot_check.check(slot, text, shape_only=True)

    def test_reviewer_wrong_shapes_are_refused_by_their_rule(self):
        def doc(**change):
            obj = reviewer_doc()
            for key, value in change.items():
                obj[key] = value
            return obj

        def cov(**change):
            obj = reviewer_doc()
            obj["coverage"].update(change)
            return obj

        def shortcuts_check_missing():
            obj = reviewer_doc()
            del obj["shortcuts_check"]
            return obj

        rows = [
            ("a list, not an object", [reviewer_doc()], "json"),
            ("contract missing", doc(contract=None), "contract"),
            ("status lower case", doc(status="done"), "status"),
            ("status not a string", doc(status=["DONE"]), "status"),
            ("findings not a list", doc(findings={"F1": finding()}), "finding-shape"),
            ("findings a number", doc(findings=1), "finding-shape"),
            ("findings missing", doc(findings=None), "finding-shape"),
            ("a finding not an object", doc(findings=["F1"]), "finding-shape"),
            ("id missing", doc(findings=[{k: v for k, v in finding().items() if k != "id"}]), "finding-shape"),
            ("title missing", doc(findings=[{k: v for k, v in finding().items() if k != "title"}]), "finding-shape"),
            ("severity missing", doc(findings=[{k: v for k, v in finding().items() if k != "severity"}]),
             "finding-shape"),
            ("file not a string", doc(findings=[finding(file=["a"])]), "finding-shape"),
            ("line a bool", doc(findings=[finding(line=True)]), "finding-shape"),
            ("line a string", doc(findings=[finding(line="2")]), "finding-shape"),
            ("line zero", doc(findings=[finding(line=0)]), "finding-shape"),
            ("line a float", doc(findings=[finding(line=2.0)]), "finding-shape"),
            ("end_line before line", doc(findings=[finding(line=5, end_line=4)]), "finding-shape"),
            ("end_line a bool", doc(findings=[finding(line=1, end_line=True)]), "finding-shape"),
            ("quote only whitespace", doc(findings=[finding(quote=" \t\n")]), "finding-shape"),
            ("repeated id", doc(findings=[finding(), finding(line=3)]), "finding-shape"),
            ("severity not a word", doc(findings=[finding(severity="High")]), "severity"),
            ("severity lower case", doc(findings=[finding(severity="minor")]), "severity"),
            ("coverage not an object", doc(coverage=["src/a.py"]), "coverage-sum"),
            ("in_scope holds a number", cov(in_scope=["src/a.py", 3], reviewed=["src/a.py", 3]), "coverage-sum"),
            ("skipped entry a string", cov(in_scope=["src/a.py", "b"], skipped=["b"]), "coverage-sum"),
            ("skipped file not a string", cov(skipped=[{"file": None, "reason": "x"}]), "coverage-sum"),
            ("a file missing from the sum", cov(in_scope=["src/a.py", "b.py"]), "coverage-sum"),
            ("a file outside in_scope", cov(reviewed=["src/a.py", "b.py"]), "coverage-sum"),
            ("a file both reviewed and skipped", cov(skipped=[{"file": "src/a.py", "reason": "x"}]), "coverage-sum"),
            ("in_scope repeats a file", cov(in_scope=["src/a.py", "src/a.py"]), "coverage-sum"),
            ("skip reason missing", cov(in_scope=["src/a.py", "b"], skipped=[{"file": "b"}]), "skip-reason"),
            ("skip reason empty", cov(in_scope=["src/a.py", "b"], skipped=[{"file": "b", "reason": ""}]),
             "skip-reason"),
            ("skip reason not a string", cov(in_scope=["src/a.py", "b"], skipped=[{"file": "b", "reason": 1}]),
             "skip-reason"),
            ("shortcuts empty, check missing", shortcuts_check_missing(), "shortcuts"),
            ("shortcuts empty, check blank", doc(shortcuts_check="  "), "shortcuts"),
            ("shortcuts not a list", doc(shortcuts="none"), "shortcuts"),
            ("shortcuts_check not a string", doc(shortcuts_check=["git"]), "shortcuts"),
        ]
        for label, obj, rule in rows:
            with self.subTest(label):
                verdict = self.shape("reviewer", as_text(obj))
                self.assertEqual((verdict.ok, verdict.rule), (False, rule), verdict)
        ok = doc(shortcuts=[{"file": "t.py", "line": 1, "kind": "x"}], shortcuts_check="")
        self.assertTrue(self.shape("reviewer", as_text(ok)).ok)
        self.assertEqual(self.shape("reviewer", as_text(reviewer_doc())).findings, 1)

    def test_other_slot_rows(self):
        summary = "## Summary\n\n- one\n- two\n- three\n"
        fields = "FILES: a.py\nROOT CAUSE: none\nGATE: ok\nRED RUNS: none\n"
        blocked = "QUESTION: q\nCALL: c\nOUTPUT: o\nALTERNATIVE: a\n"
        rows = [
            ("worker", "STATUS: DONE\n" + summary, True, ""),
            ("worker", "STATUS: NEEDS_CONTEXT\r\n" + summary.replace("\n", "\r\n"), True, ""),
            ("worker", "\nSTATUS: DONE\n" + summary, False, "status-line"),
            ("worker", "STATUS: DONE | PARTIAL\n" + summary, False, "status-line"),
            ("worker", "STATUS: DONE\n- one\n- two\n- three\n", False, "summary"),
            ("worker", "STATUS: DONE\nSummary:\n- one\n- two\n- three\n", False, "summary"),
            ("worker", "STATUS: DONE\n## Summary\n" + "- b\n" * 6, False, "summary"),
            ("worker", "STATUS: DONE\n## Summary\n- one\n- two\n## Next\n- three\n", False, "summary"),
            ("implementer", "STATUS: DONE\n" + fields, True, ""),
            ("implementer", "STATUS: BLOCKED\n" + fields + blocked, True, ""),
            ("implementer", "STATUS: DONE\nFILES: a\nROOT CAUSE: none\nGATE: ok\n", False, "fields"),
            ("implementer", "STATUS: DONE\nROOT CAUSE: none\nFILES: a\nGATE: ok\nRED RUNS: none\n", False, "fields"),
            ("implementer", "STATUS: BLOCKED\n" + fields + blocked.replace("ALTERNATIVE", "OTHER"), False,
             "blocked-fields"),
            ("implementer", "STATUS: NEEDS_CONTEXT\n" + fields, False, "blocked-fields"),
            ("mapper", "a.py:1 - x\nb/c.py:2-30 - y z\n", True, ""),
            ("mapper", "NOT FOUND: a, b\n", True, ""),
            ("mapper", "NOT FOUND: \n", False, "line-shape"),
            ("mapper", "", False, "line-shape"),
            ("mapper", "a.py:0 - x\n", False, "line-shape"),
            ("mapper", "a.py:5-4 - x\n", False, "line-shape"),
            ("mapper", "a.py:x - x\n", False, "line-shape"),
            ("mapper", ":3 - x\n", False, "line-shape"),
            ("mapper", "a.py:3 - \n", False, "line-shape"),
            ("mapper", "a.py:3 - " + " ".join(["w"] * 12) + "\n", True, ""),
            ("mapper", "a.py:3 - " + " ".join(["w"] * 13) + "\n", False, "words"),
            ("mapper", "NOT FOUND: a\nNOT FOUND: b\n", False, "not-found-alone"),
            ("shell-guard", "", True, ""),
            ("shell-guard", " \n", True, ""),
            ("shell-guard", "not json", False, "reply-shape"),
            ("shell-guard", "{} {}", False, "reply-shape"),
            ("shell-guard", 2 * json.dumps({"hookSpecificOutput": {"permissionDecision": "deny",
                                                                   "permissionDecisionReason": "g [a]: x"}}),
             False, "reply-shape"),
            ("shell-guard", json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}), False, "reply-shape"),
            ("shell-guard", json.dumps({"hookSpecificOutput": {"permissionDecision": "ask",
                                                               "permissionDecisionReason": "x [a]"}}),
             False, "reply-shape"),
            ("shell-guard", json.dumps({"hookSpecificOutput": {"permissionDecision": "deny",
                                                               "permissionDecisionReason": "g [-a]: x"}}),
             False, "deny-reason"),
            ("shell-guard", json.dumps({"hookSpecificOutput": {"permissionDecision": "deny",
                                                               "permissionDecisionReason": "g []: x"}}),
             False, "deny-reason"),
            ("shell-guard", json.dumps({"hookSpecificOutput": {"permissionDecision": "deny",
                                                               "permissionDecisionReason": "g [a.b_c-9]: x"}}),
             True, ""),
            ("lessons", "Lessons inbox: /x/lessons-inbox.md: 0 entries\n", True, ""),
            ("lessons", "Lessons inbox: /x: count unknown (OSError: x)\r\nmore\n", True, ""),
            ("lessons", "Lessons inbox: /x: 2 entries.\n", False, "inbox-line"),
            ("lessons", "Lessons inbox: : 2 entries\n", False, "inbox-line"),
            ("lessons", "", False, "inbox-line"),
        ]
        for slot, text, ok, rule in rows:
            with self.subTest(slot=slot, text=text[:60]):
                verdict = self.shape(slot, text)
                self.assertEqual((verdict.ok, verdict.rule), (ok, rule), verdict)

    def test_unknown_slot_is_refused(self):
        verdict = slot_check.check("testing", "STATUS: DONE\n")
        self.assertEqual((verdict.ok, verdict.rule), (False, "slot"), verdict)

    def test_the_last_json_block_counts(self):
        good, bad = reviewer_doc(), reviewer_doc()
        bad["status"] = "FINISHED"
        block = "```json\n%s\n```\n"
        verdict = self.shape("reviewer", "# Report\n\n" + block % as_text(bad) + "\ntext\n" + block % as_text(good))
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
        verdict = self.shape("reviewer", block % as_text(good) + "\n" + block % as_text(bad))
        self.assertEqual((verdict.ok, verdict.rule), (False, "status"), verdict)
        # a json fence inside another fenced block is that block's text, not a block of its own
        inner = "````text\n" + block % as_text(bad) + "````\n"
        verdict = self.shape("reviewer", block % as_text(good) + inner)
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
        # a shorter fence does not close a longer one, so the block after it is still found
        verdict = self.shape("reviewer", inner + block % as_text(good))
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
        self.assertEqual(slot_check.reviewer_json("~~~ JSON\n{\"a\": 1}\n~~~\n"), {"a": 1})

    def test_no_block_and_a_cut_block_are_refused_as_json(self):
        good = as_text(reviewer_doc())
        rows = [
            ("no block", "# Report\n\nNo findings.\n"),
            ("a block of another language", "```python\n%s\n```\n" % good),
            ("a cut block after a good one", "```json\n%s\n```\n\n```json\n{\"contract\": \"rigger." % good),
            ("a block that is not JSON", "```json\n{contract: 1}\n```\n"),
            ("an array", "```json\n[1, 2]\n```\n"),
            ("an empty text", ""),
        ]
        for label, text in rows:
            with self.subTest(label):
                verdict = self.shape("reviewer", text)
                self.assertEqual((verdict.ok, verdict.rule), (False, "json"), verdict)

    def test_text_over_the_size_cap_is_refused(self):
        body = as_text(reviewer_doc())
        at_cap = body + " " * (slot_check.TEXT_CAP - len(body))
        self.assertEqual(len(at_cap), 1000000)
        self.assertTrue(self.shape("reviewer", at_cap).ok)
        verdict = self.shape("reviewer", at_cap + " ")
        self.assertEqual((verdict.ok, verdict.rule), (False, "json"), verdict)
        verdict = self.shape("worker", "STATUS: DONE\n## Summary\n- a\n- b\n- c\n" + "x" * 1000000)
        self.assertEqual((verdict.ok, verdict.rule), (False, "status-line"), verdict)
        self.assertEqual(slot_check.reviewer_json(at_cap)["status"], "DONE")
        with self.assertRaisesRegex(slot_check.Refused, "over 1000000 characters"):
            slot_check.reviewer_json(at_cap + " ")

    def test_unknown_keys_are_ignored(self):
        for value in ("- a change\n", ["a", {"b": 1}], {"x": None}, None, 3):
            obj = reviewer_doc()
            obj["behaviour_changes"] = value
            obj["a_key_nobody_defined"] = {"nested": [value]}
            obj["findings"][0]["extra"] = value
            with self.subTest(value=value):
                self.assertTrue(self.shape("reviewer", as_text(obj)).ok)

    def test_an_exception_is_a_refusal(self):
        text = "STATUS: DONE\n## Summary\n- a\n- b\n- c\n"
        self.assertTrue(slot_check.check("worker", text).ok)

        def boom(_ctx):
            raise ValueError("planted")

        with mock.patch.object(slot_check, "rule_worker_summary", boom):
            verdict = slot_check.check("worker", text)
            self.assertEqual(verdict, slot_check.Verdict(False, "internal", "ValueError: planted", None))
            reply = Path(tempfile.mkdtemp(prefix="slot-check-cli-"))
            self.addCleanup(remove_tree, reply)
            (reply / "r.md").write_bytes(text.encode("utf-8"))
            out = io.BytesIO()
            with mock.patch.object(slot_check, "_out", out):
                code = slot_check.main(["worker", str(reply / "r.md")])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), b"slot-check: refused: internal: ValueError: planted\n")

    def test_fence_and_whitespace_backtracking_probe(self):
        rows = [
            ("fence openers", "```json\n" * 40, "json"),
            ("fence openers on one line", "`" * 40 + "json" + " " * 40 + "\n", "json"),
            ("tilde openers", "~~~~ json \t\n" * 40, "json"),
        ]
        for label, text, rule in rows:
            start = time.monotonic()
            verdict = self.shape("reviewer", text)
            took = time.monotonic() - start
            with self.subTest(label):
                self.assertEqual(verdict.rule, rule)
                self.assertLess(took, 0.5)
        start = time.monotonic()
        verdict = self.shape("shell-guard", json.dumps({"hookSpecificOutput": {
            "permissionDecision": "deny", "permissionDecisionReason": "[" + "a-" * 40 + " \t" * 40}}))
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(verdict.rule, "deny-reason")
        start = time.monotonic()
        verdict = self.shape("lessons", "Lessons inbox: " + ": " * 40 + "count unknown (" * 40 + "\n")
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(verdict.rule, "inbox-line")
        start = time.monotonic()
        self.assertEqual(slot_check.normalise(" \t" * 40 + "x" + " \t" * 40), "x")
        self.assertLess(time.monotonic() - start, 0.5)


class GitRuleTests(GitCase):
    def test_anchor_normalises_whitespace_and_line_endings(self):
        """Review Focus 2: CRLF, tabs and a quote over two lines anchor; a byte that is not UTF-8 is `anchor`."""
        base = self.commit("empty")
        self.write("crlf.txt", b"first\r\nsecond  line\r\nthird\r\n")
        self.write("tab.txt", b"def f():\n\tif  x:" + b"\n\t\treturn 1\n")
        self.write("two.txt", b"alpha beta\n    gamma delta\n")
        self.write("latin.txt", b"name = 'caf\xe9'\n")
        head = self.commit("files")
        scope = ["crlf.txt", "latin.txt", "tab.txt", "two.txt"]
        rows = [
            ("CRLF", finding(file="crlf.txt", line=2, quote="second line"), True, ""),
            ("CRLF, the line end is not quoted", finding(file="crlf.txt", line=2, quote="second line\r"), True, ""),
            ("tab indent quoted with spaces", finding(file="tab.txt", line=2, quote="if x:"), True, ""),
            ("a quote over two lines", finding(file="two.txt", line=1, end_line=2, quote="beta\n gamma"), True, ""),
            ("a byte that is not UTF-8", finding(file="latin.txt", line=1, quote="name = 'caf\u00e9'"), False,
             "anchor"),
            ("a file missing at head", finding(file="nope.txt", line=1, quote="x"), False, "anchor"),
        ]
        for label, row, ok, rule in rows:
            with self.subTest(label):
                verdict = self.check(reviewer_doc(base, head, findings=[row], in_scope=scope), base, head)
                self.assertEqual((verdict.ok, verdict.rule), (ok, rule), verdict)

    def test_anchor_window(self):
        base = self.commit("empty")
        self.write("w.txt", "".join("line %d of the file\n" % n for n in range(1, 21)).encode("utf-8"))
        head = self.commit("w")
        # the working copy changes after the commit: the anchor reads the commit, never the working tree
        self.write("w.txt", b"nothing here\n")
        quote = "line 10 of the file"
        rows = [(13, 13, True), (14, 14, False), (7, 7, True), (6, 6, False), (5, 7, True), (11, 30, True)]
        for line, end_line, ok in rows:
            with self.subTest(line=line, end_line=end_line):
                row = finding(file="w.txt", line=line, end_line=end_line, quote=quote)
                verdict = self.check(reviewer_doc(base, head, findings=[row], in_scope=["w.txt"]), base, head)
                self.assertEqual((verdict.ok, verdict.rule), (ok, "" if ok else "anchor"), verdict)

    def test_base_head_and_coverage_scope(self):
        self.write("a.py", b"x = 0\n")
        self.write("old.py", b"moved\n")
        self.write("gone.py", b"bye\n")
        base = self.commit("base")
        self.write("a.py", b"y = 0\nx = 1\n")
        os.rename(str(self.repo / "old.py"), str(self.repo / "new.py"))
        os.remove(str(self.repo / "gone.py"))
        head = self.commit("head")
        third = self.commit("third")
        scope = ["a.py", "gone.py", "new.py", "old.py"]
        skipped = [{"file": "gone.py", "reason": "deleted"}, {"file": "old.py", "reason": "deleted"}]
        row = finding(file="a.py", line=2, quote="x = 1")

        def doc(json_base=base, json_head=head, in_scope=scope, skip=skipped):
            reviewed = [p for p in in_scope if p not in [s["file"] for s in skip]]
            return reviewer_doc(json_base, json_head, findings=[row], in_scope=in_scope, reviewed=reviewed,
                                skipped=skip)

        rows = [
            ("ids", doc(), base, head, True, ""),
            ("revisions the caller gave by name", doc(), base, "HEAD~1", True, ""),
            ("short ids in the JSON", doc(json_base=base[:12], json_head=head[:12]), base, head, True, ""),
            ("JSON base names the head", doc(json_base=head), base, head, False, "base-head"),
            ("JSON head names another commit", doc(json_head=third), base, head, False, "base-head"),
            ("JSON base names nothing", doc(json_base="no-such-rev"), base, head, False, "base-head"),
            ("JSON base looks like an option", doc(json_base="--output=x"), base, head, False, "base-head"),
            ("JSON head not a string", doc(json_head=7), base, head, False, "base-head"),
            ("given head names nothing", doc(), base, "no-such-rev", False, "base-head"),
            ("the new name only", doc(in_scope=["a.py", "gone.py", "new.py"], skip=skipped[:1]), base, head, False,
             "coverage-scope"),
            ("an extra file", doc(in_scope=scope + ["b.py"], skip=skipped + [{"file": "b.py", "reason": "x"}]),
             base, head, False, "coverage-scope"),
        ]
        for label, obj, given_base, given_head, ok, rule in rows:
            with self.subTest(label):
                verdict = self.check(obj, given_base, given_head)
                self.assertEqual((verdict.ok, verdict.rule), (ok, rule), verdict)
        self.assertFalse((self.repo / "x").exists())

    def test_git_rules_need_their_inputs(self):
        head = self.commit("one")
        obj = reviewer_doc(head, head, findings=[], in_scope=[])
        for base, given_head in ((None, head), (head, None), (None, None)):
            with self.subTest(base=base, head=given_head), mock.patch("subprocess.run", no_git):
                verdict = slot_check.check("reviewer", as_text(obj), repo=self.repo, base=base, head=given_head)
                self.assertEqual((verdict.ok, verdict.rule), (False, "base-head"), verdict)
        with mock.patch("subprocess.run", no_git):
            verdict = slot_check.check("mapper", "a.py:1 - x\n", repo=self.repo)
        self.assertEqual((verdict.ok, verdict.rule), (False, "path-exists"), verdict)
        self.assertIn("needs the reviewed head", verdict.reason)

    def test_shape_only_skips_the_git_rules(self):
        obj = reviewer_doc("no-such-rev", "--output=x", findings=[finding(quote="not in any file")],
                           in_scope=["src/a.py"])
        with mock.patch("subprocess.run", no_git):
            self.assertTrue(slot_check.check("reviewer", as_text(obj), shape_only=True).ok)
            self.assertTrue(slot_check.check("mapper", "missing.py:1 - x\n", shape_only=True).ok)
        self.assertEqual(slot_check.GIT_RULES, frozenset(("base-head", "coverage-scope", "coverage-test-skipped",
                                                          "anchor", "path-exists")))
        verdict = slot_check.check("reviewer", as_text(obj), repo=self.repo, base="HEAD", head="HEAD")
        self.assertEqual((verdict.ok, verdict.rule), (False, "base-head"), verdict)

    def test_mapper_paths_exist_at_head(self):
        self.write("src/a.py", b"x = 1\n")
        self.write("src/dir/b.py", b"y = 1\n")
        head = self.commit("one")
        os.remove(str(self.repo / "src" / "a.py"))
        self.write("loose.py", b"untracked\n")
        rows = [
            ("a file at head, gone from the working tree", "src/a.py:1 - x\n", True, ""),
            ("two files", "src/a.py:1 - x\nsrc/dir/b.py:1-2 - y\n", True, ""),
            ("an untracked file", "loose.py:1 - x\n", False, "path-exists"),
            ("a folder", "src/dir:1 - x\n", False, "path-exists"),
            ("not found", "NOT FOUND: z\n", True, ""),
        ]
        for label, text, ok, rule in rows:
            with self.subTest(label):
                verdict = slot_check.check("mapper", text, repo=self.repo, head=head)
                self.assertEqual((verdict.ok, verdict.rule), (ok, rule), verdict)
        for text in ("../x.py:1 - x\n", "/etc/passwd:1 - x\n", "a\\b.py:1 - x\n", "C:x.py:1 - x\n"):
            with self.subTest(text=text), mock.patch("subprocess.run", no_git):
                verdict = slot_check.check("mapper", text, repo=self.repo, head=head)
                self.assertEqual((verdict.ok, verdict.rule), (False, "path-exists"), verdict)

    def test_git_runs_without_git_variables(self):
        self.write("a.py", b"x = 1\n")
        head = self.commit("one")
        seen = []

        def spy(args, **kwargs):
            seen.append((args, kwargs.get("env")))
            return REAL_RUN(args, **kwargs)

        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.tmp / "elsewhere"), "GIT_WORK_TREE": str(self.tmp)}), \
                mock.patch("subprocess.run", spy):
            verdict = slot_check.check("mapper", "a.py:1 - x\n", repo=self.repo, head=head)
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
        self.assertTrue(seen)
        for args, env in seen:
            self.assertTrue(os.path.isabs(args[0]), args)
            self.assertEqual([k for k in env if k.upper().startswith("GIT_")], [])


SESSION = "slot-check-session"


class TestSkipTests(GitCase):
    """D62: a reply never skips a test file that review_scope lists for review."""

    def diff(self, base_files, head_files):
        """Commit `base_files`, then `head_files` (None deletes); (base, head, every changed path)."""
        for name, data in base_files.items():
            self.write(name, data)
        base = self.commit("base")
        for name, data in head_files.items():
            if data is None:
                os.remove(str(self.repo / name))
            else:
                self.write(name, data)
        head = self.commit("head")
        return base, head, sorted(set(base_files) | set(head_files))

    def doc(self, base, head, in_scope, skip):
        """A reply reviewing every path of `in_scope` but those in `skip` (path -> reason)."""
        skipped = [{"file": path, "reason": reason} for path, reason in sorted(skip.items())]
        return reviewer_doc(base, head, findings=[], in_scope=in_scope,
                            reviewed=[path for path in in_scope if path not in skip], skipped=skipped)

    def standard_diff(self):
        base_files = {"tests/test_a.py": b"x = 0\n", "spec/helper.rb": b"a\n", "tests/old.bin": b"\x00old\n",
                      "src/app.py": b"a = 0\n", "tests/test_gone.py": b"gone\n",
                      "src/gen.py": b"# @generated\nv = 0\n", "yarn.lock": b"a\n"}
        head_files = {"tests/test_a.py": b"x = 1\n", "spec/helper.rb": b"b\n", "tests/old.bin": b"\x00new\n",
                      "tests/blob.bin": b"\x00blob\n", "src/app.py": b"a = 1\n", "tests/test_gone.py": None,
                      "src/gen.py": b"# @generated\nv = 1\n", "yarn.lock": b"b\n"}
        return self.diff(base_files, head_files)

    def test_a_skipped_test_file_is_refused(self):
        base, head, scope = self.standard_diff()
        rows = [
            ("a test-named file", "tests/test_a.py"),
            ("a file under a test folder", "spec/helper.rb"),
            ("a binary file added under a test folder (review: binary added)", "tests/blob.bin"),
        ]
        for label, path in rows:
            with self.subTest(label):
                verdict = self.check(self.doc(base, head, scope, {path: "not needed",
                                                                  "tests/test_gone.py": "deleted"}), base, head)
                self.assertEqual((verdict.ok, verdict.rule), (False, "coverage-test-skipped"), verdict)
                self.assertIn(path, verdict.reason)

    def test_a_skipped_generated_file_passes(self):
        """Skips of files review_scope does not list as a test to review pass, whatever reason the reply gives."""
        base, head, scope = self.standard_diff()
        rows = [
            ("a generated file", {"src/gen.py": "generated"}),
            ("a lockfile", {"yarn.lock": "lockfile"}),
            ("a deleted test file", {"tests/test_gone.py": "deleted"}),
            ("a binary file under a test folder, binary at base too", {"tests/old.bin": "binary"}),
            ("a file to review that is not a test", {"src/app.py": "out of scope for this review"}),
        ]
        for label, skip in rows:
            with self.subTest(label):
                verdict = self.check(self.doc(base, head, scope, skip), base, head)
                self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)

    def write_snapshot(self, l3):
        paths = {1: "core/rigger-defaults.json", 3: str(self.config_dir / "rigger" / "config.json"),
                 4: str(self.repo / ".claude" / "rigger.json")}
        got = config.resolve({"schema": 1}, [], l3, {}, paths)
        data = {key: [] for key in snapshot.SNAPSHOT_KEYS}
        data.update({
            "format": snapshot.SNAPSHOT_FORMAT,
            "config": {"effective": got.effective,
                       "sources": [[list(path), source] for path, source in sorted(got.sources.items())]},
            "modules": [],
        })
        self.assertIsNotNone(snapshot.write_snapshot(self.config_dir, SESSION, data))

    def test_a_test_file_a_path_rule_skips_passes(self):
        project = {"schema": 1, "review": {"paths": {"tests/data/**": {"skip": "fixture data"}}}}
        base, head, scope = self.diff(
            {".claude/rigger.json": json.dumps(project).encode("utf-8"), "tests/data/test_one.py": b"1\n",
             "tests/cases/test_two.py": b"2\n"},
            {"tests/data/test_one.py": b"one\n", "tests/cases/test_two.py": b"two\n"})
        scope.remove(".claude/rigger.json")
        self.write_snapshot({"schema": 1, "review": {"paths": {"tests/cases/**": {"skip": "case data"}}}})
        project_skip = self.doc(base, head, scope, {"tests/data/test_one.py": "fixture data"})
        session_skip = self.doc(base, head, scope, {"tests/cases/test_two.py": "case data"})
        rows = [
            ("a project entry at base", project_skip, None, True),
            ("a personal entry with its session", session_skip, SESSION, True),
            ("a personal entry without a session", session_skip, None, False),
            ("a personal entry with another session", session_skip, "another-session", False),
        ]
        for label, obj, session, ok in rows:
            with self.subTest(label):
                verdict = self.check(obj, base, head, session=session)
                self.assertEqual((verdict.ok, verdict.rule), (ok, "" if ok else "coverage-test-skipped"), verdict)
        reply = self.tmp / "reply.json"
        reply.write_bytes(as_text(session_skip).encode("utf-8"))
        cli = [sys.executable, "-I", "-X", "utf8", SCRIPT, "reviewer", str(reply), "--repo", str(self.repo),
               "--base", base, "--head", head]
        refused = "slot-check: refused: coverage-test-skipped: "
        cli_rows = [
            ("--session", ["--session", SESSION], {}, "slot-check: ok reviewer 0 findings\n"),
            ("the session id in the environment", [], {"CLAUDE_CODE_SESSION_ID": SESSION},
             "slot-check: ok reviewer 0 findings\n"),
            ("no session", [], {}, refused),
            ("--session over the environment", ["--session", "another-session"],
             {"CLAUDE_CODE_SESSION_ID": SESSION}, refused),
        ]
        for label, extra, env, out in cli_rows:
            with self.subTest(label), mock.patch.dict(os.environ, env):
                proc = REAL_RUN(cli + extra, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120)
                text = proc.stdout.decode("utf-8")
                self.assertEqual((proc.returncode, proc.stderr), (0 if out.endswith("findings\n") else 2, b""), text)
                self.assertTrue(text.startswith(out), text)

    def test_a_scope_error_is_this_rules_refusal(self):
        base, head, scope = self.standard_diff()
        obj = self.doc(base, head, scope, {"tests/test_gone.py": "deleted"})
        with mock.patch.object(slot_check.review_scope, "path_rules",
                               side_effect=slot_check.review_scope.ScopeError("planted")):
            verdict = self.check(obj, base, head)
        self.assertEqual((verdict.ok, verdict.rule, verdict.reason), (False, "coverage-test-skipped",
                                                                      "review_scope: planted"), verdict)


class CliTests(GitCase):
    def run_cli(self, *args):
        proc = REAL_RUN([sys.executable, "-I", "-X", "utf8", SCRIPT] + list(args), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8", "replace")

    def reply(self, name, text):
        path = self.tmp / name
        path.write_bytes(text.encode("utf-8"))
        return str(path)

    def test_cli_lines_and_exit_codes(self):
        self.write("src/a.py", b"x = 0\n")
        base = self.commit("base")
        self.write("src/a.py", b"y = 0\nx = 1\n")
        head = self.commit("head")
        good = self.reply("good.json", as_text(reviewer_doc(base, head)))
        worker = self.reply("w.md", "STATUS: DONE\n## Summary\n- a\n- b\n- c\n")
        rows = [
            (["reviewer", good, "--repo", str(self.repo), "--base", base, "--head", head], 0,
             "slot-check: ok reviewer 1 findings\n"),
            (["reviewer", good, "--shape-only"], 0, "slot-check: ok reviewer 1 findings\n"),
            (["worker", worker], 0, "slot-check: ok worker\n"),
            (["implementer", worker], 2, "slot-check: refused: fields: FILES missing\n"),
            (["reviewer", good, "--repo", str(self.repo), "--base", head, "--head", head], 2,
             "slot-check: refused: base-head: the reply's base %s is not the reviewed base %s\n" % (base, head)),
        ]
        for args, code, out in rows:
            with self.subTest(args=args[:2] + args[3:]):
                got = self.run_cli(*args)
                self.assertEqual(got, (code, out, ""))
        refusals = [
            ["reviewer", good],
            ["reviewer", good, "--base", base],
            ["worker"],
            ["worker", str(self.tmp / "missing.md")],
            ["worker", worker, "--bogus"],
            ["nothing", worker],
        ]
        for args in refusals:
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual((code, err), (2, ""))
                self.assertTrue(out.startswith("slot-check: refused: "), out)
                self.assertEqual(out.count("\n"), 1, out)


PRIVATE_USE = r"\breview_scope\._[A-Za-z]\w*"


class ReviewScopeUseTests(unittest.TestCase):
    def test_slot_check_uses_only_public_review_scope_names(self):
        import re
        self.assertEqual(re.findall(PRIVATE_USE, "x = review_scope._git(repo, args)"), ["review_scope._git"])
        with open(SCRIPT, "rb") as handle:
            source = handle.read().decode("utf-8")
        self.assertEqual(re.findall(PRIVATE_USE, source), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
