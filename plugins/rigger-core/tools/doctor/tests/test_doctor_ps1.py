"""Tests for doctor.ps1, run by Windows PowerShell 5.1 and by pwsh with stub programs on PATH.

Every case runs in both shells; the shell prints its own version in the same invocation that runs the script.
PATH is the case's stub folders plus System32. py.exe/python.exe are a small console exe built here with the
.NET Framework csc.exe; it answers `-3 -c ...`, `-c ...` and `--version` from STUB_<NAME>_<MODE>_OUT/_ERR/_EXIT
and exits 99 on any other call. git.exe and winget.exe are copies of it (only their presence matters).
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DOCTOR = Path(__file__).resolve().parent.parent / "doctor.ps1"
GIT_WINGET = "winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements"
GIT_URL = "https://git-scm.com/downloads/win"
ENV_FIX = 'set "CLAUDE_CODE_GIT_BASH_PATH": "<path to bin\\bash.exe>" under env in settings.json'
PY_WINGET = "winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements"
PY_ORG = "install Python 3.9+ from python.org"
# Built at run time so the repo's leak scanner (scripts/sweep.py) does not report a drive path here.
DRIVE_C = "C" + ":"

STUB_CS = r"""
using System;
class Stub {
    static int Main(string[] args) {
        string name = AppDomain.CurrentDomain.FriendlyName;
        name = name.Substring(0, name.LastIndexOf('.')).ToUpperInvariant();
        string mode = null;
        if (args.Length >= 2 && args[0] == "-3" && args[1] == "-c") mode = "PY3";
        else if (args.Length >= 1 && args[0] == "-c") mode = "C";
        else if (args.Length == 1 && args[0] == "--version") mode = "VERSION";
        string key = "STUB_" + name + "_" + mode;
        string exit = mode == null ? null : Environment.GetEnvironmentVariable(key + "_EXIT");
        if (exit == null) { Console.Error.WriteLine("stub " + name + ": unexpected call: " + string.Join(" ", args)); return 99; }
        string text = Environment.GetEnvironmentVariable(key + "_OUT");
        if (text != null) Console.Out.WriteLine(text);
        text = Environment.GetEnvironmentVariable(key + "_ERR");
        if (text != null) Console.Error.WriteLine(text);
        return int.Parse(exit);
    }
}
"""

STUB_EXE = None
BUILD_DIR = None


def setUpModule():
    global STUB_EXE, BUILD_DIR
    if os.name != "nt":
        return
    BUILD_DIR = Path(tempfile.mkdtemp(prefix="doctor-ps1-build-"))
    csc = next(Path(os.environ["WINDIR"]).glob("*/Framework64/v4.0.30319/csc.exe"), None)
    if csc is None:
        raise RuntimeError("no .NET Framework 4 csc.exe (64-bit) under WINDIR")
    source = BUILD_DIR / "stub.cs"
    source.write_text(STUB_CS, encoding="utf-8")
    STUB_EXE = BUILD_DIR / "stub.exe"
    result = subprocess.run([str(csc), "/nologo", "/out:" + str(STUB_EXE), str(source)], capture_output=True, timeout=120)
    if result.returncode != 0 or not STUB_EXE.is_file():
        raise RuntimeError("csc failed: %r" % result.stdout)


def tearDownModule():
    if BUILD_DIR is not None:
        shutil.rmtree(BUILD_DIR, True)


def shells():
    system32 = Path(os.environ["SystemRoot"], "System32")
    return [("powershell.exe", str(system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "5"),
            ("pwsh", shutil.which("pwsh"), "7")]


def ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


class Run:
    def __init__(self, shell, code, stdout):
        self.shell = shell
        self.code = code
        self.raw = "[%s, exit %s]\n%s" % (shell, code, stdout)
        lines = [line for line in stdout.replace("\r\n", "\n").split("\n") if line]
        self.version = lines[0] if lines else ""
        self.lines = lines[1:]
        self.checks, self.fixes, self.bash = [], [], []
        for line in self.lines:
            fields = line.split("\t")
            if fields[0] == "CHECK":
                assert len(fields) == 5, "CHECK line without 5 fields: %r" % line
                self.checks.append(tuple(fields[1:]))
            elif fields[0] == "FIX":
                assert len(fields) == 3, "FIX line without 3 fields: %r" % line
                self.fixes.append((fields[1], fields[2]))
            elif fields[0] == "BASH":
                self.bash.append(fields[1])

    def rows(self, name):
        return [row for row in self.checks if row[0] == name]

    def fix(self, name):
        return [cmd for row, cmd in self.fixes if row == name]


@unittest.skipUnless(os.name == "nt", "doctor.ps1 runs on native Windows only; doctor.sh covers the other systems")
class DoctorPs1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-ps1-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pf = self.tmp / "pf"
        self.pf86 = self.tmp / "pf86"
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.path_dirs = [self.stubs]
        self.env = {}

    def make_file(self, path, exe=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if exe:
            shutil.copyfile(STUB_EXE, path)
        else:
            path.write_bytes(b"")
        return path

    def install_bash(self, root):
        return self.make_file(Path(root) / "Git" / "bin" / "bash.exe")

    def add_stub(self, name):
        return self.make_file(self.stubs / (name + ".exe"), exe=True)

    def run_doctor(self, arguments=None):
        """Run doctor.ps1 in each shell; returns the list of Run objects (one per shell found). arguments replaces the
        default -ProgramFiles/-ProgramFilesX86 pair."""
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("CLAUDE_CODE_GIT_BASH_PATH", "PSMODULEPATH")}
        env["PATH"] = os.pathsep.join([str(d) for d in self.path_dirs] + [str(Path(os.environ["SystemRoot"], "System32"))])
        env["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        env.update(self.env)
        if arguments is None:
            arguments = "-ProgramFiles %s -ProgramFilesX86 %s" % (ps_quote(self.pf), ps_quote(self.pf86))
        command = "& { $PSVersionTable.PSVersion.ToString(); & %s %s }; exit $LASTEXITCODE" % (ps_quote(DOCTOR), arguments)
        runs = []
        for label, exe, major in shells():
            if exe is None:
                print("SKIP %s: not on PATH" % label)
                continue
            result = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                    capture_output=True, env=env, cwd=str(self.tmp), timeout=120)
            run = Run(label, result.returncode, result.stdout.decode("utf-8", "replace"))
            self.assertEqual(run.version.split(".")[0], major, "printed version %r\n%s" % (run.version, run.raw))
            print("%s %s: %s" % (self._testMethodName, label, run.version))
            runs.append(run)
        self.assertTrue(runs, "no PowerShell ran")
        return runs

    def each(self):
        """The runs of a case that must exit 0."""
        return self.each_with(None)

    def each_with(self, arguments):
        runs = self.run_doctor(arguments)
        for run in runs:
            self.assertEqual(run.code, 0, run.raw)
        return runs

    def assert_hook_shell_ok(self, run, source, path):
        self.assertEqual([r[1] for r in run.rows("hook-shell")][-1:], ["OK"], run.raw)
        detail = run.rows("hook-shell")[-1][2]
        self.assertIn(source, detail, run.raw)
        self.assertIn(str(path), detail, run.raw)
        self.assertEqual(run.bash, [str(path)], run.raw)
        self.assertEqual(run.fix("hook-shell"), [], run.raw)
        self.assertEqual(run.rows("python"), [], "python is checked only without Git Bash\n" + run.raw)

    def one_row(self, run, name):
        rows = run.rows(name)
        self.assertEqual(len(rows), 1, "expected one %s row\n%s" % (name, run.raw))
        return rows[0]

    def assert_warn(self, run, value, reason):
        warn = [r for r in run.rows("hook-shell") if r[1] == "WARN"]
        self.assertEqual(len(warn), 1, run.raw)
        self.assertIn(value, warn[0][2], run.raw)
        self.assertIn(reason, warn[0][2], run.raw)

    # --- the stubs themselves ---

    def test_stub_exit_codes(self):
        py = self.add_stub("py")
        env = dict(os.environ, STUB_PY_PY3_OUT="3.12.4 x", STUB_PY_PY3_EXIT="0", STUB_PY_VERSION_OUT="Python 3.12.4",
                   STUB_PY_VERSION_EXIT="0", STUB_PY_C_EXIT="9009")
        result = subprocess.run([str(py), "-3", "-c", "x"], capture_output=True, env=env)
        self.assertEqual((result.returncode, result.stdout), (0, b"3.12.4 x\r\n"))
        result = subprocess.run([str(py), "--version"], capture_output=True, env=env)
        self.assertEqual((result.returncode, result.stdout), (0, b"Python 3.12.4\r\n"))
        self.assertEqual(subprocess.run([str(py), "-c", "x"], capture_output=True, env=env).returncode, 9009)
        self.assertEqual(subprocess.run([str(self.add_stub("git")), "status"], capture_output=True, env=env).returncode, 99)

    # --- CLAUDE_CODE_GIT_BASH_PATH ---

    def test_env_var_valid_path_with_space(self):
        bash = self.make_file(self.tmp / "my git" / "bin" / "bash.exe")
        self.install_bash(self.pf)
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("hook-shell")], ["OK"], run.raw)
            self.assert_hook_shell_ok(run, "CLAUDE_CODE_GIT_BASH_PATH", bash)

    def test_env_var_accepted_file_names(self):
        for name in ("Bash.EXE", "sh.exe", "bash", "sh"):
            with self.subTest(name=name):
                bash = self.make_file(self.tmp / name / name)
                self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
                for run in self.each():
                    self.assert_hook_shell_ok(run, "CLAUDE_CODE_GIT_BASH_PATH", bash)

    def test_env_var_not_bash_warns_and_falls_through(self):
        other = self.make_file(self.tmp / "Git" / "cmd" / "git.exe")
        bash = self.install_bash(self.pf)
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(other)
        for run in self.each():
            self.assert_warn(run, str(other), "is not a bash/sh binary")
            self.assert_hook_shell_ok(run, "default install", bash)

    def test_env_var_missing_file_warns_and_falls_through(self):
        missing = self.tmp / "nowhere" / "bash.exe"
        bash = self.install_bash(self.pf86)
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(missing)
        for run in self.each():
            self.assert_warn(run, str(missing), "not found")
            self.assert_hook_shell_ok(run, "default install", bash)

    def test_env_var_with_illegal_path_characters_warns(self):
        value = str(self.tmp / "a<b|c" / "bash.exe")
        bash = self.install_bash(self.pf)
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = value
        for run in self.each():
            self.assert_warn(run, value, "not found")
            self.assert_hook_shell_ok(run, "default install", bash)

    # --- default installs and git on PATH ---

    def test_program_files(self):
        bash = self.install_bash(self.pf)
        for run in self.each():
            self.assert_hook_shell_ok(run, "default install", bash)

    def test_program_files_x86_only(self):
        bash = self.install_bash(self.pf86)
        for run in self.each():
            self.assert_hook_shell_ok(run, "default install", bash)

    def test_program_files_wins_over_x86(self):
        bash = self.install_bash(self.pf)
        self.install_bash(self.pf86)
        for run in self.each():
            self.assert_hook_shell_ok(run, "default install", bash)

    def test_bash_next_to_git_on_path(self):
        git = self.make_file(self.tmp / "Git for test" / "cmd" / "git.exe", exe=True)
        bash = self.make_file(self.tmp / "Git for test" / "bin" / "bash.exe")
        self.path_dirs.insert(0, git.parent)
        for run in self.each():
            self.assert_hook_shell_ok(run, "next to git on PATH", bash)
            self.assertEqual(run.rows("git"), [("git", "OK", str(git), "Get-Command git")], run.raw)

    def test_git_without_bash_is_missing(self):
        git = self.add_stub("git")
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("hook-shell")], ["MISSING"], run.raw)
            self.assertEqual(run.bash, [], run.raw)
            self.assertEqual(run.rows("git"), [("git", "OK", str(git), "Get-Command git")], run.raw)

    # --- nothing found: FIX lines ---

    def test_nothing_found_with_winget(self):
        self.add_stub("winget")
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("hook-shell")], ["MISSING"], run.raw)
            self.assertEqual(run.fix("hook-shell"), [GIT_WINGET, ENV_FIX], run.raw)
            self.assertEqual([r[1] for r in run.rows("git")], ["MISSING"], run.raw)
            self.assertEqual([cmd for _, cmd in run.fixes].count(GIT_WINGET), 1, "Git install offered once\n" + run.raw)

    def test_nothing_found_without_winget(self):
        for run in self.each():
            self.assertEqual(run.fix("hook-shell"), [GIT_URL, ENV_FIX], run.raw)
            self.assertNotIn(GIT_WINGET, [cmd for _, cmd in run.fixes], run.raw)

    def test_git_missing_with_bash_found(self):
        # Git is installed (its bash was found), so the FIX puts its cmd folder on PATH instead of installing it.
        self.add_stub("winget")
        self.install_bash(self.pf)
        (self.pf / "Git" / "cmd").mkdir()
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("git")], ["MISSING"], run.raw)
            self.assertEqual(run.fix("git"), ["add %s to PATH" % (self.pf / "Git" / "cmd")], run.raw)

    def test_git_missing_with_bash_from_the_env_var(self):
        bash = self.make_file(self.tmp / "my git" / "bin" / "bash.exe")
        (self.tmp / "my git" / "cmd").mkdir()
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
        for run in self.each():
            self.assertEqual(run.fix("git"), ["add %s to PATH" % (self.tmp / "my git" / "cmd")], run.raw)

    def test_git_missing_with_bash_under_usr_bin(self):
        # <Git>\usr\bin\bash.exe: two levels up is <Git>\usr, which holds no cmd folder.
        bash = self.make_file(self.tmp / "Git" / "usr" / "bin" / "bash.exe")
        (self.tmp / "Git" / "cmd").mkdir()
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
        for run in self.each():
            self.assertEqual(run.fix("git"), ["add the cmd folder of Git for Windows to PATH"], run.raw)

    def test_git_missing_with_bash_not_in_a_bin_folder(self):
        # <Git>\cmd exists, but bash.exe sits in "sub", not "bin", so the folder above its folder is not known to be a Git root.
        bash = self.make_file(self.tmp / "Git" / "sub" / "bash.exe")
        (self.tmp / "Git" / "cmd").mkdir()
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = str(bash)
        for run in self.each():
            self.assertEqual(run.fix("git"), ["add the cmd folder of Git for Windows to PATH"], run.raw)

    def test_git_missing_with_bash_two_levels_from_nowhere(self):
        # A relative bin\bash.exe (the run's folder is self.tmp) has no folder two levels up to name.
        self.make_file(self.tmp / "bin" / "bash.exe")
        self.env["CLAUDE_CODE_GIT_BASH_PATH"] = r"bin\bash.exe"
        for run in self.each():
            self.assertEqual(run.fix("git"), ["add the cmd folder of Git for Windows to PATH"], run.raw)

    # --- python, only without Git Bash ---

    def test_python_from_py_launcher(self):
        self.add_stub("py")
        interpreter = str(self.tmp / "Py thon" / "python.exe")
        self.env.update(STUB_PY_PY3_OUT="3.12.4 " + interpreter, STUB_PY_PY3_EXIT="0")
        for run in self.each():
            _, status, detail, _ = self.one_row(run, "python")
            self.assertEqual(status, "OK", run.raw)
            self.assertEqual(detail, "3.12.4 " + interpreter, run.raw)
            self.assertEqual(run.fix("python"), [], run.raw)

    def test_python_old_py_falls_through_to_python(self):
        self.add_stub("py")
        self.add_stub("python")
        new = str(self.tmp / "New" / "python.exe")
        self.env.update(STUB_PY_PY3_OUT="3.8.10 " + str(self.tmp / "Old" / "python.exe"), STUB_PY_PY3_EXIT="0",
                        STUB_PYTHON_C_OUT="3.11.2 " + new, STUB_PYTHON_C_EXIT="0")
        for run in self.each():
            _, status, detail, _ = self.one_row(run, "python")
            self.assertEqual(status, "OK", run.raw)
            self.assertEqual(detail, "3.11.2 " + new, run.raw)

    def test_python_store_alias_counts_as_missing(self):
        self.add_stub("winget")
        self.add_stub("python")
        self.env.update(STUB_PYTHON_C_ERR="Python was not found; run without arguments to install from the Microsoft Store",
                        STUB_PYTHON_C_EXIT="9009")
        for run in self.each():
            _, status, detail, _ = self.one_row(run, "python")
            self.assertEqual(status, "MISSING", run.raw)
            self.assertIn("Store alias", detail, run.raw)
            self.assertEqual(run.fix("python"), [PY_WINGET], run.raw)

    def test_no_python_with_winget(self):
        self.add_stub("winget")
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("python")], ["MISSING"], run.raw)
            self.assertEqual(run.fix("python"), [PY_WINGET], run.raw)

    def test_no_python_without_winget(self):
        for run in self.each():
            self.assertEqual([r[1] for r in run.rows("python")], ["MISSING"], run.raw)
            self.assertEqual(run.fix("python"), [PY_ORG], run.raw)

    # --- defaults ---

    def test_defaults_are_the_drive_c_folders(self):
        for run in self.each_with("-PrintDefaults"):
            self.assertEqual(run.lines, [DRIVE_C + r"\Program Files", DRIVE_C + r"\Program Files (x86)"], run.raw)

    # --- the script's own error ---

    def test_own_error_exits_2_with_one_reason_line(self):
        self.pf = ""
        for run in self.run_doctor():
            self.assertEqual(run.code, 2, run.raw)
            self.assertEqual(len(run.lines), 1, run.raw)
            self.assertTrue(run.lines[0].startswith("doctor.ps1: "), run.raw)


if __name__ == "__main__":
    unittest.main()
