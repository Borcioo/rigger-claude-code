"""Split a rules file into sections with ids.

A section starts at a line beginning with `## ` (the test rules_start.sh uses); text before the first
heading is the file's preamble. Section text is the file's lines as rules_start.sh prints them: one
trailing CR removed from each line, every line ending in LF.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

HEADING = b"## "
PREAMBLE = "_preamble"
_CUTS = (" (", ":", " — ", " - ")
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_BLANK = " \t\r"


@dataclass(frozen=True)
class Section:
    id: str
    stem: str
    heading: Optional[str]
    text: bytes
    line: int


def slug(heading_line: str) -> str:
    """The id part of a heading: cut at the first ` (`, `:`, ` — ` or ` - `, folded to ASCII."""
    text = heading_line.strip(_BLANK)
    if text.startswith("## "):
        text = text[3:].strip(_BLANK)
    cuts = [text.find(cut) for cut in _CUTS]
    cuts = [at for at in cuts if at >= 0]
    if cuts:
        text = text[: min(cuts)]
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_SLUG.sub("-", text.lower()).strip("-")


def _lines(data: bytes) -> List[bytes]:
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return [line[:-1] if line.endswith(b"\r") else line for line in lines]


def normalize_module_bytes(data: bytes) -> bytes:
    """The bytes rules_start.sh prints for a file: one trailing CR stripped per line, LF after each."""
    return b"".join(line + b"\n" for line in _lines(data))


def split(stem: str, data: bytes) -> List[Section]:
    result = []  # type: List[Section]
    heading = None  # type: Optional[str]
    start = 1
    chunk = []  # type: List[bytes]

    def close():
        if heading is None and not chunk:
            return
        if heading is None:
            section_id = "%s.%s" % (stem, PREAMBLE)
        else:
            part = slug(heading)
            section_id = "%s.%s" % (stem, part) if part else "%s._line%d" % (stem, start)
        result.append(Section(section_id, stem, heading, b"".join(chunk), start))

    for number, line in enumerate(_lines(data), 1):
        if line.startswith(HEADING):
            close()
            heading = line.decode("utf-8", "replace").strip(_BLANK)
            start = number
            chunk = []
        chunk.append(line + b"\n")
    close()
    return result
