STATUS: NEEDS_CONTEXT
FILES:
src/app.py
src/tests/test_app.py
ROOT CAUSE: none
GATE: python -m unittest -> Ran 4 tests, OK, exit 0
RED RUNS: test_app.test_token -> drop the token check -> assertEqual failed
QUESTION: which session class owns the token?
