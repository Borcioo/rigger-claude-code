"""Checks that `/rigger-core:coordinate` and the state-file rule call coord_state.py at every stage.
RIGGER_COORDINATE_MD points the test at another copy of coordinate.md (used for red runs)."""

import os
import re
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
CORE = Path(__file__).resolve().parents[3]
COORDINATE = Path(os.environ.get("RIGGER_COORDINATE_MD") or CORE / "commands" / "coordinate.md").read_text(encoding="utf-8")
STATE_FILE = (CORE / "rules" / "state-file.md").read_text(encoding="utf-8")


class CoordinateWiring(unittest.TestCase):
    def test_each_stage_names_its_call(self):
        for call in ("coord_state.py\" init", "add <part>", "move <part> started", "move <part> built",
                     "move <part> reviewed", "move <part> merged", "show"):
            self.assertIn(call, COORDINATE)

    def test_missing_ok_line_is_a_refusal(self):
        self.assertIn("Output without a `coord-state: ok` line is a refusal", COORDINATE)

    def test_a_refusal_stops_the_step_and_goes_into_the_state_file(self):
        self.assertRegex(COORDINATE, r"(?s)refused.{0,200}stop.{0,100}state file")

    def test_merge_evidence_quotes_the_users_ok(self):
        self.assertRegex(COORDINATE, r"merged[^\n]*(?:\n[^\n]*){0,2}quot[^\n]*OK")

    def test_state_file_rule_points_at_show(self):
        self.assertIn("coord_state.py\" show", STATE_FILE)

    def test_empty_output_is_not_an_ok(self):
        for out in ("", "\n", "coord-state: refused: x\n", "Python 3.9+ not found\n"):
            self.assertFalse(re.search(r"^coord-state: ok ", out, re.M))
        self.assertTrue(re.search(r"^coord-state: ok ", "coord-state: ok p built\n", re.M))


if __name__ == "__main__":
    unittest.main()
