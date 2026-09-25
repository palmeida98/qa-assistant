# qa-audit example (fictional)

`qa-audit.yaml` is a complete configuration for the fictional site `new-project.example`, commented key by key. It
shows where each project decision goes: pages and depth, extra exclusions, evidence, thresholds, a form that may be
submitted (only with `--allow-submit`), and visual masks and viewports.

Keep a real project's copy with the project, not in this repository (docs/qa-audit-guide.md §4, §11).
`tests/unit/test_example_config.py` loads this file with every module's validation, so it stays valid.
