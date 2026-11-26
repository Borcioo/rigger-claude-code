"""Tests for assemble.py: layers, the personal layer, drop, requires, parts, the snapshot and --check."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import assemble  # noqa: E402

REPO = HERE.parents[4]
ASSEMBLE = HERE.parent / "assemble.py"
REPO_CORE = REPO / "plugins" / "rigger-core"
CLEARED = ("RIGGER_MODULE_DIRS", "HARNESS_RULES_DROP", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR",
           "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA")
OTHER_OS = "linux" if os.name == "nt" else "windows"
CORE_ROWS = [{"id": "rigger-core.git-workflow", "before": "branching and committing",
              "load": ["rigger-core:git-workflow"], "section": "skills.skill-triggers"}]
TALK_CONFIG = {
    "reply_language": {"type": "string", "default": ""},
    "reply_lines": {"type": "number", "default": 5},
    "report_lines": {"type": "number", "default": 15},
}
TALK_TEXT = "## Talking to me\n\n- Reply in {{reply_language}}; ~{{reply_lines}} lines, ~{{var:report_lines}} report.\n"
# rigger-shellwise alone fills shell-guard (D75): the double-filler notice rigger-windows caused never comes back.
D75_LINE = ("rigger: slot shell-guard is filled by rigger-shellwise and rigger-windows; disable one with "
            "claude plugin disable <name>")
LANGUAGE_DEFAULT = ("the language of Claude Code's `language` setting (the user's language when it is "
                    "unset)")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def write_json(path, data):
    write(path, json.dumps(data))


def without_root_lines(data):
    return b"".join(line for line in data.splitlines(True) if not line.startswith(b"Plugin root: "))


class Layout:
    """A temp config dir, project dir and fixture modules loaded through RIGGER_MODULE_DIRS."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.config = tmp / "config"
        self.project = tmp / "project"
        self.plugins = tmp / "plugins"
        self.config.mkdir()
        self.project.mkdir()
        self.dirs = []
        self.core = self.module("rigger-core", {"rules/state-file.md": ("## State file\n\nkeep a state file\n", "main")},
                                triggers=CORE_ROWS, listed=False)
        shutil.copyfile(str(REPO_CORE / "rigger-defaults.json"), str(self.core / "rigger-defaults.json"))
        write(self.core / "agents" / "implementer.md", "---\nname: implementer\nomitClaudeMd: true\n---\nx\n")
        write(self.core / "agents" / "mapper.md", "---\nname: mapper\nomitClaudeMd: true\n---\nx\n")
        write(self.core / "agents" / "worker.md", "---\nname: worker\n---\nx\n")
        self.review = self.module("rigger-review", {})
        write(self.review / "agents" / "reviewer.md", "---\nname: reviewer\n---\nx\n")
        self.testing = self.module("rigger-testing", {})
        write(self.testing / "skills" / "testing" / "SKILL.md", "---\nname: testing\n---\n")
        write(self.core / "skills" / "git-workflow" / "SKILL.md", "---\nname: git-workflow\n---\n")

    def module(self, name, files, layer="general", triggers=(), subagents=None, user_config=None, listed=True):
        root = self.plugins / name
        rules = []
        for file, spec in files.items():
            text, audience = spec[0], spec[1]
            write(root / file, text)
            entry = {"file": file, "audience": audience}
            if len(spec) > 2:
                entry["requires"] = spec[2]
            rules.append(entry)
        plugin = {"name": name}
        if user_config:
            plugin["userConfig"] = user_config
        write_json(root / ".claude-plugin" / "plugin.json", plugin)
        manifest = {"schema": 1, "name": name, "layer": layer, "fills": None, "rules": rules,
                    "triggers": list(triggers)}
        if subagents is not None:
            manifest["subagents"] = subagents
        write_json(root / "rigger-module.json", manifest)
        if listed:
            self.dirs.append(str(root))
        return root

    def rules_module(self, **files):
        base = {
            "rules/tests.md": ("## Tests\n\nrun the tests with {{slot:testing}}\n", "both"),
            "rules/git.md": ("## Git ({{skill:x:y}})\n\ngit rule\n", "both"),
            "rules/plans.md": ("## Plans\n\nplan rule\n", "both"),
            "rules/skills.md": ("## Skill triggers\n\n{{triggers:skills.skill-triggers}}\n", "both"),
        }
        base.update(files)
        return self.module("rigger-rules", base)

    def plain_plugin(self, name):
        """A RIGGER_MODULE_DIRS entry with a plugin.json and no manifest: an enabled plugin, not a module (D39)."""
        root = self.plugins / name
        write_json(root / ".claude-plugin" / "plugin.json", {"name": name})
        self.dirs.append(str(root))
        return root

    def manifest_set(self, root, **values):
        path = root / "rigger-module.json"
        data = json.loads(path.read_bytes())
        data.update(values)
        write_json(path, data)

    def personal(self, name, data):
        write(self.config / "rigger" / "rules.d" / name, data)

    def environ(self, **extra):
        env = {"CLAUDE_CONFIG_DIR": str(self.config), "RIGGER_MODULE_DIRS": os.pathsep.join(self.dirs),
               "CLAUDE_PLUGIN_ROOT": str(self.core)}
        env.update(extra)
        return env

    def assemble(self, audience="main", agent_type=None, self_root=None, **extra):
        inputs = assemble.Inputs(config_dir=self.config, project_dir=self.project,
                                 environ=self.environ(**extra), self_root=self_root or self.core)
        return assemble.assemble(inputs, audience, agent_type)

    def hook_input(self, **fields):
        data = {"cwd": str(self.project), "hook_event_name": "SessionStart", "source": "startup"}
        data.update(fields)
        return json.dumps(data).encode("utf-8")

    def cli(self, *args, stdin=None, **extra):
        env = {k: v for k, v in os.environ.items() if k not in CLEARED}
        env.update(self.environ(**extra))
        done = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ASSEMBLE)] + list(args),
                              input=self.hook_input() if stdin is None else stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, cwd=str(self.project), timeout=60)
        return done.returncode, done.stdout, done.stderr

    def part(self, index, subagent=False, stdin=None, **extra):
        args = ["--part", str(index)] + (["--subagent"] if subagent else [])
        code, out, err = self.cli(*args, stdin=stdin, **extra)
        # I3: a part prints nothing only on purpose, and then says so with exit status 10.
        assert code == (10 if out == b"" else 0), (code, out[:80], err)
        return out

    def render(self, *args, **extra):
        code, out, err = self.cli("--render", *args, **extra)
        assert code == 0, (code, err)
        return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def ids(self, found):
        return [section["id"] for section in found.sections]

    def text(self, found):
        return b"".join(block[1] for block in found.blocks)

    def session_outputs(self, session_id):
        """Every way a session prints rules: the six parts on startup, on compact (from the snapshot), --render."""
        startup = self.layout.hook_input(session_id=session_id, source="startup")
        compact = self.layout.hook_input(session_id=session_id, source="compact")
        outputs = {"startup parts": b"".join(self.layout.part(i, stdin=startup) for i in range(1, 7))}
        self.assertTrue((self.layout.config / "rigger" / "state" / (session_id + ".resolved.json")).is_file())
        outputs["compact parts"] = b"".join(self.layout.part(i, stdin=compact) for i in range(1, 7))
        outputs["render"] = self.layout.render()
        outputs["render compact"] = self.layout.render("--source", "compact", "--session-id", session_id)
        return outputs


class LayerTests(Base):
    def test_layer_replace_in_place_and_append(self):
        self.layout.rules_module()
        acme = self.layout.module("acme", {"rules/tests.md": ("## Tests\n\ncompany tests\n", "both"),
                                           "rules/extra.md": ("## Extra\n\nextra rule\n", "both")}, layer="company")
        found = self.layout.assemble()
        self.assertEqual(self.ids(found), ["state-file.state-file", "slots.slots", "tests.tests", "git.git",
                                           "plans.plans", "skills.skill-triggers", "extra.extra"])
        tests = found.sections[2]
        self.assertEqual((tests["layer"], tests["module"], tests["file"]), ("company", "acme", "rules/tests.md"))
        block = [b for b in found.blocks if b[2] == "tests.tests"][0]
        self.assertEqual(block[1], b"## Tests\n\ncompany tests\n")
        self.assertEqual(block[0], assemble.root_text(acme))
        self.assertEqual(found.notices, [])

    def test_empty_replacement_removes_section(self):
        self.layout.rules_module()
        self.layout.module("acme", {"rules/tests.md": ("## Tests\n\n", "both")}, layer="company")
        found = self.layout.assemble()
        self.assertNotIn("tests.tests", self.ids(found))
        self.assertNotIn(b"## Tests", self.text(found))
        self.assertIn(b"## Plans", self.text(found))

    def test_duplicate_in_one_layer_keeps_first_with_notice(self):
        self.layout.rules_module()
        self.layout.module("rigger-extra", {"rules/tests.md": ("## Tests\n\nextra tests\n", "both")})
        found = self.layout.assemble()
        self.assertEqual(found.notices, ["rigger: rule id tests.tests in rigger-extra and rigger-rules; kept rigger-extra"])
        self.assertIn(b"extra tests", self.text(found))
        self.assertNotIn(b"run the tests", self.text(found))


