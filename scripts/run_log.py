"""
Writes the session's run log (methodology 7.1; plan 11.2), instead of a helper improvised in each session.

    python scripts/run_log.py <workspace> add <batch> <case> <status> <actual>
        [--bug ID] [--new-defect FILE.json] [--evidence PATH ...] [--environment KEY] [--build VERSION]
    python scripts/run_log.py <workspace> remove <batch> <case>

- The case must be in the project's library (the workbook, or tracker/cases.json): an unknown ID is exit 2 with the
  closest known ones, and a library that cannot be read is exit 2 too. The run block does not change once the batch
  has results: another build or environment is exit 1 (the build changed during the batch: plan a new batch).
- remove: takes one mistaken result out of a batch not yet applied. It shows the result, keeps the others, never
  deletes evidence (it lists the files), and refuses a batch already applied (exit 1) or a result that is not there (2).

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

Exit codes: 0 written · 1 refused (the batch was already applied, or another process is writing the workspace: nothing
was written, run the same command again) · 2 usage or configuration (unknown status, bad JSON, no project.yaml)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    read_project,
    replace_file,
    same_build,
    workspace_lock,
    workspace_path,
)

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
    try:
        return workspace_path(workspace, "runs", batch_id(batch) + ".json")
    except OutsideWorkspace as e:
        raise UsageError(str(e)) from None


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


def library_cases(workspace: str, proj: dict) -> tuple[set[str], str]:
    """The IDs of the project's case library and what it was read from: the workbook, or tracker/cases.json. A library
    that cannot be read is a UsageError, never an empty answer: an unknown ID must not pass for lack of a library."""
    from adapters.tickets import impact
    lib = impact.load_library(workspace, proj)
    if lib.status != impact.COMPLETE:
        hint = " (python -m adapters.tracker <workspace> import-cases writes it)" if "cases.json" in lib.source else ""
        raise UsageError(f"the project's case library cannot be read ({lib.source or proj.get('tracker')}: "
                         f"{lib.detail}){hint}: nothing was written")
    return {str(r["case"]).strip() for r in lib.rows}, lib.detail


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
        f.flush()
        os.fsync(f.fileno())        # on the disk before it takes the place of the old one
    replace_file(tmp, path)


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
    known, source = library_cases(workspace, proj)
    if case.strip() not in known:
        close = difflib.get_close_matches(case.strip(), sorted(known), n=3, cutoff=0.6)
        raise UsageError(f"case {case.strip()!r} is not in the project's library ({source})"
                         + (f"; did you mean {', '.join(close)}?" if close else "")
                         + ": nothing was written. A new case is added first (/generate-cases)")
    path = run_log_path(workspace, name)
    try:
        with workspace_lock(workspace):         # read, change and write the file as one step (audit finding D-02)
            return _add(path, doc_for=path, case=case, status=status, actual=actual, bug=bug, new_defect=new_defect,
                        evidence=evidence, environment=environment, build=build, now=now)
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None


def _add(path: str, *, doc_for: str, case: str, status: str, actual: str, bug, new_defect, evidence, environment, build,
         now) -> tuple[str, bool]:
    doc = read_log(doc_for)
    _same_context(doc, environment, build)
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
        # the block is only written while the batch has no results: after that it is the record of where they came from
        run.update({k: v for k, v in (("environment", environment), ("build", build))
                    if v and (not doc["results"] or k not in run)})
        doc = {**{k: v for k, v in doc.items() if k != "results"}, "run": run, "results": doc["results"]}
    at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict) and
               str(x.get("tc") or "").strip() == item["tc"]), None)
    if at is None:
        doc["results"].append(item)
    else:
        doc["results"][at] = item
    write_log(path, doc)
    return path, at is not None


def _same_context(doc: dict, environment: str | None, build: str | None) -> None:
    """The run block is the record of where the results came from: once the batch has results it does not change. A
    build that changes during a batch ends that batch (technique-mobile.md section 1.4)."""
    if not doc.get("results"):
        return
    run = doc.get("run") or {}
    if build and run.get("build") and not same_build(str(run["build"]), build):
        raise Refused(f"the build changed during the batch: it started on {run['build']!r} and this result says "
                      f"{build!r}. Stop this batch (its results stay as they are) and plan a new batch with the right "
                      "context (/plan-batch)")
    if environment and run.get("environment") and str(run["environment"]) != environment:
        raise Refused(f"the environment changed during the batch: it started on {run['environment']!r} and this result "
                      f"says {environment!r}. Plan a new batch (/plan-batch)")


def remove(workspace: str, batch: str, case: str) -> tuple[str, dict]:
    """Takes one mistaken result out of a batch not yet applied: (path, the result removed). The others stay as they
    are, evidence is never deleted, and the read, the change and the write are one step under the workspace's lock."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    name = batch_id(batch)
    where = applied(workspace, proj, name)
    if where:
        raise Refused(f"{name} was already {where}: it is never changed; a correction goes to a new batch, "
                      f"e.g. {next_name(workspace, proj, name)}")
    path = run_log_path(workspace, name)
    if not os.path.isfile(path):
        raise UsageError(f"{name} has no run log (runs/{os.path.basename(path)})")
    try:
        with workspace_lock(workspace):
            doc = read_log(path)
            at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict)
                       and str(x.get("tc") or "").strip() == case.strip()), None)
            if at is None:
                have = ", ".join(str(x.get("tc")) for x in doc["results"] if isinstance(x, dict)) or "none"
                raise UsageError(f"{case.strip()!r} is not in {name} (results: {have})")
            gone = doc["results"].pop(at)
            write_log(path, doc)
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None
    return path, gone


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
    p = sub.add_parser("remove", help="take one mistaken result out of a batch not yet applied")
    p.add_argument("batch")
    p.add_argument("case")
    a = ap.parse_args(argv)
    try:
        if a.command == "remove":
            path, gone = remove(a.workspace, a.batch, a.case)
            print(f"removed: {gone.get('tc')} {gone.get('status')} from {path}")
            print(json.dumps(gone, ensure_ascii=False, indent=1))
            files = [e for e in ([gone.get("evidence")] if isinstance(gone.get("evidence"), str)
                                 else gone.get("evidence") or []) if isinstance(e, str)]
            if files:
                print("evidence not deleted (delete it by hand if it is not needed): " + ", ".join(files))
            return 0
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
