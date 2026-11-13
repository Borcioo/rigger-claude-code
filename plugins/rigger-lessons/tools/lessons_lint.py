"""Check a lessons case archive against its schema (templates/archive/README.md).

python lessons_lint.py --archive DIR [--classes FILE] [--repo REPO ...] [--rule-checker FILE ...]
    FILE defaults to DIR/classes.txt. Test guards resolve against every REPO in turn (default: DIR's
    parent). Rule checkers are each --rule-checker FILE plus every REPO's
    plugins/*/tools/*/lessons_guard.py; a live:<rule id> guard whose rule id a checker claims (its
    RULES), or in a case whose tools name a shell, passes only when one of them confirms it.
    Prints one `path: problem` line per problem.
    Exit 0: at least one case file and no problem. Exit 1: any problem.
    Exit 2: DIR is missing or holds no case file.
"""

import argparse
import datetime
import glob
import importlib.util
import json
import os
import posixpath
import re
import sys

REQUIRED = ("date", "project", "stack", "tools", "kind", "class", "signature", "guard", "status",
            "rule", "scope", "repeat_of", "rule_loaded", "session")
OPTIONAL = ("error_text", "probe", "guard_reason")
PROPOSAL = re.compile(r"^Proposed \(not built\): (.*)$", re.M)
PROPOSAL_MIN = 10  # "a check", "TODO" name no mechanism
KINDS = ("env-defect", "knowledge", "judgment", "shortcut")
STATUSES = ("rule", "archived", "fixed")
RULE_LOADED = ("yes", "no", "unknown")
ERROR_TEXT_MIN = 15  # shorter fragments match unrelated output
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
CASE_NAME = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SESSION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CLASSES_FILE = "classes.txt"
NOT_CASES = ("README.md",)
SCOPE = re.compile(r"^(global|skill:[a-z0-9]+(?:-[a-z0-9]+)*|project:[A-Za-z0-9_.-]+)$")
SKILL_DIR = re.compile(r"(?:^|/)skills/([^/]+)/", re.I)
# Where global rules live: the config dir ($CLAUDE_CONFIG_DIR, or the .claude folder of a home
# folder) or a clone of the Rigger repo, known by a plugin rules
# file. A project's own .claude folder belongs to the project, and so does a plugin rules folder
# not named rigger-*, such as wp-content/plugins/<x>/rules/.
HARNESS = re.compile(
    r"^\$(?:CLAUDE_CONFIG_DIR|\{CLAUDE_CONFIG_DIR\})/"
    r"|(?:^~|^\$(?:home|\{home\})|^%userprofile%|^/root|/(?:users|home)/[^/]+)/\.claude/"
    r"|(?:^|/)plugins/rigger-[a-z0-9-]+/rules/[^/]+\.md$", re.I)
HARNESS_WHERE = "~/.claude, $CLAUDE_CONFIG_DIR or a harness repo (plugins/rigger-<name>/rules/<file>.md in any clone)"
# Installed copies in the config dir (plugins/cache/<marketplace>/<plugin>/<version>/ and a marketplace's
# managed clone): `claude plugin update` replaces them, so a rule filed there is lost.
INSTALLED = re.compile(r"(?:^|/)plugins/(?:cache|marketplaces)/", re.I)
# A live:<rule id> guard in a case whose tools name one of these is a shell hook rule: a rule checker
# must confirm it (as must any rule id a loaded checker claims).
SHELL_TOOLS = ("powershell", "bash", "git-bash")
CHECKER_GLOB = ("plugins", "*", "tools", "*", "lessons_guard.py")

