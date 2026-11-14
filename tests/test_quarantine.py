"""Bugs in quarantine (procedures/quarantine.md; scripts/quarantine.py, run_log.py progress). A fictional workspace with a
workbook per platform (Android the default, iOS its own) in a temporary folder: a quarantine of iOS only, a case left out,
retests that fail by the known reason, fail differently or pass, and a workspace without quarantines. Every command is
called through its entry point (`main`), as the skills call it.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import pbi_state
import qa_report
import quarantine as q
import run_log as rl
from build_fixture import build

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
IOS = "plan-ios.xlsx"
BUG = "9770"
BUILD = "1.0.0 (39)"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = os.path.realpath(self.tmp.name)
        build(os.path.join(self.ws, CFG["file"]))
        shutil.copy2(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, IOS))
        excel = copy.deepcopy(CFG)
        excel["workbooks"] = {"ios": {"file": IOS, "batches": ["batch_ios*"]}}
        proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel",
                "excel": excel, "report": {"platforms": {"android": ["batch_android*"], "ios": ["batch_ios*"]}},
                "tickets": {"sources": {"boards": {"service": "azure-devops", "access": "api",
                                                   "organization_url": "https://dev.azure.com/example-org",
                                                   "project": "Demo Shop", "pat_var": "SHOP_PAT"}}}}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        os.makedirs(os.path.join(self.ws, "batches"))
        env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in ("QA_WORKBOOK", "QA_LOCK_WAIT")},
                              clear=True)
        env.start()
        self.addCleanup(env.stop)

    def run_(self, mod, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = mod.main([self.ws, *args])
        return code, out.getvalue()

    def ok(self, mod, *args):
        code, out = self.run_(mod, *args)
        self.assertEqual(code, 0, out)
        return out

    def quarantine_ios(self, decide=True):
        self.ok(q, "propose", "Q-1", "--bug", BUG, "--case", "TC-SHOP-002", "--platform", "ios", "--build", BUILD,
                "--environment", "test", "--reason", "the notice repeats at the second written yes (2 of 3 runs)",
                "--exit", "a build with the fix: directed retest, then the QA decides")
        if decide:
            self.ok(q, "decide", "Q-1", "activate", "--decided-by", "QA (session 13.11)", "--ref", "context.md §6")

    def plan(self, name, cases, excluded=(), retest=()):
        path = os.path.join(self.ws, "batches", name + ".yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": list(cases), "excluded": [{"case": c, "quarantine": "Q-1"} for c in excluded],
                            "retest": [{"case": c, "quarantine": "Q-1"} for c in retest]}, f)
        return f"batches/{name}.yaml"

    def result(self, batch, case, status, *extra):
        self.ok(rl, "add", batch, case, status, "fictional observation", "--environment", "test", "--build", BUILD,
                "--mode", "manual: fictional", *extra)

    def register(self):
        return os.path.join(self.ws, "memory", "quarantine.json")


class Governance(Base):
    def test_only_the_qas_decision_activates_and_an_id_is_never_reused(self):
        self.quarantine_ios(decide=False)
        self.assertEqual(q.load(self.ws)["entries"]["Q-1"]["status"], "proposed")
        self.assertIn("No active quarantine", self.ok(q, "scope", "--platform", "ios", "--build", BUILD))   # a proposal holds nothing
        for args in (("decide", "Q-1", "activate"), ("decide", "Q-1", "activate", "--decided-by", "QA"),
                     ("decide", "Q-1", "activate", "--ref", "x")):
            self.assertEqual(self.run_(q, *args)[0], 2, args)                          # who decided and where: both
        self.assertEqual(self.run_(q, "decide", "Q-1", "lift", "--decided-by", "QA", "--ref", "x")[0], 1)   # not active yet
        out = self.ok(q, "decide", "Q-1", "activate", "--decided-by", "QA (session 13.11)", "--ref", "context.md §6")
        self.assertIn("active", out)
        entry = q.load(self.ws)["entries"]["Q-1"]
        self.assertEqual((entry["decided_by"], entry["bug_url"]),
                         ("QA (session 13.11)", "https://dev.azure.com/example-org/Demo%20Shop/_workitems/edit/9770"))
        self.assertEqual(self.run_(q, "propose", "Q-1", "--bug", BUG, "--case", "TC-SHOP-001", "--reason", "r",
                                   "--exit", "e")[0], 1)                               # an active id is not reused
        self.assertEqual(self.run_(q, "propose", "Q-2", "--bug", BUG, "--case", "TC-NOPE-404", "--reason", "r",
                                   "--exit", "e")[0], 2)                               # only cases of the library
        self.assertEqual(self.run_(q, "propose", "Q-3", "--bug", BUG, "--case", "TC-SHOP-001")[0], 2)  # reason and exit
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA", "--ref", "context.md §6 (14.11)")
        self.assertIn("No active quarantine", self.ok(q, "scope", "--platform", "ios", "--build", BUILD))


class IosOnly(Base):
    def test_a_quarantine_of_ios_leaves_android_planned_and_run_as_usual(self):
        self.quarantine_ios()
        out = self.ok(q, "scope", "--platform", "android", "--build", BUILD, "--environment", "test")
        self.assertIn("No active quarantine holds in this context", out)
        android = self.plan("batch_android_20261113_01", ["TC-SHOP-001", "TC-SHOP-002"])
        self.ok(q, "scope", "--platform", "android", "--build", BUILD, "--plan", android)      # no problem: run it
        self.ok(rl, "plan", "batch_android_20261113_01", android)
        self.result("batch_android_20261113_01", "TC-SHOP-002", "Pass")
        self.assertEqual(rl.progress(self.ws, "batch_android_20261113_01")["pending"], ["TC-SHOP-001"])
        # another build of iOS is not covered either
        self.assertIn("No active quarantine", self.ok(q, "scope", "--platform", "ios", "--build", "1.0.0 (40)"))
        # the iOS plan must say the QA's choice for the covered case
        ios = self.plan("batch_ios_20261113_01", ["TC-SHOP-001", "TC-SHOP-002"])
        code, out = self.run_(q, "scope", "--platform", "ios", "--build", BUILD, "--environment", "test", "--plan", ios)
        self.assertEqual(code, 1)
        self.assertIn("TC-SHOP-002: covered by quarantine Q-1 (bug 9770)", out)
        self.assertIn("the QA chooses excluded or a directed retest", out)


class LeftOut(Base):
    def test_a_case_left_out_is_reported_with_its_reason_and_bug_and_never_as_pass(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_02"
        plan = self.plan(name, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        out = self.ok(q, "scope", "--platform", "ios", "--build", BUILD, "--environment", "test", "--plan", plan)
        for needle in ("Q-1 [active] bug 9770 (https://dev.azure.com/example-org/Demo%20Shop/_workitems/edit/9770)",
                       "cases: TC-SHOP-002", "scope: platforms ios · builds 1.0.0 (39) · environments test",
                       "reason: the notice repeats", "exit: a build with the fix"):
            self.assertIn(needle, out)
        self.ok(rl, "plan", name, plan)
        self.result(name, "TC-SHOP-001", "Pass")
        self.result(name, "TC-SHOP-003", "Pass")
        info = rl.progress(self.ws, name)
        self.assertEqual((info["pending"], info["next"], info["excluded"]), ([], None, {"TC-SHOP-002": "Q-1"}))
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            self.assertNotIn("TC-SHOP-002", json.dumps(json.load(f)))              # no result written for it
        r = q.report(self.ws)
        self.assertEqual([(x["case"], x["quarantine"], x["bug"], x["counts_as_run"], x["status_shown"])
                          for x in r["excluded"]], [("TC-SHOP-002", "Q-1", BUG, False, "To Do")])
        text = q.render_report(r)
        self.assertIn("TC-SHOP-002 in batch_ios_20261113_02: To Do — excluded by quarantine Q-1 · bug 9770 "
                      "https://dev.azure.com/example-org/Demo%20Shop/_workitems/edit/9770", text)
        proj = rl.read_project(self.ws)
        totals = qa_report.totals(self.ws, proj, qa_report.load_batches(self.ws, proj))
        dumped = json.dumps(totals, default=str)
        self.assertIn("TC-SHOP-001", dumped)                                         # the totals list the cases run...
        self.assertNotIn("TC-SHOP-002", dumped)                                      # ...and not this one: not run, not Pass


class Retests(Base):
    def setUp(self):
        super().setUp()
        self.quarantine_ios()
        self.name = "batch_ios_20261113_03"
        plan = self.plan(self.name, ["TC-SHOP-002"], retest=["TC-SHOP-002"])
        self.ok(q, "scope", "--platform", "ios", "--build", BUILD, "--environment", "test", "--plan", plan)
        self.ok(rl, "plan", self.name, plan)

    def test_a_retest_that_fails_by_the_known_reason_keeps_its_fail_and_the_bug(self):
        self.result(self.name, "TC-SHOP-002", "Fail", "--bug", BUG)
        r = q.report(self.ws)
        self.assertEqual([(x["case"], x["status"], x["linked_bug"]) for x in r["known_failures"]],
                         [("TC-SHOP-002", "Fail", BUG)])
        self.assertEqual(r["retests"], [])
        log = rl.read_log(os.path.join(self.ws, "runs", self.name + ".json"))
        self.assertEqual((log["results"][0]["status"], log["results"][0]["bug"]), ("Fail", BUG))
        self.assertEqual(q.load(self.ws)["entries"]["Q-1"]["status"], "active")

    def test_a_different_failure_is_not_hidden_and_goes_to_the_normal_review(self):
        self.result(self.name, "TC-SHOP-002", "Fail")                               # another behaviour: no link
        r = q.report(self.ws)
        self.assertEqual(r["known_failures"], [])
        self.assertEqual(len(r["retests"]), 1)
        self.assertIn("different failure: normal review", r["retests"][0]["verdict"])
        self.assertIn("the quarantine does not explain it", q.render_report(r))

    def test_a_retest_that_passes_keeps_the_quarantine_until_the_qa_decides(self):
        self.result(self.name, "TC-SHOP-002", "Pass")
        r = q.report(self.ws)
        self.assertIn("retest passed: the quarantine stays until the QA decides", r["retests"][0]["verdict"])
        self.assertEqual(q.load(self.ws)["entries"]["Q-1"]["status"], "active")
        self.assertEqual(len(r["decisions"]), 1)
        self.assertIn("the QA decides whether it is lifted (decide Q-1 lift)", r["decisions"][0])
        # the decision waits as an open item of the PBI's record, closed only with the QA's decision
        self.ok(pbi_state, "start", "DS-34", "--platform", "ios")
        self.ok(pbi_state, "open", "DS-34", "quarantine-Q-1", "--kind", "approval", "--missing",
                "TC-SHOP-002 passed on 1.0.0 (39): lift Q-1?", "--unblocks", "QA")
        self.assertIn("quarantine-Q-1 [approval]", self.ok(pbi_state, "show", "DS-34"))
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA (session 14.11)", "--ref", "context.md §6")
        self.ok(pbi_state, "close", "DS-34", "quarantine-Q-1", "--ref", "quarantine.py decide Q-1 lift",
                "--decided-by", "QA (session 14.11)")
        self.assertEqual(q.report(self.ws)["decisions"], [])


class WithoutQuarantines(Base):
    def test_a_workspace_without_quarantines_works_as_before_and_nothing_is_created(self):
        name = "batch_ios_20261113_04"
        plan = self.plan(name, ["TC-SHOP-001", "TC-SHOP-002"])
        self.assertIn("No quarantine in this workspace.", self.ok(q, "show"))
        self.assertIn("No active quarantine", self.ok(q, "scope", "--platform", "ios", "--build", BUILD, "--plan", plan))
        self.ok(rl, "plan", name, plan)
        self.result(name, "TC-SHOP-001", "Fail")
        info = rl.progress(self.ws, name)
        self.assertEqual((info["pending"], info["excluded"]), (["TC-SHOP-002"], {}))
        r = q.report(self.ws)
        self.assertEqual((r["excluded"], r["known_failures"], r["retests"], r["decisions"]), ([], [], [], []))
        self.ok(pbi_state, "start", "DS-35", "--platform", "ios")
        self.assertIn("Next: context", self.ok(pbi_state, "show", "DS-35"))
        self.assertFalse(os.path.exists(self.register()))                          # reading never creates the register
        # a plan of before (only `cases:`) still gives its progress
        with open(os.path.join(self.ws, "batches", "old.yaml"), "w", encoding="utf-8") as f:
            f.write("cases: [TC-SHOP-001, TC-SHOP-003]\n")
        self.assertEqual(rl.progress(self.ws, name, "batches/old.yaml")["pending"], ["TC-SHOP-003"])


if __name__ == "__main__":
    unittest.main()


class EnforcedAtTheEntryPoint(Base):
    """run_log.py add is where a result is written: it refuses what would bypass the QA's decision (13.11.2026)."""

    def setUp(self):
        super().setUp()
        self.quarantine_ios()

    @staticmethod
    def text(path):
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def refused(self, batch, case, *extra, text=""):
        path = os.path.join(self.ws, "runs", batch + ".json")
        before = self.text(path)
        code, out = self.run_(rl, "add", batch, case, "Pass", "fictional observation", "--environment", "test",
                              "--build", BUILD, "--mode", "manual: fictional")
        self.assertEqual(code, 1, out)
        self.assertIn(text, out)
        self.assertIn("nothing was written", out)
        self.assertEqual(self.text(path), before)

    def test_a_covered_case_without_the_qas_decision_or_left_out_is_never_recorded(self):
        name = "batch_ios_20261113_10"
        self.refused(name, "TC-SHOP-002", text="has no decision of the QA for it")      # no plan at all
        self.ok(rl, "plan", name, self.plan(name, ["TC-SHOP-001", "TC-SHOP-002"]))       # a plan without the decision
        self.refused(name, "TC-SHOP-002", text="has no decision of the QA for it")
        name = "batch_ios_20261113_11"
        self.ok(rl, "plan", name, self.plan(name, ["TC-SHOP-001"], excluded=["TC-SHOP-002"]))
        self.result(name, "TC-SHOP-001", "Pass")                                           # the rest of the plan runs
        self.refused(name, "TC-SHOP-002", text="was left out of this batch by quarantine Q-1")
        name = "batch_ios_20261113_12"
        self.ok(rl, "plan", name, self.plan(name, ["TC-SHOP-002"], retest=["TC-SHOP-002"]))
        self.result(name, "TC-SHOP-002", "Fail", "--bug", BUG)                             # the decided retest
        self.result("batch_android_20261113_10", "TC-SHOP-002", "Pass")                    # Android: not covered

    def test_on_resuming_the_decision_holds_only_while_the_context_and_the_quarantine_are_the_same(self):
        plan = self.plan("ios_plan", ["TC-SHOP-001"], excluded=["TC-SHOP-002"])
        same = "batch_ios_20261113_20"
        self.ok(rl, "plan", same, plan)
        self.result(same, "TC-SHOP-001", "Pass")
        self.assertEqual(rl.progress(self.ws, same)["quarantine_problems"], [])             # reused as it is
        # a new build: a new batch with the same plan; the quarantine named 1.0.0 (39) only
        newer = "batch_ios_20261114_01"
        self.ok(rl, "plan", newer, plan)
        self.ok(rl, "add", newer, "TC-SHOP-001", "Pass", "x", "--environment", "test", "--build", "1.0.0 (40)",
                "--mode", "manual: fictional")
        info = rl.progress(self.ws, newer)
        self.assertTrue(any("not covered" in p or "no active quarantine covers" in p for p in info["quarantine_problems"]),
                        info["quarantine_problems"])
        self.assertIn("PLAN TO REVALIDATE (quarantine)", self.ok(rl, "progress", newer))
        code, out = self.run_(rl, "add", newer, "TC-SHOP-002", "Pass", "x", "--mode", "manual: fictional")
        self.assertEqual(code, 1)
        self.assertIn("the plan is out of date", out)
        code, out = self.run_(q, "scope", "--platform", "ios", "--build", "1.0.0 (40)", "--environment", "test",
                              "--plan", plan)
        self.assertEqual(code, 1)
        # the quarantine lifted by the QA: the same batch's plan is out of date too
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA", "--ref", "context.md §6 (14.11)")
        self.assertTrue(rl.progress(self.ws, same)["quarantine_problems"])
        code, out = self.run_(rl, "add", same, "TC-SHOP-002", "Pass", "x")
        self.assertEqual(code, 1)
        self.assertIn("the plan is out of date", out)


