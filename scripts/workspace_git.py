"""
The workspace as a git repository of its own, with a private remote
(methodology sections 4, step 4, and 12.1 and 12.3; plan, task 9.4). The one
helper /project-onboarding and /checkpoint call.

    python scripts/workspace_git.py <workspace> init
    python scripts/workspace_git.py <workspace> status
    python scripts/workspace_git.py <workspace> remote --create OWNER/NAME [--yes]
    python scripts/workspace_git.py <workspace> remote --set URL
    python scripts/workspace_git.py <workspace> push [--i-confirm-private]

- init: `git init` (branch main) when the folder is not a repository yet; the
  .gitignore from docs/workspace/gitignore (an existing one keeps its lines and
  gets the missing ones); .env.example with the names of the .env variables
  project.yaml refers to (`NAME=` lines, never a value; an existing one gets the
  missing names); an initial commit only when there is none yet. Idempotent.
  Refused when .env is tracked or a file over 50 MB would be committed.
- status: branch, remote, commits not pushed (against the upstream; all of
  them when there is none), untracked files, tracked files over 50 MB and, with
  `gh` installed, the remote's visibility.
- remote --create: prints the `gh repo create OWNER/NAME --private ...` it would
  run and runs it only with --yes (after the user said yes); never without
  --private. remote --set: adds origin.
- push: `git push -u origin HEAD`, never --force. Refused when the remote is
  public, when its visibility cannot be confirmed (gh missing, not GitHub, gh
  failed; --i-confirm-private overrides only this case, never a public remote),
  when .env is tracked or a tracked file is over 50 MB.
- The workspace must be the top of its own repository (one repository per
  project, never inside another one, e.g. qa-assistant's demo). git and gh get
  argument lists; git's hook variables (GIT_DIR...) are dropped, so the command
  always acts on the workspace given.

Exit codes: 0 ok · 1 refused by a safety rule · 2 usage/configuration (not a
repository, no project.yaml, no origin...) · 3 git or gh failed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import read_env, read_project, variable_names

TEMPLATE = os.path.join(ROOT, "docs", "workspace", "gitignore")
LIMIT = 50 * 1024 * 1024
MERGED_HEADER = "# added by qa-assistant (docs/workspace/gitignore)"
EXAMPLE_HEADER = ("# Names of the .env variables project.yaml refers to, without values. On a new computer: copy to .env\n"
                  "# and fill in from your own secret store (methodology 12.3). Never a value here.\n")
MUST_IGNORE = (".env", ".api-session.env", "dashboard.html")
HOOK_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")
SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+")
GITHUB_URL = re.compile(r"^(?:https?://(?:[^@/]+@)?|ssh://git@|git@)([^/:]+)[/:]([^/]+)/([^/]+?)(?:\.git)?/?$")


class Refused(Exception):
    """A safety rule (code 1)."""


class UsageError(Exception):
    """Usage or configuration (code 2)."""


class Failed(Exception):
    """git or gh failed (code 3)."""


# --- the doors to git and gh (the tests replace gh) -------------------------------------------------------------

def git(workspace: str, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("git")
    if not exe:
        raise UsageError("git is not installed")
    env = {k: v for k, v in os.environ.items() if k not in HOOK_VARIABLES}
    return subprocess.run([exe, "-C", workspace, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def git_ok(workspace: str, *args: str) -> str:
    r = git(workspace, *args)
    if r.returncode != 0:
        raise Failed(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def gh(*args: str) -> subprocess.CompletedProcess | None:
    """Runs gh; None when it is not installed."""
    exe = shutil.which("gh")
    if not exe:
        return None
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, timeout=120)


def file_size(path: str) -> int:
    return os.path.getsize(path)


# --- reading the repository -------------------------------------------------------------------------------------

def inside_repository(workspace: str) -> bool:
    return shutil.which("git") is not None and git(workspace, "rev-parse", "--is-inside-work-tree").returncode == 0


def own_repository(workspace: str) -> bool:
    """The workspace is the top of a repository (not a folder inside another one)."""
    if not inside_repository(workspace):
        return False
    r = git(workspace, "rev-parse", "--show-cdup")
    return r.returncode == 0 and r.stdout.strip() == ""


def has_commits(workspace: str) -> bool:
    return git(workspace, "rev-parse", "--verify", "-q", "HEAD").returncode == 0


def tracked(workspace: str, path: str) -> bool:
    return git(workspace, "ls-files", "--error-unmatch", "--", path).returncode == 0


def env_tracked(workspace: str) -> bool:
    return tracked(workspace, ".env")


def branch(workspace: str) -> str:
    r = git(workspace, "symbolic-ref", "-q", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "(detached HEAD)"


def remote_url(workspace: str) -> str | None:
    r = git(workspace, "remote", "get-url", "origin")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def upstream(workspace: str) -> str | None:
    r = git(workspace, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return r.stdout.strip() if r.returncode == 0 else None


def unpushed(workspace: str) -> list[str]:
    """Commits not pushed ('<sha> <subject>'): against the upstream, or all of them when there is none."""
    if not has_commits(workspace):
        return []
    rng = ["@{u}..HEAD"] if upstream(workspace) else ["HEAD"]
    return [x for x in git_ok(workspace, "log", "--format=%h %s", *rng).splitlines() if x.strip()]


def untracked(workspace: str) -> list[str]:
    return [x for x in git_ok(workspace, "ls-files", "--others", "--exclude-standard").splitlines() if x]


def large_files(workspace: str) -> list[tuple[str, int]]:
    """Files in git's index (tracked or staged) over 50 MB, with their size."""
    out = []
    for path in git_ok(workspace, "ls-files", "-z").split("\0"):
        full = os.path.join(workspace, path)
        if path and os.path.isfile(full) and file_size(full) > LIMIT:
            out.append((path, file_size(full)))
    return out


