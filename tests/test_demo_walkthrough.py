"""The reproducible walkthrough of docs/demo-walkthrough.md, run for real on a temporary copy of the fictional demo.

The README and the walkthrough show commands and what they print. This test runs the same commands, so the document
cannot drift from the code: the preflight of a skill, the two fictional ticket checks, a run log with a pass and a failure
carrying a new bug, the dry run of the Excel apply that lists the cells and writes nothing, and the reconciled totals.
Nothing leaves the temporary folder: no network, no tracker, no device, no LibreOffice (`--no-recalc`).

python -m unittest tests.test_demo_walkthrough   (from the root of qa-assistant)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo")
DEFECT = {"title": "Login without password shows no error message", "area": "Login", "priority": "Medium",
          "severity": "Low", "role": "standard_user", "related_tc": "TC-LOGIN-004",
          "description": "Expected: an error says the password is required. Observed: the form stays as it is, no "
                         "message. Steps: open the login page, fill only the user name, press Login."}


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Walkthrough(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "demo")
        shutil.copytree(DEMO, self.ws, ignore=shutil.ignore_patterns("memory", ".env", "node_modules"))
        self.book = os.path.join(self.ws, "workbook", "demo-plan.xlsx")

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        env = {**os.environ, "HOME": self.tmp.name}
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              check=False, env=env)

    def add(self, *args):
        return self.run_("scripts/run_log.py", self.ws, "add", "poc01", *args)

    def test_the_path_from_a_ticket_to_an_approved_preview(self):
        # 1. the skill's preflight, read only
        r = self.run_("scripts/workspace_select.py", "preflight", "--skill", "run-tests", "--workspace", self.ws)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Route: functional-web", r.stdout)

        # 2. two checks of the fictional ticket: the second says what changed
        for name, now in (("check1", "2026-11-02T10:00:00"), ("check2", "2026-11-05T16:00:00")):
            r = self.run_("-m", "adapters.tickets", self.ws, "ingest",
                          os.path.join(self.ws, "tickets", f"bundle_{name}.json"), "--now", now)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("conclusion changes", r.stdout)

        # 3. the run log: a pass, and a failure carrying a bug that is still a proposal
        before = sha(self.book)
        r = self.add("TC-CHK-002", "Pass", "Remove button on the cart page took the product out and the badge went to 0",
                     "--environment", "test", "--build", "walkthrough", "--mode",
                     "manual: documented walkthrough with fictional data")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        proposal = os.path.join(self.ws, "new_defect.json")
        with open(proposal, "w", encoding="utf-8") as f:
            json.dump({**DEFECT, "proposed": True}, f)
        r = self.add("TC-LOGIN-004", "Fail", "No message after submitting without a password", "--new-defect", proposal)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # 4. a proposal is never applied: the dry run refuses it and says how it is approved
        preview = ("-m", "adapters.excel.apply", self.ws, "runs/batch_poc01.json", "--dry-run", "--detail", "--no-recalc")
        r = self.run_(*preview)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn('still a proposal ("proposed")', r.stdout)

        # 5. approved (the user removes "proposed"): the dry run lists the cells and a token, and writes nothing
        self.assertEqual(self.run_("scripts/run_log.py", self.ws, "remove", "poc01", "TC-LOGIN-004").returncode, 0)
        with open(proposal, "w", encoding="utf-8") as f:
            json.dump(DEFECT, f)
        self.assertEqual(self.add("TC-LOGIN-004", "Fail", "No message after submitting without a password",
                                  "--new-defect", proposal).returncode, 0)
        r = self.run_(*preview)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Cells that would change", r.stdout)
        self.assertIn("Defects!B5", r.stdout)
        self.assertIn("Preview token:", r.stdout)
        self.assertEqual(sha(self.book), before)                 # the workbook is exactly as it was

        # 6. the report counts the new batch apart, and says it is not applied yet
        r = self.run_("scripts/qa_report.py", self.ws, "totals")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not applied yet: 2", r.stdout)
        self.assertIn("Build unknown", r.stdout)                 # the older fictional batches have no build recorded


if __name__ == "__main__":
    unittest.main()
