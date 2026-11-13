"""Check plugin eval suites against the case inventory in evals/CASES.md.

Usage: python -I scripts/eval_suite_check.py --cases evals/CASES.md <plugin_root>...

Per plugin: every case folder under <plugin_root>/evals (the results/ folder is ignored) has a
prompt.md with graders/*.md or a case.yaml with a graders: list, only frontmatter keys the eval tool
accepts, runs above 0, a native or wsl tag, and no shell grant unless tagged wsl; the folders and the
rows of the plugin's table in CASES.md match one to one, at most MAX_CASES of them.
Prints one line per problem and `# cases <n>, problems <m>`; exit 1 on any problem or on 0 cases.
"""

import argparse
import re
import sys
from pathlib import Path

MAX_CASES = 5
FRONTMATTER_KEYS = {
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
    "max_turns", "timeout_seconds", "allowed_tools", "disallowed_tools", "artifact_publish",
    "growthbook_overrides", "append_system_prompt", "env",
}
YAML_ONLY_KEYS = {"prompt", "graders"}
KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
ITEM_LINE = re.compile(r"^\s*-(\s|$)")
SHELL = re.compile(r"^(Bash|PowerShell)\b")


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text


def parse_keys(text):
    """Top-level `key: value` pairs; a flow list or a block list becomes a list of strings."""
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = KEY_LINE.match(lines[i])
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            out[key] = [_scalar(p) for p in value[1:-1].split(",") if p.strip()]
        elif value:
            out[key] = _scalar(value)
        else:
            items = []
            while i < len(lines) and (ITEM_LINE.match(lines[i]) or not lines[i].strip()):
                if ITEM_LINE.match(lines[i]):
                    items.append(_scalar(re.sub(r"^\s*-\s*", "", lines[i])))
                i += 1
            out[key] = items
    return out


def frontmatter(text):
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    return parse_keys(text[4:] if end < 0 else text[4:end])


def case_dirs(plugin_root):
    evals = Path(plugin_root) / "evals"
    if not evals.is_dir():
        return []
    return sorted(d for d in evals.iterdir() if d.is_dir() and d.name != "results")


def table_cases(cases_md, plugin):
    """Case names in the first column of the table under `## <plugin>`; None without the section."""
    names, inside = None, False
    for line in cases_md.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            inside = line[3:].strip() == plugin
            if inside:
                names = [] if names is None else names
            continue
        if inside and line.lstrip().startswith("|"):
            cell = line.strip().strip("|").split("|")[0].strip().strip("`")
            if cell and cell.lower() != "case" and not set(cell) <= set("-: "):
                names.append(cell)
    return names


def _check_case(d):
    problems = []
    prompt, yaml = d / "prompt.md", d / "case.yaml"
    if prompt.is_file():
        keys = frontmatter(prompt.read_text(encoding="utf-8"))
        if not sorted((d / "graders").glob("*.md")):
            problems.append(f"{d.name}: prompt.md case has no graders/*.md")
        allowed = FRONTMATTER_KEYS
    elif yaml.is_file():
        keys = parse_keys(yaml.read_text(encoding="utf-8"))
        if not keys.get("graders"):
            problems.append(f"{d.name}: case.yaml has no graders: list")
        allowed = FRONTMATTER_KEYS | YAML_ONLY_KEYS
    else:
        return [f"{d.name}: neither prompt.md nor case.yaml"]
    for key in keys:
        if key not in allowed:
            problems.append(f"{d.name}: unknown frontmatter key '{key}'")
    if "runs" in keys and not (str(keys["runs"]).isdigit() and int(keys["runs"]) > 0):
        problems.append(f"{d.name}: runs must be an integer above 0, got {keys['runs']!r}")
    tags = keys.get("tags", [])
    tags = [tags] if isinstance(tags, str) else tags
    if not {"native", "wsl"} & set(tags):
        problems.append(f"{d.name}: tagged neither native nor wsl")
    tools = keys.get("allowed_tools", [])
    tools = [tools] if isinstance(tools, str) else tools
    if any(SHELL.match(t) for t in tools) and "wsl" not in tags:
        problems.append(f"{d.name}: grants a shell but is not tagged wsl")
    return problems


def check_suite(plugin_root, cases_md):
    plugin = Path(plugin_root).name
    dirs = case_dirs(plugin_root)
    if not (Path(plugin_root) / "evals").is_dir():
        return [f"{plugin}: no evals folder"]
    problems = []
    for d in dirs:
        problems += [f"{plugin}: {p}" for p in _check_case(d)]
    if len(dirs) > MAX_CASES:
        problems.append(f"{plugin}: {len(dirs)} cases, at most {MAX_CASES} allowed")
    rows = table_cases(cases_md, plugin)
    if rows is None:
        problems.append(f"{plugin}: no '## {plugin}' section in the case inventory")
        return problems
    have = {d.name for d in dirs}
    problems += [f"{plugin}: case '{n}' is not in the table" for n in sorted(have - set(rows))]
    problems += [f"{plugin}: table row '{n}' has no case directory" for n in rows if n not in have]
    return problems


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("plugin_roots", nargs="+")
    args = ap.parse_args(argv)
    try:
        cases_md = Path(args.cases).read_text(encoding="utf-8")
    except OSError as err:
        print(f"cannot read {args.cases}: {err.strerror or err}")
        print("# cases 0, problems 1")
        return 1
    problems, total = [], 0
    for root in args.plugin_roots:
        total += len(case_dirs(root))
        problems += check_suite(root, cases_md)
    for p in problems:
        print(p)
    print(f"# cases {total}, problems {len(problems)}")
    return 1 if problems or total == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
