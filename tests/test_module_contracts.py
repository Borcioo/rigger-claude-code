"""Contract test of the module manifests (spec section 12): `assemble.py --check` over the repository, with a
planted duplicate it must catch, and no public manifest naming a denylist; every contract fixture of every
manifest's `tests.contract` through rigger-core's `slot_check.py`, and the default fillers against their
contracts."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "rigger-core"
ASSEMBLE = CORE / "tools" / "assemble" / "assemble.py"
MANIFEST = "rigger-module.json"
MODULES = 10
SUMMARY = re.compile(r"^# check: modules (\d+), rules files (\d+), findings 0$")
SLOT_CHECK_DIR = CORE / "tools" / "slot-check"
ASSEMBLE_DIR = CORE / "tools" / "assemble"
WINDOWS = ROOT / "plugins" / "rigger-windows"
SHELLWISE = ROOT / "plugins" / "rigger-shellwise"
LESSONS = ROOT / "plugins" / "rigger-lessons"
# Written by the test into the reviewer's head tree: the sweep refuses a committed binary file.
HEAD_EXTRA = {"reviewer": {"data.bin": b"\x00\x01binary\x00\xff\n"}}
GIT_IDENTITY = dict(GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test", GIT_COMMITTER_NAME="test",
                    GIT_COMMITTER_EMAIL="test")


def import_from(folder, name):
    """Import `name` from `folder`, leaving sys.path as it was."""
    sys.path.insert(0, str(folder))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(folder))


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def plain_env(**extra):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_", "SHELL_GUARD"))}
    env.update(extra)
    return env


def fail_rule(name, rules):
    """The rule id a `fail-<rule id>[-<label>].<ext>` file names: the longest id of `rules` it starts with."""
    stem = name[len("fail-"):].split(".", 1)[0]
    found = [rule for rule in rules if stem == rule or stem.startswith(rule + "-")]
    return max(found, key=len) if found else None


def build_repo(tmp, fixtures, slot):
    """A two-commit repository from `<fixtures>/repo/base` (empty when absent) and `repo/head`; (repo, base, head)."""
    repo = tmp / ("repo-" + slot)
    repo.mkdir()
    config = tmp / "gitconfig"
    config.write_bytes(b"")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(config), GIT_CEILING_DIRECTORIES=str(tmp),
               **GIT_IDENTITY)

    def git(*args):
        proc = subprocess.run(["git"] + list(args), cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode:
            raise AssertionError("git %s: %s" % (args[0], proc.stderr.decode("utf-8", "replace")))
        return proc.stdout.decode("ascii").strip()

    def lay(state, extra):
        for entry in os.listdir(str(repo)):
            if entry != ".git":
                path = repo / entry
                shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
        source = fixtures / "repo" / state
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    target = repo / path.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
        for name, data in extra.items():
            (repo / name).write_bytes(data)
        git("add", "-A")
        git("commit", "-q", "--allow-empty", "-m", state)
        return git("rev-parse", "HEAD")

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    base = lay("base", {})
    head = lay("head", HEAD_EXTRA.get(slot, {}))
    return repo, base, head


def module_dirs():
    return sorted(path.parent for path in (ROOT / "plugins").glob("*/" + MANIFEST))


def run_check(dirs, config_dir):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("CLAUDE_", "HARNESS_")) and key.upper() != "RIGGER_MODULE_DIRS"}
    env.update(CLAUDE_PLUGIN_ROOT=str(CORE), CLAUDE_CONFIG_DIR=str(config_dir), RIGGER_MODULE_DIRS="")
    proc = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(ASSEMBLE), "--check", "--modules"] + [str(d) for d in dirs],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=120,
    )
    return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n"), proc.stderr.decode("utf-8", "replace")


def denylist_findings(paths):
    """The manifests among `paths` that hold `lessons.denylist`; refuses an empty list (nothing read)."""
    paths = list(paths)
    if not paths:
        raise AssertionError("0 manifests read")
    found = []
    for path in paths:
        data = json.loads(path.read_bytes().decode("utf-8"))
        lessons = data.get("lessons") if isinstance(data, dict) else None
        if isinstance(lessons, dict) and "denylist" in lessons:
            found.append(path)
    return found


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="contracts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "config"
        self.config.mkdir()

    def test_check_over_repository(self):
        dirs = module_dirs()
        self.assertEqual(len(dirs), MODULES, dirs)
        code, out, err = run_check(dirs, self.config)
        self.assertEqual(code, 0, out + err)
        match = SUMMARY.match(out.splitlines()[-1])
        self.assertIsNotNone(match, out)
        self.assertEqual(int(match.group(1)), MODULES, out)
        self.assertGreater(int(match.group(2)), 0, out)

    def test_check_catches_a_planted_duplicate(self):
        copy = self.tmp / "plugins" / "rigger-talk"
        shutil.copytree(ROOT / "plugins" / "rigger-talk", copy, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / "rules" / "tests.md").write_bytes(b"## Tests\n- planted\n")
        manifest = json.loads((copy / MANIFEST).read_bytes().decode("utf-8"))
        manifest["rules"].append({"file": "rules/tests.md", "audience": "both"})
        (copy / MANIFEST).write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        dirs = [copy if d.name == "rigger-talk" else d for d in module_dirs()]
        self.assertIn(copy, dirs)
        code, out, err = run_check(dirs, self.config)
        self.assertEqual(code, 1, out + err)
        self.assertIn("duplicate rule id tests.tests in rigger-rules and rigger-talk", out.splitlines(), out)

    def test_no_public_manifest_names_a_denylist(self):
        manifests = [d / MANIFEST for d in module_dirs()]
        self.assertEqual(len(manifests), MODULES)
        self.assertEqual(denylist_findings(manifests), [])
        planted = self.tmp / MANIFEST
        planted.write_bytes(json.dumps({"name": "mod-company", "lessons": {"denylist": "leak/terms.txt"}}).encode("utf-8"))
        self.assertEqual(denylist_findings(manifests + [planted]), [planted])
        with self.assertRaisesRegex(AssertionError, "0 manifests read"):
            denylist_findings([])

    def test_every_contract_fixture(self):
        slot_check = import_from(SLOT_CHECK_DIR, "slot_check")
        manifest = import_from(ASSEMBLE_DIR, "manifest")
        read = 0
        for module_dir in module_dirs():
            module = manifest.load_manifest(module_dir, None, "repository")
            for slot in module.tests.get("contract", []):
                folder = module_dir / "tests" / "contract" / slot
                where = "%s %s" % (module.name, slot)
                self.assertTrue(folder.is_dir(), where)
                self.assertIn(slot, slot_check.RULES, where)
                rules = slot_check.RULES[slot]
                files = sorted(p for p in folder.iterdir() if p.is_file())
                strays = [p.name for p in files if not p.name.startswith(("pass", "fail-"))]
                self.assertEqual(strays, [], where)
                self.assertTrue([p for p in files if p.name.startswith("pass")], where + ": no pass file")
                git_rules = [rule for rule in rules if rule in slot_check.GIT_RULES]
                repo = base = head = None
                if git_rules:
                    self.assertTrue((folder / "repo" / "head").is_dir(), where + ": git rules need repo/head")
                    repo, base, head = build_repo(self.tmp, folder, slot)
                refused = set()
                for path in files:
                    text = path.read_bytes().decode("utf-8")
                    if repo is not None:
                        text = text.replace("{base}", base).replace("{head}", head)
                    verdict = slot_check.check(slot, text, repo=repo, base=base, head=head)
                    read += 1
                    with self.subTest(fixture="%s/%s" % (where, path.name)):
                        if path.name.startswith("pass"):
                            self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
                        else:
                            rule = fail_rule(path.name, rules)
                            self.assertIsNotNone(rule, "%s names no rule of %s" % (path.name, rules))
                            self.assertEqual((verdict.ok, verdict.rule), (False, rule), verdict)
                            refused.add(rule)
                with self.subTest(slot=where):
                    self.assertEqual([rule for rule in rules if rule not in refused], [],
                                     "rules without a fail- fixture")
        self.assertGreater(read, 0, "0 fixtures read")
        sys.stdout.write("# contract fixtures: %d\n" % read)

    def test_every_trigger_section_names_a_section_that_exists(self):
        # `section` is ignored by the renderer (one merged table), so a stale id would go unnoticed
        sections = import_from(ASSEMBLE_DIR, "sections")
        known, rows, stale = set(), 0, []
        manifests = [(d, json.loads((d / MANIFEST).read_bytes().decode("utf-8"))) for d in module_dirs()]
        for module_dir, data in manifests:
            for entry in data.get("rules", []):
                path = module_dir / entry["file"]
                known.update(section.id for section in sections.split(path.stem, path.read_bytes()))
        self.assertIn("skills.skill-triggers", known)
        for module_dir, data in manifests:
            for row in data.get("triggers", []):
                rows += 1
                if "section" in row and row["section"] not in known:
                    stale.append("%s %s: %s" % (module_dir.name, row["id"], row["section"]))
        self.assertGreater(rows, 0, "0 trigger rows read")
        self.assertEqual(stale, [])

    def test_fail_rule_takes_the_longest_id(self):
        rules = ("coverage-sum", "coverage-scope", "path", "base-head", "status", "status-line")
        rows = [("fail-coverage-scope-rename.json", "coverage-scope"), ("fail-path-absolute.json", "path"),
                ("fail-status-line.md", "status-line"), ("fail-status.json", "status"),
                ("fail-base-head.json", "base-head"), ("fail-pathx.json", None), ("fail-coverage.json", None)]
        for name, rule in rows:
            self.assertEqual(fail_rule(name, rules), rule, name)

    def test_default_fillers_meet_their_contracts(self):
        slot_check = import_from(SLOT_CHECK_DIR, "slot_check")
        manifest = import_from(ASSEMBLE_DIR, "manifest")
        # shell-guard (a): the one PowerShell handler, run through sh as Claude Code runs it
        hooks = json.loads((WINDOWS / "hooks" / "hooks.json").read_bytes().decode("utf-8"))
        commands = [hook["command"] for group in hooks["hooks"]["PreToolUse"] for hook in group["hooks"]
                    if "--pwsh-all" in hook["command"]]
        self.assertEqual(len(commands), 1, commands)
        # the command starts `sh` by name, as the hook does: its folder is on PATH
        env = plain_env(CLAUDE_PLUGIN_ROOT=str(WINDOWS), POWERSHELL_TELEMETRY_OPTOUT="1",
                        PATH=os.path.dirname(find_sh()) + os.pathsep + os.environ.get("PATH", ""))

        def guard(command):
            event = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": command},
                                "cwd": str(self.tmp)}).encode("utf-8")
            proc = subprocess.run([find_sh(), "-c", commands[0]], input=event, env=env, cwd=str(self.tmp),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual((proc.returncode, proc.stderr), (0, b""), command)
            return proc.stdout.decode("utf-8")

        self.assertEqual(guard("Get-ChildItem"), "")
        self.assertTrue(slot_check.check("shell-guard", guard("Get-ChildItem")).ok)
        reply = guard("git commit --no-verify -m x")
        self.assertEqual(json.loads(reply)["hookSpecificOutput"]["permissionDecision"], "deny", reply)
        verdict = slot_check.check("shell-guard", reply)
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)
        self.assertIn("[git-no-verify]", json.loads(reply)["hookSpecificOutput"]["permissionDecisionReason"])
        # shell-guard (b) and (d): the declared failure mode and the rule checker the manifest names
        windows = manifest.load_manifest(WINDOWS, None, "repository")
        self.assertEqual(windows.fills, "shell-guard")
        self.assertIn(windows.failure_mode, manifest.FAILURE_MODES)
        checker_path = WINDOWS / windows.provides["shell-guard"]["rule_checker"]
        saved_path, saved_modules = list(sys.path), set(sys.modules)
        try:
            sys.path.insert(0, str(checker_path.parent))
            spec = importlib.util.spec_from_file_location("contract_rule_checker", str(checker_path))
            checker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(checker)
        finally:
            sys.path[:] = saved_path
            for name in set(sys.modules) - saved_modules:
                del sys.modules[name]
        self.assertTrue(hasattr(checker, "RULES"))
        self.assertTrue(callable(getattr(checker, "rule_guard_problem", None)))
        # lessons (a): the SessionStart line, on a scratch inbox of two entries
        config = self.tmp / "lessons-config"
        config.mkdir()
        inbox = config / "lessons-inbox.md"
        inbox.write_bytes(b"# Lessons inbox\n\n- 2026-01-01 \xc2\xb7 global \xc2\xb7 a \xc2\xb7 b \xc2\xb7 c\n"
                          b"- 2026-01-02 \xc2\xb7 global \xc2\xb7 d \xc2\xb7 e \xc2\xb7 f\n")
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(LESSONS / "hooks" / "lessons_start.py")],
                              env=plain_env(CLAUDE_CONFIG_DIR=str(config), HARNESS_LESSONS_INBOX=str(inbox),
                                            HOME=str(self.tmp), USERPROFILE=str(self.tmp)),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        text = proc.stdout.decode("utf-8")
        self.assertEqual((proc.returncode, proc.stderr), (0, b""), text)
        self.assertTrue(text.splitlines()[0].endswith(": 2 entries"), text)
        verdict = slot_check.check("lessons", text)
        self.assertEqual((verdict.ok, verdict.rule), (True, ""), verdict)

    def test_shellwise_filler_meets_its_contract(self):
        # D85: each contract fixture, printed by a stub binary, reaches the caller of the PreToolUse command unchanged
        slot_check = import_from(SLOT_CHECK_DIR, "slot_check")
        hooks_path = SHELLWISE / "hooks" / "hooks.json"
        self.assertTrue(hooks_path.is_file(), "no shellwise hooks.json")
        hooks = json.loads(hooks_path.read_bytes().decode("utf-8"))["hooks"]
        commands = [hook["command"] for group in hooks["PreToolUse"] for hook in group["hooks"]]
        self.assertEqual(len(commands), 1, commands)
        fixtures = sorted(p for p in (SHELLWISE / "tests" / "contract" / "shell-guard").iterdir() if p.is_file())
        self.assertEqual(len(fixtures), 5, fixtures)
        rules = slot_check.RULES["shell-guard"]
        stub = self.tmp / "bin" / "shellwise"
        stub.parent.mkdir()
        data = self.tmp / "data"
        (data / "shellwise").mkdir(parents=True)
        (data / "shellwise" / "current").write_bytes(str(stub).encode("utf-8") + b"\n")
        env = plain_env(CLAUDE_PLUGIN_ROOT=SHELLWISE.as_posix(), CLAUDE_PLUGIN_DATA=str(data),
                        PATH=os.path.dirname(find_sh()) + os.pathsep + os.environ.get("PATH", ""))
        event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(self.tmp)}).encode("utf-8")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                stub.write_bytes(("#!/bin/sh\ncat >/dev/null\ncat '%s'\n" % path.as_posix()).encode("utf-8"))
                os.chmod(str(stub), 0o755)
                proc = subprocess.run([find_sh(), "-c", commands[0]], input=event, env=env, cwd=str(self.tmp),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, path.read_bytes(), b""))
                verdict = slot_check.check("shell-guard", proc.stdout.decode("utf-8"))
                want = (True, "") if path.name.startswith("pass") else (False, fail_rule(path.name, rules))
                self.assertEqual((verdict.ok, verdict.rule), want, verdict)

    def test_shellwise_filler_manifest(self):
        # the rule checker key is added by Task 18
        manifest = import_from(ASSEMBLE_DIR, "manifest")
        module = manifest.load_manifest(SHELLWISE, None, "repository")
        self.assertEqual(module.fills, "shell-guard")
        self.assertEqual(module.failure_mode, "closed")
        self.assertNotIn("rule_checker", module.provides["shell-guard"])
        self.assertEqual(module.tests.get("contract"), ["shell-guard"])

    def test_testing_filler_ships_the_commit_guards(self):
        # D71: the default testing filler, found through the level-1 defaults and the manifest that provides it,
        # ships the commit-time weak-test guard on both shells with these exact `if` filters.
        defaults = json.loads((CORE / "rigger-defaults.json").read_bytes().decode("utf-8"))
        target = defaults["slots"]["testing"]["target"]
        plugin, skill = target.split(":")
        roots = [d for d in module_dirs()
                 if json.loads((d / MANIFEST).read_bytes().decode("utf-8")).get("name") == plugin]
        self.assertEqual(len(roots), 1, "modules named %s" % plugin)
        data = json.loads((roots[0] / MANIFEST).read_bytes().decode("utf-8"))
        self.assertEqual(data.get("fills"), "testing")
        self.assertEqual(data.get("provides", {}).get("testing"), {"kind": "skill", "target": target})
        self.assertTrue((roots[0] / "skills" / skill / "SKILL.md").is_file(), target)
        hooks = json.loads((roots[0] / "hooks" / "hooks.json").read_bytes().decode("utf-8"))["hooks"]
        guards = sorted((group.get("matcher"), hook.get("if")) for group in hooks.get("PreToolUse", [])
                        for hook in group.get("hooks", []) if "weak_test_guard.py" in hook.get("command", ""))
        self.assertEqual(guards, [("Bash", "Bash(*commit*)"), ("PowerShell", "PowerShell(*commit*)")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
