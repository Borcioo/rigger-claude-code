"""Tests for snapshot.py: the per-session resolved snapshot, its path guard and its cleanup."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import snapshot  # noqa: E402

NOW = 2000000000.0
DAY = 86400


def full_data(**changes):
    data = {key: [] for key in snapshot.SNAPSHOT_KEYS}
    data.update({"format": snapshot.SNAPSHOT_FORMAT, "parts": {"session": ["a"], "subagent": []}, "config": {}})
    data["notices"] = ["rigger: zażółć"]
    data.update(changes)
    return data


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="snapshot "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "config dir"
        self.config.mkdir()
        self.state = self.config / "rigger" / "state"

    def files_under(self, path):
        return sorted(str(p.relative_to(path)) for p in path.rglob("*"))

    def test_unsafe_session_id_writes_nothing(self):
        for session_id in ("", "../x", "a\\b", "a/b", "x" * 129, "abc\n", "a b", "..", "a.b", None, 5, "ż",
                           "１"):
            with self.subTest(session_id=repr(session_id)):
                self.assertIsNone(snapshot.snapshot_path(self.config, session_id))
                self.assertIsNone(snapshot.write_snapshot(self.config, session_id, full_data()))
                self.assertIsNone(snapshot.read_snapshot(self.config, session_id))
                self.assertEqual(self.files_under(self.tmp), ["config dir"])

    def test_unsafe_session_id_reads_nothing_outside_state(self):
        planted = self.config / "rigger" / "x.resolved.json"
        planted.parent.mkdir(parents=True)
        planted.write_bytes(json.dumps(full_data()).encode("utf-8"))
        self.assertIsNone(snapshot.read_snapshot(self.config, "../x"))
        (self.state).mkdir()
        (self.state / "x.resolved.json").write_bytes(json.dumps(full_data()).encode("utf-8"))
        self.assertEqual(snapshot.read_snapshot(self.config, "x"), full_data())

    def test_safe_session_id_path(self):
        path = snapshot.snapshot_path(self.config, "Ab_9-" + "x" * 123)
        self.assertEqual(path, self.state / ("Ab_9-" + "x" * 123 + ".resolved.json"))

    def test_round_trip_and_atomic_name(self):
        data = full_data()
        path = snapshot.write_snapshot(self.config, "s1", data)
        self.assertEqual(path, self.state / "s1.resolved.json")
        self.assertEqual(snapshot.read_snapshot(self.config, "s1"), data)
        self.assertEqual(self.files_under(self.state), ["s1.resolved.json"])
        raw = path.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn(b"za\\u017c\\u00f3\\u0142\\u0107", raw)  # ASCII escapes (I1)
        raw.decode("ascii")
        self.assertEqual(json.loads(raw.decode("utf-8")), data)

    def test_lone_surrogate_round_trips(self):
        # Raw non-UTF-8 bytes reach the snapshot as surrogateescape text and must come back unchanged.
        data = full_data(notices=["a\udcbf\ud800"])
        self.assertIsNotNone(snapshot.write_snapshot(self.config, "s1", data))
        self.assertEqual(snapshot.read_snapshot(self.config, "s1")["notices"], ["a\udcbf\ud800"])

    def test_unserialisable_data_writes_nothing(self):
        self.assertIsNone(snapshot.write_snapshot(self.config, "s1", full_data(notices=[object()])))
        self.assertEqual([path for path in self.tmp.rglob("*") if path.is_file()], [])

    def test_old_format_reads_as_missing(self):
        self.state.mkdir(parents=True)
        no_parts = full_data()
        del no_parts["parts"]
        rows = [
            ("format 0", json.dumps(full_data(format=0))),
            ("format true", json.dumps(full_data(format=True))),
            ("format 1.0", json.dumps(full_data(format=1.0))),
            ("no parts", json.dumps(no_parts)),
            ("a list", "[]"),
            ("not json", "{"),
        ]
        for label, body in rows:
            with self.subTest(label):
                (self.state / "old.resolved.json").write_bytes(body.encode("utf-8"))
                self.assertIsNone(snapshot.read_snapshot(self.config, "old"))
        self.assertIsNone(snapshot.read_snapshot(self.config, "missing"))

    def test_cleanup_age(self):
        self.state.mkdir(parents=True)
        ages = {
            "old.resolved.json": 15,
            "new.resolved.json": 13,
            "old.options.rigger-talk.json": 15,
            "old.resolved.json.123.tmp": 15,
            "notes.txt": 15,
            "old.json": 15,
        }
        for name, days in ages.items():
            path = self.state / name
            path.write_bytes(b"{}")
            os.utime(str(path), (NOW - days * DAY, NOW - days * DAY))
        (self.state / "dir.resolved.json").mkdir()
        os.utime(str(self.state / "dir.resolved.json"), (NOW - 20 * DAY, NOW - 20 * DAY))
        self.assertEqual(snapshot.cleanup(self.config, NOW), 3)
        self.assertEqual(
            self.files_under(self.state), ["dir.resolved.json", "new.resolved.json", "notes.txt", "old.json"]
        )

    def test_cleanup_without_state_dir(self):
        self.assertEqual(snapshot.cleanup(self.config, NOW), 0)


if __name__ == "__main__":
    unittest.main()
