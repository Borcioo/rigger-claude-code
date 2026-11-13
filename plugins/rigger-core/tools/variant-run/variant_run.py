"""Run a command or a claude eval with a modified copy of the global CLAUDE.md instead of the live one.

python -I variant_run.py prepare <workdir> [--source <CLAUDE.md>] [--drop-section "<heading>"]... [--marker <text>]
python -I variant_run.py run <workdir> [prepare options] [--drop-rule <name>]... -- <command...>
python -I variant_run.py verify <workdir> [--claude <exe>] [--agents <abs json>] [--drop-rule <name>]...

The copy goes to <workdir>/CLAUDE.md, or to <workdir>/.claude/CLAUDE.md when the work dir has its own
CLAUDE.md; <workdir>/.claude/settings.local.json gets the source path in "claudeMdExcludes", so claude
started in the work dir loads the copy and not the source. --drop-rule names a rules file or a "## "
heading the rules hooks skip, passed to the child in HARNESS_RULES_DROP.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys

HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")
STATE_FILE = "variant-run.json"
COPY_PLACES = ("CLAUDE.md", ".claude/CLAUDE.md")
VERIFY_TIMEOUT = 600
RULES_DROP = "HARNESS_RULES_DROP"


class Refusal(Exception):
    """A request prepare cannot satisfy exactly; nothing has been written."""


def headings(lines):
    """(index, level, text) of every ATX heading outside fenced code blocks."""
    found, fence = [], None
    for index, line in enumerate(lines):
        opener = FENCE.match(line)
        if opener:
            if fence is None:
                fence = opener.group(1)
            elif opener.group(1) == fence:
                fence = None
            continue
        if fence is None:
            match = HEADING.match(line.rstrip("\r\n"))
            if match:
                found.append((index, len(match.group(1)), match.group(2)))
    return found


def drop_sections(text, names):
    lines = text.splitlines(keepends=True)
    marks = headings(lines)
    dropped = set()
    for name in dict.fromkeys(names):
        hits = [pos for pos, (_, _, title) in enumerate(marks) if title == name]
        if not hits:
            raise Refusal(f"heading not found: {name!r}")
        if len(hits) > 1:
            raise Refusal(f"heading occurs {len(hits)} times: {name!r}")
        start, level, _ = marks[hits[0]]
        end = len(lines)
        for index, other_level, _ in marks[hits[0] + 1:]:
            if other_level <= level:
                end = index
                break
        dropped.update(range(start, end))
    return "".join(line for index, line in enumerate(lines) if index not in dropped)


def merged_settings(path, exclude):
    settings = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except (OSError, ValueError) as exc:
            raise Refusal(f"cannot read {path}: {exc}") from exc
        if not isinstance(settings, dict):
            raise Refusal(f"{path} is not a JSON object")
    excludes = settings.get("claudeMdExcludes", [])
    if not isinstance(excludes, list):
        raise Refusal(f"{path}: claudeMdExcludes is not a list")
    if exclude not in excludes:
        excludes = [*excludes, exclude]
    settings["claudeMdExcludes"] = excludes
    return settings


def recorded_copy(state, workdir):
    """The copy an earlier prepare recorded; prepare records only the COPY_PLACES."""
    try:
        with open(state, encoding="utf-8") as fh:
            copy = json.load(fh)["copy"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refusal(f"cannot read {state}: {exc!r}") from exc
    if copy not in COPY_PLACES:
        raise Refusal(f"{state}: copy must be one of {COPY_PLACES}, not {copy!r}")
    return os.path.normpath(os.path.join(workdir, copy))


def copy_path(workdir):
    """Root CLAUDE.md unless the work dir has its own; a copy written there earlier is reused."""
    state = os.path.join(workdir, ".claude", STATE_FILE)
    if os.path.exists(state):
        return recorded_copy(state, workdir)
    if os.path.exists(os.path.join(workdir, "CLAUDE.md")):
        nested = os.path.join(workdir, ".claude", "CLAUDE.md")
        if os.path.exists(nested):
            raise Refusal(f"the work dir has both CLAUDE.md and .claude/CLAUDE.md, no place for the copy: {workdir}")
        return nested
    return os.path.join(workdir, "CLAUDE.md")


def within(parent, path):
    """Both paths from realpath, which gives existing parts their on-disk case; commonpath ignores case."""
    try:
        return os.path.commonpath([parent, path]) == parent
    except ValueError:
        return False


def config_dirs():
    """~/.claude and the config dir in effect, resolved."""
    dirs = [os.path.join(os.path.expanduser("~"), ".claude")]
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        dirs.append(configured)
    return [os.path.realpath(path) for path in dirs]


def default_source():
    """The CLAUDE.md of the config dir in effect."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
    return os.path.join(configured, "CLAUDE.md")


