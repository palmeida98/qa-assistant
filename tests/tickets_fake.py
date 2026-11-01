"""A stateful fake of the read endpoints /analyze-tickets uses, for Azure DevOps and Jira (plan 14.1): fictional Demo Shop
data only, no network. Not a test module. A write is an AssertionError: nothing may even try one."""

from __future__ import annotations

import json
import re
import urllib.parse

from adapters.tracker.http import Request, Response, TransportError

ORG = "https://dev.azure.com/demo-shop"
SITE = "https://demo-shop.atlassian.net"


def _json(doc, status: int = 200, headers: dict | None = None) -> Response:
    return Response(status, headers or {}, json.dumps(doc).encode("utf-8"))


class Faults:
    """Rules checked per request: a substring of the path -> an HTTP status or 'network'."""

    def __init__(self):
        self.rules: dict[str, object] = {}

    def check(self, path: str) -> Response | None:
        for needle, what in self.rules.items():
            if needle in path:
                if what == "network":
                    raise TransportError("connection refused")
                return _json({"message": "fault"}, int(what))
        return None


class FakeAdo:
    def __init__(self, page_size: int = 2):
        self.items: dict[str, dict] = {}
        self.comments: dict[str, list] = {}
        self.updates: dict[str, list] = {}
        self.wiql_ids: list[int] = []
        self.iterations = [{"path": "Demo Shop\\Sprint 5"}]
        self.page_size = page_size
        self.faults = Faults()
        self.requests: list[Request] = []

    def add(self, wid, **fields) -> dict:
        wid = str(wid)
        defaults = {"System.TeamProject": "Demo Shop", "System.WorkItemType": "Task", "System.Title": f"Item {wid}",
                    "System.State": "To Do", "System.IterationPath": "Demo Shop\\Sprint 5",
                    "System.Description": "<p>Applies a coupon</p>",
                    "System.ChangedDate": "2026-10-01T10:00:00Z"}
        fields = {k.replace("__", "."): v for k, v in fields.items()}
        self.items[wid] = {"id": int(wid), "rev": 1, "fields": {**defaults, **fields}, "relations": []}
        self.comments.setdefault(wid, [])
        self.updates.setdefault(wid, [])
        return self.items[wid]

    def relate(self, a, rel: str, b) -> None:
        self.items[str(a)]["relations"].append({"rel": rel, "url": f"{ORG}/_apis/wit/workItems/{b}", "attributes": {}})

    def link_family(self, parent, *children) -> None:
        for child in children:
            self.relate(parent, "System.LinkTypes.Hierarchy-Forward", child)
            self.relate(child, "System.LinkTypes.Hierarchy-Reverse", parent)

    def set(self, wid, by="Ana Tester", at="2026-10-02T09:00:00Z", **fields) -> None:
        item = self.items[str(wid)]
        change = {k.replace("__", "."): {"oldValue": item["fields"].get(k.replace("__", ".")), "newValue": v}
                  for k, v in fields.items()}
        for k, v in fields.items():
            item["fields"][k.replace("__", ".")] = v
        item["rev"] += 1
        item["fields"]["System.ChangedDate"] = at
        change["System.ChangedDate"] = {"newValue": at}
        self.updates[str(wid)].append({"id": len(self.updates[str(wid)]) + 1, "rev": item["rev"],
                                       "revisedBy": {"displayName": by}, "revisedDate": "9999-01-01T00:00:00Z",
                                       "fields": change})

    def comment(self, wid, cid, text, by="Dev One", at="2026-10-02T10:00:00Z") -> dict:
        c = {"id": cid, "version": 1, "text": text, "createdBy": {"displayName": by}, "createdDate": at,
             "modifiedDate": at, "isDeleted": False}
        self.comments[str(wid)].append(c)
        return c

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        path = urllib.parse.unquote(req.path)
        fault = self.faults.check(path)
        if fault:
            return fault
        if req.method == "POST" and "/_apis/wit/wiql" in path:
            assert req.read_only
            return _json({"workItems": [{"id": i} for i in self.wiql_ids]})
        assert req.method == "GET", f"a write reached the tracker: {req.method} {req.path}"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.url).query)
        if "/teamsettings/iterations" in path:
            return _json({"value": self.iterations})
        if "/_apis/connectionData" in path:
            return _json({"authenticatedUser": {"providerDisplayName": "Ana Tester"}})
        m = re.search(r"/workItems/(\d+)/comments", path, re.IGNORECASE)
        if m:
            start = int((query.get("continuationToken") or ["0"])[0])
            rows = self.comments.get(m.group(1), [])
            page = rows[start:start + self.page_size]
            doc = {"comments": page}
            if start + self.page_size < len(rows):
                doc["continuationToken"] = str(start + self.page_size)
            return _json(doc)
        m = re.search(r"/workItems/(\d+)/updates", path, re.IGNORECASE)
        if m:
            skip, top = int(query.get("$skip", ["0"])[0]), int(query.get("$top", ["200"])[0])
            return _json({"value": self.updates.get(m.group(1), [])[skip:skip + top]})
        m = re.search(r"/workitems/(\d+)", path, re.IGNORECASE)
        if m:
            item = self.items.get(m.group(1))
            return _json(item) if item else _json({"message": "not found"}, 404)
        raise AssertionError(f"unexpected request {req.method} {path}")


