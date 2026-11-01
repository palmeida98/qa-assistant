"""
The `tickets:` section of a workspace's project.yaml (docs/technique-tickets.md section 2). Independent of `tracker:`:
it needs no test plan and no Xray credential, so it does not go through adapters/tracker/config.load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from common import read_project, read_workspace_env

from adapters.tracker.config import _Check
from adapters.tracker.errors import ConfigError

SERVICES = ("azure-devops", "jira")
ACCESS = ("api", "browser")
DEFAULT_LIMITS = {"max_depth": 2, "max_items": 30, "max_comments": 200, "max_history": 500}
DEFAULT_CLOSED_STATES = ["Done", "Closed", "Resolved", "Removed", "Completed"]
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
API_VERSION = "7.1"


@dataclass
class Source:
    id: str
    service: str
    access: str
    cfg: dict

    @property
    def org(self) -> str:
        """The organisation (Azure DevOps) or site (Jira) that identifies a ticket together with the project."""
        url = urlsplit(self.cfg["organization_url"] if self.service == "azure-devops" else self.cfg["site"])
        if self.service == "azure-devops" and url.path.strip("/"):
            return url.path.strip("/").split("/")[0]
        return url.netloc.split(".")[0] if self.service == "azure-devops" else url.netloc

    @property
    def base_url(self) -> str:
        return str(self.cfg["organization_url"] if self.service == "azure-devops" else self.cfg["site"]).rstrip("/")

    def credential_names(self) -> list[str]:
        if self.access != "api":
            return []
        return [self.cfg["pat_var"]] if self.service == "azure-devops" else [self.cfg["user_var"], self.cfg["token_var"]]


@dataclass
class TicketsConfig:
    workspace: str
    sources: dict[str, Source]
    scopes: dict[str, dict] = field(default_factory=dict)
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))


def _validate_source(sid: str, s, c: _Check) -> None:
    where = f"sources.{sid}"
    if not SOURCE_ID.fullmatch(str(sid)):
        c.problem(where, "the id must be letters, digits, '-' or '_'")
    if not isinstance(s, dict):
        c.problem(where, "must be a map")
        return
    service, access = s.get("service"), s.get("access") or "api"
    if service not in SERVICES:
        c.problem(f"{where}.service", f"{service!r} outside {list(SERVICES)}")
        return
    if access not in ACCESS:
        c.problem(f"{where}.access", f"{access!r} outside {list(ACCESS)}")
    api = access == "api"
    if service == "azure-devops":
        c.https(s.get("organization_url"), f"{where}.organization_url", True)
        c.text(s.get("project"), f"{where}.project", required=True)
        c.var(s.get("pat_var"), f"{where}.pat_var", required=api)
        c.text(s.get("team"), f"{where}.team")
        c.texts(s.get("closed_states"), f"{where}.closed_states")
    else:
        c.https(s.get("site"), f"{where}.site", True, no_path=True)
        c.text(s.get("project_key"), f"{where}.project_key")
        c.var(s.get("user_var"), f"{where}.user_var", required=api)
        c.var(s.get("token_var"), f"{where}.token_var", required=api)
    fields_ = s.get("fields")
    if fields_ is not None and not (isinstance(fields_, dict) and all(isinstance(v, str) and v.strip()
                                                                       for v in fields_.values())):
        c.problem(f"{where}.fields", "must be a map label -> field reference")


def validate_section(proj: dict) -> tuple[list[str], list[str]]:
    """(problems, warnings) of `tickets:` ([], [] when absent). Read only; shared with scripts/validate_project.py."""
    section = proj.get("tickets")
    if section in (None, {}):
        return [], []
    c = _Check("tickets")
    if not isinstance(section, dict):
        c.problem("", "must be a map")
        return c.problems, c.warnings
    sources = section.get("sources")
    if not isinstance(sources, dict) or not sources:
        c.problem("sources", "is missing (required): at least one source")
        sources = {}
    for sid, s in sources.items():
        _validate_source(str(sid), s, c)
    scopes = section.get("scopes") or {}
    if not isinstance(scopes, dict):
        c.problem("scopes", "must be a map name -> {source, query | current_iteration}")
        scopes = {}
    for name, scope in scopes.items():
        where = f"scopes.{name}"
        if not isinstance(scope, dict):
            c.problem(where, "must be a map")
            continue
        src = sources.get(scope.get("source"))
        if scope.get("source") not in sources:
            c.problem(f"{where}.source", f"{scope.get('source')!r} is not one of the sources")
        if bool(scope.get("query")) == bool(scope.get("current_iteration")):
            c.problem(where, "needs exactly one of query or current_iteration")
        if scope.get("current_iteration") and isinstance(src, dict):
            if src.get("service") != "azure-devops":
                c.problem(f"{where}.current_iteration", "is for Azure DevOps; in Jira write the JQL (e.g. "
                                                        "sprint in openSprints()) with the board's project")
            elif not src.get("team"):
                c.problem(f"{where}.current_iteration", "needs the source's team: a sprint belongs to a team")
        c.boolean(scope.get("expand"), f"{where}.expand")
    cases = section.get("cases") or {}
    if not isinstance(cases, dict):
        c.problem("cases", "must be a map {link_fields: [...]}")
    else:
        c.texts(cases.get("link_fields"), "cases.link_fields")
    limits = section.get("limits") or {}
    if not isinstance(limits, dict):
        c.problem("limits", "must be a map")
        limits = {}
    for k, v in limits.items():
        if k not in DEFAULT_LIMITS:
            c.problem(f"limits.{k}", f"outside {list(DEFAULT_LIMITS)}")
        elif not (isinstance(v, int) and not isinstance(v, bool) and v > 0):
            c.problem(f"limits.{k}", "must be a positive integer")
    return c.problems, c.warnings


def _from_tracker(proj: dict) -> dict | None:
    """The tracker's own section as one API source, when the project has no `tickets:`."""
    tracker = proj.get("tracker")
    s = proj.get("azure_devops") if tracker == "azure-devops" else proj.get("jira") if tracker == "jira" else None
    if not isinstance(s, dict):
        return None
    keep = ("organization_url", "project", "pat_var", "api_version") if tracker == "azure-devops" else \
        ("site", "project_key", "user_var", "token_var")
    return {"sources": {tracker: {"service": tracker, "access": "api", **{k: s[k] for k in keep if k in s}}}}


