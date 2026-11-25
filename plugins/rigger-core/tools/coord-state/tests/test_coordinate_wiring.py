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
# RIGGER_CLI_HOST_MD does the same for the terminal steps.
CLI_HOST = " ".join(Path(os.environ.get("RIGGER_CLI_HOST_MD") or CORE / "tools" / "coord-state" / "cli-host.md")
                    .read_text(encoding="utf-8").split())


class CliHostWiring(unittest.TestCase):
    def test_step_one_runs_host_first(self):
        step_one = " ".join(COORDINATE[COORDINATE.index("\n1. "):COORDINATE.index("\n2. ")].split())
        self.assertIn('tools/coord-state/coord_state.py" host', step_one)
        self.assertLess(COORDINATE.index('coord_state.py" host'), COORDINATE.index('coord_state.py" init'))
        self.assertRegex(step_one, r"`desktop` \(the app\): this command as written")
        self.assertRegex(step_one, r"`cli` \(the terminal\): also read `\$\{CLAUDE_PLUGIN_ROOT\}/tools/coord-state/"
                                   r"cli-host\.md` and follow it")
        self.assertRegex(step_one, r"`none`: .{0,60}background subagents")

    def test_cli_host_names_each_terminal_call(self):
        for call in ("claude --bg --name <plan slug>-<part> --model <model> --permission-mode <mode> "
                     '"$(cat <brief file>)"', "with the Bash tool", "under 20 000 characters",
                     "<prefix> host", "<prefix> wait <id>", "run_in_background", "claude attach <id>",
                     'claude --bg --resume <sessionId> "<text>"', "claude stop <id>", "claude rm <id>",
                     "git worktree remove", "## Side sessions kept", "## Keep verbatim",
                     "$CLAUDE_JOB_DIR/tmp/report.md", "<config dir>/jobs/<id>/tmp/report.md",
                     "move <part> started --session <id> --branch <branch>"):
            self.assertIn(call, CLI_HOST)

    def test_cli_host_says_the_mode_is_not_inherited(self):
        self.assertRegex(CLI_HOST, r"A child does not inherit it.{0,120}every child gets that mode")

    def test_cli_host_refuses_a_start_without_its_line(self):
        self.assertRegex(CLI_HOST, r"counts only with exit 0 and its `backgrounded · <id> · <name>` line; anything "
                                   r"else is a refusal: put its output line into the state file and stop that step")

    def test_cli_host_copies_each_report_before_close_deletes_it(self):
        self.assertRegex(CLI_HOST, r"`done`: copy its report to `<scratch>/<part>-report-<n>\.md` .{0,120}"
                                   r"`claude rm` deletes the job folder.{0,40} work from the copy")

    def test_cli_host_keeps_blocked_parts_in_wait(self):
        self.assertRegex(CLI_HOST, r"with every part whose session works or is blocked")
        self.assertRegex(CLI_HOST, r"`claude attach <id>`, and keep it in `wait`")

    def test_cli_host_review_sessions_reuse_the_worktree_and_leave_it(self):
        self.assertRegex(CLI_HOST, r"review or re-review session starts the same way but in the part's existing "
                                   r"worktree, with no new worktree or branch")
        self.assertRegex(CLI_HOST, r"A review session in a builder's worktree gets `stop` and `rm` only")

    def test_cli_host_closes_on_every_end_state(self):
        self.assertRegex(CLI_HOST, r"on every end state \(merged, dropped, given up\): `claude stop <id>`")
        self.assertRegex(CLI_HOST, r"compare the kept lines with `claude agents --json --all` and close any orphan")



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
        self.assertIn("loads the testing skill the Skill triggers table names and runs only those", step_three)
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

    def test_the_coordinator_state_rule_lives_in_step_one_and_survives_compaction(self):
        step_one = COORDINATE[COORDINATE.index("\n1. "):COORDINATE.index("\n2. ")]
        text = " ".join(step_one.split())
        self.assertIn("Only this orchestrator session sets `coordinator: true`, never a side session.", text)
        self.assertIn("rewrites the file with `true` before every turn end in which it waits", text)
        self.assertIn("its running sessions are the output of the `show` call, not memory.", text)
        self.assertIn("Copy these two sentences into the state file's `## Keep verbatim`", text)
        # Rules reach the model as literal text, so `${CLAUDE_PLUGIN_ROOT}` there stays unexpanded.
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", STATE_FILE)

    def test_coordinate_marks_a_full_risk_part_reviewed_after_round_two(self):
        step_four = " ".join(COORDINATE[COORDINATE.index("\n4. "):COORDINATE.index("\n5. ")].split())
        self.assertIn("For a Risk-full part, `move <part> reviewed` comes after the second round: a fresh agent of "
                      "the `reviewer` slot reviews again after the first fix round (`orchestrating-subagents`, Fix "
                      "rounds).", step_four)

    def test_an_empty_reviewer_slot_stops_the_plan_as_goal_unreachable(self):
        step_four = " ".join(COORDINATE[COORDINATE.index("\n4. "):COORDINATE.index("\n5. ")].split())
        self.assertIn("With the `reviewer` slot empty, the review is blocked: report it as `BLOCKED` with the install "
                      "line from the session notice, or the `slot reviewer` line `/rigger-core:doctor` prints when "
                      "there is none; never skip the review or give it to an agent outside the slot. "
                      "That part stays `built`; set `stop: goal-unreachable` and ask the user to fill the "
                      "slot as that line says (step 6).", step_four)

    def test_empty_output_is_not_an_ok(self):
        for out in ("", "\n", "coord-state: refused: x\n", "Python 3.9+ not found\n"):
            self.assertFalse(re.search(r"^coord-state: ok ", out, re.M))
        self.assertTrue(re.search(r"^coord-state: ok ", "coord-state: ok p built\n", re.M))


if __name__ == "__main__":
    unittest.main()
