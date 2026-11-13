"""python -I test_archive.py - the lessons archive: the rigger-lessons lint with the rigger-windows
shell-guard rule checker, `token_audit.py archive` and the history files, on transcripts and a
lessons repo built in temp folders.
"""
import calendar
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
LINT = os.path.join(PLUGINS, "rigger-lessons", "tools", "lessons_lint.py")
GUARD = os.path.join(PLUGINS, "rigger-windows", "tools", "shell-guard", "lessons_guard.py")
SCRIPT = os.path.join(os.path.dirname(HERE), "token_audit.py")
sys.path.insert(0, os.path.dirname(HERE))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ll = load("lessons_lint_under_test", LINT)
import token_audit as ta  # noqa: E402

FAILURES = []
RAN = []


def check(name, actual, expected):
    RAN.append(name)
    if actual != expected:
        FAILURES.append(f"{name}: expected {expected!r}, got {actual!r}")


def config_env(config):
    """The environment of a CLI run: CLAUDE_CONFIG_DIR is `config` (a fake), never the real config dir."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE_")}
    env["CLAUDE_CONFIG_DIR"] = config
    return env


def write_jsonl(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(e if isinstance(e, str) else json.dumps(e, separators=(",", ":")))
            fh.write("\n")


# ---- lessons archive: lint and report -------------------------------------------------------

S1, S2, S3, S4 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                  "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444")
NOW = calendar.timegm(time.strptime("2026-11-02T12:00:00", "%Y-%m-%dT%H:%M:%S"))
SQL_MODES = ("Error: Failed to get current SQL modes. Reason: ERROR 1156 (08S01): Plugin caching_sha2_password "
             "could not be loaded: Le module spécifié est introuvable. Library path is 'caching_sha2_password.dll'")
HOOK_STDERR = ("Failed with non-blocking status code: node:internal/modules/esm/resolve:275\r\n    throw new "
               "ERR_MODULE_NOT_FOUND(\r\n          ^\r\n\r\nError [ERR_MODULE_NOT_FOUND]: Cannot find module "
               "'C:\\Users\\someone\\.claude\\plugins\\cache\\superpowers-marketplace\\episodic-memory\\1.6.0\\"
               "node_modules\\@anthropic-ai\\claude-agent-sdk\\sdk.mjs'\r\n\r\nNode.js v22.22.3\r\n"
               "Error: Command failed with exit code 1")
EPISODIC = 'node "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/cli/episodic-memory.js" sync --background'
MAKE_ERROR = "Exit code 2\nmake: *** No rule to make target 'once'."


def no_session(entry):
    return {k: v for k, v in entry.items() if k != "sessionId"}


def a_tool_use(session, mid, tid, name, args, at):
    """An assistant line as Claude Code streams it: one content block, message id and usage."""
    return {"parentUuid": "p", "isSidechain": False, "type": "assistant", "timestamp": at, "sessionId": session,
            "message": {"model": "claude-opus-5-5", "id": mid, "type": "message", "role": "assistant",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": args,
                                     "caller": {"type": "direct"}}],
                        "usage": {"input_tokens": 2, "output_tokens": 7}}}


def a_tool_result(session, tid, text, at, error=True, uuid="u"):
    """A user line carrying a tool result: content is a plain string, as for shell tools."""
    return {"parentUuid": "p", "isSidechain": False, "type": "user", "timestamp": at, "sessionId": session,
            "uuid": uuid, "message": {"role": "user", "content": [
                {"type": "tool_result", "content": text, "is_error": error, "tool_use_id": tid}]},
            "toolUseResult": "Error: " + text[:20], "userType": "external", "cwd": "/work/x"}


def a_hook_error(session, uuid, at, hook="SessionStart:startup", command=EPISODIC, stderr=HOOK_STDERR):
    return {"parentUuid": "p", "isSidechain": False, "type": "attachment", "uuid": uuid, "timestamp": at,
            "sessionId": session, "userType": "external", "entrypoint": "claude-desktop",
            "attachment": {"type": "hook_non_blocking_error", "hookName": hook, "toolUseID": "x",
                           "hookEvent": hook.split(":")[0], "stderr": stderr, "stdout": "", "exitCode": 1,
                           "command": command, "durationMs": 300}}


def build_transcripts(root):
    sql_error = "Exit code 1\n" + SQL_MODES + "\nExit code 1"
    lint_error = "Exit code 1\nnpm error Missing script: \"lint\"\nExit code 1"
    s1 = [
        a_hook_error(S1, "h1", "2026-10-20T08:00:00.000Z"),
        a_tool_use(S1, "m1", "t1", "PowerShell", {"command": "wp db export x.sql"}, "2026-10-20T08:01:00.000Z"),
        # The same tool_use streamed again on a later line counts once.
        a_tool_use(S1, "m1", "t1", "PowerShell", {"command": "wp db export x.sql"}, "2026-10-20T08:01:00.100Z"),
        a_tool_result(S1, "t1", sql_error, "2026-10-20T08:01:05.000Z"),
        a_tool_use(S1, "m2", "t2", "Skill", {"skill": "superpowers:brainstorming"}, "2026-10-20T08:02:00.000Z"),
        a_tool_use(S1, "m2", "t2", "Skill", {"skill": "superpowers:brainstorming"}, "2026-10-20T08:02:00.100Z"),
        a_tool_use(S1, "m3", "t3", "Read", {"file_path": "~/.claude/skills/windows-dev-env/SKILL.md"},
                   "2026-10-20T08:03:00.000Z"),
        a_tool_use(S1, "m4", "t4", "Read", {"file_path": "~/.claude/skills/windows-dev-env/README.md"},
                   "2026-10-20T08:04:00.000Z"),
        a_tool_use(S1, "m5", "t5", "PowerShell", {"command": "git restore a.txt # shell-guard: allow"},
                   "2026-10-20T08:05:00.000Z"),
        # Mentioning the marker is not carrying it.
        a_tool_use(S1, "m6", "t6", "PowerShell", {"command": "rg -n 'shell-guard: allow' docs"},
                   "2026-10-20T08:06:00.000Z"),
        a_tool_use(S1, "m7", "t7", "PowerShell", {"command": "wp db export y.sql"}, "2026-10-20T08:07:00.000Z"),
        a_tool_result(S1, "t7", "The user doesn't want to proceed with this tool use.", "2026-10-20T08:07:01.000Z"),
        a_tool_use(S1, "m8", "t8", "Bash", {"command": "make once"}, "2026-10-20T08:08:00.000Z"),
        a_tool_result(S1, "t8", MAKE_ERROR, "2026-10-20T08:08:01.000Z"),
        # A line without a sessionId belongs to the session its file is named after.
        a_tool_use(S1, "m9", "t9", "PowerShell", {"command": "wp db size"}, "2026-10-20T08:09:00.000Z"),
        no_session(a_tool_result(S1, "t9", sql_error, "2026-10-20T08:09:01.000Z")),
        # A result that is not an error, recurring in two sessions, is not a failure.
        a_tool_use(S1, "m10", "t10", "PowerShell", {"command": "npm test"}, "2026-10-20T08:10:00.000Z"),
        a_tool_result(S1, "t10", "Exit code 1\nok", "2026-10-20T08:10:01.000Z", error=False),
        # An undated failure is reported as skipped, not counted.
        a_tool_use(S1, "m11", "t11", "PowerShell", {"command": "npm run build"}, "2026-10-20T08:11:00.000Z"),
        {k: v for k, v in a_tool_result(S1, "t11", "Exit code 1\nbuild failed", None).items() if k != "timestamp"},
    ]
    s2 = [
        a_hook_error(S2, "h2", "2026-10-25T08:00:00.000Z"),
        a_tool_use(S2, "n1", "u1", "Bash", {"command": "wp option get siteurl"}, "2026-10-25T08:01:00.000Z"),
        a_tool_result(S2, "u1", sql_error, "2026-10-25T08:01:05.000Z"),
        a_tool_use(S2, "n2", "u2", "PowerShell", {"command": "npm run lint"}, "2026-10-25T08:02:00.000Z"),
        a_tool_result(S2, "u2", lint_error, "2026-10-25T08:02:05.000Z"),
        a_tool_use(S2, "n3", "u3", "PowerShell", {"command": "npm test"}, "2026-10-25T08:03:00.000Z"),
        a_tool_result(S2, "u3", "Exit code 1\nok", "2026-10-25T08:03:01.000Z", error=False),
        # A failure seen in one session only is not a repeat.
        a_tool_use(S2, "n4", "u4", "PowerShell", {"command": "npm run e2e"}, "2026-10-25T08:04:00.000Z"),
        a_tool_result(S2, "u4", "Exit code 1\nonly once here", "2026-10-25T08:04:01.000Z"),
    ]
    # A subagent's lines carry its parent's session id (or, without one, its folder's): more results,
    # no new session.
    sub = [a_tool_use(S2, "k1", "v1", "PowerShell", {"command": "wp post list"}, "2026-10-25T09:00:00.000Z"),
           a_tool_result(S2, "v1", sql_error, "2026-10-25T09:00:05.000Z"),
           a_tool_use(S2, "k2", "v2", "PowerShell", {"command": "wp user list"}, "2026-10-25T09:01:00.000Z"),
           no_session(a_tool_result(S2, "v2", sql_error, "2026-10-25T09:01:05.000Z"))]
    # A resumed session repeats earlier lines (same tool_use and attachment ids) under its own id.
    s3 = [dict(e, sessionId=S3) for e in s2[:3]] + [
        a_tool_use(S3, "q1", "w1", "PowerShell", {"command": "npm run lint -- --fix"}, "2026-10-28T08:00:00.000Z"),
        a_tool_result(S3, "w1", lint_error, "2026-10-28T08:00:05.000Z"),
        a_tool_use(S3, "q2", "w2", "Bash", {"command": "make once"}, "2026-10-28T08:01:00.000Z"),
        a_tool_result(S3, "w2", MAKE_ERROR, "2026-10-28T08:01:05.000Z"),
        a_hook_error(S3, "h3", "2026-10-28T08:10:00.000Z", hook="PreToolUse:Read",
                     command='node "${CLAUDE_PLUGIN_ROOT}/hooks/read-guard.js"', stderr="No stderr output"),
    ]
    # Outside the 30-day window.
    s4 = [a_tool_use(S4, "o1", "x1", "PowerShell", {"command": "wp db check"}, "2026-09-01T08:00:00.000Z"),
          a_tool_result(S4, "x1", sql_error, "2026-09-01T08:00:05.000Z"),
          a_hook_error(S4, "h4", "2026-09-01T08:10:00.000Z")]
    write_jsonl(os.path.join(root, "proj", f"{S1}.jsonl"), s1)
    write_jsonl(os.path.join(root, "proj", f"{S2}.jsonl"), s2)
    write_jsonl(os.path.join(root, "proj", S2, "subagents", "agent-a1.jsonl"), sub)
    write_jsonl(os.path.join(root, "proj", f"{S3}.jsonl"), s3)
    write_jsonl(os.path.join(root, "proj", f"{S4}.jsonl"), s4)


CASE = """---
date: {date}
project: demo
stack: [wordpress]
tools: [{tools}]
signature: "x"
{extra}kind: {kind}
class: {cls}
guard: {guard}
status: rule
rule: {rule}
scope: {scope}
repeat_of: {repeat}
rule_loaded: {loaded}
session: {session}
---
{body}"""
PROPOSAL = "Proposed (not built): a check that fails on it.\n"
BODY = "Symptom: s.\n"
GLOBAL_RULE = "~/.claude/CLAUDE.md#Tests"
# Global rules live in the config dir or in a Rigger clone's plugins/rigger-<name>/rules/<file>.md.
SKILL_RULE = "~/.claude/skills/git-workflow/SKILL.md#Commits"
BACKSLASH_RULE = r"%USERPROFILE%\.claude\templates\implementer-brief.md#Brief"
PROJECT_RULE = "/sites/demo/CLAUDE.md#Pitfalls"
HARNESS_RULE = "/src/rigger-claude-code/plugins/rigger-rules/rules/scope.md#Scope"


def case(date, kind="env-defect", cls="wrong-client", guard="none", repeat="none", loaded="unknown",
         session="unknown", extra="", tools="", body=None, rule=GLOBAL_RULE, scope="global"):
    if body is None:
        body = BODY + (PROPOSAL if guard == "proposed" else "")
    return CASE.format(date=date, kind=kind, cls=cls, guard=guard, repeat=repeat, loaded=loaded,
                       session=session, extra=extra, tools=tools, body=body, rule=rule, scope=scope)


# Stands in for shell_guard.py: denies a PowerShell command holding `^`; `warn` gives a finding that does
# not deny, `boom` raises.
FAKE_SHELL_GUARD = '''class Finding:
    def __init__(self, rule, deny, message):
        self.rule, self.deny, self.message = rule, deny, message


def check_installed(tool, cmd, cwd=None):
    if "boom" in cmd:
        raise ValueError("boom")
    if tool == "PowerShell" and "^" in cmd:
        return [Finding("bat-metachar", "warn" not in cmd, "m")]
    return []


def unused():
    return Finding("quoted-not-a-rule", True, "m")
'''
PROBE = "probe: \"composer require 'a/b:^1'\"\n"
GUARD_DIR = "plugins/rigger-windows/tools/shell-guard"


def build_repo(repo):
    """A repo with the shell-guard rule checker (a fake hook), a named test and a four-case archive that
    passes the lint."""
    with open(GUARD, encoding="utf-8") as fh:
        checker = fh.read()
    files = {
        GUARD_DIR + "/shell_guard.py": FAKE_SHELL_GUARD,
        GUARD_DIR + "/lessons_guard.py": checker,
        "tools/x/tests/test_x.py": "def test_guarded_thing():\n    pass\n# def test_commented_out():\n"
                                   "test_called_only()\ncheck(\"a named check\", 1, 1)\n",
        "tools/x/test_outside.py": "def test_outside():\n    pass\n",
        "tools/x/tests/x.tests.ps1": "Test-Case 'refuses a run' {\n}\n# Test-Case 'a commented case' {\n"
                                     "<#\n  Test-Case 'in a block comment' {\n#>\nInvoke-Test-Case 'only invoked'\n",
        "lessons/classes.txt": "# vocabulary\nwrong-client: a wrong client\nbad-call: a bad call\n",
        "lessons/README.md": "not a case\n",
        # error_text in another case than the output, and digits the report masks.
        "lessons/2026-10-22-mysql-case.md": case("2026-10-22", guard="proposed",
                                                 extra='error_text: "Caching_SHA2_Password"\n'),
        "lessons/2026-10-25-mysql-again.md": case("2026-10-25", guard="live:bat-metachar",
                                                  repeat="2026-10-22-mysql-case.md", loaded="yes", session=S2,
                                                  tools="composer, powershell",
                                                  extra='error_text: "never printed anywhere"\n' + PROBE),
        "lessons/2026-10-28-mysql-third.md": case("2026-10-28", guard="live:tools/x/tests/test_x.py::a named check",
                                                  repeat="2026-10-25-mysql-again.md", loaded="no"),
        # The output says "npm error Missing script".
        "lessons/2026-11-01-call-case.md": case("2026-11-01", kind="judgment", cls="bad-call",
                                                guard="live:tools/x/tests/test_x.py::test_guarded_thing",
                                                extra='error_text: "npm error missing script"\n'),
    }
    for rel, text in files.items():
        path = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return os.path.join(repo, "lessons")


def names(problems, lessons):
    """Problem lines with the archive folder taken off their path, so they start with the file name."""
    prefix = os.path.join(lessons, "")
    return [p[len(prefix):] if p.startswith(prefix) else p for p in problems]


def lint_names(lessons, repo):
    return names(ll.lint(lessons, repo=repo)[1], lessons)


def lint_with(repo, rel, text, run=None):
    """Lint problems (or `run()`) after writing one file; the file is put back afterwards."""
    path = os.path.join(repo, *rel.split("/"))
    try:
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
    except OSError:
        before = None
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        return run() if run else lint_names(os.path.join(repo, "lessons"), repo)
    finally:
        if before is None:
            os.remove(path)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(before)


def check_lint(repo):
    lessons = build_repo(repo)
    count, problems = ll.lint(lessons, repo=repo)
    check("lint: a valid archive has no problems", (count, problems), (4, []))
    first = "lessons/2026-10-22-mysql-case.md"
    good = case("2026-10-22", guard="proposed")
    live = case("2026-10-22", guard="live:bat-metachar", tools="powershell", extra=PROBE)
    check("lint: a live shell-guard guard whose probe the hook denies passes", lint_with(repo, first, live), [])
    third = "lessons/2026-10-28-mysql-third.md"
    with open(os.path.join(repo, *third.split("/")), encoding="utf-8") as fh:
        third_good = fh.read()
    third_none = third_good.replace("guard: live:tools/x/tests/test_x.py::a named check", "guard: none")
    check("lint: a repeat with guard none and a guard_reason passes",
          lint_with(repo, third, third_none.replace("kind:", "guard_reason: a judgement no check sees\nkind:")), [])
    guard_py = GUARD_DIR + "/shell_guard.py"
    cases = {
        "kind outside the set": (first, good.replace("kind: env-defect", "kind: bug"), "kind must be one of"),
        "class not in classes.txt": (first, good.replace("class: wrong-client", "class: wrong-clients"),
                                     "class 'wrong-clients' is not in classes.txt"),
        "live guard naming a rule the probe does not get": (
            first, live.replace("guard: live:bat-metachar", "guard: live:quoted-not-a-rule"),
            "check_installed does not deny the probe with quoted-not-a-rule (PowerShell: bat-metachar)"),
        "live guard whose probe the hook lets through": (
            first, live.replace("^1", "1"), "check_installed does not deny the probe with bat-metachar (PowerShell: no deny)"),
        "live guard whose finding does not deny": (
            first, live.replace("^1'", "^1' warn"), "does not deny the probe with bat-metachar (PowerShell: no deny)"),
        "live guard probed in a shell the hook does not deny in": (
            first, live.replace("tools: [powershell]", "tools: [bash]"), "with bat-metachar (Bash: no deny)"),
        "live shell-guard guard whose tools name no shell": (
            first, live.replace("tools: [powershell]", "tools: [composer]"), "tools names no shell"),
        "live guard whose check raises": (first, live.replace("composer require", "boom"), "PowerShell: raised ValueError"),
        "live shell-guard guard without a probe": (first, live.replace(PROBE, ""), "needs a probe"),
        "live shell-guard guard with an empty probe": (first, live.replace(PROBE, 'probe: ""\n'),
                                                       "probe must be a non-empty string"),
        "probe on a test guard": (first, live.replace(
            "guard: live:bat-metachar", "guard: live:tools/x/tests/test_x.py::test_guarded_thing"),
            "probe is only for a live rule guard"),
        "shell_guard.py without check_installed": (guard_py, "def check(tool, cmd):\n    return []\n",
                                                   "shell_guard.py has no check_installed"),
        "shell_guard.py that does not load": (guard_py, "def check_installed(:\n", "shell_guard.py (SyntaxError"),
        "rule checker that does not load": (GUARD_DIR + "/lessons_guard.py", "def rule_guard_problem(:\n",
                                            "rule checker cannot load (SyntaxError"),
        "rule checker without rule_guard_problem": (GUARD_DIR + "/lessons_guard.py", "def other():\n    pass\n",
                                                    "rule checker has no rule_guard_problem"),
        "proposed guard without a proposal line": (first, good.replace(PROPOSAL, ""),
                                                   "guard proposed needs a body line"),
        "proposal inside a sentence": (first, good.replace(PROPOSAL, "Fix: x. " + PROPOSAL),
                                       "guard proposed needs a body line"),
        "proposal naming no mechanism": (first, good.replace(PROPOSAL, "Proposed (not built): TODO\n"),
                                         "guard proposed needs a body line"),
        "guard_reason with a guard": (first, good.replace("kind:", "guard_reason: why\nkind:"),
                                      "guard_reason is only for guard: none, not proposed"),
        "empty guard_reason": (third, third_none.replace("kind:", 'guard_reason: ""\nkind:'),
                               "guard_reason must be a non-empty string"),
        "error_text short once its digits are masked": (
            first, good.replace("kind:", 'error_text: "Exit code 1234567890"\nkind:'),
            "error_text is shorter than 15 characters after masking: 'Exit code <n>'"),
        "live guard naming a missing test": (first, good.replace(
            "guard: proposed", "guard: live:tools/x/tests/test_x.py::test_gone"), "no `def test_gone(`"),
        "live guard naming a test only in a comment": (first, good.replace(
            "guard: proposed", "guard: live:tools/x/tests/test_x.py::test_commented_out"),
            "no `def test_commented_out(`"),
        "live guard naming a test that is only called": (first, good.replace(
            "guard: proposed", "guard: live:tools/x/tests/test_x.py::test_called_only"), "no `def test_called_only(`"),
        "live guard naming a file outside tests/": (first, good.replace(
            "guard: proposed", "guard: live:tools/x/test_outside.py::test_outside"), "is not a file under a tests/"),
        "live guard naming a missing test file": (first, good.replace(
            "guard: proposed", "guard: live:tools/x/tests/nope.py::t"), "test file tools/x/tests/nope.py not found"),
        "guard outside the set": (first, good.replace("guard: proposed", "guard: built"), "guard must be none"),
        "empty live guard": (first, good.replace("guard: proposed", "guard: live:"), "guard must be none"),
        "repeat_of a missing file": (first, good.replace("repeat_of: none", "repeat_of: 2026-10-01-gone.md"),
                                     "is not a case file here"),
        "repeat_of a later file": (first, good.replace("repeat_of: none", "repeat_of: 2026-10-25-mysql-again.md"),
                                   "is not earlier than this case"),
        "repeat_of another class": ("lessons/2026-11-01-call-case.md", case(
            "2026-11-01", kind="judgment", cls="bad-call", guard="proposed", repeat="2026-10-22-mysql-case.md"),
            "has another class"),
        "rule_loaded outside the set": (first, good.replace("rule_loaded: unknown", "rule_loaded: none"),
                                        "rule_loaded must be one of"),
        "rule_loaded yes with kind judgment": (first, good.replace("rule_loaded: unknown", "rule_loaded: yes")
                                               .replace("kind: env-defect", "kind: judgment"), "kind shortcut, not judgment"),
        "repeat with no guard": (third, third_good.replace("guard: live:tools/x/tests/test_x.py::a named check",
                                                           "guard: none"), "a repeat needs a guard"),
        "repeat_of not the latest earlier case": (third, third_good.replace(
            "repeat_of: 2026-10-25-mysql-again.md", "repeat_of: 2026-10-22-mysql-case.md"),
            "is not the latest earlier case of the class (2026-10-25-mysql-again.md)"),
        "error_text shorter than 15 characters": (first, good.replace("kind:", 'error_text: "fourteen chars"\nkind:'),
                                                  "error_text is shorter than 15 characters"),
        "invented session id": (first, good.replace("session: unknown", "session: s-1"), "session must be"),
        "date differs from the file name": (first, good.replace("date: 2026-10-22", "date: 2026-10-23"),
                                            "differs from the file name"),
        "missing key": (first, good.replace("rule_loaded: unknown\n", ""), "missing key: rule_loaded"),
        "unknown key": (first, good.replace("kind:", "owner: me\nkind:"), "unknown key: owner"),
        "rule status without a rule": (first, good.replace("rule: ~/.claude/CLAUDE.md#Tests", "rule: none"),
                                       "status rule needs a rule path"),
        "status outside the set": (first, good.replace("status: rule", "status: guard"), "status must be one of"),
        "missing scope": (first, good.replace("scope: global\n", ""), "missing key: scope"),
        "scope outside the set": (first, good.replace("scope: global", "scope: team"),
                                  "scope must be global, skill:<name> or project:<name>, got 'team'"),
        "project scope naming another project": (
            first, good.replace("scope: global", "scope: project:other").replace(GLOBAL_RULE, PROJECT_RULE),
            "scope project:other names another project than demo"),
        "project scope with a rule in ~/.claude": (first, good.replace("scope: global", "scope: project:demo"),
                                                   "scope project:demo needs its rule in that project"),
        "project scope with a backslash rule path in the config dir": (
            first, good.replace("scope: global", "scope: project:demo").replace(GLOBAL_RULE, BACKSLASH_RULE),
            "scope project:demo needs its rule in that project"),
        "project scope with a rule in a config-dir skill": (
            first, good.replace("scope: global", "scope: project:demo").replace(GLOBAL_RULE, SKILL_RULE),
            "scope project:demo needs its rule in that project"),
        "project scope with an absolute path into .claude": (
            first, good.replace("scope: global", "scope: project:demo")
            .replace(GLOBAL_RULE, r"C:\Users\someone\.claude\skills\x\SKILL.md#A"),
            "scope project:demo needs its rule in that project"),
        "project scope with a $HOME/.claude path": (
            first, good.replace("scope: global", "scope: project:demo").replace(GLOBAL_RULE, "$HOME/.claude/CLAUDE.md#A"),
            "scope project:demo needs its rule in that project"),
        "project scope with a mixed-case config dir path": (
            first, good.replace("scope: global", "scope: project:demo")
            .replace(GLOBAL_RULE, "~/.Claude/README.md#A"),
            "scope project:demo needs its rule in that project"),
        "scope as a list": (first, good.replace("scope: global", "scope: [global]"),
                            "scope must be global, skill:<name> or project:<name>, got ['global']"),
        "skill scope with a capital letter": (first, good.replace("scope: global", "scope: skill:Git-workflow")
                                              .replace(GLOBAL_RULE, SKILL_RULE), "scope must be global"),
        "project as a list": (first, good.replace("project: demo", "project: [a, b]"),
                              "project must be a non-empty string"),
        "global scope with a rule in a mixed-case Skills folder": (
            first, good.replace(GLOBAL_RULE, "~/.claude/Skills/windows-dev-env/SKILL.md#A"),
            "scope global but the rule is in a skill: scope skill:windows-dev-env"),
        "skill scope with a rule outside that skill": (first, good.replace("scope: global", "scope: skill:git-workflow"),
                                                       "scope skill:git-workflow needs its rule under skills/git-workflow/"),
        "skill scope naming a prefix of the rule's skill": (
            first, good.replace("scope: global", "scope: skill:git").replace(GLOBAL_RULE, SKILL_RULE),
            "scope skill:git needs its rule under skills/git/"),
        "global scope with a rule in a project": (first, good.replace(GLOBAL_RULE, PROJECT_RULE),
                                                  "scope global needs its rule in ~/.claude, $CLAUDE_CONFIG_DIR or a "
                                                  "harness repo"),
        "global scope with a rule in a project's own .claude folder": (
            first, good.replace(GLOBAL_RULE, "/sites/demo/.claude/CLAUDE.md#Pitfalls"),
            "scope global needs its rule in ~/.claude"),
        "project scope with a rule in a Rigger clone": (
            first, good.replace("scope: global", "scope: project:demo").replace(GLOBAL_RULE, HARNESS_RULE),
            "scope project:demo needs its rule in that project"),
        "global scope with a rule in a skill": (first, good.replace(GLOBAL_RULE, SKILL_RULE),
                                                "scope global but the rule is in a skill: scope skill:git-workflow"),
        "empty error_text": (first, good.replace("kind:", 'error_text: ""\nkind:'), "error_text must be a non-empty"),
        "no frontmatter": (first, "Symptom: s.\n", "no frontmatter"),
        "empty body": (first, good.replace(BODY + PROPOSAL, ""), "empty body"),
        "bad file name": ("lessons/2026-10-22_Mixed.md", good, "file name must be"),
        "duplicate class line": ("lessons/classes.txt", "wrong-client: a\nwrong-client: b\nbad-call: c\n",
                                 "duplicate class wrong-client"),
        "class line without a meaning": ("lessons/classes.txt", "wrong-client: a\nbad-call\n",
                                         "expected `slug: meaning`"),
        "frontmatter not closed": (first, "---\ndate: 2026-10-22\nSymptom: s.\n", "frontmatter is not closed"),
        "duplicate key": (first, good.replace("kind:", "kind: knowledge\nkind:"), "duplicate key: kind"),
        "unreadable frontmatter line": (first, good.replace("kind:", "just words\nkind:"),
                                        "unreadable frontmatter line"),
        "unreadable quoted value": (first, good.replace('signature: "x"', 'signature: "x'),
                                    "signature: unreadable value"),
        "stack not a list": (first, good.replace("stack: [wordpress]", "stack: wordpress"), "stack must be a [list]"),
        "date not a date": (first, good.replace("date: 2026-10-22", "date: 2026-13-40"), "date is not YYYY-MM-DD"),
    }
    for name, (rel, text, expected) in cases.items():
        problems = lint_with(repo, rel, text)
        check(f"lint: {name} (problems: {problems})", len([p for p in problems if expected in p]), 1)
    check("lint: a file with no frontmatter gets one problem, its successor one for the broken link",
          lint_with(repo, first, "Symptom: s.\n"),
          ["2026-10-22-mysql-case.md: no frontmatter (first line must be ---)",
           "2026-10-25-mysql-again.md: repeat_of 2026-10-22-mysql-case.md has another class (None)"])
    call = "lessons/2026-11-01-call-case.md"
    with open(os.path.join(repo, *call.split("/")), encoding="utf-8") as fh:
        call_good = fh.read()
    call_project = call_good.replace("scope: global", "scope: project:demo")
    check("lint: a project scope with its rule in that project passes",
          lint_with(repo, call, call_project.replace(GLOBAL_RULE, PROJECT_RULE)), [])
    check("lint: a project scope with no rule passes",
          lint_with(repo, call, call_project.replace(GLOBAL_RULE, "none").replace("status: rule", "status: archived")),
          [])
    check("lint: a global scope with no rule passes",
          lint_with(repo, call, call_good.replace(GLOBAL_RULE, "none").replace("status: rule", "status: archived")), [])
    check("lint: a skill scope with its rule in that skill passes",
          lint_with(repo, call, call_good.replace("scope: global", "scope: skill:git-workflow")
                    .replace(GLOBAL_RULE, SKILL_RULE)), [])
    check("lint: a global scope with a backslash rule path in the config dir passes",
          lint_with(repo, call, call_good.replace(GLOBAL_RULE, BACKSLASH_RULE)), [])
    third_project = third_good.replace("scope: global", "scope: project:demo").replace(GLOBAL_RULE, PROJECT_RULE)
    promoted = ("scope global: class wrong-client has a project-scoped case and cases from one project only "
                "(demo); keep it project:demo until a second project has the class")
    check("lint: a class promoted past its project with cases from one project fails",
          lint_with(repo, third, third_project),
          [f"2026-10-22-mysql-case.md: {promoted}", f"2026-10-25-mysql-again.md: {promoted}"])
    check("lint: a project name with an underscore passes",
          lint_with(repo, call, call_good.replace("project: demo", "project: my_site")
                    .replace("scope: global", "scope: project:my_site")
                    .replace(GLOBAL_RULE, "/sites/my_site/CLAUDE.md#Pitfalls")), [])
    check("lint: a project-scoped case with no rule does not block its class's global cases",
          lint_with(repo, third, third_good.replace("scope: global", "scope: project:demo")
                    .replace(GLOBAL_RULE, "none").replace("status: rule", "status: archived")), [])
    check("lint: ... and passes once a second project has the class",
          lint_with(repo, third, third_project, run=lambda: lint_with(
              repo, first, good.replace("project: demo", "project: other"))), [])
    check("lint: still clean after the mutations were put back", lint_names(lessons, repo), [])
    check("lint: a live shell guard with no rule checker to confirm it fails, and test guards need their repo",
          lint_names(lessons, os.path.join(repo, "lessons")),
          ["2026-10-25-mysql-again.md: guard live:bat-metachar: the case's tools name a shell, so a rule checker "
           "must confirm the guard, and none is loaded (--rule-checker FILE, or a --repo with "
           "plugins/*/tools/*/lessons_guard.py)",
           "2026-10-28-mysql-third.md: guard live:tools/x/tests/test_x.py::a named check: "
           "test file tools/x/tests/test_x.py not found",
           "2026-11-01-call-case.md: guard live:tools/x/tests/test_x.py::test_guarded_thing: "
           "test file tools/x/tests/test_x.py not found"])
    elsewhere = os.path.join(repo, "elsewhere")
    os.makedirs(elsewhere, exist_ok=True)
    check("lint: test guards resolve in any of several repos, and a --rule-checker file is loaded",
          names(ll.lint(lessons, repo=[elsewhere, repo], rule_checkers=[
              os.path.join(repo, *GUARD_DIR.split("/"), "lessons_guard.py")])[1], lessons), [])
    other_rule = live.replace("tools: [powershell]", "tools: [composer]").replace("live:bat-metachar",
                                                                                 "live:some-lint-rule")
    check("lint: a live rule guard no checker claims, in a case whose tools name no shell, is accepted as written",
          lint_with(repo, first, other_rule), [])
    composer = {"tools": ["composer", "powershell", "cmd"], "probe": r"vendor\bin\composer require 'pkg:^5.3'"}
    real, _ = ll.load_checkers([GUARD])
    with open(os.path.join(os.path.dirname(GUARD), "shell_guard.py"), encoding="utf-8") as fh:
        reported = set(re.findall(r'Finding\(\s*"([a-z0-9-]+)"', fh.read()))
    check("rule checker: RULES are the rule ids shell_guard.py reports (20 or more)",
          (len(reported) >= 20, sorted(real[0].RULES)), (True, sorted(reported)))
    check("lint: the rigger-windows shell-guard hook denies a real probe",
          ll.guard_problem("live:bat-metachar", [], composer, real), None)
    check("lint: ... and lets the same command without the caret through",
          ll.guard_problem("live:bat-metachar", [], dict(composer, probe="composer require 'pkg:5.3'"), real),
          "guard live:bat-metachar: check_installed does not deny the probe with bat-metachar (PowerShell: no deny)")
    check("rule checker: tools that name no shell are a problem when it is asked directly",
          real[0].rule_guard_problem("bat-metachar", dict(composer, tools=["composer"])),
          "guard live:bat-metachar: tools names no shell (powershell, bash, git-bash) to run the probe in")
    check("lint: a live guard may name a PowerShell Test-Case",
          ll.guard_problem("live:tools/x/tests/x.tests.ps1::refuses a run", repo), None)
    check("lint: ... but not one that is only in a comment",
          ll.guard_problem("live:tools/x/tests/x.tests.ps1::a commented case", repo),
          "guard live:tools/x/tests/x.tests.ps1::a commented case: no `def a commented case(`, "
          "`check(\"a commented case\"` or `Test-Case 'a commented case'` in tools/x/tests/x.tests.ps1")
    check("lint: ... nor one inside a <# #> block comment",
          ll.guard_problem("live:tools/x/tests/x.tests.ps1::in a block comment", repo) is not None, True)
    check("lint: ... nor a name passed to another command ending in Test-Case",
          ll.guard_problem("live:tools/x/tests/x.tests.ps1::only invoked", repo) is not None, True)
    missing_classes = os.path.join(repo, "nowhere", "classes.txt")
    check("lint: a missing classes.txt is a problem",
          [p.split(" (")[0] for p in ll.read_classes(missing_classes)[1]], [missing_classes + ": cannot read"])
    script = LINT
    empty = os.path.join(repo, "empty")
    os.makedirs(empty, exist_ok=True)
    shutil.copy(os.path.join(lessons, "classes.txt"), empty)

    def cli(lessons_dir):
        return subprocess.run([sys.executable, "-I", script, "--archive", lessons_dir, "--repo", repo],
                              capture_output=True, text=True, encoding="utf-8")
    clean = cli(lessons)
    check("lint CLI: a clean archive exits 0", (clean.returncode, clean.stdout.strip()),
          (0, "# 4 case files checked, 0 problems"))
    check("lint CLI: an archive with no cases exits 2", cli(empty).returncode, 2)
    bad = lint_with(repo, first, good.replace("kind: env-defect", "kind: bug"), run=lambda: cli(lessons))
    check("lint CLI: a problem exits 1 and is printed",
          (bad.returncode, bad.stdout.splitlines()[-1:]), (1, ["# 4 case files checked, 1 problems"]))


def section(text, title):
    """Lines under the `## title...` header up to the next blank line."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## " + title)), None)
    if start is None:
        return []
    out = []
    for ln in lines[start + 1:]:
        if not ln.strip():
            break
        out.append(ln)
    return out


