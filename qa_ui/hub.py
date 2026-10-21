"""
The hub (docs/qa-ui.md section 14): every project of qa-workspaces/ in one interface.

    default_root()                    -> <qa-assistant>/../qa-workspaces, the only root (14.1)
    discover(root)                    -> the projects: <root>/<company>/<project>/project.yaml, two levels (14.2)
    find(root, id)                    -> the project of an id, looked up in discover(), never joined from the id
    summary(project, pinned)          -> what the Projects screen shows of a project (14.4)
    Prefs(path).pinned() / .pin()     -> the pinned ids, in ~/.qa-assistant/qa-ui.json (14.10, 14.11)

Nothing is kept here that the root does not have: every call reads the folders again. Symbolic links (and
junctions on Windows) are never followed, so nothing outside the root is ever read.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import run_log
import yaml
from common import read_project

from . import ROOT, workspace
from .runs import Gate

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")      # a company's or a project's folder; never `.` or `_` first
PREFS = os.path.join(os.path.expanduser("~"), ".qa-assistant", "qa-ui.json")


class NotFound(workspace.NotFound):
    """No discovered project has that id (status 404)."""


@dataclass(frozen=True)
class Project:
    id: str                 # <company>/<project>
    company: str
    folder: str
    path: str


def default_root() -> str:
    """qa-workspaces/ next to this repository, where the methodology keeps the workspaces (user decision,
    21.10.2026): the hub has no other root."""
    return os.path.join(os.path.dirname(ROOT), "qa-workspaces")


def _is_plain_dir(entry: os.DirEntry) -> bool:
    if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
        return False
    return entry.is_dir(follow_symlinks=False) and bool(NAME.fullmatch(entry.name))


def _dirs(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return [e for e in it if _is_plain_dir(e)]
    except OSError:
        return []


def discover(root: str) -> list[Project]:
    """Every <root>/<company>/<project>/ with a project.yaml (a file, not a link), sorted by company and project.
    Anything else in the root is ignored."""
    out = []
    for company in _dirs(root):
        for proj in _dirs(company.path):
            yaml_path = os.path.join(proj.path, "project.yaml")
            if os.path.isfile(yaml_path) and not os.path.islink(yaml_path):
                out.append(Project(f"{company.name}/{proj.name}", company.name, proj.name,
                                   os.path.join(os.path.abspath(root), company.name, proj.name)))
    return sorted(out, key=lambda p: (p.company.lower(), p.folder.lower(), p.id))


def find(root: str, project_id: str) -> Project:
    """The discovered project of `project_id`. The id is compared with the list, never joined as a path: `..`, an
    absolute path, a third level or a project that is not discovered are all NotFound."""
    for p in discover(root):
        if p.id == project_id:
            return p
    raise NotFound(f"no project {project_id!r} in the root (docs/qa-ui.md section 14.2)")


def _needs_open(path: str, proj: dict, logs: list[dict]) -> int:
    """What needs the user in the run logs not yet applied (an applied batch can no longer be edited)."""
    total = 0
    for log in logs:
        if log["needs"] and not run_log.applied(path, proj, log["name"]):
            total += log["needs"]
    return total


def summary(project: Project, pinned: bool = False) -> dict:
    """A card of the Projects screen. A project.yaml that cannot be read gives the card its error and nothing
    else: it never stops the hub (section 14.2)."""
    out = {"id": project.id, "company": project.company, "folder": project.folder, "pinned": pinned,
           "name": project.folder, "tracker": None, "modules": [], "error": None,
           "runs": 0, "last_run": None, "needs": 0}
    try:
        proj = read_project(project.path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        # the parser names the file by its full path: the card says it relative to the project
        text = str(e).replace(os.path.join(project.path, ""), "").replace(project.path, ".")
        out["error"] = f"project.yaml cannot be read: {text}"
        return out
    if not isinstance(proj, dict):
        out["error"] = "project.yaml cannot be read: it is not a mapping of keys"
        return out
    out.update(name=str(proj.get("project") or project.folder), tracker=proj.get("tracker"),
               modules=[str(m) for m in proj.get("modules") or [] if isinstance(m, str)])
    try:
        logs = workspace.run_logs(project.path, proj)
    except (OSError, ValueError, KeyError) as e:
        out["error"] = f"the run logs cannot be read: {e}"
        return out
    out["runs"] = len(logs)
    if logs:
        last = logs[0]
        out["last_run"] = {k: last[k] for k in ("name", "batch", "modified", "counts", "total", "error")}
    out["needs"] = _needs_open(project.path, proj, logs)
    return out


class Prefs:
    """The hub's own file, outside every repository: the pinned project ids, nothing else (section 14.11)."""

    def __init__(self, path: str = PREFS):
        self.path = path

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def pinned(self) -> list[str]:
        return [p for p in self._read().get("pinned") or [] if isinstance(p, str)]

    def pin(self, project_id: str, pinned: bool) -> list[str]:
        ids = [p for p in self.pinned() if p != project_id]
        if pinned:
            ids.append(project_id)
        data = self._read()
        data["pinned"] = ids
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
        return ids


class Hub:
    """The hub's state: the root, the pins and the Gate every project's runner shares (one run at a time on the
    computer, section 14.3)."""

    def __init__(self, root: str, prefs: Prefs | None = None, gate: Gate | None = None, **runner_options):
        self.root = os.path.abspath(root)
        self.runner_options = runner_options                    # claude, max_turns, timeout_minutes (plan 13.2)
        self.prefs = prefs or Prefs()
        self.gate = gate or Gate()

    def projects(self) -> list[Project]:
        return discover(self.root)

    def find(self, project_id: str) -> Project:
        return find(self.root, project_id)

    def state(self) -> dict:
        """GET /api/hub: the projects with their summaries, the pins, the run in progress and the inbox's count."""
        pinned = set(self.prefs.pinned())
        projects = [summary(p, p.id in pinned) for p in self.projects()]
        busy = self.gate.current()
        run = dict(busy[1].public(), tail=[], project=busy[0].name) if busy else None
        return {"root": self.root, "projects": projects, "pinned": [p["id"] for p in projects if p["pinned"]],
                "run": run, "inbox": sum(p["needs"] for p in projects)}

    def pin(self, project_id: str, pinned: bool) -> dict:
        self.find(project_id)                                    # only a discovered project is pinned
        self.prefs.pin(project_id, pinned)
        return {"id": project_id, "pinned": pinned}
