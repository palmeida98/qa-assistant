"""The Jira Cloud + Xray Cloud back-end (technique-jira-xray.md; plan 10.2) against a stateful fake Jira + Xray
(tests/jira_fake.py): check, apply (new and existing bugs, bug_status, links, chunks of 100, evidence, read back,
the uncertain write and the marker search, re-authentication), bug-search with its relaxed passes, import-cases with
pagination, export-cases, the round trip (section 10.2), ADF and the field formatting. Fictional Demo Shop data only;
no network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import base64
import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

from jira_fake import FakeJiraXray, jql_match, parse_multipart
from tracker_kit import ENV, PROJECT, Workspace

from adapters.jira import fields as F
from adapters.jira.adf import from_adf, to_adf
from adapters.tracker import cli
from adapters.tracker.record import IdCache

JIRA = {
    "site": "https://demo-shop.atlassian.net", "project_key": "DS", "user_var": "DS_JIRA_USER",
    "token_var": "DS_JIRA_TOKEN",
    "xray": {"client_id_var": "DS_XRAY_CLIENT_ID", "client_secret_var": "DS_XRAY_CLIENT_SECRET"},
    "cases": {"id_source": "key", "fields": {
        "area": "components", "feature": "customfield_10051", "preconditions": "description", "priority": "priority",
        "role": "labels:role-", "platform": "labels:platform-", "technique": "labels:technique-",
        "origin": "labels:origin-"}},
    "results": {"statuses": {"Pass": "PASSED", "Fail": "FAILED", "Not run": "TODO", "Blocked": "BLOCKED",
                             "N/A": "skip"}, "test_plan_key": "DS-10"},
    "bugs": {"fields": {"title": "summary", "description": "description", "area": "components",
                        "feature": "customfield_10051", "sub_area": "labels:subarea-",
                        "platform": "labels:platform-", "role": "labels:role-", "environment": "environment",
                        "severity": "customfield_10060", "priority": "priority"},
             "statuses": ["Done"], "priorities": ["High", "Medium", "Low"], "link_type": "Relates"},
}
RUN = {"environment": "test", "build": "2.4.1"}
TS = "2026-09-28T10:00:00+01:00"


def test_fields(area="Checkout", feature="Coupons", **extra):
    return {"summary": extra.pop("summary", "Coupon"), "components": [{"name": area}],
            "customfield_10051": {"value": feature}, "priority": {"name": "High"},
            "labels": extra.pop("labels", ["role-customer", "platform-web", "technique-boundary-values",
                                           "origin-requirement"]),
            "description": to_adf(extra.pop("pre", "A cart with one product")), **extra}


def seed(fake, n_tests=4):
    """n Manual tests with steps (DS-1, DS-2... skipping 10 and 50), the test plan DS-10, the bug DS-50 (In
    Progress)."""
    numbers = [k for k in range(1, n_tests + 3) if k not in (10, 50)][:n_tests]
    for i in numbers:
        fake.new_issue("Test", test_fields(summary=f"Coupon case {i}"), key=f"DS-{i}", xray={
            "testType": {"name": "Manual", "kind": "Steps"},
            "steps": [{"action": "Open the cart", "data": "", "result": ""},
                      {"action": "Apply the coupon", "data": f"CODE{i}", "result": "The discount is shown"}],
            "preconditions": []})
    fake.new_issue("Test Plan", {"summary": "Release 2.4"}, key="DS-10")
    fake.new_issue("Bug", {"summary": "Coupon field accepts spaces", "labels": []}, key="DS-50", status="In Progress")
    return fake


class Base(unittest.TestCase):
    jira = JIRA

    def setUp(self):
        self.ws = Workspace({**PROJECT, "jira": copy.deepcopy(self.jira)})
        self.addCleanup(self.ws.cleanup)
        self.fake = seed(FakeJiraXray())
        self.lines = []

    def run_cli(self, *args, fake=None):
        self.fake = fake or self.fake
        self.lines = []
        start = len(self.fake.requests)
        code = cli.main([self.ws.path, *args], transport=self.fake, sleep=lambda s: None, printer=self.lines.append)
        self.made = self.fake.requests[start:]
        return code

    @property
    def out(self):
        return "\n".join(self.lines)

    def writes(self):
        return [r for r in self.made if r.method not in ("GET", "HEAD") and not r.read_only]

    def batch(self, items, name="batch_20260928_01", run=RUN):
        return self.ws.batch(name, items, run=run)


def item(tc, status, **kw):
    return {"tc": tc, "status": status, "actual_result": kw.pop("actual", f"observed on {tc}"), "ts": TS, **kw}


NEW_DEFECT = {"title": "Expired coupon accepted", "expected": "The coupon is refused",
              "observed": "The expired coupon is accepted", "steps": ["Open the cart", "Apply EXPIRED10"],
              "area": "Checkout", "feature": "Coupons", "sub_area": "cart", "platform": "web", "role": "customer",
              "priority": "High"}


class Check(Base):
    def test_everything_present_is_exit_0(self):
        self.assertEqual(self.run_cli("check"), 0, self.out)
        self.assertIn("Xray: authenticated", self.out)
        self.assertEqual(self.writes(), [])

    def test_what_is_missing_is_listed_and_exit_4(self):
        self.fake.statuses.remove("BLOCKED")
        jira = copy.deepcopy(JIRA)
        jira["bugs"]["fields"]["severity"] = "customfield_99999"
        jira["bugs"]["statuses"] = ["Done", "Verified"]
        jira["bugs"]["link_type"] = "Duplicates"
        self.ws.project({**PROJECT, "jira": jira})
        self.assertEqual(self.run_cli("check"), 4)
        for part in ("'BLOCKED' is not a test run status", "customfield_99999", "'Verified' is not a status",
                     "'Duplicates': no such issue link type"):
            self.assertIn(part, self.out)

    def test_a_wrong_token_is_exit_3_without_the_token(self):
        def refuse(req, real=self.fake):
            if req.url.endswith("/myself"):
                from adapters.tracker.http import Response
                return Response(401, {}, b'{"errorMessages": ["unauthorized"]}')
            return real(req)
        self.assertEqual(cli.main([self.ws.path, "check"], transport=refuse, printer=self.lines.append), 3)
        for value in ENV.values():
            self.assertNotIn(value, "\n".join(self.lines))


class Apply(Base):
    def full_batch(self):
        return self.batch([
            item("DS-1", "Pass"),
            item("DS-2", "Fail", new_defect=NEW_DEFECT),
            item("DS-3", "Fail", bug="DS-50", actual="still accepts spaces"),
            item("DS-4", "Blocked", bug="DS-50", bug_status="Done"),
        ])

    def test_dry_run_resolves_and_writes_nothing(self):
        path = self.full_batch()
        self.assertEqual(self.run_cli("apply", path), 1, self.out)
        self.assertEqual(self.writes(), [])
        for part in ("POST /rest/api/3/issue — DS-2: Bug \"Expired coupon accepted\"",
                     "POST /api/v2/import/execution/multipart — chunk 1/1: new Test Execution",
                     "DS-50: comment", "DS-50: In Progress -> Done", "the new bug of DS-2 Relates DS-2"):
            self.assertIn(part, self.out)
        self.assertFalse(self.ws.exists("tracker/sent/batch_20260928_01.json"))

    def test_yes_sends_everything_and_reads_back(self):
        path = self.full_batch()
        self.assertEqual(self.run_cli("apply", path, "--yes", "--now", "2026-09-28T12:00:00"), 0, self.out)
        bug = self.fake.by_key("DS-101")
        f = bug["fields"]
        self.assertEqual(f["issuetype"]["name"], "Bug")
        self.assertEqual(f["summary"], "Expired coupon accepted")
        self.assertEqual(sorted(f["labels"]), sorted(["qa-batch-batch_20260928_01", "qa-case-DS-2", "subarea-cart",
                                                      "platform-web", "role-customer"]))
        self.assertEqual((f["components"], f["customfield_10051"], f["priority"]),
                         ([{"name": "Checkout"}], {"value": "Coupons"}, {"name": "High"}))
        text, _ = from_adf(f["description"])
        for part in ("Environment: test · Build: 2.4.1", "Expected: The coupon is refused",
                     "Observed: The expired coupon is accepted", "Linked cases: DS-2", "- Apply EXPIRED10"):
            self.assertIn(part, text)
        self.assertEqual(from_adf(f["environment"])[0], "test")
        (execution,) = self.fake.of_type("Test Execution")
        self.assertEqual(execution["fields"]["labels"], ["qa-batch-batch_20260928_01"])
        self.assertEqual(execution["fields"]["summary"], "batch_20260928_01 · test · 2.4.1")
        self.assertEqual(execution["xrayFields"], {"environments": ["test"], "testPlanKey": "DS-10"})
        runs = self.fake.executions[execution["id"]]
        by_key = {self.fake.issues[k]["key"]: v for k, v in runs.items()}
        self.assertEqual({k: v["status"]["name"] for k, v in by_key.items()},
                         {"DS-1": "PASSED", "DS-2": "FAILED", "DS-3": "FAILED", "DS-4": "BLOCKED"})
        self.assertEqual(by_key["DS-2"]["defects"], [bug["id"]])
        self.assertEqual(by_key["DS-3"]["comment"], "still accepts spaces")
        comments = self.fake.comments["DS-50"]
        self.assertEqual(len(comments), 2)
        self.assertIn("qa-batch-batch_20260928_01 · DS-3: Fail", from_adf(comments[0]["body"])[0])
        self.assertEqual(self.fake.by_key("DS-50")["fields"]["status"]["name"], "Done")
        self.assertEqual(self.fake.links, [{"type": {"name": "Relates"}, "inwardIssue": {"key": "DS-101"},
                                            "outwardIssue": {"key": "DS-2"}}])
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertTrue(rec["complete"])
        self.assertEqual([w["kind"] for w in rec["writes"]],
                         ["bug", "execution", "comment", "comment", "transition", "link"])
        self.assertEqual(IdCache(self.ws.path, "jira").get("DS-1")["key"], "DS-1")
        self.assertIn("Read back: it matches", self.out)
        # the same batch again: no request at all
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_not_sent_statuses_and_question(self):
        path = self.batch([item("DS-1", "Pass"), item("DS-2", "N/A")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        (execution,) = self.fake.of_type("Test Execution")
        self.assertEqual(len(self.fake.executions[execution["id"]]), 1)
        self.assertIn("DS-2 (N/A): mapped to skip", self.out)
        path = self.batch([item("DS-1", "Question")], name="batch_q")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 4)
        self.assertEqual(self.made, [])

    def test_chunks_of_100_stay_in_one_execution(self):
        fake = seed(FakeJiraXray(), n_tests=205)
        path = self.batch([item(t["key"], "Pass") for t in fake.of_type("Test")])
        self.assertEqual(self.run_cli("apply", path, "--yes", fake=fake), 0, self.out)
        imports = [r.path for r in self.writes() if "/import/" in r.path]
        self.assertEqual(imports, ["/api/v2/import/execution/multipart", "/api/v2/import/execution",
                                   "/api/v2/import/execution"])
        self.assertEqual({r.json()["testExecutionKey"] for r in self.writes() if r.path == "/api/v2/import/execution"},
                         {fake.of_type("Test Execution")[0]["key"]})
        (execution,) = fake.of_type("Test Execution")
        self.assertEqual(len(fake.executions[execution["id"]]), 205)
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual([(w["kind"], w.get("chunk")) for w in rec["writes"]],
                         [("execution", 1), ("results", 2), ("results", 3)])

    def test_an_uncertain_create_is_found_by_its_marker_and_never_created_again(self):
        path = self.batch([item("DS-1", "Pass"), item("DS-2", "Fail", new_defect=NEW_DEFECT)])
        # the bug create is done by Jira, but its answer is lost: exit 3. The dry run on a copy counts the requests
        # before the first write (the same reads as the --yes run)
        probe = seed(FakeJiraXray())
        cli.main([self.ws.path, "apply", path], transport=probe, printer=[].append)
        self.fake.drop_answer_at = len(probe.requests) + 1    # the first write: the bug
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3, self.out)
        self.assertIn("never retried blindly", self.out)
        self.assertEqual(len(self.fake.of_type("Bug")), 2)   # DS-50 and the new one
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual(rec["writes"], [])
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("found by its markers", self.out)
        self.assertEqual(len(self.fake.of_type("Bug")), 2)   # not created again
        self.assertNotIn("/rest/api/3/issue", [r.path for r in self.writes()])
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual([(w["kind"], w.get("reused")) for w in rec["writes"]],
                         [("bug", True), ("execution", None), ("link", None)])

    def test_an_execution_created_without_an_answer_is_reused(self):
        path = self.batch([item("DS-1", "Pass")])
        probe = seed(FakeJiraXray())
        cli.main([self.ws.path, "apply", path], transport=probe, printer=[].append)
        self.fake.drop_answer_at = len(probe.requests) + 1
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(len(self.fake.of_type("Test Execution")), 1)
        self.assertIn("found by its marker", self.out)

    def test_a_request_that_never_arrived_is_sent_on_the_next_run(self):
        path = self.batch([item("DS-1", "Pass")])
        probe = seed(FakeJiraXray())
        cli.main([self.ws.path, "apply", path], transport=probe, printer=[].append)
        self.fake.fail_at = len(probe.requests) + 1
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3)
        self.assertEqual(self.fake.of_type("Test Execution"), [])
        self.fake.fail_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(len(self.fake.of_type("Test Execution")), 1)

    def test_a_comment_without_an_answer_is_not_repeated(self):
        path = self.batch([item("DS-3", "Fail", bug="DS-50")])
        probe = seed(FakeJiraXray())
        cli.main([self.ws.path, "apply", path], transport=probe, printer=[].append)
        self.fake.drop_answer_at = len(probe.requests) + 2   # the multipart import, then the comment
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("the comment on DS-50 is already there", self.out)
        self.assertEqual(len(self.fake.comments["DS-50"]), 1)

    def test_an_expired_xray_token_is_renewed_once(self):
        self.fake.token_uses = 1     # the token works once, then 401
        path = self.batch([item("DS-1", "Pass"), item("DS-2", "Fail")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(len([r for r in self.made if r.path == "/api/v2/authenticate"]), 2)

    def test_the_read_back_difference_is_exit_5(self):
        self.fake.automation = {"DS-2": "PASSED"}   # an automation rule changes the status
        path = self.batch([item("DS-1", "Pass"), item("DS-2", "Fail")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 5)
        self.assertIn("DS-2 (DS-2): FAILED sent, PASSED read back", self.out)
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_conflicts_are_exit_2_with_no_write(self):
        bug_as_case = self.batch([item("DS-50", "Pass")], name="b1")
        unknown = self.batch([item("DS-77", "Pass")], name="b2")
        unknown_bug = self.batch([item("DS-1", "Fail", bug="DS-88")], name="b3")
        for path, part in ((bug_as_case, "is a Bug, not a Test"), (unknown, "DS-77"), (unknown_bug, "DS-88")):
            with self.subTest(part=part):
                self.assertEqual(self.run_cli("apply", path, "--yes"), 2, self.out)
                self.assertIn(part, self.out)
                self.assertEqual(self.writes(), [])
        jira = copy.deepcopy(JIRA)
        jira["bugs"]["statuses"] = ["Done", "Verified"]
        self.ws.project({**PROJECT, "jira": jira})
        no_transition = self.batch([item("DS-1", "Pass", bug="DS-50", bug_status="Verified")], name="b4")
        self.assertEqual(self.run_cli("apply", no_transition, "--yes"), 2)
        self.assertIn("has no transition to 'Verified'", self.out)
        self.assertEqual(self.writes(), [])
        ids = IdCache(self.ws.path, "jira")
        ids.set("DS-2", key="DS-2", id="1")
        ids.save()
        self.assertEqual(self.run_cli("apply", self.batch([item("DS-2", "Pass")], name="b5"), "--yes"), 2)
        self.assertIn("now points elsewhere", self.out)

    def test_a_moved_issue_is_followed_through_its_id(self):
        issue = self.fake.by_key("DS-1")
        ids = IdCache(self.ws.path, "jira")
        ids.set("DS-1", key="DS-1", id=issue["id"])
        ids.save()
        issue["old_keys"] = ["DS-1"]
        issue["key"] = "QA-7"
        self.assertEqual(self.run_cli("apply", self.batch([item("DS-1", "Pass")]), "--yes"), 0, self.out)
        self.assertIn("moved DS-1 -> QA-7", self.out)
        self.assertEqual(IdCache(self.ws.path, "jira").get("DS-1")["key"], "QA-7")

    def test_evidence_only_with_the_policy(self):
        jira = copy.deepcopy(JIRA)
        jira["results"]["attach_evidence"] = True
        self.ws.project({**PROJECT, "jira": jira})
        self.ws.file("evidence/20260928/console.txt", f"token {ENV['DS_JIRA_TOKEN']} in the log")
        self.ws.file("evidence/20260928/cart.png", "PNG")
        path = self.batch([item("DS-2", "Fail", evidence=["evidence/20260928/console.txt",
                                                          "evidence/20260928/cart.png"])])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("cart.png: a screenshot/video goes only with evidence", self.out)
        (execution,) = self.fake.of_type("Test Execution")
        (run,) = self.fake.executions[execution["id"]].values()
        self.assertEqual(run["evidence"], ["console.txt"])
        sent = next(r for r in self.made if r.path.endswith("/multipart"))
        (test,) = parse_multipart(sent)["results"]["tests"]
        self.assertEqual(base64.b64decode(test["evidence"][0]["data"]).decode("utf-8"),
                         "token «DS_JIRA_TOKEN» in the log")   # a text file is redacted before it goes
        # with text+screenshot the image goes too
        self.ws.project({**PROJECT, "evidence": "text+screenshot", "jira": jira})
        self.assertEqual(self.run_cli("apply", self.batch([item("DS-3", "Fail", evidence="evidence/20260928/cart.png")],
                                                          name="b2"), "--yes"), 0, self.out)
        execution = self.fake.of_type("Test Execution")[1]
        self.assertEqual([r["evidence"] for r in self.fake.executions[execution["id"]].values()], [["cart.png"]])

    def test_the_output_never_has_a_credential(self):
        path = self.batch([item("DS-2", "Fail", actual=f"the page showed {ENV['DS_JIRA_TOKEN']}")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        self.assertIn("redacted", self.out)
        record = json.dumps(self.ws.read_json("tracker/sent/batch_20260928_01.json"))
        for value in ENV.values():
            self.assertNotIn(value, self.out)
            self.assertNotIn(value, record)
        self.assertNotIn("xray-token", self.out)


class BugSearch(Base):
    def setUp(self):
        super().setUp()
        self.fake.new_issue("Bug", {"summary": "Expired coupon accepted at checkout", "components": [{"name": "Checkout"}],
                                    "customfield_10051": {"value": "Coupons"},
                                    "labels": ["subarea-cart", "platform-mobile", "role-guest"]}, key="DS-60")
        self.fake.new_issue("Bug", {"summary": "Expired coupon accepted", "components": [{"name": "Checkout"}],
                                    "customfield_10051": {"value": "Coupons"}, "labels": ["platform-web"]},
                            key="DS-61", status="Done")

    def draft(self, **kw):
        return self.ws.file("draft.json", {**{k: NEW_DEFECT[k] for k in ("title", "observed", "area", "feature",
                                                                         "sub_area", "platform", "role")}, **kw})

    def test_the_passes_relax_until_a_candidate(self):
        self.assertEqual(self.run_cli("bug-search", self.draft()), 0, self.out)
        jqls = [r.json()["jql"] for r in self.made]
        self.assertEqual(len(jqls), 3)   # strict, without the role, without the platform
        self.assertIn('labels = "role-customer"', jqls[0])
        self.assertIn('statusCategory != Done', jqls[0])
        self.assertIn('text ~ "\\"expired\\" \\"coupon\\" \\"accepted\\""', jqls[0])
        self.assertNotIn("role-", jqls[1])
        self.assertNotIn("platform-", jqls[2])
        self.assertIn("DS-60 [To Do] Expired coupon accepted at checkout — matches: area, feature, sub_area, text "
                      "(pass 3)", self.out)
        self.assertEqual(self.writes(), [])

    def test_include_closed_finds_the_fixed_bug(self):
        self.assertEqual(self.run_cli("bug-search", self.draft(sub_area=None, role=None), "--include-closed"), 0)
        self.assertNotIn("statusCategory", self.made[0].json()["jql"])
        self.assertIn("DS-61 [Done]", self.out)

    def test_nothing_found(self):
        self.assertEqual(self.run_cli("bug-search", self.draft(area="Payments", feature="Refunds",
                                                               observed="Refund never arrives", title="Refund")), 0)
        self.assertEqual(len(self.made), 5)   # strict + 4 relaxed passes
        self.assertIn("No candidate found", self.out)


class ImportExport(Base):
    def test_import_follows_every_page(self):
        fake = seed(FakeJiraXray(page=100), n_tests=250)
        fake.plan_status.update({fake.by_key("DS-1")["id"]: "PASSED", fake.by_key("DS-2")["id"]: "BLOCKED"})
        fake.xray[fake.by_key("DS-3")["id"]]["steps"] = []
        fake.xray[fake.by_key("DS-4")["id"]]["testType"] = {"name": "Cucumber", "kind": "Gherkin"}
        self.assertEqual(self.run_cli("import-cases", fake=fake), 0, self.out)
        self.assertEqual(fake.search_pages[:3], [100, 100, 50])
        self.assertEqual(len([r for r in self.made if r.path == "/api/v2/graphql"]), 3)
        doc = self.ws.read_json("tracker/cases.json")
        self.assertEqual(len(doc["cases"]), 250)
        c1 = doc["cases"][0]
        self.assertEqual({k: c1[k] for k in ("id", "key", "title", "area", "feature", "priority", "role", "platform",
                                              "technique", "origin", "preconditions", "expected", "last_status")},
                         {"id": "DS-1", "key": "DS-1", "title": "Coupon case 1", "area": "Checkout",
                          "feature": "Coupons", "priority": "High", "role": "customer", "platform": "web",
                          "technique": "boundary-values", "origin": "requirement",
                          "preconditions": "A cart with one product", "expected": "The discount is shown",
                          "last_status": "Pass"})
        self.assertEqual(doc["cases"][1]["last_status"], "Blocked")
        flags = {(f["case"], f["flag"].split(":")[0]) for f in doc["flags"]}
        self.assertIn(("DS-3", "no steps"), flags)
        self.assertIn(("DS-4", "test type Cucumber"), flags)
        self.assertEqual(IdCache(self.ws.path, "jira").get("DS-252")["key"], "DS-252")

    def test_too_many_is_a_usage_error(self):
        fake = seed(FakeJiraXray(), n_tests=5)
        from adapters.tracker import http
        try:
            http.paginate_token.__defaults__ = (3,)   # the guard of section 2.7, lowered
            self.assertEqual(self.run_cli("import-cases", fake=fake), 4)
        finally:
            http.paginate_token.__defaults__ = (http.MAX_ITEMS,)
        self.assertIn("narrow the query", self.out)

    def test_two_tests_with_the_same_id_is_exit_2(self):
        jira = copy.deepcopy(JIRA)
        jira["cases"].update({"id_source": "label", "id_label_prefix": "qa-id-"})
        self.ws.project({**PROJECT, "jira": jira})
        for key in ("DS-1", "DS-2"):
            self.fake.by_key(key)["fields"]["labels"].append("qa-id-TC-SHOP-001")
        self.assertEqual(self.run_cli("import-cases"), 2)
        self.assertIn("TC-SHOP-001", self.out)
        self.assertFalse(self.ws.exists("tracker/cases.json"))

    def new_cases(self, name="new_cases_01", **extra):
        cases = [{"id": "NEW-1", "title": "Expired coupon is refused", "area": "Checkout", "feature": "Coupons",
                  "preconditions": "A cart with one product\n\nAn expired coupon EXPIRED10", "priority": "High",
                  "role": "customer", "platform": "web", "technique": "boundary-values", "origin": "requirement",
                  "steps": [{"action": "Apply EXPIRED10", "data": "EXPIRED10", "expected": "The coupon is refused"}],
                  **extra}]
        return self.ws.file(f"{name}.json", {"cases": cases})

    def test_export_dry_run_then_yes(self):
        path = self.new_cases()
        self.assertEqual(self.run_cli("export-cases", path), 1, self.out)
        self.assertIn("createTest NEW-1: \"Expired coupon is refused\" (Manual, 1 step(s)", self.out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        test = self.fake.by_key("DS-101")
        self.assertEqual(self.fake.xray[test["id"]]["steps"],
                         [{"action": "Apply EXPIRED10", "data": "EXPIRED10", "result": "The coupon is refused"}])
        self.assertIn("qa-batch-new_cases_01", test["fields"]["labels"])
        self.assertEqual(from_adf(test["fields"]["description"])[0],
                         "A cart with one product\n\nAn expired coupon EXPIRED10")
        self.assertEqual(self.fake.plan_status, {test["id"]: "TODO"})
        self.assertIn("NEW-1 -> DS-101", self.out)
        self.assertEqual(IdCache(self.ws.path, "jira").get("DS-101"), {"key": "DS-101", "id": test["id"]})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_export_refuses_a_summary_that_exists(self):
        path = self.ws.file("dup.json", {"cases": [{"id": "NEW-1", "title": "Coupon case 2"}]})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 2)
        self.assertIn("candidate duplicate", self.out)
        self.assertEqual([r.path for r in self.writes()], [])

    def test_export_with_own_ids(self):
        jira = copy.deepcopy(JIRA)
        jira["cases"].update({"id_source": "field", "id_field": "customfield_10050", "folder": "/Checkout"})
        self.ws.project({**PROJECT, "jira": jira})
        self.fake.by_key("DS-1")["fields"]["customfield_10050"] = "TC-SHOP-001"
        taken = self.ws.file("taken.json", {"cases": [{"id": "TC-SHOP-001", "title": "Another title"}]})
        self.assertEqual(self.run_cli("export-cases", taken, "--yes"), 2)
        self.assertIn("TC-SHOP-001: already exists as DS-1", self.out)
        path = self.ws.file("own.json", {"cases": [{"id": "TC-SHOP-009", "title": "Coupon for guests"}]})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        test = self.fake.by_key("DS-101")
        self.assertEqual((test["fields"]["customfield_10050"], self.fake.folders[test["id"]]),
                         ("TC-SHOP-009", "/Checkout"))
        self.assertEqual(IdCache(self.ws.path, "jira").get("TC-SHOP-009")["key"], "DS-101")

    def test_an_uncertain_create_test_is_not_repeated(self):
        path = self.new_cases()
        probe = seed(FakeJiraXray())
        cli.main([self.ws.path, "export-cases", path], transport=probe, printer=[].append)
        self.fake.drop_answer_at = len(probe.requests) + 1
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 3)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        self.assertEqual(len([i for i in self.fake.of_type("Test") if "qa-batch-new_cases_01" in
                              i["fields"]["labels"]]), 1)
        self.assertIn("reused, not created again", self.out)

    def test_round_trip(self):
        """technique 10.2: import -> export to an empty tracker -> import; the cases are the same (IDs apart)."""
        source = seed(FakeJiraXray(), n_tests=3)
        source.xray[source.by_key("DS-2")["id"]]["steps"].append({"action": "Pay", "data": "card 4111",
                                                                  "result": ""})
        source.by_key("DS-3")["fields"]["description"] = to_adf("One product\n\n- in the cart\n- with a coupon")
        self.assertEqual(self.run_cli("import-cases", fake=source), 0, self.out)
        first = self.ws.read_json("tracker/cases.json")["cases"]
        cases = [{**c, "id": f"NEW-{n}"} for n, c in enumerate(first, 1)]
        path = self.ws.file("round_trip.json", {"cases": cases})
        target = FakeJiraXray()
        target.new_issue("Test Plan", {"summary": "Release 2.4"}, key="DS-10")
        self.assertEqual(self.run_cli("export-cases", path, "--yes", fake=target), 0, self.out)
        self.assertEqual(self.run_cli("import-cases", fake=target), 0, self.out)
        second = self.ws.read_json("tracker/cases.json")["cases"]
        strip = ("id", "key", "last_status")
        self.assertEqual([{k: v for k, v in c.items() if k not in strip} for c in first],
                         [{k: v for k, v in c.items() if k not in strip} for c in second])
        self.assertEqual([c["last_status"] for c in second], ["Not run"] * 3)   # new in the plan: TODO


class Formatting(unittest.TestCase):
    def test_adf_round_trip(self):
        for text in ("", "One line", "Two\nlines", "A\n\nB", "- one\n- two", "Intro\n\n- a\n- b\n\nEnd",
                     "```\ncode here\n  indented\n```", "Text\n\n```\nx = 1\n```\n\nAfter"):
            with self.subTest(text=text):
                self.assertEqual(from_adf(to_adf(text)), (text, False))

    def test_adf_from_people_is_simplified(self):
        doc = {"type": "doc", "version": 1, "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Setup"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Ask "},
                                              {"type": "mention", "attrs": {"text": "@QA Demo"}}]}]}
        self.assertEqual(from_adf(doc), ("Setup\n\nAsk @QA Demo", True))

    def test_values_from_the_schema(self):
        self.assertEqual(F.format_value("components", {"type": "array", "items": "component"}, "Checkout, Cart"),
                         [{"name": "Checkout"}, {"name": "Cart"}])
        self.assertEqual(F.format_value("customfield_10051", {"type": "option"}, "Coupons"), {"value": "Coupons"})
        self.assertEqual(F.format_value("customfield_1", {"type": "array", "items": "option"}, ["a", "b"]),
                         [{"value": "a"}, {"value": "b"}])
        self.assertEqual(F.format_value("priority", {"type": "priority"}, "High"), {"name": "High"})
        self.assertEqual(F.format_value("description", {"type": "string"}, "x")["type"], "doc")
        self.assertEqual(F.format_value("summary", {"type": "string"}, "x"), "x")
        self.assertEqual(F.format_value("customfield_2", {"type": "number"}, "3"), 3)
        self.assertEqual(F.label_value("platform-", "mobile web"), ("platform-mobile-web", True))
        self.assertEqual(F.read_value([{"name": "Checkout"}, {"name": "Cart"}]), ("Checkout, Cart", False))
        self.assertEqual(F.read_labels(["role-guest", "x", "role-admin"], "role-"), "guest, admin")
        self.assertEqual(F.jql_field("customfield_10051"), "cf[10051]")

    def test_the_fake_jql(self):
        issue = {"id": "1", "key": "DS-1", "fields": {"issuetype": {"name": "Test"}, "labels": ["a b"],
                                                      "summary": "Expired coupon", "status": {}}}
        self.assertTrue(jql_match('project = "DS" AND (summary ~ "\\"coupon\\"" OR labels in ("x"))', issue))
        self.assertFalse(jql_match('key in ("DS-2") AND issuetype = "Test"', issue))


if __name__ == "__main__":
    unittest.main()
