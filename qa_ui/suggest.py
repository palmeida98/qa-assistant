"""
Which type of test fits each case (docs/qa-ui.md section 5): deterministic rules, and the cards built from them.

    assign(case)            -> {"module", "signals": [{"module", "signal", "weight"}], "why"}
    suggest(cases, ...)     -> {"assignments": {id: {...}}, "cards": [...]}

Every suggestion names the signals it came from; nothing here starts anything.
"""

from __future__ import annotations

import os
import re
import shutil

from . import MODULES

TIE = ("api", "mobile", "accessibility", "exploratory")     # a tie goes to the first of these
DEFAULT = "functional-web"
# the default of a project without functional-web: its first active module of this order (exploratory last: a charter
# is a choice, not a fallback)
DEFAULT_ORDER = ("functional-web", "mobile", "api", "accessibility", "exploratory")

SKILLS = {"functional-web": "/run-batch", "exploratory": "/run-batch", "api": "/test-api",
          "accessibility": "/audit-accessibility", "mobile": "/test-mobile"}
TITLES = {"functional-web": "Functional web", "exploratory": "Exploratory", "api": "API",
          "accessibility": "Accessibility (WCAG)", "mobile": "Mobile"}
TOOLS = {"functional-web": "Playwright MCP", "exploratory": "Playwright MCP", "api": "scripts/api_request.py",
         "accessibility": "qa-audit engine, then the guided manual part", "mobile": "Maestro and the device driver"}
# the checks of validate_project.environment each card depends on (by the check's name, or its prefix)
NEEDS = {"functional-web": (".env", "skills", "node"), "exploratory": (".env", "skills", "node"),
         "api": (".env", "skills"), "accessibility": (".env", "skills", "node", "qa-audit", "browser "),
         "mobile": (".env", "skills", "maestro", "adb", "emulator", "xcrun", "build"),
         "audit-a11y": (".env", "qa-audit", "browser "), "audit-site": ("qa-audit", "browser ")}
AUDITS = ("audit-a11y", "audit-site")

# exploratory charters on the app, in a project that tests a mobile app and not a web site (exploratory_on_mobile)
MOBILE_EXPLORATORY_TOOL = "the device driver of /test-mobile (a mobile project: charters on the app)"

ID_PARTS = {"API": "api", "MOB": "mobile", "A11Y": "accessibility", "EXP": "exploratory"}
HTTP_CALL = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S*")
STATUS_FIRST = re.compile(r"\s*(?:HTTP\s+)?[1-5](?:\d\d|xx)\b", re.IGNORECASE)


