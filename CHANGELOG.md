# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions of the `qa-audit` engine
(`pyproject.toml`). The workspace layer is tracked in `docs/plano-desenvolvimento.md`.

## [Unreleased]

### Added
- Performance module (plan 8.6): TTFB, FCP, LCP and CLS with the web.dev thresholds; DNS, connection, TLS,
  DOMContentLoaded, load, requests and transfer sizes measured only unless a budget is configured. Metrics table
  (observed · threshold · result) in every report and `metrics.csv`; `n/a` where an engine cannot measure.
- Module `order` (readers before page-changing modules) and `validate_config` (bad module configuration is exit 2).
- Security module (passive, plan 8.5): HTTPS, HTTP→HTTPS redirect, TLS protocol and certificate expiry, HSTS, CSP,
  clickjacking, nosniff, version disclosure, cookie flags (names and flags only), mixed content, forms posting to
  HTTP. Reported once per check with the affected pages.
- `--ignore-https-errors` for sites with certificates the browser rejects (trust then not judged).
- `doctor` tests each browser's cryptography (a Firefox that cannot load HTTPS is no longer reported as ready).
- HTTPS fixture (self-signed, two-day certificate) and security integration tests.

## [0.1.0] - 2026-09-25

### Added
- `qa-audit` engine alongside the workspace layer (plan phase 8, `docs/qa-audit.md`).
- CLI: `qa-audit <url>`, `qa-audit doctor`, `qa-audit version`; exit codes 0–4.
- Configuration with precedence and refusal of credentials in files.
- Browser layer for Chromium, Firefox and WebKit; viewport, device, video and trace.
- Same-origin, GET-only discovery with trap-link exclusions, `robots.txt`, delay and identifiable user agent.
- Findings with the evidence rule, conservative severities and statuses (problem, warning, needs-human).
- Reports: terminal, JSON, CSV (formula-safe), Markdown and self-contained HTML (CSP, escaped content).
- Accessibility module: bundled axe-core 4.13.0 (verified), keyboard walk, reflow at 320 px, manual checklist.
- Local fixture site, recording proxy, unit and integration tests; CI job with the three engines.
