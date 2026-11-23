"""Resolve the four config levels: Rigger defaults, company modules, personal, project (spec section 7).

Phase 1 applies `triggers` (levels 1-4) and `expects` (level 4). Every other key set above level 1
is checked for its type and level, then recorded in `Resolved.inactive` without effect (D11), so
`effective["slots"]` is always level 1's. A trigger row comes from files a cloned repository can
ship, so its text is held to one line without `|` or control characters before it can reach a
table, and every notice is printed with such characters escaped.
"""

import copy
import json
import unicodedata

import manifest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

ALL_LEVELS = frozenset((1, 2, 3, 4))
KEYS = {
    "schema": (int, ALL_LEVELS),
    "slots": (dict, ALL_LEVELS),
    "triggers": (dict, ALL_LEVELS),
    "expects": (list, frozenset((4,))),
    "company": (str, frozenset((4,))),
    "leak_guard": (dict, frozenset((3,))),
    "trust": (dict, frozenset((3,))),
    "review": (dict, ALL_LEVELS),
    "shell_guard": (dict, frozenset((3,))),
}  # type: Dict[str, Tuple[type, FrozenSet[int]]]

# Keys with an effect in phase 1 when set above level 1; the others are recorded only (D11).
ACTIVE_ABOVE_L1 = frozenset(("schema", "triggers", "expects"))
DEFAULTS_FILE = "rigger-defaults.json"
BEFORE_MAX = 300
LOAD_MAX = 4
_UNSAFE = ("Cc", "Cf", "Zl", "Zp")

Path_ = Tuple[str, ...]


@dataclass
class Resolved:
    effective: dict
    sources: Dict[Path_, str]
    inactive: Dict[Path_, str]
    notices: List[str]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shown(text: str, limit: int = 80) -> str:
    """Text from a config file as it may appear in a notice: one line, no control characters."""
    text = "".join("\\u%04x" % ord(ch) if unicodedata.category(ch) in _UNSAFE else ch for ch in str(text))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def defaults_path(core_root: Path) -> Path:
    return core_root / DEFAULTS_FILE


