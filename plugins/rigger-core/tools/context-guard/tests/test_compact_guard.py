"""Checks for compact_guard.py, run end to end as the hooks run it: `python -I -S <script> <command>`,
event JSON on stdin, a temporary --state-dir.

python -I test_compact_guard.py
"""
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "compact_guard.py")
CHUNK = 1 << 20
SID = "1234d76d-5612-4458-8c62-41bf0710415f"
LOG = "compact-guard.log"
failures, passed = [], 0

# Lines copied from a real transcript (Claude Code 2.1.316); only the thinking signature and the tool
# result text are shortened. Tokens and timestamps are set per test.
END_TURN = r'''{"parentUuid":"2722d88c-bdcf-4b46-909d-603f7880ffbd","isSidechain":false,"message":{"model":"claude-haiku-5-5","id":"msg_011CgfwTVKaz2w8SxwtL2mv9","type":"message","role":"assistant","content":[{"type":"text","text":"DONE-A3\n\n`results-A3.txt` has 30 lines, f01 to f30 in order, no duplicates or gaps. I re-read the file to check."}],"container":null,"stop_reason":"end_turn","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":807,"cache_read_input_tokens":114852,"output_tokens":54,"output_tokens_details":{"thinking_tokens":0},"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":807,"ephemeral_5m_input_tokens":0},"inference_geo":"global","iterations":[{"input_tokens":2,"output_tokens":54,"cache_read_input_tokens":114852,"cache_creation_input_tokens":807,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":807},"type":"message"}],"speed":"standard","fallback_credit":null},"input_transformations":[],"diagnostics":null,"context_management":null},"apiBlockIndex":0,"requestId":"req_011CgfwTUyzSSHVr3sZ6fPCn","requestRef":"4db80889-4b6d-499d-a3d5-27dbd72d0e23","requestedModel":"claude-haiku-5-5","type":"assistant","uuid":"c1e2e4c4-ec19-4c5f-9874-e29dcb72b560","timestamp":"2026-11-03T11:46:38.305Z","advisorModel":"claude-opus-5-5","effort":"medium","perTurnEffort":"medium","userType":"external","entrypoint":"claude-desktop","cwd":"/work/spike","sessionId":"1234d76d-5612-4458-8c62-41bf0710415f","version":"2.1.316","gitBranch":"HEAD","slug":"typed-bubbling-turtle"}'''
TOOL_USE = r'''{"parentUuid":"bc427893-70f1-46b0-85eb-9df10387f57e","isSidechain":false,"message":{"model":"claude-haiku-5-5","id":"msg_011CgfwBmQ2UVJMQ533zkAtm","type":"message","role":"assistant","content":[{"type":"tool_use","id":"toolu_01EPFCqMNqTdeaLigvJLDFgX","name":"Read","input":{"file_path":"/work/spike/data/f01.txt"},"caller":{"type":"direct"}}],"container":null,"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":27985,"cache_read_input_tokens":37830,"output_tokens":207,"output_tokens_details":{"thinking_tokens":135},"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":27985,"ephemeral_5m_input_tokens":0},"inference_geo":"global","iterations":[{"input_tokens":2,"output_tokens":207,"cache_read_input_tokens":37830,"cache_creation_input_tokens":27985,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":27985},"type":"message"}],"speed":"standard","fallback_credit":null},"input_transformations":[],"diagnostics":null,"context_management":null},"wireToolInputs":{"toolu_01EPFCqMNqTdeaLigvJLDFgX":{"file_path":"/work/spike/data/f01.txt"}},"apiBlockIndex":1,"requestId":"req_011CgfwBmAPGP4m2sneVHRgo","requestRef":"1c77f8c7-3a03-4964-86fb-337f7cdb7965","requestedModel":"claude-haiku-5-5","type":"assistant","uuid":"22642c98-7c44-4c08-bb25-c34c5d8634fe","timestamp":"2026-11-03T11:43:05.673Z","advisorModel":"claude-opus-5-5","effort":"medium","perTurnEffort":"medium","userType":"external","entrypoint":"claude-desktop","cwd":"/work/spike","sessionId":"1234d76d-5612-4458-8c62-41bf0710415f","version":"2.1.316","gitBranch":"HEAD"}'''
# the thinking block of the same request: Claude Code writes one line per content block
THINKING = r'''{"parentUuid":"018eafb3-0e9b-4632-8609-8a254c2bce33","isSidechain":false,"message":{"model":"claude-haiku-5-5","id":"msg_011CgfwBmQ2UVJMQ533zkAtm","type":"message","role":"assistant","content":[{"type":"thinking","thinking":"","signature":"CAQS1gcKEAgTGAI4AUIIdGhpbmtpbmcSDFc38S1Z"}],"container":null,"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":27985,"cache_read_input_tokens":37830,"output_tokens":207,"output_tokens_details":{"thinking_tokens":135},"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":27985,"ephemeral_5m_input_tokens":0},"inference_geo":"global","iterations":[{"input_tokens":2,"output_tokens":207,"cache_read_input_tokens":37830,"cache_creation_input_tokens":27985,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":27985},"type":"message"}],"speed":"standard","fallback_credit":null},"input_transformations":[],"diagnostics":null,"context_management":null},"thinkingDurationMs":852,"thinkingDisplay":"updates","apiBlockIndex":0,"requestId":"req_011CgfwBmAPGP4m2sneVHRgo","requestRef":"1c77f8c7-3a03-4964-86fb-337f7cdb7965","requestedModel":"claude-haiku-5-5","type":"assistant","uuid":"bc427893-70f1-46b0-85eb-9df10387f57e","timestamp":"2026-11-03T11:43:05.654Z","advisorModel":"claude-opus-5-5","effort":"medium","perTurnEffort":"medium","userType":"external","entrypoint":"claude-desktop","cwd":"/work/spike","sessionId":"1234d76d-5612-4458-8c62-41bf0710415f","version":"2.1.316","gitBranch":"HEAD"}'''
BOUNDARY = r'''{"parentUuid":null,"logicalParentUuid":"50c2d7b5-d202-4fec-8b85-a92280efb2e0","isSidechain":false,"type":"system","subtype":"compact_boundary","content":"Conversation compacted","level":"info","compactMetadata":{"trigger":"auto","preTokens":118355,"postTokens":30327,"cumulativeDroppedTokens":88028,"durationMs":11172,"preservedSegment":{"headUuid":"6a17afa0-1c07-490d-958c-32347fa6aab8","anchorUuid":"e6d4c66b-68ec-472e-b3b2-ea6df8278b0c","tailUuid":"50c2d7b5-d202-4fec-8b85-a92280efb2e0"},"preservedMessages":{"anchorUuid":"e6d4c66b-68ec-472e-b3b2-ea6df8278b0c","uuids":["6a17afa0-1c07-490d-958c-32347fa6aab8","50c2d7b5-d202-4fec-8b85-a92280efb2e0"],"allUuids":["6a17afa0-1c07-490d-958c-32347fa6aab8","50c2d7b5-d202-4fec-8b85-a92280efb2e0"]}},"uuid":"2564446e-6a5a-4e3d-b8b1-bbf40272e73d","timestamp":"2026-11-03T11:44:19.464Z","userType":"external","entrypoint":"claude-desktop","cwd":"/work/spike","sessionId":"1234d76d-5612-4458-8c62-41bf0710415f","version":"2.1.316","gitBranch":"HEAD","slug":"typed-bubbling-turtle"}'''
SYNTHETIC = r'''{"parentUuid":"696b193b-48ae-4e3a-81c9-2d85f28bea54","isSidechain":false,"type":"assistant","uuid":"96732d55-1404-47a7-99d7-75f94bd0a95b","timestamp":"2026-11-03T11:43:59.818Z","message":{"diagnostics":null,"id":"0bb68d36-6f1b-4d72-b1a1-8fac582670a4","container":null,"model":"<synthetic>","role":"assistant","stop_details":null,"stop_reason":"stop_sequence","stop_sequence":"","type":"message","usage":{"output_tokens_details":null,"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":null,"cache_creation":{"ephemeral_1h_input_tokens":0,"ephemeral_5m_input_tokens":0},"inference_geo":null,"iterations":null,"speed":null,"fallback_credit":null},"content":[{"type":"text","text":"Autocompact is thrashing: the context refilled to the limit within 3 turns of the previous compact, 3 times in a row."}],"context_management":null},"apiError":"autocompact_thrashing","error":"invalid_request","isApiErrorMessage":true,"userType":"external","entrypoint":"claude-desktop","cwd":"/work/spike","sessionId":"427d664f-2130-41a1-bc29-3b0ab86096b8","version":"2.1.316","gitBranch":"HEAD","slug":"whimsical-churning-hopper"}'''
USER = r'''{"parentUuid":"22642c98-7c44-4c08-bb25-c34c5d8634fe","isSidechain":false,"promptId":"75f3cf0b-bc8c-4377-84e3-1d2eefe369df","type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_01EPFCqMNqTdeaLigvJLDFgX","type":"tool_result","content":"1\t0001 harbor window orbit glacier stone cloud canyon amber velvet violet\n"}]},"uuid":"0c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f","timestamp":"2026-11-03T11:43:05.700Z","sessionId":"1234d76d-5612-4458-8c62-41bf0710415f","version":"2.1.316"}'''

