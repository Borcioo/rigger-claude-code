"""Assemble the rules of every discovered module into K hook parts (spec sections 8 and 9).

Every part handler computes the whole assembly and prints only its own part, so the packing must
depend on nothing but files, the environment and the audience: S1 found that parts arrive in a
different order on every run. Lines that only part 1 knows (the snapshot could not be written or
read) are put in front of part 1 after packing, never into the packed text.

Modes, all reading CLAUDE_CONFIG_DIR, RIGGER_MODULE_DIRS, HARNESS_RULES_DROP and CLAUDE_PLUGIN_ROOT:

  --part I [--subagent]       hook mode: hook JSON on stdin, prints part I; exits 0, or 10 when it prints nothing
  --render [--subagent] [--agent-type T] [--source S] [--session-id ID] [--mark-personal]
  --check [--modules DIR ...] exit 1 with one line per finding, else 0 with a summary line
  --doctor                    tab-separated CHECK lines in doctor.sh's format
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import snapshot  # noqa: E402
from config import Resolved, _shown, defaults_path, load_level, resolve  # noqa: E402
from discover import INLINE, PERSONAL, Discovery, _read_json, _Unreadable, discover, root_text  # noqa: E402,F401
from manifest import CORE, UNSAFE, ManifestError, Module, TriggerRow, load_manifest  # noqa: E402
from parts import PART_CAP, SESSION_PARTS, SUBAGENT_PARTS, Block, pack  # noqa: E402
from render import DEFAULT_SECTION, RenderContext, _slot_target, collect_rows, render  # noqa: E402
from sections import slug, split  # noqa: E402

import unicodedata  # noqa: E402

RULES_MODULE = "rigger-rules"
TALK = "rigger-talk"
BUILT_IN_SKIPS = ("Explore", "Plan")
PERSONAL_DIR = "rules.d"
PERSONAL_CAP = 9000
DROP = "HARNESS_RULES_DROP"
LAYER_RANK = {"general": 0, "company": 1, PERSONAL: 2}
SOURCES = ("startup", "clear", "compact")
DOCTOR = "run /rigger-core:doctor"
# --part exits with this when it prints nothing on purpose (resume, a skipped agent, an empty part);
# part.sh reads exit 0 with no output as a broken assembler (a file created but never filled).
EXIT_NOTHING = 10
LANGUAGE_DEFAULT = ("the language of Claude Code's `language` setting (the user's language when it is "
                    "unset)")
TALK_DEFAULTS = {"reply_language": LANGUAGE_DEFAULT, "reply_lines": "5", "report_lines": "15"}
DUPLICATE = "rigger: rule id {id} in {a} and {b}; kept {a}"
OVERRIDE = "rigger: {file} replaces rule {id}"
NO_TABLE = "rigger: trigger {id} from {file} not shown: no table {section}"
EXPECTS = ("rigger: this project expects {name}, which is not installed and enabled; "
           "run: claude plugin install {name}")
SNAPSHOT_MISSING = "rigger: snapshot missing; rules read again"
SNAPSHOT_NOT_WRITTEN = "rigger: snapshot not written; compaction will read the rules again"
CRASH = "rigger: rules not assembled: {kind}: {text} (" + DOCTOR + ")"
LEFT_OUT = "rules_extra: left out over the hook cap: {names}"
CANNOT_READ = "rules_extra: cannot read {name}"
_CONTROL = bytes(range(0, 9)) + bytes(range(11, 32))  # rules_extra.sh: tr -d '\000-\010\013-\037'
_TRIGGERS = re.compile(r"^[ \t]*\{\{triggers:([^{}\s]+)\}\}[ \t]*$", re.M)
_OMIT = re.compile(r"omitClaudeMd:[ \t]*true[ \t]*")
_DIGITS = re.compile(r"[0-9]+")
_ANY_PLACEHOLDER = re.compile(r"\{\{[^{}\s]*\}\}")


@dataclass
class Inputs:
    config_dir: Path
    project_dir: Path
    environ: Mapping[str, str]
    self_root: Path


@dataclass
class Assembly:
    blocks: List[Block]
    notices: List[str]
    sections: List[dict]
    rows: List[TriggerRow]
    resolved: Optional[Resolved]
    discovery: Discovery
    skipped: bool = False


@dataclass
class _Entry:
    id: str
    layer: str
    module: Optional[Module]  # None for personal text
    file: str
    text: bytes
    audience: str
    heading: Optional[str]
    requires: Tuple[str, ...] = ()
    owner: Optional[Module] = None  # whose root line the text prints under (D3)
    empty: bool = False
    line: bool = False  # a rules_extra line (cannot read, left out), not a section

    @property
    def personal(self) -> bool:
        return self.layer == PERSONAL

    @property
    def label(self) -> str:
        return self.module.name if self.module is not None else self.file


# --- helpers -------------------------------------------------------------------------------------

def _json_body(data: bytes) -> bytes:
    """The bytes a JSON string holds for data, without the quotes."""
    return json.dumps(data.decode("utf-8", "replace"), ensure_ascii=False)[1:-1].encode("utf-8")


def _identity(data: bytes) -> bytes:
    return data


def wrap(body: bytes) -> bytes:
    """The SubagentStart JSON wrapper around a main-format body."""
    text = body.decode("utf-8", "replace")
    payload = {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": text}}
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _this_os() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _requires_met(requires: Iterable[str], slots: dict, enabled: Iterable[str]) -> bool:
    for item in requires:
        kind, _, rest = item.partition(":")
        if kind == "os" and rest != _this_os():
            return False
        if kind == "plugin" and rest not in enabled:
            return False
        if kind == "slot":
            node = slots
            for key in rest.split("."):
                node = node.get(key) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return False
    return True


def _stem(file: str) -> str:
    name = file.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name.rsplit(".", 1)[0]


def _empty_body(text: bytes) -> bool:
    return not text.split(b"\n", 1)[-1].strip() if b"\n" in text else True


def _usable_dir(value) -> Optional[Path]:
    """A directory path from hook input or the environment: a string with no NUL that encodes."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


