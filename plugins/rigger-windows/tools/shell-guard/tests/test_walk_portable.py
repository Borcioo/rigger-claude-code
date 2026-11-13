"""The junction walk where the OS stat has no Windows fields: on POSIX, `git worktree remove` must not be denied.

python -I test_walk_portable.py

test_rules.py is Windows-only and covers this with a stat stub; here the real stat of the running OS is used, so
the POSIX gate exercises it. A symbolic link to an outside target must not stop the walk on any OS.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.dont_write_bytecode = True
import shell_guard  # noqa: E402


class WalkOnThisOs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sg-portable-")
        self.addCleanup(shutil.rmtree, self.root)
        self.tree = os.path.join(self.root, "tree")
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(os.path.join(self.tree, "sub"))
        os.makedirs(self.outside)

    def link_outside(self):
        try:
            os.symlink(self.outside, os.path.join(self.tree, "sub", "link"), target_is_directory=True)
        except OSError:
            self.skipTest("creating a symbolic link is not allowed here")

    def test_a_plain_tree_is_clear(self):
        self.assertEqual(shell_guard.outside_links(self.tree), ([], True))

    def test_a_symbolic_link_is_not_read_as_a_mount_point(self):
        self.link_outside()
        self.assertEqual(shell_guard.outside_links(self.tree), ([], True))

    def test_the_hook_allows_the_remove(self):
        self.link_outside()
        found = shell_guard.check_git("Bash", "git worktree remove '%s'" % self.tree, self.root)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
