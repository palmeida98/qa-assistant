"""Compatibility: compare the runs of one audit in several engines (docs/qa-audit.md §21).

Pure logic over finished runs (objects with `pages` and `findings`, as runner.RunResult); no browser.
A difference is a needs-human finding: one engine can be right and the others wrong.
"""

from __future__ import annotations

from .findings import Evidence, Finding

MODULE = "compatibility"
CHECK = "engine-difference"
# not compared (§21 point 4): timings differ by engine and by run; visual baselines are already per engine
IGNORED_MODULES = {"performance", MODULE}
IGNORED_CHECKS = {("visual", "visual-diff"), ("visual", "visual-size-changed")}


def _page_url(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/")


def finding_key(f) -> tuple[str, str, str, str]:
    return (f.module, f.check, _page_url(f.url), (f.element or {}).get("selector", ""))


def comparable(f) -> bool:
    return f.module not in IGNORED_MODULES and (f.module, f.check) not in IGNORED_CHECKS


def split(engines: list[str], present: set[str]) -> str:
    absent = [e for e in engines if e not in present]
    return f"present in: {', '.join(e for e in engines if e in present)}; absent in: {', '.join(absent)}"


def compare(runs: dict[str, object]) -> list[Finding]:
    """runs: {engine: RunResult} of the engines that completed, in the order asked for. [] with fewer than two."""
    engines = list(runs)
    if len(engines) < 2:
        return []
    out: list[Finding] = []

    # pages: visited, audited and HTTP status per engine
    pages: dict[str, dict[str, object]] = {}
    for e in engines:
        for p in runs[e].pages:
            pages.setdefault(_page_url(p.url), {})[e] = p
    for url, by in pages.items():
        if len(by) != len(engines):
            out.append(difference(url, "Page visited in some engines only", split(engines, set(by)),
                                   "Discovery found this page in some engines only (links built by script, "
                                   "engine-specific redirects or a crawl limit reached earlier)."))
            continue
        audited = {e for e, p in by.items() if p.audited}
        if 0 < len(audited) < len(engines):
            reasons = "; ".join(f"{e}: {by[e].error or 'audited'}" for e in engines)
            out.append(difference(url, "Page audited in some engines only", split(engines, audited),
                                   f"The page could not be audited everywhere ({reasons})."))
        statuses = {e: by[e].status for e in engines}
        if len(set(statuses.values())) > 1:
            out.append(difference(url, "Different HTTP status per engine",
                                   "; ".join(f"{e}: {s}" for e, s in statuses.items()),
                                   "The server answered this page differently depending on the browser."))

    # findings: the same (module, check, page, element) in some engines only, on pages every engine audited
    everywhere = {url for url, by in pages.items()
                  if len(by) == len(engines) and all(p.audited for p in by.values())}
    seen: dict[tuple, dict[str, object]] = {}
    for e in engines:
        for f in runs[e].findings:
            if comparable(f) and _page_url(f.url) in everywhere:
                seen.setdefault(finding_key(f), {}).setdefault(e, f)
    for key, by in seen.items():
        if len(by) == len(engines):
            continue
        first = next(iter(by.values()))
        module, check, url, selector = key
        out.append(difference(
            url, f"{first.title} ({module}/{check}) in some engines only", split(engines, set(by)),
            f"The engine reports {module}/{check}" + (f" on {selector}" if selector else "")
            + " in some engines only. Check the page in each engine: the difference can be a real engine-specific "
              "defect, or an engine that measures differently.",
            selector=selector, snippet=(first.element or {}).get("snippet", "")))
    return out


def difference(url: str, title: str, split: str, description: str, selector: str = "",
                snippet: str = "") -> Finding:
    return Finding(module=MODULE, check=CHECK, title=title, status="needs-human", severity="info",
                   confidence="medium", url=url, description=description,
                   expected="The same result in every engine.", actual=split,
                   element={"selector": selector, "snippet": snippet} if selector else {},
                   evidence=[Evidence(kind="text", value=split, label="engines")],
                   recommendation="Open the page in each engine and decide which result is right.")
