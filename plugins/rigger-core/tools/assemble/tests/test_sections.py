"""Tests for sections.py: rules files split into sections with ids, as rules_start.sh prints them."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import sections  # noqa: E402

REPO = HERE.parents[4]


class SlugTests(unittest.TestCase):
    def test_slug_table(self):
        rows = [
            ("## Git (procedure and naming: {{skill:rigger-core:git-workflow}})", "git"),
            ("## Tests", "tests"),
            ("## Plans: main session", "plans"),
            ("## Recurring defect classes — check while writing, not after review", "recurring-defect-classes"),
            ("## Talking to me", "talking-to-me"),
            ("## Verify in the real environment: main session", "verify-in-the-real-environment"),
            ("## Learn continuously, without being asked (hard rule)", "learn-continuously-without-being-asked"),
            (
                "## Windows skills, loaded before the first step of their kind",
                "windows-skills-loaded-before-the-first-step-of-their-kind",
            ),
            ("## Zażółć gęślą", "zazo-c-gesla"),
            ("## A - b", "a"),
            ("## —", ""),
        ]
        for heading, expected in rows:
            with self.subTest(heading=heading):
                self.assertEqual(sections.slug(heading), expected)


class SplitTests(unittest.TestCase):
    def test_preamble_and_ids(self):
        got = sections.split("windows", b"The root.\n\n## Windows shell\n\n- x\n")
        self.assertEqual([s.id for s in got], ["windows._preamble", "windows.windows-shell"])
        self.assertEqual([s.text for s in got], [b"The root.\n\n", b"## Windows shell\n\n- x\n"])
        self.assertEqual([s.heading for s in got], [None, "## Windows shell"])
        self.assertEqual([s.line for s in got], [1, 3])
        self.assertEqual({s.stem for s in got}, {"windows"})

    def test_heading_only_file_has_no_preamble(self):
        got = sections.split("tests", b"## Tests\n")
        self.assertEqual([(s.id, s.text) for s in got], [("tests.tests", b"## Tests\n")])

    def test_crlf_and_missing_final_newline(self):
        got = sections.split("a", b"## A\r\nx\r\ny")
        self.assertEqual([s.text for s in got], [b"## A\nx\ny\n"])
        mid = sections.split("a", b"## A\r\nx\ry\r\n")
        self.assertEqual(mid[0].text, b"## A\nx\ry\n")
        double = sections.split("a", b"## A\r\r\nx\n")
        self.assertEqual(double[0].text, b"## A\r\nx\n")
        bom = sections.split("a", b"\xef\xbb\xbf## A\nx\n")
        self.assertEqual([(s.id, s.text) for s in bom], [("a._preamble", b"\xef\xbb\xbf## A\nx\n")])

    def test_empty_slug_id(self):
        got = sections.split("s", b"## \xe2\x80\x94\nx\n")
        self.assertEqual(got[0].id, "s._line1")
        later = sections.split("s", b"pre\n## \xe2\x80\x94\n")
        self.assertEqual(later[1].id, "s._line2")

    def test_split_is_lossless_for_every_repo_rules_file(self):
        files = sorted(REPO.glob("plugins/*/rules/*.md"))
        self.assertGreaterEqual(len(files), 18)
        for path in files:
            with self.subTest(path=path.name):
                data = path.read_bytes()
                got = sections.split(path.stem, data)
                self.assertTrue(got)
                self.assertEqual(b"".join(s.text for s in got), sections.normalize_module_bytes(data))


if __name__ == "__main__":
    unittest.main()
