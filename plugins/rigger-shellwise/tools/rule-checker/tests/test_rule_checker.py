"""Tests of tools/rule-checker/rule_checker.py, the rule checker the lessons lint loads for `live:<rule>` guards.

The binary is a stub named on the line of `<plugin data>/shellwise/current`: on POSIX a real `#!/bin/sh` script
run as itself; on Windows a Python script stored under the binary's name and run through `launch` (an argv prefix
of sys.executable), as the installer's tests do. The stub appends its argv and stdin to files next to it and
prints the reply set for its `--shell`. The replies are `check --json` stdout captured from the v0.2.0 binary
(a too-long fix names a per-user temp file; that path is shortened to `<temp>/command.sh`).
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
import rule_checker as rc  # noqa: E402

WINDOWS = os.name == "nt"
LAUNCH = (lambda path: [sys.executable, "-I", path]) if WINDOWS else None
CONFIG = os.path.join(str(PLUGIN), "shellwise.toml")
ENV_KEYS = ("CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR", "HOME", "USERPROFILE")

# The 54 rule ids of shellwise v0.2.0 (its docs/rules/ folder; the same as v0.1.1's).
PINNED = """
analysis-incomplete bash-backslash-collapse bash-cd-slash-d bash-crlf bash-msys-path-mangle
bash-ps-call-operator bash-ps-cmdlet bash-unquoted-backslash-path bash-vendor-bin-shebang
bash-versioned-php bat-metachar claude-cmd-live-config cli-inline-prose delete-allowed-root
delete-critical delete-empty-expansion delete-outside-project delete-repo-contents
delete-through-link delete-unknown-location dynamic-target follow-unbounded gate-truncated-pipeline
git-discard git-no-verify git-restore-discards git-unreadable git-worktree-junction
inline-script-too-long opaque-destructive parse-error-destructive parse-error ps-alias-shadow
ps-auto-variable ps-bad-parameter ps-bare-bash ps-dotnet-relative-path ps-empty-arg
ps-git-commit-stdin ps-kill-wrapper ps-native-nonewline ps-remove-item-star ps-stdin-script
ps-unix-command ps-var-colon pwsh-file-list-arg rg-follow-L stale-write-var too-long
wait-loop-unbounded walk-timeout walk-unreadable will-hang wsl-argv-rejoin
""".split()

# Real `check --json` replies of v0.2.0 (one line each, as printed).
CD_SLASH_D = (r'{"version":1,"level":"break","findings":[{"rule":"bash-cd-slash-d","level":"break","message":'
              r'"`cd /d` is cmd.exe syntax; bash reads `/d` as the folder and fails with too many arguments",'
              r'"fix":"cd \"x\"","span":{"start":3,"end":5}}]}')
PS_CMDLET = (r'{"version":1,"level":"break","findings":[{"rule":"bash-ps-cmdlet","level":"break","message":'
             r'"`Get-ChildItem` is a PowerShell cmdlet, but this is the Bash tool (Git Bash)",'
             r'"fix":"run this in the PowerShell tool","span":{"start":0,"end":13}}]}')
TOO_LONG = (r'{"version":1,"level":"break","findings":[{"rule":"too-long","level":"break","message":'
            r'"the command is 8005 characters once the Bash tool escapes it, at or over the Git Bash limit of 7500: '
            r'Git Bash would cut it, so it would fail or lose its end","fix":"write the command to <temp>/command.sh '
            r'with the Write tool, run shellwise lint <temp>/command.sh, then run bash <temp>/command.sh",'
            r'"span":{"start":0,"end":8005}}]}')
INLINE_TOO_LONG = (r'{"version":1,"level":"break","findings":[{"rule":"inline-script-too-long","level":"break",'
                   r'"message":"the command is 1 lines and 8005 characters; a script over 100 lines or 8000 characters '
                   r'written inline is never linted before it runs","fix":"write the command to <temp>/command.sh with '
                   r'the Write tool, run shellwise lint <temp>/command.sh, then run bash <temp>/command.sh",'
                   r'"span":{"start":0,"end":8005}}]}')
QUOTE_BREAK = (r'{"version":1,"level":"break","findings":[{"rule":"parse-error","level":"break","message":'
               r'"bash: unterminated single quote at 1,6: the '"'"r' opened at column 6 is never closed, so the shell '
               r'refuses the whole command and runs none of it","fix":"close the '"'"r' opened at column 6, or write '
               r'the text to a file and pass the file","span":{"start":5,"end":9}}]}')
PARSE_WARN = (r'{"version":1,"level":"warn","findings":[{"rule":"parse-error","level":"warn","message":'
              r'"PowerShell syntax error at column 1","fix":"correct the syntax at column 1 (PowerShell syntax error)",'
              r'"span":{"start":0,"end":14}}]}')
NO_VERIFY_DANGER = (r'{"version":1,"level":"danger","findings":[{"rule":"git-no-verify","level":"danger","message":'
                    r'"`git commit` with `--no-verify` skips the repository' "'" r's hooks, which the project' "'"
                    r's rules forbid unless the owner asked for it","fix":"run the same `git commit` without '
                    r'`--no-verify` and fix what the hook reports","span":{"start":0,"end":27}}]}')
DESTRUCTIVE_DANGER = (r'{"version":1,"level":"danger","findings":[{"rule":"parse-error-destructive","level":"danger",'
                      r'"message":"bash: unterminated expansion at column 1; the text also names a command that '
                      r'deletes or discards data","fix":"correct the syntax at column 1 (bash: unterminated expansion)",'
                      r'"span":{"start":0,"end":16}}]}')
# `php83 -v && cd /d "x"` at defaults: the reply's own level is break, but php's finding is a warn.
PHP_WARN_CD_BREAK = (r'{"version":1,"level":"break","findings":[{"rule":"bash-versioned-php","level":"warn",'
                     r'"message":"`php83` is a Windows launcher script (.bat), which Git Bash runs by that name only '
                     r'when a shell profile defines it","fix":"call the php.exe of that version by its full path",'
                     r'"span":{"start":0,"end":5}},{"rule":"bash-cd-slash-d","level":"break","message":"`cd /d` is '
                     r'cmd.exe syntax; bash reads `/d` as the folder and fails with too many arguments",'
                     r'"fix":"cd \"x\"","span":{"start":15,"end":17}}]}')
CLEAN = '{"version":1,"level":"ok","findings":[]}'

STUB_SH = """#!/bin/sh
shell=
prev=
for arg in "$@"; do
  [ "$prev" = --shell ] && shell=$arg
  prev=$arg
  printf '%s\\n' "$arg" >>"$0.argv"
