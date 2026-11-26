"""The platform module (D83): its manifest, its Windows-only rule and triggers, its core check and its skills' words.

python -I test_platform_wiring.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

MODULE = Path(__file__).resolve().parent.parent
PLUGINS = MODULE.parent
CORE = PLUGINS / "rigger-core"
sys.path.insert(0, str(CORE / "tools" / "assemble"))

import assemble  # noqa: E402

SKILLS = ("windows-dev-env", "php-herd-tooling", "node-versions")
HERD_NEW = "${CLAUDE_CONFIG_DIR:-~/.claude}/rigger-platform/herd-docs/"
HERD_OLD = "${CLAUDE_CONFIG_DIR:-~/.claude}/rigger-windows/herd-docs/"


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def flat(text):
    return " ".join(text.split())


class Assembled:
    """The core and this module assembled in-process for one system, with an empty config dir."""

    def __init__(self, os_name):
        tmp = Path(tempfile.mkdtemp(prefix="platform-wiring-"))
        saved = assemble._this_os
        try:
            (tmp / "config").mkdir()
            (tmp / "project").mkdir()
            env = {"CLAUDE_CONFIG_DIR": str(tmp / "config"),
                   "RIGGER_MODULE_DIRS": os.pathsep.join(str(path) for path in (CORE, MODULE))}
            inputs = assemble.Inputs(config_dir=tmp / "config", project_dir=tmp / "project", environ=env,
                                     self_root=CORE)
            assemble._this_os = lambda: os_name
            self.main = assemble.assemble(inputs, "main")
        finally:
            assemble._this_os = saved
            shutil.rmtree(str(tmp), True)

    def modules(self):
        return {section["module"] for section in self.main.sections}

    def whole(self):
        return assemble.whole_text(self.main).decode("utf-8")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.data = read_json(MODULE / "rigger-module.json")

    def test_the_three_triggers_use_the_module_name_and_load_its_own_skills(self):
        rows = {row["id"]: row for row in self.data["triggers"]}
        self.assertEqual(sorted(rows), sorted("rigger-platform." + skill for skill in SKILLS))
        for skill in SKILLS:
            with self.subTest(skill=skill):
                row = rows["rigger-platform." + skill]
                self.assertEqual(row["load"], ["rigger-platform:" + skill])
                self.assertEqual(row["section"], "skills.skill-triggers")
                self.assertEqual(row.get("requires"), ["os:windows"])
                self.assertTrue((MODULE / "skills" / skill / "SKILL.md").is_file())

    def test_the_shell_rule_requires_windows(self):
        self.assertEqual(self.data["rules"], [{"file": "rules/windows.md", "audience": "both",
                                               "requires": ["os:windows"]}])
        self.assertTrue(read_text(MODULE / "rules" / "windows.md").startswith("## Windows shell\n"))

    def test_no_file_of_the_module_names_the_old_plugin_as_a_skill_owner(self):
        found = []
        for path in sorted(MODULE.rglob("*")):
            if path.is_file() and path.suffix in (".md", ".json") and "tests" not in path.relative_to(MODULE).parts:
                found += [path.relative_to(MODULE).as_posix() for _ in re.findall(r"rigger-windows:", read_text(path))]
        self.assertEqual(found, [])


class SystemTests(unittest.TestCase):
    """The rule and the skill rows reach a session on Windows only (the rule's and each row's `requires`)."""

    def test_windows_gets_the_rule_and_the_three_rows(self):
        found = Assembled("windows")
        self.assertIn("rigger-platform", found.modules())
        text = found.whole()
        self.assertIn("**the PowerShell tool is the default shell**", flat(text))
        for skill in SKILLS:
            self.assertIn("`rigger-platform:%s`" % skill, text)

    def test_macos_and_linux_get_neither(self):
        for os_name in ("macos", "linux"):
            with self.subTest(os=os_name):
                found = Assembled(os_name)
                self.assertNotIn("rigger-platform", found.modules())
                text = found.whole()
                self.assertNotIn("**the PowerShell tool is the default shell**", flat(text))
                self.assertNotIn("rigger-platform:", text)


class CoreCheckTests(unittest.TestCase):
    def test_the_session_start_handler_runs_the_core_check_with_this_plugins_name(self):
        hooks = read_json(MODULE / "hooks" / "hooks.json")["hooks"]
        self.assertEqual(list(hooks), ["SessionStart"])
        (group,) = hooks["SessionStart"]
        self.assertEqual(group["matcher"], "startup|clear|compact")
        self.assertEqual([handler["command"] for handler in group["hooks"]],
                         ['sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh" ' + MODULE.name])
        self.assertEqual(MODULE.name, "rigger-platform")


class SkillWordsTests(unittest.TestCase):
    def test_the_herd_docs_folder_is_the_new_one_and_the_old_one_is_the_fallback(self):
        text = flat(read_text(MODULE / "skills" / "php-herd-tooling" / "SKILL.md"))
        new, old = text.find(HERD_NEW), text.find(HERD_OLD)
        self.assertGreater(new, -1)
        self.assertGreater(old, new)
        self.assertIn("(when that is absent, for one release, `" + HERD_OLD + "`)", text)
        self.assertEqual(text.count(HERD_NEW), 1)

    def test_the_guard_marker_is_the_shellwise_one_and_no_path_is_dead(self):
        text = read_text(MODULE / "skills" / "windows-dev-env" / "SKILL.md")
        self.assertIn("`# shellwise: allow`", text)
        self.assertNotIn("shell-guard: allow", text)
        self.assertNotIn("tools/shell-guard", text)
        # The paragraph says where the guard's facts live now: each named file exists there.
        for named, path in (("`README.md`", PLUGINS / "rigger-shellwise" / "README.md"),
                            ("`shellwise.toml`", PLUGINS / "rigger-shellwise" / "shellwise.toml"),
                            ("the test-evidence README of rigger-testing",
                             PLUGINS / "rigger-testing" / "tools" / "test-evidence" / "README.md")):
            with self.subTest(file=named):
                self.assertIn(named, flat(text))
                self.assertTrue(path.is_file(), path)

    def test_every_plugin_folder_path_in_the_skills_exists(self):
        # `<the rigger-core plugin folder>/tools/...` in a skill names a file rigger-core ships (mem_check.py, D80);
        # no other plugin folder is named.
        found = []
        for skill in SKILLS:
            text = read_text(MODULE / "skills" / skill / "SKILL.md")
            found += re.findall(r"<the (rigger-[a-z]+) plugin folder>/([^\s\"`]+)", text)
        self.assertIn(("rigger-core", "tools/mem-check/mem_check.py"), found)
        for plugin, path in found:
            with self.subTest(plugin=plugin, path=path):
                self.assertEqual(plugin, "rigger-core")
                self.assertTrue((CORE / path).is_file(), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