# --- options (spec section 9.6, S2 branch A) -----------------------------------------------------

def _option(value, kind) -> Optional[str]:
    if kind == "number" and isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    if any(unicodedata.category(ch) in UNSAFE for ch in text):
        return None
    return text


def _talk(values: Dict[str, str]) -> Dict[str, str]:
    """talk_start.sh's fallbacks: an empty language, and a number that is not a positive integer."""
    result = dict(values)
    if not result.get("reply_language"):
        result["reply_language"] = TALK_DEFAULTS["reply_language"]
    for key in ("reply_lines", "report_lines"):
        value = result.get(key) or ""
        if not _DIGITS.fullmatch(value) or value == "0":
            result[key] = TALK_DEFAULTS[key]
    return result


def _user_config(root: Path) -> dict:
    try:
        plugin = _read_json(root / ".claude-plugin" / "plugin.json")
    except _Unreadable:
        return {}
    schema = plugin.get("userConfig") if isinstance(plugin, dict) else None
    return schema if isinstance(schema, dict) else {}


def read_options(config_dir: Optional[Path], modules: List[Module]) -> Dict[str, Dict[str, str]]:
    """Option values per module: user settings.json pluginConfigs, else the plugin.json default.

    Read-only and silent: discover() already reports an unreadable settings.json.
    """
    configs = None
    if config_dir is not None:
        try:
            settings = _read_json(config_dir / "settings.json")
        except _Unreadable:  # _read_json maps a NUL in the path to _Unreadable too
            settings = None
        configs = settings.get("pluginConfigs") if isinstance(settings, dict) else None
    values = {}  # type: Dict[str, Dict[str, str]]
    for module in modules:
        if module.origin == PERSONAL:
            continue
        schema = _user_config(module.root)
        if not schema and module.name != TALK:
            continue
        entry = configs.get("%s@%s" % (module.name, module.marketplace or INLINE)) if isinstance(configs, dict) else None
        options = entry.get("options") if isinstance(entry, dict) else None
        options = options if isinstance(options, dict) else {}
        own = {}
        for name, spec in schema.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            kind = spec.get("type")
            value = _option(options.get(name), kind)
            if value is None:
                value = _option(spec.get("default"), kind)
            own[name] = "" if value is None else value
        values[module.name] = _talk(own) if module.name == TALK else own
    return values


