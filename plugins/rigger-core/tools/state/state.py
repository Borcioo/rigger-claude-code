"""Work items, folder cleanup and log rotation for the session state files in `<config dir>/state/` (config dir:
CLAUDE_CONFIG_DIR, else ~/.claude). The model writes the state file's prose; this tool owns its `## Items` section,
one line per item:

  - <id> · <status> · <#PR or —> · <branch or —> · <owner/name or —> · <title>

with optional indented detail lines below it. Statuses: open, doing, blocked, done.

  state.py [--state-dir D] [--session ID] [--projects-dir P] <command>
  add <id> <title> [--branch B] [--repo owner/name]   new `open` item; repository from the cwd's `origin`
  set <id> <status> [--pr N] [--branch B]             change status, PR or branch (`done` goes through `done`)
  note <id> <text>                                    add a detail line
  done <id> --pr N                                    the item becomes one line; its detail lines move to
                                                      `<session>.log.md` under `## <id> (<ISO time>)`
  show                                                print the item lines
  check [--now ISO]                                   stale items of this session's file: a merged PR (gh, 3 s per
                                                      call, 5 s per run, merged answers cached in pr-cache.json),
                                                      a branch gone locally and as a remote-tracking ref, and
                                                      `safe_to_compact: false` with `updated` over 30 minutes old
  clean [--now ISO]                                   archive idle sessions' files (14 days), delete archived files
                                                      after 60 days, rotate logs over 50 KiB; under `.clean.lock`,
                                                      within a 5 s budget counted from the process start
  hook [--now ISO]                                    SessionStart: reads `source` and `session_id` from stdin;
                                                      startup cleans, resume restores this session's archived files
                                                      and checks, clear and compact check; at most 6 lines, exit 0

A session left open while another session's clean archived its files gets them back at its next own entry point:
`hook` on every source, `check`, `add`, `set`, `done`, and context_guard.py on the next prompt (restore_on_miss).
A restore first writes the stamp `<session>.restored` (mtime = restore time), which counts as activity for the
idle rule; the state file keeps its archive time, because compact_guard judges staleness by that mtime.

Every write keeps the bytes outside the lines it changes and replaces the file atomically. Exit 0 prints a
`state: ok …` line (`hook` prints only findings); exit 2 prints one line `state: refused: <reason>`. Output is
UTF-8 with LF line ends.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

STARTED = time.monotonic()
OK, REFUSED = "state: ok ", "state: refused: "
DOT, NONE = chr(0xB7), chr(0x2014)
SEP = " %s " % DOT
STATUSES = ("open", "doing", "blocked", "done")
ITEM_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# Claude Code's session ids are UUIDs: clean archives another session's files only when a UUID names them, never
# README.md. Paths for one named session (restore, its own log, archived files) take any SESSION_ID.
SESSION_FILE_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./+-]{0,199}")
PR = re.compile(r"[1-9][0-9]{0,6}")
CONTROL = re.compile(r"[\x00-\x1f\x7f\x85  ]")
ITEM = re.compile(r"- (?P<id>%s) %s (?P<status>%s) %s (?P<pr>%s|#%s) %s (?P<branch>%s|%s) %s (?P<repo>%s|%s) %s "
                  r"(?P<title>.+)" % (ITEM_ID.pattern, DOT, "|".join(STATUSES), DOT, NONE, PR.pattern, DOT, NONE,
                                     BRANCH.pattern, DOT, NONE, REPO.pattern, DOT))
HEADING = re.compile(rb"#{1,6}(?:[ \t]|$)")
FENCE = re.compile(rb"[ ]{0,3}(```|~~~)")
ITEMS_HEADING = b"## Items"

DAY = 86400
IDLE_DAYS, ARCHIVE_DAYS = 14, 60
STALE_FALSE = 30 * 60
LOCK_TAKEOVER = 10 * 60
CLEAN_BUDGET = 5.0   # seconds, under the hook's 10 s timeout
LOG_MAX, LOG_KEEP = 50 * 1024, 25 * 1024
GH_CALL, GH_RUN = 3.0, 5.0
SHARED = {"compact-guard.log", "pr-cache.json", ".clean.lock", "archive"}
STAMP = "restored"
SUFFIXES = ("md", "log.md", "parts.json", "summary.md", "side-guard.json", "turn-guard.json", STAMP)
FINDINGS_SHOWN = 5
ISO = re.compile(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d)(?:\.\d+)?)?(Z|[+-]\d\d:?\d\d)?")


class Refused(Exception):
    pass


# --- time -------------------------------------------------------------------------------------------------------

def parse_iso(text):
    """Seconds since the epoch of an ISO time (no zone = UTC), or None."""
    found = ISO.fullmatch(text.strip())
    if not found:
        return None
    year, month, day, hour, minute, second, zone = found.groups()
    import calendar
    try:
        stamp = calendar.timegm((int(year), int(month), int(day), int(hour), int(minute), int(second or 0)))
    except (ValueError, OverflowError):
        return None
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        stamp -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return stamp


def iso(stamp):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


# --- files ------------------------------------------------------------------------------------------------------

def write_atomic(path, data):
    """`data` (bytes) into `path` through a temp file in the same folder and os.replace."""
    folder = os.path.dirname(path)
    handle, temp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temp, path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def ending_of(line):
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith((b"\n", b"\r")):
        return line[-1:]
    return b""


# --- the Items section ------------------------------------------------------------------------------------------

class Document:
    """A state file as raw lines; only the lines of the `## Items` section are parsed."""

    def __init__(self, data):
        self.lines = data.splitlines(keepends=True)
        self.start, self.end = self._section()

    def _section(self):
        """(heading index, end index) of the one `## Items` section, (None, None) without one; headings inside a
        fenced block do not count."""
        found, end, fence = [], None, None
        for index, line in enumerate(self.lines):
            bare = line.rstrip(b"\r\n")
            opener = FENCE.match(bare)
            if opener:
                fence = None if fence == opener.group(1) else (fence or opener.group(1))
                continue
            if fence is not None or not HEADING.match(bare):
                continue
            if bare.rstrip() == ITEMS_HEADING:
                found.append(index)
            elif found and end is None:
                end = index
        if len(found) > 1:
            raise Refused("two ## Items sections")
        if not found:
            return None, None
        return found[0], len(self.lines) if end is None else end

    def items(self):
        """[(index, match, [detail indexes])] of the section's item lines; a detail line is an indented, non-blank
        line right below the item or below another of its detail lines."""
        result, current = [], None
        if self.start is None:
            return result
        for index in range(self.start + 1, self.end):
            try:
                text = self.lines[index].rstrip(b"\r\n").decode("utf-8")
            except UnicodeDecodeError:
                current = None
                continue
            match = ITEM.fullmatch(text)
            if match:
                current = (index, match, [])
                result.append(current)
            elif current is not None and text[:1] in (" ", "\t") and text.strip():
                current[2].append(index)
            else:
                current = None
        return result

    def find(self, ident):
        for entry in self.items():
            if entry[1].group("id") == ident:
                return entry
        return None

    def newline(self):
        if self.start is not None:
            return ending_of(self.lines[self.start]) or b"\n"
        return (ending_of(self.lines[0]) if self.lines else b"") or b"\n"

    def data(self):
        return b"".join(self.lines)


def render(fields):
    return "- " + SEP.join([fields["id"], fields["status"], fields["pr"], fields["branch"], fields["repo"],
                            fields["title"]])


def fields_of(match):
    return {key: match.group(key) for key in ("id", "status", "pr", "branch", "repo", "title")}


def check_text(text, what):
    if CONTROL.search(text):
        raise Refused("control character in %s" % what)
    if not text.strip():
        raise Refused("empty %s" % what)
    return text


def check_id(ident):
    if not ITEM_ID.fullmatch(ident):
        raise Refused("bad id %s" % ident)
    return ident


def check_branch(branch):
    if not BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".lock", "/", ".")):
        raise Refused("bad branch %s" % branch)
    return branch


def check_pr(number):
    if not PR.fullmatch(number):
        raise Refused("bad pr %s" % number)
    return "#" + number


# --- git and gh -------------------------------------------------------------------------------------------------

def _review_scope():
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "review-scope")
    if folder not in sys.path:
        sys.path.insert(1, folder)
    import review_scope
    return review_scope


def git_out(cwd, args):
    """stdout text of one git command in `cwd` through review_scope.git, or None when it fails or cannot run."""
    scope = _review_scope()
    try:
        code, out = scope.git(cwd, args)
    except scope.ScopeError:
        return None
    if code != 0:
        return None
    return out.decode("utf-8", "replace")


def repo_of_url(url):
    text = url.strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = re.split(r"[/:]", text)
    if len(parts) < 2:
        return None
    candidate = parts[-2] + "/" + parts[-1]
    return candidate if REPO.fullmatch(candidate) else None


def origin_repo(cwd):
    url = git_out(cwd, ["remote", "get-url", "origin"])
    return repo_of_url(url) if url else None


def gh_command(cwd):
    """[gh] from the absolute PATH entries outside `cwd` (review_scope.find_program), or None."""
    scope = _review_scope()
    try:
        return [scope.find_program("gh", cwd, (".exe",) if os.name == "nt" else ("",))]
    except scope.ScopeError:
        return None


def gh_state(command, repo, number, timeout):
    """'MERGED' / another state / None (failed) / 'timeout'. stdout goes to a temp file, so a child that keeps a
    pipe open cannot hold the run past its bound."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(command + ["pr", "view", number, "--repo", repo, "--json", "state"],
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL, env=env)
        except OSError:
            return None
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return "timeout"
        if code != 0:
            return None
        out.seek(0)
        try:
            answer = json.loads(out.read().decode("utf-8", "replace"))
        except ValueError:
            return None
    state = answer.get("state") if isinstance(answer, dict) else None
    return state if isinstance(state, str) else None


