"""Checks for drift_lint.py's `trigger-load` check: a trigger row whose `load` names a skill no module has.

python -I test_trigger_load.py

Runs on every platform: each case plants a marketplace layout (plugins/<name>/rigger-module.json and skills/)
under a temp folder and runs the script end to end. Expected values are written out from the fixture text.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

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


# plugin "alpha" has the skill "present"; plugin "beta" has the skill "other". Line numbers below count from 1.
ALPHA = """{
  "schema": 1,
  "name": "alpha",
  "triggers": [
    {
      "id": "alpha.fine",
      "before": "a thing that names no skill id at all",
      "load": ["alpha:present", "beta:other"]
    },
    {
      "id": "alpha.broken",
      "before": "alpha:present",
      "load": [
        "alpha:present",
        "beta:ghost"
      ]
    }
  ]
}
"""
BETA = """{
  "schema": 1,
  "name": "beta",
  "triggers": [
    {
      "id": "beta.wrong-plugin",
      "before": "x",
      "load": ["beta:other"]
    }
  ]
}
"""


def plant(root, alpha=ALPHA, beta=BETA):
    for plugin, skill, manifest in (("alpha", "present", alpha), ("beta", "other", beta)):
        write(os.path.join(root, "plugins", plugin, "skills", skill, "SKILL.md"), f"---\nname: {skill}\n---\nNothing here.\n")
        if manifest is not None:
            write(os.path.join(root, "plugins", plugin, "rigger-module.json"), manifest)


def run(root, repo=None, folder=None):
    """(exit code, output lines) of the script on the planted repo, with an empty config folder."""
    config = os.path.join(folder or root, "config")
    os.makedirs(config, exist_ok=True)
    # none of the session's Claude, harness or Rigger variables: RIGGER_MODULE_DIRS modules add drift_lint config
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
    env.update(CLAUDE_CONFIG_DIR=config, HOME=config, USERPROFILE=config)
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT, "--repo", repo or root, "--home", config],
                          capture_output=True, timeout=120, env=env, cwd=repo or root)
    return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n").splitlines()


def loads(lines):
    """The trigger-load findings of an output: ([plugin folder, file], line, reference)."""
    found = []
    for m in map(FINDING.match, lines):
        if m and m.group(3) == "trigger-load":
            found.append((m.group(1).replace("\\", "/").rsplit("/", 2)[-2:], int(m.group(2)), m.group(4)))
    return found


def repo_root():
    here = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(here, ".claude-plugin", "marketplace.json")):
            return here
        here = os.path.dirname(here)
    return None


def main():
    folder = tempfile.mkdtemp(prefix="trigger-load-")
    try:
        planted = os.path.join(folder, "planted")
        plant(planted)
        code, lines = run(planted)
        check("a load naming a skill no module has is one finding, naming the trigger and the skill, at the load's line",
              loads(lines), [(["alpha", "rigger-module.json"], 15, "alpha.broken loads beta:ghost")])
        check("the summary counts the trigger manifests read", lines[-1].endswith(", 2 trigger manifests"), True)
        one = os.path.join(folder, "one-manifest")
        plant(one, beta=None)
        check("a plugin without a manifest is not counted", run(one)[1][-1].endswith(", 1 trigger manifests"), True)

        control = os.path.join(folder, "control")
        plant(control, alpha=ALPHA.replace('"beta:ghost"', '"beta:other"'))
        code, lines = run(control)
        check("control: every load names a skill a module has, no trigger-load finding", (code, loads(lines)), (0, []))

        wrong = os.path.join(folder, "wrong-plugin")
        plant(wrong, beta=BETA.replace('"beta:other"', '"beta:present"'))
        code, lines = run(wrong)
        check("a skill that exists under another plugin's name does not satisfy a load",
              loads(lines), [(["alpha", "rigger-module.json"], 15, "alpha.broken loads beta:ghost"),
                             (["beta", "rigger-module.json"], 8, "beta.wrong-plugin loads beta:present")])

        bare = os.path.join(folder, "bare")
        plant(bare, alpha=ALPHA.replace('["alpha:present", "beta:other"]', '["present", "beta:other"]'))
        code, lines = run(bare)
        check("a bare name that is a repo plugin's skill does not satisfy a load: only plugin:skill counts",
              loads(lines), [(["alpha", "rigger-module.json"], 8, "alpha.fine loads present"),
                             (["alpha", "rigger-module.json"], 15, "alpha.broken loads beta:ghost")])

        # plugin "gamma" is installed only (installed_plugins.json of the config), with the skill "kept"
        installed = os.path.join(folder, "installed")
        gamma = os.path.join(installed, "gamma-install")
        write(os.path.join(gamma, "skills", "kept", "SKILL.md"), "---\nname: kept\n---\nNothing here.\n")
        write(os.path.join(installed, "config", "plugins", "installed_plugins.json"),
              json.dumps({"version": 2, "plugins": {"gamma@market": [{"installPath": gamma}]}}))
        repo = os.path.join(installed, "repo")
        plant(repo, alpha=ALPHA.replace('["alpha:present", "beta:other"]', '["gamma:kept", "kept"]')
              .replace('"beta:ghost"', '"beta:other"'))
        code, lines = run(repo, folder=installed)
        check("an installed plugin's skill satisfies a load as plugin:skill, never by its bare name",
              loads(lines), [(["alpha", "rigger-module.json"], 8, "alpha.fine loads kept")])

        for label, alpha in (("not JSON", "{ not json"), ("triggers is an object", '{"triggers": {}}'),
                             ("a load is a string", '{"triggers": [{"id": "a.b", "load": "alpha:present"}]}'),
                             ("a row is a list", '{"triggers": [["alpha:present"]]}'),
                             ("a load entry is an object", '{"triggers": [{"id": "a.b", "load": [{}]}]}'),
                             ("a load entry is a number", '{"triggers": [{"id": "a.b", "load": [1]}]}'),
                             ("an id is a number", '{"triggers": [{"id": 1, "load": ["beta:ghost"]}]}')):
            bad = os.path.join(folder, "bad-" + label.replace(" ", "-"))
            plant(bad, alpha=alpha, beta=BETA)
            code, lines = run(bad)
            reads = [m.group(4) for m in map(FINDING.match, lines) if m and m.group(3) == "read"]
            check(f"a manifest that is {label} is a read finding, never a pass", (code, len(reads)), (1, 1))

        root = repo_root()
        if root is None:
            print("# trigger-load: the real-tree check needs the harness repo around the tool; not run")
        else:
            code, lines = run(root, repo=root, folder=os.path.join(folder, "real"))
            check("real tree: no trigger-load finding on today's triggers", loads(lines), [])
            copy = os.path.join(folder, "real-copy")
            shutil.copytree(os.path.join(root, "plugins"), os.path.join(copy, "plugins"),
                            ignore=shutil.ignore_patterns("__pycache__", ".git"))
            manifest = os.path.join(copy, "plugins", "rigger-core", "rigger-module.json")
            with open(manifest, encoding="utf-8", newline="") as fh:
                text = fh.read()
            old = '"rigger-core:git-workflow"'
            check("real tree: the planted copy holds the load it rewrites once", text.count(old), 1)
            write(manifest, text.replace(old, '"rigger-core:no-such-skill"'))
            code, lines = run(copy, repo=copy, folder=os.path.join(folder, "real-copy-home"))
            check("real tree: positive control, one load rewritten in a copy is the only trigger-load finding",
                  [ref for _, _, ref in loads(lines)], ["rigger-core.git-workflow loads rigger-core:no-such-skill"])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    print(f"# trigger-load: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


sys.exit(main())
