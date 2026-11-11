# Trackers: what is implemented, what is validated, what is pending

**Snapshot of 08.11.2026, validation state updated 11.11.2026.** The three trackers are part of the product: Excel, Azure DevOps (Test Plans + Boards) and Jira
with Xray. Code written against fakes is not a validated integration: this page keeps the two apart, and says what a real
pilot needs. It does not authorise any write to a real instance; each real write needs a test environment identified,
a preview, and the user's approval of that exact set.

| Capability | Excel | Azure DevOps | Jira + Xray |
|---|---|---|---|
| Read cases | The workbook itself (`load`) | `import-cases` (read only) | `import-cases` (read only) |
| Add approved cases | `adapters.excel.add_cases` (dry run, then `--yes`) | `export-cases` (dry run, then `--yes`) | `export-cases` (dry run, then `--yes`) |
| Results | `adapters.excel.apply` (`--dry-run`, then `--expect <token>`) | `adapters.tracker apply` (dry run, then `--yes`): one test run per batch | `adapters.tracker apply`: one Test Execution per batch |
| Bugs | `new_defect` / `bug` in the run log, applied with the results | created or updated with the results; duplicates through `bug-search` | the same |
| Read back after writing | the workbook is reloaded and compared | yes (exit 5 on a difference) | yes (exit 5 on a difference) |
| Same batch twice | detected, no effect | detected, no request | detected, no request |
| Interrupted send resumed | not applicable (one local write) | yes, by the run's marker, no duplicate | yes, by the execution's marker, no duplicate |
| Concurrent edit | the preview token refuses a stale apply | exit 2, no write (`test` on `/rev`) | exit 2, no write |
| Wrong or missing credential | not applicable | exit 3 / 4, the token never printed | exit 3 / 4, the secret never printed |
| Proposed bug | refused until approved | refused until approved | refused until approved |

## Validation state

| | Implemented | Tested with fakes / fictional golden | Piloted on a real instance |
|---|---|---|---|
| Excel | yes | yes: unit tests, `scripts/golden_check.py` (LibreOffice) | **yes**: used on real projects (their data stays in their workspaces) |
| Azure DevOps, adapter (`adapters.tracker`): import and export of cases, results (one test run per batch), bugs, duplicate search | yes | yes: `tests/test_azure_devops_adapter.py`, `test_azure_devops_golden.py`, `test_tracker_*` | **no** |
| Azure DevOps, guarded MCP server (`scripts/ado_write_guard.py`): **one comment** or **one attachment** per call | yes | yes: `tests/test_ado_write_guard.py` (fake transport) | **yes**, on one real project: see "Real use of the guarded writes" below |
| Azure DevOps, guarded server: read-only previews (`ado_guard_preview_comment`, `ado_guard_preview_bug`) | yes | yes: `tests/test_ado_write_guard.py`, `test_ado_guard_bugs.py` | a real **read** only (it exposed the `$expand=relations` response shape, fixed in the changelog) |
| Azure DevOps, guarded server: **bug creation** (`ado_guard_create_bug`) | implemented, **disabled by default** | yes: `tests/test_ado_guard_bugs.py` | **no**: never enabled; atomicity of the create-with-parent request is not proven |
| Jira + Xray | yes | yes: `tests/test_jira_adapter.py`, `test_jira_golden.py`, `test_tracker_*` | **no** |
| Ticket reading (`/analyze-ticket`) | Azure DevOps and Jira, read only | yes: `tests/test_tickets*.py` and the demo's bundles | Azure DevOps through a logged-in browser: a real read was done; Azure DevOps by token and Jira: no |

### Real use of the guarded writes (Azure DevOps)

What the code enforces is in [technique-ado-write-guard.md](technique-ado-write-guard.md); what is *evidence* is kept apart here.

- **In this repository (verifiable):** the tool list of `scripts/ado_write_guard.py` (nine tools: status, probe, check, comment,
  comment preview, Markdown rendering, bug preview, bug creation, attachment), 90 tests of the guard on a fake transport plus
  the bug-creation tests, and one fix in the changelog that came from a **real read** (the bug-creation preview was blocked for
  every parent until the id was read from the top level of the response).
- **Outside this repository (the author's statement, not reproducible here):** comments and attachments were written with the
  guard on one real project, each after a preview, a hash of the exact text or file, and a confirmation per call. The records of
  those uses (item ids, texts, evidence, the read-backs) stay in that project's private workspace, so this page does not
  reproduce them and nobody can check them from here.
- **What that real use does not cover:** results, case import/export and bug creation (the first two belong to the adapter, the
  third is disabled); any second project or organisation; another credential type; permission failures against the real service.
- **Not a permission model.** The guard is a local safeguard: Azure DevOps does not limit writes per assignee, and the write token
  would write elsewhere outside this server.

Reproduce what is claimed as tested (no network, no credentials):

```bash
python -m unittest tests.test_tracker_cli tests.test_tracker_config tests.test_tracker_http \
  tests.test_azure_devops_adapter tests.test_azure_devops_golden tests.test_jira_adapter tests.test_jira_golden \
  tests.test_excel_dry_run tests.test_add_cases tests.test_tickets tests.test_tickets_memory tests.test_tickets_audit
python scripts/golden_check.py          # the Excel golden; needs LibreOffice
```

The tests cover, per tracker: the dry run that resolves everything and writes nothing, `--yes` with the read back, the
statuses that are not sent (`Question`, `Not run`), chunking, an uncertain create found again by its marker and never
repeated, a request that never arrived sent on the next run, a conflict with no write, a wrong token, the same batch
twice, the import following every page, and the round trip import → export → import.

## What a real pilot needs (Entrega B: **blocked** until these exist)

Nothing below is done, and none of it is reused from another project.

- **Azure DevOps:** a practice organisation and project with Test Plans for the user who runs it, a PAT with *Work Items
  (read & write)* and *Test Management (read & write)* (`pat_var` in the workspace's `.env`, never in a chat or a
  repository), an existing plan and suite, and a workspace outside this repository. Process Agile/Scrum/inherited
  (`Basic` and CMMI are refused by `check`).
- **Jira + Xray:** a Jira Cloud site with Xray Cloud (a trial is enough), an Xray API key (client id and secret) and a Jira
  API token, a practice project, and a workspace outside this repository.
- **The sequence**, per tracker, each write previewed and approved as a set: `check` (read only) → `import-cases` (read only)
  → `export-cases` dry run, approval, `--yes` → `apply` dry run, approval, `--yes`, read back → the same batch again (no
  request) → an interrupted send resumed (no duplicate) → `bug-search` for a duplicate → a missing permission (the token
  without write scope): the exit code and the message. `--record` keeps redacted exchanges in the workspace for a real golden.
- **Evidence to keep, in the workspace:** the preview shown, the approval, the read back, the recordings. The framework
  gets only generalised corrections and fictional examples.

Until then the honest state of the Azure DevOps adapter writes (results, cases, bugs) and of every Jira + Xray write is
**implemented, tested with fakes, pilot blocked**. The guarded comment and attachment writes are the one exception, with the
limits stated above.
