"""The shellwise installer: archives built here, served by a local HTTP server on 127.0.0.1.

On POSIX the fake binary is a real `#!/bin/sh` script run as itself. On Windows it is a Python
script stored under the binary's name and run through `launch` (an argv prefix of sys.executable):
spawn, timeout and parsing stay the installer's own code.
"""

import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(os.path.dirname(HERE), "shellwise_install.py")
sys.path.insert(0, os.path.dirname(HERE))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "rigger-core"))
import shellwise_install as si  # noqa: E402

WINDOWS = os.name == "nt"
TARGET = "x86_64-pc-windows-msvc" if WINDOWS else "x86_64-unknown-linux-gnu"
LAUNCH = (lambda path: [sys.executable, "-I", path]) if WINDOWS else None
BOUND = 8.0


def exe_name(target):
    return "shellwise.exe" if target.endswith("-windows-msvc") else "shellwise"


def fake_binary(output="shellwise 0.1.1", sleep=0, marker=None):
    """marker: a file the binary creates when it runs."""
    if WINDOWS:
        text = "import sys, time\ntime.sleep(%d)\nsys.stdout.write(%r + '\\n')\n" % (sleep, output)
        text += "open(%r, 'wb').close()\n" % marker if marker else ""
    else:
        text = "#!/bin/sh\n" + ("sleep %d\n" % sleep if sleep else "") + "echo '%s'\n" % output
        text += "touch '%s'\n" % marker if marker else ""
    return text.encode("utf-8")


def members(version="0.1.1", target=TARGET, binary=None, extra=()):
    """The release layout: a top-level dir, three docs and the binary (mode 644: the installer sets the bit)."""
    top = "shellwise-v%s-%s" % (version, target)
    rows = [(top + "/", None, "dir", None)]
    rows += [(top + "/" + doc, b"doc\n", "file", None) for doc in ("LICENSE", "README.md", "CHANGELOG.md")]
    rows.append((top + "/" + exe_name(target), fake_binary("shellwise " + version) if binary is None else binary,
                 "file", None))
    return rows + list(extra)


def build(rows, fmt):
    buf = io.BytesIO()
    if fmt == "zip":
        with zipfile.ZipFile(buf, "w") as archive:
            for name, data, kind, link in rows:
                info = zipfile.ZipInfo(name)
                if kind == "symlink":
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    data = link.encode("utf-8")
                archive.writestr(info, b"" if data is None else data)
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            for name, data, kind, link in rows:
                info = tarfile.TarInfo(name.rstrip("/") if kind == "dir" else name)
                info.mode = 0o755 if kind == "dir" else 0o644
                info.type = {"dir": tarfile.DIRTYPE, "file": tarfile.REGTYPE, "symlink": tarfile.SYMTYPE,
                             "hardlink": tarfile.LNKTYPE}[kind]
                if link:
                    info.linkname = link
                info.size = len(data) if kind == "file" else 0
                archive.addfile(info, io.BytesIO(data) if kind == "file" else None)
    return buf.getvalue()


def asset_name(version, target, fmt):
    return "shellwise-v%s-%s.%s" % (version, target, "zip" if fmt == "zip" else "tar.gz")


def default_fmt(target=TARGET):
    return "zip" if target.endswith("-windows-msvc") else "tar.gz"


def make_pin(version, target, name, data):
    return {"format": 1, "version": version,
            "assets": {target: {"name": name, "sha256": hashlib.sha256(data).hexdigest()}}}


