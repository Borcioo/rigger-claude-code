"""A stand-in reviewer command for test_slot_run.py.

  fake_reviewer.py <mode> --record FILE --out FILE --scope FILE --base ID [--head ID] [more args...]

It first writes its argv, working directory and environment names to the record file, then acts by mode:
ok (one finding, coverage from the scope file), copy-scope (no findings, coverage from the scope file only),
bad-status, nonzero (a valid file, then exit 3), no-out, big-out (a valid file padded past 20,000 bytes),
flood (console output without end), flood-exit (a valid file, then <n> console bytes and exit 0, with
`--bytes n`), flood-err (the same as flood, on stderr), orphan (a valid file, then a grandchild that sleeps 30 s
holding the console, and this process exits 0), detached (the same, but the grandchild's console goes to the
null device), hang (starts a grandchild, then both sleep). A grandchild writes its pid to <record>.child.pid.
"""
import json
import os
import subprocess
import sys
import time


def option(name):
    args = sys.argv
    return args[args.index(name) + 1] if name in args else None


def scope_coverage(path):
    in_scope, reviewed, skipped = [], [], []
    with open(path, "rb") as handle:
        for line in handle.read().decode("utf-8").splitlines():
            verdict, name = line.split("\t", 1)
            in_scope.append(name)
            if verdict == "review":
                reviewed.append(name)
            else:
                skipped.append({"file": name, "reason": verdict[len("skip: "):]})
    return {"in_scope": in_scope, "reviewed": reviewed, "skipped": skipped}


def document(findings, status="DONE"):
    return {
        "contract": "rigger.reviewer/1",
        "status": status,
        "base": option("--base"),
        "head": option("--head") or "HEAD",
        "findings": findings,
        "coverage": scope_coverage(option("--scope")),
        "shortcuts": [],
        "shortcuts_check": "git diff --stat base..head -- tests/",
    }


ONE_FINDING = [{"id": "F1", "file": "a.py", "line": 1, "severity": "Minor", "quote": "x = 2", "title": "t"}]


def write_out(obj, pad=0):
    with open(option("--out"), "wb") as handle:
        handle.write(json.dumps(obj).encode("utf-8") + b" " * pad)


def flood(total=None):
    chunk = b"x" * 65536
    out = sys.stdout.buffer
    written = 0
    while total is None or written < total:
        piece = chunk if total is None else chunk[: total - written]
        out.write(piece)
        out.flush()
        written += len(piece)


def main():
    mode = sys.argv[1]
    record = option("--record")
    with open(record, "w", encoding="utf-8") as handle:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ), "pid": os.getpid()}, handle)
    if mode == "sleep":  # the grandchild of `hang`
        with open(record + ".pid", "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        time.sleep(float(option("--seconds") or 120))
        return 0
    if mode == "ok":
        write_out(document(ONE_FINDING))
    elif mode == "copy-scope":
        write_out(document([]))
    elif mode == "bad-status":
        write_out(document(ONE_FINDING, status="FINE"))
    elif mode == "nonzero":
        write_out(document(ONE_FINDING))
        return 3
    elif mode == "no-out":
        pass
    elif mode == "big-out":
        write_out(document(ONE_FINDING), pad=20000)
    elif mode == "flood":
        flood()
    elif mode == "flood-err":
        sys.stdout = sys.stderr
        flood()
    elif mode in ("orphan", "detached"):
        write_out(document(ONE_FINDING))
        quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "sleep", "--record", record + ".child",
                          "--seconds", "30"], **(quiet if mode == "detached" else {}))
        deadline = time.time() + 30
        while not os.path.exists(record + ".child.pid") and time.time() < deadline:
            time.sleep(0.05)
    elif mode == "flood-exit":
        write_out(document(ONE_FINDING))
        flood(int(option("--bytes")))
    elif mode == "hang":
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "sleep", "--record", record + ".child"])
        deadline = time.time() + 30
        while not os.path.exists(record + ".child.pid") and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(120)
    else:
        return 9
    return 0


if __name__ == "__main__":
    sys.exit(main())
