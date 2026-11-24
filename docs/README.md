# Documentation entry point

Read only what the current task needs. This proposed index does not move or replace any specification.

| Need | Main reference |
|---|---|
| Choose one of the nine skills | [Skills guide](skills-guide.md) |
| Skill ownership and dependency contract | [Skills specification](skills-spec.md) |
| Universal QA method and source hierarchy | [Methodology](methodology.md) |
| Shared approval and evidence rules | [Shared rules](procedures/rules.md) |
| Long workflow steps | `procedures/` |
| Technical details | `technique-*.md`, selected by the active module |
| Workspace configuration schema | [Project template](project.yaml) |
| New computer / recovery | [Setup](workspace/setup.md), [resume](workspace/resume.md) |
| Website audit engine | [User guide](qa-audit-guide.md), [specification](qa-audit.md) |
| Reproducible fictional walkthrough | [Demo walkthrough](demo-walkthrough.md) |
| Tracker state: implemented, tested, piloted | [Tracker validation](tracker-validation.md) |
| CI jobs, what "green" means and what is not covered | [CI](ci.md) |
| Applying a run log from a workspace's GitHub Actions (manual, dry run first) | [Workspace CI](workspace/ci/README.md#applying-a-run-log) |
| Agent-behaviour scenarios (prepared, not run) | [Skills evals](skills-evals.md) |
| Framework development and history | [Development plan](development-plan.md), [changelog](../CHANGELOG.md) |
| Audit and pilot rationale (dated, in `history/`) | [Audit](history/audit-2026-11-06.md), [pilot improvements](history/pilot-improvements-2026-11-07.md), [methodology migration notes](history/methodology-migration-notes.md) |

The roadmap, audit reports and old migration notes explain history. They are not daily setup instructions and do not override the current skill contract or project rules.
Keep runtime documents at their existing paths: the skills, scripts, packaging and tests refer to them.
Client data belongs only in the separate workspace. A documentation proposal does not authorise tests, tracker writes, installation, Git mutations or publication.
