"""
The progress of one PBI through /test-pbi, so the flow can stop and resume in another session (skills-spec.md §5).

    python scripts/pbi_state.py <workspace> start <pbi> [--task ID] [--platform P ...]
    python scripts/pbi_state.py <workspace> show <pbi> [--json]
    python scripts/pbi_state.py <workspace> mark <pbi> <step> done|blocked|pending|skipped [--ref REF] [--note TEXT]
    python scripts/pbi_state.py <workspace> baseline <pbi> [--ticket ID=REV ...] [--build PLATFORM=BUILD ...]
                                                         [--environment KEY] [--spec-plan FILE]
    python scripts/pbi_state.py <workspace> check <pbi> [--ticket ID=REV ...] [--build PLATFORM=BUILD ...]
                                                      [--environment KEY] [--check CHECK_ID] [--spec-root DIR]
                                                      [--reopen]
    python scripts/pbi_state.py <workspace> published <pbi> <draft> <tracker-key> --confirmed-by REF

The record is <workspace>/memory/pbi/<pbi>.json, written whole and atomically under the workspace's lock. It holds the
steps (context, analyze, plan, run:<platform>, bugs, report, checkpoint), what the plan was based on (the baseline:
ticket revisions, the build per platform, the environment), the bugs published and an append-only history.

- start: creates the record, or shows it and adds the platforms given; a platform is never removed.
- mark: `done` needs --ref (the file, check id or batch that proves it). Marking done again with the same ref changes
  nothing; another ref replaces it and the old one stays in the history.
- check: compares what is given with the baseline and names the steps to review. A build that changed does not
  invalidate the results of the old build: they stay valid for that build, and a new run needs a new batch. What it
  could not compare is said (a baseline value not given now, a value given that the baseline does not have, an empty
  baseline): "nothing changed" only when everything of the baseline was compared. A ticket revision is not the whole
  story (children, comments): --check names the /analyze-ticket check whose conclusion is part of the answer. With
  --reopen, the steps to review that were done go back to pending (the history keeps what they were).
  With a plan recorded by `baseline --spec-plan FILE` (its `spec_refs`, scripts/spec_refs.py), check also compares the
  specification's rules: a scenario whose rule changed or was removed is named and its plan step is to review; a
  rule that could not be verified leaves its scenarios pending review (exit 3). The same text in a new revision of
  the file changes nothing. Without `specs:` (and no --spec-root) nothing is compared; a plan without `spec_refs`
  says so. --spec-root reads the rules from another export folder than the project's.
- published: records that a bug draft (`<batch>/<case>`) was published with a tracker key, with --confirmed-by: what
  shows the tracker has it (a check id that read it back, or the record in tracker/sent/). The same draft with another
  key is refused (a bug is never published twice), and so is a key already recorded for another draft.
- mark: `blocked` needs --note (the reason, and who unblocks it).
- A PBI given as a link must be on a host of `tickets.sources` in project.yaml; any other host is refused.

Exit codes: 0 done (check: nothing changed) · 1 check: something changed / published: refused · 2 usage or a record
that cannot be read · 3 check: not concluded (something could not be compared; nothing is said to be unchanged) · 5
another process is writing the workspace (nothing was written)
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

from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    same_build,
    workspace_lock,
    workspace_path,
)

STEPS = ("context", "analyze", "plan", "run", "bugs", "report", "checkpoint")
STATUSES = ("pending", "done", "blocked", "skipped")
PLATFORMS = ("android", "ios", "web", "api")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class UsageError(Exception):
    """Usage, or a record that cannot be read (code 2)."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hosts(workspace: str | None) -> set[str]:
    """The hosts of the ticket sources of project.yaml (organization_url, site, workspace_url, base_url, url)."""
    from urllib.parse import urlsplit
    if not workspace:
        return set()
    try:
        from common import read_project
        sources = ((read_project(workspace).get("tickets") or {}).get("sources") or {})
    except (OSError, ValueError, AttributeError):
        return set()
    out = set()
    for s in sources.values() if isinstance(sources, dict) else []:
        for key in ("organization_url", "site", "workspace_url", "base_url", "url"):
            host = urlsplit(str((s or {}).get(key) or "")).hostname
            if host:
                out.add(host.lower())
    return out


