# Running mobile tests
> Read by /run-tests for the mobile module (Android and iOS) and for exploratory charters of a mobile project. Technique: technique-mobile.md.
> Moved from the version 1 skills (commit 7e7cabf) with their names updated (skills-guide.md §6). Step numbers are the original ones.

## Steps (from /test-mobile)

1. Read technique-mobile.md (<qa>/docs/) and, in the current workspace, the mobile: section of project.yaml
   (platform, app_id, flows, variables, serial/avd, send_label, ignore_labels, browser) and context.md. If the
   section is empty, stop and ask.
2. Choose the mode PER BATCH (technique section 4.1) and record it with the reason BEFORE the first result, never by
   editing the JSON: python scripts/run_log.py <workspace> mode <batch> "conversational: <why>" (or "flows: <why>",
   "assisted: <why>" when the person taps and the driver only reads, "manual: <why>"), or --mode on the first
   `run_log.py add`. Another mode later in the same batch is refused: it is a new batch.
   - deterministic UI (the expected result can be asserted) -> flows, steps 3-5;
   - generated content (e.g. an AI assistant's answers) -> conversational, steps 6-9;
   - exploratory charters on the app (module exploratory in a mobile project, technique section 4.2) ->
     conversational, steps 6-9, one note per turn; each charter judged by its expected result.
   Commands run from <qa> with the ABSOLUTE path of the workspace.
3. Flows: confirm the target and the environment (technique sections 1 and 3): test build installed or test
   URL; device/emulator connected (android: adb devices; ios: simulator). With no target, the cases are
   Blocked (environment) -- never Pass. Record platform, OS, device and app version.
4. Flows in <workspace>/<flows>/, one per case, with the ID in the name (TC-MOB-003_cart.yaml), ending
   with the assertion of the expected result. If they are missing, write them as a PROPOSAL from the
   approved cases (section 4) and show them before using them. Credentials only as ${MAESTRO_<VAR>}, with
   the variable listed in mobile.variables; never written in the flow nor passed with -e.
5. Run: python scripts/run_mobile.py <workspace> [flows...] --output runs/mobile_<YYYYMMDD>_<nn>.json
   (each flow: a path, relative to the workspace or to the flows folder, or just the case ID, e.g. TC-MOB-003;
   without flows, runs them all; a flow that does not exist exits with 2 and nothing runs)
   - 3 = no target or Maestro did not run: Blocked (environment);
   - Fail from tool instability (an element that exists and was not found, a driver timeout):
     one retry of that flow; if it repeats, Blocked (tool) with a suggestion of a manual test.
   Continue to step 10.
6. Conversational (Android): prepare the target -- python scripts/setup_emulator.py <workspace> (or the
   device in mobile.serial) -- then ONE check: python scripts/device.py <workspace> check [--batch <batch>] (an iPhone that does not answer: it
   starts with `blocked by:` and `next:` from a read-only diagnosis; `device.py <workspace> doctor` gives it alone,
   and never "the cable" when Xcode sees the device). It reports
   the device, the app, the driver (its setting and whether it answers are two facts), the observed build against the
   intended one, the environment and the session, and ends in `ready: yes` or NOT READY with what to do (it changes
   nothing on the device: a setting is the person's to turn). Record platform, OS, model and the `build:` line it
   prints as the run's build (`--build`: observed, never invented). Exit 3 (no device, app not installed, driver not
   answering, session needed) -> say what it says; with no target the cases are Blocked (environment), never Pass.
   With --batch, a build that changed since the batch started is exit 3: stop that batch and plan a new one
   (/plan-tests). A session that is needed is the person's to start (a magic link is theirs); never type their
   phone number or code.
   A real device that refuses input injection (adb shell input -> SecurityException INJECT_EVENTS, e.g. a
   Xiaomi without a SIM): mobile.input: driver, the companion driver (technique-mobile.md section 1.2;
   python scripts/build_driver.py --push, the person installs it and turns its service on once); check
   then says "input: driver <n>, service on". Scroll with device.py swipe, go back with device.py back.
   Open the app (or bring it back) with device.py <workspace> launch (--restart when it hangs): it waits for
   the app and prints its screen; never adb by hand.
7. Login by link: the user asks for it in the app and hands it over; open it with
   device.py <workspace> link "<url>". Never write the link anywhere (it is a one-time credential), never
   open it elsewhere first. Typing a credential to ask for the link: a helper flow, never device.py type --
   flows/_request_link.yaml with ${MAESTRO_<VAR>}, run with python scripts/run_mobile.py <workspace>
   --helper flows/_request_link.yaml (no result recorded; not a case). No hideKeyboard step in it: it can
   close the app's bottom sheet.
8. Per case: a NEW conversation when the case asks for it (one per theme). One turn per step:
   device.py <workspace> send "<message>" --capture <case>_t<N>
   It prints only what is new after the message and saves a screenshot. A message with emoji or longer than
   a few lines goes from a UTF-8 file: device.py <workspace> send --file <msg.txt> --capture <case>_t<N>
   (on Windows an emoji passed as an argument arrives as ????; long texts are typed in chunks). Whenever the dump may miss content
   (cards, multi-line bubbles, bottom sheets) READ that screenshot before classifying -- after a scroll or an
   opened sheet, a new one with device.py <workspace> shot <case>_t<N>b; never classify from an incomplete
   dump. Exit 3 from send = the answer did not settle: read the screen again (dump) before deciding. Captures only with the app in the foreground (the driver
   refuses otherwise: bring the app back, never capture another app).
   On an iPhone `send` finds the one text field itself (--field only when there are several); when it stops it prints
   `outcome:` (nothing typed, partly typed, typed not sent, sent, uncertain). Never send again before looking at the
   app (dump or shot): a second send of an `uncertain` one can post the message twice.
   A case that expects the answer to be presented progressively (streaming): add `--samples 20 --sample-interval 3`
   to send: it keeps timed readings in evidence/<batch>/<capture>_samples.json and its `evidence:` says what they
   could show (partial text observed; answer readable, no intermediate state captured; answer text not readable;
   sampling failed or insufficient). When the answer's text is not readable (iOS apps often do not expose the
   bubbles), add `--burst 20` too: timed screenshots from the tap on send, with a manifest; READ them and judge
   their content (waiting indicator, partial answer, final answer), never their sizes. A complete final answer does
   not prove streaming; a `capture-failed` sample is not a failure of the app; never send again to get better
   evidence. Without proof either way, judge on what was observed and write the limit in the note (no captured
   intermediate state is never a Fail by itself).