done
printf -- '--\\n' >>"$0.argv"
cat >>"$0.stdin"
printf -- '--\\n' >>"$0.stdin"
cat "$0.$shell.reply"
"""
STUB_PY = """import sys
args = sys.argv[1:]
shell = args[args.index("--shell") + 1] if "--shell" in args else ""
with open(sys.argv[0] + ".argv", "a", encoding="utf-8", newline="\\n") as fh:
    fh.write("".join(a + "\\n" for a in args) + "--\\n")
with open(sys.argv[0] + ".stdin", "ab") as fh:
    fh.write(sys.stdin.buffer.read() + b"--\\n")
with open(sys.argv[0] + "." + shell + ".reply", "rb") as fh:
    sys.stdout.buffer.write(fh.read())
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rule-checker-"))
        self.root = self.tmp / "data root with space"  # stands in for <config dir>/plugins/data
        self.data = self.root / "rigger-shellwise-rigger"
        self.binary = self.data / "shellwise" / "v0.1.1" / "t" / ("shellwise.exe" if WINDOWS else "shellwise")
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes((STUB_PY if WINDOWS else STUB_SH).encode("utf-8"))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.write_current(str(self.binary) + "\n")
        # a fake config dir and home: the real ones are never read
        self.saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "config")
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(self.tmp / "home")

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def write_current(self, text, data=None):
        path = (data or self.data) / "shellwise" / "current"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def reply(self, shell, text):
        Path(str(self.binary) + "." + shell + ".reply").write_bytes((text + "\n").encode("utf-8"))

    def check(self, rule, tools, probe="cmd", windows=True, **kw):
        kw.setdefault("data_dir", str(self.data))
        return rc.rule_guard_problem(rule, {"tools": tools, "probe": probe}, launch=LAUNCH, windows=windows, **kw)

    def calls(self):
        """The argv lists the stub received, one per run."""
        path = Path(str(self.binary) + ".argv")
        text = path.read_bytes().decode("utf-8") if path.is_file() else ""
        return [call.split("\n")[:-1] for call in text.split("--\n")[:-1]]

    def stdins(self):
        return Path(str(self.binary) + ".stdin").read_bytes().split(b"--\n")[:-1]


