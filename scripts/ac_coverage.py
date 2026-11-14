"""Acceptance criteria of a PBI mapped to the cases that cover them (plan Q3, 10.11.2026).

    python scripts/ac_coverage.py <workspace> init <pbi> [--out analysis/ac_map_<pbi>.json]
    python scripts/ac_coverage.py <workspace> report <map.json> [--json]

`init` reads the PBI's acceptance criteria from the ticket memory the tickets technique keeps
(`memory/tickets/*__<pbi>.json`, `components.fields.data.acceptance_criteria`) and writes a map with one criterion per
block (a block ends at a blank line or where a new «Dado que»/«Given» starts, never inside a table or a fenced block) and
NO cases. Each criterion's `text` is a TRANSCRIPTION (rules.md section 17): the original lines, their breaks, indentation,
list markers and table rows as the tracker has them; `criteria_source.text` keeps the whole field. Each criterion names
its origin (ticket, link, field or section, lines) and how it may be shown: `inline` when it is one short line, else
`reference` (a precise reference in a table cell, and the full text in its own block). The splitting is a reading aid,
not a statement of the tracker. The mapping of cases is a decision of the mapping is a decision of
the person who plans (a case covers a criterion because of what it checks, not because of its title). When the ticket
has no criteria, or no ticket memory exists, the map says so: `criteria_source.kind` is `none` and the report says that
the SOURCE OF THE CRITERIA IS NOT AVAILABLE (coverage cannot be judged); criteria written from the tasks or from a spec
are added by hand with `criteria_source.kind: "inferred"` and a note, and the report says they are inferred.

`report` reads the map, the workbook and the run logs of the workspace (read only) and shows, per criterion, the cases that
cover it with THREE things apart: the status in the WORKBOOK (the applied, historical record), the LATEST result in the run
logs (a Question or a correction that was not applied shows only there; its batch, time and evidence are named) and a note
in the workbook that marks the result UNDER REVIEW. One verdict per criterion: `uncovered`, `not run`, `passed`,
`passed, not confirmed` (a mapped case is under review or its latest run-log result differs from the workbook),
`passed, required check open` (the criterion's own cases pass but a RELATED verification marked `required` is not a pass),
`partly passed`, `open` (a Question, Blocked or Failed case). Each association has a short `why`; a criterion has its own
`source` (ticket or `inferred: <task>`); `related` lists verifications of the same PBI that are not the criterion's text. A report NEVER shows complete coverage while a mapped result is
under review or divergent: its summary says `complete: false` and why. `--no-run-logs` reads only the workbook, and the
report then says that it does not see a Question or a correction that exists only in a run log. Exit 0 when every criterion
is covered, its source is known and nothing is under review; 1 otherwise. It never writes to the workbook.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

GIVEN = re.compile(r"^\s*(?:Dado|Given)\b", re.IGNORECASE)
INLINE_MAX = 200        # characters: a longer criterion, or one with several lines, is shown by reference (rules.md 17)
STRUCTURE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||```|~~~)")
LINES = re.compile(r"lines (\d+)-(\d+)")
UNDER_REVIEW = re.compile(r"sob revis[ãa]o|under review", re.IGNORECASE)


def split_blocks(text: str) -> list[dict]:
    """The criteria of a field, word for word: {text, first, last} with the 1-based lines of the field. A blank line ends
    a block and a line that starts with Dado/Given starts one, except inside a fenced block (``` or ~~~) or a table (rows
    that start with |). Nothing is trimmed, joined or re-indented: only the blank lines between blocks are not in one."""
    blocks, cur, start, fence = [], [], 0, False
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for n, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fence = not fence
        in_table = bool(cur) and stripped.startswith("|") and cur[-1].strip().startswith("|")
        if not fence and not stripped.startswith(("```", "~~~")) and not in_table:
            if not stripped:
                if cur:
                    blocks.append({"text": "\n".join(cur), "first": start, "last": n - 1})
                    cur = []
                continue
            if cur and GIVEN.match(line):
                blocks.append({"text": "\n".join(cur), "first": start, "last": n - 1})
                cur = []
        if not cur:
            start = n
        cur.append(line)
    if cur:
        while cur and not cur[-1].strip():
            cur.pop()
        blocks.append({"text": "\n".join(cur), "first": start, "last": start + len(cur) - 1})
    return blocks


def split_criteria(text: str) -> list[str]:
    """The text of each criterion, word for word (split_blocks)."""
    return [b["text"] for b in split_blocks(text)]


def _line_map(origin: str | None, field_text: str) -> list[int | None] | None:
    """Line of the field -> line of the ticket's description, from an origin such as "description, section 'X', lines
    5-7; description, section 'Y', lines 12-13" (sections joined by one blank line). None when it does not add up."""
    spans = [(int(a), int(b)) for a, b in LINES.findall(str(origin or ""))]
    if not spans:
        return None
    out: list[int | None] = []
    for i, (a, b) in enumerate(spans):
        if i:
            out.append(None)
        out += list(range(a, b + 1))
    return out if len(out) == len(field_text.split("\n")) else None


def presentation(text: str) -> str:
    """inline: one short line without list, table or code; reference: anything else (rules.md section 17)."""
    lines = text.split("\n")
    if len(lines) == 1 and len(text) <= INLINE_MAX and not STRUCTURE.match(text):
        return "inline"
    return "reference"


def _criteria(field_text: str, ticket: str | None, url: str | None, origin: str | None) -> list[dict]:
    mapping = _line_map(origin, field_text)
    out = []
    for i, b in enumerate(split_blocks(field_text), 1):
        cid = f"AC{i}"
        if mapping and mapping[b["first"] - 1] and mapping[b["last"] - 1]:
            where = f"description, lines {mapping[b['first'] - 1]}-{mapping[b['last'] - 1]}"
        else:
            where = f"acceptance criteria field, lines {b['first']}-{b['last']}"
        ref = " · ".join(x for x in (str(ticket) if ticket else "", cid, where) if x)
        out.append({"id": cid, "text": b["text"], "cases": [], "presentation": presentation(b["text"]),
                    "origin": {"ticket": ticket, "url": url, "where": where, "split": "blank line or Dado/Given"},
                    "reference": ref + (f" ({url})" if url else "")})
    return out


def ticket_memory(workspace: str, pbi: int | str) -> str | None:
    """The memory file of the PBI: its name ends with the id as the tickets technique writes it (lower case, ticket_slug),
    so a key such as DS-12 is found on a case-sensitive disk too."""
    from adapters.tickets.model import slug
    names = dict.fromkeys((str(pbi), slug(str(pbi))))
    found = sorted({p for name in names
                    for p in glob.glob(os.path.join(workspace, "memory", "tickets", f"*__{glob.escape(name)}.json"))})
    return found[0] if found else None


def init(workspace: str, pbi: int | str) -> dict:
    ref = ticket_memory(workspace, pbi)
    criteria, title, note, extra, field_text = [], "", "", {}, ""
    if ref:
        with open(ref, encoding="utf-8") as f:
            data = json.load(f)
        fields = ((data.get("components") or {}).get("fields") or {}).get("data") or {}
        title = str(fields.get("title") or "")
        field_text = str(fields.get("acceptance_criteria") or "")
        unavailable = fields.get("unavailable") if isinstance(fields.get("unavailable"), dict) else {}
        origin = fields.get("origin") if isinstance(fields.get("origin"), dict) else {}
        criteria = _criteria(field_text, (data.get("ref") or {}).get("id"), data.get("url"),
                             origin.get("acceptance_criteria"))
        # where the criteria come from in the tracker, for the plan to cite (rules.md section 17)
        extra = {k: v for k, v in (("url", data.get("url")), ("ticket", (data.get("ref") or {}).get("id")),
                                   ("origin", origin.get("acceptance_criteria"))) if v}
        if not criteria:
            note = ("the ticket's acceptance criteria are unavailable: " + str(unavailable["acceptance_criteria"])
                    if "acceptance_criteria" in unavailable else "the ticket has no acceptance criteria")
    else:
        note = "no ticket memory for this PBI"
    kind = "ticket-memory" if criteria else "none"
    return {"pbi": int(pbi) if str(pbi).isdigit() else str(pbi), "title": title,
            "criteria_source": {"kind": kind, "ref": os.path.relpath(ref, workspace) if ref else None, "note": note,
                                **extra, **({"text": field_text} if criteria else {})},
            "criteria": criteria}


def _workbook_of(workspace: str, platform: str | None) -> str | None:
    """The file of the `excel.workbooks` entry of `platform` (its `platform`, else its key), or None (= the default one)."""
    if not platform:
        return None
    from common import read_project

    from adapters.excel.base import workbook_patterns
    excel = (read_project(workspace).get("excel") or {})
    for key, file, _pats in workbook_patterns(excel):
        if str(((excel.get("workbooks") or {}).get(key) or {}).get("platform") or key).lower() == platform.lower():
            return file
    return None


def known_platforms(workspace: str) -> list[str]:
    """The platforms the project names: `report.platforms` and the `platform` (else the key) of each `excel.workbooks` entry."""
    from common import read_project

    from adapters.excel.base import workbook_patterns
    proj = read_project(workspace)
    excel = proj.get("excel") if isinstance(proj.get("excel"), dict) else {}
    names = [str(k).lower() for k in ((proj.get("report") or {}).get("platforms") or {})] \
        if isinstance(proj.get("report"), dict) else []
    names += [str(((excel.get("workbooks") or {}).get(k) or {}).get("platform") or k).lower()
              for k, _f, _p in workbook_patterns(excel)]
    return list(dict.fromkeys(names))


def several_workbooks(workspace: str) -> bool:
    from common import read_project

    from adapters.excel.base import workbook_patterns
    excel = read_project(workspace).get("excel")
    return bool(workbook_patterns(excel if isinstance(excel, dict) else {}))


def _library(workspace: str, platform: str | None = None):
    from adapters.excel.base import ExcelAdapter
    ad = ExcelAdapter.from_project(workspace, file=_workbook_of(workspace, platform))
    ad.load()
    cols = ad.cfg["cases"]["columns"]
    out = {}
    for rows in ad.case_rows().values():
        for r in rows:
            out[str(r.get(cols["id"]))] = {"status": r.get(cols["status"]),
                                           "notes": str(r.get(cols.get("notes")) or "") if cols.get("notes") else ""}
    return out, ad.cfg["cases"].get("new_status"), ad.cfg["cases"]["statuses"]


def _pass_words(statuses: list[str]) -> set[str]:
    return {s for s in statuses if s in ("Pass", "Passou")} or {"Pass", "Passou"}


def verdict(statuses: list[str], not_run_word: str | None, pass_words: set[str], unconfirmed: bool = False) -> str:
    """One word per criterion from the statuses of its cases. `unconfirmed`: a mapped case is under review or its latest
    run-log result differs from the workbook; then a plain `passed` is never given."""
    if not statuses:
        return "uncovered"
    run = [s for s in statuses if s != not_run_word and s is not None]
    if not run:
        return "not run"
    if all(s in pass_words for s in statuses):
        return "passed, not confirmed" if unconfirmed else "passed"
    if any(s not in pass_words and s != not_run_word for s in statuses):
        return "open"
    return "partly passed"


def _latest_run_logs(workspace: str, cases: set[str], platform: str | None = None) -> dict:
    """{case: {"status", "batch", "ts", "evidence": n, "applied"}} of the LATEST result of each case in the run logs
    (by time of execution) of the same `platform` (all when None), read only. {} when the workspace has no run logs or no project."""
    try:
        import qa_report as qr
        from common import read_project
        proj = read_project(workspace)
        batches = qr.load_batches(workspace, proj)
    except Exception:      # noqa: BLE001 - no project/run logs: the report then says it read the workbook only
        return {}
    best: dict = {}
    for b in batches:
        if platform and qr.platform_of(b, proj) != platform.lower():
            continue
        for r in b["results"]:
            tc = str(r.get("tc"))
            if tc in cases:
                key = qr.ts_key(r.get("ts"))
                if tc not in best or key >= best[tc][0]:
                    best[tc] = (key, b["name"], r)
    return {tc: {"status": r.get("status"), "batch": name, "ts": r.get("ts"), "evidence": len(qr._evidence(r))}
            for tc, (_k, name, r) in best.items()}


def _entry(x) -> dict:
    """A case of a criterion: a plain ID, or {"case", "why", "required"}."""
    return {"case": str(x), "why": "", "required": False} if not isinstance(x, dict) else \
        {"case": str(x.get("case")), "why": str(x.get("why") or ""), "required": bool(x.get("required"))}


def report(workspace: str, amap: dict, run_logs: bool = True, platform: str | None = None) -> dict:
    """Per criterion: `cases` (they COVER it: the verdict comes from them) and `related` (verifications of the same PBI that
    are not the criterion's own text; a related one marked `required` blocks a plain `passed` while it is not a pass). Each
    association carries a short `why`. See the module docstring for the verdict words and for complete coverage."""
    library, not_run, statuses = _library(workspace, platform)
    passes = _pass_words(statuses)
    wanted = {_entry(x)["case"] for c in amap.get("criteria") or [] for k in ("cases", "related") for x in c.get(k) or []}
    logs = _latest_run_logs(workspace, wanted, platform) if run_logs else {}
    src = amap.get("criteria_source") or {}
    kind = src.get("kind") or "none"
    missing_cases, unconfirmed, required_open = [], [], []

    def info(x, criterion_id):
        e = _entry(x)
        tc = e["case"]
        rec = library.get(tc)
        if rec is None:
            missing_cases.append(f"{tc} (criterion {criterion_id})")
        log = logs.get(tc)
        wb_status = rec["status"] if rec else "not in the workbook"
        under_review = bool(rec and UNDER_REVIEW.search(rec["notes"]))
        divergent = bool(log and rec and str(log["status"]) != str(wb_status))
        if under_review or divergent:
            unconfirmed.append(tc)
        return {**e, "status": wb_status, "under_review": under_review, "run_log": log, "divergent": divergent}

    rows = []
    for c in amap.get("criteria") or []:
        cases = [info(x, c.get("id")) for x in c.get("cases") or []]
        related = [info(x, c.get("id")) for x in c.get("related") or []]
        have = [x["status"] for x in cases if x["status"] != "not in the workbook"]
        # a latest run-log result that is not a pass counts as open: it is the newest observation of the case
        have += [x["run_log"]["status"] for x in cases if x["divergent"] and x["run_log"]["status"] not in passes]
        flagged = any(x["under_review"] or x["divergent"] for x in cases)
        v = verdict(have, not_run, passes, flagged)

        def settled(x):
            now = x["run_log"]["status"] if x["run_log"] else x["status"]
            return now in passes and not x["under_review"] and x["status"] in passes

        open_required = [x["case"] for x in related if x["required"] and not settled(x)]
        required_open += [f"{tc} (criterion {c.get('id')})" for tc in open_required]
        if open_required and v in ("passed", "passed, not confirmed"):
            v = "passed, required check open"
        rows.append({"id": c.get("id"), "text": c.get("text"), "presentation": c.get("presentation"),
                     "reference": c.get("reference"), "source": c.get("source") or
                     ("ticket" if kind == "ticket-memory" else kind), "cases": cases, "related": related, "verdict": v})
    problems = []
    if kind == "none":
        problems.append("the source of the acceptance criteria is not available: coverage cannot be judged")
    if kind == "inferred":
        problems.append("the criteria are inferred (not from the ticket, no confirmed source, awaiting confirmation): "
                        + (src.get("note") or "no note on where they come from"))
    problems += [f"case not in the workbook: {m}" for m in missing_cases]
    problems += [f"criterion {r['id']} is not covered by any case" for r in rows if not r["cases"]]
    if required_open:
        problems.append("a required related verification is not a pass: " + ", ".join(required_open)
                        + ": the criterion is not totally covered")
    if unconfirmed:
        problems.append("results under review or whose latest run-log result differs from the workbook: "
                        + ", ".join(sorted(set(unconfirmed))) + ": coverage is NOT complete")
    limitations = [] if run_logs and logs else [
        "statuses come from the workbook only: a Question or a correction that exists only in a run log is not shown"]
    fail = (kind == "none" or any(not r["cases"] for r in rows) or bool(missing_cases) or bool(unconfirmed)
            or bool(required_open))
    complete = not fail and kind == "ticket-memory"
    return {"pbi": amap.get("pbi"), "platform": platform, "title": amap.get("title"), "source": src, "criteria": rows, "problems": problems,
            "limitations": limitations, "complete": complete, "exit": 1 if fail else 0}


def audit_maps(workspace: str, platforms: list[str]) -> dict:
    """What `qa_report.py audit` shows of the criteria coverage (Q3, phase 19). Read only. For each map in
    analysis/ac_map_*.json and each platform of the audited batches: the report built from THAT platform's workbook and run
    logs; and the cases whose latest result DIFFERS between platforms (the same ID passes on one and fails on the other).
    A map may say `"platforms": ["android"]` to limit the evaluation to the platforms in its scope (a cycle that is Android only
    does not report iOS as uncovered). Three situations are kept apart: `maps: []` = no map exists, so coverage was NOT assessed (never read as «no criterion
    is covered»); a map whose criteria source is `none` = the source of the criteria is not available; a criterion with no
    case = uncovered. Nothing here changes the batches' `to_fix`."""
    paths = sorted(glob.glob(os.path.join(workspace, "analysis", "ac_map_*.json")))
    plats = [p for p in dict.fromkeys(platforms) if p] or [None]
    out: dict = {"maps": [], "unreadable": []}
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as f:
                amap = json.load(f)
        except (OSError, ValueError) as e:
            out["unreadable"].append(f"{os.path.relpath(path, workspace)}: {e}")
            continue
        entry = {"map": os.path.relpath(path, workspace), "pbi": amap.get("pbi"), "reports": [], "platform_differences": [],
                 "error": None}
        scope = [str(x).lower() for x in amap.get("platforms") or []]
        known = [p for p in plats if p]
        # the platforms to evaluate = the map's scope ∩ the audited platforms; no audited platform is known: the map's own
        if not scope:
            mine = plats
        elif known:
            mine = [p for p in known if p in scope]
        else:
            mine = scope
        if scope and known and not mine:
            entry["out_of_scope"] = f"the map covers {', '.join(scope)}; the audit covers {', '.join(known)}"
            out["maps"].append(entry)
            continue
        if several_workbooks(workspace) and not [p for p in mine if p]:
            # several workbooks and neither the map nor the audited batches say a platform: the default workbook is never guessed
            entry["undetermined"] = True
            out["maps"].append(entry)
            continue
        try:
            for p in mine:
                entry["reports"].append(report(workspace, amap, True, p))
            if len([p for p in mine if p]) > 1:
                cases = sorted({str(c.get("case") if isinstance(c, dict) else c)
                                for k in amap.get("criteria") or [] for key in ("cases", "related") for c in k.get(key) or []})
                latest = {p: _latest_run_logs(workspace, set(cases), p) for p in mine}
                for tc in cases:
                    seen = {p: str(latest[p][tc]["status"]) for p in mine if tc in latest[p]}
                    if len(set(seen.values())) > 1:
                        entry["platform_differences"].append(f"{tc}: " + ", ".join(f"{p} {v}" for p, v in seen.items()))
        except Exception as e:      # noqa: BLE001 - one unreadable workbook must not stop the audit of the rest
            entry["error"] = f"{type(e).__name__}: {e}"
        out["maps"].append(entry)
    return out


def audit_lines(ac: dict) -> list[str]:
    """The lines of the audit for `audit_maps`."""
    if not ac["maps"] and not ac["unreadable"]:
        return [("acceptance criteria: no map in analysis/ac_map_*.json: coverage NOT assessed (a missing map is not the "
                 "same as a criterion with no case; /plan-tests step 1c creates one)")]
    lines = [f"acceptance criteria: unreadable map {u}" for u in ac["unreadable"]]
    for m in ac["maps"]:
        if m["error"]:
            lines.append(f"acceptance criteria, PBI {m['pbi']}: not assessed ({m['error']})")
            continue
        if m.get("undetermined"):
            lines.append(f"acceptance criteria, PBI {m['pbi']}: coverage not assessed: platform undetermined (several workbooks; "
                         "neither the map's `platforms` nor the audited batches say one)")
            continue
        if m.get("out_of_scope"):
            lines.append(f"acceptance criteria, PBI {m['pbi']}: out of scope of this audit ({m['out_of_scope']}); not evaluated")
            continue
        for rep in m["reports"]:
            where = f" on {rep['platform']}" if rep["platform"] else ""
            states = ", ".join(f"{r['id']} {r['verdict']}" for r in rep["criteria"]) or "no criteria in the map"
            lines.append(f"acceptance criteria, PBI {m['pbi']}{where}: complete coverage "
                         f"{'yes' if rep['complete'] else 'NO'} ({states})")
            lines += [f"    ! {p}" for p in rep["problems"]]
        lines += [f"    ! same case, different result between platforms: {d}" for d in m["platform_differences"]]
    return lines


def _case_text(c: dict) -> str:
    text = f"{c['case']} [workbook: {c['status']}"
    if c["under_review"]:
        text += "; under review"
    if c["run_log"] and (c["divergent"] or c["status"] == "not in the workbook"):
        text += f"; latest run log: {c['run_log']['status']} in {c['run_log']['batch']}"
    return text + "]" + (f" — {c['why']}" if c.get("why") else "")


def render(rep: dict) -> str:
    out = [f"PBI {rep['pbi']} — {rep['title']}".rstrip(" —"),
           f"criteria source: {rep['source'].get('kind')}" + (f" ({rep['source'].get('ref')})" if rep["source"].get("ref") else ""),
           f"complete coverage: {'yes' if rep['complete'] else 'NO'}"]
    for r in rep["criteria"]:
        text = str(r["text"] or "")
        if r.get("presentation") == "reference" or "\n" in text:
            # the cell gets the reference; the transcription follows whole, every line as written
            out.append(f"{r['id']}  {r['verdict'].upper()}  (source: {r['source']})\n      see: "
                       f"{r.get('reference') or r['id']}\n      transcription:")
            out += ["        " + line for line in text.split("\n")]
        else:
            out.append(f"{r['id']}  {r['verdict'].upper()}  (source: {r['source']})\n      {text}")
        out += [f"      covers: {_case_text(c)}" for c in r["cases"]] or ["      covers: —"]
        out += [f"      related{' (REQUIRED)' if c['required'] else ''}: {_case_text(c)}" for c in r["related"]]
    if not rep["criteria"]:
        out.append("(no criteria in the map)")
    out += [f"! {p}" for p in rep["problems"]]
    out += [f"limitation: {x}" for x in rep["limitations"]]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acceptance criteria and the cases that cover them")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="write a map from the PBI's ticket memory (no cases mapped yet)")
    p.add_argument("pbi")
    p.add_argument("--out", help="where to write the map (default analysis/ac_map_<pbi>.json); never overwrites")
    p = sub.add_parser("report", help="show the coverage of a map (read only)")
    p.add_argument("map")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-run-logs", action="store_true", help="read only the workbook (the report then says what it cannot see)")
    p.add_argument("--platform", help="use the workbook and the run logs of this platform. A project with several workbooks without it gets one report "
                        "per platform (the map's scope); an unknown platform is an error. One workbook: optional")
    a = ap.parse_args(argv)
    if a.command == "init":
        amap = init(a.workspace, a.pbi)
        out = a.out or os.path.join(a.workspace, "analysis", f"ac_map_{a.pbi}.json")
        out = out if os.path.isabs(out) else os.path.join(a.workspace, out)
        if os.path.exists(out):
            print(f"REFUSED: {out} already exists (a map is never overwritten)", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(amap, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"map written: {out} ({len(amap['criteria'])} criteria; source {amap['criteria_source']['kind']}). "
              "Map the cases by hand: a case covers a criterion because of what it checks.")
        return 0
    path = a.map if os.path.isabs(a.map) else os.path.join(a.workspace, a.map)
    with open(path, encoding="utf-8-sig") as f:
        amap = json.load(f)
    wanted = [a.platform.strip().lower()] if a.platform else []
    if several_workbooks(a.workspace):
        known = known_platforms(a.workspace)
        if wanted and wanted[0] not in known:
            print(f"error: --platform {a.platform!r} is not a platform of this project ({', '.join(known) or 'none named'})",
                  file=sys.stderr)
            return 2
        if not wanted:      # several workbooks: one report per platform (the map's scope when it has one), never a guess
            scope = [str(x).lower() for x in amap.get("platforms") or []]
            wanted = [p for p in known if not scope or p in scope]
            if not wanted:
                print("error: the project has several workbooks and no platform could be chosen: pass --platform "
                      f"({', '.join(known) or 'none named'})", file=sys.stderr)
                return 2
    reps = [report(a.workspace, amap, run_logs=not a.no_run_logs, platform=p) for p in wanted or [None]]
    code = max(r["exit"] for r in reps)
    if a.json:
        print(json.dumps(reps[0] if len(reps) == 1 else {"reports": reps, "exit": code}, ensure_ascii=False, indent=1))
    else:
        print("\n".join(f"== platform: {r['platform']} ==\n" + render(r) if len(reps) > 1 else render(r) for r in reps), end="")
    return code


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
