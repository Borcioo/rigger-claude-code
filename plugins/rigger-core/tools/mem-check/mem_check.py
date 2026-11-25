"""Memory headroom gate before a heavy job (a build, a test suite, an agent that runs one), on every system.

  python mem_check.py [--min-free-gb N] [--ceiling-gb N] [--json]

Prints one line `mem-check: <measure>, headroom <n> GiB (min <m> GiB), max heavy agents <k>: OK|BUSY` and
exits 0 (headroom at or above the minimum), 1 (below it: wait and re-check) or 2 (no usable reading: never read
as free; run one heavy job at a time). Every value is GiB (1024^3 bytes), rounded half up.

Readings:
- Windows: commit charge from GetPerformanceInfo (ctypes). headroom = ceiling - committed, where the ceiling is
  the largest SystemCommitLimit of the System log's Event 2004 entries (Resource-Exhaustion-Detector, read by
  wevtutil), because the system-managed pagefile grows and shrinks; with no entries or a read error the
  ceiling is the current commit limit and a line says so. `--ceiling-gb` (1-4096) replaces it. The line also
  names the commit limit and the available RAM.
- Linux: /proc/meminfo, the smaller of MemAvailable and CommitLimit - Committed_AS.
- macOS: vm_stat free + inactive + purgeable pages times `sysctl -n hw.pagesize`.

Limits: `load.min_free_gb` (0-1024) and `load.max_heavy_agents` (1-8) from config levels 1-4; when no level
sets a key, the default of this system: Windows min(30 GiB, 25% of the ceiling), Linux and macOS 4 GiB; 2
heavy agents. The project level (`.claude/rigger.json`) may only make a limit stricter (a higher minimum,
fewer agents); a looser value is ignored with one line. `--min-free-gb` (0-1024) replaces the minimum.

`--system windows|linux|macos`, `--reading-json` and `--event-limits-json` are the test seams: the reading as
JSON (Windows: CommittedBytes, CommitLimit, AvailableBytes; Linux: {"meminfo": text}; macOS: {"vm_stat": text,
"pagesize": text}) and the Event 2004 SystemCommitLimit bytes as a JSON array. Output is UTF-8 with LF ends.
"""

import json
import os
import re
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_ROOT = os.path.dirname(os.path.dirname(HERE))
ASSEMBLE = os.path.join(os.path.dirname(HERE), "assemble")

GIB = 1024 ** 3
KIB = 1024
SYSTEMS = ("windows", "linux", "macos")
DEFAULT_AGENTS = 2
UNIX_MIN_GB = 4
WINDOWS_CAP_GB, WINDOWS_SHARE = 30, 0.25
MIN_RANGE, CEILING_RANGE = (0, 1024), (1, 4096)
MEMINFO = "/proc/meminfo"
COMMAND_TIMEOUT = 10
DETECTOR = "Microsoft-Windows-Resource-Exhaustion-Detector"
EVENT_QUERY = "*[System[Provider[@Name='%s'] and (EventID=2004)]]" % DETECTOR
WINDOWS_FIELDS = ("CommittedBytes", "CommitLimit", "AvailableBytes")
OPTIONS = ("--system", "--reading-json", "--event-limits-json", "--min-free-gb", "--ceiling-gb")


class NoReading(Exception):
    pass


class Refused(Exception):
    pass


# --- numbers ----------------------------------------------------------------------------------------------------

def _half_up(value, places):
    return Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def gib(nbytes, places=1):
    return "%s GiB" % _half_up(Decimal(nbytes) / GIB, places)


