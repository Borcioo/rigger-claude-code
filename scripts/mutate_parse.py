"""Read a test run for a mutation script.

    base = scrape(stdout, stderr)                # the unmutated run
    verdict(base, scrape(stdout, stderr), "test_name")   # the mutant's run: red, green or INVALID

Reads unittest, a custom runner's `# name: N passed, M failed`, and TAP's `# tests N`. Only a failed assertion in the
named test is red. Anything else that is not a clean pass is INVALID: a run that fails elsewhere, errors, 0 tests,
another test count, output it cannot read.
"""

import re
from collections import namedtuple

RED, GREEN, INVALID = "red", "green", "INVALID"
Run = namedtuple("Run", "ran ok failing assertions")

SUMMARIES = re.compile(r"(?m)^(?:Ran (\d+) tests? in\b.*?(?:\n\s*)*^(OK|FAILED)\b"
                       r"|# .*?(\d+) passed, (\d+) failed"
                       r"|# tests (\d+)\n(?:# .*\n)*?# fail (\d+))")
FAILING = re.compile(r"(?m)^(?:(FAIL|ERROR): (\S+)|[A-Z]+ FAIL\s+(.+?):|not ok \d+ - (.+)$)")
ASSERTION = re.compile(r"(?m)^\s*(AssertionError\b.*)$")


def scrape(stdout, stderr):
    """A Run of the last summary in the output: `ran` and `ok` are 0 and False when there is none."""
    text = (stdout + "\n" + stderr).replace("\r\n", "\n")
    last = None
    for last in SUMMARIES.finditer(text):
        pass
    ran, ok = 0, False
    if last and last.group(1):
        ran, ok = int(last.group(1)), last.group(2) == "OK"
    elif last and last.group(3):
        ran, ok = int(last.group(3)) + int(last.group(4)), last.group(4) == "0"
    elif last:
        ran, ok = int(last.group(5)), last.group(6) == "0"
    failing = [(found[0] or "FAIL", found[1] or found[2] or found[3]) for found in FAILING.findall(text)]
    return Run(ran, ok, failing, ASSERTION.findall(text))


def verdict(baseline, run, target):
    """red, green or INVALID for a mutant's `run`; `target` is the test that should go red."""
    if not (target and baseline.ok and baseline.ran and run.ran == baseline.ran):
        return INVALID
    if run.ok:
        return INVALID if run.failing else GREEN
    return RED if ("FAIL", target) in run.failing else INVALID