class ReportAndWorkbook(Base):
    def test_a_case_left_out_keeps_its_previous_result_in_the_workbook_and_is_reported_in_the_projects_words(self):
        import workspace_kit as kit

        from adapters.excel import apply as ap
        from adapters.excel.base import ExcelAdapter
        with open(os.path.join(self.ws, "project.yaml"), encoding="utf-8") as f:
            proj = yaml.safe_load(f)
        proj["quarantine"] = {"excluded_label": "excluído por quarentena"}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True)
        first = "batch_ios_20261112_01"                                       # the result the case had before
        self.result(first, "TC-SHOP-002", "Fail", "--bug", "BUG-1")
        code, out = kit.run_approved(ap.main, [self.ws, f"runs/{first}.json", "--no-recalc"], dry_extra=("--detail",))
        self.assertEqual(code, 0, out)

        def row(case):
            ad = ExcelAdapter.from_project(self.ws, file=IOS)
            ad.load()
            return next(r for rows in ad.case_rows().values() for r in rows if r["ID"] == case)
        before = row("TC-SHOP-002")
        self.ok(q, "propose", "Q-1", "--bug", "BUG-1", "--tracker-bug", BUG, "--case", "TC-SHOP-002", "--platform", "ios",
                "--build", BUILD, "--environment", "test", "--reason", "the notice repeats at the second written yes",
                "--exit", "a build with the fix: directed retest, then the QA decides")
        self.ok(q, "decide", "Q-1", "activate", "--decided-by", "QA", "--ref", "context.md §6")
        name = "batch_ios_20261113_30"
        self.ok(rl, "plan", name, self.plan(name, ["TC-SHOP-001"], excluded=["TC-SHOP-002"]))
        self.result(name, "TC-SHOP-001", "Pass")
        code, out = kit.run_approved(ap.main, [self.ws, f"runs/{name}.json", "--no-recalc"], dry_extra=("--detail",))
        self.assertEqual(code, 0, out)
        after = row("TC-SHOP-002")
        self.assertEqual({k: v for k, v in after.items() if k != "_row"}, {k: v for k, v in before.items() if k != "_row"})
        self.assertEqual(after[CFG["cases"]["columns"]["status"]], "Fail")                  # the earlier result stays
        self.assertEqual(row("TC-SHOP-001")[CFG["cases"]["columns"]["status"]], "Pass")
        text = q.render_report(q.report(self.ws, [name]))
        self.assertIn("TC-SHOP-002 in batch_ios_20261113_30: To Do — excluído por quarentena Q-1 · bug BUG-1 "
                      "https://dev.azure.com/example-org/Demo%20Shop/_workitems/edit/9770 · the notice repeats", text)
        # a retest that fails by the known reason links the workbook's defect: the report matches it to the quarantine
        retest = "batch_ios_20261113_31"
        self.ok(rl, "plan", retest, self.plan(retest, ["TC-SHOP-002"], retest=["TC-SHOP-002"]))
        self.result(retest, "TC-SHOP-002", "Fail", "--bug", "BUG-1")
        code, out = kit.run_approved(ap.main, [self.ws, f"runs/{retest}.json", "--no-recalc"], dry_extra=("--detail",))
        self.assertEqual(code, 0, out)                                          # the adapter accepts the link
        known = q.report(self.ws, [retest])["known_failures"]
        self.assertEqual([(x["case"], x["status"], x["linked_bug"]) for x in known], [("TC-SHOP-002", "Fail", "BUG-1")])