class Server:
    """Serves `files`; logs each path; a gated server holds every reply until `release` or 30 s."""

    def __init__(self):
        self.files, self.lengths, self.requests = {}, {}, []
        self.gated = False
        self.release = threading.Event()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                name = self.path.lstrip("/")
                server.requests.append(name)
                if server.gated:
                    server.release.wait(30)
                if name not in server.files:
                    self.send_error(404)
                    return
                body = server.files[name]
                self.send_response(200)
                self.send_header("Content-Length", str(server.lengths.get(name, len(body))))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/" % self.httpd.server_address[1]

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def wait_for(condition, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def dead_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return "http://127.0.0.1:%d/" % port


class Base(unittest.TestCase):
    def setUp(self):
        self.data = os.path.abspath(tempfile.mkdtemp(prefix="sw-install-"))
        self.root = os.path.join(self.data, "shellwise")
        self.server = Server()
        self.addCleanup(self._clean)
        # --session-start reads the personal config: never the real one.
        self.no_config = os.path.join(self.data, "no-config")
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.no_config})
        env.start()
        self.addCleanup(env.stop)

    def _clean(self):
        self.server.close()
        for _ in range(100):   # a child that just exited may still hold a handle on Windows
            shutil.rmtree(self.data, ignore_errors=True)
            if not os.path.exists(self.data):
                return
            time.sleep(0.1)

    def serve(self, version="0.1.1", target=TARGET, fmt=None, rows=None):
        fmt = fmt or default_fmt(target)
        data = build(rows if rows is not None else members(version, target), fmt)
        name = asset_name(version, target, fmt)
        self.server.files[name] = data
        return make_pin(version, target, name, data)

    def binary_path(self, version="0.1.1", target=TARGET):
        return os.path.join(self.root, "v" + version, target, exe_name(target))

    def current(self):
        path = os.path.join(self.root, "current")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")

    def status_file(self):
        with open(os.path.join(self.root, "status.json"), "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))

    def files_left(self):
        found = []
        for folder, _, names in os.walk(self.data):
            found += [os.path.relpath(os.path.join(folder, n), self.root).replace(os.sep, "/") for n in names]
        return sorted(found)

    def same(self, left, right):
        self.assertEqual(os.path.normcase(os.path.realpath(left)), os.path.normcase(os.path.realpath(right)))

    def download(self, pin, **kw):
        return si.download(self.data, pin, kw.pop("target", TARGET), kw.pop("base", self.server.url),
                           launch=LAUNCH, **kw)

    def assert_refused(self, status, word):
        self.assertEqual(status.state, "failed")
        self.assertIn(word, status.reason)
        self.assertIsNone(self.current())
        self.assertEqual(self.files_left(), ["status.json"])
        self.assertEqual(self.status_file()["state"], "failed")

    def write_lock(self, start, pid=None):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "lock"), "wb") as fh:
            fh.write(json.dumps({"pid": pid or os.getpid(), "start": start}).encode("utf-8"))

    def write_pin(self, pin):
        path = os.path.join(self.data, "pin.json")
        with open(path, "wb") as fh:
            fh.write(json.dumps(pin).encode("utf-8"))
        return path

    def session_start(self, pin, spawn, target=TARGET):
        out = io.BytesIO()
        argv = ["--session-start", "--data", self.data, "--pin", self.write_pin(pin), "--base-url",
                self.server.url]
        code = si.main(argv + (["--target", target] if target else []), spawn=spawn, out=out)
        self.assertEqual(code, 0)
        return out.getvalue().decode("utf-8")


class Spawns:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, *argv):   # ensure() calls spawn(); main's spawn gets the child's argv
        self.calls.append(argv)
        if self.error:
            raise self.error


class TargetTests(Base):
    def test_target_table(self):
        rows = [("win32", "AMD64", "x86_64-pc-windows-msvc"), ("linux", "x86_64", "x86_64-unknown-linux-gnu"),
                ("darwin", "arm64", "aarch64-apple-darwin"), ("darwin", "x86_64", "x86_64-apple-darwin"),
                ("linux", "aarch64", None), ("win32", "ARM64", None)]
        for plat, machine, target in rows:
            with self.subTest(plat=plat, machine=machine):
                self.assertEqual(si.host_target(plat, machine), target)
        pin = self.serve()
        status = si.resolve(self.data, pin, None, target="aarch64-unknown-linux-gnu")
        self.assertEqual(status.state, "no-asset")
        spawns = Spawns()
        line = self.session_start(pin, spawns, target="aarch64-unknown-linux-gnu")
        self.assertEqual(line, "shellwise: no binary (no asset for aarch64-unknown-linux-gnu); "
                               "commands run unguarded\n")
        self.assertEqual(spawns.calls, [])
        self.assertEqual(self.server.requests, [])


