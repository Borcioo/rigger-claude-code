"""Tests for scripts/sweep.py: built-in rules, denylist folding, allowlist, git sources, exit codes."""

import builtins
import codecs
import contextlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sweep.py"
sys.path.insert(0, str(ROOT / "scripts"))

import sweep  # noqa: E402

FINDING_RE = re.compile(r"^(.*):(\d+): ([a-z0-9-]+): (.*)$")

# Planted values are assembled at runtime so this file stays clean under the repo's own sweep.
GHP_TOKEN = "gh" + "p_" + "x" * 36
GHO_TOKEN = "gh" + "o_" + "x" * 36
GHU_TOKEN = "gh" + "u_" + "x" * 36
GHS_TOKEN = "gh" + "s_" + "x" * 36
GHR_TOKEN = "gh" + "r_" + "x" * 36
GLPAT_TOKEN = "gl" + "pat-" + "x" * 24
GOOGLE_KEY = "AI" + "za" + "x" * 35
DRIVE = "C" + ":"
DRIVE_BACKSLASH = DRIVE + "\\Us" + "ers\\realname\\"
DRIVE_SLASH = DRIVE + "/Us" + "ers/realname/"
MAC_HOME = "/Us" + "ers/realname/"
DRIVELESS_HOME = "\\Us" + "ers\\realname\\x"
LINUX_HOME = "/ho" + "me/realname/"
WSL_MOUNT = "/m" + "nt/c/"
UNC = "\\" + "\\fileserver" + "\\share"
EMAIL = "jane.roe" + "@" + "client-co" + ".d" + "e"
CC_DOMAIN = "client-co" + ".d" + "e"
IPV4 = ".".join(("10", "1", "2", "3"))
SK_TOKEN = "s" + "k-" + "a" * 24
PAT_TOKEN = "github" + "_pat_" + "b" * 24
AWS_KEY = "AK" + "IA" + "C" * 16
SLACK_TOKEN = "xo" + "xb-" + "1" * 12
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
# A fictional surname: the denylist side and the text side differ in diacritics and case.
TERM_PLAIN = "bron" + "dalle"
TERM_MARKED = "Br\u00f8n" + "d\u00e1\u0142le"
TEXT_PLAIN = "BRON" + "DALLE"
TEXT_MARKED = "BR\u00d8N" + "D\u00c1\u0141LE"
ALL_TLDS = ("com", "net", "org", "io", "dev", "info", "biz", "app", "me", "ai", "co")
ALL_TLDS += ("eu", "de", "uk", "fr", "nl", "it", "es", "cz", "pl")
ALL_BINARY_EXTENSIONS = "avif eot gif gz ico jpeg jpg mo mp3 mp4 otf pdf phar png pyc ttf webm webp woff woff2 zip"
# Regex escapes are assembled at runtime so no editor or tool can turn them into the character itself.
BACKSLASH = "\\"
HEX_DIGITS = "0123456789abcdefABCDEF"
# Neighbours of the hex ranges, plus an Arabic-Indic digit that int() would accept.
NOT_HEX = ("g", "G", "/", ":", "@", "`", chr(0x663))


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def remove_tree(path):
    def retry(func, target, _info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(str(path), onerror=retry)


def deny_open(path):
    """Patch `open` so that opening `path` (and nothing else) raises PermissionError."""
    real_open = builtins.open
    blocked = os.path.normcase(str(path))

    def fake_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and os.path.normcase(os.fspath(file)) == blocked:
            raise PermissionError(13, "denied", os.fspath(file))
        return real_open(file, *args, **kwargs)

    return mock.patch.object(builtins, "open", fake_open)


class SweepTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sweep-test-")).resolve()
        self.addCleanup(remove_tree, self.tmp)
        empty_config = self.tmp / "gitconfig"
        write_text(empty_config, "")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("HARNESS_", "GIT_"))}
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
            GIT_CEILING_DIRECTORIES=str(self.tmp),
        )
        self.env = env

    def run_sweep(self, *args, script=SCRIPT, env=None, timeout=60, cwd=None):
        proc = subprocess.run(
            [sys.executable, "-I", str(script)] + list(args),
            cwd=str(cwd or self.tmp),
            env=self.env if env is None else env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.decode("utf-8").replace("\r\n", "\n")

    def denylist(self, *lines):
        path = self.tmp / "denylist.txt"
        write_text(path, "".join(line + "\n" for line in lines))
        return path

    def make_tree(self, files, name="tree"):
        tree = self.tmp / name
        if tree.exists():
            remove_tree(tree)
        tree.mkdir()
        for rel, content in files.items():
            if isinstance(content, bytes):
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).write_bytes(content)
            else:
                write_text(tree / rel, content)
        return tree

    def make_repo(self, files, tracked=()):
        """A git repo holding a copy of the script, so the script's default mode scans it."""
        repo = self.make_tree(files, name="repo")
        script = repo / "scripts" / "sweep.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(SCRIPT), str(script))
        self.git(repo, "init", "-q")
        if tracked:
            self.git(repo, "add", "--", *tracked)
        return script

    def git(self, repo, *args, data=None):
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=str(repo),
            env=self.env,
            input=data if data is not None else b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=60,
        )
        return proc.stdout.decode("utf-8").strip()

    def symlink(self, target, link, directory=False):
        try:
            os.symlink(target, str(link), target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create a symlink here: %s" % type(exc).__name__)

    def findings(self, output, prefix=""):
        found = []
        for line in output.splitlines():
            match = FINDING_RE.match(line)
            if match and not line.startswith("#"):
                path = match.group(1)
                if prefix and path.startswith(prefix):
                    path = path[len(prefix) :]
                found.append((path, int(match.group(2)), match.group(3), match.group(4)))
        return found

    def scan_tree(self, files, *args):
        tree = self.make_tree(files)
        if not args:
            args = ("--no-denylist",)
        code, output = self.run_sweep(*(args + (str(tree),)))
        return code, output, self.findings(output, prefix=str(tree) + "/")

    def scan_repo(self, script, *args):
        code, output = self.run_sweep(*(args or ("--no-denylist",)), script=script)
        return code, output, self.findings(output)

    def assert_caught(self, text, rule):
        code, output, found = self.scan_tree({"notes.txt": "first line\nsee " + text + " here\n"})
        self.assertEqual(code, 1, output)
        self.assertIn(("notes.txt", 2, rule), [item[:3] for item in found], output)
        self.assertIn("# files 1, findings ", output)

    def main_in_process(self, *argv):
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = sweep.main(list(argv))
        except Exception as exc:
            self.fail("main() let %s escape" % type(exc).__name__)
        return code, buffer.getvalue()


class ControlTests(SweepTestCase):
    def test_denylist_term_with_diacritics_and_other_case(self):
        cases = (
            ("marked term, plain text", TERM_MARKED, TEXT_PLAIN),
            ("plain term, marked text", TERM_PLAIN, TEXT_MARKED),
            ("only the stroke letter differs", "\u0142odyga", "LODYGA"),
            ("a slashed o", "\u00f8resund", "ORESUND"),
            ("a stroked d and h", "\u0111a\u0127ar", "DAHAR"),
            ("regex escapes keep their case", "acme\\Wcorp", "ACME-CORP"),
        )
        for label, term, text in cases:
            with self.subTest(label):
                deny = self.denylist("# comment line", "", term)
                code, output, found = self.scan_tree(
                    {"notes.txt": "first line\nsee → " + text + " here\n"}, "--denylist", str(deny)
                )
                self.assertEqual(code, 1, output)
                self.assertIn("# denylist terms 1\n", output)
                self.assertEqual([item[:3] for item in found], [("notes.txt", 2, "denylist")], output)
                # Output is UTF-8 even without -X utf8 (the arrow has no cp1250 code).
                self.assertIn("→ " + text, found[0][3])

    def test_denylist_excerpt_maps_back_to_the_original_text(self):
        deny = self.denylist(TERM_PLAIN)
        # Each sharp s folds to two characters, so folded positions run ahead of the original.
        line = "ß" * 120 + " " + TEXT_MARKED + " " + "x" * 100
        code, output, found = self.scan_tree({"notes.txt": line + "\n"}, "--denylist", str(deny))
        self.assertEqual(code, 1, output)
        self.assertEqual(len(found), 1, output)
        self.assertIn(TEXT_MARKED, found[0][3])
        self.assertLessEqual(len(found[0][3]), 80)

    def test_ghp_token(self):
        self.assert_caught(GHP_TOKEN, "token")

    def test_windows_user_path(self):
        self.assert_caught(DRIVE_BACKSLASH, "drive-path")
        self.assert_caught(DRIVE_SLASH, "drive-path")

    def test_mac_user_path(self):
        self.assert_caught(MAC_HOME, "user-path")

    def test_email(self):
        self.assert_caught(EMAIL, "email")

    def test_country_domain(self):
        self.assert_caught("https://" + CC_DOMAIN + "/page", "host")

    def test_file_name_with_denylist_term(self):
        deny = self.denylist(TERM_MARKED)
        name = "notes-" + TEXT_PLAIN.title() + ".txt"
        script = self.make_repo({"README.md": "clean\n", name: "clean\n"})
        code, output, found = self.scan_repo(script, "--denylist", str(deny))
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [(name, 0, "denylist")], output)

    def test_non_utf8_file_is_a_finding(self):
        code, output, found = self.scan_tree({"bad.txt": b"ok\n\xff\xfe bad\n", "fine.txt": "fine\n"})
        self.assertEqual(code, 1, output)
        self.assertEqual(found, [("bad.txt", 2, "encoding", "not UTF-8: byte 0xff at offset 3")], output)
        self.assertIn("# files 2, findings 1\n", output)

    def test_allowlisted_text_suppressed_other_finding_reported(self):
        allow = "# comment\ndocs/a.md\t" + IPV4 + "\n"
        script = self.make_repo(
            {
                "scripts/sweep-allow.txt": allow,
                "docs/a.md": "ssh " + IPV4 + "\nssh " + IPV4 + " " + AWS_KEY + "\nuse " + AWS_KEY + "\n",
                "docs/b.md": "ssh " + IPV4 + "\n",
            },
            tracked=("docs/a.md", "scripts/sweep-allow.txt"),
        )
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [("docs/a.md", 2, "token"), ("docs/a.md", 3, "token"), ("docs/b.md", 1, "ipv4")],
            output,
        )

    def test_stale_allow_lines_reported(self):
        allow = "docs/a.md\tgone text\ndocs/missing.md\tanything\ndocs/a.md\tclean words\n"
        script = self.make_repo({"scripts/sweep-allow.txt": allow, "docs/a.md": "clean words\n"})
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            found,
            [
                ("scripts/sweep-allow.txt", 1, "stale-allow", "text not found: docs/a.md"),
                ("scripts/sweep-allow.txt", 2, "stale-allow", "file not scanned: docs/missing.md"),
                ("scripts/sweep-allow.txt", 3, "stale-allow", "suppresses no finding: docs/a.md"),
            ],
            output,
        )

    def test_missing_unreadable_or_empty_denylist_exits_2(self):
        tree = self.make_tree({"README.md": "clean\n"})
        missing = str(self.tmp / "no-such-denylist.txt")
        empty = self.denylist("# only a comment", "   ")
        not_utf8 = self.tmp / "latin2.txt"
        not_utf8.write_bytes(b"\xb3odz\n")
        directory = self.tmp / "a-directory"
        directory.mkdir()
        cases = (
            ("no flag, no env", (), {}, "# error: no denylist"),
            ("flag to a missing file", ("--denylist", missing), {}, "# error: denylist not found"),
            ("env to a missing file", (), {"HARNESS_SWEEP_DENYLIST": missing}, "# error: denylist not found"),
            ("empty env", (), {"HARNESS_SWEEP_DENYLIST": ""}, "# error: no denylist"),
            ("zero terms", ("--denylist", str(empty)), {}, "# error: denylist has no terms"),
            ("not UTF-8", ("--denylist", str(not_utf8)), {}, "# error: denylist is not UTF-8"),
            ("a directory", ("--denylist", str(directory)), {}, "# error: denylist unreadable"),
        )
        for label, args, extra, message in cases:
            with self.subTest(label):
                code, output = self.run_sweep(*(args + (str(tree),)), env=dict(self.env, **extra))
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)
                self.assertNotIn("# files", output)

    def test_empty_tree_exits_2(self):
        with self.subTest("path mode, empty directory"):
            empty = self.tmp / "empty"
            empty.mkdir()
            code, output = self.run_sweep("--no-denylist", str(empty))
            self.assertEqual(code, 2, output)
            self.assertIn("# files 0, findings 0\n", output)
        with self.subTest("repo mode, everything ignored"):
            script = self.make_repo({".gitignore": "*\n"})
            code, output = self.run_sweep("--no-denylist", script=script)
            self.assertEqual(code, 2, output)
            self.assertIn("# files 0, findings 0\n", output)

    def test_clean_tree_exits_0(self):
        deny = self.denylist(TERM_PLAIN)
        script = self.make_repo({"README.md": "clean, see https://example.com\n"}, tracked=("README.md",))
        code, output = self.run_sweep("--denylist", str(deny), script=script)
        self.assertEqual(code, 0, output)
        self.assertIn("# denylist terms 1\n", output)
        self.assertIn("# files 2, findings 0\n", output)

    def test_utf8_bom_in_denylist_and_allowlist_is_ignored(self):
        bom = codecs.BOM_UTF8
        deny = self.tmp / "bom-denylist.txt"
        with self.subTest("denylist: BOM before the first term"):
            deny.write_bytes(bom + (TERM_PLAIN + "\n").encode("utf-8"))
            code, output, found = self.scan_tree({"notes.txt": "see " + TEXT_PLAIN + "\n"}, "--denylist", str(deny))
            self.assertEqual(code, 1, output)
            self.assertIn("# denylist terms 1\n", output)
            self.assertEqual([item[:3] for item in found], [("notes.txt", 1, "denylist")], output)
        with self.subTest("denylist: BOM before a comment line"):
            deny.write_bytes(bom + ("# comment\n" + TERM_PLAIN + "\n").encode("utf-8"))
            code, output, found = self.scan_tree({"notes.txt": "see " + TEXT_PLAIN + "\n"}, "--denylist", str(deny))
            self.assertEqual(code, 1, output)
            self.assertIn("# denylist terms 1\n", output)
            self.assertEqual([item[:3] for item in found], [("notes.txt", 1, "denylist")], output)
        with self.subTest("allowlist: BOM before the first path"):
            allow = bom + ("docs/a.md\t" + IPV4 + "\n").encode("utf-8")
            script = self.make_repo({"scripts/sweep-allow.txt": allow, "docs/a.md": "ssh " + IPV4 + "\n"})
            code, output, found = self.scan_repo(script)
            self.assertEqual((code, found), (0, []), output)
            self.assertIn("# files 3, findings 0\n", output)


