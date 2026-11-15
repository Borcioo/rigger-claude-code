"""Checks for coord_state.py: every transition row is allowed and every other move refused (generated from
TRANSITIONS), required arguments, wrong file shapes, the atomic write, `show`, and the CLI run by path as
`python -I <script>`."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "coord_state.py"
NOW = "2026-01-02T03:04:05Z"
OK, REFUSED = "coord-state: ok ", "coord-state: refused: "
ALL = ("planned", "started", "built", "reviewed", "merged", "blocked", "dropped")
ARGS = {"head": "abc1234", "branch": "feature/x", "session": "local_1",
        "evidence": 'gate: PASS 12; user said "Tak, scalaj"'}
# The plan's transition table, row by row: (from, to, required arguments).
PLAN_ROWS = {("planned", "started", ("session|branch",)), ("started", "built", ("head", "evidence")),
             ("built", "reviewed", ("evidence",)), ("reviewed", "merged", ("head", "evidence")),
             ("built", "started", ("evidence",)), ("reviewed", "started", ("evidence",)),
             ("planned", "blocked", ("evidence",)), ("started", "blocked", ("evidence",)),
             ("built", "blocked", ("evidence",)), ("reviewed", "blocked", ("evidence",)),
             ("blocked", "started", ("evidence",)), ("blocked", "dropped", ("evidence",)),
             ("planned", "dropped", ("evidence",)), ("started", "dropped", ("evidence",))}
# „Tak, scalaj” spelled with chr() so no editor or shell turns the quotes into ASCII ones.
CURLY_OK = "user: " + chr(0x201E) + "Tak, scalaj" + chr(0x201D)


def load_module():
    spec = importlib.util.spec_from_file_location("coord_state", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = load_module()


def rows():
    """(from, to, needs) for every move the table allows; the (new) row is reached through `add` only."""
    return [(src, dst, needs) for src, targets in cs.TRANSITIONS.items() if src is not None
            for dst, needs in targets.items()]


def args_for(needs):
    argv = []
    for name in needs:
        first = name.split("|")[0]
        argv += ["--" + first, ARGS[first]]
    if "evidence" not in needs:
        argv += ["--evidence", ARGS["evidence"]]
    return argv


def run_cli(config, *argv):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=config, CLAUDE_CODE_SESSION_ID="s1")
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *argv], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)


class Case(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp(prefix="coord-state-")
        self.addCleanup(shutil.rmtree, self.config, True)
        self.state_dir = os.path.join(self.config, "state")
        self.path = os.path.join(self.state_dir, "s1.parts.json")
        self.environ = {"CLAUDE_CONFIG_DIR": self.config, "CLAUDE_CODE_SESSION_ID": "s1"}
        patcher = mock.patch.object(cs, "utcnow", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv, environ=None):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cs.main(list(argv), self.environ if environ is None else environ)
        return code, buffer.getvalue()

    def ok(self, *argv):
        code, out = self.run_main(*argv)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith(OK), out)
        return out

    def write_parts(self, updated=NOW, **states):
        os.makedirs(self.state_dir, exist_ok=True)
        parts = {name: {"state": state, "branch": None, "session": None, "head": None, "updated": updated,
                        "waits_on": [], "history": []} for name, state in states.items()}
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"version": 1, "parts": parts}, handle)

    def raw(self):
        with open(self.path, "rb") as handle:
            return handle.read()

    def part(self, name):
        return json.loads(self.raw().decode("utf-8"))["parts"][name]


class Transitions(Case):
    def test_every_allowed_move_succeeds(self):
        self.assertEqual(set(rows()), PLAN_ROWS)
        self.assertEqual(cs.TRANSITIONS[None], {"planned": ()})
        for src, dst, needs in rows():
            with self.subTest(src=src, dst=dst):
                self.write_parts(updated="2000-01-01T00:00:00Z", p=src)
                code, out = self.run_main("move", "p", dst, *args_for(needs))
                self.assertEqual(code, 0, out)
                self.assertEqual(out, OK + "p %s\n" % dst)
                entry = self.part("p")
                self.assertEqual(entry["state"], dst)
                self.assertEqual(entry["updated"], NOW)
                passed = {name.split("|")[0] for name in needs} - {"evidence"}
                for field in ("head", "branch", "session"):
                    self.assertEqual(entry[field], ARGS[field] if field in passed else None, field)
                self.assertEqual(entry["history"][-1],
                                 {"from": src, "to": dst, "at": NOW, "evidence": ARGS["evidence"]})

    def test_every_move_not_in_the_table_is_refused(self):
        allowed = {(src, dst) for src, dst, _ in rows()}
        illegal = [(src, dst) for src in ALL for dst in ALL if (src, dst) not in allowed]
        self.assertEqual(len(illegal), len(ALL) ** 2 - len(allowed))
        for src, dst in illegal:
            with self.subTest(src=src, dst=dst):
                self.write_parts(p=src)
                before = self.raw()
                code, out = self.run_main("move", "p", dst, *args_for(("head", "branch", "session")))
                self.assertEqual(code, 2)
                self.assertEqual(out, REFUSED + "not allowed: %s -> %s\n" % (src, dst))
                self.assertEqual(self.raw(), before)

    def test_each_required_argument_missing_is_refused(self):
        for src, dst, needs in rows():
            for name in needs:
                with self.subTest(src=src, dst=dst, missing=name):
                    self.write_parts(p=src)
                    before = self.raw()
                    rest = [other for other in needs if other != name]
                    argv = []
                    for other in rest:
                        argv += ["--" + other.split("|")[0], ARGS[other.split("|")[0]]]
                    code, out = self.run_main("move", "p", dst, *argv)
                    self.assertEqual(code, 2)
                    self.assertEqual(out, REFUSED + "missing --%s\n" % name.replace("|", " or --"))
                    self.assertEqual(self.raw(), before)

    def test_started_needs_session_or_branch(self):
        self.write_parts(p="planned")
        self.assertEqual(self.run_main("move", "p", "started", "--evidence", "go"),
                         (2, REFUSED + "missing --session or --branch\n"))
        self.assertEqual(self.ok("move", "p", "started", "--branch", "feature/p"), OK + "p started\n")
        self.assertEqual(self.part("p")["branch"], "feature/p")

    def test_start_waits_for_waits_on_parts(self):
        self.ok("init")
        self.ok("add", "a")
        self.ok("add", "b", "--waits-on", "a")
        self.assertEqual(self.part("b")["waits_on"], ["a"])
        self.assertEqual(self.run_main("move", "b", "started", "--session", "x"),
                         (2, REFUSED + "b waits on a (planned)\n"))
        self.ok("move", "b", "blocked", "--evidence", "a first")
        self.assertEqual(self.run_main("move", "b", "started", "--evidence", "retry"),
                         (2, REFUSED + "b waits on a (planned)\n"))
        self.ok("move", "a", "started", "--session", "x")
        self.ok("move", "a", "built", "--head", "abc1234", "--evidence", "gate PASS")
        self.ok("move", "a", "reviewed", "--evidence", "approved")
        self.ok("move", "a", "merged", "--head", "def5678", "--evidence", ARGS["evidence"])
        self.assertEqual(self.ok("move", "b", "started", "--evidence", "a merged"), OK + "b started\n")

    def test_merged_needs_a_quoted_ok(self):
        self.write_parts(p="reviewed")
        before = self.raw()
        for evidence in ("gate ok", 'only "" empty', 'one " quote'):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.run_main("move", "p", "merged", "--head", "abc1234", "--evidence", evidence),
                                 (2, REFUSED + "merged needs the user's OK quoted in --evidence\n"))
                self.assertEqual(self.raw(), before)


class Commands(Case):
    def test_init_creates_state_dir_and_refuses_a_second_init(self):
        self.assertEqual(self.run_main("init"), (0, OK + "init\n"))
        self.assertEqual(json.loads(self.raw()), {"version": 1, "parts": {}})
        before = self.raw()
        self.assertEqual(self.run_main("init"), (2, REFUSED + "file exists\n"))
        self.assertEqual(self.raw(), before)

    def test_add_writes_a_planned_part_with_its_history_row(self):
        self.ok("init")
        self.assertEqual(self.ok("add", "p"), OK + "p planned\n")
        self.assertEqual(self.part("p"), {"state": "planned", "branch": None, "session": None, "head": None,
                                          "updated": NOW, "waits_on": [],
                                          "history": [{"from": None, "to": "planned", "at": NOW, "evidence": None}]})

    def test_add_with_unknown_waits_on_is_refused(self):
        self.ok("init")
        self.ok("add", "a")
        before = self.raw()
        self.assertEqual(self.run_main("add", "b", "--waits-on", "a,z"), (2, REFUSED + "unknown part z\n"))
        self.assertEqual(self.raw(), before)

    def test_duplicate_add_is_refused(self):
        self.ok("init")
        self.ok("add", "p")
        self.assertEqual(self.run_main("add", "p"), (2, REFUSED + "part p exists\n"))

    def test_unknown_part_is_refused(self):
        self.ok("init")
        self.assertEqual(self.run_main("move", "q", "started", "--session", "x"), (2, REFUSED + "unknown part q\n"))
        self.assertEqual(self.run_main("check", "q", "planned"), (2, REFUSED + "unknown part q\n"))

    def test_commands_before_init_are_refused(self):
        self.assertEqual(self.run_main("show"), (2, REFUSED + "no state file; run init\n"))
        self.assertFalse(os.path.exists(self.state_dir))

    def test_bad_arguments_are_refused(self):
        self.ok("init")
        self.ok("add", "p")
        for argv, reason in ((["frob"], "unknown command frob"), ([], "no command"), (["a\nb"], "unknown command (bad)"),
                             (["add", "q", "--a\nb", "x"], "unknown option --(bad)"),
                             (["add"], "wrong arguments for add"), (["add", "p", "--head", "abc1234"], "unknown option --head"),
                             (["add", "p", "--waits-on"], "missing value for --waits-on"),
                             (["move", "p", "started", "--session", "a", "--session", "b"], "repeated --session"),
                             (["move", "p", "nowhere", "--evidence", "x"], "unknown state")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_main(*argv), (2, REFUSED + reason + "\n"))

    def test_wrong_shapes_are_refused_and_left_byte_identical(self):
        cases = ((b"[]", "wrong shape: expected an object"),
                 (b"7", "wrong shape: expected an object"),
                 (b'{"version": 1}', "wrong shape: parts must be an object"),
                 (b'{"version": 2, "parts": {}}', "version 2 is newer than 1"),
                 (b'{"version": true, "parts": {}}', "wrong shape: version must be 1"),
                 (b'{"version": 1, "parts": []}', "wrong shape: parts must be an object"),
                 (b'{"version": 1, "parts": {"p": 3}}', "wrong shape: part p must be an object"),
                 (b'{"version": 1, "parts": {"a b": {}}}', "wrong shape: bad part name in file"),
                 (b'{"version": 1, "parts": {"p": {"state": "x", "waits_on": [], "history": []}}}',
                  "wrong shape: part p has an unknown state"),
                 (b'{"version": 1, "parts": {"p": {"state": "built", "waits_on": 5, "history": []}}}',
                  "wrong shape: part p waits_on must be a list of part names"),
                 (b'{"version": 1, "parts": {"p": {"state": "built", "waits_on": [], "history": {}}}}',
                  "wrong shape: part p history must be a list"),
                 (b'{"version": 1, "par', "unreadable file"),
                 (b'\xff{}', "unreadable file"))
        os.makedirs(self.state_dir)
        for content, reason in cases:
            with self.subTest(content=content):
                with open(self.path, "wb") as handle:
                    handle.write(content)
                code, out = self.run_main("move", "p", "blocked", "--evidence", "x")
                self.assertEqual(code, 2)
                self.assertTrue(out.startswith(REFUSED + reason), out)
                self.assertEqual(out.count("\n"), 1)
                self.assertEqual(self.raw(), content)

    def test_a_failed_replace_leaves_the_old_file(self):
        self.ok("init")
        self.ok("add", "p")
        before = self.raw()
        with mock.patch.object(cs.os, "replace", side_effect=OSError("disk full")):
            code, out = self.run_main("move", "p", "blocked", "--evidence", "x")
        self.assertEqual((code, out), (2, REFUSED + "write failed\n"))
        self.assertEqual(self.raw(), before)
        self.assertEqual(sorted(os.listdir(self.state_dir)), ["s1.parts.json"])

    def test_show_prints_the_table_and_counts(self):
        self.write_parts(b="planned", a="merged")
        data = json.loads(self.raw())
        data["parts"]["a"]["head"] = "abc1234"
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.assertEqual(self.run_main("show"), (0,
                         "| part | state | session | branch | head | updated |\n|---|---|---|---|---|---|\n"
                         "| a | merged | - | - | abc1234 | 2026-01-02T03:04:05Z |\n"
                         "| b | planned | - | - | - | 2026-01-02T03:04:05Z |\n"
                         "# merged 1\n# planned 1\ncoord-state: ok show\n"))

    def test_show_ends_with_one_ok_line(self):
        self.ok("init")
        code, out = self.run_main("show")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "coord-state: ok show")
        self.assertEqual(out.count("coord-state: ok"), 1)

    def test_show_puts_session_and_branch_in_their_columns(self):
        self.ok("init")
        self.ok("add", "p")
        self.ok("move", "p", "started", "--session", "sess-9", "--branch", "feature/x")
        rows = [line for line in self.run_main("show")[1].splitlines() if line.startswith("| p ")]
        self.assertEqual(len(rows), 1)
        cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
        self.assertEqual(cells[:4], ["p", "started", "sess-9", "feature/x"])

    def test_show_keeps_a_pipe_in_a_value_inside_its_cell(self):
        self.ok("init")
        self.ok("add", "p")
        self.ok("move", "p", "started", "--session", "a|b", "--branch", "c|d")
        row = [line for line in self.run_main("show")[1].splitlines() if line.startswith("| p ")][0]
        self.assertEqual(len(re.split(r"(?<!\\)\|", row.strip())) - 2, 6)
        self.assertIn("a\\|b", row)

    def test_check_on_another_state_is_refused(self):
        self.write_parts(p="started")
        self.assertEqual(self.run_main("check", "p", "built"), (2, REFUSED + "p is started, not built\n"))
        self.assertEqual(self.run_main("check", "p", "a\nb"), (2, REFUSED + "p is started, not (unknown state)\n"))

    def test_bad_session_id_is_refused(self):
        for session in ("../s1", "a/b", ".x"):
            with self.subTest(session=session):
                environ = dict(self.environ, CLAUDE_CODE_SESSION_ID=session)
                self.assertEqual(self.run_main("init", environ=environ), (2, REFUSED + "bad session id\n"))
        self.assertFalse(os.path.exists(self.state_dir))


class ReviewFocus(Case):
    def test_bad_part_names_are_refused(self):
        self.ok("init")
        before = self.raw()
        for name in ("a|b", "a b", "a\nb", "", "-x", "x" * 65):
            with self.subTest(name=name):
                self.assertEqual(self.run_main("add", name), (2, REFUSED + "bad part name\n"))
                self.assertEqual(self.run_main("add", "ok", "--waits-on", name), (2, REFUSED + "bad part name\n"))
                self.assertEqual(self.run_main("move", name, "blocked", "--evidence", "x"),
                                 (2, REFUSED + "bad part name\n"))
                self.assertEqual(self.raw(), before)
        self.assertEqual(self.ok("add", "x" * 64), OK + "%s planned\n" % ("x" * 64))

    def test_head_must_be_a_sha(self):
        self.write_parts(p="started")
        before = self.raw()
        for head in ("HEAD", "", "main", "abc12", "g" * 7, "ABC1234", "a" * 41):
            with self.subTest(head=head):
                self.assertEqual(self.run_main("move", "p", "built", "--head", head, "--evidence", "gate PASS"),
                                 (2, REFUSED + "bad --head\n"))
                self.assertEqual(self.raw(), before)
        self.ok("move", "p", "built", "--head", "a" * 40, "--evidence", "gate PASS")

    def test_repeat_move_is_refused_and_check_confirms(self):
        self.write_parts(p="started")
        self.ok("move", "p", "built", "--head", "abc1234", "--evidence", "gate PASS")
        before = self.raw()
        self.assertEqual(self.run_main("move", "p", "built", "--head", "abc1234", "--evidence", "gate PASS"),
                         (2, REFUSED + "not allowed: built -> built\n"))
        self.assertEqual(self.raw(), before)
        self.assertEqual(self.run_main("check", "p", "built"), (0, OK + "p built\n"))

    def test_merged_evidence_accepts_curly_quotes_and_non_ascii(self):
        self.write_parts(p="reviewed")
        self.assertEqual(self.ok("move", "p", "merged", "--head", "abc1234", "--evidence", CURLY_OK), OK + "p merged\n")
        self.assertEqual(self.part("p")["history"][-1]["evidence"], CURLY_OK)
        self.write_parts(p="reviewed")
        proc = run_cli(self.config, "move", "p", "merged", "--head", "abc1234", "--evidence", CURLY_OK)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"coord-state: ok p merged\n", b""))
        self.assertIn(CURLY_OK.encode("utf-8"), self.raw())
        self.assertEqual(self.part("p")["history"][-1]["evidence"], CURLY_OK)

    def test_check_refuses_a_bad_part_name(self):
        self.ok("init")
        self.assertEqual(self.run_main("check", "a\nb", "planned"), (2, REFUSED + "bad part name\n"))

    def test_merged_evidence_accepts_the_english_curly_quote_form(self):
        self.write_parts(p="reviewed")
        evidence = "user: " + chr(0x201C) + "Tak, scalaj" + chr(0x201D)
        self.assertEqual(self.ok("move", "p", "merged", "--head", "abc1234", "--evidence", evidence), OK + "p merged\n")

    def test_waits_on_items_of_the_wrong_shape_are_refused(self):
        os.makedirs(self.state_dir)
        for waits in ("[5]", '["a b"]'):
            with self.subTest(waits=waits):
                content = ('{"version": 1, "parts": {"p": {"state": "built", "waits_on": %s, "history": []}}}'
                           % waits).encode("utf-8")
                with open(self.path, "wb") as handle:
                    handle.write(content)
                self.assertEqual(self.run_main("move", "p", "blocked", "--evidence", "x"),
                                 (2, REFUSED + "wrong shape: part p waits_on must be a list of part names\n"))
                self.assertEqual(self.raw(), content)

    def test_a_waits_on_part_missing_from_the_file_refuses_the_start(self):
        self.write_parts(p="planned")
        data = json.loads(self.raw())
        data["parts"]["p"]["waits_on"] = ["gone"]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        before = self.raw()
        self.assertEqual(self.run_main("move", "p", "started", "--session", "x"),
                         (2, REFUSED + "p waits on gone (missing)\n"))
        self.assertEqual(self.raw(), before)

    def test_branch_and_session_must_be_non_empty_without_control_characters(self):
        self.write_parts(p="planned")
        before = self.raw()
        for option in ("branch", "session"):
            for value in ("", "a\nb", "a\tb", "a\rb"):
                with self.subTest(option=option, value=value):
                    other = ("--session", "ok") if option == "branch" else ("--branch", "ok")
                    self.assertEqual(self.run_main("move", "p", "started", "--" + option, value, *other),
                                     (2, REFUSED + "bad --%s\n" % option))
                    self.assertEqual(self.raw(), before)

    def test_no_session_id_is_refused_and_writes_nothing(self):
        environ = {"CLAUDE_CONFIG_DIR": self.config}
        for argv in (["init"], ["add", "p"], ["show"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_main(*argv, environ=environ), (2, REFUSED + "no session id\n"))
        self.assertEqual(os.listdir(self.config), [])


class Cli(Case):
    def cli(self, *argv, config=None):
        return run_cli(config or self.config, *argv)

    def test_cli_ok_and_refusal_lines_exit_codes_and_silent_stderr(self):
        proc = self.cli("init")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"coord-state: ok init\n", b""))
        self.cli("add", "p")
        proc = self.cli("move", "p", "merged", "--evidence", "x")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr),
                         (2, b"coord-state: refused: not allowed: planned -> merged\n", b""))

    def test_cli_unexpected_error_prints_one_refusal_line(self):
        blocker = os.path.join(self.config, "a-file")
        with open(blocker, "w") as handle:
            handle.write("x")
        proc = self.cli("init", config=blocker)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, b"")
        self.assertRegex(proc.stdout.decode("utf-8"), r"\Acoord-state: refused: internal error: \w+\n\Z")
        self.assertNotIn(b"Traceback", proc.stdout)


if __name__ == "__main__":
    unittest.main()
