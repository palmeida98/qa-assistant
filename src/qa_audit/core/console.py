"""Terminal output: UTF-8 everywhere, colours only on a TTY and without NO_COLOR (docs/qa-audit.md §13)."""

from __future__ import annotations

import os
import sys

UTF = {"ok": "✓", "fail": "✗", "warn": "!", "line": "─", "dot": "·"}
ASCII = {"ok": "OK", "fail": "X", "warn": "!", "line": "-", "dot": "-"}
_COLORS = {"red": "31", "yellow": "33", "green": "32", "cyan": "36", "dim": "2", "bold": "1"}


def setup_streams() -> None:
    # on Windows a pipe defaults to cp1252 and accents/symbols arrive garbled (same fix as scripts/*.py)
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def symbols(stream) -> dict:
    """Unicode symbols when the stream can encode them, ASCII otherwise."""
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        "".join(UTF.values()).encode(enc)
        return UTF
    except (UnicodeEncodeError, LookupError):
        return ASCII


class Console:
    def __init__(self, stream=None, color: bool | None = None):
        self.stream = stream or sys.stdout
        if color is None:
            color = bool(getattr(self.stream, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ
        self.color = color

    def style(self, text: str, *names: str) -> str:
        if not self.color or not names:
            return text
        codes = ";".join(_COLORS[n] for n in names)
        return f"\033[{codes}m{text}\033[0m"

    def print(self, text: str = "", *names: str) -> None:
        self.stream.write(self.style(text, *names) + "\n")
        self.stream.flush()
