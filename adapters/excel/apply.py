"""
Applies a batch (run log) to a workspace's workbook, in one step
(technique-excel.md section 1; plan, task 2.3):

    load -> apply_batch -> regenerate_active_defects -> save_if_clean -> recalc

The configuration comes from the `excel:` section of the workspace's project.yaml.

    python -m adapters.excel.apply <workspace> <batch.json>
        [--label "[2026-09-23, batch 01]"] [--reported-by "..."] [--no-recalc]
        [--now 2026-09-23T14:03:00]   (fixed dates written; technique-excel.md section 1)
        [--dry-run [--json]]          (simulation: the same pipeline on a temporary copy; nothing written)
        [--expect <token>]            (apply only if nothing changed since the dry run that printed the token)

The dry run (technique-excel.md section 1.1) routes, validates and transforms exactly as the apply, on a copy of the
target workbook in a temporary folder, then lists every cell that would change (before -> after), the counts per
status, the new defects, the row of the control sheet and the problems. Values that only hold the instant of the
application are marked "at apply time". It ends with a preview token (batch, project.yaml and workbooks): a later
`--expect <token>` refuses to apply when any of them changed since.

Exit codes:
  0  applied and saved (or batch already applied before: nothing changes)
  2  cross-check with problems: original untouched, result in <name>.REVIEWED.xlsx
  3  saved, but the recalculation failed (review before calling it done)
  4  invalid batch or configuration, or batch already applied with another content:
     nothing was written (see the list of problems)
  5  saved and recalculated, but the workbook's summary does not match the direct
     count (summary; technique-excel.md section 5): fix the formulas
With --dry-run the same codes say what the apply would do; nothing is ever written (the original workbook, the run
log and the memory are untouched; the temporary copy is removed even on error).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from .base import (
    ConfigError,
    ExcelAdapter,
    InvalidBatch,
    read_batch,
    route_workbook,
    workbook_files,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
from common import WorkspaceBusy, workspace_lock


def _resolve(workspace: str, path: str, what: str = "batch") -> str:
    if os.path.exists(path):
        return path
    alt = os.path.join(workspace, path)
    if os.path.exists(alt):
        return alt
    raise FileNotFoundError(f"{what} not found: {path} (nor in {alt})")


def _project(workspace: str) -> dict:
    try:
        import yaml
        with open(os.path.join(workspace, "project.yaml"), encoding="utf-8-sig") as f:
            return yaml.safe_load(f) or {}
    except (OSError, ValueError) as e:
        raise ConfigError(f"project.yaml could not be read ({e}). Nothing was written.") from None


def _sha(path: str) -> str:
    if not os.path.isfile(path):
        return "missing"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def preview_token(workspace: str, batch_path: str, proj: dict | None = None) -> str:
    """What a dry run saw: the batch file, project.yaml and every declared workbook (the target and the ones checked
    for "applied elsewhere"). Any change to one of them gives another token."""
    proj = _project(workspace) if proj is None else proj
    parts = [("batch", _sha(batch_path)), ("project.yaml", _sha(os.path.join(workspace, "project.yaml")))]
    for file in sorted(set(workbook_files(proj))):
        parts.append((file, _sha(os.path.join(workspace, file))))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def apply(workspace: str, batch_path: str, label: str | None = None,
          reported_by: str | None = None, recalc: bool = True,
          now: datetime | None = None, expect: str | None = None) -> int:
    batch_path = _resolve(workspace, batch_path)
    batch_id = os.path.splitext(os.path.basename(batch_path))[0]
    proj = _project(workspace)
    doc = read_batch(batch_path)
    route = route_workbook(proj, batch_id, doc.get("run") if isinstance(doc.get("run"), dict) else None,
                           workspace=workspace)
    if route.problem:
        raise ConfigError(f"{route.problem}. Nothing was written.")
    try:
        # one writer at a time: two applies at once corrupted the workbook (audit 06.11.2026)
        with workspace_lock(workspace):
            if expect is not None:
                current = preview_token(workspace, batch_path, proj)
                if current != expect:
                    raise ConfigError(f"the preview {expect} is out of date (now {current}): the batch, project.yaml or "
                                      "a workbook changed since the dry run. Run --dry-run again and review it. "
                                      "Nothing was written.")
            elsewhere = _applied_elsewhere(workspace, proj, batch_id, route.file)
            if elsewhere:
                raise ConfigError(f"{batch_id} was already applied to {elsewhere}, not to {route.file} where it "
                                  "belongs now: review it before applying again. Nothing was written.")
            return _apply(workspace, batch_path, batch_id, label, reported_by, recalc, now, route)
    except WorkspaceBusy as e:
        raise ConfigError(str(e)) from None


def _applied_elsewhere(workspace: str, proj: dict, batch_id: str, own: str) -> str | None:
    """The other declared workbook whose control sheet already lists this batch, or None. A workbook that cannot be
    read is skipped here (its own apply reports it); the batch's own workbook is checked by apply_batch."""
    excel = proj.get("excel") if isinstance(proj.get("excel"), dict) else {}
    base = {k: v for k, v in excel.items() if k != "workbooks"}

    def full(x):
        return os.path.normcase(os.path.abspath(os.path.join(workspace, x)))
    for file in workbook_files(proj):
        if full(file) == full(own) or not os.path.isfile(os.path.join(workspace, file)):
            continue
        try:
            other = ExcelAdapter({**base, "file": file}, base_dir=workspace)
            other.wb = openpyxl.load_workbook(other.path, read_only=False, data_only=True)
            if any(b.get("batch_id") == batch_id for b in other.applied_batches()):
                return file
        except Exception:      # noqa: BLE001, S112 - unreadable: not this check's verdict
            continue
    return None


