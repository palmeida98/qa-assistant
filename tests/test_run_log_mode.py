"""The run log's `mode` (docs/pilot-improvements-2026-11-07.md B; procedures/run-mobile.md step 2), on a fictional
workspace: written by `run_log.py mode` or `add --mode` before the first result, validated, first key of the file;
the same value again is no change; another value, or a mode for a batch that already has results without one, is
refused; an applied batch is never touched; old run logs without a mode are read as they are and never given one.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import qa_report
import qa_ui_kit as kit
import run_log as rl

from adapters.excel import apply as ap


class Mode(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rl.main([self.ws, *args])
        return rc, out.getvalue() + err.getvalue()

    def doc(self, name="batch_m1"):
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_mode_is_written_before_the_first_result_as_the_first_key(self):
        rc, out = self.cli("mode", "m1", "Conversational: generated answers")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.doc(), {"mode": "conversational: generated answers", "results": []})
        rc, out = self.cli("add", "m1", "TC-SHOP-001", "Pass", "fictional", "--environment", "test", "--build", "1.0")
        self.assertEqual(rc, 0, out)
        self.assertEqual(next(iter(self.doc())), "mode")
        self.assertEqual(rl.mode_kind(self.doc()), "conversational")

    def test_add_with_mode_on_the_first_result_and_the_same_value_again(self):
        rc, _ = self.cli("add", "m1", "TC-SHOP-001", "Pass", "x", "--mode", "flows: deterministic UI")
        self.assertEqual(rc, 0)
        rc, _ = self.cli("add", "m1", "TC-SHOP-002", "Fail", "x", "--mode", "flows: deterministic UI")
        self.assertEqual(rc, 0)
        rc, out = self.cli("mode", "m1", "flows: deterministic UI")
        self.assertEqual(rc, 0)
        self.assertIn("unchanged", out)
        self.assertEqual(self.doc()["mode"], "flows: deterministic UI")

    def test_an_invalid_value_is_refused_with_the_kinds(self):
        for value in ("robot: fast", "conversational", "conversational:   ", ""):
            rc, out = self.cli("mode", "m1", value)
            self.assertEqual(rc, 2, value)
            self.assertIn("conversational, flows, assisted, manual", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", "batch_m1.json")))

    def test_another_mode_during_the_batch_is_refused_and_nothing_changes(self):
        self.cli("add", "m1", "TC-SHOP-001", "Pass", "x", "--mode", "conversational: answers")
        before = self.doc()
        rc, out = self.cli("mode", "m1", "flows: other")
        self.assertEqual(rc, 1)
        self.assertIn("does not change during the batch", out)
        rc, out = self.cli("add", "m1", "TC-SHOP-002", "Pass", "x", "--mode", "assisted: the user taps")
        self.assertEqual(rc, 1)
        self.assertEqual(self.doc(), before)

    def test_before_any_result_a_change_is_allowed_and_said(self):
        self.cli("mode", "m1", "flows: first idea")
        rc, out = self.cli("mode", "m1", "conversational: generated answers")
        self.assertEqual(rc, 0)
        self.assertIn("changed before any result", out)

    def test_an_old_run_log_is_read_as_it_is_and_never_given_a_mode(self):
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        with open(os.path.join(self.ws, "runs", "batch_old.json"), "w", encoding="utf-8") as f:
            json.dump({"results": [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "x",
                                    "ts": "2026-10-01T10:00:00"}]}, f)
        self.assertIsNone(rl.mode_kind(self.doc("batch_old")))
        rc, out = self.cli("add", "old", "TC-SHOP-002", "Pass", "x")         # without --mode: as before
        self.assertEqual(rc, 0, out)
        rc, out = self.cli("add", "old", "TC-SHOP-003", "Pass", "x", "--mode", "manual: by hand")
        self.assertEqual(rc, 1)
        self.assertIn("not filled in afterwards", out)
        self.assertNotIn("mode", self.doc("batch_old"))
        proj = rl.read_project(self.ws)
        report = qa_report.audit(self.ws, proj, qa_report.load_batches(self.ws, proj))
        self.assertIn("no mode recorded", qa_report.render_audit(report))

    def test_an_applied_batch_is_never_touched(self):
        self.cli("add", "m1", "TC-SHOP-001", "Pass", "x", "--mode", "manual: by hand")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ap.main([self.ws, "runs/batch_m1.json", "--no-recalc"]), 0)
        before = self.doc()
        rc, out = self.cli("mode", "m1", "manual: by hand")
        self.assertEqual(rc, 1)
        self.assertIn("already applied", out)
        self.assertEqual(self.doc(), before)

    def test_the_audit_and_the_page_show_the_kind(self):
        self.cli("add", "m1", "TC-SHOP-001", "Pass", "x", "--mode", "conversational: answers")
        proj = rl.read_project(self.ws)
        text = qa_report.render_audit(qa_report.audit(self.ws, proj, qa_report.load_batches(self.ws, proj)))
        self.assertIn(", conversational]", text)
        import qa_ui.workspace as wk
        self.assertEqual(wk.read_run_log(self.ws, proj, "m1")["mode"], "conversational: answers")


if __name__ == "__main__":
    unittest.main()
