"""
The QA's decisions, recorded per workspace, and the check that a run is authorised for its platform
(docs/procedures/approvals.md; increment 2, first part: the `run` gate only).

    python scripts/approvals.py <workspace> record-run --pbi PBI --batch BATCH --platform P --environment E
                                                      --build BUILD --plan batches/<name>.yaml --declared-by WHO
                                                      --message-ref REF [--quote TEXT]
    python scripts/approvals.py <workspace> check --batch BATCH [--platform P] [--environment E] [--build B] [--json]
    python scripts/approvals.py <workspace> list [--pbi PBI] [--json]
    python scripts/approvals.py <workspace> revoke <id> --declared-by WHO --message-ref REF --reason TEXT

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
RUN_VALUES = ("per-platform", "with-request")
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
    if not isinstance(approvals, dict) or set(approvals) - {"run"}:
        return ["policy.approvals: only `run` is known in this version (docs/procedures/approvals.md)"]
    out = []
    if "run" in approvals and approvals["run"] not in RUN_VALUES:
        out.append(f"policy.approvals.run must be one of {list(RUN_VALUES)}")
    if approvals.get("run") == "per-platform" and not workspace_identity.identity(proj):
        out.append("policy.approvals.run: per-platform needs the workspace's identity (workspace: {id, kind}; "
                   "technique-workspaces.md 4.1)")
    return out


def run_per_platform(proj: dict) -> bool:
    policy = proj.get("policy") if isinstance(proj, dict) else None
    approvals = policy.get("approvals") if isinstance(policy, dict) else None
    return isinstance(approvals, dict) and approvals.get("run") == "per-platform"


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


# --- command line ---------------------------------------------------------------------------------------------------

def _line(e: dict) -> str:
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
