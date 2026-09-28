---
name: checkpoint
description: Saves the run log, updates the workspace's context.md in the format of methodology.md section 12.2 and makes a local commit in the workspace.
---

1. Read the run log's path from project.yaml (run_log). If it is not defined, use runs/.
   Never write to history/: it is immutable (methodology.md section 13).
2. Save to the run log every result of the session not saved yet: case, result, short note,
   linked bug and timestamp (section 7.1). Use the project's format, if defined.
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
7a. With the jira or azure-devops tracker (no adapter; methodology.md section 4.2), the run log is the
   source: show the user what goes to the tracker (approved results and bugs) and only send it with the
   tracker's tool available and explicit approval; otherwise, leave the list for them to pass by hand.
8. Tell the user the exact point where the run stopped. With the excel tracker, the dashboard can be built:
   python scripts/build_dashboard.py <workspace> (from the root of qa-assistant).
