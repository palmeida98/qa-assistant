# Bugs in quarantine (13.11.2026)

Read by `/plan-tests`, `/test-pbi`, `/run-tests`, `/manage-bugs`, `/qa-report` and `/checkpoint` when the workspace has a
register of quarantines. A workspace without one works exactly as before.

## 1. What it is, and what it is not

A **quarantine** is a decision of the QA, recorded in the workspace: *these cases, in this context, fail because of this
known bug; until the exit condition is met they are either left out of the batches or run only as a directed retest.* It
exists so a known failure does not cost a full run every time, without hiding it.

- Its **scope** is a combination, never a whole bug: the bug (its tracker id), the cases it affects, and the platforms,
  builds and environments where it holds (each list empty = any). A quarantine of iOS never touches Android.
- It is **never automatic.** A bug in «Blocked», an intermittent failure, a bug already known or a case that failed before
  does not enter quarantine by itself. The assistant may **propose** one (or its end); only the QA's explicit decision
  activates, lifts or rejects it, and the record keeps who decided, when and on what (`--decided-by`, `--ref`).
- It changes **neither the bug nor the approved coverage on its own**: the tracker and the workbook are not written because
  of it, and leaving a case out of a batch is shown in the plan's review for the QA to confirm.

## 2. The source: the workspace's register

`<workspace>/memory/quarantine.json`, written only by `scripts/quarantine.py` (under the workspace's lock):

| Field | Meaning |
|---|---|
| `id` | stable id, `Q-<n>` or any id the QA gives |
| `bug`, `tracker_bug`, `bug_url` | the existing bug as the run logs link it (`run_log.py --bug`: the workbook's defect id in an Excel project, e.g. `BUG-7`; the tracker's key otherwise), its id in the ticket source when different (e.g. `9770`), and its link (derived from the only Azure DevOps or Jira source of `tickets:` when not given) |
| `cases` | the cases of the library it covers |
| `platforms`, `builds`, `environments` | the context where it holds (empty = any; builds compared as builds) |
| `reason` | why: the failure as observed, with its evidence or run log |
| `exit` | the condition for a retest or for the end (e.g. «a build that contains the fix: directed retest, then the QA decides») |
| `status` | `proposed` → `active` → `lifted`; or `proposed` → `rejected` |
| `decided_by`, `decided_at`, `decision_ref` | the QA's decision |
| `history` | every change, append only |

A project whose tracker already has a field or a tag for this may name it in its own rules; the register stays the source the
skills read, and the tag is never written by them.

    python <qa>/scripts/quarantine.py <workspace> show
    python <qa>/scripts/quarantine.py <workspace> propose <id> --bug <id> --case <case> ... [--platform P ...]
        [--build B ...] [--environment E ...] --reason "<why>" --exit "<condition>" [--bug-url URL] [--tracker-bug ID]
    python <qa>/scripts/quarantine.py <workspace> decide <id> activate|lift|reject --decided-by "<the QA>" --ref "<where>"
    python <qa>/scripts/quarantine.py <workspace> scope --platform P --build B [--environment E] [--plan batches/<n>.yaml]
    python <qa>/scripts/quarantine.py <workspace> report [--batch <name> ...]

`propose` writes only a proposal (the assistant may); `decide` is run only after the QA said so in the session.

## 3. Planning (`/plan-tests`, `/test-pbi`)

`scope` lists, for the platform, build and environment of the batch, the active quarantines and the cases they cover: the
bug and its link, the reason, the scope and the exit condition. The plan's review shows them, and the QA chooses per case:

- **left out**: the case goes in the plan file's `excluded:` list, `{case, quarantine}`; it is not in `cases:`;
- **directed retest**: the case stays in `cases:` and goes in `retest:`, `{case, quarantine}`.

`scope --plan` checks the plan file: a covered case in `cases:` without `retest:`, or an `excluded:` case not covered by an
active quarantine of that context, is a problem to fix before running (exit 1). Every other case is planned as usual: the
quarantine never reduces the coverage of what it does not cover.

## 4. Running (`/run-tests`)

- A case left out is **not executed** and **no result is written** for it in the run log: a pending status applied to the
  workbook would erase the result the case already has. The report shows it as «<pending status of the vocabulary> —
  <label> <id>» with the reason and the bug's link (the label: `quarantine.excluded_label` of project.yaml, e.g.
  «excluído por quarentena»; by default «excluded by quarantine»); it never counts as Pass, nor as run.
- **Enforced by the entry point:** `run_log.py add` refuses (nothing written) a result for a case the plan left out
  while its quarantine holds, for a case an active quarantine of the batch's context covers without the QA's decision
  in the plan (`retest:`), and for a case of an `excluded:` list whose quarantine no longer holds there (the plan is out
  of date: another build, platform, environment, or the quarantine was lifted). `run_log.py progress` names a plan to
  revalidate before the next case.
- **On resuming,** the decision of the plan is reused as long as the same quarantine is active and holds for the
  batch's platform, build and environment (`scope --plan` passes); otherwise the plan is revalidated and shown again to
  the QA when the coverage changes. A new build is a new batch, planned and checked again.
- A case run as a retest records what was observed, with its evidence, as any other case:
  - the **known failure**: Fail with `--bug <the quarantine's bug>`; the result stays Fail;
  - a **different failure** (other steps, screen or result): Fail without that link, investigated by `/manage-bugs` as
    any new failure; a quarantine never explains a behaviour it does not describe;
  - a **pass**: Pass; the quarantine stays active, and the report shows it to the QA as «retest passed: decide whether
    the quarantine ends».

## 5. Bugs, report and checkpoint

- `/manage-bugs`: a Fail linked to the quarantine's bug is an occurrence of it (listed in the review as such, no new bug);
  a Fail in a covered case without that link goes through the normal criteria and the batch review.
- `/qa-report` (`quarantine.py report`) shows apart: the cases **left out by quarantine** (with the reason and the bug's
  link, outside the counts of run cases), the **known failures observed** and the **retests run** (passed: decision
  pending; failed differently: normal review). Totals stay those of `qa_report.py`.
- `/checkpoint` (under `/test-pbi`): each decision that waits on the QA — a proposed quarantine, a retest that passed, a
  proposed end — is an open item of the PBI's record (`pbi_state.py open … --kind approval --by qa`), closed only with the
  `decide` that the QA confirmed.
