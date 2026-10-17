"""
Batch plan (docs/qa-ui.md section 6; methodology 6): the cases of the ticked cards grouped per module, area and
role, at most 20 per batch, and saved as batches/<name>.yaml in the format /plan-batch writes.

    build(workspace, cases, assignments, cards, today) -> {"batches": [...], "apart": [...], "selected": n, ...}
    save(workspace, plan)                              -> the paths written

The plan never reduces coverage: the cases planned plus those listed apart equal the cases selected, or it is
refused (PlanError). Nothing is written before save().
"""

from __future__ import annotations

import os
import re
from datetime import date

import yaml

from . import MODULES
from .suggest import skill_for

MAX_CASES = 20                                    # a checkpoint every 15-20 cases (methodology 7.2)
SHORT = {"functional-web": "web", "exploratory": "exp", "api": "api", "accessibility": "a11y", "mobile": "mobile"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# the IDs the workspace's CI accepts in a planned batch (scripts/workspace_ci.py, CASE)
CI_CASE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+")


class PlanError(ValueError):
    """The plan is refused (it does not add up, or a name was taken meanwhile)."""


def area_of(case: dict) -> str:
    """The case's area, else its ID up to the last '-' (TC-LOGIN-001 -> TC-LOGIN), else the ID."""
    if case.get("area"):
        return str(case["area"])
    stem, sep, _ = str(case["id"]).rpartition("-")
    return stem if sep and stem else str(case["id"])


def taken(workspace: str, name: str) -> bool:
    return (os.path.exists(os.path.join(workspace, "batches", f"{name}.yaml"))
            or os.path.exists(os.path.join(workspace, "runs", f"batch_{name}.json")))


def free_name(workspace: str, module: str, today: date, used: set[str]) -> str:
    """<YYYYMMDD>_<module>_<nn>: the first <nn> free in batches/ and runs/ and not used by this plan."""
    n = 0
    while True:
        n += 1
        name = f"{today:%Y%m%d}_{SHORT[module]}_{n:02d}"
        if name not in used and not taken(workspace, name):
            used.add(name)
            return name


def build(workspace: str, cases: list[dict], assignments: dict, cards: list[str], today: date | None = None,
          proj: dict | None = None) -> dict:
    """The batches of the ticked module cards. `assignments` is {case ID: {"module": ...}} (suggest.suggest) and
    `cards` the keys ticked; the automated audits need no batch and are ignored here. `proj` gives each batch's
    skill (suggest.skill_for: exploratory charters of a mobile project run with /test-mobile)."""
    today = today or date.today()
    ticked = [m for m in MODULES if m in set(cards)]
    selected = [c for c in cases if assignments.get(c["id"], {}).get("module") in ticked]
    apart, groups = [], {}
    for case in selected:
        if case.get("in_library") is False:
            apart.append({"id": case["id"], "title": case.get("title", ""),
                          "reason": "not in the library: add it first (docs/qa-ui.md section 7)"})
            continue
        key = (assignments[case["id"]]["module"], area_of(case), str(case.get("role") or ""))
        groups.setdefault(key, []).append(case)
    batches, used = [], set()
    # per module in the methodology's order; inside a module, areas and roles in the order they first appear
    for module in ticked:
        for (m, area, role), group in groups.items():
            if m != module:
                continue
            for start in range(0, len(group), MAX_CASES):
                chunk = group[start:start + MAX_CASES]
                batches.append({"name": free_name(workspace, module, today, used), "module": module,
                                "skill": skill_for(module, proj or {}), "area": area, "role": role or None,
                                "cases": [c["id"] for c in chunk],
                                "titles": {c["id"]: c.get("title", "") for c in chunk},
                                "questions": [c["id"] for c in chunk
                                              if {"no-expected", "vague-expected"} & set(c.get("flags") or [])]})
    planned = sum(len(b["cases"]) for b in batches)
    if planned + len(apart) != len(selected):
        raise PlanError(f"the plan does not add up: {len(selected)} case(s) selected, {planned} planned and "
                        f"{len(apart)} listed apart")
    warnings = []
    if any(c.get("in_library") is None for c in selected):
        warnings.append("the library could not be read: whether these cases are in it is not known, and the "
                        "adapter will refuse the results of a case that is not")
    odd = [i for b in batches for i in b["cases"] if not CI_CASE.fullmatch(i)]
    if odd:
        warnings.append(f"{len(odd)} case ID(s) (e.g. {odd[0]}) are not in the form the workspace's CI accepts in a "
                        "planned batch (scripts/workspace_ci.py): these batches run here, not remotely")
    return {"batches": batches, "apart": apart, "selected": len(selected), "planned": planned, "warnings": warnings}


def save(workspace: str, plan: dict) -> list[str]:
    """Writes each batch as batches/<name>.yaml: cases (what /plan-batch writes), module and, when it has one,
    role. Refused whole when a name is not valid or was taken since the plan was built."""
    for b in plan["batches"]:
        if not NAME.fullmatch(str(b["name"])):
            raise PlanError(f"batch name {b['name']!r}: letters, digits, '.', '_', '-' only")
        if taken(workspace, b["name"]):
            raise PlanError(f"batch {b['name']} exists since the plan was built: plan again")
        if not b["cases"]:
            raise PlanError(f"batch {b['name']} has no case")
    os.makedirs(os.path.join(workspace, "batches"), exist_ok=True)
    paths = []
    for b in plan["batches"]:
        doc = {"cases": list(b["cases"]), "module": b["module"]}
        if b.get("role"):
            doc["role"] = b["role"]
        path = os.path.join(workspace, "batches", f"{b['name']}.yaml")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, default_flow_style=None)
        os.replace(tmp, path)
        paths.append(path)
    return paths
