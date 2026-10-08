# qa-assistant

[![CI](https://github.com/palmeida98/qa-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/palmeida98/qa-assistant/actions/workflows/ci.yml)

A QA framework for working with [Claude Code](https://claude.com/claude-code): the methodology, the test techniques and the tools live here, and each real project lives in a separate **workspace**, outside this repository. The assistant follows the methodology through skills (`/project-onboarding`, `/run-batch`, `/checkpoint`, …) and writes the results into the project's tracker through adapters checked by tests.

Everything is in English (plan 8.11). A workspace created before the translation is converted once with `python scripts/migrate_workspace.py <workspace>`.

---

## qa-audit — automated audit engine

An automated first pass over any website, from its URL alone. It lives alongside the workspace layer.

```bash
./scripts/install.sh            # Windows: .\scripts\install.ps1  (.venv, package, Chromium, then qa-audit doctor)
. .venv/bin/activate            # Windows: .\.venv\Scripts\Activate.ps1
qa-audit https://example.com                         # every available module
qa-audit https://example.com --type accessibility --browser firefox --max-pages 5
```

It can log in first (a form, or you log in by hand for Active Directory / 2FA). It crawls the start origin with GET navigation only (never clicks, types or submits; skips logout/delete-style
links, respects `robots.txt`), runs the selected modules on each page, and writes `qa-output/<timestamp>/` with
screenshots, `results.json`, `results.csv`, `report.md` and a self-contained `report.html`. No telemetry; nothing
is sent anywhere but to the target. Only audit sites you own or are authorized to test.

Modules today: **accessibility** (axe-core 4.13.0 bundled, keyboard walk with focus visibility decided by pixels,
reflow, manual checklist) and **security** (passive: HTTPS/TLS, security headers, cookie flags, mixed content) and
**performance** (TTFB, FCP, LCP, CLS against the web.dev thresholds; sizes and timings measured) and **functional**
(broken links and resources, JavaScript errors, redirect chains, form checks; forms are submitted only with
`--allow-submit` and when listed in the configuration) and **visual** (screenshots per viewport against baselines
kept outside any repository, horizontal overflow; `pip install -e .[visual]`). Pages behind a login: a login form,
a person logging in (Active Directory, second factor), or a session saved once with `qa-audit login`. User guide:
[`docs/qa-audit-guide.md`](docs/qa-audit-guide.md) (install, run, results, new projects, new modules, client data);
example: [`examples/qa-audit/`](examples/qa-audit/). Specification: [`docs/qa-audit.md`](docs/qa-audit.md);
contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md); changes: [`CHANGELOG.md`](CHANGELOG.md).

---

## Layered architecture

| Layer | What it is | Where it lives |
|---|---|---|
| **1. Methodology** | The method: analysis, case design, batches, decision rules (Pass/Fail/N/A/Blocked/Question), bugs, checkpoints; anonymised heuristics for *error guessing* | [`docs/methodology.md`](docs/methodology.md), [`docs/heuristics.md`](docs/heuristics.md) |
| **2. Techniques** | How to test each type: [browser](docs/technique-browser.md), [API](docs/technique-api.md), [accessibility](docs/technique-accessibility.md), [mobile](docs/technique-mobile.md), [Excel workbooks](docs/technique-excel.md) | `docs/` |
| **3. Adapters and tools** | Universal Excel adapter, scripts for API, accessibility, mobile, dashboard and validation; skills. They are the framework's engine: there is no separate core | `adapters/`, `scripts/`, `.claude/skills/` |
| **4. Workspace** | The project: `CLAUDE.md`, `project.yaml`, `context.md`, workbook, `.env`, run log | `qa-workspaces/<company>/<project>/` (outside this repository) |

The hierarchy of sources of truth, the generalisation rule (what is universal vs. what belongs to a project) and the process for changing the framework are in the methodology (§1.3, §1.4, §15).

## Privacy boundary

Nothing that identifies a client enters this repository: names, environment URLs, accounts, credentials, workbooks, results or bugs of real projects. Two barriers in `pre-commit`, never worked around:

- **detect-secrets**, with a baseline;
- **personal denylist** (`~/.qa-denylist.txt`, outside the repository): blocks client names in the text, in the file names and **inside** Office files (`.xlsx`, `.docx`, `.pptx`), and in the commit message.

Credentials live only in each workspace's `.env` and are referred to by the variable's name. The scripts pass them through environment variables, never on the command line, and redact the evidence (e.g. `scripts/api_request.py` replaces tokens and passwords with `«redacted»`). In the workspace, `validate_project` warns if `.env` and the like are not in `.gitignore`, and `.claude/settings.json` forbids Claude Code from editing `history/`, which is immutable.

## How to work

```mermaid
flowchart LR
  O["/project-onboarding<br/>(once)"] --> A["/analyze-feature<br/>/generate-cases"]
  A --> P["/plan-batch"]
  P --> E["/run-batch · /test-api<br/>/audit-accessibility · /test-mobile"]
  E -->|"run log<br/>runs/batch_*.json"| C["/checkpoint<br/>(apply to the workbook)"]
  C -->|next batch| P
  C --> F["/end-session · /dashboard"]
```

The generated cases are proposals until the user approves them; a bug is only created once approved; the results go to the workbook only at the checkpoint, through the adapter, and only if the batch is valid and the cross-check gives 0 problems.

### The local interface (`qa-ui`)

```bash
python -m qa_ui ../qa-workspaces/<company>/<project>      # opens http://127.0.0.1:8765/?token=…
```

The same cycle as a page on this computer, for one workspace: paste cases (or give a `.csv`, `.xlsx`, `.docx`, or load
the project's library), see which type of test the rules suggest for each case and why, plan the batches, start one
(the module's skill in Claude Code headless, with your subscription) and follow the run log as it grows. What needs
you waits in the page: a proposed bug to approve, edit, link or drop, a `Question` to answer. An approved run log is
applied through the project's adapter after a confirmation. Loopback only, a token per start, no request to any
other address, nothing written in this repository; it never commits (`/checkpoint` still does). A batch started here
is unattended, so the rules of a remote run hold (methodology §9.4). Specification: [`docs/qa-ui.md`](docs/qa-ui.md).

### Starting a new project

1. Create the folder `qa-workspaces/<company>/<project>/` (next to this repository).
2. Open `claude` in that folder and run `/project-onboarding`: it creates `CLAUDE.md`, `project.yaml` and `.claude/settings.json` (protection of `history/`), asks about environments, roles, tracker and modules, inspects the real workbook (`python -m adapters.excel.inspect`) or, with Jira or Azure DevOps, fills in the tracker's section and runs `check` and `import-cases`, registers the Playwright MCP with a fixed version and ends with `python scripts/validate_project.py <workspace>` without problems (it also checks that the modules' URLs belong to declared test environments).
3. Fill in `.env` locally.

### Modules

| Module | Technique | Skill | Tool |
|---|---|---|---|
| `functional-web`, `exploratory` | browser | `/run-batch` | Playwright MCP |
| `api` | API | `/test-api` | `scripts/api_request.py` |
| `accessibility` | WCAG 2.1 AA | `/audit-accessibility` | `scripts/audit_a11y.py` (`qa-audit` engine: axe-core, keyboard, reflow) |
| `mobile` | Maestro | `/test-mobile` | `scripts/run_mobile.py` |

### Trackers

Each tracker has an adapter. Excel: the results and the bugs go to the workbook at `/checkpoint`, the cases approved in `/generate-cases` are added by the adapter (`python -m adapters.excel.add_cases`, never by hand), and there is a dashboard. Jira with Xray and Azure DevOps Test Plans (`python -m adapters.tracker`, specified in `docs/technique-jira-xray.md` and `docs/technique-azure-devops.md`): results, bugs (with a duplicate search), importing and exporting cases — always a dry run first and `--yes` only after the user's approval, a batch never sent twice; the skills use them the same way (`/plan-batch` and `/run-batch` read the imported cases, `/log-bug` searches duplicates, `/checkpoint` sends, `/generate-cases` exports); their progress view is the tracker's own reports (methodology §4.2).

## Demo

[`examples/demo/`](examples/demo) is a complete workspace against public practice sites ([saucedemo.com](https://www.saucedemo.com) and the [restful-booker](https://restful-booker.herokuapp.com) API), with 38 cases in 6 areas, batches run in `runs/`, 3 example bugs, Maestro flows and Playwright regression specs (`e2e/`). The workbook is rebuilt from the builder and the batches (`python examples/demo/rebuild_workbook.py`), with each batch's execution date, and always gives the same cells (the CI checks it against the versioned workbook); `python scripts/build_dashboard.py examples/demo --open` shows the progress dashboard.

## Tests and CI

- `python -m unittest discover -s tests` — the unit tests (adapter, inspection, recalculation, dashboard, summary, API, accessibility, mobile, validation, workspace migration, the scripts' common code, denylist, guards, skills, console, language, reproducible demo, real golden, and the `qa-audit` engine's unit tests). Those that need LibreOffice are *skipped* without it. The `qa-audit` integration tests (a real browser against a local site) run with `python -m unittest discover -s tests/integration`.
- `python scripts/golden_check.py` — golden test of the Excel adapter (needs LibreOffice). With `--dir <workspace>/golden/<batch>` it also runs a project's **real** golden: a historical batch applied to a copy of the workbook from before must give, cell by cell, the workbook saved afterwards (the data stays in the workspace).
- The CI (GitHub Actions, Ubuntu 24.04) runs 7 jobs on each push: `lint` (ruff), `tests`, `secrets` (detect-secrets), `golden` (golden, summary with real formulas, reproducible demo and real golden with fictional data, with LibreOffice), `demo-e2e` (the demo's Playwright specs against the public site), `qa-audit` (engine integration in Chromium, Firefox and WebKit) and `install` (clean clone, install, audit of the local site). `pre-commit` runs the same locally, plus the denylist on the files and on the commit message.
- Fixed versions: exact Python dependencies (`requirements.txt`), ruff, detect-secrets and Playwright MCP; a test prevents going back to loose versions.

The skills run from the global copy in `~/.claude/skills/` (the workspaces live outside this repository): after changing a skill, `python scripts/sync_skills.py --apply` (it also removes the skills' old names).

Requirements: Python 3.12+ (`pip install -r requirements.txt`), LibreOffice (workbook recalculation), Node.js 22+ (accessibility and specs), Maestro CLI (mobile).

## State

**v1.0** (25.09.2026): the workspace layer — methodology, techniques, skills, Excel adapter, dashboard and a project's full flow — is done and validated end to end in a pilot. The `qa-audit` engine (above) has its own version (0.2.0, see [`CHANGELOG.md`](CHANGELOG.md)). The detail of each task, with criteria and commits, is in [`docs/development-plan.md`](docs/development-plan.md).

| Phase | State |
|---|---|
| 0–1 · Base and core (skills, Excel adapter, golden) | Done |
| 2 · Browser (Playwright MCP, demo, N2 regression, pilot) | Done |
| 3 · API, accessibility and mobile modules | Done |
| 4 · The framework's CI | Done |
| 5 · Dashboard per workspace | Done |
| 6 · Portfolio (README, demo, visibility: private) | Done |
| 7 · Closing (general review, CI on Linux, pilot, real golden, protected `history/`, fixed versions, reproducible demo) | Done: **v1.0** |
| 8 · `qa-audit` engine and translation | 8.0–8.17 (0.2.0: login, saved sessions, sitemap, several browsers; `check`, focus by pixels; 8.11 everything in English) |
| 12 · `qa-ui`, the local web interface | Built and tested against fakes (08.10.2026); the first batch with a real Claude Code session and a real site is still to run |

**Pilot** (25.09.2026): in a workspace outside the repository, with a public practice site (no access to company projects). A batch of 16 cases, 10 new and 6 retests, run by a `claude` session in the workspace with the Playwright MCP and applied to the workbook at `/checkpoint`: the retests gave the same status as before; real golden passing; protection of `history/` confirmed. Lessons incorporated: Playwright MCP with `--secrets .env` (the assistant never sees the credentials) and `--isolated` (the persistent profile lost clicks), warnings of `validate_project` for `.mcp.json`, and the denylist also on the commit message.

Scope decisions: the adapters and the scripts are the workspace layer's engine, and `qa-audit` is a separate engine, next to it (25.09.2026); the `executor-web` subagent is not created (the pilot ran in its own session without filling the main context); Jira and Azure DevOps stayed as manual logging until the user's decision of 28.09.2026 to build their adapters (plan phase 10).
