"""
The Azure DevOps Services REST calls of the back-end (technique-azure-devops.md section 0; api-version 7.1, fixed in
the configuration; the endpoints confirmed in plan 10.3 are listed in the plan's note of 10.3).

- Authentication: a personal access token as basic authentication with an empty user (base64 of ":<PAT>").
- Every path is project scoped (`/<project>/_apis/...`) except the project and the process; `api-version` always last
  in the query. The work item comments API exists only as a preview (`7.1-preview.4`).
- Lists that page with the `x-ms-continuationtoken` response header (suites, suite test cases, test points,
  configurations) are followed to the end; test runs and results page with `$skip`/`$top`.
"""

from __future__ import annotations

import base64
import urllib.parse

from adapters.tracker.errors import ConfigError, Conflict, HttpError
from adapters.tracker.http import paginate_offset, paginate_token

BATCH = 200          # work items per workitemsbatch (the documented maximum)
RESULTS_PAGE = 1000  # test results per page without details (200 with details)
RUNS_PAGE = 100
WIQL_TOP = 5000      # the guard of technique-jira-xray.md 2.7 (a WIQL answers at most 20000)
JSON_PATCH = "application/json-patch+json"
CONTINUATION = "x-ms-continuationtoken"


def chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def wiql_string(value) -> str:
    """A WIQL string literal: single quotes, a quote doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def comments_version(version: str) -> str:
    return version if "-preview" in version else f"{version}-preview.4"


class AdoApi:
    """The client of one run and the reads each command shares (cached per run)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = ctx.config.cfg
        self.version = str(self.cfg.get("api_version") or "7.1")
        self.project_name = self.cfg["project"]
        self.p = "/" + urllib.parse.quote(self.project_name, safe="")
        self._client = None
        self._fields: dict | None = None
        self._type_fields: dict[str, dict] = {}
        self._suites: list | None = None

    # -- client and paths -------------------------------------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            token = base64.b64encode(f":{self.ctx.secret('pat_var')}".encode()).decode("ascii")
            self.ctx.redactor.add("ADO_BASIC_AUTH", token)
            self._client = self.ctx.client(self.cfg["organization_url"],
                                           auth=lambda: {"Authorization": f"Basic {token}"})
        return self._client

    def path(self, rel: str, version: str | None = None, **params) -> str:
        """`rel` with its query: the given parameters in order (None dropped), then api-version."""
        query = [(k, v) for k, v in params.items() if v is not None]
        query.append(("api-version", version or self.version))
        return rel + "?" + urllib.parse.urlencode(query, safe="$,")

    def work_item_url(self, wid) -> str:
        return f"{self.cfg['organization_url'].rstrip('/')}/_apis/wit/workItems/{wid}"

    def _get(self, rel: str, **params):
        return self.client.get(self.path(rel, **params)).json()

    def _token_list(self, rel: str, **params) -> list:
        """A list that pages with the x-ms-continuationtoken header."""

        def fetch(token):
            resp = self.client.get(self.path(rel, **params, continuationToken=token))
            data = resp.json()
            items = data.get("value") if isinstance(data, dict) else data
            return items or [], resp.header(CONTINUATION)

        return paginate_token(fetch)

    # -- project, process, types and fields -------------------------------------------------------------------------

    def project(self) -> dict:
        try:
            return self._get(f"/_apis/projects/{urllib.parse.quote(self.project_name, safe='')}",
                             includeCapabilities="true") or {}
        except HttpError as e:
            if e.status == 404:
                raise ConfigError(f"azure_devops.project {self.project_name!r}: project not found, or the token cannot "
                                  "read it") from None
            raise

    def process(self, type_id: str) -> dict:
        return self._get(f"/_apis/work/processes/{type_id}") or {}

    def work_item_types(self) -> list[str]:
        data = self._get(f"{self.p}/_apis/wit/workitemtypes") or {}
        return [t.get("name") for t in data.get("value") or []]

    def fields(self) -> dict[str, dict]:
        """referenceName -> field (with its type: string, integer, html, treePath, picklistString...)."""
        if self._fields is None:
            data = self._get(f"{self.p}/_apis/wit/fields") or {}
            self._fields = {f.get("referenceName"): f for f in data.get("value") or []}
        return self._fields

    def type_fields(self, type_name: str) -> dict[str, dict]:
        """referenceName -> the field on the type (alwaysRequired, defaultValue, allowedValues)."""
        if type_name not in self._type_fields:
            try:
                data = self._get(f"{self.p}/_apis/wit/workitemtypes/{urllib.parse.quote(type_name, safe='')}/fields",
                                 **{"$expand": "All"}) or {}
            except HttpError as e:
                if e.status == 404:
                    raise ConfigError(f"work item type {type_name!r} not in project {self.project_name!r} (run "
                                      "check)") from None
                raise
            self._type_fields[type_name] = {f.get("referenceName"): f for f in data.get("value") or []}
        return self._type_fields[type_name]

    # -- test plans ---------------------------------------------------------------------------------------------------

    def plan(self, plan_id: int) -> dict:
        return self._get(f"{self.p}/_apis/testplan/plans/{plan_id}") or {}

    def suites(self, plan_id: int) -> list[dict]:
        if self._suites is None:
            self._suites = self._token_list(f"{self.p}/_apis/testplan/Plans/{plan_id}/suites")
        return self._suites

    def configurations(self) -> list[dict]:
        return self._token_list(f"{self.p}/_apis/testplan/configurations")

    def suite_cases(self, plan_id: int, suite_id: int, test_ids: list | None = None) -> list[dict]:
        return self._token_list(f"{self.p}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase",
                                testIds=",".join(str(i) for i in test_ids) if test_ids else None)

    def points(self, plan_id: int, suite_id: int, case_ids: list | None = None) -> list[dict]:
        return self._token_list(f"{self.p}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestPoint",
                                testCaseId=",".join(str(i) for i in case_ids) if case_ids else None)

    def add_to_suite(self, plan_id: int, suite_id: int, ids: list[int]) -> list[dict]:
        body = [{"workItem": {"id": int(i)}} for i in ids]
        data = self.client.post(self.path(f"{self.p}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase"),
                                json_body=body).json()
        return (data.get("value") if isinstance(data, dict) else data) or []

    # -- work items ---------------------------------------------------------------------------------------------------

    def wiql(self, query: str, top: int = WIQL_TOP) -> list[int]:
        """The ids a flat WIQL returns (a read sent with POST)."""
        try:
            data = self.client.post(self.path(f"{self.p}/_apis/wit/wiql", **{"$top": top}), json_body={"query": query},
                                    read=True).json() or {}
        except HttpError as e:
            if e.status == 400:
                raise Conflict(f"Azure DevOps refused the WIQL ({_message(e.body)})") from None
            raise
        return [int(w["id"]) for w in data.get("workItems") or []]

    def batch(self, ids: list, fields: list[str] | None = None, expand: str | None = None) -> dict[int, dict]:
        """id -> work item, in groups of 200; ids that do not exist are left out (errorPolicy Omit)."""
        out: dict[int, dict] = {}
        for chunk in chunks([int(i) for i in ids], BATCH):
            body = {"ids": chunk, "errorPolicy": "Omit"}
            if fields:
                body["fields"] = fields
            if expand:
                body["$expand"] = expand
            data = self.client.post(self.path(f"{self.p}/_apis/wit/workitemsbatch"), json_body=body,
                                    read=True).json() or {}
            for wi in data.get("value") or []:
                if wi:
                    out[int(wi["id"])] = wi
        return out

    def create_work_item(self, type_name: str, ops: list[dict]) -> dict:
        path = self.path(f"{self.p}/_apis/wit/workitems/{urllib.parse.quote('$' + type_name, safe='$')}")
        return self.client.post(path, json_body=ops, headers={"Content-Type": JSON_PATCH}).json() or {}

    def update_work_item(self, wid: int, ops: list[dict]) -> dict:
        """PATCH with a `test` on /rev first: a concurrent edit is a 412 (exit 2). Not retried: after a timeout, the
        next run reads the work item again."""
        return self.client.request("PATCH", self.path(f"{self.p}/_apis/wit/workitems/{wid}"), json_body=ops,
                                   headers={"Content-Type": JSON_PATCH}).json() or {}

    def comments(self, wid: int) -> list[dict]:
        def fetch(token):
            data = self.client.get(self.path(f"{self.p}/_apis/wit/workItems/{wid}/comments",
                                             comments_version(self.version), continuationToken=token)).json() or {}
            return data.get("comments") or [], data.get("continuationToken")

        return paginate_token(fetch)

    def add_comment(self, wid: int, html_text: str) -> dict:
        return self.client.post(self.path(f"{self.p}/_apis/wit/workItems/{wid}/comments",
                                          comments_version(self.version), format="html"),
                                json_body={"text": html_text}).json() or {}

    # -- test runs and results ----------------------------------------------------------------------------------------

    def create_run(self, body: dict) -> dict:
        return self.client.post(self.path(f"{self.p}/_apis/test/runs"), json_body=body).json() or {}

    def runs(self, plan_id: int) -> list[dict]:
        """The manual runs of the plan, with their details (the marker search of an uncertain run create)."""

        def fetch(skip, top):
            data = self._get(f"{self.p}/_apis/test/runs", planId=plan_id, automated="false",
                             includeRunDetails="true", **{"$skip": skip, "$top": top}) or {}
            return data.get("value") or [], None

        return paginate_offset(fetch, RUNS_PAGE)

    def results(self, run_id: int, work_items: bool = False) -> list[dict]:
        page = 200 if work_items else RESULTS_PAGE

        def fetch(skip, top):
            data = self._get(f"{self.p}/_apis/test/Runs/{run_id}/results",
                             detailsToInclude="WorkItems" if work_items else None,
                             **{"$skip": skip, "$top": top}) or {}
            return data.get("value") or [], None

        return paginate_offset(fetch, page)

    def update_results(self, run_id: int, results: list[dict]) -> list[dict]:
        # the same outcomes and bugs again give the same results: retried as an idempotent write
        data = self.client.request("PATCH", self.path(f"{self.p}/_apis/test/Runs/{run_id}/results"),
                                   json_body=results, idempotent=True).json()
        return (data.get("value") if isinstance(data, dict) else data) or []

    def update_run(self, run_id: int, body: dict) -> dict:
        return self.client.request("PATCH", self.path(f"{self.p}/_apis/test/runs/{run_id}"), json_body=body,
                                   idempotent=True).json() or {}

    def attachments(self, run_id: int, result_id: int) -> list[dict]:
        data = self._get(f"{self.p}/_apis/test/Runs/{run_id}/Results/{result_id}/attachments") or {}
        return (data.get("value") if isinstance(data, dict) else data) or []

    def add_attachment(self, run_id: int, result_id: int, body: dict) -> dict:
        return self.client.post(self.path(f"{self.p}/_apis/test/Runs/{run_id}/Results/{result_id}/attachments"),
                                json_body=body).json() or {}


def _message(body: str) -> str:
    import json
    try:
        doc = json.loads(body)
    except (TypeError, ValueError):
        return (body or "")[:300]
    return str(doc.get("message") or doc)[:300]
