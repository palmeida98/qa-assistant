# Planning tests
> Read by /plan-tests: the analysis of a feature (A), the design of cases (B) and the batch plan (C).
> Moved from the version 1 skills (commit 7e7cabf) with their names updated (skills-guide.md §6). Step numbers are the original ones.

## A. Analysis of a feature (from /analyze-feature)

1. Read CLAUDE.md and project.yaml of the current workspace (roles, environments, reference documentation).
   Do not ask again what is already there (methodology.md section 4).
1a. A feature that starts as a ticket: /analyze-ticket gives its state, changes and relations first.
2. Identify the feature's source of truth (requirements, specification, Figma, existing cases)
   and respect the hierarchy of section 1.3. If two sources contradict each other, stop and ask.
3. For each role/profile of the project, say whether it reaches the feature and what it can and cannot do.
4. List the inputs, the states (life cycle), the business rules and the integrations.
5. Assess the risk: probability of failure x impact for the user or for the business.
6. Record the specification gaps as questions or "missing specification" notes.
   Never fill them with assumptions (section 5.1).
7. Produce the list of behaviours to test, each one linked to the role, rule or state it comes from,
   ready for the design of cases (section B).
8. Present the open questions to the user all at once, at the end.
9. Save the analysis in the workspace (methodology.md section 5.1): analysis/<feature>_<YYYYMMDD>.md, a slug of the
   feature, with _2, _3 when that name exists: an older file is never changed. In this format:
   # Analysis: <feature>
   Date: <YYYY-MM-DD>
   Status: produced | reviewed by the user on <YYYY-MM-DD>
   Sources: each document, ticket or link read, with its version or date; "none" when there was none
   ## Confirmed by a source: each rule with its source
   ## Proposed: the behaviours and risks this analysis infers, not requirements
   ## Open questions
   Status says reviewed only when the user said so. Reviewing an analysis does not turn what is proposed into a
   requirement: only a source, or an explicit decision of the user written as such with its date, does.

## B. Design of cases (from /generate-cases)

1. Start from the newest analysis/<feature>_*.md of the workspace, saying which file, its date and its status; no
   analysis: do section A first or ask the user. A source newer than the analysis: say so. What is under
   "Proposed" is never a requirement: an expected result cannot rest on it alone, so that case stays a question.
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
   and use those from then on; tracker/ changed: list it as not committed (a commit only when the user or CLAUDE.md
   asks, rules.md). A file already exported is never edited: new
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
   numbers. Never add a case by hand in the workbook. The workbook and cases/ changed: list them as not committed (a
   commit only when the user or CLAUDE.md asks). A file
   already added is never edited: new cases go in a new file. If cases.fields has no title column, it is set up
   with the user first (project.yaml, excel.cases.fields).

## C. Batch plan (from /plan-batch)

1. Read from the current workspace project.yaml (roles, environments, active modules) and context.md
   (execution point, known blockers, scope decisions).
1a. With the jira or azure-devops tracker, the cases come from <workspace>/tracker/cases.json (technique-jira-xray.md
   section 6). If it does not exist or its imported_at is older than this session, refresh it first, from the
   root of qa-assistant (read only): python -m adapters.tracker <workspace> import-cases
   and show the user the flags it prints. Use the tracker's IDs (the "id" of each case: the key or work item id
   with id_source key/id) and last_status; never cases from memory.
2. Select the candidate cases (e.g. not run, retest of fixed bugs). Exclude those out of scope by a
   recorded decision and say why.
3. Group by area + feature + platform + role + flow (section 6).
4. Within each group:
   - the cases that share a login, environment or application state run one after the other;
   - first the flows that create state other cases reuse;
   - dependencies between cases made explicit (technique-browser.md section 8).
5. One login per role and per batch (technique-browser.md section 3).
6. If the time does not cover everything, order the groups by risk.
7. Check that each batch's test data exists. Cases without data are Blocked, with the reason.
8. The optimisation never reduces coverage nor changes a case's logic: confirm that the number of cases
   planned equals the number of cases selected.
9. Present the plan (batches, order, role, cases, dependencies) and wait for confirmation before /run-tests.
10. Once confirmed, save each batch as <workspace>/batches/<name>.yaml -- cases: [<case ID>, ...] in the planned
    order (a name of letters, digits, '.', '_', '-') -- so it can also run remotely: the workspace's CI takes the
    batch's name (python scripts/workspace_ci.py <workspace> check --batch <name>; technique-mobile.md section 11).
