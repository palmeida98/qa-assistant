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
                 "/fields/Microsoft.VSTS.Common.AcceptanceCriteria", "/fields/System.AreaPath",
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
        self.create = kw.get("create", "ok")           # ok | no_link | other_assignee | html_spaces | 400 | 500 | lost | lost_not_created
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
        if method == "GET" and "/comments" in url:
            return 200, {"comments": []}
        if method == "POST" and "/comments" in url:
            return 200, {"id": 77}
        if method == "POST" and "/attachments" in url:
            return 201, {"id": "att", "url": f"{BASE}/_apis/wit/attachments/att"}
        if method == "PATCH":
            return 200, {}
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
                 "System.State": "New", **fields, "System.CreatedBy": {"displayName": "Ana Example", "id": ME}}
            if self.create == "other_assignee":           # a process rule that assigns someone despite the field being omitted
                f["System.AssignedTo"] = {"displayName": "Other", "id": OTHER, "uniqueName": "other@example.invalid"}
            if self.create == "html_spaces":              # what the real service did on 13.11.2026
                for ref in ("Microsoft.VSTS.TCM.ReproSteps", "Microsoft.VSTS.TCM.SystemInfo"):
                    f[ref] = f[ref].replace("</", " </")
            relations = [] if self.create == "no_link" else rel
            if relations:
                f["System.Parent"] = int(relations[0]["url"].rsplit("/", 1)[1])
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
        self.assertEqual(out["server_version"], g.SERVER_VERSION)
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
            only_preview = {"attachments", "creation_comment"} if name == "ado_guard_preview_bug" else set()
            self.assertEqual(set(tool["inputSchema"]["properties"]) - {"payload_sha256", "patch_sha256"},
                             {"parent_id", "title", "repro_steps", "system_info", "acceptance_criteria", "extra_fields"}
                             | only_preview)
            extra = tool["inputSchema"]["properties"]["extra_fields"]
            self.assertEqual(extra["additionalProperties"], {"type": "string"})     # texts only, keys from the project

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

    def test_a_removed_parent_or_one_without_area_or_iteration_is_refused(self):
        self.blocked(parent(state="Removed"), "Removed")
        self.blocked(parent(area=None), "System.AreaPath")

    def test_the_parent_unique_name_is_no_longer_needed_because_the_bug_is_born_unassigned(self):
        gd = self.guard({100: parent(unique="")})
        r, out = self.create(gd)
        self.assertNotIn("isError", r, out)
        self.assertTrue(out["ok"])

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
    def test_the_request_is_one_post_with_the_fields_and_the_parent_link_and_no_assignee(self):
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
        self.assertNotIn("/fields/System.AssignedTo", by)                                 # born unassigned (13.11.2026)
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

    def test_a_bug_born_with_an_assignee_is_reported_and_the_assignee_is_never_changed_afterwards(self):
        gd = self.guard(create="other_assignee")
        _r, out = self.create(gd)
        self.assertFalse(out["ok"])
        self.assertTrue(any("born WITH an assignee" in p for p in out["problems"]))
        self.assertEqual((out["bug_id"], out["state"], out["assigned_to"]), (900, "New", "another identity (Other)"))
        self.assertIn("do not create it again", out["note"])
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
        self.assertEqual((out["state"], out["assigned_to"]), ("New", "unassigned"))
        self.assertTrue(out["url"].endswith("/_workitems/edit/900"))
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
    def test_a_comment_on_the_new_bug_needs_its_own_hash(self):
        from test_ado_write_guard import sha
        gd = self.guard()
        r, out = self.create(gd)
        r, _text = self.call(gd, "ado_guard_add_comment", {"work_item_id": out["bug_id"], "text": "evidence", "text_sha256": "0" * 64})
        self.assertTrue(r["isError"])
        r, _ = self.call(gd, "ado_guard_add_comment", {"work_item_id": out["bug_id"], "text": "evidence", "text_sha256": sha("evidence")})
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



