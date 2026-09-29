---
name: generate-cases
description: Generates test case proposals from the analysis of a feature, with the design technique given in each case, in the format of the project's tracker (methodology.md sections 5.2 and 5.3).
---

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