# --- subagents without rules (spec section 9.5) ---------------------------------------------------

def _omits(path: Path) -> bool:
    try:
        lines = path.read_bytes().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if _OMIT.fullmatch(line.strip()):
            return True
    return False


def skip_set(modules: List[Module]) -> Set[str]:
    """Agent types that get no rules: built-ins, omitClaudeMd agents and manifest "none" entries."""
    names = set(BUILT_IN_SKIPS)
    for module in modules:
        if module.origin == PERSONAL:
            continue
        names.update(name for name, rules in module.subagents.items() if rules == "none")
        try:
            agents = sorted((module.root / "agents").glob("*.md"))
        except OSError:
            agents = []
        names.update("%s:%s" % (module.name, path.stem) for path in agents if _omits(path))
    return names


# --- the personal layer (D4) ----------------------------------------------------------------------

def _personal_entries(personal: Module, subagent: bool) -> Tuple[List[Tuple[str, bytes]], List[_Entry]]:
    """(kept files, extra lines) as rules_extra.sh reads them: byte order, filter, one 9,000 budget."""
    folder = personal.root / PERSONAL_DIR
    try:
        names = [name for name in os.listdir(str(folder)) if name.endswith(".md") and not name.startswith(".")]
    except OSError:
        return [], []
    names.sort(key=lambda name: name.encode("utf-8", "surrogateescape"))
    encode = _json_body if subagent else _identity
    kept = []  # type: List[Tuple[str, bytes]]
    lines = []  # type: List[_Entry]
    left = []  # type: List[str]
    size = 0
    for name in names:
        if subagent and name.endswith("-main.md"):
            continue
        if left:
            left.append(name)
            continue
        path = folder / name
        try:
            data = path.read_bytes() if path.is_file() else None
        except OSError:
            data = None
        if data is None:
            piece = (CANNOT_READ.format(name=name) + "\n").encode("utf-8", "surrogateescape")
            size += len(encode(piece))
            lines.append(_Entry("%s/%s" % (PERSONAL_DIR, name), PERSONAL, None, "%s/%s" % (PERSONAL_DIR, name), piece,
                                "both", None, line=True))
            kept.append((name, b""))
            continue
        text = data.translate(None, _CONTROL).rstrip(b"\n")
        if not text:
            continue
        piece = text + b"\n"
        if size + len(encode(piece)) > PERSONAL_CAP:
            left = [name]
            continue
        size += len(encode(piece))
        kept.append((name, piece))
    if left:
        line = (LEFT_OUT.format(names=", ".join(left)) + "\n").encode("utf-8", "surrogateescape")
        lines.append(_Entry("rules.d._left_out", PERSONAL, None, PERSONAL_DIR, line, "both", None, line=True))
    return kept, lines


# --- assembly ------------------------------------------------------------------------------------

def _config(inputs: Inputs, discovery: Discovery, modules: List[Module]) -> Tuple[Resolved, List[str]]:
    core = next((m for m in modules if m.name == CORE), None)
    paths = {1: defaults_path(core.root if core is not None else inputs.self_root),
             3: inputs.config_dir / "rigger" / "config.json",
             4: inputs.project_dir / ".claude" / "rigger.json"}
    notices = []
    levels = {}
    for level, path in paths.items():
        try:
            levels[level], found = load_level(path, level)
        except ValueError as error:
            levels[level], found = {}, ["rigger: config %s ignored: %s" % (_shown(path, 300), _shown(error))]
        notices.extend(found)
    company = [(m.name, m.config) for m in modules if m.layer == "company" and isinstance(m.config, dict)]
    resolved = resolve(levels[1], company, levels[3], levels[4], {k: str(v) for k, v in paths.items()})
    return resolved, notices + resolved.notices


