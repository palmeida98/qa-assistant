# Core Methodology

> **Layer 1 — Universal QA methodology**
> Version 2.1 · 2026-09-26 · Replaces the version used in Claude.ai Projects; in English since plan 8.11.
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
| **Engine** | Skills (`.claude/skills/`), adapters, scripts (`scripts/`, `tools/`), tests and guards; and the automated audit engine `qa-audit` (`src/qa_audit`, specified in `docs/qa-audit.md`), which lives next to this layer and does not replace it (decision of 25.09.2026, which revises the one of 24.09.2026 about a separate core) | Personal repository `qa-assistant` |
| **1. Methodology** | This document | `qa-assistant/docs/` |
| **2. Test modules** | Techniques per type of test (`technique-browser.md`, `technique-api.md`, `technique-accessibility.md`, `technique-mobile.md`; list in §4.1) | `qa-assistant/docs/` |
| **3. Adapters** | Conversion between the internal model and each company's tools. Today only Excel (`adapters/excel/`); the other trackers are manual logging (§4.2) | `qa-assistant/adapters/` |
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

The internal model is independent of any company's format: it is the common vocabulary of the skills, the run logs and the adapters, not a separate database. Excel workbooks, Azure DevOps and Jira are input and output formats; those with an adapter are handled by it, the others by hand (§4.2).

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

A case's ID is **stable**: it does not change when the case is edited, moved or exported. The adapters keep the mapping between the internal ID and each external tool's ID.

---

## 3. Work cycle

```
Onboarding → Analysis → Case design → Batch planning → Execution
    → Classification → Bug management → Validation → Checkpoint / Report
```

Each stage has a corresponding skill in Claude Code:

| Stage | Skill |
|---|---|
| Onboarding | `/project-onboarding` |
| Analysis | `/analyze-feature` |
| Case design | `/generate-cases` |
| Batch planning | `/plan-batch` |
| Execution and classification | `/run-batch` (browser), `/test-api` (API), `/audit-accessibility`, `/test-mobile` |
| Bug management | `/log-bug` |
| Checkpoint | `/checkpoint` |
| Validation and report | `/end-session` |
| Follow-up (at any time) | `/dashboard` |

---

## 4. Onboarding a project

1. Create the folder `qa-workspaces/<company>/<project>/`.
2. Run `/project-onboarding`, which copies `context-template.md` and creates `project.yaml`.
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
7. Import the existing cases with the project's adapter and validate the round-trip (import → export → compare). With the Excel adapter, which reads and writes the workbook itself (there is no import into a separate model), this is `ExcelAdapter.from_project(...).load()` without errors and `cross_check()` showing only problems that were already in the workbook; `python scripts/validate_project.py <workspace>` does both.

A workspace created before the translation (plan 8.11) is converted once with `python scripts/migrate_workspace.py <workspace>` (dry run by default, `--apply` to write).

### 4.1 Test modules

`modules:` in `project.yaml` only accepts the values of this closed list. Each active module needs its section filled in `project.yaml` (`scripts/validate_project.py` checks it):

| Module | Technique | Skill | Section in `project.yaml` |
|---|---|---|---|
| `functional-web` | `technique-browser.md` | `/run-batch` | `environments`, `roles` |
| `exploratory` | `technique-browser.md` (charters, methodology §5.2) | `/run-batch` | — |
| `api` | `technique-api.md` | `/test-api` | `api:` |
| `accessibility` | `technique-accessibility.md` | `/audit-accessibility` | `accessibility:` |
| `mobile` | `technique-mobile.md` | `/test-mobile` | `mobile:` |

Other variants mentioned in the techniques (smoke, role matrix, visual, cross-browser, forms) are done inside these modules, as design techniques or types of batch, and are not values of `modules:`. A new module first enters this table, with a technique and a skill (process of §15).

### 4.2 Trackers

`tracker:` in `project.yaml` accepts `excel`, `jira` and `azure-devops`, but only Excel has an adapter:

