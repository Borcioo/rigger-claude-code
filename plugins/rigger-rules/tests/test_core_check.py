"""Tests for hooks/core_check.sh: one visible line when rigger-rules runs from its cache next to a rigger-core
that has no assembler, and silence everywhere else. Each row runs the hooks.json command through a real sh."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
LINE = b"Rigger rules not loaded: rigger-core is older than rigger-rules (run claude plugin update for rigger-core)\n"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def check_command():
    with open(HOOKS_JSON, encoding="utf-8") as handle:
        groups = json.load(handle)["hooks"]["SessionStart"]
    found = [(group, h) for group in groups for h in group["hooks"] if "core_check.sh" in h["command"]]
    assert len(found) == 1, found
    return found[0]


class CoreCheckTests(unittest.TestCase):
    def setUp(self):
        self.sh = find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")
        # A space and non-ASCII letters, as a user profile folder can hold.
        self.tmp = Path(tempfile.mkdtemp(prefix="core check zażółć "))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.tmp / "config dir"
        self.cache = self.config / "plugins" / "cache" / "rigger"
        self.rules = self.plugin_copy(self.cache / "rigger-rules" / "945ea046f764")

    def plugin_copy(self, root):
        """rigger-rules as the cache holds it: its hooks folder and manifest under a version folder."""
        shutil.copytree(str(PLUGIN / "hooks"), str(root / "hooks"))
        shutil.copytree(str(PLUGIN / ".claude-plugin"), str(root / ".claude-plugin"))
        return root

    def core(self, folder, assembler):
        (folder / "rules").mkdir(parents=True)
        if assembler:
            (folder / "tools" / "assemble").mkdir(parents=True)
            (folder / "tools" / "assemble" / "part.sh").write_bytes(b"#!/bin/sh\n")
        return folder

    def spelled(self, path, form):
        return Path(path).as_posix() if form == "posix" else str(path)

    def listing(self, entries, minified=False, form="posix"):
        """installed_plugins.json the way Claude Code writes it: version 2, raw non-ASCII."""
        plugins = {key: [{"scope": "user", "installPath": self.spelled(path, form), "version": Path(path).name}]
                   for key, path in entries.items()}
        data = {"version": 2, "plugins": plugins}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if minified else \
            json.dumps(data, ensure_ascii=False, indent=2)
        self.write_listing(text.encode("utf-8"))

    def write_listing(self, data, config=None):
        path = (config or self.config) / "plugins" / "installed_plugins.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_check(self, root=None, config="set"):
        _, handler = check_command()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env["HOME"] = str(self.home)
        if config == "set":
            env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PLUGIN_ROOT"] = Path(root or self.rules).as_posix()
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        proc = subprocess.run([self.sh, "-c", handler["command"]], input=b'{"source":"startup"}',
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(self.tmp), timeout=60)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        return proc.stdout

    def test_old_core_prints_one_visible_line(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        self.assertEqual(self.run_check(), LINE)

    def test_rows(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        new = self.core(self.cache / "rigger-core" / "945ea046f764", assembler=True)
        source = self.plugin_copy(self.tmp / "marketplace" / "plugins" / "rigger-rules")
        folder_core = self.core(self.tmp / "marketplace" / "plugins" / "rigger-core", assembler=False)
        deep = self.core(self.cache / "rigger-core" / "85be9b16363b" / "nested", assembler=False)
        extra = self.core(self.cache / "rigger-core-extra" / "1", assembler=False)
        rows = [
            ("core with the assembler", {"rigger-rules@rigger": self.rules, "rigger-core@rigger": new}, {}, b""),
            # A directory marketplace runs hooks from its source while installPath is a stale snapshot.
            ("rules runs from another folder than its installPath",
             {"rigger-rules@rigger": self.rules, "rigger-core@rigger": old}, {"root": source}, b""),
            ("no rigger-core entry", {"rigger-rules@rigger": self.rules}, {}, b""),
            ("a core folder named by its last element", {"rigger-rules@rigger": self.rules, "rigger-core@x": folder_core},
             {}, LINE),
            ("rigger-core three elements up is not the core", {"rigger-rules@rigger": self.rules, "rigger-core@x": deep},
             {}, b""),
            ("a plugin whose name only starts with rigger-core", {"rigger-rules@rigger": self.rules, "x@x": extra},
             {}, b""),
            ("minified one-line file", {"rigger-rules@rigger": self.rules, "rigger-core@rigger": old},
             {"minified": True}, LINE),
        ]
        for name, entries, options, expected in rows:
            with self.subTest(row=name):
                self.listing(entries, minified=options.get("minified", False))
                self.assertEqual(self.run_check(options.get("root")), expected)

    def test_many_other_plugins_stay_fast(self):
        # A spawn per listed plugin cost 6.3 s for 100 entries under Git Bash; only Rigger's entries may cost one.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        entries = {"rigger-rules@rigger": self.rules, "rigger-core@rigger": old}
        for index in range(100):
            other = self.cache / "other" / ("plugin-%d" % index) / "1.0.0"
            other.mkdir(parents=True)
            entries["plugin-%d@other" % index] = other
        self.listing(entries, form="native")
        started = time.monotonic()
        self.assertEqual(self.run_check(), LINE)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_one_current_core_entry_is_enough(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        new = self.core(self.cache / "rigger-core" / "945ea046f764", assembler=True)
        text = json.dumps({"version": 2, "plugins": {
            "rigger-rules@rigger": [{"scope": "user", "installPath": self.rules.as_posix()}],
            "rigger-core@rigger": [{"scope": "project", "installPath": old.as_posix()},
                                   {"scope": "user", "installPath": new.as_posix()}]}}, indent=2, ensure_ascii=False)
        self.write_listing(text.encode("utf-8"))
        self.assertEqual(self.run_check(), b"")

    def test_unreadable_listing_prints_nothing(self):
        self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.assertEqual(self.run_check(), b"", "missing file")
        for name, data in (("not JSON", b"{not json"), ("empty", b""), ("a list", b"[]")):
            with self.subTest(row=name):
                self.write_listing(data)
                self.assertEqual(self.run_check(), b"")

    def test_default_config_dir_is_under_home(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.config = self.home / ".claude"
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        self.assertEqual(self.run_check(config="unset"), LINE)

    @unittest.skipUnless(os.name == "nt", "a drive-letter path with backslashes is a Windows spelling")
    def test_windows_path_with_backslashes(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old}, form="windows")
        self.assertIn(b"\\\\", (self.config / "plugins" / "installed_plugins.json").read_bytes())
        self.assertEqual(self.run_check(), LINE)

    def test_wiring(self):
        group, handler = check_command()
        self.assertEqual(group, {"matcher": "startup|clear", "hooks": [
            {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh"', "timeout": 10}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
