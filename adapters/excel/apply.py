"""
Applies a batch (run log) to a workspace's workbook, in one step
(technique-excel.md section 1; plan, task 2.3):

    load -> apply_batch -> regenerate_active_defects -> save_if_clean -> recalc

The configuration comes from the `excel:` section of the workspace's project.yaml.

    python -m adapters.excel.apply <workspace> <batch.json>
        [--label "[2026-09-23, batch 01]"] [--reported-by "..."] [--no-recalc]
        [--now 2026-09-23T14:03:00]   (fixed dates written; technique-excel.md section 1)

Exit codes:
  0  applied and saved (or batch already applied before: nothing changes)
  2  cross-check with problems: original untouched, result in <name>.REVIEWED.xlsx
  3  saved, but the recalculation failed (review before calling it done)
  4  invalid batch or configuration, or batch already applied with another content:
     nothing was written (see the list of problems)
  5  saved and recalculated, but the workbook's summary does not match the direct
     count (summary; technique-excel.md section 5): fix the formulas
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from .base import ConfigError, ExcelAdapter, InvalidBatch, read_batch


def _resolve(workspace: str, path: str) -> str:
    if os.path.exists(path):
        return path
    alt = os.path.join(workspace, path)
    if os.path.exists(alt):
        return alt
    raise FileNotFoundError(f"batch not found: {path} (nor in {alt})")


def apply(workspace: str, batch_path: str, label: str | None = None,
          reported_by: str | None = None, recalc: bool = True,
          now: datetime | None = None) -> int:
    batch_path = _resolve(workspace, batch_path)
    ad = ExcelAdapter.from_project(workspace)
    ad.now = now
    ad.load()
    before = ad.progress_summary()
    doc = read_batch(batch_path)
    batch = doc["results"]
    batch_id = os.path.splitext(os.path.basename(batch_path))[0]
    label = label or f"[{ad._now().date().isoformat()}, {batch_id}]"

    print(f"Workbook: {ad.path}")
    print(f"Batch: {batch_path} ({len(batch)} items)")
    r = ad.apply_batch(batch, label=label, reported_by=reported_by, batch_id=batch_id,
                       identity=doc.get("translated_from"))
    if not r["applied"]:
        print(f"Nothing applied: {r['reason']}. The workbook was not changed.")
        return 0
    if r["new_defects"]:
        print("New defects:", ", ".join(r["new_defects"]))

    v = ad.regenerate_active_defects()
    if v.get("total_open") is not None:
        print(f"Active view: {v['total_open']} open, {v['closed']} closed, by severity {v['by_severity']}")

    out = ad.save_if_clean()
    if not out["saved_original"]:
        print(f"\nCross-check: {len(out['problems'])} problem(s) -- ORIGINAL UNTOUCHED.")
        print(f"Result saved separately in: {out['file']}")
        for p in out["problems"]:
            print(" -", p)
        return 2
    print("Cross-check: 0 problems. Saved.")

    rc_code = 0
    if recalc:
        rc = ad.recalc()
        print(f"Recalculation ({rc['mechanism']}):", "OK" if rc["ok"] else f"FAILED -- {rc['error']}")
        rc_code = 0 if rc["ok"] else 3
        if rc["ok"]:
            vr = ad.check_summary()
            if vr["checked"] and vr["problems"]:
                print(f"\nSummary vs direct count: {len(vr['problems'])} difference(s) -- fix the summary's FORMULAS:")
                for p in vr["problems"]:
                    print(" -", p)
                rc_code = 5
            elif vr["checked"]:
                print("Summary vs direct count: it matches.")
            elif ad.cfg.get("summary"):
                print(f"Summary not checked: {vr['reason']}.")

    after = ad.progress_summary()
    print("\nBefore -> after (direct count per sheet):")
    for sheet in after:
        print(f"  {sheet}: {before.get(sheet)} -> {after[sheet]}")
    return rc_code


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
    a = ap.parse_args(argv)
    try:
        return apply(a.workspace, a.batch, a.label, a.reported_by, not a.no_recalc, a.now)
    except (InvalidBatch, ConfigError, FileNotFoundError) as e:
        print(f"\nNOTHING APPLIED -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
