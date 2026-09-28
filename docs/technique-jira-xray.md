# Jira (Xray) technique

> **Layer 2 — Technique for the Jira + Xray tracker adapter**
> Version 1.0 · 2026-09-28 · Plan, task 10.0 (specification first, methodology §15); built in 10.1 (common base) and 10.2 (this adapter).
>
> Project agnostic: no site, project key, field id or status of a real Jira lives here. The examples use the fictional **Demo Shop** (`https://demo-shop.atlassian.net`, project `DS`, keys like `DS-123`). Subordinate to `methodology.md`: the decision, recording, bug and checkpoint rules are there and are not repeated here.
>
> §2 is the **common contract of the tracker adapters** (command line, exit codes, dry run, idempotency, ID mapping, HTTP client, golden). `technique-azure-devops.md` follows it and only describes what differs.

---

## 0. Scope

- **Jira Cloud** with **Xray Cloud**: test cases are Xray `Test` issues (test type `Manual`, with steps), results go to an Xray `Test Execution`, bugs are Jira issues (usually `Bug`) linked to the test run and, optionally, to the test.
- APIs (version 1, target **cloud first**):
  - **Jira Cloud REST API v3** (`https://<site>/rest/api/3/…`), basic authentication with the account's e-mail and an **API token**;
  - **Xray Cloud API v2** (`https://xray.cloud.getxray.app/api/v2/…`, or the host of the Xray region the site uses): `POST /authenticate` with a **client id and client secret** (an Xray API key) returns a token used as `Authorization: Bearer`; results through the **import of an execution in Xray JSON** (`POST /import/execution`); **GraphQL** (`POST /graphql`) where REST has no equivalent (reading steps, creating a test with its steps, reading back the statuses of a test execution).
  - The endpoint names are those of the public documentation at the time of writing (09.2026); 10.2 confirms each one against the current documentation and the pilot (10.5) before relying on it, and fixes this document first if one changed.
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
      "Blocked": "BLOCKED"             # a custom Xray status: it must exist in the Xray settings
      "N/A": "skip"                    # not sent (§4.1)
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
| 0 | Sent and read back; or nothing to send (batch already sent with the same content; every item `skip`) | yes / no |
| 1 | Dry run with something to send: **not approved yet**, repeat with `--yes` after the user approves | no |
| 2 | Cross-check / conflict with the tracker: a case, bug, test plan or test point not found; a case mapped to two issues; an ID in `ids.yaml` that now points elsewhere | no |
| 3 | Network, authentication or rate limit (after the retries of §2.7), or a server error. With `--yes` the send may be **partial**: the record (§2.5) has exactly what was confirmed, and running the same command again continues from there | maybe (partial) |
| 4 | Invalid batch, input or configuration: nothing resolved nor sent (see the list of problems); includes a `Question` or unmapped status and a batch already sent with **another content** | no |
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

Before any request, the whole batch is refused (exit 4, a list of problems) if: it is empty; an item has no `tc` or `status`; a case appears twice; a status is not a key of the status map (so a `Question` still in the log is refused: methodology §8.5 — the map never has `Question`); a `new_defect` misses a field the bug format requires, or has a priority outside the allowed ones; `bug_status` is not in `bugs.statuses`; `run.environment` is missing or is not a key of `environments` in `project.yaml`, or `run.build` is missing (methodology §7.1, §9.2). Unknown keys are ignored, as in the Excel adapter.

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
| `Blocked` | no default: a custom `BLOCKED`, or `ABORTED`, or `skip` — **asked** | `ABORTED` means something else in many teams |
| `N/A` | no default: a custom status, or `skip` — **asked** | there is no built-in N/A |
| `Question` | never mapped: refused (exit 4) | methodology §8.5 |

`skip`: the item is not sent (it stays in the run log and in the record as not sent); the dry run and the summary count them. `check` confirms that every mapped status exists in Xray.

### 4.2 Flow

