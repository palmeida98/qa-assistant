# Contributing

This repository is public but proprietary (see `LICENSE`). These are the rules for changing it.

## Before you change anything

1. Read `docs/methodology.md` §15 (validation before adoption): **specification first, then code, then
   tests**, then commit.
2. The roadmap is `docs/development-plan.md`: one task per commit, message `<phase>.<task>: <summary>`.
3. The engine's specification is `docs/qa-audit.md`. Change it in the same commit as the behaviour it
   describes.

## Client data never enters this repository

- No client, company or project names, environment URLs, accounts, credentials, screenshots, videos, traces,
  results, bugs or test cases from real projects. Examples use fictional targets (`example.com`,
  `shop.example`, `new-project.example`) or the local fixture site.
- Real projects live in workspaces outside this repository (`../qa-workspaces/<company>/<project>/`).
- `qa-audit` output (`qa-output/`) is ignored by Git twice: the repository `.gitignore`, and a `.gitignore`
  with `*` that the engine writes into every output root.
- Two pre-commit guards block a commit: **detect-secrets** (with `.secrets.baseline`) and the personal
  **denylist** (`~/.qa-denylist.txt`, outside the repository; it also reads Office files and file names).
  A blocked commit is fixed by changing the content, never by skipping the hook (`--no-verify`).

## Set up

```bash
pip install -e .[dev,visual]
python -m playwright install chromium firefox webkit
pip install pre-commit && pre-commit install
qa-audit doctor
```

`pre-commit install` writes the hooks into the repository's git directory, which every worktree of the checkout shares.
Check them with `python scripts/workspace_select.py where` ("Commit guards"). The two denylist hooks run in their own
environment, so they need no `python` on the PATH. The golden and the unit-test hooks are `language: python` too and call
`scripts/qa_python.py`, which finds the interpreter that has the framework's dependencies (`$QA_PYTHON`, the checkout's
`.venv`, or an environment on the PATH, such as an active one): **no activation and no `python` on the PATH are needed**
when the checkout has its `.venv` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`). With none
usable the hook stops with the commands that create one (exit 3): a stop, never a silent pass; `--no-verify` is not the
answer. Hooks installed before this change keep working: the hook scripts call `pre-commit` through the interpreter that
installed them, and the configuration is read at each commit.

The denylist is the owner's private list (`~/.qa-denylist.txt`). Without one the guard says `NOT CONFIGURED` and checks
nothing; to make that a failure on your computer: `git config --global qa-assistant.denylistRequired true` (or
`QA_DENYLIST_REQUIRE=1`). An unreadable list, or a file it cannot read, is a failure; a PDF or a legacy `.doc/.xls/.ppt` has its
name checked and is listed as not inspected. The guard never prints a term of the list: a match names its line in the list.

## Tests

| Command | What |
|---|---|
| `python -m unittest discover -s tests` | Framework and engine unit tests (no browser; also run by pre-commit and CI) |
| `python -m unittest discover -s tests/integration` | Engine against the local fixture site, per browser (`QA_AUDIT_TEST_ENGINES`, default `chromium,firefox`) |
| `python scripts/golden_check.py` | Excel adapter golden test (needs LibreOffice) |
| `ruff check .` | Lint |

## Adding an audit module

1. Describe it in `docs/qa-audit.md` §10 (goal, checks, severities, limits).
2. Create `src/qa_audit/modules/<name>/` with `README.md`, `config.yaml` and `module.py` (`MODULE = ...`,
   a subclass of `qa_audit.core.module.AuditModule`); split the checks into `checks.py` if it helps.
3. Plant its defects in `tests/fixtures/site/` and add integration tests: every planted defect is found and
   the clean pages have no problems.
4. External modules (another package) register through the `qa_audit.modules` entry point group instead.

A finding that is a `problem` or a `warning` must carry evidence; the core refuses it otherwise.

## Updating dependencies

Versions are exact (`pyproject.toml`, `requirements.txt`). An update is its own commit with the tests passing.
The bundled axe-core is updated with `python scripts/update_axe.py <version>` (checks npm's integrity hash).
