---
name: checkpoint
description: "Validates and saves the session's state: results in the run log, a short resume point in context.md, decisions, blockers and next steps, and the list of what is recorded locally versus still to apply or publish. Applies a batch to the workbook or tracker only after the user confirms it. Use for 'save', 'save the state', 'checkpoint', 'close the session', 'before I leave', and every 15 to 20 cases."
argument-hint: "[batch to apply]"
metadata:
  intent: "Keep the workspace's memory consistent and resumable, and show what is still pending application or publication."
  inputs: "The session's results and decisions; the batch to apply when the user confirms it."
  config: "run_log; the tracker's section (excel:, jira:, azure_devops:); the workspace's CLAUDE.md rules on commits and approvals."
  tools: "run_log.py, qa_report.py audit, compact_context.py, workspace_git.py status; the adapters for applying."
  outputs: "context.md updated; the consistency check; the pending list (batches to apply, bugs to review or publish, comments, uncommitted files, commits not pushed)."
  writes: "runs/ (results not yet saved), context.md, archive/context/ (compaction), under /test-pbi memory/pbi/<pbi>.json (open items, intermediate checkpoints); the workbook or the tracker only after confirmation; a commit only when the user or the workspace's CLAUDE.md asks for it."
  approvals: "Saving and validating the local state (run log, context.md, the audit): none. Applying: the user confirms the batch after the dry run; a confirmation of the same preview whose token is still valid is not asked again. Commit: the user or the workspace's rules. Push: only when the user says so."
  failures: "An inconsistency (Question open, evidence missing, a case repeated) is shown and the batch is not applied. The adapters' exit codes are followed, nothing is forced."
  resume: "context.md is the resume point; the detail stays in runs/, analysis/, memory/ and archive/context/."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/run_log.py, scripts/qa_report.py, scripts/compact_context.py, scripts/workspace_git.py, scripts/pbi_state.py, scripts/quarantine.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/apply.md, procedures/quarantine.md, procedures/checkpoint-detail.md, methodology.md, technique-excel.md"
  requires-tools: "git"
---

0. Preflight (read only), once per skill and workspace in a session. A run started by a launcher that already validated the workspace uses those paths
   instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa> (the root of qa-assistant): $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace. `python` below is what `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill checkpoint [<project id or name>] [--workspace <path>]
   Exit 0: go on; other exits, `Route:`, `Next:`, `Ready:`: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show it. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Save every result of the session not yet in a run log with run_log.py (apply.md steps 1 to 3; attach for evidence or a bug). Never write to history/. A Question stays Question until the user answers it; then run_log.py resolve (apply.md, "Resolving a Question"), which is not applying and never replaces an
   approval the project's rules ask for. Steps 1 to 4 need none.
2. Validate (read only): python <qa>/scripts/qa_report.py <workspace> audit --since <date of the session>
   Evidence must exist; a batch with a problem is not applied; context.md must not contradict the records.
3. Update context.md (methodology.md section 12.2; apply.md steps 4 to 5a), localised edits: resume point (per
   platform, with the build), decisions, blockers, open questions, pending items with their dates (a past time is
   history, never rewritten as a schedule). A decision is never dropped: compact_context.py archives it word for word.
4. List what is recorded locally and what is pending: batches not applied, bugs to review or publish, comments not
   published, files not committed, commits not pushed (python <qa>/scripts/workspace_git.py <workspace> status).
5. Apply to the workbook or tracker only after the user confirms that batch (apply.md steps 7 and 7a, their exit
   codes). Dry run, then after the approval the apply with --expect <token> (or --yes)
   and the SAME options; a stale preview is asked again, an approval of that same preview, still valid, is not
   asked a second time. A stricter workspace rule wins.
6. Running this skill does not commit or push. Commit only when the user asks or the workspace's CLAUDE.md makes
   it part of the checkpoint (apply.md step 6); push only when the user says so (step 6a).
7. Under /test-pbi, saving the progress is not closing the flow (procedures/rules.md section 7). Each item of step 4
   that waits on someone is an open item of the PBI's record (never a second for the same work), closed only with
   a verifiable confirmation. With work open:
   python <qa>/scripts/pbi_state.py <workspace> saved <pbi> --ref context.md [--note "<what is left>"]
   Only the checkpoint that closes the flow (nothing open) marks:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> checkpoint done --ref context.md
   Never mark it done to save progress. Closing with work open, quarantine: procedures/checkpoint-detail.md.