class FakeJira:
    def __init__(self, page_size: int = 2):
        self.issues: dict[str, dict] = {}
        self.comments: dict[str, list] = {}
        self.changelog: dict[str, list] = {}
        self.remote: dict[str, list] = {}
        self.search_keys: list[str] = []
        self.page_size = page_size
        self.faults = Faults()
        self.requests: list[Request] = []

    def add(self, key, **fields) -> dict:
        defaults = {"summary": f"Issue {key}", "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                    "issuetype": {"name": "Story", "hierarchyLevel": 0}, "description": "Applies a coupon",
                    "updated": "2026-10-01T10:00:00.000+0000", "customfield_10020": [{"name": "Sprint 5", "state": "active"}],
                    "attachment": [], "issuelinks": [], "subtasks": []}
        self.issues[key] = {"key": key, "fields": {**defaults, **fields}, "names": {"customfield_10020": "Sprint"}}
        self.comments.setdefault(key, [])
        self.changelog.setdefault(key, [])
        self.remote.setdefault(key, [])
        return self.issues[key]

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        path = urllib.parse.unquote(req.path)
        assert "xray" not in req.url and "/api/v2/" not in path, f"Xray was called: {req.url}"
        fault = self.faults.check(path)
        if fault:
            return fault
        if req.method == "POST" and path.startswith("/rest/api/3/search/jql"):
            assert req.read_only
            jql = req.json()["jql"]
            m = re.match(r'parent = "([^"]+)"', jql)
            keys = [k for k, v in self.issues.items() if (v["fields"].get("parent") or {}).get("key") == m.group(1)] \
                if m else self.search_keys
            return _json({"issues": [{"key": k} for k in keys], "isLast": True})
        assert req.method == "GET", f"a write reached the tracker: {req.method} {req.path}"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.url).query)
        if path.startswith("/rest/api/3/myself"):
            return _json({"displayName": "Ana Tester"})
        m = re.match(r"/rest/api/3/issue/([^/?]+)(/\w+)?", path)
        if not m:
            raise AssertionError(f"unexpected request {req.method} {path}")
        key, tail = m.group(1), m.group(2)
        start = int(query.get("startAt", ["0"])[0])
        if key not in self.issues:
            return _json({"errorMessages": ["not found"]}, 404)
        if tail == "/comment":
            rows = self.comments[key]
            return _json({"comments": rows[start:start + self.page_size], "total": len(rows)})
        if tail == "/changelog":
            rows = self.changelog[key]
            return _json({"values": rows[start:start + self.page_size], "total": len(rows)})
        if tail == "/remotelink":
            return _json(self.remote[key])
        return _json(self.issues[key])
