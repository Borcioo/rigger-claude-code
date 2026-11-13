"""Checks for env_check.py: every case rigger-panel's own test reads (tests/env-cases.ts) gives the panel's
reason, and the CLI and the PostToolUse hook mode run end to end as `python -I <script>`."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "env_check.py"
CASES = HERE.parents[3] / "rigger-panel" / "tests" / "env-cases.ts"
EXPORT = "export default "
BAD = '{"version":2,"environments":{}}'
BAD_REASON = 'wrong shape: "version" must be 1'
GOOD = '{"version":1,"environments":{"local":{"url":"http://demo.test"}}}'
# A JSON escape for a lone surrogate, spelled with chr() so no editor turns it into the character.
LONE_ESCAPE = chr(92) + "ud800"
LONE_FILE = '{"version":1,"environments":{"%s":5}}' % LONE_ESCAPE
LONE_REASON = 'wrong shape: environment "%s" must be an object' % chr(0xD800)
DEEP_VALID = '{"version":1,"environments":{},"y":%s}' % ("[" * 1100 + "]" * 1100)


def load_cases():
    text = CASES.read_text(encoding="utf-8")
    return json.loads(text[text.index(EXPORT) + len(EXPORT):])


def load_module():
    spec = importlib.util.spec_from_file_location("env_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args, stdin=b""):
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPT), *args], input=stdin,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


def hook_reason(path, reason):
    return '%s: the Environments pane will show "Environments unavailable: %s". Fix the file and write it again.' % (path, reason)


class SharedCaseTests(unittest.TestCase):
    # Installed, each plugin sits in its own cache folder with no rigger-panel beside it; in the
    # repo the folder is there, so a missing cases file inside it still fails.
    @unittest.skipUnless(CASES.parents[1].is_dir(), "rigger-panel is not beside rigger-core (installed layout)")
    def test_every_shared_case_gives_the_panel_reason(self):
        cases = load_cases()
        self.assertGreater(len(cases), 90)
        check = load_module().check
        for case in cases:
            with self.subTest(case=case["name"]):
                reason = check(case["text"])
                if reason is not None and not case["exact"]:
                    reason = reason[:len(case["reason"])]
                self.assertEqual(reason, case["reason"])


class LimitTests(unittest.TestCase):
    """Python limits JavaScript does not have must not change the verdict."""

    def test_an_integer_past_4300_digits_is_a_number(self):
        self.assertIsNone(load_module().check('{"version":1,"environments":{},"n":%s}' % ("1" * 4301)))

    def test_a_numeric_key_past_4300_digits_is_not_an_array_index(self):
        key = "1" * 5000
        self.assertEqual(load_module().check('{"version":1,"environments":{"%s":{"token":1}}}' % key),
                         "file refused, it looks like a secret: field environments.%s.token. Keep passwords, "
                         "tokens and keys out of this file" % key)


class UnknownKeyTests(unittest.TestCase):
    """A top-level key the panel does not know is ignored, whatever its shape."""

    def test_unknown_key_is_ignored(self):
        check = load_module().check
        for name, value in (("a string", '"x"'), ("a number", "5"), ("a list", '["x"]'),
                            ("an object", '{"a": "x"}'), ("null", "null"), ("true", "true")):
            with self.subTest(shape=name):
                self.assertIsNone(check('{"version":1,"environments":{},"extra":%s}' % value))

    def test_unknown_key_does_not_hide_the_real_reason(self):
        self.assertEqual(load_module().check('{"version":1,"extra":5}'), 'wrong shape: no "environments" object')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="env check "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".claude").mkdir()
        self.env_file = self.tmp / ".claude" / "environments.json"

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


class CliTests(Base):
    def test_invalid_file_exits_1_with_the_reason_line(self):
        self.write(self.env_file, BAD)
        code, out, err = run([str(self.env_file)])
        self.assertEqual((code, out.decode("utf-8").replace("\r\n", "\n"), err), (1, BAD_REASON + "\n", b""))

    def test_valid_file_exits_0_silently(self):
        self.write(self.env_file, GOOD)
        self.assertEqual(run([str(self.env_file)]), (0, b"", b""))

    def test_a_lone_surrogate_in_the_reason_is_printed_escaped(self):
        self.write(self.env_file, LONE_FILE)
        code, out, err = run([str(self.env_file)])
        self.assertEqual((code, out.replace(b"\r\n", b"\n"), err),
                         (1, LONE_REASON.encode("utf-8", "backslashreplace") + b"\n", b""))

    def test_nesting_too_deep_for_python_exits_2(self):
        self.write(self.env_file, DEEP_VALID)
        code, out, err = run([str(self.env_file)])
        self.assertEqual((code, out), (2, b""))
        self.assertTrue(err.decode("utf-8").startswith("env_check: cannot check"), err)

    def test_missing_file_exits_1_with_a_reason(self):
        code, out, err = run([str(self.tmp / "missing.json")])
        self.assertEqual((code, err), (1, b""))
        self.assertTrue(out.decode("utf-8").startswith("cannot read the file ("), out)


class HookTests(Base):
    def event(self, file_path, tool="Write", **extra):
        data = {"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(self.tmp),
                "tool_input": {"file_path": file_path}}
        data.update(extra)
        return json.dumps(data).encode("utf-8")

    def test_invalid_file_returns_the_reason_to_the_model(self):
        self.write(self.env_file, BAD)
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                code, out, err = run(["--hook"], self.event(str(self.env_file), tool))
                self.assertEqual((code, err), (0, b""))
                self.assertTrue(out.endswith(b"}\n") and b"\r" not in out, out)
                self.assertEqual(json.loads(out), {"decision": "block", "reason": hook_reason(self.env_file, BAD_REASON)})

    def test_relative_path_is_read_from_cwd(self):
        self.write(self.env_file, BAD)
        relative = os.path.join(".claude", "environments.json")
        code, out, _ = run(["--hook"], self.event(relative))
        self.assertEqual(json.loads(out)["reason"], hook_reason(relative, BAD_REASON))

    def test_a_lone_surrogate_in_the_reason_still_reaches_the_model(self):
        self.write(self.env_file, LONE_FILE)
        code, out, err = run(["--hook"], self.event(str(self.env_file)))
        self.assertEqual((code, err), (0, b""))
        self.assertEqual(json.loads(out), {"decision": "block", "reason": hook_reason(self.env_file, LONE_REASON)})

    def test_nesting_too_deep_for_python_gives_no_output(self):
        self.write(self.env_file, DEEP_VALID)
        self.assertEqual(run(["--hook"], self.event(str(self.env_file))), (0, b"", b""))

    def test_valid_file_gives_no_output(self):
        self.write(self.env_file, GOOD)
        self.assertEqual(run(["--hook"], self.event(str(self.env_file))), (0, b"", b""))

    def test_another_environments_file_gives_no_output(self):
        for path in (self.tmp / "config" / "environments.json", self.tmp / ".claude" / "environments.json.bak"):
            with self.subTest(path=path.name):
                self.write(path, BAD)
                self.assertEqual(run(["--hook"], self.event(str(path))), (0, b"", b""))

    def test_a_file_gone_after_the_write_gives_no_output(self):
        self.assertEqual(run(["--hook"], self.event(str(self.env_file))), (0, b"", b""))

    def test_input_of_a_wrong_shape_gives_no_output(self):
        self.write(self.env_file, BAD)
        for name, stdin in (
            ("not JSON", b"{"),
            ("a list", b"[]"),
            ("tool_input a list", json.dumps({"cwd": str(self.tmp), "tool_input": []}).encode("utf-8")),
            ("file_path a number", json.dumps({"cwd": str(self.tmp), "tool_input": {"file_path": 5}}).encode("utf-8")),
            ("cwd a number", self.event(os.path.join(".claude", "environments.json"), cwd=5)),
        ):
            with self.subTest(input=name):
                self.assertEqual(run(["--hook"], stdin), (0, b"", b""))


if __name__ == "__main__":
    unittest.main()
