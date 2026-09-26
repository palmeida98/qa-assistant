"""Passive security checks as pure functions (docs/qa-audit.md §16).

Each returns None when the check passes, or a dict describing what was seen. The module turns them into findings
and aggregates them per check across pages.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

VERSIONED = re.compile(r"\d+\.\d+")
DISCLOSING_HEADERS = ("server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version")
SESSION_NAME = re.compile(r"sess|sid|auth|token|jwt|login", re.IGNORECASE)
OLD_TLS = re.compile(r"^(SSL|TLS 1\.0|TLS 1\.1)\b", re.IGNORECASE)
NONCE_OR_HASH = re.compile(r"'(nonce-|sha256-|sha384-|sha512-)")


def is_https(url: str) -> bool:
    return urlsplit(url).scheme == "https"


def parse_csp(policy: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in (policy or "").split(";"):
        tokens = part.strip().split()
        if tokens:
            out.setdefault(tokens[0].lower(), tokens[1:])
    return out


def policies(headers: dict, meta_csp: list[str]) -> list[str]:
    """CSP from the header (possibly several, comma-joined by the browser) and from meta tags."""
    header = headers.get("content-security-policy", "")
    return [p for p in header.split(",") if p.strip()] + [m for m in meta_csp or [] if m.strip()]


def csp_missing(headers: dict, meta_csp: list[str]) -> dict | None:
    if policies(headers, meta_csp):
        return None
    return {"observed": "no Content-Security-Policy header or meta tag"}


def csp_unsafe(headers: dict, meta_csp: list[str]) -> dict | None:
    problems = []
    for policy in policies(headers, meta_csp):
        d = parse_csp(policy)
        src = d.get("script-src", d.get("default-src"))
        if src is None:
            continue
        joined = " ".join(src)
        if "'unsafe-inline'" in src and not NONCE_OR_HASH.search(joined) and "'strict-dynamic'" not in src:
            problems.append("'unsafe-inline'")
        if "'unsafe-eval'" in src:
            problems.append("'unsafe-eval'")
        if "*" in src:
            problems.append("*")
    if not problems:
        return None
    return {"observed": f"script sources allow {', '.join(dict.fromkeys(problems))}",
            "value": "; ".join(policies(headers, meta_csp))[:300]}


def clickjacking(headers: dict, meta_csp: list[str]) -> dict | None:
    xfo = headers.get("x-frame-options", "").strip().lower()
    if xfo in ("deny", "sameorigin"):
        return None
    # frame-ancestors is ignored in a meta tag by browsers: only the header counts
    header_policies = [p for p in headers.get("content-security-policy", "").split(",") if p.strip()]
    if any("frame-ancestors" in parse_csp(p) for p in header_policies):
        return None
    return {"observed": "neither X-Frame-Options (DENY/SAMEORIGIN) nor CSP frame-ancestors"
                        + (f"; X-Frame-Options is '{xfo}'" if xfo else "")}


def nosniff_missing(headers: dict) -> dict | None:
    if headers.get("x-content-type-options", "").strip().lower() == "nosniff":
        return None
    return {"observed": "no X-Content-Type-Options: nosniff"}


def hsts(headers: dict, min_age: int) -> tuple[str, dict] | None:
    """('hsts-missing' | 'hsts-short', info) or None. Only meaningful on https pages."""
    value = headers.get("strict-transport-security", "")
    if not value:
        return "hsts-missing", {"observed": "no Strict-Transport-Security header"}
    m = re.search(r"max-age\s*=\s*\"?(\d+)", value, re.IGNORECASE)
    age = int(m.group(1)) if m else 0
    if age < min_age:
        return "hsts-short", {"observed": f"max-age={age} (under {min_age} seconds)",
                              "value": f"Strict-Transport-Security: {value}"}
    return None


def version_disclosure(headers: dict) -> dict | None:
    shown = [f"{h}: {headers[h]}" for h in DISCLOSING_HEADERS if h in headers and VERSIONED.search(headers[h])]
    if not shown:
        return None
    return {"observed": "; ".join(shown)}


def insecure_references(refs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(active, passive) http:// references of an https page."""
    return [r for r in refs if r.get("active")], [r for r in refs if not r.get("active")]


def forms_to_http(forms: list[dict]) -> list[str]:
    return [f["action"] for f in forms or [] if str(f.get("action", "")).startswith("http:")]


def own_cookie(cookie: dict, host: str) -> bool:
    domain = (cookie.get("domain") or "").lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith("." + domain)


def cookie_findings(cookies: list[dict], host: str, https: bool) -> list[tuple[str, dict]]:
    """[(check, {"cookie": name, "observed": ...})] — names and flags only, never values."""
    out = []
    for c in cookies:
        if not own_cookie(c, host):
            continue
        name = c.get("name", "")
        declared = c.get("declared")      # SameSite in a Set-Cookie header; None: not seen in one (e.g. set by script)
        source = ("not set in a Set-Cookie header (set by JavaScript or before the audit)" if declared is None
                  else f"Set-Cookie SameSite={declared or '(absent)'}")
        flags = (f"Secure={c.get('secure')}, HttpOnly={c.get('httpOnly')}, SameSite as reported by the browser="
                 f"{c.get('sameSite')}; {source}")
        if https and not c.get("secure"):
            out.append(("cookie-not-secure", {"cookie": name, "observed": flags}))
        if (declared or "").lower() == "none" and not c.get("secure"):
            out.append(("cookie-samesite-none-insecure", {"cookie": name, "observed": flags}))
        elif not declared:
            out.append(("cookie-samesite-unset", {"cookie": name, "observed": flags}))
        if SESSION_NAME.search(name) and not c.get("httpOnly"):
            out.append(("cookie-session-not-httponly", {"cookie": name, "observed": flags}))
    return out


def tls_findings(tls: dict, expiry_days: int, now: float | None = None) -> list[tuple[str, dict]]:
    if not tls:
        return []
    out = []
    protocol = tls.get("protocol") or ""
    if OLD_TLS.match(protocol):
        out.append(("tls-old-protocol", {"observed": f"negotiated {protocol}"}))
    valid_to = tls.get("validTo")
    if valid_to:
        days = (float(valid_to) - (now or time.time())) / 86400
        if days < expiry_days:
            out.append(("tls-cert-expiring", {"observed": f"certificate for {tls.get('subjectName', '?')} "
                                                          f"expires in {max(days, 0):.1f} days",
                                              "value": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(valid_to))}))
    return out
