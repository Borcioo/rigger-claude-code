"""Tests for rules/rules_start.sh and hooks/hooks.json: each hook runs through a real POSIX sh."""

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
REPO = PLUGIN.parent.parent
SCRIPT = PLUGIN / "rules" / "rules_start.sh"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
MATCHER = "startup|clear|compact"
SIZE_LIMIT = 9000
SESSION_FILES = [
    "00-scope.md", "english.md", "scope.md", "git.md", "tests.md", "verify.md", "verify-main.md",
    "plans.md", "plans-main.md", "defects.md", "environment.md", "skills.md",
]
SUBAGENT_FILES = [
    ["00-scope.md", "english.md", "scope.md", "git.md", "tests.md"],
    ["verify.md", "plans.md", "defects.md"],
    ["skills.md"],
]
SKIPPED_AGENTS = ["Explore", "Plan", "rigger-core:implementer", "rigger-core:mapper"]
WORKTREE_COMMAND = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/worktree_root.sh"'
EXTRA_COMMAND = 'sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_extra.sh"'
HOSTILE = 'back\\slash "quoted" \\u0041 tab\there äöüß → ≠\ntrailing backslash\\\n"\n\\\\\n'


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def write_bytes(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def lf_size(path):
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


def over_limit(paths, limit=SIZE_LIMIT):
    return [path.name for path in paths if lf_size(path) > limit]


def load_hooks():
    with open(HOOKS_JSON, encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rules-hooks-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sh = find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")

    def run_command(self, command, root=PLUGIN, stdin=b"", **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["HOME"] = env["USERPROFILE"] = str(self.tmp)
        env["CLAUDE_CONFIG_DIR"] = str(self.tmp / "cfg")
        env["CLAUDE_PLUGIN_ROOT"] = Path(root).as_posix()
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        env.update(extra)
        proc = subprocess.run(
            [self.sh, "-c", command],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.tmp),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout

    def fixture(self, files, root=None):
        root = root or self.tmp / "plugin"
        (root / "rules").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, root / "rules" / "rules_start.sh")
        for name, text in files.items():
            write_bytes(root / "rules" / name, text)
        return root

    def run_script(self, root, args, stdin=b"", **extra):
        command = 'sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_start.sh" ' + args
        return self.run_command(command, root=root, stdin=stdin, **extra)

    def context(self, stdout):
        data = json.loads(stdout.decode("utf-8"))
        self.assertEqual(list(data), ["hookSpecificOutput"])
        self.assertEqual(sorted(data["hookSpecificOutput"]), ["additionalContext", "hookEventName"])
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        return data["hookSpecificOutput"]["additionalContext"]


class HooksJsonTests(unittest.TestCase):
    def test_session_start_has_the_matcher_and_one_handler_per_file(self):
        groups = load_hooks()["SessionStart"]
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]["matcher"], MATCHER)
        commands = [handler["command"] for handler in groups[0]["hooks"]]
        self.assertEqual(
            commands, ['sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_start.sh" ' + name for name in SESSION_FILES]
        )

    def test_worktree_group_has_the_matcher_and_one_handler(self):
        self.assertEqual(load_hooks()["SessionStart"][1], {
            "matcher": MATCHER, "hooks": [{"type": "command", "command": WORKTREE_COMMAND, "timeout": 10}],
        })

    def test_extra_rules_group_comes_last_with_the_matcher(self):
        self.assertEqual(load_hooks()["SessionStart"][-1], {
            "matcher": MATCHER, "hooks": [{"type": "command", "command": EXTRA_COMMAND, "timeout": 10}],
        })

    def test_every_rules_file_is_wired(self):
        self.assertEqual(sorted(SESSION_FILES), sorted(path.name for path in (PLUGIN / "rules").glob("*.md")))

    def test_subagent_start_has_no_matcher_and_one_handler_per_list(self):
        groups = load_hooks()["SubagentStart"]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("matcher", groups[0])
        self.assertEqual(
            [handler["command"] for handler in groups[0]["hooks"]],
            ['sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_start.sh" --subagent ' + " ".join(names)
             for names in SUBAGENT_FILES] + [EXTRA_COMMAND + " --subagent"],
        )

    def test_only_the_main_session_files_stay_out_of_subagents(self):
        given = {name for names in SUBAGENT_FILES for name in names}
        self.assertEqual(set(SESSION_FILES) - given, {"verify-main.md", "plans-main.md", "environment.md"})

    def test_every_handler_is_a_forward_slash_command_with_a_timeout(self):
        hooks = load_hooks()
        handlers = [h for groups in hooks.values() for group in groups for h in group["hooks"]]
        self.assertEqual(len(handlers), 18)
        for handler in handlers:
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["timeout"], 10)
            self.assertNotIn("\\", handler["command"])

    def test_depends_on_core(self):
        with open(PLUGIN / ".claude-plugin" / "plugin.json", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["dependencies"], ["rigger-core"])


