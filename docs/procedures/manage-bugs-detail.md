# /manage-bugs: detail
> Read by /manage-bugs. Policy on bugs is procedures/bugs.md; this file holds the longer criteria of the skill's steps.

## Step 2: investigating a failure
The steps that reproduce it, the requirement or decision it breaks (with its source), whether a more recent decision
changed the expectation, and whether it is a copy change, a new request or a defect already open on the other
platform (the workspace's CLAUDE.md says what is not a bug there). In a case a quarantine covers
(procedures/quarantine.md): a Fail linked to its bug is an occurrence of that bug (listed in the review, no new
proposal); a Fail without that link is investigated by these same criteria and shown to the QA: a quarantine never
explains a behaviour it does not describe.

## Step 3: the search
Say how many bugs were found and how many were read.

## Step 5: priority and severity
Run `python <qa>/scripts/run_log.py <workspace> defect-rule`. When the project's schema REQUIRES a priority (Excel with
excel.defects.priorities), each bug in the review shows the accepted values and a line "priority: waiting for your
decision"; the bug is not ready to apply until the user gives one of them (a suggestion with its reason only if the
user asks, as a proposal to approve, never applied on its own). Severity only when the user asks. The review
shows per bug the project's order of fields (CLAUDE.md), steps, expected -> observed, environment, device, build and
the evidence path (with the bug id once it has one).

## Step 7: creating a bug through the write guard
(technique-ado-write-guard.md, «Final preview of a bug's fields».) Build the fields from the project's mapping
(write_guard.bugs of project.yaml: extra_fields only for its keys), run ado_guard_preview_bug with the evidence to
attach and the creation comment, and show the QA its preview_text as it is: each field by its label in the Board with
its exact value, the Parent, Assigned To empty, the checks and notices (a QA account «to confirm»), the evidence (a
reviewed video only when the captures cannot show the occurrence) and the comment as separate operations, and
payload_sha256 and patch_sha256. A preview with ready false is not proposed for approval. Create only with the hashes
the QA approved; a field, its mapping or the parent that changed since gives other hashes: preview again and ask
again. Attachments and the comment follow with their own approvals.