def _place(entries: List[_Entry], index: Dict[str, int], entry: _Entry, notices: List[str]) -> None:
    at = index.get(entry.id)
    if at is None:
        index[entry.id] = len(entries)
        entry.owner = entry.module
        entries.append(entry)
        return
    old = entries[at]
    if entry.personal and old.personal:
        # rules_extra.sh printed rules.d whole (D4): a repeated id is appended under a key of its own.
        number = 2
        while "%s#%d" % (entry.id, number) in index:
            number += 1
        entry.id = "%s#%d" % (entry.id, number)
        index[entry.id] = len(entries)
        entries.append(entry)
        return
    if LAYER_RANK[old.layer] >= LAYER_RANK[entry.layer]:
        notices.append(DUPLICATE.format(id=entry.id, a=old.label, b=entry.label))
        return
    if entry.personal:
        notices.append(OVERRIDE.format(file=entry.file, id=entry.id))
        if not entry.file.endswith("-main.md"):
            entry.audience = old.audience
    entry.owner = old.owner if entry.personal else entry.module  # D3: personal text keeps the root
    entry.empty = entry.heading is not None and _empty_body(entry.text)
    entries[at] = entry


def assemble(inputs: Inputs, audience: str, agent_type: Optional[str] = None,
             mark_personal: bool = False) -> Assembly:
    discovery = discover(inputs.config_dir, inputs.project_dir, inputs.environ, inputs.self_root)
    modules = [m for m in discovery.modules if m.origin != PERSONAL]
    if audience == "subagent" and agent_type is not None and agent_type in skip_set(modules):
        return Assembly([], [], [], [], None, discovery, skipped=True)
    subagent = audience == "subagent"
    resolved, notices = _config(inputs, discovery, modules)
    effective = resolved.effective
    notices = list(discovery.notices) + notices
    expects = effective.get("expects")
    for name in expects if isinstance(expects, list) else ():
        if name not in discovery.enabled:
            notices.append(EXPECTS.format(name=_shown(name)))
    slots = effective.get("slots") if isinstance(effective.get("slots"), dict) else {}

    entries = []  # type: List[_Entry]
    index = {}  # type: Dict[str, int]
    for layer in ("general", "company"):
        for module in modules:
            for rule in module.rules:
                if rule.layer != layer:
                    continue
                try:
                    data = (module.root / rule.file).read_bytes()
                except OSError as error:
                    notices.append("rigger: rules file %s of %s unreadable (%s); skipped"
                                   % (_shown(rule.file), _shown(module.name), _shown(error.strerror or error)))
                    continue
                for section in split(_stem(rule.file), data):
                    _place(entries, index, _Entry(section.id, layer, module, rule.file, section.text, rule.audience,
                                                  section.heading, rule.requires), notices)

    extra = []  # type: List[_Entry]
    personal = next((m for m in discovery.modules if m.origin == PERSONAL), None)
    if personal is not None and any(m.name == RULES_MODULE for m in modules):  # D4
        kept, extra = _personal_entries(personal, subagent)
        for name, piece in kept:
            if not piece:
                continue
            file = "%s/%s" % (PERSONAL_DIR, name)
            main_only = name.endswith("-main.md")
            for section in split(_stem(name), piece):
                _place(entries, index, _Entry(section.id, PERSONAL, None, file, section.text,
                                              "main" if main_only else "both", section.heading), notices)
        # A "cannot read" line keeps its place among the personal files; the left-out line ends them.
    ordered = entries + extra

    enabled = discovery.enabled
    rows = [row for row in collect_rows(modules, effective) if _requires_met(row.requires, slots, enabled)]
    by_section = {}  # type: Dict[str, List[TriggerRow]]
    for row in rows:
        by_section.setdefault(row.section or DEFAULT_SECTION, []).append(row)
    tables = set()
    for entry in entries:
        if not entry.personal:
            tables.update(_TRIGGERS.findall(entry.text.decode("utf-8", "replace")))
    for row in rows:
        section = row.section or DEFAULT_SECTION
        source = resolved.sources.get(("triggers", row.id, "before"), "")
        if section not in tables and source[:3] in ("L3:", "L4:"):
            notices.append(NO_TABLE.format(id=_shown(row.id), file=_shown(source[3:], 300), section=_shown(section)))

    values = read_options(inputs.config_dir, modules)
    drop = {item.strip() for item in re.split(r"[;\n]", inputs.environ.get(DROP, "")) if item.strip()}

    def context(module: Module) -> RenderContext:
        return RenderContext(slots=slots, rows_by_section=by_section, vars=values, module=module.name)

    blocks = []  # type: List[Block]
    sections = []  # type: List[dict]
    for entry in ordered:
        if entry.empty or (subagent and entry.audience == "main"):
            continue
        if entry.personal:
            text = entry.text
            root = "personal" if mark_personal else (root_text(entry.owner.root) if entry.owner else "")
        else:
            if not _requires_met(entry.requires, slots, enabled):
                continue
            ctx = context(entry.module)
            if drop:
                heading = render(entry.heading.encode("utf-8"), ctx)[0].decode("utf-8", "replace").strip() \
                    if entry.heading is not None else None
                if entry.file.rsplit("/", 1)[-1] in drop or entry.id in drop or (heading and heading in drop):
                    continue
            text = render(entry.text, ctx)[0]
            root = root_text(entry.owner.root)
        blocks.append((root, text, entry.id))
        if not entry.line:
            sections.append({"id": entry.id, "layer": entry.layer,
                             "module": entry.module.name if entry.module is not None else PERSONAL,
                             "file": entry.file, "audience": entry.audience})
    return Assembly(blocks, notices, sections, rows, resolved, discovery)


