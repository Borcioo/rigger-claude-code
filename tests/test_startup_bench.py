"""Smoke test of scripts/startup_bench.py: two runs print the summary line the CI step reads."""

import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "startup_bench.py"
SUMMARY = re.compile(r"# startup median old (\d+) new (\d+) delta (-?\d+) max new (\d+) runs (\d+)")


class StartupBenchTests(unittest.TestCase):
    def test_prints_the_summary_line(self):
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPT), "--runs", "2"], cwd=str(ROOT),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        out = done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        self.assertEqual(done.returncode, 0, out)
        found = [SUMMARY.fullmatch(line) for line in out.splitlines()]
        found = [match for match in found if match]
        self.assertEqual(len(found), 1, out)
        old, new, delta, max_new, runs = (int(value) for value in found[0].groups())
        self.assertEqual(runs, 2)
        self.assertEqual(delta, new - old)
        self.assertGreater(old, 0)
        self.assertGreaterEqual(max_new, new)


if __name__ == "__main__":
    unittest.main(verbosity=2)
