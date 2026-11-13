"""Checks for context_guard.py, run end to end as the hook runs: event JSON on stdin.

python -I test_context_guard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "context_guard.py")
failures, passed = [], 0


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
    else:
        failures.append(name)
        print(f"{name}: expected {want!r}, got {got!r}")


def assistant(msg_id, read, created=0, sidechain=False, output=1):
    """An assistant line shaped like Claude Code's: nested cache_creation split, streamed output count."""
    return json.dumps({
        "type": "assistant", "isSidechain": sidechain, "uuid": msg_id + "-u",
        "message": {"id": msg_id, "role": "assistant", "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 3, "cache_read_input_tokens": read,
                              "cache_creation_input_tokens": created,
                              "cache_creation": {"ephemeral_1h_input_tokens": created,
                                                 "ephemeral_5m_input_tokens": 0},
                              "output_tokens": output}}})


def synthetic(api_error=False):
    """What Claude Code writes after an interrupt or an API error: model <synthetic>, usage all zero."""
    entry = {"type": "assistant", "isSidechain": False,
             "message": {"id": "syn", "model": "<synthetic>", "role": "assistant",
                         "content": [{"type": "text", "text": "No response requested."}],
                         "usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0, "output_tokens": 0}}}
    if api_error:
        entry["isApiErrorMessage"] = True
        entry["message"]["model"] = "claude-opus-5-5"
    return json.dumps(entry)


def boundary():
    return json.dumps({"parentUuid": None, "isSidechain": False, "type": "system", "subtype": "compact_boundary",
                       "content": "Conversation compacted",
                       "compactMetadata": {"trigger": "manual", "preTokens": 952000}})


def user(text):
    return json.dumps({"type": "user", "isSidechain": False,
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                                "content": text}]}})


def write(folder, name, lines, tail=""):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n" + tail)
    return path


CONFIG = None   # a temp dir standing in for the config root; no subprocess may touch the real one


def base_env(**extra):
    """The environment without CLAUDE_CONFIG_DIR, plus `extra`."""
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.update(extra)
    return env


def run(stdin, *args, env=None, cwd=None):
    proc = subprocess.run([sys.executable, "-I", "-S", SCRIPT, *args], input=stdin.encode("utf-8"),
                          capture_output=True, timeout=30, cwd=cwd,
                          env=base_env(CLAUDE_CONFIG_DIR=CONFIG) if env is None else env)
    return proc.returncode, proc.stdout.decode("utf-8")


def full_reminder(path, *args, session_id=None, env=None, cwd=None):
    event = {"hook_event_name": "UserPromptSubmit", "transcript_path": path}
    if session_id is not None:
        event["session_id"] = session_id
    code, out = run(json.dumps(event), *args, env=env, cwd=cwd)
    if code != 0:
        return f"exit {code}"
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def reminder(path, *args, cwd=None):
    """The reminder's first sentence."""
    text = full_reminder(path, *args, cwd=cwd)
    return text.split(". ")[0] if text and not text.startswith("exit ") else text