def part_bodies(found: Assembly, subagent: bool, count: Optional[int] = None) -> List[bytes]:
    count = count or (SUBAGENT_PARTS if subagent else SESSION_PARTS)
    if found.skipped:
        return [b""] * count
    blocks = list(found.blocks)
    if found.notices:
        blocks.insert(0, ("", "".join(line + "\n" for line in found.notices).encode("utf-8", "replace"), "_notices"))
    if subagent:
        return pack(blocks, PART_CAP, count, _json_body)[0]
    # Room in part 1 for the longest line only part 1 may add after packing (M1).
    reserve = max(len(SNAPSHOT_MISSING), len(SNAPSHOT_NOT_WRITTEN)) + 1
    bodies, _ = pack([("", b"\0" * reserve, "_reserve")] + blocks, PART_CAP, count, _identity)
    return [bodies[0][reserve:]] + bodies[1:]


def whole_text(found: Assembly) -> bytes:
    if found.skipped:
        return b""
    blocks = list(found.blocks)
    if found.notices:
        blocks.insert(0, ("", "".join(line + "\n" for line in found.notices).encode("utf-8", "replace"), "_notices"))
    return pack(blocks, 1 << 40, 1, _identity)[0][0]


# --- the snapshot (spec section 9.4) --------------------------------------------------------------

def _snapshot_data(inputs: Inputs) -> Tuple[dict, List[bytes]]:
    main = assemble(inputs, "main")
    sub = assemble(inputs, "subagent")
    resolved = main.resolved
    modules = [m for m in main.discovery.modules]
    return {
        "format": snapshot.SNAPSHOT_FORMAT,
        "parts": {"main": [b.decode("utf-8", "surrogateescape") for b in part_bodies(main, False)],
                  "subagent": [b.decode("utf-8", "surrogateescape") for b in part_bodies(sub, True)]},
        "modules": [{"name": m.name, "root": root_text(m.root), "origin": m.origin, "marketplace": m.marketplace,
                     "version": _version(m.root)} for m in modules],
        "config": {"effective": resolved.effective,
                   "sources": [[list(path), source] for path, source in sorted(resolved.sources.items())]},
        "sections": main.sections,
        "rows": [{"id": r.id, "before": r.before, "load": list(r.load), "section": r.section,
                  "requires": list(r.requires)} for r in main.rows],
        "slots": resolved.effective.get("slots"),
        "notices": main.notices,
        "skip": sorted(skip_set(modules)),
    }, part_bodies(main, False)


