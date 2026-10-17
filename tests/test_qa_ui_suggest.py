"""qa-ui suggestion rules (docs/qa-ui.md section 5; plan 12.1): one module per case with the signals it came from,
the ties, the cards with their levels and what makes a card ready. Fictional data only (Demo Shop).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import qa_ui_kit as kit

from qa_ui import MODULES
from qa_ui import suggest as sg
from qa_ui.intake import new_case

READY = [(True, "git", ""), (True, ".env", ""), (True, "skills", ""), (True, "node", ""), (True, "qa-audit", ""),
         (True, "browser chromium", "")]


def case(id_: str, title: str, expected: str = "something observable", **fields) -> dict:
    return new_case({"id": id_, "title": title, "expected": expected, **fields}, "text")


def module(*args, **fields) -> str:
    return sg.assign(case(*args, **fields))["module"]


class Rules(unittest.TestCase):
    def test_each_module_by_its_signals(self):
        for expected, c in (
                ("api", case("ORD-002", "POST /orders without a token", "401")),
                ("api", case("API-007", "List the orders")),
                ("api", case("ORD-003", "The response payload has the order id")),
                ("mobile", case("ORD-004", "Pay with a valid card", platform="Android")),
                ("mobile", case("MOB-001", "Pay with a valid card")),
                ("mobile", case("ORD-005", "Swipe the item out of the cart on the device")),
                ("accessibility", case("A11Y-001", "Payment fields")),
                ("accessibility", case("ORD-006", "Payment fields", "WCAG 1.3.1 is met")),
                ("accessibility", case("ORD-007", "The error is announced by a screen reader")),
                ("exploratory", case("EXP-001", "The new checkout")),
                ("exploratory", case("ORD-008", "Charter: explore the new checkout with expired coupons")),
                ("functional-web", case("CHK-011", "Click the Pay button on the checkout page")),
                ("functional-web", case("CHK-012", "Pay with a refused card"))):
            with self.subTest(case=c["id"]):
                self.assertEqual(sg.assign(c)["module"], expected)

    def test_a_case_with_no_signal_is_functional_web_and_says_so(self):
        a = sg.assign(case("CHK-012", "Pay with a refused card"))
        self.assertEqual((a["module"], a["signals"], a["why"]), ("functional-web", [], "no signal: the default"))

    def test_the_signals_are_returned_with_their_weights(self):
        a = sg.assign(case("API-002", "POST /orders without a token", "401 and a JSON error"))
        self.assertEqual({(s["module"], s["signal"], s["weight"]) for s in a["signals"]},
                         {("api", "ID part API", 3), ("api", "HTTP call POST /orders", 3),
                          ("api", "the expected result is an HTTP status", 1), ("api", '"json"', 1)})
        self.assertEqual(a["why"], 'ID part API, HTTP call POST /orders, the expected result is an HTTP status, "json"')

    def test_words_are_whole_and_counted_once(self):
        self.assertEqual(sg.signals(case("CHK-001", "Tapestry and devices, the zoomed page page page")),
                         [{"module": "functional-web", "signal": '"page"', "weight": 1}])
        self.assertEqual(module("CHK-002", "The rest of the cart is kept"), "functional-web")
        self.assertEqual(module("CHK-003", "The REST contract of the cart"), "api")
        self.assertEqual(module("ACC-001", "Change the account's e-mail"), "functional-web")

    def test_the_highest_total_wins(self):
        # functional-web 3 (click, button, login) against accessibility 2 (keyboard, focus)
        self.assertEqual(module("LOG-001", "Click the login button with the keyboard focus on it"), "functional-web")
        # accessibility 2 against functional-web 1
        self.assertEqual(module("LOG-002", "Keyboard focus stays visible on the page"), "accessibility")

    def test_a_tie_goes_to_the_first_of_the_tie_order(self):
        a = sg.assign(case("ORD-009", "Tap to send the request"))                  # mobile 1, api 1
        self.assertEqual(a["module"], "api")
        self.assertIn("a tie with mobile", a["why"])
        self.assertEqual(module("ORD-010", "Zoom on the page"), "accessibility")   # accessibility 1, functional-web 1
        self.assertEqual(module("ORD-011", "Tap with the keyboard open"), "mobile")

    def test_the_cases_own_module_field_is_kept(self):
        a = sg.assign(case("API-001", "POST /orders", module="Mobile"))
        self.assertEqual((a["module"], a["why"]), ("mobile", "the case names its module"))
        self.assertEqual(module("API-001", "POST /orders", module="smoke"), "api")   # not a module of the list

    def test_a_case_moved_by_the_user_says_so(self):
        cases = [case("CHK-011", "Pay with a valid card"), case("API-002", "POST /orders")]
        out = sg.suggest(cases, kit.PROJECT, READY, "claude", moved={"CHK-011": "exploratory", "API-002": "api",
                                                                     "NOPE-1": "api"})
        self.assertEqual(out["assignments"]["CHK-011"]["module"], "exploratory")
        self.assertTrue(out["assignments"]["CHK-011"]["moved"])
        self.assertIn("the rules said functional-web", out["assignments"]["CHK-011"]["why"])
        self.assertNotIn("moved", out["assignments"]["API-002"])


class ActiveModules(unittest.TestCase):
    """The first real batch: a mobile-only project got every case suggested as functional-web (not active), and the
    batch could not start. The rules choose among the project's modules."""

    def test_a_case_with_no_signal_gets_the_projects_default(self):
        for active, expected in ((None, "functional-web"), ({"functional-web", "api"}, "functional-web"),
                                 ({"mobile", "exploratory"}, "mobile"), ({"api"}, "api"),
                                 ({"exploratory"}, "exploratory")):
            with self.subTest(active=active):
                a = sg.assign(case("CHK-012", "Pay with a refused card"), active)
                self.assertEqual(a["module"], expected)
        self.assertEqual(sg.assign(case("CHK-012", "Pay with a refused card"), {"mobile"})["why"],
                         "no signal: the project's default (mobile)")

    def test_the_signals_of_an_inactive_module_are_named_but_not_followed(self):
        a = sg.assign(case("CHK-011", "Click the Pay button on the checkout page"), {"mobile", "exploratory"})
        self.assertEqual(a["module"], "mobile")
        self.assertIn("signals of functional-web, not active in project.yaml", a["why"])
        a = sg.assign(case("ORD-009", "Tap to send the request"), {"mobile"})     # api 1, mobile 1: api is inactive
        self.assertEqual(a["module"], "mobile")
        self.assertIn("not active", a["why"])

    def test_an_active_module_still_wins_by_its_signals(self):
        self.assertEqual(sg.assign(case("EXP-001", "The new checkout"), {"mobile", "exploratory"})["module"],
                         "exploratory")

    def test_suggest_uses_the_projects_modules(self):
        proj = dict(kit.PROJECT, modules=["mobile"])
        out = sg.suggest([case("CHK-012", "Pay with a refused card")], proj, READY, "claude")
        self.assertEqual(out["assignments"]["CHK-012"]["module"], "mobile")
        self.assertEqual({c["key"]: c["level"] for c in out["cards"]}["functional-web"], "not-suggested")


