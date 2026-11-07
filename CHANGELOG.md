# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions of the `qa-audit` engine
(`pyproject.toml`). The workspace layer is tracked in [`docs/development-plan.md`](docs/development-plan.md).

## [Unreleased]

### Added (iOS pilot of 2026-11-07, `docs/pilot-improvements-2026-11-07.md`)
- Excel: `python -m adapters.excel.apply <ws> <batch> --dry-run [--json]` runs the apply's own pipeline on a temporary
  copy and lists the target workbook and platform, every cell before -> after (instant-only values marked
  `<at apply time>`), the counts, new defects, the control row, the problems and "already applied"; nothing is
  written. Its preview token makes `apply --expect <token>` refuse a stale preview. qa-ui shows the preview and applies
  only with its token; `/checkpoint` runs the dry run first.
- Run log: `run_log.py mode <batch> "<kind>: <why>"` and `add --mode` record how a batch runs (conversational, flows,
  assisted, manual) before its first result; another mode later is refused, an old run log is never given one; the
  audit and qa-ui show it.
- iOS: `device.py --platform ios doctor`, and `check` when WebDriverAgent does not answer, give a read-only diagnosis
  (devicectl, usbmux, port forward, WDA, profiles, signing identities and Xcode accounts as counts, the WDA log), each
  finding confirmed / probable / unknown, the main block and the next action first; never "no cable" when Xcode sees
  the device.
- iOS typing: `type`/`send` choose the one enabled text field (or `--field`, or the focused one), confirm the focus,
  refuse several or none with the list, and say the outcome of a stop (nothing typed, partly typed, typed not sent,
  sent, uncertain); nothing is repeated automatically.
- Streaming evidence: the sampler says what its readings could show (partial text observed; answer readable, no
  intermediate state captured; answer text not readable; sampling failed or insufficient), excluding what was on the
  screen before the send; `send --burst N` and `shot-burst` take limited timed screenshots with a manifest
  (`<name>_burst.json`).
- Device validation (07.11.2026): `mobile.field_label` for an input not exposed as a text field; the send confirmed by
  the message on the screen when the field never shows its text; the iOS sampler reads content only (buttons and
  scroll bars after a send are not the answer); the dry run marks instant cells by a second run at another instant;
  `doctor` counts Xcode accounts from the account list, opens and closes one session to prove UI automation (an older
  refusal in the WDA log is history once a session opens) and recognises "Not authorized for performing UI testing";
  qa-ui shows the count changes and no empty output box; `check_denylist.py` says NOT CONFIGURED without a list
  (`--require`: exit 3).
- Backlog of the device validation: the burst captures on its own clock (a thread); `send --read-hidden` reads an
  answer exposed only as invisible elements, apart; `send` prints `timing:` and reads the screen three times before
  the tap; `doctor --no-session`; the qa-ui preview grouped by sheet.

### Fixed (audit of 2026-11-06, `docs/audit-2026-11-06.md`)
- Closing of the audit (07.11.2026): the workbook rule is `route_workbook` and stops (exit 4, nothing written) on a
  name without a recognisable platform, a name matching two workbooks or two platforms, a name that contradicts
  `run.platform`, a missing or unreadable workbook, or a batch already applied to another workbook; `run_log` treats a
  batch applied to any declared workbook as immutable. `"applies": "<batch>"` records an earlier execution in the
  workbook without a time of its own: the report keeps the execution current and shows the applied status apart; a
  result without build is shown as `unknown`. qa-ui: the Overview names the workbooks, a run log says its build
  (`unknown`), platform and workbook, an unreadable workbook is a problem and not a dropped connection, focus returns
  to the heading after an action. Lint (ruff 0.16.8) clean again.
- Workbooks: one rule picks a batch's workbook (`excel.workbooks`, `workbook_for`) for the apply, `run_log`, the
  reports, the CI and `qa_ui`; an iOS batch no longer lands in the default workbook, and an applied iOS batch is
  immutable. The apply holds the workspace lock and saves through a verified temporary file (two applies at once
  corrupted the workbook). New defects take the run's build; an unmapped `new_defect` key is refused.
- Writes: `common.atomic_write` (no link followed at `<file>.tmp`), `history/` refused in any case, the lock never
  empty and reentrant through links, the compaction's copies never overwritten nor written through a link, sync never
  writes through or removes a link and retires only what its record installed.
