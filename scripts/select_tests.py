"""Pick the test files a change can affect, for `run_tests.py --fix-round`.

The full suite stays mandatory before merge. The change is everything between the merge base with REV (default: main) and the working tree,
untracked files included. A test file is affected when a changed file is reachable from it through
Python imports or a file name in its text, followed transitively. tests/test_repo.py always runs,
because it reads every tracked file. A changed file no test reaches means the full suite, unless it
is plain documentation: Markdown outside the folders the plugins ship, which needs tests/test_repo.py
alone. Markdown in a plugin's agents, commands, rules, skills or templates folder also picks every test
file inside that plugin. Paths built at run time and folder globs are invisible here, which is why a
selection never replaces the full suite.
"""

import ast
import re
import subprocess
from collections import defaultdict
from pathlib import PurePosixPath

ALWAYS = "tests/test_repo.py"
DEFAULT_BASE = "main"
SEARCHED_SUFFIXES = (".py", ".ps1", ".sh")
# A function-hook plugin's TypeScript is run by `claude plugin test` from its own Python test, which
# names only some of the files: any such file picks the tests inside its plugin.
FUNCTION_HOOK_SUFFIXES = (".ts", ".tsx")
# A plugin's tests read its shipped Markdown through paths built at run time: such a file also picks
# the tests inside its plugin. One that no test reaches still means the full suite.
SHIPPED_MARKDOWN_FOLDERS = {"agents", "commands", "rules", "skills", "templates"}
MIN_NAME_LENGTH = 4
GIT_TIMEOUT = 30
SHIPPED_FOLDERS = {"agents", "commands", "evals", "hooks", "rules", "skills", "templates"}
IMPORT_LINE_RE = re.compile(r"^\s*(?:from|import)\s+(\w+)", re.M)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=GIT_TIMEOUT,
    ).stdout


def null_split(text):
    return [name for name in text.split("\0") if name]


def changed_files(root, base=DEFAULT_BASE):
    """Files that differ from the merge base with `base`, plus untracked ones."""
    merge_base = git(root, "merge-base", base, "HEAD").strip()
    changed = null_split(git(root, "diff", "--name-only", "--no-renames", "-z", merge_base))
    return sorted(set(changed) | set(null_split(git(root, "ls-files", "-o", "--exclude-standard", "-z"))))


def imported_modules(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set(IMPORT_LINE_RE.findall(text))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(part for alias in node.names for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            modules.update((node.module or "").split("."))
            modules.update(alias.name for alias in node.names)
    return modules


def plugin_of(path):
    parts = PurePosixPath(path).parts
    return parts[1] if len(parts) > 2 and parts[0] == "plugins" else None


def names_file(path, text, target):
    """An ambiguous file name counts only with the target's plugin named, or from inside that plugin."""
    plugin = plugin_of(target)
    return plugin is not None and (plugin in text or plugin_of(path) == plugin)


def build_edges(root, tracked):
    """path -> the tracked files it reaches directly."""
    by_stem = defaultdict(list)
    by_name = defaultdict(list)
    for path in tracked:
        pure = PurePosixPath(path)
        by_name[pure.name].append(path)
        if pure.suffix == ".py":
            by_stem[pure.stem].append(path)
    edges = defaultdict(set)
    for path in tracked:
        if not path.endswith(SEARCHED_SUFFIXES):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.endswith(".py"):
            for module in imported_modules(text):
                edges[path].update(by_stem.get(module, ()))
        for name, targets in by_name.items():
            if len(name) < MIN_NAME_LENGTH or name not in text:
                continue
            for target in targets:
                if len(targets) == 1 or names_file(path, text, target):
                    edges[path].add(target)
        edges[path].discard(path)
    return edges


def reachable(edges, start):
    seen = {start}
    stack = [start]
    while stack:
        for target in edges.get(stack.pop(), ()):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def is_plain_docs(path):
    pure = PurePosixPath(path)
    return pure.suffix == ".md" and not SHIPPED_FOLDERS.intersection(pure.parts)


def is_shipped_markdown(path):
    pure = PurePosixPath(path)
    return pure.suffix == ".md" and plugin_of(path) is not None and pure.parts[2] in SHIPPED_MARKDOWN_FOLDERS


def select(changed, tests, edges):
    """(test files to run, unmapped changed files); None instead of a list means the full suite."""
    reach = {test: reachable(edges, test) for test in tests if test != ALWAYS}
    selected = {ALWAYS} if ALWAYS in tests else set()
    unmapped = []
    for path in changed:
        hits = {test for test, seen in reach.items() if path in seen}
        if path.endswith(FUNCTION_HOOK_SUFFIXES) and plugin_of(path) is not None:
            hits |= {test for test in reach if plugin_of(test) == plugin_of(path)}
        selected |= hits
        if not hits and path != ALWAYS and not is_plain_docs(path):
            unmapped.append(path)
        if is_shipped_markdown(path):
            selected |= {test for test in reach if plugin_of(test) == plugin_of(path)}
    if unmapped:
        return None, unmapped
    return sorted(selected), []


def selection(root, tests, base=DEFAULT_BASE):
    tracked = null_split(git(root, "ls-files", "-z"))
    changed = changed_files(root, base)
    edges = build_edges(root, sorted(set(tracked) | {path for path in changed if (root / path).is_file()}))
    return (changed, *select(changed, tests, edges))
