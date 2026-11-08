# Skills guide

> How to use the eight skills of version 2 (skills-spec.md) in any workspace. Examples use the fictional **Demo Shop**
> (`DS-12` is a story, `TC-CART-003` a case).

## 1. The flow

```text
/project-context ──► /analyze-ticket ──► /plan-tests ──► /run-tests ──► /manage-bugs ──► /qa-report ──► /checkpoint
       resume            what changed        by risk ▲        per platform     batch review       totals        resume point
                                                     └──── /test-pbi runs this flow for one PBI and resumes it ────┘
```

Each skill can be used alone. `/test-pbi` keeps a record (`memory/pbi/<id>.json`) so it can stop at any step and
continue in another session.

**What a request authorises** (`procedures/rules.md` §5 and §8). "Test DS-12 on Android" covers the analysis, a plan
by risk, the run on Android in the test environment and the local records (run log, evidence, checkpoint), without
approving the same plan twice. "Plan the tests of DS-12" stops at the plan. Every write to the workbook or the
tracker (cases, results, bugs, comments) shows its exact content and waits for its approval; another platform,
environment or target is asked; a commit, a push or a sync of the skills is never a side effect. A workspace whose
`CLAUDE.md` asks for more (for example an approval of each platform's run) keeps its rule, and `/project-context`
names the rules in force.

## 2. Examples

| Skill | Say | Or type |
|---|---|---|
| `/project-context` | "Continue the tests of this project." / "Where were we?" | `/project-context demo-shop` |
| `/analyze-ticket` | "What changed in DS-12?" | `/analyze-ticket https://demo-shop.atlassian.net/browse/DS-12` |
| `/plan-tests` | "Plan the tests of the checkout coupon for Android and iOS." | `/plan-tests DS-12 android ios` |
| `/run-tests` | "Test the Account flow on the iPhone." | `/run-tests batch DS12_ios` |
| `/manage-bugs` | "Prepare the bugs of the last batch." | `/manage-bugs DS12_ios` |
| `/qa-report` | "Prepare the Android and iOS status for the team." | `/qa-report since 2026-11-01` |
| `/checkpoint` | "Save the state, I'm leaving." | `/checkpoint` |
| `/test-pbi` | "Test PBI DS-12 on Android." | `/test-pbi DS-12 android` |

## 3. Routing by natural language, and its limits

Claude Code chooses a skill by matching the request with each skill's `description`; the skill then runs its
preflight, which selects the workspace and checks the tools. Limits seen in practice:

- **The descriptions are in English** (the framework's language); a request in another language ("continua", "testa este PBI no iPhone") is matched by its meaning, which works in practice but is less certain than the skill's name.
- **Matching is a judgement of the model, not a rule.** A vague request ("look at this") may get no skill, or the
  closest one. Naming the skill (`/run-tests`) always works.
- **`/test-pbi` is never chosen on its own** for a request that only asks one step: "what changed in DS-12" is
  `/analyze-ticket`, "test DS-12" is `/test-pbi`.
- **A new session is needed to see new or changed skills.** Claude Code reads `~/.claude/skills/` when a session
  starts.
- **The preflight decides the project, not the wording.** Two projects with a similar name are a question; a skill
  never writes in a workspace it did not select.
- **A route is not a promise of tools.** `/run-tests` prints the procedure per module and whether its tools are
  available now; a missing tool blocks those cases, it does not change the route.

## 4. Where things live

| What | Framework (`<qa>`) | Workspace |
|---|---|---|
| Methodology, techniques, procedures | `docs/`, `docs/procedures/` | — |
| Skills | `.claude/skills/<name>/SKILL.md`, installed in `~/.claude/skills/` | — |
| Scripts, adapters | `scripts/`, `adapters/` | — |
| Permanent rules of the project | — | `CLAUDE.md`, or the headed rule sections of `context.md` when the project's `CLAUDE.md` imports it as its main document (methodology §12.2); never both |
| Configuration | — | `project.yaml` (credentials only by name; values in `.env`) |
| Resume summary | — | `context.md` (older entries in `archive/context/`) |
| Ticket memory, PBI progress | — | `memory/tickets/`, `memory/checks/`, `memory/pbi/` |
| Analyses and plans | — | `analysis/`, `batches/` |
| Results and evidence | — | `runs/`, `evidence/` |
| Reports | — | `exports/` |

Each kind of information has one main place. A summary points to the record; it does not copy it.

## 5. Installing and checking

From the root of qa-assistant:

```bash
python scripts/sync_skills.py            # compares, writes nothing
python scripts/sync_skills.py --apply    # installs the eight skills in ~/.claude/skills/
python scripts/workspace_select.py where # what runs: checkout, branch, commit, local changes, the record of the skills
```

`--apply` copies only `SKILL.md`, keeps skills of other origins, removes a retired name only when its own record says
it installed it, never writes through nor removes a link, refuses a skill whose scripts, modules or documents are
missing, and records the checkout, branch, commit and, per skill, whether its file had changes not committed. `where`
prints "PLUS N file(s) not committed" when that is so: the commit alone is never presented as what is installed. On Windows use the
same commands with `python` from the virtual environment.

**Start test (a new session):** open Claude Code in a workspace and ask "where were we?". Expected: `/project-context`
runs, its preflight prints the project and the framework, and the answer is the resume summary. Then type
`/test-pbi` with no argument: it must refuse with the expected form.

### Daily commands (from the root of qa-assistant, with its `.venv` Python)

| What | Command |
|---|---|
| Is this skill usable here, and is each route ready to run? | `scripts/workspace_select.py preflight --skill <skill> --workspace <ws>` (exit 0 = configuration valid; `Ready:` lines per platform) |
| The device, the app and the build, before a batch | `scripts/device.py <ws> check [--batch <batch>]`; an iPhone: `--platform ios` |
| Record one result | `scripts/run_log.py <ws> add <batch> <case> <status> "<observed>" --evidence <file> --build "<build>" --environment <env>` (a second execution of a case needs `--replace`) |
| Add evidence or a bug to a recorded result, keeping it | `scripts/run_log.py <ws> attach <batch> <case> --bug <id> \| --new-defect <draft.json> [--evidence <file>]` |
| Where each result stands (applied or not, per platform and build) | `scripts/qa_report.py <ws> totals` (`--detail` for every retest), `audit` |
| The resume record of a PBI | `scripts/pbi_state.py <ws> show <pbi>`; after a change: `check … --reopen` |
| Read a ticket | `python -m adapters.tickets <ws> collect <id>` (the children too unless `--no-expand`) |
| The skills installed and their origin | `scripts/workspace_select.py where`, `scripts/workspace_select.py skills` |

### Code of another branch, without its skills

A capability may live only in a feature branch for a while. (The iPhone backend did, until the audit of 2026-11-06
brought it into the main checkout: `device.py --platform ios` runs from main.) Use such a capability from a separate
worktree of that branch, calling only the script it brings, and keep the skills installed from the main checkout:

```bash
git -C <qa> worktree add <folder outside /tmp> <feature-branch>      # once; a folder under /tmp is lost on restart
python <folder>/scripts/device.py <workspace> --platform ios check  # run the script of the branch, with <qa>'s Python
```

- Never run `sync_skills.py --apply` from that worktree: an older branch installs its own skills next to these
  (version 1 names come back, a skill with the same name is overwritten) and records itself as their source. A skill
  whose copy was overwritten then stops at its preflight with exit 3 ("the global copy of /<skill> differs"), and
  `python <qa>/scripts/sync_skills.py --apply` from the main checkout restores the copies and removes the retired names.
- Do not open a Claude Code session inside that worktree: its own `.claude/skills/` (the branch's older skills) would
  load as project skills for that session. Open sessions in the workspace and call the branch's script by its path.
- A worktree already created under /tmp is moved, with its Git records, by `git -C <qa> worktree move <old> <new>`
  (it refuses if the worktree has uncommitted or untracked changes; look first with `git -C <old> status --ignored`).
  Then update the path in the workspace's wrapper scripts and notes.
- `python <qa>/scripts/workspace_select.py where` says which checkout the skills came from.

## 6. Version 1 names

| Version 1 | Version 2 |
|---|---|
| `/project-onboarding` | `/project-context` (a new folder: procedures/onboarding.md) |
| `/analyze-tickets` | `/analyze-ticket` |
| `/analyze-feature`, `/generate-cases`, `/plan-batch` | `/plan-tests` (procedures/planning.md A, B, C) |
| `/run-batch`, `/test-api`, `/audit-accessibility`, `/test-mobile` | `/run-tests` (procedures/run-*.md) |
| `/log-bug` | `/manage-bugs` |
| `/end-session`, `/dashboard` | `/qa-report` (and `/checkpoint` to close) |
| `/checkpoint` | `/checkpoint` (it no longer commits on its own) |

The version 1 names stay in the history (CHANGELOG.md, development-plan.md, old run logs). `sync_skills.py --apply`
removes their global copies.
