"""Gate lab report: precision from labels, and which trials are due. Offline; run it as replay.py is run.

  report.py --data <dir>          each replayed gate's verdicts, then precision per gate and label source, tuned on the
                                  older half, reported on the newer
  report.py --data <dir> --due    trials whose review date has come, with their verdict

`unknown/hidden` is a check the transcript shows ran after the last change with a result the reader cannot read. It
measures the reader, not the agent, so it is its own column: never plain `unknown`, never credit.

`<data>/gate-lab/` holds labels.jsonl, trials.json and the shadow-<gate>.json files `replay.py --from-shadow` writes:
{"id","gate","real","source":"user|model","note"}, one label per line, `id` as replay prints it. A label's time is its
fire's time, so a label with no fire counts for nothing and is reported. A verdict reads user labels only.

The first run of either tool copies trials.template.json to trials.json, starting the trials that day.
"""
import argparse
import calendar
import datetime
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "trials.template.json")
ROW_DIRS = {"commit": ("commit-gate", "rows"), "stop": ("turn-gate", "rows")}
SOURCES = ("user", "model")
HIDDEN = "unknown/hidden"
CREDIT = {"commit": "fresh", "stop": "verified"}
NOT_IN_SCOPE = ("ungated", "out-of-scope", "dry-run")
ROW_NOUN = {"commit": "commits", "stop": "closings"}
REVIEW_DAYS = 14
MIN_LABELS = 30
MIN_PROJECTS = 2
MIN_CATCHES = 2
FLOOR = 0.10
MAX_WRONG = 0.05


def today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def epoch(day):
    return calendar.timegm(datetime.date.fromisoformat(day).timetuple())


def lab_path(data, *parts):
    return os.path.join(data, "gate-lab", *parts)


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=1)
        handle.write("\n")


def write_shadow(data, gate, summary):
    write_json(lab_path(data, "shadow-%s.json" % gate), summary)


