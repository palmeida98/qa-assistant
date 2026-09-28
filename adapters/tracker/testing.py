"""
Test kit of the tracker adapters (plan 10.1): a fake transport for the HTTP client. Used by the unit tests and, in
10.2/10.3, by the goldens with recorded fictional responses (technique-jira-xray.md section 10). No network.

    fake = FakeTransport([
        {"request": {"method": "GET", "path": "/rest/api/3/myself"}, "status": 200, "json": {...}},
        {"status": 429, "headers": {"Retry-After": "2"}},
        {"raise": "timeout"},
    ], fail_at=None)

Each response, in order, may say which request it answers (method and path, the query included when given); a
request that does not match is an AssertionError, so a golden fails at the first unexpected request. `fail_at=n`
injects a failure (no answer) at the n-th request. `requests` keeps every Request made, `writes()` those that are not
reads.
"""

from __future__ import annotations

import json

from .http import Request, Response, TransportError, TransportTimeout


class FakeTransport:
    def __init__(self, responses: list[dict] | None = None, fail_at: int | None = None):
        self.responses = list(responses or [])
        self.fail_at = fail_at
        self.requests: list[Request] = []

    def __call__(self, req: Request) -> Response:
        self.requests.append(req)
        n = len(self.requests)
        if self.fail_at is not None and n == self.fail_at:
            raise TransportTimeout(f"fault injected at request {n}")
        if not self.responses:
            raise AssertionError(f"unexpected request {n}: {req.method} {req.path} (no response left)")
        r = self.responses.pop(0)
        want = r.get("request")
        if want:
            if want.get("method", req.method).upper() != req.method:
                raise AssertionError(f"request {n}: expected {want['method']} {want.get('path')}, got "
                                     f"{req.method} {req.path}")
            path = want.get("path")
            if path is not None and path != (req.path if "?" in path else req.path.split("?")[0]):
                raise AssertionError(f"request {n}: expected {req.method} {path}, got {req.method} {req.path}")
        if r.get("raise") == "timeout":
            raise TransportTimeout("fake timeout")
        if r.get("raise"):
            raise TransportError(str(r["raise"]))
        if "json" in r:
            body = json.dumps(r["json"]).encode("utf-8")
        else:
            body = str(r.get("body", "")).encode("utf-8")
        return Response(int(r.get("status", 200)), dict(r.get("headers") or {}), body)

    def writes(self) -> list[Request]:
        """The requests that change the tracker (not GET/HEAD, not marked as a read)."""
        return [r for r in self.requests if r.method not in ("GET", "HEAD") and not r.read_only]

    @property
    def left(self) -> int:
        return len(self.responses)
