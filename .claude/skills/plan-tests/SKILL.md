---
name: plan-tests
description: "Creates or updates a test plan for a feature, a ticket or a set of cases: coverage per requirement and platform, priorities, preconditions, data, expected results, reuse of existing cases, new case proposals and the batches. Use for 'plan the tests of', 'which cases do we have for', 'prepare the batch', after /analyze-ticket and before /run-tests."
argument-hint: "<PBI|feature|cases> [android|ios|web|api ...]"
metadata:
  intent: "Plan the tests: reuse existing cases, propose new ones, and group them into approved batches."
  inputs: "A ticket analysis (memory/checks/), a feature, an analysis file of analysis/, or the cases the user names; the platforms in scope."
  config: "The tracker (excel:, jira:, azure_devops:) and its case fields; modules and environments of project.yaml; the workspace's case pattern."
  tools: "The Excel adapter (read; add_cases with a dry run) or python -m adapters.tracker import-cases / export-cases."
  outputs: "An analysis with confirmed requirements, assumptions and coverage suggestions kept apart; a coverage matrix (requirement -> cases -> platform); new case proposals; a batch plan with order, preconditions and data."
  writes: "analysis/ (a new file per version); batches/<name>.yaml after the plan is confirmed; cases/ or tracker/export/ only for cases the user approved."
  approvals: "New cases enter the library only after the user approves them (dry run first). The plan is confirmed before /run-tests."
  failures: "An ambiguity that changes an expected result: a question, never an assumption; the case waits. A library that cannot be read: stop, never propose duplicates blind."
  resume: "The newest analysis/ file and batches/; a confirmed batch is not rewritten, a new version gets a new name."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/workspace_select.py"
  requires-docs: "procedures/rules.md, procedures/planning.md, heuristics.md, methodology.md"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill plan-tests [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Start from the newest source: the ticket's analysis (memory/checks/<id>.analysis.md, from /analyze-ticket), the
   newest analysis/<feature>_*.md, or the cases named. Say which file, its date and status. A source newer than the
   analysis: say so and propose /analyze-ticket first.
2. Existing cases first. Read the library without saving (the workbook through the adapter, or tracker/cases.json
   refreshed with import-cases): match by ticket link fields, area, feature and title; the workspace's own mapping
   (e.g. a PBI column) when CLAUDE.md names one. Classify each: reuse as is, reuse with an update to propose, or
   obsolete. A match that is not certain is asked, never guessed.
3. Keep three lists apart: confirmed requirements (each with its source and date), assumptions (what the plan relies
   on without a source), and coverage suggestions (risks, heuristics.md, negative and edge cases). An ambiguity that
   changes an expected result is a question to the user; the case waits for the answer.
4. Coverage matrix: each requirement or acceptance criterion -> the cases (existing or new) -> the platforms in
   scope (Android, iOS, web, API, by the project's modules), with a priority from risk. A platform outside the
   project's modules is not planned. Say what stays uncovered and why.
5. New cases: <qa>/docs/procedures/planning.md section B (technique per behaviour, one verifiable expected result, no
   duplicates, approval before they enter the library; ids only from the adapter's dry run).
6. Batches: procedures/planning.md section C. One platform and one build per batch; preconditions, test data (the
   workspace's prefix), order, dependencies, login per role; cases planned = cases selected.
7. Save the analysis in analysis/ (format of procedures/planning.md section A, a new file per version) with the
   matrix and the plan. Present it and STOP: batches/<name>.yaml is written after the user confirms, and execution
   starts only with /run-tests after the user approves it for the platform named.
