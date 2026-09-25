"""Report writers (docs/qa-audit.md §7). Every string that comes from the audited site is untrusted."""

from __future__ import annotations

import os


def metric_value(m: dict) -> str:
    """A metric value for people: 'n/a' when the engine cannot measure it, never a guess."""
    v = m.get("value")
    if v is None:
        return "n/a"
    if isinstance(v, float):
        v = round(v, 3) if abs(v) < 10 else round(v)
    return f"{v} {m.get('unit', '')}".strip()


def write_all(result, formats: list[str]) -> dict[str, str]:
    from . import csv_report, html_report, json_report, md_report

    writers = {"json": ("results.json", json_report.write), "csv": ("results.csv", csv_report.write),
               "md": ("report.md", md_report.write), "html": ("report.html", html_report.write)}
    out = {}
    for fmt in ("json", "csv", "md", "html"):
        if fmt in formats:
            name, fn = writers[fmt]
            path = os.path.join(result.run_folder, name)
            fn(result, path)
            out[fmt] = path
    return out
