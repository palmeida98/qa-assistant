"""
HTTP client of the tracker adapters (technique-jira-xray.md section 2.7): Python's standard library, no new
dependency.

- One transport function does every request (`urllib_transport`); the tests replace it with a fake
  (adapters/tracker/testing.py). A transport takes a `Request` and returns a `Response` (any HTTP status), or raises
  `TransportError` (no answer; `TransportTimeout` for a timeout).
- Timeouts per request (default 30 s). Retries only for 429 and 502/503/504 (and no answer), and only for reads and
  idempotent writes (GET, HEAD, PUT, DELETE, or a request marked `read=True` / `idempotent=True`, e.g. a JQL or
  GraphQL query sent with POST): `Retry-After` honoured (seconds or an HTTP date, capped at 120 s), otherwise
  exponential backoff with jitter; at most 5 attempts, then exit 3.
- A create (a POST that is not a read) is never retried blindly: without an answer it raises UncertainWrite, and the
  back-end's marker search decides whether it happened (section 2.5).
- A 401 re-authenticates once (the Xray token expires); a second 401, or a 403, is exit 3 with the reason, never
  with the token.
- Redaction (the Redactor of scripts/common.py, given the credentials and any token obtained at run time) on every
  message, and on the recordings (`--record`).
"""

from __future__ import annotations

import email.utils
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

# scripts/ (for common) is on sys.path: the package's __init__ imports .config, which puts it there, first
from common import REDACTED, Redactor

from .errors import (
    AuthFailed,
    HttpError,
    InvalidInput,
    RateLimited,
    Unavailable,
    UncertainWrite,
)

DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 5
MAX_RETRY_AFTER = 120.0
BACKOFF_BASE = 1.0
BACKOFF_CAP = 60.0
MAX_ITEMS = 5000
RETRY_STATUSES = (429, 502, 503, 504)
IDEMPOTENT_METHODS = ("GET", "HEAD", "PUT", "DELETE", "OPTIONS")


class TransportError(OSError):
    """No HTTP answer (connection refused, reset, DNS)."""


class TransportTimeout(TransportError):
    """No answer within the request's timeout."""


@dataclass
class Request:
    method: str
    url: str
    headers: dict = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = DEFAULT_TIMEOUT
    read_only: bool = False     # a read, even when sent with POST (JQL, GraphQL query, WIQL)

    @property
    def path(self) -> str:
        """Path and query, without the host (what the fake transport matches)."""
        u = urllib.parse.urlsplit(self.url)
        return u.path + (f"?{u.query}" if u.query else "")

    def json(self):
        return None if self.body is None else json.loads(self.body.decode("utf-8"))


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self):
        self.headers = {str(k).lower(): str(v) for k, v in (self.headers or {}).items()}

    def header(self, name: str, default=None):
        return self.headers.get(name.lower(), default)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else None


Transport = Callable[[Request], Response]


