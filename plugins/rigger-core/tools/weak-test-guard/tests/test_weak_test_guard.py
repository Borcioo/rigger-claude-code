"""Checks for weak_test_guard.py, run as the hook runs it (`python -I -S <script>`, the PreToolUse event on
stdin) against real git repositories in temp folders; one case runs the shipped hooks.json command through
`sh` and the launcher.

python -I test_weak_test_guard.py
"""
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


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t" + "@example.invalid",
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

    def test_a_removed_local_variable_covers_no_test(self):
        diff = file_diff("src/a.py", ("    result = (a, b)", "    const value = compute(1)")) + file_diff(
            "tests/test_a.py", ("def test_result_value():", "    print(result, value)"))
        self.assertEqual(GUARD.findings(diff), ["removed test `test_result_value` in tests/test_a.py"])

    def test_regexes_stay_linear_on_repeated_tokens(self):
        tokens = [" ", "\t", "-", "--ab", "(", ")", "'", '"', "`", "it(", "it('", "test_", "def ", "func (", "const x = ",
                  "->", "::", ".", "assert", "expect(", "@", "#[", "/", "x", "a1", "|", "*", "export "]
        patterns = [GUARD.TEST_DEF, GUARD.SKIP, GUARD.ASSERT, GUARD.DEFINES, GUARD.COMMENT, GUARD.DECORATOR,
                    GUARD.PHP_METHOD, GUARD.PHP_TEST_MARK, GUARD.TEST_FILE, GUARD.TEST_AFFIX, GUARD.FOLDS, GUARD.STRING]
        for repeat in (40, 2000):
            start = time.perf_counter()
            for token in tokens:
                for tail in ("", "!", "x(", "=("):
                    text = token * repeat + tail
                    for pattern in patterns:
                        pattern.findall(text)
                    GUARD.split_command(text, False)
                    GUARD.split_command(text, True)
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

    def test_an_unreadable_message_file_fails_open(self):
        # The hook runs before the command, which may still write the file (`python msg.py > m.txt && git commit -F m.txt`).
        repo = self.weakened()
        (repo.root / "folder").mkdir()
        for command in ("python msg.py > m.txt && git commit -F m.txt", "git commit --file=folder"):
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

    def test_a_shell_glob_or_brace_in_bash_fails_open(self):
        for command in ('git add *.py && git commit -m "x"', 'git add tests/{test_parser.py,x.py} && git commit -m "x"'):
            with self.subTest(command=command):
                repo = Repo(self, BASE)
                repo.change(WEAK, stage=False)
                self.assertIsNone(reason_of(run_hook(command, repo.root)))
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
                                   ("echo 'next: git commit -m x'", repo.root, "Bash"),
                                   ("echo git commit -m x", repo.root, "Bash"),
                                   ('gh pr create --body "then git commit"', repo.root, "Bash"),
                                   ("git -C $repo commit -m 'x'", repo.root, "PowerShell"),
                                   ('cd "$HOME/x" && git commit -m "x"', repo.root, "Bash"),
                                   ('git mv tests/test_parser.py tests/t.py && git commit -m "x"', repo.root, "Bash"),
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