class RuleTests(SweepTestCase):
    def test_other_rule_families(self):
        cases = (
            (LINUX_HOME, "user-path"),
            (WSL_MOUNT, "wsl-path"),
            (UNC, "unc-path"),
            (IPV4, "ipv4"),
            (DRIVELESS_HOME, "user-path"),
            (SK_TOKEN, "token"),
            (GHO_TOKEN, "token"),
            (GHU_TOKEN, "token"),
            (GHS_TOKEN, "token"),
            (GHR_TOKEN, "token"),
            (GLPAT_TOKEN, "token"),
            (GOOGLE_KEY, "token"),
            (PAT_TOKEN, "token"),
            (AWS_KEY, "token"),
            (SLACK_TOKEN, "token"),
            (PRIVATE_KEY, "token"),
            ("Client-Co.Example" + ".eu", "host"),
        )
        for text, rule in cases:
            with self.subTest(rule=rule, text=text[:12]):
                self.assert_caught(text, rule)

    def test_path_rules_ignore_case(self):
        lines = (
            ("/us" + "ers/realname/", "user-path"),
            ("/US" + "ERS/realname", "user-path"),
            ("/Ho" + "me/realname", "user-path"),
            ("c" + ":" + "\\" + "work" + "\\x", "drive-path"),
            ("/MN" + "T/C/x", "wsl-path"),
        )
        code, output, found = self.scan_tree({"notes.txt": "".join("see %s here\n" % text for text, _ in lines)})
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found], [("notes.txt", number, rule) for number, (_, rule) in enumerate(lines, 1)], output
        )

    def test_driveless_users_path_forms(self):
        lines = (
            ("double backslashes", DRIVELESS_HOME.replace("\\", "\\\\"), ["unc-path", "user-path"]),
            ("lower case", DRIVELESS_HOME.lower(), ["user-path"]),
            ("after a variable: not at the start of a path", "$drive" + DRIVELESS_HOME, []),
            ("mixed slashes, backslash first", "\\Us" + "ers/realname/x", ["user-path"]),
            ("mixed slashes, second separator", "\\Us" + "ers\\realname/x", ["user-path"]),
            ("mixed slashes, forward slash first", "/Us" + "ers\\realname\\x", ["user-path"]),
            ("inside a folder name: App", "App\\Us" + "ers\\Profile", []),
            ("below another folder", "src\\Us" + "ers\\realname\\f.txt", []),
            ("after a drive: the drive rule's", DRIVE + DRIVELESS_HOME, ["drive-path"]),
            ("after a doubled drive: not this rule's", DRIVE + DRIVELESS_HOME.replace("\\", "\\\\"), ["drive-path", "unc-path"]),
            ("a folder name that only starts alike", "\\Us" + "ersGuide\\x", []),
            ("nothing after the folder", "\\Us" + "ers\\ end", []),
        )
        for label, text, rules in lines:
            with self.subTest(label):
                code, output, found = self.scan_tree({"notes.txt": "see " + text + "\n"})
                self.assertEqual(sorted(item[2] for item in found), rules, output)
                self.assertEqual(code, 1 if rules else 0, output)

    def test_driveless_users_path_starts_only_where_a_path_starts(self):
        for label, prefix, rules in (
            ("line start", "", ["user-path"]),
            ("whitespace", "see ", ["user-path"]),
            ("double quote", '"', ["user-path"]),
            ("single quote", "'", ["user-path"]),
            ("parenthesis", "(", ["user-path"]),
            ("equals sign", "path=", ["user-path"]),
            ("colon", "list:", ["user-path"]),
            ("letter", "App", []),
            ("digit", "7", []),
            ("dollar variable", "$home", []),
            ("dot", ".", []),
        ):
            with self.subTest(label):
                code, output, found = self.scan_tree({"notes.txt": prefix + DRIVELESS_HOME + "\n"})
                self.assertEqual([item[2] for item in found], rules, output)
                self.assertEqual(code, 1 if rules else 0, output)

    def test_denylist_catches_a_term_in_variable_prefixed_users_paths(self):
        # The user-path rule no longer matches a \Users path after a variable; the denylist must.
        deny = self.denylist(TERM_PLAIN)
        users = BACKSLASH + "Us" + "ers" + BACKSLASH
        end = BACKSLASH + "x\n"
        files = {
            "drive-variable.txt": "$drive" + users + TEXT_PLAIN + end,
            "system-drive.txt": "$env:SystemDrive" + users + TEXT_PLAIN + end,
            "control.txt": "$drive" + users + "someone" + end,
        }
        code, output, found = self.scan_tree(files, "--denylist", str(deny))
        self.assertEqual(code, 1, output)
        denylist_files = sorted(item[0] for item in found if item[2] == "denylist")
        self.assertEqual(denylist_files, ["drive-variable.txt", "system-drive.txt"], output)

    def test_new_tokens_need_their_shape(self):
        for label, text in (
            ("gho prefix, too short", GHO_TOKEN[:20]),
            ("ghu prefix, too short", GHU_TOKEN[:20]),
            ("ghs prefix, too short", GHS_TOKEN[:20]),
            ("ghr prefix, too short", GHR_TOKEN[:20]),
            ("glpat prefix, too short", GLPAT_TOKEN[:20]),
            ("AIza prefix, too short", GOOGLE_KEY[:30]),
            ("gho inside a word", "x" + GHO_TOKEN),
            ("ghu inside a word", "x" + GHU_TOKEN),
            ("ghs inside a word", "x" + GHS_TOKEN),
            ("ghr inside a word", "x" + GHR_TOKEN),
            ("glpat inside a word", "x" + GLPAT_TOKEN),
            ("AIza inside a word", "x" + GOOGLE_KEY),
        ):
            with self.subTest(label):
                code, output, found = self.scan_tree({"notes.txt": "see " + text + "\n"})
                self.assertEqual((code, found), (0, []), output)

    def test_every_tld_is_a_host(self):
        text = "".join("see client-co.%s here\n" % tld for tld in ALL_TLDS)
        code, output, found = self.scan_tree({"notes.txt": text})
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("notes.txt", n, "host") for n in range(1, len(ALL_TLDS) + 1)], output)

    def test_allowed_values_are_not_findings(self):
        text = (
            "local 127.0.0.1 and 0.0.0.0\n"
            "mail someone@example.com\n"
            "see https://github.com/x and code.claude.com, docs.claude.com.\n"
            "https://example.com/x is a URL; System.Net.WebClient is code\n"
            "version 3.11.9 and m12 and m123456 and am1234\n"
        )
        code, output, found = self.scan_tree({"notes.txt": text})
        self.assertEqual((code, found), (0, []), output)
        self.assertIn("# files 1, findings 0\n", output)

    def test_allowed_doc_host_passes_next_to_hosts_that_fail(self):
        lines = (
            "docs https://knip.dev/reference/cli and https://registry.npmjs.org/oxlint\n"
            "other https://" + "unlisted-docs" + ".dev/guide\n"
            "client https://" + CC_DOMAIN + "/page\n"
            "nested https://www." + "knip.dev/x\n"
        )
        code, output, found = self.scan_tree({"notes.txt": lines})
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("notes.txt", n, "host") for n in (2, 3, 4)], output)

    def test_subdomain_of_allowed_domain_is_a_finding(self):
        self.assert_caught("www" + ".example.com", "host")

    def test_the_license_hosts_are_allowed(self):
        # The two hosts the GPL text in LICENSE links to; another subdomain is still a finding.
        gnu = "gnu" + ".org"
        lines = "Inc. <https://fsf" + ".org/>\nsee <https://www." + gnu + "/licenses/>.\nftp." + gnu + "\n"
        code, output, found = self.scan_tree({"LICENSE": lines})
        self.assertEqual([item[:3] for item in found], [("LICENSE", 3, "host")], output)

    def test_long_lines_scan_in_linear_time(self):
        lines = ("a" * 200000, "a" * 100000 + "@" + "b" * 100000, "a." * 100000, "\\" * 200000, "1." * 100000)
        deny = self.denylist(TERM_PLAIN)
        tree = self.make_tree({"long.txt": "\n".join(lines) + "\n"})
        started = time.monotonic()
        try:
            code, output = self.run_sweep("--denylist", str(deny), str(tree), timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("the sweep took more than 10 s on long lines")
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, output)
        self.assertLess(elapsed, 2.0)


