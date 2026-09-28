# Setting up a new computer

What to install on a computer that has never run `qa-assistant`, before resuming a project with
[`resume.md`](resume.md). Windows first (the commands use `winget`); macOS and Linux are noted where they differ.
Install only what the project's active modules need: `validate_project.py --environment` (step 7) tells you what is
still missing.

| For | Tool | Needed when |
|---|---|---|
| Everything | git, Python 3.12+, GitHub CLI (`gh`), Claude Code | always |
| Tracker `excel` | LibreOffice | the workbook is recalculated after each batch |
| `functional-web`, `exploratory` | Node.js 22+ | the Playwright MCP runs with `npx` |
| `accessibility` (`qa-audit`) | Playwright browsers | `qa-audit` drives a real browser |
| `mobile` (Android) | Android SDK (platform-tools, emulator, a system image), Java 17, Maestro | an emulator or a device over USB |
| `api` | nothing more | — |

## 1. Base tools (always)

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e          # or newer; tick "Add to PATH" if asked
winget install --id GitHub.cli -e
gh auth login                                        # an account with access to the private repositories
```

Claude Code: install it as the official guide says (`npm install -g @anthropic-ai/claude-code`, or the Windows
installer) and log in once. macOS: `brew install git python@3.12 gh`. Linux: the distribution's packages.

Open a new terminal afterwards so the PATH changes take effect; `git --version`, `python --version` and `gh auth status`
must all answer.

## 2. `qa-assistant`

```bash
mkdir -p ~/qa && cd ~/qa                               # qa-assistant and qa-workspaces/ live side by side
gh repo clone <owner>/qa-assistant
cd qa-assistant
pip install -r requirements.txt                       # the Excel adapter and the scripts
pip install -e .                                      # qa-audit (or scripts\install.ps1 / scripts/install.sh: a .venv and the browsers)
python scripts/sync_skills.py --apply                 # the skills, copied to ~/.claude/skills/
pre-commit install                                    # only if you will change the framework (pip install pre-commit)
```

## 3. LibreOffice (tracker `excel`)

```powershell
winget install --id TheDocumentFoundation.LibreOffice -e
```

The adapter finds `soffice` in the usual install folder; without it, `apply` still saves but exits 3 (recalculation).

## 4. Node.js (web modules)

```powershell
winget install --id OpenJS.NodeJS.LTS -e
```

## 5. Browsers for `qa-audit` (accessibility)

```bash
python -m playwright install chromium                 # or the browser in accessibility.browser
qa-audit doctor
```

## 6. Android (module `mobile`)

1. **Java 17** (Maestro needs it): `winget install --id Microsoft.OpenJDK.17 -e`.
2. **Android SDK**, without Android Studio: download the *command-line tools* from developer.android.com, unzip them to
   `%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\`, set `ANDROID_HOME` to `%LOCALAPPDATA%\Android\Sdk` and add
   `platform-tools` and `emulator` to the PATH. Then:
   ```powershell
   sdkmanager "platform-tools" "emulator" "platforms;android-35" "system-images;android-35;google_apis;x86_64"
   ```
   Use a **Google APIs** image (not Play Store): it has root, which `setup_emulator.py` needs for ARM-only apps
   (technique-mobile §1.1). If Windows Application Control blocks the SDK tools, ask the machine's administrator; the
   emulator also needs hardware acceleration (Windows Hypervisor Platform: `emulator -accel-check`).
   Company computers: check first that you may install the SDK and run an emulator.
3. **Maestro**: in Git Bash, `curl -fsSL "https://get.maestro.mobile.dev" | bash`, then open a new terminal
   (`maestro --version`). It installs into `~/.maestro/bin`.
4. **The build is not in git.** `mobile.build.source` in the workspace's `project.yaml` says where it comes from —
   often a release of the workspace's own private repository:
   ```bash
   gh release download <tag> --repo <owner>/<company>-<project> -D ../qa-workspaces/<company>/<project>/app
   ```
   `setup_emulator.py` checks it against `mobile.build.sha256` before installing it.
5. **A real device** instead of the emulator: USB debugging on, `adb devices` shows it, `mobile.serial` in
   `project.yaml`. Some manufacturers refuse input injection without a SIM ("USB debugging (security settings)"):
   then the run is assisted — you tap, the driver only reads (technique-mobile §2).

## 7. Check and continue

Follow [`resume.md`](resume.md) from step 3: clone the workspace, create `.env` from `.env.example` (values from your
own secret store, never from the repository), then

```bash
python scripts/validate_project.py ../qa-workspaces/<company>/<project> --environment
```

Every line must be `[ok]` for the active modules; a `[missing]` line says how to install it. For `mobile`:
`python scripts/setup_emulator.py <ws> --apk <ws>/app/<build>.apk --dry-run`, then without `--dry-run`.

## Before leaving a computer

`/checkpoint`, then push only when you approve it (`python scripts/workspace_git.py <ws> push`). What is not pushed
does not exist on the next computer. `.env`, the build and the emulator stay on each computer and are set up again.
