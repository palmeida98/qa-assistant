# Workspaces and skills technique

> **Layer 2 — How a request finds its framework, its project and its skill**
> Version 1.0 · 2026-11-01 · Plan, phase 15.
>
> Project agnostic: the examples use the fictional **Demo Shop**. Subordinate to `methodology.md`. The engine is `scripts/workspace_select.py`, on top of the discovery the hub already used and of `scripts/validate_project.py`.

---

## 0. What this is

The framework is a **central library of skills**: each skill is one type of test or one QA function. A "cloud of skills" means this reusable organisation, not a hosted service: nothing migrates, and execution stays local unless a project configures a runner (methodology §12.4).

The user picks a project, asks for a task in natural language, and gets traceable work, grounded results and memory in the right workspace. The engine is never copied into a project: skills are central, configuration and data are per workspace. A client's exception stays in its workspace; a reusable capability enters the framework as configuration (methodology §1.4).

## 1. Layers

| Layer | Responsibility | Data allowed |
|---|---|---|
| Central framework | Methodology, skills, scripts, adapters, validation, tests | Generic content and fictional fixtures |
| Selection and routing | Which workspace, intent, skill and preconditions | Minimum metadata of the project selected |
| Workspace | Configuration, rules, cases, memory, results, evidence | That project's private data |
| Execution tools | Browser, APIs, device, Excel, trackers | Only what the authorised action needs |

```text
<framework>/qa-assistant/      .claude/skills/<skill>/SKILL.md, docs/, adapters/, scripts/, tests/, qa_ui/
<workspace-root>/<company>/<project>/   or   <workspace-root>/<project>/
    CLAUDE.md, project.yaml, context.md, memory/, batches/, runs/, evidence/, tracker/ or workbook/, history/
```

The root can have any name. Nothing here creates, renames or migrates a folder.

## 2. Finding the framework

A skill in `~/.claude/skills/` runs in a workspace that knows nothing about the framework's location, so each skill starts by finding `<qa>`, in this order:

1. `$QA_ASSISTANT_ROOT`: explicit, for one shell or one machine;
2. `root` in `~/.claude/skills/.qa-assistant-source.json`: the checkout the skills were last synced from (§9);
3. `../../../qa-assistant` from the workspace: the historical layout, the last resort, never the only one.

Whatever the order gives is **checked, not trusted**: `python <qa>/scripts/workspace_select.py preflight` compares it with what the skill needs (§6). A missing folder or script means the skills are newer than that checkout: the skill stops and says so.

`python scripts/workspace_select.py where` shows the framework as it really runs: its root; the main checkout when this one is a worktree (read from `.git`, without running git); branch, commit and uncommitted files; the Python interpreter; the **real origin of each import** (`adapters`, `qa_ui`, `qa_audit`, `openpyxl`, `yaml`); the installed versions that differ from the pinned ones, as information; the skills' sync record.

An editable `qa-audit` points at the checkout it was installed from, which may not be this one: `where` and `preflight` report it. A different version than the CI's is not called incompatible: it is reported, and the isolated run of §11 is what decides.

## 3. The workspace root

In this order, each named in the output as the source of the value:

1. `--root PATH`;
2. `$QA_WORKSPACES_ROOT`;
3. `workspaces_root` in `~/.qa-assistant/config.json` (`python scripts/workspace_select.py set-root PATH`, which refuses a folder that does not exist or lies inside the framework);
4. `qa-workspaces/` next to the **main checkout**, so a worktree finds the same root as its main checkout.

**A root is never created.** One that does not exist is an error that names where the value came from and how to set it, because a wrongly resolved path must not leave an empty root in the wrong place.

## 4. Discovery

`discover(root)` looks at two layouts, without following links (or junctions):

- `<root>/<project>/project.yaml`: a project whose id is `<project>`; its subfolders are not looked at;
- `<root>/<company>/<project>/`: a project whose id is `<company>/<project>`, for each subfolder of a folder without its own `project.yaml`;
- a folder with no `project.yaml` and no subfolder is a new project, id `<folder>`.

Names follow the hub's rule: letters, digits, `.`, `_`, `-`, never starting with `.` or `_`. Anything else is ignored.

