"""
The door to the Playwright CLI (`@playwright/cli`) for a workspace (technique-browser.md §16).

    python scripts/pw_cli.py check <workspace>
    python scripts/pw_cli.py run <workspace> [--batch NAME] [--session NAME] [--allow-eval] -- <playwright-cli args>

`check` reports whether the CLI is installed in `<workspace>/e2e/node_modules` at the version `web.cli_version` of
project.yaml pins (exit 0 ready, 1 not ready). It never installs: an install is a download, the user approves it.

`run` runs one CLI command from `<workspace>/e2e` with the CLI that is installed there (never `npx`, so a command can
never fetch `@latest`), and:

- puts it in its own session (`-s=qa-<workspace folder>`; `--session` changes the name), with the browser profile in
  memory: `--persistent`, `show --host`, `install` and `kill-all` are refused;
- refuses `run-code` and `eval` (arbitrary code in the page) unless `--allow-eval` says the user approved it;
- replaces `{{NAME}}` in the arguments with the value of NAME from the workspace's `.env`, so the assistant types a
  credential's name and never reads its value; a NAME that is not in `.env` stops the command;
- redacts every `.env` secret in the output (common.Redactor) and in the files the command writes;
- gives `snapshot`, `screenshot` and `pdf` a name in `<workspace>/evidence/<batch>/cli/` (`NNN-<command>.<ext>`), and
  prints the path to hand to `run_log.py add --evidence`.

Exit codes: 0 the command ran (the CLI's own code is shown), 1 not ready or the CLI failed, 2 refused or wrong usage.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common
from pw_cli_rules import BIN, PACKAGE, command_of, refusal

FILE_COMMANDS = {"snapshot": "md", "screenshot": "png", "pdf": "pdf"}
PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
SESSION_FLAG = re.compile(r"^(-s(=.*)?|--session(=.*)?)$")


def web_settings(proj: dict) -> dict:
    web = proj.get("web")
    return web if isinstance(web, dict) else {}


def e2e_dir(workspace: str) -> str:
    return os.path.join(workspace, "e2e")


def binary(workspace: str) -> str | None:
    name = BIN + (".cmd" if os.name == "nt" else "")
    path = os.path.join(e2e_dir(workspace), "node_modules", ".bin", name)
    return path if os.path.exists(path) else None


def installed_version(workspace: str) -> str | None:
    path = os.path.join(e2e_dir(workspace), "node_modules", *PACKAGE.split("/"), "package.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("version") or "") or None
    except (OSError, ValueError):
        return None


def readiness(workspace: str, proj: dict) -> tuple[bool, str]:
    """Whether `run` can start, and what to do when it cannot (nothing is installed here)."""
    pinned = web_settings(proj).get("cli_version")
    if not pinned:
        return False, "web.cli_version is missing in project.yaml: pin an exact version (technique-browser.md §16.1)"
    found = installed_version(workspace)
    if not found or not binary(workspace):
        return False, (f"{PACKAGE}@{pinned} is not installed in {e2e_dir(workspace)}: ask the user, then "
                       f"`cd e2e && npm install -D {PACKAGE}@{pinned}` and `npx playwright install chromium`")
    if found != str(pinned):
        return False, f"{PACKAGE} {found} is installed but project.yaml pins {pinned}: install the pinned version"
    return True, f"{PACKAGE} {found}"


def fill_placeholders(args: list[str], env: dict[str, str]) -> tuple[list[str], list[str]]:
    """`{{NAME}}` -> the value of NAME in .env. Returns the arguments and the names that are not in .env."""
    missing: list[str] = []

    def one(m: re.Match) -> str:
        name = m.group(1)
        if name not in env:
            missing.append(name)
            return m.group(0)
        return env[name]

    return [PLACEHOLDER.sub(one, a) for a in args], sorted(set(missing))


def with_session(args: list[str], session: str) -> list[str]:
    if any(SESSION_FLAG.match(a) for a in args):
        return list(args)
    return [f"-s={session}", *args]


def next_name(folder: str, command: str, ext: str) -> str:
    n = 1
    if os.path.isdir(folder):
        nums = [int(m.group(1)) for f in os.listdir(folder) if (m := re.match(r"(\d{3})-", f))]
        n = max(nums, default=0) + 1
    return os.path.join(folder, f"{n:03d}-{command}.{ext}")


def with_evidence_name(args: list[str], folder: str) -> tuple[list[str], str | None]:
    """Adds --filename for a command that writes a file, unless the caller named one."""
    command = command_of(args)
    ext = FILE_COMMANDS.get(command)
    if not ext or any(a == "--filename" or a.startswith("--filename=") for a in args):
        return list(args), None
    if command == "screenshot":
        for a in args:
            if a.startswith("--type="):
                ext = {"jpeg": "jpg"}.get(a.split("=", 1)[1], a.split("=", 1)[1])
    path = next_name(folder, command, ext)
    return [*args, f"--filename={path}"], path


def redact_file(path: str, redactor: common.Redactor) -> None:
    if not path.endswith((".md", ".txt", ".json", ".yml", ".yaml")) or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    clean = redactor.text(text)
    if clean != text:
        common.atomic_write(path, clean)


def run(workspace: str, args: list[str], batch: str | None, session: str | None, allow_eval: bool,
        runner=subprocess.run) -> int:
    proj = common.read_project(workspace)
    ok, why = readiness(workspace, proj)
    if not ok:
        print(f"NOT READY: {why}", file=sys.stderr)
        return 1
    problem = refusal(args, allow_eval)
    if problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 2
    env = common.read_workspace_env(workspace)
    args, missing = fill_placeholders(args, env)
    if missing:
        print(f"REFUSED: {', '.join('{{' + n + '}}' for n in missing)} not in the workspace's .env "
              "(the name only, never the value)", file=sys.stderr)
        return 2
    name = session or f"qa-{os.path.basename(os.path.normpath(workspace))}"
    batch = batch or datetime.date.today().strftime("cli_%Y-%m-%d")
    folder = common.workspace_path(workspace, "evidence", batch, "cli")
    args, saved = with_evidence_name(args, folder)
    if saved:
        os.makedirs(folder, exist_ok=True)
    cmd = [binary(workspace), *with_session(args, name)]
    done = runner(cmd, cwd=e2e_dir(workspace), capture_output=True, text=True, encoding="utf-8", errors="replace")
    redactor = common.Redactor(env)
    for stream, out in ((done.stdout, sys.stdout), (done.stderr, sys.stderr)):
        if stream:
            out.write(redactor.text(stream))
    if saved and os.path.isfile(saved):
        redact_file(saved, redactor)
        print(f"Evidence: {os.path.relpath(saved, workspace)}")
    return 0 if done.returncode == 0 else 1


def check(workspace: str) -> int:
    proj = common.read_project(workspace)
    ok, why = readiness(workspace, proj)
    print(("READY: " if ok else "NOT READY: ") + why)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rest: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, rest = argv[:cut], argv[cut + 1:]
    ap = argparse.ArgumentParser(description="Playwright CLI door of a workspace")
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check", help="is the pinned CLI installed in <workspace>/e2e")
    c.add_argument("workspace")
    r = sub.add_parser("run", help="run one CLI command: pw_cli.py run <workspace> [options] -- <command ...>")
    r.add_argument("workspace")
    r.add_argument("--batch", help="evidence/<batch>/cli/ (default cli_<today>)")
    r.add_argument("--session", help="CLI session name (default qa-<workspace folder>)")
    r.add_argument("--allow-eval", action="store_true", help="the user approved run-code / eval")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.action == "check":
        return check(workspace)
    if not rest:
        print("usage: pw_cli.py run <workspace> -- <playwright-cli command ...>", file=sys.stderr)
        return 2
    return run(workspace, rest, args.batch, args.session, args.allow_eval)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
