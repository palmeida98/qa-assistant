"""scripts/run_log.py (plan 11.2; methodology 7.1): one item per case (appended or replaced), the project's
vocabulary (or Question), the run block, and a batch already applied never touched — Excel through the workbook's
control sheet (the fictional golden fixture, no LibreOffice), Jira / Azure DevOps through tracker/sent/.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import run_log as rl
from build_fixture import build

from adapters.excel import ExcelAdapter

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 9, 29, 14, 3)


class RunLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        self.project(tracker="excel", excel=copy.deepcopy(CFG))

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, **proj):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"project": "demo-shop", "environments": {"test": "https://shop.demo"}, **proj}, f)

    def log(self, name="batch_20260929_01"):
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rl.main([self.ws, *args])
        return code, out.getvalue()

    def test_add_then_replace_the_same_case(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "cart shows 2 items", now=NOW)
        path, replaced = rl.add(self.ws, "batch_20260929_01", "TC-SHOP-002", "Fail", "coupon accepted",
                                bug="BUG-1", evidence=["evidence/b/TC-SHOP-002_t1.png"], environment="test",
                                build="1.4.0 (140)", now=NOW)
        self.assertFalse(replaced)
        _, replaced = rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Question", "is 2 the limit?", now=NOW)
        self.assertTrue(replaced)
        doc = self.log()
        self.assertEqual(doc["run"], {"environment": "test", "build": "1.4.0 (140)"})
        self.assertEqual(doc["results"], [
            {"tc": "TC-SHOP-001", "status": "Question", "actual_result": "is 2 the limit?",
             "ts": "2026-09-29T14:03:00"},
            {"tc": "TC-SHOP-002", "status": "Fail", "actual_result": "coupon accepted", "ts": "2026-09-29T14:03:00",
             "bug": "BUG-1", "evidence": "evidence/b/TC-SHOP-002_t1.png"}])
        self.assertEqual(path, os.path.join(self.ws, "runs", "batch_20260929_01.json"))

    def test_the_command_line_with_a_new_defect(self):
        draft = os.path.join(self.ws, "draft.json")
        with open(draft, "w", encoding="utf-8") as f:
            json.dump({"title": "Expired coupon accepted", "severity": "High", "proposed": True}, f)
        code, out = self.run_("add", "20260929_01", "TC-SHOP-003", "Fail", "accepted", "--new-defect", draft)
        self.assertEqual(code, 0, out)
        self.assertIn("added: TC-SHOP-003 Fail", out)
        self.assertEqual(self.log()["results"][0]["new_defect"]["proposed"], True)
        with open(draft, "w", encoding="utf-8") as f:
            f.write("[1, 2")
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-003", "Fail", "x", "--new-defect", draft)[0], 2)

    def test_status_outside_the_vocabulary_is_2(self):
        code, out = self.run_("add", "20260929_01", "TC-SHOP-001", "Passed", "x")
        self.assertEqual(code, 2)
        self.assertIn("vocabulary", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))
        self.assertEqual(self.run_("add", "../x", "TC-SHOP-001", "Pass", "x")[0], 2)

    def test_a_batch_applied_to_the_workbook_is_never_touched(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", now=NOW)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        ad.apply_batch(self.log()["results"], batch_id="batch_20260929_01")
        ad.save()
        before = self.log()
        code, out = self.run_("add", "20260929_01", "TC-SHOP-002", "Fail", "late correction")
        self.assertEqual(code, 1)
        self.assertIn("already applied to the workbook", out)
        self.assertIn("batch_20260929_02", out)                        # the name suggested for the correction
        self.assertEqual(self.log(), before)
        self.assertEqual(self.run_("add", "20260929_02", "TC-SHOP-002", "Fail", "late correction")[0], 0)

    def test_a_batch_sent_to_a_tracker_is_never_touched(self):
        self.project(tracker="jira", jira={"results": {"statuses": {"Pass": "PASSED", "Fail": "FAILED"}}})
        os.makedirs(os.path.join(self.ws, "tracker", "sent"))
        for name in ("batch_demo", "batch_demo_02"):
            with open(os.path.join(self.ws, "tracker", "sent", name + ".json"), "w", encoding="utf-8") as f:
                f.write("{}")
        code, out = self.run_("add", "demo", "DS-12", "Pass", "ok")
        self.assertEqual(code, 1)
        self.assertIn("sent to jira", out)
        self.assertIn("batch_demo_03", out)                            # _02 is taken too
        self.assertEqual(self.run_("add", "other", "DS-12", "Blocked", "x")[0], 2)      # not in the map
        self.assertEqual(self.run_("add", "other", "DS-12", "Pass", "ok")[0], 0)


if __name__ == "__main__":
    unittest.main()


class ProjectStatus(unittest.TestCase):
    """The 11.8 pilot: a workflow wrote 'Blocked' in a project whose word is 'Bloqueado'."""
    PT = ("Por executar", "Passou", "Falhou", "Bloqueado", "N/A")

    def proj(self, statuses, states=None):
        return {"tracker": "excel", "excel": {"cases": {"statuses": list(statuses)}},
                **({"dashboard": {"states": states}} if states else {})}

    def test_the_project_word_from_dashboard_states(self):
        p = self.proj(self.PT, {"Por executar": "pending", "Passou": "ok", "Falhou": "fail", "Bloqueado": "blocked",
                                "N/A": "na"})
        self.assertEqual(rl.project_status(p, "blocked"), "Bloqueado")
        self.assertEqual(rl.project_status(p, "pass"), "Passou")
        self.assertEqual(rl.project_status(p, "not run"), "Por executar")

    def test_the_english_word_when_the_vocabulary_has_it_or_has_none(self):
        self.assertEqual(rl.project_status(self.proj(["Not run", "Pass", "Fail", "Blocked", "N/A"]), "blocked"),
                         "Blocked")
        self.assertEqual(rl.project_status({"tracker": "excel"}, "fail"), "Fail")

    def test_no_word_for_the_meaning_is_a_usage_error(self):
        with self.assertRaises(rl.UsageError) as e:
            rl.project_status(self.proj(self.PT), "blocked")
        self.assertIn("dashboard.states", str(e.exception))
