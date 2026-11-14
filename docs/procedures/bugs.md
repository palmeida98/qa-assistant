# Managing bugs
> Read by /manage-bugs.
> Moved from the version 1 skills (commit 7e7cabf) with their names updated (skills-guide.md §6). Step numbers are the original ones.

## Steps (from /log-bug)

1. Read the bug format in project.yaml (bug_format and, with the excel tracker, excel.defects) and in the
   current workspace's CLAUDE.md.
2. Look for existing bugs with a similar area/feature/sub-area/platform/role (section 9.1).
   With the excel tracker, read the defects sheet through the adapter (ExcelAdapter.from_project(...).load()),
   without saving.
   With the jira or azure-devops tracker, write the draft to <workspace>/tracker/drafts/<case>.json -- the keys of
   bugs.fields (title, area, feature, sub_area, platform, role...) plus "observed" -- and search, from the root
   of qa-assistant (read only; technique-jira-xray.md section 5.1):
   python -m adapters.tracker <workspace> bug-search tracker/drafts/<case>.json
   (--include-closed when it may be a regression of a fixed bug). 4 = invalid draft or configuration; 3 = the
   tracker out of reach: tell the user, never skip the search silently.
3. If you find duplicate candidates, keep them with the proposal (key, status, summary, criteria matched): in the
   batch review the user decides whether to update one of them or create a new one, never the adapter nor the
   assistant.
4. Write: title in the project's format, environment and build, role and platform, preconditions,
   steps to reproduce, expected -> observed, linked cases. Several platforms in one bug only when the evidence shows
   the same defect on each: then per platform the environment, device or browser, OS, build observed, steps,
   observed result and evidence; a platform not tested is never listed as affected. Relevant differences: separate
   bugs, related when a link is known; a link not shown is said to be uncertain. The cause may be unknown: say so.
   Map the content to the tracker's real fields (section "Mapping a bug to the tracker's fields" below).
   Severity only if the user asks for it. The priority: `python <qa>/scripts/run_log.py <workspace> defect-rule` says
   whether the project's schema requires one and the accepted values; when it does, the bug stays "waiting for the
   user's priority" in the review until the user gives one (section 9.2), and is never filled in by the assistant.
5. Put the bug in the batch's review: /manage-bugs shows every proposal of the batch at once and the user approves,
   changes or drops each one there (section 9.4). Nothing is recorded before that review, and nothing is asked bug
   by bug in the middle of the work.
6. Once approved, with the excel tracker, the bug is NOT written straight into the workbook: it goes in the
   batch's run log, in the item of the Fail case (technique-excel.md section 4):
   - new bug -> "new_defect": {keys of excel.defects.batch_fields, "severity", "priority"};
     the priority, decided by the user, must be in excel.defects.priorities (run_log.py refuses an approved bug
     without it; a proposal with "proposed": true may wait);
   - existing bug -> "bug": "<id>".
   It reaches the workbook at the next /checkpoint (adapters.excel.apply).
7. With the jira or azure-devops tracker, the same: nothing is sent now, the bug goes in the Fail item of the run
   log (technique-jira-xray.md sections 2.8 and 5.2):
   - new bug -> "new_defect": {the keys of bugs.fields, with "title", "expected", "observed" always, and
     "preconditions", "steps" when known}; the priority, if any, in bugs.priorities;
   - existing bug -> "bug": "<key or id>";
   - a retest that confirms a fix -> "bug_status": "<status of bugs.statuses / state of bugs.states>" next to
     "bug" (resolved_bugs rule, section 9.3).
   It is sent at the next /checkpoint (apply: dry run, approval, --yes). A new_defect on an item whose status
   is not sent (skip or unmapped) is not created; the dry run lists it.
8. Retest of an existing bug (section 9.3): record the BUG's state (still reproduces -> keep and update it;
   resolved -> the resolved_bugs rule; not validated -> keep it, with the reason). A retest changes a CASE only
   when that case's own expected result was checked again in this run: a case whose expected result passed stays
   Pass even if a bug linked to it is maintained, and a case not re-run keeps its status. Never set Fail on a
   case just because it is linked to a maintained bug (a real run turned six Pass cases into Fail that way).
   The bug's update goes in the item of the case where it was retested ("bug" + a note, or "bug_status").
