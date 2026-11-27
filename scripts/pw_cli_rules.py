"""The rules of the Playwright CLI door that need no third-party module, so that a hook can import them
(scripts/pw_cli.py runs a command, scripts/guard_destructive.py refuses a raw one)."""
from __future__ import annotations

PACKAGE = "@playwright/cli"
BIN = "playwright-cli"
ALWAYS_REFUSED = {
    "install": "it downloads software: the user installs it (technique-browser.md §16.1), and its --skills option "
               "would add a second Playwright skill to ~/.claude/skills",
    "kill-all": "it stops the CLI sessions of every workspace; `close` ends this one",
}
CODE_COMMANDS = {"run-code", "eval"}
REFUSED_FLAGS = {
    "--persistent": "it keeps cookies and sessions of the tested site on disk; the profile stays in memory",
    "--host": "it opens the dashboard to other machines",
}


def refusal(args: list[str], allow_eval: bool = False) -> str | None:
    """Why this command must not run, or None. Also used by guard_destructive for a raw `playwright-cli` call."""
    command = command_of(args)
    if command in ALWAYS_REFUSED:
        return f"`{BIN} {command}` is refused: {ALWAYS_REFUSED[command]}"
    if command in CODE_COMMANDS and not allow_eval:
        return (f"`{BIN} {command}` runs code of your own in the page: ask the user and run it again with "
                "--allow-eval (use the CLI's commands or a spec instead when one does the job)")
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in REFUSED_FLAGS:
            return f"`{flag}` is refused: {REFUSED_FLAGS[flag]}"
    return None


def command_of(args: list[str]) -> str:
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-s", "--session"):
            skip = True
        elif not a.startswith("-"):
            return a
    return ""