def pbi_id(text: str, workspace: str | None = None) -> str:
    """The id of a PBI given as an id or as a link (`.../_workitems/edit/<id>`, `.../browse/<KEY>`, Linear's
    `.../issue/<KEY>/<slug>`, whose key is upper case like the memory of /analyze-ticket). A link must be on a
    host of the project's ticket sources (audit 06.11.2026: any host was accepted)."""
    from urllib.parse import urlsplit
    text = str(text).strip().rstrip("/")
    if "://" in text:
        host = (urlsplit(text).hostname or "").lower()
        allowed = _hosts(workspace)
        if host not in allowed:
            raise UsageError(f"the link's host {host or '?'} is not a ticket source of this project "
                             f"({', '.join(sorted(allowed)) or 'tickets.sources has none'}): give the id, or the link "
                             "of the configured tracker")
    m = re.search(r"/(?:_workitems/edit|browse)/([A-Za-z0-9._-]+)", text)
    linear = re.search(r"/issue/([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:/|$)", text) if not m else None
    value = m.group(1) if m else linear.group(1).upper() if linear else text
    if not ID.fullmatch(value):
        raise UsageError(f"not a PBI id or link: {text!r} (expected e.g. 1234, DS-12, .../_workitems/edit/1234 or .../issue/DS-12/<slug>)")
    return value


def step_names(platforms: list[str]) -> list[str]:
    out = []
    for s in STEPS:
        out += [f"run:{p}" for p in platforms] if s == "run" else [s]
    return out


def path_of(workspace: str, pbi: str) -> str:
    return workspace_path(workspace, "memory", "pbi", f"{pbi}.json")


def load(workspace: str, pbi: str) -> dict | None:
    path = path_of(workspace, pbi)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise UsageError(f"{path} cannot be read ({e}): fix it by hand or move it away; nothing was changed") from e
    if not isinstance(doc, dict) or doc.get("pbi") != pbi:
        raise UsageError(f"{path} is not the record of PBI {pbi}")
    return doc


def save(workspace: str, doc: dict) -> None:
    path = path_of(workspace, doc["pbi"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc["updated"] = now()
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1))


def _event(doc: dict, text: str) -> None:
    doc.setdefault("history", []).append({"at": now(), "event": text})


def _platforms(values) -> list[str]:
    out = []
    for v in values or []:
        for p in str(v).split(","):
            p = p.strip().lower()
            if not p:
                continue
            if p not in PLATFORMS:
                raise UsageError(f"unknown platform {p!r}: one of {', '.join(PLATFORMS)}")
            if p not in out:
                out.append(p)
    return out


