"""The transcript reader against records shaped like real ones. Each case has a twin that goes the other way.

python -I -X utf8 test_turn_evidence.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
sys.path.insert(0, TOOL_DIR)
sys.dont_write_bytecode = True
import turn_evidence as te  # noqa: E402

CHECKS = [r"python -I scripts/run_tests\.py", "pytest", "vitest", "run_tests", "dotnet test", "phpunit", "npm test"]
EDIT_ID = "toolu_edit"
NO_BASH_OUTPUT = "(Bash completed with no output)"
NO_POWERSHELL_OUTPUT = "(PowerShell completed with no output)"
BACKGROUND = "Command running in background with ID: b1. Output is being written to: x.output"
MOVED = "Command was moved to the background with ID: b2. Output is being written to: x.output"
TIMED_OUT = ("Command did not complete within its 120s timeout and was moved to the background (ID: b3). Output is being "
             "written to: x.output. You will be notified when it completes. To check interim output, use Read on that "
             "file path.")
EXIT_TEXT = "Command failed with exit code 1\r\nboom"
LONG_RUN = 3000   # the corpus's longest run of older records is 1500


def at(minute):
    return "2026-11-03T10:%02d:00.000Z" % minute


def tool_use(use_id, name, data, stamp, cwd):
    return {
        "parentUuid": "p", "isSidechain": False, "message": {
            "model": "m", "id": "msg", "type": "message", "role": "assistant",
            "content": [{"type": "tool_use", "id": use_id, "name": name, "input": data, "caller": {"type": "direct"}}],
            "stop_reason": None,
        }, "requestId": "r", "type": "assistant", "uuid": "u", "timestamp": stamp, "userType": "external",
        "entrypoint": "cli", "cwd": cwd, "sessionId": "s", "version": "2", "gitBranch": "develop",
    }


def tool_result(use_id, output, stamp, cwd, is_error=False):
    return {
        "parentUuid": "p", "isSidechain": False, "promptId": "q", "type": "user", "message": {
            "role": "user", "content": [{"tool_use_id": use_id, "type": "tool_result", "content": output,
                                         "is_error": is_error}],
        }, "uuid": "u", "timestamp": stamp, "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False},
        "sourceToolAssistantUUID": "a", "userType": "external", "entrypoint": "cli", "cwd": cwd, "sessionId": "s",
        "version": "2", "gitBranch": "develop",
    }


def sidechain_result(use_id, output, stamp, cwd):
    """A subagent's result line: most carry neither `toolUseResult` nor `is_error`."""
    return {
        "parentUuid": "p", "isSidechain": True, "promptId": "q", "agentId": "a1", "type": "user", "message": {
            "role": "user", "content": [{"tool_use_id": use_id, "type": "tool_result", "content": output}],
        }, "uuid": "u", "timestamp": stamp, "sourceToolAssistantUUID": "a", "userType": "external",
        "entrypoint": "cli", "cwd": cwd, "sessionId": "s", "version": "2", "gitBranch": "develop", "slug": "x",
    }


def persisted(preview):
    return ("<persisted-output>\nOutput too large (45.2KB). Full output saved to: x/tool-results/toolu_1.txt\n\n"
            "Preview (first 2KB):\n" + preview + "\n...\n</persisted-output>")


def user_line(content, stamp=None, **extra):
    record = {"parentUuid": "p", "isSidechain": False, "promptId": "q", "type": "user",
              "message": {"role": "user", "content": content}, "uuid": "u", "timestamp": stamp or at(0),
              "userType": "external", "entrypoint": "cli", "cwd": "x", "sessionId": "s"}
    record.update(extra)
    return record


def call(use_id, name, data, minute, cwd, output="", is_error=False):
    """A finished tool call: tool_use at `minute`, its result one minute later."""
    return [tool_use(use_id, name, data, at(minute), cwd),
            tool_result(use_id, output, at(minute + 1), cwd, is_error)]


def shell(minute, cwd, command="pytest", output="12 passed in 0.5s", is_error=False, name="Bash"):
    return call("toolu_%d" % minute, name, {"command": command}, minute, cwd, output, is_error)


def edit(minute, path, is_error=False, name="Edit"):
    output = "<tool_use_error>String to replace not found</tool_use_error>" if is_error else "updated"
    return call("toolu_e%d" % minute, name, {"file_path": path}, minute, "x", output, is_error)


def encode(record):
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def write_stream(path, *groups):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        for group in groups:
            for record in group if isinstance(group, list) else [group]:
                handle.write(record if isinstance(record, bytes) else encode(record))
    return path


