"""Tests for parts.py: packing section blocks into K hook parts under a byte cap."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import parts  # noqa: E402

ROOT = "/plugins/demo"
OTHER = "/plugins/other"


def same(body):
    return body


def json_escape(body):
    return json.dumps(body.decode("utf-8"), ensure_ascii=False)[1:-1].encode("utf-8")


def block(block_id, size, root=ROOT, fill=b"x"):
    return (root, fill * (size - 1) + b"\n", block_id)


def root_line(root):
    return ("Plugin root: %s\n" % root).encode("utf-8")


class PackTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual((parts.SESSION_PARTS, parts.SUBAGENT_PARTS, parts.PART_CAP), (6, 4, 9000))

    def test_pack_fills_in_order_at_section_boundaries(self):
        a, b, c = block("m.a", 4000), block("m.b", 4000), block("m.c", 4000)
        bodies, left_out = parts.pack([a, b, c], 9000, 6, same)
        self.assertEqual(len(bodies), 6)
        self.assertEqual(bodies[0], root_line(ROOT) + a[1] + b[1])
        self.assertEqual(bodies[1], root_line(ROOT) + c[1])
        self.assertEqual(bodies[2:], [b""] * 4)
        self.assertEqual(left_out, [])

    def test_root_line_repeats_at_module_change(self):
        a, b, c = block("m.a", 10), block("o.b", 10, OTHER), block("m.c", 10)
        bodies, _ = parts.pack([a, b, c], 9000, 2, same)
        self.assertEqual(bodies[0], root_line(ROOT) + a[1] + root_line(OTHER) + b[1] + root_line(ROOT) + c[1])

    def test_root_free_blocks_get_no_root_line(self):
        notices = ("", b"rigger: a notice\n", "_notices")
        personal = ("", b"## Mine\n", "personal.mine")
        bodies, _ = parts.pack([notices, block("m.a", 10), personal], 9000, 1, same)
        self.assertEqual(bodies[0], notices[1] + root_line(ROOT) + block("m.a", 10)[1] + personal[1])
        self.assertEqual(bodies[0].count(b"Plugin root:"), 1)

    def test_overflow_names_ids_and_ends_last_part(self):
        blocks = [block("m.a", 8000), block("m.b", 8000), block("m.c", 8000)]
        bodies, left_out = parts.pack(blocks, 9000, 2, same)
        self.assertEqual(left_out, ["m.c"])
        line = (parts.OVERFLOW_LINE.format(ids="m.c") + "\n").encode("utf-8")
        self.assertTrue(bodies[-1].endswith(line))
        self.assertEqual(bodies[0], root_line(ROOT) + blocks[0][1])
        for body in bodies:
            self.assertLessEqual(len(body), 9000)

    def test_overflow_line_makes_room_in_the_last_part(self):
        blocks = [block("m.a", 8900), block("m.b", 8950)]
        bodies, left_out = parts.pack(blocks, 9000, 1, same)
        self.assertEqual(left_out, ["m.b"])
        self.assertLessEqual(len(bodies[0]), 9000)
        bodies, left_out = parts.pack([block("m.a", 8970), block("m.b", 10)], 9000, 1, same)
        self.assertEqual(left_out, ["m.a", "m.b"])
        self.assertEqual(bodies[0], (parts.OVERFLOW_LINE.format(ids="m.a, m.b") + "\n").encode("utf-8"))

    def test_cap_counts_encoded_bytes(self):
        bodies, left_out = parts.pack([block("m.a", 3000), block("m.b", 3000)], 9000, 3, lambda b: b * 2)
        self.assertEqual(left_out, [])
        self.assertEqual(bodies[0], root_line(ROOT) + block("m.a", 3000)[1])
        self.assertEqual(bodies[1], root_line(ROOT) + block("m.b", 3000)[1])
        quotes = block("m.q", 3000, fill=b'"')
        bodies, _ = parts.pack([quotes, block("m.b", 3000)], 9000, 2, json_escape)
        self.assertEqual(bodies[0], root_line(ROOT) + quotes[1])


if __name__ == "__main__":
    unittest.main()
