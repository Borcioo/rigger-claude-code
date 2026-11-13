"""Tests for tools/doctor/doctor.sh: the os, claude-version, hook-shell and git rows, the Linux distro map, whole
runs and own errors. Stubs, fixtures and the run helper are in doctor_sh_harness.py."""

import os
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_sh_harness import (  # noqa: E402
    DoctorShCase,
    PooledSuite,
    PLUGIN,
    PY_SH,
    REQUIRED,
    SCRIPT,
    entry,
)


class DoctorShTests(DoctorShCase):
    # -- whole runs -------------------------------------------------------------------------

    def test_linux_rows_in_order_all_ok(self):
        self.good_config()
        self.stub_git()
        self.stub_python("python3")
        result = self.run_doctor("Linux")
        self.assertEqual(
            result.order,
            ["os", "claude-version", "hook-shell", "git", "python", "marketplace"]
            + ["plugin " + name for name in REQUIRED]
            + ["superpowers", "autoCompactWindow", "worktree-root"],
        )
        self.assertEqual({row[0] for row in result.checks.values()}, {"OK"}, result.stdout)
        self.assertEqual(result.fixes, {})

    def test_windows_rows_in_order_without_hook_shell_or_git(self):
        self.good_config(windows=True)
        self.stub_git()
        self.stub_python("py")
        result = self.run_doctor("MINGW64_NT-10.0-26100")
        self.assertEqual(
            result.order,
            ["os", "claude-version", "python", "marketplace"]
            + ["plugin " + name for name in REQUIRED + ("rigger-windows",)]
            + ["superpowers", "autoCompactWindow", "powershell-tool", "worktree-root"],
        )
        self.assertEqual({row[0] for row in result.checks.values()}, {"OK"}, result.stdout)

    def test_writes_nothing_and_never_runs_claude(self):
        self.good_config()
        self.stub_uname("Linux")
        self.stub_git()
        self.stub_python("python3")

        tests = SCRIPT.parent / "tests"

        def snapshot():
            # Only what doctor.sh could write: its sandbox, py.sh's folder and its own folder. The tests
            # folder and the rest of the plugin are skipped: tests running beside this one write there.
            found = {}
            for top in (self.tmp, PLUGIN / "bin", SCRIPT.parent):
                for path in top.rglob("*"):
                    if path == tests or tests in path.parents:
                        continue
                    stat = path.stat()
                    found[str(path)] = (stat.st_size, stat.st_mtime_ns)
            return found

        before = snapshot()
        self.assertIn(str(PY_SH), before)
        self.run_doctor("Linux")
        self.assertEqual(snapshot(), before)

    # -- os -----------------------------------------------------------------------------------

    def test_os_names_the_system(self):
        for kernel, name in (("Darwin", "macOS"), ("Linux", "Linux"), ("MINGW64_NT-10.0-26100", "Windows"), ("MSYS_NT-10.0", "Windows")):
            with self.subTest(kernel=kernel):
                self.stub_uname(kernel)
                self.assertRow(self.run_doctor(kernel), "os", "OK", name)

    def test_os_unknown_system_warns(self):
        self.assertRow(self.run_doctor("SunOS"), "os", "WARN", "unknown system SunOS")

    # -- claude-version -----------------------------------------------------------------------

    def test_claude_version_at_or_above_floor_ok(self):
        for agent, version in (("2-1-317", "2.1.317"), ("2-2-0", "2.2.0"), ("3-0-0", "3.0.0"), ("2-1-320", "2.1.320")):
            with self.subTest(agent=agent):
                result = self.run_doctor(env={"AI_AGENT": "claude-code_%s_agent" % agent})
                self.assertRow(result, "claude-version", "OK", version)
                self.assertNotIn("claude-version", result.fixes)

    def test_claude_version_below_floor_warns(self):
        for agent in ("2-1-316", "2-1-300", "2-0-999", "1-9-999"):
            with self.subTest(agent=agent):
                result = self.run_doctor(env={"AI_AGENT": "claude-code_%s_agent" % agent})
                self.assertRow(result, "claude-version", "WARN", "%s, below 2.1.317" % agent.replace("-", "."))
                self.assertEqual(result.fixes["claude-version"], ["claude update"])

    def test_claude_version_below_floor_in_desktop_app(self):
        result = self.run_doctor(env={"AI_AGENT": "claude-code_2-1-300_agent", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"})
        self.assertRow(result, "claude-version", "WARN")
        self.assertEqual(result.fixes["claude-version"], ["update the Claude desktop app"])

    def test_claude_version_unknown_without_claude_code_agent(self):
        for agent in (None, "other-agent_2-1-317_agent", "2-1-317_other_agent", "claude-code_2-x-317_agent", "claude-code_agent"):
            with self.subTest(agent=agent):
                result = self.run_doctor(env={"AI_AGENT": agent})
                self.assertRow(result, "claude-version", "WARN", "unknown")
                self.assertNotIn("claude-version", result.fixes)

    # -- hook-shell ---------------------------------------------------------------------------

    def test_hook_shell_found(self):
        result = self.run_doctor("Linux")
        self.assertRow(result, "hook-shell", "OK")
        self.assertRegex(result.checks["hook-shell"][1], r"/sh(\.exe)?$")

    def test_hook_shell_missing(self):
        result = self.run_doctor("Linux", path=[str(self.stubs), self.make_tools_dir(with_sh=False)])
        self.assertRow(result, "hook-shell", "MISSING")
        self.assertNotIn("hook-shell", result.fixes)

    # -- git ------------------------------------------------------------------------------------

    def test_git_found_ok(self):
        self.assertTrue(self.stub_git())
        self.assertRow(self.run_doctor("Linux"), "git", "OK", "2.43.0")

    def test_git_missing_on_linux_gets_the_distro_fix(self):
        self.os_release('ID=debian\nVERSION_ID="12"\n')
        result = self.run_doctor("Linux")
        self.assertRow(result, "git", "MISSING")
        self.assertEqual(result.fixes["git"], ["sudo apt install -y git"])

    def test_git_on_darwin_is_the_clt_stub_without_command_line_tools(self):
        usr_bin = self.fs / "usr" / "bin"
        self.stub_git(directory=usr_bin)
        self.stub_xcode_select(installed=False)
        result = self.run_doctor("Darwin", path=[str(self.stubs), str(usr_bin), self.tools])
        self.assertRow(result, "git", "MISSING")
        self.assertEqual(result.fixes["git"], ["xcode-select --install"])

    def test_git_on_darwin_with_command_line_tools_ok(self):
        usr_bin = self.fs / "usr" / "bin"
        self.stub_git(directory=usr_bin)
        self.stub_xcode_select(installed=True)
        result = self.run_doctor("Darwin", path=[str(self.stubs), str(usr_bin), self.tools])
        self.assertRow(result, "git", "OK", "2.43.0")

    def test_git_on_darwin_from_elsewhere_ok_without_command_line_tools(self):
        self.stub_git()
        self.stub_xcode_select(installed=False)
        self.assertRow(self.run_doctor("Darwin"), "git", "OK", "2.43.0")

    def test_git_absent_on_darwin(self):
        self.stub_xcode_select(installed=True)
        result = self.run_doctor("Darwin")
        self.assertRow(result, "git", "MISSING")
        self.assertEqual(result.fixes["git"], ["xcode-select --install"])

    # -- Linux distro map (git and python both missing) -----------------------------------------

    def check_distro(self, os_release, git_fix, python_fix):
        self.skip_if_system_python_candidates()
        if os_release is not None:
            self.os_release(os_release)
        result = self.run_doctor("Linux")
        self.assertRow(result, "git", "MISSING")
        self.assertRow(result, "python", "MISSING")
        self.assertEqual((result.fixes["git"], result.fixes["python"]), ([git_fix], [python_fix]))

    def test_distro_debian(self):
        self.check_distro("ID=debian\n", "sudo apt install -y git", "sudo apt install -y python3")

    # The rows below name only ID where ID_LIKE would reach the same command, so each map entry is tested alone.
    def test_distro_ubuntu(self):
        self.check_distro("NAME=Ubuntu\nID=ubuntu\n", "sudo apt install -y git", "sudo apt install -y python3")

    def test_distro_by_id_like_list(self):
        self.check_distro('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "sudo apt install -y git", "sudo apt install -y python3")

    def test_distro_fedora(self):
        self.check_distro("ID=fedora\n", "sudo dnf install -y git", "sudo dnf install -y python3")

    def test_distro_rhel(self):
        self.check_distro('ID="rhel"\n', "sudo dnf install -y git", "sudo dnf install -y python3")

    def test_distro_centos(self):
        self.check_distro('ID="centos"\n', "sudo dnf install -y git", "sudo dnf install -y python3")

    def test_distro_rhel_like(self):
        self.check_distro('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "sudo dnf install -y git", "sudo dnf install -y python3")

    def test_distro_arch(self):
        self.check_distro("ID=arch\n", "sudo pacman -S --noconfirm git", "sudo pacman -S --noconfirm python")

    def test_distro_opensuse(self):
        self.check_distro('ID="opensuse-tumbleweed"\n', "sudo zypper install -y git", "sudo zypper install -y python3")

    def test_distro_suse(self):
        self.check_distro('ID="sles"\nID_LIKE="suse"\n', "sudo zypper install -y git", "sudo zypper install -y python3")

    def test_distro_alpine(self):
        self.check_distro("ID=alpine\n", "sudo apk add git", "sudo apk add python3")

    def test_distro_unknown(self):
        self.check_distro(
            "ID=gentoo\n", "install git with your package manager", "install python3 with your package manager"
        )

    def test_distro_glob_is_not_expanded(self):
        # An unquoted loop over ID must not match "*" against the files in the current folder.
        (self.work / "debian").write_bytes(b"")
        self.check_distro("ID=*\n", "install git with your package manager", "install python3 with your package manager")

    def test_distro_without_os_release(self):
        self.check_distro(None, "install git with your package manager", "install python3 with your package manager")

    # -- child output and own errors -----------------------------------------------------------------

    def test_crlf_from_every_child_is_stripped(self):
        self.stub_uname("Linux", crlf=True)
        self.stub_git(crlf=True)
        stub = self.stub_python("python3", crlf=True)
        self.stub_python("python", version="2.7.18", stderr_version=True, crlf=True)
        result = self.run_doctor("Linux")
        self.assertNotIn("\r", result.stdout)
        self.assertRow(result, "os", "OK", "Linux")
        self.assertRow(result, "git", "OK", "2.43.0")
        self.assertRow(result, "python", "OK", "%s 3.12.1" % stub)
        self.assertRow(result, "python-bare", "INFO", "python is 2.7.18, python3 is 3.12.1")

    def test_no_posix_tools_exits_2_with_one_reason(self):
        result = self.run_doctor("", path=[str(self.stubs)], expect_code=2)
        self.assertEqual(result.code, 2)
        self.assertEqual(result.stdout, "")
        self.assertRegex(result.stderr, r"^doctor\.sh: [a-z]+ not found on PATH\n$")

    def test_no_home_and_no_config_dir_exits_2(self):
        if os.name == "nt":
            self.skipTest("Git Bash sets HOME itself when it is unset, so this exit 2 is reachable under dash only")
        result =self.run_doctor(env={"CLAUDE_CONFIG_DIR": None, "HOME": None}, expect_code=2)
        self.assertEqual(result.code, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "doctor.sh: neither CLAUDE_CONFIG_DIR nor HOME is set\n")


def load_tests(loader, standard_tests, pattern):
    return PooledSuite(loader.loadTestsFromTestCase(DoctorShTests))


if __name__ == "__main__":
    unittest.main(verbosity=2)
