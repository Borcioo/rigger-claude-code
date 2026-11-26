"""Every fixture git the suites start acts on its own folder when GIT_DIR and GIT_WORK_TREE are inherited.

Each site below runs, in a child, the helpers or the cases that start a test file's fixture gits, with GIT_DIR and
GIT_WORK_TREE aimed at a scratch probe repository and its own work tree. The probe's git dir must stay byte-identical: a leaked `git init`
writes `core.worktree` or `bare` into its config, a leaked `git add` or `hash-object` writes its index and objects.
The code under test is kept out of the probe (run_hook gets no GIT_* variable): only the fixture gits are checked here.
"""

import ast
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TESTING = ROOT / "plugins" / "rigger-testing" / "tools"
WEAK_TEST_GUARD = TESTING / "weak-test-guard" / "tests" / "test_weak_test_guard.py"
COMMIT_GATE = TESTING / "test-evidence" / "tests" / "test_commit_gate.py"
# Every git the child starts, leaked or not, gets an editor that does nothing and no prompt.
QUIET = {"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0", "EDITOR": "true",
         "VISUAL": "true"}
DONE = "# site %s: done"


def without_git_variables(env):
    return {key: value for key, value in env.items() if not key.upper().startswith("GIT_")}


def writable(function, path, _error):
    os.chmod(path, stat.S_IWRITE)   # git's object files are read-only on Windows
    function(path)


def control_git(cwd, *args):
    """git with no inherited GIT_* variable and no user or system config: builds the probe and reads fixtures."""
    env = without_git_variables(os.environ)
    env.update(QUIET, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CEILING_DIRECTORIES=str(Path(cwd).parent))
    proc = subprocess.run(["git", "-c", "user.name=p", "-c", "user.email=p" + "@example.invalid", *args],
                          cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    assert proc.returncode == 0, (args, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def assert_tracked(repo, names):
    """Positive control: the fixture repository itself got what its helper was asked to do."""
    tracked = sorted(control_git(repo, "ls-files").split())
    assert tracked == sorted(names), (repo, tracked)


def run_cases(module, names):
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromNames(names, module))
    assert (result.testsRun, result.wasSuccessful(), len(result.skipped)) == (len(names), True, 0), result


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def site_test_gate(scratch):
    import test_gate
    test_gate.init_repo(scratch / "gate")   # git init, config, add -A, commit through test_gate.git
    assert_tracked(scratch / "gate", [".gitignore", "a.txt"])


def site_test_plugin_refs(scratch):
    import test_plugin_refs
    run_cases(test_plugin_refs, ["PluginRefs.test_control_catches_planted_reference"])


def site_test_select_tests(scratch):
    import test_select_tests
    repo = scratch / "select"
    repo.mkdir()
    test_select_tests.git(repo, "init", "-q")
    test_select_tests.write(repo, "a.txt", "a\n")
    test_select_tests.git(repo, "add", "-A")
    assert_tracked(repo, ["a.txt"])


def site_test_shellcheck(scratch):
    import test_shellcheck
    case = test_shellcheck.ShellcheckTest("test_a_clean_file_passes_the_same_call")   # setUp skips without shellcheck
    try:
        assert_tracked(case.repo({"clean.sh": "#!/bin/sh\n"}), ["clean.sh"])
    finally:
        case.doCleanups()


def site_test_commit_gate(scratch):
    # The file runs its cases at import (sys.exit(main())), so only gate_git is compiled from its source.
    found = [node for node in ast.parse(COMMIT_GATE.read_text(encoding="utf-8")).body
             if isinstance(node, ast.FunctionDef) and node.name == "gate_git"]
    assert len(found) == 1, found
    space = {"os": os, "subprocess": subprocess}
    exec(compile(ast.Module(body=found, type_ignores=[]), str(COMMIT_GATE), "exec"), space)
    repo = scratch / "commit-gate"
    repo.mkdir()
    space["gate_git"](str(repo), "init", "-q")
    (repo / "a.txt").write_bytes(b"a\n")
    space["gate_git"](str(repo), "add", "-A")
    assert_tracked(repo, ["a.txt"])


def site_test_weak_test_guard(scratch):
    module = load(WEAK_TEST_GUARD, "test_weak_test_guard")
    hook = module.run_hook

    def run_hook(command, cwd, tool="Bash", event=None, env=None):
        return hook(command, cwd, tool, event, without_git_variables(os.environ if env is None else env))

    with mock.patch.object(module, "run_hook", run_hook):
        run_cases(module, ["BeforeApprovalTests.test_a_filter_driver_is_not_run",
                           "BeforeApprovalTests.test_a_signature_program_is_not_run",
                           "InteractiveAndLazyFetchTests.test_an_editor_option_in_any_spelling_is_not_replayed"])


SITES = {name[len("site_"):]: function for name, function in globals().items() if name.startswith("site_")}


def make_probe(probe, work):
    """The probe repository and the work tree GIT_WORK_TREE names, a folder of its own, so a leaked `git init`
    records it as core.worktree. The work tree changes a tracked file the fixtures also add and holds an untracked
    one, so a leaked `git add` of either kind writes into the probe; the probe's own editor does nothing, so a
    leaked `git add -e` opens none."""
    for folder in (probe, work):
        (folder / "tests").mkdir(parents=True)
        (folder / "tests" / "test_parser.py").write_bytes(b"old\n")
    control_git(probe, "init", "-q")
    control_git(probe, "config", "core.editor", "true")
    control_git(probe, "add", "-A")
    control_git(probe, "commit", "-q", "-m", "seed")
    (work / "tests" / "test_parser.py").write_bytes(b"changed\n")
    (work / "untracked.txt").write_bytes(b"untracked\n")


def files_under(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


class GitEnvIsolationTests(unittest.TestCase):
    def test_fixture_gits_leave_an_inherited_git_dir_alone(self):
        self.assertEqual(sorted(SITES), ["test_commit_gate", "test_gate", "test_plugin_refs", "test_select_tests",
                                         "test_shellcheck", "test_weak_test_guard"])
        for site in SITES:
            with self.subTest(site=site):
                self.check(site)

    def check(self, site):
        tmp = Path(tempfile.mkdtemp(prefix="git-env-isolation-"))
        self.addCleanup(shutil.rmtree, tmp, False, writable)
        probe, work, scratch, home = tmp / "probe", tmp / "work", tmp / "scratch", tmp / "home"
        scratch.mkdir()
        home.mkdir()
        (home / ".gitconfig").write_bytes(b"")
        make_probe(probe, work)
        git_dir = probe / ".git"
        before = files_under(git_dir)
        env = {key: value for key, value in without_git_variables(os.environ).items()
               if not key.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update(QUIET, HARNESS_NO_CLAUDE="1", HOME=str(home), GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=str(home / ".gitconfig"), GIT_DIR=str(git_dir), GIT_WORK_TREE=str(work))
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(Path(__file__).resolve()), "--child", site,
                               str(scratch)], cwd=str(scratch), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        output = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        after = files_under(git_dir)
        self.assertEqual(after.get("config", b"").decode("utf-8", "replace"), before["config"].decode("utf-8"),
                         "a fixture git wrote the inherited GIT_DIR's config\n" + output)
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        self.assertEqual(changed, [], "a fixture git wrote into the inherited GIT_DIR\n" + output)
        lines = output.strip().splitlines()
        self.assertEqual((proc.returncode, lines[-1] if lines else ""), (0, DONE % site), output)


def child(site, scratch):
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.stdout.reconfigure(encoding="utf-8")
    SITES[site](Path(scratch))
    print(DONE % site, flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
