# Saving and applying
> Read by /checkpoint: saving the run log and context.md, the local commit, and applying a batch once the user confirms it.
> Moved from the version 1 skills (commit 7e7cabf) with their names updated (skills-guide.md §6). Step numbers are the original ones.

## Steps (from /checkpoint)

1. Read the run log's path from project.yaml (run_log). If it is not defined, use runs/.
   Never write to history/: it is immutable (methodology.md section 13).
2. Save to the run log every result of the session not saved yet: case, result, short note,
   linked bug and timestamp (section 7.1). Use the project's format, if defined. With the jira or azure-devops
   tracker the run log also needs the "run" block at the top level, {"environment": "<key of environments>",
   "build": "<version>"}: if it is missing, ask the user for the build (never guess it).
3. Cases in Question stay as Question, with the concrete question (section 8.5), until the user answers it.

### Resolving a Question
Ask the user for the final status, the reason and who decided it (and where the decision is written); never decide it.
   python <qa>/scripts/run_log.py <workspace> resolve <batch> <case> <status> --reason "<why>" --decided-by "<who>"
       [--decision-source "<where>"] [--into <new batch>]
- Batch NOT applied: the item is changed in place; its observed text, `ts`, `recorded_at` and evidence stay, and the
  Question is kept inside `resolution.original`. The audit then lists it as "Question resolved", and the batch can go
  to the dry run.
- Batch ALREADY applied (never touched): `--into <new batch>` (a free name is suggested). The new batch has the same
  observed text, `ts`, evidence and run block, `"revision_of": {"batch": ..., "tc": ...}` and the `resolution`. Apply it
  like any batch: dry run, the user's approval, `--expect <token>`. The totals count the case once; a later real
  execution is current over the resolution.
- Refused, nothing written: a result that is not a Question, one already resolved, Question as the new status, a
  status outside the vocabulary, an unknown batch or case, a name that leaves the workspace.
- Recording a decision is not applying it, and it does not replace an approval the project's rules ask for.
4. Update context.md with the structure of section 12.2: current state, execution point
   (last batch, last case), work done, decisions (and who made them), open questions,
   blockers, pending work and validation state. Update the date and time.
5. Do not rebuild the progress from scratch: add to what is already recorded (section 12.1).
5a. Keep context.md short (section 12.2): one line "Last observed build: <build> (<date>, device.py check)" in
   the current state, replacing the previous one; the build of each run stays in its run log, and the intended
   build, artefact references and hashes in project.yaml are never rewritten from it. When context.md has more
   than about 15 dated entries, python <qa>/scripts/compact_context.py <workspace> (a dry run), check the entries it
   marks [check the rule] against "Decisions made" (move a rule that is still valid there first), then --apply: the
   older entries go word for word to archive/context/, after a copy of the whole file.
6. Commit only when the user asks for it or the workspace's CLAUDE.md makes it part of the checkpoint (rules.md:
   never as a side effect of a skill). Then it is one local commit (section 12.1), only with the run log files and
   context.md (and the evidence of the batch, if any, and analysis/ and memory/ when /analyze-ticket wrote there).
   Either way, from the root of qa-assistant: python scripts/workspace_git.py <workspace> status
   and tell the user what is not committed, how many commits are not pushed (and any untracked file or file over
   50 MB).
6a. Push ONLY when the user says so: python scripts/workspace_git.py <workspace> push
   (never --force, never git push by hand). Exit 1 = refused (a public remote or one whose visibility gh cannot
   confirm, .env tracked, a file over 50 MB): show the reason; --i-confirm-private only after the user checked
   that the remote is private.
