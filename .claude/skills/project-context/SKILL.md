---
name: project-context
description: "Selects or sets up a QA workspace and resumes it: the current state, decisions, blockers, the tools really available and the next actions. Use when the user says 'continue', 'resume', 'where were we', 'what is pending', opens a project, or a folder has no project.yaml yet."
argument-hint: "[project id or name]"
metadata:
  intent: "Select the workspace (or set up a new one) and present where it stands and what to do next."
  inputs: "Optionally a project id, name or path; otherwise the current folder or the root of workspaces."
  config: "project.yaml, CLAUDE.md and context.md of the workspace; for a new one, the template of docs/project.yaml."
  tools: "workspace_select.py (selection, tools), qa_report.py audit (consistency), device.py check (only when about to run on that device), the ticket sources' check."
  outputs: "A resume summary: project, environments, builds (intended, observed, of the last runs), last activity, decisions, blockers, pending items, tools verified or only configured, and the next actions with their skill."
  writes: "Nothing when resuming. Setting up a new workspace writes CLAUDE.md, project.yaml, context.md, .env (names only), .gitignore and .claude/settings.json with the user's answers."
  approvals: "Each value of a new workspace is asked; a remote repository only after the user says yes."
  failures: "Ambiguous project: one short question with the candidates. A record that cannot be read: said, the rest goes on. A tool not checked now is 'configured, not verified'."
  resume: "context.md is the resume summary; archive/context/, runs/ and analysis/ are opened only for the detail a point needs."
  requires-project: "any"
  requires-scripts: "scripts/workspace_select.py, scripts/validate_project.py, scripts/qa_report.py, scripts/workspace_git.py"
  requires-docs: "procedures/rules.md, procedures/onboarding.md, technique-workspaces.md, methodology.md"
  requires-tools: "git, python, .env, skills"
---

0. Preflight, unless the prompt of an unattended run already names the workspace and qa-assistant. Find <qa>, the
   root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill project-context [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. A new or incomplete workspace (the preflight says not-configured or incomplete): follow
   <qa>/docs/procedures/onboarding.md, asking every value, and stop there. Never write in a workspace other than the
   one selected; a project chosen by mistake is corrected before anything is read beyond its project.yaml.
2. Resume summary first: read context.md (current state, execution point, pending work, blockers, decisions), then
   CLAUDE.md and project.yaml. Open archive/context/, runs/ notes or analysis/ only for a point that needs its detail,
   and say which file you opened. A date or time written as a plan ("at 12:50") that has passed is history: never
   turn it into a schedule, and never mark its task done without a result and evidence in a run log.
3. Check the records against the summary (read only):
   python <qa>/scripts/qa_report.py <workspace> audit --since <date of the last checkpoint>
   It lists open Questions, batches not applied, evidence named but missing and bugs proposed. A difference between
   context.md and the records is said, with both sources; the records win for results, context.md for decisions.
4. Tools: the preflight's lines were checked now. A device, a session or a tracker connection is "configured, not
   verified" unless checked in this session: python <qa>/scripts/device.py <workspace> check only when the user is
   about to run on that device (it reads only); the ticket sources with python -m adapters.tickets <workspace>
   sources (from <qa>; no network). An MCP server or a browser session named in CLAUDE.md is verified only by using it.
5. Present, short, in the workspace's language: project and workspace path; environments; builds (intended in
   project.yaml, observed with its date and how, and of the last run per platform); last activity per platform;
   decisions in force (who, when); blockers; pending items with their dates and what unblocks each; open questions;
   tools (verified now / only configured); and one to three next actions, each with the skill that does it
   (/analyze-ticket, /plan-tests, /run-tests, /manage-bugs, /qa-report, /checkpoint, /test-pbi).
6. Ask nothing else unless the next action needs a decision that only the user can take.
