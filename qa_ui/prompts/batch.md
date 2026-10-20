{{skill}}

You are running one test batch started from the local interface of qa-assistant (docs/qa-ui.md section 8). No
person is watching and nobody can answer a question or approve anything during this run: every decision that needs
the user waits for the interface's review (methodology 9.4).

- Workspace: {{workspace}} (the current folder). qa-assistant: {{qa}}. Run its scripts from anywhere as
  `python {{qa}}/scripts/<script>.py {{workspace}} ...` (absolute paths, forward slashes).
- Tools: {{tools}} Each Bash call is ONE command, exactly one of {{commands}}: no `cd`, `;`, `&&`, `|`,
  redirections, variables, `sleep` nor `adb`, and there is no other shell. Anything else is refused in this run
  and nobody can allow it: a refused command is sent again as one allowed command, never given up on. Files are
  read with Read, Glob and Grep, and written with Write or Edit, only in runs/ and evidence/.
- Module: {{module}}. The plan is this batch, already approved: do not plan again and do not wait for a
  confirmation.
- Cases, in this order: {{cases}}
- Run log: runs/{{run_log}}.json (already created, with the run block). Evidence: evidence/{{run_log}}/.
- Limit: {{max_turns}} turns.

The cases, as the project's library has them (read only; the source of each expected result):

{{case_text}}

1. Read the workspace's CLAUDE.md and context.md, then the sections of project.yaml the module uses (environments,
   roles, and the module's own section). Confirm the environment is the test environment; if it is not, stop.
2. Run the cases with {{skill}}, following its technique, in the order given.
3. Classify by methodology section 8 against the expected result above, never by taste. Write each result as soon
   as it is classified (an interrupted run keeps what was written):
   `python {{qa}}/scripts/run_log.py {{workspace}} add {{run_log}} <case> <status> "<what was observed>" [--evidence <paths>]`
   The status is one of the project's vocabulary; what was observed is never the expected result copied.
4. A doubt about the requirement or the expected result: status `Question`, with the concrete question as what
   was observed. Never assume; the user answers in the interface.
5. A Fail: look for an existing bug first (/log-bug, read only). The same problem: add `--bug <id>`. A new one:
   write the bug as a JSON object in evidence/{{run_log}}/<case>_defect.json with `"proposed": true` (the title in
   the project's format, the expected and the observed behaviour, the steps, the environment and the build, the
   evidence paths; the fields of the tracker's technique) and add `--new-defect evidence/{{run_log}}/<case>_defect.json`.
   It is a proposal: the user approves it in the interface. A retest changes a case only when its own expected
   result was checked again.
6. Never apply nor send anything: no `adapters.excel.apply`, no `adapters.tracker ... apply`, no `--yes`, no git,
   no gh. Never type nor print a value of .env (a credential goes only through the tool that reads .env itself).
7. When the turns are running out, do not start a new case: a case not run stays out of the run log (Not run) and
   is named in the summary.
8. Finish by writing runs/{{run_log}}.summary.md: the cases not run and why, each proposed bug (title, case,
   expected -> observed), each Question, and anything the user must check by eye. Do not change context.md: the
   user's /checkpoint does.
