"""
Configuration of the tracker adapters: the `jira:` and `azure_devops:` sections of a workspace's project.yaml
(technique-jira-xray.md section 1, technique-azure-devops.md section 1).

`validate_section(proj)` is the read-only check shared with scripts/validate_project.py; `load(workspace)` returns
the configuration with its defaults, or raises ConfigError with every problem. Credentials are only NAMES of .env
variables (the keys ending in `_var`); `credentials()` reads their values from the workspace's .env, and only the
redaction and the HTTP client ever see them.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import ConfigError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import read_project, read_workspace_env

# tracker: value -> section of project.yaml (the key uses "_", as every key of project.yaml)
SECTIONS = {"jira": "jira", "azure-devops": "azure_devops"}
# trackers whose back-end is done (plan 10.2 jira, 10.3 azure-devops): until then validate_project.py warns that
# the tracker has no adapter yet, and a missing section is a warning instead of a problem
IMPLEMENTED: set[str] = {"jira"}
TASKS = {"jira": "10.2", "azure-devops": "10.3"}

SKIP = "skip"
XRAY_BUILTIN = ("TODO", "EXECUTING", "PASSED", "FAILED", "ABORTED")
# technique-azure-devops.md 4.1: the outcomes of a manual run (the others are for automated runs, refused)
ADO_OUTCOMES = ("Passed", "Failed", "Blocked", "NotApplicable", "NotExecuted", "Inconclusive", "Paused",
                "InProgress", "None")
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
JIRA_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")

DEFAULTS = {
    "jira": {
        "xray": {"base_url": "https://xray.cloud.getxray.app"},
        "cases": {"issue_type": "Test", "id_source": "key", "fields": {}},
        "results": {"summary": "{batch_id} · {environment} · {build}", "attach_evidence": False},
        "bugs": {"issue_type": "Bug", "fields": {}, "defaults": {}, "open_jql": "statusCategory != Done",
                 "statuses": [], "priorities": []},
    },
    "azure-devops": {
        "api_version": "7.1",
        "cases": {"work_item_type": "Test Case", "id_source": "id", "suite_ids": [], "fields": {}},
        "results": {"configurations": {}, "run_name": "{batch_id} · {environment} · {build}",
                    "attach_evidence": False},
        "bugs": {"work_item_type": "Bug", "fields": {}, "defaults": {}, "closed_states": ["Closed", "Removed"],
                 "states": [], "priorities": []},
    },
}


def is_question(status) -> bool:
    """`Question` (and variants such as "Question: ...") is never sent (methodology 8.5)."""
    return str(status or "").strip().lower().startswith("question")


def not_sent(target) -> bool:
    """A status mapped to `skip`, or left empty (unmapped): not sent, not an error (technique-jira-xray.md 4.1)."""
    return target in (None, "") or str(target).strip().lower() == SKIP


def _merge(defaults: dict, given: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in (given or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


# --- validation (read only; shared with scripts/validate_project.py) --------------------------------------------

class _Check:
    def __init__(self, section: str):
        self.section = section
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def problem(self, where: str, text: str):
        self.problems.append(f"{self.section}.{where}: {text}" if where else f"{self.section}: {text}")

    def warn(self, where: str, text: str):
        self.warnings.append(f"{self.section}.{where}: {text}" if where else f"{self.section}: {text}")

    def https(self, value, where: str, required: bool, no_path: bool = False):
        if value in (None, ""):
            if required:
                self.problem(where, "is missing (required)")
            return
        u = urlsplit(str(value))
        if u.scheme != "https" or not u.netloc:
            self.problem(where, f"{value!r} is not an https:// URL")
        elif no_path and u.path.strip("/"):
            self.problem(where, f"{value!r}: only the site, without a path")
        elif u.query or u.fragment:
            self.problem(where, f"{value!r}: without a query or fragment")

    def var(self, value, where: str, required: bool = True):
        if value in (None, ""):
            if required:
                self.problem(where, "is missing (required): the NAME of a .env variable")
        elif not (isinstance(value, str) and VARIABLE.fullmatch(value)):
            self.problem(where, f"{value!r} is not a .env variable name (only the name, never the value)")

    def text(self, value, where: str, required: bool = False):
        if value in (None, ""):
            if required:
                self.problem(where, "is missing (required)")
        elif not isinstance(value, str):
            self.problem(where, "must be a text")

    def texts(self, value, where: str):
        if value is not None and not (isinstance(value, list) and all(isinstance(x, str) and x.strip()
                                                                       for x in value)):
            self.problem(where, "must be a list of texts")

    def boolean(self, value, where: str):
        if value is not None and not isinstance(value, bool):
            self.problem(where, "must be true or false")

    def mapping(self, value, where: str, prefix: str):
        """model field -> tracker field; `<prefix>:<label prefix>` stores the value as a label/tag."""
        if value is None:
            return
        if not isinstance(value, dict):
            self.problem(where, "must be a map model field -> tracker field")
            return
        for k, v in value.items():
            if not (isinstance(v, str) and v.strip()):
                self.problem(f"{where}.{k}", "must be a field name")
            elif v.startswith(prefix + ":") and not v[len(prefix) + 1:].strip():
                self.problem(f"{where}.{k}", f"{v!r}: {prefix}: needs a prefix, e.g. {prefix}:platform-")
            elif " " in v.split(":", 1)[-1] and v.startswith(prefix + ":"):
                self.problem(f"{where}.{k}", f"{v!r}: a {prefix} prefix cannot have spaces")

    def status_map(self, value, where: str, allowed: tuple | None):
        """The project's vocabulary -> tracker status/outcome, `skip` or empty (not sent). Never Question."""
        if value in (None, {}):
            self.problem(where, "is missing (required): EVERY status of the project's vocabulary, mapped to a "
                                "tracker status or to skip")
            return
        if not isinstance(value, dict):
            self.problem(where, "must be a map project status -> tracker status")
            return
        skipped = []
        for status, target in value.items():
            if is_question(status):
                self.problem(where, f"{status!r} can never be mapped: resolve the Question before sending "
                                    "(methodology 8.5)")
            elif not_sent(target):
                skipped.append(str(status))
            elif not isinstance(target, str):
                self.problem(f"{where}.{status}", "must be a text (the tracker's status) or skip")
            elif allowed is not None and target not in allowed:
                self.problem(f"{where}.{status}", f"{target!r} outside {list(allowed)} (or skip)")
        if skipped:
            self.warn(where, f"{len(skipped)} status(es) not sent (skip or unmapped: they stay only in the run "
                             f"log): {skipped}")


