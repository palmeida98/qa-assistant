"""
Validates a workspace's project.yaml (methodology.md sections 4 and 4.1; plan,
task 3.4): base fields, the URL of each environment, modules from the closed
list, the section of each active module, module URLs inside the declared
environments (never production), the emulator keys of mobile: (plan 9.2),
Excel (when tracker: excel), the dashboard: section and, in a workspace with
git, the .gitignore of the files with secrets (warning) and the protection of
history/ in .claude/settings.json (warning).

    python scripts/validate_project.py <workspace>

Read only; never changes anything. Problems prevent calling the onboarding done;
warnings are shown to the user.

Exit codes: 0 no problems (there may be warnings) · 1 problems · 2 no project.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

# closed list (methodology 4.1): module -> (own section, required fields in it)
MODULES = {
    "functional-web": (None, ()),
    "exploratory": (None, ()),
    "api": ("api", ("base_url",)),
    "accessibility": ("accessibility", ("pages",)),
    "mobile": ("mobile", ("platform", "flows")),
}
TRACKERS = ("excel", "azure-devops", "jira")
WITH_ADAPTER = ("excel",)  # the others are manual logging (methodology 4.2)


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
    git = shutil.which("git")
    if not git:
        return []
    inside = subprocess.run([git, "-C", workspace, "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True, check=False)
    if inside.returncode != 0:
        return []
    files = ([".env"] + ([".api-session.env"] if "api" in modules else [])
             + (["dashboard.html"] if proj.get("tracker") == "excel" else []))
    missing = [f for f in files if subprocess.run([git, "-C", workspace, "check-ignore", "-q", f],
                                                  capture_output=True, check=False).returncode != 0]
    return [f"{f} is not in the workspace's .gitignore (it has secrets or project data)" for f in missing]


def mobile_target(proj: dict) -> tuple[list[str], list[str]]:
    """The keys of mobile: that scripts/setup_emulator.py reads (plan 9.2): avd, image, build, permissions."""
    m = proj.get("mobile")
    if not isinstance(m, dict):
        return [], []
    problems, warnings = [], []
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
    elif proj.get("tracker") in TRACKERS and proj["tracker"] not in WITH_ADAPTER:
        warnings.append(f"tracker {proj['tracker']!r} has no adapter: results and bugs go to the tracker by hand, "
                        "from the run log, and there is no dashboard (methodology 4.2)")
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
    warnings += gitignore_warnings(workspace, proj, modules)
    warnings += mcp_warnings(workspace)
    if not history_protected(workspace):
        warnings.append("history/ is not protected: Edit(/history/**) is missing from permissions.deny in the "
                        "workspace's .claude/settings.json (copy <qa>/docs/workspace/settings.json; methodology "
                        "section 13)")

    if proj.get("tracker") == "excel":
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validates a workspace's project.yaml.")
    ap.add_argument("workspace")
    a = ap.parse_args(argv)
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
    return 1 if problems else 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
