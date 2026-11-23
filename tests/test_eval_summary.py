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
# Init events of two real stub runs of the rigger-rules suite (paths redacted): before the
# dependency was staged, and after.
UNSATISFIED = (ROOT / "tests" / "fixtures" / "eval" / "trace-dependency-unsatisfied.jsonl").read_text(encoding="utf-8")
LOADED = (ROOT / "tests" / "fixtures" / "eval" / "trace-loaded.jsonl").read_text(encoding="utf-8")
WITHOUT_ARM = (ROOT / "tests" / "fixtures" / "eval" / "trace-without-arm.jsonl").read_text(encoding="utf-8")
LOADED_DEMO = '{"type": "system", "subtype": "init", "plugins": [{"name": "demo-plugin", "path": "x"}]}\n'
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


class CliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def attach_traces(self, r, i):
        """Give every run that did not error a trace whose init event lists demo-plugin, as a kept run has."""
        for c in r["cases"]:
            for arm, runs in c.get("arms", {}).items():
                for j, run in enumerate(runs):
                    if run.get("error") or "tracePath" in run:
                        continue
                    trace = Path(self._tmp.name) / f"r{i}-{c['name']}-{arm}-{j}.jsonl"
                    trace.write_text(LOADED_DEMO if arm == "with" else WITHOUT_ARM, encoding="utf-8")
                    run["tracePath"] = str(trace)
        return r

    def run_cli(self, *results, extra=(), plugin="demo-plugin", traces=True):
        paths = []
        for i, r in enumerate(results):
            if isinstance(r, dict) and traces:
                r = self.attach_traces(copy.deepcopy(r), i)
            p = Path(self._tmp.name) / f"r{i}.json"
            p.write_text(r if isinstance(r, str) else json.dumps(r), encoding="utf-8")
            paths.append(str(p))
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--plugin", plugin, "--model", "sonnet", *extra, *paths],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


class CliTests(CliBase):
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


