"""The commit rule (shadow) on real repositories, and the hook-skipping rows through the real shell and real git.

python -I -X utf8 test_commit_gate.py
Ends with `# commit-gate: <n> passed, <m> failed`.
"""
import calendar
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

if os.name != "nt":
    print("# commit-gate: skipped on %s: Windows-only (the hook guards native Windows shells; the rows run "
          "Git Bash and pwsh)" % sys.platform)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
# A fake home, so git reads none of the user's configuration; restored in main().
HOME_ENV = ("USERPROFILE", "HOME", "APPDATA")
SAVED_ENV = {name: os.environ.get(name) for name in HOME_ENV}
FAKE_HOME = tempfile.mkdtemp(prefix="sg-gate-home-")
os.environ.update(USERPROFILE=FAKE_HOME, HOME=FAKE_HOME, APPDATA=os.path.join(FAKE_HOME, "AppData", "Roaming"))
import shell_guard  # noqa: E402
from rules_common import GUARD, _bounded  # noqa: E402

# The commit rule. Most cases call commit_row on real repositories, since a hook process per case would add up; what
# depends on the process (main, imports, pipes, a race, the code page) goes through the hook.
GATES = {"checks": [r"python -I scripts[\\/]run_tests\.py"], "commit": "shadow",
         "scope_commit": ["src/**/*.py", "src/**/*.ts", "src/**/*.php"]}
CHECK_BASH, CHECK_PS = "python -I scripts/run_tests.py", "python -I scripts\\run_tests.py"
CHECK_PASSED, CHECK_FAILED = "# files 3, tests 12, failed 0", "# files 3, tests 12, failed 2"
EDITED, PASS_BEFORE, PASS_AFTER = 100, 200, 50   # seconds ago
SESSION = "5ad985ec-0000-4000-8000-000000000000"
GATE_FILES = ("src/a.py", "src/b.py", "src/c.py", "docs/n.md")
gate_templates = {}   # gates as JSON -> a finished repo: git init, add and commit are three spawns each
GATE_ROW_FIELDS = {"ts", "sid8", "use12", "repo8", "op", "verdict", "reason", "staged", "deleted", "chained",
                   "newest_age_s", "check_age_s", "reflog_age_s", "git_op", "ms"}
# The hook through its real entry point after argv[3], Python that replaces what the case needs.
HOOK_DRIVER = (
    "import runpy, sys\n"
    "guard, flag, prelude = sys.argv[1:4]\n"
    "sys.argv = [guard, flag]\n"
    "exec(prelude)\n"
    "runpy.run_path(guard, run_name='__main__')\n")
REPORT_MODULES = "import atexit\natexit.register(lambda: open(%r, 'w').write('\\n'.join(sys.modules)))\n"
# A thread start creates the file %r.
NOTE_WORKER = (
    "import threading\n"
    "real_start, note = threading.Thread.start, %r\n"
    "def noted(self):\n"
    "    open(note, 'a').close()\n"
    "    real_start(self)\n"
    "threading.Thread.start = noted\n")
# Every git call leaves one marker in the folder %r.
COUNT_GIT = (
    "import os, subprocess, tempfile\n"
    "real_run, folder = subprocess.run, %r\n"
    "def counted(args, **kw):\n"
    "    os.close(tempfile.mkstemp(dir=folder)[0])\n"
    "    return real_run(args, **kw)\n"
    "subprocess.run = counted\n")
# The row claim waits until %r handlers have noted their arrival in the folder %r; exit 3 says the others never came.
# How long they take to start is the machine's load, so the hook's wait and its clock stand still meanwhile.
CLAIM_BARRIER = (
    "import os, tempfile, threading, time\n"
    "real_open, real_join, racers, arrived = os.open, threading.Thread.join, %r, %r\n"
    "threading.Thread.join = lambda self, timeout=None: real_join(self)\n"
    "time.monotonic = lambda: 0.0\n"
    "def held(path, flags, *a):\n"
    "    if path.endswith('.json'):\n"
    "        os.close(tempfile.mkstemp(dir=arrived)[0])\n"
    "        for _ in range(2000):\n"
    "            if len(os.listdir(arrived)) >= racers:\n"
    "                return real_open(path, flags, *a)\n"
    "            time.sleep(0.01)\n"
    "        os._exit(3)\n"
    "    return real_open(path, flags, *a)\n"
    "os.open = held\n")
# A git that sleeps 30 s: a real process given whatever the hook gives git.
SLEEPING_GIT = (
    "import subprocess\n"
    "real_run = subprocess.run\n"
    "subprocess.run = lambda args, **kw: real_run(\n"
    "    [sys.executable, '-I', '-S', '-c', 'import time; time.sleep(30)'], **kw)\n")
# A transcript read that never returns.
NEVER_READS = (
    "import os, threading\n"
    "sys.path.insert(0, os.path.dirname(guard))\n"
    "import turn_evidence\n"
    "turn_evidence.reversed_lines = lambda path: threading.Event().wait()\n")


def gate_git(repo, *args, stdin=None, check=True):
    proc = subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "core.autocrlf=false", "-c", "advice.detachedHead=false"] + list(args),
                          input=stdin, stdin=None if stdin else subprocess.DEVNULL, capture_output=True, timeout=60)
    if check and proc.returncode:
        raise RuntimeError("git %r: %r" % (args, proc.stderr[:300]))
    return proc.stdout


def gate_write(repo, name, age=EDITED, text=None):
    """A file with new content whose mtime lies age seconds back."""
    path = os.path.join(repo, *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("%s\n" % (time.time() if text is None else text))
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def gate_repo(parent, name="x", gates=GATES):
    """A repo with one commit, its files EDITED seconds old: the first per `gates` is built, the next are copies."""
    repo = os.path.join(parent, name)
    template = gate_templates.get(json.dumps(gates))
    if template:
        shutil.copytree(template, repo)
        for file_name in GATE_FILES:
            stamp = time.time() - EDITED
            os.utime(os.path.join(repo, *file_name.split("/")), (stamp, stamp))
        return repo
    os.makedirs(os.path.join(repo, ".claude"))
    for file_name in GATE_FILES:
        gate_write(repo, file_name, text="line one of %s\nline two\nline three\nline four" % file_name)
    if gates is not None:
        with open(os.path.join(repo, ".claude", "gates.json"), "w", encoding="utf-8") as fh:
            json.dump(gates, fh)
    gate_git(repo, "init", "-q", "-b", "main")
    gate_git(repo, "add", "-A")
    gate_git(repo, "commit", "-q", "-m", "start")
    gate_templates[json.dumps(gates)] = shutil.copytree(repo, repo + "-template")
    return repo


def gate_stamp(age):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - age))


