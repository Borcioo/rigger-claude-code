"""slot_run tests: the cleanup helper names what holds a locked folder (D92).

python -I -X utf8 test_slot_run_cleanup.py
"""
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slot_run_case  # noqa: E402
from slot_run_case import (  # noqa: E402
    NT,
    alive_children,
    remove_tree,
)

LOCK_REASON = "an open file blocks deleting its folder on Windows only"


def locked_folder(case):
    """A folder holding a file that stays open without delete sharing: removing it fails with WinError 32."""
    folder = tempfile.mkdtemp(prefix="slot-run-locked-")
    handle = open(os.path.join(folder, "held.txt"), "wb")
    case.addCleanup(lambda: os.path.isdir(folder) and remove_tree(folder))  # runs after the handle is closed
    case.addCleanup(lambda: handle.closed or handle.close())
    return folder, handle


class CleanupDiagnosticTests(unittest.TestCase):
    def run_remove(self, folder, sleep=lambda _delay: None):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            try:
                remove_tree(folder, sleep=sleep)
            except PermissionError as exc:
                return exc, err.getvalue()
        return None, err.getvalue()

    def test_a_clean_removal_prints_nothing(self):
        folder = tempfile.mkdtemp(prefix="slot-run-clean-")
        error, text = self.run_remove(folder)
        self.assertEqual((error, text, os.path.exists(folder)), (None, "", False))

    def test_a_retry_that_succeeds_is_reported(self):
        folder = tempfile.mkdtemp(prefix="slot-run-retry-")
        calls = []

        def locked_once(path, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(13, "locked", path)

        slept = []
        with mock.patch("shutil.rmtree", side_effect=locked_once):
            error, text = self.run_remove(folder, sleep=slept.append)
        self.assertIsNone(error)
        self.assertEqual((len(calls), slept), (2, [0.5]))
        self.assertIn("# cleanup retried: %s" % folder, text.splitlines())
        remove_tree(folder)

    def test_a_second_failure_raises_with_the_diagnostic(self):
        folder = tempfile.mkdtemp(prefix="slot-run-stuck-")
        with mock.patch("shutil.rmtree", side_effect=PermissionError(13, "locked", folder)), \
                mock.patch.object(slot_run_case, "alive_children", return_value="4242 held.exe"):
            error, text = self.run_remove(folder)
        self.assertIsInstance(error, PermissionError)
        self.assertIn("# cleanup failed: %s" % folder, str(error))
        self.assertIn("# cleanup failed: %s" % folder, text)
        self.assertIn("children: 4242 held.exe", str(error))
        self.assertIn("children: 4242 held.exe", text)
        remove_tree(folder)

    @unittest.skipUnless(NT, LOCK_REASON)
    def test_a_handle_released_during_the_wait_lets_the_retry_succeed(self):
        folder, handle = locked_folder(self)
        error, text = self.run_remove(folder, sleep=lambda _delay: handle.close())
        self.assertEqual((error, os.path.exists(folder)), (None, False))
        self.assertIn("# cleanup retried: %s" % folder, text.splitlines())

    @unittest.skipUnless(NT, LOCK_REASON)
    def test_a_handle_held_through_the_wait_raises_with_the_diagnostic(self):
        folder, handle = locked_folder(self)
        error, text = self.run_remove(folder)
        self.assertIsInstance(error, PermissionError)
        self.assertIn("# cleanup failed: %s" % folder, str(error))
        self.assertTrue(os.path.exists(folder))


class AliveChildrenTests(unittest.TestCase):
    def test_a_running_child_is_named_and_a_finished_one_is_not(self):
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        deadline = time.time() + 20
        text = alive_children()
        while str(child.pid) not in text and time.time() < deadline:
            time.sleep(0.2)
            text = alive_children()
        self.assertIn(str(child.pid), text)
        child.kill()
        child.wait()
        self.assertNotIn(str(child.pid), alive_children())


if __name__ == "__main__":
    unittest.main(verbosity=2)
