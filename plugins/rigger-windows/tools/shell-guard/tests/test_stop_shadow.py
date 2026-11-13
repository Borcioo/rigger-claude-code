"""The Stop gate end to end: the script as the launcher starts it, over synthetic sessions with real record shapes.

python -I -X utf8 test_stop_shadow.py
"""
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
LAB_DIR = os.path.join(os.path.dirname(TOOL_DIR), "gate-lab")
SCRIPT = os.path.join(TOOL_DIR, "stop_shadow.py")
sys.dont_write_bytecode = True
sys.path[:0] = [TOOL_DIR, LAB_DIR, os.path.join(LAB_DIR, "tests")]
import lab_fixtures as fx  # noqa: E402
import replay  # noqa: E402
import report  # noqa: E402
import stop_shadow  # noqa: E402
import turn_evidence as te  # noqa: E402

CLOSING = "All done."
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
KILL_AFTER = 12
# A pass takes about 2.7 s, a hang is killed at KILL_AFTER.
HUNG_BOUND = 6.0
# What a hook that is not under test for its budget may take: far more than a loaded machine needs.
LONG_WAIT = 30
GATES = {"checks": ["pytest"], "scope_commit": ["**/*.py"], "scope_stop": ["**/*.py"], "commit": "shadow"}
NO_OUTPUT = "(Bash completed with no output)"
BACKGROUNDED = "Command running in background with ID: b1. Output is being written to: x.output"
PERSISTED = ("<persisted-output>\nOutput too large (45.2KB). Full output saved to: x/tool-results/toolu_1.txt\n\n"
             "Preview (first 2KB):\ncollected 900 items\n...\n</persisted-output>")
COMMITTED = "[develop 1a2b3c4] change\n 1 file changed, 2 insertions(+)"

# Runs the script's main with `stub` applied first. It reports what the run opened and imported.
DRIVER = """
import builtins, json, os, sys
tool, report, stub = sys.argv[1:4]
sys.path.insert(0, tool)
import stop_shadow
real_exit, real_open, opened = os._exit, builtins.open, []

def write_report():
    with real_open(report, "w", encoding="utf-8") as handle:
        json.dump({"opened": opened, "reader": "turn_evidence" in sys.modules}, handle)

def leave(code):
    write_report()
    real_exit(code)

os._exit = leave
exec(stub)
stop_shadow.main()
write_report()
"""
SPY = """
def spying_open(path, *args, **kwargs):
    opened.append(str(path))
    if str(path) == TRANSCRIPT:
        raise OSError("refused")
    return real_open(path, *args, **kwargs)

builtins.open = spying_open
"""
# The extra thread keeps a normal exit from ever finishing, as anything left running in the process could.
HANG = """
import threading, time
import turn_evidence

def hung(path):
    time.sleep(3600)

turn_evidence.reversed_lines = hung
threading.Thread(target=time.sleep, args=(3600,)).start()
"""
HOLD_ROWS = """
import turn_evidence
read_changes, held = turn_evidence.turn_changes, []

def holding(streams, start):
    rows = os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "turn-gate", "rows")
    held.extend(real_open(os.path.join(rows, name), "rb") for name in os.listdir(rows))
    return read_changes(streams, start)

turn_evidence.turn_changes = holding
"""
UNLINK_REFUSED = """
def refused(path):
    raise PermissionError(13, "held", path)

os.unlink = refused
"""
EVIDENCE_UNREADABLE = """
import turn_evidence

def unreadable(streams, stamp, budget):
    raise OSError("gone")

turn_evidence.evidence_after = unreadable
"""
PASS_OUT_OF_TIME = """
import turn_evidence
turn_evidence.last_pass = lambda *args: turn_evidence.Pass("", "budget")
"""
HIDDEN_OUT_OF_TIME = """
import turn_evidence
turn_evidence.last_pass = lambda *args: turn_evidence.Pass("", "budget", "9999")
"""
EVIDENCE_OUT_OF_TIME = """
import turn_evidence
turn_evidence.evidence_after = lambda streams, stamp, budget: "unknown"
"""
# Only the evidence read blocks: the changes and the pass scan before it read as usual.
EVIDENCE_READ_BLOCKED = """
import threading
import turn_evidence
read_evidence = turn_evidence._hands_on

def blocked(path):
    threading.Event().wait()

def hands_on(streams, stamp):
    turn_evidence.reversed_lines = blocked
    return read_evidence(streams, stamp)

turn_evidence._hands_on = hands_on
"""
EDITS_OUT_OF_TIME = """
import turn_evidence
turn_evidence.turn_edits = lambda streams, start, budget: None
"""
CHANGES_RAISE = """
import turn_evidence

def broken(streams, start):
    raise ValueError("bad record")

turn_evidence.turn_changes = broken
"""


