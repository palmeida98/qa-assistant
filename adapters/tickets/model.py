"""
The normalised snapshot of a ticket and what every source shares (docs/technique-tickets.md sections 3.2 and 5).

A snapshot:
    {"ref": {"service", "org", "project", "id"}, "url", "source_revision", "changed_at",
     "components": {"fields": {...}, "comments": [...], "history": [...], "relations": [...], "attachments": [...],
                    "links": [...]},
     "coverage": {"<component>": {"status", "detail"}}}
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime

from adapters.tracker.errors import AuthFailed, HttpError, InvalidInput, TrackerError
from adapters.tracker.http import Request, Response, Transport

COMPONENTS = ("fields", "comments", "history", "relations", "attachments", "links")
COMPLETE, PARTIAL, FAILED, DENIED, NOT_FOUND, UNSUPPORTED, NOT_READ, LISTED_ONLY = (
    "complete", "partial", "failed", "denied", "not_found", "unsupported", "not_read", "listed_only")
STATUSES = (COMPLETE, PARTIAL, FAILED, DENIED, NOT_FOUND, UNSUPPORTED, NOT_READ, LISTED_ONLY)
DEVELOPMENT_KINDS = ("pr", "commit", "build", "branch")
RELATION_KINDS = ("parent", "child", "blocks", "blocked-by", "tested-by", "tests", "related", "duplicate")
FIELD_NAMES = ("type", "title", "description", "acceptance_criteria", "state", "state_category", "reason", "assignee",
               "priority", "sprint", "area", "custom")


class WriteRefused(TrackerError):
    """The flow tried a request that is not a read (a bug in the engine: it never reaches the tracker)."""


def read_only_transport(transport: Transport) -> Transport:
    """Every API request goes through this: a request that is not a GET, or a POST marked as a read, is never sent."""

    def guarded(req: Request) -> Response:
        if req.method not in ("GET", "HEAD") and not req.read_only:
            raise WriteRefused(f"refused: {req.method} {req.path} is not a read; /analyze-tickets never writes")
        return transport(req)

    return guarded


# --- keys ----------------------------------------------------------------------------------------------------------

def ticket_key(ref: dict) -> str:
    return f"{ref['service']}:{ref['org']}/{ref['project']}#{ref['id']}"


MAX_SLUG = 24       # a long organisation or project must not eat the path: the hash is what keeps them apart


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def ticket_slug(ref: dict) -> str:
    """Safe as a file name. The hash keeps apart names that slugify alike ('A B' and 'A-B')."""
    digest = hashlib.sha256(f"{ref['service']}|{ref['org']}|{ref['project']}".encode()).hexdigest()[:8]
    return (f"{ref['service']}__{slug(ref['org'])[:MAX_SLUG]}__{slug(ref['project'])[:MAX_SLUG]}__{digest}"
            f"__{slug(ref['id'])}")


def check_order(at: str, check_id: str) -> tuple:
    """A check's place in time: the instant of the consultation (a value without an offset is local time), then the
    sequence in its id. The one order everything current is worked out with (technique section 5)."""
    try:
        when = datetime.fromisoformat(str(at)).timestamp()
    except (ValueError, OverflowError, OSError):
        when = 0.0
    tail = str(check_id).split("-")[2:]
    return (when, int(tail[0]) if tail and tail[0].isdigit() else 1, str(check_id))


def text_hash(text) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).lower().encode("utf-8")).hexdigest()[:16]


# --- redaction before anything is stored -------------------------------------------------------------------------

# other-language labels are kept on purpose: this reads what people write in tickets, in any language
_SECRET_ASSIGN = re.compile(r"(?i)\b(pass(?:word|wd|phrase)?|pwd|senha|palavra-passe|secret|client[_ -]?secret|"
                            r"api[_ -]?key|access[_ -]?key|auth[_ -]?token|access[_ -]?token|token|otp)"
                            r"(\s*[:=]\s*)(\S+)")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}")
_KNOWN = re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*|gh[pousr]_[A-Za-z0-9]{20,}|"
                    r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ATATT[A-Za-z0-9_=-]{20,})\b")
_SECRET_PARAM = re.compile(r"(?i)([?&#;](?:[a-z_]*token|code|oobcode|otp|apikey|api_key|key|sig|signature|password|pwd|"
                           r"secret|auth|session|sessionid|magic|ticket)=)[^&#\s\"'<>)]+")
_LOGIN_LINK = re.compile(r"(?i)https?://[^\s\"'<>)]*(?:magic[-_]?link|/auth/action|signin[-_]?link|/login/callback)"
                         r"[^\s\"'<>)]*")
REDACTED_TEXT = "«redacted»"


class Sanitizer:
    """The values of the workspace's .env, and the patterns of tokens, passwords and login links."""

    MIN_VALUE = 6   # a shorter .env value ("1", "true") would wreck ordinary text

    def __init__(self, env: dict[str, str] | None = None):
        self.values = sorted(((v, k) for k, v in (env or {}).items() if v and len(v) >= self.MIN_VALUE),
                             key=lambda x: -len(x[0]))

    def text(self, s: str) -> str:
        for value, name in self.values:
            s = s.replace(value, f"«{name}»")
        s = _LOGIN_LINK.sub("«login link redacted»", s)
        s = _SECRET_PARAM.sub(lambda m: m.group(1) + REDACTED_TEXT, s)
        s = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED_TEXT}", s)
        s = _KNOWN.sub(REDACTED_TEXT, s)
        return _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED_TEXT}", s)

    def obj(self, value):
        if isinstance(value, dict):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.obj(v) for v in value]
        return self.text(value) if isinstance(value, str) else value


# --- paged reads that keep what they got ---------------------------------------------------------------------------

