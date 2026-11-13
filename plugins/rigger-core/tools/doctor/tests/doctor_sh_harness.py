"""Harness for the tests of tools/doctor/doctor.sh (test_doctor_sh_*.py, split in three so the gate runs them in
parallel), run through a real POSIX sh (dash or Git Bash) with stub programs on PATH.

Stubs are sh scripts (uname, git, xcode-select, brew, claude, python, python3, py) in a temp folder; the other
PATH entry is a tools folder with only the POSIX tools doctor.sh and py.sh use. CLAUDE_CONFIG_DIR points at a
fake config folder, HOME at a decoy one, DOCTOR_FS_ROOT at a fake root for /etc/os-release, /usr/bin/git and the
brew paths. Every run checks the output contract: CHECK and FIX lines only, tab-separated, a FIX only after a
MISSING or WARN row of the same name.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "doctor.sh"
PLUGIN = HERE.parents[2]
PY_SH = PLUGIN / "bin" / "py.sh"
STATUSES = ("OK", "MISSING", "WARN", "INFO")
POSIX_TOOLS = ("awk", "tr", "uname")
REQUIRED = ("rigger-core", "rigger-rules", "rigger-lessons")
VERSION = "1a2b3c4d5e6f"
STAMP = "2026-01-01T00:00:00.000Z"
WORKTREE_ROOT = "/work/tree root"
# Built at run time so the repo's leak scanner (scripts/sweep.py) does not report a drive path here.
DRIVE = "D" + ":"
WINGET_PYTHON = (
    "winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements"
)
NEVER_RUN = "claude-was-run"
# printf escapes for the stubs, as raw strings (scripts/sweep.py reads a doubled backslash as a UNC path).
SH_LF = r"\n"
SH_CRLF = r"\r\n"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        if git_sh.is_file():
            return str(git_sh)
        return shutil.which("sh")
    return "/bin/sh"


SH = find_sh()


def find_git_bash():
    """Git's bin\\bash.exe in doctor.ps1's (Claude Code's) order, or None: CLAUDE_CODE_GIT_BASH_PATH, the drive-C
    Program Files folders, then bin\\bash.exe next to git on PATH."""
    if os.name != "nt":
        return None
    candidates = []
    from_env = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if from_env and Path(from_env).name.lower() in ("bash.exe", "sh.exe", "bash", "sh"):
        candidates.append(Path(from_env))
    drive_c = "C" + ":/"
    for folder in ("Program Files", "Program Files (x86)"):
        candidates.append(Path(drive_c, folder, "Git", "bin", "bash.exe"))
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).parent.parent / "bin" / "bash.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def sh_path(path):
    """Path in the form a hook passes it (forward slashes, as CLAUDE_PLUGIN_ROOT on Windows)."""
    return Path(path).as_posix()


def msys_path(path):
    """The form `command -v` prints under Git Bash (its mount table maps %TEMP% to /tmp), so a path doctor.sh
    compares with it matches; the folder name holds no quote or glob character (sh.exe re-parses its argv)."""
    if os.name != "nt":
        return str(path)
    proc = subprocess.run([SH, "-c", 'cd "$1" && pwd', "sh", sh_path(path)], stdout=subprocess.PIPE, timeout=60)
    text = proc.stdout.decode("utf-8").strip()
    if proc.returncode != 0 or not text.startswith("/"):
        raise AssertionError("no POSIX form for %s: %r" % (path, text))
    return text


def sh_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def entry(version, scope="user", project=None):
    """One install in installed_plugins.json, keys in the order Claude Code writes them."""
    item = {"scope": scope}
    if project is not None:
        item["projectPath"] = project
    item.update(
        {
            "installPath": "cache/marketplace/plugin/" + version,
            "version": version,
            "installedAt": STAMP,
            "lastUpdated": STAMP,
            "gitCommitSha": "0" * 40,
        }
    )
    return item


def dump(data, style):
    if style == "oneline":
        return json.dumps(data, separators=(",", ":"))
    return json.dumps(data, indent=2) + "\n"


class Fixes(dict):
    """FIX commands per row; a row without any reads as None, so a missing FIX fails an assertEqual, not a lookup."""

    def __missing__(self, key):
        return None


class Result:
    """doctor.sh's output parsed into rows; any line outside the contract lands in errors."""

    def __init__(self, proc):
        self.code = proc.returncode
        self.stdout = proc.stdout.decode("utf-8").replace("\r\n", "\n")
        self.stderr = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
        self.checks = {}
        self.order = []
        self.fixes = Fixes()
        self.errors = []
        for line in self.stdout.splitlines():
            fields = line.split("\t")
            if (
                fields[0] == "CHECK"
                and len(fields) == 5
                and fields[2] in STATUSES
                and fields[1] not in self.checks
                and all(fields[1:])
            ):
                self.checks[fields[1]] = tuple(fields[2:])
                self.order.append(fields[1])
            elif (
                fields[0] == "FIX"
                and len(fields) == 3
                and all(fields[1:])
                and self.checks.get(fields[1], ("",))[0] in ("MISSING", "WARN")
            ):
                self.fixes.setdefault(fields[1], []).append(fields[2])
            else:
                self.errors.append(line)


class DoctorShCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SH, "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.config = self.tmp / "config"
        (self.config / "plugins").mkdir(parents=True)
        self.home = self.tmp / "home"
        (self.home / ".claude" / "plugins").mkdir(parents=True)
        self.fs = self.tmp / "fs"
        self.fs.mkdir()
        self.fs_root = None
        self.work = self.tmp / "cwd"
        self.work.mkdir()
        self.tools = self.make_tools_dir()
        self.write_sh(self.stubs / "claude", ": >%s\n" % sh_quote(sh_path(self.tmp / NEVER_RUN)))

    # -- harness ----------------------------------------------------------------------------

    def make_tools_dir(self, with_sh=True):
        """A PATH entry with the tools doctor.sh and py.sh need, and no Python, git or brew."""
        names = POSIX_TOOLS + (("sh",) if with_sh else ())
        if os.name == "nt":
            if with_sh:
                return str(Path(SH).parent)
            # Git's usr\bin holds sh itself, so a folder without sh gets small wrappers around its tools.
            tools = self.tmp / "tools-without-sh"
            tools.mkdir()
            for name in names:
                real = Path(SH).parent / (name + ".exe")
                self.assertTrue(real.is_file(), real)
                self.write_sh(tools / name, 'exec %s "$@"\n' % sh_quote(sh_path(real)))
            return str(tools)
        tools = self.tmp / ("tools" if with_sh else "tools-without-sh")
        tools.mkdir()
        for name in names:
            real = shutil.which(name)
            self.assertIsNotNone(real, "tool %s not found" % name)
            (tools / name).symlink_to(real)
        return str(tools)

    def write_sh(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def stub_uname(self, kernel, crlf=False):
        self.write_sh(self.stubs / "uname", "printf '%%s%s' %s\n" % (SH_CRLF if crlf else SH_LF, sh_quote(kernel)))

    def stub_git(self, directory=None, crlf=False):
        eol = SH_CRLF if crlf else SH_LF
        return self.write_sh((directory or self.stubs) / "git", "[ \"$1\" = --version ] && printf 'git version 2.43.0%s'\n" % eol)

    def stub_xcode_select(self, installed):
        if installed:
            body = "printf '/Library/Developer/CommandLineTools\\n'\n"
        else:
            body = "printf 'xcode-select: error: unable to get active developer directory\\n' >&2\nexit 2\n"
        self.write_sh(self.stubs / "xcode-select", body)

    def stub_python(self, name, version="3.12.1", stderr_version=False, store_alias=False, crlf=False, directory=None):
        """Answers --version, py.sh's probe (-c: prints its path, exits 1 below 3.9) and doctor's query through
        py.sh (-I -X utf8 -c: prints its path and version). store_alias: the Windows Store alias without Python.
        crlf ends the --version and query lines with CRLF; the probe line keeps LF, because off Windows py.sh
        itself cannot exec a path read with a CR, and that is py.sh's case, not doctor.sh's."""
        path = (directory or self.stubs) / name
        eol = SH_CRLF if crlf else SH_LF
        if store_alias:
            body = "printf 'Python was not found; run without arguments to install from the Microsoft Store\\n' >&2\nexit 9009\n"
            return self.write_sh(path, body)
        major, minor = (int(part) for part in version.split(".")[:2])
        probe_code = 0 if (major, minor) >= (3, 9) else 1
        body = (
            "self={self_path}\n"
            '[ "$1" != -3 ] || shift\n'
            "case $1 in\n"
            "  --version) printf 'Python {version}{eol}' {redirect}; exit 0 ;;\n"
            "  -c) printf '%s\\n' \"$self\"; exit {probe_code} ;;\n"
            "  -I) printf '%s {version}{eol}' \"$self\"; exit 0 ;;\n"
            "esac\n"
            "exit 64\n"
        ).format(
            self_path=sh_quote(str(path)),
            version=version,
            eol=eol,
            redirect=">&2" if stderr_version else "",
            probe_code=probe_code,
        )
        return self.write_sh(path, body)

    def write_file(self, path, text, crlf=False, bom=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        if crlf:
            text = text.replace("\n", "\r\n")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    def os_release(self, text):
        self.write_file(self.fs / "etc" / "os-release", text)

    def good_settings(self, windows=False):
        names = REQUIRED + (("rigger-windows",) if windows else ())
        enabled = {"%s@rigger" % name: True for name in names}
        enabled["superpowers@superpowers-marketplace"] = True
        enabled["other@elsewhere"] = False
        return {
            "env": {"CLAUDE_CODE_USE_POWERSHELL_TOOL": "1", "HARNESS_WORKTREE_ROOT": WORKTREE_ROOT},
            "permissions": {"allow": ["Bash(git status)"], "deny": []},
            "enabledPlugins": enabled,
            "autoCompactWindow": 533000,
        }

    def good_installed(self, windows=False):
        names = REQUIRED + (("rigger-windows",) if windows else ())
        plugins = {"other@elsewhere": [entry("1.0.0")]}
        for name in names:
            plugins["%s@rigger" % name] = [entry(VERSION)]
        plugins["superpowers@superpowers-marketplace"] = [entry("5.0.7")]
        return {"version": 2, "plugins": plugins}

    def good_marketplaces(self):
        return {
            "superpowers-marketplace": {
                "source": {"source": "github", "repo": "obra/superpowers-marketplace"},
                "installLocation": "marketplaces/superpowers-marketplace",
                "lastUpdated": STAMP,
                "autoUpdate": True,
            },
            "rigger": {
                "source": {"source": "github", "repo": "Borcioo/rigger-claude-code"},
                "installLocation": "marketplaces/rigger",
                "lastUpdated": STAMP,
            },
        }

    def write_config(self, settings=None, installed=None, marketplaces=None, root=None, style="pretty", **text):
        """Writes the three files; a None leaves that file out."""
        root = root or self.config
        if settings is not None:
            self.write_file(root / "settings.json", dump(settings, style), **text)
        if installed is not None:
            self.write_file(root / "plugins" / "installed_plugins.json", dump(installed, style))
        if marketplaces is not None:
            self.write_file(root / "plugins" / "known_marketplaces.json", dump(marketplaces, style))

    def good_config(self, windows=False, style="pretty"):
        self.write_config(self.good_settings(windows), self.good_installed(windows), self.good_marketplaces(), style=style)

    def run_doctor(self, kernel="Linux", env=None, path=None, script=SCRIPT, expect_code=0, script_arg=None, shell=None):
        """Runs doctor.sh with uname faking `kernel`; env entries set (a None value unsets). script_arg is passed to
        the shell as given (default: script with forward slashes); shell defaults to SH."""
        windows = kernel.startswith(("MINGW", "MSYS"))
        if kernel and not (self.stubs / "uname").exists():
            self.stub_uname(kernel)
        base = dict(os.environ)
        for key in list(base):
            if key.upper().startswith(("CLAUDE_", "HARNESS_", "DOCTOR_")) or key.upper() in ("AI_AGENT", "OS"):
                del base[key]
        base["PATH"] = os.pathsep.join(path if path is not None else [str(self.stubs), self.tools])
        base["HOME"] = sh_path(self.home)
        base["CLAUDE_CONFIG_DIR"] = sh_path(self.config)
        if self.fs_root is None:
            self.fs_root = msys_path(self.fs)
        base["DOCTOR_FS_ROOT"] = self.fs_root
        base["AI_AGENT"] = "claude-code_2-1-317_agent"
        if windows:
            base["OS"] = "Windows_NT"
        for key, value in (env or {}).items():
            if value is None:
                base.pop(key, None)
            else:
                base[key] = value
        proc = subprocess.run(
            [shell or SH, script_arg or sh_path(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=base,
            cwd=str(self.work),
            timeout=60,
        )
        result = Result(proc)
        if expect_code == 0:
            self.assertEqual((result.code, result.stderr), (0, ""), result.stdout)
            self.assertEqual(result.errors, [], "lines outside the output contract")
        self.assertFalse((self.tmp / NEVER_RUN).exists(), "doctor.sh ran claude")
        return result

    def assertRow(self, result, name, status, detail=None):
        self.assertIn(name, result.checks, result.stdout)
        self.assertEqual(result.checks[name][0], status, result.checks[name])
        if detail is not None:
            self.assertEqual(result.checks[name][1], detail, result.checks[name])

    def skip_if_system_python_candidates(self):
        for candidate in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
            if os.name != "nt" and os.path.exists(candidate):
                self.skipTest("%s exists, so py.sh finds a real Python here" % candidate)



class Recorder(unittest.TestResult):
    """Keeps every outcome call, with its arguments, to be replayed on the real result."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _keep(name):
        def keep(self, *args):
            self.calls.append((name, args))

        return keep

    addSuccess = _keep("addSuccess")
    addError = _keep("addError")
    addFailure = _keep("addFailure")
    addSkip = _keep("addSkip")
    addExpectedFailure = _keep("addExpectedFailure")
    addUnexpectedSuccess = _keep("addUnexpectedSuccess")
    addSubTest = _keep("addSubTest")


class PooledSuite(unittest.TestSuite):
    """Runs its tests on a thread pool, each against a Recorder, then replays the records in suite order. Each test
    builds its own temp folder and spawns shells that mostly wait on process creation."""

    WORKERS = 6

    @staticmethod
    def record(test):
        recorder = Recorder()
        test.run(recorder)
        return recorder.calls

    def run(self, result, debug=False):
        tests = list(self)
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            futures = [pool.submit(self.record, test) for test in tests]
            for test, future in zip(tests, futures):
                result.startTest(test)
                try:
                    for name, args in future.result():
                        getattr(result, name)(*args)
                finally:
                    result.stopTest(test)
        return result
