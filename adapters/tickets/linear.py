"""
Linear for ticket analysis (docs/technique-tickets.md section 4.4): a read-only source over Linear's official GraphQL
API, and the normaliser of its answers.

- Reads only. Every request is one of the named queries of ALLOWED below, sent as a POST marked as a read through
  the read-only transport; a document that is not a `query`, or a name not in ALLOWED, is refused before anything
  is sent (`run`). There is no mutation anywhere in this module.
- Authentication: a personal API key in the header `Authorization: <key>` (no `Bearer`), the value read from the
  workspace's .env through the variable named by `api_key_var`. The key goes to the redaction before any request.
- The endpoint is fixed (ENDPOINT): a key is never sent to another host, whatever project.yaml says.
- What Linear does not have, or does not keep, is said, never taken as empty: no acceptance criteria field (a section
  of the description, by the configured headings), no issue type (configured labels), no resolution; its history
  records that a description was edited but not the text; a comment keeps `editedAt` but not its earlier text; changes
  in the first minutes after creation are not in the history (`fields.unavailable`, the coverage's detail).
"""

from __future__ import annotations

import json
import re

from adapters.tracker.errors import (
    AuthFailed,
    HttpError,
    InvalidInput,
    RateLimited,
    TrackerError,
)

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

ENDPOINT = "https://api.linear.app"
GRAPHQL = "/graphql"
PAGE = 50                       # Linear's default page size
LABELS_PAGE = 100
DONE_TYPES = ("completed", "canceled")
PRIORITIES = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
HISTORY_NOTE = ("Linear's history records that a description was edited, not its text, and leaves out changes made "
                "in the first minutes after an issue is created")
UPLOAD_HOSTS = ("uploads.linear.app",)

_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
_CONN = "(first: $first, after: $after)"
_VARS = "($id: String!, $first: Int!, $after: String)"

# The only documents this module sends. Variables, never values spliced into the text.
ALLOWED = {
    "Issue": (
        "query Issue($id: String!) { issue(id: $id) { id identifier url title description priority priorityLabel "
        "estimate dueDate createdAt updatedAt completedAt canceledAt archivedAt trashed previousIdentifiers "
        "state { name type } assignee { displayName } team { key name } project { name } cycle { number name } "
        f"parent {{ identifier }} labels(first: {LABELS_PAGE}) {{ nodes {{ name }} {_PAGE_INFO} }} }} }}"),
    "IssueComments": (
        f"query IssueComments{_VARS} {{ issue(id: $id) {{ comments{_CONN} {{ nodes {{ id body createdAt editedAt "
        f"resolvedAt user {{ displayName }} parent {{ id }} }} {_PAGE_INFO} }} }} }}"),
    "IssueHistory": (
        f"query IssueHistory{_VARS} {{ issue(id: $id) {{ history{_CONN} {{ nodes {{ id createdAt "
        "actor { displayName } fromState { name } toState { name } fromAssignee { displayName } "
        "toAssignee { displayName } fromPriority toPriority fromTitle toTitle fromProject { name } toProject { name } "
        "fromCycle { number name } toCycle { number name } fromParent { identifier } toParent { identifier } "
        "fromTeam { key name } toTeam { key name } addedLabels { name } removedLabels { name } updatedDescription } "
        f"{_PAGE_INFO} }} }} }}"),
    "IssueRelations": (
        f"query IssueRelations{_VARS} {{ issue(id: $id) {{ relations{_CONN} {{ nodes {{ type "
        f"relatedIssue {{ identifier }} }} {_PAGE_INFO} }} }} }}"),
    "IssueInverseRelations": (
        f"query IssueInverseRelations{_VARS} {{ issue(id: $id) {{ inverseRelations{_CONN} {{ nodes {{ type "
        f"issue {{ identifier }} }} {_PAGE_INFO} }} }} }}"),
    "IssueChildren": (
        f"query IssueChildren{_VARS} {{ issue(id: $id) {{ children{_CONN} {{ nodes {{ identifier }} {_PAGE_INFO} }} "
        "} }"),
    "IssueAttachments": (
        f"query IssueAttachments{_VARS} {{ issue(id: $id) {{ attachments{_CONN} {{ nodes {{ id title url sourceType "
        f"createdAt }} {_PAGE_INFO} }} }} }}"),
    "Issues": (
        "query Issues($filter: IssueFilter, $first: Int!, $after: String) { issues(filter: $filter, first: $first, "
        f"after: $after) {{ nodes {{ identifier }} {_PAGE_INFO} }} }}"),
    "Viewer": ("query Viewer { viewer { displayName } organization { urlKey } "
               f"teams(first: {LABELS_PAGE}) {{ nodes {{ key }} {_PAGE_INFO} }} }}"),
}
_WRITE_WORD = re.compile(r"\b(mutation|subscription)\b", re.IGNORECASE)


