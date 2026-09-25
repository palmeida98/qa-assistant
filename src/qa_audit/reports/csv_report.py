"""results.csv: one row per finding.

Cells starting with = + - @ (or tab / carriage return) are prefixed with an apostrophe: text taken from an audited
site must not run as a formula when the file is opened in a spreadsheet (CSV injection)."""

from __future__ import annotations

import csv
import os

COLUMNS = ["id", "module", "check", "status", "severity", "confidence", "title", "url", "selector", "snippet",
           "description", "expected", "actual", "recommendation", "evidence", "references"]


def safe_cell(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def row(f) -> list[str]:
    ev = " | ".join(e.path or f"{e.kind}: {e.value}" for e in f.evidence)
    values = [f.id, f.module, f.check, f.status, f.severity, f.confidence, f.title, f.url,
              f.element.get("selector", ""), f.element.get("snippet", ""), f.description, f.expected, f.actual,
              f.recommendation, ev, " ".join(f.references)]
    return [safe_cell(v) for v in values]


METRIC_COLUMNS = ["module", "url", "metric", "value", "unit", "threshold", "result", "source"]


def write(result, path: str) -> None:
    # utf-8-sig: Excel opens it with the right encoding
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for finding in result.findings:
            w.writerow(row(finding))
    metrics = getattr(result, "metrics", None)
    if metrics:
        with open(os.path.join(os.path.dirname(path), "metrics.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(METRIC_COLUMNS)
            for m in metrics:
                w.writerow([safe_cell("" if m.get(c) is None else m.get(c)) for c in METRIC_COLUMNS])