class PersonalTests(Base):
    def test_personal_override_notice(self):
        self.layout.rules_module()
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        found = self.layout.assemble()
        self.assertEqual(found.notices, ["rigger: rules.d/tests.md replaces rule tests.tests"])
        block = [b for b in found.blocks if b[2] == "tests.tests"][0]
        self.assertEqual(block[1], b"## Tests\n\nmy tests\n")
        self.assertEqual(block[0], assemble.root_text(self.layout.plugins / "rigger-rules"))
        self.assertEqual(self.ids(found)[2], "tests.tests")  # after state-file and slots, as without rules.d

    def test_personal_repeated_id_keeps_every_section(self):
        # I2: rules_extra.sh printed rules.d files whole; two headings with one slug both stay.
        self.layout.rules_module()
        self.layout.personal("notes.md", "## Testing - unit\n\nunit words\n## Testing - e2e\n\ne2e words\n")
        self.layout.personal("tests.md", "## Tests\n\nmine first\n## Tests\n\nmine second\n")
        found = self.layout.assemble()
        text = self.text(found)
        for words in (b"unit words", b"e2e words", b"mine first", b"mine second"):
            self.assertIn(words, text)
        self.assertLess(text.index(b"unit words"), text.index(b"e2e words"))
        self.assertEqual(found.notices, ["rigger: rules.d/tests.md replaces rule tests.tests"])
        ids = self.ids(found)
        self.assertEqual(ids[2], "tests.tests")  # the first occurrence replaces in place
        self.assertLess(ids.index("tests.tests"), ids.index("notes.testing"))
        self.assertNotIn(b"run the tests", text)

    def test_cannot_read_line_keeps_its_byte_order_place(self):
        # M3: rules_extra.sh printed the line where the file would have been.
        self.layout.rules_module()
        self.layout.personal("a.md", "## A\n\naaa\n")
        (self.layout.config / "rigger" / "rules.d" / "b.md").mkdir()
        self.layout.personal("c.md", "## C\n\nccc\n")
        out = self.layout.render()
        self.assertIn(b"## A\n\naaa\nrules_extra: cannot read b.md\n## C\n\nccc\n", out)

    def test_personal_text_is_not_rendered(self):
        self.layout.rules_module()
        self.layout.personal("mine.md", "## Mine\n\n{{slot:testing}} and {{reply_language}}\n")
        out = self.layout.render()
        self.assertIn(b"{{slot:testing}} and {{reply_language}}\n", out)
        self.assertIn(b"run the tests with `rigger-testing:testing`", out)  # control: module text is rendered

    def test_personal_override_inherits_audience(self):
        self.layout.rules_module()
        self.layout.personal("state-file.md", "## State file\n\nmy state file\n")
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        self.layout.personal("tests-main.md", "## Main only\n\nmain words\n")
        main = self.layout.render()
        sub = self.layout.render("--subagent")
        for words in (b"my state file", b"my tests", b"main words"):
            self.assertIn(words, main)
        self.assertNotIn(b"my state file", sub)
        self.assertIn(b"my tests", sub)
        self.assertNotIn(b"main words", sub)

    def test_personal_override_of_a_subagent_only_section_stays_out_of_the_session(self):
        self.layout.rules_module(**{"rules/sub.md": ("## Sub only\n\nmodule sub words\n", "subagent")})
        self.layout.personal("sub.md", "## Sub only\n\nmy sub words\n")
        for name, out in self.session_outputs("aud2").items():
            self.assertNotIn(b"my sub words", out, name)
            self.assertNotIn(b"module sub words", out, name)
            self.assertIn(b"plan rule", out, name)  # control: the session got the other sections
        sub = self.layout.render("--subagent")
        self.assertIn(b"my sub words", sub)  # the replacement reaches subagents, in place of the module text
        self.assertNotIn(b"module sub words", sub)

    def test_personal_layer_needs_rigger_rules(self):
        self.layout.personal("a.md", "## A\n\npersonal words\n")
        self.assertNotIn(b"personal words", self.layout.render())  # rigger-rules not discovered
        old = self.layout.plugins / "old-rules"
        write_json(old / ".claude-plugin" / "plugin.json", {"name": "rigger-rules"})
        write_json(self.layout.config / "settings.json", {"enabledPlugins": {"rigger-rules@m": True}})
        write_json(self.layout.config / "plugins" / "installed_plugins.json",
                   {"version": 2, "plugins": {"rigger-rules@m": [{"scope": "user", "installPath": str(old)}]}})
        found = self.layout.assemble()
        self.assertIn("rigger-rules", found.discovery.enabled)  # enabled, but without a manifest
        self.assertNotIn(b"personal words", self.text(found))
        self.layout.rules_module()
        self.assertIn(b"personal words", self.layout.render())  # control

    def test_personal_cap_and_left_out_line(self):
        self.layout.rules_module()
        self.layout.personal("a.md", "## A\n" + "a" * 4994 + "\n")
        self.layout.personal("b.md", "## B\n" + "b" * 4994 + "\n")
        self.layout.personal("c.md", "## C\n" + "c" * 4 + "\n")
        out = self.layout.render()
        self.assertIn(b"a" * 4994 + b"\n", out)
        self.assertNotIn(b"## B", out)
        self.assertNotIn(b"## C", out)
        self.assertIn(b"rules_extra: left out over the hook cap: b.md, c.md\n", out)

    def test_personal_budget_counts_escaped_bytes_in_subagent_mode(self):
        self.layout.rules_module()
        self.layout.personal("q.md", "## Q\n" + '"' * 3997 + "x" * 3997 + "\n")
        self.assertIn(b'"' * 3997, self.layout.render())
        sub = self.layout.render("--subagent")
        self.assertNotIn(b'"' * 3997, sub)
        self.assertIn(b"rules_extra: left out over the hook cap: q.md\n", sub)

    def test_personal_files_print_in_byte_order_and_main_files_stay_in_the_session(self):
        # Moved from rigger-rules' test_rules_extra.py: what rules_extra.sh printed is what the session gets.
        self.layout.rules_module()
        for name, text in (("b.md", "b\n"), ("a.md", "a\n"), ("Z.md", "Z\n"), ("10.md", "ten\n"), ("9.md", "nine\n"),
                           ("note.txt", "not markdown\n"), ("c.md.bak", "backup\n"), ("a-main.md", "a main\n"),
                           ("main.md", "plain main\n"), ("my rules.md", "spaced\n"), ("a b-main.md", "spaced main\n")):
            self.layout.personal(name, text)
        self.assertTrue(self.layout.render().endswith(
            b"ten\nnine\nZ\nspaced main\na main\na\nb\nplain main\nspaced\n"))
        self.assertTrue(self.layout.render("--subagent").endswith(b"ten\nnine\nZ\na\nb\nplain main\nspaced\n"))

    def test_personal_filter(self):
        self.layout.rules_module()
        self.layout.personal("f.md", b"## F\r\nx\x01y\tz\r\n\n\n")
        self.layout.personal("empty.md", b"")
        self.layout.personal("blank.md", b"\n\n")
        (self.layout.config / "rigger" / "rules.d" / "x.md").mkdir()
        out = self.layout.render()
        self.assertIn(b"## F\nxy\tz\n", out)
        self.assertNotIn(b"## F\nxy\tz\n\n", out)
        self.assertIn(b"rules_extra: cannot read x.md\n", out)
        self.assertNotIn(b"empty.md", out)
        self.assertNotIn(b"blank.md", out)


class SelectionTests(Base):
    def test_requires_unmet_drops_file_and_row(self):
        rows = [{"id": "rigger-rules.other-os", "before": "other os row", "load": ["x:y"],
                 "section": "skills.skill-triggers", "requires": ["os:" + OTHER_OS]},
                {"id": "rigger-rules.with-slot", "before": "slot row", "load": ["x:y"],
                 "section": "skills.skill-triggers", "requires": ["slot:testing"]}]
        self.layout.plain_plugin("x")  # the rows load x:y, which D39 keeps only while x is enabled
        self.layout.rules_module(**{
            "rules/os.md": ("## Other OS\n\nother os words\n", "both", ["os:" + OTHER_OS]),
            "rules/plugin.md": ("## Plugin\n\nplugin words\n", "both", ["plugin:not-enabled"]),
            "rules/slot.md": ("## Slot\n\nslot words\n", "both", ["slot:testing"]),
            "rules/noslot.md": ("## No slot\n\nno slot words\n", "both", ["slot:nope"]),
        })
        manifest = self.layout.plugins / "rigger-rules" / "rigger-module.json"
        data = json.loads(manifest.read_bytes())
        data["triggers"] = rows
        write_json(manifest, data)
        found = self.layout.assemble()
        ids = self.ids(found)
        for gone in ("os.other-os", "plugin.plugin", "noslot.no-slot"):
            self.assertNotIn(gone, ids)
        self.assertIn("slot.slot", ids)
        text = self.text(found)
        self.assertNotIn(b"other os words", text)
        self.assertNotIn(b"other os row", text)
        self.assertIn(b"| slot row |", text)
        self.assertNotIn("rigger-rules.other-os", [row.id for row in found.rows])

    def test_drop_by_basename_heading_and_id(self):
        self.layout.rules_module()
        found = self.layout.assemble(HARNESS_RULES_DROP="tests.md;## Plans; git.git ")
        self.assertEqual(self.ids(found), ["state-file.state-file", "slots.slots", "skills.skill-triggers"])
        kept = self.layout.assemble(HARNESS_RULES_DROP="## Git ({{skill:x:y}})")
        self.assertIn("git.git", self.ids(kept))
        dropped = self.layout.assemble(HARNESS_RULES_DROP="## Git (`x:y`)")
        self.assertNotIn("git.git", self.ids(dropped))
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        for drop in ("tests.md", "## Tests", "tests.tests"):
            self.assertIn(b"my tests", self.text(self.layout.assemble(HARNESS_RULES_DROP=drop)), drop)

    def test_subagent_only_section_stays_out_of_the_session(self):
        self.layout.rules_module(**{"rules/sub.md": ("## Sub only\n\nsubagent words\n", "subagent")})
        for name, out in self.session_outputs("aud1").items():
            self.assertNotIn(b"subagent words", out, name)
            self.assertIn(b"plan rule", out, name)  # control: the session got the other sections
        stdin = self.layout.hook_input(session_id="aud1", hook_event_name="SubagentStart",
                                       agent_type="rigger-core:worker")
        self.assertIn(b"subagent words", self.layout.part(1, subagent=True, stdin=stdin))  # from the snapshot
        self.assertIn(b"subagent words", self.layout.render("--subagent"))

    def test_subagent_audience_and_skip(self):
        self.layout.rules_module()
        manifest = self.layout.plugins / "rigger-rules" / "rigger-module.json"
        data = json.loads(manifest.read_bytes())
        data["subagents"] = {"x:y": {"rules": "none"}}
        write_json(manifest, data)
        skipped = ("Explore", "Plan", "rigger-core:implementer", "rigger-core:mapper", "x:y")
        for agent in skipped:
            stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type=agent)
            self.assertEqual(self.layout.part(1, subagent=True, stdin=stdin), b"", agent)
        for agent in ("rigger-core:worker", "rigger-core:../agents/implementer", "other:agent"):
            stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type=agent)
            self.assertIn(b"run the tests", self.layout.part(1, subagent=True, stdin=stdin), agent)
        self.assertIn(b"run the tests", self.layout.part(1, subagent=True, stdin=b"{not json"))
        sub = self.layout.render("--subagent")
        self.assertNotIn(b"keep a state file", sub)  # main-only section
        self.assertIn(b"keep a state file", self.layout.render())

    def test_render_from_the_snapshot_keeps_the_agent_skip(self):
        # RV3 F5: --render --subagent with a live session id shows what that agent's hook prints.
        self.layout.rules_module()
        startup = self.layout.hook_input(session_id="r5", source="startup")
        self.layout.part(1, stdin=startup)
        self.assertTrue((self.layout.config / "rigger" / "state" / "r5.resolved.json").is_file())
        for agent in ("Explore", "rigger-core:implementer"):
            self.assertEqual(self.layout.render("--subagent", "--agent-type", agent, "--session-id", "r5"), b"", agent)
        self.assertIn(b"run the tests",
                      self.layout.render("--subagent", "--agent-type", "rigger-core:worker", "--session-id", "r5"))


