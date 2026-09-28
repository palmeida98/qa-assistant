# Mobile technique

> **Layer 2 — Technique for mobile tests with Maestro**
> Version 1.2 · 2026-09-28 · Plan, task 3.3; in English since plan 8.11; device driver and conversational mode in plan 9.0.
>
> Describes **how** the assistant tests mobile apps (Android/iOS) and the web version in a mobile context. It is project agnostic: it contains no apps, accounts nor rules of any client. The examples use the fictional project **Demo Shop**.
>
> Subordinate to `methodology.md` (decision rules, recording, bugs). For the web in the desktop browser, see `technique-browser.md`.

---

## 0. Scope

- Native and hybrid apps on **Android** and **iOS**, driven by **Maestro flows** (YAML), or on Android by the **device driver** (`scripts/device.py`) in **conversational mode** when the app's content is generated (§4.1).
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

### 1.1 Preparing an Android emulator

`scripts/setup_emulator.py` makes the target reproducible (AVD, boot, the build checked against its SHA-256 in `project.yaml`, the permissions listed there). Known cases it handles:

- **ARM-only build on an x86_64 image**: Google APIs x86_64 images from Android 11 on translate ARM code (`ro.dalvik.vm.native.bridge`). If the APK also has `extractNativeLibs=false` (native libraries stored uncompressed), Android does not extract them and a loader that looks for the device's ABI inside the APK fails at start (e.g. React Native's `SoLoaderDSONotFoundError`). On an image with root, the script copies the APK's `lib/arm64-v8a/*.so` into the app's native library folder; it must be repeated after each reinstall. The APK is never changed.
- **Login by e-mail link (magic link)**: the user asks for the link in the app and hands it over; `device.py link` opens it inside the target with `am start` (the web callback redirects to the app's scheme). The link is a one-time credential: it is never written to a file or log, and it must not be opened anywhere else first (that spends it). The browser's first run is turned off beforehand so it does not swallow the link.
- **No microphone** on the computer: dictation cases are **Blocked** (environment), unless a real device with a microphone is used.

Rule: the run log and the bug **always** say where it ran (platform, OS version, model or emulator, app version). A Pass on the emulator does not count for "real device" nor the other way round, if the case distinguishes them.

---

## 2. Execution levels

| Level | Tool | Serves for |
|---|---|---|
| **N1 · Agent** | Maestro Studio / commands by the agent | Exploring, writing and tuning flows |
| **N1 · Flows** | `scripts/run_mobile.py` (runs `maestro test`) | Batches of cases, each case a flow |
| **N1 · Device driver** | `scripts/device.py` (`adb`; Maestro only to type Unicode) | Conversational mode (§4.1); assisted runs on a device that refuses input injection (the user taps, the driver only reads) |
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

### 4.1 Conversational mode (generated content)

When the expected result cannot be asserted by a flow — the app's content is generated, e.g. an AI assistant's answers — the agent drives the app and classifies each answer:

- **Which mode**: deterministic UI → a flow per case (§4); generated content → conversational mode. The skill says which mode and why, per batch.
- **One turn per step**: `device.py <workspace> send "<message>" --capture <name>` types, sends, waits until the screen is stable and returns only what is new after the message. A new conversation per theme when the case asks for it (so earlier turns do not contaminate the answer).
- **Reading**: text from the accessibility tree; a **screenshot** whenever the tree may miss content (cards, multi-line bubbles, bottom sheets). A result is never classified from the tree alone when something it shows is incomplete.
- **Record per turn**: a note in the run log's `actual_result` (what was sent, what came back, the decisive quote), with the capture's path.
- **Classification**: the same rules (§6 and methodology §8). An answer judged against the specification's guardrails is Pass/Fail by the specification's text, never by taste; a doubt is `Question`.

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
- No credentials nor personal data in the evidence. `device.py` captures (screenshot or dump) **only with the target app in the foreground**; with any other app on screen it refuses, so nothing of the user's own apps is recorded.

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
- [ ] Mode chosen per batch (flows or conversational) and said why
- [ ] Captures only with the app in the foreground; screenshot when the tree misses content
