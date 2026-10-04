"""
Validates a workspace's project.yaml (methodology.md sections 4 and 4.1; plan,
task 3.4): base fields, the URL of each environment, modules from the closed
list, the section of each active module, module URLs inside the declared
environments (never production), the device and emulator keys of mobile: (plan 9.2, 9.3),
Excel (when tracker: excel), the jira: / azure_devops: section (when tracker: jira
/ azure-devops; plan 10.1: required keys, https URLs, .env variable names, every
status mapped or skip, Question never mapped, the closed list of Azure DevOps
outcomes), the optional ci: section (plan 11.0: a standard runner only, limits,
ci.secrets as .env variable names, the login mode and its helper flow), the dashboard: section and, in a workspace with
git, the .gitignore of the files with secrets (warning) and the protection of
history/ in .claude/settings.json (warning). In a workspace that is a git
repository of its own (plan 9.4, through scripts/workspace_git.py): .env
tracked or a public remote (with gh installed) are problems; no remote, commits
not pushed, .env.example missing a variable of project.yaml and tracked files
over 50 MB are warnings.

    python scripts/validate_project.py <workspace> [--environment [--strict]]

--environment (methodology 12.3; plan 9.5) also checks this computer, one line
per tool, `[ok|missing] <tool>: <detail or how to install it>`: always git, the
Python version of pyproject.toml, .env with every name of .env.example and of
project.yaml (names only, never a value) and the skills in sync with the global
copy (sync_skills.py's comparison, read only); tracker excel: LibreOffice (as
the recalculation finds it); functional-web/exploratory: Node.js (Playwright
MCP); accessibility: qa-audit importable and its browser downloaded; mobile:
maestro, and for android adb and emulator (as setup_emulator.py finds them),
for ios xcrun, for an app mobile.build.source. Nothing is started nor
installed. A missing tool is a warning (exit 0) unless --strict.

Read only; never changes anything. Problems prevent calling the onboarding done;
warnings are shown to the user.

Exit codes: 0 no problems (there may be warnings) · 1 problems (or, with
--strict, a missing tool) · 2 no project.yaml or usage
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import sys
import tomllib
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import workspace_git as wg

from adapters.tracker import config as tracker_config

# closed list (methodology 4.1): module -> (own section, required fields in it)
MODULES = {
    "functional-web": (None, ()),
    "exploratory": (None, ()),
    "api": ("api", ("base_url",)),
    "accessibility": ("accessibility", ("pages",)),
    "mobile": ("mobile", ("platform", "flows")),
}
TRACKERS = ("excel", "azure-devops", "jira")
# excel is checked here; jira (plan 10.2) and azure-devops (plan 10.3) through adapters/tracker/config.py


def environment_of(url: str, environments: dict) -> str | None:
    """Name of the declared environment that contains the URL (same scheme and host, path below)."""
    u = urlsplit(str(url))
    for name, base in environments.items():
        b = urlsplit(str(base or ""))
        if b.netloc and (u.scheme, u.netloc.lower()) == (b.scheme, b.netloc.lower()):
            path = b.path.rstrip("/")
            if not path or u.path == path or u.path.startswith(path + "/"):
                return name
    return None


def module_urls(proj: dict, modules: list) -> list[tuple[str, str]]:
    out = []
    if "api" in modules and (proj.get("api") or {}).get("base_url"):
        out.append(("api.base_url", proj["api"]["base_url"]))
    if "accessibility" in modules:
        a = proj.get("accessibility") or {}
        if (a.get("login") or {}).get("url"):
            out.append(("accessibility.login.url", a["login"]["url"]))
        out += [(f"accessibility.pages[{p.get('name')}]", p["url"])
                for p in a.get("pages") or [] if isinstance(p, dict) and p.get("url")]
    return out


def gitignore_warnings(workspace: str, proj: dict, modules: list) -> list[str]:
    """Files with secrets or project data that the workspace's git must ignore
    (methodology section 4, step 4). Only in a workspace inside a git repository."""
    if not wg.inside_repository(workspace):
        return []
    files = ([".env"] + ([".api-session.env"] if "api" in modules else [])
             + (["dashboard.html"] if proj.get("tracker") == "excel" else []))
    missing = [f for f in files if wg.git(workspace, "check-ignore", "-q", "--no-index", f).returncode != 0]
    return [f"{f} is not in the workspace's .gitignore (it has secrets or project data)" for f in missing]


def portable_checks(workspace: str, proj: dict) -> tuple[list[str], list[str]]:
    """The workspace as a repository of its own with a private remote (methodology 4, step 4, and 12.3; plan 9.4),
    through scripts/workspace_git.py. .env tracked or a public remote: problems; the rest: warnings."""
    problems, warnings = [], []
    if not wg.inside_repository(workspace):
        return problems, warnings
    if wg.env_tracked(workspace):
        problems.append(".env is tracked by git: `git rm --cached .env` (and change the credentials if it was "
                        "ever pushed)")
    if not wg.own_repository(workspace):
        return problems, warnings
    missing = wg.missing_example_variables(workspace, proj)
    if missing:
        where = ".env.example" if os.path.isfile(os.path.join(workspace, ".env.example")) else \
            ".env.example (it does not exist)"
        warnings.append(f"{where} misses variables of project.yaml: {', '.join(missing)} (scripts/workspace_git.py "
                        "<workspace> init adds them)")
    big = wg.large_files(workspace)
    if big:
        warnings.append("tracked files over 50 MB: " + ", ".join(f"{p} ({wg.size_mb(n)})" for p, n in big))
    url = wg.remote_url(workspace)
    if not url:
        warnings.append("the workspace's git has no remote: it cannot be continued on another computer "
                        "(workspace_git.py <workspace> remote --create OWNER/NAME, private)")
        return problems, warnings
    pending = wg.unpushed(workspace)
    if pending:
        warnings.append(f"{len(pending)} commit(s) not pushed to {url} (push only with the user's approval: "
                        "workspace_git.py <workspace> push)")
    vis, why = wg.visibility(url)
    if vis == "PUBLIC":
        problems.append(f"the workspace's remote {url} is PUBLIC: it holds the project's data; make it private")
    elif vis == "INTERNAL":
        warnings.append(f"the workspace's remote {url} is INTERNAL (visible to the whole organisation), not private")
    elif vis is None and why != "gh is not installed" and why != "not a GitHub remote":
        warnings.append(f"the visibility of the remote {url} could not be confirmed ({why})")
    return problems, warnings


PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")


def is_texts(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def mobile_target(proj: dict) -> tuple[list[str], list[str]]:
    """The keys of mobile: that scripts/setup_emulator.py reads (plan 9.2: avd, image, build, permissions) and
    those that scripts/device.py reads (plan 9.3: serial, send_label, ignore_labels, evidence_batch, browser)."""
    m = proj.get("mobile")
    if not isinstance(m, dict):
        return [], []
    problems, warnings = [], []
    if m.get("serial") is not None and not (isinstance(m["serial"], str) and re.fullmatch(r"\S+", m["serial"])):
        problems.append(f"mobile.serial {m['serial']!r}: a device serial as a quoted text, e.g. \"emulator-5554\"")
    label = m.get("send_label")
    if label is not None and not ((isinstance(label, str) and label.strip()) or (is_texts(label) and label)):
        problems.append("mobile.send_label must be a text or a list of texts (the send control's label)")
    if m.get("ignore_labels") is not None and not is_texts(m["ignore_labels"]):
        problems.append("mobile.ignore_labels must be a list of texts (the app's chrome dropped from `send`)")
    batch = m.get("evidence_batch")
    if batch is not None and not (isinstance(batch, str) and re.fullmatch(r"[A-Za-z0-9._-]+", batch)
                                  and batch.strip(".")):
        problems.append(f"mobile.evidence_batch {batch!r}: a folder name as a quoted text (letters, digits, "
                        "'.', '_', '-'), e.g. \"20260928\"")
    if m.get("browser") is not None and not (isinstance(m["browser"], str) and PACKAGE.fullmatch(m["browser"])):
        problems.append(f"mobile.browser {m['browser']!r}: an Android package name, e.g. com.android.chrome")
    if m.get("avd") is not None and not (isinstance(m["avd"], str) and re.fullmatch(r"[A-Za-z0-9._-]+", m["avd"])):
        problems.append(f"mobile.avd {m['avd']!r}: an AVD name (letters, digits, '.', '_', '-')")
    if m.get("image") is not None and not (isinstance(m["image"], str) and m["image"].startswith("system-images;")
                                           and m["image"].count(";") == 3):
        problems.append(f"mobile.image {m['image']!r}: expected system-images;android-<n>;<tag>;<abi>")
    build = m.get("build")
    if build is not None:
        if not isinstance(build, dict):
            problems.append("mobile.build must be a map {source, sha256}")
        else:
            if not isinstance(build.get("source") or "", str):
                problems.append("mobile.build.source must be a text (where the build comes from)")
            if not isinstance(build.get("version") or "", str):
                problems.append("mobile.build.version must be a quoted text, e.g. \"1.4.0 (140)\"")
            sha = build.get("sha256")
            if sha in (None, ""):
                warnings.append("mobile.build.sha256 is missing: setup_emulator.py cannot check the build it "
                                "installs")
            elif not (isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha)):
                problems.append("mobile.build.sha256 must be 64 hexadecimal characters")
    perms = m.get("permissions")
    if perms is not None:
        if not isinstance(perms, dict) or set(perms) - {"grant", "revoke"}:
            problems.append("mobile.permissions must be a map with 'grant' and/or 'revoke' (lists)")
        else:
            lists = {k: perms.get(k) or [] for k in ("grant", "revoke")}
            for k, v in lists.items():
                if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                    problems.append(f"mobile.permissions.{k} must be a list of permission names")
            if all(isinstance(v, list) for v in lists.values()):
                both = sorted(set(lists["grant"]) & set(lists["revoke"]))
                if both:
                    problems.append(f"mobile.permissions: {both} both granted and revoked")
    return problems, warnings


# the GitHub-hosted STANDARD Linux runners (free minutes); any other label is a larger (paid) or self-hosted runner
STANDARD_RUNNER = re.compile(r"ubuntu-(latest|\d{2}\.04)")
# secrets the workflow itself needs: never .env variables, so never in ci.secrets
WORKFLOW_SECRETS = ("CLAUDE_CODE_OAUTH_TOKEN", "QA_ASSISTANT_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY")
LOGIN_MODES = ("manual-issue", "none")
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


def positive_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def ci_section(workspace: str, proj: dict) -> tuple[list[str], list[str]]:
    """The optional ci: section (remote execution on a GitHub Actions runner; plan 11.0, technique-mobile.md §11.5):
    a standard runner only (no paid larger runner), limits as positive integers, ci.secrets as names of .env
    variables of project.yaml (never a workflow secret), the login mode and its helper flow."""
    ci = proj.get("ci")
    if ci is None:
        return [], []
    if not isinstance(ci, dict):
        return ["ci: must be a map (technique-mobile.md §11.5)"], []
    problems, warnings = [], []
    known = {"runner", "timeout_minutes", "max_turns", "secrets", "login"}
    extra = sorted(set(ci) - known)
    if extra:
        warnings.append(f"ci: unknown key(s) {extra} (known: {sorted(known)})")
    if "mobile" not in (proj.get("modules") or []):
        warnings.append("ci: filled in, but the module 'mobile' is not in 'modules' (remote execution runs mobile "
                        "batches)")
    runner = ci.get("runner", "ubuntu-24.04")
    if not (isinstance(runner, str) and STANDARD_RUNNER.fullmatch(runner)):
        problems.append(f"ci.runner {runner!r}: only a GitHub-hosted standard runner (ubuntu-24.04, ubuntu-22.04, "
                        "ubuntu-latest); larger runners are paid and self-hosted ones are not the workspace's CI")
    timeout = ci.get("timeout_minutes", 60)
    if not (positive_int(timeout) and timeout <= 360):
        problems.append(f"ci.timeout_minutes {timeout!r}: whole minutes, from 1 to 360 (GitHub's job limit)")
        timeout = 60
    if not positive_int(ci.get("max_turns", 150)):
        problems.append(f"ci.max_turns {ci.get('max_turns')!r}: a positive whole number")
    secrets = ci.get("secrets") or []
    if not (isinstance(secrets, list) and all(isinstance(x, str) and VARIABLE.fullmatch(x) for x in secrets)):
        problems.append("ci.secrets must be a list of .env variable NAMES (never values), e.g. [SHOP_APP_USER]")
    else:
        from common import variable_names
        reserved = sorted(set(secrets) & set(WORKFLOW_SECRETS))
        if reserved:
            problems.append(f"ci.secrets: {reserved} are the workflow's own secrets, not .env variables: take them "
                            "out (docs/workspace/ci/README.md)")
        unknown = sorted(set(secrets) - set(variable_names(proj)) - set(WORKFLOW_SECRETS))
        if unknown:
            warnings.append(f"ci.secrets: {unknown} are not variables project.yaml refers to (a *_var key or a "
                            "'variables' list): the runner would write them to .env for nothing")
    login = ci.get("login") or {}
    if not isinstance(login, dict):
        problems.append("ci.login must be a map {mode, flow, timeout_minutes}")
        return problems, warnings
    mode = login.get("mode", "none")
    if mode not in LOGIN_MODES:
        problems.append(f"ci.login.mode {mode!r}: one of {list(LOGIN_MODES)}")
    login_timeout = login.get("timeout_minutes", 30)
    if not positive_int(login_timeout):
        problems.append(f"ci.login.timeout_minutes {login_timeout!r}: a positive whole number")
    elif mode == "manual-issue" and login_timeout >= timeout:
        problems.append(f"ci.login.timeout_minutes ({login_timeout}) must be shorter than ci.timeout_minutes "
                        f"({timeout})")
    hosts = login.get("hosts") or []
    if not (isinstance(hosts, list) and all(isinstance(h, str) and HOST.fullmatch(h) for h in hosts)):
        problems.append("ci.login.hosts must be a list of host names, e.g. [auth.demoshop.example] (no scheme, no "
                        "path)")
    user = login.get("user")
    if user not in (None, "") and not (isinstance(user, str) and GITHUB_LOGIN.fullmatch(user)):
        problems.append(f"ci.login.user {user!r}: a GitHub login")
    if login.get("screen_text") is not None and not isinstance(login["screen_text"], str):
        problems.append("ci.login.screen_text must be a text")
    if mode == "manual-issue":
        flow = login.get("flow")
        if not (isinstance(flow, str) and flow.strip()):
            problems.append("ci.login.mode is manual-issue but ci.login.flow is empty: the helper flow that asks for "
                            "the link (e.g. flows/_request_link.yaml)")
        else:
            if not os.path.basename(flow).startswith("_"):
                problems.append(f"ci.login.flow {flow!r}: a helper flow's name starts with '_' (e.g. "
                                "flows/_request_link.yaml), so it is never taken for a case")
            if not os.path.isfile(os.path.join(workspace, flow)):
                warnings.append(f"ci.login.flow {flow} does not exist in the workspace yet")
    return problems, warnings


VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
HISTORY_RULE = re.compile(r"^Edit\((/|\./)?history/\*\*\)$")


def mcp_warnings(workspace: str) -> list[str]:
    """The workspace's Playwright MCP (.mcp.json), when it exists: fixed version, --secrets .env and --isolated
    (technique-browser.md sections 1 and 3; lessons of the pilot of 25.09.2026)."""
    path = os.path.join(workspace, ".mcp.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8-sig") as f:
            servers = (json.load(f) or {}).get("mcpServers") or {}
    except (OSError, ValueError) as e:
        return [f".mcp.json unreadable: {e}"]
    warnings = []
    for name, srv in servers.items():
        args = [str(a) for a in (srv or {}).get("args") or []]
        package = next((a for a in args if a.startswith("@playwright/mcp")), None)
        if package is None:
            continue
        if not re.fullmatch(r"@playwright/mcp@\d+\.\d+\.\d+", package):
            warnings.append(f".mcp.json ({name}): {package} without a fixed version (technique-browser.md "
                            "section 1: never @latest)")
        if "--secrets" not in args or args[args.index("--secrets") + 1:args.index("--secrets") + 2] != [".env"]:
            warnings.append(f".mcp.json ({name}): --secrets .env is missing (the assistant must not read the "
                            "credentials; technique-browser.md section 3)")
        if "--isolated" not in args:
            warnings.append(f".mcp.json ({name}): --isolated is missing (with the persistent profile clicks get "
                            "lost; technique-browser.md section 1)")
    return warnings


def history_protected(workspace: str) -> bool:
    """The workspace's .claude/settings.json (or settings.local.json) forbids editing history/**."""
    for name in ("settings.json", "settings.local.json"):
        path = os.path.join(workspace, ".claude", name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                deny = ((json.load(f) or {}).get("permissions") or {}).get("deny") or []
        except (ValueError, AttributeError):
            continue
        if any(HISTORY_RULE.match(str(r).strip()) for r in deny):
            return True
    return False


def rebuild_rule_warnings(workspace: str) -> list[str]:
    """Plan 10.6: a workspace's CLAUDE.md that still says to rebuild the workbook with the demo's rebuild_workbook.py
    (a rule of before 8.11; that script only builds the demo's workbook), without a script of its own."""
    path = os.path.join(workspace, "CLAUDE.md")
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return []
    if "rebuild_workbook" not in text or os.path.exists(os.path.join(workspace, "rebuild_workbook.py")):
        return []
    return [("CLAUDE.md mentions rebuild_workbook.py, which only builds the demo's workbook (since plan 8.11): approved "
             "cases go in with `python -m adapters.excel.add_cases` (technique-excel.md section 4.1); update the rule")]


def validate(workspace: str) -> tuple[list[str], list[str]]:
    from common import read_project
    proj = read_project(workspace)
    problems, warnings = [], []

    for field in ("project", "tracker", "bug_format"):
        if not proj.get(field):
            problems.append(f"field '{field}' is empty")
    environments = proj.get("environments") or {}
    if not environments:
        problems.append("'environments' is empty: at least the test environment is needed")
    elif not isinstance(environments, dict):
        problems.append("'environments' must be a map name -> URL")
        environments = {}
    for name, url in environments.items():
        parts = urlsplit(str(url or "").strip())
        if not str(url or "").strip():
            problems.append(f"environment '{name}' without a URL")
        elif parts.scheme not in ("http", "https") or not parts.netloc:
            problems.append(f"environment '{name}': {url!r} is not an http(s):// URL")
    if proj.get("tracker") and proj["tracker"] not in TRACKERS:
        problems.append(f"tracker {proj['tracker']!r} outside {list(TRACKERS)}")
    elif proj.get("tracker") in tracker_config.SECTIONS:
        p, w = tracker_config.validate_section(proj)
        problems += p
        warnings += w
    if proj.get("resolved_bugs") not in (None, "remove", "archive"):
        problems.append("resolved_bugs must be 'remove' or 'archive'")
    if proj.get("evidence") not in (None, "text", "text+screenshot"):
        problems.append("evidence must be 'text' or 'text+screenshot'")

    modules = proj.get("modules") or []
    if not modules:
        problems.append("'modules' is empty: choose from the list in methodology 4.1")
    outside = [m for m in modules if m not in MODULES]
    if outside:
        problems.append(f"modules outside the closed list (methodology 4.1): {outside}; valid: {list(MODULES)}")
    for m in modules:
        section, required = MODULES.get(m, (None, ()))
        if section:
            s = proj.get(section) or {}
            missing = [c for c in required if not s.get(c)]
            if missing:
                problems.append(f"module '{m}' is active but the section {section}: does not have {missing}")
    for m, (section, required) in MODULES.items():
        # all: a field with a default value in the template (e.g. mobile.flows) does not count as filled in
        if section and m not in modules and all((proj.get(section) or {}).get(c) for c in required):
            warnings.append(f"section {section}: filled in, but the module '{m}' is not in 'modules'")

    # URLs of the active modules: only from declared environments, never production (technique-browser.md section 2)
    for url_field, url in module_urls(proj, modules):
        env = environment_of(url, environments)
        if env is None:
            problems.append(f"{url_field} ({url}) outside the environments declared in 'environments'")
        elif "prod" in str(env).lower():
            problems.append(f"{url_field} ({url}) points to the environment '{env}' (production?)")

    p, w = mobile_target(proj)
    problems += p
    warnings += w
    p, w = ci_section(workspace, proj)
    problems += p
    warnings += w
    warnings += gitignore_warnings(workspace, proj, modules)
    try:
        p, w = portable_checks(workspace, proj)
    except wg.Failed as e:
        p, w = [], [f"the workspace's git could not be read: {e}"]
    problems += p
    warnings += w
    warnings += mcp_warnings(workspace)
    if not history_protected(workspace):
        warnings.append("history/ is not protected: Edit(/history/**) is missing from permissions.deny in the "
                        "workspace's .claude/settings.json (copy <qa>/docs/workspace/settings.json; methodology "
                        "section 13)")

    if proj.get("tracker") == "excel":
        warnings += rebuild_rule_warnings(workspace)
        from adapters.excel import ConfigError, ExcelAdapter
        try:
            ad = ExcelAdapter.from_project(workspace)
            ad.load()
        except (ConfigError, FileNotFoundError, KeyError) as e:
            problems.append(f"excel: {e}")
        else:
            cc = ad.cross_check()
            if cc:
                warnings.append(f"excel: the workbook has {len(cc)} cross-check problem(s) (e.g. {cc[0]})")
            import build_dashboard
            try:
                types = build_dashboard.status_types(list(ad.cfg["cases"]["statuses"]), proj.get("dashboard"))
                build_dashboard.check_not_run(types, ad.cfg)
            except ValueError as e:
                problems.append(f"dashboard: {e}")
            if not (proj.get("dashboard") or {}).get("states"):
                warnings.append("dashboard.states is empty: the dashboard treats the 1st status as pending and the "
                                "others as neutral")
    return problems, warnings


# --- the environment: the tools this computer needs for the workspace (methodology 12.3; plan 9.5) ------------
# The doors to the machine: the tests replace them. Each one only looks; nothing is started nor installed.

def which(name: str) -> str | None:
    return shutil.which(name)


def python_version() -> tuple[int, ...]:
    return tuple(sys.version_info[:3])


def soffice() -> str | None:
    """LibreOffice, found the way the adapter's recalculation finds it."""
    from adapters.excel.recalc import find_soffice
    try:
        return find_soffice()
    except FileNotFoundError:
        return None


def android_tools() -> dict:
    """adb and emulator, found the way scripts/setup_emulator.py finds them (the SDK folders, then the PATH)."""
    import setup_emulator
    return setup_emulator.find_tools()


def qa_audit_installed() -> bool:
    return importlib.util.find_spec("qa_audit") is not None


def playwright_browsers() -> set[str] | None:
    """The Playwright engines downloaded on this computer (the folders `qa-audit doctor` looks in; nothing is
    started). None when qa-audit is not importable."""
    try:
        from qa_audit.doctor import _browser_dirs
    except ImportError:
        return None
    found = set()
    for d in _browser_dirs():
        if d and os.path.isdir(d):
            for engine in ("chromium", "firefox", "webkit"):
                if glob.glob(os.path.join(d, f"{engine}-*")) or glob.glob(os.path.join(d, f"{engine}_*-*")):
                    found.add(engine)
    return found


def skills_state() -> dict[str, str]:
    """scripts/sync_skills.py's comparison (read only) of .claude/skills/ with the global copy."""
    import sync_skills
    return sync_skills.compare(sync_skills.SOURCE, sync_skills.TARGET)


def required_python() -> tuple[int, ...]:
    """The minimum of `requires-python` in pyproject.toml (3.12 when it cannot be read)."""
    try:
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            spec = tomllib.load(f)["project"]["requires-python"]
        return tuple(int(x) for x in re.search(r">=\s*(\d+(?:\.\d+)*)", spec).group(1).split("."))
    except (OSError, KeyError, AttributeError, ValueError, tomllib.TOMLDecodeError):
        return (3, 12)


VALUES_FROM = "from your own secret store (never from a chat, a log or the repository)"


def env_check(workspace: str, proj: dict, ci: bool = False) -> tuple[bool, str]:
    """.env has every name of .env.example and of project.yaml (with ci: only those of ci.secrets, the ones a runner
    gets). Only names are ever shown, never a value."""
    from common import read_env, variable_names
    if ci:
        needed = sorted(str(x) for x in ((proj.get("ci") or {}).get("secrets") or []))
    else:
        needed = sorted(set(read_env(os.path.join(workspace, ".env.example"))) | set(variable_names(proj)))
    if not needed:
        return True, "no variable needed (.env.example and project.yaml name none)"
    path = os.path.join(workspace, ".env")
    if not os.path.isfile(path):
        start = ("copy .env.example to .env" if os.path.isfile(os.path.join(workspace, ".env.example"))
                 else "create .env")
        return False, f"it does not exist: {start} and fill {', '.join(needed)} {VALUES_FROM}"
    env = read_env(path)
    absent = [n for n in needed if n not in env]
    empty = [n for n in needed if n in env and not env[n]]
    if not absent and not empty:
        return True, f"the {len(needed)} variable(s) of .env.example and project.yaml are set (values not shown)"
    parts = ([f"misses {', '.join(absent)}"] if absent else []) + \
        ([f"without a value: {', '.join(empty)}"] if empty else [])
    return False, f"{'; '.join(parts)} — fill them {VALUES_FROM}"


def environment(workspace: str, proj: dict, ci: bool = False) -> list[tuple[bool, str, str]]:
    """(found, tool, detail or how to install it) for what this computer needs to work on the workspace: always
    git, Python, .env and the skills; then per tracker and active module. Missing tools are warnings unless
    --strict. ci: a runner of the workspace's CI (.env holds only ci.secrets)."""
    out = []
    add = out.append
    exe = which("git")
    add((bool(exe), "git", exe or "install git (https://git-scm.com/downloads); the workspace is a git repository"))
    have, need = python_version(), required_python()
    add((have >= need, "python", f"{'.'.join(map(str, have))} (qa-assistant needs >= {'.'.join(map(str, need))})"
         + ("" if have >= need else ": install a newer Python, then `pip install -e .` again")))
    ok, detail = env_check(workspace, proj, ci)
    add((ok, ".env", detail))
    try:
        state = skills_state()
        pending = [n for n, st in state.items() if st != "same"]
        add((not pending, "skills", f"{len(state)} skill(s) in sync with the global copy" if not pending else
             f"{len(pending)} skill(s) differ from the global copy ({', '.join(pending)}): "
             "python scripts/sync_skills.py --apply"))
    except OSError as e:
        add((False, "skills", f"cannot compare with the global copy ({e}): python scripts/sync_skills.py"))

    modules = proj.get("modules") or []
    if proj.get("tracker") == "excel":
        mechanism = (((proj.get("excel") or {}).get("recalc") or {}).get("mechanism")) or "libreoffice"
        if mechanism == "excel-com":
            add((True, "recalculation", "excel-com (desktop Excel on Windows; LibreOffice not needed)"))
        else:
            exe = soffice()
            add((bool(exe), "libreoffice", exe or "the workbook's recalculation needs it: install LibreOffice "
                 "(e.g. `winget install TheDocumentFoundation.LibreOffice`, `apt install libreoffice-calc`, "
                 "`brew install --cask libreoffice`)"))
    if {"functional-web", "exploratory"} & set(modules):
        exe = which("node")
        add((bool(exe), "node", exe or "the Playwright MCP runs with npx: install Node.js 22+ (https://nodejs.org)"))
    if "accessibility" in modules:
        installed = qa_audit_installed()
        add((installed, "qa-audit", "importable" if installed else
             "pip install -e . (from the root of qa-assistant), then qa-audit doctor"))
        engine = str((proj.get("accessibility") or {}).get("browser") or "chromium")
        engines = playwright_browsers() if installed else None
        if engines is None:
            add((False, f"browser {engine}", ("unknown until qa-audit is installed; then "
                                              f"python -m playwright install {engine} and qa-audit doctor")))
        else:
            add((engine in engines, f"browser {engine}",
                 "downloaded (qa-audit doctor starts it and checks it)" if engine in engines else
                 f"python -m playwright install {engine}, then qa-audit doctor"))
    if "mobile" in modules:
        m = proj.get("mobile") or {}
        platform = str(m.get("platform") or "")
        exe = which("maestro")
        add((bool(exe), "maestro", exe or "install the Maestro CLI (https://maestro.mobile.dev; it needs Java 17+)"))
        if platform == "android":
            tools = android_tools()
            sdk = ("install the Android SDK (Android Studio or the command-line tools) and set ANDROID_HOME; then "
                   f"python scripts/setup_emulator.py {workspace} --dry-run")
            add((bool(tools.get("adb")), "adb", tools.get("adb") or sdk))
            serial = str(m.get("serial") or "")
            if serial and not serial.startswith("emulator-"):
                add((True, "emulator", f"not needed: the target is the device {serial}"))
            else:
                add((bool(tools.get("emulator")), "emulator", tools.get("emulator") or sdk))
        elif platform == "ios":
            exe = which("xcrun")
            add((bool(exe), "xcrun", exe or "iOS needs macOS with Xcode (xcode-select --install)"))
        if platform in ("android", "ios"):
            build = m.get("build") if isinstance(m.get("build"), dict) else {}
            source = str(build.get("source") or "").strip()
            add((bool(source), "build", (f"not in git; get it from mobile.build.source ({source})"
                                         + (": python scripts/workspace_build.py <workspace> fetch"
                                            if source.startswith("release ") else "")
                                         + (", checked against mobile.build.sha256" if build.get("sha256") else "")
                                         if source else "mobile.build.source is not set: write where the build "
                                         "comes from (the build is never committed)")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validates a workspace's project.yaml.")
    ap.add_argument("workspace")
    ap.add_argument("--environment", action="store_true",
                    help="also check the tools this computer needs for the workspace's modules (methodology 12.3)")
    ap.add_argument("--strict", action="store_true", help="with --environment: a missing tool gives exit 1")
    ap.add_argument("--ci", action="store_true", help="with --environment, on a runner of the workspace's CI: .env "
                                                      "needs only the names of ci.secrets")
    a = ap.parse_args(argv)
    if (a.strict or a.ci) and not a.environment:
        ap.error("--strict and --ci only apply with --environment")
    if not os.path.isfile(os.path.join(a.workspace, "project.yaml")):
        hint = (": the workspace is from before the translation, convert it with scripts/migrate_workspace.py"
                if os.path.isfile(os.path.join(a.workspace, "projeto.yaml")) else "")
        print(f"ERROR: no project.yaml in {a.workspace}{hint}", file=sys.stderr)
        return 2
    problems, warnings = validate(a.workspace)
    for p in problems:
        print(f"PROBLEM: {p}")
    for w in warnings:
        print(f"warning: {w}")
    print(f"{len(problems)} problem(s), {len(warnings)} warning(s)")
    if not a.environment:
        return 1 if problems else 0
    from common import read_project
    checks = environment(a.workspace, read_project(a.workspace), a.ci)
    for ok, tool, detail in checks:
        print(f"[{'ok' if ok else 'missing'}] {tool}: {detail}")
    missing = sum(1 for ok, _, _ in checks if not ok)
    print(f"environment: {missing} missing" + ("" if not missing else
          " (problems with --strict)" if a.strict else " (warnings; --strict makes them problems)"))
    return 1 if problems or (a.strict and missing) else 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
