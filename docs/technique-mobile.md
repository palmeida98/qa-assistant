# Mobile technique

> **Layer 2 — Technique for mobile tests with Maestro**
> Version 1.7 · 2026-11-02 · One check of the target and the roles of a build (§1.3, §1.4), sampling of a streamed answer (§4.1). Before: 1.6 · 2026-10-17 · Plan, task 3.3; in English since plan 8.11; device driver and conversational mode in plan 9.0; remote execution on a GitHub Actions runner in plan 11.0 (§11); a real device that blocks input injection, through the companion driver, in plan 11.7 (§1.2); on a runner, the emulator and `adb` over `mobile.serial` / `mobile.input` (`QA_MOBILE_SERIAL` / `QA_MOBILE_INPUT`) in plan 11.8 (§1.2, §11.1); exploratory charters on the app in plan 12.6 (§4.2).
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
- **The build**: a release of the workspace's private repository (`scripts/workspace_build.py publish`, then `fetch`), recorded in `mobile.build` with its SHA-256. `setup_emulator.py` reads the APK's versionCode from its binary manifest (no `aapt2`) and reinstalls only when the installed one differs: a reinstall wipes the app's data, login included.
- **A stale snapshot**: an emulator restarted from the AVD's saved snapshot comes back as it was then — with the old build installed, and without the native libraries copied later. `setup_emulator.py` boots **cold** (`-no-snapshot-load`) when the snapshot is older than the build, or with `--cold` (saving a snapshot can fail anyway with some apps, e.g. `UNSUPPORTED_VK_APP` for a Vulkan app).
- **No microphone** on the computer: dictation cases are **Blocked** (environment), unless a real device with a microphone is used.

An emulator stopped by force leaves crash reports behind; the next one then waits for a consent dialog on the desktop and never boots. `setup_emulator.py` sees it in the emulator's output and stops at once: answer the dialog, or delete `%TEMP%\AndroidEmulator\emu-crash-*.db` (and any `*.lock` left in the AVD's folder) and run again.

### 1.2 A real device that blocks input injection

Some vendor builds only let `adb` inject input with an extra option that needs a SIM card or a vendor account — on Xiaomi (MIUI / HyperOS), "USB debugging (Security settings)". Without it `adb shell input` fails with `SecurityException: … INJECT_EVENTS`, and so do `adb install` (`INSTALL_FAILED_USER_RESTRICTED`), `settings put`, `ime` and `pm grant`; Maestro, scrcpy and UiAutomator depend on the same option. Reading the screen (`uiautomator dump`, `screencap`), `am start`, `am broadcast` and `content call` still work. Diagnose it with `device.py check` and one `adb shell input keyevent 0`.

Do not fall back to "assisted" runs (the person taps): use the **companion driver** of `qa-assistant` (`tools/android-driver/`), a small app with an accessibility service that taps, swipes, goes back and sets text through the accessibility APIs, which need none of those permissions.