def _apply(workspace: str, batch_path: str, batch_id: str, label: str | None, reported_by: str | None,
           recalc: bool, now: datetime | None, route, target: str | None = None, report: dict | None = None,
           log=print) -> int:
    """The one pipeline of the apply and of the dry run. `target`: the dry run's temporary copy (an absolute path)
    instead of the workbook; `report` collects what the dry run shows; `log` prints (quiet in --json)."""
    ad = ExcelAdapter.from_project(workspace, file=route.file)
    ad.now = now
    report = report if report is not None else {}
    if not os.path.isfile(ad.path):
        raise ConfigError(f"the workbook {route.file} ({route.source}) does not exist: build it with the project's "
                          "builder first. Nothing was written.")
    try:
        ad.load()
    except (KeyError, ValueError, OSError, zipfile.BadZipFile, InvalidFileException) as e:
        raise ConfigError(f"the workbook {route.file} ({route.source}) could not be read ({type(e).__name__}: "
                          f"{e}). Nothing was written.") from None
    if target:          # the dry run: the same adapter and configuration, on the copy
        ad.cfg["file"] = target
        ad.load()
    log(f"Platform: {route.platform or 'not declared (only workbook)'}; workbook from {route.source}")
    before = ad.progress_summary()
    doc = read_batch(batch_path)
    batch = doc["results"]
    label = label or f"[{ad._now().date().isoformat()}, {batch_id}]"
    report.update({"workbook": route.file, "platform": route.platform, "source": route.source,
                   "items": len(batch), "counts_before": before})

    log(f"Workbook: {route.file if target else ad.path}")
    log(f"Batch: {batch_path} ({len(batch)} items)")
    r = ad.apply_batch(batch, label=label, reported_by=reported_by, batch_id=batch_id,
                       identity=doc.get("translated_from"), run=doc.get("run") if isinstance(doc.get("run"), dict) else None)
    if not r["applied"]:
        report["already_applied"] = r["reason"]
        log(f"Nothing applied: {r['reason']}. The workbook was not changed.")
        return 0
    report["new_defects"] = r["new_defects"]
    if r["new_defects"]:
        log("New defects:", ", ".join(r["new_defects"]))

    v = ad.regenerate_active_defects()
    if v.get("total_open") is not None:
        log(f"Active view: {v['total_open']} open, {v['closed']} closed, by severity {v['by_severity']}")

    out = ad.save_if_clean()
    if not out["saved_original"]:
        report["cross_check"] = out["problems"]
        log(f"\nCross-check: {len(out['problems'])} problem(s) -- ORIGINAL UNTOUCHED.")
        if not target:
            log(f"Result saved separately in: {out['file']}")
        for p in out["problems"]:
            log(" -", p)
        return 2
    log("Cross-check: 0 problems." + ("" if target else " Saved."))

    rc_code = 0
    if recalc:
        rc = ad.recalc()
        report["recalc"] = "OK" if rc["ok"] else f"FAILED -- {rc['error']}"
        log(f"Recalculation ({rc['mechanism']}):", report["recalc"])
        rc_code = 0 if rc["ok"] else 3
        if rc["ok"]:
            vr = ad.check_summary()
            if vr["checked"] and vr["problems"]:
                report["summary_problems"] = vr["problems"]
                log(f"\nSummary vs direct count: {len(vr['problems'])} difference(s) -- fix the summary's FORMULAS:")
                for p in vr["problems"]:
                    log(" -", p)
                rc_code = 5
            elif vr["checked"]:
                log("Summary vs direct count: it matches.")
            elif ad.cfg.get("summary"):
                log(f"Summary not checked: {vr['reason']}.")

    after = ad.progress_summary()
    report["counts_after"] = after
    log("\nBefore -> after (direct count per sheet):")
    for sheet in after:
        log(f"  {sheet}: {before.get(sheet)} -> {after[sheet]}")
    return rc_code


