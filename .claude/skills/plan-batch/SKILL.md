---
name: plan-batch
description: Groups and orders test cases into batches by area, feature, platform, role and flow, to minimise logins and context switches without reducing coverage (methodology.md section 6).
---

1. Read from the current workspace project.yaml (roles, environments, active modules) and context.md
   (execution point, known blockers, scope decisions).
1a. With the jira or azure-devops tracker, the cases come from <workspace>/tracker/cases.json (technique-jira-xray.md
   section 6). If it does not exist or its imported_at is older than this session, refresh it first, from the
   root of qa-assistant (read only): python -m adapters.tracker <workspace> import-cases
   and show the user the flags it prints. Use the tracker's IDs (the "id" of each case: the key or work item id
   with id_source key/id) and last_status; never cases from memory.
2. Select the candidate cases (e.g. not run, retest of fixed bugs). Exclude those out of scope by a
   recorded decision and say why.
3. Group by area + feature + platform + role + flow (section 6).
4. Within each group:
   - the cases that share a login, environment or application state run one after the other;
   - first the flows that create state other cases reuse;
   - dependencies between cases made explicit (technique-browser.md section 8).
5. One login per role and per batch (technique-browser.md section 3).
6. If the time does not cover everything, order the groups by risk.
7. Check that each batch's test data exists. Cases without data are Blocked, with the reason.
8. The optimisation never reduces coverage nor changes a case's logic: confirm that the number of cases
   planned equals the number of cases selected.
9. Present the plan (batches, order, role, cases, dependencies) and wait for confirmation before /run-batch.