class PlacementTests(Base):
    def test_good_archive_is_placed_and_current_written(self):
        for fmt in ("zip", "tar.gz"):
            with self.subTest(fmt=fmt):
                shutil.rmtree(self.root, ignore_errors=True)
                pin = self.serve(fmt=fmt)
                status = self.download(pin)
                self.assertEqual(status.state, "ready", status.reason)
                self.same(status.path, self.binary_path())
                self.same(self.current().rstrip("\n"), self.binary_path())
                self.assertEqual(self.current().count("\n"), 1)
                self.assertEqual(sorted(self.status_file()), ["at", "reason", "state", "target", "version"])
                self.assertEqual(self.status_file()["state"], "ready")
                self.assertEqual(self.files_left(), ["current", "status.json", "v0.1.1/%s/%s" % (TARGET, exe_name(TARGET))])
                resolved = si.resolve(self.data, pin, None, target=TARGET)
                self.assertEqual(resolved.state, "ready")
                self.same(resolved.path, self.binary_path())

    @unittest.skipIf(WINDOWS, "POSIX only: Windows has no exec bit")
    def test_exec_bit_is_set_on_posix(self):
        control = os.path.join(self.data, "control")
        with open(control, "wb") as fh:
            fh.write(b"x")
        self.assertEqual(os.stat(control).st_mode & 0o111, 0, "the temp folder marks every file executable")
        os.remove(control)
        status = self.download(self.serve())
        self.assertEqual(status.state, "ready", status.reason)
        self.assertEqual(os.stat(self.binary_path()).st_mode & 0o111, 0o111)

    def test_ready_line_once(self):
        pin = self.serve()
        self.assertEqual(self.download(pin).state, "ready")
        spawns = Spawns()
        self.assertEqual(self.session_start(pin, spawns), "shellwise: v0.1.1 ready\n")
        self.assertEqual(self.session_start(pin, spawns), "")
        self.assertEqual(spawns.calls, [])


