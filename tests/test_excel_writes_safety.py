"""Excel writes with the guarantees the apply already had (phase 17.3): `add_cases --expect` bound to its dry run and to
the workspace's lock, the final recalculation put in place in one step, and `apply --keep-previous` (the result a case
already had stays in its notes). adapters/excel/add_cases.py, recalc.py, apply.py and base.py.

Everything is fictional (the golden fixture's workbook, a temporary workspace); LibreOffice is never run.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
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
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import ExcelAdapter, InvalidBatch, recalc
from adapters.excel import add_cases as ac
from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 8, 19, 43, 0)
NEW_CASES = {"cases": [{"id": "TC-SHOP-005", "title": "Remove item from cart"},
                       {"id": "TC-ADMIN-005", "title": "Archive users"}]}


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.xlsx = os.path.join(self.ws, CFG["file"])
        build(self.xlsx)
        self.proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel",
                     "excel": copy.deepcopy(CFG)}
        self.save_project()
        os.makedirs(os.path.join(self.ws, "cases"))
        self.write_cases()

    def tearDown(self):
        self.tmp.cleanup()

    def save_project(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f)

    def write_cases(self, data=NEW_CASES, name="cases_01.json"):
        with open(os.path.join(self.ws, "cases", name), "w", encoding="utf-8") as f:
            json.dump(data, f)
        return "cases/" + name

    def sha(self, path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def run_cli(self, module, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = module.main([self.ws, *args])
        return code, out.getvalue()

    def ids(self, sheet="Shop"):
        ws = openpyxl.load_workbook(self.xlsx)[sheet]
        return [ws.cell(r, 1).value for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value]


class AddCasesToken(Workspace):
    def dry(self, *extra):
        code, out = self.run_cli(ac, "cases/cases_01.json", "--dry-run", "--detail", "--no-recalc", *extra)
        self.assertEqual(code, 0, out)
        token = next(line.split()[2] for line in out.splitlines() if line.startswith("Preview token:"))
        return token, out

    def add(self, token, *extra):
        return self.run_cli(ac, "cases/cases_01.json", "--no-recalc", "--expect", token, *extra)

    def test_the_dry_run_prints_a_token_and_writes_nothing(self):
        before = self.sha(self.xlsx)
        token, out = self.dry()
        self.assertTrue(token.startswith("v1-"))
        self.assertIn(f"add with --expect {token}", out)
        self.assertEqual(self.sha(self.xlsx), before)

    def test_the_add_with_the_token_writes_and_a_repeat_with_it_is_harmless(self):
        token, _ = self.dry()
        code, out = self.add(token)
        self.assertEqual(code, 0, out)
        self.assertIn("TC-SHOP-005", self.ids())
        after = self.sha(self.xlsx)
        code, out = self.add(token)                         # the workbook changed, but the cases are already added
        self.assertEqual(code, 0, out)
        self.assertIn("cases already added", out)
        self.assertEqual(self.sha(self.xlsx), after)

    def test_a_changed_cases_file_is_refused_and_nothing_is_written(self):
        token, _ = self.dry()
        before = self.sha(self.xlsx)
        self.write_cases({"cases": [{"id": "TC-SHOP-005", "title": "Remove item from the cart"}]})
        code, out = self.add(token)
        self.assertEqual(code, 4)
        self.assertIn("out of date", out)
        self.assertIn("Nothing was written", out)
        self.assertEqual(self.sha(self.xlsx), before)

    def test_a_changed_workbook_is_refused(self):
        token, _ = self.dry()
        wb = openpyxl.load_workbook(self.xlsx)
        wb["Shop"]["B2"] = "edited by someone else"
        wb.save(self.xlsx)
        before = self.sha(self.xlsx)
        code, out = self.add(token)
        self.assertEqual(code, 4)
        self.assertIn("out of date", out)
        self.assertEqual(self.sha(self.xlsx), before)
        self.assertNotIn("TC-SHOP-005", self.ids())

    def test_a_changed_project_file_is_refused(self):
        token, _ = self.dry()
        self.proj["project"] = "another-name"
        self.save_project()
        before = self.sha(self.xlsx)
        self.assertEqual(self.add(token)[0], 4)
        self.assertEqual(self.sha(self.xlsx), before)

    def test_another_option_is_another_token(self):
        token, _ = self.dry()
        code, out = self.run_cli(ac, "cases/cases_01.json", "--expect", token)        # recalculation on: not the preview
        self.assertEqual(code, 4)
        self.assertIn("out of date", out)
        code, out = self.add(token, "--label", "[another label]")
        self.assertEqual(code, 4)

    def test_a_token_of_another_command_and_expect_with_the_dry_run_are_refused(self):
        before = self.sha(self.xlsx)
        code, out = self.add("v2-0123456789abcdef")
        self.assertEqual(code, 4)
        self.assertIn("not a token of this command", out)
        code, out = self.run_cli(ac, "cases/cases_01.json", "--dry-run", "--expect", "v1-x")
        self.assertEqual(code, 4)
        self.assertIn("goes with the real add", out)
        self.assertEqual(self.sha(self.xlsx), before)

    def test_without_a_token_the_add_works_as_before(self):
        code, out = self.run_cli(ac, "cases/cases_01.json", "--no-recalc")
        self.assertEqual(code, 0, out)
        self.assertIn("TC-ADMIN-005", self.ids("Admin"))

    def test_a_token_does_not_authorise_cases_that_are_invalid_now(self):
        token, _ = self.dry()
        self.run_cli(ac, "cases/cases_01.json", "--no-recalc")                         # added without it
        self.write_cases({"cases": [{"id": "TC-SHOP-005", "title": "Remove item from cart"}]}, "cases_02.json")
        code, _ = self.run_cli(ac, "cases/cases_02.json", "--no-recalc", "--expect", token)
        self.assertEqual(code, 4)

    def test_another_writer_holding_the_workspace_blocks_the_add_and_the_dry_run_does_not_need_it(self):
        import workspace_kit as kit
        token, _ = self.dry()
        before = self.sha(self.xlsx)
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, out = self.add(token)
            self.assertEqual(code, 4)
            self.assertIn("Nothing was written", out)
            self.assertEqual(self.dry()[0], token)                  # a dry run reads only
        self.assertEqual(self.sha(self.xlsx), before)


class RecalcInPlace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "a.xlsx")
        with open(self.path, "wb") as f:
            f.write(b"original")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def leftovers(self):
        return [n for n in os.listdir(self.tmp.name) if n != "a.xlsx"]

    @staticmethod
    def recalculated(p, timeout):
        with open(p, "wb") as f:
            f.write(b"recalculated")

    def test_the_recalculated_file_replaces_the_workbook_and_leaves_nothing_behind(self):
        with mock.patch.object(recalc, "_recalc_libreoffice", self.recalculated):
            r = recalc.recalc_safe(self.path)
        self.assertTrue(r["ok"])
        self.assertEqual((self.read(), self.leftovers()), (b"recalculated", []))

    def test_a_replace_that_fails_leaves_the_original_and_no_half_copy(self):
        with mock.patch.object(recalc, "_recalc_libreoffice", self.recalculated), \
                mock.patch.object(recalc.os, "replace", side_effect=OSError("disk error")):
            r = recalc.recalc_safe(self.path)
        self.assertFalse(r["ok"])
        self.assertIn("could not be put in place", r["error"])
        self.assertEqual((self.read(), self.leftovers()), (b"original", []))

    def test_a_copy_that_stops_in_the_middle_leaves_the_original_and_no_half_copy(self):
        real = recalc.shutil.copy
        calls = []

        def copy_then_fail(src, dst, *a, **k):
            calls.append(dst)
            if len(calls) == 1:                         # the first copy: the workbook to the temporary folder
                return real(src, dst, *a, **k)
            with open(dst, "wb") as f:                  # the second: half of the recalculated file, then a failure
                f.write(b"recalc")
            raise OSError("copy interrupted")

        with mock.patch.object(recalc, "_recalc_libreoffice", self.recalculated), \
                mock.patch.object(recalc.shutil, "copy", copy_then_fail):
            r = recalc.recalc_safe(self.path)
        self.assertFalse(r["ok"])
        self.assertEqual((self.read(), self.leftovers()), (b"original", []))


class KeepPrevious(Workspace):
    """The result a case already had stays in its notes when a batch replaces it."""

    def batch(self, name, results):
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        path = os.path.join(self.ws, "runs", name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": {"environment": "test", "build": "1.4.0 (140)"}, "results": results}, f)
        return path

    def result(self, status, text, case="TC-SHOP-001"):
        return {"tc": case, "status": status, "actual_result": text, "ts": "2026-11-08T19:20:00"}

    def apply(self, path, *extra):
        return self.run_cli(ap, path, "--no-recalc", "--now", NOW.isoformat(), *extra)

    def cells(self, case="TC-SHOP-001"):
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        for rows in ad.case_rows().values():
            for row in rows:
                if str(row.get("ID")).strip() == case:
                    return row
        raise AssertionError(case)

    def first_result(self):
        code, out = self.apply(self.batch("batch_a_01", [self.result("Fail", "first result")]))
        self.assertEqual(code, 0, out)

    def test_the_previous_status_and_result_are_kept_in_the_notes(self):
        self.first_result()
        code, out = self.apply(self.batch("batch_a_02", [self.result("Pass", "second result")]), "--keep-previous")
        self.assertEqual(code, 0, out)
        row = self.cells()
        self.assertEqual((row["Status"], row["Actual Result"]), ("Pass", "second result"))
        self.assertIn("Previous result (Fail): first result", row["Notes"])

    def test_without_the_option_the_previous_result_is_not_kept(self):
        self.first_result()
        self.apply(self.batch("batch_a_02", [self.result("Pass", "second result")]))
        self.assertNotIn("Previous result", str(self.cells()["Notes"]))

    def test_nothing_is_kept_when_there_was_no_result_or_it_is_the_same(self):
        path = self.batch("batch_a_01", [self.result("Pass", "same text", "TC-SHOP-002")])
        self.assertEqual(self.apply(path, "--keep-previous")[0], 0)                    # no previous result
        self.apply(self.batch("batch_a_02", [self.result("Pass", "same text", "TC-SHOP-002")]), "--keep-previous")
        self.assertNotIn("Previous result", str(self.cells("TC-SHOP-002")["Notes"]))

    def test_it_needs_the_notes_column_and_refuses_before_writing(self):
        self.proj["excel"]["cases"]["columns"].pop("notes")
        self.save_project()
        path = self.batch("batch_a_01", [self.result("Pass", "x")])
        before = self.sha(self.xlsx)
        code, out = self.apply(path, "--keep-previous")
        self.assertEqual(code, 4)
        self.assertIn("needs the notes column", out)
        self.assertEqual(self.sha(self.xlsx), before)
        res = ap.dry_run(self.ws, path, recalc=False, now=NOW, keep_previous=True)
        self.assertTrue(any("notes column" in p for p in res["problems"]))

    def test_the_option_is_part_of_the_token_and_without_it_the_token_is_as_before(self):
        path = self.batch("batch_a_01", [self.result("Pass", "x")])
        plain = ap.preview_token(self.ws, path, now=NOW)
        self.assertEqual(ap.preview_token(self.ws, path, now=NOW, keep_previous=False), plain)
        self.assertNotEqual(ap.preview_token(self.ws, path, now=NOW, keep_previous=True), plain)

    def test_the_dry_run_shows_the_option_and_the_token_authorises_the_apply(self):
        self.first_result()
        path = self.batch("batch_a_02", [self.result("Pass", "second result")])
        code, out = self.run_cli(ap, path, "--dry-run", "--detail", "--no-recalc", "--now", NOW.isoformat(), "--keep-previous")
        self.assertEqual(code, 0, out)
        self.assertIn("the previous result of each case kept in its notes", out)
        self.assertTrue(any(line.lstrip().startswith("Shop!") and "Previous result" in line for line in out.splitlines()),
                        out)
        token = next(line.split()[2] for line in out.splitlines() if line.startswith("Preview token:"))
        code, out = self.apply(path, "--keep-previous", "--expect", token)
        self.assertEqual(code, 0, out)
        code, out = self.apply(self.batch("batch_a_03", [self.result("Fail", "third")]), "--expect", token)
        self.assertEqual(code, 4)                                       # another batch, and no option: another token

    def test_the_adapter_refuses_a_missing_notes_column_with_a_problem_list(self):
        cfg = copy.deepcopy(CFG)
        cfg["cases"]["columns"].pop("notes")
        ad = ExcelAdapter.from_dict(cfg, base_dir=self.ws)
        ad.load()
        with self.assertRaises(InvalidBatch) as ctx:
            ad.apply_batch([self.result("Pass", "x")], keep_previous=True)
        self.assertIn("--keep-previous", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
