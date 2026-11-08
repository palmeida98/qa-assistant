"""qa-ui with several workbooks (closing of the audit, 07.11.2026), on a fictional workspace:

- the Overview names each workbook, the batches it takes and whether its file can be opened;
- a run log says its environment, its build (`unknown` when the run block has none) and the platform and workbook
  the routing rule gives it, or that they are not certain;
- an unreadable workbook is a problem of the apply check, never a dropped connection, and any unexpected error of a
  request is still an answer (the page would otherwise say that the interface is not running).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import shutil
import sys
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import workspace_kit as kit

from qa_ui import overview, runs, server
from qa_ui import workspace as wk

IOS = "plan-ios.xlsx"


class Workbooks(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace(report={"platforms": {"web": ["batch_2026*"], "ios": ["batch_ios*"]}})
        self.ws = self.tmp.name
        self.proj = wk.project(self.ws)
        self.proj["excel"]["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
        kit.write_project(self.ws, self.proj)
        shutil.copy(os.path.join(self.ws, kit.EXCEL["file"]), os.path.join(self.ws, IOS))
        os.makedirs(os.path.join(self.ws, "runs"))

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, name, run=None, status="Pass"):
        doc = {"results": [{"tc": "TC-SHOP-001", "status": status, "actual_result": "fictional", "ts": "2026-11-07T10:00"}]}
        if run is not None:
            doc["run"] = run
        with open(os.path.join(self.ws, "runs", name + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_the_overview_names_each_workbook_and_whether_it_opens(self):
        rows = overview.workbooks(self.ws, self.proj)
        self.assertEqual([(r["file"], r["state"]) for r in rows], [(kit.EXCEL["file"], "ok"), (IOS, "ok")])
        self.assertIn("ios: batches batch_ios*", rows[1]["for"])
        with open(os.path.join(self.ws, IOS), "wb") as f:
            f.write(b"fictional: not a workbook")
        self.assertEqual(overview.workbooks(self.ws, self.proj)[1]["state"], "cannot be opened (not a workbook file)")
        os.remove(os.path.join(self.ws, IOS))
        self.assertEqual(overview.workbooks(self.ws, self.proj)[1]["state"], "missing")

    def test_a_run_log_says_its_platform_and_workbook_and_an_unknown_build(self):
        self.log("batch_ios_01")
        out = wk.read_run_log(self.ws, self.proj, "batch_ios_01")
        self.assertEqual(out["run"], {})                        # the page says "build unknown"
        self.assertEqual(out["route"], {"platform": "ios", "workbook": IOS, "problem": None})
        self.log("batch_ios_02", run={"build": "1.0 (1)", "platform": "web"})
        route = wk.read_run_log(self.ws, self.proj, "batch_ios_02")["route"]
        self.assertIn("the platforms do not agree", route["problem"])

    def test_an_ambiguous_batch_is_not_ready_and_names_the_reason(self):
        self.log("batch_smoke_01", run={"build": "1.0 (1)"})
        check = wk.check_apply(self.ws, self.proj, "batch_smoke_01")
        self.assertFalse(check["ready"])
        self.assertIn("no platform recognised", check["problems"][0])

    def test_an_unreadable_workbook_is_a_problem_of_the_check_never_a_crash(self):
        self.log("batch_ios_03", run={"build": "1.0 (1)"})
        with open(os.path.join(self.ws, IOS), "wb") as f:
            f.write(b"fictional: not a workbook")
        check = wk.check_apply(self.ws, self.proj, "batch_ios_03")
        self.assertFalse(check["ready"])
        self.assertTrue(any(f"{IOS} (excel.workbooks.ios) could not be read" in p for p in check["problems"]),
                        check["problems"])

    def test_an_unexpected_error_of_a_request_is_still_an_answer(self):
        self.log("batch_ios_04", run={"build": "1.0 (1)"})
        app = server.App(self.ws, runs.Runner(self.ws))
        with mock.patch.object(wk, "check_apply", side_effect=zipfile.BadZipFile("File is not a zip file")):
            status, body = app.handle("GET", "/api/runlogs/batch_ios_04")
        self.assertEqual(status, 500)
        self.assertIn("BadZipFile", body["error"])


if __name__ == "__main__":
    unittest.main()