- Results: `qa_report.py` counts an unreadable application as not applied, never lets a file's modification time
  decide the current result (`revision_of` does; an undecided tie is listed), marks each current result as applied or
  not, groups builds by version and code, flags results without `ts`, and is short by default (`--detail`).
  `run_log.py` refuses a second result of a case without `--replace` (and prints the one replaced) and a build or
  environment filled in after results without one; `Question` is stored in one spelling.
- Tickets: the analysis validates types (a non-list basis broke the memory) and comment sources (structured, both
  ways); `content_unavailable` keeps content not opened as not read; children not expanded are listed as
  `not-expanded`; the copyable prompt marks tracker text; the context block says when a ticket changed after its
  analysis, labels suggestions and never suggests a Removed ticket as a verification candidate.
- `/test-pbi`: `pbi_state.py check` says what it could not compare (exit 3) and `--reopen` returns the steps to review;
  `published` needs `--confirmed-by` and refuses a reused key; `blocked` needs a note; links of other hosts refused.
- Preflight: a valid configuration and being ready to run are separate (`Ready:` per platform, with the capability
  blocked and the action); Maestro is required only where it is used; `mobile.ios` adds `xcrun` and WebDriverAgent;
  an unknown or retired skill name lists the skills; `where` says when the installed skills include uncommitted files.
- The iPhone backend (`device_ios.py`, `device.py --platform ios`) is in the main checkout, with `check` giving the
  same ready/next report as Android.
- Procedures no longer commit by themselves; `/qa-report` writes only its report; bug approval is one batch review;
  the README and the `project.yaml` template no longer name removed skills.

### Changed
- Skills version 2 (06.11.2026, `docs/skills-spec.md`): eight skills replace the thirteen of version 1 —
  `/project-context`, `/analyze-ticket`, `/plan-tests`, `/run-tests`, `/manage-bugs`, `/qa-report`, `/checkpoint`,
  `/test-pbi`. Their long procedures are in `docs/procedures/`; new helpers `scripts/qa_report.py` and
  `scripts/pbi_state.py`; `sync_skills.py` checks `requires-docs`, retires the version 1 names and records the files not
  committed; the preflight prints a route per module. The names map in `docs/skills-guide.md` §6.

### Fixed
- `common.pid_alive` on macOS and the BSDs (no `/proc`): a dead holder not yet reaped (a zombie) counted as alive, so
  its workspace lock was never taken over; the state now comes from `ps` (`test_two_real_processes_and_the_death_of_the_holder`
  failed on macOS for this reason).
- `compact_context.py` raised on a section with no dated entry (an empty `## Current state`).
- `qa_report.py`: two results of a case with the same instant are decided by the order of application, then of writing,
  never by the batch's name.
- The Playwright helper `integrations/playwright/qa-audit.ts` is in English (plan 8.11): `auditState(page, testInfo,
  name, { failOn, checks, redact })`, before `auditarEstado(…, { falharEm, verificacoes, redigir })`. The workspace
  layer that calls the engine is in English too (`scripts/audit_a11y.py`, `/audit-accessibility`).
- `visual` is no longer part of `--type full` (plan 8.16): it keeps screenshots of the audited site, logged-in
  pages included, in the home folder between runs, so it runs only when named (`--type visual`).
- Security: `cookie-samesite-unset` (needs-human, info) for a cookie without a declared SameSite, or set by
  JavaScript.
- Functional: a page listed with `crawl.audit_error_pages` that answers 4xx is `error-status-served` (warning, low),
  no longer a broken link; a page linking to itself is not its own referrer.

### Fixed
- Accessibility (plan 8.17): focus visibility is decided by pixels. An element with no computed style change when
  focused is compared with a screenshot of its box plus a margin, focused and blurred: identical pixels are
  `focus-not-visible` (problem, medium, confidence high); different pixels pass (focus drawn by a pseudo-element or
  a parent). `focus-visible` (needs-human) is left only where the pixels cannot decide. The keyboard walk puts the
  focus and the scroll back where they were; findings say "and N more" past five elements; `data:` URIs in HTML
  evidence are shortened.
- `cookie-samesite-none-insecure` was reported in Firefox and WebKit for cookies without any SameSite (those engines
  show a missing attribute as `None`); it is now reported only when `SameSite=None` is declared in `Set-Cookie`.

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