class LoadGuardTests(CliBase):
    """A score is a claim about a loaded plugin: any run whose session lacked it refuses the result."""

    def traced(self, with_trace, without_trace=None):
        r = result(case("a", 1.0, 0.0))
        arms = r["cases"][0]["arms"]
        for arm, text in (("with", with_trace), ("without", without_trace or WITHOUT_ARM)):
            path = Path(self._tmp.name) / f"{arm}.jsonl"
            path.write_text(text, encoding="utf-8")
            arms[arm][0]["tracePath"] = str(path)
        return r

    def refused(self, p, *texts):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("refused:", p.stdout)
        for text in texts:
            self.assertIn(text, p.stdout)

    def test_real_dependency_unsatisfied_trace_is_refused(self):
        p = self.run_cli(self.traced(UNSATISFIED), plugin="rigger-rules")
        self.refused(p, "case 'a' with run 1: plugin_errors rigger-rules@inline dependency-unsatisfied",
                     "plugin 'rigger-rules' is not loaded")

    def test_real_loaded_trace_is_summarised(self):
        p = self.run_cli(self.traced(LOADED, WITHOUT_ARM), plugin="rigger-rules")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("discriminates", p.stdout)

    def test_case_without_runs_is_refused(self):
        for name, arms in (("no arms", None), ("empty arms", {"with": [], "without": []}),
                           ("no without runs", {"with": [run()]})):
            with self.subTest(name=name):
                r = result(case("a", 1.0, 0.0))
                if arms is None:
                    del r["cases"][0]["arms"]
                else:
                    r["cases"][0]["arms"] = arms
                self.refused(self.run_cli(r), "case 'a'", "has no runs")

    def test_partial_case_without_baseline_runs_is_not_refused(self):
        p = self.run_cli(result(case("a", 1.0, None), partial=True, reason="cost_ceiling"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("refused:", p.stdout)

    def test_plugin_error_in_the_without_arm_is_refused(self):
        p = self.run_cli(self.traced(LOADED, UNSATISFIED), plugin="rigger-rules")
        self.refused(p, "case 'a' without run 1: plugin_errors")

    def test_with_arm_missing_the_plugin_is_refused(self):
        p = self.run_cli(self.traced(LOADED), plugin="rigger-other")
        self.refused(p, "plugin 'rigger-other' is not loaded")

    def test_run_without_trace_path_is_refused(self):
        p = self.run_cli(result(case("a", 1.0, 0.0)), traces=False)
        self.refused(p, "no tracePath (run the eval with --keep-temp)")

    def test_missing_trace_file_is_refused(self):
        r = result(case("a", 1.0, 0.0))
        r["cases"][0]["arms"]["with"][0]["tracePath"] = str(Path(self._tmp.name) / "gone.jsonl")
        self.refused(self.run_cli(r), "cannot read trace")

    def test_trace_without_init_event_is_refused(self):
        p = self.run_cli(self.traced('{"type": "result"}\nnot json\n'), plugin="demo-plugin")
        self.refused(p, "has no init event")

    def test_trace_path_a_number_is_refused(self):
        r = result(case("a", 1.0, 0.0))
        r["cases"][0]["arms"]["with"][0]["tracePath"] = 7
        self.refused(self.run_cli(r), "no tracePath")

    def test_plugins_not_a_list_is_refused(self):
        init = '{"type": "system", "subtype": "init", "plugins": 7}\n'
        self.refused(self.run_cli(self.traced(init)), "plugin 'demo-plugin' is not loaded")

    def test_plugin_errors_not_a_list_is_refused(self):
        init = ('{"type": "system", "subtype": "init", "plugins": [{"name": "demo-plugin"}],'
                ' "plugin_errors": {"plugin": "x"}}\n')
        self.refused(self.run_cli(self.traced(init)), "plugin_errors {'plugin': 'x'}")


WITH_ONLY_REAL = ROOT / "tests" / "fixtures" / "eval" / "aggregate-with-only-real.json"


def graded_run(error=None, **passed):
    r = run(error)
    r["graders"] = [] if error else [{"name": n, "passed": p, "weight": 1} for n, p in passed.items()]
    return r


def with_only_case(name, *runs):
    return {"name": name, "dir": f"evals/{name}", "arms": {"with": list(runs)},
            "aggregates": {"score": 0.5, "passRate": 0.0}}


class WithOnlyTests(CliBase):
    """`--with-only` reads an `--ablation none` result: per-grader counts, behind the same load guard."""

    refused = LoadGuardTests.refused

    def with_only(self, *results, plugin="demo-plugin"):
        return self.run_cli(*results, extra=("--with-only",), plugin=plugin)

    def test_real_result_prints_per_grader_counts(self):
        # The 2026-11-18 sonnet run of the negative control: testing never loaded, `&&` in every
        # answer, git-workflow never loaded.
        p = self.with_only(json.loads(WITH_ONLY_REAL.read_text(encoding="utf-8")))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| add-commit-push-chain | no-testing-skill | 3/3 |", p.stdout)
        self.assertIn("| add-commit-push-chain | outcome | 3/3 |", p.stdout)
        self.assertIn("| add-commit-push-chain | skill | 0/3 |", p.stdout)
        self.assertIn("# cases 1", p.stdout)

    def test_load_guard_refuses_a_with_only_result(self):
        r = result(with_only_case("a", graded_run(skill=True)))
        trace = Path(self._tmp.name) / "unsatisfied.jsonl"
        trace.write_text(UNSATISFIED, encoding="utf-8")
        r["cases"][0]["arms"]["with"][0]["tracePath"] = str(trace)
        self.refused(self.with_only(r, plugin="rigger-rules"),
                     "plugin_errors rigger-rules@inline dependency-unsatisfied", "plugin 'rigger-rules' is not loaded")
        self.refused(self.run_cli(result(with_only_case("a", graded_run(skill=True))), extra=("--with-only",),
                                  traces=False), "no tracePath")

    def test_result_with_a_without_arm_is_refused(self):
        self.refused(self.with_only(result(case("a", 1.0, 0.0))), "case 'a' has a without-arm score")

    def test_default_mode_names_the_flag(self):
        self.refused(self.run_cli(result(with_only_case("a", graded_run(skill=True)))), "--with-only")

    def test_errored_runs_are_left_out_of_the_counts(self):
        r = result(with_only_case("a", graded_run(skill=True, outcome=False), graded_run("boom")),
                   with_only_case("b", graded_run("boom")))
        p = self.with_only(r)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("| a | skill | 1/1 |", p.stdout)
        self.assertIn("| a | outcome | 0/1 |", p.stdout)
        self.assertIn("case 'a': 1 errored run(s) left out of the counts", p.stdout)
        self.assertIn("| b | every run errored | - |", p.stdout)
        self.assertIn("# cases 1", p.stdout)

    def test_partial_with_only_result_exits_2(self):
        p = self.with_only(result(with_only_case("a", graded_run(skill=True)), partial=True, reason="cost_ceiling"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("partial result: cost_ceiling", p.stdout)

    def test_aggregates_of_the_wrong_shape_are_refused(self):
        for label, agg in (("missing", None), ("null", "null"), ("a list", [1])):
            with self.subTest(label):
                c = with_only_case("a", graded_run(skill=True))
                if agg is None:
                    del c["aggregates"]
                else:
                    c["aggregates"] = None if agg == "null" else agg
                self.refused(self.with_only(result(c)), "case 'a': aggregates is missing or not an object")

    def test_wrong_grader_shapes_are_refused(self):
        for label, graders in (("graders a dict", {"skill": True}), ("graders missing", None), ("graders empty", []),
                               ("passed a string", [{"name": "skill", "passed": "yes"}]),
                               ("name missing", [{"passed": True}])):
            with self.subTest(label):
                bad = graded_run(skill=True)
                if graders is None:
                    del bad["graders"]
                else:
                    bad["graders"] = graders
                self.refused(self.with_only(result(with_only_case("a", bad))), "case 'a' with run 1: graders")


if __name__ == "__main__":
    unittest.main()