class StopCase(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="stop-shadow-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.data = os.path.join(self.base, "data")
        self.config = os.path.join(self.base, "config")
        self.repo = fx.make_repo(self.base, "repo")
        self.write_gates(self.repo, GATES)
        self.session = fx.Session(self.config, "project", fx.SID_A, self.repo)
        self.session.prompt()

    def write_gates(self, repo, gates):
        os.makedirs(repo + "/.claude", exist_ok=True)
        with open(repo + "/.claude/gates.json", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(gates if isinstance(gates, str) else json.dumps(gates))

    def event(self, closing, **payload):
        event = {
            "session_id": self.session.sid, "transcript_path": self.session.main, "cwd": self.repo,
            "hook_event_name": "Stop", "permission_mode": "default", "stop_hook_active": False,
            "last_assistant_message": closing, "prompt_id": self.session.prompt_id, "background_tasks": [],
        }
        event.update(payload)
        return event

    def run_hook(self, event, stub="", data="default", wait=LONG_WAIT, direct=False):
        """One Stop. Stays silent and exits 0 whatever it found. Returns the driver's report.

        The hook waits `wait` seconds: a test that is not about the budget gives it so many that a loaded machine
        cannot run it out, and one that is passes None for the real wait. `direct` starts the script as the launcher
        does, with the real wait."""
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_DATA", None)
        if data is not None:
            env["CLAUDE_PLUGIN_DATA"] = self.data if data == "default" else data
        report = os.path.join(self.base, "report.json")
        patch = "" if wait is None else "stop_shadow.STOP_WAIT = %r\n" % wait
        tail = [SCRIPT] if direct else ["-c", DRIVER, TOOL_DIR, report, patch + stub]
        try:
            proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-S"] + tail, env=env, cwd=self.base,
                                  input=json.dumps(event).encode("utf-8"), capture_output=True, timeout=KILL_AFTER)
        except subprocess.TimeoutExpired:
            self.fail("the hook was still running after %d s" % KILL_AFTER)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        if direct:
            return None
        with open(report, encoding="utf-8") as handle:
            return json.load(handle)

    def close(self, closing, stub="", wait=LONG_WAIT, **payload):
        """The assistant closes the turn on `closing`, and Stop fires."""
        self.session.say(closing)
        self.session.save()
        return self.run_hook(self.event(closing, **payload), stub, wait=wait)

    def rows(self):
        """{key12: parsed lines} of every row file."""
        folder = os.path.join(self.data, "turn-gate", "rows")
        found = {}
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
            with open(os.path.join(folder, name), "rb") as handle:
                found[name[: -len(".json")]] = [json.loads(line) for line in handle.read().splitlines()]
        return found

    def key(self, closing):
        return te.row_key(self.session.sid, self.session.prompt_id, closing)

    def row(self, closing, stub="", **payload):
        """The single line a closing writes."""
        self.close(closing, stub, **payload)
        rows = self.rows()
        self.assertIn(self.key(closing), rows)
        self.assertEqual(len(rows[self.key(closing)]), 1)
        return rows[self.key(closing)][0]

    def verdict(self, closing, **payload):
        return self.row(closing, **payload)["verdict"]

    def assert_no_row(self, closing, **payload):
        self.close(closing, **payload)
        self.assertEqual(self.rows(), {})


class HiddenChecks(StopCase):
    def judged(self, *steps):
        """What the closing of a fresh turn logs after `steps`: (verdict, reason, evidence)."""
        for step in steps:
            step()
        row = self.row(CLOSING)
        for field in ("verdict", "reason", "evidence"):
            self.assertIn(field, row)
        return row["verdict"], row["reason"], row["evidence"]

    def fresh_session(self, sid):
        self.session = fx.Session(self.config, "project", sid, self.repo)
        self.session.prompt()

    def in_both_orders(self, command, output, is_error=False):
        """The same call after the last change, then before it, in two sessions that differ in nothing else."""
        def check():
            self.session.shell(command, output, is_error)

        def change():
            self.session.edit("a.py")

        found = []
        for sid, steps in ((fx.SID_A, (change, check)), (fx.SID_B, (check, change))):
            self.fresh_session(sid)
            found.append(self.judged(*steps))
        return found

    def assert_hidden_only_after_the_change(self, command, output):
        self.assertEqual(self.in_both_orders(command, output), [("unknown", "hidden", None), ("flag", "ok", None)])

    def test_a_piped_check(self):
        self.assert_hidden_only_after_the_change("pytest | tail -1", "some lines")

    def test_a_backgrounded_check(self):
        self.assert_hidden_only_after_the_change("pytest", BACKGROUNDED)

    def test_a_check_sent_to_the_background_by_the_shell(self):
        self.assert_hidden_only_after_the_change("pytest &", "started")

    def test_a_persisted_check(self):
        self.assert_hidden_only_after_the_change("pytest 2>&1 | tee log", PERSISTED)

    def test_a_check_in_a_call_that_commits(self):
        self.assert_hidden_only_after_the_change('pytest && git commit -m "change"', COMMITTED)

    def test_a_readable_pass_keeps_its_verdicts(self):
        self.assertEqual(self.in_both_orders("pytest", "12 passed in 0.5s"),
                         [("verified", None, "check"), ("flag", "ok", None)])

    def test_a_readable_failure_keeps_its_verdicts(self):
        for label, command, output, is_error in (
                ("plain", "pytest", "1 failed in 0.5s", False),
                ("piped", "pytest | tail -1", "1 failed in 0.5s", False),
                ("in a call that commits", 'pytest && git commit -m "change"', "Exit code 1\n1 failed in 0.5s", True)):
            with self.subTest(label):
                self.assertEqual(self.in_both_orders(command, output, is_error), [("flag", "ok", None)] * 2)
                shutil.rmtree(self.data)

    def test_a_pass_wins_over_a_newer_hidden_check(self):
        self.session.edit("a.py")
        self.session.check()
        self.session.shell("pytest | tail -1", "some lines")
        self.assertEqual(self.judged(), ("verified", None, "check"))

    def test_a_pass_wins_over_an_older_hidden_check(self):
        self.session.edit("a.py")
        self.session.shell("pytest | tail -1", "some lines")
        self.session.check()
        self.assertEqual(self.judged(), ("verified", None, "check"))

    def test_a_pass_older_than_the_change_does_not_win(self):
        self.session.check()
        self.session.edit("a.py")
        self.session.shell("pytest | tail -1", "some lines")
        self.assertEqual(self.judged(), ("unknown", "hidden", None))

    def test_hands_on_evidence_wins_over_a_hidden_check(self):
        self.session.edit("a.py")
        self.session.shell("pytest | tail -1", "some lines")
        self.session.shell("curl -s http://localhost/a", "<html>page</html>")
        self.assertEqual(self.judged(), ("verified", None, "curl"))

    def test_a_subagents_hidden_check_counts(self):
        self.session.edit("a.py")
        self.session.in_subagent("a1").shell("pytest | tail -1", "some lines")
        self.session.in_main()
        self.assertEqual(self.judged(), ("unknown", "hidden", None))

    def test_a_hidden_check_in_another_repo_changes_nothing(self):
        other = fx.make_repo(self.base, "other")
        self.session.edit("a.py")
        self.session.shell("cd '%s' && pytest | tail -1" % other, "some lines")
        self.assertEqual(self.judged(), ("flag", "ok", None))

    def test_a_hidden_check_in_the_same_instant_as_the_change_is_not_newer(self):
        self.session.edit("a.py")
        self.session.shell("pytest | tail -1", "some lines")
        records = self.session.streams[self.session.main]
        records[-2]["timestamp"] = records[-3]["timestamp"]
        self.assertEqual(self.judged(), ("flag", "ok", None))

    def test_a_scan_out_of_time_with_a_hidden_check_says_hidden(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, HIDDEN_OUT_OF_TIME)
        self.assertEqual((row["verdict"], row["reason"]), ("unknown", "hidden"))

    def test_a_hidden_row_reaches_the_lab_as_its_own_column_and_no_fire(self):
        self.session.edit("a.py")
        self.session.shell("pytest | tail -1", "some lines")
        row = self.row(CLOSING)
        self.assertEqual(replay.report.verdict_column(row), replay.report.HIDDEN)
        self.assertNotEqual(row["verdict"], replay.FIRE["stop"])


class Verdicts(StopCase):
    def test_claim_and_edit_log_a_flag(self):
        self.session.edit("a.py")
        self.session.edit("b.py")
        edited = self.session.streams[self.session.main][-1]["timestamp"]
        row = self.row(CLOSING)
        self.assertEqual(row["verdict"], "flag")
        self.assertEqual(row["claim"], "done")
        self.assertEqual(row["code_changes"], 2)
        self.assertEqual(row["fingerprint"], te.row_key(edited))
        self.assertEqual(row["sid8"], fx.SID_A[:8])
        self.assertEqual(row["key12"], self.key(CLOSING))
        self.assertEqual(row["repo8"], te.row_key(te.norm(self.repo))[:8])
        self.assertIsNone(row["evidence"])
        self.assertIs(row["msg_in_tail"], True)
        try:
            stamp = time.strptime(row["ts"], STAMP_FORMAT)
        except ValueError:
            self.fail("not a UTC stamp: %r" % row["ts"])
        self.assertEqual(time.strftime(STAMP_FORMAT, stamp), row["ts"])
        self.assertLess(abs(calendar.timegm(stamp) - time.time()), 120)
        self.assertIsInstance(row["ms"], int)

    def test_every_field_the_lab_reads_is_in_the_row(self):
        self.session.edit("a.py")
        row = self.row(CLOSING)
        self.assertEqual(sorted(row), sorted(stop_shadow.ROW_FIELDS))
        self.assertEqual(sorted(fx.stop_row(fx.SID_A, "p", CLOSING)), sorted(set(row) - {"reason"}))

    def test_claims_each_alone(self):
        self.session.edit("a.py")
        for closing, word in (("Fixed.", "Fixed"), ("All tests passing.", "All tests passing"), ("It works now.", "works now"),
                              ("Merged.", "Merged"), ("Shipped.", "Shipped")):
            with self.subTest(closing):
                self.assertEqual(self.row(closing)["claim"], word)

    def test_paraphrased_claim(self):
        self.session.edit("a.py")
        for closing in ("That bug is gone: the change is in and it works now.", "Everything is ready to ship."):
            with self.subTest(closing):
                self.assertEqual(self.verdict(closing), "flag")

    def test_claim_in_the_last_paragraph(self):
        self.session.edit("a.py")
        closing = "Here is what changed in the parser and why.\n\nThe list follows.\n\nAll done."
        self.assertEqual(self.verdict(closing), "flag")

    def test_claim_in_the_last_paragraph_past_the_lead(self):
        self.session.edit("a.py")
        closing = "Some words about it. " * 20 + "\n\nThe list follows.\n\nAll done."
        self.assertGreater(closing.index("All done"), stop_shadow.LEAD)
        self.assertEqual(self.row(closing)["claim"], "done")

    def test_claim_past_the_lead_and_not_in_the_last_paragraph_is_no_claim(self):
        self.session.edit("a.py")
        self.assert_no_row("Some words about it. " * 20 + "That part is done.\n\nThe list follows.")

    def test_negated_claims_write_nothing(self):
        self.session.edit("a.py")
        for closing in ("Not done yet.", "This is not fixed.", "It is still not working.", "It is not ready to ship."):
            with self.subTest(closing):
                self.assert_no_row(closing)

    def test_a_later_claim_survives_a_negated_one(self):
        self.session.edit("a.py")
        self.assertEqual(self.row("The docs are not done, but the parser is done.")["claim"], "done")

    def test_check_after_the_edit_verifies(self):
        self.session.edit("a.py")
        self.session.check()
        row = self.row(CLOSING)
        self.assertEqual((row["verdict"], row["evidence"]), ("verified", "check"))

    def test_check_before_the_edit_does_not_verify(self):
        self.session.check()
        self.session.edit("a.py")
        self.assertEqual(self.verdict(CLOSING), "flag")

    def test_failing_check_after_the_edit_does_not_verify(self):
        self.session.edit("a.py")
        self.session.shell("pytest", "1 failed in 0.5s")
        self.assertEqual(self.verdict(CLOSING), "flag")

    def test_a_subagents_check_verifies(self):
        self.session.edit("a.py")
        self.session.in_subagent("a1").check()
        self.session.in_main()
        self.assertEqual(self.verdict(CLOSING), "verified")

    def stamp_of_last_record(self):
        return self.session.streams[self.session.path][-1]["timestamp"]

    def test_a_later_edit_in_a_subagent_is_not_vouched_for_by_the_check_before_it(self):
        self.session.edit("a.py")
        self.session.check()
        self.session.in_subagent("a1").edit("b.py")
        edited = self.stamp_of_last_record()
        self.session.in_main()
        row = self.row(CLOSING)
        self.assertEqual((row["verdict"], row["code_changes"]), ("flag", 1))
        self.assertEqual(row["fingerprint"], te.row_key(edited))

    def test_a_subagent_edit_before_the_check_stays_verified(self):
        self.session.edit("a.py")
        self.session.in_subagent("a1").edit("b.py")
        self.session.in_main()
        self.session.check()
        self.assertEqual(self.verdict(CLOSING), "verified")

    def test_a_later_subagent_edit_out_of_scope_or_in_another_repo_leaves_the_check_standing(self):
        other = fx.make_repo(self.base, "other")
        self.write_gates(other, GATES)
        self.session.edit("a.py")
        self.session.check()
        sub = self.session.in_subagent("a1")
        sub.edit("README.md")
        sub.call("Edit", {"file_path": other + "/c.py"}, "updated")
        sub.call("Edit", {"file_path": self.base + "/loose/d.py"}, "updated")
        self.session.in_main()
        self.assertEqual(self.verdict(CLOSING), "verified")

    def test_an_older_turns_edit_stamped_late_is_not_this_turns_last(self):
        self.session.edit("a.py")
        older = self.session.streams[self.session.main][-2:]
        self.session.prompt()
        self.session.edit("b.py")
        self.session.check()
        for record in older:
            record["timestamp"] = fx.iso(self.session.days_ago, 500)
        self.assertEqual(self.verdict(CLOSING), "verified")

    def test_a_check_in_the_same_instant_as_the_edit_does_not_verify(self):
        self.session.edit("a.py")
        self.session.check()
        records = self.session.streams[self.session.main]
        records[-2]["timestamp"] = records[-3]["timestamp"]
        self.assertEqual(self.verdict(CLOSING), "flag")

    def test_a_closing_inside_a_subagent_reads_that_subagents_stream(self):
        sub = self.session.in_subagent("a1")
        sub.prompt("Do the part")
        sub.edit("a.py")
        row = self.row(CLOSING, agent_id="a1")
        self.assertEqual((row["verdict"], row["code_changes"], row["msg_in_tail"]), ("flag", 1, True))

    def test_browser_call_after_the_edit_verifies(self):
        self.session.edit("a.py")
        self.session.call("mcp__claude-in-chrome__navigate", {"url": "http://localhost"}, "loaded")
        row = self.row(CLOSING)
        self.assertEqual((row["verdict"], row["evidence"]), ("verified", "browser"))

    def test_browser_call_before_the_edit_does_not_verify(self):
        self.session.call("mcp__claude-in-chrome__navigate", {"url": "http://localhost"}, "loaded")
        self.session.edit("a.py")
        self.assertEqual(self.verdict(CLOSING), "flag")

    def test_curl_with_output_after_the_edit_verifies(self):
        self.session.edit("a.py")
        self.session.shell("curl -s http://localhost/a", "<html>page</html>")
        row = self.row(CLOSING)
        self.assertEqual((row["verdict"], row["evidence"]), ("verified", "curl"))

    def test_curl_that_hides_its_output_does_not_verify(self):
        self.session.edit("a.py")
        self.session.shell("curl -s http://localhost/a >/dev/null", NO_OUTPUT)
        self.assertEqual(self.verdict("All done, first."), "flag")
        self.session.shell("curl -s http://localhost/a; echo ok", "ok")
        self.assertEqual(self.verdict("All done, second."), "flag")

    def test_disclosure(self):
        self.session.edit("a.py")
        for closing in ("Fixed, but the tests were not run.", "Done. I haven't run it.", "Done, untested.",
                        "Done, but I did not run the tests.", "Fixed, to be checked in the browser.",
                        "Done, I have not verified it."):
            with self.subTest(closing):
                self.assertEqual(self.verdict(closing), "disclosed")

    def test_status_closing(self):
        self.session.edit("a.py")
        for closing in ("Task 1 is done.\n\nTask 2 is still running.", "Task 1 is done, waiting for the review.",
                        "Task 8 is done.\n\nTwo more tasks are still running.", "Task 4 is done. Review in the background."):
            with self.subTest(closing):
                self.assertEqual(self.verdict(closing), "disclosed")

    def test_status_early_in_a_closing_is_not_its_ending(self):
        self.session.edit("a.py")
        self.assertEqual(self.verdict("Two more tasks are still running.\n\nTask 8 is done."), "flag")

    def test_disclosure_and_deferral_with_no_gated_change_write_nothing(self):
        self.session.edit("README.md")
        for closing, payload in (("Done, untested.", {}), ("Task 1 is done, waiting for the review.", {}),
                                 (CLOSING, {"background_tasks": [{"id": "b1", "type": "shell"}]})):
            with self.subTest(closing):
                self.assert_no_row(closing, **payload)

    def test_doc_only_edit_writes_nothing(self):
        self.session.edit("README.md")
        self.assert_no_row(CLOSING)

    def test_one_file_in_scope_is_enough_and_only_it_counts(self):
        self.session.edit("README.md")
        self.session.edit("a.py")
        self.assertEqual(self.row(CLOSING)["code_changes"], 1)

    def test_no_edit_writes_nothing(self):
        self.assert_no_row(CLOSING)

    def test_background_task_defers(self):
        self.session.edit("a.py")
        self.assertEqual(self.verdict(CLOSING, background_tasks=[{"id": "b1", "type": "shell"}]), "deferred")

    def test_stop_hook_active_writes_nothing(self):
        self.session.edit("a.py")
        self.assert_no_row(CLOSING, stop_hook_active=True)

    def test_task_notification_mid_turn_keeps_the_edits(self):
        self.session.edit("a.py")
        self.session.notification()
        self.assertEqual(self.verdict(CLOSING), "flag")

    def test_a_new_prompt_drops_the_last_turns_edits(self):
        self.session.edit("a.py")
        self.session.prompt()
        self.assert_no_row(CLOSING)

    def test_a_subagents_edit_is_not_this_closings_change(self):
        self.session.in_subagent("a1").edit("a.py")
        self.session.in_main()
        self.assert_no_row(CLOSING)

    def test_closing_not_yet_in_the_transcript_shows_in_the_row(self):
        self.session.edit("a.py")
        self.session.save()
        self.run_hook(self.event(CLOSING))
        self.assertIs(self.rows()[self.key(CLOSING)][0]["msg_in_tail"], False)

    def test_gate3_16_shape(self):
        for _ in range(3):
            self.session.edit("assets/css/home.css")
        self.write_gates(self.repo, dict(GATES, scope_stop=["**/*.css"]))
        self.session.shell('Set-Location x; git add -A; git commit -q -m "fix: arrow"', "Shell cwd was reset",
                           name="PowerShell")
        closing = ("Fixed.\n\n- **Arrow** sits in the middle of the circle.\n- **Icon** only grows by 6 %.\n\n"
                   "Reload the home page and check.")
        self.assertEqual(self.verdict(closing), "flag")

    def test_gate3_34_shape(self):
        self.session.edit(".github/workflows/deploy-dev.yml")
        self.session.edit(".gitignore")
        self.write_gates(self.repo, dict(GATES, scope_stop=["**/*.yml"]))
        self.session.shell("git add .gitignore; git push -u origin main 2>&1 | tail -3; gh secret set KEY", "set")
        self.session.shell("du -sh .git | cut -f1", "4M")
        closing = ("Workflow done and pushed.\n\n1. **Database.** It has to be moved.\n\n"
                   "3. **Panel password.** I suggest an HTTP password on the whole site. Do it?")
        row = self.row(closing)
        self.assertEqual((row["verdict"], row["code_changes"]), ("flag", 1))


class NoRead(StopCase):
    def test_no_claim_reads_no_file_and_writes_none(self):
        self.session.edit("a.py")
        report = self.close("Which of the two do you prefer?", "TRANSCRIPT = %r\n%s" % (self.session.main, SPY))
        self.assertEqual(report, {"opened": [], "reader": False})
        self.assertFalse(os.path.exists(self.data))

    def test_the_spy_sees_a_claim_reading_the_transcript(self):
        self.session.edit("a.py")
        report = self.close(CLOSING, "TRANSCRIPT = %r\n%s" % (self.session.main, SPY))
        self.assertIn(self.session.main, report["opened"])
        self.assertEqual([line["reason"] for line in self.rows()[self.key(CLOSING)]], ["unreadable"])

    def test_unset_plugin_data_imports_nothing_and_writes_nothing(self):
        self.session.edit("a.py")
        self.session.say(CLOSING)
        self.session.save()
        before = sorted(os.listdir(self.base))
        for data in (None, ""):
            with self.subTest(data=data):
                report = self.run_hook(self.event(CLOSING), "pass", data=data)
                self.assertFalse(report["reader"])
                self.assertEqual(sorted(set(os.listdir(self.base)) - {"report.json"}), before)


class RowFile(StopCase):
    def test_no_gates_json_leaves_no_file(self):
        os.unlink(self.repo + "/.claude/gates.json")
        self.session.edit("a.py")
        self.assert_no_row(CLOSING)
        self.assertEqual(os.listdir(os.path.join(self.data, "turn-gate", "rows")), [])

    def test_gates_json_without_scope_stop_is_no_gate(self):
        self.write_gates(self.repo, {"checks": ["pytest"], "commit": "shadow"})
        self.session.edit("a.py")
        self.assert_no_row(CLOSING)

    def test_edit_outside_any_repo_leaves_no_file(self):
        self.session.call("Edit", {"file_path": self.base + "/loose/a.py"}, "updated")
        self.assert_no_row(CLOSING)

    def test_malformed_gates_json(self):
        self.session.edit("a.py")
        for closing, gates in (("All done, broken file.", "{"), ("All done, a list.", "[]"),
                               ("All done, wrong type.", {"checks": "pytest", "scope_stop": ["**/*.py"]})):
            with self.subTest(closing):
                self.write_gates(self.repo, gates)
                row = self.row(closing)
                self.assertEqual((row["verdict"], row["reason"]), ("error", "gates"))

    @unittest.skipUnless(os.name == "nt", "only Windows refuses to delete an open file")
    def test_a_reader_holding_the_row_open_gives_ungated(self):
        os.unlink(self.repo + "/.claude/gates.json")
        self.session.edit("a.py")
        row = self.row(CLOSING, HOLD_ROWS)
        self.assertEqual((row["verdict"], row["claim"]), ("ungated", "done"))

    def test_a_refused_unlink_gives_ungated(self):
        os.unlink(self.repo + "/.claude/gates.json")
        self.session.edit("a.py")
        self.assertEqual(self.row(CLOSING, UNLINK_REFUSED)["verdict"], "ungated")

    def test_two_identical_closings_under_different_prompts_give_two_rows(self):
        self.session.edit("a.py")
        self.close("Done.")
        self.session.prompt()
        self.session.edit("b.py")
        self.close("Done.")
        self.assertEqual(len(self.rows()), 2)

    def test_a_second_stop_with_the_same_key_writes_nothing(self):
        self.session.edit("a.py")
        self.close(CLOSING)
        path = os.path.join(self.data, "turn-gate", "rows", self.key(CLOSING) + ".json")
        with open(path, "wb") as handle:
            handle.write(b'"first"\n')
        self.run_hook(self.event(CLOSING))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b'"first"\n')

    def test_row_cap(self):
        self.assertEqual(stop_shadow.ROW_CAP, replay.ROW_CAP)
        self.session.edit("a.py")
        folder = os.path.join(self.data, "turn-gate", "rows")
        os.makedirs(folder)
        fx.write_row(folder, "existing")
        self.close("All done, under the cap.", "stop_shadow.ROW_CAP = 2\n")
        self.assertEqual(len(os.listdir(folder)), 2)
        self.close("All done, at the cap.", "stop_shadow.ROW_CAP = 2\n")
        self.assertEqual(len(os.listdir(folder)), 2)

    def test_the_script_run_as_the_launcher_starts_it_writes_its_row(self):
        self.session.edit("a.py")
        self.session.say(CLOSING)
        self.session.save()
        self.run_hook(self.event(CLOSING, background_tasks=[{"id": "b1", "type": "shell"}]), direct=True)
        rows = self.rows()
        self.assertIn(self.key(CLOSING), rows)
        self.assertEqual([line["verdict"] for line in rows[self.key(CLOSING)]], ["deferred"])

    def test_bytecode_goes_to_the_data_folder(self):
        self.session.edit("a.py")
        self.close(CLOSING)
        compiled = [name for _, _, names in os.walk(os.path.join(self.data, "pycache")) for name in names]
        self.assertTrue(any(name.startswith("turn_evidence.") for name in compiled), compiled)

    def test_data_dir_pointing_at_a_regular_file_exits_0(self):
        self.session.edit("a.py")
        with open(self.data, "wb") as handle:
            handle.write(b"a file")
        self.close(CLOSING)
        with open(self.data, "rb") as handle:
            self.assertEqual(handle.read(), b"a file")

    def test_no_closing_text_is_silent(self):
        self.session.edit("a.py")
        for message in (None, 5):
            with self.subTest(message=message):
                self.run_hook(self.event(message))
                self.assertEqual(self.rows(), {})

    def test_broken_stdin_is_silent(self):
        for raw in (b"", b"[]", b"not json"):
            with self.subTest(raw=raw):
                proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-S", SCRIPT], input=raw,
                                      capture_output=True, timeout=KILL_AFTER)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))


