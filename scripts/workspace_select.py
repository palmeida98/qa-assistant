"""
Which framework runs, which project a request means, and whether a skill can run there (technique-workspaces.md).

    python scripts/workspace_select.py where [--root PATH] [--json]
    python scripts/workspace_select.py list [--root PATH] [--json]
    python scripts/workspace_select.py resolve [<project>] [--workspace PATH] [--root PATH] [--json]
    python scripts/workspace_select.py preflight --skill NAME [<project>] [--workspace PATH] [--root PATH] [--json]
    python scripts/workspace_select.py skills [--json]
    python scripts/workspace_select.py set-root PATH

Everything is read only, except set-root (~/.qa-assistant/config.json). A root of workspaces is never created. Listing
reads only each project.yaml: no ticket, evidence, workbook, memory or .env. A skill's contract is its own frontmatter:
this script keeps no list of skills.

This file must run on an old checkout, without the modules it checks: what it needs beyond the standard library is
imported inside the functions, and a missing piece is exit 3, with what to do.

Exit codes:
  0  resolved, or the skill can run (missing tools, if any, are listed)
  1  found, but not usable: incomplete, not configured, inaccessible, or a configuration the skill needs is missing
  2  ambiguous: several candidates, listed; nothing is chosen
  3  the skill and the code do not match: stop, and do what the message says; nothing was installed or changed
  4  nothing to select: the root or the path does not exist, or a usage error
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "scripts"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CONFIG = os.path.join(os.path.expanduser("~"), ".qa-assistant", "config.json")
ROOT_ENV = "QA_WORKSPACES_ROOT"
CONFIG_ENV = "QA_ASSISTANT_CONFIG"
VALID, INCOMPLETE, NOT_CONFIGURED, INACCESSIBLE = "valid", "incomplete", "not-configured", "inaccessible"
ONBOARDING = "/project-onboarding"
CONTRACT = ("intent", "inputs", "config", "tools", "outputs", "writes", "approvals", "failures", "resume")
OK, NOT_USABLE, AMBIGUOUS, MISMATCH, NOTHING = 0, 1, 2, 3, 4
WALK_UP = 4
PINNED = ("openpyxl", "et-xmlfile", "pyyaml", "playwright", "pillow")


def _sync():
    import sync_skills
    return sync_skills


# --- the framework ---------------------------------------------------------------------------------------------------

def _inside(path: str, base: str) -> bool:
    from common import plain_path
    a = os.path.normcase(plain_path(os.path.realpath(path)))
    b = os.path.normcase(plain_path(os.path.realpath(base)))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:      # another drive
        return False


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def inside_framework(path: str, framework: str = ROOT) -> bool:
    """Inside this checkout or its main checkout, and not in examples/ (the demo, the one workspace in the repository)."""
    from common import main_checkout
    for base in (framework, main_checkout(framework)):
        if _inside(path, base):
            return not _inside(path, os.path.join(framework, "examples")) and \
                not _inside(path, os.path.join(base, "examples"))
    return False


def import_origin(module: str) -> str | None:
    """Where a module really comes from, without running it (its parent packages are imported to find it)."""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError, AttributeError):
        return None
    return spec.origin if spec else None


def versions_that_differ() -> list[dict]:
    """The packages pinned in requirements.txt and pyproject.toml whose installed version differs. Information only: a
    different version is not an incompatible one (technique-workspaces.md section 2)."""
    pins: dict[str, str] = {}
    for rel in ("requirements.txt", "pyproject.toml"):
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        for name, version in re.findall(r"([A-Za-z][A-Za-z0-9._-]*)==([0-9][A-Za-z0-9._-]*)", text):
            pins[name.lower()] = version
    out = []
    for name in PINNED:
        if name not in pins:
            continue
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            have = None
        if have != pins[name]:
            out.append({"package": name, "pinned": pins[name], "installed": have})
    return out


def where(root_arg: str | None = None, environ=None, config_path: str | None = None, target: str | None = None) -> dict:
    from common import git_state, main_checkout
    sync = _sync()
    main = main_checkout(ROOT)
    origins = {m: import_origin(m) for m in ("adapters", "qa_ui", "qa_audit", "openpyxl", "yaml")}
    framework_modules = ("adapters", "qa_ui", "qa_audit")
    elsewhere = [m for m in framework_modules if origins[m] and not _inside(origins[m], ROOT)]
    root, source = workspaces_root(root_arg, environ, config_path)
    target = target or sync.TARGET
    return {"framework": {"root": ROOT, "main_checkout": main, "worktree": not sync.same_path(ROOT, main),
                          **git_state(ROOT)},
            "python": {"version": ".".join(map(str, sys.version_info[:3])), "executable": sys.executable},
            "imports": origins, "imports_from_another_checkout": elsewhere, "versions_that_differ": versions_that_differ(),
            "workspaces_root": {"path": root, "source": source, "exists": os.path.isdir(root)},
            "skills": {"target": target, "record": sync.read_record(target)}}


def _print_where(info: dict) -> None:
    f = info["framework"]
    print(f"Framework: {f['root']} (branch {f['branch']}, commit {f['commit']}"
          f"{', uncommitted changes' if f['dirty'] else ''})")
    if f["worktree"]:
        print(f"  a worktree of {f['main_checkout']}")
    print(f"Python: {info['python']['version']} ({info['python']['executable']})")
    for module, origin in info["imports"].items():
        print(f"  import {module}: {origin or 'not found'}")
    for module in info["imports_from_another_checkout"]:
        print(f"warning: {module} is imported from another checkout, not from this one")
    for v in info["versions_that_differ"]:
        print(f"note: {v['package']} {v['installed'] or 'not installed'} here, pinned {v['pinned']} (a difference, "
              "not an incompatibility: the tests decide)")
    w = info["workspaces_root"]
    print(f"Root of workspaces: {w['path']} (from {w['source']}){'' if w['exists'] else ' -- it does not exist'}")
    record = info["skills"]["record"]
    print(f"Global skills: {info['skills']['target']}, " + (
        f"last synced from {record.get('root')} (branch {record.get('branch')}, commit {record.get('commit')}) at "
        f"{record.get('synced_at')}" if record else "no record of where they came from"))


# --- the root of workspaces ----------------------------------------------------------------------------------------

def _config_path(environ, config_path: str | None) -> str:
    return config_path or environ.get(CONFIG_ENV) or CONFIG


def read_config(environ=None, config_path: str | None = None) -> dict:
    environ = os.environ if environ is None else environ
    try:
        with open(_config_path(environ, config_path), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def workspaces_root(explicit: str | None = None, environ=None, config_path: str | None = None,
                    framework: str = ROOT) -> tuple[str, str]:
    """(the root, where the value came from). Never created here nor anywhere else (technique-workspaces.md section 3)."""
    from common import main_checkout
    environ = os.environ if environ is None else environ
    if explicit:
        return os.path.abspath(explicit), "--root"
    if environ.get(ROOT_ENV, "").strip():
        return os.path.abspath(environ[ROOT_ENV].strip()), f"${ROOT_ENV}"
    configured = read_config(environ, config_path).get("workspaces_root")
    if isinstance(configured, str) and configured.strip():
        return os.path.abspath(configured), _config_path(environ, config_path)
    return os.path.join(os.path.dirname(os.path.abspath(main_checkout(framework))), "qa-workspaces"), \
        "the folder next to the main checkout"


def missing_root_message(root: str, source: str) -> str:
    return (f"The root of workspaces {root} (from {source}) does not exist, and nothing was created. Say which one to "
            f"use: --root PATH, ${ROOT_ENV}, or `python scripts/workspace_select.py set-root PATH`; or name one "
            "workspace with --workspace PATH.")


def set_root(path: str, environ=None, config_path: str | None = None) -> tuple[int, str]:
    environ = os.environ if environ is None else environ
    if not os.path.isdir(path):
        return NOTHING, f"{path} does not exist: create it first, on purpose (this command creates no root)"
    if inside_framework(path):
        return NOTHING, f"{path} is inside the framework: workspaces live outside this repository"
    target = _config_path(environ, config_path)
    doc = read_config(environ, config_path)
    doc["workspaces_root"] = os.path.realpath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(target + ".tmp", target)
    return OK, f"root of workspaces: {doc['workspaces_root']} (kept in {target})"


# --- discovery -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    id: str                 # <company>/<project>, or <project> directly under the root
    company: str
    folder: str
    path: str
    status: str = VALID
    reason: str = ""
    name: str = ""
    tracker: str | None = None
    modules: tuple = ()
    configured: bool = True     # it has its own project.yaml, a file and not a link

    def public(self, root: str | None = None) -> dict:
        """What a list shows: the minimum needed to choose. The path relative to the root, when there is one."""
        out = asdict(self)
        out["modules"] = list(self.modules)
        if root:
            out["path"] = os.path.relpath(self.path, root).replace("\\", "/")
        out["proposes"] = ONBOARDING if self.needs_onboarding else None
        return out

    @property
    def needs_onboarding(self) -> bool:
        return self.status in (INCOMPLETE, NOT_CONFIGURED)


def classify(path: str) -> tuple[str, str, dict]:
    """(status, reason, project.yaml) of a folder. Reads only its project.yaml (technique-workspaces.md section 4)."""
    import validate_project
    import yaml
    from common import read_project
    try:
        os.listdir(path)
    except OSError as e:
        return INACCESSIBLE, f"the folder cannot be read ({e.strerror or e})", {}
    yaml_path = os.path.join(path, "project.yaml")
    if _is_link(yaml_path):
        return INACCESSIBLE, "project.yaml is a link, and links are not followed", {}
    if not os.path.isfile(yaml_path):
        if os.path.isfile(os.path.join(path, "projeto.yaml")):
            return INCOMPLETE, "a workspace from before the translation: scripts/migrate_workspace.py converts it", {}
        return NOT_CONFIGURED, "no project.yaml", {}
    try:
        proj = read_project(path)
    except OSError as e:
        return INACCESSIBLE, f"project.yaml cannot be read ({e.strerror or e})", {}
    except (ValueError, yaml.YAMLError):
        return INCOMPLETE, "project.yaml is not valid YAML", {}
    if not isinstance(proj, dict):
        return INCOMPLETE, "project.yaml is not a mapping of keys", {}
    problems, _ = validate_project.base_problems(proj)
    if problems:
        return INCOMPLETE, f"{len(problems)} problem(s) in project.yaml", proj
    return VALID, "", proj


def _candidate(pid: str, company: str, folder: str, path: str, read: bool) -> Candidate:
    yaml_path = os.path.join(path, "project.yaml")
    configured = os.path.isfile(yaml_path) and not _is_link(yaml_path)
    if not read:
        return Candidate(pid, company, folder, path, name=folder, configured=configured)
    status, reason, proj = classify(path)
    modules = tuple(str(m) for m in proj.get("modules") or [] if isinstance(m, str))
    return Candidate(pid, company, folder, path, status, reason, str(proj.get("project") or folder),
                     proj.get("tracker") if isinstance(proj.get("tracker"), str) else None, modules, configured)


def _entries(path: str) -> tuple[list[os.DirEntry], str | None]:
    """The folders of a folder whose names could be a project's, links included, and why it could not be read."""
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.name[:1] not in (".", "_") and (e.is_dir(follow_symlinks=False) or _is_link(e.path))], None
    except OSError as e:
        return [], e.strerror or str(e)


