---
name: test-pbi
description: "Tests one PBI or requirement end to end by coordinating the other skills (context, ticket analysis, plan, run, bugs, report, checkpoint), with a resume record so it can continue in another session. Use for 'test this PBI', 'test PBI 1234 on Android', 'continue the PBI', a PBI link with a platform. Resuming the whole workspace (no PBI named) is /project-context; one case or a retest of named cases is /run-tests."
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
  requires-scripts: "scripts/pbi_state.py, scripts/approvals.py, scripts/workspace_select.py, scripts/qa_python.py"
  requires-tools: "spec"
  requires-docs: "procedures/rules.md, procedures/preflight.md, procedures/approvals.md, procedures/quarantine.md, procedures/test-pbi-detail.md, skills-guide.md, technique-specs.md, methodology.md"
---

0. Preflight (read only), once per skill and workspace in a session. A run started by a launcher that already validated the workspace
   uses its paths instead; a check that cannot run leaves its cases Blocked, never assumed. Otherwise find <qa>, the root of qa-assistant: $QA_ASSISTANT_ROOT, else
   "root" in ~/.claude/skills/.qa-assistant-source.json, else ../../../qa-assistant from the workspace (../../qa-assistant when the
   project is directly under the root; in the demo, ../..). `python` below is what `python3 <qa>/scripts/qa_python.py` prints. Then:
   python <qa>/scripts/workspace_select.py preflight --skill test-pbi [<project id or name>] [--workspace <path>]
   Exit 0: show it, go on. Other exits and `Route:`/`Next:`/`Ready:`: <qa>/docs/procedures/preflight.md.
   <qa> or the script missing, or exit 3: stop, show it. Bare document names are in <qa>/docs/ (procedures/ inside it); the rules of <qa>/docs/procedures/rules.md apply to every step below.
1. Parameters: the PBI (id, key or link of a source in tickets:; another host is refused), QA task, the platforms (default: the project's modules). Invalid: refuse with `/test-pbi <PBI> [QA task] [android|ios|web|api]` and stop.
2. Open or resume the record:
   python <qa>/scripts/pbi_state.py <workspace> start <pbi> [--task <id>] [--dev-task <id> ...] [--platform <p> ...]
   Follow its state block; a `done` step is not redone. Read each `--dev-task` on the tracker, check it is a child of the PBI, name
   the source of the build containing it. Exit 6: STOP, show it, never rewrite the record.
3. Resuming a record with a plan: /analyze-ticket again, the mobile device check, then `pbi_state.py ... check`. Exit 1: present the
   changes, ask before re-planning; exit 3: "not concluded". detail: <qa>/docs/procedures/test-pbi-detail.md section 1.
4. Run the steps through their skills, marking each:
   python <qa>/scripts/pbi_state.py <workspace> mark <pbi> <step> done --ref <file, check id or batch>
   - context: /project-context. analyze: /analyze-ticket.
   - plan: /plan-tests (quarantine review: procedures/quarantine.md). The request to test the PBI covers the plan: present it and go
     on, unless it widens the scope or the workspace's CLAUDE.md asks for a confirmation (then STOP for it, saying which rule). Mark plan done
     once its pending choice is given (detail: section 2).
   - run:<platform>: /run-tests, one platform at a time, when `Next:` names it. With `policy.approvals.plan: required`, before the first
     device action `approvals.py <workspace> check-plan --batch <batch> --build <observed>`: anything but `valid` STOPS. It is not a run
     authorisation: the run confirmation the workspace's rules ask for is still asked.
   - bugs: /manage-bugs. report: /qa-report. checkpoint: /checkpoint (closes the flow).
5. A step that cannot go on: `mark <pbi> <step> blocked --note "<reason>"`; independent work goes on. Waiting items are open
   items, not approvals (rules.md section 7; detail: section 3).
6. Publish a bug or comment only on the user's explicit request, then `published` (detail: section 3).
7. Saving progress is not closing the flow (rules.md section 7). End with the /qa-report summary and state block. Tests never commit,
   push or sync (rules.md section 5).
