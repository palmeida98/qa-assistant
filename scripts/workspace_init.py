"""
A new workspace in the project folder layout (technique-workspaces.md section 4.2).

    python scripts/workspace_init.py <project> [--root PATH] [--yes]

Creates `<root>/<project>/repo/` (the workspace: project.yaml, context.md, .claude/settings.json and the folders the
skills write to, including `e2e/` and `flows/`) and, beside it, `_tools/` and `_arquivo/` (with an INDICE.md): what
must stay outside git. Without --yes it only lists what it would create. Never overwrites: a project folder that
exists is refused. It does not run git: `python scripts/workspace_git.py <root>/<project>/repo init` is the next step,
then /project-context. Exit codes: 0 done or listed, 2 refused.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.path.dirname(HERE)
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REPO_DIRS = ("memory", "analysis", "batches", "runs", "evidence", "history", "archive", "e2e/specs", "flows")
OUTSIDE_DIRS = ("_tools", "_arquivo")
INDEX = ("# _arquivo · index\n\nWhat was archived here, why, and when it can go. Nothing is archived without a line below.\n\n"
         "| Path | What it is | Delete after |\n|---|---|---|\n")


def plan(root: str, project: str) -> tuple[str, list[tuple[str, str]]]:
    """The project folder and the (kind, relative path) steps; raises ValueError when refused."""
    if not NAME.match(project):
        raise ValueError("the project name is lower-case letters, digits, '.', '_' or '-', starting with a letter or digit")
    base = os.path.join(root, project)
    if os.path.lexists(base):
        raise ValueError(f"{base} already exists: nothing was changed")
    steps = [("dir", "repo")] + [("dir", os.path.join("repo", d)) for d in REPO_DIRS]
    steps += [("dir", d) for d in OUTSIDE_DIRS]
    steps += [("file", os.path.join("repo", "project.yaml")), ("file", os.path.join("repo", "context.md")),
              ("file", os.path.join("repo", ".claude", "settings.json")), ("file", os.path.join("_arquivo", "INDICE.md"))]
    return base, steps


def create(base: str, project: str, steps: list[tuple[str, str]]) -> None:
    for kind, rel in steps:
        path = os.path.join(base, rel)
        if kind == "dir":
            os.makedirs(path, exist_ok=True)
            if rel.startswith("repo") and rel != "repo":
                with open(os.path.join(path, ".gitkeep"), "w"):
                    pass
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if rel.endswith("project.yaml"):
            with open(os.path.join(ROOT, "docs", "project.yaml"), encoding="utf-8") as f:
                text = f.read()
            text = text.replace('project: ""', f'project: "{project}"', 1)
            text += f'\nworkspace:\n  id: "{project}"\n  kind: "real"\n'
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        elif rel.endswith("context.md"):
            shutil.copyfile(os.path.join(ROOT, "docs", "context-template.md"), path)
        elif rel.endswith("settings.json"):
            shutil.copyfile(os.path.join(ROOT, "docs", "workspace", "settings.json"), path)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(INDEX)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project")
    ap.add_argument("--root")
    ap.add_argument("--yes", action="store_true", help="create (without it the steps are only listed)")
    a = ap.parse_args(argv)
    import workspace_select
    root, source = workspace_select.workspaces_root(a.root, os.environ, None, ROOT)
    if not os.path.isdir(root):
        print(f"STOP: the workspaces root {root} (from {source}) does not exist; it is never created here.")
        return 2
    try:
        base, steps = plan(root, a.project)
    except ValueError as e:
        print(f"STOP: {e}")
        return 2
    print(f"{'Created' if a.yes else 'Would create'} (root {root}, from {source}):")
    for kind, rel in steps:
        print(f"  {kind:4} {os.path.join(a.project, rel)}")
    if a.yes:
        create(base, a.project, steps)
        print(f"next: python scripts/workspace_git.py {os.path.join(base, 'repo')} init, then /project-context there")
    else:
        print("dry run: add --yes to create")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
