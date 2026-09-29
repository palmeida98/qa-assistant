# Development plan — qa-assistant

> Source of truth of the framework's roadmap. Version 1.1 · 2026-09-26 (in English since plan 8.11).
>
> Universal: no name of a real client, company, environment or project enters here. The state of each real pilot lives in the `context.md` of its workspace (`qa-workspaces/<company>/<project>/`), never in this file.
>
> Names: this plan uses the current names of files, skills, scripts and keys, also in the history of tasks done before plan 8.11 (when they were in Portuguese); the old names are listed in 8.11. Commit hashes point to the code as it was at the time.

---

## How to use this plan in Claude Code

1. Open Claude Code at the root of the repository (`cd $HOME\qa-assistant` and then `claude`).
2. Ask for one task at a time, by number: *"Read docs/development-plan.md and do task 1.1."*
3. Before writing code, Claude Code describes what it will do and waits for confirmation.
4. A task is only `[x]` when **every** acceptance criterion is met and checked (not just "it ran").
5. One commit per task, with the message `<phase>.<task>: <summary>` (e.g. `1.1: skills analyze-feature, generate-cases, plan-batch, checkpoint`). After the commit, add the hash next to the task.
6. The privacy guards (detect-secrets + denylist) are never worked around. If a commit is blocked, the content is fixed — never the guard.
7. From task 1.5 on, any change in `adapters/` or `docs/technique-excel.md` must pass the golden test before the commit (methodology §15).
8. Example and test data are always fictional.

---

## Summary state

| Phase | Goal | State |
|---|---|---|
| 0 | Base: repository, universal documents, guards, templates | Done |
| 1 | Complete the core: missing skills, universal Excel adapter, golden test | Done |
| 2 | Browser automation with the Playwright MCP, public demo, pilot | Done (pilot 2.4 on a public practice site, 25.09.2026) |
| 3 | New technique modules: API, accessibility, mobile | Done |
| 4 | The framework's own CI | Done |
| 5 | Interface: progress dashboard per workspace | Done |
| 6 | Portfolio: README, demo, decision on the repository's visibility | Done (the repository stays private by the user's decision, 24.09.2026) |
| 7 | Closing: CI on Linux, pilot, real golden, protected history, fixed versions, reproducible demo, scope decisions, v1.0 | Done: **v1.0** (25.09.2026) |
| 8 | `qa-audit`: automated audit engine alongside the workspace layer (`docs/qa-audit.md`); and the translation (8.11) | 8.0–8.10 done (8.10 login merged 26.09.2026; `/audit-accessibility` on `qa-audit` and `tools/a11y/` retired, 26.09.2026); 8.12 saved sessions, 8.13 sitemap discovery and 8.14 several engines in one run, 8.15 `qa-audit check` and 8.16 fixes from the first real use done (26.09.2026); 8.17 (focus visibility decided by pixels) done (26.09.2026); 8.11 (everything in English) done (27.09.2026) |
| 9 | Conversational apps on a device and portable workspaces: drive an Android device/emulator from the framework, and clone a project's workspace on any computer and keep testing | Done (28.09.2026) |
| 10 | Jira (Xray) and Azure DevOps adapters: results, bugs, importing and exporting cases, with approval and without duplicates (revises 7.7) | In progress: 10.0 (specification), 10.1 (common base) and 10.2 (Jira + Xray) done (28.09.2026); 10.3 (Azure DevOps) done (29.09.2026) |

Recommended order: 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 2.x → 3.x → 4 → 5 → 6. The real pilot (2.4) can move forward in parallel, in its workspace, at any time after 2.2.

---

## Phase 0 — Base (done)

- [x] Private personal repository `qa-assistant` on GitHub.
- [x] `docs/methodology.md`, `docs/technique-browser.md`, `docs/technique-excel.md`.
- [x] Root `CLAUDE.md` with references and fixed rules.
- [x] `.gitignore` and privacy guards in pre-commit: `detect-secrets` + `scripts/check_denylist.py` (personal denylist in `~/.qa-denylist.txt`, outside the repository). Tested both ways.
- [x] Workspace templates: `docs/context-template.md`, `docs/project.yaml`.
- [x] Skills: `project-onboarding`, `run-batch`, `log-bug`, `end-session` (in `.claude/skills/` and a global copy in `~/.claude/skills/`).
- [x] Flow validated in a real pilot: onboarding, import of an existing workbook and scripts, migration of the recalculation to headless LibreOffice, full sweep of errors without changing the official file.

Lessons of the pilot already incorporated in the framework: never recalculate in a synced/mounted folder; the recalculation mechanism is not portable between environments; the status vocabulary is per project; never claim "validated" without comparing numbers.

---

## Phase 1 — Complete the core

### 1.1 Missing skills — `4e2da2a`
`CLAUDE.md` lists 8 commands, but only 4 skills exist. Create the missing ones in `.claude/skills/<name>/SKILL.md`:

- `analyze-feature` — analyses a feature per role/profile and produces the list of behaviours to test (methodology §5).
- `generate-cases` — generates test cases from the analysis, with the techniques of §5 (equivalence partitioning, boundary values, decision tables, state transitions, negatives), in the format of the project's tracker (read from `project.yaml`).
- `plan-batch` — groups cases by area + feature + platform + role + flow to minimise logins and context switches (§6).
- `checkpoint` — saves the run log, updates `context.md` in the format of §12.2 and makes a local commit in the workspace.

Acceptance criteria:
- [x] `dir .claude\skills` shows 8 folders, each with `SKILL.md` and a `name` + `description` frontmatter.
- [x] Each skill cites the methodology section it implements and contains nothing specific to a project.
- [x] Inside `claude`, typing `/` shows the 8 skills with the `(project)` label. *(confirmed by the user, 24.09.2026)*
- [x] Copy updated in `~/.claude/skills/`.
- [x] The commit passes both guards.

Note: `run_log` added to the template `docs/project.yaml` (path of the run log used by `checkpoint`; by default `runs/`, never `history/`).

### 1.2 Universal Excel adapter — `158d310`
Create `adapters/excel/base.py`, generalising the pattern of `technique-excel.md` §1: load keeping formulas, apply a batch, regenerate the defects view, cross-check, save only if clean (otherwise `.REVIEWED.xlsx`), recalculate safely.

- All layout information (file, prefix → sheet map, column names, valid case and defect statuses, "closed" defect statuses) comes from a configuration, never hardcoded.
- `recalc_safe()` with two selectable mechanisms: headless LibreOffice (default) and Excel through COM (optional, Windows only). Always on a copy in a local temporary folder.
- Detection of a batch applied again (`technique-excel.md` §5 requires that applying the same batch again duplicates neither notes nor defects).

Acceptance criteria:
- [x] No sheet, column or status name in the code (checkable with grep).
- [x] Works with a fictional configuration (used in 1.4).
- [x] Applying the same batch twice does not change the result the second time.
- [x] `docs/technique-excel.md` updated to point to the adapter.

Notes:
- Also validated by equivalence with a real `qa_lib.py` (copy of the workbook, synthetic batch): 0 differences in values, formulas and styles. Intentional differences: the adapter's control sheet and the extra "related case does not exist" check in the cross-check.
- The only sheet name in the code: the adapter's own control sheet (by default; configurable).
- The `excel-com` mechanism is implemented but **not validated** (no Excel on the development machine).
- Out of scope: export for other teams (`technique-excel.md` §6) and the "archive" rule for resolved bugs.

### 1.3 Excel configuration in `project.yaml` — `259fbfc` (2nd criterion closed at the demo's onboarding, `5afda66`)
Add to the template `docs/project.yaml` an `excel:` section with the fields the adapter needs. Update the `project-onboarding` skill to fill in this section **by inspecting the real headers of the workbook** (never guessing).

