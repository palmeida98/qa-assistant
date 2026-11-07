---
name: qa-report
description: "Audits the run logs and produces a traceable QA report by platform, build, feature, result and risk, with reconciled totals, plus a short summary ready to share with the team. Use for 'prepare the Android and iOS status for the team', 'report', 'how far are we', 'summary for the team'."
argument-hint: "[batch|PBI|since YYYY-MM-DD] [platform]"
metadata:
  intent: "Report what was really tested, on which platform and build, with totals that add up and the risks that remain."
  inputs: "A scope: a PBI, batches, a date, a platform or a plan (batches/<name>.yaml); by default the work since the last report."
  config: "run_log, report.platforms (which batches belong to which platform) and the tracker of project.yaml."
  tools: "qa_report.py totals and audit (read only); build_dashboard.py for an Excel project."
  outputs: "A technical report (exports/) and a short summary for the team in the conversation."
  writes: "exports/<report>.md only."
  approvals: "None to write the report. Sending or posting it anywhere is the user's."
  failures: "Results without platform or build are reported as unknown, never folded into another platform. Totals that do not reconcile: the report says so and stops short of percentages."
  resume: "The run logs are the source; the report is generated again from them, never edited by hand."
  requires-scripts: "scripts/qa_report.py, scripts/build_dashboard.py"
  requires-docs: "procedures/rules.md, procedures/report.md, methodology.md"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill qa-report [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Fix the scope and say it: the batches, the date, the platform, the PBI and its plan. Then audit and count, from <qa>:
   python <qa>/scripts/qa_report.py <workspace> audit [--batch <b> ...] [--since <date>]
   python <qa>/scripts/qa_report.py <workspace> totals [--batch <b> ...] [--since <date>] [--plan batches/<p>.yaml]
   An audit problem (Question open, evidence missing, a case repeated) is reported as an open inconsistency.
2. Keep apart: historical results (older runs), recent results (the current result of each case), retests (before ->
   after with builds) and coverage still missing (planned and not run). Every total names its scope and every
   percentage its denominator; platforms and builds are never added into one figure, and nothing seen on one
   platform or build is said of another.
3. The rest of the report comes from the records, with their sources: per feature or acceptance criterion, the cases
   and their current result; open and new bugs (from the run logs and the bug review); blocked cases with the reason;
   risks (uncovered criteria, Fails without a bug, results on an old build, unknown platform or build).
4. Write the technical report to exports/qa_report_<scope>_<YYYYMMDD>.md (qa_report.py --output gives the totals
   part). For an Excel project the dashboard is procedures/report.md section B.
5. In the conversation, a short summary for the team, professional and structured, in the workspace's language: scope
   and date, per platform the build and the totals with denominators, main bugs, blockers, next steps. Ready to paste;
   the user decides where it goes.
