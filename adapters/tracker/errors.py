"""
Exit codes and errors of the tracker adapters (technique-jira-xray.md section 2.4).

Every error carries its exit code, so the command line maps an exception to a code in one place.
"""

from __future__ import annotations

OK = 0            # sent and read back; or nothing to send
NOT_APPROVED = 1  # dry run with something to send: repeat with --yes after the user approves
CONFLICT = 2      # cross-check / conflict with the tracker: nothing written
UNAVAILABLE = 3   # network, authentication, rate limit or server error: a send may be partial
INVALID = 4       # invalid batch, input or configuration: nothing resolved nor sent
READ_BACK = 5     # sent, but the read back does not match what was sent


class TrackerError(Exception):
    """Base of the adapter's errors; `code` is the exit code, `problems` an optional list shown one per line."""

    code = INVALID

    def __init__(self, message: str, problems: list[str] | None = None):
        self.problems = list(problems or [])
        super().__init__(message)


class InvalidInput(TrackerError):
    """Invalid batch, cases file or draft (exit 4): nothing resolved nor sent."""

    code = INVALID


class ConfigError(InvalidInput):
    """Invalid configuration (the tracker's section of project.yaml) or a credential missing from .env."""


class NotImplementedYet(TrackerError):
    """A back-end (or one of its directions) that a later task of the plan builds (exit 4)."""

    code = INVALID


class Conflict(TrackerError):
    """The tracker does not agree with the input: a case, bug or test plan not found, a case mapped twice (exit 2)."""

    code = CONFLICT


class Unavailable(TrackerError):
    """Network, server error or rate limit after the retries (exit 3)."""

    code = UNAVAILABLE


class AuthFailed(Unavailable):
    """A second 401, or a 403 (exit 3). The message never has the token."""


class RateLimited(Unavailable):
    """429 after the retries, or on a request that is not retried (exit 3)."""


class UncertainWrite(Unavailable):
    """A write without an answer (timeout, 502/503/504): it may have been done. Never retried blindly: the next run
    searches the marker (technique-jira-xray.md section 2.5) before creating again (exit 3)."""


class HttpError(TrackerError):
    """A 4xx the client does not handle itself. 404/409/412 are conflicts with the tracker (exit 2); the others
    mean the tracker refused the input (exit 4). The back-ends may catch it and raise something more precise."""

    def __init__(self, status: int, message: str, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(message)
        self.code = CONFLICT if status in (404, 409, 412) else INVALID