def _pairs(values, what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise UsageError(f"{what}: expected KEY=VALUE, got {v!r}")
        k, val = v.split("=", 1)
        if not k.strip() or not val.strip():
            raise UsageError(f"{what}: expected KEY=VALUE, got {v!r}")
        out[k.strip()] = val.strip()
    return out


def next_step(doc: dict) -> str | None:
    for name in step_names(doc["platforms"]):
        if doc["steps"].get(name, {}).get("status", "pending") == "pending":
            return name
    return None


def start(workspace: str, pbi: str, task: str | None, platforms: list[str]) -> tuple[dict, bool]:
    with workspace_lock(workspace):
        doc = load(workspace, pbi)
        created = doc is None
        if created:
            doc = {"pbi": pbi, "task": task, "platforms": [], "created": now(), "steps": {}, "baseline": {},
                   "published": {}, "history": []}
            _event(doc, "started")
        if task and doc.get("task") and doc["task"] != task:
            raise UsageError(f"PBI {pbi} is recorded with the QA task {doc['task']}, not {task}")
        doc["task"] = doc.get("task") or task
        added = [p for p in platforms if p not in doc["platforms"]]
        doc["platforms"] += added
        if added and not created:
            _event(doc, f"platforms added: {', '.join(added)}")
        for name in step_names(doc["platforms"]):
            doc["steps"].setdefault(name, {"status": "pending"})
        if created or added:
            save(workspace, doc)
    return doc, created


def mark(workspace: str, pbi: str, step: str, status: str, ref: str | None, note: str | None) -> tuple[dict, str]:
    if status not in STATUSES:
        raise UsageError(f"unknown status {status!r}: one of {', '.join(STATUSES)}")
    if status == "done" and not (ref or "").strip():
        raise UsageError("done needs --ref: the file, check id or batch that proves the step was done")
    if status == "blocked" and not (note or "").strip():
        raise UsageError("blocked needs --note: the reason, and who or what unblocks it")
    with workspace_lock(workspace):
        doc = load(workspace, pbi)
        if doc is None:
            raise UsageError(f"no record of PBI {pbi}: run start first")
        if step not in step_names(doc["platforms"]):
            raise UsageError(f"unknown step {step!r} for this PBI: one of {', '.join(step_names(doc['platforms']))}"
                             " (a run of another platform: start with --platform first)")
        old = doc["steps"].get(step, {"status": "pending"})
        if old.get("status") == status and old.get("ref") == ref and (note is None or old.get("note") == note):
            return doc, f"{step} already {status}" + (f" ({ref})" if ref else "") + ": nothing changed"
        entry = {"status": status, "at": now()}
        if ref:
            entry["ref"] = ref
        if note:
            entry["note"] = note
        doc["steps"][step] = entry
        _event(doc, f"{step}: {old.get('status', 'pending')}{' (' + old['ref'] + ')' if old.get('ref') else ''} -> "
                    f"{status}{' (' + ref + ')' if ref else ''}{': ' + note if note else ''}")
        save(workspace, doc)
    return doc, f"{step}: {status}"


def set_baseline(workspace: str, pbi: str, tickets: dict, builds: dict, environment: str | None,
                 spec_plan: str | None = None) -> dict:
    with workspace_lock(workspace):
        doc = load(workspace, pbi)
        if doc is None:
            raise UsageError(f"no record of PBI {pbi}: run start first")
        base = doc.setdefault("baseline", {})
        base.setdefault("tickets", {}).update(tickets)
        base.setdefault("builds", {}).update(builds)
        if environment:
            base["environment"] = environment
        if spec_plan:
            base["spec_plan"] = spec_plan
        base["at"] = now()
        _event(doc, f"baseline: tickets {tickets or '-'}, builds {builds or '-'}, environment {environment or '-'}"
                    + (f", spec plan {spec_plan}" if spec_plan else ""))
        save(workspace, doc)
    return doc


def not_compared(doc: dict, tickets: dict, builds: dict, environment: str | None) -> list[str]:
    """What check could not compare: so "nothing changed" is never said of something not looked at."""
    base = doc.get("baseline") or {}
    out = []
    bt, bb = base.get("tickets") or {}, base.get("builds") or {}
    if not bt:
        out.append("the baseline has no ticket revision (record it with baseline --ticket ID=REV)")
    if not bb:
        out.append("the baseline has no build (record it with baseline --build PLATFORM=BUILD)")
    out += [f"ticket {k}: in the baseline, not given now" for k in bt if k not in tickets]
    out += [f"ticket {k}: given now, not in the baseline" for k in tickets if k not in bt]
    out += [f"build {k}: in the baseline, not given now" for k in bb if k not in builds]
    out += [f"build {k}: given now, not in the baseline" for k in builds if k not in bb]
    if base.get("environment") and not environment:
        out.append("environment: in the baseline, not given now")
    return out


def reopen(workspace: str, pbi: str, steps: list[str], why: str) -> list[str]:
    """Puts the steps to review that were done back to pending, with the reason in the history."""
    done = []
    with workspace_lock(workspace):
        doc = load(workspace, pbi)
        for step in steps:
            old = doc["steps"].get(step)
            if old and old.get("status") == "done":
                doc["steps"][step] = {"status": "pending", "at": now(), "note": f"to review: {why}"}
                _event(doc, f"{step}: done ({old.get('ref', '-')}) -> pending: to review, {why}")
                done.append(step)
        if done:
            save(workspace, doc)
    return done


def compare(doc: dict, tickets: dict, builds: dict, environment: str | None) -> list[dict]:
    """What changed since the baseline, and which steps to review. Read only."""
    base = doc.get("baseline") or {}
    changes = []
    for key, rev in tickets.items():
        before = (base.get("tickets") or {}).get(key)
        if before is not None and before != rev:
            changes.append({"what": f"ticket {key}", "before": before, "now": rev,
                            "review": ["analyze", "plan"] + [f"run:{p}" for p in doc["platforms"]] + ["report"],
                            "why": "the requirement may have changed: the plan and the results may no longer match it"})
    for platform, build in builds.items():
        before = (base.get("builds") or {}).get(platform)
        if before is not None and not same_build(before, build):
            changes.append({"what": f"build {platform}", "before": before, "now": build,
                            "review": [f"run:{platform}", "report"],
                            "why": f"results of {before} stay valid for {before}; a new run on {build} is a new batch"})
    before_env = base.get("environment")
    if environment and before_env and environment != before_env:
        changes.append({"what": "environment", "before": before_env, "now": environment,
                        "review": [f"run:{p}" for p in doc["platforms"]] + ["report"],
                        "why": "results of another environment are not comparable"})
    return changes


def spec_check(workspace: str, doc: dict, root: str | None) -> dict:
    """The plan's `spec_refs` against the specification available now (scripts/spec_refs.py). Read only.
    {"changes": [...], "missing": [...], "notes": [...], "lines": [...], "result": {...}|None}: nothing is compared
    (and nothing printed) when the project has no `specs:`, no --spec-root and no plan with references."""
    import spec_refs
    out = {"changes": [], "missing": [], "notes": [], "lines": [], "result": None}
    plan_rel = (doc.get("baseline") or {}).get("spec_plan")
    source = spec_refs.source_from(workspace, root)
    if not plan_rel:
        if source is not None:
            out["notes"].append("spec: the baseline records no plan with spec_refs (baseline --spec-plan FILE): "
                                "nothing to compare")
        return out
    try:
        plan = spec_refs.plan_refs(workspace_path(workspace, plan_rel))
    except (spec_refs.SpecError, OutsideWorkspace) as e:
        out["missing"].append(f"spec: the plan {plan_rel} cannot be read for its spec_refs ({e})")
        return out
    if plan is None:
        out["notes"].append(f"spec: the plan {plan_rel} has no spec_refs: no references to compare")
        return out
    if source is None:
        out["missing"].append(f"spec: the plan {plan_rel} cites the specification but the project has no specs: "
                              "section (and no --spec-root): its rules were not compared")
        return out
    result = spec_refs.compare(plan, source)
    out["result"], out["lines"] = result, spec_refs.render(result)
    runs = [f"run:{p}" for p in doc["platforms"]]
    unverified = []
    for r in result["rules"]:
        who = ", ".join(r["scenarios"])
        if r["status"] in ("changed", "removed", "moved"):
            out["changes"].append({"what": f"spec rule {r['rule_id']} ({r['path']})",
                                   "before": r["old_revision"] or "?", "now": r["new_revision"] or r["status"],
                                   "review": ["plan"] + runs + ["report"],
                                   "why": f"{r['reason']}; scenarios to review before the plan is reused: {who}"})
        elif r["status"] == "unverified":
            unverified.append(r)
    if unverified:
        pending = sorted({s for r in unverified for s in r["scenarios"]})
        why = (result["source"]["reason"] if not result["source"]["available"]
               else "; ".join(sorted({f"{r['rule_id']}: {r['reason']}" for r in unverified})))
        out["missing"].append(f"spec: {len(unverified)} referenced rule(s) could not be verified ({why}); scenarios "
                              f"pending review: {', '.join(pending)}")
    return out


def publish(workspace: str, pbi: str, draft: str, key: str, confirmed_by: str | None = None) -> tuple[dict, str, bool]:
    draft, key, confirmed_by = str(draft or "").strip(), str(key or "").strip(), str(confirmed_by or "").strip()
    if not draft or not key:
        raise UsageError("published needs a draft id (<batch>/<case>) and the tracker's key, both not empty")
    if not confirmed_by:
        raise UsageError("published needs --confirmed-by: what shows the tracker has it (a check id that read it back, "
                         "or tracker/sent/<batch>.json); a bug is not recorded as published on the word of a step")
    with workspace_lock(workspace):
        doc = load(workspace, pbi)
        if doc is None:
            raise UsageError(f"no record of PBI {pbi}: run start first")
        done = doc.setdefault("published", {}).get(draft)
        if done and done.get("key") == key:
            return doc, f"{draft} already recorded as published ({key}): nothing changed", True
        if done:
            return doc, (f"REFUSED: {draft} was already published as {done['key']} on {done['at']}: never publish a bug "
                         "twice; update the existing one instead"), False
        other = next((d for d, p in doc["published"].items() if p.get("key") == key), None)
        if other:
            return doc, (f"REFUSED: {key} is already recorded for {other}: two drafts are one bug only when the review "
                         "merged them; record the merge in the run log, not here"), False
        doc["published"][draft] = {"key": key, "at": now(), "confirmed_by": confirmed_by}
        _event(doc, f"bug {draft} published as {key}")
        save(workspace, doc)
    return doc, f"{draft} recorded as published: {key}", True


def _engine_conclusion(workspace: str, check: str) -> str:
    """The conclusion of an /analyze-ticket check record (memory/checks/<id>.json), or why it cannot be read."""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}(-[0-9]+)?", check or ""):
        raise UsageError(f"--check: expected a check id like 20261106-214613, got {check!r}")
    path = os.path.join(workspace, "memory", "checks", f"{check}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("conclusion") or "unknown")
    except (OSError, ValueError) as e:
        raise UsageError(f"--check {check}: {path} cannot be read ({e})") from None


def render(doc: dict) -> str:
    lines = [f"PBI {doc['pbi']}" + (f" · QA task {doc['task']}" if doc.get("task") else "")
             + f" · platforms: {', '.join(doc['platforms']) or 'none yet'}"]
    blocked = []
    for name in step_names(doc["platforms"]):
        s = doc["steps"].get(name, {"status": "pending"})
        extra = "".join(f" · {k}: {s[k]}" for k in ("ref", "note", "at") if s.get(k))
        lines.append(f"  {s['status']:8} {name}{extra}")
        if s["status"] == "blocked":
            blocked.append(name)
    base = doc.get("baseline") or {}
    if base:
        lines.append(f"Baseline ({base.get('at', '?')}): tickets {base.get('tickets') or {}}, builds "
                     f"{base.get('builds') or {}}, environment {base.get('environment') or '-'}"
                     + (f", spec plan {base['spec_plan']}" if base.get("spec_plan") else ""))
    for draft, p in (doc.get("published") or {}).items():
        lines.append(f"Published: {draft} -> {p['key']} ({p['at']})")
    nxt = next_step(doc)
    lines.append(f"Next: {nxt}" if nxt else "Next: nothing pending")
    if blocked:
        lines.append(f"Blocked (go back to them when unblocked): {', '.join(blocked)}")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/pbi_state.py", description="The progress of one PBI (/test-pbi).")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("pbi")
    p.add_argument("--task")
    p.add_argument("--platform", action="append")
    p = sub.add_parser("show")
    p.add_argument("pbi")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("mark")
    p.add_argument("pbi")
    p.add_argument("step")
    p.add_argument("status")
    p.add_argument("--ref")
    p.add_argument("--note")
    for name in ("baseline", "check"):
        p = sub.add_parser(name)
        p.add_argument("pbi")
        p.add_argument("--ticket", action="append", help="ID=REVISION of a ticket the plan rests on")
        p.add_argument("--build", action="append", help="PLATFORM=BUILD (observed)")
        p.add_argument("--environment")
        p.add_argument("--json", action="store_true")
        if name == "baseline":
            p.add_argument("--spec-plan", help="the plan (a file of the workspace) whose spec_refs a resume compares")
        if name == "check":
            p.add_argument("--spec-root", help="read the specification's rules from this folder instead of the "
                                               "project's specs.location (a local export)")
            p.add_argument("--check", help="the /analyze-ticket check id read for this check (its conclusion counts)")
            p.add_argument("--reopen", action="store_true", help="put the steps to review back to pending")
    p = sub.add_parser("published")
    p.add_argument("pbi")
    p.add_argument("draft", help="<batch>/<case> of the bug proposal")
    p.add_argument("key")
    p.add_argument("--confirmed-by", help="a check id that read the bug back, or tracker/sent/<batch>.json")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        if not os.path.isfile(os.path.join(ws, "project.yaml")):
            raise UsageError(f"{ws} has no project.yaml: not a workspace")
        pbi = pbi_id(a.pbi, ws)
        if a.command == "start":
            doc, created = start(ws, pbi, a.task, _platforms(a.platform))
            print(("created" if created else "resumed") + f": {path_of(ws, pbi)}")
            print(render(doc))
            return 0
        if a.command == "show":
            doc = load(ws, pbi)
            if doc is None:
                raise UsageError(f"no record of PBI {pbi} (memory/pbi/{pbi}.json): run start first")
            print(json.dumps(doc, ensure_ascii=False, indent=1) if a.json else render(doc))
            return 0
        if a.command == "mark":
            doc, text = mark(ws, pbi, a.step, a.status, a.ref, a.note)
            print(text)
            nxt = next_step(doc)
            print(f"Next: {nxt}" if nxt else "Next: nothing pending")
            return 0
        if a.command == "published":
            _, text, ok = publish(ws, pbi, a.draft, a.key, a.confirmed_by)
            print(text, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        tickets, builds = _pairs(a.ticket, "--ticket"), _pairs(a.build, "--build")
        builds = {k.lower(): v for k, v in builds.items()}
        if a.command == "baseline":
            spec_plan = None
            if a.spec_plan:
                full = workspace_path(ws, a.spec_plan)
                if not os.path.isfile(full):
                    raise UsageError(f"--spec-plan {a.spec_plan}: not a file of the workspace")
                spec_plan = os.path.relpath(full, ws).replace(os.sep, "/")
            doc = set_baseline(ws, pbi, tickets, builds, a.environment, spec_plan)
            print(render(doc))
            return 0
        doc = load(ws, pbi)
        if doc is None:
            raise UsageError(f"no record of PBI {pbi}: run start first")
        changes = compare(doc, tickets, builds, a.environment)
        missing = not_compared(doc, tickets, builds, a.environment)
        spec = spec_check(ws, doc, a.spec_root)
        changes += spec["changes"]
        missing += spec["missing"]
        engine = _engine_conclusion(ws, a.check) if a.check else None
        if engine and engine not in ("no-changes", "first-and-no-changes"):
            missing.append(f"the ticket check {a.check} concluded {engine!r}: read its changes (children, comments and "
                           "attachments do not change a revision)")
        reopened = []
        if a.reopen and changes:
            reopened = reopen(ws, pbi, sorted({s for c in changes for s in c["review"]}),
                              "; ".join(f"{c['what']} {c['before']} -> {c['now']}" for c in changes))
        if a.json:
            print(json.dumps({"changes": changes, "not_compared": missing, "reopened": reopened,
                              "spec": spec["result"], "spec_notes": spec["notes"]}, ensure_ascii=False, indent=1))
        else:
            for c in changes:
                print(f"CHANGED {c['what']}: {c['before']} -> {c['now']}; review {', '.join(c['review'])} ({c['why']})")
            for line in spec["lines"]:
                print(f"SPEC {line}")
            for n in spec["notes"]:
                print(f"NOTE: {n}")
            for m in missing:
                print(f"NOT COMPARED: {m}")
            if reopened:
                print(f"reopened (pending again): {', '.join(reopened)}")
            elif changes:
                print("the steps stay as they are until reviewed: --reopen puts them back to pending")
            if not changes and not missing:
                print("nothing changed since the baseline (everything recorded in it was compared)")
            elif not changes:
                print("NOT CONCLUDED: no change found in what was compared, but not everything could be compared")
        return 1 if changes else (3 if missing else 0)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except OutsideWorkspace as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except WorkspaceBusy as e:
        print(f"BUSY: {e}: nothing was written; run the same command again", file=sys.stderr)
        return 5


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
