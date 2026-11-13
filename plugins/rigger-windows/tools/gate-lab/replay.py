"""Gate lab replay: what the commit and Stop gates did, or would have done, read offline from transcripts.

Run it from the installed plugin. In `${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/installed_plugins.json`, under the key
that starts with `rigger-windows@`, take the first `installPath` that holds `tools/gate-lab/replay.py`. `--data` is the
plugin's data folder, `~/.claude/plugins/data/rigger-windows-<marketplace>`: only hooks get it as CLAUDE_PLUGIN_DATA.

  replay.py --data <dir> --gate <commit|stop> [--days 30] [--show <id>]
      The prototype rules over sessions touched in that window: fires per day, sample ids. An upper bound for commit.
  replay.py --data <dir> --gate commit --from-shadow <data>/commit-gate/rows [--show <id>]
  replay.py --data <dir> --gate stop --from-shadow <data>/turn-gate/rows [--show <id>]
      Row files joined to transcript calls. The only figures that judge the shipped rules: report.py reads them.
  replay.py --data <dir> --baseline --manifest <file>
      The prototype rules over a frozen session list, one main .jsonl per line, relative to the projects folder or
      absolute. Compared with the plan's funnel, so these rules stay as they were.

Exit 2 on 0 files read or a relied-on field seen 0 times. Prints hashes and counts; transcript text reaches stdout only
through --show, and the files written under --data hold none.
"""
import argparse
import collections
import datetime
import json
import os
import random
import re
import sys

# The installed plugin folder stays as shipped.
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "shell-guard")]
import report  # noqa: E402
import turn_evidence as te  # noqa: E402

ROW_CAP = 20000
SAMPLE = 40
SEED = 20261104
PER_SESSION = {"commit": 1, "stop": 2}
FIRE = {"commit": "stale", "stop": "flag"}
ROW_KEY = {"commit": "use12", "stop": "key12"}
RECORD_FIELDS = ("turnOrigin", "promptId", "toolUseResult")
RELIED = ("is_error",) + RECORD_FIELDS

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

HERE_DOC = re.compile(r"@'.*?'@|@\".*?\"@|<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$", re.S | re.M)
QUOTED = re.compile(r"'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\"")
COMMENT = re.compile(r"(?m)(^|\s)#.*$")
SPLIT = re.compile(r"&&|\|\||[;\n|&]")
PATH_ARG = r"(\"[^\"]+\"|'[^']+'|[^\s;&|]+)"
GIT_C = re.compile(r"\bgit\s+-C\s+" + PATH_ARG)
CD = re.compile(
    r"(?:^|[;&|\n]\s*)(?:cd|Set-Location|Push-Location|sl|pushd)\s+(?:-LiteralPath\s+|-Path\s+)?" + PATH_ARG
)
PREFIX = re.compile(r"^\s*(?:\(|\$\w+\s*=\s*|&\s*|env\s+|\w+=\S+\s+|time\s+|command\s+|exec\s+)+")
GIT_OP = re.compile(r"^git(?:\.exe)?(?:\s+-[cC]\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+(commit|push)\b(.*)$", re.S)
GH_MERGE = re.compile(r"^gh(?:\.exe)?\s+pr\s+merge\b")

