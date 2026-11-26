"""Checks for drift_lint.py's command lists from config (D95): drift_lint.remote_commands and
drift_lint.project_bins in company modules (level 2) and the personal config (level 3) extend the defaults.

python -I test_drift_config.py

Runs on every platform: each case plants a skill repo, an empty config folder and, for level 2, a company module
loaded through RIGGER_MODULE_DIRS under a temp folder, and runs the script end to end with an empty PATH folder.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "drift_lint.py")
FINDING = re.compile(r"^(.+):(\d+)  (\S+)  (.*)$")
# Every name is a command span: a default of each list, two names config may add, and one nothing skips.
LINE = ("Run `htpasswd -B x`, `wp-env start`, `deploytool-co x`, `deploytool-me x` "
        "and `frobnicate-zz --x`.\n")
UNKNOWN = "frobnicate-zz"
ADDED = ["deploytool-co", "deploytool-me"]
CLEARED = ("CLAUDE_", "HARNESS_", "RIGGER_")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if isinstance(text, str) else json.dumps(text))
    return path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repo_root():
    here = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(here, "scripts", "sweep.py")):
            return here
        here = os.path.dirname(here)
    return None


class DriftConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-config-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        write(os.path.join(self.repo, "plugins", "alpha", "skills", "one", "SKILL.md"), "---\nname: one\n---\n" + LINE)
        self.config = os.path.join(self.tmp, "config")
        self.path = os.path.join(self.tmp, "empty-path")
        os.makedirs(self.config)
        os.makedirs(self.path)
        self.dirs = []

    def company(self, block):
        root = os.path.join(self.tmp, "company")
        write(os.path.join(root, ".claude-plugin", "plugin.json"), {"name": "company-mod"})
        write(os.path.join(root, "rigger-module.json"), {"schema": 1, "name": "company-mod", "layer": "company",
                                                          "fills": None, "rules": [], "triggers": [],
                                                          "config": {"drift_lint": block}})
        self.dirs.append(root)

    def personal(self, data):
        write(os.path.join(self.config, "rigger", "config.json"), data)

    def run_lint(self, script=SCRIPT):
        """(exit code, command findings, stderr) of the script on the planted repo."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(CLEARED)}
        env.update(CLAUDE_CONFIG_DIR=self.config, HOME=self.config, RIGGER_MODULE_DIRS=os.pathsep.join(self.dirs))
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", script, "--repo", self.repo, "--home", self.config,
                               "--path", self.path], capture_output=True, timeout=120, env=env, cwd=self.repo)
        out = proc.stdout.decode("utf-8").replace("\r\n", "\n").splitlines()
        found = sorted(m.group(4) for m in map(FINDING.match, out) if m and m.group(3) == "command")
        return proc.returncode, found, proc.stderr.decode("utf-8").replace("\r\n", "\n")

    def test_no_config_flags_every_name_but_the_defaults(self):
        self.assertEqual(self.run_lint()[:2], (1, sorted(ADDED + [UNKNOWN])))

    def extends(self, key):
        self.company({key: ["deploytool-co"]})
        self.personal({"schema": 1, "drift_lint": {key: ["Deploytool-Me"]}})
        code, found, err = self.run_lint()
        self.assertEqual((code, found, err), (1, [UNKNOWN], ""))

    def test_config_extends_remote_commands(self):
        self.extends("remote_commands")

    def test_config_extends_project_bins(self):
        self.extends("project_bins")

    def test_level_4_is_ignored(self):
        block = {"schema": 1, "drift_lint": {"remote_commands": ADDED, "project_bins": ADDED}}
        write(os.path.join(self.repo, ".claude", "rigger.json"), block)
        write(os.path.join(self.repo, ".claude", "rigger", "config.json"), block)
        self.assertEqual(self.run_lint()[:2], (1, sorted(ADDED + [UNKNOWN])))

    def test_config_that_cannot_be_used_leaves_the_defaults(self):
        for what, data, notice in (
                ("not JSON", "{ not json", "ignored: not valid JSON"),
                ("over the cap", {"schema": 1, "drift_lint": {"remote_commands": ["c%d" % n for n in range(50)]
                                                              + ADDED + [UNKNOWN]}},
                 "holds 53 drift_lint.remote_commands entries; the last 3 ignored")):
            with self.subTest(what):
                self.personal(data)
                code, found, err = self.run_lint()
                self.assertEqual((code, found), (1, sorted(ADDED + [UNKNOWN])))
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertIn(notice, err)
                self.assertNotIn("Traceback", err)

    def test_a_company_module_that_does_not_load_says_so(self):
        """Its drift_lint lists are not read, so the lint names the module instead of dropping them silently."""
        self.company({"remote_commands": ADDED})
        write(os.path.join(self.dirs[0], "rigger-module.json"), "{ not json")
        code, found, err = self.run_lint()
        self.assertEqual((code, found), (1, sorted(ADDED + [UNKNOWN])))
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertTrue(err.startswith("rigger: "), err)
        self.assertIn("rigger-module.json", err)
        self.assertNotIn("Traceback", err)

    def test_config_module_missing_leaves_the_defaults(self):
        tools = os.path.join(self.tmp, "core-copy", "tools")
        script = os.path.join(tools, "drift-lint", "drift_lint.py")
        os.makedirs(os.path.dirname(script))
        shutil.copyfile(SCRIPT, script)
        shutil.copytree(os.path.join(os.path.dirname(os.path.dirname(SCRIPT)), "assemble"),
                        os.path.join(tools, "assemble"), ignore=shutil.ignore_patterns("__pycache__", "tests"))
        # the core's manifests too: without them discover says the copied core is skipped
        core = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT)))
        for name in ("rigger-module.json", os.path.join(".claude-plugin", "plugin.json")):
            os.makedirs(os.path.dirname(os.path.join(os.path.dirname(tools), name)), exist_ok=True)
            shutil.copyfile(os.path.join(core, name), os.path.join(os.path.dirname(tools), name))
        shutil.copytree(os.path.join(core, "rules"), os.path.join(os.path.dirname(tools), "rules"))
        self.personal({"schema": 1, "drift_lint": {"remote_commands": ADDED}})
        # control: the copy reads config, and leaves no bytecode in the copied config module's folder
        self.assertEqual(self.run_lint(script), (1, [UNKNOWN], ""))
        self.assertFalse(os.path.exists(os.path.join(tools, "assemble", "__pycache__")))
        shutil.rmtree(os.path.join(tools, "assemble"))
        code, found, err = self.run_lint(script)
        self.assertEqual((code, found), (1, sorted(ADDED + [UNKNOWN])))
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertTrue(err.startswith("drift-lint: config not read ("), err)
        self.assertIn("default command lists only", err)

    def test_defaults_name_no_provider(self):
        root = repo_root()
        self.assertIsNotNone(root, "scripts/sweep.py not found above the tool: the host rule cannot be checked")
        sweep = load("sweep", os.path.join(root, "scripts", "sweep.py"))
        lint = load("drift_lint_defaults", SCRIPT)

        def hosts(names):
            return [name for name in names for rule, _, _ in sweep.scan_text(name, []) if rule == "host"]

        provider = "deploy-cloud." + sweep.TLDS[0]  # positive control: a provider host is flagged
        self.assertEqual(hosts([provider, "biome"]), [provider])
        self.assertEqual(hosts(sorted(lint.REMOTE_COMMANDS | lint.PROJECT_BINS)), [])
        # generic tool names only: the defaults D95 keeps; a bare provider word passes the host rule, so it is pinned
        self.assertEqual(lint.REMOTE_COMMANDS, {"htpasswd", "rrsync"})
        self.assertEqual(lint.PROJECT_BINS, {"wp-env", "biome", "astro", "knip", "mago", "vue-tsc", "playwright"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
