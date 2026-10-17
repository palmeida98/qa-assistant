"""qa-ui runs (docs/qa-ui.md sections 5.3, 7, 8 and 10; plan 12.3): a batch through Claude Code headless, the two
automated audits, stopping and the time limit, one run at a time, the output redacted; asking Claude; new cases and
applying a run log through the adapter, a dry run first. A fake `claude` and fake commands: no real session, browser
or site is ever used. Fictional data only (Demo Shop).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import qa_ui_kit as kit
import yaml
from common import read_project

from adapters.excel import ExcelAdapter
from adapters.excel.recalc import find_soffice
from qa_ui import runs as rn
from qa_ui import workspace as wk
from qa_ui.intake import new_case


def soffice() -> bool:
    try:
        return bool(find_soffice())
    except FileNotFoundError:
        return False


class Base(unittest.TestCase):
    modules = ("functional-web", "api")

    def setUp(self):
        self.tmp = kit.workspace(modules=list(self.modules))
        self.ws = self.tmp.name
        self.bin = tempfile.TemporaryDirectory()
        self.claude = kit.fake_claude(self.bin.name)
        self.record = os.path.join(self.bin.name, "record.json")
        self.proj = read_project(self.ws)
        self.env = mock.patch.dict(os.environ, {"QA_FAKE_RECORD": self.record, "QA_FAKE_CLAUDE": "ok"})
        self.env.start()
        self.runner = rn.Runner(self.ws, claude=self.claude, max_turns=40, timeout_minutes=1, grace=1)

    def tearDown(self):
        self.runner.shutdown()
        self.env.stop()
        self.bin.cleanup()
        self.tmp.cleanup()

    def batch(self, name="b1", cases=("TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"), module="functional-web"):
        os.makedirs(os.path.join(self.ws, "batches"), exist_ok=True)
        with open(os.path.join(self.ws, "batches", f"{name}.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": list(cases), **({"module": module} if module else {})}, f)

    def recorded(self) -> dict:
        with open(self.record, encoding="utf-8") as f:
            return json.load(f)

    def wait(self, run) -> dict:
        self.assertTrue(run.done.wait(30), "the run did not end")
        return self.runner.get(run.id)


class Batch(Base):
    def test_a_batch_runs_the_skill_headless_and_the_run_log_is_the_source(self):
        self.batch()
        out = self.wait(self.runner.start_batch(self.proj, "b1", "test", "2.4.1"))
        self.assertEqual((out["state"], out["code"], out["kind"], out["batch"]), ("finished", 0, "batch", "batch_b1"))
        log = wk.read_run_log(self.ws, self.proj, "b1")
        self.assertEqual(log["run"], {"environment": "test", "build": "2.4.1"})
        self.assertEqual([(i["tc"], i["status"]) for i in log["items"]],
                         [("TC-SHOP-001", "Pass"), ("TC-SHOP-002", "Fail"), ("TC-SHOP-003", "Question")])
        self.assertEqual([(n["kind"], n["tc"]) for n in log["needs"]],
                         [("defect", "TC-SHOP-002"), ("question", "TC-SHOP-003")])

    def test_the_command_is_the_one_of_the_specification(self):
        self.batch()
        self.wait(self.runner.start_batch(self.proj, "b1"))
        rec = self.recorded()
        args = rec["args"]
        self.assertEqual(os.path.realpath(rec["cwd"]), os.path.realpath(self.ws))
        self.assertEqual(args[:9], ["-p", "--max-turns", "40", "--permission-mode", "dontAsk", "--output-format",
                                    "stream-json", "--verbose", "--add-dir"])
        self.assertEqual(args[args.index("--disallowedTools") + 1], "WebFetch,WebSearch")
        qa = rn.posix(ROOT)
        self.assertEqual(args[args.index("--allowedTools") + 1].split(","), [
            "Read", "Glob", "Grep", "Skill", f"Bash(python {qa}/scripts/run_log.py *)", "Write(runs/**)",
            "Edit(runs/**)", "Write(evidence/**)", "Edit(evidence/**)"])
        self.assertNotIn("--mcp-config", args)                          # this workspace has no .mcp.json
        self.assertFalse(any("TC-SHOP" in a for a in args))             # the prompt never goes on the command line

    def test_the_prompt_has_the_skill_the_paths_and_the_cases_text(self):
        self.batch(cases=("TC-SHOP-001", "TC-SHOP-002"))
        self.wait(self.runner.start_batch(self.proj, "b1"))
        prompt = self.recorded()["prompt"]
        self.assertTrue(prompt.startswith("/run-batch\n"))
        self.assertIn(f"Workspace: {rn.posix(self.ws)} (the current folder)", prompt)
        self.assertIn("Cases, in this order: TC-SHOP-001 TC-SHOP-002\n", prompt)
        self.assertIn("Run log: runs/batch_b1.json", prompt)
        self.assertIn("Limit: 40 turns.", prompt)
        self.assertIn("### TC-SHOP-001 — Add item to cart", prompt)
        self.assertIn("Expected result: (none in the library: mark it Question)", prompt)
        self.assertIn('`"proposed": true`', prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn(kit.SECRET, prompt)

    def test_each_module_gets_its_skill_its_scripts_and_the_mcp(self):
        with open(os.path.join(self.ws, ".mcp.json"), "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.82"]}}}, f)
        qa = rn.posix(ROOT)
        self.batch("web")
        self.wait(self.runner.start_batch(self.proj, "web"))
        args = self.recorded()["args"]
        self.assertIn("mcp__playwright", args[args.index("--allowedTools") + 1].split(","))
        self.assertEqual(args[args.index("--mcp-config") + 1], os.path.join(self.ws, ".mcp.json"))
        self.batch("api", module="api")
        self.wait(self.runner.start_batch(self.proj, "api"))
        rec = self.recorded()
        tools = rec["args"][rec["args"].index("--allowedTools") + 1].split(",")
        self.assertIn(f"Bash(python {qa}/scripts/api_request.py *)", tools)
        self.assertNotIn("mcp__playwright", tools)
        self.assertNotIn("--mcp-config", rec["args"])
        self.assertTrue(rec["prompt"].startswith("/test-api\n"))
        self.assertEqual(rn.allowed_tools("mobile", "/qa", [])[-2:],
                         ["Bash(python /qa/scripts/device.py *)", "Bash(python /qa/scripts/run_mobile.py *)"])
        self.assertEqual(rn.mcp_servers(self.bin.name), [])

    def test_the_output_is_rendered_redacted_and_stored(self):
        self.batch()
        out = self.wait(self.runner.start_batch(self.proj, "b1"))
        self.assertEqual(out["tail"][0], "Reading the workspace. The password is «SHOP_PASSWORD».")
        self.assertEqual(out["tail"][1], "> Bash python qa/scripts/run_log.py ws add batch_b1 TC-SHOP-001 Pass")
        self.assertEqual(out["tail"][-2:], ["Batch finished.", "[success: 7 turn(s), 4 s]"])
        with open(os.path.join(self.ws, ".qa-ui", "runs", out["id"] + ".log"), encoding="utf-8") as f:
            stored = f.read()
        self.assertEqual(stored.splitlines(), out["tail"])
        self.assertNotIn(kit.SECRET, stored + json.dumps(out))

    def test_a_failure_keeps_the_exit_code_and_the_raw_line(self):
        self.batch()
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "fail"}):
            out = self.wait(self.runner.start_batch(self.proj, "b1"))
        self.assertEqual((out["state"], out["code"], out["reason"]), ("failed", 3, "exit code 3"))
        self.assertEqual(out["tail"][-1], "a line that is not an event")
        self.assertEqual(wk.read_run_log(self.ws, self.proj, "b1")["items"], [])

    def test_one_run_at_a_time_and_stop_keeps_what_was_written(self):
        self.batch()
        self.batch("b2")
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "slow"}):
            run = self.runner.start_batch(self.proj, "b1")
            with self.assertRaises(rn.Refused) as cm:
                self.runner.start_batch(self.proj, "b2")
            self.assertIn("a run is in progress", str(cm.exception))
            with self.assertRaises(rn.Refused):
                self.runner.start_audit(self.proj, "site")
            self.assertEqual(self.runner.get(run.id)["state"], "running")
            out = self.runner.stop(run.id)
        self.assertEqual((out["state"], out["reason"]), ("stopped", "stopped by the user"))
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "runs", "batch_b1.json")))
        self.assertEqual(self.runner.stop(run.id)["state"], "stopped")       # stopping again changes nothing
        self.assertEqual(self.wait(self.runner.start_batch(self.proj, "b2"))["state"], "finished")
        listed = self.runner.listing()
        self.assertEqual(sorted((r["batch"], r["state"], r["tail"]) for r in listed),
                         [("batch_b1", "stopped", []), ("batch_b2", "finished", [])])

    @unittest.skipIf(os.name == "nt", "signals of a process group: POSIX")
    def test_a_command_that_ignores_the_interrupt_is_killed(self):
        self.batch()
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "stubborn"}):
            run = self.runner.start_batch(self.proj, "b1")
            while not self.runner.get(run.id)["lines"]:
                run.done.wait(0.05)
            out = self.runner.stop(run.id)
        self.assertTrue(run.done.wait(10))
        self.assertEqual(self.runner.get(run.id)["state"], "stopped")
        self.assertIn(out["state"], ("running", "stopped"))

    def test_the_time_limit_interrupts_the_run(self):
        self.batch()
        runner = rn.Runner(self.ws, claude=self.claude, timeout_minutes=0.02, grace=1)
        self.addCleanup(runner.shutdown)
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "slow"}):
            run = runner.start_batch(self.proj, "b1")
            self.assertTrue(run.done.wait(30))
        self.assertEqual((run.state, run.reason), ("stopped", "the time limit was reached"))

    def test_what_cannot_start_says_why(self):
        self.batch()
        self.batch("nomodule", module=None)
        self.batch("mobile", module="mobile")
        self.batch("unknown", cases=("TC-SHOP-001", "NEW-1"))
        for name, kwargs, text in (
                ("nope", {}, "batches/nope.yaml does not exist"),
                ("nomodule", {}, "has no `module:`"),
                ("mobile", {}, "`mobile` is not in `modules:`"),
                ("unknown", {}, "NEW-1: not in the library"),
                ("b1", {"environment": "production"}, "is not a key of environments")):
            with self.subTest(name=name), self.assertRaises((rn.Refused, rn.NotFound)) as cm:
                self.runner.start_batch(self.proj, name, **kwargs)
            self.assertIn(text, str(cm.exception))
        with self.assertRaises(rn.Refused) as cm:
            self.runner.start_batch(dict(self.proj, tracker="jira"), "b1", "test", None)
        self.assertIn("the run block is required", str(cm.exception))
        with self.assertRaises(rn.Refused) as cm:
            rn.Runner(self.ws, claude=os.path.join(self.bin.name, "missing")).start_batch(self.proj, "b1")
        self.assertIn("Claude Code was not found", str(cm.exception))
        with self.assertRaises(rn.NotFound):
            self.runner.get("nope")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))     # nothing was created by a refusal

    def test_a_batch_already_applied_is_not_run_again(self):
        self.batch(cases=("TC-SHOP-001",))
        self.wait(self.runner.start_batch(self.proj, "b1"))
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        with open(os.path.join(self.ws, "runs", "batch_b1.json"), encoding="utf-8") as f:
            ad.apply_batch(json.load(f)["results"], batch_id="batch_b1")
        ad.save()
        with self.assertRaises(rn.Refused) as cm:
            self.runner.start_batch(self.proj, "b1")
        self.assertIn("plan a new batch", str(cm.exception))


class Events(unittest.TestCase):
    def test_render_event_is_tolerant(self):
        for line, expected in (
                ("plain text", None), ("[1, 2]", None), ('{"no": "type"}', None),
                ('{"type": "system", "subtype": "init"}', []),
                ('{"type": "assistant"}', []), ('{"type": "assistant", "message": {"content": "text"}}', []),
                ('{"type": "assistant", "message": {"content": [{"type": "text", "text": "a\\nb"}, 3]}}', ["a", "b"]),
                (json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "project.yaml"}}]}}),
                 ["> Read project.yaml"]),
                ('{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill"}]}}', ["> Skill"]),
                ('{"type": "stream_event", "event": {}}', []),
                ('{"type": "result", "result": "done"}', ["[result: ? turn(s)]"]),
                (json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                             "result": "out of turns", "num_turns": 40}),
                 ["out of turns", "[error_max_turns: 40 turn(s)]"])):
            with self.subTest(line=line):
                self.assertEqual(rn.render_event(line), expected)
        long = rn.render_event(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "x" * 300}}]}}))[0]
        self.assertEqual(len(long), len("> Bash ") + 201)

    def test_a_prompt_template_with_an_unknown_placeholder_is_refused(self):
        with self.assertRaises(ValueError):
            rn.template("batch.md", {"skill": "/run-batch"})
        text = rn.template("ask.md", {"notes": "{{cases}}", "cases": "[]", "proposals_rule": "none"})
        self.assertIn("{{cases}}", text)                         # a value is never read again for placeholders


class Audits(Base):
    modules = ("functional-web", "accessibility")

    def test_the_accessibility_audit_runs_the_script_with_a_free_report_name(self):
        os.makedirs(os.path.join(self.ws, "runs"))
        open(os.path.join(self.ws, "runs", f"a11y_{rn.datetime.now():%Y%m%d}_01.json"), "w").close()
        with mock.patch.object(rn.Runner, "_spawn", autospec=True, side_effect=lambda self, run, *a, **k: run) as sp:
            run = self.runner.start_audit(self.proj, "a11y")
        self.assertEqual(run.command[1:4], [os.path.join(ROOT, "scripts", "audit_a11y.py"), self.runner.ws,
                                            "--output"])
        self.assertRegex(run.command[4], r"^runs/a11y_\d{8}_02\.json$")
        self.assertEqual((run.kind, run.report), ("audit-a11y", run.command[4]))
        self.assertEqual(sp.call_args.args[2:], (ROOT, None))          # from the root of qa-assistant, no input
        with self.assertRaises(rn.Refused):
            self.runner.start_audit(dict(self.proj, modules=["functional-web"]), "a11y")

    def test_the_site_audit_runs_the_engine_on_the_test_environment_only(self):
        fake = kit.executable(self.bin.name, "qa-audit", "import sys\nprint('audited', sys.argv[1:])\n")
        with mock.patch.object(rn.shutil, "which", return_value=fake):
            out = self.wait(self.runner.start_audit(self.proj, "site"))
        self.assertEqual((out["state"], out["kind"], out["report"]), ("finished", "audit-site", "qa-output/"))
        self.assertEqual(out["tail"], [f"audited {['https://shop.example/', '--type', rn.SITE_AUDIT_TYPES]}"])
        several = dict(self.proj, environments={"staging": "https://s.shop.example/", "uat": "https://u.shop.example/"})
        with self.assertRaises(rn.Refused) as cm:
            self.runner.start_audit(several, "site")
        self.assertIn("several environments", str(cm.exception))
        with mock.patch.object(rn.shutil, "which", return_value=None), self.assertRaises(rn.Refused):
            self.runner.start_audit(self.proj, "site")
        with self.assertRaises(rn.Refused):
            self.runner.start_audit(self.proj, "everything")


class Ask(Base):
    CASES = (new_case({"id": "CHK-011", "title": "Pay with a valid card", "expected": "order created"}, "text"),
             new_case({"id": "API-002", "title": "POST /orders without a token", "expected": "401"}, "text"))

    def test_the_reading_is_read_strictly(self):
        out = self.runner.ask_claude(list(self.CASES), ["The checkout has a new payment form."], proposals=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["assignments"], {"CHK-011": {"module": "functional-web", "why": "read by the fake"},
                                              "API-002": {"module": "api", "why": "read by the fake"}})
        self.assertEqual(out["gaps"], [{"id": "CHK-011", "question": "Which message is expected?"},
                                       {"id": None, "question": "A gap about nothing given"}])
        self.assertEqual(len(out["proposals"]), 1)
        p = out["proposals"][0]
        self.assertEqual((p["id"], p["title"], p["technique"], p["in_library"], p["flags"], p["why"]),
                         ("NEW-1", "Pay with an expired card", "Equivalence partitioning", False,
                          ["not-in-library"], "the refused path"))

    def test_the_command_allows_no_tool_and_the_prompt_goes_through_the_input(self):
        self.runner.ask_claude(list(self.CASES), [])
        rec = self.recorded()
        self.assertEqual(rec["args"], ["-p", "--max-turns", "2", "--permission-mode", "dontAsk", "--output-format",
                                       "json", "--disallowedTools", "WebFetch,WebSearch"])
        self.assertNotIn("--allowedTools", rec["args"])
        self.assertIn('"id": "CHK-011"', rec["prompt"])
        self.assertIn('The user did not ask for new cases: "proposals" is an empty list.', rec["prompt"])
        self.assertEqual(self.runner.ask_claude(list(self.CASES), [])["proposals"], [])     # not asked for: never taken

    def test_a_fenced_answer_is_read_and_anything_else_changes_nothing(self):
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "ask-fenced"}):
            self.assertTrue(self.runner.ask_claude(list(self.CASES), [])["ok"])
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "ask-bad"}):
            out = self.runner.ask_claude(list(self.CASES), [])
        self.assertEqual(out["ok"], False)
        self.assertIn("mostly web tests", out["text"])
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "ask-fail"}):
            out = self.runner.ask_claude(list(self.CASES), [])
        self.assertEqual((out["ok"], out["code"], out["text"]), (False, 1, "not logged in"))
        with self.assertRaises(rn.Refused):
            self.runner.ask_claude([], [])
        self.assertIsNone(rn.parse_reading('{"assignments": "none"}', set(), False))
        self.assertIsNone(rn.parse_reading("[]", set(), False))


class Adapters(Base):
    NEW = ({"id": "TC-SHOP-005", "title": "Pay with an expired card", "steps": "1. Add an item\n- Pay",
            "expected": "The payment is refused", "role": "customer"},)

    def test_model_case_has_the_steps_as_a_list(self):
        self.assertEqual(rn.model_case(self.NEW[0]), {
            "id": "TC-SHOP-005", "title": "Pay with an expired card", "area": None, "preconditions": None,
            "expected": "The payment is refused", "role": "customer", "platform": None, "priority": None,
            "technique": None, "module": None, "origin": None,
            "steps": [{"action": "Add an item", "data": None, "expected": None},
                      {"action": "Pay", "data": None, "expected": None}]})
        self.assertIsNone(rn.model_case({"id": "X-1", "title": "t"})["steps"])

    def test_new_cases_dry_run_first_and_nothing_is_written_to_the_workbook(self):
        book = os.path.join(self.ws, kit.EXCEL["file"])
        before = os.path.getmtime(book)
        out = self.runner.new_cases(self.proj, list(self.NEW))
        self.assertEqual((out["dry_run"], out["code"], out["can_confirm"]), (True, 0, True))
        self.assertRegex(out["file"], r"^cases/cases_\d{8}_01\.json$")
        self.assertIn("TC-SHOP-005", out["output"])
        self.assertEqual(os.path.getmtime(book), before)
        with open(os.path.join(self.ws, *out["file"].split("/")), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cases"][0]["id"], "TC-SHOP-005")
        # a new dry run replaces the file of the one that was never confirmed
        again = self.runner.new_cases(self.proj, list(self.NEW))
        self.assertEqual(again["file"], out["file"])
        self.assertEqual(os.listdir(os.path.join(self.ws, "cases")), [os.path.basename(out["file"])])

    def test_a_refused_dry_run_leaves_no_file_and_cannot_be_confirmed(self):
        out = self.runner.new_cases(self.proj, [{"id": "TC-SHOP-001", "title": "Add item to cart"}])
        self.assertEqual((out["code"], out["can_confirm"], out["file"]), (4, False, None))
        self.assertIn("already exists", out["output"])
        self.assertIn("refused", out["meaning"])
        self.assertEqual(os.listdir(os.path.join(self.ws, "cases")), [])
        for confirm in ("cases/cases_20261008_01.json", "../project.yaml"):
            with self.subTest(confirm=confirm), self.assertRaises(rn.Refused):
                self.runner.new_cases(self.proj, confirm=confirm)
        with self.assertRaises(rn.Refused):
            self.runner.new_cases(self.proj, [])
        with self.assertRaises(rn.Refused):
            self.runner.new_cases(dict(self.proj, tracker="other"), list(self.NEW))

    @unittest.skipUnless(soffice(), "needs LibreOffice (the workbook's recalculation)")
    def test_the_confirmation_adds_the_cases_through_the_adapter(self):
        dry = self.runner.new_cases(self.proj, list(self.NEW))
        out = self.runner.new_cases(self.proj, confirm=dry["file"])
        self.assertEqual((out["dry_run"], out["code"], out["done"], out["meaning"]), (False, 0, True, "added"), out)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        self.assertEqual(ad.case_rows()["Shop"][-1]["ID"], "TC-SHOP-005")
        with self.assertRaises(rn.Refused):
            self.runner.new_cases(self.proj, confirm=dry["file"])                # confirmed once

    def test_apply_is_refused_while_something_needs_the_user(self):
        self.batch()
        self.wait(self.runner.start_batch(self.proj, "b1"))
        out = self.runner.apply(self.proj, "b1", confirm=True)
        self.assertEqual((out["ready"], out["done"], out["code"]), (False, False, None))
        self.assertEqual(len(out["problems"]), 2)

    @unittest.skipUnless(soffice(), "needs LibreOffice (the workbook's recalculation)")
    def test_apply_checks_first_and_writes_only_with_the_confirmation(self):
        self.batch()
        self.wait(self.runner.start_batch(self.proj, "b1"))
        wk.edit_item(self.ws, self.proj, "b1", "TC-SHOP-002", "approve")
        wk.edit_item(self.ws, self.proj, "b1", "TC-SHOP-003", "answer", {"status": "Pass", "actual_result": "ok"})
        book = os.path.join(self.ws, kit.EXCEL["file"])
        before = os.path.getmtime(book)
        out = self.runner.apply(self.proj, "b1")
        self.assertEqual((out["ready"], out["dry_run"], out["done"]), (True, True, False))
        self.assertEqual(out["summary"]["new_defects"], 1)
        self.assertEqual(os.path.getmtime(book), before)
        out = self.runner.apply(self.proj, "b1", confirm=True)
        self.assertEqual((out["done"], out["code"], out["meaning"]), (True, 0, "applied"), out)
        self.assertIn("applied to the workbook", wk.read_run_log(self.ws, self.proj, "b1")["applied"])
        again = self.runner.apply(self.proj, "b1", confirm=True)
        self.assertEqual((again["ready"], again["done"]), (False, False))

    def test_the_tracker_dry_run_then_yes(self):
        calls = []

        def once(self_, cmd, cwd, timeout, stdin_text=None):
            calls.append(cmd[1:])
            return (0 if "--yes" in cmd else 1), "plan: 1 result"

        ready = {"name": "batch_b1", "ready": True, "problems": [], "tracker": "jira", "summary": {}}
        proj = dict(self.proj, tracker="jira")
        path = os.path.join(self.runner.ws, "runs", "batch_b1.json")
        with mock.patch.object(rn.Runner, "_once", once), mock.patch.object(wk, "check_apply", return_value=ready):
            dry = self.runner.apply(proj, "b1")
            real = self.runner.apply(proj, "b1", confirm=True)
        self.assertEqual(calls, [["-m", "adapters.tracker", self.runner.ws, "apply", path],
                                 ["-m", "adapters.tracker", self.runner.ws, "apply", path, "--yes"]])
        self.assertEqual((dry["ready"], dry["dry_run"], dry["done"], dry["meaning"]),
                         (True, True, False, "dry run: nothing sent yet"))
        self.assertEqual((real["done"], real["meaning"]), (True, "sent, or nothing to send"))

class MobileProject(Base):
    """The first real batch: exploratory charters of a mobile project ran with the browser's tools only, and the
    agent was refused the device on its first command. They run with the mobile scripts."""
    modules = ("mobile", "exploratory")

    def test_exploratory_charters_run_on_the_app_with_the_mobile_tools(self):
        with open(os.path.join(self.ws, ".mcp.json"), "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.82"]}}}, f)
        qa = rn.posix(ROOT)
        self.batch("charters", module="exploratory")
        self.wait(self.runner.start_batch(self.proj, "charters"))
        rec = self.recorded()
        tools = rec["args"][rec["args"].index("--allowedTools") + 1].split(",")
        self.assertIn(f"Bash(python {qa}/scripts/device.py *)", tools)
        self.assertNotIn("mcp__playwright", tools)
        self.assertNotIn("--mcp-config", rec["args"])
        self.assertTrue(rec["prompt"].startswith("/test-mobile\n"))
        self.assertIn("Module: exploratory, charters on the mobile app", rec["prompt"])

    def test_with_a_web_module_too_the_charters_stay_in_the_browser(self):
        self.assertEqual(rn.allowed_tools("exploratory", "/qa", ["playwright"]),
                         rn.allowed_tools("functional-web", "/qa", ["playwright"]))
        self.assertEqual(rn.allowed_tools("exploratory", "/qa", ["playwright"], on_app=True),
                         rn.allowed_tools("mobile", "/qa", ["playwright"]))


if __name__ == "__main__":
    unittest.main()
