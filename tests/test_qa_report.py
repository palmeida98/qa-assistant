"""scripts/qa_report.py: reconciled totals per platform and build, and the audit of run logs (/qa-report, /checkpoint).
A fictional workspace in a temporary folder.

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
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import qa_report as qr
from build_fixture import build

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)


def item(tc, status, ts, evidence=None, **extra):
    out = {"tc": tc, "status": status, "actual_result": f"observed for {tc}", "ts": ts, **extra}
    if evidence:
        out["evidence"] = evidence
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        os.makedirs(os.path.join(self.ws, "runs"))
        os.makedirs(os.path.join(self.ws, "evidence", "b1"))
        build(os.path.join(self.ws, CFG["file"]))
        self.proj = {"project": "Demo Shop", "tracker": "excel", "excel": copy.deepcopy(CFG),
                     "report": {"platforms": {"android": ["batch_android_*"], "ios": ["batch_ios_*"]}}}
        self.write_project()

    def tearDown(self):
        self.tmp.cleanup()

    def write_project(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f)

    def batch(self, name, results, run=None):
        doc = {"results": results}
        if run is not None:
            doc = {"run": run, **doc}
        with open(os.path.join(self.ws, "runs", f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = qr.main([self.ws, *args])
        return code, out.getvalue()


class Totals(Base):
    def setUp(self):
        super().setUp()
        self.batch("batch_android_01", [item("TC-SHOP-001", "Pass", "2026-11-01T10:00"),
                                        item("TC-SHOP-002", "Fail", "2026-11-01T10:05"),
                                        item("TC-SHOP-003", "Blocked", "2026-11-01T10:10")],
                   run={"environment": "test", "build": "1.0.0 (7)"})
        self.batch("batch_android_02", [item("TC-SHOP-002", "Pass", "2026-11-03T09:00")],      # a retest, new build
                   run={"environment": "test", "build": "1.0.1 (8)"})
        self.batch("batch_ios_01", [item("TC-SHOP-001", "Fail", "2026-11-02T11:00")],
                   run={"environment": "test", "build": "1.0.0 (7)"})

    def report(self, **kw):
        batches = qr.load_batches(self.ws, self.proj)
        return qr.totals(self.ws, self.proj, batches, **kw)

    def test_platforms_stay_apart_and_each_reconciles(self):
        r = self.report()
        self.assertEqual(set(r["platforms"]), {"android", "ios"})
        android, ios = r["platforms"]["android"], r["platforms"]["ios"]
        self.assertEqual(android["cases"], 3)
        self.assertEqual(android["statuses"], {"Blocked": 1, "Pass": 2})
        self.assertTrue(android["reconciled"])
        self.assertEqual(ios["cases"], 1)
        self.assertEqual(ios["statuses"], {"Fail": 1})          # the iOS Fail never touches the Android totals

    def test_the_current_result_is_the_newest_and_the_older_one_is_history(self):
        android = self.report()["platforms"]["android"]
        self.assertEqual(android["historical_results"], 1)
        self.assertEqual([r["status"] for r in android["retests"]["TC-SHOP-002"]], ["Fail", "Pass"])
        self.assertEqual(android["by_build"], {"1.0.0 (7)": {"cases": 2, "statuses": {"Blocked": 1, "Pass": 1}},
                                               "1.0.1 (8)": {"cases": 1, "statuses": {"Pass": 1}}})

    def test_on_the_same_instant_a_declared_revision_wins_and_the_file_time_never_decides(self):
        # a working batch and the batch that applied a decision share the observation's ts (a real case: a Question
        # applied as Blocked); neither the name of the batch nor the file's modification time may decide (audit
        # 06.11.2026: touching a file changed the current result)
        self.batch("batch_android_zz", [item("TC-SHOP-004", "Pass", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        self.batch("batch_android_aa", [item("TC-SHOP-004", "Blocked", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        android = self.report()["platforms"]["android"]
        self.assertEqual(android["ambiguous_order"], ["TC-SHOP-004"])         # nothing decides: said, not guessed
        older = os.path.join(self.ws, "runs", "batch_android_zz.json")
        os.utime(older, (os.path.getmtime(older) + 60, os.path.getmtime(older) + 60))
        self.assertEqual(self.report()["platforms"]["android"]["ambiguous_order"], ["TC-SHOP-004"])
        self.batch("batch_android_aa", [{**item("TC-SHOP-004", "Blocked", "2026-11-05T10:00"),
                                         "revision_of": "batch_android_zz"}], run={"build": "1.0.1 (8)"})
        android = self.report()["platforms"]["android"]
        self.assertEqual(android["retests"]["TC-SHOP-004"][-1]["status"], "Blocked")
        self.assertEqual(android["ambiguous_order"], [])

    def test_a_current_result_says_whether_it_is_applied_and_unknown_is_not_applied(self):
        self.batch("batch_android_x", [item("TC-SHOP-001", "Fail", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        android = self.report()["platforms"]["android"]
        self.assertIn("TC-SHOP-001", android["current_not_applied"])

    def test_an_application_of_an_earlier_execution_keeps_the_execution_and_says_both(self):
        # closing of the audit (07.11.2026): a Question observed in a working batch, then recorded in the workbook as
        # Blocked by a batch of its own (the workbook has no Question status). The application has no execution time
        # and must not get one: it points at the execution it records.
        self.batch("batch_ios_work", [item("TC-SHOP-005", "Question", "2026-11-04T07:16:00")])
        self.batch("batch_ios_020", [{"tc": "TC-SHOP-005", "status": "Blocked", "actual_result": "[Question] ...",
                                      "applies": "batch_ios_work"}], run={"build": "1.0.0 (37)"})
        applied = {"batch_ios_020": "applied to plan-ios.xlsx on 2026-11-04T08:02:46"}

        def view(name, run=None):
            return applied.get(name)
        with mock.patch.object(qr, "_applied_view", return_value=view):
            ios = self.report()["platforms"]["ios"]
            text = qr.render_totals(self.report(), "all")
            audit = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertEqual(ios["retests"].get("TC-SHOP-005"), None)          # one execution, not two results
        self.assertNotIn("TC-SHOP-005", ios["without_ts"])
        self.assertNotIn("TC-SHOP-005", ios["current_not_applied"])         # recorded in the workbook by the link
        self.assertEqual(ios["applied_as"]["TC-SHOP-005"]["same_status"], False)
        self.assertIn("execution Question (batch_ios_work, 2026-11-04T07:16:00); applied as Blocked (batch_ios_020, "
                      "applied 2026-11-04T08:02:46)", text)
        entry = next(e for e in audit["batches"] if e["batch"] == "batch_ios_020")
        self.assertEqual(entry["without_ts"], [])
        # the execution's build is still its own: unknown, never the build of the application
        self.assertIn("TC-SHOP-005", ios["unknown_build"])

    def test_an_application_without_the_link_is_still_flagged_as_without_ts(self):
        self.batch("batch_ios_work", [item("TC-SHOP-005", "Question", "2026-11-04T07:16:00")])
        self.batch("batch_ios_020", [{"tc": "TC-SHOP-005", "status": "Blocked", "actual_result": "x"}],
                   run={"build": "1.0.0 (37)"})
        ios = self.report()["platforms"]["ios"]
        self.assertIn("TC-SHOP-005", ios["without_ts"])
        self.assertEqual(ios["retests"]["TC-SHOP-005"][-1]["status"], "Question")

    def test_a_result_without_build_is_unknown_and_never_counted_in_the_current_build(self):
        self.batch("batch_ios_nobuild", [item("TC-SHOP-006", "Pass", "2026-11-04T09:00")])
        ios = self.report()["platforms"]["ios"]
        self.assertEqual(ios["by_build"]["unknown"], {"cases": 1, "statuses": {"Pass": 1}})
        self.assertEqual(ios["by_build"]["1.0.0 (7)"]["cases"], 1)            # only the batch that declares it
        self.assertEqual(ios["unknown_build"], ["TC-SHOP-006"])
        self.assertTrue(ios["reconciled"])
        self.assertIn("Build unknown (no build in the run block; never counted as any build): 1 case(s)",
                      qr.render_totals(self.report(), "all"))

    def test_builds_written_differently_are_one_build(self):
        self.batch("batch_android_a1", [item("TC-SHOP-001", "Pass", "2026-11-05T10:00")], run={"build": "pt.app 1.0.1 (8)"})
        self.batch("batch_android_a2", [item("TC-SHOP-002", "Pass", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        android = self.report()["platforms"]["android"]
        self.assertNotIn("pt.app 1.0.1 (8)", android["by_build"])
        self.assertIn("1.0.1 (8)", android["by_build"])

    def test_on_the_same_instant_the_batch_applied_later_wins_over_a_working_batch(self):
        # a working batch edited after the batch that applied it must not take its place (a real workspace, 06.11.2026)
        self.batch("batch_android_apply", [item("TC-SHOP-004", "Fail", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        self.batch("batch_android_work", [item("TC-SHOP-004", "Question", "2026-11-05T10:00")], run={"build": "1.0.1 (8)"})
        work = os.path.join(self.ws, "runs", "batch_android_work.json")
        os.utime(work, (os.path.getmtime(work) + 60, os.path.getmtime(work) + 60))     # written last, never applied
        applied = {"batch_android_apply": "applied to x.xlsx on 2026-11-05T11:00:00"}

        def view(name, run=None):
            return applied.get(name)
        with mock.patch.object(qr, "_applied_view", return_value=view):
            android = self.report()["platforms"]["android"]
        self.assertEqual(android["retests"]["TC-SHOP-004"][-1]["status"], "Fail")

    def test_coverage_still_missing_comes_from_the_plan(self):
        os.makedirs(os.path.join(self.ws, "batches"))
        with open(os.path.join(self.ws, "batches", "coupon.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-004"]}, f)
        code, out = self.run_("totals", "--plan", "batches/coupon.yaml", "--platform", "ios")
        self.assertEqual(code, 0)
        self.assertIn("Planned: 3; with a result: 1 (33% of 3); not run: 2", out)
        self.assertIn("Not run: TC-SHOP-002, TC-SHOP-004", out)
        self.assertNotIn("Platform: android", out)

    def test_every_percentage_names_its_denominator(self):
        _, out = self.run_("totals")
        for line in out.splitlines():
            if "%" in line and "Rule" not in line:
                with self.subTest(line=line):
                    self.assertRegex(line, r"\d+% of \d+")

    def test_a_batch_with_no_platform_is_unknown_never_folded_into_another(self):
        self.batch("batch_web_01", [item("TC-SHOP-004", "Pass", "2026-11-04T10:00")])
        r = self.report()
        self.assertEqual(r["platforms"]["unknown"]["cases"], 1)
        self.assertEqual(r["platforms"]["android"]["cases"], 3)
        self.assertEqual(r["platforms"]["unknown"]["by_build"], {"unknown": {"cases": 1, "statuses": {"Pass": 1}}})

    def test_the_run_block_platform_wins(self):
        self.batch("batch_android_03", [item("TC-SHOP-004", "Pass", "2026-11-04T10:00")],
                   run={"environment": "test", "build": "2.0 (1)", "platform": "ios"})
        self.assertEqual(self.report()["platforms"]["ios"]["cases"], 2)

    def test_the_report_file_stays_in_the_workspace(self):
        code, out = self.run_("totals", "--output", "exports/qa_report_test.md")
        self.assertEqual(code, 0)
        self.assertIn("written: exports", out)
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "exports", "qa_report_test.md")))
        code, _ = self.run_("totals", "--output", "../outside.md")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.ws), "outside.md")))

    def test_an_unknown_batch_is_an_error(self):
        code, out = self.run_("totals", "--batch", "nothing")
        self.assertEqual(code, 2)
        self.assertIn("no run log for: nothing", out)


class Audit(Base):
    def test_a_consistent_batch_has_nothing_to_fix(self):
        open(os.path.join(self.ws, "evidence", "b1", "TC-SHOP-001_t1.png"), "wb").close()
        self.batch("batch_android_01", [item("TC-SHOP-001", "Pass", "2026-11-01T10:00",
                                             evidence="evidence/b1/TC-SHOP-001_t1.png")],
                   run={"environment": "test", "build": "1.0.0 (7)"})
        code, out = self.run_("audit")
        self.assertEqual(code, 0)
        self.assertIn("applied: no", out)
        self.assertIn("nothing to fix", out)

    def test_missing_evidence_open_question_and_proposed_bug_are_named(self):
        self.batch("batch_android_01", [
            item("TC-SHOP-001", "Question", "2026-11-01T10:00"),
            item("TC-SHOP-002", "Fail", "2026-11-01T10:05", evidence="evidence/b1/TC-SHOP-002_BUG-new.png",
                 new_defect={"title": "[Cart] total wrong", "proposed": True})])
        code, out = self.run_("audit")
        self.assertEqual(code, 1)
        self.assertIn("Question open: TC-SHOP-001", out)
        self.assertIn("evidence named but missing: TC-SHOP-002: evidence/b1/TC-SHOP-002_BUG-new.png", out)
        self.assertIn("bugs proposed, to review: TC-SHOP-002", out)
        self.assertIn("no build in the run block", out)

    def test_evidence_named_in_the_text_counts(self):
        self.batch("batch_ios_01", [{"tc": "TC-SHOP-001", "status": "Pass", "ts": "2026-11-01T10:00",
                                     "actual_result": "ok; evidence/b1/missing.png"}])
        report = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertEqual(report["batches"][0]["missing_evidence"], ["TC-SHOP-001: evidence/b1/missing.png"])

    def test_a_pattern_of_evidence_counts_when_it_matches(self):
        open(os.path.join(self.ws, "evidence", "b1", "TC-SHOP-001_t1.png"), "wb").close()
        self.batch("batch_android_01", [{"tc": "TC-SHOP-001", "status": "Pass", "ts": "2026-11-01T10:00",
                                         "actual_result": "see evidence/b1/TC-SHOP-001*.png"},
                                        {"tc": "TC-SHOP-002", "status": "Pass", "ts": "2026-11-01T10:00",
                                         "actual_result": "see evidence/b1/TC-SHOP-002*.png"}])
        report = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertEqual(report["batches"][0]["missing_evidence"], ["TC-SHOP-002: evidence/b1/TC-SHOP-002*.png"])

    def test_a_batch_of_another_workbook_is_read_there(self):
        # excel.workbooks: the iOS batches go to another workbook; its control sheet decides, never the main one
        self.proj["excel"]["workbooks"] = {"ios": {"file": "ios-plan.xlsx", "batches": ["batch_ios_*"]}}
        self.write_project()
        self.batch("batch_ios_01", [item("TC-SHOP-001", "Pass", "2026-11-01T10:00")])
        report = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertTrue(report["batches"][0]["applied"].startswith("unknown (the workbook ios-plan.xlsx"))
        build(os.path.join(self.ws, "ios-plan.xlsx"))
        report = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertEqual(report["batches"][0]["applied"], "no")

    def test_it_writes_nothing(self):
        self.batch("batch_android_01", [item("TC-SHOP-001", "Question", "2026-11-01T10:00")])
        before = sorted(os.walk(self.ws))
        self.run_("audit")
        self.run_("totals")
        self.assertEqual(sorted(os.walk(self.ws)), before)


if __name__ == "__main__":
    unittest.main()
