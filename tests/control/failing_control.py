"""Planted failure for `run_tests.py --control`: the runner must report this file failed.

Not discovered by the normal run (the runner only collects test_*.py files).
"""

import unittest


class FailingControl(unittest.TestCase):
    def test_planted_failure(self):
        self.assertEqual(1, 2, "planted failure: run_tests.py --control must report this file failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