def discover(root: str, read: bool = True) -> list[Candidate]:
    """What the root holds, in its two layouts (technique-workspaces.md section 4): every folder that is a project or
    could become one, sorted by company and project. Links and junctions are never followed: they are listed as
    inaccessible. `read=False` only sees whether a project.yaml exists: every candidate then says `valid`, unchecked."""
    root = os.path.abspath(root)
    out: list[Candidate] = []
    entries, _ = _entries(root)
    for entry in entries:
        if _is_link(entry.path):
            out.append(Candidate(entry.name, "", entry.name, entry.path, INACCESSIBLE, "a link: links are not followed",
                                 entry.name, configured=False))
            continue
        if os.path.exists(os.path.join(entry.path, "project.yaml")):
            out.append(_candidate(entry.name, "", entry.name, entry.path, read))
            continue
        subfolders, error = _entries(entry.path)
        if error:
            out.append(Candidate(entry.name, "", entry.name, entry.path, INACCESSIBLE,
                                 f"the folder cannot be read ({error})", entry.name, configured=False))
        elif not subfolders:
            out.append(_candidate(entry.name, "", entry.name, entry.path, read))
        for sub in subfolders:
            pid = f"{entry.name}/{sub.name}"
            if _is_link(sub.path):
                out.append(Candidate(pid, entry.name, sub.name, sub.path, INACCESSIBLE,
                                     "a link: links are not followed", sub.name, configured=False))
            else:
                out.append(_candidate(pid, entry.name, sub.name, sub.path, read))
    return sorted(out, key=lambda c: (c.company.lower(), c.folder.lower(), c.id))


