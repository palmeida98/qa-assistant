# The QA's decisions: the register and the run gate

> Increment 2, first part (15.11.2026). Code: `scripts/approvals.py`; checked by `run_log.py add`, the CI's
> «Resolve the batch» (`workspace_ci.py batch`) and shown by `pbi_state.py show`. Read by /run-tests (step 3).

## 1. The option

```yaml
policy:
  approvals:
    run: per-platform     # per-platform | with-request (default: a request to test covers the run, rules.md §5)
```

Opt-in per project; `plan: required | with-request` (increment 2B, section 5) is independent of `run`. Without them
nothing changes (rules.md §5 and §8, the project's own CLAUDE.md and context.md apply as
before). With `run: per-platform` the workspace must declare its identity (`workspace: {id, kind}`, technique-workspaces.md
§4.1); `validate_project.py` reports the option without it. `run` and `plan` exist in this version: the bug review, the workbook
writes and the publication of reports keep their current controls (the write guard's hashes, the Excel token, the
explicit request).

## 2. The register

`memory/approvals.jsonl`: one JSON object per line, appended under the workspace's lock, never rewritten. A run
decision holds: `id` (stable, `AP-0001`…), `gate: run`, `workspace` (the identity), `pbi`, `batch`, `platform`,
`environment`, `build` (the build the QA authorised, as observed), `plan` and `plan_sha256` (the exact content
approved), `declared_by`, `confirmation: declared-in-chat`, `message_ref` (the QA's message: session and time),
`message_ref_verified: false`, the optional `quote`, `limitation`, `recorded_by: assistant` and `recorded_at`.

- **Declared, not signed.** The name in `declared_by` and the record itself are declared information written by the
  assistant after the QA's explicit message; the tool cannot open the conversation, so the reference is recorded as not
  verified. Never present a record as proof of authorship.
- **Never from a successful command.** No command writes a record of a confirmation given at a permission prompt; a
  write that succeeded is not an approval.
- A revocation is a new line (`revokes: AP-…`, reason, who declared it): the decision stays for consultation.

- **What a decision is for.** `record-run` refuses a plan that is not the batch's own (the one its run log follows,
  else `batches/<batch>.yaml`), and a PBI, platform or environment that the plan itself declares differently. The same
  decision recorded twice (same workspace, PBI, batch, platform, environment, build and plan hash, not revoked) is one
  line: the second call returns the existing id.

## 3. The check and the resume

`approvals.py <ws> check --batch <b>` reads only. The batch's context is its run log's run block, else the values given,
else the plan's `run:`; the plan is the one recorded in the run log, hashed now.

| Answer | Meaning | What the skill does |
|---|---|---|
| `valid` | a decision of this workspace matches platform, environment, build and the plan's hash, not revoked | reuse it on resuming: ask nothing again |
| `missing` | no decision for this batch | ask the QA to authorise this context, then `record-run` |
| `stale` | decisions exist, none matches; the field that differs is named (build, environment, platform, plan, workspace) | show what changed, ask again, `record-run` (the old one stays) |
| `revoked` | the matching decision was revoked | ask again |

A register that cannot be read (a line that is not JSON) is an error: the operation subject to the policy is refused
and the line is named; nothing is repaired automatically.

### Where each value comes from

