"""Checks for state.py: item edits that keep every other byte of the state file, the log of a done item,
refusals, the bounded `check`, the folder `clean` (idle rule, live and current sessions, the lock, the
60-day rule, log rotation), the restore at resume, the `hook` dispatch, and the CLI run by path."""

import calendar
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "state.py")

DOT, DASH = chr(0xB7), chr(0x2014)
SEP = " %s " % DOT
DAY = 86400
NOW = "2031-06-01T12:00:00Z"   # a day the real clock cannot reach while these tests run
NOW_TS = calendar.timegm((2031, 6, 1, 12, 0, 0))
CURRENT = "11111111-1111-4111-8111-111111111111"
DEAD = "22222222-2222-4222-8222-222222222222"
LIVE = "33333333-3333-4333-8333-333333333333"
NO_TRANSCRIPT = "44444444-4444-4444-8444-444444444444"
SIBLING = "55555555-5555-4555-8555-555555555555"
EDGE_IN = "66666666-6666-4666-8666-666666666666"
EDGE_OUT = "77777777-7777-4777-8777-777777777777"
OTHER = "88888888-8888-4888-8888-888888888888"
MERGED = {("owner/name", "12"), ("o/a", "12")}
ZAZOLC = "Za" + chr(0x17C) + chr(0xF3) + chr(0x142) + chr(0x107)

STUB_GH = '''import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
number = args[args.index("view") + 1]
repo = args[args.index("--repo") + 1]
with open(os.path.join(here, "calls.log"), "a", encoding="utf-8") as log:
    log.write("%s#%s\\n" % (repo, number))
sleep = os.path.join(here, "sleep")
if os.path.exists(sleep):
    time.sleep(float(open(sleep, encoding="utf-8").read()))
merged = {tuple(pair) for pair in json.load(open(os.path.join(here, "merged.json"), encoding="utf-8"))}
print(json.dumps({"state": "MERGED" if (repo, number) in merged else "OPEN"}))
'''


