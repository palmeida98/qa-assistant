"""
Rebuilds the demo's workbook: builds it again (build_workbook.py) and applies,
in execution order (1st ts of each batch), every batch of runs/ (batch_*.json)
through the adapter.

It serves to add areas or cases to the demo without losing the results already
recorded: the workbook is always the builder + the batches applied. Each batch's
label is derived from the file name (batch_YYYYMMDD_<id> ->
"[YYYY-MM-DD, batch_YYYYMMDD_<id>]") and every date written is the batch's 1st ts
(apply --now), so the cells always come out the same (plan 7.6;
tests/test_demo_reproducible.py). The file is not equal byte for byte: the
.xlsx properties (created/modified) are those of the rebuild.

    python examples/demo/rebuild_workbook.py [workspace]   (from the root of qa-assistant)

Without an argument it rebuilds the demo's own workbook; with a folder that has a
copy of the demo's project.yaml, it rebuilds there (used by the test).

Exits with apply's first code other than 0 (technique-excel.md section 1).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(os.path.dirname(HERE))]

import build_workbook
import yaml

from adapters.excel.apply import main as apply


def label(name: str) -> str:
    m = re.match(r"^batch_(\d{4})(\d{2})(\d{2})_", name)
    return f"[{m.group(1)}-{m.group(2)}-{m.group(3)}, {name}]" if m else f"[{name}]"


def order(path: str) -> tuple[str, str]:
    """Order of application = order of execution: the batch's first ts, then the name.
    (By name alone, batch_..._a11y01 would come before batch_..._api01 and the BUG-n would swap.)"""
    with open(path, encoding="utf-8-sig") as f:
        tss = [x.get("ts") or "" for x in json.load(f).get("results", [])]
    return (min((t for t in tss if t), default="9999"), os.path.basename(path))


def rebuild(workspace: str = HERE) -> int:
    with open(os.path.join(workspace, "project.yaml"), encoding="utf-8-sig") as f:
        target = os.path.join(workspace, yaml.safe_load(f)["excel"]["file"])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    build_workbook.build(target)
    print(f"workbook built again: {target}")
    for batch in sorted(glob.glob(os.path.join(HERE, "runs", "batch_*.json")), key=order):
        name = os.path.splitext(os.path.basename(batch))[0]
        print(f"\n== {name}")
        args = [workspace, batch, "--label", label(name)]
        first_ts = order(batch)[0]
        if first_ts != "9999":  # batch without ts: the clock is used
            args += ["--now", first_ts]
        rc = apply(args)
        if rc != 0:
            return rc
    return 0


def main() -> int:
    return rebuild(sys.argv[1] if len(sys.argv) > 1 else HERE)


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
