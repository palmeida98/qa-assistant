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
3. Fill in CLAUDE.md and project.yaml with the answers. Add to CLAUDE.md, next to the other universal imports,
   the tracker's technique: <qa>/docs/technique-excel.md (excel), <qa>/docs/technique-jira-xray.md (jira), or
   both <qa>/docs/technique-jira-xray.md (the common contract, section 2) and <qa>/docs/technique-azure-devops.md
   (azure-devops).
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
4t. If the tracker is "jira" or "azure-devops", fill in the jira: / azure_devops: section of project.yaml with the
   user, from the template's commented section (technique-jira-xray.md section 1, technique-azure-devops.md
   section 1). ASK every value; never guess a project key, field id, status, plan or suite:
   a. Site / organisation URL and project; the credential variables ONLY as names in the *_var keys
      (jira: user_var, token_var, xray.client_id_var, xray.client_secret_var; azure-devops: pat_var). The values
      go to .env by the user (step 6), never here nor in the chat.
   b. Azure DevOps: ASK which process the project uses. Agile or Scrum (or one inherited from them) only; Basic
      has no Bug work item type and CMMI is not covered: stop and tell the user (technique-azure-devops.md
      section 0). Then plan_id, suite_ids, new_cases_suite_id (a static suite), area paths (full paths).
   c. Cases: id_source (default key in Jira / id in Azure DevOps; field or label/tag only when the project keeps
      its own IDs, with id_field or the prefix), the JQL (Jira), the folder / test plan, and cases.fields.
   d. Status map, the project's vocabulary for this tracker (jira: results.statuses; azure-devops:
      results.outcomes, from the closed list of outcomes): EVERY status of the project is a key. Propose the
      defaults of the technique's section 4.1 and ASK, per project, for Blocked and N/A (never assumed): in Xray
      a custom status that exists in the Xray settings, or ABORTED, or skip; in Azure DevOps Blocked /
      NotApplicable, or skip. skip (or empty) = not sent, it stays only in the run log. Question is never mapped.
   e. Azure DevOps: platform -> Test Configuration (results.configurations), needed when a case has several test
      points. Evidence: attach_evidence stays false unless the evidence policy is text+screenshot and the user
      wants the files uploaded (technique-jira-xray.md section 8).
   f. bugs: the fields from the project's bug format (bug_format), defaults, the statuses/states a retest may
      move a bug to (resolved_bugs rule), priorities; open_jql (jira) or closed_states (azure-devops).
   Validate with step 8a; check and import-cases come after .env has the values (step 8d).
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
7. The .gitignore comes in step 8c (from <qa>/docs/workspace/gitignore). Protect history/
   (methodology.md section 13): copy <qa>/docs/workspace/settings.json to <workspace>/.claude/settings.json; if it
   already exists, add "Edit(/history/**)" to permissions.deny without deleting the rest, and show the change.
8. Create the initial context.md, in the format of methodology.md section 12.2.
8a. For each active module with a section of its own (api:, accessibility:, mobile: -- methodology section
    4.1), fill in the section with the user (URLs only from the test environment; credentials only as names
    of .env variables). At the end validate, from the root of qa-assistant:
    python scripts/validate_project.py <workspace>
    and only call the onboarding done with 0 problems (the warnings are shown to the user).
8b. With mobile active (technique-mobile.md sections 1.1 and 4.1), ASK and record in mobile: of project.yaml:
    - app_id and the target: mobile.serial (a real device or a running emulator) OR mobile.avd + mobile.image
      (an emulator setup_emulator.py creates);
    - mobile.build: source (where the TEST build comes from) and sha256 of the file. The APK/IPA is never
      committed nor copied into the workspace's git;
    - mobile.permissions (grant / revoke);
    - for a conversational app (generated content): mobile.send_label, mobile.ignore_labels (the app's
      chrome) and mobile.browser (if the login is by e-mail link).
    With an emulator, show the plan without touching anything: python scripts/setup_emulator.py <workspace>
    --dry-run [--apk <path of the build>]
    (from the root of qa-assistant), then validate again (8a).
8c. With the files created, make the workspace a git repository of its own (methodology.md sections 4, step 4,
    and 12.3), from the root of qa-assistant:
    python scripts/workspace_git.py <workspace> init
    (git init on branch main, the .gitignore of the template, .env.example with the variable NAMES of project.yaml,
    the initial commit; exit 1 = refused, e.g. .env tracked: show why and fix it with the user). Then PROPOSE the
    private remote: show the command without running it
    python scripts/workspace_git.py <workspace> remote --create <owner>/<company>-<project>
    and, only if the user says yes, run it again with --yes (always --private; with no gh, the user creates a
    private repository and remote --set URL adds it). Do not push here: the push is the user's call (/checkpoint).
    Validate again (8a): "no remote" stays a warning if the user declined.
    To continue the project on another computer: <qa>/docs/workspace/resume.md (methodology.md section 12.3).
8d. With the jira or azure-devops tracker (methodology.md section 4, step 7), once the USER has put the values
    of the tracker's variables in .env (never read nor shown by the assistant), from the root of qa-assistant:
    python -m adapters.tracker <workspace> check
    (read only: authentication, project, process, issue/work item types, every mapped field, the mapped statuses
    or outcomes, the test plan and suites). Exit 4 = the problems listed: fix project.yaml with the user and run
    it again; 3 = network or authentication (a wrong or expired token: the user fixes .env). Then:
    python -m adapters.tracker <workspace> import-cases
    (read only, into tracker/cases.json and tracker/ids.yaml): tell the user how many cases came and show every
    flag (no steps, several expected results, no internal ID...) -- they are recorded in context.md, not fixed
    here; exit 2 = two tests with the same internal ID. Nothing is written to the tracker at the onboarding (the
    round trip is the adapter's golden, methodology section 15). Commit tracker/ in the workspace.
9. Never write this project's information into the universal layers (docs/ of qa-assistant).
