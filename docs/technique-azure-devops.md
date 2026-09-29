# Azure DevOps technique

> **Layer 2 — Technique for the Azure DevOps (Test Plans + Boards) tracker adapter**
> Version 1.2 · 2026-09-29 · Plan, task 10.0 (specification first, methodology §15); decisions of 28.09.2026 recorded in 10.1 (§0, §3, §4.1); built in 10.1 (common base) and 10.3 (this adapter). 1.2 (10.3): the endpoints confirmed against the public documentation, and what that changed — the comments API is a preview (`7.1-preview.4`); the build goes in the run's name and comment, not in a build reference; the tracker stamps the dates; a bug is linked to a result through the result's `associatedBugs`; the order of the writes (the state change right after the new bugs); the marker of a test run is its comment; area and iteration values are full paths (§0, §1.1, §2, §3, §4.2, §5.2, §6, §7).
>
> Project agnostic: no organisation, project, area path, field or state of a real Azure DevOps lives here. The examples use the fictional **Demo Shop** (`https://dev.azure.com/demo-shop`, project `Demo Shop`). Subordinate to `methodology.md`.
>
> The **common contract** of the tracker adapters (command line, exit codes, dry run and `--yes`, the record of what was sent, markers, input validation, HTTP client, redaction, run log fields, golden) is `technique-jira-xray.md` §2 and §10, and is not repeated here: this document describes only what is specific to Azure DevOps.

---

## 0. Scope