def _evidence(c: _Check, results: dict, proj: dict):
    attach = results.get("attach_evidence")
    c.boolean(attach, "results.attach_evidence")
    if attach is True and proj.get("evidence") != "text+screenshot":
        c.warn("results.attach_evidence", "true with evidence: \"text\": only text files are sent, screenshots "
                                          "only with evidence: \"text+screenshot\" (decision of 28.09.2026)")


def _jira(s: dict, proj: dict) -> _Check:
    c = _Check("jira")
    c.https(s.get("site"), "site", True, no_path=True)
    key = s.get("project_key")
    if not key:
        c.problem("project_key", "is missing (required)")
    elif not (isinstance(key, str) and JIRA_KEY.fullmatch(key)):
        c.problem("project_key", f"{key!r} is not a Jira project key (e.g. DS)")
    c.var(s.get("user_var"), "user_var")
    c.var(s.get("token_var"), "token_var")
    xray = s.get("xray")
    if not isinstance(xray, dict):
        c.problem("xray", "is missing (required): client_id_var and client_secret_var")
        xray = {}
    c.https(xray.get("base_url"), "xray.base_url", False, no_path=True)
    c.var(xray.get("client_id_var"), "xray.client_id_var")
    c.var(xray.get("client_secret_var"), "xray.client_secret_var")

    cases = s.get("cases") or {}
    if not isinstance(cases, dict):
        c.problem("cases", "must be a map")
        cases = {}
    source = cases.get("id_source") or "key"
    if source not in ("key", "field", "label"):
        c.problem("cases.id_source", f"{source!r} outside ['key', 'field', 'label']")
    elif source == "field" and not cases.get("id_field"):
        c.problem("cases.id_field", "is missing: id_source field needs the custom field (e.g. customfield_10050)")
    elif source == "label" and not cases.get("id_label_prefix"):
        c.problem("cases.id_label_prefix", "is missing: id_source label needs the prefix (e.g. qa-id-)")
    for k in ("issue_type", "jql", "folder", "id_field", "id_label_prefix"):
        c.text(cases.get(k), f"cases.{k}")
    c.mapping(cases.get("fields"), "cases.fields", "labels")

    results = s.get("results") or {}
    if not isinstance(results, dict):
        c.problem("results", "must be a map")
        results = {}
    c.status_map(results.get("statuses"), "results.statuses", None)
    plan = results.get("test_plan_key")
    if plan not in (None, "") and not (isinstance(plan, str) and ISSUE_KEY.fullmatch(plan)):
        c.problem("results.test_plan_key", f"{plan!r} is not an issue key (e.g. DS-10)")
    c.text(results.get("summary"), "results.summary")
    _evidence(c, results, proj)

    bugs = s.get("bugs") or {}
    if not isinstance(bugs, dict):
        c.problem("bugs", "must be a map")
        bugs = {}
    c.mapping(bugs.get("fields"), "bugs.fields", "labels")
    for k in ("issue_type", "open_jql", "link_type"):
        c.text(bugs.get(k), f"bugs.{k}")
    c.texts(bugs.get("statuses"), "bugs.statuses")
    c.texts(bugs.get("priorities"), "bugs.priorities")
    if bugs.get("defaults") is not None and not isinstance(bugs["defaults"], dict):
        c.problem("bugs.defaults", "must be a map field -> value")
    return c


