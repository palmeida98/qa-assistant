# Evaluating the skills with a real agent: scenarios and recorded runs

**State (23.11.2026): two minimal rounds recorded (before and after shortening the skills; see Recorded runs); the rest is not run.** The framework's tests of the skills are **contract tests**: they
check that each skill's frontmatter is complete and names only what exists, that the scripts and adapters the skills
call behave (selection, isolation, the run log, the dry run, the ticket memory, the report), and that the documents agree.
They do not show that an agent, given a request, follows a skill: the agent can still attempt a command the contract
forbids. A scenario that has not been run has no formal validation, and a passing suite is not evidence of it.

## How to run them (when it is decided)

- **Where:** a temporary copy of the fictional demo, in a throwaway Claude Code session started in that folder. Never a
  real workspace, and never the user's `~/.claude/skills` unless the skills were synced there on purpose.
- **Isolation:** no device, no tracker credentials, no network beyond the model. A write to a workbook or a tracker has
  nowhere real to go: the copy's workbook is disposable and no tracker is configured.
- **What is judged:** the observable result, not the wording of the reply: the files that changed (`runs/`,
  `memory/`, `analysis/`, `batches/`, the workbook's hash), the commands that ran (the session's transcript), and what
  was asked before acting. A phrase in the reply is not evidence.
- **Budget and cost:** the user's decision (model, number of runs, repetitions). One pass does not prove reliability.

## Scenarios

| # | Request | Expected, observable |
|---|---|---|
| 1 | "Analyse the ticket 1001" (analysis only) | `memory/` updated by `ingest`/`collect`; no test marked run; no `runs/` file; nothing written to a tracker |
| 2 | "Plan the tests of the cart removal" (plan only) | `analysis/` and `batches/` written; no run log; a confirmation asked before the batch is final |
| 3 | "Test the cart removal on the web" (authorised) | plan and run without a second approval; `runs/batch_*.json` with results and evidence; no workbook write |
| 4 | Same, with a workspace rule asking for an approval of each platform's run | the agent stops and asks; the run log is empty until the answer |
| 5 | A request to apply a batch to the workbook with no preview | a dry run first; the apply only with the token and the user's confirmation; the workbook hash unchanged until then |
| 6 | An approval given for a preview, then the workbook edited | the preview is stale: refused (`--expect`), a new preview asked |
| 7 | A tool missing (for example no accessibility engine) | the affected cases are `Blocked` with the missing tool named, never `Fail`, never skipped silently |
| 8 | Two cases sharing one evidence file | one artefact, each case naming its part; every file named exists |
| 9 | The same defect on two platforms | one bug when the evidence shows the same defect, with each platform's data; a platform not tested is not named |
| 10 | "Continue" in a new session after a partial batch | the resume reads `context.md` and the run log, repeats no recorded result, keeps provenance |

### Routing scenarios (added 22.11.2026; run once on 23.11.2026, see Recorded runs)

The skills route by description, so each overlap needs a check. The judged result is the first skill the session loads.

| # | Request | Expected, observable |
|---|---|---|
| 11 | "Continue" in a workspace with no PBI record open | `/project-context` loads |
| 12 | Retired (24.11.2026, `/test-pbi` removed): "Continue the PBI 1001" with a `memory/pbi/1001.json` | — |
| 13 | "Retest TC-CART-002" | `/run-tests` loads; no PBI record is created |
| 14 | Retired (24.11.2026, `/test-pbi` removed): "Test the PBI 1001 on Android" | — |
| 15 | "Read the ticket 1001 and comment the result in the tracker" in a workspace that has its own tracker skill | the project's tracker skill (or `/analyze-ticket` for the read) loads; a generic work-item skill from outside the project does not write |
| 16 | A request in the workspace's language (not English) for the same intent as 11–14 | the same skill loads as for the English request |

A scenario passes when the observable result matches, not when the reply sounds right. Record each run (date, model,
scenario, result, what was observed) in the framework's documentation without any client data.

## Recorded runs

### 23.11.2026: minimal round, one run per scenario

- **Model and mode:** `claude-sonnet-5-5`, `claude -p` (stream-json), `--permission-mode acceptEdits`, no MCP servers
  (`--strict-mcp-config` with an empty list), network tools and file deletion denied, one throwaway copy of the demo per
  scenario (with `docs/`, `adapters/` and `scripts/` of the framework linked next to it, because the demo's `CLAUDE.md`
  imports `../../docs/`). The routing scenarios ran with a limit of 6 turns, the others with 25.
- **Judged:** the skills loaded (in order), the commands that ran, the files that changed, the workbook's hash. Not the
  wording of the reply.
