"""Stand-in for `claude -p` in the lessons batch tests, plus the helpers those tests share. Never reaches the network.

As a script (given through HARNESS_LESSONS_CLAUDE): reads the prompt on stdin and recognises the call by its content
(TRIAGE_MARK or PROPOSAL_MARK), then follows the step for that call in the JSON file $LESSONS_STUB_PLAN:
  structured  value printed as `structured_output`;  result  text printed as `result`;  stdout  raw text printed
  instead of the JSON;  crlf  pretty-printed JSON with CRLF line ends;  exit  exit code;  started  a file written
  on start;  release  a file waited for (60 s at most) before answering;  grandchild  a file that gets
  "<own pid> <grandchild pid>" after starting a grandchild that sleeps;  sleep  seconds to sleep.
Every call appends {stage, argv, cwd, env (CLAUDE*/HARNESS* keys), prompt} as one JSON line to the plan's `log`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TRIAGE_MARK = "You triage"
PROPOSAL_MARK = "Write the exact rule or skill edit"
STRIPPED = ("CLAUDE_", "ANTHROPIC_", "HARNESS_")


def clean_env(home: Path, **extra) -> dict:
    """os.environ without CLAUDE_*, ANTHROPIC_* and HARNESS_* keys, with HOME, USERPROFILE and CLAUDE_CONFIG_DIR
    under `home`, PYTHONUTF8 cleared, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIPPED) and k.upper() != "PYTHONUTF8"}
    env.update(HOME=str(home), USERPROFILE=str(home), CLAUDE_CONFIG_DIR=str(home / ".claude"))
    env.update(extra)
    return env


def stub_argv() -> list:
    return [sys.executable, "-I", str(Path(__file__).resolve())]


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_for(check, seconds: float = 30.0) -> bool:
    """Polls check() until it is true or `seconds` pass; returns its last value."""
    end = time.monotonic() + seconds
    while not check():
        if time.monotonic() > end:
            return bool(check())
        time.sleep(0.05)
    return True


def main() -> int:
    prompt = sys.stdin.buffer.read().decode("utf-8")
    plan = json.loads(Path(os.environ["LESSONS_STUB_PLAN"]).read_text(encoding="utf-8"))
    stage = "triage" if TRIAGE_MARK in prompt else "proposal" if PROPOSAL_MARK in prompt else "unknown"
    step = plan.get(stage) or {}
    if plan.get("log"):
        env = {k: v for k, v in os.environ.items() if k.upper().startswith(("CLAUDE", "HARNESS"))}
        with open(plan["log"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"stage": stage, "argv": sys.argv[1:], "cwd": os.getcwd(), "env": env,
                                     "prompt": prompt}) + "\n")
    if step.get("started"):
        Path(step["started"]).write_text(str(os.getpid()), encoding="utf-8")
    if step.get("release"):
        wait_for(Path(step["release"]).exists, 60)
    if step.get("grandchild"):
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
        Path(step["grandchild"]).write_text("%d %d" % (os.getpid(), child.pid), encoding="utf-8")
    if step.get("sleep"):
        time.sleep(step["sleep"])
    if "stdout" in step:
        text = step["stdout"]
    else:
        payload = {"type": "result", "result": step.get("result", ""), "total_cost_usd": 0.01,
                   "modelUsage": {"stub-model": {"inputTokens": 10, "outputTokens": 5}}}
        if "structured" in step:
            payload["structured_output"] = step["structured"]
        text = json.dumps(payload, indent=2 if step.get("crlf") else None)
        if step.get("crlf"):
            text = text.replace("\n", "\r\n")
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()
    return int(step.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
