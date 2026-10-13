"""
Intake of test cases (docs/qa-ui.md section 4): pasted text, a file, or the workspace's library.

Read only. Every case comes out with the fields of the model the source gave (methodology 2) and its flags; a line
that is not a case is kept as a note and never turned into one (methodology 5.3: a new case is a proposal).

    intake(workspace, proj, text=..., file={"name", "data"}, library=True)
        -> {"cases": [...], "notes": [...], "dropped": [...], "library": {...}}
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import os
import re
import zipfile
from xml.etree import ElementTree

MAX_FILE = 5 * 1024 * 1024          # an uploaded file (docs/qa-ui.md section 4)
MAX_XML = 40 * 1024 * 1024          # the document inside a .docx, once unzipped
MAX_ROWS = 20000

FIELDS = ("id", "title", "expected", "steps", "preconditions", "role", "platform", "area", "priority", "module",
          "technique")
HEADERS = {
    "id": ("id", "case", "case id", "test case id", "test id", "tc", "key"),
    "title": ("title", "name", "summary", "test case", "case title"),
    "expected": ("expected", "expected result", "expected results"),
    "steps": ("steps", "step", "test steps", "action", "actions"),
    "preconditions": ("preconditions", "precondition", "pre conditions", "pre condition"),
    "role": ("role", "profile", "user role"),
    "platform": ("platform",),
    "area": ("area", "feature"),
    "priority": ("priority",),
    "module": ("module", "test module"),
    "technique": ("technique", "design technique"),
}
_HEADER_OF = {name: field for field, names in HEADERS.items() for name in names}
DEFAULT_ORDER = {0: "id", 1: "title", 2: "expected"}

ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
ARROW = re.compile(r"\s*(?:→|->|=>)\s*")
RULE = re.compile(r":?-{2,}:?")
BULLET = re.compile(r"(?:[-*•]|#{1,6})\s+")
ENUMERATED = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
# an expected result that names no observable outcome (flag vague-expected, with six words or fewer)
VAGUE = re.compile(r"\b(?:as expected|works?(?: fine| well| correctly| properly)?|correctly|properly|"
                   r"appropriate(?:ly)?|generic error|tbd|to be defined)\b", re.IGNORECASE)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class IntakeError(ValueError):
    """The input cannot be read (shown to the user as it is)."""


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------


def norm(header) -> str:
    return re.sub(r"[\s_-]+", " ", str(header or "")).strip().lower()


def is_id(token: str, known=frozenset()) -> bool:
    """A case ID: letters, digits, '.', '_', '-', starting with a letter and holding a digit; or exactly an ID of
    the workspace's library (e.g. an Azure DevOps work item number)."""
    return bool(token) and (token in known or bool(ID.fullmatch(token) and any(c.isdigit() for c in token)))


