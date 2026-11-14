"""
Bugs in quarantine: the workspace's register and what the skills read from it (procedures/quarantine.md).

    python scripts/quarantine.py <workspace> show [--json]
    python scripts/quarantine.py <workspace> propose <id> --bug ID --case CASE ... [--platform P ...] [--build B ...]
                                                    [--environment E ...] --reason TEXT --exit TEXT [--bug-url URL]
                                                    [--tracker-bug ID]

--bug is the id the run logs link with --bug (the workbook's defect, e.g. BUG-7, in an Excel project; the tracker's key
otherwise); --tracker-bug the same bug's id in the ticket source when they differ (it gives the link).
    python scripts/quarantine.py <workspace> decide <id> activate|lift|reject --decided-by WHO --ref WHERE
    python scripts/quarantine.py <workspace> scope --platform P [--build B] [--environment E] [--plan FILE] [--json]
    python scripts/quarantine.py <workspace> report [--batch NAME ...] [--json]

The register is <workspace>/memory/quarantine.json, written whole and atomically under the workspace's lock, and only by
`propose` and `decide`. A quarantine covers ONE bug, the cases it affects and the platforms, builds and environments where
it holds (each list empty = any): never a whole bug, never another platform. It is never automatic: `propose` writes a
proposal (the assistant may); `decide` records the QA's explicit decision (`activate` a proposal, `reject` it, `lift` an
active one) with who decided and where (both required). An active or lifted id is never reused.

- scope: the active quarantines that hold for a platform, build and environment, and the cases they cover; with --plan,
  the check of a batch plan (`batches/<name>.yaml`: `cases`, `excluded: [{case, quarantine}]`, `retest: [{case,
  quarantine}]`): a covered case in `cases` without `retest`, or an `excluded` case that no active quarantine of that
  context covers, is a problem (exit 1). Read only.
- report: per run log, the cases left out by quarantine (from its plan; no result is written for them), the known failures
  observed (a Fail linked to the quarantine's bug), and the retests run (a pass: the QA decides whether the quarantine
  ends; a Fail without that link: a different failure, for the normal review; another status: not concluded). Read only.
  The plan recorded in the run log is the decision of its time: its `excluded:` and `retest:` stay shown with the
  quarantine they name after it is lifted. qa_report.py report puts the same rows in its reports (report_logs, with its
  own scope: batches, --since, --platform), and leaves the section out when the scope has none.

Exit codes: 0 done · 1 refused (nothing written) / scope: the plan has problems · 2 usage, or a register that cannot be
read · 5 another process is writing the workspace (nothing was written)
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    read_project,
    same_build,
    workspace_lock,
    workspace_path,
)

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
ACTIONS = {"activate": ("proposed", "active"), "reject": ("proposed", "rejected"), "lift": ("active", "lifted")}


class UsageError(Exception):
    """Usage, or a register that cannot be read (code 2)."""


class Refused(Exception):
    """A change the register refuses (code 1): nothing was written."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def path_of(workspace: str) -> str:
    return workspace_path(workspace, "memory", "quarantine.json")


def load(workspace: str) -> dict:
    """The register; {"entries": {}} when the workspace has none (a workspace without quarantines)."""
    path = path_of(workspace)
    if not os.path.isfile(path):
        return {"entries": {}}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise UsageError(f"{path} cannot be read ({e}): fix it by hand; nothing was changed") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), dict):
        raise UsageError(f"{path} is not a register of quarantines ({{\"entries\": {{...}}}})")
    return doc


def save(workspace: str, doc: dict) -> None:
    path = path_of(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc["updated"] = now()
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1))


def _list(values) -> list[str]:
    out = []
    for v in values or []:
        for x in str(v).split(","):
            x = x.strip()
            if x and x not in out:
                out.append(x)
    return out


