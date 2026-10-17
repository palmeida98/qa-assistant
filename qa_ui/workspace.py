"""
The workspace as the interface sees it (docs/qa-ui.md sections 9 to 11): the project, what this computer has, the
planned batches, the run logs and what needs the user, and the user's edits of a run log.

Nothing is kept here that the workspace does not have: every call reads the files again. The edits are the same
ones the methodology gives a pull request of a remote run (9.4), written whole and atomically with
scripts/run_log.py, and refused for a batch that was already applied.
"""

from __future__ import annotations

import glob
import os
import shutil
from collections import Counter
from datetime import datetime

import run_log
import yaml
from common import read_env, read_project, variable_names

DEFECT_ACTIONS = ("approve", "edit", "link", "drop")


class Refused(Exception):
    """The edit is refused (the batch was applied, or the item is not in the state the action needs)."""


class NotFound(Exception):
    """The run log, the case or the batch does not exist."""


def project(workspace: str) -> dict:
    return read_project(workspace)


def is_question(status) -> bool:
    return str(status or "").lower().startswith("question")


def is_proposed(item: dict) -> bool:
    new = item.get("new_defect")
    return isinstance(new, dict) and new.get("proposed", False) is not False


def readiness(workspace: str, proj: dict) -> list[tuple[bool, str, str]]:
    """The checks of `validate_project.py --environment` (never a second list), plus the qa-audit command when
    the accessibility module is not active (the automated site audit needs it either way)."""
    import validate_project
    checks = list(validate_project.environment(workspace, proj))
    if not any(tool == "qa-audit" for _, tool, _ in checks):
        exe = shutil.which("qa-audit")
        checks.append((bool(exe), "qa-audit", exe or "pip install -e . (from the root of qa-assistant), then "
                                                     "qa-audit doctor"))
    return checks


def env_names(workspace: str, proj: dict) -> list[dict]:
    """The names of the .env variables project.yaml refers to and whether each is set. Never a value."""
    env = read_env(os.path.join(workspace, ".env"))
    return [{"name": n, "set": bool(env.get(n))} for n in variable_names(proj)]


def batches(workspace: str) -> list[dict]:
    """The planned batches (batches/<name>.yaml), newest name first."""
    out = []
    for path in sorted(glob.glob(os.path.join(workspace, "batches", "*.yaml")), reverse=True):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8-sig") as f:
                doc = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            doc = {}
        cases = doc.get("cases") if isinstance(doc, dict) else None
        ok = isinstance(cases, list) and bool(cases) and all(isinstance(c, str) for c in cases)
        out.append({"name": name, "cases": list(cases) if ok else [], "valid": ok,
                    "module": doc.get("module") if isinstance(doc, dict) else None,
                    "role": doc.get("role") if isinstance(doc, dict) else None,
                    "run_log": os.path.isfile(run_log.run_log_path(workspace, name)) if run_log.NAME.fullmatch(name)
                    else False})
    return out


def batch(workspace: str, name: str) -> dict:
    found = next((b for b in batches(workspace) if b["name"] == name), None)
    if not found:
        raise NotFound(f"batches/{name}.yaml does not exist")
    if not found["valid"]:
        raise Refused(f"batches/{name}.yaml: expected cases: [<case ID>, ...]")
    return found


def set_batch_module(workspace: str, proj: dict, name: str, module: str) -> dict:
    """Adds `module: <module>` to a planned batch that has none (one line at the end: the file's comments and order
    are kept). Only an active module of project.yaml; a batch that names one already is changed in its file."""
    from . import MODULES
    found = batch(workspace, name)
    if found["module"]:
        raise Refused(f"batches/{name}.yaml already has module: {found['module']} (change it in the file)")
    if module not in MODULES:
        raise Refused(f"{module!r} is not a module of the list {list(MODULES)}")
    if module not in (proj.get("modules") or []):
        raise Refused(f"`{module}` is not in `modules:` of project.yaml (methodology 4.1)")
    path = os.path.join(workspace, "batches", f"{name}.yaml")
    with open(path, "rb") as f:
        raw = f.read()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    tail = b"" if not raw or raw.endswith(b"\n") else nl
    with open(path, "ab") as f:
        f.write(tail + f"module: {module}".encode() + nl)
    return batch(workspace, name)


