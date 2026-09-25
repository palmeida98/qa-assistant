"""Functional checks as pure functions (docs/qa-audit.md §18). No browser, no network."""

import unittest

from qa_audit.modules.functional import checks
from qa_audit.modules.functional.module import MODULE

OWN = "https://shop.example"


class Resources(unittest.TestCase):
    def test_own_third_party_and_aborted(self):
        failed = [
            {"url": f"{OWN}/img/a.png", "status": 404, "type": "image"},
            {"url": f"{OWN}/img/a.png", "status": 404, "type": "image"},              # same URL once
            {"url": "https://cdn.example/lib.js", "error": "net::ERR_NAME_NOT_RESOLVED", "type": "script"},
            {"url": f"{OWN}/api/poll", "error": "net::ERR_ABORTED", "type": "fetch"},    # cancelled, not broken
            {"url": "https://ads.example/x", "error": "blockedbyclient", "type": "script"},  # --block-third-party
            {"url": "data:image/png;base64,AA", "error": "x"},
        ]
        own, third = checks.resource_failures(failed, OWN)
        self.assertEqual([r["url"] for r in own], [f"{OWN}/img/a.png"])
        self.assertEqual([r["url"] for r in third], ["https://cdn.example/lib.js"])
        self.assertEqual(checks.describe_failure(own[0]), f"{OWN}/img/a.png (image): HTTP 404")


class Console(unittest.TestCase):
    def test_load_errors_are_left_to_broken_resource(self):
        msgs = ["Failed to load resource: the server responded with a status of 404", "TypeError: x is undefined"]
        self.assertEqual(checks.console_errors(msgs), ["TypeError: x is undefined"])


class Forms(unittest.TestCase):
    def test_email_fields(self):
        forms = [{"action": f"{OWN}/news", "fields": [
            {"tag": "input", "type": "text", "name": "email", "label": "", "pattern": "", "autocomplete": ""},
            {"tag": "input", "type": "text", "name": "contact", "label": "Your e-mail", "pattern": "",
             "autocomplete": ""},
            {"tag": "input", "type": "text", "name": "x", "label": "", "pattern": "", "autocomplete": "email"},
            {"tag": "input", "type": "email", "name": "email2", "label": "", "pattern": "", "autocomplete": ""},
            {"tag": "input", "type": "text", "name": "mail_email", "label": "", "pattern": ".+@.+",
             "autocomplete": ""},
            {"tag": "input", "type": "hidden", "name": "email_hash", "label": "", "pattern": "",
             "autocomplete": ""},
            {"tag": "input", "type": "text", "name": "city", "label": "City", "pattern": "", "autocomplete": ""},
        ]}]
        self.assertEqual([f["name"] for f in checks.email_fields_without_validation(forms)],
                         ["email", "contact", "x"])


class Links(unittest.TestCase):
    def test_status(self):
        self.assertIsNone(checks.link_status_check(200))
        self.assertIsNone(checks.link_status_check(301))
        self.assertEqual(checks.link_status_check(404), "broken-link")
        self.assertEqual(checks.link_status_check(503), "broken-link-server-error")
        self.assertIsNone(checks.link_status_check(None))


class Configuration(unittest.TestCase):
    def test_defaults_are_safe(self):
        cfg = MODULE.default_config()
        self.assertEqual(cfg["forms"], [])
        self.assertFalse(cfg["check_external_links"])
        self.assertEqual(MODULE.validate_config(cfg), [])

    def test_bad_form_entries(self):
        problems = MODULE.validate_config({"forms": [{"page": "("}, {"page": "/x", "selector": "form",
                                                                     "fields": ["a"]}], "max_link_checks": -1})
        self.assertEqual(len(problems), 3)


if __name__ == "__main__":
    unittest.main()
