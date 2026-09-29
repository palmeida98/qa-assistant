"""Batch validation in the Excel adapter (technique-excel.md section 4).

python -m unittest discover -s tests   (from the root of qa-assistant)
Does not need LibreOffice: never recalculates.
"""

import copy
import json
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import ExcelAdapter, InvalidBatch, load_batch

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)


def item(tc="TC-SHOP-001", status="Pass", **extra):
    return {"tc": tc, "status": status, "actual_result": "obs", "new_defect": None, **extra}


class ValidateBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        build(os.path.join(self.tmp.name, CFG["file"]))
        self.ad = ExcelAdapter.from_dict(copy.deepcopy(CFG), base_dir=self.tmp.name)
        self.ad.load()

    def tearDown(self):
        self.tmp.cleanup()

    def refused(self, batch, fragment):
        """The batch is refused, with `fragment` in the message, and the workbook stays the same."""
        before = self.ad.progress_summary()
        with self.assertRaises(InvalidBatch) as cm:
            self.ad.apply_batch(batch)
        self.assertIn(fragment, str(cm.exception))
        self.assertEqual(self.ad.progress_summary(), before)
        self.assertEqual(self.ad.applied_batches(), [])

    def test_valid_batch(self):
        self.assertEqual(self.ad.validate_batch([item(), item("TC-ACCOUNT-001", "Fail")]), [])

    def test_empty(self):
        self.refused([], "empty batch")

    def test_status_outside_the_vocabulary(self):
        self.refused([item(status="Passed")], "status 'Passed'")

    def test_a_proposed_defect_is_never_applied(self):
        # plan 11.6 (methodology 9.4): the user approves a remote run's bug by removing "proposed" in the PR
        new = {"title": "Coupon accepted", "severity": "High", "priority": "P2"}
        self.refused([item(status="Fail", new_defect={**new, "proposed": True})], 'still a proposal ("proposed")')
        self.assertEqual(self.ad.validate_batch([item(status="Fail", new_defect={**new, "proposed": False})]), [])

    def test_a_question_must_be_resolved(self):
        self.refused([item(status="Question")], "resolve the Question")

    def test_without_status(self):
        self.refused([{"tc": "TC-SHOP-001"}], "'status' is missing")

    def test_case_does_not_exist(self):
        self.refused([item("TC-NONE-999")], "the case does not exist")

    def test_repeated_case(self):
        self.refused([item(), item(status="Fail")], "case repeated")

    def test_bug_does_not_exist(self):
        self.refused([item(status="Fail", bug="BUG-999")], "bug 'BUG-999'")

    def test_priority_outside_the_list(self):
        self.refused([item(status="Fail", new_defect={"title": "t", "priority": "P9"})], "priority")

    def test_invalid_status_does_not_write_the_valid_items(self):
        self.refused([item("TC-SHOP-003", "Pass"), item(status="xx")], "status 'xx'")

    def test_an_existing_bug_adds_a_note(self):
        r = self.ad.apply_batch([item("TC-SHOP-004", "Fail", bug="BUG-1")], label="[t]")
        self.assertEqual(r["new_defects"], [])
        row = self.ad.cases_with_status("Fail")["Shop"]
        notes = next(x for x in row if x["ID"] == "TC-SHOP-004")["Notes"]
        self.assertTrue(str(notes).endswith("[t] Linked defect: BUG-1."), notes)

    def test_extra_keys_are_ignored(self):
        self.assertEqual(self.ad.validate_batch([item(ts="2026-09-24T10:00:00", note="x")]), [])