def bug_url(proj: dict, bug: str) -> str | None:
    """The link of a bug from the project's only Azure DevOps or Jira ticket source (never guessed between two)."""
    sources = [s for s in (((proj.get("tickets") or {}).get("sources")) or {}).values() if isinstance(s, dict)]
    ado = [s for s in sources if s.get("service") == "azure-devops" and s.get("organization_url") and s.get("project")]
    jira = [s for s in sources if s.get("service") == "jira" and (s.get("base_url") or s.get("site"))]
    if len(ado) == 1 and not jira:
        s = ado[0]
        return f"{str(s['organization_url']).rstrip('/')}/{urllib.parse.quote(str(s['project']))}/_workitems/edit/{bug}"
    if len(jira) == 1 and not ado:
        return f"{str(jira[0].get('base_url') or jira[0].get('site')).rstrip('/')}/browse/{bug}"
    return None


def _library(workspace: str, proj: dict) -> set[str]:
    """The case library: with Excel, every workbook the project declares (a case may live only in a platform's own
    workbook); otherwise run_log.library_cases. One that cannot be read refuses the proposal, never lets it pass."""
    from run_log import UsageError as LogUsage
    from run_log import library_cases
    if proj.get("tracker") == "excel":
        from adapters.excel import ExcelAdapter
        from adapters.excel.base import workbook_files
        known: set[str] = set()
        for file in workbook_files(proj):
            try:
                ad = ExcelAdapter.from_project(workspace, file=file)
                ad.load()
                id_col = ad.cfg["cases"]["columns"]["id"]
                known |= {str(r.get(id_col)).strip() for rows in ad.case_rows().values() for r in rows}
            except Exception as e:      # noqa: BLE001 - any unreadable workbook refuses, never an empty library
                raise UsageError(f"the case library cannot be read ({file}: {type(e).__name__}: {e}): a quarantine "
                                 "names cases of it") from None
        return known
    try:
        known, _source = library_cases(workspace, proj)
    except LogUsage as e:
        raise UsageError(f"the case library cannot be read ({e}): a quarantine names cases of it") from None
    return {str(c) for c in known}


def propose(workspace: str, qid: str, bug: str, cases: list, platforms: list, builds: list, environments: list,
            reason: str, exit_condition: str, url: str | None, tracker_bug: str | None = None) -> tuple[dict, str]:
    if not ID.fullmatch(qid or ""):
        raise UsageError(f"not a quarantine id: {qid!r} (e.g. Q-1)")
    bug, reason, exit_condition = str(bug or "").strip(), str(reason or "").strip(), str(exit_condition or "").strip()
    cases = _list(cases)
    if not bug or not cases or not reason or not exit_condition:
        raise UsageError("propose needs --bug, at least one --case, --reason and --exit (the condition for a retest or "
                         "for the end)")
    proj = read_project(workspace)
    known = _library(workspace, proj)
    unknown = [c for c in cases if c not in known]
    if unknown:
        raise UsageError(f"{', '.join(unknown)}: not in the project's library; a quarantine covers cases of it")
    tracker_bug = str(tracker_bug or "").strip() or None
    entry = {"id": qid, "bug": bug, "tracker_bug": tracker_bug, "bug_url": url or bug_url(proj, tracker_bug or bug),
             "cases": cases, "platforms": _list(platforms),
             "builds": _list(builds), "environments": _list(environments), "reason": reason, "exit": exit_condition}
    with workspace_lock(workspace):
        doc = load(workspace)
        old = doc["entries"].get(qid)
        if old and old.get("status") != "proposed":
            raise Refused(f"REFUSED: {qid} is {old.get('status')}: an id is not reused; propose another id"
                          + (" (and lift this one with the QA's decision)" if old.get("status") == "active" else ""))
        if old and all(old.get(k) == v for k, v in entry.items()):
            return old, f"{qid} already proposed as it is: nothing changed"
        history = (old or {}).get("history", [])
        history.append({"at": now(), "event": ("proposal changed" if old else "proposed") + f": bug {bug}, cases "
                        f"{', '.join(cases)}, platforms {entry['platforms'] or 'any'}, builds {entry['builds'] or 'any'}, "
                        f"environments {entry['environments'] or 'any'}"})
        doc["entries"][qid] = {**entry, "status": "proposed", "proposed_at": now(), "history": history}
        save(workspace, doc)
    return doc["entries"][qid], (f"{qid}: proposed (waits for the QA's decision: decide {qid} activate|reject "
                                 "--decided-by ... --ref ...)")


