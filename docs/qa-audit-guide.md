# qa-audit — user guide

> How to install, run, read and extend `qa-audit`. The rules behind every behaviour are in the specification,
> `docs/qa-audit.md` (cited as §n). Every example uses fictional targets.

---

## 1. What it is

`qa-audit` audits a website from its URL: it discovers pages, runs QA modules on each one, keeps the evidence in a
local folder and writes a report. It is part of the `qa-assistant` framework, next to the workspace layer
(methodology, skills and the Excel adapter), and needs no project setup.

| Module | Finds |
|---|---|
| `accessibility` | WCAG 2.1 A/AA issues (axe-core), keyboard traps, focus visibility, reflow at 320 px; lists the manual checks |
| `security` | passive only: HTTPS/TLS, security headers, cookie flags, mixed content, version disclosure |
| `performance` | TTFB, FCP, LCP, CLS against the web.dev thresholds; timings and sizes measured |
| `functional` | broken links and resources, JavaScript errors, redirect chains, form hygiene; opt-in form submit |
| `visual` | screenshots per viewport compared with baselines; horizontal overflow; viewport meta tag |

What it is **not**: a certification (accessibility), a penetration test (security), field performance data, or a
replacement for designed test cases. Every finding carries evidence; what the engine cannot decide is marked
`needs-human`.

**Only audit sites you own or are authorized to test.**

---

## 2. Install

Requirements: Python 3.12+, Git. The browsers are downloaded once, at install time.

```bash
git clone <qa-assistant repository>
cd qa-assistant
./scripts/install.sh                 # Linux / macOS: .venv + package + Chromium, then qa-audit doctor
.\scripts\install.ps1                # Windows PowerShell
```

Options: `--all-browsers` / `-AllBrowsers` (Firefox and WebKit too); `--with-deps` on Linux installs the system
libraries the browsers need (sudo). By hand: `pip install -e .[visual]` and `python -m playwright install chromium`.

`qa-audit doctor` checks Python, Playwright, each browser (including its cryptography: a browser that cannot load
HTTPS is reported), the bundled axe-core, ffmpeg (video), Pillow (visual) and the output folder. On Windows, Smart
App Control can block WebKit or Firefox files; `doctor` says so, and the state can change over time (§13).

---

## 3. Run

```bash
qa-audit https://new-project.example                     # every module that can run here
qa-audit https://new-project.example --type accessibility,security
qa-audit https://new-project.example --max-pages 5 --depth 1 --browser firefox
qa-audit https://new-project.example --report html --cleanup
qa-audit https://new-project.example --config qa-audit.yaml
```

The terminal shows the plan (target, browser, modules, limits), one line per page, one line per module and the
summary; the report path is at the end. Exit codes: `0` ok · `1` problems at or above `--fail-on` (default `high`) ·
`2` usage or configuration · `3` target unreachable · `4` browser could not start. In CI:
`--fail-on medium` to be stricter, `--fail-on none` to never fail.

Safety defaults you can rely on (§5, §6): GET navigation only, same origin only, no clicks, no typing, no form
submitted; logout/delete-style links and `robots.txt`-disallowed paths are never requested; 500 ms between pages;
a user agent that ends in `qa-audit/<version>`.

---

## 4. A new QA project

Keep the project's configuration **outside this repository**, next to the rest of the project (for example in its
workspace, `qa-workspaces/<company>/<project>/qa-audit.yaml`, or in the project's own repository):

1. Copy `examples/qa-audit/qa-audit.yaml` there and adapt it: pages, depth, modules, thresholds, masks, forms.
2. Keep credentials in environment variables only: the configuration names them (`fields_env`), never holds them.
   A secret-looking key with a literal value makes the file invalid (§4).
3. Run from that folder: `qa-audit https://staging.new-project.example --config qa-audit.yaml`. The output goes
   to `./qa-output/` there, which ignores itself in Git.
4. First run with `visual`: it creates the baselines (in `~/.qa-audit/baselines`, outside every repository).
   Review later differences and accept intended ones with `--update-baseline`.
5. When the project ends: `qa-audit baselines --delete <host>` and delete its `qa-output/`.

---

## 5. Choosing modules

`--type full` (default) runs every available module; a module that cannot run here (e.g. `visual` without Pillow)
is skipped with the reason. A list runs only those: `--type performance,functional`. A module asked for by name
that cannot run is a usage error (exit 2). Modules run on each page in a fixed order: the ones that only read the
page first (performance measures an untouched page), the ones that change it (viewport, Tab key) last.

Module options live under `modules.<name>` in the configuration; each module's `README.md` next to its code
(`src/qa_audit/modules/<name>/`) lists them.

---

## 6. Browsers and viewports

| Option | Effect |
|---|---|
| `--browser chromium\|firefox\|webkit` | engine (one per run) |
| `--headed` | watch the browser |
| `--viewport 1280x800` | window size (default 1440×900) |
| `--device "iPhone 15"` | Playwright device (size, user agent, touch) |
| `--ignore-https-errors` | self-signed staging certificates (their trust is then not judged) |
| `--block-third-party` | block requests outside the target (the page then differs from what visitors see) |

Browser choice matters: CLS is only measured in Chromium (elsewhere `n/a`, never estimated); visual baselines are
per engine.

### Pages behind a login

Configure `auth:` in the project's configuration file (§20 of the spec). Two methods:

