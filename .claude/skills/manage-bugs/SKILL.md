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
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/bugs.md, procedures/quarantine.md, procedures/manage-bugs-detail.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI) uses those paths
   instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace. `python` below is what `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill manage-bugs [<project id or name>] [--workspace <path>]
   Exit 0: show what it printed and go on. Any other exit, and the `Route:`, `Next:` and `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show what it says. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Collect the batch's failures (run log) and evidence; procedures/bugs.md gives the search, format and run log fields.
2. Investigate each: criteria in procedures/manage-bugs-detail.md.
3. Search before proposing (procedures/bugs.md, --include-closed for a possible regression); say how many were found and read. Similar symptoms do not prove the same defect: it needs the
   same steps, screen or flow and result. A tool failure, a block or inconclusive evidence is not a bug (rules.md
   section 4).
4. One bug per problem. The same defect shown by the evidence on Android, iOS or web: one bug with the platforms,
   devices or browsers, OS, builds, steps, result and evidence of each. A relevant difference:
   separate bugs, related when a link is known. A link not shown stays uncertain and is said so. A platform not tested
   is never named as affected or validated. A defect that reproduces needs no root cause: say when unknown. The
   same defect already open on the other platform: propose a comment on that bug, not a new one (rules.md section 8).
5. Write the batch review in runs/<batch>_bugs_review.md: per item the proposal in the project's bug format. Present all items together at the end and wait.
   Priority and severity (`run_log.py defect-rule`): procedures/manage-bugs-detail.md.
6. After the user approves an item, record it in the run log (procedures/bugs.md steps 6 to 10); a case that already
   has its result keeps it:
   python <qa>/scripts/run_log.py <workspace> attach <batch> <case> --new-defect <draft.json>
   or --bug <id> for an existing one; a failure not yet in the run log: run_log.py add, with --new-defect or --bug.
   It reaches the workbook or the tracker at /checkpoint, after that batch's dry run.
7. Publishing outside /checkpoint (a bug the user creates, a comment): only when the user asks for that item; show the
   exact text first; under /test-pbi record the key with pbi_state.py published, which refuses a second publication of
   the same draft. Then reopen the bug and verify it (procedures/bugs.md, "Verify after publishing"). Creation through the write guard: procedures/manage-bugs-detail.md.