def check_targets(workdir, source, targets):
    """Refuse writes that leave the work dir, reach a live config dir or overwrite the source, links resolved."""
    guarded = config_dirs()
    real_workdir = os.path.realpath(workdir)
    for config in guarded:
        if within(real_workdir, config):
            raise Refusal(f"the work dir is the home dir or an ancestor of {config}: {workdir}")
    real_source = os.path.realpath(source)
    for target in targets:
        real = os.path.realpath(target)
        if not within(real_workdir, real):
            raise Refusal(f"refusing to write outside the work dir: {target}")
        for config in guarded:
            if within(config, real):
                raise Refusal(f"refusing to write under {config}: {target}")
        if real == real_source:
            raise Refusal(f"refusing to overwrite the source: {target}")


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def prepare(workdir, source, drops, marker):
    workdir = os.path.abspath(workdir)
    source = os.path.abspath(os.path.expanduser(source))
    if not os.path.isdir(workdir):
        raise Refusal(f"work dir not found: {workdir}")
    try:
        with open(source, encoding="utf-8", newline="") as fh:
            original = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise Refusal(f"cannot read source {source}: {exc}") from exc
    text = drop_sections(original, drops)
    removed = len(original) - len(text)
    if marker is not None:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"When asked for the variant marker, print {marker}.\n"
    settings_path = os.path.join(workdir, ".claude", "settings.local.json")
    settings = merged_settings(settings_path, source.replace("\\", "/"))
    target = copy_path(workdir)
    state_path = os.path.join(workdir, ".claude", STATE_FILE)
    check_targets(workdir, source, [target, settings_path, state_path])
    os.makedirs(os.path.join(workdir, ".claude"), exist_ok=True)
    write_text(target, text)
    write_text(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    write_text(state_path,
               json.dumps({"copy": os.path.relpath(target, workdir).replace("\\", "/")}) + "\n")
    print(f"removed {removed} chars, wrote {len(text)} chars to {target}", flush=True)
    return target


def add_prepare_options(parser):
    parser.add_argument("workdir")
    parser.add_argument("--source")
    parser.add_argument("--drop-section", action="append", default=[], dest="drops")
    parser.add_argument("--marker")


def add_drop_rule(parser):
    parser.add_argument("--drop-rule", action="append", default=[], dest="rules")


def rules_drop(names):
    """The HARNESS_RULES_DROP value; its consumers split on ; and newlines, so an entry cannot hold them."""
    for name in names:
        if not name.strip() or re.search(r"[;\r\n]", name):
            raise Refusal(f"--drop-rule must be one non-empty entry without ';' or a newline: {name!r}")
    return ";".join(names)


def child_env(rules):
    """The child's environment: HARNESS_RULES_DROP is this run's value or absent, never the parent's."""
    env = dict(os.environ)
    env.pop(RULES_DROP, None)
    if rules:
        env[RULES_DROP] = rules
    return env


def run_command(workdir, command, rules):
    exe = shutil.which(command[0]) or command[0]
    return subprocess.run([exe, *command[1:]], cwd=workdir, env=child_env(rules)).returncode


def claude_argv(claude, prompt, agents):
    exe = shutil.which(claude) or claude
    argv = [sys.executable, "-I", exe] if exe.lower().endswith(".py") else [exe]
    argv += ["-p", prompt, "--model", "haiku", "--max-budget-usd", "0.2", "--output-format", "stream-json",
             "--verbose", "--no-session-persistence"]
    if agents:
        argv += ["--agents", agents]
    return argv


VERIFY_AGENT = "rigger-core:worker"  # never one with omitClaudeMd: true, which cannot see the marker
VERIFY_PROMPT = (
    f"Call the Agent tool once with subagent_type \"{VERIFY_AGENT}\" and pass it this brief word for word, "
    "adding nothing: \"Do not use any tools and do not edit anything. If your instructions say what to print "
    "when asked for the variant marker, reply with exactly that text and nothing else; otherwise reply with "
    "exactly NO-MARKER.\" Then reply with the subagent's answer."
)


def events(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def blocks(event, kind):
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind] if isinstance(content, list) else []


def judge(stdout, marker):
    """(passed, reason, cost) from claude's stream-json output.

    The subagent's reply is the Agent call's tool_result, or for a backgrounded agent the
    handback_report of the task_notification carrying the call's tool_use_id.
    """
    calls, results, cost, init = {}, {}, None, False
    for event in events(stdout):
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = True
        elif kind == "system" and event.get("subtype") == "task_notification":
            report = json.dumps(event.get("handback_report"), ensure_ascii=False)
            results.setdefault(event.get("tool_use_id"), []).append(report)
        elif kind == "result":
            cost = event.get("total_cost_usd")
        elif kind == "assistant" and event.get("parent_tool_use_id") is None:
            for block in blocks(event, "tool_use"):
                if block.get("name") in ("Agent", "Task"):
                    calls[block.get("id")] = block.get("input") or {}
        elif kind == "user" and event.get("parent_tool_use_id") is None:
            for block in blocks(event, "tool_result"):
                answer = json.dumps(block.get("content"), ensure_ascii=False)
                results.setdefault(block.get("tool_use_id"), []).append(answer)
    if not init:
        return False, "no init event (claude did not start; read stderr)", cost
    asked = [cid for cid, call in calls.items() if call.get("subagent_type") == VERIFY_AGENT]
    if not asked:
        return False, f"no Agent call to {VERIFY_AGENT}", cost
    for cid in asked:
        if marker in json.dumps(calls[cid], ensure_ascii=False):
            return False, "the marker leaked into the brief, so the result proves nothing", cost
    answers = [answer for cid in asked for answer in results.get(cid, [])]
    if not answers:
        return False, f"no tool_result for the {VERIFY_AGENT} call", cost
    if any(marker in answer for answer in answers):
        return True, "the subagent's reply contains the marker", cost
    return False, "the subagent's reply lacks the marker: " + answers[-1][:200], cost


def verify(workdir, claude, agents, rules):
    if agents and not os.path.isabs(agents):
        raise Refusal(f"--agents must be an absolute path: {agents}")
    marker = "VARIANT-" + secrets.token_hex(6)
    prepare(workdir, default_source(), [], marker)
    proc = subprocess.run(claude_argv(claude, VERIFY_PROMPT, agents), cwd=os.path.abspath(workdir),
                          stdout=subprocess.PIPE, timeout=VERIFY_TIMEOUT, env=child_env(rules))
    stdout = proc.stdout.decode("utf-8", errors="replace")
    passed, reason, cost = judge(stdout, marker)
    print(f"verify: {'PASS' if passed else 'FAIL'}: {reason}; claude exit {proc.returncode}, cost {cost}")
    return 0 if passed else 1


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", newline="\n")
    command = []
    if argv[:1] == ["run"] and "--" in argv:
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(prog="variant_run.py")
    sub = parser.add_subparsers(dest="action", required=True)
    add_prepare_options(sub.add_parser("prepare"))
    launch = sub.add_parser("run")
    add_prepare_options(launch)
    add_drop_rule(launch)
    check = sub.add_parser("verify")
    check.add_argument("workdir")
    check.add_argument("--claude", default="claude")
    check.add_argument("--agents")
    add_drop_rule(check)
    args = parser.parse_args(argv)
    try:
        rules = rules_drop(getattr(args, "rules", []))
        if args.action == "verify":
            return verify(args.workdir, args.claude, args.agents, rules)
        if args.action == "run" and not command:
            raise Refusal("run needs a command after --")
        prepare(args.workdir, args.source or default_source(), args.drops, args.marker)
        if args.action == "run":
            return run_command(os.path.abspath(args.workdir), command, rules)
        return 0
    except Refusal as exc:
        print(f"variant_run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
