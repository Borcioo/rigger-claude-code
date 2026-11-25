"""Checks for mem_check.py: the 13 cases of mem-check.tests.ps1 ported (Windows commit headroom through the
--reading-json and --event-limits-json seams), the Linux /proc/meminfo and macOS vm_stat readings, the
load.* config keys at each level with the per-system defaults and the project's stricter-only rule, the
no-reading exits, and one live read on the running system."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "mem_check.py")
sys.path.insert(0, os.path.dirname(HERE))

import mem_check  # noqa: E402

GIB = 1024 ** 3
KIB = 1024

# Lines copied from a real /proc/meminfo (Linux 6.18 under WSL2); values replaced per case.
MEMINFO = ("MemTotal:       10186856 kB\nMemFree:         9198352 kB\nMemAvailable:    {available} kB\n"
           "SwapTotal:       3145728 kB\nCommitLimit:     {limit} kB\nCommitted_AS:    {committed} kB\n")
# vm_stat's shape on macOS 14 (Apple silicon: 16 KiB pages); values replaced per case.
VM_STAT = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
           "Pages free:                               {free}.\n"
           "Pages active:                            301210.\n"
           "Pages inactive:                          {inactive}.\n"
           "Pages speculative:                         2312.\n"
           "Pages throttled:                              0.\n"
           "Pages wired down:                        120044.\n"
           "{purgeable}"
           "\"Translation faults\":                 912345678.\n"
           "Pages copy-on-write:                   12345678.\n")


def reading(committed, limit=95, available=6.25):
    """The three Windows fields in GiB; the fractions are exact, so a boundary is exact."""
    return json.dumps({"CommittedBytes": int(committed * GIB), "CommitLimit": int(limit * GIB),
                       "AvailableBytes": int(available * GIB)})


def events(*limits):
    return json.dumps([int(value * GIB) for value in limits])


EVENTS_127 = events(127.125)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mem-check-test-")
        self.config = os.path.join(self.tmp, "config")
        self.project = os.path.join(self.tmp, "project")
        self.core = os.path.join(self.tmp, "core")
        for folder in (self.config, self.project, self.core):
            os.makedirs(folder)
        self.environ = {"CLAUDE_CONFIG_DIR": self.config, "CLAUDE_PROJECT_DIR": self.project}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *argv, system="windows", environ=None, **kwargs):
        out = io.BytesIO()
        args = (["--system", system] if system else []) + list(argv)
        code = mem_check.main(args, environ=self.environ if environ is None else environ, out=out,
                              core_root=self.core, **kwargs)
        return code, out.getvalue().decode("utf-8")

    def windows(self, committed, limit=95, available=6.25, ev=EVENTS_127, *extra):
        argv = list(extra) + ["--reading-json", reading(committed, limit, available)]
        if ev is not None:
            argv += ["--event-limits-json", ev]
        return self.run_main(*argv)

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def level(self, number, load):
        """Writes `load` as the load block of config level `number`; returns the extra environ for level 2."""
        data = {"schema": 1, "load": load}
        if number == 1:
            self.write_json(os.path.join(self.core, "rigger-defaults.json"), data)
        elif number == 2:
            module = os.path.join(self.tmp, "company")
            self.write_json(os.path.join(module, ".claude-plugin", "plugin.json"), {"name": "acme-rules"})
            self.write_json(os.path.join(module, "rigger-module.json"),
                            {"schema": 1, "name": "acme-rules", "layer": "company", "config": data})
            return {"RIGGER_MODULE_DIRS": module}
        elif number == 3:
            self.write_json(os.path.join(self.config, "rigger", "config.json"), data)
        else:
            self.write_json(os.path.join(self.project, ".claude", "rigger.json"), data)
        return {}


class Windows(Case):
    """The 13 cases of mem-check.tests.ps1, through the same two seams."""

    def test_headroom_above_the_minimum_prints_every_value_in_gib(self):
        self.assertEqual(self.windows(90), (0, "mem-check: committed 90.0 GiB, limit 95.0 GiB, ceiling 127.13 GiB "
                                                "(max of 1 Event 2004 entries), headroom 37.13 GiB (min 30 GiB), "
                                                "available RAM 6.3 GiB, max heavy agents 2: OK\n"))

    def test_headroom_below_the_minimum_exits_1(self):
        code, text = self.windows(100)
        self.assertEqual(code, 1)
        self.assertRegex(text, r"headroom 27\.13 GiB .*: BUSY\n$")

    def test_headroom_exactly_at_the_minimum_passes_and_one_byte_less_fails(self):
        code, text = self.windows(97, ev=events(127))
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"headroom 30\.00 GiB .*: OK\n$")
        under = json.dumps({"CommittedBytes": 97 * GIB + 1, "CommitLimit": 95 * GIB, "AvailableBytes": GIB})
        self.assertEqual(self.run_main("--reading-json", under, "--event-limits-json", events(127))[0], 1)

    def test_min_free_gb_moves_the_boundary_both_ways(self):
        code, text = self.windows(100, 95, 6.25, EVENTS_127, "--min-free-gb", "10")
        self.assertEqual(code, 0, text)
        self.assertIn("(min 10 GiB)", text)
        self.assertEqual(self.windows(90, 95, 6.25, EVENTS_127, "--min-free-gb", "40")[0], 1)
        code, text = self.windows(126, 95, 6.25, EVENTS_127, "--min-free-gb", "0")
        self.assertEqual(code, 0, "an explicit 0 is a minimum, not the default")
        self.assertIn("(min 0 GiB)", text)

    def test_no_event_2004_entries_falls_back_to_the_commit_limit_and_says_so(self):
        code, text = self.windows(175, 200, ev="[]")
        self.assertEqual(code, 1)
        self.assertRegex(text, r"(?m)^mem-check: ceiling falls back to the current CommitLimit "
                               r"\(no Event 2004 entries\)$")
        self.assertIn("ceiling 200.00 GiB (current CommitLimit, fallback), headroom 25.00 GiB", text)
        self.assertEqual(self.windows(160, 200, ev="[]")[0], 0)

    def test_the_default_minimum_is_a_quarter_of_the_ceiling_capped_at_30_gib(self):
        code, text = self.windows(9, 38, ev="[]")
        self.assertEqual(code, 0, "an idle 32 GB machine: 29 GiB headroom against 9.5")
        self.assertIn("(min 9.5 GiB)", text)
        code, text = self.windows(15, 19, ev="[]")
        self.assertEqual(code, 1, "a loaded 16 GB machine: 4 GiB headroom against 4.75")
        self.assertRegex(text, r"\(min 4\.75 GiB\).*: BUSY\n$")
        code, text = self.windows(75, 100, ev="[]")
        self.assertEqual(code, 0, "headroom exactly 25% of the ceiling")
        self.assertIn("(min 25 GiB)", text)
        self.assertIn("(min 30 GiB)", self.windows(90)[1])
        self.assertIn("(min 10 GiB)", self.windows(10, 95, 6.25, EVENTS_127, "--ceiling-gb", "40")[1])
        self.assertEqual(self.windows(9, 38, 6.25, "[]", "--min-free-gb", "30")[0], 1)

    def test_an_unreadable_event_log_falls_back_to_the_commit_limit_and_says_so(self):
        code, text = self.windows(60, 100, ev="not json")
        self.assertEqual(code, 0)
        self.assertRegex(text, r"(?m)^mem-check: ceiling falls back to the current CommitLimit "
                               r"\(event log read failed: .+\)$")
        self.assertIn("ceiling 100.00 GiB (current CommitLimit, fallback)", text)

    def test_the_ceiling_is_the_maximum_of_several_entries_wherever_it_sits(self):
        for order in ((110, 127.125, 100), (127.125, 100, 110), (100, 110, 127.125)):
            code, text = self.windows(90, ev=events(*order))
            self.assertIn("ceiling 127.13 GiB (max of 3 Event 2004 entries), headroom 37.13 GiB", text, order)
            self.assertNotIn("falls back", text, order)

    def test_ceiling_gb_overrides_the_events(self):
        code, text = self.windows(90, 95, 6.25, EVENTS_127, "--ceiling-gb", "110")
        self.assertEqual(code, 1, "override 110 under events 127 leaves 20 GiB")
        self.assertIn("ceiling 110.00 GiB (--ceiling-gb override), headroom 20.00 GiB", text)
        code, text = self.windows(90, 95, 6.25, events(100), "--ceiling-gb", "127")
        self.assertEqual(code, 0)
        self.assertIn("ceiling 127.00 GiB (--ceiling-gb override)", text)
        self.assertNotIn("falls back", self.windows(90, 95, 6.25, "[]", "--ceiling-gb", "127")[1])

    def test_an_unusable_reading_fails_closed_with_exit_2(self):
        rows = [('{"CommittedBytes":1,"CommitLimit":2}', "missing AvailableBytes"),
                ("not json", None),
                ('{"CommittedBytes":"1","CommitLimit":2,"AvailableBytes":3}', "bad CommittedBytes"),
                ('{"CommittedBytes":true,"CommitLimit":2,"AvailableBytes":3}', "bad CommittedBytes"),
                ('{"CommittedBytes":-1,"CommitLimit":2,"AvailableBytes":3}', "bad CommittedBytes"),
                ('{"CommittedBytes":1,"CommitLimit":0,"AvailableBytes":3}', "CommitLimit is 0"),
                ("", None), ("[]", None)]
        for seam, why in rows:
            with self.subTest(seam=seam):
                code, text = self.run_main("--reading-json", seam, "--event-limits-json", EVENTS_127)
                self.assertEqual(code, 2, text)
                self.assertRegex(text, r"^mem-check: no reading \(.+\)\n$")
                if why:
                    self.assertIn("no reading (%s)" % why, text)

    def test_numbers_print_with_a_dot_and_half_up(self):
        code, text = self.windows(40.5, 95, 0.5, EVENTS_127, "--min-free-gb", "2.5")
        self.assertRegex(text, r"committed 40\.5 GiB, .*\(min 2\.5 GiB\), available RAM 0\.5 GiB, "
                               r"max heavy agents 2: OK\n$")

    def test_out_of_range_options_are_refused_not_run(self):
        for bad in (("--ceiling-gb", "0"), ("--min-free-gb", "-1"), ("--min-free-gb", "1025"),
                    ("--min-free-gb", "x"), ("--system", "beos")):
            with self.subTest(bad=bad):
                code, text = self.windows(10, 95, 6.25, EVENTS_127, *bad)
                self.assertEqual(code, 2, text)
                self.assertRegex(text, r"^mem-check: refused: ")
                self.assertNotIn("headroom", text)

    def test_the_native_reading_and_the_event_query(self):
        """Without seams: GetPerformanceInfo pages times the page size, and wevtutil's SystemCommitLimit values."""
        page = 4096
        info = mock.Mock(CommitTotal=90 * GIB // page, CommitLimit=95 * GIB // page,
                         PhysicalAvailable=6 * GIB // page, PageSize=page)
        xml = ("<Event><UserData><MemoryExhaustionInfo><SystemInfo><SystemCommitLimit>%d</SystemCommitLimit>"
               "</SystemInfo></MemoryExhaustionInfo></UserData></Event>" % (127 * GIB)) * 2
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, xml.encode("utf-8"), b""))
        with mock.patch.object(mem_check, "performance_info", return_value=info):
            code, text = self.run_main(run=run)
        self.assertEqual(code, 0, text)
        self.assertIn("committed 90.0 GiB, limit 95.0 GiB, ceiling 127.00 GiB (max of 2 Event 2004 entries), "
                      "headroom 37.00 GiB", text)
        self.assertEqual(run.call_args[0][0][:3], ["wevtutil", "qe", "System"])
        failed = mock.Mock(return_value=subprocess.CompletedProcess([], 5, b"", b"denied"))
        with mock.patch.object(mem_check, "performance_info", return_value=info):
            code, text = self.run_main(run=failed)
        self.assertIn("ceiling falls back to the current CommitLimit (event log read failed: ", text)
        with mock.patch.object(mem_check, "performance_info", side_effect=OSError(5, "denied")):
            code, text = self.run_main(run=run)
        self.assertEqual(code, 2)
        self.assertRegex(text, r"^mem-check: no reading \(GetPerformanceInfo: .+\)\n$")


