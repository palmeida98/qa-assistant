"""
Command line of the tracker adapters (technique-jira-xray.md section 2.2): one entry point for both trackers, the
tracker comes from project.yaml's `tracker:`.

    python -m adapters.tracker <workspace> check
    python -m adapters.tracker <workspace> import-cases [--query "<JQL|WIQL>"]
    python -m adapters.tracker <workspace> export-cases <cases.json> [--yes] [--now ...]
    python -m adapters.tracker <workspace> apply <batch.json> [--yes] [--now YYYY-MM-DDTHH:MM:SS]
    python -m adapters.tracker <workspace> bug-search <draft.json> [--include-closed]
    (every command also takes --record: the exchanges, redacted, go to <workspace>/tracker/recordings/)

Writing commands (apply, export-cases) are a dry run without --yes: they validate, resolve against the tracker
(reads only) and print the plan. Exit codes (section 2.4):
  0  sent and read back; or nothing to send (already sent with the same content, every item not sent)
  1  dry run with something to send: not approved yet, repeat with --yes after the user approves
  2  conflict with the tracker (a case, bug or test plan not found...): nothing written
  3  network, authentication, rate limit or server error: with --yes the send may be partial; the record has
     exactly what was confirmed, and running the same command again continues from there
  4  invalid batch, input or configuration (incl. Question, a status outside the map, a batch already sent with
     another content, a back-end not built yet): nothing resolved nor sent
  5  sent, but the read back differs: show the differences, never send again blindly
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime

from . import config as tconfig
from .base import Context, prepare_batch, prepare_cases, resolve_input
from .errors import (
    INVALID,
    NOT_APPROVED,
    OK,
    READ_BACK,
    InvalidInput,
    NotImplementedYet,
    TrackerError,
)
from .http import Recorder, SecretRedactor, urllib_transport
from .record import SentRecord, safe_path

BACKENDS = {"jira": ("adapters.jira", "JiraAdapter"),
            "azure-devops": ("adapters.azure_devops", "AzureDevOpsAdapter")}
NAMES = {"jira": "Jira (Xray)", "azure-devops": "Azure DevOps"}


def backend_class(tracker: str):
    module, name = BACKENDS[tracker]
    cls = getattr(importlib.import_module(module), name)
    if not getattr(cls, "implemented", True):
        raise NotImplementedYet(
            f"the {NAMES[tracker]} back-end is not implemented yet (plan {tconfig.TASKS[tracker]}): until then "
            "results and bugs go to the tracker by hand, from the run log (methodology 4.2). The configuration "
            "and the input were checked.")
    return cls


def _print_plan(ctx: Context, plan, yes: bool, what: str, resumed: bool) -> None:
    head = "Plan" if yes else "Plan (dry run: nothing was sent)"
    if resumed:
        head += " — continuing an incomplete send (the record has the writes already confirmed)"
    ctx.out(f"{head} — {what}:")
    for line in plan.lines() or ["  nothing to send"]:
        ctx.out(line)


def run_apply(ctx: Context, cls, path: str, yes: bool) -> int:
    batch = prepare_batch(resolve_input(ctx.workspace, path), ctx.config)
    record = SentRecord(ctx.workspace, batch.batch_id, batch.hash, ctx.config.tracker, ctx.redactor, ctx.now)
    state = record.state()
    ctx.out(f"Batch: {batch.path} ({len(batch.items)} items, {len(batch.to_send)} to send, "
            f"{len(batch.not_sent)} not sent) · environment {batch.run['environment']} · build {batch.run['build']}")
    if state == "complete":
        ctx.out(f"Already sent with the same content (record {record.path}): nothing to do, no request made.")
        return OK
    adapter = cls(ctx)
    plan = adapter.plan_apply(batch, record)
    plan.not_sent[:0] = [f"{i.tc} ({i.status}): "
                         f"{'mapped to skip' if ctx.config.status_map.get(i.status) else 'unmapped'} in "
                         f"{ctx.config.status_map_name} — stays only in the run log" for i in batch.not_sent]
    _print_plan(ctx, plan, yes, f"batch {batch.batch_id}", state == "incomplete")
    if not plan.writes:
        if yes:
            record.finish(not_sent=plan.not_sent)
        ctx.out("Nothing to send.")
        return OK
    if not yes:
        ctx.out("Show this list to the user; after the approval, run the same command with --yes (methodology 9.4).")
        return NOT_APPROVED
    record.start(run=batch.run, not_sent=plan.not_sent)
    adapter.send_apply(batch, plan, record)
    record.finish()
    ctx.out(f"Sent: {len(record.writes)} write(s) confirmed, recorded in {record.path}.")
    differences = adapter.read_back(batch, plan, record)
    if differences:
        ctx.out(f"\nRead back: {len(differences)} difference(s) — do NOT send again blindly:")
        for d in differences:
            ctx.out(f" - {d}")
        return READ_BACK
    ctx.out("Read back: it matches what was sent.")
    return OK


def run_export(ctx: Context, cls, path: str, yes: bool) -> int:
    path = resolve_input(ctx.workspace, path)
    doc, digest = prepare_cases(path, ctx.config)
    name = os.path.splitext(os.path.basename(path))[0]
    record = SentRecord(ctx.workspace, name, digest, ctx.config.tracker, ctx.redactor, ctx.now)
    state = record.state()
    if state == "complete":
        ctx.out(f"Already exported with the same content (record {record.path}): nothing to do, no request made.")
        return OK
    adapter = cls(ctx)
    plan = adapter.plan_export(doc, record)
    _print_plan(ctx, plan, yes, f"cases file {name}", state == "incomplete")
    if not plan.writes:
        if yes:
            record.finish(not_sent=plan.not_sent)
        ctx.out("Nothing to create.")
        return OK
    if not yes:
        ctx.out("Show this list to the user; after the approval, run the same command with --yes.")
        return NOT_APPROVED
    record.start(not_sent=plan.not_sent)
    adapter.send_export(doc, plan, record)
    record.finish()
    ctx.out(f"Created: {len(record.writes)} write(s) confirmed, recorded in {record.path}.")
    return OK


def run_bug_search(ctx: Context, cls, path: str, include_closed: bool) -> int:
    path = resolve_input(ctx.workspace, path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            draft = json.load(f)
    except FileNotFoundError:
        raise InvalidInput(f"draft not found: {path}") from None
    except ValueError as e:
        raise InvalidInput(f"{path}: not valid JSON ({e})") from None
    if not isinstance(draft, dict) or not (draft.get("observed") or draft.get("title")):
        raise InvalidInput(f"{path}: a draft is an object with at least 'observed' or 'title' "
                           "(and the keys of bugs.fields)")
    candidates = cls(ctx).bug_search(draft, include_closed)
    if not candidates:
        ctx.out("No candidate found: a new bug is justified (methodology 9.1) — the user decides.")
        return OK
    ctx.out(f"{len(candidates)} candidate(s) — the user decides: update one of them, or create a new bug:")
    for c in candidates:
        ctx.out(f"  {c.get('key')} [{c.get('status')}] {c.get('summary')} — matches: "
                f"{', '.join(c.get('matched') or []) or '-'} (pass {c.get('pass', 1)})")
    return OK


def run_import(ctx: Context, cls, query: str | None) -> int:
    summary = cls(ctx).import_cases(query) or {}
    ctx.out(f"Imported {summary.get('cases', 0)} case(s) into {safe_path(ctx.workspace, 'cases.json')}.")
    for flag in summary.get("flags") or []:
        ctx.out(f"  flag: {flag}")
    return OK


def run_check(ctx: Context, cls) -> int:
    missing = cls(ctx).check() or []
    if missing:
        ctx.out(f"{len(missing)} problem(s) in the tracker for this configuration:")
        for m in missing:
            ctx.out(f" - {m}")
        return INVALID
    ctx.out("check: the tracker has everything this configuration uses.")
    return OK


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m adapters.tracker",
                                 description="Tracker adapter (Jira with Xray, Azure DevOps): the tracker comes "
                                             "from project.yaml")
    ap.add_argument("workspace")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--record", action="store_true",
                        help="save each exchange, redacted, in <workspace>/tracker/recordings/ (pilot, real golden)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="read only: the configuration against the tracker")
    p = sub.add_parser("import-cases", parents=[common], help="read only: the tracker's cases into tracker/cases.json")
    p.add_argument("--query", help="JQL (Jira) or WIQL (Azure DevOps) instead of the configured one")
    now_help = "instant used in every date written, instead of the clock (golden)"
    p = sub.add_parser("export-cases", parents=[common], help="create the approved new cases (dry run without --yes)")
    p.add_argument("cases")
    p.add_argument("--yes", action="store_true", help="send, after the user approved the dry run")
    p.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS", help=now_help)
    p = sub.add_parser("apply", parents=[common], help="send a batch's results and bugs (dry run without --yes)")
    p.add_argument("batch")
    p.add_argument("--yes", action="store_true", help="send, after the user approved the dry run")
    p.add_argument("--now", type=datetime.fromisoformat, metavar="YYYY-MM-DDTHH:MM:SS", help=now_help)
    p = sub.add_parser("bug-search", parents=[common], help="read only: candidate duplicates of a bug draft")
    p.add_argument("draft")
    p.add_argument("--include-closed", action="store_true", help="also closed bugs (a regression of a fixed bug)")
    return ap


def main(argv=None, backend=None, transport=None, sleep=None, printer=None) -> int:
    """`backend`, `transport`, `sleep` and `printer` replace the real ones in the tests."""
    a = parser().parse_args(argv)
    printer = printer or print
    err = (lambda s: printer(s)) if printer is not print else (lambda s: print(s, file=sys.stderr))
    redactor = SecretRedactor()
    try:
        config = tconfig.load(a.workspace)
        for w in config.warnings:
            printer(f"warning: {w}")
        cls = backend or backend_class(config.tracker)
        now = getattr(a, "now", None)
        # apply / export-cases validate the input and read the record before the credentials (see _writing)
        secrets = {} if a.command in ("apply", "export-cases") else tconfig.credentials(config)
        redactor = SecretRedactor(secrets)
        recorder = None
        if a.record:
            stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
            recorder = Recorder(safe_path(a.workspace, "recordings", f"{stamp}-{a.command}"), redactor)
        ctx = Context(a.workspace, config, secrets, redactor, transport or urllib_transport, now, recorder,
                      printer=printer, **({"sleep": sleep} if sleep else {}))
        if a.command == "check":
            return run_check(ctx, cls)
        if a.command == "import-cases":
            return run_import(ctx, cls, a.query)
        if a.command == "bug-search":
            return run_bug_search(ctx, cls, a.draft, a.include_closed)
        # writing commands: credentials loaded lazily, after the input and the record were checked
        return _writing(ctx, cls, a)
    except TrackerError as e:
        err(redactor.text(f"\n{'NOTHING SENT -- ' if e.code in (2, 4) else ''}{e}"))
        for p in e.problems:
            err(redactor.text(f" - {p}"))
        return e.code


def _writing(ctx: Context, cls, a) -> int:
    """apply / export-cases: the input and the record first (no credential needed for exit 4, or for exit 0 when
    the batch was already sent), then the credentials, then the back-end."""

    def with_credentials(klass):
        def make(c: Context):
            if not c.secrets:
                secrets = tconfig.credentials(c.config)
                c.secrets.update(secrets)
                for name, value in secrets.items():
                    c.redactor.add(name, value)
            return klass(c)
        return make

    if a.command == "apply":
        return run_apply(ctx, with_credentials(cls), a.batch, a.yes)
    return run_export(ctx, with_credentials(cls), a.cases, a.yes)


__all__ = ["BACKENDS", "backend_class", "main", "run_apply", "run_export"]
