"""Checks for variant_run.py, run end to end as a subprocess on fixtures in a temp folder.

python -I test_variant_run.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "variant_run.py")
AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "agents")
failures, passed = [], 0

PRELUDE = "Prelude.\n\n"
GLOBAL = "# Global\n\nIntro: naïve façade — Grüße.\n\n"
ALPHA = "## Alpha\n\nalpha body\n\n"
CHILD = "### Alpha child\n\nchild body\n\n"
GRANDCHILD = "#### Alpha grandchild\n\ngrandchild body\n\n"
BETA = "## Beta\n\nbeta body\n\n```powershell\n~~~\n# Beta code comment\n```\n\n"
TWIN = "## Twin\n\none\n\n### Twin\n\ntwo\n\n"
TAIL = "# Tail\n\ntail body\n"
SOURCE = PRELUDE + GLOBAL + ALPHA + CHILD + GRANDCHILD + BETA + TWIN + TAIL

STUB = r'''import json, os, re, sys
MODE = {mode!r}
with open({argv_file!r}, "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh)
with open({env_file!r}, "w", encoding="utf-8") as fh:
    json.dump(os.environ.get("HARNESS_RULES_DROP"), fh)
marker = None
for name in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md")):
    if os.path.exists(name):
        found = re.search(r"print (\S+)\.$", open(name, encoding="utf-8").read(), re.M)
        marker = found.group(1) if found else marker
if MODE == "empty":
    print("stub: not logged in", file=sys.stderr)
    sys.exit(1)
brief = "Reply with the variant marker." + (" It is " + marker if MODE == "leak" else "")
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else 'subagent_type "rigger-core:worker"'
wanted = re.search(r'subagent_type "([^"]+)"', prompt).group(1)
agent = {{"wrongagent": "general-purpose", "bareagent": "worker"}}.get(MODE, wanted)
definition = os.path.join({agents_dir!r}, agent.split(":")[-1] + ".md")
# As in a real run: an agent with omitClaudeMd: true never loads the copy, so it cannot know the marker.
blind = os.path.exists(definition) and re.search(r"^omitClaudeMd: true$", open(definition, encoding="utf-8").read(), re.M)
answer = "NO-MARKER" if MODE.endswith("nomarker") or blind else "STATUS: DONE " + marker
synchronous = not MODE.startswith("async")
lines = [
    {{"type": "system", "subtype": "hook_started", "hook_name": "SessionStart"}},
    {{"type": "system", "subtype": "init", "agents": ["rigger-core:worker"]}},
    {{"type": "assistant", "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "tool_use", "id": "toolu_1", "name": "Agent",
         "input": {{"subagent_type": agent, "prompt": brief}}}}]}}}},
    {{"type": "assistant", "parent_tool_use_id": "toolu_1", "message": {{"content": [
        {{"type": "text", "text": answer}}]}}}},
]
if MODE != "noresult":
    lines.append({{"type": "user", "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {{"type": "text", "text": answer if synchronous else "Async agent launched successfully."}}]}}]}}}})
if not synchronous:
    lines.append({{"type": "system", "subtype": "task_notification", "tool_use_id": "toolu_1",
                  "status": "completed", "handback_report": {{"text": answer}}}})
lines += [
    {{"type": "assistant", "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "text", "text": "The marker is " + marker}}]}}}},
    {{"type": "result", "subtype": "success", "total_cost_usd": 0.0123, "result": marker}},
]
print("\n".join(json.dumps(line) for line in lines))
'''


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
    else:
        failures.append(name)
        print(f"{name}: expected {want!r}, got {got!r}")


def run(*args, home=None, config=None, inherited=None):
    """The script with CLAUDE_CONFIG_DIR and HARNESS_RULES_DROP only as given, whatever this shell has."""
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CONFIG_DIR", "HARNESS_RULES_DROP")}
    if home:
        env.update(USERPROFILE=home, HOME=home)
    if config:
        env["CLAUDE_CONFIG_DIR"] = config
    if inherited:
        env["HARNESS_RULES_DROP"] = inherited
    proc = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, timeout=120, env=env)
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def read(path):
    """The file's text, or None when it is missing, so a mutation shows as a failed check, not a crash."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def listing(folder):
    return sorted(os.path.relpath(os.path.join(root, name), folder).replace("\\", "/")
                  for root, _, names in os.walk(folder) for name in names)