class FoldTests(SweepTestCase):
    def assert_term_matches(self, term, text, expected=True):
        try:
            regex = re.compile(sweep.fold_pattern(term))
        except re.error as exc:
            self.fail("folded term does not compile: %s" % exc)
        self.assertEqual(bool(regex.search(sweep.fold(text)[0])), expected, (term, text))

    def test_format_and_nonspacing_characters_are_ignored(self):
        lines = (
            "Bron" + chr(0xAD) + "dalle",  # soft hyphen (Cf)
            "Bron" + chr(0x200B) + "dalle",  # zero-width space (Cf)
            "Bron" + chr(0x34F) + "dalle",  # combining grapheme joiner (Mn, combining class 0)
            "Acme" + chr(0x2122) + " Ltd",  # trade mark sign stays a separate non-word character
        )
        deny = self.denylist(TERM_PLAIN, r"\bacme\b")
        code, output, found = self.scan_tree({"notes.txt": "\n".join(lines) + "\n"}, "--denylist", str(deny))
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("notes.txt", n, "denylist") for n in (1, 2, 3, 4)], output)

    def test_crlf_line_end_is_not_part_of_the_line(self):
        deny = self.denylist(TERM_PLAIN + "$")
        code, output, found = self.scan_tree({"notes.txt": "see " + TEXT_PLAIN + "\r\nnext\r\n"}, "--denylist", str(deny))
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("notes.txt", 1, "denylist")], output)

    def test_pattern_escapes_and_groups_survive_folding(self):
        cases = (
            (r"\x41cme", "ACME", True),
            ("\\" + "u0141odz", "lodz", True),
            (r"\U00000141odz", "LODZ", True),
            (r"\N{LATIN CAPITAL LETTER L WITH STROKE}odz", "Lodz", True),
            (r"\101cme", "acme", True),
            (r"\0101cme", "Acme", False),
            ("(?P<Word>acme)-(?P=Word)", "ACME-acme", True),
            ("(?<!x)acme", "ACME", True),
            ("(?<!x)acme", "XACME", False),
            ("(?i:Acme)(?#Comment)", "acme", True),
            ("[\u0141L]odz", "lodz", True),
            ("[A-Z]cme", "acme", True),
            ("[^\\d]cme", "Acme", True),
            ("ß+", "SSSS", True),
            ("ß{2}", "SSS", False),
            ("a\\.b", "A.B", True),
            ("a\\.b", "AxB", False),
            ("\\x2e", "x", False),
        )
        for term, text, expected in cases:
            with self.subTest(term=term, text=text):
                self.assert_term_matches(term, text, expected)

    def test_unsafe_terms_are_refused(self):
        for term in ("[\u0104-\u017b]x", "[ß]", r"\N{NO SUCH NAME}", r"\x4", "(?Px)", "[0-z]", "[\\d-z]", "ab\\"):
            with self.subTest(term=term):
                try:
                    folded = sweep.fold_pattern(term)
                except sweep.UnsafeTerm:
                    continue
                except Exception as exc:
                    self.fail("fold_pattern raised %s instead of UnsafeTerm" % type(exc).__name__)
                self.fail("fold_pattern accepted it as %r" % folded)

    def test_unsafe_term_exits_2_with_its_line_number(self):
        secret = "unus" + "edterm"
        deny = self.denylist("# comment", TERM_PLAIN, "[\u0104-\u017b]" + secret)
        code, output, _found = self.scan_tree({"README.md": "clean\n"}, "--denylist", str(deny))
        self.assertEqual(code, 2, output)
        self.assertIn("# error: denylist line 3: cannot fold this term safely", output)
        self.assertNotIn(secret, output.lower())

    def test_malformed_escape_in_a_denylist_term_exits_2_with_its_line_number(self):
        secret = "unus" + "edterm"
        cases = (
            ("hex digits, none valid", BACKSLASH + "xZZ"),
            ("hex digits, second invalid", BACKSLASH + "x4G"),
            ("hex digits, first invalid", BACKSLASH + "xG4"),
            ("4-digit code point, invalid digit", BACKSLASH + "u00GZ"),
            ("8-digit code point, invalid digit", BACKSLASH + "U0000014Z"),
            ("name escape, no closing brace", BACKSLASH + "N{LATIN SMALL LETTER LX"),
            ("name escape, no opening brace", BACKSLASH + "NxLATIN SMALL LETTER L}"),
        )
        tree = self.make_tree({"README.md": "clean\n"})
        for label, escape in cases:
            with self.subTest(label):
                deny = self.denylist("# comment", TERM_PLAIN, secret + escape)
                try:
                    code, output = self.run_sweep("--denylist", str(deny), str(tree), timeout=10)
                except subprocess.TimeoutExpired:
                    self.fail("the sweep did not finish: the term made fold_pattern loop")
                self.assertEqual(code, 2, output)
                self.assertIn("# error: denylist line 3: cannot fold this term safely", output)
                self.assertNotIn(secret, output.lower())

    def test_hex_digits_of_a_code_escape_are_exactly_the_hex_alphabet(self):
        for digit in HEX_DIGITS:
            for term in (BACKSLASH + "x" + digit + "0", BACKSLASH + "x0" + digit):
                with self.subTest(accepted=term):
                    try:
                        sweep.fold_pattern(term)
                    except sweep.UnsafeTerm:
                        self.fail("fold_pattern refused a valid hex digit")
        for other in NOT_HEX:
            for term in (BACKSLASH + "x" + other + "0", BACKSLASH + "x0" + other):
                with self.subTest(refused=term):
                    with self.assertRaises(sweep.UnsafeTerm):
                        sweep.fold_pattern(term)

    def test_denylist_term_never_printed_outside_a_finding(self):
        secret = "unus" + "edterm"
        for label, line, expected in (("valid term", secret, 0), ("invalid regex", "(" + secret, 2)):
            with self.subTest(label):
                deny = self.denylist(line)
                code, output, _found = self.scan_tree({"README.md": "clean\n"}, "--denylist", str(deny))
                self.assertEqual(code, expected, output)
                self.assertNotIn(secret, output.lower())

    def test_invalid_denylist_regex_exits_2(self):
        deny = self.denylist("(" + "unclosed")
        code, output, _found = self.scan_tree({"README.md": "clean\n"}, "--denylist", str(deny))
        self.assertEqual(code, 2, output)
        self.assertIn("# error: denylist line 1: invalid regex", output)

    def test_env_denylist_is_used(self):
        deny = self.denylist(TERM_PLAIN)
        tree = self.make_tree({"notes.txt": TEXT_MARKED + "\n"})
        code, output = self.run_sweep(str(tree), env=dict(self.env, HARNESS_SWEEP_DENYLIST=str(deny)))
        self.assertEqual(code, 1, output)
        self.assertIn("# denylist terms 1\n", output)
        self.assertIn(":1: denylist: ", output)


