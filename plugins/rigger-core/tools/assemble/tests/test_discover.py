"""Tests for discover.py: modules from Claude Code state, RIGGER_MODULE_DIRS and the config folder."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import discover  # noqa: E402

MARKET = "rigger"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    path.write_bytes(body)


def make_plugin(root, name, manifest=True, layer="general", marker=None):
    write_json(root / ".claude-plugin" / "plugin.json", {"name": name})
    if manifest:
        data = {"schema": 1, "name": name, "layer": layer, "fills": None, "rules": []}
        if marker:
            data["triggers"] = [{"id": name + "." + marker, "before": marker, "load": ["x:y"]}]
        write_json(root / discover.MANIFEST, data)
    return root


def same(a, b):
    return os.path.samefile(str(a), str(b))


class Layout:
    """A temp config dir, project dir, directory marketplace and installed copies, built per test."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.config = tmp / "config"
        self.project = tmp / "project"
        self.market = tmp / "market"
        self.core = make_plugin(tmp / "self" / "rigger-core", "rigger-core")
        self.config.mkdir()
        self.project.mkdir()
        self.installed = {}
        self.listed = []
        self.environ = {}
        self.known = {MARKET: {"source": {"source": "directory", "path": str(self.market)},
                               "installLocation": str(self.market)}}

    def plugin(self, name, manifest=True, scope="user", marker=None, **entry):
        """A plugin in the directory marketplace with an installed (stale) cache copy."""
        source = make_plugin(self.market / "plugins" / name, name, manifest, marker=marker)
        cache = make_plugin(self.config / "plugins" / "cache" / MARKET / name / "unknown", name, manifest,
                            marker="stale")
        self.listed.append({"name": name, "source": "./plugins/" + name})
        record = {"scope": scope, "installPath": str(cache), "version": "unknown"}
        record.update(entry)
        self.installed.setdefault(name + "@" + MARKET, []).append(record)
        return source

    def enable(self, where, **flags):
        files = {"user": self.config / "settings.json",
                 "user-local": self.config / "settings.local.json",
                 "project": self.project / ".claude" / "settings.json",
                 "local": self.project / ".claude" / "settings.local.json"}
        write_json(files[where], {"enabledPlugins": {k.replace("_", "-") + "@" + MARKET: v for k, v in flags.items()}})

    def run(self):
        write_json(self.market / ".claude-plugin" / "marketplace.json", {"name": MARKET, "plugins": self.listed})
        write_json(self.config / "plugins" / "known_marketplaces.json", self.known)
        if self.installed is not None and not (self.config / "plugins" / "installed_plugins.json").exists():
            write_json(self.config / "plugins" / "installed_plugins.json", {"version": 2, "plugins": self.installed})
        return discover.discover(self.config, self.project, self.environ, self.core)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="discover "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.layout = Layout(self.tmp)

    def names(self, found):
        return [module.name for module in found.modules]

    def test_s4_scenarios(self):
        # Known answers from the real CLI: docs/superpowers/spikes/phase-1/S4.md, rows 0-14.
        rows = [
            (0, [], set()),
            (1, [("user", {"s4_a": True})], {"s4-a"}),
            (2, [("user-local", {"s4_b": True})], set()),
            (3, [("project", {"s4_c": True})], {"s4-c"}),
            (4, [("local", {"s4_d": True})], {"s4-d"}),
            (5, [("user", {"s4_e": True}), ("project", {"s4_e": False})], set()),
            (6, [("project", {"s4_f": True}), ("local", {"s4_f": False})], set()),
            (7, [("user", {"s4_g": True})], {"s4-g"}),
            (8, [], {"s4-h"}),
            (9, [("project", {"s4_g": True})], {"s4-g"}),
            (10, [("user", {"s4_e": False}), ("project", {"s4_e": True})], {"s4-e"}),
            (11, [("user", {"s4_b": False}), ("user-local", {"s4_b": True})], set()),
            (12, [("project", {"s4_f": False}), ("local", {"s4_f": True})], {"s4-f"}),
            (13, [("user", {"s4_d": True}), ("local", {"s4_d": False})], set()),
            (14, [("user", {"s4_d": False}), ("local", {"s4_d": True})], {"s4-d"}),
        ]
        for number, settings, expected in rows:
            with self.subTest(scenario=number):
                tmp = self.tmp / ("s%d" % number)
                tmp.mkdir()
                layout = Layout(tmp)
                for letter in "abcdef":
                    layout.plugin("s4-" + letter)
                layout.plugin("s4-g", scope="project", projectPath=str(tmp / "elsewhere"))
                if number == 8:
                    layout.environ[discover.MODULE_DIRS] = str(make_plugin(tmp / "dev" / "s4-h", "s4-h"))
                for where, flags in settings:
                    layout.enable(where, **flags)
                found = layout.run()
                self.assertEqual({name for name in found.enabled if name.startswith("s4-")}, expected)
                self.assertEqual({name for name in self.names(found) if name.startswith("s4-")}, expected)
                self.assertEqual(found.notices, [])

    def test_clean_layout_gives_no_notice(self):
        rules = self.layout.plugin("rigger-rules")
        self.layout.plugin("superpowers", manifest=False)
        self.layout.installed["rigger-core@" + MARKET] = [{"scope": "user", "installPath": str(self.layout.core)}]
        self.layout.enable("user", rigger_rules=True, superpowers=True, rigger_core=True)
        self.layout.environ[discover.MODULE_DIRS] = os.pathsep.join([str(self.layout.core), str(rules)])
        found = self.layout.run()
        self.assertEqual(found.notices, [])
        self.assertEqual(self.names(found), ["rigger-core", "rigger-rules"])
        self.assertEqual(found.enabled, frozenset({"rigger-core", "rigger-rules", "superpowers"}))
        self.assertEqual(found.modules[0].origin, "self")

    def test_module_dirs_override_installed_with_notice(self):
        self.layout.plugin("rigger-wp")
        self.layout.enable("user", rigger_wp=True)
        dev = make_plugin(self.tmp / "dev" / "rigger-wp", "rigger-wp", marker="dev")
        self.layout.environ[discover.MODULE_DIRS] = str(dev)
        found = self.layout.run()
        wp = [module for module in found.modules if module.name == "rigger-wp"]
        self.assertEqual(len(wp), 1)
        self.assertTrue(same(wp[0].root, dev))
        self.assertEqual(wp[0].origin, "dirs")
        self.assertEqual(len(found.notices), 1)
        self.assertIn("rigger-wp", found.notices[0])
        self.assertIn(discover.MODULE_DIRS, found.notices[0])

    def test_module_dirs_core_replaces_self_with_notice(self):
        dev = make_plugin(self.tmp / "dev" / "rigger-core", "rigger-core", marker="dev")
        self.layout.environ[discover.MODULE_DIRS] = str(dev)
        found = self.layout.run()
        self.assertTrue(same(found.modules[0].root, dev))
        self.assertEqual(found.modules[0].origin, "dirs")
        self.assertEqual(len(found.notices), 1)
        self.assertIn(discover.MODULE_DIRS, found.notices[0])

    def test_module_dirs_entry_not_a_dir_or_without_manifest(self):
        plain = make_plugin(self.tmp / "plain", "plain", manifest=False)
        missing = self.tmp / "gone"
        relative = "relative" + os.sep + "dir"
        make_plugin(self.tmp / "relative" / "dir", "relative-dir")
        self.layout.environ[discover.MODULE_DIRS] = os.pathsep.join([str(missing), "", str(plain), relative])
        cwd = os.getcwd()
        os.chdir(str(self.tmp))
        try:
            found = self.layout.run()
        finally:
            os.chdir(cwd)
        self.assertEqual(self.names(found), ["rigger-core"])
        self.assertEqual(len(found.notices), 3, found.notices)
        self.assertIn("gone", found.notices[0])
        self.assertIn("plain", found.notices[1])
        self.assertIn("relative", found.notices[2])

    def test_module_dirs_with_space_and_non_ascii(self):
        root = make_plugin(self.tmp / "za\u017c\u00f3\u0142\u0107 \u0105" / "rigger-talk", "rigger-talk")
        other = make_plugin(self.tmp / "second one" / "rigger-wp", "rigger-wp")
        self.layout.environ[discover.MODULE_DIRS] = os.pathsep.join([str(root), str(other)])
        found = self.layout.run()
        talk = [module for module in found.modules if module.name == "rigger-talk"][0]
        self.assertEqual(str(talk.root), str(root))
        self.assertIn("rigger-wp", self.names(found))
        self.assertEqual(found.notices, [])
        self.assertIn("rigger-talk", found.enabled)

    def test_wrong_shapes_give_one_notice_each(self):
        installed_path = self.layout.config / "plugins" / "installed_plugins.json"

        def broken_installed(layout):
            write_json(layout.config / "plugins" / "installed_plugins.json", b"{not json")

        def plugins_list(layout):
            write_json(layout.config / "plugins" / "installed_plugins.json", {"plugins": []})

        def no_install_path(layout):
            layout.plugin("rigger-talk")
            layout.installed["rigger-talk@" + MARKET][0].pop("installPath")
            layout.enable("user", rigger_wp=True, rigger_talk=True)

        def entries_object(layout):
            layout.installed["rigger-talk@" + MARKET] = {"installPath": "x"}
            layout.enable("user", rigger_wp=True, rigger_talk=True)

        def install_path_gone(layout):
            layout.plugin("rigger-talk")
            layout.known = {}
            layout.installed["rigger-talk@" + MARKET][0]["installPath"] = str(layout.tmp / "gone")
            layout.enable("user", rigger_wp=True, rigger_talk=True)

        def enabled_list(layout):
            write_json(layout.project / ".claude" / "settings.json", {"enabledPlugins": ["rigger-talk@rigger"]})

        def enabled_yes(layout):
            layout.plugin("rigger-talk")
            write_json(layout.project / ".claude" / "settings.json",
                       {"enabledPlugins": {"rigger-talk@rigger": "yes"}})

        def settings_string(layout):
            write_json(layout.project / ".claude" / "settings.local.json", b'"enabledPlugins"')

        def settings_broken(layout):
            write_json(layout.project / ".claude" / "settings.local.json", b"{")

        def settings_deep(layout):
            write_json(layout.project / ".claude" / "settings.json", b"[" * 100000 + b"]" * 100000)

        def known_broken(layout):
            write_json(layout.config / "plugins" / "known_marketplaces.json", b"[")

        def known_list(layout):
            write_json(layout.config / "plugins" / "known_marketplaces.json", b"[]")

        def listing_broken(layout):
            write_json(layout.market / ".claude-plugin" / "marketplace.json", b"{")

        rows = [
            ("installed not JSON", broken_installed, False),
            ("plugins as a list", plugins_list, False),
            ("installPath missing", no_install_path, True),
            ("entries not a list", entries_object, True),
            ("installPath gone", install_path_gone, True),
            ("enabledPlugins a list", enabled_list, True),
            ("enabledPlugins value yes", enabled_yes, True),
            ("settings a JSON string", settings_string, True),
            ("settings not JSON", settings_broken, True),
            ("settings nested 100000", settings_deep, True),
            ("known marketplaces not JSON", known_broken, True),
            ("known marketplaces a list", known_list, True),
            ("marketplace listing not JSON", listing_broken, True),
        ]
        for label, breaker, wp_kept in rows:
            with self.subTest(label):
                tmp = self.tmp / label.replace(" ", "-")
                tmp.mkdir()
                layout = Layout(tmp)
                layout.plugin("rigger-wp")
                layout.enable("user", rigger_wp=True)
                dev = make_plugin(tmp / "dev" / "rigger-dev", "rigger-dev")
                layout.environ[discover.MODULE_DIRS] = str(dev)
                breaker(layout)
                real_run = layout.run
                if label in ("known marketplaces not JSON", "known marketplaces a list",
                             "marketplace listing not JSON"):
                    def run_keep_known():
                        if label != "marketplace listing not JSON":
                            write_json(layout.market / ".claude-plugin" / "marketplace.json",
                                       {"name": MARKET, "plugins": layout.listed})
                        else:
                            write_json(layout.config / "plugins" / "known_marketplaces.json", layout.known)
                        write_json(layout.config / "plugins" / "installed_plugins.json",
                                   {"version": 2, "plugins": layout.installed})
                        return discover.discover(layout.config, layout.project, layout.environ, layout.core)
                    found = run_keep_known()
                else:
                    found = real_run()
                self.assertEqual(len(found.notices), 1, found.notices)
                self.assertTrue(found.notices[0].startswith("rigger: "), found.notices[0])
                names = self.names(found)
                self.assertEqual(names[0], "rigger-core")
                self.assertIn("rigger-dev", names)
                self.assertEqual("rigger-wp" in names, wp_kept)
                self.assertNotIn("rigger-talk", names)
        self.assertFalse(installed_path.exists())

    def test_invalid_manifest_skips_module_with_reason(self):
        root = self.layout.plugin("rigger-wp")
        write_json(root / discover.MANIFEST, {"schema": 2, "name": "rigger-wp"})
        self.layout.enable("user", rigger_wp=True)
        found = self.layout.run()
        self.assertEqual(self.names(found), ["rigger-core"])
        self.assertEqual(len(found.notices), 1)
        self.assertTrue(found.notices[0].startswith("rigger: module rigger-wp skipped: schema 2"), found.notices[0])

    def test_order_core_first_then_marketplace_order(self):
        for name in ("rigger-wp", "rigger-rules", "rigger-lessons"):
            self.layout.plugin(name)
        self.layout.environ[discover.MODULE_DIRS] = os.pathsep.join(
            [str(make_plugin(self.tmp / "dev" / n, n)) for n in ("zeta-dev", "alpha-dev")])
        self.layout.enable("user", rigger_wp=True, rigger_rules=True, rigger_lessons=True)
        found = self.layout.run()
        self.assertEqual(self.names(found), ["rigger-core", "rigger-wp", "rigger-rules", "rigger-lessons",
                                             "alpha-dev", "zeta-dev"])

    def test_directory_marketplace_root(self):
        # S3: a directory marketplace's hooks run from its source path; installPath lags.
        source = self.layout.plugin("rigger-rules", marker="fresh")
        self.layout.enable("user", rigger_rules=True)
        found = self.layout.run()
        rules = [module for module in found.modules if module.name == "rigger-rules"][0]
        self.assertTrue(same(rules.root, source))
        self.assertEqual(rules.triggers[0].id, "rigger-rules.fresh")
        self.assertEqual(rules.marketplace, MARKET)
        self.assertEqual(found.notices, [])

    def test_module_root_string_form(self):
        # A Windows-shaped stored path, built from parts; the drive letter keeps the case it was typed in.
        drive = "q" + ":"
        stored = "\\".join([drive, "Work", "Repo"])
        known = {"m": {"source": {"source": "directory", "path": stored}}}
        listing = {"plugins": [{"name": "p", "source": "./plugins/p"}]}
        entry = {"installPath": "\\".join([drive, "cfg", "plugins", "cache", "m", "p", "unknown"])}
        root = discover.module_root(entry, known, "p@m", lambda _: listing)
        expected = "/".join([drive, "Work", "Repo", "plugins", "p"]) if os.name == "nt" else stored + "/plugins/p"
        self.assertEqual(discover.root_text(root), expected)
        git = {"m": {"source": {"source": "git", "url": "https://example.invalid/m.git"}}}
        self.assertEqual(discover.module_root(entry, git, "p@m", lambda _: listing), Path(entry["installPath"]))
        other = {"m": {"source": {"source": "file", "path": stored}}}
        self.assertEqual(discover.module_root(entry, other, "p@m", lambda _: listing), Path(entry["installPath"]))
        outside = {"plugins": [{"name": "p", "source": "../elsewhere"}]}
        self.assertEqual(discover.module_root(entry, known, "p@m", lambda _: outside), Path(entry["installPath"]))

    def test_eval_module_dirs_file(self):
        # E1 branch B: a staged rigger-core lists the staged roots in .rigger-module-dirs.
        staged = make_plugin(self.tmp / "staged" / "rigger-rules", "rigger-rules")
        (self.layout.core / discover.MODULE_DIRS_FILE).write_bytes(
            (str(self.layout.core) + "\r\n" + str(staged) + "\n\n").encode("utf-8"))
        found = self.layout.run()
        self.assertEqual(self.names(found), ["rigger-core", "rigger-rules"])
        self.assertEqual(found.notices, [])

    def test_personal_module(self):
        self.assertIsNone(discover.personal_module(self.layout.config))
        (self.layout.config / "rigger" / "rules.d").mkdir(parents=True)
        personal = discover.personal_module(self.layout.config)
        self.assertEqual((personal.name, personal.layer, personal.origin), ("personal", "personal", "personal"))
        found = self.layout.run()
        self.assertEqual(self.names(found), ["rigger-core", "personal"])


if __name__ == "__main__":
    unittest.main()
