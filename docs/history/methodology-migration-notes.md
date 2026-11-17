# Methodology — migration notes

> The notes of each version of [`methodology.md`](../methodology.md) (what changed, what was kept), moved out of it on
> 17.11.2026 so they are not loaded with the current rules in every session. Read them when an older workspace, run log
> or document refers to a rule that changed. Later changes are in the [changelog](../../CHANGELOG.md).

### v2.14 → v2.15 (traceability of applications and results, 08.11.2026)
- **Changed:** the Excel preview token covers the options that change what is written and has a version (`v2-`): previews pending at the upgrade are repeated (`technique-excel.md` §1.1); `ts` stays the time of the execution and a record time `recorded_at` is added, with `--executed-at` for an earlier observation (§7.1); a Question is resolved by a recorded decision that keeps the observation (§7.1, `procedures/apply.md`); the priority of a new bug is asked of the user when the schema requires it (§9.2); `scripts/qa_python.py` finds the interpreter (`procedures/rules.md` §11).
- **Kept:** every approval, the statuses, run logs and applied batches as they are (no migration of history), `history/` immutable.

### v2.13 → v2.14 (context as the main document, 08.11.2026)
- **Changed:** a project may import `context.md` from its `CLAUDE.md` as its main document, with the permanent rules in headed sections that compaction never touches (§12.2); the default (rules in `CLAUDE.md`) is unchanged.
- **Kept:** every approval, the statuses, `history/` immutable, one place for each rule.

### v2.12 → v2.13 (skills configuration, 08.11.2026)
- **Changed:** a request to test authorises the work inside its scope (analysis, a plan by risk, the run, the local records) without approving the same plan or run twice (§3); the defaults and the precedence of a project's own rules are in `procedures/rules.md` §5 and §8. Writes to a workbook or a tracker keep their approval.
- **Changed:** the evidence is proportional to the risk on top of the project's policy, and one artefact may serve several cases (§7.3); one bug per problem, grouping platforms only on evidence (§9.1).
- **New:** `run_log.py attach` adds evidence or a bug to a recorded result and keeps the rest of it (§7.1).
- **Kept:** every approval of a write to a workbook or a tracker, the statuses, the run block, the unattended runs (§9.4); a project's stricter rules.

### v2.11 → v2.12 (skills version 2, 06.11.2026)
- **Changed:** the thirteen skills became eight (`skills-spec.md`, §3): `/project-context` (onboarding and resuming), `/analyze-ticket`, `/plan-tests` (analysis, case design and batches), `/run-tests` (mobile, web, API, accessibility, by a route per module), `/manage-bugs`, `/qa-report` (totals per platform and build, and the dashboard), `/checkpoint` and `/test-pbi` (the flow for one PBI, resumable). The long procedures moved word for word to `procedures/`; the rules every skill shares are in `procedures/rules.md`. The names map in `skills-guide.md` §6.
- **New:** `scripts/qa_report.py` (reconciled totals, audit of run logs), `scripts/pbi_state.py` (the record of `/test-pbi`), `report.platforms` in `project.yaml`, `requires-docs` and `routes` in a skill's contract.
- **Changed:** `/checkpoint` no longer commits on its own: a commit only when the user or the workspace's `CLAUDE.md` asks for it (§12.1).
- **Kept:** every approval, the run log as the source, the statuses, `history/` immutable, the ticket memory and its markers in `context.md`.

### v2.10 → v2.11 (plan 15.8, 02.11.2026)

- **New:** a result only for a case of the library, removing a mistaken result, an immutable run block (§7.1); a context kept short (§12.2); the roles of a build and one check of the target (`technique-mobile.md` §1.3 and §1.4).
- **Kept:** the run log as the source, the approvals, `history/` immutable.

### v2.9 → v2.10 (plan 15.7, 01.11.2026)

- **New:** the analysis of a feature is kept in `analysis/` (§5.1) and `/generate-cases` starts from it.
- **Kept:** cases are proposals until approved; a gap is a question.

### v2.8 → v2.9 (plan 15.6, 01.11.2026)

- **New:** one writer at a time per workspace (§12.1): the ticket memory, the run log and the interface's edits take a short lock, so two sessions never overwrite each other.
- **Kept:** everything else; a lock left by a crash is taken over, so no project is ever blocked by it.