def decide(workspace: str, qid: str, action: str, decided_by: str | None, ref: str | None) -> tuple[dict, str]:
    if action not in ACTIONS:
        raise UsageError(f"unknown decision {action!r}: one of {', '.join(ACTIONS)}")
    decided_by, ref = str(decided_by or "").strip(), str(ref or "").strip()
    if not decided_by or not ref:
        raise UsageError("decide needs --decided-by (the QA who decided) and --ref (where the decision is written): a "
                         "quarantine changes only on the QA's explicit decision")
    want, becomes = ACTIONS[action]
    with workspace_lock(workspace):
        doc = load(workspace)
        entry = doc["entries"].get(qid)
        if entry is None:
            raise Refused(f"REFUSED: no quarantine {qid}")
        if entry.get("status") == becomes and entry.get("decided_by") == decided_by and entry.get("decision_ref") == ref:
            return entry, f"{qid} already {becomes}: nothing changed"
        if entry.get("status") != want:
            raise Refused(f"REFUSED: {qid} is {entry.get('status')}: {action} applies to a {want} quarantine")
        entry.update({"status": becomes, "decided_by": decided_by, "decided_at": now(), "decision_ref": ref})
        entry.setdefault("history", []).append({"at": now(), "event": f"{action} by {decided_by} ({ref})"})
        save(workspace, doc)
    return entry, f"{qid}: {becomes} (decided by {decided_by})"


def holds(entry: dict, platform: str | None, build: str | None, environment: str | None) -> bool:
    """Whether a quarantine holds in a context. A list the quarantine names must contain the context's value; an unknown
    value in the context never matches a named list (a quarantine is never applied by guess)."""
    def one(values, value, same):
        if not values:
            return True
        return bool(value) and any(same(v, value) for v in values)
    return (one(entry.get("platforms"), platform, lambda a, b: a.strip().lower() == str(b).strip().lower())
            and one(entry.get("builds"), build, lambda a, b: same_build(a, str(b)))
            and one(entry.get("environments"), environment, lambda a, b: a.strip() == str(b).strip()))


def active(doc: dict) -> list[dict]:
    return [e for e in doc["entries"].values() if e.get("status") == "active"]


def _read_plan(workspace: str, plan: str) -> dict:
    import yaml
    try:
        with open(workspace_path(workspace, *str(plan).replace("\\", "/").split("/")), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, ValueError, OutsideWorkspace) as e:
        raise UsageError(f"{plan}: cannot be read inside the workspace ({e})") from None
    if not isinstance(data, dict):
        raise UsageError(f"{plan}: not a batch plan")
    return data


def _pairs(rows) -> dict[str, str]:
    return {str(r.get("case")).strip(): str(r.get("quarantine") or "").strip()
            for r in rows or [] if isinstance(r, dict) and r.get("case")}


def scope(workspace: str, platform: str, build: str | None, environment: str | None, plan: str | None = None) -> dict:
    doc = load(workspace)
    entries = [e for e in active(doc) if holds(e, platform, build, environment)]
    covered = {c: e for e in entries for c in e["cases"]}
    out = {"context": {"platform": platform, "build": build, "environment": environment},
           "quarantines": entries, "covered": {c: e["id"] for c, e in covered.items()}, "problems": []}
    if plan:
        data = _read_plan(workspace, plan)
        cases = [str(c).strip() for c in data.get("cases") or []]
        excluded, retest = _pairs(data.get("excluded")), _pairs(data.get("retest"))
        for c in cases:
            if c in excluded:
                out["problems"].append(f"{c}: in cases and in excluded: choose one")
            elif c in covered and c not in retest:
                out["problems"].append(f"{c}: covered by quarantine {covered[c]['id']} (bug {covered[c]['bug']}) in "
                                       "this context: the QA chooses excluded or a directed retest (retest:)")
        for c, q in list(excluded.items()) + list(retest.items()):
            if c not in covered:
                out["problems"].append(f"{c}: listed as excluded or retest under {q or '?'}, but no active quarantine "
                                       "covers it in this context: plan it as usual")
            elif q and q != covered[c]["id"]:
                out["problems"].append(f"{c}: listed under {q}, but the quarantine of this context is {covered[c]['id']}")
        for c in retest:
            if c not in cases:
                out["problems"].append(f"{c}: a directed retest must also be in cases")
        out["plan"] = {"file": plan, "cases": cases, "excluded": excluded, "retest": retest}
    return out


