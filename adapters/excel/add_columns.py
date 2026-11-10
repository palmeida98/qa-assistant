"""
Adds columns to the case sheets of a workspace's workbook, so a project can record the traceability of a case (the ID of
its PBI, its QA task, its origin, its platform) next to the case itself (technique-excel.md section 4.3; plan L1 of
10.11.2026):

    python -m adapters.excel.add_columns <workspace> <columns.json>
        [--platform android|ios|both|<a platform of the project>]
        [--dry-run] [--label "[2026-11-10, columns_20261110_01]"] [--no-recalc]
        [--now 2026-11-10T10:00:00] [--expect <token of the dry run>]

The columns file is {"columns": [{"header": "PBI (ID)", "field": "pbi_id"}, ...]} (`field` is a field of the case model:
pbi_id, qa_task, origin, platform, feature...). Each header is appended AFTER the last header of every case sheet, with the
style of the header before it, and nothing else in the sheet changes (no case, no formula, no row). A header that already
exists in a sheet is refused for that workbook (nothing is written). The command prints the `cases.fields` lines to add to
project.yaml: until they are there the adapter does not fill the new columns (and lists the fields "without a column").

The dry run prints a preview token (`v1-<hash>`: the columns file, the workbook path and content, project.yaml, the plan
and the options); with `--expect <token>` the real write refuses, writing nothing, when any of them changed since. It holds
the workspace's lock like the apply does and is recorded in the control sheet like a batch (the same columns again change
nothing). The review of the dry run (sheets, positions, headers) is written next to the columns file. With --platform both,
every workbook of the project is validated before any is written and ONE combined token covers them.

Exit codes: 0 written (or already written; with --dry-run, valid); 2 the cross-check found problems (original untouched, result
in <name>.REVIEWED.xlsx); 3 saved, but the recalculation failed; 4 invalid file or configuration, a header that exists, a token
out of date, or another process writing the workspace: nothing written; 5 saved and recalculated, but the summary does not
match the direct count.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime

from openpyxl.utils import get_column_letter

from .add_cases import combined_token, resolve_platform
from .apply import _resolve, _sha

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
from common import WorkspaceBusy, atomic_write, workspace_lock, workspace_path

from .base import CASE_MODEL_FIELDS, ConfigError, ExcelAdapter, InvalidBatch, batch_hash

TOKEN_VERSION = "v1"
MAX_HEADER = 60


def read_columns(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    cols = data.get("columns") if isinstance(data, dict) else None
    if not isinstance(cols, list) or not cols:
        raise InvalidBatch([f"{path}: expected {{\"columns\": [{{\"header\": ..., \"field\": ...}}]}}"])
    problems, headers, fields = [], set(), set()
    for i, c in enumerate(cols, 1):
        if not isinstance(c, dict) or not str(c.get("header") or "").strip():
            problems.append(f"column {i}: 'header' is missing")
            continue
        header, field = str(c["header"]).strip(), c.get("field")
        if len(header) > MAX_HEADER:
            problems.append(f"column {i}: the header is longer than {MAX_HEADER} characters")
        if field is not None and field not in CASE_MODEL_FIELDS:
            problems.append(f"column {i} ({header}): field {field!r} is not a field of the case model {list(CASE_MODEL_FIELDS)}")
        if header.casefold() in headers:
            problems.append(f"column {i}: the header {header!r} appears twice")
        headers.add(header.casefold())
        if field:
            if field in fields:
                problems.append(f"column {i} ({header}): field {field!r} appears twice")
            fields.add(field)
    if problems:
        raise InvalidBatch(problems)
    return [{"header": str(c["header"]).strip(), "field": c.get("field")} for c in cols]


def plan_columns(ad: ExcelAdapter, columns: list) -> list[dict]:
    """Per case sheet: where each header goes. Raises InvalidBatch when a header exists or the sheet has a table."""
    ad._need_wb()
    problems, plan = [], []
    for sheet, ws, headers in ad._case_sheets():
        existing = {str(k).casefold() for k in headers}
        hrow = ad.cfg["cases"].get("header_row", 1)
        last = max((c for c in range(1, ws.max_column + 1) if ws.cell(hrow, c).value not in (None, "")), default=0)
        clash = [c["header"] for c in columns if c["header"].casefold() in existing]
        if clash:
            problems.append(f"'{sheet}': header(s) already exist: {', '.join(clash)}")
        if getattr(ws, "tables", None):
            problems.append(f"'{sheet}': the sheet has an Excel table: add the columns by hand or extend this tool")
        plan.append({"sheet": sheet, "header_row": hrow, "first_col": last + 1,
                     "columns": [{"header": c["header"], "field": c["field"], "col": last + 1 + i,
                                  "letter": get_column_letter(last + 1 + i)} for i, c in enumerate(columns)]})
    if problems:
        raise InvalidBatch(problems)
    return plan


def apply_columns(ad: ExcelAdapter, columns: list, label: str, batch_id: str | None = None) -> dict:
    ad._need_wb()
    h_batch = batch_hash([{"add_columns": columns}])
    previous = next((x for x in ad.applied_batches() if x["hash"] == h_batch), None)
    if previous:
        return {"added": False, "hash": h_batch, "plan": [], "reason": f"columns already added at {previous['applied_at']}"}
    if batch_id and next((x for x in ad.applied_batches() if x.get("batch_id") == batch_id), None):
        raise InvalidBatch([(f"the columns file '{batch_id}' was already written with a DIFFERENT content; new columns "
                             "go in a new file, with another name")])
    plan = plan_columns(ad, columns)
    sheets = {sheet: ws for sheet, ws, _h in ad._case_sheets()}
    for p in plan:
        ws = sheets[p["sheet"]]
        prev = ws.cell(p["header_row"], p["first_col"] - 1)
        for c in p["columns"]:
            cell = ws.cell(p["header_row"], c["col"])
            cell.value = c["header"]
            if prev.has_style:
                cell.font, cell.fill = copy.copy(prev.font), copy.copy(prev.fill)
                cell.border, cell.alignment = copy.copy(prev.border), copy.copy(prev.alignment)
            width = ws.column_dimensions[prev.column_letter].width
            ws.column_dimensions[c["letter"]].width = max(14, min(width or 14, 40))
        if ws.auto_filter and ws.auto_filter.ref:       # the filter keeps covering the header row
            first = ws.auto_filter.ref.split(":")[0]
            ws.auto_filter.ref = f"{first}:{p['columns'][-1]['letter']}{ws.max_row}"
    ctl = ad._control_ws(create=True)
    record = {"hash": h_batch, "applied_at": ad._now().isoformat(timespec="seconds"), "label": label,
              "items": len(columns), "new_defects": "", "batch_id": batch_id, "result_keys": "[]"}
    ctl.append([record.get(name) for name in ad._control_names(ctl)])
    return {"added": True, "hash": h_batch, "plan": plan}


def columns_token(workspace: str, ad: ExcelAdapter, path: str, plan: list, label: str | None, recalc: bool,
                  now: datetime | None) -> str:
    parts = [("columns file", _sha(path)), ("workbook", os.path.relpath(ad.path, workspace)),
             ("workbook content", _sha(ad.path)), ("project.yaml", _sha(os.path.join(workspace, "project.yaml"))),
             ("plan", hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()),
             ("label", label if label else "<default: [date of the write, file name]>"), ("recalc", bool(recalc)),
             ("now", now.isoformat() if now else "<clock of the write>")]
    return f"{TOKEN_VERSION}-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def yaml_hint(columns: list) -> str:
    lines = [f'      {c["field"]}: "{c["header"]}"' for c in columns if c["field"]]
    return ("Add under `excel.cases.fields` in project.yaml (until then the new columns stay empty):\n" + "\n".join(lines)
            if lines else "")


def review_text(workspace: str, prepared: list, path: str, token: str, command: str) -> str:
    """The complete review: per workbook its path and SHA-256, the token, every header cell and the project.yaml lines.
    `prepared` is [(platform, file, adapter, columns, plan, token)] (one entry per destination)."""
    out = [f"# Dry run of adding columns from {os.path.basename(path)}: complete review", "",
           "Nothing was written to the workbook(s). Only the header cells below are added; no case, formula or row changes.", "",
           f"- **Token: `{token}`**" + (" (combined: it covers every workbook below)" if len(prepared) > 1 else ""),
           f"- Columns file `{os.path.relpath(path, workspace)}` (SHA-256 `{_sha(path)}`)", "", "## Plan", ""]
    for plat, _file, ad, _cols, plan, tok in prepared:
        out += [f"### Workbook `{os.path.relpath(ad.path, workspace)}` ({plat or 'default'})", "",
                f"- SHA-256 `{_sha(ad.path)}` · token of this workbook `{tok}`", ""]
        for p in plan:
            out.append(f"- **{p['sheet']}** (header row {p['header_row']}): "
                       + ", ".join(f"`{c['header']}` -> column {c['letter']}" for c in p["columns"]))
        out.append("")
    hint = yaml_hint(prepared[0][3])
    out += ["## project.yaml", "", "~~~", hint or "(no field mapped)", "~~~", "",
            "## Command (only after the user approves THIS review)", "", "~~~", command, "~~~", ""]
    return "\n".join(out)


def _prepare(workspace: str, path: str, file: str | None, label, recalc, now):
    ad = ExcelAdapter.from_project(workspace, file=file)
    ad.now = now
    ad.load()
    columns = read_columns(path)
    plan = plan_columns(ad, columns)
    return ad, columns, plan, columns_token(workspace, ad, path, plan, label, recalc, now)


def add_columns(workspace: str, path: str, dry_run: bool = False, label: str | None = None, recalc: bool = True,
                now: datetime | None = None, expect: str | None = None, platform: str | None = None) -> int:
    if dry_run and expect:
        raise ConfigError("--expect goes with the real write, not with --dry-run")
    path = _resolve(workspace, path, "columns file")
    targets = resolve_platform(workspace, platform)
    files = [(p, f) for p, f in targets] or [(None, None)]
    if len({f for _, f in files}) != len(files):
        raise ConfigError("two destinations are the same workbook")
    file_id = os.path.splitext(os.path.basename(path))[0]
    opts = (f" --label {json.dumps(label)}" if label else "") + (" --no-recalc" if not recalc else "") \
        + (f" --now {now.isoformat()}" if now else "") + (f" --platform {platform}" if platform else "")

    def run(lock: bool) -> int:
        if not dry_run:         # a repeat is harmless: it needs no token and changes nothing
            columns0 = read_columns(path)
            h0 = batch_hash([{"add_columns": columns0}])
            seen = []
            for _p, f in files:
                ad0 = ExcelAdapter.from_project(workspace, file=f)
                ad0.load()
                seen.append(next((x for x in ad0.applied_batches() if x["hash"] == h0), None))
            if all(seen):
                print(f"Nothing written: these columns were already added at {seen[0]['applied_at']}. "
                      "The workbook was not changed.")
                return 0
        prepared = [(p, f, *_prepare(workspace, path, f, label, recalc, now)) for p, f in files]
        tokens = [(p or "default", t) for p, _f, _ad, _c, _pl, t in prepared]
        token = tokens[0][1] if len(tokens) == 1 else combined_token(tokens)
        if dry_run:
            command = f"python -m adapters.excel.add_columns {workspace} {os.path.relpath(path, workspace)}{opts} --expect {token}"
            print(f"DRY RUN add columns to {len(prepared)} workbook(s): nothing is written")
            for p, f, ad, columns, plan, tok in prepared:
                print(f"  {p or 'default'}: {os.path.relpath(ad.path, workspace)} · token {tok}")
                for pl in plan:
                    print(f"    {pl['sheet']}: " + ", ".join(f"{c['header']} -> {c['letter']}" for c in pl["columns"]))
            print(("Combined token: " if len(prepared) > 1 else "Preview token: ") + token)
            review = workspace_path(workspace, *os.path.relpath(
                os.path.join(os.path.dirname(path), f"{file_id}.dryrun.md"), workspace).replace(os.sep, "/").split("/"))
            atomic_write(review, review_text(workspace, prepared, path, token, command))
            print(f"Review: {os.path.relpath(review, workspace)}")
            print(f"Write, after approving THAT review: {command}")
            print(yaml_hint(prepared[0][3]))
            return 0
        if expect is None or str(expect) != token:
            raise ConfigError(f"the preview {expect} is not the token of a dry run of this command (now {token}): "
                              "run --dry-run and review it. Nothing was written.")
        shown = label or f"[{prepared[0][2]._now().date().isoformat()}, {file_id}]"
        rc = 0
        for p, f, ad, columns, _plan, _tok in prepared:
            print(f"\n== {p or 'default'}: {ad.path} ==")
            r = apply_columns(ad, columns, shown, file_id)
            if not r["added"]:
                print(f"Nothing written: {r['reason']}. The workbook was not changed.")
                continue
            out = ad.save_if_clean()
            if not out["saved_original"]:
                print(f"Cross-check: {len(out['problems'])} problem(s) -- ORIGINAL UNTOUCHED. Result in {out['file']}")
                for pr in out["problems"]:
                    print(" -", pr)
                return 2
            print("Columns added; cross-check: 0 problems. Saved.")
            if recalc:
                rcalc = ad.recalc()
                print(f"Recalculation ({rcalc['mechanism']}):", "OK" if rcalc["ok"] else f"FAILED -- {rcalc['error']}")
                if not rcalc["ok"]:
                    return 3
                vr = ad.check_summary()
                if vr["checked"] and vr["problems"]:
                    for pr in vr["problems"]:
                        print(" -", pr)
                    return 5
        print(yaml_hint(prepared[0][3]))
        return rc

    if dry_run:
        return run(False)
    try:
        with workspace_lock(workspace):
            return run(True)
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Adds traceability columns (PBI ID, QA task, origin...) to the case sheets")
    ap.add_argument("workspace")
    ap.add_argument("columns")
    ap.add_argument("--platform", metavar="PLATFORM", help="android | ios | both | a platform of the project")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--label")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--expect", metavar="TOKEN")
    ap.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS")
    a = ap.parse_args(argv)
    try:
        return add_columns(a.workspace, a.columns, a.dry_run, a.label, not a.no_recalc, a.now, a.expect, a.platform)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING WRITTEN -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