def repo_slug(url: str | None) -> str | None:
    """OWNER/NAME for gh (HOST/OWNER/NAME outside github.com); None for a path or an unknown form."""
    m = GITHUB_URL.match(url or "")
    if not m:
        return None
    host, owner, name = m.groups()
    return f"{owner}/{name}" if host.lower() == "github.com" else f"{host}/{owner}/{name}"


def visibility(url: str | None) -> tuple[str | None, str]:
    """(PRIVATE | PUBLIC | INTERNAL | None, why it is None)."""
    slug = repo_slug(url)
    if slug is None:
        return None, "not a GitHub remote"
    r = gh("repo", "view", slug, "--json", "visibility")
    if r is None:
        return None, "gh is not installed"
    if r.returncode != 0:
        return None, f"gh repo view failed: {(r.stderr or r.stdout).strip()[:200]}"
    try:
        return str(json.loads(r.stdout)["visibility"]).upper(), ""
    except (ValueError, KeyError, TypeError):
        return None, "gh gave no visibility"


def missing_example_variables(workspace: str, proj: dict) -> list[str]:
    """Variables of project.yaml missing from .env.example (all of them when it does not exist)."""
    names = read_env(os.path.join(workspace, ".env.example"))
    return [v for v in variable_names(proj) if v not in names]


def size_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


# --- init -------------------------------------------------------------------------------------------------------

def template_lines() -> list[str]:
    with open(TEMPLATE, encoding="utf-8") as f:
        return f.read().splitlines()


def write_gitignore(workspace: str) -> str:
    path = os.path.join(workspace, ".gitignore")
    if not os.path.isfile(path):
        shutil.copyfile(TEMPLATE, path)
        return "created from docs/workspace/gitignore"
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    have = {x.strip() for x in text.splitlines()}
    missing = [x for x in template_lines() if x.strip() and not x.startswith("#") and x.strip() not in have]
    if not missing:
        return "unchanged"
    with open(path, "a", encoding="utf-8") as f:
        f.write(("" if text.endswith("\n") or not text else "\n") + MERGED_HEADER + "\n" + "\n".join(missing) + "\n")
    return f"{len(missing)} line(s) added: {', '.join(missing)}"