class TrustTests(Base):
    def test_hash_mismatch_deletes_and_fails(self):
        marks = tempfile.mkdtemp(prefix="sw-marks-")
        self.addCleanup(shutil.rmtree, marks, True)
        marker = os.path.join(marks, "ran")
        tampered_rows = members(binary=fake_binary(marker=marker) + b"# tampered\n")
        own = self.serve(rows=tampered_rows)
        self.server.files[own["assets"][TARGET]["name"] + ".sha256"] = own["assets"][TARGET]["sha256"].encode()
        control = self.download(own)   # pinned to itself, it installs and runs
        self.assertEqual(control.state, "ready", control.reason)
        self.assertTrue(os.path.exists(marker))
        os.remove(marker)
        shutil.rmtree(self.root)
        self.server.requests.clear()
        pin = self.serve()
        name = pin["assets"][TARGET]["name"]
        tampered = build(tampered_rows, default_fmt())
        self.server.files[name] = tampered
        self.server.files[name + ".sha256"] = ("%s  %s\n" % (hashlib.sha256(tampered).hexdigest(), name)).encode()
        status = self.download(pin)
        self.assertFalse(os.path.exists(marker), "the binary ran before its hash matched")
        self.assert_refused(status, "hash")
        self.assertEqual(self.server.requests, [name])

    def test_truncated_download_fails(self):
        for case in ("length-above-body", "body-cut"):
            with self.subTest(case=case):
                shutil.rmtree(self.root, ignore_errors=True)
                pin = self.serve()
                name = pin["assets"][TARGET]["name"]
                body = self.server.files[name]
                if case == "length-above-body":   # the whole, pinned archive: only the length refuses it
                    self.server.lengths[name] = len(body) + 10
                else:
                    self.server.files[name] = body[: len(body) // 2]
                    self.server.lengths[name] = len(body)
                self.assert_refused(self.download(pin), "truncated")

    def test_only_a_verified_member_is_placed(self):
        top = "shellwise-v0.1.1-%s/" % TARGET
        exe = exe_name(TARGET)
        good = fake_binary()
        cases = []
        for fmt in ("zip", "tar.gz"):
            cases += [
                ("dotdot", fmt, members(extra=[(top + "../x", b"x", "file", None)])),
                ("absolute", fmt, members(extra=[("/abs/x", b"x", "file", None)])),
                ("second-binary", fmt, members(extra=[(top + "bin/" + exe, good, "file", None)])),
            ]
        linked = [row for row in members() if not row[0].endswith(exe)] + [(top + "payload", good, "file", None)]
        cases += [
            ("symlink", "tar.gz", linked + [(top + exe, None, "symlink", "payload")]),
            ("hardlink", "tar.gz", linked + [(top + exe, None, "hardlink", top + "payload")]),
            ("zip-symlink", "zip", linked + [(top + exe, None, "symlink", "payload")]),
        ]
        for kind, fmt, rows in cases:
            with self.subTest(kind=kind, fmt=fmt):
                shutil.rmtree(self.root, ignore_errors=True)
                self.assert_refused(self.download(self.serve(fmt=fmt, rows=rows)), "archive")

    def test_version_mismatch_after_unpack_fails(self):
        for output, sleep in (("shellwise 0.1.0", 0), ("something else", 0), ("shellwise 0.1.1", 30)):
            with self.subTest(output=output, sleep=sleep):
                shutil.rmtree(self.root, ignore_errors=True)
                pin = self.serve(rows=members(binary=fake_binary(output, sleep)))
                started = time.monotonic()
                self.assert_refused(self.download(pin), "version")
                self.assertLess(time.monotonic() - started, 15.0)


class LockTests(Base):
    def test_two_sessions_share_one_download(self):
        pin = self.serve()
        name = pin["assets"][TARGET]["name"]
        self.server.gated = True
        results = []
        first = threading.Thread(target=lambda: results.append(self.download(pin)))
        first.start()
        self.assertTrue(wait_for(lambda: self.server.requests, 10))
        second = self.download(pin)
        self.assertEqual(second.state, "downloading")
        spawns = Spawns()
        self.assertEqual(si.ensure(self.data, pin, None, spawn=spawns, target=TARGET).state, "downloading")
        self.assertEqual(spawns.calls, [])
        self.server.release.set()
        first.join(20)
        self.assertEqual(results[0].state, "ready", results[0].reason)
        self.assertEqual(self.server.requests, [name])
        self.assertFalse(os.path.exists(os.path.join(self.root, "lock")))

    def test_lock_older_than_ten_minutes_is_taken_over(self):
        pin = self.serve()
        name = pin["assets"][TARGET]["name"]
        self.write_lock(start=1000.0, pid=1)
        self.assertEqual(self.download(pin, clock=lambda: 1540.0).state, "downloading")   # 9 minutes: held
        self.assertEqual(self.server.requests, [])
        spawns = Spawns()
        self.assertEqual(si.ensure(self.data, pin, None, spawn=spawns, target=TARGET, clock=lambda: 1540.0).state,
                         "downloading")
        self.assertEqual(spawns.calls, [])
        si.ensure(self.data, pin, None, spawn=spawns, target=TARGET, clock=lambda: 1660.0)
        self.assertEqual(len(spawns.calls), 1)   # 11 minutes: the next session starts a new download
        status = self.download(pin, clock=lambda: 1660.0)
        self.assertEqual(status.state, "ready", status.reason)
        self.assertEqual(self.server.requests, [name])


class OverrideTests(Base):
    def override(self, output, sleep=0):
        path = os.path.join(self.data, "own", exe_name(TARGET))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(fake_binary(output, sleep))
        os.chmod(path, 0o755)
        return path

    def pin_for(self, version):
        return make_pin(version, TARGET, asset_name(version, TARGET, default_fmt()), b"unused")

    def test_override_used_when_at_or_above_the_pin(self):
        for pinned, own in (("0.1.1", "0.1.1"), ("0.1.1", "0.2.0"), ("0.9.0", "0.10.0")):
            with self.subTest(pinned=pinned, own=own):
                path = self.override("shellwise " + own)
                status = si.resolve(self.data, self.pin_for(pinned), path, target=TARGET, launch=LAUNCH)
                self.assertEqual(status.state, "override", status.reason)
                self.same(status.path, path)
                spawns = Spawns()
                si.ensure(self.data, self.pin_for(pinned), path, spawn=spawns, target=TARGET, launch=LAUNCH)
                self.assertEqual(spawns.calls, [])

    def test_override_below_the_pin_is_refused(self):
        rows = (("0.1.1", "shellwise 0.1.0", "below"), ("0.10.0", "shellwise 0.9.0", "below"),
                ("0.1.1", "not a version", "version"), ("0.1.1", None, "missing"))
        for pinned, output, word in rows:
            with self.subTest(pinned=pinned, output=output):
                path = self.override(output) if output else os.path.join(self.data, "nowhere", "shellwise")
                status = si.resolve(self.data, self.pin_for(pinned), path, target=TARGET, launch=LAUNCH)
                self.assertEqual(status.state, "failed")
                self.assertIn(word, status.reason)
                spawns = Spawns()
                self.assertEqual(si.ensure(self.data, self.pin_for(pinned), path, spawn=spawns, target=TARGET,
                                           launch=LAUNCH).state, "failed")
                self.assertEqual(spawns.calls, [])

    def test_override_that_hangs_is_refused_within_its_bound(self):
        path = self.override("shellwise 0.1.1", sleep=30)
        started = time.monotonic()
        status = si.resolve(self.data, self.pin_for("0.1.1"), path, target=TARGET, launch=LAUNCH)
        self.assertLess(time.monotonic() - started, 6.0)
        self.assertEqual(status.state, "failed")
        self.assertIn("timed out", status.reason)


class ConfigOverrideTests(Base):
    """D76: --session-start reads shell_guard.shellwise_path through rigger-core's config module, found by its
    installPath in a copy laid out like the plugin cache; an accepted override is what `current` names."""

    override = OverrideTests.override
    pin_for = OverrideTests.pin_for

    def setUp(self):
        super().setUp()
        self.config = os.path.join(self.data, "config")
        self.project = os.path.join(self.data, "project")
        os.makedirs(self.project)
        core = os.path.join(self.config, "plugins", "cache", "rigger", "rigger-core", "0.0.0")
        shutil.copytree(os.path.join(CORE, "tools", "assemble"), os.path.join(core, "tools", "assemble"),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        self.write_json(os.path.join(self.config, "plugins", "installed_plugins.json"),
                        {"version": 2, "plugins": {"rigger-core@rigger": [{"scope": "user", "installPath": core}]}})
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.config, "CLAUDE_PROJECT_DIR": self.project})
        env.start()
        self.addCleanup(env.stop)

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(json.dumps(data).encode("utf-8"))

    def set_override(self, path, where="personal"):
        target = os.path.join(self.config, "rigger", "config.json") if where == "personal" else \
            os.path.join(self.project, ".claude", "rigger.json")
        self.write_json(target, {"schema": 1, "shell_guard": {"shellwise_path": path}})

    def start(self, pin, spawns):
        out = io.BytesIO()
        argv = ["--session-start", "--data", self.data, "--pin", self.write_pin(pin), "--base-url",
                self.server.url, "--target", TARGET]
        self.assertEqual(si.main(argv, spawn=spawns, out=out, launch=LAUNCH), 0)
        return out.getvalue().decode("utf-8")

    def test_accepted_override_is_current_and_announced_once(self):
        path = self.override("shellwise 0.2.0")
        self.set_override(path)
        spawns = Spawns()
        self.assertEqual(self.start(self.pin_for("0.1.1"), spawns), "shellwise: v0.2.0 ready\n")
        self.assertIsNotNone(self.current())
        self.same(self.current().rstrip("\n"), path)
        self.assertEqual((self.status_file()["state"], self.status_file()["version"]), ("override", "0.2.0"))
        self.assertEqual(self.start(self.pin_for("0.1.1"), spawns), "")
        self.assertEqual(spawns.calls, [])

    def test_refused_override_leaves_the_slot_unfilled(self):
        pin = self.serve()
        self.assertEqual(self.download(pin).state, "ready")   # a downloaded binary the launcher could fall back to
        path = self.override("shellwise 0.1.0")
        self.set_override(path)
        spawns = Spawns()
        reason = "override %s: version 0.1.0 below the pin 0.1.1" % path
        self.assertEqual(self.start(pin, spawns), "shellwise: no binary (%s); commands run unguarded\n" % reason)
        self.assertIsNone(self.current())
        self.assertEqual((self.status_file()["state"], self.status_file()["reason"]), ("failed", reason))
        self.assertEqual(spawns.calls, [])

    def test_download_started_earlier_keeps_a_later_override(self):
        """Session start A starts a download child; session start B then accepts or refuses an override. The
        child, whether it begins after B (early), is mid-download (late) or then fails (late-fail), leaves B's
        `current` and status: a refused override never runs the downloaded binary."""
        personal = os.path.join(self.config, "rigger", "config.json")
        for kind in ("refused", "accepted"):
            for timing in ("early", "late", "late-fail"):
                with self.subTest(kind=kind, timing=timing):
                    shutil.rmtree(self.root, ignore_errors=True)
                    if os.path.exists(personal):
                        os.remove(personal)
                    self.server.requests.clear()
                    self.server.release.clear()
                    self.server.gated = timing != "early"
                    pin = self.serve()
                    spawns = Spawns()
                    self.assertEqual(self.start(pin, spawns),
                                     "shellwise: no binary (downloading); commands run unguarded\n")
                    self.assertEqual(len(spawns.calls), 1)
                    results = []
                    child = threading.Thread(target=lambda: results.append(self.download(pin)))
                    if timing != "early":
                        child.start()
                        self.assertTrue(wait_for(lambda: self.server.requests, 10), "the child never asked")
                    path = self.override("shellwise 0.2.0" if kind == "accepted" else "shellwise 0.1.0")
                    self.set_override(path)
                    self.start(pin, Spawns())
                    if timing == "early":
                        child.start()
                    if timing == "late-fail":
                        del self.server.files[pin["assets"][TARGET]["name"]]
                    self.server.release.set()
                    child.join(20)
                    self.assertFalse(child.is_alive())
                    if timing == "late":   # the child got as far as placing its binary
                        self.assertTrue(os.path.isfile(self.binary_path()))
                    self.assertTrue(os.path.isfile(os.path.join(self.root, "status.json")))
                    row = self.status_file()
                    if kind == "accepted":
                        self.assertIsNotNone(self.current())
                        self.same(self.current().rstrip("\n"), path)
                        self.assertEqual((row["state"], row["version"]), ("override", "0.2.0"))
                    else:
                        self.assertIsNone(self.current())
                        self.assertEqual((row["state"], row["reason"]),
                                         ("failed", "override %s: version 0.1.0 below the pin 0.1.1" % path))

    def test_override_set_in_the_project_is_not_used(self):
        self.set_override(self.override("shellwise 0.2.0"), where="project")
        spawns = Spawns()
        self.assertEqual(self.start(self.pin_for("0.1.1"), spawns),
                         "shellwise: no binary (downloading); commands run unguarded\n")
        self.assertEqual(len(spawns.calls), 1)
        self.assertIsNone(self.current())

    def test_removed_override_goes_back_to_the_download(self):
        path = self.override("shellwise 0.2.0")
        self.set_override(path)
        self.assertEqual(self.start(self.pin_for("0.1.1"), Spawns()), "shellwise: v0.2.0 ready\n")
        os.remove(os.path.join(self.config, "rigger", "config.json"))
        spawns = Spawns()
        self.assertEqual(self.start(self.pin_for("0.1.1"), spawns),
                         "shellwise: no binary (downloading); commands run unguarded\n")
        self.assertIsNone(self.current())
        self.assertFalse(os.path.exists(os.path.join(self.root, "status.json")))   # no stale override row
        self.assertEqual(len(spawns.calls), 1)

    def test_removed_refused_override_goes_back_to_the_download(self):
        self.set_override(self.override("shellwise 0.1.0"))
        self.assertTrue(self.start(self.pin_for("0.1.1"), Spawns()).startswith("shellwise: no binary (override "))
        os.remove(os.path.join(self.config, "rigger", "config.json"))
        spawns = Spawns()
        self.assertEqual(self.start(self.pin_for("0.1.1"), spawns),
                         "shellwise: no binary (downloading); commands run unguarded\n")
        self.assertFalse(os.path.exists(os.path.join(self.root, "status.json")))
        self.assertEqual(len(spawns.calls), 1)

    def test_override_is_not_read_without_a_loadable_rigger_core(self):
        listing = os.path.join(self.config, "plugins", "installed_plugins.json")

        def break_core():
            broken = os.path.join(self.data, "broken-core")
            os.makedirs(os.path.join(broken, "tools", "assemble"), exist_ok=True)
            with open(os.path.join(broken, "tools", "assemble", "config.py"), "wb") as fh:
                fh.write(b"raise RuntimeError('broken')\n")
            self.write_json(listing, {"version": 2, "plugins": {"rigger-core@rigger": [{"installPath": broken}]}})

        for name, change in (("no plugin list", lambda: os.remove(listing)), ("config.py does not load", break_core)):
            with self.subTest(name):
                shutil.rmtree(self.root, ignore_errors=True)
                self.set_override(self.override("shellwise 0.2.0"))
                change()
                spawns = Spawns()
                self.assertEqual(self.start(self.pin_for("0.1.1"), spawns),
                                 "shellwise: no binary (downloading); commands run unguarded\n")
                self.assertEqual(len(spawns.calls), 1)

    def test_no_asset_is_written_to_the_status(self):
        out = io.BytesIO()
        argv = ["--session-start", "--data", self.data, "--pin", self.write_pin(self.pin_for("0.1.1")),
                "--target", "riscv64-unknown-linux-gnu"]
        self.assertEqual(si.main(argv, spawn=Spawns(), out=out), 0)
        self.assertEqual(out.getvalue().decode("utf-8"),
                         "shellwise: no binary (no asset for riscv64-unknown-linux-gnu); commands run unguarded\n")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "status.json")))
        self.assertEqual(self.status_file()["state"], "no-asset")


