# Design 17.3b — applying part of a batch (`apply --only/--skip`)

> Status: **design only (09.11.2026). Nothing is implemented**; it does not block 17.4. Decision needed before code.

> **Status update (11.11.2026):** still **not implemented**, and not planned. The need (applying part of a batch without rewriting
> an applied one) was met another way: `run_log.py subset <source> <new-batch> --case TC-X ...` (plan Q1, 10.11.2026) writes a
> NEW batch with the named cases, and the apply refuses a result already applied from another batch. This document is kept as
> the design of the alternative that was not taken.

## Problem
A real session had a batch whose four results were ready but one case's text needed a decision. Today the batch is applied
whole or not at all. Splitting the run log by hand rewrites results (forbidden: applied batches and results are immutable).

## Why it is not a small flag
A batch is «applied» **by its name**: the control sheet `_qa_batches`, `run_log` (the applied check), `qa_report.py` (applied
vs pending, totals) and the apply's own «already applied». A batch applied with a subset would make the left-out results
look applied.

## Proposal
1. **Selection**: `apply <batch> --only TC-A,TC-B` or `--skip TC-C` (mutually exclusive; unknown ids refused; an empty
   selection refused). Everything else is validated as today, **on the selected results only**; the left-out ones are not
   validated for the apply but are listed.
2. **Record**: the control-sheet row gets `scope = partial`, the applied case ids and the left-out ids; a whole apply keeps
   `scope = full` (a missing value in older rows reads as full).
3. **Idempotency**: «already applied» becomes per **(batch, case)**: a second apply of the same batch applies only the cases
   not yet applied; a case already applied is refused with the row that applied it; nothing is applied twice.
4. **Pending cases**: the left-out cases stay in the run log unchanged and are reported as `pending application` (never as
   applied, never as Pass in the workbook). The batch is «applied» only when every result is applied; until then it is
   «partially applied» (N of M).
5. **Token**: `v2-` token additionally covers the mode (`only`/`skip`) and the sorted selection; the dry run prints it and
   the review file lists applied and left-out cases. A token from a full dry run is refused for a partial apply and vice
   versa.
6. **Report reconciliation**: `qa_report.py` counts workbook totals from applied cases only; a partially applied batch shows
   «applied N · pending application M» per platform; the audit lists the left-out cases; the report's totals still
   reconcile (applied + pending = results of the batch).
7. **Resume**: a later `apply` of the rest needs its own dry run, token and approval (the first approval does not extend).

## Open decisions
- Is a case-level «applied» marker kept in the control sheet (one row per apply) or derived from the notes? Proposal: one
  row per apply, with the ids.
- May a `Question` be left out silently? Proposal: it is always listed as left out, never counted as applied.

## Tests to write first
Partial apply writes only the selection; repeat is refused per case; the rest applies later with a new token; a full token
does not authorise a partial one; the report reconciles; an old control sheet reads as full; the lock and atomic
replacement of 17.3 still hold.