# error_text is measured the way error lines are matched: whitespace folded, then secrets, paths,
# ids and digits replaced by <placeholders>, which do not count towards its length.
SECRETS = [(re.compile(r"'[^'\s]*'@'[^'\s]*'"), "'<user>'@'<host>'"),
           (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@'\"]+@"), r"\1<cred>@"),
           (re.compile(r"[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])"), "<email>"),
           (re.compile(r"\?[^\s'\"<>]*=[^\s'\"<>]*"), "?<query>")]
TOKEN_RUN = re.compile(r"(?<![A-Za-z0-9_+-])[A-Za-z0-9_+-]{24,}={0,2}(?![A-Za-z0-9_+=-])")
SLUG_WORDS = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
PLACEHOLDER = re.compile(r"<[a-z]+>")


def _long_token(match):
    """A 24+ character run with letters and digits; not an --option, a hyphenated slug or a
    digit-free identifier."""
    run = match.group(0)
    if run.startswith("-") or SLUG_WORDS.fullmatch(run) or not re.search(r"[A-Za-z]", run) \
            or not re.search(r"\d", run):
        return run
    return "<token>"


MASKS = SECRETS + [
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![\\/])[^\s'\";&|]*"), "<path>"),
    (re.compile(r"(?<![\w>:/])/[\w.-]+(?:/[\w.-]+)+"), "<path>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b"), "<hex>"),
    (TOKEN_RUN, _long_token),
    (re.compile(r"\d+"), "<n>")]


def mask(text):
    text = " ".join(str(text).split())
    for rx, rep in MASKS:
        text = rx.sub(rep, text)
    return text


def error_text_length(text):
    return len(PLACEHOLDER.sub("", mask(text)).strip())


def parse_value(raw):
    """The YAML subset the archive uses: "double-quoted" (JSON escapes), [flow, list], plain."""
    raw = raw.strip()
    if raw.startswith('"'):
        return json.loads(raw)
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [item.strip() for item in inner.split(",")] if inner else []
    return raw


def read_case(path):
    """(frontmatter dict, body, problems) of one case file."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, "", [f"cannot read as UTF-8 ({type(exc).__name__}: {exc})"]
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        return {}, text, ["no frontmatter (first line must be ---)"]
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---")
    except StopIteration:
        return {}, text, ["frontmatter is not closed by ---"]
    meta, problems = {}, []
    for line in lines[1:end]:
        line = line.rstrip("\r")
        key, sep, raw = line.partition(":")
        if not sep or not SLUG.match(key.replace("_", "-")):
            problems.append(f"unreadable frontmatter line: {line!r}")
            continue
        if key in meta:
            problems.append(f"duplicate key: {key}")
            continue
        try:
            meta[key] = parse_value(raw)
        except ValueError as exc:
            problems.append(f"{key}: unreadable value ({exc})")
    return meta, "\n".join(lines[end + 1:]), problems


def read_classes(path):
    """(set of slugs, problems) from the classes file: one `slug: meaning` line per class."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return set(), [f"{path}: cannot read ({exc})"]
    slugs, problems = set(), []
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        slug, sep, meaning = line.partition(":")
        slug = slug.strip()
        if not sep or not SLUG.match(slug) or not meaning.strip():
            problems.append(f"{path}:{n}: expected `slug: meaning`, got {line!r}")
        elif slug in slugs:
            problems.append(f"{path}:{n}: duplicate class {slug}")
        else:
            slugs.add(slug)
    return slugs, problems


def load_cases(archive):
    """Every case file as (name, meta, body, problems), sorted by name."""
    cases = []
    for name in sorted(os.listdir(archive)):
        path = os.path.join(archive, name)
        dangling = os.path.islink(path) and not os.path.exists(path)  # read_case reports it
        if name in NOT_CASES or not name.endswith(".md") or not (os.path.isfile(path) or dangling):
            continue
        meta, body, problems = read_case(path)
        cases.append((name, meta, body, problems))
    return cases


def text_key(value):
    """A hashable stand-in for a frontmatter value (a [list] where a string belongs)."""
    return value if isinstance(value, str) else repr(value)


def repo_list(repo):
    """`repo` as a list of folders: one path or several."""
    return [repo] if isinstance(repo, (str, os.PathLike)) else list(repo)


def test_guard_problem(value, rel, name, repo):
    """None when `rel` in `repo` defines the test `name`, else the problem."""
    try:
        with open(os.path.join(repo, rel), encoding="utf-8") as fh:
            text = re.sub(r"(?s)<#.*?#>", "", fh.read())  # PowerShell block comments
    except OSError:
        return f"guard {value}: test file {rel} not found"
    except UnicodeDecodeError:
        return f"guard {value}: test file {rel} is not UTF-8"
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    defined = re.compile(r"\bdef " + re.escape(name) + r"\(|\bcheck\(\"" + re.escape(name) + '"'
                         + r"|^\s*Test-Case '" + re.escape(name) + "'")
    if name and any(defined.search(ln) for ln in code):
        return None
    return f"guard {value}: no `def {name}(`, `check(\"{name}\"` or `Test-Case '{name}'` in {rel}"


