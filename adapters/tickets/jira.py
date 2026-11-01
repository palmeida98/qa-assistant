"""
Jira for ticket analysis (docs/technique-tickets.md section 4.2): a read-only source over adapters/jira/api.py, whose
contract is unchanged, and the normaliser of the REST answers. Never touches Xray: `JiraApi.jira` alone is used, so no
Xray credential is needed.
"""

from __future__ import annotations

import urllib.parse

from adapters.jira.adf import from_adf
from adapters.jira.api import PAGE, SEARCH, JiraApi, jql_string
from adapters.tracker.errors import Conflict, HttpError, TrackerError

from .model import (
    COMPLETE,
    NOT_READ,
    PARTIAL,
    UNSUPPORTED,
    bundle_part,
    cover,
    dedupe_links,
    error_status,
    failed_snapshot,
    link_kind,
    links_in_text,
    part,
    read_pages,
    worst_status,
)

HISTORY_FIELDS = {"status": "state", "resolution": "reason", "assignee": "assignee", "priority": "priority",
                  "sprint": "sprint", "summary": "title", "description": "description", "issuetype": "type"}
EPIC_LEVEL = 1    # hierarchyLevel of an epic: its children are not in `subtasks`


def _who(value) -> str:
    return (value.get("displayName") or value.get("emailAddress") or "") if isinstance(value, dict) else str(value or "")


def _text(value) -> str:
    return from_adf(value)[0]


def links_in_adf(node, origin: str) -> list[dict]:
    """The links an ADF document holds in its marks and cards: the plain text of the field does not have them."""
    found: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            attrs = value.get("attrs") if isinstance(value.get("attrs"), dict) else {}
            for key in ("href", "url"):
                if isinstance(attrs.get(key), str) and attrs[key].startswith(("http://", "https://")):
                    found.append(attrs[key])
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(node)
    return [{"url": u, "kind": link_kind(u), "label": "", "origin": origin, "content_read": False} for u in found]


def _name_of(names: dict, wanted: str) -> str | None:
    return next((k for k, v in (names or {}).items() if str(v).lower() == wanted), None)


def _sprint(value) -> str:
    if not isinstance(value, list) or not value:
        return ""
    active = [s for s in value if isinstance(s, dict) and s.get("state") == "active"]
    chosen = active or [s for s in value if isinstance(s, dict)]
    return ", ".join(str(s.get("name")) for s in chosen)


def _link_kind_of(type_name: str, direction: str) -> str:
    text = direction.lower()
    if "block" in text or "block" in type_name.lower():
        return "blocked-by" if "by" in text else "blocks"
    if "test" in type_name.lower():
        return "tested-by" if "by" in text else "tests"
    return "duplicate" if "duplicate" in type_name.lower() else "related"


def _relations(f: dict, children: list) -> list[dict]:
    out = []
    if f.get("parent"):
        out.append({"kind": "parent", "label": "parent", "target": f["parent"].get("key"), "external": False})
    for s in f.get("subtasks") or []:
        out.append({"kind": "child", "label": "subtask", "target": s.get("key"), "external": False})
    for c in children:
        out.append({"kind": "child", "label": "child", "target": c.get("key"), "external": False})
    for link in f.get("issuelinks") or []:
        t = link.get("type") or {}
        issue, direction = (link["outwardIssue"], t.get("outward", "")) if link.get("outwardIssue") else \
            (link.get("inwardIssue") or {}, t.get("inward", ""))
        out.append({"kind": _link_kind_of(str(t.get("name") or ""), str(direction)), "label": direction or t.get("name"),
                    "target": issue.get("key"), "external": False})
    seen, unique = set(), []
    for r in out:
        if (r["kind"], r["target"]) not in seen:
            seen.add((r["kind"], r["target"]))
            unique.append(r)
    return unique


def _comment(c: dict) -> dict:
    updated, created = c.get("updated"), c.get("created")
    edited = bool(updated and updated != created)
    return {"id": str(c.get("id")), "author": _who(c.get("author")), "created_at": created,
            "edited_at": updated if edited else None, "edited_by": _who(c.get("updateAuthor")) if edited else None,
            "version": updated, "text": _text(c.get("body")), "deleted": False}


