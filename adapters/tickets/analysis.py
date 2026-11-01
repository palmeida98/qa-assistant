"""
The agent's analysis of a check: validated, then merged into each ticket's memory without duplicates
(docs/technique-tickets.md section 7).
"""

from __future__ import annotations

from .model import text_hash

SITUATIONS = ("needs-clarification", "awaiting-implementation-or-build", "verification-candidate",
              "blocked-by-dependency", "retest-or-regression", "has-validation-evidence")
COMMENT_CLASSES = ("decision", "clarification", "request", "blocker", "evidence", "opinion")
ACTION_KINDS = ("local", "external-proposal")
FORBIDDEN_ACTION_KEYS = ("done", "executed", "sent", "applied")
LISTS = ("facts", "statements", "inferences", "questions", "actions", "conflicts")
EMPTY = {"qa_situation": None, "facts": [], "statements": [], "inferences": [], "questions": [], "actions": [],
         "conflicts": [], "comment_classes": {}, "content_read": []}


def _text_items(entry: dict, name: str, where: str, problems: list[str]) -> list[dict]:
    items = entry.get(name) or []
    if not isinstance(items, list):
        problems.append(f"{where}.{name}: must be a list")
        return []
    good = []
    for n, item in enumerate(items):
        if not (isinstance(item, dict) and str(item.get("text") or "").strip()):
            problems.append(f"{where}.{name}[{n}]: needs a text")
        else:
            good.append(item)
    return good


def validate(doc, known: set[str]) -> list[str]:
    """Every problem at once. `known`: the keys and bare ids of the check's tickets."""
    if not isinstance(doc, dict) or not isinstance(doc.get("tickets"), dict):
        return ["the analysis is {\"tickets\": {\"<key or id>\": {...}}}"]
    problems: list[str] = []
    for tid, entry in doc["tickets"].items():
        where = f"tickets.{tid}"
        if tid not in known:
            problems.append(f"{where}: not a ticket of this check")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be a map")
            continue
        situation = entry.get("qa_situation")
        if situation is not None:
            value = situation.get("value") if isinstance(situation, dict) else situation
            if value not in SITUATIONS:
                problems.append(f"{where}.qa_situation: {value!r} outside {list(SITUATIONS)}; these are conclusions, "
                                "not execution statuses nor tracker states")
            elif not (isinstance(situation, dict) and situation.get("basis")):
                problems.append(f"{where}.qa_situation: needs a basis")
            elif value == "has-validation-evidence":
                for need in ("scope", "build", "evidence"):
                    if not situation.get(need):
                        problems.append(f"{where}.qa_situation: has-validation-evidence needs {need}")
        for n, fact in enumerate(_text_items(entry, "facts", where, problems)):
            source = str(fact.get("source") or "")
            if not source:
                problems.append(f"{where}.facts[{n}]: needs a source")
            elif source.startswith("comment"):
                problems.append(f"{where}.facts[{n}]: what a comment says is a statement until verified")
        for n, item in enumerate(_text_items(entry, "statements", where, problems)):
            if not item.get("source"):
                problems.append(f"{where}.statements[{n}]: needs a source")
        for n, item in enumerate(_text_items(entry, "inferences", where, problems)):
            if not item.get("basis"):
                problems.append(f"{where}.inferences[{n}]: needs a basis")
        _text_items(entry, "questions", where, problems)
        for n, item in enumerate(_text_items(entry, "actions", where, problems)):
            if item.get("kind") not in ACTION_KINDS:
                problems.append(f"{where}.actions[{n}].kind: {item.get('kind')!r} outside {list(ACTION_KINDS)}")
            for bad in FORBIDDEN_ACTION_KEYS:
                if bad in item:
                    problems.append(f"{where}.actions[{n}]: {bad!r} refused: this skill proposes, it executes nothing")
        for n, item in enumerate(_text_items(entry, "conflicts", where, problems)):
            if not item.get("sources"):
                problems.append(f"{where}.conflicts[{n}]: needs its sources")
        comments = entry.get("comments") or {}
        if not isinstance(comments, dict):
            problems.append(f"{where}.comments: must be a map comment id -> class")
        else:
            for cid, klass in comments.items():
                if klass not in COMMENT_CLASSES:
                    problems.append(f"{where}.comments.{cid}: {klass!r} outside {list(COMMENT_CLASSES)}")
    return problems


def merge(current: dict | None, entry: dict, check_id: str) -> dict:
    """A new analysis over the stored one: an item is identified by its normalised text, so repeating adds nothing."""
    out = {**EMPTY, **(current or {})}
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in out.items()}
    if entry.get("qa_situation"):
        out["qa_situation"] = {**entry["qa_situation"], "check": check_id}
    for name in LISTS:
        by_key = {i["key"]: i for i in out[name]}
        for item in entry.get(name) or []:
            key = text_hash(item["text"])
            stored = by_key.get(key)
            if stored is None:
                by_key[key] = {**item, "key": key, "first_check": check_id, "last_check": check_id}
            else:
                by_key[key] = {**stored, **item, "key": key, "first_check": stored["first_check"],
                               "last_check": check_id}
        out[name] = list(by_key.values())
    out["comment_classes"] = {**out["comment_classes"], **{str(k): v for k, v in (entry.get("comments") or {}).items()}}
    read = {i["url"]: i for i in out["content_read"]}
    for item in entry.get("content_read") or []:
        read[item["url"]] = item
    out["content_read"] = list(read.values())
    return out


def open_items(analysis: dict | None, name: str) -> list[dict]:
    return [i for i in (analysis or {}).get(name) or [] if not i.get("resolved")]
