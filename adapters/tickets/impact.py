"""
The existing cases a ticket touches (docs/technique-tickets.md section 6.1): the workspace's library, read only. The
workbook goes through ExcelAdapter and is never saved; tracker/cases.json is read as import-cases wrote it.

A configured or recognised column holding the ticket as a value of its own is `confirmed`. Any other mention is a
`candidate`, to confirm. A short number alone in free text is never associated, and finding nothing proves nothing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import zipfile

from adapters.case_headers import TICKET_WORDS, is_ticket_header

from .model import URL_IN_TEXT

COMPLETE, FAILED, NOT_AVAILABLE = "complete", "failed", "not_available"
SEARCHED = ("runs", "cases", "analysis", "batches", "tracker")
SKIPPED_FOLDERS = ("recordings",)
SEARCHED_EXT = (".json", ".md", ".yaml", ".yml", ".txt", ".csv")
MAX_REFERENCES = 20
SNIPPET = 40
LONG_ID = 5         # digits from which a number alone is worth a candidate: real text writes `(91946: ...)`, `91265/91252`
_WORDS = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in sorted(TICKET_WORDS, key=len, reverse=True))
TICKET_PATH = r"/(?:_workitems/edit|edit|workitems|browse)/{tid}(?:[/?#]|$)"


def _as_text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return "\n".join(_as_text(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(_as_text(v) for v in value.values())
    return "" if value is None else str(value)


def _host_of(url: str) -> str:
    return urllib.parse.urlsplit(url or "").netloc.lower()


def _link_to(url: str, tid: str, host: str) -> bool:
    """A link to this very ticket: the same host, and the ticket's id as its own path segment or issue parameter."""
    parts = urllib.parse.urlsplit(url.rstrip(".,;:"))
    if parts.netloc.lower() != host:
        return False
    pattern = TICKET_PATH.format(tid=re.escape(tid))
    return bool(re.search(pattern, parts.path, re.IGNORECASE)) or \
        bool(re.search(rf"(?:^|&)(?:selectedIssue|issueKey)={re.escape(tid)}(?:&|$)", parts.query, re.IGNORECASE))


def in_link_field(value, tid: str, host: str) -> bool:
    """The ticket as a value of its own in a column made for it: `123`, `#123`, `AB#123`, `DS-7`, a list of them, or a
    link to it. A longer number, or a number inside another value, is not it."""
    text = _as_text(value)
    if any(_link_to(u, tid, host) for u in URL_IN_TEXT.findall(text)):
        return True
    for token in re.split(r"[\s,;|/]+", URL_IN_TEXT.sub(" ", text)):
        token = re.sub(r"^(?:AB)?#", "", token.strip("()[]{}.:'\""), flags=re.IGNORECASE)
        if token.casefold() == tid.casefold():
            return True
    return False


def mention(text: str, tid: str, host: str) -> tuple[str, str] | None:
    """(why, snippet) when free text mentions the ticket, or None. A key needs clean boundaries; a short number needs
    its marker (#123, AB#123, ticket 123, work item 123) or a link: `123` alone is never this ticket. A long number
    alone is a candidate, as a value of its own: never part of a longer number, a date, a version or an amount."""
    text = _as_text(text)
    for url in URL_IN_TEXT.findall(text):
        if _link_to(url, tid, host):
            return "a link to the ticket", _snippet(text, text.find(url), len(url))
    rest = URL_IN_TEXT.sub(lambda m: " " * len(m.group(0)), text)
    tail = r"(?![\w-]|[.,/]\d)"
    if tid.isdigit():
        patterns = ((rf"(?<![\w#.\-/])(?:AB)?#{tid}{tail}", "#id"),
                    (rf"(?<![\w-])(?:{_WORDS})[\s:#\-\"']*{tid}{tail}", "named with its id"))
        if len(tid) >= LONG_ID:
            patterns += ((rf"(?<![\w#.,\-]){tid}(?![\w\-]|[.,]\d)", "a long number alone"),)
    else:
        patterns = ((rf"(?<![\w-]){re.escape(tid)}{tail}", "its key"),)
    for pattern, why in patterns:
        m = re.search(pattern, rest, re.IGNORECASE)
        if m:
            return why, _snippet(text, m.start(), m.end() - m.start())
    return None


def _snippet(text: str, start: int, size: int) -> str:
    return " ".join(text[max(0, start - SNIPPET):start + size + SNIPPET].split())