class AllowlistTests(SweepTestCase):
    def test_partial_overlap_does_not_suppress(self):
        allow = "docs/a.md\t" + IPV4 + "\n"
        script = self.make_repo({"scripts/sweep-allow.txt": allow, "docs/a.md": "ssh " + IPV4 + "4\n"})
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [("docs/a.md", 1, "ipv4"), ("scripts/sweep-allow.txt", 1, "stale-allow")],
            output,
        )

    def test_path_allow_must_hold_the_whole_path(self):
        quoted = DRIVE + "\\" + "tools" + "\\x.exe"
        allow = "docs/a.md\t" + DRIVE + "\\\n" + "docs/b.md\t" + quoted + "\n"
        files = {
            "scripts/sweep-allow.txt": allow,
            "docs/a.md": "see " + DRIVE + "\\Us" + "ers\\x\\y here\n",
            "docs/b.md": 'run "' + quoted + '" now\n',
        }
        script = self.make_repo(files)
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [("docs/a.md", 1, "drive-path"), ("scripts/sweep-allow.txt", 1, "stale-allow")],
            output,
        )

    def test_every_path_rule_needs_the_whole_path_in_the_allow_line(self):
        cases = (
            ("user-path", MAC_HOME + "docs/x.md"),
            ("wsl-path", WSL_MOUNT + "work/x.md"),
            ("unc-path", UNC + BACKSLASH + "docs" + BACKSLASH + "x.txt"),
        )
        for rule, path in cases:
            with self.subTest(rule):
                # a.md is allowed everything but the path's last character, b.md the whole path.
                allow = "docs/a.md\t" + path[:-1] + "\n" + "docs/b.md\t" + path + "\n"
                files = {
                    "scripts/sweep-allow.txt": allow,
                    "docs/a.md": "see " + path + " here\n",
                    "docs/b.md": "see " + path + " here\n",
                }
                script = self.make_repo(files)
                code, output, found = self.scan_repo(script)
                self.assertEqual(code, 1, output)
                self.assertEqual(
                    [item[:3] for item in found],
                    [("docs/a.md", 1, rule), ("scripts/sweep-allow.txt", 1, "stale-allow")],
                    output,
                )

    def test_encoding_and_binary_findings_ignore_text_allow_lines(self):
        allow = "bad.txt\tnot UTF-8: byte 0xff at offset 3\nlogo.png\tbinary file (.png)\n"
        script = self.make_repo({"scripts/sweep-allow.txt": allow, "bad.txt": b"ok\n\xff\n", "logo.png": b"fake\n"})
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [
                ("bad.txt", 2, "encoding"),
                ("logo.png", 0, "binary"),
                ("scripts/sweep-allow.txt", 1, "stale-allow"),
                ("scripts/sweep-allow.txt", 2, "stale-allow"),
            ],
            output,
        )

    def test_binary_token_allows_only_binary(self):
        allow = "logo.png\t<binary>\nnotes.txt\t<binary>\n"
        files = {"scripts/sweep-allow.txt": allow, "logo.png": b"\x89PNG\0\0", "notes.txt": "clean\n"}
        script = self.make_repo(files)
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(found, [("scripts/sweep-allow.txt", 2, "stale-allow", "suppresses no finding: notes.txt")], output)

    def test_malformed_or_non_utf8_allow_file_exits_2(self):
        cases = (
            ("no tab", "docs/a.md " + AWS_KEY + "\n", "# error: scripts/sweep-allow.txt:1: expected path<TAB>exact text"),
            ("empty text", "# c\ndocs/a.md\t\n", "# error: scripts/sweep-allow.txt:2: expected path<TAB>exact text"),
            ("empty path", "\t" + AWS_KEY + "\n", "# error: scripts/sweep-allow.txt:1: expected path<TAB>exact text"),
            ("not UTF-8", b"docs/a.md\t\xb3\n", "# error: allowlist is not UTF-8"),
        )
        for label, allow, message in cases:
            with self.subTest(label):
                script = self.make_repo({"scripts/sweep-allow.txt": allow, "docs/a.md": AWS_KEY + "\n"})
                code, output, _found = self.scan_repo(script)
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)


