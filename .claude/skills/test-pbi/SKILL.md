---
name: test-pbi
description: "Tests one PBI or requirement end to end by coordinating the other skills (context, ticket analysis, plan, run, bugs, report, checkpoint), with a resume record so it can continue in another session. Use for 'test this PBI', 'test PBI 1234 on Android', 'continue the PBI', a PBI link with a platform."
argument-hint: "<PBI id|URL> [QA task id|URL] [android|ios|web|api ...] [device]"
metadata:
  intent: "Take one PBI from reading to a traceable report, resuming where it stopped and never repeating a finished action."
  inputs: "The PBI (id or a link of the configured tracker), optionally its QA task, the platforms and the device."
  config: "tickets: (to read the PBI), the modules and the tracker of project.yaml; the workspace's CLAUDE.md."
  requires-config: "tickets"
  tools: "pbi_state.py for the record; each step's own tools through its skill."
  outputs: "The state of the flow per step and platform, and at the end the report of /qa-report for the PBI."
  writes: "memory/pbi/<pbi>.json, plus what each skill writes in its own step."
  approvals: "The request to test the PBI covers analysing, planning and running it on the platforms named, with no approval of the same plan or run twice (rules.md section 5); the bug review, each application to a workbook or tracker and each publication keep their own. A workspace whose CLAUDE.md asks for more (the plan confirmed, each platform's run approved) keeps it (rules.md section 8)."
  failures: "A blocked step is marked blocked with the reason; the independent steps go on and the missing ones are listed. A changed requirement, build or environment since the plan is reported before going on."
  resume: "memory/pbi/<pbi>.json: the steps done (with what proves them), the baseline of the plan and the bugs published."
  requires-scripts: "scripts/pbi_state.py, scripts/workspace_select.py"
  requires-docs: "procedures/rules.md, skills-guide.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). Then:
   python <qa>/scripts/workspace_select.py preflight --skill test-pbi [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Parameters: the PBI (an id, or a link of the tracker configured in tickets:; any other host is refused), the QA
   task if any, and the platforms (android, ios, web, api; by default those of the project's modules, said aloud).
   An invalid one: refuse with the expected form `/test-pbi <PBI> [QA task] [android|ios|web|api]` and stop.
2. Open or resume the record, from <qa>:
   python <qa>/scripts/pbi_state.py <workspace> start <pbi> [--task <id>] [--platform <p> ...]
   It shows each step (context, analyze, plan, run:<platform>, bugs, report, checkpoint) with its status and what
   proves it. A step `done` is not done again; `Next:` is the first `pending` one, and the `Blocked:` line lists the
   steps to go back to once their reason is gone.
3. Resuming a record with a plan: run /analyze-ticket again (it shows only the differences) and, for mobile, the
   device check; then
   python <qa>/scripts/pbi_state.py <workspace> check <pbi> --ticket <id>=<revision> [--ticket <task>=<rev> ...]
   --build <platform>=<observed> [--environment <key>] --check <the /analyze-ticket check id>
   Give every ticket and build of the baseline (the PBI and its tasks: a change in a task does not change the PBI's
   revision). Exit 1 lists what changed and the steps to review: present it and ask before re-planning; once the
   user agrees, the same command with --reopen puts those steps back to pending. Exit 3 is "not concluded": say what
   could not be compared, never "nothing changed". Results of an old build stay valid for that build; a new run on
   another build is a new batch. A build is required to contain the change being tested (a build confirmed with it),
   not a number higher than some other build.
4. Run the steps through their skills, never copying their procedures, and after each one mark it with what proves it:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> <step> done --ref <file, check id or batch>
   - context: /project-context.
   - analyze: /analyze-ticket on the PBI and its tasks (FE, BE, QA), decisions and related bugs.
   - plan: /plan-tests for the PBI and the platforms. The request to test the PBI covers the plan: present it and go
     on, unless it widens the scope or the workspace's CLAUDE.md asks for a confirmation (then STOP for it, and say
     which rule). Once the plan stands:
     python <qa>/scripts/pbi_state.py <workspace> baseline <pbi> --ticket <id>=<revision> --build <p>=<observed>
     [--environment <key>]
   - run:<platform>: /run-tests, one platform at a time, on the platforms the request named; a separate approval per
     platform only when the workspace's rules ask for it. An approval still valid is not asked again on resuming.
   - bugs: /manage-bugs, one review for all failures of the PBI (its approval before anything reaches the tracker).
   - report: /qa-report scoped to the PBI's batches and plan.
   - checkpoint: /checkpoint, the one that closes the flow (the intermediate checkpoints of a long run do not mark it).
5. A step that cannot go on: mark <pbi> <step> blocked --note "<reason>" and continue with the steps that do not
   depend on it (another platform, the analysis, the report of what exists). List what is missing and who unblocks it.
6. A bug or a comment published to the tracker (only on the user's explicit request), once the tracker shows it:
   python <qa>/scripts/pbi_state.py <workspace> published <pbi> <batch>/<case> <tracker key> --confirmed-by <proof>
   (<proof>: the check id that read the bug back, or tracker/sent/<batch>.json)
   Exit 1 means it was already published (or that key belongs to another draft): update the existing one, never
   publish it again. A proposal is never recorded here: proposals live in the run log until the review.
7. End with the /qa-report summary of the PBI and the record's `Next:` line. The autonomy of the tests never commits,
   pushes or syncs anything: those follow their own rules (rules.md section 5).
