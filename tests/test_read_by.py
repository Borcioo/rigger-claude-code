"""The read-by map (tests/read_by.json) against the reads a lint derives from the text of every test folder.

derived_reads applies D58's rules to every .py file under a folder that holds a discovered test file:
R1 `plugins/<module>`; R2 a quoted module name; R3 `plugins/*`, or `module_dirs(`, `iterdir(` or `glob(`
on a line that names `plugins` or `PLUGINS` (reads `*`); R4 a file inherits the reads of a helper it
imports from its own folder or from `tests/`. Every derived read of a discovered test file must be its
own module or covered by the map, as `run_tests.py --changed-modules` reads it.
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402
import select_tests  # noqa: E402

ALL = "*"
R3_CALLS = ("module_dirs(", "iterdir(", "glob(")


def own_module(rel):
    parts = rel.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "plugins" else None


def text_reads(text, modules):
    """R1-R3 on one file's text."""
    reads = set()
    if modules:
        names = "|".join(re.escape(name) for name in sorted(modules, key=len, reverse=True))
        reads.update(re.findall(r"plugins/(%s)(?![A-Za-z0-9_-])" % names, text))       # R1
        reads.update(match[1] for match in re.findall(r"([\"'])(%s)\1" % names, text))  # R2
    if "plugins/*" in text:                                                          # R3
        reads.add(ALL)
    for line in text.splitlines():
        if ("plugins" in line or "PLUGINS" in line) and any(call in line for call in R3_CALLS):
            reads.add(ALL)
    return reads


def test_folder_files(root):
    """Every .py file under a folder that holds a discovered .py test file, repository-relative."""
    folders = {path.parent for path in run_tests.discover(root) if path.suffix == ".py"}
    files = set()
    for folder in folders:
        for path in folder.rglob("*.py"):
            if "__pycache__" not in path.parts and path.is_file():
                files.add(path)
    return sorted(files)


def derived_reads(root):
    """Each .py file under a test folder to the modules its text reads, R4's inheritance applied."""
    root = Path(root)
    modules = set(run_tests.module_names(root))
    own = {}
    helpers = {}
    pending = list(test_folder_files(root))
    while pending:
        path = pending.pop()
        rel = path.relative_to(root).as_posix()
        if rel in own:
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        own[rel] = text_reads(text, modules)
        found = set()
        for name in select_tests.imported_modules(text):
            for folder in (path.parent, root / "tests"):
                helper = folder / (name + ".py")
                if helper != path and helper.is_file():
                    found.add(helper.relative_to(root).as_posix())
                    pending.append(helper)
        helpers[rel] = found
    reads = {rel: set(value) for rel, value in own.items()}
    grew = True
    while grew:                                                                      # R4, transitively
        grew = False
        for rel, used in helpers.items():
            for helper in used:
                extra = reads.get(helper, set()) - reads[rel]
                if extra:
                    reads[rel] |= extra
                    grew = True
    return reads


def declared(rel, readers):
    """What the mode lets `rel` read: its map entry, else `*` for a repo test, else its own module."""
    if rel in readers:
        return set(readers[rel])
    parts = rel.split("/")
    if len(parts) == 2 and parts[0] == "tests":
        return {ALL}
    return {own_module(rel)}


def undeclared(root):
    """(test files checked, cross-module reads, [(file, read) not covered by the map])."""
    readers = run_tests.read_by(root)
    reads = derived_reads(root)
    tests = [path.relative_to(root).as_posix() for path in run_tests.discover(root) if path.suffix == ".py"]
    count = 0
    missing = []
    for rel in tests:
        allowed = declared(rel, readers)
        for read in sorted(reads.get(rel, set()) - {own_module(rel)}):
            count += 1
            if ALL not in allowed and read not in allowed:
                missing.append((rel, read))
    return len(tests), count, missing


class ReadByTests(unittest.TestCase):
    def test_every_derived_read_is_declared(self):
        files, reads, missing = undeclared(ROOT)
        print("# read-by: files %d, reads %d, undeclared %d" % (files, reads, len(missing)))
        self.assertGreater(files, 0)
        self.assertEqual(missing, [])

    def test_the_known_readers_are_derived(self):
        reads = derived_reads(ROOT)
        rows = (
            ("plugins/rigger-core/tools/assemble/tests/test_stub_session.py", {"rigger-rules"}),
            ("plugins/rigger-core/tools/coord-state/tests/test_coordinate_host_stub.py", {"rigger-rules"}),
            ("plugins/rigger-core/tools/doctor/tests/test_doctor_sh_plugins.py",
             {"rigger-rules", "rigger-lessons", "rigger-windows"}),
            ("plugins/rigger-rules/tests/test_core_check.py", {"rigger-windows"}),
        )
        for rel, expected in rows:
            with self.subTest(rel):
                self.assertLessEqual(expected, reads[rel])

    def test_each_rule_flags_a_planted_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="read-by-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        files = {
            "plugins/m1/rigger-module.json": "{}\n",
            "plugins/m2/rigger-module.json": "{}\n",
            "plugins/m1/tests/test_r1.py": "X = ROOT / 'plugins/m2/tools'\n",
            "plugins/m1/tests/test_r2.py": "NAME = \"m2\"\n",
            "plugins/m1/tests/test_r3_star.py": "X = 'plugins/*/x'\n",
            "plugins/m1/tests/test_r3_call.py": "for p in (ROOT / 'plugins').iterdir():\n    pass\n",
            "plugins/m1/tests/test_r4.py": "import near_helper\n",
            "plugins/m1/tests/near_helper.py": "NAME = 'm2'\n",
            "plugins/m1/tests/test_r4_repo.py": "from repo_helper import x\n",
            "tests/repo_helper.py": "x = 'plugins/m2/y'\n",
            "plugins/m1/tests/test_none.py": "# m2 and plugins are words here\nimport os\nprint('m2x', ROOT.glob('*'))\n",
        }
        for rel, text in files.items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        reads = derived_reads(tmp)
        rows = {
            "plugins/m1/tests/test_r1.py": {"m2"},
            "plugins/m1/tests/test_r2.py": {"m2"},
            "plugins/m1/tests/test_r3_star.py": {ALL},
            "plugins/m1/tests/test_r3_call.py": {ALL},
            "plugins/m1/tests/test_r4.py": {"m2"},
            "plugins/m1/tests/test_r4_repo.py": {"m2"},
            "plugins/m1/tests/test_none.py": set(),
        }
        for rel, expected in rows.items():
            with self.subTest(rel):
                self.assertEqual(reads[rel], expected)

    def test_map_names_only_existing_files_and_modules(self):
        readers = run_tests.read_by(ROOT)
        found = {path.relative_to(ROOT).as_posix() for path in run_tests.discover(ROOT)}
        modules = set(run_tests.module_names(ROOT)) | {ALL}
        self.assertGreater(len(readers), 0)
        for rel, reads in readers.items():
            with self.subTest(rel):
                self.assertIn(rel, found)
                self.assertLessEqual(set(reads), modules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