class VendoredTests(SweepTestCase):
    PREFIX = "plugins/rigger-wp/skills/wordpress-router/references/"
    NOISE = "docs https://%s/a mail %s share %s\\x\n" % (CC_DOMAIN, EMAIL, UNC)

    def test_public_noise_passes_inside_the_vendored_folder_only(self):
        inside = self.PREFIX + "skills/wp-x/SKILL.md"
        outside = (
            "plugins/rigger-wp/skills/wordpress-router/SKILL.md",
            "plugins/rigger-wp/skills/wordpress-router/references.md",
            "plugins/rigger-wp/skills/wordpress-router/other/x.md",
            "plugins/rigger-wp/skills/rigger-wp-traps/references/x.md",
            "plugins/rigger-windows/skills/wordpress-router/references/x.md",
            "docs/x.md",
        )
        script = self.make_repo({path: self.NOISE for path in (inside,) + outside})
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertNotIn(inside, [item[0] for item in found], output)
        self.assertEqual(sorted({item[0] for item in found}), sorted(outside), output)
        for path in outside:
            self.assertEqual(sorted({item[2] for item in found if item[0] == path}), ["email", "host", "unc-path"], output)

    def test_vendored_folder_alone_is_clean(self):
        script = self.make_repo({self.PREFIX + "skills/wp-x/SKILL.md": self.NOISE, self.PREFIX + "LICENSE": "GPL\n"})
        code, output, found = self.scan_repo(script)
        self.assertEqual((code, found), (0, []), output)
        self.assertIn("# files 3, findings 0\n", output)

    def test_every_other_rule_still_applies_inside_the_vendored_folder(self):
        planted = (GHP_TOKEN, DRIVE_BACKSLASH, IPV4, AWS_KEY, LINUX_HOME, WSL_MOUNT, TERM_PLAIN)
        text = "".join("line %s here\n" % value for value in planted)
        script = self.make_repo({self.PREFIX + "skills/wp-x/SKILL.md": text})
        code, output, found = self.scan_repo(script, "--denylist", str(self.denylist(TERM_PLAIN)))
        self.assertEqual(code, 1, output)
        self.assertEqual(sorted({item[1] for item in found}), list(range(1, len(planted) + 1)), output)
        self.assertIn("denylist", [item[2] for item in found], output)

    def test_binary_file_inside_the_vendored_folder_is_still_a_finding(self):
        script = self.make_repo({self.PREFIX + "skills/wp-x/logo.png": b"\x89PNG"})
        code, output, found = self.scan_repo(script)
        self.assertEqual((code, [item[2] for item in found]), (1, ["binary"]), output)

    def test_absolute_path_mode_scan_is_not_exempt(self):
        code, output, found = self.scan_tree({self.PREFIX + "skills/wp-x/SKILL.md": self.NOISE})
        self.assertEqual(code, 1, output)
        self.assertEqual(sorted({item[2] for item in found}), ["email", "host", "unc-path"], output)


class BinaryTests(SweepTestCase):
    def test_every_binary_extension_is_a_finding(self):
        extensions = ALL_BINARY_EXTENSIONS.split()
        code, output, found = self.scan_tree({"f." + ext: "fake\n" for ext in extensions})
        self.assertEqual(code, 1, output)
        self.assertEqual(
            sorted(item[:3] for item in found), sorted(("f." + ext, 0, "binary") for ext in extensions), output
        )

    def test_nul_bytes_make_a_file_binary(self):
        files = {
            "utf16.txt": "hello\nworld\n".encode("utf-16-le"),
            "utf16-bom.txt": "hello\n".encode("utf-16"),
            AWS_KEY + ".png": b"fake\n",
        }
        code, output, found = self.scan_tree(files)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [(AWS_KEY + ".png", 0, "token"), (AWS_KEY + ".png", 0, "binary"), ("utf16-bom.txt", 1, "binary"), ("utf16.txt", 1, "binary")],
            output,
        )