def batch_context(proj: dict, name: str, doc: dict, build: str | None = None,
                  environment: str | None = None) -> tuple[str | None, str | None, str | None]:
    """(platform, build, environment) of a batch: its run block, else what the result about to be written gives."""
    from run_log import _platform_of
    run = doc.get("run") if isinstance(doc.get("run"), dict) else {}
    return _platform_of(proj, name, doc), run.get("build") or build, run.get("environment") or environment


def case_gate(workspace: str, proj: dict, name: str, doc: dict, case: str, build: str | None = None,
              environment: str | None = None) -> str | None:
    """Why a result of `case` must not be written in batch `name` (procedures/quarantine.md §4), or None. Enforced by
    run_log.py add: the QA's decision in the plan is never bypassed.
      - the plan left the case out (`excluded:`) while its quarantine still holds: it is not run;
      - an `excluded:` whose quarantine no longer holds here (lifted, another build, platform or environment): the plan
        is out of date and the coverage it approved changed: revalidate it and ask the QA again;
      - an active quarantine of this context covers the case and the plan has no decision for it (`retest:`).
    A workspace without a register and a plan without `excluded:` / `retest:` are never stopped here."""
    has_register = os.path.isfile(path_of(workspace))
    plan = doc.get("plan")
    excluded, retest = ({}, {})
    if plan:
        try:
            data = _read_plan(workspace, plan)
            excluded, retest = _pairs(data.get("excluded")), _pairs(data.get("retest"))
        except UsageError:
            pass
    if not has_register and not excluded:
        return None
    entries = active(load(workspace)) if has_register else []
    ctx = batch_context(proj, name, doc, build, environment)
    covering = next((e for e in entries if case in e["cases"] and holds(e, *ctx)), None)
    where = f"platform {ctx[0] or '?'}, build {ctx[1] or '?'}, environment {ctx[2] or '?'}"
    if case in excluded:
        if covering and (not excluded[case] or excluded[case] == covering["id"]):
            return (f"{case} was left out of this batch by quarantine {covering['id']} (bug {covering['bug']}) in the "
                    f"approved plan {plan}: it is not run. To run it, the QA changes the plan to a directed retest "
                    "(retest:)")
        return (f"the plan {plan} leaves {case} out by quarantine {excluded[case] or '?'}, which does not hold for this "
                f"batch ({where}): the plan is out of date. Revalidate it (quarantine.py scope --plan) and ask the QA "
                "again before running")
    if covering and retest.get(case) != covering["id"]:
        return (f"{case} is covered by quarantine {covering['id']} (bug {covering['bug']}) for {where}, and the plan "
                f"{plan or '(none recorded)'} has no decision of the QA for it: excluded or a directed retest "
                "(retest:). Run quarantine.py scope --plan, show it, and ask")
    return None


def _meanings(proj: dict) -> dict[str, set[str]]:
    words = {"pass": {"Pass", "Passou"}, "fail": {"Fail", "Falhou"}, "blocked": {"Blocked", "Bloqueado"},
             "not run": {"Not run", "Por executar", "To Do"}}
    try:
        from run_log import project_status
        for meaning in list(words):
            try:
                words[meaning].add(project_status(proj, meaning))
            except Exception:      # noqa: BLE001, S112 - a project with no word for it keeps the usual ones
                continue
    except ImportError:
        pass
    return words