TEST_WORDS = (
    r"phpunit|paratest|\bpest\b|vitest|\bjest\b|playwright\s+test|pytest|\bmocha\b|cypress\s+run"
    r"|node\s+--test|node\s+\S*--test|run_tests|\btest_\w+\.py|tests?[\\/]\S+\.py|\S+\.tests?\.(?:ps1|mjs|js|ts)"
    r"|tests?\.ps1|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|t|e2e|ci|gate|check)\b|(?:npm|pnpm)\s+run\s+\S*test"
    r"|composer\s+(?:run\s+|run-script\s+)?(?:test|check|ci|gate)\b|artisan\s+test|cargo\s+test|go\s+test"
    r"|dotnet\s+test|wp-env\s+run\s+\S*\s*\S*phpunit|safe-edit\.ps1\s+-Run"
)
LINT_WORDS = (
    r"phpstan|phpcs|\beslint\b|\btsc\b|biome\s+(?:check|ci|lint)|oxlint|\bruff\b|mypy|actionlint|shellcheck"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:lint|typecheck|type-check|build)\b|lessons_lint|drift.lint|sweep\.py"
    r"|astro\s+check|vite\s+build|next\s+build"
)
READ_ONLY = re.compile(
    r"^(?:rg|grep|sls|Select-String|Select-Object|cat|Get-Content|gc|type|ls|dir|Get-ChildItem|gci|git|gh|echo"
    r"|Write-Output|Write-Host|Test-Path|find|head|tail|wc|sed|awk|jq|Measure-Object|Where-Object|ForEach-Object"
    r"|Sort-Object|Format-\w+|Get-\w+)\b",
    re.I,
)
INTERPRETER = re.compile(r"^(?:Q|python\S*|py|node|pwsh|powershell|php\S*|sh|bash|npx|pnpm|npm)\b", re.I)
TEST_RE = re.compile(TEST_WORDS, re.I)
LINT_RE = re.compile(LINT_WORDS, re.I)
FAIL_RE = re.compile(
    r"FAILURES!|ERRORS!|^\s*# fail [1-9]|\b[1-9]\d* (?:tests? )?(?:failed|failing|failures?)\b"
    r"|Tests?:\s+.*\b[1-9]\d* failed|^FAILED\b|^FAIL\s|\bTraceback \(most recent call last\)",
    re.M,
)

CLAIM = re.compile(
    r"\b(?:done|completed?|finished|fixed|implemented|all (?:tests )?(?:pass(?:ed|ing)?|green)|tests? pass(?:ed|ing)?"
    r"|works now|ready (?:to|for)|merged|shipped)\b",
    re.I,
)
NEGATED = re.compile(r"\b(?:not|isn't|aren't|never|still not)\s+(?:\w+\s+)?$", re.I)
NON_CODE = re.compile(r"\.(?:md|txt|rst|po|pot|png|jpe?g|svg|gif|webp|csv|log)$", re.I)
OUTSIDE = re.compile(r"(?:/\.claude/|/appdata/|/temp/|/tmp/|scratchpad|/side-session-notes/)", re.I)


def projects_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def session_id(main):
    return os.path.basename(main)[: -len(".jsonl")]


def session_mains(projects):
    """Every session's main file. Subagent streams hang off it through te.stream_paths."""
    if not os.path.isdir(projects):
        return []
    found = []
    for folder in sorted(entry.path for entry in os.scandir(projects) if entry.is_dir()):
        found.extend(
            entry.path for entry in os.scandir(folder)
            if entry.is_file() and entry.name.endswith(".jsonl") and not entry.name.startswith("agent-")
        )
    return found


def strip_text(command):
    """Quoted strings, here-docs and comments become Q so their words never classify."""
    return COMMENT.sub("", QUOTED.sub("Q", HERE_DOC.sub(" Q ", command)))


def segments(command):
    return [PREFIX.sub("", part).strip() for part in SPLIT.split(strip_text(command))]


def resolve(path, base):
    path = te.norm(path)
    if not path:
        return base
    if re.match(r"^[a-zA-Z]:/", path) or path.startswith("/"):
        return path
    return te.norm(base + "/" + path) if base else path


def shell_root(command, cwd):
    """Folder the command acts in: last `git -C`, else first `cd`, else the record's cwd."""
    base = te.norm(cwd)
    found = GIT_C.findall(command)
    if found:
        return resolve(found[-1], base)
    found = CD.findall(command)
    return resolve(found[0], base) if found else base


def git_ops(command):
    """commit, push and merge in the command. Only commit is gated, but any of them keeps it from being a check."""
    ops = []
    for part in segments(command):
        match = GIT_OP.match(part)
        if match:
            short_dry = re.search(r"(^|\s)-n(\s|$)", match.group(2)) and match.group(1) == "push"
            if "--dry-run" not in match.group(2) and not short_dry:
                ops.append(match.group(1))
        elif GH_MERGE.match(part):
            ops.append("merge")
    return ops


