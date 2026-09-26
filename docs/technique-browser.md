# Browser technique

> **Layer 2 — Browser automation technique**
> Version 2.1 · 2026-09-26 · Replaces the version used in Claude.ai Projects; in English since plan 8.11.
>
> Describes **how** the assistant drives a browser to test web applications. It is project agnostic: it contains no URLs, accounts, roles nor rules of any client. The examples use the fictional project **Demo Shop**.
>
> Subordinate to `methodology.md`: the decision, recording, bug and checkpoint rules are there and are not repeated here.

---

## 0. Scope

- It is the base of the web modules (`functional-web`, `exploratory`, `accessibility`; closed list in methodology §4.1). Smoke, role matrix, visual, cross-browser and forms are variants done inside those modules. Each module adds only what is its own.
- It does **not** cover native mobile apps: see `technique-mobile.md`.
- The concrete values (URLs, roles, credential variables, exceptions) come from the workspace: `CLAUDE.md` and `project.yaml`.

---

## 1. Execution levels

Always use **the lowest level that allows the expected result to be checked**.

| Level | Tool | Serves for | Does not serve for |
|---|---|---|---|
| **N0 · Static** | HTTP request (fetch) | Page availability, HTTP code, `title`, meta tags, links and anchors, CTAs and footer present in the HTML | Anything that depends on JavaScript, login or interaction |
| **N1 · Agent** | Playwright MCP | Running batches, flows with login, forms, dynamic states, exploration | Repeated regression in large volume |
| **N2 · Deterministic** | Playwright specs (TypeScript) | Regression and smoke that run on their own and in CI | New or still unstable features |

Rules:

