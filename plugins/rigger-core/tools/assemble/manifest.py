"""Load and validate rigger-module.json.

Validation is explicit: one reason per failure, the first failure wins, and the reason is the text
of `rigger: module <name> skipped: <reason>`. Unknown top-level keys are ignored, so a manifest
written for a later phase still loads.
"""

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MANIFEST = "rigger-module.json"
PUBLIC_MARKETPLACE = "rigger"
CORE = "rigger-core"

LAYERS = ("general", "company")
AUDIENCES = ("main", "subagent", "both")
OS_NAMES = ("windows", "macos", "linux")
SUBAGENT_RULES = ("none", "all")
SLOT_PATHS = (
    "reviewer", "worker", "implementer", "mapper", "testing", "shell-guard", "lessons",
    "process.brainstorm", "process.plans", "process.tdd", "process.debug",
)
FAILURE_MODES = ("open", "closed")
MAX_DEPTH = 32
BEFORE_MAX = 300
LOAD_MAX = 4
LOAD_ENTRY_MAX = 200
# Control, format, line and paragraph separators, lone surrogates and private use. Not "Cn": what is
# unassigned depends on the running Python's Unicode data, so a newer emoji would be refused.
UNSAFE = ("Cc", "Cf", "Zl", "Zp", "Cs", "Co")


class ManifestError(Exception):
    pass


@dataclass
class RuleEntry:
    file: str
    layer: str
    audience: str
    requires: Tuple[str, ...]


@dataclass
class TriggerRow:
    id: str
    before: str
    load: Tuple[str, ...]
    section: Optional[str]
    requires: Tuple[str, ...]


@dataclass
class Module:
    name: str
    root: Path
    layer: str
    fills: Optional[str]
    provides: dict
    rules: List[RuleEntry]
    triggers: List[TriggerRow]
    subagents: Dict[str, str]
    config: Optional[dict]
    lessons: Optional[dict]
    tests: dict
    marketplace: Optional[str]
    origin: str
    failure_mode: Optional[str] = None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _TooDeep:
    """Stands for JSON the parser itself refused as too deep (RecursionError)."""


TOO_DEEP = _TooDeep()


def parsed_problem(value) -> Optional[str]:
    """Why parsed JSON is refused before anything walks it recursively, or None.

    Walks without recursion: a value nested deeper than MAX_DEPTH would make every later
    recursive walk (merge, leaves) raise RecursionError.
    """
    if value is TOO_DEEP:
        return "nested deeper than %d levels" % MAX_DEPTH
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > MAX_DEPTH:
                return "nested deeper than %d levels" % MAX_DEPTH
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return None


def _read_json(path: Path, what: str):
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ManifestError("%s unreadable: %s" % (what, error.strerror or error))
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except RecursionError:
        parsed = TOO_DEEP
    except (UnicodeDecodeError, ValueError) as error:
        raise ManifestError("%s not valid JSON: %s" % (what, error))
    problem = parsed_problem(parsed)
    if problem:
        raise ManifestError("%s %s" % (what, problem))
    return parsed


