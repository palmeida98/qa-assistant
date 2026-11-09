"""The Excel write paths with the real recalculation (LibreOffice), through the commands the skills call.

Moved here from the tests of the retired local interface (plan phase 16): what they protected was shared logic, not the
interface. Approved cases are added through `adapters.excel.add_cases` (a dry run, then the write, then refused as
a repeat), and a batch is applied through `adapters.excel.apply` (a dry run with a preview token, a stale token refused, the
write with `--expect`, a second apply refused) with the workbook recalculated, on the fixture workbook of the Excel golden
(fictional Demo Shop). Skipped without LibreOffice, so the CI runs it in the `golden` job.

python -m unittest tests.test_excel_recalc_flows   (from the root of qa-assistant)
"""

import hashlib
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import workspace_kit as kit

from adapters.excel import ExcelAdapter
from adapters.excel.recalc import find_soffice

NEW = {"cases": [{"id": "TC-SHOP-005", "title": "Pay with an expired card", "expected": "The payment is refused",
                  "role": "customer", "steps": [{"action": "Add an item", "data": None, "expected": None},
                                                {"action": "Pay", "data": None, "expected": None}]}]}


def soffice() -> bool:
    try:
        return bool(find_soffice())
    except FileNotFoundError:
        return False


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@unittest.skipUnless(soffice(), "needs LibreOffice (the workbook's recalculation)")
class RecalculatedWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.addCleanup(self.tmp.cleanup)
        self.ws = self.tmp.name
        self.book = os.path.join(self.ws, kit.EXCEL["file"])

    def run_(self, *args):
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              check=False)

    def test_approved_cases_are_added_once_through_the_adapter(self):
        path = os.path.join(self.ws, "cases_new.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(NEW, f)
        before = sha(self.book)
        dry = self.run_("-m", "adapters.excel.add_cases", self.ws, path, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(sha(self.book), before)                                  # a dry run writes nothing
        done = self.run_("-m", "adapters.excel.add_cases", self.ws, path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        self.assertEqual(ad.case_rows()["Shop"][-1]["ID"], "TC-SHOP-005")
        again = self.run_("-m", "adapters.excel.add_cases", self.ws, path)       # the same file again changes nothing
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        self.assertEqual([r["ID"] for r in ad.case_rows()["Shop"]].count("TC-SHOP-005"), 1)

    def test_a_batch_is_applied_with_the_preview_token_and_only_once(self):
        def add(*args):
            r = self.run_("scripts/run_log.py", self.ws, "add", "b1", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        add("TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build", "1.0")
        add("TC-SHOP-002", "Pass", "ok")
        defect = os.path.join(self.ws, "defect.json")
        with open(defect, "w", encoding="utf-8") as f:
            json.dump({"title": "Coupon ignored", "area": "Checkout", "priority": "P3", "severity": "Low",
                       "related_tc": "TC-SHOP-003",
                       "description": "Expected: a discount. Observed: none."}, f)
        add("TC-SHOP-003", "Fail", "no discount", "--new-defect", defect)
        batch = os.path.join("runs", "batch_b1.json")
        before = sha(self.book)
        preview = self.run_("-m", "adapters.excel.apply", self.ws, batch, "--dry-run", "--detail")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertEqual(sha(self.book), before)
        token = next(line.split()[2] for line in preview.stdout.splitlines() if line.startswith("Preview token:"))
        stale = self.run_("-m", "adapters.excel.apply", self.ws, batch, "--expect", "0" * 16)
        self.assertNotEqual(stale.returncode, 0)                                  # a stale or unknown preview
        self.assertEqual(sha(self.book), before)
        done = self.run_("-m", "adapters.excel.apply", self.ws, batch, "--expect", token)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        status = ad.cfg["cases"]["columns"]["status"]
        by_id = {r["ID"]: r[status] for r in ad.case_rows()["Shop"]}
        self.assertEqual((by_id["TC-SHOP-001"], by_id["TC-SHOP-002"], by_id["TC-SHOP-003"]), ("Pass", "Pass", "Fail"))
        written = sha(self.book)
        again = self.run_("-m", "adapters.excel.apply", self.ws, batch)             # the same batch again: no effect
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(sha(self.book), written)


if __name__ == "__main__":
    unittest.main()
