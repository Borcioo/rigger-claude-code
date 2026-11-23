"""Tests for assemble.py: layers, the personal layer, drop, requires, parts, the snapshot and --check."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
        for skill in ("git-workflow", "testing"):
            write(self.core / "skills" / skill / "SKILL.md", "---\nname: %s\n---\n" % skill)

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
        assert code == 0, (code, err)
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


class LayerTests(Base):
    def test_layer_replace_in_place_and_append(self):
        self.layout.rules_module()
        acme = self.layout.module("acme", {"rules/tests.md": ("## Tests\n\ncompany tests\n", "both"),
                                           "rules/extra.md": ("## Extra\n\nextra rule\n", "both")}, layer="company")
        found = self.layout.assemble()
        self.assertEqual(self.ids(found), ["state-file.state-file", "tests.tests", "git.git", "plans.plans",
                                           "skills.skill-triggers", "extra.extra"])
        tests = found.sections[1]
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
        self.assertEqual(self.ids(found)[1], "tests.tests")

    def test_personal_text_is_not_rendered(self):
        self.layout.rules_module()
        self.layout.personal("mine.md", "## Mine\n\n{{slot:testing}} and {{reply_language}}\n")
        out = self.layout.render()
        self.assertIn(b"{{slot:testing}} and {{reply_language}}\n", out)
        self.assertIn(b"run the tests with `rigger-core:testing`", out)  # control: module text is rendered

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
        self.assertEqual(self.ids(found), ["state-file.state-file", "skills.skill-triggers"])
        kept = self.layout.assemble(HARNESS_RULES_DROP="## Git ({{skill:x:y}})")
        self.assertIn("git.git", self.ids(kept))
        dropped = self.layout.assemble(HARNESS_RULES_DROP="## Git (`x:y`)")
        self.assertNotIn("git.git", self.ids(dropped))
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        for drop in ("tests.md", "## Tests", "tests.tests"):
            self.assertIn(b"my tests", self.text(self.layout.assemble(HARNESS_RULES_DROP=drop)), drop)

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
        for source in ("resume", "weird"):
            for index in (1, 2):
                self.assertEqual(self.layout.part(index, stdin=self.layout.hook_input(source=source)), b"", source)
        self.assertIn(b"run the tests", self.layout.part(1, stdin=self.layout.hook_input(source="clear")))

    def test_hook_input_probes(self):
        # Untrusted JSON on stdin: deep nesting, a lone surrogate, a NUL in the path. Rules print anyway.
        self.layout.rules_module()
        probes = [
            b"[" * 100000,
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

    def test_crash_prints_one_visible_line(self):
        original = assemble.assemble

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        assemble.assemble = boom
        try:
            for argv, expected in (
                (["--part", "1"], b"rigger: rules not assembled: RuntimeError: boom (run /rigger-core:doctor)\n"),
                (["--part", "2"], b""),
            ):
                out = io.BytesIO()
                self.assertEqual(assemble.main(argv, stdin=self.layout.hook_input(), stdout=out), 0)
                self.assertEqual(out.getvalue(), expected)
            out = io.BytesIO()
            self.assertEqual(assemble.main(["--part", "1", "--subagent"], stdin=b"{}", stdout=out), 0)
            context = json.loads(out.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(context, "rigger: rules not assembled: RuntimeError: boom (run /rigger-core:doctor)\n")
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

    def test_unsafe_session_id_writes_nothing(self):
        self.layout.rules_module()
        for session in ("", "../x", "a/b", "x" * 129):
            out = self.layout.part(1, stdin=self.layout.hook_input(session_id=session, source="startup"))
            self.assertIn(b"run the tests", out)
        self.assertFalse((self.layout.config / "rigger" / "state").exists())


class FullLayoutTests(unittest.TestCase):
    """All 7 repository modules plus a full rules.d."""

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
        self.assertEqual(len(self.layout.dirs), 6)
        found = self.layout.assemble()
        self.assertEqual(found.notices, [])
        session = [self.layout.part(i) for i in range(1, 7)]
        stdin = self.layout.hook_input(hook_event_name="SubagentStart", agent_type="general-purpose")
        sub = [self.layout.part(i, subagent=True, stdin=stdin) for i in range(1, 5)]
        joined = b"".join(session + sub)
        self.assertNotIn(b"rules left out over the hook cap", joined)
        self.assertNotIn(b"rules_extra: left out", b"".join(session))
        self.assertIn(b"## a.md", b"".join(session))

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
    def test_config_row_for_an_absent_section_gives_a_notice(self):
        row = {"before": "lost row", "load": ["x:y"], "section": "nope.table"}
        path = self.layout.config / "rigger" / "config.json"
        write_json(path, {"schema": 1, "triggers": {"p.lost": row}})
        found = self.layout.assemble()  # rigger-rules absent: core rows give no notice
        self.assertEqual(found.notices, ["rigger: trigger p.lost from %s not shown: no table nope.table" % path])
        write_json(path, {"schema": 1, "triggers": {"p.lost": row, "p.kept": {"before": "kept row", "load": ["x:y"]}}})
        self.layout.rules_module()
        found = self.layout.assemble()
        self.assertEqual(len(found.notices), 1)
        self.assertIn(b"| kept row |", self.text(found))
        self.assertNotIn(b"lost row", self.text(found))

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

    def test_notices_open_part_one_only(self):
        self.layout.rules_module()
        self.layout.personal("tests.md", "## Tests\n\nmy tests\n")
        first = self.layout.part(1)
        self.assertTrue(first.startswith(b"rigger: rules.d/tests.md replaces rule tests.tests\n"))


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
            ("orphan", [lay.module("acme", {"rules/tests.md": ("## Testz\n\ny\n", "both")}, layer="company")],
             "orphan override tests.testz in acme/rules/tests.md: no lower section has that id"),
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
        self.assertRegex(lines[-1], r"^# check: modules 7, rules files (1[9]|[2-9]\d), findings 0$")
        self.assertIn("# external: superpowers", lines)

    def test_doctor_rows(self):
        self.layout.rules_module()
        code, out, _ = self.layout.cli("--doctor")
        self.assertEqual(code, 0)
        fields = out.decode("utf-8").splitlines()[0].split("\t")
        self.assertEqual(fields[:3], ["CHECK", "rules-assembly", "OK"])
        self.assertRegex(fields[3], r"^modules 2, sections 5$")
        self.assertEqual(len(fields), 5)


if __name__ == "__main__":
    unittest.main()
