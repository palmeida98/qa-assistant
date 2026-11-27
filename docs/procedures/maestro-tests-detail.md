# Maestro flows: detail of /maestro-tests

> Layer 3 — Read by /maestro-tests. The skill keeps the flow and the decisions; the commands and conventions are here.
> Technique: technique-mobile.md (§2 levels, §4 flows, §6 classification, §7 evidence, §11 CI). The shared rules are
> procedures/rules.md. The mobile counterpart of procedures/playwright-tests-detail.md.

## When it does not apply

- The project must have `mobile` in `modules:` and a `mobile:` section; the preflight stops otherwise.
- A case whose expected result judges generated content (an assistant's answers, a summary, a title the app writes)
  is conversational (technique-mobile.md §4.1) and stays in `/run-tests`. A flow may still assert the **structure**
  around generated content (a card appears, a button turns disabled, a count changes), never its words; such a flow is
  flaky by nature until it passes in two runs (see "Promotion and demotion").
- A step the project gives the user (a login, a one-time code or link, a permission or consent dialog, airplane mode, a
  microphone) is never automated: it is a precondition the user prepares, and a case that needs it during the flow
  stays in `/run-tests`.
- A workspace with flows and conventions of its own, or whose `CLAUDE.md` says how flows are written and run, keeps
  them; where they conflict with these steps, stop and say which rule was followed (rules.md §8).

## Why a skill of its own

`/run-tests` already runs flows that exist (`procedures/run-mobile.md` steps 3-5). Writing them is different work:
reading the app without changing it, choosing locators, making each flow independent, and a short loop of fixes. A
flow is written once and runs the same way on the workspace's CI (`qa-mobile.yml`, mode `flows`); its results enter
the run log like any other, so a capture per step is no longer the evidence of a pass.

## Targets

| Target | Flows | Notes |
|---|---|---|
| Android emulator | yes | The default. `setup_emulator.py <ws>` prepares it; the build under test installed (an APK the user provides: installing it is asked) |
| Android device that accepts `adb shell input` | yes | `--serial <device>` |
| Android device that refuses input injection (technique-mobile.md §1.2) | **no** | Maestro needs the same permission; use the emulator. The companion driver serves `/run-tests` only |
| iOS simulator | yes | `xcrun simctl list devices booted`; `--serial <UDID>` when several |
| Physical iPhone | **no** | Maestro is not used on a physical iPhone (technique-mobile.md §1.5) |
| Web build (`platform: web`) | yes, beta | Valid for the web flow only, never for the native app |

- **The device.** `run_mobile.py` passes `--device` to Maestro: `--serial`, else `QA_MOBILE_SERIAL`, else
  `mobile.serial` (Android). When `mobile.serial` names a phone that cannot run flows, pass `--serial` with the
  emulator. Several devices connected and none chosen: nothing runs (exit 2), because Maestro would ask which one and
  wait until the timeout.
- **Java.** Maestro needs Java 17+. On macOS `/usr/bin/java` is a stub that answers nothing: set `JAVA_HOME` to an
  installed JDK. Without one, `run_mobile.py` exits 2 and runs nothing (never a Blocked case); the preflight's
  `maestro` line says so.
- **The session.** A flow starts from a session the user opened (`launchApp` without `clearState`). `clearState: true`
  only when the project allows it and the case asks for a fresh install; it erases the session, and logging in again is
  the user's.
- **The build.** `device.py <ws> check` (Android) prints the build OBSERVED; record it with `--build` on every result.
  A build changed during the batch: stop, a new batch.

## Installing (a download: ask first)

Name what is downloaded before asking. macOS: `brew install mobile-dev-inc/tap/maestro` (it brings a JDK; Homebrew may
also clean old versions of other packages, which the user should know before saying yes); elsewhere the installer of
https://maestro.mobile.dev. Then `maestro --version` with `JAVA_HOME` set. The build under test is installed with
`adb install -r <apk>` only on the user's word and never downgraded. Offline or refused: the cases are Blocked (tool),
the flows may still be written.

## Reading the app (before writing a flow)

The aim is the exact names of the elements, read from the app, never guessed.

- Read: `maestro --device <serial> hierarchy` (the whole tree, ids and texts), or `device.py <ws> dump` (visible text
  with bounds). Both only read.
- Move one named step at a time: `device.py <ws> tap "<text>"` (refuses another app in the foreground), then read the
  screen again before the next step. Never chain taps by coordinates without reading: a side menu that has not
  finished opening sends the next tap to whatever is under it.
- **Never touch, while exploring, a control that deletes, removes, sends, confirms, publishes, pays, logs out or changes
  a setting**, even to see the dialog it opens: an older build may act at once with no confirmation. Its label comes
  from the case, the specification or a screen the user shows; the flow that taps it runs only on data the flow itself
  created (`QA-AUTO-<date>-`), never on the account's own data.
- The language of the app is part of the state: a flow written against English labels fails on Portuguese ones.
  Prefer ids (`resource-id`, `accessibilityIdentifier`, a `testID` of React Native); when the app has none, the
  labels of one language, said in the flow's comment, and a note to the team that test ids would make the flows
  independent of the language.
- Captures taken while reading are working files: keep them out of `evidence/` unless they document a result.

## Layout in the workspace

```
<workspace>/<mobile.flows>/          one flow per case: TC-<AREA>-<n>_<name>.yaml
<workspace>/<mobile.flows>/_<name>.yaml   helper flows (not cases; run_mobile.py --helper)
<workspace>/runs/mobile_<date>_<nn>.json  run_mobile.py's report
<workspace>/evidence/<batch>/        captures and messages of failures, named with the case id
```

The flows live in the workspace, never in `qa-assistant` (except the demo) and never in the application's repository.

## Authoring rules

```yaml
# TC-MOB-004 · Remove an item from the cart (Demo Shop; labels in English; data created by this flow)
appId: com.demoshop.app
---
- launchApp                          # the session the user opened; no clearState
- extendedWaitUntil:
    visible: "Cart"
    timeout: 20000
- tapOn: "Add QA-AUTO-20261124-1 to cart"
- tapOn: "Cart"
- tapOn:
    id: "remove-QA-AUTO-20261124-1"
- assertVisible: "Remove this item?"   # the case's expected result: a confirmation before removing
- tapOn: "Cancel"
- assertVisible: "QA-AUTO-20261124-1"
```

- File name: the case id first, exactly the library's; `run_mobile.py` links the result by that prefix and the run log
  refuses an unknown id. One case per flow; a case with several expected results was split in `/plan-tests`.
- First line: a comment with the id, the title, the language of the labels and where its data comes from.
- Locators: `id` first, then visible text, `index` only as a last resort with a comment saying why.
- Waits: `extendedWaitUntil` on a condition; never a fixed sleep (`waitForAnimationToEnd` only after an animation).
- The last step asserts the case's expected result (`assertVisible`, `assertNotVisible`, `assertTrue`). Never
  `optional: true` on an assertion.
- Independence: each flow creates the data it changes, and removes it when it can; no flow depends on another's data
  or order. Reused steps go to a helper flow (`runFlow: _open_account.yaml`).
- Tags: the header of a flow lists `tags: [smoke, android]` (platform and priority; the case id is the file name).
  `run_mobile.py --include-tags smoke,android --exclude-tags slow` runs a batch by tag; a flow without tags runs only
  when no `--include-tags` is given.
- Helpers (`flows/_<name>.yaml`: login, language, permissions) hold steps only, never an assertion of a case, and take
  their values from `${MAESTRO_<VAR>}` or `env:` of the `runFlow` that calls them. A case is one flow that calls the
  helpers it needs; `run_mobile.py --helper _<name>.yaml` tries one helper alone and records nothing.
- Screenshots that are evidence: `- takeScreenshot: TC-MOB-004_cancel` (the case id first, then the step). With
  `--evidence-dir` they land in `<dir>/<run>/<flow>/takeScreenshot/<name>.png`, with the failure's screenshot and screen
  hierarchy under `screenshots/` and `screen-hierarchy/` (checked on an Android emulator, 27.11.2026).
- Credentials: `${MAESTRO_<VAR>}` with the variable in `mobile.variables`; never written in a flow, never `-e`.
- The expected value in an assertion is the case's, not what the app showed when the flow was written.

## Verified on a real target (27.11.2026)

Maestro 2.12.1, OpenJDK 17 (`JAVA_HOME`), an Android 15 emulator, two cases of a stock app plus one that must fail:
`--include-tags`, `--exclude-tags`, `--evidence-dir` (JUnit, per-flow folders, logcat), a failing assertion reported as
Fail with Maestro's message, and a flow left out by tags not reported at all. What did not work on this machine, and is
therefore not a result: Maestro Web (its bundled ChromeDriver 132 refuses the installed Chrome 158; the flow is
Blocked, tool) and an iOS 27 simulator (the XCTest driver needed `MAESTRO_DRIVER_STARTUP_TIMEOUT=240000`, worked once,
then the simulator runtime became unavailable). A device or driver failure (`DeviceUnreachable`, `Connection refused`,
`driver not ready`, `session not created`) is suggested as Blocked with a `tool:` prefix, never as Fail. Not run: a
real project's flows on a build of its own app.

## Running

From `<qa>`, with the workspace's absolute path:

`python scripts/run_mobile.py <ws> TC-MOB-004 TC-MOB-005 --serial emulator-5554 --output runs/mobile_20261124_01.json`

Without flows it runs every flow named after a case. Exit 0: flows ran (a Fail is a result); 2: configuration, flow,
Maestro, Java or device choice (nothing ran); 3: no target or no report (Blocked, environment). The report gives a
suggested status and Maestro's message per case. With `--evidence-dir evidence/<batch>/maestro` Maestro writes its
screenshots, recordings and logs there and the JUnit report is kept as `junit.xml` (secrets redacted); without it Maestro
keeps its screenshots of failures in `~/.maestro/tests/`. Screens can show a credential or personal data: review the
folder before it is shared or committed (the workspace's `.gitignore` decides what is versioned).

## From the report to the run log

1. `python scripts/run_log.py <ws> mode <batch> "flows: Maestro flows in <mobile.flows> (N2)"` once, before any result.
2. For each case of the report: `run_log.py <ws> add <batch> <TC-id> <status> "<observed>" --build <observed>
   --evidence <paths>`.
3. Status: Pass when the flow reached its final assertion; Fail only after the classification below; a case not run is
   Not run, never N/A unless the case itself does not apply; the project's vocabulary applies (rules.md §4).
4. Observed: the flow name, the step reached and Maestro's message for a failure; for a pass, the report file and the
   flow name. Evidence for a failure: Maestro's screenshot copied to `evidence/<batch>/<TC-id>_<step>.png` and the
   message; for a pass, the report (the project's evidence policy is the minimum, rules.md §6). Captures with the app
   in the foreground only, reviewed for personal data before they are shared.
5. Passed on retry: record it with the word flaky and say so in the summary.

## Classifying a failure (before touching anything)

| Signal | Class | Action |
|---|---|---|
| Element not found that the hierarchy shows under another id or text; a tap before the screen settled | Flow bug | Fix the locator or add a wait on a condition |
| The assertion of the expected result fails and the case is right | Application | Keep the flow and evidence, `/manage-bugs` |
| The assertion fails and the case is out of date (a build changed the behaviour on purpose) | Doubt | Question: the expected result is not changed on a guess |
| No device, app not installed, session ended, Java or Maestro error, driver timeout | Environment or tool | One retry, then Blocked; never a defect |
| Passes alone, fails after another flow | Flow bug (shared state) | Make the flow create its own data |
| Language or build of the app differs from the flow's labels | Environment | Blocked with the reason, or a flow per language agreed with the user |

Three rounds at most: after the third, stop with the open failures, their class and what is needed. Never add
`optional: true` to an assertion, delete a step or lengthen every timeout to reach green.

## Promotion and demotion

- A case becomes a flow when its expected result is deterministic and it passed in `/run-tests` on the current build,
  or the plan approved it for automation; the flow then replaces the per-step captures as the evidence of a pass.
- A flow that asserts structure around generated content, or that failed and passed on retry, is flaky until it
  passes in two runs; a flow flaky in two runs goes back to `/run-tests` and is not counted as stable regression.
- A flow whose case changed its expected result is updated with the case, in the same batch. Nothing is deleted from
  the flows folder without telling the user.

## CI

The workspace's `qa-mobile.yml` (technique-mobile.md §11) runs a batch of flows on an emulator of a GitHub-hosted
runner with `mode: flows`; its login step is the user's (an issue). The same flows, the same report, a pull request
with the run log.
