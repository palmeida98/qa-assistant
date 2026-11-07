"""The dry run of the Excel apply (docs/pilot-improvements-2026-11-07.md A; technique-excel.md section 1.1), on a
fictional workspace with two workbooks (Android by default, iOS by name):

- it routes, validates and transforms with the apply's own pipeline, on a temporary copy, and lists the cells;
- nothing in the workspace changes (workbooks, run log), on success and on error, and the temporary folder goes away;
- an applied batch, a contradictory route and a missing or unreadable workbook are reported, nothing written;
- what the dry run shows is what the real apply writes (same instant), the instant-only cells marked apart;
- the preview token: --expect refuses an apply when the batch, project.yaml or a workbook changed since.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import glob
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
IOS = "plan-ios.xlsx"
NOW = datetime(2026, 11, 7, 10, 30, 0)


class DryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        shutil.copy(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, IOS))
        excel = copy.deepcopy(CFG)
        excel["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
        self.proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel",
                     "excel": excel, "report": {"platforms": {"android": ["batch_2026*"], "ios": ["batch_ios*"]}}}
        self.write_project()
        self.environ = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                    if k not in ("QA_WORKBOOK", "QA_LOCK_WAIT")}, clear=True)
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.tmp.cleanup()

    def write_project(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f)

    def digests(self):
        out = {}
        for folder, _, files in os.walk(self.ws):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as f:
                    out[os.path.relpath(path, self.ws)] = hashlib.sha256(f.read()).hexdigest()
        return out

    def batch(self, name, items=None, platform=None):
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        path = os.path.join(self.ws, "runs", name + ".json")
        run = {"build": "1.0 (1)", "environment": "test"} | ({"platform": platform} if platform else {})
        items = items or [{"tc": "TC-SHOP-001", "status": "Fail", "actual_result": "fictional", "ts": "2026-11-07T10:00:00",
                           "new_defect": {"title": "Fictional bug", "description": "d", "area": "Shop",
                                          "severity": "P2", "priority": "P2"}},
                          {"tc": "TC-ACCOUNT-001", "status": "Pass", "actual_result": "ok", "ts": "2026-11-07T10:01:00"}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": run, "results": items}, f)
        return path

    def dry(self, path, **kw):
        return ap.dry_run(self.ws, path, recalc=False, now=NOW, **kw)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ap.main([self.ws, *args])
        return rc, out.getvalue() + err.getvalue()

    # ------------------------------------------------------------------ routing

    def test_an_android_batch_goes_to_the_default_workbook_and_an_ios_batch_to_the_ios_one(self):
        android, ios = self.batch("batch_20261107_01"), self.batch("batch_ios_01")
        before = self.digests()
        res = self.dry(android)
        self.assertEqual((res["workbook"], res["platform"], res["problems"], res["code"]),
                         (CFG["file"], "android", [], 0))
        res = self.dry(ios)
        self.assertEqual((res["workbook"], res["platform"], res["source"]), (IOS, "ios", "excel.workbooks.ios"))
        self.assertTrue(any(c["case"] == "TC-SHOP-001" and c["after"] == "Fail" for c in res["cells"]), res["cells"])
        self.assertEqual(self.digests(), before)

    def test_a_contradictory_route_is_a_problem_and_nothing_is_written(self):
        path = self.batch("batch_ios_02", platform="android")
        before = self.digests()
        res = self.dry(path)
        self.assertEqual(res["code"], 4)
        self.assertIn("do not agree", " ".join(res["problems"]))
        self.assertEqual(res["cells"], [])
        self.assertEqual(self.digests(), before)
        rc, out = self.cli(path, "--dry-run", "--no-recalc")
        self.assertEqual(rc, 4)
        self.assertIn("Problems that stop the apply", out)

    def test_a_missing_or_unreadable_workbook_is_reported(self):
        path = self.batch("batch_ios_03")
        os.remove(os.path.join(self.ws, IOS))
        res = self.dry(path)
        self.assertTrue(any("does not exist" in p for p in res["problems"]), res["problems"])
        with open(os.path.join(self.ws, IOS), "wb") as f:
            f.write(b"fictional: not a workbook")
        before = self.digests()
        res = self.dry(path)
        self.assertEqual(res["code"], 4)
        self.assertTrue(any("could not be read" in p for p in res["problems"]), res["problems"])
        self.assertEqual(self.digests(), before)

    # ------------------------------------------------------------------ applied, invalid

    def test_a_batch_already_applied_says_so_and_changes_nothing(self):
        path = self.batch("batch_20261107_02")
        rc, _ = self.cli(path, "--no-recalc")
        self.assertEqual(rc, 0)
        before = self.digests()
        res = self.dry(path)
        self.assertIn("already applied", res["already_applied"])
        self.assertEqual((res["cells"], res["code"]), ([], 0))
        self.assertEqual(self.digests(), before)

    def test_an_invalid_batch_lists_the_problems(self):
        path = self.batch("batch_20261107_03", items=[{"tc": "TC-SHOP-001", "status": "Question", "actual_result": "?",
                                                       "ts": "2026-11-07T10:00:00"}])
        res = self.dry(path)
        self.assertEqual(res["code"], 4)
        self.assertIn("resolve the Question", " ".join(res["problems"]))

    # ------------------------------------------------------------------ preservation and cleanup

    def test_the_temporary_copy_goes_away_on_success_and_on_error(self):
        made = []
        real = tempfile.mkdtemp

        def spy(*a, **k):
            made.append(real(*a, **k))
            return made[-1]
        path = self.batch("batch_20261107_04")
        before = self.digests()
        with mock.patch.object(ap.tempfile, "mkdtemp", side_effect=spy):
            self.dry(path)
            with mock.patch.object(ap, "_apply", side_effect=RuntimeError("fictional failure")), \
                    self.assertRaises(RuntimeError):
                self.dry(path)
        self.assertEqual(len(made), 2)
        self.assertFalse(any(os.path.exists(m) for m in made))
        self.assertEqual(self.digests(), before)
        self.assertEqual(glob.glob(os.path.join(self.ws, "*.REVIEWED.*")), [])

    # ------------------------------------------------------------------ the dry run is the apply

    def cells(self, file):
        wb = openpyxl.load_workbook(os.path.join(self.ws, file), data_only=False)
        try:
            return {(ws.title, c.coordinate): c.value for ws in wb for row in ws.iter_rows() for c in row
                    if c.value is not None}
        finally:
            wb.close()

    def test_what_the_dry_run_shows_is_what_the_apply_writes(self):
        path = self.batch("batch_20261107_05")
        res = self.dry(path)
        before = self.cells(CFG["file"])
        rc, _ = self.cli(path, "--no-recalc", "--now", NOW.isoformat())
        self.assertEqual(rc, 0)
        after = self.cells(CFG["file"])
        written = {k: v for k, v in after.items() if before.get(k) != v}
        written |= {k: None for k in before if k not in after}
        shown = {(c["sheet"], c["cell"]): c["after"] for c in res["cells"]}
        self.assertEqual(shown, written)
        timed = {(c["sheet"], c["cell"]) for c in res["cells"] if c["at_apply_time"]}
        self.assertTrue(timed and all(k[0] in ("_qa_batches", "Open Defects", "Defects", "Shop", "Account")
                                      for k in timed), timed)
        self.assertEqual(res["new_defects"], ["BUG-4"])
        self.assertNotEqual(res["counts_before"], res["counts_after"])

    def test_the_instant_of_the_application_is_marked_apart(self):
        res = self.dry(self.batch("batch_20261107_06"))
        control = [c for c in res["cells"] if c["sheet"] == "_qa_batches" and c["cell"].startswith("B")
                   and c["cell"] != "B1"]
        self.assertTrue(control and control[0]["at_apply_time"], control)
        status = [c for c in res["cells"] if c["case"] == "TC-ACCOUNT-001" and c["after"] == "Pass"]
        self.assertTrue(status and not status[0]["at_apply_time"])
        _, out = self.cli(self.batch("batch_20261107_06"), "--dry-run", "--no-recalc")
        self.assertIn("<at apply time>", out)
        self.assertIn("Preview token:", out)

    def test_a_date_inside_the_observed_text_is_not_taken_for_the_instant(self):
        """Seen on 07.11.2026: a note that quoted the day's date was marked <at apply time>."""
        note = f"observed on {NOW.strftime('%d.%m.%Y')} and {NOW.date().isoformat()}: fictional"
        path = self.batch("batch_20261107_12", items=[{"tc": "TC-ACCOUNT-001", "status": "Pass",
                                                       "actual_result": note, "ts": "2026-11-07T10:00:00"}])
        res = self.dry(path)
        cell = next(c for c in res["cells"] if c["case"] == "TC-ACCOUNT-001" and c["after"] == note)
        self.assertFalse(cell["at_apply_time"])
        self.assertTrue(any(c["at_apply_time"] for c in res["cells"] if c["sheet"] == "_qa_batches"))

    def test_the_json_output_is_complete(self):
        rc, out = self.cli(self.batch("batch_ios_07"), "--dry-run", "--json", "--no-recalc")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        for key in ("batch", "workbook", "platform", "cells", "counts_before", "counts_after", "new_defects", "token"):
            self.assertIn(key, doc)

    # ------------------------------------------------------------------ the preview token

    def test_a_stale_preview_is_refused_and_a_fresh_one_applies(self):
        path = self.batch("batch_20261107_08")
        token = self.dry(path)["token"]
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["results"][1]["status"] = "Blocked"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        before = self.digests()
        rc, out = self.cli(path, "--no-recalc", "--expect", token)
        self.assertEqual(rc, 4)
        self.assertIn("out of date", out)
        self.assertEqual(self.digests(), before)
        token = self.dry(path)["token"]
        rc, _ = self.cli(path, "--no-recalc", "--expect", token)
        self.assertEqual(rc, 0)

    def test_a_change_to_the_workbook_or_the_configuration_makes_the_preview_stale(self):
        path = self.batch("batch_ios_09")
        token = self.dry(path)["token"]
        self.cli(self.batch("batch_20261107_10"), "--no-recalc")        # another batch changes the other workbook
        self.assertNotEqual(ap.preview_token(self.ws, path), token)
        token = ap.preview_token(self.ws, path)
        self.proj["environments"]["test"] = "https://shop.demo/other"
        self.write_project()
        self.assertNotEqual(ap.preview_token(self.ws, path), token)

    def test_the_options_go_together(self):
        path = self.batch("batch_20261107_11")
        with self.assertRaises(SystemExit):
            self.cli(path, "--json")
        with self.assertRaises(SystemExit):
            self.cli(path, "--dry-run", "--expect", "abc")


if __name__ == "__main__":
    unittest.main()