class Library:
    """The cases of the workspace, each {case, where, fields: {name: value}}, and what was searched."""

    def __init__(self, status: str, source: str, detail: str, rows: list[dict], link_fields: list[str]):
        self.status, self.source, self.detail, self.rows = status, source, detail, rows
        self.link_fields = {str(f).casefold() for f in link_fields}

    def public(self) -> dict:
        return {"status": self.status, "source": self.source, "detail": self.detail, "cases": len(self.rows),
                "link_fields": sorted(self.link_fields)}

    def _is_link_field(self, name: str) -> bool:
        return str(name).casefold() in self.link_fields or is_ticket_header(name)

    def search(self, tid: str, url: str, title: str = "") -> dict:
        """{"confirmed": [...], "candidates": [...]} for one ticket. A case appears once, confirmed first. The ticket's
        title in a column made for tickets is a candidate: titles are not unique, and they change."""
        host = _host_of(url)
        wanted = " ".join(str(title or "").split()).casefold()
        confirmed, candidates = [], []
        for row in self.rows:
            hit = next(({"case": row["case"], "where": row["where"], "field": name, "value": _as_text(value)[:120]}
                        for name, value in row["fields"].items()
                        if self._is_link_field(name) and in_link_field(value, tid, host)), None)
            if hit:
                confirmed.append(hit)
                continue
            for name, value in row["fields"].items():
                if self._is_link_field(name):
                    if wanted and " ".join(_as_text(value).split()).casefold() == wanted:
                        candidates.append({"case": row["case"], "where": row["where"], "field": name,
                                           "why": "the ticket's title", "snippet": _as_text(value)[:120]})
                        break
                    continue
                found = mention(value, tid, host)
                if found:
                    candidates.append({"case": row["case"], "where": row["where"], "field": name, "why": found[0],
                                       "snippet": found[1]})
                    break
        return {"confirmed": confirmed, "candidates": candidates}


def _excel(workspace: str, link_fields: list[str]) -> Library:
    from adapters.excel import ExcelAdapter
    try:
        adapter = ExcelAdapter.from_project(workspace)
        adapter.load()      # openpyxl reads the whole file and closes it: nothing is ever saved here
        cfg = adapter.cfg["cases"]
        id_column = cfg["columns"]["id"]
        rows = [{"case": str(row.get(id_column)), "where": sheet,
                 "fields": {str(k): v for k, v in row.items() if k not in (None, "_row", id_column) and v not in (None, "")}}
                for sheet, sheet_rows in adapter.case_rows().items() for row in sheet_rows]
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as e:
        return Library(FAILED, "workbook", f"the workbook could not be read: {e}", [], link_fields)
    return Library(COMPLETE, "workbook", f"{len(rows)} case(s) in {os.path.basename(adapter.path)}", rows, link_fields)


def _tracker_cases(workspace: str, link_fields: list[str]) -> Library:
    path = os.path.join(workspace, "tracker", "cases.json")
    if not os.path.isfile(path):
        return Library(NOT_AVAILABLE, "tracker/cases.json", "it does not exist: import-cases writes it", [], link_fields)
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
        rows = [{"case": str(c.get("id")), "where": "tracker/cases.json",
                 "fields": {str(k): v for k, v in c.items() if k != "id" and v not in (None, "", [])}}
                for c in doc.get("cases") or [] if isinstance(c, dict) and c.get("id")]
    except (OSError, ValueError, AttributeError) as e:
        return Library(FAILED, "tracker/cases.json", f"it could not be read: {e}", [], link_fields)
    return Library(COMPLETE, "tracker/cases.json", f"{len(rows)} case(s), as last imported", rows, link_fields)


def load_library(workspace: str, proj: dict) -> Library:
    tickets = proj.get("tickets") if isinstance(proj.get("tickets"), dict) else {}
    link_fields = (tickets.get("cases") or {}).get("link_fields") or []
    tracker = proj.get("tracker")
    if tracker == "excel":
        return _excel(workspace, link_fields)
    if tracker in ("jira", "azure-devops"):
        return _tracker_cases(workspace, link_fields)
    return Library(NOT_AVAILABLE, "", f"tracker {tracker!r}: no library to search", [], link_fields)


def find_references(workspace: str, tid: str, url: str) -> list[str]:
    """Run logs, batches and case files that mention the ticket, by the same rule: possibly affected, for the agent to
    judge. Inside the workspace only; a link is not followed."""
    host = _host_of(url)
    found: list[str] = []
    for folder in SEARCHED:
        for root, dirs, files in os.walk(os.path.join(workspace, folder)):
            dirs[:] = [d for d in dirs if d not in SKIPPED_FOLDERS]
            for name in sorted(files):
                if not name.endswith(SEARCHED_EXT):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        if mention(f.read(), tid, host):
                            found.append(os.path.relpath(path, workspace).replace("\\", "/"))
                except OSError:
                    continue
                if len(found) >= MAX_REFERENCES:
                    return found
    return found
