---
name: analyze-feature
description: Analyses a feature per role/profile and produces the list of behaviours to test, risks and specification gaps (methodology.md section 5.1).
metadata:
  intent: "Analyse one feature per role: the behaviours to test, the risks and the specification gaps."
  inputs: "A feature, requirement or ticket; the project's reference documentation."
  config: "project.yaml (roles, environments); the workspace's CLAUDE.md (reference documentation)."
  tools: "Reading files; nothing else."
  outputs: "Behaviours linked to a role, rule or state, risks and questions, ready for /generate-cases."
  writes: "analysis/<feature>_<YYYYMMDD>.md in the workspace; a new version in a new file. Never the tracker or the workbook."
  approvals: "None; the questions go to the user at the end."
  failures: "Contradicting sources: stop and ask. A gap becomes a question, never an assumption."
  resume: "The newest file of analysis/ for the feature, then context.md and memory/."
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill analyze-feature [<project id or name>] [--workspace <path>]
   Exit 0: show the project, task, environment and tools it printed, and go on. 1: not usable as it is: show why and
   propose /project-onboarding. 2: ask which project, with its list. 3, or <qa> or the script missing: stop and show
   what it says: the skill and the code do not match, and nothing is installed or changed. 4: show the configuration
   it asks for. A `Route:` line names the skill that fits this project: say so and continue with that one. A `Next:`
   line is the one command to run before the work. In the steps below, the current workspace is the one it selected, and the documents named without a folder
   (methodology.md, technique-*.md, heuristics.md) are in <qa>/docs/.
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
9. Save the analysis in the workspace (methodology.md section 5.1): analysis/<feature>_<YYYYMMDD>.md, a slug of the
   feature, with _2, _3 when that name exists: an older file is never changed. In this format:
   # Analysis: <feature>
   Date: <YYYY-MM-DD>
   Status: produced | reviewed by the user on <YYYY-MM-DD>
   Sources: each document, ticket or link read, with its version or date; "none" when there was none
   ## Confirmed by a source: each rule with its source
   ## Proposed: the behaviours and risks this analysis infers, not requirements
   ## Open questions
   Status says reviewed only when the user said so. Reviewing an analysis does not turn what is proposed into a
   requirement: only a source, or an explicit decision of the user written as such with its date, does.
