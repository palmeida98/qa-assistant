"""
Reconciled totals and an audit of a workspace's run logs, for /qa-report and /checkpoint (skills-spec.md §5). Read only.

    python scripts/qa_report.py <workspace> totals [--batch NAME ...] [--since YYYY-MM-DD] [--platform P]
                                                   [--plan batches/<name>.yaml ...] [--json] [--output FILE]
    python scripts/qa_report.py <workspace> audit [--batch NAME ...] [--since YYYY-MM-DD] [--json]

Where a result belongs:
- its PLATFORM: the run block's "platform"; otherwise the first pattern of `report.platforms` in project.yaml that
  matches the batch's name ({ios: ["batch_ios*"], android: ["batch_2026*"]}); otherwise `unknown`. Never guessed.
- its BUILD: the run block's "build", otherwise `unknown` (never the current build, never counted in a build).
- an item (or run block) with "applies": "<batch>" records that batch's execution in the workbook, perhaps under
  another status decided later: it is not an execution, needs no ts, and never competes for the current result; the
  execution stays current and is marked applied, and both statuses are shown when they differ.

totals
- Per platform, the CURRENT result of each case is its newest one (by "ts"; on the same "ts", a result whose item or
  run block declares "revision_of" wins, then the batch applied later; a tie that nothing decides is listed as
  ambiguous; the file's modification time never decides); the older ones are HISTORICAL. Each current result says
  whether its batch is applied, so a recent result not applied yet is visible. Builds are grouped by version and code
  (`pt.app 1.0.0 (23)` and `1.0.0 (23)` are one build). The output is short; --detail lists each retest. A case run
  more than once on a platform counts once in the totals and is listed as a RETEST. Totals per platform and build add
  up to the number of cases of that platform (the denominator), and the output says so ("reconciled").
- Platforms are never added into one percentage: each one has its own denominator.
- With --plan, the cases planned with no result on that platform are listed as NOT RUN (coverage still missing).

audit
- Per batch: Question still open, a case repeated, evidence named that does not exist, results without evidence,
  bugs proposed ("proposed": true) waiting for review, a run block without build, and whether the batch was applied
  (the workbook of the batch by excel.workbooks, or tracker/sent/; `unknown` when that cannot be read, and unknown
  counts as NOT applied), and results without ts.

Exit codes: 0 done (audit: nothing to fix) · 1 audit: something to fix before applying · 2 usage or configuration
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import output
from common import (
    BUILD_PARTS,
    OutsideWorkspace,
    atomic_write,
    defect_priority_problem,
    read_project,
    ts_key,
    workspace_path,
)

EVIDENCE = re.compile(r"evidence/[^\s'\"(),;]+\.(?:png|jpe?g|mp4|mov|json|txt|xml|log)", re.IGNORECASE)
UNKNOWN = "unknown"


class UsageError(Exception):
    """Usage or configuration (code 2)."""


def run_log_dir(workspace: str, proj: dict) -> str:
    return os.path.join(workspace, str(proj.get("run_log") or "runs/").strip("/\\"))


def batch_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def load_batches(workspace: str, proj: dict, names: list[str] | None = None, since: str | None = None) -> list[dict]:
    """The run logs of the workspace (one per batch), oldest first. A file that is not a run log is skipped."""
    folder = run_log_dir(workspace, proj)
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = batch_name(path)
        if names and name not in names and name.removeprefix("batch_") not in names:
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
            continue
        results = [r for r in doc["results"] if isinstance(r, dict) and r.get("tc")]
        if since:
            results = [r for r in results if ts_key(r.get("ts")) >= since]
            if not results:
                continue
        out.append({"name": name, "path": path, "written": os.path.getmtime(path),
                    "run": doc.get("run") if isinstance(doc.get("run"), dict) else {},
                    "mode": doc.get("mode") if isinstance(doc.get("mode"), str) else None,
                    "results": results})
    if names:
        found = {b["name"] for b in out} | {b["name"].removeprefix("batch_") for b in out}
        missing = [n for n in names if n not in found]
        if missing:
            raise UsageError(f"no run log for: {', '.join(missing)} (in {os.path.relpath(folder, workspace)}/)")
    return out


def platform_of(batch: dict, proj: dict) -> str:
    value = str((batch.get("run") or {}).get("platform") or "").strip().lower()
    if value:
        return value
    rules = ((proj.get("report") or {}).get("platforms") or {}) if isinstance(proj.get("report"), dict) else {}
    for platform, patterns in rules.items():
        for pattern in [patterns] if isinstance(patterns, str) else patterns or []:
            if fnmatch.fnmatch(batch["name"], str(pattern)):
                return str(platform).lower()
    return UNKNOWN


def applies_of(batch: dict, result: dict) -> str:
    """The batch whose execution this item records in the workbook ("applies": "<batch>" on the item or the run
    block), or ''. Such an item is not an execution: it has no execution time of its own and never competes for the
    current result."""
    link = result.get("applies") or (batch.get("run") or {}).get("applies") or ""
    if isinstance(link, dict):
        link = link.get("batch") or ""
    return str(link).strip()


def build_of(batch: dict) -> str:
    return str((batch.get("run") or {}).get("build") or "").strip() or UNKNOWN


def build_key(build: str) -> str:
    """One label per build however it was written (`pt.app 1.0.0 (23)`, `1.0.0 (23)`, `1.0.0 (code 23)`): the version
    and the code when both are there (common.same_build), the text otherwise."""
    m = BUILD_PARTS.search(build or "")
    return f"{m.group(1)} ({m.group(2)})" if m else (build or UNKNOWN)


def is_question(status) -> bool:
    return str(status or "").strip().casefold().startswith("question")


def is_unknown(applied) -> bool:
    """An application that could not be read is not an application (audit 06.11.2026)."""
    return isinstance(applied, str) and applied.startswith(UNKNOWN)


def revision_target(batch: dict, result: dict):
    """(batch, case) a result revises ("revision_of": {"batch", "tc"}, or the older plain batch name), or None."""
    link = result.get("revision_of") or (batch.get("run") or {}).get("revision_of")
    if isinstance(link, dict) and link.get("batch"):
        return str(link["batch"]), str(link.get("tc") or result.get("tc"))
    if isinstance(link, str) and link.strip():
        return link.strip(), str(result.get("tc"))
    return None


def _item(batches: list[dict], name: str, case: str):
    for b in batches:
        if b["name"] == name:
            for r in b["results"]:
                if str(r.get("tc")) == case:
                    return b, r
    return None, None


def revision_links(batches: list[dict], proj: dict) -> tuple[dict, set, dict]:
    """Checks every resolution of a Question that points to another batch ("revision_of": {"batch", "tc"} with a
    "resolution"). Returns (superseded, valid, problems):
    - superseded {(batch, case): resolver batch}: the Questions a VALID resolution took the place of: they stay in the
      history but are neither a retest nor an older execution;
    - valid: the (batch, case) of the resolutions that passed every check;
    - problems {(batch, case): why}: a link that does not hold is shown by the audit and changes NOTHING in the totals
      (it hides no result and gets no advantage in a tie).
    A link holds when its target exists, is a Question of the same case on the same platform, is not itself a resolution
    or revision, is in another batch, and no other resolution claims the same target."""
    claims: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    problems: dict[tuple[str, str], str] = {}
    for b in batches:
        for r in b["results"]:
            if not isinstance(r.get("resolution"), dict) or not (target := revision_target(b, r)):
                continue
            key, case = (b["name"], str(r["tc"])), str(r["tc"])
            link = r.get("revision_of")
            tb, tr = _item(batches, *target)
            if isinstance(link, dict) and str(link.get("tc") or case) != case:
                why = f"revision_of names another case ({link.get('tc')})"
            elif target[0] == b["name"]:
                why = "revision_of points to its own batch"
            elif tr is None:
                why = f"revision_of points to a result that does not exist ({target[0]}/{target[1]})"
            elif not is_question(tr.get("status")):
                why = f"the result it resolves is {tr.get('status')!r}, not a Question ({target[0]})"
            elif isinstance(tr.get("resolution"), dict) or tr.get("revision_of"):
                why = f"the result it resolves is itself a resolution or revision ({target[0]})"
            elif platform_of(tb, proj) != platform_of(b, proj):
                why = (f"it is read as {platform_of(b, proj)} and the Question of {target[0]} as "
                       f"{platform_of(tb, proj)}: the batch name must match report.platforms")
            else:
                claims[(target[0], target[1])].append(key)
                continue
            problems[key] = why
    superseded, valid = {}, set()
    for target, keys in claims.items():
        if len(keys) == 1:
            superseded[target] = keys[0][0]
            valid.add(keys[0])
        else:
            for key in keys:
                problems[key] = f"{target[1]} of {target[0]} is resolved more than once ({', '.join(k[0] for k in keys)})"
    return superseded, valid, problems


def _executions(runs: list[dict]) -> list[dict]:
    """The runs of a case that are executions of their own: a result superseded by its revision is the same
    observation, counted once (by the revision)."""
    return [r for r in runs if not r.get("superseded")]


def planned_cases(workspace: str, plans: list[str]) -> list[str]:
    import yaml
    cases = []
    for rel in plans:
        path = os.path.join(workspace, rel) if not os.path.isabs(rel) else rel
        try:
            with open(path, encoding="utf-8-sig") as f:
                doc = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            raise UsageError(f"plan {rel} cannot be read: {e}") from e
        for c in doc.get("cases") or []:
            if str(c) not in cases:
                cases.append(str(c))
    return cases


def totals(workspace: str, proj: dict, batches: list[dict], platform: str | None = None,
           plan: list[str] | None = None) -> dict:
    per_platform: dict[str, list[tuple[str, dict, dict]]] = defaultdict(list)
    applications: dict[tuple[str, str], list[dict]] = defaultdict(list)
    view = _applied_view(workspace, proj)
    for b in batches:
        where = view(b["name"], b.get("run"))
        m = re.search(r" on (\S+)$", str(where or ""))
        b["applied_at"] = m.group(1) if m and not is_unknown(where) else ""
        b["applied"] = "unknown" if is_unknown(where) else ("yes" if where else "no")
        p = platform_of(b, proj)
        if platform and p != platform:
            continue
        for r in b["results"]:
            link = applies_of(b, r)
            if link:            # the record of an earlier execution in the workbook, not an execution of its own
                applications[(p, str(r["tc"]))].append({"source": link, "batch": b["name"], "status": r.get("status"),
                                                        "applied": b["applied"], "applied_at": b["applied_at"]})
                continue
            per_platform[p].append((ts_key(r.get("ts")), b, r))
    superseded, valid_links, _ = revision_links(load_batches(workspace, proj), proj)
    report = {"platforms": {}, "rule": "the newest result per case and platform is current (by ts; on the same "
              "instant, a result that declares revision_of the other wins, then the batch applied later; a tie with "
              "nothing to decide it is listed as ambiguous); the older ones are historical; platforms are never "
              "added into one percentage"}
    for p, items in sorted(per_platform.items()):
        # never the file's modification time: touching a file changed which result was current (audit 06.11.2026)
        def revises(b, r):
            link = r.get("revision_of") or (b.get("run") or {}).get("revision_of")
            if isinstance(r.get("resolution"), dict) and isinstance(link, dict):
                return 1 if (b["name"], str(r["tc"])) in valid_links else 0     # a link that does not hold wins nothing
            return 1 if link else 0
        items.sort(key=lambda x: (x[0], revises(x[1], x[2]), str(x[1].get("applied_at") or ""), x[1]["name"]))
        by_case: dict[str, list] = defaultdict(list)
        for ts, b, r in items:
            by_case[str(r["tc"])].append({"batch": b["name"], "build": build_of(b), "status": r.get("status"),
                                          "ts": str(r.get("ts") or ""),
                                          "superseded": (b["name"], str(r["tc"])) in superseded,
                                          "applied": b.get("applied", "no"), "_order": (ts, revises(b, r),
                                                                                       str(b.get("applied_at") or ""))})
        ambiguous = sorted(case for case, runs in by_case.items()
                           if len(runs) > 1 and runs[-1]["_order"] == runs[-2]["_order"]
                           and runs[-1]["status"] != runs[-2]["status"])
        for runs in by_case.values():
            for r in runs:
                r.pop("_order", None)
        current = {case: runs[-1] for case, runs in by_case.items()}
        # an item that `applies` an execution records it in the workbook (possibly under another status, a decision
        # taken later): the execution stays the current result with its own time; the application is said apart
        applied_as = {}
        for (pp, case), apps in applications.items():
            if pp != p or case not in current:
                continue
            for a in apps:
                cur = current[case]
                if a["source"] == cur["batch"]:
                    if a["applied"] == "yes" and cur["applied"] != "yes":
                        cur["applied"] = "yes"
                    applied_as[case] = {"execution": f"{cur['status']} ({cur['batch']}, {cur['ts'] or 'no ts'})",
                                        "applied": f"{a['status']} ({a['batch']}, applied "
                                                   f"{a['applied_at'] or a['applied']})",
                                        "same_status": str(a["status"]) == str(cur["status"])}
                else:
                    applied_as[case] = {"execution": f"{cur['status']} ({cur['batch']}, {cur['ts'] or 'no ts'})",
                                        "applied": f"{a['status']} ({a['batch']}) records {a['source']}, not the "
                                                   "current execution", "same_status": False, "stale": True}
        by_build: dict[str, Counter] = defaultdict(Counter)
        for cur in current.values():
            by_build[build_key(cur["build"])][str(cur["status"])] += 1
        status_total = Counter(str(c["status"]) for c in current.values())
        denominator = len(current)
        entry = {
            "cases": denominator,
            "statuses": dict(sorted(status_total.items())),
            "by_build": {build: {"cases": sum(c.values()), "statuses": dict(sorted(c.items()))}
                         for build, c in sorted(by_build.items())},
            "retests": {case: runs for case, runs in sorted(by_case.items()) if len(_executions(runs)) > 1},
            "historical_results": sum(max(len(_executions(r)) - 1, 0) for r in by_case.values()),
            "resolved_questions": {case: [r["batch"] for r in runs if r["superseded"]]
                                   for case, runs in sorted(by_case.items()) if any(r["superseded"] for r in runs)},
            "batches": sorted({b["name"] for _, b, _ in items}),
            "current_applied": sum(1 for c in current.values() if c["applied"] == "yes"),
            "current_not_applied": sorted(c for c, v in current.items() if v["applied"] == "no"),
            "current_application_unknown": sorted(c for c, v in current.items() if v["applied"] == "unknown"),
            "without_ts": sorted({str(r["tc"]) for ts, _, r in items if not ts}),
            "ambiguous_order": ambiguous,
            "applied_as": dict(sorted(applied_as.items())),
            "unknown_build": sorted(c for c, v in current.items() if build_key(v["build"]) == UNKNOWN),
        }
        entry["reconciled"] = (sum(status_total.values()) == denominator ==
                               sum(v["cases"] for v in entry["by_build"].values()))
        if plan:
            entry["planned"] = len(plan)
            entry["not_run"] = [c for c in plan if c not in current]
            entry["run_of_planned"] = len(plan) - len(entry["not_run"])
        report["platforms"][p] = entry
    return report


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "n/a"


def render_totals(report: dict, scope: str, detail: bool = False) -> str:
    lines = [f"# QA totals — {scope}", "", f"Rule: {report['rule']}.", ""]
    if not report["platforms"]:
        lines.append("No result in this scope.")
    for p, e in report["platforms"].items():
        lines.append(f"## Platform: {p}")
        lines.append(f"- Cases with a current result: {e['cases']} (denominator of every percentage below)")
        for status, n in e["statuses"].items():
            lines.append(f"  - {status}: {n} ({_pct(n, e['cases'])} of {e['cases']})")
        lines.append("- By build of the current result:")
        for build, b in e["by_build"].items():
            parts = ", ".join(f"{s} {n}" for s, n in b["statuses"].items())
            lines.append(f"  - {build}: {b['cases']} case(s) — {parts}")
        lines.append(f"- Reconciled (statuses = builds = cases): {'yes' if e['reconciled'] else 'NO'}")
        lines.append(f"- Current results applied to the workbook or tracker: {e['current_applied']} of {e['cases']}; "
                     f"not applied yet: {len(e['current_not_applied'])}; application unknown: "
                     f"{len(e['current_application_unknown'])}")
        if e["current_not_applied"]:
            lines.append("  - From a batch not recorded as applied (the workbook may show the same status through "
                         "another batch, or an older one; compare before applying): "
                         + ", ".join(e["current_not_applied"][:30])
                         + (f" … (+{len(e['current_not_applied']) - 30})" if len(e["current_not_applied"]) > 30 else ""))
        if e.get("unknown_build"):
            lines.append(f"- Build unknown (no build in the run block; never counted as any build): "
                         f"{len(e['unknown_build'])} case(s)")
        for case, a in (e.get("applied_as") or {}).items():
            if a.get("stale"):
                lines.append(f"- {case}: execution {a['execution']}; the workbook record {a['applied']}")
            elif not a["same_status"]:
                lines.append(f"- {case}: execution {a['execution']}; applied as {a['applied']} (a later decision; "
                             "the execution keeps its own status and time)")
        if e["without_ts"]:
            lines.append(f"- WARNING: {len(e['without_ts'])} case(s) have a result without ts, ordered as the oldest: "
                         + ", ".join(e["without_ts"][:20]))
        if e["ambiguous_order"]:
            lines.append(f"- WARNING: same instant, different status and nothing to say which is newer (set "
                         f"revision_of on the correction): {', '.join(e['ambiguous_order'])}")
        lines.append(f"- Historical results (older runs of the same cases): {e['historical_results']}")
        if e["retests"] and detail:
            lines.append(f"- Retests ({len(e['retests'])} case(s)):")
            for case, runs in e["retests"].items():
                lines.append("  - " + case + ": " + " → ".join(f"{r['status']} ({r['build']}, {r['batch']})" for r in runs))
        elif e["retests"]:
            lines.append(f"- Retests: {len(e['retests'])} case(s) (the sequence of each: --detail)")
        if "planned" in e:
            lines.append(f"- Planned: {e['planned']}; with a result: {e['run_of_planned']} "
                         f"({_pct(e['run_of_planned'], e['planned'])} of {e['planned']}); not run: {len(e['not_run'])}")
            if e["not_run"]:
                lines.append("  - Not run: " + ", ".join(e["not_run"]))
        lines.append(f"- Batches: {len(e['batches'])}" + (f" ({', '.join(e['batches'])})" if detail else
                                                            " (names: --detail)"))
        lines.append("")
    if UNKNOWN in report["platforms"]:
        lines.append("Note: results of platform `unknown` have no run.platform and match no `report.platforms` rule of "
                     "project.yaml; they are not counted in any other platform.")
    return "\n".join(lines).rstrip() + "\n"


def _evidence(item: dict) -> list[str]:
    ev = item.get("evidence")
    named = [ev] if isinstance(ev, str) else [e for e in ev or [] if isinstance(e, str)]
    return list(dict.fromkeys(named + EVIDENCE.findall(str(item.get("actual_result") or ""))))


def _invalid_marks(item: dict) -> dict:
    """{path: reason} of the evidence a result marks invalid (`run_log.py attach --invalid`): kept on file, not proof."""
    return {str(m.get("path")): str(m.get("reason") or "no reason written")
            for m in item.get("evidence_invalid") or [] if isinstance(m, dict) and m.get("path")}


def _evidence_state(workspace: str, item: dict) -> dict:
    """The evidence of one result in three apart lists: `valid` (named, present, not marked invalid), `invalid` ((path,
    reason): marked, whether or not the file is still there) and `missing` (named, not marked, not on disk). A pattern
    counts as present when it matches a file."""
    marks = _invalid_marks(item)
    out = {"valid": [], "invalid": [], "missing": []}
    for e in _evidence(item):
        if e in marks:
            out["invalid"].append((e, marks[e]))
            continue
        full = os.path.join(workspace, e)
        found = glob.glob(full) if any(c in e for c in "*?[") else os.path.exists(full)
        out["valid" if found else "missing"].append(e)
    return out


def _applied_in(workspace: str, cfg: dict) -> dict | str:
    """{batch_id: applied_at} of one workbook's control sheet, read only; a str says why it could not be read."""
    try:
        from adapters.excel import ExcelAdapter
        ad = ExcelAdapter(cfg, base_dir=workspace)
        ad.load()
        return {str(b.get("batch_id")): b.get("applied_at") for b in ad.applied_batches()}
    except Exception as e:      # noqa: BLE001 - any failure to read means "unknown", never "not applied"
        return str(e) or type(e).__name__


