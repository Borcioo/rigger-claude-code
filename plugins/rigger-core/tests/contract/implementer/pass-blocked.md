STATUS: BLOCKED
FILES:
src/app.py
src/tests/test_app.py
ROOT CAUSE: none
GATE: python -m unittest -> Ran 4 tests, OK, exit 0
RED RUNS: test_app.test_token -> drop the token check -> assertEqual failed
QUESTION: which session class owns the token?
CALL: python -m unittest
OUTPUT: ImportError: no module named session
ALTERNATIVE: python -I -m unittest, the same error
