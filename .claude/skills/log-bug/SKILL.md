---
name: log-bug
description: Logs or updates a bug from a Fail case, looking for duplicates first.
---
1. Read the bug format in project.yaml (bug_format and, with the excel tracker, excel.defects) and in the
   current workspace's CLAUDE.md.
2. Look for existing bugs with a similar area/feature/sub-area/platform/role (section 9.1).
   With the excel tracker, read the defects sheet through the adapter (ExcelAdapter.from_project(...).load()),
   without saving.
   With the jira or azure-devops tracker, write the draft to <workspace>/tracker/drafts/<case>.json -- the keys of
   bugs.fields (title, area, feature, sub_area, platform, role...) plus "observed" -- and search, from the root
   of qa-assistant (read only; technique-jira-xray.md section 5.1):
   python -m adapters.tracker <workspace> bug-search tracker/drafts/<case>.json
   (--include-closed when it may be a regression of a fixed bug). 4 = invalid draft or configuration; 3 = the
   tracker out of reach: tell the user, never skip the search silently.
3. If you find duplicate candidates, show them (key, status, summary, criteria matched) and ask: update one of
   them or create a new one. The user decides, never the adapter nor the assistant.
4. Write: title in the project's format, environment and build, role and platform, preconditions,
   steps to reproduce, expected -> observed, linked cases. Severity and priority only if the user asks
   for them or decides them (section 9.2).
5. Show the bug to the user and ask for explicit approval before logging it (section 9.4).
6. Once approved, with the excel tracker, the bug is NOT written straight into the workbook: it goes in the
   batch's run log, in the item of the Fail case (technique-excel.md section 4):
   - new bug -> "new_defect": {keys of excel.defects.batch_fields, "severity", "priority"};
     the priority must be in excel.defects.priorities;
   - existing bug -> "bug": "<id>".
   It reaches the workbook at the next /checkpoint (adapters.excel.apply).
7. With the jira or azure-devops tracker, the same: nothing is sent now, the bug goes in the Fail item of the run
   log (technique-jira-xray.md sections 2.8 and 5.2):
   - new bug -> "new_defect": {the keys of bugs.fields, with "title", "expected", "observed" always, and
     "preconditions", "steps" when known}; the priority, if any, in bugs.priorities;
   - existing bug -> "bug": "<key or id>";
   - a retest that confirms a fix -> "bug_status": "<status of bugs.statuses / state of bugs.states>" next to
     "bug" (resolved_bugs rule, section 9.3).
   It is sent at the next /checkpoint (apply: dry run, approval, --yes). A new_defect on an item whose status
   is not sent (skip or unmapped) is not created; the dry run lists it.
