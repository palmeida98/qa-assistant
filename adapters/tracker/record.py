"""
What the tracker adapters keep in the workspace (technique-jira-xray.md sections 2.1, 2.5, 3 and 6):

- `tracker/sent/<batch_id>.json` — the record of what was sent: the batch's hash (the same `batch_hash` as the
  Excel adapter, or the `translated_from` identity), and each write the tracker confirmed, saved after EACH one;
- `tracker/ids.yaml` — the cache of the case ID mapping (the mapping itself lives in the tracker);
- `tracker/cases.json` — the imported cases (internal model, methodology 2).

Never under history/ (methodology 13): every path is checked before writing. The record keeps keys and statuses,
never credentials; what it keeps goes through the redaction.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

import yaml

from .errors import ConfigError, InvalidInput

FOLDER = "tracker"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _now(now: datetime | None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def safe_path(workspace: str, *parts: str) -> str:
    """A path under <workspace>/tracker/, refused if it resolves inside history/ (e.g. through a link)."""
    path = os.path.join(workspace, FOLDER, *parts)
    real, history = os.path.realpath(path), os.path.realpath(os.path.join(workspace, "history"))
    if real == history or real.startswith(history + os.sep):
        raise ConfigError(f"{path} resolves inside history/, which is immutable (methodology 13)")
    return path


def _write_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


class SentRecord:
    """The record of one batch (or cases file) sent to the tracker."""

    def __init__(self, workspace: str, batch_id: str, batch_hash: str, tracker: str, redactor=None,
                 now: datetime | None = None):
        if not NAME.fullmatch(batch_id or ""):
            raise InvalidInput(f"batch id {batch_id!r}: the file name must be letters, digits, '.', '_', '-'")
        self.workspace = workspace
        self.batch_id = batch_id
        self.hash = batch_hash
        self.tracker = tracker
        self.redactor = redactor
        self.now = now
        self.path = safe_path(workspace, "sent", f"{batch_id}.json")
        self.data: dict | None = None
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8-sig") as f:
                self.data = json.load(f)

    @property
    def exists(self) -> bool:
        return self.data is not None

    @property
    def complete(self) -> bool:
        return bool(self.data and self.data.get("complete"))

    @property
    def writes(self) -> list[dict]:
        return list((self.data or {}).get("writes") or [])

    def state(self) -> str:
        """"new", "complete" (nothing to do: exit 0, no request) or "incomplete" (continue after the last confirmed
        write). The same id with another content, or another tracker, is refused (exit 4)."""
        if self.data is None:
            return "new"
        if self.data.get("hash") != self.hash:
            raise InvalidInput(
                f"{self.batch_id} was already sent with ANOTHER content (record {self.path}): a correction goes in "
                "a new batch, with another name, with only the cases to correct (and `bug` instead of "
                "`new_defect` for a bug already created)")
        if self.data.get("tracker") != self.tracker:
            raise InvalidInput(f"{self.batch_id} was sent to {self.data.get('tracker')!r}, not {self.tracker!r}")
        return "complete" if self.complete else "incomplete"

    def _save(self) -> None:
        data = self.redactor.json(self.data) if self.redactor is not None else self.data
        _write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    def start(self, **info) -> None:
        """Creates the record before the first write (or keeps an incomplete one, whose writes stay)."""
        if self.data is None:
            self.data = {"batch_id": self.batch_id, "hash": self.hash, "tracker": self.tracker,
                         "started_at": _now(self.now), "complete": False, "writes": []}
        self.data.update({k: v for k, v in info.items() if k not in ("writes", "hash", "tracker", "complete")})
        self._save()

    def add(self, kind: str, case: str | None = None, **ref) -> dict:
        """One write the tracker confirmed (e.g. kind "bug", case "DS-12", key "DS-200"); saved at once."""
        if self.data is None:
            self.start()
        entry = {"kind": kind, **({"case": case} if case is not None else {}), **ref, "at": _now(self.now)}
        self.data["writes"].append(entry)
        self._save()
        return entry

    def find(self, kind: str, case: str | None = None) -> list[dict]:
        return [w for w in self.writes if w.get("kind") == kind and (case is None or w.get("case") == case)]

    def finish(self, **info) -> None:
        if self.data is None:
            self.start()
        self.data.update(info)
        self.data["complete"] = True
        self.data["finished_at"] = _now(self.now)
        self._save()


class IdCache:
    """`tracker/ids.yaml`: internal case ID -> the tracker's references ({key, id} in Jira; {id, points} in Azure
    DevOps). A cache: `apply` always confirms it against the tracker (technique-jira-xray.md section 3)."""

    def __init__(self, workspace: str, tracker: str):
        self.tracker = tracker
        self.path = safe_path(workspace, "ids.yaml")
        self.cases: dict[str, dict] = {}
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8-sig") as f:
                doc = yaml.safe_load(f) or {}
            if doc.get("tracker") not in (None, tracker):
                raise ConfigError(f"{self.path} is for tracker {doc.get('tracker')!r}, not {tracker!r}")
            self.cases = {str(k): dict(v or {}) for k, v in (doc.get("cases") or {}).items()}

    def get(self, case_id: str) -> dict | None:
        return self.cases.get(str(case_id))

    def set(self, case_id: str, **refs) -> None:
        self.cases[str(case_id)] = {k: v for k, v in refs.items() if v is not None}

    def save(self) -> str:
        doc = {"tracker": self.tracker, "cases": {k: self.cases[k] for k in sorted(self.cases)}}
        _write_atomic(self.path, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=None))
        return self.path


def write_cases(workspace: str, tracker: str, cases: list[dict], flags: list[dict] | None = None,
                now: datetime | None = None) -> str:
    """`tracker/cases.json`: the imported cases (internal model, methodology 2) and the flags raised."""
    path = safe_path(workspace, "cases.json")
    doc = {"tracker": tracker, "imported_at": _now(now), "cases": cases, "flags": flags or []}
    _write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return path


def read_cases(workspace: str) -> dict:
    path = safe_path(workspace, "cases.json")
    if not os.path.isfile(path):
        return {"cases": [], "flags": []}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)
