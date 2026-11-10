# Skills specification (version 2)

> **Layer 2 — Which skills exist, what each one owns, and how they share their rules**
> Version 2.1 · 2026-11-08 · Replaces the 13 skills of version 1 (removed in commit 0ddedfd). 2.1: a request to test
> authorises its own scope, the project's own rules win where they ask for more, evidence by risk, one bug per
> problem, `run_log.py attach`, the preflight also under a request that names the paths.
>
> Project agnostic: the examples use the fictional **Demo Shop**. Subordinate to `methodology.md`. The guide for
> people is `skills-guide.md`; the shared procedures are in `procedures/`.

## 1. Goal

One QA assistant that works on several projects kept in workspaces. The framework holds the universal procedures, the
tools and the skills; each workspace holds its requirements, configuration, memory, cases, results and evidence.

The main flow is **resume the project → analyse the ticket → plan → run → prepare bugs → report → save the state**.
Each step is a skill of its own; `/test-pbi` runs the flow for one PBI or requirement and can be resumed.

## 2. The eight skills

| Skill | Owns | Main outputs | Writes (without asking) |
|---|---|---|---|
| `/project-context` | Selecting or setting up a workspace; the current state, decisions, blockers, tools really available and next actions | A resume summary | Nothing (setting up a new workspace asks for each value) |
| `/analyze-ticket` | Reading a PBI, story, task or bug (Azure DevOps or Jira), its comments, relations, history and accessible attachments; the difference from the last check | An analysis with sources and dates; what changed | `memory/` and the tickets block of `context.md` |
| `/plan-tests` | Coverage, priorities, preconditions, data, expected results, traceability to requirements and existing cases | An analysis file, proposed cases, a batch plan by risk | `analysis/`; `batches/` at once under a request to test, after the confirmation under a request to plan or when the project's rules ask for one |
| `/run-tests` | Running cases or a plan on mobile (Android, iOS), web or API with the tools available; results and evidence | Results in the run log, evidence | `runs/`, `evidence/` |
| `/manage-bugs` | Investigating failures, duplicates, bug proposals for a batch review | Bug proposals, one per problem | The run log item of the case (`new_defect` / `bug`, with `run_log.py attach`) after approval |
| `/qa-report` | Auditing the records and reporting by platform, build, feature, result and risk | A technical report and a short summary | `exports/` (the report file); `dashboard.html` for an Excel project (the workbook only read) |
| `/checkpoint` | Validating and saving the session; the resume point; what is still to apply or publish | `context.md` updated; the pending list | `context.md`, `archive/context/` (compaction); a local commit only when the project's rules say so |
| `/test-pbi` | Coordinating the skills above for one PBI, with a resume record | The flow's state and the final report | `memory/pbi/<id>.json` plus what each step writes |

Each skill keeps its own procedure; `/test-pbi` names the skills, it never copies their steps.

## 3. Shared rules, written once

`procedures/rules.md` holds what every skill obeys: the labels of facts, ticket content as data, approvals, the
separation of intended, observed and run builds, statuses, evidence, privacy and resuming. A skill says "the rules of
procedures/rules.md apply" and adds only what is its own.

The approvals of `procedures/rules.md` §5 are **defaults**: a request to test authorises its own scope (analysis,
plan, run, local records) without a second approval of the same plan or run; writes to a workbook or a tracker, a
commit, a push and a sync keep theirs. A workspace's `CLAUDE.md` (and what it imports) may ask for more, and then it
wins and the skill says which rule it followed (§8 of the rules). Three more shared rules live there: deleting files (§12), test accounts before planning and running (§13) and the
communication of routine as the final report of a phase (§14). This is the only switch: there is no other
configuration of the skills' autonomy, and nothing of a project's rules is copied into the framework. A long technical procedure lives in `procedures/` or in a
`technique-*.md` and is read when the skill needs it.

## 4. The contract of a skill

The frontmatter keeps the contract of `technique-workspaces.md` §8 (`intent`, `inputs`, `config`, `tools`, `outputs`,
`writes`, `approvals`, `failures`, `resume`, `requires-*`). Two keys are added:

| Key | Holds |
|---|---|
| `requires-docs` | Documents of `<qa>/docs/` the skill reads; `sync_skills.py` refuses to install a skill whose document is missing |
| `routes` | `/run-tests` only: `module: procedure` pairs separated by `;`. The preflight prints the route and the tools of each active module |