def main():
    global CONFIG
    folder = tempfile.mkdtemp(prefix="ctx-guard-")
    CONFIG = os.path.join(folder, "config")
    try:
        over = write(folder, "over.jsonl", [user("hi"), assistant("m1", 90_000), user("x"),
                                             assistant("m2", 240_000, 10_000, output=1),
                                             assistant("m2", 240_000, 10_000, output=900)])
        check("over the threshold: reminds with the last request's size", reminder(over),
              "Context is at 250k tokens")
        under = write(folder, "under.jsonl", [user("hi"), assistant("m1", 150_000, 5_000)])
        check("under the threshold: nothing", reminder(under), None)
        check("--threshold lowers it", reminder(under, "--threshold", "100000"), "Context is at 155k tokens")
        side = write(folder, "side.jsonl", [assistant("m1", 120_000), assistant("s1", 400_000, sidechain=True)])
        check("a sidechain request after the main one is ignored", reminder(side), None)
        # a tool result far larger than one read step sits after the last assistant line
        big = write(folder, "big.jsonl", [assistant("m1", 260_000), user("y" * (3 << 20))])
        check("the last request is found across several read steps", reminder(big), "Context is at 260k tokens")
        # the last request straddles the first read step's edge: its tail is read first, its head next
        last = assistant("m2", 260_000)
        pad = (1 << 20) - 100 - len(user("").encode("utf-8")) - 1
        edge = write(folder, "edge.jsonl", [assistant("m1", 300_000), user("x"), last, user("y" * pad)])
        with open(edge, "rb") as fh:
            data = fh.read()
        start = data.index(last.encode("utf-8"))
        check("fixture: the read-step edge falls inside the last request's line",
              start < len(data) - (1 << 20) < start + len(last), True)
        check("a request split by the read-step edge is joined, not skipped", reminder(edge),
              "Context is at 260k tokens")
        syn = write(folder, "syn.jsonl", [assistant("m1", 250_000), user("x"), synthetic()])
        check("a <synthetic> line after an interrupt is not the last request", reminder(syn),
              "Context is at 250k tokens")
        api = write(folder, "api.jsonl", [assistant("m1", 250_000), user("x"), synthetic(api_error=True)])
        check("an API error line is not the last request", reminder(api), "Context is at 250k tokens")
        compact = write(folder, "compact.jsonl", [assistant("m1", 950_000), boundary(), user("summary")])
        check("after a compaction and before the next request: nothing", reminder(compact), None)
        check("a bad --threshold falls back to the default", reminder(over, "--threshold", "200k"),
              "Context is at 250k tokens")
        check("a non-positive --threshold falls back to the default", reminder(under, "--threshold", "-5"), None)
        # cut after its "usage" key, so the line is not skipped before parsing
        cut = write(folder, "cut.jsonl", [assistant("m1", 230_000)], tail=assistant("m2", 10)[:-20])
        check("a half-written last line is skipped", reminder(cut), "Context is at 230k tokens")
        check("a missing transcript: nothing, exit 0", reminder(os.path.join(folder, "nope.jsonl")), None)
        check("stdin that is not JSON: nothing, exit 0", run("not json"), (0, ""))
        check("no transcript_path: nothing, exit 0", run(json.dumps({"hook_event_name": "UserPromptSubmit"})),
              (0, ""))
        home = os.path.join(folder, "home")
        env = base_env(USERPROFILE=home, HOME=home)
        sid = "0f4e2c1a-77b2-4c1e-9d3a-5b6c7d8e9f01"
        state = os.path.join(home, ".claude", "state", sid + ".md")
        check("the reminder names this session's state file and what to do with it",
              full_reminder(over, session_id=sid, env=env),
              f"Context is at 250k tokens. Keep this session's state file {state} current: rewrite it at the "
              "next task boundary with safe_to_compact: true, and keep working; compaction runs automatically "
              "at a safe point and the file is re-injected afterwards.")
        for bad in ("../../x", "a b", "", "x" * 129, "abc\n"):
            check(f"an invalid session id {bad[:12]!r} falls back to the literal path",
                  "~/.claude/state/<CLAUDE_CODE_SESSION_ID>.md current:" in
                  (full_reminder(over, session_id=bad, env=env) or ""), True)
        check("no session id falls back to the literal path",
              "~/.claude/state/<CLAUDE_CODE_SESSION_ID>.md current:" in (full_reminder(over, env=env) or ""), True)
        config = os.path.join(folder, "elsewhere")
        env_cfg = base_env(USERPROFILE=home, HOME=home, CLAUDE_CONFIG_DIR=config)
        check("CLAUDE_CONFIG_DIR moves the state file",
              f"state file {os.path.join(config, 'state', sid + '.md')} current:" in
              (full_reminder(over, session_id=sid, env=env_cfg) or ""), True)
        check("CLAUDE_CONFIG_DIR moves the fallback path too",
              f"state file {os.path.join(config, 'state', '<CLAUDE_CODE_SESSION_ID>.md')} current:" in
              (full_reminder(over, session_id="a b", env=env_cfg) or ""), True)
        env_empty = base_env(USERPROFILE=home, HOME=home, CLAUDE_CONFIG_DIR="")
        check("an empty CLAUDE_CONFIG_DIR means ~/.claude",
              f"state file {state} current:" in (full_reminder(over, session_id=sid, env=env_empty) or ""), True)
        elsewhere = os.path.join(folder, "other-cwd")
        os.makedirs(elsewhere)
        check("runs under python -I from another cwd", reminder(over, cwd=elsewhere), "Context is at 250k tokens")
        check("the reminder never mentions handing off", "hand" in (full_reminder(over) or "hand").lower(), False)
    finally:
        shutil.rmtree(folder)
    print(f"# context-guard: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


sys.exit(main())
