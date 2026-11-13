"""Where Claude Code tokens go, read from local transcripts (~/.claude/projects).

python -I token_audit.py report [--days 30] [--out FILE]
    Spend split (main vs subagents, by agent type), cache rebuilds, request prefix sizes,
    tool usage, recurring errors and repeated failing shell commands.
python -I token_audit.py runs --match TEXT [--days 30]
    One row per subagent whose description contains TEXT: an A/B run's cost.
python -I token_audit.py job --job ID [--session UUID] [--days 30]
    One row per subagent whose first user message starts with the line `Job: ID` (cost in tokens and
    USD from prices.json, duration), the totals and the wall time. --session also counts that
    session's subagents with no `Job:` line. Exit 1 when the result is INCOMPLETE or matches nothing.
    --days leaves out transcripts last changed before the window, agents of this job included: the
    report's `window: last N days` line says how far it looked.
python -I token_audit.py prefix TRANSCRIPT
    What the first request of one transcript carried: system, tool schemas, attachments.
python -I token_audit.py archive --lessons DIR [--lint FILE] [--days 30]
                                 [--expect-case FILE [--control-root DIR]] [--expect-hook TEXT ...]
                                 [--write-history --history-dir DIR]
    The lessons archive against the transcripts: cases per month by kind, open guards, repeat_of
    chains, error lines recurring in >=2 sessions with or without a case (matched by error_text),
    skill use, hook non-blocking errors, shell-guard escapes. Cases are read with the rigger-lessons
    lint (--lint, default: the installed plugin's, by its installPath in
    <config dir>/plugins/installed_plugins.json; exit 2 when neither is found). --write-history writes
    <history-dir>/<YYYY-MM>.json (secrets masked), only when every control passed.
    --expect-* are positive controls: exit 1 when the named case or hook error is not found;
    --control-root runs the case control on a fixture transcript folder instead of the live ones.

Cost is "effective tokens" in fresh-input equivalents, priced as billed:
input 1, cache read 0.1, cache write 1.25 (5 min) or 2.0 (1 h), output 5.
"""
import argparse
import calendar
import importlib.util
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool

WEIGHTS = {"input": 1.0, "cache_read": 0.10, "cache_write_5m": 1.25, "cache_write_1h": 2.0,
           "output": 5.0}
TOKEN_CLASSES = ("input", "cache_read", "cache_write_5m", "cache_write_1h", "output")
PROMPT_CLASSES = ("input", "cache_read", "cache_write_5m", "cache_write_1h")
PRICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")
JOB_LINE = re.compile(r"Job: (\S+)")
SHELL_TOOLS = ("Bash", "PowerShell")
REBUILD_MIN_TOKENS = 30000
# Secrets go before anything reaches the report or history: MySQL accounts, URL credentials, e-mail
# addresses and query strings by their context; long tokens after the uuid/hex masks have taken theirs.
SECRETS = [(re.compile(r"'[^'\s]*'@'[^'\s]*'"), "'<user>'@'<host>'"),
           (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@'\"]+@"), r"\1<cred>@"),
           # the last label is letters, so a package version (`eslint@9.1.0`) is no address
           (re.compile(r"[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])"), "<email>"),
           (re.compile(r"\?[^\s'\"<>]*=[^\s'\"<>]*"), "?<query>")]
TOKEN_RUN = re.compile(r"(?<![A-Za-z0-9_+-])[A-Za-z0-9_+-]{24,}={0,2}(?![A-Za-z0-9_+=-])")
SLUG_WORDS = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")


def _long_token(match):
    """A run of 24+ token characters with letters and digits; an `--option` or a lowercase
    hyphenated name (`example-a11y-platform-23c3`) is not one, nor is an identifier without digits
    (`ERR_PNPM_OUTDATED_LOCKFILE`)."""
    run = match.group(0)
    if run.startswith("-") or SLUG_WORDS.fullmatch(run) or not re.search(r"[A-Za-z]", run) \
            or not re.search(r"\d", run):
        return run
    return "<token>"


LONG_TOKEN = (TOKEN_RUN, _long_token)
MASKS = SECRETS + [
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])[^\s'\";&|]*"), "<path>"),
    (re.compile(r"(?<![\w>:/])/[\w.-]+(?:/[\w.-]+)+"), "<path>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b"), "<hex>"),
    LONG_TOKEN,
    (re.compile(r"\d+"), "<n>")]
# Refusals by the user or a permission rule, not failures of the command.
REJECTED_PREFIXES = ("The user doesn't want to", "[Request interrupted", "Permission to use",
                     "Permission for this action")


def mask(text, limit=None):
    """Whitespace folded, secrets, paths, ids and digits masked; cut to `limit` only for display."""
    text = " ".join(str(text).split())
    for rx, rep in MASKS:
        text = rx.sub(rep, text)
    return text if limit is None else text[:limit]


def scrub(text):
    """Secrets masked, everything else kept: for text whose paths tell things apart (hook commands)."""
    text = " ".join(str(text).split())
    for rx, rep in SECRETS + [LONG_TOKEN]:
        text = rx.sub(rep, text)
    return text


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def token_classes(usage):
    """Tokens of one request by billing class (TOKEN_CLASSES)."""
    created = _num(usage.get("cache_creation_input_tokens"))
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write_1h = _num(split.get("ephemeral_1h_input_tokens"))
    write_5m = split.get("ephemeral_5m_input_tokens")
    write_5m = _num(write_5m) if write_5m is not None else created - write_1h
    return {"input": _num(usage.get("input_tokens")),
            "cache_read": _num(usage.get("cache_read_input_tokens")),
            "cache_write_5m": write_5m, "cache_write_1h": write_1h,
            "output": _num(usage.get("output_tokens"))}


def cost(usage):
    """Effective tokens of one request, split by billing class."""
    tokens = token_classes(usage)
    return {"input": tokens["input"] * WEIGHTS["input"],
            "cache_read": tokens["cache_read"] * WEIGHTS["cache_read"],
            "cache_write": tokens["cache_write_5m"] * WEIGHTS["cache_write_5m"]
            + tokens["cache_write_1h"] * WEIGHTS["cache_write_1h"],
            "output": tokens["output"] * WEIGHTS["output"]}


def prompt_tokens(usage):
    return (_num(usage.get("input_tokens")) + _num(usage.get("cache_read_input_tokens"))
            + _num(usage.get("cache_creation_input_tokens")))


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return json.dumps(content, ensure_ascii=False)


