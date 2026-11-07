---
name: manage-bugs
description: "Investigates failures, looks for existing bugs (open and closed) and prepares bug proposals or updates of existing bugs for one batch review; records the approved ones in the run log. Use for 'prepare the bugs', 'is this a known bug?', 'review the bugs of the batch', 'log this failure', after /run-tests."
argument-hint: "[batch name]"
metadata:
  intent: "Turn the failures of a run into reviewed bug proposals, without duplicates and without publishing on its own."
  inputs: "The Fail (and suspicious Question) items of a run log, or a failure the user describes with its evidence."
  config: "bug_format and the tracker's bug fields (excel.defects, jira:/azure_devops: bugs) of project.yaml; the workspace's rules on what is not a bug and on bug fields."
  tools: "The defects sheet through the Excel adapter (read) or python -m adapters.tracker bug-search (read); the ticket memory of /analyze-ticket; run_log.py to record the approved items."
  outputs: "A batch review: per failure, the evidence, the requirement it breaks, duplicate candidates with their state, and the proposal (new bug, update of an existing one, comment on the bug of the other platform, or not a bug) in the project's format."
  writes: "The review file in runs/; after approval, the item of the case in the run log (new_defect or bug)."
  approvals: "The user approves the review item by item. Publishing to a tracker, a comment or an attachment needs an explicit request for that set of changes."
  failures: "The tracker out of reach: said, and the search stays pending, never skipped silently. Not reproducible or unclear: Question, not a bug."
  resume: "The review file and the run log; an item already approved or published (memory/pbi/ record, tracker key) is never proposed or published again."
  requires-modules: "adapters.excel, adapters.tracker"
  requires-scripts: "scripts/run_log.py"
  requires-docs: "procedures/rules.md, procedures/bugs.md, methodology.md"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
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
   were read. Similar symptoms do not prove the same defect: the same cause needs the same steps, screen and result.
4. The same defect confirmed on Android and iOS: one bug with the platforms, devices, builds and evidence of each. A
   defect already open on the other platform: propose a comment on that bug with this platform and build, never a
   new bug.
5. Write the batch review in runs/<batch>_bugs_review.md: per item the proposal in the project's bug format
   (CLAUDE.md order of fields, steps, expected -> observed, environment, device, build, evidence path with the bug id
   once it has one). Severity and priority only when the user asks. Present all items together at the end of the
   batch and wait.
6. After the user approves an item, record it in the run log (procedures/bugs.md steps 6 to 10):
   python <qa>/scripts/run_log.py <workspace> add <batch> <case> Falhou|Fail "<observed>" --new-defect <draft.json>
   or --bug <id> for an existing one. It reaches the workbook or the tracker at /checkpoint, after its own confirmation.
7. Publishing to a tracker outside /checkpoint (a bug the user creates, a comment): only when the user asks for that
   item; show the exact text first; under /test-pbi record the key with pbi_state.py published, which refuses a second
   publication of the same draft.
