"""Corpus check for shell-guard: mined Claude Code tool calls, judged against the rules.

python -I corpus_check.py --corpus FILE [--corpus FILE ...]
    FILE: JSON lines {"tool", "cmd", ...}. A line with an "error" key is a failure: when it names a
    "rule", that rule must still deny it. A line without "error" is a success: unless it has
    "flagged_as", no rule of the full guard may deny it (the git rules that stop a call which ran but
    did damage are listed, not counted). The installed handlers run a subset of the full guard's
    rules, git first, so a success they deny is already counted here.
    Exit 0: no regression, no false positive. Exit 1: any of them. Exit 2: no --corpus, a file that
    cannot be read, a line that is not a JSON object with "tool" and "cmd", or no entry at all.

Corpora mined from a user's own transcripts never ship; the tests run this on
fixtures/corpus.jsonl.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))
import shell_guard  # noqa: E402

# A success these deny is listed, not failed: they stop calls that ran but did damage or nothing.
LISTED_GIT_RULES = {"ps-git-commit-stdin", "git-restore-discards"}


def load(paths):
    """(entries, unparsed line count). Raises OSError for a file that cannot be read."""
    entries, unparsed = [], 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    unparsed += 1
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("tool"), str) and isinstance(entry.get("cmd"), str):
                    entries.append(entry)
                else:
                    unparsed += 1
    return entries, unparsed


def first_deny(fn, entry):
    return next((f for f in fn(entry["tool"], entry["cmd"]) if f.deny), None)


def evaluate(entries, unparsed):
    """Counts and the offending entries; ok only with entries, no unparsed line, no regression and no
    false positive."""
    failures = [x for x in entries if "error" in x]
    judged = [x for x in failures if x.get("rule")]
    regressed = [x for x in judged if getattr(first_deny(shell_guard.check, x), "rule", None) != x["rule"]]
    successes = [x for x in entries if "error" not in x]
    flagged = [x for x in successes if x.get("flagged_as")]
    ok = [x for x in successes if not x.get("flagged_as")]
    new_fp, git_listed = [], []
    for x in ok:
        f = first_deny(shell_guard.check, x)
        if f is None:
            continue
        (git_listed if f.rule in LISTED_GIT_RULES else new_fp).append((x, f.rule))
    counts = {"entries": len(entries), "unparsed": unparsed, "failures": len(failures), "judged": len(judged),
              "still_denied": len(judged) - len(regressed), "regressed": len(regressed),
              "successes": len(successes), "flagged": len(flagged),
              "allowed": len(ok) - len(new_fp) - len(git_listed), "git_listed": len(git_listed),
              "new_fp": len(new_fp)}
    is_ok = bool(entries) and not unparsed and not regressed and not new_fp
    return {"counts": counts, "ok": is_ok, "regressed": regressed, "new_fp": new_fp, "git_listed": git_listed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", action="append", required=True, help="a corpus JSON-lines file; repeatable")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        entries, unparsed = load(args.corpus)
    except (OSError, UnicodeDecodeError) as exc:
        print("# cannot read the corpus: %s: %s" % (type(exc).__name__, exc))
        return 2
    result = evaluate(entries, unparsed)
    c = result["counts"]
    print("failures: %d/%d judged still denied by the same rule (%d without a rule)"
          % (c["still_denied"], c["judged"], c["failures"] - c["judged"]))
    print("successes: %d/%d allowed (%d flagged skipped, git rules stopped %d)"
          % (c["allowed"], c["successes"] - c["flagged"], c["flagged"], c["git_listed"]))
    for x, rule in result["git_listed"]:
        print("  git: %s %s %r" % (rule, x["tool"], x["cmd"][:110]))
    for x in result["regressed"][:5]:
        print("  regressed: %s %r (want %s)" % (x["tool"], x["cmd"][:100], x["rule"]))
    for x, rule in result["new_fp"][:5]:
        print("  false positive: %s %s %r" % (rule, x["tool"], x["cmd"][:100]))
    print("# corpus: %d entries, %d unparsed, %d regressed, %d false positives"
          % (c["entries"], c["unparsed"], c["regressed"], c["new_fp"]))
    if not entries or unparsed:
        return 2
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
