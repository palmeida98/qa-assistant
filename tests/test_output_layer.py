"""What the scripts print (phase 17.4): a short card by default, the complete text on request, the details in a file bound
to the same token, and nothing that needs a decision lost to a line limit. scripts/output.py and its users: the
preflight, the dry run of the apply and of add_cases, run_log (a result, the plan, the progress) and the device check.

Everything is fictional (the golden fixture's workbook, the demo workspace, a temporary workspace and home).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import hashlib
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

import device as dv
import output
import run_log as rl
import workspace_select as ws_select
from build_fixture import build

from adapters.excel import add_cases as ac
from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 8, 19, 43, 0)
LONG = "A result observed in full, with every step and every quote, " * 12          # more than 700 characters


class OutputModule(unittest.TestCase):
    def test_the_default_is_a_short_card_and_the_project_the_environment_and_the_flag_change_it_in_that_order(self):
        self.assertEqual(output.settings({}, environ={})["terminal"], "brief")
        project = {"output": {"terminal": "normal", "max_lines": 20}}
        self.assertEqual(output.settings(project, environ={})["terminal"], "normal")
        self.assertEqual(output.settings(project, environ={"QA_OUTPUT": "brief"})["terminal"], "brief")
        self.assertEqual(output.settings(project, "brief", environ={"QA_OUTPUT": "verbose"})["terminal"], "brief")
        self.assertEqual(output.settings(project, environ={})["max_lines"], 20)

    def test_an_invalid_section_is_reported_by_the_validator_and_ignored_by_the_scripts(self):
        bad = {"terminal": "loud", "max_cols": 10, "max_lines": "many", "truncate": "x", "detail_files": 1,
               "reports": ["summary", "slides"], "extra": True}
        problems = output.validate(bad)
        self.assertEqual(len(problems), 7, problems)
        self.assertEqual(output.settings({"output": bad}, environ={})["terminal"], "brief")      # the default stays
        self.assertEqual(output.validate({"terminal": "brief", "reports": ["summary"]}), [])
        self.assertEqual(output.validate(None), [])
        self.assertEqual(output.validate({"terminal": "normal"}), [])

    def test_a_must_line_is_never_dropped_cut_or_counted_and_what_is_left_out_says_how_to_read_it(self):
        lines = [("info 1", False), ("PROBLEM: " + "x" * 300, True), ("info 2", False), ("info 3", False),
                 ("BLOCKED: decide", True)]
        out = output.fit(lines, 1, "--detail", 40)
        self.assertIn("PROBLEM: " + "x" * 300, out)                          # whole, past the width
        self.assertIn("BLOCKED: decide", out)
        self.assertEqual([x for x in out if x.startswith("info")], ["info 1"])
        self.assertIn("2 more line(s): --detail", out[-1])
        self.assertEqual(len(output.fit(lines, 10, None, 40)), 5)

    def test_a_value_is_never_cut_without_saying_where_it_is_complete(self):
        out = output.clip("word " * 60, 40, "runs/x.dryrun.md")
        self.assertLessEqual(len(out), 60)
        self.assertTrue(out.endswith("(full: runs/x.dryrun.md)"))
        self.assertEqual(output.clip("short", 40, "x"), "short")


class NoticesThatDoNotChange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"QA_ASSISTANT_HOME": os.path.join(self.tmp.name, "home")}
        self.ws = os.path.join(self.tmp.name, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def notices(self, mode="brief"):
        return output.Notices(self.ws, {"terminal": mode}, self.env)

    def test_a_notice_is_shown_once_again_when_it_changes_and_always_when_it_needs_an_action(self):
        self.assertTrue(self.notices().show("build", "the observed build differs"))
        self.assertFalse(self.notices().show("build", "the observed build differs"))       # a later command
        self.assertTrue(self.notices().show("build", "the observed build differs: 39"))    # it changed
        self.assertTrue(self.notices().show("build", "the observed build differs: 39", must=True))

    def test_the_complete_modes_show_everything_and_remember_nothing(self):
        for mode in ("normal", "verbose"):
            self.assertTrue(self.notices(mode).show("a", "same"))
            self.assertTrue(self.notices(mode).show("a", "same"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "home")))

    def test_the_state_is_outside_the_workspace_and_per_workspace(self):
        self.notices().show("a", "text")
        self.assertEqual(os.listdir(self.ws), [])
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        self.assertTrue(output.Notices(other, {"terminal": "brief"}, self.env).show("a", "text"))

    def test_the_unit_tests_never_write_in_the_users_home(self):
        self.assertNotEqual(output.home({}), os.path.abspath(os.path.expanduser("~/.qa-assistant")))


class Preflight(unittest.TestCase):
    demo = os.path.join(ROOT, "examples", "demo")

    def run_(self, *extra):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"QA_ASSISTANT_HOME": self.home}), contextlib.redirect_stdout(out):
            code = ws_select.main(["preflight", "--skill", "run-tests", "--workspace", self.demo, *extra])
        return code, out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_short_card_keeps_the_contract_lines_and_is_much_shorter_than_the_complete_text(self):
        code, brief = self.run_()
        _, full = self.run_("--detail")
        self.assertEqual(code, 0)
        for needed in ("Configuration: valid for this skill", "Route: ", "Next: ", "Ready"):
            self.assertIn(needed, brief, needed)
        self.assertLess(len(brief.splitlines()), len(full.splitlines()))
        self.assertIn("--detail", brief)
        for needed in ("Tools:", "Framework:", "Approvals:"):
            self.assertIn(needed, full, needed)

    def test_a_warning_that_does_not_change_is_not_repeated_and_the_complete_text_has_it_always(self):
        _, first = self.run_()
        _, second = self.run_()
        self.assertIn("Writes:", first)
        self.assertNotIn("Writes:", second)
        self.assertNotIn("Task:", second)
        self.assertIn("Route: ", second)                                       # the contract lines stay
        self.assertIn("Writes:", self.run_("--detail")[1])

    def test_the_json_report_is_unchanged(self):
        code, out = self.run_("--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skill"]["name"], "run-tests")


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.xlsx = os.path.join(self.ws, CFG["file"])
        build(self.xlsx)
        self.proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel",
                     "excel": copy.deepcopy(CFG), "report": {"platforms": {"android": ["batch_a*"]}}}
        self.save_project()
        os.makedirs(os.path.join(self.ws, "runs"))
        os.makedirs(os.path.join(self.ws, "cases"))
        os.makedirs(os.path.join(self.ws, "batches"))

    def tearDown(self):
        self.tmp.cleanup()

    def save_project(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f)

    def cli(self, module, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = module.main([self.ws, *args])
        return code, out.getvalue()

    def batch(self, name, results, run=None):
        path = os.path.join(self.ws, "runs", name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"run": run or {"environment": "test", "build": "1.4.0 (140)"}, "results": results}, f)
        return "runs/" + name + ".json"

    def result(self, case, status, text="seen", ts="2026-11-08T19:20:00"):
        return {"tc": case, "status": status, "actual_result": text, "ts": ts}

    def digests(self):
        out = {}
        for folder, _, files in os.walk(self.ws):
            for n in files:
                with open(os.path.join(folder, n), "rb") as f:
                    out[os.path.relpath(os.path.join(folder, n), self.ws).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
        return out


class DryRunOfTheApply(Workspace):
    def dry(self, path, *extra):
        return self.cli(ap, path, "--dry-run", "--no-recalc", "--now", NOW.isoformat(), *extra)

    def test_the_card_is_short_and_the_review_file_holds_every_value_in_full_with_the_same_token(self):
        path = self.batch("batch_a_01", [self.result("TC-SHOP-001", "Pass", LONG)])
        before = self.digests()
        code, card = self.dry(path)
        self.assertEqual(code, 0, card)
        self.assertLessEqual(len(card.splitlines()), 8, card)
        self.assertNotIn(LONG[:200], card)                                    # the long value is not in the card
        token = next(w for w in card.split() if w.startswith("v2-")).rstrip(".")
        review = os.path.join(self.ws, "runs", "batch_a_01.dryrun.md")
        with open(review, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(LONG, text)                                             # complete: no cut
        self.assertIn(f"**Token: `{token}`**", text)
        with open(os.path.join(self.ws, path), "rb") as f:
            self.assertIn(hashlib.sha256(f.read()).hexdigest(), text)
        self.assertEqual(ap.dry_run(self.ws, path, recalc=False, now=NOW)["token"], token)
        changed = {k for k, v in self.digests().items() if before.get(k) != v}
        self.assertEqual(changed, {"runs/batch_a_01.dryrun.md"})            # the review is the only thing written

    def test_the_apply_command_in_the_card_is_whole_and_authorises_the_apply(self):
        path = self.batch("batch_a_01", [self.result("TC-SHOP-001", "Pass")])
        _, card = self.dry(path, "--label", "[review]")
        line = next(x for x in card.splitlines() if x.startswith("Apply, after approving"))
        command = line.split(": ", 1)[1]
        self.assertIn("--expect v2-", command)
        self.assertIn("--no-recalc", command)
        self.assertIn('--label "[review]"', command)                          # the options the token covers
        token = command.split("--expect ")[1].split()[0]
        code, out = self.cli(ap, path, "--no-recalc", "--now", NOW.isoformat(), "--label", "[review]", "--expect", token)
        self.assertEqual(code, 0, out)

    def test_problems_warnings_and_the_command_survive_a_tiny_limit(self):
        self.proj["output"] = {"max_lines": 3}
        self.save_project()
        self.cli(ap, self.batch("batch_a_00", [self.result("TC-SHOP-004", "Fail", "first")]), "--no-recalc")
        path = self.batch("batch_a_01", [self.result("TC-SHOP-004", "Pass", "second", ts="2026-11-08T20:30:00")])
        _, card = self.dry(path)
        self.assertIn("Warning: the batch replaces the earlier result text of 1 case(s) (TC-SHOP-004)", card)
        self.assertIn("Review (every value before", card)
        self.assertIn("--expect v2-", card)
        bad = self.batch("batch_a_02", [self.result("TC-NOPE-001", "Pass")])
        code, card = self.dry(bad)
        self.assertEqual(code, 4)
        self.assertIn("PROBLEM:", card)

    def test_the_complete_text_is_one_flag_or_one_setting_away_and_writes_no_file(self):
        path = self.batch("batch_a_01", [self.result("TC-SHOP-001", "Pass")])
        before = self.digests()
        _, full = self.dry(path, "--detail")
        self.assertIn("Cells that would change", full)
        self.assertEqual(self.digests(), before)
        self.proj["output"] = {"terminal": "normal"}
        self.save_project()
        before = self.digests()
        _, full = self.dry(path)
        self.assertIn("Cells that would change", full)
        self.assertEqual(self.digests(), before)

    def test_without_detail_files_the_complete_text_is_printed_because_there_is_nowhere_to_point_to(self):
        self.proj["output"] = {"detail_files": False}
        self.save_project()
        path = self.batch("batch_a_01", [self.result("TC-SHOP-001", "Pass")])
        before = self.digests()
        _, out = self.dry(path)
        self.assertIn("Cells that would change", out)
        self.assertEqual(self.digests(), before)

    def test_the_json_output_is_unchanged(self):
        path = self.batch("batch_a_01", [self.result("TC-SHOP-001", "Pass")])
        code, out = self.dry(path, "--json")
        self.assertEqual(code, 0)
        self.assertIn("token", json.loads(out))


class DryRunOfAddCases(Workspace):
    def setUp(self):
        super().setUp()
        self.cases = {"cases": [{"id": "TC-SHOP-005", "title": "Remove item from cart", "expected": LONG},
                                {"id": "TC-ADMIN-005", "title": "Archive users"}]}
        with open(os.path.join(self.ws, "cases", "cases_01.json"), "w", encoding="utf-8") as f:
            json.dump(self.cases, f)
        self.proj["excel"]["cases"]["fields"]["expected"] = "Actual Result"        # a mapped column, to hold a long value
        self.save_project()

    def test_the_card_is_short_and_the_review_file_holds_every_cell_in_full(self):
        before = self.digests()
        code, card = self.cli(ac, "cases/cases_01.json", "--dry-run", "--no-recalc")
        self.assertEqual(code, 0, card)
        self.assertLessEqual(len(card.splitlines()), 8, card)
        self.assertIn("--expect v1-", card)
        self.assertNotIn(LONG[:200], card)
        with open(os.path.join(self.ws, "cases", "cases_01.dryrun.md"), encoding="utf-8") as f:
            text = f.read()
        token = next(w for w in card.split() if w.startswith("v1-"))
        self.assertIn(f"**Token: `{token}`**", text)
        self.assertIn(LONG, text)
        self.assertEqual({k for k, v in self.digests().items() if before.get(k) != v}, {"cases/cases_01.dryrun.md"})
        code, out = self.cli(ac, "cases/cases_01.json", "--no-recalc", "--expect", token)       # the token authorises it
        self.assertEqual(code, 0, out)


class ResultsAndProgress(Workspace):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.ws, "batches", "plan.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"]}, f)

    def add(self, case, status="Pass", *extra, batch="a_01"):
        return self.cli(rl, "add", batch, case, status, "seen", "--environment", "test", "--build", "1.4.0 (140)", *extra)

    def test_a_result_is_one_line_with_the_platform_the_evidence_and_what_comes_next(self):
        self.assertEqual(self.cli(rl, "plan", "a_01", "batches/plan.yaml")[0], 0)
        code, out = self.add("TC-SHOP-001", "Pass", "--evidence", "evidence/a.png", "evidence/b.png")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "added: [android] TC-SHOP-001 Pass · 2 evidence path(s) · " + os.path.join("runs", "batch_a_01.json") + " "
                                      "· next: TC-SHOP-002")
        self.assertNotIn(self.ws, out)                                         # a relative path, not an absolute one
        code, out = self.add("TC-SHOP-001", "Pass", "--detail", batch="a_02")
        self.assertIn(f"added: TC-SHOP-001 Pass in {self.ws}", out)           # the complete line, as before

    def test_a_replaced_result_is_still_printed_in_full_because_it_is_kept_nowhere_else(self):
        self.add("TC-SHOP-001", "Fail")
        code, out = self.add("TC-SHOP-001", "Pass", "--replace")
        self.assertEqual(code, 0, out)
        self.assertIn("replaced: [android] TC-SHOP-001 Pass", out)
        self.assertIn("previous result (kept only in this output and in git history)", out)
        self.assertIn('"status": "Fail"', out)

    def test_the_plan_is_checked_against_the_library_and_a_second_plan_is_a_change(self):
        with open(os.path.join(self.ws, "batches", "bad.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": ["TC-SHOP-001", "TC-NOPE-1"]}, f)
        before = self.digests()
        code, out = self.cli(rl, "plan", "a_01", "batches/bad.yaml")
        self.assertEqual(code, 2)
        self.assertIn("TC-NOPE-1", out)
        self.assertEqual(self.digests(), before)
        self.assertEqual(self.cli(rl, "plan", "a_01", "batches/plan.yaml")[0], 0)
        self.assertIn("unchanged", self.cli(rl, "plan", "a_01", "batches/plan.yaml")[1])

    def test_the_progress_shows_each_case_the_counts_and_the_next_step(self):
        self.cli(rl, "plan", "a_01", "batches/plan.yaml")
        self.add("TC-SHOP-001", "Pass")
        self.add("TC-SHOP-002", "Fail")
        code, out = self.cli(rl, "progress", "a_01")
        self.assertEqual(code, 0, out)
        for needed in ("PROGRESS batch_a_01 · android · build 1.4.0 (140)", "✓ TC-SHOP-001  Pass", "✓ TC-SHOP-002  Fail",
                       "▶ TC-SHOP-003  next", "Results: 1 Pass · 1 Fail · 1 to run", "Next: TC-SHOP-003"):
            self.assertIn(needed, out)

    def test_a_result_that_is_not_a_pass_is_never_dropped_by_the_limit(self):
        self.proj["output"] = {"max_lines": 3}
        self.save_project()
        self.cli(rl, "plan", "a_01", "batches/plan.yaml")
        self.add("TC-SHOP-001", "Pass")
        self.add("TC-SHOP-002", "Fail")
        out = self.cli(rl, "progress", "a_01")[1]
        self.assertIn("TC-SHOP-002  Fail", out)
        self.assertIn("Next: TC-SHOP-003", out)

    def test_every_planned_case_with_a_result_points_to_the_bugs_and_the_checkpoint(self):
        self.cli(rl, "plan", "a_01", "batches/plan.yaml")
        for case in ("TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"):
            self.add(case)
        self.assertIn("every planned case has a result", self.cli(rl, "progress", "a_01")[1])

    def test_without_a_plan_the_progress_says_how_to_record_one_and_the_plan_never_changes_the_apply(self):
        self.add("TC-SHOP-001")
        self.assertIn("no plan recorded", self.cli(rl, "progress", "a_01")[1])
        self.cli(rl, "plan", "a_01", "batches/plan.yaml")
        res = ap.dry_run(self.ws, os.path.join(self.ws, "runs", "batch_a_01.json"), recalc=False, now=NOW)
        self.assertEqual((res["problems"], res["code"]), ([], 0))


class DeviceCheckAdvice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"workspace": self.tmp.name}
        with open(os.path.join(self.tmp.name, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"project": "demo", "environments": {"test": "https://x.example"}}, f)
        self.args = mock.Mock(brief=False, detail=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_same_advice_is_not_repeated_at_every_check_and_a_new_build_brings_it_back(self):
        self.assertTrue(dv._notice(self.cfg, self.args, "build-differs:38:37", "confirm the build"))
        self.assertFalse(dv._notice(self.cfg, self.args, "build-differs:38:37", "confirm the build"))
        self.assertTrue(dv._notice(self.cfg, self.args, "build-differs:39:37", "confirm the build"))

    def test_the_complete_text_always_has_the_advice(self):
        self.args.detail = True
        self.assertTrue(dv._notice(self.cfg, self.args, "build-differs:38:37", "confirm the build"))
        self.assertTrue(dv._notice(self.cfg, self.args, "build-differs:38:37", "confirm the build"))


if __name__ == "__main__":
    unittest.main()