class RulesTests(unittest.TestCase):
    def test_rules_match_the_pin(self):
        self.assertEqual(len(PINNED), 54)
        self.assertIsInstance(rc.RULES, frozenset)
        self.assertEqual(sorted(rc.RULES), sorted(PINNED))
        self.assertEqual(sorted(set(rc.ALIASES.values()) - set(PINNED)), [])


class VerdictTests(Base):
    def test_probe_denied_at_break_is_none(self):
        self.reply("gitbash", CD_SLASH_D)
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.data)  # found the way the launcher finds it
        self.assertIsNone(self.check("bash-cd-slash-d", ["git-bash"], probe='cd /d "x"', data_dir=None))
        self.assertEqual(self.calls(), [["check", "--shell", "gitbash", "--json", "--config", CONFIG]])
        self.assertEqual(self.stdins(), [b'cd /d "x"'])

    def test_danger_is_none(self):
        for rule, reply in (("git-no-verify", NO_VERIFY_DANGER), ("parse-error-destructive", DESTRUCTIVE_DANGER)):
            with self.subTest(rule=rule):
                self.reply("gitbash", reply)
                self.assertIsNone(self.check(rule, ["git-bash"]))

    def test_warn_only_is_a_problem(self):
        self.reply("pwsh", PARSE_WARN)
        self.assertEqual(self.check("parse-error", ["powershell"]),
                         "guard live:parse-error: shellwise check does not deny the probe with parse-error at break "
                         "or above (pwsh: parse-error warn)")
        # the reply's own level is break, from another finding: the asked rule's finding decides
        self.reply("gitbash", PHP_WARN_CD_BREAK)
        self.assertEqual(self.check("bash-versioned-php", ["git-bash"]),
                         "guard live:bash-versioned-php: shellwise check does not deny the probe with "
                         "bash-versioned-php at break or above (gitbash: bash-versioned-php warn, bash-cd-slash-d break)")

    def test_another_rules_finding_is_a_problem(self):
        rows = (("bash-ps-cmdlet", CD_SLASH_D, "bash-cd-slash-d break"),
                ("parse-error", DESTRUCTIVE_DANGER, "parse-error-destructive danger"),
                ("too-long", INLINE_TOO_LONG, "inline-script-too-long break"),
                ("bash-cd-slash-d", CLEAN, "no finding"))
        for rule, reply, found in rows:
            with self.subTest(rule=rule, found=found):
                self.reply("gitbash", reply)
                self.assertEqual(self.check(rule, ["git-bash"]),
                                 "guard live:%s: shellwise check does not deny the probe with %s at break or above "
                                 "(gitbash: %s)" % (rule, rule, found))

    def test_old_ids_route_through_aliases(self):
        rows = (("bash-powershell-syntax", "bash-ps-cmdlet", PS_CMDLET),
                ("bash-too-long", "too-long", TOO_LONG),
                ("bash-unterminated-quote", "parse-error", QUOTE_BREAK))
        for old, new, reply in rows:
            with self.subTest(old=old):
                self.reply("gitbash", reply)
                self.assertIsNone(self.check(old, ["git-bash"]))
                self.reply("gitbash", CD_SLASH_D)
                self.assertEqual(self.check(old, ["git-bash"]),
                                 "guard live:%s: shellwise check does not deny the probe with %s at break or above "
                                 "(gitbash: bash-cd-slash-d break)" % (old, new))

    def test_tools_pick_the_shell(self):
        rows = ((["powershell"], True, ["pwsh"]), (["git-bash"], False, ["gitbash"]),
                (["bash"], True, ["gitbash"]), (["bash"], False, ["bash"]),
                (["git", "bash", "git-bash"], True, ["gitbash"]))
        for tools, windows, shells in rows:
            with self.subTest(tools=tools, windows=windows):
                Path(str(self.binary) + ".argv").unlink(missing_ok=True)
                for shell in ("pwsh", "gitbash", "bash"):
                    self.reply(shell, CLEAN)
                self.check("bash-cd-slash-d", tools, windows=windows)
                self.assertEqual([call[2] for call in self.calls()], shells)
        # any shell the case names may confirm it
        self.reply("pwsh", CLEAN)
        self.reply("gitbash", CD_SLASH_D)
        self.assertIsNone(self.check("bash-cd-slash-d", ["powershell", "git-bash"]))

    def test_case_without_a_probe_or_a_shell_is_a_problem(self):
        self.reply("gitbash", CD_SLASH_D)
        self.assertEqual(self.check("bash-cd-slash-d", ["git-bash"], probe=" "),
                         "guard live:bash-cd-slash-d: needs a probe: a command of the failure shape that the hook denies")
        self.assertEqual(self.check("bash-cd-slash-d", ["composer"]),
                         "guard live:bash-cd-slash-d: tools names no shell (powershell, bash, git-bash) to run the "
                         "probe in")
        self.assertEqual(self.calls(), [])

    def test_unreadable_reply_is_a_problem(self):
        for reply in ("shellwise: error: unexpected argument", '{"version":1,"findings":{}}', "[]",
                      '{"findings":[{"rule":"bash-cd-slash-d"}]}', '{"findings":["bash-cd-slash-d"]}'):
            with self.subTest(reply=reply):
                self.reply("gitbash", reply)
                problem = self.check("bash-cd-slash-d", ["git-bash"])
                self.assertTrue(problem.startswith("guard live:bash-cd-slash-d: shellwise check gave no readable "
                                                   "reply (gitbash: exit 0"), problem)


