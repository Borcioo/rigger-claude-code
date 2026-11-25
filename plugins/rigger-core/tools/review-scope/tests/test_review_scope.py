"""review_scope: the program lookup that keeps a planted git from running.

python -I -X utf8 test_review_scope.py
"""
import contextlib
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from review_scope_case import (  # noqa: E402
    EXPECTED,
    REAL_RUN,
    GitCase,
    review_scope,
)


EXE = ".exe" if os.name == "nt" else ""


def ctypes_volume_name(mount):
    """The `\\\\?\\Volume{...}\\` name of the volume mounted at `mount` (Windows)."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(mount, buffer, len(buffer)):
        raise OSError("no volume name for %s" % mount)
    return buffer.value


def admin_share(case, path):
    """`path` spelled through the administrative share of its drive on this machine; the test is skipped when
    the share cannot be opened, and fails instead when CI is set, so a CI runner cannot skip it unseen."""
    drive, rest = os.path.splitdrive(os.path.abspath(str(path)))
    share = "\\\\localhost\\%s$%s" % (drive[:1], rest)
    probe = "\\\\localhost\\%s$\\" % drive[:1]
    if not os.path.isdir(probe):
        reason = "the admin share %s cannot be opened" % probe
        if os.environ.get("CI"):
            case.fail(reason)
        case.skipTest(reason)
    return share


@contextlib.contextmanager
def working_directory(path):
    before = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(before)


class ProgramLookupTests(GitCase):
    def setUp(self):
        super().setUp()
        self.real_path = os.environ.get("PATH", "")
        self.good = self.tmp / "good"
        self.tool = self.program(self.good, "tool")
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()

    def program(self, folder, name, ext=EXE, executable=True):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name + ext)
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(str(path), 0o755 if executable else 0o644)
        return path

    def find(self, entries, name="tool", cwd=None, exts=(EXE,)):
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}):
            with working_directory(cwd or self.elsewhere):
                return review_scope.find_program(name, self.repo, exts)

    def same(self, a, b):
        self.assertEqual(os.path.normcase(os.path.abspath(str(a))), os.path.normcase(os.path.abspath(str(b))))

    def test_lookup_skips_every_unsafe_entry(self):
        sibling = Path(str(self.repo) + "-tools")
        self.program(self.repo, "tool")
        self.program(self.repo / "bin", "tool")
        self.program(self.elsewhere, "tool")
        self.program(self.elsewhere / "bin", "tool")
        self.program(self.tmp / "rel", "tool")
        (self.tmp / "dirs" / ("tool" + EXE)).mkdir(parents=True)
        self.program(self.tmp / "noexec", "tool", executable=False)
        nested_cwd = self.elsewhere / "cwd"
        nested_cwd.mkdir()
        # (row, the unsafe entry put before the good one, the working directory)
        rows = [
            ("the repository itself", str(self.repo), self.elsewhere),
            ("a folder inside the repository", str(self.repo / "bin"), self.elsewhere),
            ("the working directory", str(self.elsewhere), self.elsewhere),
            ("a folder inside the working directory", str(self.elsewhere / "bin"), self.elsewhere),
            # Resolves to <tmp>/rel, outside both: only the absolute-entry rule skips it.
            ("a relative entry", os.path.join("..", "..", "rel"), nested_cwd),
            ("an empty entry", "", self.elsewhere),
            ("a folder holding a directory named like the program", str(self.tmp / "dirs"), self.elsewhere),
        ]
        if os.name == "nt":
            self.program(self.tmp / "rootrel", "tool")
            rows += [
                ("the repository spelled in another case", str(self.repo).swapcase(), self.elsewhere),
                ("the repository with the extended prefix", "\\\\?\\" + str(self.repo), self.elsewhere),
                ("a folder inside the repository with the extended prefix in forward slashes",
                 "//?/" + str(self.repo / "bin").replace("\\", "/"), self.elsewhere),
                ("the repository with the device prefix", "\\\\.\\" + str(self.repo), self.elsewhere),
                # Outside both, but read against another drive's root by a child on another drive.
                ("a root-relative entry", os.path.splitdrive(str(self.tmp / "rootrel"))[1], self.elsewhere),
                ("a drive-relative entry", os.path.splitdrive(str(self.tmp))[0] + os.path.join("..", "..", "rel"),
                 nested_cwd),
            ]
        else:
            rows.append(("a file that is not executable", str(self.tmp / "noexec"), self.elsewhere))
        for label, unsafe, cwd in rows:
            with self.subTest(row=label):
                self.same(self.find([unsafe, str(self.good)], cwd=cwd), self.tool)
        # Not over-excluded: a folder whose name only starts with the repository's is outside it.
        sibling_tool = self.program(sibling, "tool")
        self.same(self.find([str(sibling), str(self.good)]), sibling_tool)

    def link(self, link, target):
        """A junction on Windows, a symlink on POSIX: `link` leads to the folder `target`."""
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(str(target), str(link))
        return link

    def test_lookup_resolves_links_on_each_side(self):
        """An entry, the repository or the working directory spelled through a link is compared by its real path."""
        self.program(self.repo / "bin", "tool")
        self.program(self.elsewhere / "bin", "tool")
        repo_link = self.link(self.tmp / "repo-link", self.repo)
        cwd_link = self.link(self.tmp / "cwd-link", self.elsewhere)
        into_repo = self.link(self.tmp / "into-repo", self.repo / "bin")
        into_cwd = self.link(self.tmp / "into-cwd", self.elsewhere / "bin")
        # (row, the unsafe entry put before the good one, the repository as given, the working directory)
        rows = [
            ("an entry linked into the repository", str(into_repo), self.repo, self.elsewhere),
            ("the repository given through a link", str(self.repo / "bin"), repo_link, self.elsewhere),
            ("an entry linked into the working directory", str(into_cwd), self.repo, self.elsewhere),
            ("the working directory entered through a link", str(self.elsewhere / "bin"), self.repo, cwd_link),
        ]
        for label, unsafe, repo, cwd in rows:
            with self.subTest(row=label):
                with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([unsafe, str(self.good)])}):
                    with working_directory(cwd):
                        self.same(review_scope.find_program("tool", repo, (EXE,)), self.tool)

    def test_an_entry_spelled_inside_through_a_link_that_leads_outside_is_not_searched(self):
        """The repository or the working directory can point its link elsewhere at any time: an entry spelled
        inside either is refused by its spelling, though its real path and every folder above it are outside."""
        self.program(self.repo / "bin", "tool")
        rows = [
            ("a link in the repository", self.link(self.repo / "out-link", self.good)),
            ("a link in the working directory", self.link(self.elsewhere / "out-link", self.good)),
        ]
        for label, entry in rows:
            with self.subTest(row=label):
                self.same(os.path.realpath(str(entry)), os.path.realpath(str(self.good)))
                self.assertIs(review_scope.searchable(str(entry), self.repo, str(self.elsewhere)), False)
        # Not over-excluded: the folder the links lead to, spelled as itself, is still searched.
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)

    @unittest.skipUnless(os.name == "nt", "the extended and device prefixes are Windows spellings")
    def test_a_prefixed_entry_is_read_without_its_prefix(self):
        """A prefixed entry outside both is still searched; one the prefix cannot be removed from is not."""
        good = str(self.good)
        volume = ctypes_volume_name(os.path.splitdrive(good)[0] + "\\")
        rows = [  # (row, entry, searchable)
            ("the extended prefix", "\\\\?\\" + good, True),
            ("the extended prefix in forward slashes", "//?/" + good.replace("\\", "/"), True),
            ("the device prefix", "\\\\.\\" + good, True),
            ("a doubled prefix", "\\\\?\\\\\\?\\" + good, False),
            ("the extended UNC prefix", "\\\\?\\UNC\\localhost\\share\\bin", True),
            ("the device UNC prefix", "\\\\.\\UNC\\localhost\\share\\bin", True),
            ("a volume name", volume + os.path.splitdrive(good)[1].lstrip("\\"), False),
            ("a device with no drive", "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\bin", False),
        ]
        for label, entry, expected in rows:
            with self.subTest(row=label, entry=entry):
                self.assertIs(review_scope.searchable(entry, self.repo, str(self.elsewhere)), expected)

    def case_twin(self, folder):
        """`folder` with its last name in the other case, when the file system reads it as the same folder."""
        twin = folder.parent / folder.name.swapcase()
        if not twin.is_dir() or not os.path.samefile(str(twin), str(folder)):
            reason = "case-sensitive file system at %s (on WSL set TMPDIR to a /mnt/<drive> folder)" % folder
            if sys.platform == "darwin":  # APFS is case-insensitive by default: a skip here would hide the proof
                self.fail(reason)
            self.skipTest(reason)
        return twin

    @unittest.skipUnless(os.name == "nt", "the admin share is a Windows spelling")
    def test_admin_share_entry_is_not_searched(self):
        """The repository or the working directory spelled through the `\\\\localhost\\<drive>$` share is the same
        folder by file identity, though no spelling or real path matches."""
        (self.repo / "bin").mkdir()
        (self.elsewhere / "bin").mkdir()
        rows = [
            ("the repository", admin_share(self, self.repo)),
            ("a folder inside the repository", admin_share(self, self.repo / "bin")),
            ("a missing folder inside the repository", os.path.join(admin_share(self, self.repo), "nope", "deeper")),
            ("the working directory", admin_share(self, self.elsewhere)),
            ("a folder inside the working directory", admin_share(self, self.elsewhere / "bin")),
        ]
        for label, unsafe in rows:
            with self.subTest(row=label):
                self.assertIs(review_scope.searchable(unsafe, self.repo, str(self.elsewhere)), False)
        # Not over-excluded: a folder outside both, spelled through the share, is still searched.
        self.assertIs(review_scope.searchable(admin_share(self, self.good), self.repo, str(self.elsewhere)), True)

    def test_lookup_skips_the_repository_and_the_cwd_spelled_in_another_case(self):
        """On a case-insensitive file system (macOS by default, WSL's /mnt/<drive>, Windows) another spelling of
        the repository or the working directory is the same folder."""
        (self.repo / "bin").mkdir()
        (self.elsewhere / "bin").mkdir()
        repo_twin = self.case_twin(self.repo)
        cwd_twin = self.case_twin(self.elsewhere)
        rows = [
            ("the repository", str(repo_twin)),
            ("a folder inside the repository", str(repo_twin / "bin")),
            ("a missing folder inside the repository", str(repo_twin / "nope" / "deeper")),
            ("the working directory", str(cwd_twin)),
            ("a folder inside the working directory", str(cwd_twin / "bin")),
        ]
        for label, unsafe in rows:
            with self.subTest(row=label):
                self.assertIs(review_scope.searchable(unsafe, self.repo, str(self.elsewhere)), False)
        # Not over-excluded: an existing folder beside them, outside both, is still searched.
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)

    def test_no_entry_is_searched_when_the_working_directory_cannot_be_read(self):
        """Without the folder's identity another spelling of it cannot be ruled out."""
        missing = self.tmp / "gone"
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(missing)), False)

    @unittest.skipUnless(os.name == "nt", "batch files exist on Windows only")
    def test_git_lookup_never_takes_a_batch_file(self):
        batch = self.tmp / "batch"
        self.program(batch, "git", ".cmd")
        self.program(batch, "git", ".bat")
        real = self.program(self.good, "git")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([str(batch), str(self.good)])}):
            with working_directory(self.elsewhere):
                self.same(review_scope.find_git(self.repo), real)

    def test_lookup_refusals(self):
        with self.assertRaises(review_scope.ScopeError) as caught:
            self.find([str(self.repo), str(self.elsewhere)])
        self.assertIn("tool not found in an absolute PATH entry outside the repository and the working directory",
                      str(caught.exception))
        for name in ("", "a/tool", "a\\tool"):
            with self.subTest(name=name):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    self.find([str(self.good)], name=name)
                self.assertIn("not a bare program name", str(caught.exception))
        for exts in ((".exe", ".cmd"), (".BAT",)):
            with self.subTest(exts=exts):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    self.find([str(self.good)], exts=exts)
                self.assertIn("a batch file is never run", str(caught.exception))

    def test_a_planted_git_is_never_run(self):
        base, head = self.two_commits()
        real_git = shutil.which("git", path=self.real_path)
        self.assertTrue(real_git and os.path.isabs(real_git))
        marker = self.tmp / "plant-marker"
        if os.name == "nt":
            plant = self.repo / "git.exe"
            shutil.copy2(sys.executable, str(plant))
            for dll in Path(os.path.dirname(sys.executable)).glob("*.dll"):
                shutil.copy2(str(dll), str(self.repo / dll.name))
        else:
            plant = self.repo / "git"
            plant.write_bytes(("#!/bin/sh\necho ran > '%s'\necho planted\nexit 1\n" % marker.as_posix()).encode("utf-8"))
            os.chmod(str(plant), 0o755)
        planted_path = os.pathsep.join([str(self.repo), self.real_path])
        # Control: an ordinary lookup over this PATH finds the plant, and it runs.
        naive = shutil.which("git", path=planted_path)
        self.same(naive, plant)
        ran = REAL_RUN([naive, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn(b"Python" if os.name == "nt" else b"planted", ran.stdout)
        if marker.exists():
            marker.unlink()
        for cwd, path in ((self.repo, self.real_path), (self.elsewhere, planted_path), (self.repo, "." + os.pathsep + self.real_path)):
            with self.subTest(cwd=cwd.name, path=path.split(os.pathsep)[0]):
                with mock.patch.dict(os.environ, {"PATH": path}), working_directory(cwd), self.git_calls() as calls:
                    self.assertEqual(review_scope.scope(self.repo, base, head), EXPECTED)
                self.assertTrue(calls)
                for argv, _env in calls:
                    self.same(argv[0], real_git)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
