"""Today's rules output, from the frozen handlers of release 0.4.0, and the normal form the golden test compares.

`run_baseline` runs every frozen rules handler of a layout through a real POSIX sh, as Claude Code
runs hook commands. `normalize` turns handler output or `assemble.py --render` output into the
measure of plan decision D1: the set of `Plugin root:` lines, and per module the ordered list of
(heading line, bytes) pieces. Subagent JSON is decoded first.
"""

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

GOLDEN_BASE = "34e7713"
ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / ("golden-" + GOLDEN_BASE)
PLUGINS_WITH_RULES = ("rigger-core", "rigger-rules", "rigger-lessons", "rigger-talk", "rigger-wp", "rigger-windows")
HANDLER_SCRIPTS = ("rules_start.sh", "rules_extra.sh", "talk_start.sh")
EXTRA_SCRIPT = "rules_extra.sh"
PERSONAL = "personal"
PREAMBLE = "_preamble"
HEAD = "_head"  # text before the first root line of --render output (notices)
ROOT_PREFIX = b"Plugin root: "
PERSONAL_ROOT = ROOT_PREFIX + b"personal"
_BLANK = b" \t\r"
_SCRIPT = re.compile(r"/(rules|hooks)/([A-Za-z0-9_.-]+\.sh)\"")


def find_sh() -> Optional[str]:
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def root_text(path: Path) -> str:
    """CLAUDE_PLUGIN_ROOT as Claude Code sets it: forward slashes on Windows (spike S3)."""
    return path.as_posix() if os.name == "nt" else str(path)


def _fires(matcher: Optional[str], source: Optional[str]) -> bool:
    if matcher in (None, "", "*"):
        return True
    return source is not None and re.fullmatch(matcher, source) is not None


def handler_commands(plugin_root: Path, event: str, source: Optional[str]) -> List[str]:
    """The rules handler commands of one plugin's hooks.json for an event, in file order."""
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_bytes().decode("utf-8"))["hooks"]
    commands = []
    for group in hooks.get(event, []):
        if event == "SessionStart" and not _fires(group.get("matcher"), source):
            continue
        for hook in group.get("hooks", []):
            found = _SCRIPT.search(hook.get("command", ""))
            if found and found.group(2) in HANDLER_SCRIPTS:
                commands.append(hook["command"])
    return commands


def hook_input(event: str, source: Optional[str], agent_type: Optional[str], session_id: str, cwd: Path) -> bytes:
    """Hook stdin as Claude Code writes it (JSON.stringify: no spaces)."""
    data = {"session_id": session_id, "transcript_path": "", "cwd": str(cwd), "hook_event_name": event}
    if event == "SessionStart":
        data["source"] = source
    else:
        data["agent_id"] = "golden-agent"
        data["agent_type"] = agent_type
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def run_baseline(layout: Path, plugins: List[str], event: str, source: Optional[str], agent_type: Optional[str],
                 env: dict, session_id: str = "golden-session", cwd: Optional[Path] = None) -> Dict[str, List[bytes]]:
    """Each plugin's rules handler stdout, in hooks.json order; rules_extra.sh output is filed under `personal`.

    `layout` holds one folder per plugin; CLAUDE_PLUGIN_ROOT is `<layout>/<plugin>`. Handlers run in
    parallel (Claude Code runs a group's hooks in parallel too); results keep hooks.json order.
    """
    sh = find_sh()
    if sh is None:
        raise RuntimeError("no POSIX sh found (Windows needs Git for Windows)")
    stdin = hook_input(event, source, agent_type, session_id, cwd or layout)
    jobs = []  # (owner, plugin root, command)
    for plugin in plugins:
        root = layout / plugin
        for command in handler_commands(root, event, source):
            owner = PERSONAL if ("/" + EXTRA_SCRIPT) in command else plugin
            jobs.append((owner, root, command))

    def run(job):
        _, root, command = job
        child = dict(env)
        child["CLAUDE_PLUGIN_ROOT"] = root_text(root)
        child["PATH"] = os.pathsep.join([str(Path(sh).parent), child.get("PATH", "")])
        done = subprocess.run([sh, "-c", command], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=child, timeout=60)
        if done.returncode != 0 or done.stderr:
            raise RuntimeError("handler %r exited %d: %r" % (command, done.returncode, done.stderr[:500]))
        return done.stdout

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, jobs))
    outputs = {}  # type: Dict[str, List[bytes]]
    for (owner, _, _), out in zip(jobs, results):
        outputs.setdefault(owner, []).append(out)
    return outputs


