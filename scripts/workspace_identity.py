"""
The identity of a workspace and the workspace a session is open in (technique-workspaces.md section 4.1).

A workspace MAY declare who it is in its project.yaml:

    workspace:
      id: "lia"            # stable, unique among the workspaces of one person: [a-z0-9][a-z0-9._-]{0,63}
      kind: "real"         # real | simulation

The name in `project:` is only informative: two folders can carry the same project name (the real workspace and a
copy made to simulate). The identity is what tells them apart, and the real path is what tells two folders apart even
when one was copied with its identity unchanged. Both fields are optional as a block: a project.yaml without
`workspace:` (every workspace made before 15.11.2026) works as before and is said to have no identity.

Standard library only, besides PyYAML for reading project.yaml: imported by workspace_select.py (which must run on an
old checkout) and by pbi_state.py.
"""

from __future__ import annotations

import os
import re

KINDS = ("real", "simulation")
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
OPEN_HINT = "cd {path} && claude"


def problems(proj: dict) -> list[str]:
    """What is wrong with the `workspace:` block of a project.yaml; [] when it is absent or right."""
    block = proj.get("workspace") if isinstance(proj, dict) else None
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["workspace: expected {id: \"<stable id>\", kind: real|simulation} (technique-workspaces.md 4.1)"]
    out = []
    extra = sorted(set(block) - {"id", "kind"})
    if extra:
        out.append(f"workspace: unknown key(s) {extra}; only id and kind (technique-workspaces.md 4.1)")
    wid = block.get("id")
    if not isinstance(wid, str) or not ID.fullmatch(wid):
        out.append("workspace.id must be a stable id of lower-case letters, digits, '.', '_' or '-' (e.g. \"lia\")")
    if block.get("kind") not in KINDS:
        out.append(f"workspace.kind must be one of {list(KINDS)}")
    return out


def identity(proj: dict) -> dict | None:
    """{'id', 'kind'} when project.yaml declares a valid identity; None when it declares none (or a wrong one, which
    validate_project reports)."""
    if not isinstance(proj, dict) or proj.get("workspace") is None or problems(proj):
        return None
    block = proj["workspace"]
    return {"id": block["id"], "kind": block["kind"]}


def read(workspace: str) -> dict | None:
    """The identity declared by <workspace>/project.yaml, or None (no file, no block, unreadable)."""
    path = os.path.join(workspace, "project.yaml")
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            proj = yaml.safe_load(f)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return identity(proj) if isinstance(proj, dict) else None


def label(ident: dict | None) -> str:
    return f"{ident['id']} ({ident['kind']})" if ident else "no identity"


def real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside(path: str, base: str) -> bool:
    p, b = real(path), real(base)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)


def session_workspace(cwd: str, framework: str | None = None) -> str | None:
    """The workspace a session opened in `cwd` belongs to: `cwd` or its nearest parent holding a project.yaml (a file,
    not a link). None when `cwd` is outside every workspace (a CLI or CI started elsewhere), or inside the framework's
    own checkout (its docs/ keeps a template project.yaml)."""
    current = os.path.realpath(os.path.abspath(cwd))
    while True:
        if framework and _inside(current, framework):
            return None
        candidate = os.path.join(current, "project.yaml")
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def session_conflict(target: str, cwd: str, framework: str | None = None) -> dict | None:
    """None when the session may work on `target`: the session is outside every workspace, or inside `target` itself
    (any subfolder). Otherwise what the refusal shows: the session's workspace, the target, both identities and the
    command that opens a session in the target. Real paths decide, never the project's name or the identity's id: a copy
    that kept the real identity is still another folder."""
    session = session_workspace(cwd, framework)
    if session is None or real(session) == real(target):
        return None
    s_id, t_id = read(session), read(target)
    return {"session": os.path.realpath(session), "session_identity": s_id, "target": os.path.realpath(target),
            "target_identity": t_id, "open": OPEN_HINT.format(path=os.path.realpath(target))}


def conflict_text(c: dict) -> str:
    same = c["session_identity"] and c["target_identity"] and c["session_identity"]["id"] == c["target_identity"]["id"]
    note = (" Both folders declare the same identity id: one of them is a copy that kept it (give the copy its own id "
            "and kind: simulation)." if same else "")
    return (f"the session is open in another workspace.\n"
            f"  session workspace: {c['session']} ({label(c['session_identity'])})\n"
            f"  requested target:  {c['target']} ({label(c['target_identity'])})\n"
            f"  open the right session: {c['open']}\n"
            f"Nothing was read beyond the two project.yaml files and nothing was written.{note}")


def record_conflict(recorded: dict | None, current: dict | None) -> str | None:
    """Why a PBI record bound to `recorded` must not be resumed in a workspace whose identity is `current`; None when it
    may (same id and kind, or a record that was never bound)."""
    if not recorded:
        return None
    if current and recorded.get("id") == current["id"] and recorded.get("kind") == current["kind"]:
        return None
    return (f"the record belongs to the workspace {label(recorded)}, and this workspace is {label(current)}: it was "
            "copied or moved here. Resume it in its own workspace; the file was not changed")
