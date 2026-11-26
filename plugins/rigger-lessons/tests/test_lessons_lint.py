"""Tests for tools/lessons_lint.py: a valid synthetic archive passes, each check fails its own fixture,
and the CLI fails closed on a missing or empty archive folder."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
LINT = PLUGIN / "tools" / "lessons_lint.py"
TEMPLATE_DIR = PLUGIN / "templates" / "archive"

_spec = importlib.util.spec_from_file_location("lessons_lint_under_test", LINT)
lessons_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lessons_lint)

CLASSES = [
    "# test vocabulary",
    "",
    "alpha-class: a file written without an explicit encoding",
    "beta-class: a skill rule that was skipped",
    "gamma-class: a deploy step that went on after a failure",
]

A = "2026-01-05-text-default-encoding.md"
B = "2026-02-10-skill-rule-skipped.md"
C = "2026-03-01-encoding-again.md"
D = "2026-04-02-deploy-went-on.md"
E = "2026-05-20-deploy-went-on-elsewhere.md"
A2 = "2026-02-01-encoding-second.md"
SESSION_ID = "1b2c3d4e-0000-4abc-8def-0123456789ab"
# Built at runtime so path scanners do not read these fixtures as real paths.
DRIVE = "F" + ":"
JAN = "/" + "home/jan"
ROUTED_RULES = (
    "plugins/rigger-rules/rules/tests.md",
    "./plugins/rigger-rules/rules/tests.md",
    "plugins\\rigger-talk\\rules\\talk.md",
    "plugins/cache/../rigger-rules/rules/tests.md",
    "/srv/work/rigger/plugins/rigger-rules/rules/tests.md",
    JAN + "/work/rigger/plugins/rigger-rules/rules/tests.md",
    DRIVE + "/Worktrees/live-clone/plugins/rigger-lessons/rules/lessons.md",
    (DRIVE + "/src/any name/Plugins/Rigger-Rules/Rules/Tests.MD").replace("/", "\\"),
)
NOT_ROUTED_RULES = (
    "plugins/rigger-rules/README.md",
    "plugins/rigger-rules/rules/sub/tests.md",
    "plugins/rigger-rules/rules/tests.txt",
    "plugins/rigger-rules/rules/tests.md.bak",
    "myplugins/rigger-rules/rules/tests.md",
    "wp-content/plugins/shop-core/rules/notes.md",
)
INSTALLED_COPIES = (
    "~/.claude/plugins/cache/rigger/rigger-rules/0123abc/rules/tests.md",
    "$CLAUDE_CONFIG_DIR/plugins/cache/rigger/rigger-rules/0123abc/rules/tests.md",
    "plugins/cache/rigger/rigger-rules/0123abc/rules/tests.md",
    "~/.claude/plugins/x/../cache/rigger/rigger-rules/0123abc/rules/tests.md",
    (DRIVE + "/cfg/Plugins/Cache/rigger/rigger-rules/0123abc/rules/tests.md").replace("/", "\\"),
    JAN + "/.claude/plugins/marketplaces/rigger/plugins/rigger-rules/rules/tests.md",
    "/srv/cfg/plugins/marketplaces/rigger/plugins/rigger-rules/rules/tests.md",
)


def valid_cases():
    """name -> {"meta": {key: raw value}, "body": text}: one case per scope and guard form."""
    return {
        A: {
            "meta": {
                "date": "2026-01-05", "project": "demo-app", "stack": "[python]", "tools": "[]",
                "kind": "judgment", "class": "alpha-class", "signature": "open(path, 'w')",
                "guard": "proposed", "status": "rule", "rule": "~/.claude/CLAUDE.md#Pitfalls",
                "scope": "global", "repeat_of": "none", "rule_loaded": "no", "session": "unknown",
            },
            "body": "Symptom: accented letters broke.\n\n"
                    "Proposed (not built): a lint rule that flags open() without encoding=\n",
        },
        B: {
            "meta": {
                "date": "2026-02-10", "project": "demo-app", "stack": "[markdown, python]",
                "tools": "[bash, git]", "kind": "knowledge", "class": "beta-class",
                "signature": '"[x] starts with a bracket and has \\"quotes\\""',
                "error_text": '"ValueError: header line has no colon separator (line 12)"',
                "guard": "live:tests/test_writer.py::test_header_needs_colon",
                "status": "rule", "rule": "skills/writer/SKILL.md#Headers", "scope": "skill:writer",
                "repeat_of": "none", "rule_loaded": "unknown", "session": SESSION_ID,
            },
            "body": "Symptom: the writer skill's header rule was not known.\n",
        },
        C: {
            "meta": {
                "date": "2026-03-01", "project": "other-app", "stack": "[]", "tools": "[]",
                "kind": "shortcut", "class": "alpha-class", "signature": "write_text(",
                "guard": "none", "guard_reason": "a judgement call no check can see in advance",
                "status": "archived", "rule": "none", "scope": "global", "repeat_of": A,
                "rule_loaded": "yes", "session": "unknown",
            },
            "body": "Symptom: the same encoding slip in another project.\n",
        },
        D: {
            "meta": {
                "date": "2026-04-02", "project": "shop-site", "stack": "[]", "tools": "[]",
                "kind": "env-defect", "class": "gamma-class", "signature": "deploy.sh",
                "guard": "live:deploy-exit-check", "probe": "sh deploy.sh --fail-upload",
                "status": "rule", "rule": "CLAUDE.md#Pitfalls", "scope": "project:shop-site", "repeat_of": "none", "rule_loaded": "no", "session": "unknown",
            },
            "body": "Symptom: the deploy went on after a failed upload.\n",
        },
        E: {
            "meta": {
                "date": "2026-05-20", "project": "other-site", "stack": "[]", "tools": "[]",
                "kind": "judgment", "class": "gamma-class", "signature": "release.sh",
                "guard": "proposed", "status": "rule", "rule": "~/.claude/CLAUDE.md#Releases",
                "scope": "global", "repeat_of": D, "rule_loaded": "no", "session": "unknown",
            },
            "body": "Symptom: the second project with the class.\n\n"
                    "Proposed (not built): set -e and a checked exit code after each upload\n",
        },
    }


def case_text(case):
    if "raw" in case:
        return case["raw"]
    lines = ["---"] + ["%s: %s" % (key, value) for key, value in case["meta"].items()]
    lines += case.get("extra", []) + ["---"]
    return "\n".join(lines) + "\n" + case["body"]


WRITER_TESTS = """\
import unittest

