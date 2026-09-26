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
   (section 5.3), with stable IDs in the project's pattern.
