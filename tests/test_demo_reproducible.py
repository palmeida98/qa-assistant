"""Reproducible workbook (plan 7.6; technique-excel.md section 1, "Date of the application").

python -m unittest discover -s tests   (from the root of qa-assistant)
- Now: with a fixed instant (apply --now), applying the same batch to two copies
  gives the same cells, and the dates written are that instant's. No LibreOffice
  (--no-recalc).
- Demo: rebuilding the demo in a temporary folder (builder + batches of runs/)
  gives exactly the cells of the versioned workbook. Needs LibreOffice: in the CI
  it runs in the golden job; in the tests job it is skipped.
"""

import contextlib
import copy
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo")
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel.apply import main as apply
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
BATCH = os.path.join(ROOT, "tests", "golden", "batch.json")
NOW = "2026-01-02T03:04:05"


def has_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except FileNotFoundError:
        return False


def cells(path: str) -> dict:
    """{sheet: {coordinate: value}} of every sheet (formulas as text)."""
    wb = openpyxl.load_workbook(path, data_only=False)
    return {ws.title: {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
            for ws in wb.worksheets}


class Now(unittest.TestCase):
    def workspace(self, tmp: str) -> str:
        cfg = copy.deepcopy(CFG)
        with open(os.path.join(tmp, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"excel": cfg}, f, allow_unicode=True)
        build(os.path.join(tmp, cfg["file"]))
        return os.path.join(tmp, cfg["file"])

    def apply(self, tmp: str) -> dict:
        path = self.workspace(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = apply([tmp, BATCH, "--no-recalc", "--now", NOW])
        self.assertEqual(rc, 0)
        return cells(path)

    def test_same_instant_same_cells(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(self.apply(a), self.apply(b))

    def test_dates_written_are_the_instants(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.apply(tmp)
        control = c[CFG.get("batch_control", {}).get("sheet") or "_qa_batches"]
        self.assertIn(NOW, control.values())                         # applied_at
        defects = c[CFG["defects"]["sheet"]]
        self.assertIn(NOW[:10], defects.values())                    # report date of the new defects
        every = [v for s in c.values() for v in s.values() if isinstance(v, str)]
        self.assertTrue(any(f"[{NOW[:10]}, batch]" in v for v in every))  # default label


@unittest.skipUnless(has_libreoffice(), "needs LibreOffice (runs in the CI's golden job)")
class Demo(unittest.TestCase):
    def test_the_rebuild_gives_the_cells_of_the_versioned_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(os.path.join(DEMO, "project.yaml"), tmp)
            r = subprocess.run([sys.executable, os.path.join(DEMO, "rebuild_workbook.py"), tmp],
                               capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=False,
                               timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            new = cells(os.path.join(tmp, "workbook", "demo-plan.xlsx"))
        versioned = cells(os.path.join(DEMO, "workbook", "demo-plan.xlsx"))
        self.assertEqual(sorted(new), sorted(versioned))
        for sheet in versioned:
            with self.subTest(sheet=sheet):
                different = {k: (versioned[sheet].get(k), new[sheet].get(k))
                             for k in set(versioned[sheet]) | set(new[sheet])
                             if versioned[sheet].get(k) != new[sheet].get(k)}
                self.assertEqual(different, {}, "rebuild the demo and version the workbook again "
                                                "(python examples/demo/rebuild_workbook.py)")


if __name__ == "__main__":
    unittest.main()
