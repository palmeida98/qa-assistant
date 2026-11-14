"""Preview and write of a workbook: the destination and the approval (technique-excel.md section 1.2; incident of
13.11.2026: an add «simulated on a copy» named the copy only in $QA_WORKBOOK, was run with --platform, which ignored the
variable, and without --expect, so it wrote into the real workbook of the platform).

Everything is fictional and lives in temporary folders: two workbooks with identical content, one the project's default
and one passed explicitly, and a project with a workbook per platform. Each command is called through its real entry
point (`main`), as the skills call it.

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
import unittest
from unittest import mock

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import add_cases as ac
from adapters.excel import apply as ap
from adapters.excel import reconcile as rec
from adapters.excel.base import ExcelAdapter

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
DEFAULT, EXPLICIT, IOS = CFG["file"], "explicit-copy.xlsx", "plan-ios.xlsx"
NOW = "2026-11-13T05:53:00"
NEW_CASES = {"cases": [{"id": "TC-SHOP-050", "title": "A brand new fictional case"},
                       {"id": "TC-SHOP-051", "title": "Another brand new fictional case"}]}


class Base(unittest.TestCase):
    multi = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = os.path.realpath(self.tmp.name)
        build(os.path.join(self.ws, DEFAULT))
        shutil.copy2(os.path.join(self.ws, DEFAULT), os.path.join(self.ws, EXPLICIT))      # identical content
        excel = copy.deepcopy(CFG)
        proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel", "excel": excel}
        if self.multi:
            shutil.copy2(os.path.join(self.ws, DEFAULT), os.path.join(self.ws, IOS))
            excel["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
            proj["report"] = {"platforms": {"android": ["batch_2026*"], "ios": ["batch_ios*"]}}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        os.makedirs(os.path.join(self.ws, "runs"))
        os.makedirs(os.path.join(self.ws, "cases"))
        with open(os.path.join(self.ws, "cases", "cases_20261113_01.json"), "w", encoding="utf-8") as f:
            json.dump(NEW_CASES, f)
        env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in ("QA_WORKBOOK", "QA_LOCK_WAIT")},
                              clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.outside = tempfile.TemporaryDirectory()                  # a copy outside the workspace (the incident)
        self.addCleanup(self.outside.cleanup)

    # -- helpers -----------------------------------------------------------------------------------------------------

    def files(self):
        return [f for f in (DEFAULT, EXPLICIT, IOS) if os.path.exists(os.path.join(self.ws, f))]

    def state(self):
        """SHA-256 and modification time of every workbook: a refusal or a preview leaves both as they were."""
        out = {}
        for f in self.files():
            p = os.path.join(self.ws, f)
            with open(p, "rb") as fh:
                out[f] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
        return out

    def run_(self, mod, *args, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = mod.main([self.ws, *args])
        return code, out.getvalue()

    def batch(self, name, case="TC-SHOP-001", status="Fail", platform=None):
        path = os.path.join(self.ws, "runs", name + ".json")
        run = {"build": "1.0 (1)", "environment": "test"} | ({"platform": platform} if platform else {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": run, "results": [{"tc": case, "status": status, "actual_result": "fictional observation",
                                                "ts": "2026-11-13T01:31:00"}]}, f)
        return f"runs/{name}.json"

    @staticmethod
    def token(out):
        import re
        found = re.findall(r"\b(v[12]-[0-9a-f]{16})\b", out)
        return found[-1] if found else None

    def cells(self, file):
        wb = openpyxl.load_workbook(os.path.join(self.ws, file), data_only=False)
        try:
            return {(ws.title, c.coordinate): c.value for ws in wb for row in ws.iter_rows() for c in row}
        finally:
            wb.close()

    def control_rows(self, file):
        ad = ExcelAdapter.from_project(self.ws, file=file)
        ad.load()
        return ad.applied_batches()

    def shop_ids(self, file):
        ws = openpyxl.load_workbook(os.path.join(self.ws, file))["Shop"]
        return [ws.cell(r, 1).value for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value]


class ExplicitDestination(Base):
    def test_the_dry_run_uses_the_explicit_workbook_shows_its_path_and_writes_nothing(self):
        before = self.state()
        self.assertEqual(before[DEFAULT][0], before[EXPLICIT][0])               # the same content in both
        batch = self.batch("batch_20261113_01")
        code, out = self.run_(ap, batch, "--dry-run", "--no-recalc", "--now", NOW, "--workbook", EXPLICIT)
        self.assertEqual(code, 0, out)
        self.assertIn(f"Target: {os.path.join(self.ws, EXPLICIT)}", out)       # the absolute path resolved
        self.assertIn("token v2-", out)
        res = ap.dry_run(self.ws, batch, recalc=False, workbook=EXPLICIT)
        self.assertEqual((res["workbook"], res["source"], res["workbook_path"]),
                         (EXPLICIT, "--workbook", os.path.join(self.ws, EXPLICIT)))
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--dry-run", "--workbook", EXPLICIT)
        self.assertEqual(code, 0, out)
        self.assertIn(f"Target: {os.path.join(self.ws, EXPLICIT)}", out)
        code, out = self.run_(ap, batch, "--dry-run", "--no-recalc", env={"QA_WORKBOOK": EXPLICIT})
        self.assertIn(f"Target: {os.path.join(self.ws, EXPLICIT)}", out)       # the variable is the same explicit path
        self.assertEqual(self.state(), before)                                   # SHA-256 and mtime of both

    def test_an_invalid_explicit_path_is_refused_and_never_falls_back(self):
        batch = self.batch("batch_20261113_02")
        copy_outside = os.path.join(self.outside.name, "plan.xlsx")
        shutil.copy2(os.path.join(self.ws, DEFAULT), copy_outside)
        with open(os.path.join(self.ws, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("not a workbook")
        before = self.state()
        cases = "cases/cases_20261113_01.json"
        for args, env, reason in (
                (("--workbook", copy_outside), None, "outside the workspace"),
                (("--workbook", "missing.xlsx"), None, "does not exist"),
                (("--workbook", "notes.txt"), None, "is not a workbook"),
                ((), {"QA_WORKBOOK": copy_outside}, "outside the workspace"),
                (("--workbook", EXPLICIT), {"QA_WORKBOOK": DEFAULT}, "name different workbooks")):
            for mod, target in ((ap, batch), (ac, cases)):
                for mode in (("--dry-run",), ("--expect", "v2-0000000000000000" if mod is ap else "v1-0000000000000000")):
                    with self.subTest(mod=mod.__name__, args=args, env=env, mode=mode):
                        code, out = self.run_(mod, target, *mode, "--no-recalc", *args, env=env)
                        self.assertEqual(code, 4, out)
                        self.assertIn(reason, out)
        code, out = self.run_(rec, "batch_20261113_02", "--from", batch, "--dry-run", "--workbook", copy_outside)
        self.assertEqual(code, 4)
        self.assertIn("outside the workspace", out)
        self.assertEqual(self.state(), before)

    def test_a_token_of_one_workbook_never_writes_another_with_the_same_content(self):
        batch = self.batch("batch_20261113_03")
        before = self.state()
        code, out = self.run_(ap, batch, "--dry-run", "--no-recalc", "--now", NOW, "--workbook", EXPLICIT)
        token_explicit = self.token(out)
        code, out = self.run_(ap, batch, "--dry-run", "--no-recalc", "--now", NOW)
        self.assertNotEqual(self.token(out), token_explicit)                     # same content, another destination
        code, out = self.run_(ap, batch, "--no-recalc", "--now", NOW, "--expect", token_explicit)
        self.assertEqual(code, 4)
        self.assertIn("another destination", out)
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--dry-run", "--workbook", EXPLICIT)
        add_token = self.token(out)
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--expect", add_token)
        self.assertEqual(code, 4)
        self.assertIn("out of date", out)
        self.assertEqual(self.state(), before)

    def test_a_stale_token_or_no_token_writes_nothing(self):
        batch = self.batch("batch_20261113_04")
        code, out = self.run_(ap, batch, "--dry-run", "--no-recalc", "--now", NOW, "--workbook", EXPLICIT)
        token = self.token(out)
        ad = ExcelAdapter.from_project(self.ws, file=EXPLICIT)                   # the destination changes after the preview
        ad.load()
        ad.wb["Shop"]["H2"] = "edited by hand after the preview"
        ad.wb.save(ad.path)
        before = self.state()
        code, out = self.run_(ap, batch, "--no-recalc", "--now", NOW, "--workbook", EXPLICIT, "--expect", token)
        self.assertEqual(code, 4)
        self.assertIn("out of date", out)
        code, out = self.run_(ap, batch, "--no-recalc", "--now", NOW, "--workbook", EXPLICIT)
        self.assertEqual(code, 4)
        self.assertIn("needs --expect", out)
        self.assertIn(os.path.join(self.ws, EXPLICIT), out)
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--workbook", EXPLICIT)
        self.assertEqual(code, 4)
        self.assertIn("needs --expect", out)
        self.assertEqual(self.state(), before)

    def test_the_approved_preview_writes_only_its_workbook_what_it_showed_and_only_once(self):
        batch = self.batch("batch_20261113_05")
        res = ap.dry_run(self.ws, batch, recalc=False, now=__import__("datetime").datetime.fromisoformat(NOW),
                         workbook=EXPLICIT)
        before_cells = self.cells(EXPLICIT)
        default_before = self.state()[DEFAULT]
        code, out = self.run_(ap, batch, "--no-recalc", "--now", NOW, "--workbook", EXPLICIT, "--expect", res["token"])
        self.assertEqual(code, 0, out)
        after = self.cells(EXPLICIT)
        written = {k: v for k, v in after.items() if before_cells.get(k) != v}
        self.assertEqual({(c["sheet"], c["cell"]): c["after"] for c in res["cells"]}, written)   # the diff shown
        self.assertEqual(self.state()[DEFAULT], default_before)                                  # the other: intact
        rows = len(self.control_rows(EXPLICIT))
        written_state = self.state()[EXPLICIT]
        # again: the new preview says already applied, its token changes nothing, no second control row
        res2 = ap.dry_run(self.ws, batch, recalc=False, workbook=EXPLICIT)
        self.assertIn("already applied", res2.get("already_applied", ""))
        code, out = self.run_(ap, batch, "--no-recalc", "--workbook", EXPLICIT, "--expect", res2["token"])
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.control_rows(EXPLICIT)), rows)
        self.assertEqual(self.state()[EXPLICIT][0], written_state[0])
        # the cases, the same way
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--dry-run", "--workbook", EXPLICIT, "--no-recalc")
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--workbook", EXPLICIT, "--no-recalc",
                              "--expect", self.token(out))
        self.assertEqual(code, 0, out)
        ids = self.shop_ids(EXPLICIT)
        self.assertEqual((ids.count("TC-SHOP-050"), ids.count("TC-SHOP-051")), (1, 1))
        self.assertNotIn("TC-SHOP-050", self.shop_ids(DEFAULT))
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--dry-run", "--workbook", EXPLICIT, "--no-recalc")
        self.assertEqual(code, 4)                                                  # already there: no token for a repeat
        self.assertEqual(self.shop_ids(EXPLICIT), ids)
        self.assertEqual(self.state()[DEFAULT], default_before)

    def test_the_python_api_keeps_working_for_programmatic_callers(self):
        """The golden check and the demo rebuild call `apply()` and `add()` directly: the token is required only by the
        command line (the request of a person); the destination rules hold for both."""
        batch = self.batch("batch_20261113_06")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ap.apply(self.ws, batch, recalc=False, workbook=EXPLICIT), 0)
        self.assertEqual(self.control_rows(EXPLICIT)[-1]["batch_id"], "batch_20261113_06")
        self.assertEqual(self.control_rows(DEFAULT), [])


class PlatformDestination(Base):
    multi = True

    def test_the_incident_a_copy_named_in_the_variable_with_platform_and_no_token_writes_nothing(self):
        copy_outside = os.path.join(self.outside.name, IOS)
        shutil.copy2(os.path.join(self.ws, IOS), copy_outside)
        before = self.state()
        code, out = self.run_(ac, "cases/cases_20261113_01.json", "--platform", "ios",
                              env={"QA_WORKBOOK": copy_outside})            # the command of 13.11.2026 05:53 UTC
        self.assertEqual(code, 4, out)
        self.assertIn("outside the workspace", out)
        self.assertEqual(self.state(), before)
        with open(copy_outside, "rb") as f, open(os.path.join(self.ws, IOS), "rb") as g:
            self.assertEqual(f.read(), g.read())                              # the copy too

    def test_an_explicit_path_that_contradicts_the_platform_or_the_route_is_refused(self):
        before = self.state()
        cases = "cases/cases_20261113_01.json"
        for args, env in ((("--platform", "ios", "--workbook", DEFAULT), None),
                          (("--platform", "ios"), {"QA_WORKBOOK": EXPLICIT}),
                          (("--platform", "both", "--workbook", IOS), None)):
            with self.subTest(args=args, env=env):
                code, out = self.run_(ac, cases, "--dry-run", *args, env=env)
                self.assertEqual(code, 4, out)
                self.assertIn("never ignored" if "both" not in args else "takes no explicit path", out)
        ios_batch = self.batch("batch_ios_20261113_01", platform="ios")
        code, out = self.run_(ap, ios_batch, "--dry-run", "--no-recalc", "--workbook", DEFAULT)
        self.assertEqual(code, 4)
        self.assertIn("belongs to", out)
        code, out = self.run_(ap, self.batch("batch_20261113_07", platform="ios"), "--dry-run", "--no-recalc")
        self.assertEqual(code, 4)                                              # the platforms disagree: nothing chosen
        self.assertEqual(self.state(), before)
        # the same workbook named both ways is accepted, and the preview names it in full
        code, out = self.run_(ac, cases, "--dry-run", "--platform", "ios", "--workbook", IOS)
        self.assertEqual(code, 0, out)
        self.assertIn(f"Target: {os.path.join(self.ws, IOS)}", out)
        self.assertEqual(self.state(), before)

    def test_reconcile_follows_the_route_of_the_batch_not_the_default_workbook(self):
        ios_batch = self.batch("batch_ios_20261113_02", platform="ios")
        code, out = self.run_(ap, ios_batch, "--dry-run", "--no-recalc")
        code, out = self.run_(ap, ios_batch, "--no-recalc", "--expect", self.token(out))
        self.assertEqual(code, 0, out)
        before = self.state()
        code, out = self.run_(rec, "batch_ios_20261113_02", "--from", ios_batch, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(f"Workbook: {os.path.join(self.ws, IOS)}", out)          # not excel.file
        code, out = self.run_(rec, "batch_ios_20261113_02", "--from", ios_batch, "--workbook", DEFAULT, "--dry-run")
        self.assertEqual(code, 4)
        self.assertIn("belongs to", out)
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
