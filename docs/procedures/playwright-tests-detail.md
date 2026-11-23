# Playwright specs: detail of /playwright-tests

> Layer 3 — Read by /playwright-tests. The skill keeps the flow and the decisions; the commands and conventions are here.
> Technique: technique-browser.md (level N2, section 13). The shared rules are procedures/rules.md. The method of the
> first draft (specs written once, run natively, fixed in a short loop) comes from a third-party playwright-tester
> skill; the swarm of sub-agents writing specs at once was left out, because one writer at a time per workspace holds
> (technique-workspaces.md section 7).

## Why a skill of its own

`/run-tests` drives a browser through the agent (level N1): every click costs tokens and the run is not repeatable.
A spec (N2) is written once, runs the same way in CI, and its results enter the run log like any other. The cost of
writing is spent on the cases that deserve it (technique-browser.md section 13); the rest stays N1 in `/run-tests`.

## Layout in the workspace

```
<workspace>/e2e/
  package.json            @playwright/test pinned (an exact version, never ^ or latest)
  playwright.config.ts    baseURL and credentials from environment variables; JUnit reporter; trace on failure
  specs/<feature>.spec.ts one file per feature, one test per case
  test-results/           ignored by git (traces, captures, junit.xml)
```

- The folder lives in the workspace, never in `qa-assistant` and never in the application's repository, unless the
  project's own `CLAUDE.md` says the specs belong there (then say which rule was followed, rules.md section 8).
- The workspace's `.gitignore` already ignores `.env`; add `e2e/node_modules/` and `e2e/test-results/` when missing.
- `examples/demo/e2e/` is the reference: its `playwright.config.ts` reads the workspace's `.env` without printing it,
  retries once only in CI and keeps the trace of failures.
- Base URL: an environment variable named in `.env.example` (for example `SHOP_BASE_URL`), taken from the test
  environment of `project.yaml`. A URL that is not a declared test environment, or looks like production, stops the run.
- Credentials: variable names only (`process.env.<NAME>`); a missing variable throws a clear error that names the
  variable, never its value. A spec never contains a URL, account or password of a client.

## Installing (a download: ask first)

`cd <workspace>/e2e && npm install -D @playwright/test@<exact version> && npx playwright install chromium`. State the
package, version and what is downloaded before asking. Offline or refused: the cases are Blocked (tool), the specs may
still be written. `validate_project.py --environment` reports whether `node` exists.

## Authoring rules

- Title: `TC-LOGIN-001 · Login with valid credentials`. The id comes first and is exactly the library's; the run log
  refuses an unknown id. One case per test; a case with several expected results was split in /plan-tests.
- Locators: `getByRole`, `getByLabel`, `getByText`, then placeholder and test id; CSS or XPath only when nothing else
  exists, with a comment saying why (technique-browser.md section 4.2). An element that cannot be located by a
  user-facing name is worth an accessibility note in the observed text.
- Waits: Playwright's auto-waiting and web-first assertions (`await expect(locator).toBeVisible()`); never a fixed sleep.
- Independence: each test sets up its own state in `beforeEach` or through the API (technique-api.md); no test depends
  on another's data. One login per role is reused through a saved storage state, never written into the repository.
- Tags in the title for module and priority (`@smoke`, `@roles`), so `--grep` selects a batch.
- The expected value in an assertion is the case's, not what the application returned when the spec was written.
- Existing specs: read first, match their style, add what is uncovered, change one only when its case changed.

## Running

From `<workspace>/e2e`:

`npx playwright test --reporter=list,junit` with `PLAYWRIGHT_JUNIT_OUTPUT_FILE=test-results/junit.xml`

Select with `--grep "TC-LOGIN"` or a spec path; `--project=chromium` while developing. Use `--headed` only when the user
asks to watch. The runner parallelises by itself; sub-agents are not needed to run. Record the **build observed**
(footer, About page, response header; technique-browser.md section 2) before the first result: unknown is Blocked for
the run block, never assumed.

## From the runner's result to the run log

1. `python <qa>/scripts/run_log.py <workspace> mode <batch> "flows: Playwright specs in e2e/ (N2)"` once, before any result.
2. For each test in `test-results/junit.xml`, read the id at the start of the title and call
   `run_log.py <workspace> add <batch> <TC-id> <status> "<observed>" --environment <key> --build <build> --evidence <paths>`.
3. Status: passed -> `Pass`; failed -> `Fail` only after the classification below; the project's vocabulary applies
   (rules.md section 4). A skipped test is `Not run`, never `N/A` unless the case itself says it does not apply.
4. The observed text is what the runner saw (the assertion that failed, the final URL or message), not the expected
   result copied. Evidence for a failure: the trace and the screenshot under `evidence/`, named with the case id
   (`evidence/TC-LOGIN-002_trace.zip`); for a pass, the JUnit file named once and the case's own test name in the observed
   text. The evidence policy of the project is the minimum (rules.md section 6).
5. A test that passed on retry: record the result with the word flaky in the observed text and say so in the summary; a
   case that is flaky in two runs goes back to N1 and is not counted as a stable regression.

## Classifying a failure (before touching anything)

| Signal | Class | Action |
|---|---|---|
| Strict mode violation, locator not found, timeout waiting for an element that exists on screen | Test bug | Fix the locator or the wait in the spec |
| Assertion text differs from the case's expected text, the case is right | Application | Keep the spec, keep the evidence, /manage-bugs |
| Assertion text differs, the case is out of date | Doubt | Question: the expected result is not changed on a guess |
| Network error, 5xx from the environment, login page unreachable, browser failed to start | Environment or tool | One retry, then Blocked (environment or tool); never a defect |
| Passes alone, fails in the suite | Test bug (shared state) | Make the test independent |

Three rounds at most: after the third, stop with the open failures, the classification and what is needed. Never add
`test.skip`, loosen an assertion or lengthen a timeout across the board to reach green. A fix in the spec is reported as
such; a fix in the application is not in this skill's scope (it has no application code).

## Promotion and demotion

A case enters `e2e/` by the criteria of technique-browser.md section 13. A spec whose case changed its expected result
is updated with the case, in the same batch. A flaky spec is demoted to N1 (run through /run-tests) until it is stable;
nothing is deleted from `e2e/` without telling the user.
