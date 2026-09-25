"""Performance module (docs/qa-audit.md §17; README.md next to this file)."""

from __future__ import annotations

from ...core.findings import Evidence, Finding
from ...core.module import AuditModule
from . import checks

SOURCES = {
    "ttfb_ms": "https://web.dev/articles/ttfb",
    "fcp_ms": "https://web.dev/articles/fcp",
    "lcp_ms": "https://web.dev/articles/lcp",
    "cls": "https://web.dev/articles/cls",
}
FLAGGED = {"needs-improvement": "low", "poor": "medium", "over-budget": "low"}
ADVICE = {
    "ttfb_ms": "Reduce server response time: caching, a CDN, fewer redirects, faster back-end work.",
    "fcp_ms": "Reduce render-blocking CSS and JavaScript; inline critical CSS; speed up the server response.",
    "lcp_ms": "Load the largest element early: preload it, optimise its size and format, avoid lazy-loading it.",
    "cls": "Reserve space for images, ads and embeds (width/height or aspect-ratio); avoid inserting content above "
           "existing content.",
}


class Performance(AuditModule):
    name = "performance"
    title = "Performance"
    description = "Page timing (TTFB, FCP, LCP, CLS, load), requests and transfer sizes, with configurable thresholds."
    id_prefix = "PERF"
    order = 0          # measures before any module touches the page
    disclaimer = ("One lab measurement per page from the auditing machine; the web.dev thresholds refer to real "
                  "users at the 75th percentile. Confirm with field data before acting.")

    def validate_config(self, cfg: dict) -> list[str]:
        return [f"modules.performance.thresholds: {p}" for p in checks.validate_thresholds(cfg.get("thresholds"))]

    def on_page(self, page, info, ctx) -> None:
        cfg = ctx.module_config(self.name)
        thresholds = cfg.get("thresholds") or {}
        url = info.final_url or info.url
        st = ctx.state(self.name)
        if "first" not in st:
            st["first"] = url
        values = page.evaluate(checks.MEASURE_JS)
        values["console_errors"] = len(info.console_errors) + len(info.page_errors)
        if values.pop("unmeasured_cross_origin", 0):
            st.setdefault("partial", []).append(url)
        values.pop("lcp_supported", None)
        engine = ctx.session.engine if ctx.session else ""
        for name, (label, unit) in checks.METRICS.items():
            value = values.get(name)
            t = thresholds.get(name)
            result = checks.evaluate(value, t)
            if value is None and name in ("lcp_ms", "cls"):
                result = f"n/a in {engine}" if engine else "n/a"
            ctx.add_metric(self.name, url, label, value, unit, checks.describe(t), result,
                           "web.dev" if name in SOURCES and isinstance(t, dict) else "")
            if result in FLAGGED:
                st.setdefault("hits", {}).setdefault(name, []).append((url, value, result))
            elif result in ("good", "within-budget"):
                ctx.passed(self.name)

    def finalize(self, ctx) -> None:
        cfg = ctx.module_config(self.name)
        st = ctx.state(self.name)
        thresholds = cfg.get("thresholds") or {}
        if st.get("first"):
            ctx.notes.append(f"performance: {st['first']} was measured with a cold cache; later pages may reuse "
                             "cached resources")
        if st.get("partial"):
            ctx.notes.append(f"performance: transfer sizes are partial on {len(st['partial'])} page(s): cross-origin "
                             "resources without Timing-Allow-Origin report 0 bytes")
        max_listed = int(cfg.get("max_pages_listed", 10))
        for name, hits in st.get("hits", {}).items():
            label, unit = checks.METRICS[name]
            hits = sorted(hits, key=lambda h: -h[1])
            worst_url, worst, worst_result = hits[0]
            severity = "medium" if any(r == "poor" for _, _, r in hits) else FLAGGED[worst_result]
            t = thresholds.get(name)
            def fmt(v, unit=unit):
                return f"{v:.3f}" if unit == "" and isinstance(v, float) else f"{v:.0f} {unit}".strip()
            pages = ", ".join(f"{u} ({fmt(v)})" for u, v, _ in hits[:max_listed])
            more = f" (+{len(hits) - max_listed} more)" if len(hits) > max_listed else ""
            ctx.add_finding(Finding(
                module=self.name, check=name, title=f"{label} above its threshold", status="warning",
                severity=severity, confidence="medium", url=worst_url,
                description=f"Pages above the threshold ({checks.describe(t)}): {pages}{more}",
                expected=f"{label} {checks.describe(t)}",
                actual=f"worst: {fmt(worst)} ({worst_result}) on {len(hits)} page(s)",
                recommendation=ADVICE.get(name, f"Reduce {label} below the budget."),
                steps=[f"Open {worst_url} in a fresh browser profile",
                       "Measure with the browser's performance tools, several times",
                       "Compare with field data (e.g. the Chrome UX Report) before acting"],
                evidence=[Evidence(kind="metric", value=f"{label} = {fmt(v)} on {u}") for u, v, _ in hits[:5]],
                references=[SOURCES[name]] if name in SOURCES else []))


MODULE = Performance()
