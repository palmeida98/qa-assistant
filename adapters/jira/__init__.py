"""
Jira Cloud + Xray Cloud back-end of the tracker adapter (docs/technique-jira-xray.md).

A stub until plan 10.2: the command line refuses it with exit 4 ("not implemented yet"). The common base, the
command line and the rules shared with Azure DevOps are in adapters/tracker/.
"""

from adapters.tracker.base import TrackerAdapter


class JiraAdapter(TrackerAdapter):
    tracker = "jira"
    implemented = False   # plan 10.2
