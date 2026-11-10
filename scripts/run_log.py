"""
Writes the session's run log (methodology 7.1; plan 11.2), instead of a helper improvised in each session.

    python scripts/run_log.py <workspace> add <batch> <case> <status> <actual>
        [--bug ID] [--new-defect FILE.json] [--evidence PATH ...] [--environment KEY] [--build VERSION]
        [--executed-at "YYYY-MM-DDTHH:MM[:SS][Z|+HH:MM]" | unknown --executed-source "<where the time comes from>"
         [--ended-at "YYYY-MM-DDTHH:MM[:SS][Z|+HH:MM]"]]
    python scripts/run_log.py <workspace> retime <batch> <case> --executed-at "YYYY-MM-DDTHH:MM[:SS]" | unknown
        --executed-source "<where the time comes from>" [--ended-at "YYYY-MM-DDTHH:MM[:SS]"]
    python scripts/run_log.py <workspace> defect-rule
    python scripts/run_log.py <workspace> resolve <batch> <case> <status> --reason "<why>" --decided-by "<who>"
        [--decision-source "<where the decision is written>"] [--into <new batch>]
    python scripts/run_log.py <workspace> attach <batch> <case> [--bug ID | --new-defect FILE.json]
        [--evidence PATH ...] [--invalid PATH ... --reason "<why>"] [--replace-link]
    python scripts/run_log.py <workspace> subset <source-batch> <new-batch> --case TC-X [--case TC-Y ...] [--reason TEXT]
    python scripts/run_log.py <workspace> remove <batch> <case>
    python scripts/run_log.py <workspace> mode <batch> "<kind>: <why>"

- Subset (plan Q1, 10.11.2026): `subset` writes a NEW batch with only the named cases of an existing one, copied
  unchanged, with `derived_from` (original batch, its SHA-256, cases, reason, when). The original is never touched. A result
  has an identity (case, status, observed text, time of execution; adapters/excel/base.py `result_key`) and the apply
  refuses a result already applied from another batch, so the full batch and its partial batches (or two partial
  batches) can never apply the same result twice. The audit lists the overlap and the derivation.
- Proof (plan Q2, 10.11.2026): `add` takes `--observed` (seen on screen, in a log or in the evidence; `label: text`),
  `--claimed` (the assistant under test said it; not verified), `--inferred` (concluded, not seen) and `--requires-observed
  LABEL` (what the case needs to have been observed). The result keeps them in `proof`. The audit flags a Pass whose
  `proof` has nothing observed or lacks a required observation; a result without `proof` (all the earlier ones) is only
  counted, never judged or rewritten.
- Times. Every item has "ts", the time of the EXECUTION (methodology 7.1), and "recorded_at", the time the item was
  written. `add` without --executed-at records an execution that is happening now: ts = recorded_at = the clock. An
  observation made EARLIER (a tester's notes, a run the day before) is recorded with --executed-at and
  --executed-source (where the time comes from: "tester notes of 05.11"): ts is that time, recorded_at is now, and
  "executed_source" says where it comes from. A time that is not known is `--executed-at unknown` with a source: the
  item has NO ts (and "ts_unknown": true), is listed by the audit as without ts and ordered as the oldest; it is never
  made current by the clock. The time needs a date and a time (a bare date is refused); a time without a zone stays
  without one (as every ts already recorded: the machine's clock), a zone given (Z, +01:00) is kept as written; a time
  in the future or not valid is exit 2 and nothing is written. Old run logs (ts only) stay valid and are never filled in.

- Window of an execution (phase 17.1): `ts` is the START of the execution; `--ended-at` records when the case ended (it
  needs `--executed-at` with a known time, is not before it and not in the future). `retime` corrects only the time of a
  result in a batch not yet applied: observed text, status, evidence, build, bug and `recorded_at` stay; the previous time
  goes to `retimed` ({at, previous_ts, previous_source, previous_ended_at}, the keys that existed), one entry per
  correction; an end already recorded stays unless a new one is given and must still fit the corrected start; neither the
  time nor the end can come after `recorded_at`; the same time again changes nothing; an applied batch is refused (exit
  1), an unknown batch or case is exit 2.
- Evidence: `--evidence` takes one or more paths and may be repeated; every path is kept, in the order given, once.
  `attach --invalid PATH ... --reason TEXT` marks evidence the result already has as showing nothing useful
  (`evidence_invalid`: path, reason, when) without deleting it or taking it out of the list; the same mark again changes
  nothing, another reason for the same path is refused, a path the result does not have is exit 2.

- resolve: records the decision about a `Question` (methodology 8.5) without losing the observation. It is NOT a new
  execution: the item keeps its tc, observed text, ts, recorded_at and evidence, and gets the new status and a
  "resolution" {from_status, original {status, actual_result, ts, evidence}, reason, decided_by, decided_at, source}.
  In a batch not yet applied the item is changed in place (the original is kept inside "resolution"). A batch already
  applied is never touched: the decision goes to a NEW batch (--into, a free name is suggested), whose item carries the
  same observed text, ts, evidence and run block, "revision_of": {"batch", "tc"} and the "resolution"; that batch is
  then applied like any other (dry run, approval, --expect). --reason and --decided-by are required: the decision is
  the user's (or the project's), never the agent's; it does not replace an approval the project asks for. Refused (exit
  2/1, nothing written): a result that is not a Question, one already resolved, a status outside the vocabulary or
  Question, an unknown batch or case, a batch name that leaves the workspace. A later real execution of the case
  (a later ts) is current over the resolution: the resolution keeps the time of the observation it decides.

- Priority of a new bug. When the project's schema requires one (`defect-rule` says so and lists the accepted values;
  Excel: `excel.defects.priorities`), a new defect is recorded as approved only with a priority from that list, decided
  by the user: otherwise it is exit 2 and the bug stays a proposal ("proposed": true), which may wait for the decision.
  The audit lists the proposals still waiting for it. The adapter keeps its own final check.

- attach: adds evidence, an existing bug (--bug) or an approved new bug (--new-defect) to a result already in the
  batch, and keeps everything else of it: status, observed text, "ts" (the time of the execution, methodology 7.1),
  its evidence (the new paths are added after it) and the run block. The same link again changes nothing; another
  link than the one recorded is refused unless --replace-link (the previous one is printed). A case without a result
  in the batch is exit 2: its result is written first with add. Used by /manage-bugs after the review.

- mode: how the batch is executed and why (procedures/run-mobile.md step 2), the run log's top-level "mode", written
  before the first result: `mode` on its own (it creates the run log when there is none), or `add ... --mode`. The kind
  is one of conversational, flows, assisted, manual, and a reason follows the colon. The same value again changes
  nothing; another value is refused once the batch has results, and a batch with results and no mode never gets one
  afterwards (it is not guessed). Old run logs without "mode" stay valid.

- The case must be in the project's library (the workbook, or tracker/cases.json): an unknown ID is exit 2 with the
  closest known ones, and a library that cannot be read is exit 2 too. The run block does not change once the batch
  has results: another build or environment is exit 1 (the build changed during the batch: plan a new batch).
- remove: takes one mistaken result out of a batch not yet applied. It shows the result, keeps the others, never
  deletes evidence (it lists the files), and refuses a batch already applied (exit 1) or a result that is not there (2).

- add: appends the result of <case> to runs/batch_<batch>.json; a case already there is refused unless --replace is
  given, and then the previous result is printed (one item per case, the adapter refuses a case repeated); "ts" is now. <batch> may be given with or without the
  "batch_" prefix. --new-defect reads a JSON object (the bug approved by the user, or on a runner a proposal with
  "proposed": true, methodology 9.4); --environment/--build set the run block (the Jira and Azure DevOps adapters
  need it). The file is written whole and atomically.
- The status must be in the project's vocabulary (excel.cases.statuses, jira.results.statuses or
  azure_devops.results.outcomes) or be `Question`, in any case (stored as `Question`; reclassified before the
  checkpoint, methodology 8.5). A batch that has results without a build or environment cannot get one later.
- A batch already applied is never touched: its name is in the workbook's control sheet (the Excel adapter's
  applied_batches) or it has a record in tracker/sent/ (Jira, Azure DevOps). Appending to it made the adapter refuse
  it ("already applied ... with a DIFFERENT content"); a correction goes to a new batch, whose name is suggested.

Exit codes: 0 written · 1 refused (the batch was already applied, or another process is writing the workspace: nothing
was written, run the same command again) · 2 usage or configuration (unknown status, bad JSON, no project.yaml)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import output
from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    defect_priority_problem,
    defect_priority_rule,
    parse_when,
    read_project,
    same_build,
    ts_key,
    workspace_lock,
    workspace_path,
)

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# how a batch is executed (procedures/run-mobile.md step 2; technique-mobile.md 4.1): "<kind>: <why>"
MODE_KINDS = ("conversational", "flows", "assisted", "manual")


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


def check_mode(value: str) -> str:
    """The mode as written: "<kind>: <why>", the kind one of MODE_KINDS (lower case), a non-empty reason."""
    kind, sep, why = str(value or "").partition(":")
    kind, why = kind.strip().lower(), why.strip()
    if kind not in MODE_KINDS or not sep or not why:
        raise UsageError(f"mode {value!r}: expected \"<kind>: <why>\" with the kind one of {', '.join(MODE_KINDS)} "
                         "(e.g. \"conversational: generated answers\")")
    return f"{kind}: {why}"


def mode_kind(doc: dict) -> str | None:
    """The kind of a run log's mode (conversational, flows...), or None when it has none (an old run log)."""
    value = doc.get("mode") if isinstance(doc, dict) else None
    if not isinstance(value, str):
        return None
    kind = value.partition(":")[0].strip().lower()
    return kind if kind in MODE_KINDS else None


