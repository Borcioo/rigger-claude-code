"""Tests for tools/doctor/doctor.sh: the marketplace, plugin and superpowers rows and files that are not valid JSON.
Stubs, fixtures and the run helper are in doctor_sh_harness.py."""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_sh_harness import (  # noqa: E402
    ASSEMBLER_OK,
    DoctorShCase,
    PooledSuite,
    DRIVE,
    PLUGIN,
    REPO_PLUGINS,
    REQUIRED,
    STAMP,
    VERSION,
    WORKTREE_ROOT,
    dump,
    entry,
    sh_path,
)


class DoctorShTests(DoctorShCase):
    # -- marketplace ----------------------------------------------------------------------------

    def test_marketplace_from_github_ok(self):
        self.good_config()
        result = self.run_doctor()
        self.assertRow(result, "marketplace", "OK", "Borcioo/rigger-claude-code")
        self.assertEqual(result.checks["marketplace"][2], sh_path(self.config) + "/plugins/known_marketplaces.json")

    def test_marketplace_from_a_directory_ok(self):
        markets = self.good_marketplaces()
        folder = DRIVE + r"\work\claude harness"
        markets["rigger"] = {
            "source": {"source": "directory", "path": folder},
            "installLocation": folder,
            "lastUpdated": STAMP,
        }
        self.write_config(self.good_settings(), self.good_installed(), markets)
        self.assertRow(self.run_doctor(), "marketplace", "OK", folder)

    def test_marketplace_absent(self):
        markets = self.good_marketplaces()
        del markets["rigger"]
        self.write_config(self.good_settings(), self.good_installed(), markets)
        result = self.run_doctor()
        self.assertRow(result, "marketplace", "MISSING")
        self.assertEqual(result.fixes["marketplace"], ["claude plugin marketplace add Borcioo/rigger-claude-code"])

    def test_marketplace_file_absent(self):
        self.write_config(self.good_settings(), self.good_installed(), None)
        self.assertRow(self.run_doctor(), "marketplace", "MISSING")

    def test_marketplace_entry_of_the_wrong_shape(self):
        for value in ("Borcioo/rigger-claude-code", ["Borcioo/rigger-claude-code"], 5):
            with self.subTest(value=value):
                markets = self.good_marketplaces()
                markets["rigger"] = value
                self.write_config(None, None, markets)
                self.assertRow(self.run_doctor(), "marketplace", "MISSING")

    # -- plugins ----------------------------------------------------------------------------------

    def test_plugins_installed_and_enabled_ok(self):
        self.good_config()
        result = self.run_doctor()
        for name in REQUIRED:
            self.assertRow(result, "plugin " + name, "OK", VERSION)
        self.assertNotIn("plugin rigger-windows", result.checks)

    def test_plugin_not_installed(self):
        installed = self.good_installed()
        del installed["plugins"]["rigger-rules@rigger"]
        self.write_config(self.good_settings(), installed, self.good_marketplaces())
        result = self.run_doctor()
        self.assertRow(result, "plugin rigger-rules", "MISSING")
        self.assertEqual(result.fixes["plugin rigger-rules"], ["claude plugin install rigger-rules@rigger"])

    def test_plugin_disabled(self):
        settings = self.good_settings()
        settings["enabledPlugins"]["rigger-lessons@rigger"] = False
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        result = self.run_doctor()
        self.assertRow(result, "plugin rigger-lessons", "WARN", VERSION + ", disabled")
        self.assertEqual(result.fixes["plugin rigger-lessons"], ["claude plugin enable rigger-lessons@rigger"])

    def test_plugin_without_an_enabled_plugins_key(self):
        for case in ("no key for it", "no enabledPlugins", "enabledPlugins is a list"):
            with self.subTest(case=case):
                settings = self.good_settings()
                if case == "no key for it":
                    del settings["enabledPlugins"]["rigger-core@rigger"]
                elif case == "no enabledPlugins":
                    del settings["enabledPlugins"]
                else:
                    settings["enabledPlugins"] = ["rigger-core@rigger"]
                self.write_config(settings, self.good_installed(), self.good_marketplaces())
                result = self.run_doctor()
                self.assertRow(result, "plugin rigger-core", "WARN", VERSION + ", not in enabledPlugins")
                self.assertEqual(result.fixes["plugin rigger-core"], ["claude plugin enable rigger-core@rigger"])

    def test_plugin_list_takes_the_first_user_entry(self):
        installed = self.good_installed()
        installed["plugins"]["rigger-core@rigger"] = [
            entry("0local00", scope="local", project="/work/project one"),
            entry("1user111"),
            entry("2user222"),
        ]
        self.write_config(self.good_settings(), installed, self.good_marketplaces())
        self.assertRow(self.run_doctor(), "plugin rigger-core", "OK", "1user111")

    def test_plugin_installed_only_for_a_project(self):
        for scope in ("local", "project"):
            with self.subTest(scope=scope):
                installed = self.good_installed()
                installed["plugins"]["rigger-core@rigger"] = [entry("0local00", scope=scope, project="/work/project one")]
                self.write_config(self.good_settings(), installed, self.good_marketplaces())
                result = self.run_doctor()
                self.assertRow(result, "plugin rigger-core", "WARN", "installed only for /work/project one (%s scope)" % scope)
                self.assertEqual(result.fixes["plugin rigger-core"], ["claude plugin install rigger-core@rigger"])

    def test_plugin_entry_as_one_object(self):
        installed = self.good_installed()
        installed["plugins"]["rigger-core@rigger"] = entry("9object9")
        self.write_config(self.good_settings(), installed, self.good_marketplaces())
        self.assertRow(self.run_doctor(), "plugin rigger-core", "OK", "9object9")

    def test_plugin_entry_of_the_wrong_shape(self):
        for value in (5, "1a2b3c", [5], [{"scope": "user"}], [{"scope": "user", "version": 5}]):
            with self.subTest(value=value):
                installed = self.good_installed()
                installed["plugins"]["rigger-core@rigger"] = value
                self.write_config(self.good_settings(), installed, self.good_marketplaces())
                self.assertRow(self.run_doctor(), "plugin rigger-core", "MISSING")

    def test_installed_file_absent(self):
        self.write_config(self.good_settings(), None, self.good_marketplaces())
        result = self.run_doctor()
        for name in REQUIRED:
            self.assertRow(result, "plugin " + name, "MISSING")

    def test_other_rigger_plugin_reported_when_installed(self):
        settings = self.good_settings()
        installed = self.good_installed()
        settings["enabledPlugins"]["rigger-wp@rigger"] = True
        installed["plugins"]["rigger-wp@rigger"] = [entry("7wp77777")]
        self.write_config(settings, installed, self.good_marketplaces())
        result = self.run_doctor()
        self.assertRow(result, "plugin rigger-wp", "OK", "7wp77777")
        self.assertEqual(result.order.index("plugin rigger-wp"), result.order.index("plugin rigger-lessons") + 1)

    def test_other_rigger_plugin_absent_gives_no_row(self):
        self.good_config()
        result = self.run_doctor()
        self.assertEqual([name for name in result.checks if name.startswith("plugin ")], ["plugin " + n for n in REQUIRED])

    def test_windows_requires_rigger_windows(self):
        self.good_config(windows=False)
        result = self.run_doctor("MINGW64_NT-10.0-26100")
        self.assertRow(result, "plugin rigger-windows", "MISSING")
        self.assertEqual(result.fixes["plugin rigger-windows"], ["claude plugin install rigger-windows@rigger"])

    def test_windows_with_rigger_windows_ok(self):
        self.good_config(windows=True)
        self.assertRow(self.run_doctor("MINGW64_NT-10.0-26100"), "plugin rigger-windows", "OK", VERSION)

    def test_one_line_json_reads_the_same(self):
        settings = self.good_settings()
        settings["enabledPlugins"]["rigger-lessons@rigger"] = False
        installed = self.good_installed()
        installed["plugins"]["rigger-core@rigger"] = [
            entry("0local00", scope="local", project="/work/project one"),
            entry("1user111"),
        ]
        del installed["plugins"]["rigger-rules@rigger"]
        self.stub_python("python3")
        rows = {}
        for style in ("pretty", "oneline"):
            self.write_config(settings, installed, self.good_marketplaces(), style=style)
            result = self.run_doctor()
            rows[style] = (result.checks, result.fixes)
        self.assertEqual(rows["oneline"], rows["pretty"])
        checks = rows["oneline"][0]
        self.assertEqual(checks["plugin rigger-core"][:2], ("OK", "1user111"))
        self.assertEqual(checks["plugin rigger-rules"][0], "MISSING")
        self.assertEqual(checks["plugin rigger-lessons"][:2], ("WARN", VERSION + ", disabled"))
        self.assertEqual(checks["autoCompactWindow"][:2], ("OK", "533000"))
        self.assertEqual(checks["worktree-root"][:2], ("OK", WORKTREE_ROOT))

    def test_settings_with_crlf_and_bom(self):
        for crlf, bom in ((True, False), (False, True), (True, True)):
            with self.subTest(crlf=crlf, bom=bom):
                self.write_config(self.good_settings(), self.good_installed(), self.good_marketplaces(), crlf=crlf, bom=bom)
                result = self.run_doctor()
                self.assertRow(result, "autoCompactWindow", "OK", "533000")
                self.assertRow(result, "worktree-root", "OK", WORKTREE_ROOT)
                self.assertRow(result, "plugin rigger-core", "OK", VERSION)

    def test_hostile_plugin_key_reaches_no_row(self):
        hostile = "rigger-x$(touch${IFS}PWNED);id"
        installed = self.good_installed()
        installed["plugins"][hostile + "@rigger"] = [entry(VERSION)]
        self.write_config(self.good_settings(), installed, self.good_marketplaces())
        result = self.run_doctor()
        self.assertEqual([cmd for cmds in result.fixes.values() for cmd in cmds if "PWNED" in cmd], [], result.stdout)
        self.assertNotIn("PWNED", result.stdout)
        self.assertEqual([str(p) for top in (self.tmp, PLUGIN) for p in top.rglob("PWNED")], [])

    def test_duplicate_key_reads_the_last_value(self):
        settings = dump(self.good_settings(), "pretty")
        one = '"rigger-lessons@rigger": true'
        self.assertEqual(settings.count(one), 1)
        cases = (
            ("value", settings.replace(one, one + ',\n    "rigger-lessons@rigger": false'), "rigger-lessons", ", disabled"),
            ("object", settings.rstrip()[:-1].rstrip() + ',\n  "enabledPlugins": {}\n}\n', "rigger-core", ", not in enabledPlugins"),
        )
        for case, text, name, detail in cases:
            with self.subTest(case=case):
                self.write_file(self.config / "settings.json", text)
                self.write_config(None, self.good_installed(), self.good_marketplaces())
                self.assertEqual(json.loads(text)["enabledPlugins"].get(name + "@rigger"), None if case == "object" else False)
                self.assertRow(self.run_doctor(), "plugin " + name, "WARN", VERSION + detail)

    def test_key_with_a_slash_never_matches_a_lookup_path(self):
        settings = self.good_settings()
        del settings["enabledPlugins"]["rigger-core@rigger"]
        settings["enabledPlugins/rigger-core@rigger"] = True
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        result = self.run_doctor()
        self.assertNotIn("settings-file", result.checks, result.stdout)
        self.assertRow(result, "plugin rigger-core", "WARN", VERSION + ", not in enabledPlugins")

    def test_valid_settings_with_a_slash_in_a_key_show_no_settings_file_row(self):
        settings = self.good_settings()
        settings["modelOverrides"] = {"vendor/model": "x"}
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        result = self.run_doctor()
        self.assertNotIn("settings-file", result.checks, result.stdout)

    # -- files that are not valid JSON --------------------------------------------------------------

    def check_invalid_file(self, relative, row, dependents, kernel="Linux"):
        """Writes a good config with a trailing comma in `relative`; expects one WARN row named `row`, the dependent
        rows WARN as unknown, no FIX for any of them, and every other row OK."""
        windows = kernel != "Linux"
        self.good_config(windows=windows)
        self.stub_git()
        self.stub_python("py" if windows else "python3")
        target = self.config / relative
        text = target.read_text(encoding="utf-8")
        self.write_file(target, text.rstrip()[:-1].rstrip() + ",\n}\n")
        with self.assertRaises(ValueError):
            json.loads(target.read_text(encoding="utf-8"))
        result = self.run_doctor(kernel)
        path = sh_path(self.config) + "/" + relative
        self.assertEqual(result.checks.get(row), ("WARN", path + " is not valid JSON", "parsed by doctor.sh"), result.stdout)
        for name in dependents:
            self.assertRow(result, name, "WARN", "unknown: %s is not valid JSON" % path)
        others = {name: status for name, (status, _, _) in result.checks.items() if name not in dependents + (row,)}
        self.assertEqual(set(others.values()), {"OK"}, result.stdout)
        self.assertEqual(result.fixes, {}, result.stdout)

    def test_settings_not_valid_json(self):
        plugins = tuple("plugin " + name for name in REQUIRED) + ("superpowers",)
        self.check_invalid_file("settings.json", "settings-file", plugins + ("autoCompactWindow", "worktree-root"))

    def test_settings_not_valid_json_on_windows(self):
        plugins = tuple("plugin " + name for name in REQUIRED + ("rigger-windows",)) + ("superpowers",)
        rows = plugins + ("autoCompactWindow", "powershell-tool", "worktree-root")
        self.check_invalid_file("settings.json", "settings-file", rows, kernel="MINGW64_NT-10.0-26100")

    def test_installed_plugins_not_valid_json(self):
        plugins = tuple("plugin " + name for name in REQUIRED) + ("superpowers",)
        self.check_invalid_file("plugins/installed_plugins.json", "installed-plugins-file", plugins)

    def test_marketplaces_not_valid_json(self):
        self.check_invalid_file("plugins/known_marketplaces.json", "marketplaces-file", ("marketplace",))

    # -- superpowers ------------------------------------------------------------------------------

    def test_superpowers_installed_and_enabled_ok(self):
        self.good_config()
        self.assertRow(self.run_doctor(), "superpowers", "OK", "5.0.7")

    def test_superpowers_absent_warns_with_both_commands(self):
        installed = self.good_installed()
        del installed["plugins"]["superpowers@superpowers-marketplace"]
        self.write_config(self.good_settings(), installed, self.good_marketplaces())
        result = self.run_doctor()
        self.assertRow(result, "superpowers", "WARN")
        self.assertEqual(
            result.fixes["superpowers"],
            ["claude plugin marketplace add obra/superpowers-marketplace", "claude plugin install superpowers@superpowers-marketplace"],
        )

    def test_superpowers_disabled_warns(self):
        settings = self.good_settings()
        settings["enabledPlugins"]["superpowers@superpowers-marketplace"] = False
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        result = self.run_doctor()
        self.assertRow(result, "superpowers", "WARN", "5.0.7, disabled")
        self.assertEqual(result.fixes["superpowers"], ["claude plugin enable superpowers@superpowers-marketplace"])


    # -- rules assembly (assemble.py --doctor through a real interpreter) ------------------------

    def run_assembly(self, *dirs, kernel=None):
        """doctor.sh with this test's own Python and RIGGER_MODULE_DIRS naming the repo's rigger-rules and `dirs`."""
        roots = [str(REPO_PLUGINS / "rigger-rules")] + [str(d) for d in dirs]
        kernel = kernel or ("MINGW64_NT-10.0-26100" if os.name == "nt" else "Linux")
        return self.run_doctor(kernel, path=self.real_python_path(), env={"RIGGER_MODULE_DIRS": os.pathsep.join(roots)})

    def rows(self, result, prefix):
        return {name: row for name, row in result.checks.items() if name.startswith(prefix)}

    def test_clean_layout_gives_rules_assembly_ok(self):
        result = self.run_assembly()
        self.assertRow(result, "rules-assembly", "OK")
        self.assertRegex(result.checks["rules-assembly"][1], r"^modules 2, sections [1-9]\d*$")
        self.assertEqual(self.rows(result, "config-inactive"), {})
        self.assertEqual(self.rows(result, "rule-override"), {})
        self.assertEqual(self.rows(result, "expects"), {})

    def test_personal_override_gives_one_rule_override_row(self):
        self.write_file(self.config / "rigger" / "rules.d" / "tests.md", "## Tests\n- mine\n")
        result = self.run_assembly()
        self.assertEqual(
            self.rows(result, "rule-override"),
            {"rule-override tests.tests": ("INFO", "rules.d/tests.md replaces rule tests.tests", "assemble.py --doctor")},
        )
        self.assertRow(result, "rules-assembly", "OK")

    def test_override_of_a_subagent_only_section_gives_a_rule_override_row(self):
        self.write_file(self.config / "rigger" / "rules.d" / "lessons-subagent.md", "## Lessons (mine)\n- mine\n")
        result = self.run_assembly(REPO_PLUGINS / "rigger-lessons")
        self.assertEqual(
            self.rows(result, "rule-override"),
            {"rule-override lessons-subagent.lessons": (
                "INFO", "rules.d/lessons-subagent.md replaces rule lessons-subagent.lessons", "assemble.py --doctor")},
        )
        self.assertRow(result, "rules-assembly", "OK")

    def test_planted_duplicate_warns(self):
        mod = self.tmp / "mods" / "mod-dup"
        self.write_file(mod / ".claude-plugin" / "plugin.json", json.dumps({"name": "mod-dup"}))
        manifest = {"schema": 1, "name": "mod-dup", "layer": "general", "fills": None,
                    "rules": [{"file": "rules/tests.md", "audience": "both"}], "triggers": []}
        self.write_file(mod / "rigger-module.json", json.dumps(manifest))
        self.write_file(mod / "rules" / "tests.md", "## Tests\n- planted\n")
        result = self.run_assembly(mod)
        # Modules outside a marketplace listing come in name order (D9), so mod-dup is read first and kept.
        self.assertRow(result, "rules-assembly", "WARN", "rigger: rule id tests.tests in mod-dup and rigger-rules; kept mod-dup")

    def test_keys_without_effect_give_config_inactive_rows(self):
        path = self.config / "rigger" / "config.json"
        self.write_file(path, json.dumps({"schema": 1, "leak_guard": {"denylist": "~/terms.txt"},
                                          "slots": {"reviewer": {"model": "opus"}}}))
        result = self.run_assembly()
        shown = str(path)
        self.assertEqual(
            self.rows(result, "config-inactive"),
            {
                "config-inactive leak_guard.denylist": (
                    "INFO", "leak_guard.denylist in %s: no effect in this release" % shown, "assemble.py --doctor"),
            },
        )
        self.assertRow(result, "rules-assembly", "OK")

    def test_expected_module_missing_gets_an_install_fix(self):
        hostile = "mod-x; rm -rf ~"
        self.write_file(self.work / ".claude" / "rigger.json",
                        json.dumps({"schema": 1, "expects": ["rigger-rules", "mod-absent", hostile, "mod-absent"]}))
        result = self.run_assembly()
        self.assertEqual(
            self.rows(result, "expects"),
            {
                "expects mod-absent": ("MISSING", "not installed and enabled", "assemble.py --doctor"),
                "expects " + hostile: ("MISSING", "not installed and enabled", "assemble.py --doctor"),
            },
        )
        self.assertEqual(result.fixes["expects mod-absent"], ["claude plugin install mod-absent"])
        self.assertIsNone(result.fixes["expects " + hostile])
        self.assertRow(result, "rules-assembly", "OK")

    def test_real_assembler_run_writes_no_bytecode(self):
        """doctor.sh writes no file: the assembler's imports leave no __pycache__ in a copy of the plugin."""
        copy = self.tmp / "plugin copy" / "rigger-core"
        shutil.copytree(str(PLUGIN), str(copy), ignore=shutil.ignore_patterns("__pycache__", "tests"))
        self.assertEqual(list(copy.rglob("__pycache__")), [])
        kernel = "MINGW64_NT-10.0-26100" if os.name == "nt" else "Linux"
        result = self.run_doctor(kernel, path=self.real_python_path(), script=copy / "tools" / "doctor" / "doctor.sh")
        self.assertRow(result, "rules-assembly", "OK")
        self.assertEqual(list(copy.rglob("__pycache__")), [])

    def test_only_whole_assembler_rows_pass(self):
        answer = ASSEMBLER_OK.replace(
            "'; exit 0",
            "not a row\\nCHECK\\tconfig-inactive a.b\\tINFO\\tx\\ty\\textra\\nCHECK\\tos\\tWARN\\tforged\\tz\\n"
            "FIX\\tos\\tforged\\nCHECK\\texpects mod-y\\tMISSING\\tnot installed and enabled\\tassemble.py --doctor\\n'; exit 0",
        )
        self.stub_python("python3", assembler=answer)
        result = self.run_doctor("Linux")
        self.assertRow(result, "rules-assembly", "OK", "stub")
        self.assertRow(result, "os", "OK", "Linux")
        self.assertNotIn("os", result.fixes)
        self.assertEqual(self.rows(result, "config-inactive"), {})
        self.assertRow(result, "expects mod-y", "MISSING")

    def test_rules_assembly_warns_without_python(self):
        self.skip_if_system_python_candidates()
        result = self.run_doctor("Linux")
        self.assertRow(result, "python", "MISSING")
        self.assertRow(result, "rules-assembly", "WARN", "not checked: no Python 3.9+")

    def test_rules_assembly_warns_when_the_assembler_prints_no_row(self):
        for name, answer in (("junk", "printf 'Traceback\\n'; exit 0"), ("exit 1", ASSEMBLER_OK.replace("exit 0", "exit 1"))):
            with self.subTest(case=name):
                self.stub_python("python3", assembler=answer)
                result = self.run_doctor("Linux")
                self.assertRow(result, "python", "OK")
                self.assertRow(result, "rules-assembly", "WARN")
                self.assertTrue(result.checks["rules-assembly"][1].startswith("not checked: assemble.py --doctor "),
                                result.checks["rules-assembly"])


def load_tests(loader, standard_tests, pattern):
    return PooledSuite(loader.loadTestsFromTestCase(DoctorShTests))


if __name__ == "__main__":
    unittest.main(verbosity=2)
