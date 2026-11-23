"""Tests for scripts/eval_stage.py: the staged copy holds the plugin and each dependency the cases load."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_stage.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def plugin(plugins_dir, name, deps=None):
    root = plugins_dir / name
    manifest = {"name": name}
    if deps is not None:
        manifest["dependencies"] = deps
    write(root / ".claude-plugin" / "plugin.json", json.dumps(manifest))
    write(root / "rules" / f"{name}.md", f"# {name} rules\n")
    return root


class StageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.plugins = Path(self._tmp.name) / "plugins"
        self.out = Path(self._tmp.name) / "staged"

    def stage(self, root):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(root), str(self.out)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def refused(self, p, text):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn(text, p.stdout)

    def test_plugin_and_dependency_are_copied_byte_for_byte(self):
        plugin(self.plugins, "rigger-core")
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        p = self.stage(root)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        staged = self.out / "rigger-rules"
        self.assertEqual((staged / "rules" / "rigger-rules.md").read_bytes(), (root / "rules" / "rigger-rules.md").read_bytes())
        dep = staged / "deps" / "rigger-core"
        self.assertEqual(
            (dep / "rules" / "rigger-core.md").read_bytes(),
            (self.plugins / "rigger-core" / "rules" / "rigger-core.md").read_bytes(),
        )
        self.assertIn(str(staged), p.stdout)
        self.assertIn("# staged rigger-rules, dependencies 1", p.stdout)

    def test_dependency_cases_are_not_staged(self):
        core = plugin(self.plugins, "rigger-core")
        write(core / "evals" / "core-case" / "prompt.md", "---\ntags: [native]\n---\nhi\n")
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        write(root / "evals" / "rules-case" / "prompt.md", "---\ntags: [native]\n---\nhi\n")
        p = self.stage(root)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        staged = self.out / "rigger-rules"
        self.assertEqual([c.name for c in staged.rglob("prompt.md")], ["prompt.md"])
        self.assertTrue((staged / "evals" / "rules-case" / "prompt.md").is_file())
        self.assertFalse((staged / "deps" / "rigger-core" / "evals").exists())

    def test_plugin_without_dependencies_is_copied_alone(self):
        root = plugin(self.plugins, "rigger-core")
        p = self.stage(root)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((self.out / "rigger-core" / "deps").exists())
        self.assertIn("# staged rigger-core, dependencies 0", p.stdout)

    def test_missing_dependency_is_refused(self):
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        self.refused(self.stage(root), "dependency 'rigger-core' not found")
        self.assertFalse(self.out.exists())

    def test_dependency_with_its_own_dependencies_is_refused(self):
        plugin(self.plugins, "rigger-core", ["rigger-base"])
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        self.refused(self.stage(root), "dependency 'rigger-core' has dependencies of its own")

    def test_existing_output_folder_is_refused(self):
        root = plugin(self.plugins, "rigger-core")
        self.out.mkdir()
        self.refused(self.stage(root), "already exists")

    def test_checked_in_deps_folder_is_refused(self):
        plugin(self.plugins, "rigger-core")
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        (root / "deps").mkdir()
        self.refused(self.stage(root), "has a deps/ folder")

    def test_dependencies_not_a_list_is_refused(self):
        root = plugin(self.plugins, "rigger-rules", "rigger-core")
        self.refused(self.stage(root), "dependencies is not a list of names")

    def test_dependency_name_outside_the_plugins_folder_is_refused(self):
        plugin(Path(self._tmp.name), "outside")
        for name in ("../outside", str(Path(self._tmp.name) / "outside"), "a/b", "a\\b", ".."):
            with self.subTest(name=name):
                root = plugin(self.plugins, "rigger-rules", [name])
                self.refused(self.stage(root), "is not a plugin folder name")
                self.assertFalse(self.out.exists())

    def test_dependency_whose_manifest_names_another_plugin_is_refused(self):
        write(self.plugins / "rigger-core" / ".claude-plugin" / "plugin.json", json.dumps({"name": "other"}))
        root = plugin(self.plugins, "rigger-rules", ["rigger-core"])
        self.refused(self.stage(root), "manifest names 'other'")

    def test_folder_without_manifest_is_refused(self):
        root = self.plugins / "nothing"
        root.mkdir(parents=True)
        self.refused(self.stage(root), "cannot read")


if __name__ == "__main__":
    unittest.main()
