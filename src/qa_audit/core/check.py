"""`qa-audit check`: audit a page another tool has open, in its current state (docs/qa-audit.md §22).

Connects over the Chrome DevTools Protocol to a browser a test launched, finds the tab by URL, runs the
accessibility checks on it without navigating, and disconnects without closing anything.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import re
import types
from urllib.parse import urlsplit

from .. import __version__
from . import config as config_mod
from .evidence import RunFolder, _slug
from .findings import assign_ids, sort_key, summarize
from .privacy import Redactor

CHECKS = ("axe", "keyboard", "reflow")
LOOPBACK_NAMES = {"localhost"}


class CheckUsageError(ValueError):
    """Exit 2: nothing was connected."""


class CheckUnreachable(RuntimeError):
    """Exit 3: the CDP endpoint cannot be reached or no tab matches."""


def validate_cdp(url: str) -> str:
    """A loopback http(s)/ws(s) endpoint only: a debugging port gives full control of the browser (§22)."""
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https", "ws", "wss") or not host:
        raise CheckUsageError(f"--cdp must be an http:// or ws:// address, not {url!r}")
    try:
        loopback = host in LOOPBACK_NAMES or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise CheckUsageError(f"--cdp must be a loopback address (127.0.0.1, localhost, ::1), not {host!r}: "
                              "a debugging port gives full control of the browser")
    return url


def parse_checks(text: str) -> list[str]:
    items = [x.strip().lower() for x in (text or "axe").split(",") if x.strip()]
    bad = [x for x in items if x not in CHECKS]
    if bad or "axe" not in items:
        raise CheckUsageError(f"--checks: use axe, optionally with keyboard and reflow (got {text!r})")
    return items


def pick_page(pages: list, pattern: str):
    """The most recent open tab whose URL matches pattern, or None."""
    rx = re.compile(pattern)
    matching = [p for p in pages if rx.search(p.url or "")]
    return matching[-1] if matching else None


def run_check(cdp: str, page_url: str, label: str, checks: list[str], output: str, screenshots: str = "on-problem",
              redact_env: list[str] | None = None, connect=None) -> dict:
    """Audit the matching page; returns the result dict (also written as check.json / check.md)."""
    from ..modules.accessibility.module import MODULE as ACCESSIBILITY
    from .runner import RunContext, _logger, _redact

    validate_cdp(cdp)
    try:
        re.compile(page_url)
    except re.error as e:
        raise CheckUsageError(f"--page-url: invalid regular expression: {e}") from None
    started = dt.datetime.now()
    cfg = config_mod.build(None, {"evidence": {"screenshots": screenshots}},
                           module_defaults={"accessibility": ACCESSIBILITY.default_config()})
    acc = cfg["modules"].setdefault("accessibility", {})
    acc["keyboard"] = {**(acc.get("keyboard") or {}), "enabled": "keyboard" in checks}
    acc["reflow"] = {**(acc.get("reflow") or {}), "enabled": "reflow" in checks}
    env = os.environ
    secrets = {name: env[name] for name in (redact_env or []) if env.get(name)}

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        try:
            browser = (connect or pw.chromium.connect_over_cdp)(cdp, timeout=15000)
        except PWError as e:
            raise CheckUnreachable(f"cannot connect to {cdp}: {str(e).splitlines()[0]} (was the browser launched "
                                   "with --remote-debugging-port?)") from e
        page = pick_page([p for c in browser.contexts for p in c.pages], page_url)
        if page is None:
            open_urls = [p.url for c in browser.contexts for p in c.pages]
            raise CheckUnreachable(f"no open tab matches {page_url!r} (open: {len(open_urls)} tab(s))")
        run = RunFolder(output, now=started, name=f"check_{_slug(label, 40)}_{started:%Y-%m-%d_%H%M%S}")
        log = _logger(run)
        ctx = RunContext(cfg, run, log)
        url = page.url
        frames = max(0, len(page.frames) - 1)
        info = types.SimpleNamespace(url=url, final_url=url, iframes=frames)
        ACCESSIBILITY.on_page(page, info, ctx)
        if "keyboard" in checks:
            ctx.notes.append("keyboard: the check pressed Tab on the page, then put the focus and the scroll back "
                             "where the test left them")
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
    finally:
        pw.stop()           # disconnects; the test's browser, contexts and pages stay as they were
    if secrets:
        _redact(ctx, None, Redactor(secrets))
    findings = sorted(ctx.findings, key=sort_key)
    assign_ids(findings, {"accessibility": ACCESSIBILITY.id_prefix})
    result = {"tool": {"name": "qa-audit", "version": __version__, "command": "check"}, "label": label,
              "url": Redactor(secrets).text(url), "checks": checks, "started_at": started.isoformat(timespec="seconds"),
              "summary": summarize(findings, ctx.tally), "findings": [f.to_dict() for f in findings],
              "notes": ctx.notes, "disclaimer": ACCESSIBILITY.disclaimer, "manual_checks": ACCESSIBILITY.manual_checks,
              "run_folder": run.path}
    _write(result, run.path)
    return result


def exit_code(result: dict, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    from .findings import severity_rank
    limit = severity_rank(fail_on)
    return 1 if any(f["status"] == "problem" and severity_rank(f["severity"]) <= limit
                    for f in result["findings"]) else 0


def _write(result: dict, folder: str) -> None:
    from ..reports.md_report import esc
    with open(os.path.join(folder, "check.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    st = result["summary"]["by_status"]
    lines = [f"# QA audit check: {esc(result['label'])}", "", f"- **Page:** {esc(result['url'])}",
             f"- **Checks:** {', '.join(result['checks'])}", f"- **Date:** {esc(result['started_at'])}",
             f"- **Problems:** {st['problem']} · **Warnings:** {st['warning']} · **Needs human:** {st['needs-human']}",
             "", f"> {esc(result['disclaimer'])}", ""]
    for f in result["findings"]:
        lines += [f"## {esc(f['id'])} {esc(f['title'])} ({f['status']}, {f['severity']})", "",
                  f"- **Rule:** {esc(f['check'])}", f"- **Element:** {esc((f.get('element') or {}).get('selector'))}",
                  f"- **Observed:** {esc(f['actual'])}", f"- **References:** {esc(', '.join(f['references']))}", ""]
    if result["notes"]:
        lines += ["## Notes", ""] + [f"- {esc(n)}" for n in result["notes"]]
    with open(os.path.join(folder, "check.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
