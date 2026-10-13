# Remote execution — the workspace's CI

Templates for running a project's mobile batches on a GitHub Actions runner started from the **workspace's own
private repository** (methodology §12.4, `technique-mobile.md` §11; plan phase 11). They hold nothing of a project:
every value comes from the workspace's `project.yaml` (`ci:` and `mobile:`). The examples use the fictional Demo Shop
(`demo-shop/demo-shop-qa`, app `com.demoshop.app`).

| Template | Copied to (by `scripts/workspace_ci.py install`) | What it is |
|---|---|---|
| `mobile.yml` | `.github/workflows/qa-mobile.yml` | runs a batch on an emulator, opens a pull request |
| `apply.yml` | `.github/workflows/qa-apply.yml` | applies the merged run logs with the project's adapter |
| `agent-prompt.md` | `.github/qa/agent-prompt.md` | the prompt of Claude Code headless (conversational mode) |
| `pr-body.md` | `.github/qa/pr-body.md` | the pull request's body, filled by `workspace_ci.py pr-body` |

## Cost: nothing beyond what the user already has

- **GitHub Actions**: GitHub-hosted **standard** runners only (`ubuntu-24.04`); a larger runner is refused by
  `validate_project.py` and by the workflow (larger runners are always paid). A private repository on the Free plan has
  2 000 Linux minutes a month, shared by all the user's private repositories, and each job is rounded up to the
  minute. Set the **spending limit to 0** (account Settings → Billing and licensing → Budgets and alerts: a budget of
  0 for Actions with "stop usage when the budget limit is reached"), so a job stops instead of being billed.
  `workspace_ci.py check` warns when it can read a non-zero limit (organisations) and shows the minutes used this
  month when the billing API answers.
- **Claude**: the user's **subscription** (Pro, Max, Team or Enterprise), through a long-lived OAuth token — never a
  paid API key. Its usage limits apply; nothing is billed extra.
- **Storage**: releases (the builds), issues (the login), pull requests and the Actions cache are free.

## Repository settings (once per workspace)

1. **The repository is private** (`workspace_ci.py install` refuses otherwise).
2. **Actions permissions** (Settings → Actions → General): allow GitHub Actions; "Workflow permissions": read and
   write, and allow GitHub Actions to create pull requests. The workflows ask only for `contents: write`,
   `pull-requests: write` and `issues: write`.
3. **Secrets** (Settings → Secrets and variables → Actions → Secrets), created by the user — never on a command line
   of a script, never in a file, a chat or a log. `workspace_ci.py install --yes` runs `gh secret set NAME` for each
   one missing, and `gh` asks for the value in the terminal — only when the script runs in **your own interactive
   terminal** (on Windows a real console: not the NUL device nor a pipe, which Python's `isatty()` takes for a
   terminal there). Run it yourself, never from an agent session (Claude Code, a CI step): there it sets no secret
   and only prints the `gh secret set NAME --repo <owner>/<repo>` commands for you to run in your own terminal. A
   secret set with a wrong or empty value is fixed by running `gh secret set NAME --repo <owner>/<repo>` again (it
   overwrites the value):
   - `CLAUDE_CODE_OAUTH_TOKEN`: run `claude setup-token` on your own computer (it opens the browser, then prints a
     token valid for one year; it is not saved anywhere) and paste it when `gh secret set` asks for it;
   - `QA_ASSISTANT_TOKEN`: a fine-grained personal access token with **read-only** access to the contents of the
     private `qa-assistant` repository and nothing else (expiry as short as you are comfortable with);
   - every name in `ci.secrets` of `project.yaml`: the `.env` variables the runner needs (e.g. the test account's
     e-mail for the login helper flow; with Jira / Azure DevOps, the tracker's credentials for `qa-apply.yml`).
4. **Variables** (same page, tab Variables): `QA_ASSISTANT_REPOSITORY` = `<owner>/qa-assistant` and, optionally,
   `QA_ASSISTANT_REF` (default `main`).
5. **Spending limit at 0** (above).
6. **`main` accepts the apply workflow's commit**: `qa-apply.yml` commits the updated workbook (or `tracker/`) to
   `main` with the workflow's token after each merge. A branch rule on `main` would have to let GitHub Actions push.

## Running a batch

1. Publish the build: `python scripts/workspace_build.py <ws> publish <path.apk>` (dry run), then with `--yes` after
   you approve it: a release `build-<versionName>-<versionCode>` with the APK, and `mobile.build` updated.
2. Plan the batch with `/plan-batch`; the confirmed plan is saved as `batches/<name>.yaml` and pushed.
3. On GitHub: Actions → "QA mobile" → Run workflow: `batch` (the planned batch's name, or case IDs separated by
   spaces), `build` (empty = `mobile.build`), `mode`.
4. When the login issue appears (you are mentioned), copy the link from the e-mail **as text** and paste it as a
   comment — nothing else in the comment. Do not open it in your own browser first (that spends it). The runner opens
   it on the emulator, deletes your comment and closes the issue.
5. Review the pull request: the run log, the evidence, the proposed bugs and the questions. Edit the run log in the
   pull request (drop or change a proposed bug, remove `"proposed"` to approve it, answer each `Question` with the
   final status), then merge. `qa-apply.yml` applies it and comments the result.

The runner tests on its emulator (`emulator-5554`) through `adb`, whatever `mobile.serial` and `mobile.input` say:
`qa-mobile.yml` sets `QA_MOBILE_SERIAL` and `QA_MOBILE_INPUT`, which the device scripts read before `project.yaml`
(`technique-mobile.md` §1.2). A project tested locally on a physical phone with the companion driver needs no change
for the runner, and a flow that only that phone can run (e.g. a vendor build's behaviour) is Blocked there.

What the runner cannot do (a camera, a microphone, biometrics, a login nobody pasted in time) is **Blocked**, never
Fail. The time each run took and the agent's turns are in the pull request.
