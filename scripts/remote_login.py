"""
The manual login of a remote run, through an issue of the workspace's private repository (technique-mobile.md
§11.2; plan 11.3). The runner cannot read the user's e-mail: the app sends a one-time link, the repository's owner
pastes it as a comment, the runner opens it on the emulator and deletes the comment.

    python scripts/remote_login.py <workspace> request [--run-id ID] [--serial S]
    python scripts/remote_login.py <workspace> wait [--run-id ID] [--serial S] [--interval 15]

- request: runs the helper flow ci.login.flow (it asks the app for the link, with ${MAESTRO_<VAR>} from .env;
  scripts/run_mobile.py's run_helper), then opens — or reuses, when it is still open — the issue
  "Login needed — <run id>", mentioning the user allowed to answer (ci.login.user, default the repository's owner),
  with what to paste and the time limit.
- wait: polls the issue's comments every --interval seconds for up to ci.login.timeout_minutes. Only a comment by
  that user with a URL whose host is one of environments or ci.login.hosts (or a subdomain of one) counts: the URL
  is masked in the Actions log (::add-mask::), opened on the device in-process (device.py's open_link: on no
  command line of this computer), the comment is DELETED, the issue closed, and — with ci.login.screen_text — the
  app is checked to have left the login screen. Any other comment is ignored and reported (who, never its text).
  No link in time: the issue is closed saying so, exit 3, and the batch is Blocked (login), never failed.
- The device: --serial, else $QA_MOBILE_SERIAL (the runner's emulator, plan 11.8), else mobile.serial, else the only
  one connected; the input likewise from $QA_MOBILE_INPUT (device.py's configuration, common.mobile_target).
- The URL never reaches a file, a log line, an issue comment nor the pull request. The run id is $GITHUB_RUN_ID on a
  runner. The GitHub API goes through `gh api` (one function; the tests replace it with a fake GitHub).

Exit codes: 0 logged in (or ci.login.mode none: nothing to do) · 2 usage or configuration · 3 Blocked (login): the
helper flow failed, no link in time, the link was not opened, the app still shows the login screen, GitHub out of
reach
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import device as dv
import run_mobile
from common import read_project
from workspace_build import UsageError as BuildUsage
from workspace_build import repository

TITLE = "Login needed — {run_id}"
LIST_TRIES, LIST_PAUSE_S = 6, 5
URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
SCREEN_WAIT_S, SCREEN_POLL_S = 60, 5


class UsageError(Exception):
    """Usage or configuration (code 2)."""


class Blocked(Exception):
    """The login could not be done (code 3): the batch is Blocked (login)."""


# --- the doors to the outside (the tests replace them) ----------------------------------------------------------

def gh_api(method: str, path: str, fields: dict | None = None):
    """`gh api` → the parsed JSON answer (None for an empty one). Blocked when gh is missing or fails."""
    exe = shutil.which("gh")
    if not exe:
        raise Blocked("gh is not installed: the issue cannot be read (on a runner, gh is preinstalled)")
    cmd = [exe, "api", "-X", method, path, *[x for k, v in (fields or {}).items() for x in ("-f", f"{k}={v}")]]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                       timeout=120)
    if r.returncode != 0:
        raise Blocked(f"GitHub API {method} {path}: {(r.stderr or r.stdout).strip()[:200]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def now() -> float:
    return time.monotonic()


def pause(s: float) -> None:
    time.sleep(s)


# --- configuration ----------------------------------------------------------------------------------------------

def configuration(workspace: str, run_id: str | None) -> dict:
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    login = ((proj.get("ci") or {}).get("login") or {}) if isinstance(proj.get("ci"), dict) else {}
    mode = login.get("mode", "none")
    try:
        slug = repository(workspace)
    except BuildUsage as e:
        raise UsageError(str(e)) from None
    hosts = {urlsplit(str(u)).hostname for u in (proj.get("environments") or {}).values() if u}
    hosts |= {str(h).lower() for h in login.get("hosts") or []}
    return {"mode": mode, "flow": login.get("flow"), "timeout_s": int(login.get("timeout_minutes") or 30) * 60,
            "screen_text": login.get("screen_text") or "", "user": str(login.get("user") or slug.split("/")[-2]),
            "hosts": sorted(h.lower() for h in hosts if h), "slug": slug,
            "run_id": run_id or os.environ.get("GITHUB_RUN_ID") or datetime.now().strftime("local-%Y%m%d%H%M")}


def allowed(url: str, hosts: list[str]) -> bool:
    host = (urlsplit(dv.unwrap(url)).hostname or "").lower()
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


def link_in(body: str, hosts: list[str]) -> str | None:
    """The first URL of the comment whose host is allowed (a markdown link counts by its target)."""
    for m in URL.finditer(body or ""):
        url = m.group(0).rstrip(".,;:!?*_")
        if allowed(url, hosts):
            return url
    return None


def run_url() -> str:
    server, repo, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else ""


def find_issue(cfg: dict) -> dict | None:
    title = TITLE.format(run_id=cfg["run_id"])
    issues = gh_api("GET", f"repos/{cfg['slug']}/issues?state=open&per_page=100") or []
    return next((i for i in issues if i.get("title") == title and "pull_request" not in i), None)


def issue_file(cfg: dict) -> str:
    """Where `request` leaves the issue's number for `wait` (the runner's temp folder, never the workspace)."""
    return os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), f"qa-login-{cfg['run_id']}.json")


def issue_number(cfg: dict) -> int | None:
    """The number `request` left; else the open issue found by its title, searched for a little while: GitHub's
    listing of open issues lags behind a new issue (11.8 pilot: `wait` did not find the issue just opened)."""
    try:
        with open(issue_file(cfg), encoding="utf-8") as f:
            return int(json.load(f)["number"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for attempt in range(LIST_TRIES):
        issue = find_issue(cfg)
        if issue:
            return int(issue["number"])
        if attempt < LIST_TRIES - 1:
            pause(LIST_PAUSE_S)
    return None


def remember(cfg: dict, number: int) -> None:
    with open(issue_file(cfg), "w", encoding="utf-8") as f:
        json.dump({"number": number}, f)


# --- request ----------------------------------------------------------------------------------------------------

def cmd_request(workspace: str, cfg: dict, a) -> int:
    if not cfg["flow"]:
        raise UsageError("ci.login.flow is empty: the helper flow that asks the app for the link")
    code, message = run_mobile.run_helper(workspace, cfg["flow"], a.serial)
    if code == 2:
        raise UsageError(message)
    if code != 0:
        raise Blocked(f"the helper flow did not ask for the link: {message}")
    print(message)
    issue = find_issue(cfg)
    minutes = cfg["timeout_s"] // 60
    if issue:
        remember(cfg, int(issue["number"]))
        print(f"issue #{issue['number']}: already open, reused")
        return 0
    run = run_url()
    body = (f"@{cfg['user']} the test run {cfg['run_id']}{f' ({run})' if run else ''} needs a login.\n\n"
            "The app has just sent the login link to the test account's e-mail. Copy the link **as text** and paste "
            "it as a comment of this issue, with nothing else. Do not open it in your browser first: that spends "
            "it.\n\n"
            f"Waiting {minutes} minutes. Only a comment by @{cfg['user']} with a link of the project's hosts "
            f"({', '.join(cfg['hosts'])}) is used; the runner opens it on the emulator, **deletes the comment** "
            "and closes this issue. With no link in time, the batch is Blocked (login).")
    created = gh_api("POST", f"repos/{cfg['slug']}/issues", {"title": TITLE.format(run_id=cfg["run_id"]),
                                                            "body": body})
    remember(cfg, int(created["number"]))
    print(f"issue #{created['number']}: opened, waiting for @{cfg['user']} ({minutes} min)")
    return 0


# --- wait -------------------------------------------------------------------------------------------------------

def close(cfg: dict, number: int, note: str) -> None:
    gh_api("POST", f"repos/{cfg['slug']}/issues/{number}/comments", {"body": note})
    gh_api("PATCH", f"repos/{cfg['slug']}/issues/{number}", {"state": "closed"})


def login_screen_left(dcfg: dict, serial: str, text: str) -> bool:
    """The app is in the foreground and the login screen's text is gone, within SCREEN_WAIT_S."""
    begin = now()
    while True:
        try:
            nodes = dv.read_screen(dcfg, serial)
            if not any(text.casefold() in n.text.casefold() for n in nodes):
                return True
        except dv.NoTarget:
            pass   # the browser may still be in front while it hands the link to the app
        if now() - begin >= SCREEN_WAIT_S:
            return False
        pause(SCREEN_POLL_S)


