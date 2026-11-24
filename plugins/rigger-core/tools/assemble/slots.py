"""Slots: resolution over the config levels, entry validation, the trust filter and the Slots table.

A project file (level 4) arrives with a clone, so it is read as hostile input: it may not set the
`kind` or `target` of a slot whose merged entry is a command unless the person's level-3 file
trusts that slot path (D33), and every fragment of it that reaches a notice or a table cell is
reduced to one safe line first.
"""

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

import config
import manifest
from manifest import Module, SLOT_PATHS, UNSAFE

KINDS = {
    "reviewer": ("agent", "command"),
    "worker": ("agent",),
    "implementer": ("agent",),
    "mapper": ("agent",),
    "testing": ("skill",),
    "shell-guard": ("hook",),
    "lessons": ("hook",),
    "process.brainstorm": ("skill",),
    "process.plans": ("skill",),
    "process.tdd": ("skill",),
    "process.debug": ("skill",),
}  # type: Dict[str, Tuple[str, ...]]
PLACEHOLDERS = ("base", "head", "out", "scope")
ENTRY_KEYS = ("kind", "target", "model", "requires")
ARGV_SHOWN = 200
TARGET_MAX = 200
ARGV_MAX = 32
ARG_MAX = 500
MODEL_MAX = 60
SOURCES = ("default", "personal", "project")
EMPTY = "—"

_NAMED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*")
_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_LEVELS = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}
_NAMES = {1: "default", 3: "personal", 4: "project"}
_shown = config._shown


@dataclass
class Row:
    path: str
    kind: Optional[str]
    target: object
    model: Optional[str]
    source: str
    fillers: Tuple[str, ...]
    failure_mode: Optional[str]
    filled: bool
    modes_differ: bool = False  # fillers declared both open and closed; failure_mode is then closed


def _unsafe(text: str) -> bool:
    return any(unicodedata.category(ch) in UNSAFE for ch in text)


