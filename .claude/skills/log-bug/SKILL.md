---
name: log-bug
description: Logs or updates a bug from a Fail case, looking for duplicates first.
---
1. Read the bug format in project.yaml (bug_format and, with the excel tracker, excel.defects) and in the
   current workspace's CLAUDE.md.
2. Look for existing bugs with a similar area/feature/sub-area/platform/role (section 9.1).
   With the excel tracker, read the defects sheet through the adapter (ExcelAdapter.from_project(...).load()),
   without saving.
3. If you find a duplicate candidate, show it and ask: update the existing one or create a new one.
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
7. With another tracker, prepare the bug in that tracker's format and only send it once approved.