class BatchEditedAfterBeingApplied(unittest.TestCase):
    """technique-excel.md section 5: the hash only recognises equal content; the batch_id
    prevents a batch edited after being applied from duplicating notes and defects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        build(os.path.join(self.tmp.name, CFG["file"]))
        self.ad = ExcelAdapter.from_dict(copy.deepcopy(CFG), base_dir=self.tmp.name)
        self.ad.load()
        self.batch = [item("TC-ACCOUNT-001", "Fail", new_defect={"title": "Failure", "priority": "P1"})]

    def tearDown(self):
        self.tmp.cleanup()

    def defects(self):
        return [v for _, v in self.ad._defect_rows()[2]]

    def test_an_edited_batch_with_the_same_id_is_refused(self):
        self.assertEqual(self.ad.apply_batch(self.batch, batch_id="batch_01")["new_defects"], ["BUG-4"])
        before = self.defects()
        edited = copy.deepcopy(self.batch)
        edited[0]["actual_result"] = "typo fixed"
        with self.assertRaises(InvalidBatch) as cm:
            self.ad.apply_batch(edited, batch_id="batch_01")
        self.assertIn("DIFFERENT content", str(cm.exception))
        self.assertEqual(self.defects(), before)
        self.assertEqual(len(self.ad.applied_batches()), 1)

    def test_same_content_with_another_id_changes_nothing(self):
        self.ad.apply_batch(self.batch, batch_id="batch_01")
        r = self.ad.apply_batch(copy.deepcopy(self.batch), batch_id="copy")
        self.assertFalse(r["applied"])
        self.assertEqual(self.defects(), ["BUG-1", "BUG-2", "BUG-3", "BUG-4"])

    def test_a_correction_batch_with_another_id_is_accepted(self):
        self.ad.apply_batch(self.batch, batch_id="batch_01")
        correction = [item("TC-ACCOUNT-001", "Fail", actual_result="typo fixed", bug="BUG-4")]
        r = self.ad.apply_batch(correction, batch_id="batch_01_correction")
        self.assertTrue(r["applied"])
        self.assertEqual(r["new_defects"], [])

    def test_an_old_control_sheet_gets_batch_id(self):
        ws = self.ad.wb.create_sheet(CFG["batch_control"]["sheet"])
        ws.append(["hash", "applied_at", "label", "items", "new_defects"])
        ws.append(["abc", "2026-09-01T10:00:00", "[old]", 1, ""])
        self.ad.apply_batch(self.batch, batch_id="batch_01")
        self.assertEqual(ws.cell(1, 6).value, "batch_id")
        ids = [x.get("batch_id") for x in self.ad.applied_batches()]
        self.assertEqual(ids, [None, "batch_01"])

    def test_a_control_sheet_from_before_the_translation_is_read_and_extended(self):
        # headers written by the adapter before plan 8.11: workbook data, never rewritten
        ws = self.ad.wb.create_sheet(CFG["batch_control"]["sheet"])
        ws.append(["hash", "aplicado_em", "etiqueta", "itens", "defeitos_novos", "lote_id"])
        ws.append(["abc", "2026-09-01T10:00:00", "[old]", 1, "", "lote_01"])
        self.assertEqual(self.ad.applied_batches()[0]["applied_at"], "2026-09-01T10:00:00")
        with self.assertRaises(InvalidBatch):                      # same id, other content
            self.ad.apply_batch(self.batch, batch_id="lote_01")
        self.ad.apply_batch(self.batch, batch_id="batch_02")
        self.assertEqual([c.value for c in ws[1]], ["hash", "aplicado_em", "etiqueta", "itens", "defeitos_novos",
                                                     "lote_id"])
        self.assertEqual([x["batch_id"] for x in self.ad.applied_batches()], ["lote_01", "batch_02"])

    def test_the_identity_of_a_converted_batch_is_its_original_hash(self):
        r = self.ad.apply_batch(self.batch, batch_id="lote_01", identity="hash-of-the-original")
        self.assertTrue(r["applied"])
        self.assertEqual(self.ad.applied_batches()[0]["hash"], "hash-of-the-original")
        again = self.ad.apply_batch(self.batch, batch_id="lote_01", identity="hash-of-the-original")
        self.assertFalse(again["applied"])


class ReadBatch(unittest.TestCase):
    def write(self, content, encoding="utf-8"):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)
        return path

    def test_accepts_a_bom(self):
        path = self.write(json.dumps({"results": [item()]}), encoding="utf-8-sig")
        self.assertEqual(load_batch(path)[0]["tc"], "TC-SHOP-001")

    def test_wrong_format_has_a_clear_message(self):
        path = self.write('{"case": "X", "result": "Fail"}')
        with self.assertRaises(InvalidBatch) as cm:
            load_batch(path)
        self.assertIn('"results"', str(cm.exception))

    def test_a_batch_from_before_the_translation_says_how_to_convert_it(self):
        path = self.write(json.dumps({"resultados": [{"tc": "TC-SHOP-001", "estado": "Pass"}]}))
        with self.assertRaises(InvalidBatch) as cm:
            load_batch(path)
        self.assertIn("migrate_workspace.py", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