# --- selection -------------------------------------------------------------------------------------------------------

@dataclass
class Selection:
    outcome: str                        # selected | ambiguous | none
    how: str = ""
    candidate: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)
    root: str = ""
    root_source: str = ""
    message: str = ""

    def public(self) -> dict:
        return {"outcome": self.outcome, "how": self.how, "root": self.root, "root_source": self.root_source,
                "message": self.message, "project": self.candidate.public() if self.candidate else None,
                "candidates": [c.public(self.root) for c in self.candidates]}


def _matches(hint: str, candidates: list[Candidate]) -> list[Candidate]:
    wanted = hint.strip().replace("\\", "/").strip("/").casefold()
    if not wanted:
        return []
    for key in (lambda c: c.id, lambda c: c.folder, lambda c: c.name):
        exact = [c for c in candidates if key(c).casefold() == wanted]
        if exact:
            return exact
    return [c for c in candidates if wanted in c.id.casefold() or wanted in c.name.casefold()]


def _at(path: str, root: str) -> Candidate:
    """The candidate of one folder, with its id relative to the root when it lies there."""
    real = os.path.realpath(path)
    rel = os.path.relpath(real, os.path.realpath(root)).replace("\\", "/") if _inside(real, root) else os.path.basename(real)
    parts = rel.split("/")
    return _candidate(rel, parts[0] if len(parts) == 2 else "", parts[-1], real, True)


