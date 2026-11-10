"""Phase A of batch 2 (plan of 10.11.2026), on fictional workspaces in temporary folders:

- Q1 partial batches: `run_log.py subset` copies chosen results unchanged with `derived_from`; the original is never
  touched; the apply refuses a result already applied from another batch (the original and a partial, or two partials);
  an old control row without `result_keys` is rebuilt from its run log; the audit lists the overlap and the derivation.
- Q2 proof: `add --observed/--claimed/--inferred/--requires-observed`; the audit flags a Pass with nothing observed or
  without a required observation, and only COUNTS the results that have no proof block (they are never judged).
- Q3 acceptance criteria: `ac_coverage.py init/report` (source available, absent, inferred; uncovered, not run, passed,
  partly passed, open; a case not in the workbook; a result noted as under review).
- Q5 approval files: `check_approval_files.py` (cut file, unbalanced fence, wrong count, command with an ellipsis, wrong
  hash) and the same inputs giving the same token in the dry runs.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import unittest
from datetime import datetime

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import ac_coverage as ac
import check_approval_files as caf
import qa_report as qr
import run_log as rl
import test_qa_report as base

from adapters.excel import add_cases as addc
from adapters.excel import annotate as an
from adapters.excel import apply as ap
from adapters.excel import reconcile as rec
from adapters.excel.base import InvalidBatch, execution_key, result_entry, result_key

CFG = base.CFG
NOW = datetime(2026, 11, 10, 10, 0, 0)
RUN = {"environment": "test", "build": "1.0.0 (7)"}


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


class Fixture(base.Base):
    """The qa_report fixture (workbook + project.yaml with the Excel example configuration)."""

    def full_batch(self, name="batch_android_full"):
        self.batch(name, [base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00"),
                          base.item("TC-SHOP-002", "Fail", "2026-11-10T09:05:00"),
                          base.item("TC-ACCOUNT-001", "Pass", "2026-11-10T09:10:00", evidence="evidence/b1/x.png")],
                   run=RUN)
        return os.path.join(self.ws, "runs", f"{name}.json")

    def sha(self, path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def apply(self, name):
        return quiet(ap.apply, self.ws, os.path.join("runs", f"{name}.json"), recalc=False, now=NOW)

    def control(self):
        wb = openpyxl.load_workbook(os.path.join(self.ws, CFG["file"]), read_only=True)
        return [r for r in wb["_qa_batches"].iter_rows(min_row=2, values_only=True)] if "_qa_batches" in wb.sheetnames else []


class PartialBatches(Fixture):
    def test_subset_copies_the_chosen_results_unchanged_records_the_origin_and_leaves_the_original_alone(self):
        original = self.full_batch()
        before = self.sha(original)
        path, _copied = rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001", "TC-ACCOUNT-001"],
                                 reason="only what is ready", now=NOW)
        self.assertEqual(self.sha(original), before)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        with open(original, encoding="utf-8") as f:
            orig = json.load(f)
        self.assertEqual([r["tc"] for r in doc["results"]], ["TC-SHOP-001", "TC-ACCOUNT-001"])
        self.assertEqual(doc["results"], [orig["results"][0], orig["results"][2]])        # unchanged, evidence included
        self.assertEqual(doc["run"], orig["run"])
        d = doc["derived_from"]
        self.assertEqual((d["batch"], d["file_sha256"], d["cases"], d["reason"]),
                         ("batch_android_full", before, ["TC-SHOP-001", "TC-ACCOUNT-001"], "only what is ready"))

    def test_subset_refuses_an_unknown_case_an_existing_target_the_same_name_and_an_empty_list(self):
        self.full_batch()
        with self.assertRaises(rl.UsageError):
            rl.subset(self.ws, "batch_android_full", "batch_android_p", ["TC-SHOP-999"])
        with self.assertRaises(rl.UsageError):
            rl.subset(self.ws, "batch_android_full", "batch_android_full", ["TC-SHOP-001"])
        with self.assertRaises(rl.UsageError):
            rl.subset(self.ws, "batch_android_full", "batch_android_p", [])
        rl.subset(self.ws, "batch_android_full", "batch_android_p", ["TC-SHOP-001"])
        with self.assertRaises(rl.Refused):
            rl.subset(self.ws, "batch_android_full", "batch_android_p", ["TC-SHOP-002"])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", "batch_android_none.json")))

    def test_the_same_result_cannot_be_applied_twice_from_the_original_and_a_partial_batch(self):
        self.full_batch()
        rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001", "TC-ACCOUNT-001"])
        self.assertEqual(self.apply("batch_android_part1"), 0)
        before = self.sha(os.path.join(self.ws, CFG["file"]))
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_full")
        msg = " ".join(cm.exception.args[0] if cm.exception.args and isinstance(cm.exception.args[0], list) else cm.exception.args)
        self.assertIn("TC-SHOP-001 (already applied from batch_android_part1)", msg)
        self.assertIn("TC-ACCOUNT-001 (already applied from batch_android_part1)", msg)
        self.assertNotIn("TC-SHOP-002 (already", msg)
        self.assertEqual(self.sha(os.path.join(self.ws, CFG["file"])), before)        # nothing written

    def test_two_partial_batches_with_a_case_in_common_cannot_both_be_applied(self):
        self.full_batch()
        rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001", "TC-SHOP-002"])
        rl.subset(self.ws, "batch_android_full", "batch_android_part2", ["TC-SHOP-002", "TC-ACCOUNT-001"])
        rl.subset(self.ws, "batch_android_full", "batch_android_part3", ["TC-ACCOUNT-001"])
        self.assertEqual(self.apply("batch_android_part1"), 0)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_part2")
        self.assertIn("TC-SHOP-002 (already applied from batch_android_part1)", str(cm.exception.args[0]))
        self.assertEqual(self.apply("batch_android_part3"), 0)                       # no case in common: fine

    def test_a_new_execution_of_the_same_case_is_not_a_duplicate(self):
        self.full_batch()
        rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001"])
        self.apply("batch_android_part1")
        self.batch("batch_android_again", [base.item("TC-SHOP-001", "Pass", "2026-11-10T11:00:00")], run=RUN)   # another time
        self.assertEqual(self.apply("batch_android_again"), 0)

    def test_the_control_sheet_keeps_the_result_keys_and_an_old_row_is_rebuilt_from_its_run_log(self):
        self.full_batch()
        rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001"])
        self.apply("batch_android_part1")
        row = self.control()[-1]
        with open(os.path.join(self.ws, "runs", "batch_android_part1.json"), encoding="utf-8") as f:
            expect = [result_entry(r) for r in json.load(f)["results"]]
        self.assertEqual(json.loads(row[6]), expect)
        # an older row (no keys): blank the cell, the keys come from runs/<batch_id>.json
        path = os.path.join(self.ws, CFG["file"])
        wb = openpyxl.load_workbook(path)
        wb["_qa_batches"].cell(wb["_qa_batches"].max_row, 7).value = None
        wb.save(path)
        with self.assertRaises(InvalidBatch):
            self.apply("batch_android_full")

    def test_result_key_ignores_evidence_and_the_record_time_but_not_the_observation(self):
        a = base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00")
        b = {**a, "evidence": "evidence/b1/x.png", "recorded_at": "2026-11-10T12:00:00", "proof": {"observed": ["x"]}}
        self.assertEqual(result_key(a), result_key(b))
        self.assertNotEqual(result_key(a), result_key({**a, "actual_result": "other"}))
        self.assertNotEqual(result_key(a), result_key({**a, "status": "Fail"}))

    def test_the_audit_lists_the_derivation_and_an_overlap_with_an_applied_batch(self):
        self.full_batch()
        rl.subset(self.ws, "batch_android_full", "batch_android_part1", ["TC-SHOP-001"], reason="r")
        self.apply("batch_android_part1")
        rep = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        by = {e["batch"]: e for e in rep["batches"]}
        self.assertEqual(by["batch_android_part1"]["derived_from"]["batch"], "batch_android_full")
        self.assertEqual(by["batch_android_full"]["already_applied_elsewhere"], {"TC-SHOP-001": "batch_android_part1"})
        self.assertTrue(by["batch_android_full"]["to_fix"])
        flags = [t for t, _ in qr._audit_flags(by["batch_android_full"])]
        self.assertTrue(any("already applied from another batch or corrected without a revision link" in t for t in flags))


class Gaps(Fixture):
    """Plan Q1, review of 10.11.2026: what the check cannot see, and the correction of an execution already applied."""

    def applied_a(self):
        self.batch("batch_android_a", [base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00")], run=RUN)
        self.assertEqual(self.apply("batch_android_a"), 0)

    def blank_keys(self, batch_id):
        path = os.path.join(self.ws, CFG["file"])
        wb = openpyxl.load_workbook(path)
        ws = wb["_qa_batches"]
        head = [c.value for c in ws[1]]
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, head.index("batch_id") + 1).value == batch_id:
                ws.cell(r, head.index("result_keys") + 1).value = None
        wb.save(path)

    def dry(self, name):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ap.main([self.ws, f"runs/{name}.json", "--dry-run", "--no-recalc", "--now", NOW.isoformat()])
        return rc, out.getvalue() + err.getvalue()

    def test_an_old_application_without_keys_and_without_its_run_log_blocks_the_apply_and_names_the_origin(self):
        self.applied_a()
        self.blank_keys("batch_android_a")
        os.remove(os.path.join(self.ws, "runs", "batch_android_a.json"))
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        before = self.sha(os.path.join(self.ws, CFG["file"]))
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_b")
        text = str(cm.exception.args[0])
        self.assertIn("duplicates cannot be ruled out against: batch_android_a applied at", text)
        self.assertIn("no result_keys in the control sheet and no runs/batch_android_a.json to rebuild them", text)
        self.assertEqual(self.sha(os.path.join(self.ws, CFG["file"])), before)
        rc, out = self.dry("batch_android_b")                       # the dry run says the same and writes nothing
        self.assertNotEqual(rc, 0, out)
        self.assertIn("batch_android_a", out)
        self.assertIn("no runs/batch_android_a.json", out)
        self.assertEqual(self.sha(os.path.join(self.ws, CFG["file"])), before)
        review = os.path.join(self.ws, "runs", "batch_android_b.dryrun.md")        # the dry run's review names it as well
        self.assertTrue(os.path.isfile(review))
        with open(review, encoding="utf-8") as f:
            self.assertIn("no runs/batch_android_a.json", f.read())

    def test_restoring_the_run_log_lifts_the_block_and_the_duplicates_are_then_found(self):
        self.applied_a()
        self.blank_keys("batch_android_a")
        path = os.path.join(self.ws, "runs", "batch_android_a.json")
        with open(path, encoding="utf-8") as f:
            saved = f.read()
        os.remove(path)
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        with self.assertRaises(InvalidBatch):
            self.apply("batch_android_b")
        with open(path, "w", encoding="utf-8") as f:
            f.write(saved)                                            # restored from where it was kept
        self.batch("batch_android_dup", [base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00"),
                                         base.item("TC-SHOP-003", "Pass", "2026-11-10T09:30:00")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_dup")
        self.assertIn("TC-SHOP-001 (already applied from batch_android_a)", str(cm.exception.args[0]))
        self.assertNotIn("cannot be ruled out", str(cm.exception.args[0]))
        self.assertEqual(self.apply("batch_android_b"), 0)

    def test_rows_of_added_cases_and_notes_are_not_unverifiable_but_only_when_their_file_is_found(self):
        os.makedirs(os.path.join(self.ws, "cases"))
        cases_file = os.path.join(self.ws, "cases", "cases_20261110_01.json")
        with open(cases_file, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "TC-SHOP-050", "title": "A brand new case"}]}, f)
        self.assertEqual(quiet(addc.main, [self.ws, "cases/cases_20261110_01.json", "--no-recalc"]), 0)
        rows = self.control()
        self.assertEqual(rows[-1][6], "[]")                         # a row of cases: no results, said explicitly
        self.blank_keys("cases_20261110_01")                        # an old row (no keys): recognised by its file
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        self.assertEqual(self.apply("batch_android_b"), 0)
        os.remove(cases_file)                                       # without the file nothing says it had no results
        self.batch("batch_android_c", [base.item("TC-SHOP-003", "Fail", "2026-11-10T10:05:00")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_c")
        self.assertIn("cases_20261110_01 applied at", str(cm.exception.args[0]))

    def test_the_rows_of_notes_keep_an_empty_list_of_results(self):
        os.makedirs(os.path.join(self.ws, "analysis"))
        with open(os.path.join(self.ws, "analysis", "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"annotations": [{"case": "TC-SHOP-001", "note": "a remark"}]}, f)
        self.assertEqual(quiet(an.main, [self.ws, "analysis/n1.json", "--no-recalc"]), 0)
        self.assertEqual(self.control()[-1][6], "[]")
        self.blank_keys("n1")                                       # old form: found by its file and its hash
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        self.assertEqual(self.apply("batch_android_b"), 0)

    def test_correcting_the_status_or_the_text_of_an_applied_execution_is_not_a_new_execution(self):
        self.applied_a()
        same_time = "2026-11-10T09:00:00"
        cases = {"status": base.item("TC-SHOP-001", "Fail", same_time),
                 "text": base.item("TC-SHOP-001", "Pass", same_time, actual_result="corrected observed text")}
        for what, item in cases.items():
            self.batch(f"batch_android_fix_{what}", [item], run=RUN)
            with self.assertRaises(InvalidBatch) as cm:
                self.apply(f"batch_android_fix_{what}")
            msg = str(cm.exception.args[0])
            self.assertIn("TC-SHOP-001 (an execution already applied from batch_android_a at the same time", msg, what)
            self.assertIn("revision_of", msg)
            self.assertEqual(execution_key(item), execution_key(base.item("TC-SHOP-001", "Pass", same_time)))
            self.assertNotEqual(result_key(item), result_key(base.item("TC-SHOP-001", "Pass", same_time)))

    def test_a_correction_with_a_revision_link_to_the_right_batch_is_accepted_and_a_wrong_link_is_not(self):
        self.applied_a()
        fixed = base.item("TC-SHOP-001", "Fail", "2026-11-10T09:00:00")
        self.batch("batch_android_wrong", [{**fixed, "revision_of": {"batch": "batch_android_other", "tc": "TC-SHOP-001"}}], run=RUN)
        with self.assertRaises(InvalidBatch):
            self.apply("batch_android_wrong")
        self.batch("batch_android_right", [{**fixed, "revision_of": {"batch": "batch_android_a", "tc": "TC-SHOP-001"}}], run=RUN)
        self.assertEqual(self.apply("batch_android_right"), 0)

    def test_the_same_case_executed_at_another_time_is_a_new_execution(self):
        self.applied_a()
        self.batch("batch_android_again", [base.item("TC-SHOP-001", "Fail", "2026-11-10T11:00:00")], run=RUN)
        self.assertEqual(self.apply("batch_android_again"), 0)

    def test_the_audit_says_a_correction_without_a_revision_link_would_be_refused(self):
        self.applied_a()
        self.batch("batch_android_fix", [base.item("TC-SHOP-001", "Fail", "2026-11-10T09:00:00")], run=RUN)
        rep = qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))
        e = next(x for x in rep["batches"] if x["batch"] == "batch_android_fix")
        self.assertIn("same execution, another status or text, no revision link", e["already_applied_elsewhere"]["TC-SHOP-001"])
        self.assertTrue(e["to_fix"])


def untimed(tc, status="Pass", text=None):
    """A result with NO time of execution (never invented)."""
    return {"tc": tc, "status": status, "actual_result": text or f"observed for {tc}"}


class Compatibility(Gaps):
    """Plan Q1, second review: results without a time, and the origin of the historic control rows."""

    def log_path(self, name):
        return os.path.join(self.ws, "runs", f"{name}.json")

    def cells(self):
        """Every cell of the sheets except the control sheet, and the control rows."""
        wb = openpyxl.load_workbook(os.path.join(self.ws, CFG["file"]))
        data = {(ws.title, c.row, c.column): c.value for ws in wb.worksheets if not ws.title.startswith("_")
                for row in ws.iter_rows() for c in row}
        ctl = [list(r) for r in wb["_qa_batches"].iter_rows(min_row=2, values_only=True)]
        return data, ctl

    def read(self, name):
        with open(self.log_path(name), encoding="utf-8") as f:
            return f.read()

    def index(self):
        from adapters.excel.base import ExcelAdapter
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        return ad, ad.applied_results_index()

    # -- results without a time ------------------------------------------------------------------------------
    def test_a_first_result_without_a_time_is_accepted_and_a_second_one_without_a_time_for_the_case_is_ambiguous(self):
        self.batch("batch_android_a", [untimed("TC-SHOP-001", "Pass", "first")], run=RUN)
        self.assertEqual(self.apply("batch_android_a"), 0)                              # nothing applied for the case yet
        self.batch("batch_android_b", [untimed("TC-SHOP-001", "Fail", "second")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_b")
        msg = str(cm.exception.args[0])
        self.assertIn("TC-SHOP-001 (no time of execution and the case already has a result applied from batch_android_a", msg)
        self.assertIn("a new execution cannot be told from a correction", msg)
        self.assertIn("run_log.py add --executed-at --executed-source", msg)

    def test_a_result_without_a_time_is_ambiguous_after_a_timed_one_unless_it_is_an_explicit_revision(self):
        self.applied_a()
        self.batch("batch_android_b", [untimed("TC-SHOP-001", "Fail", "second")], run=RUN)
        with self.assertRaises(InvalidBatch):
            self.apply("batch_android_b")
        self.batch("batch_android_c", [{**untimed("TC-SHOP-001", "Fail", "second"),
                                        "revision_of": {"batch": "batch_android_a", "tc": "TC-SHOP-001"}}], run=RUN)
        self.assertEqual(self.apply("batch_android_c"), 0)
        self.batch("batch_android_d", [untimed("TC-SHOP-002", "Fail", "another case")], run=RUN)   # another case: no prior
        self.assertEqual(self.apply("batch_android_d"), 0)

    def test_a_timed_result_after_an_untimed_one_is_a_new_execution_only_if_it_is_later_than_that_application(self):
        self.batch("batch_android_a", [untimed("TC-SHOP-001", "Pass", "old, time unknown")], run=RUN)
        self.assertEqual(self.apply("batch_android_a"), 0)                              # written at NOW = 2026-11-10T10:00:00
        self.batch("batch_android_early", [base.item("TC-SHOP-001", "Fail", "2026-11-10T08:00:00")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_early")
        self.assertIn("with no time of execution, written at 2026-11-10T10:00:00, and this result is not later than that",
                      str(cm.exception.args[0]))
        self.batch("batch_android_late", [base.item("TC-SHOP-001", "Fail", "2026-11-10T11:00:00")], run=RUN)
        self.assertEqual(self.apply("batch_android_late"), 0)                           # certainly a later execution

    def test_no_time_is_ever_filled_in(self):
        self.batch("batch_android_a", [untimed("TC-SHOP-001")], run=RUN)
        self.apply("batch_android_a")
        with open(self.log_path("batch_android_a"), encoding="utf-8") as f:
            self.assertNotIn("ts", json.load(f)["results"][0])
        self.assertIsNone(execution_key(untimed("TC-SHOP-001")))

    # -- origin of the historic rows -------------------------------------------------------------------------
    def test_a_run_log_edited_after_the_apply_is_not_a_proven_origin_when_the_keys_are_not_stored(self):
        self.applied_a()
        self.blank_keys("batch_android_a")
        text = self.read("batch_android_a").replace("observed for TC-SHOP-001", "EDITED text")
        with open(self.log_path("batch_android_a"), "w", encoding="utf-8") as f:
            f.write(text)
        _ad, idx = self.index()
        self.assertEqual(len(idx["unverifiable"]), 1)
        self.assertIn("runs/batch_android_a.json is not what was applied (its hash is not the one recorded in the row)",
                      idx["unverifiable"][0])
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_b")
        self.assertIn("duplicates cannot be ruled out against: batch_android_a", str(cm.exception.args[0]))
        self.assertIn("python -m adapters.excel.reconcile", str(cm.exception.args[0]))

    def test_with_the_keys_stored_a_run_log_edited_later_is_a_warning_and_the_original_results_still_count(self):
        self.applied_a()
        text = self.read("batch_android_a").replace("observed for TC-SHOP-001", "EDITED text")
        with open(self.log_path("batch_android_a"), "w", encoding="utf-8") as f:
            f.write(text)
        _ad, idx = self.index()
        self.assertEqual(idx["unverifiable"], [])
        self.assertTrue(any("is not what was applied" in w and "stored keys are used" in w for w in idx["warnings"]), idx["warnings"])
        # the edited file does not hide the duplicate of what was REALLY applied
        self.batch("batch_android_dup", [base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00"),
                                         base.item("TC-SHOP-003", "Pass", "2026-11-10T09:30:00")], run=RUN)
        with self.assertRaises(InvalidBatch) as cm:
            self.apply("batch_android_dup")
        self.assertIn("TC-SHOP-001 (already applied from batch_android_a)", str(cm.exception.args[0]))

    # -- reconciliation without silently unblocking -------------------------------------------------------------
    def broken(self):
        """batch A applied, its keys blanked and its run log lost; returns the exact text of the run log (what Git keeps)."""
        self.applied_a()
        self.blank_keys("batch_android_a")
        saved = self.read("batch_android_a")
        os.remove(self.log_path("batch_android_a"))
        self.batch("batch_android_b", [base.item("TC-SHOP-002", "Fail", "2026-11-10T10:00:00")], run=RUN)
        with self.assertRaises(InvalidBatch):
            self.apply("batch_android_b")
        return saved

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rec.main([self.ws, *args])
        return rc, out.getvalue() + err.getvalue()

    def test_reconcile_refuses_a_file_that_is_not_what_was_applied_and_the_block_stays(self):
        saved = self.broken()
        wrong = os.path.join(self.ws, "analysis")
        os.makedirs(wrong)
        path = os.path.join(wrong, "edited.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(saved.replace("observed for TC-SHOP-001", "EDITED text"))
        before = self.sha(os.path.join(self.ws, CFG["file"]))
        rc, out = self.cli("batch_android_a", "--from", "analysis/edited.json", "--dry-run")
        self.assertEqual(rc, 4, out)
        self.assertIn("is not what was applied as 'batch_android_a'", out)
        self.assertIn("is never accepted by this tool", out)
        rc, out = self.cli("batch_android_a", "--from", "analysis/edited.json")
        self.assertEqual(rc, 4, out)
        self.assertEqual(self.sha(os.path.join(self.ws, CFG["file"])), before)
        with self.assertRaises(InvalidBatch):                                   # still blocked
            self.apply("batch_android_b")

    def test_reconcile_with_the_proven_file_writes_only_the_key_cell_with_a_token_and_lifts_the_block(self):
        saved = self.broken()
        os.makedirs(os.path.join(self.ws, "analysis"))
        path = os.path.join(self.ws, "analysis", "restored.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(saved)
        before, ctl_before = self.cells()
        rc, out = self.cli("batch_android_a", "--from", "analysis/restored.json", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("is what was applied (same hash); 1 result key(s) would be written", out)
        self.assertEqual(self.cells()[0], before)                                # the dry run wrote nothing
        token = self.token_of(out)
        rc, out = self.cli("batch_android_a", "--from", "analysis/restored.json", "--expect", "v1-0000")
        self.assertEqual(rc, 4, out)                                             # a stale or wrong token
        self.assertIn("out of date", out)
        rc, out = self.cli("batch_android_a", "--from", "analysis/restored.json", "--expect", token)
        self.assertEqual(rc, 0, out)
        after, ctl_after = self.cells()
        self.assertEqual(before, after)                                          # no case sheet changed
        changed = [(i, j) for i, (x, y) in enumerate(zip(ctl_before, ctl_after)) for j, (u, v) in enumerate(zip(x, y)) if u != v]
        self.assertEqual(changed, [(len(ctl_before) - 1, 6)])                    # only the key cell of that row
        self.assertEqual(json.loads(ctl_after[-1][6]), [result_entry(base.item("TC-SHOP-001", "Pass", "2026-11-10T09:00:00"))])
        self.assertEqual(self.apply("batch_android_b"), 0)                       # the apply is unblocked, by proof
        rc, out = self.cli("batch_android_a", "--from", "analysis/restored.json")
        self.assertEqual(rc, 0)
        self.assertIn("already proven", out)

    def token_of(self, output):
        return next(w for w in output.replace("·", " ").split() if w.startswith("v1-"))


class Proof(Fixture):
    def add(self, case, status, **kw):
        return rl.add(self.ws, "batch_android_proof", case, status, "observed", environment="test", build="1.0.0 (7)",
                      now=NOW, executed_at="2026-11-10T09:00:00", executed_source="test", **kw)

    def entry(self):
        return qr.audit(self.ws, self.proj, qr.load_batches(self.ws, self.proj))["batches"][0]

    def test_the_proof_block_is_kept_apart_by_kind(self):
        path, _ = self.add("TC-SHOP-001", "Pass", proof={"observed": ["panel: item listed", "panel: item listed"],
                                                          "claimed": ["the assistant said it was saved"], "inferred": ["no duplicate"],
                                                          "requires_observed": ["panel"]})
        with open(path, encoding="utf-8") as f:
            item = json.load(f)["results"][0]
        self.assertEqual(item["proof"], {"observed": ["panel: item listed"], "claimed": ["the assistant said it was saved"],
                                         "inferred": ["no duplicate"], "requires_observed": ["panel"]})

    def test_a_pass_with_nothing_observed_or_without_the_required_observation_is_flagged(self):
        self.add("TC-SHOP-001", "Pass", proof={"claimed": ["the assistant said it was saved"]})
        self.add("TC-SHOP-002", "Pass", proof={"observed": ["chat: reply shown"], "requires_observed": ["panel", "chat"]})
        self.add("TC-SHOP-003", "Pass", proof={"observed": ["panel: listed", "chat: reply"], "requires_observed": ["panel", "chat"]})
        e = self.entry()
        self.assertEqual(e["proof_problems"], ["TC-SHOP-001: nothing observed (only claimed)",
                                               "TC-SHOP-002: required observation missing: panel"])
        self.assertTrue(e["to_fix"])
        flags = [t for t, must in qr._audit_flags(e) if must]
        self.assertTrue(any(t.startswith("Pass without the proof its case requires") for t in flags))

    def test_results_without_a_proof_block_are_only_counted_never_judged(self):
        self.add("TC-SHOP-001", "Pass")                                   # recorded without proof: as every earlier result
        self.add("TC-SHOP-002", "Fail", proof={"inferred": ["x"]})         # not a Pass: never judged
        e = self.entry()
        self.assertEqual((e["proof_problems"], e["pass_without_proof_class"]), ([], ["TC-SHOP-001"]))
        flags = dict(qr._audit_flags(e))
        counted = [t for t in flags if "without a proof classification" in t]
        self.assertEqual(len(counted), 1)
        self.assertFalse(flags[counted[0]])                               # informative, needs no decision

    def test_a_flag_on_an_applied_batch_does_not_ask_to_fix_what_cannot_be_changed(self):
        self.add("TC-SHOP-001", "Pass", proof={"claimed": ["the assistant said it"]})
        self.assertTrue(self.entry()["to_fix"])
        self.assertEqual(quiet(ap.apply, self.ws, "runs/batch_android_proof.json", recalc=False, now=NOW), 0)
        e = self.entry()
        self.assertEqual(e["proof_problems"], ["TC-SHOP-001: nothing observed (only claimed)"])
        self.assertFalse(e["to_fix"])                                    # applied: a batch is never changed, the flag stays

    def test_proof_gaps_of_a_result_without_the_block_is_empty(self):
        self.assertEqual(rl.proof_gaps({"tc": "TC-X", "status": "Pass"}), [])
        self.assertEqual(rl.check_proof({"observed": [" ", ""]}), None)


class Coverage(Fixture):
    def memory(self, pbi, acceptance):
        folder = os.path.join(self.ws, "memory", "tickets")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"azure-devops__demo__x__{pbi}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"components": {"fields": {"data": {"title": "Demo PBI", "acceptance_criteria": acceptance}}}}, f)

    def set_status(self, case, status, notes=None):
        path = os.path.join(self.ws, CFG["file"])
        wb = openpyxl.load_workbook(path)
        for ws in wb.worksheets:
            head = {c.value: c.column for c in ws[1]}
            for r in range(2, ws.max_row + 1):
                if ws.cell(r, 1).value == case:
                    ws.cell(r, head["Status"]).value = status
                    if notes:
                        ws.cell(r, head["Notes"]).value = notes
        wb.save(path)

    def test_init_splits_the_criteria_and_maps_no_cases_and_never_overwrites(self):
        self.memory(123, "Given a cart\nWhen I pay\nThen it is saved\n\nGiven a paid order\nWhen I open it\nThen I see it")
        amap = ac.init(self.ws, 123)
        self.assertEqual([c["id"] for c in amap["criteria"]], ["AC1", "AC2"])
        self.assertTrue(amap["criteria"][0]["text"].startswith("Given a cart When I pay"))
        self.assertEqual((amap["criteria_source"]["kind"], [c["cases"] for c in amap["criteria"]]), ("ticket-memory", [[], []]))
        out = os.path.join("analysis", "ac_map_123.json")
        self.assertEqual(quiet(ac.main, [self.ws, "init", "123"]), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.ws, out)))
        self.assertEqual(quiet(ac.main, [self.ws, "init", "123"]), 1)                      # never overwritten

    def test_a_source_that_is_not_available_is_said_and_fails_the_report(self):
        for pbi, acceptance in ((200, ""), (201, None)):
            self.memory(pbi, acceptance)
        no_ticket = ac.init(self.ws, 999)
        self.assertEqual((no_ticket["criteria_source"]["kind"], no_ticket["criteria_source"]["note"]),
                         ("none", "no ticket memory for this PBI"))
        empty = ac.init(self.ws, 200)
        self.assertEqual((empty["criteria_source"]["kind"], empty["criteria_source"]["note"]),
                         ("none", "the ticket has no acceptance criteria"))
        rep = ac.report(self.ws, empty)
        self.assertEqual(rep["exit"], 1)
        self.assertIn("the source of the acceptance criteria is not available", rep["problems"][0])

    def test_the_report_gives_a_verdict_per_criterion_and_flags_what_is_wrong(self):
        self.set_status("TC-SHOP-001", "Pass")
        self.set_status("TC-SHOP-002", "Fail")
        self.set_status("TC-SHOP-003", "To Do")
        self.set_status("TC-ACCOUNT-002", "Pass", notes="[10.11] UNDER REVIEW: no proof of X")
        amap = {"pbi": 1, "title": "T", "criteria_source": {"kind": "ticket-memory", "ref": "r"},
                "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001", "TC-ACCOUNT-002"]},
                             {"id": "AC2", "text": "b", "cases": ["TC-SHOP-001", "TC-SHOP-002"]},
                             {"id": "AC3", "text": "c", "cases": ["TC-SHOP-003"]},
                             {"id": "AC4", "text": "d", "cases": []},
                             {"id": "AC5", "text": "e", "cases": ["TC-SHOP-001", "TC-SHOP-003"]},
                             {"id": "AC6", "text": "f", "cases": ["TC-NONE-001"]}]}
        rep = ac.report(self.ws, amap)
        verdicts = {c["id"]: c["verdict"] for c in rep["criteria"]}
        self.assertEqual(verdicts, {"AC1": "passed, not confirmed", "AC2": "open", "AC3": "not run", "AC4": "uncovered",
                                    "AC5": "partly passed", "AC6": "uncovered"})
        self.assertTrue(next(c for c in rep["criteria"][0]["cases"] if c["case"] == "TC-ACCOUNT-002")["under_review"])
        self.assertIn("criterion AC4 is not covered by any case", rep["problems"])
        self.assertIn("case not in the workbook: TC-NONE-001 (criterion AC6)", rep["problems"])
        self.assertEqual(rep["exit"], 1)
        text = ac.render(rep)
        self.assertIn("UNCOVERED", text)
        self.assertIn("under review", text)

    def test_inferred_criteria_are_reported_as_inferred_and_a_fully_covered_map_exits_zero(self):
        self.set_status("TC-SHOP-001", "Pass")
        amap = {"pbi": 2, "criteria_source": {"kind": "inferred", "note": "from the BE tasks"},
                "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001"]}]}
        rep = ac.report(self.ws, amap)
        self.assertEqual(rep["exit"], 0)
        self.assertIn("the criteria are inferred (not from the ticket, no confirmed source, awaiting confirmation): from the BE tasks", rep["problems"])
        self.assertFalse(rep["complete"])                                 # inferred criteria: never complete coverage

    def run_log(self, name, items):
        self.batch(name, items, run=RUN)

    def test_a_question_in_a_run_log_is_shown_apart_from_the_workbook_and_stops_a_complete_coverage(self):
        self.set_status("TC-SHOP-001", "Pass", notes="[10.11] UNDER REVIEW: no proof of X")
        self.set_status("TC-SHOP-002", "Pass")
        self.run_log("batch_android_old", [base.item("TC-SHOP-001", "Pass", "2026-11-09T10:00:00"),
                                           base.item("TC-SHOP-002", "Pass", "2026-11-09T10:05:00")])
        self.run_log("batch_android_retest", [base.item("TC-SHOP-001", "Question", "2026-11-10T09:00:00",
                                                        evidence="evidence/b1/x.png")])
        amap = {"pbi": 4, "title": "T", "criteria_source": {"kind": "ticket-memory", "ref": "r"},
                "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001", "TC-SHOP-002"]},
                             {"id": "AC2", "text": "b", "cases": ["TC-SHOP-002"]}]}
        rep = ac.report(self.ws, amap)
        c1 = rep["criteria"][0]["cases"][0]
        self.assertEqual((c1["status"], c1["under_review"], c1["divergent"]), ("Pass", True, True))       # the three apart
        self.assertEqual((c1["run_log"]["status"], c1["run_log"]["batch"], c1["run_log"]["evidence"]),
                         ("Question", "batch_android_retest", 1))
        self.assertEqual({r["id"]: r["verdict"] for r in rep["criteria"]}, {"AC1": "open", "AC2": "passed"})
        self.assertFalse(rep["complete"])
        self.assertEqual(rep["exit"], 1)
        self.assertTrue(any("results under review or whose latest run-log result differs" in p and "TC-SHOP-001" in p
                            and "NOT complete" in p for p in rep["problems"]))
        text = ac.render(rep)
        self.assertIn("complete coverage: NO", text)
        self.assertIn("workbook: Pass; under review; latest run log: Question in batch_android_retest", text)

    def test_a_pass_that_is_only_under_review_is_never_a_plain_passed_and_nothing_under_review_is_complete(self):
        self.set_status("TC-SHOP-001", "Pass", notes="UNDER REVIEW")
        amap = {"pbi": 5, "criteria_source": {"kind": "ticket-memory"}, "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001"]}]}
        rep = ac.report(self.ws, amap)
        self.assertEqual(rep["criteria"][0]["verdict"], "passed, not confirmed")
        self.assertFalse(rep["complete"])
        self.set_status("TC-SHOP-001", "Pass", notes="plain note")
        clean = ac.report(self.ws, amap)
        self.assertEqual((clean["criteria"][0]["verdict"], clean["complete"], clean["exit"]), ("passed", True, 0))

    def test_reading_only_the_workbook_says_what_it_cannot_see(self):
        self.set_status("TC-SHOP-001", "Pass")
        self.run_log("batch_android_retest", [base.item("TC-SHOP-001", "Question", "2026-11-10T09:00:00")])
        amap = {"pbi": 6, "criteria_source": {"kind": "ticket-memory"}, "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001"]}]}
        rep = ac.report(self.ws, amap, run_logs=False)
        self.assertEqual(rep["criteria"][0]["verdict"], "passed")          # what the workbook alone says...
        self.assertIn("statuses come from the workbook only: a Question or a correction that exists only in a run log is not shown",
                      rep["limitations"])                                   # ...with its limit written in the report
        self.assertIn("limitation: statuses come from the workbook only", ac.render(rep))

    def test_a_required_related_verification_in_question_stops_a_totally_covered_criterion(self):
        self.set_status("TC-SHOP-001", "Pass")
        self.set_status("TC-SHOP-002", "Pass")
        self.run_log("batch_android_old", [base.item("TC-SHOP-001", "Pass", "2026-11-09T10:00:00"),
                                           base.item("TC-SHOP-002", "Pass", "2026-11-09T10:05:00")])
        amap = {"pbi": 7, "title": "T", "criteria_source": {"kind": "ticket-memory", "ref": "r"},
                "criteria": [{"id": "AC1", "text": "a", "source": "ticket",
                              "cases": [{"case": "TC-SHOP-001", "why": "it checks the criterion's own text"}],
                              "related": [{"case": "TC-SHOP-002", "why": "the FE task delivers it", "required": True},
                                          {"case": "TC-SHOP-003", "why": "optional", "required": False}]}]}
        ok = ac.report(self.ws, amap)
        self.assertEqual(ok["criteria"][0]["verdict"], "passed")                     # the required one passes; the optional one does not matter
        self.assertTrue(ok["complete"])
        self.run_log("batch_android_retest", [base.item("TC-SHOP-002", "Question", "2026-11-10T09:00:00")])
        rep = ac.report(self.ws, amap)
        self.assertEqual(rep["criteria"][0]["verdict"], "passed, required check open")
        self.assertFalse(rep["complete"])
        self.assertEqual(rep["exit"], 1)
        self.assertIn("a required related verification is not a pass: TC-SHOP-002 (criterion AC1): the criterion is not totally covered",
                      rep["problems"])
        text = ac.render(rep)
        self.assertIn("related (REQUIRED): TC-SHOP-002", text)
        self.assertIn("covers: TC-SHOP-001 [workbook: Pass] — it checks the criterion's own text", text)

    def test_a_related_verification_that_is_not_required_never_changes_the_verdict(self):
        self.set_status("TC-SHOP-001", "Pass")
        amap = {"pbi": 8, "criteria_source": {"kind": "ticket-memory"},
                "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001"],
                              "related": [{"case": "TC-SHOP-002", "why": "FE behaviour", "required": False}]}]}
        rep = ac.report(self.ws, amap)
        self.assertEqual((rep["criteria"][0]["verdict"], rep["complete"]), ("passed", True))
        self.assertEqual(rep["criteria"][0]["related"][0]["status"], "To Do")

    def test_the_workbook_is_not_touched_by_a_report(self):
        path = os.path.join(self.ws, CFG["file"])
        before = self.sha(path)
        ac.report(self.ws, {"pbi": 3, "criteria_source": {"kind": "inferred"}, "criteria": [{"id": "AC1", "text": "a", "cases": ["TC-SHOP-001"]}]})
        self.assertEqual(self.sha(path), before)


class ApprovalFiles(Fixture):
    def write(self, name, text):
        path = os.path.join(self.ws, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_a_clean_file_passes_and_the_usual_mistakes_are_reported(self):
        clean = "# Plan\n\n<!-- count: cases=2 -->\n- TC-A\n- TC-B\n\nCommand:\n\n~~~\npython x.py --expect <token>\n~~~\n"
        self.assertEqual(caf.check_text(clean), [])
        self.assertTrue(any("does not end with a new line" in p for p in caf.check_text("text without end")))
        self.assertTrue(any("truncation mark" in p for p in caf.check_text("a long sentence that\n...\n")))
        self.assertTrue(any("fence is not closed" in p for p in caf.check_text("# T\n\n~~~\ncode\n")))
        self.assertTrue(any("unbalanced «»" in p for p in caf.check_text("quote «open\n")))
        wrong = "<!-- count: attachments=3 -->\n- a\n- b\n\nfoo\n"
        self.assertEqual(caf.check_text(wrong), ["count 'attachments' declares 3 but the list that follows has 2 item(s) (line 1)"])
        cut_command = "Command:\n\n~~~\npython -m x --a 1 ...\n~~~\n"
        self.assertTrue(any("ellipsis" in p for p in caf.check_text(cut_command)))
        self.assertTrue(any("placeholder" in p for p in caf.check_text("Comando:\n\n~~~\nrun <file> --expect <token>\n~~~\n")))

    def test_a_declared_hash_is_compared_with_the_text_that_would_be_sent(self):
        html = "<div>text</div>"
        sibling = self.write("c.html", html)
        digest = hashlib.sha256(html.encode()).hexdigest()
        draft = self.write("c.md", f"text_sha256 = {digest}\n")
        self.assertEqual(caf.check_file(draft), [])
        with open(sibling, "a", encoding="utf-8") as f:
            f.write("<div>changed</div>")
        self.assertTrue(any("is not the SHA-256 of c.html" in p for p in caf.check_file(draft)))

    def test_an_html_payload_is_not_asked_for_a_final_new_line_but_is_still_checked_for_a_cut(self):
        ok = self.write("c1.html", "<div>whole text</div>")
        self.assertEqual(caf.check_file(ok), [])
        cut = self.write("c2.html", "<div>text that stops…")
        self.assertTrue(any("truncation mark" in p for p in caf.check_file(cut)))

    def test_cli_exit_codes(self):
        ok = self.write("ok.md", "fine\n")
        bad = self.write("bad.md", "cut")
        self.assertEqual(quiet(caf.main, [ok]), 0)
        self.assertEqual(quiet(caf.main, [ok, bad]), 1)
        self.assertEqual(quiet(caf.main, [os.path.join(self.ws, "nothing.md")]), 2)

    def token_of(self, fn, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fn(*args)
        return next(w for w in out.getvalue().replace("·", " ").split() if w.startswith(("v1-", "v2-")))

    def test_the_same_inputs_give_the_same_token_in_every_dry_run_and_a_change_gives_another(self):
        # apply
        self.full_batch()
        t1 = self.token_of(ap.main, [self.ws, "runs/batch_android_full.json", "--dry-run", "--no-recalc"])
        t2 = self.token_of(ap.main, [self.ws, "runs/batch_android_full.json", "--dry-run", "--no-recalc"])
        self.assertEqual(t1, t2)
        # add cases
        cases = os.path.join(self.ws, "cases")
        os.makedirs(cases)
        with open(os.path.join(cases, "c1.json"), "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "TC-SHOP-050", "title": "A brand new case"}]}, f)
        a1 = self.token_of(addc.main, [self.ws, "cases/c1.json", "--dry-run", "--no-recalc"])
        a2 = self.token_of(addc.main, [self.ws, "cases/c1.json", "--dry-run", "--no-recalc"])
        self.assertEqual(a1, a2)
        # annotate
        os.makedirs(os.path.join(self.ws, "analysis"))
        with open(os.path.join(self.ws, "analysis", "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"annotations": [{"case": "TC-SHOP-001", "note": "a remark"}]}, f)
        n1 = self.token_of(an.main, [self.ws, "analysis/n1.json", "--dry-run", "--no-recalc"])
        n2 = self.token_of(an.main, [self.ws, "analysis/n1.json", "--dry-run", "--no-recalc"])
        self.assertEqual(n1, n2)
        # a change in the input gives another token
        with open(os.path.join(self.ws, "analysis", "n1.json"), "w", encoding="utf-8") as f:
            json.dump({"annotations": [{"case": "TC-SHOP-001", "note": "another remark"}]}, f)
        self.assertNotEqual(self.token_of(an.main, [self.ws, "analysis/n1.json", "--dry-run", "--no-recalc"]), n1)


if __name__ == "__main__":
    unittest.main()
