"""A stateful fake Azure DevOps Services for the Azure DevOps back-end's tests (plan 10.3): a transport (Request ->
Response) that keeps work items (with revisions, tags, relations), comments, a test plan with suites, configurations
and test points, test runs with their results and attachments; it evaluates the small WIQL the adapter writes and pages
with x-ms-continuationtoken like the real service. Fictional Demo Shop data only; no network. Not a test module.

The scripted goldens (tests/golden/azure_devops/) use adapters.tracker.testing.FakeTransport instead; this fake serves
the unit tests and the round trip (technique-azure-devops.md section 8), where the requests depend on the state.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse

from adapters.tracker.http import Request, Response, TransportTimeout

ORG = "https://dev.azure.com/demo-shop"
PROJECT = "Demo Shop"
P = "/Demo%20Shop"
PROCESSES = {"Agile": "adcc42ab-9882-485e-a3ed-7678f01f66bc", "Scrum": "6b724908-ef14-45cf-84f8-768b5384da45",
             "CMMI": "27450541-8e31-4150-9947-dc59f998fc01", "Basic": "b8a3a935-7e91-48b8-a94c-606d37c3e9f2"}
INHERITED = "11111111-2222-3333-4444-555555555555"
ZERO = "00000000-0000-0000-0000-000000000000"
FIELD_TYPES = {
    "System.Id": "integer", "System.Title": "string", "System.State": "string", "System.Tags": "plainText",
    "System.AreaPath": "treePath", "System.IterationPath": "treePath", "System.WorkItemType": "string",
    "System.TeamProject": "string", "System.Description": "html", "Microsoft.VSTS.TCM.ReproSteps": "html",
    "Microsoft.VSTS.TCM.SystemInfo": "html", "Microsoft.VSTS.TCM.Steps": "html",
    "Microsoft.VSTS.TCM.Parameters": "html", "Microsoft.VSTS.Common.Priority": "integer",
    "Microsoft.VSTS.Common.Severity": "string", "Custom.Feature": "string", "Custom.QaId": "string",
}
SEVERITIES = ["1 - Critical", "2 - High", "3 - Medium", "4 - Low"]
BUG_STATES = {"Agile": ["New", "Active", "Resolved", "Closed", "Removed"],
              "Scrum": ["New", "Approved", "Committed", "Done", "Removed"]}
TYPE_FIELDS = {
    "Test Case": ["System.Id", "System.Title", "System.State", "System.Tags", "System.AreaPath",
                  "System.IterationPath", "System.WorkItemType", "System.TeamProject", "System.Description",
                  "Microsoft.VSTS.TCM.Steps", "Microsoft.VSTS.TCM.Parameters", "Microsoft.VSTS.Common.Priority",
                  "Custom.Feature", "Custom.QaId"],
    "Bug": ["System.Id", "System.Title", "System.State", "System.Tags", "System.AreaPath", "System.IterationPath",
            "System.WorkItemType", "System.TeamProject", "Microsoft.VSTS.TCM.ReproSteps",
            "Microsoft.VSTS.TCM.SystemInfo", "Microsoft.VSTS.Common.Priority", "Microsoft.VSTS.Common.Severity",
            "Custom.Feature"],
    "Shared Steps": ["System.Id", "System.Title", "System.State", "System.Tags", "System.AreaPath",
                     "Microsoft.VSTS.TCM.Steps"],
}
CONTINUATION = "x-ms-continuationtoken"


def _camel(outcome: str) -> str:
    return outcome[:1].lower() + outcome[1:] if outcome else outcome


def _plain(value) -> str:
    return re.sub(r"<[^>]+>", " ", str(value or "")).replace("&amp;", "&")


# --- a small WIQL evaluator (the subset the adapter writes) --------------------------------------------------------

TOKEN = re.compile(r"\s*(?:(\()|(\))|(,)|('(?:''|[^'])*')|(<>|=)|(\[[^\]]+\])|(@\w+)|(\d+)|([A-Za-z]+))")


def _tokens(text):
    out, pos = [], 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"WIQL: cannot read {text[pos:]!r}")
        pos = m.end()
        lp, rp, comma, string, op, field, macro, number, word = m.groups()
        if string is not None:
            out.append(("str", string[1:-1].replace("''", "'")))
        elif op:
            out.append(("op", op))
        elif field:
            out.append(("field", field[1:-1]))
        elif macro:
            out.append(("str", PROJECT if macro.lower() == "@project" else macro))
        elif number:
            out.append(("num", int(number)))
        elif word:
            out.append(("kw", word.upper()))
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

    def values(self):
        assert self.take() == ("p", "(")
        out = []
        while True:
            out.append(self.take()[1])
            if self.take() == ("p", ")"):
                return out

    def factor(self):
        if self.peek() == ("p", "("):
            self.take()
            node = self.expr()
            assert self.take() == ("p", ")")
            return node
        kind, field = self.take()
        assert kind == "field", (kind, field)
        kind, op = self.take()
        if (kind, op) == ("kw", "IN"):
            return ("in", field, self.values())
        if (kind, op) == ("kw", "NOT"):
            assert self.take() == ("kw", "IN")
            return ("notin", field, self.values())
        if (kind, op) == ("kw", "CONTAINS"):
            if self.peek() == ("kw", "WORDS"):
                self.take()
                return ("words", field, self.take()[1])
            return ("contains", field, self.take()[1])
        if (kind, op) == ("kw", "UNDER"):
            return ("under", field, self.take()[1])
        return (op, field, self.take()[1])


def _value(wi, field):
    if field == "System.Id":
        return wi["id"]
    return wi["fields"].get(field)


def _eval(node, wi) -> bool:
    op = node[0]
    if op == "and":
        return _eval(node[1], wi) and _eval(node[2], wi)
    if op == "or":
        return _eval(node[1], wi) or _eval(node[2], wi)
    field, want = node[1], node[2]
    value = _value(wi, field)
    text = "" if value is None else str(value)
    if op == "=":
        return text.lower() == str(want).lower()
    if op == "<>":
        return text.lower() != str(want).lower()
    if op == "in":
        return text.lower() in [str(v).lower() for v in want]
    if op == "notin":
        return text.lower() not in [str(v).lower() for v in want]
    if op == "under":
        return text.lower() == str(want).lower() or text.lower().startswith(str(want).lower() + "\\")
    if op == "contains":
        if field == "System.Tags":
            return str(want).lower() in [t.strip().lower() for t in text.split(";")]
        return str(want).lower() in text.lower()
    if op == "words":
        hay = _plain(text).lower()
        return all(re.search(rf"\b{re.escape(w)}\b", hay) for w in str(want).lower().split())
    raise ValueError(op)


def wiql_match(query: str, wi: dict) -> bool:
    where = re.split(r"\bWHERE\b", query, maxsplit=1, flags=re.IGNORECASE)[1]
    where = re.split(r"\bORDER\s+BY\b", where, flags=re.IGNORECASE)[0]
    return _eval(_Parser(_tokens(where)).expr(), wi)


# --- the fake ------------------------------------------------------------------------------------------------------

class FakeAzureDevOps:
    """Transport of a fake Azure DevOps. `drop_answer_at=n`: the n-th request is DONE, but its answer is lost
    (timeout): the uncertain write. `fail_at=n`: the n-th request never reaches the server. `automation`: work item id
    -> an outcome a rule replaces on a result (the read back sees a difference). `process`: Agile, Scrum, CMMI, Basic
    or "Inherited" (a process inherited from Agile). `page`: page size of the continuation-token lists."""

    def __init__(self, fail_at=None, drop_answer_at=None, automation=None, process="Agile", page=100):
        self.items: dict[int, dict] = {}
        self.comments: dict[int, list] = {}
        self.suites = {13: {"id": 13, "name": "Release 2.4", "suiteType": "staticTestSuite", "cases": [],
                            "configs": [1]}}
        self.configs = {1: "Windows 11 - Chrome", 2: "Android 15"}
        self.points: dict[int, dict] = {}
        self.runs: dict[int, dict] = {}
        self.attachments: dict[int, list] = {}
        self.requests: list[Request] = []
        self.fail_at, self.drop_answer_at = fail_at, drop_answer_at
        self.automation, self.process, self.page = automation or {}, process, page
        self.next_id, self.next_point, self.next_run, self.next_result = 5000, 9000, 100, 100000
        self.pages: list[int] = []

    # -- state helpers --------------------------------------------------------------------------------------------

    def new_item(self, type_name, fields, wid=None, state=None):
        if wid is None:
            self.next_id += 1
            wid = self.next_id
        elif wid > self.next_id and wid < 9000:
            self.next_id = wid
        f = {"System.Title": "", "System.State": state or ("New" if type_name == "Bug" else "Design"),
             "System.AreaPath": PROJECT, "System.IterationPath": PROJECT, **copy.deepcopy(fields),
             "System.WorkItemType": type_name, "System.TeamProject": PROJECT, "System.Id": wid}
        self.items[wid] = {"id": wid, "rev": 1, "fields": f, "relations": []}
        return self.items[wid]

    def add_to_suite(self, wid, suite=13):
        s = self.suites[suite]
        added = []
        if wid not in s["cases"]:
            s["cases"].append(wid)
            for conf in s["configs"]:
                self.next_point += 1
                self.points[self.next_point] = {"id": self.next_point, "suite": suite, "case": wid, "config": conf,
                                                "outcome": "unspecified"}
                added.append(self.next_point)
        return added

    def of_type(self, type_name):
        return [w for w in self.items.values() if w["fields"]["System.WorkItemType"] == type_name]

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
    def ok(body, status=200, headers=None):
        return Response(status, {"Content-Type": "application/json", **(headers or {})},
                        json.dumps(body).encode("utf-8"))

    def error(self, status, message):
        return self.ok({"message": message, "typeKey": "FakeError"}, status)

    def paged(self, items, q):
        start = int(q.get("continuationToken") or 0)
        page = items[start:start + self.page]
        self.pages.append(len(page))
        more = start + self.page < len(items)
        return self.ok({"count": len(page), "value": page}, headers={CONTINUATION: str(start + self.page)} if more
                       else {})

    def handle(self, req: Request) -> Response:
        u = urllib.parse.urlsplit(req.url)
        if f"{u.scheme}://{u.netloc}" + u.path[:len("/demo-shop")] != ORG:
            return self.error(404, "no such organization")
        path = u.path[len("/demo-shop"):]
        q = dict(urllib.parse.parse_qsl(u.query))
        if not q.get("api-version"):
            return self.error(400, "api-version is required")
        if not req.headers.get("Authorization", "").startswith("Basic "):
            return self.error(401, "no auth")
        method = req.method
        body = req.json() if req.body else None
        if path == "/_apis/projects/Demo%20Shop":
            name = "Agile (inherited)" if self.process == "Inherited" else self.process
            tid = INHERITED if self.process == "Inherited" else PROCESSES[self.process]
            return self.ok({"id": "fake-project-id", "name": PROJECT, "capabilities": {
                "processTemplate": {"templateName": name, "templateTypeId": tid}}})
        m = re.fullmatch(r"/_apis/work/processes/([\w-]+)", path)
        if m:
            if m.group(1) == INHERITED:
                return self.ok({"typeId": INHERITED, "name": "Agile (inherited)",
                                "parentProcessTypeId": PROCESSES["Agile"]})
            name = next(k for k, v in PROCESSES.items() if v == m.group(1))
            return self.ok({"typeId": m.group(1), "name": name, "parentProcessTypeId": ZERO})
        if not path.startswith(P + "/_apis/"):
            return self.error(404, f"no route {path}")
        path = path[len(P):]
        if path == "/_apis/wit/workitemtypes":
            types = ["Test Case", "Shared Steps", "Issue", "Task", "Epic"] if self.process == "Basic" else \
                ["Test Case", "Shared Steps", "Bug", "User Story", "Task"]
            return self.ok({"count": len(types), "value": [{"name": t} for t in types]})
        if path == "/_apis/wit/fields":
            return self.ok({"value": [{"referenceName": k, "name": k, "type": v} for k, v in FIELD_TYPES.items()]})
        m = re.fullmatch(r"/_apis/wit/workitemtypes/([^/]+)/fields", path)
        if m:
            type_name = urllib.parse.unquote(m.group(1))
            if type_name not in TYPE_FIELDS:
                return self.error(404, "work item type not found")
            out = []
            for ref in TYPE_FIELDS[type_name]:
                allowed = []
                if ref == "System.State" and type_name == "Bug":
                    allowed = BUG_STATES.get(self.process, BUG_STATES["Agile"])
                if ref == "Microsoft.VSTS.Common.Severity":
                    allowed = SEVERITIES
                out.append({"referenceName": ref, "name": ref, "alwaysRequired": ref == "System.Title",
                            "defaultValue": None, "allowedValues": allowed})
            return self.ok({"count": len(out), "value": out})
        m = re.fullmatch(r"/_apis/testplan/plans/(\d+)", path)
        if m:
            if int(m.group(1)) != 12:
                return self.error(404, "test plan not found")
            return self.ok({"id": 12, "name": "Release 2.4", "rootSuite": {"id": 13}})
        m = re.fullmatch(r"/_apis/testplan/Plans/(\d+)/suites", path)
        if m:
            if int(m.group(1)) != 12:
                return self.error(404, "test plan not found")
            return self.paged([{"id": s["id"], "name": s["name"], "suiteType": s["suiteType"]}
                               for s in self.suites.values()], q)
        if path == "/_apis/testplan/configurations":
            return self.paged([{"id": k, "name": v, "state": "active"} for k, v in self.configs.items()], q)
        m = re.fullmatch(r"/_apis/testplan/Plans/12/Suites/(\d+)/(TestCase|TestPoint)", path)
        if m:
            return self.suite(int(m.group(1)), m.group(2), method, q, body)
        if path == "/_apis/wit/wiql":
            ids = [wid for wid, wi in sorted(self.items.items()) if wiql_match(body["query"], wi)]
            ids = ids[:int(q.get("$top") or 20000)]
            return self.ok({"queryType": "flat", "workItems": [{"id": i, "url": f"{ORG}/_apis/wit/workItems/{i}"}
                                                               for i in ids]})
        if path == "/_apis/wit/workitemsbatch":
            if len(body["ids"]) > 200:
                return self.error(400, "at most 200 ids")
            out = []
            for wid in body["ids"]:
                wi = self.items.get(int(wid))
                if wi is None:
                    if body.get("errorPolicy") != "Omit":
                        return self.error(404, f"work item {wid} does not exist")
                    out.append(None)
                    continue
                fields = body.get("fields")
                f = {k: v for k, v in wi["fields"].items() if not fields or k in fields}
                entry = {"id": wid, "rev": wi["rev"], "fields": f}
                if body.get("$expand") in ("Relations", "All"):
                    entry["relations"] = wi["relations"]
                out.append(entry)
            return self.ok({"count": len(out), "value": out})
        m = re.fullmatch(r"/_apis/wit/workitems/\$(.+)", path)
        if m and method == "POST":
            return self.create(urllib.parse.unquote(m.group(1)), body, req)
        m = re.fullmatch(r"/_apis/wit/workitems/(\d+)", path)
        if m and method == "PATCH":
            return self.update(int(m.group(1)), body, req)
        m = re.fullmatch(r"/_apis/wit/workItems/(\d+)/comments", path)
        if m:
            wid = int(m.group(1))
            if wid not in self.items:
                return self.error(404, "work item not found")
            if "preview" not in q["api-version"]:
                return self.error(400, "the comments API needs a preview api-version")
            if method == "POST":
                c = {"workItemId": wid, "id": 700 + sum(len(v) for v in self.comments.values()) + 1,
                     "text": body["text"], "format": q.get("format")}
                self.comments.setdefault(wid, []).append(c)
                self.items[wid]["rev"] += 1
                return self.ok(c)
            cs = self.comments.get(wid, [])
            return self.ok({"totalCount": len(cs), "count": len(cs), "comments": cs})
        if path == "/_apis/test/runs" and method == "POST":
            return self.create_run(body)
        if path == "/_apis/test/runs":
            runs = [r for r in self.runs.values() if int(q.get("planId") or 12) == r["plan"]]
            skip, top = int(q.get("$skip") or 0), int(q.get("$top") or 100)
            return self.ok({"count": len(runs), "value": [{k: v for k, v in r.items() if k != "results"}
                                                          for r in runs[skip:skip + top]]})
        m = re.fullmatch(r"/_apis/test/runs/(\d+)", path)
        if m and method == "PATCH":
            run = self.runs.get(int(m.group(1)))
            if run is None:
                return self.error(404, "run not found")
            run.update({k: v for k, v in body.items() if k in ("state", "comment", "name")})
            return self.ok({k: v for k, v in run.items() if k != "results"})
        m = re.fullmatch(r"/_apis/test/Runs/(\d+)/results", path)
        if m:
            return self.results(int(m.group(1)), method, q, body)
        m = re.fullmatch(r"/_apis/test/Runs/(\d+)/Results/(\d+)/attachments", path)
        if m:
            rid = int(m.group(2))
            if method == "POST":
                a = {"id": len(self.attachments.get(rid, [])) + 1, "fileName": body["fileName"],
                     "stream": body["stream"], "comment": body.get("comment")}
                self.attachments.setdefault(rid, []).append(a)
                return self.ok({"id": a["id"], "url": f"{ORG}/att/{a['id']}"})
            return self.ok({"count": 0, "value": [{"id": a["id"], "fileName": a["fileName"]}
                                                  for a in self.attachments.get(rid, [])]})
        return self.error(404, f"no route {method} {path}")

    # -- work items ------------------------------------------------------------------------------------------------

    def _apply_ops(self, wi, ops, type_name):
        for op in ops:
            if op["op"] == "test":
                continue
            if op["path"] == "/relations/-":
                wi["relations"].append(op["value"])
                continue
            ref = op["path"][len("/fields/"):]
            if ref not in TYPE_FIELDS.get(type_name, []):
                return f"TF51535: Cannot find field {ref}."
            wi["fields"][ref] = op["value"]
        return None

    def create(self, type_name, ops, req):
        if req.headers.get("Content-Type") != "application/json-patch+json":
            return self.error(415, "json-patch expected")
        if type_name not in TYPE_FIELDS or (type_name == "Bug" and self.process == "Basic"):
            return self.error(404, f"work item type {type_name} does not exist")
        wi = self.new_item(type_name, {})
        problem = self._apply_ops(wi, ops, type_name)
        if problem or not wi["fields"].get("System.Title"):
            del self.items[wi["id"]]
            return self.error(400, problem or "TF401320: Rule Error for field Title")
        return self.ok({"id": wi["id"], "rev": 1, "fields": wi["fields"], "url": f"{ORG}/_apis/wit/workItems/{wi['id']}"})

    def update(self, wid, ops, req):
        wi = self.items.get(wid)
        if wi is None:
            return self.error(404, "work item not found")
        for op in ops:
            if op["op"] == "test" and op["path"] == "/rev" and int(op["value"]) != wi["rev"]:
                return self.error(412, f"TF26071: the work item was changed (rev {wi['rev']}, not {op['value']})")
        problem = self._apply_ops(wi, ops, wi["fields"]["System.WorkItemType"])
        if problem:
            return self.error(400, problem)
        wi["rev"] += 1
        return self.ok({"id": wid, "rev": wi["rev"], "fields": wi["fields"]})

    # -- test plans ------------------------------------------------------------------------------------------------

    def suite(self, sid, what, method, q, body):
        s = self.suites.get(sid)
        if s is None:
            return self.error(404, "suite not found")
        if what == "TestCase" and method == "POST":
            if s["suiteType"] != "staticTestSuite":
                return self.error(400, "cases can only be added to a static suite")
            out = []
            for entry in body:
                wid = int(entry["workItem"]["id"])
                if wid not in self.items:
                    return self.error(404, f"work item {wid} not found")
                self.add_to_suite(wid, sid)
                out.append(self._suite_case(sid, wid))
            return self.ok({"count": len(out), "value": out})
        if what == "TestCase":
            wanted = {int(x) for x in q["testIds"].split(",")} if q.get("testIds") else None
            return self.paged([self._suite_case(sid, w) for w in s["cases"] if wanted is None or w in wanted], q)
        wanted = {int(x) for x in q["testCaseId"].split(",")} if q.get("testCaseId") else None
        pts = [p for p in self.points.values() if p["suite"] == sid and (wanted is None or p["case"] in wanted)]
        return self.paged([{"id": p["id"], "configuration": {"id": p["config"], "name": self.configs[p["config"]]},
                            "testCaseReference": {"id": p["case"], "name": self.items[p["case"]]["fields"]
                                                  ["System.Title"]},
                            "testSuite": {"id": sid}, "results": {"outcome": p["outcome"]}} for p in pts], q)

    def _suite_case(self, sid, wid):
        return {"workItem": {"id": wid, "name": self.items[wid]["fields"]["System.Title"]},
                "pointAssignments": [{"id": p["id"], "configurationId": p["config"],
                                      "configurationName": self.configs[p["config"]]}
                                     for p in self.points.values() if p["suite"] == sid and p["case"] == wid]}

    # -- runs and results ------------------------------------------------------------------------------------------

    def create_run(self, body):
        if body.get("plan", {}).get("id") != "12":
            return self.error(400, "plan not found")
        self.next_run += 1
        results = {}
        for pid in body.get("pointIds") or []:
            p = self.points.get(int(pid))
            if p is None:
                return self.error(400, f"test point {pid} not found")
            self.next_result += 1
            results[self.next_result] = {"id": self.next_result, "testPoint": {"id": str(pid)},
                                         "testCase": {"id": str(p["case"])}, "outcome": "Unspecified",
                                         "state": "Pending", "associatedBugs": []}
        self.runs[self.next_run] = {"id": self.next_run, "name": body["name"], "comment": body.get("comment"),
                                    "state": "InProgress", "plan": 12, "isAutomated": body.get("automated", False),
                                    "results": results}
        return self.ok({k: v for k, v in self.runs[self.next_run].items() if k != "results"})

    def results(self, run_id, method, q, body):
        run = self.runs.get(run_id)
        if run is None:
            return self.error(404, "run not found")
        if method == "PATCH":
            for entry in body:
                r = run["results"].get(int(entry["id"]))
                if r is None:
                    return self.error(404, f"result {entry['id']} not found")
                wid = int(r["testCase"]["id"])
                outcome = self.automation.get(wid, entry.get("outcome", r["outcome"]))
                r.update({k: v for k, v in entry.items() if k in ("state", "comment")})
                r["outcome"] = outcome
                for b in entry.get("associatedBugs") or []:
                    if str(b["id"]) not in [str(x["id"]) for x in r["associatedBugs"]]:
                        bug = self.items.get(int(b["id"]))
                        if bug is None:
                            return self.error(400, f"bug {b['id']} not found")
                        r["associatedBugs"].append({"id": str(b["id"])})
                        bug["rev"] += 1   # the result's link is a new revision of the bug
                self.points[int(r["testPoint"]["id"])]["outcome"] = _camel(outcome)
            return self.ok({"count": len(body), "value": [{"id": e["id"]} for e in body]})
        skip, top = int(q.get("$skip") or 0), int(q.get("$top") or 1000)
        details = q.get("detailsToInclude") == "WorkItems"
        out = []
        for r in list(run["results"].values())[skip:skip + top]:
            entry = {k: v for k, v in r.items() if k != "associatedBugs"}
            if details:
                entry["associatedBugs"] = r["associatedBugs"]
            out.append(entry)
        return self.ok({"count": len(out), "value": out})
