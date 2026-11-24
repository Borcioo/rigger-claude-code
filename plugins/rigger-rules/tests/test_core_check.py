"""Tests for hooks/core_check.sh: one visible line when a rules plugin runs from its cache next to a rigger-core
that has no assembler, and silence everywhere else. Each row runs the plugin's own hooks.json command through a
real sh. The same script ships in every plugin of RULES_PLUGINS; tests/test_repo.py checks the copies are equal."""

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
PLUGINS = PLUGIN.parent
# Every plugin whose rules rigger-core prints: each warns for itself when it moved past the installed core.
RULES_PLUGINS = ("rigger-rules", "rigger-windows", "rigger-wp", "rigger-lessons", "rigger-talk")


def line(name):
    return ("Rigger rules not loaded for %s: rigger-core is older than %s (run claude plugin update rigger-core)\n"
            % (name, name)).encode("utf-8")


LINE = line("rigger-rules")


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def check_command(plugin=PLUGIN):
    with open(plugin / "hooks" / "hooks.json", encoding="utf-8") as handle:
        groups = json.load(handle)["hooks"]["SessionStart"]
    found = [(group, h) for group in groups for h in group["hooks"] if "core_check.sh" in h["command"]]
    assert len(found) == 1, found
    return found[0]


def realistic_listing(size, rigger):
    """(installed_plugins.json bytes, average bytes per other entry): `size` other plugins shaped like a live
    Windows listing, extra keys included, then the `rigger` entries ({key: installPath})."""
    plugins = {}
    home = "\\".join(("C:", "Users", "someone"))
    for index in range(size):
        market = "marketplace-%02d" % (index % 7)
        plugins["plugin-%03d@%s" % (index, market)] = [{
            "scope": "user",
            "installPath": "\\".join((home, ".claude", "plugins", "cache", market, "plugin-%03d" % index,
                                      "1.%d.0" % (index % 9))),
            "version": "1.%d.0" % (index % 9),
            "installedAt": "2026-11-01T10:%02d:00.000Z" % (index % 60),
            "lastUpdated": "2026-11-20T10:%02d:00.000Z" % (index % 60),
            "gitCommitSha": "%040x" % (index * 7919 + 1),
            "projectPath": "\\".join((home, "Projects", "project-%03d" % index)),
        }]
    others = len(json.dumps({"plugins": plugins}, indent=2).encode("utf-8"))
    for key, path in rigger.items():
        plugins[key] = [{"scope": "user", "installPath": path, "version": Path(path).name}]
    text = json.dumps({"version": 2, "plugins": plugins}, indent=2, ensure_ascii=False)
    return text.encode("utf-8"), others // size


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

    def plugin_copy(self, root, plugin=PLUGIN):
        """A plugin as the cache holds it: its hooks folder and manifest under a version folder."""
        shutil.copytree(str(plugin / "hooks"), str(root / "hooks"))
        shutil.copytree(str(plugin / ".claude-plugin"), str(root / ".claude-plugin"))
        return root

    def core(self, folder, assembler):
        (folder / "rules").mkdir(parents=True)
        if assembler:
            (folder / "tools" / "assemble").mkdir(parents=True)
            (folder / "tools" / "assemble" / "part.sh").write_bytes(b"#!/bin/sh\n")
        return folder

    def spelled(self, path, form):
        return Path(path).as_posix() if form == "posix" else str(path)

    def listing(self, entries, minified=False, form="posix", split=False):
        """installed_plugins.json the way Claude Code writes it: version 2, raw non-ASCII; `split` puts each
        installPath key, its colon and its value on lines of their own, with CRLF."""
        plugins = {key: [{"scope": "user", "installPath": self.spelled(path, form), "version": Path(path).name}]
                   for key, path in entries.items()}
        data = {"version": 2, "plugins": plugins}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if minified else \
            json.dumps(data, ensure_ascii=False, indent=2)
        if split:
            text = text.replace('"installPath": "', '"installPath"\r\n  :\r\n  "').replace("\n", "\r\n")
            text = text.replace("\r\r\n", "\r\n")
            self.assertEqual(text.count('"installPath"\r\n  :\r\n  "'), len(entries))
        self.write_listing(text.encode("utf-8"))

    def write_listing(self, data, config=None):
        path = (config or self.config) / "plugins" / "installed_plugins.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_check(self, root=None, config="set", plugin=PLUGIN, command=None, script=None, cwd=None):
        command = command or check_command(plugin)[1]["command"]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        if script is not None:
            env["CORE_CHECK_SCRIPT"] = script
        env["HOME"] = str(self.home)
        if config == "set":
            env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PLUGIN_ROOT"] = Path(root or self.rules).as_posix()
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        proc = subprocess.run([self.sh, "-c", command], input=b'{"source":"startup"}',
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd or self.tmp), timeout=60)
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
            # JSON allows any whitespace around the colon; the file is scanned as a whole, not line by line.
            ("key, colon and value on CRLF lines", {"rigger-rules@rigger": self.rules, "rigger-core@rigger": old},
             {"split": True}, LINE),
            ("own entry named by its last element", {"rigger-rules@x": source, "rigger-core@rigger": old},
             {"root": source}, LINE),
        ]
        for name, entries, options, expected in rows:
            with self.subTest(row=name):
                self.listing(entries, minified=options.get("minified", False), split=options.get("split", False))
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

    def test_a_long_listing_stays_fast_at_two_sizes(self):
        # A scan that copies the rest of the file per step looks fine at 100 entries and takes 3.4 s at 500:
        # two sizes show the growth. Entries as Claude Code writes them, with Windows paths on every platform.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        for size in (100, 500):
            with self.subTest(entries=size):
                text, per_entry = realistic_listing(size, {"rigger-rules@rigger": self.rules.as_posix(),
                                                           "rigger-core@rigger": old.as_posix()})
                self.assertTrue(400 <= per_entry <= 480, per_entry)
                self.write_listing(text)
                started = time.monotonic()
                out = self.run_check()
                self.assertLess(time.monotonic() - started, 2.0)
                self.assertEqual(out, LINE)

    def test_listing_cut_inside_the_core_value_prints_nothing(self):
        # Claude Code rewriting the file while another session starts: an unterminated value is no path.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        path = self.config / "plugins" / "installed_plugins.json"
        data = path.read_bytes()
        cut = data.index(b"85be9b16363b", data.index(b"rigger-core@rigger")) + 2
        path.write_bytes(data[:cut])
        self.assertEqual(self.run_check(), b"")

    def test_a_value_with_another_json_escape_is_skipped(self):
        # ó is not undone, so the value is skipped; read literally it would name a folder that does not exist.
        self.core(self.cache / "xó" / "rigger-core" / "945ea046f764", assembler=True)
        text = json.dumps({"version": 2, "plugins": {
            "rigger-rules@rigger": [{"installPath": self.rules.as_posix()}],
            "rigger-core@rigger": [{"installPath": (self.cache / "xó" / "rigger-core" / "945ea046f764").as_posix()}]},
        }, ensure_ascii=False).replace("xó", "x\\u00f3")
        self.write_listing(text.encode("utf-8"))
        self.assertIn(b"x\\u00f3", (self.config / "plugins" / "installed_plugins.json").read_bytes())
        self.assertEqual(self.run_check(), b"")

    @unittest.skipIf(os.name == "nt", "POSIX symlinks; Git Bash pwd -P does not resolve a Windows link the same way")
    def test_config_reached_through_a_symlink_prints_the_line(self):
        # A dotfiles ~/.claude that links elsewhere: the hook runs from the link spelling, installPath names the target.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        link = self.tmp / "linked config"
        link.symlink_to(self.config, target_is_directory=True)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        through_link = link / self.rules.relative_to(self.config)
        self.assertEqual(self.run_check(root=through_link), LINE)

    def test_the_assembler_path_it_probes_exists_in_rigger_core(self):
        # rigger-rules probes this path in whatever rigger-core is installed: renaming it in core breaks the check.
        self.assertTrue((PLUGIN.parent / "rigger-core" / "tools" / "assemble" / "part.sh").is_file())

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

    @unittest.skipUnless(os.name == "nt", "a script path written with backslashes is a Windows spelling")
    def test_backslash_script_path(self):
        # Claude Code may expand the hook command with backslashes on Windows: the script still finds its folder.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        script = str(self.rules / "hooks" / "core_check.sh")
        self.assertNotIn("/", script)
        # The path travels in the environment: Git Bash re-parses quotes in its own command line.
        out = self.run_check(command='sh "$CORE_CHECK_SCRIPT" rigger-rules', script=script)
        self.assertEqual(out, LINE)

    def test_bare_script_name_run_from_its_folder(self):
        # No separator in the path: the script's folder is the current one.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        out = self.run_check(command="sh core_check.sh rigger-rules", cwd=self.rules / "hooks")
        self.assertEqual(out, LINE)

    def test_wiring(self):
        # startup and clear start a session with no rules; compact repeats the line the summary dropped.
        for name in RULES_PLUGINS:
            with self.subTest(plugin=name):
                group, _ = check_command(PLUGINS / name)
                self.assertEqual(group, {"matcher": "startup|clear|compact", "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh" ' + name,
                     "timeout": 10}]})

    def test_each_rules_plugin_speaks_for_itself(self):
        # Through each plugin's own copy and hooks.json command, installed in a git cache next to the core.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        new = self.core(self.cache / "rigger-core" / "945ea046f764", assembler=True)
        for name in RULES_PLUGINS:
            cached = self.plugin_copy(self.cache / name / "0a1b2c3d4e5f", PLUGINS / name)
            source = self.plugin_copy(self.tmp / "marketplace" / "plugins" / name, PLUGINS / name)
            rows = [
                ("old core", old, cached, line(name)),
                ("current core", new, cached, b""),
                # A directory marketplace runs hooks from its source; installPath is a snapshot in the cache.
                ("directory marketplace", old, source, b""),
            ]
            for row, core, root, expected in rows:
                with self.subTest(plugin=name, row=row):
                    self.listing({name + "@rigger": cached, "rigger-core@rigger": core})
                    self.assertEqual(self.run_check(root, plugin=PLUGINS / name), expected)

    def test_two_moved_plugins_print_two_lines(self):
        # Claude Code merges identical hook text; each line names its plugin, so both reach the session.
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        windows = self.plugin_copy(self.cache / "rigger-windows" / "945ea046f764", PLUGINS / "rigger-windows")
        self.listing({"rigger-rules@rigger": self.rules, "rigger-windows@rigger": windows, "rigger-core@rigger": old})
        outputs = [self.run_check(), self.run_check(windows, plugin=PLUGINS / "rigger-windows")]
        self.assertEqual(outputs, [line("rigger-rules"), line("rigger-windows")])

    def test_no_plugin_name_prints_nothing(self):
        old = self.core(self.cache / "rigger-core" / "85be9b16363b", assembler=False)
        self.listing({"rigger-rules@rigger": self.rules, "rigger-core@rigger": old})
        self.assertEqual(self.run_check(), LINE)
        for row, argument in (("missing", ""), ("empty", ' ""')):
            with self.subTest(row=row):
                command = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh"' + argument
                self.assertEqual(self.run_check(command=command), b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