def _applied_view(workspace: str, proj: dict):
    """A function name -> where it was applied (str), None (not applied) or UNKNOWN (with the reason). Each workbook is
    read once. `excel.workbooks` of project.yaml sends some batches to another workbook ({name: {file, batches}})."""
    import copy
    tracker = proj.get("tracker")
    if tracker in ("jira", "azure-devops"):
        return lambda name: (f"sent (tracker/sent/{name}.json)"
                             if os.path.isfile(os.path.join(workspace, "tracker", "sent", f"{name}.json")) else None)
    if tracker != "excel" or not isinstance(proj.get("excel"), dict):
        return lambda name: f"{UNKNOWN} (tracker {tracker!r})"
    from adapters.excel.base import route_workbook
    base = {k: v for k, v in proj["excel"].items() if k != "workbooks"}
    cache: dict[str, dict | str] = {}

    def view(name: str, run: dict | None = None):
        # the same rule as the apply, never $QA_WORKBOOK; a route that stops is "unknown" (counts as not applied)
        route = route_workbook(proj, name, run if isinstance(run, dict) else None, environ={}, workspace=workspace)
        if route.problem:
            return f"{UNKNOWN} ({route.problem})"
        file = route.file
        if not file:
            return f"{UNKNOWN} (no workbook for it)"
        if file not in cache:
            cfg = copy.deepcopy(base)
            cfg["file"] = file
            cache[file] = _applied_in(workspace, cfg)
        done = cache[file]
        if isinstance(done, str):
            return f"{UNKNOWN} (the workbook {file} could not be read: {done})"
        return f"applied to {file} on {done[name]}" if name in done else None
    return view