class Repos(StopCase):
    def add_edit(self, repo, name):
        self.session.call("Edit", {"file_path": repo + "/" + name}, "updated")

    def test_edits_across_two_repos_are_judged_each_by_its_own_gates(self):
        other = fx.make_repo(self.base, "other")
        self.write_gates(other, dict(GATES, checks=["vitest"]))
        self.session.edit("a.py")
        self.add_edit(other, "b.py")
        self.session.check()
        self.close(CLOSING)
        lines = {line["repo8"]: line for line in self.rows()[self.key(CLOSING)]}
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[te.row_key(te.norm(self.repo))[:8]]["verdict"], "verified")
        self.assertEqual(lines[te.row_key(te.norm(other))[:8]]["verdict"], "flag")

    def test_a_repo_with_nothing_in_its_own_scope_gets_no_line(self):
        other = fx.make_repo(self.base, "other")
        self.write_gates(other, dict(GATES, scope_stop=["**/*.php"]))
        loose = fx.make_repo(self.base, "loose")
        self.session.edit("a.py")
        self.add_edit(other, "b.py")
        self.add_edit(loose, "c.py")
        self.close(CLOSING)
        self.assertEqual([line["repo8"] for line in self.rows()[self.key(CLOSING)]],
                         [te.row_key(te.norm(self.repo))[:8]])

    def test_one_root_in_two_spellings_is_one_line(self):
        spelled = self.repo.replace("/", os.sep).upper() if os.name == "nt" else self.repo
        self.session.edit("a.py")
        self.add_edit(spelled, "b.py")
        self.assertEqual(self.row(CLOSING)["code_changes"], 2)

    def test_linked_worktree_whose_git_is_a_file(self):
        linked = os.path.join(self.base, "linked").replace("\\", "/")
        os.makedirs(linked)
        with open(linked + "/.git", "w", encoding="utf-8", newline="\n") as handle:
            handle.write("gitdir: ../repo/.git/worktrees/linked\n")
        self.write_gates(linked, GATES)
        self.add_edit(linked, "a.py")
        self.assertEqual(self.row(CLOSING)["repo8"], te.row_key(te.norm(linked))[:8])

    def test_a_folder_with_non_ascii_letters(self):
        repo = fx.make_repo(self.base, "Zürich")
        self.write_gates(repo, GATES)
        self.add_edit(repo, "crème.py")
        self.assertEqual(self.verdict("Fixed."), "flag")


