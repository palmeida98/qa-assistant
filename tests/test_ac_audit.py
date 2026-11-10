"""Q3 (phase 19): `qa_report.py audit` shows the acceptance-criteria coverage, per platform, from the workbook and the run
logs of THAT platform; the same case ID with different results on Android and iOS is shown; a missing map is not an
uncovered criterion; a divergent or under-review result stops a «complete» verdict. Fictional workspace.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import ac_coverage as ac
import openpyxl
import qa_report as qr
import test_qa_report as base

CFG = base.CFG
RUN = {"environment": "test", "build": "1.0.0 (7)"}


class AcAudit(base.Base):
    def setUp(self):
        super().setUp()
        self.ios_file = "plan-ios.xlsx"
        shutil.copy(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, self.ios_file))
        self.proj["excel"]["workbooks"] = {
            "ios": {"file": self.ios_file, "platform": "ios", "batches": ["batch_ios_*"]}}
        self.write_project()

    def set_status(self, file, case, status, notes=None):
        path = os.path.join(self.ws, file)
        wb = openpyxl.load_workbook(path)
        for ws in wb.worksheets:
            head = {c.value: c.column for c in ws[1]}
            for r in range(2, ws.max_row + 1):
                if ws.cell(r, 1).value == case:
                    ws.cell(r, head["Status"]).value = status
                    if notes:
                        ws.cell(r, head["Notes"]).value = notes
        wb.save(path)

    def map(self, kind="ticket-memory", cases=("TC-SHOP-001",), name="ac_map_1.json"):
        os.makedirs(os.path.join(self.ws, "analysis"), exist_ok=True)
        with open(os.path.join(self.ws, "analysis", name), "w", encoding="utf-8") as f:
            json.dump({"pbi": 1, "title": "T", "criteria_source": {"kind": kind, "ref": "r"},
                       "criteria": [{"id": "AC1", "text": "a", "cases": list(cases)},
                                    {"id": "AC2", "text": "b", "cases": []}]}, f)

    def audit(self):
        self.batch("batch_android_01", [base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00")], run=RUN)
        self.batch("batch_ios_01", [base.item("TC-SHOP-001", "Fail", "2026-11-10T10:00:00")], run=RUN)
        return qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))

    def test_a_missing_map_is_said_apart_from_an_uncovered_criterion(self):
        rep = self.audit()
        self.assertEqual(rep["ac_coverage"]["maps"], [])
        text = qr.render_audit(rep, True)
        self.assertIn("no map in analysis/ac_map_*.json: coverage NOT assessed", text)
        self.assertNotIn("not covered by any case", text)

    def test_each_platform_uses_its_own_workbook_and_run_logs_and_shows_the_difference(self):
        self.set_status(CFG["file"], "TC-SHOP-001", "Pass")
        self.set_status(self.ios_file, "TC-SHOP-001", "Fail")
        self.map()
        rep = self.audit()
        reports = {r["platform"]: r for r in rep["ac_coverage"]["maps"][0]["reports"]}
        self.assertEqual({p: r["criteria"][0]["verdict"] for p, r in reports.items()},
                         {"android": "passed", "ios": "open"})
        self.assertEqual(reports["ios"]["criteria"][0]["cases"][0]["status"], "Fail")
        self.assertEqual(rep["ac_coverage"]["maps"][0]["platform_differences"],
                         ["TC-SHOP-001: android Pass, ios Fail"])
        text = qr.render_audit(rep, True)
        self.assertIn("criterion AC2 is not covered by any case", text)
        self.assertIn("same case, different result between platforms: TC-SHOP-001: android Pass, ios Fail", text)
        self.assertIn("complete coverage NO", text)

    def test_a_result_under_review_or_divergent_is_shown_and_blocks_complete(self):
        self.set_status(CFG["file"], "TC-SHOP-001", "Pass", notes="[10.11] UNDER REVIEW: no proof of X")
        self.set_status(self.ios_file, "TC-SHOP-001", "Pass")        # the iOS run log says Fail: divergent
        self.map(cases=("TC-SHOP-001",))
        rep = self.audit()
        problems = "\n".join(p for r in rep["ac_coverage"]["maps"][0]["reports"] for p in r["problems"])
        self.assertIn("results under review or whose latest run-log result differs from the workbook: TC-SHOP-001", problems)
        self.assertFalse(any(r["complete"] for r in rep["ac_coverage"]["maps"][0]["reports"]))

    def test_a_map_limited_to_a_platform_is_not_evaluated_on_the_others(self):
        self.map()
        path = os.path.join(self.ws, "analysis", "ac_map_1.json")
        with open(path, encoding="utf-8") as f:
            amap = json.load(f)
        amap["platforms"] = ["android"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(amap, f)
        m = self.audit()["ac_coverage"]["maps"][0]
        self.assertEqual([r["platform"] for r in m["reports"]], ["android"])
        self.assertEqual(m["platform_differences"], [])

    def test_a_source_that_is_not_available_is_not_an_uncovered_criterion(self):
        self.map(kind="none", cases=())
        text = qr.render_audit(self.audit(), True)
        self.assertIn("the source of the acceptance criteria is not available", text)

    def test_the_batches_to_fix_do_not_change_and_a_broken_map_does_not_stop_the_audit(self):
        os.makedirs(os.path.join(self.ws, "analysis"))
        with open(os.path.join(self.ws, "analysis", "ac_map_9.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        rep = self.audit()
        self.assertEqual(rep["ac_coverage"]["maps"], [])
        self.assertEqual(len(rep["ac_coverage"]["unreadable"]), 1)
        self.assertEqual(rep["problems"], 0)

    def test_the_report_command_accepts_a_platform(self):
        self.set_status(self.ios_file, "TC-SHOP-001", "Fail")
        self.map()
        self.audit()
        with open(os.path.join(self.ws, "analysis", "ac_map_1.json"), encoding="utf-8") as f:
            amap = json.load(f)
        rep = ac.report(self.ws, amap, platform="ios")
        self.assertEqual((rep["platform"], rep["criteria"][0]["verdict"]), ("ios", "open"))


if __name__ == "__main__":
    unittest.main()
