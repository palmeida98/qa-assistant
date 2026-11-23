# Running tests: detail of the steps of /run-tests

> Layer 3 — Read by /run-tests for steps 1, 3, 4 and 6. The skill keeps the flow and the decisions; the commands and their conditions are here.

## Quarantine scope (step 1)

With quarantines in the workspace (procedures/quarantine.md), before the first case and on every resume, check the plan against the batch's platform, build and environment:
`python <qa>/scripts/quarantine.py <workspace> scope --platform <p> --build <observed> [--environment <key>] --plan batches/<name>.yaml`
Exit 1 (a covered case without the QA's decision, or a decision whose quarantine no longer holds here): stop that part and go back to /plan-tests for a new confirmation; the cases the problem does not touch go on. `run_log.py add` refuses the same cases, writing nothing.

## Recording how the batch runs (step 3)

Before the first case: `python <qa>/scripts/run_log.py <workspace> mode <batch> "<conversational|flows|assisted|manual>: <why>"` (never by editing the JSON). A batch with its plan in `batches/<name>.yaml` records it next, before any action on the target or any result: `python <qa>/scripts/run_log.py <workspace> plan <batch> batches/<name>.yaml`, then `python <qa>/scripts/run_log.py <workspace> progress <batch>` shows the order, the cases left out and the next case.

This check is the skill's, before the first action on the target: device.py and run_mobile.py do not look at the plan. `run_log.py add` refuses a result written too early (a plan with `approval:`, `excluded:` or `retest:` not recorded, two own plans with decisions, or a run log following another plan), writing nothing. On resuming, progress reuses the plan already recorded; a `PLAN TO REVALIDATE` or `PLAN TO RESOLVE` line goes back to /plan-tests and the QA before the next case (the results already recorded are kept).

### With `policy.approvals.run: per-platform` (procedures/approvals.md)

Once the context is confirmed and before the first action on the target (and again on every resume): `python <qa>/scripts/approvals.py <workspace> check --batch <batch> --build <observed>`. `valid` is reused; anything else stops: ask the QA to authorise that platform, environment, build and plan, then record their explicit message with `approvals.py record-run` (declared in the chat, never as a signature). On resuming, a build observed now or a plan that changed since the decision is `stale`: back to the QA before the next case. device.py and run_mobile.py do not read the register: this check is the skill's, and `run_log.py add` refuses any result without a valid decision.

### With `policy.approvals.plan: required` (procedures/approvals.md section 5)

Before that and before the first action on the target (and on every resume): `python <qa>/scripts/approvals.py <workspace> check-plan --batch <batch> --build <observed>`. Anything but `valid` STOPS and goes back to /plan-tests and the QA with what it shows (a difference, a quarantine decision or a build assumption that no longer holds); `run_log.py plan` and `add` refuse a plan without a valid decision. An approved plan never authorises the run: the run decision stays separate (with `run: per-platform`, the check above; without it, the run confirmation the project's own rules ask for, rules.md section 8, is still asked). The target is acted on only when both are valid.

### Context to confirm and record

The environment (a test one, never production); the target (device and OS, browser, or base URL; an iPhone not ready: show the `blocked by:` and `next:` of device.py check / doctor, never guess "the cable"); the build OBSERVED now (mobile: the `Next:` line, device.py check), next to the intended one of project.yaml, never copied from it; the session (the user's login when it is theirs); the preconditions and data of the batch. Each of these unknown: say so; the cases that depend on it are Blocked.

## Recording a result (step 4)

`python <qa>/scripts/run_log.py <workspace> add <batch> <case> <status> "<observed>" --evidence <path> [--build <observed build>] [--environment <key>]`

- One item per case; a second execution of it: `--replace`. The run block is fixed by the first result and a different build is refused: that is a new batch.
- `ts` is the START of the execution (the clock when the case is run now). A case of many steps adds `--ended-at` (with `--executed-at`).
- An observation made EARLIER (a tester's notes, a run recorded afterwards) is recorded with its time and source: `--executed-at "YYYY-MM-DDTHH:MM" --executed-source "<where>"` (a zone only if the source gives one; a time not known is `--executed-at unknown`, never a guess; a future time is refused). Ask the user for the time when it is not clear.
- The observed text is what was seen, never the expected result copied.
- Evidence by risk (rules.md section 6): what lets the result be verified; more context and sequence for a Fail, a block or an intermittent result; one artefact shared by several cases is named by each, with its own part (a timestamp, an interval, lines) in the observed text.
- Evidence added later: `run_log.py <workspace> attach <batch> <case> --evidence <path>` (it keeps the rest; several files go after one `--evidence` or with the option repeated: every path is kept). A time recorded wrongly: `run_log.py retime <batch> <case> --executed-at … --executed-source …` (only the time changes; a batch already applied is refused). An evidence that shows nothing useful stays on file: `attach --invalid <path> --reason "<why>"`.

## Classifying (step 5)

A send that stopped is never repeated before looking at the app (its `outcome:` line, on Android too, says what reached it; a dump that fails after the tap is `uncertain`, not an error). Streaming: use the sampler's `evidence:` as written and, when the answer is not readable, `--burst` screenshots judged by content; report the limits; no captured intermediate state is not a Fail by itself.

## Quarantine results (step 6)

A case of the plan's `excluded:` is not run and gets no result in the run log (progress shows it as left out); the rest of the batch runs as planned. A case of `retest:` records what was observed with its evidence: the known failure as Fail with `--bug <the quarantine's bug>`; a different failure as a Fail without that link (for the normal review); a pass as Pass, said to the QA at the end: the quarantine stays until the QA decides.
