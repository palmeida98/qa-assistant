"""Report writers (docs/qa-audit.md §7). Every string that comes from the audited site is untrusted."""

from __future__ import annotations

import os

from . import csv_report, html_report, json_report, md_report

WRITERS = {"json": ("results.json", json_report.write), "csv": ("results.csv", csv_report.write),
           "md": ("report.md", md_report.write), "html": ("report.html", html_report.write)}


def write_all(result, formats: list[str]) -> dict[str, str]:
    out = {}
    for fmt in ("json", "csv", "md", "html"):
        if fmt in formats:
            name, fn = WRITERS[fmt]
            path = os.path.join(result.run_folder, name)
            fn(result, path)
            out[fmt] = path
    return out
