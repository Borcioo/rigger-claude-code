"""Stage a plugin and its dependencies for `claude plugin eval`.

Usage: python -I scripts/eval_stage.py <plugin_root> <out_dir>

`plugins:` entries of an eval case must lie inside the target plugin, so a dependency cannot be
loaded from its own folder. This copies <plugin_root> to <out_dir>/<plugin> and each name in its
manifest's `dependencies` from the sibling folder <plugin_root>/../<dep> to
<out_dir>/<plugin>/deps/<dep> (without its evals/, which would run as cases), where the cases' `plugins: ['../..', '../../deps/<dep>']` find it.
Run the eval on the staged copy; the copy is made per run, so it never drifts from the source.
Refuses (exit 1, one reason line) a missing manifest or dependency, a dependency with dependencies
of its own, a source deps/ folder and an existing <out_dir>.
Prints the staged path and `# staged <plugin>, dependencies <n>`.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

IGNORE = shutil.ignore_patterns("__pycache__")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # one folder in plugins/, never a path


def _without_cases(src):
    """A dependency's own evals/ would run as cases of the target's suite."""
    def ignore(folder, names):
        skip = set(IGNORE(folder, names))
        if Path(folder) == src and "evals" in names:
            skip.add("evals")
        return skip
    return ignore


class Refused(Exception):
    pass


def dependencies(root):
    manifest = root / ".claude-plugin" / "plugin.json"
    try:
        deps = json.loads(manifest.read_text(encoding="utf-8")).get("dependencies", [])
    except (OSError, ValueError, AttributeError) as err:
        raise Refused(f"cannot read {manifest}: {err}")
    if not isinstance(deps, list) or not all(isinstance(d, str) and d for d in deps):
        raise Refused(f"{manifest}: dependencies is not a list of names")
    for dep in deps:
        if not NAME.match(dep):
            raise Refused(f"{manifest}: dependency '{dep}' is not a plugin folder name")
    return deps


def stage(root, out):
    root = Path(root).resolve()
    deps = dependencies(root)
    if (root / "deps").exists():
        raise Refused(f"{root} has a deps/ folder; dependencies are staged, never checked in")
    sources = []
    for dep in deps:
        src = root.parent / dep
        if not (src / ".claude-plugin" / "plugin.json").is_file():
            raise Refused(f"dependency '{dep}' not found at {src}")
        if dependencies(src):
            raise Refused(f"dependency '{dep}' has dependencies of its own; staging them is not supported")
        name = json.loads((src / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("name")
        if name != dep:
            raise Refused(f"dependency '{dep}': its manifest names '{name}'")
        sources.append((dep, src))
    out = Path(out)
    if out.exists():
        raise Refused(f"{out} already exists; pass a new folder")
    staged = out / root.name
    shutil.copytree(root, staged, ignore=IGNORE)
    for dep, src in sources:
        shutil.copytree(src, staged / "deps" / dep, ignore=_without_cases(src))
    return staged, len(sources)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plugin_root")
    ap.add_argument("out_dir")
    args = ap.parse_args(argv)
    try:
        staged, count = stage(args.plugin_root, args.out_dir)
    except Refused as err:
        print(f"refused: {err}")
        return 1
    print(staged)
    print(f"# staged {staged.name}, dependencies {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