class PlatformLibrary(Base):
    def test_a_case_that_lives_only_in_the_platforms_own_workbook_can_be_proposed(self):
        import openpyxl
        path = os.path.join(self.ws, IOS)
        wb = openpyxl.load_workbook(path)
        ws = wb["Shop"]
        row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "TC-SHOP-004")
        ws.cell(row, 1).value = "TC-SHOP-099"                            # an iOS-only case (like a case added to iOS)
        wb.save(path)
        out = self.ok(q, "propose", "Q-9", "--bug", "BUG-1", "--tracker-bug", BUG, "--case", "TC-SHOP-099",
                      "--platform", "ios", "--reason", "r", "--exit", "e")
        self.assertIn("bug BUG-1 = 9770 in the tracker", out)
        self.assertEqual(self.run_(q, "propose", "Q-10", "--bug", "BUG-1", "--case", "TC-SHOP-098", "--reason", "r",
                                   "--exit", "e")[0], 2)

    def test_a_result_of_a_case_of_the_platforms_own_workbook_is_accepted_in_that_platforms_batch(self):
        import openpyxl
        path = os.path.join(self.ws, IOS)
        wb = openpyxl.load_workbook(path)
        ws = wb["Shop"]
        row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == "TC-SHOP-004")
        ws.cell(row, 1).value = "TC-SHOP-099"
        wb.save(path)
        self.result("batch_ios_20261113_40", "TC-SHOP-099", "Pass")                # the iOS batch: its own workbook
        code, out = self.run_(rl, "add", "batch_android_20261113_40", "TC-SHOP-099", "Pass", "x", "--environment",
                              "test", "--build", BUILD, "--mode", "manual: fictional")
        self.assertEqual(code, 2, out)                                           # not in the Android library
        self.assertIn("not in the project's library", out)