def _with_mode(doc: dict, mode: str) -> tuple[dict, bool]:
    """The doc with `mode` as its first key; (doc, changed). Refused when the batch has results and no mode or another
    mode: the mode is the record of how those results were obtained."""
    current = doc.get("mode")
    if current == mode:
        return doc, False
    if doc.get("results"):
        if current in (None, ""):
            raise Refused("this batch already has results recorded without a mode: it is not filled in afterwards. "
                          "Record the next results in a new batch with --mode")
        raise Refused(f"this batch's mode is {current!r} and it already has results: it does not change during the "
                      f"batch. A run in another mode ({mode!r}) is a new batch")
    return {"mode": mode, **{k: v for k, v in doc.items() if k != "mode"}}, True


def set_mode(workspace: str, batch: str, mode: str) -> tuple[str, str | None, bool]:
    """Writes the run log's mode before its first result: (path, previous mode, changed). Refused when the batch was
    applied, or has results (another mode, or none)."""
    mode = check_mode(mode)
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    name = batch_id(batch)
    path = run_log_path(workspace, name)
    try:
        with workspace_lock(workspace):
            where = applied(workspace, proj, name)
            if where:
                raise Refused(f"{name} was already {where}: it is never changed")
            doc = read_log(path)
            previous = doc.get("mode")
            doc, changed = _with_mode(doc, mode)
            if changed:
                write_log(path, doc)
            return path, previous, changed
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None


UNKNOWN_TIME = "unknown"


def check_defect(proj: dict, new_defect: dict | None) -> None:
    """An APPROVED new bug (no "proposed") needs the priority the project's schema asks for, decided by the user: the
    adapter would refuse it at the dry run, so it is refused here, before it counts as ready (methodology 9.2). A
    proposal ("proposed": true) may wait for it."""
    if not isinstance(new_defect, dict) or new_defect.get("proposed"):
        return
    problem = defect_priority_problem(proj, new_defect)
    if problem:
        raise UsageError(f"--new-defect: {problem}. Keep it as a proposal (\"proposed\": true) until the user decides")


def check_executed(executed_at: str | None, source: str | None, now: datetime | None = None):
    """(executed_at as stored or UNKNOWN_TIME or None, source or None). A time given needs a source: an observation
    made earlier says where its time comes from. A source alone, with no time, is a mistake."""
    source = " ".join(str(source or "").split()) or None
    if executed_at is None:
        if source:
            raise UsageError("--executed-source says where an execution time comes from: give --executed-at too")
        return None, None
    if not source:
        raise UsageError("--executed-at needs --executed-source: where the time comes from (the tester's notes, a "
                         "log, a message), so an observation made earlier stays identified")
    if str(executed_at).strip().casefold() == UNKNOWN_TIME:
        return UNKNOWN_TIME, source
    try:
        return parse_when(executed_at, now), source
    except ValueError as e:
        raise UsageError(f"--executed-at: {e}") from None


def check_ended(ended_at: str | None, executed_at: str | None, now: datetime | None = None) -> str | None:
    """The end of an execution's window as stored, or None. `ts` is the START, so an end needs a known start (an
    execution recorded as happening now has none to give) and is neither before it nor in the future."""
    if ended_at is None:
        return None
    if executed_at is None or executed_at == UNKNOWN_TIME:
        raise UsageError("--ended-at needs --executed-at with a known time: `ts` is the start of the execution and "
                         "the end is the end of its window")
    try:
        end = parse_when(ended_at, now)
    except ValueError as e:
        raise UsageError(f"--ended-at: {e}") from None
    if ts_key(end) < ts_key(executed_at):
        raise UsageError(f"--ended-at {end} is before the start {executed_at}")
    return end


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
        # every workbook the project declares, not only the batch's own: a batch applied anywhere is immutable,
        # whatever its name says now (a renamed pattern, an old override)
        from adapters.excel import ExcelAdapter
        from adapters.excel.base import workbook_files
        base = {k: v for k, v in (proj.get("excel") or {}).items() if k != "workbooks"}
        for file in workbook_files(proj):
            try:
                ad = ExcelAdapter({**base, "file": file}, base_dir=workspace)
                ad.load()
            except Exception as e:      # noqa: BLE001 - unreadable: say so, the other workbooks are still read
                print(f"warning: the workbook {file} could not be read to check whether {name} was applied ({e})",
                      file=sys.stderr)
                continue
            hit = next((b for b in ad.applied_batches() if b.get("batch_id") == name), None)
            if hit:
                return f"applied to the workbook {file} on {hit.get('applied_at')}"
        return None
    return None