def load(workspace: str) -> TicketsConfig:
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise ConfigError(str(e)) from None
    problems, _ = validate_section(proj)
    if problems:
        raise ConfigError(f"invalid tickets: section ({len(problems)} problem(s))", problems)
    section = proj.get("tickets") or _from_tracker(proj)
    if not section:
        raise ConfigError("no ticket source: this project has no tickets: section in project.yaml, and its tracker "
                          f"({proj.get('tracker')!r}) is not jira or azure-devops (docs/technique-tickets.md section 2)")
    sources = {str(sid): Source(str(sid), s["service"], s.get("access") or "api",
                                {**s, "api_version": str(s.get("api_version") or API_VERSION)})
               for sid, s in section["sources"].items()}
    for source in sources.values():
        if source.service == "azure-devops":
            source.cfg.setdefault("closed_states", DEFAULT_CLOSED_STATES)
    return TicketsConfig(workspace, sources, dict(section.get("scopes") or {}),
                         {**DEFAULT_LIMITS, **(section.get("limits") or {})})


def credentials(config: TicketsConfig, source: Source) -> dict[str, str]:
    """name -> value of the source's credentials. ConfigError naming what is missing, never a value."""
    env = read_workspace_env(config.workspace)
    names = source.credential_names()
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise ConfigError(f"source {source.id!r}: the workspace's .env misses (or has empty) {', '.join(missing)}: "
                          "fill them from your own secret store, or use access: browser (technique section 4.3)")
    return {n: env[n] for n in names}


def credentials_state(config: TicketsConfig, source: Source) -> dict[str, bool]:
    env = read_workspace_env(config.workspace)
    return {n: bool(env.get(n)) for n in source.credential_names()}
