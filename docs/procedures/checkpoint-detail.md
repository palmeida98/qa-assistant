# Checkpoint detail
> Read by /checkpoint, step 7: closing a PBI's flow and the quarantine items.

## Closing the PBI's checkpoint
- `pbi_state.py close` closes an open item, with who decided for an approval or a question.
- `pbi_state.py mark` refuses the checkpoint (exit 4) while work is open. Closing with work open is the QA's explicit
  decision only: add `--close-with-open --decided-by "<the QA>"` (what stayed open is kept).
- The dry run and its token, and the write guard's approved content, are asked and checked as in step 5.

## Quarantine
A quarantine waiting on the QA (proposed, a retest that passed, a proposed end; `python <qa>/scripts/quarantine.py <workspace> report`)
is an open item of approval for the QA, closed only after `quarantine.py decide` with the QA's confirmation
(procedures/quarantine.md).
