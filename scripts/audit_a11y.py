"""
Automated accessibility audit (technique-accessibility.md section 2; plan, tasks
3.2 and 8.10): the pages declared in project.yaml audited by the qa-audit engine
(docs/qa-audit.md section 11), with the result mapped to WCAG criteria and level
(A/AA) in the workspace's format.

    python scripts/audit_a11y.py <workspace> [--output runs/a11y_<date>.json]

Needs the engine installed once per machine (pip install -e . and a Playwright
browser; `qa-audit doctor`). The pages without login run in an anonymous run and
those with login in another, after a login through the declared form
(accessibility.login). The credentials come from the workspace's .env and reach the
engine through environment variables, never through arguments nor files.

Exit codes:
  0  audit done (violations are results, not errors)
  2  invalid configuration or engine not installed (nothing run)
  3  the browser did not start, the login failed or some page was not audited (Blocked by environment)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from urllib.parse import urlsplit

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import read_project, read_workspace_env

STANDARDS = {
    "WCAG 2.0 AA": ["wcag2a", "wcag2aa"],
    "WCAG 2.1 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    "WCAG 2.2 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
}
BROWSERS = ("chromium", "firefox", "webkit")
LEVELS = ["A", "AA", "AAA"]
# references of the engine's findings (docs/qa-audit.md section 11)
REF_CRITERION = re.compile(r"^WCAG (\d+\.\d+\.\d+)$")
REF_LEVEL = re.compile(r"^WCAG (\d)\.(\d) (A{1,3})$")
ELEMENTS = re.compile(r"^(\d+) element")
# variables with the credentials, only in the engine process's environment
USER_VAR, PASS_VAR = "QA_A11Y_USER", "QA_A11Y_PASS"
# the engine's own checks: leads for the manual part (technique-accessibility.md section 2)
OTHER_CHECKS = {"keyboard-trap": ["2.1.2"], "focus-not-visible": ["2.4.7"], "focus-visible": ["2.4.7"],
                "reflow": ["1.4.10"]}


class ConfigError(Exception):
    """Invalid configuration or engine not installed (code 2)."""


def _no_slash(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/")


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def tag_of_reference(ref: str) -> str | None:
    """'WCAG 2.1 AA' -> 'wcag21aa', 'WCAG 2.0 A' -> 'wcag2a' (the axe tags)."""
    m = REF_LEVEL.match(ref)
    if not m:
        return None
    return f"wcag{m.group(1)}{'' if m.group(2) == '0' else m.group(2)}{m.group(3).lower()}"


def criteria(refs: list[str]) -> list[str]:
    return [m.group(1) for r in refs or [] if (m := REF_CRITERION.match(r))]


def level(refs: list[str], standard: str) -> str | None:
    """The rule's lowest WCAG level within the standard; None outside the standard (e.g. only best-practice)."""
    in_scope = set(STANDARDS[standard])
    tags = [tag_of_reference(r) for r in refs or []]
    tags = [t for t in tags if t in in_scope]
    if not tags:
        return None
    return min((re.search(r"a+$", t).group(0).upper() for t in tags), key=LEVELS.index)


# --- configuration -------------------------------------------------------------------------------------------

def _default_success(login_url: str) -> str:
    """Regex that matches any URL except the login page's (with or without a slash, query or anchor)."""
    base = re.escape(_no_slash(login_url.split("?")[0]))
    return rf"^(?!{base}/?(?:[?#].*)?$)"


