"""Tests for turn_guard.py (D89), in process with a fake clock and, for the wiring, end to end through
side_session_guard.py as the Stop hook runs it.

python -I test_turn_guard.py
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOL, "side_session_guard.py")
sys.path.insert(0, TOOL)

import turn_guard  # noqa: E402

SID = "5e1d0a8c-1f2b-4c3d-9e4f-a0b1c2d3e4f5"
GATE = "python -I scripts/gate.py --changed-modules"
NOTE_TEXT = ('<system-reminder>\nThe session "worker" you started (local_0aa11111-2222-4333-8444-555566667777) '
             'finished a turn. It reported: "done".\n</system-reminder>')


class Clock:
    """A clock that moves only when the code sleeps; `at` runs a callback once the time is reached."""

    def __init__(self, at=None, action=None):
        self.now, self.at, self.action = 0.0, at, action

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.action is not None and self.now >= self.at:
            self.action()
            self.action = None


@contextlib.contextmanager
def uid_as(uid):
    """os.getuid returns `uid`, or is absent when `uid` is None (as on Windows); restored on exit."""
    had, old = hasattr(os, "getuid"), getattr(os, "getuid", None)
    if uid is None:
        if had:
            del os.getuid
    else:
        os.getuid = lambda: uid
    try:
        yield
    finally:
        if had:
            os.getuid = old
        elif hasattr(os, "getuid"):
            del os.getuid


def task(task_id="b58ki4brf", command=GATE, description="gate run", kind="shell", status="running"):
    return {"id": task_id, "type": kind, "status": status, "description": description, "command": command}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="turn-guard-")
        self.config = os.path.join(self.root, "config")
        self.state_dir = os.path.join(self.config, "state")
        os.makedirs(self.state_dir)
        self.state = os.path.join(self.state_dir, SID + ".md")
        self.counts = os.path.join(self.state_dir, SID + ".turn-guard.json")
        self.temp = os.path.join(self.root, "tmp")
        self.tasks = os.path.join(self.temp, "claude", "628553b7d94a7772", SID, "tasks")
        os.makedirs(self.tasks)
        self.project = os.path.join(self.root, "project")
        os.makedirs(self.project)
        self.transcript = os.path.join(self.root, "t.jsonl")
        self.write(self.transcript, '{"type":"user","message":{"role":"user","content":"hi"}}\n')
        self.environ = {"TEMP": self.temp, "TMP": self.temp, "TMPDIR": self.temp,
                        "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "CLAUDE_PROJECT_DIR": self.project}

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return path

    def output(self, task_id="b58ki4brf", text=""):
        return self.write(os.path.join(self.tasks, task_id + ".output"), text)

    def event(self, tasks, **extra):
        event = {"hook_event_name": "Stop", "session_id": SID, "transcript_path": self.transcript,
                 "cwd": self.project, "stop_hook_active": False, "background_tasks": tasks, "session_crons": [],
                 "prompt_id": "prompt-1"}
        event.update(extra)
        return event

    def check(self, tasks, coordinator=False, environ=None, clock=None, **extra):
        clock = clock or Clock()
        return turn_guard.check(self.event(tasks, **extra), self.state, coordinator,
                                dict(self.environ, **(environ or {})), self.config, clock=clock, sleep=clock.sleep)

    def assertHeld(self, result, task_id="b58ki4brf"):
        reason, _ = result
        self.assertIsNotNone(reason, "expected a block")
        self.assertTrue(reason.startswith(turn_guard.PREFIX + " "), reason)
        self.assertIn(task_id, reason)

    def assertAllowed(self, result):
        self.assertIsNone(result[0], "expected no block")


class FilterTests(Base):
    def test_running_gate_task_blocks_with_its_id_and_description(self):
        self.output()
        reason, note = self.check([task(description="phase gate")])
        self.assertHeld((reason, note))
        self.assertIn('"phase gate"', reason)
        self.assertIsNone(note)

    def test_any_running_shell_command_is_held(self):
        for command in (GATE, "npm run dev", "sleep 30", "python delegate.py", "", "cat jest.config.js"):
            with self.subTest(command=command):
                self.output()
                self.assertHeld(self.check([task(command=command)]))
                os.remove(self.counts)
        self.assertAllowed(self.check([task(status="completed")]))

    def test_monitor_and_unknown_types_are_not_held(self):
        """A Monitor is a watcher that outlives turns by design (events, expiry, re-arm): never held."""
        self.output()
        for kind in ("monitor", "teammate", None):
            with self.subTest(kind=kind):
                self.assertAllowed(self.check([task(kind=kind, command="sleep 15; echo ready")]))
        self.output("bq2")
        self.assertHeld(self.check([task(kind="monitor"), task("bq2")]), "bq2")

    def test_running_subagent_blocks_without_a_poll(self):
        """A subagent's output file never gets a trailer, so it is held at once, file or not."""
        clock = Clock()
        reason, note = self.check([task("a21a4cf9bbd611542", kind="subagent", description="bg review", command=None)],
                                  clock=clock)
        self.assertHeld((reason, note), "a21a4cf9bbd611542")
        self.assertEqual(clock.now, 0.0)
        self.assertIn('"bg review"', reason)
        self.assertIn("completion notice", reason)
        self.assertAllowed(self.check([task("a21a4cf9bbd611542", kind="subagent", status="completed")]))

    def test_a_subagent_skips_the_poll_of_a_shell_task_too(self):
        self.output()
        clock = Clock()
        self.assertHeld(self.check([task(), task("a21a4cf9bbd611542", kind="subagent")], clock=clock))
        self.assertLess(clock.now, turn_guard.POLL_STEP + 0.01)

    def test_coordinator_session_is_not_held(self):
        self.output()
        self.assertAllowed(self.check([task()], coordinator=True))
        self.assertHeld(self.check([task()], coordinator=False))

    def test_print_mode_is_not_held(self):
        self.output()
        self.assertAllowed(self.check([task()], environ={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}))
        self.assertHeld(self.check([task()], environ={"CLAUDE_CODE_ENTRYPOINT": "cli"}))

    def test_no_marker_holds_every_session(self):
        """Task 1 found no side-session marker: a session without any desktop variable is held too."""
        self.output()
        environ = {key: value for key, value in self.environ.items() if key != "CLAUDE_CODE_ENTRYPOINT"}
        clock = Clock()
        result = turn_guard.check(self.event([task()]), self.state, False, environ, self.config,
                                  clock=clock, sleep=clock.sleep)
        self.assertHeld(result)

    def test_missing_field_or_file_allows(self):
        for tasks in (None, "x", {"id": "b58ki4brf"}, ["not a task"], [{"id": "b58ki4brf", "type": "shell"}],
                      [dict(task(), command=None)], [dict(task(), id=None)], [dict(task(), id="../x")]):
            with self.subTest(tasks=tasks):
                self.output()
                self.assertAllowed(self.check(tasks))
        event = self.event([task()])
        del event["background_tasks"]
        self.assertAllowed(turn_guard.check(event, self.state, False, self.environ, self.config))
        os.remove(os.path.join(self.tasks, "b58ki4brf.output"))
        self.assertAllowed(self.check([task()]))
        self.output()
        self.assertHeld(self.check([task()]))

    def test_output_file_is_found_through_the_notes_folder(self):
        notes_root = os.path.join(self.root, "short", "cc8538a5", SID)
        self.write(os.path.join(notes_root, "tasks", "b58ki4brf.output"), "")
        environ = {"TEMP": os.path.join(self.root, "none"), "TMP": os.path.join(self.root, "none"),
                   "TMPDIR": os.path.join(self.root, "none"), "CLAUDE_CODE_CHILD_NOTES_DIR": os.path.join(notes_root, "started")}
        self.assertHeld(self.check([task()], environ=environ))
        environ["CLAUDE_CODE_CHILD_NOTES_DIR"] = os.path.join(self.root, "elsewhere", SID, "started")
        self.assertAllowed(self.check([task()], environ=environ))
        other = os.path.join(self.root, "short", "cc8538a5", "other-session")
        self.write(os.path.join(other, "tasks", "b58ki4brf.output"), "")
        environ["CLAUDE_CODE_CHILD_NOTES_DIR"] = os.path.join(other, "started")
        self.assertAllowed(self.check([task()], environ=environ))

    def test_output_file_is_found_in_each_cli_temp_layout(self):
        # The CLI's task root is `<CLAUDE_CODE_TMPDIR, else os.tmpdir()>/claude-<uid>` where the platform has a
        # uid (Linux, macOS), `claude-` where it has none, and `claude` on Windows.
        none = os.path.join(self.root, "none")
        shutil.rmtree(self.temp)
        own_tmp = os.path.join(self.root, "own-tmp")
        rows = (("POSIX claude-<uid>", self.temp, "claude-1000", 1000, {}),
                ("no uid: claude-", self.temp, "claude-", None, {}),
                ("CLAUDE_CODE_TMPDIR root", own_tmp, "claude", None,
                 {"TEMP": none, "TMP": none, "TMPDIR": none, "CLAUDE_CODE_TMPDIR": own_tmp}))
        for name, root, folder, uid, environ in rows:
            with self.subTest(layout=name):
                tasks = os.path.join(root, folder, "-tmp-some-cwd", SID, "tasks")
                path = self.write(os.path.join(tasks, "b58ki4brf.output"), "")
                with uid_as(uid):
                    self.assertEqual(turn_guard.output_file(SID, "b58ki4brf", dict(self.environ, **environ)), path)
                    self.assertHeld(self.check([task()], environ=environ))
                shutil.rmtree(os.path.join(root, folder))
                os.remove(self.counts)   # each layout starts below the cap
        with uid_as(1000):
            self.write(os.path.join(self.temp, "claude-1001", "k", SID, "tasks", "b58ki4brf.output"), "")
            self.assertAllowed(self.check([task()]))


