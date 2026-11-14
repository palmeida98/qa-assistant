# Guarded Azure DevOps writes (ado_write_guard)

`scripts/ado_write_guard.py` is a small MCP server (stdio) that is the only write path an agent gets to Azure DevOps. The official `@azure-devops/mcp` server stays read-only for the agent (its write tools are denied in the client and its credential is read-only).

## The rule it enforces
A write happens only if the work item is in the configured organisation and project **and** `System.AssignedTo.id` equals the verified identity id (a GUID; display names are never compared). Before each write it reads the item, checks, verifies the content hash the user approved, reads again, and refuses on any difference or any error.

| Operation | Behaviour |
|---|---|
| Comment | One comment on one item; `text_sha256` must match the text; an identical existing comment blocks the write |
| Comment preview (`ado_guard_preview_comment`) | **Read only.** Runs the same checks as the comment write on the exact text and says whether it would be written, and why not |
| Markdown rendering (`ado_guard_render_markdown`) | **Read only, local, no network.** Deterministic Markdown to HTML of a draft, with the SHA-256 of the HTML |
| Attachment | One file from the allowed folder (png, jpg, pdf, txt, md ≤ 10 MB; video mp4, mov, webm ≤ 60 MB with a review record `<file>.review.json` next to it, see «Video evidence»; symlinks and paths outside refused); uploaded and linked in one call, the link carries a JSON Patch `test` on `/rev` |
| Bug creation (`ado_guard_preview_bug`, `ado_guard_create_bug`) | See «Bug creation» below: **DISABLED by default**; one bug, born **unassigned** (13.11.2026), under a PBI or QA task assigned to the verified identity, with only the listed fields |
| Other links, field updates, state changes, Assigned To changes, deleting, batches, unknown tools | **Not exposed** |