def check_archive(root, repo):
    build_transcripts(root)
    lessons = os.path.join(repo, "lessons")
    cases = ta.load_cases(lessons, LINT)
    events = ta.archive_events(root, days=3650, workers=1)
    check("archive: parallel parse gives the same events",
          {k: sorted(map(str, v)) for k, v in ta.archive_events(root, days=3650, workers=2).items()},
          {k: sorted(map(str, v)) for k, v in events.items()})
    parsed = [ta.parse_events(os.path.join(folder, n)) for folder, _, names in os.walk(root)
              for n in sorted(names) if n.endswith(".jsonl")]
    check("archive: a copied event keeps the same session whatever the file order",
          {k: sorted(map(str, v)) for k, v in ta.merge_events(parsed).items()},
          {k: sorted(map(str, v)) for k, v in ta.merge_events(list(reversed(parsed))).items()})
    try:
        gone = ta.parse_events(os.path.join(root, "removed-after-listing.jsonl"))
    except Exception as exc:  # recorded as a failed check, so the run reaches the summary
        gone = {"errors": [], "skills": [], "allow": [], "hooks": [], "failed": f"raised {type(exc).__name__}"}
    check("archive: a file that cannot be read is reported, not raised",
          (gone["errors"], (gone["failed"] or "").startswith("FileNotFoundError")), ([], True))
    check("archive: merged events keep the unreadable file", len(ta.merge_events(parsed + [gone])["failed"]), 1)
    check("archive: a hook error with empty stderr", ta.hook_marker(""), "<no stderr>")
    text, ok = ta.build_archive(cases, events, NOW, 30, expect_case="2026-10-22-mysql-case.md",
                                expect_hook=("SessionStart", "episodic-memory", "ERR_MODULE_NOT_FOUND"))
    check("archive: positive controls found", ok, True)
    check("archive: positive control lines", text.splitlines()[1:3], [
        "positive control: case 2026-10-22-mysql-case.md matched 1 recurring error line(s) in 2 sessions",
        "positive control: hook error SessionStart + episodic-memory + ERR_MODULE_NOT_FOUND counted 2 times, "
        "first 2026-10-20"])
    months = section(text, "Cases per month by kind")
    check("archive: cases per month by kind", months[1:],
          ["2026-10           3          0          0          0      3",
           "2026-11           0          0          1          0      1"])
    with_case = section(text, "Machine-made repeats")[1:]
    check("archive: recurring errors with their case (error_text in any letter case), sessions not "
          "inflated by a resumed copy, a subagent or a line without a session id",
          [ln.split(" | ")[0].split() for ln in with_case],
          [["2", "5", "2026-10-22-mysql-case.md"], ["2", "2", "2026-11-01-call-case.md"]])
    check("archive: tools of the recurring errors", [ln.split(" | ")[1] for ln in with_case],
          ["Bash,PowerShell", "PowerShell"])
    check("archive: one row per case with sessions, results, lines and the last day seen",
          section(text, "Cases with a recurring error line")[1:],
          ["       2       5     1 2026-10-25  2026-10-22-mysql-case.md",
           "       2       2     1 2026-10-28  2026-11-01-call-case.md"])
    no_case = section(text, "Recurring with no case")[1:]
    check("archive: recurring error with no case (one-session errors, rejections and successes left out)",
          no_case, ["       2       2  Bash | make: *** No rule to make target 'once'."])
    check("archive: summary counts", ("error lines in >=2 sessions: 3 (2 with a case, 1 with no case); "
                                      "cases whose error_text did not recur: 1 of 3") in text, True)
    check("archive: undated events are reported", "undated events skipped: 1;" in text, True)
    check("archive: open guards with age", section(text, "Open guards")[1:],
          ["   11  wrong-client | 2026-10-22-mysql-case.md"])
    check("archive: one line per chain, from its newest case", section(text, "Repeats from repeat_of"),
          ["  3  wrong-client: 10-22 mysql-case -> 10-25 mysql-again -> 10-28 mysql-third"])
    check("archive: repeat header counts rule_loaded",
          "## Repeats from repeat_of: 2 in 1 classes (rule_loaded: no 1, yes 1)" in text, True)
    check("archive: skill use from Skill calls and SKILL.md reads", section(text, "Skill use")[1:],
          ["     1      1      0  brainstorming", "     1      0      1  windows-dev-env"])
    hooks = section(text, "Hook non-blocking errors")[1:]
    check("archive: hook errors grouped by hook and command, copies dropped, old ones out of the window",
          [ln.split()[:4] for ln in hooks], [["2", "2", "2026-10-20", "2026-10-25"],
                                             ["1", "1", "2026-10-28", "2026-10-28"]])
    check("archive: hook group keeps the plugin script path",
          hooks[0].endswith(f"ERR_MODULE_NOT_FOUND 2 | SessionStart:startup | {EPISODIC}"), True)
    check("archive: a hook error with no error code shows its first stderr line",
          hooks[1].endswith("No stderr output 1 | PreToolUse:Read | node \"${CLAUDE_PLUGIN_ROOT}/hooks/read-guard.js\""),
          True)
    check("archive: shell-guard escapes counted from the trailing comment only",
          "commands carrying '# shell-guard: allow': 1 (PowerShell 1)" in text, True)
    _, missing_case = ta.build_archive(cases, events, NOW, 30, expect_case="2026-10-25-mysql-again.md")
    check("archive: a case no recurring error matches fails the control", missing_case, False)
    _, missing_hook = ta.build_archive(cases, events, NOW, 30, expect_hook=("episodic-memory", "EACCES"))
    check("archive: a hook error that is not there fails the control", missing_hook, False)

    history = os.path.join(root, "history")
    aggregates = ta.monthly_aggregates(events, "2026-10-03")
    check("history: months before the window are left out", sorted(aggregates), ["2026-10"])
    october = aggregates.get("2026-10", {})
    check("history: October aggregate",
          (october.get("events"), october.get("error_results"), october.get("shell_guard_allow"),
           october.get("hooks"), october.get("skills"), october.get("transcripts_modified_since"),
           sorted(october.get("errors_in_2_sessions", {}).values(), key=str)),
          (16, 10, 1, {f"SessionStart:startup | {EPISODIC}": 2,
                      'PreToolUse:Read | node "${CLAUDE_PLUGIN_ROOT}/hooks/read-guard.js"': 1},
           {"brainstorming": {"Skill": 1}, "windows-dev-env": {"Read": 1}}, "2026-10-03",
           [{"results": 2, "sessions": 2}, {"results": 2, "sessions": 2}, {"results": 5, "sessions": 2}]))
    check("history: first write", ta.write_history(history, aggregates), ["2026-10"])
    with open(os.path.join(history, "2026-10.json"), encoding="utf-8") as fh:
        check("history: file holds the aggregate", json.load(fh), october)
    fewer = {"2026-10": dict(october, events=october["events"] - 1, error_results=0)}
    check("history: a run that sees fewer events keeps the file", ta.write_history(history, fewer), [])
    with open(os.path.join(history, "2026-10.json"), encoding="utf-8") as fh:
        check("history: kept file unchanged", json.load(fh)["error_results"], october["error_results"])
    more = {"2026-10": dict(october, events=october["events"] + 1)}
    check("history: a run that sees more events rewrites it", ta.write_history(history, more), ["2026-10"])

    script = SCRIPT
    no_config = os.path.join(root, "no-config")
    control_root = os.path.join(HERE, "fixtures", "control")
    for n, (label, extra, code, said) in enumerate((
            ("a found control without --write-history writes nothing", ["--expect-hook", "episodic-memory"], 0,
             "history not written: no --write-history"),
            ("a failed control writes nothing", ["--expect-case", "2026-10-25-mysql-again.md", "--write-history"], 1,
             "history not written: a positive control failed"),
            ("a case control on the fixture transcripts passes",
             ["--expect-case", "2026-10-22-mysql-case.md", "--control-root", control_root], 0,
             "positive control: case 2026-10-22-mysql-case.md matched 1 recurring error line(s) of the control "
             "transcripts in 2 sessions"),
            ("a case control on the fixture fails for a case the fixture does not print",
             ["--expect-case", "2026-11-01-call-case.md", "--control-root", control_root], 1,
             "NOT matched by any recurring error line of the control transcripts: FAILED"),
            ("found controls with --write-history write the months", ["--expect-hook", "episodic-memory",
                                                                     "--write-history"], 0,
             "history written: 2026-09, 2026-10"))):
        folder = os.path.join(root, f"cli-history-{n}")
        run = subprocess.run([sys.executable, "-I", script, "--root", root, "archive", "--days", "3650",
                              "--lessons", lessons, "--lint", LINT, "--history-dir", folder, *extra],
                             capture_output=True, text=True, encoding="utf-8", env=config_env(no_config))
        files = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        check(f"archive CLI: {label}", (run.returncode, said in run.stdout, files),
              (code, True, ["2026-09.json", "2026-10.json"] if "history written" in said else []))
    for label, extra, said in (
            ("without --lessons it refuses to run", [], "--lessons"),
            ("--write-history without --history-dir refuses to run", ["--lessons", lessons, "--write-history"],
             "--write-history needs --history-dir"),
            ("a --lint that is not there refuses to run",
             ["--lessons", lessons, "--lint", os.path.join(root, "no-lint.py")], "lint not found")):
        run = subprocess.run([sys.executable, "-I", script, "--root", root, "archive", "--days", "3650", *extra],
                             capture_output=True, text=True, encoding="utf-8", env=config_env(no_config))
        check(f"archive CLI: {label}", (run.returncode, said in run.stderr), (2, True))
    # Outside every Windows ANSI code page, so a stderr in the console's code page cannot carry it.
    non_ascii = os.path.join(root, "Ωμέγα 日本", "no-lint.py")
    env = {key: value for key, value in config_env(no_config).items()
           if key.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    run = subprocess.run([sys.executable, "-I", script, "--root", root, "archive", "--lessons", lessons, "--lint", non_ascii],
                         capture_output=True, env=env)
    check("archive CLI: an error naming a non-ASCII path is UTF-8 on stderr",
          (run.returncode, non_ascii in run.stderr.decode("utf-8", "replace")), (2, True))
    check_installed_lint(root, lessons)


def check_installed_lint(root, lessons):
    """`archive` without --lint finds the rigger-lessons lint by its installPath, with token_audit.py run from
    the plugin cache layout (<config>/plugins/cache/<marketplace>/<plugin>/<version>/), and exits 2 without it."""
    config = os.path.join(root, "cache-config")
    cache = os.path.join(config, "plugins", "cache", "rigger")
    core = os.path.join(cache, "rigger-core", "1.0.0")
    lessons_plugin = os.path.join(cache, "rigger-lessons", "2.0.0")
    script = os.path.join(core, "tools", "token-audit", "token_audit.py")
    os.makedirs(os.path.dirname(script))
    shutil.copy(SCRIPT, script)
    os.makedirs(os.path.join(lessons_plugin, "tools"))
    shutil.copy(LINT, os.path.join(lessons_plugin, "tools", "lessons_lint.py"))
    installed = os.path.join(config, "plugins", "installed_plugins.json")

    def run(plugins):
        if os.path.exists(installed):
            os.remove(installed)
        if plugins is not None:
            with open(installed, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(plugins if isinstance(plugins, str) else json.dumps({"version": 2, "plugins": plugins}))
        return subprocess.run([sys.executable, "-I", script, "--root", root, "archive", "--days", "3650",
                               "--lessons", lessons], capture_output=True, text=True, encoding="utf-8",
                              env=config_env(config))

    found = run({"rigger-lessons@rigger": [{"scope": "user", "installPath": lessons_plugin}]})
    check("archive CLI from the cache layout: without --lint it loads the lint at rigger-lessons' installPath",
          (found.returncode, found.stdout.split(";")[0], found.stderr), (0, "# Lessons archive: 4 cases, 2 classes", ""))
    stale = run({"rigger-lessons@rigger": [
        {"scope": "project", "installPath": os.path.join(cache, "rigger-lessons", "0.9.0")},
        {"scope": "user", "installPath": lessons_plugin}]})
    check("archive CLI from the cache layout: an installPath without the lint is passed over for the next one",
          (stale.returncode, stale.stdout.split(";")[0]), (0, "# Lessons archive: 4 cases, 2 classes"))
    for label, plugins in (("no installed_plugins.json", None),
                           ("an installed_plugins.json that is not JSON", "{not json"),
                           ("rigger-lessons not installed, another plugin holding a lessons_lint.py",
                            {"rigger-core@rigger": [{"installPath": core}],
                             "lessons-copy@elsewhere": [{"installPath": lessons_plugin}]}),
                           ("a rigger-lessons installPath without the lint",
                            {"rigger-lessons@rigger": [{"installPath": core}]}),
                           ("an installed_plugins.json whose plugins is a list", '{"plugins": []}'),
                           ("an installed_plugins.json that is a list", "[]"),
                           ("a rigger-lessons entry that is a string",
                            {"rigger-lessons@rigger": "x"}),
                           ("a rigger-lessons entry that is not iterable",
                            {"rigger-lessons@rigger": 5}),
                           ("a rigger-lessons entry list holding a number",
                            {"rigger-lessons@rigger": [1]}),
                           ("a rigger-lessons entry without an installPath",
                            {"rigger-lessons@rigger": [{"scope": "user"}]}),
                           ("a rigger-lessons installPath that is a number",
                            {"rigger-lessons@rigger": [{"installPath": 7}]})):
        refused = run(plugins)
        check(f"archive CLI from the cache layout: {label} exits 2 and asks for --lint",
              (refused.returncode, "pass --lint" in refused.stderr, refused.stdout), (2, True, ""))
    # Two rigger-lessons entries, the one that sorts last listed first: the sorted key wins, so the real lint
    # (4 cases) is loaded, not the stub of the entry listed first (0 cases).
    stub_plugin = os.path.join(cache, "rigger-lessons", "3.0.0")
    os.makedirs(os.path.join(stub_plugin, "tools"))
    with open(os.path.join(stub_plugin, "tools", "lessons_lint.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("def load_cases(lessons_dir):\n    return []\n")
    ordered = run({"rigger-lessons@zz-stub": [{"installPath": stub_plugin}],
                   "rigger-lessons@aa-real": [{"installPath": lessons_plugin}]})
    check("archive CLI from the cache layout: of two rigger-lessons entries the first by sorted key is used",
          (ordered.returncode, ordered.stdout.split(";")[0], ordered.stderr),
          (0, "# Lessons archive: 4 cases, 2 classes", ""))


def check_recurring():
    tail = "tail_marker_xyz could not be loaded"
    full = ta.error_line("PowerShell", "Exit code 1\nWarning: " + "a step ran long and printed more words " * 4 + tail)
    check("error line: the whole line is kept, the group key is its head",
          (full.endswith(tail), len(full) > 100, tail in ta.group_key("PowerShell", full)), (True, True, False))
    errors = [("e1", S1, "2026-10-20T08:00:00.000Z", "PowerShell", full),
              ("e2", S2, "2026-10-21T08:00:00.000Z", "PowerShell", full)]
    _, recurring, _, matched = ta.recurring_errors([("long.md", {"error_text": "tail_marker_xyz could not"})], errors)
    check("archive: error_text past character 100 of the line matches", [matched[k] for k in recurring], [["long.md"]])
    errors = [("a1", S1, "2026-10-20T08:00:00.000Z", "PowerShell", "first shared fragment line"),
              ("a2", S2, "2026-10-21T08:00:00.000Z", "PowerShell", "first shared fragment line"),
              ("b1", S2, "2026-10-22T08:00:00.000Z", "Bash", "second shared fragment line"),
              ("b2", S3, "2026-10-23T08:00:00.000Z", "Bash", "second shared fragment line"),
              ("b3", S3, "2026-10-24T08:00:00.000Z", "Bash", "second shared fragment line")]
    groups, _, _, matched = ta.recurring_errors([("shared.md", {"error_text": "shared fragment"})], errors)
    rows = ta.case_rows(groups, matched)
    check("archive: a case row merges the sessions of its lines",
          {k: (sorted(s), n, lines, last) for k, (s, n, lines, last) in rows.items()},
          {"shared.md": ([S1, S2, S3], 5, 2, "2026-10-24T08:00:00.000Z")})


SECRET_LINES = [
    ("ERROR 1045 (28000): Access denied for user 'app_prod'@'example.com' (using password: YES)",
     "app_prod", "'<user>'@'<host>'"),
    ("fatal: unable to access 'https://deploy:Sekret!77@example.com/org/x.git/': returned error: 403",
     "Sekret", "https://<cred>@"),
    ("Error: invalid recipient jane.doe@example.com", "doe", "<email>"),
    # Short enough that the 100-character group key keeps the whole masked line.
    ("curl: (22) error: 401 https://example.com/p?api_key=pk_live_AbCdEf123", "pk_live_AbCdEf", "/p?<query>"),
    # Split so secret scanners do not read the fake key as a real one.
    ("Stripe: invalid API key sk_" "live_51HxYzAbCdEfGhIjKlMnOpQr provided", "HxYzAbCdEfGhIjKlMnOpQr", "key <token>"),
    ("Error: bad signature 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b in header", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
     "signature <hex> in"),
    # 40 characters of base64 with `+` and padding; its head has no digit, so the digit mask cannot hide a leak
    ("Error: token QUJDREVG+d29ybGQrMTIzNDU2Nzg5MGFiY2RlZg= rejected", "QUJDREVG", "token <token> rejected"),
]


HOOK_SECRET = "abcdEFGHijklMNOPqrstUVWX1234"


def check_secrets(root):
    """Known secrets in error output and a hook command, in two sessions: none may reach the history file,
    and each masked line must (or the test would pass on an empty file)."""
    check("lint and archive mask error text alike (error_text is matched against the archive's mask)",
          [ll.mask(line) for line, _, _ in SECRET_LINES] + [ll.mask(SQL_MODES), ll.mask(HOOK_STDERR)],
          [ta.mask(line) for line, _, _ in SECRET_LINES] + [ta.mask(SQL_MODES), ta.mask(HOOK_STDERR)])
    for session, day in ((S1, "2026-10-20"), (S2, "2026-10-21")):
        entries = [a_hook_error(session, f"h-{session}", f"{day}T08:00:00.000Z",
                                command=f'node "${{CLAUDE_PLUGIN_ROOT}}/hooks/x.js" --token {HOOK_SECRET}')]
        for n, (line, _, _) in enumerate(SECRET_LINES):
            tid = f"s{n}-{session}"
            entries += [a_tool_use(session, f"m{tid}", tid, "PowerShell", {"command": "x"}, f"{day}T08:0{n}:00.000Z"),
                        a_tool_result(session, tid, "Exit code 1\n" + line, f"{day}T08:0{n}:01.000Z")]
        write_jsonl(os.path.join(root, "proj", f"{session}.jsonl"), entries)
    events = ta.archive_events(root, days=3650, workers=1)
    folder = os.path.join(root, "history")
    ta.write_history(folder, ta.monthly_aggregates(events, "2026-10-01"))
    with open(os.path.join(folder, "2026-10.json"), encoding="utf-8") as fh:
        raw = json.dumps(json.load(fh), ensure_ascii=False)
    check("history: no secret reaches the file",
          [s for _, s, _ in SECRET_LINES if s in raw] + [HOOK_SECRET] * (HOOK_SECRET in raw), [])
    check("history: every masked line and the hook command are in the file",
          [m for _, _, m in SECRET_LINES if m not in raw] + ["hook"] * ("--token <token>" not in raw), [])


def main():
    archive_root = tempfile.mkdtemp(prefix="token-audit-archive-")
    secrets_root = tempfile.mkdtemp(prefix="token-audit-secrets-")
    repo = tempfile.mkdtemp(prefix="token-audit-repo-")
    try:
        check_lint(repo)
        check_archive(archive_root, repo)
        check_recurring()
        check_secrets(secrets_root)
    finally:
        shutil.rmtree(archive_root, ignore_errors=True)
        shutil.rmtree(secrets_root, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"# tests failed: {len(FAILURES)} of {len(RAN)}")
        sys.exit(1)
    print(f"# all {len(RAN)} checks passed")


if __name__ == "__main__":
    main()
