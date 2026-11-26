"""Downloads, verifies and places the pinned shellwise binary.

    python shellwise_install.py --session-start   prints at most one line; starts a detached --child
    python shellwise_install.py --child           the download itself, silent

Layout under the plugin data folder (`CLAUDE_PLUGIN_DATA`, or `--data`):
`shellwise/v<ver>/<target>/shellwise[.exe]`, `shellwise/current` (one line, the path),
`shellwise/status.json`, `shellwise/lock` (pid and start time) and `shellwise/announced` (the last
ready line printed, so it is printed once).

Override: `shell_guard.shellwise_path` (personal config, level 3), read through rigger-core's config module
found by its installPath; a config or core that cannot be read changes nothing while an override is on record,
and leaves the pin in use otherwise. An accepted override is what `current` names (status `override`); a refused
one leaves `current` absent, so the launcher runs nothing, never the downloaded binary.

Trust: only the SHA-256 committed in pin.json is compared, never a hash fetched with the archive;
only the one expected member is streamed out; the binary runs (`--version`) only after the hash
matched; `current` and the ready status are written only after both checks passed.
"""

import argparse
import collections
import hashlib
import importlib
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BASE = "https://github.com/Borcioo/shellwise/releases/download/v{version}/"
LOCK_STALE = 600.0   # seconds: an older lock is taken over
VERSION_BOUND = 10.0
OVERRIDE_BOUND = 3.0
FETCH_TIMEOUT = 60
TARGETS = {
    ("win32", "AMD64"): "x86_64-pc-windows-msvc",
    ("linux", "x86_64"): "x86_64-unknown-linux-musl",   # static: no glibc version needed
    ("linux", "aarch64"): "aarch64-unknown-linux-musl",
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("darwin", "x86_64"): "x86_64-apple-darwin",
}
BINARY_NAMES = ("shellwise", "shellwise.exe")
VERSION_LINE = re.compile(r"shellwise (\d+\.\d+\.\d+)")
NO_BINARY = "shellwise: no binary (%s); commands run unguarded"
UNREADABLE = "shellwise: override not read (%s); nothing changed"
UNREAD_PIN = "shellwise: override not read (%s); %s"

Status = collections.namedtuple("Status", "state version target path reason")


class Refused(Exception):
    """A check refused the download or the binary; the message is the reason."""


class Unreadable(Exception):
    """The override cannot be known (a broken personal config or rigger-core); the message is the reason."""


def host_target(plat=None, machine=None):
    plat = sys.platform if plat is None else plat
    machine = platform.machine() if machine is None else machine
    return TARGETS.get((plat, machine))


def exe_name(target):
    return "shellwise.exe" if target.endswith("-windows-msvc") else "shellwise"


def _root(data_dir):
    return os.path.join(data_dir, "shellwise")


