"""
Azure DevOps for ticket analysis (docs/technique-tickets.md section 4.1): a read-only source over adapters/azure_devops/
api.py, whose contract is unchanged, and the normaliser of the REST answers (the browser route uses the same one).
"""

from __future__ import annotations

import re
import urllib.parse

from adapters.azure_devops.api import WIQL_TOP, AdoApi, comments_version, wiql_string
from adapters.azure_devops.html import plain
from adapters.tracker.errors import Conflict, HttpError, InvalidInput, TrackerError

from .model import (
    COMPLETE,
    DEVELOPMENT_KINDS,
    LISTED_ONLY,
    NOT_FOUND,
    NOT_READ,
    PARTIAL,
    bundle_part,
    cover,
    dedupe_links,
    error_status,
    failed_snapshot,
    link_kind,
    links_in_html,
    links_in_text,
    part,
    read_pages,
    worst_status,
)

COMMENTS_PAGE = 100
UPDATES_PAGE = 200
RELATIONS = {"System.LinkTypes.Hierarchy-Reverse": "parent", "System.LinkTypes.Hierarchy-Forward": "child",
             "System.LinkTypes.Dependency-Reverse": "blocked-by", "System.LinkTypes.Dependency-Forward": "blocks",
             "Microsoft.VSTS.Common.TestedBy-Forward": "tested-by", "Microsoft.VSTS.Common.TestedBy-Reverse": "tests",
             "System.LinkTypes.Related": "related", "System.LinkTypes.Duplicate-Forward": "duplicate",
             "System.LinkTypes.Duplicate-Reverse": "duplicate"}
ARTIFACTS = {"pull request": "pr", "fixed in commit": "commit", "fixed in changeset": "commit", "branch": "branch",
             "build": "build", "found in build": "build", "integrated in build": "build"}
# field reference -> the normalised name, for the history
HISTORY_FIELDS = {"System.State": "state", "System.Reason": "reason", "System.AssignedTo": "assignee",
                  "Microsoft.VSTS.Common.Priority": "priority", "System.IterationPath": "sprint",
                  "System.Title": "title", "System.Description": "description",
                  "System.WorkItemType": "type", "System.AreaPath": "area"}
AC_FIELD = "Microsoft.VSTS.Common.AcceptanceCriteria"
NEVER = "9999"   # a revisedDate in 9999: the revision still current
WORK_ITEM_ID = re.compile(r"/workItems/(\d+)", re.IGNORECASE)


def _who(value) -> str:
    return (value.get("displayName") or value.get("uniqueName") or "") if isinstance(value, dict) else str(value or "")


def ticket_url(source, project: str, wid) -> str:
    return f"{source.base_url}/{urllib.parse.quote(project)}/_workitems/edit/{wid}"


def _relations(raw: list, base_url: str) -> tuple[list, list, list]:
    relations, attachments, links = [], [], []
    host = urllib.parse.urlsplit(base_url).netloc.lower()
    for r in raw or []:
        rel, url, attrs = r.get("rel", ""), r.get("url", ""), r.get("attributes") or {}
        if rel == "AttachedFile":
            attachments.append({"id": url.rstrip("/").rsplit("/", 1)[-1], "name": attrs.get("name") or "", "url": url,
                                "size": attrs.get("resourceSize"), "created_at": attrs.get("resourceCreatedDate"),
                                "content_read": False})
        elif rel == "ArtifactLink":
            kind = ARTIFACTS.get(str(attrs.get("name") or "").lower(), "other")
            links.append({"url": url, "kind": kind, "label": attrs.get("name") or "", "origin": "relation",
                          "content_read": False})
        elif rel == "Hyperlink":
            links.append({"url": url, "kind": link_kind(url), "label": attrs.get("comment") or "",
                          "origin": "relation", "content_read": False})
        else:
            match = WORK_ITEM_ID.search(url)
            external = urllib.parse.urlsplit(url).netloc.lower() != host or not match
            relations.append({"kind": RELATIONS.get(rel, "related"), "label": attrs.get("name") or rel,
                              "target": match.group(1) if match else url, "external": external})
    return relations, attachments, links


def _comment(c: dict) -> dict:
    modified = c.get("modifiedDate")
    edited = bool(modified and modified != c.get("createdDate"))
    # renderedText: what the page shows. `text` may be Markdown with mentions as @<GUID> (seen in a real read)
    return {"id": str(c.get("id")), "author": _who(c.get("createdBy")), "created_at": c.get("createdDate"),
            "edited_at": modified if edited else None, "edited_by": _who(c.get("modifiedBy")) if edited else None,
            "version": c.get("version"), "text": plain(c.get("renderedText") or c.get("text")),
            "deleted": bool(c.get("isDeleted"))}


