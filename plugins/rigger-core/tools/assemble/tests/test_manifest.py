"""Tests for manifest.py: loading and validating rigger-module.json."""

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import manifest  # noqa: E402


def base_manifest(name="demo"):
    return {
        "schema": 1,
        "name": name,
        "layer": "general",
        "fills": None,
        "rules": [{"file": "rules/demo.md", "audience": "both"}],
        "triggers": [{"id": name + ".one", "before": "a thing", "load": ["x:y"]}],
        "tests": {"paths": ["tests/"], "contract": []},
    }


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="manifest "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, data, name="demo", raw=None, plugin_name=None):
        root = self.tmp / ("plugin-%d" % len(list(self.tmp.iterdir())))
        (root / ".claude-plugin").mkdir(parents=True)
        (root / "rules").mkdir()
        (root / "rules" / "demo.md").write_bytes(b"## Demo\n")
        plugin = {"name": plugin_name or name}
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin), encoding="utf-8")
        body = raw if raw is not None else json.dumps(data).encode("utf-8")
        (root / manifest.MANIFEST).write_bytes(body)
        return root

    def test_refusals(self):
        def with_(**changes):
            data = base_manifest()
            data.update(changes)
            return data

        def rule(**changes):
            entry = {"file": "rules/demo.md", "audience": "both"}
            entry.update(changes)
            return with_(rules=[entry])

        two_rows = base_manifest()
        two_rows["triggers"] = [
            {"id": "demo.one", "before": "a", "load": ["x:y"]},
            {"id": "demo.one", "before": "b", "load": ["x:z"]},
        ]
        rows = [
            ("schema 2", with_(schema=2), None, "rigger", None, "schema 2"),
            ("schema true", with_(schema=True), None, "rigger", None, "schema True"),
            ("name differs", with_(name="other"), None, "rigger", "demo", "name"),
            ("missing rules file", rule(file="rules/nope.md"), None, "rigger", None, "missing rules file"),
            ("rules file outside", rule(file="../demo.md"), None, "rigger", None, "outside the module"),
            ("rules file absolute", rule(file="/abs.md"), None, "rigger", None, "outside the module"),
            ("rules file drive", rule(file="C" + ":/abs.md"), None, "rigger", None, "outside the module"),
            ("rules file backslash", rule(file="rules\\..\\..\\x.md"), None, "rigger", None, "outside the module"),
            ("company public", with_(layer="company"), None, "rigger", None, "company layer in the public marketplace"),
            ("config general", with_(config={"slots": {}}), None, "rigger", None, "config"),
            ("duplicate trigger", two_rows, None, "rigger", None, "duplicate trigger id"),
            ("rule above module", rule(layer="company"), None, "private", None, "above its module"),
            ("audience all", rule(audience="all"), None, "rigger", None, "audience"),
            ("audience missing", with_(rules=[{"file": "rules/demo.md"}]), None, "rigger", None, "audience"),
            ("requires os", rule(requires=["os:beos"]), None, "rigger", None, "requires"),
            ("requires object", rule(requires={"os:linux": True}), None, "rigger", None, "requires must be a list"),
            ("rules object", with_(rules={"file": "rules/demo.md"}), None, "rigger", None, "rules must be a list"),
            (
                "load string",
                with_(triggers=[{"id": "demo.one", "before": "a", "load": "x:y"}]),
                None,
                "rigger",
                None,
                "load must be a list",
            ),
            ("invalid json", None, b"{not json", "rigger", None, "not valid JSON"),
            ("top-level list", None, b"[]", "rigger", None, "must be an object"),
            ("nested 40", None, b'{"schema": 1, "provides": ' + b'{"a": ' * 40 + b"1" + b"}" * 41, "rigger", None,
             "nested deeper than"),
            ("nested 100000", None, b"[" * 100000 + b"]" * 100000, "rigger", None, "nested deeper than"),
        ]
        for label, data, raw, marketplace, plugin_name, fragment in rows:
            with self.subTest(label):
                root = self.make(data, raw=raw, plugin_name=plugin_name)
                with self.assertRaises(manifest.ManifestError) as caught:
                    manifest.load_manifest(root, marketplace, "installed")
                self.assertIn(fragment, str(caught.exception))

    def test_bom_prefixed_file_loads(self):
        body = b"\xef\xbb\xbf" + json.dumps(base_manifest()).encode("utf-8")
        root = self.make(None, raw=body)
        module = manifest.load_manifest(root, "rigger", "installed")
        self.assertEqual(module.name, "demo")

    def test_company_module_outside_the_public_marketplace_loads(self):
        data = base_manifest()
        data["layer"] = "company"
        data["config"] = {"slots": {}}
        data["rules"][0]["layer"] = "company"
        module = manifest.load_manifest(self.make(data), "private", "installed")
        self.assertEqual((module.layer, module.config, module.rules[0].layer), ("company", {"slots": {}}, "company"))

    def test_defaults(self):
        data = base_manifest()
        data["failure_mode"] = "later phase key"
        root = self.make(data)
        module = manifest.load_manifest(root, "rigger", "dirs")
        self.assertEqual(
            module.rules, [manifest.RuleEntry(file="rules/demo.md", layer="general", audience="both", requires=())]
        )
        self.assertEqual(
            module.triggers,
            [manifest.TriggerRow(id="demo.one", before="a thing", load=("x:y",), section=None, requires=())],
        )
        self.assertEqual((module.subagents, module.config, module.lessons, module.provides), ({}, None, None, {}))
        self.assertEqual((module.marketplace, module.origin, module.root), ("rigger", "dirs", root))

    def test_subagents_map_to_rules_value(self):
        data = base_manifest()
        data["subagents"] = {"x:y": {"rules": "none"}}
        module = manifest.load_manifest(self.make(data), "rigger", "installed")
        self.assertEqual(module.subagents, {"x:y": "none"})
        data["subagents"] = {"x:y": {"rules": "some"}}
        with self.assertRaises(manifest.ManifestError) as caught:
            manifest.load_manifest(self.make(data), "rigger", "installed")
        self.assertIn("subagents", str(caught.exception))

    def test_core_may_carry_config(self):
        data = base_manifest(manifest.CORE)
        data["config"] = {"schema": 1}
        module = manifest.load_manifest(self.make(data, name=manifest.CORE), "rigger", "self")
        self.assertEqual(module.config, {"schema": 1})

    def test_valid_manifest_round_trip(self):
        # Spec section 6.3, the public reviewer module (phase 3).
        data = {
            "schema": 1,
            "name": "rigger-review",
            "layer": "general",
            "fills": "reviewer",
            "provides": {"reviewer": {"kind": "agent", "target": "rigger-REVIEW:reviewer", "model": "by-rule"}},
            "rules": [],
            "triggers": [
                {
                    "id": "rigger-review.review",
                    "before": "reviewing a change, a branch or a PR, or verifying an agent's claim",
                    "load": ["rigger-REVIEW:review"],
                }
            ],
            "tests": {"paths": ["tests/", "tools/*/tests/"], "contract": ["reviewer"]},
        }
        root = self.make(copy.deepcopy(data), name="rigger-review")
        module = manifest.load_manifest(root, "rigger", "installed")
        self.assertEqual((module.name, module.fills, module.rules), ("rigger-review", "reviewer", []))
        self.assertEqual(len(module.triggers), 1)
        self.assertEqual(module.triggers[0].load, ("rigger-REVIEW:review",))
        self.assertEqual(module.provides, data["provides"])
        self.assertEqual(module.tests, data["tests"])


if __name__ == "__main__":
    unittest.main()
