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
  resume: "memory/pbi/<pbi>.json: the steps done (with what proves them), the baseline of the plan, the bugs published, the open items (what waits on whom) and the intermediate checkpoints."
  requires-scripts: "scripts/pbi_state.py, scripts/workspace_select.py, scripts/qa_python.py"
  requires-tools: "spec"
  requires-docs: "procedures/rules.md, procedures/quarantine.md, skills-guide.md, technique-specs.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session while project.yaml and the checkout stay the
   same. A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths
   instead; the steps below still check what the launcher did not (the target, the build observed), and a check
   that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else "root" in ~/.claude/skills/.qa-assistant-source.json, else
   ../../../qa-assistant from the workspace (../../qa-assistant when the project is directly under the root; in the
   demo, ../..). `python` in every command below is the interpreter that `python3 <qa>/scripts/qa_python.py` prints (the one with the framework's dependencies; exit 3 says how to create it, nothing is installed for you). Then:
   python <qa>/scripts/workspace_select.py preflight --skill test-pbi [<project id or name>] [--workspace <path>]
   Exit 0: the configuration is valid; show the project, task, environment and tools it printed, and go on (a
   `Ready: NO` line names what blocks a route now and the action, never a reason to stop the other work). 1: not usable as it is: show why and
   propose /project-context. 2: ask only which project, with its list. 3, or <qa> or the script missing: stop and
   show what it says (the skill and the code do not match; nothing is installed or changed). 4: show the
   configuration it asks for. `Route:` lines name the procedure of each module; `Next:` is the one command to run
   before the work. The current workspace is the one it selected. Documents named without a folder are in
   <qa>/docs/ (procedures/ inside it), and the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Parameters: the PBI (an id or key, or a link of a source configured in tickets:, Linear's .../issue/<KEY>/<slug>
   included; any other host is refused), the QA
   task if any, and the platforms (android, ios, web, api; by default those of the project's modules, said aloud).
   An invalid one: refuse with the expected form `/test-pbi <PBI> [QA task] [android|ios|web|api]` and stop.
2. Open or resume the record, from <qa>:
   python <qa>/scripts/pbi_state.py <workspace> start <pbi> [--task <id>] [--platform <p> ...]
   It shows each step (context, analyze, plan, run:<platform>, bugs, report, checkpoint) with its status and what
   proves it, then the same state block as show and mark: `Next:` is the first step that can run (or an open item
   this session can do), `Waiting on the QA` / `Waiting on the team` list the open items with what is missing and who
   unblocks them, `Blocked:` the steps to go back to once their reason is gone, and `Published:` what already reached
   the tracker. A step `done` is not done again and a publication listed is never repeated. "no work available in this
   session" means that everything left waits on someone: say what, never "nothing pending" (that is said only when
   the record says it).
3. Resuming a record with a plan: run /analyze-ticket again (it shows only the differences) and, for mobile, the
   device check; then
   python <qa>/scripts/pbi_state.py <workspace> check <pbi> --ticket <id>=<revision> [--ticket <task>=<rev> ...]
   --build <platform>=<observed> [--environment <key>] --check <the /analyze-ticket check id>
   Give every ticket and build of the baseline (the PBI and its tasks: a change in a task does not change the PBI's
   revision). Exit 1 lists what changed and the steps to review: present it and ask before re-planning; once the
   user agrees, the same command with --reopen puts those steps back to pending. Exit 3 is "not concluded": say what
   could not be compared, never "nothing changed". Results of an old build stay valid for that build; a new run on
   another build is a new batch. A build is required to contain the change being tested (a build confirmed with it),
   not a number higher than some other build. When the plan cites `spec_refs` (technique-specs.md), the same command
   compares them (`--spec-root <export folder>` reads another export than the project's): a scenario whose rule
   changed or was removed is named and reviewed before the plan is reused, a rule with the same text in a new
   revision of its file changes nothing, and what could not be verified leaves its scenarios pending review and
   the check "not concluded" (section 5). A source of another kind than `export` is compared by hand, rule by rule.
4. Run the steps through their skills, never copying their procedures, and after each one mark it with what proves it:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> <step> done --ref <file, check id or batch>
   - context: /project-context.
   - analyze: /analyze-ticket on the PBI and its tasks (FE, BE, QA), decisions and related bugs.
   - plan: /plan-tests for the PBI and the platforms; its review shows the cases a quarantine covers (bug, reason,
     scope, exit condition) and the QA's choice per case (left out or directed retest; procedures/quarantine.md). On
     resuming, `quarantine.py scope --plan` says whether that choice still holds for the build observed now. The request to test the PBI covers the plan: present it and go
     on, unless it widens the scope or the workspace's CLAUDE.md asks for a confirmation (then STOP for it, and say
     which rule). A choice or a confirmation the plan waits on is an open item on the step plan (`open ... --kind
     approval --step plan --by qa`), and plan is marked done only after the QA gave it and the plan file passes its
     checks (`scope --plan` exit 0). Once the plan stands:
     python <qa>/scripts/pbi_state.py <workspace> baseline <pbi> --ticket <id>=<revision> --build <p>=<observed>
     [--environment <key>] [--spec-plan <the plan file, when it has spec_refs>]
   - run:<platform>: /run-tests, one platform at a time, on the platforms the request named, and only when the state
     block's `Next:` names run:<platform>. While it is listed under `Pending, waiting on an earlier step` (the plan not
     done, or waiting on the QA), /run-tests is not called: say what it waits on and go on only with the independent
     work. A separate approval per platform only when the workspace's rules ask for it. An approval still valid is not
     asked again on resuming.
   - bugs: /manage-bugs, one review for all failures of the PBI (its approval before anything reaches the tracker).
   - report: /qa-report scoped to the PBI's batches and plan.
   - checkpoint: /checkpoint, the one that closes the flow (the intermediate checkpoints of a long run do not mark it).
