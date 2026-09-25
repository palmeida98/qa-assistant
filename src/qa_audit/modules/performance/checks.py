"""Measurement in the page and threshold evaluation (docs/qa-audit.md §17).

Only standard browser APIs; an entry type the engine does not implement gives None ("n/a"), never an estimate.
"""

from __future__ import annotations

MEASURE_JS = """async () => {
  const supported = (PerformanceObserver && PerformanceObserver.supportedEntryTypes) || [];
  const buffered = type => !supported.includes(type) ? Promise.resolve(null) : new Promise(resolve => {
    const entries = [];
    const po = new PerformanceObserver(list => entries.push(...list.getEntries()));
    po.observe({type, buffered: true});
    setTimeout(() => { po.disconnect(); resolve(entries); }, 100);
  });
  const nav = performance.getEntriesByType('navigation')[0];
  const paint = performance.getEntriesByType('paint');
  const fcpEntry = paint.find(p => p.name === 'first-contentful-paint');
  const lcpEntries = await buffered('largest-contentful-paint');
  const shifts = await buffered('layout-shift');
  // CLS as defined by web.dev: largest session window (gap < 1 s, window < 5 s) of shifts without recent input
  let cls = null;
  if (shifts !== null) {
    cls = 0; let win = 0, first = 0, last = 0;
    for (const s of shifts) {
      if (s.hadRecentInput) continue;
      if (win && s.startTime - last < 1000 && s.startTime - first < 5000) { win += s.value; }
      else { win = s.value; first = s.startTime; }
      last = s.startTime; cls = Math.max(cls, win);
    }
  }
  const res = performance.getEntriesByType('resource');
  const kb = list => list.reduce((a, r) => a + (r.transferSize || r.encodedBodySize || 0), 0) / 1024;
  const zero = res.filter(r => !r.transferSize && !r.encodedBodySize && !r.name.startsWith(location.origin)).length;
  const isJs = r => r.initiatorType === 'script' || /\\.m?js(\\?|$)/.test(r.name);
  const isImg = r => r.initiatorType === 'img' || /\\.(png|jpe?g|gif|webp|avif|svg)(\\?|$)/i.test(r.name);
  const isCss = r => (r.initiatorType === 'link' && /\\.css(\\?|$)/i.test(r.name)) || r.initiatorType === 'css';
  const docKb = nav ? (nav.transferSize || nav.encodedBodySize || 0) / 1024 : 0;
  const d = (a, b) => (nav && nav[a] > 0 && nav[b] > 0) ? nav[b] - nav[a] : null;
  return {
    ttfb_ms: nav ? nav.responseStart : null,
    dns_ms: d('domainLookupStart', 'domainLookupEnd'),
    connect_ms: d('connectStart', 'connectEnd'),
    tls_ms: nav && nav.secureConnectionStart > 0 ? nav.connectEnd - nav.secureConnectionStart : null,
    dom_content_loaded_ms: nav ? nav.domContentLoadedEventEnd : null,
    load_ms: nav ? nav.loadEventEnd : null,
    fcp_ms: fcpEntry ? fcpEntry.startTime : null,
    lcp_ms: lcpEntries && lcpEntries.length ? lcpEntries[lcpEntries.length - 1].startTime : null,
    lcp_supported: lcpEntries !== null,
    cls: cls,
    requests: res.length + 1,
    transfer_kb: docKb + kb(res),
    js_kb: kb(res.filter(isJs)),
    image_kb: kb(res.filter(isImg)),
    css_kb: kb(res.filter(isCss)),
    unmeasured_cross_origin: zero,
  };
}"""

# name -> (label, unit)
METRICS = {
    "ttfb_ms": ("TTFB", "ms"), "fcp_ms": ("FCP", "ms"), "lcp_ms": ("LCP", "ms"), "cls": ("CLS", ""),
    "dns_ms": ("DNS", "ms"), "connect_ms": ("connection", "ms"), "tls_ms": ("TLS", "ms"),
    "dom_content_loaded_ms": ("DOMContentLoaded", "ms"), "load_ms": ("load", "ms"),
    "requests": ("requests", ""), "transfer_kb": ("transferred", "KB"), "js_kb": ("JavaScript", "KB"),
    "image_kb": ("images", "KB"), "css_kb": ("CSS", "KB"), "console_errors": ("console errors", ""),
}


def describe(threshold) -> str:
    if threshold is None:
        return ""
    if isinstance(threshold, dict):
        return f"good <= {threshold['good']}, poor > {threshold['poor']}"
    return f"budget <= {threshold}"


def evaluate(value, threshold) -> str:
    """'measured' (no threshold), 'n/a', 'good', 'needs-improvement', 'poor', 'within-budget' or 'over-budget'."""
    if value is None:
        return "n/a"
    if threshold is None:
        return "measured"
    if isinstance(threshold, dict):
        if value <= threshold["good"]:
            return "good"
        return "needs-improvement" if value <= threshold["poor"] else "poor"
    return "within-budget" if value <= threshold else "over-budget"


def validate_thresholds(thresholds: dict) -> list[str]:
    problems = []
    for name, t in (thresholds or {}).items():
        if name not in METRICS:
            problems.append(f"unknown metric {name!r} (known: {', '.join(METRICS)})")
        elif isinstance(t, dict):
            if not {"good", "poor"} <= set(t) or not all(isinstance(t[k], (int, float)) for k in ("good", "poor")) \
                    or t["good"] > t["poor"]:
                problems.append(f"{name}: expected {{good: number, poor: number}} with good <= poor")
        elif t is not None and not isinstance(t, (int, float)):
            problems.append(f"{name}: expected a number, {{good, poor}} or null")
    return problems