class GitSourceTests(SweepTestCase):
    def test_staged_secret_hidden_by_unstaged_edit_is_caught(self):
        script = self.make_repo({"docs/a.md": "token " + GHP_TOKEN + "\n"}, tracked=("docs/a.md",))
        write_text(script.parent.parent / "docs" / "a.md", "clean now\n")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)

    def test_unstaged_secret_in_tracked_file_is_caught(self):
        script = self.make_repo({"docs/a.md": "clean\n"}, tracked=("docs/a.md",))
        write_text(script.parent.parent / "docs" / "a.md", "clean\nuse " + AWS_KEY + "\n")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("docs/a.md", 2, "token")], output)

    def test_same_finding_in_index_and_worktree_reported_once(self):
        script = self.make_repo({"docs/a.md": "use " + AWS_KEY + "\n"}, tracked=("docs/a.md",))
        write_text(script.parent.parent / "docs" / "a.md", "use " + AWS_KEY + "\r\n")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)

    def test_allow_file_entry_lines_are_resolved_in_each_scanned_copy(self):
        other_key = "AK" + "IA" + "D" * 16
        allow = "scripts/sweep-allow.txt"
        entry = "docs/a.md\t" + AWS_KEY + "\n"
        cases = (
            (
                "an unstaged edit shifts the entry up: the staged entry passes, the staged comment at its old number fails",
                "# header\n# see " + other_key + "\n" + entry,
                "# header\n" + entry,
                {},
                [(allow, 2, "token")],
            ),
            (
                "a staged entry the working tree dropped is checked against the index, so it is stale",
                entry + "docs/b.md\t" + other_key + "\n",
                entry,
                {},
                [(allow, 2, "stale-allow")],
            ),
            (
                "the same text as an entry line, in another file, fails",
                entry,
                entry,
                {"docs/b.md": entry},
                [("docs/b.md", 1, "token")],
            ),
        )
        for label, staged, worktree, extra, expected in cases:
            with self.subTest(label):
                files = dict({allow: staged, "docs/a.md": "use " + AWS_KEY + "\n"}, **extra)
                script = self.make_repo(files, tracked=(allow, "docs/a.md"))
                write_text(script.parent.parent / allow, worktree)
                code, output, found = self.scan_repo(script)
                self.assertEqual(code, 1, output)
                self.assertEqual([item[:3] for item in found], expected, output)

    def test_index_blob_is_judged_only_by_the_index_copy_of_the_allow_file(self):
        allow = "scripts/sweep-allow.txt"
        entry = "docs/a.md\t" + AWS_KEY + "\n"
        cases = (
            ("an untracked allow file hides nothing in the index", None, entry),
            ("an unstaged allow entry hides nothing in the index", "# no entries\n", entry),
        )
        for label, staged, worktree in cases:
            with self.subTest(label):
                script = self.make_repo(
                    {allow: staged or worktree, "docs/a.md": "use " + AWS_KEY + "\n"},
                    tracked=("docs/a.md",) if staged is None else ("docs/a.md", allow),
                )
                write_text(script.parent.parent / allow, worktree)
                code, output, found = self.scan_repo(script)
                self.assertEqual(code, 1, output)
                self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)

    def test_index_blob_that_differs_from_the_worktree_is_judged_by_the_index_copy(self):
        allow = "scripts/sweep-allow.txt"
        script = self.make_repo({allow: "# no entries\n", "docs/a.md": "use " + AWS_KEY + "\n"}, tracked=("docs/a.md", allow))
        repo = script.parent.parent
        write_text(repo / allow, "docs/a.md\t" + AWS_KEY + "\n")
        write_text(repo / "docs" / "a.md", "the file was cleaned, the entry stays unused\n")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            [item[:3] for item in found],
            [("docs/a.md", 1, "token"), (allow, 1, "stale-allow")],
            output,
        )

    def test_working_tree_file_is_judged_only_by_the_working_tree_copy(self):
        allow = "scripts/sweep-allow.txt"
        entry = "docs/a.md\t" + AWS_KEY + "\n"
        with self.subTest("an entry staged and dropped from the working tree does not cover the working tree"):
            script = self.make_repo({allow: entry, "docs/a.md": "use " + AWS_KEY + "\n"}, tracked=("docs/a.md", allow))
            write_text(script.parent.parent / allow, "# no entries\n")
            code, output, found = self.scan_repo(script)
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)
        with self.subTest("the same line in an index blob and a different working-tree file needs both entries"):
            script = self.make_repo({allow: entry, "docs/a.md": "use " + AWS_KEY + "\n"}, tracked=("docs/a.md", allow))
            write_text(script.parent.parent / allow, "# no entries\n")
            write_text(script.parent.parent / "docs" / "a.md", "use " + AWS_KEY + "\nmore\n")
            code, output, found = self.scan_repo(script)
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)
        with self.subTest("an unstaged edit needs the working-tree entry, the staged clean blob needs none"):
            script = self.make_repo({allow: "# no entries\n", "docs/a.md": "clean\n"}, tracked=("docs/a.md", allow))
            write_text(script.parent.parent / allow, entry)
            write_text(script.parent.parent / "docs" / "a.md", "use " + AWS_KEY + "\n")
            code, output, found = self.scan_repo(script)
            self.assertEqual((code, found), (0, []), output)

    def test_staged_allow_entry_covers_a_staged_file(self):
        allow = "scripts/sweep-allow.txt"
        files = {allow: "docs/a.md\t" + AWS_KEY + "\n", "docs/a.md": "use " + AWS_KEY + "\n"}
        script = self.make_repo(files, tracked=("docs/a.md", allow))
        code, output, found = self.scan_repo(script)
        self.assertEqual((code, found), (0, []), output)
        self.assertIn("# files 3, findings 0\n", output)

    def test_path_finding_is_judged_by_each_copy(self):
        allow = "scripts/sweep-allow.txt"
        name = "docs/" + AWS_KEY + ".md"
        entry = name + "\t" + AWS_KEY + "\n"
        with self.subTest("an untracked allow file does not cover the tracked name"):
            script = self.make_repo({allow: entry, name: "clean\n"}, tracked=(name,))
            code, output, found = self.scan_repo(script)
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in found], [(name, 0, "token")], output)
        with self.subTest("a staged entry does not cover the name on disk once the working tree drops it"):
            script = self.make_repo({allow: entry, name: "clean\n"}, tracked=(name, allow))
            write_text(script.parent.parent / allow, "# no entries\n")
            code, output, found = self.scan_repo(script)
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in found], [(name, 0, "token")], output)
        with self.subTest("a file deleted from the disk has its name in the index copy only"):
            script = self.make_repo({allow: entry, name: "clean\n"}, tracked=(name, allow))
            write_text(script.parent.parent / allow, "# no entries\n")
            os.remove(str(script.parent.parent / name))
            code, output, found = self.scan_repo(script)
            self.assertEqual((code, found), (0, []), output)

    def test_stale_reason_reads_the_same_copy_as_the_entry(self):
        allow = "scripts/sweep-allow.txt"
        script = self.make_repo({allow: "docs/a.md\t" + AWS_KEY + "\n", "docs/a.md": "clean\n"}, tracked=("docs/a.md", allow))
        write_text(script.parent.parent / allow, "# no entries\n")
        write_text(script.parent.parent / "docs" / "a.md", "use " + AWS_KEY + "\n")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertIn((allow, 1, "stale-allow", "text not found: docs/a.md"), found, output)

    def test_stale_entry_in_both_copies_is_reported_once(self):
        allow = "scripts/sweep-allow.txt"
        script = self.make_repo({allow: "docs/a.md\tgone text\n", "docs/a.md": "clean\n"}, tracked=("docs/a.md", allow))
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(found, [(allow, 1, "stale-allow", "text not found: docs/a.md")], output)

    def test_bad_index_copy_of_the_allow_file_exits_2(self):
        allow = "scripts/sweep-allow.txt"
        good = "docs/a.md\t" + AWS_KEY + "\n"
        cases = (
            ("not UTF-8", b"docs/a.md\t\xb3\n", "# error: allowlist is not UTF-8: index copy of " + allow),
            ("malformed", b"no tab here\n", "# error: scripts/sweep-allow.txt:1: expected path<TAB>exact text"),
        )
        for label, staged, message in cases:
            with self.subTest(label):
                script = self.make_repo({allow: staged, "docs/a.md": "use " + AWS_KEY + "\n"}, tracked=("docs/a.md", allow))
                write_text(script.parent.parent / allow, good)
                code, output, _found = self.scan_repo(script)
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)

    def test_bom_in_the_index_copy_of_the_allow_file_is_ignored(self):
        allow = "scripts/sweep-allow.txt"
        files = {allow: codecs.BOM_UTF8 + ("docs/a.md\t" + AWS_KEY + "\n").encode("utf-8"), "docs/a.md": "use " + AWS_KEY + "\n"}
        script = self.make_repo(files, tracked=("docs/a.md", allow))
        code, output, found = self.scan_repo(script)
        self.assertEqual((code, found), (0, []), output)

    def test_allow_file_in_a_merge_conflict_exits_2(self):
        allow = "scripts/sweep-allow.txt"
        identity = ("-c", "user.name=Test", "-c", "user.email=test@example.com")
        script = self.make_repo({allow: "# base\n"}, tracked=(allow,))
        repo = script.parent.parent
        self.git(repo, *identity, "commit", "-q", "-m", "base")
        self.git(repo, "checkout", "-q", "-b", "ours")
        write_text(repo / allow, "# ours\n")
        self.git(repo, *identity, "commit", "-q", "-a", "-m", "ours")
        self.git(repo, "checkout", "-q", "-b", "theirs", "HEAD~1")
        write_text(repo / allow, "# theirs\n")
        self.git(repo, *identity, "commit", "-q", "-a", "-m", "theirs")
        self.git(repo, "checkout", "-q", "ours")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git(repo, *identity, "merge", "-q", "theirs")
        write_text(repo / allow, "# resolved in the working tree only\n")
        code, output, _found = self.scan_repo(script)
        self.assertEqual(code, 2, output)
        self.assertIn("# error: scripts/sweep-allow.txt has 3 index stages", output)

    def test_allow_file_entry_lines_are_scanned_in_path_mode(self):
        # Path mode checks no entry whose file it does not scan, so the entry lines must be scanned.
        allow = "scripts/sweep-allow.txt"
        script = self.make_repo({allow: "docs/a.md\t" + AWS_KEY + "\n", "docs/a.md": "use " + AWS_KEY + "\n"})
        proc = subprocess.run(
            [sys.executable, "-I", str(script), "--no-denylist", allow],
            cwd=str(script.parent.parent),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        output = proc.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(proc.returncode, 1, output)
        self.assertEqual([item[:3] for item in self.findings(output)], [(allow, 1, "token")], output)

    def test_tracked_symlink_blob_is_scanned_as_link_text(self):
        script = self.make_repo({"README.md": "clean\n"})
        repo = script.parent.parent
        oid = self.git(repo, "hash-object", "-w", "--stdin", data=(MAC_HOME + "photo.png").encode("utf-8"))
        self.git(repo, "update-index", "--add", "--cacheinfo", "120000,%s,logo.png" % oid)
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("logo.png", 1, "user-path")], output)

    def test_submodule_entry_is_a_finding(self):
        script = self.make_repo({"README.md": "clean\n"})
        self.git(script.parent.parent, "update-index", "--add", "--cacheinfo", "160000,%s,vendor" % ("1" * 40))
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(found, [("vendor", 0, "encoding", "unreadable (submodule, not scanned)")], output)

    def test_untracked_nested_repository_is_a_finding(self):
        script = self.make_repo({"README.md": "clean\n", "nested/inner.txt": "clean\n"})
        self.git(script.parent.parent / "nested", "init", "-q")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual(found, [("nested/", 0, "encoding", "unreadable (directory, not scanned)")], output)

    def test_untracked_symlink_is_not_followed(self):
        outside = self.make_tree({"secret.txt": "token " + GHP_TOKEN + "\n"}, name="outside-" + AWS_KEY)
        script = self.make_repo({"README.md": "clean\n"})
        self.symlink(os.path.join("..", outside.name, "secret.txt"), script.parent.parent / "link.txt")
        code, output, found = self.scan_repo(script)
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in found], [("link.txt", 1, "token")], output)

    def test_path_mode_symlinks_are_not_followed(self):
        outside = self.make_tree({"secret.txt": "token " + GHP_TOKEN + "\n"}, name="outside-" + AWS_KEY)
        tree = self.make_tree({"README.md": "clean\n"})
        self.symlink(os.path.join("..", outside.name, "secret.txt"), tree / "link.txt")
        self.symlink(os.path.join("..", outside.name), tree / "dirlink", directory=True)
        code, output = self.run_sweep("--no-denylist", str(tree))
        self.assertEqual(code, 1, output)
        found = self.findings(output, prefix=str(tree) + "/")
        self.assertEqual([item[:3] for item in found], [("dirlink", 1, "token"), ("link.txt", 1, "token")], output)
        code, output = self.run_sweep("--no-denylist", str(tree / "dirlink"))
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in self.findings(output)], [(str(tree / "dirlink"), 1, "token")], output)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs os.mkfifo (POSIX)")
    def test_path_mode_special_file_is_a_finding(self):
        tree = self.make_tree({"README.md": "clean\n"})
        os.mkfifo(str(tree / "pipe"))
        code, output = self.run_sweep("--no-denylist", str(tree))
        self.assertEqual(code, 1, output)
        found = self.findings(output, prefix=str(tree) + "/")
        self.assertEqual(found, [("pipe", 0, "encoding", "unreadable (not a regular file, not scanned)")], output)

    def git_mock(self, responses):
        def run(args, **_kwargs):
            key = " ".join(args[1:3])
            result = responses[key]
            if isinstance(result, BaseException):
                raise result
            return subprocess.CompletedProcess(args, 0, result, b"")

        return mock.patch.object(sweep.subprocess, "run", side_effect=run)

    def test_git_failures_exit_2(self):
        oid = "a" * 40
        cases = (
            ("git missing", {"ls-files -s": FileNotFoundError(2, "no git")}, "# error: git ls-files failed: FileNotFoundError"),
            (
                "git timeout",
                {"ls-files -s": subprocess.TimeoutExpired(["git"], 60)},
                "# error: git ls-files failed: TimeoutExpired",
            ),
            (
                "non-UTF-8 path",
                {"ls-files -s": ("100644 %s 0\t" % oid).encode("ascii") + b"\xff.txt\0"},
                "# error: git ls-files printed a path that is not UTF-8",
            ),
            (
                "blob missing",
                {
                    "ls-files -s": ("100644 %s 0\ta.txt\0" % oid).encode("ascii"),
                    "ls-files -o": b"",
                    "cat-file --batch": ("%s missing\n" % oid).encode("ascii"),
                },
                "# error: git cat-file: unexpected output for object " + oid,
            ),
        )
        for label, responses, message in cases:
            with self.subTest(label):
                with self.git_mock(responses):
                    code, output = self.main_in_process("--no-denylist")
                self.assertEqual(code, 2, output)
                self.assertIn(message, output)

    def test_cat_file_reply_is_checked(self):
        oid = "a" * 40
        listing = {"ls-files -s": ("100644 %s 0\ta.txt\0" % oid).encode("ascii"), "ls-files -o": b""}
        cases = (
            ("reply for another object", "%s blob 3\nabc\n" % ("b" * 40)),
            ("size is not a number", "%s blob x3\nabc\n" % oid),
            ("size is negative", "%s blob -3\nabc\n" % oid),
            ("extra header field", "%s blob 3 extra\nabc\n" % oid),
            ("no header line", ""),
            ("header without a line end", "%s blob 33" % oid),
            ("body shorter than its size", "%s blob 3\nab" % oid),
            ("body without its line end", "%s blob 3\nabc" % oid),
        )
        with self.subTest("well-formed reply (control)"):
            responses = dict(listing, **{"cat-file --batch": ("%s blob 3\nabc\n" % oid).encode("ascii")})
            with mock.patch.object(sweep, "ROOT", self.tmp), self.git_mock(responses):
                code, output = self.main_in_process("--no-denylist")
            self.assertEqual(code, 0, output)
            self.assertIn("# files 1, findings 0\n", output)
        for label, reply in cases:
            with self.subTest(label):
                responses = dict(listing, **{"cat-file --batch": reply.encode("ascii")})
                with mock.patch.object(sweep, "ROOT", self.tmp), self.git_mock(responses):
                    code, output = self.main_in_process("--no-denylist")
                self.assertEqual(code, 2, output)
                self.assertIn("# error: git cat-file: unexpected output for object " + oid, output)

    def test_every_stage_of_a_conflicted_index_entry_is_scanned(self):
        identity = ("-c", "user.name=Test", "-c", "user.email=test@example.com")
        for stage in ("base", "ours", "theirs"):
            with self.subTest(stage=stage):
                contents = {"base": "base\n", "ours": "ours\n", "theirs": "theirs\n"}
                contents[stage] = "token " + GHP_TOKEN + "\n"
                script = self.make_repo({"docs/a.md": contents["base"]})
                repo = script.parent.parent
                target = repo / "docs" / "a.md"
                self.git(repo, "add", "--", "docs/a.md")
                self.git(repo, *identity, "commit", "-q", "-m", "base")
                self.git(repo, "checkout", "-q", "-b", "ours")
                write_text(target, contents["ours"])
                self.git(repo, *identity, "commit", "-q", "-a", "-m", "ours")
                self.git(repo, "checkout", "-q", "-b", "theirs", "HEAD~1")
                write_text(target, contents["theirs"])
                self.git(repo, *identity, "commit", "-q", "-a", "-m", "theirs")
                self.git(repo, "checkout", "-q", "ours")
                with self.assertRaises(subprocess.CalledProcessError):
                    self.git(repo, *identity, "merge", "-q", "theirs")
                entries = self.git(repo, "ls-files", "-s", "--", "docs/a.md").splitlines()
                self.assertEqual([entry.split()[2] for entry in entries], ["1", "2", "3"])
                # The conflict markers in the worktree would repeat both sides: only the index holds the stages.
                write_text(target, "clean\n")
                code, output, found = self.scan_repo(script)
                self.assertEqual(code, 1, output)
                self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)

    def test_not_a_git_repo_exits_2(self):
        script = self.tmp / "norepo" / "scripts" / "sweep.py"
        script.parent.mkdir(parents=True)
        shutil.copyfile(str(SCRIPT), str(script))
        code, output = self.run_sweep("--no-denylist", script=script)
        self.assertEqual(code, 2, output)
        self.assertIn("# error: git ls-files failed", output)


