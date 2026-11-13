"""Tests for tools/inbox_lint.py (the provenance fields of lessons inbox entries) and hooks/inbox_guard.py.

Every inbox is synthetic and written under a temp dir.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN / "tools" / "inbox_lint.py"
GUARD = PLUGIN / "hooks" / "inbox_guard.py"
HEAD = "- 2026-01-01 · global · demo · case · rule"
OLD_BAD = "- 2025-12-31 · global · demo · old case · signal_type: review finding\n"  # as entries in the wild are
FIELDS = {
    "signal_type": "test-failure",
    "source_ref": "s#t",
    "target": "rules/x.md",
    "why": "a stated reason",
}


def entry(**overrides):
    """One field-bearing entry line; a value None drops the field, '' leaves it empty."""
    fields = dict(FIELDS)
    fields.update(overrides)
    return HEAD + "".join(" · %s: %s" % (k, v) for k, v in fields.items() if v is not None) + "\n"


def dated(day, signal="test-failure", tag="", curated=None):
    """A field-bearing entry dated 2026-01-<day>; signal None is an old entry with no fields at all."""
    head = "- 2026-01-%02d · global · demo · case%s · rule" % (day, tag)
    if signal is None:
        line = head
    else:
        line = head + " · signal_type: %s · source_ref: s#t · target: rules/x.md · why: a reason" % signal
    return line + (" · curated: %s" % curated if curated else "") + "\n"


class InboxLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="inbox-lint-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def lint(self, content):
        """(exit code, stdout lines) of the CLI on an inbox holding `content` (str or bytes)."""
        path = self.tmp / "inbox.md"
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        path.write_bytes(data)
        return self.run_cli(path)

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(SCRIPT)] + [str(a) for a in args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.tmp),
            timeout=60,
        )
        self.assertEqual(proc.stderr, b"")
        return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n").splitlines()

    def problems(self, content):
        code, lines = self.lint(content)
        found = [l for l in lines if not l.startswith("# ")]
        self.assertEqual(code, 1 if found else 0, lines)
        return found

    def test_planted_valid_entry_is_counted_and_passes(self):
        self.assertEqual(self.lint(entry() + entry(review="2027-02-01")), (0, ["# 2 entries checked, 2 with fields, 0 problems"]))

    def test_old_entries_are_counted_not_failed(self):
        old = HEAD + "\n- 2025-12-31 · project:x · x · what happened · the rule\n"
        self.assertEqual(self.lint(old + entry()), (0, ["# 3 entries checked, 1 with fields, 0 problems"]))

    def test_unknown_signal_type_fails_with_its_line_number(self):
        found = self.problems("# Inbox\n" + entry(signal_type="guess"))
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].endswith(":2: signal_type must be one of user-correction, test-failure, review-finding, agent-observation, got 'guess'"), found)

    def test_every_known_signal_type_passes(self):
        for kind in ("user-correction", "test-failure", "review-finding", "agent-observation"):
            with self.subTest(kind=kind):
                self.assertEqual(self.problems(entry(signal_type=kind)), [])

    def test_empty_or_missing_required_field_fails(self):
        for name in ("signal_type", "source_ref", "target", "why"):
            for label, value in (("empty", ""), ("blank", "  "), ("missing", None)):
                with self.subTest(field=name, value=label):
                    found = self.problems(entry(**{name: value}))
                    self.assertEqual(len(found), 1, found)
                    self.assertTrue(found[0].endswith(":1: %s is missing or empty" % name), found)

    def test_bad_review_date_fails(self):
        for value in ("2027-13-99", "2027-02-30", "tomorrow", "2027-2-1", "20270201", "", "2027-02-01x"):
            with self.subTest(review=value):
                found = self.problems(entry(review=value))
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].endswith(":1: review is not a YYYY-MM-DD date: %r" % value), found)

    def test_review_is_optional_and_a_real_date_passes(self):
        self.assertEqual(self.problems(entry()), [])
        self.assertEqual(self.problems(entry(review="2028-02-29")), [])

    def test_a_repeated_field_is_checked_each_time(self):
        for label, first, second in (("empty last", "s#t", ""), ("empty first", "", "s#t")):
            with self.subTest(order=label):
                line = entry(source_ref=first).rstrip("\n") + " · source_ref: %s\n" % second
                self.assertEqual(len(self.problems(line)), 1)

    def test_crlf_inbox(self):
        self.assertEqual(self.problems((entry() + entry(why="")).replace("\n", "\r\n")), ["%s:2: why is missing or empty" % (self.tmp / "inbox.md")])
        self.assertEqual(self.lint(entry(review="2027-02-01").replace("\n", "\r\n")), (0, ["# 1 entries checked, 1 with fields, 0 problems"]))
        self.assertEqual(self.lint(dated(2, curated="2026-02-01").replace("\n", "\r\n")), (0, ["# 1 entries checked, 1 with fields, 0 problems"]))

    def test_non_utf8_byte_is_not_a_problem(self):
        data = entry().encode("utf-8").replace(b"case", b"caf\xe9 \xff")
        self.assertEqual(self.lint(data), (0, ["# 1 entries checked, 1 with fields, 0 problems"]))

    def test_lines_that_are_not_entries_are_ignored(self):
        text = (
            "  - 2026-01-01 · global · demo · case · rule · signal_type: guess\n"
            "note · why:\n"
            "- 26-01-01 · global · demo · case · rule · signal_type: guess\n"
            "- 2026-01-01 · global · demo · what happened why: because · rule\n"
        )
        self.assertEqual(self.lint(text + entry()), (0, ["# 2 entries checked, 1 with fields, 0 problems"]))

    def test_an_entry_without_its_dash_is_a_problem(self):
        dashless = HEAD[2:] + "\n"
        found = self.problems("# Inbox\n" + entry() + dashless)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].endswith(":3: entry without `- `"), found)
        self.assertEqual(self.problems(entry() + HEAD + "\n"), [])

    def test_a_dashless_line_is_caught_among_the_other_non_entries(self):
        text = (
            "  " + HEAD[2:] + "\n"
            "2026-01-01 no separator\n"
            "26-01-01 · global\n"
            + HEAD[2:] + " · signal_type: guess\n"
        )
        found = self.problems(text + entry())
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].endswith(":4: entry without `- `"), found)

    def test_no_entries_fails(self):
        for label, text in (("empty", ""), ("prose", "# Inbox\n\nSome text, signal_type: guess\n"), ("indented", "  " + entry())):
            with self.subTest(inbox=label):
                self.assertEqual(self.lint(text), (1, ["# no entries in %s" % (self.tmp / "inbox.md")]))

    def test_missing_inbox_exits_2(self):
        code, lines = self.run_cli(self.tmp / "absent.md")
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("# cannot read "), lines)

    def test_directory_as_inbox_exits_2(self):
        self.assertEqual(self.run_cli(self.tmp)[0], 2)

    def test_wrong_argument_count_exits_2(self):
        self.assertEqual(self.run_cli()[0], 2)

    def test_problems_are_all_reported(self):
        found = self.problems(entry(why="") + entry(signal_type="x", review="nope"))
        self.assertEqual(len(found), 3, found)

    def test_a_bad_curated_date_fails(self):
        for value in ("soon", "2026-13-01", ""):
            with self.subTest(curated=value):
                found = self.problems(entry().rstrip("\n") + " · curated: %s\n" % value)
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].endswith(":1: curated is not a YYYY-MM-DD date: %r" % value), found)


    # -- slice ----------------------------------------------------------------------------

    def slice(self, content):
        """(entry lines without their line-number prefix, summary line) of a slice run that exits 0."""
        path = self.tmp / "inbox.md"
        path.write_bytes(content.encode("utf-8"))
        code, lines = self.run_cli("--slice", path)
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[-1].startswith("# slice: "), lines)
        return [line.split(": ", 1)[1] for line in lines[:-1]], lines[-1]

    def days(self, content):
        return [int(line[10:12]) for line in self.slice(content)[0]]

    def test_slice_prints_line_numbers_of_the_inbox(self):
        path = self.tmp / "inbox.md"
        path.write_bytes(("# Inbox\n\n" + dated(2)).encode("utf-8"))
        self.assertEqual(self.run_cli("--slice", path)[1][0], "3: " + dated(2).rstrip("\n"))

    def test_slice_fills_up_with_the_oldest_of_the_rest(self):
        rest = "".join(dated(day, "agent-observation") for day in range(20, 0, -1))
        self.assertEqual(self.days(rest + dated(28) + dated(29)), [28, 29] + list(range(1, 14)))

    def test_every_kind_of_signal_that_is_not_evidence_comes_after_the_evidence(self):
        for kind in ("agent-observation", "review finding", "wrong assumption in a plan", "", None):
            with self.subTest(signal=kind):
                self.assertEqual(self.days(dated(1, kind) + dated(2)), [2, 1])

    def test_a_repeated_signal_type_counts_only_when_every_value_is_evidence(self):
        mixed = dated(1).rstrip("\n") + " · signal_type: agent-observation\n"
        self.assertEqual(self.days(mixed + dated(2)), [2, 1])

    def test_equal_dates_keep_their_inbox_order(self):
        entries = self.slice(dated(5, tag=" b") + dated(5, tag=" a"))[0]
        self.assertEqual([line.split(" · ")[3] for line in entries], ["case b", "case a"])
        parked = dated(5, tag=" b", curated="2026-02-01") + dated(4, tag=" a", curated="2026-02-01")
        self.assertEqual([line.split(" · ")[3] for line in self.slice(parked)[0]], ["case b", "case a"])

    def test_curated_entries_do_not_block_the_next_slice(self):
        parked = "".join(dated(1, curated="2026-02-01") for _ in range(15))
        waiting = "".join(dated(day, "agent-observation") for day in range(10, 26))
        entries, summary = self.slice(parked + waiting)
        self.assertEqual([int(line[10:12]) for line in entries], list(range(10, 25)))
        self.assertEqual(summary, "# slice: 15 of 31 entries, 0 with evidence, 16 without, 15 curated before")

    def test_a_bad_curated_date_parks_nothing(self):
        self.assertEqual(self.days(dated(1, curated="soon") + dated(2)), [1, 2])
        prose = dated(1).replace("a reason", "curated: 2026-02-01")
        self.assertEqual(self.problems(prose), [])
        self.assertEqual(self.days(prose + dated(2)), [1, 2])

    def test_an_entry_past_its_review_date_comes_first(self):
        def waiting(review, curated=None):
            return dated(9, "agent-observation", curated=curated).rstrip("\n") + " · review: %s\n" % review

        with mock.patch.dict(os.environ, HARNESS_LESSONS_TODAY="2026-06-15"):
            for review, curated, days in (
                ("2026-06-14", None, [9, 2]), ("2026-06-15", None, [9, 2]), ("2026-06-16", None, [2, 9]),
                ("2026-06-01", "2026-05-01", [9, 2]), ("2026-06-01", "2026-06-01", [2, 9]),  # curated after the date: seen, parked
            ):
                with self.subTest(review=review, curated=curated):
                    self.assertEqual(self.days(waiting(review, curated) + dated(2)), days)
        self.assertEqual(self.days(waiting("2000-01-01") + dated(2)), [9, 2])  # no override: the clock

    def test_slice_of_a_crlf_inbox_prints_no_carriage_return(self):
        path = self.tmp / "inbox.md"
        path.write_bytes(dated(2).replace("\n", "\r\n").encode("utf-8"))
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(SCRIPT), "--slice", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        out = proc.stdout.replace(b"\r\n", b"\n") if os.name == "nt" else proc.stdout  # print adds a CR on Windows
        self.assertNotIn(b"\r", out)

    def test_slice_with_no_entries_exits_1(self):
        path = self.tmp / "inbox.md"
        path.write_bytes(b"# Inbox\n")
        self.assertEqual(self.run_cli("--slice", path), (1, ["# no entries in %s" % path]))


class InboxGuardTests(unittest.TestCase):
    """hooks/inbox_guard.py, run as py.sh runs it; the inbox is a fixture named by the env override."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="inbox-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inbox = self.tmp / "lessons-inbox.md"
        self.inbox.write_bytes(("# Inbox\n\n## Entries\n" + OLD_BAD).encode("utf-8"))

    def run_guard(self, stdin, **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["HARNESS_LESSONS_INBOX"] = str(self.inbox)
        env.update(extra)
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-S", str(GUARD)],
            input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(self.tmp), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout

    def edit(self, new, old="## Entries", path=None, **extra):
        event = {"tool_name": "Edit", "cwd": str(self.tmp),
                 "tool_input": {"file_path": str(path or self.inbox), "old_string": old, "new_string": new}}
        return self.run_guard(json.dumps(event).encode("utf-8"), **extra)

    def write(self, content, path=None):
        event = {"tool_name": "Write", "cwd": str(self.tmp),
                 "tool_input": {"file_path": str(path or self.inbox), "content": content}}
        return self.run_guard(json.dumps(event).encode("utf-8"))

    def reason(self, out):
        self.assertNotEqual(out, b"", "the guard allowed what it must deny")
        data = json.loads(out.decode("utf-8"))
        self.assertEqual(list(data), ["hookSpecificOutput"])
        output = data["hookSpecificOutput"]
        self.assertEqual(sorted(output), ["hookEventName", "permissionDecision", "permissionDecisionReason"])
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        return output["permissionDecisionReason"]

    def test_an_added_entry_with_no_fields_is_denied_naming_each_field_and_the_signal_types(self):
        reason = self.reason(self.edit("## Entries\n" + HEAD + "\n"))
        for name in ("signal_type", "source_ref", "target", "why"):
            self.assertIn("%s is missing or empty" % name, reason)
        self.assertIn("user-correction, test-failure, review-finding, agent-observation", reason)
        self.assertIn("demo · case · rule", reason)

    def test_only_the_added_entry_of_an_edit_is_named(self):
        reason = self.reason(self.edit(OLD_BAD + "- 2026-02-02 · global · demo · fresh · rule\n", old=OLD_BAD))
        self.assertIn("fresh", reason)
        self.assertNotIn("old case", reason)

    def test_two_entries_changed_in_one_edit_add_nothing(self):
        self.inbox.write_bytes((OLD_BAD + HEAD + "\n").encode("utf-8"))
        old = OLD_BAD + HEAD + "\n"
        self.assertEqual(self.edit(old.replace("case", "sharper case"), old=old), b"")

    def test_a_change_and_an_addition_in_one_edit_check_the_addition(self):
        new = OLD_BAD.replace("old case", "sharper case") + "- 2026-02-02 · global · demo · fresh · rule\n"
        reason = self.reason(self.edit(new, old=OLD_BAD))
        self.assertIn("fresh", reason)
        self.assertNotIn("sharper", reason)

    def test_an_edit_that_leaves_no_new_bad_line_is_allowed(self):
        old, good = OLD_BAD.rstrip("\n"), entry().rstrip("\n")
        for inbox, before, after in (
            (old, "review finding", "review finding\n" + good),  # the tail of an entry as the anchor
            (old, "- 2025-12-31", good + "\n- 2025-12-31"),  # its date
            (old + "\n" + HEAD, "finding\n- 2026-01-01", "finding · curated: 2026-02-01\n- 2026-01-01"),  # two fragments
            (old, old, good + "\n" + old.replace("old case", "sharper case")),  # the change comes after the addition
            (old, "not in the file", "- 2026-02-02 · fresh"),  # this Edit changes nothing
        ):
            with self.subTest(before=before, after=after):
                self.inbox.write_bytes(inbox.encode("utf-8"))
                self.assertEqual(self.edit(after, old=before), b"")

    def test_an_edit_that_leaves_a_new_bad_line_is_denied(self):
        crlf = OLD_BAD.replace("\n", "\r\n")
        for inbox, before, after, problem in (
            ("## Entries\r\n" + crlf, "## Entries\n", "## Entries\n- 2026-02-02 · fresh\n", "why is missing or empty"),
            ("## Entries\n" + OLD_BAD, "## Entries", "## Entries\n- 2026-01-02", "why is missing or empty"),
            ("## Entries\n" + OLD_BAD, "## Entries", "## Entries\n" + entry()[2:], "entry without `- `"),
        ):
            with self.subTest(after=after):
                self.inbox.write_bytes(inbox.encode("utf-8"))
                self.assertIn(problem, self.reason(self.edit(after, old=before)))

    def test_write_of_a_new_inbox_is_checked(self):
        self.inbox.unlink()
        self.assertIn("why is missing or empty", self.reason(self.write(entry(why=None))))
        self.assertEqual(self.write("# Inbox\n"), b"")

    def test_a_crlf_inbox_and_edit_compare_as_lines(self):
        self.inbox.write_bytes(OLD_BAD.replace("\n", "\r\n").encode("utf-8"))
        added = (OLD_BAD + entry()).replace("\n", "\r\n")
        self.assertEqual(self.edit(added, old=OLD_BAD.replace("\n", "\r\n")), b"")
        self.assertEqual(self.write(added), b"")
        self.assertEqual(self.write(OLD_BAD + entry()), b"")
        self.inbox.write_bytes((chr(0xFEFF) + OLD_BAD).encode("utf-8"))
        self.assertEqual(self.write(OLD_BAD), b"")

    def test_every_spelling_of_the_inbox_path_is_checked(self):
        spellings = [self.inbox.as_posix(), str(self.tmp / "sub" / ".." / "lessons-inbox.md")]
        if os.name == "nt":  # paths compare case-insensitively here; on POSIX another case is another file
            spellings += [str(self.inbox).upper(), str(self.inbox).lower()]
        for spelling in spellings:
            with self.subTest(path=spelling):
                self.assertIn(b"deny", self.edit("## Entries\n" + HEAD + "\n", path=spelling))

    def test_a_relative_path_is_checked(self):
        self.assertIn(b"deny", self.edit("## Entries\n" + HEAD + "\n", path="lessons-inbox.md"))

    def test_a_link_to_the_inbox_is_checked(self):
        link = self.tmp / "link" / "lessons-inbox.md"
        link.parent.mkdir()
        try:
            os.symlink(str(self.inbox), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink on %s: %s" % (sys.platform, exc))
        self.assertIn(b"deny", self.edit("## Entries\n" + HEAD + "\n", path=link))

    def test_a_file_of_the_same_name_that_is_not_the_inbox_is_allowed(self):
        other = self.tmp / "templates" / "lessons-inbox.md"
        other.parent.mkdir()
        other.write_bytes(b"# Template\n")
        self.assertEqual(self.edit("## Entries\n" + HEAD + "\n", path=other), b"")

    def test_the_default_inbox_is_the_one_in_the_config_dir(self):
        config = self.tmp / "cfg"
        config.mkdir()
        (config / "lessons-inbox.md").write_bytes(b"## Entries\n")
        stdin = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(config / "lessons-inbox.md"),
                                                                "old_string": "## Entries",
                                                                "new_string": "## Entries\n" + HEAD + "\n"}})
        self.assertIn(b"deny", self.run_guard(stdin.encode("utf-8"), HARNESS_LESSONS_INBOX="", CLAUDE_CONFIG_DIR=str(config)))

    def test_any_wrong_input_allows_silently(self):
        tool_inputs = (
            ["a"], "text", 5, None,
            {"file_path": 5, "old_string": "a", "new_string": HEAD},
            {"file_path": ["x"], "old_string": "a", "new_string": HEAD},
            {"file_path": str(self.inbox), "old_string": "a", "new_string": 5},
            {"file_path": str(self.inbox), "old_string": "a", "new_string": ["- 2026-01-01"]},
            {"file_path": str(self.inbox), "old_string": 5, "new_string": HEAD},
            {"file_path": str(self.inbox), "old_string": "a"},
        )
        stdins = [b"", b"{", b"[]", b"5", b'"text"', b"null", b"{}", b"\xff\xfe"]
        stdins += [json.dumps({"tool_name": "Edit", "tool_input": item}).encode("utf-8") for item in tool_inputs]
        stdins += [json.dumps({"tool_name": 5, "tool_input": tool_inputs[6]}).encode("utf-8")]
        stdins += [json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(self.inbox), "content": ["x"]}}).encode("utf-8")]
        for stdin in stdins:
            with self.subTest(stdin=stdin):
                self.assertEqual(self.run_guard(stdin), b"")

    def test_an_unreadable_inbox_allows_silently(self):
        self.inbox.unlink()
        self.inbox.mkdir()
        self.assertEqual(self.edit("## Entries\n" + HEAD + "\n"), b"")

    def test_a_write_full_of_bad_entries_stays_under_the_output_cap_with_lf_only(self):
        out = self.write("".join(HEAD + " %d\n" % number for number in range(500)))
        self.assertLess(len(out), 2000)
        self.assertNotIn(b"\r", out)
        self.assertIn("and 497 more", self.reason(out))

    def test_end_to_end_through_the_hooks_json_commands(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn("PreToolUse", hooks)
        data = hooks["PreToolUse"]
        self.assertEqual([group["matcher"] for group in data], ["Edit|Write"])
        handlers = data[0]["hooks"]
        self.assertEqual([handler["if"] for handler in handlers], ["Edit(*lessons-inbox.md)", "Write(*lessons-inbox.md)"])
        sh = shutil.which("sh") if os.name != "nt" else str(Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe"))
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env.update({"HARNESS_LESSONS_INBOX": str(self.inbox), "CLAUDE_PLUGIN_ROOT": PLUGIN.as_posix(),
                    "CLAUDE_PLUGIN_DATA": (self.tmp / "data").as_posix(),
                    "PATH": os.pathsep.join((str(Path(sys.executable).parent), str(Path(sh).parent), os.environ["PATH"]))})
        bad = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(self.inbox), "old_string": "## Entries",
                                                              "new_string": "## Entries\n" + HEAD + "\n"}}).encode("utf-8")
        for handler in handlers:
            with self.subTest(condition=handler["if"]):
                self.assertTrue(handler["command"].endswith(" || true"), handler["command"])
                self.assertEqual(handler["timeout"], 10)
                command = handler["command"].replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN.as_posix())
                proc = subprocess.run([sh, "-c", command], input=bad, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=env, cwd=str(self.tmp), timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("why is missing or empty", self.reason(proc.stdout))


if __name__ == "__main__":
    unittest.main(verbosity=2)