## Bug creation (plan C, 10.11.2026; disabled by default)
**Switch:** the launcher must set `ADO_GUARD_ALLOW_BUG_CREATE=1`; without it `ado_guard_create_bug` answers «DISABLED» before reading anything, and `ado_guard_status` shows `bug_creation: disabled`. The preview (`ado_guard_preview_bug`) only reads and works either way.
**Fields (closed schema, `additionalProperties: false`):** `parent_id`, `title` (one line, ≤ 255), `repro_steps`, `system_info`, `acceptance_criteria` (optional), `extra_fields` (only the keys the workspace maps, «Final preview» below), and for the creation `payload_sha256` (from the preview) and `patch_sha256` (optional unless the workspace requires it); the preview also takes `attachments` and `creation_comment`, only to list them. There is no parameter for a URL, method, project, type, area, iteration, state, assignee, severity, priority, tags or relations.
**Checks, all before writing:** the payload hash equals the SHA-256 of the canonical JSON of what is sent; the parent is read and is a `Product Backlog Item` or a `Task` whose title starts with «QA», in the configured project, not Removed, **assigned to the verified identity by stable id**, with an area and an iteration; the process's `Bug` type (`GET …/workitemtypes/Bug`, read on 10.11.2026 for the configured project: it has the four fields, and the only required fields without a default are the title and the area and iteration ids, which the paths set; State, Value Area and the DEV/QA/PREPROD/PROD flags have defaults) has the fields written, requires nothing else without a default and neither requires nor defaults `System.AssignedTo` (read on 13.11.2026: `alwaysRequired: false`, `defaultValue: null`; otherwise the bug would not be born unassigned and the creation is blocked); no bug with the same normalised title under the same parent or anywhere in the project (WIQL as a read with the read credential); the parent's revision is the same when read again right before the request.
**The request:** one `POST …/_apis/wit/workitems/$Bug?api-version=7.1` (`application/json-patch+json`), built from constants, with the fields, **no `System.AssignedTo`** (13.11.2026: omitting the field is the supported way to leave it empty; sending an empty value is not needed), the parent's `AreaPath` and `IterationPath`, and one `System.LinkTypes.Hierarchy-Reverse` relation to the parent. The assignee is never set or changed afterwards: there is no tool that updates a field.
**Atomicity is NOT PROVEN** (`ATOMICITY_PROVEN = False`): the Microsoft documentation shows a create body that carries a relation, not a statement that the fields and the link are committed together. So the result is always **read back** (type, project, **no assignee** (a process rule that assigned someone is reported as `born WITH an assignee`), the four fields, area, iteration, and exactly one Parent link to the right parent). Rich-text fields are compared ignoring only the whitespace Azure DevOps adds before closing tags (seen on two real bugs on 13.11.2026: a space before `</div>`, `</li>`, `</ol>`, `</ul>`); such fields are listed in `html_normalised_by_azure_devops`, any other difference is a problem. The creation result always carries the bug's id, URL, revision, **real state** and **real assignee** as read back (`unassigned`, `the verified identity` or `another identity (name)`), also after an uncertain answer when the bug is found, so a bug that exists is never created again. Limits: a process default on Assigned To is caught before the request (the Bug type check); a rule that assigns someone while the bug is saved is only detected in the read-back (the bug already exists); an assignment made later by another automation or person is not seen by the guard at all. Server version 1.2.0 (1.1.0 before the final preview of the fields). A bug created without the parent link, or different from what was approved, is reported with its id and `CREATED BUT NOT AS APPROVED`: nothing is repeated, corrected or deleted. This is why the creation stays disabled until the user accepts that limit or a test in a sandbox project shows the behaviour.
**Failure semantics:** 4xx = not created (stated, nothing repeated); a lost answer (no HTTP response), a 5xx or an answer without an id = UNCERTAIN: the guard looks for the bug by title (read only) and reports `found after an uncertain answer`, `not found` or `unknown`, and never sends the request again; a bug that exists is stopped from being created twice by the duplicate rule.
**Comments and attachments** on the new bug: each is still one operation with its own approval and hash. Because the bug is unassigned, the rule «assigned to the verified identity» has ONE narrow exception (13.11.2026), used by `check`, `add_comment`, `preview_comment` and `attach_file`: the item is in the guard's **ledger of bugs it created** (`--created-bugs-file` / `ADO_GUARD_CREATED_BUGS_FILE`: bug id, parent id, time and payload hash, no content; without the file the ledger lives only in the process), is still a `Bug`, still has **no** assignee, was created by the verified identity (`System.CreatedBy.id`), still has as `System.Parent` the parent recorded at creation, and that parent passes the normal check (assigned to the verified identity; never the exception again, so a ledger cycle cannot recurse). `check` reports `assigned_to_verified: false` and `allowed_as`. Every other unassigned item stays refused; once anyone assigns the bug, the normal rule applies. An unreadable ledger opens nothing.

## Final preview of a bug's fields (13.11.2026)
**Why:** the preview returned only the JSON Patch, and the guard could write only the four standard fields. A project whose
Bug form has its own fields (for example separate rich-text fields for the actual and the expected result) had its content
moved by hand after the creation, so what the QA approved was not what reached those fields.

**The project's mapping lives in its workspace**, in `project.yaml`, section `write_guard.bugs` (same shape as
`azure_devops.bugs.fields`: model key -> reference name), read when the launcher passes `--project-yaml <workspace>/project.yaml`
(or `ADO_GUARD_PROJECT_YAML`). Without it the guard behaves as before (the four standard fields, the same hash).
```yaml
write_guard:
  bugs:
    labels: {repro_steps: "Repro Steps", system_info: "System Info"}   # (opt.) the name shown in the Board, per model key
    fields:                                                           # (opt.) extra fields: model key -> reference name
      actual_result: {reference: "Custom.<confirmed name>", label: "<label in the form>", required: true}
    checks:                                                           # (opt.) what each field must contain (regex, case-insensitive)
      repro_steps: [{name: "preconditions", pattern: "Pré-condições:"}]
    notices:                                                          # (opt.) said in the preview when present
      system_info: [{name: "QA account to confirm", pattern: "por confirmar"}]
    require_patch_hash: false                                         # (opt.) true: the creation needs patch_sha256
```
- An extra field's reference name must be one **confirmed** in the project's form (the guard also checks that the Bug type
  has it, before any write). Management fields are refused as extra fields: `System.*`, Assigned To, State, Priority,
  Severity, Found in / Integrated in Build, and the guard's own four fields.
