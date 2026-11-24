"""Render placeholders in module rules text and the trigger tables (spec sections 9.2 and 9.3).

One pass over the text: inserted text (a slot target, an option value, a table row) is never
scanned again, as rules_start.sh promises for `--var` today. A `{{triggers:<id>}}` placeholder
must stand alone on its line; the whole line, newline included, becomes the table, or nothing
when the section has no rows (D7). Anything else is an unknown placeholder: printed as written and
reported.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from config import _shown
from manifest import Module, TriggerRow
from parts import PART_CAP, _piece

DEFAULT_SECTION = "skills.skill-triggers"
PLACEHOLDER = re.compile(r"\{\{([a-z]+):([^{}\s]+)\}\}")
LEGACY = re.compile(r"\{\{([a-z_]+)\}\}")
_ANY = re.compile(r"\{\{(?:([a-z]+):([^{}\s]+)|([a-z_]+))\}\}")
_TRIGGERS_LINE = re.compile(r"[ \t]*\{\{triggers:([^{}\s]+)\}\}[ \t]*")
HEADER = b"| Before | Skill |\n|---|---|\n"
JOINER = ", then "
SECOND_TABLE = "second trigger table"
DROP_ORDER = ("L4:", "L3:", "L2:")  # project rows leave the table first, then personal, then company
LEFT_OUT = "rigger: left out {count} of the trigger rows from {files}: the Skill triggers table must fit in one hook part"


@dataclass
class RenderContext:
    slots: dict
    rows_by_section: Dict[str, List[TriggerRow]]
    vars: Dict[str, Dict[str, str]]
    module: str
    rows: Optional[List[TriggerRow]] = None
    enabled: Optional[FrozenSet[str]] = None
    table_seen: Optional[List[str]] = None
    # The config level that set each row ("L2:<module>", "L3:<file>", "L4:<file>"), the module or
    # level-1 row a config row replaced, and whether a rendered section fits one hook part.
    row_sources: Optional[Dict[str, str]] = None
    replaced: Optional[Dict[str, TriggerRow]] = None
    fits: Optional[Callable[[bytes], bool]] = None


def fits_part(root: str, encode: Callable[[bytes], bytes], cap: int = PART_CAP) -> Callable[[bytes], bool]:
    """Whether a section, with the `Plugin root:` line the packer puts before it, fits an empty part."""
    def fits(body: bytes) -> bool:
        return len(encode(_piece(0, (root, body, ""), [])[2])) <= cap
    return fits


def _slot_target(slots: dict, path: str) -> Optional[str]:
    node = slots
    for key in path.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    target = node.get("target") if isinstance(node, dict) else None
    return target if isinstance(target, str) else None


def _entry(entry: str, slots: dict) -> str:
    if entry.startswith("slot:"):
        target = _slot_target(slots, entry[len("slot:"):])
        if target is not None:
            return target
    return entry


def visible(rows: List[TriggerRow], slots: dict,
            enabled: Optional[FrozenSet[str]]) -> List[Tuple[TriggerRow, List[str]]]:
    """Each row with its resolved entries (D39); enabled None drops nothing, as phase 1 printed.

    An entry goes when its slot is empty or its plugin is not enabled; a row goes when no entry is left.
    An entry with no `:` names no plugin (a personal skill) and stays.
    """
    shown = []
    for row in rows:
        entries = []
        for entry in row.load:
            name = _entry(entry, slots)
            if enabled is not None:
                if entry.startswith("slot:") and _slot_target(slots, entry[len("slot:"):]) is None:
                    continue
                if ":" in name and name.split(":", 1)[0] not in enabled:
                    continue
            entries.append(name)
        if entries or enabled is None:
            shown.append((row, entries))
    return shown


def _row_lines(pairs: List[Tuple[TriggerRow, List[str]]]) -> bytes:
    return "".join("| %s | %s |\n" % (row.before, JOINER.join("`%s`" % e for e in entries))
                   for row, entries in pairs).encode("utf-8")


def table(rows: List[TriggerRow], slots: dict, enabled: Optional[FrozenSet[str]] = None) -> bytes:
    pairs = visible(rows, slots, enabled)
    return HEADER + _row_lines(pairs) if pairs else b""


def collect_rows(modules: List[Module], effective: dict) -> List[TriggerRow]:
    """Module rows in module order, then config rows: null removes, a known id replaces in place."""
    rows = {}  # type: Dict[str, TriggerRow]
    for module in modules:
        for row in module.triggers:
            rows.setdefault(row.id, row)
    triggers = effective.get("triggers") if isinstance(effective, dict) else None
    for row_id, value in (triggers.items() if isinstance(triggers, dict) else ()):
        if value is None:
            rows.pop(row_id, None)
            continue
        rows[row_id] = TriggerRow(
            id=row_id,
            before=value["before"],
            load=tuple(value["load"]),
            section=value.get("section") or DEFAULT_SECTION,
            requires=tuple(value.get("requires") or ()),
        )
    return list(rows.values())


def render(text: bytes, ctx: RenderContext) -> Tuple[bytes, List[str]]:
    unknown = []  # type: List[str]
    own_vars = ctx.vars.get(ctx.module, {})

    def replace(match) -> str:
        kind, value, legacy = match.group(1), match.group(2), match.group(3)
        if legacy is not None:
            if legacy in own_vars:
                return own_vars[legacy]
        elif kind == "slot":
            target = _slot_target(ctx.slots, value)
            if target is not None:
                return "`%s`" % target
        elif kind == "skill" and ":" in value:
            return "`%s`" % value
        elif kind == "var" and value in own_vars:
            return own_vars[value]
        unknown.append(match.group(0))
        return match.group(0)

    out = []  # type: List[bytes]
    decoded = text.decode("utf-8", "surrogateescape")
    lines = [part + "\n" for part in decoded.split("\n")]
    lines[-1] = lines[-1][:-1]
    table_at = None  # type: Optional[int]
    for line in lines:
        body = line[:-1] if line.endswith("\n") else line
        whole = _TRIGGERS_LINE.fullmatch(body)
        if whole and ctx.rows is not None:
            if table_at is not None or ctx.table_seen:
                unknown.append(SECOND_TABLE)
                continue
            table_at = len(out)
            out.append(b"")  # filled once the rest of the section is known
            if ctx.table_seen is not None:
                ctx.table_seen.append(ctx.module)
            continue
        if whole:
            out.append(table(ctx.rows_by_section.get(whole.group(1), []), ctx.slots))
            continue
        out.append(_ANY.sub(replace, line).encode("utf-8", "surrogateescape"))
    if table_at is not None:
        out[table_at] = _fitted_table(ctx, b"".join(out[:table_at]), b"".join(out[table_at + 1:]))
    return b"".join(out), unknown


def _source_name(source: str) -> str:
    if source.startswith("L2:"):
        return "company module %s" % _shown(source[3:])
    return _shown(source[3:], 300)


def _fitted_table(ctx: RenderContext, before: bytes, after: bytes) -> bytes:
    """The merged table, with config rows left out until the section fits one part.

    Module and level-1 rows always stay (coordinator ruling on review RV1 Important 1): project rows
    go first, then personal, then company rows, each level from its last row up. A dropped row that
    replaced a module or level-1 row brings that row back. When the kept rows alone do not fit, every
    config row goes and the packer reports the section.
    """
    pairs = visible(ctx.rows or [], ctx.slots, ctx.enabled)
    sources = ctx.row_sources or {}
    replaced = ctx.replaced or {}
    queue = [row.id for prefix in DROP_ORDER for row, _ in reversed(pairs)
             if sources.get(row.id, "").startswith(prefix)]

    def build(count: int) -> bytes:
        dropped = set(queue[:count])
        rows = [replaced.get(row.id) if row.id in dropped else row for row, _ in pairs]
        kept = visible([row for row in rows if row is not None], ctx.slots, ctx.enabled)
        text = HEADER + _row_lines(kept) if kept else b""
        if count:
            names = []  # type: List[str]
            for row_id in queue[:count]:
                name = _source_name(sources[row_id])
                if name not in names:
                    names.append(name)
            files = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            text += ("\n" + LEFT_OUT.format(count=count, files=files) + "\n").encode("utf-8")
        return text

    def fits(count: int) -> bool:
        return ctx.fits is None or ctx.fits(before + build(count) + after)

    if fits(0):
        return build(0)
    low, high = 0, len(queue)  # build(low) does not fit; build(high) fits or keeps only what must stay
    if fits(high):
        while high - low > 1:
            middle = (low + high) // 2
            if fits(middle):
                high = middle
            else:
                low = middle
    return build(high)