5. A step that cannot go on: mark <pbi> <step> blocked --note "<reason>" and continue with the independent work
   already authorised (another platform, the analysis, the report of what exists, the local proposals and dry runs).
   Record each decision or operation that waits on someone as soon as it appears, with a stable id (for a bug, its
   draft id `<batch>/<case>`), what is missing and who unblocks it (procedures/rules.md section 7):
   python <qa>/scripts/pbi_state.py <workspace> open <pbi> <item> --kind <approval|publish|apply|dependency|question> --missing "<what>" --unblocks "<who>" [--by <assistant|qa|team>] [--step <step>]
   On resuming, change that same item, never open another one for it:
   python <qa>/scripts/pbi_state.py <workspace> update <pbi> <item> [--missing "<what>"] [--unblocks "<who>"] [--by <who acts>]
   and close it only with a verifiable confirmation (the dry run and the apply of the workbook, the record of the
   QA's answer; for an approval or a question, who gave it):
   python <qa>/scripts/pbi_state.py <workspace> close <pbi> <item> --ref "<proof>" [--decided-by "<who>"]
   An open item is not an approval: an approval exists only when the QA gives it explicitly for the set shown, and a
   resume never replaces the confirmation of a plan, a run or a write, nor the checks of the adapters and the write
   guard (the dry run's token, the approved hash).
6. A bug or a comment published to the tracker (only on the user's explicit request), once the tracker shows it:
   python <qa>/scripts/pbi_state.py <workspace> published <pbi> <batch>/<case> <tracker key> --confirmed-by <proof>
   (<proof>: the check id that read the bug back, or tracker/sent/<batch>.json)
   Exit 1 means it was already published (or that key belongs to another draft): update the existing one, never
   publish it again. A proposal is never recorded here: proposals live in the run log until the review. It closes
   only the open item whose id is that draft; the others stay open. While the tracker or its write guard is out of
   reach the item stays open (update it with what was tried), and the independent work goes on.
7. Saving the progress is not closing the flow. With work still open, /checkpoint saves an intermediate checkpoint:
   python <qa>/scripts/pbi_state.py <workspace> saved <pbi> --ref context.md [--note "<what is left>"]
   The step checkpoint is marked done only by the checkpoint that closes the flow, and the record refuses it (exit 4)
   while a step is pending or blocked or an item is open; only the QA's explicit decision closes with them:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> checkpoint done --ref context.md --close-with-open --decided-by "<the QA>"
   End with the /qa-report summary of the PBI and the record's state block. The autonomy of the tests never commits,
   pushes or syncs anything: those follow their own rules (rules.md section 5).
