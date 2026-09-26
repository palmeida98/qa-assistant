# qa-audit — automated audit engine

> Specification of the `qa-audit` engine (plan phase 8). Written before the code (methodology §15).
> Universal: every example uses fictional targets (`example.com`, `new-project.example`, a local test site).

---

## 0. What it is

`qa-audit` is a command-line engine that audits a website given only its URL:

```bash
qa-audit https://new-project.example
qa-audit https://new-project.example --type accessibility
```

It discovers pages, runs the selected QA modules, stores evidence in a local output folder and writes a report.
It stores nothing about the audited target outside that output folder, and sends nothing anywhere except to
the target itself.

It lives **alongside** the workspace layer (methodology, skills, Excel adapter; `docs/metodologia-core.md`):

| Layer | Use it for |
|---|---|
| `qa-audit` (this document) | A first, automated pass over any site: discovery, accessibility, and later functional, performance, security and visual checks. No project setup needed. |
| Workspace layer (`/onboarding-projeto`, `/executar-lote`, …) | A real project: designed test cases, roles, tracker, checkpoints. |

A workspace skill may call `qa-audit`: `/auditar-acessibilidade` runs it through `scripts/auditar_a11y.py`, which
translates the workspace's `acessibilidade:` section into an engine configuration (`docs/tecnica-acessibilidade.md` §2).

---

## 1. Installation

```bash
git clone <qa-assistant repository>
cd qa-assistant
pip install -e .                              # Python >= 3.12
python -m playwright install chromium         # add firefox / webkit if needed
qa-audit doctor
```

Dependencies have exact versions in `pyproject.toml` (`playwright`, `pyyaml`; `pillow` as the `visual` extra).
The browsers are the only download, and only at install time.

---

## 2. Command line

```text
qa-audit <url> [options]      audit a target
qa-audit doctor               check the environment (exit 0 ok · 1 something required is missing)
qa-audit version              print the version
qa-audit baselines [--delete HOST|all] [--baseline-dir DIR]   list or delete stored visual baselines (§19)
qa-audit login <url> --config FILE [--browser ENGINE]      log in once and save the session (§20)
qa-audit sessions [--delete HOST|all] [--sessions-dir DIR]  list or delete saved sessions (§20)
```

| Option | Default | Meaning |
|---|---|---|
| `--type LIST` | `full` | Comma-separated module names, or `full` (every available module that applies to the target). Unknown or not-yet-available names are a usage error (exit 2) listing what exists. |
| `--browser ENGINE` | `chromium` | `chromium`, `firefox` or `webkit`. |
| `--headed` | off | Show the browser window. |
| `--viewport WxH` | `1440x900` | Viewport size. |
| `--device NAME` | none | Playwright device descriptor (e.g. `"iPhone 15"`); overrides the viewport. |
| `--max-pages N` | `20` | Maximum pages to visit. |
| `--depth N` | `2` | Maximum link depth from the start URL (0 = start page only). |
| `--delay-ms N` | `500` | Pause between page visits. |
| `--exclude REGEX` | see §5 | Extra URL pattern never visited (repeatable). |
| `--screenshots MODE` | `on-problem` | `off`, `on-problem`, `all`. |
| `--video` | off | Record video of every page (sensitive, §7). |
| `--trace MODE` | `off` | `off`, `on`, `retain-on-problem` (sensitive, §7). |
| `--report LIST` | `html,json,csv,md` | Report formats. The terminal summary is always printed. |
| `--output DIR` | `./qa-output` | Output root. |
| `--cleanup` | off | Delete the run folder after printing the summary. |
| `--config FILE` | none | YAML configuration (§4). |
| `--fail-on SEVERITY` | `high` | Exit 1 if a problem at or above this severity exists; `none` never fails. |
| `--ignore-https-errors` | off | Audit a site whose certificate the browser rejects (e.g. a self-signed staging certificate); the report notes it, and the certificate's trust is then not judged. |
| `--allow-submit` | off | Let the functional module submit the forms **listed in the configuration** (§6, §18); forms found by discovery are never submitted. |
| `--baseline-dir DIR` | `~/.qa-audit/baselines` | Where visual baselines are kept (§19); never inside the output folder. |
| `--update-baseline` | off | Replace the visual baselines with this run's screenshots (after reviewing the differences). |
| `--block-third-party` | off | Block every request the page makes outside the target origin (§8). |
| `--no-color` | auto | Plain terminal output (also `NO_COLOR`). |