class PinChangeTests(Base):
    def test_pin_change_replaces_the_cached_binary(self):
        self.assertEqual(self.download(self.serve("0.1.1")).state, "ready")
        new = self.serve("0.2.0")
        self.assertNotEqual(si.resolve(self.data, new, None, target=TARGET).state, "ready")
        status = self.download(new)
        self.assertEqual(status.state, "ready", status.reason)
        self.same(self.current().rstrip("\n"), self.binary_path("0.2.0"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "v0.1.1")))
        self.assertEqual(si.resolve(self.data, new, None, target=TARGET).state, "ready")


class SessionStartTests(Base):
    def test_no_network_is_one_line(self):
        pin = self.serve()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "RIGGER_", "HARNESS_"))}
        env["CLAUDE_CONFIG_DIR"] = self.no_config
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", MODULE, "--child", "--data", self.data, "--pin",
                               self.write_pin(pin), "--base-url", dead_url(), "--target", TARGET],
                              capture_output=True, timeout=60, env=env)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""))
        self.assertEqual(self.status_file()["state"], "failed")
        gone = subprocess.run([sys.executable, "-I", "-X", "utf8", MODULE, "--child", "--data", self.data, "--pin",
                               os.path.join(self.data, "no-pin.json")], capture_output=True, timeout=60, env=env)
        self.assertEqual((gone.returncode, gone.stdout, gone.stderr), (0, b"", b""))   # a child never tracebacks
        line = self.session_start(pin, Spawns())
        self.assertEqual(line.count("\n"), 1)
        self.assertTrue(line.startswith("shellwise: no binary (") and line.endswith("; commands run unguarded\n"))
        self.assertIn("download failed", line)
        self.assertNotIn("Traceback", line)

    def test_failed_download_is_retried_at_the_next_start(self):
        pin = self.serve()
        self.assertEqual(self.download(pin, base=dead_url()).state, "failed")
        self.assertEqual(self.status_file()["state"], "failed")
        spawns = Spawns()
        line = self.session_start(pin, spawns)
        self.assertEqual(len(spawns.calls), 1)
        self.assertTrue(line.startswith("shellwise: no binary (download failed: ")
                        and line.endswith("; retrying); commands run unguarded\n"), line)

    def test_vanished_binary_is_downloaded_again(self):
        """`current` still names the binary, but the file is gone (quarantined): the next start downloads it
        again and says so, though the ready line was announced before."""
        pin = self.serve()
        self.assertEqual(self.download(pin).state, "ready")
        self.assertEqual(self.session_start(pin, Spawns()), "shellwise: v0.1.1 ready\n")
        os.remove(self.binary_path())
        self.assertIsNotNone(self.current())
        self.assertEqual(si.resolve(self.data, pin, None, target=TARGET).state, "missing")
        spawns = Spawns()
        self.assertEqual(self.session_start(pin, spawns),
                         "shellwise: no binary (downloading); commands run unguarded\n")
        self.assertEqual(len(spawns.calls), 1)

    def test_session_start_is_bounded(self):
        target = si.host_target()
        if target is None:
            self.skipTest("this host has no shellwise asset")
        pin = self.serve(target=target)
        self.server.gated = True
        with self.subTest(mode="detached spawn"):
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("CLAUDE_", "RIGGER_", "HARNESS_"))}
            env["CLAUDE_CONFIG_DIR"] = self.no_config
            started = time.monotonic()
            proc = subprocess.run([sys.executable, "-I", "-X", "utf8", MODULE, "--session-start", "--data", self.data,
                                   "--pin", self.write_pin(pin), "--base-url", self.server.url],
                                  capture_output=True, timeout=40, env=env)
            self.assertLess(time.monotonic() - started, BOUND)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, b"shellwise: no binary (downloading); commands run unguarded\n")   # LF
            self.assertTrue(wait_for(lambda: self.server.requests, 20), "the detached child never asked")
            del self.server.files[pin["assets"][target]["name"]]
            self.server.release.set()
            self.assertTrue(wait_for(lambda: not os.path.exists(os.path.join(self.root, "lock")), 20))
        shutil.rmtree(self.root, ignore_errors=True)
        self.server.requests.clear()
        self.server.release.clear()
        with self.subTest(mode="spawn fails"):
            started = time.monotonic()
            line = self.session_start(pin, Spawns(OSError("spawn refused")), target=target)
            self.assertLess(time.monotonic() - started, BOUND)
            self.assertEqual(line, "shellwise: no binary (download could not start: spawn refused); "
                                   "commands run unguarded\n")
            self.assertEqual(self.server.requests, [])
            self.assertFalse(os.path.exists(self.binary_path(target=target)))
            self.assertIsNone(self.current())
            self.assertTrue(os.path.isfile(os.path.join(self.root, "status.json")))
            self.assertEqual(self.status_file()["state"], "failed")   # the doctor row reads it

    def test_spawn_flags(self):
        with mock.patch.object(si.subprocess, "Popen") as popen:
            si.spawn_child(["child", "--child"])
        self.assertEqual(popen.call_count, 1)
        kwargs = popen.call_args.kwargs
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(kwargs.get(stream), subprocess.DEVNULL)
        self.assertIs(kwargs.get("close_fds"), True)
        if WINDOWS:
            self.assertEqual(kwargs.get("creationflags"),
                             subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertIs(kwargs.get("start_new_session"), True)


if __name__ == "__main__":
    unittest.main()
