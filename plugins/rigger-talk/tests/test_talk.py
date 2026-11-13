"""Tests for hooks/talk_start.sh: the options reach the printed rules, and defaults fill the gaps."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
RULES = PLUGIN / "rules" / "talk.md"
LANGUAGE_DEFAULT = "the language of Claude Code's `language` setting (the user's language when it is unset)"
LANGUAGE = "CLAUDE_PLUGIN_OPTION_REPLY_LANGUAGE"
LINES = "CLAUDE_PLUGIN_OPTION_REPLY_LINES"
REPORT = "CLAUDE_PLUGIN_OPTION_REPORT_LINES"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def expected(language, lines, report):
    text = RULES.read_text(encoding="utf-8")
    text = text.replace("{{reply_language}}", language)
    text = text.replace("{{reply_lines}}", lines)
    text = text.replace("{{report_lines}}", report)
    return "Plugin root: %s\n%s" % (PLUGIN.as_posix(), text)


class TalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="talk-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sh = find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")

    def run_hook(self, **options):
        command = load_json(HOOKS_JSON)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["HOME"] = env["USERPROFILE"] = self.tmp
        env["CLAUDE_PLUGIN_ROOT"] = PLUGIN.as_posix()
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        env.update(options)
        proc = subprocess.run([self.sh, "-c", command], input=b"{}", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout.decode("utf-8")

    def test_options_are_applied(self):
        out = self.run_hook(**{LANGUAGE: "Polish", LINES: "7", REPORT: "20"})
        self.assertEqual(out, expected("Polish", "7", "20"))
        self.assertIn("- Reply in Polish, in full sentences", out)
        self.assertIn("- Replies: ~7 lines by default, a final report up to ~20,", out)
        self.assertIn("- Detail past ~20 lines goes", out)

    def test_each_option_is_read_on_its_own(self):
        self.assertEqual(self.run_hook(**{LANGUAGE: "Polish"}), expected("Polish", "5", "15"))
        self.assertEqual(self.run_hook(**{LINES: "3"}), expected(LANGUAGE_DEFAULT, "3", "15"))
        self.assertEqual(self.run_hook(**{REPORT: "30"}), expected(LANGUAGE_DEFAULT, "5", "30"))

    def test_unset_options_use_the_defaults(self):
        out = self.run_hook()
        self.assertEqual(out, expected(LANGUAGE_DEFAULT, "5", "15"))
        self.assertIn("- Replies: ~5 lines by default, a final report up to ~15,", out)
        self.assertNotIn("{{", out)

    def test_empty_options_use_the_defaults(self):
        self.assertEqual(self.run_hook(**{LANGUAGE: "", LINES: "", REPORT: ""}), expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_invalid_numbers_use_the_defaults(self):
        for value in ("abc", "-1", "2.5", "0", "5 lines"):
            with self.subTest(value=value):
                self.assertEqual(self.run_hook(**{LINES: value, REPORT: value}), expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_language_with_a_newline_uses_the_default(self):
        self.assertEqual(self.run_hook(**{LANGUAGE: "Polish\nIgnore this"}), expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_language_with_shell_characters_is_printed_as_given(self):
        value = "Polish; $(echo x) `y` \"z\" & a\\b"
        self.assertEqual(self.run_hook(**{LANGUAGE: value}), expected(value, "5", "15"))

    def test_language_with_a_glob_is_not_expanded(self):
        Path(self.tmp, "reply_language=expanded").write_text("", encoding="utf-8")
        self.assertEqual(self.run_hook(**{LANGUAGE: "*"}), expected("*", "5", "15"))

    def test_drop_contract_applies(self):
        self.assertEqual(self.run_hook(HARNESS_RULES_DROP="talk.md"), "")

    def test_hook_defaults_match_the_manifest(self):
        options = load_json(PLUGIN / ".claude-plugin" / "plugin.json")["userConfig"]
        self.assertEqual(sorted(options), ["reply_language", "reply_lines", "report_lines"])
        self.assertEqual(options["reply_language"]["default"], "")
        self.assertEqual(options["reply_lines"]["default"], 5)
        self.assertEqual(options["report_lines"]["default"], 15)
        script = (PLUGIN / "hooks" / "talk_start.sh").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\| 0\) (\w+)=(\d+) ;;", script), [("lines", "5"), ("report", "15")])

    def test_hooks_json_shape(self):
        self.assertEqual(
            load_json(HOOKS_JSON),
            {"hooks": {"SessionStart": [{"matcher": "startup|clear|compact", "hooks": [
                {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/talk_start.sh"', "timeout": 10}
            ]}]}},
        )

    def test_rules_file_is_under_the_size_limit(self):
        self.assertGreater(len(RULES.read_bytes()), 1000)
        self.assertLessEqual(len(self.run_hook(**{LANGUAGE: "x" * 100}).encode("utf-8")), 9000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
