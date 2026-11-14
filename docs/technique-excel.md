# Excel technique

> **Layer 2 — Technique for automating Excel workbooks**
> Version 1.3 · 2026-10-04 · Extracted from a real `qa_lib.py`, used in a client project. Implemented in the universal adapter `adapters/excel/`; in English since plan 8.11.
>
> Project agnostic: no sheet, column or status name specific to a real workbook lives here. Subordinate to `methodology.md`.

---

## 0. Scope

Covers the automation of Excel workbooks used as the source of truth for test cases and defects (through `openpyxl`), including the safe recalculation of formulas. The implementation is the **universal adapter** `adapters/excel/base.py` (`ExcelAdapter`), which contains no sheet, column or status name: each project gives it a **configuration** with the real layout of its workbook (see `adapters/excel/example.yaml`, fictional). Sheet, column and status names live **only** in the project's workspace. Projects not migrated yet can keep a `scripts/qa_lib.py` of their own in the workspace.

The configuration lives in the `excel:` section of the workspace's `project.yaml` (fields commented in the template `docs/project.yaml`) and is read with `ExcelAdapter.from_project(<workspace>)`. At the onboarding, it is filled in by inspecting the real workbook with `python -m adapters.excel.inspect <copy.xlsx>`, which only reads the file and proposes the section: what it cannot detect safely stays as `CONFIRM: …` for the user to decide.

**One workbook per platform (or per environment).** `excel.file` is the default workbook: the workbook of every platform without an entry of its own. `excel.workbooks` declares the others, one per platform (the entry's `platform`, else its key), each with its batches by name: `{ios: {file: workbook/app-ios.xlsx, batches: ["batch_ios*"]}}`. The workbook of a batch is decided by one rule, `adapters.excel.base.route_workbook`, used by the apply, `run_log` (whether a batch is applied, so it stays immutable), the audit and totals of `qa_report.py`, and the CI's validation. The batch's platform comes from three places that must agree: the run block's `platform`, the `report.platforms` pattern its name matches, and the `excel.workbooks` entry its name matches. The apply **stops with exit 4 and writes nothing** when: the name matches the patterns of two workbooks or of two platforms; two of the three places name different platforms; the batch is of a platform with its own workbook but its name is outside that workbook's patterns; the project declares several workbooks and nothing says the batch's platform (the default workbook is never chosen by elimination); the target workbook does not exist or cannot be read; the batch is already listed in the control sheet of another workbook. A project with several workbooks therefore names its batches by platform (`report.platforms`) or declares `run.platform`. `run_log` treats a batch as applied when **any** declared workbook lists it. The environment variable `QA_WORKBOOK` (or the `file` argument of `from_project`) replaces the file for one command (a path relative to the workspace, or absolute; empty counts as unset); when the project declares `excel.workbooks` and the variable names another workbook than the batch's own, the apply refuses it. The workbook must already exist (build it with the project's builder and the same cases). Cases files (`add_cases`) are not batches: they go to `excel.file`, or to `QA_WORKBOOK`.

**One apply at a time, never a broken workbook.** The apply holds the workspace's lock (technique-workspaces.md §7) from reading the workbook to the recalculation: a second apply waits, then is refused with nothing written. The workbook is saved to a temporary file next to it, opened again to check it, and only then put in place. A new defect takes the build of the run block (`run.build`) before `excel.defects.defaults.build`; a `new_defect` key with no column in `excel.defects.batch_fields` is refused (it would be dropped without a word).

## 1. Flow of applying a batch

The pattern that works in practice, in this order, never writing row by row during the execution (methodology §7.1):

