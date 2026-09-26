# Accessibility technique

> **Layer 2 — Technique for web accessibility audits (WCAG 2.1 AA)**
> Version 1.2 · 2026-09-26 · Plan, tasks 3.2 and 8.10 (automated part on the `qa-audit` engine); in English since plan 8.11.
>
> Describes **how** the assistant audits a web application's accessibility. It is project agnostic: it contains no URLs, accounts nor rules of any client. The examples use the fictional project **Demo Shop**.
>
> Subordinate to `methodology.md` (decision rules, recording, bugs) and to `technique-browser.md` (preparation, authentication, locators, evidence in the browser).

---

## 0. Scope

- Reference standard: **WCAG 2.1, level AA** (level A included), unless `project.yaml` gives another (`accessibility.standard`).
- Two parts, always both:
  - **automated**: axe-core, keyboard and reflow through the `qa-audit` engine, page by page (§2);
  - **guided manual**: keyboard, focus, screen reader, contrast, zoom and reflow (§3). The automated part detects a fraction of the problems; **"0 axe violations" does not mean "accessible"**.
- Each failure is mapped to the **WCAG criterion** (e.g. 1.4.3) and to the **level** (A/AA).
- It does **not** cover formal certification nor legal conformance audits: the result is test evidence, not a conformance statement.

---

## 1. Execution levels

| Level | Tool | Serves for |
|---|---|---|
| **N0 · Static** | The page's HTML | `lang`, `title`, heading structure, `alt` on static images |
| **N1 · Automated** | `scripts/audit_a11y.py` on the `qa-audit` engine (bundled axe-core, `docs/qa-audit.md` §11) | Sweep of the declared pages, with and without login |
| **N1 · Guided manual** | Playwright MCP or browser, by the agent with the user | Keyboard, focus, screen reader, zoom, reflow (§3) |
| **N2 · Deterministic** | Playwright specs with `@axe-core/playwright` in the workspace, or `auditState` (`technique-browser.md` §13) | Regression: "page X is still free of A/AA violations" |

---

## 2. Automated part (axe-core)

1. The pages to audit come from the `accessibility:` section of `project.yaml` (name, URL, whether it needs a login). Only declared test environments.
2. Run, from the root of qa-assistant:
   `python scripts/audit_a11y.py <workspace> [--output runs/a11y_<date>.json]`
   The script translates the `accessibility:` section into a configuration of the `qa-audit` engine and runs it with the accessibility module only, only on the declared pages (without following links):
   - the pages without login in an anonymous run; the pages with login in another, after **one** login through the declared form (`accessibility.login`). The credentials come from the workspace's `.env` and reach the engine through environment variables, never on the command line nor in files;
   - the login only counts as done when the URL leaves the login page (or when `login.success_url` / `login.success_selector` is met, if defined). A failed login is not repeated (the shared account is not locked) and the pages with login stay **not audited**;
   - a page that opens at another URL (e.g. session lost, sent back to the login) stays **not audited**, so as not to give a false Pass;
   - a page that answers 4xx/5xx stays **not audited** (it is an error page, not the content). Exception: in an SPA whose server answers 404 to a direct link and shows the page anyway, `accessibility.audit_error_pages: true` audits the declared pages that answer 4xx; the report names each one in the notes. The 404 is still a server problem, to record as an observation;
   - the engine's configuration and report are temporary: only the workspace's report is kept (`--output`).
3. The report separates, per page:
   - **WCAG violations** (axe rules with a WCAG criterion of the standard): each with the rule, criterion(s), level, suggested impact, number of elements and the first selectors;
   - **best practices** (axe rules without a WCAG criterion): observations, not Fail;
   - **incomplete** (axe could not decide, e.g. contrast over images): they go to the manual review (§3), neither Fail nor Pass;
   - **the engine's other checks**: keyboard trap (2.1.2), focus not visible (2.4.7: `focus-not-visible` when the element's screenshot is identical with and without focus; `focus-visible` when the screenshots are not enough to decide) and horizontal scrolling at 320 px (1.4.10). They are leads for the manual part (§3), which confirms or discards them; on their own they do not give Fail.
4. axe's impact (`critical`, `serious`, `moderate`, `minor`) goes through the engine as a severity (`high`, `medium`, `low`) and is only a suggested severity; the decision is the user's (methodology §9.2).
5. Exit codes: `0` audit done (violations are results, not errors) · `2` invalid configuration or engine not installed (nothing run) · `3` the browser did not start, the login failed or a page was not audited (Blocked by environment).