class Failure(StopCase):
    def test_a_read_that_never_returns_leaves_an_empty_file_in_time(self):
        self.session.edit("a.py")
        started = time.monotonic()
        self.close(CLOSING, HANG, wait=None)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, HUNG_BOUND)
        self.assertEqual(self.rows(), {self.key(CLOSING): []})

    def test_missing_transcript_says_so(self):
        self.session.edit("a.py")
        for closing, path in (("All done, gone.", self.session.main + ".gone"), ("All done, unset.", "")):
            with self.subTest(closing):
                row = self.row(closing, transcript_path=path)
                self.assertEqual((row["verdict"], row["reason"]), ("unknown", "unreadable"))

    def test_evidence_read_failing_says_so(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, EVIDENCE_UNREADABLE)
        self.assertEqual((row["verdict"], row["reason"]), ("unknown", "unreadable"))

    def test_any_other_failure_is_an_error_row(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, CHANGES_RAISE)
        self.assertEqual((row["verdict"], row["reason"]), ("error", "ValueError"))

    def test_scan_out_of_time_is_no_flag(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, PASS_OUT_OF_TIME)
        self.assertEqual((row["verdict"], row["reason"]), ("unknown", "budget"))

    def test_evidence_out_of_time_is_no_flag(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, EVIDENCE_OUT_OF_TIME)
        self.assertEqual((row["verdict"], row["reason"]), ("unknown", "budget"))
        self.assertIsNone(row["evidence"])

    def test_evidence_read_that_never_returns_still_writes_its_row_in_time(self):
        self.session.edit("a.py")
        started = time.monotonic()
        row = self.row(CLOSING, EVIDENCE_READ_BLOCKED, wait=None)
        self.assertLess(time.monotonic() - started, HUNG_BOUND)
        self.assertEqual((row["verdict"], row["reason"], row["evidence"]), ("unknown", "budget", None))
        self.assertLess(row["ms"], stop_shadow.STOP_WAIT * 1000)

    def test_edits_out_of_time_is_no_flag(self):
        self.session.edit("a.py")
        row = self.row(CLOSING, EDITS_OUT_OF_TIME)
        self.assertEqual((row["verdict"], row["reason"], row["code_changes"]), ("unknown", "budget", 1))

    def test_a_pass_found_before_the_evidence_ran_out_still_verifies(self):
        self.session.edit("a.py")
        self.session.check()
        row = self.row(CLOSING, EVIDENCE_OUT_OF_TIME)
        self.assertEqual((row["verdict"], row["evidence"]), ("verified", "check"))

    def test_bad_checks_regex_is_an_error_row(self):
        self.write_gates(self.repo, dict(GATES, checks=["("]))
        self.session.edit("a.py")
        self.session.shell("git status", "clean")
        row = self.row(CLOSING)
        self.assertEqual((row["verdict"], row["reason"]), ("error", type(re.error("x")).__name__))

    def test_one_repos_bad_regex_leaves_the_others_line(self):
        other = fx.make_repo(self.base, "other")
        self.write_gates(other, dict(GATES, checks=["("]))
        self.session.edit("a.py")
        self.session.call("Edit", {"file_path": other + "/b.py"}, "updated")
        self.session.shell("git status", "clean")
        self.close(CLOSING)
        self.assertEqual(sorted(line["verdict"] for line in self.rows()[self.key(CLOSING)]), ["error", "flag"])


