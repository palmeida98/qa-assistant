# Mobile technique

> **Layer 2 — Technique for mobile tests with Maestro**
> Version 1.1 · 2026-09-26 · Plan, task 3.3; in English since plan 8.11.
>
> Describes **how** the assistant tests mobile apps (Android/iOS) and the web version in a mobile context. It is project agnostic: it contains no apps, accounts nor rules of any client. The examples use the fictional project **Demo Shop**.
>
> Subordinate to `methodology.md` (decision rules, recording, bugs). For the web in the desktop browser, see `technique-browser.md`.

---

## 0. Scope

- Native and hybrid apps on **Android** and **iOS**, driven by **Maestro flows** (YAML).
- The web in a mobile context, through **Maestro Web** (beta) when there is no device; the result is valid for the web flow, **not** for the native app.
- Gestures, permissions, deep links, offline and background states, differences between platforms.
- It does **not** cover performance, battery nor active security tests of the app.

---

## 1. Where to run: emulator, device or web

| Target | Serves for | Limitations |
|---|---|---|
| **Android emulator / iOS simulator** | Most functional flows; repeatable; CI | Sensors, camera, push notifications and performance are not real |
| **Real device** | Fine gestures, camera, biometrics, push, real network, specific manufacturers | Less repeatable; needs device management |
| **Maestro Web (beta)** | Flows of the web version when there is no device | Does not test the native app; the support is beta |

Rule: the run log and the bug **always** say where it ran (platform, OS version, model or emulator, app version). A Pass on the emulator does not count for "real device" nor the other way round, if the case distinguishes them.

---

## 2. Execution levels

| Level | Tool | Serves for |
|---|---|---|
| **N1 · Agent** | Maestro Studio / commands by the agent | Exploring, writing and tuning flows |
| **N1 · Flows** | `scripts/run_mobile.py` (runs `maestro test`) | Batches of cases, each case a flow |
| **N2 · Regression** | The same flows in CI or Maestro Cloud | Smoke and regression |

The flows live **in the workspace** (`flows/`), never in `qa-assistant` (except the demo).

---

## 3. Preparation

1. Read the `mobile:` section of `project.yaml`: platform, `app_id` (or `url` for the web), flows folder, credential variables.
2. Confirm the environment: the app installed is the **test build** (not the store's), or the URL is the test environment's. If it looks like production, stop and ask.
3. Confirm the target: `adb devices` (Android) or `xcrun simctl list` (iOS) shows the expected device/emulator. With no target, the native cases are **Blocked** (environment), never Pass.
4. Record the app version, OS and device.

---

## 4. Flows

- **One flow per case**, with the case ID in the file name (`TC-MOB-003.yaml`, or `TC-MOB-003_cart.yaml`): the script links the result to the case by that prefix.
- Each flow starts in a known state (`launchApp` with `clearState: true` when the case requires it) and ends with the **assertion of the case's expected result** (`assertVisible`, `assertNotVisible`, `assertTrue`).
- Locators, in this order: `id` (resource-id / accessibilityIdentifier / id in the HTML), visible text, `index` only as a last resort (as in `technique-browser.md` §4.2).
- Waits for a condition (`extendedWaitUntil`), never for a fixed time.
- **Credentials**: never written in the flow. The flow uses `${MAESTRO_<VAR>}`; the script puts the value in an environment variable `MAESTRO_<VAR>` from `.env` (Maestro makes the variables with that prefix available to the flows). Never `-e VAR=value` on the command line.
- Test data with the prefix `QA-AUTO-<date>-` (`technique-browser.md` §7).

---

## 5. What to test on mobile (beyond the functional)

| Topic | What to check |
|---|---|
| Gestures | tap, long tap, swipe, scroll to the element, pull-to-refresh |
| Keyboard | the right keyboard type per field; the keyboard covers neither the field nor the button |
| Permissions | first time: request with an explanation; refuse: the app stays usable; accept later in the settings |
| Deep links | each link opens the right screen, with and without a session; an invalid link is handled |
| Offline and slow network | clear message; nothing is lost; resumes when the network comes back |
| Background and resume | going back to the app keeps the state (form, cart); an expired session is handled |
| Orientation and sizes | rotate the screen; small screen and tablet; large system text |
| iOS vs Android | "back" button (physical/gesture on Android, navigation on iOS), date pickers, notifications |

---

## 6. Classification

The rules of methodology §8. Particulars:

- The flow passed up to the final assertion → **Pass**.
- The assertion of the expected result failed, reproduced → **Fail**.
- It failed earlier, from tool instability (an element that exists and was not found, a driver timeout) → **one** retry; if it repeats, **Blocked** (tool), with a suggestion of a manual test.
- No device/emulator or app not installed → **Blocked** (environment).
- A feature that does not exist on a platform, by definition → **N/A** on that platform.

---

## 7. Evidence

- Pass: Maestro's report (JUnit) is enough.
- Fail: flow, step that failed and Maestro's message, platform/OS/device/app version, expected → observed. Screenshot only by the project's policy or if the problem is visual (Maestro keeps them in `~/.maestro/tests/`).
- No credentials nor personal data in the evidence.

---

## 8. Security and limits

- Only declared test builds and environments; never real client accounts.
- Never send the app or the flows to third-party services (e.g. Maestro Cloud) without the project's authorisation.
- No real purchases, SMS or calls; sensitive permissions (location, contacts) only with test data.

---

## 9. Link to the methodology

| Methodology | In this technique |
|---|---|
| §5 case design | §5 (mobile topics) and §4 (one flow per case) |
| §7 run log | one item per flow; `actual_result` with the platform and the message |
| §8 decision rules | §6 |
| §9 bugs | §7 |
| §1.2 privacy | §4 (credentials) and §8 |

---

## 10. Checklist per batch

- [ ] Target confirmed (device/emulator or web) and recorded
- [ ] Test build / test URL confirmed
- [ ] Credentials only as `${MAESTRO_<VAR>}`
- [ ] One flow per case, with the ID in the name
- [ ] JUnit report kept in the workspace
- [ ] Cases without a target marked Blocked, not Pass
