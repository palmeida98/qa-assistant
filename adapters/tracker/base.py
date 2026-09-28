"""
Common base of the tracker adapters (technique-jira-xray.md section 2; plan 10.1): the batch and its validation
(section 2.6), the status mapping with what is not sent (section 4.1), the markers (section 2.5), the evidence
selection (section 8), the plan a dry run prints (section 2.3), and `TrackerAdapter`, the class each back-end
(adapters/jira, adapters/azure_devops) extends with the four directions.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from adapters.excel.base import batch_hash

from .config import TASKS, TrackerConfig, is_question, not_sent
from .errors import InvalidInput, NotImplementedYet
from .http import HttpClient, Recorder, SecretRedactor, Transport, urllib_transport

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
IMAGE_OR_VIDEO = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".webm", ".mov", ".avi", ".mkv")


# --- markers (section 2.5) -----------------------------------------------------------------------------------------

def _label(text: str) -> str:
    return re.sub(r"\s+", "-", str(text).strip())


def batch_marker(batch_id: str) -> str:
    """Label (Jira) / tag (Azure DevOps) on everything the adapter creates for a batch."""
    return f"qa-batch-{_label(batch_id)}"


def case_marker(case_id: str) -> str:
    """Label / tag of a bug created for a case."""
    return f"qa-case-{_label(case_id)}"


# --- the batch (sections 2.6 and 2.8) --------------------------------------------------------------------------------

@dataclass
class Item:
    index: int
    tc: str
    status: str
    target: str | None      # the tracker's status/outcome; None = not sent
    data: dict


@dataclass
class Batch:
    batch_id: str
    path: str
    doc: dict
    hash: str
    run: dict
    items: list[Item]

    @property
    def to_send(self) -> list[Item]:
        return [i for i in self.items if i.target is not None]

    @property
    def not_sent(self) -> list[Item]:
        return [i for i in self.items if i.target is None]


def read_json(path: str, key: str, what: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise InvalidInput(f"{what} not found: {path}") from None
    except ValueError as e:
        raise InvalidInput(f"{path}: not valid JSON ({e})") from None
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise InvalidInput(f"{path}: expected {{\"{key}\": [...]}}")
    return data


def resolve_input(workspace: str, path: str) -> str:
    """A path as given, or relative to the workspace (as adapters.excel.apply)."""
    if os.path.exists(path):
        return path
    alt = os.path.join(workspace, path)
    return alt if os.path.exists(alt) else path


def evidence_path(workspace: str, rel: str) -> str | None:
    """The absolute path of an evidence file, or None when it is outside the workspace's evidence/."""
    base = os.path.realpath(os.path.join(workspace, "evidence"))
    real = os.path.realpath(os.path.join(workspace, rel))
    return real if real.startswith(base + os.sep) else None


def _evidence_list(value) -> list | None:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) and x for x in value):
        return value
    return None


def validate_batch(doc: dict, config: TrackerConfig) -> list[str]:
    """Read only: every problem that prevents sending the batch (empty = it can be resolved and sent)."""
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        return ["empty batch"]
    problems = []
    run = doc.get("run")
    envs = config.environments
    if not isinstance(run, dict):
        problems.append("'run' is missing: {\"environment\": <key of environments>, \"build\": <version>} at the "
                        "top of the run log (methodology 7.1)")
    else:
        env = run.get("environment")
        if env in (None, ""):
            problems.append("run.environment is missing (methodology 7.1)")
        elif env not in envs:
            problems.append(f"run.environment {env!r} is not a key of environments in project.yaml: {list(envs)}")
        if run.get("build") in (None, ""):
            problems.append("run.build is missing: the version/build tested (methodology 9.2)")
    statuses = config.status_map
    bug_statuses, priorities = config.bug_statuses, config.priorities
    seen: dict[str, int] = {}
    for i, item in enumerate(results, 1):
        if not isinstance(item, dict):
            problems.append(f"item {i}: not an object")
            continue
        tc = str(item.get("tc") or "").strip()
        where = f"item {i} ({tc or 'no tc'})"
        if not tc:
            problems.append(f"{where}: 'tc' is missing")
        else:
            seen[tc] = seen.get(tc, 0) + 1
        status = item.get("status")
        if status in (None, ""):
            problems.append(f"{where}: 'status' is missing")
        elif is_question(status):
            problems.append(f"{where}: status {status!r} -- resolve the Question before sending (methodology 8.5)")
        elif status not in statuses:
            problems.append(f"{where}: status {status!r} is not a key of {config.status_map_name} "
                            f"{list(statuses)}")
        new = item.get("new_defect")
        if new not in (None, {}):
            if not isinstance(new, dict):
                problems.append(f"{where}: 'new_defect' must be an object or null")
            else:
                missing = [k for k in ("title", "expected", "observed") if new.get(k) in (None, "")]
                if missing:
                    problems.append(f"{where}: the new_defect misses {missing} (methodology 9.2)")
                if priorities and new.get("priority") not in (None, "") and new["priority"] not in priorities:
                    problems.append(f"{where}: priority of the new_defect {new['priority']!r} outside "
                                    f"{priorities}")
        bug_status = item.get("bug_status")
        if bug_status not in (None, ""):
            if item.get("bug") in (None, ""):
                problems.append(f"{where}: bug_status without a 'bug' (the existing bug it applies to)")
            if bug_status not in bug_statuses:
                problems.append(f"{where}: bug_status {bug_status!r} outside {config.bug_statuses_name} "
                                f"{bug_statuses}")
        files = _evidence_list(item.get("evidence"))
        if files is None:
            problems.append(f"{where}: 'evidence' must be a path or a list of paths under evidence/")
        for rel in files or []:
            real = evidence_path(config.workspace, rel)
            if real is None:
                problems.append(f"{where}: evidence {rel!r} is outside the workspace's evidence/")
            elif not os.path.isfile(real):
                problems.append(f"{where}: evidence {rel!r} does not exist")
            elif config.attach_evidence and os.path.getsize(real) > MAX_EVIDENCE_BYTES:
                problems.append(f"{where}: evidence {rel!r} is over {MAX_EVIDENCE_BYTES // (1024 * 1024)} MB")
    dup = sorted(tc for tc, n in seen.items() if n > 1)
    if dup:
        problems.append(f"case repeated in the batch: {dup}")
    return problems


