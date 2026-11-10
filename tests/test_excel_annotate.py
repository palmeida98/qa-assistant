"""`python -m adapters.excel.annotate` (technique-excel.md section 4.2; plan Q10, 10.11.2026), on a fictional workspace:

- the dry run writes nothing in the workbook and puts its review next to the annotations file (never in runs/);
- the real write adds a dated note to the Notes column and leaves the status and the observed result of the case, and
  every other cell, exactly as they were;
- the preview token: --expect refuses the write when the file, the workbook or an option changed since the dry run;
- the same annotations again change nothing; the same file name with another content, a note already in the case's
  notes, an unknown case and a case twice in a file are refused, writing nothing.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import hashlib
import io
import json
import os
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

from adapters.excel import annotate as an

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 10, 9, 0, 0)


class Annotate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.wb = os.path.join(self.ws, CFG["file"])
        build(self.wb)
        proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel", "excel": CFG,
                "report": {"platforms": {"android": ["batch_2026*"]}}}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        os.makedirs(os.path.join(self.ws, "analysis"))
        os.makedirs(os.path.join(self.ws, "runs"))
        self.env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                if k not in ("QA_WORKBOOK", "QA_LOCK_WAIT")}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    # ---------------------------------------------------------------- helpers
    def file(self, name="annotations_01", items=None):
        items = items if items is not None else [{"case": "TC-SHOP-001", "note": "Under review: no independent proof."},
                                                 {"case": "TC-ACCOUNT-001", "note": "Second note."}]
        path = os.path.join(self.ws, "analysis", name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"annotations": items}, f, ensure_ascii=False)
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = an.main([self.ws, *args])
        return rc, out.getvalue() + err.getvalue()

    def sha(self, path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def cells(self):
        """Every cell of the case sheets and of the defects sheet, by (sheet, row, column), and the control rows."""
        wb = openpyxl.load_workbook(self.wb)
        data = {(ws.title, c.row, c.column): c.value for ws in wb.worksheets if not ws.title.startswith("_")
                for row in ws.iter_rows() for c in row}
        ctl = [r for r in wb["_qa_batches"].iter_rows(min_row=2, values_only=True)] if "_qa_batches" in wb.sheetnames else []
        return data, ctl

    def token_of(self, output):
        return next(w for w in output.split() if w.startswith("v1-"))

    def notes_of(self, case_row_sheet):
        wb = openpyxl.load_workbook(self.wb)
        ws = wb[case_row_sheet]
        head = {c.value: c.column for c in ws[1]}
        return {ws.cell(r, 1).value: ws.cell(r, head["Notes"]).value for r in range(2, ws.max_row + 1)}

    # ------------------------------------------------------------------ tests
    def test_the_dry_run_writes_nothing_in_the_workbook_and_puts_its_review_next_to_the_file(self):
        path, before = self.file(), self.sha(self.wb)
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sha(self.wb), before)
        review = os.path.join(self.ws, "analysis", "annotations_01.dryrun.md")
        self.assertTrue(os.path.isfile(review))
        self.assertFalse([f for f in os.listdir(os.path.join(self.ws, "runs")) if f.endswith(".md")])
        with open(review, encoding="utf-8") as f:
            text = f.read()
        for needle in ("Under review: no independent proof.", "Second note.", "pre-existing note", "--expect v1-"):
            self.assertIn(needle, text)
        self.assertIn(self.token_of(out), text)
        self.assertTrue(path)

    def test_the_write_adds_only_the_notes_and_keeps_the_status_the_result_and_every_other_cell(self):
        self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc", "--now", NOW.isoformat())
        token = self.token_of(out)
        before, ctl_before = self.cells()
        rc, out = self.run_cli("analysis/annotations_01.json", "--no-recalc", "--now", NOW.isoformat(), "--expect", token)
        self.assertEqual(rc, 0, out)
        after, ctl_after = self.cells()
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        head = {c.value: c.column for c in openpyxl.load_workbook(self.wb)["Shop"][1]}
        notes_cells = {("Shop", 2, head["Notes"]), ("Account", 2, head["Notes"])}
        self.assertEqual(changed, notes_cells, "only the two Notes cells may change")
        notes = self.notes_of("Account")
        self.assertTrue(notes["TC-ACCOUNT-001"].startswith("pre-existing note\n\n[2026-11-10, annotations_01] Second note."))
        self.assertIn("[2026-11-10, annotations_01] Under review: no independent proof.", self.notes_of("Shop")["TC-SHOP-001"])
        self.assertEqual(len(ctl_after), len(ctl_before) + 1)
        self.assertEqual(ctl_after[-1][5], "annotations_01")

    def test_the_same_annotations_again_change_nothing(self):
        self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")
        self.run_cli("analysis/annotations_01.json", "--no-recalc", "--expect", self.token_of(out))
        after_first = self.sha(self.wb)
        rc, out = self.run_cli("analysis/annotations_01.json", "--no-recalc")
        self.assertEqual(rc, 0, out)
        self.assertIn("already written", out)
        self.assertEqual(self.sha(self.wb), after_first)

    def test_a_note_already_in_the_cases_notes_is_refused(self):
        self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")
        self.run_cli("analysis/annotations_01.json", "--no-recalc", "--expect", self.token_of(out))
        self.file("annotations_02", [{"case": "TC-SHOP-001", "note": "Under review: no independent proof."}])
        before = self.sha(self.wb)
        rc, out = self.run_cli("analysis/annotations_02.json", "--dry-run", "--no-recalc")
        self.assertEqual(rc, 4, out)
        self.assertIn("already in the case's notes", out)
        self.assertEqual(self.sha(self.wb), before)

    def test_an_unknown_case_a_case_twice_and_a_bad_entry_are_refused_with_every_problem(self):
        self.file("annotations_03", [{"case": "TC-SHOP-999", "note": "x"}, {"case": "TC-SHOP-001", "note": "a"},
                                     {"case": "TC-SHOP-001", "note": "b"}, {"case": "TC-SHOP-002"},
                                     {"case": "TC-SHOP-003", "note": "n", "status": "Pass"}])
        before = self.sha(self.wb)
        rc, out = self.run_cli("analysis/annotations_03.json", "--dry-run", "--no-recalc")
        self.assertEqual(rc, 4, out)
        for needle in ("TC-SHOP-999 is not in the workbook", "appears twice", "'note' is required", "only 'case' and 'note'"):
            self.assertIn(needle, out)
        self.assertEqual(self.sha(self.wb), before)

    def test_the_token_refuses_a_write_when_the_file_the_workbook_or_an_option_changed(self):
        path = self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")
        token = self.token_of(out)
        before = self.sha(self.wb)
        self.file("annotations_01", [{"case": "TC-SHOP-001", "note": "Another note."}])        # the file changed
        rc, out = self.run_cli("analysis/annotations_01.json", "--no-recalc", "--expect", token)
        self.assertEqual(rc, 4, out)
        self.assertIn("out of date", out)
        self.assertEqual(self.sha(self.wb), before)
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")      # new dry run: new token
        self.assertNotEqual(self.token_of(out), token)
        rc, out = self.run_cli("analysis/annotations_01.json", "--no-recalc", "--label", "[x]", "--expect", self.token_of(out))
        self.assertEqual(rc, 4, out)                                                         # an option changed
        self.assertEqual(self.sha(self.wb), before)
        self.assertTrue(path)

    def test_the_same_file_name_with_another_content_is_refused_after_it_was_written(self):
        self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc")
        self.run_cli("analysis/annotations_01.json", "--no-recalc", "--expect", self.token_of(out))
        self.file("annotations_01", [{"case": "TC-SHOP-002", "note": "New content with the same name."}])
        before = self.sha(self.wb)
        rc, out = self.run_cli("analysis/annotations_01.json", "--no-recalc")
        self.assertEqual(rc, 4, out)
        self.assertIn("DIFFERENT content", out)
        self.assertEqual(self.sha(self.wb), before)

    def test_the_write_without_a_token_is_possible_but_dry_run_with_a_token_is_not(self):
        self.file()
        rc, out = self.run_cli("analysis/annotations_01.json", "--dry-run", "--no-recalc", "--expect", "v1-0000")
        self.assertEqual(rc, 4, out)
        self.assertIn("--expect goes with the real write", out)


if __name__ == "__main__":
    unittest.main()
