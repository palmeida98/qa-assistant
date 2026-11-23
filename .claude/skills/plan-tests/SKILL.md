---
name: plan-tests
description: "Creates or updates a test plan for a feature, a ticket or a set of cases: coverage per requirement and platform, priorities, preconditions, data, expected results, reuse of existing cases, new case proposals and the batches. Use for 'plan the tests of', 'which cases do we have for', 'prepare the batch', after /analyze-ticket and before /run-tests."
argument-hint: "<PBI|feature|cases> [android|ios|web|api ...]"
metadata:
  intent: "Plan the tests by risk: reuse existing cases, propose new ones, and group them into batches ready to run."
  inputs: "A ticket analysis (memory/checks/), a feature, an analysis file of analysis/, or the cases the user names; the platforms in scope."
  config: "The tracker (excel:, jira:, azure_devops:) and its case fields; modules and environments of project.yaml; the workspace's case pattern."
  tools: "The Excel adapter (read; add_cases with a dry run) or python -m adapters.tracker import-cases / export-cases."
  outputs: "An analysis with confirmed requirements, assumptions and coverage suggestions kept apart; a coverage matrix (requirement -> cases -> platform) by risk, with what is covered, what is missing and the residual risk; new case proposals; a batch plan with order, preconditions and data."
  writes: "analysis/ (a new file per version); batches/<name>.yaml once the plan stands (at once under a request to test, after the user confirms it under a request to plan or when the project's rules ask for a confirmation); cases/ or tracker/export/ only for cases the user approved; under policy.approvals.plan required, the draft analysis/<name>.yaml and its review, then (after the QA's explicit message) batches/<name>.yaml and the decision in memory/approvals.jsonl."
  approvals: "New cases enter the library only after the user approves them (dry run first). The plan itself: none under a request to test within its scope (rules.md section 5); a confirmation when the request was only to plan, when it widens the scope, or when the workspace's CLAUDE.md asks for one (rules.md section 8)."
  failures: "An ambiguity that changes an expected result: a question, never an assumption; the case waits. A library that cannot be read: stop, never propose duplicates blind."
  resume: "The newest analysis/ file and batches/; a batch already saved is not rewritten, a new version gets a new name; a confirmation still valid for the same plan is not asked again."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/workspace_select.py, scripts/quarantine.py, scripts/approvals.py, scripts/qa_python.py"
  requires-tools: "spec"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/planning.md, procedures/approvals.md, procedures/quarantine.md, procedures/plan-tests-detail.md, heuristics.md, technique-specs.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace per session; under a launcher that already validated the workspace its paths are used, and a check that cannot run leaves its cases Blocked, never assumed. Find <qa>: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else ../../../qa-assistant from the workspace. `python` is what `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill plan-tests [<project id or name>] [--workspace <path>]
   Exits, `Route:`, `Next:`, `Ready:`: <qa>/docs/procedures/preflight.md. <qa> or the script missing, or exit 3: stop and show what it says. Documents are in <qa>/docs/; the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Start from the newest source (memory/checks/<id>.analysis.md, analysis/<feature>_*.md) or the cases named; say which and its date. Newer source: propose /analyze-ticket first.
2. Existing cases first. Read the library without saving (adapter, or tracker/cases.json refreshed with import-cases); match by ticket link, area, feature, title and the workspace's mapping. Classify: reuse, update, or obsolete. Uncertain: ask.
3. Keep apart: confirmed requirements (source, date), assumptions, coverage suggestions (heuristics.md). An ambiguity that changes an expected result is a question; the case waits. `spec source` line: procedures/plan-tests-detail.md section 1.
4. Coverage matrix by risk: requirement or criterion -> cases (existing or new) -> platforms in scope, with priority, negative, boundary and regression cases. Say what stays uncovered and the residual risk. Widening on a relevant sign: procedures/plan-tests-detail.md section 2.
5. New cases: <qa>/docs/procedures/planning.md section B (approval before they enter the library; ids from the adapter's dry run only).
6. Batches: procedures/planning.md section C. One platform and build per batch; cases planned = cases selected. Quarantines: procedures/quarantine.md section 3 and procedures/plan-tests-detail.md section 3; no case left out without the QA's choice.
7. Save the analysis in analysis/ (procedures/planning.md section A, new file per version) and present it. Then, by request (rules.md sections 5 and 8):
   - a request to test within its scope, with no confirmation asked by the workspace's CLAUDE.md: write batches/<name>.yaml and go on with /run-tests, without asking to approve the same plan again. Cases awaiting library approval wait; the others run;
   - a request to plan only, a plan that widens the scope, or a workspace whose rules ask for a confirmation: STOP. batches/<name>.yaml is written after the user confirms; /run-tests starts with the approval those rules name. Name the rule;
   - with `policy.approvals.plan: required`, whatever the request: procedures/approvals.md section 5, procedures/plan-tests-detail.md section 4. The QA's explicit message comes first (STOP after the review); it approves the coverage only, never a run.