class FrozenClock:
    """`time` as stop_shadow sees it with monotonic standing still: a budget is then the same on a machine of any speed."""

    def __init__(self, now):
        self.now = now

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class Recording:
    """The reader with the budget of each timed read noted. The reads themselves get a long one."""

    def __init__(self):
        self.budgets = {}

    def __getattr__(self, name):
        return getattr(te, name)

    def turn_edits(self, streams, turn_ts, budget):
        self.budgets["turn_edits"] = budget
        return te.turn_edits(streams, turn_ts, LONG_WAIT)

    def last_pass(self, streams, root, checks, budget):
        self.budgets["last_pass"] = budget
        return te.last_pass(streams, root, checks, LONG_WAIT)

    def evidence_after(self, streams, ts, budget):
        self.budgets["evidence_after"] = budget
        return te.evidence_after(streams, ts, LONG_WAIT)


class Budget(StopCase):
    """_judge and _turn_lines in this process, on a clock that stands still, so the budgets the reader gets can be read."""

    NOW = 1000.0

    def setUp(self):
        super().setUp()
        real = stop_shadow.time
        stop_shadow.time = FrozenClock(self.NOW)
        self.addCleanup(setattr, stop_shadow, "time", real)

    def judge(self, left, reader=te):
        """_judge on a deadline `left` seconds ahead."""
        self.session.save()
        streams = te.stream_paths(self.session.main, None)
        stamp = self.session.streams[self.session.main][-1]["timestamp"]
        line = dict.fromkeys(stop_shadow.ROW_FIELDS)
        edits = [(stamp, self.repo + "/a.py")]
        return stop_shadow._judge(line, self.repo, GATES, [(stamp, "a.py")], edits, streams, reader, self.NOW + left)

    def test_every_timed_read_gets_what_the_deadline_leaves_less_the_spare(self):
        self.session.edit("a.py")
        self.session.say(CLOSING)
        self.session.save()
        reader = Recording()
        row = dict.fromkeys(stop_shadow.ROW_FIELDS)
        lines = stop_shadow._turn_lines(row, self.event(CLOSING), CLOSING, reader, self.NOW + 1.0)
        self.assertEqual([(line["verdict"], line["reason"]) for line in lines], [("flag", "ok")])
        self.assertEqual(reader.budgets, dict.fromkeys(("turn_edits", "last_pass", "evidence_after"),
                                                       1.0 - stop_shadow.ROW_SPARE))

    def test_a_deadline_already_spent_is_a_budget_verdict_without_waiting(self):
        self.session.edit("a.py")
        reached, release, finished = threading.Event(), threading.Event(), threading.Event()
        real = te.reversed_lines

        def blocked(path):
            reached.set()
            release.wait(30)
            finished.set()
            return iter(())

        te.reversed_lines = blocked
        self.addCleanup(setattr, te, "reversed_lines", real)
        self.addCleanup(release.set)
        verdict = self.judge(-5)
        self.assertTrue(reached.wait(30))
        self.assertFalse(finished.is_set())
        self.assertEqual(verdict, ("unknown", "budget"))


