# Resuming a project on another computer

The steps of methodology §12.3, in order. On a computer that has never run `qa-assistant`, install the tools first with [`setup.md`](setup.md). The examples use the fictional Demo Shop (`demo/demo-shop`); `<qa>` is the
folder of `qa-assistant`, `<ws>` the workspace's folder.

1. **Prerequisites.** git; Python 3.12 or newer (`requires-python` in `pyproject.toml`); the GitHub CLI logged in
   (`gh auth login`) with access to the workspace's private remote.
2. **Install `qa-assistant`** (next to `qa-workspaces/`):
   ```bash
   git clone https://github.com/<owner>/qa-assistant.git
   cd qa-assistant
   pip install -r requirements.txt        # the adapter and the scripts (openpyxl, PyYAML)
   pip install -e .                       # qa-audit (or scripts/install.sh / scripts\install.ps1: a .venv, browsers)
   python scripts/sync_skills.py --apply  # the skills the workspaces use (~/.claude/skills/), and which checkout they came from
   ```
3. **Clone the workspace** from its private remote into `qa-workspaces/<company>/<project>/` (a root elsewhere:
   `python scripts/workspace_select.py set-root <path>`; `technique-workspaces.md` §3):
   ```bash
   gh repo clone <owner>/demo-demo-shop ../qa-workspaces/demo/demo-shop
   ```
4. **Create `.env`** from `.env.example` (it has only the names) and fill in each value from **your own secret
   store** (a password manager, the team's vault) — never from a chat, a log, an e-mail or the repository. `.env` is
   ignored by the workspace's git and is never committed.
5. **Check the environment**:
   ```bash
   python scripts/validate_project.py ../qa-workspaces/demo/demo-shop --environment
   ```
   Each tool is printed as `[ok|missing] <tool>: <detail or how to install it>`; `--strict` makes a missing tool an
   exit 1. What is missing is installed before testing, per active module:
   - tracker `excel`: LibreOffice (the workbook's recalculation);
   - `functional-web`, `exploratory`: Node.js 22+ (the Playwright MCP runs with `npx`);
   - `accessibility`: `pip install -e .` gives `qa-audit`; `python -m playwright install chromium` (or the
     `accessibility.browser`), then `qa-audit doctor`;
   - `mobile`: the Maestro CLI; for Android the SDK (`adb`, `emulator`, with `ANDROID_HOME` set) and then
     `python scripts/setup_emulator.py <ws> --dry-run`, and without `--dry-run` once the plan is right;
   - `api`: nothing beyond Python.
6. **Get the build** (mobile apps): it is not in git. `mobile.build.source` in `project.yaml` says where it comes
   from (usually a release of the workspace's private repository, `release <tag>`:
   `python scripts/workspace_build.py <ws> fetch` downloads it to `app/<tag>.apk` and checks its SHA-256); `setup_emulator.py <ws> --apk <path>` checks it against `mobile.build.sha256` before installing it.
7. **Continue**: open Claude Code in the workspace, read `context.md` and the latest run log in `runs/`, and go on
   from the last checkpoint (methodology §12.1). Each skill starts with its preflight, which says whether the skill,
   the code and the project match: `python scripts/workspace_select.py preflight --skill <name> --workspace <ws>`. Whatever a run needs that is not in the repository (a device, a login
   link) is written in `context.md` or `project.yaml`. Tickets already followed with `/analyze-tickets` are in
   `memory/` (committed with the workspace), so the next check compares with them (technique-tickets.md §5).
8. **Or on a runner**: a mobile batch can also run on a GitHub Actions runner of the workspace's repository, from a
   browser only (methodology §12.4; settings in [`ci/README.md`](ci/README.md)).
9. **Before leaving a computer**: `/checkpoint` (it commits locally and says how many commits are not pushed), then
   push only when you approve it (`python scripts/workspace_git.py <ws> push`; never `--force`). Unpushed work does
   not exist on the next computer.
10. **If a command says another process is writing the workspace**: two sessions on one project write one at a time (technique-workspaces.md §7). The second one waits up to 30 seconds, then stops with nothing written: run the same command again. If that process is gone, a lock older than 15 minutes, or of a process that no longer exists, is taken over by itself; or delete `.qa-assistant.lock` in the workspace.
