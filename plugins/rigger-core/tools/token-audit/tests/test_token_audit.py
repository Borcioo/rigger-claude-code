"""python -I test_token_audit.py — builds transcripts in a temp folder and checks the numbers.

Line shapes follow real Claude Code transcripts: one assistant line per content block, all
carrying the message id; the first line's output_tokens can be a placeholder, later lines the
final count; a tool_use block can repeat on a later line.
"""
import argparse
import calendar
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "token_audit.py")
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import token_audit as ta  # noqa: E402

FAILURES = []
RAN = []


def check(name, actual, expected):
    RAN.append(name)
    if actual != expected:
        FAILURES.append(f"{name}: expected {expected!r}, got {actual!r}")


def assistant(mid, usage, at, blocks=()):
    return {"parentUuid": "p", "isSidechain": False, "type": "assistant", "timestamp": at,
            "requestId": "req_" + mid, "message": {"id": mid, "role": "assistant", "usage": usage,
                                                   "content": list(blocks)}}


def thinking():
    return {"type": "thinking", "thinking": "", "signature": "s"}


def tool_use(tid, name, args):
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


def tool_result(tid, text, error=False):
    return {"type": "user", "timestamp": "2026-11-01T10:00:01.000Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}],
         "is_error": error}]}}


def write_jsonl(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(e if isinstance(e, str) else json.dumps(e, separators=(",", ":")))
            fh.write("\n")


def usage(cw, cr, out, cw_1h=None):
    u = {"input_tokens": 2, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
         "output_tokens": out}
    if cw_1h is not None:
        u["cache_creation"] = {"ephemeral_1h_input_tokens": cw_1h, "ephemeral_5m_input_tokens": cw - cw_1h}
    return u


CMD = 'cd X:\\sites\\x; npm test 2>&1'


def build(root):
    main = [
        {"type": "attachment", "attachment": {"type": "skill_listing", "content": "x" * 500}},
        # m1: placeholder output on the thinking line, final on the tool_use line, tool_use repeated.
        assistant("m1", usage(50000, 0, 8, cw_1h=50000), "2026-11-01T10:00:00.250Z", [thinking()]),
        assistant("m1", usage(50000, 0, 189, cw_1h=50000), "2026-11-01T10:00:02.000Z",
                  [tool_use("t1", "PowerShell", {"command": CMD})]),
        assistant("m1", usage(50000, 0, 189, cw_1h=50000), "2026-11-01T10:00:02.100Z",
                  [tool_use("t1", "PowerShell", {"command": CMD})]),
        tool_result("t1", "Exit code 1\n> test\nError: expected 3 got 4\nExit code 1", error=True),
        # Another entry type carrying the same result (and the word "user") is not a tool result.
        dict(tool_result("t1", "Exit code 1\nError: expected 3 got 4", error=True), type="progress"),
        assistant("m2", usage(1000, 50000, 10), "2026-11-01T10:01:00.000Z",
                  [tool_use("t2", "PowerShell", {"command": CMD})]),
        tool_result("t2", "Exit code 1\nError: expected 3 got 5", error=True),
        assistant("m3", usage(1000, 50000, 10), "2026-11-01T10:02:00.000Z",
                  [tool_use("t3", "PowerShell", {"command": CMD})]),
        tool_result("t3", "Exit code 1\nError: expected 3 got 6", error=True),
        # A rejected call is not a failure.
        assistant("m4", usage(1000, 50000, 10), "2026-11-01T10:03:00.000Z",
                  [tool_use("t4", "PowerShell", {"command": CMD})]),
        tool_result("t4", "The user doesn't want to proceed with this tool use.", error=True),
        # Same failure text for a different URL stays a different signature.
        assistant("m5", usage(1000, 50000, 10), "2026-11-01T10:04:00.000Z",
                  [tool_use("t5", "Bash", {"command": "curl https://a.example.com/x"}),
                   tool_use("t6", "Bash", {"command": "curl https://b.example.com/x"})]),
        tool_result("t5", "Exit code 7\ncurl: (7) Failed to connect", error=True),
        tool_result("t6", "Exit code 7\ncurl: (7) Failed to connect", error=True),
        # Odd lines must not abort the file.
        '["assistant"]',
        '{"type":"assistant","message":"assistant"}',
        '{"type":"assistant","message":{"id":"m9","usage":[1,2]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t5","content":[{"type":"text","text":null}]}]}}',
        '{"type":"assistant","timestamp":12,"message":{"id":"m6","usage":{"output_tokens":"7"},"content":[]}}',
        '{"type":"assistant","message":{"id":"m8", truncated',
        '{"type":"assistant","x":' + "[" * 200000 + "]" * 200000 + "}",
        # Rebuild after a long pause.
        assistant("m7", usage(40000, 0, 10), "2026-11-01T11:34:00.000Z",
                  [tool_use("t7", "Read", {"file_path": "a.py"})]),
        tool_result("t7", "abc"),
        # Rebuild 90 minutes after a request with a known time.
        assistant("m10", usage(40000, 0, 10), "2026-11-01T13:04:00.000Z",
                  [tool_use("t8", "PowerShell", {"command": CMD})]),
        tool_result("t8", "The user doesn't want to take this action right now. STOP", error=True),
        assistant("m11", usage(1000, 40000, 10), "2026-11-01T13:05:00.000Z",
                  [tool_use("t9", "PowerShell", {"command": CMD}), tool_use("t10", "PowerShell", {"command": CMD})]),
        tool_result("t9", "Permission to use PowerShell denied by plugin", error=True),
        tool_result("t10", "Permission for this action was denied by the auto mode classifier.", error=True),
    ]
    main_path = os.path.join(root, "proj", "s1.jsonl")
    write_jsonl(main_path, main)
    sub_path = os.path.join(root, "proj", "s1", "subagents", "agent-1.jsonl")
    write_jsonl(sub_path, [
        {"type": "api-request-shape", "shape": {
            "system": [{"type": "text", "text": "y" * 100}],
            "tools": [{"name": "Artifact", "description": "z" * 1000}, {"name": "Read"}]}},
        {"type": "attachment", "attachment": {"type": "instructions", "content": "c" * 300}},
        assistant("a1", usage(1000, 50000, 10), "2026-11-01T10:05:00.000Z"),
        {"type": "attachment", "attachment": {"type": "late", "content": "after first reply"}},
    ])
    with open(sub_path[:-len(".jsonl")] + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"agentType": "ab-lean", "model": "sonnet", "description": "AB review task-1 B1"}, fh)
    # A subagent whose meta file is missing is still a subagent.
    write_jsonl(os.path.join(root, "proj", "s1", "subagents", "agent-2.jsonl"),
                [assistant("a2", usage(1000, 0, 10), "2026-11-01T10:06:00.000Z")])
    return main_path, sub_path


