"""tracker: linear (methodology 4.2): accepted with a Linear ticket source, read only, no write adapter. Fictional Demo
Shop data and the fake Linear GraphQL API of tests/linear_fake.py: no network, no real workspace, no real key.

python -m unittest tests.test_tracker_linear   (from the root of qa-assistant)
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "scripts")]

import validate_project as vp
import workspace_select
from test_tickets_linear import LinearBase

DEMO = {"project": "Demo Shop", "environments": {"test": "https://test.demo-shop.example"},
        "modules": ["functional-web"], "bug_format": "[DS] <area>: <summary>"}


def write(ws, proj):
    with open(os.path.join(ws, "project.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(proj, f, allow_unicode=True)


class LinearTracker(LinearBase):
    def write_project(self, tickets):
        write(self.ws, {**DEMO, "tracker": "linear", "tickets": tickets})

    def test_tracker_linear_is_accepted_with_a_linear_source_and_says_it_writes_nothing(self):
        with open(os.path.join(self.ws, "project.yaml"), encoding="utf-8") as f:
            problems, warnings = vp.base_problems(yaml.safe_load(f))
        self.assertEqual(problems, [])
        self.assertTrue(any("no write adapter" in w and "by hand" in w for w in warnings), warnings)

    def test_tracker_linear_without_a_linear_source_is_a_problem(self):
        for tickets in (None, {"sources": {"boards": {"service": "azure-devops", "access": "browser",
                                                      "organization_url": "https://dev.azure.com/demo-shop",
                                                      "project": "Demo Shop"}}}):
            with self.subTest(tickets=bool(tickets)):
                proj = {**DEMO, "tracker": "linear", **({"tickets": tickets} if tickets else {})}
                problems, _ = vp.base_problems(proj)
                self.assertTrue(any("tracker: linear needs a Linear source" in p for p in problems), problems)

    def test_the_analyze_ticket_preflight_passes_and_a_ticket_is_read(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = workspace_select.main(["preflight", "--workspace", self.ws, "--skill", "analyze-ticket", "--brief"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("(valid)", out.getvalue())
        fake = self.fake()
        self.assertEqual(self.collect_lin(fake, "DS-12"), 0, "\n".join(self.lines))
        doc = self.memory_doc(self.last_check(), "DS-12")
        self.assertIn("Cupão expirado", str(doc))
        self.assertTrue(fake.requests)
        self.assertTrue(all(r.read_only and b"mutation" not in (r.body or b"") for r in fake.requests))


class OtherTrackersUnchanged(unittest.TestCase):
    def test_an_unknown_tracker_is_still_refused_and_excel_needs_no_linear_source(self):
        problems, _ = vp.base_problems({**DEMO, "tracker": "trello"})
        self.assertTrue(any("outside" in p and "linear" in p for p in problems), problems)
        problems, warnings = vp.base_problems({**DEMO, "tracker": "excel"})
        self.assertFalse(any("linear" in x.lower() for x in problems + warnings), problems + warnings)


if __name__ == "__main__":
    unittest.main()
