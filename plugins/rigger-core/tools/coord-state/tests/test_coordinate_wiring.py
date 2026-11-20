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

    def test_calls_carry_their_options(self):
        for call in ("move <part> built --head <sha> --evidence '", "move <part> reviewed --evidence '",
                     "move <part> merged --head <merge sha> --evidence '"):
            self.assertIn(call, COORDINATE)

    def test_missing_ok_line_is_a_refusal(self):
        self.assertIn("Output without a `coord-state: ok` line is a refusal", COORDINATE)

    def test_a_refusal_stops_the_step_and_goes_into_the_state_file(self):
        self.assertRegex(COORDINATE, r"(?s)refused.{0,200}stop.{0,100}state file")

    def test_merge_evidence_quotes_the_plan_approval(self):
        self.assertRegex(COORDINATE, r"merged[^\n]*(?:\n[^\n]*){0,2}quot[^\n]*plan approval")

    def test_plan_key_is_written_in_step_one(self):
        self.assertRegex(COORDINATE, r"\n1\. [^\n]*(?:\n   [^\n]*)*`plan: <plan path>`")

    def test_only_the_integration_branch_merges_on_the_plan(self):
        self.assertRegex(COORDINATE, r"\n5\. Merge each reviewed part into the integration branch \(the approved plan is\s+"
                                     r"the user's OK; the default branch only on their explicit instruction, at\s+"
                                     r"`stop: main`, also when it is the integration branch\)")

    def test_a_default_branch_merge_quotes_the_instruction(self):
        self.assertRegex(COORDINATE, r"quoting the user's plan approval \(for the default branch, their\s+"
                                     r"instruction\) in quotes")

    def test_brief_step_loads_testing_and_keeps_the_full_suite(self):
        step_three = " ".join(COORDINATE[COORDINATE.index("\n3. "):COORDINATE.index("\n4. ")].split())
        self.assertIn("loads `rigger-core:testing` and runs only those", step_three)
        self.assertIn("the full suite and the gate are yours, at checkpoints", step_three)

    def test_split_step_runs_heavy_parts_two_at_once_and_groups_light_ones(self):
        step_two = " ".join(COORDINATE[COORDINATE.index("\n2. "):COORDINATE.index("\n3. ")].split())
        self.assertIn("at most 2 heavy parts run at once", step_two)
        self.assertIn("several light parts go to one session", step_two)

    def test_over_the_app_cap_the_user_starts_a_card_and_no_agent_takes_it(self):
        flat = " ".join(COORDINATE.split())
        self.assertIn("past the app's cap of sessions working at once", flat)
        self.assertIn("never to a background agent without the user's OK", flat)

    def test_the_wrap_up_start_button_follows_app_refused(self):
        self.assertRegex(COORDINATE, r"set `stop: app-refused`[^.]{0,60}`wrap_up` question with a start button")

    def test_a_mid_plan_stop_is_cleared_once_answered(self):
        self.assertRegex(COORDINATE, r"with `stop:` set \(clear it once they\s+answer\)")

    def test_the_final_summary_sets_done_then_asks_about_branches_once(self):
        self.assertRegex(COORDINATE, r"(?s)final summary.{0,200}`stop: done`.{0,100}ask once whether to delete"
                                     r".{0,200}remove `plan:` and `stop:`")

    def test_state_file_rule_points_at_show_without_a_plugin_root_path(self):
        # Rules reach the model as literal text, so `${CLAUDE_PLUGIN_ROOT}` there stays unexpanded.
        self.assertIn("/rigger-core:coordinate", STATE_FILE)
        self.assertIn("`show`", STATE_FILE)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", STATE_FILE)

    def test_empty_output_is_not_an_ok(self):
        for out in ("", "\n", "coord-state: refused: x\n", "Python 3.9+ not found\n"):
            self.assertFalse(re.search(r"^coord-state: ok ", out, re.M))
        self.assertTrue(re.search(r"^coord-state: ok ", "coord-state: ok p built\n", re.M))


if __name__ == "__main__":
    unittest.main()