class OutputTests(Base):
    def test_subagent_json_is_valid_and_decodes_to_text(self):
        self.layout.rules_module()
        stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type="rigger-core:worker")
        out = self.layout.part(1, subagent=True, stdin=stdin)
        self.assertTrue(out.startswith(b'{"hookSpecificOutput":{"hookEventName":"SubagentStart",'), out[:60])
        context = json.loads(out.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(context.encode("utf-8"), self.layout.render("--subagent"))
        self.assertTrue(out.endswith(b"}\n"))
        for index in (2, 3, 4):
            self.assertEqual(self.layout.part(index, subagent=True, stdin=stdin), b"")

    def test_resume_and_unknown_source_print_nothing(self):
        self.layout.rules_module()
        for source in ("resume", "weird", None):  # D23: only a missing key prints
            for index in (1, 2):
                self.assertEqual(self.layout.part(index, stdin=self.layout.hook_input(source=source)), b"", source)
        self.assertIn(b"run the tests", self.layout.part(1, stdin=self.layout.hook_input(source="clear")))

    def test_hook_input_probes(self):
        # Untrusted JSON on stdin: deep nesting, a lone surrogate, a NUL in the path. Rules print anyway.
        self.layout.rules_module()
        probes = [
            b"[" * 100000,
            b"[1,2]",  # a list, not an object
            self.layout.hook_input(session_id="a\udfff", source="startup"),
            self.layout.hook_input(cwd=str(self.layout.project) + "\x00x"),
        ]
        for stdin in probes:
            self.assertIn(b"run the tests", self.layout.part(1, stdin=stdin), stdin[:40])
        self.assertFalse((self.layout.config / "rigger" / "state").exists())
        # An unusable cwd falls back to CLAUDE_PROJECT_DIR (D10), whose project file then applies.
        other = self.tmp / "other"
        write_json(other / ".claude" / "rigger.json", {"schema": 1, "expects": ["nope"]})
        for cwd in (str(self.layout.project) + "\x00x", str(self.layout.project) + "\udfff"):
            out = self.layout.part(1, stdin=self.layout.hook_input(cwd=cwd), CLAUDE_PROJECT_DIR=str(other))
            self.assertIn(b"expects nope", out, repr(cwd))
            self.assertNotIn(b"ignored", out)

    def test_output_is_lf_bytes(self):
        self.layout.rules_module(**{"rules/crlf.md": ("## CRLF\r\nline one\r\nline two\r\n", "both")})
        out = self.layout.part(1)
        self.assertIn(b"## CRLF\nline one\nline two\n", out)
        for index in range(1, 7):
            self.assertNotIn(b"\r", self.layout.part(index))

    def test_part_cap_counts_escaped_bytes_near_the_limit(self):
        # I6: a module section sized to fill part 1 to just under 9,000 bytes, half of it `"`.
        small = "## Small\n\nsmall words\n"
        mid = "## Mid\n\n" + "z" * 9100 + "\n"  # subagents only: over 9,000 bytes, under 9,400 with its root line
        self.layout.module("mod-big", {"rules/big.md": ("## Big\n\nx\n", "both"), "rules/small.md": (small, "both"),
                                       "rules/mid.md": (mid, "subagent")})
        startup = self.layout.hook_input(source="startup")
        reserve = len(b"rigger: snapshot not written; compaction will read the rules again\n")  # M1
        room = 9000 - reserve - len(self.layout.part(1, stdin=startup)) + len(small) + len("x\n")
        size = room - 10  # part 1 then ends 10 bytes under the cap: too little for the small section
        filler = ('"' * (size // 2) + "y" * (size - size // 2 - 1) + "\n")
        write(self.layout.plugins / "mod-big" / "rules" / "big.md", "## Big\n\n" + filler)
        parts = [self.layout.part(i, stdin=startup) for i in range(1, 7)]
        self.assertIn(filler.encode(), parts[0])
        self.assertGreater(len(parts[0]), 8900)
        self.assertLessEqual(len(parts[0]), 9000)
        self.assertIn(b"small words", parts[1])  # the next section moves on to part 2
        sub_in = self.layout.hook_input(hook_event_name="SubagentStart", agent_type="rigger-core:worker")
        subs = [self.layout.part(i, subagent=True, stdin=sub_in) for i in range(1, 5)]
        contexts = [json.loads(s.decode("utf-8"))["hookSpecificOutput"]["additionalContext"] if s else "" for s in subs]
        for context in contexts:
            self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)[1:-1].encode("utf-8")), 9000)
        joined = "".join(contexts)
        self.assertNotIn(filler[:200], joined)  # escaped it is over a part: left out and named
        self.assertIn("rigger: rules left out over the hook cap: big.big, mid.mid", joined)
        self.assertIn("small words", joined)
        # M1: the late line of part 1 still leaves the body at or under 9,000 bytes.
        compact = self.layout.hook_input(session_id="m1", source="compact")
        late = self.layout.part(1, stdin=compact)
        self.assertTrue(late.startswith(b"rigger: snapshot missing; rules read again\n"))
        self.assertLessEqual(len(late), 9000)
        shutil.rmtree(str(self.layout.config / "rigger" / "state"))
        write(self.layout.config / "rigger" / "state", "not a folder")
        failing = self.layout.part(1, stdin=self.layout.hook_input(session_id="m1", source="startup"))
        self.assertTrue(failing.startswith(b"rigger: snapshot not written"))
        self.assertLessEqual(len(failing), 9000)

    def test_crash_prints_one_visible_line(self):
        original = assemble.assemble

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        assemble.assemble = boom
        try:
            for argv, expected, code in (
                (["--part", "1"], b"rigger: rules not assembled: RuntimeError: boom (run /rigger-core:doctor)\n", 0),
                (["--part", "2"], b"Rigger rules part 2 of 6 not loaded: RuntimeError: boom (run /rigger-core:doctor)\n", 0),
            ):
                out = io.BytesIO()
                self.assertEqual(assemble.main(argv, stdin=self.layout.hook_input(), stdout=out), code)
                self.assertEqual(out.getvalue(), expected)
            out = io.BytesIO()
            self.assertEqual(assemble.main(["--part", "1", "--subagent"], stdin=b"{}", stdout=out), 0)
            context = json.loads(out.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(context, "rigger: rules not assembled: RuntimeError: boom (run /rigger-core:doctor)\n")
        finally:
            assemble.assemble = original

    def test_a_crash_in_a_part_above_one_prints_its_line(self):
        original = assemble.assemble

        def crash(*args, **kwargs):
            raise ValueError("x")

        assemble.assemble = crash
        try:
            out = io.BytesIO()
            self.assertEqual(assemble.main(["--part", "3"], stdin=self.layout.hook_input(), stdout=out), 0)
            self.assertEqual(out.getvalue(), b"Rigger rules part 3 of 6 not loaded: ValueError: x (run /rigger-core:doctor)\n")
            out = io.BytesIO()
            self.assertEqual(assemble.main(["--part", "4", "--subagent"], stdin=b"{}", stdout=out), 0)
            self.assertTrue(out.getvalue().endswith(b"}\n"), out.getvalue())
            context = json.loads(out.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(context, "Rigger rules part 4 of 4 not loaded: ValueError: x (run /rigger-core:doctor)\n")
            out = io.BytesIO()  # no part number to name: an index outside 1..6 stays silent, as before
            self.assertEqual((assemble.main(["--part", "7"], stdin=self.layout.hook_input(), stdout=out), out.getvalue()),
                             (10, b""))
        finally:
            assemble.assemble = original

    def test_core_root_spelled_differently(self):
        # CLAUDE_PLUGIN_ROOT with a trailing separator (and the drive letter's case swapped on Windows)
        # is the same folder as the core RIGGER_MODULE_DIRS names: one core, no notice.
        spelled = str(self.layout.core) + os.sep
        if os.name == "nt":
            spelled = spelled[0].swapcase() + spelled[1:]
        self.layout.dirs.append(str(self.layout.core))
        self.layout.rules_module()
        found = self.layout.assemble(self_root=Path(spelled), CLAUDE_PLUGIN_ROOT=spelled)
        self.assertEqual(found.notices, [])
        self.assertEqual([m.name for m in found.discovery.modules].count("rigger-core"), 1)


class SnapshotTests(Base):
    def test_compact_reads_snapshot(self):
        rules = self.layout.rules_module()
        startup = self.layout.hook_input(session_id="s1", source="startup")
        compact = self.layout.hook_input(session_id="s1", source="compact")
        first = [self.layout.part(i, stdin=startup) for i in range(1, 7)]
        snapshot = self.layout.config / "rigger" / "state" / "s1.resolved.json"
        self.assertTrue(snapshot.is_file())
        write(rules / "rules" / "plans.md", "## Plans\n\nnew plan rule\n")
        self.assertEqual([self.layout.part(i, stdin=compact) for i in range(1, 7)], first)
        snapshot.unlink()
        self.assertNotIn(b"snapshot missing", self.layout.part(2, stdin=compact))  # late lines open part 1 only
        self.assertFalse(snapshot.exists())
        again = self.layout.part(1, stdin=compact)
        self.assertTrue(again.startswith(b"rigger: snapshot missing; rules read again\n"))
        self.assertIn(b"new plan rule", again)
        self.assertEqual(again.count(b"snapshot missing"), 1)
        self.assertTrue(snapshot.is_file())
        self.assertNotIn(b"snapshot missing", self.layout.part(2, stdin=compact))
        write(rules / "rules" / "plans.md", "## Plans\n\nnewest plan rule\n")
        sub = self.layout.part(1, subagent=True, stdin=self.layout.hook_input(
            session_id="s1", hook_event_name="SubagentStart", agent_type="rigger-core:worker"))
        context = json.loads(sub.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("new plan rule", context)
        self.assertNotIn("newest", context)
        for agent in ("Explore", "rigger-core:implementer"):  # the skip list holds when the snapshot is read
            stdin = self.layout.hook_input(session_id="s1", hook_event_name="SubagentStart", agent_type=agent)
            self.assertEqual(self.layout.part(1, subagent=True, stdin=stdin), b"", agent)
        self.assertNotIn("snapshot missing", self.layout.part(1, stdin=compact).decode("utf-8"))

    def test_compact_reads_snapshot_with_non_utf8_bytes(self):
        # I1: rules_extra.sh printed raw bytes; a cp1250 rules.d file or a Latin-1 module byte must
        # still give a snapshot, and compaction prints the session's bytes.
        rows = [
            ("cp1250 rules.d", lambda: self.layout.personal("mine.md", b"## Mine\n\n\xbf\xf3\xb3w\n"), b"\xbf\xf3\xb3w\n"),
            ("latin-1 module file", lambda: write(self.layout.plugins / "rigger-rules" / "rules" / "plans.md",
                                                  b"## Plans\n\ncaf\xe9 rule\n"), b"caf\xe9 rule\n"),
        ]
        for name, seed, marker in rows:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.layout.rules_module()
                seed()
                startup = self.layout.hook_input(session_id="s9", source="startup")
                first = [self.layout.part(i, stdin=startup) for i in range(1, 7)]
                self.assertNotIn(b"snapshot not written", first[0])
                self.assertIn(marker, b"".join(first))
                self.assertTrue((self.layout.config / "rigger" / "state" / "s9.resolved.json").is_file())
                compact = self.layout.hook_input(session_id="s9", source="compact")
                self.assertEqual([self.layout.part(i, stdin=compact) for i in range(1, 7)], first)

    def test_subagent_without_parent_snapshot_recomputes(self):
        self.layout.rules_module()
        sub = self.layout.part(1, subagent=True, stdin=self.layout.hook_input(
            session_id="nosnap", hook_event_name="SubagentStart", agent_type="rigger-core:worker"))
        self.assertIn(b"run the tests", sub)
        self.assertNotIn(b"snapshot missing", sub)

    def test_snapshot_write_failure_gives_one_notice(self):
        self.layout.rules_module()
        write(self.layout.config / "rigger" / "state", "not a folder")
        startup = self.layout.hook_input(session_id="s2", source="startup")
        first = self.layout.part(1, stdin=startup)
        self.assertEqual(first.count(b"rigger: snapshot not written; compaction will read the rules again\n"), 1)
        self.assertIn(b"run the tests", first)
        self.assertNotIn(b"snapshot not written", self.layout.part(2, stdin=startup))
        unsafe = self.layout.hook_input(session_id="../x", source="startup")
        self.assertNotIn(b"snapshot not written", self.layout.part(1, stdin=unsafe))
        # A compaction that cannot rewrite the snapshot says only that it was missing (one line).
        compact = self.layout.part(1, stdin=self.layout.hook_input(session_id="s2", source="compact"))
        self.assertTrue(compact.startswith(b"rigger: snapshot missing; rules read again\n"))
        self.assertNotIn(b"snapshot not written", compact)

    def test_startup_deletes_snapshots_older_than_14_days(self):
        self.layout.rules_module()
        state = self.layout.config / "rigger" / "state"
        old, recent = state / "old.resolved.json", state / "recent.resolved.json"
        for path, days in ((old, 15), (recent, 13)):
            write(path, "{}")
            stamp = time.time() - days * 86400
            os.utime(str(path), (stamp, stamp))
        self.layout.part(1, stdin=self.layout.hook_input(session_id="s3", source="startup"))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_unsafe_session_id_writes_nothing(self):
        self.layout.rules_module()
        for session in ("", "../x", "a/b", "x" * 129):
            out = self.layout.part(1, stdin=self.layout.hook_input(session_id=session, source="startup"))
            self.assertIn(b"run the tests", out)
        self.assertFalse((self.layout.config / "rigger" / "state").exists())


class FullLayoutTests(unittest.TestCase):
    """All 11 repository modules plus a full rules.d."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)
        self.layout.core = REPO_CORE
        self.layout.dirs = [str(p.parent) for p in sorted((REPO / "plugins").glob("*/rigger-module.json"))
                            if p.parent.name != "rigger-core"]
        for name in ("a.md", "b.md", "c.md"):
            self.layout.personal(name, "## " + name + "\n" + ("w " * 1490).strip() + "\n")

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_full_repo_layout_fits_with_full_rules_d(self):
        self.assertEqual(len(self.layout.dirs), 10)
        found = self.layout.assemble()
        self.assertNotIn(D75_LINE, found.notices)
        self.assertEqual(found.notices, [])
        session = [self.layout.part(i) for i in range(1, 7)]
        stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type="general-purpose")
        sub = [self.layout.part(i, subagent=True, stdin=stdin) for i in range(1, 5)]
        joined = b"".join(session + sub)
        self.assertNotIn(b"rules left out over the hook cap", joined)
        self.assertNotIn(b"rules_extra: left out", b"".join(session))
        self.assertIn(b"## a.md", b"".join(session))

    def test_slots_section_for_the_default_layout_is_under_1200_bytes(self):
        # D50: ## Slots has its own cap of 1,200 bytes for the default set (11 rows on every system).
        block = [b for b in self.layout.assemble().blocks if b[2] == "slots.slots"]
        self.assertEqual(len(block), 1)
        rows = [line for line in block[0][1].splitlines() if line.startswith(b"| ") and not line.startswith(b"| Slot ")]
        self.assertEqual(len(rows), 11)
        self.assertLessEqual(len(block[0][1]), 1200)

    def test_emitted_part_stays_under_the_hook_cap(self):
        stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type="general-purpose")
        outputs = [self.layout.part(i) for i in range(1, 7)]
        outputs += [self.layout.part(i, subagent=True, stdin=stdin) for i in range(1, 5)]
        self.assertTrue(any(outputs))
        for out in outputs:
            self.assertLess(len(out), 9500)

    def test_parts_concatenate_to_render(self):
        bodies = [self.layout.part(i) for i in range(1, 7)]
        for body in bodies:
            self.assertLessEqual(len(body), 9000)
        self.assertGreater(sum(1 for body in bodies if body), 2)
        self.assertEqual(without_root_lines(b"".join(bodies)), without_root_lines(self.layout.render()))


class ConfigTests(Base):
    def test_a_config_row_of_any_section_goes_into_the_one_table(self):
        # D38: `section` is ignored, so a row naming a table nobody prints still shows, with no notice.
        self.layout.plain_plugin("x")
        path = self.layout.config / "rigger" / "config.json"
        write_json(path, {"schema": 1, "triggers": {
            "p.other": {"before": "other section row", "load": ["x:y"], "section": "nope.table"},
            "p.kept": {"before": "kept row", "load": ["x:y"]}}})
        self.layout.rules_module()
        found = self.layout.assemble()
        self.assertEqual(found.notices, [])
        self.assertIn(b"| other section row | `x:y` |\n", self.text(found))
        self.assertIn(b"| kept row | `x:y` |\n", self.text(found))

    def test_clean_layout_has_no_notices(self):
        self.layout.rules_module()
        self.assertEqual(self.layout.assemble().notices, [])

    def test_expects_notice(self):
        self.layout.module("rigger-wp", {"rules/wp.md": ("## WP\n\nwp words\n", "both")})
        write_json(self.layout.project / ".claude" / "rigger.json", {"schema": 1, "expects": ["rigger-wp", "nope"]})
        notices = self.layout.assemble().notices
        self.assertEqual(len(notices), 1)
        self.assertIn("nope", notices[0])
        self.assertIn("claude plugin install nope", notices[0])
        self.assertNotIn("rigger-wp", notices[0])

    def test_a_flood_of_notices_is_cut_and_the_rules_still_print(self):
        # RV2 M4: 80 broken project rows give over 9,000 bytes of notices; they must not take every notice out.
        self.layout.rules_module()
        rows = {"p.row%02d" % n: {"before": "a\nb", "load": ["x:y"]} for n in range(80)}
        write_json(self.layout.project / ".claude" / "rigger.json", {"schema": 1, "triggers": rows})
        self.assertEqual(len(self.layout.assemble().notices), 80)
        parts = [self.layout.part(i) for i in range(1, 7)]
        joined = b"".join(parts)
        shown = [line for line in joined.splitlines() if line.startswith(b"rigger: config ")]
        self.assertGreater(len(shown), 0)
        self.assertLessEqual(sum(len(line) + 1 for line in shown), 2000)
        self.assertTrue(parts[0].startswith(shown[0] + b"\n"))
        self.assertIn(b"rigger: %d more notices not shown; assemble.py --render prints them all\n"
                      % (80 - len(shown)), parts[0])
        self.assertNotIn(b"_notices", joined)
        self.assertIn(b"run the tests", joined)
        self.assertEqual(self.layout.render().count(b"rigger: config "), 80)

    def test_a_first_block_too_big_for_any_part_leaves_the_rest_in_part_one(self):
        # RV2b N1: part 1's reserved room for a late line must not count as content.
        self.layout.rules_module()
        write(self.layout.core / "rules" / "state-file.md", "## State file\n\n" + "s" * 9500 + "\n")
        parts = [self.layout.part(i) for i in range(1, 7)]
        self.assertIn(b"run the tests", parts[0])
        self.assertTrue(b"".join(parts).endswith(b"rigger: rules left out over the hook cap: state-file.state-file\n"))

    def test_notices_open_part_one_only(self):
        self.layout.rules_module()
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        first = self.layout.part(1)
        self.assertTrue(first.startswith(b"rigger: rules.d/tests.md replaces rule tests.tests\n"))


def sh_quoted(text):
    return "'" + text.replace("'", "'\\''") + "'"


def command_line(core, session):
    """The one line under ## Slots when a slot is a command (D36, spike Q3: the Bash tool runs it)."""
    root = Path(str(core)).as_posix()
    return ("Run a command slot with the Bash tool: `sh %s %s <slot> --session %s --base <base revision>`"
            % (sh_quoted(root + "/bin/py.sh"), sh_quoted(root + "/tools/slot-run/slot_run.py"), session))


def shown_argv(argv):
    """D53's form, written out here from its definition: ASCII JSON cut to 200, its size and 12 hex of sha256."""
    import hashlib
    line = json.dumps(argv, ensure_ascii=True)
    return "%s … (%d bytes, sha256:%s)" % (line[:200], len(line), hashlib.sha256(line.encode("ascii")).hexdigest()[:12])


class SlotWiringTests(Base):
    """The assembler resolves slots (Task 15): ## Slots, its notices, the trust filter and the merged table."""

    def config_file(self):
        return self.layout.config / "rigger" / "config.json"

    def project_file(self):
        return self.layout.project / ".claude" / "rigger.json"

    def startup(self, session, **fields):
        return self.layout.hook_input(session_id=session, source="startup", **fields)

    def compact(self, session):
        return self.layout.hook_input(session_id=session, source="compact")

    def section_text(self, found, section_id):
        return b"".join(block[1] for block in found.blocks if block[2] == section_id)

    def test_slots_section_closes_core_and_is_main_only(self):
        self.layout.manifest_set(self.layout.core, rules=[{"file": "rules/state-file.md", "audience": "main"},
                                                          {"file": "rules/second.md", "audience": "both"}])
        write(self.layout.core / "rules" / "second.md", "## Second\n\nsecond core words\n")
        self.layout.rules_module()
        found = self.layout.assemble()
        self.assertEqual(self.ids(found)[:4], ["state-file.state-file", "second.second", "slots.slots", "tests.tests"])
        block = [b for b in found.blocks if b[2] == "slots.slots"][0]
        self.assertEqual(block[0], assemble.root_text(self.layout.core))
        self.assertTrue(block[1].startswith(b"## Slots\n\n| Slot | Kind | Target | Model | From |\n"), block[1][:80])
        self.assertIn(b"| worker | agent | rigger-core:worker | by-rule | default |\n", block[1])
        sub = self.layout.assemble("subagent")
        self.assertNotIn("slots.slots", self.ids(sub))
        self.assertIn("second.second", self.ids(sub))  # control: core's own rules reach the subagent
        self.assertNotIn(b"## Slots", self.layout.render("--subagent"))

    def test_a_personal_section_cannot_replace_slots(self):
        self.layout.rules_module()
        self.layout.personal("slots.md", "## Slots\n\nmy own slots\n")
        found = self.layout.assemble()
        self.assertIn("rigger: rule id slots.slots in rigger-core and rules.d/slots.md; kept rigger-core",
                      found.notices)
        self.assertNotIn("rigger: rules.d/slots.md replaces rule slots.slots", found.notices)
        self.assertNotIn(b"my own slots", self.text(found))
        self.assertIn(b"| Slot | Kind | Target | Model | From |", self.section_text(found, "slots.slots"))

    def test_drop_by_heading_drops_slots(self):
        self.layout.rules_module()
        self.assertIn("slots.slots", self.ids(self.layout.assemble()))  # control
        for drop in ("## Slots", "slots.slots"):
            found = self.layout.assemble(HARNESS_RULES_DROP=drop)
            self.assertNotIn("slots.slots", self.ids(found), drop)
            self.assertNotIn(b"| Slot | Kind |", self.text(found), drop)

    def test_default_install_without_superpowers_is_quiet(self):
        # Review Focus 5: no notice per empty process slot, no superpowers skill named, the four rows empty.
        self.layout.rules_module(**{"rules/tdd.md": ("## TDD only\n\ntdd words\n", "both", ["slot:process.tdd"])})
        self.layout.manifest_set(self.layout.plugins / "rigger-rules", triggers=[
            {"id": "rigger-rules.tdd-row", "before": "tdd row", "load": ["rigger-core:git-workflow"],
             "requires": ["slot:process.tdd"]}])
        found = self.layout.assemble()
        self.assertEqual(found.notices, [])
        text = self.text(found)
        self.assertIn(b"| writing code or tests, reviewing tests, a spike, port or rehearsal | `rigger-testing:testing` |\n",
                      text)
        self.assertNotIn(b"superpowers:", text)
        self.assertNotIn(b"new behaviour, a spec or a plan", text)  # its every entry is a superpowers skill
        self.assertNotIn(b"tdd words", text)  # `requires: slot:process.tdd` reads the resolved, empty slot
        self.assertNotIn(b"| tdd row |", text)
        self.assertIn(b"plan rule", text)  # control: the other rigger-rules sections print
        slots_text = self.section_text(found, "slots.slots")
        for path in ("process.brainstorm", "process.plans", "process.tdd", "process.debug"):
            self.assertIn(("| %s | — | — | — | default |\n" % path).encode("utf-8"), slots_text, path)

    def test_a_second_table_prints_nothing_with_a_notice(self):
        # D38: the first {{triggers:…}} of the assembly holds every row; a later one, in another module, prints none.
        self.layout.rules_module()
        # Modules outside a marketplace come in name order (D9): rigger-zz is read after rigger-rules.
        self.layout.module("rigger-zz", {"rules/more.md": ("## More\n\nmore words\n{{triggers:other.table}}\n", "both")})
        found = self.layout.assemble()
        self.assertEqual(found.notices, ["rigger: second trigger table in rigger-zz/rules/more.md not shown; the first "
                                         "one holds every row"])
        self.assertEqual(self.section_text(found, "more.more"), b"## More\n\nmore words\n")
        self.assertEqual(self.text(found).count(b"| Before | Skill |"), 1)

    def test_slot_cells_are_printed_as_built(self):
        # The table is generated: a model text that looks like a placeholder stays as written.
        write_json(self.config_file(), {"schema": 1, "slots": {"worker": {"model": "{{skill:a:b}}"}}})
        self.layout.rules_module()
        self.assertIn(b"| worker | agent | rigger-core:worker | {{skill:a:b}} | default |\n",
                      self.section_text(self.layout.assemble(), "slots.slots"))

    def test_a_personal_slot_changes_the_row_the_cell_and_the_placeholder(self):
        self.layout.plain_plugin("sp")
        write_json(self.config_file(), {"schema": 1, "slots": {"testing": {"target": "sp:my-testing"}}})
        self.layout.rules_module()
        found = self.layout.assemble()
        self.assertEqual(found.notices, [])
        self.assertIn(b"| testing | skill | sp:my-testing | \xe2\x80\x94 | personal |\n",
                      self.section_text(found, "slots.slots"))
        text = self.text(found)
        self.assertIn(b"| writing code or tests, reviewing tests, a spike, port or rehearsal | `sp:my-testing` |\n", text)
        self.assertIn(b"run the tests with `sp:my-testing`\n", text)
        self.assertNotIn(b"rigger-testing:testing", text)

    def test_an_entry_that_fails_falls_back_in_the_row_the_cell_and_the_placeholder(self):
        # D34: the fallback, not the plain merge, is what every reader of a slot sees.
        write_json(self.project_file(), {"schema": 1, "slots": {"testing": {"target": "nope:x"}}})
        self.layout.rules_module()
        found = self.layout.assemble()
        self.assertEqual(found.notices, ["rigger: slot testing from %s ignored: plugin nope is not enabled; using default"
                                         % self.project_file()])
        text = self.text(found)
        self.assertIn(b"run the tests with `rigger-testing:testing`\n", text)
        self.assertIn(b"| writing code or tests, reviewing tests, a spike, port or rehearsal | `rigger-testing:testing` |\n", text)
        self.assertIn(b"| testing | skill | rigger-testing:testing | \xe2\x80\x94 | default |\n",
                      self.section_text(found, "slots.slots"))
        self.assertNotIn(b"nope:x", text)

    def test_project_command_without_trust_prints_the_line_and_keeps_the_agent(self):
        self.layout.rules_module(**{"rules/review.md": ("## Review\n\nreview with {{slot:reviewer}}\n", "both")})
        write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                             "target": ["rv", "{out}"]}}})
        first = self.layout.part(1, stdin=self.startup("pc1"))
        notice = ("rigger: .claude/rigger.json may not set a command for slot reviewer; add it to "
                  "trust.project_commands in %s to allow it\n" % self.config_file()).encode("utf-8")
        self.assertTrue(first.startswith(notice), first[:300])
        self.assertIn(b"| reviewer | agent | rigger-review:reviewer | by-rule | default |\n", first)
        self.assertIn(b"review with `rigger-review:reviewer`\n", first)
        self.assertNotIn(b"runs the project's command", first)
        self.assertNotIn(b"Run a command slot", first)

    def test_trusted_project_command_prints_its_argv(self):
        # D53: the notice at startup and again after compact; another argv gives another text.
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "trust": {"project_commands": ["reviewer"]}})
        texts = []
        for session, argv in (("tc1", ["rv", "{out}"]), ("tc2", ["rv", "--deep", "{out}"])):
            write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command", "target": argv}}})
            notice = ("rigger: slot reviewer runs the project's command %s (trusted in %s)\n"
                      % (shown_argv(argv), self.config_file())).encode("utf-8")
            started = self.layout.part(1, stdin=self.startup(session))
            self.assertIn(notice, started)
            self.assertIn(b"| reviewer | command | rv \xe2\x80\xa6 | by-rule | project |\n", started)
            self.assertIn(command_line(self.layout.core, session).encode("utf-8") + b"\n", started)
            write_json(self.project_file(), {"schema": 1})  # the compaction must print the session's text
            self.assertIn(notice, self.layout.part(1, stdin=self.compact(session)))
            texts.append(notice)
        self.assertNotEqual(texts[0], texts[1])
        self.assertIn(b'["rv", "{out}"] \xe2\x80\xa6 (15 bytes, sha256:', texts[0])

    def test_a_flood_of_project_notices_cannot_hide_the_slot_lines(self):
        # Notices print up to 2,000 bytes; a project file's broken rows must not push its command line out.
        deep = self.tmp / ("deep-" + "d" * 80)  # the notices name the config file: the same size on every system
        deep.mkdir()
        self.layout = Layout(deep)
        self.layout.rules_module()
        rows = {"p.row%02d" % n: {"before": "a\nb", "load": ["x:y"]} for n in range(80)}
        write_json(self.config_file(), {"schema": 1, "trust": {"project_commands": ["reviewer"]}})
        commands = {"reviewer": {"kind": "command", "target": ["rv", "{out}"]},
                    "worker": {"kind": "command", "target": ["w", "{out}"]}}
        write_json(self.project_file(), {"schema": 1, "triggers": rows, "slots": commands})
        first = self.layout.part(1, stdin=self.startup("fl1"))
        self.assertIn(b"more notices not shown", first)  # the precondition: the notices overflow their cap
        self.assertIn(b"rigger: slot reviewer runs the project's command ", first)
        self.assertIn(b"rigger: .claude/rigger.json may not set a command for slot worker;", first)
        # Every other slot as an untrusted command: their lines alone fill the cap; the running command stays first.
        others = [path for path in ("worker", "implementer", "mapper", "testing", "lessons")]
        commands = dict(commands, **{path: {"kind": "command", "target": ["w", "{out}"]} for path in others},
                        process={role: {"kind": "command", "target": ["w", "{out}"]}
                                 for role in ("brainstorm", "plans", "tdd", "debug")})
        commands["reviewer"] = {"kind": "command", "target": ["rv" + "r" * 300, "{out}"]}
        write_json(self.project_file(), {"schema": 1, "slots": commands})
        found = self.layout.assemble()
        self.assertGreater(sum(len(line.encode("utf-8")) + 1 for line in found.notices), 2000)
        first = self.layout.part(1, stdin=self.startup("fl2"))
        self.assertTrue(first.startswith(b"rigger: slot reviewer runs the project's command "), first[:120])

    def test_project_command_notice_stays_inside_the_part_budget(self):
        # D53: the most validation allows, 32 arguments of 500 characters; part 1 keeps its rules and the notice.
        self.layout.rules_module()
        argv = ["p" * 500] + ["a" * 495 + "{out}"] + [chr(ord("b") + n % 20) * 500 for n in range(30)]
        write_json(self.config_file(), {"schema": 1, "trust": {"project_commands": ["reviewer"]}})
        write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command", "target": argv}}})
        found = self.layout.assemble()
        self.assertEqual(len([n for n in found.notices if "runs the project's command" in n]), 1)
        first = self.layout.part(1, stdin=self.startup("pb1"))
        self.assertLess(len(first), 9500)
        self.assertIn(b"rigger: slot reviewer runs the project's command " + shown_argv(argv).encode("utf-8"), first)
        self.assertIn(b"keep a state file", first)

    def test_two_fillers_line_at_startup_and_after_compact(self):
        # Review Focus 6: one line naming both, at startup and after compact; both names in the row.
        for name in ("mod-a", "mod-b"):
            root = self.layout.module(name, {"rules/%s.md" % name: ("## %s\n\n%s words\n" % (name, name), "both")})
            self.layout.manifest_set(root, fills="lessons")
        self.layout.rules_module()
        line = b"rigger: slot lessons is filled by mod-a and mod-b; disable one with claude plugin disable <name>\n"
        started = b"".join(self.layout.part(i, stdin=self.startup("tf1")) for i in range(1, 7))
        self.assertEqual(started.count(line), 1)
        self.assertIn(b"| lessons | hook | mod-a + mod-b (failure mode: undeclared) | \xe2\x80\x94 | default |\n",
                      started)
        compacted = b"".join(self.layout.part(i, stdin=self.compact("tf1")) for i in range(1, 7))
        self.assertEqual(compacted.count(line), 1)
        self.assertNotIn(b"snapshot missing", compacted)

    def test_default_slot_without_its_plugin_names_the_install_command(self):
        # Review Focus 1: rigger-core updated, the review and testing modules not installed.
        self.layout.dirs = [d for d in self.layout.dirs if d not in (str(self.layout.review), str(self.layout.testing))]
        self.layout.rules_module()
        lines = [b"rigger: slot reviewer is empty: install rigger-review (claude plugin install rigger-review@rigger)\n",
                 b"rigger: slot testing is empty: install rigger-testing (claude plugin install rigger-testing@rigger); "
                 b"the commit-time weak-test guard is off\n"]
        for source in ("startup", "clear", "compact"):
            with self.subTest(source=source):
                stdin = self.layout.hook_input(session_id="ih1", source=source)
                text = b"".join(self.layout.part(i, stdin=stdin) for i in range(1, 7))
                for line in lines:
                    self.assertEqual(text.count(line), 1, (source, line))
                self.assertIn(b"| reviewer | \xe2\x80\x94 | \xe2\x80\x94 | \xe2\x80\x94 | default |\n", text)
                self.assertNotIn(b"snapshot missing", text)  # compact printed the session's snapshot

    def test_format_one_snapshot_is_read_again(self):
        # Review Focus 8: a snapshot of release 0.5.0 (format 1) is read as missing, and replaced.
        self.layout.rules_module()
        self.layout.part(1, stdin=self.startup("f1"))
        path = self.layout.config / "rigger" / "state" / "f1.resolved.json"
        data = json.loads(path.read_bytes())
        self.assertEqual(data["format"], 2)
        data["format"] = 1
        write_json(path, data)
        again = self.layout.part(1, stdin=self.compact("f1"))
        self.assertTrue(again.startswith(b"rigger: snapshot missing; rules read again\n"), again[:80])
        self.assertEqual(json.loads(path.read_bytes())["format"], 2)
        self.assertNotIn(b"snapshot missing", self.layout.part(1, stdin=self.compact("f1")))

    def test_snapshot_holds_the_resolved_slot_table(self):
        # D44: the snapshot's slots are the filtered rows and the trusted list, never the plain merge.
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "trust": {"project_commands": ["process.tdd"]}})
        write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                             "target": ["rv", "{out}"]}}})
        self.layout.part(1, stdin=self.startup("sn1"))
        data = json.loads((self.layout.config / "rigger" / "state" / "sn1.resolved.json").read_bytes())
        self.assertEqual(sorted(data["slots"]), ["rows", "trusted"])
        self.assertEqual(data["slots"]["trusted"], ["process.tdd"])
        reviewer = [row for row in data["slots"]["rows"] if row["path"] == "reviewer"][0]
        self.assertEqual((reviewer["kind"], reviewer["target"], reviewer["source"]),
                         ("agent", "rigger-review:reviewer", "default"))
        import slots  # the reader slot_run.py uses gives back the assembly's own rows
        self.assertEqual(slots.from_snapshot(data["slots"]), (self.layout.assemble().slot_rows,
                                                              frozenset(["process.tdd"])))

    def test_a_personal_null_on_a_filled_hook_slot_reads_back_from_the_snapshot(self):
        # The module's hook still runs after a null: the row stays filled with no expected filler, and the
        # snapshot slot_run.py reads must accept it.
        root = self.layout.module("mod-a", {"rules/a.md": ("## A\n\na words\n", "both")})
        self.layout.manifest_set(root, fills="lessons")
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "slots": {"lessons": None}})
        started = self.layout.part(1, stdin=self.startup("hn1"))
        self.assertIn(b"| lessons | hook | mod-a (failure mode: undeclared) | \xe2\x80\x94 | personal |\n", started)
        data = json.loads((self.layout.config / "rigger" / "state" / "hn1.resolved.json").read_bytes())
        import slots
        back = slots.from_snapshot(data["slots"])
        self.assertIsNotNone(back)
        lessons = [r for r in back[0] if r.path == "lessons"][0]
        self.assertEqual((lessons.kind, lessons.target, lessons.fillers, lessons.filled, lessons.source),
                         ("hook", None, ("mod-a",), True, "personal"))
        self.assertEqual(back[0], self.layout.assemble().slot_rows)

    def test_an_untrusted_project_command_is_nowhere_in_the_snapshot(self):
        # Fail closed: what the trust filter dropped is not stored, in the slot table or in config.effective.
        self.layout.rules_module()
        marker = "UNTRUSTEDMARK"
        write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {
            "kind": "command", "target": ["rv-" + marker, "{out}"], "model": "model-" + marker}}})
        self.layout.part(1, stdin=self.startup("ut1"))
        path = self.layout.config / "rigger" / "state" / "ut1.resolved.json"
        raw = path.read_bytes()
        self.assertNotIn(marker.encode("ascii"), raw)
        data = json.loads(raw)
        self.assertEqual(data["config"]["effective"]["slots"]["reviewer"],
                         {"kind": "agent", "target": "rigger-review:reviewer"})
        self.assertEqual([(p, s) for p, s in data["config"]["sources"] if p[0] == "slots" and s.startswith("L4:")], [])
        # Control: once trusted, the same command is in effect and the snapshot holds it.
        write_json(self.config_file(), {"schema": 1, "trust": {"project_commands": ["reviewer"]}})
        self.layout.part(1, stdin=self.startup("ut2"))
        self.assertIn(marker.encode("ascii"), (self.layout.config / "rigger" / "state" / "ut2.resolved.json").read_bytes())

    def test_compact_without_a_snapshot_applies_the_trust_filter_again(self):
        self.layout.rules_module()
        write_json(self.project_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                             "target": ["rv", "{out}"]}}})
        again = self.layout.part(1, stdin=self.compact("nc1"))
        self.assertTrue(again.startswith(b"rigger: snapshot missing; rules read again\n"), again[:80])
        self.assertIn(b"rigger: .claude/rigger.json may not set a command for slot reviewer;", again)
        self.assertIn(b"| reviewer | agent | rigger-review:reviewer | by-rule | default |\n", again)

    def test_every_part_packs_the_same_text_with_slots(self):
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                            "target": ["rv", "{out}"]}}})
        parts = b"".join(self.layout.part(i, stdin=self.startup("ep1")) for i in range(1, 7))
        rendered = self.layout.render("--session-id", "ep1")
        self.assertIn(b"## Slots\n", rendered)
        self.assertIn(command_line(self.layout.core, "ep1").encode("utf-8"), rendered)
        self.assertEqual(without_root_lines(parts), without_root_lines(rendered))

    def test_command_line_holds_the_session_id(self):
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                            "target": ["rv", "{out}"]}}})
        started = self.layout.part(1, stdin=self.startup("Ab_9-z"))
        self.assertIn(b"| reviewer | command | rv \xe2\x80\xa6 | by-rule | personal |\n", started)
        self.assertIn(command_line(self.layout.core, "Ab_9-z").encode("utf-8") + b"\n", started)
        self.assertIn(command_line(self.layout.core, "<session id>").encode("utf-8") + b"\n", self.layout.render())
        for unusable in ("../x", "a b", "x" * 129):
            self.assertIn(command_line(self.layout.core, "<session id>").encode("utf-8"),
                          self.layout.render("--session-id", unusable), unusable)

    def test_command_line_quotes_a_root_with_a_quote_and_a_space(self):
        # The line goes to a shell: a plugin root holding a quote or a space stays one word.
        tricky = self.tmp / "it's here" / "rigger-core"
        shutil.copytree(str(self.layout.core), str(tricky))
        self.layout.core = tricky
        self.layout.rules_module()
        write_json(self.config_file(), {"schema": 1, "slots": {"reviewer": {"kind": "command",
                                                                            "target": ["rv", "{out}"]}}})
        rendered = self.layout.render("--session-id", "q1").decode("utf-8")
        line = command_line(tricky, "q1")
        self.assertIn(line, rendered)
        # The shell is the oracle: it reads the two words of the printed line back as the two paths.
        words = rendered.split("`sh ", 1)[1].split(" <slot> ", 1)[0]
        if os.name == "nt":
            sh = str(Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe"))
            sh = sh if Path(sh).is_file() else shutil.which("sh")
        else:
            sh = "/bin/sh"
        done = subprocess.run([sh, "-c", "printf '%s\\n' " + words], stdout=subprocess.PIPE, timeout=60)
        root = assemble.root_text(tricky)
        self.assertEqual(done.stdout.decode("utf-8").splitlines(),
                         [root + "/bin/py.sh", root + "/tools/slot-run/slot_run.py"])


def cap_rows(count, before, loads, load_len):
    """The rows of the RV1 cap probes: CAPROW<n> rows loading x:<letters>, at or below the D28 caps."""
    rows = {}
    for i in range(count):
        rows["r%02d" % i] = {"before": ("CAPROW%02d " % i) + "b" * (before - 9),
                             "load": ["x:" + chr(97 + k) * (load_len - 2) for k in range(loads)]}
    return rows


class TableFitWiringTests(unittest.TestCase):
    """Ruling RV1 Important 1 through the assembler: project rows at the caps cannot push the Skill triggers
    table, its heading or a module row out of the session (the review's two cap probes, as test rows)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)
        self.layout.core = REPO_CORE
        self.layout.dirs = [str(REPO / "plugins" / "rigger-rules"), str(REPO / "plugins" / "rigger-testing")]
        self.layout.plain_plugin("x")  # x enabled: D39 cannot drop the rows, only the fit can

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_probe_scenarios_keep_the_table_heading_and_core_row_in_one_part(self):
        scenarios = [
            ("control", {}, 0),
            ("50 rows at the caps", cap_rows(50, 300, 4, 200), None),
            ("10 rows at the caps", cap_rows(10, 300, 4, 200), None),
            ("50 moderate rows", cap_rows(50, 120, 2, 40), None),
            ("10 moderate rows", cap_rows(10, 120, 2, 40), 10),
        ]
        for name, rows, shown in scenarios:
            with self.subTest(name):
                write_json(self.layout.project / ".claude" / "rigger.json", {"schema": 1, "triggers": rows})
                found = self.layout.assemble()
                bodies = assemble.part_bodies(found, False)
                holding = [body for body in bodies if b"## Skill triggers" in body]
                self.assertEqual(len(holding), 1, [len(body) for body in bodies])
                self.assertIn(b"| `rigger-testing:testing` |", holding[0])
                joined = b"".join(bodies)
                self.assertNotIn(b"rules left out over the hook cap", joined)
                if shown is None:  # the precondition: these rows overflow a part, so the fit had to drop some
                    self.assertIn(b"rigger: left out ", holding[0])
                    self.assertGreater(joined.count(b"CAPROW"), 0)
                else:
                    self.assertNotIn(b"rigger: left out ", joined)
                    self.assertEqual(joined.count(b"CAPROW"), shown)

    def test_a_subagent_table_is_measured_as_its_escaped_bytes(self):
        # A row of quotes doubles in the subagent's JSON: the fit must measure what the subagent part holds.
        rows = {"q%02d" % i: {"before": "Q%02d " % i + '"' * 295, "load": ["x:a"]} for i in range(50)}
        write_json(self.layout.project / ".claude" / "rigger.json", {"schema": 1, "triggers": rows})
        found = self.layout.assemble("subagent", agent_type="general-purpose")
        bodies = assemble.part_bodies(found, True)
        holding = [body for body in bodies if b"## Skill triggers" in body]
        self.assertEqual(len(holding), 1, [len(body) for body in bodies])
        self.assertIn(b"| `rigger-testing:testing` |", holding[0])
        self.assertIn(b"rigger: left out ", holding[0])  # the precondition: the rows overflow once escaped
        self.assertNotIn(b"rules left out over the hook cap", b"".join(bodies))

    def test_a_dropped_project_override_brings_back_the_module_row(self):
        # The fit restores the row a dropped config row replaced, read before the merge overwrote it.
        intro = "".join("- intro line %04d keeps this section close to one hook part\n" % n for n in range(130))
        (self.tmp / "own").mkdir()
        lay = Layout(self.tmp / "own")  # a fixture core and rigger-rules whose table section is nearly full
        lay.plain_plugin("x")
        lay.rules_module(**{"rules/skills.md": ("## Skill triggers\n\n" + intro + "\n{{triggers:skills.skill-triggers}}\n",
                                                "both")})
        lay.manifest_set(lay.plugins / "rigger-rules", triggers=[
            {"id": "rigger-rules.other-os", "before": "other os module row", "load": ["x:y"],
             "requires": ["os:" + OTHER_OS]}])
        big = {"before": "my own git way " + "g" * 285, "load": ["x:" + chr(97 + k) * 198 for k in range(4)]}
        other = dict(big, before="my own other row " + "o" * 283)
        write_json(lay.project / ".claude" / "rigger.json", {"schema": 1, "triggers": {
            "rigger-core.git-workflow": big, "rigger-rules.other-os": other}})
        text = b"".join(assemble.part_bodies(lay.assemble(), False))
        self.assertIn(b"rigger: left out 2 of the trigger rows from ", text)  # neither override fits
        self.assertNotIn(b"my own", text)
        self.assertIn(b"| branching and committing | `rigger-core:git-workflow` |\n", text)
        self.assertNotIn(b"other os module row", text)  # a replaced row whose requires is unmet stays out


class OptionTests(Base):
    def talk(self, options=None, settings=None):
        self.layout.module("rigger-talk", {"rules/talk.md": (TALK_TEXT, "main")}, user_config=TALK_CONFIG)
        if settings is not None:
            write(self.layout.config / "settings.json", settings)
        elif options is not None:
            write_json(self.layout.config / "settings.json",
                       {"pluginConfigs": {"rigger-talk@inline": {"options": options}}})
        return self.layout.render()

    def line(self, language, lines, report):
        return ("- Reply in %s; ~%s lines, ~%s report.\n" % (language, lines, report)).encode("utf-8")

    def test_talk_options_from_store(self):
        self.assertIn(self.line("Polish", 7, 20), self.talk({"reply_language": "Polish", "reply_lines": 7,
                                                             "report_lines": 20}))

    def test_talk_option_fallbacks(self):
        rows = [
            ({}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": 0}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": -3}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": 7.5}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": True}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": "x7"}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_lines": "7"}, self.line(LANGUAGE_DEFAULT, 7, 15)),
            ({"report_lines": [1]}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_language": ""}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_language": "a\nb"}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_language": "a b"}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_language": "Polish\udfff"}, self.line(LANGUAGE_DEFAULT, 5, 15)),
            ({"reply_language": 3}, self.line(LANGUAGE_DEFAULT, 5, 15)),
        ]
        for options, expected in rows:
            with self.subTest(options=repr(options)):
                self.tearDown()
                self.setUp()
                self.assertIn(expected, self.talk(options))

    def test_generic_number_option_refuses_bool(self):
        config = {"width": {"type": "number", "default": 3}}
        self.layout.module("rigger-x", {"rules/x.md": ("## X\n\nwidth {{var:width}}\n", "both")}, user_config=config)
        write_json(self.layout.config / "settings.json",
                   {"pluginConfigs": {"rigger-x@inline": {"options": {"width": True}}}})
        self.assertIn(b"width 3\n", self.layout.render())

    def test_option_store_probes(self):
        # Untrusted JSON: deep nesting, a lone surrogate, wrong shapes; defaults and no option notice.
        rows = [
            b"[" * 100000 + b"]" * 100000,
            b'{"pluginConfigs": {"rigger-talk@inline": {"options": {"reply_language": "\\udfff"}}}}',
            b'{"pluginConfigs": []}',
            b'{"pluginConfigs": {"rigger-talk@inline": []}}',
            b'{"pluginConfigs": {"rigger-talk@inline": {"options": "x"}}}',
            b"not json",
        ]
        for settings in rows:
            with self.subTest(settings=settings[:40]):
                self.tearDown()
                self.setUp()
                self.assertIn(self.line(LANGUAGE_DEFAULT, 5, 15), self.talk(settings=settings))
                found = self.layout.assemble()
                self.assertEqual(found.notices, found.discovery.notices)  # the option reader adds none

    def test_option_reader_with_nul_in_path(self):
        module = self.layout.module("rigger-talk", {"rules/talk.md": (TALK_TEXT, "main")}, user_config=TALK_CONFIG)
        found = self.layout.assemble()
        talk = [m for m in found.discovery.modules if m.name == "rigger-talk"][0]
        self.assertEqual(talk.root, module)
        values = assemble.read_options(Path(str(self.layout.config) + "\x00x"), [talk])
        self.assertEqual(values["rigger-talk"]["reply_lines"], "5")
        self.assertEqual(values["rigger-talk"]["reply_language"], LANGUAGE_DEFAULT)

    def test_installed_module_reads_its_marketplace_key(self):
        self.layout.module("rigger-talk", {"rules/talk.md": (TALK_TEXT, "main")}, user_config=TALK_CONFIG,
                           listed=False)
        root = self.layout.plugins / "rigger-talk"
        write_json(self.layout.config / "settings.json", {
            "enabledPlugins": {"rigger-talk@rigger": True},
            "pluginConfigs": {"rigger-talk@rigger": {"options": {"reply_lines": 9}},
                              "rigger-talk@inline": {"options": {"reply_lines": 3}}}})
        write_json(self.layout.config / "plugins" / "installed_plugins.json",
                   {"version": 2, "plugins": {"rigger-talk@rigger": [{"scope": "user", "installPath": str(root)}]}})
        self.assertIn(self.line(LANGUAGE_DEFAULT, 9, 15), self.layout.render())


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def check(self, *roots):
        args = ["--check", "--modules"] + [str(root) for root in roots]
        return self.layout.cli(*args)

    def test_check_findings(self):
        lay = self.layout
        good = lay.module("mod-a", {"rules/tests.md": ("## Tests\n\nx\n", "both")})
        rows = [
            ("duplicate", [lay.module("mod-b", {"rules/tests.md": ("## Tests\n\ny\n", "both")})],
             "duplicate rule id tests.tests in mod-a and mod-b"),
            ("oversize", [lay.module("mod-c", {"rules/big.md": ("## Big\n\n" + "b" * 9000 + "\n", "both")})],
             "section big.big in mod-c/rules/big.md is over 9000 bytes"),
            ("placeholder", [lay.module("mod-d", {"rules/u.md": ("## U\n\n{{nope:x}}\n", "both")})],
             "unknown placeholder {{nope:x}} in mod-d/rules/u.md"),
            ("load", [lay.module("mod-e", {"rules/e.md": ("## E\n\ne\n", "both")}, triggers=[
                {"id": "mod-e.t", "before": "b", "load": ["mod-a:missing"]}])],
             "trigger mod-e.t load mod-a:missing resolves to nothing"),
            ("slug", [lay.module("mod-f", {"rules/f.md": ("## —\n\nf\n", "both")})],
             "empty heading slug in mod-f/rules/f.md:1"),
        ]
        code, out, err = self.check(lay.core, good)
        self.assertEqual(code, 0, out + err)  # control: the base set is clean
        for name, extra, line in rows:
            with self.subTest(name):
                code, out, err = self.check(lay.core, good, *extra)
                self.assertEqual(code, 1, out + err)
                self.assertIn(line, out.decode("utf-8").splitlines())

    def test_check_slot_and_table_findings(self):
        # Task 15's --check findings, one planted fixture each, each with its exact line (D37, D38, D47).
        lay = self.layout
        good = lay.module("mod-a", {"rules/tests.md": ("## Tests\n\nuse {{slot:testing}}\n", "both"),
                                    "rules/skills.md": ("## Skill triggers\n\n{{triggers:skills.skill-triggers}}\n",
                                                        "both")})
        defaults = lay.core / "rigger-defaults.json"
        clean = defaults.read_bytes()

        def broken_default():
            data = json.loads(clean)
            data["slots"]["worker"]["target"] = "rigger-core:nope"
            write_json(defaults, data)
            return []

        rows = [
            ("level-1 default", broken_default, "slot worker: rigger-core has no agent nope"),
            ("second table", lambda: [lay.module("mod-b", {"rules/more.md": (
                "## More\n\n{{triggers:skills.skill-triggers}}\n", "both")})],
             "second trigger table in mod-b/rules/more.md"),
            ("absent slot plugin", lambda: [lay.module("mod-c", {"rules/p.md": (
                "## P\n\nload {{slot:process.plans}} first\n", "both")})],
             "{{slot:process.plans}} in mod-c/rules/p.md names a slot an absent plugin fills"),
            ("tdd before testing", lambda: [lay.module("mod-d", {"rules/d.md": ("## D\n\nd\n", "both")}, triggers=[
                {"id": "mod-d.t", "before": "b", "load": ["slot:process.tdd", "slot:testing"]}])],
             "trigger mod-d.t loads process.tdd without testing before it"),
        ]
        code, out, err = self.check(lay.core, lay.testing, good)
        self.assertEqual(code, 0, out + err)  # control: the base set, with one table and a filled slot, is clean
        self.assertTrue(out.decode("utf-8").splitlines()[-1].endswith("findings 0"))
        for name, plant, line in rows:
            with self.subTest(name):
                extra = plant()
                try:
                    code, out, err = self.check(lay.core, lay.testing, good, *extra)
                finally:
                    defaults.write_bytes(clean)
                lines = out.decode("utf-8").splitlines()
                self.assertEqual(code, 1, out + err)
                self.assertEqual([l for l in lines if not l.startswith("#")], [line])

    def test_check_orphan_override_by_file_stem(self):
        # D22: a company file whose stem matches a general file overrides that file's sections.
        lay = self.layout
        good = lay.module("mod-a", {"rules/tests.md": ("## Tests\n\nx\n## Plans\n\np\n", "both")})
        rows = [  # a renamed heading in a file the general layer has is a stale override (StaleOverrideTests)
            ("empty section, new file", {"rules/fresh.md": ("## Fresh\n\n", "both")},
             "orphan override fresh.fresh in acme/rules/fresh.md: no lower section has that id"),
        ]
        for name, files, line in rows:
            with self.subTest(name):
                shutil.rmtree(str(lay.plugins / "acme"), ignore_errors=True)
                acme = lay.module("acme", files, layer="company")
                code, out, err = self.check(lay.core, good, acme)
                self.assertEqual(code, 1, out + err)
                orphans = [l for l in out.decode("utf-8").splitlines() if l.startswith("orphan override")]
                self.assertEqual(orphans, [line])

    def test_check_new_company_file_is_appended_and_clean(self):
        # D22: an unmatched id in a file whose stem matches no general file is a new section, not a finding.
        lay = self.layout
        good = lay.module("mod-a", {"rules/tests.md": ("## Tests\n\nx\n", "both")})
        acme = lay.module("acme", {"rules/policy.md": ("## Policy\n\ncompany policy\n", "both")}, layer="company")
        code, out, err = self.check(lay.core, good, acme)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(out.decode("utf-8").splitlines()[-1].endswith("findings 0"))
        lay.dirs = [str(good), str(acme)]
        found = lay.assemble()
        self.assertEqual(found.sections[-1]["id"], "policy.policy")
        self.assertIn(b"company policy", self.text_of(found))
        # I5: a preamble is no heading-only section, so a new stem with one stays clean.
        for name, text in (("one-line preamble", "Company intro line.\n## Policy\n\np\n"),
                           ("leading blank line", "\n## Policy\n\np\n")):
            with self.subTest(name):
                shutil.rmtree(str(acme), ignore_errors=True)
                acme = lay.module("acme", {"rules/policy.md": (text, "both")}, layer="company")
                code, out, err = self.check(lay.core, good, acme)
                self.assertEqual(code, 0, out + err)

    def text_of(self, found):
        return b"".join(block[1] for block in found.blocks)

    def test_check_company_in_public_marketplace(self):
        repo = self.tmp / "repo"
        write_json(repo / ".claude-plugin" / "marketplace.json", {"name": "rigger", "plugins": []})
        self.layout.plugins = repo / "plugins"
        acme = self.layout.module("acme", {"rules/a.md": ("## A\n\na\n", "both")}, layer="company")
        code, out, _ = self.check(self.layout.core, acme)
        self.assertEqual(code, 1)
        self.assertIn("module acme skipped: company layer in the public marketplace", out.decode("utf-8").splitlines())

    def test_check_zero_rules_files(self):
        empty = self.layout.module("rigger-z", {})
        code, out, _ = self.check(empty)
        self.assertEqual(code, 1)
        self.assertIn("0 rules files read", out.decode("utf-8").splitlines())

    def test_check_repo_is_clean(self):
        code, out, err = self.layout.cli("--check", CLAUDE_PLUGIN_ROOT=str(REPO_CORE), RIGGER_MODULE_DIRS="")
        lines = out.decode("utf-8").splitlines()
        self.assertEqual(code, 0, out + err)
        self.assertRegex(lines[-1], r"^# check: modules 11, rules files (1[89]|[2-9]\d), findings 0$")
        self.assertIn("# external: superpowers", lines)

    def test_check_without_modules_in_a_cache_layout_says_it_saw_one_module(self):
        cached = self.tmp / "cache" / "rigger" / "rigger-core" / "1.0.0"
        shutil.copytree(str(REPO_CORE), str(cached), ignore=shutil.ignore_patterns("__pycache__"))
        code, out, err = self.layout.cli("--check", CLAUDE_PLUGIN_ROOT=str(cached), RIGGER_MODULE_DIRS="")
        lines = out.decode("utf-8").splitlines()
        self.assertEqual(code, 0, out + err)
        self.assertRegex(lines[-2], r"^# check: modules 1, rules files \d+, findings 0$")
        self.assertEqual(lines[-1], "# note: only this plugin was found next to itself; pass --modules <dir>... to check the others")

    def test_check_without_modules_ignores_older_cached_versions_of_itself(self):
        base = self.tmp / "cache" / "rigger" / "rigger-core"
        for version in ("0.4.9", "0.5.0"):
            shutil.copytree(str(REPO_CORE), str(base / version), ignore=shutil.ignore_patterns("__pycache__"))
        code, out, err = self.layout.cli("--check", CLAUDE_PLUGIN_ROOT=str(base / "0.5.0"), RIGGER_MODULE_DIRS="")
        lines = out.decode("utf-8").splitlines()
        self.assertEqual(code, 0, out + err)
        self.assertRegex(lines[-2], r"^# check: modules 1, rules files \d+, findings 0$")
        self.assertTrue(lines[-1].startswith("# note: only this plugin was found"), lines)

    def test_doctor_rows(self):
        self.layout.rules_module()
        code, out, _ = self.layout.cli("--doctor")
        self.assertEqual(code, 0)
        fields = out.decode("utf-8").splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["CHECK", "rules-assembly", "OK"])
        self.assertRegex(fields[3], r"^modules 4, sections 6$")  # ## Slots is a section of rigger-core; rigger-review and rigger-testing have none
        self.assertEqual(len(fields), 5)

    def test_each_expects_row_gets_its_own_fix(self):
        # RV3 F3: two names that a shortened row name shows alike keep their own FIX line.
        names = ["a" * 85 + "x", "a" * 85 + "y"]
        write_json(self.layout.project / ".claude" / "rigger.json", {"schema": 1, "expects": names})
        code, out, _ = self.layout.cli("--doctor", CLAUDE_PROJECT_DIR=str(self.layout.project))
        self.assertEqual(code, 0)
        lines = out.decode("utf-8").splitlines() + [""]
        pairs = [(lines[i].split("\t")[1], lines[i + 1]) for i, line in enumerate(lines)
                 if line.startswith("CHECK\texpects ")]
        self.assertEqual(len(pairs), 2)
        for (row, fix), name in zip(pairs, names):
            self.assertEqual(fix, "FIX\t%s\tclaude plugin install %s" % (row, name))


class StaleOverrideTests(unittest.TestCase):
    """D94: a company section in a file whose stem the general layer has, under an id that layer lacks."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def check(self, *roots):
        return self.layout.cli("--check", "--modules", *[str(root) for root in roots])

    def doctor_rows(self):
        code, out, err = self.layout.cli("--doctor")
        self.assertEqual(code, 0, out + err)
        return [line.split("\t") for line in out.decode("utf-8").splitlines()
                if line.startswith("CHECK\tstale-override")]

    def lower(self):  # rigger-rules: the personal layer is read only with it enabled (D4)
        return self.layout.module("rigger-rules", {"rules/tests.md": ("## Tests\n\nx\n## Plans\n\np\n", "both")})

    def test_stale_override_names_the_id_and_both_files(self):
        lay = self.layout
        good = self.lower()
        acme = lay.module("acme", {"rules/tests.md": ("## Testz\n\ny\n", "both")}, layer="company")
        code, out, err = self.check(lay.core, good, acme)
        self.assertEqual(code, 1, out + err)
        lines = [l for l in out.decode("utf-8").splitlines() if " override " in l]
        self.assertEqual(lines, ["stale override tests.testz in acme/rules/tests.md: rigger-rules/rules/tests.md has no "
                                 "such id, so both rules print; closest general id tests.tests"])

    def test_matching_override_is_not_flagged(self):
        lay = self.layout
        good = self.lower()
        # A second general file with that stem adds ids to the same layer: never an override.
        more = lay.module("mod-b", {"rules/tests.md": ("## More tests\n\nm\n", "both")})
        acme = lay.module("acme", {"rules/tests.md": ("## Plans\n\nours\n", "both"),
                                   "rules/policy.md": ("## Policy\n\nnew\n", "both")}, layer="company")
        # A second company file with a stem only the company layer has: no general file lies below it.
        lay.module("acme-b", {"rules/policy.md": ("## Policy extra\n\ne\n", "both")}, layer="company")
        lay.personal("tests.md", "## Tests\n\nmine\n")
        lay.personal("notes.md", "## Notes\n\nmine\n")
        code, out, err = self.check(lay.core, good, more, acme)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.doctor_rows(), [])
        self.assertEqual([n for n in lay.assemble().notices if "stale" in n], [])

    def test_personal_layer_adds_sections(self):
        # D1: rules.d is additive (curate-lessons writes rules.d/lessons.md), so a new id there is no stale override.
        lay = self.layout
        self.lower()
        lay.module("rigger-lessons", {"rules/lessons.md": ("## Lessons\n\nl\n", "both")})
        lay.module("acme", {"rules/policy.md": ("## Policy\n\np\n", "both")}, layer="company")
        lay.personal("lessons.md", "## Git\n\n- a lesson\n")
        lay.personal("tests.md", "a line with no heading\n")  # tests._preamble
        lay.personal("policy.md", "## Policy rules\n\nmine\n")  # over a company file
        self.assertEqual(self.doctor_rows(), [])
        found = lay.assemble()
        self.assertEqual([n for n in found.notices if "stale" in n], [])
        self.assertIn(b"- a lesson", lay.part(1))  # read, so the silence is not a skipped rules.d
        lay.module("acme-b", {"rules/tests.md": ("## Testz\n\ny\n", "both")}, layer="company")
        self.assertEqual([row[1] for row in self.doctor_rows()], ["stale-override tests.testz"])  # company still fires

    def test_doctor_shows_the_stale_override(self):
        lay = self.layout
        self.lower()
        lay.module("acme", {"rules/tests.md": ("## Testz\n\ny\n", "both")}, layer="company")
        self.assertEqual(self.doctor_rows(), [
            ["CHECK", "stale-override tests.testz", "WARN",
             "stale override tests.testz in acme/rules/tests.md: rigger-rules/rules/tests.md has no such id, so both "
             "rules print", "assemble.py --doctor"]])
        code, out, err = lay.cli("--doctor")
        head = out.decode("utf-8").splitlines()[0].split("\t")
        self.assertEqual(head[:3], ["CHECK", "rules-assembly", "OK"])  # the session notice has its own row

    def test_session_notice_is_one_bounded_line(self):
        # Nobody runs doctor daily: the session that gets both rules says so, in one line.
        lay = self.layout
        self.lower()
        lay.module("acme", {"rules/tests.md": ("## Testz\n\ny\n## Tests a\n\na\n## Tests b\n\nb\n## Tests c\n\nc\n",
                                               "both")}, layer="company")
        line = ("rigger: stale override tests.testz in acme/rules/tests.md, tests.tests-a in acme/rules/tests.md, "
                "tests.tests-b in acme/rules/tests.md +1 more: the lower rules file has no such id, so both rules "
                "print (run /rigger-core:doctor)")
        self.assertEqual([n for n in lay.assemble().notices if "stale" in n], [line])
        self.assertIn(line.encode("utf-8") + b"\n", lay.part(1))

    def test_check_without_modules_reads_the_modules_a_session_reads(self):
        # The overlay's 0 findings: --check alone looked only next to itself, never at RIGGER_MODULE_DIRS.
        lay = self.layout
        self.lower()
        lay.plugins = self.tmp / "elsewhere"  # not next to rigger-core, where --check alone already looks
        lay.module("acme", {"rules/tests.md": ("## Testz\n\ny\n", "both")}, layer="company")
        code, out, err = lay.cli("--check")
        self.assertEqual(code, 1, out + err)
        # Only that line: a module both next to rigger-core and in RIGGER_MODULE_DIRS is read once.
        self.assertEqual([l for l in out.decode("utf-8").splitlines() if not l.startswith("#")],
                         ["stale override tests.testz in acme/rules/tests.md: rigger-rules/rules/tests.md has no such "
                          "id, so both rules print; closest general id tests.tests"])
        code, out, err = lay.cli("--check", RIGGER_MODULE_DIRS="")
        self.assertEqual(code, 0, out + err)  # control: without the variable that module is not read
        self.assertNotIn("acme", out.decode("utf-8"))


