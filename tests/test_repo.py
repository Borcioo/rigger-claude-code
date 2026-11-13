"""Repo-level tests: manifests, launcher copies, `claude plugin validate`, and the test runner."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import run_tests  # noqa: E402
import test_py_sh  # noqa: E402

MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_WITH_PY_SH = ("rigger-lessons", "rigger-core", "rigger-windows")
PLUGINS_WITH_HOOKS = PLUGINS_WITH_PY_SH + ("rigger-rules", "rigger-talk", "rigger-wp")


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def find_key(value, key):
    if isinstance(value, dict):
        return key in value or any(find_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(find_key(item, key) for item in value)
    return False


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


class ManifestTests(unittest.TestCase):
    def test_marketplace_manifest(self):
        data = load_json(MARKETPLACE)
        self.assertEqual(data["name"], "rigger")
        self.assertEqual(data["owner"], {"name": "Borcioo"})
        self.assertEqual(
            [plugin["name"] for plugin in data["plugins"]],
            [
                "rigger-lessons",
                "rigger-core",
                "rigger-rules",
                "rigger-talk",
                "rigger-wp",
                "rigger-windows",
                "rigger-panel",
            ],
        )

    def test_each_source_has_plugin_json_with_same_name(self):
        for entry in load_json(MARKETPLACE)["plugins"]:
            with self.subTest(plugin=entry["name"]):
                self.assertEqual(entry["source"], "./plugins/" + entry["name"])
                manifest = ROOT / entry["source"] / ".claude-plugin" / "plugin.json"
                self.assertTrue(manifest.is_file(), manifest)
                data = load_json(manifest)
                self.assertEqual(data["name"], entry["name"])
                self.assertEqual(data["author"], {"name": "Borcioo"})
                self.assertTrue(data["description"])

    def test_the_marketplace_and_the_plugin_describe_it_alike(self):
        for entry in load_json(MARKETPLACE)["plugins"]:
            with self.subTest(plugin=entry["name"]):
                data = load_json(ROOT / entry["source"] / ".claude-plugin" / "plugin.json")
                self.assertEqual(entry["description"], data["description"])

    def test_each_plugin_declares_its_license_and_home(self):
        home = "https://github.com/Borcioo/rigger-claude-code"
        for entry in load_json(MARKETPLACE)["plugins"]:
            with self.subTest(plugin=entry["name"]):
                data = load_json(ROOT / entry["source"] / ".claude-plugin" / "plugin.json")
                self.assertEqual(
                    (data.get("license"), data.get("homepage"), data.get("repository")),
                    ("GPL-3.0-or-later", home, home),
                )

    def test_no_version_anywhere(self):
        manifests = [MARKETPLACE] + sorted(ROOT.glob("plugins/*/.claude-plugin/plugin.json"))
        self.assertEqual(len(manifests), 8)
        for path in manifests:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertFalse(find_key(load_json(path), "version"))

    def test_py_sh_copies_identical(self):
        copies = sorted(ROOT.glob("plugins/*/bin/py.sh"))
        names = [path.parent.parent.name for path in copies]
        for name in PLUGINS_WITH_PY_SH:
            self.assertIn(name, names)
        reference = (ROOT / "plugins" / "rigger-lessons" / "bin" / "py.sh").read_bytes()
        for path in copies:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(path.read_bytes(), reference)


HOOK_EVENTS = frozenset(
    "SessionStart UserPromptSubmit PreToolUse PostToolUse PreCompact PostCompact SubagentStart SubagentStop Stop SessionEnd".split()
)
# Exit 2 from a command hook stops the tool call, the prompt or the compaction.
EXIT_2_BLOCKS = ("PreToolUse", "UserPromptSubmit", "PreCompact")
FAIL_OPEN_PLUGINS = ("rigger-core", "rigger-windows")
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
ROOT_PATH_RE = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"')
LAUNCHER = "bin/py.sh"
RULES_SCRIPTS = ("rules_start.sh", "rules_extra.sh", "talk_start.sh")


def is_function_hooks(path):
    """A function-hook plugin's hooks.json lists `modules`, not command handlers."""
    return "modules" in load_json(path)


def hook_files():
    return [path for path in sorted(ROOT.glob("plugins/*/hooks/hooks.json")) if not is_function_hooks(path)]