def _from_directory(cwd: str, root: str, framework: str) -> Candidate | None:
    """The current directory or the nearest parent with a project.yaml; a new folder inside the root too."""
    current = os.path.abspath(cwd)
    for _ in range(WALK_UP):
        if inside_framework(current, framework) or os.path.normcase(current) == os.path.normcase(os.path.abspath(root)):
            break
        if os.path.isfile(os.path.join(current, "project.yaml")):
            return _at(current, root)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    start = os.path.abspath(cwd)
    if _inside(start, root) and not _same_path(start, root):
        for candidate in discover(root):
            if _same_path(candidate.path, start):
                return candidate
    return None


def _same_path(a: str, b: str) -> bool:
    return _sync().same_path(a, b)


def resolve(hint: str | None = None, workspace: str | None = None, root: str | None = None, cwd: str | None = None,
            environ=None, config_path: str | None = None, framework: str = ROOT) -> Selection:
    """Which project a request means (technique-workspaces.md section 5). Nothing is chosen between candidates."""
    cwd = cwd or os.getcwd()
    the_root, source = workspaces_root(root, environ, config_path, framework)
    base = Selection("none", root=the_root, root_source=source)
    if hint and not workspace and (os.path.isabs(hint) or hint.startswith(".")) and os.path.isdir(os.path.join(cwd, hint)):
        workspace, hint = os.path.join(cwd, hint), None
    if workspace:
        path = os.path.realpath(os.path.join(cwd, workspace))
        if not os.path.isdir(path):
            base.message = f"{workspace} does not exist: nothing was created"
            return base
        if inside_framework(path, framework):
            base.message = f"{workspace} is inside the framework: a workspace lives outside this repository"
            return base
        base.outcome, base.how, base.candidate = "selected", "the path given", _at(path, the_root)
        return base
    root_exists = os.path.isdir(the_root)
    if hint:
        if not root_exists:
            base.message = missing_root_message(the_root, source)
            return base
        everything = discover(the_root)
        found = _matches(hint, everything)
        if len(found) == 1:
            base.outcome, base.how, base.candidate = "selected", f"the name {hint!r}", found[0]
        elif found:
            base.outcome, base.candidates = "ambiguous", found
            base.message = f"{hint!r} matches {len(found)} projects: which one?"
        else:
            base.candidates = everything
            base.message = f"no project matches {hint!r} in {the_root}"
        return base
    here = _from_directory(cwd, the_root, framework)
    if here:
        base.outcome, base.how, base.candidate = "selected", "the current directory", here
        return base
    if not root_exists:
        base.message = missing_root_message(the_root, source)
        return base
    everything = discover(the_root)
    if len(everything) == 1 and everything[0].status == VALID:
        base.outcome, base.how, base.candidate = "selected", "the only project in the root", everything[0]
    elif everything:
        base.outcome, base.candidates = "ambiguous", everything
        base.message = f"{len(everything)} folders in {the_root}: which project?"
    else:
        base.message = (f"no project in {the_root}: create its folder and run {ONBOARDING} there, or name one with "
                        "--workspace PATH")
    return base


