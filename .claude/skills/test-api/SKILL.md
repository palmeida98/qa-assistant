---
name: test-api
description: Tests an HTTP API of the current workspace (contract, status codes, authentication per role, negatives, persistence) with redacted evidence, following technique-api.md.
metadata:
  intent: "Test an HTTP API: contract, status codes, authentication per role, negatives, persistence."
  inputs: "Approved API cases; the contract."
  config: "api: (base_url, contract, auth, credentials); the module active."
  tools: "scripts/api_request.py."
  outputs: "Contract checked, results with redacted evidence."
  writes: "runs/ and runs/evidence/; test resources in the test environment, cleaned up or listed."
  approvals: "A new bug through /log-bug; applying through /checkpoint."
  failures: "No response: one retry, then Blocked. A base_url that looks like production: stop."
  resume: "The run log and the resources listed in context.md."
  requires-scripts: "scripts/api_request.py, scripts/run_log.py"
  requires-config: "api"
  requires-tools: ".env"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill test-api [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. In the steps below, the current workspace is the one it selected, and the documents named without a folder
   (methodology.md, technique-*.md, heuristics.md) are in <qa>/docs/.
1. Read technique-api.md (<qa>/docs/) and, in the current workspace, the api: section of project.yaml
   (base_url, contract, auth, credentials per role) and context.md. If the api module is not active or the api:
   section is empty, stop and ask.
2. Confirm that base_url is the TEST environment (technique-api.md section 2). If it looks like production,
   stop.
3. Confirm that .env and .api-session.env are in the workspace's .gitignore (add them if missing) and that
   the credential variables exist in .env (only that they exist; never show values).
4. Cases: use the approved API cases in the workbook/tracker (with jira or azure-devops, tracker/cases.json,
   refreshed with import-cases as in /plan-batch). If there are none, propose them with
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