- Move from N0 to N1 as soon as the expected result involves dynamic or interactive behaviour.
- A case moves from N1 to N2 by the criteria of §13.
- If the Playwright MCP is not available in the environment, use the available browser (for example, Claude in Chrome) and **record the environment difference** (methodology §14.2).
- The Playwright MCP is registered with a **fixed version** (`@playwright/mcp@<version>`, the same in the demo's `.mcp.json` and in `/project-onboarding`), never `@latest`: a new version can change the tools or the snapshot in the middle of a project. Updating is a change of its own, checked in the demo.
- The Playwright MCP runs with `--isolated` (a clean browser profile in each session, in memory). With the default persistent profile, in the pilot of 25.09.2026 the clicks (`browser_click`, `browser_press_key`) stopped reaching the page after the 1st navigation, while filling fields kept working; the diagnosis with three variants (the same task: login + add to cart) failed only with the persistent profile. The clean profile also leaves no cookies nor sessions of the tested site from one session to the next. Never work around clicks that do not arrive with `element.click()` in `browser_evaluate`: the case is `Blocked` (tool limitation, §9).

---

## 2. Preparing the session

Before the first case of a batch:

1. Read `project.yaml`: environments, roles, active modules, evidence policy.
2. **Confirm the environment.** The URL to open must be one of the test environments declared in `project.yaml`. If the URL does not match, or looks like production, **stop and ask**. An out-of-date link is enough to test the wrong system.
3. Confirm that the needed credential variables exist (check only that they exist, without showing the value).
4. Record the version/build tested, if the application shows it (footer, "About" page, response header).
5. Check that the test data the batch depends on exists (§7). If it does not, the affected cases are `Blocked`.

---

## 3. Authentication and roles

- Credentials come **exclusively** from environment variables. They are never written in logs, notes, bugs, reports or messages, not even partly masked.
- **The assistant never reads a credential's value.** With the Playwright MCP, the workspace's `.env` is passed as a secrets file (`--secrets .env`, in `.mcp.json`): in text fields (`browser_type`, `browser_fill_form`) the variable's **name** is written (e.g. `SHOP_BO_ADMIN_PASS`) and the MCP replaces it with the value; in the responses, any `.env` value shows as `<secret>NAME</secret>`. Without that mechanism (another browser, an MCP without `--secrets`), the user logs in in the browser window and the assistant continues from there.
- The `.playwright-mcp/` folder, where the MCP keeps snapshots and screenshots, has data of the tested application: it stays out of git (the workspace's `.gitignore`).
- **One login per role and per batch.** The order of the batches (methodology §6) should allow running every case of a role before switching to another.
- When the tool allows it, save and reuse the session state (cookies/storage) per role, to avoid repeating the login.
- When switching roles: log out explicitly or use a new browser context. Never assume the previous session ended.
- Session expired in the middle of a batch: log in again **once** and repeat the step. If it fails again, the following cases of that role are `Blocked`, with the reason.
- CAPTCHA, 2FA or e-mail/SMS verification: **do not work around**. Ask the user to complete the step, or mark as `Blocked` if they are not available.

---

## 4. Reading the page and locating elements

### 4.1 Read the structure, not the image

- Prefer the **accessibility snapshot** (tree of roles and accessible names) to the screenshot. It is faster, cheaper and more reliable.
- To read content, extract only the relevant region instead of the whole page.
- The screenshot is only used when the visual look **is** the expected result (layout, colour, overlap) or when the project requires it as evidence.

### 4.2 Locator priority

1. Role + accessible name (e.g. button "Complete purchase")
2. Label associated with the field
3. Placeholder
4. Visible text
5. `data-testid` or equivalent
6. Stable CSS selector

Never use positional XPath nor automatically generated selectors (classes with a hash, indexes such as `div:nth-child(7)`).

When an element can only be located by levels 5–6, it may point to an accessibility problem. Record it as an observation for the `accessibility` module, not as a Fail of the functional case.

### 4.3 Waits

- Wait for a **condition** (element visible, text present, network request finished, URL changed), never for a fixed time.
- In SPAs, confirm that the view changed (title, heading or URL) before checking the content.
- A timeout counts as an environment signal (§9), not as the case's result.

---

## 5. Interaction

| Situation | Technique |
|---|---|
| Text fields | After typing, read the field's value and confirm it is what was intended (masks, autocomplete and size limits change the value) |
| Selects and comboboxes | Select by the option's visible text. In custom comboboxes: open, wait for the list, choose and confirm the value shown |
| Dates | Prefer typing into the field. Use the date picker only if the case tests the picker itself |
| Uploads | Use only the workspace's test files (`data/`), never real documents |
| Downloads | Confirm the download started and, if the case requires it, the file's name, type and size |
| Modals and dialogs | Handle native dialogs (alert/confirm) explicitly before continuing. An unhandled dialog blocks the browser |
| New tabs/windows | Switch explicitly to the new tab and go back to the original at the end |
| iframes | Enter the frame before interacting. Record whether the content comes from a third party |
| Pagination and infinite scroll | Go through until the element is found or until the declared end. Never conclude "it does not exist" from the first page alone |
| Destructive actions (delete, cancel, pay) | Only in the test environment and only when the case requires it (§12) |

---

## 6. Verification

- Compare what was observed with the **expected result written in the case**, not with what looks reasonable.
- **Text:** normalise spaces. Ignore case or accents only if the specification allows it.
- **State:** visible, enabled/disabled, selected, read only.
- **Navigation:** URL and title or heading of the target view.
- **Persistence:** when the case claims something was saved, reload the page or reopen the record and confirm.
- **Messages:** confirm the text and the type (error, success, warning) and that the message appears next to the right element.
- **Secondary signals:** JavaScript console errors and failed network requests (4xx/5xx) are recorded as **observations**. They only give Fail if the expected result covers them or if they cause an observable difference in behaviour.

---

## 7. Test data

- Never use real personal data. Generate valid fictional data for the local format (names, addresses, tax numbers, IBANs and test phone numbers).
- Everything the assistant creates carries an **identifiable prefix**, for example `QA-AUTO-20260922-`, to stand out from other people's data and be easy to clean up.
- Record in the run log the entities created (type + identifier) so they can be cleaned up.
- Do not change nor delete data not created by the assistant, unless the case requires it and the project allows it.
- Clean up at the end of the batch, when the project allows it, or leave the list of what was created in `context.md`.

---

## 8. State and isolation between cases

- Each case declares the preconditions it needs. Before running it, confirm they hold.
- When a case leaves the application in a state that affects the next one (full cart, active filter, saved draft), restore the state or order the batch to use that state intentionally.
- Dependencies between cases are explicit in the batch plan. If case A fails, the cases that depend on it are `Blocked` (blocked by A), not Fail.

---

## 9. Robustness: telling application, environment and tool apart

Before classifying a problem, identify its origin:

| Origin | Signals | Classification |
|---|---|---|
| **Application** | Reproducible behaviour different from the expected result | Fail |
| **Environment** | Timeouts, 502/503, external service down, missing data | Blocked (reason: environment) |
| **Tool** | The element exists but the automation cannot interact with it (canvas, native controls, complex drag-and-drop) | Blocked (reason: tool limitation). Suggest a manual test |

Retry policy:

- Environment-type errors: **one** retry after a short pause.
- If it passes on the second attempt: `Pass`, with the note "unstable on the 1st attempt". If the instability repeats in several runs, record it as an observation for the team.
- An application failure is **not** repeated looking for a Pass. It is reproduced once to confirm and then classified.

---

## 10. Evidence

Follows the policy of `project.yaml` (methodology §7.3). In the browser, a **Fail** records:

- URL at the moment of the failure;
- role and platform (browser + viewport);
- steps actually done (not the case's steps copied);
- data entered, without credentials;
- expected → observed, with the exact text of what appeared;
- relevant console errors and failed requests;
- screenshot only if the project's policy is `text+screenshot`, or if the problem is visual.

---

## 11. Efficiency of the execution

- Reuse the session and the navigation within the batch. Go directly by URL when the case does not test the navigation itself.
- Do not take repeated full snapshots of the same page when nothing changed.
- In large batches, the execution can run in a **subagent**, which returns to the main context only the run log: ID, result, short note and bug. There is no subagent of the framework's own: Claude Code's generic subagent is used with the instructions of `/run-batch`. A dedicated subagent (`executor-web`) is only created if the pilot in a real project shows batches filling the main context (decision of 24.09.2026).
- Group checks of the same page, when several cases share it, without mixing each case's results.

---

## 12. Security and limits

- Only environments declared in the workspace are tested, with the authorisation of the responsible team.
- Never make real payments, send communications to real people nor change settings of shared accounts (password, e-mail, 2FA), unless the case requires it in an environment prepared for it.
- Never work around security controls (CAPTCHA, rate limiting, account lock).
- Active security tests are outside this technique. The `qa-audit` engine's security module is passive only, and only with express authorisation.
- If an action could have effects outside the test environment, **stop and ask**.

---

## 13. Promotion to regression (N1 → N2)

A case is a candidate for a Playwright spec when:

- it gave `Pass` in at least **two** consecutive runs, without instability;
- the data it needs can be created or is deterministic;
- the locators used are of levels 1–4 (§4.2);
- the expected result is stable (the feature is not under active development).

In the conversion:

- one spec file per feature;
- the case ID in the test's title (e.g. `CHK-014 · Quantity at the limit (99)`), so the results can be imported into the model;
- tags per module and per priority (`@smoke`, `@roles`, `@a11y`);
- credentials and URLs read from environment variables, never written in the spec;
- the specs live in the project's workspace (or in the company's repository), **not** in `qa-assistant`.

**Auditing states in the middle of a spec.** A state that only exists after interacting (step 2 of a checkout, an error message, an open dialog) can be audited by the `qa-audit` engine without it touching the page (`docs/qa-audit.md` §22):

- copy `integrations/playwright/qa-audit.ts` from `qa-assistant` into the workspace's specs folder;
- in `playwright.config.ts`, with `QA_AUDIT_CDP_PORT` defined: `launchOptions.args` with `--remote-debugging-port=<port>` and `workers: 1` (see `examples/demo/e2e/playwright.config.ts`);
- in the spec, after reaching the state: `await auditState(page, testInfo, "<state-name>", { failOn: "none", redact: [<credential variables>] })`. The report is attached to the test's result;
- Chromium or Chrome only (connection over CDP). By default only axe runs, which does not touch the page. The keyboard and reflow (`checks: "axe,keyboard,reflow"`) move the focus and change the window's width, so they are left for the end of the flow (the keyboard walk puts the focus and the scroll back afterwards);
- a violation found this way follows the rules of `technique-accessibility.md` §4 and §5. With `failOn: "none"` the test does not fail because of it: the result goes into the run log through the corresponding accessibility case.

---

## 14. Connection points with other modules

| Module | What it adds to this technique |
|---|---|
| `accessibility` | axe-core, keyboard and reflow through the `qa-audit` engine + manual checks of keyboard, focus and accessible names (`technique-accessibility.md`, `scripts/audit_a11y.py`); states in the middle of an N2 spec with `auditState` (§13) |
| `visual` | Reference screenshots and comparison with the Figma frames |
| `cross-browser` | The same batch in Chromium, Firefox, WebKit and mobile viewports |
| `forms` | Systematic generation of boundary values and equivalence classes per field |
| `api` | Direct requests to the API in the same session, to prepare data or check persistence (`technique-api.md`, `scripts/api_request.py`) |

---

## 15. Checklist per batch

**Before**
- [ ] Environment confirmed against `project.yaml`
- [ ] Credential variables present for the batch's roles
- [ ] Needed test data available
- [ ] Build/version recorded
- [ ] Batch ordered by role and context

**During**
- [ ] One login per role
- [ ] Run log updated after each case
- [ ] Checkpoint every 15–20 cases
- [ ] Questions marked, without forced classification

**After**
- [ ] Entities created recorded or cleaned up
- [ ] Secondary observations (console, network, accessibility) kept apart from the results
- [ ] N2 candidates identified
- [ ] Control passed to `/end-session`

---

## Migration notes

### v2.0 → v2.1 (plan 8.11, 26.09.2026)

- **Changed:** in English, with the new names (`technique-browser.md`, `/run-batch`, `auditState`, `data/`).

### v1 → v2

- **New:** execution levels N0/N1/N2 (§1), required confirmation of the environment (§2), classification by origin application/environment/tool (§9), test data with a prefix and clean-up (§7), promotion to regression criteria (§13), connection points with the other modules (§14).
- **Changed:** the main browser becomes the Playwright MCP in Claude Code. Claude in Chrome stays as an alternative, with the difference recorded.
- **Changed:** the evidence becomes textual by default. Screenshots only by the project's policy, or when the problem is visual.
- **Kept:** static checks for light structural validation (now N0), escalation to a full browser when there is dynamic behaviour, and tests done directly by the agent.
