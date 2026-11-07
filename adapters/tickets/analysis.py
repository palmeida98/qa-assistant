"""
The agent's analysis of a check: validated, then merged into each ticket's memory without duplicates and without going
back in time (docs/technique-tickets.md section 7). The verification prompt's rules are in section 7.1.
"""

from __future__ import annotations

import json
import re

from .model import text_hash

SITUATIONS = ("needs-clarification", "awaiting-implementation-or-build", "verification-candidate",
              "blocked-by-dependency", "retest-or-regression", "has-validation-evidence")
COMMENT_CLASSES = ("decision", "clarification", "request", "blocker", "evidence", "opinion")
ACTION_KINDS = ("local", "external-proposal")
FORBIDDEN_ACTION_KEYS = ("done", "executed", "sent", "applied")
FORBIDDEN_PROMPT_KEYS = ("results", "result", "executed", "passed", "failed", "tested")
LISTS = ("facts", "statements", "inferences", "questions", "actions", "conflicts")
EMPTY = {"qa_situation": None, "facts": [], "statements": [], "inferences": [], "questions": [], "actions": [],
         "conflicts": [], "comment_classes": {}, "content_read": [], "content_unavailable": [], "latest_analysis": None,
         "analysis_log": []}
# a source that names one comment: `comment:9`, a link to `#comment-9`, "comment 9", "comentário 9" (the agent writes in
# the user's language). `component:comments` (the collected list as a whole) is a fact of the collection, not a comment.
_COMMENT = re.compile(r"^comments?\s*:\s*\S*\d|^comment\s*:|#comment-?\d|\bcomments?\s*[#:-]?\s*\d|"
                      r"\bcoment[aá]rios?\s*[#:-]?\s*\d|/comments/\d", re.IGNORECASE)
READY, PARTIAL, BLOCKED = "ready", "partial", "blocked"
READINESS = (READY, PARTIAL, BLOCKED)
BLOCKS = ("all", "part")
RELATIONS = ("confirmed", "candidate", "manual")
# what stands for "we do not know": refused as a value, so nothing unknown passes as known. Also what people write in
# other languages, because this reads what the agent wrote in the user's language
PLACEHOLDERS = {"", "?", "-", "--", "tbd", "tba", "todo", "unknown", "n/a", "na", "none", "null", "to be defined",
                "desconhecido", "por definir"}


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


def _is_comment(source) -> bool:
    """Whether a source names a comment (audit 06.11.2026: a prefix test refused `comments component complete` and let
    `<url>#comment-9` and `ADO comment 9` pass). A list counts when any of its sources does."""
    if isinstance(source, (list, tuple)):
        return any(_is_comment(s) for s in source)
    text = str(source or "").strip()
    if text.lower().startswith("component:"):
        return False
    return bool(_COMMENT.search(text))


def _basis_ok(basis) -> bool:
    return isinstance(basis, list) and bool(basis) and all(isinstance(b, str) and b.strip() for b in basis)


def _sourced(entry: dict, where: str, problems: list[str], facts: str, statements: str, inferences: str) -> None:
    """The three kinds of knowledge, under whatever names the entry gives them: what a comment says is never a fact."""
    for n, item in enumerate(_text_items(entry, facts, where, problems)):
        if not item.get("source"):
            problems.append(f"{where}.{facts}[{n}]: needs a source")
        elif _is_comment(item["source"]):
            problems.append(f"{where}.{facts}[{n}]: what a comment says is a statement until verified")
    for n, item in enumerate(_text_items(entry, statements, where, problems)):
        if not item.get("source"):
            problems.append(f"{where}.{statements}[{n}]: needs a source")
    for n, item in enumerate(_text_items(entry, inferences, where, problems)):
        if not _basis_ok(item.get("basis")):
            problems.append(f"{where}.{inferences}[{n}]: needs a basis, a list of texts")


def validate(doc, known: set[str], check: dict | None = None, skills: set[str] | None = None) -> list[str]:
    """Every problem at once. `known`: the keys and bare ids of the check's tickets. `check` and `skills`: what the
    verification prompt is checked against."""
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
            elif not (isinstance(situation, dict) and _basis_ok(situation.get("basis"))):
                problems.append(f"{where}.qa_situation: needs a basis, a list of texts (audit 06.11.2026: a number "
                                "or a string passed and broke the ticket's memory)")
            elif value == "has-validation-evidence":
                for need in ("scope", "build", "evidence"):
                    if not situation.get(need):
                        problems.append(f"{where}.qa_situation: has-validation-evidence needs {need}")
        _sourced(entry, where, problems, "facts", "statements", "inferences")
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
        seen = json.dumps(check, ensure_ascii=False) if check else ""
        for name, need in (("content_read", "result"), ("content_unavailable", "reason")):
            items = entry.get(name) or []
            if not isinstance(items, list):
                problems.append(f"{where}.{name}: must be a list")
                continue
            for n, item in enumerate(items):
                url = str(item.get("url") or "") if isinstance(item, dict) else ""
                if not url:
                    problems.append(f"{where}.{name}[{n}]: needs its url")
                elif url.lower().startswith("vstfs:"):
                    problems.append(f"{where}.{name}[{n}]: {url} is an identifier, not content: name the page of the "
                                    "PR, commit or build that was opened")
                elif seen and url not in seen:
                    problems.append(f"{where}.{name}[{n}]: {url} is not a link or attachment of this check")
                elif name == "content_unavailable" and not str(item.get(need) or "").strip():
                    problems.append(f"{where}.{name}[{n}]: needs the reason it could not be opened")
    if doc.get("verification_prompt") is not None:
        _validate_prompt(doc["verification_prompt"], known, check or {}, skills, problems)
    return problems


