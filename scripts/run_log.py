"""
Writes the session's run log (methodology 7.1; plan 11.2), instead of a helper improvised in each session.

    python scripts/run_log.py <workspace> add <batch> <case> <status> <actual>
        [--bug ID] [--new-defect FILE.json] [--evidence PATH ...] [--environment KEY] [--build VERSION]

- add: appends the result of <case> to runs/batch_<batch>.json, or replaces it when the case is already there (one
  item per case, the adapter refuses a case repeated); "ts" is now. <batch> may be given with or without the
  "batch_" prefix. --new-defect reads a JSON object (the bug approved by the user, or on a runner a proposal with
  "proposed": true, methodology 9.4); --environment/--build set the run block (the Jira and Azure DevOps adapters
  need it). The file is written whole and atomically.
- The status must be in the project's vocabulary (excel.cases.statuses, jira.results.statuses or
  azure_devops.results.outcomes) or be `Question` (reclassified before the checkpoint, methodology 8.5).
- A batch already applied is never touched: its name is in the workbook's control sheet (the Excel adapter's
  applied_batches) or it has a record in tracker/sent/ (Jira, Azure DevOps). Appending to it made the adapter refuse
  it ("already applied ... with a DIFFERENT content"); a correction goes to a new batch, whose name is suggested.

Exit codes: 0 written · 1 refused (the batch was already applied) · 2 usage or configuration (unknown status, bad
JSON, no project.yaml)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from common import read_project

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class Refused(Exception):
    """The batch was already applied (code 1)."""


class UsageError(Exception):
    """Usage or configuration (code 2)."""


def batch_id(batch: str) -> str:
    """batch_<name>: the run log's file name without .json (the adapter's batch id)."""
    name = batch.removesuffix(".json")
    name = name if name.startswith("batch_") else f"batch_{name}"
    if not NAME.fullmatch(name):
        raise UsageError(f"batch {batch!r}: letters, digits, '.', '_', '-' only")
    return name


def run_log_path(workspace: str, batch: str) -> str:
    return os.path.join(workspace, "runs", batch_id(batch) + ".json")


def vocabulary(proj: dict) -> list[str]:
    """The project's statuses: those of the tracker's section ([] when it has none)."""
    tracker = proj.get("tracker")
    if tracker == "excel":
        return [str(s) for s in ((proj.get("excel") or {}).get("cases") or {}).get("statuses") or []]
    section, key = {"jira": ("jira", "statuses"), "azure-devops": ("azure_devops", "outcomes")}.get(tracker, (None, None))
    if section:
        return [str(s) for s in (((proj.get(section) or {}).get("results") or {}).get(key) or {})]
    return []


MEANINGS = {"pass": ("Pass", "ok"), "fail": ("Fail", "fail"), "blocked": ("Blocked", "blocked"),
            "na": ("N/A", "na"), "not run": ("Not run", "pending")}


def project_status(proj: dict, meaning: str) -> str:
    """The project's word for a meaning a script writes on its own (pass, fail, blocked, na, not run): the status
    of that type in dashboard.states, else the English word when the vocabulary has it (11.8 pilot: a workflow wrote
    'Blocked' in a project whose word is 'Bloqueado')."""
    english, kind = MEANINGS[meaning]
    statuses = vocabulary(proj)
    if not statuses or english in statuses:
        return english
    states = (proj.get("dashboard") or {}).get("states") or {}
    word = next((str(k) for k, v in states.items() if v == kind and str(k) in statuses), None)
    if word is None:
        raise UsageError(f"no status of the project means {meaning!r}: add it to dashboard.states "
                         f"(e.g. {{\"<status>\": {kind}}}) -- the vocabulary is {statuses}")
    return word


def applied(workspace: str, proj: dict, name: str) -> str | None:
    """Where the batch `name` was already applied, or None. Excel: the workbook's control sheet (read only);
    Jira / Azure DevOps: a record in tracker/sent/."""
    tracker = proj.get("tracker")
    if tracker in ("jira", "azure-devops"):
        path = os.path.join(workspace, "tracker", "sent", f"{name}.json")
        return f"sent to {tracker} (record {os.path.relpath(path, workspace)})" if os.path.isfile(path) else None
    if tracker == "excel":
        from adapters.excel import ConfigError, ExcelAdapter
        try:
            ad = ExcelAdapter.from_project(workspace)
            ad.load()
        except (ConfigError, FileNotFoundError, KeyError, OSError) as e:
            print(f"warning: the workbook could not be read to check whether {name} was applied ({e})",
                  file=sys.stderr)
            return None
        hit = next((b for b in ad.applied_batches() if b.get("batch_id") == name), None)
        return f"applied to the workbook on {hit.get('applied_at')}" if hit else None
    return None


def next_name(workspace: str, proj: dict, name: str) -> str:
    """A free batch name for a correction: <name>_02, _03... (or the next number when it already ends in one)."""
    m = re.fullmatch(r"(.*?)(?:_(\d{2}))?", name)
    stem, n = m.group(1), int(m.group(2) or 1)
    while True:
        n += 1
        candidate = f"{stem}_{n:02d}"
        if not os.path.exists(os.path.join(workspace, "runs", candidate + ".json")) and \
                not applied(workspace, proj, candidate):
            return candidate


def read_log(path: str) -> dict:
    if not os.path.isfile(path):
        return {"results": []}
    try:
        with open(path, encoding="utf-8-sig") as f:
            doc = json.load(f)
    except ValueError as e:
        raise UsageError(f"{path} is not valid JSON ({e}): fix it by hand first") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
        raise UsageError(f"{path}: expected {{\"results\": [...]}} (methodology 7.1)")
    return doc


def write_log(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def add(workspace: str, batch: str, case: str, status: str, actual: str, bug: str | None = None,
        new_defect: dict | None = None, evidence: list[str] | None = None, environment: str | None = None,
        build: str | None = None, now: datetime | None = None) -> tuple[str, bool]:
    """Appends or replaces the result of `case`; (path, replaced?). Refused / UsageError as in the header."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    name = batch_id(batch)
    where = applied(workspace, proj, name)
    if where:
        raise Refused(f"{name} was already {where}: it is never changed; write the correction to a new batch, "
                      f"e.g. {next_name(workspace, proj, name)}, with only the cases to correct")
    statuses = vocabulary(proj)
    if not case.strip():
        raise UsageError("the case ID is empty")
    if statuses and status not in statuses and not status.lower().startswith("question"):
        raise UsageError(f"status {status!r} is not in the project's vocabulary {statuses} (or Question)")
    if new_defect is not None and not isinstance(new_defect, dict):
        raise UsageError("--new-defect must hold a JSON object")
    path = os.path.join(workspace, "runs", name + ".json")
    doc = read_log(path)
    item = {"tc": case.strip(), "status": status, "actual_result": actual,
            "ts": (now or datetime.now()).isoformat(timespec="seconds")}
    if bug:
        item["bug"] = bug
    if new_defect:
        item["new_defect"] = new_defect
    if evidence:
        item["evidence"] = evidence if len(evidence) > 1 else evidence[0]
    if environment or build:
        run = dict(doc.get("run") or {})
        run.update({k: v for k, v in (("environment", environment), ("build", build)) if v})
        doc = {**{k: v for k, v in doc.items() if k != "results"}, "run": run, "results": doc["results"]}
    at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict) and
               str(x.get("tc") or "").strip() == item["tc"]), None)
    if at is None:
        doc["results"].append(item)
    else:
        doc["results"][at] = item
    write_log(path, doc)
    return path, at is not None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Writes the session's run log (methodology 7.1).")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="append or replace one case's result in runs/batch_<batch>.json")
    p.add_argument("batch")
    p.add_argument("case")
    p.add_argument("status")
    p.add_argument("actual", help="what was observed (not the expected result copied)")
    p.add_argument("--bug", help="an EXISTING bug linked to the case")
    p.add_argument("--new-defect", metavar="FILE", help="JSON object of a new bug (approved; or \"proposed\": true)")
    p.add_argument("--evidence", nargs="+", metavar="PATH", help="paths under evidence/")
    p.add_argument("--environment", help="run.environment (a key of environments)")
    p.add_argument("--build", help="run.build (the version tested)")
    a = ap.parse_args(argv)
    try:
        new = None
        if a.new_defect:
            try:
                with open(a.new_defect, encoding="utf-8-sig") as f:
                    new = json.load(f)
            except (OSError, ValueError) as e:
                raise UsageError(f"--new-defect {a.new_defect}: {e}") from None
        path, replaced = add(a.workspace, a.batch, a.case, a.status, a.actual, a.bug, new, a.evidence,
                             a.environment, a.build)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"{'replaced' if replaced else 'added'}: {a.case} {a.status} in {path}")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
