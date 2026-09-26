---
name: test-api
description: Tests an HTTP API of the current workspace (contract, status codes, authentication per role, negatives, persistence) with redacted evidence, following technique-api.md.
---
1. Read technique-api.md (<qa>/docs/) and, in the current workspace, the api: section of project.yaml
   (base_url, contract, auth, credentials per role) and context.md. <qa> is the root of qa-assistant:
   usually ../../../qa-assistant; in the demo, ../.. . If the api module is not active or the api:
   section is empty, stop and ask.
2. Confirm that base_url is the TEST environment (technique-api.md section 2). If it looks like production,
   stop.
3. Confirm that .env and .api-session.env are in the workspace's .gitignore (add them if missing) and that
   the credential variables exist in .env (only that they exist; never show values).
4. Cases: use the approved API cases in the workbook/tracker. If there are none, propose them with
   /generate-cases from the contract (sections 3 to 7: contract, negatives, role matrix,
   idempotency, persistence). The expected result comes from the contract; if the contract is silent,
   it only goes in with another objective source written in the case (e.g. RFC 9110 for 4xx vs 5xx) or
   stays as a question.
5. Order with /plan-batch: health check first, then the flows that create resources, those that use
   them, and the clean-ups at the end. One token per role and per batch.
6. Every request through the script, from the root of qa-assistant, with the ABSOLUTE path of the workspace:
   python scripts/api_request.py <workspace> <METHOD> <path> [--json '...'] [-H "Name: {{VAR}}"]
       [--expected <code>] [--save field=NAME] [--save-secret token=TOKEN]
   - path relative to api.base_url, WITHOUT a leading slash (booking/1): in Windows Git Bash an
     argument /booking reaches the script converted into C:/Program Files/Git/booking;
   - credentials always as {{VARIABLE}}; tokens always with --save-secret, used afterwards as
     {{TOKEN}}. Never write a credential or token value in a command, note or file;
   - exit 3 (no response) = Blocked by environment: one retry and, if it fails, Blocked;
   - persistence confirmed with a GET when the case claims it.
7. Classify by methodology.md section 8 and technique-api.md section 8 (5xx to invalid input
   = Fail). Write to the run log (format of methodology section 7.1): actual_result = status code +
   summary of the body. Resources created (type + id) are recorded too.
8. New Fail -> gather the script's evidence (already redacted) and propose /log-bug; never create the
   defect without approval.
9. At the end: delete the test resources you created (if the project allows it) or list them in context.md;
   confirm that no secret was left in the run log, in the bugs or in context.md; continue to
   /checkpoint.
10. Never run load tests, fuzzing, brute force nor bursts of requests (technique-api.md section 11).
