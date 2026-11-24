"""Tests for render.py: placeholders in rules text and the trigger tables."""

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

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


if __name__ == "__main__":
    unittest.main()