def decode_subagent(out: bytes) -> bytes:
    """A SubagentStart handler's additionalContext as UTF-8 bytes; b"" stays b""."""
    if not out:
        return b""
    data = json.loads(out.decode("utf-8"))
    hook = data["hookSpecificOutput"]
    if set(data) != {"hookSpecificOutput"} or set(hook) != {"hookEventName", "additionalContext"} \
            or hook["hookEventName"] != "SubagentStart":
        raise ValueError("unexpected SubagentStart JSON shape: %r" % out[:200])
    return hook["additionalContext"].encode("utf-8", "surrogatepass")


def lf_lines(data: bytes) -> List[bytes]:
    """Lines split at LF only, each keeping its LF (bytes.splitlines would also split at a lone CR)."""
    lines = [line + b"\n" for line in data.split(b"\n")]
    lines[-1] = lines[-1][:-1]
    return [line for line in lines if line]


def _pieces(stream: bytes) -> List[Tuple[str, bytes]]:
    pieces = []  # type: List[Tuple[str, bytes]]
    key, chunk = PREAMBLE, []  # type: Tuple[str, List[bytes]]
    for line in lf_lines(stream):
        if line.startswith(b"## "):
            if chunk:
                pieces.append((key, b"".join(chunk)))
            key = line.rstrip(b"\n").strip(_BLANK).decode("utf-8", "surrogateescape")
            chunk = []
        chunk.append(line)
    if chunk:
        pieces.append((key, b"".join(chunk)))
    return [(k, v) for k, v in pieces if v]


def _split_roots(text: bytes, roots: Set[str]) -> List[Tuple[Optional[bytes], bytes]]:
    """(root line or None, the text after it until the next root line), in order; root lines go into `roots`."""
    runs = []  # type: List[Tuple[Optional[bytes], List[bytes]]]
    current = None  # type: Optional[bytes]
    chunk = []  # type: List[bytes]
    for line in lf_lines(text):
        if line.startswith(ROOT_PREFIX):
            runs.append((current, chunk))
            current = line.rstrip(b"\n")
            chunk = []
            continue
        chunk.append(line)
    runs.append((current, chunk))
    return [(root, b"".join(lines)) for root, lines in runs]


def normalize(outputs: Dict[str, List[bytes]], subagent: bool) -> Tuple[Set[str], Dict[str, List[Tuple[str, bytes]]]]:
    """Handler outputs per plugin to (root-line set, per-module pieces), per D1."""
    roots = set()  # type: Set[str]
    streams = {}  # type: Dict[str, bytes]
    for module, outs in outputs.items():
        text = b"".join(decode_subagent(out) if subagent else out for out in outs)
        joined = b""
        for root, chunk in _split_roots(text, roots):
            if root is not None:
                roots.add(root.decode("utf-8", "surrogateescape"))
            joined += chunk
        streams[module] = joined
    result = {m: _pieces(s) for m, s in streams.items()}
    return roots, {m: p for m, p in result.items() if p}


def normalize_render(text: bytes, root_names: Dict[str, str]) -> Tuple[Set[str], Dict[str, List[Tuple[str, bytes]]]]:
    """`assemble.py --render --mark-personal` output to the same form; `root_names` maps a root line to its module.

    Text after `Plugin root: personal` is filed under `personal`; that line stays out of the root set.
    Text before the first root line is filed under `_head`. A root line no module owns raises.
    """
    roots = set()  # type: Set[str]
    streams = {}  # type: Dict[str, bytes]
    for root, chunk in _split_roots(text, roots):
        if root is None:
            module = HEAD
        elif root == PERSONAL_ROOT:
            module = PERSONAL
        else:
            line = root.decode("utf-8", "surrogateescape")
            roots.add(line)
            if line not in root_names:
                raise ValueError("root line of no layout module: %r" % line)
            module = root_names[line]
        streams[module] = streams.get(module, b"") + chunk
    result = {m: _pieces(s) for m, s in streams.items()}
    return roots, {m: p for m, p in result.items() if p}
