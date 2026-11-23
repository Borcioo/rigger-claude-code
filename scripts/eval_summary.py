"""Summarise `claude plugin eval` results (aggregate-result.json) into a per-plugin table.

Usage: python -I scripts/eval_summary.py --plugin <name> --model <model> [--with-only] <result.json>...

`--with-only` reads results of `--ablation none` runs (no without arm; a result that has one is
refused) and prints, per case and grader, how many of the runs that did not error passed it.

Several results (the native and the wsl run of one plugin) are merged by case name. A case is
labelled from its with-arm and without-arm scores: `discriminates` (with > without), `plugin worse`
(with < without), `passes in both` (equal, full score), `fails in both` (equal, below full score) or
`errored` (every run of an arm errored; the case is left out of every count). A plugin is `measured`
only when at least one case discriminates (either way); otherwise it is `not measurable`, never a
delta of 0. A partial result (`partial: true`) gives status `partial` and no pass-rate numbers.
A result of the wrong shape gives status `refused` with a reason line.

Every run that did not error must show its plugins loaded: its trace (`tracePath`, kept only by
`claude plugin eval --keep-temp`; delete the kept folders after the summary) has an init event with
no `plugin_errors`, and in the with arm that event lists the plugin. Otherwise the result is
refused: a case whose plugin failed to load (`dependency-unsatisfied`) runs both arms without it.

Exit: 1 on refused, 2 on partial, else 0.
"""

import argparse
import json
import sys
from pathlib import Path

EPS = 1e-9


class Refused(Exception):
    pass


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _runs(case, arm):
    arms = case.get("arms")
    if arms is None:
        return None
    if not isinstance(arms, dict):
        raise Refused(f"case '{case['name']}': arms is not an object")
    runs = arms.get(arm)
    if runs is None:
        return None
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        raise Refused(f"case '{case['name']}': arms.{arm} is not a list of runs")
    return runs


def _case_name(raw):
    if not isinstance(raw, dict):
        raise Refused("a case is not an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise Refused("a case has no name")
    return name


def _read_case(raw, partial):
    name = _case_name(raw)
    agg = raw.get("aggregates")
    if not isinstance(agg, dict) or not _number(agg.get("score")):
        raise Refused(f"case '{name}': aggregates.score is missing or not a number")
    without = agg.get("scoreWithout")
    if without is not None and not _number(without):
        raise Refused(f"case '{name}': aggregates.scoreWithout is not a number")
    if without is None and not partial:
        raise Refused(f"case '{name}': no without-arm score (run with the with-without ablation, "
                      "or pass --with-only for an --ablation none result)")
    with_runs, without_runs = _runs(raw, "with"), _runs(raw, "without")
    errors_with = sum(1 for r in with_runs or [] if r.get("error"))
    errors_without = sum(1 for r in without_runs or [] if r.get("error"))
    all_errored = bool(with_runs) and errors_with == len(with_runs)
    all_errored = all_errored or (bool(without_runs) and errors_without == len(without_runs))
    return {
        "name": name,
        "with_score": agg["score"],
        "without_score": without,
        "pass_with": agg.get("passRate"),
        "pass_without": agg.get("passRateWithout"),
        "errored_runs": errors_with + errors_without,
        "all_errored": all_errored,
    }


def _read_with_only(raw, partial):
    name = _case_name(raw)
    agg = raw.get("aggregates")
    if not isinstance(agg, dict):
        raise Refused(f"case '{name}': aggregates is missing or not an object")
    if agg.get("scoreWithout") is not None or _runs(raw, "without"):
        raise Refused(f"case '{name}' has a without-arm score (--with-only reads an --ablation none result)")
    runs = _runs(raw, "with") or []
    counts, errored = {}, 0
    for i, run in enumerate(runs):
        if run.get("error"):
            errored += 1
            continue
        graders = run.get("graders")
        if not isinstance(graders, list) or not graders or not all(
                isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]
                and isinstance(g.get("passed"), bool) for g in graders):
            raise Refused(f"case '{name}' with run {i + 1}: graders is not a non-empty list of named pass/fail results")
        for g in graders:
            passed, total = counts.get(g["name"], (0, 0))
            counts[g["name"]] = (passed + g["passed"], total + 1)
    return {"name": name, "graders": counts, "errored_runs": errored,
            "all_errored": bool(runs) and errored == len(runs)}