def _json_shown(value) -> str:
    return _shown(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def _first_and_more(items: List[str], one: str, many: str) -> str:
    """One notice for a list of refused items: the first, shown, and how many more (the list is unbounded)."""
    return one % items[0] if len(items) == 1 else many % (items[0], len(items) - 1)


def flatten(slots: dict) -> Dict[str, dict]:
    """Slot path -> entry. Unknown paths, a non-object `process` and a dotted top-level key
    (written in JSON quotes, so it never aliases a real path) come back under their own path."""
    flat = {}
    for key, value in slots.items():
        if key == "process" and isinstance(value, dict):
            for role, entry in value.items():
                flat["process." + role] = entry
        elif "." not in key:
            flat[key] = value
        else:
            flat[json.dumps(key, ensure_ascii=False)] = value
    return flat


def _argv_problem(target) -> Optional[str]:
    if not isinstance(target, list) or not 1 <= len(target) <= ARGV_MAX \
            or not all(isinstance(arg, str) for arg in target):
        return "target must be a list of 1 to %d strings" % ARGV_MAX
    for index, arg in enumerate(target):
        if not 1 <= len(arg) <= ARG_MAX or _unsafe(arg):
            return "argument %d is empty, too long or holds a control character" % index
        for word in _PLACEHOLDER.findall(arg):
            if word not in PLACEHOLDERS:
                return "unknown placeholder {%s}" % _shown(word)
    if not any("{out}" in arg for arg in target):
        return "no {out} placeholder"
    if _PLACEHOLDER.search(target[0]):
        return "the program name holds a placeholder"
    return None


def _named_problem(kind: str, target, modules: List[Module], enabled: FrozenSet[str]) -> Optional[str]:
    if not isinstance(target, str) or len(target) > TARGET_MAX or not _NAMED.fullmatch(target):
        return "target must be <plugin>:<name>"
    plugin, name = target.split(":")
    if plugin not in enabled:
        return "plugin %s is not enabled" % plugin
    module = next((item for item in modules if item.name == plugin), None)
    if module is not None:
        if kind == "agent" and not (module.root / "agents" / (name + ".md")).is_file():
            return "%s has no agent %s" % (plugin, name)
        if kind == "skill" and not (module.root / "skills" / name / "SKILL.md").is_file():
            return "%s has no skill %s" % (plugin, name)
    return None


def entry_problem(path: str, entry: dict, modules: List[Module], enabled: FrozenSet[str]) -> Optional[str]:
    """Why a merged slot entry cannot fill its slot, or None."""
    if not isinstance(entry, dict):
        return "must be an object or null"
    for key in entry:
        if key not in ENTRY_KEYS:
            return "unknown key %s" % _shown(key)
    kind = entry.get("kind")
    if kind not in KINDS.get(path, ()):
        return "kind %s not allowed for %s" % (_json_shown(kind), _shown(path))
    target = entry.get("target")
    if kind == "command":
        problem = _argv_problem(target)
    elif kind == "hook":
        problem = None if isinstance(target, str) and _PLUGIN.fullmatch(target) else "target must be a plugin name"
    else:
        problem = _named_problem(kind, target, modules, enabled)
    if problem:
        return problem
    if "model" in entry:
        model = entry["model"]
        if not isinstance(model, str) or not 1 <= len(model) <= MODEL_MAX or "|" in model or "`" in model \
                or _unsafe(model):
            return "model must be a short one-line string"
    try:
        manifest.check_requires(entry.get("requires"), "slot " + _shown(path))
    except manifest.ManifestError as error:
        return _shown(error, 300)
    return None


def _merge(base, value):
    """One slot entry merged as config.merge merges a key: null removes, objects merge, else replace."""
    return config.merge({"e": base} if base is not None else {}, {"e": value}).get("e")


def _command_notice(path: str, config_file: str) -> str:
    return ("rigger: .claude/rigger.json may not set a command for slot %s; add it to trust.project_commands "
            "in %s to allow it" % (_shown(path), _shown(config_file, 300)))


def trust_filter(lower: Dict[str, dict], project: Dict[str, object], trusted: FrozenSet[str],
                 config_file: str) -> Tuple[Dict[str, object], List[str]]:
    """Drop a level-4 value that sets `kind` or `target` of a path whose merged entry is a command (D33)."""
    kept = {}
    notices = []
    for path, value in project.items():
        if isinstance(value, dict) and ("kind" in value or "target" in value) and path not in trusted:
            merged = _merge(lower.get(path), value)
            if merged.get("kind") == "command":
                notices.append(_command_notice(path, config_file))
                continue
        kept[path] = value
    return kept, notices


def _level(source: str) -> Tuple[int, str, str]:
    """(group, source name, where for notices) of a `L<n>:<file or module>` label."""
    tag, sep, rest = source.partition(":")
    if not sep or tag not in _LEVELS:
        raise ValueError("unknown level label %r" % source)
    group = _LEVELS[tag]
    if group == 2:
        return group, "company:" + rest, "module " + _shown(rest)
    return group, _NAMES[group], _shown(rest, 300)


def _trusted(data: dict, where: str, notices: List[str]) -> FrozenSet[str]:
    trust = data.get("trust")
    if not isinstance(trust, dict) or "project_commands" not in trust:
        return frozenset()
    items = trust["project_commands"]
    head = "rigger: config %s trust.project_commands " % where
    if not isinstance(items, list):
        notices.append(head + "must be a list of slot names; ignored")
        return frozenset()
    good = [item for item in items if isinstance(item, str) and item in SLOT_PATHS]
    bad = [item for item in items if not (isinstance(item, str) and item in SLOT_PATHS)]
    if bad:
        notices.append(head + _first_and_more([_shown(json.dumps(bad[0], ensure_ascii=False))] + bad[1:],
                                              "entry %s is not a slot; ignored",
                                              "entry %s and %d more are not slots; ignored"))
    return frozenset(good)


def _level_slots(group: int, data: dict, where: str, notices: List[str]) -> Dict[str, object]:
    slots = data.get("slots")
    if group > 1 and "slots" in data and slots is None:
        notices.append("rigger: config %s may not set slots to null; set a single slot to null instead" % where)
    if not isinstance(slots, dict):
        return {}
    slots = dict(slots)
    if "process" in slots and slots["process"] is None:
        if group > 1:
            notices.append("rigger: config %s may not set slots.process to null; set a single slot to null "
                           "instead" % where)
        del slots["process"]
    flat = flatten(slots)
    unknown = [path for path in flat if path not in SLOT_PATHS]
    if unknown:
        notices.append("rigger: config %s %s" % (where, _first_and_more(
            [_shown(unknown[0])] + unknown[1:], "slot %s is not a slot; ignored",
            "slot %s and %d more are not slots; ignored")))
    return {path: value for path, value in flat.items() if path in SLOT_PATHS}


def _source(touches: list) -> str:
    """The name of the highest level that set `kind` or `target` (D53); a removal or a whole value counts."""
    source = "default"
    for _, name, _, value in touches:
        if not isinstance(value, dict) or "kind" in value or "target" in value:
            source = name
    return source


def _shown_argv(argv: list) -> str:
    line = json.dumps(argv, ensure_ascii=True)
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()[:12]
    return "%s … (%d bytes, sha256:%s)" % (line[:ARGV_SHOWN], len(line.encode("ascii")), digest)


def _not_enabled(entry: object, problem: str) -> bool:
    target = entry.get("target") if isinstance(entry, dict) else None
    plugin = target.split(":")[0] if isinstance(target, str) else ""
    return problem == "plugin %s is not enabled" % plugin


def _walk(path: str, touches: list, modules: List[Module], enabled: FrozenSet[str],
          notices: List[str]) -> Tuple[Optional[dict], str]:
    """Validate the merge of levels 1-4, then 1-3, 1-2, 1 (D34); return (entry or None, source)."""
    groups = sorted({group for group, _, _, _ in touches}, reverse=True)
    failed = None  # (where, reason, group) of the highest entry that did not validate
    chosen = None  # (entry, source, landing name)
    for group in groups:
        below = [touch for touch in touches if touch[0] <= group]
        entry = None
        for _, _, _, value in below:
            entry = _merge(entry, value)
        landing = below[-1][1] if below else "default"
        problem = None if entry is None else entry_problem(path, entry, modules, enabled)
        if problem is None:
            chosen = (entry, _source(below), landing)
            break
        if failed is None:
            failed = (below[-1][2], problem, group, entry)
    if failed is not None:
        where, problem, group, entry = failed
        if not (group == 1 and _not_enabled(entry, problem)):
            tail = "using %s" % _shown(chosen[2]) if chosen and chosen[0] is not None else "the slot is empty"
            notices.append("rigger: slot %s from %s ignored: %s; %s" % (path, where, problem, tail))
    if chosen is None:
        return None, _source(touches)
    return chosen[0], chosen[1]


def _requires_met(requires, filled: Dict[str, bool], os_name: str, enabled: FrozenSet[str]) -> bool:
    for item in requires or ():
        kind, _, rest = item.partition(":")
        if kind == "os" and rest != os_name:
            return False
        if kind == "plugin" and rest not in enabled:
            return False
        if kind == "slot" and not filled.get(rest, False):
            return False
    return True


def resolve_slots(levels: List[Tuple[str, dict]], modules: List[Module], enabled: FrozenSet[str], os_name: str,
                  config_file: str) -> Tuple[List[Row], FrozenSet[str], List[str]]:
    """Rows in SLOT_PATHS order (paths whose `requires` is unmet left out), the trusted list, the notices."""
    notices = []  # type: List[str]
    parsed = []  # (group, name, where, flat slots)
    trusted = frozenset()  # type: FrozenSet[str]
    for source, data in levels:
        group, name, where = _level(source)
        data = data if isinstance(data, dict) else {}
        if group == 3:
            trusted = trusted | _trusted(data, where, notices)
        parsed.append([group, name, where, _level_slots(group, data, where, notices)])

    merged = {}  # type: Dict[str, object]
    for item in parsed:
        group, _, _, flat = item
        if group == 4:
            item[3], found = trust_filter(merged, flat, trusted, config_file)
            notices.extend(found)
        for path, value in item[3].items():
            merged[path] = _merge(merged.get(path), value)

    rows = []
    entries = {}
    walk_notices = {}
    for path in SLOT_PATHS:
        touches = [(group, name, where, flat[path]) for group, name, where, flat in parsed if path in flat]
        found = []  # type: List[str]
        entry, source = _walk(path, touches, modules, enabled, found)
        walk_notices[path] = found
        entries[path] = entry
        if KINDS[path] == ("hook",):
            fillers = tuple(module.name for module in modules if module.fills == path and module.name in enabled)
            modes = {module.failure_mode for module in modules if module.name in fillers}
            differ = {"open", "closed"} <= modes  # the stricter mode wins: fail closed
            mode = "closed" if differ else (modes.pop() if len(modes) == 1 else None)
            rows.append(Row(path, "hook", entry.get("target") if entry else None, None, source, fillers,
                            mode, bool(fillers), differ))
        elif entry is None:
            rows.append(Row(path, None, None, None, source, (), None, False))
        else:
            model = entry.get("model") if entry["kind"] in ("agent", "command") else None
            rows.append(Row(path, entry["kind"], copy.deepcopy(entry["target"]), model, source, (), None, True))

    filled = {row.path: row.filled for row in rows}
    kept = [row for row in rows
            if _requires_met((entries[row.path] or {}).get("requires"), filled, os_name, enabled)]
    for row in kept:
        notices.extend(walk_notices[row.path])
        if row.kind == "hook":
            if len(row.fillers) > 1:
                names = ", ".join(_shown(name) for name in row.fillers[:-1]) + " and " + _shown(row.fillers[-1])
                notices.append("rigger: slot %s is filled by %s; disable one with claude plugin disable <name>"
                               % (row.path, names))
            if row.source != "default" and isinstance(row.target, str) and row.target not in enabled:
                notices.append("rigger: slot %s: expected %s is not enabled" % (row.path, _shown(row.target)))
        elif row.kind == "command" and row.source == "project":
            notices.append("rigger: slot %s runs the project's command %s (trusted in %s)"
                           % (row.path, _shown_argv(row.target), _shown(config_file, 300)))
    return kept, trusted, notices


def slot_targets(rows: List[Row]) -> dict:
    """Filled slots shaped like level-1 `slots`: agent and skill with their target, hook and command by kind."""
    result = {}  # type: dict
    for row in rows:
        if not row.filled:
            continue
        if row.kind in ("agent", "skill"):
            entry = {"kind": row.kind, "target": row.target}
        else:
            entry = {"kind": row.kind}
        if "." in row.path:
            group, role = row.path.split(".", 1)
            result.setdefault(group, {})[role] = entry
        else:
            result[row.path] = entry
    return result


def _cell(text) -> str:
    return "".join("?" if ch in "|`" or unicodedata.category(ch) in UNSAFE else ch for ch in str(text))


def _target_cell(row: Row) -> str:
    if not row.filled:
        return EMPTY
    if row.kind == "hook":
        mode = {"open": "fails open", "closed": "fails closed"}.get(row.failure_mode, "failure mode: undeclared")
        if row.modes_differ:
            mode = "failure mode: closed (fillers disagree)"
        return "%s (%s)" % (" + ".join(_cell(name) for name in row.fillers), mode)
    if row.kind == "command":
        return _cell(row.target[0]) + " …"
    return _cell(row.target)


def table(rows: List[Row], command_line: Optional[str]) -> bytes:
    """The `## Slots` section (D36), heading included."""
    lines = ["## Slots", "", "| Slot | Kind | Target | Model | From |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (
            _cell(row.path), _cell(row.kind or EMPTY), _target_cell(row),
            _cell(row.model) if row.model else EMPTY, _cell(row.source)))
    if command_line and any(row.filled and row.kind == "command" for row in rows):
        lines += ["", "".join("?" if unicodedata.category(ch) in UNSAFE else ch for ch in command_line)]
    return ("\n".join(lines) + "\n").encode("utf-8")


