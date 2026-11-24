# QA Assistant — framework repository

@docs/methodology.md

## What this repository is (and is not)

- The **universal library** that QA work in project workspaces runs on: eight skills (`.claude/skills/`), the
  procedures and techniques they read (`docs/`), and the tested scripts and adapters they call (`scripts/`,
  `adapters/`). It also hosts `qa-audit` (`src/qa_audit`), a standalone website audit engine.
- It is used **from the workspaces**, never the other way round. A workspace session runs the global copy of the
  skills in `~/.claude/skills/` (installed by `scripts/sync_skills.py`); each skill finds this checkout through
  `$QA_ASSISTANT_ROOT` or the sync record and runs its preflight. This `CLAUDE.md` is **not** loaded there.
- So a session opened here is a **framework development session**: changing skills, procedures, techniques,
  scripts, adapters, tests or docs, always for every project.
- QA work on a project (analyse a ticket, plan, run, report, checkpoint) is **not** done here. Point the user to a
  session opened in that project's workspace (`python scripts/workspace_select.py list`). The only workspace inside
  this repository is the fictional demo `examples/demo/`, used to develop and validate the framework.
- The product is the eight skills used through Claude Code. No hub, web interface, application or launcher of its
  own, none promised (decision of 08.11.2026; the removed `qa-ui` stays in git history). Reports a command produces
  (HTML, Excel) are outputs, not an interface.

## Fixed rules

1. **No client data, ever**: no client, company or project names, URLs, accounts, credentials, workbooks, results,
   bugs, evidence or real test cases. Examples use the fictional Demo Shop and `example.com`-style hosts. A block by
   the pre-commit guards (detect-secrets, the private denylist `~/.qa-denylist.txt`) is fixed in the content, never
   bypassed (`--no-verify`).
2. **Universal only** (methodology §1.4): what one project needs goes to its workspace; what several need becomes
   configuration (`project.yaml` option, adapter, module), never a special case for a named project. A workaround
   seen in one project is not universal until shown in another; a heuristic enters `docs/heuristics.md` only
   anonymised (§17).
3. **Validation before adoption** (methodology §15): specification (Markdown) first, then code, then tests, then
   commit. A change is not safe because it looks logically right.
4. **Sources of truth**: methodology §1.3. Claude's memory is not one.
5. **Workspaces live outside this repository**, under the root of workspaces (resolved by
   `scripts/workspace_select.py`: `--root`, `$QA_WORKSPACES_ROOT`, `~/.qa-assistant/config.json`, then
   `qa-workspaces/` next to the main checkout). Development never reads or writes a real workspace unless the user
   names it for that task (e.g. a real golden). Tests use fictional fixtures only, never a real workspace or the
   real `~/.claude`.
6. **English** for everything in the repository, commit messages included (plan 8.11). A project's own names
   (sheets, columns, statuses) are never translated. Talk to the user in their own language.
7. **Git and installation**: commit, push, merge and `sync_skills.py --apply` only when the user asks. Never write
   to `docs/history/` (dated, reference only).

## Map

| Where | What |
|---|---|
| `.claude/skills/<skill>/SKILL.md` | The eight skills; the contract is in the frontmatter (`workspace_select.py skills` prints the catalogue) |
| `docs/skills-spec.md`, `docs/skills-guide.md` | What each skill owns and its contract; the guide for people |
| `docs/procedures/` | Long procedures the skills read; `rules.md` is shared by all eight |
| `docs/technique-*.md` | Technical how-to per module, tracker and concern (list below) |
| `docs/project.yaml`, `docs/context-template.md`, `docs/workspace/` | Templates copied into a workspace (settings, `.gitignore`, setup, resume, CI, tracker skill) |
| `adapters/` | `excel/`, `tracker/` (common base), `jira/`, `azure_devops/`, `tickets/` (read only) |
| `scripts/` | Deterministic helpers the skills call; `common.py` holds workspace paths and the writer lock |
| `src/qa_audit/`, `tools/` | The audit engine; the Android companion driver and the Playwright helper |
| `tests/` | Unit tests, `golden/` (Excel, Jira, Azure DevOps), `fixtures/`, `integration/` (engine, real browsers) |
| `examples/demo/` | Fictional demo workspace; used by tests and the walkthrough |
| `docs/development-plan.md`, `CHANGELOG.md`, `docs/history/` | Roadmap, changes, dated history |

## Read when needed

