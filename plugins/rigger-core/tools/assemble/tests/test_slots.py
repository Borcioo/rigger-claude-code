"""Tests for slots.py: slot resolution, entry validation, the trust filter and the Slots table."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import manifest  # noqa: E402
import slots  # noqa: E402

CORE_ROOT = HERE.parent.parent.parent
DEFAULT_SLOTS = json.loads((CORE_ROOT / "rigger-defaults.json").read_bytes().decode("utf-8"))["slots"]
L1 = "L1:rigger-defaults.json"
L3_FILE = "cfg/rigger/config.json"
L4_FILE = "repo/.claude/rigger.json"
ENABLED = frozenset(("rigger-core", "rigger-review", "rigger-testing", "rigger-shellwise", "rigger-lessons",
                     "superpowers"))
COMMAND = ["reviewer-cli", "--out", "{out}"]
EMPTY_SLOT = "the slot is empty"
RLO = chr(0x202E)  # right-to-left override, category Cf
LS = chr(0x2028)  # line separator, category Zl


def notice_l4_command(path):
    return ("rigger: .claude/rigger.json may not set a command for slot %s; add it to "
            "trust.project_commands in %s to allow it" % (path, L3_FILE))


class SlotsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="slots "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.core = self.module("rigger-core", agents=("reviewer", "worker", "implementer", "mapper"),
                                skills=("testing",))
        self.shellwise = self.module("rigger-shellwise", fills="shell-guard", failure_mode="closed")
        self.lessons = self.module("rigger-lessons", fills="lessons")
        self.review = self.module("rigger-review", agents=("reviewer",), fills="reviewer")
        self.testing = self.module("rigger-testing", skills=("testing",), fills="testing")
        self.modules = [self.core, self.review, self.testing, self.shellwise, self.lessons]

    def module(self, name, agents=(), skills=(), fills=None, failure_mode=None):
        root = self.tmp / name
        root.mkdir()
        for agent in agents:
            (root / "agents").mkdir(exist_ok=True)
            (root / "agents" / (agent + ".md")).write_bytes(b"agent\n")
        for skill in skills:
            (root / "skills" / skill).mkdir(parents=True)
            (root / "skills" / skill / "SKILL.md").write_bytes(b"skill\n")
        return manifest.Module(
            name=name, root=root, layer="general", fills=fills, provides={}, rules=[], triggers=[],
            subagents={}, config=None, lessons=None, tests={}, marketplace="rigger", origin="test",
            failure_mode=failure_mode,
        )

    def resolve(self, l2=(), l3=None, l4=None, enabled=ENABLED, os_name="windows", modules=None, l1=None):
        levels = [(L1, {"schema": 1, "slots": copy.deepcopy(DEFAULT_SLOTS)} if l1 is None else l1)]
        levels += [("L2:" + name, data) for name, data in l2]
        if l3 is not None:
            levels.append(("L3:" + L3_FILE, l3))
        if l4 is not None:
            levels.append(("L4:" + L4_FILE, l4))
        rows, trusted, notices = slots.resolve_slots(
            levels, self.modules if modules is None else modules, frozenset(enabled), os_name, L3_FILE)
        return {row.path: row for row in rows}, [row.path for row in rows], trusted, notices


def level(slot_values, **other):
    data = {"schema": 1, "slots": slot_values}
    data.update(other)
    return data


class ResolutionTests(SlotsTestCase):
    def test_defaults_give_eleven_rows_on_every_system(self):
        expected = [
            ("reviewer", "agent", "rigger-review:reviewer", "by-rule", (), None),
            ("worker", "agent", "rigger-core:worker", "by-rule", (), None),
            ("implementer", "agent", "rigger-core:implementer", "pinned", (), None),
            ("mapper", "agent", "rigger-core:mapper", "pinned", (), None),
            ("testing", "skill", "rigger-testing:testing", None, (), None),
            ("shell-guard", "hook", "rigger-shellwise", None, ("rigger-shellwise",), "closed"),
            ("lessons", "hook", "rigger-lessons", None, ("rigger-lessons",), None),
            ("process.brainstorm", "skill", "superpowers:brainstorming", None, (), None),
            ("process.plans", "skill", "superpowers:writing-plans", None, (), None),
            ("process.tdd", "skill", "superpowers:test-driven-development", None, (), None),
            ("process.debug", "skill", "superpowers:systematic-debugging", None, (), None),
        ]
        for os_name in ("windows", "linux", "macos"):
            with self.subTest(os=os_name):
                rows, order, trusted, notices = self.resolve(os_name=os_name)
                self.assertEqual(order, [item[0] for item in expected])
                self.assertEqual(len(order), 11)
                for path, kind, target, model, fillers, mode in expected:
                    row = rows[path]
                    self.assertEqual((row.kind, row.target, row.model, row.source, row.fillers, row.failure_mode,
                                      row.filled), (kind, target, model, "default", fillers, mode, True), path)
                self.assertEqual((trusted, notices), (frozenset(), []))

    def test_fallback_walks_down_with_one_notice(self):
        rows, _, _, notices = self.resolve(l3=level({"worker": {"target": "rigger-core:mapper"}}),
                                           l4=level({"worker": {"target": "rigger-core:nobody"}}))
        self.assertEqual((rows["worker"].target, rows["worker"].source, rows["worker"].filled),
                         ("rigger-core:mapper", "personal", True))
        self.assertEqual(notices, ["rigger: slot worker from %s ignored: rigger-core has no agent nobody; "
                                   "using personal" % L4_FILE])

    def test_fallback_names_the_level_it_lands_on(self):
        cases = [
            # (l2, l3, l4, expected target, source, notice tail)
            ((), level({"worker": {"target": "rigger-core:gone"}}), level({"worker": {"model": "opus"}}),
             "rigger-core:worker", "default", "from %s ignored: rigger-core has no agent gone; using default" % L4_FILE),
            ([("acme", level({"worker": {"target": "rigger-core:mapper"}}))], None,
             level({"worker": {"kind": "skill"}}), "rigger-core:mapper", "company:acme",
             "from %s ignored: kind skill not allowed for worker; using company:acme" % L4_FILE),
            ([("acme", level({"worker": {"target": "rigger-core:gone"}}))], None, None,
             "rigger-core:worker", "default", "from module acme ignored: rigger-core has no agent gone; using default"),
            ((), level({"worker": None}), level({"worker": {"model": "opus"}}),
             None, "personal", "from %s ignored: kind null not allowed for worker; the slot is empty" % L4_FILE),
            ((), None, level({"worker": "rigger-core:mapper"}),
             "rigger-core:worker", "default", "from %s ignored: must be an object or null; using default" % L4_FILE),
        ]
        for l2, l3, l4, target, source, tail in cases:
            with self.subTest(tail=tail):
                rows, _, _, notices = self.resolve(l2=l2, l3=l3, l4=l4)
                row = rows["worker"]
                self.assertEqual((row.target, row.source, row.filled), (target, source, target is not None))
                self.assertEqual(notices, ["rigger: slot worker " + tail])

    def test_level_one_default_of_a_plugin_that_is_not_enabled_is_empty_and_quiet(self):
        rows, order, _, notices = self.resolve(enabled=ENABLED - {"superpowers"})
        self.assertEqual(len(order), 11)
        for path in ("process.brainstorm", "process.plans", "process.tdd", "process.debug"):
            self.assertEqual((rows[path].filled, rows[path].kind, rows[path].target, rows[path].source),
                             (False, None, None, "default"), path)
        self.assertEqual(notices, [])
        rows, _, _, notices = self.resolve(l3=level({"process": {"tdd": {"target": "superpowers:tdd"}}}),
                                           enabled=ENABLED - {"superpowers"})
        self.assertEqual((rows["process.tdd"].filled, rows["process.tdd"].source), (False, "personal"))
        self.assertEqual(notices, ["rigger: slot process.tdd from %s ignored: plugin superpowers is not enabled; "
                                   "the slot is empty" % L3_FILE])

    def test_level_one_default_broken_for_another_reason_says_so(self):
        core = self.module("core-copy", agents=("reviewer",))
        core.name = "rigger-core"
        testing = self.module("testing-copy")
        testing.name = "rigger-testing"
        rows, _, _, notices = self.resolve(modules=[core, testing, self.shellwise, self.lessons])
        self.assertFalse(rows["testing"].filled)
        self.assertIn("rigger: slot testing from rigger-defaults.json ignored: rigger-testing has no skill testing; "
                      + EMPTY_SLOT, notices)

    def test_levels_without_slots_are_quiet(self):
        defaults, _, _, _ = self.resolve()
        rows, _, _, notices = self.resolve(l3={"schema": 1, "expects": ["rigger-wp"]}, l4={"schema": 1})
        self.assertEqual((rows, notices), (defaults, []))
        rows, _, _, notices = self.resolve(l4=["slots"])
        self.assertEqual((rows, notices), (defaults, []))

    def test_level_labels_are_checked(self):
        for label in ("L5:x", "nolabel", "l1:x"):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    slots.resolve_slots([(label, {})], self.modules, ENABLED, "windows", L3_FILE)

    def test_model_is_kept_for_agents_and_commands_only(self):
        rows, _, _, _ = self.resolve(l3=level({"testing": {"model": "opus"}, "lessons": {"model": "opus"},
                                               "worker": {"model": "opus"}}))
        self.assertEqual((rows["testing"].model, rows["lessons"].model, rows["worker"].model), (None, None, "opus"))

    def test_slot_missing_at_level_one_with_a_failing_entry_above_is_empty(self):
        l1_slots = copy.deepcopy(DEFAULT_SLOTS)
        del l1_slots["mapper"]
        rows, _, _, notices = self.resolve(l1=level(l1_slots), l3=level({"mapper": {"target": "rigger-core:nobody"}}))
        self.assertEqual((rows["mapper"].filled, rows["mapper"].source), (False, "personal"))
        self.assertEqual(notices, ["rigger: slot mapper from %s ignored: kind null not allowed for mapper; "
                                   "the slot is empty" % L3_FILE])

    def test_null_empties_one_slot(self):
        rows, _, _, notices = self.resolve(l3=level({"mapper": None}))
        self.assertEqual((rows["mapper"].filled, rows["mapper"].source), (False, "personal"))
        self.assertEqual(notices, [])

    def test_null_for_all_slots_or_for_a_group_is_ignored_with_a_notice(self):
        defaults, _, _, _ = self.resolve()
        cases = [
            ((), level(None), "config %s may not set slots to null" % L4_FILE),
            ((), level({"process": None}), "config %s may not set slots.process to null" % L4_FILE),
            ([("acme", level(None))], None, "config module acme may not set slots to null"),
        ]
        for l2, l4, head in cases:
            with self.subTest(head=head):
                rows, _, _, notices = self.resolve(l2=l2, l4=l4)
                self.assertEqual(rows, defaults)
                self.assertEqual(notices, ["rigger: %s; set a single slot to null instead" % head])
        rows, _, _, notices = self.resolve(l1=level(None))
        self.assertFalse(rows["worker"].filled)
        self.assertEqual(notices, [])
        l1_slots = dict(copy.deepcopy(DEFAULT_SLOTS), process=None)
        rows, _, _, notices = self.resolve(l1=level(l1_slots))
        self.assertEqual((rows["worker"].filled, rows["process.tdd"].filled), (True, False))
        self.assertEqual(notices, [])

    def test_unknown_slot_paths_are_ignored_with_one_notice_per_file(self):
        defaults, _, _, _ = self.resolve()
        cases = [
            ({"reviewers": {"kind": "agent"}}, "slot reviewers is not a slot"),
            ({"process": {"tdd": {"model": "x"}, "review": {}}, "nope": {}},
             "slot process.review and 1 more are not slots"),
            ({"process": "superpowers:x"}, "slot process is not a slot"),
            ({"process.tdd": {"target": "superpowers:x"}}, 'slot "process.tdd" is not a slot'),
            ({"reviewerz": {"kind": "command", "target": COMMAND}}, "slot reviewerz is not a slot"),
        ]
        for value, middle in cases:
            with self.subTest(middle=middle):
                rows, _, _, notices = self.resolve(l4=level(value))
                self.assertEqual(rows["process.tdd"].target, "superpowers:test-driven-development")
                self.assertEqual(rows["reviewer"], defaults["reviewer"])
                self.assertEqual(notices, ["rigger: config %s %s; ignored" % (L4_FILE, middle)])

    def test_requires_of_the_entry_drops_the_row(self):
        cases = [
            ({"mapper": {"requires": ["plugin:absent"]}}, False),
            ({"mapper": {"requires": ["plugin:rigger-lessons"]}}, True),
            ({"mapper": {"requires": ["slot:lessons"]}}, True),
            ({"mapper": {"requires": ["slot:process.tdd"]}}, False),
            ({"mapper": {"requires": ["os:linux"]}}, False),
        ]
        for value, kept in cases:
            with self.subTest(value=value):
                _, order, _, notices = self.resolve(l3=level(value), enabled=ENABLED - {"superpowers"})
                self.assertEqual("mapper" in order, kept)
                self.assertEqual(notices, [])

    def test_a_slot_dropped_by_its_own_requires_fills_no_other_slot(self):
        # A shell-guard row that requires os:windows is left out elsewhere, so a slot that requires it is left out
        # too, and so is one that requires that slot in turn.
        l3 = level({"shell-guard": {"requires": ["os:windows"]}, "mapper": {"requires": ["slot:shell-guard"]},
                    "worker": {"requires": ["slot:mapper"]}})
        for os_name, kept in (("windows", True), ("linux", False), ("macos", False)):
            with self.subTest(os=os_name):
                _, order, _, notices = self.resolve(l3=l3, os_name=os_name)
                self.assertEqual(("shell-guard" in order, "mapper" in order, "worker" in order), (kept,) * 3)
                self.assertEqual(notices, [])


REVIEW_HINT = "install rigger-review (claude plugin install rigger-review@rigger)"
TESTING_HINT = ("install rigger-testing (claude plugin install rigger-testing@rigger); "
                "the commit-time weak-test guard is off")
WITHOUT_MODULES = ENABLED - {"rigger-review", "rigger-testing"}


class InstallHintTests(SlotsTestCase):
    """D57: the reviewer and testing defaults name the install command when their plugin is not enabled."""

    def plain_core(self):
        core = self.module("core-plain", agents=("worker", "implementer", "mapper"))
        core.name = "rigger-core"
        return core

    def test_level_one_defaults_give_one_install_line_each(self):
        rows, _, _, notices = self.resolve(enabled=WITHOUT_MODULES, modules=[self.plain_core(), self.shellwise,
                                                                             self.lessons])
        self.assertEqual(notices, ["rigger: slot reviewer is empty: " + REVIEW_HINT,
                                   "rigger: slot testing is empty: " + TESTING_HINT])
        for path in ("reviewer", "testing"):
            self.assertEqual((rows[path].filled, rows[path].target, rows[path].source), (False, None, "default"), path)

    def test_testing_notice_says_the_commit_guard_is_off(self):
        _, _, _, notices = self.resolve(enabled=ENABLED - {"rigger-testing"})
        self.assertEqual(notices, ["rigger: slot testing is empty: install rigger-testing (claude plugin install "
                                   "rigger-testing@rigger); the commit-time weak-test guard is off"])

    def test_a_slot_set_above_level_one_keeps_its_own_notice(self):
        # A model set at level 3 fails the same way: D34's line names the file and carries the hint, no second line.
        _, _, _, notices = self.resolve(l3=level({"reviewer": {"model": "opus"}}), enabled=WITHOUT_MODULES)
        self.assertEqual(notices, ["rigger: slot reviewer from %s ignored: plugin rigger-review is not enabled; "
                                   "the slot is empty; %s" % (L3_FILE, REVIEW_HINT),
                                   "rigger: slot testing is empty: " + TESTING_HINT])
        # A slot the person emptied on purpose, or filled from another plugin, gets no hint.
        for value in (None, {"target": "superpowers:code-reviewer"}):
            with self.subTest(value=value):
                rows, _, _, notices = self.resolve(l3=level({"reviewer": value}), enabled=ENABLED - {"rigger-review"})
                self.assertEqual(notices, [])
                self.assertEqual(rows["reviewer"].filled, value is not None)
        # A plugin named only above level 1 is never offered for install: the command names level 1's plugin.
        l1_slots = copy.deepcopy(DEFAULT_SLOTS)
        del l1_slots["reviewer"]
        _, _, _, notices = self.resolve(l1=level(l1_slots), enabled=ENABLED - {"rigger-review"},
                                        l3=level({"reviewer": {"kind": "agent", "target": "elsewhere:reviewer"}}))
        self.assertEqual(notices, ["rigger: slot reviewer from %s ignored: plugin elsewhere is not enabled; "
                                   "the slot is empty" % L3_FILE])

    def test_a_stale_override_falls_back_with_the_install_command(self):
        # A personal file from before the move still names rigger-core's reviewer, which rigger-core no longer has.
        _, _, _, notices = self.resolve(l3=level({"reviewer": {"target": "rigger-core:reviewer"}}),
                                        enabled=ENABLED - {"rigger-review"},
                                        modules=[self.plain_core(), self.testing, self.shellwise, self.lessons])
        self.assertEqual(notices, ["rigger: slot reviewer from %s ignored: rigger-core has no agent reviewer; "
                                   "the slot is empty; %s" % (L3_FILE, REVIEW_HINT)])

    def test_superpowers_slots_stay_quiet(self):
        _, _, _, notices = self.resolve(enabled=ENABLED - {"superpowers"})
        self.assertEqual(notices, [])
        rows, _, _, notices = self.resolve(enabled=WITHOUT_MODULES - {"superpowers"})
        self.assertEqual([n.split(" is empty")[0] for n in notices], ["rigger: slot reviewer", "rigger: slot testing"])
        self.assertFalse(rows["process.tdd"].filled)  # control: the superpowers slots are empty too

    def test_install_command_names_rigger_cores_marketplace(self):
        core = self.plain_core()
        for market, expected in (("rigger", "rigger"), ("my-market", "my-market"), ("inline", "rigger"),
                                 (None, "rigger"), ("bad name;", "rigger")):
            with self.subTest(market=market):
                core.marketplace = market
                _, _, _, notices = self.resolve(enabled=ENABLED - {"rigger-review"},
                                                modules=[core, self.testing, self.shellwise, self.lessons])
                self.assertEqual(notices, ["rigger: slot reviewer is empty: install rigger-review (claude plugin "
                                           "install rigger-review@%s)" % expected])


class TrustTests(SlotsTestCase):
    def test_l4_command_without_trust_is_dropped_with_the_line(self):
        rows, _, trusted, notices = self.resolve(l4=level({"reviewer": {"kind": "command", "target": COMMAND}}))
        row = rows["reviewer"]
        self.assertEqual((row.kind, row.target, row.model, row.source), ("agent", "rigger-review:reviewer", "by-rule", "default"))
        self.assertEqual(notices, [notice_l4_command("reviewer")])
        self.assertEqual(trusted, frozenset())

    def test_l4_command_with_trust_applies(self):
        trust = {"trust": {"project_commands": ["reviewer"]}}
        rows, _, trusted, notices = self.resolve(
            l3=level({}, **trust), l4=level({"reviewer": {"kind": "command", "target": COMMAND}}))
        row = rows["reviewer"]
        self.assertEqual((row.kind, row.target, row.source, row.filled), ("command", COMMAND, "project", True))
        self.assertEqual(trusted, frozenset(["reviewer"]))
        self.assertEqual(notices, [
            "rigger: slot reviewer runs the project's command [\"reviewer-cli\", \"--out\", \"{out}\"] "
            "… (34 bytes, sha256:c79964168955) (trusted in %s)" % L3_FILE])
        rows, _, _, notices = self.resolve(l3=level({"reviewer": {"kind": "command", "target": COMMAND}}, **trust))
        self.assertEqual((rows["reviewer"].kind, rows["reviewer"].source), ("command", "personal"))
        self.assertEqual(notices, [])

    def test_project_command_notice_is_cut_and_hashed(self):
        trust = level({}, trust={"project_commands": ["reviewer"]})

        def notice(argv):
            _, _, _, notices = self.resolve(l3=trust, l4=level({"reviewer": {"kind": "command", "target": argv}}))
            self.assertEqual(len(notices), 1)
            return notices[0]

        long_one = ["p" * 500, "{out}" + "o" * 495] + ["a" * 500] * 30
        long_two = long_one[:-1] + ["a" * 499 + "b"]
        first, second = notice(long_one), notice(long_two)
        whole = json.dumps(long_one)
        self.assertLess(len(first), 400)
        self.assertTrue(first.startswith("rigger: slot reviewer runs the project's command " + whole[:200] + " … ("))
        self.assertIn("(%d bytes, sha256:" % len(whole), first)
        self.assertNotEqual(first, second)

    def test_l4_model_on_an_l3_command_keeps_source_personal(self):
        rows, _, _, notices = self.resolve(l3=level({"reviewer": {"kind": "command", "target": COMMAND}}),
                                           l4=level({"reviewer": {"model": "opus"}}))
        row = rows["reviewer"]
        self.assertEqual((row.kind, row.target, row.model, row.source), ("command", COMMAND, "opus", "personal"))
        self.assertEqual(notices, [])

    def test_l4_target_alone_cannot_rewrite_a_command(self):
        rows, _, _, notices = self.resolve(l3=level({"reviewer": {"kind": "command", "target": COMMAND}}),
                                           l4=level({"reviewer": {"target": ["evil", "{out}"]}}))
        self.assertEqual((rows["reviewer"].target, rows["reviewer"].source), (COMMAND, "personal"))
        self.assertEqual(notices, [notice_l4_command("reviewer")])

    def test_l4_kind_alone_cannot_make_a_command(self):
        rows, _, _, notices = self.resolve(l3=level({"reviewer": {"target": ["evil", "{out}"]}}),
                                           l4=level({"reviewer": {"kind": "command"}}))
        self.assertEqual(rows["reviewer"].kind, "agent")
        self.assertEqual(notices[0], notice_l4_command("reviewer"))

    def test_l4_model_alone_and_null_are_allowed(self):
        rows, _, _, notices = self.resolve(l4=level({"reviewer": {"model": "opus"}}))
        self.assertEqual((rows["reviewer"].model, rows["reviewer"].source), ("opus", "default"))
        self.assertEqual(notices, [])
        rows, _, _, notices = self.resolve(l3=level({"reviewer": {"kind": "command", "target": COMMAND}}),
                                           l4=level({"reviewer": None}))
        self.assertEqual((rows["reviewer"].filled, rows["reviewer"].source), (False, "project"))
        self.assertEqual(notices, [])

    def test_dropped_l4_value_takes_its_model_with_it(self):
        rows, _, _, notices = self.resolve(
            l3=level({"reviewer": {"kind": "command", "target": COMMAND}}),
            l4=level({"reviewer": {"target": ["evil", "{out}"], "model": "haiku"}}))
        self.assertEqual((rows["reviewer"].target, rows["reviewer"].model), (COMMAND, "by-rule"))
        self.assertEqual(notices, [notice_l4_command("reviewer")])

    def test_trust_names_the_slot_path(self):
        trust = level({}, trust={"project_commands": ["reviewer"]})
        rows, _, _, notices = self.resolve(
            l3=trust, l4=level({"process": {"tdd": {"kind": "command", "target": COMMAND}}}))
        self.assertEqual(rows["process.tdd"].target, "superpowers:test-driven-development")
        self.assertEqual(notices, [notice_l4_command("process.tdd")])
        where = "rigger: config %s trust.project_commands " % L3_FILE
        cases = [
            ([5], frozenset(), [where + "entry 5 is not a slot; ignored"]),
            (["reviewers", "reviewer"], frozenset(["reviewer"]), [where + 'entry "reviewers" is not a slot; ignored']),
            ([[], "process", "worker"], frozenset(["worker"]),
             [where + "entry [] and 1 more are not slots; ignored"]),
            ("reviewer", frozenset(), [where + "must be a list of slot names; ignored"]),
            ({"reviewer": True}, frozenset(), [where + "must be a list of slot names; ignored"]),
        ]
        for value, expected, expected_notices in cases:
            with self.subTest(value=value):
                _, _, trusted, notices = self.resolve(l3=level({}, trust={"project_commands": value}))
                self.assertEqual((trusted, notices), (expected, expected_notices))
        for trust in ({"other": ["reviewer"]}, ["reviewer"]):
            with self.subTest(trust=trust):
                _, _, trusted, notices = self.resolve(l3=level({}, trust=trust))
                self.assertEqual((trusted, notices), (frozenset(), []))

    def test_trust_comes_from_level_three_only(self):
        command = level({"reviewer": {"kind": "command", "target": COMMAND}},
                        trust={"project_commands": ["reviewer"]})
        rows, _, trusted, notices = self.resolve(l2=[("acme", level({}, trust={"project_commands": ["reviewer"]}))],
                                                 l4=command)
        self.assertEqual((rows["reviewer"].kind, trusted), ("agent", frozenset()))
        self.assertEqual(notices, [notice_l4_command("reviewer")])

    def test_l2_command_is_allowed(self):
        rows, _, _, notices = self.resolve(l2=[("acme", level({"reviewer": {"kind": "command", "target": COMMAND}}))])
        row = rows["reviewer"]
        self.assertEqual((row.kind, row.target, row.source), ("command", COMMAND, "company:acme"))
        self.assertEqual(notices, [])

    def test_trust_filter_rows(self):
        command = {"kind": "command", "target": COMMAND}
        agent = {"kind": "agent", "target": "rigger-core:reviewer"}
        cases = [
            # (lower reviewer entry, project value, trusted, dropped)
            (command, {"target": ["evil", "{out}"]}, (), True),
            (command, {"target": None}, (), True),
            (command, {"kind": "command"}, (), True),
            ({"kind": "agent", "target": ["evil", "{out}"]}, {"kind": "command"}, (), True),
            (agent, {"kind": "command", "target": COMMAND}, (), True),
            (None, {"kind": "command", "target": COMMAND}, (), True),
            (agent, {"kind": "command", "target": COMMAND}, ("reviewer",), False),
            (command, {"model": "opus"}, (), False),
            (command, {"requires": ["os:linux"]}, (), False),
            (command, None, (), False),
            (command, {"kind": "agent", "target": "rigger-core:worker"}, (), False),
            (command, "rigger-core:worker", (), False),
            (command, ["evil", "{out}"], (), False),
        ]
        for lower_entry, value, trusted, dropped in cases:
            with self.subTest(lower=lower_entry, value=value, trusted=trusted):
                lower = {} if lower_entry is None else {"reviewer": lower_entry}
                kept, notices = slots.trust_filter(lower, {"reviewer": value, "worker": {"model": "x"}},
                                                   frozenset(trusted), L3_FILE)
                expected = {"worker": {"model": "x"}} if dropped else {"reviewer": value, "worker": {"model": "x"}}
                self.assertEqual(kept, expected)
                self.assertEqual(notices, [notice_l4_command("reviewer")] if dropped else [])

    def test_trust_filter_compares_whole_paths(self):
        lower = {"process.debug": {"kind": "skill", "target": "superpowers:systematic-debugging"}}
        kept, notices = slots.trust_filter(lower, {"process.debug": {"kind": "command", "target": COMMAND}},
                                           frozenset(["process.tdd"]), L3_FILE)
        self.assertEqual((kept, notices), ({}, [notice_l4_command("process.debug")]))


class FillerTests(SlotsTestCase):
    def test_hook_slot_is_filled_by_fills(self):
        rows, _, _, notices = self.resolve()
        self.assertEqual((rows["lessons"].fillers, rows["lessons"].filled, rows["lessons"].failure_mode),
                         (("rigger-lessons",), True, None))
        rows, _, _, notices = self.resolve(modules=[self.core, self.shellwise], enabled=ENABLED - {"rigger-lessons"})
        self.assertEqual((rows["lessons"].fillers, rows["lessons"].filled, rows["lessons"].kind),
                         ((), False, "hook"))
        self.assertEqual(notices, [])
        rows, _, _, notices = self.resolve(enabled=ENABLED - {"rigger-lessons"})
        self.assertEqual((rows["lessons"].fillers, rows["lessons"].filled), ((), False))
        rows, _, _, notices = self.resolve(l4=level({"lessons": None}))
        self.assertEqual((rows["lessons"].fillers, rows["lessons"].filled), (("rigger-lessons",), True))
        self.assertEqual(notices, [])

    def test_two_fillers_give_one_line_naming_both(self):
        other = self.module("other-guard", fills="shell-guard", failure_mode="closed")
        rows, _, _, notices = self.resolve(modules=self.modules + [other], enabled=ENABLED | {"other-guard"})
        row = rows["shell-guard"]
        self.assertEqual((row.fillers, row.failure_mode, row.modes_differ),
                         (("rigger-shellwise", "other-guard"), "closed", False))
        self.assertEqual(notices, ["rigger: slot shell-guard is filled by rigger-shellwise and other-guard; "
                                   "disable one with claude plugin disable <name>"])

    def test_fillers_that_disagree_fail_closed(self):
        cases = [
            # (failure modes of rigger-shellwise and the extra fillers, expected mode, modes differ)
            (("open", "closed"), "closed", True),
            (("closed", "open"), "closed", True),
            (("open", "closed", None), "closed", True),
            (("closed", "closed"), "closed", False),
            (("open", None), None, False),
        ]
        for number, (modes, mode, differ) in enumerate(cases):
            with self.subTest(modes=modes):
                self.shellwise.failure_mode = modes[0]
                extra = [self.module("guard-%d-%d" % (number, index), fills="shell-guard", failure_mode=value)
                         for index, value in enumerate(modes[1:])]
                enabled = ENABLED | {item.name for item in extra}
                rows, _, _, _ = self.resolve(modules=self.modules + extra, enabled=enabled)
                row = rows["shell-guard"]
                self.assertEqual((row.failure_mode, row.modes_differ), (mode, differ))

    def test_expected_filler_missing_above_level_one_gives_a_notice(self):
        _, _, _, notices = self.resolve(l3=level({"lessons": {"target": "my-lessons"}}))
        self.assertEqual(notices, ["rigger: slot lessons: expected my-lessons is not enabled"])
        _, _, _, notices = self.resolve(enabled=ENABLED - {"rigger-lessons"})
        self.assertEqual(notices, [])
        _, _, _, notices = self.resolve(l3=level({"lessons": {"target": "rigger-lessons"}}))
        self.assertEqual(notices, [])
        # A slot its own requires leaves out names no missing filler.
        _, order, _, notices = self.resolve(l3=level({"shell-guard": {"target": "my-guard", "requires": ["os:windows"]}}),
                                            os_name="linux")
        self.assertNotIn("shell-guard", order)
        self.assertEqual(notices, [])


class EntryRuleTests(SlotsTestCase):
    def problem(self, path, entry):
        return slots.entry_problem(path, entry, self.modules, ENABLED)

    def test_entry_rules(self):
        agent = {"kind": "agent"}
        cmd = {"kind": "command"}
        rows = [
            ("reviewer", "rigger-core:reviewer", "must be an object or null"),
            ("reviewer", dict(agent, target="rigger-core:reviewer", colour="red"), "unknown key colour"),
            ("worker", {"kind": "command", "target": COMMAND}, "kind command not allowed for worker"),
            ("worker", {"target": "rigger-core:worker"}, "kind null not allowed for worker"),
            ("worker", {"kind": ["agent"], "target": "rigger-core:worker"}, 'kind ["agent"] not allowed for worker'),
            ("lessons", {"kind": "agent", "target": "rigger-core:worker"}, "kind agent not allowed for lessons"),
            ("worker", dict(agent, target="rigger-core"), "target must be <plugin>:<name>"),
            ("worker", dict(agent, target="rigger-core:worker\n"), "target must be <plugin>:<name>"),
            ("worker", dict(agent, target="-rigger-core:worker"), "target must be <plugin>:<name>"),
            ("worker", dict(agent, target=["rigger-core:worker"]), "target must be <plugin>:<name>"),
            ("worker", agent, "target must be <plugin>:<name>"),
            ("testing", {"kind": "skill", "target": "superpowers:" + "s" * 189}, "target must be <plugin>:<name>"),
            ("worker", dict(agent, target="absent:worker"), "plugin absent is not enabled"),
            ("worker", dict(agent, target="rigger-core:nobody"), "rigger-core has no agent nobody"),
            ("worker", dict(agent, target="rigger-core:testing"), "rigger-core has no agent testing"),
            ("testing", {"kind": "skill", "target": "rigger-core:worker"}, "rigger-core has no skill worker"),
            ("reviewer", dict(cmd, target="reviewer-cli {out}"), "target must be a list of 1 to 32 strings"),
            ("reviewer", dict(cmd, target=[]), "target must be a list of 1 to 32 strings"),
            ("reviewer", dict(cmd, target=["p", "{out}"] + ["a"] * 31), "target must be a list of 1 to 32 strings"),
            ("reviewer", dict(cmd, target=["p", "{out}", 5]), "target must be a list of 1 to 32 strings"),
            ("reviewer", cmd, "target must be a list of 1 to 32 strings"),
            ("reviewer", dict(cmd, target=["p", "", "{out}"]),
             "argument 1 is empty, too long or holds a control character"),
            ("reviewer", dict(cmd, target=["p", "{out}" + "a" * 496]),
             "argument 1 is empty, too long or holds a control character"),
            ("reviewer", dict(cmd, target=["p", "{out}\nrm"]),
             "argument 1 is empty, too long or holds a control character"),
            ("reviewer", dict(cmd, target=["p" + RLO, "{out}"]),
             "argument 0 is empty, too long or holds a control character"),
            ("reviewer", dict(cmd, target=["p", "{outfile}"]), "unknown placeholder {outfile}"),
            ("reviewer", dict(cmd, target=["p", "{out}", "{}"]), "unknown placeholder {}"),
            ("reviewer", dict(cmd, target=["p", "{base}", "{scope}"]), "no {out} placeholder"),
            ("reviewer", dict(cmd, target=["{base}", "{out}"]), "the program name holds a placeholder"),
            ("reviewer", dict(cmd, target=["{out}"]), "the program name holds a placeholder"),
            ("lessons", {"kind": "hook", "target": "rigger lessons"}, "target must be a plugin name"),
            ("lessons", {"kind": "hook", "target": "rigger-lessons\n"}, "target must be a plugin name"),
            ("lessons", {"kind": "hook", "target": 5}, "target must be a plugin name"),
            ("lessons", {"kind": "hook"}, "target must be a plugin name"),
            ("worker", dict(agent, target="rigger-core:worker", model=""), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", model="m" * 61), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", model="a|b"), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", model="a`b"), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", model="a\nb"), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", model=5), "model must be a short one-line string"),
            ("worker", dict(agent, target="rigger-core:worker", requires="os:linux"),
             "slot worker: requires must be a list"),
            ("worker", dict(agent, target="rigger-core:worker", requires=["os:plan9"]),
             "slot worker: requires 'os:plan9': expected slot:<x>, plugin:<name> or os:windows|macos|linux"),
        ]
        for path, entry, reason in rows:
            with self.subTest(path=path, entry=entry):
                self.assertEqual(self.problem(path, entry), reason)

    def test_valid_entries_and_limits(self):
        cmd = {"kind": "command"}
        rows = [
            ("worker", {"kind": "agent", "target": "rigger-core:worker", "model": "m" * 60}),
            ("worker", {"kind": "agent", "target": "rigger-core:worker", "model": "x",
                        "requires": ["os:linux", "slot:lessons", "plugin:x"]}),
            ("reviewer", {"kind": "agent", "target": "rigger-core:reviewer"}),
            ("testing", {"kind": "skill", "target": "rigger-core:testing"}),
            ("testing", {"kind": "skill", "target": "superpowers:" + "s" * 188}),
            ("process.tdd", {"kind": "skill", "target": "superpowers:test-driven-development"}),
            ("reviewer", dict(cmd, target=["p", "{out}"] + ["a" * 500] * 30)),
            ("reviewer", dict(cmd, target=["reviewer-cli", "--base={base}", "--head", "{head}", "{scope}", "-o{out}",
                                           "{", "}{", "a}"])),
            ("lessons", {"kind": "hook", "target": "my.lessons_2"}),
            ("shell-guard", {"kind": "hook", "target": "rigger-windows", "requires": ["os:windows"]}),
        ]
        for path, entry in rows:
            with self.subTest(path=path, entry=entry):
                self.assertIsNone(self.problem(path, entry))

    def test_reason_text_is_one_line(self):
        reason = self.problem("worker", {"kind": "agent", "target": "rigger-core:worker", "x\ny": 1})
        self.assertEqual(reason, "unknown key x\\u000ay")
        reason = self.problem("worker", {"kind": "a" + LS + "b", "target": "rigger-core:worker"})
        self.assertEqual(reason, "kind a\\u2028b not allowed for worker")


def row(path, kind, target, model, source, fillers=(), mode=None, filled=True, differ=False):
    return slots.Row(path=path, kind=kind, target=target, model=model, source=source, fillers=tuple(fillers),
                     failure_mode=mode, filled=filled, modes_differ=differ)


FIVE_ROWS = [
    row("worker", "agent", "rigger-core:worker", "by-rule", "default"),
    row("testing", "skill", "rigger-core:testing", None, "personal"),
    row("process.tdd", None, None, None, "default", filled=False),
    row("shell-guard", "hook", "rigger-windows", None, "default", ("rigger-windows", "other-guard"), "open"),
    row("reviewer", "command", ["review-tool", "{out}"], "opus", "project"),
]
FIVE_ROWS_BYTES = (
    "## Slots\n"
    "\n"
    "| Slot | Kind | Target | Model | From |\n"
    "|---|---|---|---|---|\n"
    "| worker | agent | rigger-core:worker | by-rule | default |\n"
    "| testing | skill | rigger-core:testing | — | personal |\n"
    "| process.tdd | — | — | — | default |\n"
    "| shell-guard | hook | rigger-windows + other-guard (fails open) | — | default |\n"
    "| reviewer | command | review-tool … | opus | project |\n"
).encode("utf-8")


class TableTests(unittest.TestCase):
    def test_table_bytes(self):
        self.assertEqual(slots.table(FIVE_ROWS, None), FIVE_ROWS_BYTES)

    def test_hook_cells(self):
        cases = [
            (row("lessons", "hook", "rigger-lessons", None, "default", ("rigger-lessons",)),
             "| lessons | hook | rigger-lessons (failure mode: undeclared) | — | default |\n"),
            (row("shell-guard", "hook", "x", None, "default", ("x",), "closed"),
             "| shell-guard | hook | x (fails closed) | — | default |\n"),
            (row("shell-guard", "hook", "x", None, "default", ("a", "b"), "closed", differ=True),
             "| shell-guard | hook | a + b (failure mode: closed (fillers disagree)) | — | default |\n"),
            (row("shell-guard", "hook", "rigger-windows", None, "default", (), None, False),
             "| shell-guard | hook | — | — | default |\n"),
        ]
        for item, line in cases:
            with self.subTest(line=line):
                self.assertTrue(slots.table([item], None).endswith(line.encode("utf-8")))

    def test_command_line_only_with_a_command_slot(self):
        line = "Run a command slot: sh <core>/bin/py.sh <core>/tools/slot-run/slot_run.py <slot> --session abc"
        self.assertEqual(slots.table(FIVE_ROWS, line), FIVE_ROWS_BYTES + ("\n" + line + "\n").encode("utf-8"))
        self.assertEqual(slots.table(FIVE_ROWS[:4], line), slots.table(FIVE_ROWS[:4], None))
        unfilled = row("reviewer", "command", ["p", "{out}"], None, "project", filled=False)
        self.assertEqual(slots.table([unfilled], line), slots.table([unfilled], None))
        self.assertEqual(slots.table(FIVE_ROWS, "a\nb"), FIVE_ROWS_BYTES + b"\na?b\n")

    def test_cells_cannot_break_the_table(self):
        bad = [
            row("reviewer", "command", ["re|view`er\nx" + LS, "{out}"], "a|b", "project"),
            row("shell-guard", "hook", "x", None, "company:ac|me", ("gu|ard", "b`c"), "open"),
            row("worker", "agent", "a\r:b", None, "default"),
        ]
        text = slots.table(bad, None).decode("utf-8")
        lines = text.split("\n")
        self.assertEqual(lines[4], "| reviewer | command | re?view?er?x? … | a?b | project |")
        self.assertEqual(lines[5], "| shell-guard | hook | gu?ard + b?c (fails open) | — | company:ac?me |")
        self.assertEqual(lines[6], "| worker | agent | a?:b | — | default |")
        self.assertEqual(len(lines), 8)
        for line in lines[2:7]:
            self.assertEqual(line.count("|"), 6, line)


class TargetsAndSnapshotTests(SlotsTestCase):
    def test_slot_targets_holds_filled_slots_only(self):
        rows, order, _, _ = self.resolve(l3=level({"reviewer": {"kind": "command", "target": COMMAND},
                                                   "mapper": None}),
                                         enabled=ENABLED - {"superpowers", "rigger-lessons"})
        got = slots.slot_targets([rows[path] for path in order])
        self.assertEqual(got, {
            "reviewer": {"kind": "command"},
            "worker": {"kind": "agent", "target": "rigger-core:worker"},
            "implementer": {"kind": "agent", "target": "rigger-core:implementer"},
            "testing": {"kind": "skill", "target": "rigger-testing:testing"},
            "shell-guard": {"kind": "hook"},
        })
        rows, order, _, _ = self.resolve()
        got = slots.slot_targets([rows[path] for path in order])
        self.assertEqual(got["process"], {
            "brainstorm": {"kind": "skill", "target": "superpowers:brainstorming"},
            "plans": {"kind": "skill", "target": "superpowers:writing-plans"},
            "tdd": {"kind": "skill", "target": "superpowers:test-driven-development"},
            "debug": {"kind": "skill", "target": "superpowers:systematic-debugging"},
        })
        self.assertEqual(got["lessons"], {"kind": "hook"})

    def test_snapshot_round_trip_and_wrong_shapes(self):
        trust = level({}, trust={"project_commands": ["reviewer"]})
        rows, order, trusted, _ = self.resolve(
            l3=trust, l4=level({"reviewer": {"kind": "command", "target": COMMAND}}),
            enabled=ENABLED - {"superpowers"})
        listed = [rows[path] for path in order]
        data = json.loads(json.dumps(slots.as_snapshot(listed, trusted)))
        self.assertEqual(slots.from_snapshot(data), (listed, frozenset(["reviewer"])))
        disagree = [row("shell-guard", "hook", "x", None, "default", ("a", "b"), "closed", differ=True)]
        self.assertEqual(slots.from_snapshot(json.loads(json.dumps(slots.as_snapshot(disagree, frozenset())))),
                         (disagree, frozenset()))

        good_row = data["rows"][0]
        self.assertEqual(good_row["path"], "reviewer")

        def with_row(**changes):
            item = dict(good_row, **changes)
            return dict(data, rows=[item] + data["rows"][1:])

        def without(key):
            item = dict(good_row)
            del item[key]
            return dict(data, rows=[item] + data["rows"][1:])

        wrong = [
            ("list", []),
            ("number", 5),
            ("no rows", {"trusted": []}),
            ("extra key", dict(data, more=1)),
            ("rows not a list", dict(data, rows={})),
            ("row not an object", dict(data, rows=["reviewer"])),
            ("row without path", without("path")),
            ("row with an extra key", with_row(colour="red")),
            ("unknown path", with_row(path="reviewers")),
            ("path a list", with_row(path=["reviewer"])),
            ("duplicate path", dict(data, rows=data["rows"] + [good_row])),
            ("kind not allowed", with_row(kind="skill")),
            ("filled without kind", with_row(kind=None)),
            ("target a number", with_row(target=5)),
            ("command target a string", with_row(target="reviewer-cli {out}")),
            ("command target unsafe", with_row(target=["{base}", "{out}"])),
            ("agent target a list", with_row(kind="agent")),
            ("model a number", with_row(model=5)),
            ("source unknown", with_row(source="Project")),
            ("source a number", with_row(source=5)),
            ("fillers a string", with_row(fillers="rigger-windows")),
            ("fillers of numbers", with_row(fillers=[5])),
            ("failure mode unknown", with_row(failure_mode="maybe")),
            ("modes differ a number", with_row(modes_differ=1, failure_mode="closed")),
            ("modes differ without closed", with_row(modes_differ=True, failure_mode="open")),
            ("modes differ missing", without("modes_differ")),
            ("filled a number", with_row(filled=1)),
            ("row a list of the keys", dict(data, rows=[list(good_row)])),
            ("kind of another path", with_row(kind="skill", target="rigger-core:reviewer")),
            ("unfilled target a number", with_row(kind=None, target=5, model=None, filled=False)),
            ("source company without a name", with_row(source="company:")),
            ("trusted an object", dict(data, trusted={"reviewer": True})),
            ("trusted a string", dict(data, trusted="reviewer")),
            ("trusted unknown", dict(data, trusted=["reviewers"])),
            ("trusted numbers", dict(data, trusted=[5])),
        ]
        for name, value in wrong:
            with self.subTest(name=name):
                self.assertIsNone(slots.from_snapshot(value))
        good = [
            with_row(source="company:acme"),
            with_row(source="personal"),
            with_row(source="default"),
            with_row(kind=None, target=None, model=None, filled=False),
            with_row(modes_differ=True, failure_mode="closed"),
        ]
        for value in good:
            with self.subTest(value=value["rows"][0]):
                self.assertIsNotNone(slots.from_snapshot(value))

    def test_snapshot_hook_row_targets(self):
        # A null on a hook slot removes only the expected filler: the module's hook still runs, so the row is
        # filled with no target, and the snapshot must read back. A target of another shape stays refused.
        for target in (None, "rigger-lessons"):
            with self.subTest(target=target):
                rows = [row("lessons", "hook", target, None, "personal", ("mod-a",), None)]
                data = json.loads(json.dumps(slots.as_snapshot(rows, frozenset())))
                self.assertEqual(slots.from_snapshot(data), (rows, frozenset()))
        for target in (["rigger-lessons"], 5, {"name": "x"}):
            with self.subTest(target=target):
                rows = [row("lessons", "hook", "rigger-lessons", None, "personal", ("mod-a",), None)]
                data = json.loads(json.dumps(slots.as_snapshot(rows, frozenset())))
                data["rows"][0]["target"] = target
                self.assertIsNone(slots.from_snapshot(data))


if __name__ == "__main__":
    unittest.main()
