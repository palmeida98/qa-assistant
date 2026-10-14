# QA Assistant

References:
- @docs/methodology.md — universal methodology
- docs/technique-browser.md — browser automation technique (read when running browser batches; `/run-batch`, `/project-onboarding`)
- docs/technique-excel.md — Excel workbook automation technique (read when touching the adapter or applying batches; `/checkpoint`, `/log-bug`)
- docs/technique-jira-xray.md — Jira + Xray tracker adapter technique, with the common contract of the tracker adapters in §2 (read when `tracker: jira`, or when touching `adapters/tracker/` or `adapters/jira/`; built in plan 10.2)
- docs/technique-azure-devops.md — Azure DevOps (Test Plans + Boards) tracker adapter technique (read when `tracker: azure-devops`, or when touching `adapters/azure_devops/`; built in plan 10.3)
- docs/technique-api.md — API testing technique (read when the `api` module is active; `/test-api`)
- docs/technique-accessibility.md — WCAG 2.1 AA accessibility technique (read when the `accessibility` module is active; `/audit-accessibility`)
- docs/technique-mobile.md — mobile technique with Maestro (read when the `mobile` module is active; `/test-mobile`)
- docs/heuristics.md — anonymised failure patterns for error guessing (methodology §17; read when generating cases)
- docs/development-plan.md — the framework's roadmap (read in development sessions)
- docs/qa-audit.md — `qa-audit` automated audit engine (read when working on `src/qa_audit`); user guide: docs/qa-audit-guide.md
- docs/qa-ui.md — `qa-ui` local web interface for a workspace: intake, suggested types of test, batch plan, runs, approvals (read when working on `qa_ui/`; plan phase 12)

## Fixed rules
- Never write client data (URLs, accounts, names, credentials) in this repository.
- Follow the hierarchy of sources of truth of the methodology (section 1.3).
- A project workspace always lives outside this repository, in qa-workspaces/.
- Before any change to the framework, follow the validation process (methodology section 15).
- Everything in this repository is in English (plan 8.11); a project's own names (sheets, columns, statuses) are never translated.

## Scripts (from the root of qa-assistant)
Each script's docstring and `--help` give its flags and exit codes: read them before using one. What they do:
- `scripts/validate_project.py <workspace> [--environment]` — validates `project.yaml` and, with `--environment`, the tools this computer needs
- `scripts/workspace_git.py <workspace> init|status|remote|push` — the workspace as its own git repository with a private remote; `push` never forces and refuses a public remote
- `scripts/migrate_workspace.py <workspace> [--apply]` — converts a workspace from before the translation (plan 8.11)
- `python -m adapters.excel.inspect|add_cases|apply` — the Excel adapter: propose the `excel:` section, add approved cases, apply a batch
- `python -m adapters.tracker <workspace> check|import-cases|export-cases|apply|bug-search` — the Jira (Xray) / Azure DevOps adapter; every writing command is a dry run without `--yes`, and `--yes` only after the user approved what it showed
- `scripts/api_request.py`, `scripts/audit_a11y.py`, `scripts/run_mobile.py`, `scripts/device.py`, `scripts/run_log.py` — the API, accessibility and mobile techniques and the run log (`device.py` with `mobile.input: driver` for a device that refuses `adb shell input`, technique-mobile.md §1.2)
- `scripts/build_driver.py` — builds the companion driver `tools/android-driver/`; its signing key lives in `~/.qa-assistant/`, never in a repository
- `scripts/setup_emulator.py`, `scripts/workspace_build.py`, `scripts/workspace_ci.py`, `scripts/remote_login.py` — the emulator, builds as releases, and remote execution (phase 11; `docs/workspace/ci/README.md`)
- `scripts/build_dashboard.py <workspace> [--open]` — the workspace's `dashboard.html` (`/dashboard`)
- `python scripts/sync_skills.py --apply` — run after changing a skill: the workspaces use the global copy in `~/.claude/skills/`
- `qa-audit …` (`pip install -e .` first; `qa-audit doctor`), `scripts/update_axe.py` — the audit engine (docs/qa-audit.md)
- `python -m qa_ui <workspace>` — the local web interface (docs/qa-ui.md)
- Tests: `python -m unittest discover -s tests` and `python scripts/golden_check.py` (needs LibreOffice); a workspace's real golden: `--dir <workspace>/golden/<batch>` (methodology §15). Engine integration tests: `python -m unittest discover -s tests/integration` (browsers must be able to start: outside the Bash sandbox)

## Environment
Personal repository. Each company's workspaces live in ../qa-workspaces/<company>/<project>/.
