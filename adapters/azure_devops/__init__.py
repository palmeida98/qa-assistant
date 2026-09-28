"""
Azure DevOps Services (Test Plans + Boards) back-end of the tracker adapter (docs/technique-azure-devops.md).

A stub until plan 10.3: the command line refuses it with exit 4 ("not implemented yet"). The common base, the
command line and the rules shared with Jira are in adapters/tracker/.
"""

from adapters.tracker.base import TrackerAdapter


class AzureDevOpsAdapter(TrackerAdapter):
    tracker = "azure-devops"
    implemented = False   # plan 10.3