FALSE = "---\nsafe_to_compact: false\nupdated: 2026-11-03T10:00:00+00:00\n---\n## Goal\nship it\n"
TRUE = FALSE.replace("false", "true")


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
    else:
        failures.append(name)
        print(f"{name}: expected {want!r}, got {got!r}")


def stamp(minutes_ago):
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (moment.microsecond // 1000)


def line(template, tokens=None, minutes_ago=0.0, timestamp=None, **message):
    """A fixture line from a real one: prompt size split like Claude Code's usage, timestamp from now."""
    entry = json.loads(template)
    if tokens is not None:
        usage = entry["message"]["usage"]
        usage.update(input_tokens=2, cache_creation_input_tokens=1_000, cache_read_input_tokens=tokens - 1_002)
    entry["timestamp"] = stamp(minutes_ago) if timestamp is None else timestamp
    if message:
        entry["message"].update(message)
    text = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
    assert b'"usage"' in text.encode("utf-8") or b"compact_boundary" in text.encode("utf-8")
    return text


def user(text="ok"):
    entry = json.loads(USER)
    entry["message"]["content"][0]["content"] = text
    return json.dumps(entry, separators=(",", ":"))


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def read_bytes(path):
    """The file's bytes, or b"" when it cannot be read (so a red run fails on its check, not here)."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


CONFIG = None   # a temp dir standing in for the config root; no subprocess may touch the real one


def base_env(**extra):
    """The environment without CLAUDE_CONFIG_DIR, plus `extra`."""
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.update(extra)
    return env


def run(command, stdin, *args, env=None, cwd=None):
    proc = subprocess.run([sys.executable, "-I", "-S", SCRIPT, command, *args], input=stdin,
                          capture_output=True, timeout=30, cwd=cwd,
                          env=base_env(CLAUDE_CONFIG_DIR=CONFIG) if env is None else env)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class Case:
    """A fresh folder: transcript, state dir, state file."""
    count = 0

    def __init__(self, root, lines=None, state=FALSE):
        Case.count += 1
        self.folder = os.path.join(root, "c%03d" % Case.count)
        self.state_dir = os.path.join(self.folder, "state")
        os.makedirs(self.state_dir)
        self.transcript = os.path.join(self.folder, "t.jsonl")
        self.state = os.path.join(self.state_dir, SID + ".md")
        self.log = os.path.join(self.state_dir, LOG)
        if lines is not None:
            write_bytes(self.transcript, ("\n".join(lines) + "\n").encode("utf-8"))
        if state is not None:
            write_bytes(self.state, state if isinstance(state, bytes) else state.encode("utf-8"))

    def event(self, **changes):
        event = {"session_id": SID, "transcript_path": self.transcript, "cwd": self.folder,
                 "hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": None}
        event.update(changes)
        return {key: value for key, value in event.items() if value is not DROP}

    def run(self, command, event, *args):
        stdin = event if isinstance(event, bytes) else json.dumps(event).encode("utf-8")
        return run(command, stdin, "--state-dir", self.state_dir, *args)

    def last_log(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read().splitlines()[-1]
        except (OSError, IndexError):
            return None

    def pre(self, *args, **changes):
        """(exit code, block / allow / other stdout, reason from the log line)."""
        code, out, err = self.run("pre", self.event(**changes), *args)
        verdict = "allow" if out == "" else ("block" if json.loads(out).get("decision") == "block" else out)
        last = self.last_log()
        return code, verdict, last.split(" ")[-1] if last else None


DROP = object()


def fresh(tokens=260_000, minutes_ago=0.5, template=END_TURN, **message):
    """The fixture that blocks: a user line, then a fresh main-chain assistant line over the floor."""
    return [user("hi"), line(END_TURN, 90_000, 30), user(), line(template, tokens, minutes_ago, **message)]


def pre_checks(root):
    blocked = (0, "block", "unsafe")
    case = Case(root, fresh())
    code, out, err = case.run("pre", case.event())
    check("positive control: false + 260k + fresh line blocks, naming the state file",
          (code, json.loads(out) if out else out),
          (0, {"decision": "block",
               "reason": f"compaction postponed at 260k: {case.state} has safe_to_compact: false"}))
    check("positive control: stderr is empty", err, "")
    check("each decision is logged as `ts sid tokens decision reason`",
          re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00 " + SID + r" 260000 block unsafe",
                       case.last_log() or "")
          is not None, True)

    case = Case(root, fresh())
    check("trigger manual: allow", case.pre(trigger="manual"), (0, "allow", "manual"))
    check("a decision made before the transcript is read logs its tokens as -",
          (case.last_log() or "").split(" ")[1:3], [SID, "-"])
    check("no trigger: allow", Case(root, fresh()).pre(trigger=DROP), (0, "allow", "manual"))
    check("agent_id present: allow", Case(root, fresh()).pre(agent_id="a1b2c3"), (0, "allow", "agent"))
    case = Case(root, fresh())
    check("missing transcript: allow", case.pre(transcript_path=os.path.join(case.folder, "nope.jsonl")),
          (0, "allow", "no-transcript"))
    case = Case(root, fresh())
    check("transcript path is a folder: allow", case.pre(transcript_path=case.folder), (0, "allow", "no-transcript"))
    check("transcript path is not a string: allow", Case(root, fresh()).pre(transcript_path=["x"]),
          (0, "allow", "no-transcript"))
    check("transcript without an assistant line: allow", Case(root, [user(), user()]).pre(),
          (0, "allow", "no-transcript"))

    check("199 999 tokens: allow", Case(root, fresh(199_999)).pre(), (0, "allow", "below-floor"))
    check("200 000 tokens: block", Case(root, fresh(200_000)).pre(), blocked)
    check("449 999 tokens: block", Case(root, fresh(449_999)).pre(), blocked)
    check("450 000 tokens: allow", Case(root, fresh(450_000)).pre(), (0, "allow", "hard-cap"))
    check("--floor raises the floor", Case(root, fresh()).pre("--floor", "300000"), (0, "allow", "below-floor"))
    check("a bad --floor falls back to the default", Case(root, fresh(199_999)).pre("--floor", "1k"),
          (0, "allow", "below-floor"))
    check("a non-positive --floor falls back to the default", Case(root, fresh(199_999)).pre("--floor", "-1"),
          (0, "allow", "below-floor"))
    check("--hard-cap lowers the cap", Case(root, fresh()).pre("--hard-cap", "250000"), (0, "allow", "hard-cap"))

    check("end_turn 44 min ago: block", Case(root, fresh(minutes_ago=44)).pre(), blocked)
    check("end_turn 46 min ago: allow (idle)", Case(root, fresh(minutes_ago=46)).pre(), (0, "allow", "idle"))
    check("tool_use line 60 min ago: block", Case(root, fresh(minutes_ago=60, template=TOOL_USE)).pre(), blocked)
    check("thinking line of a tool_use request 60 min ago: block",
          Case(root, fresh(minutes_ago=60, template=THINKING)).pre(), blocked)
    case = Case(root, fresh(minutes_ago=60, template=TOOL_USE, stop_reason="end_turn"))
    check("a tool_use block 60 min ago with stop_reason end_turn: block", case.pre(), blocked)
    lines = fresh()
    lines[-1] = line(END_TURN, 260_000, timestamp="yesterday")
    check("an unparseable timestamp is not idle: block", Case(root, lines).pre(), blocked)
    lines[-1] = line(END_TURN, 260_000, timestamp=stamp(60)[:-1])
    check("a timestamp without a zone is not idle: block", Case(root, lines).pre(), blocked)
    lines[-1] = line(END_TURN, 260_000, timestamp=1_700_000_000)
    check("a timestamp that is not a string is not idle: block", Case(root, lines).pre(), blocked)
    check("last line a compact boundary: allow (0 tokens)",
          Case(root, fresh() + [line(BOUNDARY), user("summary")]).pre(), (0, "allow", "below-floor"))
    check("a <synthetic> API-error line after the request is skipped: block",
          Case(root, fresh() + [user(), line(SYNTHETIC)]).pre(), blocked)

    # the deciding line straddles the first read step's edge: "usage" before the cut, timestamp after it
    deciding = line(END_TURN, 260_000, 0.5).encode("utf-8")
    cut_at = (deciding.index(b'"usage"') + deciding.index(b'"timestamp"')) // 2
    pad = CHUNK + cut_at - len(deciding) - 2 - len(user("").encode("utf-8"))
    head = ("\n".join([user("hi"), line(END_TURN, 150_000, 1), user()]) + "\n").encode("utf-8")
    data = head + deciding + b"\n" + user("y" * pad).encode("utf-8") + b"\n"
    case = Case(root)
    write_bytes(case.transcript, data)
    edge = len(data) - CHUNK
    start = data.index(deciding)
    check("fixture: the read-step edge falls between the deciding line's usage and timestamp",
          start + deciding.index(b'"usage"') < edge < start + deciding.index(b'"timestamp"'), True)
    check("fixture: the transcript is larger than one read step", len(data) > CHUNK, True)
    check("a deciding line split by the read-step edge is joined, not skipped", case.pre(), blocked)

    check("state file missing: allow", Case(root, fresh(), state=None).pre(), (0, "allow", "no-state"))
    check("safe_to_compact: true: allow", Case(root, fresh(), state=TRUE).pre(), (0, "allow", "safe"))
    check("safe_to_compact: True: allow", Case(root, fresh(), state=TRUE.replace("true", "True")).pre(),
          (0, "allow", "safe"))
    check("safe_to_compact: 'true': allow", Case(root, fresh(), state=TRUE.replace("true", "'true'")).pre(),
          (0, "allow", "safe"))
    check('safe_to_compact: "false": block', Case(root, fresh(), state=FALSE.replace("false", '"false"')).pre(),
          blocked)
    check("CRLF frontmatter, false: block", Case(root, fresh(), state=FALSE.replace("\n", "\r\n")).pre(), blocked)
    check("BOM, false: block", Case(root, fresh(), state=b"\xef\xbb\xbf" + FALSE.encode("utf-8")).pre(), blocked)
    check("frontmatter without the key: allow",
          Case(root, fresh(), state="---\nupdated: x\n---\nsafe_to_compact: false\n").pre(), (0, "allow", "no-state"))
    check("no opening --- line: allow", Case(root, fresh(), state="title\nsafe_to_compact: false\n---\nbody\n").pre(),
          (0, "allow", "no-state"))
    check("frontmatter never closed: allow",
          Case(root, fresh(), state="---\nsafe_to_compact: false\nmore: x\n").pre(),
          (0, "allow", "no-state"))
    check("an unknown value: allow", Case(root, fresh(), state=FALSE.replace("false", "later")).pre(),
          (0, "allow", "bad-value"))
    check("a state file that is not UTF-8: allow",
          Case(root, fresh(), state=FALSE.encode("utf-8") + b"\xff\xfe").pre(), (0, "allow", "no-state"))
    case = Case(root, fresh())
    check("an invalid session id: allow", case.pre(session_id="../" + SID), (0, "allow", "bad-sid"))
    check("a session id that is not a string: allow", Case(root, fresh()).pre(session_id=12345),
          (0, "allow", "bad-sid"))
    check("an invalid session id is logged as -", (case.last_log() or "").split(" ")[1:2], ["-"])

    for stdin in (b"not json", b"[]", b"\xff\xfe{"):
        case = Case(root, fresh())
        check(f"stdin {stdin[:8]!r}: allow, no output", case.run("pre", stdin)[:2], (0, ""))
    case = Case(root, fresh())
    os.makedirs(case.log)
    check("the log cannot be written: the decision stands", case.pre(), (0, "block", None))
    case = Case(root, fresh())
    missing = os.path.join(case.folder, "new", "state")
    code, out, err = run("pre", json.dumps(case.event()).encode("utf-8"), "--state-dir", missing)
    check("a missing state dir is created for the log", (code, out, os.path.isfile(os.path.join(missing, LOG))),
          (0, "", True))

    case = Case(root, fresh())
    old = b"".join(b"old-%07d\n" % n for n in range(100_001))   # 1.2 MB, its half falls inside a line
    write_bytes(case.log, old)
    case.pre()
    kept = read_bytes(case.log)
    old_part = kept.rsplit(b"\n", 2)[0] + b"\n"
    check("a log over 1 MB is cut to its last half, at a line start, before the append",
          (len(old) // 2 - 12 <= len(kept) < len(old) // 2 + 100, kept.startswith(b"old-"),
           old.endswith(old_part), kept.endswith(b" block unsafe\n")), (True, True, True, True))
    case = Case(root, fresh())
    small = b"".join(b"old-%07d\n" % n for n in range(80_000))   # 0.96 MB
    write_bytes(case.log, small)
    case.pre()
    check("a log under 1 MB is only appended to", read_bytes(case.log).startswith(small), True)


def coordinator_state(coordinator="true", safe="false"):
    """A state file; a None value leaves its key out."""
    keys = [("coordinator", coordinator), ("safe_to_compact", safe), ("updated", "2026-11-03T10:00:00+00:00")]
    head = "".join(f"{key}: {value}\n" for key, value in keys if value is not None)
    return f"---\n{head}---\n## Goal\nship it\n"


HOOKS_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "hooks", "hooks.json")


def coordinator_checks(root):
    """`coordinator: true` in the frontmatter: no idle rule; safe_to_compact: true (fresh file) allows, anything
    else blocks until its own cap (default 950 000)."""
    blocked = (0, "block", "coordinator")
    allowed = (0, "allow", "coordinator-cap")
    safe = (0, "allow", "coordinator-safe")
    case = Case(root, fresh(520_000), state=coordinator_state())
    code, out, err = case.run("pre", case.event())
    check("positive control: coordinator + false + 520k blocks, naming the state file and the cap",
          (code, json.loads(out) if out else out, err),
          (0, {"decision": "block",
               "reason": f"compaction postponed at 520k: {case.state} has safe_to_compact: false (coordinator), "
                         "forced at 950k"}, ""))
    check("the coordinator decision is logged with its tokens",
          re.fullmatch(r"\S+ " + SID + r" 520000 block coordinator", case.last_log() or "") is not None, True)

    check("coordinator at 949 999: block", Case(root, fresh(949_999), state=coordinator_state()).pre(), blocked)
    check("coordinator at 950 000: allow", Case(root, fresh(950_000), state=coordinator_state()).pre(), allowed)
    check("coordinator at 1 000 000: allow", Case(root, fresh(1_000_000), state=coordinator_state()).pre(), allowed)
    check("coordinator at 200 000 (the floor): block", Case(root, fresh(200_000), state=coordinator_state()).pre(),
          blocked)
    check("coordinator at 199 999: allow, the floor comes first",
          Case(root, fresh(199_999), state=coordinator_state()).pre(), (0, "allow", "below-floor"))
    check("coordinator at 520k: block although the hard cap is 450k",
          Case(root, fresh(520_000), state=coordinator_state()).pre("--hard-cap", "450000"), blocked)
    check("--coordinator-cap raises the cap",
          Case(root, fresh(959_999), state=coordinator_state()).pre("--coordinator-cap", "960000"), blocked)
    check("--coordinator-cap is the cap: allow at it",
          Case(root, fresh(960_000), state=coordinator_state()).pre("--coordinator-cap", "960000"), allowed)
    check("--coordinator-cap lowers the cap",
          Case(root, fresh(300_000), state=coordinator_state()).pre("--coordinator-cap", "300000"), allowed)
    check("a bad --coordinator-cap falls back to the default",
          Case(root, fresh(949_999), state=coordinator_state()).pre("--coordinator-cap", "1m"), blocked)
    check("a non-positive --coordinator-cap falls back to the default",
          Case(root, fresh(949_999), state=coordinator_state()).pre("--coordinator-cap", "0"), blocked)
    check("--coordinator-cap does not move the hard cap of a session without the key",
          Case(root, fresh(520_000), state=FALSE).pre("--coordinator-cap", "960000"), (0, "allow", "hard-cap"))

    check("coordinator idle 46 min: block, the idle rule does not apply",
          Case(root, fresh(520_000, minutes_ago=46), state=coordinator_state()).pre(), blocked)
    check("coordinator idle 600 min: block", Case(root, fresh(520_000, minutes_ago=600), state=coordinator_state()).pre(),
          blocked)
    check("the same idle session without the key: allow (idle)",
          Case(root, fresh(260_000, minutes_ago=46), state=FALSE).pre(), (0, "allow", "idle"))
    check("coordinator idle at the cap: allow (coordinator-cap)",
          Case(root, fresh(950_000, minutes_ago=46), state=coordinator_state()).pre(), allowed)
    # branch 2: safe_to_compact: true on a fresh file allows
    check("coordinator + safe_to_compact: true: allow (coordinator-safe)",
          Case(root, fresh(520_000), state=coordinator_state(safe="true")).pre(), safe)
    check("coordinator + safe_to_compact: true, idle 600 min: allow (coordinator-safe)",
          Case(root, fresh(520_000, minutes_ago=600), state=coordinator_state(safe="true")).pre(), safe)
    check("coordinator + safe_to_compact: true at 200 000: allow (coordinator-safe)",
          Case(root, fresh(200_000), state=coordinator_state(safe="true")).pre(), safe)
    check("coordinator: TRUE, safe_to_compact: True: allow (case-insensitive)",
          Case(root, fresh(520_000), state=coordinator_state("TRUE", "True")).pre(), safe)
    check("coordinator: 'true', safe_to_compact: 'true': allow (quotes allowed)",
          Case(root, fresh(520_000), state=coordinator_state("'true'", "'true'")).pre(), safe)
    check("CRLF frontmatter, safe: allow",
          Case(root, fresh(520_000), state=coordinator_state(safe="true").replace("\n", "\r\n")).pre(), safe)
    check("BOM, safe: allow",
          Case(root, fresh(520_000), state=b"\xef\xbb\xbf" + coordinator_state(safe="true").encode("utf-8")).pre(),
          safe)
    check("coordinator + safe_to_compact: true at the cap: allow (coordinator-cap, the cap comes first)",
          Case(root, fresh(950_000), state=coordinator_state(safe="true")).pre(), allowed)

    # branch 3: false blocks
    check("coordinator + safe_to_compact: false: block",
          Case(root, fresh(520_000), state=coordinator_state(safe="false")).pre(), blocked)
    check("coordinator + safe_to_compact: FALSE with quotes: block",
          Case(root, fresh(520_000), state=coordinator_state(safe="'FALSE'")).pre(), blocked)
    check("coordinator + false with CRLF: block",
          Case(root, fresh(520_000), state=coordinator_state().replace("\n", "\r\n")).pre(), blocked)
    check("coordinator + false with a BOM: block",
          Case(root, fresh(520_000), state=b"\xef\xbb\xbf" + coordinator_state().encode("utf-8")).pre(), blocked)

    # branch 4: a coordinator whose safe_to_compact is missing or the wrong shape: fail closed
    bad = (0, "block", "coordinator-bad-state")
    for label, value in (("missing key", None), ("non-boolean string", "later"), ("number", "1"),
                         ("list", "[true]"), ("empty", ""), ("yes", "yes")):
        check(f"coordinator + safe_to_compact {label}: block (coordinator-bad-state)",
              Case(root, fresh(520_000), state=coordinator_state(safe=value)).pre(), bad)
    check("coordinator + wrong shape at the cap: allow (coordinator-cap)",
          Case(root, fresh(950_000), state=coordinator_state(safe="later")).pre(), allowed)
    case = Case(root, fresh(520_000), state=coordinator_state(safe=None))
    case.pre()
    check("the bad-state block is logged with its tokens",
          re.fullmatch(r"\S+ " + SID + r" 520000 block coordinator-bad-state", case.last_log() or "") is not None,
          True)

    # branch 5: a `true` older than the last request by more than 30 min is stale
    stale = (0, "block", "coordinator-stale")
    edge = "2026-11-03T12:00:00.000Z"
    edge_time = datetime.datetime(2026, 11, 3, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
    for age, want, name in ((1800, safe, "exactly 30 min older: allow"), (1801, stale, "30 min and 1 s older: block"),
                            (1799, safe, "29 min 59 s older: allow"), (-60, safe, "written after the request: allow")):
        lines = fresh(520_000)
        lines[-1] = line(END_TURN, 520_000, timestamp=edge)
        case = Case(root, lines, state=coordinator_state(safe="true"))
        os.utime(case.state, (edge_time - age, edge_time - age))
        check(f"coordinator + true, state file {name}", case.pre(), want)
    lines = fresh(520_000)
    lines[-1] = line(END_TURN, 520_000, timestamp="yesterday")
    check("coordinator + true, the last request has no usable timestamp: block (coordinator-stale)",
          Case(root, lines, state=coordinator_state(safe="true")).pre(), stale)
    lines[-1] = line(END_TURN, 520_000, timestamp=edge[:-1])
    check("coordinator + true, a timestamp without a zone: block (coordinator-stale)",
          Case(root, lines, state=coordinator_state(safe="true")).pre(), stale)
    lines = fresh(520_000)
    lines[-1] = line(END_TURN, 520_000, timestamp=edge)
    case = Case(root, lines, state=coordinator_state(safe="false"))
    os.utime(case.state, (edge_time - 7200, edge_time - 7200))
    check("coordinator + false and an old file: block with reason coordinator, not coordinator-stale",
          case.pre(), blocked)
    lines[-1] = line(END_TURN, 950_000, timestamp=edge)
    case = Case(root, lines, state=coordinator_state(safe="true"))
    os.utime(case.state, (edge_time - 7200, edge_time - 7200))
    check("coordinator + stale true at the cap: allow (coordinator-cap)", case.pre(), allowed)

    # no coordinator: the existing path, decision for decision
    check("coordinator: false + safe_to_compact: false at 260k: block (unsafe)",
          Case(root, fresh(), state=coordinator_state("false")).pre(), (0, "block", "unsafe"))
    check("coordinator: false + safe_to_compact: true at 260k: allow (safe)",
          Case(root, fresh(), state=coordinator_state("false", "true")).pre(), (0, "allow", "safe"))
    check("coordinator: false at 520k: allow (hard-cap)",
          Case(root, fresh(520_000), state=coordinator_state("false")).pre(), (0, "allow", "hard-cap"))
    check("coordinator: false, idle 46 min: allow (idle)",
          Case(root, fresh(260_000, minutes_ago=46), state=coordinator_state("false")).pre(), (0, "allow", "idle"))
    check("key absent at 260k: block (unsafe)", Case(root, fresh(), state=coordinator_state(None)).pre(),
          (0, "block", "unsafe"))
    check("key absent at 520k: allow (hard-cap)", Case(root, fresh(520_000), state=FALSE).pre(),
          (0, "allow", "hard-cap"))
    for value in ("yes", "1", "", "truee", "later"):
        check(f"coordinator: {value!r} is not true: the existing path (block, unsafe)",
              Case(root, fresh(), state=coordinator_state(value)).pre(), (0, "block", "unsafe"))
    check("a malformed value at 520k: allow (hard-cap)",
          Case(root, fresh(520_000), state=coordinator_state("yes")).pre(), (0, "allow", "hard-cap"))
    check("the key in the body, not the frontmatter: not a coordinator",
          Case(root, fresh(), state=FALSE + "coordinator: true\n").pre(), (0, "block", "unsafe"))
    check("the key after the closing --- line: not a coordinator",
          Case(root, fresh(), state="---\nsafe_to_compact: false\n---\ncoordinator: true\n").pre(),
          (0, "block", "unsafe"))
    check("coordinator: true but the frontmatter never closes, 520k: the existing path (allow, hard-cap)",
          Case(root, fresh(520_000), state="---\ncoordinator: true\nsafe_to_compact: false\n").pre(),
          (0, "allow", "hard-cap"))
    check("coordinator: true with no frontmatter at 260k: allow (no-state)",
          Case(root, fresh(), state="coordinator: true\nbody\n").pre(), (0, "allow", "no-state"))
    check("a state file that is not UTF-8: allow (no-state)",
          Case(root, fresh(), state=coordinator_state().encode("utf-8") + b"\xff\xfe").pre(),
          (0, "allow", "no-state"))
    case = Case(root, fresh(), state=None)
    os.makedirs(case.state)
    check("an unreadable state file (a folder): allow (no-state)", case.pre(), (0, "allow", "no-state"))
    check("state file missing at 260k: allow (no-state)", Case(root, fresh(), state=None).pre(),
          (0, "allow", "no-state"))
    check("an invalid session id: allow (bad-sid), no state is read",
          Case(root, fresh(), state=coordinator_state()).pre(session_id="../" + SID), (0, "allow", "bad-sid"))

    # the checks before the coordinator rule still win
    check("coordinator, subagent event: allow", Case(root, fresh(520_000), state=coordinator_state()).pre(agent_id="a1"),
          (0, "allow", "agent"))
    check("coordinator, manual: allow", Case(root, fresh(520_000), state=coordinator_state()).pre(trigger="manual"),
          (0, "allow", "manual"))
    check("coordinator, below the floor: allow", Case(root, fresh(150_000), state=coordinator_state()).pre(),
          (0, "allow", "below-floor"))
    case = Case(root, fresh(520_000), state=coordinator_state())
    check("coordinator, no transcript: allow", case.pre(transcript_path=os.path.join(case.folder, "nope.jsonl")),
          (0, "allow", "no-transcript"))
    check("coordinator, garbage stdin: allow, no output",
          Case(root, fresh(520_000), state=coordinator_state()).run("pre", b"nope")[:2], (0, ""))

    # a session without the key under the hard cap the plugin passes (560 000)
    check("no key at 520k, default cap: allow (hard-cap)", Case(root, fresh(520_000), state=FALSE).pre(),
          (0, "allow", "hard-cap"))
    check("no key at 520k, --hard-cap 560000: block (unsafe)",
          Case(root, fresh(520_000), state=FALSE).pre("--hard-cap", "560000"), (0, "block", "unsafe"))
    check("no key at 520k, --hard-cap 560000, safe_to_compact: true: allow (safe)",
          Case(root, fresh(520_000), state=TRUE).pre("--hard-cap", "560000"), (0, "allow", "safe"))
    check("no key at 559 999, --hard-cap 560000: block", Case(root, fresh(559_999), state=FALSE)
          .pre("--hard-cap", "560000"), (0, "block", "unsafe"))
    check("no key at 560 000, --hard-cap 560000: allow", Case(root, fresh(560_000), state=FALSE)
          .pre("--hard-cap", "560000"), (0, "allow", "hard-cap"))

    # post keeps the key: the coordinator is still one after its compaction
    front = "---\ncoordinator: true\nsafe_to_compact: true\nupdated: 2026-11-01T00:00:00+00:00\n---\n"
    case = Case(root, fresh(520_000), state=front + "## Next step\nmerge\n")
    code, out, err = case.run("post", {"session_id": SID, "transcript_path": case.transcript,
                                       "hook_event_name": "PostCompact", "trigger": "auto", "compact_summary": "s"})
    after = read_bytes(case.state).decode("utf-8").split("\n")
    check("post: coordinator: true is kept, safe_to_compact is reset to false",
          ((code, out), after[:3], after[4:]),
          ((0, ""), ["---", "coordinator: true", "safe_to_compact: false"], ["---", "## Next step", "merge", ""]))
    check("after post the session is still a coordinator: block at 520k", case.pre(), blocked)


def hooks_checks(root):
    """The PreCompact command the plugin ships: both flags are on it and they take effect."""
    with open(HOOKS_JSON, encoding="utf-8") as fh:
        groups = json.load(fh)["hooks"]["PreCompact"]
    commands = [handler["command"] for group in groups for handler in group["hooks"]]
    check("hooks.json: one PreCompact command", len(commands), 1)
    command = commands[0]
    match = re.fullmatch(r'sh "\$\{CLAUDE_PLUGIN_ROOT\}/bin/py\.sh" -S "\$\{CLAUDE_PLUGIN_ROOT\}/tools/context-guard/'
                         r'compact_guard\.py" pre(?P<flags>( --[a-z-]+ \d+)*) \|\| true', command)
    check("hooks.json: the PreCompact command keeps its shape and the `|| true`", match is not None, True)
    flags = match.group("flags").split() if match else []
    check("hooks.json: the PreCompact command carries --hard-cap 560000 and --coordinator-cap 650000",
          flags, ["--hard-cap", "560000", "--coordinator-cap", "650000"])
    check("shipped flags: a coordinator + false at 649 999 blocks",
          Case(root, fresh(649_999), state=coordinator_state()).pre(*flags), (0, "block", "coordinator"))
    check("shipped flags: a coordinator + false at 650 000 is allowed",
          Case(root, fresh(650_000), state=coordinator_state()).pre(*flags), (0, "allow", "coordinator-cap"))
    check("shipped flags: a coordinator + true at 520k is allowed",
          Case(root, fresh(520_000), state=coordinator_state(safe="true")).pre(*flags), (0, "allow", "coordinator-safe"))
    check("shipped flags: a side session at 520k blocks (unsafe)",
          Case(root, fresh(520_000), state=FALSE).pre(*flags), (0, "block", "unsafe"))
    check("shipped flags: a side session at 560 000 is allowed (hard-cap)",
          Case(root, fresh(560_000), state=FALSE).pre(*flags), (0, "allow", "hard-cap"))


def post_checks(root):
    body =("## Goal\r\nnaïve façade — Grüße\n---\nsafe_to_compact: true\n\n## Next step\nmerge\n"
            "trailing without newline").encode("utf-8")
    front = b"---\nsafe_to_compact: true\nupdated: 2026-11-01T00:00:00+00:00\nowner: main\n---\n"
    case = Case(root, fresh(), state=front + body)
    summary = "This session is being continued.\nSummary: Grüße\n"
    code, out, err = case.run("post", {"session_id": SID, "transcript_path": case.transcript,
                                       "hook_event_name": "PostCompact", "trigger": "auto",
                                       "compact_summary": summary})
    check("post: prints nothing, exit 0", (code, out), (0, ""))
    saved = read_bytes(os.path.join(case.state_dir, SID + ".summary.md")).decode("utf-8")
    head, _, rest = saved.partition("\n")
    check("post: the summary file starts with `ts trigger`",
          re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00 auto", head) is not None, True)
    check("post: the summary is saved as given", rest, summary)
    after = read_bytes(case.state)
    check("post: the body is byte-identical", after.endswith(b"\n---\n" + body), True)
    lines = after[:len(after) - len(body)].decode("utf-8").split("\n")
    lines += [""] * (6 - len(lines))
    check("post: the frontmatter is reset to false with a fresh updated, other keys kept",
          (lines[:2], re.fullmatch(r"updated: \d{4}-\d\d-\d\dT[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00", lines[2]) is not None,
           lines[2] != "updated: 2026-11-01T00:00:00+00:00", lines[3:]),
          (["---", "safe_to_compact: false"], True, True, ["owner: main", "---", ""]))
    check("post: no temp file is left behind", sorted(os.listdir(case.state_dir)),
          sorted([SID + ".md", SID + ".summary.md"]))

    crlf = b"\xef\xbb\xbf---\r\nupdated: old\r\n---\r\nbody\r\n"
    case = Case(root, fresh(), state=crlf)
    case.run("post", {"session_id": SID, "trigger": "auto"})
    after = read_bytes(case.state)
    check("post: BOM and CRLF are kept, a missing key is added",
          (after.startswith(b"\xef\xbb\xbf---\r\nupdated: 20"), b"\r\nsafe_to_compact: false\r\n---\r\nbody\r\n"
           in after, after.count(b"\n") == after.count(b"\r\n")), (True, True, True))
    check("post: without compact_summary no summary file", os.path.exists(case.state[:-3] + ".summary.md"), False)

    for name, state in (("no frontmatter", b"safe_to_compact: true\nbody\n"),
                        ("an unclosed frontmatter", b"---\nsafe_to_compact: true\nbody\n"),
                        ("non-UTF-8", b"---\nsafe_to_compact: true\n---\nbody \xff\n"),
                        ("no state file", None)):
        case = Case(root, fresh(), state=state)
        case.run("post", {"session_id": SID, "trigger": "manual", "compact_summary": "s"})
        check(f"post: {name}: the state file is untouched, the summary is still saved",
              (read_bytes(case.state) if state else os.path.exists(case.state),
               read_bytes(case.state[:-3] + ".summary.md").partition(b"\n")[2]), (state or False, b"s\n"))
    case = Case(root, fresh(), state=TRUE)
    case.run("post", {"session_id": SID, "trigger": "auto", "compact_summary": ""})
    check("post: an empty summary writes no summary file", os.path.exists(case.state[:-3] + ".summary.md"), False)
    case = Case(root, fresh(), state=TRUE)
    case.run("post", {"session_id": SID, "trigger": "auto\nforged line", "compact_summary": "s"})
    check("post: an unknown trigger is written as -",
          read_bytes(case.state[:-3] + ".summary.md").split(b"\n")[0].endswith(b"+00:00 -"), True)
    missing = os.path.join(case.folder, "new", "state")
    run("post", json.dumps({"session_id": SID, "trigger": "auto", "compact_summary": "s"}).encode("utf-8"),
        "--state-dir", missing)
    check("post: a missing state dir is created for the summary",
          os.path.isfile(os.path.join(missing, SID + ".summary.md")), True)
    case = Case(root, fresh(), state=TRUE)
    os.makedirs(case.state[:-3] + ".summary.md")
    case.run("post", {"session_id": SID, "trigger": "auto", "compact_summary": "s"})
    check("post: a failed rewrite leaves no temp file", sorted(os.listdir(case.state_dir)),
          sorted([SID + ".md", SID + ".summary.md"]))
    check("post: a failed summary write still resets the state file", read_bytes(case.state).startswith(
        b"---\nsafe_to_compact: false\n"), True)
    case = Case(root, fresh(), state=TRUE)
    os.chmod(case.state, stat.S_IREAD)
    try:
        result = case.run("post", {"session_id": SID, "trigger": "auto", "compact_summary": "kept\n"})[:2]
        check("post: a read-only state file: exit 0, file unchanged, the summary is saved",
              (result, read_bytes(case.state), read_bytes(case.state[:-3] + ".summary.md").partition(b"\n")[2],
               sorted(os.listdir(case.state_dir))),
              ((0, ""), TRUE.encode(), b"kept\n", sorted([SID + ".md", SID + ".summary.md"])))
    finally:
        os.chmod(case.state, stat.S_IREAD | stat.S_IWRITE)

    for name, event in (("agent_id", {"session_id": SID, "agent_id": "a1", "compact_summary": "s"}),
                        ("a bad session id", {"session_id": "a/b", "compact_summary": "s"})):
        case = Case(root, fresh(), state=TRUE)
        code, out, err = case.run("post", event)
        check(f"post: {name} changes nothing", ((code, out), sorted(os.listdir(case.state_dir)),
                                                 read_bytes(case.state)), ((0, ""), [SID + ".md"], TRUE.encode()))
    case = Case(root, fresh(), state=TRUE)
    check("post: garbage stdin changes nothing", (case.run("post", b"{")[:2], read_bytes(case.state)),
          ((0, ""), TRUE.encode()))


def start_context(case, **changes):
    event = {"session_id": SID, "transcript_path": case.transcript, "hook_event_name": "SessionStart",
             "source": "compact"}
    event.update(changes)
    code, out, err = case.run("start", event)
    if code != 0:
        return f"exit {code}"
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


def header_of(case):
    return f"This session was compacted. Its state file is {case.state}; its content follows."


def cut_header_of(case, size):
    return (f"This session was compacted. Its state file is {case.state}; its {size} characters do not fit "
            f"here (9 500), so only its head follows: read the file, then move history to {SID}.log.md "
            "until it fits.")


def start_checks(root):
    state = ("---\nsafe_to_compact: true\nupdated: x\n---\n# Session state\n\n## Goal\nship\n\n"
             "## Next step\nrun the gates\n#1 priority\n### detail\nthen merge\n\n## Decisions\nnone\n")
    case = Case(root, fresh(), state=state)
    check("start: Next step first, then the rest of the body, without the frontmatter", start_context(case),
          header_of(case) + "\n\n## Next step\nrun the gates\n#1 priority\n### detail\nthen merge\n\n"
          "# Session state\n\n## Goal\nship\n\n## Decisions\nnone")
    for fence in ("```", "~~~~"):
        next_step = f"## Next step\nrun:\n{fence}bash\n# install deps\nnpm ci\n{fence}\nthen merge"
        case = Case(root, fresh(), state=f"## Goal\nship\n\n{next_step}\n\n## Decisions\nnone\n")
        check(f"start: a # line inside a {fence} fence does not end the Next step section", start_context(case),
              header_of(case) + "\n\n" + next_step + "\n\n## Goal\nship\n\n## Decisions\nnone")
    keep = ("## Keep verbatim\r\nError: `ENOENT: no such file, open '/x/y.json'`  \r\n"
            "- tools/a.py:42 raises it; run `python -I t.py --only edge`\r\n```\r\n# not a heading\r\n```\r\n"
            "- decision: keep CRLF, because the user edits it in Notepad")
    next_step = "## Next step\nfix a.py:42"
    case = Case(root, fresh(), state=f"---\nsafe_to_compact: true\n---\n## Goal\nship\n\n{keep}\r\n\r\n"
                                     f"## Lessons\nnone\n\n{next_step}\n\n## Decisions\nnone\n")
    check("start: Next step first, Keep verbatim second and byte-for-byte, then the rest", start_context(case),
          header_of(case) + "\n\n" + next_step + "\n\n" + keep + "\n\n## Goal\nship\n\n## Lessons\nnone\n\n"
          "## Decisions\nnone")
    case = Case(root, fresh(), state="## Next step\nmerge\n### Keep verbatim\nexit 3\n## Goal\nship\n")
    check("start: a Keep verbatim section inside Next step is not repeated",
          (start_context(case) or "").partition("follows.\n\n")[2],
          "## Next step\nmerge\n### Keep verbatim\nexit 3\n\n## Goal\nship")
    case = Case(root, fresh(), state="# Keep verbatim\nexit 3\n## Next step\nmerge\n# Goal\nship\n")
    check("start: a Next step section inside Keep verbatim is not repeated",
          (start_context(case) or "").partition("follows.\n\n")[2],
          "## Next step\nmerge\n\n# Keep verbatim\nexit 3\n# Goal\nship")
    case = Case(root, fresh(), state="---\nsafe_to_compact: true\n---\n## Goal\nship\n\n## Next steps:\nmerge\n")
    check("start: a last Next steps section runs to the end",
          (start_context(case) or "").partition("follows.\n\n")[2], "## Next steps:\nmerge\n\n## Goal\nship")
    case = Case(root, fresh(), state="## Goal\nship\n")
    check("start: no Next step and no frontmatter: the body as written",
          (start_context(case) or "").partition("follows.\n\n")[2], "## Goal\nship")

    big = "---\nsafe_to_compact: true\n---\n## Goal\n" + "goal text\n" * 3600 + "## Next step\nmerge now\n" \
          + "## Lessons\n" + "lesson\n" * 2000
    check("fixture: the body is about 50k chars", 49_000 < len(big) < 52_000, True)
    case = Case(root, fresh(), state=big)
    text = start_context(case) or ""
    marker = f"\n[truncated: read {case.state}]"
    check("start: a 50k body is cut to at most 9 500 chars, marker last, Next step first",
          (len(text) <= 9_500, len(text) > 9_000, text.endswith(marker),
           text.partition("\n\n")[2].startswith("## Next step\nmerge now\n\n## Goal\ngoal text\n")),
          (True, True, True, True))
    ordered = "## Next step\nmerge now\n\n## Goal\n" + "goal text\n" * 3600 + "## Lessons\n" + "lesson\n" * 1999 + "lesson"
    check("start: a cut is announced first, with the size of the whole body and the log file for history",
          text.partition("\n\n")[0], cut_header_of(case, len(ordered)))
    case = Case(root, fresh(), state=None)
    fits = "x" * (9_500 - len(header_of(case)) - 2)
    write_bytes(case.state, fits.encode("utf-8"))
    check("start: a context of exactly 9 500 chars is not cut", start_context(case), header_of(case) + "\n\n" + fits)
    write_bytes(case.state, (fits + "x").encode("utf-8"))
    text = start_context(case) or ""
    check("start: one char more is cut and announced",
          (len(text), text.partition("\n\n")[0], text.endswith(f"\n[truncated: read {case.state}]")),
          (9_500, cut_header_of(case, len(fits) + 1), True))
    keep_big = "## Keep verbatim\n" + "".join(f"- src/m{n}.py:{n} `err {n}`\n" for n in range(60)).rstrip()
    big = "## Goal\n" + "goal text\n" * 3600 + "## Next step\nmerge now\n" + "## Lessons\n" + "lesson\n" * 2000 \
          + keep_big + "\n"
    check("fixture: the Keep verbatim body is at the very end of a 50k body",
          (49_000 < len(big) < 54_000, big.endswith(keep_big + "\n")), (True, True))
    case = Case(root, fresh(), state=big)
    text = start_context(case) or ""
    check("start: a 50k body keeps Next step and Keep verbatim whole and cuts the rest",
          (len(text) <= 9_500, text.endswith(f"\n[truncated: read {case.state}]"),
           text.partition("\n\n")[2].startswith("## Next step\nmerge now\n\n" + keep_big + "\n\n## Goal\n")),
          (True, True, True))
    case = Case(root, fresh(), state="## Next step\n" + "\U0001F600" * 6_000)
    text = start_context(case) or ""
    check("start: the cap counts UTF-16 units, as the 10 000-char limit does",
          (len(text.encode("utf-16-le")) // 2 <= 9_500, text.endswith(f"[truncated: read {case.state}]")),
          (True, True))

    case = Case(root, fresh(), state=None)
    check("start: no state file", start_context(case),
          f"This session was compacted. No state file exists at {case.state}.")
    for name, changes in (("agent_id", {"agent_id": "a1"}), ("a bad session id", {"session_id": "a b"}),
                          ("source startup", {"source": "startup"})):
        check(f"start: {name}: nothing", start_context(Case(root, fresh(), state=state), **changes), None)
    case = Case(root, fresh(), state=state)
    start_context(case)
    check("start: never writes", (sorted(os.listdir(case.state_dir)), read_bytes(case.state)),
          ([SID + ".md"], state.encode("utf-8")))
    check("start: garbage stdin: nothing", case.run("start", b"nope")[:2], (0, ""))


def config_checks(root):
    """The state dir without --state-dir: `$CLAUDE_CONFIG_DIR/state`, else `~/.claude/state`; started from
    another cwd, where only `python -I` and the script's own sys.path insert find context_guard."""
    elsewhere = os.path.join(root, "other-cwd")
    os.makedirs(elsewhere)
    home = os.path.join(root, "home")
    config = os.path.join(root, "elsewhere")
    case = Case(root, fresh(), state=None)
    stdin = json.dumps(case.event()).encode("utf-8")

    env = base_env(USERPROFILE=home, HOME=home, CLAUDE_CONFIG_DIR=config)
    state = write_bytes(os.path.join(config, "state", SID + ".md"), FALSE.encode("utf-8"))
    code, out, err = run("pre", stdin, env=env, cwd=elsewhere)
    check("CLAUDE_CONFIG_DIR: the state file under it decides, from another cwd",
          (code, json.loads(out) if out else out),
          (0, {"decision": "block", "reason": f"compaction postponed at 260k: {state} has safe_to_compact: false"}))
    check("CLAUDE_CONFIG_DIR: the log goes under it",
          (read_bytes(os.path.join(config, "state", LOG)).split(b" ")[-2:],
           os.path.exists(os.path.join(home, ".claude"))), ([b"block", b"unsafe\n"], False))

    env = base_env(USERPROFILE=home, HOME=home)
    state = write_bytes(os.path.join(home, ".claude", "state", SID + ".md"), FALSE.encode("utf-8"))
    code, out, err = run("pre", stdin, env=env, cwd=elsewhere)
    check("CLAUDE_CONFIG_DIR unset: ~/.claude/state decides, from another cwd",
          (code, json.loads(out) if out else out),
          (0, {"decision": "block", "reason": f"compaction postponed at 260k: {state} has safe_to_compact: false"}))
    check("CLAUDE_CONFIG_DIR unset: the log goes to ~/.claude/state",
          read_bytes(os.path.join(home, ".claude", "state", LOG)).split(b" ")[-2:], [b"block", b"unsafe\n"])

    env = base_env(USERPROFILE=home, HOME=home, CLAUDE_CONFIG_DIR="")
    os.remove(os.path.join(config, "state", SID + ".md"))
    code, out, err = run("pre", stdin, env=env, cwd=elsewhere)
    check("an empty CLAUDE_CONFIG_DIR means ~/.claude", (code, out != ""), (0, True))

    code, out, err = run("pre", stdin, "--state-dir", case.state_dir, cwd=elsewhere)
    check("--state-dir wins over CLAUDE_CONFIG_DIR (the stand-in config dir stays untouched)",
          (code, os.path.exists(CONFIG), os.path.isfile(case.log)), (0, False, True))


# one writer process: loads the real script, waits for the shared start time, then logs through `_log`
WRITER = """import importlib.util, sys, time
script, folder, name, start, count = sys.argv[1:]
spec = importlib.util.spec_from_file_location("compact_guard", script)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
while time.time() < float(start):
    pass
for index in range(int(count)):
    guard._log(folder, "w" + name, index, "allow", "race")
"""


def race_report(data, writers, per_writer):
    """(lines missing or repeated, torn lines, writer switches) of a log whose lines are
    `ts w<writer> <index> allow race`; every (writer, index) pair must appear exactly once."""
    pieces = data.split(b"\n")
    torn = 1 if pieces.pop() else 0   # bytes after the last newline
    seen, switches, last = {}, 0, None
    for piece in pieces:
        match = re.fullmatch(rb"\S+ w(\d+) (\d+) allow race", piece)
        if match is None:
            torn += 1
            continue
        seen[match.groups()] = seen.get(match.groups(), 0) + 1
        switches += match.group(1) != last
        last = match.group(1)
    wrong = sum(1 for w in range(writers) for i in range(per_writer)
                if seen.get((b"%d" % w, b"%d" % i)) != 1)
    return wrong, torn, switches


def race_checks(root):
    """Overlapping writers through the real `_log`: no line lost, repeated or torn."""
    writers, per_writer = 8, 300
    total = writers * per_writer
    sequential = b"".join(b"2026-11-04T00:00:00+00:00 w%d %d allow race\n" % (w, i)
                          for w in range(writers) for i in range(per_writer))
    wrong, torn, switches = race_report(sequential, writers, per_writer)
    check("race control: a log written one writer after another is complete and whole",
          (wrong, torn), (0, 0))
    check("race control: that sequential log does not count as overlapping writers",
          switches >= total / 4, False)
    first, second, rest = sequential.split(b"\n", 2)
    damaged = second + b"\n" + second + b"\n" + rest + b"2026-11-04T00:00:00+00:00 w1 4"
    check("race control: a missing line, a repeated line and a torn tail are all reported",
          race_report(damaged, writers, per_writer)[:2], (2, 1))

    folder = os.path.join(root, "race")
    os.makedirs(folder)
    start = str(time.time() + 1.5)
    procs = [subprocess.Popen([sys.executable, "-I", "-S", "-c", WRITER, SCRIPT, folder, str(w), start,
                               str(per_writer)], stderr=subprocess.PIPE) for w in range(writers)]
    errors = [proc.communicate(timeout=60)[1].decode("utf-8", "replace") for proc in procs]
    check("race: every writer exits 0 without output on stderr",
          [(proc.returncode, err) for proc, err in zip(procs, errors)], [(0, "")] * writers)
    data = read_bytes(os.path.join(folder, LOG))
    wrong, torn, switches = race_report(data, writers, per_writer)
    check("race: every line is present exactly once, none torn", (wrong, torn, len(data.splitlines())),
          (0, 0, total))
    check("race: the writers overlapped (a run without overlap proves nothing)", switches >= total / 4, True)


def main():
    global CONFIG
    root = tempfile.mkdtemp(prefix="compact-guard-")
    CONFIG = os.path.join(root, "config")
    try:
        pre_checks(root)
        coordinator_checks(root)
        hooks_checks(root)
        post_checks(root)
        start_checks(root)
        config_checks(root)
        race_checks(root)
        case = Case(root, fresh(), state=TRUE)
        check("an unknown command: nothing, exit 0",
              case.run("other", {"session_id": SID, "source": "compact", "trigger": "auto"})[:2], (0, ""))
    finally:
        shutil.rmtree(root)
    print(f"# compact-guard: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


sys.exit(main())
