"""
Jira Cloud + Xray Cloud back-end of the tracker adapter (docs/technique-jira-xray.md; plan 10.2).

    python -m adapters.tracker <workspace> check|import-cases|export-cases|apply|bug-search ...

The common base, the command line and the rules shared with Azure DevOps are in adapters/tracker/; this package has
the API calls (api.py), the field mapping (fields.py), ADF <-> text (adf.py) and the four directions (adapter.py).
"""

from .adapter import JiraAdapter

__all__ = ["JiraAdapter"]
