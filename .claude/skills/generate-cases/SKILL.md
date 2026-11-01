---
name: generate-cases
description: Generates test case proposals from the analysis of a feature, with the design technique given in each case, in the format of the project's tracker (methodology.md sections 5.2 and 5.3).
metadata:
  intent: "Propose test cases from an analysis, with the design technique of each, without duplicates."
  inputs: "The output of /analyze-feature; the existing cases."
  config: "The tracker's section (excel.cases.fields, or the cases of jira: or azure_devops:)."
  tools: "The project's adapter."
  outputs: "Traceable proposals; after approval, the cases added with their final IDs."
  writes: "cases/ or tracker/export/, then the workbook or the tracker through the adapter."
  approvals: "Every case is a proposal until the user approves it; a dry run first."
  failures: "An ID that exists or a candidate duplicate: the user decides, it is never forced."
  resume: "A file already added is never edited: new cases go in a new file."
  requires-modules: "adapters.excel, adapters.tracker"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (in the demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill generate-cases [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. In the steps below, the current workspace is the one it selected.
1. Start from the output of /analyze-feature. If it does not exist, run it first or ask the user.
2. Read the case format and the tracker in project.yaml (current workspace) and the structure of the
   workbook/tracker in CLAUDE.md. Do not invent columns nor statuses.
3. Choose the design technique per behaviour (section 5.2): equivalence partitioning, boundary values,
   decision table, state transition, pairwise, role matrix, error guessing (heuristics in
   <qa>/docs/heuristics.md, section 17) or exploratory charter. Include negative cases.
4. Each case has the fields of the data model (section 2): feature, title, preconditions, steps,
   ONE verifiable expected result, role, platform, module, technique, priority and origin.
   If a case has several expected results, split it (section 5.3).
5. The expected result comes from the specification, never from the application's current behaviour.
6. The priority derives from the feature's risk, unless the project has a different rule.
7. Before proposing, look for equivalent existing cases and flag duplicates (section 10).
8. Present the cases as PROPOSALS. They only go into the workbook/tracker after the user approves them
   (section 5.3), with stable IDs in the project's pattern. With the jira or azure-devops tracker, compare with
   tracker/cases.json in step 7.
9. With the jira or azure-devops tracker (technique-jira-xray.md section 7), write the APPROVED cases to
   <workspace>/tracker/export/cases_<YYYYMMDD>_<nn>.json as {"cases": [...]} with the fields of the model (the
   same as tracker/cases.json: id, title, area, feature, preconditions, steps [{action, data, expected}],
   expected, role, platform, priority, technique, origin); the id is provisional (NEW-1, NEW-2...) with
   id_source key/id, or the project's own ID. From the root of qa-assistant, the dry run first:
   python -m adapters.tracker <workspace> export-cases tracker/export/cases_<YYYYMMDD>_<nn>.json
   1 = show the plan to the user; only after approval run it again with --yes. 2 = an ID that already exists or
   a candidate duplicate: the user decides (remove the case from the file, never force it); 3 = partial, run the
   same --yes again (it continues); 4 = invalid file. Tell the user the final IDs it prints (NEW-1 -> DS-140)
   and use those from then on; commit tracker/ in the workspace. A file already exported is never edited: new
   cases go in a new file.
10. With the excel tracker (technique-excel.md section 4.1), write the APPROVED cases to
   <workspace>/cases/cases_<YYYYMMDD>_<nn>.json as {"cases": [...]} with the same fields of the model; the id is the
   FINAL ID in the project's pattern (the next free number of the sheet its prefix chooses). From the root of
   qa-assistant, the dry run first:
   python -m adapters.excel.add_cases <workspace> cases/cases_<YYYYMMDD>_<nn>.json --dry-run
   0 = show the user the rows and the fields not written (no column), then run it again without --dry-run;
   4 = an ID that exists, a candidate duplicate (same title in the sheet) or an invalid file: the user decides
   (remove the case or give another ID, never force it). After the write: 2 = cross-check problems (original
   untouched); 3 = recalculation failed; 5 = the summary does not count the new rows: fix its formulas, never the
   numbers. Never add a case by hand in the workbook. Commit the workbook and cases/ in the workspace. A file
   already added is never edited: new cases go in a new file. If cases.fields has no title column, it is set up
   with the user first (project.yaml, excel.cases.fields).