def _requires(value, what: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError("%s: requires must be a list" % what)
    for item in value:
        kind, _, rest = item.partition(":") if isinstance(item, str) else ("", "", "")
        if kind == "os" and rest in OS_NAMES:
            continue
        if kind in ("slot", "plugin") and rest:
            continue
        raise ManifestError("%s: requires %r: expected slot:<x>, plugin:<name> or os:windows|macos|linux" % (what, item))
    return tuple(value)


def check_requires(value, what: str) -> Tuple[str, ...]:
    """Validate a `requires` list (slot:<x>, plugin:<name>, os:windows|macos|linux); raise ManifestError."""
    return _requires(value, what)


def line_problem(text: str) -> Optional[str]:
    if "|" in text:
        return "holds |"
    if any(unicodedata.category(ch) in UNSAFE for ch in text):
        return "holds a line break or control character"
    return None


def row_problem(row) -> Optional[str]:
    if row is None:
        return None
    if not isinstance(row, dict):
        return "must be an object or null"
    before, load = row.get("before"), row.get("load")
    if not isinstance(before, str):
        return "before must be a string"
    problem = line_problem(before)
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
        problem = line_problem(entry)
        if problem:
            return "load entry " + problem
        if len(entry) > LOAD_ENTRY_MAX:
            return "load entry over %d characters" % LOAD_ENTRY_MAX
    try:
        _requires(row.get("requires"), "requires")
    except ManifestError as error:
        return str(error)
    if any(line_problem(item) for item in row.get("requires") or []):
        return "requires entry holds a line break, control character or |"
    section = row.get("section")
    if section is not None and (not isinstance(section, str) or line_problem(section)):
        return "section must be a one-line string"
    return None


def _inside(file: str) -> bool:
    """A rules path relative to the plugin root, written with `/`, that cannot leave it."""
    if not file or file.startswith("/") or "\\" in file or ":" in file:
        return False
    return ".." not in file.split("/")


def _rules(value, module_layer: str, root: Path) -> List[RuleEntry]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("rules must be a list")
    entries = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ManifestError("rules entry must be an object with a file")
        file = entry["file"]
        if not _inside(file):
            raise ManifestError("rules file %s is outside the module" % file)
        if not (root / file).is_file():
            raise ManifestError("missing rules file %s" % file)
        layer = entry.get("layer", module_layer)
        if layer not in LAYERS:
            raise ManifestError("rules file %s: layer %r" % (file, layer))
        if LAYERS.index(layer) > LAYERS.index(module_layer):
            raise ManifestError("rules file %s: layer %s above its module (%s)" % (file, layer, module_layer))
        audience = entry.get("audience")
        if audience not in AUDIENCES:
            raise ManifestError("rules file %s: audience %r: expected main, subagent or both" % (file, audience))
        entries.append(RuleEntry(file, layer, audience, _requires(entry.get("requires"), "rules file " + file)))
    return entries


def _triggers(value) -> List[TriggerRow]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("triggers must be a list")
    rows = []
    seen = set()
    for row in value:
        if not isinstance(row, dict):
            raise ManifestError("triggers entry must be an object")
        row_id, before, load, section = row.get("id"), row.get("before"), row.get("load"), row.get("section")
        if not isinstance(row_id, str) or not row_id:
            raise ManifestError("trigger id must be a string")
        if row_id in seen:
            raise ManifestError("duplicate trigger id %s" % row_id)
        seen.add(row_id)
        if not isinstance(before, str):
            raise ManifestError("trigger %s: before must be a string" % row_id)
        if not isinstance(load, list):
            raise ManifestError("trigger %s: load must be a list" % row_id)
        if not all(isinstance(item, str) for item in load):
            raise ManifestError("trigger %s: load must hold strings" % row_id)
        if section is not None and not isinstance(section, str):
            raise ManifestError("trigger %s: section must be a string" % row_id)
        if line_problem(row_id):
            raise ManifestError("trigger id %s" % line_problem(row_id))
        problem = row_problem(row)
        if problem:
            raise ManifestError("trigger %s: %s" % (row_id, problem))
        rows.append(TriggerRow(row_id, before, tuple(load), section, _requires(row.get("requires"), "trigger " + row_id)))
    return rows


def _subagents(value) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError("subagents must be an object")
    result = {}
    for name, entry in value.items():
        rules = entry.get("rules") if isinstance(entry, dict) else None
        if rules not in SUBAGENT_RULES:
            raise ManifestError("subagents %s: rules must be none or all" % name)
        result[name] = rules
    return result


def _object(data: dict, key: str, default):
    value = data.get(key, default)
    if value is not default and not isinstance(value, dict):
        raise ManifestError("%s must be an object" % key)
    return value


def _fills(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError("fills must be a slot name or null")
    if value not in SLOT_PATHS:
        raise ManifestError("fills %s is not a slot" % value)
    return value


def _failure_mode(data: dict) -> Optional[str]:
    if "failure_mode" not in data:
        return None
    value = data["failure_mode"]
    if value not in FAILURE_MODES:
        raise ManifestError("failure_mode must be open or closed")
    return value


def _provides(data: dict, root: Path) -> dict:
    provides = _object(data, "provides", {})
    for slot, entry in provides.items():
        if slot not in SLOT_PATHS:
            raise ManifestError("provides %s is not a slot" % slot)
        if not isinstance(entry, dict):
            raise ManifestError("provides %s must be an object" % slot)
    entry = provides.get("shell-guard")
    if entry is not None and "rule_checker" in entry:
        checker = entry["rule_checker"]
        what = "provides shell-guard rule_checker"
        if not isinstance(checker, str):
            raise ManifestError("%s must be a string" % what)
        if not _inside(checker):
            raise ManifestError("%s %s is outside the module" % (what, checker))
        if not (root / checker).is_file():
            raise ManifestError("%s %s is not a file" % (what, checker))
    return provides


def _tests(data: dict) -> dict:
    tests = _object(data, "tests", {})
    if "contract" in tests:
        contract = tests["contract"]
        if not isinstance(contract, list) or not all(isinstance(slot, str) for slot in contract):
            raise ManifestError("tests contract must be a list of slot names")
        for slot in contract:
            if slot not in SLOT_PATHS:
                raise ManifestError("tests contract %s is not a slot" % slot)
    return tests


def load_manifest(root: Path, marketplace: Optional[str], origin: str) -> Module:
    """Load <root>/rigger-module.json, or raise ManifestError with the first reason it fails."""
    data = _read_json(root / MANIFEST, MANIFEST)
    if not isinstance(data, dict):
        raise ManifestError("%s must be an object" % MANIFEST)
    schema = data.get("schema")
    if not _is_int(schema) or schema != 1:
        raise ManifestError("schema %s not supported (expected 1)" % (schema,))
    name = data.get("name")
    plugin = _read_json(root / ".claude-plugin" / "plugin.json", "plugin.json")
    plugin_name = plugin.get("name") if isinstance(plugin, dict) else None
    if not isinstance(name, str) or name != plugin_name:
        raise ManifestError("name %r does not match plugin.json name %r" % (name, plugin_name))
    layer = data.get("layer")
    if layer not in LAYERS:
        raise ManifestError("layer %r: expected general or company" % (layer,))
    if layer == "company" and marketplace == PUBLIC_MARKETPLACE:
        raise ManifestError("company layer in the public marketplace")
    fills = _fills(data.get("fills"))
    failure_mode = _failure_mode(data)
    provides = _provides(data, root)
    config = _object(data, "config", None)
    if config is not None and layer != "company" and name != CORE:
        raise ManifestError("config is honoured only in company modules and %s" % CORE)
    lessons = _object(data, "lessons", None)
    tests = _tests(data)
    return Module(
        name=name,
        root=root,
        layer=layer,
        fills=fills,
        provides=provides,
        rules=_rules(data.get("rules"), layer, root),
        triggers=_triggers(data.get("triggers")),
        subagents=_subagents(data.get("subagents")),
        config=config,
        lessons=lessons,
        tests=tests,
        marketplace=marketplace,
        origin=origin,
        failure_mode=failure_mode,
    )
