"""review_scope: the project's review.paths entries, read at base.

python -I -X utf8 test_review_scope_project.py
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from review_scope_case import (  # noqa: E402
    PathRuleCase,
    config,
    review_scope,
)


class ProjectRulesTests(PathRuleCase):
    """D61: level 4 is read at base, by object id, and handed out as files."""

    def base_and_out(self, head_files, paths=None):
        paths = paths or {"src/**": {"rules": "docs/rules.md"}}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"base rules\n",
                           "src/a.py": b"a = 1\n"}, "base")
        head = self.files(head_files, "head")
        out = self.tmp / "rules out"
        return base, head, out

    def test_project_entries_come_from_base(self):
        rows = [
            ("the config file", {".claude/rigger.json": self.project({"src/**": {"skip": "head says skip"}}),
                                 "src/a.py": b"a = 2\n"}, ".claude/rigger.json"),
            ("the rules file", {"docs/rules.md": b"head rules\n", "src/a.py": b"a = 2\n"}, "docs/rules.md"),
        ]
        for label, head_files, changed in rows:
            with self.subTest(label):
                self.git("checkout", "-q", "--orphan", "case-" + changed.replace("/", "-").strip("."))
                self.git("rm", "-rqf", "--ignore-unmatch", ".")
                base, head, out = self.base_and_out(head_files)
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo),
                                     "--rules-out", str(out))
                self.assertIn("review\tsrc/a.py", lines)
                self.assertIn("# review.paths: project entries read at base; %s changed in this diff" % changed,
                              lines)
                rules_lines = [line for line in lines if line.startswith("# rules ")]
                self.assertEqual(len(rules_lines), 1, lines)
                self.assertTrue(rules_lines[0].startswith("# rules src/** "), rules_lines)
                self.assertTrue(rules_lines[0].endswith(" (level 4)"), rules_lines)
                written = rules_lines[0][len("# rules src/** "):-len(" (level 4)")]
                self.same_file(written, out / "rules-1.md")
                self.assertEqual((out / "rules-1.md").read_bytes(), b"base rules\n")

    def test_rules_out_holds_the_base_text(self):
        base, head, out = self.base_and_out({"src/a.py": b"a = 2\n"})
        self.write("docs/rules.md", b"working tree rules\n")
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        self.assertEqual((out / "rules-1.md").read_bytes(), b"base rules\n")
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual(len(rules_lines), 1, lines)
        self.assertNotIn("docs/rules.md", rules_lines[0])
        self.assertFalse(any("changed in this diff" in line for line in lines), lines)
        without = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
        self.assertIn("# rules src/** (not written: no --rules-out) (level 4)", without)

    def test_project_entries_apply_without_a_snapshot(self):
        base = self.files({".claude/rigger.json": self.project({"gen/**": {"skip": "built"}}),
                           "gen/x.py": b"x = 1\n"}, "base")
        head = self.files({"gen/x.py": b"x = 2\n"}, "head")
        for extra in ((), ("--session", "no-such-session")):
            with self.subTest(extra=extra):
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), *extra)
                self.assertIn("skip: built (review.paths)\tgen/x.py", lines)
                self.assertIn("# review.paths: none (no snapshot)", lines)
                self.assertIn("# review-scope: files 1, review 0, skipped 1", lines)

    def test_rules_lines_name_matched_entries_only(self):
        paths = {"src/**": "docs/rules.md", "docs/*.txt": "docs/rules.md",
                 "gen/**": {"rules": "docs/rules.md", "skip": "built"}}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"r\n",
                           "src/a.py": b"a\n", "gen/g.py": b"g\n", "docs/unchanged.txt": b"u\n"}, "base")
        head = self.files({"src/a.py": b"a2\n", "gen/g.py": b"g2\n"}, "head")
        out = self.tmp / "out"
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual([line.split(" ")[2] for line in rules_lines], ["src/**"], lines)
        self.assertEqual(sorted(os.listdir(str(out))), ["rules-1.md"])

    def test_level_four_rules_file_outside_the_repository_is_dropped(self):
        (self.tmp / "outside.md").write_bytes(b"outside\n")
        paths = {"a/**": "../outside.md", "b/**": "docs/../../outside.md", "c/**": (self.tmp / "outside.md").as_posix(),
                 "d/**": "docs\\rules.md", "e/**": "./docs/rules.md", "f/**": "docs//rules.md"}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"r\n",
                           "a/x.py": b"1\n", "b/x.py": b"1\n", "c/x.py": b"1\n", "d/x.py": b"1\n", "e/x.py": b"1\n",
                           "f/x.py": b"1\n"}, "base")
        head = self.files({name: b"2\n" for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py", "e/x.py", "f/x.py")})
        notices = []
        self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        for glob in paths:
            reason = "outside the repository" if glob[0] in "abc" else "not a plain repository path"
            with self.subTest(glob=glob):
                self.assertIn("project entry %s dropped: rules file %s" % (glob, reason), notices)
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out",
                             str(self.tmp / "out"))
        self.assertEqual([line for line in lines if line.startswith("# rules ")], [])

    def test_level_four_rules_file_must_be_tracked_at_base(self):
        hostile = "docs/$(x); y.md"
        self.write(".claude/rigger.json", self.project({"a/**": "docs/untracked.md", "b/**": hostile,
                                                        "c/**": "docs/link.md", "d/**": "docs/big.md"}))
        self.write(hostile, b"hostile rules\n")
        self.write("docs/big.md", b"x" * (review_scope.TEXT_CAP + 1))
        for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py"):
            self.write(name, b"1\n")
        self.git("add", "-A")
        target = self.repo / "link-target.txt"
        target.write_bytes(b"docs/rules.md")
        link = self.git("hash-object", "-w", str(target))
        target.unlink()
        self.git("update-index", "--add", "--cacheinfo", "120000,%s,docs/link.md" % link)
        self.git("commit", "-q", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        self.write("docs/untracked.md", b"untracked rules\n")
        for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py"):
            self.write(name, b"2\n")
            self.git("add", name)
        self.git("commit", "-q", "-m", "head")
        head = self.git("rev-parse", "HEAD")
        out = self.tmp / "out"
        notices = []
        kept = review_scope.path_rules(None, self.repo, base, notices)
        self.assertEqual([item.glob for item in kept], ["b/**"])
        self.assertIn("project entry a/** dropped: rules file not tracked at base", notices)
        batch = []
        with self.git_calls(batch_input=batch) as calls:
            lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        self.assertIn("# review.paths: project entry a/** dropped: rules file not tracked at base", lines)
        self.assertIn("# review.paths: project entry c/** dropped: rules file not a regular file at base", lines)
        self.assertIn("# review.paths: project entry d/** dropped: rules file over %d bytes" % review_scope.TEXT_CAP,
                      lines)
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual(len(rules_lines), 1, lines)
        self.assertTrue(rules_lines[0].startswith("# rules b/** "), rules_lines)
        self.assertEqual((out / "rules-1.md").read_bytes(), b"hostile rules\n")
        self.assertTrue(calls)
        for argv, _env in calls:
            with self.subTest(argv=argv[1:]):
                for element in argv:
                    self.assertNotIn("$(x)", element)
                    self.assertNotIn("untracked", element)
                    self.assertNotIn("rules.md", element)
                if "ls-tree" in argv:
                    self.assertEqual(argv[-1], "--")
        fed = b"".join(batch).split(b"\n")
        self.assertEqual(fed[-1], b"")
        self.assertTrue(fed[:-1])
        for line in fed[:-1]:
            with self.subTest(line=line):
                self.assertRegex(line.decode("ascii", "replace"), r"\A[0-9a-f]{40}([0-9a-f]{24})?\Z")

    def test_level_four_entries_are_validated(self):
        rows = [
            ("a bad entry", self.project({"a/**": {}, "b/**": {"skip": "kept"}}),
             "config base:.claude/rigger.json review.paths a/** ignored: needs rules or skip", {"b/x.py"}),
            ("a line break in a reason", self.project({"b/**": {"skip": "x\nreview\tb/x.py"}}),
             "review.paths b/** ignored: skip holds a line break or control character", set()),
            ("not JSON", b"{nope", "not valid JSON", set()),
            ("schema 2", b'{"schema": 2, "review": {"paths": {"b/**": {"skip": "s"}}}}', "schema 2", set()),
            ("over the cap", self.project({"b/**": {"skip": "s"}}, pad="x" * review_scope.TEXT_CAP),
             "over %d bytes" % review_scope.TEXT_CAP, set()),
        ]
        for index, (label, body, notice, skipped) in enumerate(rows):
            with self.subTest(label):
                self.git("checkout", "-q", "--orphan", "case-%d" % index)
                self.git("rm", "-rqf", "--ignore-unmatch", ".")
                base = self.files({".claude/rigger.json": body, "a/x.py": b"1\n", "b/x.py": b"1\n"}, "base")
                head = self.files({"a/x.py": b"2\n", "b/x.py": b"2\n"}, "head")
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
                self.assertTrue(any(line.startswith("# review.paths: ") and notice in line for line in lines), lines)
                got = {line.split("\t")[1] for line in lines if line.startswith("skip: ")}
                self.assertEqual(got, skipped)

    def test_a_project_config_that_is_a_link_is_dropped(self):
        self.write("real.json", self.project({"a/**": {"skip": "s"}}))
        self.write("a/x.py", b"1\n")
        self.git("add", "-A")
        target = self.tmp / "target.txt"
        target.write_bytes(b"../real.json")
        link = self.git("hash-object", "-w", str(target))
        self.git("update-index", "--add", "--cacheinfo", "120000,%s,.claude/rigger.json" % link)
        self.git("commit", "-q", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        notices = []
        self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        self.assertEqual(notices, ["project config .claude/rigger.json dropped: not a regular file at base"])

    def test_rules_out_that_cannot_be_written_refuses(self):
        base, head, _out = self.base_and_out({"src/a.py": b"a = 2\n"})
        blocker = self.tmp / "a file"
        blocker.write_bytes(b"")
        code, text = self.cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(blocker))
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith("review-scope: refused: rules could not be written to "), text)

    def test_validate_level_is_what_checks_level_four(self):
        base = self.files({".claude/rigger.json": self.project({"b/**": {"skip": "s"}}), "b/x.py": b"1\n"}, "base")
        with mock.patch.object(config, "validate_level", return_value=({}, ["rigger: config w refused"])) as check:
            notices = []
            self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        self.assertEqual(check.call_args[0][1], 4)
        self.assertIn("config w refused", notices)


if __name__ == "__main__":
    unittest.main()