# def test_commented_out(self):
<#
Test-Case 'test_in_block_comment'
#>


class WriterTests(unittest.TestCase):
    def test_header_needs_colon(self):
        pass


check("custom_check_name", lambda: True)
check("", lambda: True)
Test-Case 'powershell_case' {
}
"""


def write_archive(folder, cases, classes):
    """The archive in `folder`, plus the test file its test guard names in the folder's parent."""
    folder.mkdir(parents=True, exist_ok=True)
    tests = folder.parent / "tests"
    tests.mkdir(exist_ok=True)
    with open(tests / "test_writer.py", "w", encoding="utf-8", newline="") as handle:
        handle.write(WRITER_TESTS)
    with open(folder / "README.md", "w", encoding="utf-8", newline="") as handle:
        handle.write("# Archive\n\nNot a case file.\n")
    with open(folder / "notes.txt", "w", encoding="utf-8", newline="") as handle:
        handle.write("not a case file either\n")
    if classes is not None:
        with open(folder / "classes.txt", "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(classes) + "\n")
    for name, case in cases.items():
        text = case_text(case)
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        with open(folder / name, "wb") as handle:
            handle.write(data)


def set_meta(name, **values):
    def mutate(cases, classes):
        cases[name]["meta"].update(values)
    return mutate


def drop_meta(name, key):
    def mutate(cases, classes):
        del cases[name]["meta"][key]
    return mutate


def set_case(name, **fields):
    def mutate(cases, classes):
        cases[name].update(fields)
    return mutate


def rename(old, new):
    def mutate(cases, classes):
        cases[new] = cases.pop(old)
    return mutate


def add_case(name, **meta):
    def mutate(cases, classes):
        base = valid_cases()[A]
        base["meta"].update(meta)
        cases[name] = base
    return mutate


def add_class_line(line):
    def mutate(cases, classes):
        classes.append(line)
    return mutate


def drop_classes(cases, classes):
    classes.clear()
    classes.append(None)


CLASSES_FILE = "<classes>"

# check id -> (mutation of the valid archive, [(file name or CLASSES_FILE, message part)] in output order)
CHECKS = {
    "no_frontmatter": (set_case(B, raw="Symptom only, no frontmatter\n"),
                       [(B, "no frontmatter (first line must be ---)")]),
    "frontmatter_not_closed": (set_case(B, raw="---\ndate: 2026-02-10\nSymptom\n"),
                               [(B, "frontmatter is not closed by ---")]),
    "unreadable_frontmatter_line": (set_case(B, extra=["Not A Key here"]),
                                    [(B, "unreadable frontmatter line: 'Not A Key here'")]),
    "duplicate_key": (set_case(B, extra=["kind: judgment"]), [(B, "duplicate key: kind")]),
    "unreadable_value": (set_meta(B, signature='"unterminated'),
                         [(B, "signature: unreadable value ("), (B, "missing key: signature")]),
    "not_utf8": (set_case(B, raw=b"---\ndate: 2026-02-10\n---\nbad byte \xff\n"),
                 [(B, "cannot read as UTF-8 (UnicodeDecodeError")]),
    "classes_bad_line": (add_class_line("Bad Slug: with spaces"),
                         [(CLASSES_FILE, ":6: expected `slug: meaning`, got 'Bad Slug: with spaces'")]),
    "classes_no_meaning": (add_class_line("delta-class:"), [(CLASSES_FILE, ":6: expected `slug: meaning`")]),
    "classes_duplicate": (add_class_line("alpha-class: again"), [(CLASSES_FILE, ":6: duplicate class alpha-class")]),
    "classes_missing": (drop_classes, [(CLASSES_FILE, ": cannot read (")] + [
        (name, "is not in classes.txt") for name in (A, B, C, D, E)]),
    "file_name": (rename(B, "2026-02-10-Bad_Name.md"),
                  [("2026-02-10-Bad_Name.md", "file name must be YYYY-MM-DD-<slug>.md")]),
    "missing_key": (drop_meta(B, "session"), [(B, "missing key: session")]),
    "unknown_key": (set_meta(B, owner="someone"), [(B, "unknown key: owner")]),
    "date_format": (set_meta(B, date="2026-2-10"), [(B, "date is not YYYY-MM-DD: '2026-2-10'"),
                                                   (B, "date 2026-2-10 differs from the file name")]),
    "date_basic_format": (set_meta(B, date="20260210"), [(B, "date is not YYYY-MM-DD: '20260210'"),
                                                        (B, "date 20260210 differs from the file name")]),
    "date_impossible_day": (set_meta(B, date="2026-02-30"), [(B, "date is not YYYY-MM-DD: '2026-02-30'"),
                                                            (B, "date 2026-02-30 differs from the file name")]),
    "date_differs_from_name": (set_meta(B, date="2026-02-11"), [(B, "date 2026-02-11 differs from the file name")]),
    "list_type": (set_meta(B, stack="python"), [(B, "stack must be a [list]")]),
    "kind_enum": (set_meta(B, kind="oops"), [(B, "kind must be one of env-defect, knowledge, judgment, shortcut, got 'oops'")]),
    "class_unknown": (set_meta(B, **{"class": "zeta-class"}), [(B, "class 'zeta-class' is not in classes.txt")]),
    "non_empty_string": (set_meta(B, signature=""), [(B, "signature must be a non-empty string")]),
    "error_text_short": (set_meta(B, error_text='"Error 404 at C:/srv/app/x.log"'),
                         [(B, "error_text is shorter than 15 characters after masking: 'Error <n> at <path>'")]),
    "rule_loaded_kind": (set_meta(B, rule_loaded="yes", kind="judgment"),
                         [(B, "rule_loaded yes means the rule was known: kind shortcut, not judgment")]),
    "guard_format": (set_meta(B, guard="maybe"), [(B, "guard must be none, proposed or live:<rule id>, got 'maybe'")]),
    "guard_live_empty": (set_meta(B, guard="live: "), [(B, "guard must be none, proposed or live:<rule id>, got 'live:'")]),
    "guard_test_not_under_tests": (set_meta(B, guard="live:src/writer.py::test_header_needs_colon"),
                                   [(B, "guard live:src/writer.py::test_header_needs_colon: src/writer.py is not a file "
                                        "under a tests/ folder")]),
    "guard_test_file_missing": (set_meta(B, guard="live:tests/test_absent.py::test_header_needs_colon"),
                                [(B, "guard live:tests/test_absent.py::test_header_needs_colon: test file "
                                     "tests/test_absent.py not found")]),
    "guard_test_name_missing": (set_meta(B, guard="live:tests/test_writer.py::test_absent"),
                                [(B, "guard live:tests/test_writer.py::test_absent: no `def test_absent(`, "
                                     "`check(\"test_absent\"` or `Test-Case 'test_absent'` in tests/test_writer.py")]),
    "guard_test_name_empty": (set_meta(B, guard="live:tests/test_writer.py::"),
                              [(B, "guard live:tests/test_writer.py::: no `def (`")]),
    "guard_test_commented_out": (set_meta(B, guard="live:tests/test_writer.py::test_commented_out"),
                                 [(B, "guard live:tests/test_writer.py::test_commented_out: no `def test_commented_out(`")]),
    "guard_test_in_block_comment": (set_meta(B, guard="live:tests/test_writer.py::test_in_block_comment"),
                                    [(B, "guard live:tests/test_writer.py::test_in_block_comment: no ")]),
    "probe_without_live": (set_meta(A, probe="echo x"), [(A, "probe is only for a live rule guard (live:<rule id>, not a test)")]),
    "probe_with_test_guard": (set_meta(B, probe="echo x"),
                              [(B, "probe is only for a live rule guard (live:<rule id>, not a test)")]),
    "guard_reason_not_none": (set_meta(A, guard_reason="because"), [(A, "guard_reason is only for guard: none, not proposed")]),
    "proposed_line_missing": (set_case(A, body="Symptom: no proposal line.\n"),
                              [(A, "guard proposed needs a body line `Proposed (not built): <mechanism>`")]),
    "proposed_line_short": (set_case(A, body="Proposed (not built): TODO\n"),
                            [(A, "guard proposed needs a body line `Proposed (not built): <mechanism>`")]),
    "status_enum": (set_meta(B, status="done"), [(B, "status must be one of rule, archived, fixed, got 'done'")]),
    "status_rule_needs_path": (set_meta(B, rule="none"), [(B, "status rule needs a rule path, not none")]),
    "scope_format": (set_meta(B, scope="team"), [(B, "scope must be global, skill:<name> or project:<name>, got 'team'")]),
    "scope_project_mismatch": (set_meta(D, scope="project:other-shop"),
                               [(D, "scope project:other-shop names another project than shop-site")]),
    "scope_skill_path": (set_meta(B, rule="docs/writer.md#Headers"), [(B, "scope skill:writer needs its rule under skills/writer/")]),
    "scope_skill_other_skill": (set_meta(B, rule="skills/other/SKILL.md#Headers"),
                                [(B, "scope skill:writer needs its rule under skills/writer/")]),
    "scope_global_in_skill": (set_meta(A, rule="~/.claude/skills/writer/SKILL.md#Rules"),
                              [(A, "scope global but the rule is in a skill: scope skill:writer")]),
    "scope_global_outside_config": (set_meta(A, rule="docs/CLAUDE.md#Pitfalls"),
                                    [(A, "scope global needs its rule in ~/.claude, $CLAUDE_CONFIG_DIR or a harness "
                                         "repo (plugins/rigger-<name>/rules/<file>.md in any clone), not docs/CLAUDE.md")]),
    "scope_project_in_config": (set_meta(D, rule="~/.claude/CLAUDE.md#Deploys"),
                                [(D, "scope project:shop-site needs its rule in that project, not ~/.claude/CLAUDE.md")]),
    "project_class_promoted": (set_meta(E, project="shop-site"),
                               [(E, "scope global: class gamma-class has a project-scoped case and cases from one "
                                    "project only (shop-site); keep it project:shop-site")]),
    "rule_loaded_enum": (set_meta(B, rule_loaded="maybe"), [(B, "rule_loaded must be one of yes, no, unknown, got 'maybe'")]),
    "session_format": (set_meta(B, session="abc"), [(B, "session must be a session id or unknown, got 'abc'")]),
    "repeat_missing": (set_meta(C, repeat_of="2026-01-01-nothing.md"),
                       [(C, "repeat_of '2026-01-01-nothing.md' is not a case file here")]),
    "repeat_not_earlier": (set_meta(A, repeat_of=C), [(A, "repeat_of %s is not earlier than this case" % C)]),
    "repeat_other_class": (set_meta(C, repeat_of=B), [(C, "repeat_of %s has another class (beta-class)" % B)]),
    "repeat_not_latest": (add_case(A2, date="2026-02-01", repeat_of=A),
                          [(C, "repeat_of %s is not the latest earlier case of the class (%s)" % (A, A2))]),
    "repeat_needs_guard": (drop_meta(C, "guard_reason"), [(C, "a repeat needs a guard: proposed or live, or none with a guard_reason")]),
    "empty_body": (set_case(B, body="\n  \n"), [(B, "empty body")]),
    "list_where_string_belongs": (set_meta(B, **{"class": "[beta-class, x]", "repeat_of": "[x]"}),
                                  [(B, "class ['beta-class', 'x'] is not in classes.txt"),
                                   (B, "repeat_of ['x'] is not a case file here")]),
}


class LessonsLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lessons-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = self.tmp / "archive"

    def build(self, mutate=None):
        cases, classes = valid_cases(), list(CLASSES)
        if mutate:
            mutate(cases, classes)
        write_archive(self.archive, cases, None if classes == [None] else classes)
        return cases

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-I", str(LINT)] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.tmp),
            timeout=60,
        )
        return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n"), proc.stderr

    def test_valid_archive_passes(self):
        self.build()
        self.assertEqual(lessons_lint.lint(str(self.archive)), (5, []))
        code, out, err = self.run_cli("--archive", str(self.archive))
        self.assertEqual((code, err), (0, b""), out)
        self.assertEqual(out, "# 5 case files checked, 0 problems\n")

    def test_failing_archive_cli_exit_1_with_path_lines(self):
        self.build(set_meta(B, kind="oops"))
        code, out, err = self.run_cli("--archive", str(self.archive))
        self.assertEqual((code, err), (1, b""), out)
        self.assertEqual(
            out.splitlines(),
            [
                "%s: kind must be one of env-defect, knowledge, judgment, shortcut, got 'oops'"
                % os.path.join(str(self.archive), B),
                "# 5 case files checked, 1 problems",
            ],
        )

    def test_classes_option(self):
        self.build(drop_classes)
        elsewhere = self.tmp / "vocab.txt"
        with open(elsewhere, "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(CLASSES) + "\n")
        code, out, _ = self.run_cli("--archive", str(self.archive), "--classes", str(elsewhere))
        self.assertEqual(code, 0, out)

    def test_missing_archive_exit_2(self):
        code, out, _ = self.run_cli("--archive", str(self.tmp / "nowhere"))
        self.assertEqual(code, 2, out)
        self.assertIn("# archive folder not found: ", out)

    def test_archive_without_case_files_exit_2(self):
        write_archive(self.archive, {}, CLASSES)
        code, out, _ = self.run_cli("--archive", str(self.archive))
        self.assertEqual(code, 2, out)
        self.assertIn("# no case files in ", out)

    def test_template_archive_is_empty_and_its_classes_parse(self):
        code, out, _ = self.run_cli("--archive", str(TEMPLATE_DIR))
        self.assertEqual(code, 2, out)
        slugs, problems = lessons_lint.read_classes(str(TEMPLATE_DIR / "classes.txt"))
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(slugs), 10)

    def test_directory_named_like_a_case_is_skipped(self):
        self.build()
        (self.archive / "2026-06-01-a-folder.md").mkdir()
        self.assertEqual(lessons_lint.lint(str(self.archive)), (5, []))

    def test_test_guard_file_not_utf8(self):
        self.build()
        with open(self.tmp / "tests" / "test_writer.py", "wb") as handle:
            handle.write(b"def test_header_needs_colon(self):\n    pass  # caf\xe9\n")
        code, out, err = self.run_cli("--archive", str(self.archive))
        self.assertEqual((code, err), (1, b""), out)
        self.assertEqual(
            out.splitlines()[0],
            "%s: guard live:tests/test_writer.py::test_header_needs_colon: test file tests/test_writer.py "
            "is not UTF-8" % os.path.join(str(self.archive), B),
        )

    def test_dangling_symlink_case_is_a_problem(self):
        self.build()
        link = self.archive / "2026-06-01-dangling.md"
        try:
            os.symlink(str(self.tmp / "nowhere.md"), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink on %s: %s" % (sys.platform, exc))
        count, problems = lessons_lint.lint(str(self.archive))
        self.assertEqual(count, 6)
        self.assertEqual(len(problems), 1, "\n".join(problems))
        self.assertTrue(problems[0].startswith(str(link) + ": cannot read"), problems[0])

    def test_repo_option_and_default(self):
        self.build()
        repo = self.tmp / "elsewhere"
        shutil.move(str(self.tmp / "tests"), str(repo / "tests"))
        guard_line = "%s: guard live:tests/test_writer.py::test_header_needs_colon: test file " % os.path.join(
            str(self.archive), B)
        count, problems = lessons_lint.lint(str(self.archive))
        self.assertEqual([p[: len(guard_line)] for p in problems], [guard_line])
        code, out, _ = self.run_cli("--archive", str(self.archive), "--repo", str(repo))
        self.assertEqual(code, 0, out)

    def test_test_guard_forms_accepted(self):
        self.build()
        for name in ("test_header_needs_colon", "custom_check_name", "powershell_case"):
            with self.subTest(name=name):
                self.assertIsNone(lessons_lint.guard_problem("live:tests/test_writer.py::" + name, str(self.tmp)))
        self.assertIsNone(lessons_lint.guard_problem("live:any-rule-id", str(self.tmp)))

    def test_config_root_spellings(self):
        def problems(rule):
            meta = {"scope": "global", "project": "p", "rule": rule + "#Heading"}
            return lessons_lint.scope_problem(meta), lessons_lint.scope_problem(dict(meta, scope="project:p"))

        for rule in ("~/.claude/CLAUDE.md", "$CLAUDE_CONFIG_DIR/CLAUDE.md", "${CLAUDE_CONFIG_DIR}/CLAUDE.md",
                     "/home/someone/.claude/CLAUDE.md", "E:\\home\\someone\\.Claude\\CLAUDE.md",
                     "$HOME/.claude/CLAUDE.md", "/src/rigger-clone/plugins/rigger-rules/rules/tests.md"):
            with self.subTest(config_rule=rule):
                path = rule.replace("\\", "/")
                self.assertEqual(
                    problems(rule), (None, "scope project:p needs its rule in that project, not %s" % path)
                )
        for rule in ("docs/CLAUDE.md", ".claude/CLAUDE.md", "$CLAUDE_CONFIG_DIRS/CLAUDE.md", "/srv/x.claude/CLAUDE.md",
                     "/srv/site/.claude/CLAUDE.md", "/src/rigger-copy/CLAUDE.md", "/src/claude-skills/README.md"):
            with self.subTest(project_rule=rule):
                self.assertEqual(
                    problems(rule), ("scope global needs its rule in %s, not %s" % (lessons_lint.HARNESS_WHERE, rule), None)
                )

    def scope_problems(self, rule):
        meta = {"scope": "global", "project": "p", "rule": rule + "#Heading"}
        return lessons_lint.scope_problem(meta), lessons_lint.scope_problem(dict(meta, scope="project:p"))

    def test_routed_rules_file_is_global_whatever_the_clone_is_called(self):
        for rule in ROUTED_RULES:
            with self.subTest(rule=rule):
                global_problem, project_problem = self.scope_problems(rule)
                self.assertIsNone(global_problem)
                self.assertIn("needs its rule in that project, not ", str(project_problem))

    def test_other_files_in_a_clone_are_not_the_routed_rules_file(self):
        for rule in NOT_ROUTED_RULES:
            with self.subTest(rule=rule):
                global_problem, project_problem = self.scope_problems(rule)
                self.assertIn("scope global needs its rule in ", str(global_problem))
                self.assertIsNone(project_problem)

    def test_installed_copy_is_refused(self):
        for rule in INSTALLED_COPIES:
            with self.subTest(rule=rule):
                meta = {"scope": "global", "project": "p", "rule": rule + "#Heading"}
                problem = str(lessons_lint.scope_problem(meta))
                self.assertTrue(problem.startswith("scope global: "), problem)
                self.assertTrue(problem.endswith(" is an installed copy that the next update replaces; "
                                                 "use the plugin's source repo"), problem)

    def test_installed_skill_copy_is_refused(self):
        meta = {"scope": "skill:git-workflow", "project": "p",
                "rule": "~/.claude/plugins/cache/rigger/rigger-core/0123abc/skills/git-workflow/SKILL.md#Commits"}
        self.assertIn("is an installed copy", str(lessons_lint.scope_problem(meta)))

    def test_project_folder_shaped_like_a_cache_stays_the_projects(self):
        meta = {"scope": "project:p", "project": "p", "rule": "wp-content/plugins/cache/a/b/c/notes.md#Heading"}
        self.assertIsNone(lessons_lint.scope_problem(meta))

    def test_cli_on_a_routed_path_and_on_an_installed_copy(self):
        for rule, code in ((ROUTED_RULES[0], 0), (ROUTED_RULES[6], 0), (INSTALLED_COPIES[0], 1)):
            with self.subTest(rule=rule):
                self.build(set_meta(A, rule=rule + "#Tests"))
                got, out, err = self.run_cli("--archive", str(self.archive))
                self.assertEqual((got, err), (code, b""), out)
                self.assertTrue(out.endswith("# 5 case files checked, %d problems\n" % code), out)

    def test_rule_guard_routing(self):
        """A live:<rule id> guard in a case whose tools name a shell needs a confirming rule checker; any
        other rule id is accepted as written."""
        self.build()
        checker = self.tmp / "checker" / "lessons_guard.py"
        checker.parent.mkdir()
        with open(checker, "w", encoding="utf-8", newline="") as handle:
            handle.write("def rule_guard_problem(rule, meta):\n"
                         "    return None if meta.get('probe') == 'ok' else 'guard live:%s: denied' % rule\n")
        modules, problems = lessons_lint.load_checkers([str(checker)])
        self.assertEqual((len(modules), problems), (1, []))
        shell = {"tools": ["git", "bash"], "probe": "ok"}
        self.assertIsNone(lessons_lint.guard_problem("live:r", str(self.tmp), {"tools": ["git"], "probe": "x"}))
        self.assertIn("none is loaded", lessons_lint.guard_problem("live:r", str(self.tmp), shell))
        self.assertIsNone(lessons_lint.guard_problem("live:r", str(self.tmp), shell, modules))
        self.assertEqual(lessons_lint.guard_problem("live:r", str(self.tmp), dict(shell, probe="no"), modules),
                         "guard live:r: denied")

    def test_claimed_rule_id_goes_to_its_checker_whatever_the_tools(self):
        """A rule id in a checker's RULES is checked by that checker even when the case's tools name no
        shell; an id no checker claims stays accepted there."""
        self.build()
        folder = self.tmp / "checkers"
        folder.mkdir()
        sources = {"claims.py": "RULES = frozenset({'r'})\n"
                                "def rule_guard_problem(rule, meta):\n    return 'guard live:%s: claimed' % rule\n",
                   "other.py": "RULES = ('q',)\n"
                               "def rule_guard_problem(rule, meta):\n    return 'guard live:%s: other' % rule\n",
                   # a string is no set of rule ids: it claims nothing, not even its own substrings
                   "string.py": "RULES = 'r'\n"
                                "def rule_guard_problem(rule, meta):\n    return 'guard live:%s: string' % rule\n"}
        for name, source in sources.items():
            with open(folder / name, "w", encoding="utf-8", newline="") as handle:
                handle.write(source)
        modules, problems = lessons_lint.load_checkers([str(folder / name) for name in sources])
        self.assertEqual((len(modules), problems), (3, []))
        no_shell = {"tools": ["composer"], "probe": "x"}
        self.assertEqual(lessons_lint.guard_problem("live:r", str(self.tmp), no_shell, modules),
                         "guard live:r: claimed")
        self.assertEqual(lessons_lint.guard_problem("live:r", str(self.tmp), {}, modules), "guard live:r: claimed")
        self.assertIsNone(lessons_lint.guard_problem("live:unclaimed", str(self.tmp), no_shell, modules))
        self.assertEqual(lessons_lint.guard_problem("live:unclaimed", str(self.tmp), dict(no_shell, tools=["bash"]),
                                                    modules),
                         "guard live:unclaimed: claimed; guard live:unclaimed: other; guard live:unclaimed: string")

    def test_old_rule_id_is_claimed_through_aliases(self):
        """An old id among the ALIASES keys of shellwise's rule_checker.py is claimed like a RULES id, with only
        that checker in the repo (no lessons_guard.py beside it): a case whose tools name no shell is judged by
        it, not accepted."""
        self.build(set_meta(D, guard="live:bash-too-long", tools="[composer]"))
        real = PLUGIN.parent / "rigger-shellwise" / "tools" / "rule-checker" / "rule_checker.py"
        self.assertTrue(real.is_file(), real)
        checker = self.tmp / "plugins" / "rigger-shellwise" / "tools" / "rule-checker" / "rule_checker.py"
        checker.parent.mkdir(parents=True)
        shutil.copyfile(str(real), str(checker))
        self.assertEqual(lessons_lint.find_checkers([str(self.tmp)]), [str(checker)])
        saved = {key: os.environ.pop(key) for key in ("CLAUDE_PLUGIN_DATA",) if key in os.environ}
        saved["CLAUDE_CONFIG_DIR"] = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "no-config")   # never the real plugin data
        try:
            result = lessons_lint.lint(str(self.archive))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(result, (5, ["%s: guard live:bash-too-long: tools names no shell (powershell, bash, git-bash) "
                                      "to run the probe in" % os.path.join(str(self.archive), D)]))
        # ALIASES that is no mapping claims nothing, not even a string equal to the id
        shaped = self.tmp / "string_aliases.py"
        with open(shaped, "w", encoding="utf-8", newline="") as handle:
            handle.write("ALIASES = 'bash-too-long'\n"
                         "def rule_guard_problem(rule, meta):\n    return 'guard live:%s: string' % rule\n")
        modules, problems = lessons_lint.load_checkers([str(shaped)])
        self.assertEqual((len(modules), problems), (1, []))
        self.assertIsNone(lessons_lint.guard_problem("live:bash-too-long", str(self.tmp),
                                                     {"tools": ["composer"], "probe": "x"}, modules))

    def test_rule_checker_found_in_repo_through_a_link(self):
        """A --repo given as a junction (Windows) or symlink finds its plugins/*/tools/*/lessons_guard.py and
        plugins/*/tools/*/rule_checker.py, and its test guards resolve through the link."""
        self.build()
        real = self.tmp / "real-repo"
        sources = {"plugins/p/tools/t/lessons_guard.py": "from the linked repo",
                   "plugins/s/tools/rule-checker/rule_checker.py": "from its rule_checker.py"}
        for rel, said in sources.items():
            checker = real.joinpath(*rel.split("/"))
            checker.parent.mkdir(parents=True)
            with open(checker, "w", encoding="utf-8", newline="") as handle:
                handle.write("def rule_guard_problem(rule, meta):\n    return 'guard live:%%s: %s' %% rule\n" % said)
        shutil.move(str(self.tmp / "tests"), str(real / "tests"))
        link = self.tmp / "linked-repo"
        make_dir_link(link, real)
        self.assertEqual(sorted(Path(p).name for p in lessons_lint.find_checkers([str(link)])),
                         ["lessons_guard.py", "rule_checker.py"])
        self.build(set_meta(D, tools="[powershell]"))
        shutil.rmtree(str(self.tmp / "tests"))
        count, problems = lessons_lint.lint(str(self.archive), repo=str(link))
        self.assertEqual(count, 5)
        self.assertEqual(problems, ["%s: guard live:deploy-exit-check: from the linked repo; "
                                    "guard live:deploy-exit-check: from its rule_checker.py" % os.path.join(
                                        str(self.archive), D)])

    def test_a_rule_checker_py_confirms_by_glob_and_by_flag(self):
        """plugins/*/tools/*/rule_checker.py in a --repo is a rule checker, and so is one given by --rule-checker;
        one that confirms the probe clears the guard."""
        self.build(set_meta(D, tools="[powershell]"))
        source = ("RULES = frozenset({'deploy-exit-check'})\n"
                  "def rule_guard_problem(rule, meta):\n"
                  "    return None if meta.get('probe') == 'sh deploy.sh --fail-upload' else 'guard live:%s: no' % rule\n")
        repo = self.tmp / "repo"
        in_repo = repo / "plugins" / "s" / "tools" / "rule-checker" / "rule_checker.py"
        given = self.tmp / "elsewhere" / "rule_checker.py"
        for path in (in_repo, given):
            path.parent.mkdir(parents=True)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(source)
        self.assertEqual(lessons_lint.find_checkers([str(self.tmp), str(repo)]), [str(in_repo)])
        self.assertEqual(lessons_lint.lint(str(self.archive), repo=[str(self.tmp), str(repo)]), (5, []))
        # without a checker the same case is a problem
        self.assertEqual([p.split(", so ")[0] for p in lessons_lint.lint(str(self.archive), repo=str(self.tmp))[1]],
                         ["%s: guard live:deploy-exit-check: the case's tools name a shell" % os.path.join(
                             str(self.archive), D)])
        code, out, err = self.run_cli("--archive", str(self.archive), "--repo", str(self.tmp),
                                      "--rule-checker", str(given))
        self.assertEqual((code, err), (0, b""), out)
        self.assertTrue(out.endswith("# 5 case files checked, 0 problems\n"), out)

    def test_cli_repo_and_checker_must_exist(self):
        self.build()
        for args in (["--repo", str(self.tmp / "no-repo")], ["--rule-checker", str(self.tmp / "no-checker.py")]):
            with self.subTest(args=args[0]):
                code, out, _ = self.run_cli("--archive", str(self.archive), *args)
                self.assertEqual(code, 2, out)
                self.assertIn("# not found: ", out)


