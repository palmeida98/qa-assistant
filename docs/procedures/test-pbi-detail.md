# /test-pbi: commands and detail

> Layer 3 — Read by `/test-pbi`. The flow and its decisions stay in the skill; the commands and long rules of steps 2 to 7 are here. `<qa>` and `python` as in the skill's step 0.

## 1. Resuming a record with a plan (step 3)

`python <qa>/scripts/pbi_state.py <workspace> check <pbi> --ticket <id>=<revision> [--ticket <task>=<rev> ...] --build <platform>=<observed> [--environment <key>] --check <the /analyze-ticket check id>`

- Give every ticket and build of the baseline (the PBI and its tasks: a change in a task does not change the PBI's revision).
- Exit 1 lists what changed and the steps to review: present it and ask before re-planning; once the user agrees, the same command with `--reopen` puts those steps back to pending. Exit 3 is "not concluded": say what could not be compared, never "nothing changed".
- Results of an old build stay valid for that build; a new run on another build is a new batch. A build is required to contain the change being tested (a build confirmed with it), not a number higher than some other build.
- When the plan cites `spec_refs` (technique-specs.md), the same command compares them (`--spec-root <export folder>` reads another export than the project's): a scenario whose rule changed or was removed is named and reviewed before the plan is reused, a rule with the same text in a new revision of its file changes nothing, and what could not be verified leaves its scenarios pending review and the check "not concluded" (section 5). A source of another kind than `export` is compared by hand, rule by rule.

## 2. The plan step (step 4)

- The plan review shows the cases a quarantine covers (bug, reason, scope, exit condition) and the QA's choice per case (left out or directed retest). On resuming, `quarantine.py scope --plan` says whether that choice still holds for the build observed now.
- A choice or a confirmation the plan waits on is an open item on the step plan (`open ... --kind approval --step plan --by qa`), and plan is marked done only after the QA gave it and the plan file passes its checks (`scope --plan` exit 0).
- With `policy.approvals.plan: required` (procedures/approvals.md section 5) the plan is approved per batch with `approvals.py review-plan` / `approve-plan` (STOP for the QA's explicit message), and `mark <pbi> plan done` is refused until every platform in scope has a valid plan decision for its batch or its run step was formally skipped (`mark <pbi> run:<p> skipped --note "<why, who decided>"`).
- Once the plan stands: `python <qa>/scripts/pbi_state.py <workspace> baseline <pbi> --ticket <id>=<revision> --build <p>=<observed> [--environment <key>] [--spec-plan <the plan file, when it has spec_refs>]`
- The plan decision is not a run authorisation: with `policy.approvals.run: per-platform` that one is asked and checked separately, and without it the run confirmation the workspace's rules ask for is still asked; `run:<platform>` goes on only when both are valid. A separate approval per platform only when the workspace's rules ask for it. An approval still valid is not asked again on resuming. While `run:<platform>` is listed under `Pending, waiting on an earlier step` (the plan not done, or waiting on the QA), /run-tests is not called: say what it waits on and go on only with the independent work.

## 3. Open items, publications, checkpoints (steps 5 to 7)

Blocked step: `mark <pbi> <step> blocked --note "<reason>"`, then continue with the independent work already authorised (another platform, the analysis, the report of what exists, the local proposals and dry runs). A "no work available in this session" state means everything left waits on someone: say what, never "nothing pending".

Record each decision or operation that waits on someone as soon as it appears, with a stable id (for a bug, its draft id `<batch>/<case>`), what is missing and who unblocks it (procedures/rules.md section 7):

`python <qa>/scripts/pbi_state.py <workspace> open <pbi> <item> --kind <approval|publish|apply|dependency|question> --missing "<what>" --unblocks "<who>" [--by <assistant|qa|team>] [--step <step>]`

On resuming, change that same item, never open another one for it:

`python <qa>/scripts/pbi_state.py <workspace> update <pbi> <item> [--missing "<what>"] [--unblocks "<who>"] [--by <who acts>]`

Close it only with a verifiable confirmation (the dry run and the apply of the workbook, the record of the QA's answer; for an approval or a question, who gave it):

`python <qa>/scripts/pbi_state.py <workspace> close <pbi> <item> --ref "<proof>" [--decided-by "<who>"]`

An open item is not an approval: an approval exists only when the QA gives it explicitly for the set shown, and a resume never replaces the confirmation of a plan, a run or a write, nor the checks of the adapters and the write guard (the dry run's token, the approved hash).

Publication (only on the user's explicit request), once the tracker shows the bug or comment:

`python <qa>/scripts/pbi_state.py <workspace> published <pbi> <batch>/<case> <tracker key> --confirmed-by <proof>` (`<proof>`: the check id that read the bug back, or `tracker/sent/<batch>.json`)

Exit 1 means it was already published (or that key belongs to another draft): update the existing one, never publish it again. A proposal is never recorded here: proposals live in the run log until the review. It closes only the open item whose id is that draft. While the tracker or its write guard is out of reach the item stays open (update it with what was tried), and the independent work goes on.

Intermediate checkpoint, with work still open: `python <qa>/scripts/pbi_state.py <workspace> saved <pbi> --ref context.md [--note "<what is left>"]`. The step checkpoint is marked done only by the checkpoint that closes the flow; the record refuses it (exit 4) while a step is pending or blocked or an item is open. Only the QA's explicit decision closes with them:

`python <qa>/scripts/pbi_state.py <workspace> mark <pbi> checkpoint done --ref context.md --close-with-open --decided-by "<the QA>"`
