"""Checks for weak_test_guard.py, run as the hook runs it (`python -I -S <script>`, the PreToolUse event on
stdin) against real git repositories in temp folders; one case runs the shipped hooks.json command through
`sh` and the launcher.

python -I test_weak_test_guard.py
"""
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "weak_test_guard.py"
CORE = HERE.parent.parent.parent
HOOKS = CORE / "hooks" / "hooks.json"
REASON = "Test-change: the old parser and its tests are gone"

PY_TEST = '''import parser


def test_parses_dates():
    assert parser.parse("2026-01-01") == (2026, 1, 1)


def test_rejects_garbage():
    assert parser.parse("x") is None
'''
PY_CODE = '''def parse(text):
    return None


def parse_legacy(text):
    return None
'''
JS_TEST = '''import { sum } from "../src/sum";

describe("sum", () => {
  it("adds two numbers", () => {
    expect(sum(1, 2)).toBe(3);
  });

  it("adds negatives", () => {
    expect(sum(-1, -2)).toBe(-3);
  });
});
'''
PHP_TEST = '''<?php
class CartTest extends TestCase
{
    public function test_total_includes_tax(): void
    {
        $this->assertSame(123, $this->cart()->total());
    }

    public function test_empty_cart_is_zero(): void
    {
        $this->assertSame(0, (new Cart())->total());
    }
}
'''
PEST_TEST = '''<?php

it('totals the cart', function () {
    expect((new Cart())->total())->toBe(0);
});
'''
GO_TEST = '''package cart

import "testing"

func TestTotal(t *testing.T) {
\tif Total() != 3 {
\t\tt.Fatalf("want 3")
\t}
}

func TestEmpty(t *testing.T) {
\tif Empty() != 0 {
\t\tt.Errorf("want 0")
\t}
}
'''


EMPTY_GITCONFIG = []  # the one empty file git() names as the global config, made on first use


def empty_gitconfig():
    if not EMPTY_GITCONFIG:
        folder = tempfile.mkdtemp(prefix="fixture-gitconfig-")
        atexit.register(shutil.rmtree, folder, True)
        path = Path(folder) / "gitconfig"
        path.write_bytes(b"")
        EMPTY_GITCONFIG.append(str(path))
    return EMPTY_GITCONFIG[0]


