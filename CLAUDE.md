# QA Assistant — framework repository

## Purpose

- This repository is the **universal library** that QA work in project workspaces runs on: the nine skills
  (`.claude/skills/`), the procedures and techniques they read (`docs/`), and the tested scripts and adapters they call
  (`scripts/`, `adapters/`). It also hosts `qa-audit` (`src/qa_audit`), a standalone website audit engine.
- It is used **from the workspaces**, never the other way round. A workspace session runs the global copy of the skills
  in `~/.claude/skills/` (installed by `scripts/sync_skills.py`) and finds this checkout through `$QA_ASSISTANT_ROOT` or
  the sync record (`docs/technique-workspaces.md` §2). This file is **not** loaded there.
- A session opened here is a **framework development session**: changing skills, procedures, techniques, scripts,
  adapters, tests or docs, always for every project.
- QA work on a project (analyse a ticket, plan, run, report, checkpoint) is **not** done here: point the user to a
  session opened in that project's workspace (`python scripts/workspace_select.py list`).
- `examples/demo/` is the only workspace inside this repository: fictional, used to develop and validate the framework.
- The product is the nine skills used through Claude Code: no hub, web interface or launcher of its own (decision of
  08.11.2026). The HTML and Excel files a command produces are outputs, not an interface.

## Fixed rules

1. **No client data, ever** (methodology §1.2): no client, company or project names, URLs, accounts, credentials,
   workbooks, results, bugs, evidence or real test cases. Examples use the fictional Demo Shop and `example.com`-style
   hosts. A block by the pre-commit guards (detect-secrets, the private denylist) is fixed in the content, never
   bypassed (`--no-verify`).
2. **Universal only** (methodology §1.4): one project's need goes to its workspace; several projects' need becomes
   configuration (a `project.yaml` option, an adapter, a module), never a special case. A heuristic enters
   `docs/heuristics.md` only anonymised (§17).
3. **Validation before adoption** (methodology §15): specification (Markdown) first, then code, then tests, then
   commit. A change is not safe because it looks logically right.
4. **Sources of truth**: methodology §1.3. Claude's memory is not one.
5. **Workspaces live outside this repository** (root resolution: `docs/technique-workspaces.md` §3). Development never
   reads or writes a real workspace unless the user names it for that task (e.g. a real golden). Tests use fictional
   fixtures only, never a real workspace or the real `~/.claude`.
6. **English** for everything in the repository, commit messages included. A project's own names (sheets, columns,
   statuses) are never translated. Talk to the user in their own language.
7. **Git and installation only when asked**: commit, push, merge and `sync_skills.py --apply`. Never write to
   `docs/history/` (dated, reference only).

## Map

| Where | What |
|---|---|
| `.claude/skills/<skill>/SKILL.md` | The nine skills; the contract is in the frontmatter |
| `docs/README.md` | Documentation index: which document answers which need |
| `docs/skills-spec.md`, `docs/skills-guide.md` | What each skill owns and its contract; the guide for people |
| `docs/procedures/` | Long procedures the skills read; `rules.md` is shared by all nine, `<skill>-detail.md` holds a skill's long steps |
| `docs/technique-*.md` | Technical how-to per module, tracker and concern |
| `docs/project.yaml`, `docs/context-template.md`, `docs/workspace/` | Templates copied into a workspace (settings, `gitignore`, setup, resume, CI, tracker skill) |
| `adapters/` | `excel/`, `tracker/` (common base), `jira/`, `azure_devops/`, `tickets/` (read only), `case_headers.py` |
| `scripts/` | Deterministic helpers the skills call; `common.py` holds workspace paths and the writer lock, `output.py` the short card / `--detail` output |
| `src/qa_audit/` | The audit engine (`docs/qa-audit.md`) |
| `tools/` | `android-driver/` (companion driver, built by `build_driver.py`), `playwright/` (audit helper) |
| `tests/` | Framework unit tests; `unit/` (engine), `golden/` (Excel, Jira, Azure DevOps), `fixtures/`, `integration/` (engine, real browsers) |
| `examples/demo/` | The fictional demo workspace; used by tests and `docs/demo-walkthrough.md` |
| `docs/development-plan.md`, `CHANGELOG.md`, `docs/history/` | Roadmap and task state, user-visible changes, dated history |

## Read when needed

Read the document before changing what it governs; do not rely on a summary here.

- The method itself (statuses, run log, evidence, bugs, approvals, checkpoints, `context.md`) or any change to a
  universal layer: `docs/methodology.md` (not imported here; §0 says why). Its §1.2, §1.3, §1.4 and §15 back the
  fixed rules above.
- Any change to a skill or procedure: `docs/skills-spec.md` (§3 shared rules, §4 contract, §6 contract tests)
- Selection, discovery, preflight, writer lock, contract keys, sync: `docs/technique-workspaces.md`
- Setup, pre-commit guards, the denylist, adding an audit module, dependencies: `CONTRIBUTING.md`
- CI jobs, what "green" means, what is not covered: `docs/ci.md`
- Test modules (methodology §4.1): `technique-browser.md` (also the specs of `/playwright-tests`), `technique-api.md`,
  `technique-accessibility.md`, `technique-mobile.md` (also the flows of `/maestro-tests`)
