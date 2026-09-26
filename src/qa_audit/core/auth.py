"""Authentication before the crawl (docs/qa-audit.md §20): methods `form` and `manual`.

One attempt only; success must be confirmed by `success_url` or `success_selector`. Credentials come from the
environment and are handed to the redactor, never written anywhere.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlsplit

METHODS = ("form", "manual")
PLANNED_METHODS = {"storage-state": "after 8.10"}
HIDDEN_INPUT_CSS = "input[type=password]"


class AuthError(RuntimeError):
    """The login did not succeed (exit code 3). Never retried."""


def configured(cfg: dict) -> bool:
    return bool(cfg.get("auth"))


def absolute(url: str, target: str) -> str:
    return urljoin(target, url)


def validate(cfg: dict, environ=None) -> list[str]:
    """Problems in the auth section (exit 2 before the browser starts)."""
    a = cfg.get("auth")
    if not a:
        return []
    env = os.environ if environ is None else environ
    if not isinstance(a, dict):
        return ["auth must be a mapping (docs/qa-audit.md §20)"]
    problems = []
    method = a.get("method")
    if method in PLANNED_METHODS:
        problems.append(f"auth.method {method!r} is not available yet (planned: {PLANNED_METHODS[method]})")
    elif method not in METHODS:
        problems.append(f"auth.method must be one of {', '.join(METHODS)}")
    if not a.get("success_url") and not a.get("success_selector"):
        problems.append("auth needs success_url (regular expression) or success_selector (CSS) to confirm the login")
    for key in ("success_url", "expired_url"):
        if a.get(key):
            try:
                re.compile(a[key])
            except re.error as e:
                problems.append(f"auth.{key}: invalid regular expression: {e}")
    if (a.get("success_url") and a.get("login_url") and not problems
            and re.search(a["success_url"], a["login_url"])):
        problems.append("auth.success_url also matches login_url: a failed login would look like a success")
    if method == "form":
        if not a.get("login_url"):
            problems.append("auth.login_url is required for method form")
        for key in ("username_env", "pass_env"):
            name = a.get(key)
            if not name:
                problems.append(f"auth.{key} is required for method form (the name of an environment variable)")
            elif not env.get(name):
                problems.append(f"environment variable {name} ({key}) is not set")
    if str(cfg.get("evidence", {}).get("trace", "off")) != "off":
        problems.append("evidence.trace cannot be used with auth: traces record what is typed, credentials included")
    timeout = a.get("manual_timeout_s", 300)
    if not isinstance(timeout, int) or timeout <= 0:
        problems.append("auth.manual_timeout_s must be a positive integer")
    return problems


def secrets(cfg: dict, environ=None) -> dict[str, str]:
    """{environment variable name: value} for the redactor."""
    a = cfg.get("auth") or {}
    env = os.environ if environ is None else environ
    return {a[k]: env[a[k]] for k in ("username_env", "pass_env") if a.get(k) and env.get(a[k])}


def expired_pattern(cfg: dict, target: str) -> re.Pattern | None:
    a = cfg.get("auth") or {}
    if not a:
        return None
    if a.get("expired_url"):
        return re.compile(a["expired_url"])
    if a.get("login_url"):
        path = urlsplit(absolute(a["login_url"], target)).path
        return re.compile(re.escape(path) + r"(\?|$)")
    return None


def login(session, cfg: dict, target: str, progress=None, environ=None) -> str:
    """Log in in the session's context; returns a one-line description. AuthError when it fails."""
    a = cfg["auth"]
    env = os.environ if environ is None else environ
    progress = progress or (lambda event, **data: None)
    page = session.new_page()
    try:
        url = absolute(a.get("login_url") or target, target)
        page.goto(url, wait_until="load")
        if a["method"] == "form":
            _fill_and_submit(page, a, env)
            timeout = int(cfg["browser"]["timeout_ms"])
        else:
            progress("auth_manual", url=url, timeout_s=int(a.get("manual_timeout_s", 300)))
            timeout = int(a.get("manual_timeout_s", 300)) * 1000
        _wait_success(page, a, timeout)
        return f"logged in ({a['method']}) at {urlsplit(page.url).path or '/'}"
    finally:
        page.close()


def _fill_and_submit(page, a: dict, env) -> None:
    from playwright.sync_api import Error as PWError

    try:
        pwd = page.locator(a.get("pass_selector") or f"{HIDDEN_INPUT_CSS} >> visible=true").first
        pwd.wait_for(state="visible", timeout=10000)
        if a.get("username_selector"):
            user = page.locator(a["username_selector"]).first
        else:
            # the visible text / e-mail field before the password, in the same form
            user_handle = pwd.evaluate_handle("""p => {
              const scope = p.form || document;
              const fields = [...scope.querySelectorAll('input')].filter(e =>
                ['text', 'email', 'tel', ''].includes((e.getAttribute('type') || '').toLowerCase()) &&
                e.getBoundingClientRect().width > 0);
              const before = fields.filter(e => e.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING);
              return before.length ? before[before.length - 1] : null;
            }""")
            user = user_handle.as_element()
            if user is None:
                raise AuthError("could not find the user name field before the password field; "
                                "set auth.username_selector")
        user.fill(env[a["username_env"]])
        pwd.fill(env[a["pass_env"]])
        if a.get("submit_selector"):
            page.locator(a["submit_selector"]).first.click()
        else:
            submit = pwd.evaluate_handle("""p => {
              const f = p.form; if (!f) return null;
              return f.querySelector('button[type=submit], input[type=submit], button:not([type])');
            }""").as_element()
            if submit is not None:
                submit.click()
            else:
                pwd.press("Enter")
    except PWError as e:
        raise AuthError(f"login form: {str(e).splitlines()[0]}") from e


def _wait_success(page, a: dict, timeout_ms: int) -> None:
    from playwright.sync_api import Error as PWError

    try:
        if a.get("success_url"):
            pattern = re.compile(a["success_url"])
            page.wait_for_url(lambda u: bool(pattern.search(u)), timeout=timeout_ms)
        if a.get("success_selector"):
            page.wait_for_selector(a["success_selector"], timeout=timeout_ms)
    except PWError as e:
        raise AuthError(f"login not confirmed within {timeout_ms // 1000} s (one attempt, not retried): "
                        f"{'success_url' if a.get('success_url') else 'success_selector'} not reached") from e
