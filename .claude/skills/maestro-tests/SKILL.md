---
name: maestro-tests
description: "Writes and runs deterministic Maestro flows for mobile cases of the library (Android or iOS), fixes the flows that are wrong and records one result per case in the run log. Use for 'automate these mobile cases', 'write the Maestro flows', 'run the mobile regression', 'fix the failing flows'. A conversational run (generated content) is /run-tests; web specs are /playwright-tests."
argument-hint: "<TC-… ids | batch name | feature> [author|run|fix] [android|ios]"
metadata:
  intent: "Turn approved mobile cases with a deterministic expected result into flows (level N2), run them and record each result with its evidence."
  inputs: "Cases of the library or a planned batch (steps and expected result), the platform and target, the mobile: section of project.yaml."
  config: "modules with mobile; mobile: platform, app_id, flows, variables, serial; the evidence policy."
  requires-config: "mobile"
  tools: "Maestro CLI with Java 17+; adb or xcrun; run_mobile.py, device.py, run_log.py."
  outputs: "Flows in the workspace's flows folder, one result per case in the run log, the report and captures of failures as evidence, a summary."
  writes: "The flows folder, runs/ and evidence/ of the selected workspace only; never qa-assistant, never the application's code."
  approvals: "A request to automate or run covers authoring and running flows on the target it names (rules.md section 5). Installing Maestro, a JDK or a build, another target, a workbook or tracker write, a commit or push ask first. Logins, codes, links, permissions and consents stay the user's. A stricter workspace CLAUDE.md wins."
  failures: "No target, Maestro or Java: Blocked, never Pass. A failing flow is classified before it is touched; three fix rounds at most. A tool failure is never a defect."
  resume: "A case with a result and an unchanged flow is not run again; a changed build means a new batch."
  requires-scripts: "scripts/run_mobile.py, scripts/device.py, scripts/run_log.py, scripts/qa_python.py"
  requires-tools: "maestro, adb, xcrun, .env"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/maestro-tests-detail.md, technique-mobile.md, methodology.md"
  next-check: "yes"
---

0. Preflight (read only), once per skill and workspace in a session. A run started by a launcher that already validated
   the workspace uses its paths instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find
   <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (other layouts: procedures/preflight.md). `python` is what
   `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill maestro-tests [<project id or name>] [--workspace <path>]
   Exit 0: go on. Other exits and the `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show it. Documents are in <qa>/docs/, and the rules of
   <qa>/docs/procedures/rules.md apply to every step below. Commands: procedures/maestro-tests-detail.md.
1. Scope: cases and platform. The expected result comes from the case, never from what the app shows now
   (methodology section 5.3). Only a deterministic expected result becomes a flow (technique-mobile.md section 4.1);
   generated text stays in /run-tests; a step the project gives the user (login, code, permission) is a precondition,
   never a flow step. A case not in the library: /plan-tests first.
2. Target and build: one `device.py <ws> check`, the build OBSERVED recorded before the first result, the workspace's
   build rule followed. A device that refuses input injection runs flows only on an emulator (detail: "Targets").
3. Read the screens before writing (detail: "Reading the app"): read only, one named step at a time, the screen read
   after each. While exploring, never touch a control that deletes, sends, confirms, pays, logs out or changes a
   setting: such a step goes in the flow and runs only on data the flow created (`QA-AUTO-<date>-`).
4. Author: one flow per case, `TC-…_<name>.yaml` in mobile.flows; a known start state the project allows; locate by
   id, then text; waits by condition; credentials only as ${MAESTRO_<VAR>}; end with the assertion of the expected
   result. Read existing flows first. A doubt about the expected result is a Question.
5. Run: `run_mobile.py <ws> <flows or ids> --serial <device> --output runs/mobile_<date>_<nn>.json`.
6. Failures, three rounds at most, each named before it is touched: flow bug (fix the flow), environment or tool (one
   retry, then Blocked), application (keep flow and evidence, /manage-bugs; never edit the app). Never loosen an
   assertion or lengthen waits to reach green. A pass only on retry is flaky.
7. Record: mode `flows`, then one run_log.py add per case from the report (observed message, evidence, the build
   OBSERVED). Never create or publish a bug here.
8. End with /checkpoint and a summary: statuses, flaky, Blocked with the reason, Questions, flows changed, commands,
   the run log path.
