"""The daily path the five pilot improvements serve, end to end on a fictional workspace and a fictional iPhone
(docs/pilot-improvements-2026-11-07.md F): a batch with its mode; the diagnosis of a device that is not available; a
send that finds the field itself, with burst evidence; the result recorded; the dry run of the apply to the right
workbook; the apply equal to the preview; and a checkpoint resumed without a duplicate.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import device as dv
import device_ios as wda
import qa_report
import run_log as rl
import workspace_kit as kit
from test_device_ios import Clock
from test_ios_diagnose import Fake as FakeProbes
from test_streaming_evidence import MESSAGE, Streamed

from adapters.excel import apply as ap

IOS = "plan-ios.xlsx"
BATCH = "ios_pilot_01"


class PilotFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.ws = self.tmp.name
        shutil.copy(os.path.join(self.ws, kit.EXCEL["file"]), os.path.join(self.ws, IOS))
        with open(os.path.join(self.ws, "project.yaml"), encoding="utf-8") as f:
            proj = yaml.safe_load(f)
        proj["excel"]["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
        proj["report"] = {"platforms": {"web": ["batch_2026*"], "ios": ["batch_ios*"]}}
        proj["mobile"] = {"platform": "ios", "evidence_batch": f"batch_{BATCH}",
                          "ios": {"app_id": "com.demoshop.app", "udid": "00008110-FICTIONAL0001"}}
        kit.write_project(self.ws, proj)
        self.environ = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                    if k not in ("QA_WORKBOOK", "QA_LOCK_WAIT", "QA_WDA_LOG")},
                                       clear=True)
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.tmp.cleanup()

    def quiet(self, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fn(*args)
        return rc, out.getvalue() + err.getvalue()

    def device(self, request, probes, *args):
        clock = Clock()
        with mock.patch.object(wda, "request", request), mock.patch.object(dv, "ios_probes", probes), \
                mock.patch.object(dv.Burst, "threaded", False), \
                mock.patch.object(dv.time, "sleep", clock.sleep), mock.patch.object(dv.time, "monotonic", clock.now):
            return self.quiet(dv.main, [self.ws, *args])

    def cells(self, file):
        wb = openpyxl.load_workbook(os.path.join(self.ws, file), data_only=False)
        try:
            return {(ws.title, c.coordinate): c.value for ws in wb for row in ws.iter_rows() for c in row
                    if c.value is not None}
        finally:
            wb.close()

    def test_the_whole_path(self):
        name = f"batch_{BATCH}"
        # 1. the batch with its mode, before any result
        rc, out = self.quiet(rl.main, [self.ws, "mode", BATCH, "conversational: generated answers"])
        self.assertEqual(rc, 0, out)

        # 2. the iPhone is not available: the diagnosis says why and what to do, nothing is typed
        def down(cfg, method, path, body=None, timeout=30):
            raise wda.Unreachable("WebDriverAgent did not answer")
        locked = FakeProbes(status={"_error": "x"}, lock={"result": {"passcodeRequired": True}})
        rc, out = self.device(down, locked, "check")
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("blocked by: the iPhone is locked"), out)

        # 3-4. the person unlocks it: the send finds the field, the burst watches the answer the tree cannot read
        app = Streamed(["", "Your", "Your order ships tomorrow."], readable=False)
        rc, out = self.device(app, FakeProbes(), "send", MESSAGE, "--capture", "TC-SHOP-001_t1", "--timeout", "60",
                              "--samples", "10", "--sample-interval", "2", "--burst", "6", "--burst-interval", "2")
        self.assertEqual(rc, 0, out)
        self.assertIn("outcome: sent", out)
        self.assertIn("evidence: answer text not readable", out)
        manifest = os.path.join(self.ws, "evidence", name, "TC-SHOP-001_t1_burst.json")
        with open(manifest, encoding="utf-8") as f:
            shots = [x["file"] for x in json.load(f)["files"]]
        self.assertEqual(len(shots), 6)

        # 5. the result recorded (the mode given again is the same: no change)
        note = "answer shown progressively in the burst screenshots (content judged)"
        rc, out = self.quiet(rl.main, [self.ws, "add", BATCH, "TC-SHOP-001", "Pass", note, "--evidence", *shots,
                                       "--environment", "test", "--build", "1.0 (2)",
                                       "--mode", "conversational: generated answers"])
        self.assertEqual(rc, 0, out)

        # 6. the dry run picks the iOS workbook and writes nothing
        before = {f: self.cells(f) for f in (kit.EXCEL["file"], IOS)}
        res = ap.dry_run(self.ws, f"runs/{name}.json", recalc=False)
        self.assertEqual((res["workbook"], res["platform"], res["problems"], res["code"]), (IOS, "ios", [], 0))
        self.assertEqual({f: self.cells(f) for f in before}, before)

        # 7. the apply with the preview's token writes exactly what the preview showed
        rc, out = self.quiet(ap.main, [self.ws, f"runs/{name}.json", "--no-recalc", "--now", res["now"],
                                       "--expect", res["token"]])
        self.assertEqual(rc, 0, out)
        after = self.cells(IOS)
        written = {k: v for k, v in after.items() if before[IOS].get(k) != v}
        self.assertEqual({(c["sheet"], c["cell"]): c["after"] for c in res["cells"]}, written)
        self.assertEqual(self.cells(kit.EXCEL["file"]), before[kit.EXCEL["file"]])

        # 8. a checkpoint resumed: nothing is applied twice, the run log is closed, the audit says applied
        again = ap.dry_run(self.ws, f"runs/{name}.json", recalc=False)
        self.assertTrue(again["problems"] or again.get("already_applied"), again)
        rc, out = self.quiet(ap.main, [self.ws, f"runs/{name}.json", "--no-recalc"])
        self.assertEqual(rc, 0)
        self.assertIn("Nothing applied", out)
        self.assertEqual(self.cells(IOS), after)
        rc, out = self.quiet(rl.main, [self.ws, "add", BATCH, "TC-SHOP-002", "Pass", "x"])
        self.assertEqual(rc, 1)
        proj = rl.read_project(self.ws)
        report = qa_report.audit(self.ws, proj, qa_report.load_batches(self.ws, proj))
        entry = next(e for e in report["batches"] if e["batch"] == name)
        self.assertIn(f"applied to {IOS}", entry["applied"])
        self.assertEqual(entry["mode"], "conversational: generated answers")
        wb = openpyxl.load_workbook(os.path.join(self.ws, IOS), read_only=True)
        rows = [r for r in wb["_qa_batches"].iter_rows(min_row=2, values_only=True) if r[0]]
        wb.close()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
