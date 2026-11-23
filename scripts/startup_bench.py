"""Startup cost of the rules handlers: release 0.4.0's 19 SessionStart handlers against the assembler's parts.

Usage: python -I -X utf8 scripts/startup_bench.py [--runs N]

Each run starts one side's startup handlers at once, through a real POSIX sh as Claude Code does, and
times the slowest of them (the session waits for all). "Old" is the golden fixture's 19 rules handlers
(tests/fixtures/golden-34e7713); "new" is rigger-core's 6 part.sh handlers; "new+checks" adds the 5
core_check.sh handlers that also run at a startup SessionStart. All on the windows layout (rigger-windows
installed) with an empty rules.d, from a temp folder laid out like the plugin cache, with a config dir
that lists the plugins as installed and enabled. One unmeasured run per side comes first; the sides
alternate within each run.

Prints, after any detail lines:
  # startup median old <ms> new <ms> delta <ms> max new <ms> runs <n>
  # startup median new+checks <ms> max <ms> runs <n>
On any error it prints `# startup bench failed: <reason>` instead. It always exits 0.
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.dont_write_bytecode = True

import golden_baseline as gb  # noqa: E402

MARKET = "rigger"
MODULES = ("rigger-core", "rigger-rules", "rigger-lessons", "rigger-talk", "rigger-wp", "rigger-windows", "rigger-panel")
OLD_HANDLERS = 19
PARTS = 6
CHECKS = 5
NOT_LOADED = b"Rigger rules not loaded"
TIMEOUT = 60

Job = Tuple[Path, str]  # (CLAUDE_PLUGIN_ROOT, command)


def startup_commands(root: Path, needle: str) -> List[str]:
    """Commands of a plugin's SessionStart groups that fire on startup and contain `needle`."""
    hooks = json.loads((root / "hooks" / "hooks.json").read_bytes().decode("utf-8"))["hooks"]
    found = []
    for group in hooks.get("SessionStart", []):
        if gb._fires(group.get("matcher"), "startup"):
            found.extend(h["command"] for h in group.get("hooks", []) if needle in h.get("command", ""))
    return found