def _print_candidates(candidates: list[Candidate]) -> None:
    width = max([len(c.id) for c in candidates] + [10])
    for c in candidates:
        tail = f"{c.reason} -> {ONBOARDING}" if c.needs_onboarding else c.reason
        print(f"  {c.status:15} {c.id:{width}}  {c.name if c.name != c.folder else ''}{'  ' if tail else ''}{tail}")


# --- preflight -------------------------------------------------------------------------------------------------------

def _config_problems(tokens: list[str], workspace: str, proj: dict) -> list[str]:
    """What project.yaml must have for this skill (requires-config, technique-workspaces.md section 8)."""
    problems = []
    for token in tokens:
        if token == "tickets":
            from adapters.tickets import config as tickets_config
            from adapters.tracker.errors import ConfigError
            try:
                tickets_config.load(workspace)
            except ConfigError as e:
                problems.append(f"tickets: {e}")
                problems += [f"tickets: {p}" for p in e.problems]
        elif token == "excel":
            if proj.get("tracker") != "excel":
                problems.append(f"this skill is for tracker: excel, and this project has {proj.get('tracker')!r}")
        else:   # a module with its own section
            if token not in (proj.get("modules") or []):
                problems.append(f"the module {token} is not in modules: of project.yaml")
            elif not proj.get(token):
                problems.append(f"the module {token} is active but the section {token}: is empty")
    return problems


def _mcp_line(workspace: str) -> tuple[bool, str, str]:
    try:
        with open(os.path.join(workspace, ".mcp.json"), encoding="utf-8-sig") as f:
            servers = sorted((json.load(f) or {}).get("mcpServers") or {})
    except (OSError, ValueError, AttributeError):
        servers = []
    if servers:
        return True, "mcp", (f".mcp.json names {', '.join(servers)}; whether it is connected shows in /mcp")
    return False, "mcp", (".mcp.json names no server: /project-onboarding registers the Playwright MCP, or use another "
                          "browser and record the difference")