def _version(root: Path) -> Optional[str]:
    try:
        plugin = _read_json(root / ".claude-plugin" / "plugin.json")
    except _Unreadable:
        return None
    version = plugin.get("version") if isinstance(plugin, dict) else None
    return version if isinstance(version, str) else None


def _stored_parts(data: Optional[dict], audience: str, count: int) -> Optional[List[bytes]]:
    parts = data.get("parts") if isinstance(data, dict) else None
    bodies = parts.get(audience) if isinstance(parts, dict) else None
    if not isinstance(bodies, list) or len(bodies) != count or not all(isinstance(b, str) for b in bodies):
        return None
    try:
        return [b.encode("utf-8", "surrogateescape") for b in bodies]
    except UnicodeEncodeError:
        return None


# --- modes ---------------------------------------------------------------------------------------

def _inputs(environ: Mapping[str, str], hook: dict) -> Inputs:
    value = environ.get("CLAUDE_CONFIG_DIR", "")
    config_dir = Path(value) if value else Path.home() / ".claude"
    project = _usable_dir(hook.get("cwd")) or _usable_dir(environ.get("CLAUDE_PROJECT_DIR")) or Path(os.getcwd())
    plugin_root = environ.get("CLAUDE_PLUGIN_ROOT", "")
    self_root = Path(plugin_root) if plugin_root else HERE.parent.parent
    return Inputs(config_dir=config_dir, project_dir=project, environ=environ, self_root=self_root)


def _hook(data: bytes) -> dict:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(hook: dict, key: str) -> Optional[str]:
    value = hook.get(key)
    return value if isinstance(value, str) else None


def _session_part(inputs: Inputs, hook: dict, index: int) -> bytes:
    source = _text(hook, "source")
    if "source" in hook and source not in SOURCES:
        return b""
    session = _text(hook, "session_id") or ""
    late = []  # type: List[str]
    bodies = None
    if source == "compact":
        bodies = _stored_parts(snapshot.read_snapshot(inputs.config_dir, session), "main", SESSION_PARTS)
        if bodies is None:
            late.append(SNAPSHOT_MISSING)
    if bodies is None:
        writes = index == 1 and source in SOURCES and snapshot.snapshot_path(inputs.config_dir, session) is not None
        if writes:
            data, bodies = _snapshot_data(inputs)
            if snapshot.write_snapshot(inputs.config_dir, session, data) is None and source != "compact":
                late.append(SNAPSHOT_NOT_WRITTEN)
            if source in ("startup", "clear"):
                snapshot.cleanup(inputs.config_dir, time.time())
        else:
            bodies = part_bodies(assemble(inputs, "main"), False)
    body = bodies[index - 1]
    if index == 1 and late:
        body = "".join(line + "\n" for line in late).encode("utf-8") + body
    return body


def _subagent_part(inputs: Inputs, hook: dict, index: int) -> bytes:
    agent = _text(hook, "agent_type")
    stored = snapshot.read_snapshot(inputs.config_dir, _text(hook, "session_id") or "")
    bodies = _stored_parts(stored, "subagent", SUBAGENT_PARTS)
    skip = stored.get("skip") if isinstance(stored, dict) else None
    if bodies is not None and isinstance(skip, list) and all(isinstance(s, str) for s in skip):
        if agent is not None and agent in set(skip) | set(BUILT_IN_SKIPS):
            return b""
    else:
        bodies = part_bodies(assemble(inputs, "subagent", agent), True)
    body = bodies[index - 1]
    return wrap(body) if body else b""


def _option_value(argv: List[str], name: str) -> Optional[str]:
    if name not in argv:
        return None
    at = argv.index(name)
    if at + 1 >= len(argv):
        raise ValueError("%s needs a value" % name)
    return argv[at + 1]


