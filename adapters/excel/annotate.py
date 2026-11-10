"""
Adds a dated note to the Notes column of cases that already have a result, WITHOUT touching their status or their
observed result (technique-excel.md section 4.2; plan Q10 of 10.11.2026):

    python -m adapters.excel.annotate <workspace> <annotations.json>
        [--dry-run] [--label "[2026-11-10, annotations_20261110_01]"] [--no-recalc]
        [--now 2026-11-10T10:00:00] [--expect <token of the dry run>]

The annotations file is {"annotations": [{"case": "TC-X-001", "note": "text"}, ...]}. A case appears once; the
note is appended to the case's notes after a blank line, as "<label> <note>", and nothing else in the row changes:
the status and the observed result are compared before and after and a difference stops the write. It is the way to
record that a result is UNDER REVIEW (or any later remark) when the project's workbook cannot hold that state and the
workbook is never edited by hand: the history stays, the remark is visible to anyone who opens the workbook.

The dry run prints a preview token (`v1-<hash>`): the annotations file, the workbook (path and content),
project.yaml, the plan (case, sheet, row, the notes now and the note) and the options that change what is written. With
`--expect <token>` the real write refuses, writing nothing, when any of them changed since the dry run. The write holds
the workspace's lock like the apply does and is recorded in the control sheet like a batch: the same annotations again
change nothing, and the same file name with another content is refused. A note already contained in the case's notes is
refused (no duplicate). The dry run's review (every note in full, before and after) is written next to the annotations
file, never in runs/.

Exit codes: 0 written (or already written: nothing changes; with --dry-run, valid); 2 the cross-check found problems
(original untouched, result in <name>.REVIEWED.xlsx); 3 saved, but the recalculation failed; 4 invalid file or
configuration, a duplicate note, a token out of date, or another process writing the workspace: nothing written;
5 saved and recalculated, but the workbook's summary does not match the direct count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

from .apply import _resolve, _sha

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
from common import WorkspaceBusy, atomic_write, workspace_lock, workspace_path

from .base import ConfigError, ExcelAdapter, InvalidBatch, batch_hash

TOKEN_VERSION = "v1"
MAX_NOTE = 2000


def read_annotations(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("annotations"), list):
        raise InvalidBatch([(f"{path}: expected {{\"annotations\": [{{\"case\": ..., \"note\": ...}}]}} "
                             "(technique-excel.md section 4.2)")])
    return data["annotations"]


def _find_case(ad: ExcelAdapter, case_id: str):
    for sheet, ws, h in ad._case_sheets():
        for r, v in ad._case_rows(ws, h):
            if str(v) == case_id:
                return sheet, ws, h, r
    return None


def _columns(ad: ExcelAdapter) -> dict:
    return ad.cfg["cases"]["columns"]


def plan_annotations(ad: ExcelAdapter, annotations: list, label: str) -> list[dict]:
    """Read only. One entry per case: where it is, its notes now, the note and the notes after. Raises InvalidBatch
    with every problem found (nothing is written by this function)."""
    problems: list[str] = []
    if not annotations:
        raise InvalidBatch(["the annotations file has no annotation"])
    cols, seen, plan = _columns(ad), set(), []
    notes_col = cols.get("notes")
    if not notes_col:
        raise InvalidBatch(["annotating needs the notes column (excel.cases.columns.notes) in the configuration"])
    for i, a in enumerate(annotations, 1):
        where = f"annotation {i}"
        if not isinstance(a, dict) or set(a) - {"case", "note"}:
            problems.append(f"{where}: only 'case' and 'note' are accepted")
            continue
        case, note = a.get("case"), a.get("note")
        if not isinstance(case, str) or not case.strip():
            problems.append(f"{where}: 'case' is required")
            continue
        if not isinstance(note, str) or not note.strip():
            problems.append(f"{where} ({case}): 'note' is required and cannot be empty")
            continue
        if len(note) > MAX_NOTE:
            problems.append(f"{where} ({case}): the note has {len(note)} characters (limit {MAX_NOTE})")
            continue
        if case in seen:
            problems.append(f"{where}: {case} appears twice (one annotation per case in a file)")
            continue
        seen.add(case)
        found = _find_case(ad, case)
        if not found:
            problems.append(f"{where}: case {case} is not in the workbook")
            continue
        sheet, ws, h, row = found
        if notes_col not in h:
            problems.append(f"{where} ({case}): the sheet '{sheet}' has no '{notes_col}' column")
            continue
        existing = ws.cell(row, h[notes_col]).value
        existing_text = "" if existing in (None, "") else str(existing)
        if note.strip() in existing_text:
            problems.append(f"{where} ({case}): this note is already in the case's notes: not added twice")
            continue
        text = f"{label} {note.strip()}"
        after = (existing_text.rstrip() + "\n\n" + text).strip() if existing_text else text
        plan.append({"case": case, "sheet": sheet, "row": row, "note": note.strip(), "before": existing_text,
                     "after": after, "status": ws.cell(row, h[cols["status"]]).value})
    if problems:
        raise InvalidBatch(problems)
    return plan


def apply_annotations(ad: ExcelAdapter, annotations: list, label: str, batch_id: str | None = None) -> dict:
    """Writes the notes (only the Notes cell of each case), checks that the status and the observed result did not
    change and records the write in the control sheet. The same annotations again: {"added": False}."""
    ad._need_wb()
    h_batch = batch_hash([{"annotate": annotations}])
    previous = next((x for x in ad.applied_batches() if x["hash"] == h_batch), None)
    if previous:
        return {"added": False, "hash": h_batch, "plan": [], "reason": f"annotations already written at {previous['applied_at']}"}
    if batch_id:
        same_id = next((x for x in ad.applied_batches() if x.get("batch_id") == batch_id), None)
        if same_id:
            raise InvalidBatch([(f"the annotations file '{batch_id}' was already written at {same_id['applied_at']} with a "
                                 "DIFFERENT content (it was edited after being written). New notes go in a new file, with "
                                 "another name.")])
    plan = plan_annotations(ad, annotations, label)
    cols = _columns(ad)
    guarded = [c for c in (cols["status"], cols.get("actual_result")) if c]
    sheets = {sheet: (ws, h) for sheet, ws, h in ad._case_sheets()}
    snapshot = {}
    for p in plan:
        ws, h = sheets[p["sheet"]]
        snapshot[p["case"]] = [ws.cell(p["row"], h[c]).value for c in guarded if c in h]
    for p in plan:
        ws, h = sheets[p["sheet"]]
        ws.cell(p["row"], h[cols["notes"]]).value = p["after"]
    for p in plan:         # the guarantee of this command: nothing but the notes changed
        ws, h = sheets[p["sheet"]]
        if [ws.cell(p["row"], h[c]).value for c in guarded if c in h] != snapshot[p["case"]]:
            raise InvalidBatch([(f"{p['case']}: the status or the observed result changed while writing: stopped, "
                                 "nothing was saved")])
    ctl = ad._control_ws(create=True)
    record = {"hash": h_batch, "applied_at": ad._now().isoformat(timespec="seconds"), "label": label,
              "items": len(annotations), "new_defects": "", "batch_id": batch_id,
              "result_keys": "[]"}            # a row of notes: it applies no results
    ctl.append([record.get(name) for name in ad._control_names(ctl)])
    return {"added": True, "hash": h_batch, "plan": plan}


def annotations_token(workspace: str, ad: ExcelAdapter, path: str, plan: list, label: str | None, recalc: bool,
                      now: datetime | None) -> str:
    """What a dry run saw, as `v1-<hash>`. The plan is hashed WITHOUT the label and the date inside the new notes (those
    are options of their own), so the real clock is not part of it."""
    core = json.dumps([{k: p[k] for k in ("case", "sheet", "row", "note", "before")} for p in plan],
                      ensure_ascii=False, sort_keys=True)
    parts = [("annotations file", _sha(path)), ("workbook", os.path.relpath(ad.path, workspace)),
             ("workbook content", _sha(ad.path)), ("project.yaml", _sha(os.path.join(workspace, "project.yaml"))),
             ("plan", hashlib.sha256(core.encode()).hexdigest()),
             ("label", label if label else "<default: [date of the write, file name]>"), ("recalc", bool(recalc)),
             ("now", now.isoformat() if now else "<clock of the write>")]
    return f"{TOKEN_VERSION}-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def _fence(text: str) -> str:
    return "~~~~" if "~~~" in text else "~~~"


def review_text(workspace: str, ad: ExcelAdapter, path: str, plan: list, token: str, command: str, label: str | None,
                recalc: bool, now: datetime | None) -> str:
    out = [f"# Dry run of annotating with {os.path.basename(path)}: complete review", "",
           ("Nothing was written to the workbook. Only the Notes cell of each case below changes; the status and the "
            "observed result are not touched. This file is replaced by the next dry run of the same file."), "",
           "## What the token covers", "",
           f"- **Token: `{token}`**: the write with `--expect` is refused, writing nothing, if any of this changes",
           f"- Target: `{os.path.relpath(ad.path, workspace)}` (SHA-256 `{_sha(ad.path)}`)",
           f"- Annotations file `{os.path.relpath(path, workspace)}` (SHA-256 `{_sha(path)}`)",
           f"- SHA-256 of project.yaml: `{_sha(os.path.join(workspace, 'project.yaml'))}`",
           (f"- Options: label {label!r}, recalculation {'on' if recalc else 'off'}, "
            f"date {now.isoformat() if now else '<clock of the write>'}"), "", f"## Cases ({len(plan)})", ""]
    for p in plan:
        out += [f"### {p['case']} -> `{p['sheet']}` row {p['row']} (status kept: {p['status']})", "",
                "- **Note to add** (the label and the date are put before it when written)", ""]
        for block, title in ((p["note"], None), (p["before"], "Notes now"), (p["after"], "Notes after (with the label of this review)")):
            if title:
                out += [f"- **{title}**", ""]
            fence = _fence(block or "")
            out += [fence, block if block else "(empty)", fence, ""]
    return "\n".join(out + ["## Command (only after the user approves THIS review)", "", "~~~", command, "~~~", ""])


def annotate(workspace: str, path: str, dry_run: bool = False, label: str | None = None, recalc: bool = True,
             now: datetime | None = None, expect: str | None = None) -> int:
    if dry_run and expect:
        raise ConfigError("--expect goes with the real write, not with --dry-run")
    path = _resolve(workspace, path, "annotations file")
    if dry_run:
        return _annotate(workspace, path, True, label, recalc, now, None)
    try:
        with workspace_lock(workspace):         # one writer at a time, as the apply does
            return _annotate(workspace, path, False, label, recalc, now, expect)
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def _annotate(workspace: str, path: str, dry_run: bool, label: str | None, recalc: bool, now: datetime | None,
              expect: str | None) -> int:
    ad = ExcelAdapter.from_project(workspace)
    ad.now = now
    ad.load()
    annotations = read_annotations(path)
    file_id = os.path.splitext(os.path.basename(path))[0]
    shown_label = label or f"[{ad._now().date().isoformat()}, {file_id}]"
    print(f"Workbook: {ad.path}")
    print(f"Annotations: {path} ({len(annotations)})")

    if dry_run:
        plan = plan_annotations(ad, annotations, shown_label)
        token = annotations_token(workspace, ad, path, plan, label, recalc, now)
        options = (f" --label {json.dumps(label)}" if label else "") + (" --no-recalc" if not recalc else "") \
            + (f" --now {now.isoformat()}" if now else "")
        command = f"python -m adapters.excel.annotate {workspace} {os.path.relpath(path, workspace)}{options} --expect {token}"
        review = workspace_path(workspace, *os.path.relpath(
            os.path.join(os.path.dirname(path), f"{file_id}.dryrun.md"), workspace).replace(os.sep, "/").split("/"))
        atomic_write(review, review_text(workspace, ad, path, plan, token, command, label, recalc, now))
        print(f"DRY RUN annotate {len(plan)} case(s): nothing is written to the workbook")
        print(f"Target: {os.path.relpath(ad.path, workspace)} · token {token}")
        for p in plan:
            print(f"  {p['case']} -> '{p['sheet']}' row {p['row']} (status kept: {p['status']})")
        print(f"Review (every note in full, before and after, hashes, token): {os.path.relpath(review, workspace)}")
        print(f"Write, after approving THAT review: {command}")
        return 0

    h_batch = batch_hash([{"annotate": annotations}])
    already = next((x for x in ad.applied_batches() if x["hash"] == h_batch), None)
    if already:         # a repeat is harmless: it needs no token and changes nothing
        print(f"Nothing written: these annotations were already written at {already['applied_at']}. The workbook was not changed.")
        return 0
    if expect is not None:
        plan = plan_annotations(ad, annotations, shown_label)
        if not str(expect).startswith(TOKEN_VERSION + "-"):
            raise ConfigError(f"the preview {expect} is not a token of this command (v1-...): run --dry-run and "
                              "review it. Nothing was written.")
        current = annotations_token(workspace, ad, path, plan, label, recalc, now)
        if current != expect:
            raise ConfigError(f"the preview {expect} is out of date (now {current}): the annotations file, the workbook, "
                              "project.yaml, a note already in the case or an option (--label, --no-recalc, --now) changed "
                              "since the dry run. Run --dry-run again and review it. Nothing was written.")

    r = apply_annotations(ad, annotations, shown_label, file_id)
    if not r["added"]:
        print(f"Nothing written: {r['reason']}. The workbook was not changed.")
        return 0
    print("Notes added (status and observed result untouched):")
    for p in r["plan"]:
        print(f"  {p['case']} -> '{p['sheet']}' row {p['row']}")
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
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Adds a dated note to the Notes column of cases, keeping status and result")
    ap.add_argument("workspace")
    ap.add_argument("annotations")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the notes, write nothing")
    ap.add_argument("--label", help="text before each note (default: [date, file name])")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--expect", metavar="TOKEN",
                    help="the preview token of a dry run: refuse (nothing written) when the file, the workbook, "
                         "project.yaml or an option changed since")
    ap.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS",
                    help="instant recorded instead of the clock")
    a = ap.parse_args(argv)
    try:
        return annotate(a.workspace, a.annotations, a.dry_run, a.label, not a.no_recalc, a.now, a.expect)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING WRITTEN -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