# --- session paths ----------------------------------------------------------------------------------------------

class Context:
    def __init__(self, state_dir, session, projects_dir, now, out, deadline=float("inf"), clock=time.monotonic):
        self.state_dir, self.session, self.projects_dir, self.now, self.out = state_dir, session, projects_dir, now, out
        self.deadline, self.clock = deadline, clock

    def out_of_time(self):
        """True once the clean budget is spent; asked only between units of work, never inside one."""
        return self.clock() >= self.deadline

    def say(self, line):
        self.out.write(line.encode("utf-8") + b"\n")

    def state_path(self):
        if not self.session:
            raise Refused("no session id")
        return os.path.join(self.state_dir, self.session + ".md")


def load(ctx):
    path = ctx.state_path()
    try:
        data = read_bytes(path)
    except FileNotFoundError:
        raise Refused("no state file %s; write it first" % path)
    return path, Document(data)


# --- item commands ----------------------------------------------------------------------------------------------

def cmd_add(ctx, args, opts):
    ident, title = check_id(args[0]), check_text(args[1], "title")
    branch = check_branch(opts["branch"]) if "branch" in opts else NONE
    if "repo" in opts:
        if not REPO.fullmatch(opts["repo"]):
            raise Refused("bad repo %s" % opts["repo"])
        repo = opts["repo"]
    else:
        repo = origin_repo(os.getcwd()) or NONE
    path, doc = load(ctx)
    if doc.find(ident):
        raise Refused("id exists %s" % ident)
    eol = doc.newline()
    line = render({"id": ident, "status": "open", "pr": NONE, "branch": branch, "repo": repo,
                   "title": title}).encode("utf-8") + eol
    if doc.start is None:
        if doc.lines and not ending_of(doc.lines[-1]):
            doc.lines[-1] += eol
        if doc.lines and doc.lines[-1].strip():
            doc.lines.append(eol)
        doc.lines += [ITEMS_HEADING + eol, line]
    else:
        entries = doc.items()
        last = (entries[-1][2] or [entries[-1][0]])[-1] if entries else doc.start
        if not ending_of(doc.lines[last]):
            doc.lines[last] += eol
        doc.lines.insert(last + 1, line)
    write_atomic(path, doc.data())
    ctx.say(OK + "add " + ident)


