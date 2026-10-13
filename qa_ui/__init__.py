"""
qa-ui: the local web interface of a workspace (docs/qa-ui.md, plan phase 12).

    python -m qa_ui <workspace> [--port 8765] [--no-open] [--claude PATH] [--max-turns 150] [--timeout-minutes 60]

A front of the work cycle (methodology 3): cases in, the suggested types of test, the batch plan, the runs, the
user's approvals and the apply step. Every step is an existing skill, script or adapter; nothing here is a second
implementation of them. Standard library only, plus what the framework already needs (pyyaml, openpyxl).

The scripts of scripts/ are imported as they import each other: with scripts/ on sys.path.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "scripts"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODULES = ("functional-web", "exploratory", "api", "accessibility", "mobile")   # methodology 4.1, closed list
