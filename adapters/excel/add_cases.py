"""
Adds the approved cases of a cases file to a workspace's workbook, in one step
(technique-excel.md section 4.1; plan 10.6):

    load -> add_cases -> save_if_clean -> recalc -> check_summary

The configuration comes from the `excel:` section of the workspace's project.yaml
(cases.fields maps the model's fields to columns; title is required).

    python -m adapters.excel.add_cases <workspace> <cases.json>
        [--dry-run] [--label "[2026-10-04, cases_20261004_01]"] [--no-recalc]
        [--now 2026-10-04T10:00:00]

Exit codes:
  0  added and saved (or the file was already added: nothing changes); with
     --dry-run, the cases are valid (nothing written)
  2  cross-check with problems: original untouched, result in <name>.REVIEWED.xlsx
  3  saved, but the recalculation failed (review before calling it done)
  4  invalid cases file or configuration (incl. an ID that exists, a candidate
     duplicate, or a file already added with another content): nothing written
  5  saved and recalculated, but the workbook's summary does not match the direct
     count (e.g. fixed ranges that do not reach the new rows): fix the formulas
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from .apply import _resolve
from .base import ConfigError, ExcelAdapter, InvalidBatch


def read_cases(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise InvalidBatch([f"{path}: expected {{\"cases\": [...]}} (technique-excel.md section 4.1)"])
    return data["cases"]


def _show(plan: list) -> None:
    for p in plan:
        print(f"  {p['id']} -> '{p['sheet']}' row {p['row']}")
        for col, value in p["cells"].items():
            text = str(value).replace("\n", " | ")
            print(f"      {col}: {text[:100] + '...' if len(text) > 100 else text}")
        if p["not_written"]:
            print(f"      not written (no column in this sheet): {', '.join(p['not_written'])}")


def add(workspace: str, cases_path: str, dry_run: bool = False, label: str | None = None,
        recalc: bool = True, now: datetime | None = None) -> int:
    cases_path = _resolve(workspace, cases_path, "cases file")
    ad = ExcelAdapter.from_project(workspace)
    ad.now = now
    ad.load()
    cases = read_cases(cases_path)
    file_id = os.path.splitext(os.path.basename(cases_path))[0]
    print(f"Workbook: {ad.path}")
    print(f"Cases: {cases_path} ({len(cases)})")

    if dry_run:
        problems = ad.validate_cases(cases)
        if problems:
            raise InvalidBatch(problems)
        print("Dry run -- nothing written. It would add:")
        _show(ad.plan_cases(cases))
        return 0

    label = label or f"[{ad._now().date().isoformat()}, {file_id}]"
    r = ad.add_cases(cases, label=label, batch_id=file_id)
    if not r["added"]:
        print(f"Nothing added: {r['reason']}. The workbook was not changed.")
        return 0
    print("Added:")
    _show(r["cases"])

    out = ad.save_if_clean()
    if not out["saved_original"]:
        print(f"\nCross-check: {len(out['problems'])} problem(s) -- ORIGINAL UNTOUCHED.")
        print(f"Result saved separately in: {out['file']}")
        for p in out["problems"]:
            print(" -", p)
        return 2
    print("Cross-check: 0 problems. Saved.")

    if not recalc:
        return 0
    rc = ad.recalc()
    print(f"Recalculation ({rc['mechanism']}):", "OK" if rc["ok"] else f"FAILED -- {rc['error']}")
    if not rc["ok"]:
        return 3
    vr = ad.check_summary()
    if vr["checked"] and vr["problems"]:
        print(f"\nSummary vs direct count: {len(vr['problems'])} difference(s) -- fix the summary's FORMULAS:")
        for p in vr["problems"]:
            print(" -", p)
        return 5
    if vr["checked"]:
        print("Summary vs direct count: it matches.")
    elif ad.cfg.get("summary"):
        print(f"Summary not checked: {vr['reason']}.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Adds approved cases to a workspace's workbook")
    ap.add_argument("workspace")
    ap.add_argument("cases")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the rows, write nothing")
    ap.add_argument("--label", help="label recorded in the control sheet (default: [date, file name])")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS",
                    help="instant recorded instead of the clock")
    a = ap.parse_args(argv)
    try:
        return add(a.workspace, a.cases, a.dry_run, a.label, not a.no_recalc, a.now)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING ADDED -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
