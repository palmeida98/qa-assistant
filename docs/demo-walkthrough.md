# A reproducible walkthrough on the fictional demo

Everything below runs offline on a temporary copy of [`examples/demo`](../examples/demo) (a fictional workspace: the
Demo Shop, public practice sites, no real client). It needs no credentials, no tracker, no device and no LibreOffice.
The same commands are run by `python -m unittest tests.test_demo_walkthrough`, so this page cannot drift from the code.

It shows the **engine** the skills call: the preflight, the ticket memory, the run log, the dry run that precedes any
write, and the reconciled totals. It does not show the agent deciding what to do: that is Claude Code, driven by the
skills, and its behaviour is not proved by these commands (see [skills-evals.md](skills-evals.md)).

From the root of the repository, with the Python of `requirements.txt` active:

```bash
WORK="$(mktemp -d)/demo" && cp -R examples/demo "$WORK"
```

## 1. Preflight (read only): which project, which tools, which route

```bash
python scripts/workspace_select.py preflight --skill run-tests --workspace "$WORK"
```

Prints the project, the configuration check, the tools found or missing (a missing tool makes that route's cases
`Blocked`, never a guess), one `Route:` per active module and the writes and approvals the skill has. Nothing is written.

## 2. Ticket analysis: what changed since the last check

The demo's `tickets:` source uses the browser route, so a bundle read in a session goes through `ingest`. Two fictional
bundles of the same PBI, a few days apart, are in `examples/demo/tickets/`.

```bash
python -m adapters.tickets "$WORK" ingest "$WORK/tickets/bundle_check1.json" --now 2026-11-02T10:00:00
python -m adapters.tickets "$WORK" ingest "$WORK/tickets/bundle_check2.json" --now 2026-11-05T16:00:00
```

The first check is `first`; the second is `changes`: the PBI's state and acceptance criteria changed, a comment was
added, and its QA task changed. The engine compares; it never interprets the ticket text. The result is in
`$WORK/memory/` (the references and a dated record of each check), never in a tracker. `/analyze-ticket` and
`/test-pbi` use this memory; a real project uses `access: api` or a logged-in browser instead of these bundles.

## 3. The run log: a pass, and a failure with a proposed bug

```bash
python scripts/run_log.py "$WORK" add poc01 TC-CHK-002 Pass \
  "Remove button on the cart page took the product out and the badge went to 0" \
  --environment test --build walkthrough --mode "manual: documented walkthrough with fictional data"
cat > "$WORK/new_defect.json" <<'JSON'
{"title": "Login without password shows no error message", "area": "Login", "priority": "Medium", "severity": "Low",
 "role": "standard_user", "related_tc": "TC-LOGIN-004", "proposed": true,
 "description": "Expected: an error says the password is required. Observed: the form stays as it is, no message."}
JSON
python scripts/run_log.py "$WORK" add poc01 TC-LOGIN-004 Fail "No message after submitting without a password" \
  --new-defect "$WORK/new_defect.json"
```

A result is accepted only for a case of the library (`TC-…` must exist in the workbook), the run block is fixed once
the batch has results, and a bug is recorded as `proposed` until the user approves it.

## 4. The dry run: what would be written, and the guard against a proposal

```bash
python -m adapters.excel.apply "$WORK" runs/batch_poc01.json --dry-run --no-recalc
```

Exit code 4: the proposed bug is refused (`still a proposal ("proposed")`). The user approves it by removing
`"proposed"` (here: `run_log.py remove`, then add the result again with the edited file). The dry run then exits 0, lists
the cells that would change (`Cells that would change (44): …`, the new `Defects!B5`, …) and prints a **preview token**.
The workbook is byte for byte the same as before. A real apply is `python -m adapters.excel.apply … --expect <token>` (a `v2-…` token, which also covers `--label`, `--reported-by`, `--now` and `--no-recalc`: give the same ones; a token from before version 2 is refused and the dry run is repeated),
refused if anything changed since the preview, and only after the user confirms it; it is not part of this walkthrough.

## 5. The report: totals that reconcile, apart from what is not applied

```bash
python scripts/qa_report.py "$WORK" totals
python scripts/qa_report.py "$WORK" audit
```

Totals per platform and build, with `not applied yet: 2` for the new batch and `Build unknown` for the older fictional
batches, whose run block names none: an unknown build is never counted as any build.

## What this does not show

- The agent's choices (which skill, which question, whether it stops for an approval): [skills-evals.md](skills-evals.md).
- A write to a real workbook or tracker; a device; a real project. Those need the user's approvals and, for the
  trackers, a test instance: [tracker-validation.md](tracker-validation.md).