def report(workspace: str, batches: list[str] | None = None) -> dict:
    """The quarantine report of the run logs whose name matches `batches` (fnmatch patterns; all when None)."""
    proj = read_project(workspace)
    folder = workspace_path(workspace, str(proj.get("run_log") or "runs/").strip("/"))
    logs = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if batches and not any(fnmatch.fnmatch(name, b) for b in batches):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                logs.append((name, json.load(f)))
        except (OSError, ValueError):
            continue
    return report_logs(workspace, proj, logs)


def has_data(r: dict) -> bool:
    """Whether a report has anything to show (a report section is omitted otherwise)."""
    return any(r.get(k) for k in ("excluded", "known_failures", "retests", "decisions"))


def report_logs(workspace: str, proj: dict, logs, platform: str | None = None, build: str | None = None,
                environment: str | None = None, proposed_in_scope: bool = False) -> dict:
    """The quarantine report of the run logs given, as (name, run log) pairs: the scope of the caller (qa_report.py gives
    the batches and results of its own report, `--since` included). A run log outside the platform, build or environment
    given is left out. The plan recorded in each run log is the decision of its time: an `excluded:` or `retest:` case is
    shown with the quarantine it names even after that quarantine was lifted (the history is never rewritten). With
    proposed_in_scope, a quarantine still proposed is listed only when it would hold for a run log of the scope."""
    doc = load(workspace)
    entries = list(doc["entries"].values())
    words = _meanings(proj)
    pending_word = next(iter(sorted(words["not run"] & set(_vocabulary(proj)) or {"Not run"})))
    out = {"excluded": [], "known_failures": [], "retests": [], "decisions": [], "batches": 0}
    if not entries:
        return out
    from run_log import _platform_of
    by_id = {e["id"]: e for e in entries}
    contexts = []
    for name, log in logs:
        if not isinstance(log, dict) or not isinstance(log.get("results"), list):
            continue
        run = log.get("run") if isinstance(log.get("run"), dict) else {}
        ctx = (_platform_of(proj, name, log), run.get("build"), run.get("environment"))
        if platform and str(ctx[0] or "").strip().lower() != platform.strip().lower():
            continue
        if build and not (ctx[1] and same_build(str(ctx[1]), build)):
            continue
        if environment and str(ctx[2] or "").strip() != environment.strip():
            continue
        out["batches"] += 1
        contexts.append(ctx)
        here = [e for e in entries if e.get("status") in ("active", "lifted", "proposed") and holds(e, *ctx)]
        excluded, retest = {}, {}
        if log.get("plan"):
            try:
                data = _read_plan(workspace, log["plan"])
                excluded, retest = _pairs(data.get("excluded")), _pairs(data.get("retest"))
            except UsageError:
                pass
            for case, qid in excluded.items():
                e = by_id.get(qid) or {}
                out["excluded"].append({"batch": name, "case": case, "status_shown": pending_word,
                                        "label": excluded_label(proj),
                                        "quarantine": qid, "bug": e.get("bug"), "bug_url": e.get("bug_url"),
                                        "reason": e.get("reason"), "quarantine_status": e.get("status"),
                                        "counts_as_run": False})
        for item in log["results"]:
            if not isinstance(item, dict):
                continue
            case, status = str(item.get("tc") or "").strip(), str(item.get("status") or "").strip()
            planned = by_id.get(retest.get(case, ""))
            for e in [planned] if planned else here:
                if case not in e["cases"]:
                    continue
                row = {"batch": name, "case": case, "status": status, "quarantine": e["id"], "bug": e["bug"],
                       "bug_url": e.get("bug_url"), "linked_bug": item.get("bug"), "evidence": item.get("evidence"),
                       "quarantine_status": e.get("status")}
                linked = str(item.get("bug") or "").strip()
                if status in words["fail"] and linked and linked in {str(e["bug"]), str(e.get("tracker_bug") or "")}:
                    out["known_failures"].append(row)
                elif status in words["fail"]:
                    out["retests"].append({**row, "verdict": "different failure: normal review (/manage-bugs); "
                                                             "the quarantine does not explain it"})
                elif status in words["pass"]:
                    out["retests"].append({**row, "verdict": "retest passed: the quarantine stays until the QA decides"})
                    if e.get("status") == "active":
                        out["decisions"].append(f"{e['id']} (bug {e['bug']}): {case} passed in {name}: the QA "
                                                f"decides whether it is lifted (decide {e['id']} lift)")
                else:
                    out["retests"].append({**row, "verdict": "retest not concluded (" + (status or "no status") + ")"})
    out["decisions"] += [f"{e['id']} (bug {e['bug']}): proposed, waits for the QA's decision (activate or reject)"
                         for e in entries if e.get("status") == "proposed"
                         and (not proposed_in_scope or any(holds(e, *c) for c in contexts))]
    return out