def git(cwd, *args):
    """git in a fixture repository: no inherited GIT_* variable, no system or user config."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=empty_gitconfig(),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t" + "@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t" + "@example.invalid")
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"] + list(args), cwd=cwd,
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8", "replace")


def run_hook(command, cwd, tool="Bash", event=None, env=None):
    if event is None:
        event = {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(cwd)}
    proc = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT)], input=json.dumps(event).encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=env)
    return proc


def without_editors():
    """The environment with every editor variable removed, so `core.editor` of the repository decides (Claude Code
    sets GIT_EDITOR=true, which would hide an editor run)."""
    return {k: v for k, v in os.environ.items() if k not in ("GIT_EDITOR", "VISUAL", "EDITOR", "GIT_SEQUENCE_EDITOR")}


def reason_of(proc):
    """The denial reason, or None when the hook allowed (no output)."""
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout:
        return None
    assert b"\r\n" not in proc.stdout, proc.stdout
    data = json.loads(proc.stdout.decode("ascii"))
    out = data["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


class Repo:
    def __init__(self, case, files):
        self.root = Path(tempfile.mkdtemp(prefix="weak test guard "))
        case.addCleanup(shutil.rmtree, self.root, True)
        git(self.root, "init", "-q", "-b", "develop")
        self.write(files)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def write(self, files):
        for name, text in files.items():
            path = self.root / name
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))

    def change(self, files, stage=True):
        self.write(files)
        if stage:
            git(self.root, "add", "-A")


def plain_git(cwd, *args):
    """git with no inherited GIT_* variable and no user config: the oracle of FixtureGitTests."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t")
    proc = subprocess.run(["git"] + list(args), cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout.decode("utf-8").strip()


class FixtureGitTests(unittest.TestCase):
    """The fixture's git acts on the folder it is given, whatever the caller's GIT_* variables and git config say."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fixture-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "f.txt").write_bytes(b"f\n")

    def build(self):
        git(self.repo, "init", "-q", "-b", "develop")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def test_an_inherited_git_dir_does_not_move_the_fixture(self):
        victim = self.tmp / "victim"
        victim.mkdir()
        plain_git(victim, "init", "-q", "-b", "main")
        plain_git(victim, "commit", "-q", "--allow-empty", "-m", "victim")
        before = plain_git(victim, "rev-parse", "HEAD")
        # GIT_WORK_TREE too, so every step succeeds and a leak shows as a victim commit, not a git error
        with mock.patch.dict(os.environ, {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(self.repo)}):
            self.build()
        self.assertEqual((plain_git(victim, "rev-parse", "HEAD"), plain_git(victim, "status", "--porcelain")),
                         (before, ""))
        self.assertEqual(plain_git(self.repo, "log", "--format=%s"), "base")

    def test_the_user_git_config_is_not_read(self):
        home = self.tmp / "home"
        template = self.tmp / "template"
        template.mkdir()
        (template / "planted-by-config").write_bytes(b"x\n")
        home.mkdir()
        (home / ".gitconfig").write_bytes(("[init]\n\ttemplateDir = %s\n" % template.as_posix()).encode("utf-8"))
        fake_home = {"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / "xdg")}
        # Control: plain git with that home reads the file and copies the template.
        control = self.tmp / "control"
        control.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(fake_home)
        subprocess.run(["git", "init", "-q"], cwd=str(control), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60, check=True)
        self.assertTrue((control / ".git" / "planted-by-config").is_file())
        with mock.patch.dict(os.environ, fake_home):
            self.build()
        self.assertFalse((self.repo / ".git" / "planted-by-config").exists())


BASE = {
    "src/parser.py": PY_CODE,
    "tests/test_parser.py": PY_TEST,
    "src/sum.js": "export const sum = (a, b) => a + b;\n",
    "tests/sum.test.js": JS_TEST,
    "src/Cart.php": "<?php\nclass Cart { public function total() { return 0; } }\n",
    "tests/Unit/CartTest.php": PHP_TEST,
    "cart/cart.go": "package cart\n\nfunc Total() int { return 3 }\nfunc Empty() int { return 0 }\n",
    "cart/cart_test.go": GO_TEST,
    "tests/Feature/TotalsTest.php": PEST_TEST,
}


def drop(text, start, end):
    """text without the lines from the one holding `start` up to and including the one holding `end`."""
    lines = text.split("\n")
    first = next(i for i, line in enumerate(lines) if start in line)
    last = next(i for i, line in enumerate(lines) if i >= first and end in line)
    return "\n".join(lines[:first] + lines[last + 1:])


# (case, changed files) -> the findings a denial must name.
WEAKENINGS = [
    ("python test function removed", {"tests/test_parser.py": drop(PY_TEST, "def test_rejects", "is None")},
     ["removed test `test_rejects_garbage`"]),
    ("js it() removed", {"tests/sum.test.js": drop(JS_TEST, 'it("adds negatives"', "toBe(-3)")},
     ["removed test `adds negatives`"]),
    ("phpunit method removed", {"tests/Unit/CartTest.php": drop(PHP_TEST, "test_empty_cart", "(new Cart())")},
     ["removed test `test_empty_cart_is_zero`"]),
    ("go test removed", {"cart/cart_test.go": drop(GO_TEST, "func TestEmpty", "want 0")},
     ["removed test `TestEmpty`"]),
    ("test file deleted", {"tests/sum.test.js": None},
     ["removed tests `adds two numbers`, `adds negatives` in tests/sum.test.js (file deleted)"]),
    ("pytest skip", {"tests/test_parser.py": PY_TEST.replace("def test_rejects", "@pytest.mark.skip\ndef test_rejects")},
     ["added skip in tests/test_parser.py: @pytest.mark.skip"]),
    ("unittest skipTest", {"tests/test_parser.py": PY_TEST.replace('    assert parser.parse("x")', '    self.skipTest("later")\n    assert parser.parse("x")')},
     ["added skip"]),
    ("jest it.skip", {"tests/sum.test.js": JS_TEST.replace('it("adds negatives"', 'it.skip("adds negatives"')},
     ["added skip in tests/sum.test.js"]),
    ("jest describe.only", {"tests/sum.test.js": JS_TEST.replace('describe("sum"', 'describe.only("sum"')},
     ["added skip"]),
    ("mocha xit", {"tests/sum.test.js": JS_TEST.replace('it("adds negatives"', 'xit("adds negatives"')},
     ["added skip"]),
    ("playwright fixme", {"tests/sum.test.js": JS_TEST.replace("    expect(sum(-1", "    test.fixme();\n    expect(sum(-1")},
     ["added skip"]),
    ("phpunit markTestSkipped", {"tests/Unit/CartTest.php": PHP_TEST.replace("        $this->assertSame(0", "        $this->markTestSkipped('flaky');\n        $this->assertSame(0")},
     ["added skip"]),
    ("pest skip", {"tests/Feature/TotalsTest.php": PEST_TEST.replace("});\n", "})->skip();\n")}, ["added skip"]),
    ("go t.Skip", {"cart/cart_test.go": GO_TEST.replace("\tif Empty()", "\tt.Skip(\"later\")\n\tif Empty()")},
     ["added skip"]),
    ("python assert removed", {"tests/test_parser.py": PY_TEST.replace('    assert parser.parse("x") is None\n', "    parser.parse(\"x\")\n")},
     ["assertions in test files: 1 removed, 0 added"]),
    ("js expect commented out", {"tests/sum.test.js": JS_TEST.replace("    expect(sum(-1", "    // expect(sum(-1")},
     ["assertions in test files: 1 removed, 0 added"]),
    ("php assert removed", {"tests/Unit/CartTest.php": PHP_TEST.replace("        $this->assertSame(0, (new Cart())->total());\n", "        (new Cart())->total();\n")},
     ["assertions in test files: 1 removed, 0 added"]),
    ("go t.Fatalf removed", {"cart/cart_test.go": GO_TEST.replace('\t\tt.Fatalf("want 3")\n', "")},
     ["assertions in test files: 1 removed, 0 added"]),
]

LEGACY_TEST = PY_TEST + '\n\ndef test_parse_legacy_dates():\n    assert parser.parse_legacy("1.1.26") is None\n'
HELPER = '\n\ndef windows_only():\n    if os.name != "nt":\n        pytest.skip("Windows only")\n'
NEW_FILE = ('import os\nimport pytest\n\n\ndef windows_only():\n    if os.name != "nt":\n        pytest.skip("Windows only")\n\n\n'
            'def test_drive():\n    windows_only()\n    assert True\n')

# (case, files the base commit holds besides BASE, the change) -> no denial.
ALLOWED = [
    ("test removed with the function it covered", {"tests/test_parser.py": LEGACY_TEST},
     {"src/parser.py": PY_CODE.replace("\n\ndef parse_legacy(text):\n    return None\n", ""), "tests/test_parser.py": PY_TEST}),
    ("test file deleted with its source file", {}, {"src/sum.js": None, "tests/sum.test.js": None}),
    ("phpunit file deleted with its class", {"app/Cart.php": "<?php\nclass Cart {}\n"},
     {"app/Cart.php": None, "tests/Unit/CartTest.php": None}),
    ("test renamed in place", {}, {"tests/test_parser.py": PY_TEST.replace("test_rejects_garbage", "test_rejects_text")}),
    ("test moved to another file", {},
     {"tests/test_parser.py": drop(PY_TEST, "def test_rejects", "is None"),
      "tests/test_garbage.py": 'import parser\n\n\ndef test_rejects_garbage():\n    assert parser.parse("x") is None\n'}),
    ("test added", {}, {"tests/test_parser.py": PY_TEST + '\n\ndef test_more():\n    assert parser.parse("") is None\n'}),
    ("assertions grow", {},
     {"tests/test_parser.py": PY_TEST.replace('    assert parser.parse("x") is None\n', '    assert parser.parse("x") is None\n    assert parser.parse("y") is None\n')}),
    ("skip helper moved to another file",
     {"tests/test_parser.py": PY_TEST + HELPER, "tests/helpers.py": "import os\n"},
     {"tests/test_parser.py": PY_TEST, "tests/helpers.py": "import os\n" + HELPER}),
    ("skip inside a new test file", {}, {"tests/test_windows.py": NEW_FILE}),
    ("skip inside a new test", {}, {"tests/test_parser.py": PY_TEST + '\n\n' + '@pytest.mark.skipif(True, reason="x")\ndef test_new():\n    assert True\n'}),
    ("collection only() and skip(1) in a Pest test", {},
     {"tests/Unit/CartTest.php": PHP_TEST.replace("        $this->assertSame(0,", "        $rows = collect([1, 2])->skip(1)->only([0]);\n        $this->assertSame(0,")}),
    ("vendored test code", {"vendor/acme/lib/tests/LibTest.php": "<?php\nfunction test_a() {\n}\n"},
     {"vendor/acme/lib/tests/LibTest.php": "<?php\nfunction test_a() {\n    $this->markTestSkipped('x');\n}\n"}),
    ("code only", {}, {"src/parser.py": PY_CODE.replace("return None", "return ()", 1)}),
]


class WeakeningTests(unittest.TestCase):
    def test_each_weakening_is_denied_with_its_finding(self):
        for name, files, wanted in WEAKENINGS:
            with self.subTest(case=name):
                repo = Repo(self, BASE)
                repo.change(files)
                reason = reason_of(run_hook('git commit -m "fix"', repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: "), reason)
                for item in wanted:
                    self.assertIn(item, reason)
                self.assertIn("`Test-change: <why>`", reason)

    def test_the_same_commit_passes_with_a_reason(self):
        for name, files, _ in WEAKENINGS:
            with self.subTest(case=name):
                repo = Repo(self, BASE)
                repo.change(files)
                self.assertIsNone(reason_of(run_hook('git commit -m "fix" -m "%s"' % REASON, repo.root)))

    def test_changes_that_weaken_nothing_pass(self):
        for name, base, files in ALLOWED:
            with self.subTest(case=name):
                repo = Repo(self, dict(BASE, **base))
                repo.change(files)
                self.assertIsNone(reason_of(run_hook('git commit -m "change"', repo.root)))


WEAK = {"tests/test_parser.py": drop(PY_TEST, "def test_rejects", "is None")}


def load_guard():
    spec = importlib.util.spec_from_file_location("weak_test_guard_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()


def file_diff(path, removed=(), added=(), new=False):
    """A one-hunk unified diff of one file, as git diff -U0 prints it."""
    head = "diff --git a/%s b/%s\n%s--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n" % (
        path, path, "new file mode 100644\n" if new else "", path, path)
    return head + "".join("-%s\n" % line for line in removed) + "".join("+%s\n" % line for line in added)


# One row per alternative of the skip table: (file, removed lines, added lines). Each must be found.
SKIP_ROWS = [
    ("tests/test_a.py", (), ("@pytest.mark.skip",)),
    ("tests/test_a.py", (), ("@pytest.mark.skipif(sys.platform == 'win32', reason='x')",)),
    ("tests/test_a.py", (), ("@pytest.mark.xfail",)),
    ("tests/test_a.py", (), ("pytestmark = pytest.mark.skip(reason='later')",)),
    ("tests/test_a.py", (), ("@mark.skip",)),
    ("tests/test_a.py", (), ("@skip('later')",)),
    ("tests/test_a.py", (), ("@skipIf(True, 'x')",)),
    ("tests/test_a.py", (), ("@unittest.skip('x')",)),
    ("tests/test_a.py", (), ("@unittest.skipIf(True, 'x')",)),
    ("tests/test_a.py", (), ("@unittest.skipUnless(False, 'x')",)),
    ("tests/test_a.py", (), ("@unittest.expectedFailure",)),
    ("tests/test_a.py", (), ("    pytest.skip('x')",)),
    ("tests/test_a.py", (), ("    pytest.xfail('x')",)),
    ("tests/test_a.py", (), ("    self.skipTest('x')",)),
    ("tests/test_a.py", (), ("    raise unittest.SkipTest('x')",)),
    ("tests/test_a.py", (), ("    raise SkipTest('x')",)),
    ("tests/a.test.ts", ("  it('x', () => {",), ("  it.skip('x', () => {",)),
    ("tests/a.test.ts", ("  test('x', () => {",), ("  test.only('x', () => {",)),
    ("tests/a.test.ts", (), ("describe.skip('suite', () => {",)),
    ("tests/a.test.ts", (), ("context.only('suite', () => {",)),
    ("tests/a.test.ts", (), ("suite.skip('suite', () => {",)),
    ("tests/a.test.ts", (), ("  test.fixme();",)),
    ("tests/a.test.ts", (), ("  it.todo('later');",)),
    ("tests/a.test.ts", (), ("describe.skipIf(isWindows)('suite', () => {",)),
    ("tests/a.test.ts", (), ("describe.runIf(isLinux)('suite', () => {",)),
    ("tests/a.test.ts", (), ("  xit('x', () => {",)),
    ("tests/a.test.ts", (), ("  xtest('x', () => {",)),
    ("tests/a.test.ts", (), ("xdescribe('suite', () => {",)),
    ("tests/a.test.ts", (), ("xcontext('suite', () => {",)),
    ("tests/a.test.ts", (), ("  fit('x', () => {",)),
    ("tests/a.test.ts", (), ("fdescribe('suite', () => {",)),
    ("tests/Unit/ATest.php", (), ("        $this->markTestSkipped('x');",)),
    ("tests/Unit/ATest.php", (), ("        $this->markTestIncomplete('x');",)),
    ("tests/Feature/ATest.php", (), ("})->skip();",)),
    ("tests/Feature/ATest.php", (), ("})->skip('later');",)),
    ("tests/Feature/ATest.php", (), ("})->todo();",)),
    ("tests/Feature/ATest.php", (), ("})->only();",)),
    ("a/a_test.go", (), ("\tt.Skip(\"x\")",)),
    ("a/a_test.go", (), ("\tt.Skipf(\"x %d\", 1)",)),
    ("a/a_test.go", (), ("\tt.SkipNow()",)),
]
NOT_SKIPS = ["        $rows = $c->skip(1)->only(['a']);", "        $rows = collect($x)->skip(2);", "  // it.skip('x')",
             "    # @pytest.mark.skip", "    (\"tests/test_a.py\", (), (\"@pytest.mark.skip\",)),", "  const marker = `it.skip(`;"]

# One row per alternative of the assertion table: (file, removed line). Each must count once.
ASSERT_ROWS = [
    ("tests/test_a.py", "    self.assertEqual(a, b)"),
    ("tests/test_a.py", "    assert a == b"),
    ("tests/test_a.py", "    mock.assert_called_once()"),
    ("tests/test_a.py", "    with pytest.raises(ValueError):"),
    ("tests/a.test.ts", "  expect(a).toBe(b);"),
    ("tests/a.test.ts", "  assert.equal(a, b);"),
    ("tests/a.test.ts", "  assert(a);"),
    ("tests/Unit/ATest.php", "        $this->assertSame(1, $a);"),
    ("tests/Unit/ATest.php", "        self::assertSame(1, $a);"),
    ("tests/Unit/ATest.php", "        $response->assertOk();"),
    ("tests/Unit/ATest.php", "        $this->expectException(E::class);"),
    ("a/a_test.go", "\t\tt.Errorf(\"x\")"),
    ("a/a_test.go", "\t\tt.Fatal(\"x\")"),
    ("a/a_test.go", "\t\tt.FailNow()"),
    ("a/a_test.go", "\trequire.Equal(t, 1, a)"),
]
NOT_ASSERTS = ["expect()->extend('toBeOne', fn () => 1);", "| use expect($value) to assert things", "    # assert a",
               "    message = 'assert x == 1 failed' + \"self.assertTrue(\""]


class PatternTests(unittest.TestCase):
    def test_each_skip_form_is_found(self):
        for path, removed, added in SKIP_ROWS:
            with self.subTest(added=added[0]):
                found = GUARD.findings(file_diff(path, removed, added))
                self.assertEqual([line for line in found if line.startswith("added skip")],
                                 ["added skip in %s: %s" % (path, added[0].strip())])

    def test_lookalikes_are_no_skip(self):
        for line in NOT_SKIPS:
            with self.subTest(line=line):
                self.assertEqual(GUARD.findings(file_diff("tests/Unit/ATest.php", (), (line,))), [])

    def test_each_assertion_form_counts_once(self):
        for path, line in ASSERT_ROWS:
            with self.subTest(line=line):
                self.assertEqual(GUARD.findings(file_diff(path, (line,))), ["assertions in test files: 1 removed, 0 added"])

    def test_lookalikes_are_no_assertion(self):
        for line in NOT_ASSERTS:
            with self.subTest(line=line):
                self.assertEqual(GUARD.findings(file_diff("tests/Pest.php", (line,))), [])

    def test_a_phpunit_method_marked_as_test_is_a_test(self):
        for mark in ("    /** @test */", "    #[Test]", r"    #[\PHPUnit\Framework\Attributes\Test]"):
            with self.subTest(mark=mark):
                found = GUARD.findings(file_diff("tests/Unit/ATest.php", (mark, "    public function it_totals(): void", "    {", "    }")))
                self.assertEqual(found, ["removed test `it_totals` in tests/Unit/ATest.php"])

    def test_a_removed_method_covers_the_test_named_after_it(self):
        diff = file_diff("app/Jira.php", ("    public function pushToJira($ticket)",)) + file_diff(
            "tests/Feature/TicketTest.php", ("it('push to jira fails without an integration', function () {",
                                             "    $this->post('/x')->assertStatus(422);", "});"))
        self.assertEqual(GUARD.findings(diff), [])

    def test_cases_folded_into_a_table_are_no_weakening(self):
        for path, removed, added in (
                ("tests/test_a.py", ("def test_a1():", "    assert f(1) == 1", "def test_a2():", "    assert f(2) == 2"),
                 ("@pytest.mark.parametrize('n', [1, 2])", "def test_a(n):", "    assert f(n) == n")),
                ("tests/Feature/ATest.php", ("it('adds one', function () {", "    expect(f(1))->toBe(1);", "});",
                                             "it('adds two', function () {", "    expect(f(2))->toBe(2);", "});"),
                 ("it('adds', function (int $n) {", "    expect(f($n))->toBe($n);", "})->with([1, 2]);")),
                ("tests/a.test.js", ("it('adds one', () => {", "  expect(f(1)).toBe(1);", "});",
                                     "it('adds two', () => {", "  expect(f(2)).toBe(2);", "});"),
                 ("it.each([1, 2])('adds %i', (n) => {", "  expect(f(n)).toBe(n);", "});"))):
            with self.subTest(path=path):
                self.assertEqual(GUARD.findings(file_diff(path, removed, added)), [])

    def test_a_mock_expectation_or_a_loop_is_no_table(self):
        for path, removed, line in (
                ("tests/Unit/ATest.php", ("    public function test_b(): void", "    {", "        $this->assertTrue(true);", "    }"),
                 "        $m->shouldReceive('x')->with(1);"),
                ("tests/a.test.js", ("it('b', () => {", "  expect(1).toBe(1);", "});"), "  [1].each((x) => x);")):
            with self.subTest(path=path):
                found = GUARD.findings(file_diff(path, removed, (line,)))
                self.assertTrue(found and found[0].startswith("removed test"), found)

    def test_a_method_name_the_removed_test_only_calls_covers_nothing(self):
        diff = file_diff("app/Http/Controllers/PostController.php", ("    public function create()",)) + file_diff(
            "tests/Feature/PostTest.php", ("it('lists posts', function () {", "    User::factory()->create();",
                                           "    $this->get('/posts')->assertOk();", "});"))
        self.assertEqual(GUARD.findings(diff), ["removed test `lists posts` in tests/Feature/PostTest.php",
                                                "assertions in test files: 1 removed, 0 added"])

    def test_a_removed_test_marker_is_a_removed_test(self):
        for added in ((), ("    /** @group slow */",)):
            with self.subTest(added=added):
                found = GUARD.findings(file_diff("tests/Unit/ATest.php", ("    /** @test */",), added))
                self.assertEqual(found, ["removed test marker in tests/Unit/ATest.php: /** @test */"])
        self.assertEqual(GUARD.findings(file_diff("tests/test_marks.py", ('MARKS = ("    /** @test */", "#[Test]")',))), [])

    def test_a_test_file_renamed_out_of_the_pattern_is_found(self):
        diff = ("diff --git a/src/sum.test.js b/src/sum.test.disabled.js\nsimilarity index 100%\n"
                "rename from src/sum.test.js\nrename to src/sum.test.disabled.js\n")
        self.assertEqual(GUARD.findings(diff), ["test file renamed to a name no runner collects: "
                                                "src/sum.test.js -> src/sum.test.disabled.js"])

    def test_a_short_name_after_folding_covers_no_test(self):
        # `__lt__` folds to `lt`, which is part of `testrequestdefault`: too short to say which test covered it.
        removed_test = file_diff("tests/test_req.py", ("def test_request_default():", "    assert make() == 1"))
        alone = GUARD.findings(removed_test)
        self.assertEqual(alone, ["removed test `test_request_default` in tests/test_req.py",
                                 "assertions in test files: 1 removed, 0 added"])
        for line in ("    def __eq__(self, other):", "    def __lt__(self, other):", "def _____(x):"):
            with self.subTest(line=line):
                self.assertEqual(GUARD.findings(file_diff("src/model.py", (line,)) + removed_test), alone)

    def test_a_test_file_of_each_js_extension_is_read(self):
        for path in ("src/a.test.mts", "src/a.spec.cts", "src/a.test.mjs", "src/a.test.ts"):
            with self.subTest(path=path):
                found = GUARD.findings(file_diff(path, ("it('adds', () => {", "  expect(1).toBe(1);", "});")))
                self.assertEqual(found, ["removed test `adds` in %s" % path, "assertions in test files: 1 removed, 0 added"])

    def test_a_removed_local_variable_covers_no_test(self):
        diff = file_diff("src/a.py", ("    result = (a, b)", "    const value = compute(1)")) + file_diff(
            "tests/test_a.py", ("def test_result_value():", "    print(result, value)"))
        self.assertEqual(GUARD.findings(diff), ["removed test `test_result_value` in tests/test_a.py"])

    def test_regexes_stay_linear_on_repeated_tokens(self):
        tokens = [" ", "\t", "-", "--ab", "(", ")", "'", '"', "`", "it(", "it('", "test_", "def ", "func (", "const x = ",
                  "->", "::", ".", "assert", "expect(", "@", "#[", "/", "x", "a1", "|", "*", "export ",
                  "$(", '"$(', "<<'EOF'\n", "cat <<", "\nEOF\n", "$((", "$'", "git ", "git -c a ", "-c ", " commit",
                  "env ", "X=1 ", "sh -c '"]
        patterns = [GUARD.TEST_DEF, GUARD.SKIP, GUARD.ASSERT, GUARD.DEFINES, GUARD.COMMENT, GUARD.DECORATOR,
                    GUARD.PHP_METHOD, GUARD.PHP_TEST_MARK, GUARD.TEST_FILE, GUARD.TEST_AFFIX, GUARD.FOLDS, GUARD.STRING,
                    GUARD.HEREDOC_CAT, GUARD.SEGMENT, GUARD.SEGMENT_PS]
        for repeat in (40, 2000):
            start = time.perf_counter()
            for token in tokens:
                for tail in ("", "!", "x(", "=("):
                    text = token * repeat + tail
                    for pattern in patterns:
                        pattern.findall(text)
                    for powershell in (False, True):
                        try:
                            GUARD.split_command(text, powershell)
                        except GUARD.Unparsed:   # an unclosed construct: refused, which is what the hook needs
                            pass
            for powershell in (False, True):   # a `commit` after many `git` and options words
                for text in ("git -C " * repeat + "commit", " commit" * repeat, "git " + "-x " * repeat + "commit"):
                    try:
                        GUARD._unread_commit(text, [], powershell)
                    except GUARD.Unclear:
                        pass
            self.assertLess(time.perf_counter() - start, 5.0, "repeat %d" % repeat)


class ReasonTests(unittest.TestCase):
    def weakened(self, stage=True):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=stage)
        return repo

    def test_a_short_reason_does_not_count(self):
        repo = self.weakened()
        self.assertIsNotNone(reason_of(run_hook('git commit -m "x" -m "Test-change: flaky"', repo.root)))

    def test_a_reason_ends_with_its_message_and_line(self):
        repo = self.weakened()
        self.assertIsNotNone(reason_of(run_hook('git commit -m "Test-change: x" -m "body text long"', repo.root)))
        (repo.root / "msg.txt").write_text("fix: x\n\nTest-change: x\nmore body text here\n", encoding="utf-8")
        self.assertIsNotNone(reason_of(run_hook("git commit -F msg.txt", repo.root)))

    def test_an_apostrophe_inside_a_word_does_not_end_the_reason(self):
        repo = self.weakened()
        self.assertIsNone(reason_of(run_hook('git commit -m "fix" -m "Test-change: it\'s obsolete since v2 removed parse"',
                                             repo.root)))
        (repo.root / "msg.txt").write_text("fix\n\nTest-change: it's obsolete since the v2 parser\n", encoding="utf-8")
        self.assertIsNone(reason_of(run_hook("git commit -F msg.txt", repo.root)))
        (repo.root / "quoted.txt").write_text("fix\n\nTest-change: 'x' and \"y\" are gone with the parser\n",
                                              encoding="utf-8")   # no quoting in a file: a quote ends nothing
        self.assertIsNone(reason_of(run_hook("git commit -F quoted.txt", repo.root)))
        self.assertIsNotNone(reason_of(run_hook("git commit -m 'Test-change: x' -m 'body text long'", repo.root)))

    def test_a_reused_message_counts(self):
        repo = self.weakened(stage=False)
        git(repo.root, "commit", "-q", "--allow-empty", "-m", "note", "-m", REASON)
        reasoned = git(repo.root, "rev-parse", "HEAD").strip()
        git(repo.root, "commit", "-q", "--allow-empty", "-m", "plain")
        git(repo.root, "add", "-A")
        self.assertIsNone(reason_of(run_hook("git commit -C %s" % reasoned, repo.root)))
        self.assertIsNotNone(reason_of(run_hook("git commit -C HEAD", repo.root)))

    def test_a_reason_in_the_message_file_counts(self):
        repo = self.weakened()
        (repo.root / "msg.txt").write_text("fix: x\n\n" + REASON + "\n", encoding="utf-8")
        self.assertIsNone(reason_of(run_hook("git commit -F msg.txt", repo.root)))
        (repo.root / "plain.txt").write_text("fix: x\n", encoding="utf-8")
        self.assertIsNotNone(reason_of(run_hook("git commit --file=plain.txt", repo.root)))

    def test_a_message_file_path_is_resolved_and_an_unread_one_states_no_reason(self):
        repo = self.weakened()
        root = str(repo.root)
        drive = "/" + root[0].lower() + root[2:].replace("\\", "/") if os.name == "nt" else root   # as Git Bash names it
        (repo.root / "plain.txt").write_text("fix: x\n", encoding="utf-8")
        (repo.root / "reasoned.txt").write_text("fix: x\n\n" + REASON + "\n", encoding="utf-8")
        (repo.root / "folder").mkdir()
        temp = Path(tempfile.gettempdir() if os.name == "nt" else "/tmp")   # where Git Bash mounts /tmp
        name = "wtg-message-%d.txt" % os.getpid()
        (temp / name).write_text("fix: x\n", encoding="utf-8")
        self.addCleanup((temp / name).unlink)
        (temp / ("r" + name)).write_text("fix: x\n\n" + REASON + "\n", encoding="utf-8")
        self.addCleanup((temp / ("r" + name)).unlink)
        for command, tool in (('git commit -F "%s/plain.txt"' % drive, "Bash"), ("git commit -F /tmp/" + name, "Bash"),
                              ('m="%s/plain.txt"; git commit -F "$m"' % drive, "Bash"),
                              ("$m = '%s'; git commit -F $m" % os.path.join(root, "plain.txt"), "PowerShell"),
                              ('git commit -F "$nowhere/m.txt"', "Bash"), ("git commit --file=folder", "Bash"),
                              # written by the command: the old text in the file is no reason
                              ("python msg.py > reasoned.txt && git commit -F reasoned.txt", "Bash"),
                              ("cp plain.txt reasoned.txt && git commit -F reasoned.txt", "Bash")):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        for command in ('git commit -F "%s/reasoned.txt"' % drive, 'm="%s/reasoned.txt"; git commit -F "$m"' % drive,
                        "git commit -F /tmp/r" + name,
                        "cat > m.txt <<'EOF'\nfix: x\n\n%s\nEOF\ngit commit -F m.txt" % REASON):
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root)))

    def test_a_reason_in_a_trailer_option_counts(self):
        repo = self.weakened()
        self.assertIsNone(reason_of(run_hook('git commit -m "fix" --trailer "%s"' % REASON, repo.root)))

    def test_a_powershell_here_string_reason_counts(self):
        repo = self.weakened()
        command = "git commit -m @'\nfix: x\n\n%s\n'@" % REASON
        self.assertIsNone(reason_of(run_hook(command, repo.root, tool="PowerShell")))
        self.assertIsNotNone(reason_of(run_hook("git commit -m @'\nfix: x\n'@", repo.root, tool="PowerShell")))
        piped = "@'\nfix: x\n\n%s\n'@ | git commit -F -" % REASON
        self.assertIsNone(reason_of(run_hook(piped, repo.root, tool="PowerShell")))

    def test_a_bash_heredoc_reason_counts(self):
        repo = self.weakened()
        command = "git commit -F - <<'EOF'\nfix: x\n\n%s\nEOF" % REASON
        self.assertIsNone(reason_of(run_hook(command, repo.root)))
        self.assertIsNotNone(reason_of(run_hook("git commit -F - <<'EOF'\nfix: x\nEOF", repo.root)))

    def test_a_reason_the_shell_passes_on_counts(self):
        repo = self.weakened()
        (repo.root / "msg.txt").write_text("fix: x\n\n" + REASON + "\n", encoding="utf-8")
        for command, tool in (("Get-Content msg.txt | git commit -F -", "PowerShell"),
                              ("$msg = @'\nfix: x\n\n%s\n'@\ngit commit -m $msg" % REASON, "PowerShell"),
                              ('git commit -m "fix" --trailer "Test-change=the old parser is gone"', "Bash"),
                              ("git commit -m \"$(cat <<'EOF'\nfix: widen to 27\"\n\n%s\nEOF\n)\"" % REASON, "Bash")):
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root, tool=tool)))
        (repo.root / "plain.txt").write_text("fix: x\n", encoding="utf-8")
        self.assertIsNotNone(reason_of(run_hook("Get-Content plain.txt | git commit -F -", repo.root, tool="PowerShell")))

    def test_amend_without_a_new_message_reads_the_old_one(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken", "-m", REASON)
        (repo.root / "tests" / "extra.py").write_text("x = 1\n", encoding="utf-8")
        git(repo.root, "add", "-A")
        self.assertIsNone(reason_of(run_hook("git commit --amend --no-edit", repo.root)))
        self.assertIsNone(reason_of(run_hook("git commit -C HEAD --amend", repo.root)))
        self.assertIsNotNone(reason_of(run_hook('git commit --amend -m "weaken"', repo.root)))


def shell():
    return shutil.which("sh") if os.name != "nt" else str(Path(os.environ["ProgramFiles"], "Git", "bin", "sh.exe"))


# Message bodies of the commit form Claude Code writes: -m "$(cat <<'EOF' ... EOF )". Each holds a character that
# ends or opens something outside a heredoc.
HEREDOC_BODIES = ['fix "the parser" edge', 'an odd " quote', "close ) paren (and open", "it's here", "a `tick` here",
                  "a && b; c | d > e", "mention --dry-run and -- tests/test_parser.py",
                  'subject\n\n- item "x" (y)\n- $HOME stays', 'trailing blank lines "\n\n']


def heredoc_commit(body, delimiter="'EOF'"):
    return 'git commit -am "$(cat <<%s\n%s\nEOF\n)"' % (delimiter, body)


class SubstitutionTests(unittest.TestCase):
    def test_a_heredoc_message_is_the_word_bash_passes(self):
        major = subprocess.run([shell()], input=b"echo ${BASH_VERSINFO:-0}\n", stdout=subprocess.PIPE).stdout.strip()
        if major == b"3":
            self.skipTest("bash 3 (macOS /bin/sh) misreads a heredoc inside $(...), so it is no oracle here")
        for body in HEREDOC_BODIES:
            with self.subTest(body=body):
                command = heredoc_commit(body)
                script = 'printf %s "$(cat <<\'EOF\'\n' + body + '\nEOF\n)"\n'
                bash = subprocess.run([shell()], input=script.encode("utf-8"), stdout=subprocess.PIPE, check=True)
                simples = GUARD.split_command(command, False)
                self.assertEqual([s.words for s in simples], [["git", "commit", "-am", bash.stdout.decode("utf-8")]])

    def test_any_other_substitution_is_one_word_kept_as_its_text(self):
        # One row per thing inside `$(...)` that holds a `)` or `"` bash does not end the substitution on.
        # A trailing `"q"` makes a substitution closed too early end the outer quote too.
        for inner in ("printf '%s' 'a ) b' \"q\"", 'echo "x ) y"', r'echo \) "q"', "echo $(echo a ) b) c",
                      ' (echo a) "q"',
                      r'echo "\" ) $(echo ")" ) z"', "cat <<'EOF'\nbody ) \"\nEOF\necho tail"):
            with self.subTest(inner=inner):
                simples = GUARD.split_command('git commit -am "$(%s)" && git status' % inner, False)
                self.assertEqual([s.words for s in simples], [["git", "commit", "-am", "$(%s)" % inner], ["git", "status"]])

    def test_a_heredoc_message_does_not_hide_a_weakening(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command in [heredoc_commit(body) for body in HEREDOC_BODIES] + [heredoc_commit('fix "x" (y)', "EOF")]:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: removed test"), reason)


UNCLEAR = "weak-test-guard: cannot tell what this commit holds: "

# (command, the words bash gives its first simple command): constructs that hold a `'`, `"`, `)` or `<<` bash does
# not read as a quote, an end or a heredoc. Each is followed by a commit the guard must still see.
MODELLED = [
    ("echo $'it\\'s' && git commit -am x", ["echo", "$'it\\'s'"]),
    ("echo \"$(printf %s $'a\\' \"q')\" && git commit -am x", ["echo", "$(printf %s $'a\\' \"q')"]),
    ("echo \"$(echo a # it's )\n)\" && git commit -am x", ["echo", "$(echo a # it's )\n)"]),
    ("echo \"$(echo x#y)\" && git commit -am x", ["echo", "$(echo x#y)"]),
    ("echo $((1<<2))\ngit commit -am x", ["echo", "$((1<<2))"]),
    ("echo \"$((1<<2))\"\ngit commit -am x", ["echo", "$((1<<2))"]),
    ("echo \"$(echo $((1<<2)) )\"\ngit commit -am x", ["echo", "$(echo $((1<<2)) )"]),
    ("echo \"$(echo \"$((1<<2))\")\"\ngit commit -am x", ["echo", "$(echo \"$((1<<2))\")"]),
    # A newline inside makes a `<<` read as a heredoc swallow the rest.
    ("echo \"$((1<<2\n))\"\ngit commit -am x", ["echo", "$((1<<2\n))"]),
    ("echo \"$(echo $((1<<2))\necho y)\"\ngit commit -am x", ["echo", "$(echo $((1<<2))\necho y)"]),
    ("echo \"$(echo \"$((1<<2))\"\necho y)\"\ngit commit -am x", ["echo", "$(echo \"$((1<<2))\"\necho y)"]),
]
# Commands the guard cannot read: it denies instead of reading the rest as one word.
UNREADABLE = ['echo "$(case a in a) echo b;; esac)" && git commit -am x',
              "git commit -am \"$(cat <<'EOF'\nmsg\nEOF\n\"",
              "echo $'unclosed && git commit -am x", "echo $((1 && git commit -am x"]
# A `git commit` that runs where the guard cannot read it.
HIDDEN = ['bash -c "git commit -am x"', 'bash -c "git \\commit -am x"', "sudo git commit -am x", "xargs git commit -am x < /dev/null",
          "pwsh -c 'git commit -am x'", "env -u X git commit -am x", "exec -a n git commit -am x",
          "time -p git commit -am x", "eval 'git commit -am x'", "timeout 5 git commit -am x",
          'echo "$(git commit -am x)"',
          # The parse reads one commit and the text holds one more: the pairing is by place, not by count.
          'git -C "a b" commit -am x && bash -c "git commit -am y"',
          # Text that only names a commit is refused the same way: the check is loose on purpose.
          "echo 'next: git commit -m x'", "echo git commit -m x", 'gh pr create --body "then git commit"',
          # A heredoc body belongs to the command that opened it, not to the next one.
          "cat > notes.txt <<'EOF'\ngit commit -am x\nEOF\ngit commit -am y"]
HIDDEN += [
    # Inside a commit the guard reads, only text that cannot run counts as read.
    'git commit -am x -m "$(git commit -a -m y)"', 'git commit -am x -m "`git commit -a -m y`"',
    "git commit -a -F - <<EOF\n$(git commit -a -m y)\nEOF", 'git commit -am x -m "$((0)) $(git commit -a -m y)"',
    # No lookback window: any distance between `git` and `commit` in one segment.
    "sudo git " + "--opt " * 40 + "commit -am x", "echo git" + " x" * 400 + " commit",
    "git log --grep commit",   # loose by ruling: a read-only git that names `commit` is refused too
    # Arithmetic, a bare backtick and an expanding `cat` heredoc run what they hold.
    "git commit -am x -m $(( $(git commit -a -m y) ))", "git commit -am x -m `git commit -a -m y`",
    'git commit -am x -m "$(cat <<EOF\n$(git commit -a -m y)\nEOF\n)"',
]
POWERSHELL_HIDDEN = ['git commit -am x -m "$(git commit -a -m y)"', "git commit -am x -m @\"\n$(git commit -a)\n\"@",
"Invoke-Expression 'GIT COMMIT -am x'", "$s = { Git -C repo Commit -am x }; & $s"]
# (tool, command, refused by the cross-check): `commit` counts only as a whole word, so options, branch names, config
# keys and hook files that hold it pass, while a `commit-tree` or a quoted `"commit"` another program runs does not.
NARROW_ROWS = [
    ("Bash", 'python -c "import subprocess; subprocess.run([\\"git\\", \\"commit\\", \\"-am\\", \\"x\\"])"', True),
    ("Bash", "python -c \"import subprocess; subprocess.run(['git', 'commit', '-am', 'x'])\"", True),
    ("Bash", 'bash -c "git commit -am x"', True),
    ("Bash", "wsl.exe -e git -C /srv/x commit -am x", False),   # unchecked by ruling: the hook cannot read that repo
    ("PowerShell", "$t = git write-tree; $c = git commit-tree $t -p HEAD -m x; git update-ref HEAD $c", True),
    ("Bash", "sudo /usr/bin/git commit -am x", True),
    ("PowerShell", "& '.\\Git tools\\cmd\\git.exe' -c a=b commit -am x | Out-Null; $x = 1", False),
    ("Bash", "git merge --no-ff --no-commit topic", False),
    ("Bash", "git revert --no-commit HEAD", False),
    ("Bash", "git checkout -b fix/commit-guard", False),
    ("Bash", "git branch -D fix/commit-guard", False),
    ("Bash", "git config commit.gpgsign false", False),
    ("Bash", "cat .git/hooks/post-commit", False),
    ("Bash", "git log --oneline --grep commit", True),   # still a whole word
    # the rewrite the hint names
    ("Bash", 'git log --oneline --grep "$(printf commit)"', False),
    ("PowerShell", "git log --oneline --grep \"$('commit')\"", False),
]
# Shell keywords and wrappers in front of a commit the guard reads.
WRAPPED = ["if true; then git commit -am x; fi", "while false; do :; done; command git commit -am x",
           "env -i X=1 git commit -am x", "time git commit -am x", "for f in a; do git commit -am x; done",
           "f() { git commit -am x; }; f", "nohup git commit -am x", "! exec git commit -am x",
           "if git commit -am x; then :; fi", "while git commit -am x; do break; done",
           "until git commit -am x; do :; done", "if false; then :; else git commit -am x; fi",
           "if false; then :; elif git commit -am x; then :; fi"]


class ReadingTests(unittest.TestCase):
    def test_constructs_bash_reads_are_modelled(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command, words in MODELLED:
            with self.subTest(command=command):
                try:
                    read = [s.words for s in GUARD.split_command(command, False)]
                except GUARD.Unparsed as why:
                    read = "refused: %s" % why
                self.assertEqual(read, [words, ["git", "commit", "-am", "x"]])
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_a_command_it_cannot_read_is_denied(self):
        repo = Repo(self, BASE)
        for command in UNREADABLE:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "the guard cannot read the command"), reason)
                self.assertIn("Commit in a command of its own", reason)
        self.assertIsNone(reason_of(run_hook("echo $'unclosed commit", repo.root)))   # no git commit in it

    def test_a_commit_it_cannot_read_is_denied(self):
        repo = Repo(self, BASE)
        for command in HIDDEN:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "a git commit the guard could not parse"), reason)
                self.assertIn("Run `git commit` itself", reason)
                self.assertIn("if the command does not commit, split it so no `git` and `commit` share", reason)
                self.assertIn("pass an argument such as `--grep commit` from a substitution", reason)
        for command in POWERSHELL_HIDDEN:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool="PowerShell"))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "a git commit the guard could not parse"), reason)
        self.assertIsNone(reason_of(run_hook('bash -c "git commit -am x -m \'%s\'"' % REASON, repo.root)))

    def test_the_cross_check_matches_commit_as_a_whole_word(self):
        for tool, command, refused in NARROW_ROWS:
            with self.subTest(command=command):
                try:
                    GUARD.read_command(command, ".", tool == "PowerShell")
                    got = False
                except GUARD.Unclear as why:
                    got = "could not parse" in str(why)
                self.assertEqual(got, refused)

    def test_a_commit_in_the_linux_file_system_or_over_ssh_is_unchecked(self):
        # It works on a repository the hook cannot read: a stated limit, not a denial only a trailer gets past.
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command, tool in (("wsl git -C /srv/x commit -am x", "Bash"), ("wsl.exe -e git -C /srv/x commit -am x", "Bash"),
                              ("wsl -- git -C '/srv/a b' -c user.name=x commit -am x", "Bash"),
                              ("wsl --cd ~ git commit -am x", "Bash"), ("wsl --cd /srv/repo git commit -am x", "Bash"),
                              ('wsl -d Ubuntu -- bash -lc "cd ~/repo && git add -A && git commit -m x"', "Bash"),
                              ("wsl -d Ubuntu bash -c 'cd; cd repo; git -c user.name=x commit -am x'", "PowerShell"),
                              ("wsl bash -c 'git -C ~/r commit -am x'", "Bash"),
                              ("ssh host git --no-pager commit -am x", "Bash"),
                              ('ssh host "cd /srv/x && git commit -am x"', "Bash"),
                              ("wsl -d Ubuntu -- git -C /srv/x commit -am x", "PowerShell"),
                              ("ssh.exe host git -C /srv/x commit -am x", "PowerShell")) + (
                # Off Windows an absolute path is local to the hook, so only Windows has a Linux-only one to name.
                (('wsl bash -c "cd /srv/r && git commit -am x"', "Bash"),) if os.name == "nt" else ()):
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root, tool)))

    def test_a_commit_through_wsl_on_a_windows_folder_is_checked(self):
        # WSL starts in the Windows folder and reaches the drives under /mnt/<d>: the repository the hook reads.
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        root = str(repo.root)
        mounted = "/mnt/" + root[0].lower() + root[2:].replace("\\", "/") if os.name == "nt" else root
        for command, tool in (("wsl git commit -am x", "Bash"), ("wsl -e git commit -am x", "Bash"),
                              ('wsl git -C "%s" commit -am x' % mounted, "Bash"),
                              # read as WSL resolves it: `..` and a doubled leading slash do not leave /mnt
                              ('wsl git -C "/srv/..%s" commit -am x' % mounted, "Bash"),
                              ('wsl git -C "/%s" commit -am x' % mounted, "Bash"),
                              ('wsl --cd "%s" git commit -am x' % mounted, "Bash"),
                              ("wsl -d Ubuntu -- git commit -am x", "PowerShell")):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        for command in ('wsl echo "$(git commit -am x)"', "wsl true; eval 'git commit -am x'",
                        'wsl bash -c "git commit -am x"', 'wsl bash -c "cd %s && git commit -am x"' % "/".join(("", "mnt", "c", "x")),
                        "wsl bash -c \"cd ~/r && eval 'git commit -am x'\"", "wsl --exotic git commit -am x",
                        "wsl bash -c 'GIT_DIR=/srv/r/.git git -C ~ commit -am x'",
                        "wsl bash -c 'cd ~/../../srv && git commit -am x'", "wsl bash -c 'cd ~/r && git commit -am \"$(x'",
                        # the script's folder walk does not follow a folder stack or a subshell
                        'wsl bash -c "pushd ~/r && popd && git commit -am x"',
                        'wsl bash -c "(cd ~/r); git commit -am x"', 'wsl bash -c "pushd; git commit -am x"',
                        'wsl bash -c "pushd -n ~/r; git commit -am x"'):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "a git commit the guard could not parse"), reason)
        # Unchecked only when the repository is surely in the Linux file system: `..` from `~`, a repository
        # option, a core setting or a GIT_* variable may point it anywhere.
        for command, tool in (("wsl git -C ~/../x commit -am x", "Bash"), ("wsl --cd ~ git -C ../x commit -am x", "Bash"),
                              ("wsl git -C ~/$X commit -am x", "Bash"),
                              ("wsl git --git-dir=/srv/r/.git -C ~ commit -am x", "Bash"),
                              ("wsl git --work-tree /srv/r -C ~/x commit -am x", "Bash"),
                              ("wsl git --namespace=n -C ~ commit -am x", "Bash"),
                              ("wsl git -c core.worktree=/srv/r -C ~ commit -am x", "Bash"),
                              ("GIT_DIR=/srv/r/.git wsl git -C ~ commit -am x", "Bash"),
                              ("$env:GIT_WORK_TREE = 'x'; wsl git -C ~ commit -am x", "PowerShell")):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "a `wsl` git commit"), reason)
                self.assertIn("Name the WSL folder literally", reason)
        # Staging through WSL in such a folder before a local commit: what it stages cannot be told.
        reason = reason_of(run_hook("wsl git -C ~/.. add -A && git commit -m x", repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "the folder"), reason)

    def test_a_folder_named_by_a_variable_is_resolved_or_refused(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        root = str(repo.root)
        drive = "/" + root[0].lower() + root[2:].replace("\\", "/") if os.name == "nt" else root
        resolved = [("$wt = '%s'; git -C $wt commit -m x" % root, "PowerShell"),
                    ("$WT='%s'; Set-Location $wt; git commit -m x" % root, "PowerShell"),
                    ("$wt = '%s'; Set-Location $WT; git commit -m x" % root, "PowerShell"),
                    ('$env:WT = "%s"; git -C "$env:WT" commit -m x' % root, "PowerShell"),
                    ('wt="%s"; git -C "$wt" commit -m x' % drive, "Bash"),
                    ('export WT="%s" && cd "${WT}" && git commit -m x' % drive, "Bash"),
                    # in the same `&&` chain as the git step: when it does not run, neither does the step
                    ('true && wt="%s" && git -C "$wt" commit -m x' % drive, "Bash"),
                    ('if true; then :; fi; wt="%s"; git -C "$wt" commit -m x' % drive, "Bash"),   # after the block
                    # the name inside its own value (the folder holds "guard") or inside a longer word is no use
                    ('guard="%s"; git -C "$guard" commit -m x' % drive, "Bash"),
                    ("$guard = '%s'; git -C $guard commit -m x" % root, "PowerShell"),
                    ('wt="%s"; git -C "$wt" commit -m "fix my-wt"' % drive, "Bash"),
                    # the literal text of a commit the guard reads is its message, never an assignment
                    ("wt=\"%s\"; git -C \"$wt\" commit -m 'move wt to the new place'" % drive, "Bash"),
                    ('wt="%s"; git -C "$wt" commit -m "wt: fix"' % drive, "Bash"),
                    ("$wt = '%s'; git -C $wt commit -m 'fix wt path'" % root, "PowerShell"),
                    ("wt=\"%s\"; git -C \"$wt\" commit -F - <<'EOF'\nmove wt\nEOF" % drive, "Bash"),   # a quoted heredoc
                    ('wt="%s"; git -C "$wt" commit -m "move wt here"' % drive, "Bash"),
                    ("wt=\"%s\"; git -C \"$wt\" commit -m $'move wt here'" % drive, "Bash"),
                    ("$wt = '%s'; git -C $wt commit -m @'\nmove wt here\n'@" % root, "PowerShell"),
                    ('wt="%s"; git commit -m `wt=/s`; git -C "$wt" commit -m x' % drive, "Bash")]   # a subshell
        for command, tool in resolved:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        clean = Repo(self, BASE)
        self.assertIsNone(reason_of(run_hook(resolved[0][0].replace(root, str(clean.root)), clean.root, "PowerShell")))
        for command, tool in (("git -C $repo commit -m 'x'", "PowerShell"), ('cd "$HOME/x" && git commit -m "x"', "Bash"),
                              ('git -C "$PWD" commit -m x', "Bash"), ("cd ~/x && git commit -m x", "Bash"),
                              ("$wt = Join-Path $x 'y'; git -C $wt commit -m x", "PowerShell"),
                              ("git -C $wt add -A; git commit -m x", "PowerShell"),
                              ('wt=%s git -C "$wt" commit -m x' % drive.replace(" ", "\\ "), "Bash"),   # set after expansion
                              ("cd `pwd` && git commit -m x", "Bash")) + tuple(
                # An assignment that may not run, runs elsewhere, or is not the only one to the name is not trusted.
                (command % (drive if tool == "Bash" else root), tool) for command, tool in (
                    ('false || wt="%s"; git -C "$wt" commit -m x', "Bash"),
                    ('false && wt="%s"; git -C "$wt" commit -m x', "Bash"),
                    ('(wt="%s"); git -C "$wt" commit -m x', "Bash"),
                    ('(true; wt="%s"); git -C "$wt" commit -m x', "Bash"),
                    ('true |\nwt="%s"; git -C "$wt" commit -m x', "Bash"),
                    ("if ($true) { $null; $wt = '%s' }; git -C $wt commit -m x", "PowerShell"),
                    ('if true; then\nwt="%s"\nfi\ngit -C "$wt" commit -m x', "Bash"),
                    ('wt="%s" | cat; git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s" & git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s"; wt+=/sub; git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s"; read wt < /dev/null; git -C "$wt" commit -m x', "Bash"),
                    ('wt=x; wt="%s"; git -C "$wt" commit -m x', "Bash"),
                    ('for wt in "%s"; do :; done; git -C "$wt" commit -m x', "Bash"),
                    ("if ($true) { $wt = '%s' }; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; $wt += 'x'; git -C $wt commit -m x", "PowerShell"),
                    ("foreach ($wt in @('%s')) { }; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; foreach ($wt in @('x')) { }; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; Get-Item x -OutVariable wt; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; Set-Variable wt 'x'; git -C $wt commit -m x", "PowerShell"),
                    ("& { $wt = '%s' }; git -C $wt commit -m x", "PowerShell"),
                    # Any appearance of the name but its one assignment and plain reads leaves it unresolved.
                    ('wt="%s"; declare -n r=wt; git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s"; wt[1]=x; git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s"; local wt; git -C "$wt" commit -m x', "Bash"),
                    ('local wt="%s"; git -C "$wt" commit -m x', "Bash"),
                    ("$wt = '%s'; Remove-Variable wt; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; $script:wt = 'x'; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; [ref]$wt; git -C $wt commit -m x", "PowerShell"),
                    ("$wt = '%s'; $wt.Replace('a', 'b'); git -C $wt commit -m x", "PowerShell"),
                    # what runs inside a commit's message, or stands outside every commit, still counts
                    ('wt="%s"; git commit -m "$(wt=/s; echo x)"; git -C "$wt" commit -m y', "Bash"),
                    ("$wt = '%s'; git commit -m \"$($wt = 'x')\"; git -C $wt commit -m y", "PowerShell"),
                    ('wt="%s"; echo wt; git -C "$wt" commit -m x', "Bash"),
                    ('wt="%s"; git commit -m a; wt=/s; git -C "$wt" commit -m x', "Bash"),
                    # only quoted message text with no `${`, `$[` or subscript around the name is skipped
                    ('wt="%s"; wt=/x git commit -m x; cd "$wt" && git commit -m y', "Bash"),
                    ('wt="%s"; declare "wt=/s"; git -C "$wt" commit -m y', "Bash"),   # quoted, but no commit's
                    ('wt="%s"; git commit -m x ${wt:=/s}; git -C "$wt" commit -m y', "Bash"),
                    ('wt="%s"; git commit -m "${wt:=/s}"; git -C "$wt" commit -m y', "Bash"),
                    ('wt="%s"; git commit -m "$[wt=1]"; git -C "$wt" commit -m y', "Bash"),
                    ('wt="%s"; git commit -m "x[wt]"; git -C "$wt" commit -m y', "Bash"),
                    ('wt="%s"; git commit -m "wt[1]"; git -C "$wt" commit -m y', "Bash"),
                    ('wt="%s"; git commit -F - <<EOF\n${wt:=/s}\nEOF\ngit -C "$wt" commit -m y', "Bash"))):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "the folder"), reason)
                self.assertIn("Name the folder literally", reason)
        # A file write in such a folder before a commit in a named one: the guard cannot see what it removes.
        reason = reason_of(run_hook("Set-Location $nowhere; Remove-Item tests/test_parser.py; Set-Location '%s'; "
                                    "git add -A; git commit -m x" % clean.root, clean.root, "PowerShell"))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "`Remove-Item`"), reason)

    def test_a_commit_message_that_names_git_commit_is_read(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command in ("git commit -a -F - <<'EOF'\nfix: x\n\nrun git commit -am later\nEOF",
                        'git commit -am "fix: git commit hook"',
                        # a quoted heredoc is literal text: a `$(` in it runs nothing
                        "git commit -a -F - <<'EOF'\nfix: x\n\nsee $(git commit -h)\nEOF",
                        "git commit -am \"$(cat <<'EOF'\nfix: x\n\nsee $(git commit -h)\nEOF\n)\""):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_keywords_and_wrappers_before_a_commit_are_read(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command in WRAPPED:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

# (command, tool, the step the denial names): a step before the commit that changes the index, or changes a test
# file a later staging step picks up. The guard cannot replay it, so it denies without claiming a weakening.
UNCLEAR_ROWS = [
    ("rm tests/test_parser.py && git commit -am x", "Bash", "`rm`"),
    ("rm -f tests/test_parser.py && git add -A && git commit -m x", "Bash", "`rm`"),
    ("rm -r tests && git commit -am x", "Bash", "`rm`"),
    ("rm tests/*.py && git add -A && git commit -m x", "Bash", "`rm`"),
    ("rm ../*.py && git add -A && git commit -m x", "Bash", "`rm`"),
    ("rm tests/test_parser.py && git commit -m x tests/test_parser.py", "Bash", "`rm`"),
    ("sed --in-place '/rejects/d' tests/test_parser.py && git commit -am x", "Bash", "`sed`"),
    ("mv tests/test_parser.py old.py && git add -A && git commit -m x", "Bash", "`mv`"),
    ("cp src/parser.py tests/test_parser.py && git commit -am x", "Bash", "`cp`"),
    ("sed -i '/rejects/d' tests/test_parser.py && git add tests && git commit -m x", "Bash", "`sed`"),
    ("Remove-Item tests/test_parser.py; git commit -am x", "PowerShell", "`Remove-Item`"),
    ("Move-Item tests/test_parser.py old.py; git add -A; git commit -m x", "PowerShell", "`Move-Item`"),
    ("Copy-Item src/parser.py tests/test_parser.py; git commit -am x", "PowerShell", "`Copy-Item`"),
    ("Set-Content tests/test_parser.py 'x'; git commit -am x", "PowerShell", "`Set-Content`"),
    ("Add-Content -Path tests/test_parser.py -Value 'x'; git commit -am x", "PowerShell", "`Add-Content`"),
    ("'x' | Out-File tests/test_parser.py; git commit -am x", "PowerShell", "`Out-File`"),
    ("git checkout HEAD~1 -- tests && git commit -m x", "Bash", "`git checkout`"),
    ("git checkout -b topic HEAD~1 && git commit -m x", "Bash", "`git checkout`"),
    ("git switch other && git commit -am x", "Bash", "`git switch`"),
    ("git restore --source HEAD~1 tests && git commit -am x", "Bash", "`git restore`"),
    ("git reset -q HEAD~1 && git commit -am x", "Bash", "`git reset`"),
    ("git stash pop && git commit -am x", "Bash", "`git stash`"),
    ("git mv tests/test_parser.py tests/t.py && git commit -m x", "Bash", "`git mv`"),
    ("git apply fix.patch && git commit -am x", "Bash", "`git apply`"),
    ("git read-tree HEAD~1 && git commit -m x", "Bash", "`git read-tree`"),
    ("git update-index --force-remove tests/test_parser.py && git commit -m x", "Bash", "`git update-index`"),
    ("git am fix.patch && git commit -m x", "Bash", "`git am`"),
    ("git merge --abort && git commit -m x", "Bash", "`git merge`"),
    ("git cherry-pick --quit && git commit -m x", "Bash", "`git cherry-pick`"),
    ("git stash pop && git merge --squash topic && git commit -m x", "Bash", "`git merge`"),
    ("git add -A && git merge --squash topic && git commit -m x", "Bash", "`git merge`"),
    ("git merge --squash topic && git commit -am x", "Bash", "`git merge`"),
    ("git merge --squash topic && rm tests/test_parser.py && git add -A && git commit -m x", "Bash", "`git merge`"),
    ("git add --pathspec-from-file=list.txt && git commit -m x", "Bash", "`--pathspec-from-file`"),
    ("rm.exe tests/test_parser.py && git commit -am x", "Bash", "`rm.exe`"),
    ("cp -t tests src/parser.py && git commit -am x", "Bash", "`cp`"),
    ("cp --target-directory=tests src/parser.py && git commit -am x", "Bash", "`cp`"),
    ("Copy-Item -Destination tests/test_parser.py src/parser.py; git commit -am x", "PowerShell", "`Copy-Item`"),
    ("cat src/parser.py > tests/test_parser.py && git commit -am x", "Bash", "`cat`"),
    ("rm $DIR/test_parser.py && git commit -am x", "Bash", "`rm`"),
    ("rm ~/x && git add -A && git commit -m x", "Bash", "`rm`"),
    ("rm tests/{a,b}.py && git add -A && git commit -m x", "Bash", "`rm`"),
    ("rm `cat list` && git commit -am x", "Bash", "`rm`"),
] + [("%s tests/test_parser.py; git commit -am x" % alias, "PowerShell", "`%s`" % alias)
     for alias in ("del", "erase", "ri", "rd", "rmdir", "move", "mi", "copy", "cpi", "sc", "ac")]
# Plain forms around those steps that must stay allowed on the same unchanged repository.
AROUND_ROWS = [
    ("rm notes.txt && git commit -am x", "Bash"),
    ("rm src/sum.js && git add -A && git commit -m x", "Bash"),
    ("rm ../outside.txt && git add -A && git commit -m x", "Bash"),
    ("cp tests/test_parser.py backup.py && git add -A && git commit -m x", "Bash"),
    ("Copy-Item tests/test_parser.py backup.py; git add -A; git commit -m x", "PowerShell"),
    ("git add -A && rm tests/test_parser.py && git commit -m x", "Bash"),
    ("rm tests/test_parser.py && git commit -m x", "Bash"),
    ("sed 's/a/b/' tests/test_parser.py > out.txt && git commit -am x", "Bash"),
    ("Remove-Item notes.txt; git add -A; git commit -m x", "PowerShell"),
    ("Set-Content CHANGELOG.md 'x'; git add CHANGELOG.md; git commit -m x", "PowerShell"),
    ("git checkout -b topic && git commit -am x", "Bash"),
    ("git checkout -q -b topic && git commit -am x", "Bash"),
    ("git switch -c topic --quiet && git add -A && git commit -m x", "Bash"),
    ("/usr/bin/sed 's/a/b/' tests/test_parser.py > out.txt && git commit -am x", "Bash"),
    ("git status --short && git diff --stat && git add -A && git commit -m x", "Bash"),
    ("python fmt.py && git add -A && git commit -m x", "Bash"),
    ("git commit -am x && git checkout develop && git merge --squash topic", "Bash"),
    ("python gen.py && git add made.py && git commit -m x", "Bash"),
    ("cp -t backup tests/test_parser.py && git add -A && git commit -m x", "Bash"),
    ("Copy-Item -Destination backup.py tests/test_parser.py; git add -A; git commit -m x", "PowerShell"),
    ("echo x > notes.txt && git add -A && git commit -m x", "Bash"),
    ("git add -A > /dev/null && git commit -m x 2>&1", "Bash"),
    ("rm $TMPDIR/x && git commit -m x", "Bash"),
    ("git merge --squash topic && git diff --cached --stat && git commit -m x", "Bash"),
    ("git add -A 2>$null; git commit -m x", "PowerShell"),
    ("git add -A && git commit -m a && git merge --squash topic && git commit -m b", "Bash"),
]


class UnclearTests(unittest.TestCase):
    def test_a_step_the_guard_cannot_replay_is_denied_with_a_hint(self):
        repo = Repo(self, BASE)
        for command, tool, step in UNCLEAR_ROWS:
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root, tool=tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR), reason)
                self.assertIn(step, reason)
                self.assertIn("in its own call", reason)
                self.assertNotIn("weakens tests", reason)

    def test_plain_forms_around_those_steps_pass(self):
        repo = Repo(self, BASE)
        for command, tool in AROUND_ROWS:
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root, tool=tool)))

    def test_a_path_the_command_makes_is_a_new_file(self):
        # No file and no index entry yet: something before `git add` makes it. A tracked path deleted from disk is
        # not new: adding it stages the deletion.
        for command, prepare in (('python gen.py && git add made.py tests/test_parser.py && git commit -m "x"',
                                  lambda repo: repo.change(WEAK, stage=False)),
                                 ('git add made.py tests/test_parser.py && git commit -m "x"',
                                  lambda repo: (repo.root / "tests" / "test_parser.py").unlink()),
                                 ("git add ':(literal)tests/test_parser.py' made.py && git commit -m x",
                                  lambda repo: repo.change(WEAK, stage=False))):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                prepare(repo)
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: removed test"), reason)
        repo = Repo(self, BASE)   # `-A` limited to a new path stages only that path
        repo.change(WEAK, stage=False)
        self.assertIsNone(reason_of(run_hook('python gen.py && git add -A made.py && git commit -m "x"', repo.root)))

    def test_a_stated_reason_passes_a_step_it_cannot_replay(self):
        repo = Repo(self, BASE)
        self.assertIsNone(reason_of(run_hook('rm tests/test_parser.py && git commit -am x -m "%s"' % REASON, repo.root)))

    def test_an_operation_in_progress_comes_first(self):
        repo = Repo(self, BASE)
        git(repo.root, "switch", "-q", "-c", "other")
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken")
        git(repo.root, "switch", "-q", "develop")
        git(repo.root, "merge", "-q", "--no-ff", "--no-commit", "other")
        self.assertIsNone(reason_of(run_hook("git checkout --theirs tests && git add -A && git commit -m x", repo.root)))

    def test_a_history_step_before_the_commit_checks_what_is_staged(self):
        # The commit is diffed as the index stands when the hook runs: a weakening an earlier call staged is denied,
        # while the history step's own content, not in the index yet, is not seen.
        repo = Repo(self, BASE)
        repo.change(WEAK)
        for command in ("git merge --squash topic && git commit -m x", "git merge --squash topic; git commit -m x",
                        "git pull --ff-only && git commit -m x", "git cherry-pick -n abc && git commit -m x",
                        "git revert -n abc && git commit -m x", "git rebase -q main && git commit -m x"):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        clean = Repo(self, BASE)
        git(clean.root, "switch", "-q", "-c", "topic")
        clean.change(WEAK)
        git(clean.root, "commit", "-q", "-m", "weaken")
        git(clean.root, "switch", "-q", "develop")
        self.assertIsNone(reason_of(run_hook("git merge --squash topic && git commit -m squash", clean.root)))
        for command in ("git merge --help && git commit -m x", "git rebase --show-current-patch && git commit -m x",
                        "git revert -h && git commit -m x"):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_every_commit_of_the_command_is_checked(self):
        for command in ('git commit -m first -- src/sum.js && git commit -am second',
                        'git commit --dry-run -a && git commit -am x',
                        'git add src/parser.py && git commit -m a && git add -A && git commit -m b',
                        'git commit -am a && git commit --amend -m b',
                        'git commit -m a -- tests/test_parser.py && git commit --allow-empty -m b',
                        'git commit -am a && git commit -m b -- src/sum.js'):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                repo.change(WEAK, stage=False)
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: removed test"), reason)
        repo = Repo(self, BASE)   # a dry run stages nothing, and a reason in any commit's message counts
        repo.change(WEAK, stage=False)
        (repo.root / "msg.txt").write_text("fix: x\n\n" + REASON + "\n", encoding="utf-8")
        for command in ('git commit --dry-run -a && git commit -m x',
                        'git commit -F msg.txt -- src/sum.js && git commit -am b'):
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root)))

    def test_a_later_amend_keeps_the_base_of_the_first_commit(self):
        # The amend rewrites the commit this command just made, so the commits together sit on HEAD.
        repo = Repo(self, BASE)
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken", "-m", REASON)
        repo.change({"src/parser.py": PY_CODE + "\n# more\n"}, stage=False)
        self.assertIsNone(reason_of(run_hook("git commit -m a -- src/parser.py && git commit --amend -m b", repo.root)))
        self.assertIsNotNone(reason_of(run_hook("git commit --amend -m a -- src/parser.py && git commit -m b",
                                                repo.root)))

    def test_commits_in_two_repositories_cannot_be_told(self):
        repo, other = Repo(self, BASE), Repo(self, BASE)
        reason = reason_of(run_hook('git commit -am a && cd "%s" && git commit -am b' % other.root.as_posix(), repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "commits in more than one repository"), reason)
        nested = repo.root / "nested"   # a repository inside the first one is another one
        nested.mkdir()
        git(nested, "init", "-q")
        for command in ("git commit -am a && cd nested && git commit -am b",
                        "cd nested && git commit -am a && cd .. && git commit -am b"):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "commits in more than one repository"), reason)
        repo.change(WEAK, stage=False)   # a subfolder of the same repository is read as one
        reason = reason_of(run_hook('git commit -m a -- src/sum.js && cd tests && git commit -am b', repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_a_soft_reset_squash_is_read_against_its_new_base(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken", "-m", REASON)
        git(repo.root, "commit", "-q", "--allow-empty", "-m", "more")
        reason = reason_of(run_hook("git reset --soft HEAD~2 && git commit -m squash", repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: removed test"), reason)
        self.assertIsNone(reason_of(run_hook("git reset --soft HEAD~1 && git commit -m squash", repo.root)))
        self.assertIsNone(reason_of(run_hook('git reset --soft HEAD~2 && git commit -m squash -m "%s"' % REASON,
                                             repo.root)))


class DiffSourceTests(unittest.TestCase):
    def test_unstaged_weakening_is_not_in_a_plain_commit(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        for command in ('git commit -m "x"', 'git commit -m "feat: add date parsing"',
                        'git add src/parser.py && git commit -m "x"'):
            with self.subTest(command=command):
                self.assertIsNone(reason_of(run_hook(command, repo.root)))

    def test_a_command_that_stages_is_replayed(self):
        for command in ('git add -A && git commit -m "x"', 'git commit -am "x"', 'git commit --all -m "x"',
                        'git add tests && git commit -m "x"', 'git commit tests/test_parser.py -m "x"',
                        'git commit -m "x" -- tests/test_parser.py', 'git commit -i tests/test_parser.py -m "x"'):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                repo.change(WEAK, stage=False)
                self.assertIsNotNone(reason_of(run_hook(command, repo.root)))
                self.assertEqual(git(repo.root, "diff", "--cached", "--name-only"), "")

    def test_a_repo_reached_through_a_link_is_replayed(self):
        # git prints the resolved top level; the hook's folder is the link (macOS /var is one to /private/var)
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        holder = Path(tempfile.mkdtemp(prefix="weak test guard link "))
        self.addCleanup(shutil.rmtree, holder, True)
        link = holder / "repo"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(repo.root), str(link))   # a junction needs no symlink privilege
        else:
            os.symlink(repo.root, link, target_is_directory=True)
        self.assertIsNotNone(reason_of(run_hook('git add -A && git commit -m "x"', link)))

    def test_staging_from_a_subfolder_is_replayed(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        self.assertIsNotNone(reason_of(run_hook('cd tests && git add -A && git commit -m "x"', repo.root)))

    @unittest.skipUnless(os.name == "nt", "drive letters exist only on Windows")
    def test_a_folder_with_the_drive_letter_in_other_case_is_replayed(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        folder = str(repo.root)
        folder = (folder[0].lower() if folder[0].isupper() else folder[0].upper()) + folder[1:]
        self.assertIsNotNone(reason_of(run_hook('git add -A && git commit -m "x"', folder)))

    def test_git_rm_is_replayed_without_touching_the_tree(self):
        repo = Repo(self, BASE)
        self.assertIsNotNone(reason_of(run_hook('git rm -q tests/test_parser.py; git commit -m "x"', repo.root)))
        self.assertTrue((repo.root / "tests" / "test_parser.py").is_file())

    def test_a_test_moved_to_an_untracked_file_and_added_passes(self):
        repo = Repo(self, BASE)
        repo.change(dict(WEAK, **{"tests/test_garbage.py":
                                  'import parser\n\n\ndef test_rejects_garbage():\n    assert parser.parse("x") is None\n'}),
                    stage=False)
        self.assertIsNone(reason_of(run_hook('git add -A && git commit -m "x"', repo.root)))

    def test_amend_reads_the_commit_being_amended(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken")
        self.assertIsNone(reason_of(run_hook('git commit -m "x"', repo.root)))
        self.assertIsNotNone(reason_of(run_hook('git commit --amend -m "x"', repo.root)))

    def test_the_folder_comes_from_cd_and_dash_c(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        outside = repo.root.parent
        name = repo.root.name
        for command, tool in (('git -C "%s" commit -m "x"' % name, "Bash"),
                              ('cd "%s" && git commit -m "x"' % name, "Bash"),
                              ("Set-Location '%s'; git commit -m 'x'" % name, "PowerShell")):
            with self.subTest(command=command):
                self.assertIsNotNone(reason_of(run_hook(command, outside, tool=tool)))

    def test_the_folder_is_restored_after_popd_and_a_subshell(self):
        # Two repositories with different test files: the commit is read in the one whose test it removes.
        w, x = Repo(self, BASE), Repo(self, {"tests/test_other.py": PY_TEST})
        rm_w, rm_x = "git rm -q tests/test_parser.py", "git rm -q tests/test_other.py"
        for command, tool, cwd in (
                ("(cd {x} && git status); %s && git commit -m x" % rm_w, "Bash", w),
                ("pushd {x} && git status && popd && %s && git commit -m x" % rm_w, "Bash", w),
                ("pushd {w} && pushd {x} && popd && %s && git commit -m x" % rm_w, "Bash", x),
                ("pushd {w} && (popd) && popd && %s && git commit -m x" % rm_x, "Bash", x),
                ("v=$(cd {x}; pwd); %s && git commit -m x" % rm_w, "Bash", w),
                # a `case` pattern ends in a `)` that closes no subshell
                ("case a in a) true;; esac; %s && git commit -m x" % rm_w, "Bash", w),
                ("case a in a) (cd {x}); %s && git commit -m x;; esac" % rm_w, "Bash", w),
                ("(case a in a) cd {x};; esac); %s && git commit -m x" % rm_w, "Bash", w),
                ("Push-Location {x}; git status; Pop-Location; %s; git commit -m x" % rm_w, "PowerShell", w),
                ("pushd {x}; popd; %s; git commit -m x" % rm_w, "PowerShell", w),
                ("Push-Location; Set-Location {x}; Pop-Location; %s; git commit -m x" % rm_w, "PowerShell", w),
                ("Push-Location {x}; Pop-Location -PassThru; %s; git commit -m x" % rm_w, "PowerShell", w),
                ("pushd {x} && pushd && %s && git commit -m x" % rm_w, "Bash", w),   # a bare pushd swaps the top two
                # The folder a step leaves stays for the steps after it.
                ("pushd {x} && %s && git commit -m x" % rm_x, "Bash", w),
                ("Push-Location -Path {x}; %s; git commit -m x" % rm_x, "PowerShell", w),
                ("Push-Location -LiteralPath {x}; %s; git commit -m x" % rm_x, "PowerShell", w),
                ("pushd -- {x} && %s && git commit -m x" % rm_x, "Bash", w),
                ("(Set-Location {x}); %s; git commit -m x" % rm_x, "PowerShell", w),   # no subshell in PowerShell
                # A step after the last commit changes nothing it holds; an absolute folder is known again.
                ("%s && git commit -m x; popd" % rm_w, "Bash", w),
                ("popd; cd {w} && %s && git commit -m x" % rm_w, "Bash", w)):
            command = command.format(w="'%s'" % w.root.as_posix(), x="'%s'" % x.root.as_posix())
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, cwd.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests: removed test"), reason)

    def test_a_folder_stack_it_cannot_follow_is_refused(self):
        w, x = Repo(self, BASE), Repo(self, {"tests/test_other.py": PY_TEST})
        for command, tool in (("popd && git commit -am x", "Bash"), ("Pop-Location; git commit -am x", "PowerShell"),
                              ("pushd && git commit -am x", "Bash"), ("pushd +1 && git commit -am x", "Bash"),
                              ("pushd -n {x} && git commit -am x", "Bash"),
                              ("pushd {x} && popd +0 && git commit -am x", "Bash"),
                              ("Push-Location -StackName s {x}; Pop-Location -StackName s; git commit -am x",
                               "PowerShell"),
                              ("echo a); cd tests && git commit -am x", "Bash"),   # a `)` no `(` or `case` opened
                              ("case a in a) true;; esac; echo a); cd tests && git commit -am x", "Bash"),
                              ("pushd {x}; popd tests; git commit -am x", "Bash"),
                              ("popd; cd tests && git commit -am x", "Bash"),
                              ("popd; git add -A; cd {w} && git commit -m x", "Bash"),
                              # a `case` word that may be no keyword, at the depth of a subshell's `)`
                              ('(cd {x}; "case" a); git commit -am x', "Bash"),
                              ("(cd {x}; \\case a); git commit -am x", "Bash"),
                              # a push or pop in a pipeline or in the background
                              ("pushd {x} | cat; git commit -am x", "Bash"),
                              ("true | pushd {x}; git commit -am x", "Bash"),
                              ("pushd {x} & git commit -am x", "Bash"),
                              ("Push-Location {x} | Out-Null; git commit -am x", "PowerShell"),
                              # the stack edited or named another way
                              ("dirs -c; git commit -am x", "Bash"),
                              ("Set-Location -StackName s; git commit -am x", "PowerShell"),
                              ("Set-Location -Stack s; git commit -am x", "PowerShell")):
            command = command.format(w="'%s'" % w.root.as_posix(), x="'%s'" % x.root.as_posix())
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, w.root, tool))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR + "the guard cannot follow the folder"), reason)
                self.assertIn("after the last `popd`, `Pop-Location` or `)`", reason)
        # a file write in a folder the guard lost: what it changes cannot be placed (the file is no test)
        reason = reason_of(run_hook("popd; rm notes.txt; cd '%s' && git add -A && git commit -m x"
                                    % w.root.as_posix(), w.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "`rm` changes test files"), reason)

    @unittest.skipUnless(os.name == "nt", "Git Bash drive paths exist only on Windows")
    def test_a_git_bash_drive_path_is_read(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        posix = "/" + str(repo.root)[0].lower() + str(repo.root)[2:].replace("\\", "/")
        self.assertIsNotNone(reason_of(run_hook('git -C "%s" commit -m "x"' % posix, repo.root.parent)))

    def test_an_operation_in_progress_is_not_checked(self):
        repo = Repo(self, BASE)
        git(repo.root, "switch", "-q", "-c", "other")
        repo.change(WEAK)
        git(repo.root, "commit", "-q", "-m", "weaken")
        git(repo.root, "switch", "-q", "develop")
        git(repo.root, "merge", "-q", "--no-ff", "--no-commit", "other")
        self.assertIsNone(reason_of(run_hook('git commit -m "merge"', repo.root)))
        for name in ("CHERRY_PICK_HEAD", "REVERT_HEAD"):
            with self.subTest(head=name):
                other = Repo(self, BASE)
                other.change(WEAK)
                head = git(other.root, "rev-parse", "HEAD").strip()
                (other.root / ".git" / name).write_text(head + "\n", encoding="utf-8")
                self.assertIsNone(reason_of(run_hook('git commit -m "x"', other.root)))

    def test_a_pathspec_commit_takes_only_its_paths(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        repo.change({"src/parser.py": PY_CODE + "\n# more\n"}, stage=False)
        self.assertIsNone(reason_of(run_hook('git commit src/parser.py -m "x"', repo.root)))
        self.assertIsNone(reason_of(run_hook('git commit -o src/parser.py -m "x"', repo.root)))
        self.assertIsNotNone(reason_of(run_hook('git commit -i src/parser.py -m "x"', repo.root)))

    def test_an_amend_with_paths_takes_the_amended_commit_and_its_paths(self):
        repo = Repo(self, BASE)
        repo.change({"src/parser.py": PY_CODE + "\n# more\n"})
        git(repo.root, "commit", "-q", "-m", "more")
        repo.change(WEAK, stage=False)
        self.assertIsNotNone(reason_of(run_hook('git commit --amend tests/test_parser.py -m "x"', repo.root)))
        self.assertIsNone(reason_of(run_hook('git commit --amend src/parser.py -m "x"', repo.root)))

    def test_a_shell_glob_or_brace_in_bash_cannot_be_told(self):
        # The shell and git expand these differently: read as git does, the commit may take more.
        for command in ('git add *.py && git commit -m "x"', 'git add tests/{test_parser.py,x.py} && git commit -m "x"'):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                repo.change(WEAK, stage=False)
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith(UNCLEAR), reason)
        clean = Repo(self, BASE)   # git's reading weakens nothing either: no denial
        clean.change({"src/parser.py": PY_CODE + "\n# more\n"}, stage=False)
        self.assertIsNone(reason_of(run_hook('git add *.py && git commit -m "x"', clean.root)))
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        self.assertIsNotNone(reason_of(run_hook("git add *.py; git commit -m 'x'", repo.root, tool="PowerShell")))

    def test_redirections_are_no_words(self):
        for command, tool, stage in (('git commit -m "x" 2>&1 | tail -3', "Bash", True),
                                     ('git commit -m "x" > /dev/null', "Bash", True),
                                     ('git commit -q -m "x" 2>/dev/null', "Bash", True),
                                     ('git add -A 2>/dev/null && git commit -m "x"', "Bash", False),
                                     ("git commit -m 'x' 2>&1 | Out-String", "PowerShell", True),
                                     ("git add -A 2>$null; git commit -m 'x'", "PowerShell", False)):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                repo.change(WEAK, stage=stage)
                self.assertIsNotNone(reason_of(run_hook(command, repo.root, tool=tool)))

    def test_a_long_option_git_accepts_abbreviated_is_read(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        for command in ("git commit --mes x", "git commit --messa=x"):
            with self.subTest(command=command):
                reason = reason_of(run_hook(command, repo.root))
                self.assertIsNotNone(reason)
                self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        self.assertIsNone(reason_of(run_hook("git commit --mes x --dry", repo.root)))   # --dry-run
        git(repo.root, "commit", "-q", "-m", "weaken")
        reason = reason_of(run_hook("git commit --amen --no-edit", repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_a_help_option_value_or_path_does_not_turn_a_commit_off(self):
        repo = Repo(self, BASE)
        repo.change(WEAK, stage=False)
        reason = reason_of(run_hook("git commit -a -m -h", repo.root))
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)
        self.assertIsNotNone(reason_of(run_hook("git commit -a -m x -- --help", repo.root)))
        reason = reason_of(run_hook("git merge -m -h topic && git commit -am x", repo.root))   # a merge, not help
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "`git merge`"), reason)
        reason = reason_of(run_hook("git merge topic -- --help && git commit -am x", repo.root))   # a path
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith(UNCLEAR + "`git merge`"), reason)
        reason = reason_of(run_hook("git commit -a --help", repo.root))   # never skipped for a commit
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("weak-test-guard: this commit weakens tests"), reason)

    def test_a_dry_run_is_no_commit(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        self.assertIsNone(reason_of(run_hook('git commit --dry-run -m "x"', repo.root)))

    def test_message_bodies_hold_no_commit_options(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        for command, tool in (("cat > notes <<'EOF'\ngit commit --dry-run\nEOF\ngit commit -m x", "Bash"),
                              ("git commit -m @'\nwon't --dry-run\n'@", "PowerShell")):
            with self.subTest(command=command):
                self.assertIsNotNone(reason_of(run_hook(command, repo.root, tool)))

    def test_diff_prefix_settings_do_not_change_the_paths(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        git(repo.root, "config", "diff.mnemonicPrefix", "true")
        reason = reason_of(run_hook('git commit -m "x"', repo.root))
        self.assertIn("removed test `test_rejects_garbage` in tests/test_parser.py;", reason)

    def test_staging_in_another_repository_fails_open(self):
        repo, other = Repo(self, BASE), Repo(self, BASE)
        repo.change(WEAK)
        other.change(WEAK, stage=False)
        command = 'git -C "%s" add -A && git commit -m "x"' % other.root
        self.assertIsNone(reason_of(run_hook(command, repo.root)))
        self.assertEqual(git(other.root, "diff", "--cached", "--name-only"), "")

    def test_the_git_calls_share_one_deadline(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        saved = GUARD.DEADLINE
        try:
            GUARD.DEADLINE = 0.0
            with self.assertRaisesRegex(RuntimeError, "out of time"):
                GUARD.check('git commit -m "x"', str(repo.root), False)
        finally:
            GUARD.DEADLINE = saved

    def test_a_filter_name_that_c_cannot_spell_stops_the_replay(self):
        repo = Repo(self, BASE)
        git(repo.root, "config", "filter.a=b.clean", "cat")
        GUARD._deadline[0] = time.monotonic() + 8
        with self.assertRaisesRegex(RuntimeError, "cannot spell"):
            GUARD._no_filters(str(repo.root))

    def test_a_diff_over_the_cap_is_not_read(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        saved = GUARD.MAX_DIFF
        try:
            self.assertIsNotNone(GUARD.check('git commit -m "x"', str(repo.root), False))
            GUARD.MAX_DIFF = 10
            self.assertIsNone(GUARD.check('git commit -m "x"', str(repo.root), False))
        finally:
            GUARD.MAX_DIFF = saved


class BeforeApprovalTests(unittest.TestCase):
    """The hook runs before the command is approved: it must not run the repository's programs or write files."""

    def marker(self, repo):
        path = repo.root.parent / (repo.root.name + " marker")
        self.addCleanup(lambda: path.exists() and path.unlink())
        return path

    def test_a_reused_ref_is_never_an_option(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        marker = self.marker(repo)
        reason = reason_of(run_hook('git commit -C "--output=%s"' % marker.as_posix(), repo.root))
        self.assertIsNotNone(reason)
        self.assertFalse(marker.exists())

    def test_a_filter_driver_is_not_run(self):
        for keys in (("clean",), ("process",), ("clean", "process")):   # git-lfs sets both
            with self.subTest(keys=keys):
                repo = Repo(self, BASE)
                marker = self.marker(repo)
                for key in keys:
                    git(repo.root, "config", "filter.mark." + key, "touch '%s'; cat" % marker.as_posix())
                (repo.root / ".gitattributes").write_text("*.py filter=mark\n", encoding="utf-8")
                repo.change(WEAK, stage=False)
                self.assertIsNotNone(reason_of(run_hook('git add -A && git commit -m "x"', repo.root)))
                self.assertFalse(marker.exists())
                subprocess.run(["git", "add", "tests/test_parser.py"], cwd=repo.root, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)   # a process filter that speaks no protocol fails the add
                self.assertTrue(marker.exists(), "control: git add runs this filter")

    def test_a_signature_program_is_not_run(self):
        repo = Repo(self, BASE)
        folder = Path(tempfile.mkdtemp(prefix="wtggpg"))   # no space: git starts the program by its path
        self.addCleanup(shutil.rmtree, folder, True)
        marker, program = folder / "marker", folder / "gpg.sh"
        program.write_bytes(("#!/bin/sh\ntouch '%s'\n" % marker.as_posix()).encode("utf-8"))
        program.chmod(0o755)
        head = git(repo.root, "rev-parse", "HEAD").strip()
        tree = git(repo.root, "rev-parse", "HEAD^{tree}").strip()
        who = "t <t" + "@example.invalid> 0 +0000"
        body = ("tree %s\nparent %s\nauthor %s\ncommitter %s\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n"
                " -----END PGP SIGNATURE-----\n\nfix: x\n" % (tree, head, who, who))
        signed = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo.root,
                                input=body.encode("utf-8"), stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        git(repo.root, "config", "log.showSignature", "true")
        git(repo.root, "config", "gpg.program", program.as_posix())
        repo.change(WEAK)
        self.assertIsNotNone(reason_of(run_hook("git commit -C %s" % signed, repo.root)))
        self.assertFalse(marker.exists())
        git(repo.root, "log", "-1", signed)
        self.assertTrue(marker.exists(), "control: git log runs this signature program")

    def test_the_fsmonitor_program_is_not_run(self):
        repo = Repo(self, BASE)
        folder = Path(tempfile.mkdtemp(prefix="wtgfsm"))   # no space: Git for Windows starts a hook script by its path
        self.addCleanup(shutil.rmtree, folder, True)
        marker, hook = folder / "marker", folder / "fsmonitor.sh"
        hook.write_bytes(("#!/bin/sh\ntouch '%s'\n" % marker.as_posix()).encode("utf-8"))
        hook.chmod(0o755)
        repo.change(WEAK, stage=False)
        git(repo.root, "config", "core.fsmonitor", hook.as_posix())
        git(repo.root, "status", "--short")
        self.assertTrue(marker.exists(), "control: git runs this fsmonitor program")
        marker.unlink()
        self.assertIsNotNone(reason_of(run_hook('git add -A && git commit -m "x"', repo.root)))
        self.assertFalse(marker.exists())


class InteractiveAndLazyFetchTests(unittest.TestCase):
    """Two more programs git can start before the command is approved: an editor and a promisor remote."""

    def test_an_editor_option_in_any_spelling_is_not_replayed(self):
        repo = Repo(self, BASE)
        folder = Path(tempfile.mkdtemp(prefix="wtgedit"))
        self.addCleanup(shutil.rmtree, folder, True)
        marker = folder / "marker"
        git(repo.root, "config", "core.editor", "echo ran >> '%s' #" % marker.as_posix())
        repo.change(WEAK, stage=False)
        env = without_editors()
        control = subprocess.run(["git", "add", "-e", "tests/test_parser.py"], cwd=repo.root, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(marker.exists(), "control: git add -e runs core.editor (exit %s)" % control.returncode)
        for flags in ("-ve", "-ev", "--ed", "--edi", "-vp", "--pat", "--inter"):
            with self.subTest(flags=flags):
                marker.unlink(missing_ok=True)
                proc = run_hook("git add %s tests/test_parser.py && git commit -m x" % flags, repo.root, env=env)
                self.assertIsNone(reason_of(proc))
                self.assertFalse(marker.exists())

    def test_a_missing_blob_of_a_partial_clone_is_not_fetched(self):
        version = subprocess.run(["git", "--version"], stdout=subprocess.PIPE).stdout.decode()
        numbers = [int(part) for part in version.split()[2].split(".")[:2]]
        if numbers < [2, 45]:
            self.skipTest("GIT_NO_LAZY_FETCH needs git 2.45; this is " + version.strip())
        source = Repo(self, BASE)
        git(source.root, "config", "uploadpack.allowFilter", "true")
        git(source.root, "config", "uploadpack.allowAnySHA1InWant", "true")
        source.change(WEAK)
        git(source.root, "commit", "-q", "-m", "weaker")
        clone = Path(tempfile.mkdtemp(prefix="wtgclone"))
        self.addCleanup(shutil.rmtree, clone, True)
        git(clone.parent, "clone", "-q", "--filter=blob:none", "--no-checkout", "file://" + source.root.as_posix(),
            str(clone / "c"))
        work = clone / "c"
        git(work, "reset", "-q")

        def missing():
            out = git(work, "rev-list", "--objects", "--missing=print", "HEAD~1..HEAD", "--")
            return len([line for line in out.splitlines() if line.startswith("?")])
        before = missing()
        self.assertGreater(before, 0)
        self.assertIsNone(reason_of(run_hook("git commit --amend -m x", work)))   # fails open: the diff cannot run
        self.assertEqual(missing(), before)
        git(work, "diff", "--cached", "HEAD~1", "--", "*.py")
        self.assertLess(missing(), before, "control: a plain git diff fetches the blob")


class FailOpenTests(unittest.TestCase):
    def test_other_commands_and_places_print_nothing(self):
        repo = Repo(self, BASE)
        repo.change(WEAK)
        not_a_repo = Path(tempfile.mkdtemp(prefix="weak test guard plain "))
        self.addCleanup(shutil.rmtree, not_a_repo, True)
        for command, cwd, tool in (("git status", repo.root, "Bash"), ("echo commit", repo.root, "Bash"),
                                   ("echo git\necho commit", repo.root, "Bash"),
                                   ("git log --oneline -1; echo commit", repo.root, "Bash"),
                                   ('git commit -m "x"', not_a_repo, "Bash"), ('git commit -m "x"', repo.root, "Write")):
            with self.subTest(command=command, tool=tool):
                self.assertIsNone(reason_of(run_hook(command, cwd, tool=tool)))

    def test_wrong_event_shapes_print_nothing(self):
        for event in ([], {"tool_name": "Bash", "tool_input": ["git commit"]},
                      {"tool_name": "Bash", "tool_input": {"command": 7}},
                      {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": 7}):
            with self.subTest(event=event):
                self.assertIsNone(reason_of(run_hook(None, None, event=event)))


class WiringTests(unittest.TestCase):
    def handlers(self):
        groups = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        return [(group["matcher"], hook) for group in groups for hook in group["hooks"]
                if "weak_test_guard.py" in hook["command"]]

    def test_one_handler_per_shell_filtered_on_commit(self):
        found = sorted((matcher, hook.get("if"), hook["timeout"]) for matcher, hook in self.handlers())
        self.assertEqual(found, [("Bash", "Bash(*commit*)", 20), ("PowerShell", "PowerShell(*commit*)", 20)])

    def test_the_shipped_command_denies_through_the_launcher(self):
        sh = shutil.which("sh") if os.name != "nt" else str(Path(os.environ["ProgramFiles"], "Git", "bin", "sh.exe"))
        repo = Repo(self, BASE)
        repo.change(WEAK)
        data = Path(tempfile.mkdtemp(prefix="weak test guard data "))
        self.addCleanup(shutil.rmtree, data, True)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update(CLAUDE_PLUGIN_ROOT=CORE.as_posix(), CLAUDE_PLUGIN_DATA=data.as_posix())
        for matcher, hook in self.handlers():
            with self.subTest(matcher=matcher):
                event = json.dumps({"tool_name": matcher, "tool_input": {"command": "git commit -m 'x'"},
                                    "cwd": str(repo.root)})
                proc = subprocess.run([sh, "-c", hook["command"]], input=event.encode("utf-8"), env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                self.assertIn("removed test `test_rejects_garbage`", reason_of(proc))


if __name__ == "__main__":
    unittest.main()