def hook_handlers(path):
    """(event, group, handler) of every handler in one hooks.json."""
    for event, groups in load_json(path)["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, group, handler


def load_harness_start():
    path = ROOT / "plugins" / "rigger-core" / "tools" / "harness_start" / "harness_start.py"
    spec = importlib.util.spec_from_file_location("harness_start", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["harness_start"] = module
    spec.loader.exec_module(module)
    return module


harness_start = load_harness_start()


def guard_handler(tool, script, *args, timeout=5, condition=None):
    """A live settings.json handler in its `command` + `args` form."""
    handler = {
        "type": "command",
        "command": "python",
        "args": ["-I", "-S", "~/.claude/tools/%s/%s" % (tool, script)] + list(args),
        "timeout": timeout,
    }
    if condition:
        handler["if"] = condition
    return handler


def shell_guard(flag, condition, timeout):
    return guard_handler("shell-guard", "shell_guard.py", flag, timeout=timeout, condition=condition)


USER_COMMAND = "pwsh -NoProfile -File ~/.claude/tools/my-hook.ps1"
INBOX_COMMAND = 'n=$(grep -c "x" ~/.claude/lessons-inbox.md); echo "lessons-inbox: $n entries"'
TASK_LIST_COMMAND = "echo '{\"additionalContext\":\"Keep the visible task list current.\"}'"
CONTEXT = "context-guard"

# A hand-made settings.json that copied these hooks before installing the plugins ("Migrating a
# hand-made setup" in README.md): (row, event, matcher, handler, fate once the plugins are
# installed). A fate names the plugin that now ships the handler; "stays" is the user's own and stays
# in settings.json; "replaced" goes, with a plugin script of another name doing the work, or rows
# 9-12, 14 and 15 with row 19's `*git*` handler, which starts the git rule on every git call.
HAND_MADE_HOOKS = (
    (1, "SessionStart", None, {"type": "command", "command": USER_COMMAND}, "stays"),
    (2, "SessionStart", None, {"type": "command", "command": INBOX_COMMAND}, "replaced"),
    (3, "SessionStart", "compact", guard_handler(CONTEXT, "compact_guard.py", "start"), "rigger-core"),
    (4, "PreCompact", "auto",
     guard_handler(CONTEXT, "compact_guard.py", "pre", "--hard-cap", "560000", "--coordinator-cap", "650000"),
     "rigger-core"),
    (5, "PostCompact", None, guard_handler(CONTEXT, "compact_guard.py", "post"), "rigger-core"),
    (6, "UserPromptSubmit", None, {"type": "command", "command": TASK_LIST_COMMAND}, "stays"),
    (7, "UserPromptSubmit", None, guard_handler(CONTEXT, "context_guard.py"), "rigger-core"),
    (8, "PreToolUse", "PowerShell", shell_guard("--pwsh-all", None, 10), "rigger-windows"),
    (9, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(git checkout *)", 10), "replaced"),
    (10, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*git* checkout *)", 10), "replaced"),
    (11, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(git restore *)", 10), "replaced"),
    (12, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*git* restore *)", 10), "replaced"),
    (13, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*worktree remove *)", 10), "rigger-windows"),
    (14, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*git commit *)", 10), "replaced"),
    (15, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*git* commit *)", 10), "replaced"),
    (16, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*push *--no-veri*)", 10), "rigger-windows"),
    (17, "PreToolUse", "Bash", shell_guard("--only-narrow", "Bash(*tee *)", 5), "rigger-windows"),
    (18, "PreToolUse", "Bash", shell_guard("--only-narrow", "Bash(*safe-edit*)", 5), "rigger-windows"),
    (19, "PreToolUse", "Bash", shell_guard("--only-git", "Bash(*git*)", 10), "rigger-windows"),
    (20, "PreToolUse", "Bash", shell_guard("--only-narrow", "Bash(*<<*)", 5), "rigger-windows"),
    # The `if` is unescaped twice: eight backslashes match a literal `\\`.
    (21, "PreToolUse", "Bash", shell_guard("--only-narrow", "Bash(*" + "\\" * 8 + "*)", 5), "rigger-windows"),
)


def settings_hooks(rows):
    hooks = {}
    for _, event, matcher, handler, _ in rows:
        group = {"hooks": [handler]}
        if matcher:
            group["matcher"] = matcher
        hooks.setdefault(event, []).append(group)
    return hooks


class HookFileTests(unittest.TestCase):
    def test_every_plugin_with_a_hooks_folder_has_a_valid_file(self):
        folders = [path for path in sorted(ROOT.glob("plugins/*/hooks")) if path.is_dir()]
        self.assertTrue(folders)
        for folder in folders:
            with self.subTest(folder=folder.relative_to(ROOT).as_posix()):
                path = folder / "hooks.json"
                self.assertTrue(path.is_file(), path)
                if is_function_hooks(path):
                    modules = load_json(path)["modules"]
                    self.assertTrue(modules)
                    for module in modules:
                        self.assertTrue((folder / module).is_file(), module)
                    continue
                handlers = list(hook_handlers(path))
                self.assertTrue(handlers)
                for event, group, handler in handlers:
                    self.assertIn(event, HOOK_EVENTS)
                    self.assertEqual(handler["type"], "command")
                    self.assertIsInstance(handler["command"], str)
                    self.assertIsInstance(handler["timeout"], int)
                    self.assertIsInstance(group.get("matcher", ""), str)

    def test_each_plugin_declared_with_hooks_has_a_file(self):
        for name in PLUGINS_WITH_HOOKS:
            with self.subTest(plugin=name):
                self.assertTrue((ROOT / "plugins" / name / "hooks" / "hooks.json").is_file())

    def test_commands_use_the_plugin_root_and_call_existing_files(self):
        files = hook_files()
        self.assertGreaterEqual(len(files), len(PLUGINS_WITH_HOOKS))
        checked = 0
        for path in files:
            plugin = path.parent.parent
            for event, _, handler in hook_handlers(path):
                command = handler["command"]
                with self.subTest(hooks=path.relative_to(ROOT).as_posix(), event=event, command=command):
                    self.assertNotIn("args", handler, "the args form skips sh and the launcher")
                    self.assertNotIn("\\", command.replace('\\"', ""))
                    self.assertIn(ROOT_VAR, command)
                    targets = ROOT_PATH_RE.findall(command)
                    self.assertEqual(command.count(ROOT_VAR), len(targets), "an unquoted plugin path")
                    self.assertTrue(command.startswith(f'sh "{ROOT_VAR}/'), "a hook starts through sh")
                    runs_python = any(target.endswith(".py") for target in targets)
                    self.assertEqual(LAUNCHER in targets, runs_python, "the launcher is for Python only")
                    scripts = [target for target in targets if target != LAUNCHER]
                    self.assertTrue(scripts, "no script after the launcher")
                    for target in targets:
                        self.assertTrue((plugin / target).is_file(), target)
                    checked += 1
        self.assertGreaterEqual(checked, 16)

    def test_the_stop_shadow_handler_is_async(self):
        """A synchronous Stop handler holds the end of every turn for as long as it runs."""
        handlers = [
            handler
            for path in hook_files()
            for event, _, handler in hook_handlers(path)
            if event == "Stop" and "stop_shadow.py" in handler["command"]
        ]
        self.assertEqual(len(handlers), 1)
        self.assertIs(handlers[0].get("async"), True)

    def report_shadow_handlers(self):
        return [
            (group, handler)
            for path in hook_files()
            for event, group, handler in hook_handlers(path)
            if event == "SubagentStop" and "report_shadow.py" in handler["command"]
        ]

    def test_the_report_shadow_handler_is_async_unmatched_and_short(self):
        """A synchronous SubagentStop handler holds every subagent's return for as long as it runs."""
        found = self.report_shadow_handlers()
        self.assertEqual(len(found), 1)
        group, handler = found[0]
        self.assertNotIn("matcher", group)
        self.assertIs(handler.get("async"), True)
        self.assertEqual(handler["timeout"], 10)

    def test_the_report_shadow_command_writes_its_row_through_the_launcher(self):
        found = self.report_shadow_handlers()
        self.assertEqual(len(found), 1)
        handler = found[0][1]
        plugin = ROOT / "plugins" / "rigger-core"
        data = Path(tempfile.mkdtemp(prefix="hook-data-"))
        self.addCleanup(shutil.rmtree, data, True)
        event = json.dumps({"session_id": "s1", "agent_id": "a1", "agent_type": "rigger-core:worker",
                            "last_assistant_message": "STATUS: DONE\nok"}).encode("utf-8")
        proc = self.run_hook(handler["command"], plugin, data, event)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in (data / "report-gate" / "rows").glob("*.json")]
        self.assertEqual([(row["verdict"], row["status"], row["source"]) for row in rows], [("ok", "DONE", "event")])

    def test_commands_with_a_missing_script_exit_zero(self):
        checked = 0
        for path in hook_files():
            plugin = path.parent.parent
            for event, _, handler in hook_handlers(path):
                sh_only = any(name in handler["command"] for name in RULES_SCRIPTS)
                if event not in EXIT_2_BLOCKS and (plugin.name not in FAIL_OPEN_PLUGINS or sh_only):
                    continue
                with self.subTest(hooks=path.relative_to(ROOT).as_posix(), event=event, command=handler["command"]):
                    root, data = self.bare_plugin(plugin)
                    proc = self.run_hook(handler["command"], root, data, b"{}")
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn(b"can't open file", proc.stderr, "the command never reached Python")
                    checked += 1
        self.assertGreaterEqual(checked, 11)

    def test_pretooluse_commands_still_deny_with_their_exit_zero(self):
        event = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m x"}, "cwd": str(ROOT)}
        ).encode("utf-8")
        plugin = ROOT / "plugins" / "rigger-windows"
        handlers = [
            handler
            for _, _, handler in hook_handlers(plugin / "hooks" / "hooks.json")
            if "--only-git" in handler["command"] or "--pwsh-all" in handler["command"]
        ]
        self.assertEqual(len(handlers), 4)
        data = tempfile.mkdtemp(prefix="hook-data-")
        self.addCleanup(shutil.rmtree, data, True)
        for handler in handlers:
            with self.subTest(command=handler["command"]):
                proc = self.run_hook(handler["command"], plugin, Path(data), event)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(b'"permissionDecision": "deny"', proc.stdout)

    def bare_plugin(self, plugin):
        """A copy of the plugin with its launcher and manifest but no scripts, under a path with a space."""
        base = Path(tempfile.mkdtemp(prefix="hook root "))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "plugin root"
        (root / "bin").mkdir(parents=True)
        shutil.copyfile(plugin / LAUNCHER, root / LAUNCHER)
        return root, base / "data"

    def run_hook(self, command, root, data, stdin):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["CLAUDE_PLUGIN_ROOT"] = Path(root).as_posix()
        env["CLAUDE_PLUGIN_DATA"] = Path(data).as_posix()
        env["PATH"] = os.pathsep.join((str(Path(test_py_sh.SH).parent), env["PATH"]))
        return subprocess.run(
            [test_py_sh.SH, "-c", command],
            env=env,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )


class DuplicateHookTests(unittest.TestCase):
    """A hand-made settings.json left after installing, plus every plugin hooks.json, holds no duplicate."""

    def plugin_handlers(self):
        found = []
        for path in hook_files():
            data = load_json(path)
            found.extend(harness_start.handlers(data["hooks"], "plugin " + path.parent.parent.name))
        return found

    def check(self, rows):
        settings = harness_start.handlers(settings_hooks(rows), "settings.json")
        return harness_start.duplicates(settings + self.plugin_handlers())

    def rows(self, *fates):
        return [row for row in HAND_MADE_HOOKS if row[4] in fates]

    def test_table_is_complete(self):
        self.assertEqual([row[0] for row in HAND_MADE_HOOKS], list(range(1, 22)))

    def test_settings_after_install_have_no_duplicate(self):
        self.assertEqual(len(self.rows("stays")), 2)
        self.assertEqual(self.check(self.rows("stays")), [])

    def test_row_7_left_in_settings_is_named(self):
        row = [row for row in HAND_MADE_HOOKS if row[0] == 7]
        found = self.check(self.rows("stays") + row)
        self.assertEqual(len(found), 1)
        handler, sources = found[0]
        self.assertEqual(sources, ["settings.json", "plugin rigger-core"])
        self.assertEqual(harness_start.describe(handler), "UserPromptSubmit, context_guard.py")

    def test_every_moved_row_is_a_plugin_handler_with_its_timeout(self):
        for number, event, matcher, handler, fate in self.rows("rigger-core", "rigger-windows"):
            with self.subTest(row=number):
                wanted = harness_start.handlers(settings_hooks([(number, event, matcher, handler, fate)]), "x")[0][1:]
                path = ROOT / "plugins" / fate / "hooks" / "hooks.json"
                matches = []
                for item_event, group, item in hook_handlers(path):
                    single = {item_event: [{"matcher": group.get("matcher", ""), "hooks": [item]}]}
                    if harness_start.handlers(single, "x")[0][1:] == wanted:
                        matches.append(item)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["timeout"], handler["timeout"])

    def test_plugins_hold_no_handler_besides_the_table_and_the_known_ones(self):
        moved = len(self.rows("rigger-core", "rigger-windows"))
        names = sorted(
            item.identity[1]
            for item in self.plugin_handlers()
            if item.source != "plugin rigger-lessons" and item.identity[1] not in RULES_SCRIPTS
        )
        self.assertEqual(len(names), moved + 8)
        self.assertEqual(names.count("harness_start.py"), 1)
        self.assertEqual(names.count("worktree_root.sh"), 1)
        self.assertEqual(names.count("stop_shadow.py"), 1)
        self.assertEqual(names.count("report_shadow.py"), 1)
        self.assertEqual(names.count("side_session_guard.py"), 2)
        self.assertEqual(names.count("env_check.py"), 2)

    def test_all_moved_rows_are_named_on_a_hand_made_config(self):
        self.assertEqual(len(self.check(HAND_MADE_HOOKS)), len(self.rows("rigger-core", "rigger-windows")))


BOM_BYTES = chr(0xFEFF).encode("utf-8")
NBSP_BYTES = chr(0xA0).encode("utf-8")
INVISIBLE_BYTES = tuple(chr(code).encode("utf-8") for code in range(0x200B, 0x2010)) + (NBSP_BYTES,)
INVISIBLE_RE = re.compile(b"|".join(re.escape(item) for item in (BOM_BYTES,) + INVISIBLE_BYTES))
WINDOWS_GIT_DIR_RE = re.compile(r"gitdir: ([A-Za-z]):/(.*)")


def git_env(root):
    """The environment for git in `root`; a worktree made on Windows needs explicit paths under WSL."""
    env = dict(os.environ)
    dot_git = root / ".git"
    if os.name != "nt" and dot_git.is_file():
        match = WINDOWS_GIT_DIR_RE.fullmatch(dot_git.read_text(encoding="utf-8").strip())
        if match:
            env["GIT_DIR"] = "/mnt/%s/%s" % (match.group(1).lower(), match.group(2))
            env["GIT_WORK_TREE"] = str(root)
    return env


def tracked_files(root):
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "ls-files", "-z"],
        cwd=str(root),
        env=git_env(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError("git ls-files failed: " + proc.stderr.decode("utf-8", "replace"))
    return [name for name in proc.stdout.decode("utf-8").split("\0") if name]


def invisible_findings(root):
    """(files read, ["path:line: U+XXXX"]) for the tracked text files; a NUL byte marks a binary file."""
    findings = []
    read = 0
    for name in tracked_files(root):
        data = (root / name).read_bytes()
        if b"\0" in data:
            continue
        read += 1
        for match in INVISIBLE_RE.finditer(data):
            if match.group() == BOM_BYTES and match.start() == 0:
                continue
            line = data.count(b"\n", 0, match.start()) + 1
            findings.append("%s:%d: U+%04X" % (name, line, ord(match.group().decode("utf-8"))))
    return read, findings


TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]*\.py|[^/]*\.tests\.ps1)$")


