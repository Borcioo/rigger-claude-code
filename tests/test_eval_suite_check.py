"""Tests for scripts/eval_suite_check.py: each check catches a planted defect on a temp suite."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_suite_check.py"
sys.path.insert(0, str(ROOT / "scripts"))

import eval_suite_check as esc  # noqa: E402

HEADER = "| case | recorded failure | rule | correct run | grader type | tag |\n|---|---|---|---|---|---|\n"


def cases_md(plugin, names):
    rows = "".join(f"| {n} | src | rule | run | regex | native |\n" for n in names)
    return f"# Inventory\n\n## {plugin}\n\n{HEADER}{rows}\n## Not measurable\n\n| case | reason |\n|---|---|\n| x | y |\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def prose_case(root, name, front="tags: [native]\nruns: 1\n", graders=("a",)):
    d = Path(root) / "evals" / name
    write(d / "prompt.md", f"---\n{front}---\n\nDo the thing.\n")
    for g in graders:
        write(d / "graders" / f"{g}.md", "---\ntype: regex\npattern: x\n---\n")
    return d


def yaml_case(root, name, body=None):
    d = Path(root) / "evals" / name
    if body is None:
        body = "tags: [native]\nruns: 1\nprompt: hi\ngraders:\n  - type: regex\n    pattern: x\n"
    write(d / "case.yaml", body)
    return d


class Suite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "demo-plugin"
        self.root.mkdir()

    def check(self, names, md=None):
        md = md if md is not None else cases_md("demo-plugin", names)
        return esc.check_suite(str(self.root), md)

    def has(self, problems, text):
        self.assertTrue(any(text in p for p in problems), f"{text!r} not in {problems}")


class CheckSuiteTests(Suite):
    def test_clean_prose_suite(self):
        prose_case(self.root, "one")
        self.assertEqual(self.check(["one"]), [])

    def test_clean_yaml_suite(self):
        yaml_case(self.root, "one")
        self.assertEqual(self.check(["one"]), [])

    def test_case_without_prompt_or_yaml(self):
        (self.root / "evals" / "one").mkdir(parents=True)
        self.has(self.check(["one"]), "neither prompt.md nor case.yaml")

    def test_prose_case_without_graders(self):
        prose_case(self.root, "one", graders=())
        self.has(self.check(["one"]), "no graders/*.md")

    def test_yaml_case_without_graders_list(self):
        yaml_case(self.root, "one", "tags: [native]\nruns: 1\nprompt: hi\n")
        self.has(self.check(["one"]), "no graders: list")

    def test_yaml_case_with_empty_graders_list(self):
        yaml_case(self.root, "one", "tags: [native]\nruns: 1\nprompt: hi\ngraders:\n")
        self.has(self.check(["one"]), "no graders: list")

    def test_unknown_frontmatter_key(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 1\nbogus_key: 1\n")
        self.has(self.check(["one"]), "unknown frontmatter key 'bogus_key'")

    def test_case_yaml_keys_are_unknown_in_prose_frontmatter(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 1\ngraders: x\n")
        self.has(self.check(["one"]), "unknown frontmatter key 'graders'")

    def test_unknown_key_in_case_yaml(self):
        yaml_case(self.root, "one", "tags: [native]\nruns: 1\nprompt: hi\nbogus: 1\ngraders:\n  - type: regex\n")
        self.has(self.check(["one"]), "unknown frontmatter key 'bogus'")

    def test_runs_zero(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 0\n")
        self.has(self.check(["one"]), "runs must be an integer above 0")

    def test_runs_not_a_number(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: many\n")
        self.has(self.check(["one"]), "runs must be an integer above 0")

    def test_case_with_no_tag(self):
        prose_case(self.root, "one", front="runs: 1\n")
        self.has(self.check(["one"]), "tagged neither native nor wsl")

    def test_case_with_other_tag(self):
        prose_case(self.root, "one", front="tags: [slow]\nruns: 1\n")
        self.has(self.check(["one"]), "tagged neither native nor wsl")

    def test_shell_grant_without_wsl_tag(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 1\nallowed_tools: [Read, Bash]\n")
        self.has(self.check(["one"]), "grants a shell but is not tagged wsl")

    def test_powershell_grant_without_wsl_tag(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 1\nallowed_tools: [PowerShell]\n")
        self.has(self.check(["one"]), "grants a shell but is not tagged wsl")

    def test_shell_grant_with_pattern_without_wsl_tag(self):
        prose_case(self.root, "one", front="tags: [native]\nruns: 1\nallowed_tools: ['Bash(git:*)']\n")
        self.has(self.check(["one"]), "grants a shell but is not tagged wsl")

    def test_shell_grant_in_case_yaml_block_list(self):
        yaml_case(
            self.root,
            "one",
            "tags: [native]\nruns: 1\nprompt: hi\nallowed_tools:\n  - Bash\ngraders:\n  - type: regex\n",
        )
        self.has(self.check(["one"]), "grants a shell but is not tagged wsl")

    def test_shell_grant_with_wsl_tag_is_clean(self):
        prose_case(self.root, "one", front="tags: [wsl]\nruns: 1\nallowed_tools: [Bash]\n")
        self.assertEqual(self.check(["one"]), [])

    def test_case_directory_missing_from_table(self):
        prose_case(self.root, "one")
        prose_case(self.root, "two")
        self.has(self.check(["one"]), "case 'two' is not in the table")

    def test_table_row_without_directory(self):
        prose_case(self.root, "one")
        self.has(self.check(["one", "ghost"]), "table row 'ghost' has no case directory")

    def test_plugin_section_missing(self):
        prose_case(self.root, "one")
        self.has(self.check([], md=cases_md("other-plugin", ["one"])), "no '## demo-plugin' section")

    def test_more_than_five_cases(self):
        names = [f"c{i}" for i in range(6)]
        for n in names:
            prose_case(self.root, n)
        self.has(self.check(names), "6 cases, at most 5 allowed")

    def test_five_cases_are_allowed(self):
        names = [f"c{i}" for i in range(5)]
        for n in names:
            prose_case(self.root, n)
        self.assertEqual(self.check(names), [])

    def test_results_folder_is_ignored(self):
        prose_case(self.root, "one")
        (self.root / "evals" / "results" / "2026-01-01").mkdir(parents=True)
        self.assertEqual(self.check(["one"]), [])

    def test_no_evals_folder(self):
        self.has(self.check([]), "no evals folder")

    def test_not_measurable_table_is_not_an_inventory(self):
        prose_case(self.root, "x")
        self.has(self.check([], md=cases_md("demo-plugin", [])), "case 'x' is not in the table")


class CliTests(Suite):
    def run_cli(self, md, *roots):
        cases = Path(self._tmp.name) / "CASES.md"
        write(cases, md)
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--cases", str(cases), *map(str, roots)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_clean_suite_prints_summary_and_exits_0(self):
        prose_case(self.root, "one")
        p = self.run_cli(cases_md("demo-plugin", ["one"]), self.root)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("# cases 1, problems 0", p.stdout)

    def test_problem_exits_1_with_reason_line(self):
        prose_case(self.root, "one", front="runs: 1\n")
        p = self.run_cli(cases_md("demo-plugin", ["one"]), self.root)
        self.assertEqual(p.returncode, 1)
        self.assertIn("tagged neither native nor wsl", p.stdout)
        self.assertIn("# cases 1, problems 1", p.stdout)

    def test_zero_cases_exits_1(self):
        (self.root / "evals").mkdir()
        p = self.run_cli(cases_md("demo-plugin", []), self.root)
        self.assertEqual(p.returncode, 1)
        self.assertIn("# cases 0, problems", p.stdout)

    def test_two_plugins_add_up(self):
        other = Path(self._tmp.name) / "two-plugin"
        other.mkdir()
        prose_case(self.root, "one")
        prose_case(other, "uno")
        md = cases_md("demo-plugin", ["one"]) + cases_md("two-plugin", ["uno"])
        p = self.run_cli(md, self.root, other)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("# cases 2, problems 0", p.stdout)

    def test_missing_cases_file_is_a_reason_line_not_a_traceback(self):
        prose_case(self.root, "one")
        p = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--cases", str(Path(self._tmp.name) / "nope.md"), str(self.root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("cannot read", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
