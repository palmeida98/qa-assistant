# QA Assistant

References:
- @docs/methodology.md — universal methodology
- docs/skills-spec.md — the eight skills, what each owns, their contract and helpers (read when touching `.claude/skills/` or `docs/procedures/`); guide for people: docs/skills-guide.md
- docs/procedures/ — the long procedures the skills read (`rules.md` is shared by all of them)
- docs/technique-browser.md — browser automation technique (read when running browser batches; `/run-tests`, `/project-context`)
- docs/technique-excel.md — Excel workbook automation technique (read when touching the adapter or applying batches; `/checkpoint`, `/manage-bugs`)
- docs/technique-jira-xray.md — Jira + Xray tracker adapter technique, with the common contract of the tracker adapters in §2 (read when `tracker: jira`, or when touching `adapters/tracker/` or `adapters/jira/`; built in plan 10.2)
- docs/technique-azure-devops.md — Azure DevOps (Test Plans + Boards) tracker adapter technique (read when `tracker: azure-devops`, or when touching `adapters/azure_devops/`; built in plan 10.3)
- docs/technique-workspaces.md — finding the framework, selecting a project, isolation, the skills' contract and sync (read when a skill's preflight fails, or when touching `scripts/workspace_select.py`, `scripts/sync_skills.py` or a skill's frontmatter; plan phase 15)
- docs/technique-tickets.md — read-only ticket analysis in Azure DevOps and Jira, what changed and the workspace's `memory/` (read for `/analyze-ticket`, or when touching `adapters/tickets/`; plan phases 14 and 15)
- docs/technique-api.md — API testing technique (read when the `api` module is active; `/run-tests`)
- docs/technique-accessibility.md — WCAG 2.1 AA accessibility technique (read when the `accessibility` module is active; `/run-tests`)
- docs/technique-mobile.md — mobile technique with Maestro (read when the `mobile` module is active; `/run-tests`)
- docs/heuristics.md — anonymised failure patterns for error guessing (methodology §17; read when generating cases)
- docs/development-plan.md — the framework's roadmap (read in development sessions)
- docs/qa-audit.md — `qa-audit` automated audit engine (read when working on `src/qa_audit`); user guide: docs/qa-audit-guide.md
- docs/tracker-validation.md — what each tracker (Excel, Azure DevOps, Jira + Xray) has implemented, tested with fakes and piloted for real, and what a real pilot needs (read when touching a tracker adapter or reporting its state)
- docs/demo-walkthrough.md, docs/skills-evals.md — the reproducible fictional walkthrough (run by a test) and the agent-behaviour scenarios prepared but not yet run

## Fixed rules
- The product is the eight skills used through Claude Code. It has no hub, web interface, graphical application or launcher of its own, and none is promised for a later phase (decision of 08.11.2026; the earlier `qa-ui` was removed and stays in the git history). Reports a command produces (HTML, Excel) are outputs, not an interface.
- Never write client data (URLs, accounts, names, credentials) in this repository.
- Follow the hierarchy of sources of truth of the methodology (section 1.3).
- A project workspace always lives outside this repository, under the root of workspaces (qa-workspaces/ by default).
- Before any change to the framework, follow the validation process (methodology section 15).
- Everything in this repository is in English (plan 8.11); a project's own names (sheets, columns, statuses) are never translated.

## Scripts (from the root of qa-assistant)
Each script's docstring and `--help` give its flags and exit codes: read them before using one. What they do:
- `scripts/validate_project.py <workspace> [--environment]` — validates `project.yaml` and, with `--environment`, the tools this computer needs
- `scripts/workspace_git.py <workspace> init|status|remote|push` — the workspace as its own git repository with a private remote; `push` never forces and refuses a public remote
- `scripts/migrate_workspace.py <workspace> [--apply]` — converts a workspace from before the translation (plan 8.11)
- `python -m adapters.excel.inspect|add_cases|apply` — the Excel adapter: propose the `excel:` section, add approved cases, apply a batch (`apply --dry-run` first: the cells it would change and a preview token; `apply --expect <token>` refuses a stale preview)
- `python -m adapters.tracker <workspace> check|import-cases|export-cases|apply|bug-search` — the Jira (Xray) / Azure DevOps adapter; every writing command is a dry run without `--yes`, and `--yes` only after the user approved what it showed
- `python -m adapters.tickets <workspace> sources|collect|ingest|analysis` — `/analyze-ticket`: reads Azure DevOps or Jira (a transport that cannot write), compares with the last check, writes only the workspace's `memory/` and a block of `context.md`
- `scripts/compact_context.py <workspace> [--apply]` — keeps a workspace's `context.md` short: older dated entries go word for word to `archive/context/`, after a copy of the whole file (dry run by default)
- `scripts/api_request.py`, `scripts/audit_a11y.py`, `scripts/run_mobile.py`, `scripts/device.py`, `scripts/run_log.py` — the API, accessibility and mobile techniques and the run log (`device.py check` is the one report of the target, §1.3; `device.py` with `mobile.input: driver` for a device that refuses `adb shell input`, §1.2; `run_log.py add|remove|mode`: a result only for a case of the library, the run block and the `mode` immutable once the batch has results; `device.py --platform ios doctor`: why an iPhone is not ready, read only; `device.py send --burst` / `shot-burst`: timed screenshots with a manifest)
- `docs/pilot-improvements-2026-11-07.md` — the five improvements from the iOS pilot (Excel dry run, run log mode, iOS diagnosis, focus before typing, streaming evidence)
- `scripts/build_driver.py` — builds the companion driver `tools/android-driver/`; its signing key lives in `~/.qa-assistant/`, never in a repository
- `scripts/setup_emulator.py`, `scripts/workspace_build.py`, `scripts/workspace_ci.py`, `scripts/remote_login.py` — the emulator, builds as releases, and remote execution (phase 11; `docs/workspace/ci/README.md`)
- `scripts/build_dashboard.py <workspace> [--open]` — the workspace's `dashboard.html` (`/qa-report`, Excel only)
- `scripts/workspace_select.py where|list|resolve|preflight|skills|set-root` — which framework really runs (and the commit guards: hooks installed, private denylist configured), the projects under the root with their status, which one a request means, and whether a skill can run there (read only, apart from `set-root`)
- `scripts/qa_report.py <workspace> totals|audit` — reconciled totals per platform and build, and the audit of run logs, evidence and application (`/qa-report`, `/checkpoint`; read only)
- `scripts/pbi_state.py <workspace> start|show|mark|baseline|check|published` — the resume record of `/test-pbi` (`memory/pbi/<id>.json`)
- `python scripts/sync_skills.py --apply` — run after changing a skill: the workspaces use the global copy in `~/.claude/skills/`, and the record next to it says which checkout it came from
- `qa-audit …` (`pip install -e .` first; `qa-audit doctor`, or `python -m qa_audit doctor` when the `qa-audit` launcher points at a Python that is gone), `scripts/update_axe.py` — the audit engine (docs/qa-audit.md)
- Tests: `python -m unittest discover -s tests` and `python scripts/golden_check.py` (needs LibreOffice); a workspace's real golden: `--dir <workspace>/golden/<batch>` (methodology §15). Engine integration tests: `python -m unittest discover -s tests/integration` (browsers must be able to start: outside the Bash sandbox)

## Environment
Personal repository. Workspaces live under a root of workspaces, by default ../qa-workspaces/ next to the main checkout, as <company>/<project>/ or <project>/ (docs/technique-workspaces.md §3 and §4).
