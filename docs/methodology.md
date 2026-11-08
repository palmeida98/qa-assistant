# Core Methodology

> **Layer 1 — Universal QA methodology**
> Version 2.15 · 2026-11-08 · Replaces the version used in Claude.ai Projects; in English since plan 8.11; tracker adapters for Jira (Xray) and Azure DevOps since plan phase 10; remote execution in the workspace's CI since plan phase 11; approved cases added to an Excel workbook through the adapter since plan 10.6; runs started from the local interface since plan phase 12; exploratory charters on the app of a mobile project since plan 12.6; read-only ticket analysis with a local memory since plan phase 14; finding the framework and selecting a project, for any skill, since plan phase 15; one writer at a time per workspace since plan 15.6; an analysis kept in the workspace since plan 15.7; a run log that checks its cases and keeps its run block, and a context kept short, since plan 15.8; eight skills with shared procedures (`skills-spec.md`) since 06.11.2026; a request to test that authorises its own scope, evidence by risk and one bug per problem since 08.11.2026; a project's `context.md` may be its main document, with permanent rules in preserved sections, since 08.11.2026 (v2.14).
>
> This document applies to **every** project and **every** company. It does not, and cannot, contain information specific to a project, client or company. The examples use a fictional project, the **Demo Shop**.

---

## 0. How to use this document

- Claude reads this document as a permanent reference (imported by the `CLAUDE.md` of the `qa-assistant` repository).
- What is here is **the method**: what is done, in what order and with what criteria.
- The **technical how** is in the test modules (layer 2), for example `technique-browser.md`.
- The **what of each project** is in the project's workspace (layer 4): `CLAUDE.md`, `project.yaml` and `context.md`.
- When a rule of this document refers to "the project's rule", the value is in `project.yaml`. If it is not defined, **ask** and do not assume.

---

## 1. Architecture

### 1.1 Layers

| Layer | Content | Where it lives |
|---|---|---|
| **Engine** | A central library of skills (`.claude/skills/`), adapters, scripts (`scripts/`, `tools/`), tests and guards; and the automated audit engine `qa-audit` (`src/qa_audit`, specified in `docs/qa-audit.md`), which lives next to this layer and does not replace it (decision of 25.09.2026, which revises the one of 24.09.2026 about a separate core) | Personal repository `qa-assistant` |
| **1. Methodology** | This document | `qa-assistant/docs/` |
| **2. Test modules** | Techniques per type of test (`technique-browser.md`, `technique-api.md`, `technique-accessibility.md`, `technique-mobile.md`; list in §4.1) | `qa-assistant/docs/` |
| **3. Adapters** | Conversion between the internal model and each company's tools: Excel (`adapters/excel/`, technique `technique-excel.md`), Jira with Xray and Azure DevOps Test Plans (`adapters/jira/`, `adapters/azure_devops/`, on the common base `adapters/tracker/`; techniques `technique-jira-xray.md` and `technique-azure-devops.md`; built in phase 10 of the plan — §4.2) | `qa-assistant/adapters/` |
| **4. Workspace** | Project context, environments, roles, workbook, runs, history, credentials | `qa-workspaces/<company>/<project>/` |

### 1.2 Privacy boundary

The line between layer 3 and layer 4 is a hard boundary.

- **Never** into the `qa-assistant` repository: names of clients or companies, environment URLs, account names, credentials, workbooks, results, bugs, screenshots or any example that could identify a real project.
- Credentials live **only** in the workspace's `.env` and are referred to by the variable's name (e.g. `SHOP_BO_ADMIN_USER`), never by the value.
- When extracting a heuristic or an example from a real project into the framework, **anonymise first** (see §17).
- The privacy guard (detect-secrets + denylist, which also reads the content of Office files and file names) blocks commits that break this rule. A block by the guard is never worked around: the content is fixed.

### 1.3 Hierarchy of sources of truth

When two sources conflict, the one with the lower number prevails:

1. Explicit and current requirements of the project (given by the user or the team in the session)
2. Project context (`CLAUDE.md` and `project.yaml` of the workspace)
3. Test case data (internal model / project workbook)
4. This methodology
5. Test modules (layer 2)
6. Versioned code, validated by the framework's tests
7. Historical artefacts (as a reference only)

If the conflict cannot be resolved objectively with this hierarchy, **stop and ask** before any change with consequences.

Claude's personal or global memory is not a source of truth for a project: it does not enter this hierarchy (`technique-workspaces.md` §7).

### 1.4 Generalisation rule

When a new requirement appears, decide where it belongs:

| The requirement applies to… | It goes to… |
|---|---|
| Every project, and it is about method | This document |
| Every project, and it is technical (automation, tool) | The corresponding test module |
| Several projects, but not all | A configurable component (an option in `project.yaml`, adapter or module), **never** a special case written into a shared layer |
| A single project | That project's workspace |

Always tell a **framework capability** apart from a **project's implementation detail**. A workaround that worked in one project is not universal until it is shown in another.

---

## 2. Data model

The internal model is independent of any company's format: it is the common vocabulary of the skills, the run logs and the adapters, not a separate database. Excel workbooks, Azure DevOps and Jira are input and output formats, each handled by its adapter (§4.2).

| Entity | Minimum fields |
|---|---|
| **Project** | id, name, environments, roles, platforms, active modules, tracker, bug format |
| **Feature** | id, area, sub-area, name, applicable roles, risk (probability × impact) |
| **Test case** | id, feature, title, preconditions, steps, expected result, role, platform, module, design technique, priority, origin (requirement / Figma / exploratory / bug) |
| **Batch** | id, grouping context (area, feature, platform, role, flow), ordered list of cases |
| **Run** | id, batch, environment, version/build tested, date, executor (agent/human) |
| **Result** | case, run, status, short note, linked bug, evidence (when applicable) |
| **Bug** | internal id, tracker id, area, feature, sub-area, platform, role, expected, observed, steps, status |
| **Heuristic** | anonymised pattern, type of screen/component, module, technique that detects it |