def prepare_batch(path: str, config: TrackerConfig) -> Batch:
    """Reads and validates the run log; InvalidInput (exit 4) with every problem."""
    doc = read_json(path, "results", "batch")
    problems = validate_batch(doc, config)
    if problems:
        raise InvalidInput(f"invalid batch ({len(problems)} problem(s)): nothing resolved nor sent", problems)
    statuses = config.status_map
    items = []
    for i, item in enumerate(doc["results"], 1):
        target = statuses[item["status"]]
        items.append(Item(i, str(item["tc"]).strip(), item["status"], None if not_sent(target) else str(target),
                          item))
    batch_id = os.path.splitext(os.path.basename(path))[0]
    identity = doc.get("translated_from") or batch_hash(doc["results"])
    return Batch(batch_id, path, doc, identity, dict(doc["run"]), items)


def split_evidence(workspace: str, files: list[str], config: TrackerConfig) -> tuple[list[str], list[str]]:
    """(files to send, reasons of the files not sent) under the decision of 28.09.2026: nothing without
    attach_evidence; images and videos only with evidence: "text+screenshot" (they cannot be redacted)."""
    if not config.attach_evidence:
        return [], [f"{f}: attach_evidence is off" for f in files]
    send, skipped = [], []
    for f in files:
        if f.lower().endswith(IMAGE_OR_VIDEO) and config.evidence_policy != "text+screenshot":
            skipped.append(f"{f}: a screenshot/video goes only with evidence: \"text+screenshot\"")
        else:
            send.append(evidence_path(workspace, f) or f)
    return send, skipped


# --- export-cases input (section 7) -----------------------------------------------------------------------------------

def prepare_cases(path: str, config: TrackerConfig) -> tuple[dict, str]:
    """The cases file of export-cases, validated; returns (doc, hash)."""
    doc = read_json(path, "cases", "cases file")
    cases = doc["cases"]
    problems = []
    if not cases:
        problems.append("no cases")
    provisional = config.cfg["cases"].get("id_source") in ("key", "id")
    seen: dict[str, int] = {}
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            problems.append(f"case {i}: not an object")
            continue
        cid = str(case.get("id") or "").strip()
        where = f"case {i} ({cid or 'no id'})"
        if not cid:
            problems.append(f"{where}: 'id' is missing")
        else:
            seen[cid] = seen.get(cid, 0) + 1
            if provisional and not re.fullmatch(r"NEW-\d+", cid):
                problems.append(f"{where}: with id_source {config.cfg['cases']['id_source']!r} a new case has a "
                                "provisional id NEW-<n>; the tracker gives the final one")
        if not str(case.get("title") or "").strip():
            problems.append(f"{where}: 'title' is missing")
        steps = case.get("steps")
        if steps is not None and not (isinstance(steps, list) and all(isinstance(s, dict) for s in steps)):
            problems.append(f"{where}: 'steps' must be a list of {{action, data, expected}}")
    dup = sorted(c for c, n in seen.items() if n > 1)
    if dup:
        problems.append(f"case id repeated in the file: {dup}")
    if problems:
        raise InvalidInput(f"invalid cases file ({len(problems)} problem(s)): nothing resolved nor sent", problems)
    return doc, batch_hash(cases)