def proposed_case_files(workspace: str, case: str) -> list[str]:
    """The cases files (cases/*.json, never the .dryrun review) of the workspace that propose `case`, as relative paths.
    A proposal is not in the library: its result may be recorded with --proposed and waits until the case is added."""
    out = []
    folder = os.path.join(workspace, "cases")
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or name.endswith(".dryrun.json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        cases = data.get("cases") if isinstance(data, dict) else None
        if isinstance(cases, list) and any(isinstance(c, dict) and str(c.get("id") or "").strip() == case for c in cases):
            out.append(f"cases/{name}")
    return out


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
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def add(workspace: str, batch: str, case: str, status: str, actual: str, bug: str | None = None,
        new_defect: dict | None = None, evidence: list[str] | None = None, environment: str | None = None,
        build: str | None = None, now: datetime | None = None, replace: bool = False,
        mode: str | None = None, executed_at: str | None = None,
        executed_source: str | None = None, ended_at: str | None = None,
        proof: dict | None = None, proposed: bool = False) -> tuple[str, dict | None]:
    """Appends the result of `case`, or replaces it with `replace`; (path, the result replaced or None). Refused /
    UsageError as in the header. `mode`: as `set_mode`, checked before the result is written. `ended_at`: the end of
    the execution's window (`ts` is its start)."""
    mode = check_mode(mode) if mode is not None else None
    proof = check_proof(proof)
    executed_at, executed_source = check_executed(executed_at, executed_source, now)
    ended_at = check_ended(ended_at, executed_at, now)
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    name = batch_id(batch)
    where = applied(workspace, proj, name)          # early, for a clear answer; checked again under the lock
    if where:
        raise Refused(f"{name} was already {where}: it is never changed; write the correction to a new batch, "
                      f"e.g. {next_name(workspace, proj, name)}, with only the cases to correct")
    statuses = vocabulary(proj)
    if not case.strip():
        raise UsageError("the case ID is empty")
    if status.strip().casefold() == "question":
        status = "Question"         # one spelling, the one the audit and the adapters look for
    elif statuses and status not in statuses:
        raise UsageError(f"status {status!r} is not in the project's vocabulary {statuses} (or Question)")
    if new_defect is not None and not isinstance(new_defect, dict):
        raise UsageError("--new-defect must hold a JSON object")
    check_defect(proj, new_defect)
    known, source = library_cases(workspace, proj)
    pending = None
    if case.strip() not in known:
        found = proposed_case_files(workspace, case.strip())
        if found and proposed:
            pending = found[0]
        else:
            close = difflib.get_close_matches(case.strip(), sorted(known), n=3, cutoff=0.6)
            raise UsageError(f"case {case.strip()!r} is not in the project's library ({source})"
                             + (f"; did you mean {', '.join(close)}?" if close else "")
                             + (f"; it IS proposed in {', '.join(found)}: record its result with --proposed (it stays "
                                "pending until the case is added to the workbook)" if found else "")
                             + ": nothing was written. A new case is added first (/plan-tests)")
    path = run_log_path(workspace, name)
    try:
        with workspace_lock(workspace):         # read, change and write the file as one step (audit finding D-02)
            where = applied(workspace, proj, name)      # under the lock: an apply cannot start in between
            if where:
                raise Refused(f"{name} was already {where}: it is never changed; write the correction to a new batch, "
                              f"e.g. {next_name(workspace, proj, name)}, with only the cases to correct")
            return _add(path, doc_for=path, case=case, status=status, actual=actual, bug=bug, new_defect=new_defect,
                        evidence=evidence, environment=environment, build=build, now=now, replace=replace, mode=mode,
                        executed_at=executed_at, executed_source=executed_source, ended_at=ended_at, proof=proof,
                        case_pending_add=pending)
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None


PROOF_KINDS = ("observed", "claimed", "inferred")


def check_proof(proof: dict | None) -> dict | None:
    """The proof block of a result (plan Q2, 10.11.2026): what was OBSERVED (seen on screen, in a log or in the
    evidence), what the assistant under test CLAIMED (what the assistant said; not verified) and what was INFERRED (concluded
    from the observed, not seen), plus `requires_observed`: the labels of what the case needs to have been observed to be
    a Pass. Each entry is a short text, an observed one optionally `label: text`. Returns None for no proof."""
    if not proof:
        return None
    out: dict = {}
    for kind in (*PROOF_KINDS, "requires_observed"):
        values = [str(v).strip() for v in (proof.get(kind) or []) if str(v).strip()]
        if values:
            out[kind] = list(dict.fromkeys(values))
    return out or None


def proof_gaps(item: dict) -> list[str]:
    """What a Pass lacks of its proof (empty = nothing lacks, or the result has no proof block: a result from before
    the block is never judged by it). Used by the audit."""
    proof = item.get("proof") if isinstance(item, dict) else None
    if not isinstance(proof, dict):
        return []
    gaps = []
    observed = [str(x) for x in proof.get("observed") or []]
    if not observed:
        kinds = [k for k in ("claimed", "inferred") if proof.get(k)]
        gaps.append("nothing observed" + (f" (only {' and '.join(kinds)})" if kinds else ""))
    for label in proof.get("requires_observed") or []:
        want = str(label).strip().casefold()
        if not any(o.strip().casefold() == want or o.strip().casefold().startswith(want + ":") for o in observed):
            gaps.append(f"required observation missing: {label}")
    return gaps


def _add(path: str, *, doc_for: str, case: str, status: str, actual: str, bug, new_defect, evidence, environment, build,
         now, replace: bool = False, mode: str | None = None, executed_at: str | None = None,
         executed_source: str | None = None, ended_at: str | None = None,
         proof: dict | None = None, case_pending_add: str | None = None) -> tuple[str, dict | None]:
    doc = read_log(doc_for)
    _same_context(doc, environment, build)
    if mode is not None:
        doc, _ = _with_mode(doc, mode)
    recorded = (now or datetime.now()).isoformat(timespec="seconds")
    item = {"tc": case.strip(), "status": status, "actual_result": actual}
    if executed_at is None:
        item["ts"] = recorded                       # executed now: the execution and its record are one instant
    elif executed_at == UNKNOWN_TIME:
        item["ts_unknown"] = True                   # no ts: a time nobody gave is never filled in
    else:
        item["ts"] = executed_at
    item["recorded_at"] = recorded
    if executed_source:
        item["executed_source"] = executed_source
    if ended_at:
        item["ended_at"] = ended_at
    if proof:
        item["proof"] = proof
    if case_pending_add:
        item["case_pending_add"] = case_pending_add      # the case is only PROPOSED (cases/<file>): not yet in the workbook
    if bug:
        item["bug"] = bug
    if new_defect:
        item["new_defect"] = new_defect
    evidence = list(dict.fromkeys(e for e in (evidence or []) if e))      # every path once, in the order given
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
    old = None
    if at is None:
        doc["results"].append(item)
    else:
        old = doc["results"][at]
        if not replace:
            raise Refused(f"{item['tc']} already has a result in this batch ({old.get('status')}, {old.get('ts')}): "
                          "add --replace to replace it (its evidence, bug and new defect are not carried over), "
                          "or record the new result in a new batch")
        doc["results"][at] = item
    write_log(path, doc)
    return path, old


def _same_context(doc: dict, environment: str | None, build: str | None) -> None:
    """The run block is the record of where the results came from: once the batch has results it does not change. A
    build that changes during a batch ends that batch (technique-mobile.md section 1.4)."""
    if not doc.get("results"):
        return
    run = doc.get("run") or {}
    for key, value in (("build", build), ("environment", environment)):
        if value and not run.get(key):
            raise Refused(f"this batch already has results recorded without a {key}: their {key} cannot be filled in "
                          f"afterwards. Record this result in a new batch with --{key}")
    if build and run.get("build") and not same_build(str(run["build"]), build):
        raise Refused(f"the build changed during the batch: it started on {run['build']!r} and this result says "
                      f"{build!r}. Stop this batch (its results stay as they are) and plan a new batch with the right "
                      "context (/plan-tests)")
    if environment and run.get("environment") and str(run["environment"]) != environment:
        raise Refused(f"the environment changed during the batch: it started on {run['environment']!r} and this result "
                      f"says {environment!r}. Plan a new batch (/plan-tests)")


def _paths(value) -> list[str]:
    """The evidence of an item as a list: one path or several."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [e for e in value if isinstance(e, str) and e]
    return []


def attach(workspace: str, batch: str, case: str, bug: str | None = None, new_defect: dict | None = None,
           evidence: list[str] | None = None, replace_link: bool = False, invalid: list[str] | None = None,
           reason: str | None = None, now: datetime | None = None) -> tuple[str, dict, dict]:
    """Adds evidence or a bug to a result already recorded, keeping the rest of it: (path, before, after); before ==
    after when nothing changed. `invalid` marks evidence paths of the result as showing nothing useful, with the
    `reason` (they stay in the list, never deleted). Refused / UsageError as in the header."""
    reason = " ".join(str(reason or "").split()) or None
    invalid = list(dict.fromkeys(p for p in (invalid or []) if p))
    if not (bug or new_defect is not None or evidence or invalid):
        raise UsageError("nothing to attach: give --bug, --new-defect, --evidence or --invalid")
    if invalid and not reason:
        raise UsageError("--invalid needs --reason: why the evidence shows nothing useful")
    if reason and not invalid:
        raise UsageError("--reason says why an evidence is invalid: give --invalid <path> too")
    if bug and new_defect is not None:
        raise UsageError("--bug (an existing bug) and --new-defect (a new one) exclude each other")
    if new_defect is not None and (not isinstance(new_defect, dict) or not new_defect):
        raise UsageError("--new-defect must hold a JSON object")
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    check_defect(proj, new_defect)
    name = batch_id(batch)
    where = applied(workspace, proj, name)
    if where:
        raise Refused(f"{name} was already {where}: it is never changed; write the correction to a new batch, "
                      f"e.g. {next_name(workspace, proj, name)}, with only the cases to correct")
    path = run_log_path(workspace, name)
    if not os.path.isfile(path):
        raise UsageError(f"{name} has no run log (runs/{os.path.basename(path)}): write the result first with add")
    try:
        with workspace_lock(workspace):
            where = applied(workspace, proj, name)
            if where:
                raise Refused(f"{name} was already {where}: it is never changed; write the correction to a new batch, "
                              f"e.g. {next_name(workspace, proj, name)}, with only the cases to correct")
            doc = read_log(path)
            at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict)
                       and str(x.get("tc") or "").strip() == case.strip()), None)
            if at is None:
                have = ", ".join(str(x.get("tc")) for x in doc["results"] if isinstance(x, dict)) or "none"
                raise UsageError(f"{case.strip()!r} has no result in {name} (results: {have}): write it first with add")
            before = doc["results"][at]
            item = dict(before)
            link = ("bug", bug) if bug else ("new_defect", new_defect) if new_defect is not None else None
            if link:
                key, value = link
                recorded = {k: before[k] for k in ("bug", "new_defect") if before.get(k) not in (None, "", {})}
                if recorded and recorded != {key: value} and not replace_link:
                    raise Refused(f"{item.get('tc')} is already linked to {json.dumps(recorded, ensure_ascii=False)}: "
                                  "add --replace-link to replace that link (the rest of the result is kept)")
                item.pop("bug" if key == "new_defect" else "new_defect", None)
                item[key] = value
            if evidence:
                # only the paths not recorded yet are added, after the recorded ones, which stay as they were
                # written (a list of one stays a list); naming a recorded path again changes nothing
                old = before.get("evidence")
                new = [e for e in dict.fromkeys(evidence) if e and e not in _paths(old)]
                if new:
                    if old in (None, "", []):
                        item["evidence"] = new[0] if len(new) == 1 else new
                    else:
                        item["evidence"] = (old if isinstance(old, list) else [old]) + new
            if invalid:
                have = _paths(item.get("evidence"))
                missing = [p for p in invalid if p not in have]
                if missing:
                    raise UsageError(f"{', '.join(missing)} is not in the evidence of {item.get('tc')} "
                                     f"({', '.join(have) or 'none'}): only a recorded path is marked")
                marked = {str(m.get("path")): m for m in before.get("evidence_invalid") or [] if isinstance(m, dict)}
                new_marks = []
                for p in invalid:
                    if p in marked:
                        if marked[p].get("reason") != reason:
                            raise Refused(f"{p} is already marked invalid ({marked[p].get('reason')!r}): another "
                                          "reason is not written over it")
                        continue
                    new_marks.append({"path": p, "reason": reason,
                                      "at": (now or datetime.now()).isoformat(timespec="seconds")})
                if new_marks:
                    item["evidence_invalid"] = list(before.get("evidence_invalid") or []) + new_marks
            if item != before:
                doc["results"][at] = item
                write_log(path, doc)
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None
    return path, before, item


def retime(workspace: str, batch: str, case: str, executed_at: str, executed_source: str | None,
           ended_at: str | None = None, now: datetime | None = None) -> tuple[str, dict, dict]:
    """Corrects only the time of a result in a batch not yet applied: (path, before, after); before == after when the
    time is already that. The observed text, status, evidence, build, bug and `recorded_at` stay; the previous time goes
    to `retimed`, one entry per correction, never replaced. `ts` is the start of the execution, `ended_at` the end of
    its window: one already recorded stays unless a new one is given, and must still fit the corrected start; neither
    can come after `recorded_at`. An unknown time removes `ts` (`ts_unknown`) and the end (kept in `retimed`). Refused
    / UsageError as in the header."""
    executed_at, executed_source = check_executed(executed_at, executed_source, now)
    if executed_at is None:
        raise UsageError("retime needs --executed-at: the corrected time, or `unknown`")
    ended_at = check_ended(ended_at, executed_at, now)
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
            where = applied(workspace, proj, name)
            if where:
                raise Refused(f"{name} was already {where}: it is never changed; a correction goes to a new batch, "
                              f"e.g. {next_name(workspace, proj, name)}")
            doc = read_log(path)
            at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict)
                       and str(x.get("tc") or "").strip() == case.strip()), None)
            if at is None:
                have = ", ".join(str(x.get("tc")) for x in doc["results"] if isinstance(x, dict)) or "none"
                raise UsageError(f"{case.strip()!r} has no result in {name} (results: {have})")
            before = doc["results"][at]
            item = dict(before)
            known = executed_at != UNKNOWN_TIME
            # the end already recorded stays unless a new one is given (only the time of the result changes); it must
            # still fit the corrected start. An unknown start cannot keep an end: it goes to the history below.
            kept_end = before.get("ended_at") if known and not ended_at else None
            end = ended_at or kept_end
            if kept_end and ts_key(kept_end) < ts_key(executed_at):
                raise UsageError(f"the end already recorded ({kept_end}) is before the corrected start "
                                 f"({executed_at}): give --ended-at with the corrected end")
            recorded = before.get("recorded_at")
            if recorded:        # an execution, and its end, cannot come after the moment the result was recorded
                for label, value in (("--executed-at", executed_at if known else None), ("the end", end)):
                    if value and ts_key(value) > ts_key(recorded):
                        raise UsageError(f"{label} {value} is after the time the result was recorded ({recorded})")
            if known:
                item["ts"] = executed_at
                item.pop("ts_unknown", None)
            else:
                item.pop("ts", None)
                item["ts_unknown"] = True
            item["executed_source"] = executed_source
            if end:
                item["ended_at"] = end
            else:
                item.pop("ended_at", None)
            same = all(item.get(k) == before.get(k) for k in ("ts", "ts_unknown", "executed_source", "ended_at"))
            if not same:
                entry = {"at": (now or datetime.now()).isoformat(timespec="seconds")}
                previous = before.get("ts") or (UNKNOWN_TIME if before.get("ts_unknown") else None)
                if previous:
                    entry["previous_ts"] = previous
                if before.get("executed_source"):
                    entry["previous_source"] = before["executed_source"]
                if before.get("ended_at"):
                    entry["previous_ended_at"] = before["ended_at"]
                item["retimed"] = list(before.get("retimed") or []) + [entry]
                doc["results"][at] = item
                write_log(path, doc)
            else:
                item = before
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None
    return path, before, item


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


def subset(workspace: str, source: str, target: str, cases: list[str], reason: str | None = None,
           now: datetime | None = None) -> tuple[str, list[dict]]:
    """A NEW batch with only the named cases of an existing one (plan Q1, 10.11.2026): (path, the results copied).
    The results are copied UNCHANGED (same case, status, observed text, time, evidence, bug), so a result keeps its
    identity (`result_key`) and can be applied only once, from the original or from any partial batch. The original is
    never touched; the new file records where it comes from (`derived_from`: batch, the SHA-256 of the original file,
    the cases, the reason, when). Refused: an unknown case, a target that exists or was applied, the source itself."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    src, dst = batch_id(source), batch_id(target)
    if src == dst:
        raise UsageError("the new batch needs another name than the original")
    wanted = list(dict.fromkeys(c.strip() for c in cases if c and c.strip()))
    if not wanted:
        raise UsageError("name the cases to copy (--case TC-X-001, repeated)")
    src_path, dst_path = run_log_path(workspace, src), run_log_path(workspace, dst)
    if not os.path.isfile(src_path):
        raise UsageError(f"{src} has no run log (runs/{os.path.basename(src_path)})")
    where = applied(workspace, proj, dst)
    if os.path.exists(dst_path) or where:
        raise Refused(f"{dst} already exists" + (f" and was {where}" if where else "")
                      + f": choose another name, e.g. {next_name(workspace, proj, dst)}")
    try:
        with workspace_lock(workspace):
            doc = read_log(src_path)
            by_case: dict = {}
            for item in doc["results"]:
                if isinstance(item, dict):
                    by_case.setdefault(str(item.get("tc") or "").strip(), []).append(item)
            unknown = [c for c in wanted if c not in by_case]
            if unknown:
                raise UsageError(f"not in {src}: {', '.join(unknown)} (results: {', '.join(sorted(by_case)) or 'none'})")
            copied = [json.loads(json.dumps(i)) for c in wanted for i in by_case[c]]
            with open(src_path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            new_doc = {k: json.loads(json.dumps(v)) for k, v in doc.items() if k not in ("results", "derived_from")}
            new_doc["derived_from"] = {"batch": src, "file_sha256": digest, "cases": wanted,
                                       "reason": reason or "", "created_at": (now or datetime.now()).isoformat(timespec="seconds")}
            new_doc["results"] = copied
            write_log(dst_path, new_doc)
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None
    return dst_path, copied


def _is_question(status) -> bool:
    return str(status or "").strip().casefold().startswith("question")


def _revisions_of(workspace: str, name: str, case: str) -> list[str]:
    """The batches (other run logs of the workspace) that already hold a revision of this result."""
    import glob
    found = []
    for path in sorted(glob.glob(os.path.join(workspace, "runs", "*.json"))):
        other = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8-sig") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        for x in doc.get("results", []) if isinstance(doc, dict) and isinstance(doc.get("results"), list) else []:
            link = x.get("revision_of") if isinstance(x, dict) else None
            if isinstance(link, dict) and link.get("batch") == name and str(link.get("tc") or "").strip() == case:
                found.append(other)
    return found


def _same_place(proj: dict, name: str, run, target: str, target_run: dict) -> None:
    """The decision must be read where the Question was: the same platform (the reports take it from the run block or
    from the batch name, `report.platforms`) and, with Excel, the same workbook. Otherwise a resolution of an iOS Question
    could be counted on Android or applied to the other workbook."""
    from qa_report import platform_of
    here = platform_of({"name": name, "run": run if isinstance(run, dict) else {}}, proj)
    there = platform_of({"name": target, "run": target_run}, proj)
    if here != there:
        raise UsageError(f"--into {target} would be read as platform {there!r}, but the Question is {here!r}: name the "
                         f"batch so that report.platforms gives {here!r} (e.g. keep the prefix of {name})")
    if proj.get("tracker") == "excel":
        from adapters.excel.base import route_workbook
        a = route_workbook(proj, name, run if isinstance(run, dict) else None, environ={})
        b = route_workbook(proj, target, target_run or None, environ={})
        if a.problem or b.problem or a.file != b.file:
            raise UsageError(f"--into {target} would go to the workbook {b.file or b.problem!r}, but the Question's batch "
                             f"goes to {a.file or a.problem!r}: name it so that both route to the same workbook")


def resolve(workspace: str, batch: str, case: str, status: str, reason: str, decided_by: str,
            decision_source: str | None = None, into: str | None = None,
            now: datetime | None = None) -> tuple[str, str, dict, dict]:
    """Records the decision about a Question: (path written, "in place" | "new batch", the Question as it was, the item
    now). The observation (text, ts, evidence) is kept; see the header. Refused / UsageError as in the header."""
    reason, decided_by = " ".join(str(reason or "").split()), " ".join(str(decided_by or "").split())
    decision_source = " ".join(str(decision_source or "").split()) or None
    if not reason or not decided_by:
        raise UsageError("a resolution needs --reason (why this status) and --decided-by (who decided): the decision "
                         "is the user's, it is not made here")
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    statuses = vocabulary(proj)
    if _is_question(status):
        raise UsageError("a resolution gives the Question's final status, not Question again: an unanswered "
                         "Question stays as it is")
    if statuses and status not in statuses:
        raise UsageError(f"status {status!r} is not in the project's vocabulary {statuses}")
    name = batch_id(batch)
    src_path = run_log_path(workspace, name)
    if not os.path.isfile(src_path):
        raise UsageError(f"{name} has no run log (runs/{os.path.basename(src_path)})")
    target = batch_id(into) if into else None
    if target == name:
        raise UsageError("--into is another batch than the one that holds the Question")
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    try:
        with workspace_lock(workspace):
            where = applied(workspace, proj, name)
            if where and not target:
                raise Refused(f"{name} was already {where}: it is never changed. The decision goes to a new batch "
                              f"with --into, e.g. {next_name(workspace, proj, name)} (applied afterwards with its own "
                              "dry run and approval)")
            doc = read_log(src_path)
            at = next((i for i, x in enumerate(doc["results"]) if isinstance(x, dict)
                       and str(x.get("tc") or "").strip() == case.strip()), None)
            if at is None:
                have = ", ".join(str(x.get("tc")) for x in doc["results"] if isinstance(x, dict)) or "none"
                raise UsageError(f"{case.strip()!r} has no result in {name} (results: {have})")
            before = doc["results"][at]
            if not _is_question(before.get("status")):
                raise UsageError(f"{case.strip()!r} in {name} is {before.get('status')!r}, not a Question: only a "
                                 "Question is resolved (a mistaken result is removed, a new execution is added)")
            if before.get("resolution") or before.get("revision_of"):
                raise Refused(f"{case.strip()!r} in {name} is itself a resolution or a revision: it is not resolved again")
            held = _revisions_of(workspace, name, case.strip())
            if held:
                raise Refused(f"the Question of {case.strip()} in {name} was already resolved in {', '.join(held)}")
            original = {"status": before.get("status"), "actual_result": before.get("actual_result")}
            for key in ("ts", "ts_unknown", "recorded_at", "evidence", "executed_source"):
                if before.get(key) not in (None, "", []):
                    original[key] = before[key]
            resolution = {"from_status": "Question", "original": original, "reason": reason,
                          "decided_by": decided_by, "decided_at": stamp}
            if decision_source:
                resolution["source"] = decision_source
            if target and not where:
                raise UsageError(f"{name} is not applied: its Question is resolved in place (leave out --into). --into "
                                 "is for a batch already applied, which is never touched")
            if not target:
                item = {**before, "status": status, "resolution": resolution}
                doc["results"][at] = item
                write_log(src_path, doc)
                return src_path, "in place", before, item
            if applied(workspace, proj, target):
                raise Refused(f"{target} was already {applied(workspace, proj, target)}: use another batch name, e.g. "
                              f"{next_name(workspace, proj, target)}")
            dst = run_log_path(workspace, target)
            new = read_log(dst)
            if any(isinstance(x, dict) and str(x.get("tc") or "").strip() == case.strip() for x in new["results"]):
                raise Refused(f"{target} already has a result for {case.strip()}: one item per case in a batch")
            source_run = {k: v for k, v in (doc.get("run") or {}).items() if k in ("environment", "build", "platform")}
            _same_place(proj, name, doc.get("run"), target, source_run)
            _same_context(new, source_run.get("environment"), source_run.get("build"))
            item = {k: before[k] for k in ("tc", "actual_result", "ts", "ts_unknown", "evidence", "executed_source")
                    if before.get(k) not in (None, "", [])}
            item = {"tc": item["tc"], "status": status, **{k: v for k, v in item.items() if k != "tc"},
                    "recorded_at": stamp, "revision_of": {"batch": name, "tc": case.strip()}, "resolution": resolution}
            if not new["results"]:
                new = {"mode": f"manual: resolution of a Question of {name} (a decision, not an execution)",
                       **({"run": source_run} if source_run else {}), "results": []}
            new["results"].append(item)
            write_log(dst, new)
            return dst, "new batch", before, item
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None


def _platform_of(proj: dict, name: str, doc: dict | None = None) -> str | None:
    """The platform of a batch: the run block's, else the project's `report.platforms` patterns (never guessed)."""
    import fnmatch
    declared = ((doc or {}).get("run") or {}).get("platform")
    if declared:
        return str(declared)
    for platform, patterns in ((proj.get("report") or {}).get("platforms") or {}).items():
        for pattern in [patterns] if isinstance(patterns, str) else patterns or []:
            if fnmatch.fnmatch(name, str(pattern)):
                return str(platform)
    return None


def _plan_cases(workspace: str, plan: str | None) -> list[str] | None:
    """The cases of a batch plan (`batches/<name>.yaml`, key `cases`), in order; None without a readable plan."""
    if not plan:
        return None
    try:
        import yaml
        with open(workspace_path(workspace, *str(plan).replace("\\", "/").split("/")), encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, ValueError, ImportError, OutsideWorkspace):
        return None
    cases = data.get("cases") if isinstance(data, dict) else None
    return [str(c).strip() for c in cases] if isinstance(cases, list) else None


def set_plan(workspace: str, batch: str, plan: str) -> tuple[str, str | None, bool]:
    """Records which batch plan (`batches/<name>.yaml`) the run log follows, so a result can say what comes next:
    (path, previous plan, changed). Every case of the plan must be in the library; refused when the batch was applied."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    rel = os.path.relpath(os.path.join(workspace, plan), workspace).replace(os.sep, "/")
    cases = _plan_cases(workspace, rel)
    if not cases:
        raise UsageError(f"{plan}: not found inside the workspace, or it has no `cases:` list (a batches/<name>.yaml)")
    known, source = library_cases(workspace, proj)
    unknown = [c for c in cases if c not in known]
    if unknown:
        raise UsageError(f"{plan}: {', '.join(unknown)} not in the project's library ({source}): nothing was written")
    name = batch_id(batch)
    path = run_log_path(workspace, name)
    try:
        with workspace_lock(workspace):
            where = applied(workspace, proj, name)
            if where:
                raise Refused(f"{name} was already {where}: it is never changed")
            doc = read_log(path)
            previous = doc.get("plan")
            if previous == rel:
                return path, previous, False
            doc = {**{k: v for k, v in doc.items() if k != "results"}, "plan": rel, "results": doc["results"]}
            write_log(path, doc)
            return path, previous, True
    except WorkspaceBusy as e:
        raise Refused(str(e)) from None


def progress(workspace: str, batch: str, plan: str | None = None) -> dict:
    """Where a batch stands, read only: the platform, the build, each planned case with its result or what is next, and
    results outside the plan. The plan is the one given, else the one recorded by `plan`."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    name = batch_id(batch)
    path = run_log_path(workspace, name)
    if not os.path.isfile(path):
        raise UsageError(f"{name} has no run log (runs/{os.path.basename(path)})")
    doc = read_log(path)
    planned = _plan_cases(workspace, plan or doc.get("plan"))
    done = {str(x.get("tc")).strip(): x for x in doc["results"] if isinstance(x, dict)}
    order = planned if planned is not None else list(done)
    todo = [c for c in order if c not in done]
    return {"batch": name, "platform": _platform_of(proj, name, doc), "build": (doc.get("run") or {}).get("build"),
            "plan": plan or doc.get("plan"), "order": order, "results": done, "next": todo[0] if todo else None,
            "pending": todo, "outside_plan": [c for c in done if planned is not None and c not in planned],
            "mode": doc.get("mode")}


def _pass_word(proj: dict) -> str | None:
    try:
        return project_status(proj, "pass")
    except UsageError:
        return None


def print_progress(info: dict, proj: dict, cfg: dict) -> None:
    """A short card of the progress: one line per case (a result, or what is next), the counts, and the next step. A
    result that is not a pass (a fail, a block, a Question) is a must line: it never falls out of the limit."""
    import output
    ok = _pass_word(proj)
    head = f"PROGRESS {info['batch']}" + (f" · {info['platform']}" if info["platform"] else "") \
        + (f" · build {info['build']}" if info["build"] else "")
    lines: list[tuple[str, bool]] = [(head, True)]
    for case in info["order"]:
        got = info["results"].get(case)
        if got:
            at = str(got.get("ts") or "")[11:16] or "no ts"
            lines.append((f"  ✓ {case}  {got.get('status')} · {at}", got.get("status") != ok))
        elif case == info["next"]:
            lines.append((f"  ▶ {case}  next", True))
        else:
            lines.append((f"  · {case}", False))
    lines += [(f"  + {c}  {info['results'][c].get('status')} (not in the plan)", True) for c in info["outside_plan"]]
    counts: dict = {}
    for got in info["results"].values():
        counts[str(got.get("status"))] = counts.get(str(got.get("status")), 0) + 1
    lines.append(("Results: " + (" · ".join(f"{n} {s}" for s, n in counts.items()) or "none yet")
                  + (f" · {len(info['pending'])} to run" if info["plan"] else ""), True))
    if info["next"]:
        lines.append((f"Next: {info['next']}" + (f" (plan {info['plan']})" if info["plan"] else ""), True))
    elif info["plan"]:
        lines.append(("Next: every planned case has a result: /manage-bugs for the Fails, then /checkpoint", True))
    else:
        lines.append(("Next: no plan recorded: run_log.py <workspace> plan <batch> batches/<name>.yaml", True))
    cols = cfg["max_cols"] if output.is_brief(cfg) else None
    for text in output.fit(lines, max(cfg["max_lines"], 12) if output.is_brief(cfg) else 10**6, "--detail", cols):
        print(text)


def _project_or_empty(workspace: str) -> dict:
    try:
        return read_project(workspace)
    except Exception:       # noqa: BLE001 - a missing project.yaml is the command's own error, said where it matters
        return {}


def _brief_added(workspace: str, batch: str, case: str, status: str, path: str, replaced, evidence) -> str:
    """One line for a recorded result: the platform, the case, the result, the evidence count, the file (relative) and,
    when the batch has a plan, what comes next."""
    try:
        info = progress(workspace, batch)
    except UsageError:
        info = {"platform": None, "next": None, "plan": None}
    kept = len({e for e in (evidence or []) if e})
    parts = [f"{'replaced' if replaced else 'added'}: " + (f"[{info['platform']}] " if info["platform"] else "")
             + f"{case} {status}"]
    if kept:
        parts.append(f"{kept} evidence path(s)")
    parts.append(os.path.relpath(path, workspace))
    if info["next"]:
        parts.append(f"next: {info['next']}")
    elif info["plan"]:
        parts.append("every planned case has a result")
    return " · ".join(parts)


def _read_defect(path: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise UsageError(f"--new-defect {path}: {e}") from None


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
    p.add_argument("--evidence", nargs="+", action="extend", metavar="PATH",
                   help="paths under evidence/: several after one option, or the option repeated (every path is kept)")
    p.add_argument("--environment", help="run.environment (a key of environments)")
    p.add_argument("--build", help="run.build (the version tested)")
    p.add_argument("--replace", action="store_true",
                   help="replace the case's result already in this batch (shown); without it a second result is refused")
    p.add_argument("--mode", help='how the batch runs, "<kind>: <why>" (kind: ' + ", ".join(MODE_KINDS)
                   + "); only before the first result, or the same value again")
    p.add_argument("--executed-at", metavar="WHEN",
                   help="when the case was EXECUTED, if not now: YYYY-MM-DDTHH:MM[:SS] (optionally Z or +HH:MM; a time "
                        "without zone stays without one), or 'unknown'. Needs --executed-source. Not in the future")
    p.add_argument("--executed-source", metavar="TEXT",
                   help="where --executed-at comes from (e.g. 'tester notes of 05.11')")
    p.add_argument("--ended-at", metavar="WHEN",
                   help="when the case ENDED (the end of its window; `ts` is the start): needs --executed-at with a "
                        "known time; not before it, not in the future")
    p.add_argument("--observed", action="append", metavar="TEXT",
                   help="what was OBSERVED (seen on screen, in a log or in the evidence); `label: text` names the "
                        "observation; repeat the option")
    p.add_argument("--claimed", action="append", metavar="TEXT",
                   help="what the assistant under test CLAIMED (what the assistant said; not verified); repeat the option")
    p.add_argument("--inferred", action="append", metavar="TEXT",
                   help="what was INFERRED from the observed (not seen); repeat the option")
    p.add_argument("--requires-observed", action="append", metavar="LABEL",
                   help="a label that must appear as an observation for a Pass to be sound (audit flags a Pass without it)")
    p.add_argument("--proposed", action="store_true",
                   help="the case is only PROPOSED in cases/*.json (not yet in the workbook): record its result, marked "
                        "case_pending_add; the apply refuses it until the case is added")
    output.add_flags(p)
    p = sub.add_parser("retime", help="correct only the time of a result in a batch not yet applied")
    p.add_argument("batch")
    p.add_argument("case")
    p.add_argument("--executed-at", metavar="WHEN", required=True,
                   help="the corrected start of the execution, or 'unknown' (YYYY-MM-DDTHH:MM[:SS][Z|+HH:MM])")
    p.add_argument("--executed-source", metavar="TEXT", help="where the time comes from (required)")
    p.add_argument("--ended-at", metavar="WHEN", help="the end of the execution's window (optional)")
    p = sub.add_parser("plan", help="record which batch plan (batches/<name>.yaml) the run log follows")
    p.add_argument("batch")
    p.add_argument("file", help="the plan, inside the workspace: a YAML with `cases: [...]` in the planned order")
    p = sub.add_parser("progress", help="where the batch stands: each case, the counts and the next step (read only)")
    p.add_argument("batch")
    p.add_argument("--plan", metavar="FILE", help="the plan to follow (default: the one recorded by `plan`)")
    output.add_flags(p)
    p = sub.add_parser("defect-rule", help="what the project's schema says about the priority of a new bug")
    p = sub.add_parser("resolve", help="record the decision about a Question, keeping the observation")
    p.add_argument("batch")
    p.add_argument("case")
    p.add_argument("status", help="the final status (the project's vocabulary; not Question)")
    p.add_argument("--reason", required=True, help="why this status (the decision, in the decider's words)")
    p.add_argument("--decided-by", required=True, help="who decided (the user, the PO...)")
    p.add_argument("--decision-source", help="where the decision is written (a message, a ticket comment, a meeting)")
    p.add_argument("--into", metavar="BATCH", help="record it in this NEW batch (required when the batch was applied)")
    p = sub.add_parser("mode", help="write the run log's mode before its first result")
    p.add_argument("batch")
    p.add_argument("value", help='"<kind>: <why>", kind one of ' + ", ".join(MODE_KINDS))
    p = sub.add_parser("subset", help="a NEW batch with only the named cases of a batch, copied unchanged")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--case", action="append", required=True, metavar="TC", help="a case to copy (repeat the option)")
    p.add_argument("--reason", help="why only these cases (kept in the new batch, `derived_from`)")
    p = sub.add_parser("remove", help="take one mistaken result out of a batch not yet applied")
    p.add_argument("batch")
    p.add_argument("case")
    p = sub.add_parser("attach", help="add evidence or a bug to a result already recorded, keeping the rest of it")
    p.add_argument("batch")
    p.add_argument("case")
    p.add_argument("--bug", help="an EXISTING bug linked to the case")
    p.add_argument("--new-defect", metavar="FILE", help="JSON object of a new bug (approved; or \"proposed\": true)")
    p.add_argument("--evidence", nargs="+", action="extend", metavar="PATH",
                   help="paths under evidence/, added after the ones recorded (several after one option, or the "
                        "option repeated)")
    p.add_argument("--invalid", nargs="+", action="extend", metavar="PATH",
                   help="paths of the result's evidence that show nothing useful: marked, never deleted; needs --reason")
    p.add_argument("--reason", help="why the --invalid evidence shows nothing useful")
    p.add_argument("--replace-link", action="store_true",
                   help="replace a bug or new defect already linked to the result (the previous one is printed)")
    a = ap.parse_args(argv)
    cfg = output.settings(_project_or_empty(a.workspace), output.cli_choice(a))
    try:
        if a.command == "retime":
            path, before, after = retime(a.workspace, a.batch, a.case, a.executed_at, a.executed_source, a.ended_at)

            def window(x):
                return (f"ts={x.get('ts') or 'unknown'} source={x.get('executed_source') or '-'} "
                        f"ended_at={x.get('ended_at') or '-'}")

            if before == after:
                print(f"unchanged: {a.case} in {path} already has that time ({window(after)})")
                return 0
            print(f"retimed: {a.case} ({after.get('status')}) in {path}; observed text, status, evidence, build and "
                  "recorded_at kept")
            print(f"before: {window(before)}")
            print(f"after:  {window(after)}")
            return 0
        if a.command == "plan":
            path, previous, changed = set_plan(a.workspace, a.batch, a.file)
            print(f"plan: {a.file} in {os.path.relpath(path, a.workspace)}" if changed
                  else f"unchanged: the plan of {os.path.relpath(path, a.workspace)} is already {previous}")
            return 0
        if a.command == "progress":
            info = progress(a.workspace, a.batch, a.plan)
            print_progress(info, read_project(a.workspace), cfg)
            return 0
        if a.command == "attach":
            new = _read_defect(a.new_defect) if a.new_defect else None
            path, before, after = attach(a.workspace, a.batch, a.case, a.bug, new, a.evidence, a.replace_link,
                                         invalid=a.invalid, reason=a.reason)
            if before == after:
                print(f"unchanged: {a.case} in {path} already has it")
                return 0
            print(f"attached to {a.case} ({after.get('status')}, {after.get('ts')}) in {path}; status, observed "
                  "text, time, evidence and run block kept")
            gone = {k: before[k] for k in ("bug", "new_defect") if k in before and before.get(k) != after.get(k)}
            if gone:
                print("previous link (kept only in this output and in git history): "
                      + json.dumps(gone, ensure_ascii=False))
            return 0
        if a.command == "defect-rule":
            try:
                rule = defect_priority_rule(read_project(a.workspace))
            except FileNotFoundError as e:
                raise UsageError(str(e)) from None
            print(f"priority of a new bug: {'REQUIRED' if rule['required'] else 'optional'}; accepted values "
                  f"({rule['where']}): {', '.join(rule['accepted']) or 'none listed'}")
            print("the user decides it: ask at the bug review; a suggestion is only a proposal for approval")
            return 0
        if a.command == "resolve":
            path, how, before, _item = resolve(a.workspace, a.batch, a.case, a.status, a.reason, a.decided_by,
                                              a.decision_source, a.into)
            print(f"resolved ({how}): {a.case} Question -> {a.status} in {path}; observation, ts and evidence kept")
            print("the resolution is a record of a decision: a workbook or tracker change still needs its dry run "
                  "and approval (/checkpoint)")
            return 0
        if a.command == "mode":
            path, previous, changed = set_mode(a.workspace, a.batch, a.value)
            if not changed:
                print(f"unchanged: the mode of {path} is already {previous!r}")
            elif previous:
                print(f"mode changed before any result: {previous!r} -> {check_mode(a.value)!r} in {path}")
            else:
                print(f"mode: {check_mode(a.value)!r} in {path}")
            return 0
        if a.command == "subset":
            path, copied = subset(a.workspace, a.source, a.target, a.case, a.reason)
            print(f"subset: {len(copied)} result(s) of {a.source} copied unchanged to {path}")
            print("the original was not touched; each result keeps its identity: the same result is applied only once, "
                  "from whichever batch")
            return 0
        if a.command == "remove":
            path, gone = remove(a.workspace, a.batch, a.case)
            print(f"removed: {gone.get('tc')} {gone.get('status')} from {path}")
            print(json.dumps(gone, ensure_ascii=False, indent=1))
            files = [e for e in ([gone.get("evidence")] if isinstance(gone.get("evidence"), str)
                                 else gone.get("evidence") or []) if isinstance(e, str)]
            if files:
                print("evidence not deleted (delete it by hand if it is not needed): " + ", ".join(files))
            return 0
        new = _read_defect(a.new_defect) if a.new_defect else None
        path, replaced = add(a.workspace, a.batch, a.case, a.status, a.actual, a.bug, new, a.evidence,
                             a.environment, a.build, replace=a.replace, mode=a.mode,
                             executed_at=a.executed_at, executed_source=a.executed_source, ended_at=a.ended_at,
                             proof={"observed": a.observed, "claimed": a.claimed, "inferred": a.inferred,
                                    "requires_observed": a.requires_observed}, proposed=a.proposed)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if output.is_brief(cfg):
        print(_brief_added(a.workspace, a.batch, a.case, a.status, path, replaced, a.evidence))
    else:
        kept = len(dict.fromkeys(e for e in (a.evidence or []) if e))
        print(f"{'replaced' if replaced else 'added'}: {a.case} {a.status} in {path}"
              + (f"; {kept} evidence path(s) kept" if kept else ""))
    if replaced:
        print("previous result (kept only in this output and in git history):")
        print(json.dumps(replaced, ensure_ascii=False, indent=1))
        files = [e for e in ([replaced.get("evidence")] if isinstance(replaced.get("evidence"), str)
                             else replaced.get("evidence") or []) if isinstance(e, str)]
        if files:
            print("its evidence was not deleted: " + ", ".join(files))
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