def configuration(workspace: str) -> tuple[dict, dict]:
    """(conf, env): conf has the standard, the browser, the warnings and the page groups, each with the engine's
    configuration; env has the credentials (only when there are pages with login) and never goes into conf."""
    try:
        a = read_project(workspace).get("accessibility") or {}
    except FileNotFoundError as e:
        raise ConfigError(str(e)) from None
    pages = a.get("pages") or []
    if not pages:
        raise ConfigError("accessibility.pages is empty in project.yaml")
    for p in pages:
        if not p.get("name") or not p.get("url") or urlsplit(p["url"]).scheme not in ("http", "https"):
            raise ConfigError(f"accessibility.pages: each page needs a name and an http(s) url ({p!r})")
    standard = a.get("standard") or "WCAG 2.1 AA"
    if standard not in STANDARDS:
        raise ConfigError(f"accessibility.standard {standard!r} not supported (valid: {list(STANDARDS)})")
    warnings = []
    browser = a.get("browser") or "chromium"
    if browser == "chrome":
        browser = "chromium"
        warnings.append("accessibility.browser 'chrome' treated as 'chromium' (the qa-audit engine uses the "
                        "Playwright browsers); update project.yaml")
    if browser not in BROWSERS:
        raise ConfigError(f"accessibility.browser {browser!r} not supported (valid: {list(BROWSERS)})")
    timeout_ms = int(a.get("timeout_ms") or 30000)

    login = a.get("login") or None
    env, auth = {}, None
    if any(p.get("login") for p in pages):
        if not login:
            raise ConfigError("there are pages with login: true but accessibility.login is missing")
        missing = [k for k in ("url", "user_field", "pass_field", "button", "user_var", "pass_var")
                   if not login.get(k)]
        if missing:
            raise ConfigError(f"accessibility.login incomplete: missing {missing}")
        vars_ = {**os.environ, **read_workspace_env(workspace)}
        for k in ("user_var", "pass_var"):
            if not vars_.get(login[k]):
                raise ConfigError(f"variable {login[k]} ({k}) has no value in the workspace's .env")
        env = {USER_VAR: vars_[login["user_var"]], PASS_VAR: vars_[login["pass_var"]]}
        auth = {"method": "form", "login_url": login["url"], "username_env": USER_VAR,
                "pass_env": PASS_VAR, "username_selector": login["user_field"],
                "pass_selector": login["pass_field"], "submit_selector": login["button"]}
        if login.get("success_url") or login.get("success_selector"):
            if login.get("success_url"):
                auth["success_url"] = login["success_url"]
            if login.get("success_selector"):
                auth["success_selector"] = login["success_selector"]
        else:
            auth["success_url"] = _default_success(login["url"])

    # one group per (login, origin): the engine only visits the start URL's origin and logs in once
    groups: dict[tuple, list] = {}
    for p in pages:
        groups.setdefault((bool(p.get("login")), _origin(p["url"])), []).append(
            {"name": p["name"], "url": p["url"], "login": bool(p.get("login"))})
    out = []
    for (with_login, _), pgs in groups.items():
        engine = {
            "browser": {"engine": browser, "timeout_ms": timeout_ms},
            "crawl": {"pages": [p["url"] for p in pgs[1:]], "follow_links": False, "max_pages": len(pgs),
                      "max_depth": 0, "audit_error_pages": bool(a.get("audit_error_pages"))},
            "evidence": {"screenshots": "off", "video": False, "trace": "off"},
            "output": {"formats": ["json"], "cleanup": False},
            "fail_on": "none",
            "modules": {"accessibility": {"tags": STANDARDS[standard] + ["best-practice"]}},
        }
        if with_login:
            engine["auth"] = auth
        out.append({"login": with_login, "target": pgs[0]["url"], "pages": pgs, "engine": engine})
    return {"standard": standard, "browser": browser, "warnings": warnings, "groups": out}, env


# --- engine --------------------------------------------------------------------------------------------------

def engine_installed() -> bool:
    r = subprocess.run([sys.executable, "-c", "import qa_audit.cli"], capture_output=True, check=False)
    return r.returncode == 0


