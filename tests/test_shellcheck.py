"""Runs `shellcheck --shell=sh` on every tracked *.sh; the hooks run under any POSIX sh, so a bashism breaks one."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_repo import tracked_files  # noqa: E402

SHELLCHECK = shutil.which("shellcheck")
# Notes on code that is right in rules_start.sh: SC1003 reads the literal '\' as a failed escape,
# SC2295 objects to `[!$blank]`, which is meant as a pattern.
EXCLUDED = ("SC1003", "SC2295")
BASHISM = '#!/bin/bash\nif [[ -n "$1" ]]; then echo "$1"; fi\n'
CLEAN = '#!/bin/sh\nif [ -n "$1" ]; then echo "$1"; fi\n'


def lint(root):
    """(tracked *.sh names, shellcheck exit code, its output); no file to read is a failure, never a clean run."""
    names = [name for name in tracked_files(root) if name.endswith(".sh")]
    if not names:
        raise AssertionError("no tracked *.sh file under %s" % root)
    proc = subprocess.run(
        [SHELLCHECK, "--shell=sh", "--format=gcc", "--exclude=" + ",".join(EXCLUDED), *names],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )
    return names, proc.returncode, proc.stdout.decode("utf-8", "replace")


class ShellcheckTest(unittest.TestCase):
    def setUp(self):
        if not SHELLCHECK:
            self.skipTest("shellcheck not on PATH: tracked *.sh files are not linted here")

    def repo(self, files):
        """A throwaway git repository with `files` ({name: text}) tracked."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True, stdin=subprocess.DEVNULL, timeout=60)
        for name, text in files.items():
            (root / name).write_bytes(text.encode("utf-8"))
        subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, stdin=subprocess.DEVNULL, timeout=60)
        return root

    def test_every_tracked_sh_file_is_clean(self):
        names, code, output = lint(ROOT)
        self.assertIn("plugins/rigger-core/bin/py.sh", names)
        self.assertIn("plugins/rigger-rules/rules/rules_start.sh", names)
        self.assertEqual((code, output), (0, ""))

    def test_a_clean_file_passes_the_same_call(self):
        self.assertEqual(lint(self.repo({"clean.sh": CLEAN}))[1:], (0, ""))

    def test_a_bashism_fails_even_under_a_bash_shebang(self):
        _, code, output = lint(self.repo({"clean.sh": CLEAN, "bad.sh": BASHISM}))
        self.assertEqual(code, 1)
        self.assertRegex(output, r"bad\.sh:2:\d+: .*\[SC3010\]")
        self.assertNotIn("clean.sh", output)

    def test_only_tracked_sh_files_are_linted(self):
        root = self.repo({"clean.sh": CLEAN, "bash.txt": BASHISM})
        (root / "untracked.sh").write_bytes(BASHISM.encode("utf-8"))
        names, code, output = lint(root)
        self.assertEqual((names, code, output), (["clean.sh"], 0, ""))

    def test_no_tracked_sh_file_fails(self):
        with self.assertRaisesRegex(AssertionError, r"no tracked \*\.sh file"):
            lint(self.repo({"bash.txt": BASHISM}))

    def test_a_tracked_file_that_cannot_be_read_is_not_clean(self):
        root = self.repo({"clean.sh": CLEAN, "gone.sh": CLEAN})
        (root / "gone.sh").unlink()
        _, code, output = lint(root)
        self.assertNotEqual(code, 0)
        self.assertIn("gone.sh", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
