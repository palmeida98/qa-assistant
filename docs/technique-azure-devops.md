# Azure DevOps technique

> **Layer 2 — Technique for the Azure DevOps (Test Plans + Boards) tracker adapter**
> Version 1.1 · 2026-09-28 · Plan, task 10.0 (specification first, methodology §15); decisions of 28.09.2026 recorded in 10.1 (§0, §3, §4.1); built in 10.1 (common base) and 10.3 (this adapter).
>
> Project agnostic: no organisation, project, area path, field or state of a real Azure DevOps lives here. The examples use the fictional **Demo Shop** (`https://dev.azure.com/demo-shop`, project `Demo Shop`). Subordinate to `methodology.md`.
>
> The **common contract** of the tracker adapters (command line, exit codes, dry run and `--yes`, the record of what was sent, markers, input validation, HTTP client, redaction, run log fields, golden) is `technique-jira-xray.md` §2 and §10, and is not repeated here: this document describes only what is specific to Azure DevOps.

---

## 0. Scope

- **Azure DevOps Services** (cloud): test cases are `Test Case` work items in a **test plan** and its suites; a case runs as a **test point** (test case × configuration in a suite); results go to a **test run** with one **outcome** per point; bugs are `Bug` work items linked to the test case and to the test result.
- API (version 1): **Azure DevOps Services REST API**, `api-version=7.1` (fixed in the configuration, raised only through the validation of methodology §15), authentication with a **personal access token** (PAT, basic authentication with an empty user) with the scopes *Work Items (read & write)* and *Test Management (read & write)*. Areas used: `wit` (work items, WIQL, links), `testplan` (plans, suites, test cases, test points) and `test` (runs and results).
- The endpoint names are those of the public documentation at the time of writing (09.2026) and are **not yet confirmed**: 10.3 confirms each one against the current public documentation (and the pilot, 10.5), and fixes this document first if one changed.
- **Process template** (decision of 28.09.2026): the project must use the **Agile** or **Scrum** process (or a process inherited from one of them). `Basic` has no `Bug` work item type, so `check` refuses it with a clear message ("the project uses the Basic process, which has no Bug work item type: the adapter needs Agile or Scrum") and exit 4; any other process (e.g. CMMI) is reported by `check` as not covered in version 1.
- The four directions, as in Jira: send results (§4), bugs (§5), import cases (§6), export new cases (§7).
- **Out of scope in version 1** (future, only when a real project needs them): Azure DevOps Server (on premises, other API versions and authentication); OAuth / Microsoft Entra ID service principals (the PAT is enough for one tester); shared steps and parameters written by the adapter (imported expanded to text and flagged, never created); results per step (only the point's outcome, with the actual result as the comment); creating test plans, suites or configurations (existing ones are read); the dashboard (`/dashboard` stays Excel only, as in `technique-jira-xray.md` §9); the processes `Basic` (it has no `Bug` work item type: `check` refuses it) and CMMI.

---

## 1. Configuration in `project.yaml`

The `azure_devops:` section (the key uses `_`, as every key of `project.yaml`; the value of `tracker:` stays `azure-devops`), read only when `tracker: "azure-devops"`. Only names of `.env` variables.

```yaml
tracker: "azure-devops"
azure_devops:
  organization_url: "https://dev.azure.com/demo-shop"   # (req.)
  project: "Demo Shop"                                  # (req.)
  pat_var: "DS_ADO_PAT"                                 # (req.) personal access token (variable name)
  api_version: "7.1"                                    # (opt.)

  cases:
    work_item_type: "Test Case"        # (opt.)
    plan_id: 12                        # (req.) the test plan the cases and results belong to
    suite_ids: []                      # (opt.) default: every suite of the plan
    new_cases_suite_id: 13             # (req. for export-cases) static suite where new cases go
    area_path: "Demo Shop\\Checkout"   # (opt.) area of new cases
    id_source: "id"                    # id | field | tag  (§3)
    id_field: ""                       # with field: e.g. Custom.QaId
    id_tag_prefix: ""                  # with tag: e.g. "qa-id-"
    fields:                            # model field -> work item field (§1.1)
      area: "System.AreaPath"
      feature: "Custom.Feature"
      preconditions: "System.Description"
      priority: "Microsoft.VSTS.Common.Priority"
      role: "tag:role-"
      platform: "tag:platform-"
      technique: "tag:technique-"
      origin: "tag:origin-"

  results:
    outcomes:                          # (req.) the project's vocabulary -> outcome (§4.1)
      "Pass": "Passed"
      "Fail": "Failed"
      "Blocked": "Blocked"
      "N/A": "NotApplicable"
      "Not run": "skip"
    configurations: {}                 # (opt.) platform -> Test Configuration name, when a case has several points (§3)
    run_name: "{batch_id} · {environment} · {build}"   # (opt.)
    attach_evidence: false             # (opt.) as in technique-jira-xray.md §8

  bugs:
    work_item_type: "Bug"              # (opt.)
    area_path: "Demo Shop\\Checkout"   # (opt.) default: the test case's area
    iteration_path: ""                 # (opt.)
    fields:                            # model field -> work item field; the run log's new_defect uses these keys
      title: "System.Title"
      description: "Microsoft.VSTS.TCM.ReproSteps"
      area: "System.AreaPath"
      feature: "Custom.Feature"
      sub_area: "tag:subarea-"
      platform: "tag:platform-"
      role: "tag:role-"
      environment: "Microsoft.VSTS.TCM.SystemInfo"
      severity: "Microsoft.VSTS.Common.Severity"
      priority: "Microsoft.VSTS.Common.Priority"
    defaults: {}
    closed_states: ["Closed", "Removed"]   # (opt.) what does not count as open in the duplicate search (§5.1)
    states: []                         # (opt.) states bug_status may ask for (resolved_bugs rule, methodology §9.3)
    priorities: []                     # (opt.) priorities a new_defect may have (technique-jira-xray.md §2.6)
```

### 1.1 Field mapping

- A plain value is a work item field's reference name. The adapter reads the fields of the work item type once per run (`GET …/_apis/wit/workitemtypes/<type>/fields`) and formats the value: plain text, HTML (`ReproSteps`, `Description`, `SystemInfo`: the text is escaped and written as paragraphs and lists), integer (priority, a picklist of numbers), picklist (severity, e.g. `2 - High`: the value must be one of the allowed values, otherwise exit 4), tree path (area, iteration: must exist). A field that does not exist on the type is a configuration error (exit 4), found by `check`.
- `tag:<prefix>` stores the value as a tag (`tag:platform-` + `web` → `platform-web`); tags are `;`-separated in `System.Tags` and the adapter adds without removing the tags already there.
- Test case steps live in `Microsoft.VSTS.TCM.Steps` (XML with an action and an expected result per step, both HTML): the adapter writes and reads that format; the model's `data` of a step goes at the end of the action, after a line `Data:`, and is read back from there.

---

## 2. Common contract

`technique-jira-xray.md` §2 applies as written, with these specific points:

- **Markers** are tags: `qa-batch-<batch_id>` on everything created, `qa-case-<case id>` on bugs; a test run carries the batch id in its name and in its comment.
- **Retries**: Azure DevOps throttles by usage and answers `429` (or delays) with `Retry-After` and `X-RateLimit-*` headers; the client honours them as in §2.7 of the Jira technique. Work item updates are `PATCH` with a JSON Patch body (`application/json-patch+json`) and a `test` operation on `/rev` when changing an existing work item, so a concurrent edit is a conflict (`412`, exit 2) instead of a silent overwrite.
- **Record** (`tracker/sent/<batch_id>.json`) keeps the test run id, each result id and its outcome, and the ids of the bugs created.

---

## 3. Case IDs: internal ID ↔ work item id

| `id_source` | The internal ID is | Example `tc` in the run log |
|---|---|---|
| `id` (default) | the work item id | `4711` |
| `field` | the value of a field (`id_field`) | `TC-SHOP-001` in `Custom.QaId` |
| `tag` | a tag with a prefix (`id_tag_prefix`) | `TC-SHOP-001` from `qa-id-TC-SHOP-001` |

The mapping lives in Azure DevOps; `<workspace>/tracker/ids.yaml` is the cache, with the test points:

```yaml
tracker: azure-devops
cases:
  TC-SHOP-001: {id: 4711, points: {"Windows 11 - Chrome": 9001, "Android 15": 9002}}
```

The default is `id` (decision of 28.09.2026): the work item id is stable and unique; `field` and `tag` stay for projects with their own IDs.

**Platform → Test Configuration** (decision of 28.09.2026): the model's platform (the run log item's `platform`, or the case's) is translated to a Test Configuration through the table `results.configurations` of `project.yaml`, e.g.:

```yaml
    configurations:
      web: "Windows 11 - Chrome"
      android: "Android 15"
```

`apply` confirms each case (a WIQL query or `GET _apis/wit/workitemsbatch`) and each point (`GET _apis/testplan/Plans/<plan>/Suites/<suite>/TestPoint`). A case with several points needs the item's platform in `results.configurations`; a case in two suites of the plan with the same configuration is a conflict (exit 2), as is a case not in the plan.

---

## 4. Sending results (`apply`, at `/checkpoint`)

### 4.1 Outcome mapping

`results.outcomes` maps every status of the project's vocabulary to an outcome, or to `skip`. Outcomes are a closed list, so `validate_project.py` checks them without the network: `Passed`, `Failed`, `Blocked`, `NotApplicable`, `NotExecuted`, `Inconclusive`, `Paused`, `InProgress`, `None` (the others the API knows, e.g. `Timeout`, `Aborted`, `Error`, `Warning`, are for automated runs and refused).

Defaults proposed at the onboarding (the user confirms):

| Project status | Outcome | Why |
|---|---|---|
| `Pass` | `Passed` | |
| `Fail` | `Failed` | |
| `Blocked` | `Blocked` | |
| `N/A` | `NotApplicable` | |
| `Not run` | `skip` | a run is not the place to reset a point; the point keeps its current outcome |
| `Question` | never mapped: refused (exit 4) | methodology §8.5 |

A status mapped to `skip` or left empty (unmapped) is **not sent**, as in `technique-jira-xray.md` §4.1: it stays in the run log and is listed in the dry run and the record, not an error; a status that is not a key of `results.outcomes` is refused (exit 4).

### 4.2 Flow

1. Validate the batch (Jira technique §2.6) and read the record.
2. Resolve every case → test point (§3), every `bug` → existing work item, the marker search.
3. Without `--yes`: the plan, exit 1.
4. With `--yes`, recording after each write:
   1. new bugs (`POST _apis/wit/workitems/$Bug`, §5.2);
   2. **one test run per batch**: `POST _apis/test/runs` (`name` from `run_name`, `plan`, `pointIds` of the items sent, `automated: false`, `buildNumber`/comment with `run.build` and `run.environment`, the dates from the items' `ts`) — the run creates one result per point;
   3. `GET _apis/test/Runs/<run>/results` (the result id of each point), then `PATCH _apis/test/Runs/<run>/results` with each result's `outcome`, `state: Completed`, `comment` (the actual result, redacted) and the linked bugs;
   4. `PATCH _apis/test/runs/<run>` with `state: Completed`;
   5. `bug_status` state changes on existing bugs (`PATCH` of `System.State`).
5. **Read back** the run's results and the points' latest outcome: a difference → exit 5.

A run created but not completed (an earlier exit 3) is continued through the run id in the record, never created again.

---

## 5. Bugs

### 5.1 Duplicate search (`bug-search`)

The same draft and rules as the Jira technique §5.1, with WIQL (`POST _apis/wit/wiql`, then `GET _apis/wit/workitemsbatch` in groups of up to 200 ids):

```sql
SELECT [System.Id] FROM WorkItems
WHERE [System.TeamProject] = @project AND [System.WorkItemType] = 'Bug'
  AND [System.State] NOT IN ('Closed', 'Removed')
  AND [System.AreaPath] UNDER 'Demo Shop\Checkout' AND [Custom.Feature] = 'Coupons'
  AND [System.Tags] CONTAINS 'subarea-cart' AND [System.Tags] CONTAINS 'platform-web'
  AND [System.Tags] CONTAINS 'role-customer'
  AND ([System.Title] CONTAINS WORDS 'coupon expired' OR [Microsoft.VSTS.TCM.ReproSteps] CONTAINS WORDS 'coupon expired')
```

Values are escaped (single quotes doubled); the relaxed passes and the output are those of the Jira technique; the user decides.

### 5.2 Create and update

- **New bug** (`new_defect` in the Fail item): `POST _apis/wit/workitems/$Bug` with the mapped fields, `bugs.defaults`, the area/iteration, the markers, the repro steps in the project's bug format (methodology §9.2) and the relations: `Microsoft.VSTS.Common.TestedBy-Forward` to the test case, and the link to the test result (the artifact link a manual run creates), once the result exists.
- **Existing bug** (`bug`): linked to the test result, plus a comment (`POST _apis/wit/workItems/<id>/comments`) with the run's observation; with `bug_status`, the state change. Nothing else changed.
- Only after the approval of the dry run (methodology §9.4).

---

## 6. Importing cases (`import-cases`)

1. The test cases of the plan's suites (`GET _apis/testplan/Plans/<plan>/Suites/<suite>/TestCase`, following `x-ms-continuationtoken`), or a WIQL (`--query`) for cases outside a plan.
2. Their fields in groups of 200 (`workitemsbatch`), including `Microsoft.VSTS.TCM.Steps`; shared steps are expanded to text and flagged `shared_steps`; parameters are flagged `parameters` (not expanded).
3. The points and their latest outcome (`TestPoint`), mapped back to the project's vocabulary into `last_status`.
4. `tracker/cases.json` and `ids.yaml`, with the same model and flags as the Jira technique §6.

---

## 7. Exporting new cases (`export-cases`)

- Input and duplicate rules as the Jira technique §7.
- Per case: `POST _apis/wit/workitems/$Test Case` (title, mapped fields, the steps XML, the markers, the area), then `POST _apis/testplan/Plans/<plan>/Suites/<new_cases_suite_id>/TestCase` to add it to the suite, which creates its points for the suite's configurations.
- Record `tracker/sent/<cases file name>.json`; `ids.yaml` gets `NEW-1 → 4712` and the points.

---

## 8. Tests and golden

As the Jira technique §10, in `tests/golden/azure_devops/<scenario>/`: recorded fictional responses of `dev.azure.com/demo-shop`, the exact list of expected requests (JSON Patch bodies included), a second run with no request, the dry run with no writing request, the conflict, the interrupted send resumed through the run id, and the round trip import → export → import through the fake tracker (steps XML ↔ model included). The pilot (plan 10.5) uses a free Azure DevOps organisation with a practice project (Test Plans needs the organisation's trial or licence for the user who runs it), in a workspace outside this repository.