def _label(c):
    if c["all_errored"]:
        return "errored"
    delta = c["with_score"] - c["without_score"]
    if delta > EPS:
        return "discriminates"
    if delta < -EPS:
        return "plugin worse"
    return "passes in both" if c["with_score"] >= 1 - EPS else "fails in both"


def _summarise(results, read_case=_read_case):
    if not isinstance(results, list) or not results:
        raise Refused("no result given")
    cases, seen, cost, partial_reasons = [], set(), 0.0, []
    for r in results:
        if not isinstance(r, dict):
            raise Refused("a result is not an object")
        if "schemaVersion" not in r:
            raise Refused("a result has no schemaVersion")
        raw_cases = r.get("cases")
        if not isinstance(raw_cases, list):
            raise Refused("a result's cases is not a list")
        if not raw_cases:
            raise Refused("a result has no cases")
        partial = r.get("partial") is True
        if partial:
            partial_reasons.append(str(r.get("partialReason") or "unknown reason"))
        if _number(r.get("costUsd")):
            cost += r["costUsd"]
        for raw in raw_cases:
            c = read_case(raw, partial)
            if c["name"] in seen:
                raise Refused(f"case '{c['name']}' appears in more than one result")
            seen.add(c["name"])
            cases.append(c)
    return cases, cost, partial_reasons


def summarise(results):
    summary = {"status": "refused", "reason": "", "cost": 0.0, "cases": [], "plugin_label": None,
               "counted": 0, "discriminating": 0}
    try:
        cases, cost, partial_reasons = _summarise(results)
    except Refused as err:
        summary["reason"] = str(err)
        return summary
    summary["cost"] = cost
    rows = []
    for c in cases:
        if c["without_score"] is None:
            continue  # a partial run stopped before this case's baseline arm
        label = _label(c)
        rows.append({"name": c["name"], "with_score": c["with_score"], "without_score": c["without_score"],
                     "delta": c["with_score"] - c["without_score"], "label": label,
                     "errored_runs": c["errored_runs"], "_c": c})
    summary["cases"] = [{k: v for k, v in row.items() if k != "_c"} for row in rows]
    counted = [row for row in rows if row["label"] != "errored"]
    summary["counted"] = len(counted)
    summary["discriminating"] = sum(1 for row in counted if row["label"] in ("discriminates", "plugin worse"))
    if partial_reasons:
        summary["status"] = "partial"
        summary["plugin_label"] = "partial"
        summary["reason"] = "partial result: " + ", ".join(partial_reasons)
        return summary
    summary["status"] = "complete"
    if summary["discriminating"] == 0:
        summary["plugin_label"] = "not measurable"
        summary["reason"] = "no case discriminates between the arms"
        return summary
    summary["plugin_label"] = "measured"
    summary["pass_rate_with"] = sum(row["_c"]["pass_with"] or 0 for row in counted) / len(counted)
    summary["pass_rate_without"] = sum(row["_c"]["pass_without"] or 0 for row in counted) / len(counted)
    return summary


def summarise_with_only(results):
    summary = {"status": "refused", "reason": "", "cost": 0.0, "cases": [], "counted": 0}
    try:
        cases, cost, partial_reasons = _summarise(results, _read_with_only)
    except Refused as err:
        summary["reason"] = str(err)
        return summary
    summary.update(cost=cost, cases=cases, counted=sum(1 for c in cases if not c["all_errored"]))
    if partial_reasons:
        summary.update(status="partial", reason="partial result: " + ", ".join(partial_reasons))
    else:
        summary["status"] = "complete"
    return summary


KEEP_TEMP = "run the eval with --keep-temp"


def _init_event(path):
    """(init event, None) from a run's trace, or (None, reason)."""
    if not isinstance(path, str) or not path:
        return None, f"no tracePath ({KEEP_TEMP})"
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as err:
        return None, f"cannot read trace {path} ({KEEP_TEMP}): {err.strerror or err}"
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event, None
    return None, f"trace {path} has no init event"


def _describe(errors):
    if not isinstance(errors, list):
        return repr(errors)
    return ", ".join(f"{e.get('plugin')} {e.get('type')}" if isinstance(e, dict) else repr(e) for e in errors)