### v2.7 → v2.8 (plan phase 15, 01.11.2026)

- **New:** finding the framework and selecting a project for any skill (§3, §4.3, `technique-workspaces.md`); each skill's contract in its frontmatter; the two layouts of a workspace; the statuses of a project.
- **Changed:** a root of workspaces that does not exist is an error, no longer created; the root can be configured.
- **Kept:** the names of the skills, the closed list of modules, the approvals of §9.4, `history/` immutable, one engine: nothing is copied into a project.

### v2.6 → v2.7 (plan phase 14, 01.11.2026)

- **New:** `/analyze-tickets` and `memory/` in the workspace (§3, §5.1, §12.2): a read-only follow-up of tickets, independent of `tracker:`, with a deterministic comparison between checks.
- **Kept:** nothing reaches a tracker without the user's approval (§9.4), the statuses of §2, `/checkpoint` for the commits, `history/` immutable.

### v2.5 → v2.6 (plan 12.6, 17.10.2026)

- **New:** in a project with `mobile` and without `functional-web`, the `exploratory` charters run on the app with the mobile technique and `/test-mobile` (§4.1); before, they could only use the browser.
- **Kept:** the closed list of modules, a charter judged by its expected result, the run log as the source.

### v2.4 → v2.5 (plan phase 12, 08.10.2026)

- **New:** a run can be started from the local interface (since removed, 08.11.2026); it is unattended like a remote run, and the approval of new bugs and the answers to `Question` happen in the interface, as edits of the run log (§9.4).
- **Kept:** the run log as the source, approval before any bug reaches the workbook or a tracker, the adapters' refusal of a `new_defect` with `"proposed"`, and `/checkpoint` for `context.md` and the commit.

### v2.3 → v2.4 (plan 10.6, 04.10.2026)

- **New:** approved cases enter through the project's adapter, never by hand (§5.3); with Excel, `add-cases` (§4.2, `technique-excel.md` §4.1).
- **Kept:** cases are proposals until the user approves them; IDs are stable and in the project's pattern.

### v2.2 → v2.3 (plan phase 11, 29.09.2026)

- **New:** remote execution in the workspace's CI (§12.1, §12.4); the runner as an environment of its own (§14.2); in a remote run the approval of new bugs and the answers to `Question` happen in the pull request, and a `new_defect` with `"proposed": true` is never applied (§9.4).
- **Kept:** the run log as the source, approval before any bug reaches the workbook or a tracker, push only with the user's approval (the runner pushes only its own `run/…` branch; the merge is the user's).

### v2.1 → v2.2 (plan phase 10, 28.09.2026)

- **Changed:** Jira (with Xray) and Azure DevOps (Test Plans + Boards) get adapters (§1.1, §4.2), with the four directions of the Excel one plus importing and exporting cases; this revises the 24.09.2026 decision (plan 7.7) that kept them as manual logging. Manual logging stays until each adapter is done.
- **New:** the case ID mapping lives in the external tracker, with a cache in the workspace (§2); the run log's optional `run` block (environment and build, §7.1); the dry run before sending to a tracker (§9.4).
- **Kept:** the run log as the source during the session, checkpoints, `Question` refused before sending, approval before any bug reaches a tracker.

### v2.0 → v2.1 (plan 8.11, 26.09.2026)

- **Changed:** everything in English, the names included: this file (`methodology.md`, before `metodologia-core.md`), the techniques (`technique-*.md`), the skills (e.g. `/run-batch`), the scripts, the keys of `project.yaml` and of the run log, and the default statuses (`Not run`, `Question`). Existing workspaces are converted by `scripts/migrate_workspace.py`.

### v1 → v2

- **New:** privacy boundary (§1.2), own data model (§2), design techniques required per case (§5.2), the `Question` status (§8.5), approval before sending to the tracker (§9.4), golden and round-trip tests (§15), heuristics (§17).
- **Changed:** the code is no longer rebuilt in each session. It becomes versioned and validated by tests, and stays below the specifications in the hierarchy (§1.3, point 6).
- **Changed:** the rule for resolved bugs and the evidence policy become configurable per project (`project.yaml`).
- **Kept:** batch execution, run log, checkpoints every 15–20 cases, Pass/Fail/N/A/Blocked rules, duplicate handling, consistency between documents, immutable history, reuse before creating.
