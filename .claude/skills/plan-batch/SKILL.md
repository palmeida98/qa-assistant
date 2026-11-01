---
name: plan-batch
description: Groups and orders test cases into batches by area, feature, platform, role and flow, to minimise logins and context switches without reducing coverage (methodology.md section 6). Where a request such as "test this feature" starts when no batch is approved yet.
metadata:
  intent: "Group and order cases into batches, to minimise logins and context switches without losing coverage."
  inputs: "The candidate cases; the time available; a request such as 'test this feature'."
  config: "roles, environments, modules; the tracker's cases."
  tools: "The project's adapter, to read the cases."
  outputs: "Batches with order, dependencies and scope, saved after confirmation."
  writes: "batches/<name>.yaml; tracker/cases.json when refreshed."
  approvals: "The plan is confirmed before anything runs."
  failures: "No test data: the case is Blocked. Planned must equal selected."
  resume: "batches/ and context.md."
  requires-modules: "adapters.tracker"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill plan-batch [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. In the steps below, the current workspace is the one it selected, and the documents named without a folder
   (methodology.md, technique-*.md, heuristics.md) are in <qa>/docs/.
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
10. Once confirmed, save each batch as <workspace>/batches/<name>.yaml -- cases: [<case ID>, ...] in the planned
    order (a name of letters, digits, '.', '_', '-') -- so it can also run remotely: the workspace's CI takes the
    batch's name (python scripts/workspace_ci.py <workspace> check --batch <name>; technique-mobile.md section 11).