def replace_item(doc, entry, fields):
    index = entry[0]
    doc.lines[index] = render(fields).encode("utf-8") + ending_of(doc.lines[index])


def cmd_set(ctx, args, opts):
    ident, status = check_id(args[0]), args[1]
    if status not in STATUSES:
        raise Refused("unknown status %s" % status)
    if status == "done":
        raise Refused("use done %s --pr <N>" % ident)
    pr = check_pr(opts["pr"]) if "pr" in opts else None
    branch = check_branch(opts["branch"]) if "branch" in opts else None
    path, doc = load(ctx)
    entry = doc.find(ident)
    if not entry:
        raise Refused("unknown id %s" % ident)
    fields = fields_of(entry[1])
    fields["status"] = status
    if pr:
        fields["pr"] = pr
    if branch:
        fields["branch"] = branch
    replace_item(doc, entry, fields)
    write_atomic(path, doc.data())
    ctx.say(OK + "set %s %s" % (ident, status))


def cmd_note(ctx, args, opts):
    ident, text = check_id(args[0]), check_text(args[1], "note")
    path, doc = load(ctx)
    entry = doc.find(ident)
    if not entry:
        raise Refused("unknown id %s" % ident)
    eol = doc.newline()
    last = (entry[2] or [entry[0]])[-1]
    if not ending_of(doc.lines[last]):
        doc.lines[last] += eol
    doc.lines.insert(last + 1, ("  " + text).encode("utf-8") + eol)
    write_atomic(path, doc.data())
    ctx.say(OK + "note " + ident)


