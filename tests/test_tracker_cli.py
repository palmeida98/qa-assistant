"""python -m adapters.tracker (technique-jira-xray.md sections 2.2-2.5; plan 10.1): dry run by default and --yes,
the exit codes, the record of what was sent (same content: no request; other content: exit 4; incomplete: continue),
the ID cache, the stub back-ends and the redaction of what is printed. A fake back-end over the fake transport, in
temporary Demo Shop workspaces. No network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

from tracker_kit import (
    AZURE,
    ENV,
    PROJECT,
    FakeJira,
    Workspace,
    readback_response,
    resolve_response,
    results,
    write_response,
)

from adapters.tracker import cli
from adapters.tracker.errors import ConfigError, InvalidInput
from adapters.tracker.record import (
    IdCache,
    SentRecord,
    read_cases,
    safe_path,
    write_cases,
)
from adapters.tracker.testing import FakeTransport

BATCH = results(("DS-1", "Pass"), ("DS-2", "Fail"), ("DS-3", "Blocked"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.lines = []

    def run_cli(self, *args, responses=(), fail_at=None, backend=FakeJira):
        self.fake = FakeTransport(list(responses), fail_at=fail_at)
        self.lines = []
        code = cli.main([self.ws.path, *args], backend=backend, transport=self.fake, sleep=lambda s: None,
                        printer=self.lines.append)
        return code

    @property
    def out(self):
        return "\n".join(self.lines)

    def send_responses(self, n, differences=()):
        return [resolve_response()] + [write_response(i) for i in range(1, n + 1)] + [readback_response(differences)]

    # -- dry run and --yes ------------------------------------------------------------------------------------------

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        code = self.run_cli("apply", path, responses=[resolve_response()])
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.writes(), [])
        self.assertIn("dry run: nothing was sent", self.out)
        self.assertIn("POST /fake/results — DS-1: PASSED", self.out)
        self.assertIn("DS-3 (Blocked): mapped to skip", self.out)
        self.assertFalse(self.ws.exists("tracker/sent/batch_20260928_01.json"))

    def test_yes_sends_records_each_write_and_reads_back(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        self.assertEqual(self.run_cli("apply", path, "--yes", "--now", "2026-09-28T12:00:00",
                                      responses=self.send_responses(2)), 0)
        self.assertEqual([r.json() for r in self.fake.writes()], [{"tc": "DS-1"}, {"tc": "DS-2"}])
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertTrue(rec["complete"])
        self.assertEqual(rec["tracker"], "jira")
        self.assertEqual([(w["case"], w["id"], w["at"]) for w in rec["writes"]],
                         [("DS-1", "run-1", "2026-09-28T12:00:00"), ("DS-2", "run-2", "2026-09-28T12:00:00")])
        expected = "DS-3 (Blocked): mapped to skip in jira.results.statuses — stays only in the run log"
        self.assertEqual(rec["not_sent"], [expected])
        self.assertEqual(self.fake.left, 0)

    def test_the_same_batch_again_makes_no_request(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        self.run_cli("apply", path, "--yes", responses=self.send_responses(2))
        for extra in ((), ("--yes",)):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_cli("apply", path, *extra), 0)
                self.assertEqual(self.fake.requests, [])
                self.assertIn("Already sent with the same content", self.out)

    def test_the_same_name_with_another_content_is_exit_4(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        self.run_cli("apply", path, "--yes", responses=self.send_responses(2))
        self.ws.batch("batch_20260928_01", results(("DS-1", "Fail"), ("DS-2", "Fail")))
        self.assertEqual(self.run_cli("apply", path, "--yes"), 4)
        self.assertEqual(self.fake.requests, [])
        self.assertIn("ANOTHER content", self.out)

    def test_an_interrupted_send_continues_without_duplicates(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        # request 3 (the 2nd write) has no answer: exit 3, the record keeps the 1st write
        code = self.run_cli("apply", path, "--yes", responses=self.send_responses(2), fail_at=3)
        self.assertEqual(code, 3)
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertFalse(rec["complete"])
        self.assertEqual([w["case"] for w in rec["writes"]], ["DS-1"])
        # the same command again sends only what is missing
        code = self.run_cli("apply", path, "--yes", responses=[resolve_response(), write_response(2),
                                                               readback_response()])
        self.assertEqual(code, 0)
        self.assertEqual([r.json() for r in self.fake.writes()], [{"tc": "DS-2"}])
        self.assertIn("continuing an incomplete send", self.out)
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual(([w["case"] for w in rec["writes"]], rec["complete"]), (["DS-1", "DS-2"], True))

    def test_a_read_back_difference_is_exit_5_and_not_resent(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        code = self.run_cli("apply", path, "--yes",
                            responses=self.send_responses(2, ["DS-2: FAILED sent, PASSED read back"]))
        self.assertEqual(code, 5)
        self.assertIn("do NOT send again blindly", self.out)
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)   # complete: nothing sent again
        self.assertEqual(self.fake.requests, [])

    def test_a_conflict_while_resolving_is_exit_2_with_no_write(self):
        path = self.ws.batch("batch_20260928_01", BATCH)
        code = self.run_cli("apply", path, "--yes", responses=[{"status": 404, "json": {"errorMessages": ["x"]}}])
        self.assertEqual(code, 2)
        self.assertEqual(self.fake.writes(), [])
        self.assertIn("NOTHING SENT", self.out)

    def test_everything_not_sent_is_exit_0(self):
        path = self.ws.batch("batch_20260928_02", results(("DS-3", "Blocked"), ("DS-4", "N/A")))
        self.assertEqual(self.run_cli("apply", path, responses=[resolve_response()]), 0)
        self.assertIn("DS-4 (N/A): unmapped", self.out)
        self.assertIn("Nothing to send", self.out)

    # -- exit 4 before any request ------------------------------------------------------------------------------

    def test_question_and_invalid_batches_make_no_request(self):
        for name, items in (("q", results(("DS-1", "Question"))), ("typo", results(("DS-1", "Passed"))),
                            ("empty", [])):
            with self.subTest(batch=name):
                self.assertEqual(self.run_cli("apply", self.ws.batch(name, items)), 4)
                self.assertEqual(self.fake.requests, [])
                self.assertIn("nothing resolved nor sent", self.out)

    def test_a_missing_credential_is_exit_4_after_the_input_is_checked(self):
        self.ws.env({k: v for k, v in ENV.items() if k != "DS_JIRA_TOKEN"})
        self.assertEqual(self.run_cli("apply", self.ws.batch("b", BATCH)), 4)
        self.assertIn("DS_JIRA_TOKEN", self.out)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.run_cli("check"), 4)

    def test_an_invalid_configuration_is_exit_4(self):
        self.ws.project({**PROJECT, "jira": {**PROJECT["jira"], "site": "http://demo-shop.atlassian.net"}})
        self.assertEqual(self.run_cli("check"), 4)
        self.assertIn("jira.site", self.out)

    def test_the_stub_back_ends_say_which_task_builds_them(self):
        self.assertEqual(self.run_cli("check", backend=None), 4)
        self.assertIn("not implemented yet (plan 10.2)", self.out)
        self.ws.project({**{k: v for k, v in PROJECT.items() if k != "jira"}, "tracker": "azure-devops",
                         "azure_devops": AZURE})
        self.assertEqual(self.run_cli("apply", self.ws.batch("b", BATCH), backend=None), 4)
        self.assertIn("not implemented yet (plan 10.3)", self.out)
        self.assertEqual(self.fake.requests, [])

    def test_a_direction_the_back_end_lacks_is_exit_4(self):
        path = self.ws.file("draft.json", {"title": "Expired coupon accepted", "observed": "accepted"})
        self.assertEqual(self.run_cli("bug-search", path), 4)
        self.assertIn("bug-search is not implemented yet (plan 10.2)", self.out)
        self.assertEqual(self.run_cli("bug-search", self.ws.file("bad.json", {"area": "Checkout"})), 4)
        self.assertIn("at least 'observed' or 'title'", self.out)

    def test_export_cases_validates_before_the_back_end(self):
        bad = self.ws.file("new_cases.json", {"cases": [{"id": "TC-1", "title": "Coupon"}]})
        self.assertEqual(self.run_cli("export-cases", bad), 4)
        good = self.ws.file("new_cases2.json", {"cases": [{"id": "NEW-1", "title": "Coupon"}]})
        self.assertEqual(self.run_cli("export-cases", good), 4)
        self.assertIn("export-cases is not implemented yet", self.out)

    # -- output, recording -------------------------------------------------------------------------------------

    def test_nothing_printed_has_a_credential(self):
        class Leaky(FakeJira):
            leak = True
        path = self.ws.batch("batch_20260928_01", BATCH)
        self.assertEqual(self.run_cli("apply", path, responses=[resolve_response()], backend=Leaky), 1)
        self.assertIn("«DS_JIRA_TOKEN»", self.out)
        for value in ENV.values():
            self.assertNotIn(value, self.out)

    def test_record_saves_redacted_exchanges(self):
        self.assertEqual(self.run_cli("check", "--record", responses=[{"json": {"accountId": "a1"}}]), 0)
        folder = os.path.join(self.ws.path, "tracker", "recordings")
        (run,) = os.listdir(folder)
        with open(os.path.join(folder, run, "0001.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("/rest/api/3/myself", text)
        for value in ENV.values():
            self.assertNotIn(value, text)


class Records(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_states(self):
        r = SentRecord(self.ws.path, "batch_1", "h1", "jira")
        self.assertEqual(r.state(), "new")
        r.add("bug", "DS-2", key="DS-200")
        r2 = SentRecord(self.ws.path, "batch_1", "h1", "jira")
        self.assertEqual((r2.state(), r2.find("bug", "DS-2")[0]["key"]), ("incomplete", "DS-200"))
        r2.finish()
        self.assertEqual(SentRecord(self.ws.path, "batch_1", "h1", "jira").state(), "complete")
        with self.assertRaises(InvalidInput):
            SentRecord(self.ws.path, "batch_1", "h2", "jira").state()
        with self.assertRaises(InvalidInput):
            SentRecord(self.ws.path, "batch_1", "h1", "azure-devops").state()
        with self.assertRaises(InvalidInput):
            SentRecord(self.ws.path, "../history/x", "h1", "jira")

    def test_never_under_history(self):
        self.assertTrue(safe_path(self.ws.path, "sent", "b.json").endswith(os.path.join("tracker", "sent", "b.json")))
        link = os.path.join(self.ws.path, "tracker")
        os.makedirs(os.path.join(self.ws.path, "history"))
        try:
            os.symlink(os.path.join(self.ws.path, "history"), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links not available here")
        with self.assertRaises(ConfigError):
            safe_path(self.ws.path, "sent", "b.json")

    def test_the_id_cache_and_the_cases(self):
        ids = IdCache(self.ws.path, "jira")
        ids.set("TC-SHOP-001", key="DS-123", id="10123")
        ids.save()
        self.assertEqual(IdCache(self.ws.path, "jira").get("TC-SHOP-001"), {"key": "DS-123", "id": "10123"})
        with self.assertRaises(ConfigError):
            IdCache(self.ws.path, "azure-devops")
        write_cases(self.ws.path, "jira", [{"id": "DS-123", "title": "Expired coupon refused"}],
                    [{"case": "DS-123", "flag": "no steps"}])
        doc = read_cases(self.ws.path)
        self.assertEqual((doc["cases"][0]["id"], doc["flags"][0]["flag"]), ("DS-123", "no steps"))

    def test_the_record_is_redacted(self):
        from adapters.tracker.http import SecretRedactor
        r = SentRecord(self.ws.path, "b", "h", "jira", SecretRedactor({"DS_JIRA_TOKEN": "fictional-value-111"}))
        r.add("comment", "DS-1", text="saw fictional-value-111 in the page")
        with open(r.path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("fictional-value-111", text)
        self.assertEqual(json.loads(text)["writes"][0]["text"], "saw «DS_JIRA_TOKEN» in the page")


if __name__ == "__main__":
    unittest.main()
