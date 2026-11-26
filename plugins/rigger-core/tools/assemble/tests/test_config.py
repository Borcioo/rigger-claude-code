"""Tests for config.py: the four config levels, their notices and sources, and rigger-defaults.json."""

import copy
import json
import os
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
    "reviewer": {"kind": "agent", "target": "rigger-review:reviewer", "model": "by-rule"},
    "worker": {"kind": "agent", "target": "rigger-core:worker", "model": "by-rule"},
    "implementer": {"kind": "agent", "target": "rigger-core:implementer", "model": "pinned"},
    "mapper": {"kind": "agent", "target": "rigger-core:mapper", "model": "pinned"},
    "testing": {"kind": "skill", "target": "rigger-testing:testing"},
    "shell-guard": {"kind": "hook", "target": "rigger-shellwise"},
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
            "section-newline": {"before": "a", "load": ["x:y"], "section": "a\nb"},
            "section-number": {"before": "a", "load": ["x:y"], "section": 5},
            "surrogate-before":{"before": "a\ud800b", "load": ["x:y"]},
            "surrogate-load": {"before": "a", "load": ["x:a\udfff"]},
            "private-use": {"before": "a\ue000b", "load": ["x:y"]},
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

    def test_emoji_newer_than_the_running_unicode_data_is_accepted(self):
        # U+1FAE8 (Unicode 15) and U+1FAE9 (Unicode 16) are unassigned in Python 3.9's data (Unicode 13).
        row = {"before": "shaking \U0001fae8 face, tired \U0001fae9 face", "load": ["x:y"]}
        got = resolve(l4={"schema": 1, "triggers": {"p.emoji": row}})
        self.assertEqual(got.notices, [])
        self.assertEqual(got.effective["triggers"]["p.emoji"], row)

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

    def test_slots_trust_and_review_above_level_one_apply(self):
        # D32: slots (levels 2-4), trust (level 3), review (D61) and shell_guard (D76) take effect; company and
        # leak_guard stay recorded only. effective["slots"] is the plain merge, before the trust filter.
        own = os.path.abspath("sw")
        l3 = {
            "schema": 1,
            "slots": {"testing": {"target": "x:y"}},
            "trust": {"project_commands": ["reviewer"]},
            "review": {"paths": {"*.php": "rules/php.md"}},
            "shell_guard": {"shellwise_path": own},
            "leak_guard": {"denylist": "~/deny.txt"},
        }
        l4 = {"schema": 1, "company": "company-deploy",
              "slots": {"reviewer": {"kind": "command", "target": ["rv", "{out}"]}}}
        got = resolve(l3=l3, l4=l4)
        self.assertEqual(got.effective["slots"]["testing"]["target"], "x:y")
        self.assertEqual(got.effective["slots"]["reviewer"],
                         {"kind": "command", "target": ["rv", "{out}"], "model": "by-rule"})
        self.assertEqual(got.effective["trust"], {"project_commands": ["reviewer"]})
        self.assertEqual(got.effective["review"], {"paths": {"*.php": "rules/php.md"}})
        source3, source4 = "L3:" + PATHS[3], "L4:" + PATHS[4]
        self.assertEqual(got.sources[("review", "paths", "*.php")], source3)
        self.assertEqual(got.effective.get("shell_guard"), {"shellwise_path": own})
        self.assertEqual(
            got.inactive,
            {
                ("leak_guard", "denylist"): source3,
                ("company",): source4,
            },
        )
        self.assertEqual(got.notices, [])
        company = resolve(l2=[("company-a", {"slots": {"reviewer": {"model": "opus"}}})])
        self.assertEqual(company.inactive, {})
        self.assertEqual(company.effective["slots"]["reviewer"]["model"], "opus")

    def test_levels_are_kept_in_order(self):
        # Resolved.levels feeds slots.resolve_slots: cleaned data per level, lowest first, with the level-2
        # conflicts already removed and the recorded-only keys gone.
        a = ("company-a", {"slots": {"worker": {"model": "a"}, "mapper": {"model": "m"}}})
        b = ("company-b", {"slots": {"worker": {"model": "b"}}})
        l3 = {"schema": 1, "slots": {"testing": {"target": "x:y"}}, "trust": {"project_commands": ["reviewer"]},
              "leak_guard": {"denylist": "~/deny.txt"}, "nope": 1}
        l4 = {"schema": 1, "slots": {"reviewer": None}, "company": "c"}
        got = resolve(l2=[a, b], l3=l3, l4=l4)
        self.assertEqual([source for source, _ in got.levels],
                         ["L1:" + PATHS[1], "L2:company-a", "L2:company-b", "L3:" + PATHS[3], "L4:" + PATHS[4]])
        data = dict(got.levels)
        self.assertEqual(data["L1:" + PATHS[1]]["slots"], SPEC_SLOTS)
        self.assertEqual(data["L2:company-a"], {"slots": {"worker": {}, "mapper": {"model": "m"}}})
        self.assertEqual(data["L2:company-b"], {"slots": {"worker": {}}})
        self.assertEqual(data["L3:" + PATHS[3]], {"schema": 1, "slots": {"testing": {"target": "x:y"}},
                                                  "trust": {"project_commands": ["reviewer"]}})
        self.assertEqual(data["L4:" + PATHS[4]], {"schema": 1, "slots": {"reviewer": None}})

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

    def test_trust_outside_the_personal_file_is_refused(self):
        # D33: only the personal file (level 3) may trust a project command; a cloned project's file or a company
        # module setting `trust` is dropped with a notice.
        trust = {"project_commands": ["reviewer"]}
        rows = [
            ("project file", resolve(l4={"schema": 1, "trust": trust}),
             "rigger: config %s key trust not allowed at level 4; ignored" % PATHS[4]),
            ("company module", resolve(l2=[("company-a", {"trust": trust})]),
             "rigger: config module company-a key trust not allowed at level 2; ignored"),
        ]
        for row, got, notice in rows:
            with self.subTest(row=row):
                self.assertEqual(got.notices, [notice])
                self.assertNotIn("trust", got.effective)

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

    def test_load_entry_length(self):
        mine = {"before": "my way", "load": ["me:testing"]}
        at_cap = {"before": "a", "load": ["x:" + "a" * 198]}
        over = {"before": "a", "load": ["x:" + "a" * 199]}
        self.assertEqual((len(at_cap["load"][0]), len(over["load"][0])), (200, 201))
        kept = resolve(l4={"schema": 1, "triggers": {"mine": at_cap}})
        self.assertEqual((kept.effective["triggers"].get("mine"), kept.notices), (at_cap, []))
        got = resolve(l3={"schema": 1, "triggers": {"mine": mine}}, l4={"schema": 1, "triggers": {"mine": over}})
        self.assertEqual(got.notices, ["rigger: config %s trigger mine ignored: load entry over 200 characters" % PATHS[4]])
        self.assertEqual(got.effective["triggers"]["mine"], mine)
        self.assertEqual(got.sources[("triggers", "mine", "before")], "L3:" + PATHS[3])

    def test_row_cap_per_level(self):
        def rows(count, prefix):
            # Ids in descending order, so file order differs from sorted order.
            return {"%s%02d" % (prefix, i): {"before": "a", "load": ["x:y"]} for i in range(count - 1, -1, -1)}

        def cap_notice(where, count):
            return "rigger: config %s holds %d trigger rows; the last %d ignored (at most 50 per file)" % (
                where, count, count - 50)

        base = set(level_one()["triggers"])
        many = rows(51, "p")
        got = resolve(l4={"schema": 1, "triggers": many})
        self.assertEqual(got.notices, [cap_notice(PATHS[4], 51)])
        self.assertEqual(set(got.effective["triggers"]), base | set(list(many)[:50]))
        self.assertNotIn("p00", got.effective["triggers"])

        both = resolve(l3={"schema": 1, "triggers": rows(50, "m")}, l4={"schema": 1, "triggers": rows(50, "p")})
        self.assertEqual(both.notices, [])
        self.assertEqual(len(both.effective["triggers"]), len(base) + 100)

        with_null = dict([("rigger-core.debugging", None)] + list(rows(50, "p").items()))
        nulled = resolve(l4={"schema": 1, "triggers": with_null})
        self.assertEqual(nulled.notices, [cap_notice(PATHS[4], 51)])
        self.assertNotIn("rigger-core.debugging", nulled.effective["triggers"])
        self.assertNotIn("p00", nulled.effective["triggers"])
        self.assertIn("p01", nulled.effective["triggers"])

        broken = dict(list(rows(50, "p").items()) + [("bad", {"before": "a\nb", "load": ["x:y"]})])
        shaped = resolve(l4={"schema": 1, "triggers": broken})
        self.assertEqual(shaped.notices, ["rigger: config %s trigger bad ignored: before holds a line break or control character" % PATHS[4]])
        self.assertEqual(len(shaped.effective["triggers"]), len(base) + 50)

        l1 = level_one()
        l1["triggers"].update(rows(51, "d"))
        defaults = resolve(l1=l1)
        self.assertEqual(defaults.notices, [])
        self.assertEqual(len(defaults.effective["triggers"]), len(base) + 51)

        company = resolve(l2=[("company-a", {"triggers": rows(51, "c")})])
        self.assertEqual(company.notices, [cap_notice("module company-a", 51)])
        self.assertEqual(len(company.effective["triggers"]), len(base) + 50)
        self.assertNotIn("c00", company.effective["triggers"])