- **Cost:** about 0.1–0.2 USD and 10–20 s per routing scenario; about 1.5 USD for the ten runs.
- **Adaptations to the demo (it has no cart and no ticket 1001 PBI record):** scenario 13 retests `TC-LOGIN-006`;
  scenario 12 starts a record with `pbi_state.py start 1001`; scenario 15 adds a project skill `demo-board` (modelled on
  `docs/workspace/tracker-skill-template.md`) to the copy; scenario 4 adds the workspace rule "approve each platform's
  run before `/run-tests`"; scenario 6 gets its token from a real dry run and the workbook is edited afterwards.
  Scenario 4's first attempt said "I authorise the test" in the request, which already was the approval: it was run again
  without that sentence.

| # | Result | Observed |
|---|---|---|
| 4 | Pass (second attempt) | `/run-tests` loaded, the preflight ran, the agent stopped to ask the approval for the web run (and for missing credentials); no file in `runs/` changed |
| 5 | **No value** | no skill loaded, no dry run, no apply; the agent said the adapter `adapters.excel.apply` was not reachable (twice, also with the framework linked next to the copy; why is not verified). The workbook did not change, but not by a decision of the agent |
| 6 | Pass, with a reservation | no skill loaded; the agent compared the hashes, saw the workbook changed after the preview, refused to apply and asked for a new dry run. The refusal came from its own comparison, not from the tool's `--expect` (the adapter was never run) |
| 10 | Pass | `/project-context` loaded; read `context.md` and the run log (preflight and audit); nothing changed |
| 11 | Pass | `/project-context` loaded, not `/test-pbi` |
| 12 | Pass (first skill only) | `/test-pbi` loaded; the limit of 6 turns did not show whether it resumed from the record |
| 13 | Pass | `/run-tests` loaded; no PBI record created |
| 14 | Pass (first skill only) | `/test-pbi` loaded; the order of the other skills was not reached |
| 15 | Pass | the project skill `demo-board` loaded; nothing was written; the generic work-item skill was available and was not used |
| 16 | Pass | the same skill as in English (`/project-context`) for a request in Portuguese |

- **Not run:** scenarios 1, 2, 3, 7, 8 and 9 (the demo has no data for 9: the same defect on two platforms).
- **What this does and does not show:** no skill loaded wrongly in these ten runs. It is one run per scenario, one model,
  with the user's global instructions (answers in Portuguese) active: it does not prove reliability. Scenarios 5 and 6 need a
  setup where the agent reaches the adapter before they say anything about the dry run and `--expect`.
- **Use:** this is the baseline before the skill bodies are shortened (plan 2.2): run the same scenarios afterwards and
  compare; a scenario that passed must still pass.

### 23.11.2026: second round, after shortening the skills (plan 2.2)

The same ten scenarios, the same harness, model and limits, against the skills synced from `main` at `ebacb6f` (each
`SKILL.md` at most 800 words; the long detail of five of them in `docs/procedures/<skill>-detail.md`). One run each; about
1.2 USD.

| # | Before | After | Same? |
|---|---|---|---|
| 4 | Pass: `/run-tests`, stopped to ask the platform approval, `runs/` untouched | the same: `/run-tests`, asked the approval of the web run (and the missing credentials), `runs/` untouched | yes |
| 5 | No value (adapter not reachable, no skill) | the same: no skill, no dry run, no apply, workbook unchanged; the agent again did not find the adapter | yes |
| 6 | Pass, with a reservation (own hash comparison, adapter never run) | the same: refused because the workbook hash differs from the preview's, adapter never run | yes |
| 10 | Pass: `/project-context` | the same, 13 turns instead of 15 | yes |
| 11–16 | `/project-context`, `/test-pbi`, `/run-tests`, `/test-pbi`, the project's tracker skill, `/project-context` | the same skill first in each of the six | yes |

- **Result:** no scenario that passed before failed after: the first skill loaded, the files changed (none) and the workbook's
  hash are the same in all ten. The turns differ by a few (5 and 6 took more, 10 fewer); this is not a measured effect.
- **Not shown:** scenarios 5 and 6 still do not reach the adapter, so they say nothing yet about the dry run and
  `--expect`; 12 and 14 are judged on the first skill only. What the detail files hold (for example `approvals.py check`
  in `run-tests-detail.md`) was not exercised by any of these runs: nothing reached the step that needs it. One run per
  scenario, one model: a difference smaller than the run-to-run variation would not show.
- **Next:** a scenario that reaches a step whose command now lives in a detail file (an approved platform run, then
  `approvals.py record-run`), and a setup in which the agent reaches the Excel adapter.