def child(code, *args, timeout=60):
    """Runs code the way the launcher does, with no UTF-8 mode to hide a code page failure."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
    return subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, TOOL_DIR] + list(args), env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="te-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.main = os.path.join(self.base, "session.jsonl")
        self.root = self.repo("Worktrees", "app")

    def repo(self, *parts):
        path = os.path.join(self.base, *parts)
        os.makedirs(os.path.join(path, ".git"))
        return path

    def linked_repo(self, *parts):
        """A linked worktree: its .git is a file."""
        path = os.path.join(self.base, *parts)
        os.makedirs(path)
        with open(os.path.join(path, ".git"), "w", encoding="utf-8") as handle:
            handle.write("gitdir: elsewhere\n")
        return path

    def stream(self, name, *groups):
        return write_stream(os.path.join(self.base, *name.split("/")), *groups)

    def touched(self, path, minute):
        stamp = te.to_epoch(at(minute))
        os.utime(path, (stamp, stamp))
        return path

    def assert_pass(self, found, ts, reason):
        self.assertEqual((found.ts, found.reason), (ts, reason))


class TurnStart(unittest.TestCase):
    def test_lines_that_open_a_turn(self):
        cases = [
            ("human", user_line("fix it", turnOrigin="human"), "fix it"),
            ("sdk list", user_line([{"type": "text", "text": "fix it"}], turnOrigin="sdk"), "fix it"),
            ("scheduled", user_line("run", turnOrigin="scheduled"), "run"),
            ("prompt behind a reminder", user_line("<system-reminder>x</system-reminder>\nfix", turnOrigin="sdk"),
             "<system-reminder>x</system-reminder>\nfix"),
            ("subagent brief", user_line("build it", isSidechain=True), "build it"),
        ]
        for name, record, text in cases:
            with self.subTest(name):
                self.assertIs(te.turn_start(record, text), True)

    def test_each_flag_alone_refuses_a_plain_line_that_otherwise_opens_a_turn(self):
        cases = [
            ("isMeta", {"isMeta": True}),
            ("isCompactSummary", {"isCompactSummary": True, "turnOrigin": "human"}),
            ("toolUseResult dict", {"toolUseResult": {"stdout": ""}}),
            ("toolUseResult list", {"toolUseResult": []}),
            ("toolUseResult text", {"toolUseResult": ""}),
            ("task notification origin", {"turnOrigin": "task_notification"}),
        ]
        for name, flags in cases:
            with self.subTest(name):
                self.assertIs(te.turn_start(user_line("plain words", **flags), "plain words"), False)
                self.assertIs(te.turn_start(user_line("plain words", turnOrigin="human"), "plain words"), True)

    def test_each_body_refuses_a_line_with_no_flag(self):
        for text in ("<task-notification>\n<task-id>a</task-id>", "  \n<task-notification>x",
                     "<command-name>/x</command-name>", "<command-message>x</command-message>",
                     "<local-command-stdout>x</local-command-stdout>", "<ci-monitor-event>x",
                     "[Request interrupted by user]", "[Request interrupted by user for tool use]"):
            with self.subTest(text):
                self.assertIs(te.turn_start(user_line(text), text), False)
                self.assertIs(te.turn_start(user_line(text), "real " + text), True)

    def test_a_tool_result_line_refuses_on_its_content_alone(self):
        line = sidechain_result("t", "output", at(1), "x")
        self.assertNotIn("toolUseResult", line)
        self.assertIs(te.turn_start(line, ""), False)
        self.assertIs(te.turn_start(user_line("build it", isSidechain=True, agentId="a1"), "build it"), True)

    def test_a_line_with_an_odd_body_is_read_by_its_text(self):
        self.assertIs(te.turn_start({"message": "x"}, "fix it"), True)
        self.assertIs(te.turn_start({"message": {"content": ["x"]}}, "fix it"), True)

    def test_known_limit_a_slash_command_that_opens_a_model_turn_is_no_turn_start(self):
        text = "<command-message>review</command-message>\n<command-name>/review</command-name>"
        self.assertIs(te.turn_start(user_line(text, turnOrigin="human"), text), False)


class TurnBoundaries(TempDir):
    """A line that does not open a turn keeps the edits made before it."""

    def changes_of_last_turn(self, middle):
        path = self.stream("turn.jsonl", user_line("fix it", at(1), turnOrigin="human"), edit(2, "first.py"), middle,
                           edit(6, "second.py"))
        return [name for _, name in te.turn_changes([path], te.turn_start_ts(path))]

    def test_lines_inside_a_turn_keep_its_earlier_edits(self):
        cases = [
            ("notification with origin", user_line("anything", at(4), turnOrigin="task_notification")),
            ("notification without origin", user_line("<task-notification>x", at(4))),
            ("notification as a list", user_line([{"type": "text", "text": "<task-notification>x"}], at(4))),
            ("slash command", user_line("<command-name>/x</command-name>", at(4))),
            ("local command", user_line("<local-command-stdout>x</local-command-stdout>", at(4))),
            ("compact summary", user_line("This session is being continued", at(4), isCompactSummary=True,
                                          isVisibleInTranscriptOnly=True, turnOrigin="human")),
            ("subagent result", sidechain_result("t", "output", at(4), "x")),
            ("interrupted", user_line([{"type": "text", "text": "[Request interrupted by user]"}], at(4))),
            ("monitor event", user_line("<ci-monitor-event>x", at(4), turnOrigin="sdk")),
        ]
        for name, line in cases:
            with self.subTest(name):
                self.assertEqual(self.changes_of_last_turn(line), ["first.py", "second.py"])

    def test_a_person_starts_a_new_turn(self):
        self.assertEqual(self.changes_of_last_turn(user_line("next", at(4), turnOrigin="human")), ["second.py"])

    def test_known_limit_a_slash_command_keeps_the_turn_before_it(self):
        line = user_line("<command-message>review</command-message>\n<command-name>/review</command-name>", at(4))
        self.assertEqual(self.changes_of_last_turn(line), ["first.py", "second.py"])


class TurnStartTs(TempDir):
    def test_the_newest_opening_line_wins(self):
        path = self.stream("main.jsonl", user_line("one", at(1), turnOrigin="human"), edit(2, "a.py"),
                           user_line("two", at(4), turnOrigin="human"), edit(5, "b.py"))
        self.assertEqual(te.turn_start_ts(path), at(4))

    def test_a_list_body_is_read(self):
        notification = [{"type": "other", "text": "a"}, {"type": "text", "text": "<task-notification>x"}]
        path = self.stream("main.jsonl", user_line([{"type": "text", "text": "fix"}], at(1)),
                           user_line(notification, at(3)))
        self.assertEqual(te.turn_start_ts(path), at(1))

    def test_a_subagent_stream_opens_at_its_brief(self):
        use = tool_use("t", "Edit", {"file_path": "a.py"}, at(2), "x")
        path = self.stream("agent-a.jsonl", user_line("build it", at(1), isSidechain=True, agentId="a1"), use,
                           sidechain_result("t", "updated", at(3), "x"))
        self.assertEqual(te.turn_start_ts(path), at(1))
        self.assertEqual(te.turn_changes([path], te.turn_start_ts(path)), [(at(3), "a.py")])

    def test_a_stream_with_no_opening_line_gives_no_time(self):
        self.assertEqual(te.turn_start_ts(self.stream("main.jsonl", edit(2, "a.py"))), "")
        self.assertEqual(te.turn_start_ts(self.stream("empty.jsonl")), "")

    def test_a_missing_stream_raises(self):
        with self.assertRaises(OSError):
            te.turn_start_ts(os.path.join(self.base, "missing.jsonl"))

    def test_junk_is_passed_over(self):
        opening = user_line("fix", at(1), turnOrigin="human")
        torn = encode(user_line("torn", at(7)))[:-9]
        junk = [torn, b'["type":"user"]\n', b'{"type":"user","timestamp":"\xff"}\n', b'{"x":"\\"type\\":\\"user\\""}\n']
        path = self.stream("main.jsonl", opening, user_line("newer", "not a time", turnOrigin="human"), junk)
        self.assertEqual(te.turn_start_ts(path), at(1))

    def test_a_text_of_a_bad_shape_is_no_text(self):
        path = self.stream("main.jsonl", user_line("fix", at(1)), user_line([{"type": "text", "text": 5}], at(6)))
        self.assertEqual(te.turn_start_ts(path), at(6))

    def test_only_user_lines_are_parsed(self):
        parsed = []
        real_loads = te.json.loads

        def noting_loads(line):
            parsed.append(line)
            return real_loads(line)

        te.json.loads = noting_loads
        self.addCleanup(setattr, te.json, "loads", real_loads)
        path = self.stream("main.jsonl", user_line("fix", at(1)), {"type": "system", "timestamp": at(2)},
                           {"type": "assistant", "timestamp": at(3), "message": {"content": "hi"}})
        self.assertEqual(te.turn_start_ts(path), at(1))
        self.assertEqual(len(parsed), 1)


class ClassifyCheck(unittest.TestCase):
    def assert_cases(self, cases):
        for command, output, is_error, want in cases:
            with self.subTest(command=command, output=output[:40], is_error=is_error):
                self.assertEqual(te.classify_check(command, output, is_error, CHECKS), want)

    def test_reading_a_check_is_no_check(self):
        self.assert_cases([
            ("rg vitest", "src/a.ts: vitest", False, None),
            ("cat pytest.ini", "[pytest]", False, None),
            ("echo run_tests", "run_tests", False, None),
            ("FOO=1 rg pytest", "x", False, None),
            ("$out = rg pytest", "x", False, None),
            ("(cat pytest.ini)", "x", False, None),
            ("env A=1 cat pytest.ini", "x", False, None),
            ("time cat pytest.ini", "x", False, None),
            ("command cat pytest.ini", "x", False, None),
            ("exec cat pytest.ini", "x", False, None),
            ("ls # pytest", "x", False, None),
            ("# pytest\nls", "x", False, None),
            ("python x.py # pytest", "x", False, None),
            ("mkdir 'pytest'", "x", False, None),
            ("deploy --note 'pytest; run_tests'", "x", False, None),
            ("deploy", "pytest", False, None),
        ])

    def test_running_a_check_is_one(self):
        self.assert_cases([
            ("pytest", "12 passed", False, "pass"),
            ("npx vitest run", "12 passed", False, "pass"),
            ("python -I scripts/run_tests.py", "# files 3, tests 9, failed 0, skipped 0", False, "pass"),
            ("cat pytest.ini && pytest", "12 passed", False, "pass"),
            ("FOO=1 pytest", "12 passed", False, "pass"),
            ("PYTEST", "12 passed", False, "pass"),
            ("python -I \"scripts/run_tests.py\"", "12 passed", False, "pass"),
            ("bash -c 'set -e; run_tests # now' | cat", "12 passed", False, "pass"),
            ("& \"tools/php.exe\" vendor/bin/run_tests --filter 'A|B'", "12 passed", False, "pass"),
            ("pytest -v", "some lines", False, "pass"),
            ("python -m pytest", "some lines", False, "pass"),
            ("python -X utf8 -I -m pytest", "some lines", False, "pass"),
            ("./vendor/bin/pest --compact && pytest", "some lines", False, "pass"),
            ("tools\\run_tests.ps1", "some lines", False, "pass"),
            ("\"C:/py/python.exe\" -m pytest", "some lines", False, "pass"),
            ("C:/py/python.exe -m pytest", "some lines", False, "pass"),
            ("timeout 60 pytest", "some lines", False, "pass"),
            ("@(python -m pytest)", "some lines", False, "pass"),
            ("pnpm --filter app run vitest", "some lines", False, "pass"),
            ("npm run vitest", "some lines", False, "pass"),
            ("pytest --help-me", "some lines", False, "pass"),
            ("run_tests-h", "some lines", False, "pass"),
            ("env A=1 pytest", "some lines", False, "pass"),
            ("time pytest", "some lines", False, "pass"),
            ("command pytest", "some lines", False, "pass"),
            ("exec pytest", "some lines", False, "pass"),
            ("$out = pytest", "some lines", False, "pass"),
            ("python x#y.py && pytest", "some lines", False, "pass"),
        ])

    def test_a_runner_can_carry_the_check_as_an_argument(self):
        for head in ("python", "python3", "python.exe", "py", "node", "pwsh", "powershell", "powershell.exe", "php", "php8.3",
                     "sh", "bash", "npx", "pnpm", "npm", "npm.cmd", "yarn", "bun", "composer", "timeout 60", "wsl",
                     "C:/py/python.exe", "C:\\py\\python.exe", "\"C:/Program Files/python.exe\""):
            with self.subTest(head):
                self.assertEqual(te.classify_check(head + " x/run_tests", "some lines", False, CHECKS), "pass")
        self.assertIsNone(te.classify_check("deploy x/run_tests", "some lines", False, CHECKS))

    def test_what_only_names_a_check_is_no_run(self):
        self.assert_cases([
            ("pip install pytest", "Successfully installed pytest-8", False, None),
            ("python -m pip install pytest", "x", False, None),
            ("npm install -D vitest", "added 12 packages", False, None),
            ("npm i -D vitest", "x", False, None),
            ("npm uninstall vitest", "x", False, None),
            ("npm remove vitest", "x", False, None),
            ("npm update vitest", "x", False, None),
            ("npm upgrade vitest", "x", False, None),
            ("npm ls vitest", "x", False, None),
            ("npm list vitest", "x", False, None),
            ("npm why vitest", "x", False, None),
            ("npm outdated vitest", "x", False, None),
            ("npm view vitest version", "3.0.0", False, None),
            ("pnpm --filter app add vitest", "x", False, None),
            ("pnpm -F app add vitest", "x", False, None),
            ("pnpm -r add vitest", "x", False, None),
            ("yarn add vitest", "x", False, None),
            ("bun add vitest", "x", False, None),
            ("composer require pytest", "x", False, None),
            ("python -m pip show pytest", "x", False, None),
            ("npm info vitest", "x", False, None),
            ("pytest --version", "pytest 8.3.0", False, None),
            ("pytest --help", "usage: pytest", False, None),
            ("pytest -h", "usage: pytest", False, None),
            ("pytest -V", "pytest 8.3.0", False, None),
            ("pytest --collect-only", "<Module x>", False, None),
            ("python -I scripts/run_tests.py --help", "usage: run_tests.py", False, None),
            ("python -I scripts/run_tests.py --list", "a\nb", False, None),
            ("python -I scripts/run_tests.py --list-tests", "a\nb", False, None),
            ("which pytest", "/usr/bin/pytest", False, None),
            ("command -v pytest", "/usr/bin/pytest", False, None),
            ("cp pytest.ini x", "", False, None),
            ("Copy-Item pytest.ini x", "", False, None),
            ("python -c \"print('pytest')\"", "pytest", False, None),
            ("python3 -c \"print('pytest')\"", "pytest", False, None),
            ("py -c \"print('pytest')\"", "pytest", False, None),
            ("python -I -c \"print('pytest')\"", "pytest", False, None),
            ("python -X utf8 -c \"print('pytest')\"", "pytest", False, None),
            ("node -e \"run_tests()\"", "x", False, None),
            ("php -r \"run_tests();\"", "x", False, None),
            ("some prose naming pytest", "x", False, None),
        ])

    def test_a_check_that_may_not_have_run_is_unknown(self):
        self.assert_cases([
            ("true || pytest", "", False, "unknown"),
            ("true || pytest", "12 passed", False, "unknown"),
            ("if (Test-Path nope) { pytest }", "", False, "unknown"),
            ("if (Test-Path nope) { pytest }", "12 passed", False, "unknown"),
            ("if [ -f x ]; then pytest; fi", "", False, "unknown"),
            ("if [ -f x ]; then pytest; else echo no; fi", "12 passed", False, "unknown"),
            ("if [ -f x ]; then echo; else pytest; fi", "", False, "unknown"),
            ("if pytest; then echo ok; fi", "ok", False, "unknown"),
            ("while pytest; do sleep 1; done", "", False, "unknown"),
            ("until pytest; do sleep 1; done", "", False, "unknown"),
            ("for t in a b; do pytest $t; done", "", False, "unknown"),
            ("foreach ($t in $l) { pytest $t }", "", False, "unknown"),
            ("try { pytest } finally { popd }", "", False, "unknown"),
            ("catch { pytest }", "", False, "unknown"),
            ("finally { pytest }", "", False, "unknown"),
            ("elseif ($x) { pytest }", "", False, "unknown"),
            ("while ($x) { pytest }", "", False, "unknown"),
            ("if a; then b; elif pytest; then c; fi", "", False, "unknown"),
            ("{ pytest; } > log", "", False, "unknown"),
            ("{\npytest\n}", "", False, "unknown"),
            ("true || pytest a && pytest b", "12 passed", False, "unknown"),
            ("true || pytest", "2 failed", False, "fail"),
            ("true && pytest", "", False, "pass"),
            ("true || echo no; pytest", "", False, "pass"),
        ])

    def test_a_command_with_a_real_commit_is_no_check(self):
        self.assert_cases([
            ("pytest && git commit -m x", "12 passed", False, None),
            ("pytest\ngit commit -m x", "12 passed", False, None),
            ("pytest; git -C 'a b' commit -m x", "12 passed", False, None),
            ("pytest; git -c a=b --no-pager commit", "12 passed", False, None),
            ("pytest; git.exe commit -m x", "12 passed", False, None),
            ("pytest; GIT commit -m x", "12 passed", False, None),
            ("git commit -m 'pytest passes'", "x", False, None),
        ])

    def test_a_commit_that_only_appears_in_text_is_no_commit(self):
        self.assert_cases([
            ("python scripts/run_tests.py <<'EOF'\ngit commit -m x\nEOF", "12 passed", False, "pass"),
            ("cat <<EOF > note.txt\ngit commit -m x\nEOF\npytest", "12 passed", False, "pass"),
            ("$note = @'\ngit commit -m x\n'@; pytest", "12 passed", False, "pass"),
            ("$note = @\"\ngit commit -m x\n\"@; pytest", "12 passed", False, "pass"),
            ("echo 'git commit' && pytest", "12 passed", False, "pass"),
            ("pytest # git commit", "12 passed", False, "pass"),
            ("git commit --dry-run; pytest", "12 passed", False, "pass"),
        ])

    def test_failure_signals(self):
        self.assert_cases([
            ("pytest", "boom", True, "fail"),
            ("pytest", "Exit code 1\nboom", False, "fail"),
            ("pytest", "Exit code 137", False, "fail"),
            ("pytest", "Exit code -1073741819", False, "fail"),
            ("pytest", "\nExit code 2\n", False, "fail"),
            ("pytest", EXIT_TEXT, False, "fail"),
            ("pytest", "Command failed with exit code 137", False, "fail"),
        ])

    def test_exit_code_words_that_are_no_failure(self):
        self.assert_cases([
            ("pytest", "Exit code 0\n12 passed", False, "pass"),
            ("pytest", "12 passed\nasserted Exit code 1 from the child", False, "pass"),
            ("pytest", "12 passed\nExit code 1", False, "pass"),
            ("pytest", "12 passed\nCommand failed with exit code 1", False, "pass"),
        ])

    def test_each_failing_summary_beats_exit_zero(self):
        for output in ("FAILURES!", "ERRORS!", "# fail 2", "\u2139 fail 2", "2 failed", "1 test failed",
                       "3 tests failing", "4 failures", "# files 3, tests 9, failed 2, skipped 0", "# tests failed: 3 of 9",
                       "Tests: 5, Assertions: 9, Failures: 1.", "Tests: 5, Assertions: 9, Errors: 1.",
                       "FAILED (failures=1)", "FAIL src/a.test.ts", "Traceback (most recent call last):",
                       "1 error in 1.20s", "2 errors"):
            for command in ("pytest", "pytest | tail -5"):
                with self.subTest(output=output, command=command):
                    self.assertEqual(te.classify_check(command, "12 passed\n" + output, False, CHECKS), "fail")

    def test_a_zero_in_a_summary_is_no_failure(self):
        self.assert_cases([
            ("pytest", "# fail 0", False, "pass"),
            ("pytest", "12 passed, 0 failed", False, "pass"),
            ("pytest", "# files 3, tests 9, failed 0, skipped 0", False, "pass"),
            ("pytest", "Tests: 5, Assertions: 9, Failures: 0.", False, "pass"),
        ])

    def test_each_passing_summary_clears_a_pipe(self):
        for output in ("OK (12 tests, 30 assertions)", "OK (1 test, 1 assertion)",
                       "OK, but there were issues!\nTests: 5, Assertions: 9, Warnings: 1.", "Ran 12 tests in 0.500s\n\nOK",
                       "Ran 1 test in 0.5s\n\nOK (skipped=1)", "12 passed in 0.5s", "Tests: 12 passed (30 assertions)",
                       "1 test passed", "# pass 12", "\u2139 pass 12", "# files 3, tests 9, failed 0, skipped 0",
                       "# all 12 checks passed",
                       "test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"):
            with self.subTest(output):
                self.assertEqual(te.classify_check("pytest | tail -5", output, False, CHECKS), "pass")

    def test_a_line_that_counts_nothing_is_no_summary(self):
        for output in ("Ran 12 tests in 0.500s", "...F\n---\nRan 12 tests in 0.500s\n", "3 of 12 passed",
                       "3 of 12 tests passed", "3/12 passed", "3/12 tests passed", "OK", "OK, nothing to do",
                       "OK, but there were issues!", "OK (0 tests, 0 assertions)", "# files 0, tests 0, failed 0, skipped 0",
                       "# all checks passed", "# all 0 checks passed", "all tests passed", "test result: ok.",
                       "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"):
            with self.subTest(output):
                self.assertEqual(te.classify_check("pytest | tail -5", output, False, CHECKS), "unknown")

    def test_a_failing_count_beats_a_passing_one_behind_a_pipe(self):
        self.assert_cases([
            ("pytest | tail -1", "=== 10 passed, 1 error in 1.20s ===", False, "fail"),
            ("pytest; echo done", "=== 10 passed, 2 errors in 1.20s ===\ndone", False, "fail"),
            ("pytest | tail -1", "=== 10 passed, 0 errors in 1.20s ===", False, "pass"),
        ])

    def test_a_pipe_with_no_summary_is_unknown(self):
        self.assert_cases([
            ("pytest | tail -5", "some lines", False, "unknown"),
            ("pytest | tail -5", "", False, "unknown"),
            ("pytest | tail -5", "0 passed", False, "unknown"),
            ("pytest | tail -5", "Ran 0 tests in 0.000s\n\nOK", False, "unknown"),
            ("pytest | tail -5", "# pass 0\n# fail 0", False, "unknown"),
            ("pytest 2>&1 | Select-Object -Last 3", NO_POWERSHELL_OUTPUT, False, "unknown"),
        ])

    def test_no_pipe_and_no_summary_is_a_pass(self):
        self.assert_cases([
            ("pytest", "some lines", False, "pass"),
            ("pytest", "", False, "pass"),
            ("pytest", NO_BASH_OUTPUT, False, "pass"),
            ("pytest 'a|b'", "some lines", False, "pass"),
            ("pytest # then a | b", "some lines", False, "pass"),
        ])

    def test_a_stage_after_the_check_can_replace_its_exit_status(self):
        self.assert_cases([
            ("pytest; echo done", "INTERNALERROR> boom\ndone", False, "unknown"),
            ("pytest; echo \"exit=$?\"", "ImportError: x\nexit=1", False, "unknown"),
            ("pytest || true", "ImportError: x", False, "unknown"),
            ("pytest || echo gone", "some lines", False, "unknown"),
            ("pytest; exit 0", "ImportError: x", False, "unknown"),
            ("python -I scripts/run_tests.py; git status", "On branch x", False, "unknown"),
            ("pytest\ngit status", "On branch x", False, "unknown"),
            ("pytest && echo a; echo b", "a\nb", False, "unknown"),
            ("pytest | tee log; echo b", "b", False, "unknown"),
        ])

    def test_only_and_passes_a_failure_on(self):
        self.assert_cases([
            ("pytest && echo done", "some lines", False, "pass"),
            ("pytest && echo a && echo b", "some lines", False, "pass"),
            ("cat a; pytest", "some lines", False, "pass"),
            ("cat a\npytest", "some lines", False, "pass"),
            ("pytest;", "some lines", False, "pass"),
            ("pytest\n", "some lines", False, "pass"),
            ("pytest;\n\n", "some lines", False, "pass"),
            ("pytest &&\n  echo done", "some lines", False, "pass"),
            ("pytest \\\n  -q", "some lines", False, "pass"),
            ("pytest `\n  -q", "some lines", False, "pass"),
            ("pytest \\\r\n  -q", "some lines", False, "pass"),
        ])

    def test_a_summary_clears_a_hidden_exit(self):
        self.assert_cases([
            ("pytest; echo done", "12 passed\ndone", False, "pass"),
            ("pytest || true", "12 passed", False, "pass"),
            ("pytest\ngit status", "12 passed\nOn branch x", False, "pass"),
        ])

    def test_a_summary_speaks_for_the_last_check_alone(self):
        self.assert_cases([
            ("pytest a; pytest b", "ImportError: x\n12 passed", False, "unknown"),
            ("pytest a | tail -1; pytest b", "12 passed", False, "unknown"),
            ("pytest a && pytest b", "5 passed\n12 passed", False, "pass"),
        ])

    def test_a_persisted_preview_is_the_head_of_the_output_so_no_summary(self):
        self.assert_cases([
            ("pytest", persisted("collected 900 items"), False, "pass"),
            ("pytest", persisted("suite a: 12 passed"), False, "pass"),
            ("pytest | tee log", persisted("suite a: 12 passed"), False, "unknown"),
            ("pytest; echo done", persisted("collected 900 items"), False, "unknown"),
            ("pytest", persisted("suite a: 2 failed"), False, "fail"),
        ])

    def test_a_background_run_is_unknown(self):
        self.assert_cases([
            ("pytest", BACKGROUND, False, "unknown"),
            ("pytest", MOVED, False, "unknown"),
            ("pytest", TIMED_OUT, False, "unknown"),
            ("pytest", TIMED_OUT.replace("120s", "7200s"), False, "unknown"),
            ("pytest &", "[1] 1234", False, "unknown"),
            ("pytest &\nwait", "[1] 1234", False, "unknown"),
            ("pytest & echo started", "started", False, "unknown"),
            ("pytest & echo started", "started\n12 passed", False, "unknown"),
            ("pytest & sleep 1; echo ok", "ok", False, "unknown"),
            ("pytest && echo a & echo b", "b", False, "unknown"),
            ("pytest && echo a & echo b", "12 passed", False, "unknown"),
            ("Start-Process pytest", "", False, "unknown"),
            ("Start-Process -FilePath pytest", "", False, "unknown"),
            ("saps pytest", "", False, "unknown"),
            ("Start-Job { pytest }", "Id Name State", False, "unknown"),
            ("Start-Job -ScriptBlock { pytest }", "Id Name State", False, "unknown"),
            ("Start-ThreadJob { pytest }", "Id Name State", False, "unknown"),
            ("nohup pytest > log", "", False, "unknown"),
            ("pytest &", "2 failed", False, "fail"),
        ])

    def test_a_foreground_run_is_not_read_as_backgrounded(self):
        self.assert_cases([
            ("pytest 2>&1", "some lines", False, "pass"),
            ("pytest && echo done", "some lines", False, "pass"),
            ("pytest", "12 passed\n" + BACKGROUND, False, "pass"),
            ("pytest", "12 passed\n" + TIMED_OUT, False, "pass"),
            ("pytest *>&1", "some lines", False, "pass"),
            ("pytest &> log", "some lines", False, "pass"),
            ("pytest &>> log", "some lines", False, "pass"),
            ("pytest >& log", "some lines", False, "pass"),
            ("server & pytest", "some lines", False, "pass"),
            ("& pytest", "some lines", False, "pass"),
            ("$out = & pytest", "some lines", False, "pass"),
            ("cd a; & pytest", "some lines", False, "pass"),
            ("cd a && & pytest", "some lines", False, "pass"),
        ])

    def test_only_the_end_of_the_output_is_a_summary_while_the_exit_reaches_the_result(self):
        output = "1 failed\n" + "x\n" * 2000 + "12 passed\n"
        self.assertEqual(te.classify_check("pytest", output, False, CHECKS), "pass")
        self.assertEqual(te.classify_check("pytest a && pytest b", output, False, CHECKS), "pass")
        self.assertEqual(te.classify_check("pytest", "Exit code 1\n" + "x\n" * 2000, False, CHECKS), "fail")
        self.assertEqual(te.classify_check("pytest | tee log", "12 passed\n" + "x\n" * 2000, False, CHECKS), "unknown")

    def test_with_the_exit_hidden_a_failure_anywhere_in_the_output_counts(self):
        output = "1 failed\n" + "x\n" * 2000 + "12 passed\n"
        self.assertEqual(te.classify_check("pytest a; pytest b", output, False, CHECKS), "fail")
        self.assertEqual(te.classify_check("pytest | tee log", output, False, CHECKS), "fail")

    def test_no_configured_check_means_none(self):
        self.assertIsNone(te.classify_check("pytest", "12 passed", False, []))

    def test_a_php_variable_is_a_runner(self):
        self.assert_cases([
            ("$php vendor/bin/phpunit", "12 passed", False, "pass"),
            ("$php82 vendor/bin/phpunit --filter A", "some lines", False, "pass"),
            ("$PHP vendor/bin/phpunit", "12 passed", False, "pass"),
            ("$php vendor/bin/phpunit | tail -1", "some lines", False, "unknown"),
            ("$php vendor/bin/phpunit | tail -1", "2 failed", False, "fail"),
            ("$php vendor/bin/phpunit; echo done", "done", False, "unknown"),
        ])

    def test_a_php_variable_that_runs_no_check_is_none(self):
        self.assert_cases([
            ("$php -r \"phpunit();\"", "x", False, None),
            ("$php vendor/bin/phpunit --version", "PHPUnit 11", False, None),
            ("$go vendor/bin/phpunit", "x", False, None),
        ])

    def test_measure_command_runs_what_it_wraps_and_hides_the_exit(self):
        self.assert_cases([
            ("Measure-Command { npm test }", "TotalSeconds : 3", False, "unknown"),
            ("Measure-Command { npm test }", "12 passed", False, "pass"),
            ("Measure-Command { npm test }", "2 failed", False, "fail"),
            ("$t = Measure-Command { pytest }", "TotalSeconds : 3", False, "unknown"),
            ("Measure-Command { npm ci; npm test }", "TotalSeconds : 3", False, "unknown"),
            ("Measure-Command { pytest | Out-Host }", "12 passed", False, "pass"),
        ])

    def test_measure_command_around_no_check_is_none(self):
        self.assert_cases([
            ("Measure-Command { cat pytest.ini }", "x", False, None),
            ("Measure-Command { rg vitest }", "x", False, None),
        ])

    def test_an_assignment_inside_a_block_is_read_through(self):
        self.assert_cases([
            ("foreach ($i in 1,2) { $o = npm test 2>&1 }", "12 passed", False, "unknown"),
            ("try { $o = npm test 2>&1 }", "12 passed", False, "unknown"),
            ("try { $o = pytest 2>&1 }", "2 failed", False, "fail"),
        ])

    def test_an_assignment_inside_a_block_that_runs_no_check_is_none(self):
        self.assert_cases([
            ("try { $o = cat pytest.ini }", "x", False, None),
            ("foreach ($i in 1) { $o = rg pytest }", "x", False, None),
        ])


class LastPass(TempDir):
    def scan(self, *groups, root=None):
        return te.last_pass([self.stream("main.jsonl", *groups)], root or self.root, CHECKS, 10)

    def test_the_time_is_the_start_of_the_check(self):
        self.assert_pass(self.scan(shell(5, self.root)), at(5), "ok")

    def test_the_newest_pass_wins_and_a_failure_is_passed_over(self):
        found = self.scan(shell(1, self.root), shell(5, self.root), shell(9, self.root, output="2 failed"))
        self.assert_pass(found, at(5), "ok")

    def test_powershell_exit_code_is_no_pass(self):
        run = shell(5, self.root, output="Exit code 1\nboom", is_error=True, name="PowerShell")
        self.assert_pass(self.scan(run), "", "ok")
        self.assert_pass(self.scan(shell(2, self.root, name="PowerShell"), run), at(2), "ok")

    def test_a_list_body_is_read(self):
        run = shell(5, self.root)
        run[1]["message"]["content"][0]["content"] = [{"type": "text", "text": "1 failed"}]
        self.assert_pass(self.scan(run), "", "ok")
        run[1]["message"]["content"][0]["content"] = [{"type": "text", "text": "12 passed"}]
        self.assert_pass(self.scan(run), at(5), "ok")

    def test_a_pending_call_or_another_tool_is_no_pass(self):
        self.assert_pass(self.scan(shell(5, self.root)[0]), "", "ok")
        self.assert_pass(self.scan(call("t", "Agent", {"command": "pytest"}, 5, self.root, "12 passed")), "", "ok")

    def test_a_pipe_with_no_summary_or_a_background_run_is_no_pass(self):
        self.assert_pass(self.scan(shell(5, self.root, "pytest | tail -1", "some lines")), "", "ok")
        self.assert_pass(self.scan(shell(5, self.root, output=BACKGROUND)), "", "ok")

    def test_a_second_worktree_does_not_count(self):
        other = self.repo("Worktrees", "app-2")
        self.assert_pass(self.scan(shell(5, other)), "", "ok")
        self.assert_pass(self.scan(shell(5, other), root=other), at(5), "ok")

    def test_a_linked_worktree_is_its_own_root(self):
        linked = self.linked_repo("Worktrees", "app-linked")
        self.assert_pass(self.scan(shell(5, linked)), "", "ok")
        self.assert_pass(self.scan(shell(5, linked), root=linked), at(5), "ok")

    def test_a_nested_agent_worktree_does_not_count(self):
        nested = self.linked_repo("Worktrees", "app", ".claude", "worktrees", "agent-1")
        self.assert_pass(self.scan(shell(5, nested)), "", "ok")
        self.assert_pass(self.scan(shell(5, nested), root=nested), at(5), "ok")

    def test_a_subfolder_of_the_root_counts(self):
        sub = os.path.join(self.root, "plugins", "x")
        os.makedirs(sub)
        self.assert_pass(self.scan(shell(5, sub)), at(5), "ok")

    def test_the_command_can_move_to_another_root(self):
        other = self.repo("Worktrees", "other")
        run = shell(5, self.root, "cd '%s' && pytest" % other)
        self.assert_pass(self.scan(run), "", "ok")
        self.assert_pass(self.scan(run, root=other), at(5), "ok")

    def test_a_relative_cd_stays_in_the_root(self):
        os.makedirs(os.path.join(self.root, "sub"))
        self.assert_pass(self.scan(shell(5, self.root, "cd sub && pytest")), at(5), "ok")

    def test_a_subagent_result_of_the_real_shape_is_read(self):
        run = [tool_use("t", "Bash", {"command": "pytest"}, at(5), self.root),
               sidechain_result("t", "12 passed", at(6), self.root)]
        self.assert_pass(self.scan(run), at(5), "ok")

    def test_a_stamp_with_no_fraction_or_zone_is_read(self):
        for stamp in ("2026-11-03T10:05:00Z", "2026-11-03T10:05:00"):
            with self.subTest(stamp):
                self.assert_pass(self.scan(call("t", "Bash", {"command": "pytest"}, 5, self.root, "12 passed"),
                                           tool_use("u", "Read", {}, stamp, self.root)), at(5), "ok")
                run = shell(5, self.root)
                run[0]["timestamp"] = stamp
                self.assert_pass(self.scan(run), stamp, "ok")

    def test_a_gone_folder_compares_as_itself(self):
        gone = os.path.join(self.base, "gone")
        self.assert_pass(self.scan(shell(5, gone), root=gone), at(5), "ok")
        self.assert_pass(self.scan(shell(5, gone, "python scripts/run_tests.py"), root=gone), at(5), "ok")
        self.assert_pass(self.scan(shell(5, gone + "/sub"), root=gone), "", "ok")

    def test_a_subagent_pass_counts(self):
        main = self.stream("session.jsonl", shell(1, self.root, output="2 failed"))
        sub = self.stream("session/subagents/agent-a.jsonl", shell(5, self.root))
        self.touched(sub, 6)
        self.assert_pass(te.last_pass(te.stream_paths(main, ""), self.root, CHECKS, 10), at(5), "ok")

    def test_a_later_subagent_pass_beats_the_calling_stream(self):
        main = self.stream("session.jsonl", shell(1, self.root))
        sub = self.stream("session/subagents/agent-a.jsonl", shell(5, self.root))
        self.touched(sub, 6)
        self.assert_pass(te.last_pass([main, sub], self.root, CHECKS, 10), at(5), "ok")

    def test_an_older_subagent_pass_does_not_move_the_answer_back(self):
        main = self.stream("session.jsonl", shell(5, self.root))
        sub = self.stream("session/subagents/agent-a.jsonl", shell(1, self.root), edit(8, "x"))
        self.touched(sub, 9)
        self.assert_pass(te.last_pass([main, sub], self.root, CHECKS, 10), at(5), "ok")

    def test_a_stream_untouched_since_the_pass_is_not_read(self):
        main = self.stream("session.jsonl", shell(5, self.root))
        sub = self.stream("session/subagents/agent-a.jsonl", shell(9, self.root))
        self.touched(sub, 4)
        self.assert_pass(te.last_pass([main, sub], self.root, CHECKS, 10), at(5), "ok")

    def test_the_newest_stream_goes_first_so_a_pass_there_spares_the_rest(self):
        main = self.stream("session.jsonl", edit(1, "x"))
        old = self.touched(self.stream("session/subagents/agent-a.jsonl", shell(3, self.root)), 6)
        new = self.touched(self.stream("session/subagents/agent-b.jsonl", shell(7, self.root)), 9)
        opened = []
        real_open = open

        def noting_open(path, mode):
            opened.append(path)
            return real_open(path, mode)

        te.open = noting_open
        self.addCleanup(delattr, te, "open")
        self.assert_pass(te.last_pass([main, old, new], self.root, CHECKS, 10), at(7), "ok")
        self.assertEqual(opened, [main, new])

    def test_a_torn_last_line_is_skipped(self):
        whole = encode(shell(9, self.root)[1])
        run = shell(5, self.root), shell(9, self.root)[0]
        path = self.stream("main.jsonl", *run, [whole[:-30]])
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(5), "ok")
        path = self.stream("main.jsonl", *run, [whole])
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(9), "ok")

    def test_junk_lines_are_skipped(self):
        noisy = shell(7, self.root)
        no_stamp = shell(8, self.root)
        del no_stamp[0]["timestamp"]
        junk = [
            b'[{"type":"user","x":"tool_use"}]\n',
            b'{"type":"user","timestamp":"' + at(9).encode() + b'","message":"tool_use"}\n',
            b'{"type":"user","timestamp":"' + at(9).encode() + b'","message":{"content":"tool_use"}}\n',
            b'{"type":"user","timestamp":"' + at(9).encode() + b'","message":{"role":"tool_use"}}\n',
            b'{"type":"user","timestamp":"' + at(9).encode() + b'","message":{"content":["tool_use"]}}\n',
            b'{"type":"user","tool_use":"\xff"}\n',
        ]
        blob = shell(9, self.root)
        blob[0]["type"] = blob[1]["type"] = "api-request-blob"
        blob[1]["note"] = {"type": "user"}
        blob[0]["note"] = {"type": "assistant"}
        junk.extend(blob)
        self.assert_pass(self.scan(shell(5, self.root), noisy[0], no_stamp, junk), at(5), "ok")

    def test_only_user_and_assistant_lines_about_a_tool_are_parsed(self):
        parsed = []
        real_loads = te.json.loads

        def noting_loads(line):
            parsed.append(line)
            return real_loads(line)

        te.json.loads = noting_loads
        self.addCleanup(setattr, te.json, "loads", real_loads)
        chatter = [user_line("no tool here"), {"type": "system", "subtype": "tool_use", "timestamp": at(9)}]
        self.assert_pass(self.scan(shell(5, self.root), chatter), at(5), "ok")
        self.assertEqual(len(parsed), 2)

    def test_a_zero_byte_file_is_read_and_holds_nothing(self):
        path = self.stream("main.jsonl")
        self.assertEqual(os.path.getsize(path), 0)
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), "", "ok")

    def test_a_missing_file_is_unreadable(self):
        missing = os.path.join(self.base, "missing.jsonl")
        self.assert_pass(te.last_pass([missing], self.root, CHECKS, 10), "", "unreadable")

    def test_no_stream_at_all_is_unreadable(self):
        self.assert_pass(te.last_pass([], self.root, CHECKS, 10), "", "unreadable")

    def test_one_unreadable_stream_keeps_the_pass_of_another(self):
        missing = os.path.join(self.base, "missing.jsonl")
        path = self.stream("main.jsonl", shell(5, self.root))
        self.assert_pass(te.last_pass([missing, path], self.root, CHECKS, 10), at(5), "unreadable")

    def test_a_listed_stream_that_vanished_is_unreadable(self):
        missing = os.path.join(self.base, "missing.jsonl")
        path = self.stream("main.jsonl", edit(5, "x"))
        self.assert_pass(te.last_pass([path, missing], self.root, CHECKS, 10), "", "unreadable")

    def test_a_bad_check_regex_raises_for_the_caller(self):
        path = self.stream("main.jsonl", shell(5, self.root))
        with self.assertRaises(re.error):
            te.last_pass([path], self.root, ["("], 10)


class HiddenCheck(TempDir):
    def scan(self, *groups, root=None):
        return te.last_pass([self.stream("main.jsonl", *groups)], root or self.root, CHECKS, 10)

    def assert_read(self, found, ts, hidden):
        self.assert_pass(found, ts, "ok")
        self.assertEqual(found.hidden, hidden)

    def test_each_form_the_reader_cannot_read_is_kept_and_is_no_pass(self):
        for label, command, output in (
                ("exit hidden by a pipe", "pytest | tail -1", "some lines"),
                ("exit hidden by a later stage", "pytest; echo done", "some lines"),
                ("sent to the background by the shell", "pytest &", "started"),
                ("sent to the background by the tool", "pytest", BACKGROUND),
                ("moved to the background", "pytest", MOVED),
                ("timed out into the background", "pytest", TIMED_OUT),
                ("persisted with no summary", "pytest | tee log", persisted("collected 900 items")),
                ("persisted with a summary it cannot trust", "pytest | tee log", persisted("suite a: 12 passed")),
                ("a commit in the call", "pytest && git commit -m x", "[develop 1a2b3c4] x"),
                ("a commit in the call and a summary", "pytest && git commit -m x", "12 passed\n[develop 1a2b3c4] x"),
                ("a commit before the check", "git commit -m x && pytest", "12 passed"),
                ("a commit and a pipe", "pytest | tail -1 && git commit -m x", "[develop 1a2b3c4] x"),
                ("a commit and a background run", "pytest & git commit -m x", "[develop 1a2b3c4] x")):
            with self.subTest(label):
                self.assert_read(self.scan(shell(5, self.root, command, output)), "", at(5))

    def test_a_check_read_as_failed_is_not_hidden(self):
        for label, command, output, is_error in (
                ("plain", "pytest", "2 failed", False),
                ("by its error flag", "pytest", EXIT_TEXT, True),
                ("through a pipe", "pytest | tail -1", "2 failed", False),
                ("persisted", "pytest | tee log", persisted("suite a: 2 failed"), False),
                ("with a commit after a semicolon", "pytest; git commit -m x", "2 failed\n[develop 1a2b3c4] x", False),
                ("with a commit after &&", "pytest && git commit -m x", EXIT_TEXT, True)):
            with self.subTest(label):
                self.assert_read(self.scan(shell(5, self.root, command, output, is_error)), "", "")

    def test_a_readable_pass_is_not_hidden(self):
        self.assert_read(self.scan(shell(5, self.root)), at(5), "")
        self.assert_read(self.scan(shell(5, self.root, "pytest", persisted("collected 900 items"))), at(5), "")

    def test_a_call_that_runs_no_check_is_not_hidden(self):
        for command in ("git commit -m x", "rg pytest | tail -1", "echo pytest &", "git add x && git commit -m pytest"):
            with self.subTest(command):
                self.assert_read(self.scan(shell(5, self.root, command, "some lines")), "", "")

    def test_a_call_with_no_result_is_not_hidden(self):
        self.assert_read(self.scan(shell(5, self.root, "pytest | tail -1")[0]), "", "")

    def test_the_newest_of_two_is_kept(self):
        older, newer = shell(3, self.root, "pytest | tail -1", "x"), shell(7, self.root, "pytest &", "x")
        self.assert_read(self.scan(older, newer), "", at(7))

    def test_the_newest_across_streams_is_kept_in_either_order(self):
        older = self.stream("older.jsonl", shell(3, self.root, "pytest | tail -1", "x"))
        newer = self.stream("newer.jsonl", shell(7, self.root, "pytest | tail -1", "x"))
        for streams in ([older, newer], [newer, older]):
            with self.subTest(streams):
                self.assert_read(te.last_pass(streams, self.root, CHECKS, 10), "", at(7))

    def test_a_subagents_hidden_check_counts(self):
        main = self.stream("main.jsonl", edit(1, "a.py"))
        sub = self.stream("agent-a.jsonl", tool_use("t", "Bash", {"command": "pytest | tail -1"}, at(5), self.root),
                          sidechain_result("t", "some lines", at(6), self.root))
        self.assert_read(te.last_pass([main, sub], self.root, CHECKS, 10), "", at(5))

    def test_a_pass_and_a_newer_hidden_check_are_both_kept(self):
        self.assert_read(self.scan(shell(3, self.root), shell(5, self.root, "pytest | tail -1", "x")), at(3), at(5))

    def test_the_scan_ends_at_the_pass(self):
        self.assert_read(self.scan(shell(3, self.root, "pytest | tail -1", "x"), shell(5, self.root)), at(5), "")

    def test_a_hidden_check_counts_for_its_own_root_only(self):
        other = self.repo("Worktrees", "app-2")
        run = shell(5, other, "pytest | tail -1", "x")
        self.assert_read(self.scan(run), "", "")
        self.assert_read(self.scan(run, root=other), "", at(5))

    def test_a_commit_call_counts_for_the_root_it_moved_to(self):
        other = self.repo("Worktrees", "other")
        run = shell(5, self.root, "cd '%s' && pytest && git commit -m x" % other, "[develop 1a2b3c4] x")
        self.assert_read(self.scan(run), "", "")
        self.assert_read(self.scan(run, root=other), "", at(5))

    def test_a_check_with_no_folder_counts_for_no_root(self):
        self.assert_read(self.scan(shell(5, self.root, "cd $dir && pytest | tail -1", "x")), "", "")

    def test_the_contract_of_the_commit_rule_stays(self):
        self.assertIsNone(te.classify_check("pytest && git commit -m x", "12 passed", False, CHECKS))
        self.assertIsNone(te.classify_check("pytest | tail -1 && git commit -m x", "x", False, CHECKS))


class CheckRoot(TempDir):
    """Which repo a pass counts for. Every command here classifies as a pass; only the folder decides."""

    def setUp(self):
        super().setUp()
        self.other = self.repo("Worktrees", "other")
        self.sub = os.path.join(self.root, "sub")
        os.makedirs(self.sub)

    def credited(self, command, cwd=None):
        self.assertEqual(te.classify_check(command, "12 passed", False, CHECKS), "pass", command)
        path = self.stream("main.jsonl", shell(5, cwd or self.root, command))
        return [name for name, root in (("root", self.root), ("other", self.other))
                if te.last_pass([path], root, CHECKS, 10).ts]

    def assert_credits(self, want, *commands):
        for command in commands:
            with self.subTest(command):
                self.assertEqual(self.credited(command % {"o": self.other, "r": self.root}), want)

    def test_git_c_plays_no_part(self):
        self.assert_credits(["root"], "git -C '%(o)s' status && pytest", "pytest && git -C '%(o)s' log",
                            "git -C '%(o)s' status\npytest")

    def test_the_last_cd_before_the_check_decides(self):
        self.assert_credits(["other"], "cd '%(o)s' && pytest", "cd '%(o)s'; pytest", "cd '%(o)s'\npytest",
                            "cd sub && cd '%(o)s' && pytest", "(cd '%(o)s' && pytest)", "pushd '%(o)s' && pytest && popd",
                            "Set-Location -LiteralPath '%(o)s'; pytest", "Set-Location -Path \"%(o)s\"; pytest",
                            "sl '%(o)s'; pytest", "chdir '%(o)s'; pytest", "Push-Location '%(o)s'; pytest",
                            "cd -- '%(o)s' && pytest", "cd -P '%(o)s' && pytest", "cd -L '%(o)s' && pytest",
                            "cd ../other && pytest", "cd sub; cd ../../other && pytest", "pytest && cd '%(o)s' && pytest")
        self.assert_credits(["root"], "cd '%(o)s' && cd '%(r)s' && pytest", "cd sub; pytest", "cd 'sub)x' && pytest")

    def test_a_cd_after_the_check_plays_no_part(self):
        self.assert_credits(["root"], "pytest && cd '%(o)s'", "pytest && cd $NOWHERE", "pytest && popd")

    def test_dots_collapse(self):
        self.assertEqual(self.credited("cd .. && pytest", self.sub), ["root"])
        self.assertEqual(self.credited("cd ../../other && pytest", self.sub), ["other"])
        self.assert_credits(["root"], "cd . && pytest", "cd sub/.. && pytest", "cd ./sub/./.. && pytest",
                            "cd sub/../../app && pytest")
        self.assert_credits([], "cd ../plain && pytest", "cd .. && pytest")

    def test_climbing_past_the_top_stays_at_the_top(self):
        rooted = re.sub(r"^[A-Za-z]:", "", te.norm(self.other))
        self.assert_credits(["other"], "cd " + "../" * 40 + rooted.lstrip("/") + " && pytest")

    def test_a_cd_that_does_not_resolve_gives_no_root(self):
        self.assert_credits([], "cd \"$OTHER\" && pytest", "cd $OTHER; pytest", "$w = 'x'; Set-Location $w; pytest",
                            "cd ~/other && pytest", "cd %%TEMP%% && pytest", "cd `pwd` && pytest", "cd a* && pytest",
                            "cd a? && pytest", "cd $(dirname x) && pytest", "cd (Get-Location) ; pytest", "cd - && pytest",
                            "cd; pytest", "cd a b; pytest", "popd; pytest", "Pop-Location; pytest",
                            "if [ -f x ]; then cd y; fi; pytest")

    def test_a_group_closed_after_a_cd_gives_no_root(self):
        self.assert_credits([], "(cd '%(o)s' && make) && pytest", "(cd '%(o)s'); pytest", "(cd sub); pytest",
                            "(cd '%(o)s' && make)\npytest")

    def test_a_group_with_no_cd_changes_nothing(self):
        self.assert_credits(["root"], "(Get-Item x).Name; pytest", "(make) && pytest", "echo '(' && pytest")

    def test_a_command_that_starts_like_cd_is_no_cd(self):
        self.assert_credits(["root"], "cdk synth && pytest")

    def test_a_form_that_moves_the_run_elsewhere_gives_no_root(self):
        self.assert_credits([], "npm --prefix '%(o)s' run vitest", "npm --prefix=other run vitest", "pnpm -C other vitest",
                            "yarn --cwd other vitest", "pytest --rootdir=other", "pytest --rootdir other",
                            "pytest --root other", "pytest --cd other", "pytest --dir other", "pytest --directory other",
                            "pytest --working-dir other", "pytest --manifest-path other/x", "pytest --prefix",
                            "bash -c 'cd other && pytest'", "pwsh -c 'Set-Location other; pytest'",
                            "pwsh -c 'Push-Location other; pytest'", "bash -c 'pushd other; pytest'",
                            "bash -c 'chdir other; pytest'", "bash -c 'true;cd other;pytest'",
                            "bash -c 'true&&cd other&&pytest'", "bash -c 'true|cd other|pytest'")

    def test_a_named_script_in_another_repo_gives_no_root(self):
        self.repo("Worktrees", "app-2")
        self.assert_credits([], "python '%(o)s/run_tests.py'", "python ../other/run_tests.py", "python $HOME/x/run_tests.py",
                            "python ~/x/run_tests.py", "python %%TEMP%%/run_tests.py", "python ../other/RUN_TESTS.py",
                            "python ../app-2/run_tests.py")

    def test_a_named_script_inside_the_repo_counts(self):
        self.assert_credits(["root"], "python '%(r)s/scripts/run_tests.py'", "python scripts/run_tests.py",
                            "python ./run_tests.py", "pytest tests/a.py", "C:/py/python.exe -m pytest")
        self.assert_credits(["other"], "cd '%(o)s' && python scripts/run_tests.py")

    def test_a_folder_change_in_a_block_gives_no_root(self):
        self.assert_credits([], "if (Test-Path '%(o)s') { cd '%(o)s' }; pytest", "try { cd '%(o)s' } catch {}; pytest",
                            "foreach ($d in 1) { cd '%(o)s' }; pytest", "{ cd '%(o)s'; }; pytest",
                            "try { Set-Location '%(o)s' } catch {}; pytest", "if ($x) { Push-Location '%(o)s' }; pytest",
                            "if ($x) { echo a; cd '%(o)s' }; pytest", "if ($x) {\ncd '%(o)s'\n}\npytest",
                            "if [ -d x ]; then\ncd '%(o)s'\nfi\npytest", "if a; then b; cd '%(o)s'; fi; pytest",
                            "for d in a; do\ncd '%(o)s'\ndone\npytest", "1 | ForEach-Object { cd '%(o)s' }; pytest",
                            "try { cd '%(o)s'; pytest } catch {}")

    def test_a_block_with_no_folder_change_changes_nothing(self):
        self.assert_credits(["root"], "if (Test-Path '%(o)s') { echo a }; pytest", "try { make } catch {}; pytest",
                            "foreach ($d in 1) { make }; pytest", "if a; then b; fi; pytest", "for d in a; do\nb\ndone\npytest")

    def test_a_cd_after_a_closed_block_still_moves_the_check(self):
        self.assert_credits(["other"], "if ($x) { echo a }; cd '%(o)s'; pytest", "if a; then b; fi; cd '%(o)s' && pytest",
                            "for d in a; do\nb\ndone\ncd '%(o)s' && pytest")

    def test_a_backgrounded_folder_change_gives_no_root(self):
        self.assert_credits([], "cd '%(o)s' & pytest", "Set-Location '%(o)s' & pytest", "Push-Location '%(o)s' & pytest",
                            "cd '%(o)s' && make & pytest")

    def test_a_background_stage_with_no_cd_leaves_the_folder_alone(self):
        self.assert_credits(["root"], "make & pytest")
        self.assert_credits(["other"], "make & cd '%(o)s' && pytest", "cd '%(o)s'; make & pytest")

    def test_a_path_argument_outside_the_root_gives_no_root(self):
        self.assert_credits([], "pytest '%(o)s/tests'", "pytest ../other/tests", "npx vitest run %(o)s/tests",
                            "dotnet test %(o)s/x.csproj", "pytest -q %(o)s/tests", "pytest -c '%(o)s/pytest.ini'",
                            "pytest --cov=../other", "pytest tests/../../other", "python -m pytest ../other/tests")

    def test_a_path_argument_inside_the_root_counts(self):
        self.assert_credits(["root"], "pytest tests/a.py", "pytest ./tests", "pytest '%(r)s/tests'", "pytest tests/../a.py",
                            "pytest ./...", "dotnet test x.csproj", "pytest --cov=./src", "pytest --cov=%(r)s/src")
        self.assertEqual(self.credited("pytest ../tests", self.sub), ["root"])

    def test_a_path_argument_keeps_its_spaces(self):
        spaced = self.repo("a b", "app")
        path = self.stream("main.jsonl", shell(5, spaced, "pytest '%s/tests'" % spaced))
        self.assertEqual(te.last_pass([path], spaced, CHECKS, 10).ts, at(5))

    def test_a_redirect_target_is_no_path_argument(self):
        self.assert_credits(["root"], "pytest > '%(o)s/log.txt'", "pytest 2> %(o)s/err.txt", "pytest *> %(o)s/all.txt",
                            "pytest >> ../log.txt")

    def test_a_windows_path_argument_climbs_too(self):
        self.addCleanup(setattr, te, "WINDOWS", te.WINDOWS)
        te.WINDOWS = True
        self.assertEqual(te._check_folders("pytest ..\\other\\tests", "X:/w/app", CHECKS), ["x:/w/app", "x:/w/other/tests"])


class StampOrder(TempDir):
    """Stamps are not in strict order, so an old record among new ones does not end the stream."""

    def old_records(self, count):
        return [tool_use("t", "Read", {}, at(0), "x") for _ in range(count)]

    def test_one_old_record_mid_turn_keeps_the_earlier_edit(self):
        path = self.stream("main.jsonl", edit(2, "a.py"), self.old_records(1), edit(5, "b.py"))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(3), "a.py"), (at(6), "b.py")])

    def test_a_run_wider_than_any_in_the_corpus_keeps_it_too(self):
        path = self.stream("main.jsonl", edit(2, "a.py"), self.old_records(LONG_RUN), edit(5, "b.py"))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(3), "a.py"), (at(6), "b.py")])

    def test_a_pass_below_an_old_record_is_found(self):
        path = self.stream("main.jsonl", shell(5, self.root), self.old_records(1))
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(5), "ok")

    def test_a_pass_behind_a_long_run_is_found(self):
        main = self.stream("main.jsonl", shell(5, self.root))
        sub = self.stream("sub.jsonl", shell(8, self.root), self.old_records(LONG_RUN), edit(9, "x.py"))
        self.assert_pass(te.last_pass([main, self.touched(sub, 20)], self.root, CHECKS, 10), at(8), "ok")

    def test_evidence_behind_a_long_run_is_found(self):
        browser = call("b", "mcp__claude-in-chrome__navigate", {}, 2, "x", "ok")
        path = self.stream("main.jsonl", browser, self.old_records(LONG_RUN), edit(5, "b.py"))
        self.assertEqual(te.evidence_after([path], at(1), 10), "browser")

    def test_no_evidence_behind_a_long_run_is_none(self):
        path = self.stream("main.jsonl", self.old_records(LONG_RUN), edit(5, "b.py"))
        self.assertIsNone(te.evidence_after([path], at(1), 10))


class TurnEnd(TempDir):
    """The calling stream's walk ends at its last turn start, whatever the stamps behind it say."""

    def browser(self, minute):
        return call("b", "mcp__claude-in-chrome__navigate", {}, minute, "x", "ok")

    def turn(self, minute):
        return user_line("do it", at(minute))

    def test_the_walk_ends_at_the_turn_start_so_a_long_stream_costs_little(self):
        parsed = []
        real_loads = te.json.loads

        def noting_loads(line):
            parsed.append(line)
            return real_loads(line)

        path = self.stream("main.jsonl", edit(1, "older.py"), [tool_use("t", "Read", {}, at(0), "x")] * LONG_RUN,
                           self.turn(4), edit(5, "a.py"))
        te.json.loads = noting_loads
        self.addCleanup(setattr, te.json, "loads", real_loads)
        self.assertEqual(te.turn_changes([path], at(4)), [(at(6), "a.py")])
        self.assertLess(len(parsed), 10)

    def test_a_newer_stamp_before_the_turn_start_is_not_in_the_turn(self):
        path = self.stream("main.jsonl", edit(7, "older.py"), self.turn(4), edit(5, "a.py"))
        self.assertEqual(te.turn_changes([path], te.turn_start_ts(path)), [(at(6), "a.py")])

    def test_evidence_before_the_turn_start_is_not_in_the_turn(self):
        path = self.stream("main.jsonl", self.browser(6), self.turn(4), edit(5, "a.py"))
        self.assertIsNone(te.evidence_after([path], at(5), 10))

    def test_a_turn_start_newer_than_the_cut_does_not_end_the_walk(self):
        path = self.stream("main.jsonl", edit(2, "a.py"), self.turn(4), edit(5, "b.py"))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(3), "a.py"), (at(6), "b.py")])

    def test_a_task_notification_does_not_end_the_walk(self):
        note = user_line("<task-notification>done</task-notification>", at(3), turnOrigin="task_notification")
        path = self.stream("main.jsonl", edit(7, "a.py"), note, edit(5, "b.py"))
        self.assertEqual(te.turn_changes([path], at(3)), [(at(6), "b.py"), (at(8), "a.py")])

    def test_only_the_calling_stream_ends_at_a_turn_start(self):
        main = self.stream("main.jsonl", self.turn(1))
        sub = self.stream("sub.jsonl", edit(7, "a.py"), self.browser(6), self.turn(4))
        self.assertEqual(te.turn_changes([main, sub], at(5)), [(at(8), "a.py")])
        self.assertEqual(te.evidence_after([main, self.touched(sub, 20)], at(5), 10), "browser")

    def test_a_stream_written_before_the_cut_is_passed_over(self):
        main = self.stream("main.jsonl", self.turn(1))
        sub = self.stream("sub.jsonl", self.browser(6))
        for minute, want in ((4, None), (8, "browser")):
            with self.subTest(minute):
                self.assertEqual(te.evidence_after([main, self.touched(sub, minute)], at(5), 10), want)

    def test_a_missing_other_stream_still_raises(self):
        main = self.stream("main.jsonl", self.turn(1))
        with self.assertRaises(OSError):
            te.evidence_after([main, os.path.join(self.base, "missing.jsonl")], at(5), 10)