- Trackers: `technique-excel.md`, `technique-jira-xray.md` (§2: common contract of tracker adapters),
  `technique-azure-devops.md`, `docs/tracker-validation.md` (what each has really been piloted on)
- Tickets and specifications: `technique-tickets.md`, `technique-specs.md`
- Guards: `technique-destructive-guard.md` (PreToolUse hook), `technique-ado-write-guard.md` (the only Azure DevOps write path)
- The engine: `docs/qa-audit.md` (spec, changed in the same commit as the behaviour), `docs/qa-audit-guide.md`
- Agent behaviour: `docs/skills-evals.md` (scenarios and rounds); `docs/demo-walkthrough.md` is run by a test

## Changing a skill or a procedure

1. Spec first: the contract of `docs/skills-spec.md` §4 (frontmatter keys; `requires-docs` and `requires-scripts`
   name every document and script used; step 0 written in each skill; the folder holds only `SKILL.md`).
2. A rule shared by several skills goes once to `docs/procedures/rules.md`; long steps to
   `docs/procedures/<skill>-detail.md`. Each `SKILL.md` stays within the word budget of `skills-spec.md` §4
   (`wc -w`; several are at the limit).
3. Check: `python -m unittest tests.test_skills tests.test_skill_flows`, then the whole suite.
4. Workspaces see the change only after a sync: `python scripts/sync_skills.py` (dry run) and, when the user asks,
   `--apply` from a clean checkout of the merged `main` (a worktree is refused without `--allow-worktree`). Confirm with
   `python scripts/workspace_select.py where`.

## Scripts (run from the repository root)

Read each script's docstring and `--help` before using it (flags, exit codes). `python` below is the interpreter
`python3 scripts/qa_python.py` prints; `python3 scripts/qa_python.py run <script|-m module> ...` runs with it.

- **Framework and selection**: `workspace_select.py where|list|resolve|preflight|skills|set-root`, `sync_skills.py`,
  `workspace_identity.py`, `workspace_init.py <project>` (a new workspace in the project folder layout), `validate_project.py <ws> [--environment]`, `migrate_workspace.py`, `workspace_git.py`,
  `install.sh` / `install.ps1`
- **Run records**: `run_log.py` (results, evidence, times, Questions, bugs; examples in `docs/skills-guide.md`),
  `approvals.py`, `quarantine.py`, `spec_refs.py`, `ac_coverage.py`, `check_approval_files.py`, `compact_context.py`,
  `pbi_state.py` (no skill creates a PBI record since `/test-pbi` was removed on 24.11.2026; `/checkpoint` and `/manage-bugs` only update one that exists)
- **Reports**: `qa_report.py <ws> totals|audit|report`, `build_dashboard.py`
- **Adapters**: `python -m adapters.excel.inspect|add_cases|add_columns|annotate|reconcile|apply`,
  `python -m adapters.tracker <ws> check|import-cases|export-cases|apply|bug-search`,
  `python -m adapters.tickets <ws> sources|collect|ingest|analysis`; every write is a dry run first (Excel: preview
  token and `--expect`; trackers: `--yes` only after the user approved the dry run)
- **Execution**: `pw_cli.py` (Playwright CLI door; `check`/`run`), `api_request.py`, `audit_a11y.py`, `device.py` (Android; iOS through `device_ios.py`,
  `ios_diagnose.py`), `run_mobile.py`, `setup_emulator.py`, `apk_manifest.py`, `build_driver.py`, `workspace_build.py`,
  `workspace_ci.py`, `remote_login.py`, `build_candidates.py` (with `slack_build_messages.py`, offline)
- **Guards**: `guard_destructive.py`, `ado_write_guard.py` (started by `ado_guard_launch.py <workspace>`), `check_denylist.py`; `archive_check.py` lists what `_arquivo/` holds without a reason
- **Engine and CI**: `qa-audit ...` (`pip install -e .`; `qa-audit doctor`), `update_axe.py`, `ci_test_summary.py`

## Tests and checks

- `python -m unittest discover -s tests` — the unit suite (also in pre-commit and CI)
- `python scripts/golden_check.py` — fictional Excel golden (needs LibreOffice); a real golden only with
  `--dir <workspace>/golden/<batch>` when the user names it
- `python -m unittest discover -s tests/integration` — engine against the fixture site (browsers must start: outside
  the Bash sandbox)
- `ruff check .` — lint; `python scripts/check_denylist.py <files>` — the privacy guard on given files
- The hooks are shared by every worktree; `workspace_select.py where` shows them under "Commit guards".
- Before saying a change is done, run what it touches and report the real output.

## Commits

- One task per commit. A task of `docs/development-plan.md` is `<phase>.<task>: <summary>`; other work is
  `<area>: <summary>` or a short imperative summary, in English.
- A dependency update is a commit of its own (exact pins in `pyproject.toml` and `requirements.txt`).
- Record user-visible changes in `CHANGELOG.md` and the state of a plan task in `docs/development-plan.md`.