def _azure_devops(s: dict, proj: dict) -> _Check:
    c = _Check("azure_devops")
    c.https(s.get("organization_url"), "organization_url", True)
    c.text(s.get("project"), "project", required=True)
    c.var(s.get("pat_var"), "pat_var")
    version = s.get("api_version")
    if version not in (None, "") and not (isinstance(version, str)
                                          and re.fullmatch(r"\d+\.\d+(-preview(\.\d+)?)?", version)):
        c.problem("api_version", f"{version!r}: a quoted version, e.g. \"7.1\"")

    cases = s.get("cases") or {}
    if not isinstance(cases, dict):
        c.problem("cases", "must be a map")
        cases = {}
    plan = cases.get("plan_id")
    if plan in (None, ""):
        c.problem("cases.plan_id", "is missing (required): the test plan of the cases and results")
    elif not (isinstance(plan, int) and not isinstance(plan, bool) and plan > 0):
        c.problem("cases.plan_id", f"{plan!r} must be a positive integer")
    suites = cases.get("suite_ids")
    if suites is not None and not (isinstance(suites, list) and all(isinstance(x, int) and x > 0 for x in suites)):
        c.problem("cases.suite_ids", "must be a list of suite ids (integers)")
    new_suite = cases.get("new_cases_suite_id")
    if new_suite not in (None, "", 0) and not (isinstance(new_suite, int) and new_suite > 0):
        c.problem("cases.new_cases_suite_id", f"{new_suite!r} must be a positive integer")
    source = cases.get("id_source") or "id"
    if source not in ("id", "field", "tag"):
        c.problem("cases.id_source", f"{source!r} outside ['id', 'field', 'tag']")
    elif source == "field" and not cases.get("id_field"):
        c.problem("cases.id_field", "is missing: id_source field needs the field (e.g. Custom.QaId)")
    elif source == "tag" and not cases.get("id_tag_prefix"):
        c.problem("cases.id_tag_prefix", "is missing: id_source tag needs the prefix (e.g. qa-id-)")
    for k in ("work_item_type", "area_path", "id_field", "id_tag_prefix"):
        c.text(cases.get(k), f"cases.{k}")
    c.mapping(cases.get("fields"), "cases.fields", "tag")

    results = s.get("results") or {}
    if not isinstance(results, dict):
        c.problem("results", "must be a map")
        results = {}
    c.status_map(results.get("outcomes"), "results.outcomes", ADO_OUTCOMES)
    conf = results.get("configurations")
    if conf is not None and not (isinstance(conf, dict) and all(isinstance(v, str) and v.strip()
                                                                  for v in conf.values())):
        c.problem("results.configurations", "must be a map platform -> Test Configuration name")
    c.text(results.get("run_name"), "results.run_name")
    _evidence(c, results, proj)

    bugs = s.get("bugs") or {}
    if not isinstance(bugs, dict):
        c.problem("bugs", "must be a map")
        bugs = {}
    c.mapping(bugs.get("fields"), "bugs.fields", "tag")
    for k in ("work_item_type", "area_path", "iteration_path"):
        c.text(bugs.get(k), f"bugs.{k}")
    for k in ("closed_states", "states", "priorities"):
        c.texts(bugs.get(k), f"bugs.{k}")
    if bugs.get("defaults") is not None and not isinstance(bugs["defaults"], dict):
        c.problem("bugs.defaults", "must be a map field -> value")
    return c


