"""
What changed between the last reference of a ticket and a new read (docs/technique-tickets.md section 6). Pure: reads
and writes no file. Per component, against the last COMPLETE reference of that component.
"""

from __future__ import annotations

import hashlib

from .model import COMPLETE, PARTIAL

TEXT_FIELDS = ("description", "acceptance_criteria", "title")
LISTED_FIELDS = ("relation", "attachment", "link")      # in the history, for who changed them; compared as lists
COMPARED_FIELDS = ("type", "title", "description", "acceptance_criteria", "state", "reason", "assignee", "priority",
                   "sprint", "area")
# what a change means for QA: a code, which the agent turns into the user's language and judges in context
IMPACT = {"state": ("state-changed", "confirm-build-before-retest"), "reason": ("state-changed", "read-the-reason"),
          "acceptance_criteria": ("requirements-changed", "review-prepared-cases-and-results"),
          "description": ("requirements-changed", "review-prepared-cases-and-results"),
          "title": ("scope-may-have-changed", "read-the-ticket"), "type": ("scope-may-have-changed", "read-the-ticket"),
          "assignee": ("ownership-changed", "note-who-to-ask"), "priority": ("priority-changed", "review-batch-order"),
          "sprint": ("planning-changed", "review-what-this-sprint-covers"), "area": ("planning-changed", "read-the-ticket"),
          "comment": ("new-information", "read-and-classify-the-comment"),
          "relation": ("dependencies-changed", "review-dependencies"),
          "attachment": ("new-artifact", "read-the-artifact"), "link": ("new-artifact", "read-the-artifact"),
          "scope": ("scope-membership-changed", "confirm-with-the-team"),
          "custom": ("project-field-changed", "read-the-ticket")}


def _same(a, b) -> bool:
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def _change(component: str, kind: str, item: str, before, after, impact_key: str, detected_at: str, source: str,
            author=None, changed_at=None, from_partial: bool = False) -> dict:
    digest = hashlib.sha256(f"{component}|{kind}|{item}|{after}".encode()).hexdigest()[:16]
    impact, action = IMPACT[impact_key]
    return {"key": digest, "component": component, "kind": kind, "item": item, "before": before, "after": after,
            "author": author, "changed_at": changed_at, "detected_at": detected_at, "source": source,
            "impact": impact, "action": action, "from_partial": from_partial}


def _who_changed(history: list, field: str, after) -> tuple:
    """The newest change of `field` to this value, when the source gave one."""
    for entry in sorted(history or [], key=lambda h: str(h.get("at") or ""), reverse=True):
        for c in entry.get("changes") or []:
            if c.get("field") == field and _same(c.get("to"), after):
                return entry.get("author"), entry.get("at")
    return None, None


def _fields(old: dict, new: dict, history: list, at: str, url: str) -> list[dict]:
    out = []
    for name in COMPARED_FIELDS:
        if not _same(old.get(name), new.get(name)):
            author, when = _who_changed(history, name, new.get(name))
            out.append(_change("fields", "changed", name, old.get(name), new.get(name), name, at, url, author, when))
    old_custom, new_custom = old.get("custom") or {}, new.get("custom") or {}
    for name in sorted(set(old_custom) | set(new_custom)):
        if not _same(old_custom.get(name), new_custom.get(name)):
            out.append(_change("fields", "changed", f"custom.{name}", old_custom.get(name), new_custom.get(name), "custom",
                               at, url))
    return out


def _edited(before: dict, now: dict) -> bool:
    """The source's version decides, when it gives one: how a text was rendered is not an edit."""
    if before.get("version") is not None and now.get("version") is not None:
        return before["version"] != now["version"]
    return not _same(before.get("text"), now.get("text"))


def _comments(old: list, new: list, complete: bool, at: str, url: str) -> list[dict]:
    out = []
    old_by_id = {c["id"]: c for c in old}
    new_by_id = {c["id"]: c for c in new}
    partial = not complete
    for cid, c in new_by_id.items():
        before = old_by_id.get(cid)
        link = f"{url}#comment-{cid}"
        if c.get("deleted"):
            if before and not before.get("deleted"):
                out.append(_change("comments", "removed", cid, before.get("text"), None, "comment", at, link,
                                   c.get("edited_by") or c.get("author"), c.get("edited_at"), partial))
        elif before is None:
            out.append(_change("comments", "added", cid, None, c.get("text"), "comment", at, link, c.get("author"),
                               c.get("created_at"), partial))
        elif _edited(before, c):
            out.append(_change("comments", "edited", cid, before.get("text"), c.get("text"), "comment", at, link,
                               c.get("edited_by") or c.get("author"), c.get("edited_at"), partial))
    if complete:      # absence is evidence only in a complete listing
        for cid, c in old_by_id.items():
            if cid not in new_by_id and not c.get("deleted"):
                out.append(_change("comments", "removed", cid, c.get("text"), None, "comment", at, f"{url}#comment-{cid}"))
    return out


def _who_moved(history: list, field: str, key: str, side: str) -> tuple:
    """Who added (`to`) or removed (`from`) an item of a list, when the source's history says so."""
    for entry in sorted(history or [], key=lambda h: str(h.get("at") or ""), reverse=True):
        for c in entry.get("changes") or []:
            if c.get("field") == field and str(c.get(side)) == str(key):
                return entry.get("author"), entry.get("at")
    return None, None