def use_link(workspace: str, cfg: dict, a, url: str) -> str:
    """Opens the link on the device; returns its host. The URL is masked first on a runner."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{url}", flush=True)    # the runner hides it from every later line of the log
    try:
        dcfg = dv.configuration(workspace, a.serial)
        serial = dv.resolve_serial(dcfg["serial"])
        return dv.open_link(dcfg, serial, url)
    except dv.UsageError as e:
        raise UsageError(str(e)) from None
    except (dv.NoTarget, dv.NotFound) as e:
        raise Blocked(f"the link was not opened on the device: {e}") from None


def cmd_wait(workspace: str, cfg: dict, a) -> int:
    number = issue_number(cfg)
    if number is None:
        raise UsageError(f"no open issue \"{TITLE.format(run_id=cfg['run_id'])}\": run `request` first")
    seen, reported = set(), set()
    begin = now()
    print(f"issue #{number}: waiting up to {cfg['timeout_s'] // 60} min for a link by @{cfg['user']}", flush=True)
    while True:
        comments = gh_api("GET", f"repos/{cfg['slug']}/issues/{number}/comments?per_page=100") or []
        for c in comments:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            user = (c.get("user") or {})
            login = str(user.get("login") or "")
            if user.get("type") == "Bot" or login.endswith("[bot]"):
                continue
            if login.casefold() != cfg["user"].casefold():
                if login not in reported:
                    print(f"ignored: a comment by @{login} (only @{cfg['user']} may paste the link)", flush=True)
                    reported.add(login)
                continue
            url = link_in(c.get("body") or "", cfg["hosts"])
            if not url:
                print(f"ignored: a comment by @{login} without a link of {', '.join(cfg['hosts'])}", flush=True)
                continue
            failure = None
            try:
                host = use_link(workspace, cfg, a, url)
            except Blocked as e:
                failure = e
            finally:
                gh_api("DELETE", f"repos/{cfg['slug']}/issues/comments/{c['id']}")   # the link is a credential
                print("comment with the link: deleted", flush=True)
            if failure:
                close(cfg, number, "The link could not be opened on the emulator (see the run's log); the comment "
                                   "was deleted. Start the run again for a new link.")
                raise failure
            close(cfg, number, f"Link received from @{login} and opened on the emulator ({host}); the comment was "
                               "deleted.")
            print(f"{host}: opened; issue #{number} closed")
            if not cfg["screen_text"]:
                print("login screen: not checked (ci.login.screen_text not set)")
                return 0
            dcfg = dv.configuration(workspace, a.serial)
            if not login_screen_left(dcfg, dv.resolve_serial(dcfg["serial"]), cfg["screen_text"]):
                raise Blocked(f"the app still shows the login screen ({cfg['screen_text']!r}) {SCREEN_WAIT_S} s "
                              "after the link: expired or already used? Start the run again for a new link")
            print("login screen: left")
            return 0
        if now() - begin >= cfg["timeout_s"]:
            close(cfg, number, f"No link received in {cfg['timeout_s'] // 60} minutes: the batch is Blocked "
                               "(login). Start the run again when you can answer.")
            raise Blocked(f"no link by @{cfg['user']} in {cfg['timeout_s'] // 60} minutes")
        pause(a.interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The manual login of a remote run, through an issue.")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, text in (("request", "run the helper flow and open the issue"),
                       ("wait", "wait for the owner's comment, open the link, delete the comment")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--run-id", help="default: $GITHUB_RUN_ID")
        p.add_argument("--serial",
                       help="device (default: $QA_MOBILE_SERIAL, mobile.serial, or the only one connected)")
        if name == "wait":
            p.add_argument("--interval", type=float, default=15, help="seconds between readings (default 15)")
    a = ap.parse_args(argv)
    workspace = os.path.abspath(a.workspace)
    try:
        cfg = configuration(workspace, a.run_id)
        if cfg["mode"] != "manual-issue":
            print(f"ci.login.mode is {cfg['mode']!r}: no login through an issue")
            return 0
        return {"request": cmd_request, "wait": cmd_wait}[a.command](workspace, cfg, a)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (Blocked, subprocess.TimeoutExpired) as e:
        print(f"BLOCKED (login): {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