class PollTests(Base):
    def test_task_that_ends_during_the_poll_allows(self):
        path = self.output(text="early\n")
        clock = Clock(at=1.0, action=lambda: self.write(path, "early\nlate\n\n[exited with code 0]\n"))
        self.assertAllowed(self.check([task()], clock=clock))
        self.assertLess(clock.now, 1.5)
        self.output(text="early\n")
        self.assertHeld(self.check([task()]))

    def test_killed_trailer_allows(self):
        for text in ("\n[killed]\n", "partial\r\n\r\n[exited with code 3]\r\n", "x\n\n[exited with code -1]\n"):
            with self.subTest(text=text):
                self.output(text=text)
                self.assertAllowed(self.check([task()]))
        for text in ("partial\n", "[exited with code 0] was printed by the run\nmore\n", ""):
            with self.subTest(text=text):
                self.output(text=text)
                self.assertHeld(self.check([task()]))
                os.remove(self.counts)

    def test_poll_is_bounded(self):
        path = self.output()
        clock = Clock()
        self.assertEqual(turn_guard.poll({"b58ki4brf": path}, clock=clock, sleep=clock.sleep), {"b58ki4brf"})
        self.assertGreaterEqual(clock.now, turn_guard.POLL_BOUND)
        self.assertLessEqual(clock.now, turn_guard.POLL_BOUND + turn_guard.POLL_STEP)
        self.assertEqual(turn_guard.POLL_BOUND, 4.0)
        started = time.monotonic()
        reason, _ = turn_guard.check(self.event([task()]), self.state, False, self.environ, self.config)
        elapsed = time.monotonic() - started
        self.assertIsNotNone(reason)
        self.assertGreaterEqual(elapsed, 3.9)
        self.assertLess(elapsed, 5.0)


