"""Acceptance criteria of a PBI mapped to the cases that cover them (plan Q3, 10.11.2026).

    python scripts/ac_coverage.py <workspace> init <pbi> [--out analysis/ac_map_<pbi>.json]
    python scripts/ac_coverage.py <workspace> report <map.json> [--json]

`init` reads the PBI's acceptance criteria from the ticket memory the tickets technique keeps
(`memory/tickets/*__<pbi>.json`, `components.fields.data.acceptance_criteria`) and writes a map with one criterion per
block (a block ends at a blank line or where a new «Dado que»/«Given» starts) and NO cases: the mapping is a decision of
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
UNDER_REVIEW = re.compile(r"sob revis[ãa]o|under review", re.IGNORECASE)


def split_criteria(text: str) -> list[str]:
    """One criterion per block: a blank line ends it, and a line that starts with Dado/Given starts a new one."""
    blocks, cur = [], []
    for line in (text or "").replace("\r", "").split("\n"):
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = []
            continue
        if cur and GIVEN.match(line):
            blocks.append(cur)
            cur = []
        cur.append(line.strip())
    if cur:
        blocks.append(cur)
    return [" ".join(b) for b in blocks]


def ticket_memory(workspace: str, pbi: int | str) -> str | None:
    found = sorted(glob.glob(os.path.join(workspace, "memory", "tickets", f"*__{pbi}.json")))
    return found[0] if found else None


def init(workspace: str, pbi: int | str) -> dict:
    ref = ticket_memory(workspace, pbi)
    criteria, title, note = [], "", ""
    if ref:
        with open(ref, encoding="utf-8") as f:
            data = json.load(f)
        fields = ((data.get("components") or {}).get("fields") or {}).get("data") or {}
        title = str(fields.get("title") or "")
        criteria = split_criteria(str(fields.get("acceptance_criteria") or ""))
        if not criteria:
            note = "the ticket has no acceptance criteria"
    else:
        note = "no ticket memory for this PBI"
    kind = "ticket-memory" if criteria else "none"
    return {"pbi": int(pbi) if str(pbi).isdigit() else str(pbi), "title": title,
            "criteria_source": {"kind": kind, "ref": os.path.relpath(ref, workspace) if ref else None, "note": note},
            "criteria": [{"id": f"AC{i}", "text": t, "cases": []} for i, t in enumerate(criteria, 1)]}


def _library(workspace: str):
    from adapters.excel.base import ExcelAdapter
    ad = ExcelAdapter.from_project(workspace)
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


def _latest_run_logs(workspace: str, cases: set[str]) -> dict:
    """{case: {"status", "batch", "ts", "evidence": n, "applied"}} of the LATEST result of each case in the run logs
    (by time of execution), read only. {} when the workspace has no run logs or no project."""
    try:
        import qa_report as qr
        from common import read_project
        proj = read_project(workspace)
        batches = qr.load_batches(workspace, proj)
    except Exception:      # noqa: BLE001 - no project/run logs: the report then says it read the workbook only
        return {}
    best: dict = {}
    for b in batches:
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


def report(workspace: str, amap: dict, run_logs: bool = True) -> dict:
    """Per criterion: `cases` (they COVER it: the verdict comes from them) and `related` (verifications of the same PBI that
    are not the criterion's own text; a related one marked `required` blocks a plain `passed` while it is not a pass). Each
    association carries a short `why`. See the module docstring for the verdict words and for complete coverage."""
    library, not_run, statuses = _library(workspace)
    passes = _pass_words(statuses)
    wanted = {_entry(x)["case"] for c in amap.get("criteria") or [] for k in ("cases", "related") for x in c.get(k) or []}
    logs = _latest_run_logs(workspace, wanted) if run_logs else {}
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
        rows.append({"id": c.get("id"), "text": c.get("text"), "source": c.get("source") or
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
    return {"pbi": amap.get("pbi"), "title": amap.get("title"), "source": src, "criteria": rows, "problems": problems,
            "limitations": limitations, "complete": complete, "exit": 1 if fail else 0}


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
        out.append(f"{r['id']}  {r['verdict'].upper()}  (source: {r['source']})\n      {r['text']}")
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
    rep = report(a.workspace, amap, run_logs=not a.no_run_logs)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if a.json else render(rep), end="")
    return rep["exit"]


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