| Status | Meaning | Proposed |
|---|---|---|
| `valid` | `project.yaml` is readable and its base fields are right (`validate_project.base_problems`) | — |
| `incomplete` | `project.yaml` cannot be parsed or has problems, or the workspace predates plan 8.11 | `/project-onboarding` |
| `not-configured` | No `project.yaml`: incomplete in the strongest sense, a new folder | `/project-onboarding` |
| `inaccessible` | A link, or the system refused to read it | Check the folder |

`valid` says the configuration is well formed, not that the tools are there: that is `preflight`.

**Listing reads only each `project.yaml`.** It never opens tickets, evidence, workbooks, `.env` or memory, and it shows the name, the id, the status and the path relative to the root. Listing does not authorise reading more.

## 5. Selection

`python scripts/workspace_select.py resolve [<project>] [--workspace PATH] [--root PATH]`, in this order:

1. **An explicit path:** normalised with links resolved. It must exist and lie outside the framework (`examples/` is the exception: the demo).
2. **A name:** matched against the discovery by id, then folder, then the `project:` of `project.yaml`, then a part of the id or name. One match is selected; several are ambiguous.
3. **The current directory**, or the nearest parent with a `project.yaml`; inside the framework this step is skipped. A new folder inside the root is selected too, as `not-configured`, so `/project-onboarding` can start there.
4. **The root:** selected only when it holds exactly one project and that project is `valid`. Otherwise the list is shown and the user chooses.

**Nothing is chosen silently between candidates.** Ambiguity is a short question with the name, id and relative path of each one.

## 6. Preflight

`python <qa>/scripts/workspace_select.py preflight --skill <name> [<project>] [--workspace PATH] [--root PATH] [--json]` runs before a skill does anything. It writes nothing and calls no external service.

It checks, in order:

1. **The skill against the code:** the skill exists in `<qa>`, its `requires-modules` and `requires-scripts` exist there, the Python imports come from `<qa>`, and the global copy equals `<qa>`'s (not checked when the session runs inside `<qa>`, where the project's copy is the one in use). This must not need the modules it checks: it survives an old checkout with no `adapters/tickets/`.
2. **The project:** the selection of §5, and the status `requires-project` asks for.
3. **The configuration the skill needs** (`requires-config`).
4. **The tools** (`requires-tools`): the lines of `validate_project.environment()`, only those of this skill. A missing tool is shown, not fatal: a case that needs it is `Blocked` (environment), never Pass.

Then it presents the project, the task, the environment keys, the tools, the writes and the approvals. The skill goes on without asking, unless there is ambiguity or an authorisation is missing.

| Exit | Meaning | What the skill does |
|---|---|---|
| 0 | Ready; missing tools, if any, are listed | Shows the summary and goes on |
| 1 | Found, not usable: `incomplete`, `not-configured`, `inaccessible`, or a configuration the skill needs is missing | Shows why and proposes `/project-onboarding` |
| 2 | Ambiguous: several candidates | Asks which one, with the list |
| 3 | The skill and the code do not match | Stops and shows the instruction, which names the checkout that has the code. Nothing is installed or changed |
| 4 | Nothing to select: the root or the path does not exist, or a usage error | Shows the expected configuration |

**Unattended runs skip this step.** The prompts of `qa-ui` (`qa_ui/prompts/batch.md`) and of the runner (`docs/workspace/ci/agent-prompt.md`) already name the workspace and `<qa>`, and their tool lists allow only the commands they need.

## 7. Isolation

- **Writes** go through `common.workspace_path(workspace, ...)`: the path, with links resolved, must stay inside the workspace and outside `history/`. A traversal, an absolute path elsewhere or a link pointing out is refused before anything is written. It guards the ticket memory, the tracker's records, `run_log.py`, `qa-ui`'s batches, the `--output` of `api_request.py`, `audit_a11y.py` and `run_mobile.py`, the evidence folder of `device.py` and the `--dest` of `workspace_build.py`.
- **Two explicit exceptions, both stated by configuration or by the runner:** the workbook lives where `excel.file` of `project.yaml` says, possibly a synced folder (`technique-excel.md` §2); on a runner, `run_mobile.py` may keep its report in `$RUNNER_TEMP`, a throwaway folder, as `docs/workspace/ci/mobile.yml` does.
- **Credentials** are read from the selected workspace's `.env` only, and the sanitiser of `/analyze-tickets` knows only that file's values.
- **Memory and sessions** belong to one workspace. Two projects with the same name, ticket id or source never share anything: nothing global holds project data.
- **Claude's personal or global memory is not a source of truth for a project.** On resuming, the agent loads `context.md`, the relevant records and the configuration. Real results stay in the workspace; the framework receives only generalised improvements and fictional examples.