def cmd_done(ctx, args, opts):
    ident = check_id(args[0])
    if "pr" not in opts:
        raise Refused("done needs --pr <N>")
    pr = check_pr(opts["pr"])
    path, doc = load(ctx)
    entry = doc.find(ident)
    if not entry:
        raise Refused("unknown id %s" % ident)
    details = [doc.lines[index] for index in entry[2]]
    if details:
        log = os.path.join(ctx.state_dir, ctx.session + ".log.md")
        try:
            old = read_bytes(log)
        except FileNotFoundError:
            old = b""
        eol = doc.newline()
        if old and not ending_of(old):
            old += eol
        if old and not old.endswith((b"\n\n", b"\r\n\r\n")):
            old += eol
        last = details[-1]
        if not ending_of(last):
            details[-1] = last + eol
        heading = ("## %s (%s)" % (ident, iso(time.time()))).encode("utf-8") + eol
        write_atomic(log, old + heading + b"".join(details))
    fields = fields_of(entry[1])
    fields.update(status="done", pr=pr)
    replace_item(doc, entry, fields)
    for index in reversed(entry[2]):
        del doc.lines[index]
    write_atomic(path, doc.data())
    ctx.say(OK + "done " + ident)


def cmd_show(ctx, args, opts):
    _path, doc = load(ctx)
    entries = doc.items()
    for entry in entries:
        ctx.say(entry[1].group(0))
    ctx.say(OK + "show %d" % len(entries))


# --- check ------------------------------------------------------------------------------------------------------

