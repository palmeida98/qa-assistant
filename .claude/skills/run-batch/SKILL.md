---
name: run-batch
description: Runs a batch of test cases in the browser, classifies the results and records them.
---
1. Start from the plan approved in /plan-batch and from context.md (execution point). If there is no
   approved plan, run /plan-batch first.
2. Confirm the environment against project.yaml (technique-browser.md section 2) and that the credential
   variables of the batch's roles exist in .env (only that they exist; never show values).
3. Use the Playwright MCP (level N1, technique-browser.md section 1). If it is not connected, stop and tell
   the user how to connect it (/mcp), or use another available browser, recording the environment difference.
4. Follow technique-browser.md for authentication (one login per role), locators and verification.
5. Apply the decision rules of methodology.md section 8 (Pass/Fail/N-A/Blocked/Question), with the
   statuses of the project's vocabulary (excel.cases.statuses in project.yaml).
6. Write to the run log after each case (section 7.1): one file per batch,
   <run_log>/batch_<YYYYMMDD>_<nn>.json (run_log from project.yaml; by default runs/;
   never history/), in the format {"results": [{"tc", "status", "actual_result", "ts"}, ...]}.
   actual_result is what was observed, not the expected result copied.
7. Fail:
   - problem already recorded -> add "bug": "<existing id>" to the item;
   - new problem -> do NOT create the defect: gather the evidence (technique-browser.md section 10) and
     propose /log-bug to the user.
8. When in doubt about the expected result, do not classify: mark Question with the concrete question and
   continue.
9. Checkpoint every 15-20 cases (section 7.2) with /checkpoint.
