"""
One HTTP request with redacted evidence (technique-api.md; plan, task 3.1).

Replaces {{VARIABLE}} (URL, headers, body) with the values of the workspace's
.env and of the API session, makes the request and prints the evidence as JSON
with the secrets redacted: authentication headers, JSON keys with token /
password / secret / api_key in the name, and the literal value of any .env
variable or session secret. No secret reaches the output.

    python scripts/api_request.py <workspace> <METHOD> <url|path>
        [-H "Name: value"]... [--json '{...}' | --json-file f.json]
        [--expected 201] [--save field=NAME]... [--save-secret field=NAME]...
        [--output runs/evidence/<case>.json] [--timeout 30]

- A relative path uses api.base_url from project.yaml. In Git Bash (Windows)
  write it without the leading slash (booking/1): the shell converts /booking
  into a Windows path before the script sees it.
- --save / --save-secret keep a field of the JSON response (e.g. token,
  bookingid, 0.bookingid) in <workspace>/.api-session.env, for the following
  requests to use {{NAME}}. Secrets are always redacted in the output.
  .api-session.env must be in the workspace's .gitignore.

Exit codes:
  0  request made (and, with --expected, same status code)
  1  request made, but the status code differs from --expected
  2  invalid usage or configuration (nothing was requested)
  3  no response (timeout, connection refused, DNS, TLS): Blocked by environment
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from common import (
    REDACTED,
    SECRET_HEADERS,
    Redactor,
    read_env,
    read_project,
    read_workspace_env,
)

SESSION = ".api-session.env"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class UsageError(Exception):
    """Invalid usage or configuration (code 2)."""


def read_session(workspace: str) -> tuple[dict[str, str], set[str]]:
    """Saved values and the set of those that are secrets (lines '#secret NAME')."""
    path = os.path.join(workspace, SESSION)
    values = read_env(path)
    secrets = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            secrets = {m.group(1) for m in re.finditer(r"^#secret (\w+)$", f.read(), re.MULTILINE)}
    return values, secrets


def save_session(workspace: str, name: str, value: str, secret: bool) -> None:
    values, secrets = read_session(workspace)
    values[name] = value
    (secrets.add if secret else secrets.discard)(name)
    with open(os.path.join(workspace, SESSION), "w", encoding="utf-8", newline="\n") as f:
        f.write("# qa-assistant API session (scripts/api_request.py) -- NOT for git\n")
        f.writelines(f"#secret {k}\n" for k in sorted(secrets))
        f.writelines(f"{k}={v}\n" for k, v in values.items())


def substitute(text: str, vars_: dict[str, str]) -> str:
    def swap(m):
        if m.group(1) not in vars_:
            raise UsageError(f"variable {{{{{m.group(1)}}}}} not defined in .env nor in the API session")
        return vars_[m.group(1)]
    return PLACEHOLDER.sub(swap, text)


def field(obj, path: str):
    """'token', 'booking.firstname', '0.bookingid'."""
    for part in path.split("."):
        if isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            raise UsageError(f"field '{path}' does not exist in the response")
    return obj


def base_url(workspace: str) -> str | None:
    try:
        proj = read_project(workspace)
    except FileNotFoundError:
        return None
    return ((proj.get("api") or {}).get("base_url") or "").rstrip("/") or None


def request(a) -> tuple[int, dict]:
    env = read_workspace_env(a.workspace)
    session, session_secrets = read_session(a.workspace)
    vars_ = {**os.environ, **env, **session}
    # redact by value: everything from .env (>= 4 characters) and the session's secrets
    by_value = {k: v for k, v in env.items() if len(v) >= 4}
    by_value.update({k: session[k] for k in session_secrets if k in session})
    red = Redactor(by_value)

    url = substitute(a.url, vars_)
    if re.match(r"^[A-Za-z]:[/\\]", url):
        # Git Bash (MSYS) converts arguments such as /booking into C:/Program Files/Git/booking
        raise UsageError(f"the path arrived converted by the shell ({red.text(url)}): write the path without the "
                         "leading slash (booking/1) or run with MSYS_NO_PATHCONV=1")
    if not re.match(r"^https?://", url):  # path relative to api.base_url, with or without a leading slash
        base = base_url(a.workspace)
        if not base:
            raise UsageError(f"relative path ({red.text(url)}) without api.base_url in the workspace's project.yaml")
        url = base + "/" + url.lstrip("/")

    headers = {}
    for h in a.H:
        name, sep, value = h.partition(":")
        if not sep:
            raise UsageError(f"header without ':' -- {h!r}")
        headers[name.strip()] = substitute(value.strip(), vars_)
    body = None
    if a.json_file:
        with open(a.json_file, encoding="utf-8-sig") as f:
            a.json = f.read()
    if a.json is not None:
        body = substitute(a.json, vars_)
        try:
            json.loads(body)
        except ValueError as e:
            raise UsageError(f"invalid --json: {e}") from None
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")

    req = urllib.request.Request(url, data=body.encode("utf-8") if body is not None else None,
                                 headers=headers, method=a.method.upper())
    evid = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "request": {"method": a.method.upper(), "url": red.text(url), "headers": red.headers(headers),
                    "body": red.body(body, a.max_body)},
    }
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as r:
            status, headers_r, text = r.status, dict(r.headers), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # 4xx/5xx: it is a response, not an environment failure
        with e:
            status, headers_r, text = e.code, dict(e.headers), e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        evid["error"] = f"no response: {red.text(str(getattr(e, 'reason', e)))}"
        return 3, evid
    evid["response"] = {"status": status, "time_ms": round((time.perf_counter() - t0) * 1000),
                        "headers": red.headers({k: v for k, v in headers_r.items()
                                                if k.lower() in {"content-type", "location"}
                                                or k.lower() in SECRET_HEADERS}),
                        "body": red.body(text, a.max_body)}

    to_save = [(g, False) for g in a.save] + [(g, True) for g in a.save_secret]
    if to_save:
        try:
            data = json.loads(text)
        except ValueError:
            raise UsageError("--save needs a JSON response") from None
        evid["saved"] = {}
        for g, secret in to_save:
            path, sep, name = g.partition("=")
            if not sep or not re.match(r"^[A-Za-z_]\w*$", name):
                raise UsageError(f"--save expects field=NAME -- {g!r}")
            value = str(field(data, path))
            save_session(a.workspace, name, value, secret)
            evid["saved"][name] = f"{REDACTED} ({len(value)} characters)" if secret else value

    if a.expected is not None:
        evid["expected"] = a.expected
        evid["matches"] = status == a.expected
        return (0 if evid["matches"] else 1), evid
    return 0, evid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HTTP request with redacted evidence (technique-api.md).")
    ap.add_argument("workspace")
    ap.add_argument("method")
    ap.add_argument("url")
    ap.add_argument("-H", action="append", default=[], metavar="'Name: value'")
    ap.add_argument("--json")
    ap.add_argument("--json-file")
    ap.add_argument("--expected", type=int)
    ap.add_argument("--save", action="append", default=[], metavar="field=NAME")
    ap.add_argument("--save-secret", action="append", default=[], metavar="field=NAME")
    ap.add_argument("--output", help="also write the evidence to this JSON file (relative to the workspace, "
                                     "unless absolute)")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--max-body", type=int, default=4000)
    a = ap.parse_args(argv)
    try:
        rc, evid = request(a)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    out = json.dumps(evid, ensure_ascii=False, indent=1)
    print(out)
    if a.output:
        target = a.output if os.path.isabs(a.output) else os.path.join(a.workspace, a.output)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(out + "\n")
    return rc


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
