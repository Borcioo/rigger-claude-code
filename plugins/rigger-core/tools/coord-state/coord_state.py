"""Keeps a coordinator's part states in `<config dir>/state/<session id>.parts.json` and refuses every move
its transition table does not allow (config dir: CLAUDE_CONFIG_DIR, else ~/.claude; session id:
CLAUDE_CODE_SESSION_ID). Only this script writes the file.

  coord_state.py init                                   create an empty file (refused when it exists)
  coord_state.py add <part> [--waits-on a,b]            new part in `planned`
  coord_state.py move <part> <state> [--head <sha>] [--branch <b>] [--session <id>] [--evidence <text>]
  coord_state.py show                                   Markdown table: part, state, head, updated; then `# <state> <n>`
  coord_state.py check <part> <state>                   ok only when the part is in that state

Exit 0 prints `coord-state: ok <part> <state>` (`ok init`; `show` prints the table). Exit 2 prints one line
`coord-state: refused: <reason>`. Output without a `coord-state: ok` line is a refusal: `bin/py.sh` exits 0
silently when it finds no Python.
"""

import copy
import json
import os
import re
import sys
import tempfile
import time

OK, REFUSED = "coord-state: ok ", "coord-state: refused: "
ALL = ("planned", "started", "built", "reviewed", "merged", "blocked", "dropped")
# from-state -> to-state -> required arguments; "a|b" needs one of them; None is "(new)", reached by `add` only.
TRANSITIONS = {
    None: {"planned": ()},
    "planned": {"started": ("session|branch",), "blocked": ("evidence",), "dropped": ("evidence",)},
    "started": {"built": ("head", "evidence"), "blocked": ("evidence",), "dropped": ("evidence",)},
    "built": {"reviewed": ("evidence",), "started": ("evidence",), "blocked": ("evidence",)},
    "reviewed": {"merged": ("head", "evidence"), "started": ("evidence",), "blocked": ("evidence",)},
    "blocked": {"started": ("evidence",), "dropped": ("evidence",)},
}
PART_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SHA = re.compile(r"[0-9a-f]{7,40}")
# The user's OK in straight, English curly or Polish low-high quotes, spelled with chr() so no editor turns
# them into ASCII ones.
LEFT_DOUBLE, RIGHT_DOUBLE, LOW_DOUBLE = chr(0x201C), chr(0x201D), chr(0x201E)
QUOTED = re.compile('"[^"]+"|%s[^%s]+%s|%s[^%s]+%s' % (LEFT_DOUBLE, RIGHT_DOUBLE, RIGHT_DOUBLE,
                                                     LOW_DOUBLE, RIGHT_DOUBLE, RIGHT_DOUBLE))
COMMANDS = {"init": (0, ()), "add": (1, ("waits-on",)), "move": (2, ("head", "branch", "session", "evidence")),
            "show": (0, ()), "check": (2, ())}
VERSION = 1


class Refused(Exception):
    pass


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def state_path(environ):
    session = environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        raise Refused("no session id")
    if not SESSION_ID.fullmatch(session):
        raise Refused("bad session id")
    config = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(config, "state", session + ".parts.json")


def check_name(name):
    if not PART_NAME.fullmatch(name):
        raise Refused("bad part name")


def check_shape(data):
    if not isinstance(data, dict):
        raise Refused("wrong shape: expected an object")
    version = data.get("version")
    if type(version) is int and version > VERSION:
        raise Refused("version %d is newer than %d" % (version, VERSION))
    if type(version) is not int or version != VERSION:
        raise Refused("wrong shape: version must be 1")
    parts = data.get("parts")
    if not isinstance(parts, dict):
        raise Refused("wrong shape: parts must be an object")
    for name, entry in parts.items():
        if not PART_NAME.fullmatch(name):
            raise Refused("wrong shape: bad part name in file")
        if not isinstance(entry, dict):
            raise Refused("wrong shape: part %s must be an object" % name)
        if entry.get("state") not in ALL:
            raise Refused("wrong shape: part %s has an unknown state" % name)
        waits = entry.get("waits_on")
        if not isinstance(waits, list) or not all(isinstance(w, str) and PART_NAME.fullmatch(w) for w in waits):
            raise Refused("wrong shape: part %s waits_on must be a list of part names" % name)
        if not isinstance(entry.get("history"), list):
            raise Refused("wrong shape: part %s history must be a list" % name)


