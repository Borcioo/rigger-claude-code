"""Resolve the four config levels: Rigger defaults, company modules, personal, project (spec section 7).

`triggers` (levels 1-4), `expects` (level 4), `slots` (levels 1-4), `trust` (level 3) and `review`
(levels 1-4; review_scope reads level 4 itself, at the reviewed diff's base), `lessons` (levels 2-3, read
by rigger-lessons' lint), `turn_guard` (levels 2-4, read by the Stop hook's turn_guard.py), `load` (levels
1-4, read by mem_check.py), `shell_guard` (level 3, read by rigger-shellwise's installer) and `test_evidence`
(levels 2-3, read by rigger-testing's Stop shadow) apply.
Every other key set above level 1 is checked for its type and level, then recorded in
`Resolved.inactive` without effect (D11, D32). `effective["slots"]` is the plain merge, before the
trust filter: only `slots.resolve_slots`, which reads `Resolved.levels`, may decide what fills a
slot. A trigger row comes from files a cloned repository can ship, so its text is held to one line
without `|` or control characters before it can reach a table, and every notice is printed with
such characters escaped.
"""

import copy
import json
import os
import unicodedata

import manifest
from dataclasses import dataclass, field
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
    "lessons": (dict, frozenset((2, 3))),
    "turn_guard": (dict, frozenset((2, 3, 4))),
    "load": (dict, ALL_LEVELS),
    "test_evidence": (dict, frozenset((2, 3))),
}  # type: Dict[str, Tuple[type, FrozenSet[int]]]

# Keys with an effect when set above level 1; the others are recorded only (D11, D32).
ACTIVE_ABOVE_L1 = frozenset(("schema", "triggers", "expects", "slots", "trust", "review", "lessons", "turn_guard",
                             "load", "shell_guard", "test_evidence"))
LOAD_MIN_FREE_GB = (0, 1024)  # load.min_free_gb, a number (D80)
LOAD_HEAVY_AGENTS = (1, 8)  # load.max_heavy_agents, a whole number (D80)
REVIEW_PATHS_MAX = 50  # review.paths entries per config level (D61)
REVIEW_TEXT_MAX = 200  # characters of a review.paths glob or skip reason
LESSONS_PATHS_MAX = 50  # lessons.allowed_paths globs per config level (D97), each at most REVIEW_TEXT_MAX
PHRASE_CATEGORIES = ("claim", "negation", "disclosure", "status")  # test_evidence.phrases (D95)
PHRASES_MAX = 100  # entries per category per config level, each at most PHRASE_TEXT_MAX characters
PHRASE_TEXT_MAX = 60
DEFAULTS_FILE = "rigger-defaults.json"
BEFORE_MAX = manifest.BEFORE_MAX
LOAD_MAX = manifest.LOAD_MAX
ROWS_MAX = 50  # trigger rows per config level 2-4, after the shape check; level 1 is exempt
_UNSAFE = manifest.UNSAFE

Path_ = Tuple[str, ...]


@dataclass
class Resolved:
    effective: dict
    sources: Dict[Path_, str]
    inactive: Dict[Path_, str]
    notices: List[str]
    # (source, cleaned data) per level, lowest first, level-2 conflicts removed: what slots.resolve_slots reads.
    levels: List[Tuple[str, dict]] = field(default_factory=list)


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
    return parse_level(raw, path)


def parse_level(raw: bytes, path) -> Tuple[dict, List[str]]:
    """The bytes of one config file named `path` as load_level reads them: {} and one notice when refused."""
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


_line_problem = manifest.line_problem
_row_problem = manifest.row_problem


def _text_problem(name: str, text: str, limit: Optional[int] = None) -> Optional[str]:
    """Why config text printed into a review_scope line is refused: a tab or a line break would forge a row."""
    if any(unicodedata.category(ch) in _UNSAFE for ch in text):
        return "%s holds a line break or control character" % name
    if limit is not None and len(text) > limit:
        return "%s over %d characters" % (name, limit)
    return None