class CapTests(Base):
    def test_block_names_the_task_and_stops_after_two(self):
        self.output()
        self.output("bq2")
        for count, (active, prompt) in enumerate(((False, "prompt-1"), (False, "prompt-2")), 1):
            reason, note = self.check([task()], stop_hook_active=active, prompt_id=prompt)
            self.assertHeld((reason, note))
            self.assertIn("%d of 2" % count, reason)
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, SID + ".side-guard.json")))
        with open(self.counts, "rb") as fh:
            self.assertEqual(json.loads(fh.read())["blocks"], {"b58ki4brf": 2})
        reason, note = self.check([task()], stop_hook_active=False, prompt_id="prompt-3")
        self.assertIsNone(reason)
        self.assertTrue(note.startswith(turn_guard.PREFIX + " "), note)
        self.assertIn("b58ki4brf", note)
        self.assertNotIn("\n", note)
        self.assertEqual(self.check([task()], prompt_id="prompt-4"), (None, None))
        self.assertHeld(self.check([task(), task("bq2")], prompt_id="prompt-5"), "bq2")

    def test_a_turn_holds_at_most_twice_across_its_tasks(self):
        self.output()
        self.output("bq2")
        self.assertHeld(self.check([task(), task("bq2")]))
        self.assertHeld(self.check([task("bq2")], stop_hook_active=True), "bq2")   # the first one ended
        reason, note = self.check([task("bq2")], stop_hook_active=True)
        self.assertIsNone(reason)
        self.assertTrue(note.startswith(turn_guard.PREFIX + " ") and "2 holds this turn" in note, note)
        self.assertIn("bq2", note)
        self.assertNotIn("\n", note)
        with open(self.counts, "rb") as fh:
            self.assertEqual(json.loads(fh.read())["blocks"], {"b58ki4brf": 1, "bq2": 1})
        self.assertHeld(self.check([task("bq2")], prompt_id="prompt-2"), "bq2")

    def test_without_a_prompt_id_a_stop_chain_holds_once(self):
        self.output()
        event_kw = {"prompt_id": None}
        self.assertHeld(self.check([task()], stop_hook_active=False, **event_kw))
        self.assertAllowed(self.check([task()], stop_hook_active=True, **event_kw))
        self.assertHeld(self.check([task()], stop_hook_active=False, **event_kw))

    def test_a_capped_run_keeps_its_note_when_another_run_blocks(self):
        """Run A reaches its cap in the same Stop where run B holds: A's one note comes with B's block."""
        self.output()
        self.output("bq2")
        self.write(self.counts, json.dumps({"format": 1, "blocks": {"b58ki4brf": 2}}))
        reason, note = self.check([task(), task("bq2")], stop_hook_active=True)
        self.assertHeld((reason, note), "bq2")
        self.assertIsNotNone(note, "expected run A's note with the block")
        self.assertTrue(note.startswith(turn_guard.PREFIX + " ") and "b58ki4brf" in note, note)
        self.assertIn("still runs after 2 holds", note)
        with open(self.counts, "rb") as fh:
            self.assertEqual(json.loads(fh.read())["blocks"], {"b58ki4brf": 3, "bq2": 1})

    def test_a_config_without_a_state_folder_holds_and_counts(self):
        self.output()
        shutil.rmtree(self.state_dir)
        for count, active in ((1, False), (2, True)):
            reason, note = self.check([task()], stop_hook_active=active)
            self.assertHeld((reason, note))
            self.assertIn("%d of 2" % count, reason)
        with open(self.counts, "rb") as fh:
            self.assertEqual(json.loads(fh.read())["blocks"], {"b58ki4brf": 2})

    def test_count_that_cannot_be_written_holds_once_per_stop_chain(self):
        def count_is_a_folder():
            os.makedirs(self.counts)
            return lambda: os.rmdir(self.counts)

        def state_folder_is_a_file():
            shutil.rmtree(self.state_dir)
            self.write(self.state_dir, "")
            return lambda: os.remove(self.state_dir) or os.makedirs(self.state_dir)

        self.output()
        for name, unwritable in (("count path is a folder", count_is_a_folder),
                                 ("state folder is a file", state_folder_is_a_file)):
            with self.subTest(case=name):
                undo = unwritable()
                reason, note = self.check([task()], stop_hook_active=False)
                self.assertHeld((reason, note))
                self.assertIsNone(note)
                for active in (False, None, "true", 1):
                    with self.subTest(stop_hook_active=active):
                        self.assertHeld(self.check([task()], stop_hook_active=active))
                event = self.event([task()])
                del event["stop_hook_active"]
                clock = Clock()
                self.assertHeld(turn_guard.check(event, self.state, False, self.environ, self.config,
                                                 clock=clock, sleep=clock.sleep))
                reason, note = self.check([task()], stop_hook_active=True)
                self.assertIsNone(reason)
                self.assertIsNotNone(note, "expected the one-line note")
                self.assertTrue(note.startswith(turn_guard.PREFIX + " "), note)
                self.assertIn("block count cannot be written (%s)" % self.counts, note)
                self.assertIn("the turn ends without holding again", note)
                self.assertNotIn("\n", note)
                undo()
                self.assertHeld(self.check([task()], stop_hook_active=True))
                os.remove(self.counts)

    def test_block_reason_is_one_line_naming_the_run(self):
        self.output()
        reason, _ = self.check([task(description="line one\nline two\r\n" + "x" * 200)])
        self.assertHeld((reason, None))
        self.assertNotIn("\n", reason)
        self.assertNotIn("\r", reason)
        self.assertIn('"line one line two ', reason)
        self.assertNotIn("x" * 61, reason)
        self.assertIn("wait", reason)

    def test_shell_reason_names_its_output_file_and_how_to_end_the_turn(self):
        path = self.output()
        reason, _ = self.check([task()])
        self.assertIn(path, reason)
        self.assertIn("[exited with code N]", reason)
        self.assertIn("bounded foreground wait", reason)
        self.assertIn("only with a result or a question", reason)
        self.assertIn("server", reason)