- `extra_fields` (an object of model key -> text) is accepted by `ado_guard_preview_bug` and `ado_guard_create_bug` only for
  the configured keys; a required one missing is refused. Values follow the rules of the other fields (length, no control
  characters).
- **No personal addresses nor credentials in a bug:** an e-mail address, or a `password`/`senha`/`pwd`/`token`/`pat`
  followed by `:` or `=`, in any field is refused (preview and creation), for every project. The QA account appears by its
  alias or role, or as «to confirm».

**What the preview returns** (read only, the same code as the creation): `fields_preview`, built from the very patch the
creation sends, one row per field in the order sent, with the **label shown in the Board**, the **reference name** and the
**exact value**; the Parent (id, type, title, revision) and the relation; `Assigned To: empty (born unassigned)`; the area
and iteration taken from the parent; the `checks` (each configured check, `ok` or `missing`) and `notices`; `ready`
(false while a check is missing: the creation is then refused with the same list; the checks of an optional field left
empty, the acceptance criteria of a parent without criteria or an extra field not required, are shown as «not applicable»); `payload_sha256`, `patch_sha256` and
`hash_covers`; and `associated_operations` (see below). `preview_text` is the same content as plain text for the review.

**What the hashes cover:**
- `payload_sha256`: the canonical JSON of the parent id, the title, the repro steps, the system info, the acceptance
  criteria and, when there are any, the extra fields **with their reference names** (`{key: {reference, value}}`). A
  project without extra fields gets the same hash as before. Any change of a field, of the mapping of an extra field or
  of the parent gives another hash.
- `patch_sha256`: the SHA-256 of the exact JSON Patch the creation sends (fields, area and iteration taken from the
  parent at that moment, the Parent relation). It changes when the parent moves to another area or iteration after the
  preview. `ado_guard_create_bug` accepts it (optional; required with `require_patch_hash: true`) and refuses a patch
  that differs.
- Not covered: the attachments and the creation comment, which are separate operations.

**Approval and its renewal:** the QA approves the preview shown (its `payload_sha256` and `patch_sha256`). A change of a
field, of the mapping or of the parent link after that, or a parent that moved, gives other hashes: the creation is
refused, a new preview is made and approved again. A check missing keeps `ready: false`.