def frontmatter(data):
    text = data.decode("utf-8-sig", "replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return values
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("\"'").strip()
    return {}


def read_cache(path):
    try:
        data = json.loads(read_bytes(path).decode("utf-8"))
    except (OSError, ValueError):
        return set()
    merged = data.get("merged") if isinstance(data, dict) else None
    return {entry for entry in merged if isinstance(entry, str)} if isinstance(merged, list) else set()


def findings(ctx):
    """(finding lines, unchecked line or None) of the current session's state file."""
    path, doc = load(ctx)
    found, unchecked = [], None
    entries = [fields_of(entry[1]) for entry in doc.items()]
    active = [fields for fields in entries if fields["status"] != "done"]
    cache_path = os.path.join(ctx.state_dir, "pr-cache.json")
    cache = read_cache(cache_path)
    added = False
    asked = [fields for fields in active if fields["pr"] != NONE and fields["repo"] != NONE]
    command = None
    deadline = time.monotonic() + GH_RUN
    for fields in asked:
        key = "%s#%s" % (fields["repo"], fields["pr"][1:])
        state = "MERGED" if key in cache else None
        if state is None:
            if command is None:
                command = gh_command(os.getcwd()) or []
            if not command:
                unchecked = unchecked or "state: unchecked: gh not found"
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                unchecked = unchecked or "state: unchecked: gh did not answer within %d s" % GH_RUN
                continue
            state = gh_state(command, fields["repo"], fields["pr"][1:], min(GH_CALL, left))
            if state in (None, "timeout"):
                unchecked = unchecked or "state: unchecked: gh gave no answer for %s" % key
                continue
            if state == "MERGED":
                cache.add(key)
                added = True
        if state == "MERGED":
            found.append("state: stale %s: PR %s merged in %s; run done %s --pr %s"
                         % (fields["id"], fields["pr"], fields["repo"], fields["id"], fields["pr"][1:]))
    if added:
        try:
            write_atomic(cache_path, json.dumps({"format": 1, "merged": sorted(cache)}).encode("utf-8"))
        except OSError:
            pass
    with_branch = [fields for fields in active if fields["branch"] != NONE]
    if with_branch:
        cwd = os.getcwd()
        refs = git_out(cwd, ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/"])
        if refs is not None:
            here = origin_repo(cwd)
            names = set()
            for ref in refs.split():
                if ref.startswith("refs/heads/"):
                    names.add(ref[len("refs/heads/"):])
                elif ref.startswith("refs/remotes/") and "/" in ref[len("refs/remotes/"):]:
                    names.add(ref[len("refs/remotes/"):].split("/", 1)[1])
            for fields in with_branch:
                if here and fields["repo"] != NONE and fields["repo"] != here:
                    continue
                if fields["branch"] not in names:
                    found.append("state: stale %s: branch %s is gone" % (fields["id"], fields["branch"]))
    data = doc.data()
    values = frontmatter(data)
    if values.get("safe_to_compact", "").lower() == "false":
        written = parse_iso(values.get("updated", ""))
        if written is None:
            try:
                written = os.stat(path).st_mtime
            except OSError:
                written = ctx.now
        if ctx.now - written > STALE_FALSE:
            found.append("state: stale: safe_to_compact: false since %s (over %d min); set it back to true"
                         % (iso(written), STALE_FALSE // 60))
    return found, unchecked


def cmd_check(ctx, args, opts):
    found, unchecked = findings(ctx)
    for line in found[:FINDINGS_SHOWN]:
        ctx.say(line)
    if unchecked:
        ctx.say(unchecked)
    shown = "" if len(found) <= FINDINGS_SHOWN else ", %d shown" % FINDINGS_SHOWN
    ctx.say(OK + "check %d%s" % (len(found), shown))


# --- clean ------------------------------------------------------------------------------------------------------

def session_of(name, pattern=SESSION_FILE_ID):
    """The session id of a per-session file name, or None for a shared or foreign file."""
    if name in SHARED or name.startswith("."):
        return None
    ident, dot, rest = name.partition(".")
    if not dot or not rest or not pattern.fullmatch(ident):
        return None
    return ident


def regular_files(folder):
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return []
    return [entry for entry in entries if entry.is_file(follow_symlinks=False)]


def transcript_times(projects_dir, sessions):
    """{session: newest mtime of `<projects>/*/<session>.jsonl`} for `sessions`, from one scan of the project
    folders (one stat per matching transcript); a session without one is absent. Raises OSError when a folder
    cannot be read: the caller then cannot tell any session is idle."""
    times = {}
    try:
        folders = list(os.scandir(projects_dir))
    except FileNotFoundError:
        return times
    for folder in folders:
        if not folder.is_dir():
            continue
        try:
            entries = list(os.scandir(folder.path))
        except FileNotFoundError:
            continue
        for entry in entries:
            session = entry.name[:-len(".jsonl")] if entry.name.endswith(".jsonl") else None
            if session not in sessions:
                continue
            try:
                mtime = os.stat(entry.path).st_mtime
            except FileNotFoundError:
                continue
            times[session] = max(times.get(session, 0), mtime)
    return times


def take_lock(ctx):
    """The lock text when this run holds `.clean.lock`, else None."""
    path = os.path.join(ctx.state_dir, ".clean.lock")
    text = "%d %s\n" % (os.getpid(), iso(ctx.now))
    for attempt in (0, 1):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if attempt:
                return None
            try:
                held = read_bytes(path).decode("utf-8", "replace").split()
                since = parse_iso(held[1]) if len(held) > 1 else None
                if since is None:
                    since = os.stat(path).st_mtime
            except OSError:
                return None
            if ctx.now - since <= LOCK_TAKEOVER:
                return None
            try:
                os.remove(path)
            except OSError:
                return None
            continue
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        return text
    return None


def drop_lock(ctx, text):
    path = os.path.join(ctx.state_dir, ".clean.lock")
    try:
        if read_bytes(path).decode("utf-8", "replace") == text:
            os.remove(path)
    except OSError:
        pass


def archive_idle(ctx, archive):
    """(files moved, stopped by the budget). A session's files move one `os.replace` each, so a kill or an
    OSError can split a group; `<sid>.md` moves first, so a split group always has it archived. The next clean
    moves the rest while the session stays idle, and a restore brings the archived part back."""
    groups = {}
    for entry in regular_files(ctx.state_dir):
        session = session_of(entry.name)
        if session and session != ctx.session:
            groups.setdefault(session, []).append(entry)
    groups = {session: entries for session, entries in groups.items()
              if any(entry.name[len(session) + 1:] in SUFFIXES for entry in entries)}
    if not groups:
        return 0, False
    try:
        transcripts = transcript_times(ctx.projects_dir, groups)
    except OSError:
        return 0, False
    moved = 0
    for session, entries in sorted(groups.items()):
        if ctx.out_of_time():
            return moved, True
        try:
            # a fresh lstat, not the listing's cached times: a restore may have renewed the stamp since
            newest = max(os.lstat(entry.path).st_mtime for entry in entries)
        except OSError:
            continue
        if ctx.now - max(newest, transcripts.get(session, 0)) <= IDLE_DAYS * DAY:
            continue
        os.makedirs(archive, exist_ok=True)
        if any(os.path.lexists(os.path.join(archive, entry.name)) for entry in entries):
            continue
        # The state file moves first: a group split by a later failure has `<sid>.md` archived, so the session's
        # next entry point (restore_on_miss) brings it back; its own failure moves nothing.
        for entry in sorted(entries, key=lambda entry: entry.name != session + ".md"):
            target = os.path.join(archive, entry.name)
            try:
                os.replace(entry.path, target)
                os.utime(target, (ctx.now, ctx.now))
            except OSError:
                break
            moved += 1
    return moved, False


def delete_old(ctx, archive):
    """(files deleted, stopped by the budget)."""
    deleted = 0
    for entry in regular_files(archive):
        try:
            if ctx.now - entry.stat(follow_symlinks=False).st_mtime <= ARCHIVE_DAYS * DAY:
                continue
        except OSError:
            continue
        if ctx.out_of_time():
            return deleted, True
        try:
            os.remove(entry.path)
        except OSError:
            continue
        deleted += 1
    return deleted, False


def rotate_logs(ctx, archive):
    """(logs rotated, stopped by the budget)."""
    rotated = 0
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ctx.now))
    for entry in regular_files(ctx.state_dir):
        own = entry.name == ctx.session + ".log.md"   # this session's own log, whatever its id's shape
        session = ctx.session if own else session_of(entry.name)
        if not session or entry.name != session + ".log.md":
            continue
        try:
            if entry.stat(follow_symlinks=False).st_size <= LOG_MAX:
                continue
        except OSError:
            continue
        if ctx.out_of_time():
            return rotated, True
        try:
            data = read_bytes(entry.path)
        except OSError:
            continue
        cut = len(data) - LOG_KEEP
        if data[cut - 1:cut] != b"\n":
            after = data.find(b"\n", cut)
            cut = after + 1 if 0 <= after < len(data) - 1 else data.rfind(b"\n", 0, cut) + 1
        if cut <= 0 or cut >= len(data):
            continue
        target = os.path.join(archive, "%s.log.%s.md" % (session, stamp))
        if os.path.lexists(target):
            continue
        try:
            os.makedirs(archive, exist_ok=True)
            write_atomic(target, data[:cut])
            write_atomic(entry.path, data[cut:])
        except OSError:
            continue
        rotated += 1
    return rotated, False


def clean(ctx):
    """(moved, deleted, rotated, stopped) or None when another run holds the lock. One time budget
    (`ctx.deadline`) covers all three steps; once it is spent, clean stops before the next unit (a session's
    group, a delete, a rotation) and drops the lock. A hook killed at its timeout cannot drop the lock: the
    next run then skips until the lock is older than LOCK_TAKEOVER."""
    if not ctx.session:
        raise Refused("no session id")
    lock = take_lock(ctx)
    if lock is None:
        return None
    try:
        archive = os.path.join(ctx.state_dir, "archive")
        moved, stopped = archive_idle(ctx, archive)
        deleted = rotated = 0
        if not stopped:
            deleted, stopped = delete_old(ctx, archive)
        if not stopped:
            rotated, stopped = rotate_logs(ctx, archive)
        return moved, deleted, rotated, stopped
    finally:
        drop_lock(ctx, lock)


def cmd_clean(ctx, args, opts):
    result = clean(ctx)
    if result is None:
        ctx.say(OK + "clean skipped: locked")
    else:
        ctx.say(OK + "clean moved %d deleted %d rotated %d" % result[:3]
                + ("; stopped at the time budget" if result[3] else ""))


# --- hook -------------------------------------------------------------------------------------------------------

def restore_files(state_dir, session, now):
    """Moves `session`'s archived files back to `state_dir`, never over a present file; returns the count. The
    stamp `<session>.restored` is written first (created or its mtime set to `now`), so a clean running in
    parallel either sees the fresh stamp or does not see the restored files yet; an archived stamp is dropped,
    or it would block the session's next archiving."""
    archive = os.path.join(state_dir, "archive")
    moves, old_stamps = [], []
    for entry in regular_files(archive):
        if session_of(entry.name, SESSION_ID) != session:
            continue
        rest = entry.name[len(session) + 1:]
        if rest == STAMP:
            old_stamps.append(entry.path)
        elif rest in SUFFIXES and not os.path.lexists(os.path.join(state_dir, entry.name)):
            moves.append(entry)
    if not moves:
        return 0
    stamp = os.path.join(state_dir, session + "." + STAMP)
    with open(stamp, "ab"):
        pass
    os.utime(stamp, (now, now))
    for path in old_stamps:
        try:
            os.remove(path)
        except OSError:
            pass
    restored = 0
    for entry in moves:
        target = os.path.join(state_dir, entry.name)
        if os.path.lexists(target):
            continue
        try:
            os.replace(entry.path, target)   # keeps the archive time: compact_guard reads staleness from mtime
        except OSError:
            continue
        restored += 1
    return restored


def restore_on_miss(state_dir, session, now):
    """restore_files when `<session>.md` is missing and `archive/<session>.md` exists, else 0. Costs one stat
    while the state file is present; shared with context_guard.py, which calls it on every prompt."""
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return 0
    if os.path.lexists(os.path.join(state_dir, session + ".md")):
        return 0
    if not os.path.isfile(os.path.join(state_dir, "archive", session + ".md")):
        return 0
    return restore_files(state_dir, session, now)


def restore(ctx):
    return restore_files(ctx.state_dir, ctx.session, ctx.now)


def hook(ctx, stdin):
    try:
        event = json.loads(stdin.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(event, dict):
        return
    source = event.get("source")
    if "session_id" in event:
        session = event["session_id"]
        if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
            return
        ctx.session = session
    if not ctx.session or source not in ("startup", "clear", "compact", "resume"):
        return
    lines = []
    restored = restore(ctx) if source == "resume" else restore_on_miss(ctx.state_dir, ctx.session, ctx.now)
    if restored:
        lines.append("state: restored %d archived files of this session to %s" % (restored, ctx.state_dir))
    if source == "startup":
        result = clean(ctx)
        if result and any(result):
            lines.append("state: clean moved %d idle files to archive/, deleted %d, rotated %d logs" % result[:3]
                         + ("; stopped at the time budget, the next start goes on" if result[3] else ""))
    else:
        if os.path.isfile(ctx.state_path()):
            found, unchecked = findings(ctx)
            room = FINDINGS_SHOWN - len(lines)
            lines += found[:room]
            if unchecked and len(found) < room:
                lines.append(unchecked)
            if len(found) > room:
                lines.append("state: %d findings, %d shown" % (len(found), room))
    for line in lines[:6]:
        ctx.say(line)


# --- main -------------------------------------------------------------------------------------------------------

COMMANDS = {"add": (2, ("branch", "repo")), "set": (2, ("pr", "branch")), "note": (2, ()), "done": (1, ("pr",)),
            "show": (0, ()), "check": (0, ("now",)), "clean": (0, ("now",)), "hook": (0, ("now",))}
HANDLERS = {"add": cmd_add, "set": cmd_set, "note": cmd_note, "done": cmd_done, "show": cmd_show,
            "check": cmd_check, "clean": cmd_clean}
RESTORE_FIRST = ("check", "add", "set", "done")


def parse(argv):
    glob_opts, rest = {}, list(argv)
    while rest and rest[0] in ("--state-dir", "--session", "--projects-dir"):
        if len(rest) < 2:
            raise Refused("%s needs a value" % rest[0])
        glob_opts[rest[0][2:]] = rest[1]
        rest = rest[2:]
    if not rest:
        raise Refused("no command")
    command, rest = rest[0], rest[1:]
    if command not in COMMANDS:
        raise Refused("unknown command %s" % command)
    count, allowed = COMMANDS[command]
    args, opts = [], {}
    while rest:
        word = rest.pop(0)
        if word.startswith("--") and len(word) > 2:
            name = word[2:]
            if name not in allowed:
                raise Refused("unknown option %s" % word)
            if not rest:
                raise Refused("%s needs a value" % word)
            opts[name] = rest.pop(0)
        else:
            args.append(word)
    if len(args) != count:
        raise Refused("%s takes %d arguments" % (command, count))
    return glob_opts, command, args, opts


def config_dir(environ):
    return environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def main(argv, environ=None, stdin=None, out=None, started=None):
    """`started`: the monotonic time the clean budget counts from (the process start when run as a script)."""
    started = time.monotonic() if started is None else started
    environ = os.environ if environ is None else environ
    out = out if out is not None else sys.stdout.buffer
    is_hook = False
    try:
        glob_opts, command, args, opts = parse(argv)
        is_hook = command == "hook"
        session = glob_opts.get("session") or environ.get("CLAUDE_CODE_SESSION_ID") or None
        if session and not SESSION_ID.fullmatch(session):
            if command == "hook":
                session = None
            else:
                raise Refused("bad session id")
        now = time.time()
        if "now" in opts:
            now = parse_iso(opts["now"])
            if now is None:
                raise Refused("bad --now %s" % opts["now"])
        state_dir = os.path.abspath(glob_opts.get("state-dir") or os.path.join(config_dir(environ), "state"))
        projects = os.path.abspath(glob_opts.get("projects-dir") or os.path.join(config_dir(environ), "projects"))
        ctx = Context(state_dir, session, projects, now, out, deadline=started + CLEAN_BUDGET)
        if command == "hook":
            data = stdin if stdin is not None else sys.stdin.buffer.read()
            try:
                hook(ctx, data)
            except Exception:
                pass
            return 0
        if command in RESTORE_FIRST:
            restored = restore_on_miss(state_dir, ctx.session, now)
            if restored:
                ctx.say(OK + "restored %d archived files of this session" % restored)
        HANDLERS[command](ctx, args, opts)
        return 0
    except Refused as exc:
        if is_hook:
            return 0
        out.write((REFUSED + str(exc)).encode("utf-8") + b"\n")
        return 2
    except Exception as exc:   # never a traceback: a caller reads the one line
        if is_hook:
            return 0
        out.write((REFUSED + "error: %s: %s" % (type(exc).__name__, exc)).encode("utf-8", "replace") + b"\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], started=STARTED))
