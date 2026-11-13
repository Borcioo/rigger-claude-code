"""No tracked text file names a plugin the marketplace does not ship."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_repo import tracked_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PREFIXES = "rigger"
# `rigger-core:worker` (a skill or agent) and `plugins/rigger-core` (a folder).
QUALIFIED_RE = re.compile(r"(?<![A-Za-z0-9-])%s-([a-z]+):[a-z][a-z0-9-]*" % PREFIXES)
FOLDER_RE = re.compile(r"plugins/%s-([a-z]+)(?![A-Za-z0-9-])" % PREFIXES)


def plugin_names(root):
    folder = root / "plugins"
    return {path.name.split("-", 1)[1] for path in folder.iterdir() if path.is_dir() and "-" in path.name}


def findings(root, names):
    """(files read, ["path:line: text"]) for tracked text files; a NUL byte marks a binary file."""
    found = []
    read = 0
    for name in tracked_files(root):
        data = (root / name).read_bytes()
        if b"\0" in data:
            continue
        read += 1
        for number, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            for regex in (QUALIFIED_RE, FOLDER_RE):
                for match in regex.finditer(line):
                    if match.group(1) not in names:
                        found.append("%s:%d: %s" % (name, number, match.group(0)))
    return read, found


class PluginRefs(unittest.TestCase):
    def test_no_reference_to_unshipped_plugin(self):
        names = plugin_names(ROOT)
        self.assertIn("core", names)
        read, found = findings(ROOT, names)
        self.assertGreater(read, 100, "read too few files to prove anything")
        self.assertEqual(found, [])

    def test_control_catches_planted_reference(self):
        # built at run time so this file holds no reference itself
        planted = "rigger-" + "intake" + ":ticket-intake"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "plugins" / "rigger-core").mkdir(parents=True)
            (root / "plugins" / "rigger-core" / "a.md").write_text("run `rigger-core:worker` first\n", encoding="utf-8")
            (root / "b.md").write_text("line one\nrun `%s` now\n" % planted, encoding="utf-8")
            (root / "c.md").write_text("see plugins/rigger-" + "gone/x\n", encoding="utf-8")
            import subprocess
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
            subprocess.run(["git", "add", "-A"], cwd=folder, check=True)
            read, found = findings(root, plugin_names(root))
        self.assertEqual(read, 3)
        self.assertEqual(sorted(found), ["b.md:2: " + planted, "c.md:1: plugins/rigger-" + "gone"])


if __name__ == "__main__":
    unittest.main()