LABEL_DEFAULT = "excluded by quarantine"


def excluded_label(proj: dict) -> str:
    """The words the report puts after the pending status of a case left out: `quarantine.excluded_label` of
    project.yaml (the project's language, e.g. «excluído por quarentena»), else the default."""
    section = proj.get("quarantine") if isinstance(proj.get("quarantine"), dict) else {}
    return str(section.get("excluded_label") or LABEL_DEFAULT).strip()


def _vocabulary(proj: dict) -> list[str]:
    try:
        from run_log import vocabulary
        return vocabulary(proj)
    except Exception:      # noqa: BLE001
        return []


def _entry_line(e: dict) -> str:
    where = (f"platforms {', '.join(e['platforms']) or 'any'} · builds {', '.join(e['builds']) or 'any'} · environments "
             f"{', '.join(e['environments']) or 'any'}")
    return (f"{e['id']} [{e.get('status')}] bug {e['bug']}"
            + (f" = {e['tracker_bug']} in the tracker" if e.get("tracker_bug") else "")
            + (f" ({e['bug_url']})" if e.get("bug_url") else "")
            + f"\n    cases: {', '.join(e['cases'])}\n    scope: {where}\n    reason: {e['reason']}\n    exit: {e['exit']}"
            + (f"\n    decided by {e['decided_by']} on {e['decided_at']} ({e['decision_ref']})" if e.get("decided_by") else ""))


def render_report(r: dict) -> str:
    lines = [f"QUARANTINE REPORT ({r['batches']} run log(s) read)"]
    lines.append(f"\nLeft out by quarantine ({len(r['excluded'])}): not executed, never counted as Pass nor as run")
    lines += [f"  - {x['case']} in {x['batch']}: {x['status_shown']} — {x.get('label') or LABEL_DEFAULT} "
              f"{x['quarantine']} · bug {x['bug']}"
              + (f" {x['bug_url']}" if x.get("bug_url") else "") + f" · {x['reason']}" for x in r["excluded"]]
    lines.append(f"\nKnown failures observed ({len(r['known_failures'])}): the result stays, linked to the bug")
    lines += [f"  - {x['case']} in {x['batch']}: {x['status']} · bug {x['bug']}" for x in r["known_failures"]]
    lines.append(f"\nRetests run ({len(r['retests'])})")
    lines += [f"  - {x['case']} in {x['batch']}: {x['status']} · quarantine {x['quarantine']}: {x['verdict']}"
              for x in r["retests"]]
    lines.append(f"\nDecisions of the QA pending ({len(r['decisions'])})")
    lines += [f"  - {d}" for d in r["decisions"]]
    return "\n".join(lines)


def _excluded_md(x: dict) -> str:
    lifted = " (lifted since: the plan's decision stays)" if x.get("quarantine_status") == "lifted" else ""
    return (f"- {x['case']} in {x['batch']}: {x['status_shown']} — {x.get('label') or LABEL_DEFAULT} "
            f"{x['quarantine']}{lifted} · bug {x['bug']}" + (f" {x['bug_url']}" if x.get("bug_url") else "")
            + (f" · {x['reason']}" if x.get("reason") else ""))


