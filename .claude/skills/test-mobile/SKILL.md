---
name: test-mobile
description: Tests the current workspace's mobile app (Android/iOS) or the web in a mobile context with Maestro flows, one flow per case, following technique-mobile.md.
---
1. Read technique-mobile.md (<qa>/docs/) and, in the current workspace, the mobile: section of project.yaml
   (platform, flows, variables) and context.md. <qa> is the root of qa-assistant: usually
   ../../../qa-assistant; in the demo, ../.. . If the section is empty, stop and ask.
2. Confirm the target and the environment (sections 1 and 3): test build installed or test URL;
   device/emulator connected (android: adb devices; ios: simulator). With no target, the cases are
   Blocked (environment) -- never Pass. Record platform, OS, device and app version.
3. Flows in <workspace>/<flows>/, one per case, with the ID in the name (TC-MOB-003_cart.yaml), ending
   with the assertion of the expected result. If they are missing, write them as a PROPOSAL from the
   approved cases (section 4) and show them before using them. Credentials only as ${MAESTRO_<VAR>}, with
   the variable listed in mobile.variables; never written in the flow nor passed with -e.
4. Run, from <qa>, with the ABSOLUTE path of the workspace:
   python scripts/run_mobile.py <workspace> [flows...] --output runs/mobile_<YYYYMMDD>_<nn>.json
   (each flow: a path, relative to the workspace or to the flows folder, or just the case ID, e.g. TC-MOB-003;
   without flows, runs them all; a flow that does not exist exits with 2 and nothing runs)
   - 3 = no target or Maestro did not run: Blocked (environment);
   - Fail from tool instability (an element that exists and was not found, a driver timeout):
     one retry of that flow; if it repeats, Blocked (tool) with a suggestion of a manual test.
5. Classify by methodology section 8 and technique-mobile.md section 6. Run log in the format of
   methodology section 7.1, with the platform and the target in the actual_result.
6. New Fail -> evidence (flow, step, Maestro's message, target) and /log-bug, only with approval.
7. Never send the app nor the flows to third-party services (e.g. Maestro Cloud) without the project's
   authorisation. Continue to /checkpoint.