def gate_transcript(path, cwd, age, command=CHECK_BASH, output=CHECK_PASSED, name="Bash"):
    """A transcript with one finished shell call that started age seconds ago."""
    use = {"parentUuid": "p", "isSidechain": False, "type": "assistant", "uuid": "u", "timestamp": gate_stamp(age),
           "userType": "external", "cwd": cwd, "sessionId": SESSION, "message": {
               "role": "assistant", "type": "message", "content": [
                   {"type": "tool_use", "id": "toolu_check", "name": name, "input": {"command": command}}]}}
    result = {"parentUuid": "p", "isSidechain": False, "promptId": "q", "type": "user", "uuid": "v",
              "timestamp": gate_stamp(age - 1), "toolUseResult": {"stdout": "", "stderr": ""}, "cwd": cwd,
              "sessionId": SESSION, "message": {"role": "user", "content": [
                  {"tool_use_id": "toolu_check", "type": "tool_result", "content": output, "is_error": False}]}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        for record in (use, result):
            fh.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n")
    return path


def gate_event(tool, cmd, cwd, use_id, transcript, **extra):
    event = {"session_id": SESSION, "transcript_path": transcript, "cwd": cwd, "hook_event_name": "PreToolUse",
             "tool_name": tool, "tool_input": {"command": cmd}, "tool_use_id": use_id}
    event.update(extra)
    return event


def gate_hook(event, data, flag=None, prelude=None, drop=(), extra=None, guard=GUARD, cwd=None, timeout=30):
    """One hook process; `data` is CLAUDE_PLUGIN_DATA, None for unset. Returns the finished process."""
    flag = flag or ("--pwsh-all" if event["tool_name"] == "PowerShell" else "--only-git")
    env = {k: v for k, v in os.environ.items() if k not in ("SHELL_GUARD", "CLAUDE_PLUGIN_DATA") + tuple(drop)}
    env.update({} if data is None else {"CLAUDE_PLUGIN_DATA": data})
    env.update(extra or {})
    args = [guard, flag] if prelude is None else ["-c", HOOK_DRIVER, guard, flag, prelude]
    return subprocess.run([sys.executable, "-I", "-S"] + args, input=json.dumps(event).encode("utf-8"),
                          capture_output=True, timeout=timeout, env=env, cwd=cwd)


def gate_rows(data):
    """The names in the rows folder; none when the rule never made it."""
    rows = os.path.join(data, "commit-gate", "rows")
    return sorted(os.listdir(rows)) if os.path.isdir(rows) else []


def gate_row(data, use_id):
    """A row by the id the commit had: its fields, "" for an empty file, None for no file."""
    name = hashlib.sha256(use_id.encode("utf-8")).hexdigest()[:12] + ".json"
    return gate_row_file(data, name)


def gate_row_file(data, name):
    path = os.path.join(data, "commit-gate", "rows", name)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        text = fh.read()
    return json.loads(text) if text else ""


def gate_modules(path):
    with open(path, encoding="ascii") as fh:
        return set(fh.read().split("\n"))


def commit_gate_checks(root, settle):
    """The commit rule end to end on real repositories. CLAUDE_PLUGIN_DATA, sys.path, sys.pycache_prefix and ROW_CAP
    are restored in finally. settle() returns when nothing else runs; the checks with a time window call it first."""
    lab = os.path.join(root, "commit-gate")
    results = []   # (label, got, want)
    uses = itertools.count(1)
    start = time.monotonic()
    saved_data, saved_path = os.environ.get("CLAUDE_PLUGIN_DATA"), list(sys.path)
    saved_prefix, saved_cap = sys.pycache_prefix, shell_guard.ROW_CAP

    def scene(name):
        path = os.path.join(lab, name)
        os.makedirs(path)
        return path, os.path.join(path, "data")

    def run(data, tool, cmd, cwd, transcript, **extra):
        """commit_row on one call: the row, "" for an empty file, None for no file."""
        use_id = "toolu_%04d" % next(uses)
        os.environ["CLAUDE_PLUGIN_DATA"] = data
        shell_guard.commit_row(gate_event(tool, cmd, cwd, use_id, transcript, **extra), cmd, time.monotonic() + 60)
        return gate_row(data, use_id)

    def want(label, row, **fields):
        got = row if not isinstance(row, dict) else {k: row.get(k) for k in fields}
        results.append((label, got, fields))

    def none(label, row):
        results.append((label, row, None))

    def near(label, value, target):
        results.append((label, isinstance(value, (int, float)) and abs(value - target) < 30, True))

    def hook(tool, cmd, folder, transcript, data, **kw):
        use_id = "toolu_%04d" % next(uses)
        return gate_hook(gate_event(tool, cmd, folder, use_id, transcript), data, **kw), use_id

    try:
        # Every form of the call, both shells. An edit after the check is stale; a check after it is fresh.
        parent, data = scene("forms")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        before = gate_transcript(os.path.join(parent, "before.jsonl"), repo, PASS_BEFORE)
        after = gate_transcript(os.path.join(parent, "after.jsonl"), repo, PASS_AFTER)
        forms = [("Bash", "git commit -m m", repo), ("Bash", "git -C x commit -m m", parent),
                 ("Bash", "git -c a=b commit -m m", repo), ("Bash", "cd x && git commit -m m", parent),
                 ("Bash", "g=x; git -C $g commit -m m", parent), ("Bash", 'git commit -m "$(echo sub)"', repo),
                 ("Bash", "FOO=1 git commit -m m", repo),
                 ("PowerShell", "git commit -m m", repo), ("PowerShell", "git -C x commit -m m", parent),
                 ("PowerShell", "git -c a=b commit -m m", repo), ("PowerShell", "cd x && git commit -m m", parent),
                 ("PowerShell", "$g = 'x'; git -C $g commit -m m", parent), ("PowerShell", "git.exe commit -m m", repo),
                 ("PowerShell", "GIT commit -m m", repo), ("PowerShell", "$o = git commit -m m 2>&1", repo),
                 ("PowerShell", "& git commit -m m", repo),
                 ("PowerShell", "Set-Location x; git commit -m m", parent)]
        for tool, cmd, cwd in forms:
            want("%s stale: %s" % (tool, cmd), run(data, tool, cmd, cwd, before), verdict="stale", reason="ok",
                 staged=1, deleted=0, op="commit", sid8=SESSION[:8], git_op=None, chained=False)
            want("%s fresh: %s" % (tool, cmd), run(data, tool, cmd, cwd, after), verdict="fresh", reason="ok")
        for tool, cmd in (("Bash", "echo 'git commit -m m'"), ("PowerShell", "Write-Output 'git commit -m m'"),
                          ("Bash", "git commit-tree HEAD^{tree} -m m"), ("PowerShell", "git log --grep commit"),
                          ("Bash", "git show commit"), ("PowerShell", "echo git commit"),
                          ("Bash", "cat <<'EOF'\ngit commit -m m\nEOF"), ("PowerShell", "@'\ngit commit -m m\n'@ | Out-Null")):
            none("no real commit: %s" % cmd, run(data, tool, cmd, repo, before))
        want("staged and staged again from a subfolder is one file",
             run(data, "PowerShell", "cd src && git add a.py && git commit -m m", repo, before), staged=1)
        row = run(data, "Bash", "git commit -m m", repo, before)
        results.append(("every field and no other", set(row), GATE_ROW_FIELDS))
        near("newest_age_s", row["newest_age_s"], EDITED)
        near("check_age_s", row["check_age_s"], PASS_BEFORE)
        near("reflog_age_s", row["reflog_age_s"], 0)
        results.append(("ms is a count", isinstance(row["ms"], int) and row["ms"] >= 0, True))
        with open(os.path.join(data, "commit-gate", "rows", gate_rows(data)[0]), "rb") as fh:
            text = fh.read().decode("ascii")
        results.append(("the row holds no file name, path or command",
                        [s for s in ("a.py", "src", "git commit", "gate", "commit-gate") if s in text], []))
        with open(after, encoding="utf-8") as fh:
            started = json.loads(fh.readline())["timestamp"]
        moment = calendar.timegm(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))
        os.utime(os.path.join(repo, "src", "a.py"), (moment, moment))
        want("an edit at the check's own start is stale", run(data, "Bash", "git commit -m m", repo, after),
             verdict="stale")

        # What the same command stages first: nothing is in the index yet.
        parent, data = scene("staging")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_write(repo, "src/new.py")
        gate_write(repo, "src/ignored.py")
        with open(os.path.join(repo, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
            fh.write("src/ignored.py\n")
        os.utime(os.path.join(repo, "src", "c.py"))   # stat-dirty: a plain `git diff` would rewrite the index
        with open(os.path.join(repo, ".git", "index"), "rb") as fh:
            index = fh.read()
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        staging = [
            ("git commit -m m", 0, "out-of-scope"), ("git add src/a.py && git commit -m m", 1, "stale"),
            ("git add src/a.py; git commit -m m", 1, "stale"), ("git add -A && git commit -m m", 2, "stale"),
            ("git add --all && git commit -m m", 2, "stale"), ("git add . && git commit -m m", 2, "stale"),
            ("git add -u && git commit -m m", 2, "stale"), ("git add -Av && git commit -m m", 2, "stale"),
            ("git commit -a -m m", 1, "stale"), ("git commit -am m", 1, "stale"), ("git commit -qam m", 1, "stale"),
            ("git commit --all -m m", 1, "stale"), ("git commit -m m -- src/a.py", 1, "stale"),
            ("git commit -m m src/a.py", 1, "stale"), ("git commit -m -a", 0, "out-of-scope"),
            ("git commit -m m -- src/b.py", 0, "out-of-scope"), ("git add src && git commit -m m", 2, "stale"),
            ("cd src && git add a.py && git commit -m m", 1, "stale"),
            ("cd src && git add a.py && cd .. && git commit -m m", 1, "stale"),
            ("git -C src add a.py && git commit -m m", 1, "stale"),
            ("git commit -m m; git add src/a.py", 0, "out-of-scope"),
            ("git commit -uall -m m", 0, "out-of-scope"), ("git commit -Sa -m m", 0, "out-of-scope"),
            ("git commit --message src/a.py", 0, "out-of-scope"),
        ]

        def parsed(tool, cmd):
            commit = shell_guard.find_commit(tool, cmd, repo)
            return (commit.base, commit.dry_run, commit.whole, commit.paths, commit.added, commit.again,
                    [stage[2:4] for stage in commit.stages])

        for cmd, staged, verdict in staging:
            want("Bash: " + cmd, run(data, "Bash", cmd, repo, before), verdict=verdict, staged=staged)
            # Past the parse the shell plays no part, so this column need not spawn git again.
            results.append(("PowerShell parses as Bash: " + cmd, parsed("PowerShell", cmd), parsed("Bash", cmd)))
        for tool, cmd in (("PowerShell", "$w = 'x'; git -C $w add . && git -C $w commit -m m"),
                          ("Bash", "w=x; git -C $w add . && git -C $w commit -m m")):
            want(cmd, run(data, tool, cmd, parent, before), verdict="stale", staged=2)
        for cmd in ("x=`cd src && pwd`; git commit -m m", "x=\"`cd src && pwd`\"; git commit -m m",
                    "x=$(cd src && pwd); git commit -m m", "(cd src && pwd); git commit -m m"):
            results.append(("the outer folder after: " + cmd, shell_guard.find_commit("Bash", cmd, repo).base, repo))
        for cmd in ("(cd src && git commit -m m)", "x=$(cd src && git commit -m m)", "cd src && git commit -m m"):
            results.append(("the body's folder: " + cmd, shell_guard.find_commit("Bash", cmd, repo).base,
                            os.path.join(repo, "src")))
        for cmd in ("d=$(cat f); cd \"$d\" && git commit -m m", "d=$(cat f); git -C \"$d\" commit -m m"):
            results.append(("no folder: " + cmd, shell_guard.find_commit("Bash", cmd, repo).base, None))
        with open(os.path.join(repo, ".git", "index"), "rb") as fh:
            results.append(("the hook's git calls leave the index alone", fh.read() == index, True))
        want("a pathspec git expands", run(data, "PowerShell", "git add src/*.py && git commit -m m", repo, before),
             verdict="stale", staged=2)
        want("a redirect is no path", run(data, "PowerShell", "git add src/a.py 2>&1 && git commit -m m", repo, before),
             verdict="stale", staged=1)
        want("a line continuation before the commit", run(data, "Bash", "git add src/a.py && \\\n  git commit -m m", repo, before),
             verdict="stale", staged=1)
        want("a line continuation between paths", run(data, "Bash", "git add src/a.py \\\n  src/b.py && git commit -m m", repo, before),
             verdict="stale", staged=2)
        want("a path the hook cannot read", run(data, "PowerShell", "git add $f && git commit -m m", repo, before),
             verdict="unknown", reason="paths")
        gate_write(repo, "docs/n.md")
        want("`git add .` in a subfolder stops there", run(data, "Bash", "cd src && git add . && git commit -m m", repo, before),
             verdict="stale", staged=2)
        want("`git add .` at the root takes it all", run(data, "Bash", "git add . && git commit -m m", repo, before),
             verdict="stale", staged=3)
        os.remove(os.path.join(repo, "src", "b.py"))
        want("git add on a deleted file", run(data, "Bash", "git add src/b.py && git commit -m m", repo, before),
             verdict="unknown", reason="deleted", staged=1, deleted=1)

        # The two commits the labels call real catches, by shape.
        parent, data = scene("catches")
        repo = gate_repo(parent)
        gate_write(repo, "src/inc/Theme.php")
        gate_write(repo, "src/site.config.ts")
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        cmd = "Set-Location '%s'; git add src/inc/Theme.php; git commit -q -m @'\nchore: register\n'@" % repo
        want("gate1-11", run(data, "PowerShell", cmd, parent, before), verdict="stale", staged=1)
        cmd = ("git add src/site.config.ts; $msg = \"Merge remote-tracking branch 'origin/develop' into feat/x`n`nKeeps "
               "every tool\"; git commit -m $msg")
        want("gate1-9", run(data, "PowerShell", cmd, repo, before), verdict="stale", staged=1)

        # What the rule cannot read is never fresh. old.py is staged and older than `between`, a.py edited after it, and
        # both are older than `later`. Each input has a twin that shows the rule still answers.
        parent, data = scene("unread")
        repo = gate_repo(parent)
        gate_repo(parent, "other")
        gate_write(repo, "src/old.py", age=300)
        gate_git(repo, "add", "src/old.py")
        gate_write(repo, "src/a.py")
        between = gate_transcript(os.path.join(parent, "between.jsonl"), repo, PASS_BEFORE)
        later = gate_transcript(os.path.join(parent, "later.jsonl"), repo, PASS_AFTER)
        both = ("Bash", "PowerShell")
        unread = [
            # A `git add` elsewhere in the repo counts from its own folder; its folder unknown, it is unread.
            (both, "git -C src add a.py && git commit -m m", between, "stale", "ok"),
            (both, "cd src && git add a.py && cd .. && git commit -m m", between, "stale", "ok"),
            (both, "git -C src add . && git commit -m m", between, "stale", "ok"),
            (both, "git -C src add a.py && git commit -m m", later, "fresh", "ok"),
            (both, "cd src && git add a.py && cd .. && git commit -m m", later, "fresh", "ok"),
            (both, "git -C src add . && git commit -m m", later, "fresh", "ok"),
            (("Bash",), 'git -C "$(pwd)" add src/a.py && git commit -m m', between, "unknown", "paths"),
            (("PowerShell",), "git -C (Get-Location) add src/a.py; git commit -m m", between, "unknown", "paths"),
            (("PowerShell",), "git -C $(Get-Location) add src/a.py; git commit -m m", between, "unknown", "paths"),
            (("PowerShell",), "git -C $PWD.Path add src/a.py; git commit -m m", between, "unknown", "paths"),
            (both, "git -C nosuch add src/a.py; git commit -m m", between, "unknown", "paths"),
            (both, "git -C ../other add src/a.py && git commit -m m", between, "fresh", "ok"),
            (both, "git -C .. add x; git commit -m m", between, "fresh", "ok"),
            # A `git add` the rule cannot read, and a word that is neither on disk nor in the index.
            (both, "git add :/ && git commit -m m", between, "unknown", "paths"),
            (both, "git add :/src && git commit -m m", between, "unknown", "paths"),
            (both, "git add ':!docs' && git commit -m m", between, "unknown", "paths"),
            (both, "git add ':(glob)src/**' && git commit -m m", between, "unknown", "paths"),
            (("Bash",), "git add src/{a,b}.py && git commit -m m", between, "unknown", "paths"),
            (both, "git add --pathspec-from-file=f && git commit -m m", between, "unknown", "paths"),
            (both, "git add -p && git commit -m m", between, "unknown", "paths"),
            (both, "git add -i && git commit -m m", between, "unknown", "paths"),
            (both, "git add -e && git commit -m m", between, "unknown", "paths"),
            (both, "git add --patch && git commit -m m", between, "unknown", "paths"),
            (("PowerShell",), "$f = @('src/a.py'); git add @f; git commit -m m", between, "unknown", "paths"),
            (("PowerShell",), "(cd src && ls); git add src/a.py && git commit -m m", between, "unknown", "paths"),
            (("Bash",), "(cd src && ls); git add src/a.py && git commit -m m", between, "stale", "ok"),
            (("Bash",), "x=`cd src && pwd`; git add src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git ls-files -m | xargs git add && git commit -m m", between, "unknown", "paths"),
            (("PowerShell",), "git ls-files -m | ForEach-Object { git add $_ }; git commit -m m", between, "unknown", "paths"),
            (both, "git add src/a.py && git commit -m m", between, "stale", "ok"),
            (both, "git add src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git add -A && git commit -m m", later, "fresh", "ok"),
            (both, "git commit -am m", later, "fresh", "ok"),
            (both, "git add src/old.py && git commit -m m", between, "fresh", "ok"),
            (both, "git add -- src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git add -vf src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git add --force --verbose src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git add src/*.py && git commit -m m", later, "fresh", "ok"),
            (both, "git log --grep git add && git commit -m m", later, "fresh", "ok"),
            # Files change in the call, or a second commit takes what the first left.
            (both, "git commit -m one && git add src/a.py && git commit -m two", later, "unknown", "same_call"),
            (both, "git commit -m one && git add src/a.py && git commit -m two", between, "unknown", "same_call"),
            (("Bash",), "echo x >> src/b.py && git add src/b.py && git commit -m m", later, "unknown", "same_call"),
            (("Bash",), "echo x &> src/b.py && git add src/b.py && git commit -m m", later, "unknown", "same_call"),
            (("PowerShell",), "Add-Content src/b.py x; git add src/b.py; git commit -m m", later, "unknown", "same_call"),
            (("PowerShell",), "echo x > src/b.py; git add src/b.py; git commit -m m", later, "unknown", "same_call"),
            (("PowerShell",), "echo x >src/b.py; git add src/b.py; git commit -m m", later, "unknown", "same_call"),
            (("PowerShell",), "[IO.File]::WriteAllText('src/b.py', 'x'); git add src/b.py; git commit -m m", later, "unknown",
             "same_call"),
            (both, "git mv src/b.py src/f.py && git commit -m m", later, "unknown", "same_call"),
            (both, "python fix.py && git add -A && git commit -m m", later, "unknown", "same_call"),
            (("Bash",), "echo x >> src/b.py && git add src/a.py && git commit -m m", between, "stale", "ok"),
            (("Bash",), "o=$(echo x > src/b.py) && git add src/b.py && git commit -m m", later, "unknown", "same_call"),
            (both, "git status && git commit -m m", later, "fresh", "ok"),
            (both, "echo hi && git commit -m m", later, "fresh", "ok"),
            (("Bash",), "echo hi > /dev/null && git commit -m m", later, "fresh", "ok"),
            (("Bash",), "git status 2>/dev/null && git commit -m m", later, "fresh", "ok"),
            (("Bash",), "cat < src/a.py && git commit -m m", later, "fresh", "ok"),
            (both, "git status 2>&1 && git commit -m m", later, "fresh", "ok"),
            (("PowerShell",), "echo hi > $null; git commit -m m", later, "fresh", "ok"),
            (("PowerShell",), "git status 2>$null; git commit -m m", later, "fresh", "ok"),
            (("PowerShell",), "[string]$msg = 'm'; git commit -m $msg", later, "fresh", "ok"),
            (both, "git commit -m one; git log -1", later, "fresh", "ok"),
        ]
        for tools, cmd, transcript, verdict, reason in unread:
            for tool in tools:
                want("%s, pass %s: %s" % (tool, "after a.py" if transcript == later else "before a.py", cmd),
                     run(data, tool, cmd, repo, transcript), verdict=verdict, reason=reason)
        want("a write between the check and the commit is no pending",
             run(data, "Bash", "%s && echo x >> src/b.py && git add src/b.py && git commit -m m" % CHECK_BASH, repo, later),
             verdict="unknown", reason="same_call", chained=True)
        want("a write before the check is pending",
             run(data, "Bash", "echo x >> src/b.py && %s && git commit -m m" % CHECK_BASH, repo, later),
             verdict="pending", chained=True)
        gate_git(repo, "reset", "-q", "--hard")
        for tool, cmd, verdict, reason in (("Bash", "echo x >> src/b.py && git commit -am m", "unknown", "same_call"),
                                           ("PowerShell", "Add-Content src/b.py x; git commit -am m", "unknown", "same_call"),
                                           ("Bash", "git commit -m one && git commit -am two", "unknown", "same_call"),
                                           ("Bash", "echo hi && git commit -am m", "out-of-scope", None)):
            want("nothing in scope yet: " + cmd, run(data, tool, cmd, repo, later), verdict=verdict, reason=reason)
        gate_git(repo, "rm", "-q", "src/b.py")
        want("a deletion and a write", run(data, "Bash", "rm src/c.py && git commit -m m", repo, later),
             verdict="unknown", reason="same_call", deleted=1)
        gate_git(repo, "reset", "-q", "--hard")
        # The same folder in another spelling is the same repo.
        gate_write(repo, "src/a.py")
        spelled = os.path.isdir(os.path.join(parent, "X"))   # a case-insensitive file system
        want("a repo spelled in another case", run(data, "Bash", "git -C X add src/a.py && git -C x commit -m m", parent, between),
             verdict="stale" if spelled else "out-of-scope")

        # Which files, and which verdict for them.
        parent, data = scene("verdicts")
        repo = gate_repo(parent)
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        gate_write(repo, "docs/n.md")
        gate_git(repo, "add", "docs/n.md")
        want("docs only", run(data, "Bash", "git commit -m m", repo, before), verdict="out-of-scope", staged=1, deleted=0)
        gate_git(repo, "reset", "-q")
        gate_git(repo, "rm", "-q", "src/b.py")
        want("deletion only", run(data, "Bash", "git commit -m m", repo, before),
             verdict="unknown", reason="deleted", staged=1, deleted=1)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        want("a deletion beside an edit", run(data, "Bash", "git commit -m m", repo, before),
             verdict="stale", staged=2, deleted=1)
        gate_git(repo, "reset", "-q", "--hard")
        gate_git(repo, "mv", "src/c.py", "src/d.py")
        want("rename: both names", run(data, "Bash", "git commit -m m", repo, before),
             verdict="stale", staged=2, deleted=1)
        gate_git(repo, "reset", "-q", "--hard")
        for flag, tool in (("--dry-run", "Bash"), ("--dry", "PowerShell")):
            gate_write(repo, "src/a.py")
            gate_git(repo, "add", "src/a.py")
            want("dry run: " + flag, run(data, tool, "git commit %s -m m" % flag, repo, before), verdict="dry-run", staged=1)
        want("a word in the message is no dry run", run(data, "Bash", "git commit -m --dry-run", repo, before),
             verdict="stale")
        want("marker", run(data, "Bash", "git commit -m m # shell-guard: allow", repo, before), verdict="marker", staged=1)
        want("marker, PowerShell", run(data, "PowerShell", "git commit -m m # shell-guard: allow", repo, before),
             verdict="marker", staged=1)
        gate_git(repo, "reset", "-q", "--hard")
        blob = gate_git(repo, "hash-object", "-w", "src/a.py").decode("ascii").strip()
        gate_git(repo, "update-index", "--index-info", stdin=b"100644 " + blob.encode("ascii") + b"\tsrc/bad\xff.py\n")
        want("a name that is not UTF-8", run(data, "Bash", "git commit -m m", repo, before),
             verdict="unknown", reason="deleted", staged=1, deleted=1)
        gate_git(repo, "reset", "-q", "--hard")
        os.remove(os.path.join(repo, ".git", "HEAD"))
        want("git refuses", run(data, "Bash", "git commit -m m", repo, before), verdict="unknown", reason="git")

        # A real merge, rebase and cherry-pick stopped on a conflict.
        parent, data = scene("operations")
        repo = gate_repo(parent)
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        after = gate_transcript(os.path.join(parent, "after.jsonl"), repo, PASS_AFTER)
        gate_git(repo, "checkout", "-q", "-b", "other")
        gate_write(repo, "src/a.py", text="other")
        gate_git(repo, "commit", "-q", "-am", "other")
        gate_git(repo, "checkout", "-q", "main")
        gate_write(repo, "src/a.py", text="main")
        gate_git(repo, "commit", "-q", "-am", "main")
        for label, start_op, abort_op, state in ((("merge"), ("merge", "other"), ("merge", "--abort"), "MERGE_HEAD"),
                                                 ("rebase", ("rebase", "other"), ("rebase", "--abort"), "rebase-merge"),
                                                 ("cherry-pick", ("cherry-pick", "other"), ("cherry-pick", "--abort"),
                                                  "CHERRY_PICK_HEAD")):
            gate_git(repo, *start_op, check=False)
            gate_write(repo, "src/a.py", text="both")
            want(label, run(data, "PowerShell", "git add src/a.py; git commit -m m", repo, before),
                 verdict="stale", git_op=state, staged=1)
            gate_git(repo, *abort_op)
        # A rebase resets mtimes: replay needs the reflog to discount the stale row this gives.
        gate_git(repo, "checkout", "-q", "-b", "side", "HEAD~1")
        gate_write(repo, "src/b.py", text="side")
        gate_git(repo, "commit", "-q", "-am", "side")
        gate_git(repo, "checkout", "-q", "main")
        gate_write(repo, "src/c.py")
        gate_git(repo, "add", "src/c.py")
        want("before the rebase", run(data, "Bash", "git commit -m m", repo, after), verdict="fresh")
        gate_git(repo, "stash", "-q")
        gate_git(repo, "rebase", "-q", "side")
        gate_git(repo, "stash", "pop", "-q", "--index")
        row = run(data, "Bash", "git commit -m m", repo, after)
        want("rebase-reset mtime", row, verdict="stale", git_op=None)
        near("rebase-reset reflog_age_s", row["reflog_age_s"], 0)
        near("rebase-reset newest_age_s", row["newest_age_s"], 0)

        # A check in the same command has not run yet.
        parent, data = scene("pending")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        before = gate_transcript(os.path.join(parent, "before.jsonl"), repo, PASS_BEFORE)
        after = gate_transcript(os.path.join(parent, "after.jsonl"), repo, PASS_AFTER)
        tail = {"Bash": "tail -5", "PowerShell": "Select-Object -Last 5"}
        for tool, check in (("Bash", CHECK_BASH), ("PowerShell", CHECK_PS)):
            for cmd, verdict, chained in (
                    ("%s && git commit -m m", "pending", True),
                    ("%s 2>&1 && git commit -m m", "pending", True),
                    ("%s > out.txt && git commit -m m", "pending", True),
                    ("%s > out.txt && git add src/a.py && git commit -m m", "pending", True),
                    ("( %s && echo ok ) && git commit -m m", "pending", True),
                    ("%s &&\n git commit -m m", "pending", True),
                    ("%s && git add src/a.py && git commit -m m", "pending", True),
                    ("%s; git commit -m m", "stale", True),
                    ("%s\ngit commit -m m", "stale", True),
                    ("%s || git commit -m m", "stale", True),
                    ("%s && echo ok; git commit -m m", "stale", True),
                    ("%s | " + tail[tool] + " && git commit -m m", "stale", True),
                    ("echo '%s' && git commit -m m", "stale", False)):
                want("%s: %r" % (tool, cmd), run(data, tool, cmd % check, repo, before), verdict=verdict, chained=chained)
            want(tool + " pending is never fresh", run(data, tool, check + " && git commit -m m", repo, after),
                 verdict="pending")
            want(tool + " a check before ; leaves the earlier pass", run(data, tool, check + "; git commit -m m", repo, after),
                 verdict="fresh", chained=True)
        for tool, cmd, verdict, chained in (
                ("Bash", CHECK_BASH + ' && git commit -m "$(echo x)"', "pending", True),
                ("Bash", CHECK_BASH + " && git commit -m \"$(cat <<'EOF'\nsubject\n\nbody\nEOF\n)\"", "pending", True),
                ("Bash", CHECK_BASH + ' && git add src/a.py && git commit -m "$(echo x)"', "pending", True),
                ("Bash", CHECK_BASH + ' && echo "$(date)" && git commit -m m', "pending", True),
                ("PowerShell", CHECK_PS + " && git commit -m $(Get-Content msg.txt)", "pending", True),
                ("PowerShell", CHECK_PS + ' && git commit -m "$(Get-Date)"', "pending", True),
                ("Bash", CHECK_BASH + '; git commit -m "$(echo x)"', "stale", True),
                ("Bash", CHECK_BASH + ' | tail -5 && git commit -m "$(echo x)"', "stale", True),
                ("PowerShell", CHECK_PS + "; git commit -m $(Get-Content msg.txt)", "stale", True),
                ("Bash", 'git commit -m "$(%s)"' % CHECK_BASH, "stale", True),
                ("Bash", CHECK_BASH + ' && git commit -m "`echo x`"', "pending", True),
                ("Bash", CHECK_BASH + " && git commit -m `echo x`", "pending", True),
                ("Bash", CHECK_BASH + ' && echo "`date`" && git commit -m m', "pending", True),
                ("Bash", CHECK_BASH + '; git commit -m "`echo x`"', "stale", True),
                ("Bash", CHECK_BASH + ' | tail -5 && git commit -m "`echo x`"', "stale", True),
                ("Bash", 'git commit -m "`%s`"' % CHECK_BASH, "stale", True)):
            want("the commit's own $(): " + cmd, run(data, tool, cmd, repo, before), verdict=verdict, chained=chained)
        want("commit in a backtick body", run(data, "Bash", "o=`%s && git commit -m m`" % CHECK_BASH, repo, before),
             verdict="pending")
        want("a backtick body that only reads", run(data, "Bash", 'echo "`echo x`" && git commit -m m', repo, after),
             verdict="fresh")
        want("a redirect in a backtick body is a write in this call",
             run(data, "Bash", 'echo "`echo x > o.txt`" && git commit -m m', repo, after),
             verdict="unknown", reason="same_call")
        want("a line continuation after the check", run(data, "Bash", CHECK_BASH + " && \\\n  git commit -m m", repo, before),
             verdict="pending", chained=True)
        want("commit in a Bash substitution", run(data, "Bash", "o=$(%s && git commit -m m)" % CHECK_BASH, repo, before),
             verdict="pending")
        want("commit in a PowerShell subexpression",
             run(data, "PowerShell", "$o = $(%s && git commit -m m)" % CHECK_PS, repo, before), verdict="pending")
        failed_call = gate_transcript(os.path.join(parent, "failed.jsonl"), repo, PASS_AFTER, output="Exit code 1\n" + CHECK_FAILED,
                                      command=CHECK_PS, name="PowerShell")
        want("failing check before &&: its result is not out yet",
             run(data, "Bash", CHECK_BASH + " && git commit -m m", repo, failed_call), verdict="pending")
        want("a failed earlier call is no pass", run(data, "Bash", "git commit -m m", repo, failed_call),
             verdict="stale", check_age_s=None)
        piped = gate_transcript(os.path.join(parent, "piped.jsonl"), repo, PASS_AFTER, CHECK_BASH + " | tail -3", CHECK_FAILED)
        want("a piped failing check is no pass", run(data, "Bash", "git commit -m m", repo, piped),
             verdict="stale", check_age_s=None)

        # A check that ran with a result the reader cannot read, through the real hook. Each pair differs only in the
        # check's time against the edit.
        def hooked(cmd, transcript, tool="Bash"):
            proc, use_id = hook(tool, cmd, repo, transcript, data)
            results.append(("hook silent: %s" % cmd, (proc.stdout, proc.stderr, proc.returncode), (b"", b"", 0)))
            return gate_row(data, use_id)

        def transcript_of(name, *calls):
            """One stream holding each (age, command, output) call in turn."""
            path = os.path.join(parent, name + ".jsonl")
            joined = b""
            for index, call in enumerate(calls):
                with open(gate_transcript(os.path.join(parent, "%s-%d.jsonl" % (name, index)), repo, *call), "rb") as fh:
                    joined += fh.read()
            with open(path, "wb") as fh:
                fh.write(joined)
            return path

        moved = "Command running in background with ID: b1. Output is being written to: x.output"
        stored = ("<persisted-output>\nOutput too large (45.2KB). Full output saved to: x/tool-results/toolu_1.txt\n\n"
                  "Preview (first 2KB):\ncollected 900 items\n...\n</persisted-output>")
        made = "[main 1a2b3c4] m\n 1 file changed, 2 insertions(+)"
        for label, command, output in (("a pipe", CHECK_BASH + " | tail -1", "some lines"),
                                       ("the background", CHECK_BASH, moved),
                                       ("a shell's background", CHECK_BASH + " &", "started"),
                                       ("persisted output", CHECK_BASH + " 2>&1 | tee log", stored),
                                       ("a commit in the call", CHECK_BASH + " && git commit -m m", made)):
            newer = transcript_of("newer", (PASS_AFTER, command, output))
            older = transcript_of("older", (PASS_BEFORE, command, output))
            want("%s after the edit" % label, hooked("git commit -m m", newer), verdict="unknown", reason="hidden",
                 check_age_s=None)
            want("%s before the edit" % label, hooked("git commit -m m", older), verdict="stale", reason="ok",
                 check_age_s=None)
        piped = (PASS_AFTER, CHECK_BASH + " | tail -1", "some lines")
        want("a readable pass after the edit stays fresh",
             hooked("git commit -m m", transcript_of("readable", (PASS_AFTER, CHECK_BASH, CHECK_PASSED))), verdict="fresh")
        want("a readable pass before the edit stays stale",
             hooked("git commit -m m", transcript_of("readable", (PASS_BEFORE, CHECK_BASH, CHECK_PASSED))), verdict="stale")
        want("a readable failure after the edit stays stale",
             hooked("git commit -m m", transcript_of("failed", (PASS_AFTER, CHECK_BASH + " | tail -1", CHECK_FAILED))),
             verdict="stale", reason="ok")
        want("a readable failure in a commit call stays stale",
             hooked("git commit -m m", transcript_of("failed", (PASS_AFTER, CHECK_BASH + "; git commit -m m", CHECK_FAILED))),
             verdict="stale", reason="ok")
        want("a pass wins over a newer hidden check",
             hooked("git commit -m m", transcript_of("both", (PASS_AFTER + 10, CHECK_BASH, CHECK_PASSED), piped)),
             verdict="fresh")
        want("a pass older than the edit does not win",
             hooked("git commit -m m", transcript_of("both", (PASS_BEFORE, CHECK_BASH, CHECK_PASSED), piped)),
             verdict="unknown", reason="hidden")
        want("a change in the same call leaves the order open",
             hooked("echo x >> src/b.py && git add src/b.py && git commit -m m", transcript_of("same", piped)),
             verdict="unknown", reason="same_call")
        want("a change in the same call and an older hidden check stays stale",
             hooked("echo x >> src/a.py && git add src/a.py && git commit -m m",
                    transcript_of("same", (PASS_BEFORE, CHECK_BASH + " | tail -1", "some lines"))),
             verdict="stale", reason="ok")
        other = gate_repo(parent, "other")
        elsewhere = gate_transcript(os.path.join(parent, "elsewhere.jsonl"), other, PASS_AFTER, piped[1], piped[2])
        want("a hidden check in another repo changes nothing", hooked("git commit -m m", elsewhere), verdict="stale",
             reason="ok")
        same_time = transcript_of("instant", piped)
        with open(same_time, encoding="utf-8") as fh:
            started = json.loads(fh.readline())["timestamp"]
        moment = calendar.timegm(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))
        kept = os.stat(os.path.join(repo, "src", "a.py")).st_mtime
        os.utime(os.path.join(repo, "src", "a.py"), (moment, moment))
        want("a hidden check in the same instant as the edit is not newer", hooked("git commit -m m", same_time),
             verdict="stale", reason="ok")
        os.utime(os.path.join(repo, "src", "a.py"), (kept, kept))

        # The reader's answers.
        main_stream = os.path.join(parent, "session.jsonl")
        with open(main_stream, "wb"):
            pass
        gate_transcript(os.path.join(parent, "session", "subagents", "agent-a1.jsonl"), repo, PASS_AFTER)
        want("a subagent's check", run(data, "Bash", "git commit -m m", repo, main_stream, agent_id="a2"), verdict="fresh")
        want("a commit in the subagent that ran it", run(data, "Bash", "git commit -m m", repo, main_stream, agent_id="a1"),
             verdict="fresh")
        want("no such transcript", run(data, "Bash", "git commit -m m", repo, os.path.join(parent, "none.jsonl")),
             verdict="unknown", reason="unreadable")
        want("no transcript path", run(data, "Bash", "git commit -m m", repo, None), verdict="unknown", reason="unreadable")

        parent, data = scene("bad-regex")
        repo = gate_repo(parent, gates=dict(GATES, checks=["("]))
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        other = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_AFTER)
        want("a bad checks regex", run(data, "Bash", "git commit -m m", repo, other), verdict="error",
             reason=re.error.__name__)
        want("a bad checks regex, chained", run(data, "Bash", "python t.py && git commit -m m", repo, other),
             verdict="error", reason=re.error.__name__)

        # gates.json.
        parent, data = scene("config")
        for label, content in (("not JSON", b"{"), ("not UTF-8", b'{"commit": "\xff"}'), ("not an object", b"[]"),
                               ("checks not a list", b'{"commit": "shadow", "checks": "x", "scope_commit": []}'),
                               ("no scope_commit", b'{"commit": "shadow", "checks": []}')):
            repo = gate_repo(os.path.join(parent, label.replace(" ", "-")), gates=None)
            with open(os.path.join(repo, ".claude", "gates.json"), "wb") as fh:
                fh.write(content)
            want("malformed: " + label, run(data, "Bash", "git commit -m m", repo, None), verdict="error", reason="gates")
        repo = gate_repo(os.path.join(parent, "no-key"), gates={"checks": [], "scope_commit": []})
        none("no commit key", run(data, "Bash", "git commit -m m", repo, None))
        repo = gate_repo(os.path.join(parent, "off"), gates=dict(GATES, commit="off"))
        none("only shadow logs", run(data, "Bash", "git commit -m m", repo, None))
        folder = os.path.join(parent, "no-repo")
        os.makedirs(os.path.join(folder, ".claude"))
        with open(os.path.join(folder, ".claude", "gates.json"), "w", encoding="utf-8") as fh:
            json.dump(GATES, fh)
        none("outside any repo", run(data, "Bash", "git commit -m m", folder, None))
        with open(os.path.join(folder, ".git"), "wb") as fh:
            fh.write(b"not a gitfile\n")
        none("a .git file that names no git dir", run(data, "Bash", "git commit -m m", folder, None))

        # Through the hook process.
        parent, data = scene("process")
        repo = gate_repo(parent)
        bare = gate_repo(parent, "bare", gates=None)
        gate_write(repo, "src/a.py")
        gate_write(bare, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        gate_git(bare, "add", "src/a.py")
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        for tool, flag in (("Bash", "--only-git"), ("PowerShell", "--pwsh-all"), ("Bash", None), ("PowerShell", None)):
            proc, use_id = hook(tool, "git commit -m m", repo, before, data, flag=flag or "--default")
            want("main writes the row: %s %s" % (tool, flag), gate_row(data, use_id), verdict="stale")
            results.append(("stdout stays empty: %s %s" % (tool, flag), (proc.stdout, proc.stderr, proc.returncode), (b"", b"", 0)))
        proc, use_id = hook("Bash", "git commit -m m", repo, before, data, flag="--only-narrow")
        none("the narrow handler writes none", gate_row(data, use_id))
        proc, use_id = hook("Bash", "git commit -m m", repo, before, data, extra={"SHELL_GUARD": "off"})
        none("SHELL_GUARD=off writes none", gate_row(data, use_id))
        utc = os.path.join(parent, "utc")
        proc, use_id = hook("Bash", "git commit -m m", repo, before, utc, extra={"TZ": "EST5"})
        stamp = (gate_row(utc, use_id) or {}).get("ts", "1970-01-01T00:00:00Z")
        results.append(("ts is UTC", abs(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.time()) < 120, True))

        for label, cmd, cwd, worker in (("no gates.json", "git commit -m m", bare, True),
                                        ("no commit in the call", "git status", repo, False),
                                        ("the word only", "echo commit", repo, True)):
            folder = os.path.join(parent, label.replace(".", "-").replace(" ", "-"))
            report, note = folder + "-modules.txt", folder + "-worker"
            hook("Bash", cmd, cwd, before, folder, prelude=REPORT_MODULES % report + NOTE_WORKER % note)
            modules = gate_modules(report)
            results.append((label + ": reader not imported", "turn_evidence" in modules, False))
            results.append((label + ": no git spawned", "subprocess" in modules, False))
            results.append((label + ": modules were reported", "json" in modules, True))
            results.append((label + ": data untouched", os.path.exists(folder), False))
            results.append((label + ": worker started", os.path.exists(note), worker))
        for label, value in (("unset", None), ("empty", "")):
            report = os.path.join(parent, "modules-%s.txt" % label)
            hook("Bash", "git commit -m m", repo, before, value, prelude=REPORT_MODULES % report, cwd=repo)
            results.append(("CLAUDE_PLUGIN_DATA %s: reader not imported" % label,
                            "turn_evidence" in gate_modules(report), False))
            results.append(("CLAUDE_PLUGIN_DATA %s: no row in the repo" % label,
                            os.path.exists(os.path.join(repo, "commit-gate")), False))

        # Bytecode lands in the data folder, not beside the scripts or in the cwd.
        copied = os.path.join(parent, "copy")
        os.makedirs(copied)
        for name in ("shell_guard.py", "turn_evidence.py"):
            shutil.copy(os.path.join(os.path.dirname(GUARD), name), copied)
        out = os.path.join(parent, "bytecode")
        proc, use_id = hook("Bash", "git commit -m m", repo, before, out, guard=os.path.join(copied, "shell_guard.py"), cwd=repo)
        want("through a copy of the scripts", gate_row(out, use_id), verdict="stale")
        results.append(("no __pycache__ beside the scripts", os.path.exists(os.path.join(copied, "__pycache__")), False))
        results.append(("no __pycache__ in the cwd", os.path.exists(os.path.join(repo, "__pycache__")), False))
        cached = [n for _, _, names in os.walk(os.path.join(out, "pycache")) for n in names if n.startswith("turn_evidence")]
        results.append(("the bytecode is under the data folder", len(cached), 1))

        # A non-ASCII repo, file and transcript path under the machine's real code page.
        folder = os.path.join(parent, "naïve")
        nonascii = gate_repo(folder, "façade")
        gate_write(nonascii, "src/naïve.py")
        gate_git(nonascii, "add", "src/naïve.py")
        pl_before = gate_transcript(os.path.join(folder, "sess-é.jsonl"), nonascii, PASS_BEFORE)
        pl_after = gate_transcript(os.path.join(folder, "sess-ü.jsonl"), nonascii, PASS_AFTER)
        pl_data = os.path.join(folder, "dane")
        for label, transcript, verdict in (("stale", pl_before, "stale"), ("fresh", pl_after, "fresh")):
            proc, use_id = hook("PowerShell", "git commit -m m", nonascii, transcript, pl_data, drop=("PYTHONUTF8",))
            want("non-ASCII names, " + label, gate_row(pl_data, use_id), verdict=verdict, staged=1, deleted=0)
        gate_write(nonascii, "src/new-é.py")
        gate_write(nonascii, "src/drugi.py")
        proc, use_id = hook("PowerShell", "git add -A && git commit -m m", nonascii, pl_before, pl_data, drop=("PYTHONUTF8",))
        want("two untracked names, one non-ASCII", gate_row(pl_data, use_id), verdict="stale", staged=3, deleted=0)

        # A linked worktree: `.git` is a file, the state lives in the git dir it names, here by a relative path.
        parent, data = scene("worktree")
        repo = gate_repo(parent)
        gate_git(repo, "branch", "other")
        gate_write(repo, "src/a.py", text="main")
        gate_git(repo, "commit", "-q", "-am", "main")
        linked = os.path.join(parent, "linked")
        gate_git(repo, "worktree", "add", "-q", linked, "-b", "work", "other")
        gate_write(linked, "src/a.py", text="work")
        gate_git(linked, "commit", "-q", "-am", "work")
        results.append((".git is a file", os.path.isfile(os.path.join(linked, ".git")), True))
        with open(os.path.join(linked, ".claude", "gates.json"), "w", encoding="utf-8") as fh:
            json.dump(GATES, fh)
        before = gate_transcript(os.path.join(parent, "t.jsonl"), linked, PASS_BEFORE)
        gate_git(linked, "merge", "main", check=False)
        gate_write(linked, "src/a.py", text="both")
        gate_git(linked, "add", "src/a.py")
        row = run(data, "Bash", "git commit -m m", linked, before)
        want("linked worktree", row, verdict="stale", staged=1, git_op="MERGE_HEAD")
        near("linked worktree reflog_age_s", row["reflog_age_s"], 0)
        os.remove(os.path.join(linked, ".git"))   # git hides it, and Windows refuses to open a hidden file for "w"
        with open(os.path.join(linked, ".git"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("gitdir: ../x/.git/worktrees/linked\n")
        want("a relative gitdir", run(data, "Bash", "git commit -m m", linked, before), verdict="stale", git_op="MERGE_HEAD")

        # tool_use_id: one fixed name would swallow every later commit. Open process handles keep the two pids apart.
        parent, data = scene("no-id")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        for label, change in (("removed", None), ("a number", 7), ("empty", "")):
            folder = os.path.join(data, label.replace(" ", "-"))
            event = gate_event("Bash", "git commit -m m", repo, change, None)
            if change is None:
                del event["tool_use_id"]
            env = dict(os.environ, CLAUDE_PLUGIN_DATA=folder)
            held = []
            for _ in range(2):
                held.append(subprocess.Popen([sys.executable, "-I", "-S", GUARD, "--only-git"], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env))
                held[-1].communicate(json.dumps(event).encode("utf-8"), timeout=30)
            names = gate_rows(folder)
            results.append(("tool_use_id %s: two rows" % label, [n.startswith("noid-") for n in names], [True, True]))
            want("tool_use_id %s" % label, gate_row_file(folder, "".join(names[:1]) or "no-row"), verdict="error",
                 reason="use_id", use12=None)

        # A deny stays first, and quick. No row, since the call never runs.
        settle()
        parent, data = scene("deny")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        for tool in ("Bash", "PowerShell"):
            began = time.monotonic()
            proc, use_id = hook(tool, "git commit --no-verify -m m", repo, None, data, prelude=SLEEPING_GIT)
            took = time.monotonic() - began
            results.append(("%s deny skips the wait (%.2f)" % (tool, took), took < 2, True))
            results.append(("%s deny arrives" % tool, b"permissionDecision" in proc.stdout, True))
            none("%s deny claims no row" % tool, gate_row(data, use_id))
        results.append(("deny leaves the rows folder empty", gate_rows(data), []))

        # A hung git or a hung read: the hook's stdout reaches EOF inside the wait; captured pipes prove no child holds it.
        def hung(transcript, prelude):
            """(wall seconds, the hook's output or a text saying it never ended, the call's id)."""
            use_id = "toolu_%04d" % next(uses)
            began = time.monotonic()
            try:
                proc = gate_hook(gate_event("Bash", "git commit -m m", repo, use_id, transcript), data, prelude=prelude,
                                 timeout=15)
                outcome = (proc.stdout, proc.stderr, proc.returncode)
            except subprocess.TimeoutExpired:
                outcome = "no EOF within 15 s"
            return time.monotonic() - began, outcome, use_id

        took, outcome, use_id = hung(None, SLEEPING_GIT)
        results.append(("a hung git: the hook waited, then left (%.2f)" % took, 2.3 < took < 4, True))
        results.append(("a hung git leaves the row file empty", gate_row(data, use_id), ""))
        results.append(("a hung git is silent", outcome, (b"", b"", 0)))
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        took, outcome, use_id = hung(before, NEVER_READS)
        results.append(("a hung read: the hook left in time (%.2f)" % took, took < 4, True))
        want("a hung read logs budget", gate_row(data, use_id), verdict="unknown", reason="budget")

        # A transcript read ends ROW_SPARE before the deadline, so the row is out when the hook stops waiting. The clock
        # stands still: under load the git calls before the read would hide the spare.
        reader, real_clock = sys.modules["turn_evidence"], shell_guard._clock
        real_pass, budgets = reader.last_pass, []

        def noting(streams, folder, checks, budget):
            budgets.append(budget)
            return real_pass(streams, folder, checks, budget)

        try:
            reader.last_pass, shell_guard._clock = noting, lambda: 100.0
            shell_guard.commit_row(gate_event("Bash", "git commit -m m", repo, "toolu_spare", before), "git commit -m m", 130.0)
        finally:
            reader.last_pass, shell_guard._clock = real_pass, real_clock
        results.append(("a read leaves 0.2 s of a 30 s deadline", [round(b, 3) for b in budgets], [29.8]))

        # log_commit itself: what is left of the hook budget bounds the wait, and nothing it meets escapes.
        real_row, real_budget = shell_guard.commit_row, shell_guard.HOOK_BUDGET
        try:
            shell_guard.commit_row = lambda *_a: time.sleep(5)
            shell_guard.HOOK_BUDGET = 0.3
            shell_guard.start_budget()
            began = time.monotonic()
            shell_guard.log_commit(gate_event("Bash", "git commit -m m", repo, "toolu_wait", None))
            took = time.monotonic() - began
            results.append(("the wait stays inside the hook budget (%.2f)" % took, 0.2 < took < 1, True))
        finally:
            shell_guard.commit_row, shell_guard.HOOK_BUDGET, shell_guard._deadline = real_row, real_budget, None
        results.append(("log_commit never raises", _bounded(lambda: shell_guard.log_commit({"tool_input": 5}), 2),
                        ("ok", None)))

        taken = os.path.join(parent, "taken")
        with open(taken, "wb") as fh:
            fh.write(b"keep")
        proc, _ = hook("Bash", "git commit -m m", repo, before, taken)
        results.append(("a data path that is a file", (proc.stdout, proc.stderr, proc.returncode), (b"", b"", 0)))
        with open(taken, "rb") as fh:
            results.append(("a data path that is a file stays as it was", fh.read(), b"keep"))

        # Two handlers on one commit, held at the claim: one claims, the other runs no git.
        parent, data = scene("race")
        repo = gate_repo(parent)
        gate_write(repo, "src/a.py")
        gate_git(repo, "add", "src/a.py")
        before = gate_transcript(os.path.join(parent, "t.jsonl"), repo, PASS_BEFORE)
        arrived, markers = os.path.join(parent, "arrived"), os.path.join(parent, "markers")
        os.makedirs(arrived)
        os.makedirs(markers)
        prelude = COUNT_GIT % markers + CLAIM_BARRIER % (2, arrived)
        env = {k: v for k, v in os.environ.items() if k != "SHELL_GUARD"}
        env["CLAUDE_PLUGIN_DATA"] = data
        event = json.dumps(gate_event("Bash", "git commit -m m", repo, "toolu_race", before)).encode("utf-8")
        racers = [subprocess.Popen([sys.executable, "-I", "-S", "-c", HOOK_DRIVER, GUARD, "--only-git", prelude],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                  for _ in range(2)]
        for racer in racers:
            racer.stdin.write(event)
            racer.stdin.close()
        for racer in racers:
            racer.wait(timeout=60)
            racer.stdout.close()
            racer.stderr.close()
        results.append(("both handlers reached the claim before either took it",
                        (len(os.listdir(arrived)), [racer.returncode for racer in racers]), (2, [0, 0])))
        results.append(("one claim runs git once", len(os.listdir(markers)), 1))
        want("the claimed row", gate_row(data, "toolu_race"), verdict="stale")

        # The cap: a count at the cap adds nothing, one below it adds one.
        parent, data = scene("cap")
        repo = gate_repo(parent)
        results.append(("the cap", shell_guard.ROW_CAP, 20000))
        shell_guard.ROW_CAP = 3
        for n in range(3):
            run(data, "Bash", "git commit -m m", repo, None)
        none("past the cap, no new row", run(data, "Bash", "git commit -m m", repo, None))
        results.append(("rows at the cap", len(gate_rows(data)), 3))
        os.remove(os.path.join(data, "commit-gate", "rows", gate_rows(data)[0]))
        results.append(("below the cap again", run(data, "Bash", "git commit -m m", repo, None) is None, False))
    finally:
        sys.path[:] = saved_path
        sys.pycache_prefix = saved_prefix
        shell_guard.ROW_CAP = saved_cap
        if saved_data is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = saved_data

    failed = 0
    for label, got, expected in results:
        if got != expected:
            failed += 1
            print("COMMIT GATE FAIL  %s: want=%r got=%r" % (label, expected, got))
    print("commit gate checks: %d/%d pass; wall s: %.1f" % (len(results) - failed, len(results), time.monotonic() - start))
    return failed, len(results)


# Each spelling through the real shell and real git; the hook log says what git did with it. (tool, command kind,
# command, effect): "skips" the hook stayed silent on a command git ran, "runs" the hook logged, "refused" git
# exited non-zero and the hook stayed silent, "no-commit" exit 0, HEAD did not move, the hook stayed silent;
# "ignored" the hook logged, though the rule refuses the class. The guard must deny "skips" and "ignored" only.
BYPASS_WORKERS = 6
BYPASS_COMMIT = "git commit --allow-empty -q -m x"
BYPASS_NV = "git commit --no-verify --allow-empty -q -m x"
BYPASS_HOOK = {"commit": "pre-commit", "merge": "pre-merge-commit", "rebase": "pre-rebase",
               "cherry-pick": "prepare-commit-msg", "am": "pre-applypatch", "push": "pre-push"}
BYPASS_COUNT = "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0={nh} "
BYPASS_PARAMS = "GIT_CONFIG_PARAMETERS=\"'core.hooksPath={nh}'\" "
BYPASS_ROWS = [
    ("Bash", "commit", BYPASS_COMMIT, "runs"),
    ("PowerShell", "commit", BYPASS_COMMIT, "runs"),
    ("Bash", "merge", "git merge --no-edit side", "runs"),
    ("Bash", "rebase", "git rebase main", "runs"),
    ("Bash", "cherry-pick", "git cherry-pick side", "runs"),
    ("Bash", "am", "git am {patch}", "runs"),
    ("Bash", "push", "git push origin main", "runs"),
    # --no-verify per command
    ("Bash", "commit", "git commit --no-verify --allow-empty -q -m x", "skips"),
    ("Bash", "commit", "git commit --no-veri --allow-empty -q -m x", "skips"),
    ("Bash", "commit", "git commit --no-ver --allow-empty -q -m x", "refused"),
    ("Bash", "merge", "git merge --no-verify --no-edit side", "skips"),
    ("Bash", "merge", "git merge --verify --no-verify --no-edit side", "skips"),
    ("Bash", "merge", "git merge --no-veri --no-edit side", "refused"),
    ("Bash", "merge", "git merge --no-verif --no-edit side", "refused"),
    ("Bash", "merge", "git merge -n --no-edit side", "runs"),
    ("Bash", "merge", "git merge --no-verify --verify --no-edit side", "runs"),
    ("Bash", "merge", "git merge --no-verify-signatures --no-edit side", "runs"),
    ("Bash", "merge", "git merge -m --no-verify side", "runs"),
    ("Bash", "rebase", "git rebase --no-verify main", "skips"),
    ("Bash", "rebase", "git rebase --no-veri main", "skips"),
    ("Bash", "rebase", "git rebase --no-ver main", "refused"),
    ("Bash", "rebase", "git rebase -n main", "runs"),
    ("Bash", "rebase", "git rebase --no-verify --veri main", "runs"),
    ("Bash", "rebase", "git rebase -X --no-verify main", "runs"),
    ("Bash", "cherry-pick", "git cherry-pick --no-verify side", "refused"),
    ("Bash", "cherry-pick", "git cherry-pick -n side", "no-commit"),
    ("Bash", "am", "git am --no-verify {patch}", "skips"),
    ("Bash", "am", "git am --no-v {patch}", "skips"),
    ("Bash", "am", "git am -n {patch}", "skips"),
    ("Bash", "am", "git am -3n {patch}", "skips"),
    ("Bash", "am", "git am -p1 -n {patch}", "skips"),
    ("Bash", "am", "git am -p -n {patch}", "runs"),
    ("Bash", "am", "git am --no-verify --v {patch}", "runs"),
    ("Bash", "am", "git am --directory --no-verify {patch}", "runs"),
    ("Bash", "push", "git push --no-verify origin main", "skips"),
    ("Bash", "push", "git push -n origin main", "runs"),
    # the override on each protected command
    ("Bash", "commit", "git -c core.hooksPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "merge", "git -c core.hooksPath={nh} merge --no-edit side", "skips"),
    ("Bash", "rebase", "git -c core.hooksPath={nh} rebase main", "skips"),
    ("Bash", "cherry-pick", "git -c core.hooksPath={nh} cherry-pick side", "skips"),
    ("Bash", "am", "git -c core.hooksPath={nh} am {patch}", "skips"),
    ("Bash", "push", "git -c core.hooksPath={nh} push origin main", "skips"),
    # spellings git still accepts
    ("Bash", "commit", "git -c Core.HooksPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c core.HOOKSPATH={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c core.hooksPath= " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c 'core.hooksPath={nh}' " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git \"-c\" core.hooksPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c core.hook\"sPath\"={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c core.hook\\sPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c $'core.hooks\\x50ath={nh}' " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "k=core.hooksPath; git -c $k={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git --attr-source HEAD -c core.hooksPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "V={nh} git --config-env=core.hooksPath=V " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "V={nh} git --config-env core.hooksPath=V " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "V={nh} git --config-env=CORE.hookspath=V " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c include.path={inc} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c INCLUDE.PATH={inc} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c includeIf.onbranch:main.path={inc} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -c INCLUDEIF.onbranch:main.PATH={inc} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "V={inc} git --config-env=include.path=V " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "V={inc} git --config-env=includeIf.onbranch:main.path=V " + BYPASS_COMMIT[4:], "skips"),
    ("PowerShell", "commit", "git -c \"core.hooksPath={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("PowerShell", "commit", "git -c core.hook`sPath={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("PowerShell", "commit", "$k='core.hooksPath'; git -c \"$k={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "git -ccore.hooksPath={nh} " + BYPASS_COMMIT[4:], "refused"),   # one word: unknown option
    ("Bash", "commit", "V={nh} git --config-en=core.hooksPath=V " + BYPASS_COMMIT[4:], "refused"),
    ("Bash", "commit", "git -c includeIf.GITDIR:{repo}/.path={inc} " + BYPASS_COMMIT[4:], "ignored"),
    # twins: another key, or one with a subsection where git takes none
    ("Bash", "commit", "git -c user.name=x " + BYPASS_COMMIT[4:], "runs"),
    ("Bash", "commit", "git -c core.x.hooksPath={nh} " + BYPASS_COMMIT[4:], "runs"),
    ("Bash", "commit", "git -c include.x.path={inc} " + BYPASS_COMMIT[4:], "runs"),
    ("Bash", "commit", "git -c includeIf.path={inc} " + BYPASS_COMMIT[4:], "runs"),
    # the environment
    ("Bash", "commit", BYPASS_COUNT + BYPASS_COMMIT, "skips"),
    ("Bash", "push", BYPASS_COUNT + "git push origin main", "skips"),
    ("Bash", "commit", BYPASS_COUNT.replace("GIT_CONFIG", "git_config") + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_COUNT.replace("=core.hooksPath", "=Core.HOOKSPATH") + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_COUNT.replace("=core.hooksPath", "=include.path").replace("{nh}", "{inc}")
     + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_COUNT.replace("COUNT=1", "COUNT=01") + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "GIT_CONFIG_COUNT=+1 " + BYPASS_COUNT.split(" ", 1)[1] + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x GIT_CONFIG_KEY_1=core.hooksPath "
     "GIT_CONFIG_VALUE_1={nh} " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_PARAMS.replace("GIT_CONFIG_PARAMETERS", "git_config_parameters") + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "export " + BYPASS_PARAMS.strip() + "; " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "export " + BYPASS_COUNT.strip() + "; " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "declare -x " + BYPASS_PARAMS.strip() + "; " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_PARAMS.strip() + "; export GIT_CONFIG_PARAMETERS; " + BYPASS_COMMIT, "skips"),
    # the flag held in a variable
    ("Bash", "commit", "NV=--no-verify; git commit $NV --allow-empty -q -m x", "skips"),
    ("Bash", "commit", "OPTS=\"--no-verify -q\"; git commit $OPTS --allow-empty -m x", "skips"),
    ("Bash", "commit", "C=commit; git $C --no-verify --allow-empty -q -m x", "skips"),
    ("PowerShell", "commit", "$f='--no-verify'; git commit $f --allow-empty -q -m x", "skips"),
    ("Bash", "commit", "m='a --no-verify b'; git commit --allow-empty -q -m \"$m\"", "runs"),
    ("Bash", "commit", "E=; git commit --allow-empty -q -m $E --no-verify", "runs"),
    ("Bash", "commit", "OPTS=\"--no-verify -q\"; git commit \"$OPTS\" --allow-empty -m x", "refused"),
    ("PowerShell", "commit", "$o='--no-verify -q'; git commit $o --allow-empty -m x", "refused"),
    # a value only the shell knows
    ("Bash", "commit", "k=$(echo core.hooksPath); git -c \"$k={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "k=`echo core.hooksPath`; git -c $k={nh} " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "b=.hooksPath; k=\"core$b\"; git -c \"$k={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "k=(core.hooksPath); git -c \"$k={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "k=user.name; k=$(echo core.hooksPath); git -c \"$k={nh}\" " + BYPASS_COMMIT[4:], "skips"),
    ("Bash", "commit", "v=\"$(echo \"'core.hooksPath={nh}'\")\"; GIT_CONFIG_PARAMETERS=$v " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "n=$(echo 1); GIT_CONFIG_COUNT=$n GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0={nh} "
     + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "x=$(echo y); git -c \"user.name=$x\" " + BYPASS_COMMIT[4:], "runs"),
    ("Bash", "commit", "env " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "/usr/bin/env " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "env.exe " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "env -u X " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "env -C . " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", "env --unset=X " + BYPASS_PARAMS + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "$env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0='core.hooksPath'; "
     "$env:GIT_CONFIG_VALUE_0='{nh}'; " + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "$env:GIT_CONFIG_COUNT = 1; $env:GIT_CONFIG_KEY_0 = 'core.hooksPath'; "
     "$env:GIT_CONFIG_VALUE_0 = '{nh}'; " + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "$ENV:git_config_parameters=\"'core.hooksPath={nh}'\"; " + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "${env:GIT_CONFIG_PARAMETERS}=\"'core.hooksPath={nh}'\"; " + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "$v=\"'core.hooksPath={nh}'\"; $env:GIT_CONFIG_PARAMETERS=$v; " + BYPASS_COMMIT, "skips"),
    # twins of the environment
    ("Bash", "commit", BYPASS_COUNT.replace("=core.hooksPath", "=user.name") + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x GIT_CONFIG_KEY_1=core.hooksPath "
     "GIT_CONFIG_VALUE_1={nh} " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", BYPASS_COUNT.replace("COUNT=1", "COUNT=0") + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", BYPASS_COUNT.replace("GIT_CONFIG_COUNT=1 ", "") + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", BYPASS_COUNT.replace("COUNT=1", "COUNT=") + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "GIT_CONFIG_PARAMETERS= " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", BYPASS_PARAMS.strip() + "; " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "declare " + BYPASS_PARAMS.strip() + "; " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "export GIT_CONFIG_PARAMETERS; " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "export GIT_CONFIG_PARAMETERS; " + BYPASS_PARAMS.strip() + "; " + BYPASS_COMMIT, "skips"),
    ("Bash", "commit", BYPASS_PARAMS.replace("GIT_CONFIG_PARAMETERS", "git_config_parameters").strip()
     + "; export git_config_parameters; " + BYPASS_COMMIT, "skips"),
    ("PowerShell", "commit", "$env:GIT_CONFIG_PARAMETERS=\"'core.hooksPath={nh}'\"; "
     "$env:GIT_CONFIG_PARAMETERS=$null; " + BYPASS_COMMIT, "runs"),
    ("PowerShell", "commit", "$env:GIT_CONFIG_PARAMETERS=\"'core.hooksPath={nh}'\"; "
     "$env:GIT_CONFIG_PARAMETERS=$NULL; " + BYPASS_COMMIT, "runs"),
    ("PowerShell", "commit", BYPASS_COMMIT + "; $env:GIT_CONFIG_PARAMETERS=\"'core.hooksPath={nh}'\"", "runs"),
    # a backtick body runs, in and out of double quotes, and bash drops the backslash of \` \$ \\ (and \" in quotes)
    ("Bash", "commit", "echo `" + BYPASS_NV + "`", "skips"),
    ("Bash", "commit", "echo \"`" + BYPASS_NV + "`\"", "skips"),
    ("Bash", "commit", "echo `echo \\`" + BYPASS_NV + "\\``", "skips"),
    ("Bash", "commit", "echo \"`echo \\`" + BYPASS_NV + "\\``\"", "skips"),
    ("Bash", "commit", "echo `\\\n#x\n" + BYPASS_NV + "`", "skips"),
    ("Bash", "commit", "echo `git commit --allow-empty -q -m \\\"a --no-verify -m b\\\"`", "skips"),
    ("Bash", "commit", "echo \"`git commit --allow-empty -q -m \\\"a --no-verify b\\\"`\"", "runs"),
    ("Bash", "commit", "echo `echo \\`echo \\\\\\`" + BYPASS_NV + "\\\\\\`\\``", "skips"),
    # a double-quoted message runs its ticks: the first row's body is the bypass, the others leave the hook on
    ("Bash", "commit", "git commit --allow-empty -q -m \"a `" + BYPASS_NV + "`\"", "ignored"),
    ("Bash", "commit", "git commit --allow-empty -q -m \"see `printf x`\"", "runs"),
    ("Bash", "commit", "git commit --allow-empty -q -m \"add `--no-verify` docs\"", "runs"),
    ("Bash", "commit", "git commit --allow-empty -q -m 'add `--no-verify` docs'", "runs"),
    ("Bash", "commit", "echo `echo \"\\$(" + BYPASS_NV + ")\"`", "skips"),
    # twins: no body runs, or the body lists no such command
    ("Bash", "commit", "echo `git log`", "no-commit"),
    ("Bash", "commit", "echo '`" + BYPASS_NV + "`'", "no-commit"),
    ("Bash", "commit", "cat <<'EOF'\n`" + BYPASS_NV + "`\nEOF", "no-commit"),
    ("Bash", "commit", "echo \\`" + BYPASS_NV.replace(" -q", "") + "\\`", "no-commit"),
    ("Bash", "commit", "echo \"\\`" + BYPASS_NV + "\\`\"", "no-commit"),
    ("Bash", "commit", "echo `" + BYPASS_COMMIT + "`", "runs"),
    ("Bash", "commit", "echo `it's`; " + BYPASS_COMMIT, "runs"),
    ("Bash", "commit", "echo `" + BYPASS_NV + "; echo 'x`", "no-commit"),
    # a heredoc delimiter in pieces: the command after it runs, and a line equal to one piece ends nothing
    ("Bash", "commit", "cat <<'E'OF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<\"E\"OF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<E'OF'\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<E\"O\"F\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<E\\\nOF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<\\\n  EOF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<\"E\\\nOF\"\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<\\" + "EOF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<E\\OF\nbody\nEOF\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<'E F'\nbody\nE F\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<\"E\\\"F\"\nbody\nE\"F\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<'E\\F'\nbody\nE\\F\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<''\nbody\n\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<E`x y`\nbody\nE`x y`\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<$x\nbody\n$x\n" + BYPASS_NV, "skips"),
    ("Bash", "commit", "cat <<'E'OF; " + BYPASS_NV + "\nbody\nEOF", "skips"),
    ("Bash", "commit", "cat <<'E'OF\nE\n" + BYPASS_NV + "\nEOF", "no-commit"),
    ("Bash", "commit", "cat <<E\\\nOF\nE\n" + BYPASS_NV + "\nEOF", "no-commit"),
    ("Bash", "commit", "cat <<\"E\"OF\nOF\n" + BYPASS_NV + "\nEOF", "no-commit"),
    ("Bash", "commit", "cat <<'E\\F'\nEF\n" + BYPASS_NV + "\nE\\F", "no-commit"),
    ("Bash", "commit", "cat <<\"$x\"\n" + BYPASS_NV + "\n$x", "no-commit"),
]


def bypass_template(parent):
    """A repo with every hook logging, a branch per scenario, a bare remote, a patch, and what an override points at."""
    tree = os.path.join(parent, "bypass")
    repo = os.path.join(tree, "r")
    os.makedirs(os.path.join(tree, "nohooks"))
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", os.path.join(tree, "remote.git")], check=True, timeout=60)
    os.makedirs(repo)
    gate_git(repo, "init", "-q", "-b", "main")
    gate_git(repo, "remote", "add", "origin", "../remote.git")
    gate_write(repo, "a.txt", text="base")
    gate_git(repo, "add", "-A")
    gate_git(repo, "commit", "-q", "-m", "base")
    gate_git(repo, "branch", "side")
    gate_git(repo, "branch", "topic")
    for branch in ("side", "topic", "main"):
        gate_git(repo, "switch", "-q", branch)
        gate_write(repo, branch + ".txt", text=branch)
        gate_git(repo, "add", "-A")
        gate_git(repo, "commit", "-q", "-m", branch)
    with open(os.path.join(tree, "side.patch"), "wb") as fh:
        fh.write(gate_git(repo, "format-patch", "-1", "--stdout", "side"))
    with open(os.path.join(tree, "inc.cfg"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[core]\n\thooksPath = %s\n" % os.path.join(tree, "nohooks").replace("\\", "/"))
    with open(os.path.join(tree, "global.cfg"), "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = t\n\temail = t@example.com\n[core]\n\tautocrlf = false\n")
    for hook_name in set(BYPASS_HOOK.values()):
        with open(os.path.join(repo, ".git", "hooks", hook_name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#!/bin/sh\necho %s >> "$BYPASS_LOG"\n' % hook_name)
    return tree


def bypass_run(tree, parent, base_env, index, tool, kind, command):
    """Runs command in a fresh copy of tree; (the command as run, its folder, exit code, HEAD moved, hook logged)."""
    copy = shutil.copytree(tree, os.path.join(parent, "b%03d" % index))
    repo = os.path.join(copy, "r")
    slashed = copy.replace("\\", "/")
    text = command
    for slot, path in (("{nh}", "/nohooks"), ("{inc}", "/inc.cfg"), ("{repo}", "/r"), ("{patch}", "/side.patch")):
        text = text.replace(slot, slashed + path)
    log = os.path.join(copy, "hook.log")
    env = dict(base_env)
    home = os.path.join(copy, "home")   # rows run side by side: no profile, temp or config path is shared
    os.makedirs(home)
    # pwsh first runs at once in fresh profile folders hung for a minute or more in a probe; the opt-out ended it.
    env.update(BYPASS_LOG=log.replace("\\", "/"), GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true", POWERSHELL_TELEMETRY_OPTOUT="1",
               GIT_CONFIG_GLOBAL=os.path.join(copy, "global.cfg"),
               USERPROFILE=home, HOME=home, APPDATA=home, TEMP=home, TMP=home)
    if kind == "rebase":
        gate_git(repo, "switch", "-q", "topic")
    tip = "refs/remotes/origin/main" if kind == "push" else "HEAD"
    before = gate_git(repo, "rev-parse", "-q", "--verify", tip, check=False)
    script = os.path.join(copy, "row.sh")   # a file: bash.exe collapses `\\` in its command line
    with open(script, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    shell = ([os.path.join(os.environ["ProgramFiles"], "Git", "bin", "bash.exe"), script.replace("\\", "/")] if tool == "Bash"
             else ["pwsh", "-NoProfile", "-NonInteractive", "-Command", text])
    proc = subprocess.run(shell, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    logged = os.path.exists(log) and BYPASS_HOOK[kind] in open(log, encoding="utf-8").read()
    return text, repo, proc.returncode, gate_git(repo, "rev-parse", "-q", "--verify", tip, check=False) != before, logged


def bypass_runs(root):
    """Every row through the real shell and git, BYPASS_WORKERS at once; the runs in row order. The environment is read
    here once: the commit rule changes CLAUDE_PLUGIN_DATA in this process meanwhile, and no row may see it."""
    parent = os.path.join(root, "bypass-runs")
    os.makedirs(parent)
    base_env = {k: v for k, v in os.environ.items()
                if not k.upper().startswith("GIT_") and k != "CLAUDE_PLUGIN_DATA"}
    tree = bypass_template(parent)
    with ThreadPoolExecutor(max_workers=BYPASS_WORKERS) as pool:
        return list(pool.map(lambda numbered: bypass_run(tree, parent, base_env, numbered[0], *numbered[1][:3]),
                             enumerate(BYPASS_ROWS)))


def hook_bypass_checks(runs):
    """deny == the hook really stayed silent on a command git ran, row by row; nothing here is a canonical spelling alone.
    The judgement stays on this thread, in row order."""
    failed = ran = 0
    for (tool, kind, command, effect), (text, repo, code, moved, logged) in zip(BYPASS_ROWS, runs):
        ran += 1
        seen = {"skips": code == 0 and moved and not logged, "runs": code == 0 and logged,
                "ignored": code == 0 and logged, "refused": code != 0 and not logged,
                "no-commit": code == 0 and not moved and not logged}[effect]
        got = next((f.rule for f in shell_guard.check_git(tool, text, repo) if f.deny), None)
        want = "git-no-verify" if effect in ("skips", "ignored") else None
        if not seen or got != want:
            failed += 1
            print("BYPASS FAIL  want=%s %s got=%s real=%s code=%s moved=%s logged=%s  %s: %r"
                  % (effect, want, got, seen, code, moved, logged, tool, command[:100]))
    # a run that executed no row, or lost its controls, proves nothing
    if ran != len(BYPASS_ROWS) or sum(1 for r in BYPASS_ROWS if r[3] == "skips") < 50:
        failed += 1
        print("BYPASS FAIL  rows run: %d of %d" % (ran, len(BYPASS_ROWS)))
    print("hook bypass rows: %d/%d pass" % (ran - failed, ran))
    return failed, ran


def main():
    import stat
    root = tempfile.mkdtemp(prefix="sg-")
    # The shell and git runs of the bypass rows wait on process creation, so they run beside the commit rule's checks;
    # those stop at the first check that reads the clock (settle) until every row has ended.
    background = ThreadPoolExecutor(max_workers=1)
    rows = background.submit(bypass_runs, root)
    try:
        failed = total = 0
        section_failed, section_total = commit_gate_checks(root, rows.result)
        failed += section_failed
        total += section_total
        section_failed, section_total = hook_bypass_checks(rows.result())
        failed += section_failed
        total += section_total
        print("# commit-gate: %d passed, %d failed" % (total - failed, failed))
        return 1 if failed else 0
    finally:
        background.shutdown(wait=True)
        def writable(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)   # git's object files are read-only
            func(path)
        shutil.rmtree(root, onerror=writable)
        shutil.rmtree(FAKE_HOME, ignore_errors=True)
        for name, value in SAVED_ENV.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


sys.exit(main())