class NoBinaryTests(Base):
    def test_no_binary_is_no_checker(self):
        rows = (("no data folder", None, None),
                ("no current", "", None),
                ("empty current", None, "\n"),
                ("current names a missing file", None, str(self.tmp / "gone" / "shellwise") + "\n"))
        for name, data, current in rows:
            with self.subTest(name):
                if current is not None:
                    self.write_current(current)
                if data == "":
                    (self.data / "shellwise" / "current").unlink()
                try:
                    problem = rc.rule_guard_problem("bash-cd-slash-d", {"tools": ["git-bash"], "probe": "x"},
                                                    data_dir=str(self.data) if name != "no data folder" else None,
                                                    launch=LAUNCH, windows=True)
                except Exception as exc:  # a missing binary is "no checker", never a crash of the lint
                    problem = exc
                self.assertIsInstance(problem, str)
                self.assertTrue(problem.startswith("guard live:bash-cd-slash-d: no checker (no shellwise binary: "),
                                problem)
        self.assertEqual(self.calls(), [])

    def problem_with(self, launch):
        try:
            return rc.rule_guard_problem("bash-cd-slash-d", {"tools": ["git-bash"], "probe": "x"},
                                         data_dir=str(self.data), launch=launch, windows=True)
        except Exception as exc:  # every failure is a problem line, never a crash of the lint
            return exc

    def test_a_binary_that_does_not_answer_in_time_is_a_problem(self):
        slow = "import time\ntime.sleep(20)\n" if WINDOWS else "#!/bin/sh\nexec sleep 20\n"
        self.binary.write_bytes(slow.encode("utf-8"))
        with mock.patch.object(rc, "CHECK_BOUND", 1.0):
            started = time.monotonic()
            problem = self.problem_with(LAUNCH)
        self.assertLess(time.monotonic() - started, 10.0)
        self.assertIsInstance(problem, str)
        self.assertEqual(problem, "guard live:bash-cd-slash-d: shellwise check gave no readable reply "
                                  "(gitbash: timed out after 1 s)")

    def test_a_binary_the_system_cannot_run_is_a_problem(self):
        self.binary.write_bytes(b"\x00 not a program")
        problem = self.problem_with(None)  # run as itself: no interpreter in front
        self.assertIsInstance(problem, str)
        self.assertTrue(problem.startswith("guard live:bash-cd-slash-d: shellwise check gave no readable reply "
                                           "(gitbash: cannot run ("), problem)

    @unittest.skipIf(WINDOWS, "POSIX only: Windows has no exec bit")
    def test_a_binary_without_the_exec_bit_is_no_checker(self):
        self.binary.chmod(0o644)
        problem = self.problem_with(None)
        self.assertEqual(problem, "guard live:bash-cd-slash-d: no checker (no shellwise binary: %s is not an "
                                  "executable file)" % self.binary)

    def test_current_with_crlf_names_the_binary(self):
        self.write_current(str(self.binary) + "\r\n")
        self.reply("gitbash", CD_SLASH_D)
        self.assertIsNone(self.check("bash-cd-slash-d", ["git-bash"]))


