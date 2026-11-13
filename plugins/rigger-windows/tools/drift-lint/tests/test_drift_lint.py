"""Checks for drift_lint.py, run end to end on temp fixtures: every input points at the fixture.

python -I test_drift_lint.py
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

if os.name != "nt":
    print("# drift-lint: skipped on %s: Windows-only (the fixtures lock folders with icacls)" % sys.platform)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "drift_lint.py")
FINDING = re.compile(r"^(.+):(\d+)  (\S+)  (.*)$")
failures, passed = [], 0


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
    else:
        failures.append(name)
        print(f"{name}: expected {want!r}, got {got!r}")


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


class Fixture:
    """A harness in miniature: rule file, skill repo, ~/.claude/skills, agents, plugins, PATH."""

    def __init__(self, root):
        self.root = root
        j = lambda *p: os.path.join(root, *p)
        self.claude, self.skills, self.user = j("home", ".claude", "CLAUDE.md"), j("skills"), j("user-skills")
        self.no_baseline = write(j("empty-baseline.txt"))
        self.empty = j("empty")
        os.makedirs(self.empty)
        self.agents, self.plugins, self.repo, self.home = j("agents"), j("plugins"), j("repo"), j("home")
        self.bin, self.git_cmd = j("bin"), j("git", "cmd")
        self.missing = j("no-such-dir", "gone.md")
        self.existing = write(j("exists", "here.md"))
        self.spaced = os.path.dirname(write(j("exists", "with space", "bin", "f.md")))
        write(j("exists", "name-of-a-file.md"))
        for entry in ("my report 2026.md", "two words here.md", "session name 12", "Common Files", "Windows Kits"):
            write(os.path.join(self.spaced, entry, "x.md") if "." not in entry else os.path.join(self.spaced, entry))
        write(j("repo", "tools", "real-tool", "run.py"))
        write(j("repo", "templates", "brief.md"))
        write(j("skills", "real-skill", "SKILL.md"),
              "---\nname: real-skill\n---\nRun `realcmd --x` on `templates/page.html`.\n")
        write(j("skills", "real-skill", "templates", "page.html"))
        write(j("user-skills", "own-skill", "SKILL.md"), "---\nname: own-skill\n---\nNothing here.\n")
        write(j("user-skills", "wp-fake", "SKILL.md"), "Run `frobnicate-up --x`.\n")
        write(j("linked-target", "SKILL.md"), "Run `frobnicate-linked --x`.\n")
        if os.name == "nt":   # a junction needs no admin rights; a symlink does
            subprocess.run(["cmd", "/c", "mklink", "/J", j("user-skills", "linked-skill"), j("linked-target")],
                           capture_output=True, check=True)
        else:
            os.symlink(j("linked-target"), j("user-skills", "linked-skill"), target_is_directory=True)
        write(j("agents", "real-agent.md"), "---\nname: real-agent\n---\nDo it.\n")
        plug = j("plugcache")
        write(os.path.join(plug, "skills", "plug-skill", "SKILL.md"), "---\nname: plug-skill\n---\n")
        write(os.path.join(plug, "agents", "plug-agent.md"), "---\nname: plug-agent\n---\n")
        write(os.path.join(plug, "rules", "plug-rule.md"), "Nothing here.\n")  # --plugins-dir needs a rules file
        write(j("plugins", "installed_plugins.json"),
              json.dumps({"version": 2, "plugins": {"fakeplug@market": [{"installPath": plug}]}}))
        write(j("home", ".claude", "real.md"))
        for exe in ("realcmd.exe", "ssh.exe", "python.exe"):
            write(j("bin", exe))
        write(j("git", "cmd", "git.exe"))
        write(j("git", "usr", "bin", "sedx.exe"))
        self.market = j("market")
        core = j("market", "plugins", "rigger-core")
        self.market_skill = write(os.path.join(core, "skills", "s", "SKILL.md"))
        self.market_rules = write(os.path.join(core, "rules", "r.md"))
        write(os.path.join(core, "agents", "worker.md"), "---\nname: worker\n---\n")
        write(os.path.join(core, "templates", "x.md"))
        write(os.path.join(core, "rules", "notes.txt"), "Follow `templates/in-a-txt.md`.\n")
        write(os.path.join(core, "rules", "nested", "deep.md"), "Follow `templates/in-a-subfolder.md`.\n")
        os.makedirs(os.path.join(core, "rules", "folder.md"))
        sibling =j("market", "plugins", "other-plugin")
        write(os.path.join(sibling, "skills", "o", "SKILL.md"), "Nothing here.\n")
        write(os.path.join(sibling, "agents", "helper.md"), "---\nname: helper\n---\n")
        write(os.path.join(sibling, "templates", "only-other.md"))

    def run_market(self, skill, rules="Nothing here.\n", *extra, repo=None, cwd=None):
        """The fixture marketplace as --repo: skills roots and rules come from the repo itself."""
        write(self.market_skill, skill)
        write(self.market_rules, rules)
        write(self.claude, "Nothing to see.\n")
        args = ["--plugins-dir", self.plugins, "--repo", repo or self.market, "--home", self.home,
                "--path", os.pathsep.join([self.bin, self.git_cmd]), "--claude-md", self.claude,
                "--user-skills", self.empty, "--agents-dir", self.agents, "--baseline", self.no_baseline, *extra]
        proc = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, timeout=60, cwd=cwd)
        return proc.returncode, proc.stdout.decode("utf-8")

    def market_hits(self, skill, rules="Nothing here.\n", *extra, **kwargs):
        """Findings of a run_market: (file name, line, check, reference)."""
        return [(os.path.basename(m.group(1)), int(m.group(2)), m.group(3), m.group(4))
                for m in map(FINDING.match, self.run_market(skill, rules, *extra, **kwargs)[1].splitlines()) if m]

    def run(self, text, *extra):
        write(self.claude, text)
        return self.run_args(["--claude-md", self.claude, "--skills-root", self.skills, "--user-skills", self.user,
                              "--agents-dir", self.agents, "--baseline", self.no_baseline, *extra])

    def run_args(self, args, script=SCRIPT):
        """The script with only args plus the plugins, repo, home and PATH of the fixture."""
        args = ["--plugins-dir", self.plugins, "--repo", self.repo, "--home", self.home,
                "--path", os.pathsep.join([self.bin, self.git_cmd]), *args]
        proc = subprocess.run([sys.executable, "-I", script, *args], capture_output=True, timeout=60)
        return proc.returncode, proc.stdout.decode("utf-8")

    def run_no_repo(self, text, env_repos, *extra):
        """The script with no --repo; HARNESS_DRIFT_REPOS = env_repos (unset when None).
        (exit code, stdout, stderr)."""
        write(self.claude, text)
        env = {k: v for k, v in os.environ.items() if k != "HARNESS_DRIFT_REPOS"}
        if env_repos is not None:
            env["HARNESS_DRIFT_REPOS"] = env_repos
        args = ["--plugins-dir", self.plugins, "--home", self.home, "--path", os.pathsep.join([self.bin, self.git_cmd]),
                "--claude-md", self.claude, "--user-skills", self.user, "--agents-dir", self.agents,
                "--baseline", self.no_baseline, *extra]
        proc = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, timeout=60, env=env)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def baseline(self, *entries):
        """A baseline file holding the entries, each `(file, check, reference)`."""
        return write(os.path.join(self.root, "baseline.txt"),
                     "# reason\n" + "".join("\t".join(e) + "\n" for e in entries))

    def hits(self, text, *extra):
        """Findings in the rule file: (line, check, reference)."""
        return [(int(m.group(2)), m.group(3), m.group(4))
                for m in map(FINDING.match, self.run(text, *extra)[1].splitlines())
                if m and os.path.normcase(m.group(1)) == os.path.normcase(self.claude)]


def repo_root():
    """The harness repo holding this tool (its .claude-plugin/marketplace.json), or None when installed."""
    here = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(here, ".claude-plugin", "marketplace.json")):
            return here
        here = os.path.dirname(here)
    return None


def real_tree_gate(folder):
    """The repo's own plugins against an empty home: 0 findings and files read, then one planted fake caught."""
    root = repo_root()
    if root is None:
        print("# drift-lint: the real-tree gate needs the harness repo around the tool; not run")
        return
    home = os.path.join(folder, "empty-home")
    write(os.path.join(home, ".claude", "CLAUDE.md"))
    os.makedirs(os.path.join(home, ".claude", "skills"))
    os.makedirs(os.path.join(home, ".claude", "agents"))
    env = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"), HOME=home, USERPROFILE=home)
    base = os.path.join(HERE, "repo-baseline.txt")

    def run(cwd):
        proc = subprocess.run([sys.executable, "-I", SCRIPT, "--repo", ".", "--plugins-dir", "plugins",
                               "--baseline", base], capture_output=True, timeout=120, cwd=cwd, env=env)
        return proc.returncode, proc.stdout.decode("utf-8").splitlines()

    code, lines = run(root)
    m = re.fullmatch(r"# drift-lint: (\d+) files, 0 findings, (\d+) baselined, 0 in upstream copies, (\d+) rules files",
                     lines[-1] if lines else "")
    files, baselined, rules = (int(g) for g in m.groups()) if m else (0, 0, 0)
    check("real tree: exit 0, only the summary line, 0 findings, 0 upstream copies from the empty home",
          (code, len(lines), bool(m)), (0, 1, True))
    check("real tree: files and rules files were read, every baseline entry matched at least one finding",
          (files > 0, rules > 0, baselined >= len(load_entries(base))), (True, True, True))
    copy = os.path.join(folder, "real-copy")
    shutil.copytree(os.path.join(root, "plugins"), os.path.join(copy, "plugins"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    planted = sorted(glob.glob(os.path.join(copy, "plugins", "*", "rules", "*.md")))[0]
    with open(planted, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\nSee `tools/planted-gone/x.py`.\n")
    code, lines = run(copy)
    check("real tree: positive control, a broken reference planted in a copy of a rules file is the only finding",
          (code, [(os.path.basename(m.group(1)), m.group(3), m.group(4))
                  for m in map(FINDING.match, lines) if m]),
          (1, [(os.path.basename(planted), "repo-path", "tools/planted-gone/x.py")]))


def load_entries(path):
    """The entries of a baseline file: its lines that are not blank or # comments."""
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip() and not ln.startswith("#")]


def main():
    folder = tempfile.mkdtemp(prefix="drift-lint-")
    locked = []  # folders denied listing; the deny is removed before cleanup
    try:
        fx = Fixture(folder)
        miss = fx.missing

        # positive controls: one planted fake per check
        check("(a) a missing absolute path is reported",
              fx.hits(f"Read `{miss}` first.\n"), [(1, "path", miss)])
        check("(a) a missing ~ path is reported",
              fx.hits("Append to `~/.claude/gone.md`.\n"), [(1, "path", "~/.claude/gone.md")])
        check("(b) a missing repo-relative path is reported",
              fx.hits("Follow `tools\\no-such-tool\\x.py`.\n"), [(1, "repo-path", "tools\\no-such-tool\\x.py")])
        check("(c) a missing skill after load is reported",
              fx.hits("Load `no-such-skill` before the work.\n"), [(1, "skill", "no-such-skill")])
        check("(c) a missing skill in a Skill table column is reported",
              fx.hits("| Before | Skill |\n|---|---|\n| git | `real-skill` |\n| css | `no-such-skill` |\n"),
              [(4, "skill", "no-such-skill")])
        check("(c) a missing skill:<name> is reported",
              fx.hits("Scope it as skill:no-such-skill here.\n"), [(1, "skill", "no-such-skill")])
        check("(c) a missing skill after see and before skill is reported",
              fx.hits("PHP: see `no-such-skill`; the `gone-skill` skill.\n"),
              [(1, "skill", "no-such-skill"), (1, "skill", "gone-skill")])
        check("(d) a missing agent named as `x` agent or Agent type is reported",
              fx.hits("Use the `no-such-agent` agent.\nAgent type: `gone-agent` for review.\n"),
              [(1, "agent", "no-such-agent"), (2, "agent", "gone-agent")])
        check("(d) a missing subagent_type is reported",
              fx.hits('Dispatch with subagent_type: "no-such-agent".\n'), [(1, "agent", "no-such-agent")])
        check("(d) a missing agent in an Agent column is reported",
              fx.hits("| Task | Agent |\n|---|---|\n| fix | `no-such-agent` |\n"), [(3, "agent", "no-such-agent")])
        check("(e) a command missing from PATH is reported",
              fx.hits("Run `frobnicate-zz --all` now.\n"), [(1, "command", "frobnicate-zz")])
        check("(e) a command in a shell block is reported",
              fx.hits("```powershell\nfrobnicate-zz --all\n```\n"), [(2, "command", "frobnicate-zz")])

        # what exists is not reported
        check("existing references are not reported", fx.hits(
            f"`{fx.existing}`, `~/.claude/real.md`, `tools/real-tool/run.py`, `templates/brief.md`.\n"
            "Load `real-skill`, load `own-skill`, load `linked-skill`, load `fakeplug:plug-skill`, load `plug-skill`.\n"
            'subagent_type: "real-agent", subagent_type: "general-purpose", subagent_type: "fakeplug:plug-agent".\n'
            "Run `realcmd --x`, `Get-ChildItem -Force`, `cd somewhere`.\n"), [])
        check("a templates/ path relative to the skill's own folder is not reported", fx.run("x\n")[1].count(
            os.path.join("real-skill", "SKILL.md")), 0)

        check("(a) a missing $HOME or $env:USERPROFILE path is reported, an existing one is not", fx.hits(
            "`$HOME\\.claude\\gone.md`, `$env:USERPROFILE/.claude/gone2.md`, `$HOME\\.claude\\real.md`,\n"
            "`$env:USERPROFILE\\.claude\\real.md`, `& \"$home/.claude/real.md\"`.\n"),
            [(1, "path", "$HOME\\.claude\\gone.md"), (1, "path", "$env:USERPROFILE/.claude/gone2.md")])
        check("(a) a placeholder, glob or ellipsis after a missing folder is reported", fx.hits(
            "`X:\\src\\old-skills*\\skills\\`, `X:\\audits-old\\<domain>\\`,\n"
            f"`{folder}\\no-such\\…`\n"),
            [(1, "path", "X:\\src\\old-skills*"), (1, "path", "X:\\audits-old\\"),
             (2, "path", f"{folder}\\no-such\\")])
        check("(a) a glob matching nothing in an existing folder is reported",
              fx.hits(f"`{folder}\\exists\\*.txt`\n"), [(1, "path", f"{folder}\\exists\\*.txt")])
        check("(a) a ? glob after a missing folder is reported, one matching a file is not", (
            fx.hits(f"`{folder}\\no-such\\he?e.md`\n"), fx.hits(f"`{folder}\\exists\\he?e.md`\n")),
            ([(1, "path", f"{folder}\\no-such\\he?e.md")], []))

        # skip rules: each skips its class and still reports the planted fake beside it
        check("skip: a placeholder, glob or ellipsis after an existing folder", fx.hits(
            f"`{folder}\\exists\\pre-<task>\\`, `{folder}\\exists\\he*.md`, `{folder}\\exists\\…`,\n"
            f"`{folder}\\exists\\name...`, `{miss}`\n"),
            [(2, "path", miss)])
        check("an existing path with a space in prose is not reported, a missing one beside it is",
              fx.hits(f"Tools in {fx.spaced} (on PATH) and `{miss}`.\n"), [(1, "path", miss)])
        # between a pair of quotes or backticks a path with spaces is one reference (fx.spaced always holds one)
        gone_spaced = f"{fx.spaced}\\gone.md"
        for label, text in (("backticks", f"Read `{gone_spaced}` now.\n"), ("double quotes", f'Read "{gone_spaced}" now.\n'),
                            ("single quotes", f"Read '{gone_spaced}' now.\n")):
            check(f"a missing path with a space in {label} is reported whole", fx.hits(text), [(1, "path", gone_spaced)])
        for label, text in (("no quotes", f"Read {gone_spaced} now.\n"), ("mismatched quotes", f"Read `{gone_spaced}' now.\n")):
            check(f"with {label} a missing path is reported by its first word",
                  fx.hits(text), [(1, "path", gone_spaced.split(" ")[0])])
        check("an ellipsis after a quoted path with a space: the folder before it exists", fx.hits(
            f"`{fx.spaced}\\…`, `{fx.spaced}\\no-such\\…`\n"), [(1, "path", f"{fx.spaced}\\no-such\\")])
        check("an ellipsis after an unquoted path with a space is prose", fx.hits(
            f"Tools in {fx.spaced} … `{miss}`\n"), [(1, "path", miss)])
        outside = "X:" + os.sep + os.path.join("no-such-fixture", "gone.md")   # no space, whatever TEMP holds
        check("an ellipsis later in unquoted prose does not excuse a missing path", fx.hits(
            f"Read {outside} then … more.\n"), [(1, "path", outside)])
        check("a command in backticks with arguments: its program exists", fx.hits(
            f"Run `{fx.existing} --flag value`.\n"), [])
        gone_program = outside.replace("gone.md", "gone.ps1")
        check("an ellipsis after arguments does not excuse a missing program", fx.hits(
            f"`{gone_program} -Foo …`\n`{gone_program} ...`\n\"{gone_program} --x ...\"\n"),
            [(1, "path", f"{gone_program} -Foo"), (2, "path", gone_program), (3, "path", f"{gone_program} --x")])
        check("an ellipsis after arguments: the program exists", fx.hits(
            f"`{fx.existing} -Foo …`, `{fx.existing} ...`, `{fx.spaced}\\f.md -Foo …`\n"), [])
        # the text before an ellipsis is the start of a name
        check("an ellipsis cuts a name short: the folder holds an entry starting with that text, any case", fx.hits(
            f"`{fx.spaced}\\my report….md`, `{fx.spaced}\\two words...`, `{fx.spaced}\\session name …\\log.txt`,\n"
            f"\"{fx.spaced}\\Common Fi…\", '{fx.spaced}\\Windows Kits...', `{fx.spaced}\\COMMON FI…`\n"), [])
        check("a second ellipsis: the parent is elided, only the first is checked", fx.hits(
            f"`{fx.spaced}\\session name …\\log….txt`, `{fx.spaced}\\…\\my other….md`\n"), [])
        no_folder = os.path.join(fx.spaced, "no-such", "session name")
        check("an ellipsis ends a folder's name: the parent is elided, the folder before it must exist", fx.hits(
            f"`{fx.spaced}\\no such name …\\log.txt`, `{fx.spaced}/no such name …/log.txt`,\n"
            f"`{no_folder} …\\log.txt`\n"), [(2, "path", no_folder)])
        no_entry, no_name = os.path.join(fx.spaced, "no such report"), os.path.join(folder, "exists", "no-such-name")
        check("no entry starts with the text before the ellipsis", fx.hits(
            f"`{no_entry}….md`, `{no_name}...`\n"), [(1, "path", no_entry), (1, "path", no_name)])
        check("a forward slash ends a segment before the ellipsis too", fx.hits(
            f"`{folder}/no-such/session name …/log.txt`\n"), [(1, "path", f"{folder}/no-such/session name")])
        check("a path alone on its line is not quoted: reported by its first word", fx.hits(
            outside.replace("gone.md", "gone name.md") + "\n"), [(1, "path", outside.replace("gone.md", "gone"))])
        check("skip: a path ends at ; (PATH lists)",
              fx.hits(f'$env:PATH = "{fx.existing};$env:PATH"; `{miss}`\n'), [(1, "path", miss)])
        check("skip: capitalised first word (prose, error text, SQL)", fx.hits(
            "Look for `Unknown command`, `ALTER TABLE x`, `NOT FOUND: x`; run `frobnicate-zz --x`.\n"),
            [(1, "command", "frobnicate-zz")])
        check("skip: an unknown Verb-Noun cmdlet is still reported",
              fx.hits("Run `Get-NoSuchThing -Force`.\n"), [(1, "command", "Get-NoSuchThing")])
        check("skip: code statements (snake_case, keywords, assignments)", fx.hits(
            "Set `ib_logfile0 not found`, `import json`, `extension = foo`; `frobnicate-zz --x`.\n"),
            [(1, "command", "frobnicate-zz")])
        check("skip: a file name as first word", fx.hits(
            "Use `safe-edit.ps1 -Find -Replace` or `frobnicate-zz --x`.\n"), [(1, "command", "frobnicate-zz")])
        check("skip: ~ paths on an ssh line are remote", fx.hits(
            "Run `ssh host 'cat ~/no-remote.txt'`.\nLocal `~/.claude/gone.md`.\n"), [(2, "path", "~/.claude/gone.md")])
        check("skip: server commands", fx.hits(
            "Run `rrsync -ro x` or `htpasswd -B x` or `frobnicate-zz --x`.\n"), [(1, "command", "frobnicate-zz")])
        check("skip: project-local tools", fx.hits(
            "Run `wp-env run cli` or `biome check x` or `frobnicate-zz --x`.\n"), [(1, "command", "frobnicate-zz")])
        check("skip: Git's usr/bin is on the PATH beside git.exe", fx.hits(
            "Run `sedx -i x` or `frobnicate-zz --x`.\n"), [(1, "command", "frobnicate-zz")])
        check("skip: a multi-line quoted string and a heredoc in a shell block", fx.hits(
            '```bash\npython -c "\nimport json\nfrobnicate-instring --x\n"\ncat <<\'PY\'\nfrobnicate-heredoc --x\nPY\n'
            "frobnicate-zz --x\n```\n"), [(9, "command", "frobnicate-zz")])
        check("skip: a PowerShell here-string in a shell block", fx.hits(
            "```powershell\n$body = @'\nfrobnicate-herestring --x\n'@\nfrobnicate-zz --x\n```\n"),
            [(5, "command", "frobnicate-zz")])
        check("skip: a shell comment is not a command", fx.hits(
            "```bash\nrealcmd x   # one thing; a placeholder\nfrobnicate-zz --x\n```\n"), [(3, "command", "frobnicate-zz")])
        check("skip: a|b without spaces is one argument, a | b is a pipeline", fx.hits(
            "Run `realcmd one|frobnicate-qq` or `realcmd x | frobnicate-zz`.\n"), [(1, "command", "frobnicate-zz")])
        check("skip: a code span crossing a line break pairs its own backticks", fx.hits(
            "Serve with `realcmd --directory <dir>\nmore` in the background, open `http://x/` and run\n"
            "`frobnicate-zz --x`.\n"), [(3, "command", "frobnicate-zz")])
        check("skip: an untagged block is not tracked for quotes", fx.hits(
            "```\nrealcmd don't\nfrobnicate-zz --x\n```\n"), [(3, "command", "frobnicate-zz")])

        # skip classes of the real-tree pass: shell default, remote, and three command extractions
        check("skip: a ${VAR:-~/...} default is not a home path, a plain ~ path beside it is still checked", fx.hits(
            "Read `${CLAUDE_CONFIG_DIR:-~/.claude}/gone/env.md` or `~/.claude/gone2.md`.\n"),
            [(1, "path", "~/.claude/gone2.md")])
        check("skip: the same ~ path without the ${VAR:-} default is reported",
              fx.hits("Read `~/.claude/gone/env.md`.\n"), [(1, "path", "~/.claude/gone/env.md")])
        check("skip: a ~ path on a line naming the remote or the server is remote, a local one is reported", fx.hits(
            "- Remote path: ~/sites/x/www\nCopy it to `~/backups/x` on the server.\n"
            "Local `~/sites/x/www`.\n"), [(3, "path", "~/sites/x/www")])
        check("skip: a bare word server on a line does not make a ~ path remote", fx.hits(
            "Config of the MCP server is `~/.claude/x`.\n"), [(1, "path", "~/.claude/x")])
        check("skip: the shared-hosting lines that name a server path are remote", [h for h in fx.hits(
            "```\n## Deploy\n- Remote path: ~/sites/<site>/www[/wp-content/themes/<theme>]\n```\n\n"
            "- Before overwriting anything on the server, copy it to `~/backups/<what>-before-<date>`\n\n"
            "## Working on the server\n\n- Site root `~/sites/<site>/www` (`~` is the account's home).\n"
            "- `~/sites/<site>/logs/` holds the logs.\n"
            "- **WP-CLI is preinstalled**: `cd ~/sites/<site>/www && wp ...`;\n"
            "  `AddType application/x-httpd-php84 .php` in `~/sites/<site>/.htaccess` (the domain folder).\n")
            if h[1] == "path"], [])
        check("skip: the same hosting lines under a heading that does not name the server are reported", [
            h[2] for h in fx.hits(
                "## Deploy\n\n- Site root `~/sites/<site>/www`.\n- Copy it to `~/backups/<what>`.\n")
            if h[1] == "path"], ["~/sites/", "~/backups/"])
        check("skip: a ~ path under a heading naming the server is remote, the next heading ends it", fx.hits(
            "## Working on the server\n\n- Root `~/sites/x/www`.\n\n## Local setup\n\n"
            "- Root `~/sites/y/www`.\n"), [(7, "path", "~/sites/y/www")])
        check("skip: a heading-looking line inside a code block is not a heading", fx.hits(
            "```\n## Server\n```\n\nRoot `~/sites/x/www`.\n"), [(5, "path", "~/sites/x/www")])
        check("skip: spans that a stray backtick paired wrongly are prose, a real span after them is checked", fx.hits(
            'and a replace on "`n" then silently matches nothing. Write with\n`WriteAllText` and an LF-joined string, '
            "Python with `newline=''`, or the Write tool.\n\n`frobnicate-zz --x`\n"), [(4, "command", "frobnicate-zz")])
        check("skip: a span of plain words is a phrase, one with a hyphenated first word is still checked", fx.hits(
            "Fails with `unexpected EOF` or `agent returned N keys`; run `frobnicate-zz now`.\n"),
            [(1, "command", "frobnicate-zz")])
        check("skip: the same plain words as a line of a shell block are still a command", fx.hits(
            "```bash\nunexpected EOF\n```\n"), [(2, "command", "unexpected")])
        check("skip: a markdown list item in a block is not a command line, a command line beside it is", fx.hits(
            "```\n- PROD: <domain> on <server> | none\nrealcmd x | none\n```\n"), [(3, "command", "none")])

        # CLI wiring and sections
        code, out = fx.run(f"Read `{miss}`.\n")
        check("cli: a finding exits 1", code, 1)
        check("cli: finding line format", out.splitlines()[:1], [f"{fx.claude}:1  path  {miss}"])
        code, out = fx.run("Nothing to see.\n")
        lines = out.splitlines() + ["", ""]
        check("cli: no main finding exits 0, upstream findings listed after their header",
              (code, lines[0], lines[1].endswith("  command  frobnicate-up")), (0, "## upstream copies", True))
        check("cli: a linked skill folder is not scanned", "frobnicate-linked" in out, False)
        check("cli: summary line", out.splitlines()[-1:],
              ["# drift-lint: 6 files, 0 findings, 0 baselined, 1 in upstream copies, 1 rules files"])

        # inputs fail closed: a missing or unreadable input, or 0 files, is a finding and exits 1
        gone_md, gone_root = os.path.join(folder, "gone", "CLAUDE.md"), os.path.join(folder, "gone-skills")
        code, out = fx.run("Nothing to see.\n", "--claude-md", gone_md)
        check("read: a missing --claude-md is reported", (code, f"{gone_md}:0  read  missing file" in out), (1, True))
        code, out = fx.run("Nothing to see.\n", "--skills-root", gone_root)
        check("read: a missing skills root is reported", (code, f"{gone_root}:0  read  missing folder" in out), (1, True))
        lock_root = os.path.dirname(write(os.path.join(folder, "lock-root", "a", "SKILL.md")))
        lock_user = os.path.dirname(write(os.path.join(folder, "lock-user", "b", "SKILL.md")))
        for parent in (lock_root, lock_user):
            os.makedirs(os.path.join(parent, "locked"))
            locked.append(os.path.join(parent, "locked"))
            subprocess.run(["icacls", locked[-1], "/deny", f"{os.environ['USERNAME']}:(RD)"],
                           capture_output=True, check=True)
        code, out = fx.run("Nothing to see.\n", "--skills-root", os.path.dirname(lock_root),
                           "--user-skills", os.path.dirname(lock_user))
        check("read: a folder that cannot be listed is reported, in a skills root and in user skills",
              (code, [f"{p}:0  read  cannot list folder (PermissionError)" in out for p in locked]), (1, [True, True]))
        code, out = fx.run_args(["--claude-md", gone_md, "--skills-root", fx.empty, "--user-skills", fx.empty,
                                 "--agents-dir", fx.empty, "--plugins-dir", fx.empty, "--baseline", fx.no_baseline])
        check("read: 0 files scanned is reported", (code, "drift-lint:0  read  0 files scanned" in out), (1, True))
        for flag, label in (("--user-skills", "user skills"), ("--agents-dir", "agents"), ("--plugins-dir", "plugins")):
            gone_dir = os.path.join(folder, "gone-" + label.replace(" ", "-"))
            code, out = fx.run("Nothing to see.\n", flag, gone_dir)
            check(f"read: a missing {flag} folder is reported",
                  (code, f"{gone_dir}:0  read  missing folder" in out), (1, True))
        write(os.path.join(folder, "cache-only", "cache", "market", "p", "1", "agents", "x.md"))
        write(os.path.join(folder, "cache-only", "cache", "market", "p", "1", "rules", "r.md"))
        code, out = fx.run("Nothing to see.\n", "--plugins-dir", os.path.join(folder, "cache-only"))
        check("read: a plugins folder with a cache and no installed_plugins.json is not reported",
              (code, "  read  " in out), (0, False))
        bad = write(os.path.join(folder, "bad", "CLAUDE.md"))
        with open(bad, "wb") as fh:
            fh.write(b"Read `" + os.path.join(folder, "exists").encode("utf-8") + b"\\caf\xff.md` now.\n")
        code, out = fx.run_args(["--claude-md", bad, "--skills-root", fx.skills, "--user-skills", fx.user,
                                 "--agents-dir", fx.agents, "--baseline", fx.no_baseline])
        check("read: a main file with invalid UTF-8 is one read finding, no path finding from mojibake",
              (code, [ln.split("  ")[1] for ln in out.splitlines() if ln.startswith(bad)], chr(0xFFFD) in out),
              (1, ["read"], False))
        up_user = os.path.dirname(os.path.dirname(write(os.path.join(folder, "up-user", "wp-bad", "SKILL.md"))))
        with open(os.path.join(up_user, "wp-bad", "SKILL.md"), "wb") as fh:
            fh.write(b"Run `frobnicate-up --x` \xff\n")
        code, out = fx.run("Nothing to see.\n", "--user-skills", up_user)
        check("read: an upstream copy with invalid UTF-8 is decoded leniently and scanned",
              (code, "  command  frobnicate-up" in out, "  read  " in out), (0, True, False))
        bad_base = write(os.path.join(folder, "bad-baseline.txt"))
        with open(bad_base, "wb") as fh:
            fh.write(b"# reason \xff\n")
        code, out = fx.run("Nothing to see.\n", "--baseline", bad_base)
        check("read: a baseline with invalid UTF-8 is reported",
              (code, f"{bad_base}:0  read  baseline is not valid UTF-8" in out), (1, True))
        lock_md = write(os.path.join(folder, "lock-file", "CLAUDE.md"), "Nothing to see.\n")
        locked.append(lock_md)
        subprocess.run(["icacls", lock_md, "/deny", f"{os.environ['USERNAME']}:(RD)"], capture_output=True, check=True)
        code, out = fx.run("Nothing to see.\n", "--claude-md", lock_md)
        check("read: a rule file that cannot be opened is reported", (code, [
            ln.split("  ")[1:2] + ["Permission denied" in ln] for ln in out.splitlines() if ln.startswith(lock_md)]),
            (1, [["read", True]]))

        # baseline: listed findings suppressed, unmatched entries stale, the rest still fails
        gone2 = os.path.join(folder, "no-such-dir", "gone2.md")
        base = fx.baseline((".claude/CLAUDE.md", "path", miss))
        code, out = fx.run(f"Read `{miss}`.\n", "--baseline", base)
        check("baseline: a listed finding is suppressed and counted",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.claude)], out.splitlines()[-1:]),
              (0, [], ["# drift-lint: 6 files, 0 findings, 1 baselined, 1 in upstream copies, 1 rules files"]))
        code, out = fx.run(f"Read `{miss}` and `{gone2}`.\n", "--baseline", base)
        check("baseline: a finding not listed still exits 1",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.claude)]),
              (1, [f"{fx.claude}:1  path  {gone2}"]))
        base = fx.baseline((".claude/CLAUDE.md", "path", miss), (".claude/CLAUDE.md", "path", gone2))
        code, out = fx.run(f"Read `{miss}`.\n", "--baseline", base)
        check("baseline: an entry matching no finding is reported as stale",
              (code, [ln for ln in out.splitlines() if "stale-baseline" in ln]),
              (1, [f"{base}:3  stale-baseline  .claude/CLAUDE.md  path  {gone2}"]))
        other = write(os.path.join(folder, "other", "CLAUDE.md"), f"Read `{miss}`.\n")
        base = fx.baseline((".claude/CLAUDE.md", "path", miss))
        code, out = fx.run(f"Read `{miss}`.\n", "--baseline", base, "--claude-md", other)
        check("baseline: an entry suppresses the finding in its own file only",
              (code, [ln for ln in out.splitlines() if ln.startswith((fx.claude, other))]),
              (1, [f"{other}:1  path  {miss}"]))
        code, out = fx.run("Nothing to see.\n", "--baseline", os.path.join(folder, "no-baseline.txt"))
        check("baseline: a missing --baseline file is reported",
              (code, f"{os.path.join(folder, 'no-baseline.txt')}:0  read  missing baseline" in out), (1, True))
        tool = os.path.join(folder, "tool")
        os.makedirs(tool)
        shutil.copy(SCRIPT, tool)
        shutil.copy(fx.baseline((".claude/CLAUDE.md", "path", miss)), tool)
        write(fx.claude, f"Read `{miss}`.\n")
        code, out = fx.run_args(["--claude-md", fx.claude, "--skills-root", fx.skills, "--user-skills", fx.user,
                                 "--agents-dir", fx.agents], script=os.path.join(tool, "drift_lint.py"))
        check("baseline: a baseline.txt beside the script is not read without --baseline",
              (code, out.splitlines()[-1:]),
              (1, ["# drift-lint: 6 files, 1 findings, 0 baselined, 1 in upstream copies, 1 rules files"]))

        # the skill repos have no built-in default: --repo, else HARNESS_DRIFT_REPOS, else exit 2
        tool_ref = "Follow `tools/real-tool/run.py` and `tools/no-such-tool/x.py`.\n"
        want = [f"{fx.claude}:1  repo-path  tools/no-such-tool/x.py"]
        skills = ("--skills-root", fx.skills)
        code, out, err = fx.run_no_repo(tool_ref, fx.repo, *skills)
        check("repos: HARNESS_DRIFT_REPOS is used when there is no --repo",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.claude)], err), (1, want, ""))
        code, out, err = fx.run_no_repo(tool_ref, os.pathsep.join([fx.empty, fx.repo]), *skills)
        check("repos: HARNESS_DRIFT_REPOS holds several roots separated by os.pathsep",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.claude)]), (1, want))
        code, out, err = fx.run_no_repo(tool_ref, fx.empty, *skills)
        check("repos: positive control, a repo without the tool reports it too",
              (code, sorted(ln.split("  ")[1:] for ln in out.splitlines() if ln.startswith(fx.claude))),
              (1, [["repo-path", "tools/no-such-tool/x.py"], ["repo-path", "tools/real-tool/run.py"]]))
        code, out, err = fx.run_no_repo("Nothing to see.\n", folder)
        check("repos: the skills roots default to <repo>/skills of HARNESS_DRIFT_REPOS",
              (code, out.splitlines()[-1:]),
              (0, ["# drift-lint: 6 files, 0 findings, 0 baselined, 1 in upstream copies, 1 rules files"]))
        code, out, err = fx.run_no_repo(tool_ref, fx.empty, *skills, "--repo", fx.repo)
        check("repos: --repo wins over HARNESS_DRIFT_REPOS",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.claude)]), (1, want))
        for label, env_repos in (("unset", None), ("empty", ""), ("only separators", os.pathsep * 2)):
            code, out, err = fx.run_no_repo(tool_ref, env_repos, *skills)
            check(f"repos: no --repo and HARNESS_DRIFT_REPOS {label}: exit 2 with the message, no report",
                  (code, out, err.strip()), (2, "", "drift-lint: no repos; pass --repo or set HARNESS_DRIFT_REPOS"))

        # marketplace repo: plugins/*/skills and plugins/*/rules/*.md, names and paths resolved per plugin
        good = ("Agent type: `rigger-core:worker` for the work.\n\nFollow `templates/x.md`.\n\n"
                "Load `rigger-core:s` and load `other-plugin:o`.\n\nThe `other-plugin:helper` agent.\n\n"
                'subagent_type: "rigger-core:worker"\n')
        code, out = fx.run_market(good, "Follow `templates/x.md`.\n\nAgent type: `rigger-core:worker`.\n")
        check("plugin: existing namespaced names and plugin-root paths are not reported, every file is scanned",
              (code, out.splitlines()),
              (0, ["# drift-lint: 6 files, 0 findings, 0 baselined, 0 in upstream copies, 2 rules files"]))
        for label, text, want in (
                ("a missing namespaced agent", "Agent type: `rigger-core:nope`.\n", ("agent", "rigger-core:nope")),
                ("a missing namespaced agent after subagent_type", 'subagent_type: "rigger-core:nope"\n',
                 ("agent", "rigger-core:nope")),
                ("a missing namespaced agent in a table", "| Agent |\n|---|\n| `rigger-core:nope` |\n",
                 ("agent", "rigger-core:nope")),
                ("a missing namespaced skill", "Load `rigger-core:nope`.\n", ("skill", "rigger-core:nope")),
                ("a missing plugin-root path", "Follow `templates/nope.md`.\n", ("repo-path", "templates/nope.md")),
                ("an agent of another plugin", "Agent type: `other-plugin:worker`.\n",
                 ("agent", "other-plugin:worker")),
                ("an unknown plugin", "Agent type: `gone-plugin:worker`.\n", ("agent", "gone-plugin:worker")),
                ("a bare agent name, which is not the installed name", "Agent type: `worker`.\n", ("agent", "worker")),
                ("a path only in another plugin", "Follow `templates/only-other.md`.\n",
                 ("repo-path", "templates/only-other.md")),
                ("a prefixed agent", "Agent type: `xrigger-core:worker`.\n", ("agent", "xrigger-core:worker")),
                ("a prefixed agent after subagent_type", 'subagent_type: "xrigger-core:worker"\n',
                 ("agent", "xrigger-core:worker")),
                ("a hyphen-prefixed agent", "The `x-rigger-core:worker` agent.\n",
                 ("agent", "x-rigger-core:worker")),
                ("a prefixed skill", "Load `xrigger-core:s`.\n", ("skill", "xrigger-core:s")),
                ("a prefixed skill:<name>", "As skill:xrigger-core:s here.\n", ("skill", "xrigger-core:s"))):
            check(f"plugin: {label} is reported", fx.market_hits(text), [("SKILL.md", int("|" in text) * 2 + 1, *want)])
        check("rules: a plugin's rules/*.md is scanned, the plugin root resolves there too", fx.market_hits(
            "Nothing here.\n", "Follow `templates/x.md`, `templates/gone.md`.\n\nAgent type: `rigger-core:gone`.\n"),
            [("r.md", 1, "repo-path", "templates/gone.md"), ("r.md", 3, "agent", "rigger-core:gone")])
        code, out = fx.run_market("Nothing here.\n")
        check("rules: only *.md directly in rules/ is scanned",
              ("in-a-txt" in out, "in-a-subfolder" in out, code), (False, False, 0))
        broken_rule = "Follow `templates/gone.md`.\n"
        check("rules: scanned with an explicit --skills-root too, which still resolves the holding plugin",
              fx.market_hits("Follow `templates/x.md`.\n", broken_rule, "--skills-root",
                             os.path.dirname(os.path.dirname(fx.market_skill))),
              [("r.md", 1, "repo-path", "templates/gone.md")])
        check("plugin: --repo . resolves from the working folder",
              fx.market_hits("Follow `templates/x.md`, `templates/nope.md`.\n", repo=".", cwd=fx.market),
              [("SKILL.md", 1, "repo-path", "templates/nope.md")])
        check("plugin: the holding plugin is found whatever the case of --repo", fx.market_hits(
            "Follow `templates/x.md`.\n", "Nothing here.\n", "--skills-root",
            os.path.dirname(os.path.dirname(fx.market_skill)), repo=fx.market.upper()), [])
        base = fx.baseline(("rigger-core/rules/r.md", "repo-path", "templates/gone.md"))
        code, out = fx.run_market("Nothing here.\n", broken_rule, "--baseline", base)
        check("baseline: a rules file's key is <plugin>/rules/<file>",
              (code, out.splitlines()),
              (0, ["# drift-lint: 6 files, 0 findings, 1 baselined, 0 in upstream copies, 2 rules files"]))
        both = os.path.join(folder, "both")
        write(os.path.join(both, "plugins", "p", "skills", "a", "SKILL.md"), "Follow `tools/gone-a.py`.\n")
        write(os.path.join(both, "skills", "b", "SKILL.md"), "Follow `tools/gone-b.py`.\n")
        check("plugin: a repo with skills/ and plugins/*/skills has both scanned",
              fx.market_hits("Nothing here.\n", repo=both),
              [("SKILL.md", 1, "repo-path", "tools/gone-a.py"), ("SKILL.md", 1, "repo-path", "tools/gone-b.py")])
        vendored = os.path.join(folder, "vendored")
        write(os.path.join(vendored, "plugins", "p", "skills", "top", "SKILL.md"), "Follow `tools/gone-top.py`.\n")
        write(os.path.join(vendored, "plugins", "p", "skills", "top", "references", "skills", "inner", "SKILL.md"),
              "Follow `tools/gone-inner.py`.\n")
        write(os.path.join(vendored, "plugins", "p", "skills", "top", "references", "doc.md"), "Plain document.\n")
        write(os.path.join(vendored, "plugins", "p", "skills", "plain", "SKILL.md"), "Nothing here.\n")
        write(os.path.join(vendored, "plugins", "p", "skills", "plain", "sub", "SKILL.md"), "Follow `tools/gone-sub.py`.\n")
        write(os.path.join(vendored, "plugins", "p", "skills", "grouped", "references", "deep", "SKILL.md"),
              "Follow `tools/gone-deep.py`.\n")
        check("skills: a SKILL.md under a skill's references/ is not a skill; one in another subfolder, or under a "
              "references/ folder that holds no SKILL.md, still is",
              fx.market_hits("Nothing here.\n", repo=vendored),
              [("SKILL.md", 1, "repo-path", "tools/gone-deep.py"), ("SKILL.md", 1, "repo-path", "tools/gone-sub.py"),
               ("SKILL.md", 1, "repo-path", "tools/gone-top.py")])
        prefixed = os.path.join(folder, "prefixed")
        write(os.path.join(prefixed, "plugins", "p", "templates", "x.md"))
        write(os.path.join(prefixed, "plugins", "p2", "skills", "a", "SKILL.md"), "Follow `templates/x.md`.\n")
        check("plugin: a plugin whose name starts with another's is not held by it",
              fx.market_hits("Nothing here.\n", repo=prefixed), [("SKILL.md", 1, "repo-path", "templates/x.md")])
        no_skills = os.path.join(folder, "no-skills")
        write(os.path.join(no_skills, "plugins", "p", "agents", "a.md"))
        for label, repo in (("a repo without skills/ or plugins/", fx.empty),
                            ("a plugin repo with no skills folder", no_skills)):
            code, out = fx.run_market("Nothing here.\n", repo=repo)
            check(f"read: {label} reports the missing root",
                  (code, f"{os.path.join(repo, 'skills')}:0  read  missing folder" in out), (1, True))
        locked_rules = os.path.dirname(write(os.path.join(folder, "lock-rules", "plugins", "p", "rules", "r.md")))
        write(os.path.join(folder, "lock-rules", "plugins", "p", "skills", "a", "SKILL.md"))
        locked.append(locked_rules)
        subprocess.run(["icacls", locked_rules, "/deny", f"{os.environ['USERNAME']}:(RD)"],
                       capture_output=True, check=True)
        code, out = fx.run_market("Nothing here.\n", repo=os.path.join(folder, "lock-rules"))
        check("read: a rules folder that cannot be listed is reported",
              (code, f"{locked_rules}:0  read  cannot list folder (PermissionError)" in out), (1, True))

        # installed plugins' rules/*.md through --plugins-dir: installed_plugins.json, the cache and the flat layout
        def installed(kind, root):
            """A plugins folder with one installed plugin holding a rules file with a good and a bad reference."""
            plugin = {"json": os.path.join(root + "-store", "p1"), "flat": os.path.join(root, "p1"),
                      "cache": os.path.join(root, "cache", "m", "p", "1")}[kind]
            rule = write(os.path.join(plugin, "rules", "a.md"), "Follow `templates/there.md` and `templates/gone.md`.\n")
            write(os.path.join(plugin, "rules", "nested", "deep.md"), "Follow `templates/in-a-subfolder.md`.\n")
            write(os.path.join(plugin, "templates", "there.md"))
            if kind == "json":
                write(os.path.join(root, "installed_plugins.json"),
                      json.dumps({"version": 2, "plugins": {"p1@m": [{"installPath": plugin}]}}))
            return rule

        def rules_run(plugins_dir):
            write(fx.claude, "Nothing to see.\n")
            return fx.run_args(["--claude-md", fx.claude, "--skills-root", fx.skills, "--user-skills", fx.empty,
                                "--agents-dir", fx.agents, "--baseline", fx.no_baseline, "--plugins-dir", plugins_dir])

        for kind in ("json", "cache", "flat"):
            root = os.path.join(folder, "installed-" + kind)
            rule = installed(kind, root)
            code, out = rules_run(root)
            check(f"rules: an installed plugin's rules/*.md is scanned ({kind} layout), resolved in its own plugin",
                  (code, [ln for ln in out.splitlines() if ln.startswith(rule)], out.splitlines()[-1:]),
                  (1, [f"{rule}:1  repo-path  templates/gone.md"],
                   ["# drift-lint: 4 files, 1 findings, 0 baselined, 0 in upstream copies, 1 rules files"]))
        empty_plugins = os.path.join(folder, "no-rules-plugins")
        write(os.path.join(empty_plugins, "installed_plugins.json"), json.dumps(
            {"version": 2, "plugins": {"p2@m": [{"installPath": os.path.dirname(os.path.dirname(
                write(os.path.join(folder, "bare-plugin", "agents", "x.md"))))}]}}))
        code, out = rules_run(empty_plugins)
        check("rules: --plugins-dir given and 0 rules files read exits 1",
              (code, f"{empty_plugins}:0  read  0 rules files read" in out), (1, True))
        code, out = fx.run_market("Nothing here.\n", "Follow `templates/x.md`.\n", "--plugins-dir", fx.empty)
        check("rules: --plugins-dir at an empty folder exits 1 although --repo has rules files",
              (code, f"{fx.empty}:0  read  0 rules files read" in out, out.splitlines()[-1:]),
              (1, True, ["# drift-lint: 5 files, 1 findings, 0 baselined, 0 in upstream copies, 1 rules files"]))
        code, out = fx.run_market("Nothing here.\n", broken_rule, "--plugins-dir", os.path.join(fx.market, "plugins"))
        check("rules: a file reached through --repo and through --plugins-dir is scanned and counted once",
              (code, [ln for ln in out.splitlines() if ln.startswith(fx.market_rules)], out.splitlines()[-1:]),
              (1, [f"{fx.market_rules}:1  repo-path  templates/gone.md"],
               ["# drift-lint: 5 files, 1 findings, 0 baselined, 0 in upstream copies, 1 rules files"]))
        home2 = os.path.join(folder, "home2")
        os.makedirs(os.path.join(home2, ".claude", "plugins"))
        write(fx.claude, "Nothing to see.\n")
        proc = subprocess.run([sys.executable, "-I", SCRIPT, "--home", home2, "--repo", fx.repo, "--claude-md", fx.claude,
                               "--skills-root", fx.skills, "--user-skills", fx.empty, "--agents-dir", fx.agents,
                               "--path", os.pathsep.join([fx.bin, fx.git_cmd]), "--baseline", fx.no_baseline],
                              capture_output=True, timeout=60)
        check("rules: without --plugins-dir, 0 rules files is not an error",
              (proc.returncode, "rules files read" in proc.stdout.decode("utf-8")), (0, False))

        # the config folder: CLAUDE_CONFIG_DIR wins over ~/.claude, and a plugin-only one is no error
        def run_config(config, *extra, skills=fx.skills):
            env = dict(os.environ)
            env.pop("CLAUDE_CONFIG_DIR", None)
            env.update({"CLAUDE_CONFIG_DIR": config} if config else {})
            proc = subprocess.run([sys.executable, "-I", SCRIPT, "--home", os.path.join(folder, "home3"), "--repo", fx.repo,
                                   "--skills-root", skills, "--path", os.pathsep.join([fx.bin, fx.git_cmd]), *extra],
                                  capture_output=True, timeout=60, env=env)
            return proc.returncode, proc.stdout.decode("utf-8")

        config = os.path.join(folder, "config")
        cfg_md = write(os.path.join(config, "CLAUDE.md"), "Follow `tools/in-the-config.md`.\n")
        write(os.path.join(config, "skills", "cfg-skill", "SKILL.md"), "Follow `tools/in-a-config-skill.md`.\n")
        write(os.path.join(config, "agents", "cfg-agent.md"), "Follow `tools/in-a-config-agent.md`.\n")
        code, out = run_config(config)
        check("CLAUDE_CONFIG_DIR: its CLAUDE.md, skills and agents are scanned, not ~/.claude",
              (code, f"{cfg_md}:1  repo-path  tools/in-the-config.md" in out, "tools/in-a-config-skill.md" in out,
               "tools/in-a-config-agent.md" in out), (1, True, True, True))
        write(os.path.join(folder, "home3", ".claude", "CLAUDE.md"), "Follow `tools/in-home.md`.\n")
        code, out = run_config(config)
        check("CLAUDE_CONFIG_DIR: ~/.claude is left out when it is set", "tools/in-home.md" in out, False)
        code, out = run_config(None)
        check("no CLAUDE_CONFIG_DIR: ~/.claude is read", "tools/in-home.md" in out, True)
        plugin_only = os.path.join(folder, "plugin-only")
        os.makedirs(os.path.join(plugin_only, "plugins"))
        code, out = run_config(plugin_only)
        check("a plugin-only config: no read finding, exit 0, files were scanned",
              (code, "  read  " in out, bool(re.search(r"# drift-lint: [1-9]\d* files", out))), (0, False, True))
        code, out = run_config(plugin_only, "--user-skills", os.path.join(folder, "named-gone"))
        check("a plugin-only config: a folder named on the command line must still exist",
              (code, "named-gone:0  read  missing folder" in out), (1, True))
        code, out = run_config(plugin_only, skills=fx.empty)
        check("a plugin-only config with nothing else: 0 files is still a read finding",
              (code, "drift-lint:0  read  0 files scanned" in out), (1, True))

        # a boundary before each regex: the keyword inside a longer word is not a reference
        for label, text in (("xskill:", "As xskill:nope here.\n"), ("my-skill:", "As my-skill:nope here.\n"),
                            ("xload", "Do xload `nope` now.\n"), ("pre-load", "Do pre-load `nope` now.\n"),
                            ("xsee", "Do xsee `nope` now.\n"), ("pre-see", "Do pre-see `nope` now.\n"),
                            ("xskill", "Do xskill `nope` now.\n"), ("pre-skill", "Do pre-skill `nope` now.\n"),
                            ("xsubagent_type", "Do xsubagent_type: nope now.\n"),
                            ("my-subagent_type", "Do my-subagent_type: nope now.\n"),
                            ("xagent type", "Do xagent type `nope` now.\n"),
                            ("pre-agent type", "Do pre-agent type `nope` now.\n"),
                            ("xtemplates/", "Do `xtemplates/nope.md` now.\n"),
                            ("pre-templates/", "Do `pre-templates/nope.md` now.\n")):
            check(f"boundary: {label} is not a reference", fx.hits(text), [])
        check("boundary: positive control, the same words on their own are references",
              fx.hits("As skill:nope here.\n\nDo load `nope1` now.\n\nDo see `nope2` now.\n\nDo skill `nope3` now.\n\n"
                      "Do subagent_type: nope4 now.\n\nDo agent type `nope5` now.\n\nDo `templates/nope.md` now.\n"),
              [(1, "skill", "nope"), (3, "skill", "nope1"), (5, "skill", "nope2"), (7, "skill", "nope3"),
               (9, "agent", "nope4"), (11, "agent", "nope5"), (13, "repo-path", "templates/nope.md")])

        real_tree_gate(folder)
    finally:
        for path in locked:
            subprocess.run(["icacls", path, "/remove:d", os.environ["USERNAME"]], capture_output=True)
        link = os.path.join(folder, "user-skills", "linked-skill")
        if os.path.islink(link):
            os.unlink(link)  # a POSIX symlink
        elif os.path.isdir(link):
            os.rmdir(link)  # removes the junction, not its target
        shutil.rmtree(folder)
    print(f"# drift-lint: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


sys.exit(main())
