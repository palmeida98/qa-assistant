"""Sitemap discovery (docs/qa-audit.md §5.1): read the site's sitemap(s) and return the page URLs they list.

Pure logic: the caller passes `fetch(url) -> (status, final_url, body bytes)`, so this module needs no browser.
Only sitemaps on the target origin are fetched; entity declarations are refused before parsing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 5 * 1024 * 1024       # per sitemap file
MAX_URLS = 5000                   # page URLs read in total
MAX_FILES = 10                    # sitemap files fetched (the start ones and one level of sitemapindex)
ENTITY = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class SitemapError(ValueError):
    """This file cannot be used as a sitemap (the message is the reason, recorded in the report)."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(body: bytes) -> tuple[str, list[str]]:
    """("urlset" | "sitemapindex", [loc, …]). SitemapError for anything else."""
    if ENTITY.search(body):
        raise SitemapError("refused: DOCTYPE or ENTITY declaration")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise SitemapError(f"not XML ({e})") from None
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"not a sitemap (root element <{kind}>)")
    child = "url" if kind == "urlset" else "sitemap"
    locs = []
    for item in root:
        if _local(item.tag) != child:
            continue
        for el in item:
            if _local(el.tag) == "loc" and (el.text or "").strip():
                locs.append(el.text.strip())
    return kind, locs


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def collect(fetch, sitemaps: list[str], target_origin: str) -> tuple[list[str], str, list[dict]]:
    """Read the sitemaps (one level of index). Returns (page URLs in order, status for the report, skipped)."""
    urls: list[str] = []
    skipped: list[dict] = []
    problems: list[str] = []
    read: list[str] = []
    queue = [(u, 0) for u in sitemaps]
    files = 0
    while queue:
        url, level = queue.pop(0)
        if _origin(url) != target_origin.lower():
            skipped.append({"url": url, "reason": "sitemap outside the target origin"})
            continue
        if urlsplit(url).path.lower().endswith(".gz"):
            skipped.append({"url": url, "reason": "compressed sitemap (.gz) not supported"})
            continue
        if files >= MAX_FILES:
            skipped.append({"url": url, "reason": f"more than {MAX_FILES} sitemap files"})
            continue
        files += 1
        path = urlsplit(url).path or "/"
        try:
            status, final, body = fetch(url)
        except Exception as e:  # noqa: BLE001 - an unreachable sitemap never stops the audit
            problems.append(f"{path}: unavailable ({type(e).__name__})")
            continue
        if status != 200:
            problems.append(f"{path}: none (HTTP {status})")
            continue
        if _origin(final) != target_origin.lower():
            problems.append(f"{path}: redirected outside the target origin")
            continue
        if len(body) > MAX_BYTES:
            problems.append(f"{path}: larger than {MAX_BYTES // (1024 * 1024)} MB")
            continue
        try:
            kind, locs = parse(body)
        except SitemapError as e:
            problems.append(f"{path}: {e}")
            continue
        if kind == "sitemapindex":
            if level == 0:
                queue += [(urljoin(url, u), 1) for u in locs]
            else:
                problems.append(f"{path}: nested sitemap index ignored (one level only)")
            read.append(path)
            continue
        room = MAX_URLS - len(urls)
        if len(locs) > room:
            problems.append(f"{path}: {len(locs) - room} URLs beyond the limit of {MAX_URLS} not read")
        urls += [urljoin(url, u) for u in locs[:room]]
        read.append(path)
    if urls:
        status = f"read: {len(urls)} URLs from {', '.join(read)}"
    elif read:
        status = f"read: no page URLs in {', '.join(read)}"
    else:
        return urls, (problems[0] if problems else "none"), skipped
    if problems:
        status += "; " + "; ".join(problems)
    return urls, status, skipped