def urllib_transport(req: Request) -> Response:
    """The real transport: urllib, any HTTP status returned as a Response."""
    r = urllib.request.Request(req.url, data=req.body, method=req.method, headers=req.headers)
    try:
        with urllib.request.urlopen(r, timeout=req.timeout) as resp:
            return Response(resp.status, dict(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as e:
        with e:
            return Response(e.code, dict(e.headers.items()) if e.headers else {}, e.read() or b"")
    except TimeoutError as e:
        raise TransportTimeout(f"no answer in {req.timeout:g} s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise TransportTimeout(f"no answer in {req.timeout:g} s") from e
        raise TransportError(str(e.reason)) from e
    except (ConnectionError, OSError) as e:
        raise TransportError(str(e)) from e


class SecretRedactor(Redactor):
    """The Redactor of scripts/common.py that also learns secrets obtained at run time (e.g. the Xray token)."""

    def __init__(self, secrets_by_name: dict[str, str] | None = None):
        self.secrets = {k: v for k, v in (secrets_by_name or {}).items() if v}
        super().__init__(self.secrets)   # {name: value}: each value is printed as «name»

    def add(self, name: str, value: str) -> None:
        if value:
            self.secrets[name] = value
            super().__init__(self.secrets)


class Recorder:
    """`--record`: each exchange, redacted, as one JSON file in a folder (technique-jira-xray.md 10.3). Never
    `Authorization` nor a credential value; the bodies go through the same redaction as everything printed."""

    MAX_BODY = 200_000

    def __init__(self, folder: str, redactor: Redactor):
        self.folder = folder
        self.redactor = redactor
        self.count = 0
        os.makedirs(folder, exist_ok=True)

    def _body(self, data: bytes | None):
        if not data:
            return None
        return self.redactor.body(data.decode("utf-8", errors="replace"), self.MAX_BODY)

    def write(self, req: Request, resp: Response | None, error: str | None = None) -> str:
        self.count += 1
        doc = {
            "n": self.count,
            "request": {"method": req.method, "url": self.redactor.text(req.url),
                        "headers": self.redactor.headers(req.headers), "body": self._body(req.body)},
            "response": None if resp is None else {"status": resp.status,
                                                   "headers": self.redactor.headers(resp.headers),
                                                   "body": self._body(resp.body)},
            "error": self.redactor.text(error) if error else None,
        }
        path = os.path.join(self.folder, f"{self.count:04d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        return path


class HttpClient:
    """Every request of a back-end goes through `request`. `auth()` returns the authentication headers (called per
    attempt, so a renewed token is used); `reauth()` renews them after a first 401."""

    def __init__(self, transport: Transport, redactor: Redactor, base_url: str = "",
                 auth: Callable[[], dict] | None = None, reauth: Callable[[], None] | None = None,
                 timeout: float = DEFAULT_TIMEOUT, max_attempts: int = MAX_ATTEMPTS,
                 max_retry_after: float = MAX_RETRY_AFTER, sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random, clock: Callable[[], datetime] | None = None,
                 recorder: Recorder | None = None):
        self.transport = transport
        self.redactor = redactor
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.reauth = reauth
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
        self.sleep = sleep
        self.rng = rng
        self.clock = clock or (lambda: datetime.now(UTC))
        self.recorder = recorder

    # -- helpers ------------------------------------------------------------------------------------------------

    def url(self, path: str, params: dict | None = None) -> str:
        url = path if urllib.parse.urlsplit(path).scheme else f"{self.base_url}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
            url += ("&" if "?" in url else "?") + query
        return url

    def describe(self, method: str, url: str) -> str:
        """`METHOD /path` redacted, for messages."""
        u = urllib.parse.urlsplit(url)
        return self.redactor.text(f"{method} {u.path}" + (f"?{u.query}" if u.query else ""))

    def _reason(self, resp: Response) -> str:
        text = self.redactor.body(resp.text, 300) if resp.body else ""
        if isinstance(text, (dict, list)):
            text = json.dumps(text, ensure_ascii=False)[:300]
        return f"HTTP {resp.status}" + (f": {text}" if text else "")

    def retry_delay(self, resp: Response | None, attempt: int) -> float:
        """Retry-After (seconds or an HTTP date) capped at max_retry_after; otherwise exponential backoff with
        jitter (half to all of base * 2^(attempt-1), capped)."""
        value = resp.header("retry-after") if resp is not None else None
        if value not in (None, ""):
            try:
                seconds = float(value)
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(value)
                    seconds = (when - self.clock()).total_seconds()
                except (TypeError, ValueError):
                    seconds = None
            if seconds is not None:
                return max(0.0, min(seconds, self.max_retry_after))
        step = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt - 1))
        return step * (0.5 + 0.5 * self.rng())

    def _record(self, req: Request, resp: Response | None, error: str | None = None):
        if self.recorder is not None:
            self.recorder.write(req, resp, error)

    # -- the request --------------------------------------------------------------------------------------------

    def request(self, method: str, path: str, *, json_body=None, body: bytes | None = None,
                headers: dict | None = None, params: dict | None = None, read: bool = False,
                idempotent: bool | None = None, timeout: float | None = None) -> Response:
        """One request, with the retry policy of the module's docstring. Returns the 2xx/3xx Response; raises
        AuthFailed / RateLimited / Unavailable / UncertainWrite (exit 3) or HttpError (other 4xx)."""
        method = method.upper()
        url = self.url(path, params)
        retryable = read or (idempotent if idempotent is not None else method in IDEMPOTENT_METHODS)
        base_headers = {"Accept": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            base_headers["Content-Type"] = "application/json"
        base_headers.update(headers or {})
        what = self.describe(method, url)
        attempt, reauthenticated = 0, False
        while True:
            attempt += 1
            req = Request(method, url, {**base_headers, **(self.auth() if self.auth else {})}, body,
                          timeout if timeout is not None else self.timeout, read_only=read)
            try:
                resp = self.transport(req)
            except TransportError as e:
                reason = self.redactor.text(str(e))
                self._record(req, None, reason)
                if not retryable:
                    raise UncertainWrite(f"{what}: no answer ({reason}); it may have been done — never retried "
                                         "blindly: the next run searches the marker first") from None
                if attempt >= self.max_attempts:
                    raise Unavailable(f"{what}: no answer after {attempt} attempts ({reason})") from None
                self.sleep(self.retry_delay(None, attempt))
                continue
            self._record(req, resp)
            if resp.status == 401:
                if reauthenticated or self.reauth is None:
                    raise AuthFailed(f"{what}: authentication refused ({self._reason(resp)}): check the "
                                     "credentials' variables in .env")
                reauthenticated = True
                attempt -= 1   # the renewal does not count as a retry
                self.reauth()
                continue
            if resp.status == 403:
                raise AuthFailed(f"{what}: forbidden ({self._reason(resp)}): the account lacks a permission")
            if resp.status in RETRY_STATUSES:
                if not retryable:
                    if resp.status == 429:
                        raise RateLimited(f"{what}: rate limited ({self._reason(resp)}); a write is not retried "
                                          "here: run the same command again later (it continues)")
                    raise UncertainWrite(f"{what}: {self._reason(resp)}; it may have been done — never retried "
                                         "blindly: the next run searches the marker first")
                if attempt >= self.max_attempts:
                    cls = RateLimited if resp.status == 429 else Unavailable
                    raise cls(f"{what}: {self._reason(resp)} after {attempt} attempts")
                self.sleep(self.retry_delay(resp, attempt))
                continue
            if resp.status >= 500:
                raise Unavailable(f"{what}: server error ({self._reason(resp)})")
            if resp.status >= 400:
                raise HttpError(resp.status, f"{what}: {self._reason(resp)}",
                                self.redactor.text(resp.text[:2000]))
            return resp

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> Response:
        return self.request("POST", path, **kw)


# --- pagination (technique-jira-xray.md 2.7: always to the end, with a guard) --------------------------------------

def paginate_token(fetch: Callable[[str | None], tuple[list, str | None]], max_items: int = MAX_ITEMS) -> list:
    """Token pagination (Jira's nextPageToken, Azure DevOps' x-ms-continuationtoken): `fetch(token)` returns
    (items, next token or None). Followed to the end; more than max_items is a usage error (exit 4: narrow the
    query); a token repeated by the tracker is exit 3 instead of a loop."""
    items, token, seen = [], None, set()
    while True:
        page, token = fetch(token)
        items.extend(page or [])
        if len(items) > max_items:
            raise InvalidInput(f"more than {max_items} items: narrow the query (e.g. --query with a component "
                               "or a suite)")
        if not token:
            return items
        if token in seen:
            raise Unavailable("the tracker returned the same page token twice: pagination stopped")
        seen.add(token)


def paginate_offset(fetch: Callable[[int, int], tuple[list, int | None]], limit: int = 100,
                    max_items: int = MAX_ITEMS) -> list:
    """Offset pagination (Xray GraphQL start/limit/total): `fetch(start, limit)` returns (items, total or None).
    Stops at the total, or at a short or empty page."""
    items, start = [], 0
    while True:
        page, total = fetch(start, limit)
        page = page or []
        items.extend(page)
        if len(items) > max_items or (total is not None and total > max_items):
            raise InvalidInput(f"more than {max_items} items: narrow the query")
        start += len(page)
        if not page or len(page) < limit or (total is not None and start >= total):
            return items


__all__ = ["REDACTED", "HttpClient", "Recorder", "Request", "Response", "SecretRedactor", "TransportError",
           "TransportTimeout", "paginate_offset", "paginate_token", "urllib_transport"]