9. Record one note per turn in actual_result: what was sent, the decisive quote of the answer and the
   capture's path (evidence/<batch>/<case>_t<N>.png), through
   python scripts/run_log.py <workspace> add <batch> <case> <status> "<note>" [--evidence <path> ...]
   (every evidence path is kept: several after one option or the option repeated; a case of many steps: `ts` is its
   start and --ended-at its end; a time recorded wrongly: run_log.py retime; an evidence that shows nothing useful:
   attach --invalid <path> --reason "<why>", never deleted; one item per case, a second execution of it with --replace; an observation made earlier, such as the tester's
   notes of the day before, with its time and source: --executed-at "2026-11-05T16:21" --executed-source "tester
   notes of 05.11", or --executed-at unknown when nobody knows: `ts` is the execution time, never the time it was
   written, and a time is never guessed; a batch already applied is refused: use the new name
   it suggests). A result written for the wrong case: python scripts/run_log.py <workspace> remove <batch> <case> (a batch
   not yet applied; it shows the result, keeps the rest and never deletes evidence). A case that is not in the project's
   library is refused: it is added first (/plan-tests). Assisted mode (the user taps and types; device.py only reads) only when the companion
   driver cannot be used on that device, said in the run log.
10. Classify by methodology section 8 and technique-mobile.md section 6. An answer judged against the
    specification's guardrails is Pass/Fail by the specification's TEXT, never by taste; a doubt is
    Question (asked at the checkpoint). Run log in the format of methodology section 7.1, with the platform
    and the target in the actual_result.
11. New Fail -> evidence (flow and step with Maestro's message, or the turn with its quote and capture;
    target) and /manage-bugs, only with approval. On a runner (remote execution, technique-mobile.md section 11;
    the prompt of .github/qa/agent-prompt.md) nobody approves during the run: the bug goes as a new_defect with
    "proposed": true, a doubt stays Question, nothing is applied, and the run ends with context.md and
    runs/<batch>.summary.md updated for the pull request.
12. Never send the app, the flows nor the captures to third-party services (e.g. Maestro Cloud) without the
    project's authorisation. Continue to /checkpoint.