def _check_document(name: str, document: str) -> None:
    if not document.lstrip().startswith(f"query {name}") or _WRITE_WORD.search(document):
        raise InvalidInput(f"Linear: {name!r} is not a read query; /analyze-ticket never writes")


for _name, _doc in ALLOWED.items():         # checked once at import: a write can never be added by mistake
    _check_document(_name, _doc)


class GraphQLFailure(TrackerError):
    """Linear answered with `errors` that are not about access or existence (exit 3: the read failed)."""

    code = 3


def _classify(errors: list, what: str) -> TrackerError:
    """An exception for Linear's `errors`: only RATELIMITED is documented; the other codes are matched conservatively
    and anything unknown is a failure, never an absence."""
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    code = str(((first.get("extensions") or {}).get("code")) or "").upper()
    message = str(first.get("message") or "").strip()[:200]
    text = f"{code} {message}".lower()
    detail = f"{what}: Linear answered {code or 'an error'}" + (f" ({message})" if message else "")
    if "ratelimit" in text.replace("_", "").replace(" ", ""):
        return RateLimited(detail)
    if any(w in text for w in ("authentication", "unauthenticated", "forbidden", "not authorized", "unauthorized",
                               "permission", "access denied")):
        return AuthFailed(detail)
    if any(w in text for w in ("not found", "not_found", "notfound", "does not exist")):
        return HttpError(404, detail + "; or not visible to this key (Linear does not tell the two apart)")
    return GraphQLFailure(detail)


class LinearApi:
    """The GraphQL client: one POST per allowed query, marked as a read."""

    def __init__(self, ctx, key_var: str):
        self.ctx = ctx
        key = ctx.secrets[key_var]
        ctx.redactor.add(key_var, key)
        self.http = ctx.client(ENDPOINT, auth=lambda: {"Authorization": key})

    def run(self, name: str, variables: dict | None = None, allow_partial: bool = False):
        """The `data` of an allowed query. With allow_partial, (data, errors) when Linear answered both."""
        document = ALLOWED.get(name)
        if document is None:
            raise InvalidInput(f"Linear: {name!r} is not one of the allowed queries")
        _check_document(name, document)
        body = {"query": document, "operationName": name, "variables": variables or {}}
        try:
            resp = self.http.post(GRAPHQL, json_body=body, read=True)
        except HttpError as e:       # Linear answers a refused query with 400 and `errors`
            try:
                errors = (json.loads(e.body or "{}") or {}).get("errors")
            except ValueError:
                errors = None
            if errors:
                raise _classify(errors, name) from None
            raise
        doc = resp.json()
        if not isinstance(doc, dict):
            raise ValueError(f"{name}: the answer is not a GraphQL document")  # noqa: TRY004 - read as a failed read, like any answer that is not JSON
        data, errors = doc.get("data"), doc.get("errors") or []
        if errors and allow_partial and isinstance(data, dict) and any(v is not None for v in data.values()):
            return data, errors
        if errors:
            raise _classify(errors, name)
        if not isinstance(data, dict):
            raise ValueError(f"{name}: the answer has no data")  # noqa: TRY004 - same
        return (data, []) if allow_partial else data


