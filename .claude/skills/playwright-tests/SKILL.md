---
name: playwright-tests
description: "Writes and runs deterministic Playwright specs (.spec.ts) in the workspace's e2e/ folder for cases of the library, runs them with `npx playwright test`, fixes the specs that are wrong and records one result per case in the run log. Use for 'automate these cases with Playwright', 'write the regression suite', 'run the e2e suite', 'turn the passing cases into specs', 'fix the failing specs'. A manual or exploratory browser run is /run-tests; planning cases is /plan-tests."
argument-hint: "<TC-… ids | batch name | feature> [author|run|fix]"
metadata:
  intent: "Turn approved web cases into deterministic specs (level N2), run them natively and record each case's result with its evidence."
  inputs: "Cases of the library or a planned batch (their steps and expected result), the test environment and its roles from project.yaml."
  config: "environments and roles (functional-web); the evidence policy."
  tools: "node and npx with @playwright/test pinned in <workspace>/e2e/; run_log.py for every result."
  outputs: "Spec files and config in <workspace>/e2e/, a run log with one result per case, traces and captures of failures as evidence, a short summary."
  writes: "e2e/, runs/ and evidence/ of the selected workspace only; never qa-assistant, never the application's code."
  approvals: "A request to automate or run covers authoring and running specs on the environment it names (rules.md section 5). Installing packages or browsers (a download), another environment, a write to a workbook or tracker, a commit or push each ask first. A workspace whose CLAUDE.md asks for more keeps it."
  failures: "No node, package or browser: those cases Blocked (tool), never Pass. A failing spec is classified (test bug, environment, application) before it is touched; three fix rounds at most. A tool failure is never a defect."
  resume: "e2e/ and the run log: a case with a result and a spec that has not changed is not run again; a build that changed means a new batch."
  requires-scripts: "scripts/run_log.py, scripts/qa_python.py"
  requires-tools: "node, .env"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/playwright-tests-detail.md, technique-browser.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session. A run started by a launcher that already validated
   the workspace uses its paths instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find
   <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (other layouts: procedures/preflight.md). `python` is what
   `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill playwright-tests [<project id or name>] [--workspace <path>]
   Exit 0: go on. Other exits and the `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show it. Documents are in <qa>/docs/, and the rules of
   <qa>/docs/procedures/rules.md apply to every step below. Commands: procedures/playwright-tests-detail.md.
1. Scope: the cases (ids, a batch, or a feature's cases) and the test environment. Read each case's steps and expected
   result from the library; the expected result comes from the case, never from what the application does now
   (methodology section 5.3). A case not in the library: /plan-tests first. Candidates by technique-browser.md section 13;
   a case never run by an agent can still be authored when the user asks, but it is not called a regression until it passes.
2. Prepare e2e/ in the workspace (not in qa-assistant, not in the application's repository): config, pinned
   @playwright/test, credentials and base URL only from environment variables named in .env, never written in a spec.
   Confirm the URL is a test environment of project.yaml (technique-browser.md section 2) before any run. Installing is
   a download: ask first.
3. Author: one spec file per feature, one test per case, the case id first in the title (`TC-… · title`), locators by
   role, label or text before CSS, independent tests. Existing
   specs are read first and extended. A doubt about the expected result is a Question, not a guess.
4. Run `npx playwright test` from e2e/ (list and JUnit reporters, traces on failure); do not drive the browser by hand.
5. Failures, at most three rounds, each one named before it is touched: test bug (selector, wait, wrong data: fix the
   spec), environment or tool (retry once, then Blocked), application bug (do not change the spec to make it pass and
   never edit the application: keep the evidence, hand to /manage-bugs). Never loosen an assertion, add a skip or raise a
   timeout to hide a real difference. A test that passed only on retry is reported as flaky.
6. Record: mode `flows`, then one run_log.py add per case from the JUnit result (observed text, trace or capture as
   evidence, the run block with the environment and the build OBSERVED). The title's id must exist in the library.
   Fail: never create or publish a bug here.
7. End with /checkpoint and a summary: cases per status, flaky, Blocked with the reason, Questions, specs written or
   changed, the commands run, the run log's path.