def load(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        raise Refused("no state file; run init")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as error:  # JSONDecodeError and UnicodeDecodeError
        raise Refused("unreadable file (%s)" % error)
    check_shape(data)
    return data


def save(path, data):
    folder = os.path.dirname(path)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    handle, temp = tempfile.mkstemp(dir=folder, prefix=".parts-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(text.encode("utf-8"))
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise Refused("write failed")


def apply_move(data, part, to, args, now):
    check_name(part)
    parts = data["parts"]
    if part not in parts:
        raise Refused("unknown part %s" % part)
    if to not in ALL:
        raise Refused("unknown state")
    src = parts[part]["state"]
    needs = TRANSITIONS.get(src, {}).get(to)
    if needs is None:
        raise Refused("not allowed: %s -> %s" % (src, to))
    if args.get("head") is not None and not SHA.fullmatch(args["head"]):
        raise Refused("bad --head")
    for name in needs:
        if not any(args.get(option) for option in name.split("|")):
            raise Refused("missing --" + name.replace("|", " or --"))
    if to == "started":  # every way into `started`, so a blocked part cannot skip what it waits on
        for other in parts[part]["waits_on"]:
            state = parts[other]["state"] if isinstance(parts.get(other), dict) else "missing"
            if state != "merged":
                raise Refused("%s waits on %s (%s)" % (part, other, state))
    if to == "merged" and not QUOTED.search(args["evidence"]):
        raise Refused("merged needs the user's OK quoted in --evidence")
    result = copy.deepcopy(data)
    entry = result["parts"][part]
    entry["state"], entry["updated"] = to, now
    for name in ("head", "branch", "session"):
        if args.get(name):
            entry[name] = args[name]
    entry["history"].append({"from": src, "to": to, "at": now, "evidence": args.get("evidence")})
    return result


def parse(argv):
    if not argv:
        raise Refused("no command")
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        raise Refused("unknown command %s" % (command if PART_NAME.fullmatch(command) else "(bad)"))
    count, options = COMMANDS[command]
    positional, values, index = [], {}, 0
    while index < len(rest):
        token = rest[index]
        if token.startswith("--"):
            name = token[2:]
            if name not in options:
                raise Refused("unknown option --%s" % (name if PART_NAME.fullmatch(name) else "(bad)"))
            if index + 1 >= len(rest):
                raise Refused("missing value for --%s" % name)
            if name in values:
                raise Refused("repeated --%s" % name)
            values[name] = rest[index + 1]
            index += 2
        else:
            positional.append(token)
            index += 1
    if len(positional) != count:
        raise Refused("wrong arguments for %s" % command)
    return command, positional, values


def run(argv, environ):
    command, positional, values = parse(argv)
    path = state_path(environ)
    if command == "init":
        if os.path.exists(path):
            raise Refused("file exists")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save(path, {"version": VERSION, "parts": {}})
        return [OK + "init"]
    if command in ("add", "check"):  # move: apply_move checks the name
        check_name(positional[0])
    data = load(path)
    parts = data["parts"]
    if command == "add":
        part = positional[0]
        waits = values["waits-on"].split(",") if "waits-on" in values else []
        for other in waits:
            check_name(other)
        if part in parts:
            raise Refused("part %s exists" % part)
        for other in waits:
            if other not in parts:
                raise Refused("unknown part %s" % other)
        now = utcnow()
        parts[part] = {"state": "planned", "branch": None, "session": None, "head": None, "updated": now,
                       "waits_on": waits, "history": [{"from": None, "to": "planned", "at": now, "evidence": None}]}
        save(path, data)
        return [OK + "%s planned" % part]
    if command == "move":
        part, to = positional
        save(path, apply_move(data, part, to, values, utcnow()))
        return [OK + "%s %s" % (part, to)]
    if command == "check":
        part, asked = positional
        if part not in parts:
            raise Refused("unknown part %s" % part)
        actual = parts[part]["state"]
        if actual != asked:
            raise Refused("%s is %s, not %s" % (part, actual, asked if asked in ALL else "(unknown state)"))
        return [OK + "%s %s" % (part, actual)]
    lines = ["| part | state | head | updated |", "|---|---|---|---|"]
    counts = {}
    for name in sorted(parts):
        entry = parts[name]
        lines.append("| %s | %s | %s | %s |" % (name, entry["state"], entry.get("head") or "", entry.get("updated") or ""))
        counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    lines += ["# %s %d" % (state, counts[state]) for state in sorted(counts)]
    return lines


def main(argv, environ=os.environ):
    try:
        lines = run(argv, environ)
    except Refused as reason:
        print(REFUSED + str(reason))
        return 2
    except Exception as error:
        print(REFUSED + "internal error: " + type(error).__name__)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main(sys.argv[1:]))
