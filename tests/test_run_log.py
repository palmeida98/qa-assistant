"""scripts/run_log.py (plan 11.2; methodology 7.1): one item per case (appended or replaced), the project's
vocabulary (or Question), the run block, and a batch already applied never touched — Excel through the workbook's
control sheet (the fictional golden fixture, no LibreOffice), Jira / Azure DevOps through tracker/sent/.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

import run_log as rl
from build_fixture import build

from adapters.excel import ExcelAdapter

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 9, 29, 14, 3)


class RunLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        self.project(tracker="excel", excel=copy.deepcopy(CFG))

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, **proj):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"project": "demo-shop", "environments": {"test": "https://shop.demo"}, **proj}, f)

    def log(self, name="batch_20260929_01"):
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rl.main([self.ws, *args])
        return code, out.getvalue()

    def test_add_then_replace_the_same_case(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "cart shows 2 items", now=NOW)
        path, replaced = rl.add(self.ws, "batch_20260929_01", "TC-SHOP-002", "Fail", "coupon accepted",
                                bug="BUG-1", evidence=["evidence/b/TC-SHOP-002_t1.png"], environment="test",
                                build="1.4.0 (140)", now=NOW)
        self.assertFalse(replaced)
        _, replaced = rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Question", "is 2 the limit?", now=NOW)
        self.assertTrue(replaced)
        doc = self.log()
        self.assertEqual(doc["run"], {"environment": "test", "build": "1.4.0 (140)"})
        self.assertEqual(doc["results"], [
            {"tc": "TC-SHOP-001", "status": "Question", "actual_result": "is 2 the limit?",
             "ts": "2026-09-29T14:03:00"},
            {"tc": "TC-SHOP-002", "status": "Fail", "actual_result": "coupon accepted", "ts": "2026-09-29T14:03:00",
             "bug": "BUG-1", "evidence": "evidence/b/TC-SHOP-002_t1.png"}])
        self.assertEqual(path, os.path.join(self.ws, "runs", "batch_20260929_01.json"))

    def test_results_added_at_the_same_time_are_all_kept(self):
        # audit finding D-02: each writer read the file, added its case and wrote the whole file back over the others
        cases = [f"TC-{area}-{n:03d}" for area in ("ACCOUNT", "ADMIN") for n in range(1, 5)]
        errors = []

        def add(case):
            try:
                rl.add(self.ws, "20260929_01", case, "Pass", "ok", now=NOW)
            except Exception as e:      # noqa: BLE001 -- reported below, in the main thread
                errors.append(repr(e))

        threads = [threading.Thread(target=add, args=(c,)) for c in cases]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r["tc"] for r in self.log()["results"]), cases)

    def test_a_workspace_held_by_another_process_is_a_refusal_that_writes_nothing(self):
        import qa_ui_kit as kit
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, out = self.run_(*("add", "20260929_01", "TC-SHOP-001", "Pass", "ok"))
        self.assertEqual(code, 1)
        self.assertIn("another process", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", "batch_20260929_01.json")))

    def test_the_run_log_is_on_the_disk_before_it_takes_the_place_of_the_old_one(self):
        # audit finding D-06 (a risk): the run log is where the results live until the checkpoint
        events = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def replace(src, dst):
            events.append("replace")
            return real_replace(src, dst)

        with mock.patch.object(rl.os, "fsync", fsync), mock.patch.object(rl.os, "replace", replace):
            rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", now=NOW)
        self.assertEqual(events, ["fsync", "replace"])

    def test_an_unknown_case_is_refused_with_the_closest_ones(self):
        # audit finding D-12: TC-SHOP-099, which no library has, entered a real run log
        code, out = self.run_("add", "20260929_01", "TC-SHOP-099", "Pass", "ok")
        self.assertEqual(code, 2)
        self.assertIn("TC-SHOP-099", out)
        self.assertIn("not in the project's library", out)
        self.assertIn("did you mean TC-SHOP-00", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))
        self.assertEqual(self.run_("add", "20260929_01", " TC-SHOP-001 ", "Pass", "ok")[0], 0)   # blanks are not part of an ID

    def test_a_library_that_cannot_be_read_is_an_error_not_a_pass(self):
        os.remove(os.path.join(self.ws, CFG["file"]))
        code, out = self.run_("add", "20260929_01", "TC-SHOP-001", "Pass", "ok")
        self.assertEqual(code, 2)
        self.assertIn("library cannot be read", out)
        self.assertIn("nothing was written", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))

    def test_the_library_of_a_tracker_project_is_its_cases_file(self):
        self.project(tracker="jira", jira={"site": "https://demo.example", "project_key": "CHK"})
        code, out = self.run_("add", "20260929_01", "CHK-1", "Pass", "ok")
        self.assertEqual(code, 2)
        self.assertIn("tracker/cases.json", out)
        os.makedirs(os.path.join(self.ws, "tracker"))
        with open(os.path.join(self.ws, "tracker", "cases.json"), "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "CHK-1", "title": "x"}]}, f)
        code, out = self.run_("add", "20260929_01", "CHK-2", "Pass", "ok")
        self.assertEqual((code, "CHK-1" in out), (2, True))

    def test_remove_a_mistaken_result_and_keep_the_rest(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", evidence=["evidence/b/a.png"], now=NOW)
        rl.add(self.ws, "20260929_01", "TC-SHOP-002", "Fail", "wrong case", evidence=["evidence/b/b.png"], now=NOW)
        rl.add(self.ws, "20260929_01", "TC-SHOP-003", "Pass", "ok", environment="test", build="1.0 (1)", now=NOW)
        os.makedirs(os.path.join(self.ws, "evidence", "b"))
        for name in ("a.png", "b.png"):
            with open(os.path.join(self.ws, "evidence", "b", name), "wb") as f:
                f.write(b"x")
        before = self.log()
        code, out = self.run_("remove", "20260929_01", "TC-SHOP-002")
        self.assertEqual(code, 0, out)
        self.assertIn("wrong case", out)                                    # the result affected is shown
        self.assertIn("evidence/b/b.png", out)
        self.assertIn("not deleted", out)
        after = self.log()
        self.assertEqual([r["tc"] for r in after["results"]], ["TC-SHOP-001", "TC-SHOP-003"])
        self.assertEqual(after["results"], [r for r in before["results"] if r["tc"] != "TC-SHOP-002"])
        self.assertEqual(after["run"], before["run"])
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "evidence", "b", "b.png")))      # evidence stays
        self.assertEqual(self.run_("remove", "20260929_01", "TC-SHOP-002")[0], 2)             # no longer there
        self.assertEqual(self.run_("remove", "20260929_99", "TC-SHOP-001")[0], 2)             # no such batch

    def test_a_batch_already_applied_cannot_lose_a_result(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", now=NOW)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        ad.apply_batch(self.log()["results"], batch_id="batch_20260929_01")
        ad.save()
        before = self.log()
        code, out = self.run_("remove", "20260929_01", "TC-SHOP-001")
        self.assertEqual(code, 1)
        self.assertIn("already applied", out)
        self.assertEqual(self.log(), before)

    def test_removing_and_adding_at_the_same_time_loses_nothing(self):
        for case in ("TC-ACCOUNT-001", "TC-ACCOUNT-002"):
            rl.add(self.ws, "20260929_01", case, "Pass", "ok", now=NOW)
        errors = []

        def add(case):
            try:
                rl.add(self.ws, "20260929_01", case, "Pass", "ok", now=NOW)
            except Exception as e:      # noqa: BLE001 -- reported below
                errors.append(repr(e))

        def remove():
            try:
                rl.remove(self.ws, "20260929_01", "TC-ACCOUNT-001")
            except Exception as e:      # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=add, args=(c,)) for c in ("TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003")]
        threads.append(threading.Thread(target=remove))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r["tc"] for r in self.log()["results"]),
                         ["TC-ACCOUNT-002", "TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003"])

    def test_remove_waits_for_the_lock_and_writes_nothing_without_it(self):
        import qa_ui_kit as kit
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", now=NOW)
        before = self.log()
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, out = self.run_("remove", "20260929_01", "TC-SHOP-001")
        self.assertEqual(code, 1)
        self.assertIn("another process", out)
        self.assertEqual(self.log(), before)

    def test_the_build_of_a_batch_cannot_change_once_it_has_results(self):
        # the run block is the record of where the results came from
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-001", "Pass", "ok", "--environment", "test",
                                   "--build", "pt.demo.app 1.0.0 (33)")[0], 0)
        before = self.log()
        code, out = self.run_("add", "20260929_01", "TC-SHOP-002", "Pass", "ok", "--build", "pt.demo.app 1.0.0 (34)")
        self.assertEqual(code, 1)
        self.assertIn("build changed", out)
        self.assertIn("plan a new batch", out)
        self.assertEqual(self.log(), before)
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-002", "Pass", "ok", "--environment", "staging")[0], 1)
        # the same build, written another way, is the same build
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-002", "Pass", "ok", "--build", "1.0.0 (33)")[0], 0)
        self.assertEqual(self.log()["run"], before["run"])

    def test_the_build_can_still_be_set_before_any_result(self):
        # the interface writes the run block when a run starts, with no result yet
        with open(os.path.join(self.ws, "runs.tmp"), "w", encoding="utf-8"):
            pass
        os.makedirs(os.path.join(self.ws, "runs"))
        with open(os.path.join(self.ws, "runs", "batch_20260929_01.json"), "w", encoding="utf-8") as f:
            json.dump({"run": {"environment": "test", "build": "1.0 (1)"}, "results": []}, f)
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-001", "Pass", "ok", "--build", "1.0 (2)")[0], 0)
        self.assertEqual(self.log()["run"]["build"], "1.0 (2)")

    def test_the_command_line_with_a_new_defect(self):
        draft = os.path.join(self.ws, "draft.json")
        with open(draft, "w", encoding="utf-8") as f:
            json.dump({"title": "Expired coupon accepted", "severity": "High", "proposed": True}, f)
        code, out = self.run_("add", "20260929_01", "TC-SHOP-003", "Fail", "accepted", "--new-defect", draft)
        self.assertEqual(code, 0, out)
        self.assertIn("added: TC-SHOP-003 Fail", out)
        self.assertEqual(self.log()["results"][0]["new_defect"]["proposed"], True)
        with open(draft, "w", encoding="utf-8") as f:
            f.write("[1, 2")
        self.assertEqual(self.run_("add", "20260929_01", "TC-SHOP-003", "Fail", "x", "--new-defect", draft)[0], 2)

    def test_status_outside_the_vocabulary_is_2(self):
        code, out = self.run_("add", "20260929_01", "TC-SHOP-001", "Passed", "x")
        self.assertEqual(code, 2)
        self.assertIn("vocabulary", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))
        self.assertEqual(self.run_("add", "../x", "TC-SHOP-001", "Pass", "x")[0], 2)

    def test_a_batch_applied_to_the_workbook_is_never_touched(self):
        rl.add(self.ws, "20260929_01", "TC-SHOP-001", "Pass", "ok", now=NOW)
        ad = ExcelAdapter.from_project(self.ws)
        ad.load()
        ad.apply_batch(self.log()["results"], batch_id="batch_20260929_01")
        ad.save()
        before = self.log()
        code, out = self.run_("add", "20260929_01", "TC-SHOP-002", "Fail", "late correction")
        self.assertEqual(code, 1)
        self.assertIn("already applied to the workbook", out)
        self.assertIn("batch_20260929_02", out)                        # the name suggested for the correction
        self.assertEqual(self.log(), before)
        self.assertEqual(self.run_("add", "20260929_02", "TC-SHOP-002", "Fail", "late correction")[0], 0)

    def test_a_batch_sent_to_a_tracker_is_never_touched(self):
        self.project(tracker="jira", jira={"results": {"statuses": {"Pass": "PASSED", "Fail": "FAILED"}}})
        os.makedirs(os.path.join(self.ws, "tracker", "sent"))
        for name in ("batch_demo", "batch_demo_02"):
            with open(os.path.join(self.ws, "tracker", "sent", name + ".json"), "w", encoding="utf-8") as f:
                f.write("{}")
        code, out = self.run_("add", "demo", "DS-12", "Pass", "ok")
        self.assertEqual(code, 1)
        self.assertIn("sent to jira", out)
        self.assertIn("batch_demo_03", out)                            # _02 is taken too
        self.assertEqual(self.run_("add", "other", "DS-12", "Blocked", "x")[0], 2)      # not in the map
        with open(os.path.join(self.ws, "tracker", "cases.json"), "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "DS-12", "title": "x"}]}, f)                    # the library of a tracker project
        self.assertEqual(self.run_("add", "other", "DS-12", "Pass", "ok")[0], 0)


class ProjectStatus(unittest.TestCase):
    """The 11.8 pilot: a workflow wrote 'Blocked' in a project whose word is 'Bloqueado'."""
    PT = ("Por executar", "Passou", "Falhou", "Bloqueado", "N/A")

    def proj(self, statuses, states=None):
        return {"tracker": "excel", "excel": {"cases": {"statuses": list(statuses)}},
                **({"dashboard": {"states": states}} if states else {})}

    def test_the_project_word_from_dashboard_states(self):
        p = self.proj(self.PT, {"Por executar": "pending", "Passou": "ok", "Falhou": "fail", "Bloqueado": "blocked",
                                "N/A": "na"})
        self.assertEqual(rl.project_status(p, "blocked"), "Bloqueado")
        self.assertEqual(rl.project_status(p, "pass"), "Passou")
        self.assertEqual(rl.project_status(p, "not run"), "Por executar")

    def test_the_english_word_when_the_vocabulary_has_it_or_has_none(self):
        self.assertEqual(rl.project_status(self.proj(["Not run", "Pass", "Fail", "Blocked", "N/A"]), "blocked"),
                         "Blocked")
        self.assertEqual(rl.project_status({"tracker": "excel"}, "fail"), "Fail")

    def test_no_word_for_the_meaning_is_a_usage_error(self):
        with self.assertRaises(rl.UsageError) as e:
            rl.project_status(self.proj(self.PT), "blocked")
        self.assertIn("dashboard.states", str(e.exception))


if __name__ == "__main__":
    unittest.main()
