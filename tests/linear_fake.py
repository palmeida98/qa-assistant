"""A stateful fake of Linear's GraphQL API for /analyze-ticket (technique-tickets.md section 4.4): fictional Demo Shop
data only, no network. Not a test module.

It accepts only the documents of adapters/tickets/linear.ALLOWED, sent as a POST marked as a read, with the key in
`Authorization` and no `Bearer`; anything else is an AssertionError: nothing may even try a write. The answers follow
the shapes of Linear's public schema (Relay connections: nodes + pageInfo {hasNextPage endCursor})."""

from __future__ import annotations

import json
import uuid

from adapters.tickets.linear import ALLOWED
from adapters.tracker.http import Request, Response, TransportError

WORKSPACE = "https://linear.app/demo-shop"
FICTIONAL_CREDENTIAL = "lin_api_fictional0123456789abcdef"   # the fake Linear's key: fictional, never a real one
API_KEY = FICTIONAL_CREDENTIAL
CONNECTIONS = {"IssueComments": "comments", "IssueHistory": "history", "IssueRelations": "relations",
               "IssueInverseRelations": "inverseRelations", "IssueChildren": "children",
               "IssueAttachments": "attachments"}


def _json(doc, status: int = 200, headers: dict | None = None) -> Response:
    return Response(status, headers or {}, json.dumps(doc).encode("utf-8"))


def gql_error(code: str, message: str, status: int = 200) -> Response:
    return _json({"data": None, "errors": [{"message": message, "extensions": {"code": code}}]}, status)


class FakeLinear:
    def __init__(self, page_size: int = 2, key: str = API_KEY):
        self.key = key
        self.issues: dict[str, dict] = {}
        self.conn: dict[str, dict[str, list]] = {}
        self.search_ids: list[str] = []
        self.teams = ["DS"]
        self.org = "demo-shop"
        self.page_size = page_size
        # (operationName, after-cursor or "*") -> a Response, "network", or a list of them consumed in order
        self.faults: dict[tuple[str, str], object] = {}
        self.requests: list[Request] = []

    # -- data -----------------------------------------------------------------------------------------------------

    def add(self, ident: str, **fields) -> dict:
        team = ident.split("-")[0]
        issue = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, ident)), "identifier": ident,
                 "url": f"{WORKSPACE}/issue/{ident}/{ident.lower()}-slug", "title": f"Issue {ident}",
                 "description": "Applies a coupon", "priority": 3, "priorityLabel": "Medium", "estimate": None,
                 "dueDate": None, "createdAt": "2026-10-01T09:00:00.000Z", "updatedAt": "2026-10-01T10:00:00.000Z",
                 "completedAt": None, "canceledAt": None, "archivedAt": None, "trashed": False,
                 "previousIdentifiers": [], "state": {"name": "Todo", "type": "unstarted"}, "assignee": None,
                 "team": {"key": team, "name": "Checkout"}, "project": {"name": "Coupons"},
                 "cycle": {"number": 5, "name": None}, "parent": None,
                 "labels": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        issue.update(fields)
        self.issues[ident] = issue
        self.conn[ident] = {name: [] for name in CONNECTIONS.values()}
        return issue

    def comment(self, ident: str, cid: str, body: str, by: str = "Dev One", at: str = "2026-10-02T10:00:00.000Z",
                **extra) -> dict:
        c = {"id": cid, "body": body, "createdAt": at, "editedAt": None, "resolvedAt": None,
             "user": {"displayName": by}, "parent": None, **extra}
        self.conn[ident]["comments"].append(c)
        return c

    def relate(self, a: str, kind: str, b: str) -> None:
        self.conn[a]["relations"].append({"type": kind, "relatedIssue": {"identifier": b}})
        if b in self.conn:
            self.conn[b]["inverseRelations"].append({"type": kind, "issue": {"identifier": a}})

    def child(self, parent: str, child: str) -> None:
        self.conn[parent]["children"].append({"identifier": child})
        self.issues[child]["parent"] = {"identifier": parent}

    def _by_id(self, value: str) -> str | None:
        value = str(value)
        if value.upper() in self.issues:
            return value.upper()
        return next((k for k, v in self.issues.items() if v["id"] == value), None)

    # -- the transport --------------------------------------------------------------------------------------------

    def _fault(self, name: str, after) -> Response | None:
        for key in ((name, str(after)), (name, "*")):
            what = self.faults.get(key)
            if isinstance(what, list):
                what = what.pop(0) if what else None
            if what == "network":
                raise TransportError("connection refused")
            if what is not None:
                return what
        return None

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        assert req.url == "https://api.linear.app/graphql", f"not Linear's endpoint: {req.url}"
        assert req.method == "POST" and req.read_only, f"not a read: {req.method} {req.url}"
        assert req.headers.get("Authorization") == self.key, "the key must be sent as Authorization: <key>"
        body = req.json()
        name = body.get("operationName")
        assert body.get("query") == ALLOWED.get(name), f"a document outside the allowed queries: {name}"
        assert "mutation" not in body["query"].lower()
        variables = body.get("variables") or {}
        fault = self._fault(name, variables.get("after"))
        if fault is not None:
            return fault
        if name == "Viewer":
            return _json({"data": {"viewer": {"displayName": "Ana Tester"}, "organization": {"urlKey": self.org},
                                   "teams": {"nodes": [{"key": k} for k in self.teams],
                                             "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
        if name == "Issues":
            return _json({"data": {"issues": self._page([{"identifier": i} for i in self.search_ids],
                                                         variables.get("after"))}})
        ident = self._by_id(variables.get("id"))
        if ident is None:
            return gql_error("INVALID_INPUT", "Entity not found: Issue")
        if name == "Issue":
            return _json({"data": {"issue": self.issues[ident]}})
        field = CONNECTIONS[name]
        return _json({"data": {"issue": {field: self._page(self.conn[ident][field], variables.get("after"))}}})

    def _page(self, rows: list, after) -> dict:
        start = int(after) if after else 0
        end = start + self.page_size
        more = end < len(rows)
        return {"nodes": rows[start:end], "pageInfo": {"hasNextPage": more, "endCursor": str(end) if more else None}}
