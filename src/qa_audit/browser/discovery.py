"""Same-origin discovery (docs/qa-audit.md §5).

Safety (risk 3): pages are only reached by GET navigation to <a href> targets on the start origin. The crawler
never clicks, types or submits. Trap-looking URLs (logout, delete, …) and non-HTML resources are never visited.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from ..core.privacy import Redactor
from .session import origin

BUILTIN_EXCLUDE = (r"log[-_]?out|sign[-_]?out|log[-_]?off|delete|remove|destroy|unsubscribe|deactivate|cancel")
NON_HTML = re.compile(
    r"\.(pdf|zip|gz|tgz|rar|7z|tar|exe|msi|dmg|apk|jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp3|mp4|m4a|avi|mov|"
    r"wmv|webm|ogg|wav|flac|docx?|xlsx?|pptx?|odt|ods|csv|xml|json|txt|rss|atom|woff2?|ttf|otf|css|js)$",
    re.IGNORECASE)
ROBOTS_AGENT = "qa-audit"

COLLECT_JS = """() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = el => {
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    return el.getAttribute('aria-label') || '';
  };
  return {
    title: document.title || '',
    lang: document.documentElement.getAttribute('lang') || '',
    links: [...document.querySelectorAll('a[href]')].map(a => a.href),
    forms: [...document.forms].map(f => ({
      method: (f.getAttribute('method') || 'get').toLowerCase(),
      action: f.action,
      fields: [...f.elements].filter(e => e.name || e.id).map(e => ({
        tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || e.id,
        required: !!e.required, label: labelOf(e), pattern: e.getAttribute('pattern') || '',
        autocomplete: e.getAttribute('autocomplete') || '' })) })),
    interactive: [...document.querySelectorAll(
      'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [tabindex]')]
      .filter(vis).length,
    iframes: document.querySelectorAll('iframe, frame').length,
    metaCsp: [...document.querySelectorAll('meta[http-equiv]')]
      .filter(m => m.httpEquiv.toLowerCase() === 'content-security-policy').map(m => m.content),
    passwordFields: document.querySelectorAll('input[type=password]').length,
    insecureRefs: location.protocol !== 'https:' ? [] : [
      ...[...document.querySelectorAll('script[src], iframe[src], frame[src], embed[src], object[data], ' +
                                       'link[rel~=stylesheet][href]')]
        .map(e => ({tag: e.tagName.toLowerCase(), url: e.src || e.href || e.data || '', active: true})),
      ...[...document.querySelectorAll('img[src], audio[src], video[src], source[src], input[type=image][src]')]
        .map(e => ({tag: e.tagName.toLowerCase(), url: e.src || '', active: false})),
    ].filter(r => r.url.startsWith('http:')).slice(0, 50),
  };
}"""


@dataclass
class PageInfo:
    url: str
    depth: int
    final_url: str = ""
    status: int | None = None
    redirects: list[str] = field(default_factory=list)
    content_type: str = ""
    title: str = ""
    lang: str = ""
    headers: dict = field(default_factory=dict)          # redacted (core/privacy.py)
    links: list[str] = field(default_factory=list)       # same-origin, normalized
    external_links: list[str] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)
    interactive_count: int = 0
    iframes: int = 0
    meta_csp: list[str] = field(default_factory=list)
    password_fields: int = 0
    insecure_refs: list[dict] = field(default_factory=list)   # http:// resources referenced by an https:// page
    tls: dict = field(default_factory=dict)                   # protocol, issuer, subjectName, validFrom, validTo
    console_errors: list[str] = field(default_factory=list)   # console.error messages
    page_errors: list[str] = field(default_factory=list)      # uncaught exceptions
    failed_requests: list[dict] = field(default_factory=list)
    timing: dict = field(default_factory=dict)
    error: str = ""
    audited: bool = False     # modules ran on it

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CrawlResult:
    pages: list[PageInfo] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)    # {"url", "reason"}
    not_visited: int = 0                                   # left in the queue at max_pages
    robots: str = "not checked"


class TargetUnreachable(RuntimeError):
    """The start URL could not be loaded (exit code 3)."""


def normalize(url: str) -> str:
    p = urlsplit(url)
    scheme, host = p.scheme.lower(), (p.hostname or "").lower()
    port = p.port
    netloc = host + (f":{port}" if port and port != {"http": 80, "https": 443}.get(scheme) else "")
    return urlunsplit((scheme, netloc, p.path or "/", p.query, ""))


def exclusion_reason(url: str, patterns: list[re.Pattern]) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        return f"scheme {p.scheme}"
    target = p.path + ("?" + p.query if p.query else "")
    for pat in patterns:
        m = pat.search(target)
        if m:
            return f"excluded: matches '{m.group(0)}'"
    if NON_HTML.search(p.path):
        return "not an HTML page"
    return ""


class Crawler:
    def __init__(self, session, cfg: dict, start_url: str, on_page=None, log=None, redactor: Redactor | None = None):
        self.s, self.cfg = session, cfg
        self.start = normalize(start_url)
        self.on_page = on_page or (lambda page, info: None)
        self.log = log or (lambda msg: None)
        self.redactor = redactor or Redactor()
        c = cfg["crawl"]
        self.patterns = [re.compile(BUILTIN_EXCLUDE, re.IGNORECASE)] + \
                        [re.compile(p, re.IGNORECASE) for p in c.get("exclude") or []]
        self.robots: RobotFileParser | None = None

    # --- robots.txt ------------------------------------------------------------------------------------------
    def _load_robots(self, result: CrawlResult) -> None:
        if not self.cfg["crawl"].get("respect_robots", True):
            result.robots = "ignored (configuration)"
            return
        url = origin(self.s.target) + "/robots.txt"
        try:
            resp = self.s.request().get(url, timeout=self.cfg["browser"]["timeout_ms"], max_redirects=3)
        except Exception as e:  # noqa: BLE001 - a missing robots.txt never stops the audit
            result.robots = f"unavailable ({type(e).__name__})"
            return
        if resp.status != 200:
            result.robots = f"none (HTTP {resp.status})"
            return
        rp = RobotFileParser()
        rp.parse(resp.text().splitlines())
        self.robots = rp
        result.robots = "respected"

    # --- crawl -----------------------------------------------------------------------------------------------
    def run(self) -> CrawlResult:
        c = self.cfg["crawl"]
        result = CrawlResult()
        queue: deque[tuple[str, int]] = deque([(self.start, 0)])
        seen = {self.start}
        first = True
        while queue and len(result.pages) < c["max_pages"]:
            url, depth = queue.popleft()
            if not first and c["delay_ms"]:
                time.sleep(c["delay_ms"] / 1000)
            info = self._visit(url, depth, first)
            if first:
                first = False
                self._load_robots(result)
            result.pages.append(info)
            if not info.audited:
                continue
            for link in info.links:
                if link in seen:
                    continue
                seen.add(link)
                reason = exclusion_reason(link, self.patterns)
                if not reason and self.robots is not None and not self.robots.can_fetch(ROBOTS_AGENT, link):
                    reason = "disallowed by robots.txt"
                if not reason and depth + 1 > c["max_depth"]:
                    reason = "beyond max depth"
                if reason:
                    result.skipped.append({"url": link, "reason": reason})
                else:
                    queue.append((link, depth + 1))
        result.not_visited = len(queue)
        return result

    def _visit(self, url: str, depth: int, is_start: bool) -> PageInfo:
        from playwright.sync_api import Error as PWError

        info = PageInfo(url=url, depth=depth)
        self.log(f"visit {url}")
        page = self.s.new_page()
        page.on("console", lambda m: info.console_errors.append(m.text[:500]) if m.type == "error" else None)
        page.on("pageerror", lambda e: info.page_errors.append(str(e)[:500]))
        page.on("requestfailed", lambda r: info.failed_requests.append(
            {"url": r.url, "error": (r.failure or "")[:200], "type": r.resource_type}))

        def on_response(r):
            if r.status >= 400 and r.request.resource_type != "document":
                info.failed_requests.append({"url": r.url, "status": r.status, "type": r.request.resource_type})
        page.on("response", on_response)
        try:
            try:
                resp = page.goto(url, wait_until="load")
            except PWError as e:
                info.error = str(e).splitlines()[0][:300]
                if is_start:
                    hint = ""
                    if "NS_ERROR_FAILURE" in info.error and url.startswith("https:"):
                        hint = (" (this Firefox cannot open HTTPS here, typically Windows application control "
                                "blocking its cryptography; run 'qa-audit doctor', use --browser chromium)")
                    raise TargetUnreachable(f"{url}: {info.error}{hint}") from e
                return info
            if resp is None:
                info.error = "no response"
                if is_start:
                    raise TargetUnreachable(f"{url}: no response")
                return info
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PWError:
                pass    # long-polling pages never go idle; "load" already happened
            info.final_url = normalize(page.url)
            info.status = resp.status
            chain, r = [], resp.request.redirected_from
            while r is not None:
                chain.insert(0, r.url)
                r = r.redirected_from
            info.redirects = chain
            headers = resp.all_headers()
            # a response served from the cache can come without Content-Type (seen in CI on Linux): ask the page
            info.content_type = headers.get("content-type", "") or page.evaluate("() => document.contentType")
            info.headers = self.redactor.headers(headers)
            if is_start:
                if resp.status >= 400:
                    raise TargetUnreachable(f"{url}: HTTP {resp.status}")
                if origin(info.final_url) != origin(self.s.target):
                    self.log(f"start page redirected to {info.final_url}; auditing that origin")
                self.s.retarget(info.final_url)
            elif origin(info.final_url) != origin(self.s.target):
                info.error = f"redirected outside the target origin to {info.final_url}"
                return info
            elif resp.status >= 400:
                # reported as a broken link (functional); an error page is not audited as content
                info.error = f"HTTP {resp.status}"
                return info
            if "html" not in info.content_type.lower():
                if is_start:
                    raise TargetUnreachable(f"{url}: not an HTML page ({info.content_type or 'no content type'})")
                info.error = f"not an HTML page ({info.content_type or 'no content type'})"
                return info
            data = page.evaluate(COLLECT_JS)
            info.title, info.lang = data["title"], data["lang"]
            info.forms, info.interactive_count, info.iframes = data["forms"], data["interactive"], data["iframes"]
            info.meta_csp, info.password_fields = data["metaCsp"], data["passwordFields"]
            info.insecure_refs = data["insecureRefs"]
            try:
                info.tls = resp.security_details() or {}
            except PWError:
                info.tls = {}
            own = origin(self.s.target)
            links, external = [], []
            for href in data["links"]:
                if not href.lower().startswith(("http://", "https://")):
                    continue
                n = normalize(href)
                (links if origin(n) == own else external).append(n)
            info.links = list(dict.fromkeys(links))
            info.external_links = list(dict.fromkeys(external))
            info.timing = page.evaluate(
                "() => { const n = performance.getEntriesByType('navigation')[0]; return n ? n.toJSON() : {}; }")
            info.audited = True
            self.on_page(page, info)
            return info
        finally:
            page.close()
