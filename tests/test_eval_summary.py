"""Tests for scripts/eval_summary.py: a summary is a claim about a plugin, so every label, the
partial check, the merge and each shape check has a test that goes red when it is removed."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_summary.py"
FIXTURE = ROOT / "tests" / "fixtures" / "eval" / "aggregate-real.json"
sys.path.insert(0, str(ROOT / "scripts"))

import eval_summary as es  # noqa: E402


def run(error=None):
    return {"score": 0, "passed": False, "turns": 1, "costUsd": 0.01, "error": error, "graders": []}


def case(name, with_score, without_score, with_errors=(None,), without_errors=(None,), cost_note=None):
    """A case in the shape of a real result: arms hold runs, aggregates hold the scores."""
    agg = {"score": with_score, "passRate": 1.0 if with_score == 1 else 0.0}
    if without_score is not None:
        agg.update(
            scoreWithout=without_score,
            passRateWithout=1.0 if without_score == 1 else 0.0,
            delta=with_score - without_score,
        )
    arms = {"with": [run(e) for e in with_errors]}
    if without_score is not None:
        arms["without"] = [run(e) for e in without_errors]
    return {"name": name, "dir": f"evals/{name}", "arms": arms, "aggregates": agg}


def result(*cases, partial=False, cost=0.5, reason=None):
    r = {"schemaVersion": 1, "partial": partial, "costUsd": cost, "cases": list(cases), "aggregates": {}}
    if reason:
        r["partialReason"] = reason
    return r


def labels(summary):
    return {c["name"]: c["label"] for c in summary["cases"]}


class LabelTests(unittest.TestCase):
    def test_real_result_summarises(self):
        real = json.loads(FIXTURE.read_text(encoding="utf-8"))
        s = es.summarise([real])
        self.assertEqual(s["status"], "complete", s)
        self.assertEqual([c["label"] for c in s["cases"]], ["discriminates"])
        self.assertEqual(s["cases"][0]["with_score"], 1)
        self.assertEqual(s["cases"][0]["without_score"], 0)
        self.assertAlmostEqual(s["cost"], real["costUsd"])

    def test_positive_delta_discriminates(self):
        self.assertEqual(labels(es.summarise([result(case("a", 1.0, 0.0))])), {"a": "discriminates"})

    def test_both_full_passes_in_both(self):
        s = es.summarise([result(case("a", 1.0, 1.0), case("b", 1.0, 0.0))])
        self.assertEqual(labels(s)["a"], "passes in both")

    def test_both_zero_fails_in_both(self):
        s = es.summarise([result(case("a", 0.0, 0.0), case("b", 1.0, 0.0))])
        self.assertEqual(labels(s)["a"], "fails in both")

    def test_equal_partial_score_is_not_a_pass(self):
        s = es.summarise([result(case("a", 0.5, 0.5), case("b", 1.0, 0.0))])
        self.assertEqual(labels(s)["a"], "fails in both")

    def test_negative_delta_is_plugin_worse(self):
        s = es.summarise([result(case("a", 0.0, 1.0))])
        self.assertEqual(labels(s)["a"], "plugin worse")
        self.assertEqual(s["cases"][0]["delta"], -1.0)

    def test_mixed_suite_is_measured_with_the_right_count(self):
        s = es.summarise(
            [result(case("a", 1.0, 0.0), case("b", 1.0, 1.0), case("c", 0.0, 0.0), case("d", 0.0, 1.0))]
        )
        self.assertEqual(s["plugin_label"], "measured")
        self.assertEqual(s["discriminating"], 2)
        self.assertEqual(s["pass_rate_with"], 0.5)
        self.assertEqual(s["pass_rate_without"], 0.5)

    def test_no_discriminating_case_is_not_measurable(self):
        s = es.summarise([result(case("a", 1.0, 1.0), case("b", 0.0, 0.0))])
        self.assertEqual(s["plugin_label"], "not measurable")
        self.assertEqual(s["discriminating"], 0)
        self.assertNotIn("pass_rate_with", s)
        self.assertNotIn("pass_rate_without", s)

    def test_all_runs_errored_is_errored_and_not_counted(self):
        s = es.summarise([result(case("a", 0.0, 0.0, with_errors=("boom", "boom")), case("b", 1.0, 0.0))])
        self.assertEqual(labels(s)["a"], "errored")
        self.assertEqual(s["counted"], 1)
        self.assertEqual(s["discriminating"], 1)

    def test_errored_baseline_arm_is_errored(self):
        s = es.summarise([result(case("a", 1.0, 0.0, without_errors=("boom",)), case("b", 1.0, 0.0))])
        self.assertEqual(labels(s)["a"], "errored")

    def test_all_cases_errored_is_not_measurable(self):
        s = es.summarise([result(case("a", 0.0, 0.0, with_errors=("boom",)))])
        self.assertEqual(s["plugin_label"], "not measurable")
        self.assertEqual(s["counted"], 0)

    def test_one_errored_run_of_three_keeps_the_score_and_is_flagged(self):
        s = es.summarise([result(case("a", 1.0, 0.0, with_errors=(None, "boom", None)))])
        c = s["cases"][0]
        self.assertEqual(c["label"], "discriminates")
        self.assertEqual(c["errored_runs"], 1)
        self.assertEqual(s["discriminating"], 1)

    def test_cost_is_summed(self):
        s = es.summarise([result(case("a", 1.0, 0.0), cost=0.25), result(case("b", 1.0, 0.0), cost=0.5)])
        self.assertAlmostEqual(s["cost"], 0.75)


class PartialTests(unittest.TestCase):
    def test_partial_result_is_partial_with_no_plugin_number(self):
        s = es.summarise([result(case("a", 1.0, 0.0), partial=True, reason="cost_ceiling")])
        self.assertEqual(s["status"], "partial")
        self.assertEqual(s["plugin_label"], "partial")
        self.assertIn("cost_ceiling", s["reason"])
        self.assertNotIn("pass_rate_with", s)

    def test_partial_case_without_baseline_arm_is_not_refused(self):
        s = es.summarise([result(case("a", 1.0, None), partial=True, reason="cost_ceiling")])
        self.assertEqual(s["status"], "partial")

    def test_complete_case_without_baseline_arm_is_refused(self):
        s = es.summarise([result(case("a", 1.0, None))])
        self.assertEqual(s["status"], "refused")
        self.assertIn("without", s["reason"])

    def test_one_partial_result_makes_the_merge_partial(self):
        s = es.summarise([result(case("a", 1.0, 0.0)), result(case("b", 1.0, 0.0), partial=True)])
        self.assertEqual(s["status"], "partial")


class MergeTests(unittest.TestCase):
    def test_two_results_merge_by_case_name(self):
        s = es.summarise([result(case("a", 1.0, 0.0)), result(case("b", 1.0, 1.0))])
        self.assertEqual(s["status"], "complete")
        self.assertEqual(labels(s), {"a": "discriminates", "b": "passes in both"})

    def test_duplicate_case_name_is_refused(self):
        s = es.summarise([result(case("a", 1.0, 0.0)), result(case("a", 1.0, 0.0))])
        self.assertEqual(s["status"], "refused")
        self.assertIn("'a'", s["reason"])


class ShapeTests(unittest.TestCase):
    def refused(self, results, text):
        s = es.summarise(results)
        self.assertEqual(s["status"], "refused", s)
        self.assertIn(text, s["reason"])
        return s

    def test_top_level_list(self):
        self.refused([[1, 2]], "not an object")

    def test_cases_a_number(self):
        r = result(case("a", 1.0, 0.0))
        r["cases"] = 3
        self.refused([r], "cases")

    def test_empty_cases_list(self):
        self.refused([result()], "no cases")

    def test_case_without_score_key(self):
        c = case("a", 1.0, 0.0)
        del c["aggregates"]["score"]
        self.refused([result(c)], "score")

    def test_score_not_a_number(self):
        c = case("a", 1.0, 0.0)
        c["aggregates"]["score"] = "1.0"
        self.refused([result(c)], "score")

    def test_boolean_score(self):
        c = case("a", 1.0, 0.0)
        c["aggregates"]["score"] = True
        self.refused([result(c)], "score")

    def test_aggregates_not_an_object(self):
        c = case("a", 1.0, 0.0)
        c["aggregates"] = 5
        self.refused([result(c)], "score")

    def test_score_without_not_a_number(self):
        c = case("a", 1.0, 0.0)
        c["aggregates"]["scoreWithout"] = "0"
        self.refused([result(c)], "scoreWithout")

    def test_arms_not_an_object(self):
        c = case("a", 1.0, 0.0)
        c["arms"] = 5
        self.refused([result(c)], "arms")

    def test_missing_schema_version(self):
        r = result(case("a", 1.0, 0.0))
        del r["schemaVersion"]
        self.refused([r], "schemaVersion")

    def test_case_not_an_object(self):
        r = result(case("a", 1.0, 0.0))
        r["cases"] = [5]
        self.refused([r], "case")

    def test_case_without_a_name(self):
        c = case("a", 1.0, 0.0)
        del c["name"]
        self.refused([result(c)], "name")

    def test_arms_runs_not_a_list(self):
        c = case("a", 1.0, 0.0)
        c["arms"]["with"] = 7
        self.refused([result(c)], "arms")

    def test_no_results_at_all(self):
        self.refused([], "no result")

    def test_results_not_a_list(self):
        self.refused({"cases": []}, "no result")

    def test_error_in_real_result_shape_is_read(self):
        real = copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))
        real["cases"][0]["arms"]["with"][0]["error"] = "exit 1: refused"
        s = es.summarise([real])
        self.assertEqual(s["cases"][0]["label"], "errored")


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *results, extra=()):
        paths = []
        for i, r in enumerate(results):
            p = Path(self._tmp.name) / f"r{i}.json"
            p.write_text(r if isinstance(r, str) else json.dumps(r), encoding="utf-8")
            paths.append(str(p))
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--plugin", "demo-plugin", "--model", "sonnet", *extra, *paths],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_complete_run_prints_table_rates_cost_and_summary(self):
        p = self.run_cli(result(case("a", 1.0, 0.0), case("b", 1.0, 1.0), cost=0.25))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| a |", p.stdout)
        self.assertIn("discriminates", p.stdout)
        self.assertIn("passes in both", p.stdout)
        self.assertIn("pass rate with 100%, without 50%", p.stdout)
        self.assertIn("cost $0.25", p.stdout)
        self.assertIn("# cases 2, discriminating 1", p.stdout)

    def test_summary_line_leaves_errored_cases_out(self):
        p = self.run_cli(result(case("a", 1.0, 0.0), case("b", 0.0, 0.0, with_errors=("boom",))))
        self.assertIn("# cases 1, discriminating 1", p.stdout)
        self.assertIn("errored", p.stdout)

    def test_not_measurable_prints_no_pass_rate_line(self):
        p = self.run_cli(result(case("a", 1.0, 1.0)))
        self.assertEqual(p.returncode, 0)
        self.assertIn("not measurable", p.stdout)
        self.assertNotIn("pass rate", p.stdout)
        self.assertIn("# cases 1, discriminating 0", p.stdout)

    def test_partial_exits_2_and_prints_no_pass_rate_line(self):
        p = self.run_cli(result(case("a", 1.0, 0.0), partial=True, reason="cost_ceiling"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("partial", p.stdout)
        self.assertNotIn("pass rate", p.stdout)

    def test_wrong_shape_exits_1_with_a_reason_line_and_no_traceback(self):
        p = self.run_cli("[1, 2]")
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("refused:", p.stdout)

    def test_invalid_json_exits_1_with_a_reason_line(self):
        p = self.run_cli("{not json")
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("refused:", p.stdout)

    def test_missing_file_exits_1_with_a_reason_line(self):
        p = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--plugin", "x", "--model", "m", str(Path(self._tmp.name) / "no.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("refused:", p.stdout)

    def test_duplicate_case_across_files_exits_1(self):
        p = self.run_cli(result(case("a", 1.0, 0.0)), result(case("a", 1.0, 0.0)))
        self.assertEqual(p.returncode, 1)
        self.assertIn("refused:", p.stdout)

    def test_two_files_merge(self):
        p = self.run_cli(result(case("a", 1.0, 0.0)), result(case("b", 1.0, 1.0)))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("# cases 2, discriminating 1", p.stdout)


if __name__ == "__main__":
    unittest.main()
