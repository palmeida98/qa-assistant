# Accessibility module

`qa-audit <url> --type accessibility` · specification: `docs/qa-audit.md` §11

## Goal

A first automated pass over WCAG 2.1 levels A and AA, with every result backed by evidence, and a checklist of what
only a person can judge. **It is not a conformance certification.**

## Prerequisites

A browser engine (`qa-audit doctor`). axe-core ships with the package (`vendor/`, version and SHA-256 in
`vendor/axe.yaml`); nothing is downloaded at run time.

## Configuration (`config.yaml`, override under `modules.accessibility`)

| Key | Default | Meaning |
|---|---|---|
| `tags` | `wcag2a, wcag2aa, wcag21a, wcag21aa` | axe-core rule tags to run |
| `max_nodes_per_rule` | `5` | affected elements listed per finding |
| `keyboard.enabled`, `keyboard.max_stops` | `true`, `40` | keyboard walk |
| `keyboard.focus_margin` | `8` | px around a focused element compared focused vs blurred |
| `reflow.enabled`, `reflow.width` | `true`, `320` | reflow check width in CSS px |

## Checks, statuses and severities

| Check | Status | Severity | Confidence |
|---|---|---|---|
| `axe/<rule>` violations | problem | axe `critical` → high, `serious` → medium, `moderate`/`minor` → low | high |
| `axe/<rule>` incomplete | needs-human | same mapping | low |
| `keyboard-trap`: Tab three times leaves focus on one element while tabbable elements follow | warning | high | medium |
| `focus-not-visible`: no computed style change and identical pixels focused and blurred (element box + margin) | problem | medium | high |
| `focus-visible`: no computed style change, and the pixels cannot decide (no size, off screen, area changing on its own) | needs-human | medium | low |
| `reflow`: horizontal scrolling at 320 CSS px | warning | medium | medium |
| `frames-not-scanned`: the page has frames | needs-human | info | high |
| `axe-not-run`: axe could not load (usually a strict CSP in Firefox/WebKit) | needs-human | info | high |

Never `critical`: that level needs a person's judgement (`docs/qa-audit.md` §9).

## How it runs

- axe-core is injected as a `<script>` element so it runs in the page's own realm, and its result comes back as JSON
  text through an attribute of `<html>` (removed afterwards). Evaluating it from Playwright's separate realm fails
  in Firefox: page objects are seen through X-ray wrappers, and `crypto.getRandomValues` refuses typed arrays from
  another realm. When the page's Content-Security-Policy blocks inline scripts, the module falls back to
  `evaluate` (Chromium is not subject to the page CSP there); if that fails too, the page gets `axe-not-run`.
- Some browser builds throw from `crypto.getRandomValues` even in the page realm (seen in Playwright's Firefox on
  Windows). Only then a `Math.random` substitute is installed while axe runs, and put back afterwards; the report
  notes it. axe uses it for internal ids only.
- Keyboard: only the Tab key is pressed, which moves focus and never activates anything. Firefox headless keeps the
  focus on the last element at the end of the page; that is recognised as the end, not a trap.
- Reflow: the viewport is narrowed to 320 CSS px, measured, screenshotted when it overflows, and restored.

## Output

Findings in every report, with the affected element (selector and HTML snippet), element and page screenshots,
WCAG references and the axe rule documentation. The manual checklist (screen reader, alternative text quality,
media, text resize, colour, errors, consistency, timing, focus order) is listed in every report, never marked done.

## Limits

Content inside frames and shadow roots is not screenshotted; frames are not scanned. Colour contrast over images
and gradients comes back as `needs-human` from axe. A clean automated result does not mean the page is accessible.