# --- normalising -------------------------------------------------------------------------------------------------

def _who(value) -> str:
    return str((value or {}).get("displayName") or (value or {}).get("name") or "") if isinstance(value, dict) else ""


def _cycle(value) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name") or (f"Cycle {value['number']}" if value.get("number") is not None else ""))


def _priority(value) -> str:
    try:
        return PRIORITIES.get(int(value), str(value))
    except (TypeError, ValueError):
        return ""


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BOLD_LINE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")


def _heading(line: str) -> tuple[int, str] | None:
    """(level, text) of a markdown heading, or of a line that is only bold text (level 7)."""
    m = _HEADING.match(line)
    if m:
        return len(m.group(1)), m.group(2)
    m = _BOLD_LINE.match(line)
    return (7, m.group(1)) if m else None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[*_`:]+", " ", str(text)).split()).casefold()


def sections(description: str, headings: list[str]) -> list[dict]:
    """The sections of the description under one of the headings, word for word: {heading, start, end, text} with the
    1-based lines of the section's body. A section ends at the next heading of the same or a higher level."""
    wanted = {_norm(h) for h in headings}
    lines = str(description or "").split("\n")
    out = []
    i = 0
    while i < len(lines):
        h = _heading(lines[i])
        if h and _norm(h[1]) in wanted:
            level, j = h[0], i + 1
            while j < len(lines):
                nxt = _heading(lines[j])
                if nxt and (nxt[0] <= level or level == 7):
                    break
                j += 1
            body = lines[i + 1:j]
            while body and not body[0].strip():
                body, i = body[1:], i + 1
            while body and not body[-1].strip():
                body = body[:-1]
            start = i + 2           # 1-based line of the body's first line
            out.append({"heading": h[1], "start": start, "end": start + len(body) - 1, "text": "\n".join(body)})
            i = j
            continue
        i += 1
    return out


def _acceptance(source, description: str, have_description: bool) -> tuple[str, str | None, str | None]:
    """(text, origin, reason unavailable)."""
    headings = list(((source.cfg.get("acceptance_criteria") or {}).get("headings")) or [])
    if not headings:
        return "", None, ("Linear has no acceptance criteria field and no acceptance_criteria.headings is configured "
                          "for this source: read the description")
    if not have_description:
        return "", None, "the description was not read"
    found = sections(description, headings)
    if not found:
        return "", f"description: no section under {', '.join(repr(h) for h in headings)}", None
    origin = "; ".join(f"description, section {s['heading']!r}, lines {s['start']}-{s['end']}" for s in found)
    return "\n\n".join(s["text"] for s in found), origin, None


def _type(source, labels: list[str]) -> tuple[str, str | None]:
    mapping = source.cfg.get("type_labels") or {}
    if not mapping:
        return "", "Linear issues have no type; no type_labels is configured for this source"
    by_label = {_norm(k): str(v) for k, v in mapping.items()}
    for label in labels:
        if _norm(label) in by_label:
            return by_label[_norm(label)], None
    return "", None


def _fields(source, issue: dict, issue_errors: list) -> tuple[dict, str, str]:
    state = issue.get("state") or {}
    labels_conn = issue.get("labels") or {}
    labels = sorted(str(n.get("name")) for n in labels_conn.get("nodes") or [] if isinstance(n, dict))
    unavailable: dict[str, str] = {"reason": "Linear has no resolution field: the state's type is in custom.state_type"}
    partial: dict[str, str] = {}
    origin: dict[str, str] = {"title": "title", "description": "description"}
    description = issue.get("description")
    has_description = "description" in issue
    if description is None and has_description:
        description = ""
    ac, ac_origin, ac_reason = _acceptance(source, description or "", has_description)
    if ac_reason:
        unavailable["acceptance_criteria"] = ac_reason
    if ac_origin:
        origin["acceptance_criteria"] = ac_origin
    kind, type_reason = _type(source, labels)
    if type_reason:
        unavailable["type"] = type_reason
    if (labels_conn.get("pageInfo") or {}).get("hasNextPage"):
        partial["custom.labels"] = f"only the first {LABELS_PAGE} labels were read"
    if issue_errors:
        # Linear answered the issue with errors in some of its fields: what is null there was not read
        for name, path_key in (("assignee", "assignee"), ("sprint", "cycle"), ("area", "team"),
                               ("custom.project", "project"), ("custom.labels", "labels")):
            if issue.get(path_key) is None:
                partial[name] = "not read: Linear answered this field with an error"
    fields = {"type": kind, "title": issue.get("title") or "", "description": description or "",
              "acceptance_criteria": ac,
              "state": state.get("name") or "",
              "state_category": "done" if state.get("type") in DONE_TYPES else "open",
              "reason": "", "assignee": _who(issue.get("assignee")),
              "priority": issue.get("priorityLabel") or _priority(issue.get("priority")),
              "sprint": _cycle(issue.get("cycle")), "area": str((issue.get("team") or {}).get("name") or ""),
              "custom": {"state_type": str(state.get("type") or ""),
                         "team_key": str((issue.get("team") or {}).get("key") or ""),
                         "project": str((issue.get("project") or {}).get("name") or ""),
                         "labels": ", ".join(labels),
                         "estimate": "" if issue.get("estimate") is None else str(issue.get("estimate")),
                         "due_date": str(issue.get("dueDate") or ""),
                         "previous_identifiers": ", ".join(str(x) for x in issue.get("previousIdentifiers") or []),
                         "archived_at": str(issue.get("archivedAt") or ""),
                         "uuid": str(issue.get("id") or "")},
              "unavailable": unavailable, "origin": origin}
    if partial:
        fields["partial"] = partial
    status = PARTIAL if issue_errors else COMPLETE
    detail = f"fields: Linear answered with {len(issue_errors)} error(s): {'; '.join(sorted(partial))}" \
        if issue_errors else ""
    return fields, status, detail


def _comment(c: dict) -> dict:
    edited = c.get("editedAt")
    return {"id": str(c.get("id")), "author": _who(c.get("user")), "created_at": c.get("createdAt"),
            "edited_at": edited, "edited_by": None,
            # editedAt moves only when the text is edited (updatedAt also moves on a resolution or a reaction)
            "version": edited or f"created:{c.get('createdAt')}", "text": c.get("body") or "", "deleted": False,
            "thread_parent": str((c.get("parent") or {}).get("id") or "") or None, "resolved_at": c.get("resolvedAt"),
            "earlier_text": "unavailable: Linear keeps no earlier text of an edited comment" if edited else None}


def _history(h: dict) -> dict | None:
    changes = []

    def pair(field, before, after):
        if before != after and (before or after):
            changes.append({"field": field, "from": before or "", "to": after or ""})

    if h.get("fromState") is not None or h.get("toState") is not None:
        pair("state", _who(h.get("fromState")), _who(h.get("toState")))
    if h.get("fromAssignee") is not None or h.get("toAssignee") is not None:
        pair("assignee", _who(h.get("fromAssignee")), _who(h.get("toAssignee")))
    if h.get("fromPriority") is not None or h.get("toPriority") is not None:
        pair("priority", _priority(h.get("fromPriority")) if h.get("fromPriority") is not None else "",
             _priority(h.get("toPriority")) if h.get("toPriority") is not None else "")
    if h.get("fromTitle") is not None or h.get("toTitle") is not None:
        pair("title", h.get("fromTitle"), h.get("toTitle"))
    if h.get("fromProject") is not None or h.get("toProject") is not None:
        pair("custom.project", _who(h.get("fromProject")), _who(h.get("toProject")))
    if h.get("fromCycle") is not None or h.get("toCycle") is not None:
        pair("sprint", _cycle(h.get("fromCycle")), _cycle(h.get("toCycle")))
    if h.get("fromTeam") is not None or h.get("toTeam") is not None:
        pair("area", _who(h.get("fromTeam")), _who(h.get("toTeam")))
    if h.get("fromParent") is not None or h.get("toParent") is not None:
        before = (h.get("fromParent") or {}).get("identifier")
        after = (h.get("toParent") or {}).get("identifier")
        if before != after:
            changes.append({"field": "relation", "from": f"parent:{before}" if before else "",
                            "to": f"parent:{after}" if after else ""})
    added = sorted(str(x.get("name")) for x in h.get("addedLabels") or [] if isinstance(x, dict))
    removed = sorted(str(x.get("name")) for x in h.get("removedLabels") or [] if isinstance(x, dict))
    if added or removed:
        changes.append({"field": "custom.labels", "from": ", ".join(removed), "to": ", ".join(added),
                        "note": "labels removed (from) and added (to)"})
    if h.get("updatedDescription"):
        changes.append({"field": "description", "from": "", "to": "",
                        "note": "edited; Linear's history keeps no text of the description"})
    if not changes:
        return None
    return {"id": str(h.get("id")), "author": _who(h.get("actor")), "at": h.get("createdAt"), "changes": changes}


REL_KINDS = {("blocks", False): ("blocks", "blocks"), ("blocks", True): ("blocked-by", "blocked by"),
             ("duplicate", False): ("duplicate", "duplicate of"), ("duplicate", True): ("duplicate", "duplicated by"),
             ("related", False): ("related", "related"), ("related", True): ("related", "related"),
             ("similar", False): ("related", "similar"), ("similar", True): ("related", "similar")}


def _relations(issue: dict, relations: list, inverse: list, children: list) -> list[dict]:
    out = []
    if (issue.get("parent") or {}).get("identifier"):
        out.append({"kind": "parent", "label": "parent", "target": issue["parent"]["identifier"], "external": False})
    for c in children:
        out.append({"kind": "child", "label": "sub-issue", "target": c.get("identifier"), "external": False})
    for items, inverse_side, key in ((relations, False, "relatedIssue"), (inverse, True, "issue")):
        for r in items:
            kind, label = REL_KINDS.get((str(r.get("type") or ""), inverse_side), ("related", str(r.get("type"))))
            target = (r.get(key) or {}).get("identifier")
            if target:
                out.append({"kind": kind, "label": label, "target": target, "external": False})
    seen, unique = set(), []
    for r in out:
        if r["target"] and (r["kind"], r["target"]) not in seen:
            seen.add((r["kind"], r["target"]))
            unique.append(r)
    return unique


def _ref(source, tid: str, issue: dict | None) -> dict:
    ident = str((issue or {}).get("identifier") or tid).upper()
    return {"service": "linear", "org": source.org, "project": ident.rsplit("-", 1)[0], "id": ident}


def normalize(source, bundle: dict) -> dict:
    """One issue: {id, issue | issue_error, issue_errors, comments, history, relations, inverse_relations, children,
    attachments}; each part {items, status, detail}."""
    tid = str(bundle["id"]).upper()
    issue = bundle.get("issue")
    ref = _ref(source, tid, issue)
    url = (issue or {}).get("url") or f"{source.base_url}/issue/{ref['id']}"
    if not issue:
        err = bundle.get("issue_error") or {}
        return failed_snapshot(ref, url, err.get("status") or NOT_READ, err.get("detail") or "issue not provided")
    fields, fields_status, fields_detail = _fields(source, issue, bundle.get("issue_errors") or [])
    parts = {name: bundle.get(name) or part([], NOT_READ, f"{name}: not provided")
             for name in ("comments", "history", "relations", "inverse_relations", "children", "attachments")}
    comments = [_comment(c) for c in parts["comments"]["items"]]
    history = [h for h in (_history(x) for x in parts["history"]["items"]) if h]
    history.sort(key=lambda h: str(h.get("at") or ""))
    attachments = [{"id": str(a.get("id")), "name": a.get("title") or "", "url": a.get("url") or "", "size": None,
                    "created_at": a.get("createdAt"), "source_type": a.get("sourceType") or "", "content_read": False}
                   for a in parts["attachments"]["items"]]
    links = [{"url": a["url"], "kind": link_kind(a["url"]), "label": a["name"], "origin": "attachment",
              "content_read": False} for a in attachments if a["url"]]
    links += links_in_text(fields["description"], "description")
    for c in comments:
        links += links_in_text(c["text"], f"comment:{c['id']}")
    for link in links:
        if any(h in link["url"] for h in UPLOAD_HOSTS):
            link["kind"] = "upload"
    rel_status = worst_status(parts["relations"]["status"], parts["inverse_relations"]["status"],
                              parts["children"]["status"])
    rel_detail = "; ".join(p["detail"] for p in (parts["relations"], parts["inverse_relations"], parts["children"])
                           if p["detail"])
    hist_detail = "; ".join(x for x in (parts["history"]["detail"], HISTORY_NOTE) if x)
    return {"ref": ref, "url": url, "source_revision": issue.get("updatedAt"), "changed_at": issue.get("updatedAt"),
            "components": {"fields": fields, "comments": comments, "history": history,
                           "relations": _relations(issue, parts["relations"]["items"],
                                                   parts["inverse_relations"]["items"], parts["children"]["items"]),
                           "attachments": attachments, "links": dedupe_links([x for x in links if x["url"]])},
            "coverage": {"fields": cover(fields_status, fields_detail),
                         "comments": cover(parts["comments"]["status"], parts["comments"]["detail"]),
                         "history": cover(parts["history"]["status"], hist_detail),
                         "relations": cover(rel_status, rel_detail),
                         "attachments": cover(parts["attachments"]["status"], parts["attachments"]["detail"]),
                         "links": cover(worst_status(parts["attachments"]["status"], parts["comments"]["status"]),
                                        parts["attachments"]["detail"] or parts["comments"]["detail"]),
                         "development": cover(UNSUPPORTED, "Linear shows pull requests and commits only as attachments "
                                                           "and links; no development panel is read")}}


def bundle_from_raw(raw: dict) -> dict:
    """A browser or MCP bundle (technique section 4.3): `issue` is the GraphQL Issue object; every other part is
    {"items": [...], "complete": true|false} or {"error": ...}; a part left out is not_read, never empty."""
    out = {"id": raw.get("id"), "issue": raw.get("issue")}
    if raw.get("issue_error"):
        out["issue_error"] = {"status": {"denied": "denied", "not_found": "not_found"}.get(raw["issue_error"], "failed"),
                              "detail": str(raw.get("detail") or "")}
    for name in ("comments", "history", "relations", "inverse_relations", "children", "attachments"):
        out[name] = bundle_part(raw.get(name), name)
    return out


class LinearSource:
    def __init__(self, source, ctx, limits: dict):
        self.source = source
        self.limits = limits
        self.api = LinearApi(ctx, source.cfg["api_key_var"])

    def _connection(self, tid: str, query: str, field: str, limit: int, what: str) -> dict:
        def fetch(cursor):
            data = self.api.run(query, {"id": tid, "first": PAGE, "after": cursor})
            conn = ((data.get("issue") or {}).get(field)) or {}
            info = conn.get("pageInfo") or {}
            return conn.get("nodes") or [], (info.get("endCursor") if info.get("hasNextPage") else None)

        return read_pages(fetch, limit, what)

    def fetch(self, tid: str) -> dict:
        tid = str(tid).upper()
        try:
            data, errors = self.api.run("Issue", {"id": tid}, allow_partial=True)
        except (TrackerError, ValueError) as e:
            return {"id": tid, "issue": None, "issue_error": {"status": error_status(e), "detail": str(e)}}
        issue = data.get("issue")
        if not issue:
            return {"id": tid, "issue": None, "issue_error": {"status": error_status(_classify(errors, "Issue"))
                                                              if errors else "not_found",
                                                              "detail": "issue not found, or not visible to this key"}}
        key = str(issue.get("id") or tid)       # the UUID: the identifier changes when an issue moves team
        limit = self.limits["max_items"]
        return {"id": tid, "issue": issue,
                "issue_errors": [{"message": str((e or {}).get("message") or "")[:200]} for e in errors],
                "comments": self._connection(key, "IssueComments", "comments", self.limits["max_comments"], "comments"),
                "history": self._connection(key, "IssueHistory", "history", self.limits["max_history"], "history"),
                "relations": self._connection(key, "IssueRelations", "relations", limit, "relations"),
                "inverse_relations": self._connection(key, "IssueInverseRelations", "inverseRelations", limit,
                                                      "inverse relations"),
                "children": self._connection(key, "IssueChildren", "children", limit, "children"),
                "attachments": self._connection(key, "IssueAttachments", "attachments", limit, "attachments")}

    def normalize(self, bundle: dict) -> dict:
        return normalize(self.source, bundle)

    def query(self, text) -> tuple[list[str], str, str]:
        """A scope: an IssueFilter as JSON (Linear has no query language)."""
        issue_filter = parse_filter(text)

        def fetch(cursor):
            data = self.api.run("Issues", {"filter": issue_filter, "first": PAGE, "after": cursor})
            conn = data.get("issues") or {}
            info = conn.get("pageInfo") or {}
            return conn.get("nodes") or [], (info.get("endCursor") if info.get("hasNextPage") else None)

        result = read_pages(fetch, self.limits["max_items"] * 10, "query")
        return [str(i.get("identifier")) for i in result["items"]], result["status"], result["detail"]

    def current_iteration_query(self):
        raise InvalidInput("current_iteration is for Azure DevOps; in Linear write an IssueFilter (e.g. "
                           '{"cycle": {"isActive": {"eq": true}}, "team": {"key": {"eq": "DS"}}})')

    def identity(self) -> str:
        return _who((self.api.run("Viewer") or {}).get("viewer")) or "?"

    def online_check(self) -> list[str]:
        """What the key really reaches, against the configuration: names only, never the key nor an e-mail."""
        data = self.api.run("Viewer")
        lines = [f"access confirmed for {_who(data.get('viewer')) or '?'}"]
        org = str((data.get("organization") or {}).get("urlKey") or "")
        if org.lower() != self.source.org.lower():
            lines.append(f"PROBLEM: the key belongs to the workspace {org or '?'}, not to {self.source.org} "
                         "(workspace_url)")
        teams_conn = data.get("teams") or {}
        teams = {str(t.get("key")).upper() for t in teams_conn.get("nodes") or [] if isinstance(t, dict)}
        wanted = [str(k).upper() for k in self.source.cfg.get("team_keys") or []]
        missing = [k for k in wanted if k not in teams]
        if missing and not (teams_conn.get("pageInfo") or {}).get("hasNextPage"):
            lines.append(f"PROBLEM: the key does not reach the team(s) {', '.join(missing)} of team_keys")
        elif missing:
            lines.append(f"the team(s) {', '.join(missing)} were not among the first {LABELS_PAGE} teams listed: "
                         "not confirmed")
        else:
            lines.append(f"teams reached: {', '.join(sorted(k for k in wanted)) or 'no team_keys configured'}")
        return lines


def parse_filter(text) -> dict:
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(str(text))
    except ValueError as e:
        raise InvalidInput(f"a Linear scope or --query is an IssueFilter written as JSON ({e})") from None
    if not isinstance(value, dict) or not value:
        raise InvalidInput("a Linear scope or --query is a non-empty IssueFilter (a JSON object)")
    return value


__all__ = ["ALLOWED", "LinearSource", "bundle_from_raw", "normalize", "parse_filter", "sections"]
