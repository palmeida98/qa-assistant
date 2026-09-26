---
name: project-onboarding
description: Sets up a new project workspace - environments, roles, tracker, bug format - following methodology.md section 4.
---

1. Find the root of qa-assistant (<qa>) from the current workspace: usually ../../../qa-assistant
   (workspace in qa-workspaces/<company>/<project>/); in the demo, inside the repository itself, it is ../..
   (examples/demo/). Confirm that <qa>/docs/methodology.md exists before continuing.
   Check whether CLAUDE.md and project.yaml exist in the current workspace. If not, copy them from:
   - <qa>/docs/context-template.md -> CLAUDE.md (and adjust the @ imports to the same <qa>)
   - <qa>/docs/project.yaml -> project.yaml
   A workspace with projeto.yaml is from before the translation (plan 8.11): convert it first with
   python scripts/migrate_workspace.py <workspace> (from the root of qa-assistant).
2. Read CLAUDE.md and project.yaml. For each blank field, ASK the user
   (do not assume nor invent):
   - environments (confirm which one is the TEST environment, not production)
   - roles/profiles and access differences
   - names of the credential variables (the values go to .env, never here)
   - reference documentation
   - structure of the workbook or tracker
   - required bug format
   - rule for resolved bugs (remove or archive)
   - evidence policy (text or text+screenshot)
   - active test modules: only those of the closed list in methodology.md section 4.1 (functional-web,
     exploratory, api, accessibility, mobile); show the list and what each one implies
3. Fill in CLAUDE.md and project.yaml with the answers. With the excel tracker, add to CLAUDE.md
   the import of <qa>/docs/technique-excel.md, next to the other universal imports.
4. If the tracker is "excel", fill in the excel: section of project.yaml by INSPECTING the real workbook
   (technique-excel.md section 1; never guess sheet, column or status names):
   a. Run, from the root of qa-assistant, on a local COPY of the workbook (never the official one
      in a synced folder; the script only reads, but the copy avoids locks):
      python -m adapters.excel.inspect <copy.xlsx> --yaml-out <scratchpad>/proposal.yaml
   b. Show the user the evidence found and the proposal. Everything that comes as
      "CONFIRM: ..." is ASKED, with the options the script listed: status column,
      actual result and notes, valid statuses, closed statuses (aligned with the resolved_bugs
      rule), initial status of a new defect, order of the priorities, mapping of the active view's
      columns, summary text (if any).
   c. Also show the inconsistencies the inspection reveals (e.g. IDs outside the format, validation
      lists that do not match the values used) -- they are recorded, not fixed here.
   d. Write the excel: section into project.yaml, with no "CONFIRM" left unresolved.
   e. Validate: ExcelAdapter.from_project(<workspace>).load() must pass (it fails if a sheet or
      column is missing) and cross_check() only to show problems already in the workbook. Do not
      apply batches nor save the workbook during the onboarding.
   f. If the workbook has a totals sheet with formulas (summary per area and status), fill in
      excel.summary (sheet, header_row, area_column, total_column, total_row and, if the headers are
      not the status names, status_columns) from the real headers, and confirm with the user. If
      there is a % run column, fill in percent_column and not_run_status (the same status that will
      be "pending" in the dashboard: section); percent_tolerance only if the summary rounds on
      purpose. It serves to check the formulas against the direct count (technique-excel.md
      section 5); the totals are never read from the summary.
   g. Fill in the dashboard: section of project.yaml (scripts/build_dashboard.py): for each status of
      excel.cases.statuses, ASK the type -- pending, ok, fail, blocked or na. Propose the types from
      the names, but the user confirms (the vocabulary is the project's).
5. If a browser module is active (e.g. functional-web), register the Playwright MCP IN THE WORKSPACE
   (technique-browser.md section 1, level N1). The command runs from the workspace's folder, because
   --scope project writes .mcp.json in the current folder:
   - Windows: claude mcp add playwright --scope project -- cmd /c npx @playwright/mcp@0.0.82 --secrets .env --isolated
     (without "cmd /c" Claude Code cannot launch npx on Windows)
   - Linux/macOS: claude mcp add playwright --scope project -- npx @playwright/mcp@0.0.82 --secrets .env --isolated
   (fixed version, the same as in examples/demo/.mcp.json; never @latest: technique-browser.md section 1.
   --secrets .env: at login the variable's NAME is written and the MCP puts the value; the assistant
   never reads the value: technique-browser.md section 3. --isolated: a clean browser profile in each
   session; with the persistent profile clicks stopped reaching the page: technique-browser.md section 1)
   Then:
   a. Confirm that .mcp.json is in the workspace's folder and NEVER in the root of qa-assistant
      (the only exception: the demo, in examples/demo/).
   b. Confirm that .mcp.json has no credentials nor client URLs.
   c. Tell the user that, when opening claude in the workspace for the first time, they must approve
      the server (project servers ask for confirmation) and check with /mcp that "playwright"
      shows as connected. This session does not see the tools of the MCP registered now.
6. Create .env with the credential variables identified, WITHOUT values.
7. Confirm that .env, dashboard.html and .playwright-mcp/ are in the workspace's .gitignore. Protect history/
   (methodology.md section 13): copy <qa>/docs/workspace/settings.json to <workspace>/.claude/settings.json; if it
   already exists, add "Edit(/history/**)" to permissions.deny without deleting the rest, and show the change.
8. Create the initial context.md, in the format of methodology.md section 12.2.
8a. For each active module with a section of its own (api:, accessibility:, mobile: -- methodology section
    4.1), fill in the section with the user (URLs only from the test environment; credentials only as names
    of .env variables). At the end validate, from the root of qa-assistant:
    python scripts/validate_project.py <workspace>
    and only call the onboarding done with 0 problems (the warnings are shown to the user).
9. Never write this project's information into the universal layers (docs/ of qa-assistant).
