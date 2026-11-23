"""Render placeholders in module rules text and the trigger tables (spec sections 9.2 and 9.3).

One pass over the text: inserted text (a slot target, an option value, a table row) is never
scanned again, as rules_start.sh promises for `--var` today. A `{{triggers:<id>}}` placeholder
must stand alone on its line; the whole line, newline included, becomes the table, or nothing
when the section has no rows (D7). Anything else is an unknown placeholder: printed as written and
reported.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from manifest import Module, TriggerRow

DEFAULT_SECTION = "skills.skill-triggers"
PLACEHOLDER = re.compile(r"\{\{([a-z]+):([^{}\s]+)\}\}")
LEGACY = re.compile(r"\{\{([a-z_]+)\}\}")
_ANY = re.compile(r"\{\{(?:([a-z]+):([^{}\s]+)|([a-z_]+))\}\}")
_TRIGGERS_LINE = re.compile(r"[ \t]*\{\{triggers:([^{}\s]+)\}\}[ \t]*")
HEADER = b"| Before | Skill |\n|---|---|\n"
JOINER = ", then "


@dataclass
class RenderContext:
    slots: dict
    rows_by_section: Dict[str, List[TriggerRow]]
    vars: Dict[str, Dict[str, str]]
    module: str


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


def table(rows: List[TriggerRow], slots: dict) -> bytes:
    if not rows:
        return b""
    lines = ["| %s | %s |\n" % (row.before, JOINER.join("`%s`" % _entry(e, slots) for e in row.load)) for row in rows]
    return HEADER + "".join(lines).encode("utf-8")


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
    for line in lines:
        body = line[:-1] if line.endswith("\n") else line
        whole = _TRIGGERS_LINE.fullmatch(body)
        if whole:
            out.append(table(ctx.rows_by_section.get(whole.group(1), []), ctx.slots))
            continue
        out.append(_ANY.sub(replace, line).encode("utf-8", "surrogateescape"))
    return b"".join(out), unknown
