/test-mobile

You are running one test batch on a GitHub Actions runner: remote execution (methodology 12.4, technique-mobile.md
section 11). No person is watching and nobody can answer a question or approve anything during this run: every
decision that needs the user waits for the pull request this run opens.

- Workspace: {{workspace}} (the current folder). qa-assistant: {{qa}}. Run its scripts from anywhere as
  `python {{qa}}/scripts/<script>.py {{workspace}} ...` (absolute paths).
- Cases, in this order: {{cases}}
- Run log: runs/{{run_log}}.json (already created, with the run block). Captures: evidence/{{run_log}}/ (pass
  `--batch {{run_log}}` to every `device.py send --capture`, `dump --capture` and `shot`).
- Limits: {{max_turns}} turns and the job's time limit. The emulator is running, the build installed, the login done.

1. Read the workspace's CLAUDE.md and context.md, then the mobile: section of project.yaml and the cases' text
   (the workbook or tracker/cases.json, read only). `python {{qa}}/scripts/device.py {{workspace}} check` once.
2. Run the cases with /test-mobile in CONVERSATIONAL mode (technique-mobile.md 4.1), one turn per step:
   `device.py {{workspace}} send "<message>" --capture <case>_t<N> --batch {{run_log}}`; a message with emoji or
   several lines goes through a UTF-8 file in evidence/{{run_log}}/ and `send --file`. Read the screenshot
   whenever the dump may miss content: it is the only view the user will have of this run.
3. Classify by methodology section 8 against the specification's text, never by taste. Write each result as soon
   as it is classified (a timeout keeps what was written):
   `python {{qa}}/scripts/run_log.py {{workspace}} add {{run_log}} <case> <status> "<note per turn>" --evidence <paths>`
4. A doubt about the requirement or the expected result: status `Question`, with the concrete question in
   actual_result. Never assume; the user answers in the pull request.
5. A Fail: look for an existing bug first (/log-bug, read only). The same problem -> "bug": "<id>" in the item.
   A new one -> "new_defect" with "proposed": true (title in the project's format, expected, observed, steps,
   environment and build, the evidence paths), added by editing the item in runs/{{run_log}}.json. It is a
   proposal: the user approves it in the pull request (methodology 9.4). A retest changes a case only when its own
   expected result was checked again.
6. Never apply nor send anything: no `adapters.excel.apply`, no `adapters.tracker ... apply`, no `--yes`, no git,
   no gh. Never type a value of .env (a credential goes only through a helper flow), never write or print a login
   link, and capture only with the app in the foreground (the driver refuses otherwise: bring the app back).
7. When the turns or the time are running out, do not start a new case: a case not run stays out of the run log
   (Not run) and is named in the summary.
8. Finish: update context.md (execution point, work done, open questions, blockers, what the next run should
   do) and write runs/{{run_log}}.summary.md for the pull request: the cases not run and why, each proposed bug
   (title, case, expected -> observed, evidence paths), each Question, and anything the user must check by eye in
   the screenshots.
