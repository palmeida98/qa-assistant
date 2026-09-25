# Functional module

`qa-audit <url> --type functional` · specification: `docs/qa-audit.md` §18

## Goal

Generic functional checks that need no knowledge of the application: links, resources, JavaScript errors,
redirects, and basic form hygiene. Non-destructive by default; submitting a form is a double opt-in.

## Prerequisites

A browser engine (`qa-audit doctor`).

## Configuration (`config.yaml`, override under `modules.functional`)

| Key | Default | Meaning |
|---|---|---|
| `max_link_checks` | `50` | same-origin links the crawl did not visit, checked with `HEAD` (`GET` if refused) |
| `check_external_links` | `false` | `true` also checks external links (sends requests to third parties) |
| `max_items_listed` | `10` | occurrences listed in a finding |
| `forms` | `[]` | forms to submit, only with `--allow-submit` (below) |

## Checks

| Check | Status · severity |
|---|---|
| `broken-link` (4xx) · `broken-link-server-error` (5xx) | problem · medium · high |
| `broken-external-link` (only with `check_external_links`) | warning · low |
| `broken-resource` (same origin) · `broken-resource-third-party` | problem · medium · warning · low |
| `js-exception` (uncaught) · `console-error` | warning · medium · warning · low |
| `redirect-chain` (3+ redirects) | warning · low |
| `form-email-type` (e-mail field accepting any text) | warning · low · confidence medium |
| `form-submit-failed` (listed form answers 4xx/5xx or throws) | problem · medium (high on 5xx) |
| `form-submit-no-navigation` | needs-human |

Every finding lists the occurrences; broken links show the pages that link to them.

## Safety

- Links excluded by the discovery rules (logout, delete, …) or disallowed by `robots.txt` are never requested, not
  even with `HEAD`.
- Pages answering 4xx/5xx are reported as broken links and are not audited as content by any module.
- Aborted requests (the page navigated away, `--block-third-party`) are not failures; console messages about failed
  loads are left to `broken-resource`, so one failure is not reported twice.
- Forms: a form is submitted only when **both** `--allow-submit` is given and the form is listed under
  `modules.functional.forms` (page regular expression, selector, `fields`, `fields_env` for values from environment
  variables). Once, in its own tab, on the first matching audited page. Without the flag the report notes the
  listed forms that were not submitted. Use fictional, recognisable data (`QA-AUTO-…`) in a test environment.

## Limits

No scripted flows (login, checkout) yet; single-page applications that handle a form without navigation give
`form-submit-no-navigation` for a person to check. Status codes are those seen without login.
