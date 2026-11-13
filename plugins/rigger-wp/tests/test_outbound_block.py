"""templates/wp-outbound-block.php: its guards, driven through real PHP and WordPress stand-ins."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "wp-outbound-block.php"
HARNESS = Path(__file__).resolve().parent / "outbound_block_harness.php"
RULE = PLUGIN / "rules" / "wp-skills.md"
SKILL = PLUGIN / "skills" / "rigger-wp-traps" / "SKILL.md"
PHP = shutil.which("php")
HOME = "https://copy.test/"
LAST_PRIORITY = 9223372036854775807
BLOCK_CODE = "rigger_outbound_blocked"
DOMAIN = "example.com"
WP_ORG = "wordpress.org"


@unittest.skipIf(PHP is None, "php not on PATH: the outbound block template was not run")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="outbound-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = self.tmp / "php.log"
        self.log.write_bytes(b"")
        (self.tmp / "wp" / "wp-includes").mkdir(parents=True)
        with open(self.tmp / "wp" / "wp-includes" / "pluggable.php", "w", encoding="utf-8", newline="") as handle:
            handle.write("<?php\nfunction wp_mail() {\n\treturn false;\n}\n")

    def run_php(self, calls=None, constants=None, home=HOME, site=None, use_add_filter=True, load_twice=False,
                core_mail=True):
        scenario = {
            "template": TEMPLATE.as_posix(),
            "log": self.log.as_posix(),
            "home": home,
            "site": site or home,
            "constants": constants or {},
            "use_add_filter": use_add_filter,
            "load_twice": load_twice,
            "abspath": (self.tmp / "wp").as_posix(),
            "core_mail": core_mail,
            "calls": calls or {},
        }
        path = self.tmp / "scenario.json"
        path.write_text(json.dumps(scenario), encoding="utf-8")
        proc = subprocess.run(
            [PHP, "-d", "display_errors=stderr", str(HARNESS), str(path)],
            cwd=str(self.tmp),
            env=dict(os.environ, HOME=str(self.tmp), USERPROFILE=str(self.tmp), XDEBUG_MODE="off"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(proc.stderr, b"")
        return json.loads(proc.stdout.decode("utf-8"))

    def requests(self, urls, **options):
        calls = {"requests": [{"pre": False, "url": url} for url in urls]}
        return self.run_php(calls, **options)["requests"]

    def log_lines(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [line.split("] ", 1)[-1] for line in lines]

    def assert_passes(self, results):
        self.assertEqual(results, [{"value": False}] * len(results))

    def assert_blocked(self, results):
        self.assertTrue(results)
        for result in results:
            self.assertEqual(result.get("error"), BLOCK_CODE, result)


class HttpTests(Base):
    def test_allowed_hosts_pass(self):
        self.assert_passes(self.requests([
            "http://localhost/x",
            "http://LOCALHOST./x",
            "http://127.0.0.1:8080/x",
            "http://[::1]/x",
            "https://shop.test/x",
            "https://a.b.localhost/x",
            "https://copy.test/wp-json/x",
            "https://wordpress.org/x",
            "https://api.%s/x" % WP_ORG,
        ]))

    def test_home_and_site_hosts_each_count(self):
        copy, admin = "https://copy.%s/" % DOMAIN, "https://admin.%s/" % DOMAIN
        self.assert_passes(self.requests([copy + "x", admin + "x"], home=copy, site=admin))

    def test_third_party_hosts_are_blocked(self):
        self.assert_blocked(self.requests([
            "https://api.stripe.%s/x" % DOMAIN,
            "https://example.com/x",
            "https://%s.%s/x" % (WP_ORG, DOMAIN),
            "https://not%s/x" % WP_ORG,
            "https://test/x",
            "https://%s.test.%s/x" % (DOMAIN, DOMAIN),
        ]))

    def test_a_url_without_a_host_is_blocked(self):
        for url in ("http:///x", "/relative", "", "http://:80"):
            with self.subTest(url=url):
                self.assert_blocked(self.requests([url]))

    def test_a_host_that_trims_to_nothing_is_blocked_even_when_home_has_no_host(self):
        self.assert_blocked(self.requests(["http://./x", "http://[]/x"], home="not a url"))

    def test_the_allow_constant_takes_exact_hosts_and_suffixes(self):
        constants = {"RIGGER_OUTBOUND_ALLOW": " Example.com ,,.partner.%s, api.%s. " % (DOMAIN, DOMAIN)}
        urls = ["https://example.com/x", "https://a.partner.%s/x" % DOMAIN, "https://api.%s/x" % DOMAIN]
        self.assert_passes(self.requests(urls, constants=constants))
        self.assert_blocked(self.requests(["https://sub.%s/x" % DOMAIN, "https://partner.%s/x" % DOMAIN],
                                          constants=constants))

    def test_an_earlier_filter_answer_is_kept(self):
        for pre in (True, {"body": "fake"}, "text"):
            with self.subTest(pre=pre):
                result = self.run_php({"requests": [{"pre": pre, "url": "https://example.com/x"}]})["requests"]
                self.assertEqual(result, [{"value": pre}])
                self.assertEqual(self.log_lines(), [])

    def test_the_log_line_holds_method_and_host_and_no_query(self):
        result = self.requests(["https://Example.com/x?api_key=SECRET-VALUE"])
        self.assertEqual(self.log_lines(), ["rigger-outbound-block: blocked POST example.com"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_a_request_without_a_method_is_logged_as_get(self):
        self.run_php({"requests": [{"pre": False, "url": "https://example.com/x", "args": {}}]})
        self.assertEqual(self.log_lines(), ["rigger-outbound-block: blocked GET example.com"])

    def test_a_url_without_a_host_is_labelled_in_the_error_and_the_log(self):
        for url in ("http:///x", "/relative"):
            with self.subTest(url=url):
                self.log.write_bytes(b"")
                result = self.requests([url])[0]
                self.assertEqual(result["message"], "Outbound request to (unparsed host) blocked by rigger-outbound-block.")
                self.assertEqual(self.log_lines(), ["rigger-outbound-block: blocked POST (unparsed host)"])


class RedirectTests(Base):
    def redirects(self, locations):
        return self.run_php({"redirects": locations})["redirects"]

    def test_a_redirect_to_an_allowed_host_passes(self):
        self.assertEqual(self.redirects(["https://copy.test/x", "https://api.%s/y" % WP_ORG]),
                         [{"value": None}, {"value": None}])

    def test_a_redirect_without_a_host_throws_even_when_home_has_no_host(self):
        result = self.run_php({"redirects": ["/relative"]}, home="not a url")["redirects"][0]
        self.assertEqual(result.get("thrown"), BLOCK_CODE, result)

    def test_a_redirect_to_a_blocked_or_unparsed_host_throws(self):
        for location, shown in (("https://Example.com/x?k=SECRET", "example.com"), ("http:///x", "(unparsed host)")):
            with self.subTest(location=location):
                result = self.redirects([location])[0]
                self.assertEqual(result.get("thrown"), BLOCK_CODE, result)
                self.assertIn(shown, result["message"])
                self.assertNotIn("SECRET", json.dumps(result))
                self.assertEqual(self.log_lines()[-1], "rigger-outbound-block: blocked redirect to " + shown)


class MailTests(Base):
    def mail(self, pre=None, atts=None, constants=None):
        atts = atts if atts is not None else {"to": "Someone <a@Example.com>", "subject": "Hi\r\nthere"}
        return self.run_php({"mail": [{"pre": pre, "atts": atts}]}, constants=constants)["mail"][0]

    def test_mail_is_stopped_and_logged_without_the_local_part(self):
        self.assertEqual(self.mail(), {"value": False})
        self.assertEqual(self.log_lines(), ['rigger-outbound-block: blocked wp_mail to example.com, subject "Hi there"'])

    def test_a_list_of_recipients_is_logged_by_domain_once(self):
        atts = {"to": ["a@example.com", "b@example.com", "no-at-sign"], "subject": "x"}
        self.assertEqual(self.mail(atts=atts), {"value": False})
        self.assertEqual(self.log_lines(), ['rigger-outbound-block: blocked wp_mail to example.com,?, subject "x"'])

    def test_a_comma_separated_recipient_string_is_logged_per_domain(self):
        atts = {"to": "a@one.%s, b@two.%s" % (DOMAIN, DOMAIN), "subject": "x"}
        self.assertEqual(self.mail(atts=atts), {"value": False})
        self.assertEqual(self.log_lines(),
                         ['rigger-outbound-block: blocked wp_mail to one.%s,two.%s, subject "x"' % (DOMAIN, DOMAIN)])

    def test_a_tab_after_a_recipient_is_trimmed(self):
        atts = {"to": ["a@example.com\t", "Someone <b@example.com>\t"], "subject": "x"}
        self.assertEqual(self.mail(atts=atts), {"value": False})
        self.assertEqual(self.log_lines(), ['rigger-outbound-block: blocked wp_mail to example.com, subject "x"'])

    def test_the_mail_constant_lets_mail_through(self):
        self.assertEqual(self.mail(constants={"RIGGER_ALLOW_MAIL": True}), {"value": None})
        self.assertEqual(self.log_lines(), [])

    def test_a_false_mail_constant_keeps_mail_stopped(self):
        self.assertEqual(self.mail(constants={"RIGGER_ALLOW_MAIL": False}), {"value": False})

    def test_an_earlier_filter_answer_is_kept(self):
        self.assertEqual(self.mail(pre=True), {"value": True})
        self.assertEqual(self.log_lines(), [])


class LoadingTests(Base):
    def test_hooks_are_registered_at_the_last_priority(self):
        for use_add_filter in (True, False):
            with self.subTest(use_add_filter=use_add_filter):
                out = self.run_php(use_add_filter=use_add_filter)
                hooks = out["hooks"]
                for hook in ("pre_http_request", "requests-requests.before_redirect", "pre_wp_mail", "plugins_loaded"):
                    self.assertEqual(hooks.get(hook), [LAST_PRIORITY], hook)
                self.assertEqual(out["add_filter_calls"], 6 if use_add_filter else 0)

    def test_the_block_works_when_loaded_before_add_filter_exists(self):
        self.assert_blocked(self.requests(["https://example.com/x"], use_add_filter=False))

    def test_a_second_load_adds_no_second_callback(self):
        out = self.run_php({"requests": [{"pre": False, "url": "https://example.com/x"}]}, load_twice=True)
        self.assert_blocked(out["requests"])
        self.assertEqual(out["add_filter_calls"], 6)

    def test_plugins_loaded_says_it_is_active_and_warns_about_a_foreign_wp_mail(self):
        self.run_php({"plugins_loaded": True}, core_mail=True)
        lines = self.log_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("rigger-outbound-block: active (", lines[0])
        self.assertTrue(lines[0].endswith("HTTP to third-party hosts is blocked, wp_mail() is blocked"), lines)
        self.log.write_bytes(b"")
        self.run_php({"plugins_loaded": True}, core_mail=False)
        lines = self.log_lines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("WARNING wp_mail() comes from", lines[1])

    def test_the_admin_notice_names_the_mail_state(self):
        for constants, state in (({}, "wp_mail() is blocked"), ({"RIGGER_ALLOW_MAIL": True}, "wp_mail() is allowed")):
            with self.subTest(state=state):
                notice = self.run_php({"notices": True}, constants=constants)["notice"]
                self.assertIn(state, notice)
                self.assertIn("It must never run on production.", notice)


class DocTests(unittest.TestCase):
    def test_the_template_names_no_local_stack_tool(self):
        self.assertNotIn("Herd", TEMPLATE.read_text(encoding="utf-8"))
        self.assertIn("a local mail catcher", TEMPLATE.read_text(encoding="utf-8"))

    def test_the_injected_rule_says_where_the_template_is(self):
        self.assertTrue((PLUGIN / "templates" / "wp-outbound-block.php").is_file())
        self.assertIn("`templates/wp-outbound-block.php` under the `Plugin root:` line above",
                      RULE.read_text(encoding="utf-8"))

    def test_the_skill_names_rule_topics_without_a_pointer(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertNotIn("CLAUDE.md", text)
        for topic in ("pin global state and restore it", "asserts its URL", "compare the way the"):
            with self.subTest(topic=topic):
                self.assertIn(topic, " ".join(text.split()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
