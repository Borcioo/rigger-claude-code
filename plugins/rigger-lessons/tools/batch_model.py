"""Model side of a background lessons batch: the corpus, the `claude -p` command and environment, prompts, parsing.

HARNESS_LESSONS_CLAUDE (tests only): a JSON list of non-empty strings, the argv prefix that replaces `claude`. Any
other value gives no command at all, so a broken override never falls back to the real CLI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Mapping

import inbox_lint

DROP_PREFIXES = ("CLAUDE_CODE_MESSAGING_", "CLAUDE_CODE_HOST_", "CLAUDE_CODE_SDK_")
DROP_KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID")
TRIAGE_ACTIONS = ("merge", "covered", "propose", "keep")
PROPOSAL_KINDS = ("edit", "guard", "archive")
EVIDENCE = ("ok", "needs-evidence")
KILL_WAIT = 10
STEP2 = re.compile(r"^## Step 2\b.*?(?=^## Step 3\b)", re.M | re.S)

TRIAGE_SCHEMA = {
    "type": "object", "required": ["entries"],
    "properties": {"entries": {"type": "array", "items": {
        "type": "object", "required": ["line", "action", "reason"],
        "properties": {
            "line": {"type": "integer"},
            "action": {"enum": list(TRIAGE_ACTIONS)},
            "merge_into": {"type": ["integer", "null"]},
            "covered_by": {"type": ["string", "null"]},
            "quote": {"type": ["string", "null"]},
            "reason": {"type": "string"}}}}}}

PROPOSAL_SCHEMA = {
    "type": "object", "required": ["proposals"],
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object", "required": ["line", "kind", "target", "old_text", "new_text", "evidence", "reason"],
        "properties": {
            "line": {"type": "integer"},
            "kind": {"enum": list(PROPOSAL_KINDS)},
            "target": {"type": "string"},
            "heading": {"type": ["string", "null"]},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "evidence": {"enum": list(EVIDENCE)},
            "reason": {"type": "string"}}}}}}

TRIAGE_PROMPT = """You triage {count} entries of a lessons inbox. Edit nothing; answer only through the structured output.

The file `corpus.md` in your working directory holds every rules file and skill in use (the global lessons in rigger/rules.d included), each starting with a
`===== FILE: <path>` line. Grep it for the key terms of each entry before you decide; Read around a hit to find its
`#` heading.

For each entry choose one action:
- merge: another inbox entry (below, any line) describes the same class of mistake; give its line as merge_into.
  Merge only into an older entry: an earlier date, or the same date and a smaller line.
- covered: a rule or skill in corpus.md already says what the entry's proposed rule says, in substance. Give
  covered_by as `<path>#<heading>` and quote the sentence.
- propose: the entry is general (it would recur outside its one case), not covered, and worth a rule or skill edit.
- keep: too specific to one case, too vague, or not yet worth an edit; say why.
Reason: one line.

Entries to triage (line: entry):
{slice}

The whole inbox, numbered:
{inbox}
"""

PROPOSAL_PROMPT = """Write the exact rule or skill edit for each lesson below. Edit nothing; answer only through the
structured output. `corpus.md` in your working directory holds every rules file and skill, including the global lessons in rigger/rules.d, each starting with a
`===== FILE: <path>` line; Read the target section before you write anything.

For each lesson give one item, with kind:
- edit: target is the path as corpus.md names it; old_text is copied exactly from that file and occurs exactly once
  in it; new_text replaces it.
- guard: a process guard (test, lint, hook, helper, template) is the better fix; describe it in new_text with its
  cost, old_text may be empty.
- archive: no edit is right (the rule already says it, or it is not worth one); say why in reason.
evidence: ok when the lesson's signal_type is user-correction, test-failure or review-finding; else needs-evidence.

Follow these routing rules (from the curate-lessons skill):
{rules}