9. On a runner (remote execution, methodology 9.4 and 12.4) nobody approves during the run: a new bug goes as
   "new_defect" with "proposed": true, for the user to approve in the pull request; never applied before that.
10. Writing the item, from the root of qa-assistant. A case that already has its result in the batch:
    python scripts/run_log.py <workspace> attach <batch> <case> [--bug ID | --new-defect draft.json] [--evidence PATH]
    keeps its status, observed text, time ("ts", the execution's), evidence and run block; another link than the
    one recorded needs --replace-link. A failure not yet in the run log:
    python scripts/run_log.py <workspace> add <batch> <case> <status> "<observed>" [--bug ID] [--new-defect draft.json]
    Both refuse a batch already applied and name a new batch for the correction. Never `add --replace` only to link
    a bug: it drops the result's evidence and time.

## Mapping a bug to the tracker's fields (user, 13.11.2026)

Applies to every bug prepared, created or corrected in a tracker (Jira, Azure DevOps or another), in the batch
review (steps 4 and 5), in the run log draft (steps 6 and 7) and in any publication or correction the user asks
for outside /checkpoint. The workspace may name its own fields; this section only says how to use them.

1. **Read the real form first.** Before writing the proposal, and again before creating or correcting a bug, read
   the tracker's form or schema for that bug type (the fields shown in the form, their names, which are editable
   and which apply), not only bugs.fields of project.yaml. A field that exists in the form and is not in
   project.yaml is reported, not ignored. If the form cannot be read, the proposal says so and the mapping stays
   pending.
2. **One piece of content per field.** Map the content to the editable fields that apply: when the tracker has
   separate fields for the observed result, the expected result, the acceptance criteria, the system/environment
   or the build, use them; the reproduction field keeps the preconditions and the numbered steps. Sections inside a
   general field (for example "Frequency" or "Runs" at the end of the reproduction steps) only when no dedicated
   field exists and the project's bug format allows it. The same content is not left in two fields: a summary is
   removed from its old place only after it is confirmed in the field that receives it.
3. **Only what is sustained.** Fill each applicable field with information the run or a source supports
   (environment, platform, device, OS, app build, backend, date and time of the run). A field without evidence or
   without application stays empty and the review says why. Management fields (state, assignee, integrated build,
   deployment status, priority or severity unless the user decides them) are never filled from a QA run; a build
   field is filled only by selecting the real build the tracker offers, never by typing a guess.
4. **Criteria and specs word for word.** Acceptance criteria and spec excerpts are copied as in the source, with
   their line breaks and formatting (rules.md §17); only references and QA notes around them may be reworded.
   Evidence, references and the test account (by role or name, with what is still to confirm) go in the field where
   a developer looks for them, without credentials, codes or personal data (rules.md §6).
5. **Say the mapping in the proposal.** Per bug, the review lists what goes in each field and what stays pending
   (a value to confirm, a field left empty and why, a build not selectable).
5a. **The final preview is what is approved.** When a bug is created through a write guard
   (technique-ado-write-guard.md, «Final preview of a bug's fields»), the QA approves the guard's own preview: each
   field by its label in the form, with its reference name and exact value, the Parent, Assigned To empty, the checks
   and notices (an account «to confirm»), the evidence and the creation comment as operations of their own, and the two
   hashes. The fields come from the project's mapping (`write_guard.bugs` of project.yaml), never from a list written
   in the skill. A field, its mapping or the parent that changes after the approval gives other hashes: a new preview
   and a new approval come before the creation. No e-mail address or credential goes in any field.
6. **Verify after publishing.** After an authorised creation or correction, reopen the bug and check, field by
   field, that each content is in the right field, that the history shows only the intended changes, and that the
   rendered form is readable (paragraphs, numbered lists and sub-lists, no duplicated content, no applicable field
   left empty when the information exists). If the editor lost the formatting, fix it in the field and check again.
   A successful save, or the text being equal, is not enough to call the bug validated.
