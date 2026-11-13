"""Tests of scripts/mutate_parse.py on the output of real child runs, in LF and in CRLF."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import mutate_parse  # noqa: E402
from mutate_parse import GREEN, INVALID, RED, scrape, verdict  # noqa: E402

CASE = """import unittest

class T(unittest.TestCase):
    def test_target(self):
        self.assertEqual(1, {target})

    def test_other(self):
        self.assertEqual(1, {other})

    def test_third(self):
        {third}

unittest.main()
"""
GOOD = {"target": 1, "other": 1, "third": "pass"}


def child(source):
    """(stdout, stderr) of a real run, as bytes decoded with their line ends."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "child.py"
        path.write_text(source, encoding="utf-8")
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(path)], capture_output=True, timeout=60)
    return done.stdout.decode("utf-8"), done.stderr.decode("utf-8")


def run_case(**changes):
    return child(CASE.format(**{**GOOD, **changes}))


def crlf(streams):
    return tuple(text.replace("\r\n", "\n").replace("\n", "\r\n") for text in streams)


class Scrape(unittest.TestCase):
    def test_green_run_both_line_ends(self):
        for streams in (run_case(), crlf(run_case())):
            run = scrape(*streams)
            self.assertEqual((run.ran, run.ok, run.failing), (3, True, []))

    def test_failure_keeps_name_and_assertion_both_line_ends(self):
        for streams in (run_case(target=2), crlf(run_case(target=2))):
            run = scrape(*streams)
            self.assertEqual((run.ran, run.ok), (3, False))
            self.assertEqual(run.failing, [("FAIL", "test_target")])
            self.assertEqual(run.assertions, ["AssertionError: 1 != 2"])

    def test_error_is_no_assertion(self):
        run = scrape(*run_case(third="raise ValueError('x')"))
        self.assertEqual((run.failing, run.assertions), ([("ERROR", "test_third")], []))

    def test_last_summary_wins(self):
        self.assertEqual(scrape("", "Ran 1 test in 0.1s\n\nOK\nRan 4 tests in 0.1s\n\nOK\n").ran, 4)

    def test_custom_runner_line(self):
        run = scrape("LINK FAIL  join: want=1 got=2\n# shell-guard: 7 passed, 1 failed\n", "")
        self.assertEqual((run.ran, run.ok, run.failing), (8, False, [("FAIL", "join")]))
        self.assertEqual(scrape("# shell-guard: 8 passed, 0 failed\n", "").ok, True)

    def test_tap_lines(self):
        run = scrape("not ok 1 - joins\n# tests 2\n# pass 1\n# fail 1\n", "")
        self.assertEqual((run.ran, run.ok, run.failing), (2, False, [("FAIL", "joins")]))
        self.assertEqual(scrape("ok 1 - joins\n# tests 1\n# pass 1\n# fail 0\n", "").ok, True)

    def test_unreadable_output_reads_nothing(self):
        for text in ("", "garbage\n", "Ran x tests\n", "# tests 3\n"):
            run = scrape(text, "")
            self.assertFalse(run.ok, text)


class Verdict(unittest.TestCase):
    def setUp(self):
        self.base = scrape(*run_case())

    def test_red_on_the_target_assertion(self):
        self.assertEqual(verdict(self.base, scrape(*run_case(target=2)), "test_target"), RED)

    def test_red_on_a_custom_runner_line(self):
        base = scrape("# shell-guard: 8 passed, 0 failed\n", "")
        run = scrape("LINK FAIL  join: want=1 got=2\n# shell-guard: 7 passed, 1 failed\n", "")
        self.assertEqual(verdict(base, run, "join"), RED)

    def test_green_survivor(self):
        self.assertEqual(verdict(self.base, scrape(*run_case()), "test_target"), GREEN)

    def test_failure_on_an_unrelated_check_proves_nothing(self):
        self.assertEqual(verdict(self.base, scrape(*run_case(other=2)), "test_target"), INVALID)

    def test_target_error_proves_nothing(self):
        run = scrape(*run_case(target="undefined_name"))
        self.assertEqual(run.failing, [("ERROR", "test_target")])
        self.assertEqual(verdict(self.base, run, "test_target"), INVALID)

    def test_import_error_proves_nothing(self):
        self.assertEqual(verdict(self.base, scrape(*child("import no_such_module\n")), "test_target"), INVALID)

    def test_syntax_error_proves_nothing(self):
        self.assertEqual(verdict(self.base, scrape(*child("def\n")), "test_target"), INVALID)

    def test_zero_tests_prove_nothing(self):
        empty = scrape(*child("import unittest\nunittest.main()\n"))
        self.assertEqual(empty.ran, 0)
        self.assertEqual(verdict(self.base, empty, "test_target"), INVALID)
        self.assertEqual(verdict(empty, empty, "test_target"), INVALID)

    def test_other_count_proves_nothing(self):
        run = scrape("", "Ran 2 tests in 0.1s\n\nOK\n")
        self.assertEqual(verdict(self.base, run, "test_target"), INVALID)

    def test_red_baseline_proves_nothing(self):
        red = scrape(*run_case(other=2))
        self.assertEqual(verdict(red, scrape(*run_case(target=2)), "test_target"), INVALID)

    def test_empty_target_proves_nothing(self):
        self.assertEqual(verdict(self.base, scrape(*run_case(target=2)), ""), INVALID)
        self.assertEqual(verdict(self.base, self.base, ""), INVALID)

    def test_clean_summary_under_a_failure_line_proves_nothing(self):
        run = scrape("", "FAIL: test_other (m.T.test_other)\nRan 3 tests in 0.1s\n\nOK\n")
        self.assertEqual(verdict(self.base, run, "test_target"), INVALID)

    def test_failed_summary_with_no_name_proves_nothing(self):
        self.assertEqual(verdict(self.base, scrape("", "Ran 3 tests in 0.1s\n\nFAILED (failures=1)\n"), "test_target"), INVALID)

    def test_cut_output_proves_nothing(self):
        for text in ("", "garbage\n", "Ran 3 tests in 0.1s\n"):
            self.assertEqual(verdict(self.base, scrape("", text), "test_target"), INVALID, repr(text))

    def test_module_names_the_three_verdicts(self):
        self.assertEqual({mutate_parse.RED, mutate_parse.GREEN, mutate_parse.INVALID}, {"red", "green", "INVALID"})


if __name__ == "__main__":
    unittest.main()
