---
name: checkpoint
description: Saves the run log, updates the workspace's context.md in the format of methodology.md section 12.2 and makes a local commit in the workspace.
---

1. Read the run log's path from project.yaml (run_log). If it is not defined, use runs/.
   Never write to history/: it is immutable (methodology.md section 13).
2. Save to the run log every result of the session not saved yet: case, result, short note,
   linked bug and timestamp (section 7.1). Use the project's format, if defined. With the jira or azure-devops
   tracker the run log also needs the "run" block at the top level, {"environment": "<key of environments>",
   "build": "<version>"}: if it is missing, ask the user for the build (never guess it).
3. Cases in Question stay as Question, with the concrete question (section 8.5).
4. Update context.md with the structure of section 12.2: current state, execution point
   (last batch, last case), work done, decisions (and who made them), open questions,
   blockers, pending work and validation state. Update the date and time.
5. Do not rebuild the progress from scratch: add to what is already recorded (section 12.1).
6. If the workspace is a git repository, make one local commit per checkpoint (section 12.1),
   only with the run log files and context.md (and the evidence of the batch, if any). Then, from the root of
   qa-assistant: python scripts/workspace_git.py <workspace> status
   and tell the user how many commits are not pushed (and any untracked file or file over 50 MB).
6a. Push ONLY when the user says so: python scripts/workspace_git.py <workspace> push
   (never --force, never git push by hand). Exit 1 = refused (a public remote or one whose visibility gh cannot
   confirm, .env tracked, a file over 50 MB): show the reason; --i-confirm-private only after the user checked
   that the remote is private.
7. Do not pass results to the main workbook/tracker without the user's confirmation. With the excel
   tracker and confirmation given, apply the batch through the adapter (technique-excel.md section 1), from
   the root of qa-assistant: python -m adapters.excel.apply <workspace> <run_log>/<batch>.json
   and only continue if it exits with 0. The other codes:
   - 4 = invalid batch (e.g. an unresolved Question, a status outside the vocabulary, a repeated case):
     nothing was written; fix the run log with the user and apply again. Exception: if the batch HAD
     already been applied and was edited afterwards, do not apply it again -- the correction goes in a new
     batch, with another name, with only the cases to correct (and "bug" instead of "new_defect" for
     defects already created);
   - 2 = cross-check with problems: original untouched, result in .REVIEWED.xlsx; show the problems;
   - 3 = saved, but the recalculation failed: review before calling it done;
   - 5 = saved and recalculated, but the workbook's summary does not match the direct count
     (summary): show the cells to the user; the summary's FORMULAS are fixed, never the numbers
     by hand. The totals of the dashboard and of the session summary come from the direct count.
   Applying the same batch again changes nothing (the adapter detects it).
   After exit 0, commit the workbook too (a second local commit, "<batch>: applied"), so a clone on
   another computer has the current workbook (methodology section 12.3); then workspace_git.py status again.
7a. With the jira or azure-devops tracker, the tracker adapter sends the batch (technique-jira-xray.md section 2,
   the common contract; methodology.md section 9.4). From the root of qa-assistant, first the DRY RUN (reads only):
   python -m adapters.tracker <workspace> apply <run_log>/<batch>.json
   - 1 = something to send, not approved yet: show the user the plan as printed (results, new bugs, updated
     bugs, links, and what is NOT sent -- statuses mapped to skip or unmapped, which stay only in the run log).
     Only after an explicit approval run the same command with --yes (it resolves again and sends);
   - 0 = sent and read back, or nothing to send (a batch already sent with the same content makes no request);
   - 4 = invalid (a Question, a status that is not a key of results.statuses / results.outcomes, no "run" block,
     a new_defect without title/expected/observed, a batch already sent with ANOTHER content...): nothing sent;
     fix the run log with the user. A batch already sent is never edited and sent again: the correction goes in
     a new batch, with another name, with only the cases to correct ("bug" with the key from
     tracker/sent/<batch>.json instead of "new_defect" for bugs already created);
   - 2 = conflict with the tracker (a case, bug, test plan or test point not found, an ID in tracker/ids.yaml that
     points elsewhere): nothing written; show the problems; refresh with import-cases or fix project.yaml or the
     run log with the user;
   - 3 = network, authentication, rate limit or server error: with --yes the send may be partial and
     tracker/sent/<batch>.json has exactly what was confirmed. When the cause is gone, run the same --yes
     command again (same batch, same approval): it continues from the record and never creates twice;
   - 5 = sent, but the read back differs: show the differences to the user and record them in context.md; never
     send again to "fix" it.
   After a --yes run that wrote something (0, 3 or 5), commit the workspace's tracker/ (sent/, ids.yaml,
   cases.json) in a second local commit ("<batch>: sent"), so another computer knows what was sent; then
   workspace_git.py status again. Without the tracker's API (e.g. out of reach), the run log stays the source:
   leave the user the list to pass by hand (methodology.md section 4.2).
8. Tell the user the exact point where the run stopped. With the excel tracker, the dashboard can be built:
   python scripts/build_dashboard.py <workspace> (from the root of qa-assistant). With jira or azure-devops the
   progress view is the tracker's own reports (methodology.md section 4.2).
9. A run log from a REMOTE run (runs/batch_<name>_r<run id>.json, from a pull request of the workspace's CI;
   technique-mobile.md section 11) is never applied here: its pull request is the review (proposed bugs approved
   by removing "proposed", each Question answered), and after the merge qa-apply.yml applies it. Pull main first.