def number(value):
    """A limit as the line shows it: at most two decimals, no trailing zeros (30, 9.5, 4.75)."""
    text = format(_half_up(value, 2), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- Windows ----------------------------------------------------------------------------------------------------

def performance_info():
    """GetPerformanceInfo's PERFORMANCE_INFORMATION (page counts and the page size); raises OSError on failure."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                    ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                    ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                    ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                    ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                    ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD),
                    ("ThreadCount", wintypes.DWORD)]

    info = Info()
    info.cb = ctypes.sizeof(info)
    if not ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo(ctypes.byref(info), info.cb):
        raise OSError(ctypes.get_last_error(), "GetPerformanceInfo failed")
    return info


def windows_reading(seam):
    """{field: bytes} of WINDOWS_FIELDS; raises NoReading."""
    if seam is None:
        try:
            info = performance_info()
        except OSError as error:
            raise NoReading("GetPerformanceInfo: %s" % error)
        page = info.PageSize
        values = {"CommittedBytes": info.CommitTotal * page, "CommitLimit": info.CommitLimit * page,
                  "AvailableBytes": info.PhysicalAvailable * page}
    else:
        values = _seam_object(seam)
    for name in WINDOWS_FIELDS:
        if name not in values:
            raise NoReading("missing %s" % name)
        if not _is_number(values[name]) or values[name] < 0:
            raise NoReading("bad %s" % name)
    if values["CommitLimit"] <= 0:
        raise NoReading("CommitLimit is 0")
    return values


def event_limits(seam, run):
    """The SystemCommitLimit bytes of every Event 2004 entry; raises ValueError or OSError when unreadable."""
    if seam is not None:
        limits = json.loads(seam)
        if not isinstance(limits, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in limits):
            raise ValueError("not a list of byte counts")
        return limits
    try:
        done = run(["wevtutil", "qe", "System", "/q:" + EVENT_QUERY, "/f:xml"], stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise OSError("wevtutil timed out")
    if done.returncode != 0:
        raise OSError("wevtutil exited %d: %s" % (done.returncode, _one_line(done.stderr)))
    text = done.stdout.decode("utf-8", "replace")
    return [int(value) for value in re.findall(r"<SystemCommitLimit>\s*(\d+)\s*</SystemCommitLimit>", text)]


def windows(options, run):
    """(measure, headroom bytes, default minimum, extra lines, trailing measure)."""
    values = windows_reading(options.get("reading-json"))
    committed, limit = values["CommittedBytes"], values["CommitLimit"]
    lines = []
    if "ceiling-gb" in options:
        ceiling, source = options["ceiling-gb"] * GIB, "--ceiling-gb override"
    else:
        try:
            limits = event_limits(options.get("event-limits-json"), run)
            reason = None if limits else "no Event 2004 entries"
        except (ValueError, OSError) as error:
            limits, reason = [], "event log read failed: %s" % _one_line(str(error))
        if limits:
            ceiling, source = max(limits), "max of %d Event 2004 entries" % len(limits)
        else:
            ceiling, source = limit, "current CommitLimit, fallback"
            lines.append("mem-check: ceiling falls back to the current CommitLimit (%s)" % reason)
    default = min(float(WINDOWS_CAP_GB), WINDOWS_SHARE * ceiling / GIB)
    measure = "committed %s, limit %s, ceiling %s (%s)" % (gib(committed), gib(limit), gib(ceiling, 2), source)
    return measure, ceiling - committed, default, lines, ", available RAM %s" % gib(values["AvailableBytes"])


# --- Linux ------------------------------------------------------------------------------------------------------

def linux(options, meminfo_path):
    seam = options.get("reading-json")
    if seam is None:
        try:
            with open(meminfo_path, "rb") as handle:
                text = handle.read().decode("utf-8", "replace")
        except OSError as error:
            raise NoReading("cannot read %s: %s" % (meminfo_path, error.strerror or error))
    else:
        text = _seam_object(seam).get("meminfo")
        if not isinstance(text, str):
            raise NoReading("no meminfo text")
    fields = {}
    for line in text.splitlines():
        name, colon, rest = line.partition(":")
        if colon:
            fields[name.strip()] = rest.split()
    counts = {}
    for name in ("MemAvailable", "CommitLimit", "Committed_AS"):
        if name not in fields:
            raise NoReading("/proc/meminfo has no %s" % name)
        words = fields[name]
        if not words or not words[0].isdigit() or words[1:] not in ([], ["kB"]):
            raise NoReading("/proc/meminfo %s is not a number of kB" % name)
        counts[name] = int(words[0]) * KIB
    available = counts["MemAvailable"]
    commit = counts["CommitLimit"] - counts["Committed_AS"]
    measure = "available %s, commit headroom %s" % (gib(available), gib(commit))
    return measure, min(available, commit), float(UNIX_MIN_GB), [], ""


# --- macOS ------------------------------------------------------------------------------------------------------

def _command(run, argv):
    try:
        done = run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NoReading("%s: %s" % (argv[0], _one_line(str(error))))
    if done.returncode != 0:
        raise NoReading("%s exited %d" % (argv[0], done.returncode))
    return done.stdout.decode("utf-8", "replace")


def macos(options, run):
    seam = options.get("reading-json")
    if seam is None:
        text, size = _command(run, ["vm_stat"]), _command(run, ["sysctl", "-n", "hw.pagesize"])
    else:
        data = _seam_object(seam)
        text, size = data.get("vm_stat"), data.get("pagesize")
        if not isinstance(text, str) or not isinstance(size, str):
            raise NoReading("no vm_stat or pagesize text")
    if not size.strip().isdigit() or int(size) <= 0:
        raise NoReading("bad hw.pagesize %s" % _one_line(size))
    page = int(size)
    pages = {name: int(count) for name, count in
             re.findall(r"(?m)^Pages (free|inactive|purgeable):\s+(\d+)\.\s*$", text)}
    for name in ("free", "inactive"):
        if name not in pages:
            raise NoReading("vm_stat has no Pages %s" % name)
    free, inactive, purgeable = (pages.get(name, 0) * page for name in ("free", "inactive", "purgeable"))
    measure = "free %s, inactive %s, purgeable %s" % (gib(free), gib(inactive), gib(purgeable))
    return measure, free + inactive + purgeable, float(UNIX_MIN_GB), [], ""


# --- config -----------------------------------------------------------------------------------------------------

def _only_load(data):
    return {"load": data["load"]} if isinstance(data, dict) and "load" in data else {}


def limits(environ, core_root, default_min):
    """(minimum GiB, heavy agents, lines) from config levels 1-4 (D80); the defaults when the config does not
    load. The project level only tightens."""
    if ASSEMBLE not in sys.path:
        sys.path.insert(0, ASSEMBLE)
    try:
        from pathlib import Path
        import config
        import discover
        config_dir = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        project_dir = environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        paths = {1: str(config.defaults_path(Path(core_root))),
                 3: os.path.join(config_dir, "rigger", "config.json"),
                 4: os.path.join(project_dir, ".claude", "rigger.json")}
        found = discover.discover(Path(config_dir), Path(project_dir), environ, Path(core_root))
        levels = {level: _only_load(config.load_level(Path(path), level)[0]) for level, path in paths.items()}
        company = [(m.name, _only_load(m.config)) for m in found.modules
                   if m.layer == "company" and isinstance(m.config, dict)]
        resolved = config.resolve(levels[1], company, levels[3], levels[4], paths)
    except Exception:   # a config that does not load keeps the defaults
        return default_min, DEFAULT_AGENTS, []
    lines = list(resolved.notices)
    below, project = {}, {}
    for source, data in resolved.levels:
        block = data.get("load") if isinstance(data.get("load"), dict) else {}
        if source.startswith("L4:"):
            project = block
        else:
            below = config.merge(below, block)
    minimum = below.get("min_free_gb")
    minimum = default_min if minimum is None else minimum
    agents = below.get("max_heavy_agents")
    agents = DEFAULT_AGENTS if agents is None else agents
    for key, base, stricter in (("min_free_gb", minimum, lambda new, old: new >= old),
                                ("max_heavy_agents", agents, lambda new, old: new <= old)):
        value = project.get(key)
        if value is None:
            continue
        if stricter(value, base):
            if key == "min_free_gb":
                minimum = value
            else:
                agents = value
        else:
            lines.append("mem-check: project config %s load.%s %s is looser than %s; ignored"
                         % (paths[4], key, number(value), number(base)))
    return minimum, agents, lines


# --- main -------------------------------------------------------------------------------------------------------

def _one_line(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return " ".join(str(text).split())[:200]


def _seam_object(seam):
    try:
        data = json.loads(seam)
    except ValueError as error:
        raise NoReading("bad --reading-json: %s" % error)
    if not isinstance(data, dict):
        raise NoReading("--reading-json is not an object")
    return data


def _range(text, low, high, name):
    try:
        value = float(text)
    except ValueError:
        raise Refused("%s %s is not a number" % (name, _one_line(text)))
    if not low <= value <= high:   # also refuses nan
        raise Refused("%s %s is outside %d-%d" % (name, _one_line(text), low, high))
    return value


def parse(argv):
    options = {}
    rest = list(argv)
    while rest:
        word = rest.pop(0)
        if word == "--json":
            options["json"] = True
        elif word in OPTIONS:
            if not rest:
                raise Refused("%s needs a value" % word)
            options[word[2:]] = rest.pop(0)
        else:
            raise Refused("unknown argument %s" % _one_line(word))
    if "min-free-gb" in options:
        options["min-free-gb"] = _range(options["min-free-gb"], MIN_RANGE[0], MIN_RANGE[1], "--min-free-gb")
    if "ceiling-gb" in options:
        options["ceiling-gb"] = _range(options["ceiling-gb"], CEILING_RANGE[0], CEILING_RANGE[1], "--ceiling-gb")
    if "system" in options and options["system"] not in SYSTEMS:
        raise Refused("--system must be one of %s" % ", ".join(SYSTEMS))
    return options


def this_system():
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return None


def main(argv, environ=None, out=None, core_root=CORE_ROOT, run=subprocess.run, meminfo_path=MEMINFO):
    environ = os.environ if environ is None else environ
    out = out if out is not None else sys.stdout.buffer

    def say(line):
        out.write(line.encode("utf-8") + b"\n")

    try:
        options = parse(argv)
    except Refused as error:
        say("mem-check: refused: %s" % error)
        return 2
    system = options.get("system") or this_system()
    as_json = options.get("json", False)
    try:
        if system == "windows":
            measure, headroom, default, lines, trailing = windows(options, run)
        elif system == "linux":
            measure, headroom, default, lines, trailing = linux(options, meminfo_path)
        elif system == "macos":
            measure, headroom, default, lines, trailing = macos(options, run)
        else:
            raise NoReading("no reading on %s" % sys.platform)
    except NoReading as error:
        if as_json:
            say(json.dumps({"system": system, "state": "NO_READING", "reason": str(error)}))
        else:
            say("mem-check: no reading (%s)" % error)
        return 2
    minimum, agents, notices = limits(environ, core_root, default)
    if "min-free-gb" in options:
        minimum = options["min-free-gb"]
    busy = headroom < minimum * GIB
    state = "BUSY" if busy else "OK"
    verdict = "mem-check: %s, headroom %s (min %s GiB)%s, max heavy agents %d: %s" % (
        measure, gib(headroom, 2), number(minimum), trailing, agents, state)
    if as_json:
        say(json.dumps({"system": system, "state": state, "headroom_bytes": int(headroom), "min_free_gb": minimum,
                        "max_heavy_agents": agents, "notices": notices + lines, "line": verdict}))
    else:
        for line in notices + lines:
            say(line)
        say(verdict)
    return 1 if busy else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