Exit codes: `0` ok · `1` problems at or above `--fail-on` · `2` usage or configuration error ·
`3` target unreachable or login failed · `4` browser could not start (run `qa-audit doctor`).

Login is configured in the configuration file (`auth:`, §20), not on the command line.

---

## 3. Architecture

```text
src/qa_audit/
├── cli.py              argument parsing, subcommands, exit codes
├── core/
│   ├── config.py       defaults < module config.yaml < --config file < command line
│   ├── findings.py     Finding, Evidence, severities, statuses (§9)
│   ├── evidence.py     run folder, screenshots, logs, cleanup (§7)
│   ├── privacy.py      redaction of secrets in evidence
│   ├── registry.py     module discovery: built-in packages + entry points "qa_audit.modules"
│   ├── runner.py       discovery -> module hooks per page -> finalize -> reports
│   └── console.py      terminal output (UTF-8, colours only on a TTY)
├── browser/
│   ├── session.py      engine abstraction (chromium / firefox / webkit), viewport, device, video, trace
│   └── discovery.py    same-origin crawl (§5)
├── modules/
│   └── <name>/         one folder per module (§10)
└── reports/            terminal, json, csv, markdown, html
```

`core` never imports Playwright at module import time; unit tests run without browsers.

---

## 4. Configuration

Precedence: built-in defaults < each module's `config.yaml` < `--config` file < command-line options.

```yaml
browser:
  engine: chromium
  headless: true
  viewport: {width: 1440, height: 900}
  device: null
  timeout_ms: 30000
  ignore_https_errors: false
crawl:
  max_pages: 20
  max_depth: 2
  delay_ms: 500
  respect_robots: true
  exclude: []                 # added to the built-in exclusions (§5)
  pages: []                   # explicit pages, visited first (§5)
  follow_links: true          # false: only the start URL and crawl.pages
  audit_error_pages: false    # true: the start URL and crawl.pages are audited even when they answer 4xx (§5)
network:
  block_third_party: false
evidence:
  screenshots: on-problem
  max_element_screenshots: 5  # per finding
  video: false
  trace: "off"
output:
  directory: ./qa-output
  formats: [html, json, csv, md]
  cleanup: false
fail_on: high
safety:
  allow_submit: false        # §6: also needs the form listed under modules.functional.forms
modules:
  accessibility:
    tags: [wcag2a, wcag2aa, wcag21a, wcag21aa]
```

**No credentials in configuration files.** A key whose name looks like a secret (`password`, `passwd`,
`secret`, `token`, `api_key`, `apikey`, `authorization`, `cookie`) with a literal value is a configuration error
(exit 2). Credentials, when login exists (§12), come from environment variables named in the file
(`pass_env: SHOP_ADMIN_PASS`) or from a hidden terminal prompt.

---

## 5. Discovery

1. Validate the URL: `http` or `https`, with a host. Anything else is a usage error.
2. Fetch `robots.txt` (when `respect_robots`) through the same browser context and user agent.
3. Visit the start URL; if it cannot be loaded, exit 3.
4. For each visited page record: requested and final URL, HTTP status, redirect chain, title, `lang`,
   response headers, links, forms (method, action, fields with name/type/required/label), interactive element
   count, console errors, failed requests, and timing.
5. Queue links breadth-first while under `max_pages` and `max_depth`.
6. With `crawl.pages` (a list of URLs or paths on the target origin), those pages are visited first, in order,
   after the start URL; `crawl.follow_links: false` visits only them (the start URL and the list).
7. A page answering 4xx or 5xx is not audited as content, and a start URL answering one stops the audit (exit 3).
   `crawl.audit_error_pages: true` (off by default) is the one exception, for single-page applications whose server
   answers 404 to a deep link and renders the page anyway: the pages **listed** (the start URL and `crawl.pages`)
   that answer **4xx** are audited as content, and the report notes each one with its status. Pages found by
   following links, and 5xx answers, are never audited that way.

**Safety rules (risk 3):**

- Pages are only reached with `GET` navigation to `<a href>` targets. The crawler never clicks, never types,
  never submits a form.