class LogTests(Base):
    def test_an_error_inside_the_check_allows_and_writes_one_stderr_line(self):
        import io
        import side_session_guard
        sys.path.insert(0, os.path.join(os.path.dirname(TOOL), "context-guard"))
        import context_guard
        stderr = io.StringIO()
        with mock.patch.object(turn_guard, "running_tasks", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stderr", stderr):
            self.assertEqual(side_session_guard.turn_end(self.event([task()]), self.state, context_guard), (None, None))
        self.assertEqual(stderr.getvalue().count("\n"), 1)
        self.assertTrue(stderr.getvalue().startswith(turn_guard.PREFIX + " ") and "boom" in stderr.getvalue())


class ConfigTests(Base):
    def level_file(self, level, value):
        body = json.dumps({"schema": 1, "turn_guard": {"enabled": value}})
        if level == 3:
            return self.write(os.path.join(self.config, "rigger", "config.json"), body)
        if level == 4:
            return self.write(os.path.join(self.project, ".claude", "rigger.json"), body)
        module = os.path.join(self.root, "company")
        self.write(os.path.join(module, ".claude-plugin", "plugin.json"), json.dumps({"name": "acme-rules"}))
        self.write(os.path.join(module, "rigger-module.json"), json.dumps({
            "schema": 1, "name": "acme-rules", "layer": "company", "config": {"turn_guard": {"enabled": value}}}))
        return module

    def test_config_turns_the_guard_off(self):
        self.output()
        for level in (2, 3, 4):
            with self.subTest(level=level):
                found = self.level_file(level, False)
                environ = {"RIGGER_MODULE_DIRS": found} if level == 2 else {}
                self.assertAllowed(self.check([task()], environ=environ))
                self.level_file(level, True)
                self.assertHeld(self.check([task()], environ=environ))
                self.level_file(level, "no")
                self.assertHeld(self.check([task()], environ=environ))
                shutil.rmtree(self.root + os.sep + "company", ignore_errors=True)
                for path in (os.path.join(self.config, "rigger"), os.path.join(self.project, ".claude")):
                    shutil.rmtree(path, ignore_errors=True)
                os.remove(self.counts)

    def test_a_config_that_does_not_load_keeps_the_guard_on(self):
        self.output()
        self.level_file(3, False)
        self.assertAllowed(self.check([task()]))
        sys.path.insert(0, turn_guard.ASSEMBLE)
        import discover
        with mock.patch.object(discover, "discover", side_effect=RuntimeError("broken install")):
            self.assertHeld(self.check([task()]))

    def test_project_level_comes_from_the_project_dir_not_the_cwd(self):
        self.output()
        self.level_file(4, False)
        elsewhere = os.path.join(self.root, "elsewhere")
        os.makedirs(elsewhere)
        self.assertAllowed(self.check([task()], cwd=elsewhere))
        self.assertHeld(self.check([task()], environ={"CLAUDE_PROJECT_DIR": elsewhere}))


class WiringTests(Base):
    def run_hook(self, event, environ=None):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env.update(self.environ, CLAUDE_CONFIG_DIR=self.config, **(environ or {}))
        proc = subprocess.run([sys.executable, "-I", "-S", SCRIPT], input=json.dumps(event).encode("utf-8"),
                              capture_output=True, timeout=60, env=env)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        return json.loads(proc.stdout or b"{}")

    def test_the_stop_hook_blocks_before_its_transcript_check(self):
        self.output()
        data = self.run_hook(self.event([task()], stop_hook_active=True))
        self.assertEqual(sorted(data), ["decision", "reason"])
        self.assertEqual(data["decision"], "block")
        self.assertTrue(data["reason"].startswith(turn_guard.PREFIX + " ") and "b58ki4brf" in data["reason"])
        self.write(self.state, "---\ncoordinator: true\n---\n")
        self.assertEqual(self.run_hook(self.event([task()])), {})

    def test_a_turn_block_is_the_only_output(self):
        """With an open side session too, the turn guard's block is printed alone (the transcript is not read)."""
        self.write(self.transcript, json.dumps({"type": "user", "message": {"role": "user", "content": NOTE_TEXT}}) + "\n")
        self.output()
        data = self.run_hook(self.event([task()]))
        self.assertEqual(sorted(data), ["decision", "reason"])
        self.assertTrue(data["reason"].startswith(turn_guard.PREFIX + " "), data)

    def test_the_released_hold_prints_one_line(self):
        self.output()
        self.write(self.counts, json.dumps({"format": 1, "blocks": {"b58ki4brf": 2}}))
        data = self.run_hook(self.event([task()], stop_hook_active=True))
        self.assertEqual(sorted(data), ["systemMessage"])
        self.assertTrue(data["systemMessage"].startswith(turn_guard.PREFIX + " ") and "b58ki4brf" in data["systemMessage"])
        self.assertEqual(self.run_hook(self.event([task()], stop_hook_active=True)), {})

    def test_the_hook_prints_a_capped_runs_note_with_another_runs_block(self):
        self.output()
        self.output("bq2")
        self.write(self.counts, json.dumps({"format": 1, "blocks": {"b58ki4brf": 2}}))
        data = self.run_hook(self.event([task(), task("bq2")], stop_hook_active=True))
        self.assertEqual(sorted(data), ["decision", "reason", "systemMessage"])
        self.assertEqual(data["decision"], "block")
        self.assertIn("bq2", data["reason"])
        self.assertTrue(data["systemMessage"].startswith(turn_guard.PREFIX + " ") and "b58ki4brf" in data["systemMessage"])

    def test_the_stop_hook_holds_in_a_config_without_a_state_folder(self):
        self.output()
        shutil.rmtree(self.state_dir)
        data = self.run_hook(self.event([task()]))
        self.assertEqual(data.get("decision"), "block")
        self.assertTrue(data["reason"].startswith(turn_guard.PREFIX + " ") and "Block 1 of 2" in data["reason"])
        self.assertTrue(os.path.isfile(self.counts))

    def test_an_unwritable_count_holds_then_prints_one_line(self):
        self.output()
        shutil.rmtree(self.state_dir)
        self.write(self.state_dir, "")
        data = self.run_hook(self.event([task()]))
        self.assertEqual(data.get("decision"), "block")
        self.assertTrue(data["reason"].startswith(turn_guard.PREFIX + " "), data)
        data = self.run_hook(self.event([task()], stop_hook_active=True))
        self.assertEqual(sorted(data), ["systemMessage"])
        self.assertIn("block count cannot be written", data["systemMessage"])

    def test_an_open_side_session_still_blocks_with_bad_tasks(self):
        self.write(self.transcript, json.dumps({"type": "user", "message": {"role": "user", "content": NOTE_TEXT}}) + "\n")
        data = self.run_hook(self.event("not a list"))
        self.assertEqual(data.get("decision"), "block")
        self.assertTrue(data["reason"].startswith("side-session-guard: "), data)
        self.assertEqual(self.run_hook(self.event("not a list", stop_hook_active=True)), {})


if __name__ == "__main__":
    unittest.main()