class UnassignedBugs(Base):
    """13.11.2026: a new bug is born unassigned; only the guard's own new bug may then receive attachments and comments."""

    def evidence(self, name="shot.png", data=b"png"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def comment(self, gd, wid, text="Comment for the developer"):
        from test_ado_write_guard import sha
        return self.call(gd, "ado_guard_add_comment", {"work_item_id": wid, "text": text, "text_sha256": sha(text)})

    def test_the_bug_is_born_unassigned_verified_and_recorded_in_the_ledger_file(self):
        gd = self.guard()
        ledger = os.path.join(self.tmp.name, "ledger", "created.json")
        gd.created_bugs_file = ledger
        _r, out = self.create(gd)
        self.assertEqual((out["ok"], out["assigned_to"], out["state"]), (True, "unassigned", "New"))
        self.assertNotIn("System.AssignedTo", self.http.items[900]["fields"])
        with open(ledger, encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual([(r["bug_id"], r["parent_id"]) for r in rows], [(900, 100)])
        self.assertEqual(set(rows[0]), {"bug_id", "parent_id", "payload_sha256", "created_utc"})       # no content

    def test_attachments_and_the_creation_comment_follow_on_the_unassigned_bug(self):
        gd = self.guard()
        _r, _out = self.create(gd)
        r, res = self.call(gd, "ado_guard_check", {"work_item_id": 900})
        self.assertNotIn("isError", r, res)
        self.assertEqual(res["assigned_to_verified"], False)
        self.assertIn("unassigned bug created by this guard under 100", res["allowed_as"])
        r, res = self.call(gd, "ado_guard_attach_file", {"work_item_id": 900, "file_path": self.evidence()})
        self.assertNotIn("isError", r, res)
        r, res = self.comment(gd, 900)
        self.assertNotIn("isError", r, res)
        self.assertEqual([c for c in self.http.calls if c["method"] in ("PUT", "DELETE")], [])
        patches = [json.loads(c["body"]) for c in self.http.calls if c["method"] == "PATCH"]
        self.assertEqual([[o["path"] for o in p] for p in patches], [["/rev", "/relations/-"]])       # only the attachment link

    def test_the_ledger_file_lets_a_restarted_guard_finish_the_steps(self):
        gd = self.guard()
        ledger = os.path.join(self.tmp.name, "created.json")
        gd.created_bugs_file = ledger
        self.create(gd)
        gd2 = g.Guard("example-org", "EXAMPLE", ME, self.tmp.name, self.http, self.logs.append, created_bugs_file=ledger)
        r, res = self.comment(gd2, 900)
        self.assertNotIn("isError", r, res)
        gd3 = g.Guard("example-org", "EXAMPLE", ME, self.tmp.name, self.http, self.logs.append)        # no ledger, new process
        r, res = self.comment(gd3, 900, "another")
        self.assertTrue(r["isError"])
        self.assertIn("no assignee", res)

    def unassigned(self, wid=500, kind="Bug", creator=ME, parent_id=100):
        return {"id": wid, "rev": 3, "fields": {"System.Id": wid, "System.TeamProject": "EXAMPLE", "System.Rev": 3,
                                                 "System.WorkItemType": kind, "System.State": "To Do", "System.Title": "x",
                                                 "System.CreatedBy": {"id": creator}, "System.Parent": parent_id}}

    def refused(self, items, ledger, expect):
        gd = self.guard(items)
        gd._created_bugs.update(ledger)
        r, res = self.comment(gd, 500)
        self.assertTrue(r["isError"], res)
        self.assertIn(expect, res)
        r, res = self.call(gd, "ado_guard_attach_file", {"work_item_id": 500, "file_path": self.evidence()})
        self.assertTrue(r["isError"], res)
        self.assertEqual([c for c in self.http.calls if c["method"] in ("POST", "PATCH", "PUT", "DELETE")], [])

    def test_every_other_unassigned_item_stays_refused(self):
        self.refused({100: parent(), 500: self.unassigned()}, {}, "no assignee")                       # not created by the guard
        self.refused({100: parent(), 500: self.unassigned(kind="Task")}, {500: 100}, "not a Bug")
        self.refused({100: parent(), 500: self.unassigned(creator=OTHER)}, {500: 100}, "not created by the verified identity")
        self.refused({100: parent(), 500: self.unassigned(parent_id=101)}, {500: 100}, "no longer has the parent")
        self.refused({100: parent(assigned=OTHER), 500: self.unassigned()}, {500: 100}, "no longer passes the check")
        self.refused({100: parent(assigned=None), 500: self.unassigned()}, {500: 100}, "no longer passes the check")

    def test_a_ledger_cycle_never_recurses(self):
        items = {500: self.unassigned(parent_id=501), 501: {**self.unassigned(wid=501, parent_id=500)}}
        gd = self.guard(items)
        gd._created_bugs.update({500: 501, 501: 500})
        r, res = self.comment(gd, 500)
        self.assertTrue(r["isError"])
        self.assertIn("no longer passes the check", res)

    def test_once_assigned_the_normal_rule_applies(self):
        gd = self.guard()
        self.create(gd)
        self.http.items[900]["fields"]["System.AssignedTo"] = {"id": OTHER, "uniqueName": "other@example.invalid"}
        r, res = self.comment(gd, 900)
        self.assertTrue(r["isError"])
        self.assertIn("assigned to another identity", res)

    def test_an_unreadable_ledger_opens_nothing(self):
        gd = self.guard({100: parent(), 500: self.unassigned()})
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        gd.created_bugs_file = bad
        r, res = self.comment(gd, 500)
        self.assertTrue(r["isError"])
        self.assertIn("no assignee", res)

    def test_a_bug_type_that_defaults_or_requires_assigned_to_blocks_the_creation(self):
        for row in ({"referenceName": "System.AssignedTo", "alwaysRequired": False, "defaultValue": "someone@example.invalid"},
                    {"referenceName": "System.AssignedTo", "alwaysRequired": True, "defaultValue": None}):
            tf = type_fields(missing=("System.AssignedTo",))
            tf["fields"].append(row)
            gd = self.guard(type_fields=tf)
            r, res = self.create(gd)
            self.assertTrue(r["isError"], res)
            self.assertIn("would not be born unassigned", res)
            self.assertEqual(self.http.posts(), [])

    def test_whitespace_added_before_closing_tags_by_azure_devops_is_not_a_problem(self):
        gd = self.guard(create="html_spaces")
        _r, out = self.create(gd, repro_steps="<div>a</div>\n<ol><li>b</li></ol>", system_info="<div>iOS</div>")
        self.assertEqual((out["ok"], out["problems"]), (True, []))
        self.assertEqual(sorted(out["html_normalised_by_azure_devops"]),
                         ["Microsoft.VSTS.TCM.ReproSteps", "Microsoft.VSTS.TCM.SystemInfo"])
        self.assertFalse(g.html_equivalent("<div>a b</div>", "<div>ab</div>"))                # any other difference counts
        self.assertFalse(g.html_equivalent("<div>a</div>x", "<div>a</div>"))


class VideoEvidence(Base):
    def setUp(self):
        self.gd = self.guard()

    def video(self, review=None, size=1000, name="TC-X_bug.mp4"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(b"v" * size)
        if review is not None:
            with open(path + ".review.json", "w", encoding="utf-8") as fh:
                fh.write(review if isinstance(review, str) else json.dumps(review))
        return path

    def attach(self, path):
        self.http.calls.clear()
        return self.call(self.gd, "ado_guard_attach_file", {"work_item_id": 100, "file_path": path})

    def test_a_reviewed_video_is_attached(self):
        for pd in ("none", "masked"):
            r, out = self.attach(self.video({"reviewed": True, "personal_data": pd, "reviewed_utc": "2026-11-13T01:00:00Z"}))
            self.assertNotIn("isError", r, out)
            self.assertEqual(out["file"], "TC-X_bug.mp4")

    def test_a_video_without_a_valid_review_record_is_refused_before_any_write(self):
        for review, expect in ((None, "needs its review record"), ("{bad", "could not be read"),
                               ({"reviewed": False, "personal_data": "none"}, '"reviewed": true'),
                               ({"reviewed": True, "personal_data": "present"}, '"reviewed": true'),
                               ({"reviewed": "yes", "personal_data": "none"}, '"reviewed": true')):
            r, out = self.attach(self.video(review))
            self.assertTrue(r["isError"], review)
            self.assertIn(expect, out)
            self.assertEqual(self.http.calls, [])

    def test_the_video_limit_is_60_mb_and_images_stay_at_10_mb(self):
        with mock.patch.object(g, "MAX_VIDEO", 2000), mock.patch.object(g, "MAX_FILE", 500):
            r, _ = self.attach(self.video({"reviewed": True, "personal_data": "none"}, size=1500))
            self.assertNotIn("isError", r)
            r, out = self.attach(self.video({"reviewed": True, "personal_data": "none"}, size=2001))
            self.assertIn("larger than the limit", out)
            img = os.path.join(self.tmp.name, "big.png")
            with open(img, "wb") as fh:
                fh.write(b"p" * 501)
            r, out = self.attach(img)
            self.assertIn("larger than the limit", out)
        self.assertEqual(g.MAX_VIDEO, 60 * 1024 * 1024)

    def test_other_extensions_stay_refused(self):
        path = os.path.join(self.tmp.name, "x.avi")
        with open(path, "wb") as fh:
            fh.write(b"x")
        _r, out = self.attach(path)
        self.assertIn("not allowed", out)


if __name__ == "__main__":
    unittest.main()


# --- the final preview of a bug's fields (13.11.2026): technique-ado-write-guard.md «Final preview of a bug's fields» ---
# A fictional project shaped like a real one (Bug form with its own rich-text fields for the actual and the expected result).

PROJECT_YAML = """
project: "Example"
tracker: "excel"
write_guard:
  bugs:
    labels: {repro_steps: "Repro Steps", system_info: "System Info", acceptance_criteria: "Acceptance Criteria"}
    fields:
      actual_result: {reference: "Custom.ActualResult", label: "Actual Result", required: true}
      expected_result: {reference: "Custom.ExpectedResult", label: "Expected Result", required: true}
    checks:
      repro_steps:
        - {name: "preconditions", pattern: "Preconditions:"}
        - {name: "numbered steps", pattern: "<ol>|^\\\\s*1\\\\. "}
        - {name: "observed frequency", pattern: "Frequency:|Runs:"}
      acceptance_criteria:
        - {name: "criterion id", pattern: "#\\\\d+"}
        - {name: "revision", pattern: "rev\\\\.? ?\\\\d+"}
        - {name: "link", pattern: "https://dev\\\\.azure\\\\.com/"}
      system_info:
        - {name: "environment", pattern: "Environment"}
        - {name: "device and OS", pattern: "iPhone|Android|iOS"}
        - {name: "app and observed build", pattern: "\\\\(\\\\d+\\\\)"}
        - {name: "QA account (alias or to confirm)", pattern: "QA account"}
    notices:
      system_info: [{name: "QA account to confirm", pattern: "to confirm"}]
    require_patch_hash: true
"""

CUSTOM = [{"referenceName": "Custom.ActualResult", "alwaysRequired": False, "defaultValue": None},
          {"referenceName": "Custom.ExpectedResult", "alwaysRequired": False, "defaultValue": None}]

LIKE_A_REAL_BUG = {
    "parent_id": 100,
    "title": "[Actions] The assistant repeats the accept-on-the-card notice when the lead says yes in writing again",
    "repro_steps": ("<div><b>Preconditions:</b> QA account in dev, signed in; new conversation; fictional names.</div>"
                    "<ol><li>Ask for a concrete action until the «Task» card appears.</li>"
                    "<li>Without touching the card, send «Yes, agreed.»</li><li>Without touching the card, send «Yes.»</li></ol>"
                    "<div><b>Frequency:</b> 2 of 3 runs in dev (iOS 1.0.0 (39)).</div>"),
    "system_info": ("Environment test (dev) · iOS · iPhone 13 Pro · iOS 26.7.1 · dev app 1.0.0 (39) · backend dev · "
                    "QA account iOS (alias and profile to confirm) · 2026-11-13 01:31–01:55 UTC"),
    "acceptance_criteria": ("<div>PBI #100, rev 24 (https://dev.azure.com/example-org/EXAMPLE/_workitems/edit/100), "
                            "criterion 2, copied from the Acceptance Criteria field:</div>"
                            "<div>2. When the lead confirms in writing, the action stays proposed and the notice is given once.</div>"),
    "extra_fields": {"actual_result": "<div>At step 3 the notice repeats: «I cannot mark the action for you in writing.»</div>",
                     "expected_result": "<div>The action stays proposed and the notice is given only once per proposal.</div>"},
}


class FinalPreview(Base):
    def setUp(self):
        self.cfgdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.cfgdir.cleanup)
        path = os.path.join(self.cfgdir.name, "project.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(PROJECT_YAML)
        self.config = g.load_project_bug_config(path)

    def project_guard(self, items=None, config=None, **kw):
        kw.setdefault("type_fields", type_fields(extra=CUSTOM))
        gd = self.guard(items, **kw)
        gd.bug_config = config if config is not None else self.config
        return gd

    def args(self, **over):
        a = json.loads(json.dumps(LIKE_A_REAL_BUG))
        for k, v in over.items():
            if v is None:
                a.pop(k, None)
            else:
                a[k] = v
        return a

    def preview_(self, gd, **over):
        return self.call(gd, "ado_guard_preview_bug", self.args(**over))

    def test_the_preview_shows_exactly_what_the_creation_sends_with_the_board_labels(self):
        gd = self.project_guard()
        r, prev = self.preview_(gd)
        self.assertNotIn("isError", r, prev)
        rows = prev["fields_preview"]
        self.assertEqual([x["label"] for x in rows],
                         ["Title", "Repro Steps", "System Info", "Area", "Iteration", "Acceptance Criteria",
                          "Actual Result", "Expected Result"])
        sent_by_patch = [(op["path"][len("/fields/"):], op["value"]) for op in prev["patch"] if op["path"].startswith("/fields/")]
        self.assertEqual([(x["reference"], x["value"]) for x in rows], sent_by_patch)
        for x in rows[1:3] + rows[5:]:
            self.assertIn(x["value"], prev["preview_text"])                      # the text shown is the value sent
        self.assertEqual(prev["parent"]["relation"], g.PARENT_REL)
        self.assertIn("Assigned To: empty", prev["preview_text"])
        self.assertTrue(prev["ready"], prev["missing"])
        # the creation sends that very patch, and its hash is the one shown
        args = self.args()
        r, out = self.call(gd, "ado_guard_create_bug", {**args, "payload_sha256": prev["payload_sha256"],
                                                       "patch_sha256": prev["patch_sha256"]})
        self.assertNotIn("isError", r, out)
        self.assertEqual((out["ok"], out["problems"]), (True, []))
        sent = json.loads(self.http.posts()[0]["body"])
        self.assertEqual(sent, prev["patch"])
        self.assertEqual(g.Guard.patch_hash(sent), prev["patch_sha256"])
        self.assertNotIn("System.AssignedTo", json.dumps(sent))
        stored = self.http.items[900]["fields"]
        self.assertEqual(stored["Custom.ActualResult"], args["extra_fields"]["actual_result"])
        self.assertEqual(stored["Custom.ExpectedResult"], args["extra_fields"]["expected_result"])

    def test_extra_fields_come_only_from_the_project_mapping(self):
        gd = self.project_guard()
        r, text = self.preview_(gd, extra_fields={"actual_result": "x", "expected_result": "y", "severity": "1"})
        self.assertTrue(r["isError"])
        self.assertIn("not mapped", text)
        r, text = self.preview_(gd, extra_fields={"actual_result": "x"})
        self.assertTrue(r["isError"])
        self.assertIn("Expected Result", text)                                 # required by the mapping
        self.assertEqual(self.http.calls, [])                                      # refused before any read
        gd = self.project_guard(type_fields=type_fields())                         # the form does not have the field
        r, text = self.preview_(gd)
        self.assertTrue(r["isError"])
        self.assertIn("Custom.ActualResult", text)
        for bad in ({"fields": {"x": {"reference": "System.AssignedTo"}}}, {"fields": {"x": "Microsoft.VSTS.Common.Priority"}},
                    {"fields": {"x": "Microsoft.VSTS.TCM.ReproSteps"}}, {"fields": {"repro_steps": "Custom.X"}},
                    {"fields": {"a": "Custom.X", "b": "Custom.X"}}, {"labels": {"actual_result": "x"}},
                    {"checks": {"nope": [{"name": "n", "pattern": "x"}]}}, {"checks": {"title": [{"name": "n", "pattern": "("}]}},
                    {"colour": "red"}):
            with self.subTest(bad=bad), self.assertRaises(g.ConfigError):
                g.load_bug_config(bad)

    def test_an_account_to_confirm_is_said_and_no_address_or_credential_ever_goes(self):
        gd = self.project_guard()
        _r, prev = self.preview_(gd)
        self.assertEqual(prev["notices"], ["System Info: QA account to confirm"])
        self.assertIn("Notices: System Info: QA account to confirm", prev["preview_text"])
        for field, value in (("system_info", LIKE_A_REAL_BUG["system_info"] + "\nqa.lead@example.com"),
                             ("repro_steps", LIKE_A_REAL_BUG["repro_steps"] + "<div>password: x1</div>"),
                             ("title", "[Actions] token=abc")):
            r, text = self.preview_(gd, **{field: value})
            self.assertTrue(r["isError"], field)
            self.assertRegex(text, "e-mail address|credential")
        r, text = self.preview_(gd, extra_fields={**LIKE_A_REAL_BUG["extra_fields"], "actual_result": "see a@b.example"})
        self.assertTrue(r["isError"])
        r, text = self.preview_(gd, creation_comment="Account used: qa@example.com")
        self.assertTrue(r["isError"])
        self.assertEqual(self.http.posts(), [])
        default = self.guard()                                                     # every project, not only one with a mapping
        r, _ = self.preview(default, system_info="Android 13 · user@example.org")
        self.assertTrue(r["isError"])

    def test_a_missing_check_keeps_the_preview_not_ready_and_refuses_the_creation(self):
        gd = self.project_guard()
        no_freq = LIKE_A_REAL_BUG["repro_steps"].replace("<b>Frequency:</b>", "")
        r, prev = self.preview_(gd, repro_steps=no_freq)
        self.assertNotIn("isError", r)
        self.assertFalse(prev["ready"])
        self.assertEqual(prev["missing"], ["Repro Steps: observed frequency"])
        args = self.args(repro_steps=no_freq)
        r, text = self.call(gd, "ado_guard_create_bug", {**args, "payload_sha256": prev["payload_sha256"],
                                                        "patch_sha256": prev["patch_sha256"]})
        self.assertTrue(r["isError"])
        self.assertIn("observed frequency", text)
        no_link = LIKE_A_REAL_BUG["acceptance_criteria"].replace("https://dev.azure.com/example-org/EXAMPLE/_workitems/edit/100", "")
        _r, prev = self.preview_(gd, acceptance_criteria=no_link)
        self.assertEqual(prev["missing"], ["Acceptance Criteria: link"])
        self.assertEqual(self.http.posts(), [])

    def test_a_change_after_the_approval_needs_a_new_preview(self):
        gd = self.project_guard()
        _r, prev = self.preview_(gd)
        approved = {"payload_sha256": prev["payload_sha256"], "patch_sha256": prev["patch_sha256"]}
        changed_fields = (
            {"extra_fields": {**LIKE_A_REAL_BUG["extra_fields"], "expected_result": "<div>another expected result</div>"}},
            {"system_info": LIKE_A_REAL_BUG["system_info"].replace("01:55", "01:56")},
            {"parent_id": 200},
        )
        for over in changed_fields:
            with self.subTest(over=list(over)):
                r, text = self.call(gd, "ado_guard_create_bug", {**self.args(**over), **approved})
                self.assertTrue(r["isError"])
                self.assertIn("preview again", text)
        # the same content mapped to another field is another approval
        gd2 = self.project_guard(config=g.load_bug_config({"fields": {
            "actual_result": {"reference": "Custom.Observed", "label": "Actual Result", "required": True},
            "expected_result": {"reference": "Custom.ExpectedResult", "label": "Expected Result", "required": True}}}),
            type_fields=type_fields(extra=CUSTOM + [{"referenceName": "Custom.Observed", "alwaysRequired": False,
                                                    "defaultValue": None}]))
        r, text = self.call(gd2, "ado_guard_create_bug", {**self.args(), **approved})
        self.assertTrue(r["isError"])
        self.assertIn("payload_sha256 does not match", text)
        # the parent moved to another iteration after the preview: same payload, another request -> refused
        gd = self.project_guard()
        _r, prev = self.preview_(gd)
        self.http.items[100]["fields"]["System.IterationPath"] = "EXAMPLE\\Sprint 13"
        r, text = self.call(gd, "ado_guard_create_bug", {**self.args(), "payload_sha256": prev["payload_sha256"],
                                                        "patch_sha256": prev["patch_sha256"]})
        self.assertTrue(r["isError"])
        self.assertIn("patch_sha256 does not match", text)
        # this project requires the patch hash
        r, text = self.call(gd, "ado_guard_create_bug", {**self.args(), "payload_sha256": prev["payload_sha256"]})
        self.assertTrue(r["isError"])
        self.assertIn("requires patch_sha256", text)
        self.assertEqual(self.http.posts(), [])
        # a new preview of the new state is approved and created
        _r, prev2 = self.preview_(gd)
        self.assertNotEqual(prev2["patch_sha256"], prev["patch_sha256"])
        self.assertEqual(prev2["payload_sha256"], prev["payload_sha256"])
        r, out = self.call(gd, "ado_guard_create_bug", {**self.args(), "payload_sha256": prev2["payload_sha256"],
                                                       "patch_sha256": prev2["patch_sha256"]})
        self.assertNotIn("isError", r, out)

    def test_the_parent_moving_to_another_area_after_the_approval_is_refused(self):
        gd = self.project_guard()
        _r, prev = self.preview_(gd)
        self.http.items[100]["fields"]["System.AreaPath"] = "EXAMPLE\\Other team"
        r, text = self.call(gd, "ado_guard_create_bug", {**self.args(), "payload_sha256": prev["payload_sha256"],
                                                        "patch_sha256": prev["patch_sha256"]})
        self.assertTrue(r["isError"])
        self.assertIn("patch_sha256 does not match", text)
        self.assertEqual(self.http.posts(), [])

    def test_an_optional_field_left_empty_is_not_held_by_its_checks(self):
        gd = self.project_guard()
        r, prev = self.preview_(gd, acceptance_criteria="")         # a parent without criteria: nothing to copy
        self.assertNotIn("isError", r, prev)
        self.assertTrue(prev["ready"], prev["missing"])
        self.assertEqual({c["result"] for c in prev["checks"] if c["field"] == "Acceptance Criteria"},
                         {"not applicable (field left empty)"})
        self.assertNotIn("Acceptance Criteria", [x["label"] for x in prev["fields_preview"]])
        r, prev = self.preview_(gd, acceptance_criteria="<div>criterion without its source</div>")
        self.assertFalse(prev["ready"])                              # filled in: the checks hold
        self.assertIn("Acceptance Criteria: link", prev["missing"])

    def test_attachments_and_the_comment_are_listed_as_separate_operations(self):
        gd = self.project_guard()
        names = {}
        for name, data in (("s4_t1_answer.png", b"png"), ("s4c_sim.mp4", b"mp4"), ("raw.mp4", b"mp4")):
            names[name] = os.path.join(self.tmp.name, name)
            with open(names[name], "wb") as fh:
                fh.write(data)
        with open(names["s4c_sim.mp4"] + ".review.json", "w", encoding="utf-8") as fh:
            json.dump({"reviewed": True, "personal_data": "masked"}, fh)
        comment = "Note for the developer: frequency 2 of 3 in dev."
        r, prev = self.preview_(gd, attachments=list(names.values()), creation_comment=comment)
        self.assertNotIn("isError", r, prev)
        assoc = prev["associated_operations"]
        rows = {a["file"]: a for a in assoc["attachments"]}
        self.assertTrue(rows["s4_t1_answer.png"]["accepted"])
        self.assertEqual(rows["s4c_sim.mp4"]["video_review"], {"reviewed": True, "personal_data": "masked"})
        self.assertFalse(rows["raw.mp4"]["accepted"])                              # a video nobody reviewed
        self.assertIn("review record", rows["raw.mp4"]["reason"])
        self.assertEqual(assoc["creation_comment"]["text_sha256"], g.sha256_text(comment))
        self.assertIn("own approval", assoc["note"])
        _r, bare = self.preview_(gd)
        self.assertEqual((bare["payload_sha256"], bare["patch_sha256"]),
                         (prev["payload_sha256"], prev["patch_sha256"]))           # not part of the bug's hashes
        self.assertFalse([c for c in self.http.calls if "/attachments" in c["url"] or "/comments" in c["url"]])

    def test_a_project_with_the_standard_fields_keeps_its_hash_and_its_behaviour(self):
        gd = self.guard()
        r, prev = self.preview(gd)
        self.assertNotIn("isError", r, prev)
        legacy = g.sha256_text(json.dumps({k: BUG[k] for k in ("parent_id", "title", "repro_steps", "system_info",
                                                                 "acceptance_criteria")},
                                          ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        self.assertEqual(prev["payload_sha256"], legacy)                           # approvals made before stay valid
        self.assertEqual([x["label"] for x in prev["fields_preview"]],
                         ["Title", "Repro Steps", "System Info", "Area", "Iteration", "Acceptance Criteria"])
        self.assertEqual((prev["ready"], prev["checks"], prev["notices"]), (True, [], []))
        self.assertFalse(prev["require_patch_hash"])
        r, out = self.create(gd)                                                   # without patch_sha256, as before
        self.assertNotIn("isError", r, out)
        self.assertEqual({op["path"] for op in json.loads(self.http.posts()[0]["body"])}, ALLOWED_PATHS)
        r, text = self.preview(self.guard(), extra_fields={"actual_result": "x"})
        self.assertTrue(r["isError"])
        self.assertIn("not mapped", text)
        self.assertEqual(self.guard().status()["bug_fields"]["extra"], {})

    def test_the_launcher_option_reads_the_workspace_and_a_bad_section_stops_the_start(self):
        path = os.path.join(self.cfgdir.name, "project.yaml")
        self.assertEqual(self.config["fields"]["actual_result"],
                         {"reference": "Custom.ActualResult", "label": "Actual Result", "required": True})
        self.assertTrue(self.config["require_patch_hash"])
        bad = os.path.join(self.cfgdir.name, "bad.yaml")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("write_guard:\n  bugs:\n    fields:\n      actual_result: System.AssignedTo\n")
        argv = ["--org", "example-org", "--project", "EXAMPLE", "--identity-id", ME, "--allowed-dir", self.cfgdir.name]
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            g.main(argv + ["--project-yaml", bad])
        with open(os.path.join(self.cfgdir.name, "none.yaml"), "w", encoding="utf-8") as fh:
            fh.write('project: "Example"\n')
        self.assertEqual(g.load_project_bug_config(os.path.join(self.cfgdir.name, "none.yaml"))["fields"], {})
        gd = self.project_guard()
        self.assertEqual(gd.status()["bug_fields"]["extra"],
                         {"actual_result": "Actual Result (Custom.ActualResult)",
                          "expected_result": "Expected Result (Custom.ExpectedResult)"})
        self.assertTrue(os.path.isfile(path))
