# Mobile technique

> **Layer 2 — Technique for mobile tests with Maestro**
> Version 1.3 · 2026-09-29 · Plan, task 3.3; in English since plan 8.11; device driver and conversational mode in plan 9.0; remote execution on a GitHub Actions runner in plan 11.0 (§11).
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
5. `validate_project.py --environment --strict`.
6. **Build**: `workspace_build.py fetch` (the release's asset, SHA-256 checked against `mobile.build.sha256`).
7. **Emulator**: `setup_emulator.py --apk … --cold --headless --gpu swiftshader_indirect --no-audio`.
8. **Login** (`ci.login.mode: manual-issue`): `remote_login.py request` (the helper flow asks for the link; an issue asks the owner for it) and `wait` (§11.2).
9. **Batch**: `run_mobile.py` for the flows (mode `flows` or `both`) and/or the agent (mode `conversational` or `both`, §11.3).
10. **Checks** after the run (`workspace_ci.py post-check`): the run log is valid for the adapter (without applying), no `.env` value in any file to be committed, every new bug `"proposed": true`.
11. **Pull request**: `runs/`, `evidence/` and `context.md` committed to a branch `run/<batch>-<run id>`, pushed, and a pull request opened with the body of `pr-body.md` (build, batch, totals, proposed bugs, questions, time and turns). The runner never pushes to `main`.

After the merge, a second workflow (`apply.yml`) applies each new run log with the project's adapter (§11.4).

### 11.2 Manual login through an issue

A login that needs a person (a one-time magic link sent by e-mail) stays **manual**: the helper flow `ci.login.flow` (e.g. `flows/_request_link.yaml`: tap the e-mail field, `inputText: ${MAESTRO_<VAR>}`, tap send, wait until the sheet closes — no `hideKeyboard`, it can close the app's bottom sheet) asks for the link; `remote_login.py` opens an issue "Login needed — <run id>" that mentions the repository's owner (or `ci.login.user`) with what to paste and the time limit. The owner copies the link from the e-mail (as text: the e-mail may show it as plain text) and pastes it as a **comment** of that issue. The runner then:

- accepts only a comment **from that user** with a URL whose host is one of `environments` or `ci.login.hosts`; any other comment is ignored and reported (its text never printed);
- masks the URL in the log (`::add-mask::`), opens it on the emulator with the device driver's `link` (never printed), **deletes the comment**, closes the issue, and checks that the app left the login screen (`ci.login.screen_text`);
- with no comment within `ci.login.timeout_minutes`, stops with exit 3: the batch is **Blocked (login)**, never failed.

The URL never reaches a file, a log line nor the pull request. The link is spent once opened: it must not be opened in the user's own browser first.

### 11.3 Conversational mode on a runner

Claude Code **headless** (`claude -p`) runs the batch with `/test-mobile`, authenticated with the user's Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`, created with `claude setup-token`; never an API key), within `ci.max_turns` and the job's time limit. What changes from a local session:

- **No screen for the user**: the screenshots in `evidence/` are the only view. A turn whose dump may miss content always gets its screenshot read before it is classified (§4.1).
- **No one to ask**: a doubt is `Question` in the run log, answered in the pull request; a new bug is a `new_defect` with `"proposed": true` (methodology §9.4); the run log is **never applied** on the runner.
- **Tools limited**: Bash only for the framework's scripts, Read, and Write/Edit only in `runs/`, `evidence/` and `context.md`; no web tools.
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

Known limits of emulators on runners: an x86_64 image with ARM translation (an ARM-only build runs; `extractNativeLibs=false` handled by `setup_emulator.py`, §1.1); no GPU (`-gpu swiftshader_indirect`: slower rendering, animations may need longer waits); always a **cold boot** (a runner has no snapshot; locally, a snapshot older than the installed build is never loaded — it brought back an old build once); no camera, microphone, biometrics nor push. Time: the emulator's first boot takes 2–4 minutes (plus the SDK download without a cache), a conversational turn about 20–60 s, so a batch of 10 cases with 3 turns each takes about 20–40 minutes. The free minutes are shared by all the user's private repositories (2 000 Linux minutes a month on the Free plan) and each job is rounded up to the minute: `ci.timeout_minutes` bounds every run, the pull request says how long it took, and `workspace_ci.py check` warns about the spending limit and the minutes used when GitHub's API gives them.
