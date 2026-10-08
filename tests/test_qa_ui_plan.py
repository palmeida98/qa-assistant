"""qa-ui batch plan and the workspace's state (docs/qa-ui.md sections 6, 9 and 10; plan 12.2): the batches per
module, area and role, the split at 20, the cases listed apart, the names and the files; the run logs with what
needs the user, each edit and each refusal, and the check before applying. Fictional data only (Demo Shop).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest
from datetime import date, datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import qa_ui_kit as kit
import run_log as rl
import workspace_ci
from common import read_project

from adapters.excel import ExcelAdapter
from qa_ui import plan as pl
from qa_ui import workspace as wk
from qa_ui.intake import intake, new_case
from qa_ui.suggest import suggest

TODAY = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 10, 42)
DEFECT = {"title": "Expired coupon accepted", "description": "The discount is applied", "severity": "High",
          "priority": "P2", "area": "Shop", "proposed": True}


def case(id_: str, title: str = "A title", **fields) -> dict:
    c = new_case({"id": id_, "title": title, "expected": "something observable", **fields}, "text")
    c["in_library"] = True
    return c


def assignments(cases, module="functional-web") -> dict:
    return {c["id"]: {"module": c.get("_module", module)} for c in cases}


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, cases, cards=("functional-web", "api"), asg=None) -> dict:
        return pl.build(self.ws, cases, asg or assignments(cases), list(cards), TODAY)

    def test_batches_per_module_area_and_role_in_the_inputs_order(self):
        cases = [case("CHK-001", area="Checkout", role="customer"), case("LOG-001", area="Login", role="customer"),
                 case("CHK-002", area="Checkout", role="admin"), case("CHK-003", area="Checkout", role="customer"),
                 dict(case("API-001"), _module="api"), case("CHK-004", area="Checkout")]
        out = self.build(cases)
        self.assertEqual([(b["name"], b["module"], b["area"], b["role"], b["cases"]) for b in out["batches"]], [
            ("20261008_web_01", "functional-web", "Checkout", "customer", ["CHK-001", "CHK-003"]),
            ("20261008_web_02", "functional-web", "Login", "customer", ["LOG-001"]),
            ("20261008_web_03", "functional-web", "Checkout", "admin", ["CHK-002"]),
            ("20261008_web_04", "functional-web", "Checkout", None, ["CHK-004"]),
            ("20261008_api_01", "api", "API", None, ["API-001"])])
        self.assertEqual((out["selected"], out["planned"], out["apart"]), (6, 6, []))
        self.assertEqual(out["batches"][0]["skill"], "/run-batch")

    def test_the_area_falls_back_to_the_id_up_to_its_last_dash(self):
        self.assertEqual([pl.area_of(case(i)) for i in ("TC-LOGIN-001", "CHK-7", "X1")], ["TC-LOGIN", "CHK", "X1"])
        self.assertEqual(pl.area_of(case("CHK-7", area="Checkout")), "Checkout")

    def test_a_group_larger_than_20_is_split_and_nothing_is_lost(self):
        cases = [case(f"CHK-{n:03d}", area="Checkout") for n in range(1, 46)]
        out = self.build(cases)
        self.assertEqual([len(b["cases"]) for b in out["batches"]], [20, 20, 5])
        self.assertEqual([i for b in out["batches"] for i in b["cases"]], [c["id"] for c in cases])
        self.assertEqual((out["selected"], out["planned"]), (45, 45))

    def test_only_the_ticked_cards_and_never_the_audits(self):
        cases = [case("CHK-001"), dict(case("API-001"), _module="api"), dict(case("MOB-001"), _module="mobile")]
        out = self.build(cases, cards=("api", "audit-site", "audit-a11y"))
        self.assertEqual([(b["module"], b["cases"]) for b in out["batches"]], [("api", ["API-001"])])
        self.assertEqual(out["selected"], 1)
        self.assertEqual(self.build(cases, cards=())["batches"], [])

    def test_a_case_that_is_not_in_the_library_is_listed_apart_and_the_counts_add_up(self):
        new = dict(case("NEW-1", "A new idea"), in_library=False)
        out = self.build([case("CHK-001"), new])
        self.assertEqual([b["cases"] for b in out["batches"]], [["CHK-001"]])
        self.assertEqual([(a["id"], a["title"]) for a in out["apart"]], [("NEW-1", "A new idea")])
        self.assertIn("not in the library", out["apart"][0]["reason"])
        self.assertEqual(out["planned"] + len(out["apart"]), out["selected"])

    def test_warnings_for_an_unreadable_library_and_ids_the_ci_does_not_take(self):
        unknown = dict(case("chk_1"), in_library=None)
        out = self.build([unknown])
        self.assertEqual([b["cases"] for b in out["batches"]], [["chk_1"]])
        self.assertIn("the library could not be read", out["warnings"][0])
        self.assertIn("chk_1", out["warnings"][1])
        self.assertEqual(self.build([case("CHK-001")])["warnings"], [])

    def test_the_cases_that_will_become_a_question_are_named(self):
        vague = new_case({"id": "LOG-004", "title": "Login", "expected": "generic error"}, "text")
        vague["in_library"] = True
        out = self.build([case("LOG-001"), vague])
        self.assertEqual(out["batches"][0]["questions"], ["LOG-004"])

    def test_names_skip_what_exists_in_batches_and_runs(self):
        os.makedirs(os.path.join(self.ws, "batches"))
        os.makedirs(os.path.join(self.ws, "runs"))
        open(os.path.join(self.ws, "batches", "20261008_web_01.yaml"), "w").close()
        open(os.path.join(self.ws, "runs", "batch_20261008_web_02.json"), "w").close()
        out = self.build([case("CHK-001", area="A"), case("LOG-001", area="B")])
        self.assertEqual([b["name"] for b in out["batches"]], ["20261008_web_03", "20261008_web_04"])

    def test_nothing_is_written_before_save_and_the_file_is_what_plan_batch_writes(self):
        out = self.build([case("CHK-001", role="customer"), dict(case("API-001"), _module="api")])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches")))
        paths = pl.save(self.ws, out)
        self.assertEqual([os.path.basename(p) for p in paths], ["20261008_web_01.yaml", "20261008_api_01.yaml"])
        with open(paths[0], encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f), {"cases": ["CHK-001"], "module": "functional-web",
                                                 "role": "customer"})
        with open(paths[1], encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f), {"cases": ["API-001"], "module": "api"})
        # the workspace's CI reads the same file (scripts/workspace_ci.py)
        self.assertEqual(workspace_ci.resolve_batch(self.ws, "20261008_web_01"), ("20261008_web_01", ["CHK-001"]))

    def test_save_refuses_a_name_taken_meanwhile_or_not_valid(self):
        out = self.build([case("CHK-001")])
        pl.save(self.ws, out)
        with self.assertRaises(pl.PlanError) as cm:
            pl.save(self.ws, out)
        self.assertIn("exists since the plan was built", str(cm.exception))
        with self.assertRaises(pl.PlanError):
            pl.save(self.ws, {"batches": [{"name": "../x", "module": "api", "cases": ["API-001"]}]})
        with self.assertRaises(pl.PlanError):
            pl.save(self.ws, {"batches": [{"name": "empty", "module": "api", "cases": []}]})
        self.assertEqual(os.listdir(os.path.join(self.ws, "batches")), ["20261008_web_01.yaml"])

    def test_from_the_library_to_a_saved_plan(self):
        proj = read_project(self.ws)
        got = intake(self.ws, proj, library=True)
        asg = suggest(got["cases"], proj, [], "claude")["assignments"]
        out = pl.build(self.ws, got["cases"], asg, ["functional-web"], TODAY)
        self.assertEqual([(b["area"], len(b["cases"])) for b in out["batches"]],
                         [("Shop", 4), ("Account", 4), ("Admin", 4)])
        self.assertEqual((out["selected"], out["planned"]), (12, 12))
        pl.save(self.ws, out)
        listed = wk.batches(self.ws)
        self.assertEqual([b["name"] for b in listed],
                         ["20261008_web_03", "20261008_web_02", "20261008_web_01"])
        self.assertEqual((listed[0]["module"], listed[0]["valid"], listed[0]["run_log"]),
                         ("functional-web", True, False))
        self.assertEqual(wk.batch(self.ws, "20261008_web_01")["cases"],
                         [f"TC-SHOP-{n:03d}" for n in range(1, 5)])
        with self.assertRaises(wk.NotFound):
            wk.batch(self.ws, "nope")


class RunLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.ws = self.tmp.name
        self.proj = read_project(self.ws)
        rl.add(self.ws, "b1", "TC-SHOP-001", "Pass", "cart shows 1 item", now=NOW)
        rl.add(self.ws, "b1", "TC-SHOP-002", "Fail", "the expired coupon is accepted", new_defect=dict(DEFECT),
               evidence=["evidence/b1/TC-SHOP-002.txt"], now=NOW)
        rl.add(self.ws, "b1", "TC-SHOP-003", "Question", "which message is expected?", now=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self) -> dict:
        with open(os.path.join(self.ws, "runs", "batch_b1.json"), encoding="utf-8") as f:
            return json.load(f)

    def item(self, case: str) -> dict:
        return next(x for x in self.log()["results"] if x["tc"] == case)

    def edit(self, case, action, **data) -> dict:
        return wk.edit_item(self.ws, self.proj, "b1", case, action, data, now=NOW)

    def test_what_the_page_shows_of_a_run_log(self):
        out = wk.read_run_log(self.ws, self.proj, "b1")
        self.assertEqual(out["name"], "batch_b1")
        self.assertEqual(out["counts"], [{"status": "Pass", "n": 1}, {"status": "Fail", "n": 1},
                                         {"status": "Question", "n": 1}])
        self.assertEqual([(n["kind"], n["tc"]) for n in out["needs"]],
                         [("defect", "TC-SHOP-002"), ("question", "TC-SHOP-003")])
        self.assertEqual(out["statuses"], ["To Do", "Pass", "Fail", "Blocked", "N/A"])
        self.assertIsNone(out["applied"])
        listed = wk.run_logs(self.ws, self.proj)
        self.assertEqual([(x["name"], x["batch"], x["total"], x["needs"]) for x in listed], [("batch_b1", "b1", 3, 2)])
        with self.assertRaises(wk.NotFound):
            wk.read_run_log(self.ws, self.proj, "nope")
        with self.assertRaises(wk.NotFound):
            wk.read_run_log(self.ws, self.proj, "../project")

    def test_a_run_log_that_is_not_valid_json_is_listed_with_its_error(self):
        with open(os.path.join(self.ws, "runs", "batch_bad.json"), "w", encoding="utf-8") as f:
            f.write("{")
        bad = next(x for x in wk.run_logs(self.ws, self.proj) if x["name"] == "batch_bad")
        self.assertIn("not valid JSON", bad["error"])
        with self.assertRaises(wk.Refused):
            wk.read_run_log(self.ws, self.proj, "bad")

    def test_approve_removes_only_the_proposed_key(self):
        out = self.edit("TC-SHOP-002", "approve")
        self.assertEqual(self.item("TC-SHOP-002")["new_defect"], {k: v for k, v in DEFECT.items() if k != "proposed"})
        self.assertEqual(self.item("TC-SHOP-002")["evidence"], "evidence/b1/TC-SHOP-002.txt")
        self.assertEqual([n["kind"] for n in out["needs"]], ["question"])
        with self.assertRaises(wk.Refused) as cm:
            self.edit("TC-SHOP-002", "approve")
        self.assertIn("already approved", str(cm.exception))

    def test_edit_changes_text_fields_and_never_approves(self):
        self.edit("TC-SHOP-002", "edit", fields={"title": " Expired coupon is accepted ", "priority": "P1",
                                                 "description": ""})
        new = self.item("TC-SHOP-002")["new_defect"]
        self.assertEqual((new["title"], new["priority"], new["proposed"]), ("Expired coupon is accepted", "P1", True))
        self.assertNotIn("description", new)
        for fields in ({"proposed": "false"}, {"title": 3}, {}, None):
            with self.subTest(fields=fields), self.assertRaises(wk.Refused):
                self.edit("TC-SHOP-002", "edit", fields=fields)
        self.assertTrue(self.item("TC-SHOP-002")["new_defect"]["proposed"])

    def test_link_replaces_the_new_defect_with_an_existing_bug(self):
        with self.assertRaises(wk.Refused):
            self.edit("TC-SHOP-002", "link", bug="  ")
        self.edit("TC-SHOP-002", "link", bug="BUG-1")
        item = self.item("TC-SHOP-002")
        self.assertEqual((item["bug"], item["status"]), ("BUG-1", "Fail"))
        self.assertNotIn("new_defect", item)

    def test_drop_removes_the_new_defect_and_keeps_the_result(self):
        self.edit("TC-SHOP-002", "drop")
        item = self.item("TC-SHOP-002")
        self.assertEqual((item["status"], item["actual_result"]), ("Fail", "the expired coupon is accepted"))
        self.assertNotIn("new_defect", item)
        with self.assertRaises(wk.Refused):
            self.edit("TC-SHOP-002", "drop")

    def test_answer_reclassifies_a_question_with_the_projects_vocabulary(self):
        for data in ({"status": "Passed", "actual_result": "x"}, {"status": "Question", "actual_result": "x"},
                     {"status": "Pass", "actual_result": " "}, {"status": "", "actual_result": "x"}):
            with self.subTest(data=data), self.assertRaises(wk.Refused):
                self.edit("TC-SHOP-003", "answer", **data)
        out = self.edit("TC-SHOP-003", "answer", status="Pass", actual_result="The same message for both fields")
        self.assertEqual(self.item("TC-SHOP-003"), {
            "tc": "TC-SHOP-003", "status": "Pass", "actual_result": "The same message for both fields",
            "ts": "2026-10-08T10:42:00", "question": "which message is expected?"})
        self.assertEqual([n["kind"] for n in out["needs"]], ["defect"])
        with self.assertRaises(wk.Refused):
            self.edit("TC-SHOP-001", "answer", status="Fail", actual_result="x")     # not in Question

    def test_what_does_not_exist_or_is_not_an_action(self):
        with self.assertRaises(wk.NotFound):
            self.edit("TC-SHOP-999", "approve")
        with self.assertRaises(wk.NotFound):
            wk.edit_item(self.ws, self.proj, "nope", "TC-SHOP-001", "approve")
        with self.assertRaises(wk.Refused):
            self.edit("TC-SHOP-001", "approve")                                      # no new_defect
        with self.assertRaises(wk.Refused):
            self.edit("TC-SHOP-002", "apply")
        self.assertEqual(self.item("TC-SHOP-002")["new_defect"], DEFECT)

    def test_check_apply_names_what_is_left_and_then_what_the_adapter_says(self):
        out = wk.check_apply(self.ws, self.proj, "b1")
        self.assertFalse(out["ready"])
        self.assertEqual(len(out["problems"]), 2)
        self.assertIn('TC-SHOP-002: the new_defect still has "proposed"', out["problems"][0])
        self.assertIn("TC-SHOP-003: still Question", out["problems"][1])
        self.edit("TC-SHOP-003", "answer", status="Pass", actual_result="ok")
        self.edit("TC-SHOP-002", "edit", fields={"priority": "Urgent"})
        self.edit("TC-SHOP-002", "approve")
        out = wk.check_apply(self.ws, self.proj, "b1")                              # the adapter's own validation
        self.assertFalse(out["ready"])
        self.assertIn("Urgent", " ".join(out["problems"]))
        self.edit("TC-SHOP-002", "edit", fields={"priority": "P2"})
        out = wk.check_apply(self.ws, self.proj, "b1")
        self.assertEqual((out["ready"], out["problems"], out["tracker"]), (True, [], "excel"))
        self.assertEqual(out["summary"], {"results": 3, "new_defects": 1, "linked_bugs": 0,
                                          "target": "example-plan.xlsx"})

    def test_an_applied_batch_is_never_edited(self):
        self.edit("TC-SHOP-003", "answer", status="Pass", actual_result="ok")
        self.edit("TC-SHOP-002", "approve")
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        ad.apply_batch(self.log()["results"], batch_id="batch_b1")
        ad.save()
        before = self.log()
        with self.assertRaises(wk.Refused) as cm:
            self.edit("TC-SHOP-002", "edit", fields={"title": "late"})
        self.assertIn("a correction goes to a new batch", str(cm.exception))
        self.assertEqual(self.log(), before)
        self.assertIn("applied to the workbook", wk.read_run_log(self.ws, self.proj, "b1")["applied"])
        self.assertIn("already applied", wk.check_apply(self.ws, self.proj, "b1")["problems"][0])
        with self.assertRaises(wk.Refused):
            wk.create_run_log(self.ws, self.proj, "b1", "test", "2.4.1")

    def test_create_run_log_writes_the_run_block_and_keeps_what_is_there(self):
        path = wk.create_run_log(self.ws, self.proj, "b2", "test", "2.4.1")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"run": {"environment": "test", "build": "2.4.1"}, "results": []})
        wk.create_run_log(self.ws, self.proj, "b1", "test", None)
        self.assertEqual(self.log()["run"], {"environment": "test"})
        self.assertEqual(len(self.log()["results"]), 3)
        with self.assertRaises(wk.Refused):
            wk.create_run_log(self.ws, self.proj, "../x", None, None)

    def test_state_has_names_and_never_a_value_of_env(self):
        out = wk.state(self.ws, self.proj)
        self.assertEqual(out["project"]["name"], "demo-shop")
        self.assertEqual(out["project"]["environments"], ["test"])
        self.assertEqual(out["project"]["statuses"], ["To Do", "Pass", "Fail", "Blocked", "N/A"])
        self.assertEqual(out["env"], [{"name": "SHOP_PASSWORD", "set": True}, {"name": "SHOP_USER", "set": True}])
        text = json.dumps(out)
        self.assertNotIn(kit.SECRET, text)
        self.assertNotIn("shop.example", text)        # an environment's URL is not sent either: only its key

    def test_readiness_is_the_list_of_validate_project_plus_qa_audit(self):
        checks = wk.readiness(self.ws, self.proj)
        tools = [tool for _, tool, _ in checks]
        self.assertEqual(tools[:4], ["git", "python", ".env", "skills"])
        self.assertEqual(tools.count("qa-audit"), 1)
        self.assertNotIn(kit.SECRET, json.dumps(checks))


if __name__ == "__main__":
    unittest.main()