1. Validate the batch (§2.6) and read the record (§2.5).
2. Authenticate (Jira: basic; Xray: `POST /authenticate`).
3. Resolve: every `tc` → test issue (§3), every `bug` → existing issue, the test plan, the marker search for objects of this batch (§2.5).
4. Without `--yes`: print the plan, exit 1.
5. With `--yes`, in this order, recording after each write:
   1. new bugs (`POST /rest/api/3/issue`, §5.2), so their keys can go into the test runs;
   2. the results: **one Test Execution per batch**, through `POST /api/v2/import/execution` (Xray JSON): `info` (project, summary from `results.summary`, the test plan, the test environment = `run.environment`, the version/revision = `run.build`, start/finish from the first and last `ts`), and per item `testKey`, `status`, `comment` (the actual result, redacted), `start`/`finish`, `defects` (the item's `bug` or the new bug) and `evidence` (§8). Batches over 100 items go in chunks, the first creates the execution and the others import into its key (`testExecutionKey`), so the whole batch stays in one execution;
   3. `bug_status` transitions (`GET`/`POST /rest/api/3/issue/<key>/transitions`) and the optional `link_type` links (`POST /rest/api/3/issueLink`).
6. **Read back** (GraphQL `getTestExecution` → the test runs' statuses and defects) and compare with what was sent: a difference → exit 5.

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
- **Existing bug** (`bug` in the item): linked to the test run, plus a comment with the run's observation (`POST /rest/api/3/issue/<key>/comment`: batch, environment, build, status, actual result); with `bug_status`, the transition to that status (resolved_bugs rule, methodology §9.3). Nothing else of the bug is changed.
- Nothing is sent without the approval of the dry run (§2.3; methodology §9.4).

---

## 6. Importing cases (`import-cases`)

For the onboarding (methodology §4 step 7), `/plan-batch` and `/run-batch`:

1. JQL `cases.jql` (default `project = <project_key> AND issuetype = <issue_type>`), through `POST /rest/api/3/search/jql` with `nextPageToken` pagination (up to 100 issues per page), only the mapped fields.
2. Steps and test type through GraphQL `getTests(jql: …, limit: 100, start: …)`: each step's action, data and expected result; linked `Precondition` issues (their definition as text; `preconditions_from` keeps their keys, read only).
3. The latest status of each test in the test plan (`results.test_plan_key`, GraphQL `getTestPlan` → test statuses), mapped back to the project's vocabulary (a status mapped from two project statuses is imported as the first one and flagged).
4. Writes `<workspace>/tracker/cases.json` (internal model, methodology §2: `id`, `key`, `title`, `area`, `feature`, `preconditions`, `steps` [{`action`, `data`, `expected`}], `expected` — the last step's expected result —, `role`, `platform`, `priority`, `technique`, `origin`, `last_status`) and refreshes `ids.yaml`.

Flags, never silent fixes (methodology §11): a test without steps, a non-`Manual` test, a test without an internal ID (`field`/`label`), two tests with the same ID (exit 2), a case with several expected results (methodology §5.3).

---

## 7. Exporting new cases (`export-cases`, after `/generate-cases`)

- Input: the cases the user approved in `/generate-cases`, as `{"cases": [...]}` with the fields of the model (the same as `cases.json`) and a provisional `id` (`NEW-1`, …) when `id_source` is `key`, or the project's ID pattern otherwise.
- Resolution: an internal ID that already exists → exit 2; a test with the same summary in the same folder → a candidate duplicate, listed, not created without the user's decision (methodology §10: re-run with the case removed from the file).
- Creation, per case: GraphQL `createTest` (test type `Manual`, the steps, and the Jira fields: summary, mapped fields, the markers `qa-batch-<file name>`), then the test repository folder (`folder`) and, with `results.test_plan_key`, the test plan.
- The record is `tracker/sent/<cases file name>.json` (§2.5, same rules); `ids.yaml` gets `NEW-1 → DS-140`, and the skill tells the user the final IDs.

---

## 8. Evidence and redaction

- Comments, bug descriptions and the record are redacted (§2.7). A redaction that changed something is reported in the dry run (the user sees what the tracker will get).
- With `attach_evidence: true`, the files an item lists under `evidence` (only inside the workspace's `evidence/`) go as Xray evidence (base64 in the import) or as a bug attachment (`POST /rest/api/3/issue/<key>/attachments`, header `X-Atlassian-Token: no-check`). Text files are redacted; images and videos cannot be, so they go only with `evidence: "text+screenshot"` in `project.yaml` and are listed one by one in the dry run. A file over the size limit (default 10 MB) is refused (exit 4).

---

## 9. What is not done

- `/dashboard` stays Excel only: a tracker's own reports (Xray's test plan and execution reports) are the progress view; building the dashboard from `tracker/cases.json` and the records would show only what this assistant sent. Revisited only if a real project asks for it.
- No bulk edit of existing tests (steps, fields): `export-cases` only creates. A change to an existing case is proposed to the user and done in Jira.

---

## 10. Tests and golden (methodology §15)

### 10.1 Golden with recorded fictional responses

`tests/golden/jira/<scenario>/`, all **fictional** and written by hand (Demo Shop): `project.yaml`, a `.env` with fake values, `tracker/ids.yaml`, the batch or cases file, `responses.json` (the tracker's responses, in order, each with the request it answers: method and path) and `expected_requests.json` (the exact list of requests: method, URL, the relevant headers with `Authorization` redacted, and the body).

The test runs the command with `--yes` and `--now` through the fake transport of §2.7 and compares the requests made with `expected_requests.json` **exactly** (order included); then runs it again and checks there is **no request at all** (idempotency), and runs the dry run and checks there is no writing request. The expected list is never generated by the test (as the Excel golden: `technique-excel.md`, methodology §15); a `--print` option only shows the requests made, to help the person writing the fixture. Scenarios: results only; a new bug and an existing bug with `bug_status`; a conflict (unknown case: exit 2, no write); an interrupted send (a fault injected at request *n*: exit 3; then a resumed run with no duplicate creation, the marker found); `Question` in the batch (exit 4, no request).

It is a `unittest` (no LibreOffice, no network), so it runs in `pre-commit` and in the CI with the other unit tests.

### 10.2 Round trip

Import → export → import through the fake tracker: the cases of a fixture tracker are imported into `cases.json`, exported to an empty fake tracker, imported again, and the two `cases.json` must be equal (IDs apart, which the mapping relates). It covers the field mapping, the labels with prefix, ADF ↔ text and the steps.

### 10.3 Pilot and real recordings

The pilot (plan 10.5) runs on a free Jira Cloud site with an Xray trial, in a practice workspace outside this repository. `--record` saves each request and response, redacted, in `<workspace>/tracker/recordings/`: the base of a later **real golden** inside the workspace (methodology §15), never in qa-assistant.
