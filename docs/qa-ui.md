# qa-ui — local web interface

> Version 1.0 · 2026-10-08 · Plan, phase 12 (specification first, methodology §15).
>
> Universal: nothing of a real project enters this document. The examples use the fictional Demo Shop.

`qa-ui` is a web page served by this computer, for one workspace at a time. The user gives it test cases (pasted,
in a file, or already in the project's library); it suggests which type of test fits each case, plans the batches,
starts them, shows the results as they are written and collects the user's approvals. It is a front of the work
cycle of the methodology (§3), not a new method: every step it starts is an existing skill, script or adapter.

```
python -m qa_ui <workspace> [--port 8765] [--no-open] [--claude PATH] [--max-turns 150] [--timeout-minutes 60]
```

Exit codes: 0 stopped by the user (Ctrl+C) · 2 usage or configuration (no `project.yaml`, the port is taken).

---

## 1. What it is and what it is not

| It does | It never does |
|---|---|
| Reads cases from text, a file or the workspace's library | Invents cases: a new case is a proposal until the user approves it (methodology §5.3) |
| Suggests a test module per case with deterministic rules, and shows why | Hide a rule: every suggestion names the signals it came from |
| Plans batches and saves them as `batches/<name>.yaml` (the format of `/plan-batch`) | Reduce coverage: the cases planned equal the cases selected (methodology §6) |
| Starts a batch: Claude Code headless with the module's skill, or a script | Run two batches at once in one workspace (they would share a login and the application's state) |
| Shows the run log as it grows and what needs the user | Classify a result: Pass, Fail, N/A, Blocked and `Question` come from the run (methodology §8) |
| Records the user's approvals in the run log | Approve anything by itself |
| Applies an approved run log through the project's adapter, after a confirmation | Commit, push or write to `history/`; `/checkpoint` still updates `context.md` and commits |

A session in Claude Code and the interface can be used on the same workspace, one at a time: both read and write the
same files (`batches/`, `runs/`, the workbook or `tracker/`), so nothing is kept in the interface's memory that the
workspace does not have.

## 2. Where it runs, and privacy

The page shows a real project's data (cases, results, bugs), so it follows the privacy boundary (methodology §1.2):