class BadShapes(TempDir):
    """Valid JSON of a shape no tool writes is skipped, as a junk line is."""

    def bad_lines(self):
        wrong_id = tool_result("t", "12 passed", at(9), self.root)
        wrong_id["message"]["content"][0]["tool_use_id"] = ["t"]
        body = [{"type": "text", "text": 5}, {"type": "text", "text": "2 failed"}]
        number_body = tool_result("t", body, at(9), self.root)
        number_name = tool_use("t", 5, {"command": "pytest"}, at(8), self.root)
        list_id = tool_use(["t"], "Bash", {"command": "pytest"}, at(8), self.root)
        number_stamp = tool_use("t", "Bash", {"command": "pytest"}, 5, self.root)
        return [
            ("input as text", call("t", "Bash", "pytest", 8, self.root, "12 passed")),
            ("input as text, edit tool", call("t", "Edit", "a.py", 8, self.root, "updated")),
            ("number command", call("t", "Bash", {"command": 5}, 8, self.root, "12 passed")),
            ("junk stamp", [tool_use("t", "Bash", {"command": "pytest"}, "not a time", self.root),
                            tool_result("t", "12 passed", "not a time", self.root)]),
            ("stamp a date alone", [tool_use("t", "Bash", {"command": "pytest"}, "2026-11-03", self.root),
                                    tool_result("t", "12 passed", at(9), self.root)]),
            ("stamp with text behind", [tool_use("t", "Bash", {"command": "pytest"}, at(8) + " x", self.root),
                                        tool_result("t", "12 passed", at(9), self.root)]),
            ("number stamp", [number_stamp, tool_result("t", "12 passed", at(9), self.root)]),
            ("list tool_use_id", [wrong_id]),
            ("number in a body", [tool_use("t", "Bash", {"command": "pytest"}, at(8), self.root), number_body]),
            ("number name", [number_name, tool_result("t", "12 passed", at(9), self.root)]),
            ("list id", [list_id, tool_result("t", "12 passed", at(9), self.root)]),
        ]

    def stream_with(self, bad):
        return self.stream("main.jsonl", edit(1, "a.py"), shell(3, self.root), shell(5, "x", "curl -s x", "<html>"), bad)

    def test_a_bad_line_leaves_the_answers_of_the_good_ones(self):
        for name, bad in self.bad_lines():
            with self.subTest(name):
                path = self.stream_with(bad)
                self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(3), "ok")
                self.assertEqual(te.turn_changes([path], at(0)), [(at(2), "a.py")])
                self.assertEqual(te.evidence_after([path], at(0), 10), "curl")

    def test_a_cwd_of_a_bad_shape_is_no_folder(self):
        run = shell(8, self.root)
        run[0]["cwd"] = 5
        path = self.stream_with(run)
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(3), "ok")

    def test_a_path_of_a_bad_shape_is_no_change(self):
        path = self.stream("main.jsonl", call("t", "Edit", {"file_path": 5}, 5, "x", "updated"))
        self.assertEqual(te.turn_changes([path], at(1)), [])


