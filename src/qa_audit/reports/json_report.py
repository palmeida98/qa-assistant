"""results.json: the complete run, machine-readable."""

from __future__ import annotations

import json


def write(result, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