class ReviewPathsTests(unittest.TestCase):
    """D61: review.paths maps a glob to a rules file or {rules, skip}; a bad entry is dropped with one notice."""

    GOOD = {"src/**/*.py": "rules/py.md", "gen/**": {"skip": "generated by the build"},
            "api/*.ts": {"rules": "rules/ts.md", "skip": "x"}, "old/**": None}

    def test_good_entries_are_kept(self):
        for level in (3, 4):
            with self.subTest(level=level):
                clean, notices = config.validate_level({"review": {"paths": dict(self.GOOD)}}, level, "where")
                self.assertEqual((clean, notices), ({"review": {"paths": self.GOOD}}, []))
        edge = {"g" * 200: {"rules": "r.md", "skip": "s" * 200}}
        self.assertEqual(config.validate_level({"review": {"paths": edge}}, 4, "w"), ({"review": {"paths": edge}}, []))
        self.assertEqual(config.validate_level({"review": {"paths": None}}, 4, "w"), ({"review": {"paths": None}}, []))

    def test_bad_entries_are_dropped_one_by_one(self):
        rows = [
            ("not an object", "a/**", 5, "must be a rules file or an object"),
            ("a list", "a/**", ["r.md"], "must be a rules file or an object"),
            ("both keys missing", "a/**", {"other": "x"}, "unknown key"),
            ("empty object", "a/**", {}, "needs rules or skip"),
            ("glob over 200", "g" * 201, "r.md", "glob over 200 characters"),
            ("empty glob", "", "r.md", "glob is empty"),
            ("glob with a tab", "a\tb", "r.md", "glob holds a line break or control character"),
            ("glob with a line break", "a\nb", "r.md", "glob holds a line break or control character"),
            ("reason over 200", "a/**", {"skip": "s" * 201}, "skip over 200 characters"),
            ("empty reason", "a/**", {"skip": ""}, "skip must be a non-empty string"),
            ("reason a number", "a/**", {"skip": 1}, "skip must be a non-empty string"),
            ("reason with a line break", "a/**", {"skip": "x\n# rules"}, "skip holds a line break or control character"),
            ("empty rules", "a/**", "", "rules must be a non-empty string"),
            ("rules a number", "a/**", {"rules": 1}, "rules must be a non-empty string"),
            ("rules with a tab", "a/**", {"rules": "r\t.md"}, "rules holds a line break or control character"),
            ("rules string with a line break", "a/**", "r\n.md", "rules holds a line break or control character"),
        ]
        for label, glob, entry, reason in rows:
            with self.subTest(label):
                paths = {"keep/**": "k.md", glob: entry}
                clean, notices = config.validate_level({"review": {"paths": paths}}, 4, "repo/.claude/rigger.json")
                self.assertEqual(clean, {"review": {"paths": {"keep/**": "k.md"}}})
                self.assertEqual(len(notices), 1, notices)
                self.assertIn("review.paths", notices[0])
                self.assertIn(reason, notices[0])
                self.assertNotIn("\n", notices[0])

    def test_paths_must_be_an_object_and_review_keys_are_known(self):
        clean, notices = config.validate_level({"review": {"paths": ["a"]}}, 4, "w")
        self.assertEqual((clean, len(notices)), ({"review": {}}, 1))
        self.assertIn("review.paths must be an object", notices[0])
        clean, notices = config.validate_level({"review": {"other": 1, "paths": {"a": "r.md"}}}, 3, "w")
        self.assertEqual(clean, {"review": {"paths": {"a": "r.md"}}})
        self.assertEqual(len(notices), 1)
        self.assertIn("review key other unknown", notices[0])

    def test_at_most_fifty_entries_per_level(self):
        paths = {"g%02d/**" % index: "r.md" for index in range(51)}
        clean, notices = config.validate_level({"review": {"paths": paths}}, 4, "w")
        self.assertEqual(list(clean["review"]["paths"]), ["g%02d/**" % index for index in range(50)])
        self.assertEqual(len(notices), 1)
        self.assertIn("holds 51 review.paths entries", notices[0])
        fifty = {"g%02d/**" % index: "r.md" for index in range(50)}
        self.assertEqual(config.validate_level({"review": {"paths": fifty}}, 4, "w")[1], [])

    def test_resolve_drops_a_bad_entry_with_its_level_file(self):
        got = resolve(l3={"schema": 1, "review": {"paths": {"a/**": {}, "b/**": "r.md"}}})
        self.assertEqual(got.effective["review"], {"paths": {"b/**": "r.md"}})
        self.assertEqual(len(got.notices), 1)
        self.assertIn(PATHS[3], got.notices[0])