7. Do not pass results to the main workbook/tracker without the user's confirmation. With the excel
   tracker, first the DRY RUN (technique-excel.md section 1.1; writes nothing), from the root of qa-assistant:
   python -m adapters.excel.apply <workspace> <run_log>/<batch>.json --dry-run
   and show the user what it printed: the short card (and, to review every value, the file it names,
   `<batch>.dryrun.md`; `--detail` prints it all): target workbook and platform, the cells before -> after (the ones marked
   <at apply time> take the instant of the real apply), counts, new defects, problems, "already applied". A problem
   or "already applied" means no apply. Once the user confirms THAT preview, apply with its token:
   python -m adapters.excel.apply <workspace> <run_log>/<batch>.json --expect <token>
   (with the SAME --label, --reported-by, --now, --no-recalc and --keep-previous the dry run had: the `v2-` token covers them, and an
   apply with others, or a token from before this rule, is refused before any write)
   An "out of date" refusal (exit 4, nothing written) means the batch, project.yaml or a workbook changed since the
   preview: run the dry run again and ask again. Never copy the workspace by hand to simulate.
   Only continue if the apply exits with 0. The other codes:
   - 4 = invalid batch (e.g. an unresolved Question, a status outside the vocabulary, a repeated case):
     nothing was written; fix the run log with the user and apply again. Exception: if the batch HAD
     already been applied and was edited afterwards, do not apply it again -- the correction goes in a new
     batch, with another name, with only the cases to correct (and "bug" instead of "new_defect" for
     defects already created);
   - 2 = cross-check with problems: original untouched, result in .REVIEWED.xlsx; show the problems;
   - 3 = saved, but the recalculation failed: review before calling it done;
   - 5 = saved and recalculated, but the workbook's summary does not match the direct count
     (summary): show the cells to the user; the summary's FORMULAS are fixed, never the numbers
     by hand. The totals of the dashboard and of the session summary come from the direct count.
   Applying the same batch again changes nothing (the adapter detects it).
   After exit 0, the workbook is part of what step 6 commits when a commit is asked for (a second local commit,
   "<batch>: applied", so a clone on another computer has the current workbook, methodology section 12.3); otherwise
   list it as not committed. The workbook of a batch is chosen by excel.workbooks (its patterns), never by guessing;
   QA_WORKBOOK that contradicts it is refused.
7a. With the jira or azure-devops tracker, the tracker adapter sends the batch (technique-jira-xray.md section 2,
   the common contract; methodology.md section 9.4). From the root of qa-assistant, first the DRY RUN (reads only):
   python -m adapters.tracker <workspace> apply <run_log>/<batch>.json
   - 1 = something to send, not approved yet: show the user the plan as printed (results, new bugs, updated
     bugs, links, and what is NOT sent -- statuses mapped to skip or unmapped, which stay only in the run log).
     Only after an explicit approval run the same command with --yes (it resolves again and sends);
   - 0 = sent and read back, or nothing to send (a batch already sent with the same content makes no request);
   - 4 = invalid (a Question, a status that is not a key of results.statuses / results.outcomes, no "run" block,
     a new_defect without title/expected/observed, a batch already sent with ANOTHER content...): nothing sent;
     fix the run log with the user. A batch already sent is never edited and sent again: the correction goes in
     a new batch, with another name, with only the cases to correct ("bug" with the key from
     tracker/sent/<batch>.json instead of "new_defect" for bugs already created);
   - 2 = conflict with the tracker (a case, bug, test plan or test point not found, an ID in tracker/ids.yaml that
     points elsewhere): nothing written; show the problems; refresh with import-cases or fix project.yaml or the
     run log with the user;
   - 3 = network, authentication, rate limit or server error: with --yes the send may be partial and
     tracker/sent/<batch>.json has exactly what was confirmed. When the cause is gone, run the same --yes
     command again (same batch, same approval): it continues from the record and never creates twice;
   - 5 = sent, but the read back differs: show the differences to the user and record them in context.md; never
     send again to "fix" it.
   After a --yes run that wrote something (0, 3 or 5), the workspace's tracker/ (sent/, ids.yaml, cases.json) goes in
   a second local commit ("<batch>: sent") when a commit is asked for (step 6), so another computer knows what was
   sent; otherwise list it as not committed. Without the tracker's API (e.g. out of reach), the run log stays the source:
   leave the user the list to pass by hand (methodology.md section 4.2).
8. Tell the user the exact point where the run stopped. With the excel tracker, the dashboard can be built:
   python scripts/build_dashboard.py <workspace> (from the root of qa-assistant). With jira or azure-devops the
   progress view is the tracker's own reports (methodology.md section 4.2).
9. A run log from a REMOTE run (runs/batch_<name>_r<run id>.json, from a pull request of the workspace's CI;
   technique-mobile.md section 11) is never applied here: its pull request is the review (proposed bugs approved
   by removing "proposed", each Question answered). The merge applies nothing. Applying is a separate, manual step:
   the workspace's `qa-apply.yml` starts only by `workflow_dispatch` (a push to main starts nothing). Its default
   mode is a dry run: it writes nothing, refuses a run log that still has a `proposed` bug or a Question, and shows
   the review (for Excel, the cells and a preview token). `mode: apply` needs the typed approval `APPLY <batch>` and,
   for Excel, the token of the dry run the user reviewed (Jira / Azure DevOps have no token: the typed approval and
   the dry run are the guard). The result goes to a new branch `qa-apply/<batch>-<run id>`, never to main, and the user
   reviews and merges that pull request. Pull main first (docs/workspace/ci/README.md, "Applying a run log").