def read_shadow(data, gate):
    try:
        with open(lab_path(data, "shadow-%s.json" % gate), encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def ensure_trials(data):
    path = lab_path(data, "trials.json")
    if not os.path.exists(path):
        with open(TEMPLATE, encoding="utf-8") as handle:
            trials = json.load(handle)
        started = today()
        for trial in trials:
            trial["started"] = started.isoformat()
            trial["review"] = (started + datetime.timedelta(days=REVIEW_DAYS)).isoformat()
        write_json(path, trials)
    return path


def verdict_column(row):
    """The column a row counts in."""
    return HIDDEN if row.get("verdict") == "unknown" and row.get("reason") == "hidden" else row.get("verdict")


def wilson(hits, total, z=1.96):
    share = hits / total
    denom = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denom
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def read_labels(data):
    """Label rows as written, in file order. A label that cannot be trusted stops the run."""
    labels, seen = [], set()
    with open(lab_path(data, "labels.jsonl"), encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row.get("id"), str) or row.get("gate") not in ROW_DIRS \
                    or not isinstance(row.get("real"), bool) or row.get("source") not in SOURCES:
                sys.exit("labels.jsonl:%d: needs id, gate, real and source" % number)
            if row["id"] in seen:
                sys.exit("labels.jsonl:%d: duplicate id %s" % (number, row["id"]))
            seen.add(row["id"])
            labels.append(row)
    return labels


def labelled(labels, summary, gate, source):
    """(this gate's labels from `source` with their fire's ts and repo8, labels with no fire)."""
    fires = {fire["id"]: fire for fire in summary["fires"]}
    rows, orphans = [], 0
    for label in labels:
        if label["gate"] != gate or label["source"] != source:
            continue
        if label["id"] in fires:
            rows.append(dict(label, ts=fires[label["id"]]["ts"], repo8=fires[label["id"]]["repo8"]))
        else:
            orphans += 1
    return rows, orphans


def split(rows):
    """(older half, newer half) by fire time; the odd row goes to the newer."""
    ordered = sorted(rows, key=lambda row: (row["ts"], row["id"]))
    middle = len(ordered) // 2
    return ordered[:middle], ordered[middle:]


def precision(rows):
    return sum(1 for row in rows if row["real"]) / len(rows)


def wrong_denies(newer, summary):
    """Estimated commits the gate would deny for reasons the labels reject: the newer half's wrong share of all
    fires."""
    return (1 - precision(newer)) * len(summary["fires"])


def describe(name, rows):
    if not rows:
        return "  %s: no labels" % name
    hits = sum(1 for row in rows if row["real"])
    low, high = wilson(hits, len(rows))
    return "  %s: %d of %d real, precision %.1f%% (%.1f-%.1f%%)" % (
        name, hits, len(rows), 100 * hits / len(rows), 100 * low, 100 * high)


def print_verdicts(gate, summary):
    """The verdict table, credit and the hidden rate. A summary from before the table has nothing to count."""
    verdicts = summary.get("verdicts")
    if verdicts is None:
        print("%s: no verdict table in this summary: run replay.py --from-shadow" % gate)
        return
    print("%s verdicts: %s" % (gate, ", ".join("%s %d" % item for item in sorted(verdicts.items()))))
    in_scope = sum(count for name, count in verdicts.items() if name not in NOT_IN_SCOPE)
    if not in_scope:
        print("%s: 0 in-scope %s" % (gate, ROW_NOUN[gate]))
        return
    credit, hidden = verdicts.get(CREDIT[gate], 0), verdicts.get(HIDDEN, 0)
    print("%s credit: %s %d of %d in-scope %s (%.1f%%)" % (
        gate, CREDIT[gate], credit, in_scope, ROW_NOUN[gate], 100 * credit / in_scope))
    print("%s hidden: %s %d of %d in-scope %s (%.1f%%)" % (
        gate, HIDDEN, hidden, in_scope, ROW_NOUN[gate], 100 * hidden / in_scope))
    if hidden * 3 > in_scope:
        print("%s: over a third of the in-scope %s ran a check the reader cannot read, so this gate is measuring the "
              "reader, not the agent" % (gate, ROW_NOUN[gate]))


def print_gate(gate, labels, summary):
    print("%s: fires %d, total %d, unfinished %d, unmatched %d, shadow read %s" % (
        gate, len(summary["fires"]), summary["total"], summary["unfinished"], summary["unmatched"], summary["written"]))
    for source in SOURCES:
        rows, orphans = labelled(labels, summary, gate, source)
        older, newer = split(rows)
        print("%s %s: %d labels, %d projects, %d with no fire" % (
            gate, source, len(rows), len({row["repo8"] for row in rows}), orphans))
        print(describe("older half (tuned)", older))
        print(describe("newer half (reported)", newer))
        if gate == "commit" and newer and summary["total"]:
            wrong = wrong_denies(newer, summary)
            print("  wrong denies over all commits: %.1f of %d (%.1f%%)" % (
                wrong, summary["total"], 100 * wrong / summary["total"]))


def row_count(data, gate, started):
    """Row files the gate wrote since `started`, empty ones included: a worker that ran means the gate is wired."""
    folder = os.path.join(data, *ROW_DIRS[gate])
    if not os.path.isdir(folder):
        return 0
    edge = epoch(started)
    return sum(1 for entry in os.scandir(folder) if entry.stat().st_mtime >= edge)


def commit_verdict(rows, summary):
    if len(rows) < MIN_LABELS:
        return "no-case"
    _, newer = split(rows)
    if precision(newer) < FLOOR:
        return "KILL"
    projects = len({row["repo8"] for row in rows})
    keep = wrong_denies(newer, summary) / summary["total"] <= MAX_WRONG and projects >= MIN_PROJECTS
    return "KEEP" if keep else "no-case"


def stop_verdict(rows, trial):
    last = (datetime.date.fromisoformat(trial["started"]) + datetime.timedelta(days=REVIEW_DAYS)).isoformat()
    if sum(1 for row in rows if row["real"] and row["ts"][:10] <= last) >= MIN_CATCHES:
        return "KEEP"
    if len(rows) < MIN_LABELS:
        return "no-case"
    _, newer = split(rows)
    return "KEEP" if precision(newer) >= FLOOR else "KILL"


def verdict(trial, data, labels):
    gate = trial["gate"]
    if not row_count(data, gate, trial["started"]):
        return "not-wired"
    summary = read_shadow(data, gate)
    if summary is None:
        return "no-case"
    rows, _ = labelled(labels, summary, gate, "user")
    return commit_verdict(rows, summary) if gate == "commit" else stop_verdict(rows, trial)


def due(data):
    with open(ensure_trials(data), encoding="utf-8") as handle:
        trials = json.load(handle)
    if not trials:
        sys.exit("0 trials read")
    labels = read_labels(data) if os.path.exists(lab_path(data, "labels.jsonl")) else []
    now = today()
    found = [trial for trial in trials if datetime.date.fromisoformat(trial["review"]) <= now]
    for trial in found:
        print("due %s %s review %s verdict %s" % (
            trial["name"], trial["gate"], trial["review"], verdict(trial, data, labels)))
    print("# trials %d, due %d" % (len(trials), len(found)))
    return 0


def summarise(data):
    ensure_trials(data)
    for gate in sorted(ROW_DIRS):
        summary = read_shadow(data, gate)
        if summary is not None:
            print_verdicts(gate, summary)
    if not os.path.exists(lab_path(data, "labels.jsonl")):
        print("0 labels read", file=sys.stderr)
        return 2
    labels = read_labels(data)
    if not labels:
        print("0 labels read", file=sys.stderr)
        return 2
    for gate in sorted({label["gate"] for label in labels}):
        summary = read_shadow(data, gate)
        if summary is None:
            print("labels for %s but no shadow-%s.json: run replay.py --from-shadow" % (gate, gate), file=sys.stderr)
            return 2
        print_gate(gate, labels, summary)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Precision and due trials of the gate lab.")
    parser.add_argument("--data", required=True, help="the plugin's data folder")
    parser.add_argument("--due", action="store_true", help="print due trials")
    args = parser.parse_args(argv)
    return due(args.data) if args.due else summarise(args.data)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