def _words(*words: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


# (module, weight, pattern): each distinct word found counts once
WORD_SIGNALS = (
    ("api", 1, _words("endpoint", "status code", "http status", "request", "response", "payload", "json",
                      "graphql")),
    ("api", 1, re.compile(r"\bREST\b")),
    ("mobile", 1, _words("android", "ios", "emulator", "device", "tap", "swipe", "push notification", "apk",
                         "the app")),
    ("accessibility", 3, _words("wcag")),
    ("accessibility", 1, _words("accessibility", "screen reader", "keyboard", "focus", "contrast", "aria",
                                "alt text", "reflow", "zoom", "tab order")),
    ("exploratory", 2, _words("charter", "explore", "exploratory")),
    ("functional-web", 1, _words("page", "click", "button", "form", "browser", "link", "login", "url")),
)
FORM_LIKE = _words("form", "login", "log in", "sign in", "sign up", "checkout", "registration", "register",
                   "payment")


def signals(case: dict) -> list[dict]:
    """Every signal found in the case, as {"module", "signal", "weight"}."""
    out = []

    def add(module: str, signal: str, weight: int):
        out.append({"module": module, "signal": signal, "weight": weight})

    for part in re.split(r"[-_.]", str(case.get("id") or "").upper()):
        if part in ID_PARTS:
            add(ID_PARTS[part], f"ID part {part}", 3)
    platform = str(case.get("platform") or "").strip().lower()
    if platform in ("android", "ios", "mobile"):
        add("mobile", f"platform {platform}", 3)
    text = " \n".join(str(case.get(f) or "") for f in ("title", "preconditions", "steps", "expected"))
    call = HTTP_CALL.search(text)
    if call:
        add("api", f"HTTP call {call.group(0)}", 3)
    if STATUS_FIRST.match(str(case.get("expected") or "")):
        add("api", "the expected result is an HTTP status", 1)
    for module, weight, pattern in WORD_SIGNALS:
        for word in dict.fromkeys(m.group(0).lower() for m in pattern.finditer(text)):
            add(module, f'"{word}"', weight)
    return out


def default_module(active=None) -> str:
    """The module of a case with no signal: functional-web, or, in a project that does not have it, its first active
    module of DEFAULT_ORDER (a mobile-only project: mobile)."""
    if not active:
        return DEFAULT
    return next((m for m in DEFAULT_ORDER if m in active), DEFAULT)


def assign(case: dict, active=None) -> dict:
    """The module of one case. A valid `module` field is kept; otherwise the highest total of signals wins, a tie
    goes to the first of TIE, and a case with no signal gets the default. With `active` (the project's modules),
    only those are chosen by the rules: the signals of an inactive module are named in the reason, never followed,
    and the default is the project's (default_module)."""
    given = str(case.get("module") or "").strip().lower()
    if given in MODULES:
        return {"module": given, "signals": [{"module": given, "signal": "the case's own module field", "weight": 0}],
                "why": "the case names its module"}
    found = signals(case)
    totals = dict.fromkeys(MODULES, 0)
    for s in found:
        totals[s["module"]] += s["weight"]
    allowed = [m for m in MODULES if not active or m in active] or list(MODULES)
    ignored = sorted({m for m in MODULES if totals[m] and m not in allowed})
    note = f" (signals of {', '.join(ignored)}, not active in project.yaml)" if ignored else ""
    best = max(totals[m] for m in allowed)
    if best == 0:
        module = default_module(active)
        why = "no signal: the default" if module == DEFAULT else f"no signal: the project's default ({module})"
        return {"module": module, "signals": found, "why": why + note}
    top = [m for m in allowed if totals[m] == best]
    module = next((m for m in TIE if m in top), top[0])
    mine = [s["signal"] for s in found if s["module"] == module]
    why = ", ".join(mine) + (f" (a tie with {', '.join(m for m in top if m != module)})" if len(top) > 1 else "")
    return {"module": module, "signals": found, "why": why + note}


def exploratory_on_mobile(proj: dict) -> bool:
    """Exploratory charters run on the app, with the mobile tools, when the project has `mobile` and not
    `functional-web` among its modules (there is no web site to explore); otherwise in the browser."""
    active = set(proj.get("modules") or [])
    return "mobile" in active and "functional-web" not in active


def skill_for(module: str, proj: dict) -> str:
    """The skill a batch of the module runs: /test-mobile for exploratory charters on the app."""
    return SKILLS["mobile"] if module == "exploratory" and exploratory_on_mobile(proj) else SKILLS[module]


def find_test_environment(proj: dict) -> tuple[str | None, str]:
    """(key, reason): the test environment of project.yaml — the key `test`, or the only one. Never guessed."""
    envs = {str(k): str(v or "") for k, v in (proj.get("environments") or {}).items() if v}
    if "test" in envs:
        return "test", ""
    if len(envs) == 1:
        return next(iter(envs)), ""
    if not envs:
        return None, "project.yaml has no environment with a URL"
    return None, f"project.yaml has several environments ({', '.join(envs)}) and none is named `test`"


def find_claude(path: str | None = None) -> str | None:
    """The Claude Code executable: --claude PATH, else `claude` on the PATH."""
    if path:
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    return shutil.which("claude")


def _blockers(key: str, checks, active: bool, claude: str | None, proj: dict) -> list[str]:
    out = []
    needs = NEEDS["mobile"] if key == "exploratory" and exploratory_on_mobile(proj) else NEEDS[key]
    module = key if key in MODULES else "accessibility" if key == "audit-a11y" else None
    if module and not active:
        out.append(f"`{module}` is not in `modules:` of project.yaml: it is added at the onboarding, with its "
                   "section (methodology 4.1)")
    for ok, tool, detail in checks:
        if not ok and any(tool == n or (n.endswith(" ") and tool.startswith(n)) for n in needs):
            out.append(f"{tool}: {detail}")
    if key in MODULES and not claude:
        out.append("Claude Code was not found on this computer (the `claude` executable, or --claude PATH)")
    if key == "audit-a11y" and active and not ((proj.get("accessibility") or {}).get("pages")):
        out.append("`accessibility.pages` of project.yaml is empty: there is no page to audit")
    if key == "audit-site":
        env, reason = find_test_environment(proj)
        if not env:
            out.append(reason)
    return out


def cards(cases: list[dict], assignments: dict, proj: dict, checks, claude: str | None) -> list[dict]:
    """One card per module and per automated audit (docs/qa-ui.md section 5.2). `checks` is the list of
    validate_project.environment; `claude` the executable found, or None."""
    active = set(proj.get("modules") or [])
    by_module = {m: [c["id"] for c in cases if assignments.get(c["id"], {}).get("module") == m] for m in MODULES}
    web_modules = ("functional-web",) if exploratory_on_mobile(proj) else ("functional-web", "exploratory")
    web = [c for c in cases if assignments.get(c["id"], {}).get("module") in web_modules]
    out = []
    for m in MODULES:
        ids = by_module[m]
        blockers = _blockers(m, checks, m in active, claude, proj)
        on_app = m == "exploratory" and exploratory_on_mobile(proj)
        out.append({"key": m, "kind": "module", "title": TITLES[m], "runs": skill_for(m, proj),
                    "tool": MOBILE_EXPLORATORY_TOOL if on_app else TOOLS[m],
                    "level": "recommended" if ids else "not-suggested",
                    "reason": (f"{len(ids)} case(s) assigned by the rules." if ids
                               else "No case was assigned to this module."),
                    "cases": ids, "active": m in active, "ready": not blockers, "blockers": blockers})
    form_like = [c["id"] for c in web if FORM_LIKE.search(" ".join(str(c.get(f) or "")
                                                                   for f in ("title", "steps", "expected")))]
    a11y_active = "accessibility" in active
    blockers = _blockers("audit-a11y", checks, a11y_active, claude, proj)
    out.append({"key": "audit-a11y", "kind": "audit", "title": "Automated accessibility audit",
                "runs": "scripts/audit_a11y.py", "tool": "qa-audit engine (axe-core, keyboard, reflow)",
                "level": "optional" if form_like and a11y_active else "not-suggested",
                "reason": (f"{len(form_like)} web case(s) go through a form, a login or a checkout "
                           f"({', '.join(form_like[:5])}{'…' if len(form_like) > 5 else ''}). A first pass on the "
                           "pages of accessibility.pages; its findings are not case results."
                           if form_like and a11y_active else
                           "The accessibility module is not active in project.yaml." if not a11y_active else
                           "No web case goes through a form, a login or a checkout."),
                "cases": [], "active": a11y_active, "ready": not blockers, "blockers": blockers})
    blockers = _blockers("audit-site", checks, True, claude, proj)
    env, _ = find_test_environment(proj)
    out.append({"key": "audit-site", "kind": "audit", "title": "Automated site audit",
                "runs": f"qa-audit <{env or 'test environment'}>", "tool": "qa-audit engine (GET navigation only)",
                "level": "optional" if web else "not-suggested",
                "reason": ("A first pass on the test environment's URL: security headers, performance, broken "
                           "links. Its findings are not case results." if web else "There is no web case."),
                "cases": [], "active": True, "ready": not blockers, "blockers": blockers})
    return out


def suggest(cases: list[dict], proj: dict, checks, claude: str | None, moved: dict | None = None) -> dict:
    """The module of each case and the cards. `moved` is {case ID: module} chosen by the user: it wins, and the
    assignment says so."""
    assignments = {}
    active = set(proj.get("modules") or [])
    for case in cases:
        a = assign(case, active)
        target = (moved or {}).get(case["id"])
        if target in MODULES and target != a["module"]:
            a = {**a, "module": target, "why": f"moved by the user (the rules said {a['module']})", "moved": True}
        assignments[case["id"]] = a
    return {"assignments": assignments, "cards": cards(cases, assignments, proj, checks, claude)}
