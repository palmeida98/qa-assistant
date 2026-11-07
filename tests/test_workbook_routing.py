"""Which workbook a batch belongs to, and an apply that cannot corrupt it (audit of 2026-11-06).

- `excel.workbooks` sends batches by name to another workbook: the apply, run_log.applied() and the reports use the
  same rule (adapters.excel.base.route_workbook); $QA_WORKBOOK that contradicts it is refused.
- The rule stops, with nothing written, when the platform is not recognised, when the name matches two workbooks or
  two platforms, when the name and run.platform disagree, when the workbook is missing or unreadable, and when the
  batch is already applied to another workbook (closing of the audit, 07.11.2026). The other workbook stays
  byte for byte the same.
- An apply holds the workspace's lock (a second one waits, then is refused) and saves through a verified temporary
  file (a failure in the middle leaves the workbook as it was).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import run_log as rl
from build_fixture import build
from common import workspace_lock

from adapters.excel import ExcelAdapter
from adapters.excel import apply as ap
from adapters.excel.base import (
    configured_workbook,
    route_workbook,
    workbook_conflict,
    workbook_for,
)

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
IOS = "plan-ios.xlsx"


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        shutil.copy(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, IOS))
        excel = copy.deepcopy(CFG)
        excel["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
        self.excel = excel
        # two workbooks: each batch says its platform by its name (report.platforms) or by run.platform
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

    def digest(self, file):
        with open(os.path.join(self.ws, file), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def digests(self):
        return {f: self.digest(f) for f in (CFG["file"], IOS) if os.path.exists(os.path.join(self.ws, f))}

    def batch(self, name, case="TC-SHOP-001", status="Fail", platform=None):
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        path = os.path.join(self.ws, "runs", name + ".json")
        run = {"build": "1.0 (1)", "environment": "test"} | ({"platform": platform} if platform else {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": run,
                       "results": [{"tc": case, "status": status, "actual_result": "x", "ts": "2026-11-06T10:00:00"}]}, f)
        return path

    def apply(self, path):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ap.main([self.ws, path, "--no-recalc"])
        return rc, out.getvalue() + err.getvalue()

    def status(self, file, case="TC-SHOP-001"):
        ad = ExcelAdapter(copy.deepcopy(CFG) | {"file": file}, base_dir=self.ws)
        ad.load()
        idcol = CFG["cases"]["columns"]["id"]
        rows = [r for rows in ad.case_rows().values() for r in rows if r.get(idcol) == case]
        return rows[0][CFG["cases"]["columns"]["status"]]

    def test_the_batch_name_picks_the_workbook(self):
        self.assertEqual(configured_workbook(self.excel, "batch_ios_01"), (IOS, "excel.workbooks.ios"))
        self.assertEqual(configured_workbook(self.excel, "batch_20261106_01")[0], CFG["file"])
        self.assertEqual(workbook_for(self.excel, "batch_ios_01", {"QA_WORKBOOK": "x.xlsx"}), ("x.xlsx", "$QA_WORKBOOK"))

    def test_an_ios_batch_is_applied_to_the_ios_workbook_without_any_variable(self):
        android_before = self.status(CFG["file"])
        rc, out = self.apply(self.batch("batch_ios_01"))
        self.assertEqual(rc, 0, out)
        self.assertIn(IOS, out)
        self.assertEqual(self.status(IOS), "Fail")
        self.assertEqual(self.status(CFG["file"]), android_before)          # the Android workbook is untouched
        # and run_log sees it as applied, so the batch is immutable
        with self.assertRaises(rl.Refused):
            rl.add(self.ws, "ios_01", "TC-SHOP-002", "Pass", "late")

    def test_a_variable_that_contradicts_the_project_is_refused(self):
        self.assertIsNotNone(workbook_conflict(self.excel, "batch_ios_01", self.ws, {"QA_WORKBOOK": CFG["file"]}))
        self.assertIsNone(workbook_conflict(self.excel, "batch_ios_01", self.ws, {"QA_WORKBOOK": IOS}))
        with mock.patch.dict(os.environ, {"QA_WORKBOOK": CFG["file"]}):
            rc, out = self.apply(self.batch("batch_ios_02"))
        self.assertEqual(rc, 4)
        self.assertIn("belongs to", out)

    def test_a_second_apply_waits_for_the_lock_and_is_refused_when_it_is_not_released(self):
        path = self.batch("batch_20261106_01")
        held, release = threading.Event(), threading.Event()

        def holder():
            with workspace_lock(self.ws):
                held.set()
                release.wait(5)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}):
                rc, out = self.apply(path)
        finally:
            release.set()
            t.join()
        self.assertEqual(rc, 4)
        self.assertIn("another process is writing", out)
        self.assertEqual(self.apply(path)[0], 0)

    def test_a_save_that_fails_in_the_middle_leaves_the_workbook_as_it_was(self):
        target = os.path.join(self.ws, CFG["file"])
        with open(target, "rb") as f:
            before = f.read()
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()

        def broken(path):
            with open(path, "wb") as f:
                f.write(b"half a zip")
            raise OSError("disk full")
        with mock.patch.object(type(ad._need_wb()), "save", lambda self_, p: broken(p)), self.assertRaises(OSError):
            ad.save()
        with open(target, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(self.ws) if n.startswith(".~qa-")], [])

    def test_a_new_defect_takes_the_build_of_the_run_and_an_unmapped_key_is_refused(self):
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        fields = ad.cfg["defects"]["batch_fields"]
        item = {"tc": "TC-SHOP-001", "status": "Fail", "actual_result": "x",
                "new_defect": {"title": "t", "severity": ad.cfg["defects"]["priorities"][0] if ad.cfg["defects"]["priorities"] else None,
                               "unmapped_key": "kept nowhere"}}
        item["new_defect"] = {k: v for k, v in item["new_defect"].items() if v is not None}
        self.assertTrue(any("unmapped_key" in p for p in ad.validate_batch([item])))
        fields.pop("area", None)
        fields["build"] = "Component"            # the fixture has no Build column: an existing one stands in
        if True:
            del item["new_defect"]["unmapped_key"]
            ad.apply_batch([item], batch_id="batch_20261106_09", run={"build": "9.9 (99)"})
            ws, h, rows = ad._defect_rows()
            self.assertEqual(ws.cell(rows[-1][0], h[fields["build"]]).value, "9.9 (99)")



class RoutingStops(Routing):
    """Every case the closing of the audit asked for, on copies: the apply stops with a useful message and leaves
    both workbooks byte for byte as they were (no temporary file left)."""

    def refused(self, path, *expected):
        before = self.digests()
        rc, out = self.apply(path)
        self.assertEqual(rc, 4, out)
        self.assertIn("NOTHING APPLIED", out)
        self.assertIn("Nothing was written", out)
        for text in expected:
            self.assertIn(text, out)
        self.assertEqual(self.digests(), before)
        self.assertEqual([n for n in os.listdir(self.ws) if n.startswith(".~qa-")], [])
        return out

    def test_android_and_ios_batches_go_to_their_own_workbook_and_leave_the_other_unchanged(self):
        ios_before = self.digest(IOS)
        rc, out = self.apply(self.batch("batch_20261106_01"))
        self.assertEqual(rc, 0, out)
        self.assertIn("Platform: android; workbook from excel.file", out)
        self.assertEqual(self.status(CFG["file"]), "Fail")
        self.assertEqual(self.digest(IOS), ios_before)
        main_before = self.digest(CFG["file"])
        rc, out = self.apply(self.batch("batch_ios_01", status="Pass"))
        self.assertEqual(rc, 0, out)
        self.assertIn("Platform: ios; workbook from excel.workbooks.ios", out)
        self.assertEqual(self.status(IOS), "Pass")
        self.assertEqual(self.digest(CFG["file"]), main_before)

    def test_a_declared_platform_that_agrees_with_the_name_is_accepted(self):
        route = route_workbook(self.proj, "batch_ios_01", {"platform": "iOS"}, environ={})
        self.assertEqual((route.file, route.platform, route.problem), (IOS, "ios", None))
        self.assertEqual(self.apply(self.batch("batch_20261106_02", platform="android"))[0], 0)

    def test_a_name_without_a_recognisable_platform_is_refused_not_sent_to_the_main_workbook(self):
        out = self.refused(self.batch("batch_smoke_01"), "no platform recognised",
                           "the default workbook is not chosen by elimination")
        self.assertNotIn("Workbook:", out)

    def test_a_name_without_platform_but_a_declared_one_without_own_workbook_goes_to_the_main_workbook(self):
        rc, out = self.apply(self.batch("batch_smoke_02", platform="android"))
        self.assertEqual(rc, 0, out)
        self.assertIn("Platform: android; workbook from excel.file", out)

    def test_a_name_that_matches_two_workbooks_is_refused(self):
        self.proj["excel"]["workbooks"]["tablet"] = {"file": "plan-tablet.xlsx", "batches": ["batch_ios_tab*"]}
        self.write_project()
        self.refused(self.batch("batch_ios_tab_01"), "matches the patterns of 2 workbooks",
                     "excel.workbooks.ios", "excel.workbooks.tablet")

    def test_a_name_that_matches_two_platforms_is_refused(self):
        self.proj["report"]["platforms"]["web"] = ["batch_2026*"]
        self.write_project()
        self.refused(self.batch("batch_20261106_03"), "report.platforms patterns of android and web")

    def test_a_name_that_contradicts_the_declared_platform_is_refused_both_ways(self):
        self.refused(self.batch("batch_20261106_04", platform="ios"), "the platforms do not agree",
                     "run.platform: ios", "report.platforms: android")
        self.refused(self.batch("batch_ios_05", platform="android"), "the platforms do not agree",
                     "run.platform: android", "excel.workbooks.ios: ios")

    def test_a_platform_with_its_own_workbook_and_a_name_outside_its_patterns_is_refused(self):
        self.refused(self.batch("batch_smoke_06", platform="ios"), "whose workbook is plan-ios.xlsx",
                     "matches none of batch_ios*")

    def test_a_missing_target_workbook_is_refused(self):
        os.remove(os.path.join(self.ws, IOS))
        self.refused(self.batch("batch_ios_07"), "plan-ios.xlsx (excel.workbooks.ios) does not exist")
        self.assertFalse(os.path.exists(os.path.join(self.ws, IOS)))

    def test_an_unreadable_target_workbook_is_refused_and_kept_as_it_is(self):
        with open(os.path.join(self.ws, IOS), "wb") as f:
            f.write(b"not a workbook")
        self.refused(self.batch("batch_ios_08"), "plan-ios.xlsx (excel.workbooks.ios) could not be read")
        with open(os.path.join(self.ws, IOS), "rb") as f:
            self.assertEqual(f.read(), b"not a workbook")

    def test_a_batch_applied_again_changes_nothing(self):
        path = self.batch("batch_ios_09")
        self.assertEqual(self.apply(path)[0], 0)
        before = self.digests()
        rc, out = self.apply(path)
        self.assertEqual(rc, 0, out)
        self.assertIn("Nothing applied", out)
        self.assertEqual(self.digests(), before)

    def test_a_batch_applied_to_another_workbook_is_refused_and_stays_immutable(self):
        path = self.batch("batch_20261106_10")
        self.assertEqual(self.apply(path)[0], 0)
        # the patterns change: the same name would now go to the iOS workbook
        self.proj["excel"]["workbooks"]["ios"]["batches"] = ["batch_ios*", "batch_20261106_10"]
        self.proj["report"]["platforms"] = {"ios": ["batch_ios*", "batch_20261106_10"], "android": ["batch_20261106_0*"]}
        self.write_project()
        self.refused(path, "already applied to " + CFG["file"])
        self.assertIn("applied to the workbook " + CFG["file"], rl.applied(self.ws, self.proj, "batch_20261106_10"))

    def test_an_ambiguous_route_is_not_applied_in_the_report_nor_in_the_ui_check(self):
        import qa_report
        self.batch("batch_smoke_11")
        where = qa_report._applied_view(self.ws, self.proj)("batch_smoke_11", {"build": "1.0 (1)"})
        self.assertTrue(where.startswith(qa_report.UNKNOWN), where)
        self.assertIn("no platform recognised", where)
        import workspace_ci
        doc = {"run": {"build": "1.0 (1)"}, "results": [{"tc": "TC-SHOP-001", "status": "Fail", "actual_result": "x"}]}
        self.assertIn("no platform recognised", workspace_ci.adapter_problems(self.ws, self.proj, doc,
                                                                               "batch_smoke_11")[0])


if __name__ == "__main__":
    unittest.main()
