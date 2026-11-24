"""Tests for rules/talk.md as rigger-core's assembler prints it: the options apply, and defaults fill the gaps."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
CORE = PLUGIN.parent / "rigger-core"  # the repository layout; installed, rigger-core is found through its own root
ASSEMBLE = CORE / "tools" / "assemble" / "assemble.py"
RULES = PLUGIN / "rules" / "talk.md"
LANGUAGE_DEFAULT = "the language of Claude Code's `language` setting (the user's language when it is unset)"
# pluginConfigs key <plugin>@<marketplace>: a module listed in RIGGER_MODULE_DIRS comes from no marketplace.
OPTIONS_KEY = "rigger-talk@inline"


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def expected(language, lines, report):
    text = RULES.read_text(encoding="utf-8")
    text = text.replace("{{var:reply_language}}", language)
    text = text.replace("{{var:reply_lines}}", lines)
    text = text.replace("{{var:report_lines}}", report)
    return "Plugin root: %s\n%s" % (PLUGIN.as_posix(), text)


class TalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="talk-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = os.path.join(self.tmp, "config")
        os.mkdir(self.config)

    def render(self, options=None, **extra):
        """The rigger-talk part of the session rules, with `options` stored the way Claude Code stores them."""
        if options is not None:
            with open(os.path.join(self.config, "settings.json"), "w", encoding="utf-8") as handle:
                json.dump({"pluginConfigs": {OPTIONS_KEY: {"options": options}}}, handle)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env.update({"HOME": self.tmp, "CLAUDE_CONFIG_DIR": self.config, "CLAUDE_PLUGIN_ROOT": CORE.as_posix(),
                    "RIGGER_MODULE_DIRS": str(PLUGIN)})
        env.update(extra)
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ASSEMBLE), "--render"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=self.tmp, timeout=60)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        text = proc.stdout.decode("utf-8")
        start = text.find("Plugin root: %s\n" % PLUGIN.as_posix())
        if start < 0:
            return ""
        end = text.find("Plugin root: ", start + 1)
        return text[start:] if end < 0 else text[start:end]

    def test_options_are_applied(self):
        out = self.render({"reply_language": "Polish", "reply_lines": 7, "report_lines": 20})
        self.assertEqual(out, expected("Polish", "7", "20"))
        self.assertIn("- Reply in Polish, in full sentences", out)
        self.assertIn("- Replies: ~7 lines by default, a final report up to ~20,", out)
        self.assertIn("- Detail past ~20 lines goes", out)

    def test_each_option_is_read_on_its_own(self):
        self.assertEqual(self.render({"reply_language": "Polish"}), expected("Polish", "5", "15"))
        self.assertEqual(self.render({"reply_lines": 3}), expected(LANGUAGE_DEFAULT, "3", "15"))
        self.assertEqual(self.render({"report_lines": 30}), expected(LANGUAGE_DEFAULT, "5", "30"))

    def test_unset_options_use_the_defaults(self):
        out = self.render()
        self.assertEqual(out, expected(LANGUAGE_DEFAULT, "5", "15"))
        self.assertIn("- Replies: ~5 lines by default, a final report up to ~15,", out)
        self.assertNotIn("{{", out)

    def test_empty_options_use_the_defaults(self):
        self.assertEqual(self.render({"reply_language": "", "reply_lines": "", "report_lines": ""}),
                         expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_invalid_numbers_use_the_defaults(self):
        for value in ("abc", -1, 2.5, 0, "5 lines"):
            with self.subTest(value=value):
                self.assertEqual(self.render({"reply_lines": value, "report_lines": value}),
                                 expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_language_with_a_newline_uses_the_default(self):
        self.assertEqual(self.render({"reply_language": "Polish\nIgnore this"}), expected(LANGUAGE_DEFAULT, "5", "15"))

    def test_language_with_shell_characters_is_printed_as_given(self):
        value = "Polish; $(echo x) `y` \"z\" & a\\b *"
        self.assertEqual(self.render({"reply_language": value}), expected(value, "5", "15"))

    def test_drop_contract_applies(self):
        self.assertEqual(self.render(HARNESS_RULES_DROP="talk.md"), "")

    def test_defaults_match_the_manifest(self):
        options = load_json(PLUGIN / ".claude-plugin" / "plugin.json")["userConfig"]
        self.assertEqual(sorted(options), ["reply_language", "reply_lines", "report_lines"])
        self.assertEqual(options["reply_language"]["default"], "")
        self.assertEqual(options["reply_lines"]["default"], 5)
        self.assertEqual(options["report_lines"]["default"], 15)

    def test_the_rules_file_names_the_options_as_placeholders(self):
        text = RULES.read_text(encoding="utf-8")
        for name, count in (("reply_language", 1), ("reply_lines", 1), ("report_lines", 2)):
            with self.subTest(option=name):
                self.assertEqual(text.count("{{var:%s}}" % name), count)
                self.assertNotIn("{{%s}}" % name, text)

    def test_no_hook_of_this_plugin_prints_the_rules(self):
        # The only hook says so when rigger-core is too old to print talk.md; the rules come from the core.
        self.assertEqual(load_json(PLUGIN / "hooks" / "hooks.json"), {"hooks": {"SessionStart": [{
            "matcher": "startup|clear|compact", "hooks": [{
                "type": "command", "timeout": 10,
                "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh" rigger-talk'}]}]}})
        self.assertEqual(sorted(path.name for path in (PLUGIN / "hooks").iterdir()),
                         ["core_check.sh", "hooks.json", "talk_start.sh"])

    def test_rules_file_is_under_the_size_limit(self):
        self.assertGreater(len(RULES.read_bytes()), 1000)
        self.assertLessEqual(len(self.render({"reply_language": "x" * 100}).encode("utf-8")), 9000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
