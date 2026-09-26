# Session context — demo-saucedemo
Updated: 2026-09-24 13:30 (user decisions on the delegation and TC-LOGIN-006)

## Current state
Workbook with 38 cases (7 Login, 5 Products, 6 Checkout, 10 API, 7 Accessibility, 3 Mobile). Run: 26/38 (UI: 7 Pass, 1 Fail, 10 not run; API: 9 Pass, 1 Fail; Accessibility: 4 Pass, 1 Fail, 2 not run; Mobile: 3 Pass). 3 open defects (BUG-1, BUG-2, BUG-3). No cases in Question. Workbook summary checked against the direct count: it matches.

## Execution point (last batch, last case)
Batches applied, in order: `runs/batch_20260924_api01.json` (API), `batch_20260924_a11y01.json` (Accessibility), `batch_20260924_mob01.json` (Mobile), `batch_20260924_ui01.json` (UI, plan 2.3) and `batch_20260924_ui02.json` (TC-LOGIN-006, reclassified). Last case: TC-LOGIN-006. Still to run in the UI: TC-LOGIN-004, 005, 007, TC-PROD-003, 004, TC-CHK-002…006. Not run in Accessibility: TC-A11Y-005 (contrast of the incomplete results) and TC-A11Y-007 (keyboard), which need the browser (Playwright MCP).

## Work done
- UI cases approved by the user (23.09.2026): 7 Login, 5 Products, 6 Checkout.
- Onboarding (2.1) and Playwright MCP registered (2.2, user confirmation pending).
- Plan 3.1 (24.09.2026): API area (10 cases); API batch 01 run with `/test-api` and `scripts/api_request.py` (redacted evidence; no secret in the files).
- Plan 3.2 (24.09.2026): Accessibility area (7 cases); automated audit with `scripts/audit_a11y.py` (axe-core 4.13 in the installed Chrome) on login, products, cart and checkout-information: 0 WCAG 2.1 A/AA violations; missing best practices (no `<h1>`, content outside landmarks) as an observation; 1 incomplete contrast result on Products (→ TC-A11Y-005). TC-A11Y-006 Fail → BUG-2 ([2.4.2 A] every page with the title "Swag Labs"). Report: `runs/a11y_20260924_01.json`.
- Plan 3.3 (24.09.2026): Mobile area (3 cases) with Maestro flows in `flows/`, run with `scripts/run_mobile.py` in Maestro Web (beta, Chrome): 3 Pass. No device/emulator on this machine: the native app was not tested. Report: `runs/mobile_20260924_01.json`.
- Plan 2.3 (24.09.2026): UI batch with the 8 approved cases, grouped by role (no session → locked_out_user → standard_user → problem_user). Run with the Playwright library in Chrome, driven by the agent, because the Playwright MCP was not available in the session (environment difference recorded, technique-browser.md §1). 6 Pass, TC-PROD-005 Fail → BUG-3 (problem_user: the 6 products with the same image), TC-LOGIN-006 in Question (resolved later: Pass). The 1st observation of problem_user came back empty (the script did not wait for the list); repeated with an explicit wait before classifying. State restored: the product added in TC-CHK-001 was removed.
- Plan 2.5 (24.09.2026): TC-LOGIN-001…003 promoted to Playwright specs in `e2e/`.
- Workbook rebuildable with `rebuild_workbook.py` (builder + batches in execution order).
- Resources created and cleaned up: test booking 2501 (firstname `QA-AUTO-20260924-api01`) created in TC-API-003 and deleted in TC-API-010 (next GET = 404). Nothing left to clean up.
- Plan 8.11 (26.09.2026): the demo translated to English (cases, sheets, statuses, batches); the workbook rebuilt from the batches.

## Decisions made (and who made them)
- Practice site: saucedemo.com; workbook with the site's own cases in the format of the golden's fixture (user, 23.09.2026).
- Bug format `BUG-{n}` with the columns of the Defects sheet; resolved bugs: remove from the active view; evidence: text (user, 23.09.2026).
- Active modules: functional-web, exploratory, api, accessibility (user, 23.09.2026); mobile (user, 24.09.2026).
- Defect statuses, severity and priority, `Active Defects` view: see `project.yaml` (user, 23.09.2026).
- "Run" = every case outside `Not run` (N/A and Blocked count); Summary formula aligned (user, 24.09.2026).
- Approved by the user on 24.09.2026, what had been done by delegation:
  - API target: restful-booker (public practice API, published documentation); the 10 cases TC-API-001…010, including TC-API-009 with the HTTP standard (RFC 9110) as the source, because the documentation is silent about errors;
  - the 7 cases TC-A11Y-001…007 and the 3 cases TC-MOB-001…003 with their flows;
  - BUG-1 (Medium/Medium), BUG-2 (Low/Low) and BUG-3 (Medium/Medium), with the proposed severities and priorities;
  - `.env` with the demo accounts published by the sites themselves (local only).
- TC-LOGIN-006: showing the login form with the right error counts as Pass, even if the URL stays /inventory.html; expected result rewritten in `build_workbook.py` (user, 24.09.2026).
- Everything in English, the demo included (user, 26.09.2026; plan 8.11).

## Open questions
- The expected results of the UI cases not run yet have not been confirmed in the browser.

## Known problems / blockers
- Playwright MCP registered in `.mcp.json` (2.2), waiting for approval: open `claude` in this folder, approve the server and confirm with `/mcp`.
- BUG-1 (API): POST /booking without required fields answers 500 instead of 4xx. It is a defect of the practice API (we do not control the fix); it stays open as an example of the bug flow.

## Pending work
- **25.09.2026: `--secrets .env` added to `.mcp.json` (technique-browser.md §3).** Validated the same day in a real workspace (not in the demo): the login fields became `<secret>VARIABLE_NAME</secret>`, never the value. Still to repeat here, in the demo: open `claude` in this folder, approve `playwright` again (args changed), `/mcp` connected; login with `standard_user` writing only the variable NAMES (`DEMO_SAUCE_STANDARD_USER`, `DEMO_SAUCE_PASSWORD`) and confirm the response shows `<secret>…</secret>` and not the value. Then `/plan-batch` + `/run-batch` with the 10 UI cases not run.
- 2.2: confirm `/mcp` with playwright connected and a login with `standard_user` through the MCP.
- Next UI batch (10 cases not run), preferably with the Playwright MCP after closing 2.2.
- Manual accessibility: TC-A11Y-005 and TC-A11Y-007.
- Note: the next defect will be BUG-4.

## Validation state
UI: 8/18, API: 10/10, Accessibility: 5/7 and Mobile: 3/3 run and applied; cross-check clean, recalculation OK, summary vs direct count matches.