def _path_entry_problem(glob: str, entry) -> Optional[str]:
    """Why one review.paths entry is refused (D61), or None; null is the merge's removal."""
    if not glob:
        return "glob is empty"
    problem = _text_problem("glob", glob, REVIEW_TEXT_MAX)
    if problem:
        return problem
    if entry is None:
        return None
    if isinstance(entry, str):
        entry = {"rules": entry}
    elif not isinstance(entry, dict):
        return "must be a rules file or an object"
    elif not entry:
        return "needs rules or skip"
    unknown = sorted(key for key in entry if key not in ("rules", "skip"))
    if unknown:
        return "unknown key %s" % _shown(unknown[0])
    for key, limit in (("rules", None), ("skip", REVIEW_TEXT_MAX)):
        if key not in entry:
            continue
        value = entry[key]
        if not isinstance(value, str) or not value:
            return "%s must be a non-empty string" % key
        problem = _text_problem(key, value, limit)
        if problem:
            return problem
    return None


def _review(value: dict, where: str) -> Tuple[dict, List[str]]:
    clean = {}
    notices = []
    for key, item in value.items():
        if key != "paths":
            notices.append("rigger: config %s review key %s unknown; ignored" % (where, _shown(key)))
            continue
        if item is None:
            clean[key] = None
            continue
        if not isinstance(item, dict):
            notices.append("rigger: config %s review.paths must be an object; ignored" % where)
            continue
        kept = {}
        for glob, entry in item.items():
            problem = _path_entry_problem(glob, entry)
            if problem:
                notices.append("rigger: config %s review.paths %s ignored: %s" % (where, _shown(glob), problem))
            else:
                kept[glob] = entry
        if len(kept) > REVIEW_PATHS_MAX:
            notices.append("rigger: config %s holds %d review.paths entries; the last %d ignored (at most %d per file)"
                           % (where, len(kept), len(kept) - REVIEW_PATHS_MAX, REVIEW_PATHS_MAX))
            kept = dict(list(kept.items())[:REVIEW_PATHS_MAX])
        clean[key] = kept
    return clean, notices


def _lessons(value: dict, where: str) -> Tuple[dict, List[str]]:
    """lessons.allowed_paths (D97): a list of non-empty globs, each one line of at most REVIEW_TEXT_MAX characters."""
    clean = {}
    notices = []
    for key, item in value.items():
        if key != "allowed_paths":
            notices.append("rigger: config %s lessons key %s unknown; ignored" % (where, _shown(key)))
            continue
        if item is None:
            clean[key] = None
            continue
        if not isinstance(item, list):
            notices.append("rigger: config %s lessons.allowed_paths must be a list of globs; ignored" % where)
            continue
        kept = []
        for glob in item:
            problem = "must be a non-empty string" if not isinstance(glob, str) or not glob else \
                _text_problem("glob", glob, REVIEW_TEXT_MAX)
            if problem:
                problem = problem[len("glob "):] if problem.startswith("glob ") else problem
                notices.append("rigger: config %s lessons.allowed_paths entry %s ignored: %s"
                               % (where, _shown(repr(glob)), problem))
            else:
                kept.append(glob)
        if len(kept) > LESSONS_PATHS_MAX:
            notices.append("rigger: config %s holds %d lessons.allowed_paths entries; the last %d ignored "
                           "(at most %d per file)" % (where, len(kept), len(kept) - LESSONS_PATHS_MAX, LESSONS_PATHS_MAX))
            kept = kept[:LESSONS_PATHS_MAX]
        clean[key] = kept
    return clean, notices


def _turn_guard(value: dict, where: str) -> Tuple[dict, List[str]]:
    """turn_guard.enabled (D89): true or false; a bad value is dropped, so the default (true) stays."""
    clean = {}
    notices = []
    for key, item in value.items():
        if key != "enabled":
            notices.append("rigger: config %s turn_guard key %s unknown; ignored" % (where, _shown(key)))
        elif item is None or isinstance(item, bool):
            clean[key] = item
        else:
            notices.append("rigger: config %s turn_guard.enabled must be true or false; ignored" % where)
    return clean, notices


def _shell_guard(value: dict, where: str) -> Tuple[dict, List[str]]:
    """shell_guard.shellwise_path (D76): an absolute path, or null; a bad value is dropped, so the download stays."""
    clean = {}
    notices = []
    for key, item in value.items():
        if key != "shellwise_path":
            notices.append("rigger: config %s shell_guard key %s unknown; ignored" % (where, _shown(key)))
        elif item is None or (isinstance(item, str) and os.path.isabs(item)):
            clean[key] = item
        else:
            notices.append("rigger: config %s shell_guard.shellwise_path must be an absolute path; ignored" % where)
    return clean, notices


