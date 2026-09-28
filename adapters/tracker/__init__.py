"""
Common base of the tracker adapters (Jira with Xray, Azure DevOps): docs/technique-jira-xray.md section 2, plan 10.1.

    python -m adapters.tracker <workspace> check|import-cases|export-cases|apply|bug-search ...

The back-ends are adapters/jira (plan 10.2) and adapters/azure_devops (plan 10.3).
"""

from .base import (
    Batch,
    Context,
    Item,
    Plan,
    TrackerAdapter,
    batch_marker,
    case_marker,
    prepare_batch,
)
from .config import TrackerConfig, load, validate_section
from .errors import (
    CONFLICT,
    INVALID,
    NOT_APPROVED,
    OK,
    READ_BACK,
    UNAVAILABLE,
    AuthFailed,
    ConfigError,
    Conflict,
    HttpError,
    InvalidInput,
    NotImplementedYet,
    RateLimited,
    TrackerError,
    Unavailable,
    UncertainWrite,
)
from .http import HttpClient, Request, Response, paginate_offset, paginate_token
from .record import IdCache, SentRecord, read_cases, write_cases

__all__ = [
    "CONFLICT", "INVALID", "NOT_APPROVED", "OK", "READ_BACK", "UNAVAILABLE", "AuthFailed", "Batch", "ConfigError",
    "Conflict", "Context", "HttpClient", "HttpError", "IdCache", "InvalidInput", "Item", "NotImplementedYet", "Plan",
    "RateLimited", "Request", "Response", "SentRecord", "TrackerAdapter", "TrackerConfig", "TrackerError",
    "Unavailable", "UncertainWrite", "batch_marker", "case_marker", "load", "paginate_offset", "paginate_token",
    "prepare_batch", "read_cases", "validate_section", "write_cases",
]
