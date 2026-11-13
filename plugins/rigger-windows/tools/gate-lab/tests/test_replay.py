"""replay.py over a synthetic corpus whose fires are known and read back before each run.

python -I -X utf8 test_replay.py
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import lab_fixtures as fx  # noqa: E402
from lab_fixtures import SID_A, SID_B, SID_C, SID_D, SID_E, Session  # noqa: E402

import replay  # noqa: E402
import turn_evidence as te  # noqa: E402

SCRIPT = os.path.join(fx.LAB, "replay.py")
NOT_A_PATH = re.compile("(?!)")
LONG_LEAD = "Looked through the module first, nothing unusual there. " * 8


class Lab(unittest.TestCase):
    """In-process runs pin OUTSIDE to nothing, since a temp folder is itself outside; the CLI runs keep it."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="lab-crème-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.config = os.path.join(self.base, "config")
        self.data = os.path.join(self.base, "data")
        self.rows = os.path.join(self.base, "rows")
        for patcher in (mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.config}),
                        mock.patch.object(replay, "OUTSIDE", NOT_A_PATH)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.repo = fx.make_repo(self.base, "repo-a")
        self.other = fx.make_repo(self.base, "repo-b")

    def session(self, sid=SID_A, cwd=None, days_ago=2):
        return Session(self.config, "project-a", sid, cwd or self.repo, days_ago)

    def replay(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = replay.main(["--data", self.data] + list(args))
        return code, out.getvalue(), err.getvalue()

    def cli(self, *args):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
        done = subprocess.run([sys.executable, "-I", SCRIPT, "--data", self.data] + list(args), env=env, cwd=self.base,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        out = done.stdout.decode("utf-8").replace("\r\n", "\n")
        return done.returncode, out, done.stderr.decode("utf-8", "replace")

    def corpus_text(self):
        found = []
        for folder, _, names in os.walk(self.config):
            for name in names:
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    found.append(handle.read())
        return "".join(found)

    def shadow_summary(self, gate):
        with open(os.path.join(self.data, "gate-lab", "shadow-%s.json" % gate), encoding="utf-8") as handle:
            return json.load(handle)

    def commit_corpus(self):
        """Fires: 2 in the window. Allowed: tested, docs only, inline check, failed commit, dry run, ungated root."""
        session = self.session()
        session.prompt()
        session.edit("src/a.py")
        session.check()
        session.commit()
        session.edit("src/b.py")
        session.commit()
        session.edit("README.md")
        session.commit()
        session.edit("src/c.py")
        session.shell('pytest && git commit -m "change"', "12 passed")
        session.edit("src/d.py")
        session.commit(is_error=True)
        session.check()
        session.commit()
        session.edit("src/e.py")
        session.shell('git commit --dry-run -m "change"')
        session.check()
        session.commit()
        session.in_subagent("a1").edit("src/f.py")
        session.in_main().commit()
        session.save()
        ungated = self.session(SID_B, self.other)
        ungated.prompt()
        ungated.edit("src/a.py")
        ungated.commit()
        ungated.save()
        old = self.session(SID_C, days_ago=40)
        old.prompt()
        old.edit("src/old.py")
        old.commit()
        old.save()
        return session

    def stop_corpus(self):
        """Fires: 2. Quiet: negated claim, docs only, check after the edit, edit in an earlier turn, claim mid-text,
        ungated root."""
        session = self.commit_corpus()
        closing = self.session(SID_D, self.repo)
        closing.prompt()
        closing.edit("src/g.py")
        closing.say("Done.")
        closing.prompt()
        closing.edit("src/h.py")
        closing.say("Not done yet.")
        closing.prompt()
        closing.edit("docs/a.md")
        closing.say("Done.")
        closing.prompt()
        closing.edit("src/i.py")
        closing.check()
        closing.say("Done.")
        closing.prompt()
        closing.say("Done.")
        closing.prompt()
        closing.edit("src/j.py")
        closing.say("Waiting on the build.")
        closing.notification()
        closing.say("Fixed.")
        closing.prompt()
        closing.edit("src/k.py")
        closing.say(LONG_LEAD + "All of it is done now, says the log.\n\n" + "See the log for the rest.")
        closing.save()
        ungated = self.session(SID_E, self.other)
        ungated.prompt()
        ungated.edit("src/g.py")
        ungated.say("Done.")
        ungated.save()
        return closing

    def plant_commit(self):
        planted = self.session("dddddddd-4444-4444-8444-444444444444")
        planted.prompt()
        planted.edit("src/planted.py")
        planted.commit()
        planted.save()

    def plant_closing(self, text):
        planted = self.session("eeeeeeee-5555-4555-8555-555555555555")
        planted.prompt()
        planted.edit("src/planted.py")
        planted.say(text)
        planted.save()


class CommitReplay(Lab):
    def read_corpus(self):
        self.commit_corpus()
        back = fx.read_back(self.config)
        self.assertEqual((back["files"], back["Bash"], back["Edit"]), (4, 14, 9))

    def test_known_fires(self):
        self.read_corpus()
        code, out, err = self.replay("--gate", "commit")
        self.assertEqual(code, 0, err)
        self.assertIn("day %s 2\n" % fx.day(2), out)
        self.assertIn("fires 2\n", out)
        self.assertIn("# files read 4, sessions 3,", out)

    def test_prints_upper_bound(self):
        self.read_corpus()
        self.assertIn("upper bound", self.replay("--gate", "commit")[1])

    def test_window_keeps_the_older_fire_out(self):
        self.read_corpus()
        self.assertIn("fires 3\n", self.replay("--gate", "commit", "--days", "60")[1])

    def test_planted_commit_is_caught(self):
        self.read_corpus()
        self.plant_commit()
        self.assertIn("planted.py", self.corpus_text())
        self.assertIn("fires 3\n", self.replay("--gate", "commit")[1])

    def test_sample_takes_one_fire_per_session(self):
        self.read_corpus()
        self.plant_commit()
        out = self.replay("--gate", "commit")[1]
        self.assertEqual(len(re.findall(r"^sample [0-9a-f]{12}$", out, re.M)), 2)

    def test_session_past_the_window_is_not_read(self):
        self.read_corpus()
        stale = os.path.join(self.config, "projects", "project-a", SID_C + ".jsonl")
        os.utime(stale, (fx.NOW.timestamp() - 90 * 86400,) * 2)
        self.assertIn("# files read 3, sessions 2,", self.replay("--gate", "commit")[1])


class StopReplay(Lab):
    def read_corpus(self):
        self.stop_corpus()
        back = fx.read_back(self.config)
        self.assertEqual((back["files"], back["text"], back["Edit"]), (6, 9, 16))

    def test_known_fires(self):
        self.read_corpus()
        code, out, err = self.replay("--gate", "stop")
        self.assertEqual(code, 0, err)
        self.assertIn("fires 2\n", out)
        self.assertNotIn("upper bound", out)

    def test_task_notification_keeps_the_turn_edits(self):
        self.read_corpus()
        out = self.replay("--gate", "stop")[1]
        shown = [self.replay("--gate", "stop", "--show", name)[1] for name in re.findall(r"^sample (\S+)$", out, re.M)]
        self.assertEqual(sum("src/j.py" in text and "Fixed." in text for text in shown), 1)

    def test_planted_claim_and_its_paraphrases_are_caught(self):
        self.stop_corpus()
        for text in ("Done.", "Fixed the parser, tests passing.", "The feature is implemented.",
                     "Everything works now.", "I fixed the parser and the tests pass."):
            with self.subTest(text=text):
                self.plant_closing(text)
                self.assertIn(text, self.corpus_text())
                self.assertIn("fires 3\n", self.replay("--gate", "stop")[1])

    def test_negated_paraphrases_stay_quiet(self):
        self.stop_corpus()
        for text in ("This is not done yet.", "Not ready yet."):
            with self.subTest(text=text):
                self.plant_closing(text)
                self.assertIn("fires 2\n", self.replay("--gate", "stop")[1])

    def test_edit_under_temp_is_outside(self):
        self.read_corpus()
        self.assertIn("fires 0\n", self.cli("--gate", "stop")[1])


class Shadow(Lab):
    """One corpus per gate: a joined fire, a joined quiet row, and every row that must not join."""

    def commit_rows(self):
        session = self.session()
        session.prompt()
        stale = session.commit()
        fresh = session.commit()
        denied = session.shell('git commit -m "change"', "Hook PreToolUse denied this tool call", is_error=True)
        lost = session.unanswered('git commit -m "change"')
        session.save()
        other = self.session(SID_B)
        other.prompt()
        other.commit()
        foreign = other.commit()
        other.save()
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, stale))
        fx.write_row(self.rows, "b", fx.commit_row(SID_A, fresh, "fresh"))
        fx.write_row(self.rows, "c", fx.commit_row(SID_A, denied))
        fx.write_row(self.rows, "d", fx.commit_row(SID_A, lost))
        fx.write_row(self.rows, "e", fx.commit_row(SID_A, "toolu_unknown"))
        fx.write_row(self.rows, "f", fx.commit_row(SID_A, foreign))
        fx.write_row(self.rows, "g")
        return session

    def stop_rows(self):
        session = self.session()
        first = session.prompt()
        session.say("Working on it.")
        session.edit("src/a.py")
        session.say("Done.")
        second = session.prompt()
        session.edit("src/b.py")
        session.say("Done.")
        third = session.prompt()
        session.say("Checked, all fine and verified.")
        session.save()
        other = self.session(SID_B)
        other.prompt()
        other.say("Done.")
        other.save()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, first, "Done."))
        fx.write_row(self.rows, "b", fx.stop_row(SID_A, third, "Checked, all fine and verified.", "verified"))
        fx.write_row(self.rows, "c", fx.stop_row(SID_A, first, "Working on it."))
        fx.write_row(self.rows, "d", fx.stop_row(SID_A, "prompt-none", "Done."))
        fx.write_row(self.rows, "e", fx.stop_row(SID_B, second, "Done."))
        fx.write_row(self.rows, "f")
        return first, second

    def test_commit_join(self):
        self.commit_rows()
        code, out, err = self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        self.assertIn("verdict stale 1\n", out)
        self.assertIn("verdict fresh 1\n", out)
        self.assertIn("fires 1\n", out)
        self.assertNotIn("upper bound", out)
        self.assertEqual(self.shadow_summary("commit")["total"], 2)

    def test_commit_hidden_rows_stay_their_own_column(self):
        session = self.session()
        session.prompt()
        hidden, plain, fresh = session.commit(), session.commit(), session.commit()
        session.save()
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, hidden, "unknown", reason="hidden"))
        fx.write_row(self.rows, "b", fx.commit_row(SID_A, plain, "unknown", reason="budget"))
        fx.write_row(self.rows, "c", fx.commit_row(SID_A, fresh, "fresh"))
        code, out, err = self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        self.assertIn("verdict unknown/hidden 1\n", out)
        self.assertIn("verdict unknown 1\n", out)
        self.assertIn("fires 0\n", out)
        summary = self.shadow_summary("commit")
        self.assertIn("verdicts", summary)
        self.assertEqual(summary["verdicts"], {"unknown/hidden": 1, "unknown": 1, "fresh": 1})

    def test_a_hidden_row_is_left_out_of_the_commits_a_wrong_deny_is_a_share_of(self):
        session = self.session()
        session.prompt()
        hidden, fresh = session.commit(), session.commit()
        session.save()
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, hidden, "unknown", reason="hidden"))
        fx.write_row(self.rows, "b", fx.commit_row(SID_A, fresh, "fresh"))
        self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual(self.shadow_summary("commit")["total"], 1)

    def test_stop_hidden_rows_stay_their_own_column_and_earn_no_credit(self):
        first, _ = self.stop_rows()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, first, "Done.", "unknown", reason="hidden"))
        code, out, err = self.replay("--gate", "stop", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        self.assertIn("verdict unknown/hidden 1\n", out)
        self.assertIn("fires 0\n", out)
        summary = self.shadow_summary("stop")
        self.assertIn("verdicts", summary)
        self.assertEqual(summary["verdicts"], {"unknown/hidden": 1, "verified": 1})
        self.assertEqual(summary["total"], 1)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            replay.report.print_verdicts("stop", summary)
        self.assertIn("stop credit: verified 1 of 2 in-scope closings (50.0%)\n", printed.getvalue())
        self.assertIn("stop hidden: unknown/hidden 1 of 2 in-scope closings (50.0%)\n", printed.getvalue())

    def test_commit_reports_unmatched_rows(self):
        self.commit_rows()
        out = self.replay("--gate", "commit", "--from-shadow", self.rows)[1]
        self.assertIn("unmatched 2\n", out)
        self.assertEqual(self.shadow_summary("commit")["unmatched"], 2)

    def test_commit_drops_calls_with_no_result(self):
        self.commit_rows()
        self.assertIn("dropped 2\n", self.replay("--gate", "commit", "--from-shadow", self.rows)[1])

    def test_commit_empty_file_is_unfinished(self):
        self.commit_rows()
        out = self.replay("--gate", "commit", "--from-shadow", self.rows)[1]
        self.assertIn("unfinished 1\n", out)
        self.assertEqual(self.shadow_summary("commit")["unfinished"], 1)

    def test_commit_counts_stale_rows_a_git_operation_explains(self):
        session = self.session()
        session.prompt()
        rebased, reset, plain = session.commit(), session.commit(), session.commit()
        session.save()
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, rebased, git_op="rebase-merge"))
        fx.write_row(self.rows, "b", fx.commit_row(SID_A, reset, reflog_age_s=60, newest_age_s=60))
        fx.write_row(self.rows, "c", fx.commit_row(SID_A, plain))
        out = self.replay("--gate", "commit", "--from-shadow", self.rows)[1]
        self.assertIn("in progress 1, with HEAD moved after the newest change 1\n", out)

    def test_stop_join(self):
        self.stop_rows()
        code, out, err = self.replay("--gate", "stop", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        self.assertIn("verdict flag 1\n", out)
        self.assertIn("verdict verified 1\n", out)
        self.assertIn("fires 1\n", out)
        self.assertEqual(self.shadow_summary("stop")["total"], 2)

    def test_stop_reports_unmatched_rows(self):
        self.stop_rows()
        out = self.replay("--gate", "stop", "--from-shadow", self.rows)[1]
        self.assertIn("unmatched 3\n", out)
        self.assertEqual(self.shadow_summary("stop")["unmatched"], 3)

    def test_stop_empty_file_is_unfinished(self):
        self.stop_rows()
        out = self.replay("--gate", "stop", "--from-shadow", self.rows)[1]
        self.assertIn("unfinished 1\n", out)
        self.assertEqual(self.shadow_summary("stop")["unfinished"], 1)

    def test_stop_counts_distinct_fires_by_fingerprint(self):
        session = self.session()
        one = session.prompt()
        session.edit("src/a.py")
        session.say("Done.")
        two = session.prompt()
        session.say("Done, really.")
        session.save()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, one, "Done.", fingerprint="same"))
        fx.write_row(self.rows, "b", fx.stop_row(SID_A, two, "Done, really.", fingerprint="same"))
        out = self.replay("--gate", "stop", "--from-shadow", self.rows)[1]
        self.assertIn("verdict flag 2\n", out)
        self.assertIn("fires 1\n", out)

    def test_stop_row_file_with_two_roots_gives_two_fires(self):
        session = self.session()
        prompt = session.prompt()
        session.edit("src/a.py")
        session.say("Done.")
        session.save()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, prompt, "Done.", repo8="repo0001", fingerprint="same"),
                     fx.stop_row(SID_A, prompt, "Done.", repo8="repo0002", fingerprint="same"))
        out = self.replay("--gate", "stop", "--from-shadow", self.rows)[1]
        self.assertIn("fires 2\n", out)
        self.assertEqual(len({fire["id"] for fire in self.shadow_summary("stop")["fires"]}), 2)

    def test_row_cap_warns(self):
        self.commit_rows()
        self.assertEqual(replay.ROW_CAP, 20000)
        with mock.patch.object(replay, "ROW_CAP", 8):
            self.assertNotIn("warning", self.replay("--gate", "commit", "--from-shadow", self.rows)[1])
        with mock.patch.object(replay, "ROW_CAP", 7):
            out = self.replay("--gate", "commit", "--from-shadow", self.rows)[1]
            self.assertIn("warning: 7 row files, at the 7 cap", out)

    def test_fire_id_is_the_hash_of_the_text_show_prints(self):
        self.stop_rows()
        out = self.replay("--gate", "stop", "--from-shadow", self.rows)[1]
        name = re.search(r"^sample (\S+)$", out, re.M).group(1)
        shown = self.replay("--gate", "stop", "--from-shadow", self.rows, "--show", name)[1]
        projected = shown[shown.index("stop %s" % SID_A[:8]):].rstrip("\n")
        self.assertEqual(te.row_key("stop", projected), name)

    def test_no_transcript_text_is_stored(self):
        session = self.session()
        prompt = session.prompt()
        session.edit("src/a.py")
        session.say("Done, SENTINEL-CLOSING.")
        session.save()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, prompt, "Done, SENTINEL-CLOSING."))
        code, out, err = self.replay("--gate", "stop", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        self.assertNotIn("SENTINEL", out)
        stored = []
        for folder, _, names in os.walk(self.data):
            stored.extend(os.path.join(folder, name) for name in names)
        self.assertGreaterEqual(len(stored), 2)
        for path in stored:
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn("SENTINEL", handle.read())
        name = self.shadow_summary("stop")["fires"][0]["id"]
        self.assertIn("SENTINEL", self.replay("--gate", "stop", "--from-shadow", self.rows, "--show", name)[1])


class Refusals(Lab):
    def test_zero_files_exit_nonzero(self):
        code, out, err = self.replay("--gate", "commit")
        self.assertEqual((code, "0 files read" in err), (2, True), out)

    def test_zero_row_files_exit_nonzero(self):
        os.makedirs(self.rows)
        self.commit_corpus()
        code, _, err = self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual((code, "0 row files" in err), (2, True))

    def test_rows_with_no_transcript_exit_nonzero(self):
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, "toolu_x"))
        code, _, err = self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual((code, "0 files read" in err), (2, True))

    def test_each_relied_on_field_seen_zero_times_exits_nonzero(self):
        self.commit_corpus()
        self.assertEqual(self.replay("--gate", "commit")[0], 0)
        for field in replay.RELIED:
            with self.subTest(field=field):
                backup = os.path.join(self.base, "keep")
                shutil.copytree(self.config, backup)
                fx.strip_field(self.config, field)
                code, _, err = self.replay("--gate", "commit")
                shutil.rmtree(self.config)
                shutil.move(backup, self.config)
                self.assertEqual(code, 2)
                self.assertEqual(err.strip(), "relied-on field seen 0 times: %s" % field)

    def test_shadow_refuses_a_missing_field_too(self):
        session = self.session()
        session.prompt()
        use_id = session.commit()
        session.save()
        fx.write_row(self.rows, "a", fx.commit_row(SID_A, use_id))
        self.assertEqual(self.replay("--gate", "commit", "--from-shadow", self.rows)[0], 0)
        fx.strip_field(self.config, "promptId")
        code, _, err = self.replay("--gate", "commit", "--from-shadow", self.rows)
        self.assertEqual((code, "promptId" in err), (2, True))

    def test_show_of_an_unknown_id_fails(self):
        self.commit_corpus()
        code, _, err = self.replay("--gate", "commit", "--show", "000000000000")
        self.assertEqual((code, "no fire with id" in err), (1, True))


