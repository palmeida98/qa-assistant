"""
Adds the approved cases of a cases file to a workspace's workbook, in one step
(technique-excel.md section 4.1; plan 10.6):

    load -> add_cases -> save_if_clean -> recalc -> check_summary

The configuration comes from the `excel:` section of the workspace's project.yaml
(cases.fields maps the model's fields to columns; title is required).

    python -m adapters.excel.add_cases <workspace> <cases.json>
        [--platform android|ios|both|<a platform of the project>]
        [--dry-run] [--label "[2026-10-04, cases_20261004_01]"] [--no-recalc]
        [--now 2026-10-04T10:00:00] [--expect <token of the dry run>]

--platform chooses the workbook from project.yaml (the `excel.workbooks` entry of that platform, else `excel.file`, the
workbook of the platforms without an entry of their own): no QA_WORKBOOK and no copy of a file. `both` adds to every workbook
of the project in one command, validating all of them before writing any; its dry run prints ONE combined token. A case that
says another platform in its `platform` field is refused for this destination. Without --platform the behaviour is the old one
(excel.file, or $QA_WORKBOOK) and, when the project has several workbooks, a notice says so.

An ID proposed by ANOTHER cases file (cases/*.json) with different content that is not yet in the target workbook is refused:
the reserved IDs of the proposals of a session cannot collide.

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

from .base import ConfigError, ExcelAdapter, InvalidBatch, batch_hash, workbook_patterns

TOKEN_VERSION = "v1"


def read_cases(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise InvalidBatch([f"{path}: expected {{\"cases\": [...]}} (technique-excel.md section 4.1)"])
    return data["cases"]


def project_targets(workspace: str) -> list[tuple[str, str]]:
    """(platform, workbook file) of every workbook the project declares: each `excel.workbooks` entry under its `platform`
    (else its key), and `excel.file` for the platforms without an entry of their own (listed as `default`)."""
    excel = (_project_dict(workspace).get("excel") or {})
    out = []
    for key, file, _pats in workbook_patterns(excel):
        plat = str(((excel.get("workbooks") or {}).get(key) or {}).get("platform") or key).lower()
        out.append((plat, file))
    out.append(("default", str(excel.get("file") or "")))
    return out


def resolve_platform(workspace: str, platform: str | None) -> list[tuple[str, str]]:
    """The destinations of `--platform`: [(label, file)]. `both` is every workbook; a platform with an entry of its own
    is that workbook; any other platform is `excel.file` (the workbook of the platforms without an entry). No platform
    means the old behaviour (an empty list: from_project decides, honouring $QA_WORKBOOK)."""
    if not platform:
        return []
    targets = project_targets(workspace)
    named = [(p, f) for p, f in targets if p != "default" and f]
    default = next((f for p, f in targets if p == "default"), "")
    want = platform.strip().lower()
    if want == "both":
        out = [(p, f) for p, f in named] + ([("default", default)] if default else [])
        if len(out) < 2:
            raise ConfigError("--platform both needs the project to declare more than one workbook "
                              "(excel.file and excel.workbooks)")
        return out
    own = next((f for p, f in named if p == want), None)
    if own:
        return [(want, own)]
    if not default:
        raise ConfigError(f"--platform {platform}: the project declares no workbook for it (excel.file is empty)")
    return [(want, default)]


def _case_platforms(case: dict) -> set[str]:
    """The platforms a case says it is for ('android', 'ios'); empty when it says nothing."""
    text = str(case.get("platform") or "").lower()
    out = set()
    if "android" in text:
        out.add("android")
    if "ios" in text:
        out.add("ios")
    if any(w in text for w in ("both", "ambas", "todas", "all")):
        out |= {"android", "ios"}
    return out


def platform_problems(cases: list, label: str) -> list[str]:
    """Cases that say a platform other than the destination's (`label`; `default` and unknown labels are not checked)."""
    if label not in ("android", "ios"):
        return []
    return [f"{c.get('id')}: says platform {c.get('platform')!r}, not {label}: it is not added to this workbook"
            for c in cases if _case_platforms(c) and label not in _case_platforms(c)]


def _norm(case: dict) -> str:
    return json.dumps({k: v for k, v in case.items() if k != "platform"}, ensure_ascii=False, sort_keys=True)


def reserved_conflicts(workspace: str, cases_path: str, cases: list, ad: ExcelAdapter) -> list[str]:
    """IDs of this file that ANOTHER cases file (cases/*.json) proposes with different content (the `platform` field aside)
    while that file is not yet added to the target workbook. Same content in a sibling file (e.g. the iOS copy) is fine."""
    folder = os.path.join(workspace, "cases")
    mine = {str(c.get("id")): _norm(c) for c in cases if isinstance(c, dict)}
    problems = []
    if not os.path.isdir(folder):
        return problems
    added = {x["hash"] for x in ad.applied_batches()}
    in_workbook = {str(v).strip() for _, ws, h in ad._case_sheets() for _, v in ad._case_rows(ws, h)}
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not name.endswith(".json") or os.path.abspath(full) == os.path.abspath(cases_path):
            continue
        try:
            other = read_cases(full)
        except (OSError, ValueError, InvalidBatch):
            continue
        if batch_hash([{"add_cases": other}]) in added:
            continue
        for c in other:
            cid = str(c.get("id")) if isinstance(c, dict) else ""
            if cid in mine and cid not in in_workbook and _norm(c) != mine[cid]:   # an ID already in the workbook is refused by validate
                problems.append(f"{cid}: also proposed in cases/{name} with different content and not yet added: "
                                "resolve the clash (rename or align one of them) before adding")
    return problems


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