CORE_SOURCE = PLUGIN.parent / "rigger-core"
TEAM_RULE = "/srv/team-rules/rules/git.md"
TEAM_GLOB = "/srv/team-rules/rules/*.md"


class AllowedPathsTests(unittest.TestCase):
    """D97: config key lessons.allowed_paths (levels 2-3) adds path patterns where a global rule may live."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lessons-paths-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = self.tmp / "archive"
        self.config = self.tmp / "config"
        self.project = self.tmp / "project"
        self.project.mkdir()
        cases = valid_cases()
        cases[A]["meta"]["rule"] = TEAM_RULE + "#Commits"
        write_archive(self.archive, cases, CLASSES)

    def install_core(self):
        """A copy of rigger-core laid out like the plugin cache, listed in installed_plugins.json."""
        root = self.config / "plugins" / "cache" / "rigger" / "rigger-core" / "1.0.0"
        skip = shutil.ignore_patterns("tests", "__pycache__")
        shutil.copytree(str(CORE_SOURCE / "tools" / "assemble"), str(root / "tools" / "assemble"), ignore=skip)
        for name in ("rigger-module.json", "rigger-defaults.json"):
            shutil.copy(str(CORE_SOURCE / name), str(root / name))
        listing = {"version": 2, "plugins": {"rigger-core@rigger": [{"installPath": os.path.abspath(str(root))}]}}
        self.write_json(self.config / "plugins" / "installed_plugins.json", listing)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(data).encode("utf-8"))

    def personal(self, lessons):
        self.write_json(self.config / "rigger" / "config.json", {"schema": 1, "lessons": lessons})

    def company_module(self, lessons):
        """A company module folder whose config sets `lessons` (level 2), for RIGGER_MODULE_DIRS."""
        root = self.tmp / "team-module"
        self.write_json(root / ".claude-plugin" / "plugin.json", {"name": "team-rules"})
        self.write_json(root / "rigger-module.json", {"schema": 1, "name": "team-rules", "layer": "company",
                                                      "config": {"lessons": lessons}})
        return os.path.abspath(str(root))

    def run_cli(self, extra=None, cwd=None):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("CLAUDE_", "RIGGER_"))}
        env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env.update(extra or {})
        proc = subprocess.run([sys.executable, "-I", str(LINT), "--archive", str(self.archive)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(cwd or self.project), env=env, timeout=60)
        return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n").splitlines(), proc.stderr

    def refused(self):
        return "%s: scope global needs its rule in %s, not %s" % (
            os.path.join(str(self.archive), A), lessons_lint.HARNESS_WHERE, TEAM_RULE)

    def test_config_paths_extend_the_allowed_set(self):
        self.install_core()
        code, lines, err = self.run_cli()
        self.assertEqual((code, err, lines[0]), (1, b"", self.refused()), lines)  # control: the fixed set refuses it
        self.personal({"allowed_paths": [TEAM_GLOB]})
        code, lines, err = self.run_cli()
        self.assertEqual((code, err, lines), (0, b"", ["# 5 case files checked, 0 problems"]))
        self.personal({})
        module = {"RIGGER_MODULE_DIRS": self.company_module({"allowed_paths": [TEAM_GLOB]})}
        code, lines, err = self.run_cli(module)  # level 2: a company module's config
        self.assertEqual((code, err, lines), (0, b"", ["# 5 case files checked, 0 problems"]))
        meta = {"scope": "project:p", "project": "p", "rule": TEAM_RULE}
        self.assertIsNone(lessons_lint.scope_problem(meta, [TEAM_GLOB]))  # a project may still keep its rule there
        spelled = {"scope": "global", "project": "p", "rule": "\\".join(("", "SRV", "Team-Rules", "rules", "Git.md#Commits"))}
        self.assertIsNone(lessons_lint.scope_problem(spelled, [TEAM_GLOB]))  # backslashes and case, as HARNESS
        self.assertIsNotNone(lessons_lint.scope_problem(dict(spelled, rule="/srv/team-rules/git.md"), [TEAM_GLOB]))

    def test_bad_entries_are_dropped_with_a_notice(self):
        self.install_core()
        where = str(self.config / "rigger" / "config.json")
        rows = [
            ("not a list", "/srv/x/*.md", ["rigger: config %s lessons.allowed_paths must be a list of globs; ignored"
                                          % where]),
            ("a number and an empty glob", [7, "", TEAM_GLOB],
             ["rigger: config %s lessons.allowed_paths entry 7 ignored: must be a non-empty string" % where,
              "rigger: config %s lessons.allowed_paths entry '' ignored: must be a non-empty string" % where]),
            ("a line break", ["/srv/a\n.md", TEAM_GLOB],
             ["rigger: config %s lessons.allowed_paths entry '/srv/a\\n.md' ignored: holds a line break "
              "or control character" % where]),
            ("too long", ["/srv/" + "a" * 200, TEAM_GLOB],
             ["rigger: config %s lessons.allowed_paths entry %s ignored: over 200 characters"
              % (where, ("'/srv/" + "a" * 200)[:79] + "…")]),
            ("over the cap", ["/srv/n%d/*.md" % n for n in range(50)] + [TEAM_GLOB],
             ["rigger: config %s holds 51 lessons.allowed_paths entries; the last 1 ignored (at most 50 per file)"
              % where]),
            ("an unknown key", {"allowed_paths": [TEAM_GLOB], "extra": 1}, None),
        ]
        for label, paths, notices in rows:
            with self.subTest(row=label):
                if notices is None:
                    self.personal(paths)
                    notices = ["rigger: config %s lessons key extra unknown; ignored" % where]
                else:
                    self.personal({"allowed_paths": paths})
                globs, got, reason = lessons_lint.allowed_paths(str(self.config), str(self.project), {})
                self.assertEqual((got, reason), (notices, None))
                self.assertEqual(TEAM_GLOB in globs, label not in ("not a list", "over the cap"), globs)
                code, lines, _ = self.run_cli()
                self.assertEqual(lines[:len(notices)], ["# " + notice for notice in notices])

    def test_no_rigger_core_uses_the_fixed_set_and_says_so(self):
        self.personal({"allowed_paths": [TEAM_GLOB]})
        code, lines, err = self.run_cli()
        self.assertEqual((code, err), (1, b""), lines)
        self.assertEqual(lines[0], "# lessons.allowed_paths not read (no plugin list); the fixed set applies")
        self.assertEqual(lines[1], self.refused())
        self.install_core()
        listing = self.config / "plugins" / "installed_plugins.json"
        root = json.loads(listing.read_bytes())["plugins"]["rigger-core@rigger"][0]["installPath"]
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(str(self.config))
        relative = os.path.relpath(root)  # exists from this cwd, but an installPath must be absolute
        rows = [
            ("another plugin only", {"plugins": {"rigger-lessons@rigger": [{"installPath": root}]}},
             "no rigger-core in the plugin list"),
            ("a relative installPath", {"plugins": {"rigger-core@rigger": [{"installPath": relative}]}},
             "no tools/assemble/config.py in an installPath of rigger-core@rigger"),
        ]
        for label, data, reason in rows:
            with self.subTest(row=label):
                self.write_json(listing, data)
                self.assertEqual(lessons_lint.allowed_paths(str(self.config), str(self.project), {}), ([], [], reason))
        self.write_json(listing, {"plugins": {"rigger-core@rigger": [{"installPath": root}]}})
        (Path(root) / "tools" / "assemble" / "config.py").write_bytes(b"raise ValueError('broken copy')\n")
        globs, notices, reason = lessons_lint.allowed_paths(str(self.config), str(self.project), {})
        self.assertEqual((globs, notices, reason), ([], [], "rigger-core did not load (ValueError: broken copy)"))

    def test_a_wrongly_shaped_plugin_list_uses_the_fixed_set(self):
        self.install_core()
        self.personal({"allowed_paths": [TEAM_GLOB]})
        listing = self.config / "plugins" / "installed_plugins.json"
        root = json.loads(listing.read_bytes())["plugins"]["rigger-core@rigger"][0]["installPath"]
        no_core, no_path = "no rigger-core in the plugin list", "no tools/assemble/config.py in an installPath of rigger-core@rigger"
        rows = [
            ("a list file", [{"rigger-core@rigger": [{"installPath": root}]}], no_core),
            ("plugins a number", {"plugins": 7}, no_core),
            ("plugins a list of names", {"plugins": ["rigger-core@rigger"]}, no_core),
            ("its row a number", {"plugins": {"rigger-core@rigger": 7}}, no_path),
            ("its row item a string", {"plugins": {"rigger-core@rigger": [root]}}, no_path),
        ]
        for label, data, reason in rows:
            with self.subTest(row=label):
                self.write_json(listing, data)
                code, lines, err = self.run_cli()
                self.assertEqual((code, err, lines[:2]), (1, b"", [
                    "# lessons.allowed_paths not read (%s); the fixed set applies" % reason, self.refused()]))

    def test_level_4_is_ignored(self):
        self.install_core()
        self.write_json(self.project / ".claude" / "rigger.json", {"schema": 1, "lessons": {"allowed_paths": [TEAM_GLOB]}})
        linked = self.tmp / "linked-project"
        make_dir_link(linked, self.project)  # macOS temp folders sit behind /var -> /private/var
        for label, folder in (("the folder", self.project), ("a link to it", linked)):
            with self.subTest(row=label):
                code, lines, err = self.run_cli(cwd=folder)
                self.assertEqual((code, err), (1, b""), lines)
                project_file = os.path.join(cwd_as_seen(folder), ".claude", "rigger.json")
                self.assertEqual(lines[:2], ["# rigger: config %s key lessons not allowed at level 4; ignored"
                                             % project_file, self.refused()])


def cwd_as_seen(folder):
    """What os.getcwd() returns inside `folder`, the project path the lint reads and prints: POSIX resolves
    symlinks there, Windows keeps a junction's own path."""
    saved = os.getcwd()
    os.chdir(str(folder))
    try:
        return os.getcwd()
    finally:
        os.chdir(saved)


