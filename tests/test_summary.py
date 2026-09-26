"""Workbook summary vs direct count (technique-excel.md section 5).

python -m unittest discover -s tests   (from the root of qa-assistant)
The WithLibreOffice class recalculates real formulas and only runs where there is
LibreOffice (locally and in the CI's golden job); the others do not need it:
they write numbers into the summary instead of formulas.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import ConfigError, ExcelAdapter, load_batch
from adapters.excel import apply as apply_mod
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
with open(os.path.join(ROOT, "tests", "golden", "expected.json"), encoding="utf-8") as _f:
    TOTALS = json.load(_f)["summary_totals"]  # written by hand, after the golden's batch
BATCH = os.path.join(ROOT, "tests", "golden", "batch.json")


def has_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except Exception:  # noqa: BLE001 -- any failure = no LibreOffice
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        build(os.path.join(self.tmp.name, CFG["file"]))
        self.ad = ExcelAdapter.from_dict(copy.deepcopy(CFG), base_dir=self.tmp.name)
        self.ad.load()
        self.ad.apply_batch(load_batch(BATCH), label="[t]")
        self.ad.save()

    def tearDown(self):
        self.tmp.cleanup()

    def summary_with_numbers(self, change=None):
        """Replaces the summary's formulas with the expected totals (numbers).
        `change` = {(area, column): value} to introduce errors."""
        self.ad.load()
        ws = self.ad.wb["Summary"]
        head = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            area = row[0].value
            if area in TOTALS:
                for i, col in enumerate(head[1:], start=1):
                    if col in TOTALS[area]:  # only the count columns (not the % run)
                        row[i].value = (change or {}).get((area, col), TOTALS[area][col])
        self.ad.save()


class WithoutLibreOffice(Base):
    def test_without_calculated_values_it_does_not_compare(self):
        vr = self.ad.check_summary()
        self.assertFalse(vr["checked"])
        self.assertIn("recalculate", vr["reason"])

    def test_right_summary(self):
        self.summary_with_numbers()
        self.assertEqual(self.ad.check_summary(), {"checked": True, "problems": [], "reason": None})

    def test_a_wrong_value_points_at_the_cell(self):
        self.summary_with_numbers({("Shop", "Pass"): 0})
        problems = self.ad.check_summary()["problems"]
        self.assertEqual(len(problems), 1)
        self.assertIn("Summary!D2 (area 'Shop', Pass)", problems[0])

    def test_wrong_total(self):
        self.summary_with_numbers({("Total", "Total"): 11})
        self.assertIn("total row, total", self.ad.check_summary()["problems"][0])

    def test_area_missing_from_the_summary(self):
        self.summary_with_numbers()
        self.ad.load()
        self.ad.wb["Summary"].delete_rows(4)  # row of the Admin area
        self.ad.save()
        problems = self.ad.check_summary()["problems"]
        self.assertTrue(any("'Admin' is not in the summary" in p for p in problems), problems)

    def test_without_a_summary_it_does_not_check(self):
        cfg = copy.deepcopy(CFG)
        del cfg["summary"]
        ad = ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name)
        self.assertFalse(ad.check_summary()["checked"])

    def test_invalid_configuration(self):
        cfg = copy.deepcopy(CFG)
        cfg["summary"]["area_column"] = "DoesNotExist"
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name).load()
        cfg = copy.deepcopy(CFG)
        cfg["summary"]["status_columns"] = {"MadeUp": "X"}
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name)


def cfg_with_percentage(**extra):
    cfg = copy.deepcopy(CFG)
    cfg["summary"].update({"percent_column": "% Run", "not_run_status": "To Do", **extra})
    return cfg


def pct(area):
    """% run expected by the rule of methodology 16.3, from the totals written by hand."""
    s = TOTALS[area]
    return (s["Total"] - s["To Do"]) / s["Total"]


class Percentage(Base):
    """% run column: (total - not run) / total, N/A and Blocked count."""

    def with_column(self, values, cfg=None):
        self.summary_with_numbers()
        self.ad.load()
        ws = self.ad.wb["Summary"]
        ws.cell(1, 8).value = "% Run"
        for row in ws.iter_rows(min_row=2):
            if row[0].value in values:
                ws.cell(row[0].row, 8).value = values[row[0].value]
        self.ad.save()
        ad = ExcelAdapter.from_dict(cfg or cfg_with_percentage(), base_dir=self.tmp.name)
        ad.load()
        return ad.check_summary()["problems"]

    def test_right_fraction(self):
        self.assertEqual(self.with_column({a: pct(a) for a in TOTALS}), [])

    def test_right_percentage_points(self):
        self.assertEqual(self.with_column({a: round(100 * pct(a), 6) for a in TOTALS}), [])

    def test_a_formula_that_excludes_the_na_is_detected(self):
        # Admin: 4 cases, 1 To Do, 1 N/A -> right 3/4; excluding the N/A would give (4-1-1)/(4-1)
        values = {a: pct(a) for a in TOTALS}
        values["Admin"] = 2 / 3
        problems = self.with_column(values)
        self.assertEqual(len(problems), 1)
        self.assertIn("area 'Admin', % run", problems[0])

    def test_rounding_only_passes_with_a_tolerance(self):
        values = {a: round(pct(a), 2) for a in TOTALS}  # Total: 9/12 = 0.75 exact; the others too
        values["Shop"] = 0.76  # 3/4 = 0.75 rounded up on purpose
        self.assertEqual(len(self.with_column(values)), 1)
        self.assertEqual(self.with_column(values, cfg_with_percentage(percent_tolerance=1.5)), [])

    def test_not_run_status_required_and_valid(self):
        cfg = cfg_with_percentage()
        del cfg["summary"]["not_run_status"]
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg)
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg_with_percentage(not_run_status="MadeUp"))


@unittest.skipUnless(has_libreoffice(), "needs LibreOffice (runs in the CI's golden job)")
class WithLibreOffice(Base):
    def test_real_formulas_match_after_the_recalculation(self):
        self.assertTrue(self.ad.recalc()["ok"])
        self.assertEqual(self.ad.check_summary()["problems"], [])

    def test_a_formula_counting_the_wrong_column(self):
        # the pilot's case: the formula counts another column and gives a plausible but wrong number
        self.ad.wb["Summary"]["D2"] = "=COUNTIF('Shop'!A2:A100,\"Pass\")"
        self.ad.save()
        self.assertTrue(self.ad.recalc()["ok"])
        problems = self.ad.check_summary()["problems"]
        self.assertTrue(any("Summary!D2" in p for p in problems), problems)

    def test_a_case_outside_the_fixed_range(self):
        ws = self.ad.wb["Shop"]
        ws.cell(101, 1).value = "TC-SHOP-099"  # the fixture's formulas stop at row 100
        ws.cell(101, 3).value = "To Do"
        self.ad.save()
        self.assertTrue(self.ad.recalc()["ok"])
        problems = self.ad.check_summary()["problems"]
        self.assertTrue(any("area 'Shop', total" in p for p in problems), problems)

    def test_a_percentage_formula_that_excludes_the_na(self):
        ws = self.ad.wb["Summary"]
        ws.cell(1, 8).value = "% Run"
        for r in range(2, 6):
            ws.cell(r, 8).value = f"=IFERROR((B{r}-C{r}-G{r})/(B{r}-G{r}),0)"
        self.ad.save()
        self.assertTrue(self.ad.recalc()["ok"])
        ad = ExcelAdapter.from_dict(cfg_with_percentage(), base_dir=self.tmp.name)
        ad.load()
        problems = ad.check_summary()["problems"]
        self.assertTrue(any("area 'Admin', % run" in p for p in problems), problems)
        self.assertFalse(any("area 'Shop'" in p for p in problems), problems)  # Shop has no N/A

    def test_apply_exits_with_5(self):
        ws_dir = self.tmp.name
        self.ad.wb["Summary"]["D2"] = "=COUNTIF('Shop'!A2:A100,\"Pass\")"
        self.ad.save()
        with open(os.path.join(ws_dir, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"tracker": "excel", "excel": CFG}, f, allow_unicode=True)
        batch = os.path.join(ws_dir, "batch_extra.json")
        with open(batch, "w", encoding="utf-8") as f:
            json.dump({"results": [{"tc": "TC-SHOP-003", "status": "Pass", "actual_result": "ok"}]}, f)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = apply_mod.main([ws_dir, batch])
        self.assertEqual(rc, 5, out.getvalue())
        self.assertIn("fix the summary's FORMULAS", out.getvalue())


if __name__ == "__main__":
    unittest.main()
