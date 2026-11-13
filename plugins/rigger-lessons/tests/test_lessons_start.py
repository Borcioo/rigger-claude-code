"""Tests for hooks/lessons_start.py (the SessionStart hook), hooks/skill_lessons.py and their hooks.json wiring.

Each test runs the hook in its own interpreter with CLAUDE_CONFIG_DIR, HOME and USERPROFILE under a
temp dir, or the inbox override on a temp file, so the real config dir is never read or written. Background
batches are off (RIGGER_LESSONS_BATCH unset) except in the tests that start one, which use a stub `claude`.
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(PLUGIN / "tools"), str(PLUGIN / "tests")]

import lessons_batch  # noqa: E402
from stub_claude import alive, wait_for  # noqa: E402

SCRIPT = PLUGIN / "hooks" / "lessons_start.py"
SKILL_SCRIPT = PLUGIN / "hooks" / "skill_lessons.py"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
TEMPLATE = PLUGIN / "templates" / "lessons-inbox.md"
NOTICE = (
    "Lessons proposals waiting: {count} in {queue}; propose `/rigger-lessons:curate-lessons` in one line — only in "
    "a session the user drives; a session started by another session never proposes or runs curation."
)
FAILURE = "Lessons batch failed {n} runs in a row; last log: {path}."
# A `claude` that passes the start check and does nothing.
STUB_CLAUDE = json.dumps([sys.executable, "-I", "-c", "import sys; sys.exit(1)"])
HOOK_COMMAND = (
    'sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice "${CLAUDE_PLUGIN_ROOT}/hooks/lessons_start.py"'
)


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        if git_sh.is_file():
            return str(git_sh)
        return shutil.which("sh")
    return "/bin/sh"


def entries(count, day=1):
    return "".join("- 2026-01-%02d · global · demo · case %d · rule %d\n" % (day, n, n) for n in range(count))


def reviewed(day):
    return (
        "- 2026-01-01 · global · demo · case · rule · signal_type: test-failure · source_ref: s#t · "
        "target: rules/x.md · why: a stated reason · review: %s\n" % day
    )


TODAY = {"HARNESS_LESSONS_TODAY": "2040-06-15"}  # a date the real clock has not reached


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


class LessonsStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lessons-start-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cfg = self.tmp / "cfg"

    def base_env(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "ANTHROPIC_", "HARNESS_"))}
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        return env

    def run_hook(self, config_dir=None, cwd=None, **extra):
        """Runs the hook without -X utf8, so its UTF-8 output is its own doing. Returns stdout text."""
        env = self.base_env()
        env["CLAUDE_CONFIG_DIR"] = str(self.cfg if config_dir is None else config_dir)
        if config_dir is False:
            del env["CLAUDE_CONFIG_DIR"]
        env.update(extra)
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(cwd or self.tmp),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertNotIn(b"\r", proc.stdout)
        self.assertLess(len(proc.stdout), 10000)
        try:
            text = proc.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.fail("hook output is not UTF-8: %s" % exc)
        return text.replace("\r\n", "\n")

    def inbox(self, text):
        path = self.cfg / "lessons-inbox.md"
        write_text(path, text)
        return path

    def count_line(self, path, count):
        return "Lessons inbox: %s: %d entries" % (path, count)

    # -- rule and count ---------------------------------------------------------------------

    def test_prints_only_the_inbox_count(self):
        path = self.inbox(entries(2))
        self.assertEqual(self.run_hook(), self.count_line(path, 2) + "\n")

    def test_count_includes_entries_under_other_headings(self):
        body = (
            "# Lessons inbox\n"
            "- 2026-01-01 · global · demo · before any heading · rule\n"
            "## Entries\n"
            + entries(2)
            + "## Archived by hand\n"
            "- 2025-12-31 · project:demo · demo · under another heading · rule\n"
            "  - 2026-01-01 indented, not an entry\n"
            "-2026-01-01 no space, not an entry\n"
            "* 2026-01-01 other bullet, not an entry\n"
            "- 26-01-01 short year, not an entry\n"
        )
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                path = self.inbox(body.replace("\n", newline))
                self.assertIn(self.count_line(path, 4), self.run_hook().splitlines())

    def test_missing_inbox_created_from_template(self):
        cfg = self.tmp / "nested" / "config dir"
        out = self.run_hook(config_dir=cfg)
        path = cfg / "lessons-inbox.md"
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), TEMPLATE.read_bytes())
        self.assertNotIn(b"\r\n", path.read_bytes())
        self.assertIn("## Entries", path.read_text(encoding="utf-8"))
        self.assertIn(self.count_line(path, 0), out.splitlines())

    def test_config_dir_honoured_and_inbox_override(self):
        cfg = self.tmp / "custom-cfg"
        out = self.run_hook(config_dir=cfg)
        self.assertIn(self.count_line(cfg / "lessons-inbox.md", 0), out.splitlines())
        self.assertFalse((self.home / ".claude").exists())
        override = self.tmp / "elsewhere" / "inbox.md"
        write_text(override, entries(2))
        other_cfg = self.tmp / "other-cfg"
        out = self.run_hook(config_dir=other_cfg, HARNESS_LESSONS_INBOX=str(override))
        self.assertIn(self.count_line(override, 2), out.splitlines())
        self.assertFalse((other_cfg / "lessons-inbox.md").exists())

    def test_inbox_override_expands_a_leading_tilde(self):
        out = self.run_hook(HARNESS_LESSONS_INBOX="~/notes/inbox.md")
        path = self.home / "notes" / "inbox.md"
        self.assertFalse((self.tmp / "~").exists())
        self.assertIn(self.count_line(path, 0), out.splitlines())
        self.assertEqual(path.read_bytes(), TEMPLATE.read_bytes())

    def test_dangling_inbox_link_is_reported_and_its_target_not_created(self):
        target = self.tmp / "gone-inbox.md"
        link = self.cfg / "lessons-inbox.md"
        self.cfg.mkdir()
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink on %s: %s" % (sys.platform, exc))
        out = self.run_hook()
        self.assertTrue(out.startswith("Lessons inbox: %s: count unknown (FileNotFoundError: " % link), out)
        self.assertFalse(target.exists())

    def test_inbox_that_appears_during_the_check_is_not_overwritten(self):
        spec = importlib.util.spec_from_file_location("lessons_start_race", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = self.inbox(entries(3))
        with mock.patch.object(module.os.path, "lexists", return_value=False):
            count, error, data = module.count_entries(path)
        self.assertIsNone(count)
        self.assertIsNone(data)
        self.assertTrue(error.startswith("FileExistsError: "), error)
        self.assertEqual(path.read_text(encoding="utf-8"), entries(3))

    def test_path_that_is_not_utf8_is_printed_escaped(self):
        path = str(self.tmp / "inbox-\udcff.md")
        self.assertIn("inbox-\\udcff.md: ", self.run_hook(HARNESS_LESSONS_INBOX=path))

    def test_config_root_falls_back_to_home(self):
        for value in (False, ""):
            with self.subTest(config_dir=value):
                out = self.run_hook(config_dir=value)
                path = self.home / ".claude" / "lessons-inbox.md"
                self.assertTrue(path.is_file())
                self.assertIn(self.count_line(path, 0), out.splitlines())
                self.assertFalse(self.cfg.exists())
                shutil.rmtree(self.home / ".claude")

    def test_non_ascii_entry_counted_and_output_utf8(self):
        cfg = self.tmp / "config-αβγδε"
        path = cfg / "lessons-inbox.md"
        write_text(path, "## Entries\n- 2026-02-03 · project:αβγδ · Αβγδ · αβγδε ζηθικ λμνξ · οπρστ\n")
        out = self.run_hook(config_dir=cfg)
        self.assertIn(self.count_line(path, 1), out.splitlines())

    def test_unreadable_inbox_count_unknown(self):
        path = self.cfg / "lessons-inbox.md"
        path.mkdir(parents=True)  # a directory: unreadable as a file on every OS, also for root
        out = self.run_hook()
        line = [l for l in out.splitlines() if l.startswith("Lessons inbox: ")]
        self.assertEqual(len(line), 1, out)
        self.assertTrue(line[0].startswith("Lessons inbox: %s: count unknown (" % path), line[0])
        self.assertTrue(line[0].endswith(")"))
        self.assertNotIn("entries", line[0])

    # -- lessons due for review -------------------------------------------------------------

    def due_line(self, count):
        return "Lessons due for review: %d" % count

    def test_review_date_today_is_due(self):
        path = self.inbox(reviewed("2040-06-15"))
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 1) + "\n" + self.due_line(1) + "\n")

    def test_review_date_in_the_past_is_due(self):
        path = self.inbox(reviewed("2040-06-14"))
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 1) + "\n" + self.due_line(1) + "\n")

    def test_review_date_in_the_future_is_not_due(self):
        path = self.inbox(reviewed("2040-06-16"))
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 1) + "\n")

    def test_entries_without_a_review_field_are_not_due(self):
        path = self.inbox(entries(2) + "- 2026-01-01 · global · demo · case · rule · why: because\n")
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 3) + "\n")

    def test_due_entries_are_counted_among_the_others(self):
        self.inbox(reviewed("2040-06-15") + reviewed("2025-01-01") + reviewed("2040-06-16") + entries(1))
        self.assertIn(self.due_line(2), self.run_hook(**TODAY).splitlines())

    def test_impossible_review_date_is_compared_as_text(self):
        self.inbox(reviewed("2020-13-99") + reviewed("2099-13-99"))
        self.assertIn(self.due_line(1), self.run_hook(**TODAY).splitlines())

    def test_review_text_outside_a_field_is_not_due(self):
        body = (
            "  - 2026-01-01 · global · demo · case · rule · review: 2020-01-01\n"
            "note · review: 2020-01-01\n"
            "x - 2026-01-01 · global · demo · case · rule · review: 2020-01-01\n"
            "- 26-01-01 · global · demo · case · rule · review: 2020-01-01\n"
            "- 2026-01-01 · global · demo · see review: 2020-01-01 in the PR · rule\n"
            "- 2026-01-01 · global · demo · case · rule · review 2020-01-01\n"
        )
        path = self.inbox(body)
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 2) + "\n")

    def test_due_entry_with_crlf_lines(self):
        path = self.inbox((entries(1) + reviewed("2040-06-15") + reviewed("2041-01-01")).replace("\n", "\r\n"))
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 3) + "\n" + self.due_line(1) + "\n")

    def test_due_entry_in_an_inbox_that_is_not_utf8(self):
        path = self.cfg / "lessons-inbox.md"
        path.parent.mkdir(parents=True)
        with open(path, "wb") as handle:
            handle.write(b"- 2026-01-01 \xff\xe9 \xc2\xb7 global \xc2\xb7 caf\xe9 \xc2\xb7 rule \xc2\xb7 review: 2020-01-01\n")
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 1) + "\n" + self.due_line(1) + "\n")

    def test_inbox_without_entries_has_nothing_due(self):
        path = self.inbox("# Lessons inbox\n\n## Entries\n")
        self.assertEqual(self.run_hook(**TODAY), self.count_line(path, 0) + "\n")

    def test_unreadable_inbox_adds_no_due_line(self):
        (self.cfg / "lessons-inbox.md").mkdir(parents=True)
        self.assertNotIn("due for review", self.run_hook(**TODAY))

    def test_due_line_comes_right_after_the_count_line_and_before_the_notice(self):
        path = self.inbox(entries(10) + reviewed("2020-01-01"))
        queue = self.queue(5)
        lines = self.run_hook(**TODAY).splitlines()
        self.assertEqual(lines, [self.count_line(path, 11), self.due_line(1), NOTICE.format(count=5, queue=queue)])

    def test_today_defaults_to_the_clock(self):
        self.inbox(reviewed("2000-01-01") + reviewed("2999-12-31"))
        self.assertIn(self.due_line(1), self.run_hook().splitlines())

    # -- background batch -------------------------------------------------------------------

    def test_test_env_starts_no_batch(self):
        path = self.inbox(entries(50))
        self.assertEqual(self.run_hook(HARNESS_LESSONS_CLAUDE=STUB_CLAUDE), self.count_line(path, 50) + "\n")
        self.assertFalse((self.cfg / "lessons-batch").exists())

    def test_batch_guard_env_starts_nothing(self):
        path = self.inbox(entries(50))
        out = self.run_hook(HARNESS_LESSONS_BATCH="1", RIGGER_LESSONS_BATCH="1", HARNESS_LESSONS_CLAUDE=STUB_CLAUDE)
        self.assertEqual(out, self.count_line(path, 50) + "\n")
        self.assertFalse((self.cfg / "lessons-batch").exists())

    def test_hook_script_starts_a_batch_and_prints_nothing_for_it(self):
        path = self.inbox(entries(50))
        out = self.run_hook(RIGGER_LESSONS_BATCH="1", HARNESS_LESSONS_CLAUDE=STUB_CLAUDE, **TODAY)
        self.assertEqual(out, self.count_line(path, 50) + "\n")
        state = self.cfg / "lessons-batch"
        logs = lambda: sorted((state / "logs").glob("2*.log"))  # noqa: E731
        ended = lambda: logs() and not (state / "lock").exists() and logs()[-1].read_text("utf-8").endswith("stopped\n")
        spawn_log = state / "logs" / "spawn.log"
        self.assertTrue(wait_for(ended, 60), "no batch ran; spawn log: %r" % lessons_batch._read(spawn_log))
        self.assertTrue((state / "stamp").is_file())
        pid = int(logs()[-1].stem.rsplit("-", 1)[1])
        self.assertTrue(wait_for(lambda: not alive(pid)), "batch left running")
        self.assertEqual(path.read_text(encoding="utf-8"), entries(50))

    def load_hook(self, name):
        spec = importlib.util.spec_from_file_location(name, SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hook_starts_batch(self):
        path = self.inbox(entries(6))
        env = dict(self.base_env(), CLAUDE_CONFIG_DIR=str(self.cfg), CLAUDE_PROJECT_DIR=str(self.tmp))
        module = self.load_hook("lessons_start_batch")
        calls = []

        def broken(*args):
            raise RuntimeError("synthetic failure")

        for start, wanted in ((lambda *args: calls.append(args), 1), (broken, 1)):
            with mock.patch.object(lessons_batch, "maybe_start", start):
                try:
                    out = module.build_output(env)
                except Exception as exc:  # a raised error fails the test as an assertion
                    self.fail("build_output raised %r" % exc)
            self.assertEqual(out, self.count_line(path, 6) + "\n")
        self.assertEqual(calls, [(env, path, entries(6).encode("utf-8"))])

    # -- notices ---------------------------------------------

    def queue(self, count):
        block = "## Run 20400615-000000-{0} · 2026-01-01 case {0}\n\n- entry: x\n\nOld:\n~~~\n## Run x\n~~~\n\n"
        path = self.cfg / "lessons-proposals.md"
        write_text(path, "".join(block.format(n) for n in range(count)))
        return path

    def test_notice_with_five_proposals(self):
        path = self.inbox(entries(1))
        queue = self.queue(5)
        wanted = self.count_line(path, 1) + "\n" + NOTICE.format(count=5, queue=queue) + "\n"
        self.assertEqual(self.run_hook(CLAUDE_PROJECT_DIR=str(self.tmp)), wanted)
        self.queue(7)
        self.assertIn(NOTICE.format(count=7, queue=queue), self.run_hook(cwd=self.tmp).splitlines())

    def test_no_notice_below_five(self):
        path = self.inbox(entries(1))
        self.queue(4)
        self.assertEqual(self.run_hook(), self.count_line(path, 1) + "\n")

    def test_notice_in_any_folder(self):
        path = self.inbox(entries(1))
        queue = self.queue(5)
        wanted = self.count_line(path, 1) + "\n" + NOTICE.format(count=5, queue=queue) + "\n"
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.assertEqual(self.run_hook(CLAUDE_PROJECT_DIR=str(elsewhere)), wanted)
        self.assertEqual(self.run_hook(cwd=elsewhere), wanted)

    def test_failure_notice_after_three_failed_runs(self):
        path = self.inbox(entries(1))
        logs = self.cfg / "lessons-batch" / "logs"
        for name in ("20400614-120000-77.log", "20400615-090000-5.log", "spawn.log"):
            write_text(logs / name, "x\n")
        failed = logs / "20400614-120000-77.log"  # the newest log is a run that found the lock taken
        write_text(self.cfg / "lessons-batch" / "failed-log", str(failed))
        for failures, wanted in (("3", [FAILURE.format(n=3, path=failed)]), ("12", [FAILURE.format(n=12, path=failed)]),
                                 ("2", []), ("0", []), ("x", []), ("", [])):
            with self.subTest(failures=failures):
                write_text(self.cfg / "lessons-batch" / "failures", failures)
                out = self.run_hook(CLAUDE_PROJECT_DIR=str(self.tmp))
                self.assertEqual(out.splitlines(), [self.count_line(path, 1)] + wanted)
        write_text(self.cfg / "lessons-batch" / "failures", "3")
        (self.cfg / "lessons-batch" / "failed-log").unlink()
        self.assertEqual(self.run_hook(CLAUDE_PROJECT_DIR=str(self.tmp)).splitlines(),
                         [self.count_line(path, 1), FAILURE.format(n=3, path=logs)])

    def test_notice_error_keeps_the_inbox_line(self):
        path = self.inbox(entries(6))
        env = dict(self.base_env(), CLAUDE_CONFIG_DIR=str(self.cfg))
        module = self.load_hook("lessons_start_notice_error")
        with mock.patch.dict(sys.modules, {"batch_apply": None}):  # `import batch_apply` raises ImportError
            try:
                out = module.build_output(env)
            except Exception as exc:  # a raised error fails the test as an assertion
                out = "raised %r" % exc
        self.assertEqual(out, self.count_line(path, 6) + "\n")

    def test_unexpected_error_prints_a_failure_line(self):
        spec = importlib.util.spec_from_file_location("lessons_start_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def broken(environ):
            raise RuntimeError("synthetic failure")

        module.build_output = broken
        captured = io.BytesIO()
        saved = sys.stdout
        wrapper = io.TextIOWrapper(captured, encoding="utf-8", newline="")
        sys.stdout = wrapper
        try:
            code = module.main()
        finally:
            sys.stdout = saved
            wrapper.detach()  # keeps captured open when the wrapper is collected
        lines = captured.getvalue().decode("utf-8").splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["rigger: lessons hook failed (RuntimeError: synthetic failure)"])

    # -- wiring -----------------------------------------------------------------------------

    def test_hooks_json_shape(self):
        with open(HOOKS_JSON, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(
            data["hooks"]["SessionStart"][0],
            {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 10}]},
        )

    def test_end_to_end_through_py_sh(self):
        sh = find_sh()
        self.assertIsNotNone(sh, "no POSIX sh found (Windows needs Git for Windows)")
        with open(HOOKS_JSON, encoding="utf-8") as handle:
            command = json.load(handle)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        root = PLUGIN.as_posix()
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
        command = command.replace("${CLAUDE_PLUGIN_ROOT}", root)
        env = self.base_env()
        path_parts = [str(Path(sys.executable).parent)]
        if os.name == "nt":
            path_parts.append(str(Path(sh).parent))
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
        env["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        env["CLAUDE_PLUGIN_ROOT"] = root
        env["CLAUDE_PLUGIN_DATA"] = (self.tmp / "data").as_posix()
        event = {
            "session_id": "00000000-0000-4000-8000-000000000000",
            "transcript_path": (self.tmp / "t.jsonl").as_posix(),
            "cwd": self.tmp.as_posix(),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }
        proc = subprocess.run(
            [sh, "-c", command],
            input=json.dumps(event).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.tmp),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        out = proc.stdout.decode("utf-8").replace("\r\n", "\n")
        path = self.cfg / "lessons-inbox.md"
        self.assertTrue(path.is_file())
        self.assertEqual(out.splitlines(), [self.count_line(path, 0)])
        self.assertFalse((self.home / ".claude").exists())


SKILL_NAME = "rigger-wp-traps"
HEADER = "Inbox lessons for skill rigger-wp-traps, not yet in its text:\n"
CAP = 8000


def scoped(day, scope="skill:rigger-wp-traps", text="case"):
    return "- 2026-01-%02d · %s · demo · %s · rule · signal_type: test-failure" % (day, scope, text)


class SkillLessonsTests(unittest.TestCase):
    """hooks/skill_lessons.py, run as py.sh runs it; the inbox is a fixture named by the env override."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-lessons-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inbox = self.tmp / "lessons-inbox.md"

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["HARNESS_LESSONS_INBOX"] = str(self.inbox)
        return env

    def run_stdin(self, stdin):
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-S", str(SKILL_SCRIPT)],
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env(), cwd=str(self.tmp), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout

    def load(self, name=SKILL_NAME):
        event = {"tool_name": "Skill", "hook_event_name": "PostToolUse", "tool_input": {"skill": name}}
        return self.run_stdin(json.dumps(event).encode("utf-8"))

    def write_inbox(self, lines, newline="\n"):
        write_text(self.inbox, "# Inbox\n\n## Entries\n" + "".join(line + newline for line in lines))

    def context(self, out):
        self.assertNotEqual(out, b"", "the hook printed nothing where a lesson matches")
        data = json.loads(out.decode("utf-8"))
        self.assertEqual(list(data), ["hookSpecificOutput"])
        output = data["hookSpecificOutput"]
        self.assertEqual(sorted(output), ["additionalContext", "hookEventName"])
        self.assertEqual(output["hookEventName"], "PostToolUse")
        return output["additionalContext"]

    def test_a_match_adds_exactly_the_entries_of_that_skill(self):
        bad_date, prose = scoped(6) + " · curated: soon", scoped(7) + " · why: curated: 2026-02-01"
        self.write_inbox([scoped(1), scoped(2, "global"), scoped(3, text="second"), scoped(4, "skill:other"), "- 2026-01-02",
                          scoped(5) + " · curated: 2026-02-01", bad_date, prose])
        self.assertEqual(self.context(self.load()), HEADER + "\n".join((scoped(1), scoped(3, text="second"), bad_date, prose)))

    def test_the_name_matches_with_and_without_a_plugin_prefix(self):
        for called, scope in (
            ("rigger-wp-traps", "skill:rigger-wp-traps"),
            ("rigger-wp:rigger-wp-traps", "skill:rigger-wp-traps"),
            ("rigger-wp-traps", "skill:rigger-wp:rigger-wp-traps"),
            ("rigger-wp:rigger-wp-traps", "skill:rigger-wp:rigger-wp-traps"),
        ):
            with self.subTest(called=called, scope=scope):
                self.write_inbox([scoped(1, scope)])
                self.assertEqual(self.context(self.load(called)), HEADER + scoped(1, scope))

    def test_no_match_prints_nothing(self):
        self.write_inbox([
            scoped(1, "skill:other"), scoped(2, "global"), scoped(3, "project:rigger-wp-traps"),
            scoped(4, "skill:rigger-wp"), scoped(5, "skill:rigger-wp-traps-extra"), scoped(6, "rigger-wp-traps"),
            scoped(7, "global", "see skill:rigger-wp-traps"), "  " + scoped(8), "note · skill:rigger-wp-traps · x",
            scoped(9).replace("- 2026-01-09", "- 26-01-09"),
        ])
        self.assertEqual(self.load(), b"")

    def test_a_crlf_inbox_adds_no_carriage_return(self):
        self.write_inbox([scoped(1), scoped(2)], newline="\r\n")
        out = self.load()
        self.assertEqual(self.context(out), HEADER + scoped(1) + "\n" + scoped(2))
        self.assertNotIn(b"\\r", out)
        self.assertNotIn(b"\r", out)

    def test_the_cap_counts_bytes_not_characters(self):
        lines = [scoped(1, text="αβγδεζηθ" * 40 + str(n)) for n in range(60)]
        self.write_inbox(lines)
        out = self.load()
        self.assertLess(len(out), CAP)
        self.assertIn("αβγδεζηθ".encode("utf-8"), out)

    def test_one_entry_past_the_cap_is_named_not_cut(self):
        self.write_inbox([scoped(1, text="x" * 20000)])
        out = self.load()
        self.assertLess(len(out), CAP)
        self.assertEqual(self.context(out), HEADER + "and 1 older in %s" % self.inbox)

    def test_a_missing_inbox_prints_nothing_and_is_not_created(self):
        self.assertEqual(self.load(), b"")
        self.assertFalse(self.inbox.exists())

    def test_an_inbox_that_is_not_utf8_still_matches(self):
        self.inbox.write_bytes(scoped(1).encode("utf-8").replace(b"case", b"caf\xe9") + b"\n")
        self.assertIn("caf", self.context(self.load()))

    def test_any_wrong_input_prints_nothing(self):
        self.write_inbox([scoped(1)])
        tool_inputs = (["a"], "text", 5, None, {}, {"skill": 5}, {"skill": ["rigger-wp-traps"]}, {"skill": None}, {"skill": ""})
        stdins = [b"", b"{", b"[]", b"5", b'"text"', b"null", b"{}", b"\xff\xfe"]
        stdins += [json.dumps({"tool_name": "Skill", "tool_input": item}).encode("utf-8") for item in tool_inputs]
        for stdin in stdins:
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_stdin(stdin), b"")

    def test_end_to_end_through_the_hooks_json_command(self):
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
        self.assertIn("PostToolUse", hooks)
        groups = hooks["PostToolUse"]
        self.assertEqual([group["matcher"] for group in groups], ["Skill"])
        self.assertEqual(len(groups[0]["hooks"]), 1)
        handler = groups[0]["hooks"][0]
        self.assertTrue(handler["command"].endswith(" || true"), handler["command"])
        self.assertEqual(handler["timeout"], 10)
        self.assertNotIn("if", handler)
        sh = find_sh()
        self.write_inbox([scoped(1)])
        env = self.env()
        env.update({"CLAUDE_PLUGIN_ROOT": PLUGIN.as_posix(), "CLAUDE_PLUGIN_DATA": (self.tmp / "data").as_posix(),
                    "PATH": os.pathsep.join((str(Path(sys.executable).parent), str(Path(sh).parent), os.environ["PATH"]))})
        event = {"tool_name": "Skill", "tool_input": {"skill": "rigger-wp:rigger-wp-traps"}}
        proc = subprocess.run([sh, "-c", handler["command"].replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN.as_posix())],
                              input=json.dumps(event).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=str(self.tmp), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.context(proc.stdout), HEADER + scoped(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
