"""Load and validate rigger-module.json.

Validation is explicit: one reason per failure, the first failure wins, and the reason is the text
of `rigger: module <name> skipped: <reason>`. Unknown top-level keys are ignored, so a manifest
written for a later phase still loads.
"""

import json
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
MAX_DEPTH = 32


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
    fills = data.get("fills")
    if fills is not None and not isinstance(fills, str):
        raise ManifestError("fills must be a slot name or null")
    provides = _object(data, "provides", {})
    config = _object(data, "config", None)
    if config is not None and layer != "company" and name != CORE:
        raise ManifestError("config is honoured only in company modules and %s" % CORE)
    lessons = _object(data, "lessons", None)
    tests = _object(data, "tests", {})
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
    )