class ReadFailureTests(SweepTestCase):
    def test_unreadable_worktree_file_is_a_finding(self):
        message = "unreadable (PermissionError)"
        with self.subTest("path mode"):
            tree = self.make_tree({"locked.txt": "clean\n", "fine.txt": "clean\n"})
            with deny_open(tree / "locked.txt"):
                code, output = self.main_in_process("--no-denylist", str(tree))
            self.assertEqual(code, 1, output)
            self.assertEqual(self.findings(output, prefix=str(tree) + "/"), [("locked.txt", 0, "encoding", message)], output)
            self.assertIn("# files 2, findings 1\n", output)
        with self.subTest("repo mode, tracked file: its clean index blob does not make up for it"):
            repo = self.make_repo({"docs/a.md": "clean\n"}, tracked=("docs/a.md",)).parent.parent
            with mock.patch.object(sweep, "ROOT", repo), deny_open(repo / "docs" / "a.md"):
                code, output = self.main_in_process("--no-denylist")
            self.assertEqual(code, 1, output)
            self.assertEqual(self.findings(output), [("docs/a.md", 0, "encoding", message)], output)
            self.assertIn("# files 2, findings 1\n", output)

    def test_file_that_vanishes_after_listing_is_a_finding(self):
        def vanish_after(listing, victim):
            def wrapper(*args):
                targets = listing(*args)
                os.remove(str(victim))
                return targets

            return wrapper

        message = "unreadable (vanished)"
        with self.subTest("path mode"):
            tree = self.make_tree({"gone.txt": "clean\n", "fine.txt": "clean\n"})
            with mock.patch.object(sweep, "path_targets", vanish_after(sweep.path_targets, tree / "gone.txt")):
                code, output = self.main_in_process("--no-denylist", str(tree))
            self.assertEqual(code, 1, output)
            self.assertEqual(self.findings(output, prefix=str(tree) + "/"), [("gone.txt", 0, "encoding", message)], output)
            self.assertIn("# files 2, findings 1\n", output)
        with self.subTest("repo mode, untracked file"):
            repo = self.make_repo({"docs/a.md": "clean\n", "docs/gone.md": "clean\n"}, tracked=("docs/a.md",)).parent.parent
            vanishing = vanish_after(sweep.repo_targets, repo / "docs" / "gone.md")
            with mock.patch.object(sweep, "ROOT", repo), mock.patch.object(sweep, "repo_targets", vanishing):
                code, output = self.main_in_process("--no-denylist")
            self.assertEqual(code, 1, output)
            self.assertEqual(self.findings(output), [("docs/gone.md", 0, "encoding", message)], output)
        with self.subTest("repo mode, tracked file deleted from the worktree: its index blob is scanned"):
            script = self.make_repo({"docs/a.md": "token " + GHP_TOKEN + "\n"}, tracked=("docs/a.md",))
            os.remove(str(script.parent.parent / "docs" / "a.md"))
            code, output, found = self.scan_repo(script)
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in found], [("docs/a.md", 1, "token")], output)