NOISE = [
    ("node:test summary and the error object's name line are skipped",
     "Exit code 1\nnot ok 1 - per page: size change\n  error: 'perPageUrl is not a function'\n  name: 'TypeError'\n"
     "# tests 1\n# pass 0\n# fail 1", "error: 'perPageUrl is not a function'"),
    ("a Node error object's properties, its stack and the Node.js version are skipped",
     "Error: ENOENT: no such file or directory, open 'X:\\Temp\\x\\out.json'\n    at Object.openSync (node:fs:573:18)\n"
     "    at node:internal/main/eval_string:74:3 {\n  errno: -4058,\n  code: 'ENOENT',\n  syscall: 'open',\n"
     "  path: 'X:\\\\Temp\\\\x\\\\out.json'\n}\n\nNode.js v22.22.3", "Error: ENOENT: no such file or directory, open '<path>'"),
    ("a line of masked paths only is not a message",
     "Exit code 1\nCopy failed for two files.\nX:\\sites\\a.txt -> X:\\sites\\b.txt", "Copy failed for two files."),
    ("a runner's checks summary and exit line are skipped",
     "Exit code 1\nFAIL: shipping: 400 € → every rate free\n15 checks, 1 failed\nexit 1",
     "FAIL: shipping: <n> € → every rate free"),
    ("a bare number, False and a brace are skipped", "Exit code 1\nSettings saved wrongly\n0\nFalse\n}",
     "Settings saved wrongly"),
    ("a PowerShell message wrapped mid-sentence is joined",
     "Exit code 1\nInvoke-Expression: Der Variablenverweis ist ungültig. Verwenden Sie zur Abgrenzung\n"
     "des Namens ${}.",
     "Invoke-Expression: Der Variablenverweis ist ungültig. Verwenden Sie zur Abgrenzung des Namens ${}."),
    ("cmd.exe's two-line error is joined",
     "Exit code 255\n'Engine' is not recognized as an internal or external command,\noperable program or batch file.",
     "'Engine' is not recognized as an internal or external command, operable program or batch file."),
    ("PowerShell's generic second sentence is joined to its error",
     "Exit code 1\ntail: The term 'tail' is not recognized as a name of a cmdlet.\n"
     "Check the spelling of the name, or if a path was included, verify that the path is correct and try again.",
     "tail: The term 'tail' is not recognized as a name of a cmdlet. Check the spelling of the name, or if a path "
     "was included, verify that the path is correct and try again."),
    ("an error line of its own is not joined to the line before",
     "Exit code 2\nbuilding the target now\nmake: *** No rule to make target 'once'.",
     "make: *** No rule to make target 'once'."),
    ("output with no meaningful line keeps its last line", "Exit code 1\n0", "<n>"),
    ("an exit code alone stays the line", "Exit code 1", "Exit code <n>"),
    ("no output gives an empty line", "", ""),
]


def check_error_lines():
    for label, output, expected in NOISE:
        check(f"error line: {label}", ta.error_line("PowerShell", output), expected)


def check_masks():
    for label, text, expected in (
            ("an identifier without digits stays", "ERR_PNPM_OUTDATED_LOCKFILE in mcp__ccd_turn__update_plan",
             "ERR_PNPM_OUTDATED_LOCKFILE in mcp__ccd_turn__update_plan"),
            ("a long number is a number, not a token", "order 123456789012345678901234 failed", "order <n> failed"),
            ("a package version is no e-mail address", "npm error peer eslint@9.1.0 from x",
             "npm error peer eslint@<n>.<n>.<n> from x")):
        check(f"mask: {label}", ta.mask(text), expected)
    for label, text in (
            ("an --option with digits stays", "node --experimental-loader2-hooks-v8 x"),
            ("a lowercase hyphenated name with digits stays", "cd example-app-23c3-staging"),
            ("hyphenated words stay", "plugins superpowers-marketplace-episodic-memory")):
        check(f"scrub: {label}", ta.scrub(text), text)
    check("scrub: a 40-character hex secret is masked", ta.scrub("--token " + "a3f9" * 10), "--token <token>")


def cli(script, env, *args):
    run = subprocess.run([sys.executable, "-I", script, *args], capture_output=True, text=True,
                         encoding="utf-8", env=env)
    return run.returncode, run.stdout


def check_default_root(root):
    """Without --root the transcripts are `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`; and the
    commands other than `archive` run with token_audit.py alone (no lessons_lint.py beside it)."""
    script = os.path.join(os.path.dirname(HERE), "token_audit.py")
    solo = os.path.join(root, "solo")
    os.makedirs(solo)
    shutil.copy(script, solo)
    script = os.path.join(solo, "token_audit.py")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    config, home, empty_home = (os.path.join(root, n) for n in ("config", "home", "empty-home"))
    build(os.path.join(config, "projects"))
    build(os.path.join(home, ".claude", "projects"))
    os.makedirs(empty_home)
    runs = ("runs", "--match", "ab review", "--days", "3650")
    found = "ab-lean|sonnet"
    code, out = cli(script, dict(env, CLAUDE_CONFIG_DIR=config, HOME=empty_home, USERPROFILE=empty_home), *runs)
    check("default root: CLAUDE_CONFIG_DIR/projects is read", (code, found in out), (0, True))
    code, out = cli(script, dict(env, HOME=home, USERPROFILE=home), *runs)
    check("default root: without CLAUDE_CONFIG_DIR ~/.claude/projects is read", (code, found in out), (0, True))
    code, out = cli(script, dict(env, CLAUDE_CONFIG_DIR="", HOME=home, USERPROFILE=home), *runs)
    check("default root: an empty CLAUDE_CONFIG_DIR means ~/.claude", (code, found in out), (0, True))
    code, out = cli(script, dict(env, HOME=empty_home, USERPROFILE=empty_home), *runs)
    check("default root: positive control, a home with no transcripts finds no run", (code, found in out),
          (0, False))
    code, out = cli(script, dict(env, CLAUDE_CONFIG_DIR=empty_home, HOME=home, USERPROFILE=home), *runs)
    check("default root: CLAUDE_CONFIG_DIR wins over ~/.claude", (code, found in out), (0, False))
    code, out = cli(script, dict(env, CLAUDE_CONFIG_DIR=empty_home), "--root", os.path.join(config, "projects"), *runs)
    check("default root: --root wins over CLAUDE_CONFIG_DIR", (code, found in out), (0, True))
    code, out = cli(script, dict(env, CLAUDE_CONFIG_DIR=config), "report", "--days", "3650")
    check("report runs without lessons_lint.py beside the script",
          (code, "Error: expected <n> got <n>" in out), (0, True))
    gone = os.path.join(root, "no-such-folder")
    report_out = os.path.join(root, "report.md")
    for label, folder, said in (("a missing --root", gone, "transcripts folder not found"),
                                ("an empty --root", empty_home, "read 0 transcripts")):
        run = subprocess.run([sys.executable, "-I", script, "--root", folder, "report", "--out", report_out],
                             capture_output=True, text=True, encoding="utf-8", env=env)
        check(f"report with {label}: exit 2 naming the cause, no report",
              (run.returncode, said in run.stderr, "main sessions" in run.stdout, os.path.exists(report_out)),
              (2, True, False, False))