- **Azure DevOps Services** (cloud): test cases are `Test Case` work items in a **test plan** and its suites; a case runs as a **test point** (test case × configuration in a suite); results go to a **test run** with one **outcome** per point; bugs are `Bug` work items linked to the test case and to the test result.
- API (version 1): **Azure DevOps Services REST API**, `api-version=7.1` (fixed in the configuration, raised only through the validation of methodology §15), authentication with a **personal access token** (PAT, basic authentication with an empty user) with the scopes *Work Items (read & write)* and *Test Management (read & write)*. Areas used: `wit` (work items, WIQL, links), `testplan` (plans, suites, test cases, test points) and `test` (runs and results).
- The endpoints were confirmed against the current public documentation in 10.3 (Azure DevOps Services REST API 7.1 on learn.microsoft.com; the list with its links is in the plan's note of 10.3). All use `api-version=7.1` except the work item **comments**, which exist only as a preview: `7.1-preview.4` (derived from `api_version`). What the documentation cannot show (e.g. the exact answer to a failed `test` on `/rev`, whether `CONTAINS WORDS` with several words needs all of them, whether linking a bug to a result adds a revision to the bug) is confirmed by the pilot (10.5), and this document is fixed first if it differs.
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
- `tag:<prefix>` stores the value as a tag (`tag:platform-` + `web` → `platform-web`); tags are `;`-separated in `System.Tags` (a `;` in a value is written as `,` and reported) and the adapter adds without removing the tags already there.
- The field types come from `GET _apis/wit/fields` (one read per run: `html`, `integer`, `picklistInteger`, `treePath`, `string`…) and what the type allows from `GET _apis/wit/workitemtypes/<type>/fields?$expand=All` (`alwaysRequired`, `defaultValue`, `allowedValues`): a value outside `allowedValues` or a required field without a value is exit 4 before any write.
- **Area and iteration** (`System.AreaPath`, `System.IterationPath`) are full paths of the project (`Demo Shop\Checkout`): a value that is not the project or under it (e.g. only `Checkout`) is refused (exit 4) rather than guessed. A new bug's area is, in order, the draft's mapped `area`, `bugs.area_path`, the test case's area; a new case's, its mapped `area` or `cases.area_path`.
- Test case steps live in `Microsoft.VSTS.TCM.Steps` (XML with an action and an expected result per step, both HTML): the adapter writes and reads that format; the model's `data` of a step goes at the end of the action, after a line `Data:`, and is read back from there.

---

## 2. Common contract

`technique-jira-xray.md` §2 applies as written, with these specific points:

- **Markers** are tags: `qa-batch-<batch_id>` on everything created, `qa-case-<case id>` on bugs; a test run carries the marker at the start of its **comment** (`qa-batch-<batch_id> · environment … · build …`; a run's tags are limited to 5 alphanumeric names under 30 characters, so a tag cannot carry it), and the batch id in its default name. The marker search of a run (`GET _apis/test/runs?planId=…&automated=false&includeRunDetails=true`, paged with `$skip`/`$top`) runs only when a record exists without a run: a run can only exist if an earlier `--yes` started the record. The marker search of a bug or a case is one WIQL on the tag, before any create.
- **Retries**: Azure DevOps throttles by usage and answers `429` (or delays) with `Retry-After` and `X-RateLimit-*` headers; the client honours them as in §2.7 of the Jira technique. Work item updates are `PATCH` with a JSON Patch body (`application/json-patch+json`) and a `test` operation on `/rev` when changing an existing work item, so a concurrent edit is a conflict (`412`, exit 2) instead of a silent overwrite.
- **Record** (`tracker/sent/<batch_id>.json`) keeps, in order: `bug` (the ids of the bugs created or reused), `state` (a `bug_status` sent), `run` (the test run id), `results` (per chunk of 100, case → result id), `attachment`, `run_completed`, `comment`. For `export-cases`: `case` (`NEW-1` → work item id) and `suite`.

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

`apply` confirms each case (`POST _apis/wit/workitemsbatch` with the ids for `id`, a WIQL on the field or the tag for `field`/`tag`, then the batch read) and each point (`GET _apis/testplan/Plans/<plan>/Suites/<suite>/TestPoint?testCaseId=<ids>` for each suite of `suite_ids`, or of the plan, following `x-ms-continuationtoken`). The platform is the item's `platform`, or the case's in `tracker/cases.json`. A case with several points needs that platform in `results.configurations`; a case in two suites of the plan with the same configuration is a conflict (exit 2), as is a case not in the plan, a work item that is not a `Test Case`, or an ID that `ids.yaml` has pointing to another work item.

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
   2. `bug_status` state changes on existing bugs (`PATCH _apis/wit/workitems/<id>` with `test` on `/rev` — the revision read at the resolution — and `System.State`). They go **right after the new bugs**: associating a bug with a test result and commenting on it add revisions to it, so after those the `test` would fail against the adapter's own writes;
   3. **one test run per batch**: `POST _apis/test/runs` (`name` from `run_name`, `plan: {id}`, `pointIds` of the items sent, `automated: false`, `comment` with the marker, `run.environment` and `run.build`) — the run creates one result per point. The build goes in the name and the comment: the run's `build`/`buildReference` refer to a pipeline build, which a manual run of a tester usually does not have. No dates are sent: Azure DevOps stamps the run and the results when they are written (a result's completed date must follow its started date, which is the run's creation), and the run log keeps each item's `ts`;
   4. `GET _apis/test/Runs/<run>/results` (the result id of each point, `testPoint.id`), then `PATCH _apis/test/Runs/<run>/results` in chunks of 100, each result with `outcome`, `state: Completed`, `comment` (the actual result, redacted, at most 1000 characters) and `associatedBugs` (`[{id}]`: the item's `bug` and the new bug) — the documented way a bug is linked to a test result;
   5. evidence (§8 of the Jira technique) on the test **result**: `POST _apis/test/Runs/<run>/Results/<result>/attachments` (`stream` in base64, `fileName`, `attachmentType: GeneralAttachment`); a resumed send lists the result's attachments first;
   6. `PATCH _apis/test/runs/<run>` with `state: Completed`;
   7. a comment on each existing bug (§5.2).
5. **Read back** the run's results (`detailsToInclude=WorkItems`: outcome and `associatedBugs`) and the points' latest outcome (`TestPoint`): a difference → exit 5.

A run created but not completed (an earlier exit 3) is continued through the run id in the record, never created again; a run whose create got no answer is found by its marker (§2).

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

- **New bug** (`new_defect` in the Fail item): `POST _apis/wit/workitems/$Bug` (`application/json-patch+json`: one `add` per field) with the mapped fields, `bugs.defaults`, the area/iteration (§1.1), the markers, the repro steps in the project's bug format (methodology §9.2: environment and build, role, platform, preconditions, expected, observed, the linked case, then the steps), and the relation `Microsoft.VSTS.Common.TestedBy-Forward` to the test case (`<organization>/_apis/wit/workItems/<id>`). The link to the test result is made from the result's side (`associatedBugs`, §4.2), once the result exists.
- **Existing bug** (`bug`): associated with the test result, plus a comment (`POST _apis/wit/workItems/<id>/comments?format=html&api-version=7.1-preview.4`) with the marker `qa-batch-<batch_id> · <case>`, the status, environment, build and the actual result (a resumed send looks for that marker in `GET …/comments` before commenting again); with `bug_status`, the state change (none when the bug is already there; a state that is not one of the type's is exit 2). Nothing else changed.
- Only after the approval of the dry run (methodology §9.4).

---

## 6. Importing cases (`import-cases`)

1. The test cases of the plan's suites (`suite_ids`, or every suite of `GET _apis/testplan/Plans/<plan>/suites`; for each `GET …/Suites/<suite>/TestCase`, following `x-ms-continuationtoken`), or a WIQL (`--query`) for cases outside a plan (flagged when they have no test point).
2. Their fields in groups of 200 (`POST _apis/wit/workitemsbatch`), including `Microsoft.VSTS.TCM.Steps` and `Microsoft.VSTS.TCM.Parameters`; shared steps (`<compref ref=…>`) are expanded to text from the shared steps work item and flagged `shared_steps`; parameters are flagged `parameters` (not expanded).
3. The points and their latest outcome (`TestPoint`), mapped back to the project's vocabulary into `last_status` (the point of the case's platform when it has several; points that disagree leave it empty, flagged).
4. `tracker/cases.json` (`key` is the work item id) and `ids.yaml` (`{id, points: {configuration: point}}`), with the same model and flags as the Jira technique §6.

---

## 7. Exporting new cases (`export-cases`)

- Input and duplicate rules as the Jira technique §7.
- Duplicates: an own ID (`field`/`tag`) that already exists → exit 2; a `Test Case` with the same title in the project (WIQL) → a candidate duplicate, exit 2; a case with the file's marker and the same title (a create that got no answer) is reused.
- Per case: `POST _apis/wit/workitems/$Test Case` (title, mapped fields, the steps XML, the markers, the area), then one `POST _apis/testplan/Plans/<plan>/Suites/<new_cases_suite_id>/TestCase` (`[{workItem: {id}}]`) with every new case, which creates their points for the suite's configurations (`pointAssignments` in the answer). A resumed export asks the suite which of the cases it already has before adding. `new_cases_suite_id` must be a static suite (`check`).
- Record `tracker/sent/<cases file name>.json` (`NEW-1 → 5002`); `ids.yaml` gets the **final** ID (the work item id with `id`, the project's own ID with `field`/`tag`) and its points, as in the Jira technique §7; the command prints `NEW-1 -> 5002`.

---

## 8. Tests and golden

As the Jira technique §10, in `tests/golden/azure_devops/<scenario>/`: recorded fictional responses of `dev.azure.com/demo-shop`, the exact list of expected requests (JSON Patch bodies included), a second run with no request, the dry run with no writing request, the conflict, the interrupted send resumed through the run id, and the round trip import → export → import through the fake tracker (steps XML ↔ model included). The pilot (plan 10.5) uses a free Azure DevOps organisation with a practice project (Test Plans needs the organisation's trial or licence for the user who runs it), in a workspace outside this repository.
