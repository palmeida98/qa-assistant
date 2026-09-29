# Excel technique

> **Layer 2 — Technique for automating Excel workbooks**
> Version 1.2 · 2026-09-26 · Extracted from a real `qa_lib.py`, used in a client project. Implemented in the universal adapter `adapters/excel/`; in English since plan 8.11.
>
> Project agnostic: no sheet, column or status name specific to a real workbook lives here. Subordinate to `methodology.md`.

---

## 0. Scope

Covers the automation of Excel workbooks used as the source of truth for test cases and defects (through `openpyxl`), including the safe recalculation of formulas. The implementation is the **universal adapter** `adapters/excel/base.py` (`ExcelAdapter`), which contains no sheet, column or status name: each project gives it a **configuration** with the real layout of its workbook (see `adapters/excel/example.yaml`, fictional). Sheet, column and status names live **only** in the project's workspace. Projects not migrated yet can keep a `scripts/qa_lib.py` of their own in the workspace.

The configuration lives in the `excel:` section of the workspace's `project.yaml` (fields commented in the template `docs/project.yaml`) and is read with `ExcelAdapter.from_project(<workspace>)`. At the onboarding, it is filled in by inspecting the real workbook with `python -m adapters.excel.inspect <copy.xlsx>`, which only reads the file and proposes the section: what it cannot detect safely stays as `CONFIRM: …` for the user to decide.

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

## 5. Consistency between results and defects

When regenerating the bugs view from the cases (instead of editing it by hand), the same duplicate rules of the methodology apply (§9.1, §10): one defect per distinct problem, update instead of duplicating, and a defect whose status means "resolved" follows the project's rule (remove from the active list or archive — methodology §9.3, configurable in `project.yaml`).

A batch **must never be applicable twice** without being detected — applying the same batch again cannot duplicate notes nor create defects twice. The adapter checks this automatically: it keeps the fingerprint (SHA-256 of the content) of each batch applied in a hidden control sheet inside the workbook itself (by default `_qa_batches`, configurable in `batch_control.sheet`). Applying the same batch again returns `{"applied": False, "reason": …}` and changes nothing.

The fingerprint only recognises **equal** content. A batch edited after being applied (e.g. fixing a typo in the actual result) would have another hash and be applied again: repeated notes in the notes column and new defects duplicated. So the control sheet also keeps the batch's identity (`batch_id`; `apply` uses the file name without the extension), and a batch with a `batch_id` already applied but a different content is **refused** (code 4 in `apply`, nothing written). A correction goes in a new batch, with another name, with only the cases to correct (and with `bug` instead of `new_defect` for a defect already created). Old control sheets, without the `batch_id` column, get it at the next application. Control sheets written before the translation (plan 8.11: `_qa_lotes`, with the headers `aplicado_em`, `etiqueta`, `itens`, `defeitos_novos`, `lote_id`) are workbook data: the adapter reads them as they are and keeps writing under those headers; `scripts/migrate_workspace.py` pins the sheet's name in the project's configuration. A project `scripts/qa_lib.py` that does not do this check leaves the responsibility to whoever runs it: do not apply again a batch already processed (the workspace's `context.md` records how far it got — methodology §12.2).

The adapter's cross-check also checks that each defect points to a case that exists (only text starting with a configured case prefix counts as a reference).

**Summary vs direct count.** The formulas of a summary sheet can be wrong without anyone noticing: counting the wrong column, with a fixed range that no longer reaches the last row, or without a new area. So the adapter **never uses the summary as the source of the totals**: the dashboard and `apply` count the statuses row by row. When the optional block `excel.summary` exists, `check_summary()` compares the summary's **calculated** values (read from the saved file, with `data_only=True`) with that direct count, per area, per status, in the area's total and in the total row, and reports each difference. The calculated values only exist after a recalculation (`openpyxl` saves formulas without cached values); without them, the check says it cannot compare, without inventing numbers. In `apply`, it runs after the recalculation: if the summary does not match, it exits with code 5 (saved and recalculated, but the workbook's summary is wrong: fix the formulas, never the numbers by hand). The dashboard shows the same warning.

If the summary has a % run column (`summary.percent_column`), it is also compared, per area and in the total row, with the methodology's definition (§16.3): **(total − cases in `not_run_status`) / total**, with N/A and Blocked counted as run, and 0 in an area without cases. It accepts the fraction (0.2857 with a % format) or percentage points (28.57). The default tolerance is strict (0.001 percentage points), so as not to let through a formula that excludes the N/A in a large workbook; if the summary rounds on purpose, `percent_tolerance` (in percentage points) says by how much. The `not_run_status` must be the same status the `dashboard:` section treats as pending.

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
