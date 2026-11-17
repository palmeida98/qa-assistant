# CI

<!-- Moved from the README on 17.11.2026, unchanged except for the heading and the relative links. -->


The README badge shows the **CI workflow** ([`ci.yml`](../.github/workflows/ci.yml)) on `main` and nothing else: it says nothing
about the Windows suite or `demo-e2e`, which are separate workflows below.

CI runs on every pull request, on push to `main` and on demand (`workflow_dispatch`), with a read-only token, no persisted
credentials and actions pinned to a commit SHA:

| Job | What |
|---|---|
| `lint`, `secrets` | ruff; detect-secrets with a baseline |
| `tests` (Ubuntu), `tests-macos` | the whole unit suite |
| `windows-only-tests` | only the two tests that exist for Windows |
| `golden` | the Excel golden and the LibreOffice-dependent tests |
| `qa-audit` (Chromium, Firefox, WebKit) | the audit engine against a local fixture site, plus a cross-engine comparison |
| `install` | a clean clone: the audit of the fixture site, the eight skills synced to a temporary folder and their preflight on the demo |
| `ci-status` | one aggregate result: success only when every job above succeeded (or, for the browsers, was skipped by design) |

- **Pull request vs `main`.** On a pull request the three browser jobs (about 80% of the runner minutes) run only when it touches
  `src/`, the integration tests or the fixture site, the summary script they call, the dependency files or `.github/` (job
  `changes`; checked by `tests/test_ci_filter.py`); otherwise `qa-audit` reports "skipped by design" and `ci-status` still
  passes. `main` and manual runs always run everything. A pull request keeps one run (a newer push cancels the older); `main`
  runs are never cancelled.
- **`ci-status` is not enforced yet.** It is meant to be the single check to require, but on 11.11.2026 `main` has no branch
  protection and no ruleset (checked through the GitHub API), so nothing blocks a merge on it today.
- **What "green" means.** Each test job writes its counts and skips by reason to the run summary and fails if no test ran or a
  skip has a reason that is not allowed (`golden`: no skip at all). A skipped test proved nothing.
- **Windows is unconfirmed, and separate.** The **Windows suite** workflow
  ([`windows-suite.yml`](../.github/workflows/windows-suite.yml)) runs the whole suite on Windows and is not part of `ci-status`. Since
  11.11.2026 it passes with no known failure left (the list in [`.github/known-failures-windows.txt`](../.github/known-failures-windows.txt)
  is empty; a failure that is not on it is reported as new), **but with skips**: the destructive-command guard is a POSIX hook, so
  its tests are skipped there, and the Excel recalculation tests need LibreOffice, which that runner does not have. A skipped test
  proves nothing, so a green Windows run does not show that those parts work on Windows, and Windows stays unconfirmed.
- **`demo-e2e` is a separate workflow** ([`demo-e2e.yml`](../.github/workflows/demo-e2e.yml)), not a CI check: Playwright specs against
  a public practice site, started by hand or weekly when the repository variable `DEMO_E2E_SCHEDULE` is `true`. It needs the
  three repository secrets (named in the header of the workflow file); without them it fails with a clear message. No end-to-end coverage is claimed until it has run with executed specs.

**Dated results** (commit `e83d9b2`, 11.11.2026): CI [run 43004572200](https://github.com/palmeida98/qa-assistant/actions/runs/43004572200)
succeeded: Ubuntu 1846 tests run, 0 failed, 23 skipped; macOS 1846 run, 0 failed, 24 skipped; `golden` 44 run, 0 skipped;
`windows-only-tests` 2 run. **Windows suite** [run 43004572292](https://github.com/palmeida98/qa-assistant/actions/runs/43004572292)
succeeded: 1846 run, 0 failed, 43 skipped (11 guard tests that are POSIX only, 21 that need LibreOffice, 6 that use shell-script fake
interpreters, 3 `time.tzset`, 1 permissions, 1 `/proc`), no known and no new failure. Earlier the same day that workflow was red
(commit `2307430`: 13 failures, [run 42957805113](https://github.com/palmeida98/qa-assistant/actions/runs/42957805113)); six were test
path-separator problems and seven the POSIX-only guard. `demo-e2e` has not been observed running. Numbers change with the code: each run's summary has its own counts. Local and CI environments differ in some
versions (for example Pillow and LibreOffice); `python scripts/workspace_select.py where` prints the difference.

Privacy guards on every commit: detect-secrets with a baseline and a private denylist kept outside the repository (a missing or
unreadable list is reported, never called clean). Hooks can be bypassed and CI runs after a push, so review stays necessary.
