# Preflight (step 0 of every skill)

> Layer 3 — What the preflight prints and what to do with each exit. Written once; every skill's step 0 points here.
> Step 0 itself stays in each skill (it finds `<qa>`, so an installed copy can resolve this document).

The preflight is read only. It runs once per skill and workspace in a session, while `project.yaml` and the checkout stay the same.

## Exit codes

| Exit | Meaning | What to do |
|---|---|---|
| 0 | The configuration is valid | Show the project, task, environment and tools it printed, and go on. A `Ready: NO` line names what blocks a route now and the action; it is never a reason to stop the other work |
| 1 | Not usable as it is | Show why and propose `/project-context` (in `/project-context` itself, go on to onboarding) |
| 2 | Several projects fit | Ask only which project, with its list |
| 3 | The skill and the code do not match (or no interpreter with the framework's dependencies) | Stop and show what it says. Nothing is installed or changed for you |
| 4 | Configuration missing | Show the configuration it asks for |
| 5 | The session is open in another workspace than the target | STOP. Show the two workspaces and the command it prints to open the right session; nothing else runs here |

`<qa>` or the preflight script missing counts as exit 3.

## The lines it prints

- `Route:` names the procedure of each module of the project (for example `mobile -> docs/procedures/run-mobile.md`), with `[missing: <tool>]` when a tool is not available.
- `Next:` is the one command to run before the work.
- `Ready:` says, per route, whether it can run now.
- The current workspace is the one the preflight selected. Never write in another one.

## Under a launcher

A run started by a launcher that already validated the workspace (the prompt of the workspace's CI: it names the workspace and qa-assistant and allows no other command) uses those paths instead of the search in step 0. The preflight still checks what the launcher did not (the target, the build observed), and a check that cannot run leaves its cases Blocked, never assumed.

## Finding `<qa>`

`$QA_ASSISTANT_ROOT`, else `"root"` in `~/.claude/skills/.qa-assistant-source.json`, else `../../../qa-assistant` from the workspace (`../../qa-assistant` when the project is directly under the root; in the demo, `../..`). `python` in every command is the interpreter that `python3 <qa>/scripts/qa_python.py` prints.