| Tracker | Results | Bugs | Dashboard |
|---|---|---|---|
| `excel` | applied by the adapter at `/checkpoint` (`python -m adapters.excel.apply`) | `new_defect` / `bug` in the run log, applied with the results | `/dashboard` |
| `jira`, `azure-devops` | **manual logging**: the run log in `runs/` is the source; the user (or the assistant, with the tracker's tool available and approval) passes them to the tracker | prepared in the project's format and sent only once approved (§9.4) | none |

An adapter for another tracker is only written when a real project needs it, with the golden and the tests of the Excel adapter as a model (§15). `scripts/validate_project.py` warns when the tracker has no adapter.

Information already in the workspace **is not asked for nor researched again**, unless there is evidence that it is out of date or inconsistent. If information needed to continue is missing, identify exactly what is missing and ask.

---

## 5. Analysis and case design

### 5.1 Analysis

For each feature, identify:

- the roles that reach it and what each one can or cannot do;
- the inputs, the states, the business rules and the integrations;
- the risk: probability of failure × impact for the user or for the business;
- the specification gaps. These **are not filled with assumptions**: they are recorded as a question or as a "missing specification" note.

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
- A **new** bug goes as `new_defect` (fields in the tracker's technique, e.g. `technique-excel.md` §4), only after the user approves it (§9).
- Cases in `Question` can stay in the log during the session, but are reclassified before the batch goes to the workbook or tracker (§8.5): the adapter refuses a batch with statuses outside the project's vocabulary.

### 7.2 Checkpoints

The results go to the model and to the main files at **checkpoints**:

- every 15–20 cases;
- before any operation that could lose work;
- at the end of the session.

Each checkpoint keeps the statuses, the results, the new or updated bugs and the exact point where the execution is.

### 7.3 Evidence

| Result | Evidence |
|---|---|
| Pass | None beyond the record |
| Fail | Enough to reproduce: URL, role, steps done, data used, expected → observed, relevant messages |
| N/A | Short justification |
| Blocked | Reason and the dependency that blocks |

By default the evidence is **textual**. Screenshots or video only when `project.yaml` requires them. Do not produce redundant evidence.

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

### 9.2 Minimum content

- title in the format defined by the project;
- environment and version/build;
- role and platform;
- preconditions and steps to reproduce;
- **expected → observed**;
- linked test cases;
- suggested severity and priority **only when asked for**. The decision is the user's.

The bug's structure always follows the project's format. It is not changed without need.

### 9.3 Retest

| Result of the retest | Action |
|---|---|
| Still reproduces | Keep and update the bug |
| Resolved | Apply the project's rule (`remove` from the active list or `archive`) |
| Could not be validated | Keep the current status and record the reason |

### 9.4 Sending to the tracker

The assistant prepares the bug. Sending it to the external tracker only happens **after the user approves it**.

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
- When resuming: read `context.md` and the latest run log in `runs/` first, identify what was already run and continue from the last valid point.
- Do not repeat work without need. Do not rebuild the progress from scratch.
- The workspace's git serves as a restore point: one commit per checkpoint. The checkpoint says how many commits are not pushed; the push to the private remote happens only when the user says so, never with `--force`.

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

`context.md` is the session's **temporary** state. It does not replace the project's `CLAUDE.md` nor `project.yaml`. A decision that becomes a permanent rule moves up to one of those files.

### 12.3 Resuming on another computer

A project must be continuable from any computer with access to its private remote:

1. Install the tools and `qa-assistant` ([`workspace/setup.md`](workspace/setup.md); clone, `pip install -r requirements.txt` and `pip install -e .`, `python scripts/sync_skills.py --apply`).
2. Clone the workspace into `qa-workspaces/<company>/<project>/`.
3. Create `.env` from `.env.example`, with the values from the user's own secret store (never from the repository, a chat or a log).
4. `python scripts/validate_project.py <workspace> --environment`: the tools the active modules need are present; what is missing is installed before testing (e.g. `scripts/setup_emulator.py` for `mobile`).
5. Read `context.md` and continue from the last checkpoint (§12.1).

Anything a run needs and that is not in the repository (a build, a device, a login link) is written in `context.md` or `project.yaml`, with where to get it.

Step by step, with what each module needs: [`docs/workspace/resume.md`](workspace/resume.md).

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

Do not assume that a tool available in one environment exists in another.

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

### 16.2 End of session (`/end-session`)

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

### v2.0 → v2.1 (plan 8.11, 26.09.2026)

- **Changed:** everything in English, the names included: this file (`methodology.md`, before `metodologia-core.md`), the techniques (`technique-*.md`), the skills (e.g. `/run-batch`), the scripts, the keys of `project.yaml` and of the run log, and the default statuses (`Not run`, `Question`). Existing workspaces are converted by `scripts/migrate_workspace.py`.

### v1 → v2

- **New:** privacy boundary (§1.2), own data model (§2), design techniques required per case (§5.2), the `Question` status (§8.5), approval before sending to the tracker (§9.4), golden and round-trip tests (§15), heuristics (§17).
- **Changed:** the code is no longer rebuilt in each session. It becomes versioned and validated by tests, and stays below the specifications in the hierarchy (§1.3, point 6).
- **Changed:** the rule for resolved bugs and the evidence policy become configurable per project (`project.yaml`).
- **Kept:** batch execution, run log, checkpoints every 15–20 cases, Pass/Fail/N/A/Blocked rules, duplicate handling, consistency between documents, immutable history, reuse before creating.
