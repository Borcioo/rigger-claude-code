"""Summarise `claude plugin eval` results (aggregate-result.json) into a per-plugin table.

Usage: python -I scripts/eval_summary.py --plugin <name> --model <model> <result.json>...

Several results (the native and the wsl run of one plugin) are merged by case name. A case is
labelled from its with-arm and without-arm scores: `discriminates` (with > without), `plugin worse`
(with < without), `passes in both` (equal, full score), `fails in both` (equal, below full score) or
`errored` (every run of an arm errored; the case is left out of every count). A plugin is `measured`
only when at least one case discriminates (either way); otherwise it is `not measurable`, never a
delta of 0. A partial result (`partial: true`) gives status `partial` and no pass-rate numbers.
A result of the wrong shape gives status `refused` with a reason line.

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


def _read_case(raw, partial):
    if not isinstance(raw, dict):
        raise Refused("a case is not an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise Refused("a case has no name")
    agg = raw.get("aggregates")
    if not isinstance(agg, dict) or not _number(agg.get("score")):
        raise Refused(f"case '{name}': aggregates.score is missing or not a number")
    without = agg.get("scoreWithout")
    if without is not None and not _number(without):
        raise Refused(f"case '{name}': aggregates.scoreWithout is not a number")
    if without is None and not partial:
        raise Refused(f"case '{name}': no without-arm score (run with the with-without ablation)")
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


def _label(c):
    if c["all_errored"]:
        return "errored"
    delta = c["with_score"] - c["without_score"]
    if delta > EPS:
        return "discriminates"
    if delta < -EPS:
        return "plugin worse"
    return "passes in both" if c["with_score"] >= 1 - EPS else "fails in both"


def _summarise(results):
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
            c = _read_case(raw, partial)
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


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("results", nargs="+")
    args = ap.parse_args(argv)
    loaded = []
    for path in args.results:
        try:
            loaded.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as err:
            print(render(args.plugin, args.model, {"status": "refused", "reason": f"cannot read {path}: {err}"}))
            return 1
    summary = summarise(loaded)
    print(render(args.plugin, args.model, summary))
    return {"refused": 1, "partial": 2}.get(summary["status"], 0)


if __name__ == "__main__":
    sys.exit(main())
