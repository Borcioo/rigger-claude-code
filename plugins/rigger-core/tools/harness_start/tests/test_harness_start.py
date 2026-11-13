"""Tests for harness_start.py, run as the hook runs: a child process with a fake home and config."""

import codecs
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "harness_start.py"
PLUGIN = "rigger-core@rigger"
OTHER = "other@rigger"
GUARD_HANDLER = {"type": "command", "command": "python", "args": ["-I", "-S", "~/.claude/tools/g/context_guard.py"]}
PLUGIN_COMMAND = 'sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/g/context_guard.py" || true'


def group(handler, matcher=None):
    data = {"hooks": [handler]}
    if matcher is not None:
        data["matcher"] = matcher
    return data


def plugin_handler(command=PLUGIN_COMMAND, **extra):
    return dict({"type": "command", "command": command, "timeout": 5}, **extra)


class HarnessStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harness start "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.config = self.tmp / "config dir"
        self.home.mkdir()
        self.config.mkdir()
        self.installed = {}
        self.enabled = {}

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle)

    def install(self, key, hooks, scope="user", enabled=True):
        path = self.tmp / "cache" / key.split("@")[0] / scope
        self.write_json(path / "hooks" / "hooks.json", {"hooks": hooks})
        self.installed.setdefault(key, []).append({"scope": scope, "installPath": str(path)})
        self.enabled[key] = enabled

    def settings(self, hooks, name="settings.json", **extra):
        value = dict({"hooks": hooks, "enabledPlugins": self.enabled}, **extra)
        self.write_json(self.config / name, value)

    def run_hook(self):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_CONFIG_DIR=str(self.config))
        if self.installed:
            self.write_json(self.config / "plugins" / "installed_plugins.json", {"version": 2, "plugins": self.installed})
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-X", "utf8", str(SCRIPT)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertNotIn(b"\r", proc.stdout)
        return proc.stdout.decode("utf-8")

    def assert_duplicate(self, output, *expected):
        self.assertEqual(len(output.splitlines()), 1, output)
        self.assertIn("rigger: hook runs 2 times", output)
        for text in expected:
            self.assertIn(text, output)

    def test_settings_args_form_matches_plugin_shell_form(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.assert_duplicate(
            self.run_hook(),
            "(UserPromptSubmit, context_guard.py)",
            str(self.config / "settings.json"),
            "plugin " + PLUGIN,
            "remove the entry from",
        )

    def test_settings_shell_form_matches(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": "python -I -S ~/x/context_guard.py"})]})
        self.assert_duplicate(self.run_hook(), "context_guard.py")

    def test_backslash_path_and_case_match(self):
        path = "~/x/CONTEXT_GUARD.py".replace("/", "\\")
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": "python", "args": ["-I", path]})]})
        self.assert_duplicate(self.run_hook(), "context_guard.py")

    def test_settings_local_is_read(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({})
        self.write_json(self.config / "settings.local.json", {"hooks": {"UserPromptSubmit": [group(GUARD_HANDLER)]}})
        self.assert_duplicate(self.run_hook(), "settings.local.json")

    def test_args_matcher_and_if_are_part_of_the_identity(self):
        guard = 'sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" "${CLAUDE_PLUGIN_ROOT}/g.py" pre || true'
        self.install(PLUGIN, {"PreCompact": [group(plugin_handler(guard), "auto")]})
        same = {"type": "command", "command": "python", "args": ["-I", "/x/g.py", "pre"]}
        other_args = dict(same, args=["-I", "/x/g.py", "post"])
        cases = {
            "args": {"PreCompact": [group(other_args, "auto")]},
            "matcher": {"PreCompact": [group(same, "manual")]},
            "if": {"PreCompact": [group(dict(same, **{"if": "Bash(x *)"}), "auto")]},
            "event": {"PostCompact": [group(same, "auto")]},
        }
        for name, hooks in cases.items():
            with self.subTest(differs=name):
                self.settings(hooks)
                self.assertEqual(self.run_hook(), "")
        self.settings({"PreCompact": [group(same, "auto")]})
        self.assert_duplicate(self.run_hook(), "matcher auto", "g.py pre")

    def test_same_if_value_is_a_duplicate(self):
        condition = {"if": "Bash(git restore *)"}
        self.install(PLUGIN, {"PreToolUse": [group(plugin_handler(**condition), "Bash")]})
        self.settings({"PreToolUse": [group(dict(GUARD_HANDLER, **condition), "Bash")]})
        self.assert_duplicate(self.run_hook(), "if Bash(git restore *)")

    def test_catch_all_matchers_are_one_matcher(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        for matcher in ("", "*"):
            with self.subTest(matcher=matcher):
                self.settings({"UserPromptSubmit": [group(GUARD_HANDLER, matcher)]})
                self.assert_duplicate(self.run_hook(), "context_guard.py")

    def test_disabled_or_other_scope_plugin_does_not_count(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]}, enabled=False)
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.assertEqual(self.run_hook(), "")
        self.installed.clear()
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]}, scope="project")
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.assertEqual(self.run_hook(), "")

    def test_two_plugins_with_one_handler(self):
        hooks = {"UserPromptSubmit": [group(plugin_handler())]}
        self.install(PLUGIN, hooks)
        self.install(OTHER, hooks)
        self.settings({})
        self.assert_duplicate(self.run_hook(), PLUGIN, OTHER, "disable one of the plugins")

    def test_handlers_without_a_script_compare_by_command(self):
        echo = {"type": "command", "command": "echo hello"}
        self.install(PLUGIN, {"UserPromptSubmit": [group(echo)]})
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": "echo other"})]})
        self.assertEqual(self.run_hook(), "")
        self.settings({"UserPromptSubmit": [group(echo)]})
        self.assert_duplicate(self.run_hook(), "`echo hello`")

    def test_redirects_and_a_leading_list_do_not_hide_a_duplicate(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        for command in ("python ~/x/context_guard.py 2>/dev/null",
                        "python ~/x/context_guard.py 2>&1",
                        "python ~/x/context_guard.py >out.log 2>&1 || true",
                        "python 2>/dev/null ~/x/context_guard.py",
                        "cd ~/x && python context_guard.py",
                        "cd ~/x ; python context_guard.py",
                        "cd ~/x && python context_guard.py 2>/dev/null || true"):
            with self.subTest(command=command):
                self.settings({"UserPromptSubmit": [group({"type": "command", "command": command})]})
                self.assert_duplicate(self.run_hook(), "(UserPromptSubmit, context_guard.py)")

    def test_a_word_after_a_redirect_is_still_an_argument(self):
        self.install(PLUGIN, {"PreCompact": [group(plugin_handler('python /x/g.py pre'), "auto")]})
        for redirect in ("2>/dev/null", "<in.txt"):
            with self.subTest(redirect=redirect):
                same = "python /x/g.py %s pre" % redirect
                self.settings({"PreCompact": [group({"type": "command", "command": same}, "auto")]})
                self.assert_duplicate(self.run_hook(), "g.py pre")
                other = "python /x/g.py %s post" % redirect
                self.settings({"PreCompact": [group({"type": "command", "command": other}, "auto")]})
                self.assertEqual(self.run_hook(), "")

    def test_a_digit_before_a_spaced_redirect_is_an_argument_not_an_fd(self):
        self.install(PLUGIN, {"PreCompact": [group(plugin_handler("python /x/g.py 5 > out"), "auto")]})
        for command in ("python /x/g.py 6 > out", "python /x/g.py > out", "python /x/g.py 5 5 > out"):
            with self.subTest(other=command):
                self.settings({"PreCompact": [group({"type": "command", "command": command}, "auto")]})
                self.assertEqual(self.run_hook(), "")
        for command in ("python /x/g.py 5 > out", "python /x/g.py '5'>out"):
            with self.subTest(same=command):
                self.settings({"PreCompact": [group({"type": "command", "command": command}, "auto")]})
                self.assert_duplicate(self.run_hook(), "g.py 5")

    def test_a_digit_glued_to_a_redirect_is_an_fd_and_dropped(self):
        self.install(PLUGIN, {"PreCompact": [group(plugin_handler("python /x/g.py pre"), "auto")]})
        for command in ("python /x/g.py pre 2>/dev/null", "python /x/g.py pre 1>&2", "python /x/g.py pre 5> out",
                        "python /x/g.py 2>/dev/null pre"):
            with self.subTest(command=command):
                self.settings({"PreCompact": [group({"type": "command", "command": command}, "auto")]})
                self.assert_duplicate(self.run_hook(), "g.py pre")

    def test_a_script_name_behind_a_redirect_or_a_fallback_is_not_what_runs(self):
        for name, first, second in (
            ("redirect target", "echo one > same.py", "echo two > same.py"),
            ("pipe", "echo one | same.py", "echo two | same.py"),
            ("fallback", "echo one || same.py", "echo two || same.py"),
            ("background", "echo one & same.py", "echo two & same.py"),
        ):
            with self.subTest(operator=name):
                self.installed.clear()
                self.install(PLUGIN, {"UserPromptSubmit": [group({"type": "command", "command": first})]})
                self.settings({"UserPromptSubmit": [group({"type": "command", "command": second})]})
                self.assertEqual(self.run_hook(), "")

    def test_a_command_shlex_cannot_split_compares_by_its_text(self):
        broken = 'python "/x/g.py'
        self.install(PLUGIN, {"UserPromptSubmit": [group({"type": "command", "command": broken})]})
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": 'python "/y/g.py'})]})
        self.assertEqual(self.run_hook(), "")
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": "  " + broken + " "})]})
        self.assert_duplicate(self.run_hook(), "`%s`" % broken)

    def test_clean_and_empty_configs_print_nothing(self):
        self.assertEqual(self.run_hook(), "")
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({"UserPromptSubmit": [group({"type": "command", "command": "echo stays"})]})
        self.assertEqual(self.run_hook(), "")

    def test_plugin_without_hooks_file_prints_nothing(self):
        self.install(PLUGIN, {})
        Path(self.installed[PLUGIN][0]["installPath"], "hooks", "hooks.json").unlink()
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.assertEqual(self.run_hook(), "")

    def test_settings_with_bom_is_read(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        text = json.dumps({"hooks": {"UserPromptSubmit": [group(GUARD_HANDLER)]}, "enabledPlugins": self.enabled})
        (self.config / "settings.json").write_bytes(text.encode("utf-8-sig"))
        self.assert_duplicate(self.run_hook(), "context_guard.py")

    def test_unreadable_files_are_named_not_silent(self):
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        hooks_file = Path(self.installed[PLUGIN][0]["installPath"], "hooks", "hooks.json")
        hooks_file.write_bytes(b"{broken")
        self.settings({})
        self.assertIn("rigger: cannot read %s" % hooks_file, self.run_hook())
        (self.config / "settings.json").write_bytes(b"{broken")
        self.assertIn("rigger: cannot read %s" % (self.config / "settings.json"), self.run_hook())

    def test_malformed_shapes_do_not_fail_the_hook(self):
        self.write_json(self.config / "plugins" / "installed_plugins.json", {"plugins": {PLUGIN: "x", OTHER: [1, {}]}})
        self.settings({"A": "x", "B": [1, {"hooks": "x"}, {"hooks": [1, {"type": "http"}, {"command": 1}]}]})
        self.assertEqual(self.run_hook(), "")

    def test_failure_is_reported_and_exits_zero(self):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home))
        probe = (
            "import runpy, sys; module = runpy.run_path(%r, run_name='probe')\n"
            "module['build_output'].__globals__['config_root'] = None\nsys.exit(module['main']())\n"
        ) % str(SCRIPT)
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", probe], env=env, capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"rigger: harness_start failed (TypeError", proc.stdout)


    # CLAUDE.md headings that a rules file of an enabled plugin injects too.

    def add_rules(self, key, files, **kwargs):
        self.install(key, {}, **kwargs)
        rules = Path(self.installed[key][0]["installPath"], "rules")
        rules.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (rules / name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def claude_md(self, content):
        (self.config / "CLAUDE.md").write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def warning(self, heading, source):
        return (
            'rigger: CLAUDE.md section "%s" is also injected by %s; delete that section from %s '
            "because the plugin injects it\n" % (heading, source, self.config / "CLAUDE.md")
        )

    def duplicate_setup(self):
        """A settings/plugin hook pair that always yields exactly one duplicate warning."""
        self.install(PLUGIN, {"UserPromptSubmit": [group(plugin_handler())]})
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})

    def test_heading_in_both_warns_once_with_heading_and_source(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\nrule line\n## Other rule\n"})
        self.settings({})
        self.claude_md("# Title\n\n## Tests\nmine\n\n## Mine only\nmine\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))

    def test_every_overlapping_heading_warns_once_in_claude_md_order(self):
        self.add_rules(OTHER, {"a.md": "## Second\n## First\n", "b.md": "## Third\n"})
        self.settings({})
        self.claude_md("## First\n## Second\n## First\n## Third\n## Not shared\n")
        self.assertEqual(
            self.run_hook(),
            self.warning("## First", "other/rules/a.md")
            + self.warning("## Second", "other/rules/a.md")
            + self.warning("## Third", "other/rules/b.md"),
        )

    def test_no_overlap_prints_nothing(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\n## Plans\n"})
        self.settings({})
        self.claude_md("## Verify\n## Tests and more\n## Plan\n")
        self.assertEqual(self.run_hook(), "")

    def test_only_two_hash_headings_count(self):
        self.add_rules(OTHER, {"deep.md": "### Deep\n#### Deeper\n## Shared\n", "one.md": "# Deep\n##Shared\n"})
        self.settings({})
        self.claude_md("### Shared\n## Deep\n# Deep\n#### Deeper\n ## Shared\n##Shared\n### Deep\n")
        self.assertEqual(self.run_hook(), "")

    def test_headings_match_the_way_rules_start_matches_them(self):
        self.add_rules(OTHER, {"tests.md": "## Tests \t\n"})
        self.settings({})
        self.claude_md("## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("## Tests  \t\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        for text in ("## tests\n", "## Tests.\n", "##  Tests\n", "## Te sts\n"):
            with self.subTest(claude_md=text):
                self.claude_md(text)
                self.assertEqual(self.run_hook(), "")

    def test_only_a_line_feed_ends_a_heading_line(self):
        # rules_start.sh reads lines up to LF, so a lone CR stays inside the heading text.
        self.add_rules(OTHER, {"tests.md": "## Tests\n## Plans\n"})
        self.settings({})
        self.claude_md("## Tests\r## Plans\n")
        self.assertEqual(self.run_hook(), "")

    def test_a_bom_hides_the_first_heading_of_a_rules_file_as_it_does_for_rules_start(self):
        self.add_rules(OTHER, {"tests.md": codecs.BOM_UTF8 + b"## Tests\n", "plans.md": codecs.BOM_UTF8 + b"x\n## Plans\n"})
        self.settings({})
        self.claude_md("## Tests\n## Plans\n")
        self.assertEqual(self.run_hook(), self.warning("## Plans", "other/rules/plans.md"))

    def test_headings_inside_fenced_code_are_not_headings(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\n## Plans\n"})
        self.settings({})
        fenced = {
            "backticks": "```\n## Tests\n```\n",
            "tildes": "~~~\n## Tests\n~~~\n",
            "long fence closed by a long one": "````md\n```\n## Tests\n```\n````\n",
            "info string": "```markdown\n## Tests\n```\n",
            "unclosed": "intro\n```\n## Tests\n",
            "indented fence": "   ```\n## Tests\n   ```\n",
            "crlf": "```\r\n## Tests\r\n```\r\n",
            "tildes crlf": "~~~\r\n## Tests\r\n~~~\r\n",
        }
        for name, text in fenced.items():
            with self.subTest(fence=name):
                self.claude_md(text)
                self.assertEqual(self.run_hook(), "")
        # A heading after the fence closes is a heading again; a shorter or other-kind marker does not close it.
        self.claude_md("```\n## Plans\n```\n## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("````\n```\n~~~\n## Plans\n````\n## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("```\n~~~\n## Plans\n~~~\n```\n## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("```\n## Plans\n``` text\n## Tests\n")
        self.assertEqual(self.run_hook(), "")
        self.claude_md("``` not a fence`\n## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("```\r\n## Plans\r\n```\r\n## Tests\r\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md("~~~\r\n## Plans\r\n~~~\r\n## Tests\r\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))

    def test_install_path_with_a_nul_byte_does_not_crash(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\n"})
        self.installed["nul@rigger"] = [{"scope": "user", "installPath": str(self.tmp) + "\x00x"}]
        self.enabled["nul@rigger"] = True
        self.settings({})
        self.claude_md("## Tests\n")
        lines = self.run_hook().splitlines(True)
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("rigger: cannot read", lines[0])
        self.assertEqual(lines[1], self.warning("## Tests", "other/rules/tests.md"))

    def test_bom_and_crlf_in_claude_md_still_match(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\n## Plans\n"})
        self.settings({})
        self.claude_md(codecs.BOM_UTF8 + b"## Tests\r\nbody\r\n```\r\n## Plans\r\n```\r\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))
        self.claude_md(b"# Title\r\n## Plans\r\n")
        self.assertEqual(self.run_hook(), self.warning("## Plans", "other/rules/tests.md"))

    def test_crlf_in_a_rules_file_still_matches(self):
        self.add_rules(OTHER, {"tests.md": b"## Tests\r\nbody\r\n"})
        self.settings({})
        self.claude_md("## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))

    def test_non_ascii_heading_matches(self):
        heading = "## Prüfungen — vor der Arbeit"
        self.add_rules(OTHER, {"de.md": heading + "\n"})
        self.settings({})
        self.claude_md(heading + "\n")
        self.assertEqual(self.run_hook(), self.warning(heading, "other/rules/de.md"))

    def test_same_heading_in_two_rules_files_names_the_first_sorted(self):
        self.add_rules(OTHER, {"z.md": "## Tests\n", "b.md": "## Tests\n"})
        self.add_rules("zeta@rigger", {"a.md": "## Tests\n"})
        self.settings({})
        self.claude_md("## Tests\n")
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/b.md"))
        self.add_rules("alpha@rigger", {"z.md": "## Tests\n"})
        self.settings({})
        self.assertEqual(self.run_hook(), self.warning("## Tests", "alpha/rules/z.md"))

    def test_only_markdown_files_directly_under_rules_count(self):
        self.add_rules(OTHER, {"notes.txt": "## Tests\n", "rules_start.sh": "## Tests\n", "md": "## Tests\n"})
        Path(self.installed[OTHER][0]["installPath"], "rules", "sub").mkdir()
        Path(self.installed[OTHER][0]["installPath"], "rules", "sub", "deep.md").write_text("## Tests\n", encoding="utf-8")
        Path(self.installed[OTHER][0]["installPath"], "other.md").write_text("## Tests\n", encoding="utf-8")
        Path(self.installed[OTHER][0]["installPath"], "rules", "dir.md").mkdir()
        self.settings({})
        self.claude_md("## Tests\n")
        self.assertEqual(self.run_hook(), "")

    def test_disabled_or_other_scope_plugin_rules_do_not_count(self):
        self.add_rules(OTHER, {"tests.md": "## Tests\n"}, enabled=False)
        self.settings({})
        self.claude_md("## Tests\n")
        self.assertEqual(self.run_hook(), "")
        self.installed.clear()
        self.enabled.clear()
        self.add_rules(OTHER, {"tests.md": "## Tests\n"}, scope="project")
        self.settings({})
        self.assertEqual(self.run_hook(), "")
        self.installed.clear()
        self.add_rules(OTHER, {"tests.md": "## Tests\n"})
        self.settings({})
        self.assertEqual(self.run_hook(), self.warning("## Tests", "other/rules/tests.md"))

    def test_missing_install_path_rules_folder_and_unreadable_rules_are_silent(self):
        self.duplicate_setup()
        self.installed["gone@rigger"] = [{"scope": "user", "installPath": str(self.tmp / "no such dir")}]
        self.installed["norules@rigger"] = [{"scope": "user", "installPath": str(self.tmp / "cache")}]
        self.add_rules("filerules@rigger", {})
        rules = Path(self.installed["filerules@rigger"][0]["installPath"], "rules")
        rules.rmdir()
        rules.write_text("## Tests\n", encoding="utf-8")
        self.add_rules("bad@rigger", {"a.md": b"## Tests\n\xff\xfe\n", "b.md": b"\xff## Tests\n"})
        self.add_rules("good@rigger", {"c.md": "## Tests\n"})
        for key in ("gone", "norules"):
            self.enabled[key + "@rigger"] = True
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.claude_md("## Tests\n")
        output = self.run_hook()
        lines = output.splitlines(True)
        self.assertEqual(len(lines), 2, output)
        self.assert_duplicate("".join(lines[:1]))
        self.assertEqual(lines[1], self.warning("## Tests", "good/rules/c.md"))

    def test_missing_claude_md_is_silent_and_duplicates_still_print(self):
        self.duplicate_setup()
        self.add_rules(OTHER, {"tests.md": "## Tests\n"})
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.assert_duplicate(self.run_hook())

    def test_unreadable_claude_md_is_silent_and_duplicates_still_print(self):
        self.duplicate_setup()
        self.add_rules(OTHER, {"tests.md": "## Tests\n"})
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        cases = {
            "invalid utf-8 after the heading": b"## Tests\n\xff\xfe\n",
            "invalid utf-8 before the heading": b"\xc3\x28\n## Tests\n",
            "a folder": None,
            "empty": b"",
        }
        for name, content in cases.items():
            with self.subTest(claude_md=name):
                path = self.config / "CLAUDE.md"
                if path.is_dir():
                    path.rmdir()
                elif path.exists():
                    path.unlink()
                if content is None:
                    path.mkdir()
                else:
                    path.write_bytes(content)
                self.assert_duplicate(self.run_hook())

    def test_duplicate_warning_comes_before_the_heading_warning(self):
        self.duplicate_setup()
        self.add_rules(OTHER, {"tests.md": "## Tests\n"})
        self.settings({"UserPromptSubmit": [group(GUARD_HANDLER)]})
        self.claude_md("## Tests\n")
        output = self.run_hook().splitlines(True)
        self.assertEqual(len(output), 2, output)
        self.assertIn("hook runs 2 times", output[0])
        self.assertEqual(output[1], self.warning("## Tests", "other/rules/tests.md"))

    def test_shipped_rules_files_are_found_through_a_real_installed_plugins_entry(self):
        plugin = Path(__file__).resolve().parents[4] / "rigger-rules"
        shipped = {}
        for path in sorted((plugin / "rules").glob("*.md")):
            lines = [line.rstrip("\r\n").strip(" \t") for line in path.read_text(encoding="utf-8").split("\n")
                     if line.startswith("## ")]
            if path.name != "00-scope.md":  # the one preamble file, which has no heading by design
                self.assertTrue(lines, "%s lost its `## ` heading" % path.name)
            if lines:
                shipped[path.name] = lines
        self.assertGreaterEqual(len(shipped), 9, shipped)
        key = "rigger-rules@rigger"
        self.installed[key] = [{"scope": "user", "installPath": str(plugin)}]
        self.enabled[key] = True
        self.settings({})
        self.claude_md("# My notes\n\n" + "".join("%s\nmine\n\n" % lines[0] for lines in shipped.values()))
        expected = "".join(self.warning(lines[0], "rigger-rules/rules/" + name) for name, lines in shipped.items())
        self.assertEqual(self.run_hook(), expected)

    def test_entry_point_prints_the_warning_on_stdout_only(self):
        heading = "## Prüfungen — vor der Arbeit"
        self.add_rules(OTHER, {"tests.md": heading + "\n"})
        self.settings({})
        self.claude_md(heading + "\n")
        self.write_json(self.config / "plugins" / "installed_plugins.json", {"version": 2, "plugins": self.installed})
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_CONFIG_DIR=str(self.config))
        proc = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT)], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=30)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        self.assertEqual(proc.stdout, self.warning(heading, "other/rules/tests.md").encode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