```yaml
auth:                                   # a plain login form
  method: form
  login_url: /login
  username_env: NEW_PROJECT_QA_USER     # names of environment variables, never the values
  pass_env: NEW_PROJECT_QA_PASS
  success_url: "/account"               # or success_selector: "#logout"
```

```yaml
auth:                                   # Active Directory, a second factor, a CAPTCHA
  method: manual
  login_url: https://staging.new-project.example/login
  success_selector: "#user-menu"
  manual_timeout_s: 300
```

With `manual` the browser opens visibly and the terminal asks you to log in; the audit continues when the success
check passes. Either way there is **one attempt** (a failed login is exit 3, never retried, so a shared account is
not locked), the session's logout links are never followed, a page that sends you back to the login page stops the
crawl ("session ended"), and the credential values are replaced by `«VARIABLE_NAME»` in everything the engine
writes. Traces are refused with `auth`. To audit a fixed set of pages rather than crawl, list them in
`crawl.pages` and set `crawl.follow_links: false`.

---

## 7. Reading the results

`qa-output/<date_time>/` contains `report.html` (open it in any browser; it loads nothing from the network),
`report.md`, `results.json` (everything, for tools), `results.csv` and `metrics.csv`, `screenshots/` and
`logs/run.log`. `--cleanup` deletes the folder after the summary.

Each finding has an id (`ACC-003`, `SEC-001`, …), a module and check, a **status**, a **severity**, a
**confidence**, the URL, the element (selector and HTML) when there is one, expected / actual, steps, evidence and a
recommendation with references.

| Status | Means | Do |
|---|---|---|
| `problem` | a rule is violated and the engine shows it | fix, or record why it is acceptable |
| `warning` | likely an issue; context can make it fine | judge it |
| `needs-human` | the engine cannot decide | a person checks (with the evidence given) |

Severities: `critical` is never assigned automatically; `high` blocks a task for a group of users; `medium` a
clear violation with a workaround; `low` minor; `info` an observation (§9). The metrics table shows every measured
value with its threshold. The manual checklist (accessibility) is never "done" by the tool.

A finding is a lead with evidence, not a verdict: reproduce it before filing a bug. In a workspace, a confirmed
finding becomes a bug through the usual flow (`/registar-bug`).

---

## 8. Creating a new module

1. Write its behaviour first in `docs/qa-audit.md` (a new section: checks, status and severity of each, limits).
2. Create `src/qa_audit/modules/<name>/`:

   ```text
   README.md       goal, prerequisites, configuration, checks, output, limits
   config.yaml     defaults (the user overrides them under modules.<name>)
   module.py       MODULE = MyModule()
   checks.py       the checks as pure functions (unit-testable)
   ```

   ```python
   from ...core.findings import Evidence, Finding
   from ...core.module import AuditModule

   class MyModule(AuditModule):
       name, title, id_prefix = "seo", "SEO", "SEO"
       order = 15                                   # < 50 if it only reads the page

       def validate_config(self, cfg): return []    # problems -> exit 2 before the audit
       def on_page(self, page, info, ctx):          # page: live Playwright page; info: what discovery saw
           if not info.title:
               ctx.add_finding(Finding(module=self.name, check="title-missing", title="Page without title",
                                       status="warning", severity="low", url=info.final_url,
                                       evidence=[Evidence(kind="html", value="<title></title>")]))
           else:
               ctx.passed(self.name)
       def finalize(self, ctx): ...                 # after the crawl (aggregate, site-wide checks)

   MODULE = MyModule()
   ```

3. Plant its defects in `tests/fixtures/site/` and add integration tests (every planted defect found, clean pages
   clean) and unit tests for `checks.py`.

The core finds it without changes. A module in another package registers through the entry point group
`qa_audit.modules` in its own `pyproject.toml`.

---

## 9. Adding a kind of test to an existing module

Add a pure function in the module's `checks.py` with unit tests, call it from `on_page` or `finalize`, give it a
check id, status and severity in the module's README and in the specification table, and plant a defect in the
fixture site. Keep the evidence rule: a `problem` or `warning` without evidence is refused by the core.

---

## 10. Contributing through Git

See `CONTRIBUTING.md`: specification before code, one task per commit (`<phase>.<task>: <summary>`), the tests
(`python -m unittest discover -s tests`; integration: `python -m unittest discover -s tests/integration`), and the
pre-commit hooks, which are never skipped. CI runs lint, unit tests, secret scan, the Excel golden test, the demo and
the engine's integration tests on Chromium, Firefox and WebKit.

---

## 11. Keeping client data out of the repository

- The repository holds the tool, never a project: no client names, URLs, accounts, credentials, screenshots,
  reports or findings. Project configuration and output live with the project (§4 above).
- `qa-output/` ignores itself (`.gitignore` with `*`), and so do the visual baselines' root; this repository's
  `.gitignore` also covers evidence, traces, HAR files and local configuration.
- Credentials only in environment variables; configuration files with literal secrets are refused.
- Traces and videos (which hold cookies and page content) are off by default.
- The pre-commit hooks block secrets (detect-secrets) and the names on your personal denylist
  (`~/.qa-denylist.txt`, outside the repository, also inside Office files and file names). A blocked commit is
  fixed by changing the content.
- Nothing is sent anywhere but to the target: no telemetry; axe-core is bundled; the browsers' own background
  traffic is disabled and a test proves it with a recording proxy (§8).
