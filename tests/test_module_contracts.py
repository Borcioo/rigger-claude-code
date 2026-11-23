"""Contract test of the module manifests (spec section 12): `assemble.py --check` over the repository, with a
planted duplicate it must catch, and no public manifest naming a denylist."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "rigger-core"
ASSEMBLE = CORE / "tools" / "assemble" / "assemble.py"
MANIFEST = "rigger-module.json"
MODULES = 7
SUMMARY = re.compile(r"^# check: modules (\d+), rules files (\d+), findings 0$")


def module_dirs():
    return sorted(path.parent for path in (ROOT / "plugins").glob("*/" + MANIFEST))


def run_check(dirs, config_dir):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("CLAUDE_", "HARNESS_")) and key.upper() != "RIGGER_MODULE_DIRS"}
    env.update(CLAUDE_PLUGIN_ROOT=str(CORE), CLAUDE_CONFIG_DIR=str(config_dir), RIGGER_MODULE_DIRS="")
    proc = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(ASSEMBLE), "--check", "--modules"] + [str(d) for d in dirs],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=120,
    )
    return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")


def denylist_findings(paths):
    """The manifests among `paths` that hold `lessons.denylist`; refuses an empty list (nothing read)."""
    paths = list(paths)
    if not paths:
        raise AssertionError("0 manifests read")
    found = []
    for path in paths:
        data = json.loads(path.read_bytes().decode("utf-8"))
        lessons = data.get("lessons") if isinstance(data, dict) else None
        if isinstance(lessons, dict) and "denylist" in lessons:
            found.append(path)
    return found


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="contracts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "config"
        self.config.mkdir()

    def test_check_over_repository(self):
        dirs = module_dirs()
        self.assertEqual(len(dirs), MODULES, dirs)
        code, out, err = run_check(dirs, self.config)
        self.assertEqual(code, 0, out + err)
        match = SUMMARY.match(out.splitlines()[-1])
        self.assertIsNotNone(match, out)
        self.assertEqual(int(match.group(1)), MODULES, out)
        self.assertGreater(int(match.group(2)), 0, out)

    def test_check_catches_a_planted_duplicate(self):
        copy = self.tmp / "plugins" / "rigger-talk"
        shutil.copytree(ROOT / "plugins" / "rigger-talk", copy, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / "rules" / "tests.md").write_bytes(b"## Tests\n- planted\n")
        manifest = json.loads((copy / MANIFEST).read_bytes().decode("utf-8"))
        manifest["rules"].append({"file": "rules/tests.md", "audience": "both"})
        (copy / MANIFEST).write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        dirs = [copy if d.name == "rigger-talk" else d for d in module_dirs()]
        self.assertIn(copy, dirs)
        code, out, err = run_check(dirs, self.config)
        self.assertEqual(code, 1, out + err)
        self.assertIn("duplicate rule id tests.tests in rigger-rules and rigger-talk", out.splitlines(), out)

    def test_no_public_manifest_names_a_denylist(self):
        manifests = [d / MANIFEST for d in module_dirs()]
        self.assertEqual(len(manifests), MODULES)
        self.assertEqual(denylist_findings(manifests), [])
        planted = self.tmp / MANIFEST
        planted.write_bytes(json.dumps({"name": "mod-company", "lessons": {"denylist": "leak/terms.txt"}}).encode("utf-8"))
        self.assertEqual(denylist_findings(manifests + [planted]), [planted])
        with self.assertRaisesRegex(AssertionError, "0 manifests read"):
            denylist_findings([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
