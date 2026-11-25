"""slot_run: the time, output and process bounds of a run.

python -I -X utf8 test_slot_run_bounds.py
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slot_run_case import (  # noqa: E402
    NT,
    REAL_POPEN,
    SCRIPT,
    SESSION,
    SlotRunCase,
    command_row,
    inside,
    pid_alive,
    slot_run,
)


class BoundTests(SlotRunCase):
    def test_timeout_kills_the_tree_and_refuses(self):
        """Review Focus 3: a command that never exits and leaves a grandchild."""
        self.write_snapshot([command_row(self.command("hang"))])
        calls = []

        def recording_popen(*args, **kwargs):
            calls.append(args[0] if args else kwargs["args"])
            return REAL_POPEN(*args, **kwargs)

        started = time.monotonic()
        with mock.patch.object(slot_run, "TIMEOUT", 2), mock.patch.object(subprocess, "Popen", recording_popen):
            code, lines = self.run_slot()
        elapsed = time.monotonic() - started
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: timed out after 2 s"), lines)
        self.assertLess(elapsed, 15)
        with open(self.record + ".child.pid", "rb") as handle:
            pid = int(handle.read())
        deadline = time.monotonic() + 5
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid), "the grandchild %d outlived the kill" % pid)
        for argv0 in [call[0] for call in calls]:  # git, the command and the tree kill: never by bare name
            self.assertTrue(os.path.isabs(argv0) and not inside(argv0, self.repo), argv0)

    def test_output_over_the_cap_refuses(self):
        """Review Focus 3 and Q4: the console is read through a pipe and kept to the cap, at two caps."""
        for mode, cap in (("flood", 10000), ("flood", 1000000), ("flood-err", 10000)):
            with self.subTest(mode=mode, cap=cap):
                self.write_snapshot([command_row(self.command(mode))])
                started = time.monotonic()
                # TIMEOUT stays far above the bound, so a missing bound fails on the reason, never hangs.
                with mock.patch.object(slot_run, "CONSOLE_CAP", cap), mock.patch.object(slot_run, "TIMEOUT", 20):
                    code, lines = self.run_slot()
                elapsed = time.monotonic() - started
                self.assertEqual((code, lines[-1]), (2, "slot-run: refused: more than %d bytes of console output"
                                                     % cap), lines)
                console = os.path.join(os.path.dirname(self.output_path(lines)), "console.txt")
                self.assertEqual(os.path.getsize(console), cap)
                self.assertLess(elapsed, 30)
                pid = self.recorded()["pid"]
                deadline = time.monotonic() + 5
                while pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertFalse(pid_alive(pid), "the flooding command %d outlived the refusal" % pid)

    def test_output_over_the_cap_refuses_after_a_clean_exit(self):
        self.write_snapshot([command_row(self.command("flood-exit", "--bytes", "10100"))])
        with mock.patch.object(slot_run, "CONSOLE_CAP", 10000):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: more than 10000 bytes of console output"), lines)

    def grandchild_pid(self):
        with open(self.record + ".child.pid", "rb") as handle:
            return int(handle.read())

    def assert_gone_after_the_run(self, pid):
        """Gone within a second of slot_run's return: the kill belongs to the run, not to a later cleanup."""
        deadline = time.monotonic() + 1
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(pid_alive(pid), "process %d outlived the run" % pid)

    def test_a_descendant_left_holding_the_console_is_stopped_and_refuses(self):
        """The command exits 0 with a valid file, but a process it started sleeps 30 s holding the console:
        refused after the exit grace, long before the deadline, with that process gone."""
        self.assertEqual(slot_run.EXIT_GRACE, 5)  # the default the 10 s bound below is written for
        self.write_snapshot([command_row(self.command("orphan"))])
        started = time.monotonic()
        with mock.patch.object(slot_run, "TIMEOUT", 60), mock.patch.object(slot_run, "EXIT_GRACE", 1):
            code, lines = self.run_slot()
        elapsed = time.monotonic() - started
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: child process left running"), lines)
        self.assertLess(elapsed, 10)
        self.assert_gone_after_the_run(self.grandchild_pid())
        self.assert_gone_after_the_run(self.recorded()["pid"])

    @unittest.skipUnless(NT, "the job object is Windows only")
    def test_a_command_outside_a_job_never_runs(self):
        self.write_snapshot([command_row(self.command("ok"))])
        with mock.patch.object(slot_run, "_enter_job", return_value=False), mock.patch.object(slot_run, "TIMEOUT", 5):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the command could not be placed in a job object"))
        self.assertFalse(os.path.exists(self.record), "the suspended command ran")

    @unittest.skipUnless(NT, "the job object is Windows only; on POSIX the group outlives a killed slot_run")
    def test_killing_slot_run_stops_the_command_tree(self):
        """The shell tool's own timeout kills slot_run: the job closes with it, and so does the command tree."""
        self.write_snapshot([command_row(self.command("hang"))])
        proc = REAL_POPEN([sys.executable, "-I", "-X", "utf8", SCRIPT, "reviewer", "--session", SESSION, "--base",
                           self.base, "--head", self.head], cwd=str(self.repo), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=dict(os.environ, TMP=tempfile.tempdir, TEMP=tempfile.tempdir))  # its work folder
        try:
            deadline = time.monotonic() + 30
            while not os.path.exists(self.record + ".child.pid") and time.monotonic() < deadline:
                time.sleep(0.05)
            pid = self.grandchild_pid()
            self.assertTrue(pid_alive(pid))
        finally:
            proc.kill()
            proc.wait(timeout=30)
        self.assert_gone_after_the_run(pid)
        self.assert_gone_after_the_run(self.recorded()["pid"])

    def test_a_descendant_that_let_go_of_the_console_is_stopped(self):
        """A process the command started, with its console on the null device, does not outlive the run."""
        self.write_snapshot([command_row(self.command("detached"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        self.assert_gone_after_the_run(self.grandchild_pid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
