"""/analyze-ticket on Linear (docs/technique-tickets.md section 4.4): a fictional fake of the GraphQL API, no network,
no real workspace, no real key.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest
from typing import ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ac_coverage
import pbi_state
import validate_project
from linear_fake import API_KEY, WORKSPACE, FakeLinear, _json, gql_error
from test_tickets import BOARDS, ISSUES, T1, T2, T3, Base
from tickets_fake import FakeAdo, FakeJira

from adapters.tickets import linear, render
from adapters.tickets.collect import resolve_ref
from adapters.tickets.config import load, validate_section
from adapters.tickets.model import WriteRefused, read_only_transport
from adapters.tracker.errors import Conflict, InvalidInput
from adapters.tracker.http import Request, Response

KEY_VAR = "DS_LINEAR_API_KEY"   # the name of the .env variable that holds the key, not a key
LINEAR = {"service": "linear", "access": "api", "workspace_url": WORKSPACE, "team_keys": ["DS"],
          "api_key_var": KEY_VAR,
          "acceptance_criteria": {"headings": ["Acceptance criteria", "Critérios de aceitação"]}}

DESCRIPTION = ("Apply a coupon at checkout.  \n"
               "\n"
               "## Acceptance criteria\n"
               "\n"
               "1. Given a valid coupon, when I apply it, then the total drops by 10%.\n"
               "2. An expired coupon shows «Cupão expirado» and the total stays the same.\n"
               "   - except for staff accounts\n"
               "\n"
               "## Notes\n"
               "Design: https://www.figma.com/file/demo/coupon")
CRITERIA = ("1. Given a valid coupon, when I apply it, then the total drops by 10%.\n"
            "2. An expired coupon shows «Cupão expirado» and the total stays the same.\n"
            "   - except for staff accounts")


class LinearBase(Base):
    tickets: ClassVar[dict] = {"sources": {"lin": LINEAR}}

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.ws, ".env"), "a", encoding="utf-8") as f:
            f.write(f"DS_LINEAR_API_KEY={API_KEY}\n")

    def fake(self, page_size=2):
        fake = FakeLinear(page_size)
        fake.add("DS-12", description=DESCRIPTION, assignee={"displayName": "Rita Dev"},
                 labels={"nodes": [{"name": "Bug"}, {"name": "Checkout"}],
                         "pageInfo": {"hasNextPage": False, "endCursor": None}})
        for i in range(5):
            fake.comment("DS-12", f"c{i}", f"Comment {i}:\n\n- keeps *markdown* and «text» as written  ",
                         at=f"2026-10-0{i + 2}T10:00:00.000Z")
        fake.conn["DS-12"]["history"].append(
            {"id": "h1", "createdAt": "2026-10-03T08:00:00.000Z", "actor": {"displayName": "Pat Lead"},
             "fromState": {"name": "Backlog"}, "toState": {"name": "Todo"}, "fromPriority": 0, "toPriority": 3})
        return fake

    def collect_lin(self, fake, *refs, now=T1, expand=False):
        return self.run_cli(fake, "collect", *refs, *([] if expand else ["--no-expand"]), "--now", now)

    def all_text(self):
        return "\n".join(self.lines) + "".join(v.decode("utf-8", "replace") for v in self.snapshot_of_files().values())


class Configuration(LinearBase):
    def test_a_minimal_source_is_valid_offline(self):
        problems, warnings = validate_section({"tickets": {"sources": {"lin": LINEAR}}})
        self.assertEqual((problems, warnings), ([], []))

    def test_problems_are_named_without_contacting_anything(self):
        bad = {"service": "linear", "workspace_url": "https://linear.app/demo-shop/issue/DS-1", "team_keys": "DS",
               "project_key": "DS", "acceptance_criteria": ["x"]}
        problems, _ = validate_section({"tickets": {"sources": {"lin": bad}, "scopes": {
            "s": {"source": "lin", "current_iteration": True}, "q": {"source": "lin", "query": "team = DS"}}}})
        text = "\n".join(problems)
        for where in ("workspace_url", "api_key_var", "team_keys", "project_key", "acceptance_criteria",
                      "current_iteration", "scopes.q.query"):
            self.assertIn(where, text)
        problems, _ = validate_section({"tickets": {"sources": {"lin": {**LINEAR,
                                                                        "workspace_url": "https://evil.example/x"}}}})
        self.assertTrue(any("linear.app" in p for p in problems))

    def test_without_team_keys_a_warning_explains_the_ambiguity(self):
        source = {k: v for k, v in LINEAR.items() if k != "team_keys"}
        problems, warnings = validate_section({"tickets": {"sources": {"lin": source}}})
        self.assertEqual(problems, [])
        self.assertTrue(any("team_keys" in w for w in warnings))

    def test_browser_access_needs_no_key(self):
        source = {k: v for k, v in LINEAR.items() if k != "api_key_var"}
        self.assertEqual(validate_section({"tickets": {"sources": {"lin": {**source, "access": "browser"}}}})[0], [])

    def test_sources_offline_says_it_is_configuration_only_and_sends_nothing(self):
        fake = FakeLinear()
        self.assertEqual(self.run_cli(fake, "sources"), 0)
        self.assertEqual(fake.requests, [])
        out = "\n".join(self.lines)
        self.assertIn("lin: linear, access api, demo-shop; credentials: DS_LINEAR_API_KEY: set", out)
        self.assertIn("access not confirmed", out)
        self.assertNotIn(API_KEY, out)
        line = next(x for x in validate_project.ticket_sources(self.ws) if x[1] == "ticket source lin")
        self.assertTrue(line[0])
        self.assertIn("access not confirmed", line[2])
        self.assertNotIn(API_KEY, line[2])

    def test_a_missing_key_is_named_never_shown(self):
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write("OTHER=1\n")
        line = next(x for x in validate_project.ticket_sources(self.ws) if x[1] == "ticket source lin")
        self.assertFalse(line[0])
        self.assertIn("DS_LINEAR_API_KEY", line[2])

    def test_sources_online_confirms_the_workspace_and_the_teams(self):
        fake = FakeLinear()
        self.assertEqual(self.run_cli(fake, "sources", "--online"), 0)
        out = "\n".join(self.lines)
        self.assertIn("access confirmed for Ana Tester", out)
        self.assertIn("teams reached: DS", out)
        self.assertNotIn("PROBLEM", out)
        self.assertNotIn(API_KEY, out)

    def test_sources_online_says_when_the_key_reaches_another_workspace_or_misses_a_team(self):
        fake = FakeLinear()
        fake.org, fake.teams = "other-co", ["OPS"]
        self.run_cli(fake, "sources", "--online")
        out = "\n".join(self.lines)
        self.assertIn("PROBLEM: the key belongs to the workspace other-co", out)
        self.assertIn("does not reach the team(s) DS", out)

    def test_sources_online_denied_is_exit_3_without_the_key(self):
        fake = FakeLinear()
        fake.faults[("Viewer", "*")] = gql_error("AUTHENTICATION_ERROR", f"Bad key {API_KEY}", 400)
        self.assertEqual(self.run_cli(fake, "sources", "--online"), 3)
        self.assertNotIn(API_KEY, "\n".join(self.lines))


class References(LinearBase):
    def config(self, sources):
        self.write_project({"sources": sources})
        return load(self.ws)

    def test_an_id_and_a_link_resolve_to_the_same_issue(self):
        config = self.config({"lin": LINEAR})
        self.assertEqual(resolve_ref(config, "ds-12"), ("lin", "DS-12"))
        self.assertEqual(resolve_ref(config, f"{WORKSPACE}/issue/DS-12/apply-coupon"), ("lin", "DS-12"))
        self.assertEqual(resolve_ref(config, f"{WORKSPACE}/issue/ds-12"), ("lin", "DS-12"))
        self.assertEqual(resolve_ref(config, "lin:DS-12"), ("lin", "DS-12"))

    def test_a_link_of_another_workspace_is_refused_before_any_request(self):
        config = self.config({"lin": LINEAR})
        with self.assertRaises(InvalidInput):
            resolve_ref(config, "https://linear.app/other-co/issue/DS-12/x")
        fake = self.fake()
        self.assertEqual(self.collect_lin(fake, "https://linear.app/other-co/issue/DS-12/x"), 4)
        self.assertEqual(fake.requests, [])

    def test_a_key_of_both_jira_and_linear_is_asked_never_chosen(self):
        config = self.config({"jira": ISSUES, "lin": LINEAR})
        with self.assertRaises(Conflict) as e:
            resolve_ref(config, "DS-12")
        self.assertIn("<source>:DS-12", str(e.exception))
        self.assertEqual(resolve_ref(config, "lin:DS-12"), ("lin", "DS-12"))
        self.assertEqual(resolve_ref(config, "jira:DS-12"), ("jira", "DS-12"))
        fake = self.fake()
        self.assertEqual(self.collect_lin(fake, "DS-12"), 2)
        self.assertEqual(fake.requests, [])

    def test_a_linear_source_without_team_keys_keeps_a_jira_key_ambiguous(self):
        open_linear = {k: v for k, v in LINEAR.items() if k != "team_keys"}
        config = self.config({"jira": ISSUES, "lin": open_linear})
        with self.assertRaises(Conflict):
            resolve_ref(config, "DS-1")

    def test_different_prefixes_choose_without_asking(self):
        config = self.config({"jira": ISSUES, "lin": {**LINEAR, "team_keys": ["OPS"]}})
        self.assertEqual(resolve_ref(config, "DS-1"), ("jira", "DS-1"))
        self.assertEqual(resolve_ref(config, "OPS-7"), ("lin", "OPS-7"))

    def test_jira_only_workspaces_keep_their_rule(self):
        config = self.config({"a": ISSUES, "b": {**ISSUES, "project_key": "OPS"}})
        self.assertEqual(resolve_ref(config, "DS-1"), ("a", "DS-1"))
        with self.assertRaises(Conflict):
            resolve_ref(config, "XY-1")      # no exact source: every Jira source, as before


class Collect(LinearBase):
    def test_first_read_keeps_every_text_word_for_word_with_its_origin(self):
        fake = self.fake()
        self.assertEqual(self.collect_lin(fake, "DS-12"), 0)
        check = self.last_check()
        self.assertEqual(check["conclusion"], "first")
        t = self.ticket(check, "DS-12")
        self.assertEqual(t["ref"], {"service": "linear", "org": "demo-shop", "project": "DS", "id": "DS-12"})
        self.assertTrue(t["url"].startswith(f"{WORKSPACE}/issue/DS-12/"))
        doc = self.memory_doc(check, "DS-12")
        fields = doc["components"]["fields"]["data"]
        self.assertEqual(fields["description"], DESCRIPTION)
        self.assertEqual(fields["acceptance_criteria"], CRITERIA)
        self.assertEqual(fields["origin"]["acceptance_criteria"], "description, section 'Acceptance criteria', lines 5-7")
        lines = DESCRIPTION.split("\n")
        self.assertEqual("\n".join(lines[4:7]), CRITERIA)
        self.assertEqual((fields["state"], fields["state_category"], fields["priority"], fields["assignee"],
                          fields["sprint"], fields["area"]), ("Todo", "open", "Medium", "Rita Dev", "Cycle 5", "Checkout"))
        self.assertEqual(fields["custom"]["labels"], "Bug, Checkout")
        self.assertIn("type", fields["unavailable"])
        self.assertIn("reason", fields["unavailable"])
        comments = doc["components"]["comments"]["data"]
        self.assertEqual([c["id"] for c in comments], [f"c{i}" for i in range(5)])      # three pages of two
        self.assertEqual(comments[0]["text"], "Comment 0:\n\n- keeps *markdown* and «text» as written  ")
        self.assertEqual(t["coverage"]["comments"]["status"], "complete")
        self.assertEqual(t["coverage"]["development"]["status"], "unsupported")
        self.assertIn("not its text", t["coverage"]["history"]["detail"])
        history = doc["components"]["history"]["data"]
        self.assertEqual(history[0]["changes"], [{"field": "state", "from": "Backlog", "to": "Todo"},
                                                 {"field": "priority", "from": "No priority", "to": "Medium"}])
        self.assertIn("figma", {link["kind"] for link in doc["components"]["links"]["data"]})

    def test_two_identical_reads_report_no_changes(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 0)
        check = self.last_check()
        self.assertEqual(check["conclusion"], "no-changes")
        self.assertEqual(self.ticket(check, "DS-12")["changes"], [])

    def test_empty_unavailable_and_partial_fields_are_apart(self):
        fake = self.fake()
        fake.add("DS-13", description="No criteria here")
        fake.add("DS-14", description=None,
                 labels={"nodes": [{"name": "Bug"}], "pageInfo": {"hasNextPage": True, "endCursor": "x"}})
        self.collect_lin(fake, "DS-13", "DS-14")
        check = self.last_check()
        f13 = self.memory_doc(check, "DS-13")["components"]["fields"]["data"]
        self.assertEqual(f13["acceptance_criteria"], "")
        self.assertNotIn("acceptance_criteria", f13["unavailable"])                       # empty: read, nothing there
        self.assertIn("no section under", f13["origin"]["acceptance_criteria"])
        f14 = self.memory_doc(check, "DS-14")["components"]["fields"]["data"]
        self.assertEqual(f14["description"], "")
        self.assertIn("custom.labels", f14["partial"])                                    # partial: only a part read
        self.write_project({"sources": {"lin": {k: v for k, v in LINEAR.items() if k != "acceptance_criteria"}}})
        self.collect_lin(fake, "DS-13", now=T2)
        check = self.last_check()
        f13 = self.memory_doc(check, "DS-13")["components"]["fields"]["data"]
        self.assertIn("acceptance_criteria", f13["unavailable"])                          # unavailable: no source
        self.assertEqual(self.ticket(check, "DS-13")["changes"], [])                      # and not a false change

    def test_real_changes_are_reported_with_author_and_honest_history(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        issue = fake.issues["DS-12"]
        issue["state"] = {"name": "Done", "type": "completed"}
        issue["description"] = DESCRIPTION.replace("10%", "15%")
        issue["labels"]["nodes"].append({"name": "Regression"})
        fake.conn["DS-12"]["history"] += [
            {"id": "h2", "createdAt": "2026-11-01T15:00:00.000Z", "actor": {"displayName": "Pat Lead"},
             "fromState": {"name": "Todo"}, "toState": {"name": "Done"}},
            {"id": "h3", "createdAt": "2026-11-01T14:00:00.000Z", "actor": {"displayName": "Rita Dev"},
             "updatedDescription": True, "addedLabels": [{"name": "Regression"}]}]
        fake.conn["DS-12"]["comments"][1].update(body="Comment 1 edited", editedAt="2026-11-01T12:00:00.000Z")
        fake.conn["DS-12"]["comments"][2]["resolvedAt"] = "2026-11-01T12:30:00.000Z"     # not an edit
        fake.comment("DS-12", "c9", "New decision: 15%", by="Pat Lead", at="2026-11-01T13:00:00.000Z")
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 0)
        changes = self.changes(self.last_check(), "DS-12")
        self.assertEqual(changes[("fields", "changed", "state")]["author"], "Pat Lead")
        self.assertIn("15%", changes[("fields", "changed", "acceptance_criteria")]["after"])
        self.assertIn(("fields", "changed", "description"), changes)
        self.assertIn(("fields", "changed", "custom.labels"), changes)
        self.assertEqual(changes[("comments", "edited", "c1")]["after"], "Comment 1 edited")
        self.assertNotIn(("comments", "edited", "c2"), changes)
        self.assertIn(("comments", "added", "c9"), changes)
        self.assertNotIn(("history", "transient", "custom.labels"), changes)
        doc = self.memory_doc(self.last_check(), "DS-12")
        notes = [c.get("note") for h in doc["components"]["history"]["data"] for c in h["changes"]]
        self.assertIn("edited; Linear's history keeps no text of the description", notes)

    def test_relations_parent_children_and_expansion(self):
        fake = self.fake()
        fake.add("DS-1", title="Epic coupons")
        fake.add("DS-20")
        fake.add("DS-21")
        fake.add("DS-22")
        fake.add("DS-30")
        fake.child("DS-1", "DS-12")
        fake.child("DS-12", "DS-30")
        fake.relate("DS-20", "blocks", "DS-12")
        fake.relate("DS-12", "duplicate", "DS-21")
        fake.relate("DS-12", "similar", "DS-22")
        self.assertEqual(self.collect_lin(fake, "DS-12", expand=True), 0)
        check = self.last_check()
        rels = {(r["kind"], r["target"], r["label"]) for r in
                self.memory_doc(check, "DS-12")["components"]["relations"]["data"]}
        self.assertEqual(rels, {("parent", "DS-1", "parent"), ("child", "DS-30", "sub-issue"),
                                ("blocked-by", "DS-20", "blocked by"), ("duplicate", "DS-21", "duplicate of"),
                                ("related", "DS-22", "similar")})
        self.assertEqual({t["ref"]["id"]: t["role"] for t in check["tickets"]},
                         {"DS-12": "root", "DS-1": "parent", "DS-30": "child", "DS-20": "related",
                          "DS-21": "related", "DS-22": "related"})
        self.assertEqual(self.ticket(check, "DS-12")["suggested_situation"]["blockers"], ["DS-20"])


class FailuresAndPartialReads(LinearBase):
    def test_a_failure_in_the_middle_keeps_what_was_read_and_removes_nothing(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        fake.faults[("IssueComments", "4")] = "network"         # the third page of comments never answers
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 1)
        check = self.last_check()
        t = self.ticket(check, "DS-12")
        self.assertEqual(t["coverage"]["comments"]["status"], "partial")
        self.assertEqual(t["changes"], [])                       # c4 was not seen: not a removal
        self.assertNotEqual(check["conclusion"], "no-changes")
        doc = self.memory_doc(check, "DS-12")
        self.assertEqual(len(doc["components"]["comments"]["data"]), 5)     # the complete reference is kept
        fake.faults.clear()
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T3), 0)
        self.assertEqual(self.last_check()["conclusion"], "no-changes")

    def test_a_graphql_error_in_a_page_is_a_failure_never_an_empty_list(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        fake.faults[("IssueHistory", "None")] = gql_error("INTERNAL_SERVER_ERROR", "boom")
        fake.faults[("IssueRelations", "None")] = gql_error("FORBIDDEN", "You do not have access", 400)
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 1)
        t = self.ticket(self.last_check(), "DS-12")
        self.assertEqual(t["coverage"]["history"]["status"], "failed")
        self.assertEqual(t["coverage"]["relations"]["status"], "denied")
        self.assertEqual(t["changes"], [])

    def test_an_issue_answered_with_data_and_errors_is_partial_and_compares_no_field(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        issue = dict(fake.issues["DS-12"], assignee=None, title="Changed title")
        fake.faults[("Issue", "*")] = _json({"data": {"issue": issue},
                                             "errors": [{"message": "assignee: not authorized", "path": ["issue",
                                                                                                        "assignee"]}]})
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 1)
        t = self.ticket(self.last_check(), "DS-12")
        self.assertEqual(t["coverage"]["fields"]["status"], "partial")
        self.assertFalse([c for c in t["changes"] if c["component"] == "fields"])

    def test_denied_is_recorded_as_denied_and_the_key_never_shows(self):
        fake = self.fake()
        fake.faults[("Issue", "*")] = gql_error("AUTHENTICATION_ERROR", f"invalid key {API_KEY}", 400)
        self.assertEqual(self.collect_lin(fake, "DS-12"), 3)
        t = self.ticket(self.last_check(), "DS-12")
        self.assertEqual(t["coverage"]["fields"]["status"], "denied")
        self.assertNotIn(API_KEY, self.all_text())

    def test_a_missing_issue_is_not_found_or_not_visible_never_deleted(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        del fake.issues["DS-12"]
        self.assertEqual(self.collect_lin(fake, "DS-12", now=T2), 3)
        t = self.ticket(self.last_check(), "DS-12")
        self.assertEqual(t["coverage"]["fields"]["status"], "not_found")
        self.assertIn("not visible", t["coverage"]["fields"]["detail"])
        doc = self.memory_doc(self.last_check(), "DS-12")
        self.assertEqual(doc["components"]["fields"]["data"]["description"], DESCRIPTION)

    def test_rate_limit_and_temporary_failure_are_retried(self):
        fake = self.fake()
        limited = Response(429, {"Retry-After": "1"}, json.dumps({"errors": [{"message": "Rate limit",
                                                                              "extensions": {"code": "RATELIMITED"}}]}).encode())
        fake.faults[("Issue", "*")] = [limited, Response(503, {}, b"unavailable")]
        fake.faults[("IssueComments", "2")] = ["network"]
        self.assertEqual(self.collect_lin(fake, "DS-12"), 0)
        self.assertEqual(self.ticket(self.last_check(), "DS-12")["coverage"]["comments"]["status"], "complete")

    def test_an_answer_that_is_not_json_is_a_failure(self):
        fake = self.fake()
        fake.faults[("Issue", "*")] = Response(200, {}, b"<html>sign in</html>")
        self.assertEqual(self.collect_lin(fake, "DS-12"), 3)
        self.assertEqual(self.ticket(self.last_check(), "DS-12")["coverage"]["fields"]["status"], "failed")


class Safety(LinearBase):
    def test_every_request_is_an_allowed_query_and_no_mutation_exists(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12", expand=True)
        self.run_cli(fake, "sources", "--online")
        self.assertTrue(fake.requests)
        names = set()
        for req in fake.requests:
            body = req.json()
            names.add(body["operationName"])
            self.assertEqual(body["query"], linear.ALLOWED[body["operationName"]])
            self.assertTrue(req.read_only)
            self.assertNotIn("Bearer", req.headers["Authorization"])
        self.assertLessEqual(names, set(linear.ALLOWED))
        for name, doc in linear.ALLOWED.items():
            self.assertTrue(doc.startswith(f"query {name}"))
            self.assertNotRegex(doc.lower(), r"\bmutation\b|\bsubscription\b")

    def test_a_write_document_is_refused_before_the_transport(self):
        with self.assertRaises(InvalidInput):
            linear._check_document("Issue", "mutation Issue { issueDelete(id: \"x\") { success } }")
        guarded = read_only_transport(lambda req: Response(200, {}, b"{}"))
        with self.assertRaises(WriteRefused):
            guarded(Request("POST", "https://api.linear.app/graphql", body=b"{}"))
        fake = self.fake()
        config = load(self.ws)
        from adapters.tickets.cli import api_source
        from adapters.tracker.http import SecretRedactor
        reader = api_source(config, config.sources["lin"], SecretRedactor(), fake)
        with self.assertRaises(InvalidInput):
            reader.api.run("IssueDelete", {"id": "DS-12"})
        self.assertEqual(fake.requests, [])

    def test_no_secret_in_memory_check_or_output(self):
        fake = self.fake()
        fake.comment("DS-12", "c8", f"temporary token: {API_KEY} and password=hunter2hunter2")
        self.collect_lin(fake, "DS-12")
        text = self.all_text()
        self.assertNotIn(API_KEY, text)
        self.assertNotIn("hunter2hunter2", text)

    def test_a_scope_is_an_issue_filter_read_with_the_same_guard(self):
        self.write_project({"sources": {"lin": LINEAR}, "scopes": {
            "project": {"source": "lin", "query": '{"project": {"slugId": {"eq": "demo-coupons"}}}'}}})
        fake = self.fake()
        fake.add("DS-13")
        fake.search_ids = ["DS-12", "DS-13"]
        self.assertEqual(self.run_cli(fake, "collect", "--scope", "project", "--now", T1), 0)
        self.assertEqual({t["ref"]["id"] for t in self.last_check()["tickets"]}, {"DS-12", "DS-13"})
        issues = [r.json() for r in fake.requests if r.json()["operationName"] == "Issues"]
        self.assertEqual(issues[0]["variables"]["filter"], {"project": {"slugId": {"eq": "demo-coupons"}}})


class BrowserOrMcpRoute(LinearBase):
    def bundle(self, fake, ident, **drop):
        conn = fake.conn[ident]
        doc = {"id": ident, "issue": fake.issues[ident]}
        for name, field in (("comments", "comments"), ("history", "history"), ("relations", "relations"),
                            ("inverse_relations", "inverseRelations"), ("children", "children"),
                            ("attachments", "attachments")):
            if name not in drop:
                doc[name] = {"items": conn[field], "complete": True}
        return doc

    def ingest(self, doc):
        path = os.path.join(self.ws, "bundle.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"source": "lin", "roots": [doc["id"]], "tickets": [doc]}, f)
        return self.run_cli(None, "ingest", path, "--now", T2)

    def test_same_comparison_as_the_api(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        self.assertEqual(self.ingest(self.bundle(fake, "DS-12")), 0)
        self.assertEqual(self.last_check()["conclusion"], "no-changes")

    def test_a_part_left_out_is_not_read_never_empty(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        self.assertEqual(self.ingest(self.bundle(fake, "DS-12", comments=True)), 1)
        t = self.ticket(self.last_check(), "DS-12")
        self.assertEqual(t["coverage"]["comments"]["status"], "not_read")
        self.assertEqual(t["changes"], [])


class SkillPath(LinearBase):
    """/analyze-ticket -> /plan-tests -> /test-pbi on a fictional issue."""

    def test_analysis_of_a_linear_check_is_recorded(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        check = self.last_check()
        doc = {"summary": "Coupon at checkout", "tickets": {"DS-12": {
            "qa_situation": {"value": "awaiting-implementation-or-build", "basis": ["state Todo"]},
            "facts": [{"text": CRITERIA.split("\n")[0], "source": "field:acceptance_criteria"}],
            "statements": [{"text": "Comment 0", "author": "Dev One", "source": "comment:c0"}],
            "inferences": [], "questions": [{"text": "Which build?", "blocking": True}],
            "actions": [], "conflicts": [], "comments": {"c0": "clarification"}}}}
        path = os.path.join(self.ws, "analysis_input.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        self.assertEqual(self.run_cli(fake, "analysis", check["id"], path), 0, self.lines)
        md = render.ticket_markdown(self.memory_doc(check, "DS-12"))
        self.assertIn("Source: https://linear.app/demo-shop/issue/DS-12/", md)
        self.assertIn("section 'Acceptance criteria', lines 5-7", md)
        self.assertIn("- type: unavailable (", md)

    def test_plan_tests_cites_the_issue_as_the_source_of_its_criteria(self):
        fake = self.fake()
        self.collect_lin(fake, "DS-12")
        amap = ac_coverage.init(self.ws, "DS-12")
        self.assertEqual(amap["criteria_source"]["kind"], "ticket-memory")
        self.assertEqual(amap["criteria_source"]["ticket"], "DS-12")
        self.assertTrue(amap["criteria_source"]["url"].startswith(f"{WORKSPACE}/issue/DS-12/"))
        self.assertEqual(amap["criteria_source"]["origin"], "description, section 'Acceptance criteria', lines 5-7")
        self.assertEqual([c["text"] for c in amap["criteria"]], [CRITERIA])          # word for word, line breaks kept
        self.assertEqual(amap["criteria_source"]["text"], CRITERIA)
        self.assertEqual(amap["criteria"][0]["presentation"], "reference")
        self.assertEqual(amap["criteria"][0]["origin"]["where"], "description, lines 5-7")
        self.assertTrue(amap["criteria"][0]["reference"].startswith("DS-12 · AC1 · description, lines 5-7 (https://"))
        self.assertEqual(ac_coverage.init(self.ws, "ds-12")["criteria_source"]["ticket"], "DS-12")

    def test_a_multi_line_criterion_is_transcribed_whole_with_lists_tables_and_its_lines(self):
        criteria = ("Given a logged-in customer with a cart\n"
                    "When they apply the coupon «SUMMER-10»\n"
                    "Then:\n"
                    "  - the total drops by 10%\n"
                    "    * rounded to 2 decimals  \n"
                    "  - the coupon line shows the code\n"
                    "\n"
                    "| Coupon | State | Message |\n"
                    "|---|---|---|\n"
                    "| SUMMER-10 | expired | «Cupão expirado» |\n"
                    "| WINTER | unknown |  |\n"
                    "\n"
                    "```\nPOST /cart/coupon {\"code\": \"X\"}\n\n-> 422\n```\n"
                    "\n"
                    "Short rule: one coupon per order.")
        description = "Intro line\n\n## Critérios de aceitação\n\n" + criteria + "\n\n## Notes\nnone"
        fake = FakeLinear()
        fake.add("DS-40", description=description)
        self.collect_lin(fake, "DS-40")
        amap = ac_coverage.init(self.ws, "DS-40")
        texts = [c["text"] for c in amap["criteria"]]
        self.assertEqual(texts[0], criteria.split("\n\n")[0])                         # list and nested list as written
        self.assertIn("    * rounded to 2 decimals  ", texts[0].split("\n"))            # indentation and trailing spaces
        self.assertEqual(texts[1], "\n".join(criteria.split("\n")[7:11]))              # the table whole, one criterion
        self.assertEqual(texts[2], "```\nPOST /cart/coupon {\"code\": \"X\"}\n\n-> 422\n```")   # a blank line inside a fence
        self.assertEqual(texts[3], "Short rule: one coupon per order.")
        self.assertEqual("\n\n".join(texts), criteria)                                  # nothing lost, nothing added
        self.assertEqual([c["presentation"] for c in amap["criteria"]], ["reference", "reference", "reference", "inline"])
        lines = description.split("\n")
        for c in amap["criteria"]:                                                     # each reference points at its lines
            a, b = (int(x) for x in c["origin"]["where"].rsplit(" ", 1)[1].split("-"))
            self.assertEqual("\n".join(lines[a - 1:b]), c["text"])
        from unittest import mock
        with mock.patch.object(ac_coverage, "_library", return_value=({}, "Not run", ["Pass", "Not run"])):
            rendered = ac_coverage.render(ac_coverage.report(self.ws, amap, run_logs=False))
        self.assertIn("see: DS-40 · AC2 · description, lines 12-15", rendered)
        for c in amap["criteria"][:3]:                                                  # shown by reference: each line
            for line in c["text"].split("\n"):                                           # in the transcription, as written
                self.assertIn("        " + line + "\n", rendered)
        self.assertIn("      Short rule: one coupon per order.", rendered)

    def test_plan_tests_says_when_the_criteria_are_unavailable(self):
        self.write_project({"sources": {"lin": {k: v for k, v in LINEAR.items() if k != "acceptance_criteria"}}})
        self.collect_lin(self.fake(), "DS-12")
        amap = ac_coverage.init(self.ws, "DS-12")
        self.assertEqual(amap["criteria_source"]["kind"], "none")
        self.assertIn("unavailable", amap["criteria_source"]["note"])

    def test_test_pbi_recognises_the_id_and_the_link_on_resume(self):
        self.assertEqual(pbi_state.pbi_id("DS-12", self.ws), "DS-12")
        self.assertEqual(pbi_state.pbi_id(f"{WORKSPACE}/issue/ds-12/apply-coupon", self.ws), "DS-12")
        self.assertEqual(pbi_state.pbi_id(f"{WORKSPACE}/issue/DS-12", self.ws), "DS-12")
        with self.assertRaises(pbi_state.UsageError):
            pbi_state.pbi_id("https://linear.example.com/issue/DS-12/x", self.ws)

    def test_test_pbi_accepts_a_jira_site_link(self):
        self.write_project({"sources": {"jira": ISSUES}})
        self.assertEqual(pbi_state.pbi_id("https://demo-shop.atlassian.net/browse/DS-3", self.ws), "DS-3")


class AzureDevOpsForBuilds(LinearBase):
    """Linear for issues while Azure DevOps stays the place of the builds: the issue reading needs no ADO credential and
    an ADO build link in an issue is listed, never opened, never proof that a fix is in a build."""

    tickets: ClassVar[dict] = {"sources": {"lin": LINEAR}}

    def test_no_azure_devops_credential_nor_request(self):
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write(f"DS_LINEAR_API_KEY={API_KEY}\n")
        fake = self.fake()
        fake.comment("DS-12", "c7", "Fixed in https://dev.azure.com/demo-shop/Demo%20Shop/_build/results?buildId=38")
        self.assertEqual(self.collect_lin(fake, "DS-12"), 0)
        doc = self.memory_doc(self.last_check(), "DS-12")
        builds = [x for x in doc["components"]["links"]["data"] if x["kind"] == "build"]
        self.assertEqual(len(builds), 1)
        self.assertFalse(builds[0]["content_read"])
        self.assertTrue(all(r.url.startswith("https://api.linear.app/") for r in fake.requests))

    def test_a_linear_issue_and_an_ado_item_in_one_workspace_stay_apart(self):
        from test_tickets import BOARDS
        from tickets_fake import ORG
        self.write_project({"sources": {"lin": LINEAR, "boards": BOARDS}})
        config = load(self.ws)
        self.assertEqual(resolve_ref(config, "123"), ("boards", "123"))
        self.assertEqual(resolve_ref(config, "DS-12"), ("lin", "DS-12"))
        self.assertEqual(resolve_ref(config, f"{ORG}/Demo%20Shop/_workitems/edit/123"), ("boards", "123"))


class JiraUnchanged(LinearBase):
    tickets: ClassVar[dict] = {"sources": {"issues": ISSUES}}

    def test_a_jira_key_still_reads_jira(self):
        fake = FakeJira()
        fake.add("DS-1")
        self.assertEqual(self.collect(fake, "DS-1"), 0)
        self.assertEqual(self.ticket(self.last_check(), "DS-1")["ref"]["service"], "jira")


class SharedWithJiraAndAzureDevOps(Base):
    """What this change touches in the common flow, run on the two existing services: a failure in the middle of a read,
    then a complete read with no real change, reports nothing; their fields carry no unavailable/partial marks, so their
    comparison is the one it was; links still resolve only to their own sources."""

    tickets: ClassVar[dict] = {"sources": {"boards": BOARDS, "issues": ISSUES}}

    def assert_fail_then_complete_is_quiet(self, fake, ref, fault_key, tid):
        self.assertEqual(self.collect(fake, ref), 0)
        fake.faults.rules[fault_key] = "network"
        self.assertEqual(self.collect(fake, ref, now=T2), 1)
        partial = self.ticket(self.last_check(), tid)
        self.assertEqual(partial["coverage"]["comments"]["status"], "partial")
        self.assertEqual(partial["changes"], [])
        fake.faults.rules.clear()
        self.assertEqual(self.collect(fake, ref, now=T3), 0)
        check = self.last_check()
        self.assertEqual(check["conclusion"], "no-changes")
        self.assertEqual(self.ticket(check, tid)["changes"], [])
        fields = self.memory_doc(check, tid)["components"]["fields"]["data"]
        self.assertNotIn("unavailable", fields)
        self.assertNotIn("partial", fields)

    def test_azure_devops(self):
        fake = FakeAdo(page_size=2)
        fake.add(100)
        for i in range(1, 6):
            fake.comment(100, i, f"<p>comment {i}</p>")
        self.assert_fail_then_complete_is_quiet(fake, "100", "continuationToken=2", "100")

    def test_jira(self):
        fake = FakeJira()
        fake.add("DS-1")
        for i in range(5):
            fake.comments["DS-1"].append({"id": str(i), "body": f"c{i}", "created": "2026-10-01T10:00:00.000+0000",
                                          "updated": "2026-10-01T10:00:00.000+0000"})
        self.assert_fail_then_complete_is_quiet(fake, "issues:DS-1", "startAt=2", "DS-1")

    def test_links_still_resolve_only_to_their_own_sources(self):
        from tickets_fake import ORG, SITE
        config = load(self.ws)
        self.assertEqual(resolve_ref(config, f"{ORG}/Demo%20Shop/_workitems/edit/7"), ("boards", "7"))
        self.assertEqual(resolve_ref(config, f"{SITE}/browse/DS-3"), ("issues", "DS-3"))
        for foreign in ("https://linear.app/demo-shop/issue/DS-3/x", "https://evil.example/browse/DS-3",
                        "https://dev.azure.com/other-org/Demo%20Shop/_workitems/edit/7"):
            with self.assertRaises(InvalidInput):
                resolve_ref(config, foreign)
        self.assertEqual(resolve_ref(config, "DS-3"), ("issues", "DS-3"))     # one key service: no question asked

    def test_test_pbi_links_of_both_services_and_a_foreign_host(self):
        from tickets_fake import ORG, SITE
        self.assertEqual(pbi_state.pbi_id(f"{ORG}/Demo%20Shop/_workitems/edit/7", self.ws), "7")
        self.assertEqual(pbi_state.pbi_id(f"{SITE}/browse/DS-3", self.ws), "DS-3")
        with self.assertRaises(pbi_state.UsageError):
            pbi_state.pbi_id("https://evil.example/browse/DS-3", self.ws)


if __name__ == "__main__":
    unittest.main()
