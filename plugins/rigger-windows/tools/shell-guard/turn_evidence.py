"""turn_evidence: what a Claude Code transcript proves about a commit or a turn.

A pure reader shared by the commit rule, the Stop gate and the gate lab. Stdlib only. shell_guard imports it on a
hot path, so importing does no I/O and compiles nothing: patterns stay strings and `re` compiles them on first use.

A stream is one transcript file: the session's main file or a subagent's. Timestamps are compared as text, which
holds because every record carries the same `toISOString` form.
"""
import calendar
import hashlib
import json
import os
import re
import time

CHUNK = 1 << 20
OUTPUT_TAIL = 3000
WINDOWS = os.name == "nt"

EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
BROWSER_TOOLS = (
    "mcp__Claude_Browser__", "mcp__claude-in-chrome__", "mcp__plugin_chrome-devtools-mcp_", "mcp__plugin_browser-use_",
)
NOT_HUMAN = ("<task-notification", "<command-", "<local-command", "<ci-monitor-event", "[Request interrupted by user")

STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z?$"
HERE_DOC = r"(?sm)@'.*?'@|@\".*?\"@|<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$"
LINE_CONTINUATION = r"[\\`]\r?\n"
QUOTED = r"'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\""
REDIRECT = r">&|&>"
TOKEN = (
    r"(?m)(?P<quoted>" + QUOTED + r")|(?P<redirect>" + REDIRECT + r")|(?P<comment>(?:^|(?<=\s))#.*$)"
    r"|(?P<op>&&|\|\||[;\n|&])"
)
IN_QUOTES_SPLIT = r"[;|&]"
PREFIX = r"^\s*(?:@?\(|\$\w+\s*=\s*|env\s+|\w+=\S+\s+|time\s+|command\s+|exec\s+)+"
CONTROL = (
    r"(?i)^(?:(?:then|do|else)\s+|\{\s*|(?:if|elseif|while|foreach|try|catch|finally)\b[^{]*\{\s*"
    r"|(?:if|elif|while|until)\b\s*)+"
)
DETACHED = r"(?i)^(?:Start-Process|Start-Job|Start-ThreadJob|saps|nohup)\s+(?:-(?:FilePath|ScriptBlock)\s+)?\{?\s*"
GIT_COMMIT = r"(?is)^git(?:\.exe)?(?:\s+-c\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+commit\b(.*)$"

MEASURED = r"(?i)^Measure-Command\s*\{\s*"

RUNNER = (
    r"(?i)^(?:Q|\$php\w*|(?:\S*[\\/])?(?:python\S*|py|node|pwsh|powershell|php\S*|sh|bash|npx|pnpm|npm|yarn|bun|composer|timeout"
    r"|wsl)(?:\.exe|\.cmd)?)(?:\s|$)"
)
NOT_A_RUN = (
    r"(?:^|\s)(?:--version|--help|-h|-V|--list\S*|--collect-only)(?:\s|$)"
    r"|(?i:^(?:python\S*|py|node|\$?php\S*)(?:\s+-X\s+\S+|\s+-\S+)*?\s+-[cer]\s+Q)"
    r"|(?i:^(?:\S+\s+-m\s+)?(?:pip|npm|pnpm|yarn|bun|composer)(?:\s+(?:--filter|-F)\s+\S+|\s+-\S+)*"
    r"\s+(?:install|i|add|require|update|upgrade|uninstall|remove|view|ls|list|why|outdated|show|info)(?:\s|$))"
)
PATH_ARG = r"(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)"
REDIRECT_TARGET = r"[\d*]?>>?&?\s*" + PATH_ARG
OPTION_NAME = r"^-[\w-]*="
LEAVES_FOLDER = r"^(?:[a-zA-Z]:)?[\\/]|(?:^|[\\/])\.\.(?:[\\/]|$)"
DIR_WORD = r"(?:cd|chdir|sl|Set-Location|Push-Location|pushd|popd|Pop-Location)(?:\s|$)"
DIR_CHANGE = r"(?i)^(?:(?:then|do|else)\s+)?" + DIR_WORD
BLOCK_MOVE = r"(?i)\{\s*" + DIR_WORD
BLOCK_OPEN = r"(?i)\{|^(?:then|do)\b(?!\s*\{)"
BLOCK_CLOSE = r"(?i)\}|^(?:fi|done)\b"
CD =r"(?i)^(?:cd|chdir|sl|Set-Location|Push-Location|pushd)\s+(?:--\s+|-(?:LiteralPath|Path|P|L)\s+)?" + PATH_ARG + r"\s*$"
UNRESOLVED = r"[$~%`*?]|^-$"
UNRESOLVED_START = r"[$~%]"
RUN_ELSEWHERE = (
    r"(?:^|\s)(?:--prefix|--cwd|--cd|--dir|--directory|--root|--rootdir|--working-dir|--manifest-path|-C)(?:[=\s]|$)"
    r"|(?i:(?:^|\s)(?:cd|chdir|Set-Location|Push-Location|pushd)\s)"
)

