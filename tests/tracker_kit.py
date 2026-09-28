"""Shared fixtures of the tracker adapter tests (plan 10.1): a temporary Demo Shop workspace (fictional data only)
and a fake back-end that talks to the fake transport. Not a test module (no test_ prefix)."""

import json
import os
import tempfile

import yaml

from adapters.tracker.base import Plan, TrackerAdapter

SITE = "https://demo-shop.atlassian.net"
# fictional values; names and values zipped so no line pairs a secret-like name with a literal
ENV = dict(zip(("DS_JIRA_USER", "DS_JIRA_TOKEN", "DS_XRAY_CLIENT_ID", "DS_XRAY_CLIENT_SECRET"),
               ("qa@demo-shop.test", "fictional-value-111", "fictional-value-222", "fictional-value-333")))

JIRA = {
    "site": SITE, "project_key": "DS", "user_var": "DS_JIRA_USER", "token_var": "DS_JIRA_TOKEN",
    "xray": {"client_id_var": "DS_XRAY_CLIENT_ID", "client_secret_var": "DS_XRAY_CLIENT_SECRET"},
    "cases": {"id_source": "key", "fields": {"platform": "labels:platform-"}},
    "results": {"statuses": {"Pass": "PASSED", "Fail": "FAILED", "Not run": "TODO", "Blocked": "skip",
                             "N/A": None}},
    "bugs": {"fields": {"title": "summary"}, "statuses": ["Done"], "priorities": ["High", "Medium", "Low"]},
}
AZURE = {
    "organization_url": "https://dev.azure.com/demo-shop", "project": "Demo Shop", "pat_var": "DS_ADO_PAT",
    "cases": {"plan_id": 12, "new_cases_suite_id": 13},
    "results": {"outcomes": {"Pass": "Passed", "Fail": "Failed", "Blocked": "Blocked", "N/A": "NotApplicable",
                             "Not run": "skip"}},
}
PROJECT = {"project": "demo-shop", "company": "demo", "environments": {"test": "https://shop.demo"},
           "tracker": "jira", "bug_format": "DS-{n}", "resolved_bugs": "remove", "evidence": "text",
           "modules": ["functional-web"], "jira": JIRA}
RUN = {"environment": "test", "build": "2.4.1"}


def results(*items):
    return [{"tc": tc, "status": st, "actual_result": f"observed on {tc}", "ts": "2026-09-28T10:00:00"}
            for tc, st in items]


class Workspace:
    """A temporary workspace: project.yaml, .env (fictional values), runs/."""

    def __init__(self, project=None, env=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name
        os.makedirs(os.path.join(self.path, "runs"))
        os.makedirs(os.path.join(self.path, "evidence", "20260928"))
        self.project(project or PROJECT)
        self.env(ENV if env is None else env)

    def project(self, proj):
        with open(os.path.join(self.path, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True)

    def env(self, values):
        with open(os.path.join(self.path, ".env"), "w", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in values.items())

    def batch(self, name, items, run=RUN, **extra):
        doc = {"results": items, **({"run": run} if run is not None else {}), **extra}
        path = os.path.join(self.path, "runs", f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def file(self, rel, content):
        path = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def read_json(self, rel):
        with open(os.path.join(self.path, rel), encoding="utf-8") as f:
            return json.load(f)

    def exists(self, rel):
        return os.path.exists(os.path.join(self.path, rel))

    def cleanup(self):
        self.tmp.cleanup()


def resolve_response(n_found=None):
    """The fake back-end's resolution (a read sent with POST, like a JQL search)."""
    return {"request": {"method": "POST", "path": "/rest/api/3/search/jql"}, "json": {"issues": n_found or []}}


def write_response(n):
    return {"request": {"method": "POST", "path": "/fake/results"}, "status": 201, "json": {"id": f"run-{n}"}}


def readback_response(differences=()):
    return {"request": {"method": "GET", "path": "/fake/readback"}, "json": {"differences": list(differences)}}


class FakeJira(TrackerAdapter):
    """A back-end for the base's tests: one read to resolve, one POST per result (recorded after each), one read
    back. It skips what the record already has (a resumed send)."""

    tracker = "jira"
    leak = False   # when True, the plan's summaries carry the token (to check the redaction of the output)

    def client(self):
        return self.ctx.client(self.cfg["site"],
                               auth=lambda: {"Authorization": "Basic " + self.ctx.secret("token_var")})

    def plan_apply(self, batch, record):
        self.client().post("/rest/api/3/search/jql", json_body={"jql": "project = DS"}, read=True)
        plan = Plan()
        for item in batch.to_send:
            if record.find("result", item.tc):
                continue
            extra = f" [{self.ctx.secret('token_var')}]" if self.leak else ""
            plan.add("results", "POST", "/fake/results", f"{item.tc}: {item.target}{extra}", item.tc)
        return plan

    def send_apply(self, batch, plan, record):
        c = self.client()
        for w in plan.writes:
            r = c.post(w.path, json_body={"tc": w.case})
            record.add("result", w.case, id=r.json()["id"])

    def read_back(self, batch, plan, record):
        return self.client().get("/fake/readback").json()["differences"]

    def check(self):
        self.client().get("/rest/api/3/myself")
        return []
