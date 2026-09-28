# QA Assistant

References:
- @docs/methodology.md — universal methodology
- docs/technique-browser.md — browser automation technique (read when running browser batches; `/run-batch`, `/project-onboarding`)
- docs/technique-excel.md — Excel workbook automation technique (read when touching the adapter or applying batches; `/checkpoint`, `/log-bug`)
- docs/technique-api.md — API testing technique (read when the `api` module is active; `/test-api`)
- docs/technique-accessibility.md — WCAG 2.1 AA accessibility technique (read when the `accessibility` module is active; `/audit-accessibility`)
- docs/technique-mobile.md — mobile technique with Maestro (read when the `mobile` module is active; `/test-mobile`)
- docs/heuristics.md — anonymised failure patterns for error guessing (methodology §17; read when generating cases)
- docs/development-plan.md — the framework's roadmap (read in development sessions)
- docs/qa-audit.md — `qa-audit` automated audit engine (read when working on `src/qa_audit`); user guide: docs/qa-audit-guide.md

## Fixed rules
- Never write client data (URLs, accounts, names, credentials) in this repository.
- Follow the hierarchy of sources of truth of the methodology (section 1.3).
- A project workspace always lives outside this repository, in qa-workspaces/.
- Before any change to the framework, follow the validation process (methodology section 15).
- Everything in this repository is in English (plan 8.11); a project's own names (sheets, columns, statuses) are never translated.

## Available commands
/project-onboarding · /analyze-feature · /generate-cases · /plan-batch
/run-batch · /log-bug · /checkpoint · /end-session · /dashboard
/test-api · /audit-accessibility · /test-mobile

## Scripts (from the root of qa-assistant)
- `python scripts/validate_project.py <workspace>` — validates `project.yaml` (environments, modules, sections, URLs inside the environments, Excel, dashboard; warns if `.env` and the like are not in `.gitignore`); 0 ok · 1 problems
- `python scripts/migrate_workspace.py <workspace> [--apply]` — converts a workspace from before the translation (plan 8.11) to the English names and keys; dry run without `--apply`; 0 converted or nothing to do · 1 changes to make · 2 refused
- `python -m adapters.excel.inspect <copy.xlsx>` — proposes the `excel:` section (onboarding)
- `python -m adapters.excel.apply <workspace> <batch.json>` — applies a batch to the workbook (0 ok · 2 cross-check · 3 recalculation · 4 invalid batch · 5 summary does not match)
- `python scripts/api_request.py <workspace> <METHOD> <path> …` — one HTTP request with redacted evidence (0 ok · 1 different code · 2 usage · 3 no response)
- `python scripts/audit_a11y.py <workspace> [--output …]` — accessibility of the pages of `project.yaml` through the `qa-audit` engine (axe-core, keyboard, reflow; 0 ok · 2 configuration/engine · 3 page not audited); needs the engine installed (`pip install -e .`, `qa-audit doctor`)
- `python scripts/run_mobile.py <workspace> [flows…] [--output …]` — runs the Maestro flows and links them to the cases; each flow is a path or the case ID (0 ok · 2 configuration or missing flow · 3 no target)
- `python scripts/device.py <workspace> [--serial S] check|dump|tap|type|send|shot|link …` — drives an Android device/emulator through `adb` for the conversational mode (screen text with bounds, tap by text, Unicode typing through Maestro, send and wait for the answer to settle, screenshots to `evidence/<batch>/`, deep links never printed); only with `mobile.app_id` in the foreground (0 ok · 2 usage/configuration · 3 no target, app not in the foreground or timeout · 4 element not found)
- `python scripts/build_dashboard.py <workspace> [--open]` — builds (and opens) the workspace's `dashboard.html`; it is what `/dashboard` runs
- `python scripts/sync_skills.py [--apply]` — compares (and copies) `.claude/skills/` with `~/.claude/skills/`, the copy the workspaces use; run with `--apply` after changing a skill (0 in sync · 1 differences); also removes the old skill names
- `qa-audit <url> [--type accessibility,security,performance,functional,visual] [--sitemap] [--browser chromium,firefox,webkit|all] [--allow-submit] [--update-baseline]`; `qa-audit baselines [--delete HOST] [--browser …]`; `qa-audit login <url> --config FILE` (saves a session for `auth.method: storage-state`) and `qa-audit sessions [--delete HOST]`; `qa-audit check --cdp URL --page-url REGEX --label NAME` (audits the page an N2 spec has open; helper `integrations/playwright/qa-audit.ts`) — automated audit of any site (`pip install -e .` first); `qa-audit doctor` checks the environment (docs/qa-audit.md). Engine integration tests: `python -m unittest discover -s tests/integration` (browsers must be able to start: outside the Bash sandbox)
- `python scripts/update_axe.py <version>` — updates the axe-core bundled with `qa-audit` (npm integrity checked)
- Tests: `python -m unittest discover -s tests` and `python scripts/golden_check.py` (needs LibreOffice); a workspace's real golden: `python scripts/golden_check.py --dir <workspace>/golden/<batch>` (methodology §15)

## Environment
Personal repository. Each company's workspaces live in ../qa-workspaces/<company>/<project>/.
