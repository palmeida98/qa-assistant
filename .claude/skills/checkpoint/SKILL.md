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
  requires-scripts: "scripts/run_log.py, scripts/qa_report.py, scripts/compact_context.py, scripts/workspace_git.py, scripts/pbi_state.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/apply.md, methodology.md, technique-excel.md"
  requires-tools: "git"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints (the one with the framework's dependencies; exit 3 says how to create it, nothing is installed for you). Then:
   python <qa>/scripts/workspace_select.py preflight --skill checkpoint [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Save every result of the session not yet in a run log, with run_log.py (procedures/apply.md steps 1 to 3); evidence
   or a bug for a result already there with run_log.py attach, which keeps the rest of it. Never write to history/.
   A Question stays Question, with its concrete question, until the user answers it (the answer, its reason and who
   decided it are asked, never assumed): then python <qa>/scripts/run_log.py <workspace> resolve <batch> <case>
   <status> --reason "<why>" --decided-by "<who>" [--decision-source "<where>"] [--into <new batch>] keeps the
   observation, its time and evidence and records the decision (procedures/apply.md section "Resolving a Question");
   a batch already applied needs --into, and that batch is applied with its own dry run and approval. Recording the
   decision is not applying it, and it never replaces an approval the project's own rules ask for (for example the
   workspace's CLAUDE.md or context.md requiring a specific decision, a dry run review or an approval per platform):
   those rules still prevail and are asked as written. Steps 1 to 4 are local and need no approval; applying
   (step 5) and publishing are separate acts with their own.
2. Validate, read only:
   python <qa>/scripts/qa_report.py <workspace> audit --since <date of the session>
   Every evidence file named must exist; Questions and proposed bugs are listed; a batch with a problem is not
   applied. Check also that context.md says nothing the records contradict (a case "pending" that has a result, a
   task "done" without one).
3. Update context.md (methodology.md section 12.2; procedures/apply.md steps 4 to 5a) with localised edits that keep
   independent content: a short resume point (what was done, the exact point reached per platform and the build of
   each run); decisions with who and when; blockers with what unblocks them; pending items with their dates (a past
   time is history, never rewritten as a schedule); open questions; validation state. A decision is never dropped:
   older entries go word for word to archive/context/ with compact_context.py, and context.md says where.
4. Show what is recorded locally and what is still pending, as a list: batches not applied (and the dry run to run);
   bugs to review or to publish; tracker comments the user asked for and not yet published; files not committed
   (git status, read only); commits not pushed (python <qa>/scripts/workspace_git.py <workspace> status).
5. Applying to the workbook or the tracker only after the user confirms that batch: procedures/apply.md steps 7 and
   7a. Excel: python -m adapters.excel.apply <workspace> <batch> --dry-run, show the cells, counts and problems it
   lists, then after the approval the apply with --expect <token>, repeating the SAME --label, --reported-by, --now and
   --no-recalc the dry run was given (the token `v2-...` covers them: a different one, or a token from before it, is
   refused before any write: preview again, and ask again; an approval of that same preview, still valid, is not asked a second time).
   Jira / Azure DevOps: dry run, approval, --yes. The exit codes as written there. The workspace's own rule on
   workbooks wins when stricter.
6. Running this skill does not commit or push. Commit only when the user asks or the workspace's CLAUDE.md makes
   it part of the checkpoint (procedures/apply.md step 6); push only when the user says so (step 6a).
7. Under /test-pbi, saving the progress is not closing the flow (procedures/rules.md section 7). Each item of step 4
   that waits on someone (a dry run to approve, a bug to publish, a dependency, a question) is an open item of the
   PBI's record: update the one already there, or open it with a stable id, never a second one for the same work;
   close it only with a verifiable confirmation (`pbi_state.py close`, with who decided for an approval or a question).
   An intermediate checkpoint, with work still open:
   python <qa>/scripts/pbi_state.py <workspace> saved <pbi> --ref context.md [--note "<what is left>"]
   Only the checkpoint that closes the PBI's flow (after its report, with nothing pending, blocked or open) marks:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> checkpoint done --ref context.md
   The record refuses it (exit 4) while work is open; never mark the step done to save progress. Closing with work
   open is the QA's explicit decision only: add --close-with-open --decided-by "<the QA>" (what stayed open is kept).
   Saving or resuming never replaces an approval: the dry run and its token, and the write guard's approved content,
   are asked and checked as in step 5.