def audit(workspace: str, proj: dict, batches: list[dict]) -> dict:
    view = _applied_view(workspace, proj)
    superseded, _, bad_links = revision_links(load_batches(workspace, proj), proj)   # all of them, not a filter
    out = {"batches": [], "problems": 0}
    for b in batches:
        counts = Counter(str(r["tc"]) for r in b["results"])
        repeated = sorted(c for c, n in counts.items() if n > 1)
        questions = sorted(str(r["tc"]) for r in b["results"] if is_question(r.get("status"))
                           and (b["name"], str(r["tc"])) not in superseded)
        resolved = sorted(str(r["tc"]) for r in b["results"] if isinstance(r.get("resolution"), dict)
                          and not revision_target(b, r))
        resolved += sorted(f"{r['tc']} (in {superseded[(b['name'], str(r['tc']))]})" for r in b["results"]
                           if (b["name"], str(r["tc"])) in superseded)
        dangling = sorted(f"{r['tc']}: {bad_links[(b['name'], str(r['tc']))]}" for r in b["results"]
                          if (b["name"], str(r["tc"])) in bad_links)
        without_ts = sorted(str(r["tc"]) for r in b["results"] if not r.get("ts") and not applies_of(b, r))
        proposed = sorted(str(r["tc"]) for r in b["results"]
                          if isinstance(r.get("new_defect"), dict) and r["new_defect"].get("proposed"))
        no_priority = sorted(str(r["tc"]) for r in b["results"] if isinstance(r.get("new_defect"), dict)
                             and defect_priority_problem(proj, r["new_defect"]))
        missing, without, invalid, no_valid = [], [], [], []
        counts_ev = {"valid": 0, "invalid": 0, "missing": 0}
        for r in b["results"]:
            state = _evidence_state(workspace, r)
            if not (state["valid"] or state["invalid"] or state["missing"]):
                without.append(str(r["tc"]))
            for kind in counts_ev:
                counts_ev[kind] += len(state[kind])
            missing += [f"{r['tc']}: {e}" for e in state["missing"]]
            invalid += [f"{r['tc']}: {e} ({why})" for e, why in state["invalid"]]
            if state["invalid"] and not state["valid"] and not state["missing"]:
                no_valid.append(str(r["tc"]))
        applied = view(b["name"], b.get("run"))
        entry = {"batch": b["name"], "platform": platform_of(b, proj), "build": build_of(b), "mode": b.get("mode"),
                 "results": len(b["results"]), "applied": applied if applied else "no",
                 "questions": questions, "repeated": repeated, "missing_evidence": missing,
                 "without_evidence": without, "invalid_evidence": invalid, "without_valid_evidence": no_valid,
                 "evidence_counts": counts_ev, "proposed_bugs": proposed, "without_ts": without_ts,
                 "resolved": resolved, "invalid_revisions": dangling, "bugs_without_priority": no_priority}
        settled = bool(applied) and not is_unknown(applied)     # unknown is not applied (audit 06.11.2026)
        entry["to_fix"] = bool(repeated or missing or dangling or no_valid or ((questions or proposed or no_priority) and not settled))
        out["problems"] += int(entry["to_fix"])
        out["batches"].append(entry)
    return out


