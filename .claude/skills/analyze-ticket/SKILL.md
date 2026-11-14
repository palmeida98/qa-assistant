---
name: analyze-ticket
description: "Reads a PBI, story, task, issue or bug in Azure DevOps, Jira or Linear (comments, relations, history, accessible attachments), compares it with the last check and gives a QA analysis: acceptance criteria, decisions, FE/BE/QA dependencies, related bugs. Read only on the tracker. Use for 'what changed in this PBI?', 'analyse ticket 1234', a ticket link, a sprint or 'my tickets'."
argument-hint: "<ticket id|URL ...> | <scope name>"
metadata:
  intent: "Analyse tickets for QA and say what changed since the last check, keeping every source and date."
  inputs: "A link, an id or key, a list, a scope of tickets.scopes (the current sprint, my tickets) or a WIQL/JQL query (a Linear IssueFilter as JSON)."
  config: "tickets: of project.yaml (sources, scopes, limits); the credentials only as names of .env variables."
  tools: "python -m adapters.tickets (a transport that cannot write); or a browser session or an MCP server of the workspace, through a bundle and ingest."
  outputs: "The analysis per ticket: official state, QA situation, acceptance criteria, decisions, dependencies, related bugs, what changed (before -> after, source, date), what was read, only listed, inaccessible or out of scope, and the verification prompt."
  writes: "memory/ (tickets, checks, analyses) and the tickets block of context.md; nothing else of the workspace. Never the tracker."
  approvals: "None to read, nor to hand the analysis to /plan-tests under a request to test. Nothing is ever written to the tracker."
  failures: "Partial read: what was not read is said, nothing is concluded about it and nothing previously known disappears. Nothing read: the references are kept and no change is concluded. No access: stop, nothing analysed from memory."
  resume: "The newest check in memory/checks/ and the block of context.md; a check already analysed is not analysed again unless asked."
  requires-modules: "adapters.tickets"
  requires-config: "tickets"
  requires-tools: "spec"
  requires-docs: "procedures/rules.md, procedures/tickets.md, technique-tickets.md, technique-specs.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints (the one with the framework's dependencies; exit 3 says how to create it, nothing is installed for you). Then:
   python <qa>/scripts/workspace_select.py preflight --skill analyze-ticket [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Follow <qa>/docs/procedures/tickets.md (collect or ingest, the exit codes, the check record, the analysis and its
   verification prompt) and technique-tickets.md for the engine. Run the commands from <qa>.
2. Access: `access: api` reads with the token named in .env; `access: browser`, or an MCP server the workspace's
   CLAUDE.md names, reads through a bundle (technique-tickets.md section 4.3) that `ingest` records. Only read tools;
   an MCP write tool is never called, whatever the server offers.
3. The first check of a ticket is the baseline: say "first analysis, no previous comparison". The next ones show only
   the differences the engine found (before -> after, author and date when the source gives them, date detected).
   "No changes" only when the check concluded it. A partial read never makes a known item disappear and never proves
   that something was removed: say what was not read.
4. For QA, identify and keep with their source and date: the acceptance criteria; decisions in comments (who, when;
   a more recent explicit decision overrides an older requirement, and the contradiction is shown, methodology.md
   section 1.3); the Frontend, Backend and QA tasks and their dependencies; related bugs, open and closed; blockers.
   When the preflight printed a `spec source` line (project.yaml has `specs:`), also read the specification of the
   ticket's flow and cross it with the ticket as technique-specs.md says, with the `spec_refs` and their revision;
   without that line this step is as before.
5. Coverage of the read, in four lists: content read; only listed (a link, a relation, an attachment name, a
   vstfs:/// id); inaccessible (with the reason); out of scope (deeper than the limits, or not asked).
6. Record the analysis (procedures/tickets.md step 8) and report as its step 10 says, short summary first. Nothing
   is committed here: /checkpoint does it. When the user's request goes on to plan or test (a request to test, or
   /test-pbi), hand the analysis to /plan-tests without waiting; a request to analyse only stops here and runs
   nothing.