def validate_section(proj: dict) -> tuple[list[str], list[str]]:
    """(problems, warnings) of the tracker's section, for tracker: jira / azure-devops ([], [] otherwise).
    Read only. A missing section is a problem once the tracker's back-end is done (IMPLEMENTED); before, it is
    not reported here (manual logging still works, methodology 4.2: validate_project.py's "no adapter yet" warning
    says so)."""
    tracker = proj.get("tracker")
    if tracker not in SECTIONS:
        return [], []
    name = SECTIONS[tracker]
    section = proj.get(name)
    if section in (None, {}):
        if tracker not in IMPLEMENTED:
            return [], []
        technique = "jira-xray" if tracker == "jira" else "azure-devops"
        return [f"tracker {tracker!r} without the {name}: section (technique-{technique}.md section 1)"], []
    if not isinstance(section, dict):
        return [f"{name}: must be a map"], []
    c = _jira(section, proj) if tracker == "jira" else _azure_devops(section, proj)
    return c.problems, c.warnings


# --- loading -----------------------------------------------------------------------------------------------------

@dataclass
class TrackerConfig:
    tracker: str                 # jira | azure-devops
    section: str                 # jira | azure_devops
    cfg: dict                    # the section, with the defaults
    project: dict                # the whole project.yaml
    workspace: str
    warnings: list[str] = field(default_factory=list)

    @property
    def status_map(self) -> dict:
        """The project's vocabulary -> tracker status/outcome (or skip/empty: not sent)."""
        key = "statuses" if self.tracker == "jira" else "outcomes"
        return dict(self.cfg["results"].get(key) or {})

    @property
    def status_map_name(self) -> str:
        return f"{self.section}.results.{'statuses' if self.tracker == 'jira' else 'outcomes'}"

    @property
    def bug_statuses(self) -> list:
        """What bug_status may ask for (resolved_bugs rule, methodology 9.3)."""
        return list(self.cfg["bugs"].get("statuses" if self.tracker == "jira" else "states") or [])

    @property
    def bug_statuses_name(self) -> str:
        return f"{self.section}.bugs.{'statuses' if self.tracker == 'jira' else 'states'}"

    @property
    def priorities(self) -> list:
        return list(self.cfg["bugs"].get("priorities") or [])

    @property
    def environments(self) -> dict:
        envs = self.project.get("environments")
        return envs if isinstance(envs, dict) else {}

    @property
    def evidence_policy(self) -> str:
        return str(self.project.get("evidence") or "text")

    @property
    def attach_evidence(self) -> bool:
        return self.cfg["results"].get("attach_evidence") is True

    def credential_names(self) -> list[str]:
        """The `*_var` names of the section, in a fixed order (only names)."""
        from common import variable_names
        return variable_names(self.cfg)


def load(workspace: str) -> TrackerConfig:
    """The tracker's configuration of the workspace. ConfigError (exit 4) with every problem when project.yaml's
    tracker is not jira/azure-devops or its section is invalid."""
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise ConfigError(str(e)) from None
    tracker = proj.get("tracker")
    if tracker == "excel":
        raise ConfigError("tracker: excel uses its own adapter: python -m adapters.excel.apply <workspace> <batch>")
    if tracker not in SECTIONS:
        raise ConfigError(f"tracker {tracker!r}: this adapter serves {list(SECTIONS)} (project.yaml's tracker:)")
    problems, warnings = validate_section(proj)
    name = SECTIONS[tracker]
    if not problems and proj.get(name) in (None, {}):
        problems = [f"tracker {tracker!r} without the {name}: section in project.yaml"]
    envs = proj.get("environments")
    if not isinstance(envs, dict) or not envs:
        problems.append("environments: empty (the run's environment must be one of its keys)")
    if problems:
        raise ConfigError(f"invalid configuration ({len(problems)} problem(s))", problems)
    return TrackerConfig(tracker, name, _merge(DEFAULTS[tracker], proj[name]), proj, workspace, warnings)


def credentials(config: TrackerConfig) -> dict[str, str]:
    """name -> value of every `*_var` of the section, from the workspace's .env. ConfigError (exit 4) naming the
    missing variables, never a value."""
    env = read_workspace_env(config.workspace)
    names = config.credential_names()
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise ConfigError(f"the workspace's .env misses (or has empty) {', '.join(missing)}: fill them from your "
                          "own secret store (never from a chat, a log or the repository)")
    return {n: env[n] for n in names}