def load_level(path: Path, level: int) -> Tuple[dict, List[str]]:
    """Read one config file; a missing file is {} with no notice, any other failure one notice."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, []
    except OSError as error:
        return {}, ["rigger: config %s ignored: %s" % (_shown(path, 300), _shown(error.strerror or error))]
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except RecursionError:
        data = manifest.TOO_DEEP
    except (UnicodeDecodeError, ValueError) as error:
        return {}, ["rigger: config %s ignored: not valid JSON (%s)" % (_shown(path, 300), _shown(error))]
    problem = manifest.parsed_problem(data)
    if problem:
        return {}, ["rigger: config %s ignored: %s" % (_shown(path, 300), problem)]
    if not isinstance(data, dict):
        return {}, ["rigger: config %s ignored: not a JSON object" % _shown(path, 300)]
    schema = data.get("schema")
    if not _is_int(schema) or schema != 1:
        reason = "no schema" if "schema" not in data else "schema %s" % _shown(json.dumps(schema))
        return {}, ["rigger: config %s ignored: %s (expected 1)" % (_shown(path, 300), reason)]
    return data, []


def merge(base: dict, over: dict, _path: Path_ = ()) -> dict:
    """Spec section 7.3: null removes, objects merge, anything else replaces; a trigger row is a leaf."""
    result = copy.deepcopy(base)
    for key, value in over.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict) and _path != ("triggers",):
            result[key] = merge(result[key], value, _path + (key,))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _leaves(value, path: Path_ = (), rows: bool = True) -> List[Tuple[Path_, object]]:
    """Leaves as merge sees them (a trigger row whole when rows is True), or every field."""
    if isinstance(value, dict) and not value:
        return []  # an empty object changes nothing in a merge
    if not isinstance(value, dict) or (rows and len(path) == 2 and path[0] == "triggers"):
        return [(path, value)]
    found = []
    for key, item in value.items():
        found.extend(_leaves(item, path + (key,), rows))
    return found


def _line_problem(text: str) -> Optional[str]:
    if "|" in text:
        return "holds |"
    if any(unicodedata.category(ch) in _UNSAFE for ch in text):
        return "holds a line break or control character"
    return None


def _row_problem(row) -> Optional[str]:
    if row is None:
        return None
    if not isinstance(row, dict):
        return "must be an object or null"
    before, load = row.get("before"), row.get("load")
    if not isinstance(before, str):
        return "before must be a string"
    problem = _line_problem(before)
    if problem:
        return "before " + problem
    if len(before) > BEFORE_MAX:
        return "before over %d characters" % BEFORE_MAX
    if not isinstance(load, list):
        return "load must be a list"
    if not 1 <= len(load) <= LOAD_MAX:
        return "load must hold 1 to %d entries" % LOAD_MAX
    for entry in load:
        if not isinstance(entry, str):
            return "load entries must be strings"
        problem = _line_problem(entry)
        if problem:
            return "load entry " + problem
    try:
        manifest.check_requires(row.get("requires"), "requires")
    except manifest.ManifestError as error:
        return str(error)
    if any(_line_problem(item) for item in row.get("requires") or []):
        return "requires entry holds a line break, control character or |"
    section = row.get("section")
    if section is not None and (not isinstance(section, str) or _line_problem(section)):
        return "section must be a one-line string"
    return None


def _validate(data: dict, level: int, where: str) -> Tuple[dict, List[str]]:
    clean = {}
    notices = []
    for key, value in data.items():
        if key not in KEYS:
            notices.append("rigger: config %s key %s unknown; ignored" % (where, _shown(key)))
            continue
        kind, levels = KEYS[key]
        if level not in levels:
            notices.append("rigger: config %s key %s not allowed at level %d; ignored" % (where, key, level))
            continue
        if value is None and key != "schema":
            clean[key] = None
            continue
        good = _is_int(value) if kind is int else isinstance(value, kind)
        if key == "expects" and good:
            good = all(isinstance(item, str) for item in value)
        if not good:
            what = {int: "a number", dict: "an object", list: "a list of names", str: "a string"}[kind]
            notices.append("rigger: config %s key %s must be %s; ignored" % (where, key, what))
            continue
        if key == "triggers":
            rows = {}
            for row_id, row in value.items():
                problem = _line_problem(row_id)
                problem = "id " + problem if problem else _row_problem(row)
                if problem:
                    notices.append("rigger: config %s trigger %s ignored: %s" % (where, _shown(row_id), problem))
                else:
                    rows[row_id] = row
            value = rows
        clean[key] = value
    return clean, notices


def resolve(l1: dict, l2: List[Tuple[str, dict]], l3: dict, l4: dict, paths: Dict[int, str]) -> Resolved:
    notices = []  # type: List[str]
    inactive = {}  # type: Dict[Path_, str]
    applied = []  # type: List[Tuple[str, dict]]  (source, what this level changes), lowest first

    def take(level: int, data: dict, where: str, source: str) -> dict:
        problem = manifest.parsed_problem(data)
        if problem:
            notices.append("rigger: config %s ignored: %s" % (where, problem))
            return {}
        clean, found = _validate(data, level, where)
        notices.extend(found)
        if level == 1:
            return clean
        for key in [key for key in clean if key not in ACTIVE_ABOVE_L1]:
            for path, _ in _leaves(clean.pop(key), (key,), rows=False):
                inactive[path] = source
        return clean

    applied.append(("L1:" + paths.get(1, ""), take(1, l1, _shown(paths.get(1, ""), 300), "L1:" + paths.get(1, ""))))

    modules = []
    for name, block in l2:
        source = "L2:" + name
        modules.append((name, source, take(2, block, "module " + _shown(name), source)))
    setters = {}  # type: Dict[Path_, List[str]]
    for name, _, clean in modules:
        for path, _ in _leaves(clean):
            setters.setdefault(path, []).append(name)
    conflicts = {path: names for path, names in setters.items() if len(names) > 1}
    for path, names in conflicts.items():
        notices.append(
            "rigger: config key %s set by company modules %s; neither applies"
            % (_shown(".".join(path)), " and ".join(_shown(name) for name in names))
        )
    for name, source, clean in modules:
        for path in conflicts:
            holder = clean
            for key in path[:-1]:
                holder = holder.get(key, {})
            if isinstance(holder, dict):
                holder.pop(path[-1], None)
        applied.append((source, clean))

    for level, data in ((3, l3), (4, l4)):
        source = "L%d:%s" % (level, paths.get(level, ""))
        applied.append((source, take(level, data, _shown(paths.get(level, ""), 300), source)))

    effective = {}  # type: dict
    for _, clean in applied:
        effective = merge(effective, clean)

    covered = [(source, [path for path, _ in _leaves(clean)]) for source, clean in applied]
    sources = {}
    for path, _ in _leaves(effective, rows=False):
        for source, leaves in reversed(covered):
            if any(path[: len(leaf)] == leaf for leaf in leaves):
                sources[path] = source
                break
    return Resolved(effective=effective, sources=sources, inactive=inactive, notices=notices)
