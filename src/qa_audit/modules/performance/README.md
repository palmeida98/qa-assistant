# Performance module

`qa-audit <url> --type performance` · specification: `docs/qa-audit.md` §17

## Goal

Measure how each page loads, show every value next to its threshold, and flag only what exceeds a cited or
explicitly configured threshold.

## Prerequisites

A browser engine (`qa-audit doctor`). No extra requests: the standard browser performance APIs are read on each
page the audit visits.

## What is measured

| Metric | API | Default threshold |
|---|---|---|
| TTFB | Navigation Timing (`responseStart`) | 800 / 1800 ms (web.dev) |
| FCP | Paint Timing | 1800 / 3000 ms (web.dev) |
| LCP | Largest Contentful Paint (Chromium, Firefox, WebKit 26) | 2500 / 4000 ms (web.dev) |
| CLS | Layout Instability, session windows (Chromium) | 0.1 / 0.25 (web.dev) |
| DNS, connection, TLS, DOMContentLoaded, load | Navigation Timing | measured only |
| requests, transferred / JavaScript / image / CSS KB | Resource Timing | measured only |
| console errors | from the discovery | measured only |

`good / poor`: at or under good is `good`; up to poor is `needs-improvement` (warning · low); above poor is `poor`
(warning · medium). No default is invented for the metrics without a cited standard.

## Configuration (`config.yaml`, override under `modules.performance.thresholds`)

```yaml
modules:
  performance:
    thresholds:
      lcp_ms: {good: 2000, poor: 3500}   # stricter than web.dev
      transfer_kb: 1500                  # a budget: above it -> warning · low (over-budget)
      js_kb: null                        # measured only
```

An invalid threshold (unknown metric, `good > poor`, not a number) is a usage error (exit 2) before the audit.

## Output

- **Metrics table** in every report (`metrics.csv` next to `results.csv`): page · metric · observed · threshold ·
  result. A metric the engine cannot measure is `n/a in <engine>`, never estimated.
- **Findings**: one per metric above its threshold, listing the pages and values, worst first; always `warning`
  with confidence `medium`.

## Limits

- One lab measurement per page, from the auditing machine and network; the web.dev thresholds describe real users
  at the 75th percentile. Treat findings as leads to confirm with field data (e.g. the Chrome UX Report).
- The first page is measured with a cold cache; later pages may reuse cached resources (noted in the report).
- CLS only includes shifts before the measurement (after `load` and network idle). This module runs before the
  modules that interact with the page, so their key presses and viewport changes never count.
- Cross-origin resources without `Timing-Allow-Origin` report 0 bytes; the report says when totals are partial.