def check_ignores(workspace: str) -> None:
    """The secrets are ignored and .env.example is not (an older line of the workspace can undo the template)."""
    def ignored(p: str) -> bool:
        return git(workspace, "check-ignore", "-q", "--no-index", p).returncode == 0

    if ignored(".env.example"):
        with open(os.path.join(workspace, ".gitignore"), "a", encoding="utf-8") as f:
            f.write("!.env.example\n")
    not_ignored = [p for p in MUST_IGNORE if not ignored(p)]
    if not_ignored:
        raise Refused(f"{', '.join(not_ignored)} not ignored: a line of .gitignore (e.g. '!.env') undoes the "
                      "template; fix it and run init again")


def write_env_example(workspace: str, proj: dict) -> str:
    path = os.path.join(workspace, ".env.example")
    missing = missing_example_variables(workspace, proj)
    if os.path.isfile(path):
        if not missing:
            return "unchanged"
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        with open(path, "a", encoding="utf-8") as f:
            f.write(("" if text.endswith("\n") or not text else "\n") + "".join(f"{v}=\n" for v in missing))
        return f"{len(missing)} name(s) added: {', '.join(missing)}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(EXAMPLE_HEADER + "".join(f"{v}=\n" for v in missing))
    return f"created with {len(missing)} name(s)"


def refuse_unsafe_tree(workspace: str) -> None:
    if env_tracked(workspace):
        raise Refused(".env is tracked by git: take it out with `git rm --cached .env` (and change the credentials "
                      "if it was ever pushed), then run again")
    big = large_files(workspace)
    if big:
        raise Refused("files over 50 MB: " + ", ".join(f"{p} ({size_mb(n)})" for p, n in big)
                      + " -- keep them out of git (.gitignore) and record where they come from")


def cmd_init(workspace: str, a) -> int:
    proj = read_project(workspace)
    if inside_repository(workspace) and not own_repository(workspace):
        top = git_ok(workspace, "rev-parse", "--show-toplevel").strip()
        raise UsageError(f"the workspace is inside another git repository ({top}); a workspace is a repository of "
                         "its own, in qa-workspaces/<company>/<project>/")
    if not inside_repository(workspace):
        git_ok(workspace, "init", "--initial-branch=main")
        print("git: repository created (branch main)")
    else:
        print("git: already a repository")
    refuse_unsafe_tree(workspace)
    print(f".gitignore: {write_gitignore(workspace)}")
    check_ignores(workspace)
    print(f".env.example: {write_env_example(workspace, proj)}")
    if has_commits(workspace):
        print("commit: the repository already has commits (the checkpoints commit from now on)")
        return 0
    git_ok(workspace, "add", "-A")
    try:
        refuse_unsafe_tree(workspace)
    except Refused:
        git(workspace, "reset", "-q")
        raise
    git_ok(workspace, "commit", "-q", "-m", "workspace: initial commit")
    print(f"commit: workspace: initial commit ({len(git_ok(workspace, 'ls-files').splitlines())} file(s))")
    return 0


# --- status -----------------------------------------------------------------------------------------------------

def require_repository(workspace: str) -> None:
    if not own_repository(workspace):
        raise UsageError(f"{workspace} is not the top of a git repository: run `workspace_git.py <workspace> init`")


def cmd_status(workspace: str, a) -> int:
    require_repository(workspace)
    url = remote_url(workspace)
    print(f"branch: {branch(workspace)}")
    print(f"remote: {url or 'none (propose one: remote --create OWNER/NAME)'}")
    up = upstream(workspace)
    pending = unpushed(workspace)
    print(f"not pushed: {len(pending)} commit(s)" + (f" (against {up})" if up else " (no upstream)"))
    for c in pending[:10]:
        print(f"  {c}")
    if len(pending) > 10:
        print(f"  ... and {len(pending) - 10} more")
    new = untracked(workspace)
    print(f"untracked: {len(new)} file(s)")
    for p in new[:10]:
        print(f"  {p}")
    big = large_files(workspace)
    print(f"over 50 MB: {', '.join(f'{p} ({size_mb(n)})' for p, n in big) or 'none'}")
    if env_tracked(workspace):
        print("WARNING: .env is tracked by git")
    if url:
        vis, why = visibility(url)
        print(f"visibility: {vis or 'unknown (' + why + ')'}")
    return 0