def check_argparse_error_is_utf8():
    """An argparse error is written before parse_args returns, so stderr is switched to UTF-8 before it. The
    value is outside cp1250 (the pipe's default here) and the child gets no PYTHON* encoding variables."""
    script = os.path.join(os.path.dirname(HERE), "token_audit.py")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    value = "日本語"
    run = subprocess.run([sys.executable, "-I", script, "report", "--days", value],
                         capture_output=True, env=env)
    check("argparse error: exit 2", run.returncode, 2)
    check("argparse error: the non-ASCII value reaches a pipe as UTF-8",
          ("invalid int value: '%s'" % value).encode("utf-8") in run.stderr, True)


HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
T0 = calendar.timegm((2026, 11, 1, 10, 0, 0))
REMINDER = ("<system-reminder>\nYour final report is delivered through SubagentHandback: when your work is "
            "complete, call SubagentHandback({message: <your full report>}).\n</system-reminder>")


def iso(offset):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(T0 + offset))


def user_entry(content, offset, meta=False):
    entry = {"parentUuid": None, "isSidechain": True, "type": "user", "timestamp": iso(offset),
             "message": {"role": "user", "content": content}}
    if meta:
        entry["isMeta"] = True
    return entry


def msg(mid, model, u, offset, blocks=()):
    entry = assistant(mid, u, iso(offset), blocks)
    entry["message"]["model"] = model
    return entry


def agent_lines(first, start, body, end):
    """A subagent transcript in the measured order: the brief (a string, or a list of blocks), the harness
    reminder as a later user entry, the other entry types, the assistant lines, a closing tool result."""
    return [user_entry(first, start), user_entry(REMINDER, start, meta=True),
            {"type": "attachment", "timestamp": iso(start), "attachment": {"type": "date", "date": "2026-11-01"}},
            {"type": "api-request-shape", "timestamp": iso(start), "shape": {"system": [], "tools": []}},
            {"type": "api-request-blob", "hash": "h", "message": {"role": "user", "content": []}},
            {"type": "api-request", "timestamp": iso(start), "params": {"model": HAIKU}},
            *body,
            user_entry([{"type": "tool_result", "tool_use_id": "tx", "content": [{"type": "text", "text": "ok"}]}],
                       end)]


def write_agent(root, session, stem, lines, meta=None):
    path = os.path.join(root, "proj", session, "subagents", stem + ".jsonl")
    write_jsonl(path, lines)
    if meta is not None:
        with open(path[:-len(".jsonl")] + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
    return path


def cli_err(*args):
    run = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, text=True, encoding="utf-8")
    return run.returncode, run.stdout, run.stderr


def job_cli(root, *args):
    return cli_err("--root", root, "job", *args, "--days", "3650")


def table_rows(out):
    """{transcript: fields} of the table rows; a row has 12 whitespace-separated fields."""
    rows = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 12 and fields[-1].startswith("agent-"):
            rows[fields[-1]] = fields
    return rows


def stems(rows):
    return sorted(os.path.basename(r["path"])[:-len(".jsonl")] for r in rows)


class temp_root:
    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="token-audit-job-")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


def check_job_known_totals():
    with temp_root() as root:
        # Agent A (haiku): m1 is streamed, first line output 1, last line 69; hand-computed with
        # $/MTok 0.10 in, 0.01 read, 0.125 write 5m, 0.20 write 1h, 0.50 out:
        # in 4, read 1000, w5m 600, w1h 400, out 100 -> 2154 effective, USD 0.0002154.
        write_agent(root, "s-tot", "agent-aaa", agent_lines("Job: TOT-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(1000, 0, 1, cw_1h=400), 2, [thinking()]),
            msg("m1", HAIKU, usage(1000, 0, 69, cw_1h=400), 5,
                [tool_use("t1", "mcp__github__get_issue", {"issue_number": 1})]),
            msg("m2", HAIKU, usage(0, 1000, 31), 8)], 12),
            {"agentType": "rigger-core:worker", "model": "haiku", "description": "intake"})
        # Agent B (sonnet 5.5; $/MTok 2 in, 0.20 read, 2.5 write 5m, 10 out):
        # in 2, read 5000, w5m 2000, out 200 -> 4002 effective, USD 0.008004.
        write_agent(root, "s-tot", "agent-bbb", agent_lines("Job: TOT-1\nTicket: ABC-2", 3, [
            msg("m3", SONNET, usage(2000, 5000, 200), 10)], 20))
        # Another job in the same session is not part of the totals.
        write_agent(root, "s-tot", "agent-ccc", agent_lines("Job: TOT-10\nTicket: ABC-3", 0, [
            msg("m4", SONNET, usage(9000, 9000, 900), 4)], 6))
        code, out, err = job_cli(root, "--job", "TOT-1")
        rows = table_rows(out)
        check("job totals: exit 0, no stderr, no INCOMPLETE", (code, err, "INCOMPLETE" in out), (0, "", False))
        check("job totals: only the two TOT-1 agents", sorted(rows), ["agent-aaa", "agent-bbb"])
        check("job totals: streamed agent, last usage wins",
              rows.get("agent-aaa"), ["rigger-core:worker|haiku", HAIKU, "2", "4", "1,000", "600", "400", "100",
                                      "2,154", "0.000215", "12s", "agent-aaa"])
        check("job totals: second agent (no meta file)",
              rows.get("agent-bbb"), ["?|inherit", SONNET, "1", "2", "5,000", "2,000", "0", "200", "4,002",
                                      "0.008004", "17s", "agent-bbb"])
        total = [line.split() for line in out.splitlines() if line.startswith("total ")]
        check("job totals: total row", total, [["total", "-", "3", "6", "6,000", "2,600", "400", "300", "6,156",
                                                "0.008219", "-", "-"]])
        check("job totals: wall is first to last entry of all agents",
              [line for line in out.splitlines() if line.startswith("wall ")],
              ["wall 20s 2026-11-01T10:00:00Z -> 2026-11-01T10:00:20Z"])
        check("job totals: price provenance line",
              "prices: as of 2026-10-06 (claude-api skill, CLI 2.1.316)" in out.splitlines(), True)
        check("job totals: no price note under the prompt limit", "note:" in out, False)
        check("job totals: the window is on the report", "window: last 3650 days" in out.splitlines(), True)