class Linux(Case):
    def meminfo(self, available, limit, committed):
        return json.dumps({"meminfo": MEMINFO.format(available=available, limit=limit, committed=committed)})

    def test_the_smaller_of_available_memory_and_commit_headroom_counts(self):
        # available 6 GiB, commit headroom 10 GiB: available counts
        code, text = self.run_main("--reading-json", self.meminfo(6 * GIB // KIB, 30 * GIB // KIB, 20 * GIB // KIB),
                                   system="linux")
        self.assertEqual((code, text), (0, "mem-check: available 6.0 GiB, commit headroom 10.0 GiB, headroom 6.00 GiB "
                                           "(min 4 GiB), max heavy agents 2: OK\n"))
        # available 9 GiB, commit headroom 3 GiB: the commit headroom counts and is under the 4 GiB default
        code, text = self.run_main("--reading-json", self.meminfo(9 * GIB // KIB, 23 * GIB // KIB, 20 * GIB // KIB),
                                   system="linux")
        self.assertEqual(code, 1, text)
        self.assertIn("headroom 3.00 GiB (min 4 GiB), max heavy agents 2: BUSY", text)

    def test_exactly_at_the_minimum_passes_and_one_kib_less_fails(self):
        at = self.meminfo(4 * GIB // KIB, 30 * GIB // KIB, 1)
        self.assertEqual(self.run_main("--reading-json", at, system="linux")[0], 0)
        under = self.meminfo(4 * GIB // KIB - 1, 30 * GIB // KIB, 1)
        self.assertEqual(self.run_main("--reading-json", under, system="linux")[0], 1)

    def test_a_negative_commit_headroom_is_busy(self):
        code, text = self.run_main("--reading-json", self.meminfo(9 * GIB // KIB, 10 * GIB // KIB, 12 * GIB // KIB),
                                   system="linux")
        self.assertEqual(code, 1, text)
        self.assertIn("commit headroom -2.0 GiB, headroom -2.00 GiB", text)

    def test_an_unreadable_or_incomplete_meminfo_exits_2(self):
        missing = json.dumps({"meminfo": "MemTotal: 1 kB\nMemAvailable: 5 kB\nCommitLimit: 9 kB\n"})
        self.assertEqual(self.run_main("--reading-json", missing, system="linux"),
                         (2, "mem-check: no reading (/proc/meminfo has no Committed_AS)\n"))
        bad = json.dumps({"meminfo": MEMINFO.format(available="x", limit=1, committed=1)})
        self.assertEqual(self.run_main("--reading-json", bad, system="linux"),
                         (2, "mem-check: no reading (/proc/meminfo MemAvailable is not a number of kB)\n"))
        other_unit = json.dumps({"meminfo": MEMINFO.format(available="5 MB", limit=1, committed=1)})
        self.assertEqual(self.run_main("--reading-json", other_unit, system="linux"),
                         (2, "mem-check: no reading (/proc/meminfo MemAvailable is not a number of kB)\n"))
        code, text = self.run_main(system="linux", meminfo_path=os.path.join(self.tmp, "no-such-meminfo"))
        self.assertEqual(code, 2)
        self.assertRegex(text, r"^mem-check: no reading \(cannot read .*no-such-meminfo: .+\)\n$")


class MacOS(Case):
    def vm_stat(self, free, inactive, purgeable=None):
        line = "" if purgeable is None else "Pages purgeable:                          %d.\n" % purgeable
        return VM_STAT.format(free=free, inactive=inactive, purgeable=line)

    def test_free_inactive_and_purgeable_pages_times_the_page_size(self):
        pages = GIB // 16384   # pages per GiB at 16 KiB
        seam = json.dumps({"vm_stat": self.vm_stat(2 * pages, pages, pages), "pagesize": "16384\n"})
        self.assertEqual(self.run_main("--reading-json", seam, system="macos"),
                         (0, "mem-check: free 2.0 GiB, inactive 1.0 GiB, purgeable 1.0 GiB, headroom 4.00 GiB "
                             "(min 4 GiB), max heavy agents 2: OK\n"))
        seam = json.dumps({"vm_stat": self.vm_stat(2 * pages, pages, pages - 1), "pagesize": "16384"})
        self.assertEqual(self.run_main("--reading-json", seam, system="macos")[0], 1)

    def test_a_vm_stat_without_purgeable_counts_it_as_zero(self):
        pages = GIB // 4096
        seam = json.dumps({"vm_stat": self.vm_stat(5 * pages, pages), "pagesize": "4096"})
        code, text = self.run_main("--reading-json", seam, system="macos")
        self.assertEqual(code, 0, text)
        self.assertIn("purgeable 0.0 GiB, headroom 6.00 GiB", text)

    def test_the_commands_are_run_and_their_failure_exits_2(self):
        pages = GIB // 16384
        outputs = {"vm_stat": self.vm_stat(8 * pages, pages, 0), "sysctl": "16384\n"}

        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, outputs[argv[0]].encode("utf-8"), b"")

        code, text = self.run_main(system="macos", run=run)
        self.assertEqual(code, 0, text)
        self.assertIn("headroom 9.00 GiB", text)
        for broken in ({"vm_stat": "Pages active: 3.\n"}, {"pagesize": "0"}, {"pagesize": "big"}):
            with self.subTest(broken=broken):
                seam = dict({"vm_stat": self.vm_stat(1, 1, 1), "pagesize": "4096"}, **broken)
                code, text = self.run_main("--reading-json", json.dumps(seam), system="macos")
                self.assertEqual(code, 2, text)
                self.assertRegex(text, r"^mem-check: no reading \(.+\)\n$")

        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, b"", b"vm_stat: nope")

        code, text = self.run_main(system="macos", run=failing)
        self.assertEqual(code, 2)
        self.assertRegex(text, r"^mem-check: no reading \(vm_stat exited 1\)\n$")


class Config(Case):
    """load.min_free_gb and load.max_heavy_agents from config levels 1-4 (D80)."""

    def linux(self, available_gib, *argv, environ=None):
        seam = json.dumps({"meminfo": MEMINFO.format(available=int(available_gib * GIB // KIB),
                                                     limit=100 * GIB // KIB, committed=1)})
        return self.run_main("--reading-json", seam, *argv, system="linux", environ=environ)

    def test_each_level_sets_the_limits(self):
        for number in (1, 2, 3, 4):
            with self.subTest(level=number):
                shutil.rmtree(self.tmp)
                self.setUp()
                extra = self.level(number, {"min_free_gb": 6, "max_heavy_agents": 1})
                code, text = self.linux(5, environ=dict(self.environ, **extra))
                self.assertEqual(code, 1, text)
                self.assertTrue(text.endswith("headroom 5.00 GiB (min 6 GiB), max heavy agents 1: BUSY\n"), text)

    def test_the_per_system_defaults_apply_when_no_level_sets_a_key(self):
        self.level(3, {"max_heavy_agents": 3})
        self.assertTrue(self.linux(5)[1].endswith("(min 4 GiB), max heavy agents 3: OK\n"))
        self.level(3, {"min_free_gb": 2.5})
        self.assertTrue(self.linux(5)[1].endswith("(min 2.5 GiB), max heavy agents 2: OK\n"))
        self.level(3, {"min_free_gb": 10})
        code, text = self.windows(120)
        self.assertEqual(code, 1, "a level-3 minimum replaces the Windows default too: 7.13 GiB against 10")
        self.assertEqual((self.windows(110)[0], "(min 10 GiB)" in self.windows(110)[1]), (0, True))

    def test_the_project_may_only_make_a_limit_stricter(self):
        self.level(3, {"min_free_gb": 6, "max_heavy_agents": 2})
        self.level(4, {"min_free_gb": 3, "max_heavy_agents": 4})
        code, text = self.linux(5)
        project = os.path.join(self.project, ".claude", "rigger.json")
        self.assertEqual((code, text.splitlines()), (1, [
            "mem-check: project config %s load.min_free_gb 3 is looser than 6; ignored" % project,
            "mem-check: project config %s load.max_heavy_agents 4 is looser than 2; ignored" % project,
            "mem-check: available 5.0 GiB, commit headroom 100.0 GiB, headroom 5.00 GiB (min 6 GiB), "
            "max heavy agents 2: BUSY"]))
        self.level(4, {"min_free_gb": 8, "max_heavy_agents": 1})
        self.assertTrue(self.linux(7)[1].endswith("(min 8 GiB), max heavy agents 1: BUSY\n"))
        # against the per-system default when no lower level sets the key
        os.remove(os.path.join(self.config, "rigger", "config.json"))
        self.level(4, {"min_free_gb": 3, "max_heavy_agents": 2})
        code, text = self.linux(3.5)
        self.assertEqual(code, 1, text)
        self.assertIn("load.min_free_gb 3 is looser than 4; ignored", text)
        self.level(4, {"min_free_gb": 4, "max_heavy_agents": None})
        self.assertEqual(self.linux(4), (0, "mem-check: available 4.0 GiB, commit headroom 100.0 GiB, headroom "
                                            "4.00 GiB (min 4 GiB), max heavy agents 2: OK\n"))

    def test_the_command_line_minimum_wins_over_the_config(self):
        self.level(3, {"min_free_gb": 6})
        self.assertTrue(self.linux(5, "--min-free-gb", "2")[1].endswith("(min 2 GiB), max heavy agents 2: OK\n"))

    def test_a_bad_config_value_keeps_the_default_with_its_notice(self):
        self.level(3, {"min_free_gb": "6", "max_heavy_agents": 9})
        code, text = self.linux(5)
        lines = text.splitlines()
        self.assertEqual(len(lines), 3, text)
        self.assertIn("load.min_free_gb must be a number from 0 to 1024", lines[0])
        self.assertIn("load.max_heavy_agents must be a whole number from 1 to 8", lines[1])
        self.assertEqual(lines[2][-len("(min 4 GiB), max heavy agents 2: OK"):], "(min 4 GiB), max heavy agents 2: OK")
        self.assertEqual(code, 0)

    def test_a_config_that_does_not_load_keeps_the_defaults(self):
        os.makedirs(os.path.join(self.project, ".claude"))
        with open(os.path.join(self.project, ".claude", "rigger.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        code, text = self.linux(5)
        self.assertEqual(code, 0, text)
        self.assertTrue(text.endswith("(min 4 GiB), max heavy agents 2: OK\n"), text)


class Json(Case):
    def test_json_output_carries_the_verdict_and_exit_code(self):
        code, text = self.run_main("--json", "--reading-json", reading(100), "--event-limits-json", EVENTS_127)
        self.assertEqual(code, 1)
        data = json.loads(text)
        self.assertEqual((data["state"], data["headroom_bytes"], data["min_free_gb"], data["max_heavy_agents"],
                          data["system"]), ("BUSY", int(27.125 * GIB), 30, 2, "windows"))
        code, text = self.run_main("--json", "--reading-json", "{}", "--event-limits-json", EVENTS_127)
        self.assertEqual((code, json.loads(text)["state"]), (2, "NO_READING"))


class Live(unittest.TestCase):
    def test_the_live_read_of_this_system(self):
        system = mem_check.this_system()
        if system is None:
            self.skipTest("no reading tool on %s" % sys.platform)
        if system == "linux" and not os.path.isfile("/proc/meminfo"):
            self.skipTest("no /proc/meminfo")
        if system == "macos" and not shutil.which("vm_stat"):
            self.skipTest("no vm_stat")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("CLAUDE_", "RIGGER_"))}
        empty = tempfile.mkdtemp(prefix="mem-check-live-")
        try:
            env.update(CLAUDE_CONFIG_DIR=empty, CLAUDE_PROJECT_DIR=empty)
            proc = subprocess.run([sys.executable, "-I", SCRIPT], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60)
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        text = proc.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(proc.stderr, b"")
        self.assertNotIn(b"\r\n", proc.stdout)
        found = re.search(r"headroom (-?[\d.]+) GiB \(min ([\d.]+) GiB\)(?:, available RAM [\d.]+ GiB)?, "
                          r"max heavy agents (\d): (OK|BUSY)\n$", text)
        self.assertTrue(found, text)
        headroom, minimum, _agents, state = found.groups()
        self.assertEqual(proc.returncode, 1 if float(headroom) < float(minimum) else 0, text)
        self.assertEqual(state, "BUSY" if proc.returncode else "OK")
        if system == "windows" and shutil.which("pwsh"):
            cim = subprocess.run(["pwsh", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).CommittedBytes"],
                                 stdout=subprocess.PIPE, timeout=60)
            committed = float(re.search(r"committed ([\d.]+) GiB", text).group(1))
            self.assertLess(abs(committed - int(cim.stdout) / GIB), 1.0, "committed within 1 GiB of the CIM read")


if __name__ == "__main__":
    unittest.main()
