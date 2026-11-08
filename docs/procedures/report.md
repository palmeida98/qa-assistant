# Closing and reporting
> Read by /qa-report: what the report cross-checks (A) and the Excel dashboard (B).
> Moved from the version 1 skills (commit 7e7cabf) with their names updated (skills-guide.md §6). Step numbers are the original ones.

## A. Cross-check and summary

The steps of /qa-report are in its SKILL.md: it audits and counts (scripts/qa_report.py) and writes only its report in
exports/. Closing a session (applying batches, updating context.md) is /checkpoint's; reviewing bugs is /manage-bugs'.
/qa-report names what is still to do there and does none of it. The cross-check of what was sent to Jira or Azure
DevOps (each run log against tracker/sent/<batch>.json; exit 3 or 5 of a send is an open inconsistency) and the
summary format of methodology section 16.3 stay the references of the report.

## B. Dashboard (Excel only) (from /dashboard)

1. <qa> and <qa>/scripts/build_dashboard.py are what the preflight confirmed.
2. Confirm that the current workspace has a project.yaml with tracker: excel. With jira or azure-devops, stop:
   the dashboard is Excel only by decision (plan 10.0; technique-jira-xray.md section 9), because one built
   from what this assistant sent would miss everyone else's results. Point the user to the tracker's own
   reports (the Xray test plan and execution reports, the Azure DevOps test plan's progress report) and, for
   this session's numbers, to /qa-report's summary.
3. Confirm that dashboard.html is in the workspace's .gitignore. If it is not, add that one line and tell the
   user (the dashboard has project data and does not go into git). That line and dashboard.html are the only files
   this section writes.
4. From the root of qa-assistant, with the ABSOLUTE path of the workspace:
   python scripts/build_dashboard.py <workspace> --open
   The script only reads the workbook; it never saves it. Any change to a workbook (e.g. a summary's formulas) is
   /checkpoint's, after its dry run and the user's approval.
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