Possible statuses of a **Result**: `Pass`, `Fail`, `N/A`, `Blocked`, `Not run`. The provisional status `Question` can exist during the session, but can never reach a final report (see §8.5).

A case's ID is **stable**: it does not change when the case is edited, moved or exported. The adapters keep the mapping between the internal ID and each external tool's ID. With an external tracker, the mapping lives **in the tracker** (its own key, a field or a label, chosen per project), so it is the same for everyone; the workspace keeps only a cache of it (`tracker/ids.yaml`).

---

## 3. Work cycle

```
Onboarding → Analysis → Case design → Batch planning → Execution
    → Classification → Bug management → Validation → Checkpoint / Report
```

Each stage has a corresponding skill in Claude Code, which is also where a request in natural language goes:

| Stage or request | Skill | Result |
|---|---|---|
| Onboarding or resuming: set up a project, or "where were we?" | `/project-context` | The workspace selected, its state, decisions, blockers, tools verified and next actions |
| Ticket follow-up (at any time; read only) | `/analyze-ticket` | Acceptance criteria, decisions, dependencies, related bugs, what changed, memory and a verification prompt |
| Analysis, case design and batch planning | `/plan-tests` | Requirements apart from assumptions, existing cases reused, proposals without duplicates, batches with order and scope |
| Execution and classification (mobile, web, API, accessibility) | `/run-tests` | Observed results and evidence per platform and build |
| Bug management | `/manage-bugs` | Duplicate search and bug proposals for one batch review |
| Validation and report | `/qa-report` | Reconciled totals per platform and build, risks, a summary for the team; the Excel dashboard |
| Checkpoint: save progress or close a session | `/checkpoint` | State kept, pending application listed, application once authorised |
| The whole flow for one PBI, resumable | `/test-pbi` | Each step through the skills above, with a record of what is done |

Version 2 of the skills (2026-11-06; `skills-spec.md`, `skills-guide.md`) replaced the thirteen skills of version 1; the long procedures they held are in `procedures/`.

The user need not know these commands. Claude Code matches the request against the skills' descriptions, and each skill starts by finding the framework and the project (§4.3). Each skill's contract (intent, inputs, configuration, tools, outputs, writes, approvals, failures, resume) is in its own frontmatter; `python scripts/workspace_select.py skills` prints the catalogue from there. A skill existing does not prove its tools are available.

**Analysis is not execution.** "Analyse these tickets" gives an analysis and marks nothing as tested. "Test this feature" may go through planning and execution, respecting the batch planned for it and the project's rules.

