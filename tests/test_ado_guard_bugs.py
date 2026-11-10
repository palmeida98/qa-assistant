"""The controlled creation of bugs in the guard (plan C, 10.11.2026): scripts/ado_write_guard.py `ado_guard_preview_bug` and
`ado_guard_create_bug`. Everything is fictional: an in-memory fake of the Azure DevOps REST calls (nothing is sent anywhere,
no real ticket, no real bug). The fake can simulate a server that does NOT commit the Parent link with the fields, a lost
answer, a refusal and a server error, to show what the guard reports.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import ado_write_guard as g
from test_ado_write_guard import ME, OTHER

BASE = "https://dev.azure.com/example-org"
ALLOWED_PATHS = {"/fields/System.Title", "/fields/Microsoft.VSTS.TCM.ReproSteps", "/fields/Microsoft.VSTS.TCM.SystemInfo",
                 "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "/fields/System.AssignedTo", "/fields/System.AreaPath",
                 "/fields/System.IterationPath", "/relations/-"}
BUG = {"parent_id": 100, "title": "[Chat] The app freezes when the draft is restored", "repro_steps": "<ol><li>Open</li></ol>",
       "system_info": "Android 13, build 1.0.0 (38)", "acceptance_criteria": "Given a draft, then no freeze"}


def parent(wid=100, kind="Product Backlog Item", title="[Chat] Parent", assigned=ME, project="EXAMPLE", state="Done", rev=7,
           area="EXAMPLE", iteration="EXAMPLE\\Demo", unique="ana@example.invalid", children=()):
    f = {"System.Id": wid, "System.TeamProject": project, "System.Rev": rev, "System.WorkItemType": kind, "System.State": state,
         "System.Title": title, "System.AreaPath": area, "System.IterationPath": iteration}
    if assigned:
        f["System.AssignedTo"] = {"displayName": "Ana Example", "id": assigned, "uniqueName": unique}
    if area is None:
        f.pop("System.AreaPath")
    return {"id": wid, "rev": rev, "fields": f,
            "relations": [{"rel": "System.LinkTypes.Hierarchy-Forward", "url": f"{BASE}/_apis/wit/workItems/{c}"} for c in children]}


def type_fields(extra=(), missing=()):
    """The shape of `GET .../workitemtypes/Bug` of the test project (read on 10.11.2026): `fields` with referenceName, alwaysRequired
    and defaultValue; required without a default: Title (ours) and the area and iteration ids (set through the paths); required with
    a default: State, Value Area and the Custom.DEV/QA/PREPROD/PROD flags."""
    names = ["System.Title", "Microsoft.VSTS.TCM.ReproSteps", "Microsoft.VSTS.TCM.SystemInfo", "Microsoft.VSTS.Common.AcceptanceCriteria",
             "System.AssignedTo", "System.AreaPath", "System.IterationPath", "System.Reason", "System.Id"]
    rows = [{"referenceName": n, "alwaysRequired": n == "System.Title", "defaultValue": None} for n in names if n not in missing]
    rows += [{"referenceName": "System.AreaId", "alwaysRequired": True, "defaultValue": None},
             {"referenceName": "System.IterationId", "alwaysRequired": True, "defaultValue": None},
             {"referenceName": "System.State", "alwaysRequired": True, "defaultValue": "To Do"},
             {"referenceName": "Microsoft.VSTS.Common.ValueArea", "alwaysRequired": True, "defaultValue": "Business"}]
    rows += [{"referenceName": f"Custom.{x}", "alwaysRequired": True, "defaultValue": "0"} for x in ("DEV", "QA", "PREPROD", "PROD")]
    return {"fields": rows + list(extra)}


class BugHttp:
    """The few calls the bug creation makes, answered in memory; every call recorded; behaviours chosen per test."""

    def __init__(self, items, **kw):
        self.items = {k: json.loads(json.dumps(v)) for k, v in items.items()}
        self.calls = []
        self.type_fields = kw.get("type_fields", type_fields())
        self.wiql_ids = kw.get("wiql_ids", [])
        self.wiql_status = kw.get("wiql_status", 200)
        self.create = kw.get("create", "ok")           # ok | no_link | other_assignee | 400 | 500 | lost | lost_not_created
        self.after_first_parent_get = kw.get("after_first_parent_get")
        self.parent_gets = 0
        self.new_id = 900

    def request(self, method, url, body=None, content_type=None, use_write=False, read=False):
        self.calls.append({"method": method, "url": url, "body": body, "ctype": content_type, "read": read})
        if method == "GET" and "/workitemtypes/Bug?" in url:
            return 200, self.type_fields
        if method == "POST" and "/wiql" in url:
            return self.wiql_status, {"workItems": [{"id": i} for i in self.wiql_ids]}
        if method == "POST" and "/workitems/$Bug" in url:
            return self._create(json.loads(body))
        if method == "GET" and "/workitems?ids=" in url:
            ids = [int(i) for i in url.split("ids=")[1].split("&")[0].split(",")]
            return 200, {"value": [{"id": i, "fields": self.items[i]["fields"]} for i in ids if i in self.items]}
        if method == "GET" and "/workitems/" in url:
            wid = int(url.split("/workitems/")[1].split("?")[0])
            if wid == 100 or wid == 200:
                self.parent_gets += 1
                if self.parent_gets == 4 and self.after_first_parent_get:      # the second full read, right before writing
                    self.after_first_parent_get(self)
            if wid not in self.items:
                return 404, {}
            doc = json.loads(json.dumps(self.items[wid]))
            if "$expand=relations" in url:      # the REAL service returns the id at the top level only (read on 10.11.2026)
                doc["fields"].pop("System.Id", None)
            return 200, doc
        return 400, {}

    def _create(self, ops):
        fields = {op["path"].split("/fields/")[1]: op["value"] for op in ops if op["path"].startswith("/fields/")}
        rel = [op["value"] for op in ops if op["path"] == "/relations/-"]
        if self.create in ("400", "500"):
            return int(self.create), {"message": "refused by the fake"}
        stored = self.create != "lost_not_created"
        if stored:
            f = {"System.Id": self.new_id, "System.TeamProject": "EXAMPLE", "System.WorkItemType": "Bug", "System.Rev": 1,
                 "System.State": "New", **fields}
            who = {"displayName": "Ana Example", "id": ME if self.create != "other_assignee" else OTHER, "uniqueName": fields["System.AssignedTo"]}
            f["System.AssignedTo"] = who
            relations = [] if self.create == "no_link" else rel
            self.items[self.new_id] = {"id": self.new_id, "rev": 1, "fields": f, "relations": relations}
        if self.create in ("lost", "lost_not_created"):
            return 0, {"error": "no HTTP response (TimeoutError)"}
        return 201, {"id": self.new_id}

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST" and "/workitems/$Bug" in c["url"]]

    def methods(self):
        return sorted({(c["method"], "wiql" if "/wiql" in c["url"] else "other") for c in self.calls})


class Base(unittest.TestCase):
    def guard(self, items=None, enabled=True, **kw):
        items = items if items is not None else {100: parent()}
        self.http = BugHttp(items, **kw)
        self.logs = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return g.Guard("example-org", "EXAMPLE", ME, self.tmp.name, self.http, self.logs.append, allow_bug_create=enabled)

    def call(self, gd, name, args):
        r = g.call_tool(gd, name, args, self.logs.append)
        text = r["content"][0]["text"]
        return r, (json.loads(text) if not r.get("isError") else text)

    def preview(self, gd, **over):
        args = {**BUG, **over}
        args.pop("payload_sha256", None)
        return self.call(gd, "ado_guard_preview_bug", args)

    def create(self, gd, **over):
        args = {**BUG, **over}
        args.pop("payload_sha256", None)
        clean = g.Guard._clean_args(args)
        return self.call(gd, "ado_guard_create_bug", {**args, "payload_sha256": g.Guard.payload_hash(clean)})


class Disabled(Base):
    def setUp(self):
        self.logs = []

    def test_creation_is_off_by_default_and_never_writes(self):
        gd = g.Guard("example-org", "EXAMPLE", ME, ".", BugHttp({100: parent()}), write_enabled=True)
        self.assertFalse(gd.allow_bug_create)
        http = gd.http
        r, out = self.call(gd, "ado_guard_status", {})
        self.assertEqual((out["bug_creation"], out["bug_creation_atomicity_proven"]), ("disabled", False))
        args = {**BUG, "payload_sha256": g.Guard.payload_hash(g.Guard._clean_args(dict(BUG)))}
        r, out = self.call(gd, "ado_guard_create_bug", args)
        self.assertTrue(r["isError"])
        self.assertIn("DISABLED", out)
        self.assertEqual(http.posts(), [])
        self.assertEqual(http.calls, [])                                  # it stopped before reading anything else

    def test_the_launcher_flag_is_off_unless_it_is_exactly_1(self):
        for value, expected in ((None, False), ("", False), ("0", False), ("true", False), ("1", True)):
            env = {k: v for k, v in os.environ.items() if k != "ADO_GUARD_ALLOW_BUG_CREATE"}
            if value is not None:
                env["ADO_GUARD_ALLOW_BUG_CREATE"] = value
            seen: dict = {}

            class Spy:
                def __init__(self, *a, _seen=seen, **k):
                    _seen.update(k)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(g, "Guard", Spy), \
                    mock.patch.object(sys, "stdin", iter([])):
                g.main(["--org", "o", "--project", "p", "--identity-id", ME, "--allowed-dir", "."])
            self.assertEqual(seen["allow_bug_create"], expected, value)

    def test_the_preview_only_reads_even_when_creation_is_off(self):
        gd = self.guard(enabled=False)
        r, out = self.preview(gd)
        self.assertNotIn("isError", r)
        self.assertEqual((out["creation_enabled"], out["atomicity_proven"]), (False, False))
        self.assertEqual(self.http.posts(), [])
        self.assertTrue(all(c["method"] == "GET" or c["read"] for c in self.http.calls))     # a POST only for the WIQL read


class Content(Base):
    def test_only_the_defined_fields_exist(self):
        gd = self.guard()
        for extra in ({"assigned_to": "someone"}, {"state": "Done"}, {"area_path": "X"}, {"iteration_path": "X"},
                      {"severity": "1 - Critical"}, {"priority": 1}, {"tags": "x"}, {"url": "https://x"}, {"method": "DELETE"},
                      {"project": "Other"}, {"type": "Task"}, {"relations": []}, {"fields": {"System.Title": "x"}}):
            r, out = self.preview(gd, **extra)
            self.assertTrue(r["isError"], extra)
            self.assertIn("unexpected arguments", out)
        self.assertEqual(self.http.calls, [])

    def test_the_schema_is_closed_and_lists_no_dangerous_parameter(self):
        for name in ("ado_guard_preview_bug", "ado_guard_create_bug"):
            tool = next(t for t in g.TOOLS if t["name"] == name)
            self.assertIs(tool["inputSchema"]["additionalProperties"], False)
            self.assertEqual(set(tool["inputSchema"]["properties"]) - {"payload_sha256"},
                             {"parent_id", "title", "repro_steps", "system_info", "acceptance_criteria"})

    def test_a_bad_value_is_refused_before_any_read(self):
        gd = self.guard()
        for over in ({"parent_id": True}, {"parent_id": [1, 2]}, {"parent_id": 0}, {"title": ""}, {"title": "a\nb"},
                     {"title": "x" * 256}, {"repro_steps": "  "}, {"system_info": "a\x00b"}, {"repro_steps": "y" * 30001}):
            r, _ = self.preview(gd, **over)
            self.assertTrue(r["isError"], over)
        self.assertEqual(self.http.calls, [])

    def test_the_hash_is_of_what_is_sent_and_a_different_content_is_refused(self):
        gd = self.guard()
        r, out = self.preview(gd)
        good = out["payload_sha256"]
        r, _ = self.call(gd, "ado_guard_create_bug", {**BUG, "payload_sha256": good})
        self.assertNotIn("isError", r)
        gd = self.guard()
        for changed in ({"title": BUG["title"] + " "}, {"repro_steps": BUG["repro_steps"] + "x"}, {"parent_id": 200}):
            r, text = self.call(gd, "ado_guard_create_bug", {**BUG, **changed, "payload_sha256": good})
            self.assertTrue(r["isError"], changed)
            self.assertIn("payload_sha256 does not match", text)
        self.assertEqual(self.http.posts(), [])
        r, text = self.call(gd, "ado_guard_create_bug", {**BUG, "payload_sha256": "0" * 64})
        self.assertTrue(r["isError"])


class ParentChecks(Base):
    def blocked(self, parent_item, expect, **over):
        gd = self.guard({100: parent_item})
        r, text = self.create(gd, **over)
        self.assertTrue(r["isError"], text)
        self.assertIn(expect, text)
        self.assertEqual(self.http.posts(), [])

    def test_a_parent_of_another_person_without_assignee_or_of_another_project_is_refused(self):
        self.blocked(parent(assigned=OTHER), "assigned to another identity")
        self.blocked(parent(assigned=None), "no assignee")
        self.blocked(parent(project="Other"), "not 'EXAMPLE'")

    def test_only_a_pbi_or_a_qa_task_can_be_the_parent(self):
        self.blocked(parent(kind="Epic"), "only Product Backlog Item or Task")
        self.blocked(parent(kind="Bug"), "only Product Backlog Item or Task")
        self.blocked(parent(kind="Task", title="FE | Not a QA task"), "not a QA task")
        gd = self.guard({100: parent(kind="Task", title="QA | Análise e correção | X")})
        r, _ = self.create(gd)
        self.assertNotIn("isError", r)

    def test_a_removed_parent_or_one_without_area_iteration_or_unique_name_is_refused(self):
        self.blocked(parent(state="Removed"), "Removed")
        self.blocked(parent(area=None), "System.AreaPath")
        self.blocked(parent(unique=""), "unique name")

    def test_a_missing_parent_is_refused(self):
        gd = self.guard({})
        r, _text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertEqual(self.http.posts(), [])

    def test_the_parent_changing_between_the_check_and_the_creation_blocks_it(self):
        def change(h):
            h.items[100]["rev"] = 8
            h.items[100]["fields"]["System.Rev"] = 8
        gd = self.guard(after_first_parent_get=change)
        r, text = self.create(gd)
        self.assertTrue(r["isError"], text)
        self.assertIn("parent changed between the check and the creation", text)
        self.assertEqual(self.http.posts(), [])


class Process(Base):
    def test_a_bug_type_without_a_field_this_tool_writes_is_refused(self):
        gd = self.guard(type_fields=type_fields(missing=("Microsoft.VSTS.TCM.ReproSteps",)))
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("Microsoft.VSTS.TCM.ReproSteps", text)
        self.assertEqual(self.http.posts(), [])

    def test_a_field_the_process_requires_without_a_default_is_refused_and_one_with_a_default_is_not(self):
        required = {"referenceName": "Custom.Team", "alwaysRequired": True}
        gd = self.guard(type_fields=type_fields(extra=[required]))
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("Custom.Team", text)
        gd = self.guard(type_fields=type_fields(extra=[{**required, "defaultValue": "Core"}]))
        r, _ = self.create(gd)
        self.assertNotIn("isError", r)

    def test_an_optional_acceptance_criteria_field_may_be_absent_from_the_type(self):
        gd = self.guard(type_fields=type_fields(missing=("Microsoft.VSTS.Common.AcceptanceCriteria",)))
        r, _ = self.create(gd, acceptance_criteria="")
        self.assertNotIn("isError", r)


class Duplicates(Base):
    def child(self, wid, title, kind="Bug"):
        return {"id": wid, "fields": {"System.Title": title, "System.WorkItemType": kind}}

    def test_the_same_title_under_the_parent_is_refused_ignoring_case_and_spaces(self):
        items = {100: parent(children=(301, 302)), 301: self.child(301, "  [chat]   THE APP freezes when the draft is restored"),
                 302: self.child(302, "Something else")}
        gd = self.guard(items)
        r, text = self.create(gd)
        self.assertTrue(r["isError"], text)
        self.assertIn("possible duplicate", text)
        self.assertIn("301 (under the same parent)", text)
        self.assertEqual(self.http.posts(), [])

    def test_the_same_title_elsewhere_in_the_project_is_refused_and_a_task_with_that_title_is_not_a_bug(self):
        gd = self.guard(wiql_ids=[555])
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("555 (in the project)", text)
        items = {100: parent(children=(303,)), 303: self.child(303, BUG["title"], kind="Task")}
        gd = self.guard(items)
        r, _ = self.create(gd)
        self.assertNotIn("isError", r)

    def test_a_duplicate_search_that_cannot_run_blocks_the_creation(self):
        gd = self.guard(wiql_status=500)
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("duplicate search could not run", text)
        self.assertEqual(self.http.posts(), [])

    def test_the_wiql_text_escapes_quotes_and_is_a_read_with_the_read_credential(self):
        gd = self.guard()
        self.create(gd, title="It's a title with 'quotes'")
        wiql = next(c for c in self.http.calls if "/wiql" in c["url"])
        self.assertTrue(wiql["read"])
        self.assertIn("It''s a title with ''quotes''", json.loads(wiql["body"])["query"])


class Creation(Base):
    def test_the_request_is_one_post_with_the_fields_the_assignee_and_the_parent_link(self):
        gd = self.guard()
        r, out = self.create(gd)
        self.assertNotIn("isError", r, out)
        post = self.http.posts()[0]
        self.assertEqual(len(self.http.posts()), 1)
        self.assertEqual(post["url"], f"{BASE}/EXAMPLE/_apis/wit/workitems/$Bug?api-version=7.1")      # built from constants
        self.assertEqual(post["ctype"], "application/json-patch+json")
        ops = json.loads(post["body"])
        self.assertTrue({o["path"] for o in ops} <= ALLOWED_PATHS)
        self.assertTrue(all(o["op"] == "add" for o in ops))
        by = {o["path"]: o["value"] for o in ops}
        self.assertEqual(by["/fields/System.AssignedTo"], "ana@example.invalid")      # the parent's unique name, never an argument
        self.assertEqual((by["/fields/System.AreaPath"], by["/fields/System.IterationPath"]), ("EXAMPLE", "EXAMPLE\\Demo"))
        rels = [o["value"] for o in ops if o["path"] == "/relations/-"]
        self.assertEqual(len(rels), 1)
        self.assertEqual((rels[0]["rel"], rels[0]["url"]), (g.PARENT_REL, f"{BASE}/EXAMPLE/_apis/wit/workItems/100"))
        self.assertEqual(out["bug_id"], 900)

    def test_the_only_calls_are_reads_one_wiql_read_and_the_single_creation(self):
        gd = self.guard()
        self.create(gd)
        self.assertEqual(self.http.methods(), [("GET", "other"), ("POST", "other"), ("POST", "wiql")])
        for c in self.http.calls:
            self.assertNotIn(c["method"], ("PATCH", "PUT", "DELETE"))
        self.assertEqual([c["method"] for c in self.http.calls if c["method"] == "POST" and "/wiql" not in c["url"]], ["POST"])

    def test_the_result_is_read_back_and_a_correct_bug_is_verified(self):
        gd = self.guard()
        _r, out = self.create(gd)
        self.assertEqual((out["ok"], out["created"], out["read_back"], out["problems"]), (True, True, True, []))
        self.assertEqual(out["note"], "created and verified")
        self.assertFalse(out["atomicity_proven"])
        self.assertEqual(self.http.calls[-1]["method"], "GET")                           # the last thing it does is read

    def test_a_server_that_does_not_commit_the_parent_link_is_reported_with_the_bug_id_and_nothing_is_fixed(self):
        gd = self.guard(create="no_link")
        r, out = self.create(gd)
        self.assertNotIn("isError", r)
        self.assertEqual((out["ok"], out["created"], out["bug_id"]), (False, True, 900))
        self.assertTrue(any("NOT linked to the parent" in p for p in out["problems"]), out["problems"])
        self.assertIn("CREATED BUT NOT AS APPROVED", out["note"])
        self.assertEqual(len(self.http.posts()), 1)
        self.assertEqual([c for c in self.http.calls if c["method"] in ("PATCH", "PUT", "DELETE")], [])      # no repair, no delete

    def test_a_bug_born_with_another_assignee_is_reported_and_the_assignee_is_never_changed_afterwards(self):
        gd = self.guard(create="other_assignee")
        _r, out = self.create(gd)
        self.assertFalse(out["ok"])
        self.assertTrue(any("assignee is not the verified identity" in p for p in out["problems"]))
        self.assertEqual([c for c in self.http.calls if c["method"] in ("PATCH", "PUT", "DELETE")], [])

    def test_a_definite_refusal_creates_nothing_and_is_not_repeated(self):
        gd = self.guard(create="400")
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("not created (HTTP 400", text)
        self.assertEqual(len(self.http.posts()), 1)

    def test_a_lost_answer_looks_for_the_bug_first_and_never_sends_the_request_again(self):
        gd = self.guard(create="lost")                         # the fake created the bug and then lost the answer
        self.http.wiql_ids = []
        orig = self.http.request

        def request(method, url, *a, **k):
            if method == "POST" and "/wiql" in url and self.http.posts():
                self.http.wiql_ids = [900]                     # after the creation the search finds it
            return orig(method, url, *a, **k)
        self.http.request = request
        r, out = self.create(gd)
        self.assertNotIn("isError", r)
        self.assertEqual(len(self.http.posts()), 1)            # ONE creation request, never repeated
        self.assertEqual((out["created"], out["candidates"], out["bug_id"]), ("found after an uncertain answer", [900], 900))
        self.assertIn("NOT sent again", out["note"])
        self.assertTrue(out["read_back"])

    def test_a_lost_answer_with_no_bug_found_says_so_and_does_not_retry(self):
        gd = self.guard(create="lost_not_created")
        _r, out = self.create(gd)
        self.assertEqual((out["ok"], out["created"]), (False, "not found"))
        self.assertEqual(len(self.http.posts()), 1)
        self.assertIn("NOT sent again", out["note"])

    def test_a_server_error_is_uncertain_not_a_definite_refusal(self):
        gd = self.guard(create="500")
        r, out = self.create(gd)
        self.assertNotIn("isError", r)
        self.assertEqual(out["http_status"], 500)
        self.assertEqual(len(self.http.posts()), 1)

    def test_a_duplicate_search_that_fails_after_an_uncertain_answer_gives_unknown(self):
        gd = self.guard(create="lost")
        orig = self.http.request

        def request(method, url, *a, **k):
            if method == "POST" and "/wiql" in url and self.http.posts():
                return 500, {}
            return orig(method, url, *a, **k)
        self.http.request = request
        _r, out = self.create(gd)
        self.assertEqual(out["created"], "unknown")
        self.assertEqual(len(self.http.posts()), 1)

    def test_creating_twice_is_stopped_by_the_duplicate_rule(self):
        gd = self.guard()
        r, out = self.create(gd)
        self.assertTrue(out["ok"])
        self.http.wiql_ids = [900]                              # the search now finds the bug that was created
        r, text = self.create(gd)
        self.assertTrue(r["isError"])
        self.assertIn("possible duplicate", text)
        self.assertEqual(len(self.http.posts()), 1)


class CommentsAndAttachmentsStayIndividual(Base):
    def test_a_comment_on_the_new_bug_needs_its_own_hash_and_the_assignee_check(self):
        from test_ado_write_guard import FakeHttp, sha
        gd = self.guard()
        r, out = self.create(gd)
        bug = self.http.items[out["bug_id"]]
        fake = FakeHttp({out["bug_id"]: {"id": bug["id"], "rev": 1, "fields": {**bug["fields"], "System.AssignedTo": bug["fields"]["System.AssignedTo"]}}})
        gd2 = g.Guard("example-org", "EXAMPLE", ME, self.tmp.name, fake, self.logs.append)
        r, _text = self.call(gd2, "ado_guard_add_comment", {"work_item_id": 900, "text": "evidence", "text_sha256": "0" * 64})
        self.assertTrue(r["isError"])
        r, _ = self.call(gd2, "ado_guard_add_comment", {"work_item_id": 900, "text": "evidence", "text_sha256": sha("evidence")})
        self.assertNotIn("isError", r)

    def test_the_documentation_asks_for_approval_on_both_tools_and_never_allows_them(self):
        with open(os.path.join(ROOT, "docs", "technique-ado-write-guard.md"), encoding="utf-8") as f:
            doc = f.read()
        self.assertIn('"ask"', doc)
        ask = doc[doc.index('"ask"'):doc.index('"ask"') + 500]
        self.assertIn("ado_guard_create_bug", ask)
        self.assertIn("ado_guard_preview_bug", ask)
        allow = doc[doc.index('"allow"'):doc.index('"allow"') + 300] if '"allow"' in doc else ""
        self.assertNotIn("ado_guard_create_bug", allow)


if __name__ == "__main__":
    unittest.main()