- Only the start URL's origin (scheme + host + port) is visited. External links are recorded, never requested.
- Built-in exclusions (case-insensitive, on the URL path and query): `logout`, `log-out`, `signout`,
  `sign-out`, `logoff`, `delete`, `remove`, `destroy`, `unsubscribe`, `deactivate`, `cancel`, plus
  `javascript:`, `mailto:`, `tel:` links and non-HTML resources (`.pdf`, `.zip`, images, media, office files).
- Disallowed paths in `robots.txt` are skipped (recorded as skipped, not as findings).
- `delay_ms` between visits; one page at a time.

**User agent (risk 4):** the engine's default user agent with ` qa-audit/<version>` appended, so site
owners can identify the traffic.

---

## 6. Destructive actions

Nothing in the engine submits forms, clicks buttons, creates accounts, sends messages, pays, or changes data by
default. The only exception is explicit and double: `--allow-submit` on the command line (or `safety.allow_submit`)
**and** the form listed under `modules.functional.forms` with its test data (§18). A form found by discovery alone
is never submitted, with or without the option. Test data should be recognisable and fictional (e.g. prefixed
`QA-AUTO-`), and only in a test environment.

---

## 7. Evidence and output

```text
qa-output/
├── .gitignore                    "*": nothing in here can be committed by accident
└── 2026-09-25_085000/
    ├── screenshots/
    ├── videos/                   only with --video
    ├── traces/                   only with --trace
    ├── logs/run.log
    ├── results.json
    ├── results.csv
    ├── report.md
    └── report.html
```

Risk 5 (evidence holds sensitive data):

- The output root always gets a `.gitignore` containing `*`, so a run inside any Git repository never stages
  evidence. The `qa-assistant` repository also ignores `qa-output/`, and a guard test fails if any tracked path
  sits under a `qa-output/` folder.
- Traces and videos store page content, cookies and tokens and cannot be redacted inside their files: both are
  off by default, and the terminal warns when they are on.
- `--cleanup` deletes the run folder after the summary is printed.
- Text evidence (logs, headers) goes through the redactor (`core/privacy.py`): `Authorization`, `Cookie`,
  `Set-Cookie` and API-key headers become `«redacted»`.

---

## 8. Privacy and network

Risk 6. By default the engine:

- has **no telemetry, analytics or uploads** of any kind;
- makes requests only to the target origin (`robots.txt`, pages). The page itself loads its own third-party
  resources, exactly as a visitor's browser would; `--block-third-party` blocks them (and records what was
  blocked), at the cost of an audit that no longer matches what visitors see;
- ships axe-core inside the package (§11): nothing is fetched from a CDN;
- writes self-contained HTML reports: inline CSS, no external resources, and a Content-Security-Policy
  `default-src 'none'` meta tag so the report cannot load anything even if audited content slipped through
  escaping;
