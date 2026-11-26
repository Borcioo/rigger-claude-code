"""Tests for render.py: placeholders in rules text and the trigger tables."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import assemble  # noqa: E402
import manifest  # noqa: E402
import parts  # noqa: E402
import render  # noqa: E402
from manifest import Module, TriggerRow  # noqa: E402

SLOTS = {
    "testing": {"kind": "skill", "target": "rigger-core:testing"},
    "process": {
        "brainstorm": {"kind": "skill", "target": "superpowers:brainstorming"},
        "tdd": {"kind": "skill", "target": "superpowers:test-driven-development"},
    },
}


def row(row_id, before, *load, section="s.t"):
    return TriggerRow(id=row_id, before=before, load=tuple(load), section=section, requires=())


def module(name, *rows):
    return Module(name=name, root=Path("/m/" + name), layer="general", fills=None, provides={}, rules=[],
                  triggers=list(rows), subagents={}, config=None, lessons=None, tests={}, marketplace=None,
                  origin="installed")


def ctx(rows=None, variables=None, name="demo"):
    return render.RenderContext(slots=SLOTS, rows_by_section=rows or {}, vars=variables or {}, module=name)


TWO_ROWS = {"s.t": [row("m.a", "a", "x:a"), row("m.b", "b", "x:b")]}


class RenderTests(unittest.TestCase):
    def test_slot_and_skill(self):
        text = b"Use {{slot:process.brainstorm}} and {{skill:rigger-core:git-workflow}}.\n"
        self.assertEqual(
            render.render(text, ctx()),
            (b"Use `superpowers:brainstorming` and `rigger-core:git-workflow`.\n", []),
        )

    def test_table_rows_join_and_order(self):
        # Row shape of today's skills.md:6-15.
        modules = [module("x", row("x.a", "a", "x:a", section=None), row("x.b", "b", "x:b", section=None))]
        effective = {"triggers": {"core.c": {"before": "c", "load": ["slot:testing", "slot:process.tdd"]}}}
        rows = render.collect_rows(modules, effective)
        self.assertEqual(
            render.table(rows, SLOTS),
            b"| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `x:b` |\n"
            b"| c | `rigger-core:testing`, then `superpowers:test-driven-development` |\n",
        )

    def test_triggers_line_is_replaced_whole(self):
        got = render.render(b"intro\n\n{{triggers:s.t}}\nafter\n", ctx(TWO_ROWS))
        self.assertEqual(
            got, (b"intro\n\n| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `x:b` |\nafter\n", [])
        )

    def test_lines_split_only_at_line_feed(self):
        text = "a\rb   {{slot:testing}}\n{{triggers:s.t}} \nx {{triggers:s.t}}\nend".encode("utf-8")
        got, unknown = render.render(text, ctx(TWO_ROWS))
        expected = "a\rb   `rigger-core:testing`\n{{triggers:s.t}} \nx {{triggers:s.t}}\nend"
        self.assertEqual(got, expected.encode("utf-8"))
        self.assertEqual(unknown, ["{{triggers:s.t}}", "{{triggers:s.t}}"])

    def test_empty_section_renders_nothing(self):
        self.assertEqual(render.render(b"intro\n\n{{triggers:s.t}}\nafter\n", ctx()), (b"intro\n\nafter\n", []))

    def test_unknown_placeholder_printed_and_reported(self):
        text = b"a {{tool:x:y}} b {{slot:nope}} {{triggers:s.t}} c {{shout}}\n"
        got, unknown = render.render(text, ctx(TWO_ROWS))
        self.assertEqual(got, text)
        self.assertEqual(unknown, ["{{tool:x:y}}", "{{slot:nope}}", "{{triggers:s.t}}", "{{shout}}"])

    def test_an_empty_slot_drops_the_sentence_that_names_it(self):
        # process.plans and process.debug are slots with no filler in SLOTS; testing is filled.
        text = (b"## P\n"
                b"\n"
                b"- Keep a plan. Load {{slot:process.plans}} first,\n"
                b"  then write it. Review it once.\n"
                b"- An item with no full stop\n"
                b"- A brief never waives planning (with superpowers installed:\n"
                b"  {{slot:process.debug}}), the thinking pass (goal,\n"
                b"  reader).\n"
                b"- Stays.\n"
                b"- Kept first. Then {{slot:process.plans}} last.\n"
                b"## Q\n"
                b"Debug with {{slot:process.debug}} when stuck. Use {{slot:testing}} always.\n"
                b"| a {{slot:process.plans}} | b |\n"
                b"| c | d |\n")
        expected = (b"## P\n"
                    b"\n"
                    b"- Keep a plan. Review it once.\n"
                    b"- An item with no full stop\n"
                    b"- Stays.\n"
                    b"- Kept first.\n"
                    b"## Q\n"
                    b"Use `rigger-core:testing` always.\n"
                    b"| c | d |\n")
        self.assertEqual(render.render(text, ctx()), (expected, []))

    def test_var_and_legacy_var(self):
        variables = {"demo": {"reply_language": "Reply in {{slot:testing}}.", "report_lines": "15"}}
        text = b"{{var:reply_language}} / {{report_lines}} / {{var:other}}\n"
        got, unknown = render.render(text, ctx(variables=variables))
        self.assertEqual(got, b"Reply in {{slot:testing}}. / 15 / {{var:other}}\n")
        self.assertEqual(unknown, ["{{var:other}}"])
        _, unknown = render.render(b"{{report_lines}}\n", ctx(variables=variables, name="another"))
        self.assertEqual(unknown, ["{{report_lines}}"])

    def test_rows_text_is_not_scanned_again(self):
        rows = {"s.t": [row("m.a", "uses {{slot:testing}}", "x:{{a}}")]}
        got, unknown = render.render(b"{{triggers:s.t}}\n", ctx(rows))
        self.assertEqual(got, b"| Before | Skill |\n|---|---|\n| uses {{slot:testing}} | `x:{{a}}` |\n")
        self.assertEqual(unknown, [])

    def test_config_row_without_section_goes_to_the_default_table(self):
        effective = {"triggers": {"me.notes": {"before": "notes", "load": ["me:notes"]}}}
        rows = render.collect_rows([], effective)
        self.assertEqual(rows, [TriggerRow("me.notes", "notes", ("me:notes",), render.DEFAULT_SECTION, ())])
        self.assertEqual(render.DEFAULT_SECTION, "skills.skill-triggers")
        placed = render.collect_rows([], {"triggers": {"me.w": {"before": "w", "load": ["a:b"], "section": "w.t",
                                                                "requires": ["os:windows"]}}})
        self.assertEqual(placed, [TriggerRow("me.w", "w", ("a:b",), "w.t", ("os:windows",))])

    def test_config_row_replace_and_remove(self):
        modules = [module("x", row("x.a", "a", "x:a"), row("x.b", "b", "x:b"), row("x.c", "c", "x:c"))]
        effective = {"triggers": {"x.b": {"before": "B", "load": ["y:b"]}, "x.c": None}}
        rows = render.collect_rows(modules, effective)
        self.assertEqual([(r.id, r.before, r.load) for r in rows], [("x.a", "a", ("x:a",)), ("x.b", "B", ("y:b",))])
        self.assertEqual(rows[1].section, render.DEFAULT_SECTION)

    def test_backtracking_probe(self):
        # In a child with a bound, so a backtracking regex fails the test instead of hanging the run.
        code = (
            "import sys, time; sys.path.insert(0, sys.argv[1]); import render; "
            "text = ('{{' * 40 + 'slot:' * 40 + '\\n' + '{{slot:' * 40 + 'x' * 40 + '\\n').encode(); "
            "ctx = render.RenderContext(slots={}, rows_by_section={}, vars={}, module='m'); "
            "start = time.monotonic(); got, _ = render.render(text, ctx); "
            "print(round(time.monotonic() - start, 3), got == text)"
        )
        try:
            result = subprocess.run([sys.executable, "-I", "-c", code, str(HERE.parent)], capture_output=True,
                                    timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("render did not finish within 20 s")
        seconds, same = result.stdout.decode("utf-8").split()
        self.assertLess(float(seconds), 0.5)
        self.assertEqual(same, "True")


def merged(rows, enabled=None, seen=None, slots=None, **extra):
    return render.RenderContext(slots=slots or SLOTS, rows_by_section={}, vars={}, module="rigger-core", rows=rows,
                                enabled=enabled, table_seen=seen, **extra)


class MergedTableTests(unittest.TestCase):
    def test_one_table_holds_the_rows_of_every_section(self):
        modules = [module("x", row("x.a", "a", "x:a", section="one.t")), module("y", row("y.b", "b", "y:b", section="two.t"))]
        rows = render.collect_rows(modules, {"triggers": {"me.c": {"before": "c", "load": ["me:c"]}}})
        got, unknown = render.render(b"intro\n{{triggers:one.t}}\nafter\n", merged(rows))
        self.assertEqual(got, b"intro\n| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `y:b` |\n| c | `me:c` |\nafter\n")
        self.assertEqual(unknown, [])

    def test_second_table_placeholder_renders_nothing_and_is_reported(self):
        rows = [row("x.a", "a", "x:a")]
        seen = []
        got, unknown = render.render(b"{{triggers:s.t}}\nmid\n{{triggers:w.t}}\n", merged(rows, seen=seen))
        self.assertEqual(got, b"| Before | Skill |\n|---|---|\n| a | `x:a` |\nmid\n")
        self.assertEqual(unknown, ["second trigger table"])
        self.assertEqual(seen, ["rigger-core"])
        got, unknown = render.render(b"## Other\n{{triggers:s.t}}\n", merged(rows, seen=seen))
        self.assertEqual((got, unknown), (b"## Other\n", ["second trigger table"]))

    def test_entry_of_a_plugin_that_is_not_enabled_is_dropped(self):
        rows = [row("r.both", "both", "x:a", "sup:b"), row("r.sup", "sup only", "sup:b"),
                row("r.slot", "slot", "slot:nope", "slot:testing"), row("r.gone", "gone", "slot:nope"),
                row("r.mine", "mine", "notes-style")]
        got, _ = render.render(b"{{triggers:s.t}}\n", merged(rows, enabled=frozenset({"x", "rigger-core"})))
        self.assertEqual(got, b"| Before | Skill |\n|---|---|\n| both | `x:a` |\n| slot | `rigger-core:testing` |\n"
                              b"| mine | `notes-style` |\n")
        # A slot target whose plugin is not enabled goes too: without superpowers only the testing skill stays.
        tdd = [row("r.t", "t", "slot:testing", "slot:process.tdd")]
        self.assertEqual(render.table(tdd, SLOTS, frozenset({"rigger-core"})),
                         b"| Before | Skill |\n|---|---|\n| t | `rigger-core:testing` |\n")
        # An empty slot goes even when a plugin happens to be named `slot`.
        self.assertEqual(render.visible([row("r.s", "s", "slot:nope")], SLOTS, frozenset({"slot"})), [])
        # enabled None drops nothing: an empty slot keeps its text, as phase 1 printed it.
        self.assertEqual([entries for _, entries in render.visible(rows, SLOTS, None)],
                         [["x:a", "sup:b"], ["sup:b"], ["slot:nope", "rigger-core:testing"], ["slot:nope"],
                          ["notes-style"]])

    def test_callers_without_rows_render_as_in_phase_one(self):
        # A manifest row may have an empty load; phase 1 prints it with an empty Skill cell.
        sections = {"one.t": [row("x.a", "a", "x:a"), row("x.e", "e")], "two.t": [row("y.b", "b", "slot:nope")]}
        got, unknown = render.render(b"{{triggers:one.t}}\n{{triggers:two.t}}\n", ctx(sections))
        self.assertEqual(got, b"| Before | Skill |\n|---|---|\n| a | `x:a` |\n| e |  |\n| Before | Skill |\n|---|---|\n"
                              b"| b | `slot:nope` |\n")
        self.assertEqual(unknown, [])
        # rows=[] is the merged path with no rows: nothing renders, whatever rows_by_section holds.
        empty = render.RenderContext(slots=SLOTS, rows_by_section=sections, vars={}, module="m", rows=[])
        self.assertEqual(render.render(b"{{triggers:one.t}}\n", empty), (b"", []))


PLUGINS = HERE.parents[3]
SECTION = (b"## Skill triggers\n\n- Load the skill before the first step of its kind:\n\n"
           b"{{triggers:skills.skill-triggers}}\n")
ROOT = "/opt/claude-config/plugins/cache/rigger/rigger-core/0.5.0"  # an installed root of typical length
PROJECT = "/work/cloned-repo/.claude/rigger.json"
CORE_ROW = "`rigger-testing:testing`"  # the testing slot's level-1 default
LEFT_OUT = re.compile(r"^rigger: left out (\d+) of the trigger rows from (.+): the Skill triggers table must fit in "
                      r"one hook part$", re.M)


def _real_rows(project_rows):
    """Rows as a session with the four rule modules and a cloned repository's file would merge them."""
    modules = [manifest.load_manifest(PLUGINS / name, "inline", "dirs")
               for name in ("rigger-core", "rigger-rules", "rigger-platform", "rigger-wp")]
    defaults = json.loads((PLUGINS / "rigger-core" / "rigger-defaults.json").read_bytes().decode("utf-8"))
    triggers = dict(defaults["triggers"])
    triggers.update(project_rows)
    sources = {row_id: "L1:rigger-defaults.json" for row_id in defaults["triggers"]}
    sources.update({row_id: "L4:" + PROJECT for row_id in project_rows})
    module_rows = [r for m in modules for r in m.triggers] + render.collect_rows([], {"triggers": defaults["triggers"]})
    return render.collect_rows(modules, {"triggers": triggers}), sources, module_rows, defaults["slots"]


