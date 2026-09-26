"""Authentication before the crawl (docs/qa-audit.md §20): methods `form`, `manual` and `storage-state`.

One attempt only; success must be confirmed by `success_url` or `success_selector`. Credentials come from the
environment and are handed to the redactor, never written anywhere. `storage-state` reuses a session saved by
`qa-audit login` (§20.1): the state file is a credential, kept out of Git working trees and output folders.
"""

from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urljoin, urlsplit

METHODS = ("form", "manual", "storage-state")
LOGIN_METHODS = ("form", "manual")          # how a session is created (storage-state: auth.login_method)
PLANNED_METHODS: dict[str, str] = {}
HIDDEN_INPUT_CSS = "input[type=password]"
SESSIONS_GITIGNORE = "# saved browser sessions (qa-audit login): live cookies, never commit\n*\n"
MIN_REDACTED_COOKIE = 6                     # shorter cookie values ("1", "en") would redact ordinary text


class AuthError(RuntimeError):
    """The login did not succeed (exit code 3). Never retried."""


def configured(cfg: dict) -> bool:
    return bool(cfg.get("auth"))


def absolute(url: str, target: str) -> str:
    return urljoin(target, url)


def login_method(a: dict) -> str:
    """How the login is done: the method itself, or auth.login_method for storage-state (default manual)."""
    if a.get("method") == "storage-state":
        return a.get("login_method") or "manual"
    return a.get("method")


# --- saved sessions (§20.1) ------------------------------------------------------------------------------------
def sessions_root(directory: str | None = None) -> str:
    if directory:
        return os.path.abspath(directory)
    return os.path.join(os.path.expanduser("~"), ".qa-audit", "sessions")


def host_key(url: str) -> str:
    p = urlsplit(url)
    raw = f"{p.hostname}_{p.port}" if p.port else (p.hostname or "unknown")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-") or "unknown"


def state_path(cfg: dict, target: str) -> str:
    a = cfg.get("auth") or {}
    if a.get("state_file"):
        return os.path.abspath(os.path.expanduser(a["state_file"]))
    return os.path.join(sessions_root(), host_key(target) + ".json")


def git_work_tree(path: str) -> str | None:
    """The Git working tree that contains path (a folder with .git at or above it), or None."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:        # different drives on Windows
        return False


def state_location_problems(cfg: dict, target: str) -> list[str]:
    path = state_path(cfg, target)
    out = (cfg.get("output") or {}).get("directory")
    problems = []
    if out and _inside(path, out):
        problems.append(f"auth.state_file {path} is inside the output folder: a saved session is a credential "
                        "(docs/qa-audit.md §20.1)")
    tree = git_work_tree(path)
    if tree:
        problems.append(f"auth.state_file {path} is inside the Git working tree {tree}: a saved session is a "
                        "credential and must never be committed (docs/qa-audit.md §20.1)")
    return problems


def save_state(context, path: str) -> None:
    """Write the context's storage state; the default folder gets a .gitignore, the file is owner-only."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    gi = os.path.join(folder, ".gitignore")
    if folder == sessions_root() and not os.path.isfile(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(SESSIONS_GITIGNORE)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(context.storage_state(), f)
    os.replace(tmp, path)


def list_sessions(root: str) -> list[tuple[str, float, int]]:
    """[(host, age in hours, size in bytes)] of the saved sessions in root."""
    if not os.path.isdir(root):
        return []
    rows = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if name.endswith(".json") and os.path.isfile(p):
            rows.append((name[:-5], (time.time() - os.path.getmtime(p)) / 3600, os.path.getsize(p)))
    return rows


def delete_sessions(root: str, host: str) -> list[str]:
    removed = []
    for name, _, _ in list_sessions(root):
        if host in ("all", name):
            os.remove(os.path.join(root, name + ".json"))
            removed.append(name)
    return removed


def state_cookies(path: str) -> dict[str, str]:
    """{"cookie:NAME": value} of a saved state, for the redactor (values shorter than 6 characters skipped)."""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    return {f"cookie:{c.get('name')}": str(c["value"]) for c in state.get("cookies") or []
            if len(str(c.get("value") or "")) >= MIN_REDACTED_COOKIE}


def state_age(path: str) -> str:
    hours = (time.time() - os.path.getmtime(path)) / 3600
    if hours < 1:
        return f"{hours * 60:.0f} min"
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.0f} days"


def validate(cfg: dict, environ=None, target: str | None = None, mode: str = "audit") -> list[str]:
    """Problems in the auth section (exit 2 before the browser starts). mode "login": for `qa-audit login`."""
    a = cfg.get("auth")
    if not a:
        if mode == "login":
            return ["qa-audit login needs an auth section in the configuration (docs/qa-audit.md §20.1)"]
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
    if mode == "login" and method != "storage-state":
        problems.append("qa-audit login saves a session for auth.method storage-state (docs/qa-audit.md §20.1)")
    how = login_method(a)
    if method == "storage-state":
        if how not in LOGIN_METHODS:
            problems.append(f"auth.login_method must be one of {', '.join(LOGIN_METHODS)}")
        if not a.get("login_url"):
            problems.append("auth.login_url is required for method storage-state (session expiry and qa-audit login)")
        if target:
            where = state_location_problems(cfg, target)
            problems += where
            path = state_path(cfg, target)
            if mode == "audit" and not where and not os.path.isfile(path):
                problems.append(f"no saved session at {path}: create it with qa-audit login {target} "
                                "--config <this file> (docs/qa-audit.md §20.1)")
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
    if method == "form" or (method == "storage-state" and how == "form" and mode == "login"):
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


def secrets(cfg: dict, environ=None, target: str | None = None) -> dict[str, str]:
    """{name: value} for the redactor: the credentials from the environment and, with storage-state, the saved
    session's cookie values."""
    a = cfg.get("auth") or {}
    env = os.environ if environ is None else environ
    out = {a[k]: env[a[k]] for k in ("username_env", "pass_env") if a.get(k) and env.get(a[k])}
    if a.get("method") == "storage-state" and target:
        out.update(state_cookies(state_path(cfg, target)))
    return out


def logs_in(cfg: dict) -> bool:
    """True when the audit itself logs in (form, manual); storage-state starts from a saved session instead."""
    return (cfg.get("auth") or {}).get("method") in LOGIN_METHODS


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
    how = login_method(a)
    page = session.new_page()
    try:
        url = absolute(a.get("login_url") or target, target)
        page.goto(url, wait_until="load")
        if how == "form":
            _fill_and_submit(page, a, env)
            timeout = int(cfg["browser"]["timeout_ms"])
        else:
            progress("auth_manual", url=url, timeout_s=int(a.get("manual_timeout_s", 300)))
            timeout = int(a.get("manual_timeout_s", 300)) * 1000
        _wait_success(page, a, timeout)
        return f"logged in ({how}) at {urlsplit(page.url).path or '/'}"
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