def make_link(target, link):
    """A directory junction on Windows (no admin needed), a symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link, target_is_directory=True)


def main():
    global CASE_INSENSITIVE
    folder = tempfile.mkdtemp(prefix="variant-")
    CASE_INSENSITIVE = os.path.isdir(folder.upper()) and os.path.isdir(folder.lower())
    try:
        source = os.path.join(folder, "source.md")
        write(source, SOURCE)
        excluded = source.replace("\\", "/")
        count = 0

        def workdir():
            nonlocal count
            count += 1
            path = os.path.join(folder, f"w{count}")
            os.makedirs(path)
            return path

        def dropped(*names):
            path = workdir()
            args = ["prepare", path, "--source", source]
            for name in names:
                args += ["--drop-section", name]
            code, _, err = run(*args)
            return read(os.path.join(path, "CLAUDE.md")) if code == 0 else f"exit {code}: {err}"

        # section drop at each level, nested subsections, the last section, a fenced "#" line
        check("level 1 drops everything to the next level-1 heading", dropped("Global"), PRELUDE + TAIL)
        check("level 2 drops its nested subsections", dropped("Alpha"),
              PRELUDE + GLOBAL + BETA + TWIN + TAIL)
        check("level 3 drops its level-4 child and stops at level 2", dropped("Alpha child"),
              PRELUDE + GLOBAL + ALPHA + BETA + TWIN + TAIL)
        check("level 4 stops at the next level-2 heading", dropped("Alpha grandchild"),
              PRELUDE + GLOBAL + ALPHA + CHILD + BETA + TWIN + TAIL)
        check("the last section is dropped to the end of the file", dropped("Tail"),
              PRELUDE + GLOBAL + ALPHA + CHILD + GRANDCHILD + BETA + TWIN)
        check("a '#' line in a code fence is not a section boundary", dropped("Beta"),
              PRELUDE + GLOBAL + ALPHA + CHILD + GRANDCHILD + TWIN + TAIL)
        check("a section and its own subsection together", dropped("Alpha", "Alpha child"),
              PRELUDE + GLOBAL + BETA + TWIN + TAIL)
        check("two sections in one call", dropped("Alpha", "Tail"), PRELUDE + GLOBAL + BETA + TWIN)
        check("no drop: an exact copy", dropped(), SOURCE)

        path = workdir()
        code, out, _ = run("prepare", path, "--source", source, "--drop-section", "Alpha")
        check("prints chars removed and written", out.split(" to ")[0],
              f"removed {len(ALPHA + CHILD + GRANDCHILD)} chars, "
              f"wrote {len(SOURCE) - len(ALPHA + CHILD + GRANDCHILD)} chars")

        # fail closed: nothing written
        for label, names in (("an unknown heading", ["Nope"]),
                             ("an unknown heading after a known one", ["Alpha", "Nope"]),
                             ("a heading text inside a code fence", ["Beta code comment"])):
            path = workdir()
            args = ["prepare", path, "--source", source]
            for name in names:
                args += ["--drop-section", name]
            code, _, err = run(*args)
            check(f"{label}: exit 2 naming it, nothing written", (code, names[-1] in err, listing(path)),
                  (2, True, []))
        path = workdir()
        code, _, err = run("prepare", path, "--source", source, "--drop-section", "Twin")
        check("a heading that occurs twice: exit 2, nothing written", (code, "2 times" in err, listing(path)),
              (2, True, []))

        # marker line
        check("--marker appends the marker line", dropped_with_marker(workdir(), source),
              SOURCE + "When asked for the variant marker, print VM-42.\n")
        unterminated = os.path.join(folder, "unterminated.md")
        write(unterminated, "# A\n\nbody")
        check("--marker after a last line without a newline starts its own line",
              dropped_with_marker(workdir(), unterminated),
              "# A\n\nbody\nWhen asked for the variant marker, print VM-42.\n")

        # copy placement
        own = workdir()
        write(os.path.join(own, "CLAUDE.md"), "fixture own\n")
        run("prepare", own, "--source", source, "--drop-section", "Tail")
        check("work dir with its own CLAUDE.md: the copy goes to .claude/CLAUDE.md",
              (read(os.path.join(own, "CLAUDE.md")), read(os.path.join(own, ".claude", "CLAUDE.md"))),
              ("fixture own\n", PRELUDE + GLOBAL + ALPHA + CHILD + GRANDCHILD + BETA + TWIN))
        bare = workdir()
        run("prepare", bare, "--source", source)
        run("prepare", bare, "--source", source, "--drop-section", "Global")
        check("work dir without one: the copy goes to CLAUDE.md, also on a second prepare",
              (read(os.path.join(bare, "CLAUDE.md")), os.path.exists(os.path.join(bare, ".claude", "CLAUDE.md"))),
              (PRELUDE + TAIL, False))
        both = workdir()
        write(os.path.join(both, "CLAUDE.md"), "fixture own\n")
        os.makedirs(os.path.join(both, ".claude"))
        write(os.path.join(both, ".claude", "CLAUDE.md"), "fixture nested\n")
        code, _, err = run("prepare", both, "--source", source)
        check("work dir with CLAUDE.md and .claude/CLAUDE.md: exit 2, nothing written",
              (code, "both" in err, listing(both), read(os.path.join(both, ".claude", "CLAUDE.md"))),
              (2, True, [".claude/CLAUDE.md", "CLAUDE.md"], "fixture nested\n"))

        # guard: no write under ~/.claude or over the source; a fake home through USERPROFILE and HOME
        ancestor = os.path.join(folder, "ancestor")
        home = os.path.join(ancestor, "home")
        os.makedirs(os.path.join(home, ".claude", "sub"))
        write(os.path.join(home, ".claude", "CLAUDE.md"), SOURCE)
        before = listing(ancestor)
        control = workdir()
        code, _, _ = run("prepare", control, home=home)
        check("control: with the fake home a normal work dir prepares and excludes the fake source",
              (code, json.loads(read(os.path.join(control, ".claude", "settings.local.json")) or "null")),
              (0, {"claudeMdExcludes": [os.path.join(home, ".claude", "CLAUDE.md").replace("\\", "/")]}))
        link = os.path.join(folder, "link")
        make_link(os.path.join(home, ".claude", "sub"), link)
        cases = [("the home dir", home, "home dir or an ancestor"),
                 ("an ancestor of ~/.claude", ancestor, "home dir or an ancestor"),
                 ("a dir inside ~/.claude", os.path.join(home, ".claude", "sub"), "refusing to write under"),
                 ("a link into ~/.claude", link, "refusing to write under")]
        if CASE_INSENSITIVE:
            cases.append(("the home dir spelled upper-case", home.upper(), "home dir or an ancestor"))
        try:
            for label, path, reason in cases:
                code, _, err = run("prepare", path, home=home)
                check(f"work dir is {label}: exit 2, nothing written", (code, reason in err, listing(ancestor)),
                      (2, True, before))
        finally:
            os.unlink(link) if os.path.islink(link) else os.rmdir(link)
        same = workdir()
        run("prepare", same, "--source", source)
        kept = listing(same), read(os.path.join(same, "CLAUDE.md"))
        code, _, err = run("prepare", same, "--source", os.path.join(same, "CLAUDE.md"), "--drop-section", "Tail")
        check("the copy target is the source: exit 2, nothing written",
              (code, "overwrite the source" in err, (listing(same), read(os.path.join(same, "CLAUDE.md")))),
              (2, True, kept))
        if CASE_INSENSITIVE:
            code, _, err = run("prepare", same, "--source", os.path.join(same, "CLAUDE.md").upper())
            check("the copy target is the source spelled upper-case: exit 2, nothing written",
                  (code, "overwrite the source" in err, (listing(same), read(os.path.join(same, "CLAUDE.md")))),
                  (2, True, kept))
        else:
            print("# skipped 2 upper-case spelling checks: the file system is case-sensitive")

        # guard: the config dir in effect, as well as ~/.claude
        area = os.path.join(folder, "config-area")
        config = os.path.join(area, "cfg")
        inside = os.path.join(config, "x")
        os.makedirs(inside)
        sibling = os.path.join(area, "cfg2")
        os.makedirs(sibling)
        respelled = (lambda path: path.upper()) if CASE_INSENSITIVE else (lambda path: path)
        config_link = os.path.join(folder, "config-link")
        make_link(inside, config_link)
        try:
            for label, path, setting, reason in (
                    ("a dir under CLAUDE_CONFIG_DIR", inside, config, "refusing to write under"),
                    ("a dir under it, other case and a trailing slash", respelled(inside) + "/", config,
                     "refusing to write under"),
                    ("a dir under it, the variable in other case with a trailing slash", inside,
                     respelled(config) + "/", "refusing to write under"),
                    ("a link into it", config_link, config, "refusing to write under"),
                    ("an ancestor of it", area, config, "home dir or an ancestor"),
                    ("a dir under ~/.claude while the variable points elsewhere",
                     os.path.join(home, ".claude", "sub"), config, "refusing to write under")):
                code, _, err = run("prepare", path, "--source", source, home=home, config=setting)
                check(f"work dir is {label}: exit 2, nothing written",
                      (code, reason in err, listing(area), listing(os.path.join(home, ".claude", "sub"))),
                      (2, True, [], []))
        finally:
            os.unlink(config_link) if os.path.islink(config_link) else os.rmdir(config_link)
        code, _, err = run("prepare", sibling, "--source", source, home=home, config=config)
        check("a sibling whose name starts with the config dir's is allowed",
              (code, err, read(os.path.join(sibling, "CLAUDE.md"))), (0, "", SOURCE))
        code, _, err = run("prepare", inside, "--source", source, home=home)
        check("control: without CLAUDE_CONFIG_DIR the same dir is allowed",
              (code, err, read(os.path.join(inside, "CLAUDE.md"))), (0, "", SOURCE))

        # the default source: the config dir in effect, not ~/.claude
        config_home = os.path.join(folder, "source-home")
        own_config = os.path.join(folder, "source-config")
        os.makedirs(os.path.join(config_home, ".claude"))
        os.makedirs(own_config)
        write(os.path.join(config_home, ".claude", "CLAUDE.md"), "home source\n")
        write(os.path.join(own_config, "CLAUDE.md"), "config source\n")
        path = workdir()
        code, _, _ = run("prepare", path, home=config_home, config=own_config)
        check("no --source with CLAUDE_CONFIG_DIR: its CLAUDE.md is copied and excluded",
              (code, read(os.path.join(path, "CLAUDE.md")),
               json.loads(read(os.path.join(path, ".claude", "settings.local.json")))),
              (0, "config source\n", {"claudeMdExcludes": [os.path.join(own_config, "CLAUDE.md").replace("\\", "/")]}))
        path = workdir()
        code, _, _ = run("run", path, "--", sys.executable, "-c", "", home=config_home, config=own_config)
        check("run with no --source reads the same file", (code, read(os.path.join(path, "CLAUDE.md"))),
              (0, "config source\n"))
        path = workdir()
        code, _, _ = run("prepare", path, home=config_home)
        check("control: no --source and no CLAUDE_CONFIG_DIR: ~/.claude/CLAUDE.md",
              (code, read(os.path.join(path, "CLAUDE.md"))), (0, "home source\n"))

        # .claude/variant-run.json: only what prepare records is followed
        precious = os.path.join(folder, "precious.txt")
        write(precious, "precious\n")
        for label, content in (("a path above the work dir", '{"copy": "../precious.txt"}'),
                               ("an absolute path", json.dumps({"copy": precious})),
                               ("a subfolder", '{"copy": "sub/x.md"}'),
                               ("the work dir itself", '{"copy": "."}'),
                               ("not a string", '{"copy": ["CLAUDE.md"]}'),
                               ("no copy key", '{}'),
                               ("not an object", '["copy"]'),
                               ("broken JSON", '{"copy": ')):
            path = workdir()
            os.makedirs(os.path.join(path, ".claude"))
            write(os.path.join(path, ".claude", "variant-run.json"), content)
            code, _, err = run("prepare", path, "--source", source)
            check(f"variant-run.json with {label}: exit 2, a clean message, nothing written",
                  (code, err.startswith("variant_run: "), "Traceback" in err, listing(path), read(precious)),
                  (2, True, False, [".claude/variant-run.json"], "precious\n"))
        outside = os.path.join(folder, "outside")
        os.makedirs(outside)
        path = workdir()
        make_link(outside, os.path.join(path, ".claude"))
        try:
            code, _, err = run("prepare", path, "--source", source)
            check("a .claude link out of the work dir: exit 2, nothing written",
                  (code, "outside the work dir" in err, listing(outside), os.path.exists(os.path.join(path, "CLAUDE.md"))),
                  (2, True, [], False))
        finally:
            link = os.path.join(path, ".claude")
            os.unlink(link) if os.path.islink(link) else os.rmdir(link)

        # settings merge
        path = workdir()
        os.makedirs(os.path.join(path, ".claude"))
        settings_file = os.path.join(path, ".claude", "settings.local.json")
        write(settings_file, json.dumps({"permissions": {"allow": ["Bash(ls)"]},
                                         "claudeMdExcludes": ["/other/CLAUDE.md"]}))
        want = {"permissions": {"allow": ["Bash(ls)"]}, "claudeMdExcludes": ["/other/CLAUDE.md", excluded]}
        run("prepare", path, "--source", source)
        check("settings merge keeps existing keys and adds the source", json.loads(read(settings_file)), want)
        run("prepare", path, "--source", source)
        check("a second prepare leaves the settings unchanged", json.loads(read(settings_file)), want)
        fresh = workdir()
        run("prepare", fresh, "--source", source)
        check("new settings exclude the source path, forward slashes",
              json.loads(read(os.path.join(fresh, ".claude", "settings.local.json"))),
              {"claudeMdExcludes": [excluded]})
        broken = workdir()
        os.makedirs(os.path.join(broken, ".claude"))
        write(os.path.join(broken, ".claude", "settings.local.json"), "{not json")
        code, _, _ = run("prepare", broken, "--source", source)
        check("unreadable settings: exit 2, nothing written", (code, listing(broken)),
              (2, [".claude/settings.local.json"]))
        for label, content in (("settings that are not an object", "[]"),
                               ("claudeMdExcludes that is not a list", '{"claudeMdExcludes": "x"}')):
            path = workdir()
            os.makedirs(os.path.join(path, ".claude"))
            write(os.path.join(path, ".claude", "settings.local.json"), content)
            code, _, _ = run("prepare", path, "--source", source)
            check(f"{label}: exit 2, nothing written",
                  (code, listing(path), read(os.path.join(path, ".claude", "settings.local.json"))),
                  (2, [".claude/settings.local.json"], content))

        # run
        path = workdir()
        code, out, _ = run("run", path, "--source", source, "--drop-section", "Alpha", "--",
                           sys.executable, "-c", "import os, sys; print(os.getcwd()); sys.exit(7)")
        check("run passes the exit code through", code, 7)
        check("run starts the command in the work dir after prepare",
              (out.strip().splitlines()[-1], read(os.path.join(path, "CLAUDE.md"))),
              (path, PRELUDE + GLOBAL + BETA + TWIN + TAIL))
        path = workdir()
        code, _, _ = run("run", path, "--source", source)
        check("run without a command: exit 2, nothing written", (code, listing(path)), (2, []))

        # verify against a stub claude
        agents = os.path.join(folder, "agents.json")
        write(agents, "{}")
        for mode, want_code, want_word, reason in (
                ("marker", 0, "PASS", "contains the marker"), ("nomarker", 1, "FAIL", "lacks the marker"),
                ("async", 0, "PASS", "contains the marker"), ("async-nomarker", 1, "FAIL", "lacks the marker"),
                ("empty", 1, "FAIL", "no init event"), ("leak", 1, "FAIL", "leaked into the brief"),
                ("wrongagent", 1, "FAIL", "no Agent call to rigger-core:worker"),
                ("bareagent", 1, "FAIL", "no Agent call to rigger-core:worker"),
                ("noresult", 1, "FAIL", "no tool_result for the rigger-core:worker")):
            path = workdir()
            stub = os.path.join(folder, f"stub-{mode}.py")
            argv_file = os.path.join(folder, f"argv-{mode}.json")
            write(stub, STUB.format(mode=mode, argv_file=argv_file, env_file=os.path.join(folder, "env-unused.json"),
                                  agents_dir=AGENTS))
            code, out, err = run("verify", path, "--claude", stub, "--agents", agents, home=home)
            check(f"verify with stub mode {mode}: exit and verdict",
                  (code, out.strip().splitlines()[-1].split(":")[0:2], reason in out),
                  (want_code, ["verify", " " + want_word], True))
            if mode == "marker":
                argv = json.loads(read(argv_file))
                flags = {flag: argv[argv.index(flag) + 1] for flag in ("--model", "--max-budget-usd",
                                                                        "--output-format", "--agents")}
                check("verify passes haiku, the budget, stream-json and the agents file", flags,
                      {"--model": "haiku", "--max-budget-usd": "0.2", "--output-format": "stream-json",
                       "--agents": agents})
                check("verify passes -p, --verbose and --no-session-persistence",
                      all(flag in argv for flag in ("-p", "--verbose", "--no-session-persistence")), True)
                check("verify reports the cost", "cost 0.0123" in out, True)
                check("verify asks for the installed agent name",
                      'subagent_type "rigger-core:worker"' in argv[argv.index("-p") + 1], True)
            if mode == "empty":
                check("verify keeps claude's stderr", "stub: not logged in" in err, True)
        code, _, err = run("verify", workdir(), "--claude", stub, "--agents", "agents.json")
        check("verify refuses a relative --agents path", (code, "absolute" in err), (2, True))

        # --drop-rule: the environment the child really got, through verify and through run
        env_file = os.path.join(folder, "child-env.json")
        rules_stub = os.path.join(folder, "stub-rules.py")
        write(rules_stub, STUB.format(mode="marker", argv_file=os.path.join(folder, "argv-rules.json"),
                                      env_file=env_file, agents_dir=AGENTS))

        path = workdir()
        code, _, _ = run("verify", path, "--claude", rules_stub, home=config_home, config=own_config)
        check("verify copies the config dir's CLAUDE.md and excludes it",
              (code, read(os.path.join(path, "CLAUDE.md")).split("\n")[0],
               json.loads(read(os.path.join(path, ".claude", "settings.local.json")))),
              (0, "config source", {"claudeMdExcludes": [os.path.join(own_config, "CLAUDE.md").replace("\\", "/")]}))

        def through_verify(path, flags, **kwargs):
            return run("verify", path, "--claude", rules_stub, *flags, home=home, **kwargs)

        def through_run(path, flags, **kwargs):
            return run("run", path, "--source", source, "--marker", "VM-42", *flags, "--",
                       sys.executable, "-I", rules_stub, **kwargs)

        def child_got(launch, flags, **kwargs):
            """(exit code, HARNESS_RULES_DROP in the child: None when absent, NOT STARTED without a child)."""
            if os.path.exists(env_file):
                os.remove(env_file)
            code = launch(workdir(), flags, **kwargs)[0]
            return code, json.loads(read(env_file)) if os.path.exists(env_file) else "NOT STARTED"

        for name, launch in (("verify", through_verify), ("run", through_run)):
            check(f"{name}: two --drop-rule values reach the child joined with ;",
                  child_got(launch, ["--drop-rule", "a.md", "--drop-rule", "## Heading"]), (0, "a.md;## Heading"))
            check(f"{name}: one --drop-rule value reaches the child alone",
                  child_got(launch, ["--drop-rule", "a.md"]), (0, "a.md"))
            check(f"{name}: no flag: the variable is absent in the child", child_got(launch, []), (0, None))
            check(f"{name}: no flag: the parent's value is not inherited",
                  child_got(launch, [], inherited="parent.md"), (0, None))
            check(f"{name}: the flag replaces the parent's value",
                  child_got(launch, ["--drop-rule", "a.md"], inherited="parent.md"), (0, "a.md"))
            for label, value in (("a ;", "a.md;b.md"), ("a newline", "a.md\nb.md"), ("a carriage return", "a.md\rb.md"),
                                 ("nothing", ""), ("only spaces", "  ")):
                path = workdir()
                code, _, err = run("verify", path, "--claude", rules_stub, "--drop-rule", value, home=home) \
                    if name == "verify" else run("run", path, "--source", source, "--drop-rule", value, "--",
                                                 sys.executable, "-I", rules_stub)
                check(f"{name}: a value holding {label} exits 2 naming the flag, nothing written",
                      (code, "--drop-rule" in err, listing(path)), (2, True, []))
                check(f"{name}: a value holding {label} never starts the child",
                      child_got(launch, ["--drop-rule", "ok.md", "--drop-rule", value]), (2, "NOT STARTED"))
    finally:
        shutil.rmtree(folder)
    print(f"# variant-run: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


def dropped_with_marker(path, source):
    code, _, err = run("prepare", path, "--source", source, "--marker", "VM-42")
    return read(os.path.join(path, "CLAUDE.md")) if code == 0 else f"exit {code}: {err}"


sys.exit(main())