def _tool_lines(tokens: list[str], workspace: str, proj: dict) -> list[dict]:
    """The lines of validate_project.environment() this skill needs. A tool's name is compared up to its first space
    (`browser chromium` is the token `browser`)."""
    if not tokens:
        return []
    lines = []
    import validate_project
    wanted = set(tokens)
    for ok, tool, detail in validate_project.environment(workspace, proj):
        if tool in wanted or tool.split(" ")[0] in wanted:
            lines.append({"ok": ok, "tool": tool, "detail": detail})
    if "mcp" in wanted:
        ok, tool, detail = _mcp_line(workspace)
        lines.append({"ok": ok, "tool": tool, "detail": detail})
    return lines


def _ticket_sources(workspace: str) -> list[dict]:
    from adapters.tickets import config as tickets_config
    config = tickets_config.load(workspace)
    return [{"id": s.id, "service": s.service, "access": s.access,
             "credentials": tickets_config.credentials_state(config, s)} for s in config.sources.values()]


def _instruction(framework: str, what: str, target: str) -> str:
    """What to do when the skill and the code do not match: it names the checkout that has the code, when it is known."""
    sync = _sync()
    record = sync.read_record(target)
    parts = [f"{what} (framework at {framework})."]
    if record and record.get("root") and not sync.same_path(record["root"], framework):
        if os.path.isdir(record["root"]):
            parts.append(f"The global skills came from {record['root']} (branch {record.get('branch')}, commit "
                         f"{record.get('commit')}): use that checkout, with QA_ASSISTANT_ROOT={record['root']}.")
        else:
            parts.append(f"The global skills came from {record['root']}, which no longer exists.")
    parts.append(f"Or update this checkout (git -C {framework} switch main, then git -C {framework} pull) and run "
                 f"python {framework}/scripts/sync_skills.py --apply.")
    parts.append("Nothing was installed or changed; no data was touched.")
    return " ".join(parts)


def check_skill(skill: str, framework: str, target: str, cwd: str) -> tuple[dict, str | None, list[dict], list[str]]:
    """The skill against the code: (its frontmatter, what to do if they do not match, imports, warnings). Files only,
    apart from the imports of this very checkout, so it works where the module it looks for does not exist."""
    sync = _sync()
    path = os.path.join(framework, ".claude", "skills", skill, "SKILL.md")
    if not os.path.isfile(path):
        return {}, _instruction(framework, f"The skill /{skill} does not exist in this checkout", target), [], []
    doc = sync.skill_doc(path)
    missing = sync.missing_code(framework, doc)
    if missing:
        return doc, _instruction(framework, f"/{skill} needs {', '.join(missing)}, not in this checkout", target), [], []
    warnings, imports = [], []
    if sync.same_path(framework, ROOT):
        for module in sync.listed(doc, "requires-modules"):
            origin = import_origin(module)
            imports.append({"module": module, "origin": origin})
            if origin is None:
                warnings.append(f"{module} is not importable here (for qa_audit: pip install -e . from the checkout)")
            elif not _inside(origin, framework):
                if module.startswith("adapters"):
                    return doc, _instruction(framework, f"{module} is imported from {origin}, another checkout",
                                             target), imports, warnings
                warnings.append(f"{module} is imported from {origin}, another checkout: an editable install runs the "
                                "code of where it was installed")
    if not _inside(cwd, framework):     # inside the framework the project's own copy is the one in use
        state = sync.compare(os.path.join(framework, ".claude", "skills"), target).get(skill)
        if state == "different":
            return doc, _instruction(framework, f"The global copy of /{skill} differs from this checkout's", target), \
                imports, warnings
    return doc, None, imports, warnings


