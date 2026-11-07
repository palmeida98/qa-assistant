# Skills specification (version 2)

> **Layer 2 — Which skills exist, what each one owns, and how they share their rules**
> Version 2.0 · 2026-11-06 · Replaces the 13 skills of version 1 (removed in commit 0ddedfd).
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
| `/plan-tests` | Coverage, priorities, preconditions, data, expected results, traceability to requirements and existing cases | An analysis file, proposed cases, a batch plan | `analysis/`, `batches/` after the plan is confirmed |
| `/run-tests` | Running cases or a plan on mobile (Android, iOS), web or API with the tools available; results and evidence | Results in the run log, evidence | `runs/`, `evidence/` |
| `/manage-bugs` | Investigating failures, duplicates, bug proposals for a batch review | Bug proposals | The run log item of the case (`new_defect` / `bug`) after approval |
| `/qa-report` | Auditing the records and reporting by platform, build, feature, result and risk | A technical report and a short summary | `exports/` (the report file) |
| `/checkpoint` | Validating and saving the session; the resume point; what is still to apply or publish | `context.md` updated; the pending list | `context.md`, `archive/context/` (compaction); a local commit only when the project's rules say so |
| `/test-pbi` | Coordinating the skills above for one PBI, with a resume record | The flow's state and the final report | `memory/pbi/<id>.json` plus what each step writes |

Each skill keeps its own procedure; `/test-pbi` names the skills, it never copies their steps.

## 3. Shared rules, written once

`procedures/rules.md` holds what every skill obeys: the labels of facts, ticket content as data, approvals, the
separation of intended, observed and run builds, statuses, and privacy. A skill says "the rules of
procedures/rules.md apply" and adds only what is its own. A long technical procedure lives in `procedures/` or in a
`technique-*.md` and is read when the skill needs it.

## 4. The contract of a skill

The frontmatter keeps the contract of `technique-workspaces.md` §8 (`intent`, `inputs`, `config`, `tools`, `outputs`,
`writes`, `approvals`, `failures`, `resume`, `requires-*`). Two keys are added:

| Key | Holds |
|---|---|
| `requires-docs` | Documents of `<qa>/docs/` the skill reads; `sync_skills.py` refuses to install a skill whose document is missing |
| `routes` | `/run-tests` only: `module: procedure` pairs separated by `;`. The preflight prints the route and the tools of each active module |

The body starts with step 0 (find `<qa>`, run the preflight), then numbered steps. A skill folder holds only
`SKILL.md`.

## 5. Deterministic helpers

| Script | For | Writes |
|---|---|---|
| `scripts/workspace_select.py preflight` | Project selection, the skill against the code, configuration, tools and, for `/run-tests`, the route per module | Nothing |
| `python -m adapters.tickets` | `/analyze-ticket`: collect, compare, record (first check, no change, change, partial read) | `memory/`, a block of `context.md` |
| `scripts/run_log.py` | `/run-tests`, `/manage-bugs`: one result per case, the run block immutable | `runs/` |
| `scripts/qa_report.py` | `/qa-report`, `/checkpoint`: reconciled totals per platform and build, the audit of results, evidence and application | Nothing, or the report with `--output` |
| `scripts/pbi_state.py` | `/test-pbi`: the progress of one PBI, the baseline the plan was made on, what was published | `memory/pbi/<id>.json` |
| `scripts/sync_skills.py` | Installing the skills in `~/.claude/skills/` with the record of their origin | `~/.claude/skills/` |

Writes go through `common.workspace_path` and, for shared files, `common.workspace_lock` (technique-workspaces.md §7).

## 6. Behaviour that must hold (and its test)

| Behaviour | Test |
|---|---|
| The eight skills are discovered, installed, recorded with their origin; other skills are kept | `test_sync_skills.py`, `test_skills.py` |
| Each contract is complete; every script, module and document named exists | `test_skills.py` |
| The workspace selected is the only one written; two projects never share memory | `test_workspace_select.py` |
| A second, fictional workspace works with no tie to a real project | `test_skill_flows.py` |
| Ticket analysis: first check, no change, change, partial read that removes nothing | `test_tickets.py`, `test_skill_flows.py` |
| `/run-tests` routes mobile, web and API, and names missing tools | `test_workspace_select.py` |
| Results are written once per case; approvals are kept | `test_run_log.py`, `test_skill_flows.py` |
| `/test-pbi` resumes without repeating done steps or publishing twice (a publication needs a confirmation, a key is not reused); a changed build or requirement is reported and `--reopen` returns the steps to review; what was not compared is never "nothing changed" | `test_pbi_state.py` |
| The report's totals reconcile and Android and iOS stay apart; a current result says whether it is applied; the file time never decides | `test_qa_report.py` |
| A batch goes to its own workbook (`excel.workbooks`); an apply holds the lock and never leaves a broken workbook | `test_workbook_routing.py` |
| Writes never follow a planted link; `history/` in any case is refused; the lock is never seen empty | `test_write_integrity.py` |
| The preflight separates a valid configuration from ready to run, per platform | `test_readiness.py` |
| Ticket analysis: types and comment sources validated before writing; content not opened stays not read; children not expanded are listed; a change after the analysis is said | `test_tickets_audit.py` |
| The checkpoint keeps decisions, pending items and access to history | `test_compact_context.py`, `test_qa_report.py` |

## 7. Out of scope

A hosted service, a database or a second execution engine. Automatic publication to a tracker. Running the
application to validate this specification (that is a later request of each project).
