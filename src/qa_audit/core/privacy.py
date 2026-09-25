"""Redaction of secrets in text evidence (docs/qa-audit.md §7).

Traces and videos cannot be redacted (binary, page content): they are off by default. Everything textual the
engine writes (headers, logs) goes through here first.
"""

from __future__ import annotations

REDACTED = "«redacted»"
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                  "x-auth-token", "x-csrf-token", "x-xsrf-token"}


class Redactor:
    """Redacts secret headers by name and known secret values (e.g. credentials from the environment)."""

    def __init__(self, secret_values: dict[str, str] | None = None):
        # longest first, so a secret that contains another is replaced whole
        self.values = sorted(((v, k) for k, v in (secret_values or {}).items() if v), key=lambda x: -len(x[0]))

    def text(self, s: str) -> str:
        for value, name in self.values:
            s = s.replace(value, f"«{name}»")
        return s

    def headers(self, h: dict) -> dict:
        return {k: (REDACTED if k.lower() in SECRET_HEADERS else self.text(str(v))) for k, v in h.items()}
