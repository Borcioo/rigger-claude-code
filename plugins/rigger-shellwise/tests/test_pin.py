"""pin.json against the release table of the pinned shellwise version, copied here literally."""

import json
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PIN = PLUGIN / "pin.json"

VERSION = "0.1.1"
# target -> (asset name, SHA-256); the Release table of the v0.1.1 spike
TABLE = {
    "x86_64-pc-windows-msvc": (
        "shellwise-v0.1.1-x86_64-pc-windows-msvc.zip",
        "fe30a2b01b979a0fd3c2e965123bbf29ab1620f711b79165a21bc9cb5cf0a5f9",
    ),
    "x86_64-unknown-linux-gnu": (
        "shellwise-v0.1.1-x86_64-unknown-linux-gnu.tar.gz",
        "e4a2a5f3291baef64f63c272e5a28e2bf355cab0b066904833dbc92ab0531dfc",
    ),
    "aarch64-apple-darwin": (
        "shellwise-v0.1.1-aarch64-apple-darwin.tar.gz",
        "206c39687e216db360c214e0f94544299557a0b3cfdc5a003b53362e4f95fa08",
    ),
    "x86_64-apple-darwin": (
        "shellwise-v0.1.1-x86_64-apple-darwin.tar.gz",
        "4a248c43812290fb37e618af39a10e2008d432ebd77b63f28d98e3646caee3cd",
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

    def test_exactly_the_four_targets(self):
        self.assertEqual(sorted(load_pin()["assets"]), sorted(TABLE))

    def test_each_target_has_its_asset_name_and_hash(self):
        assets = load_pin()["assets"]
        self.assertEqual(len(assets), 4)
        for target, (name, sha) in TABLE.items():
            with self.subTest(target=target):
                self.assertEqual(assets[target], {"name": name, "sha256": sha})

    def test_hashes_are_64_lower_case_hex(self):
        assets = load_pin()["assets"]
        self.assertEqual(len(assets), 4)
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
