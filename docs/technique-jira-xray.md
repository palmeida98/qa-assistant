# Jira (Xray) technique

> **Layer 2 — Technique for the Jira + Xray tracker adapter**
> Version 1.2 · 2026-09-28 · Plan, task 10.0 (specification first, methodology §15); decisions of 28.09.2026 recorded in 10.1 (§3, §4.1, §8); built in 10.1 (common base) and 10.2 (this adapter). 1.2 (10.2): the endpoints confirmed against the public documentation, and what that changed — the first chunk of results through the multipart import (the execution's label), the build in the summary, `getTests` by issue ids, the test plan status through `status(testPlan:)`, rich text of new tests through Jira after `createTest`, the final IDs in `ids.yaml` (§0, §4.2, §5.2, §6, §7, §8, §10.1).
>
> Project agnostic: no site, project key, field id or status of a real Jira lives here. The examples use the fictional **Demo Shop** (`https://demo-shop.atlassian.net`, project `DS`, keys like `DS-123`). Subordinate to `methodology.md`: the decision, recording, bug and checkpoint rules are there and are not repeated here.
>
> §2 is the **common contract of the tracker adapters** (command line, exit codes, dry run, idempotency, ID mapping, HTTP client, golden). `technique-azure-devops.md` follows it and only describes what differs.

---

## 0. Scope

- **Jira Cloud** with **Xray Cloud**: test cases are Xray `Test` issues (test type `Manual`, with steps), results go to an Xray `Test Execution`, bugs are Jira issues (usually `Bug`) linked to the test run and, optionally, to the test.
- APIs (version 1, target **cloud first**):
  - **Jira Cloud REST API v3** (`https://<site>/rest/api/3/…`), basic authentication with the account's e-mail and an **API token**;
  - **Xray Cloud API v2** (`https://xray.cloud.getxray.app/api/v2/…`, or the host of the Xray region the site uses): `POST /authenticate` with a **client id and client secret** (an Xray API key) returns the token as a JSON string, used as `Authorization: Bearer`; results through the **import of an execution in Xray JSON** (`POST /import/execution/multipart` for the first chunk, which creates the execution with Jira fields — its label among them —, and `POST /import/execution` with `testExecutionKey` for the next ones); **GraphQL** (`POST /graphql`) where REST has no equivalent (the Xray statuses, reading steps, creating a test with its steps, adding tests to a test plan, reading back the test runs of a test execution).
  - The endpoints were confirmed against the current public documentation in 10.2 (list and links in the plan's note of 10.2): the Jira Cloud REST v3 OpenAPI description and the Xray Cloud REST v2 and GraphQL references. What the documentation cannot show (e.g. the exact error of a JQL with a key that does not exist, whether `createTest`'s folder must exist) is confirmed by the pilot (10.5), and this document is fixed first if it differs.
- The four directions of version 1: **send results** (§4), **bugs** — search duplicates, create, update (§5), **import cases** (§6), **export new cases** (§7).
- **Out of scope in version 1** (future, only when a real project needs them): Jira Server / Data Center and Xray Server / Data Center (other APIs: REST v2 and the Xray `raven` API, personal access tokens); OAuth 2.0 (3LO) apps; test types other than `Manual` (`Cucumber`, `Generic` are imported read only, flagged, never exported); creating `Precondition`, `Test Set` or `Test Plan` issues (existing ones are read and linked); results per step (only the test's status, with the actual result as the comment); the dashboard (`/dashboard` stays Excel only: §9); webhooks or any sync that runs without the assistant.

---

## 1. Configuration in `project.yaml`

The `jira:` section of the workspace's `project.yaml` (commented in the template `docs/project.yaml`), read only when `tracker: "jira"`. Only **names** of `.env` variables, never values (methodology §1.2); the names end in `_var`, so `workspace_git.py init` puts them in `.env.example`.

```yaml
tracker: "jira"
jira:
  site: "https://demo-shop.atlassian.net"   # (req.) Jira Cloud site, no path
  project_key: "DS"                          # (req.)
  user_var: "DS_JIRA_USER"                   # (req.) e-mail of the account (variable name)
  token_var: "DS_JIRA_TOKEN"                 # (req.) Atlassian API token (variable name)
  xray:
    base_url: "https://xray.cloud.getxray.app"   # (opt.) the region's host, if the site uses one
    client_id_var: "DS_XRAY_CLIENT_ID"           # (req.) Xray API key (variable names)
    client_secret_var: DS_XRAY_CLIENT_SECRET     # (req.)

  cases:
    issue_type: "Test"                 # (opt.)
    jql: "project = DS AND issuetype = Test AND component = Checkout"   # (opt.) which tests import-cases reads
    id_source: "key"                   # key | field | label  (§3)
    id_field: ""                       # with field: e.g. customfield_10050
    id_label_prefix: ""                # with label: e.g. "qa-id-"
    folder: ""                         # (opt.) Xray test repository folder of new tests, e.g. "/Checkout"
    fields:                            # model field -> Jira field (§1.1)
      area: "components"
      feature: "customfield_10051"
      preconditions: "description"
      priority: "priority"
      role: "labels:role-"
      platform: "labels:platform-"
      technique: "labels:technique-"
      origin: "labels:origin-"

  results:
    statuses:                          # (req.) the project's vocabulary -> Xray status (§4.1)
      "Pass": "PASSED"
      "Fail": "FAILED"
      "Not run": "TODO"
      "Blocked": "BLOCKED"             # asked at the onboarding: a custom Xray status (it must exist in the Xray settings) or ABORTED
      "N/A": "skip"                    # asked; skip (or empty) = not sent, stays in the run log (§4.1)
    test_plan_key: ""                  # (opt.) e.g. DS-10: every execution is linked to this test plan
    summary: "{batch_id} · {environment} · {build}"   # (opt.) summary of the Test Execution
    attach_evidence: false             # (opt.) true: the batch's files under evidence/ go as Xray evidence (§8)

  bugs:
    issue_type: "Bug"                  # (opt.)
    fields:                            # model field -> Jira field; the run log's new_defect uses these keys
      title: "summary"
      description: "description"
      area: "components"
      feature: "customfield_10051"
      sub_area: "labels:subarea-"
      platform: "labels:platform-"
      role: "labels:role-"
      environment: "environment"
      severity: "customfield_10060"
      priority: "priority"
    defaults: {}                       # (opt.) field -> value when the batch does not bring it
    open_jql: "statusCategory != Done" # (opt.) what counts as open in the duplicate search (§5.1)
    link_type: ""                      # (opt.) issue link Bug -> Test (e.g. "Relates"); the test run's defect link always exists
    statuses: []                       # (opt.) statuses bug_status may ask for (resolved_bugs rule, methodology §9.3)
    priorities: []                     # (opt.) priorities a new_defect may have (§2.6); empty: not checked before the tracker
```

### 1.1 Field mapping

- A plain value (`"summary"`, `"customfield_10051"`) is a Jira field id. The adapter reads the field's schema once per run (`GET /rest/api/3/field` and the create metadata of the issue type) and formats the value: text, option (`{"value": …}`), array of options, component (`{"name": …}`), priority (`{"name": …}`), rich text in **Atlassian Document Format** (ADF). A field that does not exist in the project, or is not on the issue type's create screen, is a configuration error (exit 4) found by `check` (§2.2) before any write.
- `labels:<prefix>` stores the value as a label with that prefix (`labels:platform-` + `web` → `platform-web`); on import, the prefix is removed. Labels cannot have spaces: a value with spaces is written with `-` and reported.
- Rich text: the adapter writes plain paragraphs, bullet lists and code blocks in ADF, and reads ADF back to plain text. The round trip (§10.2) is exact for what the adapter writes; formatting written by people in Jira (tables, panels, mentions) is imported as plain text and flagged `rich_text_simplified`.
- A model field without a mapping is not sent and is not imported (the dry run lists them).

---

## 2. Common contract of the tracker adapters

Shared by this adapter and `technique-azure-devops.md` (common base `adapters/tracker/`, plan 10.1).

### 2.1 Where things live

| What | Where | Committed in the workspace |
|---|---|---|
| Code | `adapters/tracker/` (base, HTTP client, record, CLI), `adapters/jira/`, `adapters/azure_devops/` | — (qa-assistant) |
| Configuration | `jira:` / `azure_devops:` of `project.yaml` | yes |
| Credentials | `.env`, referred to by the `*_var` names | never |
| ID mapping cache | `<workspace>/tracker/ids.yaml` (§3) | yes |
| Imported cases | `<workspace>/tracker/cases.json` (§6) | yes |
| Record of what was sent | `<workspace>/tracker/sent/<batch_id>.json` (§2.5) | yes |
| Recorded exchanges (pilot, `--record`) | `<workspace>/tracker/recordings/` (§10.3) | no (`.gitignore`) |

Nothing is ever written to `history/` (methodology §13), and no record lives in qa-assistant.

### 2.2 Command line

One entry point for both trackers; the tracker comes from `project.yaml` (`tracker:`), so the skills do not branch per tracker. From the root of qa-assistant:

```
python -m adapters.tracker <workspace> check
python -m adapters.tracker <workspace> import-cases [--query "<JQL|WIQL>"]
python -m adapters.tracker <workspace> export-cases <cases.json> [--yes]
python -m adapters.tracker <workspace> apply <batch.json> [--yes] [--now YYYY-MM-DDTHH:MM:SS]
python -m adapters.tracker <workspace> bug-search <draft.json> [--include-closed]
```

- `check` — read only: authenticates, confirms the project, the issue types, every mapped field, the statuses of `results.statuses` (Xray settings) and the test plan; lists what is missing. Used at the onboarding and after changing the configuration.
- `import-cases` (§6) and `bug-search` (§5.1) only read from the tracker.
- `export-cases` (§7) and `apply` (§4, with the batch's bugs, §5) write to the tracker, **only with `--yes`**.
- `--now` fixes every date the adapter writes (as `adapters.excel.apply --now`), for the golden.

### 2.3 Dry run by default, `--yes` to send

Without `--yes`, a writing command does everything except the writes: it validates the input, authenticates, **resolves** every reference against the tracker (reads only) and prints the plan: each request it would make (method, path and a summary of the body, redacted), grouped as results, new bugs, updated bugs, links, and what is not sent (`skip`, unmapped fields). The user approves that list (methodology §9.4); only then the skill runs the same command with `--yes`. `--yes` repeats the resolution (the tracker may have changed since) and sends.

### 2.4 Exit codes

The same meaning as `adapters.excel.apply` where one exists, and the dry-run code of `migrate_workspace.py`:

| Code | Meaning | Anything written? |
|---|---|---|
| 0 | Sent and read back; or nothing to send (batch already sent with the same content; every item not sent: `skip` or unmapped, §4.1) | yes / no |
| 1 | Dry run with something to send: **not approved yet**, repeat with `--yes` after the user approves | no |
| 2 | Cross-check / conflict with the tracker: a case, bug, test plan or test point not found; a case mapped to two issues; an ID in `ids.yaml` that now points elsewhere | no |
| 3 | Network, authentication or rate limit (after the retries of §2.7), or a server error. With `--yes` the send may be **partial**: the record (§2.5) has exactly what was confirmed, and running the same command again continues from there | maybe (partial) |
| 4 | Invalid batch, input or configuration: nothing resolved nor sent (see the list of problems); includes a `Question`, a status that is not a key of the status map (a typo is never dropped silently) and a batch already sent with **another content**; also a back-end not built yet (plan 10.2/10.3) | no |
| 5 | Sent, but the read back does not match what was sent (e.g. an Xray status changed by an automation rule, a result not recorded): show the differences; never "fix" by sending again blindly | yes |

`import-cases` and `bug-search` use 0, 2, 3 and 4. `check` uses 0, 3 and 4.

**Everything is resolved before the first write** (the tracker version of the Excel adapter's "validate the whole batch before writing"): codes 2 and 4 never leave a half-sent batch.

### 2.5 Idempotency: the record of what was sent

A batch **must never be sent twice without being detected** (as `technique-excel.md` §5):

- The record `<workspace>/tracker/sent/<batch_id>.json` (`batch_id` = the run log's file name without the extension, as in Excel) keeps: the SHA-256 of the batch's `results` (the same `batch_hash` as the Excel adapter; the `translated_from` identity when present), the tracker, the instant, and **each write confirmed by the tracker** with what it created (Test Execution key, test run ids, bug keys, test run id and result ids in Azure DevOps). It is written after **each** confirmed write, not at the end.
- Same `batch_id`, same content, record complete → exit 0, **no request at all**.
- Same `batch_id`, **other content** → exit 4, nothing sent. A correction goes in a new batch, with another name, with only the cases to correct (and `bug` instead of `new_defect` for a bug already created) — the rule of `/checkpoint` for Excel.
- Record incomplete (an earlier run ended with 3) → the run continues after the last confirmed write.
- **Marker in the tracker**: everything the adapter creates carries the label (Jira) or tag (Azure DevOps) `qa-batch-<batch_id>`, and a bug also `qa-case-<case id>`. Before creating, the adapter searches for the marker: an object already there (a create that timed out after the tracker had done it, or a record lost with a computer) is **reused** and recorded, never created again.
- A create request (`POST`) is **never retried blindly**: after a timeout, the marker search decides whether it happened.

The records are committed with the workspace's checkpoint (methodology §12.1), so a clone on another computer knows what was sent. They contain keys and statuses, never credentials; comments and bug texts are kept redacted (§8).

### 2.6 Validation of the input

Before any request, the whole batch is refused (exit 4, a list of problems) if: it is empty; an item has no `tc` or `status`; a case appears twice; a status is not a key of the status map (so a `Question` still in the log is refused: methodology §8.5 — the map never has `Question`); a `new_defect` is not an object, misses `title`, `expected` or `observed` (the minimum of methodology §9.2; the project's bug format may ask for more, which the back-end checks against the tracker's required fields), or has a `priority` outside `bugs.priorities` when the project lists them; `bug_status` is not in `bugs.statuses` (`bugs.states` in Azure DevOps), or comes without a `bug`; an `evidence` path is outside the workspace's `evidence/` or does not exist; `run.environment` is missing or is not a key of `environments` in `project.yaml`, or `run.build` is missing (methodology §7.1, §9.2). Unknown keys are ignored, as in the Excel adapter.

### 2.7 HTTP client

- Python's standard library (`urllib`), as `scripts/api_request.py`: no new dependency (fixed versions, plan 7.5). One function does every request, so the tests replace it (§10).
- Timeouts per request (default 30 s). Retries only for `429` and `502/503/504`, and only for reads and idempotent writes (`GET`, `PUT`, Xray's import into an existing execution): `Retry-After` honoured (capped at 120 s), otherwise exponential backoff with jitter; at most 5 attempts, then exit 3. A `401` re-authenticates once (the Xray token expires), a second `401` or a `403` is exit 3 with the reason, never with the token.
- Pagination is always followed to the end (§6), with a guard: more than `max_items` (default 5000) results is a usage error that asks for a narrower query.
- **Redaction**: the `Redactor` of `scripts/common.py` (the one `api_request.py` uses), given the values of every `*_var` variable of the section and the Xray token obtained at run time. It is applied to everything the adapter prints, to the record and to the recordings. The `Authorization` header is never printed.

### 2.8 Run log fields the tracker adapters use

The run log of methodology §7.1 (`{"results": [...]}`), plus:

- `run` (top level, required by these adapters): `{"environment": "test", "build": "2.4.1"}`; the environment is a key of `environments` in `project.yaml`. The Excel adapter ignores it.
- per item: `tc`, `status`, `actual_result`, `ts` (start/finish of the test run), `bug` (an existing bug key), `new_defect` (a dict with the keys of `bugs.fields`), and `bug_status` (optional: a status of `bugs.statuses` for the item's `bug` — a retest that confirms the fix; methodology §9.3), `evidence` (optional: file paths under `evidence/`, §8).

---

## 3. Case IDs: internal ID ↔ Jira key

The case ID is **stable** (methodology §2). `cases.id_source` says where the internal ID lives, and the mapping always lives **in Jira**, so it is the same for every person and every computer:

| `id_source` | The internal ID is | Example `tc` in the run log |
|---|---|---|
| `key` (default) | the issue key itself — the project has no IDs of its own | `DS-123` |
| `field` | the value of a custom field (`id_field`) | `TC-SHOP-001` in `customfield_10050` |
| `label` | a label with a prefix (`id_label_prefix`) | `TC-SHOP-001` from `qa-id-TC-SHOP-001` |

The default is `key` (decision of 28.09.2026): most Jira projects have no IDs of their own, and the key is already stable and unique; `field` and `label` stay for projects that keep their own IDs (e.g. cases migrated from a workbook).

`<workspace>/tracker/ids.yaml` is a **cache** written by `import-cases` and `export-cases`:

```yaml
tracker: jira
cases:
  TC-SHOP-001: {key: DS-123, id: "10123"}
```

`apply` resolves each `tc` through the cache and **confirms** it with one JQL query (`key in (…)`, or the field/label); a case not found, a key that now has another internal ID, or two tests with the same internal ID → exit 2. An issue moved to another project (key changed) is followed through the issue id and the cache is updated, reported. With `key`, a case created by `export-cases` gets its ID from Jira (§7).

---

## 4. Sending results (`apply`, at `/checkpoint`)

### 4.1 Status mapping

`results.statuses` maps **every** status of the project's vocabulary to an Xray test run status, or to `skip`. It is the project's vocabulary for these adapters (the role `excel.cases.statuses` has in Excel). Xray Cloud's built-in statuses are `TODO`, `EXECUTING`, `PASSED`, `FAILED`, `ABORTED`; others (e.g. `BLOCKED`, `NA`) are custom statuses an Xray administrator creates.

Defaults proposed at the onboarding, for the methodology's statuses (the user confirms; methodology §1.3):

| Project status | Xray | Why |
|---|---|---|
| `Pass` | `PASSED` | built in |
| `Fail` | `FAILED` | built in |
| `Not run` | `TODO` | built in; the test stays in the execution, not run |
| `Blocked` | no default: a custom Xray status (e.g. `BLOCKED`) or `ABORTED` — **asked**; otherwise unmapped (not sent) | `ABORTED` means something else in many teams |
| `N/A` | no default: a custom Xray status (e.g. `NA`) — **asked**; otherwise unmapped (not sent) | there is no built-in N/A |
| `Question` | never mapped: refused (exit 4) | methodology §8.5 |

**Not sent** (decision of 28.09.2026, plan Phase 10): a status whose value is `skip` or empty (`""` / `null`: *unmapped*) is part of the vocabulary but **is not sent** — the item stays only in the run log, is listed in the dry-run plan, in the summary and in the record (§2.5) as not sent, and it is **not an error**. `/project-onboarding` asks per project for `Blocked` and `N/A` (a custom Xray status or `ABORTED`); when the user chooses none, it writes the key with `skip`, so every status of the vocabulary stays a key of the map. A status that is **not a key** of the map is refused (exit 4, §2.6): only the map decides what is left out, a typo is never dropped silently. `Question` is refused even if someone writes it in the map (`validate_project.py` flags it). `check` confirms that every mapped status exists in Xray.

### 4.2 Flow

1. Validate the batch (§2.6) and read the record (§2.5).
2. Authenticate (Jira: basic; Xray: `POST /authenticate`).
3. Resolve: every `tc` → test issue (§3), every `bug` → existing issue, the test plan, the marker search for objects of this batch (§2.5).
4. Without `--yes`: print the plan, exit 1.
5. With `--yes`, in this order, recording after each write:
   1. new bugs (`POST /rest/api/3/issue`, §5.2), so their keys can go into the test runs;
   2. the results: **one Test Execution per batch**, in chunks of 100 items. The first chunk goes through `POST /api/v2/import/execution/multipart`: the part `results` (Xray JSON: per item `testKey`, `status`, `comment` — the actual result, redacted —, `start`/`finish` from the item's `ts` with its offset — a `ts` without one gets this computer's —, `defects` — the item's `bug` and/or the new bug — and `evidence`, §8) and the part `info` (the Jira fields of the new execution: project, issue type `Test Execution`, summary from `results.summary`, the label `qa-batch-<batch_id>`; and `xrayFields`: the test environment = `run.environment`, the test plan). The multipart import is used because the plain import's `info` cannot take a label, and without the label an execution created by a request that timed out could not be found again (§2.5). The **build** (`run.build`) goes in the summary (default `{batch_id} · {environment} · {build}`) and in each bug's description: Xray's `version`/`revision` are a Jira version and a field many projects do not use. The next chunks import into the execution's key (`POST /api/v2/import/execution` with `testExecutionKey`, retried as an idempotent write), so the whole batch stays in one execution;
   3. existing bugs: the comment (`POST /rest/api/3/issue/<key>/comment`), the `bug_status` transitions (`GET`/`POST /rest/api/3/issue/<key>/transitions`, the transition whose target status is the one asked, resolved before the first write), and the optional `link_type` links of the new bugs (`POST /rest/api/3/issueLink`).
6. **Read back** (GraphQL `getTestExecution(issueId)` → `testRuns`, 100 per page: status and defects, which Xray gives as issue ids) and compare with what was sent: a difference → exit 5.

A case run again in a later batch goes into that batch's new execution: Xray keeps the history per execution, and the latest status in the test plan is the latest execution's.

---

## 5. Bugs (`/log-bug` and `apply`)

### 5.1 Duplicate search (`bug-search`)

Methodology §9.1: before creating, look for the same problem. `bug-search <draft.json>` reads a draft with the keys of `bugs.fields` (the same dict that becomes the item's `new_defect`) plus `observed` (and `title`), builds a JQL from the mapped fields and runs it, read only:

```
project = DS AND issuetype = Bug AND statusCategory != Done
  AND component = "Checkout" AND cf[10051] = "Coupons"
  AND labels = "subarea-cart" AND labels = "platform-web" AND labels = "role-customer"
  AND text ~ "\"coupon\" \"expired\" \"accepted\""
```

- Criteria from area, feature, sub-area, platform, role (each only when mapped and present) and the observed text (up to 6 significant words of `observed`/`title`: no stop words, at least 4 letters, JQL-escaped). `open_jql` limits it to open bugs; `--include-closed` also looks at closed ones (a regression of a fixed bug).
- **Relaxed passes** when the strict query finds nothing: drop the role, then the platform, then the sub-area, then the text; each candidate says which pass found it and which criteria match.
- Output: key, summary, status, criteria matched, ordered by criteria matched. The adapter **never decides**: the candidates are shown and the user chooses — update the existing bug or create a new one (methodology §9.1 step 5).

### 5.2 Create and update

- **New bug**: the approved draft goes in the Fail item as `new_defect` (as in Excel), and is created at `apply` (`POST /rest/api/3/issue`) with the mapped fields, `bugs.defaults`, the markers `qa-batch-<batch_id>` and `qa-case-<tc>`, and the description in the project's bug format (`bug_format` and the workspace's `CLAUDE.md`): environment and build (`run`), role, platform, preconditions, steps, **expected → observed**, linked cases (methodology §9.2). Severity and priority only when the user decided them. It is linked to the test run (`defects`) and, with `link_type`, to the test.
- **Existing bug** (`bug` in the item): linked to the test run, plus a comment with the run's observation (`POST /rest/api/3/issue/<key>/comment`: the marker `qa-batch-<batch_id> · <case>`, status, environment, build, actual result — a resumed run looks for that marker in the bug's comments before commenting again, since a comment has no label); with `bug_status`, the transition to that status (resolved_bugs rule, methodology §9.3; none when the bug is already there; no transition to it is exit 2). Nothing else of the bug is changed.
- A `new_defect` on an item whose status is not sent (`skip`/unmapped) is not created either, and is listed as not sent.
- Nothing is sent without the approval of the dry run (§2.3; methodology §9.4).

---

## 6. Importing cases (`import-cases`)

For the onboarding (methodology §4 step 7), `/plan-batch` and `/run-batch`:

1. JQL `cases.jql` (default `project = <project_key> AND issuetype = <issue_type>`), through `POST /rest/api/3/search/jql` with `nextPageToken` pagination (up to 100 issues per page), only the mapped fields.
2. Steps and test type through GraphQL `getTests(issueIds: […], limit: 100)`, 100 issue ids at a time (the ids of step 1: `getTests` with a `jql` refuses a JQL that matches more than 100 issues): each step's action, data and expected result; linked `Precondition` issues (their definition appended to `preconditions` as text; `preconditions_from` keeps their keys, read only).
3. The latest status of each test in the test plan (`results.test_plan_key`: the plan's issue id from a JQL, then the field `status(testPlan: <id>)` of each test in the same `getTests`), mapped back to the project's vocabulary (a status mapped from two project statuses is imported as the first one and flagged; a status not in the map is flagged).
4. Writes `<workspace>/tracker/cases.json` (internal model, methodology §2: `id`, `key`, `title`, `area`, `feature`, `preconditions`, `steps` [{`action`, `data`, `expected`}], `expected` — the last step's expected result —, `role`, `platform`, `priority`, `technique`, `origin`, `last_status`, and `test_type`), ordered by key, and refreshes `ids.yaml`. A model field without a mapping is `null`.

Flags, never silent fixes (methodology §11): a test without steps, a non-`Manual` test, a test without an internal ID (`field`/`label`), two tests with the same ID (exit 2), a case with several expected results (methodology §5.3).

---

## 7. Exporting new cases (`export-cases`, after `/generate-cases`)

- Input: the cases the user approved in `/generate-cases`, as `{"cases": [...]}` with the fields of the model (the same as `cases.json`) and a provisional `id` (`NEW-1`, …) when `id_source` is `key`, or the project's ID pattern otherwise.
- Resolution: an internal ID that already exists → exit 2; a test with the same summary in the same folder → a candidate duplicate, listed, not created without the user's decision (methodology §10: re-run with the case removed from the file).
- Creation, per case: GraphQL `createTest` (test type `Manual`, the steps, `folderPath` = `folder`, and the Jira fields: summary, mapped fields, the marker `qa-batch-<file name>`, and the ID field or label with `field`/`label`); the **rich text** fields (e.g. `description`) go right after through `PUT /rest/api/3/issue/<key>` in ADF, because the format `createTest`'s `jira` object expects for them is not documented; then, with `results.test_plan_key`, one `addTestsToTestPlan` with every new test (idempotent). A `createTest` without an answer is exit 3 and the next run finds the test by its marker and summary (§2.5).
- The record is `tracker/sent/<cases file name>.json` (§2.5, same rules): it keeps `NEW-1 → DS-140`; `ids.yaml` gets the **final** ID (`DS-140: {key: DS-140, id: …}` with `key`, the project's own ID with `field`/`label`) — a provisional `NEW-<n>` is reused by the next cases file, so it is never a key of the cache —, and the command prints `NEW-1 -> DS-140` for the skill to tell the user.

---

## 8. Evidence and redaction

- Comments, bug descriptions and the record are redacted (§2.7). A redaction that changed something is reported in the dry run (the user sees what the tracker will get).
- With `attach_evidence: true`, the files an item lists under `evidence` (only inside the workspace's `evidence/`) go as Xray evidence of the test run (base64 in the import: `data`, `filename`, `contentType`). Attaching them to a bug (`POST /rest/api/3/issue/<key>/attachments`, header `X-Atlassian-Token: no-check`) is not done in version 1: the test run, linked to the bug as a defect, already carries them. Text files are redacted; images and videos cannot be, so they go only with `evidence: "text+screenshot"` in `project.yaml` and are listed one by one in the dry run. A file over the size limit (default 10 MB) is refused (exit 4).
- **Screenshots** (decision of 28.09.2026): uploading them is **off by default** (`attach_evidence: false`); it is on only when the project's evidence policy is `text+screenshot` **and** `attach_evidence: true`. With `evidence: "text"`, an image or video an item lists is not uploaded (listed as not sent in the dry run), and `validate_project.py` warns that `attach_evidence: true` only sends text files.

---

## 9. What is not done

- `/dashboard` stays Excel only: a tracker's own reports (Xray's test plan and execution reports) are the progress view; building the dashboard from `tracker/cases.json` and the records would show only what this assistant sent. Revisited only if a real project asks for it.
- No bulk edit of existing tests (steps, fields): `export-cases` only creates. A change to an existing case is proposed to the user and done in Jira.

---

## 10. Tests and golden (methodology §15)

### 10.1 Golden with recorded fictional responses

`tests/golden/jira/<scenario>/`, all **fictional** and written by hand (Demo Shop): `scenario.json` (the command, the exit codes of the dry run and of each run, the fault to inject, the kinds of writes the record must end with), the batch under `runs/`, optionally `tracker/ids.yaml` and a `project.yaml` (otherwise the shared `tests/golden/jira/project.yaml`), and per run `responses*.json` (the tracker's responses, in order, each with the request it answers: method and path) and `expected_requests*.json` (the exact list of requests: method, URL, the authentication scheme — never the credential —, the media type, and the body, a multipart body as its parsed parts; a credential's value appears as `<NAME>`). The `.env` is written by the test with the fictional values of `tests/tracker_kit.py`: a `.env` is never committed, not even a fictional one (`.gitignore`, detect-secrets).

The test runs the command with `--yes` and `--now` through the fake transport of §2.7 and compares the requests made with `expected_requests.json` **exactly** (order included); then runs it again and checks there is **no request at all** (idempotency), and runs the dry run and checks there is no writing request. The expected list is never generated by the test (as the Excel golden: `technique-excel.md`, methodology §15); `python tests/test_jira_golden.py --print <scenario>` only shows the requests made, to help the person writing the fixture. Scenarios: results only; a new bug and an existing bug with `bug_status`; a conflict (unknown case: exit 2, no write); an interrupted send (a fault injected at request *n*: exit 3; then a resumed run with no duplicate creation, the marker found); `Question` in the batch (exit 4, no request).

It is a `unittest` (no LibreOffice, no network), so it runs in `pre-commit` and in the CI with the other unit tests.

### 10.2 Round trip

Import → export → import through the fake tracker: the cases of a fixture tracker are imported into `cases.json`, exported to an empty fake tracker, imported again, and the two `cases.json` must be equal (IDs apart, which the mapping relates). It covers the field mapping, the labels with prefix, ADF ↔ text and the steps.

### 10.3 Pilot and real recordings

The pilot (plan 10.5) runs on a free Jira Cloud site with an Xray trial, in a practice workspace outside this repository. `--record` saves each request and response, redacted, in `<workspace>/tracker/recordings/`: the base of a later **real golden** inside the workspace (methodology §15), never in qa-assistant.