# ----------------------------------------------------------------------
# Dry run (technique-excel.md section 1.1)
# ----------------------------------------------------------------------

def _values(path: str) -> dict:
    wb = openpyxl.load_workbook(path, data_only=False)
    try:
        return {(ws.title, c.coordinate): c.value for ws in wb for row in ws.iter_rows() for c in row}
    finally:
        wb.close()


def _formula_form(v) -> str | None:
    """A formula without the quotes and spaces a recalculation may add or drop around sheet names, or None."""
    if isinstance(v, str) and v.startswith("="):
        return v.replace("'", "").replace(" ", "").upper()
    return None


def _cell_diff(original: str, simulated: str, row_case: dict, timed_cells: set) -> tuple[list[dict], int]:
    """Every cell whose stored value differs (formulas compared as formulas), sheet by sheet, with the case of its row
    when the row is a case, and whether its value depends on the instant of the application (`timed_cells`: the cells
    a second transformation at another instant wrote differently; never guessed from a date in the text). A formula
    only rewritten by the recalculation (same formula, other quoting) is counted apart, not listed."""
    a = openpyxl.load_workbook(original, data_only=False)
    b = openpyxl.load_workbook(simulated, data_only=False)
    out, rewritten = [], 0
    try:
        for name in b.sheetnames:
            ws_new = b[name]
            ws_old = a[name] if name in a.sheetnames else None
            rows = max(ws_new.max_row, ws_old.max_row if ws_old is not None else 0)
            cols = max(ws_new.max_column, ws_old.max_column if ws_old is not None else 0)
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    new = ws_new.cell(r, c).value
                    old = ws_old.cell(r, c).value if ws_old is not None else None
                    if new == old:
                        continue
                    if _formula_form(new) is not None and _formula_form(new) == _formula_form(old):
                        rewritten += 1
                        continue
                    timed = (name, ws_new.cell(r, c).coordinate) in timed_cells
                    out.append({"sheet": name, "cell": ws_new.cell(r, c).coordinate,
                                "case": row_case.get((name, r)), "before": old, "after": new,
                                "at_apply_time": timed})
        for name in a.sheetnames:
            if name not in b.sheetnames:
                out.append({"sheet": name, "cell": None, "case": None, "before": "(sheet)", "after": None,
                            "at_apply_time": False})
    finally:
        a.close()
        b.close()
    return out, rewritten