class TurnGuardTests(unittest.TestCase):
    """D89: turn_guard.enabled, levels 2-4; a bad value is dropped with one notice, so the default stays."""

    def test_enabled_applies_at_levels_two_to_four(self):
        for value in (True, False):
            got = resolve(l4={"schema": 1, "turn_guard": {"enabled": value}})
            self.assertEqual((got.effective.get("turn_guard"), got.notices), ({"enabled": value}, []))
        got = resolve(l2=[("acme", {"turn_guard": {"enabled": False}})], l3={"schema": 1, "turn_guard": {"enabled": True}})
        self.assertEqual(got.effective["turn_guard"], {"enabled": True})
        self.assertEqual(got.sources[("turn_guard", "enabled")], "L3:" + PATHS[3])
        got = resolve(l2=[("acme", {"turn_guard": {"enabled": False}})])
        self.assertEqual(got.effective["turn_guard"], {"enabled": False})
        clean, notices = config.validate_level({"turn_guard": {"enabled": False}}, 1, "w")
        self.assertEqual((clean, len(notices)), ({}, 1))
        self.assertIn("not allowed at level 1", notices[0])

    def test_bad_value_keeps_the_default_with_one_notice(self):
        for value in ("no", 0, 1, [], {"x": 1}):
            with self.subTest(value=value):
                got = resolve(l3={"schema": 1, "turn_guard": {"enabled": value}})
                self.assertEqual(got.effective.get("turn_guard"), {})
                self.assertEqual(len(got.notices), 1, got.notices)
                self.assertIn("turn_guard.enabled must be true or false", got.notices[0])
                self.assertIn(PATHS[3], got.notices[0])
        clean, notices = config.validate_level({"turn_guard": {"enabled": True, "poll": 9}}, 4, "w")
        self.assertEqual(clean, {"turn_guard": {"enabled": True}})
        self.assertEqual(len(notices), 1)
        self.assertIn("turn_guard key poll unknown", notices[0])
        clean, notices = config.validate_level({"turn_guard": "off"}, 4, "w")
        self.assertEqual((clean, len(notices)), ({}, 1))


