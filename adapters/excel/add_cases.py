"""
Adds the approved cases of a cases file to a workspace's workbook, in one step
(technique-excel.md section 4.1; plan 10.6):

    load -> add_cases -> save_if_clean -> recalc -> check_summary

The configuration comes from the `excel:` section of the workspace's project.yaml
(cases.fields maps the model's fields to columns; title is required).

    python -m adapters.excel.add_cases <workspace> <cases.json>
        [--dry-run] [--label "[2026-10-04, cases_20261004_01]"] [--no-recalc]
        [--now 2026-10-04T10:00:00] [--expect <token of the dry run>]

The dry run prints a preview token (`v1-<hash>`): the cases file, the workbook it would change (path and content),
project.yaml, the plan (sheet, row, cells) and the options that change what is written. With `--expect <token>` the real
add refuses, writing nothing, when any of them changed since the dry run. The add holds the workspace's lock like the
apply does (one writer at a time). Cases already added are not added again, with or without a token.

Exit codes:
  0  added and saved (or the file was already added: nothing changes); with
     --dry-run, the cases are valid (nothing written)
  2  cross-check with problems: original untouched, result in <name>.REVIEWED.xlsx
  3  saved, but the recalculation failed (review before calling it done)
  4  invalid cases file or configuration (incl. an ID that exists, a candidate
     duplicate, or a file already added with another content), a token out of
     date, or another process writing the workspace: nothing written
  5  saved and recalculated, but the workbook's summary does not match the direct
     count (e.g. fixed ranges that do not reach the new rows): fix the formulas
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
import output
from common import WorkspaceBusy, atomic_write, workspace_lock, workspace_path

from .base import ConfigError, ExcelAdapter, InvalidBatch, batch_hash

TOKEN_VERSION = "v1"


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


def cases_token(workspace: str, ad: ExcelAdapter, cases_path: str, cases: list, label: str | None = None,
                recalc: bool = True, now: datetime | None = None) -> str:
    """What a dry run saw, as `v1-<hash>`: the cases file, the workbook that would change (its path and its content),
    project.yaml, the plan (the sheet, the row and the cells of every case) and the options that change what is
    written (the label as given, whether the workbook is recalculated, a fixed date). The real clock is not part of it."""
    plan = json.dumps(ad.plan_cases(cases), ensure_ascii=False, sort_keys=True)
    parts = [("cases file", _sha(cases_path)), ("workbook", os.path.relpath(ad.path, workspace)),
             ("workbook content", _sha(ad.path)), ("project.yaml", _sha(os.path.join(workspace, "project.yaml"))),
             ("plan", hashlib.sha256(plan.encode()).hexdigest()),
             ("label", label if label else "<default: [date of the add, file name]>"), ("recalc", bool(recalc)),
             ("now", now.isoformat() if now else "<clock of the add>")]
    return f"{TOKEN_VERSION}-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def review_text(workspace: str, ad: ExcelAdapter, cases_path: str, plan: list, token: str, command: str,
                label: str | None, recalc: bool, now: datetime | None) -> str:
    """The complete review of an add: the token and what it covers, then every cell of every case in full."""
    out = [f"# Dry run of adding {os.path.basename(cases_path)}: complete review", "",
           ("Nothing was written to the workbook. This file is replaced by the next dry run of the same cases file; the "
            "token below says which one it describes."), "", "## What the token covers", "",
           f"- **Token: `{token}`**: the add with `--expect` is refused, writing nothing, if any of this changes",
           f"- Target: `{os.path.relpath(ad.path, workspace)}` (SHA-256 `{_sha(ad.path)}`)",
           f"- Cases file `{os.path.relpath(cases_path, workspace)}` (SHA-256 `{_sha(cases_path)}`)",
           f"- SHA-256 of project.yaml: `{_sha(os.path.join(workspace, 'project.yaml'))}`",
           (f"- Options: label {label!r}, recalculation {'on' if recalc else 'off'}, "
            f"date {now.isoformat() if now else '<clock of the add>'}"), "", f"## Cases ({len(plan)})", ""]
    for p in plan:
        out += [f"### {p['id']} -> `{p['sheet']}` row {p['row']}", ""]
        for col, value in p["cells"].items():
            out += [f"- **{col}**", "", "~~~", str(value), "~~~", ""]
        if p["not_written"]:
            out += [f"Not written (no column in this sheet): {', '.join(p['not_written'])}", ""]
    return "\n".join(out + ["## Command (only after the user approves THIS review)", "", "~~~", command, "~~~", ""])


def add(workspace: str, cases_path: str, dry_run: bool = False, label: str | None = None,
        recalc: bool = True, now: datetime | None = None, expect: str | None = None,
        cfg: dict | None = None, command_options: str = "") -> int:
    if dry_run and expect:
        raise ConfigError("--expect goes with the real add, not with --dry-run")
    cases_path = _resolve(workspace, cases_path, "cases file")
    if dry_run:
        return _add(workspace, cases_path, True, label, recalc, now, None, cfg, command_options)
    try:
        with workspace_lock(workspace):         # one writer at a time, as the apply does
            return _add(workspace, cases_path, False, label, recalc, now, expect, None, "")
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def _add(workspace: str, cases_path: str, dry_run: bool, label: str | None, recalc: bool, now: datetime | None,
         expect: str | None, cfg: dict | None, command_options: str) -> int:
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
        plan = ad.plan_cases(cases)
        token = cases_token(workspace, ad, cases_path, cases, label, recalc, now)
        if cfg is not None and output.is_brief(cfg) and cfg["detail_files"]:
            command = (f"python -m adapters.excel.add_cases {workspace} {os.path.relpath(cases_path, workspace)}"
                       f"{command_options} --expect {token}")
            review = os.path.join(os.path.dirname(cases_path), f"{os.path.splitext(os.path.basename(cases_path))[0]}.dryrun.md")
            review = workspace_path(workspace, *os.path.relpath(review, workspace).replace(os.sep, "/").split("/"))
            atomic_write(review, review_text(workspace, ad, cases_path, plan, token, command, label, recalc, now))
            _print_brief(workspace, ad, plan, token, os.path.relpath(review, workspace), command, cfg)
            return 0
        print("Dry run -- nothing written. It would add:")
        _show(plan)
        print(f"Preview token: {token}  (add with --expect {token}: refused if anything changed since)")
        return 0

    already = next((x for x in ad.applied_batches() if x["hash"] == batch_hash([{"add_cases": cases}])), None)
    if already:         # a repeat is harmless: it needs no token and changes nothing
        print(f"Nothing added: cases already added at {already['applied_at']}. The workbook was not changed.")
        return 0
    if expect is not None:
        problems = ad.validate_cases(cases)
        if problems:
            raise InvalidBatch(problems)
        if not str(expect).startswith(TOKEN_VERSION + "-"):
            raise ConfigError(f"the preview {expect} is not a token of this command (v1-...): run --dry-run and "
                              "review it. Nothing was written.")
        current = cases_token(workspace, ad, cases_path, cases, label, recalc, now)
        if current != expect:
            raise ConfigError(f"the preview {expect} is out of date (now {current}): the cases file, the workbook, "
                              "project.yaml or an option (--label, --no-recalc, --now) changed since the dry run. "
                              "Run --dry-run again and review it. Nothing was written.")

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


def _project_dict(workspace: str) -> dict:
    try:
        from common import read_project
        return read_project(workspace)
    except Exception:       # noqa: BLE001 - the command's own configuration errors are reported where they matter
        return {}


def _print_brief(workspace: str, ad: ExcelAdapter, plan: list, token: str, review: str, command: str, cfg: dict) -> None:
    """A short card of the dry run: where, how many cases and which rows, what is not written, the review file and the
    command. The command and the review are never dropped; `--detail` prints every cell."""
    per: dict = {}
    for p in plan:
        per.setdefault(p["sheet"], []).append(p)
    lines: list[tuple[str, bool]] = [(f"DRY RUN add {len(plan)} case(s): nothing is written to the workbook", False),
                                     (f"Target: {os.path.relpath(ad.path, workspace)} · token {token}", False)]
    for sheet, items in per.items():
        ids = ", ".join(p["id"] for p in items)
        lines.append((f"{sheet}: rows {items[0]['row']}-{items[-1]['row']}: {ids}", False))
    skipped = sorted({f for p in plan for f in p["not_written"]})
    if skipped:
        lines.append((f"Fields without a column (not written): {', '.join(skipped)}", True))
    lines.append((f"Review (every cell in full, hashes, token): {review}", True))
    lines.append((f"Add, after approving THAT review: {command}", True))
    for text in output.fit(lines, cfg["max_lines"], "--detail", cfg["max_cols"]):
        print(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Adds approved cases to a workspace's workbook")
    ap.add_argument("workspace")
    ap.add_argument("cases")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the rows, write nothing")
    ap.add_argument("--label", help="label recorded in the control sheet (default: [date, file name])")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--expect", metavar="TOKEN",
                    help="the preview token of a dry run: refuse (nothing written) when the cases file, the workbook, "
                         "project.yaml or an option changed since")
    ap.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS",
                    help="instant recorded instead of the clock")
    output.add_flags(ap)
    a = ap.parse_args(argv)
    try:
        cfg = output.settings(_project_dict(a.workspace), output.cli_choice(a))
        options = (f" --label {json.dumps(a.label)}" if a.label else "") + (" --no-recalc" if a.no_recalc else "") \
            + (f" --now {a.now.isoformat()}" if a.now else "")
        return add(a.workspace, a.cases, a.dry_run, a.label, not a.no_recalc, a.now, a.expect, cfg, options)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING ADDED -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
