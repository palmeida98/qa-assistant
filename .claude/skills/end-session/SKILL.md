---
name: end-session
description: "Closes the test session: final checkpoint, bugs, cross-check and summary."
---
1. Confirm that no case was left in Question without being presented to the user; reclassify with the
   answers (section 8.5). The adapter refuses batches with a Question.
2. Make the last checkpoint with /checkpoint (methodology.md section 16.2), including applying the
   batches to the workbook/tracker, with the user's confirmation.
3. Update new, existing and resolved bugs (section 9.3) with /log-bug.
4. Cross-check between cases, results, bugs and totals (section 11). With the excel tracker, the adapter's
   cross-check must give 0 problems (adapters.excel.apply exits with 0).
5. Produce the summary in the format of section 16.3, with the numbers read from the workbook/tracker (not
   from memory).
6. Update the workspace's context.md (section 12.2), with the next steps.
7. With the excel tracker, build the dashboard from the root of qa-assistant:
   python scripts/build_dashboard.py <workspace> --open   (the same as /dashboard)
   and tell the user where dashboard.html is. Do not declare the session done with known inconsistencies
   left unresolved (section 16.2).
