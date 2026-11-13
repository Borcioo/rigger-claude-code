"""Tests for rules/rules_extra.sh, which prints the user's rules.d folder; each case runs a real POSIX sh."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN / "rules" / "rules_extra.sh"
START_SCRIPT = PLUGIN / "rules" / "rules_start.sh"
COMMAND = 'sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_extra.sh"'
CAP = 9000
WORKER = b'{"agent_type":"rigger-core:worker","hook_event_name":"SubagentStart"}'
HOSTILE = 'back\\slash "quoted" \\u0041 tab\there äöüß →\ntrailing backslash\\\n"\n\\\\\n'


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def agent_list(path):
    match = re.search(r"^  for agent in (.+); do$", path.read_text(encoding="utf-8"), re.M)
    return match.group(1).split() if match else []


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rules-extra-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sh = find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")
        self.config = self.tmp / "config dir"
        self.folder = self.config / "rigger" / "rules.d"

    def write(self, files, folder=None):
        folder = folder or self.folder
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (folder / name).write_bytes(text.encode("utf-8"))

    def run_extra(self, args="", stdin=b"", **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "LC_"))}
        env["HOME"] = env["USERPROFILE"] = str(self.tmp)
        env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PLUGIN_ROOT"] = PLUGIN.as_posix()
        env["LANG"] = env["LC_ALL"] = "en_US.UTF-8"
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        proc = subprocess.run(
            [self.sh, "-c", (COMMAND + " " + args).strip()],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self.tmp),
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertNotIn(b"\r", proc.stdout)
        return proc.stdout

    def text(self, **extra):
        return self.run_extra(**extra).decode("utf-8")

    def context(self, stdin=WORKER, **extra):
        out = self.run_extra("--subagent", stdin=stdin, **extra)
        self.assertTrue(out.endswith(b"}\n"), out)
        try:
            data = json.loads(out.decode("utf-8"))
        except ValueError as error:
            self.fail("not valid JSON (%s): %r" % (error, out[:200]))
        self.assertEqual(list(data), ["hookSpecificOutput"])
        self.assertEqual(sorted(data["hookSpecificOutput"]), ["additionalContext", "hookEventName"])
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        return data["hookSpecificOutput"]["additionalContext"]


class RulesExtraTests(Base):
    def test_prints_files_sorted(self):
        self.write({"b.md": "b\n", "a.md": "a\n", "Z.md": "Z\n", "10.md": "ten\n", "9.md": "nine\n",
                    "note.txt": "not markdown\n", "c.md.bak": "backup\n"})
        expected = "ten\nnine\nZ\na\nb\n"
        self.assertEqual(self.text(), expected)
        self.assertEqual(self.context(), expected)

    def test_default_folder_is_under_home(self):
        self.write({"a.md": "from home\n"}, folder=self.tmp / ".claude" / "rigger" / "rules.d")
        self.assertEqual(self.text(CLAUDE_CONFIG_DIR=None), "from home\n")

    @unittest.skipUnless(os.name == "nt", "a backslash config path is a Windows spelling")
    def test_windows_config_dir_with_backslashes(self):
        self.write({"a.md": "windows\n"})
        self.assertEqual(self.text(CLAUDE_CONFIG_DIR=str(self.config).replace("/", "\\")), "windows\n")

    def test_main_files_skipped_for_subagent(self):
        self.write({"a.md": "a\n", "a-main.md": "a main\n", "z-main.md": "z main\n", "main.md": "plain main\n"})
        self.assertEqual(self.text(), "a main\na\nplain main\nz main\n")
        self.assertEqual(self.context(), "a\nplain main\n")

    def test_skip_list_agent_gets_nothing(self):
        agents = agent_list(SCRIPT)
        self.assertEqual(agents, agent_list(START_SCRIPT))
        self.assertIn("Explore", agents)
        self.write({"a.md": "text\n"})
        for agent in agents:
            for form in ('{"agent_type":"%s"}', '{"agent_id":"a1", "agent_type": "%s", "cwd":"x"}'):
                with self.subTest(agent=agent, form=form):
                    self.assertEqual(self.run_extra("--subagent", stdin=(form % agent).encode("utf-8")), b"")
        for stdin in (WORKER, b'{"agent_type":"explore"}', b'{"agent_type":"my:Explore"}', b"{}", b""):
            with self.subTest(stdin=stdin):
                self.assertEqual(self.context(stdin=stdin), "text\n")

    def test_missing_folder_prints_nothing(self):
        self.assertEqual(self.run_extra(), b"")
        self.assertEqual(self.run_extra("--subagent", stdin=WORKER), b"")
        self.folder.mkdir(parents=True)
        self.assertEqual(self.run_extra(), b"")
        self.assertEqual(self.run_extra("--subagent", stdin=WORKER), b"")

    def test_empty_file_prints_nothing(self):
        self.write({"a.md": "", "b.md": "\n\n", "c.md": "c\n"})
        self.assertEqual(self.text(), "c\n")
        self.write({"c.md": ""})
        self.assertEqual(self.run_extra("--subagent", stdin=WORKER), b"")

    def test_crlf_file_printed_without_cr(self):
        self.write({"a.md": "one\r\ntwo\r\n\r\nlast", "b.md": "next\r\n"})
        expected = "one\ntwo\n\nlast\nnext\n"
        self.assertEqual(self.text(), expected)
        self.assertEqual(self.context(), expected)

    def test_name_with_space(self):
        self.config = self.tmp / "with space" / "cfg"
        self.folder = self.config / "rigger" / "rules.d"
        self.write({"my rules.md": "spaced\n", "a b-main.md": "spaced main\n"})
        self.assertEqual(self.text(), "spaced main\nspaced\n")
        self.assertEqual(self.context(), "spaced\n")

    def test_subagent_output_is_valid_json(self):
        self.write({"a.md": HOSTILE + "bell\x07 escape\x1b[0m formfeed\x0c nul\x00 end\n", "b.md": "second\n"})
        expected = HOSTILE + "bell escape[0m formfeed nul end\nsecond\n"
        self.assertEqual(self.context(), expected)
        self.assertEqual(self.text(), expected)

    def test_unreadable_file_reported(self):
        self.write({"a.md": "a\n", "z.md": "z\n"})
        (self.folder / "b.md").mkdir()
        expected = ["a", "rules_extra: cannot read b.md"]
        if os.name != "nt":
            (self.folder / "c.md").symlink_to(self.folder / "gone")
            expected.append("rules_extra: cannot read c.md")
        expected.append("z")
        self.assertEqual(self.text(), "\n".join(expected) + "\n")
        self.assertEqual(self.context(), "\n".join(expected) + "\n")

    @unittest.skipIf(os.name == "nt", "Windows has no POSIX read bit to clear")
    def test_file_without_read_bits_reported(self):
        if os.geteuid() == 0:
            self.skipTest("root reads a file whatever its mode bits; the folder and dangling-link cases cover root")
        self.write({"a.md": "a\n", "d.md": "secret\n"})
        path = self.folder / "d.md"
        path.chmod(path.stat().st_mode & ~0o444)
        self.addCleanup(path.chmod, 0o644)
        self.assertEqual(path.stat().st_mode & 0o444, 0)
        self.assertEqual(self.text(), "a\nrules_extra: cannot read d.md\n")

    def test_over_cap_folder_leaves_out_and_reports(self):
        body = "é" * 499 + "x\n"  # 1,000 bytes, 501 characters
        files = {"f%02d.md" % n: body for n in range(1, 13)}
        files["f13 last.md"] = "tiny\n"
        self.write(files)
        out = self.run_extra()
        report = b"rules_extra: left out over the hook cap: f10.md, f11.md, f12.md, f13 last.md\n"
        self.assertEqual(out, body.encode("utf-8") * 9 + report)
        self.assertLessEqual(len(out) - len(report), CAP)

        context = self.context().encode("utf-8")
        report = b"rules_extra: left out over the hook cap: f09.md, f10.md, f11.md, f12.md, f13 last.md\n"
        self.assertEqual(context, body.encode("utf-8") * 8 + report)
        self.assertLessEqual(len(self.run_extra("--subagent", stdin=WORKER)) - len(report), CAP + 100)

    def test_exactly_at_cap_is_printed_whole(self):
        self.write({"a.md": "x" * (CAP - 1) + "\n"})
        self.assertEqual(self.run_extra(), b"x" * (CAP - 1) + b"\n")
        self.write({"b.md": "y\n"})
        self.assertTrue(self.text().endswith("\nrules_extra: left out over the hook cap: b.md\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