CURL = r"(?i)^curl(?:\.exe)?(?:\s|$)"
CURL_NO_BODY = r"(?<![2-9*])>|\s(?:--version|--help|-V|-h)(?:\s|$)"
CURL_TO_FILE = r"\s(?:-[a-zA-Z]*[oO]|--output|--remote-name)(?:[\s=]|$)"
CURL_WRITE_OUT = r"\s(?:-[a-zA-Z]*w|--write-out)(?:[\s=]|$)"

EXIT_CODE = r"\s*(?:Command failed with exit code|Exit code) -?[1-9]"
BACKGROUNDED = (
    r"\s*Command (?:running in|was moved to the|did not complete within its \d+s timeout and was moved to the) background"
)
PERSISTED = r"\s*<persisted-output>"
NO_OUTPUT = r"\(\w+ completed with no output\)"
FAIL_SUMMARY = (
    r"(?m)FAILURES!|ERRORS!|^\s*(?:#|\u2139) fail [1-9]|\b[1-9]\d* (?:tests? )?(?:failed|failing|failures?)\b"
    r"|\b[1-9]\d* errors?\b|\b[Ff]ailed:? [1-9]|\b(?:Failures|Errors): [1-9]|^FAILED\b|^FAIL\s"
    r"|\bTraceback \(most recent call last\)"
)
OK_SUMMARY = (
    r"(?m)^\s*OK \([1-9]\d* tests?\b|^\s*OK, but .*\n\s*Tests: [1-9]|^Ran [1-9]\d* tests? in \S+\s+OK\b"
    r"|(?<!of )(?<!/)\b[1-9]\d* (?:tests? )?passed\b|^\s*(?:#|\u2139) pass [1-9]|\btests [1-9]\d*, failed 0\b"
    r"|\ball [1-9]\d* checks passed\b"
)


class Pass:
    """The latest passing check: `ts` is its tool_use timestamp, "" when none was found; `reason` is ok, unreadable
    or budget. `hidden` is the stamp of the newest check it cannot read, "" when none turned up; the scan ends at the
    pass, so one older than the pass may go unseen."""
    __slots__ = ("ts", "reason", "hidden")

    def __init__(self, ts, reason, hidden=""):
        self.ts = ts
        self.reason = reason
        self.hidden = hidden


def norm(path):
    """The form `last_pass` compares: on Windows lower-case with forward slashes, MSYS /f/x as its drive path."""
    path = (path or "").strip().strip("'\"")
    if WINDOWS:
        path = path.replace("\\", "/")
        path = re.sub(r"^/([a-zA-Z])/", r"\1:/", path).lower()
    return re.sub(r"/+", "/", path).rstrip("/")


def _resolve(path, base):
    path = norm(path)
    if not re.match(r"[a-zA-Z]:/|/", path):
        path = norm(base + "/" + path)
    kept = []
    for part in path.split("/"):
        if part != "." and part != "..":
            kept.append(part)
        elif part == ".." and len(kept) > 1:
            kept.pop()
    return "/".join(kept)


def _path_args(raw):
    """The arguments of a stage as typed that can point outside its folder: absolute, or climbing with `..`. The first
    word is the program; a redirect target is no argument."""
    words = re.findall(PATH_ARG, re.sub(REDIRECT_TARGET, " ", re.sub(PREFIX, "", raw)))
    values = [re.sub(OPTION_NAME, "", word).strip("'\"") for word in words[1:]]
    return [value for value in values if re.search(LEAVES_FOLDER, value)]