| Value | /run-tests and `run_log.py add` | CI («Resolve the batch», then «The build is the one authorised») |
|---|---|---|
| workspace | the path given; its identity from project.yaml | the checkout of the workspace; its identity |
| PBI | the plan's `pbi:` when it declares one (else not compared) | the same, from `batches/<batch>.yaml` |
| batch | the batch named; a remote run log `<batch>_r<digits>` also carries its base batch's decision (only that form) | `<batch>` then `<batch>_r<run id>` |
| platform | the run block, else `report.platforms`, else the plan's `run:` | `android` (the runner is an Android emulator) |
| environment | the run block, else `--environment`, else the plan's `run:` | `test` (or the first environment) |
| build | **observed**: the run block (the first result's `--build`, from device.py check), else `--build`; the plan's build is only the intended one and is refused as a source | «Resolve the batch»: `mobile.build.version` (declared); then the **APK's own manifest** (versionName and versionCode) after the fetch, before the emulator: a difference or no valid decision for it stops the job |
| plan hash | SHA-256 of the plan recorded in the run log | SHA-256 of `batches/<batch>.yaml` |

## 4. Where it is enforced

- **`run_log.py add`**, before every result, when the option is active: the plan must be recorded in the run log
  (`run_log.py plan`); a check other than `valid` refuses, writing nothing (the results already there stay).
- **The CI**: «Resolve the batch» (`workspace_ci.py batch`) runs the same check with the run's platform (android), the
  declared build and the environment, before the run log, the emulator, the flows and the agent; an ad hoc list of
  cases is refused under the option (no approved plan). The workflow input `build` can fetch another release than the
  declared one, so «The build is the one authorised» (`workspace_ci.py build-check`) reads the fetched APK's manifest
  and, under the option, stops the job when it differs from the validated build or has no valid decision: the emulator
  and the flows never start. Without the option a difference is a warning only. An installed workflow is a copy of
  the template: `workspace_ci.py check` reports it out of date until it is installed again.
- **`pbi_state.py show`** lists, per platform, the run decision and its state, without changing any step.
- **Not enforced:** `device.py` and `run_mobile.py` act on the device without looking at the register. Started by hand,
  they are not stopped: the protection is that /run-tests checks before the first action and that no result can be
  recorded. In the CI, the flows start only after «Resolve the batch».

## 5. The plan decision (increment 2B)

```yaml
policy:
  approvals:
    plan: required        # required | with-request (default: as before; a plan's `approval:` block is information)
```

Independent of `run`: with both, both must pass (the plan first, then the run). `plan: required` needs the workspace's
identity. Without the option nothing changes: old batches, plans with an `approval:` block and ad hoc lists work as
before, and `check-plan` answers as information only.

**What it approves.** The COVERAGE of one batch's plan: the cases in their order, `excluded:`, `retest:`,
`assumptions:`, `notes:`, `run.platform`, `run.environment`, `pbi` and any other operational key. It never authorises a
run on a device or a build: that is the run decision (sections 1 to 4), asked separately, and the record says so
(`authorises_run: false`).

**The canonical hash (`plan-canonical-v1`).** The YAML's data without the old `approval:` block and without `run.build`
(the intended build), `run.platform` in lower case, `excluded` and `retest` in a stable order, `cases` in its order,
serialised as sorted JSON and hashed with SHA-256. Formatting and comments are not part of it, so:
- an instruction that changes how a case is tested goes in `notes:` (or another key), never only in a comment;
- a build the coverage depends on is an assumption, in the approved content:
  `assumptions: [{build: "1.0.0 (39)", why: "the build with the FE change"}]`; it is checked against the build
  OBSERVED (`check-plan --build`, the run log's run block, the CI's APK) and a different build makes the plan `stale`;
- the decision is never written into the plan: recording it changes nothing that was hashed.

**The flow.**
1. /plan-tests writes the draft `analysis/<name>.yaml` and runs
   `approvals.py <ws> review-plan --batch <batch> --draft analysis/<name>.yaml --pbi <pbi> [--build <observed>]`: it
   writes the readable review `analysis/<name>.review.md` (the content, the hash, the difference from the coverage
   approved before for the batch, the problems). Exit 1: a problem keeps it from being approved (cases repeated or not
   in the library nor proposed in cases/, no platform or environment, another PBI, a quarantine choice missing).
2. The skill presents the draft and its review and STOPS: an approval exists only in the QA's explicit message.
3. `approvals.py <ws> approve-plan --pbi <pbi> --batch <batch> --draft analysis/<name>.yaml --sha <the hash shown>
   --declared-by <QA> --message-ref <session · time> [--quote <words>] [--review analysis/<name>.review.md]
   [--ticket <id>=<rev> ...]`: refused when the draft no longer has the hash shown (it changed after the review), when
   the review given is not this version's, when a problem is left, or when the batch already has results with another
   plan (a new version is a new batch). Then the draft's bytes become the batch's plan (`batches/<batch>.yaml`, or its
   own plan's existing path) and the decision is appended. Run again after an interruption, it finishes what is
   missing and never records the same decision twice (`already recorded`). Everything is decided under the
   workspace's lock from one reading of the draft: the bytes whose hash was checked are the bytes written, the plan is
   read back with that hash before the decision is appended, and two approvals at once are serialised (the plan on
   disk is always the one of the last decision). A plan already followed by results (the batch's run log, or a run
   log such as a remote `<batch>_r<id>` that follows the same file) is never replaced by another version: the results
   stay, and the new version is a new batch (the refusal names one).
4. A plan saved before 2B with an `approval:` block is `legacy`: `review-plan --draft batches/<batch>.yaml`, the QA's
   confirmation, `approve-plan --draft batches/<batch>.yaml` (the file is not changed).

**The record** (`gate: plan`): `id`, `workspace`, `pbi`, `batch`, `platform`, `environment`, `plan`, `plan_sha256`,
`hash_method`, `coverage` (the approved content, to show the difference later), `intended_build` (information only),
`presented` (`draft` and its bytes' SHA-256, `review` and its SHA-256: the Markdown is kept as a reference; the approval
applies to the YAML's operational content), `basis.tickets` (the revisions given, for traceability: a change in them is
handled by `pbi_state.py check`, which reopens the steps), `authorises_run: false`, and the same declared fields as a
run decision (`declared_by`, `confirmation: declared-in-chat`, `message_ref`, `message_ref_verified: false`, `quote`,
`limitation`, `recorded_by`, `recorded_at`). `revoke <id>` works for both gates. Run decisions keep the 2A format.

**`check-plan --batch <b> [--build <observed>]`** (reads only):

| Answer | Meaning | What the skill does |
|---|---|---|
| `valid` | a decision of this workspace for this batch, plan path and coverage hash, not revoked, whose quarantine decisions and build assumptions hold in the effective context | reuse it: ask nothing again about the plan |
| `missing` | no decision; when a draft in analysis/ has the same hash, `next` names it and the `approve-plan` that finishes an interrupted approval | present the review and ask |
| `legacy` | an old `approval:` block, no decision | present it, ask for the explicit confirmation |
| `stale` | the coverage changed (each difference listed), another workspace's decision, or the same coverage whose quarantine decision or build assumption no longer holds here | back to the QA's review of the plan with the difference; a new version is approved again |
| `revoked` | the decision was revoked | ask again |

The effective context is the run log's run block, else the values given, else the plan's `run:` (the intended build
only for the quarantine check when nothing was observed). A build observed that is not an assumption of the coverage
does not reopen the plan: it is the run decision's business.

**Where it is enforced** (each refuses writing nothing, exit 1): `run_log.py plan` (only an approved plan is recorded),
`run_log.py add` (before the run decision), the CI's «Resolve the batch» (`workspace_ci.py batch`: before the run log,
the emulator, the flows and the agent; an ad hoc list is refused) and «The build is the one authorised» (the fetched
APK's build against the build assumptions and the quarantine decisions), and `pbi_state.py mark <pbi> plan done` (each
platform in scope with a valid plan decision for the PBI's batch, or its `run:<p>` formally `skipped` with a note
saying why and who decided). `pbi_state.py show`
lists the plan decision per platform. A register that cannot be read refuses (exit 2 for `check-plan`).
