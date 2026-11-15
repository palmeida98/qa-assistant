# The QA's decisions: the register and the run gate

> Increment 2, first part (15.11.2026). Code: `scripts/approvals.py`; checked by `run_log.py add`, the CI's
> «Resolve the batch» (`workspace_ci.py batch`) and shown by `pbi_state.py show`. Read by /run-tests (step 3).

## 1. The option

```yaml
policy:
  approvals:
    run: per-platform     # per-platform | with-request (default: a request to test covers the run, rules.md §5)
```

Opt-in per project. Without it nothing changes (rules.md §5 and §8, the project's own CLAUDE.md and context.md apply as
before). With `run: per-platform` the workspace must declare its identity (`workspace: {id, kind}`, technique-workspaces.md
§4.1); `validate_project.py` reports the option without it. Only `run` exists in this version: the plan, the bug review,
the workbook writes and the publication of reports keep their current controls (the plan's `approval:` block, the
write guard's hashes, the Excel token, the explicit request).

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
