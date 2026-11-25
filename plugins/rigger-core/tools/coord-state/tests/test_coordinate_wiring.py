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
# RIGGER_ORCHESTRATING_MD does the same for the orchestrating-subagents skill.
SKILL = " ".join(Path(os.environ.get("RIGGER_ORCHESTRATING_MD")
                      or CORE / "skills" / "orchestrating-subagents" / "SKILL.md").read_text(encoding="utf-8").split())


def flat_step(number):
    return " ".join(COORDINATE[COORDINATE.index("\n%d. " % number):COORDINATE.index("\n%d. " % (number + 1))].split())


def flat_paragraph(opening):
    start = COORDINATE.index("\n\n" + opening)
    end = COORDINATE.find("\n\n", start + 2)
    return " ".join(COORDINATE[start:end if end != -1 else len(COORDINATE)].split())


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

    def test_brief_step_loads_testing(self):
        step_three = flat_step(3)
        self.assertIn("loads the testing skill the Skill triggers table names and runs only those", step_three)

    def assert_all_in(self, phrases, text):
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_split_step_defines_session_granularity_once(self):
        step_two = flat_step(2)
        self.assert_all_in(("runs in one session on one model", "a part that needs no other is a lane of one",
                            "several small light parts may share a lane"), step_two)
        for stale in ("each in its own session", "as a step in its own session"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, step_two)
                self.assertNotIn(stale, " ".join(COORDINATE.split()))
                self.assertNotIn(stale, SKILL)

    def test_split_step_keeps_reviews_and_other_models_out_of_the_lane(self):
        self.assert_all_in(("A stage on another model gets its own session (a session's model is fixed when it "
                            "starts)", "a lane never reviews its own work: reviews run in a review session, "
                            "fix rounds in the lane's session"), flat_step(2))

    def test_split_step_starts_every_session_in_the_users_turn(self):
        self.assert_all_in(("in this user's turn start every session the plan needs: one per lane, the review "
                            "session and one per stage on another model, plus one spare standby session per model the "
                            "plan uses for parts not in the plan yet",
                            "record each id under `## Side sessions kept` as it starts",
                            "every other one, a lane whose first part waits included, is a standby session",
                            "has no part until it gets one: add none for it",
                            "Later stages go with `SendMessage`, which works on any turn",
                            "a new part to a free standby session on its model",
                            "Only a spare is free: a standby kept for a lane, the review or a model stage waits for "
                            "that work"), flat_step(2))

    def test_split_step_has_no_session_cap_of_its_own_and_keeps_the_load_rule(self):
        self.assert_all_in(('Sessions have no cap of our own (the app\'s cap: "A refused start")',
                            "every stage keeps the `rigger-core:orchestrating-subagents` load rule in its session",
                            "a build or test starts only while `mem-check` allows",
                            "heavy jobs (WSL, Docker, wp-env, e2e) run at most 2 at a time across all lanes",
                            "You keep that count: send a heavy stage only while fewer than 2 are out"), flat_step(2))
        self.assertNotIn("with no cap on their number", flat_step(2))

    def test_the_coordinator_does_not_implement_and_names_every_agent_exception(self):
        self.assert_all_in(("you do not implement",
                            'A background agent never stands in for a session, except under host `none`, where '
                            'sessions cannot start (last paragraph) and in "A refused start"'), flat_step(2))
        self.assertIn("`none`: run the parts with background subagents", flat_step(1))
        self.assertIn("run the parts with background subagents instead", flat_paragraph("Where this environment"))

    def test_a_refused_start_tries_a_standby_session_before_a_card_or_an_agent(self):
        refused = flat_paragraph("A refused start:")
        self.assertIn("give the step to a free standby session on its model with `SendMessage`", refused)
        self.assertLess(refused.index("free standby session"), refused.index("propose the session as a card"))
        self.assertLess(refused.index("free standby session"), refused.index("background agent"))
        self.assertIn("else to a free standby session on its model, else set `stop: app-refused`", flat_step(8))

    def test_standby_sessions_have_a_brief_and_a_close(self):
        self.assert_all_in(("A standby session's brief says only: end this turn at once with the line "
                            "`standby: ready`", "take your first message from the coordinator as the brief",
                            "step lists but the model, fixed at its start",
                            "its `standby: ready` note needs no action"), flat_step(3))
        self.assertIn("A standby session stays there as `standby` until it gets a part; close the unused ones "
                      "once every part is merged", flat_step(5))

    def test_cli_host_starts_no_standby_sessions(self):
        self.assert_all_in(("no standby sessions: a terminal start is never refused for the turn that asks for it, "
                            "so start each session when its stage is due", "an idle standby would read as `done`"),
                           CLI_HOST)

    def test_skill_allows_coordinator_sessions_and_shares_the_heavy_slots(self):
        self.assert_all_in(("or `/rigger-core:coordinate` runs a plan (the sessions its step 2 lists)",
                            "the sessions of one coordinator share these 2 slots, and the coordinator counts them, "
                            "lock or not"), SKILL)

    def test_skill_and_command_cap_the_same_heavy_jobs_at_two(self):
        pattern = r"[Hh]eavy jobs \(WSL, Docker, wp-env, e2e\) run at most (\d+)"
        self.assertEqual(re.findall(pattern, SKILL), ["2"])
        self.assertEqual(re.findall(pattern, flat_step(2)), ["2"])

    def test_step_two_has_lane_start_and_load_paragraphs(self):
        raw = COORDINATE[COORDINATE.index("\n2. "):COORDINATE.index("\n3. ")]
        paragraphs = [" ".join(chunk.split()) for chunk in raw.split("\n\n") if chunk.strip()]
        self.assertEqual(len(paragraphs), 3, paragraphs)
        lanes, start, load = paragraphs
        self.assertIn("a lane is a chain of parts", lanes)
        self.assertIn("start every session the plan needs", start)
        self.assertIn("Sessions have no cap of our own", load)
        self.assertNotIn("start every session", lanes + load)
        self.assertNotIn("Sessions have no cap", lanes + start)

    def test_a_lanes_next_part_waits_for_the_merge(self):
        self.assertRegex(flat_step(2), r"[Aa] lane's next part starts only after the previous part is merged")
        self.assert_all_in(("add it with `--waits-on <previous part>`",
                            "`coord_state` refuses an earlier `move <part> started`"), flat_step(2))

    def test_one_owner_of_the_full_gate(self):
        for number in (3, 4):
            step = flat_step(number)
            with self.subTest(step=number):
                self.assertRegex(step, r"[Aa] side session runs the full gate on its branch before it reports")
                self.assertIn("you run the full gate only once per merge point", step)
                self.assertNotIn("the gate are yours, at checkpoints", step)
                self.assertNotIn("at checkpoints", step)

    def test_over_the_app_cap_the_user_starts_a_card_and_no_agent_takes_it(self):
        flat = " ".join(COORDINATE.split())
        self.assertIn("past the app's cap of sessions working at once", flat)
        self.assertIn("never to a background agent without the user's OK", flat)
        self.assertIn("once a session ends, give the step to a free standby session on its model", flat)

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