def _check_folders(command, cwd, checks):
    """Where the command's last check ran: its folder, then any path outside it that the check regexes name or its
    arguments reach. Empty when the command does not say.

    Each `cd` before the check moves it, so a group closed after a `cd` takes that `cd` away. A `cd` in a block may not
    have run and a backgrounded one moves nothing here, so either gives no folder.
    """
    stages = _stages(command)
    last = max(index for index, stage in enumerate(stages) if _runs_check(stage, checks))
    base = norm(cwd)
    depth = cd_depth = block = 0
    moved = False
    for stage in stages[:last]:
        typed = re.sub(QUOTED, "", stage[2])
        depth += typed.count("(") - typed.count(")")
        move = re.sub(PREFIX, "", stage[2])
        if re.search(BLOCK_MOVE, typed):
            return []
        if re.match(DIR_CHANGE, move):
            found = re.match(CD, move)
            if block or not found or re.search(UNRESOLVED, found.group(1)):
                return []
            base = _resolve(found.group(1), base)
            cd_depth = max(cd_depth, depth)
            moved = True
        if depth < cd_depth or (moved and stage[3] == "&"):
            return []
        moved = moved and stage[3] == "&&"
        block += len(re.findall(BLOCK_OPEN, typed)) - len(re.findall(BLOCK_CLOSE, typed))
    plain = stages[last][1]
    if re.search(RUN_ELSEWHERE, plain):
        return []
    named = [word for word in plain.split() if any(re.search(check, word, flags=re.I) for check in checks)]
    if any(re.match(UNRESOLVED_START, word) for word in named):
        return []
    named += _path_args(stages[last][2])
    outside =[path for path in (_resolve(word, base) for word in named) if not (path + "/").startswith(base + "/")]
    return [base] + outside


def _repo_root(path, cache):
    """Nearest ancestor holding .git, or path itself when none exists on disk any more."""
    parts = path.split("/")
    for end in range(len(parts), 1, -1):
        folder = "/".join(parts[:end])
        if folder not in cache:
            cache[folder] = os.path.exists(folder + "/.git")
        if cache[folder]:
            return folder
    return path


def stream_paths(transcript_path, agent_id):
    """The session's streams, the calling one first. Workflow agents sit in folders below `subagents`.

    `transcript_path` is the main file even inside a subagent. A stream that does not exist stays in the list, so
    the reader reports it instead of passing over it.
    """
    if not transcript_path:
        return []
    subagents = os.path.join(os.path.splitext(transcript_path)[0], "subagents")
    others = [transcript_path]
    for folder, _, names in os.walk(subagents):
        others.extend(os.path.join(folder, name) for name in names
                      if name.startswith("agent-") and name.endswith(".jsonl"))
    if not agent_id:
        return others
    name = "agent-%s.jsonl" % agent_id
    calling = [path for path in others if os.path.basename(path) == name] or [os.path.join(subagents, name)]
    return calling + [path for path in others if path != calling[0]]


def reversed_lines(path):
    """Yield a file's raw lines, last to first, reading CHUNK bytes at a time."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        tail = b""
        while position > 0:
            size = min(CHUNK, position)
            position -= size
            handle.seek(position)
            parts = (handle.read(size) + tail).split(b"\n")
            tail = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line
        if tail:
            yield tail


def _result_text(block):
    body = block.get("content")
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return "\n".join(item["text"] for item in body if isinstance(item, dict) and isinstance(item.get("text"), str))
    return ""


def _record(line, kinds):
    """A line as a dict of one of `kinds` with a usable timestamp, else None: junk is skipped."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("type") not in kinds:
        return None
    stamp = record.get("timestamp")
    return record if isinstance(stamp, str) and re.match(STAMP, stamp) else None


def _well_formed(use):
    return isinstance(use.get("id"), str) and isinstance(use.get("name"), str) and isinstance(use.get("input"), dict)


def _opens_turn_by(line, cut):
    """True for a line that opens a turn no later than `cut`."""
    if b'"type":"user"' not in line:
        return False
    record = _record(line, ("user",))
    return record is not None and record["timestamp"] <= cut and turn_start(record, _first_text(record))