def describe(doc: dict, vocabulary: list[str]) -> dict:
    """What the page shows of a run log: the items, the count per status and what needs the user."""
    items, needs = [], []
    counts = Counter()
    for x in doc.get("results") or []:
        if not isinstance(x, dict):
            continue
        status = str(x.get("status") or "")
        question, proposed = is_question(status), is_proposed(x)
        counts["Question" if question else status] += 1
        new = x.get("new_defect") if isinstance(x.get("new_defect"), dict) else None
        item = {"tc": str(x.get("tc") or ""), "status": status, "actual_result": x.get("actual_result") or "",
                "ts": x.get("ts"), "bug": x.get("bug"), "new_defect": new, "question": question,
                "proposed": proposed, "evidence": x.get("evidence")}
        items.append(item)
        if proposed:
            needs.append({"kind": "defect", "tc": item["tc"], "new_defect": new,
                          "actual_result": item["actual_result"]})
        if question:
            needs.append({"kind": "question", "tc": item["tc"], "text": item["actual_result"]})
    order = [s for s in vocabulary if counts.get(s)] + [s for s in counts if s not in vocabulary]
    return {"run": doc.get("run") or {}, "items": items, "needs": needs,
            "counts": [{"status": s, "n": counts[s]} for s in order], "total": len(items)}


