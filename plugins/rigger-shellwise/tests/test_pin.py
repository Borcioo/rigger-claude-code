"""pin.json against the release table of the pinned shellwise version, copied here literally."""

import json
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PIN = PLUGIN / "pin.json"

VERSION = "0.2.0"
# target -> (asset name, SHA-256); the lines of the v0.2.0 release's SHA256SUMS. Linux takes the static musl
# build, which needs no glibc of a given version.
TABLE = {
    "x86_64-pc-windows-msvc": (
        "shellwise-v0.2.0-x86_64-pc-windows-msvc.zip",
        "a19a55c2b9d8fb62c5d5a22218ccafada111a8239ed9dc129d4e1a665d1fdcb5",
    ),
    "x86_64-unknown-linux-musl": (
        "shellwise-v0.2.0-x86_64-unknown-linux-musl.tar.gz",
        "d76cc6d842285a247b6a8256ea8c03ef5fdfc7565f7ad9a4714c304e58b1e49c",
    ),
    "aarch64-unknown-linux-musl": (
        "shellwise-v0.2.0-aarch64-unknown-linux-musl.tar.gz",
        "c9b5cfbb84103c71209257a1e7efc42a28fc4b63850e05e714187be0185b2b07",
    ),
    "aarch64-apple-darwin": (
        "shellwise-v0.2.0-aarch64-apple-darwin.tar.gz",
        "5e6f5ee8a397239de38ce251b87e16f7f5b81687f8386c7fa1bfd95dcdf08da6",
    ),
    "x86_64-apple-darwin": (
        "shellwise-v0.2.0-x86_64-apple-darwin.tar.gz",
        "922b4c41a97e0cf9bc0de88561f1905bb1067867c4e09f3713845958f54ca6a8",
    ),
}


def load_pin():
    return json.loads(PIN.read_bytes().decode("utf-8"))


class PinTests(unittest.TestCase):
    def test_format_and_version(self):
        data = load_pin()
        self.assertEqual(data["format"], 1)
        self.assertEqual(data["version"], VERSION)
        self.assertEqual(sorted(data), ["assets", "format", "version"])

    def test_exactly_the_five_targets(self):
        self.assertEqual(sorted(load_pin()["assets"]), sorted(TABLE))

    def test_each_target_has_its_asset_name_and_hash(self):
        assets = load_pin()["assets"]
        self.assertEqual(len(assets), 5)
        for target, (name, sha) in TABLE.items():
            with self.subTest(target=target):
                self.assertEqual(assets[target], {"name": name, "sha256": sha})

    def test_hashes_are_64_lower_case_hex(self):
        assets = load_pin()["assets"]
        self.assertEqual(len(assets), 5)
        for target, asset in assets.items():
            with self.subTest(target=target):
                self.assertRegex(asset["sha256"], r"\A[0-9a-f]{64}\Z")

    def test_file_is_lf_utf8(self):
        raw = PIN.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        raw.decode("utf-8")
        self.assertTrue(raw.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