def error_status(e: Exception) -> str:
    if isinstance(e, AuthFailed):
        return DENIED
    if isinstance(e, HttpError) and e.status == 404:
        return NOT_FOUND
    return FAILED


def part(items=None, status: str = COMPLETE, detail: str = "") -> dict:
    return {"items": [] if items is None else items, "status": status, "detail": detail}


def read_pages(fetch: Callable[[object], tuple[list, object]], limit: int, what: str) -> dict:
    """`fetch(cursor)` -> (items, next cursor or None). A failure in the middle keeps the pages already read
    (`partial`); the limit is `partial` too, never a silent cut; a repeated cursor stops instead of looping."""
    items, cursor, seen = [], None, set()
    while True:
        try:
            page, cursor = fetch(cursor)
        except (TrackerError, ValueError) as e:    # ValueError: an answer that is not JSON (e.g. a sign-in page)
            status = error_status(e)
            return part(items, PARTIAL if items else status, f"{what}: {e}")
        items.extend(page or [])
        if len(items) > limit or (len(items) == limit and cursor):
            return part(items[:limit], PARTIAL, f"{what}: stopped at the limit of {limit}")
        if not cursor:
            return part(items)
        if cursor in seen:
            return part(items, PARTIAL, f"{what}: the tracker repeated a page cursor")
        seen.add(cursor)


def bundle_part(raw, what: str) -> dict:
    """A part of a browser bundle (technique section 4.3): absent is not_read, never assumed empty."""
    if raw is None:
        return part([], NOT_READ, f"{what}: not provided")
    if isinstance(raw, list):
        raise InvalidInput(f"{what}: a part is {{\"items\": [...], \"complete\": true|false}}, not a bare list: "
                           "the bundle must say whether it read to the end")
    if raw.get("error"):
        status = {"denied": DENIED, "not_found": NOT_FOUND, "failed": FAILED, "unsupported": UNSUPPORTED}.get(raw["error"])
        if status is None:
            raise InvalidInput(f"{what}: error {raw['error']!r} outside denied, not_found, failed, unsupported")
        return part(raw.get("items") or [], status, str(raw.get("detail") or ""))
    if not isinstance(raw.get("complete"), bool):
        raise InvalidInput(f"{what}: \"complete\": true or false is required")
    return part(raw.get("items") or [], COMPLETE if raw["complete"] else PARTIAL,
                str(raw.get("detail") or ("" if raw["complete"] else f"{what}: not read to the end")))


# --- shared by the normalisers ---------------------------------------------------------------------------------------

URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+")
URL_IN_HTML = re.compile(r"""(?:href|src)\s*=\s*["'](https?://[^"']+)["']""", re.IGNORECASE)


def link_kind(url: str) -> str:
    u = url.lower()
    if "figma.com/" in u:
        return "figma"
    if "/pull/" in u or "/pullrequest" in u or "/pull-requests/" in u or "/merge_requests/" in u:
        return "pr"
    if "/commit/" in u or "/commits/" in u:
        return "commit"
    if "/_build/" in u or "/actions/runs/" in u:
        return "build"
    return "other"


def links_in_text(text: str, origin: str) -> list[dict]:
    return [{"url": u.rstrip(".,;:"), "kind": link_kind(u), "label": "", "origin": origin, "content_read": False}
            for u in URL_IN_TEXT.findall(str(text or ""))]


def links_in_html(html: str, origin: str) -> list[dict]:
    """The links only an attribute holds (`<a href>`, `<img src>`): the plain text of the field does not have them."""
    return [{"url": u, "kind": link_kind(u), "label": "", "origin": origin, "content_read": False}
            for u in URL_IN_HTML.findall(str(html or ""))]


def dedupe_links(links: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for link in links:
        out.setdefault(link["url"], link)
    return list(out.values())


def worst_status(*statuses: str) -> str:
    for status in (DENIED, FAILED, NOT_FOUND, NOT_READ, PARTIAL):
        if status in statuses:
            return status
    return COMPLETE


def cover(status: str, detail: str = "") -> dict:
    return {"status": status, "detail": detail}


def failed_snapshot(ref: dict, url: str, status: str, detail: str) -> dict:
    """The ticket itself could not be read: every component carries why."""
    return {"ref": ref, "url": url, "source_revision": None, "changed_at": None,
            "components": {"fields": {}, "comments": [], "history": [], "relations": [], "attachments": [], "links": []},
            "coverage": {c: cover(status, detail) for c in COMPONENTS}}


def validate_snapshot(snap) -> dict:
    """A snapshot given directly by a tool (`{"normalized": ...}`): the coverage must be stated for every component."""
    if not isinstance(snap, dict) or not isinstance(snap.get("ref"), dict):
        raise InvalidInput("normalized: a snapshot needs ref {service, org, project, id}")
    missing = [k for k in ("service", "org", "project", "id") if not snap["ref"].get(k)]
    if missing:
        raise InvalidInput(f"normalized: ref misses {missing}")
    snap["ref"]["id"] = str(snap["ref"]["id"])
    comps, cov = snap.get("components"), snap.get("coverage")
    if not isinstance(comps, dict) or not isinstance(cov, dict):
        raise InvalidInput(f"normalized {snap['ref']['id']}: components and coverage are required")
    for c in COMPONENTS:
        status = (cov.get(c) or {}).get("status")
        if status not in STATUSES:
            raise InvalidInput(f"normalized {snap['ref']['id']}: coverage.{c}.status {status!r} outside {list(STATUSES)}")
        comps.setdefault(c, {} if c == "fields" else [])
    snap.setdefault("url", "")
    snap.setdefault("source_revision", None)
    snap.setdefault("changed_at", None)
    return snap
