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
  writes: "exports/<report>.md; for an Excel project, dashboard.html in the workspace (build_dashboard.py only reads the workbook and never saves it; the file is git-ignored, and .gitignore gains that one line only when it is missing, said to the user). Never a workbook, a run log or the tracker."
  approvals: "None to write the report or the dashboard. A change to a workbook (e.g. its summary's formulas) is not done here: it goes to /checkpoint, with its dry run and approval. Sending or posting the report anywhere is the user's."
  failures: "Results without platform or build are reported as unknown, never folded into another platform. Totals that do not reconcile: the report says so and stops short of percentages. A platform in scope with no result is reported as not tested, never as passed."
  resume: "The run logs are the source; the report is generated again from them, never edited by hand."
  requires-scripts: "scripts/qa_report.py, scripts/quarantine.py, scripts/build_dashboard.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/report.md, procedures/quarantine.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI) uses those paths
   instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when directly under the root; in the demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill qa-report [<project id or name>] [--workspace <path>]
   Exit 0: show it and go on. Any other exit, and the `Route:`, `Next:` and `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show what it says (nothing is installed or changed). Documents named without a folder are in
   <qa>/docs/, and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Fix and say the scope (batches, date, platform, PBI, plan). Then audit and count, from <qa>:
   python <qa>/scripts/qa_report.py <workspace> audit [--batch <b> ...] [--since <date>]
   python <qa>/scripts/qa_report.py <workspace> totals [--batch <b> ...] [--since <date>] [--plan batches/<p>.yaml]
   An audit problem (Question open, evidence missing, a case repeated, a result whose only evidence is invalid) is an open inconsistency; invalid evidence is named apart, never counted as valid. `--detail` lists every batch and flag.
2. Keep apart: historical results (older runs), recent results (the current result of each case), retests (before ->
   after with builds) and coverage still missing (planned and not run). Quarantine (procedures/quarantine.md) has its
   own section in the step 4 reports, never written by hand (`quarantine.py <workspace> report` shows the rows). Every total names its scope and every percentage its denominator; platforms and builds are
   never added into one figure, and nothing seen on one platform or build is said of another.
3. The rest comes from the records, with sources: per feature or acceptance criterion, the cases
   and their current result; open and new bugs; blocked cases with the reason (environment and tool failures apart from a Fail); Question results; platforms and builds not tested; the limits of the evidence; risks (uncovered
   criteria, Fails without a bug, old build, unknown platform or build) and the residual risk.
4. Write exports/<stem>.summary.md (short) and exports/<stem>.technical.md (complete) from the same run logs and
   audit (`output.reports` chooses which; both by default):
   python <qa>/scripts/qa_report.py <workspace> report [--batch <b> ...] [--since <date>] [--platform <p>] [--plan batches/<p>.yaml] [--name <stem>]
   Add to the technical report only what the session's records hold, never the quarantine rows. For an Excel project
   the dashboard is procedures/report.md section B; a summary with wrong formulas is reported, and fixed at /checkpoint.
5. In the conversation, a short professional summary for the team, in the workspace's language: scope and date, per
   platform the build and the totals with denominators, main bugs, blockers, next steps. Ready to paste; the user decides where it goes.
