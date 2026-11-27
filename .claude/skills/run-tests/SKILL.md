---
name: run-tests
description: "Runs approved test cases or a planned batch on mobile (Android or iOS), web or an API with the tools available, and records results and evidence in the run log. Use for 'test the Account flow on the iPhone', 'run the batch', 'retest TC-…', 'test the API'. Analysing or planning only is /analyze-ticket or /plan-tests."
argument-hint: "<batch name | TC-… ids> [android|ios|web|api]"
metadata:
  intent: "Run cases on the platform approved, classify each result and record it with its evidence."
  inputs: "A planned batch (batches/<name>.yaml) or cases of the library named by the user; the platform and the target (device, browser, base URL)."
  config: "The section of each active module (mobile:, api:, accessibility:; environments and roles for the web); run_log; the evidence policy."
  tools: "Per module, the route the preflight prints: device.py and run_mobile.py (mobile), the browser MCP (web), api_request.py (API), audit_a11y.py (accessibility); run_log.py for every result."
  outputs: "One result per case in the run log with the observed text, the evidence path and the run block (environment, build observed); evidence files; a short run summary."
  writes: "runs/ and evidence/ only."
  approvals: "The user's request to test covers the run on the platforms, environment and targets it names, with no second approval of a plan made for it (rules.md section 5); another platform, environment or target is asked. A workspace whose CLAUDE.md asks for more (an approval of each platform's run) keeps it (rules.md section 8). Steps the project assigns to the user (logins, links, permissions, airplane mode) stay theirs."
  failures: "No target or a missing tool: those cases Blocked (environment), never Pass, the others go on. A tool failure (capture, driver, timeout): one retry, then Blocked (tool), never a defect on its own. A doubt: Question."
  resume: "The run log of the batch: a case with a result and enough evidence is not run again; a build that changed means a new batch."
  requires-scripts: "scripts/run_log.py, scripts/approvals.py, scripts/device.py, scripts/run_mobile.py, scripts/api_request.py, scripts/audit_a11y.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/approvals.md, procedures/quarantine.md, procedures/run-mobile.md, procedures/run-web.md, procedures/run-api.md, procedures/run-accessibility.md, procedures/run-tests-detail.md, methodology.md"
  requires-tools: "maestro, adb, emulator, android-driver, mobile, xcrun, ios-wda, build, node, playwright-cli, qa-audit, browser, mcp, .env"
  routes: "functional-web: procedures/run-web.md; exploratory: procedures/run-web.md; api: procedures/run-api.md; accessibility: procedures/run-accessibility.md; mobile: procedures/run-mobile.md"
  next-check: "yes"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace uses those paths instead; a check that cannot run leaves its cases Blocked, never assumed.
   Otherwise find <qa>: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (other layouts: procedures/preflight.md). `python` is what `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill run-tests [<project id or name>] [--workspace <path>]
   Exit 0: go on. Other exits and the `Route:`, `Next:`, `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show it. Documents are in <qa>/docs/, and the rules of
   <qa>/docs/procedures/rules.md apply to every step below. Commands of steps 1 and 3 to 6: procedures/run-tests-detail.md.
1. What to run: a batch of batches/ (from /plan-tests), or cases the user names (a retest included). More than a named retest and no batch: /plan-tests first; under the same request to test
   the run follows the plan without asking to approve it again, unless the workspace's CLAUDE.md asks for that
   approval (then wait, and say which rule). A case not in the library: /plan-tests. With quarantines: quarantine.py scope first and on every resume.
2. Route: the procedure of the preflight's `Route:` line for the module (run-mobile.md, run-web.md, run-api.md,
   run-accessibility.md). Tools `missing`: Blocked (environment), the rest goes on. One platform per batch.
3. Before the first case: record mode, plan and progress. With `policy.approvals.run: per-platform`,
   approvals.py check; with `policy.approvals.plan: required`, approvals.py check-plan: anything but `valid` stops
   and goes back to the QA or /plan-tests. An approved plan never authorises the run: the run confirmation the
   project's own rules ask for (rules.md section 8) is still asked. Confirm and record the test environment, the target, the build OBSERVED
   now, the session and the data; each unknown: its cases Blocked.
4. Run each case by its procedure; record it at once with run_log.py add (observed text, evidence by risk: rules.md section 6).
5. Classify by procedures/rules.md section 4: Fail only for a failure of the application shown by the evidence; tool
   failure, block and inconclusive evidence stay apart. A capture failure or timeout: one retry, never a defect by itself.
6. A Fail: keep its evidence, hand it to /manage-bugs at the end of the batch; never create or publish a bug here.
7. Every 15 to 20 cases, and at the end, /checkpoint. End with a summary: cases per status and platform, blocked with the reason, Question cases, Fails, platforms not run, the run log's path.