def text_of(value) -> str:
    """A field as text: the steps of the model ([{action, data, expected}]) become numbered lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        lines = []
        for n, step in enumerate(value, 1):
            if isinstance(step, dict):
                line = str(step.get("action") or "").strip()
                if step.get("data") not in (None, ""):
                    line += f" [{str(step['data']).strip()}]"
                if step.get("expected") not in (None, ""):
                    line += f" → {str(step['expected']).strip()}"
            else:
                line = str(step).strip()
            if line:
                lines.append(f"{n}. {line}")
        return "\n".join(lines)
    return str(value).strip()


def content_flags(case: dict) -> list[str]:
    expected = case["expected"]
    flags = []
    if not expected:
        flags.append("no-expected")
    elif VAGUE.search(expected) and len(expected.split()) <= 6:
        flags.append("vague-expected")
    if len(ENUMERATED.findall(expected)) >= 2:
        flags.append("several-expected")
    return flags


def new_case(values: dict, source: str, area: str | None = None) -> dict:
    case = {f: text_of(values.get(f)) for f in FIELDS}
    if area and not case["area"]:
        case["area"] = area
    case.update(source=source, last_status=text_of(values.get("last_status")), in_library=None)
    case["flags"] = content_flags(case)
    return case


# ---------------------------------------------------------------------------
# Text and rows
# ---------------------------------------------------------------------------


def _cells(line: str) -> list[str] | None:
    """The cells of a line separated by a tab or '|' (a Markdown table row); None for a plain line."""
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    if "|" in line:
        parts = [c.strip() for c in line.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        return parts
    return None


def header_map(cells) -> dict[int, str] | None:
    """{column: field} when the cells are a header row (it names at least the ID and the title)."""
    found = {}
    for i, c in enumerate(cells):
        field = _HEADER_OF.get(norm(c))
        if field and field not in found.values():
            found[i] = field
    return found if {"id", "title"} <= set(found.values()) else None


def _row_values(cells, mapping: dict[int, str]) -> dict:
    return {field: cells[i] for i, field in mapping.items() if i < len(cells) and cells[i] not in (None, "")}


def rows_to_cases(rows, known=frozenset(), source: str = "file", area: str | None = None,
                  need_header: bool = False) -> tuple[list[dict], list[str]]:
    """Cases of a table (a .csv, a sheet, a table of a .docx). A header row names the columns; without one the
    order is ID, title, expected result (unless need_header: then the rows before a header are ignored)."""
    cases, notes = [], []
    mapping = None
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        if not any(cells):
            continue
        header = header_map(cells)
        if header:
            mapping = header
            continue
        if mapping is None and need_header:
            continue
        values = _row_values(cells, mapping or DEFAULT_ORDER)
        if is_id(str(values.get("id", "")), known) and values.get("title"):
            cases.append(new_case(values, source, area))
        elif not need_header:
            notes.append(" | ".join(c for c in cells if c))
    return cases, notes


def parse_text(text: str, known=frozenset(), source: str = "text") -> tuple[list[dict], list[str]]:
    """One case per line: `<ID> <title> → <expected>` (also -> and =>), or cells separated by a tab or '|', with an
    optional header line. A line without an ID is a note."""
    cases, notes = [], []
    mapping = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        cells = _cells(line)
        if cells is not None and len(cells) > 1:
            if all(RULE.fullmatch(c) for c in cells if c):
                continue                                    # the rule under a Markdown table's header
            header = header_map(cells)
            if header:
                mapping = header
                continue
            values = _row_values(cells, mapping or DEFAULT_ORDER)
            if is_id(values.get("id", ""), known) and values.get("title"):
                cases.append(new_case(values, source))
            else:
                notes.append(" | ".join(c for c in cells if c))
            continue
        line = BULLET.sub("", line, count=1) if BULLET.match(line) else line
        token, _, rest = line.partition(" ")
        token, rest = token.rstrip(":"), rest.strip()
        if is_id(token, known) and rest:
            parts = ARROW.split(rest, maxsplit=1)
            title = parts[0].strip(" -–—:")
            if title:
                cases.append(new_case({"id": token, "title": title,
                                       "expected": parts[1] if len(parts) > 1 else ""}, source))
                continue
        notes.append(line)
    return cases, notes


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_csv(text: str, delimiter: str | None, known=frozenset()) -> tuple[list[dict], list[str]]:
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = []
    for n, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        if n >= MAX_ROWS:
            break
        rows.append(row)
    return rows_to_cases(rows, known)


def parse_xlsx(data: bytes, known=frozenset()) -> tuple[list[dict], list[str]]:
    """Every sheet with a header row that has an ID and a title column; the area is the sheet."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as e:   # what openpyxl raises for a non-workbook
        raise IntakeError(f"not a readable .xlsx ({type(e).__name__})") from None
    cases = []
    try:
        for ws in wb.worksheets:
            rows = []
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                if n >= MAX_ROWS:
                    break
                rows.append(row)
            cases.extend(rows_to_cases(rows, known, area=ws.title, need_header=True)[0])
    finally:
        wb.close()
    return cases, []


def _docx_text(node) -> str:
    out = []
    for el in node.iter():
        if el.tag == W + "t":
            out.append(el.text or "")
        elif el.tag == W + "tab":
            out.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