def undiscovered_tests(root):
    """(tracked test files, the ones run_tests.discover misses)."""
    tracked = [name for name in tracked_files(root) if TEST_FILE_RE.search(name)]
    found = {path.relative_to(root).as_posix() for path in run_tests.discover(root)}
    return len(tracked), [name for name in tracked if name not in found]


class DiscoveryCheckTests(unittest.TestCase):
    def test_every_tracked_test_file_is_discovered(self):
        count, missing = undiscovered_tests(ROOT)
        self.assertGreaterEqual(count, 21, "too few tracked test files read")
        self.assertEqual(missing, [])

    def test_undiscovered_files_are_named(self):
        repo = Path(tempfile.mkdtemp(prefix="discovery-"))
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, timeout=60)
        names = (
            "tests/test_a.py",
            "plugins/p/tools/t/test_d.py",
            "plugins/p/tools/t/d.tests.ps1",
            "tools/t/e.tests.ps1",
            "tests/control/test_f.py",
            "tests/mytest_g.py",
            "tests/test_h.txt",
            "tests/helper.py",
            "tests/h.tests.ps1.bak",
        )
        for name in names:
            write_text(repo / name, "")
        (repo / "tests" / "test_untracked.py").write_bytes(b"")
        subprocess.run(["git", "add", "--", *names], cwd=str(repo), check=True, timeout=60)
        self.assertEqual(
            undiscovered_tests(repo),
            (5, ["plugins/p/tools/t/test_d.py", "tests/control/test_f.py", "tools/t/e.tests.ps1"]),
        )


class ZeroWidthTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="invisible-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True, timeout=60)

    def track(self, name, data):
        path = self.repo / name
        path.write_bytes(data)
        subprocess.run(["git", "add", "--", name], cwd=str(self.repo), check=True, timeout=60)

    def test_repo_is_clean(self):
        read, findings = invisible_findings(ROOT)
        self.assertGreater(read, 50, "too few files read")
        self.assertEqual(findings, [])

    def test_each_character_is_caught_with_its_line(self):
        for code in range(0x200B, 0x2010):
            with self.subTest(code="U+%04X" % code):
                self.track("a.md", b"first\n" + b"x" + chr(code).encode("utf-8") + b"y\n")
                self.assertEqual(invisible_findings(self.repo), (1, ["a.md:2: U+%04X" % code]))

    def test_a_no_break_space_is_caught_with_its_line(self):
        self.track("a.md", b"first\nx" + NBSP_BYTES + b"y\n")
        self.assertEqual(invisible_findings(self.repo), (1, ["a.md:2: U+00A0"]))

    def test_bom_is_caught_after_offset_zero_only(self):
        self.track("start.md", BOM_BYTES + b"text\n")
        self.assertEqual(invisible_findings(self.repo), (1, []))
        self.track("later.md", b"text\n" + BOM_BYTES + b"more\n")
        self.track("twice.md", BOM_BYTES + BOM_BYTES + b"text\n")
        self.assertEqual(invisible_findings(self.repo), (3, ["later.md:2: U+FEFF", "twice.md:1: U+FEFF"]))

    def test_neighbouring_characters_pass(self):
        for code in (0x9F, 0xA1, 0x200A, 0x2010, 0x202F):
            with self.subTest(code="U+%04X" % code):
                self.track("a.md", chr(code).encode("utf-8"))
                self.assertEqual(invisible_findings(self.repo), (1, []))

    def test_binary_and_untracked_files_are_not_read(self):
        self.track("a.bin", b"\0" + INVISIBLE_BYTES[0])
        (self.repo / "untracked.md").write_bytes(INVISIBLE_BYTES[0])
        self.assertEqual(invisible_findings(self.repo), (0, []))

    def test_git_failure_is_a_failure(self):
        shutil.rmtree(self.repo / ".git")
        (self.repo / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "git ls-files failed"):
            tracked_files(self.repo)

    def test_windows_git_dir_is_translated_under_posix(self):
        if os.name == "nt":
            self.skipTest("git_env leaves the environment alone on Windows")
        (self.repo / "tree").mkdir()
        tree = self.repo / "tree"
        letter, tail = "f", "/x/.git/worktrees/w"
        (tree / ".git").write_text("gitdir: %s:%s\n" % (letter.upper(), tail), encoding="utf-8")
        env = git_env(tree)
        self.assertEqual((env.get("GIT_DIR"), env.get("GIT_WORK_TREE")), ("/mnt/%s%s" % (letter, tail), str(tree)))


class ClaudeMdTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    def test_named_helpers_exist(self):
        named = re.findall(r"`(tests/[\w./-]+\.py)` `(\w+)\(\)`", self.text)
        self.assertTrue(named, "no `tests/x.py` `helper()` pair found")
        for rel, helper in named:
            with self.subTest(rel=rel, helper=helper):
                path = ROOT / rel
                self.assertTrue(path.is_file(), rel)
                self.assertIn("def %s(" % helper, path.read_text(encoding="utf-8"))

    def test_sweep_commands_choose_a_denylist_mode(self):
        commands = re.findall(r"`[^`\n]*scripts/sweep\.py[^`\n]*`", self.text)
        self.assertTrue(commands, "no sweep command found")
        for command in commands:
            with self.subTest(command=command):
                self.assertRegex(command, r"(?<![\w-])--(?:no-)?denylist(?![\w-])")


class ClaudeValidateTests(unittest.TestCase):
    def test_claude_plugin_validate(self):
        if os.environ.get("HARNESS_NO_CLAUDE") == "1":
            self.skipTest("HARNESS_NO_CLAUDE=1 (run_tests.py --no-claude): claude plugin validate not run")
        claude = shutil.which("claude")
        self.assertIsNotNone(claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        config = tempfile.mkdtemp(prefix="harness-validate-")
        self.addCleanup(shutil.rmtree, config, True)
        env = dict(os.environ, CLAUDE_CONFIG_DIR=config)
        targets = ["."] + ["plugins/" + entry["name"] for entry in load_json(MARKETPLACE)["plugins"]]
        for target in targets:
            with self.subTest(target=target):
                proc = subprocess.run(
                    [claude, "plugin", "validate", target],
                    cwd=str(ROOT),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=60,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))


UNITTEST_OUTPUT = """\
test_a (test_x.Cases.test_a) ... ok
test_b (test_x.Cases.test_b) ... skipped 'needs claude'
test_c (test_x.Cases.test_c)
Docstring line ... skipped "it's windows-only"
test_d (test_x.Cases.test_d) ... FAIL

----------------------------------------------------------------------
Ran 4 tests in 0.010s

FAILED (failures=1, skipped=2)
"""


class RunTestsEvaluateTests(unittest.TestCase):
    def test_unittest_counts_and_skip_names(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                result = run_tests.evaluate(1, UNITTEST_OUTPUT.replace("\n", newline))
                self.assertFalse(result["ok"])
                self.assertEqual(result["tests"], 4)
                self.assertEqual(result["reported_failures"], 1)
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["skipped"], 2)
                self.assertEqual(
                    result["skip_names"],
                    ["test_b (test_x.Cases.test_b): needs claude", "test_c (test_x.Cases.test_c): it's windows-only"],
                )

    def test_exit_code_is_truth(self):
        passing = "Ran 3 tests in 0.1s\n\nOK\n"
        self.assertTrue(run_tests.evaluate(0, passing)["ok"])
        crashed = run_tests.evaluate(1, passing)
        self.assertFalse(crashed["ok"])
        self.assertEqual(crashed["failed"], 1)

    def test_exit_zero_without_summary_fails(self):
        for output in ("", "hello\n", "Ran 0 tests in 0.000s\n\nOK\n"):
            with self.subTest(output=output):
                result = run_tests.evaluate(0, output)
                self.assertFalse(result["ok"])
                self.assertEqual(result["failed"], 1)

    def test_custom_runner_summary(self):
        result = run_tests.evaluate(0, "check 1 ok\n# all 186 checks passed\n")
        self.assertTrue(result["ok"])
        self.assertEqual((result["tests"], result["summary"]), (186, "# all 186 checks passed"))
        result = run_tests.evaluate(0, "# section\n...\n# context-guard: 24 passed, 0 failed\n")
        self.assertEqual((result["tests"], result["summary"]), (24, "# context-guard: 24 passed, 0 failed"))
        self.assertFalse(run_tests.evaluate(1, "# context-guard: 23 passed, 1 failed\n")["ok"])

    def test_reported_failures_fail_a_file_that_exits_0(self):
        cases = (
            ("# a: 5 passed, 3 failed\n", 3),
            ("# a: 5 checks passed, 2 checks failed\n", 2),
            ("# a: 0 passed, 0 failed\n# b: 5 passed, 1 failed, 2 skipped\n", 1),
            ("Ran 4 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n", 2),
        )
        for output, failed in cases:
            with self.subTest(output=output):
                result = run_tests.evaluate(0, output)
                self.assertEqual((result["ok"], result["failed"], result["reported_failures"]), (False, failed, failed))
        passing = run_tests.evaluate(0, "# a: 2 failed earlier\n# a: 5 passed, 0 failed\n")
        self.assertEqual((passing["ok"], passing["failed"]), (True, 0))

    def test_executed_leaves_out_skipped_tests(self):
        cases = (
            ("Ran 4 tests in 0.1s\n\nOK (skipped=3)\n", 1),
            ("Ran 2 tests in 0.1s\n\nOK (skipped=2)\n", 0),
            ("# skipped 2 checks: reason\n# a: 51 passed, 0 failed\n", 51),
            ("# a: skipped here: reason\n", 0),
        )
        for output, executed in cases:
            with self.subTest(output=output):
                self.assertEqual(run_tests.evaluate(0, output)["executed"], executed)
        self.assertEqual(run_tests.evaluate_ps1(0, "a tests: 4 passed, 0 failed.\n")["executed"], 4)

    def test_custom_runner_with_zero_checks_fails_without_a_skip_reason(self):
        for output in ("# hello\n", "# all 0 checks passed\n", "# skipped\n", "# tool: skipped on linux\n"):
            with self.subTest(output=output):
                result = run_tests.evaluate(0, output)
                self.assertEqual((result["ok"], result["failed"], result["file_skipped"]), (False, 1, False))
                self.assertEqual(result["note"], "ran 0 checks and printed no skip reason")

    def test_custom_runner_skips_are_counted(self):
        whole = run_tests.evaluate(0, "# drift-lint: skipped on linux: Windows-only (icacls)\n")
        self.assertEqual((whole["ok"], whole["file_skipped"], whole["tests"], whole["skipped"], whole["failed"]),
                         (True, True, 0, 1, 0))
        part = run_tests.evaluate(
            0, "# skipped 2 upper-case checks: the file system is case-sensitive\n# variant-run: 51 passed, 0 failed\n"
        )
        self.assertEqual((part["ok"], part["file_skipped"], part["tests"], part["skipped"]), (True, False, 51, 2))
        # report() prints each name after "skipped ", so the runner's own leading "skipped" is dropped.
        self.assertEqual(part["skip_names"], ["2 upper-case checks: the file system is case-sensitive"])
        self.assertEqual(part["unnamed_skips"], 0)

    def test_custom_runner_count_survives_a_later_skip_line(self):
        for skip_line, skipped in (("# skipped 2 checks: reason", 2), ("# note: skipped nothing: all ran", 1)):
            with self.subTest(skip_line=skip_line):
                result = run_tests.evaluate(0, "# a: 51 passed, 0 failed\n%s\n" % skip_line)
                self.assertEqual(
                    (result["ok"], result["file_skipped"], result["tests"], result["skipped"], result["failed"]),
                    (True, False, 51, skipped, 0),
                )
                self.assertEqual(result["summary"], "# a: 51 passed, 0 failed")

    def test_custom_runner_with_a_zero_count_is_not_a_whole_file_skip(self):
        result = run_tests.evaluate(0, "# a: 0 passed, 0 failed\n# skipped 2 checks: reason\n")
        self.assertEqual((result["ok"], result["file_skipped"], result["tests"]), (False, False, 0))
        self.assertEqual(result["note"], "ran 0 checks and printed no skip reason")

    def test_a_unittest_file_with_every_test_skipped_is_reported_skip_with_its_reason(self):
        output = (
            "test_a (test_x.Cases.test_a) ... skipped 'shellcheck not on PATH'\n"
            "test_b (test_x.Cases.test_b) ... skipped 'shellcheck not on PATH'\n\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.001s\n\nOK (skipped=2)\n"
        )
        result = run_tests.evaluate(0, output)
        result.update(output=output, seconds=0.0)
        self.assertEqual((result["ok"], result["file_skipped"], result["executed"], result["skipped"]), (True, True, 0, 2))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run_tests.report("tests/test_x.py", result)
        self.assertEqual(
            buffer.getvalue().splitlines(),
            [
                "SKIP tests/test_x.py: 2 tests, 2 skipped [0.0 s]",
                "  skipped test_a (test_x.Cases.test_a): shellcheck not on PATH",
                "  skipped test_b (test_x.Cases.test_b): shellcheck not on PATH",
            ],
        )
        partly = run_tests.evaluate(0, "Ran 4 tests in 0.1s\n\nOK (skipped=3)\n")
        self.assertEqual((partly["ok"], partly["file_skipped"]), (True, False))


