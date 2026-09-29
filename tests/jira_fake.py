"""A stateful fake Jira Cloud + Xray Cloud for the Jira back-end's tests (plan 10.2): a transport (Request ->
Response) that keeps issues, Xray tests, test executions, comments and links, evaluates the small JQL the adapter
writes, and answers the GraphQL operations it sends. Fictional Demo Shop data only; no network. Not a test module.

The scripted goldens (tests/golden/jira/) use adapters.tracker.testing.FakeTransport instead; this fake serves the
unit tests and the round trip (technique-jira-xray.md section 10.2), where the requests depend on the state.
"""

from __future__ import annotations

import base64
import copy
import json
import re

from adapters.tracker.http import Request, Response, TransportTimeout

SITE = "https://demo-shop.atlassian.net"
XRAY = "https://xray.cloud.getxray.app"
TYPES = {"Test": "10001", "Bug": "10002", "Test Execution": "10003", "Test Plan": "10004", "Precondition": "10005"}
FIELD_SCHEMAS = {
    "summary": {"type": "string", "system": "summary"},
    "description": {"type": "string", "system": "description"},
    "environment": {"type": "string", "system": "environment"},
    "labels": {"type": "array", "items": "string", "system": "labels"},
    "components": {"type": "array", "items": "component", "system": "components"},
    "priority": {"type": "priority", "system": "priority"},
    "customfield_10050": {"type": "string", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textfield"},
    "customfield_10051": {"type": "option", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select"},
    "customfield_10060": {"type": "option", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select"},
}
TRANSITIONS = [{"id": "21", "name": "Start", "to": {"name": "In Progress"}},
               {"id": "31", "name": "Resolve", "to": {"name": "Done"}}]


# --- a small JQL evaluator (the subset the adapter writes) ---------------------------------------------------------

TOKEN = re.compile(r'\s*(?:(\()|(\))|(,)|("(?:\\.|[^"\\])*")|(!=|=|~)|([A-Za-z_][\w.\[\]]*|\d[\w-]*))')


def _tokens(jql):
    out, pos = [], 0
    jql = re.sub(r"\s+ORDER BY .*$", "", jql, flags=re.IGNORECASE)
    while pos < len(jql):
        m = TOKEN.match(jql, pos)
        if not m or m.end() == pos:
            if jql[pos:].strip() == "":
                break
            raise ValueError(f"JQL: cannot read {jql[pos:]!r}")
        pos = m.end()
        lp, rp, comma, string, op, word = m.groups()
        if string is not None:
            out.append(("str", json.loads(string)))
        elif op:
            out.append(("op", op))
        elif word:
            up = word.upper()
            out.append(("kw", up) if up in ("AND", "OR", "IN") else ("word", word))
        else:
            out.append(("p", lp or rp or comma))
    return out


class _Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self):
        node = self.term()
        while self.peek() == ("kw", "OR"):
            self.take()
            node = ("or", node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.peek() == ("kw", "AND"):
            self.take()
            node = ("and", node, self.factor())
        return node

    def factor(self):
        if self.peek() == ("p", "("):
            self.take()
            node = self.expr()
            assert self.take() == ("p", ")")
            return node
        _, field = self.take()
        kind, op = self.take()
        if (kind, op) == ("kw", "IN"):
            assert self.take() == ("p", "(")
            values = []
            while True:
                values.append(self.take()[1])
                if self.take() == ("p", ")"):
                    break
            return ("in", field, values)
        return (op, field, self.take()[1])


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict) and value.get("type") == "doc":
        out = []

        def walk(n):
            if n.get("type") == "text":
                out.append(n.get("text", ""))
            for c in n.get("content") or []:
                walk(c)
            out.append(" ")
        walk(value)
        return "".join(out)
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or "")
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return str(value)


def _values(issue, field) -> list[str]:
    f = issue["fields"]
    if field.lower() in ("key", "issuekey"):
        return [issue["key"]] + issue.get("old_keys", [])
    if field == "project":
        return [issue["key"].split("-")[0]]
    if field == "issuetype":
        return [f["issuetype"]["name"]]
    if field == "labels":
        return list(f.get("labels") or [])
    if field == "component":
        return [c["name"] for c in f.get("components") or []]
    if field == "statusCategory":
        return [f.get("status", {}).get("statusCategory", {}).get("name", "To Do")]
    m = re.fullmatch(r"cf\[(\d+)\]", field)
    if m:
        v = f.get(f"customfield_{m.group(1)}")
        return [x for x in _text(v).split(" ") if x] if isinstance(v, list) else ([_text(v)] if v else [])
    return [_text(f.get(field))] if f.get(field) is not None else []


def _eval(node, issue) -> bool:
    op = node[0]
    if op == "and":
        return _eval(node[1], issue) and _eval(node[2], issue)
    if op == "or":
        return _eval(node[1], issue) or _eval(node[2], issue)
    field = node[1]
    if op == "in":
        return any(v in _values(issue, field) for v in node[2])
    if op == "=":
        return any(str(v).lower() == str(node[2]).lower() for v in _values(issue, field))
    if op == "!=":
        return all(str(v).lower() != str(node[2]).lower() for v in _values(issue, field))
    if op == "~":
        words = re.findall(r'"([^"]+)"', node[2]) or [node[2]]
        if field == "text":
            hay = (_text(issue["fields"].get("summary")) + " " + _text(issue["fields"].get("description"))).lower()
            return all(w.lower() in hay for w in words)
        hay = " ".join(_values(issue, field)).lower()
        return all(w.lower() in hay for w in words)
    raise ValueError(op)


def jql_match(jql: str, issue: dict) -> bool:
    p = _Parser(_tokens(jql))
    node = p.expr()
    return _eval(node, issue)


# --- the fake ------------------------------------------------------------------------------------------------------

class FakeJiraXray:
    """Transport of a fake Jira + Xray. `drop_answer_at=n`: the n-th request is DONE, but its answer is lost
    (timeout): the uncertain write. `fail_at=n`: the n-th request never reaches the server. `token_uses`: an Xray
    token expires after this many uses (a 401, then the adapter authenticates again). `automation`: test status
    replaced by an automation rule on import (the read back sees a difference)."""

    def __init__(self, fail_at=None, drop_answer_at=None, token_uses=None, automation=None, page=100):
        self.issues: dict[str, dict] = {}
        self.xray: dict[str, dict] = {}          # test issue id -> {testType, steps, preconditions}
        self.plan_status: dict[str, str] = {}    # test issue id -> status name in the test plan
        self.executions: dict[str, dict] = {}    # execution issue id -> {test issue id: run}
        self.comments: dict[str, list] = {}
        self.links: list[dict] = []
        self.folders: dict[str, str] = {}
        self.statuses = ["TODO", "EXECUTING", "PASSED", "FAILED", "ABORTED", "BLOCKED"]
        self.requests: list[Request] = []
        self.fail_at, self.drop_answer_at = fail_at, drop_answer_at
        self.token_uses, self.automation, self.page = token_uses, automation, page
        self.token_n, self.token_left = 0, None
        self.next_id = 20000
        self.next_n = {"DS": 100}
        self.search_pages: list[int] = []

    # -- state helpers --------------------------------------------------------------------------------------------

    def new_issue(self, type_name, fields, key=None, status="To Do", xray=None):
        self.next_id += 1
        iid = str(self.next_id)
        if key is None:
            self.next_n["DS"] += 1
            key = f"DS-{self.next_n['DS']}"
        elif key.startswith("DS-") and int(key[3:]) > self.next_n["DS"]:
            self.next_n["DS"] = int(key[3:])   # new keys never repeat one given by hand
        f = copy.deepcopy(fields)
        f["issuetype"] = {"id": TYPES[type_name], "name": type_name}
        f["status"] = {"name": status, "statusCategory": {"name": "Done" if status == "Done" else "To Do"}}
        f.setdefault("labels", [])
        self.issues[iid] = {"id": iid, "key": key, "fields": f}
        if type_name == "Test":
            self.xray[iid] = xray or {"testType": {"name": "Manual", "kind": "Steps"}, "steps": [],
                                      "preconditions": []}
        return self.issues[iid]

    def by_key(self, key):
        for i in self.issues.values():
            if i["key"] == key or key in i.get("old_keys", []):
                return i
        return None

    def of_type(self, type_name):
        return [i for i in self.issues.values() if i["fields"]["issuetype"]["name"] == type_name]

    def writes(self):
        return [r for r in self.requests if r.method not in ("GET", "HEAD") and not r.read_only]

    # -- transport -------------------------------------------------------------------------------------------------

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        n = len(self.requests)
        if self.fail_at == n:
            raise TransportTimeout(f"fault injected at request {n}")
        resp = self.handle(req)
        if self.drop_answer_at == n:
            raise TransportTimeout(f"answer lost at request {n} (the server did it)")
        return resp

    @staticmethod
    def ok(body, status=200):
        return Response(status, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"))

    def error(self, status, messages):
        return self.ok({"errorMessages": messages, "errors": {}}, status)

    def handle(self, req: Request) -> Response:
        path, method = req.path.split("?")[0], req.method
        if req.url.startswith(XRAY):
            if path == "/api/v2/authenticate":
                body = req.json()
                if not body.get("client_id") or not body.get("client_secret"):
                    return self.error(400, ["missing credentials"])
                self.token_n += 1
                self.token_left = self.token_uses
                return self.ok(f"xray-token-{self.token_n}")
            auth = req.headers.get("Authorization", "")
            if auth != f"Bearer xray-token-{self.token_n}" or self.token_left == 0:
                return self.error(401, ["token expired"])
            if self.token_left is not None:
                self.token_left -= 1
            if path == "/api/v2/graphql":
                return self.graphql(req.json())
            if path == "/api/v2/import/execution/multipart":
                parts = parse_multipart(req)
                return self.import_results(parts["results"], parts["info"])
            if path == "/api/v2/import/execution":
                return self.import_results(req.json(), None)
            return self.error(404, [f"no route {path}"])
        if not req.headers.get("Authorization", "").startswith("Basic "):
            return self.error(401, ["no auth"])
        if path == "/rest/api/3/myself":
            return self.ok({"accountId": "demo-account-1", "displayName": "QA Demo"})
        if path == "/rest/api/3/project/DS":
            return self.ok({"id": "10000", "key": "DS", "name": "Demo Shop",
                            "issueTypes": [{"id": v, "name": k} for k, v in TYPES.items()]})
        if path.startswith("/rest/api/3/project/"):
            if path == "/rest/api/3/project/DS/statuses":
                return self.ok([{"name": "Bug", "statuses": [{"name": "To Do"}, {"name": "In Progress"},
                                                             {"name": "Done"}]}])
            return self.error(404, ["No project could be found"])
        if path == "/rest/api/3/field":
            return self.ok([{"id": k, "name": k} for k in FIELD_SCHEMAS])
        m = re.fullmatch(r"/rest/api/3/issue/createmeta/DS/issuetypes/(\d+)", path)
        if m:
            fields = [{"fieldId": k, "key": k, "name": k, "required": k == "summary", "schema": v}
                      for k, v in FIELD_SCHEMAS.items()]
            return self.ok({"fields": fields, "startAt": 0, "maxResults": 200, "total": len(fields)})
        if path == "/rest/api/3/search/jql":
            return self.search(req.json())
        if path == "/rest/api/3/issue" and method == "POST":
            f = req.json()["fields"]
            type_name = f.pop("issuetype")["name"]
            f.pop("project")
            issue = self.new_issue(type_name, f)
            return self.ok({"id": issue["id"], "key": issue["key"], "self": f"{SITE}/rest/api/3/issue/{issue['id']}"},
                           201)
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z]+-\d+)(/\w+)?", path)
        if m:
            issue = self.by_key(m.group(1))
            if issue is None:
                return self.error(404, ["Issue does not exist or you do not have permission to see it."])
            sub = m.group(2)
            if sub is None and method == "PUT":
                issue["fields"].update(req.json()["fields"])
                return Response(204)
            if sub == "/comment" and method == "POST":
                c = {"id": str(10000 + len(self.comments.get(issue["key"], [])) + 1), "body": req.json()["body"]}
                self.comments.setdefault(issue["key"], []).append(c)
                return self.ok(c, 201)
            if sub == "/comment":
                return self.ok({"comments": self.comments.get(issue["key"], [])})
            if sub == "/transitions" and method == "GET":
                return self.ok({"transitions": [t for t in TRANSITIONS
                                                if t["to"]["name"] != issue["fields"]["status"]["name"]]})
            if sub == "/transitions":
                t = next(t for t in TRANSITIONS if t["id"] == req.json()["transition"]["id"])
                issue["fields"]["status"] = {"name": t["to"]["name"],
                                             "statusCategory": {"name": "Done" if t["to"]["name"] == "Done"
                                                                else "To Do"}}
                return Response(204)
        if path == "/rest/api/3/issueLinkType":
            return self.ok({"issueLinkTypes": [{"id": "10003", "name": "Relates"}, {"id": "10000", "name": "Blocks"}]})
        if path == "/rest/api/3/issueLink":
            self.links.append(req.json())
            return Response(201)
        return self.error(404, [f"no route {method} {path}"])

    def search(self, body):
        jql = body["jql"]
        keys = re.findall(r'"([A-Z]+-\d+)"', jql) if re.search(r"\bkey\b", jql) else []
        missing = [k for k in keys if self.by_key(k) is None]
        if missing:
            return self.error(400, [f"An issue with key '{missing[0]}' does not exist for field 'key'."])
        found = [i for i in sorted(self.issues.values(), key=lambda i: int(i["id"])) if jql_match(jql, i)]
        start = int(body.get("nextPageToken") or 0)
        size = min(int(body.get("maxResults") or 50), self.page)
        page = found[start:start + size]
        self.search_pages.append(len(page))
        last = start + size >= len(found)
        fields = body.get("fields") or []
        issues = [{"id": i["id"], "key": i["key"],
                   "fields": {k: v for k, v in i["fields"].items() if k in fields}} for i in page]
        return self.ok({"issues": issues, "isLast": last, **({} if last else {"nextPageToken": str(start + size)})})

    def graphql(self, body):
        q, v = body["query"], body.get("variables") or {}
        if "getStatuses" in q:
            return self.ok({"data": {"getStatuses": [{"name": s, "final": True} for s in self.statuses]}})
        if "getTests" in q:
            ids = v["issueIds"]
            if len(ids) > 100:
                return self.ok({"errors": [{"message": "limit is 100"}]})
            results = []
            for iid in ids:
                if iid not in self.xray:
                    continue
                x = self.xray[iid]
                t = {"issueId": iid, "testType": x["testType"], "steps": x["steps"],
                     "preconditions": {"results": x.get("preconditions") or []}}
                if "status(testPlan" in q:
                    t["status"] = {"name": self.plan_status.get(iid, "TODO")}
                results.append(t)
            return self.ok({"data": {"getTests": {"total": len(results), "start": 0, "limit": 100,
                                                  "results": results}}})
        if "getTestExecution" in q:
            runs = self.executions.get(v["issueId"])
            if runs is None:
                return self.ok({"data": {"getTestExecution": None}})
            items = list(runs.values())
            page = items[v.get("start") or 0:(v.get("start") or 0) + v["limit"]]
            return self.ok({"data": {"getTestExecution": {"issueId": v["issueId"], "testRuns": {
                "total": len(items), "start": v.get("start") or 0, "limit": v["limit"], "results": page}}}})
        if "createTest" in q:
            f = copy.deepcopy(v["jira"]["fields"])
            f.pop("project")
            steps = [{"action": s.get("action", ""), "data": s.get("data", ""), "result": s.get("result", "")}
                     for s in v.get("steps") or []]
            issue = self.new_issue("Test", f, xray={"testType": {"name": v["testType"]["name"], "kind": "Steps"},
                                                   "steps": steps, "preconditions": []})
            if v.get("folderPath"):
                self.folders[issue["id"]] = v["folderPath"]
            return self.ok({"data": {"createTest": {"test": {"issueId": issue["id"], "jira": {"key": issue["key"]}},
                                                    "warnings": []}}})
        if "addTestsToTestPlan" in q:
            for iid in v["testIssueIds"]:
                self.plan_status.setdefault(iid, "TODO")
            return self.ok({"data": {"addTestsToTestPlan": {"addedTests": v["testIssueIds"], "warning": None}}})
        return self.ok({"errors": [{"message": "unknown operation"}]})

    def import_results(self, results, info):
        if info is not None:
            f = copy.deepcopy(info["fields"])
            f.pop("project")
            f.pop("issuetype")
            execution = self.new_issue("Test Execution", f)
            execution["xrayFields"] = info.get("xrayFields")
            self.executions[execution["id"]] = {}
        else:
            execution = self.by_key(results["testExecutionKey"])
            if execution is None or execution["id"] not in self.executions:
                return self.error(400, ["Test Execution not found"])
        runs = self.executions[execution["id"]]
        for t in results.get("tests") or []:
            test = self.by_key(t["testKey"])
            if test is None:
                return self.error(400, [f"Test {t['testKey']} not found"])
            status = (self.automation or {}).get(t["testKey"], t["status"])
            runs[test["id"]] = {"status": {"name": status}, "defects": [self.by_key(d)["id"] for d in
                                                                         t.get("defects") or []],
                                "test": {"issueId": test["id"]}, "comment": t.get("comment"),
                                "evidence": [e["filename"] for e in t.get("evidence") or []]}
        return self.ok({"id": execution["id"], "key": execution["key"],
                        "self": f"{SITE}/rest/api/2/issue/{execution['id']}"})


def parse_multipart(req: Request) -> dict:
    """{part name: parsed JSON} of a multipart/form-data request."""
    ctype = req.headers.get("Content-Type", "")
    boundary = ctype.split("boundary=", 1)[1]
    out = {}
    for chunk in req.body.split(f"--{boundary}".encode()):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, _, content = chunk.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        out[name] = json.loads(content.decode("utf-8"))
    return out


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")