def run_logs(workspace: str, proj: dict) -> list[dict]:
    """Every run log of runs/ (batch_*.json), newest first, with its counts and what needs the user."""
    vocabulary = run_log.vocabulary(proj)
    out = []
    for path in glob.glob(os.path.join(workspace, "runs", "batch_*.json")):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            d = describe(run_log.read_log(path), vocabulary)
            error = None
        except run_log.UsageError as e:
            d, error = {"counts": [], "needs": [], "total": 0}, str(e)
        out.append({"name": name, "batch": name.removeprefix("batch_"), "counts": d["counts"], "total": d["total"],
                    "needs": len(d["needs"]), "error": error,
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")})
    return sorted(out, key=lambda x: x["modified"], reverse=True)


def read_run_log(workspace: str, proj: dict, name: str, with_applied: bool = True) -> dict:
    try:
        path = run_log.run_log_path(workspace, name)
    except run_log.UsageError as e:
        raise NotFound(str(e)) from None
    if not os.path.isfile(path):
        raise NotFound(f"runs/{os.path.basename(path)} does not exist")
    try:
        doc = run_log.read_log(path)
    except run_log.UsageError as e:
        raise Refused(str(e)) from None
    out = describe(doc, run_log.vocabulary(proj))
    out["name"] = run_log.batch_id(name)
    out["statuses"] = run_log.vocabulary(proj)
    if with_applied:
        out["applied"] = run_log.applied(workspace, proj, out["name"])
    return out


def create_run_log(workspace: str, proj: dict, name: str, environment: str | None, build: str | None) -> str:
    """The run log of a batch about to run, with the run block (methodology 7.1). An existing one is kept (a
    batch run again continues it) unless it was applied."""
    try:
        batch_id = run_log.batch_id(name)
    except run_log.UsageError as e:
        raise Refused(str(e)) from None
    where = run_log.applied(workspace, proj, batch_id)
    if where:
        raise Refused(f"{batch_id} was already {where}: plan a new batch")
    path = run_log.run_log_path(workspace, name)
    try:
        doc = run_log.read_log(path)
    except run_log.UsageError as e:
        raise Refused(str(e)) from None
    run = dict(doc.get("run") or {})
    run.update({k: v for k, v in (("environment", environment), ("build", build)) if v})
    doc = {**{k: v for k, v in doc.items() if k not in ("run", "results")},
           **({"run": run} if run else {}), "results": doc["results"]}
    run_log.write_log(path, doc)
    return path


def edit_item(workspace: str, proj: dict, name: str, case: str, action: str, data: dict | None = None,
              now: datetime | None = None) -> dict:
    """One edit of one item of a run log (docs/qa-ui.md section 9); returns the run log as read_run_log."""
    data = data or {}
    try:
        batch_id = run_log.batch_id(name)
    except run_log.UsageError as e:
        raise NotFound(str(e)) from None
    path = run_log.run_log_path(workspace, name)
    if not os.path.isfile(path):
        raise NotFound(f"runs/{os.path.basename(path)} does not exist")
    where = run_log.applied(workspace, proj, batch_id)
    if where:
        raise Refused(f"{batch_id} was already {where}: it is never changed; a correction goes to a new batch")
    try:
        doc = run_log.read_log(path)
    except run_log.UsageError as e:
        raise Refused(str(e)) from None
    item = next((x for x in doc["results"] if isinstance(x, dict) and str(x.get("tc") or "").strip() == case), None)
    if item is None:
        raise NotFound(f"{case} is not in {batch_id}")

    if action in DEFECT_ACTIONS:
        new = item.get("new_defect")
        if not isinstance(new, dict):
            raise Refused(f"{case} has no new_defect")
        if action == "approve":
            if not is_proposed(item):
                raise Refused(f"the new_defect of {case} is not a proposal (it was already approved)")
            new.pop("proposed", None)
        elif action == "edit":
            fields = data.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise Refused("edit needs fields: {<key>: <text>}")
            for key, value in fields.items():
                if key == "proposed" or not isinstance(key, str) or not isinstance(value, str):
                    raise Refused(f"the field {key!r} cannot be edited here (text fields only; approve is its own "
                                  "action)")
                if value.strip():
                    new[key] = value.strip()
                else:
                    new.pop(key, None)
        elif action == "link":
            bug = str(data.get("bug") or "").strip()
            if not bug:
                raise Refused("link needs the ID of an existing bug")
            item["bug"] = bug
            del item["new_defect"]
        else:   # drop
            del item["new_defect"]
    elif action == "answer":
        if not is_question(item.get("status")):
            raise Refused(f"{case} is not in Question")
        status = str(data.get("status") or "").strip()
        actual = str(data.get("actual_result") or "").strip()
        vocabulary = run_log.vocabulary(proj)
        if is_question(status) or not status or (vocabulary and status not in vocabulary):
            raise Refused(f"status {status!r} is not in the project's vocabulary {vocabulary}")
        if not actual:
            raise Refused("an answer needs what was observed (actual_result)")
        if item.get("actual_result"):
            item["question"] = item["actual_result"]
        item.update(status=status, actual_result=actual,
                    ts=(now or datetime.now()).isoformat(timespec="seconds"))
    else:
        raise Refused(f"unknown action {action!r}: approve, edit, link, drop or answer")
    run_log.write_log(path, doc)
    return read_run_log(workspace, proj, name, with_applied=False)


def check_apply(workspace: str, proj: dict, name: str) -> dict:
    """Whether a run log can be applied (docs/qa-ui.md section 10): no Question, nothing proposed, and the
    adapter's own validation without problems (the check of scripts/workspace_ci.py post-check)."""
    import workspace_ci
    try:
        batch_id = run_log.batch_id(name)
    except run_log.UsageError as e:
        raise NotFound(str(e)) from None
    path = run_log.run_log_path(workspace, name)
    if not os.path.isfile(path):
        raise NotFound(f"runs/{os.path.basename(path)} does not exist")
    try:
        doc = run_log.read_log(path)
    except run_log.UsageError as e:
        raise Refused(str(e)) from None
    where = run_log.applied(workspace, proj, batch_id)
    problems = [f"{batch_id} was already {where}"] if where else []
    if not doc["results"]:
        problems.append("the run log has no result")
    problems += workspace_ci.refusals(doc)
    if not problems:
        problems += workspace_ci.adapter_problems(workspace, proj, doc)
    tracker = proj.get("tracker")
    new = sum(1 for x in doc["results"] if isinstance(x, dict) and isinstance(x.get("new_defect"), dict))
    linked = sum(1 for x in doc["results"] if isinstance(x, dict) and x.get("bug"))
    target = ((proj.get("excel") or {}).get("file") if tracker == "excel" else tracker) or "the tracker"
    return {"name": batch_id, "ready": not problems, "problems": problems, "tracker": tracker,
            "summary": {"results": len(doc["results"]), "new_defects": new, "linked_bugs": linked,
                        "target": str(target)}}


def state(workspace: str, proj: dict) -> dict:
    """GET /api/state, without the readiness checks and the run in progress (the server adds them)."""
    envs = {str(k): bool(v) for k, v in (proj.get("environments") or {}).items()}
    return {"project": {"name": str(proj.get("project") or ""), "company": str(proj.get("company") or ""),
                        "tracker": proj.get("tracker"), "modules": list(proj.get("modules") or []),
                        "environments": sorted(k for k, has in envs.items() if has),
                        "roles": [str(r) for r in proj.get("roles") or []],
                        "statuses": run_log.vocabulary(proj), "evidence": proj.get("evidence")},
            "env": env_names(workspace, proj),
            "batches": batches(workspace),
            "run_logs": run_logs(workspace, proj)}
