"""
The one launcher of the Azure DevOps write guard for any workspace (technique-ado-write-guard.md, «Launcher»).

    python scripts/ado_guard_launch.py <workspace>            # starts scripts/ado_write_guard.py (stdio MCP server)
    python scripts/ado_guard_launch.py <workspace> --check    # what it would use, without starting and without a secret

It replaces the per-project shell wrappers. Everything a project decides lives outside git, in the project folder's
`_tools/` (technique-workspaces.md §4.2), and outside the framework:

- `_tools/ado_guard.conf`: `KEY=value` lines, no secret in it: `ORG`, `PROJECT`, `READ_PAT_VAR` (the .env variable of the
  read token), optional `ALLOW_BUG_CREATE=1` (the user's standing decision to enable bug creation; absent = disabled),
  optional `KEYCHAIN_SERVICE` (default `<workspace.id>-ado-write-pat`);
- `_tools/ado_guard_identity`: the GUID of the confirmed identity (only that);
- `_tools/pat_validade.conf`: the expiry dates (`ADO_GUARD_READ_PAT_EXPIRES`, `ADO_GUARD_WRITE_PAT_EXPIRES`, YYYY-MM-DD);
- `_tools/ado_guard_created_bugs.json`: the ledger of the bugs the guard created (written by the guard).

The read token comes from the workspace's .env, the write token only from the macOS keychain (never a file); both are
passed to the guard through its environment and never printed. Exit codes: 0 listed, 2 refused (a missing file or key).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ROOT = os.path.dirname(HERE)
CONF_KEYS = {"ORG", "PROJECT", "READ_PAT_VAR", "ALLOW_BUG_CREATE", "KEYCHAIN_SERVICE"}
EXPIRY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refused(Exception):
    pass


def read_pairs(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    out[key.strip()] = value.strip().strip("\"'")
    return out


def tools_dir(workspace: str) -> str:
    ws = os.path.abspath(workspace)
    if os.path.basename(ws) != "repo":
        raise Refused("the workspace must be the repo/ folder of a project folder (technique-workspaces.md §4.2)")
    return os.path.join(os.path.dirname(ws), "_tools")


def keychain_read(service: str) -> str:
    """The write token from the macOS keychain; '' when there is none or the platform has no keychain."""
    if sys.platform != "darwin":
        return ""
    r = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def prepare(workspace: str, environ: dict, keychain=None) -> tuple[list[str], dict, dict]:
    """(argv, environment, facts). The facts say what is set, never a value of a secret."""
    import common
    ws = os.path.abspath(workspace)
    tools = tools_dir(ws)
    conf = read_pairs(os.path.join(tools, "ado_guard.conf"))
    unknown = set(conf) - CONF_KEYS
    if unknown:
        raise Refused(f"ado_guard.conf: unknown key(s) {sorted(unknown)} (known: {sorted(CONF_KEYS)})")
    for key in ("ORG", "PROJECT", "READ_PAT_VAR"):
        if not conf.get(key):
            raise Refused(f"{os.path.join(tools, 'ado_guard.conf')} has no {key}")
    identity_file = os.path.join(tools, "ado_guard_identity")
    if not os.path.isfile(identity_file):
        raise Refused(f"{identity_file} is missing (the GUID of the confirmed identity; without it the guard does not start)")
    with open(identity_file, encoding="utf-8") as f:
        identity = "".join(f.read().split())
    proj = common.read_project(ws)
    ident = (proj.get("workspace") or {}).get("id") or os.path.basename(os.path.dirname(ws))
    service = conf.get("KEYCHAIN_SERVICE") or f"{ident}-ado-write-pat"

    env = {k: v for k, v in environ.items() if not k.startswith("ADO_GUARD_")}
    read_pat = common.read_env(os.path.join(ws, ".env")).get(conf["READ_PAT_VAR"], "")
    if read_pat:
        env["ADO_GUARD_READ_PAT"] = read_pat
    expiry = read_pairs(os.path.join(tools, "pat_validade.conf"))
    for key in ("ADO_GUARD_READ_PAT_EXPIRES", "ADO_GUARD_WRITE_PAT_EXPIRES"):
        if EXPIRY.match(expiry.get(key, "")):
            env[key] = expiry[key]
    write_pat = (keychain or keychain_read)(service)
    if write_pat:
        env["ADO_GUARD_WRITE_PAT"] = write_pat
    if conf.get("ALLOW_BUG_CREATE") == "1":
        env["ADO_GUARD_ALLOW_BUG_CREATE"] = "1"
    argv = [sys.executable, os.path.join(ROOT, "scripts", "ado_write_guard.py"), "--org", conf["ORG"],
            "--project", conf["PROJECT"], "--identity-id", identity, "--allowed-dir", os.path.join(ws, "evidence"),
            "--created-bugs-file", os.path.join(tools, "ado_guard_created_bugs.json"),
            "--project-yaml", os.path.join(ws, "project.yaml")]
    facts = {"org": conf["ORG"], "project": conf["PROJECT"], "identity_file": True, "read_pat": bool(read_pat),
             "write_pat": bool(write_pat), "keychain_service": service, "bug_creation": conf.get("ALLOW_BUG_CREATE") == "1",
             "expiry_dates": sorted(k for k in env if k.endswith("_PAT_EXPIRES"))}
    return argv, env, facts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace")
    ap.add_argument("--check", action="store_true", help="list what would be used; start nothing")
    a = ap.parse_args(argv)
    try:
        cmd, env, facts = prepare(a.workspace, dict(os.environ))
    except Refused as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 2
    if a.check:
        for key, value in facts.items():
            print(f"{key}: {value}")
        return 0
    os.execve(cmd[0], cmd, env)


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
