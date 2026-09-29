"""
The Jira Cloud REST v3 and Xray Cloud API v2 calls of the Jira back-end (technique-jira-xray.md section 0; the
endpoints confirmed in plan 10.2 are listed in the plan's note of 10.2).

- Jira: basic authentication (account e-mail + API token); JQL through the enhanced search `POST
  /rest/api/3/search/jql` with `nextPageToken`; the project, the create metadata per issue type, issues, comments,
  transitions, issue links.
- Xray: `POST /api/v2/authenticate` (client id + secret -> a token used as Bearer, renewed once after a 401), GraphQL
  `POST /api/v2/graphql`, the results imports `POST /api/v2/import/execution` and `/import/execution/multipart`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse

from adapters.tracker.errors import (
    AuthFailed,
    ConfigError,
    Conflict,
    HttpError,
    InvalidInput,
)
from adapters.tracker.http import paginate_offset, paginate_token

PAGE = 100          # Jira search page, Xray GraphQL page (the maximum of getTests and of the test runs)
XRAY_AUTH = "/api/v2/authenticate"
GRAPHQL = "/api/v2/graphql"
IMPORT = "/api/v2/import/execution"
IMPORT_MULTIPART = "/api/v2/import/execution/multipart"
SEARCH = "/rest/api/3/search/jql"

# GraphQL (Xray Cloud): variables, never values spliced into the query
Q_STATUSES = "query($projectId: String) { getStatuses(projectId: $projectId) { name final } }"
Q_TESTS = ("query($issueIds: [String], $limit: Int!, $start: Int) { getTests(issueIds: $issueIds, limit: $limit, "
           "start: $start) { total start limit results { issueId testType { name kind } "
           "steps { action data result } preconditions(limit: 100) { results { issueId definition "
           "jira(fields: [\"key\"]) } } } } }")
Q_TESTS_PLAN = ("query($issueIds: [String], $limit: Int!, $start: Int, $plan: String) { getTests(issueIds: "
                "$issueIds, limit: $limit, start: $start) { total start limit results { issueId testType { name kind "
                "} steps { action data result } preconditions(limit: 100) { results { issueId definition "
                "jira(fields: [\"key\"]) } } status(testPlan: $plan) { name } } } }")
Q_RUNS = ("query($issueId: String, $limit: Int!, $start: Int) { getTestExecution(issueId: $issueId) { issueId "
          "testRuns(limit: $limit, start: $start) { total start limit results { status { name } defects "
          "test { issueId } } } } }")
M_CREATE_TEST = ("mutation($testType: UpdateTestTypeInput, $steps: [CreateStepInput], $folderPath: String, "
                 "$jira: JSON!) { createTest(testType: $testType, steps: $steps, folderPath: $folderPath, "
                 "jira: $jira) { test { issueId jira(fields: [\"key\"]) } warnings } }")
M_ADD_TO_PLAN = ("mutation($issueId: String!, $testIssueIds: [String]!) { addTestsToTestPlan(issueId: $issueId, "
                 "testIssueIds: $testIssueIds) { addedTests warning } }")


class GraphQLError(InvalidInput):
    """Xray answered 200 with `errors`: the query or the input was refused (exit 4)."""


def jql_string(value) -> str:
    """A JQL string literal."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def jql_phrase(value) -> str:
    """A JQL text-search literal of one exact phrase: "\\"words\\""."""
    inner = '"' + str(value).replace("\\", " ").replace('"', " ") + '"'
    return jql_string(inner)


def chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def multipart(parts: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """multipart/form-data of (name, filename, content, content type) parts; the boundary is derived from the content,
    so the same request is byte for byte the same (golden)."""
    digest = hashlib.sha256(b"".join(p[2] for p in parts)).hexdigest()[:24]
    boundary = f"qa-assistant-{digest}"
    out = b""
    for name, filename, content, ctype in parts:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


class JiraApi:
    """The two clients (Jira and Xray) of one run, the Xray token, and the reads each command shares."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = ctx.config.cfg
        self._jira = None
        self._xray = None
        self._token: str | None = None
        self._project: dict | None = None
        self._meta: dict[str, dict] = {}

    # -- clients ----------------------------------------------------------------------------------------------------

    @property
    def jira(self):
        if self._jira is None:
            pair = f"{self.ctx.secret('user_var')}:{self.ctx.secret('token_var')}"
            basic = base64.b64encode(pair.encode("utf-8")).decode("ascii")
            self.ctx.redactor.add("JIRA_BASIC_AUTH", basic)
            self._jira = self.ctx.client(self.cfg["site"], auth=lambda: {"Authorization": f"Basic {basic}"})
        return self._jira

    @property
    def xray_base(self) -> str:
        return self.cfg["xray"].get("base_url") or "https://xray.cloud.getxray.app"

    @property
    def xray(self):
        if self._xray is None:
            self._xray = self.ctx.client(self.xray_base, auth=self._bearer, reauth=self.authenticate)
        return self._xray

    def _bearer(self) -> dict:
        if self._token is None:
            self.authenticate()
        return {"Authorization": f"Bearer {self._token}"}

    def authenticate(self) -> None:
        """POST /api/v2/authenticate: the answer is the token as a JSON string. Also the renewal after a 401."""
        client = self.ctx.client(self.xray_base)
        body = {"client_id": self.ctx.secret("xray.client_id_var"),
                "client_secret": self.ctx.secret("xray.client_secret_var")}
        resp = client.post(XRAY_AUTH, json_body=body, read=True)   # changes nothing in the tracker
        try:
            token = resp.json()
        except ValueError:
            token = resp.text.strip().strip('"')
        if not isinstance(token, str) or not token:
            raise AuthFailed("Xray: the authentication gave no token (check the Xray API key's variables in .env)")
        self._token = token
        self.ctx.redactor.add("XRAY_TOKEN", token)

    # -- Jira -------------------------------------------------------------------------------------------------------

    def search(self, jql: str, fields: list[str], max_results: int = PAGE, first_page_only: bool = False) -> list:
        """Every issue of a JQL (nextPageToken followed to the end, with the guard of section 2.7)."""

        def fetch(token):
            body = {"jql": jql, "fields": fields, "maxResults": max_results}
            if token:
                body["nextPageToken"] = token
            data = self.jira.post(SEARCH, json_body=body, read=True).json() or {}
            nxt = None if (data.get("isLast", True) or first_page_only) else data.get("nextPageToken")
            return data.get("issues") or [], nxt

        return paginate_token(fetch)

    def search_or_conflict(self, jql: str, fields: list[str], what: str) -> list:
        """A search whose 400 means the input names something Jira does not have (e.g. a key that does not
        exist): a conflict (exit 2), not an invalid input."""
        try:
            return self.search(jql, fields)
        except HttpError as e:
            if e.status == 400:
                raise Conflict(f"{what}: Jira refused the query ({_messages(e.body)})") from None
            raise

    def project(self) -> dict:
        if self._project is None:
            key = self.cfg["project_key"]
            try:
                self._project = self.jira.get(f"/rest/api/3/project/{urllib.parse.quote(key)}").json() or {}
            except HttpError as e:
                if e.status == 404:
                    raise ConfigError(f"jira.project_key {key!r}: project not found, or the account cannot browse "
                                      "it") from None
                raise
        return self._project

    def issue_type(self, name: str) -> dict | None:
        for t in self.project().get("issueTypes") or []:
            if str(t.get("name", "")).lower() == str(name).lower():
                return t
        return None

    def create_meta(self, type_name: str) -> dict[str, dict]:
        """fieldId -> metadata (schema, required, allowedValues) of the issue type's create screen."""
        if type_name not in self._meta:
            t = self.issue_type(type_name)
            if t is None:
                raise ConfigError(f"issue type {type_name!r} not in project {self.cfg['project_key']} (run check)")
            path = (f"/rest/api/3/issue/createmeta/{urllib.parse.quote(self.cfg['project_key'])}/issuetypes/"
                    f"{urllib.parse.quote(str(t['id']))}")

            fields, start = [], 0
            while True:   # Jira may give fewer than maxResults per page: the total decides
                data = self.jira.get(path, params={"startAt": start, "maxResults": 200}).json() or {}
                page = data.get("fields") or data.get("results") or []
                fields += page
                start += len(page)
                if not page or data.get("total") is None or start >= int(data["total"]):
                    break
            self._meta[type_name] = {f.get("fieldId") or f.get("key"): f for f in fields}
        return self._meta[type_name]

    def create_issue(self, fields: dict) -> dict:
        return self.jira.post("/rest/api/3/issue", json_body={"fields": fields}).json() or {}

    def update_issue(self, key: str, fields: dict) -> None:
        self.jira.request("PUT", f"/rest/api/3/issue/{urllib.parse.quote(key)}", json_body={"fields": fields})

    def comments(self, key: str) -> list:
        data = self.jira.get(f"/rest/api/3/issue/{urllib.parse.quote(key)}/comment",
                             params={"maxResults": 100, "orderBy": "-created"}).json() or {}
        return data.get("comments") or []

    def add_comment(self, key: str, body: dict) -> dict:
        return self.jira.post(f"/rest/api/3/issue/{urllib.parse.quote(key)}/comment", json_body={"body": body}).json()

    def transitions(self, key: str) -> list:
        data = self.jira.get(f"/rest/api/3/issue/{urllib.parse.quote(key)}/transitions").json() or {}
        return data.get("transitions") or []

    def transition(self, key: str, transition_id: str) -> None:
        # the same transition twice fails harmlessly (not available from the new status): not retried
        self.jira.post(f"/rest/api/3/issue/{urllib.parse.quote(key)}/transitions",
                       json_body={"transition": {"id": str(transition_id)}})

    def link_types(self) -> list[str]:
        data = self.jira.get("/rest/api/3/issueLinkType").json() or {}
        return [t.get("name") for t in data.get("issueLinkTypes") or []]

    def link(self, link_type: str, inward: str, outward: str) -> None:
        self.jira.post("/rest/api/3/issueLink", json_body={"type": {"name": link_type}, "inwardIssue": {"key": inward},
                                                           "outwardIssue": {"key": outward}})

    # -- Xray -------------------------------------------------------------------------------------------------------

    def gql(self, query: str, variables: dict | None = None, mode: str = "read") -> dict:
        """mode: read (retried), idempotent (retried), create (never retried blindly)."""
        body = {"query": query, **({"variables": variables} if variables else {})}
        resp = self.xray.post(GRAPHQL, json_body=body, read=mode == "read", idempotent=mode == "idempotent")
        doc = resp.json() or {}
        if doc.get("errors"):
            msgs = "; ".join(str(e.get("message", e)) for e in doc["errors"])[:600]
            raise GraphQLError(f"Xray GraphQL refused the request: {self.ctx.redactor.text(msgs)}")
        return doc.get("data") or {}

    def statuses(self, project_id: str) -> list[str]:
        data = self.gql(Q_STATUSES, {"projectId": str(project_id)})
        return [s.get("name") for s in data.get("getStatuses") or []]

    def tests(self, issue_ids: list[str], plan_id: str | None) -> dict[str, dict]:
        """issueId -> Xray test (type, steps, preconditions, and the status in the test plan). getTests by issue id,
        in pages of 100: getTests with a JQL refuses a JQL matching more than 100 issues."""
        out: dict[str, dict] = {}
        for chunk in chunks(list(issue_ids), PAGE):
            variables = {"issueIds": chunk, "limit": PAGE, "start": 0}
            if plan_id:
                variables["plan"] = str(plan_id)
            data = self.gql(Q_TESTS_PLAN if plan_id else Q_TESTS, variables)
            for t in (data.get("getTests") or {}).get("results") or []:
                out[str(t.get("issueId"))] = t
        return out

    def test_runs(self, execution_id: str) -> list[dict]:
        def fetch(start, limit):
            data = self.gql(Q_RUNS, {"issueId": str(execution_id), "limit": limit, "start": start})
            runs = ((data.get("getTestExecution") or {}).get("testRuns") or {})
            return runs.get("results") or [], runs.get("total")

        return paginate_offset(fetch, PAGE)

    def import_new(self, results: dict, info: dict) -> dict:
        """The first chunk: creates the Test Execution (the multipart import, whose `info` part takes the Jira fields,
        the batch marker label among them). A create: never retried blindly."""
        body, ctype = multipart([
            ("results", "results.json", json.dumps(results, ensure_ascii=False).encode("utf-8"), "application/json"),
            ("info", "info.json", json.dumps(info, ensure_ascii=False).encode("utf-8"), "application/json"),
        ])
        return self.xray.post(IMPORT_MULTIPART, body=body, headers={"Content-Type": ctype}).json() or {}

    def import_into(self, results: dict) -> dict:
        """The next chunks, into the execution's key (`testExecutionKey`): importing the same results again gives the
        same test runs, so it is retried as an idempotent write."""
        return self.xray.post(IMPORT, json_body=results, idempotent=True).json() or {}

    def create_test(self, variables: dict) -> dict:
        data = self.gql(M_CREATE_TEST, variables, mode="create")
        return data.get("createTest") or {}

    def add_to_plan(self, plan_id: str, test_ids: list[str]) -> None:
        self.gql(M_ADD_TO_PLAN, {"issueId": str(plan_id), "testIssueIds": [str(i) for i in test_ids]},
                 mode="idempotent")


def _messages(body: str) -> str:
    try:
        doc = json.loads(body)
    except (TypeError, ValueError):
        return (body or "")[:300]
    msgs = list(doc.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (doc.get("errors") or {}).items()]
    return "; ".join(msgs)[:300] or (body or "")[:300]


def key_number(key: str) -> tuple:
    project, _, n = str(key).rpartition("-")
    return (project, int(n)) if n.isdigit() else (str(key), 0)
