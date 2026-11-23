"""Tests for config.py: the four config levels, their notices and sources, and rigger-defaults.json."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import config  # noqa: E402

CORE_ROOT = HERE.parents[2]

# Spec section 7.5, level 1, with the three trigger rows of the plan's Task 10.
SPEC_SLOTS = {
    "reviewer": {"kind": "agent", "target": "rigger-core:reviewer", "model": "by-rule"},
    "worker": {"kind": "agent", "target": "rigger-core:worker", "model": "by-rule"},
    "implementer": {"kind": "agent", "target": "rigger-core:implementer", "model": "pinned"},
    "mapper": {"kind": "agent", "target": "rigger-core:mapper", "model": "pinned"},
    "testing": {"kind": "skill", "target": "rigger-core:testing"},
    "shell-guard": {"kind": "hook", "target": "rigger-windows", "requires": ["os:windows"]},
    "lessons": {"kind": "hook", "target": "rigger-lessons"},
    "process": {
        "brainstorm": {"kind": "skill", "target": "superpowers:brainstorming"},
        "plans": {"kind": "skill", "target": "superpowers:writing-plans"},
        "tdd": {"kind": "skill", "target": "superpowers:test-driven-development"},
        "debug": {"kind": "skill", "target": "superpowers:systematic-debugging"},
    },
}

TESTING_ROW = {
    "section": "skills.skill-triggers",
    "before": "writing code or tests, reviewing tests, a spike, port or rehearsal",
    "load": ["slot:testing", "slot:process.tdd"],
}


def level_one():
    return {
        "schema": 1,
        "slots": copy.deepcopy(SPEC_SLOTS),
        "triggers": {
            "rigger-core.testing": copy.deepcopy(TESTING_ROW),
            "rigger-core.debugging": {
                "section": "skills.skill-triggers",
                "before": "a bug, a failing test or unexpected behaviour",
                "load": ["slot:process.debug"],
            },
        },
    }


PATHS = {1: "core/rigger-defaults.json", 3: "cfg/rigger/config.json", 4: "repo/.claude/rigger.json"}


def resolve(l2=(), l3=None, l4=None, l1=None):
    return config.resolve(l1 or level_one(), list(l2), l3 or {}, l4 or {}, PATHS)


class LoadLevelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="config "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, body):
        path = self.tmp / "config.json"
        path.write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
        return path

    def test_missing_file_is_empty_without_notice(self):
        self.assertEqual(config.load_level(self.tmp / "nope.json", 3), ({}, []))

    def test_parse_error_notice(self):
        path = self.write(b"{nope")
        data, notices = config.load_level(path, 3)
        self.assertEqual(data, {})
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].startswith("rigger: config %s ignored: " % path), notices[0])

    def test_schema_missing_or_other_counts_as_absent(self):
        for body in ({"triggers": {}}, {"schema": 2}, {"schema": True}, [1]):
            with self.subTest(body=body):
                data, notices = config.load_level(self.write(body), 3)
                self.assertEqual(data, {})
                self.assertEqual(len(notices), 1)
                self.assertIn("ignored", notices[0])

    def test_deep_nesting_is_one_notice(self):
        rows = [
            ("objects 1200", ('{"schema":1,"review":' + '{"a":' * 1200 + "1" + "}" * 1200 + "}").encode("utf-8")),
            ("lists 100000", b"[" * 100000 + b"]" * 100000),
            ("objects 33", ('{"schema":1,"review":' + '{"a":' * 32 + "1" + "}" * 32 + "}").encode("utf-8")),
        ]
        for label, body in rows:
            with self.subTest(label):
                data, notices = config.load_level(self.write(body), 4)
                self.assertEqual(data, {})
                self.assertEqual(len(notices), 1, notices)
                self.assertIn("nested deeper than", notices[0])
        body = ('{"schema":1,"review":' + '{"a":' * 31 + "1" + "}" * 31 + "}").encode("utf-8")
        data, notices = config.load_level(self.write(body), 4)
        self.assertEqual((data["schema"], notices), (1, []))

    def test_valid_file_with_bom(self):
        body = b"\xef\xbb\xbf" + json.dumps({"schema": 1, "expects": ["x"]}).encode("utf-8")
        self.assertEqual(config.load_level(self.write(body), 4), ({"schema": 1, "expects": ["x"]}, []))


class ResolveTests(unittest.TestCase):
    def test_no_files_is_level_one(self):
        got = resolve()
        self.assertEqual(got.effective, level_one())
        self.assertEqual((got.inactive, got.notices), ({}, []))
        self.assertEqual(got.sources[("slots", "testing", "target")], "L1:" + PATHS[1])
        self.assertEqual(got.sources[("triggers", "rigger-core.testing", "load")], "L1:" + PATHS[1])

    def test_deep_level_passed_to_resolve_is_one_notice(self):
        deep = 1
        for _ in range(1200):
            deep = {"a": deep}
        for kwargs in ({"l3": {"schema": 1, "review": deep}}, {"l2": [("company-a", {"review": deep})]}):
            with self.subTest(list(kwargs)[0]):
                got = resolve(**kwargs)
                self.assertEqual(got.effective, level_one())
                self.assertEqual(len(got.notices), 1, got.notices)
                self.assertIn("nested deeper than", got.notices[0])
                self.assertEqual(got.inactive, {})

    def test_inputs_are_not_mutated(self):
        l1, l4 = level_one(), {"schema": 1, "triggers": {"rigger-core.debugging": None}}
        before = (copy.deepcopy(l1), copy.deepcopy(l4))
        config.resolve(l1, [], {}, l4, PATHS)
        self.assertEqual((l1, l4), before)
        project = {"schema": 1, "triggers": {"mine": {"before": "a", "load": ["x:y"]}}}
        got = config.resolve(l1, [], {}, project, PATHS)
        got.effective["triggers"]["mine"]["load"].append("x:z")
        self.assertEqual(project["triggers"]["mine"]["load"], ["x:y"])

    def test_trigger_row_shape(self):
        rows = {
            "newline": {"before": "a\nb", "load": ["x:y"]},
            "carriage": {"before": "a\rb", "load": ["x:y"]},
            "nel": {"before": "a\x85b", "load": ["x:y"]},
            "line-sep": {"before": "a b", "load": ["x:y"]},
            "para-sep": {"before": "a b", "load": ["x:y"]},
            "control": {"before": "a\x01b", "load": ["x:y"]},
            "pipe": {"before": "a | b", "load": ["x:y"]},
            "load-string": {"before": "a", "load": "x:y"},
            "load-empty": {"before": "a", "load": []},
            "load-five": {"before": "a", "load": ["x:a", "x:b", "x:c", "x:d", "x:e"]},
            "load-injected": {"before": "a", "load": ["x:y\n## Injected"]},
            "load-pipe": {"before": "a", "load": ["x|y"]},
            "load-number": {"before": "a", "load": [1]},
            "too-long": {"before": "a" * 301, "load": ["x:y"]},
            "before-missing": {"load": ["x:y"]},
            "before-number": {"before": 5, "load": ["x:y"]},
            "not-object": "x:y",
            "requires-unknown": {"before": "a", "load": ["x:y"], "requires": ["os:beos"]},
            "requires-object": {"before": "a", "load": ["x:y"], "requires": {"os:linux": 1}},
            "requires-newline": {"before": "a", "load": ["x:y"], "requires": ["plugin:a\nb"]},
            "id\nline":{"before": "a", "load": ["x:y"]},
            "id|pipe": {"before": "a", "load": ["x:y"]},
            "surrogate-before": {"before": "a\ud800b", "load": ["x:y"]},
            "surrogate-load": {"before": "a", "load": ["x:a\udfff"]},
            "private-use": {"before": "a\ue000b", "load": ["x:y"]},
            "unassigned": {"before": "a\U000e0080b", "load": ["x:y"]},
            "id\ud800surrogate": {"before": "a", "load": ["x:y"]},
        }
        for row_id, row in rows.items():
            with self.subTest(row_id.encode("unicode_escape").decode("ascii")):
                got = resolve(l4={"schema": 1, "triggers": {row_id: row}})
                self.assertNotIn(row_id, got.effective["triggers"])
                self.assertEqual(len(got.notices), 1, got.notices)
                self.assertIn(PATHS[4], got.notices[0])
                self.assertIn(row_id.split("\n")[0].split("\ud800")[0], got.notices[0])
                self.assertNotIn("\n", got.notices[0])
                got.notices[0].encode("utf-8")
        longest = resolve(l4={"schema": 1, "triggers": {"ok": {"before": "a" * 300, "load": ["a:1", "a:2", "a:3", "a:4"]}}})
        self.assertEqual(longest.notices, [])
        self.assertIn("ok", longest.effective["triggers"])
        broken = resolve(l4={"schema": 1, "triggers": {"rigger-core.testing": {"before": "a\nb", "load": ["x:y"]}}})
        self.assertEqual(broken.effective["triggers"]["rigger-core.testing"], TESTING_ROW)
        self.assertEqual(broken.sources[("triggers", "rigger-core.testing", "before")], "L1:" + PATHS[1])

    def test_notice_never_carries_a_line_break_from_a_project_key(self):
        got = resolve(l4={"schema": 1, "x\n## Injected": 1, "triggers": {"y z": {"load": []}}})
        self.assertEqual(len(got.notices), 2, got.notices)
        for notice in got.notices:
            self.assertNotIn("\n", notice)
            self.assertNotIn(" ", notice)

    def test_surrogate_in_a_key_or_expects_is_escaped_or_refused(self):
        got = resolve(l4={"schema": 1, "k\ud800": 1, "expects": ["ok", "x\ud800", "a\nb"]})
        self.assertEqual(len(got.notices), 2, got.notices)
        self.assertNotIn("expects", got.effective)
        for notice in got.notices:
            self.assertNotIn("\ud800", notice)
            notice.encode("utf-8")

    def test_surrogate_rows_never_reach_table_or_snapshot(self):
        import render
        import snapshot

        project = {
            "schema": 1,
            "k\ud800": 1,
            "triggers": {"p.a": {"before": "a\ud800b", "load": ["x:y"]}, "p\ud800": {"before": "a", "load": ["x:y"]},
                         "p.c": {"before": "c", "load": ["x:a\ud800"]}, "p.ok": {"before": "ok", "load": ["x:y"]}},
        }
        got = resolve(l4=project)
        rows = render.collect_rows([], got.effective)
        self.assertEqual([row.id for row in rows], ["rigger-core.testing", "rigger-core.debugging", "p.ok"])
        self.assertIn(b"| ok | `x:y` |", render.table(rows, SPEC_SLOTS))
        tmp = Path(tempfile.mkdtemp(prefix="config snapshot "))
        self.addCleanup(shutil.rmtree, tmp, True)
        data = {key: [] for key in snapshot.SNAPSHOT_KEYS}
        data.update({"format": snapshot.SNAPSHOT_FORMAT, "config": got.effective, "notices": got.notices})
        self.assertIsNotNone(snapshot.write_snapshot(tmp, "s1", data))

    def test_slots_above_level_one_are_recorded_not_applied(self):
        l3 = {
            "schema": 1,
            "slots": {"testing": {"target": "x:y"}},
            "trust": {"project_commands": ["reviewer"]},
            "review": {"paths": {"*.php": "rules/php.md"}},
            "shell_guard": {"shellwise_path": "/opt/sw"},
            "leak_guard": {"denylist": "~/deny.txt"},
        }
        l4 = {"schema": 1, "company": "company-deploy"}
        got = resolve(l3=l3, l4=l4)
        self.assertEqual(got.effective["slots"]["testing"]["target"], "rigger-core:testing")
        self.assertEqual(got.effective["slots"], SPEC_SLOTS)
        source3, source4 = "L3:" + PATHS[3], "L4:" + PATHS[4]
        self.assertEqual(
            got.inactive,
            {
                ("slots", "testing", "target"): source3,
                ("trust", "project_commands"): source3,
                ("review", "paths", "*.php"): source3,
                ("shell_guard", "shellwise_path"): source3,
                ("leak_guard", "denylist"): source3,
                ("company",): source4,
            },
        )
        self.assertEqual(got.notices, [])
        company = resolve(l2=[("company-a", {"slots": {"reviewer": {"model": "opus"}}})])
        self.assertEqual(company.inactive, {("slots", "reviewer", "model"): "L2:company-a"})
        self.assertEqual(company.effective["slots"]["reviewer"]["model"], "by-rule")

    def test_merge_rules(self):
        base = {"a": {"x": 1, "y": [1, 2]}, "b": 2, "c": {"z": 3}}
        over = {"a": {"x": None, "y": [3]}, "b": {"n": 1}, "c": None, "d": [4]}
        merged = config.merge(base, over)
        self.assertEqual(merged, {"a": {"y": [3]}, "b": {"n": 1}, "d": [4]})
        self.assertEqual(base, {"a": {"x": 1, "y": [1, 2]}, "b": 2, "c": {"z": 3}})
        self.assertEqual(over["a"], {"x": None, "y": [3]})

    def test_l2_conflict_neither_applies(self):
        a = ("company-a", {"triggers": {"rigger-core.testing": {"before": "a way", "load": ["a:x"]}}})
        b = ("company-b", {"triggers": {"rigger-core.testing": {"before": "b way", "load": ["b:x"]}}})
        got = resolve(l2=[a, b])
        self.assertEqual(got.effective["triggers"]["rigger-core.testing"], TESTING_ROW)
        self.assertEqual(len(got.notices), 1)
        for word in ("company-a", "company-b", "rigger-core.testing"):
            self.assertIn(word, got.notices[0])
        alone = resolve(l2=[a])
        self.assertEqual(alone.effective["triggers"]["rigger-core.testing"], {"before": "a way", "load": ["a:x"]})
        self.assertEqual(alone.sources[("triggers", "rigger-core.testing", "before")], "L2:company-a")

    def test_trigger_row_replaces_whole(self):
        row = {"before": "my way", "load": ["me:testing"]}
        got = resolve(l3={"schema": 1, "triggers": {"rigger-core.testing": row}})
        self.assertEqual(got.effective["triggers"]["rigger-core.testing"], row)
        self.assertNotIn(("triggers", "rigger-core.testing", "section"), got.sources)
        partial = resolve(l3={"schema": 1, "triggers": {"rigger-core.testing": {"before": "only this"}}})
        self.assertEqual(partial.effective["triggers"]["rigger-core.testing"], TESTING_ROW)
        self.assertEqual(len(partial.notices), 1)
        self.assertIn("rigger-core.testing", partial.notices[0])

    def test_unknown_key_and_wrong_type_notices(self):
        got = resolve(l3={"schema": 1, "nope": True})
        self.assertEqual(len(got.notices), 1)
        self.assertIn("nope", got.notices[0])
        self.assertIn(PATHS[3], got.notices[0])
        self.assertNotIn("nope", got.effective)
        wrong = resolve(l3={"schema": 1, "triggers": []})
        self.assertEqual(len(wrong.notices), 1)
        self.assertIn("triggers", wrong.notices[0])
        self.assertEqual(wrong.effective["triggers"], level_one()["triggers"])
        expects = resolve(l4={"schema": 1, "expects": ["ok", 3]})
        self.assertEqual(len(expects.notices), 1)
        self.assertNotIn("expects", expects.effective)

    def test_level_restricted_keys(self):
        got = resolve(l3={"schema": 1, "expects": ["x"]}, l4={"schema": 1, "leak_guard": {"denylist": "d"}})
        self.assertEqual(len(got.notices), 2, got.notices)
        self.assertIn(PATHS[3], got.notices[0])
        self.assertIn("expects", got.notices[0])
        self.assertIn(PATHS[4], got.notices[1])
        self.assertIn("leak_guard", got.notices[1])
        self.assertNotIn("expects", got.effective)
        self.assertEqual(got.inactive, {})
        allowed = resolve(l4={"schema": 1, "expects": ["x"]})
        self.assertEqual((allowed.effective["expects"], allowed.notices), (["x"], []))

    def test_sources_name_the_level_file(self):
        row = {"before": "writing meeting notes", "load": ["notes-style"]}
        got = resolve(l3={"schema": 1, "triggers": {"personal.notes": row, "rigger-core.testing": row}})
        self.assertEqual(got.sources[("triggers", "personal.notes", "before")], "L3:" + PATHS[3])
        self.assertEqual(got.sources[("triggers", "rigger-core.testing", "load")], "L3:" + PATHS[3])
        self.assertEqual(got.sources[("triggers", "rigger-core.debugging", "before")], "L1:" + PATHS[1])

    def test_trigger_null_removes_row(self):
        got = resolve(l4={"schema": 1, "triggers": {"rigger-core.debugging": None}})
        self.assertNotIn("rigger-core.debugging", got.effective["triggers"])
        self.assertIn("rigger-core.testing", got.effective["triggers"])
        self.assertFalse(any(path[:2] == ("triggers", "rigger-core.debugging") for path in got.sources))
        self.assertEqual(got.notices, [])


class DefaultsTests(unittest.TestCase):
    def test_defaults_file_matches_spec(self):
        path = config.defaults_path(CORE_ROOT)
        self.assertEqual(path, CORE_ROOT / "rigger-defaults.json")
        data, notices = config.load_level(path, 1)
        self.assertEqual(notices, [])
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["slots"], SPEC_SLOTS)
        self.assertEqual(
            list(data["triggers"]), ["rigger-core.plans", "rigger-core.testing", "rigger-core.debugging"]
        )
        for row in data["triggers"].values():
            self.assertEqual(row["section"], "skills.skill-triggers")
        self.assertEqual(
            data["triggers"]["rigger-core.plans"],
            {
                "section": "skills.skill-triggers",
                "before": "new behaviour, a spec or a plan, in a side session too",
                "load": ["slot:process.brainstorm", "slot:process.plans"],
            },
        )
        self.assertEqual(data["triggers"]["rigger-core.testing"], TESTING_ROW)
        self.assertEqual(resolve(l1=data).notices, [])


if __name__ == "__main__":
    unittest.main()
