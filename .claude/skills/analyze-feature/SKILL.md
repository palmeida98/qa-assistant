---
name: analyze-feature
description: Analyses a feature per role/profile and produces the list of behaviours to test, risks and specification gaps (methodology.md section 5.1).
metadata:
  intent: "Analyse one feature per role: the behaviours to test, the risks and the specification gaps."
  inputs: "A feature, requirement or ticket; the project's reference documentation."
  config: "project.yaml (roles, environments); the workspace's CLAUDE.md (reference documentation)."
  tools: "Reading files; nothing else."
  outputs: "Behaviours linked to a role, rule or state, risks and questions, ready for /generate-cases."
  writes: "Nothing required; notes in the workspace only when the user asks."
  approvals: "None; the questions go to the user at the end."
  failures: "Contradicting sources: stop and ask. A gap becomes a question, never an assumption."
  resume: "Repeatable: read context.md and memory/ again."
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (in the demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill analyze-feature [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. In the steps below, the current workspace is the one it selected.
1. Read CLAUDE.md and project.yaml of the current workspace (roles, environments, reference documentation).
   Do not ask again what is already there (methodology.md section 4).
1a. A feature that starts as a ticket: /analyze-tickets gives its state, changes and relations first.
2. Identify the feature's source of truth (requirements, specification, Figma, existing cases)
   and respect the hierarchy of section 1.3. If two sources contradict each other, stop and ask.
3. For each role/profile of the project, say whether it reaches the feature and what it can and cannot do.
4. List the inputs, the states (life cycle), the business rules and the integrations.
5. Assess the risk: probability of failure x impact for the user or for the business.
6. Record the specification gaps as questions or "missing specification" notes.
   Never fill them with assumptions (section 5.1).
7. Produce the list of behaviours to test, each one linked to the role, rule or state it comes from,
   ready for /generate-cases.
8. Present the open questions to the user all at once, at the end.
