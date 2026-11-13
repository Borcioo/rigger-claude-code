"""Tests for session_watch.py: transcript states on fixtures copied from real lines, and the watch
loop on a fake app folder with a frozen clock.

python -I test_session_watch.py
"""
import datetime
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
SCRIPT = os.path.join(os.path.dirname(HERE), "session_watch.py")
spec = importlib.util.spec_from_file_location("session_watch", SCRIPT)
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

HOST = "local_00000000-0000-4000-8000-000000000001"
CHILD = "local_00000000-0000-4000-8000-000000000002"
CLI = "5f0a73f4-eeb3-4741-ac99-9194659fb309"


def fixture_lines(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read().splitlines()


def newest_ts(name):
    """Newest timestamp of a conversation record in a fixture, read independently of the script."""
    best = None
    for line in fixture_lines(name):
        r = json.loads(line)
        if r.get("type") in ("user", "assistant", "system") and not r.get("isSidechain"):
            t = datetime.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp()
            best = t if best is None or t > best else best
    return best


def ms(stamp):
    return round(datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


class Clock:
    def __init__(self, start, on_sleep=None):
        self.t = float(start)
        self.sleeps = 0
        self.on_sleep = on_sleep

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > 1000:
            raise AssertionError("watch loop did not exit")
        self.t += seconds
        if self.on_sleep:
            self.on_sleep()


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="session-watch-")
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return path

    def transcript(self, lines, cli=CLI, project="proj"):
        return self.write(os.path.join("config", "projects", project, cli + ".jsonl"), "\n".join(lines) + "\n")


class ClassifyTests(Tmp):
    def state(self, lines, now=None, name=None):
        path = self.transcript(lines)
        if now is None:
            now = newest_ts(name) + 3600 if name else 10 ** 10
        return self.classify(path, now)[:2]

    @staticmethod
    def classify(path, now):
        try:
            return sw.classify(path, now)
        except sw.Unreadable as exc:
            return ("raised", str(exc), None)

    def test_summary_after_end_turn_is_idle(self):
        self.assertEqual(self.state(fixture_lines("idle_after_summary.jsonl"), name="idle_after_summary.jsonl")[0], "idle")

    def test_end_turn_without_summary_settles_after_10_s(self):
        lines = fixture_lines("end_turn_no_summary.jsonl")
        t = newest_ts("end_turn_no_summary.jsonl")
        self.assertEqual(self.state(lines, now=t + 9.9)[0], "busy")
        self.assertEqual(self.state(lines, now=t + 10)[0], "idle")

    def test_stop_hook_feedback_before_summary_is_busy(self):
        self.assertEqual(self.state(fixture_lines("feedback_then_summary.jsonl"), name="feedback_then_summary.jsonl")[0], "busy")

    def test_pending_tool_use_is_tool(self):
        self.assertEqual(self.state(fixture_lines("pending_tool.jsonl"), name="pending_tool.jsonl"), ("tool", "Bash"))

    def test_sibling_result_does_not_hide_pending_tool(self):
        got = self.state(fixture_lines("pending_tool_after_sibling_result.jsonl"),
                         name="pending_tool_after_sibling_result.jsonl")
        self.assertEqual(got, ("tool", "Bash"))

    def test_all_results_back_is_busy(self):
        lines = fixture_lines("pending_tool.jsonl")
        ids = [b["id"] for line in lines[:3] for b in json.loads(line)["message"]["content"] if b["type"] == "tool_use"]
        self.assertEqual(len(ids), 1)
        result = {"type": "user", "timestamp": "2026-08-03T10:32:43.404Z", "isSidechain": False,
                  "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": "x"}
                                                          for i in ids]}}
        self.assertEqual(self.state(lines + [json.dumps(result)])[0], "busy")

    def test_user_prompt_is_busy(self):
        self.assertEqual(self.state(fixture_lines("prompt_working.jsonl"), name="prompt_working.jsonl")[0], "busy")

    def test_interrupt_is_idle(self):
        self.assertEqual(self.state(fixture_lines("interrupted.jsonl"), name="interrupted.jsonl")[0], "idle")

    def test_local_command_is_idle(self):
        self.assertEqual(self.state(fixture_lines("local_command.jsonl"), name="local_command.jsonl")[0], "idle")

    def test_api_error_is_busy(self):
        self.assertEqual(self.state(fixture_lines("api_error.jsonl"), name="api_error.jsonl")[0], "busy")

    def test_unknown_system_subtype_is_unknown(self):
        lines = fixture_lines("idle_after_summary.jsonl")
        lines = [line.replace('"stop_hook_summary"', '"brand_new"') for line in lines]
        self.assertEqual(self.state(lines), ("unknown", "system:brand_new"))

    def test_unknown_record_type_content_is_unknown(self):
        odd = {"type": "user", "timestamp": "2026-11-09T23:30:00.000Z", "isSidechain": False,
               "message": {"role": "user", "content": 7}}
        lines = fixture_lines("idle_after_summary.jsonl") + [json.dumps(odd)]
        self.assertEqual(self.state(lines)[0], "unknown")

    def test_unterminated_last_line_is_skipped(self):
        path = self.transcript(fixture_lines("idle_after_summary.jsonl"))
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write('{"type": "assistant", "timestamp": "2026-11-09T23:40:00.000Z", "message": {"con')
        self.assertEqual(self.classify(path, 10 ** 10)[0], "idle")

    def test_corrupt_complete_line_raises(self):
        lines = fixture_lines("idle_after_summary.jsonl") + ["{not json"]
        self.assertEqual(self.state(lines)[0], "raised")

    def test_large_record_after_summary_still_reads_the_summary(self):
        big = json.dumps({"type": "api-request-blob", "message": {"role": "assistant",
                                                                  "content": [{"type": "text", "text": "x" * 300000}]}})
        lines = fixture_lines("pending_tool.jsonl")[:3] + fixture_lines("idle_after_summary.jsonl") + [big]
        self.assertEqual(self.state(lines)[0], "idle")

    def test_sidechain_records_are_ignored(self):
        side = {"type": "assistant", "timestamp": "2026-11-09T23:40:00.000Z", "isSidechain": True,
                "message": {"role": "assistant", "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {}}]}}
        lines = fixture_lines("idle_after_summary.jsonl") + [json.dumps(side)]
        self.assertEqual(self.state(lines)[0], "idle")

    def test_only_metadata_is_empty(self):
        lines = [line for line in fixture_lines("pending_tool.jsonl") if '"assistant"' not in line]
        self.assertEqual(self.state(lines)[0], "empty")

    def test_newest_timestamp_is_returned(self):
        path = self.transcript(fixture_lines("api_error.jsonl"))
        self.assertEqual(sw.classify(path, 10 ** 10)[2], newest_ts("api_error.jsonl"))


class WatchTests(Tmp):
    """The loop: app folder <dir>/app/<account>/<org>/local_*.json, transcripts under <dir>/config."""

    def session_file(self, doc, name=None):
        name = name or doc["sessionId"]
        return self.write(os.path.join("app", "acct", "org", name + ".json"), json.dumps(doc))

    def host(self, notes=None, **extra):
        doc = {"sessionId": HOST, "cliSessionId": "host-cli", "isArchived": False,
               "permissionMode": "bypassPermissions", "queuedSideSessionNotes": notes, "createdAt": 1}
        doc.update(extra)
        return self.session_file(doc)

    def child(self, sid=CHILD, cli=CLI, name="theme-kit", created=1, **extra):
        doc = {"sessionId": sid, "cliSessionId": cli, "createdAt": created, "isArchived": False,
               "permissionMode": "default", "title": "Theme kit",
               "spawnedFrom": {"sessionId": HOST, "taskId": "task_1", "sideSession": True, "name": name}}
        doc.update(extra)
        return self.session_file(doc)

    def run_watch(self, start, env=None, on_sleep=None):
        clock = Clock(start, on_sleep)
        out = io.StringIO()
        e = {"CLAUDE_CODE_HOST_SESSION_ID": HOST, "CLAUDE_CONFIG_DIR": os.path.join(self.dir, "config")}
        if env is not None:
            e = env
        try:
            code = sw.main(["--app-dir", os.path.join(self.dir, "app"), "--state-dir", os.path.join(self.dir, "state")],
                           clock=clock.now, sleep=clock.sleep, out=out, env=e)
        except Exception as exc:  # a crash is a wrong result, compared like any other
            return None, "crash: %r" % exc, clock.t - start
        return code, out.getvalue(), clock.t - start

    # held notes
    NOTE_AT = 1794267188444  # a real note `at`: 23:33:08.444Z

    def held_setup(self):
        self.host([{"sessionId": CHILD, "title": "Theme kit", "report": "r", "at": self.NOTE_AT, "batch": 1}])
        self.child()
        self.transcript(fixture_lines("idle_after_summary.jsonl"))

    def test_held_note_exits_at_120_s(self):
        self.held_setup()
        code, line, waited = self.run_watch(self.NOTE_AT / 1000 + 120)
        self.assertEqual((code, waited), (0, 0))
        self.assertEqual(line, "session-watch: held-note: theme-kit (%s) finished a turn at 23:33:08Z; "
                               "the app holds the note while other side sessions work\n" % CHILD)

    def test_held_note_waits_below_120_s(self):
        self.held_setup()
        code, line, waited = self.run_watch(self.NOTE_AT / 1000 + 119.9)
        self.assertEqual((code, waited), (0, 15))
        self.assertTrue(line.startswith("session-watch: held-note: "), line)

    def test_held_note_is_reported_once(self):
        self.held_setup()
        self.run_watch(self.NOTE_AT / 1000 + 200)
        code, line, waited = self.run_watch(self.NOTE_AT / 1000 + 300)
        self.assertEqual((code, waited), (0, 1800))
        self.assertEqual(line, "session-watch: cap: 30 min; theme-kit (idle)\n")

    OTHER = "local_00000000-0000-4000-8000-000000000003"
    PLAIN = ("session-watch: held-note: %s (%s) finished a turn at %s; the app holds the note while other "
             "side sessions work\n")
    USER = ("session-watch: held-note: user-driven: %s (%s) finished a turn at %s the user drove in its "
            "window; the app delivers the note with the user's next message here\n")
    QUIET_TOOL = ("session-watch: quiet: %s (%s) mid-turn for 5 min, waiting on %s (mode %s): possibly a "
                  "permission prompt or a question in its window\n")

    def test_two_held_notes_are_two_lines_oldest_first(self):
        self.host([{"sessionId": self.OTHER, "title": "Engine", "at": self.NOTE_AT + 60000},
                   {"sessionId": CHILD, "title": "Theme kit", "at": self.NOTE_AT}])
        self.child()
        self.child(sid=self.OTHER, cli="cli-engine", name="engine")
        self.transcript(fixture_lines("idle_after_summary.jsonl"))
        self.transcript(fixture_lines("idle_after_summary.jsonl"), cli="cli-engine")
        code, out, _ = self.run_watch(self.NOTE_AT / 1000 + 400)
        self.assertEqual(out, self.PLAIN % ("theme-kit", CHILD, "23:33:08Z")
                         + self.PLAIN % ("engine", self.OTHER, "23:34:08Z"))

    # one exit, every true condition
    def note_and_quiet_setup(self):
        t = newest_ts("pending_tool.jsonl")
        self.host([{"sessionId": self.OTHER, "title": "Engine", "at": round((t + 100) * 1000)}])
        self.child()
        self.child(sid=self.OTHER, cli="cli-engine", name="engine")
        self.transcript(fixture_lines("pending_tool.jsonl"))
        self.transcript(fixture_lines("idle_after_summary.jsonl"), cli="cli-engine")
        return t

    def test_held_note_and_quiet_session_in_one_exit(self):
        t = self.note_and_quiet_setup()
        code, out, waited = self.run_watch(t + 300)
        self.assertEqual((code, waited), (0, 0))
        self.assertEqual(out, self.PLAIN % ("engine", self.OTHER, sw.hhmmss((t + 100) * 1000))
                         + self.QUIET_TOOL % ("theme-kit", CHILD, "Bash", "default"))

    def test_items_of_one_exit_are_each_reported_once(self):
        t = self.note_and_quiet_setup()
        self.run_watch(t + 300)
        code, out, waited = self.run_watch(t + 400)
        self.assertEqual((out, waited), ("session-watch: cap: 30 min; theme-kit (waiting on Bash), engine (idle)\n",
                                         1800))

    def test_two_quiet_sessions_in_one_exit(self):
        t = newest_ts("pending_tool.jsonl")
        self.host([])
        self.child()
        self.child(sid=self.OTHER, cli="cli-engine", name="engine")
        self.transcript(fixture_lines("pending_tool.jsonl"))
        self.transcript(fixture_lines("pending_tool.jsonl"), cli="cli-engine")
        code, out, waited = self.run_watch(t + 300)
        self.assertEqual(out, self.QUIET_TOOL % ("theme-kit", CHILD, "Bash", "default")
                         + self.QUIET_TOOL % ("engine", self.OTHER, "Bash", "default"))

    # user-driven notes. turn_origins.jsonl holds four real turns of one session, one per turnOrigin
    # (the tool rounds are the real ones, text is neutral). A note's `at` here is the turn's
    # stop_hook_summary time + 20 ms; 32 real notes were 4-60 ms after it.
    TURN_ENDS = {"peer": "2026-11-09T23:07:48.194Z", "human": "2026-11-09T23:08:16.406Z",
                 "sdk": "2026-11-10T00:45:35.248Z", "task_notification": "2026-11-10T00:47:52.981Z"}

    def origin_note(self, origin, lines=None, after_ms=20):
        at = ms(self.TURN_ENDS[origin]) + after_ms
        self.host([{"sessionId": CHILD, "title": "Theme kit", "at": at}])
        self.child()
        self.transcript(fixture_lines("turn_origins.jsonl") if lines is None else lines)
        code, out, waited = self.run_watch(at / 1000 + 120)
        self.assertEqual((code, waited), (0, 0), out)
        return out

    def test_note_of_a_turn_the_user_drove_is_marked(self):
        self.assertEqual(self.origin_note("human"), self.USER % ("theme-kit", CHILD, "23:08:16Z"))

    def test_note_of_a_peer_turn_is_not_marked(self):
        self.assertEqual(self.origin_note("peer"), self.PLAIN % ("theme-kit", CHILD, "23:07:48Z"))

    def test_note_of_an_sdk_turn_is_not_marked(self):
        self.assertEqual(self.origin_note("sdk"), self.PLAIN % ("theme-kit", CHILD, "00:45:35Z"))

    def test_note_of_a_task_notification_turn_is_not_marked(self):
        self.assertEqual(self.origin_note("task_notification"), self.PLAIN % ("theme-kit", CHILD, "00:47:53Z"))

    def test_note_reads_the_turn_that_ended_at_its_time_not_the_newest(self):
        lines = fixture_lines("turn_origins.jsonl")[:13]  # the peer turn, then the user's turn
        self.assertEqual(self.origin_note("peer", lines), self.PLAIN % ("theme-kit", CHILD, "23:07:48Z"))

    def test_user_mark_holds_9_s_after_the_turn_end(self):
        self.assertEqual(self.origin_note("human", after_ms=9000), self.USER % ("theme-kit", CHILD, "23:08:25Z"))

    def test_no_user_mark_11_s_after_the_turn_end(self):
        self.assertEqual(self.origin_note("human", after_ms=11000), self.PLAIN % ("theme-kit", CHILD, "23:08:27Z"))

    def test_user_mark_without_a_stop_hook_summary(self):
        lines = [line for line in fixture_lines("turn_origins.jsonl") if '"stop_hook_summary"' not in line]
        self.assertEqual(len(lines), 19)
        self.assertEqual(self.origin_note("human", lines), self.USER % ("theme-kit", CHILD, "23:08:16Z"))

    def test_no_user_mark_when_the_turn_start_is_not_in_the_transcript(self):
        lines = fixture_lines("turn_origins.jsonl")[7:13]  # the user's turn without its prompt
        self.assertEqual(self.origin_note("human", lines), self.PLAIN % ("theme-kit", CHILD, "23:08:16Z"))

    def test_no_user_mark_when_no_turn_ended_at_the_note_time(self):
        # 20 ms after the first assistant record of the user's turn; the turn before ended 15 s earlier
        at = ms("2026-11-09T23:08:03.323Z") + 20
        self.host([{"sessionId": CHILD, "title": "Theme kit", "at": at}])
        self.child()
        self.transcript(fixture_lines("turn_origins.jsonl"))
        code, out, waited = self.run_watch(at / 1000 + 120)
        self.assertEqual(out, self.PLAIN % ("theme-kit", CHILD, "23:08:03Z"))

    def test_user_mark_past_a_record_without_a_timestamp(self):
        lines = fixture_lines("turn_origins.jsonl")
        lines.insert(13, json.dumps({"type": "system", "subtype": "informational", "isSidechain": False}))
        self.assertEqual(self.origin_note("human", lines), self.USER % ("theme-kit", CHILD, "23:08:16Z"))

    def test_note_of_a_session_without_a_transcript_yet_is_not_marked(self):
        self.host([{"sessionId": CHILD, "title": "Theme kit", "at": self.NOTE_AT}])
        self.child(created=self.NOTE_AT + 100000)
        code, out, waited = self.run_watch(self.NOTE_AT / 1000 + 120)
        self.assertEqual(out, self.PLAIN % ("theme-kit", CHILD, "23:33:08Z"))

    def test_note_of_a_session_that_is_no_longer_open_is_one_plain_line(self):
        self.host([{"sessionId": self.OTHER, "title": "Engine", "at": self.NOTE_AT}])
        code, out, waited = self.run_watch(self.NOTE_AT / 1000 + 120)
        self.assertEqual(out, self.PLAIN % ("Engine", self.OTHER, "23:33:08Z"))

    # quiet
    def quiet_setup(self, fixture="pending_tool.jsonl", lines=None, **child):
        self.host([])
        self.child(**child)
        return self.transcript(fixture_lines(fixture) if lines is None else lines), newest_ts(fixture)

    def test_shell_command_in_bypass_mode_is_never_quiet(self):
        for tool in ("Bash", "PowerShell"):
            with self.subTest(tool=tool):
                lines = [line.replace('"name": "Bash"', '"name": "%s"' % tool)
                         for line in fixture_lines("pending_tool.jsonl")]
                _, t = self.quiet_setup(lines=lines, permissionMode="bypassPermissions")
                shutil.rmtree(os.path.join(self.dir, "state"), ignore_errors=True)
                code, out, waited = self.run_watch(t + 290)
                self.assertEqual((code, out, waited),
                                 (0, "session-watch: cap: 30 min; theme-kit (waiting on %s)\n" % tool, 1800))

    def test_shell_command_next_to_a_question_in_bypass_mode_is_quiet(self):
        lines = fixture_lines("pending_tool.jsonl")
        second = lines[2].replace("toolu_0138EaZheB1BW9MXqoyqnpFi", "toolu_second").replace(
            '"name": "Bash"', '"name": "AskUserQuestion"')
        self.assertNotEqual(second, lines[2])
        _, t = self.quiet_setup(lines=lines[:3] + [second] + lines[3:], permissionMode="bypassPermissions")
        code, out, waited = self.run_watch(t + 300)
        self.assertEqual(out, self.QUIET_TOOL % ("theme-kit", CHILD, "Bash, AskUserQuestion", "bypassPermissions"))

    def test_no_record_in_bypass_mode_is_quiet(self):
        _, t = self.quiet_setup("prompt_working.jsonl", permissionMode="bypassPermissions")
        code, out, waited = self.run_watch(t + 300)
        self.assertEqual(out, "session-watch: quiet: theme-kit (%s) mid-turn with no transcript record for "
                              "5 min (mode bypassPermissions)\n" % CHILD)

    def test_quiet_pending_tool_at_300_s(self):
        _, t = self.quiet_setup()
        code, line, waited = self.run_watch(t + 290)
        self.assertEqual((code, waited), (0, 15))
        self.assertEqual(line, "session-watch: quiet: theme-kit (%s) mid-turn for 5 min, waiting on Bash "
                               "(mode default): possibly a permission prompt or a question in its window\n" % CHILD)

    def test_quiet_without_pending_tool(self):
        _, t = self.quiet_setup("prompt_working.jsonl")
        code, line, waited = self.run_watch(t + 300)
        self.assertEqual(waited, 0)
        self.assertEqual(line,"session-watch: quiet: theme-kit (%s) mid-turn with no transcript record for "
                               "5 min (mode default)\n" % CHILD)

    def test_subagent_write_delays_quiet(self):
        path, t = self.quiet_setup()
        sub = self.write(os.path.join(path[:-len(".jsonl")], "subagents", "agent-a1.jsonl"), "{}\n")
        os.utime(sub, (t + 295, t + 295))
        code, line, waited = self.run_watch(t + 290)
        self.assertTrue(line.startswith("session-watch: quiet: "), line)
        self.assertEqual(waited, 315)

    def test_quiet_is_reported_once(self):
        _, t = self.quiet_setup()
        self.run_watch(t + 400)
        code, line, waited = self.run_watch(t + 500)
        self.assertEqual((line, waited), ("session-watch: cap: 30 min; theme-kit (waiting on Bash)\n", 1800))

    def test_idle_child_is_never_quiet(self):
        _, t = self.quiet_setup("idle_after_summary.jsonl")
        code, line, waited = self.run_watch(t + 5000)
        self.assertEqual((code, line, waited), (0, "session-watch: cap: 30 min; theme-kit (idle)\n", 1800))

    # nothing to watch
    def test_no_child_is_nothing_to_watch(self):
        self.host([])
        code, line, waited = self.run_watch(10 ** 9)
        self.assertEqual((code, line, waited), (0, "session-watch: nothing to watch: no open side session of %s\n"
                                                % HOST, 0))

    def test_fork_and_archived_children_are_not_watched(self):
        self.host([])
        self.child(sid="local_fork", spawnedFrom={"sessionId": HOST, "userFork": True, "name": "fork"})
        self.child(sid="local_arch", isArchived=True)
        self.child(sid="local_other", spawnedFrom={"sessionId": "local_someone_else", "name": "x"})
        code, line, _ = self.run_watch(10 ** 9)
        self.assertTrue(line.startswith("session-watch: nothing to watch: "), line)

    # errors
    def assertError(self, result, waited=0, needle=""):
        code, line, w = result
        self.assertEqual(code, 2, line)
        self.assertTrue(line.startswith("session-watch: error: "), line)
        self.assertEqual(line.count("\n"), 1, line)
        self.assertIn(needle, line)
        self.assertEqual(w, waited)

    def test_error_host_id_unset(self):
        self.host([])
        self.assertError(self.run_watch(10 ** 9, env={}), needle="CLAUDE_CODE_HOST_SESSION_ID")

    def test_error_host_file_missing(self):
        self.child()
        self.assertError(self.run_watch(10 ** 9), needle=HOST)

    def test_error_host_file_not_an_object(self):
        self.write(os.path.join("app", "acct", "org", HOST + ".json"), "[]")
        self.assertError(self.run_watch(10 ** 9), needle="not a JSON object")

    def test_error_queue_not_a_list(self):
        self.host({"sessionId": CHILD})
        self.child()
        self.assertError(self.run_watch(10 ** 9), needle="queuedSideSessionNotes is not a list")

    def test_error_queue_a_number(self):
        self.host(5)
        self.child()
        self.assertError(self.run_watch(10 ** 9), needle="queuedSideSessionNotes is not a list")

    def test_error_note_at_is_a_string(self):
        self.host([{"sessionId": CHILD, "at": "1794267188444"}])
        self.child()
        self.assertError(self.run_watch(10 ** 9), needle="queuedSideSessionNotes")

    def test_error_note_without_session_id(self):
        self.host([{"at": 1794267188444}])
        self.child()
        self.assertError(self.run_watch(10 ** 9), needle="queuedSideSessionNotes")

    def test_error_unparsable_host_file_on_third_poll(self):
        self.write(os.path.join("app", "acct", "org", HOST + ".json"), '{"sessionId": ')
        self.assertError(self.run_watch(10 ** 9), waited=30, needle="cannot parse")

    def test_error_unparsable_child_file_on_third_poll(self):
        self.host([])
        self.child()
        self.transcript(fixture_lines("idle_after_summary.jsonl"))
        self.write(os.path.join("app", "acct", "org", "local_half.json"), '{"sessionId": "local_half", "spaw')
        self.assertError(self.run_watch(10 ** 10), waited=30, needle="local_half")

    def test_half_written_host_file_is_retried(self):
        self.write(os.path.join("app", "acct", "org", HOST + ".json"), '{"sessionId": ')
        code, line, waited = self.run_watch(10 ** 9, on_sleep=lambda: self.host([]))
        self.assertEqual((code, line, waited), (0, "session-watch: nothing to watch: no open side session of %s\n"
                                                % HOST, 15))

    def test_error_no_transcript_120_s_after_creation(self):
        self.host([])
        self.child(created=(10 ** 9 - 110) * 1000)
        self.assertError(self.run_watch(10 ** 9), waited=15, needle="no transcript")

    def test_error_unknown_state_for_60_s(self):
        self.host([])
        self.child()
        lines = [line.replace('"stop_hook_summary"', '"brand_new"') for line in fixture_lines("idle_after_summary.jsonl")]
        self.transcript(lines)
        self.assertError(self.run_watch(10 ** 10), waited=60, needle="system:brand_new")

    def test_error_corrupt_transcript_line(self):
        self.host([])
        self.child()
        self.transcript(fixture_lines("idle_after_summary.jsonl") + ["{not json"])
        self.assertError(self.run_watch(10 ** 10), needle=CLI)


class ProcessTests(Tmp):
    def run_script(self, env_extra):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_HOST_SESSION_ID"}
        env.update(env_extra)
        p = subprocess.run([sys.executable, "-I", SCRIPT, "--app-dir", os.path.join(self.dir, "app"),
                            "--state-dir", os.path.join(self.dir, "state")],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        return p.returncode, p.stdout.replace("\r\n", "\n"), p.stderr

    def test_process_error_line_and_exit_2(self):
        code, out, err = self.run_script({})
        self.assertEqual((code, err), (2, ""))
        self.assertRegex(out, r"\Asession-watch: error: CLAUDE_CODE_HOST_SESSION_ID is not set\n\Z")

    def test_process_nothing_to_watch(self):
        self.write(os.path.join("app", "a", "o", HOST + ".json"), json.dumps({"sessionId": HOST, "queuedSideSessionNotes": None}))
        code, out, err = self.run_script({"CLAUDE_CODE_HOST_SESSION_ID": HOST})
        self.assertEqual((code, out, err), (0, "session-watch: nothing to watch: no open side session of %s\n" % HOST, ""))


if __name__ == "__main__":
    unittest.main()