def make_dir_link(link, target):
    """A directory junction on Windows (no admin rights needed), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


# name -> (error text, its masked form); expected values match the mask the archive is matched with
MASK_CASES = {
    "whitespace_folded": ("Error:\n   bad \t value", "Error: bad value"),
    "mysql_account": ("Access denied for user 'deploy'@'localhost'", "Access denied for user '<user>'@'<host>'"),
    "url_credentials": ("fatal: https://bob:pass@example.com/repo.git", "fatal: https://<cred>@example.com/repo.git"),
    "url_credentials_upper_scheme": ("fatal: HTTPS://bob:pass@example.com/repo.git",
                                     "fatal: HTTPS://<cred>@example.com/repo.git"),
    "email": ("mail to jan.nowak@example.org failed", "mail to <email> failed"),
    "email_short_parts": ("mail to a@b.example failed", "mail to <email> failed"),
    "email_not_package": ("cannot resolve eslint@latest here", "cannot resolve eslint@latest here"),
    "email_not_digit_last_label": ("cannot resolve pkg@1.2.30 here", "cannot resolve pkg@<n>.<n>.<n> here"),
    "email_not_letters_then_digit": ("cannot resolve pkg@1.2.rc1 here", "cannot resolve pkg@<n>.<n>.rc<n> here"),
    "query": ("GET /api?token=abc&x=1 failed", "GET /api?<query> failed"),
    "windows_path": ("cannot open C:\\temp\\app\\x.txt now", "cannot open <path> now"),
    "windows_path_not_after_letters": ("see abcC:\\temp\\x now", "see abcC:\\temp\\x now"),
    "windows_path_not_after_digit": ("see ab9C:\\temp\\x now", "see ab<n>C:\\temp\\x now"),
    "windows_path_not_double_slash": ("open C://host/share now", "open C://host/share now"),
    "windows_path_not_double_backslash": ("open C:\\\\share\\x now", "open C:\\\\share\\x now"),
    "posix_path": ("cannot open /var/log/app.log now", "cannot open <path> now"),
    "posix_path_not_in_url": ("GET http://host/path/more failed", "GET http://host/path/more failed"),
    "posix_path_not_in_word": ("open src/app/x now", "open src/app/x now"),
    "posix_path_not_after_colon": ("mount host:/srv/data/x now", "mount host:/srv/data/x now"),
    "posix_path_not_after_tag": ("see <pre>/var/log/x here", "see <pre>/var/log/x here"),
    "hex_0x": ("fault at 0xDEADBEEF here", "fault at <hex> here"),
    "uuid": ("session 1b2c3d4e-0000-4abc-8def-0123456789ab gone", "session <uuid> gone"),
    "hex_run": ("commit 3f2a9c1d not found", "commit <hex> not found"),
    "hex_run_seven": ("git 3f2a9c1 here", "git <hex> here"),
    "hex_run_six_is_digits": ("ref 3f2a9c ok", "ref <n>f<n>a<n>c ok"),
    "hex_run_letters_only_kept": ("word deadbeefcafe here", "word deadbeefcafe here"),
    "long_token": ("key 4eC39HqLyjWDarjtT1zdp7dc rejected", "key <token> rejected"),
    "long_token_padding_one": ("key Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MDE= rejected", "key <token> rejected"),
    "long_token_padding_two": ("key Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA== rejected", "key <token> rejected"),
    "long_token_padding_three_kept": ("key Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA=== rejected",
                                      "key Zm<n>vYmFyYmF<n>cXV<n>MTIzNDU<n>Nzg<n>MA=== rejected"),
    "long_option_kept": ("unknown --retry-count-for-http2-uploads flag", "unknown --retry-count-for-http<n>-uploads flag"),
    "long_slug_kept": ("repo project-alpha-platform-23c3-mirror missing",
                       "repo project-alpha-platform-<n>c<n>-mirror missing"),
    "long_without_digit_kept": ("ERR_PNPM_OUTDATED_LOCKFILE_SOMETHING failed", "ERR_PNPM_OUTDATED_LOCKFILE_SOMETHING failed"),
    "long_without_letter_is_digits": ("id 123456789012345678901234567 bad", "id <n> bad"),
    "digits": ("exit code 127", "exit code <n>"),
}


def make_mask_test(text, expected):
    def test(self):
        self.assertEqual(lessons_lint.mask(text), expected)

    return test


for _name, (_text, _expected) in MASK_CASES.items():
    setattr(LessonsLintTests, "test_mask_" + _name, make_mask_test(_text, _expected))


def make_check_test(check_id, mutate, expected):
    def test(self):
        self.build(mutate)
        count, problems = lessons_lint.lint(str(self.archive))
        self.assertGreater(count, 0)
        self.assertEqual(len(problems), len(expected), "\n".join(problems))
        for line, (where, part) in zip(problems, expected):
            prefix = str(self.archive / "classes.txt") if where == CLASSES_FILE else os.path.join(str(self.archive), where) + ": "
            self.assertTrue(line.startswith(prefix), line)
            self.assertIn(part, line)

    test.__name__ = "test_check_" + check_id
    return test


for _check_id, (_mutate, _expected) in CHECKS.items():
    setattr(LessonsLintTests, "test_check_" + _check_id, make_check_test(_check_id, _mutate, _expected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
