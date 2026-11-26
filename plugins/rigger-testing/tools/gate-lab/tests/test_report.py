"""report.py over labels and shadow summaries with known answers.

python -I -X utf8 test_report.py
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import lab_fixtures as fx  # noqa: E402

import report  # noqa: E402

SCRIPT = os.path.join(fx.LAB, "report.py")


def label(name, real, gate="commit", source="user"):
    return {"id": name, "gate": gate, "real": real, "source": source, "note": ""}


def fire(name, days_ago, minute=0, repo8="repo0001"):
    return {"id": name, "ts": fx.iso(days_ago, minute), "repo8": repo8}


def run_of(prefix, flags, days_ago=5, repos=("repo0001", "repo0002")):
    """Labels and fires for `flags`, oldest first, one minute apart."""
    labels = [label("%s%03d" % (prefix, index), flag) for index, flag in enumerate(flags)]
    fires = [fire(row["id"], days_ago, index, repos[index % len(repos)]) for index, row in enumerate(labels)]
    return labels, fires


class Report(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="lab-crème-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.data = os.path.join(self.base, "data")

    def run_report(self, *args):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONUTF8"}
        done = subprocess.run([sys.executable, "-I", SCRIPT, "--data", self.data] + list(args), env=env, cwd=self.base,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        out = done.stdout.decode("utf-8").replace("\r\n", "\n")
        return done.returncode, out, done.stderr.decode("utf-8", "replace")

    def write_labels(self, labels):
        os.makedirs(os.path.join(self.data, "gate-lab"), exist_ok=True)
        path = os.path.join(self.data, "gate-lab", "labels.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(json.dumps(row) + "\n" for row in labels))

    def write_shadow(self, gate, fires, total=1000, **fields):
        report.write_shadow(self.data, gate, dict({
            "gate": gate, "written": fx.day(0), "rows": total, "unfinished": 0, "unmatched": 0, "dropped": 0,
            "total": total, "fires": fires,
        }, **fields))

    def write_trials(self, *trials):
        path = os.path.join(self.data, "gate-lab", "trials.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(list(trials), handle)

    def trial(self, gate="commit", review=1, started=20, name="trial"):
        return {"name": name, "gate": gate, "mode": "shadow", "started": fx.day(started), "review": fx.day(review),
                "keep": "k", "kill": "k", "rollback": "r"}

    def wire(self, gate):
        fx.write_row(os.path.join(self.data, *report.ROW_DIRS[gate]), "a", {"verdict": "fresh"})

    def verdict(self, gate, labels, fires, total=1000, **trial):
        self.write_labels(labels)
        self.write_shadow(gate, fires, total)
        self.wire(gate)
        self.write_trials(self.trial(gate, **trial))
        code, out, err = self.run_report("--due")
        self.assertEqual(code, 0, err)
        return out.split(" verdict ")[1].split()[0]


class Precision(Report):
    def test_wilson_matches_the_known_interval(self):
        low, high = report.wilson(2, 40)
        self.assertAlmostEqual(low, 0.0138, 3)
        self.assertAlmostEqual(high, 0.1650, 3)

    def test_printed_interval_is_not_the_plain_ratio(self):
        labels, fires = run_of("a", [False] * 20 + [True, True] + [False] * 38)
        self.write_labels(labels)
        self.write_shadow("commit", fires)
        code, out, err = self.run_report()
        self.assertEqual(code, 0, err)
        self.assertIn("older half (tuned): 2 of 30 real, precision 6.7% (1.8-21.3%)", out)
        self.assertIn("newer half (reported): 0 of 30 real, precision 0.0% (0.0-11.4%)", out)

    def test_overfit_older_half_does_not_reach_the_reported_figure(self):
        labels, fires = run_of("a", [True] * 20 + [False] * 19 + [True])
        labels.reverse()
        fires.reverse()
        self.write_labels(labels)
        self.write_shadow("commit", fires)
        out = self.run_report()[1]
        self.assertIn("older half (tuned): 20 of 20 real, precision 100.0%", out)
        self.assertIn("newer half (reported): 1 of 20 real, precision 5.0%", out)

    def test_wrong_denies_over_all_commits(self):
        labels, fires = run_of("a", [True] * 10 + [True, False] * 5)
        self.write_labels(labels)
        self.write_shadow("commit", fires + [fire("unlabelled%d" % index, 3) for index in range(80)], total=500)
        self.assertIn("wrong denies over all commits: 50.0 of 500 (10.0%)", self.run_report()[1])

    def test_stop_prints_no_wrong_denies(self):
        labels, fires = run_of("a", [True] * 4)
        labels = [dict(row, gate="stop") for row in labels]
        self.write_labels(labels)
        self.write_shadow("stop", fires)
        out = self.run_report()[1]
        self.assertIn("stop user: 4 labels", out)
        self.assertNotIn("wrong denies", out)

    def test_sources_stay_apart(self):
        user, fires = run_of("a", [True, False])
        model, more = run_of("b", [False, False, False, False])
        self.write_labels(user + [dict(row, source="model") for row in model])
        self.write_shadow("commit", fires + more)
        out = self.run_report()[1]
        self.assertIn("commit user: 2 labels", out)
        self.assertIn("commit model: 4 labels", out)

    def test_label_with_no_fire_is_reported_and_not_counted(self):
        labels, fires = run_of("a", [True, False])
        self.write_labels(labels + [label("gone", True)])
        self.write_shadow("commit", fires)
        self.assertIn("commit user: 2 labels, 2 projects, 1 with no fire", self.run_report()[1])

    def test_no_labels_exit_nonzero(self):
        code, _, err = self.run_report()
        self.assertEqual((code, "0 labels read" in err), (2, True))
        self.write_labels([])
        code, _, err = self.run_report()
        self.assertEqual((code, "0 labels read" in err), (2, True))

    def test_labels_with_no_summary_exit_nonzero(self):
        self.write_labels([label("a", True)])
        code, _, err = self.run_report()
        self.assertEqual((code, "no shadow-commit.json" in err), (2, True))

    def test_bad_labels_stop_the_run(self):
        for row in ({"id": "a", "gate": "commit", "real": "yes", "source": "user"},
                    {"id": "a", "gate": "commit", "real": True, "source": "someone"},
                    {"id": "a", "gate": "push", "real": True, "source": "user"}):
            with self.subTest(row=row):
                self.write_labels([row])
                self.write_shadow("commit", [fire("a", 2)])
                code, _, err = self.run_report()
                self.assertEqual((code, "needs id, gate, real and source" in err), (1, True))

    def test_duplicate_label_stops_the_run(self):
        self.write_labels([label("a", True), label("a", False)])
        self.write_shadow("commit", [fire("a", 2)])
        code, _, err = self.run_report()
        self.assertEqual((code, "duplicate id a" in err), (1, True))


class VerdictTable(Report):
    def table(self, gate, verdicts):
        self.write_shadow(gate, [], verdicts=verdicts)
        code, out, err = self.run_report()
        self.assertEqual((code, "0 labels read" in err), (2, True))
        return out

    def test_a_hidden_row_has_its_own_column(self):
        self.assertEqual(report.verdict_column({"verdict": "unknown", "reason": "hidden"}), "unknown/hidden")
        self.assertEqual(report.verdict_column({"verdict": "unknown", "reason": "budget"}), "unknown")
        self.assertEqual(report.verdict_column({"verdict": "unknown", "reason": None}), "unknown")
        self.assertEqual(report.verdict_column({"verdict": "flag", "reason": "hidden"}), "flag")
        self.assertEqual(report.verdict_column({"verdict": "fresh", "reason": "ok"}), "fresh")

    def test_hidden_is_never_folded_into_unknown(self):
        out = self.table("stop", {"flag": 8, "unknown": 2, "unknown/hidden": 4, "verified": 8})
        self.assertIn("stop verdicts: flag 8, unknown 2, unknown/hidden 4, verified 8\n", out)

    def test_the_rate_is_over_in_scope_closings(self):
        out = self.table("stop", {"flag": 8, "unknown/hidden": 4, "verified": 8, "disclosed": 5, "ungated": 30})
        self.assertIn("stop hidden: unknown/hidden 4 of 25 in-scope closings (16.0%)\n", out)

    def test_commit_rows_outside_the_scope_are_left_out_of_the_rate(self):
        out = self.table("commit", {"fresh": 6, "unknown/hidden": 2, "stale": 2, "out-of-scope": 90, "dry-run": 9})
        self.assertIn("commit hidden: unknown/hidden 2 of 10 in-scope commits (20.0%)\n", out)

    def test_credit_counts_the_good_verdict_alone(self):
        out = self.table("stop", {"flag": 3, "unknown/hidden": 6, "verified": 1})
        self.assertIn("stop credit: verified 1 of 10 in-scope closings (10.0%)\n", out)
        out = self.table("commit", {"fresh": 1, "stale": 3, "unknown/hidden": 6})
        self.assertIn("commit credit: fresh 1 of 10 in-scope commits (10.0%)\n", out)

    def test_hidden_alone_earns_no_credit(self):
        out = self.table("stop", {"unknown/hidden": 6})
        self.assertIn("stop credit: verified 0 of 6 in-scope closings (0.0%)\n", out)
        out = self.table("commit", {"unknown/hidden": 6})
        self.assertIn("commit credit: fresh 0 of 6 in-scope commits (0.0%)\n", out)

    def test_over_a_third_says_so_in_plain_words(self):
        out = self.table("stop", {"flag": 5, "unknown/hidden": 6, "verified": 4})
        self.assertIn("stop: over a third of the in-scope closings ran a check the reader cannot read", out)
        out = self.table("commit", {"fresh": 4, "stale": 4, "unknown/hidden": 5})
        self.assertIn("commit: over a third of the in-scope commits ran a check", out)

    def test_exactly_a_third_stays_quiet(self):
        self.assertNotIn("over a third", self.table("stop", {"flag": 5, "unknown/hidden": 5, "verified": 5}))

    def test_rows_outside_the_scope_cannot_hide_a_high_rate(self):
        out = self.table("stop", {"unknown/hidden": 2, "verified": 2, "ungated": 90})
        self.assertIn("over a third", out)

    def test_no_row_in_scope_prints_no_rate(self):
        out = self.table("stop", {"ungated": 3})
        self.assertIn("stop: 0 in-scope closings\n", out)
        self.assertNotIn("hidden:", out)

    def test_a_summary_with_no_table_says_so(self):
        self.write_shadow("stop", [])
        self.assertIn("stop: no verdict table in this summary", self.run_report()[1])

    def test_each_replayed_gate_is_printed(self):
        self.write_shadow("stop", [], verdicts={"flag": 1})
        self.write_shadow("commit", [], verdicts={"stale": 1})
        out = self.run_report()[1]
        self.assertIn("stop verdicts: flag 1\n", out)
        self.assertIn("commit verdicts: stale 1\n", out)

    def test_labels_still_print_after_the_table(self):
        labels, fires = run_of("a", [True, False])
        self.write_labels(labels)
        self.write_shadow("commit", fires, verdicts={"stale": 2})
        out = self.run_report()[1]
        self.assertLess(out.index("commit verdicts:"), out.index("commit user:"))


class Due(Report):
    def test_prints_a_due_trial_and_not_a_future_one(self):
        self.wire("commit")
        self.write_trials(self.trial(name="past", review=1), self.trial(name="today", review=0),
                          self.trial(name="future", review=-1))
        code, out, err = self.run_report("--due")
        self.assertEqual(code, 0, err)
        self.assertIn("due past commit review %s" % fx.day(1), out)
        self.assertIn("due today commit", out)
        self.assertNotIn("future", out)
        self.assertIn("# trials 3, due 2\n", out)

    def test_first_run_copies_the_template_and_nothing_is_due(self):
        code, out, err = self.run_report("--due")
        self.assertEqual(code, 0, err)
        self.assertIn("# trials 2, due 0\n", out)
        with open(os.path.join(self.data, "gate-lab", "trials.json"), encoding="utf-8") as handle:
            trials = json.load(handle)
        self.assertEqual({trial["review"] for trial in trials}, {fx.day(-14)})

    def test_zero_trials_exit_nonzero(self):
        self.write_trials()
        code, _, err = self.run_report("--due")
        self.assertEqual((code, "0 trials read" in err), (1, True))

    def test_bad_review_date_is_no_silent_skip(self):
        self.write_trials(dict(self.trial(), review="soon"))
        self.assertNotEqual(self.run_report("--due")[0], 0)

    def test_not_wired_when_no_row_since_the_start(self):
        self.write_trials(self.trial())
        self.assertIn("verdict not-wired", self.run_report("--due")[1])

    def test_old_rows_do_not_wire_a_later_trial(self):
        self.wire("commit")
        path = os.path.join(self.data, "commit-gate", "rows", "a.json")
        moment = (fx.NOW - datetime.timedelta(days=30)).timestamp()
        os.utime(path, (moment, moment))
        self.write_trials(self.trial())
        self.assertIn("verdict not-wired", self.run_report("--due")[1])

    def test_no_case_without_a_replay(self):
        self.wire("commit")
        self.write_trials(self.trial())
        self.assertIn("verdict no-case", self.run_report("--due")[1])


class CommitVerdict(Report):
    def test_keep(self):
        labels, fires = run_of("a", [True] * 40)
        self.assertEqual(self.verdict("commit", labels, fires), "KEEP")

    def test_keep_reads_the_wrong_share_of_all_commits(self):
        labels, fires = run_of("a", [True] * 20 + [True, False] * 10)
        self.assertEqual(self.verdict("commit", labels, fires, total=400), "KEEP")
        self.assertEqual(self.verdict("commit", labels, fires, total=399), "no-case")

    def test_kill_reads_the_newer_half(self):
        labels, fires = run_of("a", [True] * 20 + [False] * 19 + [True])
        self.assertEqual(self.verdict("commit", labels, fires), "KILL")

    def test_no_kill_on_a_good_newer_half(self):
        labels, fires = run_of("a", [False] * 20 + [True] * 20)
        self.assertEqual(self.verdict("commit", labels, fires), "KEEP")

    def test_needs_thirty_owner_labels(self):
        labels, fires = run_of("a", [True] * 29)
        self.assertEqual(self.verdict("commit", labels, fires), "no-case")
        labels, fires = run_of("a", [True] * 30)
        self.assertEqual(self.verdict("commit", labels, fires), "KEEP")

    def test_model_labels_do_not_count(self):
        labels, fires = run_of("a", [True] * 40)
        self.assertEqual(self.verdict("commit", [dict(row, source="model") for row in labels], fires), "no-case")

    def test_needs_two_projects(self):
        labels, fires = run_of("a", [True] * 40, repos=("repo0001",))
        self.assertEqual(self.verdict("commit", labels, fires), "no-case")


class StopVerdict(Report):
    def stop(self, flags, days_ago=5, **trial):
        labels, fires = run_of("a", flags, days_ago)
        return self.verdict("stop", [dict(row, gate="stop") for row in labels], fires, **trial)

    def test_keep_on_precision(self):
        self.assertEqual(self.stop([False] * 20 + [True, False] * 10, started=20), "KEEP")

    def test_keep_on_two_catches_in_the_window(self):
        self.assertEqual(self.stop([True, True, False], started=10), "KEEP")

    def test_catches_after_the_window_do_not_count(self):
        self.assertEqual(self.stop([True, True, False], started=30, days_ago=5), "no-case")

    def test_one_catch_is_no_case(self):
        self.assertEqual(self.stop([True, False, False], started=10), "no-case")

    def test_kill_after_thirty_labels(self):
        self.assertEqual(self.stop([True] + [False] * 39, started=60, days_ago=5), "KILL")


if __name__ == "__main__":
    unittest.main(verbosity=1)
