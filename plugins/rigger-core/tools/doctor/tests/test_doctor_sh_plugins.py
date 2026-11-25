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
        """doctor.sh with this test's own Python and RIGGER_MODULE_DIRS naming the repo's rigger-rules, rigger-review,
        rigger-testing (the two slot defaults) and `dirs`."""
        roots = [str(REPO_PLUGINS / "rigger-rules"), str(REPO_PLUGINS / "rigger-review"),
                 str(REPO_PLUGINS / "rigger-testing")] + [str(d) for d in dirs]
        kernel = kernel or ("MINGW64_NT-10.0-26100" if os.name == "nt" else "Linux")
        return self.run_doctor(kernel, path=self.real_python_path(), env={"RIGGER_MODULE_DIRS": os.pathsep.join(roots)})

    def rows(self, result, prefix):
        return {name: row for name, row in result.checks.items() if name.startswith(prefix)}

    def test_clean_layout_gives_rules_assembly_ok(self):
        result = self.run_assembly()
        self.assertRow(result, "rules-assembly", "OK")
        self.assertRegex(result.checks["rules-assembly"][1], r"^modules 4, sections [1-9]\d*$")
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

    # -- slot rows (Task 18) ------------------------------------------------------------------------

    SLOTS = ("reviewer", "worker", "implementer", "mapper", "testing", "shell-guard", "lessons",
             "process.brainstorm", "process.plans", "process.tdd", "process.debug")  # spec section 7.1

    def module(self, name, fills, failure_mode=None, checker=None):
        """A module fixture that fills `fills`; a rule checker file when `checker` is given."""
        mod = self.tmp / "mods" / name
        self.write_file(mod / ".claude-plugin" / "plugin.json", json.dumps({"name": name}))
        manifest = {"schema": 1, "name": name, "layer": "general", "fills": fills, "rules": [], "triggers": []}
        if failure_mode is not None:
            manifest["failure_mode"] = failure_mode
        if checker is not None:
            manifest["provides"] = {fills: {"kind": "hook", "target": name, "rule_checker": checker}}
            self.write_file(mod / checker, "RULES = []\n")
        self.write_file(mod / "rigger-module.json", json.dumps(manifest))
        return mod

    def personal_config(self, data):
        self.write_file(self.config / "rigger" / "config.json", json.dumps(dict({"schema": 1}, **data)))
        return self.config / "rigger" / "config.json"

    def test_clean_layout_gives_one_slot_row_per_slot_and_no_warning(self):
        result = self.run_assembly()
        expected = set(self.SLOTS) if os.name == "nt" else set(self.SLOTS) - {"shell-guard"}  # requires os:windows
        self.assertEqual(set(self.rows(result, "slot ")), {"slot " + path for path in expected})
        how = "assemble.py --doctor"
        self.assertEqual(result.checks["slot reviewer"], ("OK", "rigger-review:reviewer (default)", how))
        self.assertEqual(result.checks["slot testing"], ("OK", "rigger-testing:testing (default)", how))
        self.assertEqual(result.checks["slot process.tdd"], ("INFO", "empty: plugin superpowers is not enabled", how))
        self.assertEqual(result.checks["slot lessons"], ("INFO", "empty: plugin rigger-lessons is not enabled", how))
        self.assertEqual([n for n, row in result.checks.items() if row[2] == how and row[0] in ("WARN", "MISSING")], [])
        self.assertEqual(self.rows(result, "slot-"), {})
        self.assertRow(result, "rules-assembly", "OK")

    def test_hook_slot_rows_name_the_filler_and_its_failure_mode(self):
        # `requires: []` keeps the shell-guard row on every system; it sets neither kind nor target.
        # An expected filler set above level 1 that is enabled gives OK, not MISSING.
        self.personal_config({"slots": {"shell-guard": {"requires": []},
                                        "lessons": {"kind": "hook", "target": "rigger-lessons"}}})
        result = self.run_assembly(REPO_PLUGINS / "rigger-lessons", REPO_PLUGINS / "rigger-windows")
        self.assertRow(result, "slot lessons", "OK", "rigger-lessons (failure mode: undeclared)")
        self.assertRow(result, "slot shell-guard", "OK", "rigger-windows (fails open)")
        checker = result.checks["slot-checker shell-guard"]
        self.assertEqual(checker[0], "OK", checker)
        self.assertTrue(checker[1].endswith("rigger-windows/tools/shell-guard/lessons_guard.py"), checker)
        self.assertTrue(Path(checker[1]).is_file(), checker)
        self.assertRow(result, "rules-assembly", "OK")

    def test_a_shell_guard_filler_without_a_rule_checker_gives_an_info_row(self):
        self.personal_config({"slots": {"shell-guard": {"requires": []}}})
        result = self.run_assembly(self.module("zz-guard", "shell-guard", failure_mode="closed"))
        self.assertRow(result, "slot shell-guard", "OK", "zz-guard (fails closed)")
        self.assertRow(result, "slot-checker shell-guard", "INFO",
                       "no checker: live:<rule> lessons are reported as no checker")

    def test_two_fillers_warn_with_a_disable_fix(self):
        for second, fix in (("zz-lessons", ["claude plugin disable zz-lessons"]), ("zz.Lessons", None)):
            with self.subTest(second=second):
                result = self.run_assembly(REPO_PLUGINS / "rigger-lessons", self.module(second, "lessons"))
                self.assertRow(result, "slot lessons", "WARN", "filled by rigger-lessons and " + second)
                self.assertEqual(result.fixes["slot lessons"], fix)
                self.assertRow(result, "rules-assembly", "OK")  # the two-filler notice has its own row

    def test_an_expected_filler_set_above_level_one_that_is_absent_is_missing(self):
        for name, fix in (("mod-guard", ["claude plugin install mod-guard"]), ("Mod.Guard", None)):
            with self.subTest(name=name):
                self.personal_config({"slots": {"lessons": {"kind": "hook", "target": name}}})
                result = self.run_assembly()
                self.assertRow(result, "slot lessons", "MISSING", "expected %s is not enabled" % name)
                self.assertEqual(result.fixes["slot lessons"], fix)
                self.assertRow(result, "rules-assembly", "OK")

    def test_a_config_row_that_loads_tdd_before_testing_warns(self):
        self.write_file(self.work / ".claude" / "rigger.json", json.dumps({"schema": 1, "triggers": {
            "proj.tdd-first": {"before": "x", "load": ["slot:process.tdd", "slot:testing"]},
            "proj.testing-first": {"before": "y", "load": ["slot:testing", "slot:process.tdd"]},
        }}))
        result = self.run_assembly()
        self.assertEqual(self.rows(result, "slot-order"), {"slot-order proj.tdd-first": (
            "WARN", "loads process.tdd without testing before it", "assemble.py --doctor")})

    def table_json(self, detail, config):
        head = "holds a trigger table; move its rows to triggers in %s: " % config
        self.assertTrue(detail.startswith(head), detail)
        return detail[len(head):]

    def test_a_rules_d_trigger_table_gives_its_rows_as_config_json(self):
        self.write_file(self.config / "rigger" / "rules.d" / "mine.md", (
            "## Skill triggers\n\n- Mine:\n\n| Before | Skill |\n|---|---|\n"
            "| writing code or tests, reviewing tests, a spike, port or rehearsal | `rigger-core:testing`, then `x:tdd` |\n"
            "| meeting notes | `notes:style` |\n"))
        self.write_file(self.config / "rigger" / "rules.d" / "other.md", "| Name | Value |\n|---|---|\n| a | `b:c` |\n")
        result = self.run_assembly()
        self.assertEqual(set(self.rows(result, "rules-d-table")), {"rules-d-table rules.d/mine.md"})
        row = result.checks["rules-d-table rules.d/mine.md"]
        self.assertEqual(row[0], "INFO", row)
        data = json.loads(self.table_json(row[1], self.config / "rigger" / "config.json"))
        self.assertEqual(data, {"triggers": {
            # a row copied from the shipped table keeps its id, so the config row replaces it
            "rigger-core.testing": {"before": "writing code or tests, reviewing tests, a spike, port or rehearsal",
                                    "load": ["rigger-core:testing", "x:tdd"]},
            "personal.mine-2": {"before": "meeting notes", "load": ["notes:style"]},
        }})

    def test_the_old_rigger_rules_table_moves_whole_under_the_shipped_ids(self):
        """Spec section 13, migration: the table release 0.5.0 shipped, copied into rules.d, fits uncut."""
        old = REPO_PLUGINS.parent / "tests" / "fixtures" / "golden-34e7713" / "plugins" / "rigger-rules" / "rules" / "skills.md"
        self.write_file(self.config / "rigger" / "rules.d" / "skills.md", old.read_bytes().decode("utf-8"))
        result = self.run_assembly()
        text = self.table_json(result.checks["rules-d-table rules.d/skills.md"][1], self.config / "rigger" / "config.json")
        rows = json.loads(text)["triggers"]  # a cut JSON would not parse
        self.assertEqual({row_id: row["load"] for row_id, row in rows.items()}, {
            "rigger-core.orchestrating-subagents": ["rigger-core:orchestrating-subagents"],
            "rigger-core.git-workflow": ["rigger-core:git-workflow"],
            "rigger-core.web-ui-traps": ["rigger-core:web-ui-traps"],
            "rigger-core.e2e-playwright-speed": ["rigger-core:e2e-playwright-speed"],
            "rigger-core.stack-tooling": ["rigger-core:stack-tooling"],
            "rigger-core.plans": ["superpowers:brainstorming", "superpowers:writing-plans"],
            "rigger-core.testing": ["rigger-core:testing", "superpowers:test-driven-development"],
            "rigger-core.debugging": ["superpowers:systematic-debugging"],
        })
        self.assertEqual(rows["rigger-core.debugging"]["before"], "a bug, a failing test or unexpected behaviour")

    def test_empty_slot_rows_say_why_above_level_one(self):
        self.personal_config({"slots": {"testing": None, "lessons": {"kind": "hook", "target": "rigger-rules"}}})
        result = self.run_assembly()
        self.assertRow(result, "slot testing", "INFO", "empty: personal config leaves it empty")
        self.assertRow(result, "slot lessons", "INFO", "empty: rigger-rules does not fill it")

    def test_two_fillers_win_over_a_missing_expected_filler(self):
        self.personal_config({"slots": {"lessons": {"kind": "hook", "target": "mod-absent"}}})
        result = self.run_assembly(REPO_PLUGINS / "rigger-lessons", self.module("zz-lessons", "lessons"))
        self.assertRow(result, "slot lessons", "WARN", "filled by rigger-lessons and zz-lessons")
        self.assertEqual(result.fixes["slot lessons"], ["claude plugin disable zz-lessons"])

    def test_a_command_slot_shows_its_program_and_source(self):
        self.personal_config({"slots": {"reviewer": {"kind": "command", "target": ["reviewer-cli", "--out", "{out}"]}}})
        result = self.run_assembly()
        self.assertRow(result, "slot reviewer", "OK", "reviewer-cli … (personal)")

    def test_a_long_rules_d_table_is_cut_at_1500_characters(self):
        lines = "".join("| step %02d %s | `a:b` |\n" % (i, "w" * 60) for i in range(40))
        self.write_file(self.config / "rigger" / "rules.d" / "long.md", "| Before | Skill |\n|---|---|\n" + lines)
        result = self.run_assembly()
        text = self.table_json(result.checks["rules-d-table rules.d/long.md"][1], self.config / "rigger" / "config.json")
        self.assertEqual(len(text), 1502)
        self.assertTrue(text.startswith('{"triggers": {"personal.long-1": {"before": "step 00 '), text[:80])
        self.assertTrue(text.endswith(" …"), text[-10:])

    def test_a_forged_row_in_a_slot_target_does_not_get_through(self):
        # With " (personal)" and the how-checked field appended, the second line is a whole row of a name the filter lets through.
        forged = "x\nCHECK\tslot worker\tWARN\tforged"
        self.personal_config({"slots": {"reviewer": {"target": forged},
                                        "mapper": {"kind": "command", "target": [forged + "\t", "{out}"]}}})
        result = self.run_assembly()
        self.assertRow(result, "slot worker", "OK", "rigger-core:worker (default)")
        self.assertRow(result, "slot reviewer", "OK", "rigger-review:reviewer (default)")
        self.assertNotIn("forged", result.stdout)

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
            "FIX\\tos\\tforged\\nCHECK\\texpects mod-y\\tMISSING\\tnot installed and enabled\\tassemble.py --doctor\\n"
            "CHECK\\tslot worker\\tOK\\tx\\tCHECK\\tos\\tWARN\\n"
            "CHECK\\tslot lessons\\tWARN\\tfilled by a and b\\tassemble.py --doctor\\nFIX\\tslot lessons\\tclaude plugin disable b\\n"
            "CHECK\\tslot-checker shell-guard\\tINFO\\tc\\td\\nCHECK\\tslot-order t.x\\tWARN\\te\\tf\\n"
            "CHECK\\trules-d-table rules.d/m.md\\tINFO\\tg\\th\\n'; exit 0",
        )
        self.stub_python("python3", assembler=answer)
        result = self.run_doctor("Linux")
        self.assertRow(result, "rules-assembly", "OK", "stub")
        self.assertRow(result, "os", "OK", "Linux")
        self.assertNotIn("os", result.fixes)
        self.assertEqual(self.rows(result, "config-inactive"), {})
        self.assertRow(result, "expects mod-y", "MISSING")
        self.assertNotIn("slot worker", result.checks)
        self.assertRow(result, "slot lessons", "WARN", "filled by a and b")
        self.assertEqual(result.fixes["slot lessons"], ["claude plugin disable b"])
        for name in ("slot-checker shell-guard", "slot-order t.x", "rules-d-table rules.d/m.md"):
            self.assertIn(name, result.checks)

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
