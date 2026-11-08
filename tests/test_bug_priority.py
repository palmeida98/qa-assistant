"""The priority of a new bug (A-04): the project's schema says whether it is required and which values it accepts; the
user decides; the run log refuses an APPROVED bug without a valid one (so the dry run is no surprise) and lets a PROPOSAL
wait; the audit lists what waits; the adapter's own final check stays.

Fictional workspaces, two vocabularies (the golden fixture's `P1..P3` and a project's own words) and a project whose
schema asks for none.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import qa_report as qr
import run_log as rl
from build_fixture import build
from common import defect_priority_problem, defect_priority_rule

from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 8, 19, 43, 0)
DEFECT = {"title": "Fictional bug", "description": "d", "area": "Shop"}


class Priority(unittest.TestCase):
    def workspace(self, priorities, tracker="excel"):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ws = self.tmp.name
        build(os.path.join(ws, CFG["file"]))
        excel = copy.deepcopy(CFG)
        excel["defects"]["priorities"] = priorities
        with open(os.path.join(ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"project": "demo", "environments": {"test": "https://x.test"}, "tracker": tracker,
                            "excel": excel}, f)
        self.ws = ws
        return ws

    def fail(self, **defect):
        return rl.add(self.ws, "20261108_01", "TC-SHOP-001", "Fail", "observed", environment="test", build="1 (1)",
                      new_defect={**DEFECT, **defect}, now=NOW)

    def test_the_rule_comes_from_the_schema(self):
        self.workspace(["P1", "P2", "P3"])
        proj = qr.read_project(self.ws)
        self.assertEqual(defect_priority_rule(proj)["required"], True)
        self.workspace([])
        self.assertEqual(defect_priority_rule(qr.read_project(self.ws)), {"required": False, "accepted": [],
                                                                         "where": "excel.defects.priorities"})
        jira = {"tracker": "jira", "jira": {"bugs": {"priorities": ["High", "Low"]}}}
        self.assertEqual(defect_priority_rule(jira), {"required": False, "accepted": ["High", "Low"],
                                                      "where": "jira.bugs.priorities"})

    def test_two_vocabularies_valid_absent_and_invalid(self):
        for words, good, other in ((["P1", "P2", "P3"], "P2", "Alta"), (["Crítica", "Alta", "Média", "Baixa"], "Alta", "P2")):
            ws = self.workspace(words)
            proj = qr.read_project(ws)
            self.assertIsNone(defect_priority_problem(proj, {"priority": good}))
            self.assertIn("requires a priority", defect_priority_problem(proj, {}))
            self.assertIn("is not one of", defect_priority_problem(proj, {"priority": other}))
            self.assertIn(", ".join(words), defect_priority_problem(proj, {"priority": other}))

    def test_an_approved_bug_without_a_decided_priority_is_refused_and_nothing_is_written(self):
        self.workspace(["P1", "P2", "P3"])
        for defect in ({}, {"priority": ""}, {"priority": "Alta"}):
            with self.assertRaises(rl.UsageError) as ctx:
                self.fail(**defect)
            self.assertIn("P1, P2, P3", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))

    def test_a_proposal_may_wait_and_the_audit_lists_it_until_the_user_decides(self):
        self.workspace(["P1", "P2", "P3"])
        self.fail(proposed=True)
        proj = qr.read_project(self.ws)
        entry = qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))["batches"][0]
        self.assertEqual((entry["proposed_bugs"], entry["bugs_without_priority"], entry["to_fix"]),
                         (["TC-SHOP-001"], ["TC-SHOP-001"], True))
        self.assertIn("priority decision", qr.render_audit(qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))))
        # the user decides: the approved bug is recorded with it (attach keeps the result) and the audit is clean of it
        _path, _, after = rl.attach(self.ws, "20261108_01", "TC-SHOP-001", new_defect={**DEFECT, "priority": "P2"},
                                   replace_link=True)
        self.assertEqual(after["new_defect"]["priority"], "P2")
        entry = qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))["batches"][0]
        self.assertEqual((entry["bugs_without_priority"], entry["proposed_bugs"]), ([], []))

    def test_attach_applies_the_same_rule(self):
        self.workspace(["P1", "P2", "P3"])
        rl.add(self.ws, "20261108_01", "TC-SHOP-001", "Fail", "observed", environment="test", build="1 (1)", now=NOW)
        with self.assertRaises(rl.UsageError):
            rl.attach(self.ws, "20261108_01", "TC-SHOP-001", new_defect=dict(DEFECT))
        with open(os.path.join(self.ws, "runs", "batch_20261108_01.json"), encoding="utf-8") as f:
            self.assertNotIn("new_defect", json.load(f)["results"][0])

    def test_a_project_that_asks_for_no_priority_is_not_asked(self):
        self.workspace([])
        self.fail()                                                  # recorded as approved, no priority
        proj = qr.read_project(self.ws)
        self.assertEqual(qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))["batches"][0]["bugs_without_priority"], [])

    def test_the_adapter_keeps_its_final_check(self):
        self.workspace(["P1", "P2", "P3"])
        self.fail(priority="P2")
        # a run log edited by hand to a priority outside the list still stops at the dry run
        path = os.path.join(self.ws, "runs", "batch_20261108_01.json")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["results"][0]["new_defect"]["priority"] = "Whatever"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        res = ap.dry_run(self.ws, path, recalc=False, now=NOW)
        self.assertEqual(res["code"], 4)
        self.assertTrue(any("priority" in p for p in res["problems"]), res["problems"])

    def test_the_cli_states_the_rule(self):
        self.workspace(["Crítica", "Alta"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rl.main([self.ws, "defect-rule"]), 0)
        self.assertIn("REQUIRED", out.getvalue())
        self.assertIn("Crítica, Alta", out.getvalue())


if __name__ == "__main__":
    unittest.main()