class LabJoin(StopCase):
    """The lab recomputes the key from the transcript, so a row it cannot find is lost for the trial."""

    def replay_rows(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config)
        script = os.path.join(LAB_DIR, "replay.py")
        rows = os.path.join(self.data, "turn-gate", "rows")
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", script, "--data", self.data, "--gate", "stop",
                               "--from-shadow", rows], capture_output=True, timeout=60, env=env)
        output = proc.stdout.decode("utf-8")
        self.assertEqual(proc.returncode, 0, output + proc.stderr.decode("utf-8"))
        return output.replace("\r\n", "\n")

    def test_a_flag_joins_the_lab_on_a_hostile_closing(self):
        closing = 'Done: „crème” and "quotes" \\ 🎉\n\n  fixed.\n'
        self.session.edit("a.py")
        self.close(closing)
        self.assertEqual(list(self.rows()), [self.key(closing)])
        output = self.replay_rows()
        self.assertIn("verdict flag 1\n", output)
        self.assertIn("unmatched 0\n", output)

    def test_identical_closings_join_each_to_its_own_turn(self):
        self.session.edit("a.py")
        self.close("Done.")
        self.session.prompt()
        self.session.edit("b.py")
        self.close("Done.")
        output = self.replay_rows()
        self.assertIn("verdict flag 2\n", output)
        self.assertIn("unmatched 0\n", output)