Lessons (line: entry), each with the triage note:
{entries}
"""


def install_paths(config: Path) -> list[tuple[str, Path]]:
    """(plugin key, installPath) of the first install of each plugin in installed_plugins.json; a wrong shape skips
    that part, an unreadable file gives none."""
    try:
        with open(config / "plugins" / "installed_plugins.json", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError, RecursionError):
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return []
    found = []
    for key, items in plugins.items():
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            continue
        path = items[0].get("installPath")
        if isinstance(path, str) and os.path.isabs(path):
            found.append((key, Path(path)))
    return found


def corpus_files(config: Path) -> list[Path]:
    files = []
    for _, root in install_paths(config):
        files += sorted(root.glob("rules/*.md")) + sorted(root.glob("skills/*/SKILL.md"))
    files += sorted((config / "skills").glob("*/SKILL.md"))
    files += sorted((config / "rigger" / "rules.d").glob("*.md"))
    if (config / "CLAUDE.md").is_file():
        files.append(config / "CLAUDE.md")
    return files


def write_corpus(run_dir: Path, config: Path) -> int:
    """corpus.md in run_dir, each file as `===== FILE: <posix path>` then its text; returns the file count."""
    files = corpus_files(config)
    with open(run_dir / "corpus.md", "w", encoding="utf-8", newline="") as handle:
        for path in files:
            with open(path, encoding="utf-8", errors="replace") as source:
                handle.write("===== FILE: %s\n%s\n" % (path.as_posix(), source.read()))
    return len(files)


def step2_rules(skill_text: str) -> str:
    """The curate-lessons `## Step 2` section up to `## Step 3`, "" when either heading is missing."""
    match = STEP2.search(skill_text)
    return match.group(0).strip() if match else ""


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    env = {key: value for key, value in environ.items()
           if not key.upper().startswith(DROP_PREFIXES) and key.upper() not in DROP_KEYS}
    env.update(CLAUDE_CODE_CHILD_SESSION="1", HARNESS_LESSONS_BATCH="1")
    return env


def claude_argv(environ: Mapping[str, str], model: str, schema: dict, budget: float) -> list[str] | None:
    override = environ.get("HARNESS_LESSONS_CLAUDE", "")
    if override:
        try:
            prefix = json.loads(override)
        except ValueError:
            return None
        if not isinstance(prefix, list) or not prefix or not all(isinstance(part, str) and part for part in prefix):
            return None
    else:
        found = shutil.which("claude", path=environ.get("PATH") or os.defpath)
        if not found:
            return None
        prefix = [found]
    return prefix + ["-p", "--model", model, "--output-format", "json", "--json-schema", json.dumps(schema),
                     "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
                     "--disable-slash-commands", "--tools", "Read,Grep,Glob", "--max-budget-usd", str(budget)]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                       timeout=KILL_WAIT)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _first_object(text):
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _lf(value):
    """CRLF in every string of a parsed value becomes LF, so old_text matches a file read with universal newlines."""
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    if isinstance(value, list):
        return [_lf(item) for item in value]
    if isinstance(value, dict):
        return {key: _lf(item) for key, item in value.items()}
    return value


def call(argv: list[str], prompt: str, env: dict, cwd: Path, timeout: float) -> dict:
    """Run one `claude -p` call with the prompt on stdin. Windows: no console window; POSIX: its own session, so
    a timeout kills the whole tree."""
    result = {"ok": False, "value": None, "usage": {}, "cost": None, "seconds": 0.0, "error": None, "stderr": ""}
    started = time.monotonic()
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, **extra)
    except OSError as exc:
        result["error"] = "start failed (%s: %s)" % (type(exc).__name__, exc)
        return result
    try:
        out, err = proc.communicate(prompt.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        result["error"] = "timeout"
    result["seconds"] = round(time.monotonic() - started, 1)
    result["stderr"] = err.decode("utf-8", "replace")[-2000:]
    if result["error"]:
        return result
    if proc.returncode != 0:
        result["error"] = "exit %d" % proc.returncode
        return result
    try:
        payload = json.loads(out.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        result["error"] = "unparsed"
        return result
    usage, cost = payload.get("modelUsage"), payload.get("total_cost_usd")
    result["usage"] = usage if isinstance(usage, dict) else {}
    result["cost"] = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    value = payload.get("structured_output")
    if value is None:
        value = _first_object(payload.get("result"))
    if value is None:
        result["error"] = "unparsed"
    else:
        result.update(ok=True, value=_lf(value))
    return result


def _numbered_entries(inbox: str) -> list[str]:
    return ["%d: %s" % (number, line.rstrip("\r")) for number, line in enumerate(inbox.split("\n"), 1)
            if inbox_lint.ENTRY.match(line)]


def triage_prompt(slice_: list[tuple[int, str]], inbox: str) -> str:
    return TRIAGE_PROMPT.format(count=len(slice_), slice="\n".join("%d: %s" % item for item in slice_),
                                inbox="\n".join(_numbered_entries(inbox)))


def proposal_prompt(items: list[dict], rules: str) -> str:
    lines = []
    for item in items:
        note = "%s: %s" % (item.get("action", ""), item.get("reason", ""))
        if item.get("covered_by"):
            note += " (covered by %s: %s)" % (item["covered_by"], item.get("quote", ""))
        lines.append("%d: %s\n   triage: %s" % (item["line"], item["entry"], note))
    return PROPOSAL_PROMPT.format(rules=rules, entries="\n".join(lines))


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _line(value, lines) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in lines


def _items(value, key: str, field: str, known: tuple, lines: set[int]) -> list[dict]:
    """Dict items under value[key] with a known value[field] and a line in `lines`, the first one per line."""
    items = value.get(key) if isinstance(value, dict) else None
    kept, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get(field) not in known or not _line(item.get("line"), lines):
            continue
        if item["line"] in seen:
            continue
        seen.add(item["line"])
        kept.append(item)
    return kept


def parse_triage(value, lines: set[int]) -> list[dict]:
    return [{"line": item["line"], "action": item["action"],
             "merge_into": item.get("merge_into") if _line(item.get("merge_into"), range(1, 1 << 31)) else None,
             "covered_by": _text(item.get("covered_by")), "quote": _text(item.get("quote")),
             "reason": _text(item.get("reason"))}
            for item in _items(value, "entries", "action", TRIAGE_ACTIONS, lines)]


def parse_proposals(value, lines: set[int]) -> list[dict]:
    return [dict({key: _text(item.get(key)) for key in ("target", "heading", "old_text", "new_text", "reason")},
                 line=item["line"], kind=item["kind"], evidence=item["evidence"])
            for item in _items(value, "proposals", "kind", PROPOSAL_KINDS, lines) if item.get("evidence") in EVIDENCE]