def _audit_flags(e: dict) -> list[tuple[str, bool]]:
    """(text, needs_a_decision) of one batch: what the audit found. The flags that need a decision or an action are
    never dropped by the short mode."""
    flags: list[tuple[str, bool]] = []
    if e["questions"]:
        flags.append((f"Question open: {', '.join(e['questions'])}", True))
    if e.get("resolved"):
        flags.append((f"Question resolved (decision recorded, observation kept): {', '.join(e['resolved'])}", False))
    if e.get("invalid_revisions"):
        flags.append((f"resolution that does not hold (it changes no total): {'; '.join(e['invalid_revisions'])}", True))
    if e["repeated"]:
        flags.append((f"case repeated: {', '.join(e['repeated'])}", True))
    if e["missing_evidence"]:
        flags.append((f"evidence named but missing: {'; '.join(e['missing_evidence'])}", True))
    if e.get("invalid_evidence"):
        flags.append((f"evidence marked invalid (kept on file, not proof of the result): {'; '.join(e['invalid_evidence'])}",
                      True))
    if e.get("without_valid_evidence"):
        flags.append((f"result(s) whose only evidence is invalid: {', '.join(e['without_valid_evidence'])}", True))
    if e.get("bugs_without_priority"):
        flags.append(("bug(s) waiting for the user's priority decision (the project requires one): "
                      + ", ".join(e["bugs_without_priority"]), True))
    if e["proposed_bugs"]:
        flags.append((f"bugs proposed, to review: {', '.join(e['proposed_bugs'])}", True))
    if e["build"] == UNKNOWN:
        flags.append(("no build in the run block", True))
    if e["without_evidence"]:
        flags.append((f"{len(e['without_evidence'])} result(s) without evidence named", True))
    if not e.get("mode") and e["applied"] == "no" and e["results"]:
        flags.append(("no mode recorded: how it was run is not known (never filled in by guess)", True))
    if e.get("without_ts"):
        flags.append((f"result(s) without ts (cannot be ordered against other runs): {', '.join(e['without_ts'])}",
                      True))
    return flags