class DataFolderTests(Base):
    """The checker finds rigger-shellwise's data folder itself: the lint runs in another plugin's process."""

    def resolve(self):
        if self.binary.parent.is_dir():
            self.reply("gitbash", CD_SLASH_D)
        try:
            return self.check("bash-cd-slash-d", ["git-bash"], data_dir=None)
        except Exception as exc:  # no folder, or several, is "no checker", never a crash of the lint
            return exc

    def sibling(self, name, current=True):
        """Another data folder under the data root; with `current`, naming a file that is not there."""
        folder = self.root / name
        folder.mkdir()
        if current:
            self.write_current(str(self.tmp / "gone" / "shellwise") + "\n", folder)
        return folder

    def test_own_data_folder_is_used(self):
        self.sibling("rigger-shellwise-inline")  # a second candidate the scan would refuse
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.data)
        self.assertIsNone(self.resolve())

    def test_one_shellwise_folder_beside_another_plugins_data(self):
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.sibling("rigger-lessons-rigger", current=False))
        self.assertIsNone(self.resolve())

    def test_inline_name_is_found(self):
        inline = self.root / "rigger-shellwise-inline"
        self.data.rename(inline)
        self.write_current(str(inline / self.binary.relative_to(self.data)) + "\n", inline)
        self.binary = inline / self.binary.relative_to(self.data)
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.sibling("rigger-lessons-inline", current=False))
        self.assertIsNone(self.resolve())

    def test_config_dir_then_home_hold_the_data_root(self):
        for name, base in (("CLAUDE_CONFIG_DIR", self.tmp / "config"), ("home", self.tmp / "home" / ".claude")):
            with self.subTest(name):
                if name == "home":
                    os.environ.pop("CLAUDE_CONFIG_DIR")
                (base / "plugins").mkdir(parents=True)
                shutil.move(str(self.root), str(base / "plugins" / "data"))
                self.root, self.data = base / "plugins" / "data", base / "plugins" / "data" / self.data.name
                self.binary = self.data / "shellwise" / "v0.1.1" / "t" / self.binary.name
                self.write_current(str(self.binary) + "\n")
                self.assertIsNone(self.resolve())

    def test_none_or_two_folders_are_no_checker(self):
        os.environ["CLAUDE_PLUGIN_DATA"] = str(self.sibling("rigger-lessons-rigger", current=False))
        self.sibling("rigger-shellwise-x", current=False)  # no shellwise/current: not a candidate
        self.sibling("rigger-shellwise-inline")
        two = self.resolve()
        shutil.rmtree(str(self.root / "rigger-shellwise-inline"))
        shutil.rmtree(str(self.data))
        none = self.resolve()
        for problem, count in ((two, 2), (none, 0)):
            with self.subTest(count=count):
                self.assertIsInstance(problem, str)
                self.assertEqual(problem, "guard live:bash-cd-slash-d: no checker (no shellwise binary: %d "
                                          "rigger-shellwise-* folders with shellwise/current in %s)"
                                 % (count, self.root))
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