| Artefact | Role | Rule |
|---|---|---|
| `CLAUDE.md` | Permanent instructions | No temporary conclusion becomes a rule |
| `project.yaml` | Configuration | The project's values; credentials only by reference |
| `context.md` | Current summary and resume point | Localised updates; independent content is kept |
| `memory/tickets/` | Ticket references | Identity made of service, organisation and project |
| `memory/checks/` | Checks and differences | Past reads are kept; analyses are linked and versioned |
| `runs/` | Results of execution | Observed apart from expected; an applied batch is never rewritten |
| `evidence/` | Proof of execution | Proportional to the project's policy |
| `history/` | Protected history | Never changed |

## 8. The skill contract and routing

Each skill's frontmatter has a `metadata` map of plain strings, the **single source** of its contract. `python scripts/workspace_select.py skills` prints the catalogue from it, so there is no second list to keep in step:

| Key | Holds |
|---|---|
| `intent`, `inputs`, `config`, `tools`, `outputs`, `writes`, `approvals`, `failures`, `resume` | The contract, for people and for the agent |
| `requires-modules` | Python modules of the framework it needs (comma-separated) |
| `requires-scripts` | Scripts it runs, relative to `<qa>` |
| `requires-config` | What `project.yaml` must have: `tickets`, `api`, `accessibility`, `mobile`, `excel` |
| `requires-tools` | Lines of `validate_project.environment()` it needs, plus `mcp` for a browser MCP named in `.mcp.json` |
| `requires-project` | `valid` (the default), or `any` for a skill that can start from an empty folder |

A skill existing does not prove its tools are available: that is what `requires-tools` answers.

**Routing is light.** Claude Code matches the request against the skills' descriptions and the skill runs its preflight, so the user need not know the commands. Methodology §3 has the table. No entry skill exists because no gap needs one: what was missing was finding the framework and the project, which this helper does for every skill. A second execution engine, a manual catalogue or a database would add nothing.

Analysis and execution stay apart. "Analyse these tickets" gives an analysis. "Test this feature" may go through planning and execution, respecting the approved batch and the project's rules.

## 9. Syncing the skills

`python scripts/sync_skills.py --apply` copies each `SKILL.md` to `~/.claude/skills/` and writes `.qa-assistant-source.json` next to them: the root, branch, commit, whether the tree had uncommitted changes, the instant and each skill's hash. So the copy always says which code it belongs to.

- It copies only `SKILL.md`, so **a skill folder holds nothing else** (a test keeps it so). Detail lives in `docs/`, found through `<qa>`. No secret or foreign file can travel this way.
- It refuses to install a skill whose `requires-modules` or `requires-scripts` are missing from the same checkout.
- Syncing from a worktree or another branch is allowed and said aloud: workspaces then run that checkout's code until the next sync. It never merges anything.
- `sync_skills.py` without `--apply` also says when the record names another checkout.

## 10. Failure and recovery

| Situation | What happens |
|---|---|
| The recorded checkout was removed | `<qa>` falls to the next option of §2; preflight catches any mismatch |
| `<qa>` is older than the skill | Exit 3, or no script at all: stop, with the instruction |
| A broken `project.yaml` | `incomplete`, with the parser's message for the selected project |
| A folder without permission | `inaccessible`; the rest of the list still works |

## 11. Validation

`tests/test_workspace_select.py` builds two fictional projects, an old checkout without the module, and a real git worktree. No test reads a real workspace or the real `~/.claude`.

The whole suite also runs in an isolated environment equal to the CI's (Python 3.12 and the pinned versions), in a throwaway folder, without touching the computer's installations. A difference from the CI only counts when a test shows it.
