---
name: manage-bugs
description: "Investigates failures, looks for existing bugs (open and closed) and prepares bug proposals or updates of existing bugs for one batch review; records the approved ones in the run log. Use for 'prepare the bugs', 'is this a known bug?', 'review the bugs of the batch', 'log this failure', after /run-tests."
argument-hint: "[batch name]"
metadata:
  intent: "Turn the failures of a run into reviewed bug proposals, without duplicates and without publishing on its own."
  inputs: "The Fail (and suspicious Question) items of a run log, or a failure the user describes with its evidence."
  config: "bug_format and the tracker's bug fields (excel.defects, jira:/azure_devops: bugs) of project.yaml; the workspace's rules on what is not a bug and on bug fields."
  tools: "The defects sheet through the Excel adapter (read) or python -m adapters.tracker bug-search (read); the ticket memory of /analyze-ticket; run_log.py to record the approved items."
  outputs: "A batch review: per failure, the evidence, the requirement it breaks, duplicate candidates with their state, and the proposal (one bug per problem, grouping platforms only when the evidence shows the same defect; an update of an existing one; a comment on the bug of the other platform; or not a bug) in the project's format."
  writes: "The review file in runs/; after approval, the item of the case in the run log (new_defect or bug, added with run_log.py attach, which keeps its result and evidence)."
  approvals: "The user approves the review item by item. Publishing to a tracker, a comment or an attachment needs an explicit request for that set of changes."
  failures: "The tracker out of reach: said, and the search stays pending, never skipped silently. Not reproducible or unclear: Question, not a bug."
  resume: "The review file and the run log; an item already approved or published (memory/pbi/ record, tracker key) is never proposed or published again."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/run_log.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/bugs.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints (the one with the framework's dependencies; exit 3 says how to create it, nothing is installed for you). Then:
   python <qa>/scripts/workspace_select.py preflight --skill manage-bugs [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Collect the failures of the batch (its run log) and their evidence. Follow <qa>/docs/procedures/bugs.md for the
   search, the format and the run log fields.
2. Investigate each: the steps that reproduce it, the requirement or decision it breaks (with its source), whether a
   more recent decision changed the expectation, and whether it is a copy change, a new request or a defect already
   open on the other platform (the workspace's CLAUDE.md says what is not a bug there).
3. Search before proposing: the defects of the workbook or bug-search in the tracker (open and closed, --include-closed
   when it may be a regression), and the related bugs /analyze-ticket recorded. Say how many were found and how many
   were read. Similar symptoms do not prove the same defect: the same defect needs the same steps, screen or flow and
   result. A tool failure, a block or inconclusive evidence is not a bug (rules.md section 4).
4. One bug per problem. The same defect shown by the evidence on Android, iOS or web: one bug with the platforms,
   devices or browsers, OS, builds, environment, steps, observed result and evidence of each. A relevant difference
   (other steps or result, only one platform reproduced, another build): separate bugs, related when a link is known.
   A link not shown stays uncertain and is said so, never forced into one bug nor claimed equal. A platform not tested
   is never named as affected or validated. A defect that reproduces needs no root cause: say when it is unknown. A
   defect already open on the other platform with the same evidence: propose a comment on that bug with this
   platform and build, not a new bug. The workspace's rules on what is a bug and on grouping win where they ask for
   more (rules.md section 8).
5. Write the batch review in runs/<batch>_bugs_review.md: per item the proposal in the project's bug format
   (CLAUDE.md order of fields, steps, expected -> observed, environment, device, build, evidence path with the bug id
   once it has one), mapped to the tracker's real fields after reading its form (procedures/bugs.md, "Mapping a bug
   to the tracker's fields": what goes in each field and what stays pending). Present all items together at the end
   of the batch and wait. Priority: run
   python <qa>/scripts/run_log.py <workspace> defect-rule. When the project's schema REQUIRES a priority (Excel with
   excel.defects.priorities), each bug in the review shows the accepted values and a line "priority: waiting for your
   decision"; the bug is not ready to apply until the user gives one of them (a suggestion with its reason only if the
   user asks, as a proposal to approve, never applied on its own). Severity only when the user asks.
6. After the user approves an item, record it in the run log (procedures/bugs.md steps 6 to 10). A case that already
   has its result in the batch keeps it (status, observed text, time, evidence, run block):
   python <qa>/scripts/run_log.py <workspace> attach <batch> <case> --new-defect <draft.json>
   or --bug <id> for an existing one. A failure the user described and not yet in the run log is written with
   run_log.py add (its status and observed text) and --new-defect or --bug. It reaches the workbook or the tracker at
   /checkpoint, after the confirmation of that batch's dry run.
7. Publishing to a tracker outside /checkpoint (a bug the user creates, a comment): only when the user asks for that
   item; show the exact text first; under /test-pbi record the key with pbi_state.py published, which refuses a second
   publication of the same draft. After publishing or correcting a bug, reopen it and verify each field and the rendered form
   (same section of procedures/bugs.md); a save or equal text alone does not validate it.
   Creating a bug through the write guard (technique-ado-write-guard.md, «Final preview of a bug's fields»): build the
   fields from the project's mapping (write_guard.bugs of project.yaml: extra_fields only for its keys), run
   ado_guard_preview_bug with the evidence to attach and the creation comment, and show the QA its preview_text as it
   is: each field by its label in the Board with its exact value, the Parent, Assigned To empty, the checks and notices
   (a QA account «to confirm»), the evidence (a reviewed video only when the captures cannot show the occurrence) and
   the comment as separate operations, and payload_sha256 and patch_sha256. A preview with ready false is not
   proposed for approval. Create only with the hashes the QA approved; a field, its mapping or the parent that changed
   since gives other hashes: preview again and ask again. Attachments and the comment follow with their own approvals.