def _tool_calls(path, after, calling=False):
    """Tool calls of one stream from `after` on, newest first.

    Stamps are not in strict order, so an old record among new ones is passed over and the walk reads to the head of
    the stream, except in the `calling` stream, where it ends at the last turn start no newer than `after`. Yields
    (stamp, cwd, tool_use block, result), `stamp` being the tool_use record's time and `result` being (is_error,
    output, the result record's time), or None while the call is pending.
    """
    results = {}
    for line in reversed_lines(path):
        if calling and _opens_turn_by(line, after):
            return
        if b'"tool_use' not in line and b'"tool_result"' not in line:
            continue
        if b'"type":"user"' not in line and b'"type":"assistant"' not in line:
            continue
        record = _record(line, ("user", "assistant"))
        if record is None or not isinstance(record.get("message"), dict):
            continue
        stamp = record["timestamp"]
        if stamp < after:
            continue
        content = record["message"].get("content")
        if not isinstance(content, list):
            continue
        cwd = record.get("cwd")
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                results[block["tool_use_id"]] = (bool(block.get("is_error")), _result_text(block), stamp)
            elif block.get("type") == "tool_use" and _well_formed(block):
                yield stamp, cwd if isinstance(cwd, str) else "", block, results.pop(block["id"], None)


def _holds_result(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result"
                                             for block in content)


def turn_start(record, text):
    """True for a user line that opens a turn; `text` is its first text. A task notification continues the turn.

    A subagent's tool_result line carries no `toolUseResult`, so its content is read too. A slash command stays
    refused even when it opens a model turn: its line looks the same as one that does not.
    """
    if record.get("isMeta") or record.get("isCompactSummary") or "toolUseResult" in record or _holds_result(record):
        return False
    if record.get("turnOrigin") == "task_notification":
        return False
    return not text.lstrip().startswith(NOT_HUMAN)