class WorktreeRootTests(Base):
    def test_set_root_prints_one_line(self):
        for value in ("work\\trees", "back\\slash and space\\trees", "/srv/work trees"):
            with self.subTest(value=value):
                out = self.run_command(WORKTREE_COMMAND, HARNESS_WORKTREE_ROOT=value)
                self.assertEqual(out, ("Worktree root: %s\n" % value).encode("utf-8"))

    def test_unset_or_empty_prints_nothing(self):
        self.assertEqual(self.run_command(WORKTREE_COMMAND), b"")
        self.assertEqual(self.run_command(WORKTREE_COMMAND, HARNESS_WORKTREE_ROOT=""), b"")


class SessionStartTests(Base):
    def test_each_handler_prints_exactly_the_root_line_and_its_file(self):
        handlers = load_hooks()["SessionStart"][0]["hooks"]
        self.assertEqual(len(handlers), len(SESSION_FILES))
        for handler, name in zip(handlers, SESSION_FILES):
            with self.subTest(file=name):
                expected = ("Plugin root: %s\n" % PLUGIN.as_posix()).encode("utf-8")
                expected += (PLUGIN / "rules" / name).read_bytes()
                self.assertEqual(self.run_command(handler["command"]), expected)

    def test_crlf_and_a_last_line_without_newline_print_as_clean_lines(self):
        root = self.fixture({"a.md": HOSTILE + "crlf line\r\nlast line"})
        expected = "Plugin root: %s\n%scrlf line\nlast line\n" % (root.as_posix(), HOSTILE)
        self.assertEqual(self.run_script(root, "a.md").decode("utf-8"), expected)
        self.assertEqual(self.context(self.run_script(root, "--subagent a.md", stdin=b"{}")), expected)

    def test_root_falls_back_to_the_script_path(self):
        root = self.fixture({"a.md": "text\n"})
        out = self.run_command('unset CLAUDE_PLUGIN_ROOT; sh "%s/rules/rules_start.sh" a.md' % root.as_posix())
        self.assertEqual(out.decode("utf-8"), "Plugin root: %s/rules/..\ntext\n" % root.as_posix())

    def test_root_falls_back_to_the_parent_of_a_bare_script_name(self):
        root = self.fixture({"a.md": "text\n"})
        out = self.run_command('unset CLAUDE_PLUGIN_ROOT; cd "%s/rules" && sh rules_start.sh a.md' % root.as_posix())
        self.assertEqual(out, b"Plugin root: ..\ntext\n")

    def test_missing_file_is_reported_in_the_text(self):
        root = self.fixture({})
        out = self.run_script(root, "gone.md").decode("utf-8")
        self.assertEqual(
            out,
            "Plugin root: %s\nrules_start: missing rules file %s/rules/gone.md\n" % (root.as_posix(), root.as_posix()),
        )

    def test_vars_replace_every_occurrence_once(self):
        root = self.fixture({"a.md": "{{x}} and {{x}}, {{y}} {{unknown}}\nplain\n"})
        out = self.run_script(root, "--var x=one --var y={{x}}z a.md").decode("utf-8")
        self.assertEqual(out.split("\n", 1)[1], "one and one, {{x}}z {{unknown}}\nplain\n")


