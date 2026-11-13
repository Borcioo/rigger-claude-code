"""Tests of scripts/select_tests.py: known answers on this repo and a real git tree."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402
import select_tests  # noqa: E402

PY_SH = "plugins/rigger-%s/bin/py.sh"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write(root, rel, text=""):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class RealTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt" and (ROOT / ".git").is_file():
            raise unittest.SkipTest("git cannot follow the .git file of a worktree made on Windows")
        cls.tests = [path.relative_to(ROOT).as_posix() for path in run_tests.discover(ROOT)]
        cls.tracked = select_tests.null_split(select_tests.git(ROOT, "ls-files", "-z"))
        cls.edges = select_tests.build_edges(ROOT, cls.tracked)

    def picked(self, *changed):
        return select_tests.select(list(changed), self.tests, self.edges)

    def test_the_tree_is_read(self):
        self.assertGreater(len(self.tests), 30)
        self.assertGreater(len(self.tracked), 100)
        self.assertGreater(len(self.edges), 30)

    def test_a_tool_picks_its_own_test(self):
        for tool, test in (
            ("plugins/rigger-core/tools/context-guard/compact_guard.py", "test_compact_guard.py"),
            ("plugins/rigger-lessons/tools/lessons_lint.py", "test_lessons_lint.py"),
        ):
            with self.subTest(tool):
                selected, unmapped = self.picked(tool)
                self.assertEqual(unmapped, [])
                self.assertIn(test, [Path(path).name for path in selected])
                self.assertLess(len(selected), len(self.tests))

    def test_the_copy_of_py_sh_under_test_picks_its_test(self):
        selected, unmapped = self.picked(PY_SH % "lessons")
        self.assertEqual(unmapped, [])
        self.assertIn("tests/test_py_sh.py", selected)

    def test_every_other_copy_of_py_sh_runs_the_test_that_compares_them(self):
        for plugin in ("core", "windows"):
            with self.subTest(plugin):
                selected, unmapped = self.picked(PY_SH % plugin)
                self.assertEqual(unmapped, [])
                self.assertIn("tests/test_repo.py", selected)

    def test_a_function_hook_source_picks_the_tests_of_its_plugin(self):
        # Neither file is named in test_panel.py, so only the plugin mapping reaches it.
        for source in ("plugins/rigger-panel/hooks/register.test.tsx", "plugins/rigger-panel/types/index.d.ts"):
            with self.subTest(source):
                selected, unmapped = self.picked(source)
                self.assertEqual(unmapped, [])
                self.assertIn("plugins/rigger-panel/tests/test_panel.py", selected)
                self.assertLess(len(selected), len(self.tests))

    def test_docs_outside_the_shipped_folders_run_the_repo_test_only(self):
        self.assertEqual(self.picked("README.md"), (["tests/test_repo.py"], []))

    def test_a_rules_file_is_not_plain_docs(self):
        selected, unmapped = self.picked("plugins/rigger-rules/rules/tests.md")
        self.assertTrue(selected is None or len(selected) > 1, (selected, unmapped))

    def test_the_repo_test_always_runs(self):
        selected, _ = self.picked("plugins/rigger-lessons/tools/lessons_lint.py")
        self.assertIn(select_tests.ALWAYS, selected)


class SmallTreeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="select-tests-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "test" + "@example.invalid")
        git(self.root, "config", "user.name", "test")
        git(self.root, "config", "core.autocrlf", "false")
        write(self.root, "tests/test_repo.py")
        write(self.root, "tests/test_a.py", "import helper_a\n")
        write(self.root, "tests/test_b.py", "print('data_b.json')\n")
        write(self.root, "scripts/helper_a.py", "import deep_a\n")
        write(self.root, "scripts/deep_a.py")
        write(self.root, "data_b.json", "{}")
        write(self.root, "plugins/one/rules/orphan.md")
        write(self.root, "notes.md")
        write(self.root, "lonely.py")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "first")
        self.tests = ["tests/test_a.py", "tests/test_b.py", "tests/test_repo.py"]

    def select(self):
        return select_tests.selection(self.root, self.tests, "main")

    def branch(self):
        git(self.root, "switch", "-q", "-c", "work")

    def test_a_typescript_file_picks_only_the_tests_inside_its_plugin(self):
        write(self.root, "plugins/one/hooks/unnamed.tsx")
        write(self.root, "plugins/one/tests/test_one.py")
        write(self.root, "plugins/two/tests/test_two.py")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "plugins")
        self.tests += ["plugins/one/tests/test_one.py", "plugins/two/tests/test_two.py"]
        self.branch()
        write(self.root, "plugins/one/hooks/unnamed.tsx", "export {}\n")
        self.assertEqual(self.select()[1:], (["plugins/one/tests/test_one.py", "tests/test_repo.py"], []))

    def shipped_markdown_tree(self, *files):
        # test_one.py builds its path at run time, as a test that reads a SKILL.md does:
        # no edge reaches the file from it. test_lint.py names every file, so each one is mapped.
        write(self.root, "plugins/one/tests/test_one.py", "read(shipped('one'))\n")
        write(self.root, "plugins/one/tools/t/tests/test_tool.py", "pass\n")
        write(self.root, "plugins/two/tests/test_two.py", "read(shipped('two'))\n")
        write(self.root, "tests/test_lint.py", "".join("print(%r)\n" % rel for rel in files))
        for rel in files:
            write(self.root, rel, "text\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "plugins")
        self.tests += [
            "plugins/one/tests/test_one.py",
            "plugins/one/tools/t/tests/test_tool.py",
            "plugins/two/tests/test_two.py",
            "tests/test_lint.py",
        ]
        self.branch()

    def test_shipped_markdown_picks_every_test_inside_its_plugin(self):
        files = [
            "plugins/one/skills/x/SKILL.md",
            "plugins/one/rules/r.md",
            "plugins/one/agents/a.md",
            "plugins/one/commands/c.md",
            "plugins/one/templates/t.md",
        ]
        self.shipped_markdown_tree(*files)
        for rel in files:
            with self.subTest(rel):
                write(self.root, rel, "changed %s\n" % rel)
                picked = self.select()[1:]
                git(self.root, "checkout", "-q", "--", rel)
                self.assertEqual(
                    picked,
                    (
                        [
                            "plugins/one/tests/test_one.py",
                            "plugins/one/tools/t/tests/test_tool.py",
                            "tests/test_lint.py",
                            "tests/test_repo.py",
                        ],
                        [],
                    ),
                )

    def test_other_plugin_files_pick_only_the_tests_that_reach_them(self):
        files = ["plugins/one/README.md", "plugins/one/docs/notes.md", "plugins/one/skills/x/data.json"]
        self.shipped_markdown_tree(*files)
        for rel in files:
            with self.subTest(rel):
                write(self.root, rel, "changed\n")
                picked = self.select()[1:]
                git(self.root, "checkout", "-q", "--", rel)
                self.assertEqual(picked, (["tests/test_lint.py", "tests/test_repo.py"], []))

    def test_shipped_markdown_no_test_reaches_means_the_full_suite_even_with_plugin_tests(self):
        self.shipped_markdown_tree("plugins/one/skills/x/SKILL.md")
        write(self.root, "plugins/one/commands/unread.md", "text\n")
        self.assertEqual(self.select()[1:], (None, ["plugins/one/commands/unread.md"]))

    def test_no_change_runs_only_the_repo_test(self):
        self.assertEqual(self.select(), ([], ["tests/test_repo.py"], []))

    def test_an_import_is_followed_through_every_level(self):
        self.branch()
        write(self.root, "scripts/deep_a.py", "x = 1\n")
        self.assertEqual(self.select()[1], ["tests/test_a.py", "tests/test_repo.py"])

    def test_a_file_named_in_a_test_is_reached(self):
        self.branch()
        write(self.root, "data_b.json", "[]")
        self.assertEqual(self.select()[1], ["tests/test_b.py", "tests/test_repo.py"])

    def test_a_commit_on_the_branch_counts_like_an_edit(self):
        self.branch()
        write(self.root, "scripts/deep_a.py", "x = 1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-q", "-m", "second")
        self.assertEqual(self.select()[0], ["scripts/deep_a.py"])

    def test_an_untracked_file_counts(self):
        self.branch()
        write(self.root, "scripts/new_one.py")
        changed, selected, unmapped = self.select()
        self.assertEqual(changed, ["scripts/new_one.py"])
        self.assertIsNone(selected)
        self.assertEqual(unmapped, ["scripts/new_one.py"])

    def test_a_changed_file_no_test_reaches_means_the_full_suite(self):
        self.branch()
        write(self.root, "lonely.py", "x = 1\n")
        self.assertEqual(self.select()[1:], (None, ["lonely.py"]))

    def test_shipped_markdown_no_test_reaches_means_the_full_suite(self):
        self.branch()
        write(self.root, "plugins/one/rules/orphan.md", "text\n")
        self.assertEqual(self.select()[1:], (None, ["plugins/one/rules/orphan.md"]))

    def test_plain_docs_need_the_repo_test_alone(self):
        self.branch()
        write(self.root, "notes.md", "text\n")
        self.assertEqual(self.select()[1:], (["tests/test_repo.py"], []))

    def test_a_missing_base_raises(self):
        with self.assertRaises(subprocess.CalledProcessError):
            select_tests.selection(self.root, self.tests, "no-such-branch")

    def test_the_default_base_is_main(self):
        self.branch()
        write(self.root, "data_b.json", "[]")
        self.assertEqual(select_tests.selection(self.root, self.tests)[0], ["data_b.json"])


class EdgeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="select-edges-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def edges(self, *files):
        for rel, text in files:
            write(self.root, rel, text)
        return select_tests.build_edges(self.root, [rel for rel, _ in files])

    def test_a_file_that_does_not_parse_still_gives_its_imports(self):
        edges = self.edges(("tests/test_a.py", "import helper\ndef broken(:\n"), ("scripts/helper.py", ""))
        self.assertEqual(edges["tests/test_a.py"], {"scripts/helper.py"})

    def test_an_ambiguous_name_counts_only_with_its_plugin(self):
        edges = self.edges(
            ("plugins/one/tests/test_x.py", "read('shared.txt')\n"),
            ("plugins/one/shared.txt", ""),
            ("plugins/two/shared.txt", ""),
        )
        self.assertEqual(edges["plugins/one/tests/test_x.py"], {"plugins/one/shared.txt"})

    def test_a_name_shorter_than_the_minimum_is_ignored(self):
        edges = self.edges(("tests/test_a.py", "abc\n"), ("abc", ""))
        self.assertEqual(edges["tests/test_a.py"], set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