def _listed(component: str, impact_key: str, old: list, new: list, identity, complete: bool, at: str, url: str,
            describe, history: list | None = None) -> list[dict]:
    out = []
    old_by_id, new_by_id = {identity(x): x for x in old}, {identity(x): x for x in new}
    for key, item in new_by_id.items():
        if key not in old_by_id:
            author, when = _who_moved(history, impact_key, key, "to")
            out.append(_change(component, "added", key, None, describe(item), impact_key, at, url, author,
                               when or item.get("created_at"), from_partial=not complete))
    if complete:
        for key, item in old_by_id.items():
            if key not in new_by_id:
                author, when = _who_moved(history, impact_key, key, "from")
                out.append(_change(component, "removed", key, describe(item), None, impact_key, at, url, author, when))
    return out


def _transient(old_history: list, new_history: list, reported: set, at: str, url: str) -> list[dict]:
    """A field that moved and came back since the reference: the fields' comparison cannot see it."""
    known = {h["id"] for h in old_history or []}
    fresh: dict[str, list] = {}
    for entry in new_history or []:
        if entry["id"] not in known:
            for c in entry.get("changes") or []:
                if c.get("field") not in reported and c.get("field") not in TEXT_FIELDS + LISTED_FIELDS:
                    fresh.setdefault(c["field"], []).append((entry, c))
    out = []
    for field, moves in fresh.items():
        path = " -> ".join([str(moves[0][1].get("from") or "")] + [str(m[1].get("to") or "") for m in moves])
        last = moves[-1][0]
        impact = field if field in IMPACT else "custom"
        out.append(_change("history", "transient", field, moves[0][1].get("from"), path, impact, at, url,
                           last.get("author"), last.get("at")))
    return out


def compare_ticket(reference: dict | None, snap: dict, at: str) -> dict:
    """{comparison: {component: first|compared|partial|not_compared}, changes, conclusion}."""
    url = snap["url"]
    comps, cov = snap["components"], snap["coverage"]
    ref_comps = (reference or {}).get("components") or {}
    comparison, changes = {}, []
    history_read = comps.get("history") or []

    def state(name):
        status = cov[name]["status"]
        if status not in (COMPLETE, PARTIAL):
            return "not_compared"
        if name not in ref_comps:
            return "first" if status == COMPLETE else "not_compared"
        return "compared" if status == COMPLETE else "partial"

    for name in ("fields", "comments", "history", "relations", "attachments", "links"):
        comparison[name] = state(name)

    def data(name):
        return ref_comps[name]["data"]

    if comparison["fields"] == "compared":
        changes += _fields(data("fields"), comps["fields"], history_read, at, url)
    if comparison["comments"] in ("compared", "partial"):
        changes += _comments(data("comments"), comps["comments"], comparison["comments"] == "compared", at, url)
    def rel_id(r):
        return f"{r['kind']}:{r['target']}"

    if comparison["relations"] in ("compared", "partial"):
        changes += _listed("relations", "relation", data("relations"), comps["relations"], rel_id,
                           comparison["relations"] == "compared", at, url, rel_id, history_read)
    if comparison["attachments"] in ("compared", "partial"):
        changes += _listed("attachments", "attachment", data("attachments"), comps["attachments"],
                           lambda a: str(a.get("id") or a.get("url")), comparison["attachments"] == "compared", at, url,
                           lambda a: a.get("name") or a.get("url"), history_read)
    if comparison["links"] in ("compared", "partial"):
        changes += _listed("links", "link", data("links"), comps["links"], lambda x: x["url"],
                           comparison["links"] == "compared", at, url, lambda x: x["url"], history_read)
    if comparison["history"] in ("compared", "partial") and comparison["fields"] == "compared":
        reported = {c["item"] for c in changes if c["component"] == "fields"}
        changes += _transient(data("history"), history_read, reported, at, url)

    values = set(comparison.values())
    if not ref_comps and values <= {"first", "not_compared"}:
        conclusion = "first" if "first" in values else "not-concluded"
    elif changes:
        conclusion = "changes"
    elif values == {"compared"}:
        conclusion = "no-changes"
    else:
        conclusion = "not-concluded"
    return {"comparison": comparison, "changes": changes, "conclusion": conclusion}


def suggested_situation(snap: dict, entries_by_id: dict) -> dict | None:
    """The engine's conservative suggestion (technique section 7). A closed state never gives validation evidence."""
    if snap["coverage"]["fields"]["status"] != COMPLETE:
        return None
    fields = snap["components"]["fields"]
    blockers = []
    for rel in snap["components"]["relations"]:
        if rel["kind"] == "blocked-by":
            other = entries_by_id.get(str(rel["target"]))
            state = other["snapshot"]["components"]["fields"].get("state_category") if other else None
            if state != "done":
                blockers.append(str(rel["target"]))
    flags = []
    if str(fields.get("state") or "").strip().casefold() in ("removed", "cut", "won't do", "wont do"):
        # withdrawn, not delivered: nothing to verify until someone says why (audit 06.11.2026)
        return {"value": "needs-clarification", "flags": ["removed"], "blockers": blockers}
    if fields.get("state_category") == "done":
        value = "verification-candidate"
        flags.append("closed_without_qa_evidence")
    elif blockers or str(fields.get("state") or "").strip().casefold() == "blocked":
        value = "blocked-by-dependency"
    elif not (fields.get("description") or fields.get("acceptance_criteria")):
        value = "needs-clarification"
    else:
        value = "awaiting-implementation-or-build"
    return {"value": value, "flags": flags, "blockers": blockers}