class InTheReports(Base):
    """qa_report.py report: the quarantine section is part of summary.md and technical.md, in the report's own scope,
    written in one step by the real entry point (13.11.2026)."""

    LINK = "https://dev.azure.com/example-org/Demo%20Shop/_workitems/edit/9770"

    def report(self, stem, *args):
        code, out = self.run_(qa_report, "report", "--name", stem, *args)
        self.assertIn(code, (0, 1), out)
        files = {}
        for kind in ("summary", "technical"):
            with open(os.path.join(self.ws, "exports", f"{stem}.{kind}.md"), encoding="utf-8") as f:
                files[kind] = f.read()
        return files

    def batch(self, name, cases, results, excluded=(), retest=()):
        plan = self.plan(name, cases, excluded=excluded, retest=retest)
        self.ok(rl, "plan", name, plan)
        for case, status, *extra in results:
            self.result(name, case, status, *extra)
        return plan

    def test_a_case_left_out_is_in_both_reports_with_its_reason_and_link_and_outside_the_run_counts(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_50"
        plan = self.batch(name, ["TC-SHOP-001", "TC-SHOP-003"], [("TC-SHOP-001", "Pass"), ("TC-SHOP-003", "Pass")],
                          excluded=["TC-SHOP-002"])
        files = self.report("r50", "--batch", name, "--plan", plan)
        for kind, text in files.items():
            with self.subTest(kind=kind):
                head, _, section = text.partition("## Quarantine")
                self.assertTrue(section, text)
                self.assertIn(f"- TC-SHOP-002 in {name}: To Do — excluded by quarantine Q-1 · bug 9770 {self.LINK} · "
                              "the notice repeats at the second written yes (2 of 3 runs)", section)
                self.assertIn("Left out by quarantine (1): not executed, outside the counts of run cases", section)
                self.assertNotIn("TC-SHOP-002", head)                               # never in the totals
                self.assertIn("2 Pass", head.replace("Pass: 2", "2 Pass"))
        self.assertIn("Cases with a current result: 2", files["technical"])

    def test_a_retest_that_fails_by_the_known_reason_stays_a_fail_linked_to_the_bug(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_51"
        self.batch(name, ["TC-SHOP-002"], [("TC-SHOP-002", "Fail", "--bug", BUG)], retest=["TC-SHOP-002"])
        files = self.report("r51", "--batch", name)
        self.assertIn("1 Fail", files["summary"])
        head, _, section = files["technical"].partition("## Quarantine")
        self.assertIn("Fail: 1", head)
        self.assertIn(f"- TC-SHOP-002 in {name}: Fail · quarantine Q-1 · bug 9770 {self.LINK}", section)
        self.assertIn("Known failures observed (1)", section)

    def test_a_retest_that_passes_stays_a_pass_and_the_qas_decision_is_pending(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_52"
        self.batch(name, ["TC-SHOP-002"], [("TC-SHOP-002", "Pass")], retest=["TC-SHOP-002"])
        files = self.report("r52", "--batch", name)
        self.assertIn("1 Pass", files["summary"])
        for text in files.values():
            self.assertIn("retest passed: the quarantine stays until the QA decides", text)
            self.assertIn("Decisions of the QA pending (1)", text)
            self.assertIn("the QA decides whether it is lifted (decide Q-1 lift)", text)
        # once the QA lifted it, the retest stays in the history and nothing waits on the QA any more
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA (session 14.11)", "--ref", "context.md §6")
        files = self.report("r52b", "--batch", name)
        self.assertIn("retest passed", files["technical"])
        self.assertNotIn("Decisions of the QA pending", files["summary"])

    def test_lifting_the_quarantine_later_never_erases_an_exclusion_already_recorded(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_53"
        self.batch(name, ["TC-SHOP-001"], [("TC-SHOP-001", "Pass")], excluded=["TC-SHOP-002"])
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA (session 14.11)", "--ref", "context.md §6")
        files = self.report("r53", "--batch", name)
        for text in files.values():
            self.assertIn(f"- TC-SHOP-002 in {name}: To Do — excluded by quarantine Q-1 (lifted since: the plan's "
                          "decision stays) · bug 9770", text)

    def test_another_batch_platform_or_period_shows_only_what_its_filters_cover(self):
        self.quarantine_ios()
        ios = "batch_ios_20261113_54"
        self.batch(ios, ["TC-SHOP-001"], [("TC-SHOP-001", "Pass")], excluded=["TC-SHOP-002"])
        other = "batch_ios_20261113_55"
        self.batch(other, ["TC-SHOP-003"], [("TC-SHOP-003", "Pass")])
        android = "batch_android_20261113_56"
        self.batch(android, ["TC-SHOP-002"], [("TC-SHOP-002", "Pass")])           # Q-1 is of iOS only
        for stem, args in (("r55", ("--batch", other)), ("r56", ("--batch", android)), ("rand", ("--platform", "android")),
                           ("rfut", ("--since", "2999-01-01"))):
            if stem == "rfut":
                code, out = self.run_(qa_report, "report", "--name", stem, *args)
                self.assertEqual(code, 0, out)
                with open(os.path.join(self.ws, "exports", f"{stem}.technical.md"), encoding="utf-8") as f:
                    files = {"technical": f.read()}
            else:
                files = self.report(stem, *args)
            for text in files.values():
                with self.subTest(stem=stem):
                    self.assertNotIn("## Quarantine", text)
        files = self.report("rall")                                                 # every run log: only the iOS one
        section = files["technical"].partition("## Quarantine")[2]
        self.assertIn(f"TC-SHOP-002 in {ios}", section)
        self.assertNotIn(android, section)
        self.assertNotIn(other, section)
        r = q.report_logs(self.ws, rl.read_project(self.ws), [(ios, rl.read_log(os.path.join(self.ws, "runs",
                                                                                                ios + ".json")))],
                          build="1.0.0 (41)")
        self.assertFalse(q.has_data(r))                                              # another build: out of scope
        r = q.report_logs(self.ws, rl.read_project(self.ws), [(ios, rl.read_log(os.path.join(self.ws, "runs",
                                                                                                ios + ".json")))],
                          build=BUILD, environment="test")
        self.assertEqual([x["case"] for x in r["excluded"]], ["TC-SHOP-002"])

    def test_the_same_report_again_gives_the_same_files_with_one_section(self):
        self.quarantine_ios()
        name = "batch_ios_20261113_57"
        self.batch(name, ["TC-SHOP-001"], [("TC-SHOP-001", "Pass")], excluded=["TC-SHOP-002"])
        first = self.report("r57", "--batch", name)
        second = self.report("r57", "--batch", name)
        self.assertEqual(first, second)
        for text in second.values():
            self.assertEqual(text.count("## Quarantine"), 1)

    def test_a_project_without_quarantines_keeps_the_reports_as_before(self):
        name = "batch_ios_20261113_58"
        plan = self.batch(name, ["TC-SHOP-001", "TC-SHOP-002"], [("TC-SHOP-001", "Pass")])
        files = self.report("r58", "--batch", name, "--plan", plan)
        proj = rl.read_project(self.ws)
        batches = qa_report.load_batches(self.ws, proj, [name])
        tot = qa_report.totals(self.ws, proj, batches, None, qa_report.planned_cases(self.ws, [plan]))
        aud = qa_report.audit(self.ws, proj, batches)
        scope = f"batches {name}, plan {plan}"
        self.assertEqual(files["summary"], qa_report.render_summary(tot, aud, scope, "exports/r58.technical.md"))
        self.assertNotIn("Quarantine", files["technical"])
        self.assertFalse(os.path.exists(self.register()))


class PlanRecordedBeforeTheFirstResult(Base):
    """A batch whose own plan (batches/<name>.yaml) carries the QA's decision (`approval:`, `excluded:`, `retest:`)
    records it in the run log before its first result (run_log.py add refuses until then, writing nothing); batches
    without such a plan are recorded as before (a simulation of 14.11.2026)."""

    def setUp(self):
        super().setUp()
        self.quarantine_ios()
        self.name = "batch_ios_20261114_plan_first"
        self.plan_file = self.plan(self.name, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        self.log = os.path.join(self.ws, "runs", self.name + ".json")

    def snapshot(self):
        if not os.path.isfile(self.log):
            return None
        with open(self.log, "rb") as f:
            return f.read()

    def test_a_result_before_the_plan_is_recorded_is_refused_and_the_run_log_stays_intact(self):
        self.assertIsNone(self.snapshot())
        code, out = self.run_(rl, "add", self.name, "TC-SHOP-001", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)
        self.assertIn(f"has an approved plan {self.plan_file} (excluded)", out)
        self.assertIn("nothing was written", out)
        self.assertIsNone(self.snapshot())                                  # no run log created
        self.ok(rl, "mode", self.name, "manual: fictional")                 # the mode may come first
        before = self.snapshot()
        progress_out = self.ok(rl, "progress", self.name)
        self.assertIn(f"Next: record the batch's plan before the first case: run_log.py <workspace> plan {self.name} "
                      f"{self.plan_file}", progress_out)
        code, out = self.run_(rl, "add", self.name, "TC-SHOP-001", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.snapshot(), before)                           # byte for byte

    def test_the_plan_recorded_first_shows_the_case_left_out_from_the_first_progress_and_survives_a_resume(self):
        self.ok(rl, "mode", self.name, "manual: fictional")
        self.ok(rl, "plan", self.name, self.plan_file)
        out = self.ok(rl, "progress", self.name)                           # before any result
        self.assertIn("▶ TC-SHOP-001  next", out)
        self.assertIn("⊘ TC-SHOP-002  not run: left out by quarantine Q-1", out)
        self.assertNotIn("PLAN TO REVALIDATE", out)                         # no run block yet: context not known
        self.result(self.name, "TC-SHOP-001", "Pass")
        with open(self.log, encoding="utf-8") as f:
            first = json.load(f)["results"][0]
        # resume: a new process reads the same run log; the plan recorded is reused, the first result kept
        info = rl.progress(self.ws, self.name)
        self.assertEqual((info["plan"], info["next"], info["excluded"], info["quarantine_problems"]),
                         (self.plan_file, "TC-SHOP-003", {"TC-SHOP-002": "Q-1"}, []))
        self.assertEqual(info["results"]["TC-SHOP-001"], first)
        code, out = self.run_(rl, "add", self.name, "TC-SHOP-002", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)                                      # still left out
        self.result(self.name, "TC-SHOP-003", "Pass")
        info = rl.progress(self.ws, self.name)
        self.assertEqual((sorted(info["results"]), info["next"]), (["TC-SHOP-001", "TC-SHOP-003"], None))
        self.assertEqual(info["results"]["TC-SHOP-001"], first)

    def test_a_resume_whose_context_no_longer_holds_goes_back_to_the_qa(self):
        self.ok(rl, "plan", self.name, self.plan_file)
        self.result(self.name, "TC-SHOP-001", "Pass")
        self.ok(q, "decide", "Q-1", "lift", "--decided-by", "QA (session 14.11)", "--ref", "context.md")
        out = self.ok(rl, "progress", self.name)
        self.assertIn("PLAN TO REVALIDATE (quarantine)", out)
        code, out = self.run_(rl, "add", self.name, "TC-SHOP-002", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)                                      # the exclusion is out of date: back to the QA
        self.assertIn("the plan is out of date", out)
        self.result(self.name, "TC-SHOP-003", "Pass")      # a case the problem does not touch goes on (run-tests step 1)

    def test_an_approval_alone_also_asks_for_the_plan_first(self):
        name = "batch_ios_20261114_approved"
        path = os.path.join(self.ws, "batches", name + ".yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001"], "approval": {"decided_by": "QA", "at": "2026-11-14"}}, f)
        code, out = self.run_(rl, "add", name, "TC-SHOP-001", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)
        self.assertIn("(approval)", out)

    def test_runs_without_a_plan_with_a_plan_without_decisions_or_already_recorded_are_as_before(self):
        self.result("batch_ios_20261114_adhoc", "TC-SHOP-001", "Pass")       # no batches/<name>.yaml
        self.plan("batch_ios_20261114_plain", ["TC-SHOP-001"])              # cases only: no decision
        self.result("batch_ios_20261114_plain", "TC-SHOP-001", "Pass")
        name = "batch_ios_20261114_old"                                      # an older batch, plan already recorded
        plan = self.plan(name, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        self.ok(rl, "plan", name, plan)
        self.result(name, "TC-SHOP-001", "Pass")
        self.assertIsNone(rl.plan_gate(self.ws, name, rl.read_log(rl.run_log_path(self.ws, name))))

    def test_before_the_first_result_the_plan_is_checked_in_the_context_it_was_approved_for(self):
        with open(os.path.join(self.ws, self.plan_file), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["run"] = {"platform": "ios", "build": BUILD, "environment": "test"}
        with open(os.path.join(self.ws, self.plan_file), "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)
        self.ok(rl, "mode", self.name, "manual: fictional")
        self.ok(rl, "plan", self.name, self.plan_file)
        out = self.ok(rl, "progress", self.name)
        self.assertNotIn("PLAN TO REVALIDATE", out)
        self.assertIn("Next: TC-SHOP-001", out)
        data["run"]["build"] = "1.0.0 (41)"            # the quarantine names build 39 only: the exclusion no longer holds
        with open(os.path.join(self.ws, self.plan_file), "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)
        out = self.ok(rl, "progress", self.name)
        self.assertIn("PLAN TO REVALIDATE (quarantine): TC-SHOP-002", out)

    def test_the_plan_is_found_with_the_name_run_tests_receives_with_or_without_the_batch_prefix(self):
        stem = "ios_20261114_noprefix"                    # batches/<name>.yaml as /plan-tests writes it
        plan = self.plan(stem, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        for given in (stem, "batch_" + stem):
            code, out = self.run_(rl, "add", given, "TC-SHOP-001", "Pass", "fictional", "--environment", "test",
                                  "--build", BUILD)
            self.assertEqual(code, 1, out)
            self.assertIn(f"approved plan {plan} (excluded)", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", f"batch_{stem}.json")))
        self.ok(rl, "plan", stem, plan)
        self.result(stem, "TC-SHOP-001", "Pass")
        self.assertEqual(rl.progress(self.ws, "batch_" + stem)["next"], "TC-SHOP-003")

    def test_another_plan_file_is_refused_for_a_batch_whose_own_plan_carries_the_qas_decision(self):
        other = os.path.join(self.ws, "analysis", "proposal.yaml")
        os.makedirs(os.path.dirname(other))
        with open(other, "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001", "TC-SHOP-003"]}, f)
        code, out = self.run_(rl, "plan", self.name, "analysis/proposal.yaml")
        self.assertEqual(code, 1, out)
        self.assertIn(f"has its own approved plan {self.plan_file}", out)
        self.assertFalse(os.path.exists(self.log))
        self.ok(rl, "plan", self.name, self.plan_file)              # its own plan is accepted
        name = "batch_ios_20261114_free"                             # no own plan: any plan file, as before
        self.ok(rl, "plan", name, "analysis/proposal.yaml")
        self.result(name, "TC-SHOP-001", "Pass")

    def test_two_own_plans_with_decisions_exclude_and_retest_are_refused_naming_both(self):
        a = self.plan("batch_ios_conflict", ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        b = self.plan("ios_conflict", ["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"], retest=["TC-SHOP-002"])
        both = f"{a} (excluded) and {b} (retest)"
        for given in ("ios_conflict", "batch_ios_conflict"):
            code, out = self.run_(rl, "add", given, "TC-SHOP-001", "Pass", "fictional", "--environment", "test",
                                  "--build", BUILD)
            self.assertEqual(code, 1, out)
            self.assertIn(both, out)
        for plan in (a, b):
            code, out = self.run_(rl, "plan", "ios_conflict", plan)
            self.assertEqual(code, 1, out)
            self.assertIn(both, out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", "batch_ios_conflict.json")))

    def test_each_variant_alone_is_valid_and_an_ad_hoc_retest_needs_no_plan(self):
        for stem in ("batch_ios_only_prefixed", "ios_only_plain"):
            plan = self.plan(stem, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
            self.ok(rl, "plan", stem, plan)
            self.result(stem, "TC-SHOP-001", "Pass")
            self.assertEqual(rl.progress(self.ws, stem)["plan_problem"], None)
        self.result("batch_ios_20261114_retest", "TC-SHOP-001", "Pass")        # no plan file: as before

    def test_a_second_plan_after_the_association_stops_the_run_and_keeps_the_results(self):
        stem = "ios_second"
        plan = self.plan(stem, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        self.ok(rl, "plan", stem, plan)
        self.result(stem, "TC-SHOP-001", "Pass")
        log = os.path.join(self.ws, "runs", f"batch_{stem}.json")
        with open(log, "rb") as f:
            before = f.read()
        other = self.plan("batch_" + stem, ["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"], retest=["TC-SHOP-002"])
        code, out = self.run_(rl, "add", stem, "TC-SHOP-003", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)
        self.assertIn(f"{other} (retest) and {plan} (excluded)", out)
        with open(log, "rb") as f:
            self.assertEqual(f.read(), before)                                # the result of 001 kept, byte for byte
        self.assertIn("PLAN TO RESOLVE", self.ok(rl, "progress", stem))
        os.remove(os.path.join(self.ws, other))                               # the QA keeps one
        self.result(stem, "TC-SHOP-003", "Pass")
        self.assertEqual(sorted(rl.progress(self.ws, stem)["results"]), ["TC-SHOP-001", "TC-SHOP-003"])

    def test_a_run_log_that_follows_another_plan_stops_when_its_own_plan_with_decisions_appears(self):
        free = os.path.join(self.ws, "analysis", "draft.yaml")
        os.makedirs(os.path.dirname(free))
        with open(free, "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001", "TC-SHOP-003"]}, f)
        stem = "ios_late_plan"
        self.ok(rl, "plan", stem, "analysis/draft.yaml")
        self.result(stem, "TC-SHOP-001", "Pass")
        own = self.plan(stem, ["TC-SHOP-001", "TC-SHOP-003"], excluded=["TC-SHOP-002"])
        code, out = self.run_(rl, "add", stem, "TC-SHOP-003", "Pass", "fictional", "--environment", "test",
                              "--build", BUILD)
        self.assertEqual(code, 1, out)
        self.assertIn(f"follows the plan analysis/draft.yaml, but its own plan {own}", out)
        self.ok(rl, "plan", stem, own)                                        # recording it keeps the result
        self.result(stem, "TC-SHOP-003", "Pass")
        self.assertEqual(sorted(rl.progress(self.ws, stem)["results"]), ["TC-SHOP-001", "TC-SHOP-003"])