def find_checkers(repos, given=()):
    """The `given` checker files, then every repo's plugins/*/tools/*/lessons_guard.py in repo
    order; each real file once."""
    found, seen = [], set()
    discovered = [path for repo in repos for path in sorted(glob.glob(os.path.join(repo, *CHECKER_GLOB)))]
    for path in list(given) + discovered:
        real = os.path.normcase(os.path.realpath(path))
        if real not in seen:
            seen.add(real)
            found.append(path)
    return found


def load_checkers(paths):
    """(modules, problems): each rule checker file loaded; one that cannot be used is a problem."""
    modules, problems = [], []
    for n, path in enumerate(paths):
        try:
            spec = importlib.util.spec_from_file_location(f"lessons_rule_checker_{n}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # a checker that does not load confirms nothing
            problems.append(f"{path}: rule checker cannot load ({type(exc).__name__}: {exc})")
            continue
        if not callable(getattr(module, "rule_guard_problem", None)):
            problems.append(f"{path}: rule checker has no rule_guard_problem")
            continue
        modules.append(module)
    return modules, problems


def claims(module, rule):
    """True when the checker's RULES (a set, list or tuple of rule ids) holds `rule`."""
    rules = getattr(module, "RULES", ())
    return isinstance(rules, (set, frozenset, list, tuple)) and rule in rules


def rule_guard_problem(value, meta, checkers):
    """None if a `live:<rule id>` guard is acceptable. A rule id a loaded checker claims (its RULES) goes
    to the checkers that claim it, whatever the case's tools say. Otherwise, in a case whose tools name a
    shell it is a shell hook rule: one of the loaded checkers must confirm it. Any other rule id is
    accepted."""
    rule, problems = value[5:], []
    claiming = [module for module in checkers if claims(module, rule)]
    tools = meta.get("tools") if isinstance(meta.get("tools"), list) else []
    if not claiming and not any(tool in SHELL_TOOLS for tool in tools):
        return None
    if not checkers:
        return (f"guard {value}: the case's tools name a shell, so a rule checker must confirm the guard, "
                "and none is loaded (--rule-checker FILE, or a --repo with plugins/*/tools/*/lessons_guard.py)")
    for module in claiming or checkers:
        try:
            problem = module.rule_guard_problem(rule, meta)
        except Exception as exc:  # a checker that crashes confirms nothing
            problem = f"guard {value}: rule checker {module.__file__} raised {type(exc).__name__}: {exc}"
        if problem is None:
            return None
        problems.append(str(problem))
    return "; ".join(problems)


def guard_problem(value, repo, meta=None, checkers=()):
    """None if `guard` is none, proposed or a valid live guard. A test guard `live:<file>::<name>` must
    name a file under a tests/ folder that defines that test in one of the repos; a rule guard is
    checked by `rule_guard_problem`."""
    if value in ("none", "proposed"):
        return None
    if not isinstance(value, str) or not value.startswith("live:") or not value[5:]:
        return f"guard must be none, proposed or live:<rule id>, got {value!r}"
    target = value[5:]
    if "::" not in target:
        return rule_guard_problem(value, meta or {}, checkers)
    rel, _, name = target.partition("::")
    if "tests" not in re.split(r"[\\/]", rel)[:-1]:
        return f"guard {value}: {rel} is not a file under a tests/ folder"
    first = None
    for one in repo_list(repo):
        problem = test_guard_problem(value, rel, name, one)
        if problem is None:
            return None
        if first is None or first.endswith(" not found"):
            first = problem
    return first or f"guard {value}: test file {rel} not found"


def scope_problem(meta):
    """None when `scope` is valid and the `rule` path fits it."""
    scope = meta.get("scope")
    if not isinstance(scope, str) or not SCOPE.match(scope):
        return f"scope must be global, skill:<name> or project:<name>, got {scope!r}"
    kind, _, name = scope.partition(":")
    if kind == "project" and name != meta.get("project"):
        return f"scope {scope} names another project than {meta.get('project')}"
    rule = meta.get("rule")
    if not isinstance(rule, str) or rule == "none":
        return None
    path = posixpath.normpath(rule.split("#")[0].replace("\\", "/"))
    skill = SKILL_DIR.search(path)
    if kind != "project" and INSTALLED.search(path):
        return f"scope {scope}: {path} is an installed copy that the next update replaces; use the plugin's source repo"
    if kind == "project":
        return f"scope {scope} needs its rule in that project, not {path}" if HARNESS.search(path) else None
    if kind == "skill":
        return None if skill and skill.group(1) == name else f"scope {scope} needs its rule under skills/{name}/"
    if not HARNESS.search(path):
        return f"scope global needs its rule in {HARNESS_WHERE}, not {path}"
    return f"scope global but the rule is in a skill: scope skill:{skill.group(1)}" if skill else None


def check_case(name, meta, body, own, classes, classes_name, by_name, class_projects, project_scoped, repo,
               checkers=()):
    found = list(own)
    match = CASE_NAME.match(name)
    if not match:
        found.append("file name must be YYYY-MM-DD-<slug>.md")
    found += [f"missing key: {k}" for k in REQUIRED if k not in meta]
    found += [f"unknown key: {k}" for k in meta if k not in REQUIRED + OPTIONAL]
    date = meta.get("date")
    valid_date = isinstance(date, str) and bool(DATE.match(date))
    if valid_date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            valid_date = False
    if not valid_date:
        found.append(f"date is not YYYY-MM-DD: {date!r}")
    if match and date is not None and match.group(1) != date:
        found.append(f"date {date} differs from the file name")
    for key in ("stack", "tools"):
        if key in meta and not isinstance(meta[key], list):
            found.append(f"{key} must be a [list]")
    if "kind" in meta and meta["kind"] not in KINDS:
        found.append(f"kind must be one of {', '.join(KINDS)}, got {meta['kind']!r}")
    if "class" in meta and text_key(meta["class"]) not in classes:
        found.append(f"class {meta['class']!r} is not in {classes_name}")
    for key in ("signature", "error_text", "project", "rule", "probe", "guard_reason"):
        if key in meta and (not isinstance(meta[key], str) or not meta[key].strip()):
            found.append(f"{key} must be a non-empty string")
    error_text = meta.get("error_text")
    if isinstance(error_text, str) and error_text.strip() and error_text_length(error_text) < ERROR_TEXT_MIN:
        found.append(f"error_text is shorter than {ERROR_TEXT_MIN} characters after masking: {mask(error_text)!r}")
    if meta.get("rule_loaded") == "yes" and meta.get("kind") in ("judgment", "knowledge"):
        found.append(f"rule_loaded yes means the rule was known: kind shortcut, not {meta['kind']}")
    guard = meta.get("guard")
    is_rule_guard = isinstance(guard, str) and guard.startswith("live:") and "::" not in guard
    if "guard" in meta:
        problem = guard_problem(guard, repo, meta, checkers)
        if problem:
            found.append(problem)
    if "probe" in meta and not is_rule_guard:
        found.append("probe is only for a live rule guard (live:<rule id>, not a test)")
    if "guard_reason" in meta and guard != "none":
        found.append(f"guard_reason is only for guard: none, not {guard}")
    if guard == "proposed":
        proposal = PROPOSAL.search(body)
        if not proposal or len(proposal.group(1).strip()) < PROPOSAL_MIN:
            found.append("guard proposed needs a body line `Proposed (not built): <mechanism>` "
                         f"(at least {PROPOSAL_MIN} characters)")
    if "status" in meta and meta["status"] not in STATUSES:
        found.append(f"status must be one of {', '.join(STATUSES)}, got {meta['status']!r}")
    if meta.get("status") == "rule" and meta.get("rule") == "none":
        found.append("status rule needs a rule path, not none")
    if "scope" in meta:
        problem = scope_problem(meta)
        if problem:
            found.append(problem)
        cls = text_key(meta.get("class"))
        projects = class_projects.get(cls, set())
        if not problem and not meta["scope"].startswith("project:") and cls in project_scoped \
                and len(projects) < 2:
            only = next(iter(projects))
            found.append(f"scope {meta['scope']}: class {cls} has a project-scoped case and cases from one "
                         f"project only ({only}); keep it project:{only} until a second project has the class")
    if "rule_loaded" in meta and meta["rule_loaded"] not in RULE_LOADED:
        found.append(f"rule_loaded must be one of {', '.join(RULE_LOADED)}, got {meta['rule_loaded']!r}")
    if "session" in meta and meta["session"] != "unknown" and not SESSION.match(str(meta["session"])):
        found.append(f"session must be a session id or unknown, got {meta['session']!r}")
    earlier = meta.get("repeat_of")
    if earlier not in (None, "none"):
        if not isinstance(earlier, str) or earlier not in by_name:
            found.append(f"repeat_of {earlier!r} is not a case file here")
        elif earlier >= name:
            found.append(f"repeat_of {earlier} is not earlier than this case")
        elif text_key(by_name[earlier].get("class")) != text_key(meta.get("class")):
            found.append(f"repeat_of {earlier} has another class ({by_name[earlier].get('class')})")
        else:
            latest = max(n for n, m in by_name.items()
                         if n < name and text_key(m.get("class")) == text_key(meta.get("class")))
            if earlier != latest:
                found.append(f"repeat_of {earlier} is not the latest earlier case of the class ({latest})")
        if guard == "none" and "guard_reason" not in meta:
            found.append("a repeat needs a guard: proposed or live, or none with a guard_reason "
                         "(why no deterministic check can catch the class)")
    if not body.strip():
        found.append("empty body")
    return found


def lint(archive, classes_path=None, repo=None, rule_checkers=()):
    """(number of case files, problem lines). Test guards resolve against `repo` (one folder or a
    list; by default the archive's parent folder). Rule checkers: the `rule_checkers` files, then
    each repo's plugins/*/tools/*/lessons_guard.py."""
    classes_path = classes_path or os.path.join(archive, CLASSES_FILE)
    repos = repo_list(repo) if repo else [os.path.dirname(os.path.abspath(archive))]
    classes, problems = read_classes(classes_path)
    checkers, checker_problems = load_checkers(find_checkers(repos, rule_checkers))
    problems += checker_problems
    classes_name = os.path.basename(classes_path)
    cases = load_cases(archive)
    by_name = {name: meta for name, meta, _, _ in cases}
    class_projects, project_scoped = {}, set()
    for meta in by_name.values():
        class_projects.setdefault(text_key(meta.get("class")), set()).add(str(meta.get("project")))
        if str(meta.get("scope")).startswith("project:") and meta.get("rule") != "none":
            project_scoped.add(text_key(meta.get("class")))
    for name, meta, body, own in cases:
        path = os.path.join(archive, name)
        if not meta and own:
            problems += [f"{path}: {p}" for p in own]
            continue
        found = check_case(name, meta, body, own, classes, classes_name, by_name, class_projects, project_scoped,
                           repos, checkers)
        problems += [f"{path}: {p}" for p in found]
    return len(cases), problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", required=True, help="the case archive folder")
    parser.add_argument("--classes", default=None, help="the classes file (default: ARCHIVE/classes.txt)")
    parser.add_argument("--repo", action="append", default=None,
                        help="folder that test guards (live:<file>::<test>) resolve against; repeat for "
                             "several (default: ARCHIVE/..)")
    parser.add_argument("--rule-checker", action="append", default=[],
                        help="a rule checker file (lessons_guard.py) for live:<rule id> guards; repeatable")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if not os.path.isdir(args.archive):
        print(f"# archive folder not found: {args.archive}")
        return 2
    missing = [path for path in (args.repo or []) if not os.path.isdir(path)]
    missing += [path for path in args.rule_checker if not os.path.isfile(path)]
    if missing:
        print(f"# not found: {', '.join(missing)}")
        return 2
    count, problems = lint(args.archive, args.classes, args.repo, args.rule_checker)
    for problem in problems:
        print(problem)
    if not count:
        print(f"# no case files in {args.archive}")
        return 2
    print(f"# {count} case files checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
