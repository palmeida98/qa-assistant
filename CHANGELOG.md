# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions of the `qa-audit` engine
(`pyproject.toml`). The workspace layer is tracked in `docs/plano-desenvolvimento.md`.

## [Unreleased]

### Added
- `qa-audit check` (plan 8.15): audits a page another tool has open, in its current state, over the Chrome DevTools
  Protocol (loopback only): the accessibility checks (axe by default; keyboard and reflow opt-in) without
  navigating, reloading, clicking or typing. Playwright helper `integrations/playwright/qa-audit.ts`
  (`auditarEstado`) for the workspace's N2 specs.

## [0.2.0] - 2026-09-26

### Added
- Several engines in one run (plan 8.14): `--browser chromium,firefox,webkit` or `--browser all` runs one audit per
  engine (a sub-folder each) and writes `compatibility.json/.md/.html`: findings, pages and HTTP statuses that
  differ between engines, as needs-human findings. An engine that cannot start does not stop the others; `manual`
  login is refused with several engines (use `storage-state`).
- Sitemap discovery (plan 8.13): `--sitemap` / `crawl.sitemap` (off by default) also queues the pages listed in
  the site's sitemap (`robots.txt` `Sitemap:` lines, else `/sitemap.xml`; one level of sitemap index), same origin
  only and through every discovery rule; entity declarations refused, size and count limits; status in the report.
- Saved login sessions (plan 8.12): `auth.method: storage-state` reuses a session saved once with
  `qa-audit login <url> --config FILE` (`login_method` `manual` or `form`); nothing is submitted during the audit,
  an expired session is exit 3 with the command to renew it. The state file is refused inside the output folder or
  a Git working tree, its cookie values are redacted in every output, and `qa-audit sessions` lists or deletes the
  saved sessions.
- `crawl.audit_error_pages` (off by default): the listed pages (start URL and `crawl.pages`) that answer 4xx are
  audited as content, for single-page applications whose server answers 404 to a deep link; each one is noted in the
  report. Pages found by following links and 5xx answers are never audited that way.
- Login before the crawl (plan 8.10): `auth.method` `form` (credentials from environment variables) and `manual`
  (the person logs in in a visible browser: Active Directory, second factors); one attempt, success check, session
  expiry stops the crawl, credentials redacted in every output, traces refused with `auth`. Explicit page list
  `crawl.pages` and `crawl.follow_links`.
- Install scripts `scripts/install.sh` and `scripts/install.ps1` (virtual environment, package with the visual
  extra, browsers, `doctor`); user guide `docs/qa-audit-guide.md`; fictional example `examples/qa-audit/` (kept
  valid by a test); CI job `install`: clean checkout, install script, audit of the local site (plan 8.9).
- Visual module (plan 8.8): full-page screenshots per viewport compared with baselines (baseline, current and diff
  images; needs-human), horizontal overflow per viewport, missing viewport meta tag. Baselines outside any
  repository (`~/.qa-audit/baselines`, `--baseline-dir`), `--update-baseline`, and `qa-audit baselines` to list or
  delete them. A module asked for by name that cannot run here is a usage error.
- Functional module (plan 8.7): broken links (visited pages, plus HEAD checks of unvisited same-origin links; never
  excluded or robots-disallowed ones), broken resources, uncaught exceptions, console errors, redirect chains,
  e-mail fields without validation; `--allow-submit` submits only the forms listed in the configuration.
- Performance module (plan 8.6): TTFB, FCP, LCP and CLS with the web.dev thresholds; DNS, connection, TLS,
  DOMContentLoaded, load, requests and transfer sizes measured only unless a budget is configured. Metrics table
  (observed · threshold · result) in every report and `metrics.csv`; `n/a` where an engine cannot measure.
- Security module (passive, plan 8.5): HTTPS, HTTP→HTTPS redirect, TLS protocol and certificate expiry, HSTS, CSP,
  clickjacking, nosniff, version disclosure, cookie flags (names and flags only), mixed content, forms posting to
  HTTP. Reported once per check with the affected pages.
- `--ignore-https-errors` for sites with certificates the browser rejects (trust then not judged).
- HTTPS fixture (self-signed, two-day certificate) and security integration tests.

### Changed
- Accessibility findings' `references` carry the WCAG version and level of each axe tag (`WCAG 2.0 AA`,
  `WCAG 2.2 AA`), besides the criteria.
- The workspace's `scripts/auditar_a11y.py` (`/auditar-acessibilidade`) runs on this engine; the separate Node tool
  `tools/a11y/` is gone.
- Pages answering 4xx/5xx are no longer audited as content (they are broken links).
- Module `order` (readers before page-changing modules) and `validate_config` (bad module configuration is exit 2).

### Fixed
- A page reached again through a redirect was audited twice by every module; it is now recorded as the same page.
- `doctor` tests each browser's cryptography (a Firefox that cannot load HTTPS is no longer reported as ready).

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