# Lines after a shell failure that name no failure although they have two words: stack frames, a
# checks summary and the properties of a printed error object (`code: 'ENOENT',`) other than its
# message. Exit lines, `Node.js v22`, `# fail 1`, a bare number or `}` drop out by the word count.
TRAILER = re.compile(r"(?i)^(at |\d+ checks?, \d+ failed\b|"
                     r"(?!error:|message:)\w+: ('[^']*'|-?\d+|[\[{]|true|false|null|undefined),?$)")
WORD = re.compile(r"[^\W\d_]{3,}")
PLACEHOLDER = re.compile(r"<[a-z]+>")
# `name: message`, the way PowerShell (and many CLIs) start an error line.
RECORD = re.compile(r"^[\w.-]+: \S")


def _words(line):
    """Letter-words of a line, not counting the placeholders the mask put in."""
    return len(WORD.findall(PLACEHOLDER.sub(" ", mask(line))))


def _continues(prev, line):
    """`line` is the tail of `prev`: a console wrap (lower-case start after a line cut mid-sentence,
    "operable program or batch file.") or the generic second sentence PowerShell prints under a
    `name: message.` error ("Check the spelling of the name...")."""
    if RECORD.match(line):
        return False
    wrapped = line[:1].islower() and (prev[-1:].isalpha() or prev.endswith(","))
    second = line[:1].isupper() and prev.endswith(".") and RECORD.match(prev) is not None
    return wrapped or second


def error_line(tool, output):
    """The whole masked line that names a failure (truncate only for display and grouping): for a shell
    tool the last output line that is not an exit code or a trailer and has two words, joined with the
    line it continues; for any other tool the first line."""
    lines = [ln.strip() for ln in str(output).splitlines() if ln.strip()]
    if tool not in SHELL_TOOLS:
        return mask(lines[0] if lines else "")
    lines = [ln for ln in lines if not ln.startswith("Exit code")] or lines
    kept = []  # the last two meaningful lines, newest first; read from the end, the output can be long
    for ln in reversed(lines):
        if not TRAILER.match(ln) and _words(ln) >= 2:
            kept.append(ln)
            if len(kept) == 2:
                break
    kept = kept or lines[::-1][:2]
    if not kept:
        return ""
    last = kept[0]
    if len(kept) == 2 and _continues(kept[1], last):
        last = kept[1] + " " + last
    return mask(last)


def group_key(tool, line):
    """Error lines group and show by their head; matching against error_text reads the whole line."""
    return line[:100 if tool in SHELL_TOOLS else 140]


def error_signature(tool, command, output):
    """Group failures that are the same failure: a shell failure by its command and error line."""
    if tool in SHELL_TOOLS:
        return f"{tool} | {mask(command, 80)} => {group_key(tool, error_line(tool, output))}"
    return f"{tool} | {group_key(tool, error_line(tool, output))}"


def is_subagent(path):
    parts = os.path.normpath(path).split(os.sep)
    return "subagents" in parts[:-1]


