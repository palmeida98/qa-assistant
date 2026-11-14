"""
Reconciles a control row whose results cannot be known (plan Q1, review of 10.11.2026), WITHOUT unblocking anything silently:

    python -m adapters.excel.reconcile <workspace> <batch_id> --from <run-log.json>
        [--dry-run] [--expect <token of the dry run>]

The apply refuses to go on while an application written to the workbook has no `result_keys` and no run log that is
PROVEN to be what was applied (the hash of the run log's results equals the hash recorded in the control row): duplicates
cannot be ruled out against it. This command writes the missing keys into that ONE control cell, and only when the file
given is proven the same way: its results hash to the row's recorded hash (or its `translated_from`). Typical use: the run
log was deleted or edited and the exact file is restored from Git history (`git log --follow -- runs/<batch_id>.json`).
A file that does not hash to the row is REFUSED, writing nothing: this tool never accepts an origin it cannot prove. When
no proven file exists, the row stays unverifiable and the apply stays blocked; accepting it anyway is a decision of the
user that the framework does not take for them (the proposal is an explicit, recorded acceptance, not implemented).

The dry run prints the preview token (`v1-<hash>`: the file, the workbook content, project.yaml, the row's hash and the keys);
with `--expect` the write refuses when any of them changed since. Only the `result_keys` cell of that row changes; the
control row's other cells and every other sheet stay as they are (checked before saving). Exit codes: 0 written, already
proven (nothing to do) or dry run valid; 2 the cross-check found problems (original untouched); 4 invalid, the file is not what
was applied, a token out of date or another process writing: nothing written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from .apply import _project, _resolve, _sha, route_for

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
from common import WorkspaceBusy, workspace_lock

from .base import ConfigError, ExcelAdapter, InvalidBatch, batch_hash, result_entry

TOKEN_VERSION = "v1"


def _row_index(ad: ExcelAdapter, batch_id: str):
    """(sheet, 1-based row number, the row as a dict) of the control row of `batch_id`, or None."""
    ws = ad._control_ws(create=False)
    if ws is None:
        return None
    head = ad._control_names(ws)
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        d = dict(zip(head, row))
        if row[0] and d.get("batch_id") == batch_id:
            return ws, n, d
    return None


def plan(ad: ExcelAdapter, batch_id: str, path: str) -> dict:
    """Read only. What would be written, or why nothing can be: raises InvalidBatch/ConfigError. The result:
    {"proven": True} when the row already has a proven origin, else {"row", "entries", "file_hash"}."""
    found = _row_index(ad, batch_id)
    if found is None:
        raise ConfigError(f"the control sheet has no row for '{batch_id}' (batches applied here: "
                          + ", ".join(sorted(str(b.get("batch_id")) for b in ad.applied_batches() if b.get("batch_id"))[:8]) + " ...)")
    _ws, n, row = found
    origin = ad.row_origin(row)
    if origin["state"] in ("stored", "rebuilt", "no-results"):
        return {"proven": True, "state": origin["state"]}
    with open(path, encoding="utf-8-sig") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise InvalidBatch([f"{path}: expected {{\"results\": [...]}}"])
    got = batch_hash(doc["results"])
    if got != row.get("hash") and doc.get("translated_from") != row.get("hash"):
        raise InvalidBatch([(f"{os.path.basename(path)} is not what was applied as '{batch_id}': its results hash to {got[:16]}…, the "
                             f"control row recorded {str(row.get('hash'))[:16]}…. Nothing was written. Restore the exact file "
                             f"(Git history: `git log --follow -- runs/{batch_id}.json`); a file that does not match is never "
                             "accepted by this tool")])
    entries = [result_entry(r) for r in doc["results"] if isinstance(r, dict)]
    return {"proven": False, "row": n, "entries": entries, "file_hash": got}


def reconcile_token(workspace: str, ad: ExcelAdapter, path: str, batch_id: str, entries: list) -> str:
    parts = [("batch", batch_id), ("file", _sha(path)), ("workbook", os.path.relpath(ad.path, workspace)),
             ("workbook content", _sha(ad.path)), ("project.yaml", _sha(os.path.join(workspace, "project.yaml"))),
             ("entries", hashlib.sha256(json.dumps(entries).encode()).hexdigest())]
    return f"{TOKEN_VERSION}-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def reconcile(workspace: str, batch_id: str, path: str, dry_run: bool = False, expect: str | None = None,
              workbook: str | None = None, require_token: bool = False) -> int:
    if dry_run and expect:
        raise ConfigError("--expect goes with the real write, not with --dry-run")
    path = _resolve(workspace, path, "run log")
    if dry_run:
        return _reconcile(workspace, batch_id, path, True, None, workbook)
    try:
        with workspace_lock(workspace):
            return _reconcile(workspace, batch_id, path, False, expect, workbook, require_token)
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def _reconcile(workspace: str, batch_id: str, path: str, dry_run: bool, expect: str | None,
               workbook: str | None = None, require_token: bool = False) -> int:
    # the destination as the apply resolves it (technique-excel.md section 1.2): an explicit path validated and never
    # ignored, else the batch's route; the token names that workbook
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    route = route_for(workspace, _project(workspace), batch_id, doc if isinstance(doc, dict) else {}, workbook)
    if route.problem:
        raise ConfigError(f"{route.problem}. Nothing was written.")
    ad = ExcelAdapter.from_project(workspace, file=route.file)
    ad.load()
    p = plan(ad, batch_id, path)
    print(f"Workbook: {os.path.realpath(ad.path)} (from {route.source})")
    if p["proven"]:
        print(f"Nothing to reconcile: the origin of '{batch_id}' is already proven ({p['state']}). The workbook was not changed.")
        return 0
    token = reconcile_token(workspace, ad, path, batch_id, p["entries"])
    print(f"'{batch_id}': {os.path.basename(path)} is what was applied (same hash); {len(p['entries'])} result key(s) would be "
          f"written to the cell result_keys of control row {p['row']}; nothing else changes.")
    if dry_run:
        print(f"DRY RUN: nothing is written · token {token}")
        print("Write, after approving THIS: python -m adapters.excel.reconcile "
              f"{workspace} {batch_id} --from {os.path.relpath(path, workspace)}"
              + (f" --workbook {route.file}" if workbook else "") + f" --expect {token}")
        return 0
    if require_token and expect is None:
        from .apply import NEEDS_TOKEN
        raise ConfigError(NEEDS_TOKEN.format(where=os.path.realpath(ad.path)))
    if expect is not None:
        if not str(expect).startswith(TOKEN_VERSION + "-"):
            raise ConfigError(f"the preview {expect} is not a token of this command (v1-...): run --dry-run first. Nothing was written.")
        if expect != token:
            raise ConfigError(f"the preview {expect} is out of date (now {token}): the file, the workbook or project.yaml changed "
                              "since the dry run. Run --dry-run again. Nothing was written.")
    ws = ad._control_ws(create=True)             # adds the column result_keys when the sheet has none (older sheets)
    head = ad._control_names(ws)
    col = head.index("result_keys") + 1
    before = {(c.row, c.column): c.value for row in ws.iter_rows() for c in row}
    ws.cell(p["row"], col).value = json.dumps(p["entries"])
    after = {(c.row, c.column): c.value for row in ws.iter_rows() for c in row}
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    allowed = {(p["row"], col)} | ({(1, col)} if before.get((1, col)) != after.get((1, col)) else set())
    if changed - allowed:
        raise InvalidBatch([f"cells other than the key cell would change ({sorted(changed - allowed)[:3]}): stopped, nothing saved"])
    out = ad.save_if_clean()
    if not out["saved_original"]:
        print(f"\nCross-check: {len(out['problems'])} problem(s) -- ORIGINAL UNTOUCHED. Result saved separately in: {out['file']}")
        for pr in out["problems"]:
            print(" -", pr)
        return 2
    print("Written (only the result_keys cell). Cross-check: 0 problems. Saved.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Writes the missing result keys of a control row from a run log PROVEN to be what was applied")
    ap.add_argument("workspace")
    ap.add_argument("batch_id")
    ap.add_argument("--from", dest="source", required=True, metavar="RUN_LOG", help="the run log (restored exact file)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--expect", metavar="TOKEN", help="the token of the dry run the user approved: needed to write")
    ap.add_argument("--workbook", metavar="PATH", help="an explicit destination inside the workspace (instead of "
                                                       "$QA_WORKBOOK): never ignored (technique-excel.md 1.2)")
    a = ap.parse_args(argv)
    try:
        return reconcile(a.workspace, a.batch_id, a.source, a.dry_run, a.expect, a.workbook, require_token=True)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING WRITTEN -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
