"""
The QA's decisions, recorded per workspace, and the check that a run is authorised for its platform
(docs/procedures/approvals.md; increment 2, first part: the `run` gate only).

    python scripts/approvals.py <workspace> record-run --pbi PBI --batch BATCH --platform P --environment E
                                                      --build BUILD --plan batches/<name>.yaml --declared-by WHO
                                                      --message-ref REF [--quote TEXT]
    python scripts/approvals.py <workspace> check --batch BATCH [--platform P] [--environment E] [--build B] [--json]
    python scripts/approvals.py <workspace> list [--pbi PBI] [--json]
    python scripts/approvals.py <workspace> revoke <id> --declared-by WHO --message-ref REF --reason TEXT
    python scripts/approvals.py <workspace> review-plan --batch BATCH --draft analysis/<name>.yaml [--pbi PBI]
                                                       [--build OBSERVED] [--out FILE] [--json]
    python scripts/approvals.py <workspace> approve-plan --pbi PBI --batch BATCH --draft analysis/<name>.yaml
                                                        --sha COVERAGE --declared-by WHO --message-ref REF
                                                        [--quote TEXT] [--review FILE] [--ticket ID=REV ...]
    python scripts/approvals.py <workspace> check-plan --batch BATCH [--platform P] [--environment E]
                                                      [--build OBSERVED] [--json]

The plan gate (increment 2B), opt-in and independent of the run gate:

    policy:
      approvals:
        plan: required           # required | with-request (the default: as before)

A plan decision approves the COVERAGE of one batch's plan (cases and order, excluded, retest, assumptions, notes,
platform, environment, PBI), by a canonical hash that leaves out formatting, comments, the old `approval:` block and
run.build; it authorises no run. It is never written into the plan. check-plan answers valid, missing, legacy (an old
`approval:` block without a decision), stale (with the difference, or a quarantine decision or a build assumption that
no longer holds in the effective context) or revoked.

The policy is opt-in, in project.yaml:

    policy:
      approvals:
        run: per-platform        # per-platform | with-request (the default: a request to test covers the run)

With `run: per-platform` the workspace must declare its identity (`workspace: {id, kind}`), and every result written
to a batch (`run_log.py add`) needs a valid run approval for the batch's platform, environment and build and for the
exact content of the plan recorded in its run log (its SHA-256). Without the option nothing changes.

The register is <workspace>/memory/approvals.jsonl: one JSON object per line, appended under the workspace's lock and
never rewritten. A revocation is a new line that names the decision it revokes; old decisions stay for consultation.

What a record is, and is not. A record says that the QA DECLARED a decision in the chat ("declared-in-chat"): the name
in `declared_by` and the record itself are declared information written by the assistant, not a signature. The
`message_ref` points at the QA's message (e.g. the session id and the time); this tool cannot open a conversation, so
the reference is recorded as NOT verified here (`message_ref_verified: false`). A command that succeeded never creates,
by itself, a record of a confirmation given at a permission prompt: that kind is not written by this tool at all.

check never writes. Its answer for a batch in its current context (platform, environment and build of the run log's
run block, else of the arguments, else of the plan's `run:`; and the plan recorded in the run log):
  valid    a decision of this workspace matches every field and was not revoked: reuse it, ask nothing again
  missing  no decision for this batch (or plan) in this workspace
  stale    decisions exist but none matches: the field that differs is named (build, environment, platform, plan,
           workspace); a new confirmation is needed
  revoked  the decision that matched was revoked
Exit codes: 0 done / valid · 1 refused, or missing / stale / revoked · 2 usage, or a register that cannot be read
(the operation subject to the policy is refused and the error shown) · 5 another process is writing the workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import workspace_identity
from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    read_project,
    same_build,
    workspace_lock,
    workspace_path,
)

GATE_RUN = "run"
GATE_PLAN = "plan"
RUN_VALUES = ("per-platform", "with-request")
PLAN_VALUES = ("required", "with-request")
HASH_METHOD = "plan-canonical-v1"
LEGACY = "legacy"
CONFIRMATION = "declared-in-chat"
LIMITATION = ("declared in the chat and recorded by the assistant: declared_by and this record are declared "
              "information, not a signature; the message reference was not verified by this tool")
VALID, MISSING, STALE, REVOKED = "valid", "missing", "stale", "revoked"


class UsageError(Exception):
    """Usage (code 2)."""


class Refused(Exception):
    """Refused (code 1): nothing was written."""


class LedgerError(Exception):
    """The register cannot be read (code 2): an operation subject to the policy is refused."""


# --- policy ---------------------------------------------------------------------------------------------------------

def policy_problems(proj: dict) -> list[str]:
    """What is wrong with `policy:` in project.yaml; [] when absent or right (validate_project)."""
    policy = proj.get("policy") if isinstance(proj, dict) else None
    if policy is None:
        return []
    if not isinstance(policy, dict) or set(policy) - {"approvals"}:
        return ["policy: expected {approvals: {run: per-platform|with-request}} (docs/procedures/approvals.md)"]
    approvals = policy.get("approvals")
    if approvals is None:
        return []
    if not isinstance(approvals, dict) or set(approvals) - {"run", "plan"}:
        return ["policy.approvals: only `run` and `plan` are known in this version (docs/procedures/approvals.md)"]
    out = []
    if "run" in approvals and approvals["run"] not in RUN_VALUES:
        out.append(f"policy.approvals.run must be one of {list(RUN_VALUES)}")
    if "plan" in approvals and approvals["plan"] not in PLAN_VALUES:
        out.append(f"policy.approvals.plan must be one of {list(PLAN_VALUES)}")
    if approvals.get("run") == "per-platform" and not workspace_identity.identity(proj):
        out.append("policy.approvals.run: per-platform needs the workspace's identity (workspace: {id, kind}; "
                   "technique-workspaces.md 4.1)")
    if approvals.get("plan") == "required" and not workspace_identity.identity(proj):
        out.append("policy.approvals.plan: required needs the workspace's identity (workspace: {id, kind}; "
                   "technique-workspaces.md 4.1)")
    return out


def run_per_platform(proj: dict) -> bool:
    policy = proj.get("policy") if isinstance(proj, dict) else None
    approvals = policy.get("approvals") if isinstance(policy, dict) else None
    return isinstance(approvals, dict) and approvals.get("run") == "per-platform"


def plan_required(proj: dict) -> bool:
    policy = proj.get("policy") if isinstance(proj, dict) else None
    approvals = policy.get("approvals") if isinstance(policy, dict) else None
    return isinstance(approvals, dict) and approvals.get("plan") == "required"


# --- the register ---------------------------------------------------------------------------------------------------

def ledger_path(workspace: str) -> str:
    return workspace_path(workspace, "memory", "approvals.jsonl")


def read(workspace: str) -> list[dict]:
    """Every line of the register, in order; [] without one. A line that is not a JSON object is a LedgerError."""
    path = ledger_path(workspace)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise LedgerError(f"{path} cannot be read ({e}): nothing was changed") from None
    out = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError as e:
            raise LedgerError(f"{path}, line {n}: not JSON ({e}); fix it by hand or move it away: nothing was "
                              "changed") from None
        if not isinstance(item, dict) or not item.get("id"):
            raise LedgerError(f"{path}, line {n}: not a record (an object with an id): nothing was changed")
        out.append(item)
    return out


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def _append(workspace: str, entry: dict) -> None:
    path = ledger_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = b""
    if os.path.exists(path):
        with open(path, "rb") as f:
            before = f.read()
    if before and not before.endswith(b"\n"):
        before += b"\n"
    atomic_write(path, before + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))


def _next_id(entries: list[dict]) -> str:
    numbers = [int(e["id"][3:]) for e in entries if str(e.get("id", "")).startswith("AP-") and e["id"][3:].isdigit()]
    return f"AP-{(max(numbers) + 1) if numbers else 1:04d}"


def plan_sha(workspace: str, plan: str) -> str:
    """SHA-256 of the plan file's bytes (a file of the workspace)."""
    try:
        full = workspace_path(workspace, *str(plan).replace("\\", "/").split("/"))
    except OutsideWorkspace as e:
        raise UsageError(str(e)) from None
    if not os.path.isfile(full):
        raise UsageError(f"the plan {plan} is not a file of the workspace")
    with open(full, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _plan_fields(workspace: str, plan: str | None) -> dict:
    """What the plan file declares about itself: `pbi` and its `run:` block (platform, environment, build)."""
    if not plan:
        return {}
    import run_log
    out = dict(run_log._plan_run(workspace, plan))
    try:
        import yaml
        with open(workspace_path(workspace, *plan.split("/")), encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and data.get("pbi"):
            out["pbi"] = str(data["pbi"]).strip()
    except (OSError, ValueError, ImportError, OutsideWorkspace):
        pass
    return out


def _identity_or_refuse(workspace: str) -> dict:
    ident = workspace_identity.read(workspace)
    if not ident:
        raise Refused(f"{workspace} declares no identity (project.yaml workspace: {{id, kind}}): a decision is "
                      "recorded only in a workspace with an explicit identity (technique-workspaces.md 4.1)")
    return ident


def record_run(workspace: str, pbi: str, batch: str, platform: str, environment: str, build: str, plan: str,
               declared_by: str, message_ref: str, quote: str | None = None, now: datetime | None = None) -> dict:
    """Appends the QA's declared authorisation to run `batch` on one platform, environment and build, for the exact
    content of `plan`. Every field is required; nothing is inferred."""
    for name, value in (("--pbi", pbi), ("--batch", batch), ("--platform", platform), ("--environment", environment),
                        ("--build", build), ("--plan", plan), ("--declared-by", declared_by),
                        ("--message-ref", message_ref)):
        if not str(value or "").strip():
            raise UsageError(f"{name} is required: a decision is recorded with every field, nothing is inferred")
    ident = _identity_or_refuse(workspace)
    rel = os.path.relpath(workspace_path(workspace, *plan.replace("\\", "/").split("/")), workspace).replace(os.sep, "/")
    sha = plan_sha(workspace, rel)
    import run_log
    name = run_log.batch_id(batch)
    recorded = run_log.read_log(run_log.run_log_path(workspace, name)).get("plan")
    own = recorded or run_log.own_plan(workspace, name)
    if own != rel:
        raise Refused(f"{rel} is not the plan of {name} (its plan: {own or 'none'}): a decision is recorded for the "
                      "batch's own plan, the one its run log follows: nothing was written")
    declared = {**_plan_fields(workspace, rel)}
    for key, given in (("pbi", str(pbi).strip()), ("platform", platform.strip().lower()),
                       ("environment", environment.strip())):
        if declared.get(key) and str(declared[key]).strip().lower() != given.lower():
            raise Refused(f"the plan {rel} declares {key} {declared[key]!r}, not {given!r}: the decision must match the "
                          "plan it authorises: nothing was written")
    with workspace_lock(workspace):
        entries = read(workspace)
        revoked = {e["revokes"] for e in entries if e.get("revokes")}
        for old in entries:
            if (old.get("gate") == GATE_RUN and old["id"] not in revoked and old.get("workspace") == ident
                    and old.get("pbi") == str(pbi).strip() and old.get("batch") == name
                    and old.get("platform") == platform.strip().lower()
                    and old.get("environment") == environment.strip() and same_build(old.get("build", ""), build)
                    and old.get("plan") == rel and old.get("plan_sha256") == sha):
                return {**old, "repeated": True}       # the same decision, still valid: nothing new is written
        entry = {"id": _next_id(entries), "gate": GATE_RUN, "workspace": ident, "pbi": str(pbi).strip(),
                 "batch": name, "platform": platform.strip().lower(),
                 "environment": environment.strip(), "build": build.strip(), "plan": rel, "plan_sha256": sha,
                 "declared_by": declared_by.strip(), "confirmation": CONFIRMATION,
                 "message_ref": message_ref.strip(), "message_ref_verified": False,
                 **({"quote": quote.strip()} if quote and quote.strip() else {}),
                 "limitation": LIMITATION, "recorded_by": "assistant", "recorded_at": _now(now)}
        _append(workspace, entry)
    return entry


def revoke(workspace: str, aid: str, declared_by: str, message_ref: str, reason: str,
           now: datetime | None = None) -> dict:
    """Appends a revocation of the decision `aid` (which stays in the register for consultation)."""
    for name, value in (("--declared-by", declared_by), ("--message-ref", message_ref), ("--reason", reason)):
        if not str(value or "").strip():
            raise UsageError(f"{name} is required")
    ident = _identity_or_refuse(workspace)
    with workspace_lock(workspace):
        entries = read(workspace)
        target = next((e for e in entries if e.get("id") == aid and e.get("gate")), None)
        if target is None:
            raise Refused(f"{aid}: no decision with that id in this register: nothing was written")
        if target.get("workspace") != ident:
            raise Refused(f"{aid} belongs to the workspace {workspace_identity.label(target.get('workspace'))}, not "
                          f"{workspace_identity.label(ident)}: nothing was written")
        if any(e.get("revokes") == aid for e in entries):
            raise Refused(f"{aid} is already revoked: nothing was written")
        entry = {"id": _next_id(entries), "revokes": aid, "workspace": ident, "reason": reason.strip(),
                 "declared_by": declared_by.strip(), "confirmation": CONFIRMATION, "message_ref": message_ref.strip(),
                 "message_ref_verified": False, "recorded_by": "assistant", "recorded_at": _now(now)}
        _append(workspace, entry)
    return entry


# --- the check ------------------------------------------------------------------------------------------------------

def context_of(workspace: str, proj: dict, batch: str, platform: str | None = None, environment: str | None = None,
               build: str | None = None, doc: dict | None = None) -> dict:
    """The batch's effective context: the run log's run block first (what was recorded), else the values given, else
    the plan's `run:`; the plan recorded in the run log (else the batch's own plan) and its SHA-256. The names under
    which a decision may have been recorded: the batch, and for a remote run the batch it came from."""
    import run_log
    name = run_log.batch_id(batch)
    doc = doc if doc is not None else run_log.read_log(run_log.run_log_path(workspace, name))
    run = doc.get("run") or {}
    plan = doc.get("plan") or run_log.own_plan(workspace, name)
    planned = _plan_fields(workspace, plan)
    names = {name}
    base = str((doc.get("remote") or {}).get("batch") or "")
    # a remote run log is <batch>_r<run id> (workspace_ci.py batch): only then does it carry its base batch's decision
    if base and re.fullmatch(re.escape(run_log.batch_id(base)) + r"_r\d+", name):
        names.add(run_log.batch_id(base))
    build_source = ("run log" if run.get("build") else "argument" if build else
                    "plan (declared)" if planned.get("build") else None)
    ctx = {"batch": name, "names": sorted(names),
           "platform": run.get("platform") or platform or planned.get("platform")
           or run_log._platform_of(proj, name, doc),
           "environment": run.get("environment") or environment or planned.get("environment"),
           "build": run.get("build") or build or planned.get("build"), "build_source": build_source,
           "pbi": planned.get("pbi"), "plan": plan, "plan_sha256": None, "plan_recorded": bool(doc.get("plan"))}
    if plan:
        try:
            ctx["plan_sha256"] = plan_sha(workspace, plan)
        except UsageError:
            ctx["plan_sha256"] = None
    return ctx


def _differences(entry: dict, ctx: dict, ident: dict | None) -> list[str]:
    out = []
    if entry.get("workspace") != ident:
        out.append(f"workspace {workspace_identity.label(ident)} ≠ approved "
                   f"{workspace_identity.label(entry.get('workspace'))}")
    if str(entry.get("platform", "")).lower() != str(ctx.get("platform") or "").lower():
        out.append(f"platform {ctx.get('platform') or '?'} ≠ approved {entry.get('platform')}")
    if str(entry.get("environment", "")) != str(ctx.get("environment") or ""):
        out.append(f"environment {ctx.get('environment') or '?'} ≠ approved {entry.get('environment')}")
    if not ctx.get("build") or not same_build(str(entry.get("build", "")), str(ctx["build"])):
        out.append(f"build {ctx.get('build') or '?'} ≠ approved {entry.get('build')}")
    if ctx.get("pbi") and str(entry.get("pbi")) != str(ctx["pbi"]):
        out.append(f"PBI {ctx['pbi']} (declared by the plan) ≠ approved {entry.get('pbi')}")
    if entry.get("plan") != ctx.get("plan") or entry.get("plan_sha256") != ctx.get("plan_sha256"):
        out.append(f"plan {ctx.get('plan') or '?'} (sha256 {str(ctx.get('plan_sha256') or '?')[:12]}) ≠ approved "
                   f"{entry.get('plan')} (sha256 {str(entry.get('plan_sha256'))[:12]})")
    return out


def check_run(workspace: str, ctx: dict, entries: list[dict] | None = None) -> dict:
    """{status, reason, approval} for the batch in `ctx`. Reads only; LedgerError when the register cannot be read."""
    entries = read(workspace) if entries is None else entries
    ident = workspace_identity.read(workspace)
    revoked = {e["revokes"] for e in entries if e.get("revokes")}
    decisions = [e for e in entries if e.get("gate") == GATE_RUN and e.get("batch") in ctx["names"]]
    if not ident:
        return {"status": MISSING, "reason": "this workspace declares no identity: no decision can be verified here",
                "approval": None}
    if not decisions:
        return {"status": MISSING, "reason": f"no run decision for {ctx['batch']} in this workspace", "approval": None}
    matching = [e for e in decisions if not _differences(e, ctx, ident)]
    alive = [e for e in matching if e["id"] not in revoked]
    if alive:
        return {"status": VALID, "reason": "", "approval": alive[-1]}
    if matching:
        e = matching[-1]
        why = next((r.get("reason") for r in entries if r.get("revokes") == e["id"]), "")
        return {"status": REVOKED, "reason": f"{e['id']} was revoked" + (f": {why}" if why else ""), "approval": e}
    latest = decisions[-1]
    return {"status": STALE, "reason": "; ".join(_differences(latest, ctx, ident)), "approval": latest}


def gate_for_add(workspace: str, proj: dict, name: str, doc: dict, environment: str | None,
                 build: str | None) -> str | None:
    """Why `run_log.py add` must not write a result to `name` under `policy.approvals.run: per-platform`, or None
    (also None without the policy). The plan recorded in the run log is required: it is what was approved."""
    if not run_per_platform(proj):
        return None
    if not workspace_identity.identity(proj):
        return "policy.approvals.run: per-platform needs the workspace's identity (workspace: {id, kind})"
    if not doc.get("plan"):
        return ("policy.approvals.run: the batch's plan is not recorded in its run log: record it first "
                "(run_log.py plan), so the approval can be checked against its content")
    ctx = context_of(workspace, proj, name, None, environment, build, doc=doc)
    missing = [k for k in ("platform", "environment", "build") if not ctx.get(k)]
    if ctx.get("build_source") == "plan (declared)":
        return ("policy.approvals.run: the build OBSERVED on the target is not known (no --build and no build in the "
                "run log); the plan's build is only the intended one: pass --build <observed> (device.py check)")
    if missing:
        return (f"policy.approvals.run: the batch's {', '.join(missing)} is unknown (run block, --environment/--build "
                "or the plan's run:): the approval cannot be checked")
    try:
        result = check_run(workspace, ctx)
    except LedgerError as e:
        return f"policy.approvals.run: {e}"
    if result["status"] == VALID:
        return None
    return (f"policy.approvals.run: run approval {result['status']} for {ctx['batch']} ({ctx['platform']}, "
            f"{ctx['environment']}, {ctx['build']}): {result['reason']}; ask the QA to confirm this context and record "
            "it with approvals.py record-run")


def run_status_by_platform(workspace: str, pbi: str, platforms: list[str]) -> dict[str, dict]:
    """For the PBI's record (pbi_state): per platform, the latest run decision of this workspace checked against its
    batch's current context, or missing. Reads only; {} when the register cannot be read is never assumed: the error
    is returned as the status `unreadable`."""
    try:
        entries = read(workspace)
    except LedgerError as e:
        return {p: {"status": "unreadable", "reason": str(e), "approval": None} for p in platforms}
    proj = read_project(workspace)
    out = {}
    for platform in platforms:
        mine = [e for e in entries if e.get("gate") == GATE_RUN and e.get("pbi") == pbi
                and str(e.get("platform")).lower() == platform]
        if not mine:
            out[platform] = {"status": MISSING, "reason": f"no run decision for {platform}", "approval": None}
            continue
        latest = mine[-1]
        ctx = context_of(workspace, proj, latest["batch"])
        out[platform] = check_run(workspace, ctx, entries)
    return out


# --- the plan gate (increment 2B) -------------------------------------------------------------------------------------
#
# The QA's decision on a batch plan: the COVERAGE it approves (the cases in their order, excluded, retest, assumptions,
# notes, platform, environment, PBI and any other operational key of the plan), never a run on a device or a build.
# The decision lives in this register only: it is never written into the plan, so recording it changes nothing that was
# hashed. The hash is canonical (plan-canonical-v1): the YAML's data without the old `approval:` block and without
# `run.build`, as sorted JSON; formatting and comments are not part of it. A build that the coverage depends on is an
# assumption of the plan (`assumptions: [{build: "1.0.0 (39)", why: "..."}]`), hashed with the rest.

PLAN_RECORD_KEYS = ("approval",)          # the old decision block: a record about the plan, not its content
PLAN_LIMITATION = (LIMITATION + "; it approves the plan's coverage only and authorises no run on a device or build")
DRAFT_DIRS = ("analysis/",)


def _rel(workspace: str, path: str) -> str:
    try:
        full = workspace_path(workspace, *str(path).replace("\\", "/").split("/"))
    except OutsideWorkspace as e:
        raise UsageError(str(e)) from None
    return os.path.relpath(full, workspace).replace(os.sep, "/")


def load_plan(workspace: str, plan: str) -> dict:
    """The plan file's data (a mapping); UsageError when it is not a readable YAML mapping of the workspace."""
    rel = _rel(workspace, plan)
    full = os.path.join(workspace, *rel.split("/"))
    if not os.path.isfile(full):
        raise UsageError(f"the plan {rel} is not a file of the workspace")
    try:
        import yaml
        with open(full, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise UsageError(f"the plan {rel} cannot be read ({e})") from None
    if not isinstance(data, dict):
        raise UsageError(f"the plan {rel} is not a mapping (a batches/<name>.yaml)")
    return data


def _key(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def canonical_plan(data: dict) -> dict:
    """The approved content of a plan: every key but the old `approval:` block, `run` without `build` (the platform in
    lower case), `excluded` and `retest` in a stable order (they are sets of choices); `cases` keeps its order."""
    out = {}
    for key, value in data.items():
        key = str(key)
        if key in PLAN_RECORD_KEYS:
            continue
        if key == "run" and isinstance(value, dict):
            value = {str(k): v for k, v in value.items() if str(k) != "build"}
            if value.get("platform") is not None:
                value["platform"] = str(value["platform"]).strip().lower()
            if not value:
                continue
        if key in ("excluded", "retest") and isinstance(value, list):
            value = sorted(value, key=_key)
        out[key] = value
    return json.loads(_key(out))           # dates and other YAML types as text, the same on every reading


def canonical_sha(canonical: dict) -> str:
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_digest(workspace: str, plan: str) -> tuple[dict, str]:
    canonical = canonical_plan(load_plan(workspace, plan))
    return canonical, canonical_sha(canonical)


def _pairs(rows) -> dict[str, str]:
    return {str(r.get("case")).strip(): str(r.get("quarantine") or "").strip()
            for r in rows or [] if isinstance(r, dict) and r.get("case")}


def plan_diff(old: dict | None, new: dict) -> list[str]:
    """What changed between the approved coverage and the plan now, in words the QA reads."""
    if not isinstance(old, dict):
        return ["no approved coverage to compare with (a first approval)"]
    out = []
    oc, nc = [str(c) for c in old.get("cases") or []], [str(c) for c in new.get("cases") or []]
    added, removed = [c for c in nc if c not in oc], [c for c in oc if c not in nc]
    if added:
        out.append(f"cases added: {', '.join(added)}")
    if removed:
        out.append(f"cases removed: {', '.join(removed)}")
    common_old, common_new = [c for c in oc if c in nc], [c for c in nc if c in oc]
    if common_old != common_new:
        out.append(f"order of the cases: {' > '.join(common_old)}  ->  {' > '.join(common_new)}")
    oe, ne, orr, nr = (_pairs(old.get("excluded")), _pairs(new.get("excluded")), _pairs(old.get("retest")),
                       _pairs(new.get("retest")))

    def where(case, ex, rt):
        if case in ex:
            return f"excluded ({ex[case] or '?'})"
        if case in rt:
            return f"directed retest ({rt[case] or '?'})"
        return "no quarantine decision"
    for case in sorted(set(oe) | set(ne) | set(orr) | set(nr)):
        before, after = where(case, oe, orr), where(case, ne, nr)
        if before != after:
            out.append(f"{case}: {before} -> {after}")
    orun, nrun = old.get("run") or {}, new.get("run") or {}
    for k in sorted(set(orun) | set(nrun)):
        if orun.get(k) != nrun.get(k):
            out.append(f"run.{k}: {orun.get(k)!r} -> {nrun.get(k)!r}")
    for key in sorted((set(old) | set(new)) - {"cases", "excluded", "retest", "run"}):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list):
            ak, bk = [_key(x) for x in a], [_key(x) for x in b]
            for x in [x for x in bk if x not in ak]:
                out.append(f"{key}: added {x}")
            for x in [x for x in ak if x not in bk]:
                out.append(f"{key}: removed {x}")
            if [x for x in ak if x in bk] != [x for x in bk if x in ak]:
                out.append(f"{key}: order changed")
        else:
            out.append(f"{key}: {_key(a)} -> {_key(b)}")
    return out or ["no difference in the coverage"]


def build_assumptions(canonical: dict) -> list[str]:
    """The builds the approved coverage depends on (assumptions written as {build: ...})."""
    rows = canonical.get("assumptions")
    return [str(r["build"]) for r in rows or [] if isinstance(r, dict) and r.get("build")]


def plan_problems(workspace: str, proj: dict, batch: str, plan: str, data: dict, pbi: str | None = None,
                  build: str | None = None) -> list[str]:
    """Why a plan cannot be approved as it is (shown in the review; approve-plan refuses while any is left)."""
    import run_log
    out = []
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases or not all(isinstance(c, (str, int)) and str(c).strip()
                                                           for c in cases):
        out.append("`cases:` must be a non-empty list of case IDs, in the order they run")
        cases = []
    cases = [str(c).strip() for c in cases]
    dup = sorted({c for c in cases if cases.count(c) > 1})
    if dup:
        out.append(f"cases repeated: {', '.join(dup)}")
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    if not str(run.get("platform") or "").strip():
        out.append("`run.platform` is required: the plan is approved for one platform")
    if not str(run.get("environment") or "").strip():
        out.append("`run.environment` is required: the plan is approved for one environment")
    elif proj.get("environments") and str(run["environment"]) not in (proj.get("environments") or {}):
        out.append(f"run.environment {run['environment']!r} is not an environment of project.yaml")
    if pbi and data.get("pbi") and str(data["pbi"]).strip() != str(pbi).strip():
        out.append(f"the plan declares pbi {data['pbi']!r}, not {pbi!r}")
    for key in ("assumptions",):
        if key in data and not isinstance(data[key], list):
            out.append(f"`{key}:` must be a list")
    for row in data.get("assumptions") or []:
        if isinstance(row, dict) and "build" in row and not str(row.get("build") or "").strip():
            out.append("an assumption {build: ...} without a build")
    if "notes" in data and not isinstance(data["notes"], (str, list)):
        out.append("`notes:` must be text or a list of texts")
    name = run_log.batch_id(batch)
    try:
        known, source = run_log.batch_library(workspace, proj, name, run_log.read_log(run_log.run_log_path(workspace,
                                                                                                           name)))
    except (run_log.UsageError, OSError, ValueError) as e:   # a library that cannot be read: never approved blind
        out.append(f"the project's library cannot be read ({e})")
        known, source = set(cases), "?"
    for c in cases:
        if c not in known and not run_log.proposed_case_files(workspace, c):
            out.append(f"{c}: not in the project's library ({source}) nor proposed in cases/")
    if run.get("platform"):
        import quarantine
        try:
            q = quarantine.scope(workspace, str(run["platform"]).strip().lower(),
                                 build or (str(run["build"]) if run.get("build") else None),
                                 str(run.get("environment") or "") or None, plan)
            out += [f"quarantine: {p}" for p in q["problems"]]
        except quarantine.UsageError as e:
            out.append(f"quarantine: {e}")
    return out


def _names(name: str, doc: dict) -> set[str]:
    import run_log
    names = {name}
    base = str((doc.get("remote") or {}).get("batch") or "")
    if base and re.fullmatch(re.escape(run_log.batch_id(base)) + r"_r\d+", name):
        names.add(run_log.batch_id(base))
    return names


def target_plan(workspace: str, batch: str) -> str:
    """The canonical path of the batch's plan: its own plan when one exists, else batches/<batch as named>.yaml."""
    import run_log
    found = run_log.plan_variants(workspace, run_log.batch_id(batch))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        ambiguous = run_log.plan_ambiguity(workspace, run_log.batch_id(batch))
        if ambiguous:
            raise Refused(ambiguous)
        stem = str(batch).strip()
        return f"batches/{stem}.yaml" if f"batches/{stem}.yaml" in found else found[0]
    stem = str(batch).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", stem):
        raise UsageError(f"--batch {batch!r}: letters, digits, '.', '_' and '-'")
    return f"batches/{stem}.yaml"


def _drafts_with(workspace: str, sha: str) -> list[str]:
    """Drafts under analysis/ whose canonical hash is `sha` (to name the draft an interrupted approval came from)."""
    folder = os.path.join(workspace, "analysis")
    out = []
    for root, _, files in os.walk(folder):
        for f in sorted(files):
            if f.endswith((".yaml", ".yml")):
                rel = os.path.relpath(os.path.join(root, f), workspace).replace(os.sep, "/")
                try:
                    if plan_digest(workspace, rel)[1] == sha:
                        out.append(rel)
                except (UsageError, Refused):
                    continue
    return out


def review_markdown(review: dict) -> str:
    c = review["canonical"]
    run = c.get("run") or {}
    lines = [f"# Plan review · {review['batch']}", "",
             f"- Draft: `{review['draft']}` (file SHA-256 `{review['draft_bytes_sha256'][:16]}…`)",
             f"- Approved path once confirmed: `{review['target']}`",
             f"- Coverage hash ({HASH_METHOD}): `{review['plan_sha256']}`",
             (f"- PBI: {c.get('pbi') or review.get('pbi') or '?'} · platform: {run.get('platform') or '?'} · "
              f"environment: {run.get('environment') or '?'}"),
             f"- Intended build (not part of the approval): {review.get('intended_build') or '-'}",
             ("- Approving this content approves the COVERAGE only: it authorises no run on a device or build "
              "(that is the run authorisation, asked separately)."), "", "## Cases, in order", ""]
    lines += [f"{n}. {case}" for n, case in enumerate(c.get("cases") or [], 1)]
    ex, rt = _pairs(c.get("excluded")), _pairs(c.get("retest"))
    if ex or rt:
        lines += ["", "## Quarantine decisions", ""]
        lines += [f"- {case}: excluded ({q or '?'})" for case, q in ex.items()]
        lines += [f"- {case}: directed retest ({q or '?'})" for case, q in rt.items()]
    if c.get("assumptions"):
        lines += ["", "## Assumptions", ""] + [f"- {_key(a) if not isinstance(a, str) else a}"
                                                for a in c["assumptions"]]
    if c.get("notes"):
        notes = c["notes"] if isinstance(c["notes"], list) else [c["notes"]]
        lines += ["", "## Notes (operational instructions, approved with the plan)", ""] + [f"- {n}" for n in notes]
    others = sorted(set(c) - {"cases", "excluded", "retest", "assumptions", "notes", "run", "pbi"})
    if others:
        lines += ["", "## Other content", ""] + [f"- {k}: {_key(c[k])}" for k in others]
    lines += ["", "## Against the coverage approved before", ""]
    if review.get("previous"):
        p = review["previous"]
        lines.append(f"Approved before: {p['id']} ({p.get('recorded_at')}, hash `{str(p.get('plan_sha256'))[:16]}…`)")
        lines += [f"- {d}" for d in review["diff"]]
    else:
        lines.append("- no approved coverage for this batch in this workspace (a first approval)")
    if review["problems"]:
        lines += ["", "## PROBLEMS: not approvable until fixed", ""] + [f"- {p}" for p in review["problems"]]
    lines += ["", (f"To approve: the QA's explicit message, then approvals.py <workspace> approve-plan --batch "
                   f"{review['batch']} --draft {review['draft']} --sha {review['plan_sha256']} ..."), ""]
    return "\n".join(lines)


def _latest_plan_decision(entries: list[dict], ident: dict | None, names: set[str]) -> dict | None:
    mine = [e for e in entries if e.get("gate") == GATE_PLAN and e.get("batch") in names
            and e.get("workspace") == ident]
    return mine[-1] if mine else None


def review_plan(workspace: str, batch: str, draft: str, pbi: str | None = None, build: str | None = None,
                out: str | None = None) -> dict:
    """The readable review of a draft (written to `out`, default analysis/<draft>.review.md): its hash, its content,
    the difference from the coverage approved before for the batch, and what keeps it from being approved."""
    import run_log
    proj = read_project(workspace)
    rel = _rel(workspace, draft)
    data = load_plan(workspace, rel)
    canonical = canonical_plan(data)
    name = run_log.batch_id(batch)
    entries = read(workspace)
    ident = workspace_identity.read(workspace)
    previous = _latest_plan_decision(entries, ident, {name})
    with open(os.path.join(workspace, *rel.split("/")), "rb") as f:
        draft_bytes = hashlib.sha256(f.read()).hexdigest()
    review = {"batch": name, "draft": rel, "draft_bytes_sha256": draft_bytes, "target": target_plan(workspace, batch),
              "plan_sha256": canonical_sha(canonical), "canonical": canonical, "pbi": pbi,
              "intended_build": str((data.get("run") or {}).get("build") or "") or None,
              "previous": previous, "diff": plan_diff((previous or {}).get("coverage"), canonical) if previous else [],
              "problems": plan_problems(workspace, proj, batch, rel, data, pbi, build)}
    text = review_markdown(review)
    stem = re.sub(r"\.ya?ml$", "", os.path.basename(rel))
    path = _rel(workspace, out) if out else f"analysis/{stem}.review.md"
    atomic_write(os.path.join(workspace, *path.split("/")), text.encode("utf-8"))
    review["review"] = path
    review["markdown"] = text
    return review


def _parse_plan_bytes(raw: bytes, rel: str) -> dict:
    try:
        import yaml
        data = yaml.safe_load(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise UsageError(f"the plan {rel} cannot be read ({e})") from None
    if not isinstance(data, dict):
        raise UsageError(f"the plan {rel} is not a mapping (a batches/<name>.yaml)")
    return data


def _logs_with_results(workspace: str, name: str, target: str) -> list[str]:
    """The run logs that already hold results of this batch's plan: the batch's own run log (whatever plan it follows)
    and any other run log that follows `target` (a remote run `<batch>_r<id>`, a batch that reused the file)."""
    import run_log
    folder = os.path.dirname(run_log.run_log_path(workspace, name))
    out = []
    for f in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not f.endswith(".json"):
            continue
        log = f[:-5]
        try:
            doc = run_log.read_log(os.path.join(folder, f))
        except (run_log.UsageError, OSError, ValueError):
            doc = {}                       # not a run log: never ours to judge here
        if doc.get("results") and (log == name or str(doc.get("plan") or "").replace("\\", "/") == target):
            out.append(log)
    return out


def approve_plan(workspace: str, pbi: str, batch: str, draft: str, sha: str, declared_by: str, message_ref: str,
                 quote: str | None = None, review: str | None = None, tickets: dict | None = None,
                 now: datetime | None = None) -> dict:
    """After the QA's explicit message: the draft must still have the hash that was presented; its bytes become the
    batch's plan (batches/...) and the decision is appended. Everything is decided under the workspace's lock from ONE
    reading of the draft's bytes: the hash checked, the bytes written and the hash recorded are the same, whatever
    another process does to the draft meanwhile; the written plan is read back before the decision is appended.
    Idempotent: run again after an interruption it finishes what is missing (the copy, the record) and never records
    the same decision twice. A plan already followed by results is never replaced: a new version is a new batch."""
    import run_log
    for flag, value in (("--pbi", pbi), ("--batch", batch), ("--draft", draft), ("--sha", sha),
                        ("--declared-by", declared_by), ("--message-ref", message_ref)):
        if not str(value or "").strip():
            raise UsageError(f"{flag} is required: a decision is recorded with every field, nothing is inferred")
    ident = _identity_or_refuse(workspace)
    proj = read_project(workspace)
    rel = _rel(workspace, draft)
    name = run_log.batch_id(batch)
    sha = sha.strip()
    with workspace_lock(workspace):
        target = target_plan(workspace, batch)
        if rel != target and not rel.startswith(DRAFT_DIRS):
            raise Refused(f"{rel}: the draft presented to the QA lives in analysis/ (or is the batch's own plan "
                          f"{target}, to confirm a plan already saved): nothing was written")
        full = os.path.join(workspace, *rel.split("/"))
        if not os.path.isfile(full):
            raise UsageError(f"the plan {rel} is not a file of the workspace")
        with open(full, "rb") as f:
            raw = f.read()                                   # the one reading everything below rests on
        data = _parse_plan_bytes(raw, rel)
        canonical = canonical_plan(data)
        now_sha = canonical_sha(canonical)
        if now_sha != sha:
            raise Refused(f"{rel} changed since it was presented (presented {sha[:16]}…, now {now_sha[:16]}…): review "
                          "it again (approvals.py review-plan) and ask the QA about the new version: nothing was "
                          "written")
        problems = plan_problems(workspace, proj, batch, rel, data, pbi)
        if problems:
            raise Refused("the plan cannot be approved as it is: " + "; ".join(problems) + ": nothing was written")
        review_sha = None
        if review:
            review = _rel(workspace, review)
            rfull = os.path.join(workspace, *review.split("/"))
            if not os.path.isfile(rfull):
                raise UsageError(f"--review {review}: not a file of the workspace")
            with open(rfull, "rb") as f:
                rraw = f.read()
            if now_sha.encode() not in rraw:
                raise Refused(f"{review} is not the review of this version (it does not show {now_sha[:16]}…): "
                              "nothing was written")
            review_sha = hashlib.sha256(rraw).hexdigest()
        where = run_log.applied(workspace, proj, name)
        if where:
            raise Refused(f"{name} was already {where}: a new version of the plan is a new batch: nothing was written")
        target_full = os.path.join(workspace, *target.split("/"))
        current = plan_digest(workspace, target)[1] if os.path.isfile(target_full) else None
        if current is not None and current != now_sha:
            followed = _logs_with_results(workspace, name, target)
            if followed:
                raise Refused(f"{target} already has results ({', '.join(followed)}) following its approved content "
                              f"(coverage {current[:12]}…): it is never replaced by another version, and those results "
                              f"stay as they are. Plan the new version as a new batch, e.g. "
                              f"{run_log.next_name(workspace, proj, name)}: nothing was written")
        entries = read(workspace)
        revoked = {e["revokes"] for e in entries if e.get("revokes")}
        if rel != target and current != now_sha:
            atomic_write(target_full, raw)                   # 1. the approved version at the batch's path
            if plan_digest(workspace, target)[1] != now_sha:
                raise Refused(f"{target} does not read back with the approved coverage {now_sha[:16]}…: no decision "
                              "was recorded")
        for old in entries:                                  # 2. the decision, once
            if (old.get("gate") == GATE_PLAN and old["id"] not in revoked and old.get("workspace") == ident
                    and old.get("pbi") == str(pbi).strip() and old.get("batch") == name and old.get("plan") == target
                    and old.get("plan_sha256") == now_sha):
                return {**old, "repeated": True}
        entry = {"id": _next_id(entries), "gate": GATE_PLAN, "workspace": ident, "pbi": str(pbi).strip(),
                 "batch": name, "platform": str(data["run"]["platform"]).strip().lower(),
                 "environment": str(data["run"]["environment"]).strip(), "plan": target,
                 "plan_sha256": now_sha, "hash_method": HASH_METHOD, "coverage": canonical,
                 "intended_build": str(data["run"].get("build") or "") or None,
                 "presented": {"draft": rel, "draft_bytes_sha256": hashlib.sha256(raw).hexdigest(),
                               **({"review": review, "review_sha256": review_sha} if review else {})},
                 **({"basis": {"tickets": dict(tickets)}} if tickets else {}),
                 "authorises_run": False, "declared_by": declared_by.strip(), "confirmation": CONFIRMATION,
                 "message_ref": message_ref.strip(), "message_ref_verified": False,
                 **({"quote": quote.strip()} if quote and quote.strip() else {}),
                 "limitation": PLAN_LIMITATION, "recorded_by": "assistant", "recorded_at": _now(now)}
        _append(workspace, entry)
    return entry


def check_plan(workspace: str, batch: str, platform: str | None = None, environment: str | None = None,
               build: str | None = None, plan: str | None = None, doc: dict | None = None) -> dict:
    """Is the batch's plan approved, as it is now, by a decision of this workspace, and do its quarantine decisions and
    build assumptions still hold in the effective context? Reads only; LedgerError when the register cannot be read.
    {status: valid|missing|legacy|stale|revoked, reason, approval, plan, plan_sha256, diff, next, context}"""
    import run_log
    name = run_log.batch_id(batch)
    doc = doc if doc is not None else run_log.read_log(run_log.run_log_path(workspace, name))
    plan = plan or doc.get("plan") or run_log.own_plan(workspace, name)
    out = {"batch": name, "plan": plan, "plan_sha256": None, "approval": None, "diff": [], "next": "",
           "status": MISSING, "reason": ""}
    entries = read(workspace)
    ident = workspace_identity.read(workspace)
    if not ident:
        out["reason"] = "this workspace declares no identity: no plan decision can be verified here"
        return out
    if not plan:
        out["reason"] = f"{name} has no plan (batches/<name>.yaml)"
        out["next"] = "plan it with /plan-tests: a draft in analysis/, approvals.py review-plan, the QA's decision"
        return out
    try:
        data = load_plan(workspace, plan)
    except UsageError as e:
        out["reason"] = str(e)
        return out
    canonical = canonical_plan(data)
    sha = canonical_sha(canonical)
    out["plan_sha256"] = sha
    run = doc.get("run") if isinstance(doc.get("run"), dict) else {}
    planned = data.get("run") if isinstance(data.get("run"), dict) else {}
    observed = run.get("build") or build
    ctx = {"platform": str(run.get("platform") or platform or planned.get("platform") or "").lower() or None,
           "environment": run.get("environment") or environment or planned.get("environment"),
           "build": observed or planned.get("build"), "build_observed": bool(observed)}
    out["context"] = ctx
    names = _names(name, doc)
    revoked = {e["revokes"] for e in entries if e.get("revokes")}
    decisions = [e for e in entries if e.get("gate") == GATE_PLAN and e.get("batch") in names]
    mine = [e for e in decisions if e.get("workspace") == ident]
    exact = [e for e in mine if e.get("plan") == plan and e.get("plan_sha256") == sha
             and (not data.get("pbi") or str(e.get("pbi")) == str(data["pbi"]).strip())]
    alive = [e for e in exact if e["id"] not in revoked]
    if alive:
        e = alive[-1]
        out["approval"] = e
        why = []
        if ctx["platform"] and ctx["platform"] != e.get("platform"):
            why.append(f"platform {ctx['platform']} ≠ approved {e.get('platform')}")
        if ctx["environment"] and str(ctx["environment"]) != str(e.get("environment")):
            why.append(f"environment {ctx['environment']} ≠ approved {e.get('environment')}")
        if observed:
            for b in build_assumptions(canonical):
                if not same_build(b, str(observed)):
                    why.append(f"the approved coverage assumes the build {b}; the build observed is {observed}")
        import quarantine
        try:
            q = quarantine.scope(workspace, ctx["platform"] or e.get("platform"), ctx["build"],
                                 ctx["environment"], plan)["problems"]
        except quarantine.UsageError as err:
            q = [str(err)]
        why += [f"quarantine decision no longer holds in this context (build {ctx['build'] or '?'}): {p}" for p in q]
        if why:
            out.update(status=STALE, reason="; ".join(why),
                       next="back to the QA's review of the plan (/plan-tests): show this, change the draft, review "
                            "and approve the new version")
            return out
        out.update(status=VALID, next="reuse it: nothing to ask again about the plan (the run authorisation is "
                                      "separate)")
        return out
    if exact:
        e = exact[-1]
        reason = next((r.get("reason") for r in entries if r.get("revokes") == e["id"]), "")
        out.update(status=REVOKED, approval=e, reason=f"{e['id']} was revoked" + (f": {reason}" if reason else ""),
                   next="present the plan again and ask the QA")
        return out
    if decisions:
        latest = mine[-1] if mine else decisions[-1]
        why = []
        if latest.get("workspace") != ident:
            why.append(f"workspace {workspace_identity.label(ident)} ≠ approved "
                       f"{workspace_identity.label(latest.get('workspace'))}")
        if latest.get("plan") != plan:
            why.append(f"plan {plan} ≠ approved {latest.get('plan')}")
        if latest.get("plan_sha256") != sha:
            why.append(f"coverage {sha[:12]} ≠ approved {str(latest.get('plan_sha256'))[:12]}")
        out.update(status=STALE, approval=latest, reason="; ".join(why) or "the decision does not match",
                   diff=plan_diff(latest.get("coverage"), canonical) if latest.get("workspace") == ident else [],
                   next="show the difference to the QA (approvals.py review-plan) and ask about the new version")
        return out
    drafts = _drafts_with(workspace, sha)
    if data.get("approval"):
        out.update(status=LEGACY, reason=f"{plan} carries an old `approval:` block but no decision in the register: "
                                         "it is shown as information, not as an approval",
                   next=f"present it (approvals.py review-plan --batch {name} --draft {plan}) and, with the QA's "
                        "explicit confirmation, approve-plan with the same file")
        return out
    out["reason"] = f"no plan decision for {name} ({plan}, coverage {sha[:12]}) in this workspace"
    out["next"] = (f"an approval left halfway? the draft {', '.join(drafts)} has this content: if the QA confirmed it "
                   f"in the chat, finish with approve-plan --draft {drafts[0]} --sha {sha}; otherwise review it and ask"
                   if drafts else "review it (approvals.py review-plan) and ask the QA")
    return out


def gate_for_plan(workspace: str, proj: dict, name: str, doc: dict, environment: str | None = None,
                  build: str | None = None, plan: str | None = None) -> str | None:
    """Why the batch must not go on under `policy.approvals.plan: required`, or None (also None without the policy)."""
    if not plan_required(proj):
        return None
    if not workspace_identity.identity(proj):
        return "policy.approvals.plan: required needs the workspace's identity (workspace: {id, kind})"
    if not (plan or doc.get("plan")):
        return ("policy.approvals.plan: the batch's plan is not recorded in its run log: record it first "
                "(run_log.py plan), so it can be checked against the QA's plan decision")
    try:
        result = check_plan(workspace, name, None, environment, build, plan, doc)
    except LedgerError as e:
        return f"policy.approvals.plan: {e}"
    if result["status"] == VALID:
        return None
    diff = ("; difference: " + " | ".join(result["diff"])) if result.get("diff") else ""
    return (f"policy.approvals.plan: plan approval {result['status']} for {result['batch']} ({result['plan']}): "
            f"{result['reason']}{diff}; {result['next']}")


def plan_status_by_platform(workspace: str, pbi: str, platforms: list[str]) -> dict[str, dict]:
    """Per platform, the latest plan decision of this workspace for the PBI, checked as it stands now."""
    try:
        entries = read(workspace)
    except LedgerError as e:
        return {p: {"status": "unreadable", "reason": str(e), "approval": None} for p in platforms}
    ident = workspace_identity.read(workspace)
    out = {}
    for platform in platforms:
        mine = [e for e in entries if e.get("gate") == GATE_PLAN and e.get("pbi") == pbi
                and str(e.get("platform")).lower() == platform and e.get("workspace") == ident]
        if not mine:
            out[platform] = {"status": MISSING, "reason": f"no plan decision for {platform}", "approval": None}
            continue
        result = check_plan(workspace, mine[-1]["batch"])
        out[platform] = result
    return out


# --- command line ---------------------------------------------------------------------------------------------------

def _line(e: dict) -> str:
    if e.get("gate") == GATE_PLAN:
        return (f"{e['id']} plan · PBI {e.get('pbi')} · {e.get('batch')} · {e.get('platform')} · {e.get('environment')}"
                f" · plan {e.get('plan')} (coverage {str(e.get('plan_sha256'))[:12]}) · "
                f"{len((e.get('coverage') or {}).get('cases') or [])} case(s) · declared by {e.get('declared_by')} "
                f"({e.get('confirmation')}, ref {e.get('message_ref')}, not verified) · authorises no run · "
                f"{e.get('recorded_at')} · {workspace_identity.label(e.get('workspace'))}")
    if e.get("revokes"):
        return f"{e['id']} revokes {e['revokes']} · {e.get('reason', '')} · declared by {e.get('declared_by')} " \
               f"({e.get('confirmation')}, ref {e.get('message_ref')}) · {e.get('recorded_at')}"
    return (f"{e['id']} {e.get('gate')} · PBI {e.get('pbi')} · {e.get('batch')} · {e.get('platform')} · "
            f"{e.get('environment')} · {e.get('build')} · plan {e.get('plan')} ({str(e.get('plan_sha256'))[:12]}) · "
            f"declared by {e.get('declared_by')} ({e.get('confirmation')}, ref {e.get('message_ref')}, not verified) · "
            f"{e.get('recorded_at')} · {workspace_identity.label(e.get('workspace'))}")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/approvals.py", description="The QA's decisions (run gate).")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("record-run", help="record the QA's declared authorisation to run a batch")
    for flag in ("--pbi", "--batch", "--platform", "--environment", "--build", "--plan", "--declared-by",
                 "--message-ref"):
        p.add_argument(flag)
    p.add_argument("--quote", help="the QA's words, as written in the chat")
    p = sub.add_parser("check", help="is the batch's run authorised in its current context (reads only)")
    p.add_argument("--batch", required=True)
    p.add_argument("--platform")
    p.add_argument("--environment")
    p.add_argument("--build")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("list", help="the decisions, old ones included")
    p.add_argument("--pbi")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("review-plan", help="the readable review of a draft plan: its hash and the difference from "
                                          "the coverage approved before (writes only the review's Markdown)")
    p.add_argument("--batch", required=True)
    p.add_argument("--draft", required=True, help="the draft (analysis/<name>.yaml) or the batch's own plan")
    p.add_argument("--pbi")
    p.add_argument("--build", help="the build observed, for the quarantine check (else the plan's intended build)")
    p.add_argument("--out", help="the review's Markdown (default analysis/<draft>.review.md)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve-plan", help="after the QA's explicit message: save the approved version and record it")
    for flag in ("--pbi", "--batch", "--draft", "--sha", "--declared-by", "--message-ref"):
        p.add_argument(flag)
    p.add_argument("--quote", help="the QA's words, as written in the chat")
    p.add_argument("--review", help="the review's Markdown presented (kept as a reference)")
    p.add_argument("--ticket", action="append", default=[], help="ID=REVISION of a ticket the plan rests on")
    p = sub.add_parser("check-plan", help="is the batch's plan approved as it is now (reads only)")
    p.add_argument("--batch", required=True)
    p.add_argument("--platform")
    p.add_argument("--environment")
    p.add_argument("--build", help="the build observed now (quarantine decisions and build assumptions)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("revoke", help="revoke a decision (a new line; the decision stays for consultation)")
    p.add_argument("id")
    p.add_argument("--declared-by")
    p.add_argument("--message-ref")
    p.add_argument("--reason")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        conflict = workspace_identity.session_conflict(ws, os.getcwd(), ROOT)
        if conflict:
            raise Refused(workspace_identity.conflict_text(conflict))
        if not os.path.isfile(os.path.join(ws, "project.yaml")):
            raise UsageError(f"{ws} has no project.yaml: not a workspace")
        if a.command == "record-run":
            e = record_run(ws, a.pbi, a.batch, a.platform, a.environment, a.build, a.plan, a.declared_by,
                           a.message_ref, a.quote)
            print(f"recorded: {_line(e)}")
            print(f"note: {LIMITATION}")
            return 0
        if a.command == "review-plan":
            r = review_plan(ws, a.batch, a.draft, a.pbi, a.build, a.out)
            if a.json:
                print(json.dumps({k: v for k, v in r.items() if k != "markdown"}, ensure_ascii=False, indent=1))
            else:
                print(r["markdown"])
                print(f"review written: {r['review']}")
            return 1 if r["problems"] else 0
        if a.command == "approve-plan":
            tickets = {}
            for t in a.ticket:
                if "=" not in t:
                    raise UsageError(f"--ticket {t!r}: ID=REVISION")
                k, v = t.split("=", 1)
                tickets[k.strip()] = v.strip()
            e = approve_plan(ws, a.pbi, a.batch, a.draft, a.sha, a.declared_by, a.message_ref, a.quote, a.review,
                             tickets)
            print(("already recorded: " if e.get("repeated") else "recorded: ") + _line(e))
            print(f"approved plan: {e['plan']} (coverage {e['plan_sha256']})")
            print(f"note: {PLAN_LIMITATION}")
            return 0
        if a.command == "check-plan":
            result = check_plan(ws, a.batch, a.platform, a.environment, a.build)
            if a.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
            else:
                print(f"PLAN APPROVAL {result['status'].upper()} · {result['batch']} · plan {result['plan'] or '?'}"
                      f" · coverage {str(result.get('plan_sha256') or '?')[:12]}")
                if result["approval"]:
                    print(f"  decision: {_line(result['approval'])}")
                if result["reason"]:
                    print(f"  why: {result['reason']}")
                for d in result.get("diff") or []:
                    print(f"  difference: {d}")
                if result["next"]:
                    print(f"  next: {result['next']}")
                print("  (an approved plan authorises no run: the run authorisation is a separate decision)")
                if not plan_required(read_project(ws)):
                    print("  (policy.approvals.plan is not required here: this answer is information, not a gate)")
            return 0 if result["status"] == VALID else 1
        if a.command == "revoke":
            e = revoke(ws, a.id, a.declared_by, a.message_ref, a.reason)
            print(f"recorded: {_line(e)}")
            return 0
        if a.command == "list":
            entries = [e for e in read(ws) if not a.pbi or e.get("pbi") == a.pbi or e.get("revokes")]
            if a.json:
                print(json.dumps(entries, ensure_ascii=False, indent=1))
            else:
                for e in entries:
                    print(_line(e))
                if not entries:
                    print("no decision recorded")
            return 0
        proj = read_project(ws)
        ctx = context_of(ws, proj, a.batch, a.platform, a.environment, a.build)
        result = check_run(ws, ctx)
        if a.json:
            print(json.dumps({"context": ctx, **result}, ensure_ascii=False, indent=1))
        else:
            head = (f"RUN APPROVAL {result['status'].upper()} · {ctx['batch']} · {ctx.get('platform') or '?'} · "
                    f"{ctx.get('environment') or '?'} · {ctx.get('build') or '?'} · plan {ctx.get('plan') or '?'}")
            print(head)
            if result["approval"]:
                print(f"  decision: {_line(result['approval'])}")
            if result["reason"]:
                print(f"  why: {result['reason']}")
            print("  " + {VALID: "reuse it: nothing to ask again",
                          MISSING: "ask the QA to authorise this run, then record-run",
                          STALE: "ask the QA to confirm the new context, then record-run (the old decision stays)",
                          REVOKED: "ask the QA again; the revoked decision stays for consultation"}[result["status"]])
            if not run_per_platform(proj):
                print("  (policy.approvals.run is not per-platform here: this answer is information, not a gate)")
        return 0 if result["status"] == VALID else 1
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except LedgerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except WorkspaceBusy as e:
        print(f"BUSY: {e}: nothing was written", file=sys.stderr)
        return 5


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
