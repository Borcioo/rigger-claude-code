"""review_scope: the company and personal review.paths entries from the session snapshot.

python -I -X utf8 test_review_scope_snapshot.py
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from review_scope_case import (  # noqa: E402
    SESSION,
    PathRuleCase,
    review_scope,
)


class SnapshotRulesTests(PathRuleCase):
    """D61: levels 2 and 3 from the session snapshot, an entry's level from its highest leaf."""

    def two(self, names):
        base = self.files({name: b"1\n" for name in names}, "base")
        head = self.files({name: b"2\n" for name in names}, "head")
        return base, head

    def test_an_entry_with_a_level_four_leaf_counts_as_level_four(self):
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "r.md").write_bytes(b"r\n")
        l3 = {"schema": 1, "review": {"paths": {"a/**": {"rules": "r.md"}, "b/**": {"skip": "personal skip"}}}}
        l4 = {"schema": 1, "review": {"paths": {"a/**": {"skip": "session folder skip"},
                                                "c/**": {"skip": "from the snapshot"}}}}
        data = self.write_snapshot(l3=l3, l4=l4)
        base, head = self.two(("a/x.py", "b/x.py", "c/x.py"))
        rules = review_scope.path_rules(data, self.repo, base)
        self.assertEqual([(item.glob, item.skip, item.level) for item in rules], [("b/**", "personal skip", 3)])
        self.assertEqual(self.verdicts(base, head, rules),
                         {"a/x.py": "review", "b/x.py": "skip: personal skip (review.paths)", "c/x.py": "review"})

    def test_no_snapshot_applies_no_personal_or_company_rule(self):
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "config.json").write_bytes(self.project({"a/**": {"skip": "personal"}}))
        self.write_snapshot(l3={"schema": 1, "review": {"paths": {"a/**": {"skip": "personal"}}}})
        base, head = self.two(("a/x.py",))
        self.assertEqual(review_scope.path_rules(None, self.repo, base), [])
        for extra in ((), ("--session", "another-session")):
            with self.subTest(extra=extra):
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), *extra)
                self.assertIn("review\ta/x.py", lines)
                self.assertIn("# review.paths: none (no snapshot)", lines)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": SESSION}):
            lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
        self.assertIn("skip: personal (review.paths)\ta/x.py", lines)

    def test_snapshot_entries_resolve_per_level(self):
        module = self.tmp / "mods" / "company-a"
        (module / "rules").mkdir(parents=True)
        (module / "rules" / "c.md").write_bytes(b"c\n")
        (self.tmp / "mods" / "outside.md").write_bytes(b"o\n")
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "p.md").write_bytes(b"p\n")
        l2 = [("company-a", {"review": {"paths": {"src/**": "rules/c.md", "out/**": "../outside.md"}}})]
        l3 = {"schema": 1, "review": {"paths": {"lib/**": "p.md", "gone/**": "missing.md"}}}
        self.write_snapshot(l2=l2, l3=l3, modules=[("company-a", module)])
        base, head = self.two(("src/a.py", "lib/b.py", "out/c.py", "gone/d.py"))
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--session", SESSION)
        rules_lines = {line.split(" ")[2]: line for line in lines if line.startswith("# rules ")}
        self.assertEqual(set(rules_lines), {"src/**", "lib/**"}, lines)
        self.assertTrue(rules_lines["src/**"].endswith(" (level 2)"))
        self.same_file(rules_lines["src/**"][len("# rules src/** "):-len(" (level 2)")], module / "rules" / "c.md")
        self.assertTrue(rules_lines["lib/**"].endswith(" (level 3)"))
        self.same_file(rules_lines["lib/**"][len("# rules lib/** "):-len(" (level 3)")],
                       self.config_dir / "rigger" / "p.md")
        self.assertIn("# review.paths: company entry out/** dropped: rules file outside the module", lines)
        self.assertIn("# review.paths: personal entry gone/** dropped: rules file not found", lines)

    def test_a_rules_file_resolves_by_the_level_that_set_it(self):
        module = self.tmp / "mods" / "company-a"
        (module / "rules").mkdir(parents=True)
        (module / "rules" / "c.md").write_bytes(b"c\n")
        (self.config_dir / "rigger" / "rules").mkdir(parents=True)
        (self.config_dir / "rigger" / "rules" / "c.md").write_bytes(b"personal c\n")
        l2 = [("company-a", {"review": {"paths": {"mix/**": {"rules": "rules/c.md"}}}}),
              ("company-b", {"review": {"paths": {"nomod/**": "rules/c.md"}}})]
        l3 = {"schema": 1, "review": {"paths": {"mix/**": {"skip": "personal"}}}}
        data = self.write_snapshot(l2=l2, l3=l3, modules=[("company-a", module)])
        base, _head = self.two(("mix/a.py",))
        notices = []
        rules = review_scope.path_rules(data, self.repo, base, notices)
        self.assertEqual([(item.glob, item.skip, item.level) for item in rules], [("mix/**", "personal", 3)])
        self.same_file(rules[0].rules, module / "rules" / "c.md")
        self.assertEqual(notices, ["company entry nomod/** dropped: module root unknown"])

    def test_snapshot_entries_are_checked_again(self):
        effective = {"review": {"paths": {"a/**": {}, "b/**": {"skip": "x\treview"}, "c/**": {"skip": "ok"},
                                          "d/**": {"skip": "no source"}}}}
        data = self.write_snapshot(l3={"schema": 1, "review": {"paths": {"a/**": {"skip": "s"}, "b/**": {"skip": "s"},
                                                                         "c/**": {"skip": "s"}}}},
                                   effective=effective)
        base, _head = self.two(("a/x.py",))
        notices = []
        rules = review_scope.path_rules(data, self.repo, base, notices)
        self.assertEqual([(item.glob, item.skip) for item in rules], [("c/**", "ok")])
        self.assertEqual(len(notices), 3, notices)
        self.assertIn("snapshot entry d/** dropped: its level is unknown", notices)

    def test_a_level_four_entry_replaces_a_lower_one_for_its_glob(self):
        l3 = {"schema": 1, "review": {"paths": {"src/**": {"skip": "personal"}, "lib/**": {"skip": "personal"}}}}
        data = self.write_snapshot(l3=l3)
        base = self.files({".claude/rigger.json": self.project({"src/**": "docs/r.md", "lib/**": None}),
                           "docs/r.md": b"r\n", "src/a.py": b"1\n", "lib/b.py": b"1\n"}, "base")
        head = self.files({"src/a.py": b"2\n", "lib/b.py": b"2\n"}, "head")
        rules = review_scope.path_rules(data, self.repo, base)
        self.assertEqual([(item.glob, item.rules, item.skip, item.level) for item in rules],
                         [("src/**", "docs/r.md", None, 4)])
        self.assertEqual(self.verdicts(base, head, rules), {"src/a.py": "review", "lib/b.py": "review"})


if __name__ == "__main__":
    unittest.main()