def at_caps(count, char="b"):
    # The probe of finding RV1 Important 1: before of 300 characters and 4 load entries of 200.
    return {"r%02d" % i: {"before": ("CAPROW%02d " % i) + char * 291, "load": ["x:" + chr(97 + k) * 198 for k in range(4)]}
            for i in range(count)}


def moderate(count):
    return {"r%02d" % i: {"before": ("CAPROW%02d " % i) + "b" * 111, "load": ["x:" + chr(97 + k) * 38 for k in range(2)]}
            for i in range(count)}


class FitTests(unittest.TestCase):
    """Module rows are never evicted by config rows (coordinator ruling on RV1 Important 1)."""

    # Every module row stays visible (superpowers enabled), and the project rows' plugin `x` is enabled,
    # so only the size check can remove a row.
    ENABLED = frozenset({"rigger-core", "rigger-rules", "rigger-platform", "rigger-testing", "rigger-wp", "superpowers",
                         "x"})

    def test_probe_scenarios_keep_the_table_in_a_part(self):
        scenarios = [("10 rows at the caps", at_caps(10)), ("50 moderate rows", moderate(50)),
                     ("10 rows of quotes at the caps", at_caps(10, '"'))]
        for label, project_rows in scenarios:
            for mode, encode in (("session", assemble._identity), ("subagent", assemble._json_body)):
                with self.subTest(scenario=label, mode=mode):
                    rows, sources, module_rows, slots = _real_rows(project_rows)
                    unfitted = render.render(SECTION, merged(rows, enabled=self.ENABLED, slots=slots))[0]
                    self.assertFalse(render.fits_part(ROOT, encode)(unfitted))  # the scenario overflows a part
                    text, _ = render.render(SECTION, merged(rows, enabled=self.ENABLED, slots=slots,
                                                            row_sources=sources, fits=render.fits_part(ROOT, encode)))
                    bodies, left_out = parts.pack([(ROOT, text, "skills.skill-triggers")], parts.PART_CAP,
                                                  parts.SESSION_PARTS, encode)
                    self.assertEqual(left_out, [])
                    printed = b"".join(bodies).decode("utf-8")
                    self.assertIn("## Skill triggers", printed)
                    self.assertIn(CORE_ROW, printed)
                    for module_row in module_rows:
                        self.assertIn("| %s |" % module_row.before, printed)
                    notice = LEFT_OUT.search(printed)
                    self.assertIsNotNone(notice, printed[-400:])
                    shown = printed.count("CAPROW")
                    self.assertGreater(shown, 0)
                    self.assertEqual(int(notice.group(1)), len(project_rows) - shown)
                    self.assertEqual(notice.group(2), PROJECT)

    def test_project_rows_go_first_then_personal_then_company(self):
        # Every row line is 410 bytes, longer than the notice, so each limit below drops exactly `dropped` rows.
        rows = [row("%s.%d" % (lvl, i), "%s%d" % (lvl, i) + lvl * 396, "x:a")
                for lvl, count in (("m", 1), ("c", 2), ("p", 2), ("r", 2)) for i in range(count)]
        sources = {"c.0": "L2:company-a", "c.1": "L2:company-a", "p.0": "L3:mine.json", "p.1": "L3:mine.json",
                   "r.0": "L4:repo.json", "r.1": "L4:repo.json"}
        full = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources))[0]
        line = len(b"| %s | `x:a` |\n" % (b"c" * 398))
        expected_ids = [["m.0", "c.0", "c.1", "p.0", "p.1", "r.0"], ["m.0", "c.0", "c.1", "p.0", "p.1"],
                        ["m.0", "c.0", "c.1", "p.0"], ["m.0", "c.0", "c.1"], ["m.0", "c.0"], ["m.0"]]
        for dropped, ids in enumerate(expected_ids, start=1):
            with self.subTest(dropped=dropped):
                limit = len(full) - dropped * line + 200  # room for the notice line
                text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources,
                                                                  fits=lambda body, n=limit: len(body) <= n))[0]
                kept = [r.id for r in rows if ("| %s |" % r.before).encode() in text]
                self.assertEqual(kept, ids)

    def test_the_fewest_rows_go_and_a_part_at_exactly_the_cap_keeps_them_all(self):
        rows = [row("m.a", "module", "x:a")] + [row("r.%d" % i, "r%d" % i + "x" * 200, "x:a") for i in range(9)]
        sources = {"r.%d" % i: "L4:repo.json" for i in range(9)}
        full = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources))[0]
        exact = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources,
                                                            fits=lambda body: len(body) <= len(full)))[0]
        self.assertEqual(exact, full)
        notice = len(b"\nrigger: left out 1 of the trigger rows from repo.json: the Skill triggers table must fit in "
                     b"one hook part\n")
        limit = len(full) - len(b"| r8%s | `x:a` |\n" % (b"x" * 200)) + notice
        one = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources,
                                                          fits=lambda body: len(body) <= limit))[0]
        self.assertEqual(len(one), limit)
        self.assertNotIn(b"| r8x", one)
        self.assertIn(b"| r7x", one)
        self.assertEqual([m.group(1) for m in LEFT_OUT.finditer(one.decode())], ["1"])
        # fits_part measures what the packer measures: the root line plus the encoded body, at most the cap.
        line = b"Plugin root: /r\n"
        self.assertTrue(render.fits_part("/r", assemble._identity)(b"a" * (parts.PART_CAP - len(line))))
        self.assertFalse(render.fits_part("/r", assemble._identity)(b"a" * (parts.PART_CAP - len(line) + 1)))
        self.assertFalse(render.fits_part("/r", assemble._json_body)(b'"' * (parts.PART_CAP - len(line))))

    def test_module_rows_that_overflow_alone_are_all_kept(self):
        rows = [row("m.a", "a" * 50, "x:a"), row("r.0", "r", "x:a"), row("m.b", "b" * 50, "x:a")]
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources={"r.0": "L4:repo.json"},
                                                          fits=lambda body: len(body) <= 10))[0]
        self.assertIn(b"a" * 50, text)
        self.assertIn(b"b" * 50, text)
        self.assertNotIn(b"| r |", text)
        self.assertNotIn(b"rigger:", text)  # no room for the line either: the packer reports the section

    def test_module_rows_outrank_the_line(self):
        rows = [row("m.a", "a" * 50, "x:a"), row("r.0", "r", "x:a")]
        alone = render.render(b"{{triggers:s.t}}\n", merged([rows[0]]))[0]
        # Room for the module row but not for the line as well: the row stays, the config row and the line go.
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources={"r.0": "L4:repo.json"},
                                                          fits=lambda body: len(body) <= len(alone)))[0]
        self.assertEqual(text, alone)

    def test_only_config_rows_leave_the_line_without_a_header(self):
        rows = [row("r.0", "r" * 300, "x:a")]
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources={"r.0": "L4:repo.json"},
                                                          fits=lambda body: len(body) <= 200))[0]
        self.assertEqual(text, b"\nrigger: left out 1 of the trigger rows from repo.json: the Skill triggers table "
                               b"must fit in one hook part\n")

    def test_names_in_the_line_are_one_line_and_bounded(self):
        long_path = "/repo/" + "d" * 400 + "/rigger.json"
        rows = [row("m.a", "m", "x:a"), row("c.0", "c" * 500, "x:a"), row("r.0", "r" * 500, "x:a")]
        sources = {"c.0": "L2:evil\nrigger: fake line" + "e" * 100, "r.0": "L4:" + long_path}
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources,
                                                          fits=lambda body: len(body) <= 900))[0].decode()
        notice = LEFT_OUT.search(text)
        self.assertIsNotNone(notice, text[-600:])
        self.assertEqual([line for line in text.split("\n") if line.startswith("rigger:")], [notice.group(0)])
        files = notice.group(2)
        self.assertEqual(files.count(" and company module "), 1, files)
        shown_path, module_part = files.split(" and company module ")
        self.assertEqual(len(shown_path), 300)
        self.assertTrue(shown_path.endswith("…"))
        self.assertEqual(len(module_part), 80)
        self.assertTrue(module_part.startswith("evil\\u000arigger: fake line"))

    def test_notice_counts_rows_and_names_each_file(self):
        rows = [row("m.a", "m", "x:a"), row("c.0", "c" * 40, "x:a"), row("p.0", "p" * 40, "x:a"),
                row("r.0", "r" * 40, "x:a"), row("r.1", "s" * 40, "x:a"), row("gone", "g" * 40, "nope:a")]
        sources = {"c.0": "L2:company-a", "p.0": "L3:mine.json", "r.0": "L4:repo.json", "r.1": "L4:repo.json",
                   "gone": "L4:repo.json"}
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, enabled=frozenset({"x"}), row_sources=sources,
                                                          fits=lambda body: len(body) <= 200))[0].decode()
        self.assertEqual(text.count("| m |"), 1)
        found = LEFT_OUT.findall(text)
        # The row whose plugin is not enabled is gone before any row is counted (D39 first).
        self.assertEqual(found, [("4", "repo.json, mine.json and company module company-a")])

    def test_dropping_a_replacement_brings_back_the_row_it_replaced(self):
        modules = [module("x", row("x.a", "a", "x:a"), row("x.b", "b", "x:b"), row("x.c", "c", "x:c"))]
        rows = render.collect_rows(modules, {"triggers": {"x.b": {"before": "B" * 200, "load": ["x:b"]}}})
        sources = {"x.b": "L4:repo.json"}
        text = render.render(b"{{triggers:s.t}}\n", merged(rows, row_sources=sources, replaced={"x.b": modules[0].triggers[1]},
                                                          fits=lambda body: len(body) <= 200))[0]
        self.assertEqual(text, b"| Before | Skill |\n|---|---|\n| a | `x:a` |\n| b | `x:b` |\n| c | `x:c` |\n"
                               b"\nrigger: left out 1 of the trigger rows from repo.json: the Skill triggers table "
                               b"must fit in one hook part\n")


if __name__ == "__main__":
    unittest.main()