def _listed_change(r: dict, base_url: str) -> tuple[str, str]:
    """(field, key) of an added or removed relation in an update, with the same key the comparison uses."""
    relations, attachments, links = _relations([r], base_url)
    if attachments:
        return "attachment", str(attachments[0]["id"])
    if links:
        return "link", links[0]["url"]
    return "relation", f"{relations[0]['kind']}:{relations[0]['target']}"


def _update(u: dict, base_url: str = "") -> dict | None:
    changes = []
    moved = u.get("relations") or {}
    for r in moved.get("added") or []:
        field, key = _listed_change(r, base_url)
        changes.append({"field": field, "from": "", "to": key})
    for r in moved.get("removed") or []:
        field, key = _listed_change(r, base_url)
        changes.append({"field": field, "from": key, "to": ""})
    for ref, value in (u.get("fields") or {}).items():
        name = HISTORY_FIELDS.get(ref) or ("acceptance_criteria" if ref == AC_FIELD else None)
        if name:
            is_text = name in ("description", "acceptance_criteria")
            before, after = value.get("oldValue"), value.get("newValue")
            changes.append({"field": name, "from": plain(before) if is_text else _who(before),
                            "to": plain(after) if is_text else _who(after)})
    if not changes:
        return None
    changed = (u.get("fields") or {}).get("System.ChangedDate") or {}
    revised = u.get("revisedDate")
    at = changed.get("newValue") or (revised if not str(revised or "").startswith(NEVER) else None)
    return {"id": str(u.get("rev") or u.get("id")), "author": _who(u.get("revisedBy")), "at": at, "changes": changes}


def normalize(source, bundle: dict) -> dict:
    """One ticket: {id, workitem | workitem_error, comments, updates}, each part from `part()` or a browser bundle."""
    wid = str(bundle["id"])
    wi = bundle.get("workitem")
    project = ((wi or {}).get("fields") or {}).get("System.TeamProject") or source.cfg["project"]
    ref = {"service": "azure-devops", "org": source.org, "project": project, "id": wid}
    url = ticket_url(source, project, wid)
    if not wi:
        err = bundle.get("workitem_error") or {}
        return failed_snapshot(ref, url, err.get("status") or NOT_READ, err.get("detail") or "work item not provided")
    f = wi.get("fields") or {}
    state = f.get("System.State") or ""
    configured = dict(source.cfg.get("fields") or {})
    ac_field = configured.pop("acceptance_criteria", AC_FIELD)
    closed = {s.lower() for s in source.cfg.get("closed_states") or []}
    fields = {"type": f.get("System.WorkItemType") or "", "title": f.get("System.Title") or "",
              "description": plain(f.get("System.Description")), "acceptance_criteria": plain(f.get(ac_field)),
              "state": state, "state_category": "done" if state.lower() in closed else "open",
              "reason": f.get("System.Reason") or "", "assignee": _who(f.get("System.AssignedTo")),
              "priority": str(f.get("Microsoft.VSTS.Common.Priority") or ""), "sprint": f.get("System.IterationPath") or "",
              "area": f.get("System.AreaPath") or "",
              "custom": {k: plain(f.get(v)) if isinstance(f.get(v), str) else _who(f.get(v))
                         for k, v in configured.items()}}
    relations, attachments, links = _relations(wi.get("relations"), source.base_url)
    comments = bundle.get("comments") or part([], NOT_READ, "comments: not provided")
    updates = bundle.get("updates") or part([], NOT_READ, "updates: not provided")
    norm_comments = [_comment(c) for c in comments["items"]]
    links += links_in_text(fields["description"], "description") + links_in_text(fields["acceptance_criteria"],
                                                                                 "acceptance_criteria")
    links += links_in_html(f.get("System.Description"), "description") + links_in_html(f.get(ac_field),
                                                                                       "acceptance_criteria")
    for raw, c in zip(comments["items"], norm_comments, strict=True):
        links += links_in_text(c["text"], f"comment:{c['id']}")
        links += links_in_html(raw.get("renderedText") or raw.get("text"), f"comment:{c['id']}")
    links_status = worst_status(COMPLETE, comments["status"])
    listed = any(x["kind"] in DEVELOPMENT_KINDS for x in links)
    return {"ref": ref, "url": url, "source_revision": str(wi.get("rev") or ""), "changed_at": f.get("System.ChangedDate"),
            "components": {"fields": fields, "comments": norm_comments,
                           "history": [h for h in (_update(u, source.base_url) for u in updates["items"]) if h],
                           "relations": relations, "attachments": attachments, "links": dedupe_links(links)},
            "coverage": {"fields": cover(COMPLETE), "comments": cover(comments["status"], comments["detail"]),
                         "history": cover(updates["status"], updates["detail"]), "relations": cover(COMPLETE),
                         "attachments": cover(COMPLETE),
                         "links": cover(links_status, "" if links_status == COMPLETE else
                                        "links inside comments depend on the comments"),
                         "development": cover(LISTED_ONLY, "PRs, commits or builds are listed from the links; nothing "
                                              "behind them was read") if listed else
                         cover(COMPLETE, "no PR, commit or build linked")}}