class PathModeTests(SweepTestCase):
    def test_single_file_argument(self):
        tree = self.make_tree({"clean.txt": "clean\n", "dirty.txt": "use " + AWS_KEY + "\n"})
        with self.subTest("clean file: only its name is scanned, not its directory"):
            code, output = self.run_sweep("--no-denylist", str(tree / "clean.txt"))
            self.assertEqual(code, 0, output)
            self.assertIn("# files 1, findings 0\n", output)
        with self.subTest("dirty file: reported under the argument as given"):
            code, output = self.run_sweep("--no-denylist", str(tree / "dirty.txt"))
            self.assertEqual(code, 1, output)
            self.assertEqual([item[:3] for item in self.findings(output)], [(str(tree / "dirty.txt"), 1, "token")])

    def test_missing_path_exits_2(self):
        code, output = self.run_sweep("--no-denylist", str(self.tmp / "no-such-dir"))
        self.assertEqual(code, 2, output)
        self.assertIn("# error: no such file or directory: ", output)

    def test_path_mode_skips_git_directory(self):
        code, output, found = self.scan_tree({"README.md": "clean\n", ".git/config": "url = " + EMAIL + "\n"})
        self.assertEqual((code, found), (0, []), output)
        self.assertIn("# files 1, findings 0\n", output)

    def test_path_mode_skips_git_file(self):
        pointer = "gitdir: " + DRIVE_SLASH + "repo/.git/worktrees/x\n"
        files = {"README.md": "clean\n", ".git": pointer, "sub/.git": pointer}
        code, output, found = self.scan_tree(files)
        self.assertEqual((code, found), (0, []), output)
        self.assertIn("# files 1, findings 0\n", output)

    def test_allow_lines_match_however_a_repo_path_is_given(self):
        allow = "scripts/sweep-allow.txt"
        files = {allow: "docs/a.md\t" + AWS_KEY + "\n", "docs/a.md": "use " + AWS_KEY + "\n"}
        script = self.make_repo(files)
        repo = script.parent.parent
        for arg in (".", "./", "./docs", "docs", "docs/", "./docs/a.md", "docs/a.md", str(repo), str(repo / "docs" / "a.md")):
            with self.subTest(arg=arg):
                code, output = self.run_sweep("--no-denylist", arg, script=script, cwd=repo)
                self.assertEqual((code, self.findings(output)), (0, []), output)
                self.assertRegex(output, r"# files [1-9]\d*, findings 0\n")

    def test_stale_allow_line_is_reported_for_a_whole_repo_path_run(self):
        allow = "scripts/sweep-allow.txt"
        script = self.make_repo({allow: "docs/a.md\t" + AWS_KEY + "\n", "docs/a.md": "clean\n"})
        code, output = self.run_sweep("--no-denylist", ".", script=script, cwd=script.parent.parent)
        self.assertEqual(code, 1, output)
        self.assertEqual(
            self.findings(output), [(allow, 1, "stale-allow", "text not found: docs/a.md")], output
        )

    def test_path_outside_the_repo_stays_as_given(self):
        self.make_tree({"dirty.txt": "use " + AWS_KEY + "\n"})
        code, output = self.run_sweep("--no-denylist", "./tree/dirty.txt")
        self.assertEqual(code, 1, output)
        self.assertEqual([item[:3] for item in self.findings(output)], [("./tree/dirty.txt", 1, "token")], output)

    def test_unlistable_subdirectory_exits_2(self):
        tree = self.make_tree({"README.md": "clean\n", "sub/notes.txt": "clean\n"})
        real_scandir = os.scandir
        blocked = os.path.normcase(str(tree / "sub"))

        def scandir(path="."):
            if os.path.normcase(os.fspath(path)) == blocked:
                raise PermissionError(13, "denied", os.fspath(path))
            return real_scandir(path)

        with mock.patch.object(os, "scandir", scandir):
            code, output = self.main_in_process("--no-denylist", str(tree))
        self.assertEqual(code, 2, output)
        self.assertIn("# internal error: PermissionError", output)

    def test_internal_error_exits_2(self):
        tree = self.make_tree({"README.md": "clean\n"})
        with mock.patch.object(sweep, "scan_file", side_effect=RuntimeError("boom")):
            code, output = self.main_in_process("--no-denylist", str(tree))
        self.assertEqual(code, 2, output)
        self.assertIn("# internal error: RuntimeError", output)

    def test_stdout_reconfigure_failure_exits_2(self):
        tree = self.make_tree({"README.md": "clean\n"})
        code = (
            "import io, runpy, sys\n"
            "script, tree = sys.argv[1], sys.argv[2]\n"
            "sys.stdout = io.StringIO()\n"
            "sys.argv = [script, '--no-denylist', tree]\n"
            "runpy.run_path(script, run_name='__main__')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code, str(SCRIPT), str(tree)],
            cwd=str(self.tmp),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