**A request to test is its own authorisation** for the work inside its scope (the platforms, environment and targets it names): analysing, planning by risk, selecting existing cases, preparing local proposals, running, and recording run logs, evidence and local checkpoints, without approving the same plan or run twice. It never covers a write to a workbook or a tracker (cases, results, bugs, comments, attachments: each one shows its exact content and waits for its approval, §5.3, §9.4), another platform, environment or target, a commit, a push or a sync of the skills. A project whose rules ask for more (for example an approval of each platform's run) keeps them (§1.3 point 2); the defaults and that precedence are written once in `procedures/rules.md` §5 and §8.

---

## 4. Onboarding a project

1. Create the folder `qa-workspaces/<company>/<project>/`.
2. Run `/project-context`, which copies `context-template.md` and creates `project.yaml`.
3. Fill in **only** information specific to the project:
   - environments (URL and purpose; explicitly identify which one is the test environment);
   - roles/profiles and access differences;
   - names of the credential variables (the values go to `.env`);
   - reference documentation (requirements, specifications, Figma);
   - structure of the workbook or tracker;
   - required bug format;
   - rule for resolved bugs (`remove` or `archive`);
   - evidence policy (`text` or `text+screenshot`);
   - active test modules;
   - the project's exceptions and own rules.
4. Confirm that `.env` is in `.gitignore` and that `history/` is protected: the workspace's `.claude/settings.json` (copied from `qa-assistant/docs/workspace/settings.json`) forbids editing `history/**` (§13).
   The workspace is a **git repository of its own**, with a **private remote** (one repository per project, never one for several clients): `git init`, the `.gitignore` of `docs/workspace/` (`.env`, saved sessions, `dashboard.html`, app binaries) and a `.env.example` with the variable names only. The remote is created only with the user's approval, and every push too (§12.3). App binaries (APK/IPA) are not committed: `project.yaml` records where the build comes from and its SHA-256.
5. Check that no project information was written into the universal layers.
6. Create the initial `context.md` (see §12.2).
7. Import the existing cases with the project's adapter and validate the round-trip (import → export → compare). With the Excel adapter, which reads and writes the workbook itself (there is no import into a separate model), this is `ExcelAdapter.from_project(...).load()` without errors and `cross_check()` showing only problems that were already in the workbook; `python scripts/validate_project.py <workspace>` does both. With Jira or Azure DevOps, `python -m adapters.tracker <workspace> check` (configuration against the real tracker, read only) and then `import-cases` (read only, into `tracker/cases.json`), with the flags it raises shown to the user; the round trip is covered by the adapter's golden (§15), so nothing is written to the tracker at the onboarding.

A workspace created before the translation (plan 8.11) is converted once with `python scripts/migrate_workspace.py <workspace>` (dry run by default, `--apply` to write).

### 4.1 Test modules

`modules:` in `project.yaml` only accepts the values of this closed list. Each active module needs its section filled in `project.yaml` (`scripts/validate_project.py` checks it):

| Module | Technique | Skill | Section in `project.yaml` |
|---|---|---|---|
| `functional-web` | `technique-browser.md` | `/run-tests` | `environments`, `roles` |
| `exploratory` | `technique-browser.md` (charters, methodology §5.2); in a project with `mobile` and without `functional-web`, the charters run on the app: `technique-mobile.md` §4.2, `/run-tests` | `/run-tests` (`/run-tests` on the app) | — |
| `api` | `technique-api.md` | `/run-tests` | `api:` |
| `accessibility` | `technique-accessibility.md` | `/run-tests` | `accessibility:` |
| `mobile` | `technique-mobile.md` | `/run-tests` | `mobile:` |

Other variants mentioned in the techniques (smoke, role matrix, visual, cross-browser, forms) are done inside these modules, as design techniques or types of batch, and are not values of `modules:`. A new module first enters this table, with a technique and a skill (process of §15).

### 4.2 Trackers

`tracker:` in `project.yaml` accepts `excel`, `jira` (with Xray) and `azure-devops` (Test Plans + Boards). Each has an adapter with the same four directions — results, bugs, importing cases, exporting new cases — and the same rules: everything validated and resolved before the first write, nothing sent without the user's approval (§9.4), a batch never sent twice without being detected:

| Tracker | Results | Bugs | Cases | Dashboard |
|---|---|---|---|---|
| `excel` | applied by the adapter at `/checkpoint` (`python -m adapters.excel.apply`) | `new_defect` / `bug` in the run log, applied with the results | the workbook itself; the new cases approved in `/plan-tests` added by `python -m adapters.excel.add_cases` | `/qa-report` |
| `jira`, `azure-devops` | sent by the adapter at `/checkpoint` (`python -m adapters.tracker <workspace> apply`): a dry run first, `--yes` only after the user approves what it showed; one Test Execution (Xray) / test run (Azure DevOps) per batch | the same `new_defect` / `bug` in the run log, created or updated with the results; duplicates searched with `bug-search` (§9.1) | `import-cases` (read only) and `export-cases` (the new cases approved in `/plan-tests`) | none (the tracker's own reports) |

The configuration is the tracker's section of `project.yaml` (`excel:`, `jira:`, `azure_devops:`), with credentials only as names of `.env` variables; the technique of each adapter (`technique-excel.md`, `technique-jira-xray.md`, `technique-azure-devops.md`) has the details. Version 1 of the Jira and Azure DevOps adapters targets the **cloud** services (Jira Cloud + Xray Cloud, Azure DevOps Services); Server / Data Center stays out until a real project needs it.

The Jira and Azure DevOps adapters were built in phase 10 of the plan (10.2 and 10.3); `scripts/validate_project.py` requires the tracker's section. When the adapter cannot be used (e.g. the tracker's API is out of reach), the run log in `runs/` stays the source and the user (or the assistant, with approval) passes the results and bugs to the tracker by hand. An adapter for any other tracker is only written when a real project needs it, with the golden and the tests of the existing adapters as a model (§15).

### 4.3 Selecting a project

A project is selected before a skill acts (`technique-workspaces.md`): an explicit path, then a name, then the current directory, then the configured root of workspaces. Both `<root>/<company>/<project>/` and `<root>/<project>/` are supported, and the root can have any name.

- **Nothing is chosen silently between candidates:** ambiguity is a short question.
- **A folder found is not a project ready:** each one is `valid`, `incomplete`, `not-configured` or `inaccessible`, and `/project-context` is proposed only when needed.
- **Listing does not authorise reading:** only the selected project's data is loaded, and listing reads no ticket, evidence or secret.
- **Writes stay inside the selected workspace.** A path that leaves it, through `..` or a link, is refused.
- **The framework is found explicitly and checked against the skill**, in a main checkout or a worktree. A mismatch stops with an instruction: nothing is installed silently, and a wrongly resolved path never creates an empty root.

Before executing, the project, task, environment and relevant tools are shown. The agent only asks when there is ambiguity or an authorisation is missing.

Information already in the workspace **is not asked for nor researched again**, unless there is evidence that it is out of date or inconsistent. If information needed to continue is missing, identify exactly what is missing and ask.

---

## 5. Analysis and case design

### 5.1 Analysis

For each feature, identify:

- the roles that reach it and what each one can or cannot do;
- the inputs, the states, the business rules and the integrations;
- the risk: probability of failure × impact for the user or for the business;
- the specification gaps. These **are not filled with assumptions**: they are recorded as a question or as a "missing specification" note.

The analysis is kept in the workspace, in `analysis/<feature>_<YYYYMMDD>.md`, with its date, its sources and its status, so another session continues from it. A new version is a new file. What it infers stays under *Proposed*: neither saving it nor the user reviewing it makes it a requirement (§1.3).

A feature often starts as a ticket. `/analyze-ticket` (`technique-tickets.md`) reads Azure DevOps or Jira, says what changed since the last check and keeps the result in the workspace's `memory/`. It separates the ticket's **official state** from the **QA situation**, an analytical conclusion that is neither a status of §2 nor a change in the tracker: a closed ticket proves neither that it was tested nor that the fix is in the available build. It also separates confirmed facts, unverified statements, inferences and questions; a comment does not become a requirement by being read (§1.3). It marks no test as run and writes nothing to the tracker.

### 5.2 Design techniques

Each case generated gives the technique it came from:

| Technique | When to use |
|---|---|
| Equivalence partitioning | Fields and inputs with classes of valid and invalid values |
| Boundary values | Numeric, size, date or quantity limits |
| Decision table | Several business conditions combined |
| State transition | Entities with a life cycle (order, request, account) |
| Pairwise | Many independent parameters combined |
| Role matrix | Each action × each profile |
| Error guessing | Accumulated heuristics (§17) |
| Exploratory (charter) | New areas, poorly specified or high risk |

### 5.3 Rules

- A case has **one** verifiable expected result. If it has several, it is split.
- The expected result comes from the specification, not from the application's current behaviour.
- The cases generated by the assistant are **proposals**. They only enter the library after the user approves them.
- Approved cases enter through the project's adapter, never by hand: `add-cases` in an Excel workbook (`technique-excel.md` §4.1), `export-cases` in Jira or Azure DevOps (`technique-jira-xray.md` §7).
- A case's priority derives from the feature's risk, unless the project says otherwise.

---

## 6. Batch planning

Cases are **not** necessarily run in the order in which they appear in the document.

Group by **area + feature + platform + role + flow**, and within each group:

- run one after the other the cases that share a login, environment or application state;
- complete every possible case of a context before switching to another;
- run first the flows that create state other cases can reuse;
- order the groups by risk when the time available does not cover everything.

**Goal:** minimise repeated logins, redundant navigation, environment changes, context switches and repeated processing.

The optimisation can **never** reduce coverage nor change a case's logic.

---

## 7. Execution and recording

### 7.1 Run log

During the session, the results are written to a run log in `runs/` (one file per batch, e.g. `runs/batch_20260922_01.json`), and not directly to the workbook or the tracker. The file is `{"results": [ … ]}` and each result contains, at least:

```json
{"tc": "CHK-013", "status": "Fail", "actual_result": "expired coupon accepted", "bug": "BUG-41", "ts": "2026-09-22T14:03:00"}
```

- `status` uses the project's vocabulary; `actual_result` is what was observed (not the expected result copied); `bug` is an **existing** bug linked to the case.
- The run's context goes at the top level, `"run": {"environment": "<key of environments>", "build": "<version>"}` (the **Run** of §2); the Jira and Azure DevOps adapters require it, the Excel adapter ignores it.
- How the batch is executed goes at the top level too, `"mode": "<kind>: <why>"` (kind `conversational`, `flows`, `assisted` or `manual`), written with `run_log.py mode` or `add --mode` before the first result and fixed afterwards; a run log without it (older ones) is valid, and a mode is never added to it later by guess.
- A **new** bug goes as `new_defect` (fields in the tracker's technique, e.g. `technique-excel.md` §4), only after the user approves it (§9).
- **A result is written only for a case of the project's library** (the workbook, or `tracker/cases.json`): an unknown ID is refused with the closest known ones, and a library that cannot be read is an error, never a silent acceptance.
- **The run block is immutable once the batch has results**: another build or environment is refused. A build that changes during a batch ends that batch; a new one is planned with the right context (`technique-mobile.md` §1.4).
- **Evidence or a bug added after the execution keeps the result**: `run_log.py attach` adds them to the item and keeps its status, observed text, `ts`, earlier evidence and the run block; `add --replace` is only for a new execution of the case.
- **A mistaken result is removed, not edited over**: `run_log.py remove` shows the result, keeps the others, never deletes evidence and refuses a batch already applied.
- Cases in `Question` can stay in the log during the session, but are reclassified before the batch goes to the workbook or tracker (§8.5): the adapter refuses a batch with statuses outside the project's vocabulary.
- **Time of the execution and time of the record.** `ts` is the time of the execution; `recorded_at` is when the item was written. Without more, `run_log.py add` records an execution happening now (`ts` = `recorded_at`). An observation made earlier is recorded with `--executed-at` and a `--executed-source` that says where its time comes from; a time that is not known is `--executed-at unknown` (no `ts`, `"ts_unknown": true`: listed by the audit, ordered as the oldest, never made current by the clock). A date without a time, an invalid or a future time is refused; a time without a zone stays without one (like every `ts` already recorded) and a zone that is given is kept as written, never assigned. Results are ordered by `ts`, so a late record never makes an old execution look newer; older run logs, with `ts` only, keep their reading.
- **A Question is resolved by a decision, not by a new execution.** `run_log.py resolve` keeps the observation (text, `ts`, evidence) and records the new status with `resolution` {reason, who decided, when, the original}. In a batch not yet applied the item is changed in place; a batch already applied is never touched: the decision goes to a new batch with `"revision_of": {"batch", "tc"}` (same observed text, `ts`, evidence and run block), applied with its own dry run and approval. A later execution of the case is current over the resolution; a Question without an answer stays pending in the audit. The record of a decision does not replace an approval the project asks for.
- **Execution, application and later decision are three things.** `ts` is the time of the execution, never the time it was recorded or applied. An item that only records an earlier execution in the workbook, perhaps under another status decided later (a Question recorded as Blocked), declares `"applies": "<batch of the execution>"` and has no `ts` of its own; the reports keep the execution as the current result and show the applied status apart (`qa_report.py totals`). A correction of an execution is a new result with its own `ts` and `revision_of`. A time that no source gives is never filled in. A result whose run block has no build has an **unknown** build: it is never attributed to the current build nor counted in a build's coverage.

### 7.2 Checkpoints

The results go to the model and to the main files at **checkpoints**:

- every 15–20 cases;
- before any operation that could lose work;
- at the end of the session.

Each checkpoint keeps the statuses, the results, the new or updated bugs and the exact point where the execution is.

### 7.3 Evidence

| Result | Evidence |
|---|---|
| Pass | What lets the result be verified (the record, a log, a response or a capture, by the risk) |
| Fail | Enough to reproduce: URL, role, steps done, data used, expected → observed, relevant messages |
| N/A | Short justification |
| Blocked | Reason and the dependency that blocks: the diagnosis that was reachable and the condition missing |

The evidence is **proportional to the risk**, and the project's policy (`evidence` in `project.yaml`, its `CLAUDE.md`) is the minimum. A Fail, a Blocked or an intermittent result is reinforced with what shows it (a capture, a video, a log, an API response) when the text alone cannot; a capture of the app is not required when it does not show a block. One artefact may serve several cases when each case names its own part (a timestamp, an interval, lines). Every file named exists. Do not produce redundant evidence, nor a video of every case by default. A failure of the tool, a block and inconclusive evidence are kept apart from a defect of the application (§8).

---

## 8. Decision rules

### 8.1 Pass

The observed behaviour matches the expected result defined in the case.

### 8.2 Fail

There is an **objective** difference **expected → observed**.

It is not Fail just because:

- the behaviour looks strange;
- there is a different preference;
- the implementation differs from what the tester imagined;
- there is a possible unspecified improvement;
- there is a suspicion without enough evidence.

These situations are recorded as an **observation** or **suggestion**, outside the results.

### 8.3 N/A

The case cannot legitimately be run in the current context: feature not available for the role, not available in the environment, platform not applicable, or a precondition impossible by definition.

N/A **never** serves to hide a functional problem.

### 8.4 Blocked

The case should be run, but cannot go on because of: environment unavailable, external dependency, blocking bug, test data missing, or a technical limitation of the tool.

Blocked is **not** converted automatically into Fail nor N/A.

### 8.5 Question

If there is a doubt about the requirement or about the expected result, **do not assume**:

1. mark the case as `Question` in the run log, with the concrete question;
2. continue with the cases that do not depend on that answer;
3. present all the questions to the user at once, at the next checkpoint;
4. reclassify based on the answer.

A final report never contains cases in `Question`.

### 8.6 Summary

| Situation | Status |
|---|---|
| Observed = expected | Pass |
| Observed ≠ expected, with evidence | Fail |
| Does not apply to this context | N/A |
| Should run but cannot | Blocked |
| It is not known what is expected | Question → ask |

---

## 9. Bug management

### 9.1 Before creating

1. Look for related existing bugs.
2. Compare, at least: area, feature, sub-area, observed behaviour, platform and role (when relevant).
3. If the problem already exists, **update the existing bug**.
4. Only create a new bug when there is evidence that it is a distinct problem.
5. When it is not clear whether it is a duplicate, propose it to the user and let them decide.
6. **One bug per problem.** The same defect on several platforms (Android, iOS, web) goes in one bug only when the evidence shows it is the same (the same steps, screen or flow and result): it then holds the environment, device or browser, OS, build, steps, observed result and evidence of each platform. Similar symptoms are not enough; a relevant difference makes separate bugs, related when a link is known; a link not shown stays uncertain. A platform not tested is never said to be affected or validated. A reproducible defect needs no root cause: say when it is unknown.

### 9.2 Minimum content

- title in the format defined by the project;
- environment and version/build;
- role and platform;
- preconditions and steps to reproduce;
- **expected → observed**;
- linked test cases;
- severity **only when asked for**, and the priority the project's schema requires (Excel: `excel.defects.priorities`) **decided by the user** at the bug review: the assistant lists the accepted values, leaves the bug pending until one is given and never fills it in (a suggestion, with its reason, only when asked and as a proposal for approval). `run_log.py defect-rule` says whether it is required; an approved bug without a valid one is refused before the dry run, and the adapter keeps its own check. The decision is the user's.

The bug's structure always follows the project's format. It is not changed without need.

### 9.3 Retest

| Result of the retest | Action |
|---|---|
| Still reproduces | Keep and update the bug |
| Resolved | Apply the project's rule (`remove` from the active list or `archive`) |
| Could not be validated | Keep the current status and record the reason |

A retest changes the **bug**. It changes a **case** only when that case's own expected result was checked again: a case whose expected result passes stays `Pass` even when a bug linked to it is kept open.

### 9.4 Sending to the tracker

The assistant prepares the bug. Sending it to the external tracker only happens **after the user approves it**. The tracker adapters make this explicit: without `--yes` they only show what they would send (a dry run); the `--yes` run happens only after the user approved that list.

In a **remote run** (§12.4) nobody is there to approve during the session, so the approval moves to the **pull request**: the agent writes every new bug as a `new_defect` with `"proposed": true`; the user reviews the run log in the pull request and edits it before the merge (removes a proposed bug, changes it, links an existing bug instead, or drops `"proposed"` to approve it) and answers each `Question`. The merge is the approval: only then is the run log applied (by the workspace's apply workflow, with `--yes` for Jira / Azure DevOps). A run log sent by the runner never has a `new_defect` applied before that, and every adapter refuses a `new_defect` that still has `"proposed"`.

A run started from a **local interface** (an earlier one, removed on 08.11.2026; the rule stays for any unattended run) is unattended in the same way: the agent runs headless on the user's computer, writes each new bug as a `new_defect` with `"proposed": true` and each doubt as `Question`, and applies nothing. The approval happens in the interface, which records it in the run log (the same edits as in the pull request); only a run log with nothing proposed and no `Question` left is applied, and only after the user confirms it.

---

## 10. Duplicate cases

If the same case appears more than once:

- identify the duplicate;
- do not run it again without need: reuse the result when the context is equivalent;
- flag the duplication for the documentation to be cleaned up.

If it appears in different contexts (another role, platform or environment), confirm whether the context really changes the test before treating it as a duplicate.

---

## 11. Consistency between documents and outputs

When there are several documents, sheets, reports or tools:

- compare the relevant information between them;
- identify inconsistencies;
- determine the source of truth (§1.3);
- update the others so they become consistent.

Never fix silently when there is ambiguity about which version is right.

Check especially: case IDs, feature names, statuses, results, bugs, descriptions, roles/permissions, environments, metrics, totals, formulas and cross-references.

---

## 12. Checkpoints, continuity and recovery

### 12.1 Rules

- The execution must survive interruptions.
- When resuming: read `context.md`, the latest run log in `runs/` and, for tickets, `memory/` first, identify what was already run and continue from the last valid point.
- Do not repeat work without need. Do not rebuild the progress from scratch.
- Two sessions never write the same workspace at the same time: the second one waits, then says so and writes nothing (`technique-workspaces.md` §7).
- The workspace's git serves as a restore point. A checkpoint saves the state on disk and lists what is not committed or not pushed; it commits only when the user or the workspace's `CLAUDE.md` asks for it, and the push to the private remote happens only when the user says so, never with `--force`.
- A run can also happen in the **workspace's CI** (§12.4): it starts from the pushed state of the private remote and comes back as a pull request; its run log is applied only after the merge.

### 12.2 Structure of `context.md`

```markdown
# Session context — <project>
Updated: <date and time>

## Current state
## Execution point (last batch, last case)
## Work done
## Decisions made (and who made them)
## Open questions
## Known problems / blockers
## Pending work
## Validation state
```

`context.md` is the session's **temporary** state by default. It does not replace the project's `CLAUDE.md` nor `project.yaml`. A decision that becomes a permanent rule moves up to one of those files.

A project may choose otherwise, in its own `CLAUDE.md`: **`context.md` as the main document, imported by `CLAUDE.md`** (`@context.md`), with the project's permanent rules in clearly headed sections of it (for example `## Permanent rules`). The choice is the project's and is written once, in its `CLAUDE.md`; there is never a second competing copy of the rules, and the framework copies none of it. Those sections are never compacted or archived: `compact_context.py` reaches only the dated entries of `Current state`, `Execution point` and `Work done`, and a resume reads the whole imported document before acting. A rule is still changed only by the user.

It stays **short**: the newest entries of the current state and of the execution point, the decisions still valid, the open questions, the blockers and the next steps, each pointing to the detailed record (run log, evidence, memory, `analysis/`). Older entries move to `archive/context/` word for word (`scripts/compact_context.py`, a dry run first, a recoverable copy of the whole file before the change); `history/` and the run logs are never touched, and a decision is never dropped by the tool: only dated entries older than the newest few are moved. A new session finds the last batch, what is pending and where the rest is without reading the older ones.

`/analyze-ticket` keeps one generated block in `context.md`, between its markers, with the resume point of each scope; every other byte of the file stays. The references it compares against live in `memory/tickets/`, and the dated records of its checks in `memory/checks/`. A failed or partial read never replaces a complete reference, and analysing an older check never rewinds the current state (`technique-tickets.md` §5).

### 12.3 Resuming on another computer

A project must be continuable from any computer with access to its private remote:

1. Install the tools and `qa-assistant` ([`workspace/setup.md`](workspace/setup.md); clone, `pip install -r requirements.txt` and `pip install -e .`, `python scripts/sync_skills.py --apply`).
2. Clone the workspace into `qa-workspaces/<company>/<project>/`.
3. Create `.env` from `.env.example`, with the values from the user's own secret store (never from the repository, a chat or a log).
4. `python scripts/validate_project.py <workspace> --environment`: the tools the active modules need are present; what is missing is installed before testing (e.g. `scripts/setup_emulator.py` for `mobile`).
5. Read `context.md` and continue from the last checkpoint (§12.1).

Anything a run needs and that is not in the repository (a build, a device, a login link) is written in `context.md` or `project.yaml`, with where to get it.

Step by step, with what each module needs: [`docs/workspace/resume.md`](workspace/resume.md).

### 12.4 Resuming on a runner (remote execution)

A batch can also run on a **GitHub Actions runner** started from the workspace's own private repository (the `mobile` module; `technique-mobile.md` §11). It is "resuming on another computer" done by a workflow:

1. The runner checks out the workspace (the **pushed** state: what is not pushed does not exist there) and `qa-assistant`, installs the tools, and writes `.env` from the repository's Actions secrets (the names in `ci.secrets` of `project.yaml`); `.env` is never committed.
2. `validate_project.py --environment --strict`: a missing tool stops the run before anything is tested.
3. The build comes from a release of the workspace's repository (checked against `mobile.build.sha256`); a login that needs a person (a magic link) is asked for in an issue and pasted there by the repository's owner (`ci.login`).
4. The batch runs (Maestro flows, and/or Claude Code headless in conversational mode) within `ci.timeout_minutes` and `ci.max_turns`.
5. The run log, the evidence and `context.md` go to a branch `run/<batch>-<run id>` and a **pull request**. Its review is the checkpoint's approval (§9.4): nothing reaches the workbook or the tracker before the merge.

The runner starts clean every time (no app state, no login kept) and uses only free resources: GitHub-hosted standard runners within the plan's free minutes, with the spending limit at 0, and the user's Claude subscription (never a paid API key). Step by step: [`docs/workspace/ci/README.md`](workspace/ci/README.md).

---

## 13. Historical artefacts

Finished batches, reports of past runs and archived exports are **immutable**, in every project:

- they live in `history/`, protected by Claude Code permissions: the rule `Edit(/history/**)` in `deny` in the workspace's `.claude/settings.json` (the leading `/` anchors it to the workspace's folder). An `Edit` rule covers every tool that edits files and the Bash commands Claude Code recognises (e.g. `sed`, `tee`, `>` redirections); it does **not** cover a script that opens the files on its own, so no framework script writes to `history/`;
- they are never changed, regenerated or overwritten;
- they serve only as a reference and as a validation base (§15).

They are only changed with an explicit instruction from the user for that specific artefact. Moving the folder unchanged (as `scripts/migrate_workspace.py` does with the old `historico/`, checked byte for byte) is not a change.

---

## 14. Tools and execution environments

### 14.1 Reuse first

Before creating a new script or tool:

1. look in the existing skills, adapters and scripts (`CLAUDE.md` of `qa-assistant`, Scripts section);
2. read their documentation;
3. understand the current flow;
4. reuse or extend;
5. only create something new when what exists does not serve, and explain why.

A tool that works is not replaced without a reason.

### 14.2 Identify the environment

At the start of each session, confirm and, if there are differences, record:

- which tools are available (Claude Code, connected MCPs, browser, Node.js, LibreOffice, Maestro);
- where the files are (repository, workspace);
- which scripts and skills can be used;
- the environment's limitations.

Do not assume that a tool available in one environment exists in another. `python scripts/workspace_select.py where` shows which framework, Python and imports really run, and each skill's preflight shows the tools it needs (`technique-workspaces.md`).

A **CI runner** (§12.4) is an environment of its own: a clean Linux machine for each run, with only the tools the workflow installs; no screen (the evidence is the only view the user gets); no person during the run (questions and approvals wait for the pull request); a time limit and a turn limit from `project.yaml`; the secrets only as Actions secrets. What it cannot do is Blocked, never assumed: a hardware feature the emulator does not have, a login nobody pasted in time.

---

## 15. Changes to the framework

Any change to the adapters, scripts, skills or universal layers follows **validation before adoption**:

1. Identify the current behaviour.
2. Change the specification (Markdown) first, then the code.
3. Run the framework's tests, in particular:
   - **golden test**: a real historical batch (read from the workspace through `QA_GOLDEN_DIR`) must produce an output identical to the one stored;
     - **fictional** golden of the Excel adapter (`python scripts/golden_check.py`: fixture, batch and expected result in `tests/golden/`, written by hand), which runs in `pre-commit` and in the CI;
     - **real** golden (`python scripts/golden_check.py --dir <folder>`, or `QA_GOLDEN_DIR=<folder>`; runs after the fictional one, which is never skipped): a folder **inside the workspace** (e.g. `<workspace>/golden/<batch>/`) with `before.xlsx` (copy of the workbook before the batch), the historical `batch_*.json` with its original name, `after.xlsx` (the workbook after the checkpoint, reviewed and accepted at the time) and `golden.yaml` (`now`: the instant of the original application, the `applied_at` of the control sheet; optional `label` and `reported_by`, **only** when `/checkpoint` used values other than the defaults: `label` is the text the adapter writes in the notes, not a description of the golden, which goes in a comment). The script applies the batch to a copy of `before.xlsx` through the same `apply` as `/checkpoint`, with the **current** configuration of `project.yaml`, compares every cell (formula and calculated value) with `after.xlsx` and confirms that applying again changes nothing. A folder with several subfolders runs them all. The expected result is never generated by the script. It runs only locally (real data never leaves the workspace). A golden from before the translation (plan 8.11) keeps its `lote_*.json` with the old keys, converted in memory with its original identity;
   - **round-trip test**: import → export without loss (with the Excel adapter, which edits the workbook itself, the golden covers it: the cases outside the batch stay the same and applying the same batch again changes nothing);
   - **unit tests** (`python -m unittest discover -s tests`), which `pre-commit` and the CI also run.
4. Investigate every relevant difference.
5. Confirm that each difference is intentional.
6. Only then adopt (commit).

A change is not "safe" just because it looks logically correct.

---

## 16. Quality of the outputs and end of session

### 16.1 Criteria

Every output must be consistent with the real results, traceable to the test cases, without duplications, organised and easy to continue in a future session.

### 16.2 End of session (`/qa-report`)

1. **Save:** last checkpoint, with every result kept.
2. **Bugs:** new ones added, existing ones updated, resolved ones handled by the project's rule.
3. **Cross-check:** cases ↔ results ↔ bugs ↔ reports ↔ totals.
4. **Questions:** no case in `Question` that was not presented to the user.
5. **Summary** (format below).
6. **Context:** `context.md` updated.

Do not declare a run finished while there are known inconsistencies that could change how the results are read.

### 16.3 Session summary

```markdown
## Summary — <project> — <date>
Environment: <environment> · Build: <version>

| Pass | Fail | N/A | Blocked | Total run | Not run |
|------|------|-----|---------|-----------|---------|

"Run" is every case outside `Not run`: **Total run = Pass + Fail + N/A + Blocked**, and the % run divides by the total number of cases. An `N/A` counts as run (it was analysed and classified), as does a `Blocked`. The same definition holds for the dashboard and for any summary of the workbook.

New bugs: …
Updated bugs: …
Resolved/removed bugs: …
Blockers: …
Inconsistencies found: …
Open questions: …
Next steps: …
```

---

## 17. Heuristics

Each relevant bug can produce a heuristic reusable in other projects.

- Keep it in `qa-assistant/docs/heuristics.md` **only after anonymising**: no names, URLs, data or texts of the client. The `pre-commit` denylist is the last barrier, not the first.
- Format: *component/type of screen → failure pattern → how to test*.
- Example: *IBAN field → accepts spaces when typing but fails validation on submit → test with and without spaces, with lower case and with the country prefix*.

The heuristics feed the *error guessing* technique (§5.2).

---

## 18. General rule

The goal is not to run the largest possible number of cases. It is to **maximise coverage and quality while minimising effort, time, processing and repeated work**.

Priority: **Reuse → Organisation → Consistency → Coverage → Efficiency**, without ever sacrificing the validity of the results.

When a doubt could significantly change the results of the tests, **stop and ask for clarification**. When it does not block, continue with the safest option and record the assumption.

---

## Migration notes

### v2.14 → v2.15 (traceability of applications and results, 08.11.2026)
- **Changed:** the Excel preview token covers the options that change what is written and has a version (`v2-`): previews pending at the upgrade are repeated (`technique-excel.md` §1.1); `ts` stays the time of the execution and a record time `recorded_at` is added, with `--executed-at` for an earlier observation (§7.1); a Question is resolved by a recorded decision that keeps the observation (§7.1, `procedures/apply.md`); the priority of a new bug is asked of the user when the schema requires it (§9.2); `scripts/qa_python.py` finds the interpreter (`procedures/rules.md` §11).
- **Kept:** every approval, the statuses, run logs and applied batches as they are (no migration of history), `history/` immutable.

### v2.13 → v2.14 (context as the main document, 08.11.2026)
- **Changed:** a project may import `context.md` from its `CLAUDE.md` as its main document, with the permanent rules in headed sections that compaction never touches (§12.2); the default (rules in `CLAUDE.md`) is unchanged.
- **Kept:** every approval, the statuses, `history/` immutable, one place for each rule.

### v2.12 → v2.13 (skills configuration, 08.11.2026)
- **Changed:** a request to test authorises the work inside its scope (analysis, a plan by risk, the run, the local records) without approving the same plan or run twice (§3); the defaults and the precedence of a project's own rules are in `procedures/rules.md` §5 and §8. Writes to a workbook or a tracker keep their approval.
- **Changed:** the evidence is proportional to the risk on top of the project's policy, and one artefact may serve several cases (§7.3); one bug per problem, grouping platforms only on evidence (§9.1).
- **New:** `run_log.py attach` adds evidence or a bug to a recorded result and keeps the rest of it (§7.1).
- **Kept:** every approval of a write to a workbook or a tracker, the statuses, the run block, the unattended runs (§9.4); a project's stricter rules.

### v2.11 → v2.12 (skills version 2, 06.11.2026)
- **Changed:** the thirteen skills became eight (`skills-spec.md`, §3): `/project-context` (onboarding and resuming), `/analyze-ticket`, `/plan-tests` (analysis, case design and batches), `/run-tests` (mobile, web, API, accessibility, by a route per module), `/manage-bugs`, `/qa-report` (totals per platform and build, and the dashboard), `/checkpoint` and `/test-pbi` (the flow for one PBI, resumable). The long procedures moved word for word to `procedures/`; the rules every skill shares are in `procedures/rules.md`. The names map in `skills-guide.md` §6.
- **New:** `scripts/qa_report.py` (reconciled totals, audit of run logs), `scripts/pbi_state.py` (the record of `/test-pbi`), `report.platforms` in `project.yaml`, `requires-docs` and `routes` in a skill's contract.
- **Changed:** `/checkpoint` no longer commits on its own: a commit only when the user or the workspace's `CLAUDE.md` asks for it (§12.1).
- **Kept:** every approval, the run log as the source, the statuses, `history/` immutable, the ticket memory and its markers in `context.md`.

### v2.10 → v2.11 (plan 15.8, 02.11.2026)

- **New:** a result only for a case of the library, removing a mistaken result, an immutable run block (§7.1); a context kept short (§12.2); the roles of a build and one check of the target (`technique-mobile.md` §1.3 and §1.4).
- **Kept:** the run log as the source, the approvals, `history/` immutable.

### v2.9 → v2.10 (plan 15.7, 01.11.2026)

- **New:** the analysis of a feature is kept in `analysis/` (§5.1) and `/generate-cases` starts from it.
- **Kept:** cases are proposals until approved; a gap is a question.

### v2.8 → v2.9 (plan 15.6, 01.11.2026)

- **New:** one writer at a time per workspace (§12.1): the ticket memory, the run log and the interface's edits take a short lock, so two sessions never overwrite each other.
- **Kept:** everything else; a lock left by a crash is taken over, so no project is ever blocked by it.

### v2.7 → v2.8 (plan phase 15, 01.11.2026)

- **New:** finding the framework and selecting a project for any skill (§3, §4.3, `technique-workspaces.md`); each skill's contract in its frontmatter; the two layouts of a workspace; the statuses of a project.
- **Changed:** a root of workspaces that does not exist is an error, no longer created; the root can be configured.
- **Kept:** the names of the skills, the closed list of modules, the approvals of §9.4, `history/` immutable, one engine: nothing is copied into a project.

### v2.6 → v2.7 (plan phase 14, 01.11.2026)

- **New:** `/analyze-tickets` and `memory/` in the workspace (§3, §5.1, §12.2): a read-only follow-up of tickets, independent of `tracker:`, with a deterministic comparison between checks.
- **Kept:** nothing reaches a tracker without the user's approval (§9.4), the statuses of §2, `/checkpoint` for the commits, `history/` immutable.

### v2.5 → v2.6 (plan 12.6, 17.10.2026)

- **New:** in a project with `mobile` and without `functional-web`, the `exploratory` charters run on the app with the mobile technique and `/test-mobile` (§4.1); before, they could only use the browser.
- **Kept:** the closed list of modules, a charter judged by its expected result, the run log as the source.

### v2.4 → v2.5 (plan phase 12, 08.10.2026)

- **New:** a run can be started from the local interface (since removed, 08.11.2026); it is unattended like a remote run, and the approval of new bugs and the answers to `Question` happen in the interface, as edits of the run log (§9.4).
- **Kept:** the run log as the source, approval before any bug reaches the workbook or a tracker, the adapters' refusal of a `new_defect` with `"proposed"`, and `/checkpoint` for `context.md` and the commit.

### v2.3 → v2.4 (plan 10.6, 04.10.2026)

- **New:** approved cases enter through the project's adapter, never by hand (§5.3); with Excel, `add-cases` (§4.2, `technique-excel.md` §4.1).
- **Kept:** cases are proposals until the user approves them; IDs are stable and in the project's pattern.

### v2.2 → v2.3 (plan phase 11, 29.09.2026)

- **New:** remote execution in the workspace's CI (§12.1, §12.4); the runner as an environment of its own (§14.2); in a remote run the approval of new bugs and the answers to `Question` happen in the pull request, and a `new_defect` with `"proposed": true` is never applied (§9.4).
- **Kept:** the run log as the source, approval before any bug reaches the workbook or a tracker, push only with the user's approval (the runner pushes only its own `run/…` branch; the merge is the user's).

### v2.1 → v2.2 (plan phase 10, 28.09.2026)

- **Changed:** Jira (with Xray) and Azure DevOps (Test Plans + Boards) get adapters (§1.1, §4.2), with the four directions of the Excel one plus importing and exporting cases; this revises the 24.09.2026 decision (plan 7.7) that kept them as manual logging. Manual logging stays until each adapter is done.
- **New:** the case ID mapping lives in the external tracker, with a cache in the workspace (§2); the run log's optional `run` block (environment and build, §7.1); the dry run before sending to a tracker (§9.4).
- **Kept:** the run log as the source during the session, checkpoints, `Question` refused before sending, approval before any bug reaches a tracker.

### v2.0 → v2.1 (plan 8.11, 26.09.2026)

- **Changed:** everything in English, the names included: this file (`methodology.md`, before `metodologia-core.md`), the techniques (`technique-*.md`), the skills (e.g. `/run-batch`), the scripts, the keys of `project.yaml` and of the run log, and the default statuses (`Not run`, `Question`). Existing workspaces are converted by `scripts/migrate_workspace.py`.

### v1 → v2

- **New:** privacy boundary (§1.2), own data model (§2), design techniques required per case (§5.2), the `Question` status (§8.5), approval before sending to the tracker (§9.4), golden and round-trip tests (§15), heuristics (§17).
- **Changed:** the code is no longer rebuilt in each session. It becomes versioned and validated by tests, and stays below the specifications in the hierarchy (§1.3, point 6).
- **Changed:** the rule for resolved bugs and the evidence policy become configurable per project (`project.yaml`).
- **Kept:** batch execution, run log, checkpoints every 15–20 cases, Pass/Fail/N/A/Blocked rules, duplicate handling, consistency between documents, immutable history, reuse before creating.