---

## 3. Guided manual part

Per page or flow, with the browser (Playwright MCP or by hand), check and record:

| Check | Criteria | How |
|---|---|---|
| Everything operable with the keyboard only, without traps | 2.1.1, 2.1.2 | `Tab`/`Shift+Tab`, `Enter`, `Space`, arrows; leave each component |
| Logical focus order | 2.4.3 | The `Tab` order follows the visual and reading order |
| Visible focus | 2.4.7 | Each focused element has a visible indicator |
| Accessible names, roles and states | 4.1.2, 1.3.1 | Accessibility snapshot: each control with the right name and role |
| Screen reader (sample) | 1.3.1, 4.1.2, 4.1.3 | NVDA/VoiceOver on a main flow; status messages announced |
| Contrast of text and components | 1.4.3, 1.4.11 | axe's incomplete results + states (hover, focus, disabled) |
| 200% zoom | 1.4.4 | No loss of content nor function |
| Reflow at 320 px wide | 1.4.10 | No horizontal scrolling of the content (except tables, maps) |
| Text spacing | 1.4.12 | No cuts with increased spacing |
| Form errors identified and with a suggestion | 3.3.1, 3.3.3 | Message in text, associated with the field |
| Page language and title | 3.1.1, 2.4.2 | Right `lang`; the `title` describes the page |
| Alternatives to images | 1.1.1 | `alt` describes the function, or empty if decorative |

Each manual check is a case (or a step of a case) with a verifiable expected result, like any other.

---

## 4. Cases and classification

- A typical automated case: *"Page X has no WCAG 2.1 A/AA violations detected by axe-core"*. Technique: tool-assisted error guessing.
- **Pass**: 0 WCAG violations on the page (the incomplete results go to the manual part).
- **Fail**: at least one WCAG violation; the `actual_result` lists the rules and the criteria.
- **Blocked**: the page did not open, the login failed, or the browser did not start (environment/tool).
- Best practices without a WCAG criterion: observation, never Fail.

---

## 5. Bugs

- **One defect per distinct problem** (axe rule + cause), not per element: if the same rule fails on 12 buttons for the same cause, it is one defect with the 12 selectors (the first ones in the text, the total given).
- The same rule on several pages, with the same cause (e.g. a shared component): one defect, with the pages listed (methodology §9.1, §10).
- Title with the criterion: *"[1.4.3 AA] Insufficient contrast on the product price"*.
- Evidence: rule, criterion and level, page, selectors, axe's `failureSummary`, and the rule's link (`helpUrl`). No screenshots, unless the project's policy asks for them or the problem is visual (contrast, focus).

---

## 6. Preparation and security

- Environment confirmed against `project.yaml` (`technique-browser.md` §2).
- Credentials only from `.env`; never in arguments, reports or bugs.
- The axe report includes HTML excerpts (truncated): in a real project they may contain data; they stay in the workspace, never in the `qa-assistant` repository.
- Normal pace: one page at a time, without sweeping whole sites with a crawler.

---

## 7. Installing the tool

Once per machine, from the root of qa-assistant, install the `qa-audit` engine (`docs/qa-audit-guide.md`: `scripts/install.ps1` or `scripts/install.sh`, or `pip install -e .` and `python -m playwright install chromium`) and confirm with:

```
qa-audit doctor
```

axe-core comes bundled with the engine (fixed version, `docs/qa-audit.md` §14): nothing is downloaded from a CDN. `accessibility.browser` accepts `chromium` (default), `firefox` or `webkit`, the Playwright browsers that `doctor` shows as available; the old value `chrome` is treated as `chromium`, with a warning.

---

## 8. Link to the methodology

| Methodology | In this technique |
|---|---|
| §5 case design | §4 (automated cases) and §3 (manual) |
| §7 run log | one item per case; `actual_result` = rules and criteria |
| §8 decision rules | §4 |
| §9 bugs | §5 |
| §1.2 privacy | §6 |

---

## 9. Checklist

- [ ] Pages and standard confirmed in `project.yaml`
- [ ] Engine installed (`qa-audit doctor` without failures on the chosen browser)
- [ ] Automated part run on every declared page
- [ ] axe's incomplete results and the engine's other checks reviewed in the manual part
- [ ] Manual part done on the main flows (at least keyboard, focus and zoom)
- [ ] Each failure with a WCAG criterion and level
- [ ] One defect per distinct problem