# A scan budget leaves room for a stall before the scan reaches the stuck read, and stays under the 4 s that read holds;
# a budget kept means the call returns well before those 4 s.
STALL_ROOM = 2.0
ON_TIME = 3.5


class Budget(TempDir):
    def stick_reads_of(self, stuck_path):
        """Reads of one file block until the test ends; the stub proves the scan got there."""
        reached = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        real_open = open

        class Stuck:
            def __init__(self, path):
                self.handle = real_open(path, "rb")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.handle.close()
                closed.set()

            def seek(self, *args):
                return self.handle.seek(*args)

            def tell(self):
                return self.handle.tell()

            def read(self, size):
                reached.set()
                release.wait(4)
                return self.handle.read(size)

        def fake_open(path, mode):
            return Stuck(path) if path == stuck_path else real_open(path, mode)

        te.open = fake_open
        self.release = release
        self.addCleanup(closed.wait, 6)
        self.addCleanup(release.set)
        self.addCleanup(delattr, te, "open")
        return reached

    def test_a_blocked_read_gives_budget_on_time(self):
        path = self.stream("main.jsonl", shell(5, self.root))
        reached = self.stick_reads_of(path)
        started = time.monotonic()
        found = te.last_pass([path], self.root, CHECKS, STALL_ROOM)
        self.assertLess(time.monotonic() - started, ON_TIME)
        self.assertTrue(reached.is_set())
        self.assert_pass(found, "", "budget")

    def test_evidence_blocked_by_a_read_is_unknown_on_time(self):
        path = self.stream("main.jsonl", edit(5, "a.py"))
        reached = self.stick_reads_of(path)
        started = time.monotonic()
        self.assertEqual(te.evidence_after([path], at(1), STALL_ROOM), "unknown")
        self.assertLess(time.monotonic() - started, ON_TIME)
        self.assertTrue(reached.is_set())

    def test_edits_blocked_by_a_read_are_none_on_time(self):
        path = self.stream("main.jsonl", edit(5, "a.py"))
        reached = self.stick_reads_of(path)
        started = time.monotonic()
        self.assertIsNone(te.turn_edits([path], at(1), STALL_ROOM))
        self.assertLess(time.monotonic() - started, ON_TIME)
        self.assertTrue(reached.is_set())

    def test_a_scan_inside_the_budget_is_ok(self):
        path = self.stream("main.jsonl", shell(5, self.root))
        self.assert_pass(te.last_pass([path], self.root, CHECKS, 10), at(5), "ok")

    def test_the_pass_found_before_the_block_is_kept(self):
        main = self.stream("main.jsonl", shell(5, self.root))
        sub = self.touched(self.stream("agent-a.jsonl", shell(9, self.root)), 10)
        reached = self.stick_reads_of(sub)
        found = te.last_pass([main, sub], self.root, CHECKS, STALL_ROOM)
        self.assertTrue(reached.is_set())
        self.assert_pass(found, at(5), "budget")

    def test_a_hidden_check_found_before_the_block_is_kept(self):
        main = self.stream("main.jsonl", shell(5, self.root, "pytest | tail -1", "some lines"))
        sub = self.touched(self.stream("agent-a.jsonl", shell(9, self.root)), 10)
        reached = self.stick_reads_of(sub)
        found = te.last_pass([main, sub], self.root, CHECKS, STALL_ROOM)
        self.assertTrue(reached.is_set())
        self.assert_pass(found, "", "budget")
        self.assertEqual(found.hidden, at(5))

    def test_the_answer_stays_as_returned_when_the_scan_ends_later(self):
        path = self.stream("main.jsonl", shell(5, self.root))
        reached = self.stick_reads_of(path)
        before = set(threading.enumerate())
        found = te.last_pass([path], self.root, CHECKS, STALL_ROOM)
        self.assertTrue(reached.is_set())
        self.release.set()
        workers = set(threading.enumerate()) - before
        self.assertEqual(len(workers), 1)
        for worker in workers:
            worker.join(6)
            self.assertFalse(worker.is_alive())
        self.assert_pass(found, "", "budget")

    def test_a_scan_blocked_for_good_does_not_hold_the_process_open(self):
        code = (
            "import sys, threading\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import turn_evidence as te\n"
            "class Blocked:\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *_): pass\n"
            "    def seek(self, *args): return 0\n"
            "    def tell(self): return 10\n"
            "    def read(self, size): threading.Event().wait()\n"
            "te.open = lambda path, mode: Blocked()\n"
            "print(te.last_pass(['x'], 'r', ['pytest'], 0.2).reason)\n"
        )
        try:
            done = child(code, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("the process stayed alive behind a blocked scan")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "budget"))