class SubagentStartTests(Base):
    def test_shape_and_text_of_each_handler(self):
        handlers = load_hooks()["SubagentStart"][0]["hooks"]
        stdin = b'{"agent_type":"rigger-core:worker","hook_event_name":"SubagentStart"}'
        for handler, names in zip(handlers, SUBAGENT_FILES):
            with self.subTest(files=names):
                expected = "Plugin root: %s\n" % PLUGIN.as_posix()
                expected += "".join((PLUGIN / "rules" / n).read_text(encoding="utf-8") for n in names)
                self.assertEqual(self.context(self.run_command(handler["command"], stdin=stdin)), expected)

    def test_skipped_agents_get_nothing(self):
        root = self.fixture({"a.md": "text\n"})
        for agent in SKIPPED_AGENTS:
            for form in ('{"agent_type":"%s"}', '{"agent_id":"a1", "agent_type": "%s", "cwd":"x"}'):
                with self.subTest(agent=agent, form=form):
                    stdin = (form % agent).encode("utf-8")
                    self.assertEqual(self.run_script(root, "--subagent a.md", stdin=stdin), b"")

    def test_other_agents_get_the_rules(self):
        root = self.fixture({"a.md": "text\n"})
        for stdin in (
            '{"agent_type":"general-purpose"}',
            '{"agent_type":"rigger-core:worker"}',
            '{"agent_type":"rigger-core:reviewer"}',
            '{"agent_type":"Explorer"}',
            '{"agent_type":"explore"}',
            '{"agent_type":"my:Explore"}',
            '{"agent_type":"worker","cwd":"/srv/agent_type/Explore"}',
            '{"agent_type":"worker","note":"\\"agent_type\\":\\"Explore\\""}',
            '{"cwd":"x"}',
            "",
        ):
            with self.subTest(stdin=stdin):
                out = self.run_script(root, "--subagent a.md", stdin=stdin.encode("utf-8"))
                self.assertTrue(self.context(out).endswith("\ntext\n"))

    def test_hostile_text_survives_json_exactly(self):
        root = self.fixture({"a.md": HOSTILE, "b.md": "second\n"})
        out = self.run_script(root, "--subagent a.md b.md", stdin=b"{}")
        self.assertEqual(self.context(out), "Plugin root: %s\n%ssecond\n" % (root.as_posix(), HOSTILE))

    def test_crlf_file_gives_valid_json_without_cr(self):
        root = self.fixture({"a.md": "one\r\ntwo\r\n"})
        out = self.run_script(root, "--subagent a.md", stdin=b"{}")
        self.assertEqual(self.context(out).split("\n", 1)[1], "one\ntwo\n")

    def test_root_is_escaped(self):
        root = self.tmp / "plugin"
        script = self.fixture({"a.md": "text\n"}) / "rules" / "rules_start.sh"
        odd_root = 'x"y'
        command = 'sh "%s" --subagent a.md' % script.as_posix()
        out = self.run_command(command, root=odd_root, stdin=b"{}")
        self.assertEqual(self.context(out), "Plugin root: %s\nrules_start: missing rules file %s/rules/a.md\n"
                         % (odd_root, odd_root))
        self.assertTrue(root.is_dir())