- escapes every string taken from the audited site before putting it in a report;
- launches browsers with Playwright's defaults, which disable background networking, component updates, sync
  and metrics in Chromium, and data reporting and connectivity checks in Firefox; the engine adds Firefox
  preferences for telemetry, captive-portal detection and safe browsing, and for application, add-on and media
  plugin updates, remote settings, push and search updates (the recording-proxy test caught Firefox contacting
  Mozilla's update server about a minute into a run).

An integration test runs the browser through a recording proxy and fails if any request, from the page or the
browser itself, goes anywhere but the local test site.

Any future feature that needs an external service must be opt-in, off by default, and documented here.

---

## 9. Findings, severity and the evidence rule

Risk 2 (false positives). A finding is one of:

| Status | Meaning | Evidence |
|---|---|---|
| `problem` | A rule is violated and the engine can show it. | **Required** — refused without it |
| `warning` | Likely an issue, but context can make it acceptable. | **Required** |
| `needs-human` | The engine cannot decide; a person must check. | Optional |

A check with no finding on a page counts as **passed**.

Each finding has a `confidence` (`high`, `medium`, `low`) and fields: `id`, `module`, `check`, `title`,
`status`, `severity`, `url`, `element` (selector and HTML snippet), `description`, `steps`, `expected`,
`actual`, `evidence`, `recommendation`, `references`.

Severity (conservative; a module may only use the levels its README justifies):

| Severity | Used when |
|---|---|
| `critical` | Blocks core use of the site for **all** users, or exposes secrets or personal data. Never assigned by an automated heuristic alone. |
| `high` | Blocks a task for a group of users (e.g. a form a screen-reader user cannot complete). |
| `medium` | Significant degradation or a clear standard violation with a workaround. |
| `low` | Minor issue or best-practice deviation. |
| `info` | Observation, no action implied. |

The terminal and report show counts per status and severity.

---

## 10. Modules

A module is a folder in `src/qa_audit/modules/`:

```text
modules/<name>/
├── README.md       goal, prerequisites, configuration, checks, severities, output, limits
├── config.yaml     defaults (merged under modules.<name>)
├── module.py       MODULE = <AuditModule subclass instance>
└── checks.py       the checks (optional split)
```

```python
class AuditModule:
    name = "accessibility"          # used by --type
    title = "Accessibility"
    requires_browser = True

    def applicable(self, ctx) -> tuple[bool, str]: ...   # (applies, reason) for --type full
    def on_page(self, page, info, ctx) -> None: ...      # called once per visited page, page is live
    def finalize(self, ctx) -> None: ...                 # after the crawl
    manual_checks: list[dict] = []                       # human checklist shown in the report
```

Modules run on each page in ascending `order`: modules that only read the page first (performance measures an
untouched page), modules that interact with it (Tab key, viewport changes) last. Modules report through
`ctx.add_finding(...)`, `ctx.passed(...)` and `ctx.add_metric(...)`, and may reject their configuration with
`validate_config` (exit 2 before the audit starts). Every visited page is loaded once
and shared by all modules (one visit per page, polite to the target).

External modules register through the `qa_audit.modules` entry point group; the core needs no change.

| Module | Status |
|---|---|
| `accessibility` | Available (§11) |
| `security` | Available, passive only (§16) |
| `performance` | Available (§17) |
| `functional` | Available (§18) |
| `visual` | Available (§19) |

---

## 11. Accessibility module

Automated checks:

- **axe-core** (version pinned, file shipped in `modules/accessibility/vendor/`). It is injected as a `<script>`
  element and runs in the page's own realm; the result comes back as JSON text through an attribute of `<html>`,
  removed afterwards. Playwright `evaluate` runs in a separate realm, which fails in Firefox (X-ray wrappers hide
  axe's functions); it is only the fallback when the page's Content-Security-Policy blocks inline scripts
  (Chromium does not apply the page CSP to `evaluate`). When both fail, the page gets a `needs-human` finding
  `axe-not-run`. Default tags: WCAG 2.0 and 2.1, A and AA.
  - `violations` → `problem`, one finding per rule per page with up to 5 affected elements (selector, snippet,
    element screenshot).
  - `incomplete` → `needs-human`.
  - `references`: the WCAG criteria of the rule (`WCAG 1.4.3`), the WCAG version and level of each of its axe
    tags (`wcag2aa` → `WCAG 2.0 AA`, `wcag22aa` → `WCAG 2.2 AA`) and the rule's help page. A rule outside WCAG
    (e.g. `best-practice`, only when that tag is configured) has neither criteria nor levels.
  - Severity from axe impact: `critical` → `high`, `serious` → `medium`, `moderate` and `minor` → `low`.
    Never `critical` (§9).
- **Keyboard** (engine's own): tabs through up to 40 stops; a focus that stays on one element for three Tabs
  while tabbable elements follow it is a keyboard trap (`warning`, `high`) — at the last element it is the end of
  the page (Firefox headless keeps the focus there); an element whose outline, box-shadow, border, background and text decoration do not
  change when focused is reported as `needs-human` (focus visibility, WCAG 2.4.7).
- **Reflow** (WCAG 1.4.10): at 320 CSS px wide, horizontal scrolling of the page is a `warning`, `medium`, with a
  screenshot; data tables and maps can legitimately scroll.
- Frames are not scanned inside; a page with iframes gets a `needs-human` note.

Manual checks (always listed, never marked as done): screen reader announcement of key flows, quality of
alternative texts, captions and transcripts, text resize to 200 %, meaning conveyed only by colour or sound,
error identification and suggestions, consistent navigation and identification, timing and motion.

Every accessibility report states: **an automated audit finds only part of the WCAG issues and is not a
conformance certification.**

---

## 12. Planned

- Several engines in one run (compatibility), sitemap discovery.
- Active security checks, always opt-in; scripted user flows (login, checkout) as
  configured, opt-in functional scenarios.

---

## 13. Platform notes

Risk 7.

- **WebKit on Windows** can be blocked by Windows application control (Smart App Control / WDAC): its bundled
  `libEGL.dll` is refused and Playwright reports it as a missing library. `qa-audit doctor` shows the engine as
  unavailable with that hint; use another engine locally. CI runs all three engines on Linux. Smart App Control
  decides by cloud reputation, so the block can disappear later without any change here (seen on 25.09.2026:
  WebKit and Firefox blocked in the morning, working in the afternoon); run `doctor` again rather than assuming.
- **Firefox (Playwright build, Windows with application control)**: the browser's cryptography is broken:
  `crypto.getRandomValues` throws `OperationError`, and **no HTTPS page loads** (`NS_ERROR_FAILURE`). `doctor` tests
  the browser's cryptography and flags it; an HTTPS start page that fails this way says so. Use Chromium there.
  On plain HTTP pages, `crypto.getRandomValues` still throws even in the page's
  realm. Only when the native call fails, the accessibility module installs a `Math.random` substitute while axe
  runs (axe uses it for internal ids) and restores the original; the report notes it.
- The Bash sandbox of some agent environments refuses to spawn the browser process (`spawn UNKNOWN`); `doctor`
  names it. Run from a normal terminal.
- Video needs Playwright's `ffmpeg` (`python -m playwright install ffmpeg`); `doctor` checks it.
- The terminal output is UTF-8 on every platform; symbols fall back to ASCII when the console cannot print them.

---

## 14. Maintenance of the bundled axe-core

Risk 9. `vendor/axe.yaml` records the version, the npm tarball URL, the npm integrity hash and the SHA-256 of
`axe.min.js`. `python scripts/update_axe.py <version>` downloads the tarball, checks npm's integrity hash,
extracts `axe.min.js` and its licence (MPL-2.0), and rewrites the manifest. Its two checksum lines carry
detect-secrets' inline allowlist pragma, its line-level marker for false positives (public hashes, not secrets;
no file is excluded from the scan), and a guard test fails if that marker appears on any other line of the
repository. `.gitattributes` keeps Git from converting the line endings of `vendor/` (a CRLF checkout would break
the hash). A unit test fails if the file and the
manifest disagree (hash or version). Updating axe is its own commit, with the accessibility integration tests
passing.

---

## 15. Tests of the engine

| Folder | What | Runner |
|---|---|---|
| `tests/unit/` | config, findings, registry, reports, CLI parsing, privacy, axe manifest | `python -m unittest discover -s tests` (with the rest of the framework) |
| `tests/integration/` | the real engine against the local test site, per browser | `python -m unittest discover -s tests/integration` |
| `tests/fixtures/site/` | a small static site with planted defects and trap links (logout, delete, a POST form) | served on `127.0.0.1` by the tests |

The integration tests assert, among others: planted accessibility defects are found and clean pages have no
problems; no trap link is requested and no form is posted; no request leaves `127.0.0.1` (recording proxy);
reports are written, escaped and self-contained; `--cleanup` removes the run folder.
Engines: `QA_AUDIT_TEST_ENGINES` (default `chromium,firefox`; CI adds `webkit`). An engine that cannot start is
skipped with the reason.

---

## 16. Security module (passive)

`--type security`. **Passive only**: it reads what the audit already received (response headers, cookies the site
set, the pages' HTML, the TLS details of the connection). The one extra request is a single `GET` of the start URL
over `http://` when the target is `https://`, to see whether it redirects to HTTPS. No payloads, no probing of
paths (`/.git/`, `/admin`), no fuzzing, no scanning: those belong to future opt-in modules.

Header checks are made on every audited page and reported **once per check**, listing the affected pages, so a
site-wide missing header is one finding, not twenty. Cookie values are never read into the results: only names and
flags.

| Check | When | Status · severity |
|---|---|---|
| `not-https` | the target is served over `http://` | problem · **high** when a page has a password field, else medium |
| `form-to-http` | an `https://` page has a form whose action is `http://` | problem · high |
| `mixed-content-active` | an `https://` page references `http://` scripts, stylesheets, frames or objects | problem · medium |
| `mixed-content-passive` | … `http://` images, audio or video | warning · low |
| `http-not-redirected` | `http://` start URL answers without redirecting to `https://` | warning · medium |
| `tls-old-protocol` | TLS 1.1 or older | problem · high |
| `tls-cert-expiring` | the certificate expires within 14 days | warning · medium |
| `hsts-missing`, `hsts-short` | `https://` without `Strict-Transport-Security`, or `max-age` under 180 days | warning · low |
| `csp-missing` | no `Content-Security-Policy` header or meta tag | warning · low |
| `csp-unsafe` | `script-src` (or `default-src`) allows `'unsafe-inline'` without nonce or hash, `'unsafe-eval'`, or `*` | warning · low |
| `clickjacking` | neither `X-Frame-Options` nor CSP `frame-ancestors` | warning · low (medium on pages with a password field) |
| `nosniff-missing` | no `X-Content-Type-Options: nosniff` | warning · low |
| `version-disclosure` | `Server`, `X-Powered-By`, `X-AspNet-Version` or `X-AspNetMvc-Version` carry a version number | warning · low |
| `cookie-not-secure` | an `https://` site sets a cookie without `Secure` | problem · medium |
| `cookie-samesite-none-insecure` | `SameSite=None` without `Secure` | problem · medium |
| `cookie-session-not-httponly` | a cookie whose name looks like a session or token (`sess`, `sid`, `auth`, `token`, `jwt`, `login`) without `HttpOnly` | warning · medium (confidence medium: the name is a heuristic) |

Only cookies of the target's own domain are judged; third-party cookies belong to their owners. Thresholds come
from the OWASP Secure Headers Project and HTTP Strict Transport Security guidance (`max-age` of at least 180 days;
a year recommended) and are configurable under `modules.security`. A header check that passes on every page counts
as passed once per page.

With `--ignore-https-errors` the certificate's trust is not judged (the browser accepted it only because it was
told to), and the report says so; its protocol and expiry date are still checked.

---

## 17. Performance module

`--type performance`. Risk 8 (measurements are noisy): every measured value is shown in a **metrics table**
(observed metric · threshold · result); a value becomes a finding only above a threshold, and the finding is a
`warning` with confidence `medium`, because it is **one lab measurement** from the auditing machine, while the
reference thresholds are meant for field data at the 75th percentile of real users.

Measured on every audited page, in the browser, with the Navigation Timing, Paint Timing, Largest Contentful Paint,
Layout Instability and Resource Timing APIs (no extra requests):

| Metric | Default threshold (good / poor) | Source |
|---|---|---|
| TTFB (`responseStart`) | 800 ms / 1800 ms | web.dev, *Time to First Byte* |
| FCP | 1800 ms / 3000 ms | web.dev, *First Contentful Paint* |
| LCP | 2500 ms / 4000 ms | web.dev, *Largest Contentful Paint* |
| CLS (session windows, until the measurement) | 0.1 / 0.25 | web.dev, *Cumulative Layout Shift* |
| DNS, connection, TLS, DOMContentLoaded, load | none: measured only | — |
| requests, transferred KB, JavaScript KB, image KB, CSS KB | none: measured only | — |
| console errors | none: measured only (findings belong to `functional`) | — |

Result: `good` (≤ good), `needs-improvement` (≤ poor) → warning · low, `poor` (> poor) → warning · medium. A
threshold can be replaced in `modules.performance.thresholds`, either with `{good, poor}` or with a single number (a
budget: above it → warning · low, result `over-budget`); `null` means measured only. One finding per metric, listing
the pages above the threshold.

Limits, stated in the report: the first page is measured with a cold cache and later pages may reuse cached
resources; LCP and CLS are only available where the engine implements them (CLS: Chromium; LCP: Chromium, Firefox and
WebKit 26) and are reported as `n/a` elsewhere, never estimated; CLS covers only the shifts before the measurement
(after `load` and network idle); sizes of cross-origin resources without `Timing-Allow-Origin` read as 0 and the
total is marked partial.

---

## 18. Functional module

`--type functional`. Generic checks that need no knowledge of the application, all non-destructive by default.

| Check | What | Status · severity |
|---|---|---|
| `broken-link` | a same-origin link answers 4xx | problem · medium |
| `broken-link-server-error` | a same-origin link answers 5xx | problem · high |
| `broken-external-link` | an external link fails (only with `check_external_links: true`) | warning · low |
| `broken-resource` | a same-origin image, script, stylesheet or fetch of an audited page fails (4xx/5xx or network error) | problem · medium |
| `broken-resource-third-party` | the same for another origin | warning · low |
| `js-exception` | an uncaught JavaScript exception on a page | warning · medium |
| `console-error` | `console.error` messages (resource-load errors excluded: they are `broken-resource`) | warning · low |
| `redirect-chain` | a link reaches its page through 3 or more redirects | warning · low |
| `form-email-type` | a field named or labelled as an e-mail is not `type=email` and has no `pattern` (accepts any text in the browser) | warning · low · confidence medium |
| `form-submit-failed` | a **listed** form, submitted with `--allow-submit`, answers 4xx (medium) or 5xx (high) or throws | problem |
| `form-submit-no-navigation` | a listed form was submitted but nothing navigated (e.g. a script handles it) | needs-human |

Pages that answer 4xx/5xx are reported here as broken links and are not audited as content by any module (the
one exception is a listed page with `crawl.audit_error_pages`, §5, which is still reported as a broken link).

Links: every same-origin link seen on the audited pages is judged. Visited pages give their status directly; links
the crawl did not visit (beyond depth or `max_pages`, or non-HTML such as PDFs) are checked with `HEAD` (falling
back to `GET` when `HEAD` is refused), at most `max_link_checks` (default 50), with the crawl delay between them.
Links excluded by the safety rules (§5) or disallowed by `robots.txt` are **never** requested. External links are
not requested unless `check_external_links: true` (it sends traffic to third parties). Each finding lists the
broken URLs with the pages that link to them. Aborted requests (navigation away, `--block-third-party`) are not
failures.

Listed forms (`--allow-submit` required):

```yaml
modules:
  functional:
    forms:
      - page: "/contact"                 # regular expression on the URL of an audited page
        selector: "form#contact"         # the form
        fields: {name: "QA-AUTO Test", message: "QA-AUTO automated check"}
        fields_env: {email: SHOP_TEST_EMAIL}   # values from environment variables
```

The form is filled and submitted once, in its own tab, on the first audited page whose URL matches; the result is
the response to the navigation that follows. Literal secrets are refused in the configuration (§4): use
`fields_env`.

---

## 19. Visual module

`--type visual` (needs Pillow: `pip install -e .[visual]`; without it the module is skipped in `full` and
`--type visual` is a usage error).

For every audited page and every configured viewport (default: desktop 1440×900, tablet 768×1024, mobile 390×844)
the module takes a full-page screenshot with animations disabled and the text caret hidden, after web fonts are
ready, masking the configured selectors (`mask`, for clocks, carousels, ads), and compares it with the **baseline**
of the same page, viewport and engine.

| Check | When | Status · severity |
|---|---|---|
| `visual-diff` | more than `max_diff_ratio` of the pixels (default 0.1 %) differ from the baseline by more than `tolerance` (default 16 of 255 per channel) | needs-human · medium: a change is not a bug until a person says so |
| `visual-size-changed` | the page's rendered size changed | needs-human · low |
| `horizontal-overflow` | at a viewport, the page is wider than the viewport | warning · medium (tablet, mobile) · low (desktop) |
| `viewport-meta-missing` | no `<meta name="viewport">` (mobile browsers render a zoomed-out desktop page) | warning · low |

Evidence of a difference: the baseline, the current screenshot and a diff image (changed pixels in red over a faded
baseline), all copied into the run folder so the report stands alone. A metrics row per page and viewport gives the
share of changed pixels (`baseline created`, `unchanged`, `changed`).

**Baselines** (§7): the first run of a page/viewport/engine creates its baseline and reports nothing for it. They
live in `~/.qa-audit/baselines/<host_port>/<engine>/<viewport>/` (or `--baseline-dir`), **outside the output folder
and any repository**; the root gets a `.gitignore` with `*`. They are screenshots of the audited site: `--cleanup`
does not delete them (they must survive between runs); `qa-audit baselines` lists them and
`qa-audit baselines --delete <host>` (or `all`) removes them. `--update-baseline` replaces them after a reviewed
change. The report says where they are.

Screenshots taller than `max_height` (default 8000 px) are cut there. The viewport is restored after the module
runs; it runs after the modules that only read the page and before accessibility (whose keyboard walk leaves
focus rings on screen).

---

## 20. Authentication

Pages behind a login are audited after one login at the start of the run, in the same browser context (the
session's cookies are shared by every page). Configured under `auth:`; without it, the audit is anonymous.

```yaml
auth:
  method: form                      # form | manual | storage-state
  login_url: /login                 # absolute, or a path on the target origin
  username_env: SHOP_QA_USER        # form: names of environment variables, never values (§4)
  pass_env: SHOP_QA_PASS
  # username_selector / pass_selector / submit_selector: optional; detected otherwise
  success_url: "/account"           # regular expression on the URL after login ...
  # success_selector: "#logout"     # ... or a CSS selector that appears after login (one of the two is required)
  # expired_url: "/login"           # optional; default: the path of login_url
  # manual_timeout_s: 300
```

| Method | What happens |
|---|---|
| `form` | Opens `login_url`, fills the user and password fields (detected: the first visible `input[type=password]`, the visible text/e-mail field before it in the same form, the form's submit button; or the configured selectors), submits **once**, and waits for the success check. |
| `manual` | For Active Directory, second factors or CAPTCHAs: the browser opens **visibly** (headless is overridden), the terminal asks the person to log in, and the audit waits up to `manual_timeout_s` for the success check. The engine types nothing. |
| `storage-state` | Reuses a session saved earlier by `qa-audit login` (below): the browser context starts with its cookies and local storage, and **nothing is submitted** during the audit. |

Rules:

- **One attempt.** A failed login (success check not met within the timeout) stops the audit with exit 3 and is
  never retried, so a shared account is not locked.
- **The login is the only form the engine submits without `--allow-submit`**, and only because it is configured.
  Logout-style links stay excluded (§5), so the crawl does not end the session.
- **Session expiry.** A page whose final URL matches `expired_url` (default: the login path) after the login means
  the session ended: the crawl stops, the page is not audited, and the report says so.
- **Credentials.** Values are read from the environment at run time and never written anywhere: the redactor
  replaces them (username and password) in every text the engine writes — findings, evidence, page data and logs.
  Traces are refused together with `auth` (they record what is typed); screenshots and videos can show the
  logged-in user's data, as any page content can.
- **Configuration errors are exit 2 before the browser starts**: unknown method, `form` without `username_env` /
  `pass_env`, neither `success_url` nor `success_selector`, invalid regular expressions, environment variables
  not set, `auth` with `evidence.trace`.

### 20.1 Saved sessions (`storage-state`)

A login that needs a person (Active Directory, a second factor) should not be repeated before every audit. The
session is saved once and reused until it expires:

```yaml
auth:
  method: storage-state
  login_method: manual              # how `qa-audit login` creates the session: manual (default) | form
  login_url: https://staging.shop.example/login
  success_selector: "#user-menu"    # checked by `qa-audit login`; one of success_url / success_selector
  # state_file: ~/somewhere/shop.json   # default: ~/.qa-audit/sessions/<host>.json
  # username_env / pass_env: only with login_method: form
```

1. `qa-audit login <url> --config FILE` performs the `login_method` login exactly as §20 describes (one attempt,
   success check, a visible browser for `manual`), then saves the browser's storage state (cookies and local
   storage) to `state_file` and exits: nothing is audited and no output folder is created. Exit codes: `0` saved ·
   `2` configuration error · `3` login failed · `4` browser could not start.
2. An audit with `method: storage-state` starts the browser context from that file and does not log in. If the
   start URL lands on the login page (`expired_url`, default the path of `login_url`), the saved session has
   expired: exit 3 with a hint to run `qa-audit login` again. A page that lands there later stops the crawl, as in
   §20. The report notes the file's age.

The state file holds live session cookies: it is a credential.

- It is never written or read inside the output folder or inside a Git working tree (configuration error, exit 2),
  so it cannot be committed or shipped with a report. The default folder `~/.qa-audit/sessions/` gets a
  `.gitignore` containing `*`; on Linux and macOS the file is created readable by its owner only (`0600`).
- A missing file is a configuration error (exit 2) that names the `qa-audit login` command to create it.
- Every cookie value of the state (six characters or more) goes to the redactor: it is replaced by
  `«cookie:NAME»` in everything the engine writes, like the credentials of `form`.
- Traces stay refused with `auth` (§20).
- `qa-audit sessions` lists the saved sessions (host, age, size) in the default folder or `--sessions-dir`;
  `--delete HOST|all` removes them. Deleting a session does not log it out on the server.
