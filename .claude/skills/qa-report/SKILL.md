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
  requires-scripts: "scripts/qa_report.py, scripts/build_dashboard.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/report.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints (the one with the framework's dependencies; exit 3 says how to create it, nothing is installed for you). Then:
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
   An audit problem (Question open, evidence missing, a case repeated, a result whose only evidence is marked invalid) is reported as an open inconsistency; evidence marked invalid is named apart (kept on file, not proof), never counted as valid. The audit prints a summary by default (`--detail` lists every batch and flag).
2. Keep apart: historical results (older runs), recent results (the current result of each case), retests (before ->
   after with builds) and coverage still missing (planned and not run). Every total names its scope and every
   percentage its denominator; platforms and builds are never added into one figure, and nothing seen on one
   platform or build is said of another.
3. The rest of the report comes from the records, with their sources: per feature or acceptance criterion, the cases
   and their current result; open and new bugs (from the run logs and the bug review); blocked cases with the reason,
   a block of the environment and a failure of the tool apart from a Fail; inconclusive results (Question); the
   platforms and builds in scope that were not tested; the limits of the evidence (a result whose evidence is
   missing or does not show it); risks (uncovered criteria, Fails without a bug, results on an old build, unknown
   platform or build) and the residual risk.
4. Write the reports: exports/<stem>.summary.md (short) and exports/<stem>.technical.md (complete), from the same run
   logs and audit; the project's `output.reports` chooses which (both by default):
   python <qa>/scripts/qa_report.py <workspace> report [--batch <b> ...] [--since <date>] [--plan batches/<p>.yaml] [--name <stem>]
   Add to the technical report what only the records of the session hold (features, bugs, risks). `totals --output`
   still gives the totals part alone. For an Excel project the dashboard is procedures/report.md section B: it writes
   dashboard.html and reads the workbook without saving it; a summary whose formulas are wrong is reported, and fixed
   at /checkpoint.
5. In the conversation, a short summary for the team, professional and structured, in the workspace's language: scope
   and date, per platform the build and the totals with denominators, main bugs, blockers, next steps. Ready to paste;
   the user decides where it goes.
