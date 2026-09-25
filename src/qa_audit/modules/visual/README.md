# Visual module

`qa-audit <url> --type visual` · specification: `docs/qa-audit.md` §19

## Goal

Notice when pages look different from an approved baseline, at several viewport sizes, and catch basic responsive
problems. A visual difference is a question for a person (`needs-human`), not a bug.

## Prerequisites

Pillow: `pip install -e .[visual]`. Without it the module is skipped in `--type full` (with the reason) and
`--type visual` is a usage error.

## Configuration (`config.yaml`, override under `modules.visual`)

| Key | Default | Meaning |
|---|---|---|
| `viewports` | desktop 1440×900, tablet 768×1024, mobile 390×844 | sizes screenshotted and checked |
| `tolerance` | `16` | per-channel change (0–255) ignored (anti-aliasing) |
| `max_diff_ratio` | `0.001` | share of changed pixels that makes a `visual-diff` (0.1 %) |
| `mask` | `[]` | CSS selectors hidden in screenshots (clocks, carousels, ads) |
| `max_height` | `8000` | px; taller screenshots are cut |
| `baseline_dir` | `~/.qa-audit/baselines` | also `--baseline-dir` |
| `update_baseline` | `false` | also `--update-baseline` |

## Checks

| Check | Status · severity |
|---|---|
| `visual-diff` | needs-human · medium, with baseline, current and diff images |
| `visual-size-changed` | needs-human · low |
| `horizontal-overflow` | warning · medium (low when only on desktop) |
| `viewport-meta-missing` | warning · low |

A metrics row per page and viewport: share of changed pixels, `baseline created` / `unchanged` / `changed`.

## Baselines and privacy

Baselines are screenshots of the audited site, so they are personal to your machine:
`~/.qa-audit/baselines/<host_port>/<engine>/<viewport>/<page>.png`, outside any repository and the output folder,
with a `.gitignore` of `*` at the root. `--cleanup` does not delete them (they must survive between runs);
`qa-audit baselines` lists them and `qa-audit baselines --delete <host>` (or `all`) removes them. The report says
where they are.

Workflow: first run creates the baselines; later runs compare; review a `visual-diff`, and if the change is
intended, run once with `--update-baseline`.

## Limits

- Baselines are per engine: engines render fonts differently.
- Screenshots are taken with animations disabled and the caret hidden, after web fonts load; content that changes
  on every load (dates, random images, ads) must be masked or it will show as a difference.
- The viewport is changed on a desktop browser context (no mobile emulation); use `--device` for a full mobile run.
- The module takes its own screenshots whatever `--screenshots` says: they are its measurement.
