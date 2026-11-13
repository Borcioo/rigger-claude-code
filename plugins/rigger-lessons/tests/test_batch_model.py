"""Tests for tools/batch_model.py (corpus, `claude -p` command and environment, prompts, output parsing).

Configs are fake folders under a temp dir; every model call goes to tests/stub_claude.py, never a real `claude`.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(PLUGIN / "tools"), str(PLUGIN / "tests")]

import batch_model  # noqa: E402
import stub_claude  # noqa: E402

SEP = " · "
FLAGS = ["-p", "--model", "sonnet", "--output-format", "json", "--json-schema", '{"type": "object"}',
         "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
         "--tools", "Read,Grep,Glob", "--max-budget-usd", "2.0"]


def no_raise(test, function, *args):
    """function(*args); an exception fails the test, so a removed shape check reads as a failure, not an error."""
    try:
        return function(*args)
    except Exception as exc:  # noqa: BLE001
        test.fail("%s raised %s: %s" % (function.__name__, type(exc).__name__, exc))


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="batch-model-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "home" / ".claude"
        self.config.mkdir(parents=True)


class CorpusTests(Temp):
    def setUp(self):
        super().setUp()
        self.one = self.tmp / "cache" / "one"
        self.two = self.tmp / "cache" / "two"
        self.files = [write(self.one / "rules" / "a.md", "rule a\r\n"), write(self.one / "rules" / "b.md", "rule b\n"),
                      write(self.one / "skills" / "s" / "SKILL.md", "skill s\n"),
                      write(self.two / "skills" / "t" / "SKILL.md", "skill t\n")]
        write(self.one / "skills" / "s" / "notes.md", "not a skill file\n")
        write(self.tmp / "cache" / "older" / "rules" / "old.md", "an older install\n")
        self.user = [write(self.config / "skills" / "u" / "SKILL.md", "user skill\n"),
                     write(self.config / "rigger" / "rules.d" / "lessons.md", "global lessons\n"),
                     write(self.config / "CLAUDE.md", "user rules\n")]

    def installed(self, value):
        text = value if isinstance(value, str) else json.dumps(value)
        write(self.config / "plugins" / "installed_plugins.json", text)

    def good(self):
        return {"one@rigger": [{"installPath": str(self.one)}, {"installPath": str(self.tmp / "cache" / "older")}],
                "two@other": [{"installPath": str(self.two)}]}

    def assert_corpus(self, plugin_files):
        self.assertEqual(no_raise(self, batch_model.corpus_files, self.config), plugin_files + self.user)

    def test_corpus_lists_plugins_user_skills_and_claude_md(self):
        self.installed({"version": 2, "plugins": self.good()})
        self.assert_corpus(self.files)
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        self.assertEqual(batch_model.write_corpus(run_dir, self.config), 7)
        corpus = (run_dir / "corpus.md").read_bytes().decode("utf-8")
        self.assertTrue(corpus.startswith("===== FILE: %s\nrule a\n\n===== FILE: " % self.files[0].as_posix()))
        self.assertTrue(corpus.endswith("===== FILE: %s\nuser rules\n\n" % self.user[2].as_posix()))

    def test_corpus_without_installed_plugins(self):
        self.assert_corpus([])

    def test_corpus_with_invalid_json(self):
        self.installed("{not json")
        self.assert_corpus([])

    def test_corpus_with_list_at_top(self):
        self.installed([self.good()])
        self.assert_corpus([])

    def test_corpus_with_plugins_not_a_dict(self):
        self.installed({"plugins": [self.good()]})
        self.assert_corpus([])

    def test_corpus_with_value_not_a_list(self):
        plugins = self.good()
        plugins["one@rigger"] = {"installPath": str(self.one)}
        self.installed({"plugins": plugins})
        self.assert_corpus(self.files[3:])

    def test_corpus_with_empty_list(self):
        plugins = self.good()
        plugins["one@rigger"] = []
        self.installed({"plugins": plugins})
        self.assert_corpus(self.files[3:])

    def test_corpus_with_item_not_a_dict(self):
        plugins = self.good()
        plugins["one@rigger"] = [str(self.one)]
        self.installed({"plugins": plugins})
        self.assert_corpus(self.files[3:])

    def test_corpus_with_install_path_not_a_string(self):
        plugins = self.good()
        plugins["one@rigger"] = [{"installPath": 7}]
        self.installed({"plugins": plugins})
        self.assert_corpus(self.files[3:])

    def test_corpus_with_relative_install_path(self):
        plugins = self.good()
        plugins["one@rigger"] = [{"installPath": "one"}]
        self.installed({"plugins": plugins})
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(str(self.tmp / "cache"))
        self.assert_corpus(self.files[3:])


class CommandTests(Temp):
    def test_child_env_strips_host_and_sets_guards(self):
        dropped = ["CLAUDE_CODE_MESSAGING_PORT", "CLAUDE_CODE_HOST_ID", "CLAUDE_CODE_SDK_VERSION",
                   "CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID"]
        kept = {"PATH": "p", "CLAUDE_CONFIG_DIR": "c", "CLAUDE_CODE_SESSION": "kept", "HOME": "h"}
        env = batch_model.child_env(dict(kept, **{key: "x" for key in dropped}))
        self.assertEqual(env, dict(kept, CLAUDE_CODE_CHILD_SESSION="1", HARNESS_LESSONS_BATCH="1"))

    def test_claude_argv_from_env_and_missing(self):
        def argv(env):
            return no_raise(self, batch_model.claude_argv, env, "sonnet", {"type": "object"}, 2.0)

        prefix = ["python", "stub.py"]
        env = {"HARNESS_LESSONS_CLAUDE": json.dumps(prefix), "PATH": ""}
        self.assertEqual(argv(env), prefix + FLAGS)
        for wrong in ("not json", '{"a": 1}', "[]", '["python", 7]', '[""]'):
            env["HARNESS_LESSONS_CLAUDE"] = wrong
            self.assertIsNone(argv(env), wrong)
        bin_dir = self.tmp / "bin"
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertIsNone(argv({"PATH": str(empty)}))
        name = "claude.exe" if os.name == "nt" else "claude"
        fake = write(bin_dir / name, "")
        fake.chmod(0o755)
        found = argv({"PATH": str(bin_dir)})
        self.assertEqual((os.path.normcase(found[0]), found[1:]), (os.path.normcase(str(fake)), FLAGS))


class CallTests(Temp):
    def call(self, timeout=60, **step):
        plan = self.tmp / "plan.json"
        log = self.tmp / "calls.jsonl"
        write(plan, json.dumps({"log": str(log), "triage": step}))
        env = batch_model.child_env(stub_claude.clean_env(self.tmp / "home", LESSONS_STUB_PLAN=str(plan),
                                                          CLAUDE_CODE_SESSION_ID="parent"))
        cwd = self.tmp / "run"
        cwd.mkdir(exist_ok=True)
        result = no_raise(self, batch_model.call, stub_claude.stub_argv() + ["-p"],
                          stub_claude.TRIAGE_MARK + " α", env, cwd, timeout)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        return result, calls

    def test_call_structured_output(self):
        result, calls = self.call(structured={"entries": [{"line": 3}]})
        self.assertEqual((result["ok"], result["error"], result["value"]), (True, None, {"entries": [{"line": 3}]}))
        self.assertEqual((result["cost"], result["usage"]), (0.01, {"stub-model": {"inputTokens": 10, "outputTokens": 5}}))
        self.assertIsInstance(result["seconds"], float)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["stage"], calls[0]["prompt"], calls[0]["argv"]),
                         ("triage", stub_claude.TRIAGE_MARK + " α", ["-p"]))
        self.assertEqual(os.path.normcase(calls[0]["cwd"]), os.path.normcase(str(self.tmp / "run")))
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", calls[0]["env"])

    def test_call_result_text_json(self):
        result, _ = self.call(result='Here it is: {"entries": [{"line": 4, "note": "a } b"}]} done')
        self.assertEqual((result["ok"], result["value"]), (True, {"entries": [{"line": 4, "note": "a } b"}]}))

    def test_call_exit_code(self):
        result, _ = self.call(structured={"entries": []}, exit=1)
        self.assertEqual((result["ok"], result["error"], result["value"]), (False, "exit 1", None))

    def test_call_unparsed(self):
        for stdout in ("not json at all", "[1, 2]", json.dumps({"result": "no object here"})):
            result, _ = self.call(stdout=stdout)
            self.assertEqual((result["ok"], result["error"]), (False, "unparsed"), stdout)

    def test_call_timeout_kills_tree(self):
        pids = self.tmp / "pids.txt"
        result, _ = self.call(timeout=5, grandchild=str(pids), sleep=120)
        self.assertEqual((result["ok"], result["error"]), (False, "timeout"))
        stub_pid, grandchild_pid = [int(value) for value in pids.read_text(encoding="utf-8").split()]
        self.assertTrue(stub_claude.wait_for(lambda: not stub_claude.alive(stub_pid), 10), "stub left running")
        self.assertTrue(stub_claude.wait_for(lambda: not stub_claude.alive(grandchild_pid), 10),
                        "grandchild left running")

    def test_call_crlf_output(self):
        result, _ = self.call(crlf=True, structured={"proposals": [{"old_text": "a\r\nb\r\n", "new_text": ["c\r\n"]}]})
        self.assertEqual((result["ok"], result["value"]), (True, {"proposals": [{"old_text": "a\nb\n",
                                                                                 "new_text": ["c\n"]}]}))


class ParseTests(unittest.TestCase):
    LINES = {3, 4}

    def triage(self, value, lines=None):
        return no_raise(self, batch_model.parse_triage, value, self.LINES if lines is None else lines)

    def proposals(self, value):
        return no_raise(self, batch_model.parse_proposals, value, self.LINES)

    def test_parse_triage_keeps_known_items(self):
        value = {"entries": [{"line": 3, "action": "merge", "merge_into": 1, "reason": "same"},
                             {"line": 4, "action": "covered", "covered_by": "x.md#H", "quote": "q", "reason": "r"}]}
        self.assertEqual(self.triage(value), [
            {"line": 3, "action": "merge", "merge_into": 1, "covered_by": "", "quote": "", "reason": "same"},
            {"line": 4, "action": "covered", "merge_into": None, "covered_by": "x.md#H", "quote": "q", "reason": "r"}])

    def test_parse_list_instead_of_dict(self):
        self.assertEqual(self.triage([{"line": 3, "action": "keep"}]), [])
        self.assertEqual(self.proposals([{"line": 3, "kind": "edit", "evidence": "ok"}]), [])

    def test_parse_number_instead_of_list(self):
        self.assertEqual(self.triage({"entries": 3}), [])
        self.assertEqual(self.proposals({"proposals": 3}), [])

    def test_parse_item_not_a_dict(self):
        self.assertEqual(self.triage({"entries": [3, "keep"]}), [])

    def test_parse_line_outside_slice(self):
        self.assertEqual(self.triage({"entries": [{"line": 9, "action": "keep"}]}), [])
        self.assertEqual(self.triage({"entries": [{"line": True, "action": "keep"}]}, lines={1}), [])
        self.assertEqual(self.triage({"entries": [{"line": "3", "action": "keep"}]}), [])
        self.assertEqual(self.proposals({"proposals": [{"line": 9, "kind": "edit", "evidence": "ok"}]}), [])

    def test_parse_duplicate_line(self):
        value = {"entries": [{"line": 3, "action": "keep", "reason": "first"}, {"line": 3, "action": "propose"}]}
        self.assertEqual([item["reason"] for item in self.triage(value)], ["first"])
        value = {"proposals": [{"line": 3, "kind": "edit", "evidence": "ok", "reason": "first"},
                               {"line": 3, "kind": "guard", "evidence": "ok"}]}
        self.assertEqual([item["reason"] for item in self.proposals(value)], ["first"])

    def test_parse_unknown_action(self):
        self.assertEqual(self.triage({"entries": [{"line": 3, "action": "delete"}]}), [])
        self.assertEqual(self.proposals({"proposals": [{"line": 3, "kind": "delete", "evidence": "ok"},
                                                       {"line": 4, "kind": "edit", "evidence": "maybe"}]}), [])

    def test_parse_merge_into_not_a_line(self):
        value = {"entries": [{"line": 3, "action": "merge", "merge_into": "1"}]}
        self.assertIsNone(self.triage(value)[0]["merge_into"])

    def test_parse_proposals_keeps_known_items(self):
        value = {"proposals": [{"line": 4, "kind": "edit", "evidence": "needs-evidence", "target": "t.md",
                                "heading": "H", "old_text": "o", "new_text": "n", "reason": "r", "extra": 1}]}
        self.assertEqual(self.proposals(value), [{"line": 4, "kind": "edit", "evidence": "needs-evidence",
                                                  "target": "t.md", "heading": "H", "old_text": "o", "new_text": "n",
                                                  "reason": "r"}])


class PromptTests(unittest.TestCase):
    def test_prompts_carry_slice_and_step2(self):
        first = "- 2026-01-01" + SEP + "global" + SEP + "case one"
        second = "- 2026-01-02" + SEP + "global" + SEP + "case two"
        inbox = "# Lessons inbox\n\n" + first + "\r\n" + "prose line\n" + second + "\n"
        prompt = batch_model.triage_prompt([(5, second)], inbox)
        self.assertTrue(prompt.startswith(stub_claude.TRIAGE_MARK))
        self.assertIn("(line: entry):\n5: " + second + "\n", prompt)
        self.assertIn("\n3: " + first + "\n", prompt)
        self.assertNotIn("prose line", prompt)
        for phrase in ("Edit nothing", "corpus.md", "older"):
            self.assertIn(phrase, prompt)
        skill = (PLUGIN / "skills" / "curate-lessons" / "SKILL.md").read_text(encoding="utf-8")
        rules = batch_model.step2_rules(skill)
        self.assertTrue(rules.startswith("## Step 2"))
        self.assertNotIn("## Step 3", rules)
        self.assertEqual(batch_model.step2_rules("## Step 1\n\ntext\n"), "")
        items = [{"line": 5, "entry": second, "action": "propose", "reason": "a gap", "covered_by": "", "quote": ""}]
        prompt = batch_model.proposal_prompt(items, rules)
        self.assertTrue(prompt.startswith(stub_claude.PROPOSAL_MARK))
        self.assertIn(rules, prompt)
        self.assertIn("5: " + second, prompt)
        self.assertIn("a gap", prompt)
        self.assertIn("exactly once", prompt)


if __name__ == "__main__":
    unittest.main()
