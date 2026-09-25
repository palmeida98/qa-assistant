"""The contract every audit module implements (docs/qa-audit.md §10)."""

from __future__ import annotations

import inspect
import os
from typing import ClassVar

import yaml


class AuditModule:
    name = ""                  # used by --type; lowercase, e.g. "accessibility"
    title = ""                 # shown in the terminal and reports
    description = ""
    id_prefix = ""             # finding ids, e.g. "ACC" -> ACC-001
    requires_browser = True
    manual_checks: ClassVar[list[dict]] = []   # [{"id", "title", "how", "references"}], listed in the report, never "done"
    disclaimer = ""            # shown with the module's results

    @property
    def package_dir(self) -> str:
        return os.path.dirname(inspect.getfile(type(self)))

    def default_config(self) -> dict:
        path = os.path.join(self.package_dir, "config.yaml")
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def applicable(self, ctx) -> tuple[bool, str]:
        """(applies, reason) — used by --type full to skip modules that make no sense for the target."""
        return True, ""

    def on_page(self, page, info, ctx) -> None:
        """Called once per visited page while it is loaded (page: Playwright Page; info: PageInfo)."""

    def finalize(self, ctx) -> None:
        """Called after the crawl."""