# --- the verification prompt (section 7.1) ---------------------------------------------------------------------------

def readiness(prompt: dict) -> str:
    """What the content allows: never what someone would like it to say."""
    blockers = prompt.get("blockers") or []
    unknown = [prompt[k] for k in ("environment", "build") if (prompt.get(k) or {}).get("unknown")]
    if any(b.get("blocks") == "all" for b in blockers) or any(u.get("blocks_testing") for u in unknown):
        return BLOCKED
    return PARTIAL if blockers or unknown else READY


def _known_or_unknown(value, where: str, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{where}: is required, as {{\"value\": ..., \"source\": ...}} or {{\"unknown\": true, \"why\": "
                        "..., \"blocks_testing\": true|false}}")
    elif value.get("unknown") is True:
        if not str(value.get("why") or "").strip():
            problems.append(f"{where}: unknown needs its why")
        if not isinstance(value.get("blocks_testing"), bool):
            problems.append(f"{where}: unknown needs blocks_testing: true or false")
    elif str(value.get("value") or "").strip().casefold() in PLACEHOLDERS:
        problems.append(f"{where}: {value.get('value')!r} is no value: write {{\"unknown\": true, ...}}, nothing is "
                        "forced to pass")
    elif not value.get("source"):
        problems.append(f"{where}: a known value needs its source")


def _texts(value, where: str, problems: list[str], required: bool = True) -> None:
    if value is None or (required and value == []):
        if required:
            problems.append(f"{where}: is required")
        return
    if not (isinstance(value, list) and all((isinstance(x, str) and x.strip()) or
                                            (isinstance(x, dict) and str(x.get("text") or "").strip()) for x in value)):
        problems.append(f"{where}: must be a list of texts")


def _validate_prompt(prompt, known: set[str], check: dict, skills: set[str] | None, problems: list[str]) -> None:
    where = "verification_prompt"
    if not isinstance(prompt, dict):
        problems.append(f"{where}: must be a map with the fields of docs/technique-tickets.md section 7.1, not a text")
        return
    for bad in FORBIDDEN_PROMPT_KEYS:
        if bad in prompt:
            problems.append(f"{where}.{bad}: refused: this is preparation, and nothing was executed")
    tickets = prompt.get("tickets")
    if not (isinstance(tickets, list) and tickets):
        problems.append(f"{where}.tickets: is required")
    else:
        for t in tickets:
            if str(t) not in known:
                problems.append(f"{where}.tickets: {t} is not a ticket of this check")
    if not str(prompt.get("objective") or "").strip():
        problems.append(f"{where}.objective: is required")
    scope = prompt.get("scope")
    if not isinstance(scope, dict):
        problems.append(f"{where}.scope: is required, as {{\"in\": [...], \"out\": [...]}}")
    else:
        _texts(scope.get("in"), f"{where}.scope.in", problems)
        _texts(scope.get("out"), f"{where}.scope.out", problems, required=False)
    changes = prompt.get("changes")
    if not isinstance(changes, list):
        problems.append(f"{where}.changes: is required (an empty list says there is none)")
    else:
        keys = {c.get("key") for t in check.get("tickets") or [] for c in t.get("changes") or []}
        for n, change in enumerate(changes):
            if isinstance(change, dict) and change.get("key"):
                if change["key"] not in keys:
                    problems.append(f"{where}.changes[{n}]: {change['key']} is not a change of this check")
            elif not (isinstance(change, dict) and change.get("text") and change.get("source")):
                problems.append(f"{where}.changes[{n}]: a change of the check's (key), or a text with its source")
    _sourced({k: prompt.get(k) for k in ("requirements", "statements", "inferences")}, where, problems,
             "requirements", "statements", "inferences")
    if not isinstance(prompt.get("preconditions"), list):
        problems.append(f"{where}.preconditions: is required (an empty list says there is none)")
    else:
        _texts(prompt["preconditions"], f"{where}.preconditions", problems, required=False)
    _known_or_unknown(prompt.get("environment"), f"{where}.environment", problems)
    _known_or_unknown(prompt.get("build"), f"{where}.build", problems)
    blockers = prompt.get("blockers")
    if not isinstance(blockers, list):
        problems.append(f"{where}.blockers: is required (an empty list says there is none)")
    else:
        for n, b in enumerate(blockers):
            if not (isinstance(b, dict) and str(b.get("text") or "").strip()):
                problems.append(f"{where}.blockers[{n}]: needs a text")
            elif b.get("blocks") not in BLOCKS:
                problems.append(f"{where}.blockers[{n}].blocks: {b.get('blocks')!r} outside {list(BLOCKS)}")
    _validate_cases(prompt, check, where, problems)
    wanted = prompt.get("skills")
    if not (isinstance(wanted, list) and wanted):
        problems.append(f"{where}.skills: is required")
    elif skills is not None:
        for s in wanted:
            if str(s).lstrip("/") not in skills:
                problems.append(f"{where}.skills: {s} is not a skill of this framework")
    _texts(prompt.get("evidence"), f"{where}.evidence", problems)
    _texts(prompt.get("done_criteria"), f"{where}.done_criteria", problems)
    if not any(p.startswith(where) for p in problems):
        computed = readiness(prompt)
        stated = prompt.get("readiness")
        if stated not in (None, computed):
            problems.append(f"{where}.readiness: {stated!r}, but its content makes it {computed!r} (technique section "
                            "7.1): fix the content or take it out")


def _validate_cases(prompt: dict, check: dict, where: str, problems: list[str]) -> None:
    found = {"confirmed": set(), "candidate": set()}
    for t in check.get("tickets") or []:
        impact = t.get("impact") or {}
        found["confirmed"] |= {str(c.get("case")) for c in impact.get("confirmed") or []}
        found["candidate"] |= {str(c.get("case")) for c in impact.get("candidates") or []}
    for n, case in enumerate(prompt.get("existing_cases") or []):
        if not (isinstance(case, dict) and case.get("id")):
            problems.append(f"{where}.existing_cases[{n}]: needs its id")
        elif case.get("relation") not in RELATIONS:
            problems.append(f"{where}.existing_cases[{n}].relation: {case.get('relation')!r} outside {list(RELATIONS)}")
        elif case["relation"] == "manual":
            if not case.get("source"):
                problems.append(f"{where}.existing_cases[{n}]: a manual relation needs its source")
        elif str(case["id"]) not in found[case["relation"]]:
            problems.append(f"{where}.existing_cases[{n}]: {case['id']} is not {case['relation']} in this check's "
                            "impact: write relation manual, with its source")
    for n, case in enumerate(prompt.get("proposed_cases") or []):
        if not (isinstance(case, dict) and str(case.get("title") or "").strip()):
            problems.append(f"{where}.proposed_cases[{n}]: needs a title")


# --- merging into a ticket's memory ----------------------------------------------------------------------------------

def merge(current: dict | None, entry: dict, check_ref: dict, analysis_file: str, analyzed_at: str, order) -> dict:
    """A new analysis over the stored one. `check_ref`: {"id", "at"} of its check; `order(ref)`: its place in time.
    An item is identified by its normalised text, so repeating adds nothing. The analysis of a check older than the one
    the ticket's analysis comes from stays with its check: it is logged and changes nothing current."""
    out = {**EMPTY, **(current or {})}
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in out.items()}
    if not any(a.get("file") == analysis_file for a in out["analysis_log"]):
        out["analysis_log"].append({"check": check_ref["id"], "check_at": check_ref["at"], "analyzed_at": analyzed_at,
                                    "file": analysis_file})
    latest = out["latest_analysis"]
    if latest and order(check_ref) < order(latest):
        return out
    out["latest_analysis"] = {"id": check_ref["id"], "at": check_ref["at"]}
    if entry.get("qa_situation"):
        out["qa_situation"] = {**entry["qa_situation"], "check": check_ref["id"]}
    for name in LISTS:
        by_key = {i["key"]: i for i in out[name]}
        for item in entry.get(name) or []:
            key = text_hash(item["text"])
            stored = by_key.get(key)
            if stored is None:
                by_key[key] = {**item, "key": key, "first_check": check_ref["id"], "last_check": check_ref["id"]}
            else:
                merged = {**stored, **item, "key": key, "first_check": stored["first_check"],
                          "last_check": check_ref["id"]}
                if "source" in stored and stored.get("source") != item.get("source", stored.get("source")):
                    # the same text from another source keeps both: an origin is never replaced (audit 06.11.2026)
                    old = stored["source"] if isinstance(stored["source"], list) else [stored["source"]]
                    new = item["source"] if isinstance(item["source"], list) else [item["source"]]
                    merged["source"] = list(dict.fromkeys(old + new))
                by_key[key] = merged
        out[name] = list(by_key.values())
    out["comment_classes"] = {**out["comment_classes"], **{str(k): v for k, v in (entry.get("comments") or {}).items()}}
    read = {i["url"]: i for i in out["content_read"]}
    for item in entry.get("content_read") or []:
        read[item["url"]] = item
    out["content_read"] = list(read.values())
    unavailable = {i["url"]: i for i in out.get("content_unavailable") or [] if i["url"] not in read}
    for item in entry.get("content_unavailable") or []:
        if item["url"] not in read:
            unavailable[item["url"]] = {**item, "check": check_ref["id"]}
    out["content_unavailable"] = list(unavailable.values())
    return out


def open_items(analysis: dict | None, name: str) -> list[dict]:
    return [i for i in (analysis or {}).get(name) or [] if not i.get("resolved")]
