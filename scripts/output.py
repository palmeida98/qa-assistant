"""
What the scripts print: a short card by default, the complete text on request, and the details in a file (phase 17.4).

The terminal is for following the work and deciding; the details are for reviewing. So a command prints a **brief** card
(a few lines) by default and says where the complete text is; `--detail` (or `terminal: normal`) prints it all as before.
Nothing that needs a decision is ever dropped by a line limit: errors, blocks, caveats and decisions are **must** lines,
shown whatever the budget; only information lines are counted, and what was left out says how to read it.

Settings, in this order (first wins): the command's `--brief` / `--detail`, the environment variable `QA_OUTPUT`
(`brief`, `normal`, `verbose`), the project's `output:` section, these defaults.

    output:
      terminal: brief        # brief | normal | verbose   (normal and verbose print the complete text)
      max_cols: 100          # width of a card line
      max_lines: 12          # information lines of a card (must lines are not counted)
      truncate: warn         # a value is never cut without saying where it is complete
      detail_files: true     # write the file that holds the complete text and cite its path
      reports: [summary, technical]

A notice that does not change is shown once (`Notices`): the state is kept outside the workspace, in
`$QA_ASSISTANT_HOME` (default `~/.qa-assistant`), keyed by workspace; a notice that needs an action is always shown.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

MODES = ("brief", "normal", "verbose")
DEFAULTS = {"terminal": "brief", "max_cols": 100, "max_lines": 12, "truncate": "warn", "detail_files": True,
            "reports": ["summary", "technical"]}
TRUNCATE = ("warn", "never")
REPORTS = ("summary", "technical")
ENV = "QA_OUTPUT"
HOME_ENV = "QA_ASSISTANT_HOME"


def validate(section) -> list[str]:
    """The problems of a project's `output:` section ([] when it is valid or absent)."""
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["output must be a map (terminal, max_cols, max_lines, truncate, detail_files, reports)"]
    problems = [f"output.{k} is not a known key (known: {', '.join(DEFAULTS)})" for k in section if k not in DEFAULTS]
    terminal = section.get("terminal")
    if terminal is not None and terminal not in MODES:
        problems.append(f"output.terminal {terminal!r}: one of {', '.join(MODES)}")
    for key, low in (("max_cols", 40), ("max_lines", 3)):
        value = section.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < low):
            problems.append(f"output.{key} {value!r}: a whole number of at least {low}")
    if section.get("truncate") is not None and section["truncate"] not in TRUNCATE:
        problems.append(f"output.truncate {section['truncate']!r}: one of {', '.join(TRUNCATE)}")
    if section.get("detail_files") is not None and not isinstance(section["detail_files"], bool):
        problems.append("output.detail_files must be true or false")
    reports = section.get("reports")
    if reports is not None and not (isinstance(reports, list) and all(r in REPORTS for r in reports)):
        problems.append(f"output.reports {reports!r}: a list of {', '.join(REPORTS)}")
    return problems


def settings(project: dict | None = None, cli: str | None = None, environ=None) -> dict:
    """The settings in force: defaults, then the project's `output:`, then `QA_OUTPUT`, then the command's flag.
    An invalid value in the project is ignored here (validate_project reports it): the framework default stays."""
    environ = os.environ if environ is None else environ
    out = dict(DEFAULTS)
    section = (project or {}).get("output") if isinstance(project, dict) else None
    if isinstance(section, dict) and not validate(section):
        out.update({k: v for k, v in section.items() if v is not None})
    env = str(environ.get(ENV, "")).strip().lower()
    if env in MODES:
        out["terminal"] = env
    if cli in MODES:
        out["terminal"] = cli
    return out


def is_brief(cfg: dict) -> bool:
    return cfg["terminal"] == "brief"


def add_flags(parser) -> None:
    """`--brief` and `--detail` on a command's parser (a project can ask for the complete text with `terminal: normal`)."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--brief", action="store_true", help="a short card (the default unless the project says otherwise)")
    group.add_argument("--detail", action="store_true", help="the complete text, as before the short cards")


def cli_choice(args) -> str | None:
    return "brief" if getattr(args, "brief", False) else "normal" if getattr(args, "detail", False) else None


def clip(text, cols: int, where: str | None = None) -> str:
    """One line of at most `cols` characters. A value that does not fit is cut with an ellipsis and, when `where` is
    given, followed by where the complete value is: a value is never cut without a way to read it."""
    text = str(text)
    if len(text) <= cols and "\n" not in text:
        return text                         # as written: indentation and spacing of a card line are kept
    line = " ".join(text.split())
    if len(line) <= cols:
        return line
    tail = f" (full: {where})" if where else ""
    keep = max(10, cols - len(tail) - 1)
    return line[:keep].rstrip() + "…" + tail


def fit(lines: list[tuple[str, bool]], max_lines: int, more: str | None = None, cols: int | None = None) -> list[str]:
    """The lines of a card: every must line (never cut, never counted, never wrapped: a command stays copyable) and the
    information lines up to `max_lines`, each clipped to `cols` with `more` as where it is complete; what is left out is
    counted and says how to read it. `lines`: (text, must) in order."""
    info = sum(1 for _t, must in lines if not must)
    shown, out = 0, []
    for text, must in lines:
        if must:
            out.append(text)
        elif shown < max(0, max_lines):
            out.append(clip(text, cols, more) if cols else text)
            shown += 1
    if info > shown:
        out.append(f"… {info - shown} more line(s)" + (f": {more}" if more else ""))
    return out


# --- notices that do not change are not repeated ---------------------------------------------------------------

_TEST_HOME: list[str] = []


def home(environ=None) -> str:
    """Where the notice state lives: `$QA_ASSISTANT_HOME`, else `~/.qa-assistant`. Inside the framework's own unit tests
    (the runner is in this process) and with no variable set, a temporary folder of the process is used instead: a test
    never writes in the user's home."""
    environ = os.environ if environ is None else environ
    if environ.get(HOME_ENV):
        return os.path.abspath(os.path.expanduser(environ[HOME_ENV]))
    if "unittest" in sys.modules:
        if not _TEST_HOME:
            _TEST_HOME.append(tempfile.mkdtemp(prefix="qa-output-home-"))
        return _TEST_HOME[0]
    return os.path.abspath(os.path.expanduser(os.path.join("~", ".qa-assistant")))


def _state_path(environ=None) -> str:
    return os.path.join(home(environ), "output-state.json")


def _read_state(environ=None) -> dict:
    try:
        with open(_state_path(environ), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict, environ=None) -> None:
    path = _state_path(environ)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".output-state-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass            # the state is a convenience: a notice shown again is better than an error


class Notices:
    """A notice (a warning that does not need an action now) is shown when it is new or its text changed since it was
    last shown for this workspace, and not again while it stays the same. In the complete modes everything is shown
    and nothing is remembered. A notice that needs an action is `must`: always shown."""

    def __init__(self, workspace: str, cfg: dict, environ=None):
        self.key = os.path.realpath(workspace)
        self.brief = is_brief(cfg)
        self.environ = environ

    def show(self, notice_id: str, text: str, must: bool = False) -> bool:
        if must or not self.brief:
            return True
        state = _read_state(self.environ)
        mine = state.setdefault(self.key, {})
        digest = hashlib.sha256(str(text).encode()).hexdigest()[:16]
        if mine.get(notice_id) == digest:
            return False
        mine[notice_id] = digest
        _write_state(state, self.environ)
        return True