class TurnChanges(TempDir):
    def test_a_failed_edit_is_no_change(self):
        path = self.stream("main.jsonl", edit(5, "failed.py", is_error=True), edit(7, "done.py"))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(8), "done.py")])

    def test_each_edit_tool_counts_and_others_do_not(self):
        for name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(name):
                path = self.stream("main.jsonl", edit(5, "a.py", name=name))
                self.assertEqual(te.turn_changes([path], at(1)), [(at(6), "a.py")])
        path = self.stream("main.jsonl", edit(5, "a.py", name="Read"))
        self.assertEqual(te.turn_changes([path], at(1)), [])

    def test_a_notebook_edit_names_its_file_in_its_own_key(self):
        data = {"notebook_path": "a.ipynb", "new_source": "x"}
        path = self.stream("main.jsonl", call("t", "NotebookEdit", data, 5, "x", "updated"))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(6), "a.ipynb")])

    def test_a_pending_edit_is_no_change(self):
        path = self.stream("main.jsonl", edit(5, "a.py")[0])
        self.assertEqual(te.turn_changes([path], at(1)), [])

    def test_an_edit_with_no_path_is_no_change(self):
        for data in ({}, {"file_path": ""}, {"file_path": "", "notebook_path": ""}):
            with self.subTest(data):
                path = self.stream("main.jsonl", call("t", "Edit", data, 5, "x", "updated"))
                self.assertEqual(te.turn_changes([path], at(1)), [])

    def test_edits_before_the_turn_are_left_out(self):
        path = self.stream("main.jsonl", edit(1, "old.py"), edit(5, "new.py"))
        self.assertEqual(te.turn_changes([path], at(5)), [(at(6), "new.py")])

    def test_streams_merge_in_time_order(self):
        one = self.stream("a.jsonl", edit(5, "a.py"))
        two = self.stream("b.jsonl", edit(2, "b.py"))
        self.assertEqual(te.turn_changes([one, two], at(1)), [(at(3), "b.py"), (at(6), "a.py")])

    def test_a_missing_stream_raises(self):
        with self.assertRaises(OSError):
            te.turn_changes([os.path.join(self.base, "missing.jsonl")], at(1))

    def test_a_non_ascii_file_name_comes_back_whole(self):
        name = "plugins/naïve/façade.py"
        path = self.stream("main.jsonl", edit(5, name))
        self.assertEqual(te.turn_changes([path], at(1)), [(at(6), name)])