def _render(argv: List[str], environ: Mapping[str, str]) -> bytes:
    subagent = "--subagent" in argv
    source = _option_value(argv, "--source")
    session = _option_value(argv, "--session-id") or ""
    hook = {"cwd": None}
    inputs = _inputs(environ, hook)
    if source is not None and source not in SOURCES:
        return b""
    audience = "subagent" if subagent else "main"
    if source == "compact" or subagent:
        count = SUBAGENT_PARTS if subagent else SESSION_PARTS
        stored = _stored_parts(snapshot.read_snapshot(inputs.config_dir, session), audience, count)
        if stored is not None:
            return b"".join(stored)
    return whole_text(assemble(inputs, audience, _option_value(argv, "--agent-type"),
                               mark_personal="--mark-personal" in argv))


def _distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
    return row[-1]


def _closest(section_id: str, candidates: List[str]) -> Optional[str]:
    """The candidate id whose slug is nearest by edit distance; the first one on a tie (D22)."""
    wanted = section_id.split(".", 1)[-1]
    best = None  # type: Optional[Tuple[int, int, str]]
    for order, candidate in enumerate(candidates):
        key = (_distance(wanted, candidate.split(".", 1)[-1]), order, candidate)
        best = key if best is None or key < best else best
    return best[2] if best else None


def _marketplace_of(root: Path) -> str:
    try:
        listing = _read_json(root.parent.parent / ".claude-plugin" / "marketplace.json")
    except _Unreadable:
        return INLINE
    name = listing.get("name") if isinstance(listing, dict) else None
    return name if isinstance(name, str) and name else INLINE


def check(module_dirs: List[Path], self_root: Path) -> Tuple[List[str], List[str], int, int]:
    """(findings, info lines, modules read, rules files read) for spec section 8.4."""
    findings = []  # type: List[str]
    modules = []  # type: List[Module]
    for root in module_dirs:
        try:
            modules.append(load_manifest(root, _marketplace_of(root), "check"))
        except ManifestError as error:
            findings.append("module %s skipped: %s" % (_shown(root.name), _shown(error, 300)))
    modules.sort(key=lambda m: m.name != CORE)
    core = next((m for m in modules if m.name == CORE), None)
    l1, notices = load_level(defaults_path(core.root if core is not None else self_root), 1)
    findings.extend(notices)
    effective = resolve(l1, [], {}, {}, {}).effective
    slots = effective.get("slots") if isinstance(effective.get("slots"), dict) else {}
    rows = collect_rows(modules, effective)
    by_section = {}  # type: Dict[str, List[TriggerRow]]
    for row in rows:
        by_section.setdefault(row.section or DEFAULT_SECTION, []).append(row)
    values = read_options(None, modules)
    names = {m.name: m for m in modules}
    files = 0
    seen = {}  # type: Dict[str, Tuple[str, str]]  id -> (layer, module)
    general_stems = {}  # type: Dict[str, List[str]]  file stem -> general section ids, in file order
    for layer in ("general", "company"):
        for module in modules:
            for rule in module.rules:
                if rule.layer != layer:
                    continue
                files += 1
                where = "%s/%s" % (module.name, rule.file)
                if layer == "general":
                    general_stems.setdefault(_stem(rule.file), [])
                data = (module.root / rule.file).read_bytes()
                ctx = RenderContext(slots=slots, rows_by_section=by_section, vars=values, module=module.name)
                for section in split(_stem(rule.file), data):
                    if section.heading is not None and not slug(section.heading):
                        findings.append("empty heading slug in %s:%d" % (where, section.line))
                    before = seen.get(section.id)
                    if before is not None and before[0] == layer:
                        findings.append("duplicate rule id %s in %s and %s" % (section.id, before[1], module.name))
                    elif before is None and layer == "company" and (
                            _stem(rule.file) in general_stems
                            or (section.heading is not None and _empty_body(section.text))):
                        line = "orphan override %s in %s: no lower section has that id" % (section.id, where)
                        nearest = _closest(section.id, general_stems.get(_stem(rule.file), []))
                        findings.append(line + ("; closest general id %s" % nearest if nearest else ""))
                    if layer == "general" and section.id not in general_stems[_stem(rule.file)]:
                        general_stems[_stem(rule.file)].append(section.id)
                    if before is None or before[0] != layer:
                        seen[section.id] = (layer, module.name)
                    text, unknown = render(section.text, ctx)
                    for placeholder in dict.fromkeys(unknown):
                        findings.append("unknown placeholder %s in %s" % (placeholder, where))
                    if len(text) > PART_CAP:
                        findings.append("section %s in %s is over %d bytes" % (section.id, where, PART_CAP))
    external = set()
    for row in rows:
        for entry in row.load:
            target = _slot_target(slots, entry[len("slot:"):]) if entry.startswith("slot:") else entry
            plugin, _, skill = (target or "").partition(":")
            if not plugin or not skill:
                findings.append("trigger %s load %s resolves to nothing" % (row.id, entry))
            elif plugin not in names:
                external.add(plugin)
            elif not (names[plugin].root / "skills" / skill / "SKILL.md").is_file():
                findings.append("trigger %s load %s resolves to nothing" % (row.id, entry))
    if files == 0:
        findings.append("0 rules files read")
    return findings, ["# external: %s" % name for name in sorted(external)], len(modules), files