def _loaded_names(plugins):
    if not isinstance(plugins, list):
        return []
    return [p.get("name") for p in plugins if isinstance(p, dict)]


def load_problems(results, plugin):
    """One line per run whose session did not load its plugins; runs that errored are skipped."""
    problems = []
    for r in results:
        for c in r["cases"]:
            for arm in ("with", "without"):
                runs = _runs(c, arm) or []
                scored = arm == "with" or c["aggregates"].get("scoreWithout") is not None
                if scored and not runs:
                    problems.append(f"case '{c['name']}' {arm} arm has no runs, so no trace shows its plugins")
                for i, run in enumerate(runs):
                    if run.get("error"):
                        continue
                    where = f"case '{c['name']}' {arm} run {i + 1}"
                    init, reason = _init_event(run.get("tracePath"))
                    if reason:
                        problems.append(f"{where}: {reason}")
                        continue
                    if init.get("plugin_errors"):
                        problems.append(f"{where}: plugin_errors {_describe(init['plugin_errors'])}")
                    if arm == "with" and plugin not in _loaded_names(init.get("plugins")):
                        problems.append(f"{where}: plugin '{plugin}' is not loaded")
    return problems


def _percent(value):
    return f"{round(value * 100)}%"


def render(plugin, model, s):
    lines = [f"## {plugin} ({model})", ""]
    if s["status"] == "refused":
        lines.append(f"refused: {s['reason']}")
        return "\n".join(lines)
    lines += ["| case | with | without | delta | label |", "|---|---|---|---|---|"]
    for c in s["cases"]:
        note = f" ({c['errored_runs']} errored run(s))" if c["errored_runs"] else ""
        lines.append(
            f"| {c['name']} | {c['with_score']:.2f} | {c['without_score']:.2f} | {c['delta']:+.2f} | {c['label']}{note} |"
        )
    lines.append("")
    if s["plugin_label"] == "measured":
        lines.append(f"Plugin: measured, pass rate with {_percent(s['pass_rate_with'])}, "
                     f"without {_percent(s['pass_rate_without'])}")
    elif s["plugin_label"] == "partial":
        lines.append(f"Plugin: {s['reason']}")
    else:
        lines.append(f"Plugin: not measurable ({s['reason']})")
    lines.append(f"cost ${s['cost']:.2f}")
    lines.append(f"# cases {s['counted']}, discriminating {s['discriminating']}")
    return "\n".join(lines)


def render_with_only(plugin, model, s):
    if s["status"] == "refused":
        return render(plugin, model, s)
    lines = [f"## {plugin} ({model}, with arm only)", "", "| case | grader | passed |", "|---|---|---|"]
    for c in s["cases"]:
        if c["all_errored"]:
            lines.append(f"| {c['name']} | every run errored | - |")
            continue
        for grader, (passed, total) in c["graders"].items():
            lines.append(f"| {c['name']} | {grader} | {passed}/{total} |")
    lines.append("")
    for c in s["cases"]:
        if c["errored_runs"] and not c["all_errored"]:
            lines.append(f"case '{c['name']}': {c['errored_runs']} errored run(s) left out of the counts")
    if s["status"] == "partial":
        lines.append(f"Plugin: {s['reason']}")
    lines.append(f"cost ${s['cost']:.2f}")
    lines.append(f"# cases {s['counted']}")
    return "\n".join(lines)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--with-only", action="store_true", help="read --ablation none results")
    ap.add_argument("results", nargs="+")
    args = ap.parse_args(argv)
    summarise_fn, render_fn = (summarise_with_only, render_with_only) if args.with_only else (summarise, render)
    loaded = []
    for path in args.results:
        try:
            loaded.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as err:
            print(render(args.plugin, args.model, {"status": "refused", "reason": f"cannot read {path}: {err}"}))
            return 1
    summary = summarise_fn(loaded)
    if summary["status"] != "refused":
        problems = load_problems(loaded, args.plugin)
        if problems:
            summary = {"status": "refused",
                       "reason": f"{len(problems)} plugin load problem(s): " + "; ".join(problems)}
    print(render_fn(args.plugin, args.model, summary))
    return {"refused": 1, "partial": 2}.get(summary["status"], 0)


if __name__ == "__main__":
    sys.exit(main())