def bundle_from_raw(raw: dict) -> dict:
    """A ticket of a browser bundle (technique section 4.3)."""
    out = {"id": raw.get("id"), "workitem": raw.get("workitem")}
    if raw.get("workitem_error"):
        out["workitem_error"] = {"status": {"denied": "denied", "not_found": NOT_FOUND}.get(raw["workitem_error"], "failed"),
                                 "detail": str(raw.get("detail") or "")}
    out["comments"] = bundle_part(raw.get("comments"), "comments")
    out["updates"] = bundle_part(raw.get("updates"), "updates")
    return out


class AdoSource:
    """Reads only: the transport is wrapped by read_only_transport in the context."""

    def __init__(self, source, ctx, limits: dict):
        self.source = source
        self.limits = limits
        self.api = AdoApi(ctx)
        self.version = str(source.cfg.get("api_version"))

    def _comments(self, wid: str) -> dict:
        def fetch(token):
            data = self.api.client.get(self.api.path(f"{self.api.p}/_apis/wit/workItems/{wid}/comments",
                                                     comments_version(self.version),
                                                     **{"$top": COMMENTS_PAGE, "$expand": "renderedText"},
                                                     order="desc", includeDeleted="true",
                                                     continuationToken=token)).json() or {}
            return data.get("comments") or [], data.get("continuationToken")

        return read_pages(fetch, self.limits["max_comments"], "comments")

    def _updates(self, wid: str, rev) -> dict:
        limit = self.limits["max_history"]
        start = max(0, int(rev or 0) - limit)     # the updates come oldest first: the limit must keep the newest

        def fetch(skip):
            skip = skip or start
            page = self.api.client.get(self.api.path(f"{self.api.p}/_apis/wit/workItems/{wid}/updates",
                                                     **{"$skip": skip, "$top": UPDATES_PAGE})).json() or {}
            items = page.get("value") or []
            return items, (skip + len(items) if len(items) == UPDATES_PAGE else None)

        result = read_pages(fetch, limit, "history")
        if start and result["status"] == COMPLETE:
            return part(result["items"], PARTIAL, f"history: only the newest {limit} revisions of {rev}")
        return result

    def fetch(self, wid: str) -> dict:
        wid = str(wid)
        try:
            wi = self.api.client.get(self.api.path(f"{self.api.p}/_apis/wit/workitems/{wid}", **{"$expand": "all"})).json()
        except (TrackerError, ValueError) as e:
            return {"id": wid, "workitem": None, "workitem_error": {"status": error_status(e), "detail": str(e)}}
        return {"id": wid, "workitem": wi, "comments": self._comments(wid), "updates": self._updates(wid, wi.get("rev"))}

    def normalize(self, bundle: dict) -> dict:
        return normalize(self.source, bundle)

    def query(self, text: str) -> tuple[list[str], str, str]:
        """(ids, status, detail). A refused WIQL is a Conflict (exit 2): the query is wrong, not the access."""
        ids = self.api.wiql(text)
        if len(ids) >= WIQL_TOP:
            return [str(i) for i in ids], PARTIAL, f"the query stopped at its limit of {WIQL_TOP} items"
        return [str(i) for i in ids], COMPLETE, ""

    def current_iteration_query(self) -> str:
        team = self.source.cfg.get("team")
        if not team:
            raise InvalidInput(f"source {self.source.id!r}: current_iteration needs its team")
        rel = f"{self.api.p}/{urllib.parse.quote(team, safe='')}/_apis/work/teamsettings/iterations"
        data = self.api.client.get(self.api.path(rel, **{"$timeframe": "current"})).json() or {}
        values = data.get("value") or []
        if len(values) != 1:
            names = ", ".join(str(v.get("path")) for v in values) or "none"
            raise Conflict(f"current iteration of team {team!r}: expected one, found {len(values)} ({names}): "
                           "say which one, as a query")
        return (f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND "
                f"[System.IterationPath] = {wiql_string(values[0].get('path'))}")

    def identity(self) -> str:
        data = self.api.client.get(self.api.path("/_apis/connectionData", f"{self.version}-preview")).json() or {}
        user = data.get("authenticatedUser") or {}
        return user.get("providerDisplayName") or user.get("customDisplayName") or "?"


__all__ = ["AdoSource", "HttpError", "bundle_from_raw", "normalize", "ticket_url"]
