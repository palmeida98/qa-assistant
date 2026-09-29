"""
Azure DevOps Services (Test Plans + Boards) back-end of the tracker adapter (docs/technique-azure-devops.md; plan
10.3).

    python -m adapters.tracker <workspace> check|import-cases|export-cases|apply|bug-search ...

The common base, the command line and the rules shared with Jira are in adapters/tracker/; this package has the REST
calls (api.py), the field mapping (fields.py), HTML <-> text (html.py), the steps XML (steps.py) and the four
directions (adapter.py).
"""

from .adapter import AzureDevOpsAdapter

__all__ = ["AzureDevOpsAdapter"]
