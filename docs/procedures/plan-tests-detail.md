# /plan-tests: details

> Layer 3 — Details of `/plan-tests` that only that skill uses. The skill keeps the flow and the decisions.

## 1. Specification source (step 3)

With a `spec source` line in the preflight, each confirmed requirement carries its `spec_refs` (technique-specs.md section 4). The plan keeps them in a ```json spec_refs block written with `python <qa>/scripts/spec_refs.py <workspace> cite <path>#<rule id>`, and the matrix of step 4 has a column with the rule and its revision. A point where the specification and the tracker diverge stays a question with both texts (section 3). A case that depends on a background job names the evidence that the job ran (section 6).

## 2. Widening the investigation (step 4)

A relevant sign (a related bug, a fragile area, a contradiction) widens the investigation inside the scope, with the reason written next to the cases it added. A widening that needs another platform, environment or target, or changes the scope materially, is asked (rules.md section 5). A platform outside the project's modules is not planned.

## 3. Quarantines (step 6)

When the workspace has them, for the batch's platform, build and environment, `python <qa>/scripts/quarantine.py <workspace> scope --platform <p> --build <observed> [--environment <key>]` lists the cases a QA-approved quarantine covers. Show each in the plan's review with its bug and link, the reason, the scope and the exit condition. The QA chooses per case: left out (the plan's `excluded:`) or a directed retest (`retest:`, and in `cases:`). Then check the plan with the same command and `--plan batches/<name>.yaml` (exit 1: fix it). Nothing else changes: cases not covered, another platform, build or environment are planned as usual.

On resuming, a decision already in the plan is reused while the same quarantine is active and holds for the batch's context (the check passes). When the build, platform, environment or quarantine changed, the check fails: show the new scope and ask the QA again.

## 4. Plan approval (step 7, `policy.approvals.plan: required`)

Write the draft `analysis/<name>.yaml` (operational instructions in `notes:`, a build the coverage depends on in `assumptions:`) and run `python <qa>/scripts/approvals.py <workspace> review-plan --batch <batch> --draft analysis/<name>.yaml --pbi <pbi>`. Present the draft, its review and the hash (on a later version, the difference it shows) and STOP. Only after the QA's explicit message: `approvals.py approve-plan` with the hash shown, the review and the message reference (declared in the chat, never a signature).

On a resume, run `approvals.py check-plan` first: `valid` is reused without asking; `missing` with a draft named finishes an interrupted approval only if the QA's message exists; `legacy`, `stale` and `revoked` go back to the QA with what it shows.