def dry_run(workspace: str, batch_path: str, label: str | None = None, reported_by: str | None = None,
            recalc: bool = True, now: datetime | None = None) -> dict:
    """The simulation: the result dict (`code` is the exit code the apply would give). Nothing in the workspace is
    written; the temporary folder goes away even on error."""
    batch_path = _resolve(workspace, batch_path)
    batch_id = os.path.splitext(os.path.basename(batch_path))[0]
    now = now or datetime.now().replace(microsecond=0)
    res = {"batch": batch_id, "dry_run": True, "problems": [], "cells": [], "code": 4, "now": now.isoformat()}
    proj = _project(workspace)
    try:
        doc = read_batch(batch_path)
    except InvalidBatch as e:
        res["problems"] = list(e.problems)
        return res
    route = route_workbook(proj, batch_id, doc.get("run") if isinstance(doc.get("run"), dict) else None,
                           workspace=workspace)
    res.update({"workbook": route.file, "platform": route.platform, "source": route.source})
    if route.problem:
        res["problems"].append(route.problem)
        return res
    tmp = tempfile.mkdtemp(prefix="qa-dry-run-")
    try:
        try:
            with workspace_lock(workspace):         # a consistent snapshot: no apply writes while we copy
                res["token"] = preview_token(workspace, batch_path, proj)
                elsewhere = _applied_elsewhere(workspace, proj, batch_id, route.file)
                if elsewhere:
                    res["problems"].append(f"{batch_id} was already applied to {elsewhere}, not to {route.file} "
                                           "where it belongs now")
                    return res
                original = os.path.join(workspace, route.file)
                if not os.path.isfile(original):
                    res["problems"].append(f"the workbook {route.file} ({route.source}) does not exist")
                    return res
                work = os.path.join(tmp, os.path.basename(route.file))
                shutil.copy2(original, work)
                snapshot = os.path.join(tmp, "before" + os.path.splitext(route.file)[1])
                shutil.copy2(original, snapshot)
        except WorkspaceBusy as e:
            res["problems"].append(str(e))
            return res
        report: dict = {}
        lines: list[str] = []
        try:
            code = _apply(workspace, batch_path, batch_id, label, reported_by, recalc, now, route,
                          target=work, report=report, log=lambda *a: lines.append(" ".join(str(x) for x in a)))
        except InvalidBatch as e:
            res["problems"] += list(e.problems)
            return res
        except ConfigError as e:
            res["problems"].append(str(e).replace(" Nothing was written.", ""))
            return res
        except (KeyError, ValueError, OSError, zipfile.BadZipFile, InvalidFileException) as e:
            res["problems"].append(f"the workbook {route.file} could not be read ({type(e).__name__}: {e})")
            return res
        res.update(report)
        res["code"] = code
        res["log"] = lines
        if "already_applied" not in report:
            if code == 2:       # the dry run never keeps a REVIEWED file; the diff is of what would be saved
                reviewed = os.path.splitext(work)[0] + ".REVIEWED" + os.path.splitext(work)[1]
                if os.path.isfile(reviewed):
                    work = reviewed
            ad = ExcelAdapter.from_project(workspace, file=route.file)
            ad.cfg["file"] = snapshot
            ad.load()
            id_col = ad.cfg["cases"]["columns"]["id"]
            row_case = {(sheet, row["_row"]): str(row.get(id_col)).strip()
                        for sheet, rows in ad.case_rows().items() for row in rows}
            # the cells that hold the instant: the same transformation at another instant, compared cell by cell
            other = os.path.join(tmp, "other" + os.path.splitext(route.file)[1])
            shutil.copy2(snapshot, other)
            later = now + timedelta(days=367, hours=1, minutes=1, seconds=1)
            _apply(workspace, batch_path, batch_id, label, reported_by, False, later, route, target=other,
                   report={}, log=lambda *a: None)
            first, second = _values(work), _values(other)
            timed = {k for k in set(first) | set(second) if first.get(k) != second.get(k)}
            res["cells"], res["formulas_rewritten"] = _cell_diff(snapshot, work, row_case, timed)
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _short(v, n: int = 70) -> str:
    t = "" if v is None else str(v).replace("\n", " / ")
    return repr(t[:n] + ("…" if len(t) > n else ""))


