---
name: run-tests
description: "Runs approved test cases or a planned batch on mobile (Android or iOS), web or an API with the tools available, and records results and evidence in the run log. Use for 'test the Account flow on the iPhone', 'run the batch', 'retest TC-…', 'test the API'."
argument-hint: "<batch name | TC-… ids> [android|ios|web|api]"
metadata:
  intent: "Run cases on the platform approved, classify each result and record it with its evidence."
  inputs: "An approved batch (batches/<name>.yaml) or cases of the library named by the user; the platform and the target (device, browser, base URL)."
  config: "The section of each active module (mobile:, api:, accessibility:; environments and roles for the web); run_log; the evidence policy."
  tools: "Per module, the route the preflight prints: device.py and run_mobile.py (mobile), the browser MCP (web), api_request.py (API), audit_a11y.py (accessibility); run_log.py for every result."
  outputs: "One result per case in the run log with the observed text, the evidence path and the run block (environment, build observed); evidence files; a short run summary."
  writes: "runs/ and evidence/ only."
  approvals: "The plan approved for that platform (Android and iOS are separate). Steps the project assigns to the user (logins, links, permissions, airplane mode) stay theirs."
  failures: "No target or a missing tool: those cases Blocked (environment), never Pass, the others go on. A tool failure (capture, driver, timeout): one retry, then Blocked (tool), never a defect on its own. A doubt: Question."
  resume: "The run log of the batch: a case with a result and enough evidence is not run again; a build that changed means a new batch."
  requires-scripts: "scripts/run_log.py, scripts/device.py, scripts/run_mobile.py, scripts/api_request.py, scripts/audit_a11y.py"
  requires-docs: "procedures/rules.md, procedures/run-mobile.md, procedures/run-web.md, procedures/run-api.md, procedures/run-accessibility.md, methodology.md"
  requires-tools: "maestro, adb, emulator, android-driver, mobile, xcrun, ios-wda, build, node, qa-audit, browser, mcp, .env"
  routes: "functional-web: procedures/run-web.md; exploratory: procedures/run-web.md; api: procedures/run-api.md; accessibility: procedures/run-accessibility.md; mobile: procedures/run-mobile.md"
  next-check: "yes"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill run-tests [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. What to run: an approved batch, or cases of the library the user names (a retest included). No approved plan and
   more than a named retest: propose /plan-tests first. A case that is not in the library is refused by run_log.py:
   it goes through /plan-tests.
2. Route: follow the procedure the preflight's `Route:` line gives for the module of these cases: mobile (Android or
   iOS) -> procedures/run-mobile.md; web and web exploratory -> procedures/run-web.md; API -> procedures/run-api.md;
   accessibility -> procedures/run-accessibility.md. A route whose tools are `missing`: say what is missing and how to
   get it; those cases are Blocked (environment) and the rest of the batch goes on. One platform per batch.
3. Before the first case, record how the batch runs: python <qa>/scripts/run_log.py <workspace> mode <batch>
   "<conversational|flows|assisted|manual>: <why>" (never by editing the JSON). Then confirm and record: the
   environment (a test one, never production); the target (device and OS, browser, or base URL; an iPhone not ready:
   show the `blocked by:` and `next:` of device.py check / doctor, never guess "the cable"); the build OBSERVED now
   (mobile: the `Next:` line, device.py check), next to the
   intended one of project.yaml, never copied from it; the session (the user's login when it is theirs); the
   preconditions and data of the batch. Each of these unknown: say so; the cases that depend on it are Blocked.
4. Run each case by its procedure and record it right away:
   python <qa>/scripts/run_log.py <workspace> add <batch> <case> <status> "<observed>" --evidence <path> [--build
   <observed build>] [--environment <key>]
   One item per case (written again, it is replaced); the run block is fixed by the first result and a different
   build is refused: that is a new batch. The observed text is what was seen, never the expected result copied.
5. Classify by procedures/rules.md section 4: Fail only for a failure of the application shown by the evidence; tool
   failure, block and inconclusive evidence stay apart, each with its reason. A capture that failed or a timeout is
   retried once and is never a defect by itself. A send that stopped is never repeated before looking at the app (its
   `outcome:` line says what reached it). Streaming: use the sampler's `evidence:` as written and, when the answer is
   not readable, --burst screenshots judged by content; report the limits; no captured intermediate state is not a
   Fail by itself.
6. A Fail: keep its evidence and hand it to /manage-bugs at the end of the batch (proposals, reviewed together). Never
   create or publish a bug here.
7. Every 15 to 20 cases, and at the end, /checkpoint. End with a short summary: cases run per status, blocked with
   the reason, Questions to answer, Fails for /manage-bugs, and the run log's path.