def combined_token(parts: list[tuple[str, str]]) -> str:
    """One token for an add to several workbooks: the per-destination tokens, in order, hashed together."""
    return f"{TOKEN_VERSION}-" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def add(workspace: str, cases_path: str, dry_run: bool = False, label: str | None = None,
        recalc: bool = True, now: datetime | None = None, expect: str | None = None,
        cfg: dict | None = None, command_options: str = "", platform: str | None = None) -> int:
    if dry_run and expect:
        raise ConfigError("--expect goes with the real add, not with --dry-run")
    cases_path = _resolve(workspace, cases_path, "cases file")
    targets = resolve_platform(workspace, platform)
    if len(targets) > 1:
        return _add_both(workspace, cases_path, targets, dry_run, label, recalc, now, expect, cfg, command_options, platform)
    file = targets[0][1] if targets else None
    plat = targets[0][0] if targets else None
    opts = command_options + (f" --platform {platform}" if platform else "")
    if dry_run:
        return _add(workspace, cases_path, True, label, recalc, now, None, cfg, opts, file, plat)
    try:
        with workspace_lock(workspace):         # one writer at a time, as the apply does
            return _add(workspace, cases_path, False, label, recalc, now, expect, None, "", file, plat)
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def _targets_state(workspace: str, cases_path: str, targets: list, label, recalc, now) -> list:
    """Per destination, everything checked BEFORE any write: [(platform, file, adapter, token)]."""
    cases = read_cases(cases_path)
    state = []
    for plat, file in targets:
        ad = ExcelAdapter.from_project(workspace, file=file)
        ad.now = now
        ad.load()
        problems = platform_problems(cases, plat) + reserved_conflicts(workspace, cases_path, cases, ad) \
            + ad.validate_cases(cases)
        if problems:
            raise InvalidBatch([f"[{plat}: {file}] {p}" for p in problems])
        state.append((plat, file, ad, cases_token(workspace, ad, cases_path, cases, label, recalc, now)))
    return state


def _add_both(workspace, cases_path, targets, dry_run, label, recalc, now, expect, cfg, command_options, platform) -> int:
    if len({f for _, f in targets}) != len(targets):
        raise ConfigError("--platform both: two destinations are the same workbook")
    if dry_run:
        state = _targets_state(workspace, cases_path, targets, label, recalc, now)
        token = combined_token([(p, t) for p, _f, _ad, t in state])
        print(f"DRY RUN add to {len(state)} workbooks ({', '.join(p for p, *_ in state)}): nothing is written")
        for plat, file, ad, tok in state:
            print(f"  {plat}: {os.path.relpath(ad.path, workspace)} · token {tok}")
        print(f"Combined token: {token}  (add with --platform {platform} --expect {token}: refused if anything changed)")
        for plat, file, ad, _tok in state:
            _add(workspace, cases_path, True, label, recalc, now, None, cfg,
                 command_options + f" --platform {plat}", file, plat, review_tag=plat)
        return 0
    try:
        with workspace_lock(workspace):
            state = _targets_state(workspace, cases_path, targets, label, recalc, now)
            token = combined_token([(p, t) for p, _f, _ad, t in state])
            if expect is None or str(expect) != token:
                raise ConfigError(f"--platform {platform} needs the combined token of a dry run "
                                  f"(now {token}, given {expect}): run --dry-run and review it. Nothing was written.")
            rc = 0
            for plat, file, _ad, tok in state:
                print(f"\n== {plat} ==")
                rc = _add(workspace, cases_path, False, label, recalc, now, tok, None, "", file, plat) or rc
            return rc
    except WorkspaceBusy as e:
        raise ConfigError(f"{e} Nothing was written.") from None


def _add(workspace: str, cases_path: str, dry_run: bool, label: str | None, recalc: bool, now: datetime | None,
         expect: str | None, cfg: dict | None, command_options: str, file: str | None = None,
         plat: str | None = None, review_tag: str | None = None) -> int:
    ad = ExcelAdapter.from_project(workspace, file=file)
    ad.now = now
    ad.load()
    cases = read_cases(cases_path)
    extra = platform_problems(cases, plat or "") + reserved_conflicts(workspace, cases_path, cases, ad)
    if extra and not (not dry_run and ad.applied_batches() and any(
            x["hash"] == batch_hash([{"add_cases": cases}]) for x in ad.applied_batches())):
        raise InvalidBatch(extra)
    if not plat and not file and len(workbook_patterns(_project_dict(workspace).get("excel") or {})) >= 1 \
            and not os.environ.get("QA_WORKBOOK"):
        print("Notice: the project has several workbooks and no --platform was given: writing to excel.file "
              "(use --platform android|ios|both to choose)")
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
            tag = f".{review_tag}" if review_tag else ""
            review = os.path.join(os.path.dirname(cases_path), f"{os.path.splitext(os.path.basename(cases_path))[0]}{tag}.dryrun.md")
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
    ap.add_argument("--platform", metavar="PLATFORM",
                    help="android | ios | both | a platform of the project: the workbook comes from project.yaml (both = "
                         "every workbook, one combined token); without it excel.file or $QA_WORKBOOK, as before")
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
        return add(a.workspace, a.cases, a.dry_run, a.label, not a.no_recalc, a.now, a.expect, cfg, options,
                   platform=a.platform)
    except (InvalidBatch, ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"\nNOTHING ADDED -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