def check_job_marker_is_exact():
    with temp_root() as root:
        def put(stem, first):
            write_agent(root, "s-mark", stem, agent_lines(first, 0, [msg("m", HAIKU, usage(0, 0, 1), 1)], 2))
        put("agent-yes-lf", "Job: ABC-1\nTicket: ABC-1")
        put("agent-yes-crlf", "Job: ABC-1\r\nTicket: ABC-1")
        put("agent-yes-alone", "Job: ABC-1")
        put("agent-no-prefix", "Job: ABC-10\nTicket: ABC-10")
        put("agent-no-inline", "Please take care of it. Job: ABC-1\nTicket: ABC-1")
        put("agent-no-line2", "Ticket: ABC-1\nJob: ABC-1")
        put("agent-no-trailing", "Job: ABC-1 and more")
        put("agent-no-space", " Job: ABC-1")
        put("agent-no-case", "job: ABC-1")
        put("agent-no-quoted", "> Job: ABC-1\n> Ticket: ABC-1")
        write_agent(root, "s-mark", "agent-no-second-message", [
            user_entry("Hello", 0), user_entry("Job: ABC-1\nTicket: ABC-1", 1), msg("m", HAIKU, usage(0, 0, 1), 2)])
        # A first user line that cannot be read does not hand the marker to the next user entry.
        write_agent(root, "s-mark", "agent-no-corrupt-first", [
            '{"type":"user","message":{"role":"user","content":"Job: ABC-1', user_entry("Job: ABC-1\nTicket: ABC-1", 1),
            msg("m", HAIKU, usage(0, 0, 1), 2)])
        check("marker: line 1 of the first user entry, whole line only",
              stems(ta.job_rows(root, "ABC-1", 3650)), ["agent-yes-alone", "agent-yes-crlf", "agent-yes-lf"])
        check("marker: the longer id is its own job",
              stems(ta.job_rows(root, "ABC-10", 3650)), ["agent-no-prefix"])
        check("first_user_text: the first user entry, not a later one",
              ta.first_user_text(os.path.join(root, "proj", "s-mark", "subagents", "agent-no-second-message.jsonl")),
              "Hello")


def check_job_marker_in_list_content():
    with temp_root() as root:
        def put(stem, blocks):
            write_agent(root, "s-list", stem, agent_lines(
                [{"type": "text", "text": t} for t in blocks], 0, [msg("m", HAIKU, usage(0, 0, 1), 1)], 2))
        put("agent-yes-list", ["Job: ABC-1\nTicket: ABC-1"])
        put("agent-no-list-second-block", ["Intro", "Job: ABC-1"])
        put("agent-no-list-line2", ["Ticket: ABC-1\nJob: ABC-1"])
        check("marker: a list of blocks counts by its text",
              stems(ta.job_rows(root, "ABC-1", 3650)), ["agent-yes-list"])
        check("first_user_text: list content is its text blocks",
              ta.first_user_text(os.path.join(root, "proj", "s-list", "subagents", "agent-yes-list.jsonl")),
              "Job: ABC-1\nTicket: ABC-1")


def check_first_user_text_shapes():
    with temp_root() as root:
        def read(stem, lines):
            return ta.first_user_text(write_agent(root, "s-shape", stem, lines))
        hello = user_entry("Hello", 1)
        check("first_user_text: a JSON line that is no object is skipped", read("agent-list", ['["user"]', hello]), "Hello")
        check("first_user_text: an entry of another type is skipped",
              read("agent-type", [{"type": "progress", "message": {"role": "user", "content": "Job: ABC-1"}}, hello]),
              "Hello")
        check("first_user_text: only text blocks count",
              read("agent-blocks", [user_entry(["Job: ABC-1", {"type": "image", "text": "Job: ABC-1"},
                                                {"type": "text", "text": "Hello"}], 0)]),
              "Hello")
        check("first_user_text: an entry with no usable content is empty", read("agent-odd", [user_entry(5, 0), hello]), "")
        check("first_user_text: a torn line before the entry is unreadable, not the next entry",
              read("agent-torn", ['{"type":"user","message":{"role":"user","content":"Job: ABC-1', hello]), None)
        check("first_user_text: a line torn before its type is unreadable too",
              read("agent-early", ['{"parentUuid":null,"isSide', hello]), None)
        check("first_user_text: a line nested too deep is unreadable",
              read("agent-deep", ['{"type":"user","x":' + "[" * 200000 + "]" * 200000 + "}", hello]), None)
        check("job_marker: an unreadable first entry has no marker", ta.job_marker(None), None)