# --- the plan a dry run prints (section 2.3) -------------------------------------------------------------------------

GROUPS = ("new bugs", "results", "updated bugs", "links", "cases")


@dataclass
class PlannedWrite:
    group: str          # one of GROUPS
    method: str
    path: str
    summary: str
    case: str | None = None


@dataclass
class Plan:
    writes: list[PlannedWrite] = field(default_factory=list)
    not_sent: list[str] = field(default_factory=list)     # "<case> (<status>): <reason>"
    notes: list[str] = field(default_factory=list)        # e.g. a redaction that changed a comment
    data: dict = field(default_factory=dict)              # the back-end's own resolution, for the send

    def add(self, group: str, method: str, path: str, summary: str, case: str | None = None) -> None:
        self.writes.append(PlannedWrite(group, method, path, summary, case))

    def lines(self) -> list[str]:
        out = []
        for g in GROUPS + tuple(sorted({w.group for w in self.writes} - set(GROUPS))):
            ws = [w for w in self.writes if w.group == g]
            if ws:
                out.append(f"  {g} ({len(ws)}):")
                out += [f"    {w.method} {w.path} — {w.summary}" for w in ws]
        if self.not_sent:
            out.append(f"  not sent ({len(self.not_sent)}):")
            out += [f"    {x}" for x in self.not_sent]
        out += [f"  note: {n}" for n in self.notes]
        return out


# --- the context and the back-ends ---------------------------------------------------------------------------------

@dataclass
class Context:
    """What a back-end gets: the configuration, the credentials (names -> values, only for the client), the
    redaction, the transport (replaced in the tests), the fixed instant (--now), the recorder (--record) and the
    output function (every line printed goes through the redaction)."""

    workspace: str
    config: TrackerConfig
    secrets: dict[str, str]
    redactor: SecretRedactor
    transport: Transport = urllib_transport
    now: datetime | None = None
    recorder: Recorder | None = None
    sleep: Callable[[float], None] = time.sleep
    printer: Callable[[str], None] = print

    def out(self, text: str = "") -> None:
        self.printer(self.redactor.text(str(text)))

    def secret(self, key: str) -> str:
        """The value of the variable named by `<key>` of the section (e.g. "token_var", "xray.client_id_var")."""
        node = self.config.cfg
        for part in key.split("."):
            node = node[part]
        return self.secrets[node]

    def client(self, base_url: str, auth: Callable[[], dict] | None = None,
               reauth: Callable[[], None] | None = None, **kw) -> HttpClient:
        return HttpClient(self.transport, self.redactor, base_url, auth=auth, reauth=reauth, sleep=self.sleep,
                          recorder=self.recorder, **kw)


class TrackerAdapter:
    """A back-end. The command line (adapters/tracker/cli.py) validates the input, reads the record and prints the
    plan; the back-end resolves against the tracker (reads only) and, with --yes, writes, calling `record.add` after
    EACH confirmed write, and reads back. Every method a back-end does not override says which task builds it."""

    tracker = ""
    implemented = True

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.config = ctx.config
        self.cfg = ctx.config.cfg

    def _todo(self, what: str):
        task = TASKS.get(self.tracker, "a later task")
        raise NotImplementedYet(f"{self.tracker}: {what} is not implemented yet (plan {task})")

    # read only
    def check(self) -> list[str]:
        """What is missing in the tracker for this configuration (empty = ready)."""
        self._todo("check")

    def import_cases(self, query: str | None) -> dict:
        """Writes tracker/cases.json and ids.yaml; returns a summary ({"cases": n, "flags": [...]})."""
        self._todo("import-cases")

    def bug_search(self, draft: dict, include_closed: bool) -> list[dict]:
        """Candidates [{key, summary, status, matched: [...], pass: n}], most criteria matched first."""
        self._todo("bug-search")

    # apply: resolve (reads only) -> send (with --yes) -> read back
    def plan_apply(self, batch: Batch, record) -> Plan:
        self._todo("apply")

    def send_apply(self, batch: Batch, plan: Plan, record) -> None:
        self._todo("apply")

    def read_back(self, batch: Batch, plan: Plan, record) -> list[str]:
        """Differences between what was sent and what the tracker has (empty = it matches)."""
        self._todo("apply")

    # export-cases
    def plan_export(self, doc: dict, record) -> Plan:
        self._todo("export-cases")

    def send_export(self, doc: dict, plan: Plan, record) -> None:
        self._todo("export-cases")
