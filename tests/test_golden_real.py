"""Real golden (scripts/golden_check.py --dir; methodology section 15; plan 7.3).

python -m unittest discover -s tests   (from the root of qa-assistant)
With fictional data (fixture and batch of the fictional golden), in a temporary
workspace: the mechanism must pass when after.xlsx matches, fail pointing at the
cell when it does not, and refuse incomplete folders. A batch from before the
translation (plan 8.11) is converted in memory and keeps its identity. Needs
LibreOffice (apply recalculates): in the CI it runs in the golden job.
"""

import contextlib
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
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import golden_check
from build_fixture import build
from migrate_workspace import convert_batch

from adapters.excel import ExcelAdapter, batch_hash
from adapters.excel.apply import apply
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
BATCH = os.path.join(ROOT, "tests", "golden", "batch.json")
NOW = "2026-01-02T03:04:05"
BATCH_NAME = "batch_20260102_01.json"
# the golden's batch as it was written before the translation: same content, the old keys
with open(BATCH, encoding="utf-8") as _f:
    _new = json.load(_f)["results"]
OLD_BATCH = {"resultados": [
    {"tc": x["tc"], "estado": x["status"], "resultado_obtido": x["actual_result"],
     "defeito_novo": None if not x.get("new_defect") else
     {{"severity": "severidade", "priority": "prioridade"}.get(k, k): v for k, v in x["new_defect"].items()}}
    for x in _new]}


def has_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except FileNotFoundError:
        return False


def workspace(folder: str) -> None:
    with open(os.path.join(folder, "project.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"project": "demo-shop", "excel": CFG}, f, allow_unicode=True)


def make_after(folder: str, batch_file: str) -> str:
    """The stored "after": the workbook after the original checkpoint, with the same instant."""
    workspace(folder)
    before = os.path.join(folder, "before.xlsx")
    build(before)
    shutil.copy(before, os.path.join(folder, CFG["file"]))
    with contextlib.redirect_stdout(io.StringIO()):
        rc = apply(folder, batch_file, now=golden_check.datetime.fromisoformat(NOW))
    assert rc == 0, rc
    return os.path.join(folder, CFG["file"])


@unittest.skipUnless(has_libreoffice(), "needs LibreOffice (runs in the CI's golden job)")
class RealGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin = tempfile.mkdtemp()
        shutil.copy(BATCH, os.path.join(cls.origin, BATCH_NAME))
        cls.after = make_after(cls.origin, os.path.join(cls.origin, BATCH_NAME))
        cls.before = os.path.join(cls.origin, "before.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.origin)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        workspace(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def golden(self, name="batch01", now=NOW) -> str:
        folder = os.path.join(self.ws, "golden", name)
        os.makedirs(folder)
        shutil.copy(self.before, os.path.join(folder, "before.xlsx"))
        shutil.copy(self.after, os.path.join(folder, "after.xlsx"))
        shutil.copy(BATCH, os.path.join(folder, BATCH_NAME))
        with open(os.path.join(folder, "golden.yaml"), "w", encoding="utf-8") as f:
            f.write(f"now: '{now}'\n" if now else "label: x\n")
        return folder

    def run_it(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = golden_check.main(list(args))
        return rc, out.getvalue()

    def test_equal_after_passes(self):
        folder = self.golden()
        rc, out = self.run_it("--dir", folder)
        self.assertEqual(rc, 0, out)
        self.assertIn("REAL GOLDEN OK", out)

    def test_a_different_cell_fails_and_points_at_the_cell(self):
        folder = self.golden()
        wb = openpyxl.load_workbook(os.path.join(folder, "after.xlsx"))
        ws = wb[CFG["defects"]["sheet"]]
        ws["A2"] = "BUG-999"
        wb.save(os.path.join(folder, "after.xlsx"))
        rc, out = self.run_it("--dir", folder)
        self.assertEqual(rc, 1)
        self.assertIn(f"{CFG['defects']['sheet']}!A2: expected ('BUG-999'", out)

    def test_another_instant_changes_the_dates_written(self):
        folder = self.golden(now="2026-03-04T05:06:07")
        rc, out = self.run_it("--dir", folder)
        self.assertEqual(rc, 1)
        self.assertIn("2026-03-04", out)

    def test_folder_with_several_goldens_and_environment_variable(self):
        self.golden("a")
        self.golden("b")
        with mock.patch.dict(os.environ, {"QA_GOLDEN_DIR": os.path.join(self.ws, "golden")}):
            rc, out = self.run_it()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("REAL GOLDEN OK"), 2)

    def test_incomplete_folder_or_outside_a_workspace_exits_with_2(self):
        folder = self.golden(now=None)
        rc, out = self.run_it("--dir", folder)
        self.assertEqual(rc, 2)
        self.assertIn("'now' is missing", out)
        os.remove(os.path.join(self.ws, "project.yaml"))
        with open(os.path.join(folder, "golden.yaml"), "w", encoding="utf-8") as f:
            f.write(f"now: '{NOW}'\n")
        rc, out = self.run_it("--dir", folder)
        self.assertEqual(rc, 2)
        self.assertIn("no project.yaml", out)
        rc, out = self.run_it("--dir", self.ws)
        self.assertEqual(rc, 2)


@unittest.skipUnless(has_libreoffice(), "needs LibreOffice (runs in the CI's golden job)")
class RealGoldenFromBeforeTheTranslation(unittest.TestCase):
    """A workspace converted by migrate_workspace.py: its golden keeps the old batch (lote_*.json, old keys),
    whose hash is the batch's identity recorded in the workbook's control sheet."""

    def test_the_old_batch_is_converted_in_memory_and_keeps_its_identity(self):
        with tempfile.TemporaryDirectory() as origin, tempfile.TemporaryDirectory() as ws:
            old_name = "lote_20260102_01.json"
            converted = os.path.join(origin, old_name)
            with open(converted, "w", encoding="utf-8") as f:
                json.dump(convert_batch(OLD_BATCH), f, ensure_ascii=False)
            after = make_after(origin, converted)
            ad = ExcelAdapter.from_project(origin)
            ad.load()
            self.assertEqual([b["hash"] for b in ad.applied_batches()], [batch_hash(OLD_BATCH["resultados"])])

            workspace(ws)
            folder = os.path.join(ws, "golden", "old")
            os.makedirs(folder)
            shutil.copy(os.path.join(origin, "before.xlsx"), os.path.join(folder, "before.xlsx"))
            shutil.copy(after, os.path.join(folder, "after.xlsx"))
            with open(os.path.join(folder, old_name), "w", encoding="utf-8") as f:
                json.dump(OLD_BATCH, f, ensure_ascii=False)
            with open(os.path.join(folder, "golden.yaml"), "w", encoding="utf-8") as f:
                f.write(f"agora: '{NOW}'\n")              # old keys are read too
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = golden_check.main_real(folder)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("REAL GOLDEN OK", out.getvalue())


if __name__ == "__main__":
    unittest.main()