def print_dry_run(res: dict) -> None:
    print(f"DRY RUN of {res['batch']} -- nothing is written.")
    if res.get("workbook"):
        print(f"Target: {res['workbook']} (platform: {res.get('platform') or 'not declared'}; from {res.get('source')})")
    if res.get("problems"):
        print("\nProblems that stop the apply:")
        for p in res["problems"]:
            print(" -", p)
    if res.get("already_applied"):
        print(f"\nAlready applied: {res['already_applied']}. Applying would change nothing.")
    if res.get("cells"):
        print(f"\nCells that would change ({len(res['cells'])}):")
        for c in res["cells"]:
            where = f"{c['sheet']}!{c['cell']}" + (f" ({c['case']})" if c.get("case") else "")
            after = ("<at apply time> " if c["at_apply_time"] else "") + _short(c["after"])
            print(f"  {where}: {_short(c['before'])} -> {after}")
    if res.get("formulas_rewritten"):
        print(f"  (+ {res['formulas_rewritten']} formula(s) only rewritten by the recalculation, same meaning)")
    if res.get("new_defects"):
        print("\nNew defects:", ", ".join(res["new_defects"]))
    if res.get("cross_check"):
        print("\nCross-check problems (the apply would leave the original untouched):")
        for p in res["cross_check"]:
            print(" -", p)
    if res.get("counts_after"):
        print("\nCounts per sheet, before -> after:")
        for sheet, after in res["counts_after"].items():
            before = (res.get("counts_before") or {}).get(sheet)
            mark = "" if before == after else "   (changes)"
            print(f"  {sheet}: {before} -> {after}{mark}")
    if res.get("recalc"):
        print(f"Recalculation of the copy: {res['recalc']}")
    if res.get("summary_problems"):
        print("Summary vs direct count would differ:")
        for p in res["summary_problems"]:
            print(" -", p)
    if not res.get("problems"):
        print(f"\nThe apply would exit with code {res['code']}. Values marked <at apply time> take the instant of the "
              "real apply.")
    if res.get("token"):
        print(f"Preview token: {res['token']}  (apply with --expect {res['token']}: refused if anything changed since)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Applies a batch to a workspace's workbook")
    ap.add_argument("workspace")
    ap.add_argument("batch")
    ap.add_argument("--label", help="text before this session's notes (default: [date, batch name])")
    ap.add_argument("--reported-by", help="reporter of new defects (default: excel.defects.reported_by, "
                                          "or 'Automated QA')")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS",
                    help="instant used in every date written, instead of the clock (rebuilds)")
    ap.add_argument("--dry-run", action="store_true",
                    help="simulate on a temporary copy: what would change, nothing written (technique-excel.md 1.1)")
    ap.add_argument("--json", action="store_true", help="with --dry-run: the result as JSON")
    ap.add_argument("--expect", metavar="TOKEN",
                    help="the preview token of a dry run: refuse when the batch, project.yaml or a workbook changed")
    a = ap.parse_args(argv)
    if a.json and not a.dry_run:
        ap.error("--json goes with --dry-run")
    if a.dry_run and a.expect:
        ap.error("--expect goes with the real apply")
    try:
        if a.dry_run:
            res = dry_run(a.workspace, a.batch, a.label, a.reported_by, not a.no_recalc, a.now)
            if a.json:
                print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
            else:
                print_dry_run(res)
            return 4 if res["problems"] else res["code"]
        return apply(a.workspace, a.batch, a.label, a.reported_by, not a.no_recalc, a.now, a.expect)
    except (InvalidBatch, ConfigError, FileNotFoundError) as e:
        print(f"\nNOTHING APPLIED -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