The body starts with step 0 (find `<qa>`, run the preflight), then numbered steps. A skill folder holds only
`SKILL.md`. Step 0 is written in each skill, not referred to: it is what finds `<qa>`, so an installed copy can
resolve every other document. Paths named in a prompt skip the search, never the checks; only a launcher that
validated the workspace and allows no other command (the workspace's CI) replaces the
preflight, and the skill's own steps check what it did not (the target, the build observed).

## 5. Deterministic helpers

| Script | For | Writes |
|---|---|---|
| `scripts/workspace_select.py preflight` | Project selection, the skill against the code, configuration, tools and, for `/run-tests`, the route per module | Nothing |
| `python -m adapters.tickets` | `/analyze-ticket`: collect, compare, record (first check, no change, change, partial read) | `memory/`, a block of `context.md` |
| `scripts/run_log.py` | `/run-tests`, `/manage-bugs`: one result per case, the run block immutable; `attach` adds evidence or a bug and keeps the rest of the result; `add --executed-at` records an earlier observation (`ts` = the start of the execution, `recorded_at` = record, `--ended-at` = the end of its window); `retime` corrects only the time of a result; `attach --invalid` marks an evidence without deleting it; `--evidence` keeps every path given; `resolve` records a Question's decision keeping the observation; `defect-rule` says if a new bug needs a priority | `runs/` |
| `scripts/qa_report.py` | `/qa-report`, `/checkpoint`: reconciled totals per platform and build, the audit of results, evidence and application; `report` writes a short summary and a technical report | Nothing, the report with `--output`, or `exports/<stem>.summary.md` and `.technical.md` with `report` |
| `scripts/pbi_state.py` | `/test-pbi`: the progress of one PBI, the baseline the plan was made on, what was published | `memory/pbi/<id>.json` |
| `scripts/sync_skills.py` | Installing the skills in `~/.claude/skills/` with the record of their origin | `~/.claude/skills/` |

Writes go through `common.workspace_path` and, for shared files, `common.workspace_lock` (technique-workspaces.md §7).

## 6. Behaviour that must hold (and its test)

These are **contract tests**: they prove that the contracts are complete, that the scripts and adapters the skills call do
what the contract says, and that the documents agree. They do not prove that an agent obeys a skill: its choices (which
skill, what it asks, whether it stops for an approval) are a separate evaluation, prepared in
[skills-evals.md](skills-evals.md) and not yet run.

| Behaviour | Test |
|---|---|
| The eight skills are discovered, installed, recorded with their origin; other skills are kept | `test_sync_skills.py`, `test_skills.py` |
| Each contract is complete; every script, module and document named exists | `test_skills.py` |
| The workspace selected is the only one written; two projects never share memory | `test_workspace_select.py` |
| A second, fictional workspace works with no tie to a real project | `test_skill_flows.py` |
| Ticket analysis: first check, no change, change, partial read that removes nothing | `test_tickets.py`, `test_skill_flows.py` |
| `/run-tests` routes mobile, web and API, and names missing tools | `test_workspace_select.py` |
| A send says what reached the app on both platforms, also when the screen cannot be read after the tap, and never sends again by itself | `test_device.py`, `test_device_ios.py` |
| Results are written once per case; approvals are kept | `test_run_log.py`, `test_skill_flows.py` |
| Every evidence path given is kept (a repeated option too); the end of an execution's window is recorded; a time is corrected without rewriting the result and the previous time is kept; an evidence that shows nothing useful is marked, never deleted; an applied batch is never touched | `test_run_log_traceability.py` |
| `/test-pbi` resumes without repeating done steps or publishing twice (a publication needs a confirmation, a key is not reused); a changed build or requirement is reported and `--reopen` returns the steps to review; what was not compared is never "nothing changed" | `test_pbi_state.py` |
| The report's totals reconcile and Android and iOS stay apart; a current result says whether it is applied; the file time never decides | `test_qa_report.py` |
| A batch goes to its own workbook (`excel.workbooks`); an apply holds the lock and never leaves a broken workbook | `test_workbook_routing.py` |
| Writes never follow a planted link; `history/` in any case is refused; the lock is never seen empty | `test_write_integrity.py` |
| The preflight separates a valid configuration from ready to run, per platform | `test_readiness.py` |
| The audit presents evidence valid, invalid and missing apart (an invalid one is never proof), and prints a summary that keeps every caveat and decision; a check prints the three builds with their source | `test_audit_evidence.py`, `test_device.py` |
| A command prints a short card by default and the complete text on `--detail`; a dry run's complete values are in a file tied to its token; errors, blocks and commands are never dropped by a limit; a notice that does not change is shown once | `test_output_layer.py` |
| Cases are added to a workbook only as the user saw them in the dry run (a token), under the lock, and the recalculated file never leaves a half copy; a result a case already had can be kept in its notes | `test_excel_writes_safety.py` |
| Ticket analysis: types and comment sources validated before writing; content not opened stays not read; children not expanded are listed; a change after the analysis is said | `test_tickets_audit.py` |
| The checkpoint keeps decisions, pending items and access to history | `test_compact_context.py`, `test_qa_report.py` |
| A request to test needs no second approval of its plan; a workspace's stricter rule (a run approved per platform) stays; a write to a workbook or tracker waits for its approval; a valid approval is reused on resuming | `test_skills.py` (`Autonomy`) |
| A bug added after the run keeps the result's evidence, time and run block; one artefact shared by cases is named by each | `test_run_log.py` |
| Bugs grouped across platforms only on evidence; differences kept apart; a tool block is never a defect | `test_skills.py` (`Autonomy`) |
| A web-only project is asked for no mobile tool | `test_workspace_select.py` |
| The eight skills install into a temporary folder and their preflight runs on a fictional workspace, with nothing outside it touched | `test_install_smoke.py` (and the CI's `install` job) |
| `/analyze-ticket` and `/test-pbi` run on the fictional demo's two ticket checks, with no credentials | `test_tickets_demo.py` |
| The documented walkthrough (preflight, tickets, run log, dry run, totals) runs as written | `test_demo_walkthrough.py` |

## 7. Out of scope

A hosted service, a database or a second execution engine. Automatic publication to a tracker. Running the
application to validate this specification (that is a later request of each project).