def _load(value: dict, where: str) -> Tuple[dict, List[str]]:
    """load.min_free_gb and load.max_heavy_agents (D80), read by mem_check.py; a bad value is dropped, so its
    per-system default stays. Which level may loosen a limit is mem_check's rule, not this shape check."""
    clean = {}
    notices = []
    for key, item in value.items():
        if key == "min_free_gb":
            low, high = LOAD_MIN_FREE_GB
            good = item is None or (isinstance(item, (int, float)) and not isinstance(item, bool)
                                    and low <= item <= high)
            what = "a number from %d to %d" % LOAD_MIN_FREE_GB
        elif key == "max_heavy_agents":
            low, high = LOAD_HEAVY_AGENTS
            good = item is None or (_is_int(item) and low <= item <= high)
            what = "a whole number from %d to %d" % LOAD_HEAVY_AGENTS
        else:
            notices.append("rigger: config %s load key %s unknown; ignored" % (where, _shown(key)))
            continue
        if good:
            clean[key] = item
        else:
            notices.append("rigger: config %s load.%s must be %s; ignored" % (where, key, what))
    return clean, notices


def _test_evidence(value: dict, where: str) -> Tuple[dict, List[str]]:
    """test_evidence.phrases (D95): words per category that the Stop shadow adds to its shipped lists; a bad
    category or entry is dropped, so the shipped lists stay."""
    clean = {}
    notices = []
    for key, item in value.items():
        if key != "phrases":
            notices.append("rigger: config %s test_evidence key %s unknown; ignored" % (where, _shown(key)))
            continue
        if item is None:
            clean[key] = None
            continue
        if not isinstance(item, dict):
            notices.append("rigger: config %s test_evidence.phrases must be an object; ignored" % where)
            continue
        phrases = {}
        for category, words in item.items():
            name = "test_evidence.phrases." + _shown(category)
            if category not in PHRASE_CATEGORIES:
                notices.append("rigger: config %s test_evidence.phrases category %s unknown; ignored"
                               % (where, _shown(category)))
            elif words is None:
                phrases[category] = None
            elif not isinstance(words, list):
                notices.append("rigger: config %s %s must be a list of words; ignored" % (where, name))
            else:
                kept = []
                for word in words:
                    problem = "must be a non-empty string" if not isinstance(word, str) or not word else \
                        _text_problem("word", word, PHRASE_TEXT_MAX)
                    if problem:
                        problem = problem[len("word "):] if problem.startswith("word ") else problem
                        notices.append("rigger: config %s %s entry %s ignored: %s"
                                       % (where, name, _shown(repr(word)), problem))
                    else:
                        kept.append(word)
                if len(kept) > PHRASES_MAX:
                    notices.append("rigger: config %s holds %d %s entries; the last %d ignored (at most %d per file)"
                                   % (where, len(kept), name, len(kept) - PHRASES_MAX, PHRASES_MAX))
                    kept = kept[:PHRASES_MAX]
                phrases[category] = kept
        clean[key] = phrases
    return clean, notices


def validate_level(data: dict, level: int, where: str) -> Tuple[dict, List[str]]:
    """The keys of one level's data that its level allows, each of the right shape, and a notice per drop."""
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
            good = all(isinstance(item, str) and not _line_problem(item) for item in value)
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
            if level > 1 and len(rows) > ROWS_MAX:
                notices.append("rigger: config %s holds %d trigger rows; the last %d ignored (at most %d per file)"
                               % (where, len(rows), len(rows) - ROWS_MAX, ROWS_MAX))
                rows = dict(list(rows.items())[:ROWS_MAX])
            value = rows
        if key == "review":
            value, found = _review(value, where)
            notices.extend(found)
        if key == "lessons":
            value, found = _lessons(value, where)
            notices.extend(found)
        if key == "turn_guard":
            value, found = _turn_guard(value, where)
            notices.extend(found)
        if key == "load":
            value, found = _load(value, where)
            notices.extend(found)
        if key == "shell_guard":
            value, found = _shell_guard(value, where)
            notices.extend(found)
        if key == "test_evidence":
            value, found = _test_evidence(value, where)
            notices.extend(found)
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
        clean, found = validate_level(data, level, where)
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
    return Resolved(effective=effective, sources=sources, inactive=inactive, notices=notices,
                    levels=applied)