class DropTests(Base):
    BODY = "intro\n## One\none line\n### Inner\nstill one\n## Two\ntwo line\n## Three\nthree line\n"

    def lines(self, value, args="a.md b.md"):
        root = self.fixture({"a.md": self.BODY, "b.md": "## Other\nother line\n"})
        return self.run_script(root, args, HARNESS_RULES_DROP=value).decode("utf-8").split("\n", 1)[1]

    def test_basename_skips_the_file(self):
        self.assertEqual(self.lines("a.md"), "## Other\nother line\n")

    def test_every_file_dropped_prints_nothing(self):
        root = self.fixture({"a.md": "text\n"})
        self.assertEqual(self.run_script(root, "a.md", HARNESS_RULES_DROP="a.md"), b"")
        self.assertEqual(self.run_script(root, "--subagent a.md", stdin=b"{}", HARNESS_RULES_DROP="a.md"), b"")

    def test_heading_skips_up_to_the_next_heading(self):
        self.assertEqual(
            self.lines("## One"), "intro\n## Two\ntwo line\n## Three\nthree line\n## Other\nother line\n"
        )

    def test_last_heading_skips_to_the_end_of_its_file(self):
        self.assertEqual(
            self.lines("## Three"),
            "intro\n## One\none line\n### Inner\nstill one\n## Two\ntwo line\n## Other\nother line\n",
        )

    def test_heading_with_trailing_blanks_is_matched_trimmed(self):
        root = self.fixture({"a.md": "intro\n## One \t\none line\n## Two\ntwo line\n"})
        out = self.run_script(root, "a.md", HARNESS_RULES_DROP="## One").decode("utf-8")
        self.assertEqual(out.split("\n", 1)[1], "intro\n## Two\ntwo line\n")

    def test_separators_and_trimming(self):
        expected = "intro\n## Three\nthree line\n"
        for value in (
            "## One;## Two;b.md",
            "## One\n## Two\nb.md",
            "  ## One  ;\t## Two\t\n b.md \r\n",
            ";;## One;;\n\n## Two;b.md;",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.lines(value), expected)

    def test_match_is_exact(self):
        (self.tmp / "a.md").write_text("a file in the cwd, so that an unquoted * would expand to it\n", encoding="utf-8")
        whole = self.lines("")
        self.assertIn("## One", whole)
        for value in ("One", "## one", "##  One", "## On", "## O*", "## One more", "xa.md", "a", "A.MD", "a.m?", "*",
                      "### Inner", "intro"):
            with self.subTest(value=value):
                self.assertEqual(self.lines(value), whole)

    def test_heading_drop_in_subagent_json(self):
        root = self.fixture({"a.md": self.BODY})
        out = self.run_script(root, "--subagent a.md", stdin=b"{}", HARNESS_RULES_DROP="## Two;## Three;## One")
        self.assertEqual(self.context(out).split("\n", 1)[1], "intro\n")


class RulesFilesTests(Base):
    def rules_files(self):
        files = sorted(REPO.glob("plugins/*/rules/*.md"))
        self.assertGreaterEqual(len(files), 19)
        return files

    def test_size_check_limit(self):
        at_limit = self.tmp / "at.md"
        write_bytes(at_limit, "x" * SIZE_LIMIT)
        above = self.tmp / "above.md"
        write_bytes(above, "x" * (SIZE_LIMIT + 1))
        crlf = self.tmp / "crlf.md"
        write_bytes(crlf, ("x" * 99 + "\r\n") * 90)  # 9000 with LF, 9090 with CRLF
        self.assertEqual(over_limit([at_limit]), [])
        self.assertEqual(over_limit([above]), ["above.md"])
        self.assertEqual(over_limit([crlf]), [])

    def test_rules_files_are_under_the_limit(self):
        self.assertEqual(over_limit(self.rules_files()), [])

    def test_each_handler_output_is_under_the_limit_with_a_long_root(self):
        long_root = self.tmp / ("r" * 150)
        for path in (PLUGIN / "rules").iterdir():
            write_bytes(long_root / "rules" / path.name, path.read_bytes().decode("utf-8"))
        hooks = load_hooks()
        handlers = hooks["SessionStart"][0]["hooks"] + hooks["SubagentStart"][0]["hooks"]
        for handler in [h for h in handlers if "rules_start.sh" in h["command"]]:
            with self.subTest(command=handler["command"]):
                out = self.run_command(handler["command"], root=long_root, stdin=b'{"agent_type":"w"}')
                text = out.decode("utf-8")
                text = self.context(out) if "--subagent" in handler["command"] else text
                self.assertGreater(len(text), 100)
                self.assertLessEqual(len(text), SIZE_LIMIT)

    def test_rules_files_are_lf_utf8_without_control_characters(self):
        for path in self.rules_files():
            with self.subTest(file=path.name):
                text = path.read_bytes().decode("utf-8")
                self.assertTrue(text.endswith("\n"))
                self.assertIsNone(re.search(r"[\x00-\x09\x0b-\x1f]", text))

    def test_script_copies_are_identical(self):
        folders = [path for path in sorted(REPO.glob("plugins/*/rules")) if path.is_dir()]
        self.assertGreaterEqual(len(folders), 6)
        for folder in folders:
            with self.subTest(path=folder.parent.name):
                self.assertEqual((folder / "rules_start.sh").read_bytes(), SCRIPT.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