class Baseline(Lab):
    def manifest(self, *lines):
        path = os.path.join(self.base, "manifest.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(line + "\n" for line in lines))
        return path

    def test_funnel_over_the_frozen_list_only(self):
        self.stop_corpus()
        listed = self.manifest("project-a/" + SID_A + ".jsonl",
                               os.path.join(self.config, "projects", "project-a", SID_D + ".jsonl"))
        code, out, err = self.replay("--baseline", "--manifest", listed)
        self.assertEqual(code, 0, err)
        self.assertIn("# files read 3, sessions 2,", out)
        self.assertIn("commits, gated roots: 7, allow 4, allow-inline 1, block 2\n", out)
        self.assertIn("commits, all roots: 7, allow 4, allow-inline 1, block 2\n", out)
        self.assertIn("funnel pending edit at turn end: 6 rows, 1 sessions\n", out)
        self.assertIn("funnel claim word: 4 rows, 1 sessions\n", out)
        self.assertIn("funnel code file: 4 rows, 1 sessions\n", out)
        self.assertIn("funnel edited this turn: 3 rows, 1 sessions\n", out)
        self.assertIn("funnel in a git repo: 3 rows, 1 sessions\n", out)
        self.assertIn("funnel claim in lead or last paragraph: 2 rows, 1 sessions\n", out)
        self.assertIn("funnel gated root: 2 rows, 1 sessions\n", out)

    def test_missing_listed_session_fails(self):
        self.stop_corpus()
        code, _, err = self.replay("--baseline", "--manifest", self.manifest("project-a/gone.jsonl"))
        self.assertEqual((code, "1 of 1 listed sessions missing" in err), (2, True))

    def test_empty_list_fails(self):
        self.stop_corpus()
        code, _, err = self.replay("--baseline", "--manifest", self.manifest())
        self.assertEqual((code, "0 files read" in err), (2, True))


class Installed(Lab):
    def test_runs_isolated_with_a_non_ascii_path_and_no_utf8_mode(self):
        session = self.session()
        prompt = session.prompt()
        session.edit("src/a.py")
        session.say("Fixed the crème bug.")
        session.save()
        fx.write_row(self.rows, "a", fx.stop_row(SID_A, prompt, "Fixed the crème bug."))
        code, out, err = self.cli("--gate", "stop", "--from-shadow", self.rows)
        self.assertEqual(code, 0, err)
        name = re.search(r"^sample (\S+)$", out, re.M).group(1)
        code, out, err = self.cli("--gate", "stop", "--from-shadow", self.rows, "--show", name)
        self.assertEqual(code, 0, err)
        self.assertIn("Fixed the crème bug.", out)

    def test_writes_no_bytecode_beside_the_tool(self):
        cache = os.path.join(fx.LAB, "__pycache__")
        self.assertFalse(os.path.exists(cache), "left by an earlier run")
        self.assertEqual(self.cli("--gate", "commit")[0], 2)
        self.assertFalse(os.path.exists(cache))

    def test_data_is_required(self):
        done = subprocess.run([sys.executable, "-I", SCRIPT, "--gate", "commit"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--data", done.stderr)

    def test_first_run_starts_the_trials_and_a_later_one_keeps_them(self):
        self.replay("--gate", "commit")
        path = os.path.join(self.data, "gate-lab", "trials.json")
        with open(path, encoding="utf-8") as handle:
            trials = json.load(handle)
        self.assertEqual([trial["name"] for trial in trials], ["commit-unchecked", "stop-done"])
        self.assertEqual(trials[0]["started"], fx.day(0))
        self.assertEqual(trials[0]["review"], fx.day(-14))
        self.assertEqual({trial["mode"] for trial in trials}, {"shadow"})
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(trials[:1], handle)
        self.replay("--gate", "commit")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(len(json.load(handle)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