def _first_text(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    for block in content if isinstance(content, list) else ():
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
    return ""


def turn_start_ts(path):
    """The timestamp of the last turn start in one stream, "" when it holds none. An unreadable stream raises OSError,
    as in turn_changes."""
    for line in reversed_lines(path):
        if b'"type":"user"' not in line:
            continue
        record = _record(line, ("user",))
        if record is not None and turn_start(record, _first_text(record)):
            return record["timestamp"]
    return ""


def _unquoted(match):
    return re.sub(IN_QUOTES_SPLIT, " ", match.group(0)[1:-1])


def _close_stage(stages, words, plain, raw, op):
    words = re.sub(PREFIX, "", words).strip()
    if words:
        stages.append((words, re.sub(PREFIX, "", plain).strip(), raw.strip(), op))


def _stages(command):
    """The command's stages in order as (words, plain, raw, op): quoted text is Q in `words`, unquoted in `plain`, as
    typed in `raw`; `op` is the operator after the stage. Here-docs and comments are gone, so they never classify.

    A stage with no words goes with its operator, so PowerShell's `&` call operator is no operator. The last stage's
    `op` is `&` or empty.
    """
    text = re.sub(LINE_CONTINUATION, " ", re.sub(HERE_DOC, " Q ", command))
    stages = []
    words = plain = ""
    start = end = 0
    for token in re.finditer(TOKEN, text):
        gap = text[end:token.start()]
        words += gap
        plain += gap
        end = token.end()
        if token.lastgroup == "quoted":
            words += "Q"
            plain += _unquoted(token)
        elif token.lastgroup == "redirect":
            words += token.group(0)
        elif token.lastgroup == "op":
            _close_stage(stages, words, plain, text[start:token.start()], token.group(0))
            words = plain = ""
            start = end
    gap = text[end:]
    _close_stage(stages, words + gap, plain + gap, text[start:], "")
    if stages and stages[-1][3] != "&":
        stages[-1] = stages[-1][:3] + ("",)
    return stages


def _commits(words):
    found = re.match(GIT_COMMIT, words)
    return bool(found) and "--dry-run" not in found.group(1)


def _runs_check(stage, checks):
    """True when the stage runs a check: the regex matches a runner's plain text, or the stage's own command word."""
    bare = re.sub(DETACHED, "", re.sub(PREFIX, "", re.sub(CONTROL, "", re.sub(MEASURED, "", stage[0])))).strip()
    if not bare or re.search(NOT_A_RUN, bare):
        return False
    runner = re.match(RUNNER, bare)
    command_end = len(bare.split(None, 1)[0])
    for check in checks:
        found = re.search(check, stage[1] if runner else bare, flags=re.I)
        if found and (runner or found.start() < command_end):
            return True
    return False


def _exit_hidden(stages, index):
    """True when something after the stage can replace its exit status, as only `&&` passes a failure on, or when
    Measure-Command wraps it: that reports success whatever its block exits with."""
    return (any(stage[3] not in ("", "&&") for stage in stages[index:])
            or any(re.match(MEASURED, stage[0]) for stage in stages[:index + 1]))


def _backgrounded(stages, index):
    return bool(re.match(DETACHED, stages[index][0])) or any(stage[3] == "&" for stage in stages[index:])


def _conditional(stages, index):
    """True when the check may have been skipped. Stage 0 reads the last stage's `op`, which is never `||`."""
    return bool(re.match(CONTROL, stages[index][0])) or stages[index - 1][3] == "||"


def _exit_failed(output, is_error):
    return is_error or bool(re.match(EXIT_CODE, output))


def classify_check(command, output, is_error, checks):
    """pass, fail or unknown for a command that runs one of the `checks` regexes, else None.

    A command that also commits is no check: only a pass from an earlier call can vouch for that commit.
    """
    stages = _stages(command)
    if any(_commits(stage[0]) for stage in stages):
        return None
    return _read_stages(stages, output, is_error, checks)


def _read_result(command, output, is_error, checks):
    """As classify_check, except that a call that also commits reads as a check: failed, or unknown whatever else its
    output says, since no pass of that call counts."""
    stages = _stages(command)
    found = _read_stages(stages, output, is_error, checks)
    return "unknown" if found == "pass" and any(_commits(stage[0]) for stage in stages) else found


def _read_stages(stages, output, is_error, checks):
    """pass, fail, unknown or None for stages, a commit among them or not. With an exit status hidden a failure can sit
    anywhere in the output, and a summary speaks for the last check alone."""
    runs = [index for index, stage in enumerate(stages) if _runs_check(stage, checks)]
    if not runs:
        return None
    hidden = any(_exit_hidden(stages, index) for index in runs)
    tail = output[-OUTPUT_TAIL:]
    if _exit_failed(output, is_error) or re.search(FAIL_SUMMARY, output if hidden else tail):
        return "fail"
    if re.match(BACKGROUNDED, output) or any(_backgrounded(stages, i) or _conditional(stages, i) for i in runs):
        return "unknown"
    if any(_exit_hidden(stages, index) for index in runs[:-1]):
        return "unknown"
    if not re.match(PERSISTED, output) and re.search(OK_SUMMARY, tail):
        return "pass"
    return "unknown" if hidden else "pass"


def _command(use):
    command = use["input"].get("command")
    return command if isinstance(command, str) else ""


def _stream_pass(path, root, checks, found, cache):
    """The newest pass for `root` in one stream, no older than found.ts; "" when there is none. A check it cannot read
    on the way moves found.hidden."""
    for stamp, cwd, use, result in _tool_calls(path, found.ts):
        if result is None or use["name"] not in SHELL_TOOLS:
            continue
        command = _command(use)
        state = _read_result(command, result[1], result[0], checks)
        if state != "pass" and (state != "unknown" or stamp <= found.hidden):
            continue
        folders = _check_folders(command, cwd, checks)
        if folders and all(_repo_root(folder, cache) == root for folder in folders):
            if state == "pass":
                return stamp
            found.hidden = stamp
    return ""


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _written_before(path, ts):
    """True for a stream last written before `ts`, which cannot hold a later record; a missing one is still read, so
    it reports."""
    return bool(ts) and 0 < _mtime(path) < to_epoch(ts)


def _scan_streams(streams, root, checks, found):
    """Sets found.ts and found.hidden as checks turn up and returns the reason. Only streams touched after the best
    pass so far can beat it, so the calling stream goes first and the rest newest first."""
    if not streams:
        return "unreadable"
    reason = "ok"
    cache = {}
    others = sorted(((_mtime(path), path) for path in streams[1:]), reverse=True)
    for touched, path in [(None, streams[0])] + others:
        if found.ts and touched is not None and touched < to_epoch(found.ts):
            break
        try:
            found.ts = _stream_pass(path, root, checks, found, cache) or found.ts
        except OSError:
            reason = "unreadable"
    return reason


def _bounded(work, budget):
    """Runs `work` for at most `budget` seconds: a read can block for good, so it runs in a daemon thread. An exception
    of `work` raises here."""
    import threading
    failure = []

    def run():
        try:
            work()
        except Exception as exc:
            failure.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(budget)
    if failure:
        raise failure[0]


def last_pass(streams, root, checks, budget):
    """The latest passing check whose repo root is exactly `root`, as a Pass, within `budget` seconds: the best pass so
    far is returned on time. A bad `checks` regex raises here."""
    found = Pass("", "budget")

    def work():
        found.reason = _scan_streams(streams, norm(root), checks, found)

    _bounded(work, budget)
    return Pass(found.ts, found.reason, found.hidden)


def _stream_edits(path, turn_ts, calling):
    """(result time, file_path) of each successful edit-tool call of one stream since `turn_ts`."""
    for _, _, use, result in _tool_calls(path, turn_ts, calling):
        if result is None or result[0] or use["name"] not in EDIT_TOOLS:
            continue
        file_path = use["input"].get("file_path") or use["input"].get("notebook_path")
        if file_path and isinstance(file_path, str):
            yield result[2], file_path


def turn_changes(streams, turn_ts):
    """(result time, file_path) of each successful edit-tool call since `turn_ts`, sorted. The first stream is the
    calling one. An unreadable stream raises OSError: no changes must not stand in for no answer."""
    changes = []
    for path in streams:
        changes.extend(_stream_edits(path, turn_ts, path == streams[0]))
    return sorted(changes)


def turn_edits(streams, turn_ts, budget):
    """turn_changes within `budget` seconds, None when they run out, passing over a stream written before the turn."""
    found = []

    def work():
        changes = []
        for path in streams:
            if not _written_before(path, turn_ts):
                changes.extend(_stream_edits(path, turn_ts, path == streams[0]))
        found.append(sorted(changes))

    _bounded(work, budget)
    return found[0] if found else None


def _curl_ran(use, result):
    """True when the output is curl's own: its last curl stage leaves stdout alone and only pipes follow it."""
    if use["name"] not in SHELL_TOOLS or result is None or _exit_failed(result[1], result[0]):
        return False
    output = result[1].strip()
    if not output or re.fullmatch(NO_OUTPUT, output):
        return False
    stages = _stages(_command(use))
    runs = [index for index, stage in enumerate(stages) if re.match(CURL, stage[0])]
    if not runs or any(stage[3] not in ("", "|") for stage in stages[runs[-1]:]):
        return False
    words = stages[runs[-1]][0]
    if re.search(CURL_NO_BODY, words):
        return False
    return not re.search(CURL_TO_FILE, words) or bool(re.search(CURL_WRITE_OUT, words))


def _hands_on(streams, ts):
    """browser, curl or None."""
    for path in streams:
        if _written_before(path, ts):
            continue
        for _, _, use, result in _tool_calls(path, ts, path == streams[0]):
            if use["name"].startswith(BROWSER_TOOLS) and result is not None and not result[0]:
                return "browser"
            if _curl_ran(use, result):
                return "curl"
    return None


def evidence_after(streams, ts, budget):
    """browser or curl when a stream shows that hands-on check from `ts` on, else None; unknown when `budget` seconds
    run out first. An unreadable stream raises OSError, as in turn_changes."""
    found = ["unknown"]

    def work():
        found[0] = _hands_on(streams, ts)

    _bounded(work, budget)
    return found[0]


def to_epoch(ts):
    """Seconds since the epoch for a transcript timestamp, which is always UTC."""
    whole = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    fraction = ts[19:].rstrip("Z")
    return whole + float(fraction) if fraction else float(whole)


def _glob_regex(glob):
    tokens = {"**/": "(?:.*/)?", "*": "[^/]*"}
    return "".join(tokens.get(part, re.escape(part)) for part in re.split(r"(\*\*/|\*)", glob))


def scope_match(path, globs):
    """True when a repo-relative path matches a glob; a glob with no slash matches the basename."""
    path = path.replace("\\", "/")
    for glob in globs:
        subject = path if "/" in glob else path.rsplit("/", 1)[-1]
        if re.fullmatch(_glob_regex(glob), subject):
            return True
    return False


def row_key(*parts):
    """First 12 hex digits of a SHA-256 over the parts. Text from JSON can hold lone surrogates."""
    return hashlib.sha256("\0".join(parts).encode("utf-8", "surrogatepass")).hexdigest()[:12]
