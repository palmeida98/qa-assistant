"""run_log add --proposed: the result of a case that is only PROPOSED in cases/*.json (plan Q4, 10.11.2026).

The case is not in the workbook yet: without --proposed the add is refused (and says the case IS proposed); with it the
result is recorded, marked `case_pending_add`; the apply refuses it, naming the way forward, until the case is added; after
the add_cases the same batch applies. Fictional workspace in a temporary folder.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import run_log as rl
import test_qa_report as base

from adapters.excel import add_cases as addc
from adapters.excel import apply as ap
from adapters.excel.base import InvalidBatch

NOW = datetime(2026, 11, 10, 10, 0, 0)
NEW = "TC-SHOP-099"


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


class Proposed(base.Base):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.ws, "cases"), exist_ok=True)
        with open(os.path.join(self.ws, "cases", "cases_20261110_01.json"), "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": NEW, "title": "A proposed case"}]}, f)

    def add(self, case=NEW, **kw):
        return rl.add(self.ws, "batch_android_proposed", case, "Pass", "observed", environment="test",
                      build="1.0.0 (7)", now=NOW, executed_at="2026-11-10T09:00:00", executed_source="test", **kw)

    def test_a_proposed_case_is_refused_without_the_flag_and_the_message_says_where_it_is_proposed(self):
        with self.assertRaises(rl.UsageError) as cm:
            self.add()
        self.assertIn("IS proposed in cases/cases_20261110_01.json", str(cm.exception))
        self.assertIn("--proposed", str(cm.exception))

    def test_with_the_flag_the_result_is_recorded_and_marked_pending(self):
        path, _ = self.add(proposed=True)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        items = doc.get("results") or doc.get("items") or doc
        item = next(x for x in items if x["tc"] == NEW)
        self.assertEqual(item["case_pending_add"], "cases/cases_20261110_01.json")
        self.assertEqual(item["status"], "Pass")

    def test_the_flag_never_accepts_a_case_nobody_proposed(self):
        with self.assertRaises(rl.UsageError) as cm:
            self.add("TC-SHOP-777", proposed=True)
        self.assertIn("not in the project's library", str(cm.exception))

    def test_the_apply_refuses_it_until_the_case_is_added_and_then_applies(self):
        self.add(proposed=True)
        rel = os.path.join("runs", "batch_android_proposed.json")
        with self.assertRaises(InvalidBatch) as cm:
            quiet(ap.apply, self.ws, rel, recalc=False, now=NOW)
        self.assertIn("case_pending_add", str(cm.exception))
        self.assertIn("add_cases", str(cm.exception))
        code = quiet(addc.add, self.ws, "cases/cases_20261110_01.json", recalc=False, now=NOW)
        self.assertEqual(code, 0)
        quiet(ap.apply, self.ws, rel, recalc=False, now=NOW)          # the same batch now applies


if __name__ == "__main__":
    unittest.main()
