---
name: test-mobile
description: Tests the current workspace's mobile app (Android/iOS) or the web in a mobile context, with Maestro flows (one per case) or, for generated content, in conversational mode with the device driver, following technique-mobile.md.
---
1. Read technique-mobile.md (<qa>/docs/) and, in the current workspace, the mobile: section of project.yaml
   (platform, app_id, flows, variables, serial/avd, send_label, ignore_labels, browser) and context.md.
   <qa> is the root of qa-assistant: usually ../../../qa-assistant; in the demo, ../.. . If the section is
   empty, stop and ask.
2. Choose the mode PER BATCH (technique section 4.1) and write it with the reason at the top of the run log
   ("mode": "flows: <why>" or "conversational: <why>"):
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
   device in mobile.serial) -- then python scripts/device.py <workspace> check. Record platform, OS, model
   and app version. Exit 3 (no target, app not installed) -> the cases are Blocked (environment).
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
9. Record one note per turn in actual_result: what was sent, the decisive quote of the answer and the
   capture's path (evidence/<batch>/<case>_t<N>.png), through
   python scripts/run_log.py <workspace> add <batch> <case> <status> "<note>" [--evidence <path>]
   (one item per case, replaced when written again; a batch already applied is refused: use the new name
   it suggests). Assisted mode (the user taps and types; device.py only reads) only when the companion
   driver cannot be used on that device, said in the run log.
10. Classify by methodology section 8 and technique-mobile.md section 6. An answer judged against the
    specification's guardrails is Pass/Fail by the specification's TEXT, never by taste; a doubt is
    Question (asked at the checkpoint). Run log in the format of methodology section 7.1, with the platform
    and the target in the actual_result.
11. New Fail -> evidence (flow and step with Maestro's message, or the turn with its quote and capture;
    target) and /log-bug, only with approval. On a runner (remote execution, technique-mobile.md section 11;
    the prompt of .github/qa/agent-prompt.md) nobody approves during the run: the bug goes as a new_defect with
    "proposed": true, a doubt stays Question, nothing is applied, and the run ends with context.md and
    runs/<batch>.summary.md updated for the pull request.
12. Never send the app, the flows nor the captures to third-party services (e.g. Maestro Cloud) without the
    project's authorisation. Continue to /checkpoint.