# --- remote -----------------------------------------------------------------------------------------------------

def create_command(workspace: str, slug: str) -> list[str]:
    return ["gh", "repo", "create", slug, "--private", "--source", workspace, "--remote", "origin"]


def run_create(cmd: list[str]) -> subprocess.CompletedProcess:
    """The only way a repository is created: private, always."""
    if "--private" not in cmd or {"--public", "--internal"} & set(cmd):
        raise Refused("a workspace's remote is created only with --private (it holds the project's data)")
    r = gh(*cmd[1:])
    if r is None:
        raise UsageError("gh is not installed: install it (https://cli.github.com) or create the private repository "
                         "by hand and use remote --set URL")
    if r.returncode != 0:
        raise Failed(f"gh repo create: {(r.stderr or r.stdout).strip()}")
    return r


def cmd_remote(workspace: str, a) -> int:
    require_repository(workspace)
    current = remote_url(workspace)
    if a.set:
        if current == a.set:
            print(f"origin: already {current}")
            return 0
        if current:
            raise UsageError(f"origin already exists ({current}); change it by hand if that is intended")
        git_ok(workspace, "remote", "add", "origin", a.set)
        print(f"origin: {a.set} (check that it is private: workspace_git.py <workspace> status)")
        return 0
    if not SLUG.fullmatch(a.create):
        raise UsageError(f"{a.create!r}: expected OWNER/NAME, e.g. my-user/demo-shop")
    if current:
        raise UsageError(f"origin already exists ({current})")
    cmd = create_command(workspace, a.create)
    print("command: " + " ".join(cmd))
    if not a.yes:
        print("not run: ask the user; with their yes, run again with --yes")
        return 0
    run_create(cmd)
    print(f"origin: {remote_url(workspace) or '(not set?)'} (private); nothing pushed yet")
    return 0


# --- push -------------------------------------------------------------------------------------------------------

PUSH = ("push", "-u", "origin", "HEAD")  # never --force


def cmd_push(workspace: str, a) -> int:
    require_repository(workspace)
    url = remote_url(workspace)
    if not url:
        raise UsageError("no origin: create the private remote first (remote --create OWNER/NAME)")
    if not has_commits(workspace):
        raise UsageError("nothing committed yet: run init")
    refuse_unsafe_tree(workspace)
    vis, why = visibility(url)
    if vis == "PUBLIC":
        raise Refused(f"the remote {url} is PUBLIC: a workspace holds the project's data; make it private first")
    if vis != "PRIVATE" and not a.i_confirm_private:
        raise Refused(f"the visibility of {url} could not be confirmed ({vis or why}); check it and, if it is "
                      "private, push with --i-confirm-private")
    pending = unpushed(workspace)
    r = git(workspace, *PUSH)
    if r.returncode != 0:
        raise Failed(f"git push: {(r.stderr or r.stdout).strip()}")
    print(f"pushed {len(pending)} commit(s) of {branch(workspace)} to {url}"
          + ("" if vis == "PRIVATE" else " (private by the user's confirmation)"))
    for c in pending:
        print(f"  {c}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The workspace's git repository and its private remote.")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="git init, .gitignore, .env.example, initial commit")
    sub.add_parser("status", help="branch, remote, commits not pushed, untracked, large files, visibility")
    r = sub.add_parser("remote", help="create (private) or set the origin")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--create", metavar="OWNER/NAME")
    g.add_argument("--set", metavar="URL")
    r.add_argument("--yes", action="store_true", help="run gh repo create (only after the user said yes)")
    p = sub.add_parser("push", help="git push -u origin HEAD, after the safety checks")
    p.add_argument("--i-confirm-private", action="store_true",
                   help="the user checked that the remote is private (when gh cannot confirm it)")
    a = ap.parse_args(argv)
    workspace = os.path.abspath(a.workspace)
    commands = {"init": cmd_init, "status": cmd_status, "remote": cmd_remote, "push": cmd_push}
    try:
        if not os.path.isdir(workspace):
            raise UsageError(f"{workspace} is not a folder")
        return commands[a.command](workspace, a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except (UsageError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (Failed, subprocess.TimeoutExpired) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