def _binary_path(root, version, target):
    return os.path.join(root, "v" + version, target, exe_name(target))


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _read_text(path):
    raw = _read_bytes(path)
    try:
        return None if raw is None else raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _write_atomic(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_status(root, version, target, state, reason=""):
    row = {"version": version, "target": target, "state": state, "reason": reason,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_atomic(os.path.join(root, "status.json"), json.dumps(row).encode("utf-8"))


def _read_status(root):
    try:
        row = json.loads(_read_text(os.path.join(root, "status.json")) or "")
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def _one_line(text):
    return " ".join(str(text).split())


def _numbers(version):
    return tuple(int(part) for part in version.split("."))


# --- running a binary -------------------------------------------------------------------------

def _kill(proc):
    if os.name != "nt":   # a script's own children (`sleep`) are in its session: end them all
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


def run_version(path, timeout, launch=None):
    """`<path> --version` within `timeout` seconds; its output, stripped. Output goes to a file, never a
    pipe, so a grandchild that keeps the pipe open cannot hold the caller past the bound."""
    argv = (launch(path) if launch else [path]) + ["--version"]
    kwargs = {"stdin": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(argv, stdout=out, **kwargs)
        except OSError as exc:
            raise Refused("--version could not run: %s" % _one_line(exc))
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            raise Refused("--version timed out after %g s" % timeout)
        out.seek(0)
        text = out.read(4096).decode("utf-8", "replace")
    return text.replace("\r\n", "\n").strip()


def _check_override(path, pin, target, launch):
    pinned = pin["version"]

    def refused(reason):
        return Status("failed", pinned, target or "", "", "override %s: %s" % (path, reason))

    if not os.path.isfile(path):
        return refused("missing")
    try:
        text = run_version(path, OVERRIDE_BOUND, launch)
    except Refused as exc:
        return refused(str(exc))
    match = VERSION_LINE.fullmatch(text)
    if not match:
        return refused("--version printed no shellwise version")
    own = match.group(1)
    if _numbers(own) < _numbers(pinned):
        return refused("version %s below the pin %s" % (own, pinned))
    return Status("override", own, target or "", os.path.abspath(path), "")


# --- the lock ---------------------------------------------------------------------------------

def _lock_held(root, clock):
    """True while a lock younger than LOCK_STALE exists. An unreadable lock counts from its mtime."""
    path = os.path.join(root, "lock")
    raw = _read_bytes(path)
    if raw is None:
        return False
    try:
        start = float(json.loads(raw.decode("utf-8"))["start"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        try:
            start = min(os.stat(path).st_mtime, clock())   # an mtime may lead the clock by a tick
        except OSError:
            return False
    return 0 <= clock() - start <= LOCK_STALE   # a start in the future is a broken clock, not a holder


def _acquire(root, clock):
    """The lock's bytes when this process holds it, else None."""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "lock")
    mine = json.dumps({"pid": os.getpid(), "start": clock()}).encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    except FileExistsError:
        if _lock_held(root, clock):
            return None
        _write_atomic(path, mine)   # take over a stale lock; a racing taker-over that wrote last wins
        return mine if _read_bytes(path) == mine else None
    with os.fdopen(fd, "wb") as fh:
        fh.write(mine)
    return mine


def _release(root, mine):
    path = os.path.join(root, "lock")
    if _read_bytes(path) == mine:
        try:
            os.remove(path)
        except OSError:
            pass


# --- the archive ------------------------------------------------------------------------------

def _fetch(url, dest):
    """Streams url into dest; the SHA-256 of what arrived. A body shorter than its Content-Length fails."""
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response, open(dest, "wb") as fh:
        length = response.headers.get("Content-Length")
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            fh.write(chunk)
            size += len(chunk)
    if length is not None and size != int(length):
        raise Refused("truncated download: %d of %s bytes" % (size, length))
    return digest.hexdigest()


def _check_names(names, expected):
    candidates = []
    for name in names:
        parts = re.split(r"[\\/]", name)
        if ".." in parts:
            raise Refused("archive member %r leaves its folder" % name)
        if name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", name):
            raise Refused("archive member %r is an absolute name" % name)
        if parts[-1] in BINARY_NAMES:
            candidates.append(name)
    if candidates != [expected]:
        raise Refused("archive holds binary candidates %r, expected only %s" % (candidates, expected))


def _extract(archive, asset, expected, dest):
    """Streams the one expected member into dest; refuses any archive with an unsafe or extra binary member."""
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as source:
            infos = source.infolist()
            _check_names([info.filename for info in infos], expected)
            info = [item for item in infos if item.filename == expected][0]
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise Refused("archive member %s is not a regular file" % expected)
            with source.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    elif asset.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as source:
            entries = source.getmembers()
            _check_names([entry.name for entry in entries], expected)
            entry = [item for item in entries if item.name == expected][0]
            if not entry.isfile():   # a symlink or a hardlink at the expected name
                raise Refused("archive member %s is not a regular file" % expected)
            with source.extractfile(entry) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        raise Refused("archive %s has an unknown format" % asset)


def _install(root, work, pin, asset, target, base_url, launch):
    version = pin["version"]
    archive = os.path.join(work, "archive")
    digest = _fetch(base_url + asset["name"], archive)
    expected = asset["sha256"]
    if digest != expected:
        os.remove(archive)
        raise Refused("hash mismatch for %s" % asset["name"])
    binary = os.path.join(work, exe_name(target))
    _extract(archive, asset["name"], "shellwise-v%s-%s/%s" % (version, target, exe_name(target)), binary)
    if os.name != "nt":
        os.chmod(binary, 0o755)
    text = run_version(binary, VERSION_BOUND, launch)
    if text != "shellwise " + version:
        raise Refused("version check printed %r, expected shellwise %s" % (text[:80], version))
    final = _binary_path(root, version, target)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    os.replace(binary, final)
    return final


def _last_write(folder):
    """The newest mtime of a flat folder and its entries, or None when one cannot be read. A download keeps
    writing its archive, which does not move the folder's own mtime."""
    try:
        return max([os.stat(folder).st_mtime] + [os.stat(os.path.join(folder, n)).st_mtime
                                                 for n in os.listdir(folder)])
    except OSError:
        return None


def _prune(root, version, now):
    """Removes the cached binaries of other versions (a pin change) and the work folders of killed downloads:
    those nothing wrote to for LOCK_STALE, so a slow download whose lock was taken over keeps its own. A busy
    one stays until next time. The caller holds the lock."""
    for name in os.listdir(root):
        path = os.path.join(root, name)
        old = name.startswith("v") and name != "v" + version
        if name.startswith(".work-"):
            written = _last_write(path)
            old = written is not None and now - written > LOCK_STALE
        if old and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def download(data_dir, pin, target, base_url, *, launch=None, clock=time.time):
    version = pin["version"]
    asset = pin["assets"].get(target)
    if not asset:
        return Status("no-asset", version, target, "", "no asset for %s" % target)
    root = _root(data_dir)
    mine = _acquire(root, clock)
    if mine is None:
        return Status("downloading", version, target, "", "another session is downloading")
    work = None
    try:
        # A session start after the one that spawned this child may have set an override: its result stands.
        if _override_left(root):
            return Status("override-kept", version, target, "", "an override is on record")
        _write_status(root, version, target, "downloading")
        work = tempfile.mkdtemp(prefix=".work-", dir=root)
        path = _install(root, work, pin, asset, target, base_url, launch)
        if _override_left(root):
            return Status("override-kept", version, target, "", "an override is on record")
        _write_atomic(os.path.join(root, "current"), (path + "\n").encode("utf-8"))
        _write_status(root, version, target, "ready")
        _prune(root, version, clock())
        return Status("ready", version, target, path, "")
    except Exception as exc:   # every failure is one reason line, never a traceback
        reason = _one_line(exc if isinstance(exc, Refused) else "%s: %s" % (type(exc).__name__, exc))
        if _override_left(root):
            return Status("override-kept", version, target, "", "an override is on record")
        try:
            _write_status(root, version, target, "failed", reason)
        except OSError:
            pass
        return Status("failed", version, target, "", reason)
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
        _release(root, mine)


# --- resolve and ensure -----------------------------------------------------------------------

def resolve(data_dir, pin, override, target=None, launch=None):
    """The slot's state now; runs nothing but an override's `--version`."""
    version = pin["version"]
    if override:
        return _check_override(override, pin, target, launch)
    target = host_target() if target is None else target
    if not target or not pin["assets"].get(target):
        name = target or "%s %s" % (sys.platform, platform.machine())
        return Status("no-asset", version, target or "", "", "no asset for %s" % name)
    root = _root(data_dir)
    path = _binary_path(root, version, target)
    current = (_read_text(os.path.join(root, "current")) or "").strip()
    if current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(path) and os.path.isfile(path):
        return Status("ready", version, target, path, "")
    status = _read_status(root)
    if status.get("version") == version and status.get("target") == target and status.get("state") == "failed":
        return Status("failed", version, target, "", "download failed: %s" % status.get("reason", ""))
    return Status("missing", version, target, "", "downloading")


def ensure(data_dir, pin, override, *, spawn, target=None, launch=None, clock=time.time):
    """resolve, then start the download with spawn() when one is needed and no other session holds it."""
    status = resolve(data_dir, pin, override, target, launch)
    if override or status.state in ("ready", "override", "no-asset"):
        return status
    if _lock_held(_root(data_dir), clock):
        return status._replace(state="downloading", reason="downloading")
    try:
        spawn()
    except Exception as exc:   # the slot stays unfilled; the next session start tries again
        return status._replace(state="failed", reason="download could not start: %s" % _one_line(exc))
    reason = "downloading" if status.state == "missing" else status.reason + "; retrying"
    return status._replace(state="downloading", reason=reason)


def spawn_child(argv):
    """Starts argv detached from the hook: its own session or process group, no inherited stdio."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
    if os.name == "nt":   # never CREATE_BREAKAWAY_FROM_JOB: the hook's job refuses it (WinError 5)
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


# --- the override from config ----------------------------------------------------------------

def _core_assemble_dir(config_dir):
    """rigger-core's tools/assemble folder from the first absolute installPath of the first `rigger-core@` key
    of installed_plugins.json that holds config.py, or None."""
    try:
        data = json.loads((_read_bytes(os.path.join(config_dir, "plugins", "installed_plugins.json")) or b"")
                          .decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    key = next((name for name in plugins if isinstance(name, str) and name.startswith("rigger-core@")), None) \
        if isinstance(plugins, dict) else None
    for item in plugins[key] if key and isinstance(plugins[key], list) else []:
        root = item.get("installPath") if isinstance(item, dict) else None
        if isinstance(root, str) and os.path.isabs(root) and (os.name != "nt" or os.path.splitdrive(root)[0]):
            folder = os.path.join(root, "tools", "assemble")
            if os.path.isfile(os.path.join(folder, "config.py")):
                return folder
    return None


def read_override(environ=None):
    """shell_guard.shellwise_path as rigger-core's config module resolves the personal config (the one level
    that may set it); None when unset or when no rigger-core is installed. Raises Unreadable when the override
    cannot be known: the personal config is refused by load_level, or the listed rigger-core does not load."""
    environ = os.environ if environ is None else environ
    config_dir = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    folder = _core_assemble_dir(config_dir)
    if folder is None:
        return None
    names = ("config", "manifest")
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path.insert(0, folder)
    try:
        config = importlib.import_module("config")
        path = os.path.join(config_dir, "rigger", "config.json")
        data, notices = config.load_level(pathlib.Path(path), 3)
        block = {"shell_guard": data["shell_guard"]} if "shell_guard" in data else {}
        guard = config.resolve({}, [], block, {}, {3: path}).effective.get("shell_guard")
    except Exception as exc:
        raise Unreadable("rigger-core config module did not load: %s: %s" % (type(exc).__name__, _one_line(exc)))
    finally:
        sys.path.remove(folder)
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    if notices:   # load_level refused a personal config that exists: its override is unknown, not unset
        notice = _one_line(notices[0])
        raise Unreadable(notice[len("rigger: "):] if notice.startswith("rigger: ") else notice)
    return guard.get("shellwise_path") if isinstance(guard, dict) else None   # config.py checks the shape


def _override_left(root):
    """True when the files on record come from an override (accepted or refused)."""
    status = _read_status(root)
    return status.get("state") == "override" or (status.get("state") == "failed"
                                                 and str(status.get("reason", "")).startswith("override "))


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- entry points -----------------------------------------------------------------------------

def _load_pin(path):
    pin = json.loads(_read_bytes(path).decode("utf-8"))
    if not (isinstance(pin, dict) and pin.get("format") == 1 and isinstance(pin.get("version"), str)
            and VERSION_LINE.fullmatch("shellwise " + pin["version"]) and isinstance(pin.get("assets"), dict)):
        raise ValueError("pin.json is not a format 1 pin")
    for asset in pin["assets"].values():
        if not (isinstance(asset, dict) and isinstance(asset.get("name"), str)
                and re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256")))):
            raise ValueError("pin.json has an asset without a name and a SHA-256")
    return pin


def _session_start(args, spawn, launch=None):
    data = args.data or os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data:
        return NO_BINARY % "no plugin data folder"
    data = os.path.abspath(data)
    pin_path = os.path.abspath(args.pin or os.path.join(PLUGIN, "pin.json"))
    pin = _load_pin(pin_path)
    base = args.base_url or DEFAULT_BASE.format(version=pin["version"])
    child = [sys.executable, "-I", "-X", "utf8", os.path.abspath(__file__), "--child", "--data", data,
             "--pin", pin_path, "--base-url", base] + (["--target", args.target] if args.target else [])
    root = _root(data)
    unread = None
    try:
        override = read_override()
    except Unreadable as exc:
        if _override_left(root):   # unknown is not unset: an override on record keeps `current` and the status
            return UNREADABLE % exc
        override, unread = None, str(exc)   # none on record: the hash-verified pin beats an unguarded slot
    if not override and _override_left(root):   # the override is gone: back to the download
        _remove(os.path.join(root, "current"))
        _remove(os.path.join(root, "status.json"))
    status = ensure(data, pin, override, spawn=lambda: spawn(child), target=args.target, launch=launch)
    # The status goes first: a download child that reads it before touching `current` sees the override.
    if override and status.state == "override":
        _write_status(root, status.version, status.target, "override")
        _write_atomic(os.path.join(root, "current"), (status.path + "\n").encode("utf-8"))
    elif override:   # refused: the slot stays unfilled, never filled by the downloaded binary
        _write_status(root, status.version, status.target, "failed", status.reason)
        _remove(os.path.join(root, "current"))
    elif status.state == "no-asset" or status.reason.startswith("download could not start"):
        _write_status(root, status.version, status.target, status.state, status.reason)
    if status.state not in ("ready", "override"):
        line = NO_BINARY % status.reason
    else:
        key = "%s %s %s" % (status.state, status.version, status.path)
        line = ""
        if _read_text(os.path.join(root, "announced")) != key:
            _write_atomic(os.path.join(root, "announced"), key.encode("utf-8"))
            line = "shellwise: v%s ready" % status.version
    if unread is None:
        return line
    # One line tells both: the override was not read, and the slot's own line (or "ready" once announced).
    return UNREAD_PIN % (unread, line[len("shellwise: "):] if line else "v%s ready" % status.version)


def _child(args):
    data = os.path.abspath(args.data or os.environ.get("CLAUDE_PLUGIN_DATA") or "")
    pin = _load_pin(os.path.abspath(args.pin or os.path.join(PLUGIN, "pin.json")))
    target = args.target or host_target()
    if args.data or os.environ.get("CLAUDE_PLUGIN_DATA"):
        download(data, pin, target, args.base_url or DEFAULT_BASE.format(version=pin["version"]))


def main(argv=None, *, spawn=None, out=None, launch=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--session-start", action="store_true")
    mode.add_argument("--child", action="store_true")
    parser.add_argument("--data", help="the plugin data folder (default: CLAUDE_PLUGIN_DATA)")
    parser.add_argument("--pin", help="pin.json (default: the plugin's)")
    parser.add_argument("--base-url", help="the release download folder (default: the pinned GitHub release)")
    parser.add_argument("--target", help="the target triple (default: this host's)")
    args = parser.parse_args(argv)
    if args.child:
        try:
            _child(args)
        except Exception:   # the child has no output; status.json carries any reason
            pass
        return 0
    try:
        line = _session_start(args, spawn or spawn_child, launch)
    except Exception as exc:
        line = NO_BINARY % _one_line(exc)
    if line:
        stream = out or sys.stdout.buffer
        stream.write((line + "\n").encode("utf-8"))
        stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