class LabCountsRows(StopCase):
    """report.row_count must find what the real hooks write: a trial whose rows it cannot see reads `not-wired`."""

    def started(self):
        return report.today().isoformat()

    def test_the_stop_script_row_is_counted(self):
        self.session.edit("a.py")
        self.session.say(CLOSING)
        self.session.save()
        self.run_hook(self.event(CLOSING), direct=True)
        self.assertEqual(report.row_count(self.data, "stop", self.started()), 1)
        self.assertEqual(list(self.rows()), [self.key(CLOSING)])

    def test_the_commit_script_row_is_counted(self):
        use_id = "toolu_lab_count"
        event = {"session_id": self.session.sid, "transcript_path": self.session.main, "cwd": self.repo,
                 "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": use_id,
                 "tool_input": {"command": "git commit -m change"}}
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data)
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-S", os.path.join(TOOL_DIR, "shell_guard.py"),
                               "--only-git"], env=env, cwd=self.base, input=json.dumps(event).encode("utf-8"),
                              capture_output=True, timeout=LONG_WAIT)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        self.assertEqual(report.row_count(self.data, "commit", self.started()), 1)
        folder = os.path.join(self.data, "commit-gate", "rows")
        self.assertEqual(os.listdir(folder), [te.row_key(use_id) + ".json"])
        with open(os.path.join(folder, te.row_key(use_id) + ".json"), encoding="ascii") as handle:
            self.assertEqual(json.loads(handle.read())["op"], "commit")


if __name__ == "__main__":
    unittest.main(verbosity=1)