def check_kind(command):
    """test, lint or None, read from the command's unquoted words plus script paths."""
    active = [part for part in segments(command) if part and not READ_ONLY.match(part)]
    if not active:
        return None
    probe = " ".join(active)
    # Quoted script paths still name a test file.
    if any(INTERPRETER.match(part) for part in active):
        probe += " " + " ".join(re.findall(r"[\"']([^\"'\n]*(?:test|run_tests)[^\"'\n]*)[\"']", command))
    if TEST_RE.search(probe):
        return "test"
    return "lint" if LINT_RE.search(probe) else None


def repo_root(path, cache):
    """Nearest ancestor holding .git; "" when none exists on disk any more."""
    parts = path.split("/")
    for end in range(len(parts), 1, -1):
        folder = "/".join(parts[:end])
        if folder not in cache:
            cache[folder] = os.path.exists(folder + "/.git")
        if cache[folder]:
            return folder
    return ""


def result_text(block):
    body = block.get("content")
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return "\n".join(item.get("text", "") for item in body if isinstance(item, dict))
    return ""


def first_text(content):
    texts = (block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return next(texts, "")


def read_records(path, stats):
    with open(path, "rb") as handle:
        for line in handle:
            if b'"type":"user"' not in line and b'"type":"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("type") in ("user", "assistant"):
                stats.update(name for name in RECORD_FIELDS if name in record)
                yield record


def human_line(record, text, stamp, stream):
    """The prompt or resume event of a user line that is no tool result, if it is one."""
    if te.turn_start(record, text):
        return [{"k": "prompt", "ts": stamp, "s": stream}]
    if record.get("turnOrigin") == "task_notification" or text.lstrip().startswith("<task-notification"):
        return [{"k": "resume", "ts": stamp, "s": stream}]
    return []


def events_from(path, stream, stats):
    """Yield dicts: edit, shell, text (assistant), prompt (a turn opens), resume (a task notification woke the
    session: a Stop fired, the turn goes on), pending (a shell call with no result)."""
    uses = {}
    prompt_id = ""
    for record in read_records(path, stats):
        stamp = record.get("timestamp") or ""
        content = (record.get("message") or {}).get("content")
        prompt_id = record.get("promptId") or prompt_id
        if record["type"] == "user":
            if isinstance(content, str):
                yield from human_line(record, content, stamp, stream)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    stats["is_error"] += "is_error" in block
                    use = uses.pop(block.get("tool_use_id"), None)
                    if use is None:
                        continue
                    name, data, cwd = use
                    failed = bool(block.get("is_error"))
                    if name in EDIT_TOOLS and data.get("file_path") and not failed:
                        yield {"k": "edit", "ts": stamp, "s": stream, "file": te.norm(data["file_path"])}
                    elif name in SHELL_TOOLS:
                        text = result_text(block)
                        yield {
                            "k": "shell", "ts": stamp, "s": stream, "tool": name, "use": block.get("tool_use_id"),
                            "cmd": (data.get("command") or "")[:4000], "cwd": cwd, "err": failed,
                            "bg": bool(data.get("run_in_background")), "tail": text[-3000:],
                            "denied": "hook error" in text[:200] or "denied" in text[:200].lower(),
                        }
                if any(isinstance(block, dict) and block.get("type") == "text" for block in content):
                    yield from human_line(record, first_text(content), stamp, stream)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    uses[block.get("id")] = (block.get("name"), block.get("input") or {}, record.get("cwd") or "")
                elif block.get("type") == "text" and block.get("text", "").strip():
                    yield {"k": "text", "ts": stamp, "s": stream, "text": block["text"], "prompt_id": prompt_id}
    for use_id, use in uses.items():
        if use[0] in SHELL_TOOLS:
            yield {"k": "pending", "ts": "", "s": stream, "use": use_id}


def session_events(main, stats):
    """A session's events in time order, its subagents' streams included."""
    found = []
    for index, path in enumerate(te.stream_paths(main, None)):
        found.extend(events_from(path, "sub%d" % index if index else "main", stats))
        stats["files"] += 1
    stats["sessions"] += 1
    stats["events"] += len(found)
    return sorted(found, key=lambda item: item["ts"])


def one_line(text):
    return text.replace("\r", "").replace("\n", " / ")


def evidence(events, end):
    """What a labeller needs around a fire."""
    shells = [item for item in events if item["k"] == "shell" and item["ts"] <= end]
    lines = ["shell calls (%d, last 7):" % len(shells)]
    lines.extend(
        "  [%s %s err=%s] %s => %s" % (item["s"], item["tool"][:2], item["err"], one_line(item["cmd"][:110]),
                                       one_line(item["tail"][-70:]))
        for item in shells[-7:]
    )
    edits = [item for item in events if item["k"] == "edit" and item["ts"] <= end][-3:]
    last = ("%s %s %s" % (item["ts"][11:19], item["s"], item["file"][-50:]) for item in edits)
    lines.append("last edits: %s" % "; ".join(last))
    return lines


def fire(gate, session, stamp, repo, lines, events):
    """A fire; its id hashes the projected text, so a label never has to store it."""
    head = "%s %s %s %s" % (gate, session[:8], stamp, repo)
    text = "\n".join([head] + lines + evidence(events, stamp))
    return {"id": te.row_key(gate, text), "ts": stamp, "repo8": repo[:8], "session": session, "text": text}


def closings(events):
    """Each turn end as (text event, turn start). A notification ends one too: the Stop fired before it woke the
    session."""
    found, last, opened = [], None, ""
    for item in events:
        if item["s"] != "main":
            continue
        if item["k"] == "text":
            last = (item, opened)
        elif item["k"] in ("prompt", "resume"):
            if last:
                found.append(last)
                last = None
            if item["k"] == "prompt":
                opened = item["ts"]
    if last:
        found.append(last)
    return found


def passed(shell):
    return not (shell["err"] or shell["bg"] or shell["denied"] or FAIL_RE.search(shell["tail"]))


def claims_done(text):
    for match in CLAIM.finditer(text):
        if not NEGATED.search(text[max(0, match.start() - 24) : match.start()]):
            return match.group(0)
    return None


def non_code(edit):
    return bool(NON_CODE.search(edit["file"]))


def related(root, other):
    return other == root or other.startswith(root + "/") or root.startswith(other + "/")


def commit_row(session, project, item, root, inline, edits, tests, commit_ts, events):
    """One gated commit: the code edits since the later of the last test pass and the last commit."""
    passes = [entry for entry in tests if related(root, entry["root"])]
    last = passes[-1]["ts"] if passes else ""
    cut = max(last, commit_ts.get(root, ""))
    fresh = [
        edit for edit in edits
        if bool(root) and (edit["file"] == root or edit["file"].startswith(root + "/"))
        and edit["ts"] > cut and not non_code(edit)
    ]
    row = {
        "session": session, "project": project, "ts": item["ts"], "root": root, "ok": not item["err"],
        "inline": inline, "ever": bool(passes), "fresh_code": len(fresh),
    }
    if row["ok"] and fresh and not inline:
        files = sorted({edit["file"][len(root) + 1 :] for edit in fresh})[:6]
        lines = ["cmd: %s" % one_line(item["cmd"][:150]),
                 "last pass %s, code edited since: %s" % (last, ", ".join(files))]
        row["fire"] = fire("commit", session, item["ts"], root, lines, events)
    return row


def closing_row(session, project, text_event, opened, edits, checks, events):
    """One turn end with edits no check has followed, or None."""
    full = text_event["text"]
    stamp = text_event["ts"]
    recent = [edit for edit in edits if edit["ts"] <= stamp and not OUTSIDE.search(edit["file"])]
    pending = []
    for edit in recent:
        root = edit.get("root") or ""
        later = (entry for entry in checks if edit["ts"] < entry["ts"] <= stamp)
        if not any(not root or related(root, entry["root"]) for entry in later):
            pending.append(edit)
    if not pending:
        return None
    code = [edit for edit in pending if not non_code(edit)]
    turn_code = [edit for edit in code if edit["ts"] > opened]
    turn_repo = [edit for edit in turn_code if edit.get("root") or "/worktrees/" in edit["file"]]
    last_paragraph = re.split(r"\n\s*\n", full.strip())[-1][:400]
    strong = claims_done(full.strip()[:300]) or claims_done(last_paragraph)
    row = {
        "session": session, "project": project, "ts": stamp, "pending": len(pending), "code": len(code),
        "claim": claims_done(full[-1500:]) or claims_done(full[:400]), "strong": strong, "turn_code": len(turn_code),
        "turn_repo": len(turn_repo), "roots": sorted({edit.get("root") or "" for edit in turn_repo})[:3],
    }
    if strong and turn_repo:
        files = sorted({edit["file"] for edit in turn_repo})[-5:]
        lines = ["claim %s" % strong, "files: %s" % ", ".join(item[-60:] for item in files),
                 "closing tail: %s" % one_line(full[-320:])]
        row["fire"] = fire("stop", session, stamp, row["roots"][0], lines, events)
    return row


def analyse(main, stats):
    """The prototype rules over one session: (commit rows, closing rows)."""
    session = session_id(main)
    project = os.path.basename(os.path.dirname(main))
    events = session_events(main, stats)
    cache, edits, tests, checks, commit_ts, commits = {}, [], [], [], {}, []
    for item in events:
        if item["k"] == "edit":
            item["root"] = repo_root(item["file"], cache)
            edits.append(item)
        elif item["k"] == "shell":
            raw = shell_root(item["cmd"], item["cwd"])
            root = repo_root(raw, cache) or raw
            ops = git_ops(item["cmd"])
            kind = check_kind(item["cmd"])
            if kind and not ops and passed(item):
                checks.append({"ts": item["ts"], "root": root})
                if kind == "test":
                    tests.append({"ts": item["ts"], "root": root})
            if "commit" in ops:
                commits.append(commit_row(session, project, item, root, bool(kind), edits, tests, commit_ts, events))
                if not item["err"]:
                    commit_ts[root] = item["ts"]
    rows = (closing_row(session, project, text, opened, edits, checks, events) for text, opened in closings(events))
    return commits, [row for row in rows if row]


def sweep(mains, stats):
    commits, closes = [], []
    for main in mains:
        one, other = analyse(main, stats)
        commits.extend(one)
        closes.extend(other)
    return commits, closes


def gated_roots(commits):
    """Roots with a test habit: at least 3 successful commits there had a passing test in the session."""
    tested = collections.Counter(row["root"] for row in commits if row["ok"] and row["ever"])
    return {root for root, count in tested.items() if count >= 3}


def unusable(stats):
    """2 and the reason when the read cannot be trusted: nothing read, or a relied-on field that never occurs and so
    would read as false everywhere."""
    if not stats["files"]:
        print("0 files read", file=sys.stderr)
        return 2
    missing = [name for name in RELIED if not stats[name]]
    if missing:
        print("relied-on field seen 0 times: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    return 0


def sample_ids(fires, gate):
    """A seeded sample, at most PER_SESSION per session, so one long session cannot fill it."""
    pool = sorted(fires, key=lambda item: item["id"])
    random.Random(SEED).shuffle(pool)
    picked, seen = [], collections.Counter()
    for item in pool:
        if seen[item["session"]] < PER_SESSION[gate] and len(picked) < SAMPLE:
            seen[item["session"]] += 1
            picked.append(item["id"])
    return picked


def print_fires(fires, gate, show):
    for day, count in sorted(collections.Counter(item["ts"][:10] for item in fires).items()):
        print("day %s %d" % (day, count))
    print("fires %d" % len(fires))
    for picked in sample_ids(fires, gate):
        print("sample %s" % picked)
    if show is None:
        return 0
    match = [item["text"] for item in fires if item["id"] == show]
    if not match:
        print("no fire with id %s" % show, file=sys.stderr)
        return 1
    print(match[0])
    return 0


def print_read(stats):
    print("# files read %d, sessions %d, events %d" % (stats["files"], stats["sessions"], stats["events"]))


def plain(args):
    cutoff = report.today() - datetime.timedelta(days=args.days)
    since = cutoff.isoformat()
    edge = report.epoch(since)
    stats = collections.Counter()
    mains = [main for main in session_mains(projects_dir()) if os.path.getmtime(main) >= edge]
    commits, closes = sweep(mains, stats)
    failed = unusable(stats)
    if failed:
        return failed
    print_read(stats)
    gated = gated_roots(commits)
    if args.gate == "commit":
        print("upper bound: this works from edit events, since transcripts hold no index or mtimes")
        rows = [row for row in commits if row["root"] in gated]
    else:
        rows = [row for row in closes if set(row["roots"]) & gated]
    fires = [row["fire"] for row in rows if "fire" in row and row["ts"] >= since]
    return print_fires(fires, args.gate, args.show)


def manifest_mains(path):
    with open(path, encoding="utf-8") as handle:
        names = [line.strip() for line in handle if line.strip()]
    return [os.path.join(projects_dir(), name) for name in names]


def commit_verdict(row):
    if not row["fresh_code"]:
        return "allow"
    return "allow-inline" if row["inline"] else "block"


def baseline(args):
    mains = manifest_mains(args.manifest)
    missing = [main for main in mains if not os.path.isfile(main)]
    if missing:
        print("%d of %d listed sessions missing, first: %s" % (len(missing), len(mains), missing[0]), file=sys.stderr)
        return 2
    stats = collections.Counter()
    commits, closes = sweep(mains, stats)
    failed = unusable(stats)
    if failed:
        return failed
    print_read(stats)
    gated = gated_roots(commits)
    print("roots %d, gated %d" % (len({row["root"] for row in commits}), len(gated)))
    for label, rows in (("all roots", commits), ("gated roots", [row for row in commits if row["root"] in gated])):
        count = collections.Counter(commit_verdict(row) for row in rows if row["ok"])
        print("commits, %s: %d, allow %d, allow-inline %d, block %d" % (
            label, sum(count.values()), count["allow"], count["allow-inline"], count["block"]))
    steps = (
        ("pending edit at turn end", lambda row: True),
        ("claim word", lambda row: row["claim"]),
        ("code file", lambda row: row["code"]),
        ("edited this turn", lambda row: row["turn_code"]),
        ("in a git repo", lambda row: row["turn_repo"]),
        ("claim in lead or last paragraph", lambda row: row["strong"]),
        ("gated root", lambda row: set(row["roots"]) & gated),
    )
    rows = closes
    for name, keep in steps:
        rows = [row for row in rows if keep(row)]
        print("funnel %s: %d rows, %d sessions" % (name, len(rows), len({row["session"] for row in rows})))
    return 0


def read_rows(folder):
    """(row lines, unfinished, malformed, files). A row file holds one line per root; an empty one is a worker that
    never finished."""
    rows, unfinished, malformed, files = [], 0, 0, 0
    if not os.path.isdir(folder):
        return rows, unfinished, malformed, files
    for entry in os.scandir(folder):
        if not entry.name.endswith(".json"):
            continue
        files += 1
        with open(entry.path, "rb") as handle:
            lines = handle.read().splitlines()
        unfinished += 0 if any(line.strip() for line in lines) else 1
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if isinstance(row, dict):
                rows.append(row)
            elif line.strip():
                malformed += 1
    return rows, unfinished, malformed, files


def commit_targets(session, events):
    """{(sid8, use12): the call}. A call that was denied or never returned has an error or no result."""
    return {(session[:8], te.row_key(item["use"])): item for item in events if item["k"] in ("shell", "pending")}


def stop_targets(session, events):
    found = {}
    for item, _ in closings(events):
        found[(session[:8], te.row_key(session, item["prompt_id"], item["text"]))] = item
    return found


def commit_fire(row, target, session, events):
    lines = ["cmd: %s" % one_line(target["cmd"][:150]),
             "staged %s, deleted %s, newest_age_s %s, check_age_s %s, git_op %s" % tuple(
                 row.get(name) for name in ("staged", "deleted", "newest_age_s", "check_age_s", "git_op"))]
    return fire("commit", session, target["ts"], str(row.get("repo8")), lines, events)


def stop_fire(row, target, session, events):
    lines = ["claim %s, code_changes %s, evidence %s" % tuple(
                 row.get(name) for name in ("claim", "code_changes", "evidence")),
             "closing tail: %s" % one_line(target["text"][-320:])]
    return fire("stop", session, target["ts"], str(row.get("repo8")), lines, events)


def after_git_op(rows):
    """Stale rows a rebase or checkout may explain: a git operation in progress, or HEAD moved after the newest
    change, since both reset mtimes."""
    in_progress = moved = 0
    for row in rows:
        in_progress += 1 if row.get("git_op") else 0
        reflog, newest = row.get("reflog_age_s"), row.get("newest_age_s")
        moved += 1 if isinstance(reflog, (int, float)) and isinstance(newest, (int, float)) and reflog <= newest else 0
    return in_progress, moved


def join_session(gate, rows, mains, stats, tally):
    """Joins one sid8's rows to its transcripts. Sessions go one at a time, so their events are not all held at once."""
    build = commit_targets if gate == "commit" else stop_targets
    targets = {}
    for main in mains:
        session = session_id(main)
        events = session_events(main, stats)
        for key, target in build(session, events).items():
            targets[key] = (target, session, events)
    for row in rows:
        key = (row.get("sid8"), row.get(ROW_KEY[gate]))
        if key not in targets:
            tally["unmatched"].append("%s %s" % key)
            continue
        target, session, events = targets[key]
        if target["k"] == "pending" or target.get("err"):
            tally["dropped"] += 1
            continue
        tally["verdicts"][report.verdict_column(row)] += 1
        if row.get("verdict") != FIRE[gate]:
            continue
        tally["stale"].append(row)
        once = (row.get("sid8"), row.get("repo8"), row.get("fingerprint") or row.get(ROW_KEY[gate]))
        if once not in tally["seen"]:
            tally["seen"].add(once)
            tally["fires"].append((commit_fire if gate == "commit" else stop_fire)(row, target, session, events))


def shadow(args):
    gate = args.gate
    rows, unfinished, malformed, files = read_rows(args.from_shadow)
    print("# row files %d, rows %d, unfinished %d, malformed %d" % (files, len(rows), unfinished, malformed))
    if files >= ROW_CAP:
        print("warning: %d row files, at the %d cap: the gate has gone silent" % (files, ROW_CAP))
    if not files:
        print("0 row files read in %s" % args.from_shadow, file=sys.stderr)
        return 2
    by_session = collections.defaultdict(list)
    for row in rows:
        by_session[row.get("sid8")].append(row)
    mains = collections.defaultdict(list)
    for main in session_mains(projects_dir()):
        mains[session_id(main)[:8]].append(main)
    stats = collections.Counter()
    tally = {"verdicts": collections.Counter(), "unmatched": [], "dropped": 0, "stale": [], "seen": set(), "fires": []}
    for sid8, group in by_session.items():
        join_session(gate, group, mains.get(sid8, []), stats, tally)
    failed = unusable(stats)
    if failed:
        return failed
    print_read(stats)
    for name, count in sorted(tally["verdicts"].items(), key=str):
        print("verdict %s %d" % (name, count))
    print("unfinished %d" % unfinished)
    print("unmatched %d" % len(tally["unmatched"]))
    for key in sorted(tally["unmatched"])[:5]:
        print("  %s" % key)
    print("dropped %d" % tally["dropped"])
    if gate == "commit":
        print("stale with a git operation in progress %d, with HEAD moved after the newest change %d"
              % after_git_op(tally["stale"]))
    total = sum(count for name, count in tally["verdicts"].items() if name not in ("dry-run", report.HIDDEN))
    report.write_shadow(args.data, gate, {
        "gate": gate, "written": report.today().isoformat(), "rows": len(rows), "unfinished": unfinished,
        "unmatched": len(tally["unmatched"]), "dropped": tally["dropped"], "total": total,
        "verdicts": {str(name): count for name, count in tally["verdicts"].items()},
        "fires": [{"id": item["id"], "ts": item["ts"], "repo8": item["repo8"]} for item in tally["fires"]],
    })
    return print_fires(tally["fires"], gate, args.show)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay the gates offline from transcripts.")
    parser.add_argument("--data", required=True, help="the plugin's data folder")
    parser.add_argument("--gate", choices=sorted(FIRE))
    parser.add_argument("--days", type=int, default=30, help="window of a plain replay")
    parser.add_argument("--from-shadow", dest="from_shadow", help="the gate's rows folder")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--manifest", help="frozen session list for --baseline")
    parser.add_argument("--show", help="print the projected text of one fire")
    args = parser.parse_args(argv)
    if args.baseline and not args.manifest:
        parser.error("--baseline needs --manifest")
    if not args.baseline and not args.gate:
        parser.error("--gate or --baseline is required")
    report.ensure_trials(args.data)
    if args.baseline:
        return baseline(args)
    return shadow(args) if args.from_shadow else plain(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