- The server listens only on the loopback address (`127.0.0.1`). `--host` does not exist.
- Each start creates a random token. The page opens at `http://127.0.0.1:<port>/?token=<token>`; every `/api/`
  request must carry it in the `X-QA-Token` header, and a request whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>` is refused (a page of another site cannot reach the interface through the browser).
- No request leaves this computer because of the page: no CDN, no external font, no telemetry. The scripts and the
  styles are files of this repository, served by the same server.
- Nothing is written in the `qa-assistant` repository. The interface writes only in the workspace: `batches/`,
  `runs/`, `cases/` or `tracker/export/` (approved new cases), and `.qa-ui/` (the output of the runs it started;
  ignored by git, added to `docs/workspace/gitignore`). Never in `history/` (methodology §13).
- A value of `.env` is never sent to the page: only the names of the variables and whether they are set. The output
  of a run is redacted with those values before it is stored or shown (`scripts/common.py`, `Redactor`).
- An uploaded file is read in memory and never stored.

## 3. The three screens

1. **Intake** — where the cases come from (§4) and what was understood: how many cases, the areas, and the flags.
2. **Suggestion** — one card per type of test (§5) with its level, the reasons, its cases and whether this computer
   is ready for it; the batch plan (§6); the new cases to approve (§7); the specification gaps.
3. **Execution** — the run in progress or a past run log (§8): the count per status, the cases, the run's output,
   and the panel **Needs you** with the proposed bugs and the questions (§9); then the apply step (§10).

The interface is in English, like everything in this repository; a project's own names (statuses, sheets, IDs) are
shown as they are.

## 4. Intake

Three sources, which can be combined (a case ID given twice is flagged, the first one kept):

| Source | How it is read |
|---|---|
| The workspace's library | `tracker: excel` — the workbook through the Excel adapter (read only): ID, the columns of `excel.cases.fields` when set, otherwise the headers `Title`, `Preconditions`, `Steps`, `Expected Result`, `Role`, `Platform`, `Priority` when the sheet has them; the area is the sheet; the last status is the status column. `jira` / `azure-devops` — `tracker/cases.json` as `import-cases` wrote it (never refreshed by the interface: it says how old the file is) |
| Pasted text | One case per line: `<ID> <title> → <expected result>` (also `->` and `=>`), or cells separated by a tab or `\|` in the order ID, title, expected result; a first line of headers (`id`, `title`, `expected`, `steps`, `preconditions`, `role`, `platform`, `area`, `priority`, in any order) names the cells instead. A Markdown table is the `\|` form |
| A file | `.csv` and `.tsv` (headers as above), `.txt` and `.md` (as pasted text), `.xlsx` (every sheet with a header row that has an ID and a title column; the area is the sheet), `.docx` (its paragraphs and table rows, then as pasted text). Up to 5 MB. `.pdf` is not read (§13) |

An ID is letters, digits, `.`, `_` and `-`, starting with a letter and holding a digit (`CHK-013`,
`TC-LOGIN-001`), or exactly an ID of the workspace's library, whatever its form (an Azure DevOps work item number
such as `4712`). A line without an ID is a **note**: it is kept as context for the rules and for Claude, and never
becomes a case.

A case of the input that is in the library gets from the library the fields the input did not give (a list of IDs
and titles is a way of selecting cases); what the input gave is kept.

Each case carries the fields of the model the source gave (methodology §2) and these flags, never silent fixes
(methodology §11):

| Flag | When |
|---|---|
| `no-expected` | No expected result. The run will mark it `Question` (methodology §8.5) |
| `vague-expected` | The expected result has six words or fewer and one of "as expected", "works", "correctly", "properly", "appropriate", "generic error", "tbd", "to be defined": it names no observable outcome |
| `several-expected` | The expected result is a numbered or bulleted list of two or more lines (methodology §5.3: split the case) |
| `duplicate-id` | The same ID twice in the input |
| `not-in-library` | The ID is not in the workspace's library: it can be planned only after it is added (§7) |

## 5. Suggestion

### 5.1 The rules

Each case gets **one** module of the closed list of the methodology (§4.1): `functional-web`, `exploratory`, `api`,
`accessibility`, `mobile`. A case with a `module` field keeps it. Otherwise each signal found in the case's ID,
title, steps, expected result and platform adds its weight to a module, and the highest total wins; a tie goes to
the first of `api`, `mobile`, `accessibility`, `exploratory`; a case with no signal is `functional-web`.

| Module | Signals (weight) |
|---|---|
| `api` | an HTTP method followed by a path, e.g. `POST /orders` (3); an ID part `API` (3); an expected result that starts with an HTTP status, e.g. `401` or `HTTP 4xx` (1); `endpoint`, `status code`, `http status`, `request`, `response`, `payload`, `JSON`, `GraphQL`, and `REST` in capitals (1 each) |
| `mobile` | a platform `android`, `ios` or `mobile` (3); an ID part `MOB` (3); `android`, `ios`, `emulator`, `device`, `tap`, `swipe`, `push notification`, `apk`, `the app` (1 each) |
| `accessibility` | an ID part `A11Y` (3); `WCAG` (3); `accessibility`, `screen reader`, `keyboard`, `focus`, `contrast`, `aria`, `alt text`, `reflow`, `zoom`, `tab order` (1 each) |
| `exploratory` | an ID part `EXP` (3); `charter`, `explore`, `exploratory` (2 each) |
| `functional-web` | `page`, `click`, `button`, `form`, `browser`, `link`, `login`, `url` (1 each) |

An ID part is what `-`, `_` and `.` separate (`ACC` is not a signal: it is as often an account as accessibility).
The words are matched whole, without case, and each one counts once per case. The signals of a case are shown next
to it, and the user can move a case to another module before planning; a moved case says so.

### 5.2 The cards

One card per module, plus two automated runs that need no case:

| Card | Runs | Level |
|---|---|---|
| A module | its skill on the cases assigned to it (§8.1) | **recommended** with at least one case; otherwise **not suggested** |
| Automated accessibility audit | `scripts/audit_a11y.py` on the pages of `accessibility.pages` | **optional** when a web case mentions a form, a login, a checkout, a registration or a payment and `accessibility` is active; otherwise **not suggested** |
| Automated site audit | `qa-audit <URL of the test environment>` (GET navigation only, as the engine always does) | **optional** when there is a web case; otherwise **not suggested** |

A card says whether it can start, and why not:

- the module is in `modules:` of `project.yaml` (a module that is not active is never started: it is added at the
  onboarding, with its section — methodology §4.1);
- the tools this computer needs, from `scripts/validate_project.py --environment` (the same checks, never a second
  list): `.env`, Node.js, the `qa-audit` engine and its browser, Maestro, `adb`, the build;
- Claude Code is on this computer (the `claude` executable, or `--claude PATH`) for the cards a skill runs;
- for the site audit, exactly one environment of `project.yaml` is the test environment (its key is `test`, or it
  is the only one); it is never guessed among several.

### 5.3 Asking Claude

The rules read words; a requirement in free text needs reading. **Ask Claude** sends the cases (ID, title, steps,
expected result, platform), the notes and the list of modules to Claude Code headless in the workspace, with no
tool allowed, and asks for JSON: a module and a reason per case, the specification gaps as questions, and, when the
user ticks it, proposals of new cases with the fields of the model and the design technique (methodology §5.2).
The answer is shown beside the rules' reading, case by case; it replaces it only where the user accepts it.
Proposals go to the list of new cases (§7). An answer that is not the expected JSON is shown as text and changes
nothing. It uses the user's Claude subscription, like any Claude Code session.

## 6. Batch plan

From the cards the user ticked and the cases assigned to them (methodology §6):

- a batch per module, area and role, in that order; the area is the case's `area`, else its sheet, else the ID up to
  its last `-`; a case without a role is with the others of its area;
- inside a batch the cases keep the order of the input (a case that creates state others reuse is ordered by the
  user, or by `/plan-batch` in a session);
- a batch holds at most 20 cases (a checkpoint every 15–20, methodology §7.2); a larger group is split;
- a `not-in-library` case is listed apart, with the reason, and is not in any batch;
- the number of cases in the batches plus those listed apart equals the number selected, or the plan is refused.

A batch is named `<YYYYMMDD>_<module>_<nn>` (`functional-web` as `web`, `accessibility` as `a11y`, `exploratory`
as `exp`), the first `<nn>` free in `batches/` and `runs/`. The plan is shown before anything is saved; **Save
plan** writes each batch as `batches/<name>.yaml` with `cases: [<ID>, …]` (what `/plan-batch` writes and
`scripts/workspace_ci.py` reads) and the keys `module:` and, when it has one, `role:`.

## 7. New cases

A case that is not in the library — pasted, or proposed by Claude and ticked by the user — enters through the
project's adapter and never by hand (methodology §5.3):

- `excel`: `cases/cases_<YYYYMMDD>_<nn>.json`, then `python -m adapters.excel.add_cases <workspace> <file> --dry-run`;
- `jira` / `azure-devops`: `tracker/export/cases_<YYYYMMDD>_<nn>.json`, then `python -m adapters.tracker <workspace>
  export-cases <file>` (a dry run without `--yes`).

The interface shows the dry run's output as it is. Only after the user confirms it runs the same command for real
(without `--dry-run`, or with `--yes`), shows its output and exit code with the meaning the adapter's technique
gives it, and reads the library again. A refusal (an ID that exists, a candidate duplicate) is shown and nothing is
forced: the user removes the case or changes its ID and tries again, in a new file.

## 8. Execution

One run at a time per workspace. A run has an id, a kind, a state (`running`, `finished`, `failed`, `stopped`), the
command's exit code and its redacted output in `.qa-ui/runs/<id>.log`.

### 8.1 A batch: Claude Code headless

The module's skill runs in Claude Code headless (`claude -p`), in the workspace's folder, with the prompt
`qa_ui/prompts/batch.md` filled in (the workspace, the root of qa-assistant, the batch's name and cases, the skill
and the turn limit), as the remote run of the methodology does (§12.4), with the user's subscription:

| Module | Skill | Tools allowed besides `Read`, `Glob`, `Grep`, `Skill`, `run_log.py`, and writing in `runs/` and `evidence/` |
|---|---|---|
| `functional-web`, `exploratory` | `/run-batch` | the Playwright MCP of the workspace's `.mcp.json` (`--mcp-config`) |
| `api` | `/test-api` | `scripts/api_request.py` |
| `accessibility` | `/audit-accessibility` | `scripts/audit_a11y.py` and the Playwright MCP |
| `mobile` | `/test-mobile` | `scripts/device.py`, `scripts/run_mobile.py` |

`--permission-mode dontAsk`, `--max-turns` (default 150), no web tools, and the time limit of `--timeout-minutes`
(default 60), after which the run is interrupted and what was written is kept. The run log `runs/batch_<name>.json`
is created first, with the `run` block (the environment and the build the user gave when starting).

Nobody answers the agent during the run, so the rules of a remote run hold (methodology §9.4):

- a result is written as soon as it is classified, with `scripts/run_log.py add`;
- a doubt about the expected result is `Question`, with the concrete question;
- a Fail whose problem already has a bug gets `"bug": "<id>"`; a new problem is a `new_defect` with
  `"proposed": true` — a proposal;
- nothing is applied nor sent, no git, no value of `.env` typed or printed.

The page reads the run log every two seconds while a run is `running`: it is the only source of the results.

### 8.2 The automated runs

| Card | Command | What is kept |
|---|---|---|
| Automated accessibility audit | `python scripts/audit_a11y.py <workspace> --output runs/a11y_<YYYYMMDD>_<nn>.json` | the JSON report, and the script's summary as the run's output |
| Automated site audit | `qa-audit <URL> --type accessibility,security,performance,functional`, in the workspace's folder | `qa-output/<timestamp>/` as the engine writes it, and the engine's summary as the run's output |

They are a first pass, not case results: their findings never enter a run log by themselves. A finding the user
wants as a result goes through a case and `/audit-accessibility` (technique-accessibility.md).

### 8.3 Stopping

**Stop** interrupts the command (an interrupt first, a kill after ten seconds). The results already written stay;
the cases not run are not in the run log (`Not run`).

## 9. What needs the user

Read from the run log, never kept apart:

| Item | Actions, each one an edit of that item in the run log |
|---|---|
| A `new_defect` with `"proposed"` | **Approve** (the key `proposed` is removed); **Edit** (its text fields); **Link an existing bug** (`"bug": "<id>"` replaces the `new_defect`); **Drop** (the `new_defect` is removed, the result stays) |
| A result in `Question` | **Answer**: the final status, of the project's vocabulary, and what was observed |

An edit is refused when the batch was already applied (`scripts/run_log.py`: the correction goes to a new batch),
and the file is written whole and atomically, as that script does.

## 10. Applying a run log

The step of `/checkpoint` that goes through the adapter. Enabled only when the run log has no `Question` and no
`"proposed"` left, and the adapter's own validation finds no problem (the check of `scripts/workspace_ci.py`
`post-check`).

- `excel`: the page says what will be written (results, new defects, the workbook's file) and, after the user
  confirms, runs `python -m adapters.excel.apply <workspace> <run log>`.
- `jira` / `azure-devops`: `python -m adapters.tracker <workspace> apply <run log>` first (the dry run), its plan
  shown as it is; after the user confirms, the same command with `--yes` (methodology §9.4).

The output and the exit code are shown with the meaning the technique gives them. The interface does not update
`context.md` nor commit: `/checkpoint` in a session does (methodology §12.1).

## 11. The HTTP interface

JSON in and out; every route needs the token (§2). An error is `{"error": "<text>"}` with status 400 (the request),
403 (token or host), 404, or 409 (refused: a run is in progress, the batch was applied, the plan does not add up).

| Route | Does |
|---|---|
| `GET /api/state` | the project (name, tracker, modules, environments' keys, statuses), the readiness checks, the planned batches, the run logs with their counts, the run in progress |
| `POST /api/intake` | `{"text"}`, `{"file": {"name", "data"}}` (base64) or `{"library": true}` → cases, notes, flags |
| `POST /api/suggest` | `{"cases", "notes"}` → the module of each case with its signals, and the cards |
| `POST /api/ask-claude` | `{"cases", "notes", "proposals"}` → Claude's reading (§5.3) |
| `POST /api/plan` | `{"cases", "assignments", "cards"}` → the batches and the cases listed apart; with `"save": true` writes them |
| `POST /api/cases` | `{"cases"}` → the dry run of §7; with `"confirm": "<file>"` the real run |
| `POST /api/runs` | `{"batch", "environment", "build"}` or `{"audit": "a11y" \| "site"}` → starts a run |
| `GET /api/runs/<id>` | its state, the tail of its output, the run log |
| `POST /api/runs/<id>/stop` | §8.3 |
| `GET /api/runlogs/<batch>` | the run log, its counts and what needs the user |
| `POST /api/runlogs/<batch>/items/<case>` | `{"action": "approve" \| "edit" \| "link" \| "drop" \| "answer", …}` (§9) |
| `POST /api/runlogs/<batch>/apply` | the dry run or the check of §10; with `"confirm": true` the real run |

## 12. Tests (methodology §15)

Unit tests, no browser and no network: the parsers of §4 on each format; every rule of §5.1 and each level of §5.2;
the plan of §6 (the counts add up, the split at 20, the names, the files written); the edits of §9 and their
refusals; the server (token, host, routes, status codes) on an ephemeral port; the runs with a **fake `claude`**
and fake scripts (an executable written by the test that appends to the run log), so no real session nor site is
ever used. The page's script has no build step and is checked by a test that it references no external address.
The demo workspace (`examples/demo`) is the fixture of the library and of the state; nothing of a real project.

## 13. What is not done

- `.pdf` and images as a source (no parser without a new dependency; export to `.docx`, `.xlsx` or text).
- Several runs at once, or a queue: one run, then the next.
- Starting a remote run in the workspace's CI (`scripts/workspace_ci.py`): it stays a `gh workflow run`.
- Editing a case of the library, or its steps: proposed to the user and done in the workbook or the tracker.
- The dashboard: `/dashboard` and `scripts/build_dashboard.py` stay as they are (Excel only).
- Any access control beyond the loopback address and the token: the interface is one user's, on their computer.