class DependencyRowTests(unittest.TestCase):
    """D96: doctor reads each enabled plugin's `dependencies`. Layouts as `claude plugin` 2.1.327 wrote them (Task 33
    spike): known_marketplaces.json gives a directory marketplace's folder as `installLocation`, a bare dependency
    resolves in the plugin's own marketplace, and install skips a cross-marketplace dependency the plugin's
    marketplace does not allow."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.layout = Layout(self.tmp)
        self.installed = {}
        self.enabled = {}
        self.known = {}

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def marketplace(self, name, allow=None):
        folder = self.tmp / ("mkt-" + name)
        listing = {"name": name, "owner": {"name": "t"}, "plugins": []}
        if allow is not None:
            listing["allowCrossMarketplaceDependenciesOn"] = allow
        write_json(folder / ".claude-plugin" / "marketplace.json", listing)
        self.known[name] = {"source": {"source": "directory", "path": str(folder)}, "installLocation": str(folder)}

    def plugin(self, key, deps=None, installed=True, enabled=True):
        name, _, market = key.partition("@")
        root = self.layout.config / "plugins" / "cache" / market / name / "0.1.0"
        data = {"name": name, "version": "0.1.0"}
        if deps is not None:
            data["dependencies"] = deps
        write_json(root / ".claude-plugin" / "plugin.json", data)
        if installed:
            self.installed[key] = [{"scope": "user", "installPath": str(root), "version": "0.1.0"}]
        self.enabled[key] = enabled

    def rows(self):
        config = self.layout.config
        write_json(config / "plugins" / "installed_plugins.json", {"version": 2, "plugins": self.installed})
        write_json(config / "plugins" / "known_marketplaces.json", self.known)
        write_json(config / "settings.json", {"enabledPlugins": self.enabled})
        code, out, err = self.layout.cli("--doctor")
        self.assertEqual(code, 0, out + err)
        lines = out.decode("utf-8").splitlines()
        return [line.split("\t") for line in lines if line.split("\t")[1].startswith("dependency ")]

    def test_missing_dependency_is_an_error_row(self):
        for name, installed, enabled in (("not installed", False, True), ("installed, disabled", True, False)):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.marketplace("core-fx")
                self.plugin("mod-b@core-fx", ["core-a"])
                self.plugin("core-a@core-fx", installed=installed, enabled=enabled)
                self.assertEqual(self.rows(), [
                    ["CHECK", "dependency mod-b@core-fx", "MISSING", "needs core-a@core-fx: not installed or not "
                     "enabled, so mod-b@core-fx does not load", "assemble.py --doctor"],
                    ["FIX", "dependency mod-b@core-fx", "claude plugin install core-a@core-fx"]])

    def test_cross_marketplace_without_allowlist_names_the_fix(self):
        self.marketplace("core-fx")
        self.marketplace("company-fx")
        self.plugin("mod-b@company-fx", ["core-a@core-fx"])
        self.assertEqual(self.rows(), [
            ["CHECK", "dependency mod-b@company-fx", "MISSING", "needs core-a@core-fx: not installed or not enabled, "
             "so mod-b@company-fx does not load; install skips it until company-fx's marketplace.json lists "
             "\"core-fx\" in allowCrossMarketplaceDependenciesOn", "assemble.py --doctor"],
            ["FIX", "dependency mod-b@company-fx", "claude plugin install core-a@core-fx"]])
        self.tearDown()
        self.setUp()  # control: with the allowlist the row is the plain missing row
        self.marketplace("core-fx")
        self.marketplace("company-fx", allow=["core-fx"])
        self.plugin("mod-b@company-fx", ["core-a@core-fx"])
        self.assertEqual(self.rows()[0][3], "needs core-a@core-fx: not installed or not enabled, so mod-b@company-fx "
                                            "does not load")

    def test_a_string_allowlist_allows_nothing(self):
        # marketplace.json is another maintainer's file: a string is a wrong shape, never a substring match (D2).
        self.marketplace("core-fx")
        self.marketplace("company-fx", allow="core-fx")
        self.plugin("mod-b@company-fx", ["core-a@core-fx"])
        self.assertTrue(self.rows()[0][3].endswith("install skips it until company-fx's marketplace.json lists "
                                                   "\"core-fx\" in allowCrossMarketplaceDependenciesOn"))

    def test_dependency_present_is_ok(self):
        self.marketplace("core-fx")
        self.marketplace("company-fx")  # installed by hand, a dependency loads without the allowlist (spike q2)
        self.plugin("mod-b@company-fx", ["core-a@core-fx"])
        self.plugin("core-a@core-fx")
        self.assertEqual(self.rows(), [["CHECK", "dependency mod-b@company-fx", "OK",
                                        "needs core-a@core-fx: installed and enabled", "assemble.py --doctor"]])

    def test_a_plugin_without_dependencies_has_no_row(self):
        self.marketplace("core-fx")
        self.plugin("core-a@core-fx")
        self.plugin("mod-c@core-fx", [])
        self.plugin("mod-d@core-fx", ["core-z"], enabled=False)  # a disabled plugin is not checked
        self.assertEqual(self.rows(), [])

    def test_wrong_shapes_give_no_row(self):
        # plugin.json is the plugin author's file: a shape doctor cannot read names no dependency.
        for name, deps in (("a string", "core-z"), ("not strings", [1, {"name": "core-z"}, ""])):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.marketplace("core-fx")
                self.plugin("mod-b@core-fx", deps)
                self.assertEqual(self.rows(), [])


class ShellwiseSlotRowTests(Base):
    """Design section 1: the `slot shell-guard` row of the shellwise filler names its status.json state and the
    failure mode closed; a state that leaves commands unguarded says so."""

    CELL = "rigger-shellwise (fails closed); "

    def setUp(self):
        super().setUp()
        root = self.layout.module("rigger-shellwise", {})
        self.layout.manifest_set(root, fills="shell-guard", failure_mode="closed")
        write_json(self.layout.config / "rigger" / "config.json",
                   {"schema": 1, "slots": {"shell-guard": {"requires": []}}})

    def status(self, row, folder="rigger-shellwise-inline"):
        path = self.layout.config / "plugins" / "data" / folder / "shellwise" / "status.json"
        write(path, row if isinstance(row, bytes) else json.dumps(dict(
            {"version": "0.1.1", "target": "x86_64-unknown-linux-gnu", "reason": "", "at": "2026-11-26T10:00:00Z"},
            **row)))

    def slot_row(self):
        code, out, err = self.layout.cli("--doctor")
        self.assertEqual(code, 0, out + err)
        rows = [line.split("\t") for line in out.decode("utf-8").splitlines()
                if line.startswith("CHECK\tslot shell-guard\t")]
        self.assertEqual(len(rows), 1, out)
        return rows[0][2], rows[0][3]

    def test_ready(self):
        self.status({"state": "ready"})
        self.assertEqual(self.slot_row(), ("OK", self.CELL + "state ready, v0.1.1"))

    def test_override(self):
        self.status({"state": "override", "version": "0.2.0"})
        self.assertEqual(self.slot_row(), ("OK", self.CELL + "state override, v0.2.0"))

    def test_downloading(self):
        self.status({"state": "downloading"})
        self.assertEqual(self.slot_row(), ("INFO", self.CELL + "state downloading, v0.1.1; commands run unguarded"))

    def test_failed(self):
        self.status({"state": "failed", "reason": "hash mismatch for a.zip\nnext"})
        self.assertEqual(self.slot_row(), ("WARN", self.CELL + "state failed, v0.1.1: hash mismatch for a.zip\\u000anext; "
                                                   "commands run unguarded"))

    def test_no_asset(self):
        self.status({"state": "no-asset", "reason": "no asset for riscv64"})
        self.assertEqual(self.slot_row(), ("WARN", self.CELL + "state no-asset, v0.1.1: no asset for riscv64; "
                                                   "commands run unguarded"))

    def test_missing(self):
        self.assertEqual(self.slot_row(), ("WARN", self.CELL + "state missing: no shellwise status.json; "
                                                   "commands run unguarded"))

    def test_unknown(self):
        for name, write_rows in (("unknown state", lambda: self.status({"state": "bogus"})),
                                 ("not JSON", lambda: self.status(b"{nope")),
                                 ("two data folders", lambda: (self.status({"state": "ready"}),
                                                               self.status({"state": "ready"}, "rigger-shellwise-b")))):
            with self.subTest(name):
                shutil.rmtree(str(self.layout.config / "plugins"), ignore_errors=True)
                write_rows()
                status, detail = self.slot_row()
                self.assertEqual(status, "WARN")
                self.assertTrue(detail.startswith(self.CELL + "state unknown: "), detail)
                self.assertTrue(detail.endswith("; commands run unguarded"), detail)


if __name__ == "__main__":
    unittest.main()
