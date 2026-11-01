"""
Read-only ticket analysis (docs/technique-tickets.md; plan phase 14): the deterministic part of `/analyze-tickets`.

    config.py        the `tickets:` section of project.yaml, independent of `tracker:`
    model.py         the normalised snapshot, its coverage, redaction, paged reads and the read-only transport
    azure_devops.py  Azure DevOps: the read-only source and the normaliser of its REST answers
    jira.py          Jira: the same, without any Xray call
    collect.py       resolving references, bounded expansion, scopes
    compare.py       what changed between a reference and a new read
    impact.py        the existing cases a ticket touches: the library, read only
    memory.py        memory/ in the workspace, what is current, and the block of context.md
    analysis.py      validating and merging the agent's analysis
    render.py        the generated Markdown views
    cli.py           the command line
"""

import adapters.tracker  # noqa: F401  (it puts scripts/ on sys.path, for `common`)
