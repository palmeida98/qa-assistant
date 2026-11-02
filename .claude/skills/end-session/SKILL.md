---
name: end-session
description: "Closes the test session: final checkpoint, bugs, cross-check and summary."
metadata:
  intent: "Close the test session: final checkpoint, bugs, cross-check and summary."
  inputs: "The session's run logs, bugs and open questions."
  config: "The tracker's section; resolved_bugs."
  tools: "git; the project's adapter; the dashboard with Excel."
  outputs: "Consistency checked, the summary of methodology 16.3, next steps."
  writes: "context.md and what /checkpoint writes."
  approvals: "As in /checkpoint and /log-bug."
  failures: "A known inconsistency is said aloud: the session is not called done."
  resume: "context.md says what stayed open."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/build_dashboard.py"
  requires-tools: "git"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill end-session [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. A `Route:` line names the skill that fits this project: say so and continue with that one. A `Next:`
   line is the one command to run before the work. In the steps below, the current workspace is the one it selected, and the documents named without a folder
   (methodology.md, technique-*.md, heuristics.md) are in <qa>/docs/.
1. Confirm that no case was left in Question without being presented to the user; reclassify with the
   answers (section 8.5). The adapter refuses batches with a Question.
2. Make the last checkpoint with /checkpoint (methodology.md section 16.2), including applying the
   batches to the workbook/tracker, with the user's confirmation.
3. Update new, existing and resolved bugs (section 9.3) with /log-bug.
4. Cross-check between cases, results, bugs and totals (section 11). With the excel tracker, the adapter's
   cross-check must give 0 problems (adapters.excel.apply exits with 0). With the jira or azure-devops tracker,
   compare each batch's run log with its record <workspace>/tracker/sent/<batch>.json: every batch sent (apply
   without --yes exits with 0 and makes no request: "Already sent with the same content"), every case of the run
   log in the record or listed as not sent (skip/unmapped), the keys of the bugs created noted for the summary.
   An incomplete record (exit 3), a read back difference (exit 5) or a batch not sent is an open inconsistency.
5. Produce the summary in the format of section 16.3, with the numbers read from the workbook (excel) or from
   the run logs and the records of what was sent and read back (jira, azure-devops) -- never from memory.
6. Update the workspace's context.md (section 12.2), with the next steps.
7. With jira or azure-devops there is no dashboard: point the user to the tracker's own reports (the Xray test
   plan / execution, the Azure DevOps test plan's progress). With the excel tracker, build the dashboard from the root of qa-assistant:
   python scripts/build_dashboard.py <workspace> --open   (the same as /dashboard)
   and tell the user where dashboard.html is. Do not declare the session done with known inconsistencies
   left unresolved (section 16.2).
