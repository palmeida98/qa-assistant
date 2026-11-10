"""add_columns: traceability columns in the case sheets (plan L1, 10.11.2026), on fictional workspaces.

The command adds headers only (never a case, formula or row), refuses a header that exists, is guarded by a dry run with
a token, is recorded in the control sheet, handles several workbooks with one combined token, and its result is then
used by add_cases through the new case-model fields pbi_id / qa_task. No LibreOffice needed (never recalculates).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import add_cases as addc
from adapters.excel import add_columns as cols

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)

IOS_FILE = "ios-plan.xlsx"
NOW = "2026-11-10T10:00:00"
COLUMNS = {"columns": [{"header": "PBI (ID)", "field": "pbi_id"}, {"header": "Task QA", "field": "qa_task"},
                       {"header": "Origin", "field": "origin"}]}


class Base(unittest.TestCase):
    two_workbooks = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        cfg = copy.deepcopy(CFG)
        if self.two_workbooks:
            shutil.copy(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, IOS_FILE))
            cfg["workbooks"] = {"ios": {"file": IOS_FILE, "batches": ["batch_ios*"]}}
        self.cfg = cfg
        self.save_project(cfg)
        os.makedirs(os.path.join(self.ws, "cases"))
        os.environ.pop("QA_WORKBOOK", None)

    def tearDown(self):
        self.tmp.cleanup()

    def save_project(self, cfg):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"tracker": "excel", "excel": cfg}, f)

    def write(self, name, data):
        with open(os.path.join(self.ws, "cases", name), "w", encoding="utf-8") as f:
            json.dump(data, f)
        return f"cases/{name}"

    def run_tool(self, mod, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main([self.ws, *args, "--no-recalc", "--now", NOW])
        return code, out.getvalue() + err.getvalue()

    def headers(self, file, sheet="Shop"):
        ws = openpyxl.load_workbook(os.path.join(self.ws, file))[sheet]
        return [c.value for c in ws[1] if c.value not in (None, "")]

    def snapshot(self, file):
        wb = openpyxl.load_workbook(os.path.join(self.ws, file))
        return {ws.title: [[c.value for c in row] for row in ws.iter_rows(min_row=2)] for ws in wb.worksheets
                if not ws.title.startswith("_")}

    @staticmethod
    def trim(row):
        row = list(row)
        while row and row[-1] is None:
            row.pop()
        return row

    def token(self, out):
        m = re.search(r"(?:Preview|Combined) token: (v1-[0-9a-f]+)", out) or re.search(r"token (v1-[0-9a-f]+)", out)
        self.assertTrue(m, out)
        return m.group(1)


class AddColumns(Base):
    def test_a_dry_run_writes_nothing_and_a_real_run_needs_its_token(self):
        f = self.write("columns_01.json", COLUMNS)
        before_headers, before_rows = self.headers(CFG["file"]), self.snapshot(CFG["file"])
        code, out = self.run_tool(cols, f, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.headers(CFG["file"]), before_headers)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "cases", "columns_01.dryrun.md")))
        self.assertIn("cases.fields", out)
        code, out2 = self.run_tool(cols, f)                                   # no token
        self.assertEqual(code, 4, out2)
        code, out2 = self.run_tool(cols, f, "--expect", "v1-0000000000000000")   # stale token
        self.assertEqual(code, 4, out2)
        self.assertEqual(self.headers(CFG["file"]), before_headers)
        code, out3 = self.run_tool(cols, f, "--expect", self.token(out))
        self.assertEqual(code, 0, out3)
        self.assertEqual(self.headers(CFG["file"])[-3:], ["PBI (ID)", "Task QA", "Origin"])
        after = {k: [self.trim(r) for r in v] for k, v in self.snapshot(CFG["file"]).items()}
        self.assertEqual(after, {k: [self.trim(r) for r in v] for k, v in before_rows.items()})   # no case/formula/row changed

    def test_the_same_columns_again_change_nothing_and_an_existing_header_is_refused(self):
        f = self.write("columns_01.json", COLUMNS)
        _, out = self.run_tool(cols, f, "--dry-run")
        self.run_tool(cols, f, "--expect", self.token(out))
        code, out = self.run_tool(cols, f, "--expect", "v1-whatever")
        self.assertEqual(code, 0, out)
        self.assertIn("already added", out)
        self.assertEqual(self.headers(CFG["file"]).count("Task QA"), 1)
        again = self.write("columns_02.json", {"columns": [{"header": "task qa", "field": "qa_task"}]})
        code, out = self.run_tool(cols, again, "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertIn("already exist", out)

    def test_the_file_is_validated(self):
        for bad, fragment in (({"columns": []}, "expected"),
                              ({"columns": [{"header": "A", "field": "nonsense"}]}, "not a field of the case model"),
                              ({"columns": [{"header": "A", "field": "pbi_id"}, {"header": "a", "field": "qa_task"}]}, "twice"),
                              ({"columns": [{"field": "pbi_id"}]}, "'header' is missing")):
            code, out = self.run_tool(cols, self.write("bad.json", bad), "--dry-run")
            self.assertEqual(code, 4, out)
            self.assertIn(fragment, out)

    def test_the_new_columns_are_recorded_in_the_control_sheet_like_a_batch(self):
        f = self.write("columns_01.json", COLUMNS)
        _, out = self.run_tool(cols, f, "--dry-run")
        self.run_tool(cols, f, "--expect", self.token(out))
        ws = openpyxl.load_workbook(os.path.join(self.ws, CFG["file"]))["_qa_batches"]
        rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
        self.assertTrue(any("columns_01" in [str(x) for x in r] for r in rows), rows)

    def test_then_add_cases_fills_the_new_columns_once_project_yaml_maps_them(self):
        f = self.write("columns_01.json", COLUMNS)
        _, out = self.run_tool(cols, f, "--dry-run")
        self.run_tool(cols, f, "--expect", self.token(out))
        cfg = copy.deepcopy(self.cfg)
        cfg["cases"]["fields"].update({"pbi_id": "PBI (ID)", "qa_task": "Task QA", "origin": "Origin"})
        self.save_project(cfg)
        cf = self.write("cases_x.json", {"cases": [{"id": "TC-SHOP-005", "title": "Remove item from cart",
                                                    "pbi_id": "1001", "qa_task": "2002", "origin": "AC 1"}]})
        _, out = self.run_tool(addc, cf, "--dry-run")
        code, out = self.run_tool(addc, cf, "--expect", self.token(out))
        self.assertEqual(code, 0, out)
        ws = openpyxl.load_workbook(os.path.join(self.ws, CFG["file"]))["Shop"]
        hdr = {c.value: c.column for c in ws[1] if c.value}
        row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "TC-SHOP-005")
        self.assertEqual([str(ws.cell(row, hdr[h]).value) for h in ("PBI (ID)", "Task QA", "Origin")], ["1001", "2002", "AC 1"])


class TwoWorkbooks(Base):
    two_workbooks = True

    def test_both_adds_to_every_workbook_with_one_combined_token(self):
        f = self.write("columns_01.json", COLUMNS)
        code, out = self.run_tool(cols, f, "--platform", "both", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("Combined token", out)
        token = self.token(out)
        code, out2 = self.run_tool(cols, f, "--platform", "both", "--expect", "v1-0000000000000000")
        self.assertEqual(code, 4, out2)
        for file in (CFG["file"], IOS_FILE):
            self.assertNotIn("Task QA", self.headers(file))
        code, out3 = self.run_tool(cols, f, "--platform", "both", "--expect", token)
        self.assertEqual(code, 0, out3)
        for file in (CFG["file"], IOS_FILE):
            self.assertIn("Task QA", self.headers(file))

    def test_a_header_that_exists_in_one_workbook_stops_both(self):
        ios = self.write("c0.json", {"columns": [{"header": "Task QA", "field": "qa_task"}]})
        _, out = self.run_tool(cols, ios, "--platform", "ios", "--dry-run")
        self.run_tool(cols, ios, "--platform", "ios", "--expect", self.token(out))
        code, out = self.run_tool(cols, self.write("columns_01.json", COLUMNS), "--platform", "both", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertNotIn("PBI (ID)", self.headers(CFG["file"]))


if __name__ == "__main__":
    unittest.main()
