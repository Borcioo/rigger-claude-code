"""The SubagentStop shadow: the script as the launcher starts it, over each event shape, and its summary mode.

python -I -X utf8 test_report_shadow.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOL_DIR, "report_shadow.py")

KILL_AFTER = 12
# A normal run ends in well under a second; a hung read is cut at the script's wait (patched to 0.5 s below).
HUNG_BOUND = 6.0
SID = "abc123def4567890"
AID = "def456abc7890123"
AGENT_TYPE = "rigger-core:worker"
FIELDS = ["agent8", "agent_type", "ms", "sid8", "source", "status", "ts", "verdict"]
TAIL = 256 * 1024

# Runs the script's main with `stub` applied first.
DRIVER = """
import os, sys, threading, time
sys.dont_write_bytecode = True
tool, stub = sys.argv[1:3]
sys.path.insert(0, tool)
import report_shadow
exec(stub)
report_shadow.main()
"""
HANG = """
report_shadow.WAIT = 0.5
report_shadow.classify = lambda text: time.sleep(3600)
threading.Thread(target=time.sleep, args=(3600,)).start()
"""


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def text_block(text):
    return {"type": "text", "text": text}


def tool_block():
    return {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}


class ShadowCase(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="report-shadow-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.data = os.path.join(self.base, "data")
        self.rows_dir = os.path.join(self.data, "report-gate", "rows")

    def event(self, message=None, **payload):
        event = {
            "session_id": SID, "transcript_path": "x", "cwd": self.base, "hook_event_name": "SubagentStop",
            "stop_hook_active": False, "agent_id": AID, "agent_type": AGENT_TYPE, "background_tasks": [],
        }
        if message is not None:
            event["last_assistant_message"] = message
        event.update(payload)
        return event

    def transcript(self, lines, name="agent.jsonl"):
        path = os.path.join(self.base, name)
        with open(path, "wb") as handle:
            for line in lines:
                handle.write((line if isinstance(line, str) else json.dumps(line)).encode("utf-8") + b"\n")
        return path

    def start(self, stdin, stub=None, data="default"):
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_DATA", None)
        if data is not None:
            env["CLAUDE_PLUGIN_DATA"] = self.data if data == "default" else data
        tail = [SCRIPT] if stub is None else ["-c", DRIVER, TOOL_DIR, stub]
        try:
            return subprocess.run([sys.executable, "-I", "-X", "utf8", "-S"] + tail, env=env, cwd=self.base,
                                  input=stdin, capture_output=True, timeout=KILL_AFTER)
        except subprocess.TimeoutExpired:
            self.fail("the hook was still running after %d s" % KILL_AFTER)

    def run_hook(self, event, stub=None, data="default"):
        """One SubagentStop. Silent and exit 0 whatever it found."""
        stdin = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
        proc = self.start(stdin, stub, data)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))

    def files(self):
        return sorted(os.listdir(self.rows_dir)) if os.path.isdir(self.rows_dir) else []

    def rows(self):
        found = []
        for name in self.files():
            with open(os.path.join(self.rows_dir, name), "rb") as handle:
                raw = handle.read()
            self.assertTrue(raw.endswith(b"\n") and raw.count(b"\n") == 1, "one JSON line per file")
            found.append(json.loads(raw))
        return found

    def row(self, event, **kwargs):
        self.run_hook(event, **kwargs)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def judged(self, event):
        row = self.row(event)
        return row["verdict"], row["status"], row["source"]


class Verdicts(ShadowCase):
    def test_each_status_word_is_ok_with_its_word(self):
        for word in ("DONE", "PARTIAL", "BLOCKED", "NEEDS_CONTEXT"):
            with self.subTest(word=word):
                shutil.rmtree(self.data, ignore_errors=True)
                self.assertEqual(self.judged(self.event("STATUS: %s\n\nbody" % word)), ("ok", word, "event"))

    def test_crlf_blank_lead_and_trailing_blanks_are_ok(self):
        for message in ("STATUS: DONE\r\nbody", "\r\n  \r\nSTATUS: DONE  \r\nbody", "STATUS: DONE"):
            with self.subTest(message=message):
                shutil.rmtree(self.data, ignore_errors=True)
                self.assertEqual(self.judged(self.event(message)), ("ok", "DONE", "event"))

    def test_a_status_line_with_another_word_or_trailing_text_is_bad_value(self):
        for message in ("STATUS: FINISHED", "STATUS: DONE (see below)", "STATUS:DONE", "STATUS: done", "STATUS: ",
                        "STATUS: DONE | PARTIAL"):
            with self.subTest(message=message):
                shutil.rmtree(self.data, ignore_errors=True)
                self.assertEqual(self.judged(self.event(message + "\nbody")), ("bad-value", "", "event"))

    def test_anything_else_is_missing(self):
        for message in ("", "   \n\n", "Here is the report.\nSTATUS: DONE", " STATUS: DONE", "status: DONE",
                        "Result: done", "Note: x\nSTATUS: DONE"):
            with self.subTest(message=message):
                shutil.rmtree(self.data, ignore_errors=True)
                self.assertEqual(self.judged(self.event(message)), ("missing", "", "event"))


class Sources(ShadowCase):
    def test_the_event_message_wins_over_the_transcript(self):
        path = self.transcript([assistant(text_block("no status here"))])
        event = self.event("STATUS: PARTIAL\nx", agent_transcript_path=path)
        self.assertEqual(self.judged(event), ("ok", "PARTIAL", "event"))

    def test_without_a_message_the_last_assistant_text_of_the_transcript_is_read(self):
        path = self.transcript([
            assistant(text_block("STATUS: BLOCKED\nold")),
            {"type": "user", "message": {"role": "user", "content": "STATUS: DONE"}},
            assistant(text_block("working"), tool_block()),
            assistant(text_block("STATUS: NEEDS_CONTEXT\nfinal"), tool_block()),
            assistant(tool_block()),
            {"type": "user", "message": {"role": "user", "content": [text_block("STATUS: DONE")]}},
        ])
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("ok", "NEEDS_CONTEXT", "transcript"))

    def test_a_malformed_block_does_not_hide_a_good_one_in_the_same_message(self):
        path = self.transcript([assistant(7, None, text_block("STATUS: BLOCKED"))])
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("ok", "BLOCKED", "transcript"))

    def test_the_last_text_block_of_a_message_counts(self):
        path = self.transcript([assistant(text_block("STATUS: DONE"), text_block("plain closing"))])
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("missing", "", "transcript"))

    def test_a_report_at_the_end_of_a_large_transcript_is_found(self):
        filler = [{"type": "user", "message": {"role": "user", "content": "x" * 1000}}] * 600
        path = self.transcript(filler + [assistant(text_block("STATUS: DONE"))])
        self.assertGreater(os.path.getsize(path), 2 * TAIL)
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("ok", "DONE", "transcript"))

    def test_the_transcript_read_stops_at_the_tail(self):
        filler = [{"type": "user", "message": {"role": "user", "content": "x" * 1000}}] * 400
        path = self.transcript([assistant(text_block("STATUS: DONE"))] + filler)
        self.assertGreater(os.path.getsize(path), TAIL + 10000)
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("unread", "", "none"))

    def test_a_non_string_message_falls_back_to_the_transcript(self):
        path = self.transcript([assistant(text_block("STATUS: DONE"))])
        for message in (7, ["STATUS: DONE"], {"a": 1}, None):
            with self.subTest(message=message):
                shutil.rmtree(self.data, ignore_errors=True)
                event = self.event(last_assistant_message=message, agent_transcript_path=path)
                self.assertEqual(self.judged(event), ("ok", "DONE", "transcript"))

    def test_non_utf8_bytes_in_the_transcript_do_not_hide_the_report(self):
        path = os.path.join(self.base, "agent.jsonl")
        line = json.dumps(assistant(text_block("STATUS: DONE\nok"))).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe broken\n" + line + b"\n" + b"\x80\x81\n")
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("ok", "DONE", "transcript"))


class Unread(ShadowCase):
    def test_no_message_and_no_readable_transcript_is_unread(self):
        folder = os.path.join(self.base, "adir")
        os.mkdir(folder)
        empty = self.transcript([], "empty.jsonl")
        no_text = self.transcript([assistant(tool_block()), {"type": "user", "message": {"content": "hi"}}], "t.jsonl")
        cases = {
            "no path": {},
            "missing file": {"agent_transcript_path": os.path.join(self.base, "gone.jsonl")},
            "a directory": {"agent_transcript_path": folder},
            "empty file": {"agent_transcript_path": empty},
            "no assistant text": {"agent_transcript_path": no_text},
            "number path": {"agent_transcript_path": 5},
            "list path": {"agent_transcript_path": ["a"]},
            "empty path": {"agent_transcript_path": ""},
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                shutil.rmtree(self.data, ignore_errors=True)
                self.assertEqual(self.judged(self.event(**payload)), ("unread", "", "none"))

    def test_a_transcript_line_of_the_wrong_shape_is_skipped(self):
        path = self.transcript([
            assistant(text_block("STATUS: DONE")),
            [1, 2],
            7,
            "text",
            {"type": "assistant", "message": []},
            {"type": "assistant", "message": {"content": "plain"}},
            {"type": "assistant", "message": {"content": [7, None, {"type": "text", "text": 5}]}},
            {"type": "assistant"},
        ])
        self.assertEqual(self.judged(self.event(agent_transcript_path=path)), ("ok", "DONE", "transcript"))

    def test_a_path_the_system_rejects_outright_is_an_error_row(self):
        self.assertEqual(self.judged(self.event(agent_transcript_path="a\x00b")), ("error", "", "none"))


class EventShapes(ShadowCase):
    def test_an_event_that_is_not_an_object_is_an_error_row(self):
        for label, raw in {"list": b"[1, 2]", "number": b"7", "string": b'"STATUS: DONE"', "null": b"null",
                           "bad json": b"{not json", "empty": b"", "binary": b"\xff\xfe\x00"}.items():
            with self.subTest(shape=label):
                shutil.rmtree(self.data, ignore_errors=True)
                row = self.row(raw)
                self.assertEqual((row["verdict"], row["status"], row["source"]), ("error", "", "none"))
                self.assertEqual((row["sid8"], row["agent8"], row["agent_type"]), ("", "", ""))

    def test_non_utf8_bytes_in_the_event_are_replaced_not_fatal(self):
        raw = b'{"session_id": "s1", "agent_id": "a1", "last_assistant_message": "STATUS: DONE\\n\xff\xfe"}'
        row = self.row(raw)
        self.assertEqual((row["verdict"], row["status"], row["source"]), ("ok", "DONE", "event"))

    def test_ids_and_type_of_the_wrong_shape_read_as_absent(self):
        for value in (7, ["x"], {"a": 1}, None, True):
            with self.subTest(value=value):
                shutil.rmtree(self.data, ignore_errors=True)
                row = self.row(self.event("STATUS: DONE", session_id=value, agent_id=value, agent_type=value))
                self.assertEqual((row["sid8"], row["agent8"], row["agent_type"]), ("", "", ""))
                self.assertEqual(row["verdict"], "ok")
        self.assertRegex(self.files()[0], r"^noid-\d+\.json$")


class RowShape(ShadowCase):
    def test_the_row_holds_exactly_the_documented_fields_and_no_report_text(self):
        marker = "UNIQUE-REPORT-TEXT-9f3a"
        self.run_hook(self.event("STATUS: DONE\n" + marker))
        (name,) = self.files()
        with open(os.path.join(self.rows_dir, name), "rb") as handle:
            raw = handle.read()
        self.assertNotIn(marker.encode(), raw)
        row = json.loads(raw)
        self.assertEqual(sorted(row), FIELDS)
        self.assertEqual((row["sid8"], row["agent8"], row["agent_type"]), (SID[:8], AID[:8], AGENT_TYPE))
        self.assertIs(type(row["ms"]), int)
        self.assertGreaterEqual(row["ms"], 0)

    def test_the_timestamp_is_utc_in_the_documented_format(self):
        stub = "time.gmtime = lambda *a: time.struct_time((2026, 11, 4, 12, 30, 5, 2, 308, 0))"
        row = self.row(self.event("STATUS: DONE"), stub=stub)
        self.assertEqual(row["ts"], "2026-11-04T12:30:05Z")

    def test_the_agent_type_is_capped_at_60_characters(self):
        row = self.row(self.event("STATUS: DONE", agent_type="a" * 61 + "b"))
        self.assertEqual(row["agent_type"], "a" * 60)

    def test_a_non_ascii_agent_type_is_kept_as_given(self):
        row = self.row(self.event("STATUS: DONE", agent_type="agent-schildkröte"))
        self.assertEqual(row["agent_type"], "agent-schildkröte")

    def test_an_absent_agent_type_is_an_empty_string(self):
        event = self.event("STATUS: DONE")
        del event["agent_type"]
        self.assertEqual(self.row(event)["agent_type"], "")


class Files(ShadowCase):
    def test_an_unset_or_empty_plugin_data_dir_writes_nothing(self):
        for data in (None, ""):
            with self.subTest(data=data):
                self.run_hook(self.event("STATUS: DONE"), data=data)
                self.assertEqual(os.listdir(self.base), [])

    def test_the_same_session_and_agent_leave_one_row_and_another_agent_a_second(self):
        self.run_hook(self.event("STATUS: DONE"))
        self.run_hook(self.event("STATUS: BLOCKED"))
        self.assertEqual([row["status"] for row in self.rows()], ["DONE"])
        self.run_hook(self.event("STATUS: PARTIAL", agent_id="other-agent-id"))
        self.assertEqual(sorted(row["status"] for row in self.rows()), ["DONE", "PARTIAL"])

    def test_another_session_with_the_same_agent_id_is_its_own_row(self):
        self.run_hook(self.event("STATUS: DONE"))
        self.run_hook(self.event("STATUS: DONE", session_id="zzz-another-session"))
        self.assertEqual(len(self.files()), 2)

    def test_the_row_cap_stops_new_rows(self):
        os.makedirs(self.rows_dir)
        for number in range(3):
            with open(os.path.join(self.rows_dir, "old%d.json" % number), "wb") as handle:
                handle.write(b"{}\n")
        self.run_hook(self.event("STATUS: DONE"), stub="report_shadow.ROW_CAP = 3")
        self.assertEqual(len(self.files()), 3)
        self.run_hook(self.event("STATUS: DONE"), stub="report_shadow.ROW_CAP = 4")
        self.assertEqual(len(self.files()), 4)

    def test_a_hung_read_ends_at_the_wait_with_no_row_and_exit_zero(self):
        started = time.monotonic()
        self.run_hook(self.event("STATUS: DONE"), stub=HANG)
        self.assertLess(time.monotonic() - started, HUNG_BOUND)
        self.assertEqual(self.files(), [])


class Summary(ShadowCase):
    def put(self, name, content):
        os.makedirs(self.rows_dir, exist_ok=True)
        if not isinstance(content, bytes):
            content = (content if isinstance(content, str) else json.dumps(content)).encode("utf-8") + b"\n"
        with open(os.path.join(self.rows_dir, name), "wb") as handle:
            handle.write(content)

    def good(self, name, agent_type, verdict):
        self.put(name, {"ts": "2026-11-04T12:00:00Z", "sid8": "s", "agent8": "a", "agent_type": agent_type,
                        "verdict": verdict, "status": "", "source": "event", "ms": 1})

    def summary(self):
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-S", SCRIPT, "--summary", "--data", self.data],
                              cwd=self.base, stdin=subprocess.DEVNULL, capture_output=True, timeout=KILL_AFTER)
        self.assertEqual(proc.stderr, b"")
        return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n")

    def test_one_line_per_agent_type_then_the_row_count(self):
        for number, (kind, verdict) in enumerate([
            ("worker", "ok"), ("worker", "ok"), ("worker", "missing"), ("reviewer", "bad-value"),
            ("reviewer", "unread"), ("worker", "error"), ("worker", "unread"), ("reviewer", "ok"),
        ]):
            self.good("r%d.json" % number, kind, verdict)
        self.assertEqual(self.summary(), (0, (
            "reviewer ok 1 missing 0 bad-value 1 unread 1 error 0\n"
            "worker ok 2 missing 1 bad-value 0 unread 1 error 1\n"
            "# rows 8\n")))

    def test_an_empty_agent_type_is_listed_under_a_dash(self):
        self.good("r1.json", "", "missing")
        self.assertEqual(self.summary(), (0, "- ok 0 missing 1 bad-value 0 unread 0 error 0\n# rows 1\n"))

    def test_zero_rows_print_the_count_and_exit_one(self):
        self.assertEqual(self.summary(), (1, "# rows 0\n"))
        os.makedirs(self.rows_dir)
        self.assertEqual(self.summary(), (1, "# rows 0\n"))

    def test_malformed_row_files_are_counted_not_skipped(self):
        self.good("ok.json", "worker", "ok")
        self.put("list.json", [1, 2])
        self.put("number.json", "7")
        self.put("string.json", '"ok"')
        self.put("badjson.json", "{nope")
        self.put("empty.json", b"")
        self.put("binary.json", b"\xff\xfe\x00{\n")
        self.put("verdict-number.json", {"agent_type": "w", "verdict": 3})
        self.put("verdict-unknown.json", {"agent_type": "w", "verdict": "fine"})
        self.put("verdict-list.json", {"agent_type": "w", "verdict": ["ok"]})
        self.put("type-number.json", {"agent_type": 5, "verdict": "ok"})
        self.put("type-missing.json", {"verdict": "ok"})
        os.mkdir(os.path.join(self.rows_dir, "dir.json"))
        self.assertEqual(self.summary(), (0, "worker ok 1 missing 0 bad-value 0 unread 0 error 0\n# rows 1\n"
                                             "# unreadable 12\n"))

    def test_only_unreadable_files_still_exit_one(self):
        self.put("bad.json", "{nope")
        self.assertEqual(self.summary(), (1, "# rows 0\n# unreadable 1\n"))

    def test_files_that_are_not_row_files_are_ignored(self):
        self.good("ok.json", "worker", "ok")
        self.put("note.txt", "hello")
        self.assertEqual(self.summary(), (0, "worker ok 1 missing 0 bad-value 0 unread 0 error 0\n# rows 1\n"))

    def test_a_missing_data_argument_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-S", SCRIPT, "--summary"], cwd=self.base,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=KILL_AFTER)
        self.assertEqual((proc.returncode, proc.stdout), (2, b""))
        self.assertIn(b"--data", proc.stderr)

    def test_the_summary_reads_the_rows_a_hook_run_wrote(self):
        self.run_hook(self.event("STATUS: DONE"))
        self.run_hook(self.event("no status", agent_id="second"))
        self.assertEqual(self.summary(), (0, AGENT_TYPE + " ok 1 missing 1 bad-value 0 unread 0 error 0\n# rows 2\n"))


if __name__ == "__main__":
    unittest.main()
