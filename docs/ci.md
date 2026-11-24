# CI

The CI is sized for how the framework is really used: **one person, on macOS, with this GitHub repository as a backup and a
proof of concept** (no other contributors, no branch protection, public). The local `pre-commit` (denylist, ruff, the Excel
golden, the unit tests) is the first barrier; the CI is the copy that runs off the machine, so a commit made with the hooks
skipped, or on a machine without LibreOffice, is still checked. It was rebuilt on 23.11.2026 from seven jobs and three
workflows (see *What was removed* below).

The README badge shows the **CI workflow** on `main`. Both workflows use a read-only token, never persist the checkout's
credentials and pin the official actions to their major tag (`actions/checkout@v7`, ...).

## Workflows

**[`ci.yml`](../.github/workflows/ci.yml)** — every pull request, every push to `main`, and on demand:

| Job | What | When |
|---|---|---|
| `lint-secrets` | ruff and detect-secrets with the baseline (same versions as `.pre-commit-config.yaml`) | always |
| `tests` | the Excel golden (`scripts/golden_check.py`) and the **whole** unit suite on Ubuntu **with LibreOffice**, so the recalculation tests run too | always |
| `install` | a clean clone: `scripts/install.sh`, an audit of the fixture site (exit 1 expected from the planted defects), and every skill of `.claude/skills/` synced to a temporary folder and preflighted on `examples/demo` | `main` and manual runs only |

**[`qa-audit.yml`](../.github/workflows/qa-audit.yml)** — the audit engine's integration tests against the local fixture site:

- Runs on a push to `main` or a pull request **only when it touches** `src/`, `tests/integration/`, `tests/fixtures/`,
  `scripts/ci_test_summary.py`, `pyproject.toml`, `requirements.txt` or the workflow itself (kept honest by
  `tests/test_ci_filter.py`), and on demand. A change that touches none of these shows no `qa-audit` check at all.
- **Chromium** by default. A manual run with `engines: all` (Actions → qa-audit → Run workflow) adds Firefox and WebKit and
  the cross-engine comparison: use it after a change to how the engine drives the browsers.

## What "green" means

Each test job writes its counts and its skips by reason to the run summary (`scripts/ci_test_summary.py`) and fails when no
test ran or a skip has a reason that is not allowed. In `tests` the only allowed skip is a test that exists only for
Windows. A skipped test proves nothing.

## What is not covered, on purpose

- **macOS.** It is where the framework is developed and used, and the local `pre-commit` runs the suite there.
- **Windows.** Not used; unconfirmed. The two Windows-only tests are skipped (allowed) on Linux.
- **The demo's end-to-end specs** (`examples/demo/e2e`, Playwright against a public practice site). They need three demo
  logins and a third-party site; run them locally (`npm ci && npx playwright test` in that folder, with the
  `DEMO_SAUCE_*` variables set). No end-to-end coverage is claimed from the CI.
- **The personal denylist** runs only in the local `pre-commit` (the list lives outside the repository).

## What was removed on 23.11.2026, and why

| Removed | Why |
|---|---|
| `tests-macos` | duplicated the local machine, where `pre-commit` already runs the suite |
| `windows-only-tests`, the *Windows suite* workflow and `.github/known-failures-windows.txt` | Windows is not used |
| `golden` as its own job | folded into `tests`, which now installs LibreOffice: one suite run, no LibreOffice skips |
| the three browsers on every engine change, `changes` and the `qa-audit` aggregate | Chromium by default, the other two on demand; a workflow-level path filter replaces the `changes` job |
| `ci-status` | an aggregate meant for branch protection, which a one-person backup repository does not use |
| `demo-e2e` workflow | its secrets were never set, so it never ran a spec; the specs stay runnable locally |
| `.github/dependabot.yml` and the SHA pins | a monthly pull request to bump pins is noise here; official actions on major tags need no bumps |

`scripts/ci_test_summary.py` keeps its `--known-failures` option (tested), though no workflow uses it now.

Privacy guards on every commit: detect-secrets with a baseline and a private denylist kept outside the repository (a missing
or unreadable list is reported, never called clean). Hooks can be bypassed and CI runs after a push, so review stays
necessary.
