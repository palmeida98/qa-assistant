"""scripts/audit_a11y.py (technique-accessibility.md section 2): translation of project.yaml into the qa-audit
engine's configuration, and of the engine's result (WCAG references of the findings) into criteria, level,
violations / best practices / incomplete / other checks; redirected or unaudited page (false Pass); validation
of the configuration. No browser nor network: uses a fictional results.json of the engine.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "src")]

import audit_a11y as a11y


def finding(url, rule, refs, status="problem", n=1, severity="medium"):
    return {"module": "accessibility", "check": f"axe/{rule}", "title": f"help {rule}", "status": status,
            "severity": severity, "url": url, "actual": f"{n} element(s) fail this rule",
            "references": refs + [f"https://dequeuniversity.com/rules/axe/4.13/{rule}"],
            "evidence": [{"kind": "html", "value": "<b>", "label": "#x"}], "element": {"selector": "#x"}}


def page(url, final=None, audited=True, error="", title="Demo Shop"):
    return {"url": url, "final_url": final or url, "title": title, "error": error, "audited": audited}


HOME, ACCOUNT, NEW = "https://shop.demo/", "https://shop.demo/account", "https://shop.demo/new"
RESULT = {
    "browser": {"engine": "chromium", "version": "153"},
    "notes": ["auth: logged in (form) at /account; one attempt"],
    "crawl": {"skipped": [{"url": "https://shop.demo/blocked", "reason": "disallowed by robots.txt"}]},
    "pages": [page(HOME), page(ACCOUNT, final="https://shop.demo/login", title="Log in"),
              page(NEW, error="HTTP 404", audited=False)],
    "findings": [
        finding(HOME, "color-contrast", ["WCAG 1.4.3", "WCAG 2.0 AA"], n=3),
        finding(HOME, "image-alt", ["WCAG 1.1.1", "WCAG 2.0 A"], severity="high"),
        finding(HOME, "region", []),                                   # best-practice: no criterion
        finding(HOME, "target-size", ["WCAG 2.5.8", "WCAG 2.2 AA"]),   # outside WCAG 2.1: a best practice here
        finding(HOME, "color-contrast", ["WCAG 1.4.3", "WCAG 2.0 AA"], status="needs-human", n=2),
        {"module": "accessibility", "check": "reflow", "title": "Content scrolls horizontally at 320 CSS px",
         "status": "warning", "severity": "medium", "url": HOME, "actual": "The page is 900 px wide",
         "references": ["WCAG 1.4.10"], "element": {}},
        {"module": "accessibility", "check": "focus-not-visible", "title": "Focus is not visible on some elements",
         "status": "problem", "severity": "medium", "url": HOME, "actual": "Identical pixels focused and not "
         "focused: #buy", "references": ["WCAG 2.4.7"], "element": {"selector": "#buy"}},
        {"module": "security", "check": "missing-csp", "title": "x", "status": "problem", "severity": "low",
         "url": HOME},
        finding("https://shop.demo/login", "image-alt", ["WCAG 1.1.1", "WCAG 2.0 A"]),
    ],
}
GROUP = {"login": True, "target": HOME, "pages": [
    {"name": "home", "url": HOME, "login": True}, {"name": "account", "url": ACCOUNT, "login": True},
    {"name": "new", "url": NEW, "login": True},
    {"name": "blocked", "url": "https://shop.demo/blocked", "login": True},
    {"name": "never", "url": "https://shop.demo/never", "login": True}]}


class References(unittest.TestCase):
    def test_criteria_and_level_from_the_engines_references(self):
        self.assertEqual(a11y.criteria(["WCAG 1.4.3", "WCAG 2.0 AA", "WCAG 1.4.10", "https://x"]),
                         ["1.4.3", "1.4.10"])
        self.assertEqual(a11y.tag_of_reference("WCAG 2.0 AA"), "wcag2aa")
        self.assertEqual(a11y.tag_of_reference("WCAG 2.1 A"), "wcag21a")
        self.assertEqual(a11y.level(["WCAG 1.4.3", "WCAG 2.0 AA"], "WCAG 2.1 AA"), "AA")
        self.assertEqual(a11y.level(["WCAG 2.0 A", "WCAG 2.1 AA"], "WCAG 2.1 AA"), "A")
        self.assertIsNone(a11y.level([], "WCAG 2.1 AA"))

    def test_level_only_within_the_standard(self):
        self.assertIsNone(a11y.level(["WCAG 2.5.8", "WCAG 2.2 AA"], "WCAG 2.1 AA"))
        self.assertEqual(a11y.level(["WCAG 2.5.8", "WCAG 2.2 AA"], "WCAG 2.2 AA"), "AA")


class Classify(unittest.TestCase):
    def setUp(self):
        self.rep = a11y.classify([{"group": GROUP, "code": 0, "result": RESULT, "error": ""}], "WCAG 2.1 AA")
        self.p = {p["name"]: p for p in self.rep["pages"]}

    def test_wcag_violations_and_best_practices_apart(self):
        v = self.p["home"]["wcag_violations"]
        self.assertEqual([(x["rule"], x["criteria"], x["level"], x["elements"]) for x in v],
                         [("color-contrast", ["1.4.3"], "AA", 3), ("image-alt", ["1.1.1"], "A", 1)])
        self.assertEqual(v[1]["suggested_severity"], "high")
        self.assertTrue(v[0]["help_url"].startswith("https://dequeuniversity.com/"))
        self.assertEqual([x["rule"] for x in self.p["home"]["best_practices"]], ["region", "target-size"])

    def test_incomplete_results_are_left_to_manual_review(self):
        self.assertEqual(self.p["home"]["incomplete"], [{"rule": "color-contrast", "criteria": ["1.4.3"],
                                                        "elements": 2}])

    def test_the_engines_other_checks_are_leads_and_only_from_accessibility(self):
        self.assertEqual([(o["check"], o["criteria"], o["status"]) for o in self.p["home"]["other_checks"]],
                         [("reflow", ["1.4.10"], "warning"), ("focus-not-visible", ["2.4.7"], "problem")])

    def test_a_redirected_page_does_not_give_a_false_pass(self):
        account = self.p["account"]
        self.assertIn("redirected", account["error"])
        self.assertEqual(account["wcag_violations"], [])

    def test_error_pages_skipped_or_not_visited_stay_unaudited(self):
        self.assertEqual(self.p["new"]["error"], "HTTP 404")
        self.assertIn("robots", self.p["blocked"]["error"])
        self.assertIn("not visited", self.p["never"]["error"])
        self.assertIsNone(self.p["home"]["error"])

    def test_a_failed_login_leaves_the_whole_group_unaudited(self):
        rep = a11y.classify([{"group": GROUP, "code": 3, "result": None,
                              "error": "qa-audit: login not confirmed"}], "WCAG 2.1 AA")
        self.assertTrue(all("login failed" in p["error"] for p in rep["pages"]))

    def test_the_engines_notes_and_browser(self):
        self.assertEqual(self.rep["browser"], "chromium 153")
        self.assertIn("auth: logged in (form) at /account; one attempt", self.rep["notes"])

    def test_the_summary_names_the_pages_not_audited(self):
        lines = a11y.text_summary(self.rep)
        self.assertTrue(any(ln.startswith("new: NOT AUDITED") for ln in lines), lines)
        self.assertTrue(any("color-contrast [1.4.3 AA] x3" in ln for ln in lines), lines)
        self.assertTrue(any("reflow [1.4.10] to confirm" in ln for ln in lines), lines)


class Configuration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, yaml_txt, env=""):
        with open(os.path.join(self.tmp.name, "project.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_txt)
        with open(os.path.join(self.tmp.name, ".env"), "w", encoding="utf-8") as f:
            f.write(env)

    LOGIN = ('  login: {url: "https://shop.demo/", user_field: "#u", pass_field: "#p",'
             ' button: "#b", user_var: SHOP_U, pass_var: SHOP_P}\n')
    PAGES = ('  pages:\n    - {name: entry, url: "https://shop.demo/", login: false}\n'
             '    - {name: account, url: "https://shop.demo/account", login: true}\n'
             '    - {name: cart, url: "https://shop.demo/cart", login: true}\n'
             '    - {name: help, url: "https://help.shop.demo/", login: false}\n')

    def test_without_pages(self):
        self.write("accessibility: {}\n")
        with self.assertRaises(a11y.ConfigError):
            a11y.configuration(self.tmp.name)

    def test_standard_and_browser_not_supported(self):
        for yml in ('accessibility:\n  standard: "WCAG 9"\n  pages: [{name: a, url: "https://x"}]\n',
                    'accessibility:\n  browser: edge\n  pages: [{name: a, url: "https://x"}]\n',
                    'accessibility:\n  pages: [{name: a, url: "ftp://x"}]\n'):
            with self.subTest(yml=yml):
                self.write(yml)
                with self.assertRaises(a11y.ConfigError):
                    a11y.configuration(self.tmp.name)

    def test_chrome_becomes_chromium_with_a_warning(self):
        self.write('accessibility:\n  browser: chrome\n  pages: [{name: a, url: "https://x"}]\n')
        conf, _ = a11y.configuration(self.tmp.name)
        self.assertEqual(conf["browser"], "chromium")
        self.assertEqual(conf["groups"][0]["engine"]["browser"]["engine"], "chromium")
        self.assertTrue(conf["warnings"])

    def test_login_without_a_value_in_the_env(self):
        self.write("accessibility:\n" + self.LOGIN + '  pages: [{name: a, url: "https://x", login: true}]\n',
                   "SHOP_U=ana\n")
        with self.assertRaises(a11y.ConfigError) as cm:
            a11y.configuration(self.tmp.name)
        self.assertIn("SHOP_P", str(cm.exception))

    def test_one_group_per_login_and_origin_only_on_the_declared_pages(self):
        self.write("accessibility:\n" + self.LOGIN + self.PAGES, "SHOP_U=ana\nSHOP_P=fictional-secret\n")
        conf, _ = a11y.configuration(self.tmp.name)
        g = {(x["login"], x["target"]): x for x in conf["groups"]}
        self.assertEqual(set(g), {(False, "https://shop.demo/"), (True, "https://shop.demo/account"),
                                  (False, "https://help.shop.demo/")})
        with_login = g[(True, "https://shop.demo/account")]["engine"]
        self.assertEqual(with_login["crawl"]["pages"], ["https://shop.demo/cart"])
        self.assertFalse(with_login["crawl"]["follow_links"])
        self.assertEqual(with_login["crawl"]["max_pages"], 2)
        self.assertFalse(with_login["crawl"]["audit_error_pages"])
        self.assertEqual(with_login["modules"]["accessibility"]["tags"],
                         ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
        self.assertEqual(with_login["auth"]["username_selector"], "#u")
        self.assertNotIn("auth", g[(False, "https://shop.demo/")]["engine"])

    def test_credentials_only_in_the_environment_never_in_the_configuration(self):
        self.write("accessibility:\n" + self.LOGIN + self.PAGES, "SHOP_U=ana\nSHOP_P=fictional-secret\n")
        conf, env = a11y.configuration(self.tmp.name)
        self.assertEqual(env, {"QA_A11Y_USER": "ana", "QA_A11Y_PASS": "fictional-secret"})
        self.assertNotIn("fictional-secret", repr(conf))
        self.assertNotIn("ana", repr([g["engine"] for g in conf["groups"]]))

    def test_login_success_by_default_is_leaving_the_login_page(self):
        self.write("accessibility:\n" + self.LOGIN + self.PAGES, "SHOP_U=ana\nSHOP_P=fictional-secret\n")
        conf, _ = a11y.configuration(self.tmp.name)
        auth = next(g for g in conf["groups"] if g["login"])["engine"]["auth"]
        success = re.compile(auth["success_url"])
        for stays in ("https://shop.demo/", "https://shop.demo", "https://shop.demo/?error=1"):
            self.assertIsNone(success.search(stays), stays)
        self.assertTrue(success.search("https://shop.demo/account"))

    def test_the_generated_configuration_is_accepted_by_the_engine(self):
        from qa_audit.core import auth, config
        self.write("accessibility:\n" + self.LOGIN + self.PAGES + "  audit_error_pages: true\n",
                   "SHOP_U=ana\nSHOP_P=fictional-secret\n")
        conf, env = a11y.configuration(self.tmp.name)
        for g in conf["groups"]:
            with self.subTest(target=g["target"]):
                cfg = config.build(None, g["engine"])
                self.assertTrue(cfg["crawl"]["audit_error_pages"])
                self.assertEqual(auth.validate(cfg, environ=env), [])


if __name__ == "__main__":
    unittest.main()