def _check(argv: List[str], environ: Mapping[str, str]) -> Tuple[int, bytes]:
    inputs = _inputs(environ, {})
    if "--modules" in argv:
        dirs = [Path(arg) for arg in argv[argv.index("--modules") + 1:] if not arg.startswith("--")]
    else:
        dirs = sorted(path.parent for path in inputs.self_root.parent.glob("*/rigger-module.json"))
    findings, info, count, files = check(dirs, inputs.self_root)
    lines = info + findings
    if not findings:
        lines.append("# check: modules %d, rules files %d, findings 0" % (count, files))
    return (1 if findings else 0), "".join(line + "\n" for line in lines).encode("utf-8", "replace")


def _doctor(environ: Mapping[str, str]) -> bytes:
    found = assemble(_inputs(environ, {}), "main")
    modules = len([m for m in found.discovery.modules if m.origin != PERSONAL])
    if found.notices:
        status, detail = "WARN", found.notices[0]
    else:
        status, detail = "OK", "modules %d, sections %d" % (modules, len(found.sections))
    return ("CHECK\trules-assembly\t%s\t%s\tassemble.py --doctor\n" % (status, detail.replace("\t", " "))).encode(
        "utf-8", "replace")


def _part_index(argv: List[str]) -> int:
    value = _option_value(argv, "--part")
    count = SUBAGENT_PARTS if "--subagent" in argv else SESSION_PARTS
    if value is None or not _DIGITS.fullmatch(value) or not 1 <= int(value) <= count:
        raise ValueError("--part must be 1 to %d" % count)
    return int(value)


def main(argv: Optional[List[str]] = None, stdin: Optional[bytes] = None, stdout=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = stdout if stdout is not None else sys.stdout.buffer
    environ = os.environ
    subagent = "--subagent" in argv
    if "--part" in argv:
        index = None
        try:
            index = _part_index(argv)
            hook = _hook(sys.stdin.buffer.read() if stdin is None else stdin)
            inputs = _inputs(environ, hook)
            data = _subagent_part(inputs, hook, index) if subagent else _session_part(inputs, hook, index)
        except Exception as error:  # never a silent empty part: part 1 says what broke
            data = b""
            if index == 1 or (index is None and _option_value(argv, "--part") == "1"):
                line = (CRASH.format(kind=type(error).__name__, text=_shown(error, 300)) + "\n").encode("utf-8")
                data = wrap(line) if subagent else line
        out.write(data)
        out.flush()
        return 0 if data else EXIT_NOTHING
    if "--render" in argv:
        out.write(_render(argv, environ))
        code = 0
    elif "--check" in argv:
        code, data = _check(argv, environ)
        out.write(data)
    elif "--doctor" in argv:
        out.write(_doctor(environ))
        code = 0
    else:
        sys.stderr.write("usage: assemble.py --part I [--subagent] | --render | --check | --doctor\n")
        code = 2
    out.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