def _evidence_line(e: dict) -> str:
    c = e.get("evidence_counts") or {}
    return f"evidence: {c.get('valid', 0)} valid · {c.get('invalid', 0)} invalid · {c.get('missing', 0)} missing"


def render_audit(report: dict, detail: bool = True) -> str:
    """The complete audit (detail) or a summary: a count line, then every batch that needs something, with the flags that
    need a decision or an action in full, and the batches with nothing to say counted, not listed."""
    lines = []
    summary_clean = 0
    for e in report["batches"]:
        flags = _audit_flags(e)
        mark = "FIX " if e["to_fix"] else "    "
        kind = str(e.get("mode") or "").partition(":")[0].strip()
        head = (f"{mark}{e['batch']} [{e['platform']}, {e['build']}{', ' + kind if kind else ''}] {e['results']} "
                f"result(s), applied: {e['applied']}")
        if detail:
            shown = [t for t, _m in flags] + ([_evidence_line(e)] if any(e.get("evidence_counts", {}).values()) else [])
            lines.append(head + "".join(f"\n       - {f}" for f in shown))
            continue
        needs = [t for t, must in flags if must]
        if not needs and not e["to_fix"]:
            summary_clean += 1
            continue
        lines.append(head + "".join(f"\n       - {t}" for t in needs))
    if not detail:
        n = len(report["batches"])
        lines.insert(0, f"AUDIT {n} batch(es): {report['problems']} to fix · {summary_clean} with nothing that needs "
                        "a decision (every batch and flag: --detail)")
    lines.append(f"{report['problems']} batch(es) to fix before applying" if report["problems"] else
                 "nothing to fix: every batch is consistent (applying still needs the user's confirmation)")
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/qa_report.py", description="Reconciled totals and an audit of "
                                 "the run logs. Read only.")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("totals", "audit"):
        p = sub.add_parser(name)
        p.add_argument("--batch", action="append", help="a batch, with or without the batch_ prefix (repeatable)")
        p.add_argument("--since", help="results with ts on or after this date (YYYY-MM-DD)")
        p.add_argument("--json", action="store_true")
        if name == "audit":
            output.add_flags(p)
        if name == "totals":
            p.add_argument("--platform")
            p.add_argument("--plan", action="append", help="batches/<name>.yaml: the cases planned (repeatable)")
            p.add_argument("--output", help="write the report to this file, inside the workspace (e.g. exports/x.md)")
            p.add_argument("--detail", action="store_true", help="list each retest's sequence and the batch names")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        try:
            proj = read_project(ws)
        except FileNotFoundError as e:
            raise UsageError(str(e)) from e
        if a.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.since):
            raise UsageError(f"--since: expected YYYY-MM-DD, got {a.since!r}")
        batches = load_batches(ws, proj, a.batch, a.since)
        if a.command == "audit":
            report = audit(ws, proj, batches)
            cfg = output.settings(proj, output.cli_choice(a))
            print(json.dumps(report, ensure_ascii=False, indent=1) if a.json
                  else render_audit(report, not output.is_brief(cfg)), end="")
            return 1 if report["problems"] else 0
        plan = planned_cases(ws, a.plan) if a.plan else None
        report = totals(ws, proj, batches, (a.platform or "").lower() or None, plan)
        scope = ", ".join(filter(None, [f"batches {', '.join(a.batch)}" if a.batch else "all run logs",
                                        f"since {a.since}" if a.since else "", f"platform {a.platform}" if a.platform
                                        else "", f"plan {', '.join(a.plan)}" if a.plan else ""]))
        text = (json.dumps(report, ensure_ascii=False, indent=1) + "\n" if a.json
                else render_totals(report, scope, a.detail or bool(a.output)))
        if a.output:
            path = workspace_path(ws, a.output)
            atomic_write(path, text)
            print(f"written: {os.path.relpath(path, ws)}")
        else:
            print(text, end="")
        return 0
    except (UsageError, OutsideWorkspace) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