class TurnEdits(TempDir):
    """turn_changes over every stream, within a budget."""

    def test_every_stream_counts_and_they_merge_in_time_order(self):
        one = self.stream("a.jsonl", edit(5, "a.py"))
        two = self.stream("b.jsonl", edit(2, "b.py"))
        self.assertEqual(te.turn_edits([one, two], at(1), 10), [(at(3), "b.py"), (at(6), "a.py")])

    def test_the_calling_stream_ends_at_its_turn_start(self):
        path = self.stream("main.jsonl", edit(7, "older.py"), user_line("do it", at(4)), edit(5, "a.py"))
        self.assertEqual(te.turn_edits([path], te.turn_start_ts(path), 10), [(at(6), "a.py")])

    def test_only_the_calling_stream_ends_at_a_turn_start(self):
        main = self.stream("main.jsonl", user_line("do it", at(1)))
        sub = self.stream("sub.jsonl", edit(7, "a.py"), user_line("do it", at(4)))
        self.assertEqual(te.turn_edits([main, sub], at(5), 10), [(at(8), "a.py")])

    def test_a_stream_written_before_the_turn_is_passed_over(self):
        main = self.stream("main.jsonl", user_line("do it", at(1)))
        sub = self.stream("sub.jsonl", edit(7, "a.py"))
        for minute, want in ((4, []), (9, [(at(8), "a.py")])):
            with self.subTest(minute):
                self.assertEqual(te.turn_edits([main, self.touched(sub, minute)], at(5), 10), want)

    def test_no_turn_start_passes_over_nothing(self):
        path = self.stream("main.jsonl", edit(5, "a.py"))
        self.assertEqual(te.turn_edits([path], "", 10), [(at(6), "a.py")])

    def test_a_missing_stream_raises(self):
        main = self.stream("main.jsonl", user_line("do it", at(1)))
        with self.assertRaises(OSError):
            te.turn_edits([main, os.path.join(self.base, "missing.jsonl")], at(5), 10)


