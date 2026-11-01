---
name: dashboard
description: Builds and opens the current workspace's progress dashboard (progress per area and status, open defects, last checkpoint, check of the workbook's summary).
metadata:
  intent: "Show the progress of an Excel project: areas, statuses, open defects, last checkpoint."
  inputs: "The workspace's workbook and context.md."
  config: "tracker: excel; dashboard: states in project.yaml."
  tools: "Python; the workbook is only read."
  outputs: "dashboard.html, local, and a summary of the numbers."
  writes: "dashboard.html in the workspace (ignored by git). Never publishes it."
  approvals: "None; changing project.yaml needs a confirmation."
  failures: "Exit 2: nothing built, the message says why. Jira or Azure DevOps: stop, the tracker's reports serve."
  resume: "Run it again at any time."
  requires-scripts: "scripts/build_dashboard.py"
  requires-config: "excel"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill dashboard [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. In the steps below, the current workspace is the one it selected, and the documents named without a folder
   (methodology.md, technique-*.md, heuristics.md) are in <qa>/docs/.
1. <qa> and <qa>/scripts/build_dashboard.py are what the preflight confirmed.
2. Confirm that the current workspace has a project.yaml with tracker: excel. With jira or azure-devops, stop:
   the dashboard is Excel only by decision (plan 10.0; technique-jira-xray.md section 9), because one built
   from what this assistant sent would miss everyone else's results. Point the user to the tracker's own
   reports (the Xray test plan and execution reports, the Azure DevOps test plan's progress report) and, for
   this session's numbers, to /end-session's summary.
3. Confirm that dashboard.html is in the workspace's .gitignore. If it is not, add it and tell the
   user (the dashboard has project data and does not go into git).
4. From the root of qa-assistant, with the ABSOLUTE path of the workspace:
   python scripts/build_dashboard.py <workspace> --open
   The script only reads the workbook; it never saves it.
5. If it exits with 2, nothing was built: show the message (e.g. invalid dashboard.states section, or
   excel.summary.not_run_status different from the dashboard's pending status) and propose the fix to
   project.yaml. Do not change project.yaml without confirmation.
6. Summarise for the user what the script printed: cases run / total, open defects and the
   state of the workbook's summary.
   - "N difference(s) with the direct count": the summary has wrong formulas (technique-excel.md section 5);
     the dashboard's numbers are right, because they come from the case-by-case count. The formulas are
     fixed, never the numbers by hand.
   - "not checked -- ... recalculate first": the workbook has not been recalculated since it was last
     saved; it is checked at the next /checkpoint (apply recalculates).
7. Privacy (methodology.md section 1.2): the dashboard of a real project contains client data.
   Open it only locally. Never publish it (Artifact, gist, page), never attach it nor send it to
   any service, and never copy it into the qa-assistant repository.