_ROW_KEYS = ("path", "kind", "target", "model", "source", "fillers", "failure_mode", "filled", "modes_differ")


def as_snapshot(rows: List[Row], trusted: FrozenSet[str]) -> dict:
    return {
        "rows": [{"path": row.path, "kind": row.kind, "target": copy.deepcopy(row.target), "model": row.model,
                  "source": row.source, "fillers": list(row.fillers), "failure_mode": row.failure_mode,
                  "filled": row.filled, "modes_differ": row.modes_differ} for row in rows],
        "trusted": sorted(trusted),
    }


def _source_ok(source) -> bool:
    if not isinstance(source, str):
        return False
    return source in SOURCES or (source.startswith("company:") and len(source) > len("company:"))


def _row_from(item) -> Optional[Row]:
    if not isinstance(item, dict) or set(item) != set(_ROW_KEYS):
        return None
    path, kind, target, model, source, fillers, mode, filled, differ = (item[key] for key in _ROW_KEYS)
    if path not in SLOT_PATHS:
        return None
    if kind is not None and kind not in KINDS[path]:
        return None
    if not isinstance(filled, bool) or (filled and kind is None):
        return None
    if target is not None and not isinstance(target, (str, list)):
        return None
    if filled and kind == "command" and _argv_problem(target):
        return None
    if filled and kind in ("agent", "skill", "hook") and not isinstance(target, str):
        return None
    if model is not None and not isinstance(model, str):
        return None
    if not _source_ok(source):
        return None
    if not isinstance(fillers, list) or not all(isinstance(name, str) for name in fillers):
        return None
    if mode is not None and mode not in manifest.FAILURE_MODES:
        return None
    if not isinstance(differ, bool) or (differ and mode != "closed"):
        return None
    return Row(path, kind, copy.deepcopy(target), model, source, tuple(fillers), mode, filled, differ)


def from_snapshot(data: object) -> Optional[Tuple[List[Row], FrozenSet[str]]]:
    """The rows and trusted list `as_snapshot` wrote, or None on any wrong shape."""
    if not isinstance(data, dict) or set(data) != {"rows", "trusted"}:
        return None
    items, trusted = data["rows"], data["trusted"]
    if not isinstance(items, list) or not isinstance(trusted, list):
        return None
    if not all(path in SLOT_PATHS for path in trusted):
        return None
    rows = []
    for item in items:
        row = _row_from(item)
        if row is None or any(row.path == other.path for other in rows):
            return None
        rows.append(row)
    return rows, frozenset(trusted)