def preflight(skill: str, hint: str | None = None, workspace: str | None = None, root: str | None = None,
              cwd: str | None = None, framework: str = ROOT, target: str | None = None, environ=None,
              config_path: str | None = None) -> tuple[int, dict]:
    """Whether a skill can run, on which project: (exit code, report). Writes nothing and calls no external service."""
    sync = _sync()
    cwd = cwd or os.getcwd()
    target = target or sync.TARGET
    report: dict = {"skill": {"name": skill}, "framework": {"root": framework}, "problems": [], "warnings": [],
                    "tools": [], "imports": [], "project": None, "candidates": [], "instruction": None}
    doc, instruction, imports, warnings = check_skill(skill, framework, target, cwd)
    report["imports"], report["warnings"] = imports, warnings
    metadata = doc.get("metadata") or {}
    report["skill"].update({k: metadata.get(k, "") for k in CONTRACT})
    if instruction:
        report["instruction"] = instruction
        return MISMATCH, report
    selection = resolve(hint, workspace, root, cwd, environ, config_path, framework)
    report["how"], report["root"], report["root_source"] = selection.how, selection.root, selection.root_source
    report["message"] = selection.message
    if selection.outcome != "selected":
        report["candidates"] = [c.public(selection.root) for c in selection.candidates]
        return (AMBIGUOUS if selection.outcome == "ambiguous" else NOTHING), report
    project = selection.candidate
    report["project"] = project.public()
    accepted = (VALID, INCOMPLETE, NOT_CONFIGURED) if metadata.get("requires-project") == "any" else (VALID,)
    status, reason, proj = classify(project.path)
    if status not in accepted:
        report["problems"].append(reason)
        if status == INCOMPLETE and proj:
            import validate_project
            report["problems"] += validate_project.base_problems(proj)[0]
        if status in (INCOMPLETE, NOT_CONFIGURED):
            report["proposes"] = ONBOARDING
        return NOT_USABLE, report
    environments = proj.get("environments") if isinstance(proj.get("environments"), dict) else {}
    report["environments"] = sorted(str(k) for k in environments)
    if status == VALID:
        problems = _config_problems(sync.listed(doc, "requires-config"), project.path, proj)
        if problems:
            report["problems"] += problems
            report["proposes"] = ONBOARDING
            return NOT_USABLE, report
        report["tools"] = _tool_lines(sync.listed(doc, "requires-tools"), project.path, proj)
        if "tickets" in sync.listed(doc, "requires-config"):
            report["sources"] = _ticket_sources(project.path)
    return OK, report


def _print_preflight(code: int, r: dict) -> None:
    p = r.get("project")
    if r["instruction"]:
        print(f"STOP: {r['instruction']}")
    if r.get("message"):
        print(r["message"])
    for c in r.get("candidates") or []:
        print(f"  {c['status']:15} {c['id']}  {c['name'] if c['name'] != c['folder'] else ''}")
    if p:
        print(f"Project: {p['name']} [{p['id']}] ({p['status']}), by {r.get('how')}")
        print(f"Workspace: {p['path']}")
    for problem in r["problems"]:
        print(f"PROBLEM: {problem}")
    if r.get("proposes"):
        print(f"Proposed: {r['proposes']} in that folder")
    if code == OK:
        print(f"Framework: {r['framework']['root']}")
        print(f"Task: /{r['skill']['name']} -- {r['skill'].get('intent', '')}")
        print(f"Environments: {', '.join(r.get('environments') or []) or 'none declared'}")
        print(f"Tools: {r['skill'].get('tools', '')}")
        for line in r["tools"]:
            print(f"  [{'ok' if line['ok'] else 'missing'}] {line['tool']}: {line['detail']}")
        for s in r.get("sources") or []:
            creds = ", ".join(f"{k}: {'set' if v else 'MISSING'}" for k, v in s["credentials"].items()) or "none"
            print(f"  source {s['id']}: {s['service']}, access {s['access']}, credentials {creds}")
        print(f"Writes: {r['skill'].get('writes', '')}")
        print(f"Approvals: {r['skill'].get('approvals', '')}")
    for warning in r["warnings"]:
        print(f"warning: {warning}")