class ShellGuardTests(unittest.TestCase):
    """D76: shell_guard.shellwise_path takes effect at level 3 only; every other level drops it with a notice."""

    def test_level_three_path_applies(self):
        path = os.path.abspath("own-shellwise")
        got = resolve(l3={"schema": 1, "shell_guard": {"shellwise_path": path}})
        self.assertEqual(got.effective.get("shell_guard"), {"shellwise_path": path})
        self.assertEqual(got.sources[("shell_guard", "shellwise_path")], "L3:" + PATHS[3])
        self.assertEqual((got.inactive, got.notices), ({}, []))

    def test_other_levels_drop_it_with_a_notice(self):
        block = {"shell_guard": {"shellwise_path": os.path.abspath("own-shellwise")}}
        cases = (
            (dict(l1=dict(level_one(), **block)), "rigger: config %s key shell_guard not allowed at level 1; ignored"
             % PATHS[1]),
            (dict(l2=[("company-a", block)]), "rigger: config module company-a key shell_guard not allowed at level 2; "
             "ignored"),
            (dict(l4=dict({"schema": 1}, **block)), "rigger: config %s key shell_guard not allowed at level 4; ignored"
             % PATHS[4]),
        )
        for kwargs, notice in cases:
            with self.subTest(level=notice):
                got = resolve(**kwargs)
                self.assertNotIn("shell_guard", got.effective)
                self.assertEqual((got.inactive, got.notices), ({}, [notice]))

    def test_bad_value_is_dropped_with_one_notice(self):
        for value, word in (("own-shellwise", "must be an absolute path"), (5, "must be an absolute path"),
                            ("", "must be an absolute path")):
            with self.subTest(value=value):
                got = resolve(l3={"schema": 1, "shell_guard": {"shellwise_path": value}})
                self.assertEqual(got.effective.get("shell_guard"), {})
                self.assertEqual(len(got.notices), 1, got.notices)
                self.assertIn("shell_guard.shellwise_path " + word, got.notices[0])
                self.assertIn(PATHS[3], got.notices[0])
        got = resolve(l3={"schema": 1, "shell_guard": {"path": "x"}})
        self.assertEqual(got.effective.get("shell_guard"), {})
        self.assertEqual(got.notices, ["rigger: config %s shell_guard key path unknown; ignored" % PATHS[3]])


