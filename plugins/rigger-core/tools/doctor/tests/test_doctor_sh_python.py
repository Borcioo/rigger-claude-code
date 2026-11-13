"""Tests for tools/doctor/doctor.sh: the python and python-bare rows, the settings rows (autoCompactWindow,
powershell-tool, worktree-root) and the config folder. Stubs, fixtures and the run helper are in
doctor_sh_harness.py."""

import json
import os
import platform
import shutil
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_sh_harness import (  # noqa: E402
    DoctorShCase,
    PooledSuite,
    PY_SH,
    SCRIPT,
    VERSION,
    WINGET_PYTHON,
    WORKTREE_ROOT,
    find_git_bash,
    sh_path,
)


class DoctorShTests(DoctorShCase):
    # -- python ---------------------------------------------------------------------------------

    def test_python_found_ok(self):
        stub = self.stub_python("python3")
        result = self.run_doctor("Linux")
        self.assertRow(result, "python", "OK", "%s 3.12.1" % stub)
        self.assertNotIn("python", result.fixes)

    def test_python_on_windows_found_through_py(self):
        stub = self.stub_python("py")
        self.assertRow(self.run_doctor("MINGW64_NT-10.0-26100"), "python", "OK", "%s 3.12.1" % stub)

    def test_python_38_is_missing(self):
        self.skip_if_system_python_candidates()
        self.stub_python("python3", version="3.8.10")
        self.os_release("ID=fedora\n")
        result = self.run_doctor("Linux")
        self.assertRow(result, "python", "MISSING")
        self.assertEqual(result.fixes["python"], ["install Python 3.9+ (found 3.8.10)"])

    def test_python_missing_on_windows_gets_winget(self):
        self.skip_if_system_python_candidates()
        self.write_sh(self.stubs / "winget", "exit 0\n")
        result = self.run_doctor("MINGW64_NT-10.0-26100")
        self.assertRow(result, "python", "MISSING")
        self.assertEqual(result.fixes["python"], [WINGET_PYTHON])

    def test_python_missing_on_windows_without_winget(self):
        self.skip_if_system_python_candidates()
        result = self.run_doctor("MINGW64_NT-10.0-26100")
        self.assertRow(result, "python", "MISSING")
        self.assertEqual(result.fixes["python"], ["install Python 3.9+ from python.org"])

    def test_python_missing_on_darwin_without_command_line_tools(self):
        self.skip_if_system_python_candidates()
        self.stub_xcode_select(installed=False)
        self.write_sh(self.stubs / "brew", "exit 0\n")
        result = self.run_doctor("Darwin")
        self.assertRow(result, "python", "MISSING")
        self.assertEqual(result.fixes["python"], ["xcode-select --install"])

    def test_python_missing_on_darwin_with_brew(self):
        self.skip_if_system_python_candidates()
        self.stub_xcode_select(installed=True)
        for where in ("path", "opt/homebrew/bin", "usr/local/bin"):
            with self.subTest(brew=where):
                directory = self.tmp / "brew-on-path" if where == "path" else self.fs / where
                self.write_sh(directory / "brew", "exit 0\n")
                path = [str(self.stubs)] + ([str(directory)] if where == "path" else []) + [self.tools]
                result = self.run_doctor("Darwin", path=path)
                (directory / "brew").unlink()
                self.assertRow(result, "python", "MISSING")
                self.assertEqual(result.fixes["python"], ["brew install python"])

    def test_python_missing_on_darwin_without_brew(self):
        self.skip_if_system_python_candidates()
        self.stub_xcode_select(installed=True)
        result = self.run_doctor("Darwin")
        self.assertEqual(result.fixes["python"], ["install Python 3.9+ from python.org"])

    def test_python_ignores_the_hooks_cache(self):
        stub = self.stub_python("python3")
        cached = self.stub_python("python3", directory=self.tmp / "cached")
        data = self.tmp / "plugin-data"
        self.write_file(data / "python-path.v2", "%s\n" % cached)
        result = self.run_doctor("Linux", env={"CLAUDE_PLUGIN_DATA": sh_path(data)})
        self.assertRow(result, "python", "OK", "%s 3.12.1" % stub)

    def test_real_interpreter_through_py_sh(self):
        interpreter = self.tmp / "real"
        interpreter.mkdir()
        if os.name == "nt":
            folder = str(Path(sys.executable).parent)
        else:
            (interpreter / "python3").symlink_to(sys.executable)
            folder = str(interpreter)
        kernel = "MINGW64_NT-10.0-26100" if os.name == "nt" else "Linux"
        result = self.run_doctor(kernel, path=[str(self.stubs), folder, self.tools])
        self.assertRow(result, "python", "OK")
        found, _, version = result.checks["python"][1].rpartition(" ")
        self.assertEqual(version, platform.python_version())
        self.assertTrue(os.path.samefile(found, sys.executable), found)

    def test_plugin_root_with_space(self):
        root = self.tmp / "plugin root" / "rigger core"
        (root / "tools" / "doctor").mkdir(parents=True)
        (root / "bin").mkdir()
        shutil.copyfile(SCRIPT, root / "tools" / "doctor" / "doctor.sh")
        shutil.copyfile(PY_SH, root / "bin" / "py.sh")
        stub = self.stub_python("python3")
        result = self.run_doctor("Linux", script=root / "tools" / "doctor" / "doctor.sh")
        self.assertRow(result, "python", "OK", "%s 3.12.1" % stub)

    def test_script_path_with_backslashes(self):
        if os.name != "nt":
            self.skipTest("off Windows a backslash is a file name character, so no caller passes such a path")
        stub = self.stub_python("py")
        result = self.run_doctor("MINGW64_NT-10.0-26100", script_arg=str(SCRIPT))
        self.assertRow(result, "python", "OK", "%s 3.12.1" % stub)

    def test_without_py_sh_exits_2_with_one_reason(self):
        root = self.tmp / "no-bin"
        (root / "tools" / "doctor").mkdir(parents=True)
        shutil.copyfile(SCRIPT, root / "tools" / "doctor" / "doctor.sh")
        self.stub_python("python3")
        result = self.run_doctor("Linux", script=root / "tools" / "doctor" / "doctor.sh", expect_code=2)
        self.assertEqual((result.code, result.stdout), (2, ""))
        self.assertRegex(result.stderr, r"\Adoctor\.sh: [^\n]*bin/py\.sh not found[^\n]*\n\Z")

    def test_known_answer_through_git_bin_bash(self):
        bash = find_git_bash()
        if bash is None:
            self.skipTest("no Git for Windows bin\\bash.exe found (CLAUDE_CODE_GIT_BASH_PATH, Program Files, git on PATH)")
        print("%s: %s" % (self._testMethodName, bash))
        result = self.run_doctor("", env={"OS": "Windows_NT"}, script_arg=str(SCRIPT), shell=bash)
        self.assertEqual(result.checks.get("os"), ("OK", "Windows", "uname -s"), result.stdout)
        self.assertIn("python", result.checks, result.stdout)

    # -- python-bare ----------------------------------------------------------------------------

    def test_python_bare_info_when_python_is_python2(self):
        self.stub_python("python", version="2.7.18", stderr_version=True)
        self.stub_python("python3")
        result = self.run_doctor("Linux")
        self.assertRow(result, "python-bare", "INFO", "python is 2.7.18, python3 is 3.12.1")
        self.assertNotIn("python-bare", result.fixes)

    def test_python_bare_absent_when_both_match(self):
        self.stub_python("python")
        self.stub_python("python3")
        self.assertNotIn("python-bare", self.run_doctor("Linux").checks)

    def test_python_bare_absent_when_python3_is_the_store_alias(self):
        self.stub_python("python", version="2.7.18", stderr_version=True)
        self.stub_python("python3", store_alias=True)
        self.assertNotIn("python-bare", self.run_doctor("Linux").checks)

    def test_python_bare_not_run_on_darwin_without_command_line_tools(self):
        self.stub_xcode_select(installed=False)
        self.stub_python("python", version="2.7.18", stderr_version=True)
        self.stub_python("python3")
        self.assertNotIn("python-bare", self.run_doctor("Darwin").checks)

    def test_python_bare_absent_without_python(self):
        self.stub_python("python3")
        self.assertNotIn("python-bare", self.run_doctor("Linux").checks)

    # -- autoCompactWindow --------------------------------------------------------------------------

    def set_compact_window(self, value):
        settings = self.good_settings()
        if value is None:
            del settings["autoCompactWindow"]
        else:
            settings["autoCompactWindow"] = value
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        return self.run_doctor()

    def test_compact_window_below_cap_ok(self):
        for value in (533000, 559999):
            with self.subTest(value=value):
                result = self.set_compact_window(value)
                self.assertRow(result, "autoCompactWindow", "OK", str(value))
                self.assertEqual(result.checks["autoCompactWindow"][2], sh_path(self.config) + "/settings.json")

    def test_compact_window_at_or_above_cap_warns(self):
        for value in (560000, 600000):
            with self.subTest(value=value):
                result = self.set_compact_window(value)
                self.assertRow(result, "autoCompactWindow", "WARN", "%d, at or above compact_guard's cap 560000" % value)
                self.assertEqual(result.fixes["autoCompactWindow"], ["/rigger-core:harness-setup"])

    def test_compact_window_absent_warns(self):
        result = self.set_compact_window(None)
        self.assertRow(result, "autoCompactWindow", "WARN", "not set; harness-setup offers it")
        self.assertEqual(result.fixes["autoCompactWindow"], ["/rigger-core:harness-setup"])

    def test_compact_window_of_the_wrong_shape_warns(self):
        for value in ("533000", [533000], {"value": 533000}, True, 533000.5):
            with self.subTest(value=value):
                result = self.set_compact_window(value)
                self.assertRow(result, "autoCompactWindow", "WARN", "not a number")
                self.assertEqual(result.fixes["autoCompactWindow"], ["/rigger-core:harness-setup"])

    # -- powershell-tool ----------------------------------------------------------------------------

    def test_powershell_tool_set_ok_on_windows(self):
        self.good_config(windows=True)
        self.assertRow(self.run_doctor("MINGW64_NT-10.0-26100"), "powershell-tool", "OK", "CLAUDE_CODE_USE_POWERSHELL_TOOL=1")

    def test_powershell_tool_unset_warns_without_fix(self):
        for value in (None, "0", 1):
            with self.subTest(value=value):
                settings = self.good_settings(windows=True)
                if value is None:
                    del settings["env"]["CLAUDE_CODE_USE_POWERSHELL_TOOL"]
                else:
                    settings["env"]["CLAUDE_CODE_USE_POWERSHELL_TOOL"] = value
                self.write_config(settings, self.good_installed(windows=True), self.good_marketplaces())
                result = self.run_doctor("MINGW64_NT-10.0-26100")
                self.assertRow(result, "powershell-tool", "WARN", "CLAUDE_CODE_USE_POWERSHELL_TOOL is not 1")
                self.assertNotIn("powershell-tool", result.fixes)

    def test_powershell_tool_row_only_on_windows(self):
        self.good_config()
        self.assertNotIn("powershell-tool", self.run_doctor("Linux").checks)

    # -- worktree-root ------------------------------------------------------------------------------

    def test_worktree_root_set_ok(self):
        self.good_config()
        self.assertRow(self.run_doctor(), "worktree-root", "OK", WORKTREE_ROOT)

    def test_worktree_root_unset_warns(self):
        settings = self.good_settings()
        del settings["env"]["HARNESS_WORKTREE_ROOT"]
        # Only the user env counts: not a nested env, an escaped string or this process's variable.
        settings["statusLine"] = {"type": "command", "command": 'echo "HARNESS_WORKTREE_ROOT": x', "env": {"HARNESS_WORKTREE_ROOT": "/decoy"}}
        self.write_config(settings, self.good_installed(), self.good_marketplaces())
        result = self.run_doctor(env={"HARNESS_WORKTREE_ROOT": "/decoy"})
        self.assertRow(result, "worktree-root", "WARN", "HARNESS_WORKTREE_ROOT not set")
        self.assertEqual(result.fixes["worktree-root"], ["/rigger-core:harness-setup"])

    # -- config folder ------------------------------------------------------------------------------

    def test_config_dir_wins_over_home(self):
        self.write_config(self.good_settings(), self.good_installed(), self.good_marketplaces(), root=self.home / ".claude")
        self.write_config({"enabledPlugins": {}}, {"version": 2, "plugins": {}}, {})
        result = self.run_doctor()
        self.assertRow(result, "marketplace", "MISSING")
        self.assertRow(result, "plugin rigger-core", "MISSING")
        self.assertRow(result, "autoCompactWindow", "WARN")
        self.assertRow(result, "worktree-root", "WARN")
        self.assertEqual(result.checks["autoCompactWindow"][2], sh_path(self.config) + "/settings.json")

    def test_home_used_without_config_dir(self):
        self.write_config(self.good_settings(), self.good_installed(), self.good_marketplaces(), root=self.home / ".claude")
        result = self.run_doctor(env={"CLAUDE_CONFIG_DIR": None})
        self.assertRow(result, "marketplace", "OK")
        self.assertRow(result, "plugin rigger-core", "OK", VERSION)
        self.assertRow(result, "worktree-root", "OK", WORKTREE_ROOT)


def load_tests(loader, standard_tests, pattern):
    return PooledSuite(loader.loadTestsFromTestCase(DoctorShTests))


if __name__ == "__main__":
    unittest.main(verbosity=2)