**Associated operations:** `ado_guard_preview_bug` also accepts `attachments` (paths in the allowed folder) and
`creation_comment` (text), only to list them: each file with its name, size, SHA-256, whether it would be accepted and,
for a video, its review record (`reviewed`, `personal_data`); the comment with its `text_sha256`. They are **not** part
of the bug's hashes and **not** sent by the creation: after it, each goes through `ado_guard_attach_file` and
`ado_guard_add_comment` with its own approval (the comment's hash is the one shown). A video goes only when the captures
cannot show the occurrence, with its review record.

## Video evidence (13.11.2026)
A video is attached only when it is needed to understand the defect (a sequence, timing, a transition or an intermittent error that captures cannot show). The guard accepts `.mp4`, `.mov` and `.webm` up to 60 MB (Azure DevOps accepts up to 130 MB in one upload; a demonstration clip should be short) and only with a review record next to the file, `<file>.review.json`, with `"reviewed": true` and `"personal_data": "none"` or `"masked"` (free fields such as `reviewed_utc`, `notes` are allowed): someone watched the clip for personal data and credentials before it goes. The record is a forcing step, not proof: the person who writes it is responsible for the review.
**Client approval (`settings`):** both tools are `ask`, never `allow`:
```json
{
  "permissions": {
    "ask": ["mcp__<server>__ado_guard_create_bug", "mcp__<server>__ado_guard_preview_bug",
            "mcp__<server>__ado_guard_add_comment", "mcp__<server>__ado_guard_attach_file"]
  }
}
```

## Preview before approving a comment (`ado_guard_preview_comment`)
Closed schema `{work_item_id, text}`. It reads the item and its comments (read credential, `GET` only) and returns `would_write` (`true`/`false`), `reasons`, `duplicate` (`true`/`false`, `null` when the comments could not be read), `text_sha256` (SHA-256 of the exact text, no trimming: this is the value `ado_guard_add_comment` needs), `length` and the item's type, state and revision. It never writes.
**It cannot diverge from the write:** both tools call the same private method (`_comment_gate`): text length, item in the project, assigned to the verified identity by stable id, comments readable, identical comment (compared on the trimmed text). The write adds only what is not a property of the text and the item: the write credential and the approved hash. The preview reports a missing write credential as a reason but still runs the checks. There is no rule on the item's state or on the title of a Task for comments: none exists in the write path, so the preview does not invent one (it shows the state for information). Only the first 200 existing comments are read (as in the write).

## Markdown helper (`ado_guard_render_markdown`, `markdown_to_html`)
A pure function with the standard library only; the same input always gives the same output. Paragraphs separated by a blank line become `<div>` (a line break inside one is `<br>`); `**bold**` becomes `<b>`; `` `code` `` becomes `<code>` (nothing inside is interpreted; bold may contain code); `-` or `*` followed by a space starts a bullet list (`<ul><li>`), `1.` or `1)` a numbered list (`<ol><li>`, the numbers themselves are not kept). **All text is HTML-escaped first**, so no markup can pass through. Not supported, left as plain text: headings, links, images, tables, nested lists, single `*` or `_` emphasis.
It is **never applied by `ado_guard_add_comment`**: the user approves an exact text, and that exact text is what is sent. The tool returns `html`, `html_sha256` and `within_comment_limit`, so a draft can be reviewed and its HTML approved (and hashed) before the comment is written. It makes no request.

## Publication reports (optional, off by default)
`--report-dir <folder>` or `ADO_GUARD_REPORT_DIR` (the option wins). When set, each **successful** comment or attachment writes ONE small JSON file `<UTC timestamp with microseconds>_<work item>_<comment|attachment>.json` (created exclusively: an existing file is never overwritten) with: work item id, kind, `comment_id` or `file_name` plus `file_sha256`, `text_sha256` of the text sent, `readback_equal` (the stored comment read again equals the text sent; `null` if it could not be read) and `readback_sha256`, `revision_before` and `revision_after` (`null` if the re-read failed) and the UTC time. **Never** a credential, the text, the file content or the file path. Reports need two extra reads (the comment and the item), made only when reports are on. The folder is created if missing; if the report cannot be written the operation still answers `ok: true` (the write already happened) and adds `report_error`. Blocked operations leave no report. `ado_guard_status` shows `publication_reports` (the folder, or `off`).

## What it does not do (real scope of the protection)
- **Azure DevOps cannot enforce "only items assigned to me".** Permissions are by project/area/iteration, not by assignee. The only ADO-side limits are the credential's scope and lifetime.
- The guard is a local layer. A credential able to write can write elsewhere if used outside this server. Keep it out of project files and out of the agent's reach (macOS Keychain read by the launcher only). Client permission rules (`ask`/`deny`) are instructions to Claude Code, not a technical barrier against a determined user-level process.
- A comment POST has no revision precondition: the re-read narrows the window between check and write, it does not remove it. Attachment links do use the revision test.
- An upload whose link step is refused stays as an unattached blob (not visible on any item).
- Per-call human approval comes from the client (`ask` rules); the content hash only binds what was approved to what is sent.

## Credentials
**Hygiene at startup.** Whitespace at the ends of a value is stripped (a paste often adds it). Whitespace or a control character **inside** a value means it was damaged or is two values glued together: it is refused, never repaired. A refused write credential leaves the guard running with writes disabled; a refused read credential is simply not used. `ado_guard_status` reports `write_credential` and `read_credential` as `present`, `absent`, `rejected: contains whitespace inside` or `rejected: contains control characters`; never the value, a part of it or its length (the startup log names the problem only). A refused credential is still added to the redaction list.
**Expiry visibility.** `ADO_GUARD_WRITE_PAT_EXPIRES` and `ADO_GUARD_READ_PAT_EXPIRES` (an ISO date `YYYY-MM-DD`; a date is not secret) make `ado_guard_status` report `credential_expiry` per credential: `{"expiry": "<date>", "days_left": n, "warning": bool}`. `warning` is `true` when 3 days or fewer are left or the date has passed; an expired date also adds a `note`. It only reports: nothing is disabled by a date (the real expiry is Azure DevOps's, the date is what the operator says). An unreadable value gives `{"expiry": "invalid date"}`; none gives `{"expiry": "not configured"}`. Days are counted in UTC.

Writes use `ADO_GUARD_WRITE_PAT` (a separate PAT, scope Work Items: Read & write, shortest practical expiry). The checks may use `ADO_GUARD_READ_PAT` (the existing read-only PAT) so the read path can be validated before a write credential exists. Without a write credential, writes answer "disabled". Nothing secret is on a command line or in a log (redacted).

## Tests
Hardening of 10.11.2026 (in `tests/test_ado_write_guard.py`, fictional, injected clock and temp folders): `CredentialHygiene` (every kind of inner whitespace and control character, the ends still stripped, writes disabled and nothing sent, the read credential refused and unused, no value or length in the status or the log), `PreviewComment` (never writes, the same fixtures give the same verdict and the very same reason as `ado_guard_add_comment` in 14 situations, duplicate, no write credential, invalid input, closed schema), `MarkdownHelper` (escaping, bold and code, lists, unbalanced markers, CRLF, determinism, the tool and its hash, add_comment sends the text unconverted), `PublicationReports` (comment and attachment reports, no text or content, read-back equal/different/failed, a report that cannot be written, no overwrite, off by default, option and environment), `CredentialExpiry` (thresholds, expired note, invalid and absent dates, UTC, environment).
`tests/test_ado_write_guard.py` (fictional, in-memory fake of the REST calls): assigned to me, assigned to another (also same display name with another id), unassigned, ambiguous identity, other project, read errors, assignee changed between check and write, hash mismatch, duplicate comment, upload+link with revision test, nothing uploaded when blocked, path/symlink/type refusals, batches and extra arguments, unknown and dangerous tool names, protocol round trip, no credential in output or logs, read/write credential separation.
`tests/test_ado_guard_bugs.py` (`FinalPreview`, 13.11.2026: the preview equals the request sent, field by field with its label; extra fields only from the mapping, required ones, a form without the field, management fields refused in the configuration; an account to confirm is a notice; e-mail addresses and credentials refused in every field and in the comment, also without a mapping; a missing check keeps `ready: false` and refuses the creation; a field, its mapping, the parent or the parent's iteration changing after the approval is refused until a new preview; attachments, a reviewed and an unreviewed video and the comment listed apart and outside the hashes; a project with the standard fields keeps its hash and behaviour; the launcher option and a bad section that stops the start) (46 tests before it, fictional, an in-memory fake that can drop the Parent link, lose an answer, refuse or fail): disabled by default and the launcher flag; the preview only reads; the schema is closed and no dangerous parameter exists; bad values; hash binding; parent of another person, without assignee, of another project, of another type, a non-QA task, Removed, without area or unique name; the parent changing before the request; the Bug type without a field or with a required field without default; duplicates (same parent, other parents, case and spaces, a task is not a bug, search failure, quote escaping); the single request, its exact URL and patch paths, no assignee in the request (13.11.2026) and a Bug type that defaults or requires Assigned To blocks the creation; only reads, one WIQL read and one creation (no PATCH, PUT or DELETE); read-back; a server that drops the link; another assignee; 4xx not repeated; lost answer, 5xx and failed search after it; no second creation; comments on the new bug need their own hash; the unassigned new bug accepts attachments and the creation comment through the ledger (memory and file, across a restart), and every other unassigned item is refused (not in the ledger, not a Bug, another creator, another parent, parent reassigned or unassigned, ledger cycle, unreadable ledger, assigned afterwards); whitespace before closing tags is not a problem, any other difference is; video: reviewed clips attached, missing or invalid review record refused before any write, 60 MB vs 10 MB limits, other extensions refused; both tools are `ask` and not `allow` in this document.

## Opt-in probe at startup
`--probe-on-start` (or `ADO_GUARD_PROBE_ON_START=1`) makes the server do ONE read with the write credential when it starts, to say early whether
it authenticates as the verified identity. The result is compact (HTTP status, classification, whether the identity matches, the time; no
credential facts), is logged and appears in `ado_guard_status` as `startup_probe` (`not run ...` otherwise). It is off by default (no network call
at startup) and it never stops the server or changes what is allowed: a failure is a result. `ado_guard_probe` remains the manual, detailed probe.