def _history(h: dict) -> dict | None:
    changes = []
    for item in h.get("items") or []:
        name = HISTORY_FIELDS.get(str(item.get("field") or "").lower())
        if name:
            changes.append({"field": name, "from": item.get("fromString") or "", "to": item.get("toString") or ""})
    if not changes:
        return None
    return {"id": str(h.get("id")), "author": _who(h.get("author")), "at": h.get("created"), "changes": changes}


def normalize(source, bundle: dict) -> dict:
    """One ticket: {id, issue | issue_error, comments, changelog, remotelinks, children}."""
    key = str(bundle["id"])
    ref = {"service": "jira", "org": source.org, "project": key.rsplit("-", 1)[0], "id": key}
    url = f"{source.base_url}/browse/{key}"
    issue = bundle.get("issue")
    if not issue:
        err = bundle.get("issue_error") or {}
        return failed_snapshot(ref, url, err.get("status") or NOT_READ, err.get("detail") or "issue not provided")
    f, names = issue.get("fields") or {}, issue.get("names") or {}
    configured = dict(source.cfg.get("fields") or {})
    ac_field = configured.pop("acceptance_criteria", None) or _name_of(names, "acceptance criteria")
    status = f.get("status") or {}
    fields = {"type": (f.get("issuetype") or {}).get("name") or "", "title": f.get("summary") or "",
              "description": _text(f.get("description")), "acceptance_criteria": _text(f.get(ac_field)) if ac_field else "",
              "state": status.get("name") or "",
              "state_category": "done" if (status.get("statusCategory") or {}).get("key") == "done" else "open",
              "reason": (f.get("resolution") or {}).get("name") or "", "assignee": _who(f.get("assignee")),
              "priority": (f.get("priority") or {}).get("name") or "",
              "sprint": _sprint(f.get(_name_of(names, "sprint") or "")),
              "area": ", ".join(str(c.get("name")) for c in f.get("components") or []),
              "custom": {k: _text(f.get(v)) if isinstance(f.get(v), (str, dict)) else str(f.get(v) or "")
                         for k, v in configured.items()}}
    comments = bundle.get("comments") or part([], NOT_READ, "comments: not provided")
    changelog = bundle.get("changelog") or part([], NOT_READ, "changelog: not provided")
    remote = bundle.get("remotelinks") or part([], NOT_READ, "remote links: not provided")
    children = bundle.get("children") or part([])
    norm_comments = [_comment(c) for c in comments["items"]]
    links = [{"url": (r.get("object") or {}).get("url") or "", "kind": link_kind((r.get("object") or {}).get("url") or ""),
              "label": (r.get("object") or {}).get("title") or "", "origin": "remote-link", "content_read": False}
             for r in remote["items"]]
    links += links_in_text(fields["description"], "description") + links_in_text(fields["acceptance_criteria"],
                                                                                 "acceptance_criteria")
    links += links_in_adf(f.get("description"), "description")
    if ac_field:
        links += links_in_adf(f.get(ac_field), "acceptance_criteria")
    for raw, c in zip(comments["items"], norm_comments, strict=True):
        links += links_in_text(c["text"], f"comment:{c['id']}")
        links += links_in_adf(raw.get("body"), f"comment:{c['id']}")
    attachments = [{"id": str(a.get("id")), "name": a.get("filename") or "", "url": a.get("content") or "",
                    "size": a.get("size"), "created_at": a.get("created"), "content_read": False}
                   for a in f.get("attachment") or []]
    links_status = worst_status(remote["status"], comments["status"])
    return {"ref": ref, "url": url, "source_revision": f.get("updated"), "changed_at": f.get("updated"),
            "components": {"fields": fields, "comments": norm_comments,
                           "history": [h for h in (_history(x) for x in changelog["items"]) if h],
                           "relations": _relations(f, children["items"]), "attachments": attachments,
                           "links": dedupe_links([x for x in links if x["url"]])},
            "coverage": {"fields": cover(COMPLETE), "comments": cover(comments["status"], comments["detail"]),
                         "history": cover(changelog["status"], changelog["detail"]),
                         "relations": cover(children["status"], children["detail"]), "attachments": cover(COMPLETE),
                         "links": cover(links_status, remote["detail"] or comments["detail"]),
                         "development": cover(UNSUPPORTED, "Jira has no public REST API for its development panel: "
                                                           "PRs, commits and builds appear only as remote links")}}


