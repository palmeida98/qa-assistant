---
name: audit-accessibility
description: Audits the accessibility (WCAG 2.1 AA) of the current workspace's pages - the automated part with the qa-audit engine (axe-core, keyboard, reflow) and the guided manual part - and maps each failure to the WCAG criterion and level.
---
1. Read technique-accessibility.md (<qa>/docs/) and, in the current workspace, the accessibility: section of
   project.yaml (standard, browser, login, pages, audit_error_pages) and context.md. <qa> is the root of
   qa-assistant: usually ../../../qa-assistant; in the demo, ../.. . If the section is empty, propose the pages
   from the main flows and ask.
2. Confirm that the URLs are from the TEST environment (technique-browser.md section 2) and that the login's
   variables exist in .env (only that they exist; never show values).
3. The qa-audit engine must be installed (technique-accessibility.md section 7): if the script exits with 2
   saying "qa-audit engine not installed", run `qa-audit doctor` (from <qa>) and show what is missing.
   The browser must start outside the Bash sandbox.
4. Automated part, from <qa>, with the ABSOLUTE path of the workspace:
   python scripts/audit_a11y.py <workspace> --output runs/a11y_<YYYYMMDD>_<nn>.json
   - exit 3 or "NOT AUDITED"/"redirected": the page is Blocked (environment), never Pass; a failed login
     is not repeated (shared account);
   - WCAG violations -> Fail of the page's case, with rules, criteria and level in the actual_result;
   - best practices -> observations (never Fail); incomplete -> manual review (step 5);
   - other checks (keyboard trap 2.1.2, focus not visible 2.4.7, reflow 1.4.10) -> leads to confirm in
     step 5, never Fail on their own;
   - "WARNING: ... 'chrome' treated as 'chromium'": propose updating accessibility.browser in project.yaml.
5. Guided manual part (technique-accessibility.md section 3), with the Playwright MCP or the browser: at
   least keyboard (2.1.1, 2.1.2), focus order and visibility (2.4.3, 2.4.7), page title (2.4.2),
   200% zoom (1.4.4) and reflow at 320 px (1.4.10), the axe incomplete results and the engine's other
   checks. With no browser available, the manual cases stay Not run, with the reason.
6. Run log in the format of methodology section 7.1; one defect per distinct problem (rule + cause),
   with the criterion and the level in the title (e.g. "[2.4.2 A] ..."), proposed with /log-bug and only
   created once approved. The suggested_severity (from axe's impact) is only a suggestion.
7. The JSON report stays in the workspace (it may have HTML excerpts with project data); it never goes
   into the qa-assistant repository. Continue to /checkpoint.
