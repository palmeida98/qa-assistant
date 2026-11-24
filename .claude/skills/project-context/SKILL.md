---
name: project-context
description: "Selects or sets up a QA workspace and resumes it: the current state, decisions, blockers, the tools really available and the next actions. Use when the user says 'continue', 'resume', 'where were we', 'what is pending', opens a project, or a folder has no project.yaml yet. Running cases is /run-tests."
argument-hint: "[project id or name]"
metadata:
  intent: "Select the workspace (or set up a new one) and present where it stands and what to do next."
  inputs: "Optionally a project id, name or path; otherwise the current folder or the root of workspaces."
  config: "project.yaml, CLAUDE.md and context.md of the workspace; for a new one, the template of docs/project.yaml."
  tools: "workspace_select.py (selection, tools), qa_report.py audit (consistency), device.py check (only when about to run on that device), the ticket sources' check."
  outputs: "A resume summary: project, environments, builds (intended, observed, of the last runs), last activity, decisions, the rules in force (the framework's defaults and the project's own rules that ask for more, with where each is written), blockers, pending items, tools verified or only configured, and the next actions with their skill."
  writes: "Nothing when resuming. Setting up a new workspace writes CLAUDE.md, project.yaml, context.md, .env (names only), .gitignore and .claude/settings.json with the user's answers."
  approvals: "Each value of a new workspace is asked; a remote repository only after the user says yes."
  failures: "Ambiguous project: one short question with the candidates. A record that cannot be read: said, the rest goes on. A tool not checked now is 'configured, not verified'."
  resume: "context.md is the resume summary; archive/context/, runs/ and analysis/ are opened only for the detail a point needs."
  requires-project: "any"
  requires-scripts: "scripts/workspace_select.py, scripts/validate_project.py, scripts/qa_report.py, scripts/workspace_git.py, scripts/qa_python.py"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/onboarding.md, technique-workspaces.md, methodology.md"
  requires-tools: "git, python, .env, skills"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (see preflight.md, Under a launcher) uses those paths
   instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (other layouts: preflight.md, Finding <qa>). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill project-context [<project id or name>] [--workspace <path>]
   Exit 0: show what it printed and go on. Any other exit, and the `Route:`, `Next:`, `Ready:` lines: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop and show what it says. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. A new or incomplete workspace (not-configured or incomplete): follow
   <qa>/docs/procedures/onboarding.md, asking every value, and stop there. Never write in a workspace other than the
   one selected; a project chosen by mistake is corrected before anything is read beyond its project.yaml.
2. Resume summary first: read context.md, then
   CLAUDE.md and project.yaml. Open archive/context/, runs/ or analysis/ only for the detail a point needs,
   saying which file. A plan time ("at 12:50") that has passed is history: never
   turn it into a schedule, and never mark its task done without a result and evidence in a run log.
3. Check the records against the summary (read only):
   python <qa>/scripts/qa_report.py <workspace> audit --since <date of the last checkpoint>
   It lists open Questions, unapplied batches, missing evidence and proposed bugs. A difference is said, with both sources; the records win for results, context.md for decisions.
4. Tools: the preflight's lines are checked now; a device, a session or a tracker connection is "configured, not
   verified" unless checked in this session: python <qa>/scripts/device.py <workspace> check only when the user is
   about to run on that device; the ticket sources with python -m adapters.tickets <workspace>
   sources. An MCP server or a browser session named in CLAUDE.md is verified only by using it.
5. Present, short, in the workspace's language: project and path; environments; builds (intended in
   project.yaml, observed with its date and how, and of the last run per platform); last activity per platform;
   decisions in force (who, when); the rules in force for this project (rules.md sections 5 and 8): what a request
   to test covers on its own, and each rule of the workspace's CLAUDE.md, or of a file it imports, that asks for more, named with its file; one project's rules are never applied to another; blockers; pending items (dates, what unblocks each); open questions;
   tools (verified / only configured); and one to three next actions, each with the skill that does it
   (/analyze-ticket, /plan-tests, /run-tests, /manage-bugs, /qa-report, /checkpoint).
6. Ask nothing else unless the next action needs a user decision.