def render_markdown(r: dict, brief: bool = False) -> str:
    """The quarantine section of /qa-report's reports (qa_report.py report), from report_logs; '' when there is nothing
    to show. `brief` is the summary's form: the same rows, without the empty groups."""
    if not has_data(r):
        return ""
    groups = [
        ((f"Left out by quarantine ({len(r['excluded'])}): not executed, outside the counts of run cases (never Pass, "
          "never run)"), [_excluded_md(x) for x in r["excluded"]]),
        ((f"Known failures observed ({len(r['known_failures'])}): counted in the totals as recorded, linked to the "
          "existing bug"), [f"- {x['case']} in {x['batch']}: {x['status']} · quarantine {x['quarantine']} · bug {x['bug']}"
                          + (f" {x['bug_url']}" if x.get("bug_url") else "") for x in r["known_failures"]]),
        (f"Retests run ({len(r['retests'])}): counted in the totals as recorded",
         [f"- {x['case']} in {x['batch']}: {x['status']} · quarantine {x['quarantine']} · bug {x['bug']}: {x['verdict']}"
          for x in r["retests"]]),
        (f"Decisions of the QA pending ({len(r['decisions'])})", [f"- {d}" for d in r["decisions"]]),
    ]
    lines = ["## Quarantine", "",
             (f"From the register memory/quarantine.json and the plan of each run log in this scope "
              f"({r['batches']} run log(s)); the totals above are unchanged by it."), ""]
    for title, rows in groups:
        if brief and not rows:
            continue
        lines += [f"**{title}**", ""] + (rows or ["- none"]) + [""]
    return "\n".join(lines).rstrip() + "\n"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/quarantine.py", description="Bugs in quarantine (procedures/quarantine.md).")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("propose")
    p.add_argument("id")
    p.add_argument("--bug")
    p.add_argument("--bug-url")
    p.add_argument("--tracker-bug", help="the bug's id in the tracker when --bug is the workbook's (e.g. BUG-7 = 9770)")
    p.add_argument("--case", action="append")
    p.add_argument("--platform", action="append")
    p.add_argument("--build", action="append")
    p.add_argument("--environment", action="append")
    p.add_argument("--reason")
    p.add_argument("--exit", dest="exit_condition")
    p = sub.add_parser("decide")
    p.add_argument("id")
    p.add_argument("action")
    p.add_argument("--decided-by")
    p.add_argument("--ref")
    p = sub.add_parser("scope")
    p.add_argument("--platform", required=True)
    p.add_argument("--build")
    p.add_argument("--environment")
    p.add_argument("--plan")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--batch", action="append")
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        if not os.path.isfile(os.path.join(ws, "project.yaml")):
            raise UsageError(f"{ws} has no project.yaml: not a workspace")
        if a.command == "show":
            doc = load(ws)
            if a.json:
                print(json.dumps(doc, ensure_ascii=False, indent=1))
            else:
                entries = list(doc["entries"].values())
                print("No quarantine in this workspace." if not entries else "\n".join(_entry_line(e) for e in entries))
            return 0
        if a.command == "propose":
            entry, text = propose(ws, a.id, a.bug, a.case, a.platform, a.build, a.environment, a.reason,
                                  a.exit_condition, a.bug_url, a.tracker_bug)
            print(text)
            print(_entry_line(entry))
            return 0
        if a.command == "decide":
            entry, text = decide(ws, a.id, a.action, a.decided_by, a.ref)
            print(text)
            print(_entry_line(entry))
            return 0
        if a.command == "scope":
            r = scope(ws, a.platform, a.build, a.environment, a.plan)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
            else:
                c = r["context"]
                print(f"Context: platform {c['platform']} · build {c['build'] or '?'} · environment {c['environment'] or '?'}")
                if not r["quarantines"]:
                    print("No active quarantine holds in this context: plan every case as usual.")
                for e in r["quarantines"]:
                    print(_entry_line(e))
                    print("    -> for the plan's review: the QA chooses, per case, excluded (not run) or a directed retest")
                for p in r["problems"]:
                    print(f"PLAN PROBLEM: {p}")
            return 1 if r["problems"] else 0
        r = report(ws, a.batch)
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str) if a.json else render_report(r))
        return 0
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except OutsideWorkspace as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 1
    except WorkspaceBusy as e:
        print(f"BUSY: {e}: nothing was written; run the same command again", file=sys.stderr)
        return 5


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
