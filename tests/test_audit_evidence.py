"""The audit presents evidence apart: valid, marked invalid (kept on file, not proof) and absent; and its summary mode
(phase 17.4 / PR-5). A fictional workspace in a temporary folder.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import qa_report as qr
import test_qa_report as base

RUN = {"environment": "test", "build": "1.0.0 (7)"}


class Moded(base.Base):
    def batch(self, name, results, run=None):
        super().batch(name, results, run)
        path = os.path.join(self.ws, "runs", f"{name}.json")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["mode"] = "manual: test"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)


class Evidence(Moded):
    def touch(self, name):
        open(os.path.join(self.ws, "evidence", "b1", name), "wb").close()
        return "evidence/b1/" + name

    def mixed(self):
        ok = self.touch("TC-SHOP-001_t1.png")
        bad = self.touch("TC-SHOP-002_t1.png")
        replaced = self.touch("TC-SHOP-003_t1.png")
        good = self.touch("TC-SHOP-003_t2.png")
        self.batch("batch_android_01", [
            base.item("TC-SHOP-001", "Pass", "2026-11-01T10:00", evidence=ok),                       # valid
            base.item("TC-SHOP-002", "Pass", "2026-11-01T10:05", evidence=bad,                       # only invalid
                      evidence_invalid=[{"path": bad, "reason": "captured another app", "at": "2026-11-01T10:09:00"}]),
            base.item("TC-SHOP-003", "Pass", "2026-11-01T10:10", evidence=[replaced, good],          # invalid + valid
                      evidence_invalid=[{"path": replaced, "reason": "blurred"}]),
            base.item("TC-SHOP-004", "Pass", "2026-11-01T10:15", evidence="evidence/b1/gone.png"),    # absent
            base.item("TC-SHOP-005", "Pass", "2026-11-01T10:20")], run=RUN)                         # none named
        return bad, replaced

    def entry(self):
        return qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))["batches"][0]

    def test_valid_invalid_and_absent_evidence_are_counted_apart(self):
        bad, replaced = self.mixed()
        e = self.entry()
        self.assertEqual(e["evidence_counts"], {"valid": 2, "invalid": 2, "missing": 1})
        self.assertEqual(sorted(e["invalid_evidence"]), sorted([f"TC-SHOP-002: {bad} (captured another app)",
                                                                f"TC-SHOP-003: {replaced} (blurred)"]))
        self.assertEqual(e["without_valid_evidence"], ["TC-SHOP-002"])      # the replaced one still has a valid capture
        self.assertEqual(e["missing_evidence"], ["TC-SHOP-004: evidence/b1/gone.png"])
        self.assertEqual(e["without_evidence"], ["TC-SHOP-005"])
        self.assertTrue(e["to_fix"])

    def test_an_invalid_file_still_on_disk_is_never_presented_as_valid(self):
        self.mixed()
        self.assertNotIn("TC-SHOP-002", " ".join(self.entry()["missing_evidence"]))
        _, out = self.run_("audit", "--detail")
        self.assertIn("evidence marked invalid (kept on file, not proof of the result)", out)
        self.assertIn("(captured another app)", out)
        self.assertIn("result(s) whose only evidence is invalid: TC-SHOP-002", out)
        self.assertIn("evidence: 2 valid · 2 invalid · 1 missing", out)

    def test_a_batch_with_a_replaced_capture_and_a_valid_one_is_not_a_problem(self):
        replaced, good = self.touch("a.png"), self.touch("b.png")
        self.batch("batch_android_01", [base.item("TC-SHOP-001", "Pass", "2026-11-01T10:00", evidence=[replaced, good],
                                                  evidence_invalid=[{"path": replaced, "reason": "blurred"}])], run=RUN)
        code, out = self.run_("audit", "--detail")
        self.assertEqual(code, 0)
        self.assertIn("evidence marked invalid", out)                       # said, but nothing to fix
        self.assertIn("nothing to fix", out)

    def test_the_json_report_carries_the_same_lists(self):
        self.mixed()
        report = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        self.assertIn("invalid_evidence", report["batches"][0])


class Summary(Moded):
    def setUp(self):
        super().setUp()
        open(os.path.join(self.ws, "evidence", "b1", "ok.png"), "wb").close()
        self.batch("batch_android_01", [base.item("TC-SHOP-001", "Pass", "2026-11-01T10:00", evidence="evidence/b1/ok.png")],
                   run=RUN)
        self.batch("batch_ios_01", [base.item("TC-SHOP-001", "Question", "2026-11-01T11:00"),
                                    base.item("TC-SHOP-002", "Pass", "2026-11-01T11:05", evidence="evidence/b1/ok.png")], run=RUN)

    def test_the_summary_lists_what_needs_a_decision_and_counts_the_rest(self):
        code, out = self.run_("audit")
        self.assertEqual(code, 1)
        self.assertIn("AUDIT 2 batch(es): 1 to fix · 1 with nothing that needs a decision", out)
        self.assertIn("Question open: TC-SHOP-001", out)
        self.assertIn("batch_ios_01", out)
        self.assertNotIn("batch_android_01", out)                            # the clean batch is counted, not listed
        self.assertIn("1 batch(es) to fix before applying", out)

    def test_the_complete_audit_is_one_flag_or_one_setting_away(self):
        self.assertIn("batch_android_01", self.run_("audit", "--detail")[1])
        self.proj["output"] = {"terminal": "normal"}
        self.write_project()
        self.assertIn("batch_android_01", self.run_("audit")[1])

    def test_a_caveat_is_never_dropped_by_the_summary(self):
        self.batch("batch_android_02", [base.item("TC-SHOP-001", "Pass", "2026-11-01T12:00")])          # no run block
        out = self.run_("audit")[1]
        self.assertIn("no build in the run block", out)
        self.assertIn("result(s) without evidence named", out)

    def test_the_summary_of_a_clean_workspace_says_so(self):
        for n in ("batch_ios_01",):
            os.remove(os.path.join(self.ws, "runs", n + ".json"))
        code, out = self.run_("audit")
        self.assertEqual(code, 0)
        self.assertIn("nothing to fix", out)


class Reports(Evidence):
    def read(self, name):
        with open(os.path.join(self.ws, "exports", name), encoding="utf-8") as f:
            return f.read()

    def test_the_summary_and_the_technical_report_show_valid_invalid_and_missing_evidence(self):
        self.mixed()
        code, out = self.run_("report", "--name", "demo")
        self.assertEqual(code, 1)                                           # something to fix: the exit says so
        self.assertIn("written: " + os.path.join("exports", "demo.technical.md"), out)
        self.assertIn("written: " + os.path.join("exports", "demo.summary.md"), out)
        summary, technical = self.read("demo.summary.md"), self.read("demo.technical.md")
        self.assertIn("Evidence: 2 valid · 2 marked invalid (kept on file, not proof) · 1 named but missing.", summary)
        self.assertIn("result(s) whose only evidence is invalid: TC-SHOP-002", summary)
        self.assertIn("exports/demo.technical.md", summary)
        self.assertLess(len(summary.splitlines()), len(technical.splitlines()))
        self.assertIn("(captured another app)", technical)
        self.assertIn("evidence: 2 valid · 2 invalid · 1 missing", technical)

    def test_without_evidence_problems_the_report_says_nothing_is_open(self):
        ok = self.touch("ok.png")
        self.batch("batch_android_01", [base.item("TC-SHOP-001", "Pass", "2026-11-01T10:00", evidence=ok)], run=RUN)
        code, _ = self.run_("report", "--name", "clean")
        self.assertEqual(code, 0)
        self.assertIn("Nothing open", self.read("clean.summary.md"))

    def test_the_project_chooses_which_reports_are_written(self):
        self.mixed()
        self.proj["output"] = {"reports": ["summary"]}
        self.write_project()
        self.run_("report", "--name", "only")
        self.assertTrue(os.path.exists(os.path.join(self.ws, "exports", "only.summary.md")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "exports", "only.technical.md")))
        self.assertNotIn("Complete data", self.read("only.summary.md"))        # no pointer to a file that is not there


if __name__ == "__main__":
    unittest.main()
