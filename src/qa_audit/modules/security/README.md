# Security module (passive)

`qa-audit <url> --type security` · specification: `docs/qa-audit.md` §16

## Goal

Find configuration weaknesses that are visible to any visitor, without attacking anything: transport security,
security headers, cookie flags, mixed content and version disclosure. **It is not a penetration test**: a clean
result does not mean the site is secure.

## What it does and does not do

- Reads what the audit already received: response headers of each page, the site's own cookies (names and flags;
  values are dropped as soon as they are read), the pages' HTML, and the TLS details of the connection.
- One extra request: when the target is `https://`, a single `GET` of the start URL over `http://` with redirects
  off, to see whether it redirects to HTTPS.
- No payloads, no path probing (`/.git/`, `/admin`, backups), no fuzzing, no port scanning, no brute force.

## Prerequisites

A browser engine that can load the target (`qa-audit doctor`; on Windows with application control, Firefox cannot
load HTTPS at all).

## Configuration (`config.yaml`, override under `modules.security`)

| Key | Default | Meaning |
|---|---|---|
| `hsts_min_age` | `15552000` (180 days) | below it, `hsts-short` |
| `cert_expiry_days` | `14` | certificate expiring sooner: `tls-cert-expiring` |
| `check_http_redirect` | `true` | the one `http://` request described above |
| `max_pages_listed` | `10` | affected pages listed in a finding |

## Checks

See the table in `docs/qa-audit.md` §16 (status, severity and trigger of every check). Header checks run on every
audited page and are reported once per check with the affected pages; a check that passes counts as passed on each
page. Severities are conservative: missing hardening headers are `warning · low`; `problem` is kept for things that
expose data in transit (`not-https`, `form-to-http`, mixed active content, old TLS) or cookies an HTTPS site sends
without `Secure`.

## Output

Findings with the observed header lines, cookie names and flags, the insecure URLs referenced by the page, TLS
protocol and expiry, and a recommendation with references (OWASP Secure Headers Project, MDN, RFC 8996).

## Limits

- Only the target's own cookies are judged; third-party cookies belong to their owners.
- `frame-ancestors` in a `<meta>` tag is ignored, as browsers ignore it.
- With `--ignore-https-errors` the certificate's trust is not judged; protocol and expiry still are.
- Headers can differ per path or per user; only what the unauthenticated crawl saw is judged.