Acceptance criteria:
- [x] Template with comments explaining each field.
- [x] `project-onboarding` asks/inspects and fills in `excel:`. *(checked at the demo's interactive onboarding, 2.1)*
- [x] The adapter reads this section without further configuration.

Notes:
- `adapters/excel/inspect.py` only reads the workbook and proposes the section; what is not safe stays `CONFIRM: …` (and makes `load()` fail if it is forgotten).
- On a copy of a real workbook it got right the case sheets and prefixes, ID columns, case statuses (from the data validation), defects sheet, ID format and 6 of the 10 columns of the view; it left to confirm the defects' status column (several validation lists), actual result/notes and the ambiguous columns of the view.
- `ExcelAdapter.from_project(workspace)`; optional blocks left blank count as absent.

### 1.4 Golden test — `c0ec70b`
Create `tests/golden/`:

- `build_fixture.py` — builds a small fictional workbook (e.g. 3 case sheets, 12 cases, 3 defects, a summary sheet with formulas).
- `batch.json` — a fictional batch that changes statuses and creates a defect.
- `expected.json` — the expected state after applying the batch (statuses, defects, summary totals).
- `scripts/golden_check.py` — builds the fixture in a temporary folder, applies the batch through the adapter, recalculates and compares with `expected.json`.

Acceptance criteria:
- [x] `python scripts/golden_check.py` passes.
- [x] Changing a value in `expected.json` on purpose makes the test fail with a clear message (and then restoring it).
- [x] No real data: the names of cases, defects and sheets are generic.

Notes:
- `expected.json` written by hand from the rules (not generated by the adapter); `{today}` is replaced by the day's date. `--show` prints the result for review, but never rewrites the expected file.
- Also checked: turning off the detection of a repeated batch in the adapter makes the golden fail; without LibreOffice the golden exits with code 2 and a clear message (simulated). Phase 4's CI needs to install LibreOffice.
- The golden with a real historical batch through `QA_GOLDEN_DIR` (methodology §15) did not exist yet; a note was added to the methodology.

### 1.5 Golden in pre-commit — `8feece0`
A local hook in `.pre-commit-config.yaml` that runs `golden_check.py` when files change in `adapters/`, `tests/golden/` or `docs/technique-excel.md`.

Acceptance criteria:
- [x] A commit that only touches `docs/methodology.md` does not run the golden.
- [x] A commit that breaks the adapter is blocked.

Notes:
- The hook also runs when `scripts/golden_check.py` itself changes (user decision, 23.09.2026).
- Validated on a temporary branch, deleted at the end: only `methodology.md` → `Skipped`; broken adapter → `Failed` and commit blocked (HEAD unchanged); a valid change in `tests/golden/` → `Passed`.
- Without LibreOffice the golden exits with 2 and blocks the commit: install LibreOffice, never `--no-verify`.

---

## Phase 2 — Browser automation (Playwright MCP)

### 2.1 Demo workspace — `5afda66`
Create `examples/demo/` inside the repository: a complete workspace (`CLAUDE.md`, `project.yaml`, `context.md`, a workbook built like the fixture) pointed at a public test practice site (e.g. saucedemo.com or the-internet.herokuapp.com). No real credentials: only the demo accounts published by the site itself, and even those in the local `.env`.

It serves to develop and validate the framework without touching client data, and it is the base of the portfolio (Phase 6).

Acceptance criteria:
- [x] The demo's onboarding done with `/project-onboarding`.
- [x] Nothing in the demo passes the denylist nor detect-secrets.

Notes:
- Site: saucedemo.com (demo accounts per role). Workbook with 18 cases of the site's own, approved by the user, in the format of the golden's fixture (instead of the fixture as it is); built by `examples/demo/build_workbook.py`.
- The onboarding revealed two gaps, fixed: fixed paths to qa-assistant in the skill (the demo lives inside the repository) and the inspection did not recognise a defects sheet that was still empty.
- Credentials only in the local `.env` (ignored by git); the demo password does not appear in any file of the repository.
- Expected results not yet confirmed in the browser: confirm in 2.3.

### 2.2 Playwright MCP at the onboarding — `0024c37`
Add to the `project-onboarding` skill the registration of the Playwright MCP in the workspace:
`claude mcp add playwright --scope project -- npx @playwright/mcp@latest`

Acceptance criteria:
- [x] `.mcp.json` created in the workspace (not in the framework's repository, except in the demo).
- [x] `/mcp` inside `claude` shows `playwright` connected. *(confirmed by the user, 24.09.2026)*
- [x] A login on the demo site works through the MCP. *(confirmed by the user, 24.09.2026)*

Notes:
- On Windows the command is `claude mcp add playwright --scope project -- cmd /c npx @playwright/mcp@latest`; without `cmd /c` Claude Code does not launch `npx`. The skill picks the command by operating system.
- `@latest` kept for now; fix the version when moving to N2 (2.5). Version checked on this machine: 0.0.82.
- `claude mcp list` shows the server as `Pending approval` until it is approved at the first start of `claude` in the workspace.

### 2.3 First batch in the demo — `b9cfc1b`
Run 5–10 cases of the demo with `/plan-batch` and `/run-batch`, apply through the adapter, `/checkpoint`.

Acceptance criteria:
- [x] Clean cross-check (and summary vs direct count matching).
- [x] A fictional defect logged with `/log-bug` in the format of `project.yaml`: BUG-3 (problem_user, images), as `new_defect` in the batch.
- [x] The demo's `context.md` updated.

Notes:
- The 8 approved cases, grouped by role (no session → locked_out_user → standard_user → problem_user): 6 Pass, 1 Fail (TC-PROD-005 → BUG-3), 1 Question (TC-LOGIN-006: the login appears with the right error, but the URL stays `/inventory.html`; the expected result says "goes back to the login page"). The Question stays out of the batch (the adapter refuses it) and is in `context.md` as a question.
- **Environment difference**: run with the Playwright library in Chrome, driven by the agent with temporary scripts (deleted), because the Playwright MCP was not available in the session (2.2 to be confirmed). Provided for in `technique-browser.md` §1.
- Lesson confirmed: the 1st observation of problem_user came back empty (the script did not wait for the product list); it was not classified on it, it was repeated with an explicit wait (`technique-browser.md` §4.3).
- Decision by delegation: BUG-3 written without individual approval (the user asked to go ahead without asking); severity and priority Medium. Approved later by the user, with the other decisions by delegation (24.09.2026).
- TC-LOGIN-006: the Question was answered by the user (showing the login with the right error counts as Pass, even with the URL /inventory.html); expected result rewritten and the case applied in the batch `batch_20260924_ui02`.

### 2.4 Pilot in a real project
*State (23.09.2026): prepared in the project's workspace (Playwright MCP registered, a batch of 16 read-only cases proposed, screenshots outside git); execution waiting for 2.2/2.3 and the credentials (2.2 and 2.3 done on 24.09.2026: only the credentials were missing). Detail in that workspace's `context.md`.*

In the real project's workspace (outside this repository): `.env` filled in locally, Playwright MCP registered, a batch of 10–20 cases already known, comparison with the previous results. The whole record stays in that workspace's `context.md`.

Here only "done" is marked and, if they come up, **generic** lessons for the methodology or technique (no names).

- [x] Pilot done and compared.
- [x] Generic lessons incorporated (if any).

Notes: pilot done on 25.09.2026 **on a public practice site** (SauceDemo), in a workspace outside the repository (`qa-workspaces/pratica/saucedemo/`, created from the demo, local git): the user no longer had access to company projects and decided the pilot runs on a scenario created for it. A batch of 16 cases (10 not run + 6 retests with a previous result) run by a `claude` opened in the workspace with the Playwright MCP, applied at `/checkpoint`: 15 Pass, 1 Fail (TC-PROD-005 -> BUG-3, the same as before); the 6 retests gave the same status. The 1st run was not applied: with the MCP's persistent profile the clicks stopped reaching the page (diagnosis with three variants; solved with `--isolated`, 37 real clicks and 0 synthetic ones in the 2nd). Lessons: see 7.2.

### 2.5 Promotion N1 → N2 — `19f7fc7`
From a demo case that passed through the agent (N1), generate a deterministic Playwright test (`.spec.ts`) as in `technique-browser.md` §13.

Acceptance criteria:
- [x] `npx playwright test` runs and passes locally (3 tests in `examples/demo/e2e/specs/login.spec.ts`, on the installed Chrome). An expected result changed on purpose makes the test fail.
- [x] The locators follow the priority of `technique-browser.md` §4.2: button by role + name (level 1); the fields have no label, only a placeholder (level 3 — an accessibility observation); visible texts (level 4). No CSS nor XPath.

Notes:
- TC-LOGIN-001, 002 and 003 promoted: two consecutive N1 Pass results (batch 2.3 and a rerun on 24.09.2026), deterministic data, stable expected result (§13). The case ID goes in the test's title; `@smoke` tag.
- `@playwright/test` 1.63.0 fixed with a lock; credentials only through environment variables (`playwright.config.ts` reads the demo's `.env` when it exists).

---

## Phase 3 — New technique modules

Each module: a document in `docs/` (the same structure as the existing ones: scope, execution levels, preparation, verification, evidence, security, link to the methodology, checklist) + a skill + an example in the demo.

### 3.1 API — `docs/technique-api.md` + `/test-api` — `5b11b05`
Contracts and schemas, status codes, authentication and authorisation per role, negative cases, idempotency, pagination, limits. Tools: Playwright's `request`, curl, Postman collections when the project already has them. Evidence: request/response with the secrets redacted.

Acceptance criteria (3.1):
- [x] Project-agnostic document (examples only with the Demo Shop), passes the guards.
- [x] Skill `.claude/skills/test-api` with a valid frontmatter (`test_skills` test), global copy in sync.
- [x] `/` shows `/test-api` with the `(project)` label. *(confirmed by the user, 24.09.2026)*
- [x] Example run in the demo: API area with 10 cases (restful-booker, expected results from the published documentation), batch `runs/batch_20260924_api01.json` applied to the workbook: 9 Pass, 1 Fail → BUG-1 (500 to a POST without required fields); cross-check clean, recalculation OK, summary vs direct count matches.
- [x] Reference in `CLAUDE.md` (without `@`: only read with the `api` module active, so as not to weigh on every session).

Notes:
- `scripts/api_request.py`: one request with redacted evidence (authentication headers, JSON keys with token/password/secret/api_key, and the literal value of any `.env` variable or session secret). Tokens saved with `--save-secret` in `.api-session.env` and used as `{{TOKEN}}`: the agent chains requests without ever seeing the token. Checked: no secret in the batch's 14 evidence files.
- In Git Bash (Windows) an argument `/booking` arrives converted into `C:/Program Files/Git/booking` (MSYS path conversion). The script accepts paths without the leading slash and explains the error if it gets a converted path; the skill says to write them without the slash.
- Decisions by delegation (the user asked to go ahead without asking): the restful-booker target, the 10 API cases and BUG-1 (priority Medium). Recorded in the demo's `context.md` for review.
- 9 tests of `api_request.py` with a local HTTP server (no network).

### 3.2 Accessibility — `docs/technique-accessibility.md` + `/audit-accessibility` — `afbdbe7`
WCAG 2.1 AA. An automated part with axe-core through Playwright; a guided manual part (keyboard, focus order, screen reader, contrast, 200% zoom, reflow). Each failure mapped to the corresponding WCAG criterion and to the level.

Acceptance criteria (3.2):
- [x] Project-agnostic document, passes the guards.
- [x] Skill `.claude/skills/audit-accessibility` with a valid frontmatter, global copy in sync.
- [x] `/` shows `/audit-accessibility` with the `(project)` label. *(confirmed by the user, 24.09.2026)*
- [x] Example run in the demo: Accessibility area (7 cases). Automated audit of 4 pages (login, products, cart, checkout-information; 3 with login): 0 WCAG 2.1 A/AA violations, best practices and 1 incomplete contrast result recorded; TC-A11Y-006 (2.4.2 A, equal titles) Fail → BUG-2. The manual TC-A11Y-005 (contrast) and TC-A11Y-007 (keyboard) stay Not run: they need the browser through the agent (Playwright MCP).
- [x] Reference in `CLAUDE.md` (without `@`, like the API).

Notes:
- `tools/a11y/` (Node, fixed versions: Playwright 1.63.0, @axe-core/playwright 4.13.0; `npm ci --prefix tools/a11y`) only opened the pages and ran axe; it used the installed Chrome (`channel: chrome`), without downloading browsers. `scripts/audit_a11y.py` read `project.yaml`, passed the login's credentials through environment variables (never arguments) and mapped axe's tags to criteria and levels; best practices → observations, incomplete → manual review. (Replaced by the `qa-audit` engine in 8.10.)
- A redirected page (e.g. a failed login) is treated as not audited: axe on the login page would give a false Pass.
- The demo's workbook became rebuildable (`examples/demo/rebuild_workbook.py`: builder + batches of `runs/` in execution order, by each batch's 1st `ts`; by name, the BUG-n would swap).
- The demo's `.env`: saucedemo accounts filled in locally by delegation, confirmed in the site's code.
- 10 tests of `audit_a11y.py` without a browser (fictional raw axe result). The Node part did not run in CI.

### 3.3 Mobile — `docs/technique-mobile.md` + `/test-mobile` — `908e922`
Maestro flows in YAML, emulator vs. real device, gestures, permissions, deep links, offline/background states, iOS/Android differences.

Acceptance criteria (3.3):
- [x] Project-agnostic document, passes the guards.
- [x] Skill `.claude/skills/test-mobile` with a valid frontmatter, global copy in sync.
- [x] `/` shows `/test-mobile` with the `(project)` label. *(confirmed by the user, 24.09.2026)*
- [x] Example run in the demo: Mobile area, 3 Maestro flows (login, locked out user, cart) run with `scripts/run_mobile.py`: 3 Pass. **In Maestro Web (beta)**, not in a native app: this machine has no adb, emulator nor device. Checked that a flow with the wrong assertion gives Fail.
- [x] Reference in `CLAUDE.md` (without `@`).

Notes:
- `scripts/run_mobile.py`: one flow per case (the ID is the prefix of the file name); credentials from `.env` in `MAESTRO_<VAR>` variables (Maestro gives them to the flows as `${MAESTRO_<VAR>}`), never `-e` on the command line; reads Maestro's JUnit and redacts the messages. With no target (Android without a device, iOS without a simulator) the cases are Blocked and Maestro does not even run.
- To do, when there is an emulator: an example on a native app (e.g. the Sauce Labs demo app).
- 8 tests of `run_mobile.py` with Maestro and adb simulated (the JUnit format is Maestro 2.10's real one).

### 3.4 Modules in `project.yaml` — `abfddc4`
A closed list of valid values in `modules:` (`functional-web`, `api`, `accessibility`, `mobile`, `exploratory`) and `project-onboarding` asking which to activate.

Acceptance criteria (3.4):
- [x] Closed list in methodology §4.1 (module → technique → skill → section of `project.yaml`) and commented in the template.
- [x] `project-onboarding` shows the list, asks which to activate, fills in the sections of the active modules and ends with `scripts/validate_project.py` without problems.
- [x] `scripts/validate_project.py` refuses modules outside the list and active modules without their section; the demo passes (0 problems, 0 warnings). 7 tests.

Notes:
- `technique-browser.md` listed as modules `smoke`, `roles`, `visual`, `cross-browser` and `forms`, which do not exist: they became variants inside the modules, not values of `modules:`. Old references to `modules/*.md` fixed.
- The common criteria of Phase 3 (document, skill, demo example, `CLAUDE.md`) were checked in each task (3.1–3.3); the manual check of `/` was confirmed by the user (24.09.2026).

---

## Phase 4 — The framework's CI

### 4.1 GitHub Actions in `qa-assistant` — `1392eda`, `fa749eb`
On each push: `golden_check.py`, `detect-secrets`, Python lint (ruff).

The personal denylist does not run in CI (it lives only on your computer, on purpose); it stays the barrier in the local pre-commit.

Notes:
- `.github/workflows/ci.yml`: jobs `lint` (ruff 0.16.8, fixed), `secrets` (only the pre-commit's `detect-secrets` hook, same version and baseline) and `golden` (installs `libreoffice-calc-nogui`, ~35 s). Python 3.12 in CI; `ruff.toml` with `DTZ` off (intentional local dates).
- Clean lint before turning the CI on: 32 warnings fixed, among them B023 in the adapter's batch application (the defect's row became a parameter of `put`; it was not a bug, but it was fragile). Golden unchanged.
- On Linux ruff requires the execute bit on files with a shebang (`git update-index --chmod=+x`); on Windows it is not noticed.
- Criterion 2 validated on a temporary branch deleted at the end: the broken commit was created through GitHub's API (like an edit on the site, without local hooks), because pre-commit blocks it locally and the guard is not worked around. Result: job `golden` failed with `apply.new_defects[0]: expected 'BUG-99', obtained 'BUG-4'`.
- Private repository: the badge (in `CLAUDE.md`, until there was a README in 6.1) only shows to who has access.
- Runners fixed at `ubuntu-24.04` (`3f72fcd`), instead of `ubuntu-latest`, which moves to Ubuntu 26 from 19.10.2026 (GitHub's warning in the jobs). The change of version stays an explicit decision.

### 4.2 The demo's regression — `034d1c4`
Run the demo's `.spec.ts` (2.5) in CI against the public site.

Notes:
- Job `demo-e2e`: Playwright's Chromium, `npm ci` and `npx playwright test` in `examples/demo/e2e`. The demo accounts come from the repository's `DEMO_SAUCE_*` secrets; without them the job issues a warning and does not run the tests (it does not fail).
- The 1st run (24.09.2026) was before the secrets existed and only gave the warning. The job was run again after creating them: `3 passed` against the public site.

Acceptance criteria (of 4.1):
- [x] Green badge on GitHub.
- [x] A commit that breaks the golden shows as failed in CI.

Note: the regression of real projects runs in the workspace or in the CI of that project's company, never in this repository.

---

## Phase 5 — Interface

### 5.1 Dashboard per workspace — `e6cc04f`
`scripts/build_dashboard.py <workspace-path>` reads the workbook (through the adapter) and `context.md` and builds `dashboard.html` inside the workspace: progress per area and status, open defects by severity, last checkpoint and next steps. The HTML built stays in the workspace (in `.gitignore`), never in the repository.

Notes:
- Only reads the workbook (`load()`, never saves). A new adapter method, `open_defects()`, reused by `regenerate_active_defects()`; golden unchanged.
- The meaning of the statuses comes from the optional `dashboard:` section of `project.yaml` (`pending | ok | fail | blocked | na`), validated against `excel.cases.statuses`; without it, the 1st status counts as pending. `project-onboarding` asks for it and puts `dashboard.html` in the workspace's `.gitignore`.
- From `context.md` it shows the `Updated:` line and the sections execution point, pending work, questions and blockers (minimal Markdown converted in the script itself). The numbers also go in a JSON block (`#numbers`) in the HTML.
- Only `tracker: excel` for now; another tracker exits with code 2 and a clear message.
- Validation (24.09.2026): in the demo, 0/18 run, equal to `progress_summary()` (no runs yet). On the golden's fixture with the batch applied and recalculated, the dashboard's numbers match the Summary recalculated by LibreOffice (per area and total), `expected.json` and the order of the active view; no external `src`/`href` nor `<link>`/`@import`. The check was manual (a script in the scratchpad); if the dashboard changes a lot, it is worth making it a CI test.

### 5.2 Skill `/dashboard` — `a016109`
Builds and opens the current workspace's dashboard.

Acceptance criteria (of 5.2):
- [x] `.claude/skills/dashboard/SKILL.md` with a valid frontmatter, global copy in `~/.claude/skills/`, referred to in `CLAUDE.md` and in methodology §3.
- [x] Following the skill's steps in the demo, the dashboard is built and opens in the browser (`build_dashboard.py --open`); with a tracker other than Excel it exits with 2 without building anything.
- [x] Inside `claude`, typing `/` shows `/dashboard` with the `(project)` label. *(confirmed by the user, 24.09.2026)*

Notes:
- `--open` uses the `webbrowser` module with the local file's URI: the same on Windows, macOS and Linux, without shell quoting. The script now prints the state of the workbook's summary, which the skill summarises.
- The skill forbids publishing or sending the dashboard: in a real project it has client data (methodology §1.2).
- Found while testing: the frontmatter of `end-session` had invalid YAML since phase 0 (`: ` in the description without quotes) and Claude Code showed the 1st line of the body as the description. Fixed; `tests/test_skills.py` validates the frontmatter of every skill and that each one is in `CLAUDE.md`.

Acceptance criteria (of 5.1):
- [x] The demo's dashboard built and consistent with the workbook (totals match).
- [x] Works offline, a single HTML file.

---

## Phase 6 — Portfolio

### 6.1 README — `00278ee`
Layered architecture, privacy boundary, how to start (onboarding of a new workspace), demo, available modules.

Notes: `README.md` with the four layers, the privacy boundary and the guards, the workflow (mermaid diagram), the onboarding of a new project, the modules table, the demo, tests/CI and requirements. The CI badge moved from `CLAUDE.md` to the README (as planned in 4.1).

### 6.2 The repository's visibility
Decide whether the repository becomes public. Before that, required:
- [x] Sweep **the whole history** with detect-secrets and with the denylist (not only the current state), because the first commits were made before the guards existed.
- [x] Review `examples/demo/` looking for any non-fictional data.
- [x] The user's decision on the visibility: **it stays private for now** (user, 24.09.2026). To review when the portfolio goes to public use.

Checks (24.09.2026, 52 commits, 219 file versions extracted from every branch):
- detect-secrets on every version, **redone in UTF-8 mode** (the 1st sweep, in cp1252, skipped files with certain characters — see the log of 24.09): 1 finding, the line with the variable name of the demo's booker password in the old version of the demo's `project.yaml`, which is the name of a variable and not a secret (apart from the integrity hashes of the `package-lock.json` files, excluded). Confirmed that the sweep reads the files, with a planted secret (caught).
- Denylist (text, names and content of Office files) on every version: 0 findings. Confirmed with a real term of the list planted (blocked).
- No `.env` nor `.api-session.env` ever in the history; the demo passwords do not appear in any version; no local paths nor the machine's user name (only the repository's name on GitHub, in the badge).
- `examples/demo/`: only public practice sites (saucedemo.com, restful-booker), demo accounts published by the sites themselves referred to by the variable's name, test data with the `QA-AUTO-` prefix, bugs of the practice sites.

---

## Phase 7 — Closing the framework

Output of the general review 2 (24.09.2026, `a57ed19`): what is missing to call qa-assistant done (v1.0). Recommended order: 7.1 → 7.2 → 7.3; 7.4, 7.5 and 7.6 in parallel; 7.7 decided right away, because it changes what the others have to cover.

### 7.1 Green CI after the general review 2 — `1efa40f`
The fix of the recalculation timeout (process group, outside Windows) can only be checked on Linux; the development machine had no active Docker nor WSL with a distribution.
- [x] CI of `a57ed19` green in the 5 jobs (`tests/test_recalc.py` runs in the `tests` job, on Ubuntu).

Notes:
- Run 36053764682 (push of `a57ed19` + `1efa40f`, 24.09.2026): demo-e2e, lint, secrets, tests and golden green. In the `tests` job (Ubuntu) the 117 tests ran; the tests of the timeout killing the process tree (an effective timeout with a hung grandchild, through the process group), of accents arriving intact through a pipe and of the `.gitignore` in a workspace with git passed.

### 7.2 Pilot in a real project (it is 2.4)
- [x] The pilot workspace's `.env` filled in (demo accounts published by the site).
- [x] `python scripts/validate_project.py <workspace>` without problems **with the new rules** (URLs inside the environments, `.gitignore`); whatever shows up is fixed in the workspace, never here.
- [x] A batch of 16 cases run and compared with the previous results (closes 2.4).
- [x] Generic lessons incorporated, anonymised (methodology §1.4, §17).
- [x] Reassess the `executor-web` subagent (7.7): if the pilot's batches filled the main context, create it; otherwise, the generic subagent stays.
- [x] Confirm the protection of `history/` (7.4): editing a file in `history/` is refused in a `claude` opened in the workspace.
- [x] For 7.3: **before** applying the batch at `/checkpoint`, copy the workbook to `<workspace>/golden/<batch>/before.xlsx`; after the checkpoint is reviewed, copy the workbook to `after.xlsx`, the batch (with its original name) and write `golden.yaml` with `now` = the `applied_at` of the control sheet for that batch.

Notes (7.2, 25.09.2026): pilot done on 25.09.2026 **on a public practice site** (SauceDemo), in a workspace outside the repository (`qa-workspaces/pratica/saucedemo/`, created from the demo, local git): the user no longer had access to company projects and decided the pilot runs on a scenario created for it. A batch of 16 cases (10 not run + 6 retests with a previous result) run by a `claude` opened in the workspace with the Playwright MCP, applied at `/checkpoint`: 15 Pass, 1 Fail (TC-PROD-005 -> BUG-3, the same as before); the 6 retests gave the same status. The 1st run was not applied: with the MCP's persistent profile the clicks stopped reaching the page (diagnosis with three variants; solved with `--isolated`, 37 real clicks and 0 synthetic ones in the 2nd).
- Lessons incorporated: (1) Playwright MCP with `--secrets .env` (the assistant writes the variable's name and never sees the value; the password does not appear in the transcript) and `--isolated` (clean profile; the persistent one lost clicks), in the browser technique §1 and §3, in `/project-onboarding`, in the demo and in a guard test; (2) `validate_project` warns when the workspace's `.mcp.json` has no fixed version, `--secrets .env` or `--isolated` (the `@latest` of a real workspace passed without a warning); (3) methodology §15: the golden's `label` is the notes' label, not a description (the real golden caught the mistake); (4) a `commit-msg` guard with the denylist (a client name got through in a commit message; fixed).
- `executor-web`: not created. The batch of 16 cases ran in a session of its own (~100 turns, 3 to 6 min) without touching the main session's context; the generic subagent / a session in the workspace are enough.
- `history/`: in a `claude` session in the workspace, with Edit allowed and edits accepted automatically, editing a file in `history/` was refused (same hash).

### 7.3 Golden with a real historical batch (`QA_GOLDEN_DIR`, methodology §15) — mechanism in `e74c281`
- [x] `golden_check.py` accepts a workspace folder (`QA_GOLDEN_DIR` or `--dir`) with the starting workbook, batch and expected result, which stay **only in the workspace**.

Notes (1st criterion, brought forward without the pilot by the user's decision, 24.09.2026):
- Folder `<workspace>/golden/<batch>/` with `before.xlsx`, the `batch_*.json` with its original name, `after.xlsx` (output saved and reviewed) and `golden.yaml` (`now` required; `label` and `reported_by` optional). A folder with several of these runs them all. The `project.yaml` is the workspace's (the nearest folder above), with the **current** configuration.
- Applies through the same `apply` as `/checkpoint` (with `--now`, 7.6) to a copy of `before.xlsx` in a temporary folder, compares every cell of every sheet (formula and calculated value) with `after.xlsx` and confirms that applying again changes nothing. The expected result is never generated by the script. Codes: 0 equal · 1 differences (listed per cell) · 2 incomplete folder, no `project.yaml` above or no LibreOffice.
- The fictional one always runs first: with `QA_GOLDEN_DIR` in the environment, the `pre-commit` hook does not lose it.
- `tests/test_golden_real.py` (fictional data, temporary workspace): passes with the right `after`, fails pointing at the changed cell, detects another instant in the dates written, runs several folders and the environment variable, refuses incomplete folders. In CI, in the `golden` job.
- Specified in methodology §15.
- [x] The pilot's real golden passing (the "not implemented yet" note left methodology §15 with the mechanism, in `e74c281`). 25.09.2026: the pilot workspace's golden of its batch 02: `REAL GOLDEN OK` (cells equal to `after.xlsx`, applying again has no effect).

### 7.4 `history/` really protected (methodology §4 step 4, §13) — `3563f24`
Until then the methodology promised the protection through Claude Code permissions, but nothing created it.
- [x] `/project-onboarding` creates the workspace's `.claude/settings.json` with a `deny` of writing and editing in `history/**`.
- [x] `validate_project` warns when the workspace does not have it; test.

Notes:
- Template `docs/workspace/settings.json` with `"deny": ["Edit(/history/**)"]`. Syntax confirmed in Claude Code's permissions documentation: the leading `/` anchors to the workspace's folder (project settings); an `Edit` rule covers every tool that edits files and the recognised Bash commands (`sed`, `tee`, `>`); a `Write(...)` rule with a path is ignored. It does not cover scripts that open files on their own (methodology §13).
- `/project-onboarding` copies the template, or adds the rule to a `settings.json` that already exists; the demo has the same file in `examples/demo/.claude/`.
- `validate_project`: a warning without the rule (it accepts `/history/**`, `./history/**` or `history/**`, in `settings.json` or `settings.local.json`); 2 new tests, and the demo stays with 0 problems and 0 warnings.
- To confirm in the pilot (7.2): open `claude` in the workspace and see editing a file in `history/` refused.

### 7.5 Fixed versions — `0a61bc6`
- [x] `@playwright/mcp` with a fixed version in the demo's `.mcp.json` and in the command of `/project-onboarding` (until then `@latest`).
- [x] `requirements.txt` with exact versions of `openpyxl` and `pyyaml` (a silent update can change the golden); updating is a decision with the golden passing.

Notes:
- `@playwright/mcp@0.0.82` (the most recent on 24.09.2026; `npx @playwright/mcp@0.0.82 --version` confirmed). Rule in the browser technique §1.
- `openpyxl==3.1.5`, `et-xmlfile==2.0.0` (a dependency of openpyxl) and `pyyaml==6.0.3`: the same that CI already installed, so the golden does not change.
- `tests/test_guards.py`: Python dependencies only with `==`, and a single numeric version of `@playwright/mcp` in the demo and in the skill (a forgotten `@latest` makes the test fail).

### 7.6 Reproducible demo — `ced85fc`
The rebuild put the day's dates (new defects, the view's summary, the control sheet, the file's properties) and the `.xlsx` always showed as changed in git.
- [x] Each batch's date fixed by the batch's 1st `ts` in the rebuild (an option in `apply`, without changing the default behaviour).
- [x] A CI test: the cells of the versioned workbook are equal to those of the builder + batches.

Notes:
- `apply --now YYYY-MM-DDTHH:MM:SS` (attribute `now` of `ExcelAdapter`): the default label, the report date of new defects, the date of the active view's summary and `applied_at` use that instant; without the option, the clock (golden unchanged). Specified in the Excel technique §1.
- `rebuild_workbook.py [workspace]` passes each batch's 1st `ts` and accepts another folder (the test rebuilds in a temporary folder, never in the demo).
- `tests/test_demo_reproducible.py`: `Now` (without LibreOffice: the same instant gives the same cells; the dates written are the instant's) and `Demo` (rebuild = versioned workbook, cell by cell, in every sheet; in the `golden` job). `Demo` failed against the previous workbook (dates of the day of the rebuild) and passes with the regenerated one; two rebuilds in a row give the same cells.
- The `.xlsx` still changes byte by byte at each rebuild (file properties saved by openpyxl and by LibreOffice); the criterion is the cells'.

### 7.7 Scope decisions (user) — `3ffbb62`
Recommendations applied by the user's decision (24.09.2026).
- [x] `qa_core` / CLI `qa`: **not built**; the adapters and the scripts are the engine. Methodology §1.1 and §2 rewritten (the internal model is the common vocabulary, not a separate database).
- [x] Subagent `executor-web`: **not built for now**; Claude Code's generic subagent is used with the instructions of `/run-batch` (browser technique §11). Reassessed in the pilot (7.2).
- [x] Trackers `jira` and `azure-devops`: **manual logging** until a real project needs an adapter. New methodology §4.2 (table per tracker), a comment in the template, step 7a in `/checkpoint`, and `validate_project` warns when the tracker has no adapter (new test). *Revised on 28.09.2026 by the user's decision: phase 10 builds both adapters; manual logging stays until each one is done.*

### 7.8 Tidying up (optional) — `799e068`
- [x] `scripts/common.py` with the reading of `project.yaml` and `.env` (until then `run_mobile` and `audit_a11y` imported from `api_request`), without changing behaviour.

Notes:
- `common.py`: `read_project`, `read_env`, `read_workspace_env` and the `Redactor` (with the redaction constants it uses). Used by `api_request`, `audit_a11y`, `run_mobile`, `build_dashboard` and `validate_project`; `api_request` keeps exporting the same names. -90 lines in the scripts.
- Same behaviour: the existing tests pass without changes (only the mobile test imports the `Redactor` from `common`); a new `tests/test_common.py` (reading `.env` and `project.yaml`, the scripts' error messages kept, a single implementation). The demo run again: validation, dashboard, API, accessibility and mobile the same.
- Seen in passing, without changing (out of scope): `run_mobile.py` with a flow that is not a file (e.g. `TC-MOB-001` instead of the path) gave `Blocked` "the flow did not run" instead of a usage error (code 2). Fixed next, separately (see the change log).

### 7.9 Version 1.0
User decision (25.09.2026): v1.0 is the **workspace layer** (methodology, skills, adapters, the pilot's flow), validated in a real project; the `qa-audit` engine has its own version (`pyproject.toml`; 0.1.0 on 25.09.2026, 0.2.0 on 26.09.2026) and is not part of v1.0's criterion.
- [x] 7.1 to 7.6 done and the decisions of 7.7 written in the documents.
- [x] README with the final state; tag `v1.0`; "Summary state" table with every phase of the workspace layer done (phase 8 is `qa-audit`, with its own version).

---

## Phase 8 — `qa-audit` engine

User decisions (25.09.2026): the automated engine **lives alongside** the workspace layer (nothing of it is removed); the engine, its CLI, reports, code and documentation are in **English**; the repository stays **private and proprietary** (only the owner has access); first slice 8.1 → 8.4. This revises the 7.7 decision "no `qa_core` / CLI `qa`": the engine is a separate package (`src/qa_audit`), the workspace scripts keep working as before. Specification: `docs/qa-audit.md` (written first, methodology §15). Technical risks and their mitigations: `docs/qa-audit.md` §5–§9, §13, §14.

### 8.0 Specification and decisions
- [x] `docs/qa-audit.md`; this phase; methodology §1.1 updated.

### 8.1 Package, CLI and core
- [x] `pyproject.toml` (exact versions; proprietary, "Private :: Do Not Upload"), `src/qa_audit`, `qa-audit version` / `doctor` / `--help`, configuration with precedence and the no-credentials rule, `Finding` with the evidence rule, run folder with `.gitignore` and `--cleanup`, module registry (built-in + entry points).
- [x] `LICENSE` (proprietary), `CONTRIBUTING.md`, `CHANGELOG.md`; `.gitignore` for output, evidence and secrets; guard test for tracked `qa-output/` paths.
- [x] Unit tests in `tests/unit/`, run by the existing `unittest` command, pre-commit and CI.

Notes (8.1): 49 unit tests (138 → 187 in the whole suite). The pre-commit `tests` hook now also runs on `src/`, `pyproject.toml`, `.gitignore` and `LICENSE`. An existing guard (the console test) caught `scripts/update_axe.py` without the UTF-8 console fix before the commit.

### 8.2 Browser layer and discovery
- [x] Engines chromium/firefox/webkit, viewport, device, video, trace; same-origin GET-only crawl with exclusions, `robots.txt`, delay and identifiable user agent.
- [x] Local test site `tests/fixtures/site/` with trap links and a POST form; integration tests: no trap requested, nothing posted, no request outside `127.0.0.1` (recording proxy, browser traffic included).
- [x] CI job with the three engines on Linux (job `qa-audit`, with `QA_AUDIT_REQUIRE_ENGINES=1` so a missing engine fails instead of skipping). Run 36116680629 (push of `7f61f5e`, 25.09.2026): all 6 jobs green; `doctor` in CI: chromium 153, firefox 155, webkit 26.6; 37 integration tests, none skipped.

Notes (8.2): locally (Windows) Chromium and Firefox pass all integration tests; WebKit cannot start here (Windows application control blocks its `libEGL.dll`: `PrintDeps` reports "An Application Control policy has blocked this file"), skipped with that reason. The recording proxy saw only `127.0.0.1:<port>` for both engines, browser traffic included.

### 8.3 Reports
- [x] Terminal summary, JSON, CSV, Markdown and self-contained HTML (inline CSS, CSP `default-src 'none'`, audited content escaped); tests.

Notes (8.3): two risks found while building the reports, both covered by tests: audited content in the HTML report (escaped + CSP, a fixture page with a hostile title and attribute) and formula injection in the CSV (cells starting with `= + - @` are prefixed with `'`). Images use no lazy loading: inside closed `<details>` they never loaded.

### 8.4 Accessibility module
- [x] axe-core 4.13.0 shipped with manifest and `scripts/update_axe.py`; violations / incomplete / manual checklist; keyboard and reflow checks; disclaimer.
- [x] Integration: planted defects found, clean page without problems, on every available engine.
- [x] `tools/a11y/` retired: `scripts/audit_a11y.py` and `/audit-accessibility` run on this engine (see 8.10, 26.09.2026).

Notes (8.4): the npm integrity of the bundled axe-core is the same as in `tools/a11y/package-lock.json` (and a test kept both on the same version). The Firefox integration tests found three things the first design got wrong, all fixed and covered: axe evaluated from Playwright's realm cannot see page functions (X-ray wrappers), so it runs in the page realm through a `<script>` element with `evaluate` only as the CSP fallback; this Firefox build throws from `crypto.getRandomValues` (a substitute is installed only when the native call fails, and noted in the report); and Firefox headless keeps the focus on the last element at the end of a page, which the first keyboard check reported as a trap on a clean page (false positive; now a trap needs tabbable elements after the stuck one). 8.2, 8.3 and 8.4 are one commit: their integration tests exercise the three together.

### 8.5 Security module (passive)
- [x] Spec (`docs/qa-audit.md` §16), module with per-check aggregation, planted defects over HTTP and HTTPS, unit tests of every check.

Notes (8.5): checks are pure functions (`modules/security/checks.py`, 15 unit tests); the module reads headers, cookie names/flags (values dropped), page HTML and TLS details, plus one `http://` request to check the redirect. The HTTPS fixture uses a self-signed certificate valid for two days (planted `tls-cert-expiring`), generated with `openssl` (Git for Windows ships one). Found on the way: this machine's Firefox cannot load **any** HTTPS page (`NS_ERROR_FAILURE`, same broken cryptography as 8.4) while `doctor` reported it ready: `doctor` now tests each browser's cryptography, and an HTTPS start page failing that way explains it. Locally Firefox HTTPS and WebKit are skipped with the reason; CI runs them.

### 8.6 Performance module
- [x] Thresholds from a cited source (web.dev: TTFB 0.8/1.8 s, FCP 1.8/3.0 s, LCP 2.5/4.0 s, CLS 0.1/0.25, confirmed on the pages on 25.09.2026), configurable; the other metrics measured only; metrics table in every report.

Notes (8.6): planted defects `slow.html` (the fixture server waits 2 s: TTFB poor) and `shift.html` (banner inserted after load: CLS). What each engine measures was checked before writing the spec: Chromium everything; Firefox LCP but not CLS (reported `n/a in firefox`, never estimated). Found while designing: the accessibility module presses Tab and resizes the viewport, which would count as layout shifts; modules now run on each page by `order` (performance 0, security 10, accessibility 90), and a test checks the reflow page shows CLS 0. Bad thresholds are a usage error before the audit (`validate_config`).

### 8.7 Functional module
- [x] Broken links and resources, JavaScript errors, redirect chains, form hygiene; `--allow-submit` only for forms listed in the configuration, with a test that nothing is posted without the flag and only the listed form with it.

Notes (8.7): links not visited by the crawl are checked with `HEAD` (never the excluded or robots-disallowed ones: the fixture server records the method of every request and the tests check it). Pages answering 4xx/5xx are no longer audited as content (the server's 404 page produced accessibility noise that is not about the site). Found while testing: once `functional` became a valid module, a *unit* test (usage errors) reached the runner and launched a browser that tried to resolve `shop.example` on the network; the CLI unit tests now block the runner. The first CI run of 8.7 failed on two things that did not show locally: the recording proxy caught **Firefox contacting `aus5.mozilla.org`** about a minute into the longer audit (application / add-on / media plugin updates; now disabled by preferences, and the network test keeps the browser open over a minute so background timers fire), and on Linux a cached response came without `Content-Type` (now read from `document.contentType`).

### 8.8 Visual module
- [x] Baselines per page, viewport and engine outside the repository, comparison with diff images, responsive checks; lifecycle test (created → unchanged → planted change found only on its page → updated).

Notes (8.8): comparing against a baseline means keeping screenshots of the audited site between runs, the one exception to "nothing persists": they live in `~/.qa-audit/baselines` (never in the output folder or a repository, `.gitignore *` at the root), `--cleanup` leaves them on purpose, the report says where they are, and `qa-audit baselines --delete <host>` removes them. A visual difference is `needs-human`, never a bug. Rendering was deterministic between runs on Chromium and Firefox. The tests always use a temporary baselines folder. Module order: visual (70) resizes the viewport, so it runs after the readers and before accessibility, whose keyboard walk would leave focus rings in the screenshots. The first full run found a discovery bug that every module had: a link redirecting to a page already audited (`/r1` → `about.html`) audited that page twice (visual compared it with the baseline created a moment before); a page whose final URL was already audited is now recorded as "same page as …" and not audited again. On the same afternoon WebKit and Firefox's cryptography started working on this machine with Smart App Control still on (cloud reputation); `doctor` reports it and the spec says to check again rather than assume.

### 8.9 Documentation, install and example
- [x] Install scripts, user guide (`docs/qa-audit-guide.md`, the 11 points asked for: what it is, install, run, new project, modules, browsers, results, new modules, new checks, Git, client data), fictional example configuration, README section; clean clone → install → audit of the local site in CI (job `install`).

Notes (8.9): `install.ps1` was tested on a fresh local clone: its first version failed only inside the script (a one-element array splatted into a native command); fixed. Writing the guide, a claim about WebKit was checked before being written and turned out wrong: WebKit 26 measures LCP (the spec and the performance README said Chromium and Firefox only); corrected in the three places. The example configuration is loaded by a test with every module's validation, so it cannot fall behind the code.

### 8.10 Login (`form` and `manual`) and explicit pages
User decision (25.09.2026): login is the next engine task; methods `form` and `manual` now, `storage-state` later. Specification: `docs/qa-audit.md` §20 and §5 (`crawl.pages`). Kept on the branch `feat/login` until the pilot (user decision, 25.09.2026) and merged into `main` on 26.09.2026, with the branch's CI green on the three engines.
- [x] `auth:` with `form` and `manual`, one attempt, success check, session-expiry detection, credentials redacted everywhere, trace refused with auth; `crawl.pages` / `follow_links`.
- [x] Fixture login flow (form, protected area, session that expires, wrong password) and tests: exactly one POST, credentials in no output file, a wrong password stops at the first attempt, expiry detected; `manual` tested with a login page that completes itself (the "person").
- [x] Afterwards (separate task): `/audit-accessibility` on `qa-audit`, and `tools/a11y/` retired (26.09.2026).

Notes (8.10, accessibility on the engine): user decisions (26.09.2026): `scripts/audit_a11y.py` stays as a thin
workspace script (same command line, `project.yaml` section and workspace report; the engine opens the browser and
runs axe), so the skill, the demo and the workbook texts keep working; `accessibility.browser` takes the engine's
browsers and the old `chrome` becomes `chromium` with a warning. The script runs one engine audit per (login,
origin) group, only the declared pages (`crawl.pages`, `follow_links: false`), with the credentials in the engine
process's environment only; a login succeeds by default when the URL leaves the login page (`login.success_url` /
`success_selector` override it). Two engine changes came out of the comparison with the old script (methodology
§15, on the practice workspace with the public practice site): (1) the WCAG criteria alone could not tell a
WCAG 2.2 rule from a 2.1 one, so the axe findings' `references` now carry each tag's version and level
(`WCAG 2.2 AA`), and the script keeps the old split (a 2.2-only rule is a best practice under WCAG 2.1);
(2) the practice site answers **HTTP 404** to the direct links of its logged-in pages (a single-page application)
and renders them anyway: the old tool ignored the status, the engine refuses error pages. New opt-in
`crawl.audit_error_pages` (listed pages answering 4xx only; noted in the report; user decision), set from
`accessibility.audit_error_pages`, with integration tests on a fixture route. Result of the comparison,
per page: the same WCAG violations (none), best practices and incompletes, except `color-contrast` incomplete on the
products page (3 elements before, 2 now), stable over two runs of each and explained by the viewport: the engine's
1440×900 against Playwright's default 1280×720 (at 1280×720 the engine also finds 3); the engine's viewport was
kept. New in the report: the engine's keyboard, focus and reflow checks, as leads for the manual part.

Notes (8.10): the login runs in the crawl's own browser context before the first page; the login page is excluded from the crawl, and a page that lands on it afterwards stops the crawl as "session ended" (the fixture's `account/expire.html` clears the cookie). Credentials are redacted in findings, evidence, page data, notes and errors after the run; the test greps every written file (json, csv, md, html, log) for both values and requires the `«QA_FIXTURE_USER»` placeholder to appear (the settings page shows the user name, so redaction is proven, not assumed). `success_url` matching the login page itself is a configuration error (a failed login would look like success). `manual` forces a visible browser; its test uses a fixture login page that fills and submits itself after a second, and in CI it runs under `xvfb-run`. Locally the auth tests passed on Chromium, Firefox and WebKit.

### 8.11 Everything in English (workspace layer included)
User decisions (26.09.2026, replacing "not now" of 25.09.2026): the whole repository is in English, documentation
included; the names change too (commands, files, scripts, configuration keys); existing workspaces are converted by
a migration script (no permanent dual vocabulary in the code); the default states and the demo are in English.
The `qa-audit` engine is already in English and only changes where it names workspace files.

Names (old -> new):
- Docs: `metodologia-core.md` -> `methodology.md`; `tecnica-browser.md`, `tecnica-excel.md`, `tecnica-api.md`,
  `tecnica-acessibilidade.md`, `tecnica-mobile.md` -> `technique-browser.md`, `technique-excel.md`,
  `technique-api.md`, `technique-accessibility.md`, `technique-mobile.md`; `heuristicas.md` -> `heuristics.md`,
  `plano-desenvolvimento.md` -> `development-plan.md`, `contexto-template.md` -> `context-template.md`,
  `projeto.yaml` -> `project.yaml`.
- Skills: `/onboarding-projeto` -> `/project-onboarding`, `/analisar-funcionalidade` -> `/analyze-feature`,
  `/gerar-casos` -> `/generate-cases`, `/planear-lote` -> `/plan-batch`, `/executar-lote` -> `/run-batch`,
  `/registar-bug` -> `/log-bug`, `/checkpoint`, `/fim-sessao` -> `/end-session`, `/painel` -> `/dashboard`,
  `/testar-api` -> `/test-api`, `/auditar-acessibilidade` -> `/audit-accessibility`, `/testar-mobile` ->
  `/test-mobile`. The global copies with the old names are removed.
- Scripts: `validar_projeto.py` -> `validate_project.py`, `api_pedido.py` -> `api_request.py`, `auditar_a11y.py` ->
  `audit_a11y.py`, `testar_mobile.py` -> `run_mobile.py`, `gerar_painel.py` -> `build_dashboard.py`,
  `sincronizar_skills.py` -> `sync_skills.py`, `comum.py` -> `common.py`; `adapters.excel.aplicar` ->
  `adapters.excel.apply`, `adapters.excel.inspecionar` -> `adapters.excel.inspect`, `exemplo.yaml` -> `example.yaml`;
  demo `gerar_workbook.py` -> `build_workbook.py`, `reconstruir_workbook.py` -> `rebuild_workbook.py`.
- Workspace: `projeto.yaml` -> `project.yaml`, `contexto.md` -> `context.md`, `historico/` -> `history/` (moved
  unchanged, protection rule updated), new run logs `batch_*.json`.
- Keys: every key of `project.yaml` (e.g. `modulos` -> `modules`, `excel.casos.estados` -> `excel.cases.statuses`,
  `aba_resumo` -> `summary`), of a batch (`resultados` -> `results`, `estado` -> `status`, `resultado_obtido` ->
  `actual_result`, `defeito_novo` -> `new_defect`) and of `golden.yaml` (`agora` -> `now`, `etiqueta` -> `label`),
  and the values the framework defines (`remover` -> `remove`, `texto` -> `text`, module and dashboard types).
  Values that come from a project (sheet, column and state names, texts) are never translated.

Criteria:
- [x] `python scripts/migrate_workspace.py <workspace>` (dry run by default, `--apply` to write): converts
  `project.yaml` (keys, framework values, templates such as `{resultado}` -> `{result}`), the batches in the run
  log folder, `golden.yaml`, `CLAUDE.md` references and the settings file; renames `projeto.yaml`, `contexto.md`,
  `historico/` (contents byte for byte, checked by hash). It pins what an old workbook shows: the old note texts,
  the `_qa_lotes` control sheet and the reporter "QA automatizado" when the project had the defaults, so the next
  batch writes the same cells as before. Idempotent; refuses a workspace with uncommitted changes.
- [x] The adapter reads an existing control sheet with the old headers (workbook data, not configuration); the
  golden check converts an old batch and `golden.yaml` in memory, with the old defaults.
- [x] Docs, skills, scripts, adapter, tests, CI, README, CONTRIBUTING, CHANGELOG and this plan in English;
  messages and JSON outputs of the scripts in English. A test fails if Portuguese words come back into the
  framework files (list of common words, outside the migration's own mapping).
- [x] Demo in English (cases, sheets, states, batches, workbook rebuilt) and still reproducible; e2e specs pass.
- [x] Practice workspace migrated with the script; golden real still identical; `/` shows only the new skills.
- [x] Unit, integration (three engines) and golden tests pass; CI green (run 36305490201, 27.09.2026, after
  giving `scripts/migrate_workspace.py` its execute bit: the first run failed only in `lint`, ruff EXE001 on Linux).

Notes (8.11): the batch hash was the one design problem: the control sheet of a workbook keeps the hash of each batch
applied, so a batch converted to the new keys would have looked new (and the real golden's `after.xlsx` would not
match). A converted batch carries `translated_from`, the hash of its original content, which stays its identity
(`apply_batch(identity=…)`); the golden check converts an old batch in memory the same way. The practice workspace
was converted with the script (committing first its untracked `qa-audit.yaml`, so that it could run): 0 problems in
`validate_project`, the real golden cell for cell equal to the `after.xlsx` written by the old code, the converted
batches recognised as already applied, and the dashboard reading it. Its prose stays in Portuguese (project data,
outside this repository); the framework's names, the quoted keys and options and the standard headings of
`context.md` were converted. One mistake while doing it: a lost backslash in a replacement erased three headings of
that `context.md`; reverted in the workspace with `git revert` and covered by the migration's test. `run_mobile.py` no
longer assumes the `CT-` prefix in the flows' names (any case ID). The demo's e2e specs pass locally (3 login specs,
and the state audit through `auditState` with `QA_AUDIT_CDP_PORT`). The global skills were swapped with
`sync_skills.py --apply`, which now removes the old names; `/` lists only the new ones. Replacing this section's
old text removed a client's short name that the denylist did not have (it had the domain and the full name).

### 8.12 Saved login sessions (`auth.method: storage-state`)
User decision (26.09.2026): next engine task after 8.10, with the design agreed before the code. Specification:
`docs/qa-audit.md` §20.1.
- [x] `qa-audit login <url> --config FILE`: the `login_method` login (`manual` by default, or `form`), one attempt,
  success check, storage state saved (default `~/.qa-audit/sessions/<host>.json`, `.gitignore *`, owner-only on
  Linux and macOS); nothing audited.
- [x] Audit with `method: storage-state`: context from the saved state, nothing submitted; expired session (start
  URL on the login page) is exit 3 with the `qa-audit login` hint; the file's age in the report notes.
- [x] State file refused inside the output folder or a Git working tree; a missing file is exit 2 naming the
  command; cookie values (6+ characters) redacted as `«cookie:NAME»`; `qa-audit sessions [--delete HOST|all]`.
- [x] Tests: unit (validation, locations, redaction, list/delete, CLI) and integration on the fixture login (login
  saves with exactly one POST, the audit posts nothing, an expired session stops with the hint, a wrong password
  saves nothing, no cookie value in any written file).

Notes (8.12): the fixture's session cookie became long enough to be redacted (`valid-session-fixture`) and the
settings page carries it in the planted image's attributes, like the user name, so the redaction test proves
something appears redacted instead of passing because nothing was written. Linking the workspace's
`scripts/audit_a11y.py` to saved sessions is left out (a separate task, if a project needs it).

### 8.13 Sitemap discovery
User decision (26.09.2026): after 8.12, the smallest of the planned engine items; design agreed before the code.
Specification: `docs/qa-audit.md` §5.1.
- [x] `crawl.sitemap` / `--sitemap`, off by default: `Sitemap:` lines of `robots.txt` (relative ones resolved),
  else `/sitemap.xml`; same origin only, same context and user agent as `robots.txt`.
- [x] `urlset` and one level of `sitemapindex` (at most 10 files), 5 MB per file, 5000 URLs; `.gz` skipped;
  `<!DOCTYPE` / `<!ENTITY` refused before parsing (no new dependency).
- [x] Entries queued after the start URL and `crawl.pages`, at depth 1, through every discovery rule (exclusions,
  `robots.txt`, login page, origin, `max_pages`); status in `results.json` (`crawl.sitemap`), a note and the HTML
  report.
- [x] Tests: unit (parser, entity refusal, limits, origins, relative locations, failures never stop the audit) and
  integration (robots.txt → index → pages: the orphan page is audited; `/logout`, a robots-disallowed page and the
  external entries are never requested; off by default changes nothing; the recording-proxy test runs with it on).

Notes (8.13): the first status line dropped the problems when a sitemap had no page URLs (e.g. a nested index
ignored); the unit test for nested indexes caught it.

### 8.14 Several engines in one run (compatibility)
User decision (26.09.2026): after 8.13, design agreed before the code. Specification: `docs/qa-audit.md` §21.
- [x] `--browser chromium,firefox,webkit` / `all` (or `browser.engine` as a list): one complete audit per engine,
  one after the other, in one run folder with a sub-folder per engine; one engine is exactly as before.
- [x] Comparison (`core/compatibility.py`): a finding (module, check, page, element) in some engines only; a page
  visited, audited or answered differently; each one a `compatibility/engine-difference` finding, `needs-human`,
  `info`. Performance and visual baseline comparisons are not compared. `compatibility.json/.md/.html` at the
  root of the run folder.
- [x] An engine that cannot start is recorded and the others run; `form` logs in once per engine (noted);
  `manual` refused with several engines (exit 2, pointing to `storage-state`); exit code the worst of the runs,
  4 only when no engine starts, 3 when the target was reached nowhere.
- [x] Tests: unit (comparison, engine list, manual refused, combined exit codes with fake runs) and integration
  (a fixture page that adds an image without alt in Firefox only: exactly one difference across the three engines).

Notes (8.14): one change to the agreed design: performance findings are not compared at all (the design only left
out `n/a` metrics), since timings differ between engines and between runs and a threshold crossed in one engine is
noise. A full compatibility run of the fixture site (three engines) gave four differences: console messages on
two pages and mixed content blocked differently by Chromium are real engine behaviour; the `SameSite=None` one was
**not**, contrary to what was written here at first: it was the engine's false positive found and fixed in 8.16
(Firefox and WebKit report a cookie without SameSite as `None`). The keyboard and focus checks agreed in all three.

### 8.15 Auditing states in the middle of an N2 spec (`qa-audit check`)
User decision (26.09.2026): option (c) of the comparison (flows stay in the workspace's specs; the engine never
clicks), design agreed before the code. Specification: `docs/qa-audit.md` §22.
- [x] `qa-audit check --cdp URL --page-url REGEX --label NAME`: connects over CDP (loopback only) to the test's
  browser, audits the matching tab as it is (axe by default; keyboard and reflow opt-in, noted), never navigates,
  reloads, clicks or types, and disconnects leaving everything as it was; `check.json` / `check.md` in a run folder;
  `--redact-env`; exit 0 / 1 (`--fail-on`, default none) / 2 / 3.
- [x] Workspace helper `integrations/playwright/qa-audit.ts` (`auditState(page, testInfo, name, options)`):
  runs the check for the page's URL, attaches the report, fails the test only with `failOn`; demo configuration
  (`QA_AUDIT_CDP_PORT`, `workers: 1`) and an auditing spec of the checkout error state, run in the `demo-e2e` CI job.
- [x] Tests: unit (loopback only, checks, tab choice, exit codes, nothing listening) and integration (a dialog that
  opens only after a click: the defect is found only in the open state, no request reaches the site, the dialog stays
  open and the focus is untouched; keyboard opt-in noted and viewport restored; no matching tab is exit 3).

Notes (8.15): two changes to the agreed design: the helper takes the test's `testInfo` as a parameter
(`auditState(page, testInfo, …)`), because the file lives outside the demo's Node project and can only import
types from `@playwright/test`; and the instruction to copy the helper sits in `technique-browser.md` §13 (where N2
specs are created) instead of `/project-onboarding`, which never creates specs. Each call writes to its own
folder (a second call in the same test would otherwise attach the first report). The demo's spec audits the
checkout error state (no WCAG violation found there); it is not the promotion of a case to N2.

### 8.16 Fixes from the first real use
User decisions (26.09.2026): stop adding features and use the engine on the public practice site
(`--browser all`, login, `crawl.pages`, `audit_error_pages`, `--sitemap`), then fix what that shows.
- [x] `cookie-samesite-none-insecure` was a false positive in Firefox and WebKit: the security module read cookies
  from the browser's store, where those engines show a cookie without SameSite as `None`. The session now records,
  per cookie name, the SameSite declared in `Set-Cookie` headers (never the value); `SameSite=None` is reported only
  when declared, and a cookie without a declaration (or set by JavaScript) is `cookie-samesite-unset`
  (needs-human, info).
- [x] The SPA pages listed with `audit_error_pages` were reported as `broken-link` (problem, medium) "linked from"
  themselves: they are now `error-status-served` (warning, low), and a page linking to itself is not its own
  referrer.
- [x] `visual` is no longer part of `full` (user decision): it stored 36 screenshots of logged-in pages in the home
  folder without being asked for; it runs when named.
- [x] Guide: regular expressions in YAML in single quotes (a `\.` in double quotes is an invalid escape; it
  happened in this first use).
- [x] Tests reproduce each case: a script-set cookie (`about.html`) and the `Set-Cookie` without SameSite
  (`sec.html`) are `cookie-samesite-unset` in every engine and never `SameSite=None`; the SPA pages are
  `error-status-served` and not broken links; `full` without `visual`.

Notes (8.16): a second run on the practice site gave identical results in the three engines and no compatibility
difference (the first run had one: the false positive). Still open from that use, for a person to judge:
`focus-visible` (needs-human) on the four pages.

### 8.17 Focus visibility decided by pixels (what was left from the first real use)
Left from 8.16: `focus-visible` was `needs-human` / confidence low on the four pages in the three engines, because
the check only compares computed styles (outline, shadow, border, background, colour, underline) and cannot see a
focus style drawn another way. Checked on 26.09.2026 on the practice site's public login page (no login): after a
real Tab (`:focus-visible` true), the element's area with a 6 px margin gives **identical pixels** focused and
blurred for the three stops, in chromium, firefox and webkit. The site has no focus indicator. A person was only
needed because the engine did not look at the pixels.
- [x] The keyboard walk also compares a screenshot of the element's box plus a margin, focused vs blurred (caret
  hidden, after the focus transition settles). Identical pixels mean `focus-not-visible` (problem, medium, confidence
  high, WCAG 2.4.7) with both crops as evidence. Pixels that differ with no computed-style change pass. The style-only
  `needs-human` stays only where the pixel test cannot decide (element off screen, zero size, or the crop keeps
  changing on its own, e.g. an animation).
- [x] The walk never clicks or types. The page is left as it was (focus, scroll, viewport). The time added per page
  is measured and noted.
- [x] The finding lists every element or says "and N more". Before, `actual` named 5 of 6 without saying so.
  `data:` URIs in the evidence HTML are shortened (a base64 image filled the snippet of `#continue-shopping`).
- [x] Tests (local fixtures, no network): a button with `outline: none` and no other style is `focus-not-visible`;
  a focus style drawn only by `::after` or by a parent's `:focus-within` passes (before, it was needs-human); an
  animated element falls back to `needs-human`. Same result in the three engines.
- [x] A new run on the practice site (same `qa-audit.yaml`, `--browser all`) gives `focus-not-visible` on the four
  pages, identical in the three engines, and no compatibility difference.

Notes (8.17): only the stops with no computed-style change are compared by pixels (one screenshot right after Tab
for every stop, two more for those). On the practice site that was every stop: 1.1 s for 6 stops, 4.3 s for 23
(chromium). Screenshots use `animations: "disabled"` (a fading ring is compared at its end) and PNG bytes are
compared directly, so Pillow is not needed. The new run (26.09.2026) gave `focus-not-visible` (problem, confidence
high) on the four pages in the three engines, with no compatibility difference. The elements listed differ by
engine because Tab order differs: WebKit does not stop on links by default (3 stops on the cart page against 6), and
Firefox also stops on the sort `select`. In chromium, 1 of 23 stops on the products page passed. `/audit-accessibility`
(`scripts/audit_a11y.py`) shows the new check with the others (2.4.7). The walk also sets the focus and the
scroll back where they were: the note of `qa-audit check` now says so, and its test checks it.

---

## Phase 9 — Conversational apps on a device and portable workspaces

Why: a real mobile pilot (an LLM chat app, 27–28.09.2026) was run with helpers written in the session's scratchpad,
outside the framework: reading the screen through `adb`, tapping by text, typing accented text, sending a message and
waiting for the reply to settle, screenshots only with the app in the foreground. Maestro flows (§4 of the mobile
technique) did not fit: the app's answers are not deterministic, so a flow cannot assert them; the agent reads each
answer and classifies it. The emulator needed manual work (an ARM-only APK on an x86_64 image, a magic-link login, the
browser's first run). And the workspace was a local git repository with no remote: the project could not be continued
on another computer. This phase brings all of that into the framework. Everything stays universal: no app, account or
text of the pilot enters this repository (methodology §1.2); the examples use the Demo Shop.

Decisions of the user (28.09.2026): one git repository per project workspace, with a **private remote**; the
checkpoint commits locally and a push happens only with the user's approval; it must be possible to clone a workspace
on any computer and run its tests. All phases, in order.

### 9.0 Specification first (methodology §15) — done 28.09.2026
- [x] Methodology: §4 (onboarding) — the workspace is a git repository with a private remote, created at onboarding;
  §12 (continuity) — resuming on another computer: clone, `.env` from the user's own secret store, environment check,
  then read `context.md`; §13 unchanged (`history/` still protected by the workspace's settings, which are versioned).
- [x] `technique-mobile.md`: a new execution level **N1 · Device driver** (`scripts/device.py`) next to Maestro flows,
  and when to use each (deterministic UI → flow; generated content, e.g. an AI assistant's answers → conversational
  mode, the agent classifies); the conversational mode (one turn per step, wait for the reply to settle, text from the
  accessibility tree and a screenshot when the tree misses content — cards, multi-line bubbles); the privacy rule
  (screenshots and dumps only with the target app in the foreground); emulator preparation (§1) for ARM-only apps on
  x86_64 images and deep-link / magic-link logins.
- [x] Decisions to write down: app binaries (APK/IPA) are **not** committed (size limits, and they are the client's
  artefact) — `project.yaml` records where the build comes from and its SHA-256; evidence (text and screenshots) is
  committed; `.env` and saved sessions never.

### 9.1 `scripts/device.py` — the device driver — done 28.09.2026
- [x] Subcommands: `check` (target, OS, model, app version, foreground app), `dump` (visible text with bounds, one
  line per node), `tap` (by exact text first, then contains; or by coordinates), `type` (Unicode, through Maestro's
  `inputText` or an ADB keyboard; plain `adb input text` refuses non-ASCII), `send` (type, tap send, wait until the
  screen is stable, print only what is new after the message), `shot` (screenshot to `evidence/<batch>/`), `link`
  (open a deep link or a login callback with `am start`).
- [x] Privacy: `dump` and `shot` refuse (exit 3) when the foreground app is not the workspace's `mobile.app_id`; no
  credential ever on the command line; `link` never writes the URL (a one-time token) to a file or log.
- [x] Reads `project.yaml` (`mobile.app_id`, device serial) through `scripts/common.py`; exit codes documented
  (0 ok · 2 usage/configuration · 3 no target or app not in the foreground · 4 element not found).
- [x] Tests with a fake `adb` (no device): parsing of the uiautomator XML (entities, empty text with content-desc,
  zero-size nodes), exact-before-contains matching, the foreground guard, "stable screen" detection, `send` printing
  only the new text.

### 9.2 `scripts/setup_emulator.py` — a reproducible Android target — done 28.09.2026
- [x] Idempotent steps: check the SDK (`emulator`, `adb`), create the AVD if missing, boot and wait for
  `sys.boot_completed`, install the build given by path (checked against the SHA-256 in `project.yaml`), grant or
  revoke permissions listed in `project.yaml`, turn off the browser's first run.
- [x] Detects an ARM-only APK on an x86_64 image with native translation, and `extractNativeLibs=false`: places the
  APK's `lib/arm64-v8a/*.so` in the app's native library folder (root image), says so, and tells that it must be
  repeated after each reinstall. It never changes the APK.
- [x] Refuses production builds (the rule of §3 of the technique) and says what it cannot do (e.g. a Play Store image
  without root).
- [x] Tests with a fake SDK/`adb`: each step skipped when already done; the ARM-only detection from a fixture APK.

### 9.3 Skill `/test-mobile` with both modes — done 28.09.2026
- [x] The skill picks the mode per batch (flows or conversational) and says why; in conversational mode: one new
  conversation per theme when the case asks for it, a note per turn in the run log, screenshot when the dump misses
  content, the same classification (methodology §8, technique §6).
- [x] `/project-onboarding` asks for the mobile target and records it in `project.yaml` (`mobile.device`,
  `mobile.build`, `mobile.permissions`); `scripts/validate_project.py` checks the new keys.
- [x] `sync_skills.py --apply` run and the global copy in sync (apply by the parent session).

### 9.4 Portable workspace (git + private remote) — done 28.09.2026
- [x] `/project-onboarding` runs `git init` in the workspace, writes a `.gitignore` from a template in
  `docs/workspace/` (`.env`, `.api-session.env`, saved sessions, `dashboard.html`, app binaries, Office lock files) and
  a `.env.example` with the variable names from `project.yaml` (no values); it proposes the private remote
  (`gh repo create --private`) and creates it only with the user's approval.
- [x] `/checkpoint` keeps committing locally; it says how many commits are not pushed and pushes only when the user
  says so. Never `--force`.
- [x] `validate_project.py`: warns when there is no remote, when commits are not pushed, when `.env.example` misses a
  variable of `project.yaml`, and when a file over 50 MB is tracked; a problem (exit 1) when `.env` is tracked.
- [x] The privacy guard stays in `qa-assistant`; the workspace keeps client data by design, so its remote must be
  private — `validate_project.py` checks it with `gh repo view --json visibility` when `gh` is available.
- Note (where the spec was open): one helper, `scripts/workspace_git.py` (`init`, `status`, `remote`, `push`), shared
  by the skills and `validate_project.py`. `init` refuses a folder inside another repository (e.g. the demo inside
  `qa-assistant`), `.env` tracked, a `.gitignore` line that un-ignores `.env`, and a file over 50 MB in the initial
  commit; the template also ignores `.playwright-mcp/` and `.claude/settings.local.json`. `remote --create` without
  `--yes` only prints the command (exit 0). `push --i-confirm-private` overrides only an unconfirmed visibility, never
  a PUBLIC one; INTERNAL counts as not private (refused by `push`, a warning in `validate_project.py`). The remote,
  push, `.env.example` and 50 MB checks run only when the workspace is the top of its own repository; `.env` tracked
  is a problem in any repository.

### 9.5 Resume on another computer — done 28.09.2026
- [x] `docs/workspace/resume.md` (or a section of the methodology): install `qa-assistant` (clone, `pip install -e .`,
  `sync_skills.py --apply`), clone the workspace, fill `.env` from `.env.example`, `setup_emulator.py`, then
  `/checkpoint`'s last point in `context.md`.
- [x] `validate_project.py --environment`: the tools the workspace's modules need are present (adb/emulator/Maestro
  for `mobile`, LibreOffice for the Excel recalculation, the browsers for `qa-audit`), with what is missing and how to
  install it.
- Note (where the spec was open): each tool is a line `[ok|missing] <tool>: <detail>` after the normal validation; a
  missing tool is a warning (exit 0) unless `--strict` (exit 1; `--strict` without `--environment` is a usage error).
  Always: git, the Python of `requires-python`, `.env` with every name of `.env.example` and of `project.yaml` (absent
  and empty names listed, never a value) and the skills (`sync_skills.compare`, read only). Also `node` for
  `functional-web`/`exploratory` (the Playwright MCP runs with `npx`; not in the plan's list); `excel-com` needs no
  LibreOffice; `emulator` is not needed when `mobile.serial` is a real device; `ios` checks `xcrun`;
  `mobile.build.source` only for an app (`android`/`ios`), not for `web`. The browsers are checked by the folders
  `qa-audit doctor` looks in (nothing is started; the doctor starts them). Methodology §12.3 step 1 now also has
  `pip install -r requirements.txt` (`pip install -e .` does not install openpyxl).

### 9.6 Pilot and heuristics — done 28.09.2026
- [x] The real mobile workspace gets its private remote (with the user's approval) and one batch is re-run from a
  **fresh clone in another folder** (simulating another computer): the environment check passes, the batch runs with
  `device.py`, `/checkpoint` applies it, the push is done with approval. Notes in that workspace's `context.md`, not
  here.
- [x] `docs/heuristics.md` gets the pilot's patterns, anonymised (methodology §17), e.g.: a chat app says a message
  failed while it was delivered (it appears after reopening); a search shows raw highlight tags; a conversation list
  stops at N items without paging; the accessibility tree misses cards and multi-line bubbles.

Notes (9.6): the real workspace got its private remote and 33 commits pushed (with the evidence, by the user's decision); a fresh clone in another folder passed `validate_project.py --environment`, `setup_emulator.py` ran for real (skips, reinstall without `aapt2`, native libraries copied again by itself), `device.py` ran one case end to end, the adapter applied it and the push was done; the original folder pulled it. Found by the pilot: `/checkpoint` committed the run log and `context.md` but not the workbook after applying — fixed (a second commit after `apply` exits 0). Without `aapt2` in the SDK the build is reinstalled every run (harmless, slower).

Acceptance of the phase: a new mobile project can be onboarded, tested in conversational mode, checkpointed and pushed,
then cloned and continued on another computer, with no helper outside the framework; the unit tests and the fictional
golden pass (methodology §15).

---

## Phase 10 — Jira (Xray) and Azure DevOps adapters

Why: the framework only writes results and bugs to Excel workbooks; with `tracker: jira` or `azure-devops` everything
after the run log is manual (7.7), and the cases cannot be read from the tracker, so `/plan-batch` and `/run-batch`
depend on the user copying them. Many teams keep their cases and bugs in one of those two trackers. This phase gives
them the same path as Excel: the run log stays the session's source (methodology §7.1), `/checkpoint` sends it with
the user's approval, a batch is never sent twice without being detected, and everything is checked by tests before it
touches a real tracker (methodology §15). Everything stays universal: examples and fixtures use the Demo Shop
(`demo-shop.atlassian.net`, `dev.azure.com/demo-shop`, keys like `DS-123`); no real site, project, field or token
enters this repository (methodology §1.2).

Decisions of the user (28.09.2026), which **revise decision 7.7** (manual logging for `jira`/`azure-devops`):
- adapters for **Jira with Xray** (cases are Xray `Test` issues, results go to a `Test Execution`) and for **Azure
  DevOps Test Plans + Bugs** (results to test runs / test points with an outcome per case; bugs are work items linked
  to the case);
- version 1 covers four directions: (1) send the results of a batch's run log at `/checkpoint`; (2) bugs: search
  duplicates, create, update, always with the user's approval before sending (methodology §9.4); (3) import cases from
  the tracker (onboarding §4 step 7, `/plan-batch`, `/run-batch`); (4) export the new cases approved in
  `/generate-cases`;
- validation: unit tests + a **golden with recorded, fictional HTTP responses** (in `pre-commit` and CI, no network)
  + a later **pilot by the user** on free Jira Cloud (Xray trial) and Azure DevOps organisations, in a practice
  workspace outside the repository;
- the repository stays private.

Decisions delegated by the user to the assistant's recommendation (28.09.2026, recorded in 10.1 in the techniques and
the template `docs/project.yaml` first, methodology §15):
1. **Blocked / N/A in Xray**: no default mapping; `/project-onboarding` asks per project (a custom Xray status, or
   `ABORTED`); when none is chosen the status is written with `skip`. A status mapped to `skip` or left empty
   (unmapped) is **not sent** — it stays only in the run log, is listed in the dry-run plan, the report and the record
   — and it is not an error. A status that is not a key of the map is refused (exit 4: a typo is never dropped
   silently), and `Question` stays refused (exit 4). Same rule for Azure DevOps outcomes (technique-jira-xray.md §4.1,
   technique-azure-devops.md §4.1).
2. **Case ID**: default `id_source: key` (Jira) / `id` (Azure DevOps); the `field` / `label` / `tag` modes stay for
   projects with their own IDs (technique-jira-xray.md §3, technique-azure-devops.md §3).
3. **Screenshots uploaded as evidence**: off by default (`attach_evidence: false`); on only when the project's evidence
   policy is `text+screenshot` (and `attach_evidence: true`); `validate_project.py` warns when `attach_evidence` is on
   with `evidence: "text"` (technique-jira-xray.md §8).
4. **Azure DevOps process**: the project must use the Agile or Scrum process (or one inherited from them); `Basic` has
   no `Bug` type and `check` refuses it with a clear message; platform → Test Configuration through the table
   `results.configurations` of `project.yaml` (technique-azure-devops.md §0, §3).
5. The endpoint names in both techniques are those of the public documentation at the time of writing: 10.2 and 10.3
   confirm each one against the current public documentation before relying on it (the techniques first if one
   changed); the common base (10.1) calls no endpoint.

Specification: `docs/technique-jira-xray.md` (with the common contract of the tracker adapters, §2) and
`docs/technique-azure-devops.md`, written first (methodology §15). Order: 10.1 → 10.2 → 10.3 → 10.4 → 10.5; 10.2 and
10.3 can go in parallel once 10.1 is done.

### 10.0 Specification first (methodology §15) — done 28.09.2026
- [x] `docs/technique-jira-xray.md` and `docs/technique-azure-devops.md`: scope and API versions (cloud first: Jira
  Cloud REST v3 + Xray Cloud API v2 with client id/secret → token, the execution import and GraphQL; Azure DevOps
  Services REST 7.1 with a PAT; Server/Data Center out of version 1), the `project.yaml` sections, status/outcome
  mapping with `Question` refused, field mapping, case ID mapping, commands and exit codes, dry run and `--yes`,
  idempotency, duplicate-bug search (JQL/WIQL), evidence redaction, rate limits and pagination, golden and round trip.
- [x] Methodology §1.1 (layer 3), §2 (ID mapping in the tracker), §4 step 7, §4.2 (table per tracker), §7.1 (`run`
  block), §9.4 (dry run), migration note v2.1 → v2.2.
- [x] `CLAUDE.md` references the two techniques; the template `docs/project.yaml` has the `jira:` and `azure_devops:`
  sections commented. The commands go into `CLAUDE.md`'s Scripts section when they exist (10.2/10.3), as for every
  script so far.
- Note (decisions where the brief was open): one entry point for both trackers, `python -m adapters.tracker
  <workspace> check|import-cases|export-cases|apply|bug-search` (the tracker comes from `project.yaml`, so the skills do
  not branch); exit codes 0 ok · 1 dry run with something to send (not approved yet, as `migrate_workspace.py`) · 2
  conflict with the tracker · 3 network/auth/rate limit (a send may be partial, the next run continues) · 4 invalid
  input or configuration · 5 sent but the read back differs (0, 2, 4 and 5 mean what they mean in
  `adapters.excel.apply`); the case ID mapping lives in the tracker (`id_source: key|field|label`, `id|field|tag` in
  Azure DevOps) with a cache in `<workspace>/tracker/ids.yaml`; idempotency through a record per batch in
  `<workspace>/tracker/sent/<batch_id>.json` (hash of the results, each confirmed write) plus markers in the tracker
  (`qa-batch-<batch_id>`, `qa-case-<id>`), and a create is never retried blindly; one Test Execution / test run per
  batch; the new `run` block (environment, build) in the run log; `N/A`/`Blocked` have no Xray default (asked at the
  onboarding: a custom status, `ABORTED` or `skip`); `Not run` → `TODO` in Xray and `skip` in Azure DevOps; the
  golden is a `unittest` (no LibreOffice), not `scripts/golden_check.py`; the HTTP client uses the standard library
  (no new dependency) and the `Redactor` of `scripts/common.py`; `/dashboard` stays Excel only (building it from what
  the assistant sent would miss everyone else's results: not cheap to do right).

### 10.1 Common tracker base (`adapters/tracker/`) — done 28.09.2026
- [x] Configuration: `jira:` / `azure_devops:` read from `project.yaml`, credentials only by variable name (`*_var`,
  so `workspace_git.py init` puts them in `.env.example`); a `TrackerAdapter` base with the four directions as methods
  and the input validation of technique-jira-xray.md §2.6 (the Excel adapter's `validate_batch` rules, reused where
  they are the same).
- [x] HTTP client (standard library): timeouts, retries only for `429`/`5xx` on reads and idempotent writes,
  `Retry-After`, backoff with jitter, re-authentication on one `401`, pagination helpers, a create never retried
  blindly; the `Redactor` of `scripts/common.py` on every output, the record and the recordings; one transport
  function the tests replace.
- [x] ID mapping cache (`tracker/ids.yaml`), the record of what was sent (`tracker/sent/<batch_id>.json`, written
  after each confirmed write; same hash → no request; same id, other content → 4; incomplete → continue) and the
  marker search; never under `history/`.
- [x] CLI `python -m adapters.tracker <workspace> <command>` with the dry run by default, `--yes`, `--now`,
  `--record`, and the exit codes of technique-jira-xray.md §2.4.
- [x] `validate_project.py`: the tracker's section (required keys, `https` URLs, `*_var` names, every status of the
  vocabulary mapped, `Question` never mapped, Azure DevOps outcomes from the closed list, `skip` counted), the
  variables in `.env` with `--environment`; the warning "no adapter" only for a tracker whose task is not done.
  `docs/workspace/gitignore` gets `tracker/recordings/`.
- [x] Test kit: a fake tracker transport (responses in order, each with the request it answers; the requests made
  kept for comparison; a fault injected at request *n*) and a local `http.server` on 127.0.0.1 only for the client's
  own timeout and retry tests. Unit tests for the client, the record, the validation and the CLI.
- Note (choices where the spec was open): modules `errors`, `config`, `http`, `record`, `base`, `cli` and `testing`
  (the fake transport ships in the package, so the goldens of 10.2/10.3 reuse it); the back-ends `adapters/jira` and
  `adapters/azure_devops` are stubs with `implemented = False` (exit 4 "not implemented yet (plan 10.2/10.3)", after
  the configuration is checked), and `config.IMPLEMENTED` is the one switch for `validate_project.py` (the "no adapter
  yet" warning, and a missing section as a problem instead of silence); "unmapped" means a key with `skip` or an empty
  value, a status that is not a key is exit 4; a `new_defect` needs `title`, `expected` and `observed` (methodology
  §9.2), and the new optional `bugs.priorities` checks its priority before any request; `bug_status` needs a `bug`;
  `apply`/`export-cases` read the credentials only after the input and the record were checked (an invalid or already
  sent batch needs no `.env`); the record is marked complete right after the sends, before the read back, so a run
  after exit 5 makes no request; with `--yes`, a batch where nothing is sent still gets a complete record; retries on
  GET/HEAD/PUT/DELETE/OPTIONS and on requests marked `read`/`idempotent` (a JQL, GraphQL or WIQL query sent with
  POST), 500 never retried, a write's 429 is exit 3 without a retry and its 502/503/504 or timeout an "uncertain
  write" (exit 3, the marker search decides next time); the renewal after a 401 is not an attempt; backoff 1 s × 2ⁿ,
  capped at 60 s, jitter 50–100 %; a 4xx the back-end does not handle is exit 2 for 404/409/412 and exit 4 for the
  others; `--record` writes `tracker/recordings/<instant>-<command>/NNNN.json`; `--now` also on `export-cases`; the
  command line is in `CLAUDE.md`'s Scripts section already (the 10.0 note said 10.2/10.3), marked as base only.

### 10.2 Jira + Xray adapter (`adapters/jira/`) — done 28.09.2026
- [x] Endpoints confirmed against the current documentation (technique §0); the technique fixed first (v1.2) where
  the documentation differed.
- [x] `check`; `import-cases` (JQL with `nextPageToken`, steps and preconditions through GraphQL, latest status in
  the test plan, `tracker/cases.json` and `ids.yaml`, flags); `export-cases` (GraphQL `createTest`, folder, test
  plan); `apply` (new bugs, one Test Execution per batch through the Xray JSON import in chunks of 100, defects,
  comments, `bug_status` transitions, read back); `bug-search` (JQL with relaxed passes).
- [x] Field formatting from the field schema (option, component, priority, labels with prefix, ADF ↔ text).
- [x] Golden `tests/golden/jira/` (scenarios of technique §10.1) and the round trip (§10.2), both in the unit tests.
- [x] `CLAUDE.md` Scripts section: the `adapters.tracker` commands and their exit codes.
- Note — endpoints confirmed (28.09.2026) and where:
  - Jira Cloud REST v3, from its OpenAPI description
    (https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json; reference
    https://developer.atlassian.com/cloud/jira/platform/rest/v3/): `GET /rest/api/3/myself`, `GET
    /rest/api/3/project/{key}` (with `issueTypes`), `GET /rest/api/3/project/{key}/statuses`, `GET /rest/api/3/field`,
    `GET /rest/api/3/issue/createmeta/{project}/issuetypes/{issueTypeId}` (the old `GET /rest/api/3/issue/createmeta`
    is deprecated), `POST /rest/api/3/search/jql` (enhanced search: `jql`, `fields`, `maxResults`, `nextPageToken`;
    answer `issues`, `nextPageToken`, `isLast`; the old `/rest/api/3/search` is "currently being removed"), `POST
    /rest/api/3/issue`, `PUT /rest/api/3/issue/{key}`, `GET`/`POST /rest/api/3/issue/{key}/comment`, `GET`/`POST
    /rest/api/3/issue/{key}/transitions`, `GET /rest/api/3/issueLinkType`, `POST /rest/api/3/issueLink`;
  - Xray Cloud REST v2: `POST /api/v2/authenticate` (the token as a JSON string;
    https://docs.getxray.app/display/XRAYCLOUD/Authentication+-+REST+v2), `POST /api/v2/import/execution` and `POST
    /api/v2/import/execution/multipart` (parts `results` and `info`; in the multipart one the results' `info` is
    ignored; https://docs.getxray.app/display/XRAYCLOUD/Import+Execution+Results+-+REST+v2), the Xray JSON format
    (`testExecutionKey`, `info.testPlanKey`/`testEnvironments`, per test `testKey`, `status`, `comment`,
    `start`/`finish`, `defects`, `evidence` with `data`/`filename`/`contentType`;
    https://docs.getxray.app/display/XRAYCLOUD/Using+Xray+JSON+format+to+import+execution+results);
  - Xray Cloud GraphQL (https://us.xray.cloud.getxray.app/doc/graphql/): `getStatuses(projectId)`, `getTests(issueIds,
    limit ≤ 100, start)` (with a `jql` matching more than 100 issues it errors), the Test fields `testType`, `steps`
    (`action`, `data`, `result`), `preconditions`, `status(testPlan:)`, `getTestExecution(issueId)` → `testRuns(limit,
    start)` (`status`, `defects` as issue ids, `test`), `createTest(testType, steps, folderPath, jira)`,
    `addTestsToTestPlan(issueId, testIssueIds)`.
- Note — what the documentation changed in the technique (v1.2, first): the first chunk of results goes through the
  **multipart** import, whose `info` part takes Jira fields — the plain import's `info` cannot take the execution's
  `qa-batch-` label, and without it an execution created by a request that timed out could not be found; the build
  goes in the execution's summary (and the bugs' description), not in `version`/`revision`; steps are read with
  `getTests(issueIds)` 100 at a time (not `getTests(jql)`), and the status in the test plan through the Test field
  `status(testPlan:)` in the same query (not `getTestPlan`); the read back pages `testRuns` and compares defects by
  key or issue id.
- Note (choices where the spec was open): modules `api.py` (the calls, both clients, the Xray token, GraphQL with
  variables, never values in the query), `fields.py`, `adf.py`, `adapter.py`; Xray's `authenticate` is sent as a read
  (it changes nothing, so it is retried and a dry run has no writing request); the rich text fields of a new test go
  through `PUT /rest/api/3/issue/{key}` in ADF after `createTest` (the format `createTest`'s `jira` object expects for
  them is not documented); `ids.yaml` keeps the final ID of an exported case (`DS-140`), not the provisional
  `NEW-1`, which the next cases file reuses — the record keeps `NEW-1 → DS-140` and the command prints it; the bug's
  description is the environment and build, role, platform, preconditions, expected, observed and the linked case,
  then the steps as a list (the project's `description` of the draft, when given, first); a resumed run looks for its
  marker in the comments of an existing bug before commenting again; the existing bugs' comment carries the marker
  `qa-batch-<id> · <case>`; `bug_status` already reached is not transitioned again; the transition is chosen by its
  target status; evidence is attached to the test run only, not to the bug; the case ID resolution uses `key in
  (...)` / `cf[N] ~ "..."` (exact match checked after) / `labels in (...)` in groups of 50, a JQL 400 there is a
  conflict (exit 2), and an issue moved to another project is followed through the issue id of the cache; the
  export's duplicate check is the same summary in the project (not only the folder: a folder needs one more query
  per case); `bugs.defaults` keys that are model fields fill the draft, other keys are Jira field ids; a
  `new_defect` on an item that is not sent is not created (listed as not sent). The golden is a runner
  (`tests/test_jira_golden.py`) over the folders of `tests/golden/jira/` with a shared `project.yaml` (a scenario
  may have its own) and the `.env` written by the test (never committed); `tests/jira_fake.py` is a stateful fake
  Jira + Xray (with a small JQL evaluator) for the unit tests and the round trip, where the requests depend on the
  state. No new dependency.
- Only the pilot (10.5) can confirm: the Xray `info` part of the multipart import on a real site (label, environments,
  test plan), the answer of `createTest` (`jira(fields: ["key"])`) and whether its `folderPath` must exist; the JQL
  error of a key that does not exist (a 400 is assumed); `cf[N] ~` on the project's ID field; the text search of
  `bug-search`; the Xray status names a real project has (`check` lists them); rate limits and the token's lifetime.

### 10.3 Azure DevOps adapter (`adapters/azure_devops/`) — done 29.09.2026
- [x] Endpoints confirmed (api-version 7.1) against the current documentation; the technique fixed first (v1.2).
- [x] `check`; `import-cases` (suites of the plan with `x-ms-continuationtoken`, `workitemsbatch` in groups of 200,
  steps XML ↔ model, shared steps and parameters flagged, points and latest outcome); `export-cases` (`$Test Case`
  with steps, added to `new_cases_suite_id`); `apply` (new bugs with `TestedBy` and the result link, one test run
  per batch, results patched, run completed, `bug_status`, read back; a run left incomplete continued through the
  record); `bug-search` (WIQL + `workitemsbatch`).
- [x] JSON Patch with a `test` on `/rev` for existing work items (a concurrent edit is exit 2); HTML fields escaped.
- [x] Golden `tests/golden/azure_devops/` and the round trip, in the unit tests.
- Note — endpoints confirmed (29.09.2026), Azure DevOps Services REST API 7.1 on learn.microsoft.com (`https://learn.microsoft.com/en-us/rest/api/azure/devops/<area>/<page>?view=azure-devops-rest-7.1`):
  - core / processes: `GET _apis/projects/{project}?includeCapabilities=true` (`capabilities.processTemplate`:
    `templateName`, `templateTypeId`; https://learn.microsoft.com/en-us/rest/api/azure/devops/core/projects/get?view=azure-devops-rest-7.1), `GET _apis/work/processes/{processTypeId}` (`name`,
    `parentProcessTypeId` for an inherited process; https://learn.microsoft.com/en-us/rest/api/azure/devops/processes/processes/get?view=azure-devops-rest-7.1);
  - wit: `GET {project}/_apis/wit/workitemtypes`, `GET …/wit/fields` (the field types; https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/fields/list?view=azure-devops-rest-7.1),
    `GET …/wit/workitemtypes/{type}/fields?$expand=All` (`alwaysRequired`, `defaultValue`, `allowedValues`;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-item-types-field/list?view=azure-devops-rest-7.1), `POST …/wit/wiql?$top=` (`{query}` → `workItems[{id}]`, no paging;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/wiql/query-by-wiql?view=azure-devops-rest-7.1), `POST …/wit/workitemsbatch` (at most 200 ids, `fields`, `$expand`, `errorPolicy:
    Omit`; https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/get-work-items-batch?view=azure-devops-rest-7.1), `POST …/wit/workitems/${type}` and `PATCH
    …/wit/workitems/{id}` (`application/json-patch+json`, ops `add`/`test`/…, `/relations/-`;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/create?view=azure-devops-rest-7.1, …/update), `GET`/`POST …/wit/workItems/{id}/comments` (**`7.1-preview.4`**,
    `format=html`, body `{text}`, `continuationToken` in the body; https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/add-work-item-comment?view=azure-devops-rest-7.1,
    …/get-comments);
  - testplan: `GET …/testplan/plans/{planId}` (https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/test-plans/get?view=azure-devops-rest-7.1), `GET
    …/testplan/Plans/{planId}/suites` (`suiteType`, `x-ms-continuationtoken`; https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/test-suites/get-test-suites-for-plan?view=azure-devops-rest-7.1),
    `GET …/testplan/configurations` (https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/configurations/list?view=azure-devops-rest-7.1), `GET`/`POST
    …/testplan/Plans/{planId}/Suites/{suiteId}/TestCase` (`testIds`; body `[{workItem: {id}}]`, answer with
    `pointAssignments`; https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/suite-test-case/get-test-case-list?view=azure-devops-rest-7.1, …/add), `GET
    …/Suites/{suiteId}/TestPoint?testCaseId=` (`configuration`, `testCaseReference`, `results.outcome`;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/testplan/test-point/get-points-list?view=azure-devops-rest-7.1);
  - test: `POST …/test/runs` (`name`, `plan`, `pointIds`, `automated`, `comment`; https://learn.microsoft.com/en-us/rest/api/azure/devops/test/runs/create?view=azure-devops-rest-7.1), `GET
    …/test/runs?planId=&automated=&includeRunDetails=&$skip=&$top=` (https://learn.microsoft.com/en-us/rest/api/azure/devops/test/runs/list?view=azure-devops-rest-7.1), `PATCH
    …/test/runs/{runId}` (`state: Completed`; https://learn.microsoft.com/en-us/rest/api/azure/devops/test/runs/update?view=azure-devops-rest-7.1), `GET …/test/Runs/{runId}/results`
    (`detailsToInclude=WorkItems` gives `associatedBugs`; `$top` at most 1000, 200 with details;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/test/results/list?view=azure-devops-rest-7.1), `PATCH …/test/Runs/{runId}/results` (`id`, `outcome`, `state`, `comment` ≤ 1000
    characters, `associatedBugs: [{id}]`; https://learn.microsoft.com/en-us/rest/api/azure/devops/test/results/update?view=azure-devops-rest-7.1), `GET`/`POST
    …/test/Runs/{runId}/Results/{resultId}/attachments` (`stream`, `fileName`, `attachmentType`;
    https://learn.microsoft.com/en-us/rest/api/azure/devops/test/attachments/create-test-result-attachment?view=azure-devops-rest-7.1).
- Note — what the documentation changed in the technique (v1.2, first): the comments API exists only as
  `7.1-preview.4`; **a bug is linked to a test result through the result's `associatedBugs`** (the documented
  field of `PATCH …/results`, read back with `detailsToInclude=WorkItems`), not an artifact link written on the bug;
  **test points are resolved per suite** with `TestPoint?testCaseId=` (the configuration name decides between
  points, through `results.configurations`); the build goes in the run's name and comment (`build`/`buildReference`
  are pipeline builds); no dates are sent (a result's completed date must follow its started date; the tracker stamps
  them, the run log keeps `ts`); a run's tags are 5 alphanumeric names under 30 characters, so the run's marker is its
  comment; the `bug_status` state change goes right after the new bugs, before the results and comments add
  revisions to the bug (its `test` on `/rev` uses the revision read at the resolution).
- Note (choices where the spec was open; mirroring 10.2 unless Azure DevOps makes it wrong): modules `api.py`,
  `fields.py`, `html.py`, `steps.py`, `adapter.py`; the shared `significant_words`/`fill_template` moved from the Jira
  back-end to `adapters/tracker/base.py` (reuse, methodology §14.1); the PAT as basic authentication with an empty user
  (the base64 also redacted); `check` refuses Basic (the technique's message) and reports CMMI (or any other base) as
  not covered, both exit 4, and follows an inherited process to its parent; `check` also requires `bugs.states` and
  `bugs.closed_states` to be states of the bug type (Scrum has no `Closed`: the default would silently count Done bugs
  as open); the marker search of a test run happens only when a record exists without a run (one list of the plan's
  runs), the marker search of bugs/cases always before a create (one WIQL); a `new_defect` area must be a full path
  of the project (refused, never guessed); evidence on the test result only, text files redacted, a resumed send
  lists the result's attachments first; comments carry the marker `qa-batch-<id> · <case>` and are looked up before
  commenting again (as in Jira); `bug_status` already reached is not sent again, a state outside the type's states
  is exit 2; `ids.yaml` keeps `{id, points: {configuration: point}}` with the final ID; results in chunks of 100,
  recorded per chunk; `update_results`/`update_run` retried as idempotent writes, the work item PATCH and every POST
  never retried blindly; `import-cases --query` flags cases with no test point; `export-cases` refuses a same-title
  `Test Case` anywhere in the project and, when resumed, asks the suite which cases it already has. The golden runner
  (`tests/test_azure_devops_golden.py`) and the stateful fake (`tests/ado_fake.py`, with a small WIQL evaluator and
  continuation tokens) follow 10.2's. No new dependency. With both back-ends built, the "no adapter yet" warning left
  `validate_project.py` (`config.IMPLEMENTED` has both; a missing section is a problem for both).
- Only the pilot (10.5) can confirm: that `associatedBugs` in a results PATCH shows the link on the bug (and whether
  it adds a revision); that `TestPoint?testCaseId=` takes a comma-separated list; the answer to a failed `test` on
  `/rev` (412 assumed, exit 2) and to a JSON Patch on a field the type does not have; `CONTAINS WORDS` with several
  words; the WIQL of `System.Tags CONTAINS` on a tag with `-`/`_`; whether a run created with `pointIds` needs a
  state; the Test Plans licence of the pilot's user; the comments preview version still accepted; rate limits.

### 10.4 Skills
- [ ] `/project-onboarding`: with `jira`/`azure-devops`, fill in the tracker's section with the user (IDs, fields,
  status/outcome map — `Blocked` and `N/A` asked, never assumed), the credential variables in `.env`, then `check`
  and `import-cases` (methodology §4 step 7), and the technique imported in the workspace's `CLAUDE.md`.
- [ ] `/checkpoint`: step 7a replaced by the adapter: `apply` without `--yes`, the plan shown, `--yes` only after the
  user's approval, the exit codes explained as in step 7; commit the record after exit 0 (as the workbook).
- [ ] `/log-bug`: `bug-search` before drafting; the draft goes in the run log as `new_defect` (or `bug` +
  `bug_status`), as with Excel.
- [ ] `/generate-cases`: the approved cases written to a cases file and sent with `export-cases` (dry run, approval,
  `--yes`); the final IDs told to the user.
- [ ] `/plan-batch` and `/run-batch`: cases from `tracker/cases.json` (refreshed with `import-cases` when older than
  the session), statuses from the tracker's map, the `run` block written in the run log.
- [ ] `/end-session`: totals from the run logs and the read back, not from memory. `/dashboard`: unchanged (Excel
  only), its message points to the tracker's own reports.
- [ ] README (trackers) aligned; `sync_skills.py --apply` run and the global copy in sync.

### 10.5 Pilot on free instances (user) and heuristics
- [ ] The user creates a free Jira Cloud site with an Xray trial and a free Azure DevOps organisation, each with a
  practice project, and a practice workspace per tracker in `qa-workspaces/` (outside this repository).
- [ ] In each: onboarding, `import-cases`, one batch with a Pass, a Fail with a new bug, a Fail linked to an existing
  bug, a `Blocked` and an `N/A`; the dry run approved, sent, sent again (nothing changes), read back; one new case
  exported; `--record` on, so the workspace can keep a real golden later (methodology §15). Notes in those
  workspaces' `context.md`, not here.
- [ ] `docs/heuristics.md` gets the anonymised patterns found (methodology §17); what the pilot shows about the APIs
  goes into the techniques first, then the code.

Acceptance of the phase: a Jira + Xray project and an Azure DevOps project can be onboarded from their tracker, have
batches planned from imported cases, send results and bugs at `/checkpoint` with approval and without duplicates,
export new cases, and be continued on another computer; the unit tests and both goldens pass (methodology §15).

---

## Change log of this plan

| Date | Change |
|---|---|
| 2026-09-29 | 10.3 done: the Azure DevOps Services back-end (`adapters/azure_devops/`: `check`, `import-cases`, `export-cases`, `apply`, `bug-search`), the endpoints confirmed against the public documentation (list in the note of 10.3) and the technique fixed first (v1.2: comments preview `7.1-preview.4`, bugs linked through the result's `associatedBugs`, points per suite, build in the run's name and comment, no dates, the run's marker in its comment, the state change right after the new bugs, full area paths); `azure-devops` in `IMPLEMENTED` and the "no adapter yet" warning removed (`validate_project.py`, methodology §1.1/§4.2, template, README); golden `tests/golden/azure_devops/` (5 scenarios, hand-written requests) and `tests/test_azure_devops_adapter.py` with a stateful fake Azure DevOps (round trip, exit codes 0–5, uncertain writes, continuation tokens, relaxed passes, Basic refused); `CLAUDE.md`. Tests 513 → 552. Choices and what only the pilot confirms in the note of 10.3. |
| 2026-09-28 | 10.2 done: the Jira Cloud + Xray Cloud back-end (`adapters/jira/`: `check`, `import-cases`, `export-cases`, `apply`, `bug-search`), the endpoints confirmed against the public documentation (list in the note of 10.2) and the technique fixed first (v1.2: multipart import for the first chunk, the build in the summary, `getTests` by issue ids, the plan status through `status(testPlan:)`, rich text of new tests through Jira, final IDs in `ids.yaml`); `jira` in `IMPLEMENTED` (`validate_project.py`: the section required, no "no adapter" warning); golden `tests/golden/jira/` (5 scenarios, hand-written requests) and `tests/test_jira_adapter.py` with a stateful fake Jira + Xray (round trip, exit codes 0–5, the uncertain write, pagination, re-authentication, relaxed passes); `CLAUDE.md`, the template. Tests 479 → 513. Choices and what only the pilot confirms in the note of 10.2. |
| 2026-09-28 | 10.1 done: the decisions the user delegated (Blocked/N/A unmapped → not sent, `Question` still refused; `id_source` key/id by default; screenshots only with `text+screenshot`; Azure DevOps needs Agile or Scrum, platform → Test Configuration table; endpoints confirmed in 10.2/10.3) recorded in the techniques (v1.1), the template and Phase 10 first; then `adapters/tracker/` (configuration and its validation, credentials by name, stdlib HTTP client with retries, Retry-After, backoff, re-authentication, pagination, redaction and `--record`; the record of what was sent, the ID cache, `cases.json`; the batch validation with the `run` block and the status map; the command line with the dry run, `--yes`, `--now` and exit codes 0–5; the fake transport), stubs for `adapters/jira` and `adapters/azure_devops` (exit 4 until 10.2/10.3), `validate_project.py` checks the tracker's section, `docs/workspace/gitignore` ignores `tracker/recordings/`, `CLAUDE.md` documents the command. `tests/test_tracker_config.py`, `test_tracker_http.py` (with a local server on 127.0.0.1), `test_tracker_cli.py` and 1 new validation test (408 → 479). Choices in the note of 10.1. |
| 2026-09-28 | Phase 10 planned by the user's decision (revises 7.7: `jira` and `azure-devops` get adapters, Jira with Xray and Azure DevOps Test Plans + Bugs, four directions, golden with recorded fictional responses, pilot on free instances). 10.0 done: `docs/technique-jira-xray.md` (with the common contract of the tracker adapters) and `docs/technique-azure-devops.md`; methodology v2.2 (§1.1, §2, §4 step 7, §4.2, §7.1, §9.4); `CLAUDE.md` references; the template's `jira:` and `azure_devops:` sections. Decisions in the note of 10.0. |
| 2026-09-28 | 9.6 done: pilot from a fresh clone (private remote, device driver, adapter, push); `/checkpoint` now commits the workbook after applying; 6 anonymised heuristics. Phase 9 done. |
| 2026-09-28 | 9.5 done: `docs/workspace/resume.md` (the steps of methodology §12.3, linked from §12.3 and `/project-onboarding` 8c) and `validate_project.py --environment [--strict]` (reuses `find_soffice`, `setup_emulator.find_tools`, `sync_skills.compare` and the doctor's browser folders, each behind a function the tests replace); 11 new tests in `tests/test_validate_project.py` (397 → 408). Decisions in the note of 9.5. |
| 2026-09-28 | 9.4 done: `docs/workspace/gitignore` (template) and `scripts/workspace_git.py` (`init`, `status`, `remote --create/--set`, `push`; `gh` through one function the tests fake); `common.variable_names()` (the `*_var` keys and `variables` lists of `project.yaml`) for `.env.example`; `validate_project.py` warns (no remote, commits not pushed, `.env.example` missing a variable, files over 50 MB) and has problems for `.env` tracked and a public remote; `/project-onboarding` step 8c (init, the remote proposed and created only with a yes) and `/checkpoint` 6/6a (status after the commit, push only when the user says so). `tests/test_workspace_git.py` (22) and 3 new validation tests, all in temporary repositories with git's hook variables dropped and a temporary global configuration (372 → 397 tests). Decisions in the note of 9.4. |
| 2026-09-28 | 9.3 (skills and validation): `/test-mobile` picks the mode per batch and writes it with the reason at the top of the run log (`"mode"`); conversational mode with `setup_emulator.py` and `device.py check` first, a `send --capture <case>_t<N>` per turn, the screenshot read whenever the dump may miss content, one note per turn, guardrail answers judged by the spec's text; assisted mode (the user taps, the driver only reads). `/project-onboarding` asks for the target (`mobile.serial`, or `mobile.avd` + `mobile.image` — the plan's `mobile.device`), `mobile.build`, `mobile.permissions` and, for conversational apps, `send_label`, `ignore_labels`, `browser`, and shows `setup_emulator.py --dry-run`. `validate_project.py` checks `serial`, `send_label`, `ignore_labels`, `evidence_batch` (a quoted folder name: an unquoted date is a YAML integer and is refused) and `browser` (371 → 372 tests); the template has them as comments. Global copy of the skills still to sync. |
| 2026-09-28 | 9.2 done: `scripts/setup_emulator.py` (SDK, AVD, boot, build checked against `mobile.build.sha256`, ARM-only native libraries on an x86_64 image with root, permissions, Chrome's first run; `--dry-run`, production builds refused) and the new `mobile:` keys (`avd`, `image`, `build`, `permissions`) in `validate_project.py` and the template; `tests/test_setup_emulator.py` with a fake SDK and fixture APKs built in the test (26 tests, 345 → 371). Decisions where the spec was open: default AVD `qa-<project>`; a missing `build.sha256` installs with a `skip` line that prints the hash to record; the dry run still does the local checks (SDK folders, SHA-256, the APK's libraries); a device step that fails stops the run (exit 3). Not run against a real SDK or emulator. |
| 2026-09-28 | 9.1 done: `scripts/device.py` (check, dump, tap, type, send, shot, link) with the foreground guard, `.env` values refused in typed text and the link never printed; `tests/test_device.py` with a fake `adb` (29 tests, 316 → 345). Checked read-only on a real emulator: `check`, the guard refusing another app, and a real uiautomator dump parsed. Found on the way: committing from a linked git worktree, the `tests` hook failed in `test_validate_project` and `test_migrate_workspace` (git's hook variables, e.g. `GIT_DIR`, are absolute there and the `git -C <tmp>` of the scripts read this repository); both modules now drop them while they run. |
| 2026-09-28 | Phase 9 planned by the user's decision, from a real mobile pilot run with helpers outside the framework: device driver and conversational mode, reproducible emulator, one git repository per workspace with a private remote, resume on another computer. |
| 2026-09-23 | Initial version |
| 2026-09-24 | General review (`994fa8b`): the denylist now reads Office files and file names (before, a client `.xlsx` got past the guard; history checked, clean); the adapter validates the whole batch before writing (`validate_batch`, code 4 in `apply`) and accepts an existing `bug`; a single run log format (methodology §7.1 = technique-excel §4); 27 `unittest` tests in CI and pre-commit (including the dashboard check of 5.1); ruff in pre-commit; skills aligned with the flow. |
| 2026-09-24 | A batch edited after being applied: before, it was applied again (different hash) and duplicated notes and defects; now the control sheet keeps the `batch_id` (file name) and the adapter refuses it (code 4). Checked: `main` cannot be protected (private repository, Free plan: API 403); the CI is a warning after the push, not a barrier — to decide in 6.2. |
| 2026-09-24 | Summary vs direct count: optional block `excel.summary`; `check_summary()` compares the summary's recalculated values with the case-by-case count (per area, status, total and total row); code 5 in `apply`, an alert in the dashboard, part of the golden. The totals are never read from the summary. Detected in the tests: a formula counting the wrong column and a case outside the fixed range (the demo uses `A2:A200`). |
| 2026-09-24 | Definition of "run" (user decision): every case outside `Not run`; N/A and Blocked count as run; % run = run / total. Written in methodology §16.3; the demo's summary formula aligned (before, it excluded the N/A) and the workbook regenerated (only those formulas and the note changed). |
| 2026-09-24 | Check of the summary's % run (`summary.percent_column` + `not_run_status`): compares with (total − not run) / total, fraction or percentage points, strict tolerance by default (0.001 pp); the dashboard refuses a `not_run_status` different from its pending status. Demo configured. |
| 2026-09-24 | detect-secrets: the CI's (Linux, Python 3.12) flagged a password key followed by a variable name in an inline YAML map that the local one (Windows, Python 3.14) let through, with the same version and baseline; obvious secrets are caught by both. The content fixed (keys `user_var`/`password_var`); exact cause to be found. In workspaces without CI, the local guard is the only barrier. |
| 2026-09-24 | **Cause of the detect-secrets difference found**: version 1.5.0 opens the files with `open()` without an encoding; on Windows (cp1252) a UTF-8 file with certain characters (e.g. "Á", bytes C3 81) raises `UnicodeDecodeError` and is **skipped silently**, as if it were binary (6 files of the repository, including the methodology). It was not Python 3.14 nor the YAML. Fixed: a local hook with the same version in UTF-8 mode (`python -X utf8`), the same id for the CI; `tests/test_guards.py` prevents going back. The history sweep of 6.2 was redone in UTF-8: 1 finding, the known false positive (a variable name in the old version of the demo's `project.yaml`). |
| 2026-09-24 | CI: runners fixed at `ubuntu-24.04` (`3f72fcd`) before the move of `ubuntu-latest` to Ubuntu 26 (19.10.2026); the 5 jobs passed (run 36047318268). Note in 4.1. |
| 2026-09-24 | General review 2 (test everything → analysis → fixes → retest): the **inspector** left the defects sheet without a proposal when every defect was open (a view with the same IDs; it happened in the demo), now it breaks the tie by the header row and the number of columns, with `tests/test_inspect.py` (there were no tests); **console**: on Windows, the output read through a pipe came in cp1252 and accents arrived garbled at Claude Code, now every entry point writes in UTF-8 (`tests/test_console.py`); **validate_project**: an environment without a URL passed (the template's case), URLs of `api`/`accessibility` outside the declared environments or from production become a problem, and it warns if `.env`/`.api-session.env`/`dashboard.html` are not in the `.gitignore` of a workspace with git; **recalculation**: on Linux the timeout only killed `soffice` and the wait stayed stuck on the child, now it kills the process group (`tests/test_recalc.py`); `api_request --output` relative to the workspace, as in the other scripts; `scripts/sync_skills.py` for the global copy of the skills (before, manual, unchecked); the pre-commit `tests` hook also for skills, `CLAUDE.md`, demo and template; docs aligned with what exists (`qa_core`, CLI `qa` and `executor-web` marked as planned; codes 4 and 5 of `apply` in the Excel technique); `docs/heuristics.md` created with the heuristics of the demo's 3 bugs. 95 → 117 tests. |
| 2026-09-24 | General review 2 in `a57ed19`. Phase 7 (closing the framework) added with what is missing for v1.0: CI on Linux, pilot, real golden, protected `history/`, fixed versions, reproducible demo, scope decisions. |
| 2026-09-24 | 7.1 done: green CI on Linux after the general review 2 (run 36053764682), with the recalculation timeout checked on Ubuntu. |
| 2026-09-24 | 7.4 (`3563f24`), 7.5 (`0a61bc6`) and 7.6 (`ced85fc`) done: `history/` protected in the workspace's `.claude/settings.json`, fixed versions (Playwright MCP and Python dependencies), a reproducible demo with a test in the `golden` job. |
| 2026-09-24 | 7.7 done (`3ffbb62`), user decisions: no `qa_core`/CLI `qa`; `executor-web` postponed and reassessed in the pilot; `jira` and `azure-devops` as manual logging (methodology §4.2). 7.2 got the steps of reassessing `executor-web` and confirming the protection of `history/`. |
| 2026-09-24 | 7.8 done (`799e068`): `scripts/common.py` with the reading of `project.yaml` and `.env` and the redaction, without a change of behaviour. The 1st commit attempt was blocked by detect-secrets (a false positive: the names of the redaction constants in a note of this plan); the text fixed, without an allowlist. |
| 2026-09-24 | `run_mobile.py`: a flow given that does not exist becomes a usage error (code 2, nothing run, with suggestions of the same case's flows), instead of `Blocked` "the flow did not run", which looked like an environment problem. Each flow can be a path (current folder, workspace or flows folder) or the case ID (the only `<ID>_*.yaml`). 3 new tests; skill `/test-mobile` and `CLAUDE.md` updated. |
| 2026-09-24 | 7.9 asked for by the user: its criteria were not met yet (7.2 and 7.3 missing). By the user's decision, the 1st criterion of 7.3 was brought forward, the real golden with `--dir`/`QA_GOLDEN_DIR` (`e74c281`), and v1.0 is left for after the pilot. |
| 2026-09-27 | 8.11 closed: CI green; the history rewritten to remove a client's short name (force push of `main`, `feat/login` and the tags, by the user's decision), and the name added to the denylist. |
| 2026-09-26 | 8.11: everything in English (docs, skills, scripts, adapter, tests, demo), `scripts/migrate_workspace.py`, the practice workspace converted with its real golden unchanged. |
| 2026-09-26 | 8.11 redefined by the user's decision: everything in English, names included, with a migration script for the workspaces. |
| 2026-09-26 | 8.17 done: `focus-not-visible` (problem) when the element's screenshot is identical with and without focus; `needs-human` stays only where the screenshots do not decide; a new run on the practice site with the finding on the 4 pages and in the 3 engines. |
| 2026-09-26 | 8.17 defined (what was left from the first real use): `focus-visible` decided by comparing pixels; checked on the practice site's public login, identical pixels with and without focus in the three engines. |
| 2026-09-26 | 8.16: fixes from the first real use (SameSite false positive in Firefox/WebKit, SPA pages as broken links) and `visual` out of `full` (user decision). |
| 2026-09-26 | `docs/qa-audit.md` §12: scripted flows in the engine refused (they stay in the workspace's specs, audited with `qa-audit check`); active security only with a concrete need (user decisions). Next: use the engine on the public practice site before new features. |
| 2026-09-26 | 8.15 (`qa-audit check`, auditing states in the middle of N2 specs) done by the user's decision (option c). |
| 2026-09-26 | `qa-audit` 0.2.0 (tag `qa-audit-v0.2.0`): 8.5–8.10 and 8.12–8.14; `CHANGELOG` reorganised (Added / Changed / Fixed). |
| 2026-09-26 | 8.14 (several browsers in one run) done by the user's decision; performance out of the comparison (noise between browsers). |
| 2026-09-26 | 8.13 (sitemap discovery) done by the user's decision; off by default (`--sitemap`). |
| 2026-09-26 | 8.12 (saved sessions, `storage-state`) done by the user's decision; `/audit-accessibility` now runs on `qa-audit` and `tools/a11y/` is retired (notes of 8.10). |
| 2026-09-26 | 8.10 (login) merged into `main` after the pilot (branch `feat/login`, green CI). |
| 2026-09-25 | 7.9: **v1.0** of the workspace layer (tag `v1.0`); README with the final state. |
| 2026-09-25 | Pilot (2.4 / 7.2) on a public practice site, by the user's decision (no access to company projects): batch applied, real golden OK, lessons incorporated (`--isolated`, `--secrets`, the `.mcp.json` warning in `validate_project`, the golden's `label`, the `commit-msg` guard). |
| 2026-09-25 | User decisions: v1.0 is only the workspace layer (`qa-audit` has its own version); translation (8.11) not now; the detect-secrets exception kept and explained in `axe.yaml` itself. |
| 2026-09-25 | 8.10 (login `form` and `manual`, `crawl.pages`) by the user's decision; `storage-state` left for later; the translation becomes 8.11. |
| 2026-09-25 | User decision: the detect-secrets exception is kept (allowlist pragma only on the two hash lines of `src/qa_audit/modules/accessibility/vendor/axe.yaml`; a test prevents it anywhere else). Public integrity keys, not secrets; no file excluded from the sweep. |
| 2026-09-25 | 8.9 done: install scripts, user guide, fictional example, `install` job in CI. |
| 2026-09-25 | 8.8 done: visual module; baselines outside any repository and the `qa-audit baselines` command. |
| 2026-09-25 | 8.7 done: functional module; forms submitted only with `--allow-submit` and a listed form. |
| 2026-09-25 | 8.6 done: performance module with the web.dev thresholds and a metrics table; order of the modules per page. |
| 2026-09-25 | 8.5 done: passive security module; `doctor` tests each browser's cryptography. |
| 2026-09-25 | Phase 8 (`qa-audit`) added by the user's decision: an automated audit engine alongside the workspace layer, in English, a private and proprietary repository; revises decision 7.7 about `qa_core`/CLI. Specification in `docs/qa-audit.md`. |