class RunTestsMinimumTests(unittest.TestCase):
    def test_discovery_finds_at_least_the_minimum(self):
        minimum = run_tests.read_min(ROOT)
        self.assertIsNotNone(minimum)
        found = run_tests.discover(ROOT)
        self.assertGreaterEqual(len(found), minimum, "fewer test files than %s says" % run_tests.MIN_FILE)


ENV_PROBE = """\
import json, os, sys, unittest
from pathlib import Path

class EnvProbe(unittest.TestCase):
    def test_env(self):
        keys = sorted(k for k in os.environ if k.upper().startswith(("CLAUDE_", "HARNESS_")))
        out = Path(__file__).resolve().parent.parent / "env.json"
        out.write_text(json.dumps({"keys": keys, "cwd": os.getcwd(), "isolated": sys.flags.isolated}))

if __name__ == "__main__":
    unittest.main(verbosity=2)
"""

SLEEPER = "import time\ntime.sleep(30)\n"

PASSING_CONTROL = """\
import unittest

class Passing(unittest.TestCase):
    def test_ok(self):
        pass

if __name__ == "__main__":
    unittest.main(verbosity=2)
"""

ALL_SKIPPED = """\
import unittest

@unittest.skip("nothing here")
class AllSkipped(unittest.TestCase):
    def test_a(self):
        self.fail("never")

    def test_b(self):
        self.fail("never")

if __name__ == "__main__":
    unittest.main(verbosity=2)
"""

# The child starts a grandchild that inherits stdout and beats on a file until it is killed.
GRANDCHILD = """\
import time
from pathlib import Path

beats = Path(__file__).resolve().parent / "beats.txt"
for _ in range(600):
    with open(beats, "a") as handle:
        handle.write("x")
    time.sleep(0.1)
"""

PARENT = """\
import subprocess, sys, time
from pathlib import Path

subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "grandchild.py")])
time.sleep(60)
"""

# Passes only when the other file is running at the same time.
MEET = """\
import time, unittest
from pathlib import Path

folder = Path(__file__).resolve().parent

class Meet(unittest.TestCase):
    def test_other_file_runs_too(self):
        (folder / "%s.here").write_text("x")
        deadline = time.monotonic() + 20
        while not (folder / "%s.here").exists():
            self.assertLess(time.monotonic(), deadline, "the other file never started")
            time.sleep(0.05)

unittest.main()
"""

LOG_SPAN = """\
import time, unittest
from pathlib import Path

spans = Path(__file__).resolve().parent.parent / "spans.txt"

class Span(unittest.TestCase):
    def test_span(self):
        with open(spans, "a") as handle:
            handle.write("start-%(name)s\\n")
        time.sleep(0.5)
        with open(spans, "a") as handle:
            handle.write("end-%(name)s\\n")

unittest.main()
"""


NOISY_PASS = """\
import unittest

class Noisy(unittest.TestCase):
    def test_ok(self):
        print("printed by the passing file")

unittest.main(verbosity=2)
"""

PLANTED_FAILURE = """\
import unittest

class Planted(unittest.TestCase):
    def test_planted(self):
        self.fail("planted failure text")

unittest.main(verbosity=2)
"""


def mask_seconds(output):
    return re.sub(r"\d+\.\d s", "S", output)


class RunTestsPowerShellTests(unittest.TestCase):
    def test_summary_rules(self):
        cases = [
            (0, "x\r\nsafe-edit tests: 3 passed, 0 failed.\r\n", True, 3, 0, ""),
            (1, "safe-edit tests: 2 passed, 1 failed.\n", False, 2, 1, "exit 1"),
            (1, "safe-edit tests: 3 passed, 0 failed.\n", False, 3, 1, "exit 1"),
            (0, "safe-edit tests: 2 passed, 1 failed.\n", False, 2, 1, ""),
            (0, "safe-edit tests: 0 passed, 0 failed.\n", False, 0, 1, "ran 0 tests"),
            (0, "hello\n", False, 0, 1, "no test summary in output"),
            (1, "", False, 0, 1, "no test summary in output"),
        ]
        for code, output, ok, tests, failed, note in cases:
            with self.subTest(output=output, code=code):
                result = run_tests.evaluate_ps1(code, output)
                self.assertEqual(
                    (result["ok"], result["tests"], result["failed"], result["note"]), (ok, tests, failed, note)
                )

    def test_absent_pwsh_prints_an_explicit_skip(self):
        root = Path(tempfile.mkdtemp(prefix="run-tests-"))
        self.addCleanup(shutil.rmtree, root, True)
        write_text(root / "plugins" / "p" / "tools" / "t" / "tests" / "a.tests.ps1", "exit 1\n")
        write_text(root / "tests" / "test_pass.py", PASSING_CONTROL)
        original = run_tests.find_pwsh
        run_tests.find_pwsh = lambda: None
        self.addCleanup(setattr, run_tests, "find_pwsh", original)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run_tests.main(["--min-files", "1"], root=root)
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertIn("SKIP plugins/p/tools/t/tests/a.tests.ps1: # skipped 1 PowerShell test file: pwsh not found", output)
        self.assertIn("# files 2, tests 1, failed 0, skipped 1", output)

    def test_ps1_runs_and_a_failure_fails_the_run(self):
        if shutil.which("pwsh") is None:
            self.skipTest("pwsh not on PATH: the real PowerShell run is not exercised here")
        root = Path(tempfile.mkdtemp(prefix="run-tests-"))
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "tests" / "a.tests.ps1"
        for body, expected_code, expected in (
            ("Write-Host 'a tests: 4 passed, 0 failed.'\nexit 0\n", 0, "PASS tests/a.tests.ps1: a tests: 4 passed, 0 failed."),
            ("Write-Host 'a tests: 3 passed, 1 failed.'\nexit 1\n", 1, "FAIL tests/a.tests.ps1: a tests: 3 passed, 1 failed."),
        ):
            with self.subTest(body=body):
                write_text(path, body)
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = run_tests.main(["--min-files", "1"], root=root)
                self.assertEqual(code, expected_code, buffer.getvalue())
                self.assertIn(expected, buffer.getvalue())

    def test_real_ps1_file_is_discovered(self):
        found = [path.relative_to(ROOT).as_posix() for path in run_tests.discover(ROOT)]
        self.assertIn("plugins/rigger-windows/tools/safe-edit/tests/safe-edit.tests.ps1", found)
        self.assertIn("plugins/rigger-windows/tools/mem-check/mem-check.tests.ps1", found)


class RunTestsMainTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="run-tests-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def set_env(self, key, value):
        old = os.environ.get(key)
        os.environ[key] = value

        def restore():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)

    def main(self, *argv, root=None):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run_tests.main(list(argv), root=root or self.root)
        return code, buffer.getvalue()

    def test_discovery_patterns(self):
        for rel in (
            "tests/test_a.py",
            "plugins/p/tests/test_b.py",
            "plugins/p/tools/t/tests/test_c.py",
            "tests/a.tests.ps1",
            "plugins/p/tests/b.tests.ps1",
            "plugins/p/tools/t/tests/c.tests.ps1",
            "plugins/p/tools/t/d.tests.ps1",
            "tests/helper.py",
            "tests/control/test_not_collected.py",
            "tests/control/e.tests.ps1",
            "plugins/p/e.tests.ps1",
            "plugins/p/tools/t/test_d.py",
        ):
            write_text(self.root / rel, "")
        found = [path.relative_to(self.root).as_posix() for path in run_tests.discover(self.root)]
        self.assertEqual(
            found,
            [
                "tests/test_a.py",
                "plugins/p/tests/test_b.py",
                "plugins/p/tools/t/tests/test_c.py",
                "tests/a.tests.ps1",
                "plugins/p/tests/b.tests.ps1",
                "plugins/p/tools/t/tests/c.tests.ps1",
                "plugins/p/tools/t/d.tests.ps1",
            ],
        )

    def test_child_env_drops_claude_and_harness_vars(self):
        self.set_env("CLAUDE_PROBE_VAR", "1")
        self.set_env("HARNESS_PROBE_VAR", "1")
        write_text(self.root / "tests" / "test_env.py", ENV_PROBE)
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 0, output)
        seen = json.loads((self.root / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(seen["keys"], [])
        self.assertEqual(seen["isolated"], 1)
        self.assertEqual(Path(seen["cwd"]).resolve(), self.root.resolve())
        self.assertIn("# files 1, tests 1, failed 0, skipped 0", output)

    def test_no_claude_passes_only_its_flag(self):
        self.set_env("HARNESS_PROBE_VAR", "1")
        write_text(self.root / "tests" / "test_env.py", ENV_PROBE)
        code, output = self.main("--min-files", "1", "--no-claude")
        self.assertEqual(code, 0, output)
        seen = json.loads((self.root / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(seen["keys"], ["HARNESS_NO_CLAUDE"])

    def test_timeout_is_failure(self):
        write_text(self.root / "tests" / "test_sleep.py", SLEEPER)
        result = run_tests.run_file(self.root / "tests" / "test_sleep.py", dict(os.environ), root=self.root, timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["note"], "timeout after 1 s")
        self.assertEqual(result["failed"], 1)

    def beat_count(self):
        path = self.root / "tests" / "beats.txt"
        return path.stat().st_size if path.is_file() else 0

    def test_timeout_bounds_a_grandchild_and_kills_it(self):
        write_text(self.root / "tests" / "grandchild.py", GRANDCHILD)
        write_text(self.root / "tests" / "test_parent.py", PARENT)
        started = time.monotonic()
        result = run_tests.run_file(self.root / "tests" / "test_parent.py", dict(os.environ), root=self.root, timeout=8)
        elapsed = time.monotonic() - started
        self.assertEqual(result["note"], "timeout after 8 s")
        self.assertLess(elapsed, 20, "the timeout waited for the grandchild")
        before = self.beat_count()
        self.assertGreater(before, 0, "the grandchild never ran, so this test proved nothing")
        time.sleep(1)
        self.assertEqual(self.beat_count(), before, "the grandchild outlived the timeout")

    def test_kill_tree_still_stops_the_process_when_the_tree_kill_fails(self):
        proc = subprocess.Popen([sys.executable, "-c", SLEEPER])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        with mock.patch.object(run_tests.subprocess, "run", side_effect=OSError("no taskkill")), mock.patch.object(
            run_tests.os, "killpg", side_effect=OSError("no group"), create=True
        ):
            try:
                run_tests.kill_tree(proc)
            except subprocess.TimeoutExpired:
                self.fail("the process outlived kill_tree")
        self.assertIsNotNone(proc.poll())

    def test_two_skip_files_fail_the_run(self):
        for name in ("a", "b"):
            write_text(self.root / "tests" / ("test_%s.py" % name), "print('# tool: skipped here: reason')\n")
        code, output = self.main("--min-files", "2")
        self.assertEqual(code, 1, output)
        self.assertIn("# files 2, tests 0, failed 0, skipped 2", output)
        self.assertIn("# no test was executed", output)

    def test_file_with_every_test_skipped_fails_the_run_alone(self):
        write_text(self.root / "tests" / "test_skipped.py", ALL_SKIPPED)
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 1, output)
        self.assertIn("# files 1, tests 0, failed 0, skipped 2", output)
        self.assertIn("# no test was executed", output)

    def test_skipped_tests_are_left_out_of_the_total(self):
        write_text(self.root / "tests" / "test_skipped.py", ALL_SKIPPED)
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("# files 2, tests 1, failed 0, skipped 2", output)

    def test_min_files_below_one_is_refused(self):
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        for value in ("0", "-5"):
            with self.subTest(value=value):
                code, output = self.main("--min-files=" + value)
                self.assertEqual(code, 1, output)
                self.assertEqual(output, "# --min-files must be at least 1\n")

    def test_min_file_below_one_is_refused(self):
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        for value in ("0\n", "-1\n", "abc\n", ""):
            with self.subTest(value=value):
                write_text(self.root / "scripts" / "run_tests.min", value)
                code, output = self.main()
                self.assertEqual(code, 1, output)
                self.assertIn("# cannot read a positive integer", output)

    def test_custom_runner_reporting_failures_with_exit_0_fails_the_run(self):
        write_text(self.root / "tests" / "test_a.py", "print('# a: 5 passed, 3 failed')\n")
        write_text(self.root / "tests" / "test_b.py", PASSING_CONTROL)
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL tests/test_a.py: # a: 5 passed, 3 failed", output)
        self.assertIn("# files 2, tests 6, failed 3, skipped 0", output)

    def test_whole_file_skip_keeps_its_reason_after_other_hash_lines(self):
        write_text(self.root / "tests" / "test_skip.py", "print('# tool: skipped here: the reason')\nprint('# done')\n")
        write_text(self.root / "tests" / "test_two.py", "print('# skipped 1 check: one')\nprint('# skipped 2 checks: two')\n")
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("SKIP tests/test_skip.py: # tool: skipped here: the reason, 1 skipped", output)
        self.assertIn("SKIP tests/test_two.py: # skipped 1 check: one; # skipped 2 checks: two, 3 skipped", output)

    def test_fewer_files_than_minimum_fails(self):
        write_text(self.root / "tests" / "test_env.py", ENV_PROBE)
        write_text(self.root / "scripts" / "run_tests.min", "2\n")
        code, output = self.main()
        self.assertEqual(code, 1, output)
        self.assertIn("fewer than the minimum 2", output)
        write_text(self.root / "scripts" / "run_tests.min", "1\n")
        code, output = self.main()
        self.assertEqual(code, 0, output)

    def test_missing_minimum_file_fails(self):
        write_text(self.root / "tests" / "test_env.py", ENV_PROBE)
        code, output = self.main()
        self.assertEqual(code, 1, output)
        self.assertIn("cannot read a positive integer", output)

    def test_failing_file_fails_the_run(self):
        write_text(self.root / "tests" / "test_fail.py", (ROOT / run_tests.CONTROL).read_text(encoding="utf-8"))
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL tests/test_fail.py", output)
        self.assertIn("planted failure", output)
        self.assertIn("# files 1, tests 1, failed 1, skipped 0", output)

    def test_custom_runner_printing_only_a_hash_line_fails_the_run(self):
        write_text(self.root / "tests" / "test_hash.py", "print('# section done')\n")
        code, output = self.main("--min-files", "1")
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL tests/test_hash.py: # section done, ran 0 checks and printed no skip reason", output)
        self.assertIn("# files 1, tests 0, failed 1, skipped 0", output)

    def test_whole_file_skip_shows_as_skip(self):
        write_text(self.root / "tests" / "test_skip.py", "print('# tool: skipped on this os: needs another os')\n")
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        code, output = self.main("--min-files", "1", "--jobs", "1")
        self.assertEqual(code, 0, output)
        lines = mask_seconds(output).splitlines()
        self.assertEqual(
            lines[:2],
            [
                "PASS tests/test_pass.py: 1 tests [S]",
                "SKIP tests/test_skip.py: # tool: skipped on this os: needs another os, 1 skipped [S]",
            ],
        )
        # Slowest first, and two files this short can end in either order.
        self.assertCountEqual(lines[2:4], ["# slow S tests/test_pass.py", "# slow S tests/test_skip.py"])
        self.assertEqual(lines[4:], ["# time S, S in files, jobs 1", "# files 2, tests 1, failed 0, skipped 1"])

    def test_unittest_skips_without_names_are_counted_in_the_output(self):
        write_text(self.root / "tests" / "test_quiet.py", "print('Ran 3 tests in 0.1s')\nprint()\nprint('OK (skipped=2)')\n")
        code, output = self.main("--min-files", "1", "--jobs", "1")
        self.assertEqual(code, 0, output)
        self.assertEqual(
            mask_seconds(output).splitlines(),
            [
                "PASS tests/test_quiet.py: 3 tests, 2 skipped [S]",
                "  skipped 2 more (names not found in the output)",
                "# slow S tests/test_quiet.py",
                "# time S, S in files, jobs 1",
                "# files 1, tests 1, failed 0, skipped 2",
            ],
        )

    def test_files_run_at_once(self):
        for name, other in (("a", "b"), ("b", "a")):
            write_text(self.root / "tests" / ("test_%s.py" % name), MEET % (name, other))
        code, output = self.main("--min-files", "2", "--jobs", "2")
        self.assertEqual(code, 0, output)
        self.assertIn("# time ", output)
        self.assertIn(", jobs 2", output)

    def test_serial_files_run_alone_after_the_others(self):
        for name in ("shared", "first", "second"):
            write_text(self.root / "tests" / ("test_%s.py" % name), LOG_SPAN % {"name": name})
        with mock.patch.object(run_tests, "SERIAL", ("tests/test_first.py", "tests/test_second.py")):
            code, output = self.main("--min-files", "3", "--jobs", "3")
        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.root / "spans.txt").read_text(encoding="utf-8").split(),
            ["start-shared", "end-shared", "start-first", "end-first", "start-second", "end-second"],
        )

    def test_slowest_files_are_listed_longest_first_and_cut_to_the_limit(self):
        for index in range(run_tests.SLOWEST + 2):
            write_text(self.root / "tests" / ("test_%02d.py" % index), "")

        def fake_run_file(path, env, root):
            result = run_tests.evaluate(0, "Ran 1 test in 0.0s\n\nOK\n")
            result.update(output="", seconds=float(path.stem[-2:]))
            return result

        with mock.patch.object(run_tests, "run_file", fake_run_file):
            code, output = self.main("--min-files", "1")
        self.assertEqual(code, 0, output)
        slow = [line for line in output.splitlines() if line.startswith("# slow ")]
        self.assertEqual(slow[0], "# slow 11.0 s tests/test_11.py")
        self.assertEqual(slow[-1], "# slow 2.0 s tests/test_02.py")
        self.assertEqual(len(slow), run_tests.SLOWEST)

    def test_jobs_below_one_is_refused(self):
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        code, output = self.main("--min-files", "1", "--jobs", "0")
        self.assertEqual(code, 1, output)
        self.assertEqual(output, "# --jobs must be at least 1\n")

    def agent_log(self):
        path = run_tests.log_path(self.root)
        self.addCleanup(shutil.rmtree, path.parent, True)
        return path

    def test_agent_run_prints_failures_only_and_logs_every_output(self):
        log = self.agent_log()
        write_text(self.root / "tests" / "test_pass.py", NOISY_PASS)
        write_text(self.root / "tests" / "test_fail.py", PLANTED_FAILURE)
        code, output = self.main("--min-files", "1", "--jobs", "1", "--agent")
        self.assertEqual(code, 1, output)
        lines = mask_seconds(output).splitlines()
        self.assertEqual(lines[0], "# log %s" % log)
        self.assertRegex(lines[1], r"^FAIL tests/test_fail\.py: 1 tests, exit 1 \[S\]$")
        self.assertRegex(lines[2], r"^  FAIL: test_planted \(")
        # The failed file is rerun alone, and that failure is reported again.
        self.assertRegex(lines[3], r"^FAIL tests/test_fail\.py \(alone\): 1 tests, exit 1 \[S\]$")
        self.assertRegex(lines[4], r"^  FAIL: test_planted \(")
        self.assertEqual(lines[5:], ["# time S, S in files, jobs 1", "# files 2, tests 2, failed 1, skipped 0"])
        self.assertNotIn("\r", output)
        text = log.read_text(encoding="utf-8")
        self.assertIn("PASS tests/test_pass.py", text)
        self.assertIn("printed by the passing file", text)
        self.assertIn("planted failure text", text)
        self.assertIn("# slow ", text)
        self.assertIn("# files 2, tests 2, failed 1, skipped 0", text)

    def test_agent_run_shows_a_limited_number_of_failed_tests(self):
        self.agent_log()
        extra = "".join(
            "    def test_%d(self):\n        self.fail()\n\n" % index for index in range(run_tests.FAILED_TESTS_SHOWN)
        )
        write_text(
            self.root / "tests" / "test_fail.py",
            PLANTED_FAILURE.replace("unittest.main", extra + "unittest.main"),
        )
        code, output = self.main("--min-files", "1", "--agent")
        self.assertEqual(code, 1, output)
        # Shown once for the shared run and once for the rerun alone, each cut to the limit.
        self.assertEqual(output.count("  FAIL: "), 2 * run_tests.FAILED_TESTS_SHOWN)

    def test_agent_run_that_passes_prints_only_the_summary(self):
        log = self.agent_log()
        write_text(self.root / "tests" / "test_pass.py", NOISY_PASS)
        code, output = self.main("--min-files", "1", "--jobs", "1", "--agent")
        self.assertEqual(code, 0, output)
        self.assertEqual(
            mask_seconds(output).splitlines(),
            ["# log %s" % log, "# time S, S in files, jobs 1", "# files 1, tests 1, failed 0, skipped 0"],
        )

    def test_agent_log_holds_only_the_last_run(self):
        log = self.agent_log()
        write_text(self.root / "tests" / "test_fail.py", PLANTED_FAILURE)
        self.assertEqual(self.main("--min-files", "1", "--agent")[0], 1)
        (self.root / "tests" / "test_fail.py").unlink()
        write_text(self.root / "tests" / "test_pass.py", NOISY_PASS)
        self.assertEqual(self.main("--min-files", "1", "--agent")[0], 0)
        self.assertNotIn("planted failure text", log.read_text(encoding="utf-8"))

    def test_agent_log_grows_while_the_run_lasts(self):
        log = self.agent_log()
        write_text(self.root / "tests" / "test_a.py", "")
        write_text(self.root / "tests" / "test_b.py", "")
        seen = []

        def fake_run_file(path, env, root):
            seen.append(log.read_text(encoding="utf-8"))
            result = run_tests.evaluate(0, "Ran 1 test in 0.0s\n\nOK\n")
            result.update(output="", seconds=0.0)
            return result

        with mock.patch.object(run_tests, "SERIAL", ("tests/test_a.py", "tests/test_b.py")):
            with mock.patch.object(run_tests, "run_file", fake_run_file):
                self.main("--min-files", "1", "--agent")
        self.assertNotIn("PASS tests/test_a.py", seen[0])
        self.assertIn("PASS tests/test_a.py", seen[1])

    def test_agent_log_names_the_tree_it_ran(self):
        log = self.agent_log()
        write_text(self.root / "tests" / "test_pass.py", NOISY_PASS)
        with mock.patch.object(run_tests, "revision", return_value="abc1234-dirty"):
            self.main("--min-files", "1", "--agent")
        self.assertEqual(
            log.read_text(encoding="utf-8").splitlines()[:2],
            ["# root %s" % self.root, "# revision abc1234-dirty"],
        )

    def test_revision_reads_the_checkout(self):
        self.fix_round_tree()
        self.assertRegex(run_tests.revision(self.root), r"^[0-9a-f]{7,}-dirty$")

    def test_revision_of_a_folder_without_git_is_unknown(self):
        self.assertEqual(run_tests.revision(self.root), "unknown")

    def test_agent_run_is_refused_when_the_log_cannot_be_written(self):
        write_text(self.root / "tests" / "test_pass.py", NOISY_PASS)
        blocker = self.root / "file"
        write_text(blocker, "")
        ran = []

        def fake_run_file(path, env, root):
            ran.append(path)
            result = run_tests.evaluate(0, "Ran 1 test in 0.0s\n\nOK\n")
            result.update(output="", seconds=0.0)
            return result

        with mock.patch.object(run_tests, "log_path", return_value=blocker / "last.log"):
            with mock.patch.object(run_tests, "run_file", fake_run_file):
                code, output = self.main("--min-files", "1", "--agent")
        self.assertEqual(ran, [])
        self.assertEqual(code, 1, output)
        self.assertIn("# cannot write", output)

    def test_two_roots_get_two_logs(self):
        self.assertNotEqual(run_tests.log_path(self.root), run_tests.log_path(self.root / "other"))

    def fix_round_tree(self):
        """A git tree on a work branch: test_a changed, test_b reached by nothing changed."""
        self.agent_log()
        self.addCleanup(lambda: run_tests.failed_path(self.root).unlink(missing_ok=True))
        write_text(self.root / "tests" / "test_a.py", PASSING_CONTROL)
        write_text(self.root / "tests" / "test_b.py", PASSING_CONTROL)
        write_text(self.root / "tests" / "test_repo.py", PASSING_CONTROL)
        for command in (
            ("init", "-q", "-b", "main"),
            ("config", "user.email", "test" + "@example.invalid"),
            ("config", "user.name", "test"),
            ("add", "."),
            ("commit", "-q", "-m", "first"),
            ("switch", "-q", "-c", "work"),
        ):
            subprocess.run(["git", "-C", str(self.root), *command], check=True, capture_output=True)
        write_text(self.root / "tests" / "test_a.py", PASSING_CONTROL + "# changed\n")

    def ran(self, log):
        return [line.split()[1].rstrip(":") for line in log.read_text(encoding="utf-8").splitlines() if line[:5] in ("PASS ", "FAIL ")]

    def test_fix_round_runs_the_changed_test_and_the_repo_test(self):
        self.fix_round_tree()
        log = run_tests.log_path(self.root)
        code, output = self.main("--fix-round", "--jobs", "1")
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(self.ran(log)), ["tests/test_a.py", "tests/test_repo.py"])
        self.assertIn("# fix round: 1 changed, 2 of 3 test files", output)
        self.assertEqual(output.splitlines()[-1], "# fix round only: the full suite stays mandatory before merge")

    def test_fix_round_ignores_the_minimum_file_count(self):
        self.fix_round_tree()
        write_text(self.root / run_tests.MIN_FILE, "99\n")
        code, output = self.main("--fix-round")
        self.assertEqual(code, 0, output)

    def test_full_run_still_needs_the_minimum_file_count(self):
        self.fix_round_tree()
        write_text(self.root / run_tests.MIN_FILE, "99\n")
        code, output = self.main("--agent")
        self.assertEqual(code, 1, output)
        self.assertIn("fewer than the minimum 99", output)

    def test_fix_round_runs_the_last_failed_file_first(self):
        self.fix_round_tree()
        log = run_tests.log_path(self.root)
        write_text(self.root / "tests" / "test_b.py", PLANTED_FAILURE)
        code, output = self.main("--agent", "--min-files", "1", "--jobs", "1")
        self.assertEqual(code, 1, output)
        self.assertEqual(run_tests.read_failed(self.root), ["tests/test_b.py"])
        write_text(self.root / "tests" / "test_b.py", PASSING_CONTROL)
        code, output = self.main("--fix-round", "--jobs", "1")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.ran(log)[0], "tests/test_b.py")
        self.assertEqual(sorted(self.ran(log)[1:]), ["tests/test_a.py", "tests/test_repo.py"])
        self.assertEqual(run_tests.read_failed(self.root), [""])

    def test_fix_round_stops_at_the_first_failure(self):
        self.fix_round_tree()
        log = run_tests.log_path(self.root)
        write_text(self.root / "tests" / "test_b.py", PLANTED_FAILURE)
        self.assertEqual(self.main("--agent", "--min-files", "1", "--jobs", "1")[0], 1)
        code, output = self.main("--fix-round", "--jobs", "1")
        self.assertEqual(code, 1, output)
        self.assertEqual(self.ran(log), ["tests/test_b.py"])
        self.assertIn("# stopped at the first failure", output)
        self.assertEqual(output.splitlines()[-1], "# fix round only: the full suite stays mandatory before merge")

    def test_stopped_fix_round_stops_the_files_in_the_workers(self):
        self.fix_round_tree()
        write_text(self.root / "tests" / "test_a.py", SLEEPER)
        write_text(self.root / "tests" / "test_repo.py", PLANTED_FAILURE)
        started = time.monotonic()
        # The real SERIAL names tests/test_repo.py: the planted failure would run alone after the sleeper.
        with mock.patch.object(run_tests, "SERIAL", ()):
            code, output = self.main("--fix-round", "--jobs", "2")
        self.assertEqual(code, 1, output)
        self.assertLess(time.monotonic() - started, 25, "the fix round waited for the sleeping file")

    def test_stopped_fix_round_keeps_the_failed_file_it_did_not_run(self):
        self.fix_round_tree()
        write_text(run_tests.failed_path(self.root), "tests/test_b.py\ntests/test_a.py")
        write_text(self.root / "tests" / "test_a.py", PLANTED_FAILURE)
        self.assertEqual(self.main("--fix-round", "--jobs", "1")[0], 1)
        self.assertEqual(run_tests.read_failed(self.root), ["tests/test_a.py", "tests/test_b.py"])

    def test_fix_round_drops_a_failed_file_that_is_gone(self):
        self.fix_round_tree()
        write_text(run_tests.failed_path(self.root), "tests/test_gone.py")
        self.assertEqual(self.main("--fix-round", "--jobs", "1")[0], 0)
        self.assertEqual(run_tests.read_failed(self.root), [""])

    def test_fix_round_without_git_runs_everything(self):
        self.agent_log()
        self.addCleanup(lambda: run_tests.failed_path(self.root).unlink(missing_ok=True))
        write_text(self.root / "tests" / "test_a.py", PASSING_CONTROL)
        write_text(self.root / "tests" / "test_b.py", PASSING_CONTROL)
        code, output = self.main("--fix-round", "--jobs", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("# fix round: full suite: git failed", output)
        self.assertEqual(sorted(self.ran(run_tests.log_path(self.root))), ["tests/test_a.py", "tests/test_b.py"])

    def test_fix_round_with_an_unmapped_change_runs_everything(self):
        self.fix_round_tree()
        write_text(self.root / "lonely.py", "x = 1\n")
        code, output = self.main("--fix-round", "--jobs", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("# fix round: full suite: no test reaches lonely.py", output)
        self.assertEqual(len(self.ran(run_tests.log_path(self.root))), 3)

    def test_default_jobs_never_exceed_the_core_count(self):
        write_text(self.root / "tests" / "test_pass.py", PASSING_CONTROL)
        with mock.patch.object(run_tests.os, "cpu_count", return_value=2):
            code, output = self.main("--min-files", "1")
        self.assertEqual(code, 0, output)
        self.assertIn(", jobs 2", output)

    def test_interrupted_run_stops_the_files_in_the_workers(self):
        write_text(self.root / "tests" / "grandchild.py", GRANDCHILD)
        write_text(self.root / "tests" / "test_parent.py", PARENT)

        def interrupted(futures):
            deadline = time.monotonic() + 30
            while not self.beat_count() and time.monotonic() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt

        with mock.patch.object(run_tests, "as_completed", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.main("--min-files", "1")
        self.assertEqual(run_tests.RUNNING, set())
        before = self.beat_count()
        self.assertGreater(before, 0, "the grandchild never ran, so this test proved nothing")
        time.sleep(1)
        self.assertEqual(self.beat_count(), before, "the grandchild outlived the interrupt")

    def test_control_caught_on_real_repo(self):
        code, output = self.main("--control", root=ROOT)
        self.assertEqual(code, 0, output)
        self.assertIn("# control: caught", output)

    def test_control_missed_when_planted_file_passes(self):
        write_text(self.root / run_tests.CONTROL, PASSING_CONTROL)
        code, output = self.main("--control")
        self.assertEqual(code, 1, output)
        self.assertIn("# control: missed", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
