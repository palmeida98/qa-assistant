"""Functional checks as pure functions (docs/qa-audit.md §18)."""

from __future__ import annotations

import re

from ...browser.session import origin

# a request cancelled by navigation, by the page itself or by --block-third-party is not a failure
ABORTED = re.compile(r"ERR_ABORTED|NS_BINDING_ABORTED|blockedbyclient|ERR_BLOCKED_BY_CLIENT|cancelled|aborted",
                     re.IGNORECASE)
# console errors the browser writes for failed loads: already reported as broken-resource
LOAD_ERROR = re.compile(r"^Failed to load resource|^Loading (failed|module)|^NetworkError", re.IGNORECASE)
EMAIL_NAME = re.compile(r"e-?mail", re.IGNORECASE)


def resource_failures(failed: list[dict], own_origin: str) -> tuple[list[dict], list[dict]]:
    """(same-origin, third-party) failed subresources, aborted ones removed, one entry per URL."""
    own, third, seen = [], [], set()
    for r in failed or []:
        if r.get("error") and ABORTED.search(r["error"]):
            continue
        if r["url"] in seen or not r["url"].startswith(("http://", "https://")):
            continue
        seen.add(r["url"])
        (own if origin(r["url"]) == own_origin else third).append(r)
    return own, third


def describe_failure(r: dict) -> str:
    kind = f" ({r['type']})" if r.get("type") else ""
    return f"{r['url']}{kind}: " + (f"HTTP {r['status']}" if r.get("status") else r.get("error") or "failed")


def console_errors(messages: list[str]) -> list[str]:
    return [m for m in messages or [] if not LOAD_ERROR.search(m)]


def email_fields_without_validation(forms: list[dict]) -> list[dict]:
    """Fields that look like an e-mail address but accept any text in the browser."""
    out = []
    for form in forms or []:
        for f in form.get("fields") or []:
            looks = EMAIL_NAME.search(f.get("name", "")) or EMAIL_NAME.search(f.get("label", "")) \
                    or f.get("autocomplete", "").lower() == "email"
            if looks and f.get("tag") == "input" and f.get("type") not in ("email", "hidden") and not f.get("pattern"):
                out.append({"form_action": form.get("action", ""), **f})
    return out


def link_status_check(status: int | None) -> str | None:
    """broken-link / broken-link-server-error / None."""
    if status is None:
        return None
    if status >= 500:
        return "broken-link-server-error"
    if status >= 400:
        return "broken-link"
    return None