def catalogue(framework: str = ROOT) -> list[dict]:
    """Every skill's contract, read from its own frontmatter: there is no other list."""
    sync = _sync()
    base = os.path.join(framework, ".claude", "skills")
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "SKILL.md")
        if os.path.isfile(path):
            doc = sync.skill_doc(path)
            metadata = doc.get("metadata") or {}
            out.append({"name": name, "description": doc.get("description", ""), **{k: metadata.get(k, "") for k in CONTRACT},
                        **{k: v for k, v in metadata.items() if k.startswith("requires-")}})
    return out


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/workspace_select.py", description="Which framework runs, which "
                                 "project a request means, and whether a skill can run there.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="the report as JSON")
    roots = argparse.ArgumentParser(add_help=False)
    roots.add_argument("--root", help="the root of workspaces (default: $QA_WORKSPACES_ROOT, the configured one, or "
                                      "qa-workspaces/ next to the main checkout)")
    pick = argparse.ArgumentParser(add_help=False)
    pick.add_argument("project", nargs="?", help="a project's id, folder or name")
    pick.add_argument("--workspace", help="the path of one workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("where", parents=[common, roots], help="the framework as it really runs: checkout, Python, imports")
    sub.add_parser("list", parents=[common, roots], help="the projects under the root, with their status")
    sub.add_parser("resolve", parents=[common, roots, pick], help="which project a request means")
    p = sub.add_parser("preflight", parents=[common, roots, pick], help="whether a skill can run, on which project")
    p.add_argument("--skill", required=True, help="the skill's name, without the slash")
    sub.add_parser("skills", parents=[common], help="the catalogue, from each skill's frontmatter")
    p = sub.add_parser("set-root", help="keep the root of workspaces in ~/.qa-assistant/config.json")
    p.add_argument("path")
    return ap


def _run(a) -> int:
    emit = (lambda doc: print(json.dumps(doc, ensure_ascii=False, indent=1))) if getattr(a, "json", False) else None
    if a.command == "where":
        info = where(a.root)
        emit(info) if emit else _print_where(info)
        return OK
    if a.command == "set-root":
        code, text = set_root(a.path)
        print(text, file=sys.stderr if code else sys.stdout)
        return code
    if a.command == "skills":
        skills = catalogue()
        if emit:
            emit(skills)
        else:
            for s in skills:
                print(f"/{s['name']}: {s['intent']}\n  tools: {s['tools']}\n  writes: {s['writes']}\n  approvals: {s['approvals']}")
        return OK
    if a.command == "list":
        root, source = workspaces_root(a.root)
        if not os.path.isdir(root):
            print(missing_root_message(root, source), file=sys.stderr)
            return NOTHING
        found = discover(root)
        if emit:
            emit({"root": root, "root_source": source, "projects": [c.public(root) for c in found]})
        else:
            print(f"Root of workspaces: {root} (from {source}); {len(found)} folder(s)")
            _print_candidates(found)
        return OK
    if a.command == "resolve":
        selection = resolve(a.project, a.workspace, a.root)
        code = {"selected": OK, "ambiguous": AMBIGUOUS, "none": NOTHING}[selection.outcome]
        if selection.candidate and selection.candidate.status != VALID:
            code = NOT_USABLE
        if emit:
            emit({"code": code, **selection.public()})
        else:
            if selection.message:
                print(selection.message)
            _print_candidates(selection.candidates)
            if selection.candidate:
                c = selection.candidate
                print(f"{c.id} ({c.status}{': ' + c.reason if c.reason else ''}), by {selection.how}: {c.path}")
                if c.needs_onboarding:
                    print(f"Proposed: {ONBOARDING} in that folder")
        return code
    code, report = preflight(a.skill, a.project, a.workspace, a.root)
    if emit:
        emit({"code": code, **report})
    else:
        _print_preflight(code, report)
    return code


def main(argv=None) -> int:
    try:
        return _run(parser().parse_args(argv))
    except ImportError as e:
        print(f"STOP: this checkout ({ROOT}) cannot run this: {e}. Install its requirements (pip install -r "
              "requirements.txt from its root), or use the checkout the skills came from (QA_ASSISTANT_ROOT). Nothing "
              "was installed or changed.", file=sys.stderr)
        return MISMATCH


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