class Layout:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.project = tmp / "project"
        self.old = tmp / "old" / "cache" / MARKET
        self.new = tmp / "new" / "cache" / MARKET
        for folder in (self.home, self.project):
            folder.mkdir(parents=True)
        for plugin in gb.PLUGINS_WITH_RULES:
            shutil.copytree(str(gb.FIXTURE / "plugins" / plugin), str(self.old / plugin))
        for plugin in MODULES:
            shutil.copytree(str(ROOT / "plugins" / plugin), str(self.new / plugin),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.old_config = self.config("old", {})
        installed = {"%s@%s" % (m, MARKET): [{"scope": "user", "installPath": str(self.new / m), "version": "bench"}]
                     for m in MODULES}
        self.new_config = self.config("new", installed)
        self.old_jobs = [(self.old / p, c) for p in gb.PLUGINS_WITH_RULES
                         for c in gb.handler_commands(self.old / p, "SessionStart", "startup")]
        self.part_jobs = [(self.new / "rigger-core", c) for c in startup_commands(self.new / "rigger-core", "part.sh")]
        self.check_jobs = [(self.new / p, c) for p in MODULES if (self.new / p / "hooks" / "core_check.sh").is_file()
                           for c in startup_commands(self.new / p, "core_check.sh")]
        counts = (len(self.old_jobs), len(self.part_jobs), len(self.check_jobs))
        if counts != (OLD_HANDLERS, PARTS, CHECKS):
            raise RuntimeError("handlers found old %d, parts %d, checks %d; expected %d, %d, %d"
                               % (counts + (OLD_HANDLERS, PARTS, CHECKS)))

    def config(self, side: str, installed: dict) -> Path:
        config = self.tmp / "config" / side
        (config / "rigger" / "rules.d").mkdir(parents=True)
        (config / "plugins").mkdir()
        (config / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": installed}), encoding="utf-8")
        enabled = {"enabledPlugins": {key: True for key in installed}}
        (config / "settings.json").write_text(json.dumps(enabled), encoding="utf-8")
        return config


def run_batch(sh: str, jobs: List[Job], config: Path, layout: Layout) -> Tuple[float, List[bytes]]:
    """Start every job at once; return (ms until the slowest one ended, outputs in job order)."""
    stdin = gb.hook_input("SessionStart", "startup", None, "bench-" + uuid.uuid4().hex, layout.project)
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
    # HOME alone: a Store Python derives its own path from USERPROFILE.
    base.update(HOME=str(layout.home), CLAUDE_CONFIG_DIR=str(config), CLAUDE_PROJECT_DIR=str(layout.project))
    base["PATH"] = os.pathsep.join([str(Path(sh).parent), base.get("PATH", "")])
    outputs = [b""] * len(jobs)
    ends = [0.0] * len(jobs)
    errors = []

    def one(index: int, root: Path, command: str) -> None:
        env = dict(base, CLAUDE_PLUGIN_ROOT=gb.root_text(root))
        try:
            done = subprocess.run([sh, "-c", command], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, cwd=str(layout.project), timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            errors.append("%s timed out after %d s" % (command, TIMEOUT))
            return
        ends[index] = time.perf_counter()
        if done.returncode != 0 or done.stderr:
            errors.append("%s exited %d: %r" % (command, done.returncode, done.stderr[:300]))
        outputs[index] = done.stdout

    threads = [threading.Thread(target=one, args=(i, root, command)) for i, (root, command) in enumerate(jobs)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise RuntimeError(errors[0])
    return (max(ends) - started) * 1000.0, outputs


def check_new(outputs: List[bytes], layout: Layout) -> None:
    """A part side that failed, or found fewer modules, would time a shorter path: every module with rules
    prints its root line and nothing says the rules were not loaded."""
    joined = b"".join(outputs)
    if NOT_LOADED in joined:
        line = next(l for l in joined.splitlines() if NOT_LOADED in l)
        raise RuntimeError(line.decode("utf-8", "replace"))
    lines = set(joined.splitlines())
    missing = [m for m in gb.PLUGINS_WITH_RULES
               if ("Plugin root: " + gb.root_text(layout.new / m)).encode("utf-8") not in lines]
    if missing:
        raise RuntimeError("no Plugin root line for " + ", ".join(missing))


def bench(runs: int) -> List[str]:
    sh = gb.find_sh()
    if sh is None:
        raise RuntimeError("no POSIX sh found (Windows needs Git for Windows)")
    tmp = Path(tempfile.mkdtemp(prefix="startup-bench-"))
    try:
        layout = Layout(tmp)
        sides = {"old": (layout.old_jobs, layout.old_config),
                 "new": (layout.part_jobs, layout.new_config),
                 "checks": (layout.part_jobs + layout.check_jobs, layout.new_config)}
        times = {name: [] for name in sides}  # type: Dict[str, List[float]]
        for run in range(runs + 1):
            for name, (jobs, config) in sides.items():
                ms, outputs = run_batch(sh, jobs, config, layout)
                if name != "old":
                    check_new(outputs, layout)
                if run:
                    times[name].append(ms)
    finally:
        shutil.rmtree(str(tmp), True)
    old = round(statistics.median(times["old"]))
    new = round(statistics.median(times["new"]))
    return ["# startup median old %d new %d delta %d max new %d runs %d"
            % (old, new, new - old, round(max(times["new"])), runs),
            "# startup median new+checks %d max %d runs %d"
            % (round(statistics.median(times["checks"])), round(max(times["checks"])), runs)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Time the rules handlers at session startup.")
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.runs < 1:
            raise ValueError("--runs must be at least 1")
        lines = bench(args.runs)
    except Exception as error:  # the CI step must never fail the job: one line says why
        print("# startup bench failed: %s" % ("%s: %s" % (type(error).__name__, error)).replace("\n", " "))
        return 0
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