- `docs/README.md` — documentation index; `CONTRIBUTING.md` — setup, guards, adding an audit module, dependencies
- `docs/skills-spec.md` — before touching `.claude/skills/` or `docs/procedures/`
- `docs/technique-workspaces.md` — framework discovery, project selection, identity, writer lock, skills' contract
  and sync (`workspace_select.py`, `sync_skills.py`, `workspace_identity.py`, a skill's frontmatter)
- `docs/technique-browser.md` (also the Playwright specs of `/playwright-tests`), `technique-api.md`, `technique-accessibility.md`, `technique-mobile.md` — the test
  modules (methodology §4.1)
- `docs/technique-excel.md`, `technique-jira-xray.md` (§2: common contract of tracker adapters),
  `technique-azure-devops.md`, `docs/tracker-validation.md` — trackers and what each has really been piloted on
- `docs/technique-tickets.md`, `technique-specs.md` — `/analyze-ticket`, `adapters/tickets/`, the `specs:` source
- `docs/technique-destructive-guard.md`, `technique-ado-write-guard.md` — `guard_destructive.py` (PreToolUse hook)
  and `ado_write_guard.py` (the only Azure DevOps write path)
- `docs/qa-audit.md` (spec, changed in the same commit as the behaviour), `docs/qa-audit-guide.md` — `src/qa_audit`
- `docs/ci.md` — CI jobs and what "green" means; `docs/heuristics.md` — error guessing
- `docs/demo-walkthrough.md` (run by a test), `docs/skills-evals.md` (agent-behaviour scenarios and their rounds)

## Changing a skill or a procedure

- Follow the contract of `docs/skills-spec.md` §4: frontmatter keys, `requires-docs` and `requires-scripts` naming
  every document and script the skill uses, step 0 (find `<qa>`, preflight) written in each skill, the folder holds
  only `SKILL.md`.
- A rule shared by several skills goes once to `docs/procedures/rules.md`; long steps go to
  `docs/procedures/<skill>-detail.md`.
- Each `SKILL.md` stays at or under 800 words (`wc -w`, frontmatter included; cut 2.2 of 23.11.2026).
- Check with `python -m unittest tests.test_skills tests.test_skill_flows`, then the whole suite.
- Workspaces only see a change after a sync: `python scripts/sync_skills.py` (dry run) and, when the user asks,
  `--apply` from a clean checkout of the merged `main` (a worktree is refused without `--allow-worktree`). Confirm
  with `python scripts/workspace_select.py where`.

## Scripts (run from the repository root)

Read each script's docstring and `--help` before using it (flags, exit codes). `python` below is the interpreter
`python3 scripts/qa_python.py` prints (`run <script|-m module> ...` runs with it).

- **Framework and selection**: `workspace_select.py where|list|resolve|preflight|skills|set-root`,
  `sync_skills.py`, `validate_project.py <ws> [--environment]`, `migrate_workspace.py`, `workspace_git.py`
- **Run records**: `run_log.py` (results, evidence, times, Questions, bugs; examples in `docs/skills-guide.md`),
  `pbi_state.py` (the PBI record; no skill drives it since `/test-pbi` was removed on 24.11.2026), `approvals.py`, `quarantine.py`, `spec_refs.py`, `ac_coverage.py`, `check_approval_files.py`,
  `compact_context.py`
- **Reports**: `qa_report.py totals|audit|report`, `build_dashboard.py`
- **Adapters**: `python -m adapters.excel.inspect|add_cases|apply`, `python -m adapters.tracker <ws> ...`,
  `python -m adapters.tickets <ws> ...`; every write is a dry run first (Excel: preview token and `--expect`;
  trackers: `--yes` only after the user approved the dry run)
- **Execution**: `api_request.py`, `audit_a11y.py`, `device.py`, `run_mobile.py`, `setup_emulator.py`,
  `build_driver.py`, `workspace_build.py`, `workspace_ci.py`, `remote_login.py`, `build_candidates.py`
- **Guards**: `guard_destructive.py`, `ado_write_guard.py`, `check_denylist.py`
- **Engine**: `qa-audit ...` (`pip install -e .`; `qa-audit doctor`), `update_axe.py`

## Tests and checks

- `python -m unittest discover -s tests` — unit tests (also in pre-commit and CI)
- `python scripts/golden_check.py` — fictional Excel golden (needs LibreOffice); a real golden only with
  `--dir <workspace>/golden/<batch>` when the user names it
- `python -m unittest discover -s tests/integration` — engine against the fixture site (browsers must start: outside
  the Bash sandbox)
- `ruff check .` — lint. Before saying a change is done, run what it touches and report the real output.

## Commits

- One task per commit, message `<phase>.<task>: <summary>` from `docs/development-plan.md`; a dependency update is a
  commit of its own (exact pins in `pyproject.toml` and `requirements.txt`).
- Record user-visible changes in `CHANGELOG.md` and the state of the task in `docs/development-plan.md`.
