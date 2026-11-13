"""What test_rules.py and test_commit_gate.py both need; not a test file."""
import os

GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shell_guard.py")


def _bounded(fn, seconds):
    """fn() on a daemon thread, waited for at most seconds, so a hang ends as a failed assertion:
    ("ok", value), ("raised", repr) or None when fn had not returned."""
    import threading
    import time
    box = [None]

    def run():
        try:
            box[0] = ("ok", fn())
        except Exception as exc:
            box[0] = ("raised", repr(exc))
    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + seconds
    while box[0] is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return box[0]