class EvidenceAfter(TempDir):
    def evidence(self, *groups, ts=None):
        return te.evidence_after([self.stream("main.jsonl", *groups)], ts or at(3), 10)

    def test_each_browser_family_counts(self):
        for name in ("mcp__Claude_Browser__navigate", "mcp__claude-in-chrome__click",
                     "mcp__plugin_chrome-devtools-mcp_chrome__take_snapshot", "mcp__plugin_browser-use_cdp__open"):
            with self.subTest(name):
                self.assertEqual(self.evidence(call("t", name, {}, 5, "x", "page")), "browser")

    def test_other_tools_do_not_count_as_a_browser(self):
        for name in ("mcp__tracker__ListTickets", "mcp__Claude_Browse__x", "Read"):
            with self.subTest(name):
                self.assertIsNone(self.evidence(call("t", name, {}, 5, "x", "page")))

    def test_a_browser_call_that_failed_or_never_returned_does_not_count(self):
        name = "mcp__claude-in-chrome__navigate"
        self.assertIsNone(self.evidence(call("t", name, {}, 5, "x", "net::ERR_CONNECTION_REFUSED", is_error=True)))
        self.assertIsNone(self.evidence(call("t", name, {}, 5, "x", "page")[0]))

    def test_a_browser_call_before_the_change_does_not_count(self):
        self.assertIsNone(self.evidence(call("t", "mcp__Claude_Browser__navigate", {}, 1, "x", "page"), edit(5, "a")))

    def test_curl_with_output_counts(self):
        for command in ("curl -s x", "curl.exe -s x", "cd a && curl -s x", "CURL x", "curl -s x | head -3"):
            with self.subTest(command):
                self.assertEqual(self.evidence(shell(5, "x", command, "<html>")), "curl")
        self.assertEqual(self.evidence(shell(5, "x", "curl -s x", "<html>", name="PowerShell")), "curl")

    def test_curl_that_shows_nothing_does_not_count(self):
        for output in ("", "  \n", NO_BASH_OUTPUT, NO_POWERSHELL_OUTPUT):
            with self.subTest(output):
                self.assertIsNone(self.evidence(shell(5, "x", "curl -s x", output)))

    def test_curl_with_no_output_of_its_own_does_not_count(self):
        for command in ("curl -s x >/dev/null; echo ok", "curl -s x >/dev/null", "curl -s x > out.html", "curl -s x &> log",
                        "curl -s x 1> out", "curl -s a && curl -s b > out", "curl --version", "curl -V", "curl --help",
                        "curl -h", "curl.exe --version",
                        "curl -s x || echo failed", "curl -s x; echo ok", "curl -s x\necho ok", "curl -s x & echo ok",
                        "curl -s -o /dev/null x", "curl -so /dev/null x", "curl --output out x", "curl --output=out x",
                        "curl -O x", "curl --remote-name x"):
            with self.subTest(command):
                self.assertIsNone(self.evidence(shell(5, "x", command, "ok")))

    def test_curl_that_shows_its_own_output_counts(self):
        for command in ("curl -s -o /dev/null -w '%{http_code}' x", "curl -so /dev/null -sw '%{http_code}' x",
                        "curl -s -o /dev/null --write-out '%{http_code}' x", "curl -s --output=/dev/null --write-out=x x",
                        "curl -s x 2>/dev/null", "curl -s x 2>&1", "curl -s x *>&1", "curl -s x | Select-Object -First 3",
                        "curl -s x | head -3 | cut -c1-9", "curl -s a; curl -s b", "echo a; curl -s x",
                        "curl -s a > out && curl -s b", "curl -s --help-me x"):
            with self.subTest(command):
                self.assertEqual(self.evidence(shell(5, "x", command, "200")), "curl")

    def test_a_failed_curl_does_not_count(self):
        self.assertIsNone(self.evidence(shell(5, "x", "curl -s x", "boom", is_error=True)))
        self.assertIsNone(self.evidence(shell(5, "x", "curl -s x", "Exit code 7\nboom")))
        self.assertIsNone(self.evidence(shell(5, "x", "curl -s x", "Command failed with exit code 7\nboom")))

    def test_words_that_only_name_curl_do_not_count(self):
        for command in ("rg curl", "echo curl", "curlew x", "echo 'curl x'"):
            with self.subTest(command):
                self.assertIsNone(self.evidence(shell(5, "x", command, "<html>")))

    def test_curl_from_another_tool_or_still_running_does_not_count(self):
        self.assertIsNone(self.evidence(call("t", "Agent", {"command": "curl x"}, 5, "x", "<html>")))
        self.assertIsNone(self.evidence(shell(5, "x", "curl x", "<html>")[0]))

    def test_curl_before_the_change_does_not_count(self):
        self.assertIsNone(self.evidence(shell(1, "x", "curl x", "<html>"), edit(5, "a")))

    def test_a_second_stream_can_hold_the_evidence(self):
        one = self.stream("a.jsonl", edit(5, "a"))
        two = self.stream("b.jsonl", shell(5, "x", "curl x", "<html>"))
        self.assertEqual(te.evidence_after([one, two], at(3), 10), "curl")

    def test_a_missing_stream_raises(self):
        with self.assertRaises(OSError):
            te.evidence_after([os.path.join(self.base, "missing.jsonl")], at(1), 10)