def load_module():
    spec = importlib.util.spec_from_file_location("state_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


st = load_module()


def item(ident, status="open", pr=DASH, branch=DASH, repo=DASH, title="t"):
    return "- " + SEP.join([ident, status, pr, branch, repo, title])


def fixture_git(cwd, *args):
    """git for building fixture repositories: no GIT_* from the caller, no system or global config."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    empty = os.path.join(cwd, os.pardir, "empty.gitconfig")
    open(empty, "wb").close()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.abspath(empty))
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t"] + list(args), cwd=cwd,
                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="state-test-")
        self.config = os.path.join(self.tmp, "config")
        self.state = os.path.join(self.config, "state")
        self.archive = os.path.join(self.state, "archive")
        self.projects = os.path.join(self.config, "projects")
        os.makedirs(self.state)
        os.makedirs(self.projects)
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *argv, stdin=b"", session=CURRENT):
        out = io.BytesIO()
        environ = {"CLAUDE_CODE_SESSION_ID": session} if session else {}
        code = st.main(["--state-dir", self.state, "--projects-dir", self.projects] + list(argv),
                       environ=environ, stdin=stdin, out=out)
        return code, out.getvalue().decode("utf-8")

    def put(self, name, data, age_days=None, folder=None):
        path = os.path.join(folder or self.state, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        if age_days is not None:
            moment = NOW_TS - age_days * DAY
            os.utime(path, (moment, moment))
        return path

    def read(self, name, folder=None):
        with open(os.path.join(folder or self.state, name), "rb") as handle:
            return handle.read()

    def listing(self, folder):
        return sorted(name for name in os.listdir(folder) if os.path.isfile(os.path.join(folder, name)))

    def state_file(self, items, frontmatter="safe_to_compact: true\nupdated: 2031-06-01T11:59:00Z\n"):
        text = "---\n%s---\n## Next step\nwork\n\n## Items\n%s\n## Keep verbatim\nx\n" % (
            frontmatter, "".join(line + "\n" for line in items))
        return self.put(CURRENT + ".md", text)

    def stub_gh(self, sleep=None):
        folder = os.path.join(self.tmp, "gh")
        os.makedirs(folder, exist_ok=True)
        self.put("gh_stub.py", STUB_GH, folder=folder)
        self.put("merged.json", json.dumps(sorted(MERGED)), folder=folder)
        if sleep is not None:
            self.put("sleep", str(sleep), folder=folder)
        self.gh_log = os.path.join(folder, "calls.log")
        return mock.patch.object(st, "gh_command", return_value=[sys.executable, "-I",
                                                                 os.path.join(folder, "gh_stub.py")])

    def gh_calls(self):
        if not os.path.exists(self.gh_log):
            return []
        with open(self.gh_log, encoding="utf-8") as handle:
            return handle.read().split()

    def make_repo(self, origin=None):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        fixture_git(repo, "init", "-q", "-b", "main")
        if origin:
            fixture_git(repo, "remote", "add", "origin", origin)
        fixture_git(repo, "commit", "-q", "--allow-empty", "-m", "base")
        fixture_git(repo, "branch", "feature/kept")
        fixture_git(repo, "update-ref", "refs/remotes/origin/remote-only", "HEAD")
        os.chdir(repo)
        return repo

    def dead_session(self, ident=DEAD, age=20):
        for suffix in (".md", ".log.md", ".parts.json"):
            self.put(ident + suffix, "x", age)
        self.put(ident + ".jsonl", "{}", age, folder=os.path.join(self.projects, "p1"))

    def report_clean(self, text):
        found = re.search(r"moved (\d+) deleted (\d+)", text)
        if found:
            sys.stderr.write("# clean: moved %s, deleted %s\n" % found.groups())


class ItemEdits(Case):
    def test_add_set_note_done_round_trip_keeps_other_sections_byte_for_byte(self):
        prefix = (b"\xef\xbb\xbf---\r\nsafe_to_compact: true\r\nupdated: 2031-06-01T11:59:00Z\r\n---\r\n"
                  b"## Next step\r\nCaf\xc3\xa9 \xe2\x80\x94 go on\r\n\r\n## Items\r\n")
        old = (item("old-one", title=ZAZOLC) + "\r\n  detail kept\r\n").encode("utf-8")
        suffix = b"\r\n## Keep verbatim\nLF line \xff raw\n  lone\rCR\nno newline at the end"
        self.put(CURRENT + ".md", prefix + old + suffix)
        steps = [("add", "new-item", "New thing", "--branch", "feature/x", "--repo", "owner/name"),
                 ("set", "old-one", "doing", "--pr", "7"),
                 ("note", "new-item", "a note"),
                 ("done", "new-item", "--pr", "9")]
        for argv in steps:
            code, text = self.run_main(*argv)
            self.assertEqual((code, text.splitlines()[-1].split(" ")[:3]), (0, ["state:", "ok", argv[0]]), text)
        expected = prefix + (item("old-one", "doing", "#7", title=ZAZOLC) + "\r\n  detail kept\r\n"
                             + item("new-item", "done", "#9", "feature/x", "owner/name", "New thing")
                             + "\r\n").encode("utf-8") + suffix
        self.assertEqual(self.read(CURRENT + ".md"), expected)

    def test_done_moves_detail_to_the_log(self):
        self.state_file([item("a", "doing", branch="feature/a"), "  first detail", "  second detail",
                         item("b")])
        self.put(CURRENT + ".log.md", "# earlier history\n")
        self.assertEqual(self.run_main("done", "a", "--pr", "3"), (0, "state: ok done a\n"))
        text = self.read(CURRENT + ".md").decode("utf-8")
        self.assertIn("## Items\n%s\n%s\n\n## Keep" % (item("a", "done", "#3", "feature/a"), item("b")), text)
        self.assertNotIn("detail", text)
        log = self.read(CURRENT + ".log.md").decode("utf-8")
        self.assertRegex(log, r"^# earlier history\n\n## a \([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z\)\n"
                              r"  first detail\n  second detail\n$")
        self.assertTrue(log.startswith("# earlier") and log.endswith("second detail\n"))

    def test_show_lists_the_items(self):
        self.state_file([item("a", title=ZAZOLC), "  detail", item("b", "blocked")])
        self.assertEqual(self.run_main("show"),
                         (0, "%s\n%s\nstate: ok show 2\n" % (item("a", title=ZAZOLC), item("b", "blocked"))))

    def test_refusals(self):
        rows = [
            (("add", "Bad_Id", "t"), "bad id"),
            (("add", "a" * 41, "t"), "bad id"),
            (("add", "a", "again"), "id exists"),
            (("add", "b", "t", "--repo", "not a repo"), "bad repo"),
            (("add", "b", "t", "--branch", "two words"), "bad branch"),
            (("add", "b", "line\n## Next step"), "control character"),
            (("note", "a", "x\r## Items"), "control character"),
            (("set", "nope", "open"), "unknown id"),
            (("note", "nope", "x"), "unknown id"),
            (("set", "a", "closed"), "unknown status"),
            (("set", "a", "done"), "use done"),
            (("set", "a", "open", "--pr", "x1"), "bad pr"),
            (("done", "a"), "done needs --pr"),
            (("frobnicate",), "unknown command"),
            (("show", "--colour", "x"), "unknown option"),
        ]
        path = self.state_file([item("a")])
        before = self.read(CURRENT + ".md")
        for argv, reason in rows:
            with self.subTest(argv=argv):
                code, text = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertEqual(text.count("\n"), 1, text)
                self.assertTrue(text.startswith("state: refused: " + reason), text)
                self.assertEqual(self.read(CURRENT + ".md"), before)
        self.put(CURRENT + ".md", before + b"\n## Items\n" + item("z").encode("utf-8") + b"\n")
        code, text = self.run_main("add", "c", "t")
        self.assertEqual((code, text), (2, "state: refused: two ## Items sections\n"))
        os.remove(path)
        self.assertTrue(self.run_main("add", "c", "t")[1].startswith("state: refused: no state file"))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.run_main("show", session=None), (2, "state: refused: no session id\n"))
        self.assertEqual(self.run_main("show", session="../x"), (2, "state: refused: bad session id\n"))

    def test_writes_are_atomic(self):
        self.state_file([item("a")])
        before = self.read(CURRENT + ".md")
        with mock.patch.object(st.os, "replace", side_effect=OSError("disk full")):
            code, text = self.run_main("add", "b", "t")
        self.assertEqual(code, 2, text)
        self.assertEqual(self.read(CURRENT + ".md"), before)
        self.assertEqual(os.listdir(self.state), [CURRENT + ".md"])

    def test_add_records_the_origin_repository(self):
        self.make_repo("git" + "@example.invalid:someone/project.git")   # the scp form, split so no sweep reads a mail
        self.state_file([])
        self.assertEqual(self.run_main("add", "a", "t")[0], 0)
        self.assertIn(item("a", repo="someone/project"), self.read(CURRENT + ".md").decode("utf-8"))


class Check(Case):
    def test_check_flags_merged_pr_deleted_branch_and_stale_false(self):
        self.make_repo("https://example.invalid/owner/name.git")
        items = [item("m1", "doing", "#12", repo="owner/name"), item("o1", "doing", "#13", repo="owner/name"),
                 item("g1", "doing", branch="gone-branch"), item("k1", branch="feature/kept"),
                 item("r1", "blocked", branch="remote-only"), item("d1", "done", "#12", "gone-too", "owner/name"),
                 item("x1", branch="elsewhere", repo="other/repo")]
        stale = "safe_to_compact: false\nupdated: 2031-06-01T11:29:00Z\n"
        self.state_file(items, stale)
        with self.stub_gh():
            code, text = self.run_main("check", "--now", NOW)
        self.assertEqual(code, 0, text)
        lines = text.splitlines()
        self.assertEqual(lines[-1], "state: ok check 3")
        self.assertEqual(sorted(re.match(r"state: stale (\S+):", line).group(1) for line in lines[:-1]
                                if not line.startswith("state: stale: ")), ["g1", "m1"], text)
        self.assertEqual(len([line for line in lines if line.startswith("state: stale: ")]), 1, text)
        self.assertTrue(any("PR #12" in line and "merged" in line for line in lines), text)
        self.assertTrue(any("gone-branch" in line for line in lines), text)
        self.assertTrue(any("safe_to_compact: false" in line for line in lines), text)
        clean = [line for line in items if not line.startswith(("- m1", "- g1"))]
        for frontmatter in ("safe_to_compact: true\nupdated: 2031-06-01T10:00:00Z\n",
                            "safe_to_compact: false\nupdated: 2031-06-01T11:31:00Z\n"):
            with self.subTest(frontmatter=frontmatter):
                self.state_file(clean, frontmatter)
                with self.stub_gh():
                    self.assertEqual(self.run_main("check", "--now", NOW), (0, "state: ok check 0\n"))
        path = self.state_file(clean, "safe_to_compact: false\n")
        os.utime(path, (NOW_TS - 31 * 60, NOW_TS - 31 * 60))
        with self.stub_gh():
            code, text = self.run_main("check", "--now", NOW)
        self.assertEqual(text.splitlines()[-1], "state: ok check 1", text)

    def test_check_is_bounded_and_offline_safe(self):
        self.state_file([item("p%d" % number, "doing", "#%d" % number, repo="owner/name")
                         for number in (21, 22, 23)])
        with self.stub_gh(sleep=10):
            started = time.monotonic()
            code, text = self.run_main("check", "--now", NOW)
            elapsed = time.monotonic() - started
        self.assertEqual(code, 0, text)
        self.assertLess(elapsed, 7.0, text)
        self.assertEqual(len(self.gh_calls()), 2, self.gh_calls())   # 3 s for the first, the 2 s left for the second
        unchecked = [line for line in text.splitlines() if line.startswith("state: unchecked")]
        self.assertEqual(len(unchecked), 1, text)
        self.assertIn("owner/name#21", unchecked[0])   # the first call that timed out is the one named
        with mock.patch.object(st, "gh_command", return_value=None):
            code, text = self.run_main("check", "--now", NOW)
        self.assertEqual((code, text), (0, "state: unchecked: gh not found\nstate: ok check 0\n"))

    def test_pr_cache_is_keyed_by_repository(self):
        self.state_file([item("a", "doing", "#12", repo="o/a"), item("b", "doing", "#12", repo="o/b")])
        with self.stub_gh():
            first = self.run_main("check", "--now", NOW)[1]
            second = self.run_main("check", "--now", NOW)[1]
        for text in (first, second):
            self.assertEqual(text.splitlines()[-1], "state: ok check 1", text)
            self.assertIn("stale a:", text)
        with open(os.path.join(self.state, "pr-cache.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["merged"], ["o/a#12"])
        self.assertEqual(self.gh_calls(), ["o/a#12", "o/b#12", "o/b#12"])

    def test_gh_is_not_taken_from_the_cwd(self):
        name = "gh.exe" if os.name == "nt" else "gh"
        here, other = os.path.join(self.tmp, "here"), os.path.join(self.tmp, "bin")
        for folder in (here, other):
            os.chmod(self.put(name, b"x", folder=folder), 0o755)
        os.chdir(here)
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([here, ".", other])}):
            found = st.gh_command(here)
        self.assertEqual([os.path.normcase(part) for part in found or []],[os.path.normcase(os.path.join(other, name))])


class Clean(Case):
    def test_clean_moves_idle_sessions_and_deletes_after_sixty_days(self):
        self.dead_session()
        self.put(NO_TRANSCRIPT + ".md", "x", 20)
        self.put(EDGE_IN + ".md", "x", 13.5)
        self.put(EDGE_OUT + ".md", "x", 14.5)
        for name in ("compact-guard.log", "pr-cache.json", "notes.txt"):
            self.put(name, "x", 100)
        self.put(OTHER + ".md", "x", 70, folder=self.archive)
        self.put(OTHER + ".log.md", "x", 59.5, folder=self.archive)
        self.put(OTHER + ".parts.json", "x", 60.5, folder=self.archive)
        code, text = self.run_main("clean", "--now", NOW)
        self.report_clean(text)
        self.assertEqual((code, text), (0, "state: ok clean moved 5 deleted 2 rotated 0\n"))
        self.assertEqual(self.listing(self.state), sorted([EDGE_IN + ".md", "compact-guard.log", "notes.txt",
                                                           "pr-cache.json"]))
        self.assertEqual(self.listing(self.archive), sorted([DEAD + ".md", DEAD + ".log.md", DEAD + ".parts.json",
                                                             NO_TRANSCRIPT + ".md", EDGE_OUT + ".md",
                                                             OTHER + ".log.md"]))

    def test_archive_resets_the_clock(self):
        self.dead_session()
        self.assertEqual(self.run_main("clean", "--now", NOW)[0], 0)
        moved = os.path.join(self.archive, DEAD + ".md")
        self.assertTrue(os.path.isfile(moved))
        self.assertEqual(os.stat(moved).st_mtime, NOW_TS)
        for days, left in ((59.5, 3), (60.5, 0)):
            later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW_TS + days * DAY))
            code, text = self.run_main("clean", "--now", later)
            self.report_clean(text)
            self.assertEqual(len(self.listing(self.archive)), left, text)

    def test_clean_never_touches_a_live_session(self):
        self.put(LIVE + ".md", "x", 20)
        self.put(LIVE + ".jsonl", "{}", 20, folder=os.path.join(self.projects, "p1"))
        self.put(LIVE + ".jsonl", "{}", 1, folder=os.path.join(self.projects, "p2"))
        self.put(SIBLING + ".md", "x", 20)
        self.put(SIBLING + ".side-guard.json", "{}", 1)
        self.dead_session()
        code, text = self.run_main("clean", "--now", NOW)
        self.report_clean(text)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.listing(self.state), sorted([LIVE + ".md", SIBLING + ".md",
                                                           SIBLING + ".side-guard.json"]))

    def test_clean_never_touches_the_current_session(self):
        for suffix in (".md", ".log.md", ".turn-guard.json"):
            self.put(CURRENT + suffix, "x", 20)
        self.dead_session()
        self.assertEqual(self.run_main("clean", "--now", NOW)[0], 0)
        self.assertEqual(self.listing(self.state), sorted(CURRENT + suffix for suffix in (".md", ".log.md",
                                                                                         ".turn-guard.json")))
        self.dead_session()
        self.assertEqual(self.run_main("clean", "--now", NOW, session=None), (2, "state: refused: no session id\n"))
        self.assertIn(DEAD + ".md", self.listing(self.state))

    def test_clean_never_overwrites_an_archived_file(self):
        self.dead_session()
        self.put(DEAD + ".md", "archived earlier", 1, folder=self.archive)
        self.assertEqual(self.run_main("clean", "--now", NOW), (0, "state: ok clean moved 0 deleted 0 rotated 0\n"))
        self.assertEqual(self.read(DEAD + ".md", folder=self.archive), b"archived earlier")
        self.assertEqual(self.listing(self.state), sorted(DEAD + suffix for suffix in (".log.md", ".md", ".parts.json")))

    def test_clean_skips_while_another_holds_the_lock(self):
        self.dead_session()
        lock = self.put(".clean.lock", "999 2031-06-01T11:55:00Z\n")
        self.assertEqual(self.run_main("clean", "--now", NOW), (0, "state: ok clean skipped: locked\n"))
        self.assertIn(DEAD + ".md", self.listing(self.state))
        self.assertEqual(self.read(".clean.lock"), b"999 2031-06-01T11:55:00Z\n")
        self.put(".clean.lock", "999 2031-06-01T11:49:00Z\n")
        code, text = self.run_main("clean", "--now", NOW)
        self.assertEqual((code, text), (0, "state: ok clean moved 3 deleted 0 rotated 0\n"))
        self.assertFalse(os.path.exists(lock))

    def test_log_rotation_keeps_the_newest_lines(self):
        original = b"".join(b"line %05d of the log\n" % number for number in range(3000))   # 63,000 bytes
        self.put(OTHER + ".log.md", original, 1)
        small = self.put(SIBLING + ".log.md", b"y\n" * 20000, 1)   # 40,000 bytes
        code, text = self.run_main("clean", "--now", NOW)
        self.assertEqual((code, text), (0, "state: ok clean moved 0 deleted 0 rotated 1\n"))
        tail = self.read(OTHER + ".log.md")
        head = self.read(OTHER + ".log.20310601T120000Z.md", folder=self.archive)
        self.assertEqual(head + tail, original)
        self.assertTrue(head.endswith(b"\n") and tail.endswith(b"line 02999 of the log\n"))
        self.assertLessEqual(len(tail), 25 * 1024)
        self.assertGreater(len(tail), 25 * 1024 - 22)
        self.assertEqual(os.path.getsize(small), 40000)


class Hook(Case):
    def test_resume_restores_archived_files(self):
        self.put(CURRENT + ".md", "---\nsafe_to_compact: true\n---\n## Items\n", 30, folder=self.archive)
        self.put(CURRENT + ".log.md", "log", 30, folder=self.archive)
        self.put(CURRENT + ".log.20310101T000000Z.md", "old part", 30, folder=self.archive)
        self.put(OTHER + ".md", "x", 30, folder=self.archive)
        event = json.dumps({"source": "resume", "session_id": CURRENT}).encode("utf-8")
        code, text = self.run_main("hook", "--now", NOW, stdin=event, session=None)
        self.assertEqual(code, 0)
        self.assertEqual(text[:len("state: restored 2 ")], "state: restored 2 ", text)
        self.assertEqual(self.listing(self.state), [CURRENT + ".log.md", CURRENT + ".md"])
        self.assertEqual(self.listing(self.archive), sorted([CURRENT + ".log.20310101T000000Z.md", OTHER + ".md"]))
        # The archive time stays: compact_guard judges a coordinator file by its mtime, so a restored file must
        # not look freshly written.
        self.assertEqual(os.stat(os.path.join(self.state, CURRENT + ".md")).st_mtime, NOW_TS - 30 * DAY)
        self.put(CURRENT + ".parts.json", "archived", 30, folder=self.archive)
        self.put(CURRENT + ".parts.json", "current", 0)
        code, text = self.run_main("hook", "--now", NOW, stdin=event, session=None)
        self.assertEqual((code, text), (0, ""))
        self.assertEqual(self.read(CURRENT + ".parts.json"), b"current")
        self.assertEqual(self.read(CURRENT + ".parts.json", folder=self.archive), b"archived")

    def test_hook_runs_the_step_of_each_source(self):
        self.make_repo()
        gone = [item("g%d" % number, branch="gone-%d" % number) for number in range(7)]
        for source, moved, lines in (("startup", True, 1), ("clear", False, 6), ("compact", False, 6),
                                     ("resume", False, 6), ("other", False, 0)):
            with self.subTest(source=source):
                self.dead_session()
                self.state_file(gone)
                event = json.dumps({"source": source, "session_id": CURRENT}).encode("utf-8")
                with mock.patch.object(st, "gh_command", return_value=None):
                    code, text = self.run_main("hook", "--now", NOW, stdin=event)
                self.assertEqual(code, 0)
                self.assertEqual(len(text.splitlines()), lines, text)
                self.assertEqual(DEAD + ".md" not in self.listing(self.state), moved, text)
                if lines == 6:
                    self.assertEqual(text.splitlines()[-1], "state: 7 findings, 5 shown", text)
                for name in self.listing(self.archive) if os.path.isdir(self.archive) else []:
                    os.remove(os.path.join(self.archive, name))

    def test_hook_survives_wrong_stdin(self):
        self.dead_session()
        for stdin in (b"", b"not json", b"[]", b'"startup"', b'{"source": 5}', b'{"source": ["startup"]}',
                      b'{"source": "startup", "session_id": "../x"}', b'{"source": "startup", "session_id": 5}',
                      b"\xff\xfe"):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_main("hook", "--now", NOW, stdin=stdin), (0, ""))
                self.assertIn(DEAD + ".md", self.listing(self.state))


class Cli(unittest.TestCase):
    def test_cli_runs_by_path_with_lf_output(self):
        tmp = tempfile.mkdtemp(prefix="state-cli-")
        try:
            with open(os.path.join(tmp, CURRENT + ".md"), "wb") as handle:
                handle.write(("## Items\n%s\n" % item("a", title=ZAZOLC)).encode("utf-8"))
            env = {key: value for key, value in os.environ.items() if not key.startswith(("CLAUDE_", "PYTHON"))}
            env["CLAUDE_CODE_SESSION_ID"] = CURRENT
            run = [sys.executable, "-I", SCRIPT, "--state-dir", tmp, "--projects-dir", tmp]
            shown = subprocess.run(run + ["show"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual((shown.returncode, shown.stdout, shown.stderr),
                             (0, ("%s\nstate: ok show 1\n" % item("a", title=ZAZOLC)).encode("utf-8"), b""))
            refused = subprocess.run(run + ["set", "a", "nope"], env=env, stdout=subprocess.PIPE, timeout=60)
            self.assertEqual((refused.returncode, refused.stdout), (2, b"state: refused: unknown status nope\n"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