class LoadTests(unittest.TestCase):
    """D80: load.min_free_gb (a number, 0-1024) and load.max_heavy_agents (a whole number, 1-8), levels 1-4;
    a bad value is dropped with one notice, so mem_check's per-system default stays."""

    def test_good_values_apply_at_every_level(self):
        good = {"min_free_gb": 2.5, "max_heavy_agents": 1}
        for edge in ({"min_free_gb": 0, "max_heavy_agents": 8}, {"min_free_gb": 1024, "max_heavy_agents": 1}):
            clean, notices = config.validate_level({"load": edge}, 3, "w")
            self.assertEqual((clean, notices), ({"load": edge}, []))
        got = resolve(l1=dict(level_one(), load={"min_free_gb": 9}), l2=[("acme", {"load": {"max_heavy_agents": 3}})])
        self.assertEqual((got.effective["load"], got.notices), ({"min_free_gb": 9, "max_heavy_agents": 3}, []))
        got = resolve(l3={"schema": 1, "load": {"min_free_gb": 6}}, l4={"schema": 1, "load": good})
        self.assertEqual((got.effective["load"], got.notices), (good, []))
        self.assertEqual(got.sources[("load", "min_free_gb")], "L4:" + PATHS[4])
        self.assertEqual(got.inactive, {})
        self.assertEqual([source for source, data in got.levels if "load" in data], ["L3:" + PATHS[3], "L4:" + PATHS[4]])

    def test_a_bad_value_is_dropped_with_one_notice(self):
        rows = [({"min_free_gb": "6"}, "load.min_free_gb must be a number from 0 to 1024"),
                ({"min_free_gb": True}, "load.min_free_gb must be a number from 0 to 1024"),
                ({"min_free_gb": -0.5}, "load.min_free_gb must be a number from 0 to 1024"),
                ({"min_free_gb": 1024.5}, "load.min_free_gb must be a number from 0 to 1024"),
                ({"min_free_gb": float("nan")}, "load.min_free_gb must be a number from 0 to 1024"),
                ({"max_heavy_agents": 2.0}, "load.max_heavy_agents must be a whole number from 1 to 8"),
                ({"max_heavy_agents": False}, "load.max_heavy_agents must be a whole number from 1 to 8"),
                ({"max_heavy_agents": 0}, "load.max_heavy_agents must be a whole number from 1 to 8"),
                ({"max_heavy_agents": 9}, "load.max_heavy_agents must be a whole number from 1 to 8"),
                ({"heavy": 1}, "load key heavy unknown")]
        for value, notice in rows:
            with self.subTest(value=value):
                got = resolve(l3={"schema": 1, "load": value})
                self.assertEqual(got.effective.get("load"), {})
                self.assertEqual(len(got.notices), 1, got.notices)
                self.assertIn(notice, got.notices[0])
                self.assertIn(PATHS[3], got.notices[0])
        got = resolve(l4={"schema": 1, "load": [4]})
        self.assertNotIn("load", got.effective)
        self.assertIn("key load must be an object", got.notices[0])
        clean, notices = config.validate_level({"load": {"min_free_gb": None}}, 4, "w")
        self.assertEqual((clean, notices), ({"load": {"min_free_gb": None}}, []))


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
