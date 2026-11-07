"""The flow of the eight skills (docs/skills-spec.md section 6) in a second, fictional workspace with no tie to any
real project: selection and isolation between two projects, the ticket analysis over time (first check, no change,
a change, a partial read), results recorded once per case, the resume of /test-pbi, the report and the checkpoint.
Every command the skills run is run here on temporary folders; the real ~/.claude and workspaces are never touched.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import glob
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import compact_context
import pbi_state
import qa_report
import run_log
import workspace_select as ws
from build_fixture import build
from tickets_fake import ORG, FakeAdo

from adapters.tickets import cli as tickets_cli

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)

EIGHT = ("project-context", "analyze-ticket", "plan-tests", "run-tests", "manage-bugs", "qa-report", "checkpoint",
         "test-pbi")
BOARDS = {"service": "azure-devops", "access": "api", "organization_url": ORG, "project": "Demo Shop",
          "pat_var": "DS_ADO_PAT"}
T1, T2, T3, T4 = "2026-11-01T10:00:00", "2026-11-02T10:00:00", "2026-11-03T10:00:00", "2026-11-04T10:00:00"


def files(path: str) -> dict:
    out = {}
    for root, _d, names in os.walk(path):
        for n in names:
            with open(os.path.join(root, n), "rb") as f:
                out[os.path.relpath(os.path.join(root, n), path)] = f.read()
    return out


class SecondWorkspace(unittest.TestCase):
    """Demo Shop (demo/shop) next to another project (acme/shop): every skill must find and write only its own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(tmp, "workspaces")
        self.shop = self.project("demo/shop", "Demo Shop", modules=["mobile", "exploratory", "api"],
                                 mobile={"app_id": "com.demo.shop", "platform": "android", "flows": "flows"},
                                 api={"base_url": "https://shop.example/api/"})
        self.other = self.project("acme/shop", "Acme Shop", modules=["functional-web"])
        self.target = os.path.join(tmp, "global-skills")
        self.config = os.path.join(tmp, "home", "config.json")
        self.neutral = os.path.join(tmp, "elsewhere")
        os.makedirs(self.neutral)

    def project(self, pid, name, **fields):
        path = os.path.join(self.root, *pid.split("/"))
        os.makedirs(path)
        build(os.path.join(path, CFG["file"]))
        proj = {"project": name, "company": pid.split("/")[0], "tracker": "excel", "bug_format": "BUG-{n}",
                "environments": {"test": "https://shop.example/"}, "excel": copy.deepcopy(CFG),
                "tickets": {"sources": {"boards": BOARDS}},
                "report": {"platforms": {"android": ["batch_android_*"], "ios": ["batch_ios_*"]}}, **fields}
        with open(os.path.join(path, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        with open(os.path.join(path, ".env"), "w", encoding="utf-8") as f:
            f.write("DS_ADO_PAT=fake-pat-value-123\n")
        with open(os.path.join(path, "context.md"), "w", encoding="utf-8") as f:
            f.write(f"# Session context — {name}\nUpdated: 2026-11-01 09:00\n\n## Current state\n\n## Execution point "
                    "(last batch, last case)\n\n## Work done\n\n## Decisions made (and who made them)\n\n"
                    "## Open questions\n\n## Known problems / blockers\n\n## Pending work\n\n## Validation state\n")
        return path

    def preflight(self, skill, hint=None, **kw):
        kw.setdefault("cwd", self.neutral)
        kw.setdefault("root", self.root)
        kw.setdefault("target", self.target)
        with mock.patch("validate_project.environment", return_value=[]):
            return ws.preflight(skill, hint, environ={}, config_path=self.config, **kw)

    # --- 3, 4: selection and isolation ----------------------------------------------------------------------------

    def test_every_skill_selects_the_named_project_and_only_it(self):
        for skill in EIGHT:
            with self.subTest(skill=skill):
                code, report = self.preflight(skill, "demo/shop")
                self.assertEqual(code, ws.OK, report.get("problems") or report.get("instruction"))
                self.assertEqual(report["project"]["path"], self.shop)

    def test_the_same_folder_name_in_two_companies_is_a_question_and_nothing_is_chosen(self):
        code, report = self.preflight("project-context", "shop")
        self.assertEqual(code, ws.AMBIGUOUS)
        self.assertEqual(sorted(c["id"] for c in report["candidates"]), ["acme/shop", "demo/shop"])

    def test_inside_a_project_its_folder_decides(self):
        code, report = self.preflight("checkpoint", cwd=os.path.join(self.other))
        self.assertEqual((code, report["project"]["id"]), (ws.OK, "acme/shop"))

    def test_the_routes_follow_each_project(self):
        _, shop = self.preflight("run-tests", "demo/shop")
        _, other = self.preflight("run-tests", "acme/shop")
        self.assertEqual([r["procedure"] for r in shop["routes"]],
                         ["docs/procedures/run-mobile.md", "docs/procedures/run-mobile.md", "docs/procedures/run-api.md"])
        self.assertEqual([r["procedure"] for r in other["routes"]], ["docs/procedures/run-web.md"])
        self.assertTrue(shop["next"])              # a mobile project: check the device first
        self.assertFalse(other["next"])

    # --- 5: the ticket analysis over time ------------------------------------------------------------------------

    def collect(self, fake, now):
        lines = []
        code = tickets_cli.main([self.shop, "collect", "100", "--now", now], transport=fake, sleep=lambda s: None,
                                printer=lines.append)
        paths = [p for p in glob.glob(os.path.join(self.shop, "memory", "checks", "*.json")) if ".analysis" not in p]
        with open(max(paths, key=lambda p: (os.path.basename(p)[:15], len(p), p)), encoding="utf-8") as f:
            return code, json.load(f)

    def test_first_check_then_no_change_then_a_change_then_a_partial_read(self):
        other_before = files(self.other)
        fake = FakeAdo()
        fake.add(100)
        for i in range(1, 5):
            fake.comment(100, i, f"<p>comment {i}</p>")
        code, check = self.collect(fake, T1)
        self.assertEqual((code, check["conclusion"]), (0, "first"))          # the baseline
        code, check = self.collect(fake, T2)
        self.assertEqual((code, check["conclusion"]), (0, "no-changes"))
        fake.set(100, System__State="Doing")
        code, check = self.collect(fake, T3)
        self.assertEqual(code, 0)
        kinds = {(c["component"], c["kind"]) for t in check["tickets"] for c in t["changes"]}
        self.assertTrue(any(comp == "fields" for comp, _ in kinds), kinds)
        fake.faults.rules["continuationToken=2"] = "network"                  # comments 3 and 4 cannot be read
        code, check = self.collect(fake, T4)
        self.assertEqual(code, 1)
        ticket = check["tickets"][0]
        self.assertEqual(ticket["conclusion"], "not-concluded")
        self.assertFalse([c for c in ticket["changes"] if c["kind"] == "removed"])   # nothing "removed" by a partial read
        memory_files = glob.glob(os.path.join(self.shop, "memory", "tickets", "**", "*.json"), recursive=True)
        with open(memory_files[0], encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["components"]["comments"]["data"]), 4)  # what was known stays
        self.assertEqual(files(self.other), other_before)                    # the other project is untouched

    # --- 7: results recorded once per case, with their approvals ---------------------------------------------------

    def add(self, batch, case, status, actual, **kw):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_log.add(self.shop, batch, case, status, actual, now=datetime(2026, 11, 5, 10, 0), **kw)

    def test_results_once_per_case_and_a_case_outside_the_library_is_refused(self):
        self.add("android_01", "TC-SHOP-001", "Fail", "total wrong", build="1.0.0 (7)", environment="test")
        with self.assertRaises(run_log.Refused):                               # a second result needs --replace
            self.add("android_01", "TC-SHOP-001", "Pass", "total right after reload")
        self.add("android_01", "TC-SHOP-001", "Pass", "total right after reload", replace=True)  # replaced, not duplicated
        with open(os.path.join(self.shop, "runs", "batch_android_01.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual([r["tc"] for r in doc["results"]], ["TC-SHOP-001"])
        self.assertEqual(doc["run"]["build"], "1.0.0 (7)")
        with self.assertRaises(run_log.UsageError):
            self.add("android_01", "TC-NOPE-999", "Pass", "x")
        with self.assertRaises(run_log.Refused):                               # the build changed: a new batch
            self.add("android_01", "TC-SHOP-002", "Pass", "x", build="1.0.1 (8)")

    def test_a_proposed_bug_waits_for_review_and_the_report_keeps_platforms_apart(self):
        draft = os.path.join(self.shop, "draft.json")
        with open(draft, "w", encoding="utf-8") as f:
            json.dump({"title": "[Cart] total wrong", "proposed": True}, f)
        with open(draft, encoding="utf-8") as f:
            new_defect = json.load(f)
        self.add("android_01", "TC-SHOP-002", "Fail", "total wrong", build="1.0.0 (7)", environment="test",
                 new_defect=new_defect)
        self.add("ios_01", "TC-SHOP-002", "Pass", "total right", build="1.0.0 (7)", environment="test")
        with open(os.path.join(self.shop, "project.yaml"), encoding="utf-8") as f:
            proj = yaml.safe_load(f)
        batches = qa_report.load_batches(self.shop, proj)
        audit = qa_report.audit(self.shop, proj, batches)
        android = next(b for b in audit["batches"] if b["batch"] == "batch_android_01")
        self.assertEqual(android["proposed_bugs"], ["TC-SHOP-002"])
        self.assertTrue(android["to_fix"])                                    # not applied while it waits for review
        totals = qa_report.totals(self.shop, proj, batches)["platforms"]
        self.assertEqual((totals["android"]["statuses"], totals["ios"]["statuses"]), ({"Fail": 1}, {"Pass": 1}))

    # --- 8: /test-pbi resumes without repeating ---------------------------------------------------------------------

    def pbi(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pbi_state.main([self.shop, *args])
        return code, out.getvalue()

    def test_a_second_session_resumes_at_the_first_step_not_done(self):
        self.pbi("start", "DS-12", "--platform", "android", "--platform", "ios")
        self.pbi("mark", "DS-12", "context", "done", "--ref", "context.md")
        self.pbi("mark", "DS-12", "analyze", "done", "--ref", "memory/checks/20261101-100000.json")
        self.pbi("mark", "DS-12", "plan", "done", "--ref", "analysis/coupon_20261101.md")
        self.pbi("baseline", "DS-12", "--ticket", "DS-12=4", "--build", "android=1.0.0 (7)")
        self.pbi("published", "DS-12", "TC-SHOP-002", "BUG-7", "--confirmed-by", "tracker/sent/batch_android_01.json")
        with open(os.path.join(self.shop, "memory", "pbi", "DS-12.json"), encoding="utf-8") as f:
            history = json.load(f)["history"]
        # a new session: start again, the same steps asked again
        _, out = self.pbi("start", "DS-12", "--platform", "android")
        self.assertIn("resumed", out)
        self.assertIn("Next: run:android", out)
        self.assertIn("already done", self.pbi("mark", "DS-12", "plan", "done", "--ref", "analysis/coupon_20261101.md")[1])
        self.assertEqual(self.pbi("published", "DS-12", "TC-SHOP-002", "BUG-8", "--confirmed-by", "x")[0], 1)
        with open(os.path.join(self.shop, "memory", "pbi", "DS-12.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["history"], history)               # nothing repeated, nothing added
        self.assertEqual(self.pbi("check", "DS-12", "--build", "android=1.0.1 (8)")[0], 1)
        self.assertFalse(os.path.exists(os.path.join(self.other, "memory")))

    # --- 10: the checkpoint keeps decisions, pending work and the way to the history -------------------------------

    def test_compacting_keeps_decisions_pending_and_the_original(self):
        path = os.path.join(self.shop, "context.md")
        entries = "".join(f"- {d:02d}.10.2026: batch L{d} run on Android\n" for d in range(1, 21))
        text = ("# Session context — Demo Shop\nUpdated: 2026-11-05 10:00\n\n## Current state\n\n"
                "## Execution point (last batch, last case)\n\n## Work done\n\n" + entries +
                "\n## Decisions made (and who made them)\n\n- 02.10.2026 (product owner): coupons never stack.\n\n"
                "## Pending work\n\n1. TC-SHOP-004 on iOS at 12:50 of 04.11.2026 (not run; ask for a new time).\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with contextlib.redirect_stdout(io.StringIO()):
            compact_context.apply(self.shop, keep=5, now=datetime(2026, 11, 5, 10, 0))
        with open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertIn("coupons never stack", after)                         # decisions stay
        self.assertIn("TC-SHOP-004 on iOS at 12:50 of 04.11.2026 (not run", after)    # the pending item, untouched
        self.assertIn("archive/context/", after)                            # the way to what moved
        archived = ""
        for p in glob.glob(os.path.join(self.shop, "archive", "context", "*.md")):
            with open(p, encoding="utf-8") as f:
                archived += f.read()
        self.assertIn("- 01.10.2026: batch L1 run on Android", archived)    # word for word
        self.assertIn(text, archived)                                       # and the whole file before the move


if __name__ == "__main__":
    unittest.main()
