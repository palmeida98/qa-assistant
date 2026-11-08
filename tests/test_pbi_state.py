"""scripts/pbi_state.py: the resume record of /test-pbi (docs/skills-spec.md section 5). A fictional workspace in a
temporary folder; nothing real is read or written.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import pbi_state as ps
import workspace_kit as kit


class PbiState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write('project: "Demo Shop"\ntracker: "excel"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ps.main([self.ws, *args])
        return code, out.getvalue()

    def record(self, pbi="1234"):
        with open(os.path.join(self.ws, "memory", "pbi", f"{pbi}.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_start_creates_the_steps_per_platform_and_resuming_adds_without_removing(self):
        code, out = self.run_("start", "1234", "--task", "1240", "--platform", "android")
        self.assertEqual(code, 0)
        self.assertIn("created", out)
        self.assertIn("Next: context", out)
        self.assertIn("run:android", self.record()["steps"])
        code, out = self.run_("start", "1234", "--platform", "ios")
        self.assertIn("resumed", out)
        doc = self.record()
        self.assertEqual(doc["platforms"], ["android", "ios"])
        self.assertEqual(doc["task"], "1240")
        self.assertIn("run:ios", doc["steps"])

    def test_a_link_gives_the_id_and_another_host_shape_is_refused(self):
        with open(os.path.join(self.ws, "project.yaml"), "a", encoding="utf-8") as f:
            f.write("tickets:\n  sources:\n    boards: {service: azure-devops, organization_url: "
                    "'https://dev.azure.com/demo-shop'}\n    jira: {service: jira, base_url: "
                    "'https://demo-shop.atlassian.net'}\n")
        self.assertEqual(ps.pbi_id("https://dev.azure.com/demo-shop/Demo%20Shop/_workitems/edit/1234/", self.ws), "1234")
        self.assertEqual(ps.pbi_id("https://demo-shop.atlassian.net/browse/DS-12", self.ws), "DS-12")
        with self.assertRaises(ps.UsageError):        # another host (audit 06.11.2026: it was accepted)
            ps.pbi_id("https://evil.example.com/browse/1234", self.ws)
        code, _ = self.run_("start", "../escape")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory")))

    def test_done_needs_proof_and_marking_it_again_changes_nothing(self):
        self.run_("start", "1234", "--platform", "android")
        self.assertEqual(self.run_("mark", "1234", "context", "done")[0], 2)          # no --ref
        self.assertEqual(self.run_("mark", "1234", "context", "done", "--ref", "context.md")[0], 0)
        before = self.record()
        code, out = self.run_("mark", "1234", "context", "done", "--ref", "context.md")
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.record()["history"], before["history"])                # nothing repeated, nothing added

    def test_resume_goes_to_the_first_step_not_done(self):
        self.run_("start", "1234", "--platform", "android", "--platform", "ios")
        for step, ref in (("context", "context.md"), ("analyze", "memory/checks/20261101-100000.json"),
                          ("plan", "analysis/coupon_20261101.md"), ("run:android", "batch_20261101_DS12_android")):
            self.run_("mark", "1234", step, "done", "--ref", ref)
        self.run_("mark", "1234", "run:ios", "blocked", "--note", "no iPhone connected")
        code, out = self.run_("show", "1234")
        self.assertEqual(code, 0)
        self.assertIn("Next: bugs", out)                       # the blocked platform does not stop the rest
        self.assertIn("no iPhone connected", out)

    def test_a_step_of_a_platform_not_started_is_refused(self):
        self.run_("start", "1234", "--platform", "android")
        code, out = self.run_("mark", "1234", "run:ios", "done", "--ref", "x")
        self.assertEqual(code, 2)
        self.assertIn("--platform", out)

    def test_check_names_what_changed_and_what_to_review(self):
        self.run_("start", "1234", "--platform", "android")
        code, out = self.run_("check", "1234", "--build", "android=1.0.0 (7)")
        self.assertEqual(code, 3)                               # no baseline: not concluded, never "nothing changed"
        self.assertNotIn("nothing changed", out)
        self.run_("baseline", "1234", "--ticket", "1234=12", "--build", "android=1.0.0 (7)", "--environment", "test")
        self.assertEqual(self.run_("check", "1234", "--ticket", "1234=12", "--build", "android=Demo 1.0.0 (code 7)",
                                   "--environment", "test")[0],
                         0)                                     # the same build written another way
        code, out = self.run_("check", "1234", "--ticket", "1234=13", "--build", "android=1.0.1 (8)")
        self.assertEqual(code, 1)
        self.assertIn("CHANGED ticket 1234: 12 -> 13", out)
        self.assertIn("CHANGED build android: 1.0.0 (7) -> 1.0.1 (8)", out)
        self.assertIn("stay valid for 1.0.0 (7)", out)          # old results are not thrown away

    def test_a_bug_is_never_published_twice(self):
        self.run_("start", "1234", "--platform", "android")
        self.assertEqual(self.run_("published", "1234", "draft-1", "BUG-9")[0], 2)    # no confirmation from the tracker
        ok = ("--confirmed-by", "memory/checks/20261106-100000.json")
        self.assertEqual(self.run_("published", "1234", "draft-1", "BUG-9", *ok)[0], 0)
        code, out = self.run_("published", "1234", "draft-1", "BUG-9", *ok)
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.run_("published", "1234", "draft-2", "BUG-9", *ok)[0], 1)    # one key, two drafts
        self.assertEqual(self.run_("published", "1234", "", "BUG-11", *ok)[0], 2)
        self.assertEqual(self.run_("published", "1234", "draft-3", " ", *ok)[0], 2)
        code, out = self.run_("published", "1234", "draft-1", "BUG-10", *ok)
        self.assertEqual(code, 1)
        self.assertIn("never publish a bug twice", out)
        self.assertEqual(self.record()["published"]["draft-1"]["key"], "BUG-9")

    def test_check_says_what_it_could_not_compare_and_reopens_on_request(self):
        self.run_("start", "1234", "--platform", "android")
        for step in ("context", "analyze", "plan", "run:android"):
            self.run_("mark", "1234", step, "done", "--ref", "x")
        self.run_("baseline", "1234", "--ticket", "1234=12", "--ticket", "1240=3", "--build", "android=1.0.0 (7)")
        code, out = self.run_("check", "1234", "--ticket", "1234=12", "--build", "android=1.0.0 (7)")
        self.assertEqual(code, 3)
        self.assertIn("NOT COMPARED: ticket 1240", out)
        code, out = self.run_("check", "1234", "--ticket", "1234=13", "--ticket", "1240=3", "--build",
                              "android=1.0.0 (7)", "--reopen")
        self.assertEqual(code, 1)
        self.assertIn("reopened", out)
        self.assertIn("Next: analyze", self.run_("show", "1234")[1])        # back to the first step to review
        self.assertEqual(self.record()["steps"]["plan"]["status"], "pending")

    def test_a_blocked_step_needs_its_reason(self):
        self.run_("start", "1234", "--platform", "ios")
        self.assertEqual(self.run_("mark", "1234", "run:ios", "blocked")[0], 2)

    def test_a_second_writer_waits_then_writes_nothing(self):
        self.run_("start", "1234", "--platform", "android")
        before = self.record()
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, _ = self.run_("mark", "1234", "context", "done", "--ref", "context.md")
        self.assertEqual(code, 5)
        self.assertEqual(self.record(), before)

    def test_a_broken_record_is_said_and_left_alone(self):
        os.makedirs(os.path.join(self.ws, "memory", "pbi"))
        path = os.path.join(self.ws, "memory", "pbi", "1234.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        code, out = self.run_("show", "1234")
        self.assertEqual(code, 2)
        self.assertIn("nothing was changed", out)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")

    def test_not_a_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                self.assertEqual(ps.main([other, "start", "1234"]), 2)
            self.assertEqual(os.listdir(other), [])


if __name__ == "__main__":
    unittest.main()