class ToEpoch(unittest.TestCase):
    def setUp(self):
        if hasattr(time, "tzset"):
            saved = os.environ.get("TZ")
            os.environ["TZ"] = "Asia/Tokyo"
            time.tzset()
            self.addCleanup(self.restore_zone, saved)

    @staticmethod
    def restore_zone(saved):
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        time.tzset()

    def test_exact_epochs(self):
        self.assertEqual(te.to_epoch("2026-11-03T10:00:00.250Z"), 1793700000.25)
        self.assertEqual(te.to_epoch("2026-11-03T10:00:00Z"), 1793700000.0)
        self.assertEqual(te.to_epoch("2024-02-29T23:59:59.500Z"), 1709251199.5)

    def test_a_timestamp_that_is_no_time_raises(self):
        with self.assertRaises(ValueError):
            te.to_epoch("not a time")


class ScopeMatch(unittest.TestCase):
    def test_double_star_spans_any_depth_from_zero(self):
        for path in ("plugins/a.py", "plugins/x/a.py", "plugins/x/y/a.py", "plugins\\x\\a.py"):
            with self.subTest(path):
                self.assertTrue(te.scope_match(path, ["plugins/**/*.py"]))
        for path in ("a.py", "other/a.py", "pluginsx/a.py", "plugins/a.txt", "plugins/a.pyc", "plugins/ax/py"):
            with self.subTest(path):
                self.assertFalse(te.scope_match(path, ["plugins/**/*.py"]))

    def test_a_leading_double_star_includes_the_root(self):
        for path in ("a.py", "x/a.py", "x/y/a.py"):
            with self.subTest(path):
                self.assertTrue(te.scope_match(path, ["**/a.py"]))
        self.assertFalse(te.scope_match("x/ba.py", ["**/a.py"]))

    def test_a_single_star_stays_in_one_folder(self):
        self.assertTrue(te.scope_match("src/a.py", ["src/*.py"]))
        self.assertFalse(te.scope_match("src/x/a.py", ["src/*.py"]))
        self.assertFalse(te.scope_match("a.py", ["src/*.py"]))

    def test_a_glob_with_no_slash_matches_the_basename(self):
        for path in ("a.py", "x/a.py", "x/y/a.py"):
            with self.subTest(path):
                self.assertTrue(te.scope_match(path, ["*.py"]))
        for path in ("a.pyc", "a.py/x", "x.py/a.txt"):
            with self.subTest(path):
                self.assertFalse(te.scope_match(path, ["*.py"]))

    def test_regex_characters_are_literal(self):
        self.assertTrue(te.scope_match("a+b.py", ["a+b.py"]))
        self.assertFalse(te.scope_match("aab.py", ["a+b.py"]))
        self.assertFalse(te.scope_match("axpy", ["a.py"]))

    def test_any_glob_can_match(self):
        self.assertTrue(te.scope_match("a.md", ["*.py", "*.md"]))
        self.assertFalse(te.scope_match("a.md", []))

    def test_a_non_ascii_path_matches(self):
        self.assertTrue(te.scope_match("plugins/naïve/façade.py", ["plugins/**/*.py"]))


class RowKey(unittest.TestCase):
    def test_known_digests(self):
        self.assertEqual(te.row_key("a", "b"), "59b271ae1bbc")
        self.assertEqual(te.row_key("crème"), "b45a901f23ee")

    def test_the_parts_stay_apart(self):
        self.assertNotEqual(te.row_key("ab", "c"), te.row_key("a", "bc"))

    def test_a_lone_surrogate_from_json_keys_like_any_text(self):
        self.assertRegex(te.row_key("\ud83d"), "^[0-9a-f]{12}$")
        self.assertNotEqual(te.row_key("\ud83d"), te.row_key("\ud83e"))


class Norm(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, te, "WINDOWS", te.WINDOWS)

    def test_windows_spellings_compare_equal(self):
        te.WINDOWS = True
        drive = "X:"
        same = drive.lower() + "/src/app"
        self.assertEqual(te.norm("/x/src/App/"), same)
        self.assertEqual(te.norm("\\".join((drive, "src", "APP"))), same)
        self.assertEqual(te.norm("'%s/src//app'" % drive), same)

    def test_posix_paths_keep_their_case_and_a_one_letter_folder(self):
        te.WINDOWS = False
        self.assertEqual(te.norm("/x/src/App/"), "/x/src/App")
        self.assertEqual(te.norm("/srv//Data/App"), "/srv/Data/App")

    def test_no_path_is_empty(self):
        self.assertEqual(te.norm(None), "")


class StreamPaths(TempDir):
    def files(self):
        main = self.stream("session.jsonl")
        sub = self.stream("session/subagents/agent-a.jsonl")
        workflow = self.stream("session/subagents/workflows/wf_1/agent-w.jsonl")
        self.stream("session/subagents/agent-a.meta.json")
        self.stream("session/subagents/workflows/wf_1/agent-w.prefix.json")
        self.stream("session/subagents/workflows/wf_1/journal.jsonl")
        self.stream("session/subagents/journal.jsonl")
        self.stream("other/subagents/agent-z.jsonl")
        return main, sub, workflow

    def test_the_main_stream_calls_first(self):
        main, sub, workflow = self.files()
        found = te.stream_paths(main, "")
        self.assertEqual(found[0], main)
        self.assertEqual(sorted(found), sorted([main, sub, workflow]))

    def test_a_subagent_calls_first_and_the_main_stream_still_counts(self):
        main, sub, workflow = self.files()
        found = te.stream_paths(main, "a")
        self.assertEqual(found[0], sub)
        self.assertEqual(sorted(found), sorted([main, sub, workflow]))

    def test_a_workflow_agent_is_found_in_its_folder(self):
        main, sub, workflow = self.files()
        found = te.stream_paths(main, "w")
        self.assertEqual(found[0], workflow)
        self.assertEqual(len(found), 3)

    def test_an_agent_with_no_file_reads_as_unreadable(self):
        main, sub, workflow = self.files()
        found = te.stream_paths(main, "gone")
        self.assertEqual(os.path.basename(found[0]), "agent-gone.jsonl")
        self.assert_pass(te.last_pass(found, self.root, CHECKS, 10), "", "unreadable")

    def test_a_session_with_no_subagents_is_the_main_stream_alone(self):
        main = self.stream("session.jsonl")
        self.assertEqual(te.stream_paths(main, ""), [main])

    def test_no_transcript_gives_no_streams(self):
        self.assertEqual(te.stream_paths("", ""), [])
        self.assertEqual(te.stream_paths(None, "a"), [])


class ReversedLines(TempDir):
    def lines_of(self, *lines):
        path = os.path.join(self.base, "lines.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\n".join(lines))
        return path

    def test_lines_come_last_to_first_and_blank_ones_go(self):
        self.assertEqual(list(te.reversed_lines(self.lines_of(b"a", b"", b"b", b""))), [b"b", b"a"])

    def test_a_last_line_with_no_newline_is_a_line(self):
        self.assertEqual(list(te.reversed_lines(self.lines_of(b"a", b"torn"))), [b"torn", b"a"])

    def test_lines_across_chunk_edges_stay_whole(self):
        char = "é".encode("utf-8")
        lines = [b"a" * (te.CHUNK - 3) + char * 3, b"b" * 5, char * te.CHUNK, b"c"]
        self.assertEqual(list(te.reversed_lines(self.lines_of(*lines))), lines[::-1])

    def test_reads_are_chunks_and_a_near_answer_reads_one(self):
        path = self.lines_of(*[b"x" * 1023] * 3000)
        sizes = []
        real_open = open

        class Counted:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.handle.close()

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def read(self, size):
                sizes.append(size)
                return self.handle.read(size)

        te.open = lambda name, mode: Counted(real_open(name, mode))
        self.addCleanup(delattr, te, "open")
        self.assertEqual(next(te.reversed_lines(path)), b"x" * 1023)
        self.assertEqual(sizes, [1 << 20])
        sizes.clear()
        self.assertEqual(len(list(te.reversed_lines(path))), 3000)
        self.assertEqual(sum(sizes), os.path.getsize(path))
        self.assertEqual(max(sizes), 1 << 20)


class Purity(TempDir):
    IMPORT_ONLY = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import builtins, calendar, hashlib, json, os, re, time\n"
        "def refuse(*args, **kwargs):\n"
        "    raise SystemExit('work at import')\n"
        "re.compile = re._compile = builtins.open = os.scandir = os.stat = os.walk = refuse\n"
        "import turn_evidence\n"
        "print('imported')\n"
    )
    REFUSED = IMPORT_ONLY.replace("import turn_evidence\n", "re.compile('x')\n")
    SCAN = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import turn_evidence as te\n"
        "streams = te.stream_paths(sys.argv[2], '')\n"
        "found = te.last_pass(streams, sys.argv[3], ['pytest'], 20)\n"
        "print(json.dumps([len(streams), found.ts, found.reason, te.turn_changes(streams, ''), te.to_epoch(found.ts),\n"
        "                  te.row_key('a', 'b'), te.scope_match('a.py', ['*.py']), te.evidence_after(streams, '', 10),\n"
        "                  te.turn_edits(streams, '', 10)]))\n"
    )

    def test_importing_does_no_work(self):
        done = child(self.IMPORT_ONLY)
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr), (0, "imported", ""))

    def test_the_import_probe_can_fail(self):
        done = child(self.REFUSED)
        self.assertEqual(done.returncode, 1)
        self.assertIn("work at import", done.stderr)

    def test_only_definitions_and_constants_sit_at_module_level(self):
        with open(os.path.join(TOOL_DIR, "turn_evidence.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        allowed = (ast.Constant, ast.Name, ast.Tuple, ast.BinOp, ast.Add, ast.LShift, ast.Compare, ast.Eq, ast.Load,
                   ast.Attribute)
        statements = 0
        for node in tree.body:
            if isinstance(node, ast.Assign):
                statements += 1
                for part in ast.walk(node.value):
                    self.assertIsInstance(part, allowed, ast.dump(node.value))
            else:
                self.assertIsInstance(node, (ast.Import, ast.ClassDef, ast.FunctionDef, ast.Expr))
                if isinstance(node, ast.Expr):
                    self.assertIsInstance(node.value, ast.Constant)
        self.assertGreater(statements, 10)

    def test_imports_are_stdlib_only(self):
        with open(os.path.join(TOOL_DIR, "turn_evidence.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse([node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)])
        self.assertIn("json", names)
        self.assertEqual([name for name in names if name not in sys.stdlib_module_names], [])

    def test_the_source_parses_as_python_3_10(self):
        with open(os.path.join(TOOL_DIR, "turn_evidence.py"), encoding="utf-8") as handle:
            ast.parse(handle.read(), feature_version=(3, 10))

    def test_a_non_ascii_path_scans_with_no_site_and_no_utf8_mode(self):
        root = self.repo("naïve", "façade")
        name = os.path.join(root, "plik.py")
        path = write_stream(os.path.join(self.base, "naïve", "sess-crème.jsonl"), edit(1, name), shell(5, root))
        sub = os.path.join(self.base, "naïve", "sess-crème", "subagents", "agent-ü.jsonl")
        write_stream(sub, edit(2, "b.py"))
        done = child(self.SCAN, path, root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        self.assertEqual(json.loads(done.stdout), [
            2, at(5), "ok", [[at(2), name], [at(3), "b.py"]], te.to_epoch(at(5)), "59b271ae1bbc", True, None,
            [[at(2), name], [at(3), "b.py"]],
        ])


if __name__ == "__main__":
    unittest.main()
