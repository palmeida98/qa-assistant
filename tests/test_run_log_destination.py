"""Q1 (phase 19): `run_log add` and `mode` refuse, before writing, a batch whose workbook is ambiguous or contradictory
in a project with several workbooks; a project with a single workbook and the reading of old run logs are unchanged.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import run_log as rl
import test_qa_report as base

NOW = datetime(2026, 11, 10, 10, 0, 0)


class Destination(base.Base):
    def setUp(self):
        super().setUp()
        f = self.proj["excel"]["file"]
        self.proj["excel"]["workbooks"] = {"android": {"file": f, "platform": "android", "batches": ["batch_android_*"]},
                                           "ios": {"file": f, "platform": "ios", "batches": ["batch_ios_*"]}}
        self.write_project()

    def add(self, name, **kw):
        return rl.add(self.ws, name, "TC-SHOP-001", "Pass", "observed", environment="test", build="1.0.0 (7)",
                      now=NOW, executed_at="2026-11-10T09:00:00", executed_source="test", **kw)

    def text(self, name):
        with open(self.path(name), encoding="utf-8") as f:
            return f.read()

    def path(self, name):
        return os.path.join(self.ws, "runs", f"{name}.json")

    def test_a_name_without_a_platform_is_refused_before_anything_is_written(self):
        with self.assertRaises(rl.UsageError) as cm:
            self.add("batch_both_01")
        self.assertIn("nothing was written", str(cm.exception))
        self.assertIn("no platform recognised", str(cm.exception))
        self.assertFalse(os.path.exists(self.path("batch_both_01")))

    def test_a_run_block_that_contradicts_the_name_is_refused(self):
        self.batch("batch_android_02", [], run={"platform": "ios", "environment": "test", "build": "1.0.0 (7)"})
        before = self.text("batch_android_02")
        with self.assertRaises(rl.UsageError) as cm:
            self.add("batch_android_02")
        self.assertIn("do not agree", str(cm.exception))
        self.assertEqual(self.text("batch_android_02"), before)

    def test_mode_is_refused_for_an_ambiguous_batch_and_nothing_is_created(self):
        with self.assertRaises(rl.UsageError):
            rl.set_mode(self.ws, "batch_both_01", "conversational: generated answers")
        self.assertFalse(os.path.exists(self.path("batch_both_01")))

    def test_a_clear_destination_still_works(self):
        path, _ = self.add("batch_android_03")
        self.assertTrue(os.path.isfile(path))
        rl.set_mode(self.ws, "batch_ios_03", "conversational: generated answers")

    def test_a_declared_platform_makes_an_unpatterned_name_unambiguous(self):
        self.batch("batch_x_01", [], run={"platform": "android", "environment": "test", "build": "1.0.0 (7)"})
        # the name matches no pattern but the platform has its own workbook: still a stop (the name must say it too)
        with self.assertRaises(rl.UsageError):
            self.add("batch_x_01")

    def test_a_project_with_a_single_workbook_is_not_affected(self):
        del self.proj["excel"]["workbooks"]
        self.write_project()
        path, _ = self.add("batch_both_01")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["results"]), 1)


if __name__ == "__main__":
    unittest.main()
