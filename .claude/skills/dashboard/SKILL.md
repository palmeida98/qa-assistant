---
name: dashboard
description: Builds and opens the current workspace's progress dashboard (progress per area and status, open defects, last checkpoint, check of the workbook's summary).
---
1. Find the root of qa-assistant (<qa>) from the current workspace: usually ../../../qa-assistant
   (workspace in qa-workspaces/<company>/<project>/); in the demo, inside the repository itself, it is ../..
   (examples/demo/). Confirm that <qa>/scripts/build_dashboard.py exists.
2. Confirm that the current workspace has a project.yaml with tracker: excel. With another tracker, tell the
   user the dashboard only supports excel for now and stop.
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