def run_engine(group: dict, env: dict, folder: str) -> dict:
    """Runs qa-audit with the accessibility module only. Returns {"code", "result" | None, "error"}."""
    cfg = os.path.join(folder, "qa-audit.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        yaml.safe_dump(group["engine"], f, allow_unicode=True, sort_keys=False)
    output = os.path.join(folder, "output")
    cmd = [sys.executable, "-m", "qa_audit.cli", group["target"], "--config", cfg, "--type", "accessibility",
           "--output", output, "--report", "json", "--fail-on", "none", "--no-color"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"}, timeout=1800, check=False)
    files = glob.glob(os.path.join(output, "*", "results.json"))
    result = None
    if files:
        with open(files[0], encoding="utf-8") as f:
            result = json.load(f)
    lines = [ln.strip() for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
    return {"code": r.returncode, "result": result, "error": lines[-1][:300] if lines else "no output"}


# --- classification ------------------------------------------------------------------------------------------

def _engine_page(page: dict, result: dict | None) -> dict | None:
    for info in (result or {}).get("pages", []):
        if _no_slash(info.get("url")) == _no_slash(page["url"]):
            return info
    return None


def _elements(f: dict) -> int:
    m = ELEMENTS.match(f.get("actual") or "")
    return int(m.group(1)) if m else sum(1 for e in f.get("evidence") or [] if e.get("kind") == "html")


def _rule(f: dict, standard: str) -> dict:
    refs = f.get("references") or []
    return {"rule": f["check"].split("/", 1)[1], "criteria": criteria(refs), "level": level(refs, standard),
            "suggested_severity": f.get("severity"), "help": f.get("title"),
            "help_url": next((r for r in refs if r.startswith("http")), None), "elements": _elements(f),
            "examples": [{"target": e.get("label"), "html": e.get("value")}
                         for e in (f.get("evidence") or []) if e.get("kind") == "html"][:3]}


def classify(runs: list[dict], standard: str) -> dict:
    """runs: [{"group", "code", "result", "error"}]. Separates, per page, WCAG violations (within the
    standard), best practices, incomplete results and the engine's other checks."""
    pages, notes, browser = [], [], None
    for run in runs:
        res = run.get("result")
        if res:
            browser = browser or " ".join(x for x in (res.get("browser", {}).get("engine"),
                                                      res.get("browser", {}).get("version")) if x)
            notes += [n for n in res.get("notes", []) if n not in notes]
        skipped = {_no_slash(s.get("url")): s.get("reason") for s in (res or {}).get("crawl", {}).get("skipped", [])}
        for page in run["group"]["pages"]:
            out = {"name": page["name"], "url": page["url"], "final_url": None, "title": None, "error": None,
                   "login": page["login"], "wcag_violations": [], "best_practices": [], "incomplete": [],
                   "other_checks": []}
            pages.append(out)
            info = _engine_page(page, res)
            if res is None or (run["code"] in (2, 3, 4) and info is None):
                reason = {2: "configuration refused by the engine", 3: "target unreachable or login failed",
                          4: "the browser did not start"}.get(run["code"], "the engine did not run")
                out["error"] = f"{reason}: {run.get('error')}"
                continue
            if info is None:
                out["error"] = ("not visited: " + skipped[_no_slash(page["url"])] if _no_slash(page["url"]) in skipped
                                else "not visited by the engine (session ended before?)")
                continue
            out["final_url"], out["title"] = info.get("final_url"), info.get("title")
            if info.get("error"):
                out["error"] = info["error"]
                continue
            if _no_slash(info.get("final_url")) != _no_slash(page["url"]):
                # e.g. session lost -> the site returns the login page; auditing it would give a false Pass
                out["error"] = f"redirected to {info.get('final_url')} (login failed or session missing?)"
                continue
            if not info.get("audited"):
                out["error"] = "the engine visited the page but did not audit it"
                continue
            urls = {_no_slash(info.get("url")), _no_slash(info.get("final_url"))}
            for f in res.get("findings", []):
                if f.get("module") != "accessibility" or _no_slash(f.get("url")) not in urls:
                    continue
                if f["check"] == "axe-not-run":
                    out["error"] = f"axe-core did not run on this page: {f.get('description')}"
                elif f["check"].startswith("axe/"):
                    r = _rule(f, standard)
                    if f["status"] == "needs-human":
                        out["incomplete"].append({k: r[k] for k in ("rule", "criteria", "elements")})
                    elif r["level"]:
                        out["wcag_violations"].append(r)
                    else:
                        out["best_practices"].append(r)
                elif f["check"] in OTHER_CHECKS:
                    out["other_checks"].append({
                        "check": f["check"], "criteria": OTHER_CHECKS[f["check"]], "status": f["status"],
                        "summary": f.get("actual") or f.get("title"),
                        "selector": (f.get("element") or {}).get("selector")})
    return {"standard": standard, "browser": browser, "notes": notes, "pages": pages}


def text_summary(rep: dict) -> list[str]:
    lines = []
    for p in rep["pages"]:
        if p.get("error"):
            lines.append(f"{p['name']}: NOT AUDITED -- {p['error']}")
            continue
        v = p["wcag_violations"]
        det = "; ".join(f"{x['rule']} [{', '.join(x['criteria']) or '-'} {x['level'] or ''}] x{x['elements']}"
                        for x in v)
        others = "".join(f" | {o['check']} [{', '.join(o['criteria'])}] to confirm" for o in p["other_checks"])
        lines.append(f"{p['name']}: {len(v)} WCAG violation(s)" + (f" -- {det}" if det else "")
                     + f" | {len(p['best_practices'])} best practice(s) | {len(p['incomplete'])} incomplete"
                     + others)
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Automated accessibility audit (qa-audit engine).")
    ap.add_argument("workspace")
    ap.add_argument("--output", help="write the JSON report (relative to the workspace, unless absolute)")
    a = ap.parse_args(argv)
    try:
        conf, env = configuration(a.workspace)
        if not engine_installed():
            raise ConfigError("qa-audit engine not installed: pip install -e . (from the root of qa-assistant) "
                              "and then qa-audit doctor")
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    for w in conf["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    runs = []
    # the engine's configuration and report are temporary: only the workspace's report is kept
    with tempfile.TemporaryDirectory(prefix="qa-a11y-") as tmp:
        for i, group in enumerate(conf["groups"]):
            folder = os.path.join(tmp, str(i))
            os.makedirs(folder)
            try:
                run = run_engine(group, env if group["login"] else {}, folder)
            except subprocess.TimeoutExpired:
                run = {"code": 4, "result": None, "error": "the engine took more than 30 minutes"}
            if run["code"] == 2 and run["result"] is None:
                print(f"ERROR: the engine refused the generated configuration -- {run['error']}", file=sys.stderr)
                return 2
            runs.append({"group": group, **run})
    rep = classify(runs, conf["standard"])
    rep["ts"] = datetime.now().isoformat(timespec="seconds")
    for line in text_summary(rep):
        print(line)
    if a.output:
        target = a.output if os.path.isabs(a.output) else os.path.join(a.workspace, a.output)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"report: {target}")
    return 3 if any(p.get("error") for p in rep["pages"]) else 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