1. **Build** it on this computer: `python scripts/build_driver.py --push` (Android build-tools, a platform and a JDK 17+; the signing key is created on the first build in `~/.qa-assistant/`, outside every repository). `--push` tries `adb install` and, when the device refuses it, copies the APK to the device's `Download/` folder.
2. **Install** it on the device (the person, once, and after each new version of the driver): the file manager → `Download/qa-driver.apk` → install (allow the file manager to install apps when asked).
3. **Allow restricted settings** (Android 13+, for an app not installed from a store): Settings → Apps → *QA driver* → ⋮ → *Allow restricted settings* (the menu appears after the first attempt to turn the service on).
4. **Turn the service on**: Settings → Accessibility → downloaded apps / installed services → *QA driver* → on.
   Then turn **off the service's shortcut** ("QA driver shortcut" / accessibility button) on the same screen: some vendor builds add a floating accessibility button on the right edge when a service is turned on, and the driver's taps land on it instead of the app (in the pilot it covered a chat's send button).
5. Set `mobile.input: driver` in `project.yaml` (or `--input driver`) and run `device.py check`: it says whether the driver is installed, the service on and its version; `validate_project.py --environment` checks the same.

**The target and the input, in order of precedence.** `mobile.serial` and `mobile.input` describe the computer the project is usually run on (e.g. a physical phone with the driver). Another target overrides them without editing `project.yaml`: for each of the two, the first that is set wins — the command-line flag (`--serial`, `--input`), the environment variable (`QA_MOBILE_SERIAL`, `QA_MOBILE_INPUT`; an empty one counts as unset), `project.yaml`, the default (the only device connected; `adb`). `QA_MOBILE_INPUT` accepts the same values as `mobile.input` (`adb` or `driver`); any other value is a configuration error (exit 2), never ignored. The same order holds in `device.py`, `run_mobile.py --helper`, `remote_login.py` and `validate_project.py --environment` (which then does not ask for the driver when the input is `adb`, and asks for the emulator when the serial is an emulator's). A runner sets both variables (§11.1).

How it works: `device.py` runs `adb shell content call --uri content://io.qaassistant.driver --method <method> --arg <args>`; the methods are `ping`, `tap x,y`, `longpress x,y,ms`, `swipe x1,y1,x2,y2,ms`, `back`, `home`, `text <base64 of the UTF-8 text>` (set on the focused field, any Unicode, the whole text in one call; no argument empties the field — `device.py clear`) and `enter`; each answers `ok` or an `error` once the gesture has happened. The arguments are comma-separated because the device's shell splits on spaces.

Security: the driver accepts commands only from the adb shell (or root), checked on every call, so no other app on the device can use it; it has no network permission, no screen of its own, stores nothing and does nothing by itself. It is off until the person turns it on, and it is turned off (or uninstalled) in the device's settings when the project ends. The APK is built locally and never published.

Limits: the driver replaces input only. Other apps are never driven nor read (the foreground guard of `device.py`): a system file picker or the system's settings (airplane mode, which such a device does not let `adb` change either) are steps of the person, said in the run log as assisted steps. Maestro flows (N2) still cannot run on such a device (use the emulator for them); setting a field's text replaces its whole content (`ACTION_SET_TEXT`), so a field that reacts to each key press may behave differently than with a keyboard — when a case is about typing itself, say so in the result; a permission dialog of the system is tapped like any other screen, but `pm grant` stays unavailable.

Rule: the run log and the bug **always** say where it ran (platform, OS version, model or emulator, app version). A Pass on the emulator does not count for "real device" nor the other way round, if the case distinguishes them.

### 1.3 One check before a batch

`python scripts/device.py <workspace> check` is the single check of the target. It reports everything and stops at nothing, so that one call says what is missing:

| Line | Says |
|---|---|
| `target`, `foreground`, `app`, `input` | The device (serial, Android version, model), the app in the foreground, the installed app and its version, and the input route (with the driver: its version and whether it answers) |
| `device` | Connected, or not, and why |
| `driver` | Only with `mobile.input: driver`. **Setting** (the service is listed in `enabled_accessibility_services`) and **answering** (it replies to a ping) are two facts. Setting on and not answering is a divergence: Android can leave a service listed without running it |
| `build` | **Observed** (read from the device now), **intended** (`mobile.build.version`, when set), and whether they are the same (§1.4) |
| `environment` | The environments of `project.yaml` and the app under test; the app must be the test build, not the store's |
| `session` | `confirmed`, `needed` or `unknown`, from the screen text and the optional `mobile.session` (`signed_in` and `signed_out`: lists of texts). Without them it is `unknown`, never guessed |
| `ready` | `yes`, or `NOT READY` with each blocker and the way out |

Recovery is a line of advice and never an action: the framework does not change device settings. Setting on and driver not answering: in Settings, Accessibility, turn the QA driver off and on again. Setting off: turn it on. Driver not installed: §1.2. Device not connected: connect it, USB debugging on. App not installed or in the background: install it or open it (`device.py launch`). Session needed: the person logs in (a magic link is theirs, §1.2).

Exit 0 when ready, 3 when not (the first blocker on stderr). `--batch <name>` also compares the observed build with the one of that batch's run log (§1.4). In a project whose modules are only `mobile` (and `exploratory`), `workspace_select.py preflight` for a browser skill prints a `Route` line to `/run-tests`: the Playwright MCP is not a requirement of a mobile run.

### 1.4 The build, three roles

- **Intended** (`mobile.build` in `project.yaml`: `source`, `sha256`, `version`): the build the project targets. Written by a person, or by `workspace_build.py publish`. Nothing updates it automatically, and nothing replaces an artefact reference or a hash with what a device reports.
- **Observed**: what `device.py check` reads from the device at the start of an execution. Printed, compared with the intended one, never written to `project.yaml`.
- **Of the run**: `run.build` of the run log, copied from the observed build when the first result is written. **Immutable once the batch has results**: `run_log.py add` refuses another build (or environment) for that batch. A build that changes during a batch (a new install) ends the batch: the results stay as they are, and a new batch (`/plan-tests`) starts with the right context.

The permanent files do not repeat a build number. `CLAUDE.md` says where it comes from (`mobile.build`); a checkpoint may keep a line "last observed build" in `context.md`, which is a summary and is replaced by the next one; the history of builds is in the run logs.

### 1.5 An iPhone through WebDriverAgent

A real iPhone cannot be driven through `adb`. `device.py` drives it through **WebDriverAgent** (WDA, the XCUITest server of Appium): a small test runner, signed by the person's Apple team, that serves taps, swipes, text, the accessibility tree and screenshots over HTTP. The same subcommands and the same privacy rule as on Android (§4.1); Maestro is not used on a physical iPhone.

**Selecting it.** `--platform ios`, else `QA_MOBILE_PLATFORM`, else `mobile.platform` of `project.yaml` (`android` by default). The section `mobile.ios` holds `app_id` (the bundle id; default `mobile.app_id`), `udid` (shown by `check`), and optionally `wda_url` (default `http://localhost:8100`; `QA_WDA_URL` overrides it) and `wda_log` (the WDA runner's log the diagnosis reads). The input is always `wda`: `--input` and `mobile.input` are not read for iOS (`mobile.ios.input` is free text of the project, e.g. `assisted` until WDA is ready). An unknown platform is a configuration error (exit 2).

**Preparing it (macOS with Xcode, once per iPhone and every 7 days with a free Apple account).**
1. The iPhone is paired (USB, "Trust"), **Developer Mode** is on, and **Settings → Developer → Enable UI Automation** is on.
2. The WDA project comes with `appium driver install xcuitest` (`~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj`). It is signed with the person's team and a bundle id of their own: `xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'id=<UDID>' -allowProvisioningUpdates DEVELOPMENT_TEAM=<TEAM> PRODUCT_BUNDLE_IDENTIFIER=<own id> build-for-testing`, then the same with `test-without-building` (it keeps running; the log says `ServerURLHere`).
3. On the iPhone, the person trusts the developer certificate (Settings → General → VPN & Device Management).
4. `iproxy 8100 8100` forwards the port over USB; `curl localhost:8100/status` answers `ready`.
A free Apple account signs for 7 days: after that the person installs WDA again (step 2). A paid account signs for a year.

**Typing on an iPhone.** `type` and `send` read the screen first: `--field LABEL` taps that field; else, with the keyboard already up, the focused field; else the one enabled text field of the screen (a late one is waited for up to 3 s; an app whose input is not exposed as a text field — a React Native composer seen as `Other` — declares its label in `mobile.field_label`, matched by "contains"), tapped from the screen just read (never stale coordinates). Several fields, none, or a disabled one only: refused with the list, nothing typed. The focus is confirmed (the keyboard up) before the first key. `send` prints `timing:` (the focus, the keys, and the seconds until the tap on send) and reads the screen three times before the tap. When typing or sending stops, the last lines say what reached the app — `outcome: nothing typed`, `partly typed`, `typed, not sent`, `sent` (the field emptied after the tap, or, for a field that never shows its text, the message shown on the screen) or `uncertain` — and nothing is ever repeated automatically: a person looks at the app first.

**The outcome of a send on Android.** `send` prints the same `outcome:` line on Android, read after the tap by comparing the screen with the nodes that showed the typed text before it: `sent` when the message is no longer where it was typed (or, when no field showed it, is shown on the screen); `typed, not sent` (exit 4) when it is still in the field `SENT_WAIT_S` seconds later, or when the screen could not be read to find the send control; `uncertain` when the screen cannot be read after the tap (the dump fails, the app is not in the foreground). A failure to read the answer after the tap ends as `STOPPED: the answer could not be read …` with the outcome and exit 3, never as a bare `NO TARGET`: the message may have gone and is not sent again. A dump that has no hierarchy (a side menu or an animation can cause it) says that a tap by coordinates still works (`tap --xy X Y`).

**What each subcommand does.** `check`: the same report as §1.3 (`device`, `target`, `foreground`, `app`, `build`, `environment`, `session`, `ready`, `next`; exit 0 ready, 3 not ready, never stopping at the first problem): the iOS version and the WDA state from `/status`, the foreground bundle, the installed version of the app (`xcrun devicectl device info apps`, when the UDID is known), compared with `mobile.ios.build.version`. The session is always `unknown`: the person looks at the app. `dump`: `/source?format=json`, one line per visible element with a size, `[x1,y1][x2,y2] text` in points; the text is the value of a text field, else the label, else the name; the same rectangle and text is listed once. `tap`, `swipe`: WDA gestures. `type`/`clear`: keys sent to the **focused** field in chunks of 300 characters (nothing focused: exit 4). `back`: iOS has no back button, so it is a swipe from the left edge. `shot`: `/screenshot`. `launch`: opens `app_id` (`--restart` terminates it first). `link` is not available yet (exit 2): the magic link is opened by the person.

**When WebDriverAgent does not answer: the diagnosis.** `check` (when WDA does not answer or is not ready) and `doctor` (always; exit 0 ready, 3 blocked) run `scripts/ios_diagnose.py`, read only and with a time limit per probe: `xcrun devicectl` (the device seen, transport, pairing, tunnel, lock state), usbmux (`idevice_id -l`), the port forward (a TCP connect to `wda_url` and whether `iproxy` runs), WDA `/status`, one session opened and closed at once (the proof that UI automation is authorized: `/status` answers even when it is not; nothing is read or tapped; `doctor --no-session` skips it and says so), the WebDriverAgent provisioning profiles on the Mac (expiry), the code-signing identities (count and kind only), the Xcode accounts (how many, never which) and the end of the WDA log (`mobile.ios.wda_log`, `$QA_WDA_LOG`, else `~/wda_run.log`) for known causes (profile expired, no account, not trusted, locked, launch failed). Each finding is `confirmed` (with its source), `probable` (with the reason) or `unknown`. The output starts with `blocked by:` and `next:`, the details below. usbmux not listing a device that `devicectl` sees is never read as "no cable". The diagnosis never renews a profile, restarts a service or changes a setting: those steps are the person's (Xcode account, re-signing with `-allowProvisioningUpdates`, trusting the certificate, unlocking the iPhone).

**Privacy.** The iPhone is the person's own. `dump`, `shot`, `send`, `tap`, `type`, `swipe`, `back` and `clear` act only when `/wda/activeAppInfo` reports `app_id` in the foreground (never `com.apple.springboard`: it also shows the person's notification banners and the app switcher, so a system alert or a banner over the app makes the command refuse, and the person answers it); otherwise exit 3 and nothing was read. Creating a WDA session happens only after that check, so a refused command never touches another app. The text refused when it holds a `.env` value is the same as on Android.

---

## 2. Execution levels

| Level | Tool | Serves for |
|---|---|---|
| **N1 · Agent** | Maestro Studio / commands by the agent | Exploring, writing and tuning flows |
| **N1 · Flows** | `scripts/run_mobile.py` (runs `maestro test`) | Batches of cases, each case a flow |
| **N1 · Device driver** | `scripts/device.py` (`adb`; Maestro only to type Unicode; or the companion driver with `mobile.input: driver`, §1.2) | Conversational mode (§4.1), on an emulator or a real device — also one that refuses input injection (§1.2) |
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
- **Helper flows**: a flow that is not a case (e.g. asking the app for a login link with `${MAESTRO_<VAR>}`) is named `flows/_<name>.yaml` and run with `run_mobile.py --helper` (the same variables, output redacted, no result recorded). No `hideKeyboard` step unless needed: it can close the app's bottom sheet.

---

### 4.1 Conversational mode (generated content)

When the expected result cannot be asserted by a flow — the app's content is generated, e.g. an AI assistant's answers — the agent drives the app and classifies each answer:

- **Which mode**: deterministic UI → a flow per case (§4); generated content → conversational mode. The skill says which mode and why, per batch, and records it before the first result with `run_log.py <ws> mode <batch> "<kind>: <why>"` (or `add --mode`; kinds `conversational`, `flows`, `assisted`, `manual`): the run log's top-level `mode`, fixed once the batch has results.
- **One turn per step**: `device.py <workspace> send "<message>" --capture <name>` types, sends, waits until the screen is stable and returns only what is new after the message. A new conversation per theme when the case asks for it (so earlier turns do not contaminate the answer).
- **Reading**: text from the accessibility tree; a **screenshot** whenever the tree may miss content (cards, multi-line bubbles, bottom sheets). A result is never classified from the tree alone when something it shows is incomplete.
- **Typing**: a message with emoji or several lines goes from a UTF-8 file (`send --file`); `adb shell input text` is cut at about 430 characters and Maestro's `inputText` slows down and stops on long texts, so the driver types in chunks of 300 characters. Maestro's keyboard sometimes is not active ("Maestro IME is not active"): the driver taps the field again and retries once, then types ASCII through `adb` and says so.
- **Proving streaming**: a final answer that is complete does not show that it was presented progressively. When a case expects it, `send ... --samples N` reads the screen during the wait and writes `evidence/<batch>/<capture>_samples.json`: each sample has a time, the seconds since the send and the answer read: on an iPhone only content elements count (never a button, a scroll bar, a text field or the declared field), and what was on the screen before the send never counts. An answer whose elements WDA reports as invisible (`isVisible=0`, no size; seen on a React Native app) is not readable. Its summary says one of: **partial text observed** (the answer grew between readings); **answer readable, no intermediate state captured**; **answer text not readable** (the accessibility tree does not expose it: a limit of the reading, never "no streaming"); **sampling failed or insufficient**. A `capture-failed` sample (the screen animating) is not a failure of the app. When the text is not readable, `send ... --burst N` (or `shot-burst NAME` started before an action a person does) takes timed screenshots from the tap on send — limits `--burst-interval`, `--burst-seconds`, `--burst-mb` — and writes `<capture>_bNN.png` and `<capture>_burst.json` (the attempt, the send's instant, each file's time, failed captures, gaps, the limit that stopped it). The burst captures on its own clock, in the background, while the screen is read for the answer: each wait is the interval minus the time the capture took (before 07.11.2026 a capture followed each reading, about 3 s for a 1.5 s interval on an iPhone 13 Pro). The manifest's gaps show any interval the device could not keep, and a short partial state can still fall between two captures. `send ... --samples N --read-hidden` (iOS) also reads the labelled elements WDA reports as invisible after the message and keeps them apart (`hidden_answer`; evidence "partial text observed, hidden in the tree" or "answer readable only hidden in the tree"): those elements are what the tree holds, not what the screen showed, so the screenshots stay the evidence of what the user saw. The images are judged by their content (a waiting indicator, a partial answer, the final answer); file sizes or changed pixels never prove streaming. Nothing is sent again to get better evidence. Without proof either way, the result says the limit, and the lack of a captured intermediate state is never a Fail by itself.
- **Record per turn**: a note in the run log's `actual_result` (what was sent, what came back, the decisive quote), with the capture's path, written with `scripts/run_log.py add` (it never touches a batch already applied).
- **Classification**: the same rules (§6 and methodology §8). An answer judged against the specification's guardrails is Pass/Fail by the specification's text, never by taste; a doubt is `Question`.

### 4.2 Exploratory charters on the app

A project with `mobile` and without `functional-web` among its modules has no web site to explore: its `exploratory`
charters (methodology §5.2) run on the app with `/run-tests`, in conversational mode (§4.1) — the device driver,
one note per turn, a screenshot whenever the dump may miss content — and each charter is judged by its expected
result. A step only a person can do (airplane mode, an incoming call, a system dialog of another app) is an assisted
step: an unattended run says it did not cover it, and the charter's result says what was explored. A run on the
workspace's CI gives such a batch the mobile tools.

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
- [ ] `device.py <workspace> check` ready (device, driver answering, test build, session), build observed recorded in the run block
- [ ] Test build / test URL confirmed
- [ ] Credentials only as `${MAESTRO_<VAR>}`
- [ ] One flow per case, with the ID in the name
- [ ] JUnit report kept in the workspace
- [ ] Cases without a target marked Blocked, not Pass
- [ ] Mode chosen per batch (flows or conversational) and said why
- [ ] Captures only with the app in the foreground; screenshot when the tree misses content
- [ ] Remote run (§11): build from a release with its SHA-256, new bugs only as `"proposed": true`, no `Question` left at the merge

---

## 11. Remote execution (GitHub Actions)

A batch can run on a **GitHub-hosted Linux runner** started from the workspace's own private repository (methodology §12.4), so that the user needs only a browser: publish a build, start a batch, paste the login, review the pull request. The workflow, the agent's prompt and the pull request's body are templates in `qa-assistant/docs/workspace/ci/`, copied into the workspace by `scripts/workspace_ci.py install`; nothing of the project is written in them, they read `project.yaml` (the `ci:` section, §11.5).

### 11.1 The workflow's steps

`workflow_dispatch` with three inputs: `batch` (a planned batch, `batches/<name>.yaml`, or a list of case IDs), `build` (a release tag; default: the tag of `mobile.build`) and `mode` (`conversational`, `flows` or `both`).

1. **Plan** (a short job): reads `ci:` from `project.yaml` (runner, time limit) and refuses a runner label that is not a GitHub-hosted standard Ubuntu runner (larger runners are paid).
2. **Checkout** of the workspace and of `qa-assistant` (a read-only token, secret `QA_ASSISTANT_TOKEN`).
3. **Tools**: Python and the requirements, Java 17 and the Maestro CLI, the Android SDK's emulator and system image (`mobile.image`), cached between runs (`actions/cache`); Claude Code (native installer) only in conversational mode. KVM enabled for the emulator.
4. **`.env`** from the Actions secrets named in `ci.secrets` (`workspace_ci.py write-env`), never printed nor committed.
5. `validate_project.py --environment --strict --ci`: a missing tool stops the run. With `--ci` the workbook's recalculation (LibreOffice) is not required: the run never applies its run log; `apply.yml` installs LibreOffice and applies it after the merge (§11.4).
6. **Build**: `workspace_build.py fetch` (the release's asset, SHA-256 checked against `mobile.build.sha256`).
7. **Emulator**: `setup_emulator.py --apk … --cold --headless --gpu swiftshader_indirect --no-audio`. The only device of a runner is this emulator, `emulator-5554` (the first one a clean machine starts), driven with `adb`: the companion driver (§1.2) is not installed there. The job sets `QA_MOBILE_SERIAL: emulator-5554` and `QA_MOBILE_INPUT: adb`, so `mobile.serial` / `mobile.input` of a project tested locally on a physical phone are overridden for every step — `validate_project.py` (step 5: no driver to check), the login, the flows and the agent's `device.py` calls (which pass no `--serial` nor `--input`) — and the step stops the run when that serial is not the emulator that booted.
8. **Login** (`ci.login.mode: manual-issue`): `remote_login.py request` (the helper flow asks for the link; an issue asks the owner for it) and `wait` (§11.2).
9. **Batch**: `run_mobile.py` for the flows (mode `flows` or `both`) and/or the agent (mode `conversational` or `both`, §11.3).
10. **Checks** after the run (`workspace_ci.py post-check`): the run log is valid for the adapter (without applying), no `.env` value in any file to be committed, every new bug `"proposed": true`.
11. **Pull request**: `runs/`, `evidence/` and `context.md` committed to a branch `run/<batch>-<run id>`, pushed, and a pull request opened with the body of `pr-body.md` (build, batch, totals, proposed bugs, questions, time and turns). The runner never pushes to `main`.

After the merge, a second workflow (`apply.yml`) applies each new run log with the project's adapter (§11.4).

The workflow's input `login: skip` checks the pipeline without the owner (no login; the agent is told so and marks every case Blocked): its pull request is never merged. Every run uploads a diagnostics artifact (the emulator's output, Maestro's logs and screenshots, the agent's result, the checks; never `.env`), and a run that stops before any case opens no pull request.

### 11.2 Manual login through an issue

A login that needs a person (a one-time magic link sent by e-mail) stays **manual**: the helper flow `ci.login.flow` (e.g. `flows/_request_link.yaml`: tap the e-mail field, `inputText: ${MAESTRO_<VAR>}`, tap send, wait until the sheet closes — no `hideKeyboard`, it can close the app's bottom sheet) asks for the link; `remote_login.py` opens an issue "Login needed — <run id>" that mentions the repository's owner (or `ci.login.user`) with what to paste and the time limit. The owner copies the link from the e-mail (as text: the e-mail may show it as plain text) and pastes it as a **comment** of that issue. The runner then:

- accepts only a comment **from that user** with a URL whose host is one of `environments` or `ci.login.hosts`; any other comment is ignored and reported (its text never printed);
- masks the URL in the log (`::add-mask::`), opens it on the emulator with the device driver's `link` (never printed), **deletes the comment**, closes the issue, and checks that the app left the login screen (`ci.login.screen_text`);
- `wait` reads the issue's number left by `request` (GitHub's list of open issues lags behind a new issue);
- with no comment within `ci.login.timeout_minutes`, stops with exit 3: the batch is **Blocked (login)**, never failed.

The helper flow gets only the `mobile.variables` it uses (`${MAESTRO_<VAR>}` in its text), so the runner needs only those in `ci.secrets`; the others with a value are still redacted from its output. A status a script writes on its own (Blocked by the workflow, Pass / Fail from a flow) is the project's word for that meaning: `dashboard.states`, or the English word when the vocabulary has it.

The URL never reaches a file, a log line nor the pull request. The link is spent once opened: it must not be opened in the user's own browser first.

### 11.3 Conversational mode on a runner

Claude Code **headless** (`claude -p`) runs the batch with `/run-tests`, authenticated with the user's Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`, created with `claude setup-token`; never an API key), within `ci.max_turns` and the job's time limit. What changes from a local session:

- **No screen for the user**: the screenshots in `evidence/` are the only view. A turn whose dump may miss content always gets its screenshot read before it is classified (§4.1).
- **No one to ask**: a doubt is `Question` in the run log, answered in the pull request; a new bug is a `new_defect` with `"proposed": true` (methodology §9.4); the run log is **never applied** on the runner.
- **Tools limited**: Bash only for the framework's scripts, one command per call (no `cd`, `;`, `|`, `sleep`, `adb`: a compound command is denied), Read, and Write/Edit only in `runs/`, `evidence/` and `context.md`; no web tools. The app is opened with `device.py launch` (the pilot's agent, with no such command, tried `adb` and was denied).
- **A reply still streaming**: reopening a conversation while the answer streams may show only the user's message; the driver waits for the screen to settle (`send`), and a dump after reopening is read again before deciding.
- **Long or non-ASCII text**: from a UTF-8 file (`device.py type --file`), typed in chunks (§4.1).

### 11.4 Review and apply

The pull request is the checkpoint's review: the user edits the run log there (drops or changes a proposed bug, removes `"proposed"` to approve it, answers each `Question` by setting the final status), then merges. `apply.yml` runs on the push to `main` that touches `runs/`: each run log added or changed is applied with the project's adapter (Excel: LibreOffice installed on the runner for the recalculation; Jira / Azure DevOps: `apply --yes`, the merge being the approval), the workbook or `tracker/` is committed, and the result is commented on the pull request. A run log with `"proposed"` or `Question` left is never applied: the workflow fails and names them.

### 11.5 Configuration (`ci:` in `project.yaml`) and limits

| Key | Meaning |
|---|---|
| `runner` | GitHub-hosted standard runner, default `ubuntu-24.04` (a larger runner is refused: it is paid) |
| `timeout_minutes` | the job's time limit (default 60) |
| `max_turns` | the agent's turn limit (default 150) |
| `secrets` | names of the `.env` variables the runner needs, each an Actions secret of the same name |
| `login.mode` | `manual-issue` or `none` |
| `login.flow` | the helper flow that asks for the link (`flows/_<name>.yaml`) |
| `login.timeout_minutes` | how long to wait for the comment (default 30) |
| `login.hosts`, `login.user`, `login.screen_text` | (opt.) extra hosts a login link may have (e.g. the auth provider's), who may paste it (default: the repository's owner), a text only the login screen shows |

Known limits of emulators on runners: an x86_64 image with ARM translation (an ARM-only build runs; `extractNativeLibs=false` handled by `setup_emulator.py`, §1.1); no GPU (`-gpu swiftshader_indirect`: slower rendering, animations may need longer waits); always a **cold boot** (a runner has no snapshot; locally, a snapshot older than the installed build is never loaded — it brought back an old build once); no camera, microphone, biometrics nor push. A GitHub-hosted runner has about 7 GB of free disk, less than the emulator's userdata needs: the workflow first removes preinstalled toolchains the batch never uses (.NET, Haskell, Boost, Swift, CodeQL), an AVD created by `setup_emulator.py` gets a 3 GB data partition (`disk.dataPartition.size`, and `-partition-size 3072` when it starts in the same run), not the device profile's ~6 GB, and the AVD's `.ini` is looked for where the `avdmanager` of the runner writes it (`XDG_CONFIG_HOME`). Time: the emulator's first boot takes 2–4 minutes (plus the SDK download without a cache), a conversational turn about 20–60 s, so a batch of 10 cases with 3 turns each takes about 20–40 minutes. The free minutes are shared by all the user's private repositories (2 000 Linux minutes a month on the Free plan) and each job is rounded up to the minute: `ci.timeout_minutes` bounds every run, the pull request says how long it took, and `workspace_ci.py check` warns about the spending limit and the minutes used when GitHub's API gives them.
