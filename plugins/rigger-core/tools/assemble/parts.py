"""Pack section blocks into K hook parts under a byte cap.

Parts are filled in order and cut only at block boundaries. Each part starts with a
`Plugin root:` line before its first block that has a root, and repeats it when the root changes
(D3); blocks with root "" (notices, personal text) get none. The cap applies to the encoded body
(identity, or JSON string escaping in subagent mode), never counting the fixed JSON wrapper.
"""

from typing import Callable, List, Optional, Tuple

Block = Tuple[str, bytes, str]

SESSION_PARTS = 6
SUBAGENT_PARTS = 4
PART_CAP = 9000
OVERFLOW_LINE = "rigger: rules left out over the hook cap: {ids}"

# A placed block: (position in the input, root, bytes printed for it, root line included).
_Piece = Tuple[int, str, bytes]


def _body(pieces: List[_Piece], extra: bytes = b"") -> bytes:
    return b"".join(piece[2] for piece in pieces) + extra


def _last_root(pieces: List[_Piece]) -> Optional[str]:
    roots = [piece[1] for piece in pieces if piece[1]]
    return roots[-1] if roots else None


def _piece(position: int, block: Block, pieces: List[_Piece]) -> _Piece:
    root, text, _ = block
    if root and root != _last_root(pieces):
        text = ("Plugin root: %s\n" % root).encode("utf-8") + text
    return (position, root, text)


def pack(
    blocks: List[Block], cap: int, count: int, encode: Callable[[bytes], bytes], first_cap: Optional[int] = None
) -> Tuple[List[bytes], List[str]]:
    """Return `count` part bodies (b"" when unused) and the ids of the blocks left out, in order.

    `first_cap` keeps room in part 1 for a line added after packing. When anything is left out, the
    last part ends with OVERFLOW_LINE; blocks at the end of the last part make room for that line
    when they must.
    """
    built = [[] for _ in range(count)]  # type: List[List[_Piece]]
    caps = [cap] * count
    if count and first_cap is not None:
        caps[0] = first_cap
    index = 0
    left_out = []  # type: List[int]

    for position, block in enumerate(blocks):
        placed = False
        while index < count:
            piece = _piece(position, block, built[index])
            if len(encode(_body(built[index], piece[2]))) <= caps[index]:
                built[index].append(piece)
                placed = True
                break
            if not built[index] and len(encode(piece[2])) > cap:
                break  # larger than an empty part: it can never fit
            index += 1
        if not placed:
            if index >= count:
                left_out.extend(range(position, len(blocks)))
                break
            left_out.append(position)

    if not left_out or not count:
        return [_body(pieces) for pieces in built], [blocks[i][2] for i in left_out]
    last = built[-1]
    while True:
        ids = [blocks[i][2] for i in sorted(left_out)]
        line = (OVERFLOW_LINE.format(ids=", ".join(ids)) + "\n").encode("utf-8")
        if len(encode(_body(last, line))) <= caps[-1] or not last:
            break
        left_out.append(last.pop()[0])
    return [_body(pieces) for pieces in built[:-1]] + [_body(last, line)], ids