def check_job_torn_first_line():
    with temp_root() as root:
        write_agent(root, "s-torn", "agent-whole", agent_lines("Job: TORN-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 1), 1)], 2))
        # A main session is no agent of any job, torn or not.
        write_jsonl(os.path.join(root, "proj", "s-torn.jsonl"), ['{"type":"user","message":{"role":"us'])
        code, out, err = job_cli(root, "--job", "TORN-1")
        check("torn first line: positive control, the clean job exits 0", (code, "INCOMPLETE" in out), (0, False))
        for stem, first in (("agent-torn", '{"type":"user","message":{"role":"user","content":"Job: TORN-1'),
                            ("agent-early", '{"parentUuid":null,"isSide')):
            path = write_agent(root, "s-torn", stem, [first, msg("m2", HAIKU, usage(0, 0, 1), 1)])
            code, out, err = job_cli(root, "--job", "TORN-1")
            rows = table_rows(out)
            check(f"torn first line {stem}: no row for it, the reason line, exit 1",
                  (code, "agent-whole" in rows, stem in rows,
                   f"INCOMPLETE: first user line of {stem}.jsonl cannot be read, so its job is unknown" in out.splitlines()),
                  (1, True, False, True))
            os.remove(path)


def check_job_torn_agent_outside_window():
    with temp_root() as root:
        write_agent(root, "s-tornwin", "agent-whole", agent_lines("Job: TW-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 1), 1)], 2))
        old = write_agent(root, "s-tornwin", "agent-torn-old", [
            '{"type":"user","message":{"role":"user","content":"Job: TW-1', msg("m2", HAIKU, usage(0, 0, 1), 1)])
        stamp = time.time() - 10 * 86400
        os.utime(old, (stamp, stamp))
        reason = "INCOMPLETE: first user line of agent-torn-old.jsonl cannot be read, so its job is unknown"
        code, out, err = cli_err("--root", root, "job", "--job", "TW-1", "--days", "30")
        check("torn agent window: positive control, 30 days reach it, the reason line, exit 1",
              (code, reason in out.splitlines()), (1, True))
        code, out, err = cli_err("--root", root, "job", "--job", "TW-1", "--days", "7")
        check("torn agent window: 7 days cut it, so it does not mark the job INCOMPLETE",
              (code, "INCOMPLETE" in out, sorted(table_rows(out)), "window: last 7 days" in out.splitlines()),
              (0, False, ["agent-whole"], True))


def check_job_ignores_main_transcripts():
    with temp_root() as root:
        write_jsonl(os.path.join(root, "proj", "s-main.jsonl"), [
            user_entry("Job: MAIN-1\nTicket: ABC-1", 0), msg("mm", SONNET, usage(5000, 5000, 500), 1)])
        write_agent(root, "s-main", "agent-sub", agent_lines("Job: MAIN-1\nTicket: ABC-1", 0, [
            msg("ms", HAIKU, usage(0, 0, 10), 1)], 2))
        check("main transcript with the marker is no subagent row",
              stems(ta.job_rows(root, "MAIN-1", 3650)), ["agent-sub"])
        code, out, err = job_cli(root, "--job", "MAIN-1")
        check("main transcript: its tokens are not in the total",
              (code, [line.split()[2:4] for line in out.splitlines() if line.startswith("total ")]),
              (0, [["1", "2"]]))
        os.remove(os.path.join(root, "proj", "s-main", "subagents", "agent-sub.jsonl"))
        check("main transcript alone gives no row", ta.job_rows(root, "MAIN-1", 3650), [])


def check_job_no_transcript_exits_1():
    with temp_root() as root:
        write_agent(root, "s-none", "agent-other", agent_lines("Job: OTHER-1\nTicket: ABC-1", 0, [
            msg("m", HAIKU, usage(0, 0, 10), 1)], 2))
        code, out, err = job_cli(root, "--job", "OTHER-1")
        check("no transcript: positive control, the existing job exits 0", code, 0)
        code, out, err = job_cli(root, "--job", "ABC-404")
        check("no transcript: exit 1, the reason on stderr, no table, no Traceback",
              (code, out, err, "Traceback" in err),
              (1, "", f'job: no subagent transcript starts with "Job: ABC-404" under {root} in the last 3650 days\n',
               False))
        gone = os.path.join(root, "no-such-folder")
        code, out, err = job_cli(gone, "--job", "OTHER-1")
        check("missing root: exit 1, the reason on stderr, no Traceback",
              (code, out, err, "Traceback" in err), (1, "", f"job: transcripts folder not found: {gone}\n", False))


def check_job_unknown_session_exits_1():
    with temp_root() as root:
        write_agent(root, "s-ok", "agent-one", agent_lines("Job: SES-1\nTicket: ABC-1", 0, [
            msg("m", HAIKU, usage(0, 0, 10), 1)], 2))
        write_jsonl(os.path.join(root, "proj", "s-main-only.jsonl"), [user_entry("hello", 0)])
        for session in ("s-ok", "s-main-only"):
            code, out, err = job_cli(root, "--job", "SES-1", "--session", session)
            check(f"session {session}: positive control, a known session exits 0", (code, err), (0, ""))
        code, out, err = job_cli(root, "--job", "SES-1", "--session", "no-such-session")
        check("unknown session: exit 1, the reason on stderr, no table, no Traceback",
              (code, out, err, "Traceback" in err),
              (1, "", f"job: no transcript of session no-such-session under {root} in the last 3650 days\n", False))


def check_job_id_refuses_whitespace():
    with temp_root() as root:
        for value in ("", "ABC 1", "ABC-1\n"):
            code, out, err = job_cli(root, "--job", value)
            check(f"job id {value!r}: usage error 2 naming the rule, no Traceback",
                  (code, "a job id is a non-empty string without whitespace" in err, "Traceback" in err),
                  (2, True, False))


def check_job_incomplete_exits_1():
    with temp_root() as root:
        write_agent(root, "s-inc", "agent-unk", agent_lines("Job: UNK-1\nTicket: ABC-1", 0, [
            msg("m1", "claude-future-9", usage(0, 0, 10), 1)], 2))
        code, out, err = job_cli(root, "--job", "UNK-1")
        check("incomplete, unknown model: the table is printed, the reason line, exit 1",
              (code, "# Job UNK-1" in out,
               "INCOMPLETE: model claude-future-9 has 12 tokens and no price in prices.json" in out.splitlines(),
               table_rows(out).get("agent-unk", [""] * 10)[9]),
              (1, True, True, "0.000000+?"))
        check("incomplete: the window is on this report too", "window: last 3650 days" in out.splitlines(), True)
        write_agent(root, "s-inc", "agent-unk2", agent_lines("Job: UNK-2\nTicket: ABC-1", 0, [
            msg("m2", "claude-future-9", usage(0, 0, 10), 1)], 2))
        write_agent(root, "s-inc", "agent-priced", agent_lines("Job: UNK-2\nTicket: ABC-1", 5, [
            msg("m3", HAIKU, usage(0, 0, 10), 6)], 7))
        code, out, err = job_cli(root, "--job", "UNK-2")
        check("incomplete: a later priced row keeps the +? on the total, and has none itself",
              (table_rows(out).get("agent-priced", [""] * 10)[9],
               [line.split()[9] for line in out.splitlines() if line.startswith("total ")]),
              ("0.000005", ["0.000005+?"]))
        nameless = msg("m1", HAIKU, usage(0, 0, 10), 1)
        del nameless["message"]["model"]
        write_agent(root, "s-inc", "agent-nameless", agent_lines("Job: NOM-1\nTicket: ABC-1", 0, [nameless], 2))
        code, out, err = job_cli(root, "--job", "NOM-1")
        check("incomplete, usage with no model: tokens are not priced as any model",
              (code, "INCOMPLETE: model ? has 12 tokens and no price in prices.json" in out.splitlines()), (1, True))
        write_agent(root, "s-inc", "agent-bad", agent_lines("Job: BAD-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1), '{"type":"assistant","message":{"id":"m8", truncated'], 2))
        code, out, err = job_cli(root, "--job", "BAD-1")
        check("incomplete, bad line: the table is printed, the reason line, exit 1",
              (code, "agent-bad" in table_rows(out),
               "INCOMPLETE: 1 unparseable line(s) in agent-bad.jsonl" in out.splitlines()),
              (1, True, True))
        write_agent(root, "s-inc", "agent-fail", agent_lines("Job: FAIL-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1)], 2))
        real = ta._parse_into

        def exploding(stats, path):
            raise OSError("boom")
        ta._parse_into = exploding
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                code = ta.run_job(argparse.Namespace(root=root, job="FAIL-1", session=None, days=3650))
        finally:
            ta._parse_into = real
        check("incomplete, failed transcript: the table is printed, the reason line, exit 1",
              (code, err_buf.getvalue(), "agent-fail" in table_rows(out_buf.getvalue()),
               "INCOMPLETE: agent-fail.jsonl unreadable: OSError: boom" in out_buf.getvalue().splitlines()),
              (1, "", True, True))


PAST_LIMIT = "INCOMPLETE: model claude-haiku-5-5 has a {:,}-token prompt past its 100,000-token price limit"


def check_job_prompt_limit():
    with temp_root() as root:
        def job(name, *agents):
            for number, body in enumerate(agents):
                write_agent(root, "s-" + name, f"agent-{name}-{number}", agent_lines(f"Job: {name}\nTicket: ABC-1", number, body, 9))
            code, out, err = job_cli(root, "--job", name)
            return code, out

        # usage() adds 2 input tokens to the prompt.
        code, out = job("AT-1", [msg("m1", HAIKU, usage(0, 99998, 10), 1)])
        check("prompt limit: exactly at the limit is priced, no note",
              (code, "INCOMPLETE" in out, "note:" in out), (0, False, False))
        code, out = job("PAST-1", [msg("m2", HAIKU, usage(0, 99999, 10), 1)])
        row = table_rows(out).get("agent-PAST-1-0", [""] * 12)
        check("prompt limit: one past it is INCOMPLETE, naming the model and the size",
              (code, PAST_LIMIT.format(100001) in out.splitlines()), (1, True))
        check("prompt limit: the low rate stays, flagged on the row and the total",
              (row[9], [line.split()[9] for line in out.splitlines() if line.startswith("total ")]),
              ("0.001005+?", ["0.001005+?"]))
        check("prompt limit: the note appears only now", "note: Haiku 5.5 rates hold up to 100K" in out, True)
        for label, tokens in (("input", {"input_tokens": 100001}), ("cache read", {"cache_read_input_tokens": 100001}),
                              ("5 min write", {"cache_creation_input_tokens": 100001}),
                              ("1 h write", {"cache_creation_input_tokens": 100001,
                                             "cache_creation": {"ephemeral_1h_input_tokens": 100001}})):
            code, out = job("CLS-" + label[:2].strip().upper(), [msg("m3", HAIKU, tokens, 1)])
            check(f"prompt limit: {label} tokens count", (code, PAST_LIMIT.format(100001) in out.splitlines()), (1, True))
        code, out = job("SUM-1", [msg("m4", HAIKU, usage(0, 60000, 10), 1), msg("m5", HAIKU, usage(0, 60000, 10), 2)])
        check("prompt limit: one request's prompt, not the sum of requests", (code, "INCOMPLETE" in out), (0, False))
        code, out = job("MOD-1", [msg("m6", HAIKU, usage(0, 60000, 10), 1), msg("m7", SONNET, usage(0, 150000, 10), 2)])
        check("prompt limit: per model, and a model with no limit has none", (code, "INCOMPLETE" in out), (0, False))
        code, out = job("MAX-1", [msg("m8", HAIKU, usage(0, 150000, 10), 1)], [msg("m9", HAIKU, usage(0, 120000, 10), 1)])
        check("prompt limit: the largest prompt over the agents is named",
              (code, [line for line in out.splitlines() if line.startswith("INCOMPLETE")]),
              (1, [PAST_LIMIT.format(150002)]))
        code, out = job("LAST-1", [msg("m10", HAIKU, usage(0, 150000, 10), 1), msg("m11", HAIKU, usage(0, 1000, 10), 2)])
        check("prompt limit: a smaller request after the largest does not clear it, one agent",
              (code, [line for line in out.splitlines() if line.startswith("INCOMPLETE")]),
              (1, [PAST_LIMIT.format(150002)]))


def check_job_window_cuts_older_agents():
    with temp_root() as root:
        write_agent(root, "s-win", "agent-new", agent_lines("Job: WIN-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 1), 1)], 2))
        old = write_agent(root, "s-win", "agent-old", agent_lines("Job: WIN-1\nTicket: ABC-1", 0, [
            msg("m2", HAIKU, usage(0, 0, 1), 1)], 2))
        stamp = time.time() - 10 * 86400
        os.utime(old, (stamp, stamp))
        code, out, err = cli_err("--root", root, "job", "--job", "WIN-1", "--days", "30")
        check("window: 30 days reach the older agent", (code, sorted(table_rows(out))), (0, ["agent-new", "agent-old"]))
        code, out, err = cli_err("--root", root, "job", "--job", "WIN-1", "--days", "7")
        check("window: 7 days cut it, and the report says how far it looked",
              (code, sorted(table_rows(out)), "window: last 7 days" in out.splitlines()), (0, ["agent-new"], True))


def check_job_rows_are_sorted():
    with temp_root() as root:
        def put(stem, lines):
            write_agent(root, "s-ord", stem, lines)
        put("agent-z-early", agent_lines("Job: ORD-1\nTicket: ABC-1", 0, [msg("m1", HAIKU, usage(0, 0, 1), 1)], 2))
        put("agent-b-tie", agent_lines("Job: ORD-1\nTicket: ABC-1", 5, [msg("m2", HAIKU, usage(0, 0, 1), 6)], 7))
        put("agent-c-tie", agent_lines("Job: ORD-1\nTicket: ABC-1", 5, [msg("m3", HAIKU, usage(0, 0, 1), 6)], 7))
        put("agent-a-undated", [{"type": "user", "message": {"role": "user", "content": "Job: ORD-1"}},
                                {"type": "assistant", "message": {"id": "m4", "model": HAIKU, "content": [],
                                                                  "usage": usage(0, 0, 1)}}])
        order = ["agent-z-early", "agent-b-tie", "agent-c-tie", "agent-a-undated"]
        by_stem = {os.path.basename(r["path"])[:-len(".jsonl")]: r for r in ta.job_rows(root, "ORD-1", 3650)}
        given = [by_stem[stem] for stem in reversed(order)]
        text, _ = ta.build_job(given, "ORD-1", ta.load_prices(), 3650)
        check("rows: by first entry, ties by path, undated last", list(table_rows(text)), order)


def check_job_zero_token_synthetic_row_is_ignored():
    with temp_root() as root:
        synthetic = msg("syn", "<synthetic>", {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                               "cache_read_input_tokens": 0}, 2,
                        [{"type": "text", "text": "No response requested."}])
        write_agent(root, "s-syn", "agent-syn", agent_lines("Job: SYN-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1), synthetic], 3))
        code, out, err = job_cli(root, "--job", "SYN-1")
        row = table_rows(out).get("agent-syn", [""] * 12)
        check("synthetic zero-token row: exit 0, no INCOMPLETE, the model column holds the real model only",
              (code, "INCOMPLETE" in out, row[1]), (0, False, HAIKU))
        check("synthetic zero-token row: not in by_model",
              sorted(ta.job_rows(root, "SYN-1", 3650)[0]["by_model"]), [HAIKU])
        write_agent(root, "s-syn", "agent-syn2", agent_lines("Job: SYN-2\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1),
            msg("syn", "<synthetic>", {"output_tokens": 5}, 2)], 3))
        code, out, err = job_cli(root, "--job", "SYN-2")
        check("synthetic row with tokens: positive control, INCOMPLETE and exit 1",
              (code, "INCOMPLETE: model <synthetic> has 5 tokens and no price in prices.json" in out.splitlines()),
              (1, True))


def check_job_wall():
    with temp_root() as root:
        for stem, start, end in (("agent-w1", 0, 10), ("agent-w2", 5, 20), ("agent-w3", 100, 110)):
            write_agent(root, "s-wall", stem, agent_lines("Job: WALL-1\nTicket: ABC-1", start, [
                msg("m" + stem, HAIKU, usage(0, 0, 1), end - 2)], end))
        code, out, err = job_cli(root, "--job", "WALL-1")
        check("wall: first to last entry over all agents, not a sum of durations",
              (code, [line for line in out.splitlines() if line.startswith("wall ")],
               [table_rows(out)[s][10] for s in ("agent-w1", "agent-w2", "agent-w3")]),
              (0, ["wall 110s 2026-11-01T10:00:00Z -> 2026-11-01T10:01:50Z"], ["10s", "15s", "10s"]))


def check_session_reports_unattributed_agents():
    with temp_root() as root:
        write_agent(root, "s-un", "agent-att", agent_lines("Job: UN-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1)], 2))
        write_agent(root, "s-un", "agent-none", agent_lines("Take a look at the ticket", 0, [
            msg("m2", SONNET, usage(2000, 5000, 200), 1)], 2))
        write_agent(root, "s-un", "agent-else", agent_lines("Job: UN-2\nTicket: ABC-2", 0, [
            msg("m3", SONNET, usage(9000, 9000, 900), 1)], 2))
        write_agent(root, "s-elsewhere", "agent-none2", agent_lines("No marker either", 0, [
            msg("m4", SONNET, usage(9000, 9000, 900), 1)], 2))
        write_agent(root, "s-clean", "agent-clean", agent_lines("Job: UN-3\nTicket: ABC-3", 0, [
            msg("m5", HAIKU, usage(0, 0, 10), 1)], 2))
        code, out, err = job_cli(root, "--job", "UN-1", "--session", "s-un")
        check("unattributed: the session's agents without any marker are named with their cost",
              (code, [line for line in out.splitlines() if line.startswith("unattributed")]),
              (0, ["unattributed: 1 agents, 4,002 effective tokens"]))
        code, out, err = job_cli(root, "--job", "UN-3", "--session", "s-clean")
        check("unattributed: a clean session says 0 explicitly",
              [line for line in out.splitlines() if line.startswith("unattributed")],
              ["unattributed: 0 agents, 0 effective tokens"])
        code, out, err = job_cli(root, "--job", "UN-1")
        check("unattributed: without --session no such line", "unattributed" in out, False)


def check_session_unattributed_bad_transcripts():
    with temp_root() as root:
        write_agent(root, "s-bad", "agent-att", agent_lines("Job: UB-1\nTicket: ABC-1", 0, [
            msg("m1", HAIKU, usage(0, 0, 10), 1)], 2))
        plain = write_agent(root, "s-bad", "agent-plain", agent_lines("Take a look at the ticket", 0, [
            msg("m2", SONNET, usage(0, 0, 10), 1)], 2))
        code, out, err = job_cli(root, "--job", "UB-1", "--session", "s-bad")
        check("unattributed bad lines: positive control, a clean agent exits 0", (code, "INCOMPLETE" in out), (0, False))
        write_agent(root, "s-bad", "agent-plain", agent_lines("Take a look at the ticket", 0, [
            msg("m2", SONNET, usage(0, 0, 10), 1), '{"type":"assistant","message":{"id":"m8", truncated'], 2))
        code, out, err = job_cli(root, "--job", "UB-1", "--session", "s-bad")
        check("unattributed bad lines: the reason line, exit 1",
              (code, "INCOMPLETE: 1 unparseable line(s) in agent-plain.jsonl" in out.splitlines()), (1, True))
        real = ta._parse_into

        def exploding(stats, path):
            if path == plain:
                raise OSError("boom")
            real(stats, path)
        ta._parse_into = exploding
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                code = ta.run_job(argparse.Namespace(root=root, job="UB-1", session="s-bad", days=3650))
        finally:
            ta._parse_into = real
        check("unattributed unreadable transcript: the reason line, exit 1",
              (code, "INCOMPLETE: agent-plain.jsonl unreadable: OSError: boom" in out_buf.getvalue().splitlines()),
              (1, True))


def check_prices_file_is_complete():
    path = os.path.join(os.path.dirname(HERE), "prices.json")
    with open(path, encoding="utf-8") as fh:
        shipped = json.load(fh)
    live = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-5",
            "claude-sonnet-5", "claude-haiku-4-5-20251001"]
    check("prices.json: complete", ta.price_problems(shipped), [])
    check("prices.json: non-empty, every live model priced",
          (bool(shipped.get("usd_per_mtok")), [m for m in live if m not in shipped.get("usd_per_mtok", {})]),
          (True, []))
    check("prices.json: provenance", (shipped.get("as_of"), shipped.get("source")),
          ("2026-10-06", "claude-api skill, CLI 2.1.316"))
    full = {c: 1 for c in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")}
    check("prices check: positive control, an inline model with five classes passes",
          ta.price_problems({"usd_per_mtok": {"m": full}}), [])
    check("prices check: refuses an inline {}", bool(ta.price_problems({})), True)
    check("prices check: refuses an empty usd_per_mtok", bool(ta.price_problems({"usd_per_mtok": {}})), True)
    four = {k: v for k, v in full.items() if k != "cache_write_1h"}
    check("prices check: refuses a model with four classes, naming the missing one",
          ta.price_problems({"usd_per_mtok": {"m": four}}),
          ["m: cache_write_1h is missing or not a non-negative number"])
    check("prices check: refuses a text price",
          bool(ta.price_problems({"usd_per_mtok": {"m": dict(full, input="1")}})), True)
    bad_rate = ["m: input is missing or not a non-negative number"]
    for label, value in (("a boolean", True), ("NaN", float("nan")), ("infinity", float("inf")), ("a negative", -1)):
        check(f"prices check: refuses {label} as a rate",
              ta.price_problems({"usd_per_mtok": {"m": dict(full, input=value)}}), bad_rate)
    check("prices check: refuses a model that is not an object", ta.price_problems({"usd_per_mtok": {"m": 1}}),
          ["m: not an object"])
    check("prices check: positive control, a prompt limit passes",
          ta.price_problems({"usd_per_mtok": {"m": dict(full, prompt_limit=100000)}}), [])
    for value in (None, "100000", True, -1):
        check(f"prices check: refuses prompt_limit {value!r}",
              ta.price_problems({"usd_per_mtok": {"m": dict(full, prompt_limit=value)}}),
              ["m: prompt_limit is not a non-negative number"])
    check("prices.json: only Haiku 5.5 has a prompt limit, the source's 100K",
          {m: r["prompt_limit"] for m, r in shipped["usd_per_mtok"].items() if "prompt_limit" in r},
          {"claude-haiku-5-5": 100000})
    with temp_root() as folder:
        bad = os.path.join(folder, "prices.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"usd_per_mtok": {}}')
        real = ta.PRICES_PATH
        ta.PRICES_PATH = bad
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                code = ta.run_job(argparse.Namespace(root=folder, job="ABC-1", session=None, days=3650))
        finally:
            ta.PRICES_PATH = real
        check("job with an unusable prices file: exit 1, the reason on stderr, no table",
              (code, out_buf.getvalue(), err_buf.getvalue()),
              (1, "", "job: cannot use prices: prices.json: usd_per_mtok is missing or empty\n"))


JOB_CASES = (check_job_known_totals, check_job_marker_is_exact, check_job_marker_in_list_content,
             check_first_user_text_shapes, check_job_torn_first_line, check_job_torn_agent_outside_window,
             check_job_ignores_main_transcripts, check_job_no_transcript_exits_1,
             check_job_unknown_session_exits_1, check_job_id_refuses_whitespace, check_job_incomplete_exits_1,
             check_job_prompt_limit, check_job_window_cuts_older_agents, check_job_rows_are_sorted,
             check_job_zero_token_synthetic_row_is_ignored, check_job_wall,
             check_session_reports_unattributed_agents, check_session_unattributed_bad_transcripts,
             check_prices_file_is_complete)


def run_job_cases():
    """Each case runs on its own, so one that cannot run (a missing function) is a failure, not the end."""
    for case in JOB_CASES:
        try:
            case()
        except Exception as exc:
            check(case.__name__, f"raised {type(exc).__name__}: {exc}", "ran to the end")


def main():
    check_error_lines()
    check_masks()
    check_argparse_error_is_utf8()
    default_root_dir = tempfile.mkdtemp(prefix="token-audit-default-root-")
    try:
        check_default_root(default_root_dir)
    finally:
        shutil.rmtree(default_root_dir, ignore_errors=True)
    root = tempfile.mkdtemp(prefix="token-audit-test-")
    try:
        main_path, sub_path = build(root)
        stats = ta.parse_transcript(main_path)
        check("parse did not fail", stats["failed"], None)
        check("requests: one per message id with usage", stats["requests"], 9)
        check("first prefix", stats["first_prefix"], 50002)
        check("1h cache write priced at 2.0",
              ta.cost({"cache_creation_input_tokens": 100,
                       "cache_creation": {"ephemeral_1h_input_tokens": 100}})["cache_write"], 200.0)
        check("5m cache write priced at 1.25 when no split",
              ta.cost({"cache_creation_input_tokens": 100})["cache_write"], 125.0)
        check("output uses the last streamed line, not the placeholder",
              stats["cost"]["output"], (189 + 10 * 4 + 10 * 3) * 5.0)
        # m1 (1h write, final output), m2-m5, m6 (string count prices as 0), m7, m10, m11;
        # m9 has no usable usage.
        expected_total = (2 + 50000 * 2.0 + 189 * 5) \
            + 4 * (2 + 1000 * 1.25 + 50000 * 0.1 + 10 * 5) \
            + 2 * (2 + 40000 * 1.25 + 10 * 5) \
            + (2 + 1000 * 1.25 + 40000 * 0.1 + 10 * 5)
        check("effective total", round(sum(stats["cost"].values()), 3), round(expected_total, 3))
        check("rebuild gaps: unknown after an unreadable time, 90 min after a known one",
              stats["rebuilds"], [(40000, None), (40000, 5400.0)])
        check("rejections and permission denials are not failures",
              [k for k in stats["errors"] if "want to" in k or "Permission" in k], [])
        check("mask keeps the word before a hyphenated id", ta.mask("cd tmp-a1b2c3d4 fix-abc123", 80),
              "cd tmp-<hex> fix-abc<n>")
        check("mask folds a UUID", ta.mask("run F47AC10B-58CC-4372-A567-0E02B2C3D479 failed", 80),
              "run <uuid> failed")
        check("a main file named agent-* is not a subagent",
              ta.is_subagent(os.path.join(root, "proj", "agent-x.jsonl")), False)
        check("tool_use repeated on a later line counts once", stats["tools"]["PowerShell"], 7)
        sigs = {k: v for k, v in stats["errors"].items() if k.startswith("PowerShell")}
        check("signature keeps the command and the last meaningful line",
              sigs, {"PowerShell | cd <path>; npm test <n>>&<n> => Error: expected <n> got <n>": 3})
        curl = sorted(k for k in stats["errors"] if k.startswith("Bash"))
        check("different URLs stay different signatures", len(curl), 2)
        check("odd lines counted, deep nesting included", stats["bad_lines"], 2)
        real_getmtime = ta.os.path.getmtime

        def vanishing(path):
            if path.endswith("s1.jsonl"):
                raise FileNotFoundError(path)
            return real_getmtime(path)
        ta.os.path.getmtime = vanishing
        try:
            listed = sorted(os.path.basename(p) for p in ta.transcripts(root, 3650))
        except OSError as exc:  # recorded as a failed check
            listed = f"raised {type(exc).__name__}"
        finally:
            ta.os.path.getmtime = real_getmtime
        check("a file vanishing during the walk is skipped", listed, ["agent-1.jsonl", "agent-2.jsonl"])

        sub = ta.parse_transcript(sub_path)
        check("subagent by path", sub["sub"], True)
        check("subagent meta read", ta.agent_label(sub["meta"]), "ab-lean|sonnet")
        orphan = ta.parse_transcript(os.path.join(root, "proj", "s1", "subagents", "agent-2.jsonl"))
        check("subagent without meta is still a subagent", (orphan["sub"], ta.agent_label(orphan["meta"])),
              (True, "?|inherit"))
        check("main is not a subagent", stats["sub"], False)
        try:
            gone = ta.parse_transcript(os.path.join(root, "proj", "removed-after-listing.jsonl"))
        except Exception as exc:  # recorded as a failed check, so the run reaches the summary
            gone = {"requests": None, "failed": f"raised {type(exc).__name__}"}
        check("a file that cannot be read is reported, not raised",
              (gone["requests"], (gone["failed"] or "").startswith("FileNotFoundError")), (0, True))

        for workers in (1, 2):
            results = ta.parse_all(root, days=3650, workers=workers)
            runs = ta.build_runs(results, "ab review").splitlines()
            check(f"runs filter finds the subagent (workers={workers})", len(runs), 2)
            check("runs row names the agent", "ab-lean|sonnet" in (runs[1] if len(runs) > 1 else ""), True)
        check("runs filter excludes others", len(ta.build_runs(results, "nothing").splitlines()), 1)
        report = ta.build_report(results, 3650)
        main_total = sum(stats["cost"].values())
        sub_total = sum(r["cost"] and sum(r["cost"].values()) for r in results if r["sub"])
        check("report splits main and subagents",
              f"main {main_total / (main_total + sub_total):.1%}" in report, True)
        check("report has the repeated failure", "Error: expected <n> got <n>" in report, True)
        check("report shows skipped lines", "unparseable lines skipped: 2" in report, True)
        check("prefix without shape says so", ta.build_prefix(main_path).startswith("no api-request-shape"), True)
        prefix = ta.build_prefix(sub_path)
        check("prefix totals shape and early attachments",
              prefix.splitlines()[0].startswith("chars by group:") and "late" not in prefix
              and "Artifact" in prefix and "instructions" in prefix, True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    run_job_cases()
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"# tests failed: {len(FAILURES)} of {len(RAN)}")
        sys.exit(1)
    print(f"# all {len(RAN)} checks passed")


if __name__ == "__main__":
    main()