1. **Load** the workbook keeping the formulas (`data_only=False`) — read with `data_only=True` only in a separate copy, when only the values already calculated are wanted (e.g. checking the Summary's totals).
2. **Apply the batch** from the run log (methodology §7.1), updating status, actual result and notes per case, and creating the new defects the batch brings.
3. **Regenerate the bugs/defects view** from the current state of the cases, instead of editing it by hand.
4. **Cross-check** (methodology §11): validate valid statuses, duplicated IDs, broken references. Returns a list of problems (empty = all good).
5. **Only save over the original if the cross-check returns zero problems.** If there are problems, save separately (e.g. `<name>.REVIEWED.xlsx`) for inspection, without touching the official file.
6. **Recalculate formulas safely** (§2) only after saving.

It is the same "validate before adopting" pattern of the methodology (§15), applied to writing the workbook instead of to a code change.

In the adapter: `load()` → `apply_batch()` → `regenerate_active_defects()` → `save_if_clean()` (includes `cross_check()`) → `recalc()`. `load()` fails straight away if the configuration does not match the workbook (a required sheet or column missing), and `apply_batch()` checks every case ID before writing anything. The optional case columns (actual result, notes) are only used in the sheets where they exist.

To run the whole flow in a workspace: `python -m adapters.excel.apply <workspace> <batch.json>` (from the root of qa-assistant). It exits with 0 (saved, or batch already applied), 2 (cross-check with problems: original untouched, `.REVIEWED.xlsx` separate), 3 (saved, but the recalculation failed), 4 (invalid batch or configuration, or a batch already applied with another content: nothing written; §4 and §5) or 5 (saved and recalculated, but the workbook's summary does not match the direct count; §5).

### 1.1 Simulating an apply (`--dry-run`)

`python -m adapters.excel.apply <workspace> <batch.json> --dry-run [--json] [--no-recalc]` runs the same route,
validation and transformation as the apply (one code path), on a copy of the target workbook in a temporary folder,
and lists: the target workbook and platform (and where the rule came from), every cell that would change (sheet,
cell, case of the row, before → after), the counts per status before and after, the new defects, the row of the
control sheet, the cross-check and summary problems, the problems that stop the apply, and "already applied". A
value that only holds the instant of the application (label date, `applied_at`, the view's summary date, the report
date) is marked `<at apply time>`: the real apply writes its own instant there. Those cells are found by running the same transformation a second time at another instant and comparing (never by looking for a date in the text, which an observed result may quote). Formulas the recalculation only
rewrites (the same formula with other quoting) are counted apart. The exit code is the one the apply would give
(4 when a problem stops it). Nothing in the workspace is written (the original's sha256 is unchanged) and the
temporary folder is removed, also on error.

**What the terminal shows (17.4).** By default the dry run prints a short card of about eight lines (target and token,
changes per sheet, statuses, problems and warnings, the path of the review file and the whole apply command with
`--expect`) and writes **one file**, `<batch>.dryrun.md` next to the batch (`<cases>.dryrun.md` for `add-cases`), with the
target, the sha256 of the batch, every cell before → after in full and the token: the same content and options as the
token, so what the user approved is what that file holds. `--detail` (or `output.terminal: normal`) prints the complete
text as before and writes nothing; with `output.detail_files: false` the complete text is printed. Problems, warnings and
the command are never dropped by a line limit. `--json` is unchanged.

The dry run ends with a **preview token** `v2-<hash>`: the batch (its name, which routes it to a workbook, and its
content), `project.yaml`, every declared workbook, the destination resolved (its path and content), and the options that change what is written: `--label` (as given; the
default label is a marker, because its date is the apply's own), the reporter actually written (`--reported-by`, else
`excel.defects.reported_by`, else the default), `--no-recalc`, a fixed `--now` and, when given, `--keep-previous` (a token
without it is the same as before). The dry run prints them
("Options covered by the token"). `apply ... --expect <token>` applies only if the same options are given and none of
those things changed; otherwise exit 4 with "the preview ... is out of date" and nothing written (checked before any
write): run the dry run again and show it again. **The real clock is not in the token**: the seconds that pass never
invalidate a preview, and the cells that take the instant of the apply (label date, `applied_at`, report date, the view's
date) are exactly the ones the dry run marks `<at apply time>`; to fix them in the approval, give `--now` to both. A
token without the `v2-` prefix is from before this rule (it did not cover the options): it is refused with a message and
the dry run is repeated. Dry runs left pending when this change is adopted must be repeated.

### 1.2 Preview and write: the destination and the approval (13.11.2026)

**Why:** an add «simulated on a copy» wrote into a real workbook. The copy was named only in `$QA_WORKBOOK`, the add was
run with `--platform` (which then chose the destination from `project.yaml` and ignored the variable without a word) and
without `--expect`, so nothing tied the write to a preview. The rules below apply to `apply`, `add_cases` and `reconcile`.

- **A preview reads, a write needs its approval.** `--dry-run` may read the destination and compute the change (the apply
  on a copy in a temporary folder made for it; the add and the reconcile as a plan in memory); it never saves, replaces or
  recalculates the destination (its SHA-256 and modification time stay). From the command line, a **write needs
  `--expect <token>`**, the token of the preview the QA approved: without it the command refuses (exit 4) and writes nothing.
  A repeat of something already done also goes through a preview, which says «already applied / already added».
- **The destination is resolved once and shown in full.** In order: an explicit path (`--workbook <path>` or
  `$QA_WORKBOOK`; both given must name the same file), else the project's rule (`--platform` for the add; the batch's
  route, `route_workbook`, for the apply and the reconcile). The preview prints the **absolute path** effectively
  resolved, where it came from, the platform, the batch or cases file, the changes and the token.
- **An explicit path never falls back silently.** It is refused (exit 4, nothing read or written) when it is outside the
  workspace (writes stay in the workspace: methodology §4.3), does not exist, is not a workbook (`.xlsx`, `.xlsm`), or
  contradicts the project's rule: another workbook than the one `--platform` or the batch's route names in a project that
  declares several workbooks, or `--platform both`. A copy to simulate on is a copy of the whole workspace, passed as the
  workspace.
- **The token is bound to the destination.** It covers the destination's workspace-relative path and its content, so the
  approval of one workbook never authorises a write to another, even with identical content. The apply's token keeps
  its `v2-` prefix (the workspace CI and the templates read it) and, from 13.11.2026, also hashes the destination: a
  preview made before that is refused as «out of date» and is regenerated with the same `--dry-run` (and shown again for
  approval); the add's `v1-…` and the reconcile's `v1-…` already named the destination. Before writing, under the workspace's lock,
  the command resolves the destination again and recomputes the token: a destination or content that changed gives
  another token, the write is refused and a new preview is shown for a new approval.

**The result a case already had** is replaced by the batch's (the workbook shows the latest result of each case). With
`apply --keep-previous` it is not lost in the workbook: when a case already had a result text and the batch replaces it
with another, the case's notes get `Previous result (<status>): <text>` (the text of `notes.previous_result`), after the
label like every note. It needs the notes column in every case sheet (else the batch is refused whole before anything is
written) and is part of the token; without it nothing changes. The previous value always stays in git and in the run logs.

**Date of the application.** By default, the adapter uses the clock: the date of the notes' label, the report date of new defects, the date of the active view's summary text and the `applied_at` of the control sheet. With `--now YYYY-MM-DDTHH:MM:SS` in `apply` (attribute `now` of `ExcelAdapter`), it uses that instant in all those places, and the same sequence of batches always produces the same cells. It serves to rebuild a workbook from the batches with the date each batch was run (e.g. `examples/demo/rebuild_workbook.py`, which uses the first `ts` of each batch); in a normal session it is not used.

## 2. Recalculating formulas safely

**Golden rule, learnt the hard way:** never recalculate a workbook running directly on a path inside a synced or mounted folder (a Cowork connected folder, OneDrive, a network drive, any FUSE mount). LibreOffice hangs forever on that filesystem, 100% reproducibly — it is not the file, it is the type of mount.

**Right pattern:** always copy the file to a local temporary folder before recalculating, recalculate there, and only then copy the result back.

The concrete recalculation mechanism depends on the environment, and **is not portable on its own**:

| Environment | How to recalculate |
|---|---|
| Claude.ai / Cowork session | `recalc.py` of the internal `xlsx` skill — only works inside those sessions |
| Local Claude Code, with LibreOffice installed (**default**) | `soffice --headless --convert-to xlsx` with an **isolated user profile** (`-env:UserInstallation=…`) set with `OOXMLRecalcMode=0` (always recalculate on open); without it LibreOffice may keep the cached values. Always on the local copy, with a timeout that kills the process tree |
| Local Claude Code, on Windows, with desktop Excel installed (optional) | COM automation (`pywin32`) in a child process (so the timeout is effective): open, `CalculateFullRebuild()`, save |

When migrating a project's `qa_lib.py` from a Cowork session to local Claude Code, **the recalculation mechanism has to be rewritten** for the new environment — the rest of the logic (load, apply batch, cross-check) stays.

In the adapter: `adapters/excel/recalc.py`, `recalc_safe(path, mechanism="libreoffice" | "excel-com", timeout)`, chosen by the configuration (`recalc:`). Returns `{"ok": True}` or `{"ok": False, "error": …}` and, on error, the original is untouched. The timeout kills the whole process tree (on Windows with `taskkill /T`; on Linux and macOS, the process group): `soffice` starts a `soffice.bin` that, when stuck, held the wait forever.

When saving the file again, LibreOffice introduces differences with no visual nor functional effect (e.g. the transparency byte of colours goes from `00` to `FF`; autofilter references lose their `$`; error literals in formulas that were already broken become lower case). Do not mistake this for data loss — but do not claim "formatting preserved" without comparing what was actually compared.

## 3. Formatting: avoiding the same kind of hang

**Never set `row_dimensions[...].height` manually on a row that has `wrap_text=True` in a wide column.** It causes the same LibreOffice hang symptom as recalculating in a mounted folder. Leave the row height automatic — set nothing.

## 4. Batch format (run log → application)

The run log (methodology §7.1) that an execution batch produces must contain, at least, per case: the case ID, the status, the actual result (what was really observed, not a repetition of the expected one) and, when there is a new defect, that defect's data (title, description, severity, related case, reference to the requirement).

In the adapter, the batch is `{"results": [item, …]}`, each item `{"tc", "status", "actual_result", "new_defect", "bug"}`:

- `new_defect` is `null` or a dictionary whose keys are mapped to columns by the configuration (`defects.batch_fields`); `related_tc` (by default the case itself), `severity` and `priority` (by default the severity) have their own role.
- `bug` (optional) is the ID of an **existing** defect linked to the case (retest, duplicate): it adds to the case's notes the note `notes.existing_defect` (by default `Linked defect: {id}.`) and creates no defect.
- Other keys (e.g. `ts`, `note`) are ignored by the adapter. `translated_from` (top level) is written by `scripts/migrate_workspace.py` in a batch converted from before the translation: the hash of the original content, which stays the batch's identity (§5).

Before writing anything, the adapter **refuses the whole batch** (an error with the list of problems) if: the batch is empty; an item has no `tc` or `status`; the same case appears twice; a status is not in `cases.statuses` (includes `Question`, which is resolved before applying — methodology §8.5); a `bug` does not exist in the defects sheet; a new defect still has `"proposed"` (a remote run's proposal, approved only by removing it in the pull request — methodology §9.4; `"proposed": false` is accepted); or, with a list of `priorities`, a new defect's priority is outside it.

A case's status always uses the list of valid statuses **of that project** (defined in `project.yaml` or in the adapter), never a word made up on the spot. Different projects may use different vocabularies for the same concepts (for example, a real project observed writes its case statuses in its own language, and has a richer list for defects, with seven statuses from "open" to "not a defect") — this document imposes no vocabulary; it requires the vocabulary to be explicit and checked in the cross-check (§1, step 4).

### 4.1 Adding approved cases (`add-cases`)

The cases `/plan-tests` proposes only enter the workbook after the user approves them (methodology §5.3), and they enter through the adapter, never by hand: the same load → validate → save-if-clean → recalculate → summary check of a batch.

    python -m adapters.excel.add_cases <workspace> <cases.json> [--platform …] [--workbook <path>] [--dry-run] [--label …] [--no-recalc] [--now …] [--expect <token>]

- **Input:** `<workspace>/cases/cases_<YYYYMMDD>_<nn>.json`, `{"cases": [case, …]}` with the fields of the case model (methodology §2), the same as the tracker adapters' cases file (`technique-jira-xray.md` §7): `id`, `title`, `area`, `feature`, `preconditions`, `steps` [{`action`, `data`, `expected`}], `expected`, `role`, `platform`, `priority`, `technique`, `origin`. The `id` is the **final** ID in the project's pattern (a workbook has no tracker to give one), and its prefix chooses the sheet (`cases.sheets`). A file already added is never edited: new cases go in a new file.
- **Configuration:** `cases.fields` maps each model field to the header of its column (`{title: "Title", steps: "Steps", expected: "Expected Result", …}`); `title` is required for `add-cases` (it is how duplicates are found); the ID and the status always use `cases.columns`. A mapped column may be missing from some sheets (as `actual_result`): there the field is not written, and the command lists it. A field with no column is listed as not written, never refused. `cases.new_status` (optional) is the status of a new case; by default the first of `cases.statuses` (the pending status the dashboard assumes).
- **Text of the cells:** `steps` becomes one line per step, `<n>. <action>`, with ` [<data>]` when there is data and ` -> <expected>` when the step has an expected result; a list becomes its items joined with `, `; every other value is written as it is.
- **Refused whole, before anything is written** (exit 4): no cases; a case that is not an object; an `id` missing, without a configured prefix, repeated in the file or that **already exists** in the workbook; a `title` missing; `steps` not a list of objects; a **candidate duplicate** — the same title (ignoring case and repeated spaces) as a case of the target sheet or another case of the file (methodology §10): the user decides and removes the case from the file; it is never forced.
- **Where and how:** each case goes in the row after the last row with an ID of its sheet, in the file's order; every cell of the new row gets the style of the previous case row (font, fill, border, alignment, number format, protection); a column the case does not fill that has a **formula** in the previous row gets that formula translated to the new row; a data validation that covered the previous row's cell is extended to the new one. The row height is never set (§3).
- **Once only:** the file is recorded in the control sheet (§5) like a batch — its identity is the file name, the fingerprint is that of its cases, `items` is the number of cases —; the same file again changes nothing (exit 0) and the same name with another content is refused (exit 4).
- **Then the usual:** `save_if_clean()` (exit 2 with the cross-check's problems, original untouched), `recalc()` (exit 3) and `check_summary()` (exit 5: a summary with fixed ranges that no longer reach the new rows, or without the new area — fix the formulas).
- `--dry-run` validates and prints, per case, the sheet, the row and the cells it would write, and the fields not written; it writes nothing (0 valid · 4 refused). It ends with a **preview token** `v1-<hash>`: the cases file, the workbook that would change (path and content), `project.yaml`, the plan (sheet, row and cells of every case) and the options that change what is written (`--label`, `--no-recalc`, a fixed `--now`).
- **`--expect <token>` binds the add to the dry run the user saw**: if the cases file, the workbook, `project.yaml`, the plan or an option changed, exit 4 with "the preview … is out of date" and nothing written (checked after the lock is taken, before the write); a token of another command (`v2-…`, the apply's) or `--expect` with `--dry-run` is refused. Cases already added are not added again. From the command line the add itself needs `--expect` (§1.2). The add **holds the workspace's lock** like the apply (one writer at a time: exit 4 and nothing written when another is writing), and the final recalculation puts the result in place in one step (a copy next to the workbook renamed over it), so a stop in the middle leaves the workbook as it was.

In the adapter: `validate_cases()` (read only) → `add_cases()` → `save_if_clean()` → `recalc()` → `check_summary()`.

- **`--platform android|ios|both|<platform>`** (plan Q4) chooses the destination from `project.yaml` without `QA_WORKBOOK` and without copying a file: the `excel.workbooks` entry of that platform (its `platform`, else its key), else `excel.file` (the workbook of the platforms without an entry). `both` is every workbook: all are validated before any is written and ONE combined token (`v1-…`) covers them; the dry run writes one review per destination. A case whose `platform` field names another platform is refused for that destination (a case for both must say so), and an ID that ANOTHER `cases/*.json` proposes with different content (the `platform` field aside) and that is not yet in the target workbook is refused. Without `--platform` the behaviour is the old one, with a notice when the project has several workbooks. An explicit path (`--workbook`, `$QA_WORKBOOK`) that names another workbook than `--platform` is refused, never ignored (§1.2). The result of a case that is only proposed is recorded with `run_log.py add --proposed`.

### 4.2 Annotating cases (`annotate`)
A result is never rewritten to improve a remark about it, and the workbook is never edited by hand. To record that a result
is **under review** (or any later remark) without touching its status or its observed result:
`python -m adapters.excel.annotate <workspace> <annotations.json> --dry-run`, review `<name>.dryrun.md` (next to the file, never in
`runs/`: every note in full, the notes before and after, the token), and after the approval of THAT review
`... --expect <token>`. The file is `{"annotations": [{"case": "TC-X-001", "note": "text"}]}`, one entry per case. The note is
appended to the Notes cell after a blank line as `<label> <note>` (label default `[date, file name]`). Before saving, the status
and the observed result of each case are compared with what they were: a difference stops the write. The write is recorded in the
control sheet like a batch (the same annotations again change nothing; the same file name with another content is refused) and a
note already contained in the case's notes is refused. Exit codes and the token as `add-cases` (§4.1). Needs
`excel.cases.columns.notes`.

### 4.3 Traceability columns (`add_columns`)
To record the PBI ID, the QA task, the origin and the platform next to each case, add the columns with
`python -m adapters.excel.add_columns <workspace> <columns.json> [--platform …] --dry-run`, review `<name>.dryrun.md` (sheets, positions, headers, the lines for
`project.yaml`) and, after the approval of THAT review, `... --expect <token>`. The file is `{"columns": [{"header": "PBI (ID)", "field":
"pbi_id"}, …]}` (`field` is a field of the case model: `pbi_id`, `qa_task`, `origin`, `platform`, `feature`…). Each header is appended after the last
header of every case sheet with the style of the one before it, the autofilter keeps covering the header row, and nothing else changes (no case,
formula or row). A header that already exists, an Excel table in a case sheet and a column file with repeated headers or unknown fields are refused,
writing nothing; the write is recorded in the control sheet like a batch (the same columns again change nothing). The adapter fills a new column only
after `excel.cases.fields` maps it (`pbi_id: "PBI (ID)"`…); until then `add-cases` lists the field as not written. Exit codes and tokens as §4.1. Try it
on a COPY of the workbook first: it is a structural change.

## 5. Consistency between results and defects

When regenerating the bugs view from the cases (instead of editing it by hand), the same duplicate rules of the methodology apply (§9.1, §10): one defect per distinct problem, update instead of duplicating, and a defect whose status means "resolved" follows the project's rule (remove from the active list or archive — methodology §9.3, configurable in `project.yaml`).

A batch **must never be applicable twice** without being detected — applying the same batch again cannot duplicate notes nor create defects twice. The adapter checks this automatically: it keeps the fingerprint (SHA-256 of the content) of each batch applied in a hidden control sheet inside the workbook itself (by default `_qa_batches`, configurable in `batch_control.sheet`). Applying the same batch again returns `{"applied": False, "reason": …}` and changes nothing.

The fingerprint only recognises **equal** content. A batch edited after being applied (e.g. fixing a typo in the actual result) would have another hash and be applied again: repeated notes in the notes column and new defects duplicated. So the control sheet also keeps the batch's identity (`batch_id`; `apply` uses the file name without the extension), and a batch with a `batch_id` already applied but a different content is **refused** (code 4 in `apply`, nothing written). A correction goes in a new batch, with another name, with only the cases to correct (and with `bug` instead of `new_defect` for a defect already created). Old control sheets, without the `batch_id` column, get it at the next application. Control sheets written before the translation (plan 8.11: `_qa_lotes`, with the headers `aplicado_em`, `etiqueta`, `itens`, `defeitos_novos`, `lote_id`) are workbook data: the adapter reads them as they are and keeps writing under those headers; `scripts/migrate_workspace.py` pins the sheet's name in the project's configuration. A project `scripts/qa_lib.py` that does not do this check leaves the responsibility to whoever runs it: do not apply again a batch already processed (the workspace's `context.md` records how far it got — methodology §12.2).

The adapter's cross-check also checks that each defect points to a case that exists (only text starting with a configured case prefix counts as a reference).

**Summary vs direct count.** The formulas of a summary sheet can be wrong without anyone noticing: counting the wrong column, with a fixed range that no longer reaches the last row, or without a new area. So the adapter **never uses the summary as the source of the totals**: the dashboard and `apply` count the statuses row by row. When the optional block `excel.summary` exists, `check_summary()` compares the summary's **calculated** values (read from the saved file, with `data_only=True`) with that direct count, per area, per status, in the area's total and in the total row, and reports each difference. The calculated values only exist after a recalculation (`openpyxl` saves formulas without cached values); without them, the check says it cannot compare, without inventing numbers. In `apply`, it runs after the recalculation: if the summary does not match, it exits with code 5 (saved and recalculated, but the workbook's summary is wrong: fix the formulas, never the numbers by hand). The dashboard shows the same warning.

If the summary has a % run column (`summary.percent_column`), it is also compared, per area and in the total row, with the methodology's definition (§16.3): **(total − cases in `not_run_status`) / total**, with N/A and Blocked counted as run, and 0 in an area without cases. It accepts the fraction (0.2857 with a % format) or percentage points (28.57). The default tolerance is strict (0.001 percentage points), so as not to let through a formula that excludes the N/A in a large workbook; if the summary rounds on purpose, `percent_tolerance` (in percentage points) says by how much. The `not_run_status` must be the same status the `dashboard:` section treats as pending.

### 5.1 Result identity and partial batches (plan Q1)
Every result has two identities: the **result** (`result_key`: case, status, observed text and time of execution; evidence, bug
links and the recording time are not part of it) and the **execution** (`execution_key`: case and time of execution). The
control sheet keeps both for what each batch applied (`result_keys`, entries `<result>:<execution>`; a control row from before
the column is rebuilt from `runs/<batch_id>.json`; a row of add-cases or annotate is recognised by its file and its content hash
and applied no results). `apply_batch` refuses, writing nothing:
- a result already applied from ANOTHER batch (the full batch and its partial batches, or two partial batches);
- the **correction** of an execution already applied (same case and time, another status or observed text) that does not
  carry `revision_of` pointing at the batch that applied it: correcting the status or the text is not a new execution (a
  correction is recorded as a revision, `run_log.py resolve --into`); the same case executed at ANOTHER time is a new execution;
- any application while a control row is **unverifiable** (no `result_keys` and no run log to rebuild them, and not a row of
  cases or notes): duplicates cannot be ruled out against it. The dry run names its origin (batch id, time, items) and the
  apply is blocked until the run log is restored (it is in Git) or the row is explained.
**Results without a time of execution.** No time is ever invented. A result without `ts` is accepted when the case has nothing applied
yet; when the case already has an applied result (with or without a time) a new execution cannot be told from a correction, so the
apply refuses it unless it carries `revision_of` or gets a time of execution with its source (`run_log.py add --executed-at
--executed-source`). A result WITH a time for a case whose applied execution has no time is accepted only if it is later than the
moment that application was written (a later one is certainly a new execution), else it needs `revision_of`. Old data stay as they
are: an applied result without a time is kept with an empty execution key and judged only when a new result for its case appears.
**Origin of an old control row.** A row without `result_keys` is rebuilt from `runs/<batch_id>.json` only when that file is PROVEN to be
what was applied (the hash of its results equals the row's hash, or its `translated_from`); a file that exists but does not hash to
the row is `divergent` and the row is unverifiable. With the keys stored, a run log edited later is reported as a warning and the
stored keys still count. To lift an unverifiable row: restore the exact file (Git) or `python -m adapters.excel.reconcile
<workspace> <batch_id> --from <file> --dry-run`, then `--expect <token>`: it writes only the `result_keys` cell of that row and
only for a file proven by the hash; it never accepts an origin it cannot prove (an explicit, recorded acceptance of an unproven
origin is a proposal, not implemented). To apply part of a batch: `run_log.py subset` (copies unchanged, records `derived_from`).
The first write with this version adds the column `result_keys` to the control sheet (header and the new row only); older rows stay
empty.

## 6. Export for other teams

When the official workbook is not the format another team (e.g. development) needs to look at, a derived, disposable export is generated from the official workbook — never the other way round. The official workbook stays the single source of truth (methodology §1.3); the export can always be regenerated.

## 7. Portability between environments

A project's `qa_lib.py`/adapter tends to be born in a concrete session (Claude.ai, Cowork, local Claude Code) and to accumulate dependencies specific to that environment — paths of internal scripts, recalculation mechanisms, libraries only installed there. When bringing that adapter into local Claude Code:

1. Confirm its environment dependencies (usually, the recalculation — §2).
2. Rewrite only those parts for the new environment.
3. Keep the business logic (sheet/column mapping, cross-check rules) unchanged, and validate with the methodology's golden test (§15) before trusting the ported version.

---

## Origin

This document generalises two lessons and a workflow discovered during a real test session in another project (a Cowork session of September 2026), originally documented in a `qa_lib.py` and `README.md` specific to that project. The code and the concrete sheet/column/status names of that project stay in its workspace — only what is reusable in any project with Excel workbooks is here.