def bundle_from_raw(raw: dict) -> dict:
    out = {"id": raw.get("id"), "issue": raw.get("issue")}
    if raw.get("issue_error"):
        out["issue_error"] = {"status": {"denied": "denied", "not_found": "not_found"}.get(raw["issue_error"], "failed"),
                              "detail": str(raw.get("detail") or "")}
    for name in ("comments", "changelog", "remotelinks"):
        out[name] = bundle_part(raw.get(name), name)
    out["children"] = bundle_part(raw["children"], "children") if raw.get("children") is not None else part([])
    return out


class JiraSource:
    def __init__(self, source, ctx, limits: dict):
        self.source = source
        self.limits = limits
        self.api = JiraApi(ctx)

    def _issue_path(self, key: str, tail: str = "") -> str:
        return f"/rest/api/3/issue/{urllib.parse.quote(key)}{tail}"

    def _comments(self, key: str) -> dict:
        def fetch(start):
            start = start or 0
            data = self.api.jira.get(self._issue_path(key, "/comment"),
                                     params={"startAt": start, "maxResults": PAGE, "orderBy": "-created"}).json() or {}
            items = data.get("comments") or []
            done = start + len(items)
            return items, (done if items and done < int(data.get("total") or 0) else None)

        return read_pages(fetch, self.limits["max_comments"], "comments")

    def _changelog(self, key: str) -> dict:
        limit = self.limits["max_history"]
        path = self._issue_path(key, "/changelog")
        cut = {"total": 0}

        def fetch(start):
            data = self.api.jira.get(path, params={"startAt": start or 0, "maxResults": PAGE}).json() or {}
            total = int(data.get("total") or 0)
            if start is None and total > limit:      # oldest first: jump so the limit keeps the newest
                cut["total"] = total
                return [], total - limit
            items = data.get("values") or []
            done = (start or 0) + len(items)
            return items, (done if items and done < total else None)

        result = read_pages(fetch, limit, "history")
        if cut["total"] and result["status"] == COMPLETE:
            return part(result["items"], PARTIAL, f"history: only the newest {limit} of {cut['total']} changes")
        return result

    def _remote(self, key: str) -> dict:
        try:
            data = self.api.jira.get(self._issue_path(key, "/remotelink")).json()
        except (TrackerError, ValueError) as e:
            return part([], error_status(e), f"remote links: {e}")
        return part(data if isinstance(data, list) else [])

    def _children(self, key: str) -> dict:
        def fetch(token):
            body = {"jql": f"parent = {jql_string(key)}", "fields": ["key"], "maxResults": PAGE}
            if token:
                body["nextPageToken"] = token
            data = self.api.jira.post(SEARCH, json_body=body, read=True).json() or {}
            return data.get("issues") or [], (None if data.get("isLast", True) else data.get("nextPageToken"))

        return read_pages(fetch, self.limits["max_items"], "children")

    def fetch(self, key: str) -> dict:
        key = str(key)
        try:
            issue = self.api.jira.get(self._issue_path(key), params={"fields": "*all", "expand": "names"}).json()
        except (TrackerError, ValueError) as e:
            return {"id": key, "issue": None, "issue_error": {"status": error_status(e), "detail": str(e)}}
        level = ((issue.get("fields") or {}).get("issuetype") or {}).get("hierarchyLevel") or 0
        return {"id": key, "issue": issue, "comments": self._comments(key), "changelog": self._changelog(key),
                "remotelinks": self._remote(key), "children": self._children(key) if level >= EPIC_LEVEL else part([])}

    def normalize(self, bundle: dict) -> dict:
        return normalize(self.source, bundle)

    def query(self, text: str) -> tuple[list[str], str, str]:
        def fetch(token):
            body = {"jql": text, "fields": ["key"], "maxResults": PAGE}
            if token:
                body["nextPageToken"] = token
            try:
                data = self.api.jira.post(SEARCH, json_body=body, read=True).json() or {}
            except HttpError as e:
                if e.status == 400:
                    raise Conflict(f"Jira refused the JQL ({e})") from None
                raise
            return data.get("issues") or [], (None if data.get("isLast", True) else data.get("nextPageToken"))

        result = read_pages(fetch, self.limits["max_items"] * 10, "query")
        return [str(i.get("key")) for i in result["items"]], result["status"], result["detail"]

    def identity(self) -> str:
        return (self.api.jira.get("/rest/api/3/myself").json() or {}).get("displayName") or "?"


__all__ = ["JiraSource", "bundle_from_raw", "normalize"]