def read_meta(transcript):
    meta = transcript[:-len(".jsonl")] + ".meta.json"
    try:
        with open(meta, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def new_stats(path):
    sub = is_subagent(path)
    return {"path": path, "sub": sub, "meta": read_meta(path) if sub else {}, "requests": 0,
            "cost": Counter(), "first_prefix": 0, "rebuilds": [], "tools": Counter(),
            "result_chars": Counter(), "errors": Counter(), "reads": Counter(), "skills": Counter(),
            "bad_lines": 0, "failed": None, "by_model": {}, "largest_prompt": {}, "first_ts": None, "last_ts": None}


def parse_transcript(path):
    """Streamed assistant lines repeat one message id: the first line carries a placeholder
    output_tokens and the last the final count, and tool_use blocks repeat. Each id is billed
    once with its last usage, timed by its first line; each tool_use id counts once."""
    stats = new_stats(path)
    try:
        _parse_into(stats, path)
    except Exception as exc:  # one unreadable file must not abort the whole report
        stats = new_stats(path)
        stats["failed"] = f"{type(exc).__name__}: {exc}"
    return stats


def _parse_into(stats, path):
    usage_by_id, model_by_id, first_time, order = {}, {}, {}, []
    seen_tools, pending = set(), {}
    first_ts = last_ts = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"assistant"' not in line and '"user"' not in line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                stats["bad_lines"] += 1
                continue
            if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user"):
                continue
            when = _timestamp(entry.get("timestamp"))
            if when is not None:
                first_ts = when if first_ts is None else min(first_ts, when)
                last_ts = when if last_ts is None else max(last_ts, when)
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if entry.get("type") == "assistant":
                usage, mid = message.get("usage"), message.get("id")
                if isinstance(usage, dict) and isinstance(mid, str):
                    if mid not in usage_by_id:
                        order.append(mid)
                        first_time[mid] = when
                    usage_by_id[mid] = usage
                    model_by_id[mid] = message.get("model")
                for block in content if isinstance(content, list) else ():
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tid = block.get("id")
                    if not isinstance(tid, str) or tid in seen_tools:
                        continue
                    seen_tools.add(tid)
                    name = str(block.get("name", "?"))
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    stats["tools"][name] += 1
                    pending[tid] = (name, str(args.get("command", "")))
                    if name == "Read":
                        stats["reads"][str(args.get("file_path", ""))] += 1
                    elif name == "Skill":
                        stats["skills"][str(args.get("skill", "?"))] += 1
            else:
                for block in content if isinstance(content, list) else ():
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    tid = block.get("tool_use_id")
                    name, command = pending.get(tid, ("?", "")) if isinstance(tid, str) else ("?", "")
                    text = result_text(block.get("content"))
                    stats["result_chars"][name] += len(text)
                    if block.get("is_error") and not text.lstrip().startswith(REJECTED_PREFIXES):
                        stats["errors"][error_signature(name, command, text)] += 1
    prev_time = None
    for i, mid in enumerate(order):
        usage, now = usage_by_id[mid], first_time[mid]
        stats["cost"].update(cost(usage))
        tokens = token_classes(usage)
        if any(tokens.values()):  # a zero-token row (the harness's `<synthetic>` replies) names no model
            model = model_by_id[mid] if isinstance(model_by_id[mid], str) and model_by_id[mid] else "?"
            stats["by_model"].setdefault(model, Counter()).update(tokens)
            prompt = sum(tokens[c] for c in PROMPT_CLASSES)
            stats["largest_prompt"][model] = max(stats["largest_prompt"].get(model, 0), prompt)
        created = _num(usage.get("cache_creation_input_tokens"))
        if i == 0:
            stats["first_prefix"] = prompt_tokens(usage)
        elif created > REBUILD_MIN_TOKENS:
            gap = now - prev_time if now is not None and prev_time is not None else None
            stats["rebuilds"].append((created, gap))
        prev_time = now
    stats["requests"] = len(order)
    stats["first_ts"], stats["last_ts"] = first_ts, last_ts


def _timestamp(value):
    """Seconds since the epoch of an ISO-8601 UTC timestamp, fractions kept; None if unreadable."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        whole = calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    fraction = re.match(r"\.(\d+)", value[19:])
    return whole + (float("0." + fraction.group(1)) if fraction else 0.0)


def transcripts(root, days):
    cutoff = time.time() - days * 86400
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            if not name.endswith(".jsonl"):
                continue
            try:
                recent = os.path.getmtime(path) >= cutoff
            except OSError:  # removed while walking, or a broken link
                continue
            if recent:
                yield path


def parse_all(root, days, workers=6):
    paths = list(transcripts(root, days))
    if workers <= 1:
        return [parse_transcript(p) for p in paths]
    with Pool(workers) as pool:
        return list(pool.imap_unordered(parse_transcript, paths, chunksize=8))


def agent_label(meta):
    return f"{meta.get('agentType') or '?'}|{meta.get('model') or 'inherit'}"


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0


def build_report(results, days):
    failed = [r for r in results if r["failed"]]
    results = [r for r in results if r["requests"]]
    main = [r for r in results if not r["sub"]]
    subs = [r for r in results if r["sub"]]
    total_main = sum(sum(r["cost"].values()) for r in main)
    total_sub = sum(sum(r["cost"].values()) for r in subs)
    total = total_main + total_sub or 1
    lines = [f"# Token audit: last {days} days, {len(main)} main sessions, {len(subs)} subagents",
             f"effective tokens: {total:,.0f} (main {total_main / total:.1%}, "
             f"subagents {total_sub / total:.1%}), requests: {sum(r['requests'] for r in results):,}"]
    bad_lines = sum(r["bad_lines"] for r in results)
    if bad_lines:
        lines.append(f"unparseable lines skipped: {bad_lines:,}")
    if failed:
        lines.append(f"unreadable transcripts skipped: {len(failed)} (first: {failed[0]['path']}: {failed[0]['failed']})")
    for label, group in (("main", main), ("subagents", subs)):
        split = Counter()
        for r in group:
            split.update(r["cost"])
        whole = sum(split.values()) or 1
        lines.append(f"{label} by class: " + ", ".join(
            f"{k} {v / whole:.1%}" for k, v in split.most_common()))
    lines.append(f"first-request prefix p50/p90: main {percentile([r['first_prefix'] for r in main], .5):,}"
                 f"/{percentile([r['first_prefix'] for r in main], .9):,}, subagents "
                 f"{percentile([r['first_prefix'] for r in subs], .5):,}"
                 f"/{percentile([r['first_prefix'] for r in subs], .9):,}")
    rebuilds = [(r["sub"], size, gap) for r in results for size, gap in r["rebuilds"]]
    lines.append(f"cache rebuilds >{REBUILD_MIN_TOKENS // 1000}k after the first request: {len(rebuilds)}, "
                 f"{sum(s for _, s, _ in rebuilds):,} tokens rewritten")
    for is_sub in (False, True):
        rows = [g for s, _, g in rebuilds if s == is_sub]
        lines.append(f"  {'subagents' if is_sub else 'main'}: gap <5 min {sum(1 for g in rows if g is not None and g <= 300)}, "
                     f"5-60 min {sum(1 for g in rows if g and 300 < g <= 3600)}, "
                     f">60 min {sum(1 for g in rows if g and g > 3600)}, "
                     f"unknown {sum(1 for g in rows if g is None)}")

    lines += ["", "## Subagents by type|model: count, effective tokens, share, avg requests, prefix p50"]
    by_type = defaultdict(list)
    for r in subs:
        by_type[agent_label(r["meta"])].append(r)
    for label, group in sorted(by_type.items(), key=lambda kv: -sum(sum(r["cost"].values()) for r in kv[1])):
        spent = sum(sum(r["cost"].values()) for r in group)
        lines.append(f"{len(group):>6} {spent:>16,.0f} {spent / total:>6.1%} "
                     f"{sum(r['requests'] for r in group) / len(group):>6.0f} "
                     f"{percentile([r['first_prefix'] for r in group], .5):>8,}  {label}")

    tools, result_chars, skills = Counter(), Counter(), Counter()
    error_transcripts, repeated = Counter(), Counter()
    rereads = 0
    for r in results:
        tools.update(r["tools"])
        result_chars.update(r["result_chars"])
        skills.update(r["skills"])
        error_transcripts.update(r["errors"].keys())
        repeated.update(sig for sig, n in r["errors"].items() if n >= 3)
        rereads += sum(n - 1 for n in r["reads"].values() if n >= 3)
    servers = Counter()
    for name, n in tools.items():
        if name.startswith("mcp__"):
            servers[name.split("__")[1]] += n
    lines += ["", f"Read of the same file >=3x in one transcript: {rereads:,} extra reads"]
    lines += ["", "## Tool calls (top 30)"] + [f"{n:>8}  {k}" for k, n in tools.most_common(30)]
    lines += ["", "## MCP servers by calls"] + [f"{n:>8}  {k}" for k, n in servers.most_common()]
    lines += ["", "## Tool result chars (top 15, /4 ~ tokens)"]
    lines += [f"{n:>14,}  {k}" for k, n in result_chars.most_common(15)]
    lines += ["", "## Skills invoked"] + [f"{n:>6}  {k}" for k, n in skills.most_common()]
    lines += ["", "## Recurring errors: transcripts per signature (>=4)"]
    lines += [f"{n:>6}  {k}" for k, n in error_transcripts.most_common() if n >= 4][:60]
    lines += ["", "## Same failure >=3x in one transcript: transcripts per signature"]
    lines += [f"{n:>6}  {k}" for k, n in repeated.most_common(40)]
    return "\n".join(lines) + "\n"


def build_runs(results, match):
    rows = [r for r in results if r["sub"] and r["requests"]
            and match.lower() in str(r["meta"].get("description", "")).lower()]
    lines = ["effective_tokens  requests  prefix  output_tokens  agent  description"]
    for r in sorted(rows, key=lambda r: str(r["meta"].get("description"))):
        lines.append(f"{sum(r['cost'].values()):>16,.0f}  {r['requests']:>8}  {r['first_prefix']:>6,}  "
                     f"{r['cost']['output'] / WEIGHTS['output']:>13,.0f}  {agent_label(r['meta'])}  "
                     f"{r['meta'].get('description')}")
    return "\n".join(lines) + "\n"


def first_user_text(path):
    """The text of the first `user` entry of a transcript: a string as is, a list of blocks as its text
    blocks joined by newlines. "" when that entry is unusable, None when a line up to it cannot be read."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                return None  # the first user entry cannot be told, so no marker is read from a later one
            if not isinstance(entry, dict) or entry.get("type") != "user":
                continue
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(str(b.get("text") or "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text")
            return ""
    return ""


def job_marker(text):
    """The id of a `Job: <ID>` line 1, else None: later lines and inline mentions do not count."""
    found = JOB_LINE.fullmatch((text or "").split("\n", 1)[0].rstrip("\r"))
    return found.group(1) if found else None


def job_rows(root, job, days):
    """Parsed subagent transcripts whose first user message starts with the line `Job: <job>`."""
    return [parse_transcript(path) for path in transcripts(root, days)
            if is_subagent(path) and job_marker(first_user_text(path)) == job]


def torn_agents(root, days):
    """Subagent transcripts whose first user entry cannot be told: any job may own them."""
    return sorted(path for path in transcripts(root, days) if is_subagent(path) and first_user_text(path) is None)


def session_agents(root, session, days):
    """(the session has a transcript, parsed subagents of that session with no `Job:` line at all)."""
    known, unattributed = False, []
    for path in transcripts(root, days):
        if session_of(path, {}) != session:
            continue
        known = True
        if is_subagent(path) and job_marker(first_user_text(path)) is None:
            unattributed.append(parse_transcript(path))
    return known, unattributed


def _is_rate(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def price_problems(prices):
    """What is wrong with a price table: [] when it is non-empty and every model has the five classes."""
    table = prices.get("usd_per_mtok") if isinstance(prices, dict) else None
    if not isinstance(table, dict) or not table:
        return ["usd_per_mtok is missing or empty"]
    problems = []
    for model, rates in sorted(table.items()):
        if not isinstance(rates, dict):
            problems.append(f"{model}: not an object")
            continue
        problems += [f"{model}: {name} is missing or not a non-negative number"
                     for name in TOKEN_CLASSES if not _is_rate(rates.get(name))]
        if "prompt_limit" in rates and not _is_rate(rates["prompt_limit"]):
            problems.append(f"{model}: prompt_limit is not a non-negative number")
    return problems


def load_prices(path=None):
    """prices.json beside this script; ValueError (or OSError) when it cannot price anything."""
    path = path or PRICES_PATH
    with open(path, encoding="utf-8") as fh:
        prices = json.load(fh)
    problems = price_problems(prices)
    if problems:
        raise ValueError(f"{os.path.basename(path)}: " + "; ".join(problems))
    return prices


def _clock(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def transcript_problems(r):
    name = os.path.basename(r["path"])
    problems = [f"{name} unreadable: {r['failed']}"] if r["failed"] else []
    if r["bad_lines"]:
        problems.append(f"{r['bad_lines']} unparseable line(s) in {name}")
    return problems


def build_job(rows, job, prices, days, unattributed=None, torn=()):
    """The per-job report and whether it is complete. Incomplete (an `INCOMPLETE:` line each): a model
    with tokens and no price, a prompt past its model's prompt_limit, a transcript that could not be read,
    unparseable lines, in a row or an unattributed agent, a `torn` subagent transcript."""
    rates = prices["usd_per_mtok"]
    problems, unpriced, past_limit = [], Counter(), {}
    lines = [f"# Job {job}: {len(rows)} agents",
             "agent  model  requests  input  cache_read  cache_write_5m  cache_write_1h  output  effective  usd  "
             "duration  transcript"]
    total, total_usd, total_requests, all_priced = Counter(), 0.0, 0, True
    rows = sorted(rows, key=lambda r: (r["first_ts"] is None, r["first_ts"] or 0, r["path"]))
    for r in rows:
        name = os.path.basename(r["path"])
        tokens, usd, priced = Counter(), 0.0, True
        for model, counts in sorted(r["by_model"].items()):
            tokens.update(counts)
            rate = rates.get(model)
            if rate is None:
                priced = False
                unpriced[model] += sum(counts.values())
            else:
                usd += sum(counts[c] * rate[c] for c in TOKEN_CLASSES) / 1e6
                largest = r["largest_prompt"].get(model, 0)
                if largest > rate.get("prompt_limit", math.inf):
                    priced = False
                    past_limit[model] = max(past_limit.get(model, 0), largest)
        problems += transcript_problems(r)
        effective = sum(r["cost"].values())
        duration = f"{r['last_ts'] - r['first_ts']:.0f}s" if r["first_ts"] is not None else "-"
        lines.append(f"{agent_label(r['meta'])}  {'+'.join(sorted(r['by_model'])) or '-'}  {r['requests']}  "
                     + "  ".join(f"{tokens[c]:,.0f}" for c in TOKEN_CLASSES)
                     + f"  {effective:,.0f}  {usd:.6f}{'' if priced else '+?'}  {duration}  {name[:-len('.jsonl')]}")
        total.update(tokens)
        total_usd += usd
        total_requests += r["requests"]
        all_priced &= priced
    lines.append(f"total  -  {total_requests}  " + "  ".join(f"{total[c]:,.0f}" for c in TOKEN_CLASSES)
                 + f"  {sum(sum(r['cost'].values()) for r in rows):,.0f}  {total_usd:.6f}{'' if all_priced else '+?'}"
                 "  -  -")
    firsts = [r["first_ts"] for r in rows if r["first_ts"] is not None]
    lasts = [r["last_ts"] for r in rows if r["last_ts"] is not None]
    lines.append(f"wall {max(lasts) - min(firsts):.0f}s {_clock(min(firsts))} -> {_clock(max(lasts))}"
                 if firsts and lasts else "wall -")
    lines.append(f"window: last {days} days")
    lines.append(f"prices: as of {prices.get('as_of', '?')} ({prices.get('source', '?')})")
    if past_limit and prices.get("notes"):
        lines.append(f"note: {prices['notes']}")
    if unattributed is not None:
        lines.append(f"unattributed: {len(unattributed)} agents, "
                     f"{sum(sum(r['cost'].values()) for r in unattributed):,.0f} effective tokens")
        for r in unattributed:
            problems += transcript_problems(r)
    problems = ([f"model {m} has {n:,.0f} tokens and no price in prices.json" for m, n in unpriced.items()]
                + [f"model {m} has a {n:,.0f}-token prompt past its {rates[m]['prompt_limit']:,.0f}-token price limit"
                   for m, n in past_limit.items()]
                + problems
                + [f"first user line of {os.path.basename(p)} cannot be read, so its job is unknown" for p in torn])
    lines += [f"INCOMPLETE: {p}" for p in problems]
    return "\n".join(lines) + "\n", not problems


def _refuse(reason):
    sys.stderr.write(reason + "\n")
    return 1


def run_job(args):
    if not os.path.isdir(args.root):
        return _refuse(f"job: transcripts folder not found: {args.root}")
    try:
        prices = load_prices()
    except (OSError, ValueError) as exc:
        return _refuse(f"job: cannot use prices: {exc}")
    unattributed = None
    if args.session is not None:
        known, unattributed = session_agents(args.root, args.session, args.days)
        if not known:
            return _refuse(f"job: no transcript of session {args.session} under {args.root} "
                           f"in the last {args.days} days")
    rows = job_rows(args.root, args.job, args.days)
    if not rows:
        return _refuse(f'job: no subagent transcript starts with "Job: {args.job}" under {args.root} '
                       f"in the last {args.days} days")
    text, complete = build_job(rows, args.job, prices, args.days, unattributed, torn_agents(args.root, args.days))
    sys.stdout.write(text)
    return 0 if complete else 1


def job_id(value):
    if not value or re.search(r"\s", value):
        raise argparse.ArgumentTypeError("a job id is a non-empty string without whitespace")
    return value


def build_prefix(path):
    """Sizes in characters (about 4 per token) of what the first request carried: system and tool
    schemas from the api-request-shape line, plus every attachment written before the first
    assistant reply (hook status entries among them may not reach the model)."""
    shape, attachments = None, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            if kind == "api-request-shape" and shape is None:
                shape = entry.get("shape") if isinstance(entry.get("shape"), dict) else {}
            elif kind == "attachment" and isinstance(entry.get("attachment"), dict):
                attachment = entry["attachment"]
                attachments.append((str(attachment.get("type", "?")),
                                    len(json.dumps(attachment, ensure_ascii=False))))
            elif kind == "assistant":
                break
    if shape is None:
        return "no api-request-shape line in this transcript\n"
    rows = [("system", f"block {i}", len(str(b.get("text") or "")))
            for i, b in enumerate(shape.get("system") or []) if isinstance(b, dict)]
    rows += [("tool", str(t.get("name", "?")), len(json.dumps(t, ensure_ascii=False)))
             for t in shape.get("tools") or [] if isinstance(t, dict)]
    rows += [("attachment", kind, size) for kind, size in attachments]
    groups = Counter()
    for group, _, size in rows:
        groups[group] += size
    lines = [f"chars by group: " + ", ".join(f"{k} {v:,}" for k, v in groups.most_common())
             + f"; total {sum(groups.values()):,}; tools {len(shape.get('tools') or [])}"]
    lines += [f"{size:>9,}  {group:<10} {name}" for group, name, size in sorted(rows, key=lambda r: -r[2])[:40]]
    return "\n".join(lines) + "\n"


SKILL_READ = re.compile(r"[\\/]([^\\/]+)[\\/]SKILL\.md$", re.I)
ALLOW_COMMENT = re.compile(r"#\s*shell-guard:\s*allow\b")
HOOK_MARKER = re.compile(r"\b(ERR_[A-Z_]+|E[A-Z]{3,9})\b")
HOOK_PREFIX = "Failed with non-blocking status code:"
KINDS = ("env-defect", "knowledge", "judgment", "shortcut")


def session_of(path, entry):
    """The top-level session an entry belongs to: subagent lines carry their parent's id."""
    sid = entry.get("sessionId")
    if isinstance(sid, str) and sid:
        return sid
    parts = os.path.normpath(path).split(os.sep)
    if "subagents" in parts[:-1]:
        return parts[parts.index("subagents") - 1]
    return os.path.splitext(parts[-1])[0]


def skill_name(name):
    """`plugin:skill` and a SKILL.md folder name count as the same skill."""
    return str(name).rsplit(":", 1)[-1]


def hook_marker(stderr):
    text = str(stderr)
    found = HOOK_MARKER.search(text)
    if found:
        return found.group(1)
    lines = [ln.strip() for ln in text.replace(HOOK_PREFIX, "").splitlines() if ln.strip()]
    return mask(lines[0], 60) if lines else "<no stderr>"


def parse_events(path):
    """Dated events of one transcript for the archive report, each with an id to drop the copies a
    resumed session repeats: failed tool results, skill use, shell-guard escapes and hook errors."""
    events = {"errors": [], "skills": [], "allow": [], "hooks": [], "failed": None}
    try:
        _events_into(events, path)
    except Exception as exc:  # one unreadable file must not abort the whole report
        events = {"errors": [], "skills": [], "allow": [], "hooks": [], "failed": f"{type(exc).__name__}: {exc}"}
    return events


def _events_into(events, path):
    pending = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, line in enumerate(fh):
            if '"tool_use"' not in line and '"tool_result"' not in line and "hook_non_blocking_error" not in line:
                continue
            here = f"{path}#{number}"  # the id of an event whose own id is missing
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(entry, dict):
                continue
            at = entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None
            session = session_of(path, entry)
            attachment = entry.get("attachment")
            if entry.get("type") == "attachment" and isinstance(attachment, dict):
                if attachment.get("type") == "hook_non_blocking_error":
                    events["hooks"].append((entry.get("uuid") or here, session, at,
                                            str(attachment.get("hookName") or "?"),
                                            # paths kept: the script path is what tells two plugins' hooks apart
                                            scrub(attachment.get("command") or "?")[:120],
                                            hook_marker(attachment.get("stderr"))))
                continue
            message = entry.get("message")
            if entry.get("type") not in ("assistant", "user") or not isinstance(message, dict):
                continue
            content = message.get("content")
            for block in content if isinstance(content, list) else ():
                if not isinstance(block, dict):
                    continue
                if entry["type"] == "assistant" and block.get("type") == "tool_use":
                    tid, name = block.get("id"), str(block.get("name", "?"))
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if not isinstance(tid, str):
                        continue
                    pending[tid] = (name, str(args.get("command", "")))
                    if name == "Skill":
                        events["skills"].append((tid, session, at, skill_name(args.get("skill", "?")), "Skill"))
                    elif name == "Read":
                        found = SKILL_READ.search(str(args.get("file_path", "")))
                        if found:
                            events["skills"].append((tid, session, at, found.group(1), "Read"))
                    elif name in SHELL_TOOLS and ALLOW_COMMENT.search(pending[tid][1]):
                        events["allow"].append((tid, session, at, name))
                elif entry["type"] == "user" and block.get("type") == "tool_result" and block.get("is_error"):
                    tid = block.get("tool_use_id")
                    name, _ = pending.get(tid, ("?", "")) if isinstance(tid, str) else ("?", "")
                    text = result_text(block.get("content"))
                    if text.lstrip().startswith(REJECTED_PREFIXES):
                        continue
                    events["errors"].append((tid if isinstance(tid, str) else here, session, at, name,
                                             error_line(name, text)))


def archive_events(root, days, workers=6):
    paths = list(transcripts(root, days))
    if workers <= 1:
        parsed = [parse_events(p) for p in paths]
    else:
        with Pool(workers) as pool:
            parsed = list(pool.imap_unordered(parse_events, paths, chunksize=8))
    return merge_events(parsed)


def merge_events(parsed):
    """One event per id across transcripts, in plain lists."""
    merged = {"errors": {}, "skills": {}, "allow": {}, "hooks": {}, "failed": []}
    for events in parsed:
        if events["failed"]:
            merged["failed"].append(events["failed"])
        for kind in ("errors", "skills", "allow", "hooks"):
            for event in events[kind]:
                # A resumed session repeats earlier lines; keep one copy, the same whatever the file order.
                kept = merged[kind].get(event[0])
                if kept is None or (event[2] or "", event[1]) < (kept[2] or "", kept[1]):
                    merged[kind][event[0]] = event
    return {k: (list(v.values()) if isinstance(v, dict) else v) for k, v in merged.items()}


def _month_summary(events):
    """Aggregate of one batch of events: what history/<YYYY-MM>.json keeps."""
    sessions, results = defaultdict(set), Counter()
    for _, session, _, tool, line in events["errors"]:
        key = group_key(tool, line)
        sessions[key].add(session)
        results[key] += 1
    skills = defaultdict(Counter)
    for _, _, _, name, via in events["skills"]:
        skills[name][via] += 1
    hooks = Counter(f"{hook} | {command}" for _, _, _, hook, command, _ in events["hooks"])
    return {"events": sum(len(events[k]) for k in ("errors", "skills", "allow", "hooks")),
            "error_results": len(events["errors"]),
            "errors_in_2_sessions": {line: {"sessions": len(s), "results": results[line]}
                                     for line, s in sessions.items() if len(s) >= 2},
            "skills": {name: dict(c) for name, c in skills.items()},
            "hooks": dict(hooks),
            "shell_guard_allow": len(events["allow"])}


def monthly_aggregates(events, since):
    """{YYYY-MM: summary} for the months from `since` (YYYY-MM-DD, the oldest transcript change
    read) on; older lines in a resumed transcript are too few to stand for their month."""
    by_month = defaultdict(lambda: {"errors": [], "skills": [], "allow": [], "hooks": []})
    for kind in ("errors", "skills", "allow", "hooks"):
        for event in events[kind]:
            if event[2] and event[2][:7] >= since[:7]:
                by_month[event[2][:7]][kind].append(event)
    return {month: dict(_month_summary(batch), month=month, transcripts_modified_since=since)
            for month, batch in sorted(by_month.items())}


def write_history(folder, aggregates):
    """Write each month unless the file already holds more events: transcripts expire, so a later
    run can only see less of a past month. Returns the months written."""
    os.makedirs(folder, exist_ok=True)
    written = []
    for month, data in aggregates.items():
        path = os.path.join(folder, f"{month}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            old_events = old.get("events", 0) if isinstance(old, dict) else 0
        except (OSError, ValueError):
            old_events = -1
        if _num(old_events) > data["events"]:
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        written.append(month)
    return written


def _in_window(event, start, end):
    when = _timestamp(event[2])
    return when is not None and start <= when <= end


def recurring_errors(cases, errors):
    """Error groups (by group_key) seen in >=2 sessions, and the cases whose error_text each matches:
    case-insensitively, masked the same way, anywhere in any whole line of the group.
    Returns (groups, recurring keys, [(case, masked error_text)], {key: [case, ...]})."""
    groups = defaultdict(lambda: {"sessions": set(), "results": 0, "tools": set(), "lines": set(), "last": ""})
    for _, session, at, tool, line in errors:
        group = groups[group_key(tool, line)]
        group["sessions"].add(session)
        group["results"] += 1
        group["tools"].add(tool)
        group["lines"].add(line.lower())
        group["last"] = max(group["last"], at or "")
    recurring = sorted((key for key, g in groups.items() if len(g["sessions"]) >= 2),
                       key=lambda key: (-len(groups[key]["sessions"]), -groups[key]["results"], key))
    texts = [(name, mask(meta["error_text"]).lower()) for name, meta in cases
             if isinstance(meta.get("error_text"), str) and meta["error_text"].strip()]
    matched = {key: [name for name, text in texts if any(text in line for line in groups[key]["lines"])]
               for key in recurring}
    return groups, recurring, texts, matched


def case_rows(groups, matched):
    """{case: (sessions merged over its lines, results, lines, last seen)} for every matched case."""
    rows = {}
    for key, names in matched.items():
        for name in names:
            sessions, results, lines, last = rows.get(name, (set(), 0, 0, ""))
            rows[name] = (sessions | groups[key]["sessions"], results + groups[key]["results"], lines + 1,
                          max(last, groups[key]["last"]))
    return rows


def build_archive(cases, events, now, days, expect_case=None, expect_hook=(), control_errors=None):
    """The archive report and whether every requested positive control was found.
    `cases` are (file name, frontmatter) pairs; `events` come from archive_events(). With
    `control_errors` (error events of a fixture, all dates) the case control reads those instead."""
    start = now - days * 86400
    since = time.strftime("%Y-%m-%d", time.gmtime(start))
    recent = {k: [e for e in events[k] if _in_window(e, start, now)] for k in ("errors", "skills", "allow", "hooks")}
    undated = sum(1 for k in recent for e in events[k] if _timestamp(e[2]) is None)
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    groups, recurring, texts, matched = recurring_errors(cases, recent["errors"])
    sessions = {key: g["sessions"] for key, g in groups.items()}
    results = {key: g["results"] for key, g in groups.items()}
    tools = {key: g["tools"] for key, g in groups.items()}
    with_case = [line for line in recurring if matched[line]]
    no_case = [line for line in recurring if not matched[line]]
    per_case = case_rows(groups, matched)

    hook_groups = defaultdict(lambda: {"n": 0, "sessions": set(), "first": None, "last": None, "markers": Counter()})
    for _, session, at, hook, command, marker in recent["hooks"]:
        group = hook_groups[(hook, command)]
        group["n"] += 1
        group["sessions"].add(session)
        group["markers"][marker] += 1
        day = at[:10]
        group["first"] = min(group["first"] or day, day)
        group["last"] = max(group["last"] or day, day)

    proposed = sorted((name for name, meta in cases if meta.get("guard") == "proposed"),
                      key=lambda name: (cases_meta(cases, name).get("date", ""), name))
    repeats = [(name, meta) for name, meta in cases if meta.get("repeat_of") not in (None, "none")]
    chains = _chains(cases)

    controls, ok = [], True
    if expect_case:
        source, rows = "", per_case
        if control_errors is not None:
            control_groups, _, _, control_matched = recurring_errors(cases, control_errors)
            source, rows = " of the control transcripts", case_rows(control_groups, control_matched)
        found = expect_case in rows
        ok &= found
        controls.append(f"positive control: case {expect_case} " + (
            f"matched {rows[expect_case][2]} recurring error line(s){source} in {len(rows[expect_case][0])} sessions"
            if found else f"NOT matched by any recurring error line{source}: FAILED"))
    if expect_hook:
        needles = [n.lower() for n in expect_hook]
        hits = [(key, g) for key, g in hook_groups.items()
                if all(n in " ".join([key[0], key[1], *g["markers"]]).lower() for n in needles)]
        found = bool(hits)
        ok &= found
        controls.append(f"positive control: hook error {' + '.join(expect_hook)} " + (
            f"counted {sum(g['n'] for _, g in hits)} times, first {min(g['first'] for _, g in hits)}"
            if found else "NOT counted: FAILED"))

    allow_tools = Counter(e[3] for e in recent["allow"])
    lines = [f"# Lessons archive: {len(cases)} cases, {len({m.get('class') for _, m in cases})} classes; "
             f"transcripts: last {days} days (since {since})"]
    lines += controls
    unseen = [name for name, _ in texts if not any(name in matched[l] for l in with_case)]
    repeat_classes = len({cls for cls, _ in chains})
    lines.append(f"open guards: {len(proposed)}; repeats: {len(repeats)} in {repeat_classes} classes; "
                 f"error lines in >=2 sessions: {len(recurring)} ({len(with_case)} with a case, "
                 f"{len(no_case)} with no case); cases whose error_text did not recur: {len(unseen)} of {len(texts)}")
    lines.append(f"hook non-blocking errors: {len(recent['hooks'])}; commands carrying "
                 f"'# shell-guard: allow': {len(recent['allow'])} ("
                 + ", ".join(f"{t} {n}" for t, n in sorted(allow_tools.items())) + ")")
    if undated or events.get("failed"):
        lines.append(f"undated events skipped: {undated}; unreadable transcripts: {len(events.get('failed') or [])}")

    lines += ["", "## Cases per month by kind", "month    " + " ".join(f"{k:>10}" for k in KINDS) + "  total"]
    months = defaultdict(Counter)
    for _, meta in cases:
        months[str(meta.get("date", ""))[:7]][meta.get("kind")] += 1
    for month in sorted(months):
        lines.append(f"{month:<8} " + " ".join(f"{months[month][k]:>10}" for k in KINDS)
                     + f"  {sum(months[month].values()):>5}")

    lines += ["", "## Machine-made repeats: error lines in >=2 sessions with an archive case",
              "sessions results  case | tools | error line"]
    lines += [f"{len(sessions[l]):>8} {results[l]:>7}  {', '.join(matched[l])} | {','.join(sorted(tools[l]))} | {l}"
              for l in with_case]
    lines += ["", "## Cases with a recurring error line: sessions merged over the case's lines",
              "sessions results lines last seen   case"]
    lines += [f"{len(s):>8} {n:>7} {k:>5} {last[:10]:<11} {name}"
              for name, (s, n, k, last) in sorted(per_case.items(), key=lambda kv: (-len(kv[1][0]), kv[0]))]
    lines += ["", f"## Recurring with no case: {len(no_case)} error lines (top 25)",
              "sessions results  tools | error line"]
    lines += [f"{len(sessions[l]):>8} {results[l]:>7}  {','.join(sorted(tools[l]))} | {l or '<empty>'}"
              for l in no_case[:25]]

    lines += ["", "## Open guards (guard: proposed), age in days", "  age  class | file"]
    for name in proposed:
        meta = cases_meta(cases, name)
        lines.append(f"{_age(meta.get('date'), today):>5}  {meta.get('class')} | {name}")

    lines += ["", f"## Repeats from repeat_of: {len(repeats)} in {repeat_classes} classes "
              f"(rule_loaded: " + ", ".join(f"{k} {n}" for k, n in sorted(
                  Counter(m.get('rule_loaded') for _, m in repeats).items(), key=str)) + ")"]
    for cls, chain in sorted(chains, key=lambda kv: (-len(kv[1]), kv[0], kv[1])):
        lines.append(f"{len(chain):>3}  {cls}: " + " -> ".join(f"{n[5:10]} {n[11:-3][:24]}" for n in chain))

    skills = defaultdict(Counter)
    for _, _, _, name, via in recent["skills"]:
        skills[name][via] += 1
    lines += ["", "## Skill use: Skill calls + Read of SKILL.md", " total  Skill   Read  skill"]
    for name, c in sorted(skills.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        lines.append(f"{sum(c.values()):>6} {c['Skill']:>6} {c['Read']:>6}  {name}")

    lines += ["", "## Hook non-blocking errors by hook and command",
              " count sessions first      last       error | hook | command"]
    for (hook, command), g in sorted(hook_groups.items(), key=lambda kv: -kv[1]["n"]):
        marker = ", ".join(f"{m} {n}" for m, n in g["markers"].most_common(2))
        lines.append(f"{g['n']:>6} {len(g['sessions']):>8} {g['first']} {g['last']} {marker} | {hook} | {command}")
    return "\n".join(lines) + "\n", ok


def cases_meta(cases, name):
    return next(meta for n, meta in cases if n == name)


def _age(date, today):
    """Whole days between two YYYY-MM-DD dates (the lint guarantees the case date's format)."""
    return (calendar.timegm(time.strptime(today, "%Y-%m-%d"))
            - calendar.timegm(time.strptime(str(date), "%Y-%m-%d"))) // 86400


def _chains(cases):
    """[(class, [first, ..., newest])] for every newest case of a repeat_of chain."""
    meta = dict(cases)
    chains = []
    for name, m in cases:
        if m.get("repeat_of") in (None, "none"):
            continue
        if any(m2.get("repeat_of") == name for m2 in meta.values()):
            continue  # an older link of a longer chain
        chain, current = [name], m.get("repeat_of")
        while current in meta and current not in chain:
            chain.append(current)
            current = meta[current].get("repeat_of")
        chains.append((m.get("class"), list(reversed(chain))))
    return chains


def config_dir():
    """`$CLAUDE_CONFIG_DIR`, else `~/.claude`."""
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude"))


def default_root():
    """The transcripts folder: `<config dir>/projects`."""
    return os.path.join(config_dir(), "projects")


def installed_lint():
    """The rigger-lessons lint of the installed plugin: the first `installPath` of a `rigger-lessons@<marketplace>`
    entry in `<config dir>/plugins/installed_plugins.json` that holds tools/lessons_lint.py, else None."""
    try:
        with open(os.path.join(config_dir(), "plugins", "installed_plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins")
    except (OSError, ValueError, AttributeError):
        return None
    for key, entries in sorted(plugins.items()) if isinstance(plugins, dict) else ():
        if key.split("@")[0] != "rigger-lessons" or not isinstance(entries, list):
            continue
        for entry in entries:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            lint = os.path.join(path, "tools", "lessons_lint.py") if isinstance(path, str) else None
            if lint and os.path.isfile(lint):
                return lint
    return None


def load_cases(lessons_dir, lint):
    """(file name, frontmatter) of every case, through the rigger-lessons lint's reader at `lint`.
    Only `archive` needs it: the other commands run without the lint."""
    spec = importlib.util.spec_from_file_location("token_audit_lessons_lint", lint)
    lessons_lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lessons_lint)
    return [(name, meta) for name, meta, _, _ in lessons_lint.load_cases(lessons_dir)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="transcripts folder (default: <config dir>/projects)")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("--days", type=int, default=30)
    report.add_argument("--out")
    runs = sub.add_parser("runs")
    runs.add_argument("--match", required=True)
    runs.add_argument("--days", type=int, default=30)
    job = sub.add_parser("job")
    job.add_argument("--job", required=True, type=job_id,
                     help="job id: the whole first line of the subagent's first user message is `Job: <ID>`")
    job.add_argument("--session", help="session id: also count that session's subagents with no `Job:` line")
    job.add_argument("--days", type=int, default=30)
    prefix = sub.add_parser("prefix")
    prefix.add_argument("transcript")
    archive = sub.add_parser("archive")
    archive.add_argument("--days", type=int, default=30)
    archive.add_argument("--lessons", required=True, help="lessons case archive folder")
    archive.add_argument("--lint",
                         help="the rigger-lessons lint, whose reader loads the cases (default: the installed "
                              "rigger-lessons plugin's, from <config dir>/plugins/installed_plugins.json)")
    archive.add_argument("--history-dir", help="folder for the history files (needed by --write-history)")
    archive.add_argument("--expect-case", help="case file that must match a recurring error line")
    archive.add_argument("--control-root",
                         help="transcript folder (a fixture, any date) the --expect-case control reads instead of --root")
    archive.add_argument("--expect-hook", action="append", default=[],
                         help="text that one hook error group must contain (repeatable, all must match)")
    archive.add_argument("--write-history", action="store_true",
                         help="write history/<YYYY-MM>.json; nothing is written when a control fails")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parser.parse_args(argv)
    args.root = args.root or default_root()
    if args.command == "job":
        return run_job(args)
    if args.command == "archive":
        if args.write_history and not args.history_dir:
            parser.error("--write-history needs --history-dir")
        args.lint = args.lint or installed_lint()
        if args.lint is None:
            parser.error("no rigger-lessons plugin with tools/lessons_lint.py in "
                         f"{os.path.join(config_dir(), 'plugins', 'installed_plugins.json')} "
                         "(pass --lint <rigger-lessons>/tools/lessons_lint.py)")
        if not os.path.isfile(args.lint):
            parser.error(f"lint not found: {args.lint} (pass --lint <rigger-lessons>/tools/lessons_lint.py)")
        now = time.time()
        events = archive_events(args.root, args.days)
        control = archive_events(args.control_root, 36500, workers=1)["errors"] if args.control_root else None
        text, ok = build_archive(load_cases(args.lessons, args.lint), events, now, args.days,
                                 args.expect_case, args.expect_hook, control)
        if not args.write_history:
            sys.stdout.write(text + "history not written: no --write-history\n")
        elif not ok:
            sys.stdout.write(text + "history not written: a positive control failed\n")
        else:
            since = time.strftime("%Y-%m-%d", time.gmtime(now - args.days * 86400))
            written = write_history(args.history_dir, monthly_aggregates(events, since))
            sys.stdout.write(text + f"history written: {', '.join(written) or 'none'} -> {args.history_dir}\n")
        return 0 if ok else 1
    if args.command == "prefix":
        text = build_prefix(args.transcript)
    elif args.command == "runs":
        text = build_runs(parse_all(args.root, args.days), args.match)
    else:
        if not os.path.isdir(args.root):
            parser.error(f"transcripts folder not found: {args.root}")
        results = parse_all(args.root, args.days)
        if not results:
            parser.error(f"read 0 transcripts under {args.root} in the last {args.days} days")
        text = build_report(results, args.days)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
