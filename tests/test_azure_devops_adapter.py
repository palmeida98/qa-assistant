"""The Azure DevOps Services back-end (technique-azure-devops.md; plan 10.3) against a stateful fake Azure DevOps
(tests/ado_fake.py): check (process template, types, fields, states, plan, suites, configurations), apply (new and
existing bugs, bug_status with the `test` on /rev, one test run per batch, outcomes, not-sent statuses, points per
configuration, chunks, evidence, read back, the uncertain write and the marker search), bug-search with its relaxed
passes, import-cases with continuation tokens, export-cases, the round trip (section 8), HTML and the steps XML.
Fictional Demo Shop data only; no network.

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

from ado_fake import FakeAzureDevOps, wiql_match
from tracker_kit import ADO_ENV, PROJECT, Workspace

from adapters.azure_devops import fields as F
from adapters.azure_devops import steps as S
from adapters.azure_devops.api import wiql_string
from adapters.azure_devops.html import from_html, to_html
from adapters.tracker import cli
from adapters.tracker.http import Response
from adapters.tracker.record import IdCache

ORG = "https://dev.azure.com/demo-shop"
AZURE = {
    "organization_url": ORG, "project": "Demo Shop", "pat_var": "DS_ADO_PAT",
    "cases": {"plan_id": 12, "new_cases_suite_id": 13, "area_path": "Demo Shop\\Checkout", "fields": {
        "area": "System.AreaPath", "feature": "Custom.Feature", "preconditions": "System.Description",
        "priority": "Microsoft.VSTS.Common.Priority", "role": "tag:role-", "platform": "tag:platform-",
        "technique": "tag:technique-", "origin": "tag:origin-"}},
    "results": {"outcomes": {"Pass": "Passed", "Fail": "Failed", "Blocked": "Blocked", "N/A": "NotApplicable",
                             "Not run": "skip"},
                "configurations": {"web": "Windows 11 - Chrome", "android": "Android 15"}},
    "bugs": {"fields": {"title": "System.Title", "description": "Microsoft.VSTS.TCM.ReproSteps",
                        "area": "System.AreaPath", "feature": "Custom.Feature", "sub_area": "tag:subarea-",
                        "platform": "tag:platform-", "role": "tag:role-",
                        "environment": "Microsoft.VSTS.TCM.SystemInfo", "severity": "Microsoft.VSTS.Common.Severity",
                        "priority": "Microsoft.VSTS.Common.Priority"},
             "states": ["Resolved", "Closed"], "priorities": ["1", "2", "3", "4"]},
}
ADO_PROJECT = {**{k: v for k, v in PROJECT.items() if k != "jira"}, "tracker": "azure-devops",
               "azure_devops": AZURE}
RUN = {"environment": "test", "build": "2.4.1"}
TS = "2026-09-28T10:00:00+01:00"
STEPS_XML = S.to_xml([{"action": "Open the cart", "data": "", "expected": ""},
                      {"action": "Apply the coupon", "data": "CODE1", "expected": "The discount is shown"}])


def case_fields(title, area="Demo Shop\\Checkout", tags="role-customer; platform-web; technique-boundary-values; "
                                                          "origin-requirement"):
    return {"System.Title": title, "System.AreaPath": area, "Custom.Feature": "Coupons",
            "Microsoft.VSTS.Common.Priority": 2, "System.Tags": tags,
            "System.Description": to_html("A cart with one product"), "Microsoft.VSTS.TCM.Steps": STEPS_XML}


def seed(fake, n_cases=4):
    """n Test Cases (4711, 4712, ...) in suite 13 with one point each (Windows 11 - Chrome), and the bug 5001
    (Active)."""
    for i in range(n_cases):
        wid = 4711 + i
        fake.new_item("Test Case", case_fields(f"Coupon case {wid}"), wid=wid)
        fake.add_to_suite(wid)
    fake.new_item("Bug", {"System.Title": "Coupon field accepts spaces", "System.AreaPath": "Demo Shop\\Checkout"},
                  wid=5001, state="Active")
    return fake


class Base(unittest.TestCase):
    azure = AZURE

    def setUp(self):
        self.ws = Workspace({**ADO_PROJECT, "azure_devops": copy.deepcopy(self.azure)}, env=ADO_ENV)
        self.addCleanup(self.ws.cleanup)
        self.fake = seed(FakeAzureDevOps())
        self.lines = []

    def configure(self, **changes):
        azure = copy.deepcopy(self.azure)
        for key, value in changes.items():
            node = azure
            *parts, last = key.split("__")
            for p in parts:
                node = node[p]
            node[last] = value
        self.ws.project({**ADO_PROJECT, "azure_devops": azure})

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

    def probe(self, *args):
        """How many requests a dry run makes (the same reads as a --yes run before its first write)."""
        probe = seed(FakeAzureDevOps())
        cli.main([self.ws.path, *args], transport=probe, printer=[].append)
        return len(probe.requests)


def item(tc, status, **kw):
    return {"tc": tc, "status": status, "actual_result": kw.pop("actual", f"observed on {tc}"), "ts": TS, **kw}


NEW_DEFECT = {"title": "Expired coupon accepted", "expected": "The coupon is refused",
              "observed": "The expired coupon is accepted", "steps": ["Open the cart", "Apply EXPIRED10"],
              "area": "Demo Shop\\Checkout", "feature": "Coupons", "sub_area": "cart", "platform": "web",
              "role": "customer", "priority": "2", "severity": "2 - High"}


class Check(Base):
    def test_everything_present_is_exit_0(self):
        self.assertEqual(self.run_cli("check"), 0, self.out)
        self.assertIn("Process: Agile", self.out)
        self.assertEqual(self.writes(), [])

    def test_an_inherited_agile_process_is_accepted(self):
        self.fake.process = "Inherited"
        self.assertEqual(self.run_cli("check"), 0, self.out)
        self.assertIn("inherited from Agile", self.out)

    def test_basic_is_refused_and_cmmi_not_covered(self):
        self.fake.process = "Basic"
        self.assertEqual(self.run_cli("check"), 4)
        self.assertIn("the project uses the Basic process, which has no Bug work item type", self.out)
        self.assertIn("work item type 'Bug' is not in project", self.out)
        self.fake.process = "CMMI"
        self.assertEqual(self.run_cli("check"), 4)
        self.assertIn("CMMI process, not covered in version 1", self.out)

    def test_what_is_missing_is_listed(self):
        self.fake.process = "Scrum"
        self.configure(bugs__fields={**AZURE["bugs"]["fields"], "feature": "Custom.Nope",
                                     "severity": "Custom.QaId"},
                       results__configurations={"web": "Windows 11 - Chrome", "ios": "iOS 18"},
                       cases__suite_ids=[13, 99])
        self.assertEqual(self.run_cli("check"), 4)
        for part in ("'Custom.Nope' does not exist", "'Custom.QaId' is not a field of 'Bug'",
                     "bugs.states: 'Resolved' is not a state of 'Bug'", "bugs.closed_states: 'Closed'",
                     "'iOS 18' is not a Test Configuration", "suite 99 is not in test plan 12"):
            self.assertIn(part, self.out)

    def test_a_wrong_token_is_exit_3_without_the_token(self):
        def refuse(req, real=self.fake):
            if "/_apis/projects/" in req.url:
                return Response(401, {}, b'{"message": "unauthorized"}')
            return real(req)
        self.assertEqual(cli.main([self.ws.path, "check"], transport=refuse, printer=self.lines.append), 3)
        text = "\n".join(self.lines)
        self.assertNotIn(ADO_ENV["DS_ADO_PAT"], text)
        self.assertNotIn(base64.b64encode(f":{ADO_ENV['DS_ADO_PAT']}".encode()).decode(), text)


class Apply(Base):
    def full_batch(self):
        return self.batch([
            item("4711", "Pass"),
            item("4712", "Fail", new_defect=NEW_DEFECT),
            item("4713", "Fail", bug="5001", actual="still accepts spaces"),
            item("4714", "Blocked", bug="5001", bug_status="Resolved"),
        ])

    def test_dry_run_resolves_and_writes_nothing(self):
        path = self.full_batch()
        self.assertEqual(self.run_cli("apply", path), 1, self.out)
        self.assertEqual(self.writes(), [])
        for part in ("POST /_apis/wit/workitems/$Bug — 4712: Bug \"Expired coupon accepted\"",
                     "POST /_apis/test/runs — new test run \"batch_20260928_01 · test · 2.4.1\"",
                     "5001: Active -> Resolved (test /rev 1)", "5001: comment", "4711 (point 9001) Passed"):
            self.assertIn(part, self.out)
        self.assertFalse(self.ws.exists("tracker/sent/batch_20260928_01.json"))

    def test_yes_sends_everything_and_reads_back(self):
        path = self.full_batch()
        self.assertEqual(self.run_cli("apply", path, "--yes", "--now", "2026-09-28T12:00:00"), 0, self.out)
        bug = self.fake.items[5002]
        f = bug["fields"]
        self.assertEqual((f["System.WorkItemType"], f["System.Title"], f["System.AreaPath"]),
                         ("Bug", "Expired coupon accepted", "Demo Shop\\Checkout"))
        self.assertEqual(F.split_tags(f["System.Tags"]), ["qa-batch-batch_20260928_01", "qa-case-4712",
                                                          "platform-web", "role-customer", "subarea-cart"])
        self.assertEqual((f["Custom.Feature"], f["Microsoft.VSTS.Common.Priority"],
                          f["Microsoft.VSTS.Common.Severity"]), ("Coupons", 2, "2 - High"))
        text, _ = from_html(f["Microsoft.VSTS.TCM.ReproSteps"])
        for part in ("Environment: test · Build: 2.4.1", "Expected: The coupon is refused", "Linked cases: 4712",
                     "- Apply EXPIRED10"):
            self.assertIn(part, text)
        self.assertEqual(bug["relations"][0]["rel"], "Microsoft.VSTS.Common.TestedBy-Forward")
        self.assertEqual(bug["relations"][0]["url"], f"{ORG}/_apis/wit/workItems/4712")
        (run,) = self.fake.runs.values()
        self.assertEqual((run["name"], run["state"], run["comment"], run["isAutomated"]),
                         ("batch_20260928_01 · test · 2.4.1", "Completed",
                          "qa-batch-batch_20260928_01 · environment test · build 2.4.1", False))
        by_case = {r["testCase"]["id"]: r for r in run["results"].values()}
        self.assertEqual({k: v["outcome"] for k, v in by_case.items()},
                         {"4711": "Passed", "4712": "Failed", "4713": "Failed", "4714": "Blocked"})
        self.assertEqual(by_case["4712"]["associatedBugs"], [{"id": "5002"}])
        self.assertEqual(by_case["4713"]["associatedBugs"], [{"id": "5001"}])
        self.assertEqual(by_case["4713"]["comment"], "still accepts spaces")
        self.assertEqual({p["case"]: p["outcome"] for p in self.fake.points.values()},
                         {4711: "passed", 4712: "failed", 4713: "failed", 4714: "blocked"})
        self.assertEqual(self.fake.items[5001]["fields"]["System.State"], "Resolved")
        comments = self.fake.comments[5001]
        self.assertEqual(len(comments), 2)
        self.assertIn("qa-batch-batch_20260928_01 · 4713: Fail", from_html(comments[0]["text"])[0])
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertTrue(rec["complete"])
        self.assertEqual([w["kind"] for w in rec["writes"]],
                         ["bug", "state", "run", "results", "run_completed", "comment", "comment"])
        self.assertEqual(IdCache(self.ws.path, "azure-devops").get("4711"),
                         {"id": 4711, "points": {"Windows 11 - Chrome": 9001}})
        self.assertIn("Read back: it matches", self.out)
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_not_sent_statuses_and_question(self):
        path = self.batch([item("4711", "Pass"), item("4712", "Not run", new_defect=NEW_DEFECT)])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        (run,) = self.fake.runs.values()
        self.assertEqual(len(run["results"]), 1)
        self.assertIn("4712 (Not run): mapped to skip", self.out)
        self.assertIn("its new_defect is not created either", self.out)
        self.assertEqual(self.fake.of_type("Bug")[1:], [])
        path = self.batch([item("4711", "Question")], name="batch_q")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 4)
        self.assertEqual(self.made, [])

    def test_the_platform_chooses_the_point(self):
        self.fake.suites[13]["configs"] = [1, 2]
        for wid in (4711, 4712):
            self.fake.suites[13]["cases"].remove(wid)
            for pid in [p for p, v in self.fake.points.items() if v["case"] == wid]:
                del self.fake.points[pid]
            self.fake.add_to_suite(wid)
        no_platform = self.batch([item("4711", "Pass")], name="b1")
        self.assertEqual(self.run_cli("apply", no_platform, "--yes"), 2)
        self.assertIn("2 test points", self.out)
        self.assertEqual(self.writes(), [])
        path = self.batch([item("4711", "Pass", platform="android"), item("4712", "Fail", platform="web")],
                          name="b2")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        outcomes = {(p["case"], self.fake.configs[p["config"]]): p["outcome"] for p in self.fake.points.values()
                    if p["case"] in (4711, 4712)}
        self.assertEqual(outcomes, {(4711, "Windows 11 - Chrome"): "unspecified", (4711, "Android 15"): "passed",
                                    (4712, "Windows 11 - Chrome"): "failed", (4712, "Android 15"): "unspecified"})

    def test_chunks_of_100_stay_in_one_run(self):
        fake = seed(FakeAzureDevOps(page=50), n_cases=205)
        path = self.batch([item(str(4711 + i), "Pass") for i in range(205)])
        self.assertEqual(self.run_cli("apply", path, "--yes", fake=fake), 0, self.out)
        self.assertEqual(len(fake.runs), 1)
        patches = [r for r in self.writes() if r.method == "PATCH" and r.path.split("?")[0].endswith("/results")]
        self.assertEqual([len(r.json()) for r in patches], [100, 100, 5])
        self.assertIn(50, fake.pages)   # the test points came in pages of 50 (x-ms-continuationtoken)
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual([(w["kind"], w.get("chunk")) for w in rec["writes"]],
                         [("run", None), ("results", 1), ("results", 2), ("results", 3), ("run_completed", None)])

    def test_an_uncertain_bug_create_is_found_by_its_markers(self):
        path = self.batch([item("4711", "Pass"), item("4712", "Fail", new_defect=NEW_DEFECT)])
        self.fake.drop_answer_at = self.probe("apply", path) + 1   # the first write: the bug
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3, self.out)
        self.assertIn("never retried blindly", self.out)
        self.assertEqual(len(self.fake.of_type("Bug")), 2)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("found by its markers", self.out)
        self.assertEqual(len(self.fake.of_type("Bug")), 2)   # not created again
        rec = self.ws.read_json("tracker/sent/batch_20260928_01.json")
        self.assertEqual([(w["kind"], w.get("reused")) for w in rec["writes"]],
                         [("bug", True), ("run", None), ("results", None), ("run_completed", None)])

    def test_an_uncertain_run_create_is_found_by_its_marker(self):
        path = self.batch([item("4711", "Pass")])
        self.fake.drop_answer_at = self.probe("apply", path) + 1
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(len(self.fake.runs), 1)
        self.assertIn("found by its marker", self.out)

    def test_a_request_that_never_arrived_is_sent_on_the_next_run(self):
        path = self.batch([item("4711", "Pass")])
        self.fake.fail_at = self.probe("apply", path) + 1
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3)
        self.assertEqual(self.fake.runs, {})
        self.fake.fail_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(len(self.fake.runs), 1)

    def test_a_comment_without_an_answer_is_not_repeated(self):
        path = self.batch([item("4713", "Fail", bug="5001")])
        # run, results, run completed, then the comment
        self.fake.drop_answer_at = self.probe("apply", path) + 5
        self.assertEqual(self.run_cli("apply", path, "--yes"), 3, self.out)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("the comment on 5001 is already there", self.out)
        self.assertEqual(len(self.fake.comments[5001]), 1)

    def test_a_concurrent_edit_of_a_bug_is_exit_2(self):
        path = self.batch([item("4711", "Pass", bug="5001", bug_status="Closed")])

        def edit_first(req, real=self.fake):
            if req.method == "PATCH" and "/wit/workitems/5001" in req.url:
                real.items[5001]["rev"] += 1   # someone edited the bug after the resolution
            return real(req)
        self.assertEqual(cli.main([self.ws.path, "apply", path, "--yes"], transport=edit_first,
                                  printer=self.lines.append), 2, "\n".join(self.lines))
        self.assertEqual(self.fake.items[5001]["fields"]["System.State"], "Active")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)   # read again, sent
        self.assertEqual(self.fake.items[5001]["fields"]["System.State"], "Closed")

    def test_the_read_back_difference_is_exit_5(self):
        self.fake.automation = {4712: "Passed"}
        path = self.batch([item("4711", "Pass"), item("4712", "Fail")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 5)
        self.assertIn("4712 (result 100002): Failed sent, Passed read back", self.out)
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_conflicts_are_exit_2_with_no_write(self):
        self.fake.new_item("Test Case", case_fields("Outside the plan"), wid=4800)
        cases = ((self.batch([item("5001", "Pass")], name="b1"), "is a Bug, not a Test Case"),
                 (self.batch([item("4799", "Pass")], name="b2"), "4799: no Test Case"),
                 (self.batch([item("TC-1", "Pass")], name="b3"), "not a work item id"),
                 (self.batch([item("4711", "Fail", bug="5999")], name="b4"), "5999"),
                 (self.batch([item("4800", "Pass")], name="b5"), "is not in test plan 12"))
        for path, part in cases:
            with self.subTest(part=part):
                self.assertEqual(self.run_cli("apply", path, "--yes"), 2, self.out)
                self.assertIn(part, self.out)
                self.assertEqual(self.writes(), [])
        self.configure(bugs__states=["Resolved", "Closed", "Verified"])
        path = self.batch([item("4711", "Pass", bug="5001", bug_status="Verified")], name="b6")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 2)
        self.assertIn("'Verified' is not a state of 'Bug'", self.out)
        ids = IdCache(self.ws.path, "azure-devops")
        ids.set("TC-SHOP-001", id=4712)
        ids.save()
        self.configure(cases__id_source="field", cases__id_field="Custom.QaId")
        self.fake.items[4711]["fields"]["Custom.QaId"] = "TC-SHOP-001"
        self.assertEqual(self.run_cli("apply", self.batch([item("TC-SHOP-001", "Pass")], name="b7"), "--yes"), 2)
        self.assertIn("now points elsewhere", self.out)

    def test_own_ids_by_tag(self):
        self.configure(cases__id_source="tag", cases__id_tag_prefix="qa-id-")
        self.fake.items[4711]["fields"]["System.Tags"] += "; qa-id-TC-SHOP-001"
        path = self.batch([item("TC-SHOP-001", "Pass")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertEqual(IdCache(self.ws.path, "azure-devops").get("TC-SHOP-001")["id"], 4711)

    def test_a_new_bug_area_must_be_a_path_of_the_project(self):
        path = self.batch([item("4712", "Fail", new_defect={**NEW_DEFECT, "area": "Checkout"})])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 4)
        self.assertIn("'Checkout' is not a path of project 'Demo Shop'", self.out)
        path = self.batch([item("4712", "Fail", new_defect={**NEW_DEFECT, "severity": "High"})], name="b2")
        self.assertEqual(self.run_cli("apply", path, "--yes"), 4)
        self.assertIn("'High' is not an allowed value of Microsoft.VSTS.Common.Severity", self.out)
        self.assertEqual(self.writes(), [])

    def test_evidence_only_with_the_policy(self):
        self.configure(results__attach_evidence=True)
        self.ws.file("evidence/20260928/console.txt", f"token {ADO_ENV['DS_ADO_PAT']} in the log")
        self.ws.file("evidence/20260928/cart.png", "PNG")
        path = self.batch([item("4712", "Fail", evidence=["evidence/20260928/console.txt",
                                                          "evidence/20260928/cart.png"])])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0, self.out)
        self.assertIn("cart.png: a screenshot/video goes only with evidence", self.out)
        (attachments,) = self.fake.attachments.values()
        self.assertEqual([a["fileName"] for a in attachments], ["console.txt"])
        self.assertEqual(base64.b64decode(attachments[0]["stream"]).decode("utf-8"),
                         "token «DS_ADO_PAT» in the log")   # a text file is redacted before it goes
        self.assertEqual(self.fake.items[4712]["relations"], [])   # evidence goes on the result, never on a bug

    def test_the_output_never_has_a_credential(self):
        path = self.batch([item("4712", "Fail", actual=f"the page showed {ADO_ENV['DS_ADO_PAT']}")])
        self.assertEqual(self.run_cli("apply", path, "--yes"), 0)
        record = json.dumps(self.ws.read_json("tracker/sent/batch_20260928_01.json"))
        (run,) = self.fake.runs.values()
        self.assertEqual([r["comment"] for r in run["results"].values()], ["the page showed «DS_ADO_PAT»"])
        for text in (self.out, record):
            self.assertNotIn(ADO_ENV["DS_ADO_PAT"], text)


class BugSearch(Base):
    def setUp(self):
        super().setUp()
        self.fake.new_item("Bug", {"System.Title": "Expired coupon accepted at checkout",
                                   "System.AreaPath": "Demo Shop\\Checkout", "Custom.Feature": "Coupons",
                                   "System.Tags": "subarea-cart; platform-mobile; role-guest"}, wid=5010,
                           state="Active")
        self.fake.new_item("Bug", {"System.Title": "Expired coupon accepted", "System.AreaPath": "Demo Shop\\Checkout",
                                   "Custom.Feature": "Coupons", "System.Tags": "platform-web"}, wid=5011,
                           state="Closed")

    def draft(self, **kw):
        keys = ("title", "observed", "area", "feature", "sub_area", "platform", "role")
        return self.ws.file("draft.json", {**{k: NEW_DEFECT[k] for k in keys}, "area": "Demo Shop\\Checkout", **kw})

    def test_the_passes_relax_until_a_candidate(self):
        self.assertEqual(self.run_cli("bug-search", self.draft()), 0, self.out)
        queries = [r.json()["query"] for r in self.made if r.path.startswith("/demo-shop/Demo%20Shop/_apis/wit/wiql")]
        self.assertEqual(len(queries), 3)   # strict, without the role, without the platform
        self.assertIn("[System.Tags] CONTAINS 'role-customer'", queries[0])
        self.assertIn("[System.State] NOT IN ('Closed', 'Removed')", queries[0])
        self.assertIn("[System.AreaPath] UNDER 'Demo Shop\\Checkout'", queries[0])
        self.assertIn("CONTAINS WORDS 'expired coupon accepted'", queries[0])
        self.assertNotIn("role-", queries[1])
        self.assertNotIn("platform-", queries[2])
        self.assertIn("5010 [Active] Expired coupon accepted at checkout — matches: area, feature, sub_area, text "
                      "(pass 3)", self.out)
        self.assertEqual(self.writes(), [])

    def test_include_closed_finds_the_fixed_bug(self):
        self.assertEqual(self.run_cli("bug-search", self.draft(sub_area=None, role=None), "--include-closed"), 0)
        self.assertNotIn("NOT IN", self.made[0].json()["query"])
        self.assertIn("5011 [Closed]", self.out)

    def test_nothing_found(self):
        self.assertEqual(self.run_cli("bug-search", self.draft(area="Demo Shop\\Payments", feature="Refunds",
                                                               observed="Refund never arrives", title="Refund")), 0)
        self.assertEqual(len(self.made), 5)   # strict + 4 relaxed passes, no batch read
        self.assertIn("No candidate found", self.out)


class ImportExport(Base):
    def test_import_follows_every_page(self):
        fake = seed(FakeAzureDevOps(page=100), n_cases=250)
        fake.points[9001]["outcome"] = "passed"
        fake.points[9002]["outcome"] = "blocked"
        fake.items[4713]["fields"]["Microsoft.VSTS.TCM.Steps"] = ""
        shared = fake.new_item("Shared Steps", {"System.Title": "Log in", "Microsoft.VSTS.TCM.Steps": S.to_xml(
            [{"action": "Log in as customer", "data": "", "expected": "The home page"}])})
        fake.items[4714]["fields"]["Microsoft.VSTS.TCM.Steps"] = (
            f'<steps id="0" last="3"><compref id="2" ref="{shared["id"]}"/>'
            + S.to_xml([{"action": "Pay", "data": "", "expected": "Paid"}])[len('<steps id="0" last="2">'):])
        fake.items[4715]["fields"]["Microsoft.VSTS.TCM.Parameters"] = '<parameters><param name="code"/></parameters>'
        self.assertEqual(self.run_cli("import-cases", fake=fake), 0, self.out)
        self.assertEqual(fake.pages[:3], [1, 100, 100])   # the suites, then the suite's test cases page by page
        doc = self.ws.read_json("tracker/cases.json")
        self.assertEqual(len(doc["cases"]), 250)
        c1 = doc["cases"][0]
        self.assertEqual({k: c1[k] for k in ("id", "key", "title", "area", "feature", "priority", "role", "platform",
                                              "technique", "origin", "preconditions", "expected", "last_status")},
                         {"id": "4711", "key": "4711", "title": "Coupon case 4711", "area": "Demo Shop\\Checkout",
                          "feature": "Coupons", "priority": "2", "role": "customer", "platform": "web",
                          "technique": "boundary-values", "origin": "requirement",
                          "preconditions": "A cart with one product", "expected": "The discount is shown",
                          "last_status": "Pass"})
        self.assertEqual(c1["steps"][1], {"action": "Apply the coupon", "data": "CODE1",
                                          "expected": "The discount is shown"})
        self.assertEqual(doc["cases"][1]["last_status"], "Blocked")
        self.assertEqual(doc["cases"][3]["steps"], [{"action": "Log in as customer", "data": "",
                                                     "expected": "The home page"},
                                                    {"action": "Pay", "data": "", "expected": "Paid"}])
        flags = {(f["case"], f["flag"].split(":")[0]) for f in doc["flags"]}
        for flag in (("4713", "no steps"), ("4714", "shared_steps"), ("4715", "parameters")):
            self.assertIn(flag, flags)
        self.assertEqual(IdCache(self.ws.path, "azure-devops").get("4960"),
                         {"id": 4960, "points": {"Windows 11 - Chrome": 9250}})

    def test_too_many_is_a_usage_error(self):
        fake = seed(FakeAzureDevOps(page=2), n_cases=5)
        from adapters.tracker import http
        try:
            http.paginate_token.__defaults__ = (3,)   # the guard of technique-jira-xray.md 2.7, lowered
            self.assertEqual(self.run_cli("import-cases", fake=fake), 4)
        finally:
            http.paginate_token.__defaults__ = (http.MAX_ITEMS,)
        self.assertIn("narrow the query", self.out)

    def test_a_query_instead_of_the_plan(self):
        self.fake.new_item("Test Case", case_fields("Outside the plan"), wid=4800)
        query = "SELECT [System.Id] FROM WorkItems WHERE [System.Title] = 'Outside the plan'"
        self.assertEqual(self.run_cli("import-cases", "--query", query), 0, self.out)
        doc = self.ws.read_json("tracker/cases.json")
        self.assertEqual([c["id"] for c in doc["cases"]], ["4800"])
        self.assertIn({"case": "4800", "flag": "not in test plan 12: no test point"}, doc["flags"])

    def test_two_cases_with_the_same_id_is_exit_2(self):
        self.configure(cases__id_source="tag", cases__id_tag_prefix="qa-id-")
        for wid in (4711, 4712):
            self.fake.items[wid]["fields"]["System.Tags"] += "; qa-id-TC-SHOP-001"
        self.assertEqual(self.run_cli("import-cases"), 2)
        self.assertIn("TC-SHOP-001", self.out)
        self.assertFalse(self.ws.exists("tracker/cases.json"))

    def new_cases(self, name="new_cases_01", **extra):
        cases = [{"id": "NEW-1", "title": "Expired coupon is refused", "area": "Demo Shop\\Checkout",
                  "feature": "Coupons", "preconditions": "A cart with one product\n\nAn expired coupon EXPIRED10",
                  "priority": "2", "role": "customer", "platform": "web", "technique": "boundary-values",
                  "origin": "requirement",
                  "steps": [{"action": "Apply EXPIRED10", "data": "EXPIRED10", "expected": "The coupon is refused"}],
                  **extra}]
        return self.ws.file(f"{name}.json", {"cases": cases})

    def test_export_dry_run_then_yes(self):
        path = self.new_cases()
        self.assertEqual(self.run_cli("export-cases", path), 1, self.out)
        self.assertIn("NEW-1: \"Expired coupon is refused\" (1 step(s), tag qa-batch-new_cases_01)", self.out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        wi = self.fake.items[5002]
        self.assertEqual(S.parse(wi["fields"]["Microsoft.VSTS.TCM.Steps"])[0],
                         [{"action": "Apply EXPIRED10", "data": "EXPIRED10", "expected": "The coupon is refused"}])
        self.assertIn("qa-batch-new_cases_01", F.split_tags(wi["fields"]["System.Tags"]))
        self.assertEqual(from_html(wi["fields"]["System.Description"])[0],
                         "A cart with one product\n\nAn expired coupon EXPIRED10")
        self.assertIn(5002, self.fake.suites[13]["cases"])
        self.assertIn("NEW-1 -> 5002", self.out)
        self.assertEqual(IdCache(self.ws.path, "azure-devops").get("5002"),
                         {"id": 5002, "points": {"Windows 11 - Chrome": 9005}})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0)
        self.assertEqual(self.made, [])

    def test_export_refuses_a_title_that_exists_and_an_area_outside_the_project(self):
        path = self.ws.file("dup.json", {"cases": [{"id": "NEW-1", "title": "Coupon case 4712"}]})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 2)
        self.assertIn("candidate duplicate", self.out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.run_cli("export-cases", self.new_cases("n2", area="Checkout"), "--yes"), 4)
        self.assertIn("is not a path of project", self.out)

    def test_export_with_own_ids(self):
        self.configure(cases__id_source="field", cases__id_field="Custom.QaId")
        self.fake.items[4711]["fields"]["Custom.QaId"] = "TC-SHOP-001"
        taken = self.ws.file("taken.json", {"cases": [{"id": "TC-SHOP-001", "title": "Another title"}]})
        self.assertEqual(self.run_cli("export-cases", taken, "--yes"), 2)
        self.assertIn("TC-SHOP-001: already exists as work item 4711", self.out)
        path = self.ws.file("own.json", {"cases": [{"id": "TC-SHOP-009", "title": "Coupon for guests"}]})
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        self.assertEqual(self.fake.items[5002]["fields"]["Custom.QaId"], "TC-SHOP-009")
        self.assertEqual(IdCache(self.ws.path, "azure-devops").get("TC-SHOP-009")["id"], 5002)

    def test_an_uncertain_create_is_not_repeated_and_the_suite_add_resumes(self):
        path = self.new_cases()
        self.fake.drop_answer_at = self.probe("export-cases", path) + 1
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 3)
        self.fake.drop_answer_at = None
        self.assertEqual(self.run_cli("export-cases", path, "--yes"), 0, self.out)
        self.assertEqual([w["id"] for w in self.fake.of_type("Test Case") if w["id"] > 5000], [5002])
        self.assertIn("reused, not created again", self.out)
        self.assertIn(5002, self.fake.suites[13]["cases"])

    def test_export_needs_the_suite(self):
        self.configure(cases__new_cases_suite_id=None)
        azure = copy.deepcopy(AZURE)
        del azure["cases"]["new_cases_suite_id"]
        self.ws.project({**ADO_PROJECT, "azure_devops": azure})
        self.assertEqual(self.run_cli("export-cases", self.new_cases()), 4)
        self.assertIn("new_cases_suite_id is missing", self.out)

    def test_round_trip(self):
        """technique section 8: import -> export to an empty tracker -> import; the cases are the same (IDs apart)."""
        source = seed(FakeAzureDevOps(), n_cases=3)
        source.items[4712]["fields"]["Microsoft.VSTS.TCM.Steps"] = S.to_xml(
            [{"action": "Open the cart\nwith one product", "data": "", "expected": ""},
             {"action": "Pay", "data": "card 4111\nexpiry 12/30", "expected": "Paid <ok> & done"}])
        source.items[4713]["fields"]["System.Description"] = to_html("One product\n\n- in the cart\n- with a coupon")
        self.assertEqual(self.run_cli("import-cases", fake=source), 0, self.out)
        first = self.ws.read_json("tracker/cases.json")["cases"]
        cases = [{**c, "id": f"NEW-{n}"} for n, c in enumerate(first, 1)]
        path = self.ws.file("round_trip.json", {"cases": cases})
        target = FakeAzureDevOps()
        self.assertEqual(self.run_cli("export-cases", path, "--yes", fake=target), 0, self.out)
        self.assertEqual(self.run_cli("import-cases", fake=target), 0, self.out)
        second = self.ws.read_json("tracker/cases.json")["cases"]
        strip = ("id", "key", "last_status")
        self.assertEqual([{k: v for k, v in c.items() if k not in strip} for c in first],
                         [{k: v for k, v in c.items() if k not in strip} for c in second])
        self.assertEqual([c["last_status"] for c in second], [None] * 3)   # new points: no outcome yet


class Formatting(unittest.TestCase):
    def test_html_round_trip(self):
        for text in ("", "One line", "Two\nlines", "A\n\nB", "- one\n- two", "Intro\n\n- a\n- b\n\nEnd",
                     "a < b & c > d", "  indented"):
            with self.subTest(text=text):
                self.assertEqual(from_html(to_html(text)), (text, False))

    def test_html_from_people_is_simplified(self):
        self.assertEqual(from_html("<div><h2>Setup</h2><table><tr><td>a</td><td>b</td></tr></table></div>"),
                         ("Setup\n\na b", True))
        self.assertEqual(from_html("<div>Line 1<br>Line 2&nbsp;!</div>"), ("Line 1\nLine 2\xa0!", False))

    def test_steps_xml_round_trip(self):
        steps = [{"action": "Open <cart>", "data": "", "expected": ""},
                 {"action": "Apply", "data": "CODE & more", "expected": "Shown \"ok\""}]
        xml = S.to_xml(steps)
        self.assertTrue(xml.startswith('<steps id="0" last="3"><step id="2" type="ActionStep">'))
        self.assertIn('<step id="3" type="ValidateStep">', xml)
        self.assertEqual(S.parse(xml), (steps, False))
        self.assertEqual(S.to_xml([]), "")

    def test_values_and_tags(self):
        self.assertEqual(F.format_value("Microsoft.VSTS.Common.Priority", {"type": "integer"}, "2"), (2, None))
        self.assertIn("not a number", F.format_value("Microsoft.VSTS.Common.Priority", {"type": "integer"}, "High")[1])
        self.assertEqual(F.format_value("System.Description", {"type": "html"}, "a\nb")[0], "<p>a<br>b</p>")
        self.assertEqual(F.tag_value("platform-", "web; mobile"), ("platform-web, mobile", True))
        self.assertEqual(F.join_tags(["a", "B", "b", ""]), "a; B")
        self.assertEqual(F.read_tags("role-guest; x; role-admin", "role-"), "guest, admin")
        self.assertEqual(wiql_string("O'Brien"), "'O''Brien'")

    def test_the_fake_wiql(self):
        wi = {"id": 1, "fields": {"System.TeamProject": "Demo Shop", "System.Tags": "a b; qa-x",
                                  "System.Title": "Expired coupon", "System.AreaPath": "Demo Shop\\Checkout\\Cart"}}
        self.assertTrue(wiql_match("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND "
                                   "([System.Tags] CONTAINS 'qa-x' OR [System.Id] IN (7)) AND [System.AreaPath] "
                                   "UNDER 'Demo Shop\\Checkout' ORDER BY [System.Id]", wi))
        self.assertFalse(wiql_match("SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'qa'", wi))


if __name__ == "__main__":
    unittest.main()