def parse_docx(data: bytes, known=frozenset()) -> tuple[list[dict], list[str]]:
    """The paragraphs (as pasted text) and the tables (as rows) of a .docx, in the document's order."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_XML:
                raise IntakeError("the .docx is too large to read")
            raw = z.read(info)
    except (zipfile.BadZipFile, KeyError):
        raise IntakeError("not a readable .docx") from None
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise IntakeError("the .docx declares XML entities: refused")
    try:
        body = ElementTree.fromstring(raw).find(W + "body")
    except ElementTree.ParseError:
        raise IntakeError("not a readable .docx") from None
    cases, notes, lines = [], [], []

    def flush():
        if lines:
            c, n = parse_text("\n".join(lines), known, "file")
            cases.extend(c)
            notes.extend(n)
            lines.clear()

    for child in list(body) if body is not None else []:
        if child.tag == W + "p":
            lines.extend(_docx_text(child).splitlines())
        elif child.tag == W + "tbl":
            flush()
            rows = [["\n".join(_docx_text(p) for p in cell.findall(W + "p")).strip() for cell in tr.findall(W + "tc")]
                    for tr in child.findall(W + "tr")]
            c, n = rows_to_cases(rows, known)
            cases.extend(c)
            notes.extend(n)
    flush()
    return cases, notes


def read_file(name: str, data: str, known=frozenset()) -> tuple[list[dict], list[str]]:
    """An uploaded file ({"name", "data": base64}), read in memory and never stored."""
    try:
        raw = base64.b64decode(data or "", validate=True)
    except (binascii.Error, ValueError):
        raise IntakeError("the file did not arrive whole (bad base64)") from None
    if len(raw) > MAX_FILE:
        raise IntakeError(f"the file has more than {MAX_FILE // (1024 * 1024)} MB")
    ext = os.path.splitext(str(name or ""))[1].lower()
    if ext == ".xlsx":
        return parse_xlsx(raw, known)
    if ext == ".docx":
        return parse_docx(raw, known)
    if ext in (".csv", ".tsv"):
        return parse_csv(_decode(raw), "\t" if ext == ".tsv" else None, known)
    if ext in (".txt", ".md"):
        return parse_text(_decode(raw), known, "file")
    if ext == ".pdf":
        raise IntakeError(".pdf is not read: export the document to .docx, .xlsx or text (docs/qa-ui.md section 13)")
    raise IntakeError(f"{ext or 'a file without an extension'} is not read: .csv, .tsv, .txt, .md, .xlsx or .docx")


# ---------------------------------------------------------------------------
# The workspace's library
# ---------------------------------------------------------------------------


def load_library(workspace: str, proj: dict) -> dict:
    """{"source", "cases", "detail", "error"}: the cases the project already has. Excel: the workbook through the
    adapter (read only). Jira / Azure DevOps: tracker/cases.json as import-cases wrote it (never refreshed here)."""
    tracker = proj.get("tracker")
    if tracker == "excel":
        from adapters.excel import ExcelAdapter
        try:
            ad = ExcelAdapter.from_project(workspace)
            ad.load()
            cfg = ad.cfg["cases"]
            by_header = {str(header): field for field, header in (cfg.get("fields") or {}).items()}
            cases = []
            for sheet, rows in ad.case_rows().items():
                for row in rows:
                    values = {}
                    for header, value in row.items():
                        if header in (None, "_row"):
                            continue
                        field = ("id" if header == cfg["columns"]["id"] else
                                 by_header.get(str(header)) or _HEADER_OF.get(norm(header)))
                        if field and field not in values:
                            values[field] = value
                    values.update(area=sheet, last_status=row.get(cfg["columns"]["status"]))
                    cases.append(new_case(values, "library"))
            return {"source": "workbook", "cases": cases, "error": None,
                    "detail": f"{len(cases)} case(s) in {os.path.basename(ad.path)}"}
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as e:
            # a configuration (ConfigError is a ValueError), a missing file or an unreadable workbook: said, never hidden
            return {"source": "workbook", "cases": [], "detail": "", "error": f"the workbook could not be read: {e}"}
    if tracker in ("jira", "azure-devops"):
        path = os.path.join(workspace, "tracker", "cases.json")
        if not os.path.isfile(path):
            return {"source": "tracker/cases.json", "cases": [], "detail": "",
                    "error": "tracker/cases.json does not exist: python -m adapters.tracker <workspace> import-cases"}
        try:
            with open(path, encoding="utf-8-sig") as f:
                doc = json.load(f)
            cases = [new_case(c, "library") for c in doc.get("cases") or [] if isinstance(c, dict) and c.get("id")]
        except (OSError, ValueError, AttributeError) as e:
            return {"source": "tracker/cases.json", "cases": [], "detail": "",
                    "error": f"tracker/cases.json could not be read: {e}"}
        return {"source": "tracker/cases.json", "cases": cases, "error": None,
                "detail": f"{len(cases)} case(s), imported at {doc.get('imported_at') or 'an unknown date'}"}
    return {"source": "", "cases": [], "detail": "", "error": f"tracker {tracker!r}: no library to read"}


def annotate(cases: list[dict], library: dict) -> None:
    """Marks each case as in the library or not. A case that is in it gets from the library the fields the input
    did not give (a list of IDs and titles is a way of selecting cases), and its flags are computed again."""
    if library.get("error"):
        return
    by_id = {c["id"]: c for c in library["cases"]}
    for case in cases:
        known = by_id.get(case["id"])
        case["in_library"] = known is not None
        if known is None:
            if "not-in-library" not in case["flags"]:
                case["flags"].append("not-in-library")
            continue
        for field in (*FIELDS, "last_status"):
            if not case[field]:
                case[field] = known[field]
        kept = [f for f in case["flags"] if f == "duplicate-id"]
        case["flags"] = content_flags(case) + kept


def merge(groups) -> tuple[list[dict], list[str]]:
    """The first case of each ID, flagged duplicate-id when the ID came again; the IDs dropped, once each."""
    seen, dropped = {}, []
    for group in groups:
        for case in group:
            first = seen.get(case["id"])
            if first is None:
                seen[case["id"]] = case
            else:
                if "duplicate-id" not in first["flags"]:
                    first["flags"].append("duplicate-id")
                if case["id"] not in dropped:
                    dropped.append(case["id"])
    return list(seen.values()), dropped


def intake(workspace: str, proj: dict, text: str | None = None, file: dict | None = None,
           library: bool = False) -> dict:
    lib = load_library(workspace, proj)
    known = frozenset(c["id"] for c in lib["cases"])
    groups, notes = [], []
    if text:
        c, n = parse_text(text, known)
        groups.append(c)
        notes.extend(n)
    if file:
        c, n = read_file(file.get("name"), file.get("data"), known)
        groups.append(c)
        notes.extend(n)
    cases, dropped = merge(groups)
    if library:   # the library's cases the input did not name: the same case twice is not a duplicate in the input
        given = {c["id"] for c in cases}
        cases.extend(dict(c, flags=list(c["flags"])) for c in lib["cases"] if c["id"] not in given)
    annotate(cases, lib)
    return {"cases": cases, "notes": notes, "dropped": dropped,
            "library": {"source": lib["source"], "count": len(lib["cases"]), "detail": lib["detail"],
                        "error": lib["error"]}}