class Cards(unittest.TestCase):
    def cards(self, cases, proj=None, checks=READY, claude="claude") -> dict:
        out = sg.suggest(cases, proj or kit.PROJECT, checks, claude)
        return {c["key"]: c for c in out["cards"]}

    def test_one_card_per_module_and_per_audit_in_a_fixed_order(self):
        out = sg.suggest([], kit.PROJECT, READY, "claude")
        self.assertEqual([c["key"] for c in out["cards"]], [*MODULES, "audit-a11y", "audit-site"])
        self.assertTrue(all(c["level"] == "not-suggested" for c in out["cards"]))

    def test_a_module_is_recommended_with_at_least_one_case(self):
        cards = self.cards([case("CHK-011", "Pay with a valid card"), case("API-002", "POST /orders"),
                            case("API-003", "GET /orders")])
        self.assertEqual((cards["functional-web"]["level"], cards["functional-web"]["cases"]),
                         ("recommended", ["CHK-011"]))
        self.assertEqual((cards["api"]["level"], cards["api"]["cases"], cards["api"]["runs"]),
                         ("recommended", ["API-002", "API-003"], "/test-api"))
        self.assertEqual(cards["mobile"]["level"], "not-suggested")

    def test_the_site_audit_is_optional_with_a_web_case(self):
        self.assertEqual(self.cards([case("API-002", "POST /orders")])["audit-site"]["level"], "not-suggested")
        card = self.cards([case("CHK-011", "Pay with a valid card")])["audit-site"]
        self.assertEqual((card["level"], card["ready"], card["runs"]), ("optional", True, "qa-audit <test>"))

    def test_the_accessibility_audit_needs_a_form_like_case_and_the_module_active(self):
        form = [case("CHK-011", "Fill in the payment form")]
        self.assertEqual(self.cards(form)["audit-a11y"]["level"], "not-suggested")       # the module is not active
        proj = dict(kit.PROJECT, modules=["functional-web", "accessibility"],
                    accessibility={"pages": [{"name": "checkout", "url": "https://shop.example/checkout"}]})
        card = self.cards(form, proj)["audit-a11y"]
        self.assertEqual((card["level"], card["ready"]), ("optional", True))
        self.assertIn("CHK-011", card["reason"])
        self.assertEqual(self.cards([case("CHK-012", "Open the catalogue")], proj)["audit-a11y"]["level"],
                         "not-suggested")
        empty = dict(proj, accessibility={"pages": []})
        self.assertIn("accessibility.pages", " ".join(self.cards(form, empty)["audit-a11y"]["blockers"]))

    def test_a_module_that_is_not_active_is_never_ready(self):
        # the rules never choose an inactive module (ActiveModules); a case that names its module still can
        self.assertEqual(self.cards([case("MOB-001", "Pay in the app")])["mobile"]["level"], "not-suggested")
        card = self.cards([case("MOB-001", "Pay in the app", module="mobile")])["mobile"]
        self.assertEqual((card["level"], card["active"], card["ready"]), ("recommended", False, False))
        self.assertIn("`mobile` is not in `modules:`", card["blockers"][0])

    def test_a_missing_tool_blocks_only_the_cards_that_need_it(self):
        checks = [(True, ".env", ""), (True, "skills", ""), (False, "node", "install Node.js 22+"),
                  (False, "libreoffice", "install LibreOffice")]
        cards = self.cards([case("CHK-011", "Pay"), case("API-002", "POST /orders")], checks=checks)
        self.assertEqual(cards["functional-web"]["blockers"], ["node: install Node.js 22+"])
        self.assertTrue(cards["api"]["ready"])            # the API module needs neither Node.js nor LibreOffice
        checks = [(False, ".env", "misses SHOP_USER"), (False, "browser chromium", "python -m playwright install")]
        cards = self.cards([case("CHK-011", "Pay")], checks=checks)
        self.assertEqual(cards["functional-web"]["blockers"], [".env: misses SHOP_USER"])
        self.assertEqual(cards["audit-site"]["blockers"], ["browser chromium: python -m playwright install"])

    def test_without_claude_code_no_skill_can_run_but_the_audits_can(self):
        cards = self.cards([case("CHK-011", "Pay")], claude=None)
        self.assertIn("Claude Code was not found", cards["functional-web"]["blockers"][0])
        self.assertTrue(cards["audit-site"]["ready"])

    def test_the_test_environment_is_never_guessed(self):
        self.assertEqual(sg.find_test_environment({"environments": {"test": "https://shop.example/"}})[0], "test")
        self.assertEqual(sg.find_test_environment({"environments": {"staging": "https://shop.example/"}})[0],
                         "staging")
        self.assertEqual(sg.find_test_environment({"environments": {"test": "https://shop.example/",
                                                                    "production": "https://shop.example/"}})[0],
                         "test")
        key, reason = sg.find_test_environment({"environments": {"staging": "https://s.shop.example/",
                                                                 "uat": "https://u.shop.example/"}})
        self.assertIsNone(key)
        self.assertIn("several environments (staging, uat)", reason)
        self.assertIsNone(sg.find_test_environment({"environments": {"test": ""}})[0])
        proj = dict(kit.PROJECT, environments={"staging": "https://s.shop.example/", "uat": "https://u.shop.example/"})
        self.assertFalse(self.cards([case("CHK-011", "Pay")], proj)["audit-site"]["ready"])

    def test_find_claude(self):
        tmp = kit.workspace()
        self.addCleanup(tmp.cleanup)
        exe = kit.executable(tmp.name, "claude", "print('fake')\n")
        self.assertEqual(sg.find_claude(exe), exe)
        self.assertIsNone(sg.find_claude(os.path.join(tmp.name, "missing")))


if __name__ == "__main__":
    unittest.main()
