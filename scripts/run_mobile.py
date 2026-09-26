"""
Runs a workspace's Maestro flows and links each result to its test case
(technique-mobile.md; plan, task 3.3).

    python scripts/run_mobile.py <workspace> [flow ...] [--output runs/mobile_<date>.json]

- The configuration comes from the mobile: section of project.yaml (platform, flows
  folder, variables). With no flows given, runs every flow of the folder whose name
  starts with a case ID.
- Each flow given is a path (from the current folder, the workspace or the flows
  folder) or the case ID (TC-MOB-003 -> the only TC-MOB-003_*.yaml of the folder).
  A flow that cannot be found is a usage error (code 2, nothing run), never a
  Blocked case.
- The case is the prefix of the flow's file name (TC-MOB-003_cart.yaml ->
  TC-MOB-003).
- Credentials: each variable listed in mobile.variables goes from .env to an
  environment variable MAESTRO_<VAR>, which the flow reads as ${MAESTRO_<VAR>};
  never through the command line. The report's messages are redacted.

Exit codes:
  0  flows run (Fail results are results, not errors)
  2  invalid configuration, flow not found or Maestro not installed (nothing run)
  3  no target (no device/emulator) or Maestro produced no report:
     the cases are Blocked by environment
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import Redactor, read_project, read_workspace_env

PLATFORMS = ("android", "ios", "web")
CASE = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)")


class UsageError(Exception):
    """Invalid configuration or missing tool (code 2)."""


def case_of_flow(file: str) -> str | None:
    m = CASE.match(os.path.basename(file))
    return m.group(1) if m else None


def resolve_flows(names: list[str], workspace: str, folder: str) -> list[str]:
    """Absolute paths of the flows given (see the header). An error with
    suggestions if one does not exist: before, a wrong name reached Maestro
    and the case was Blocked "the flow did not run", as if it were the environment."""
    flows, missing = [], []
    for name in names:
        path = next((c for c in (name, os.path.join(workspace, name), os.path.join(folder, name))
                     if os.path.isfile(c)), None)
        if path is None and CASE.fullmatch(name):
            of_case = sorted(glob.glob(os.path.join(folder, glob.escape(name) + "_*.yaml")))
            path = of_case[0] if len(of_case) == 1 else None
        if path:
            flows.append(os.path.abspath(path))
            continue
        # suggestions: the flows of the same case (catches typos in the rest of the name and IDs with several flows)
        base = os.path.splitext(os.path.basename(name))[0]
        case = case_of_flow(base) or base
        similar = sorted(os.path.basename(f) for f in glob.glob(os.path.join(folder, glob.escape(case) + "*.yaml")))
        missing.append(name + (f" (did you mean {similar}?)" if similar else ""))
    if missing:
        raise UsageError(f"flow(s) not found in the current folder, the workspace nor {folder}: {'; '.join(missing)}")
    return flows


def configuration(workspace: str) -> dict:
    try:
        m = read_project(workspace).get("mobile") or {}
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    plat = m.get("platform")
    if plat not in PLATFORMS:
        raise UsageError(f"mobile.platform must be one of {PLATFORMS} (it is: {plat!r})")
    folder = os.path.join(workspace, m.get("flows") or "flows")
    if not os.path.isdir(folder):
        raise UsageError(f"flows folder missing: {folder}")
    return {"platform": plat, "folder": folder, "variables": list(m.get("variables") or []),
            "timeout": int(m.get("timeout_s") or 900)}


def maestro_environment(workspace: str, variables: list[str]) -> tuple[dict, dict]:
    """(env for the process, secrets by value to redact)."""
    env_ws = read_workspace_env(workspace)
    extra, secrets = {}, {}
    for var in variables:
        value = env_ws.get(var) or os.environ.get(var)
        if not value:
            raise UsageError(f"variable {var} (mobile.variables) has no value in the workspace's .env")
        extra[f"MAESTRO_{var}"] = value
        secrets[var] = value
    return extra, secrets


def target_available(platform: str) -> tuple[bool, str]:
    if platform == "web":
        return True, "Maestro Web (beta) in Chrome"
    if platform == "android":
        adb = shutil.which("adb")
        if not adb:
            return False, "adb not found (no Android SDK)"
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, check=False).stdout
        devices = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        return (True, f"android: {', '.join(devices)}") if devices else (False, "no Android device/emulator connected")
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return False, "xcrun not found (iOS only on macOS with Xcode)"
    out = subprocess.run([xcrun, "simctl", "list", "devices", "booted"], capture_output=True, text=True,
                         check=False).stdout
    return ("Booted" in out, "iOS simulator booted" if "Booted" in out else "no iOS simulator booted")


def read_junit(xml_txt: str, red: Redactor) -> list[dict]:
    res = []
    for tc in ET.fromstring(xml_txt).iter("testcase"):
        failure = tc.find("failure")
        if failure is None:
            failure = tc.find("error")
        ok = tc.get("status", "SUCCESS") == "SUCCESS" and failure is None
        file = tc.get("file") or tc.get("name") or ""
        res.append({"case": case_of_flow(file), "flow": file, "suggested_status": "Pass" if ok else "Fail",
                    "message": red.text((failure.text or failure.get("message") or "").strip())
                    if failure is not None else "",
                    "time_s": float(tc.get("time") or 0)})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Runs Maestro flows and links the results to the cases "
                                             "(technique-mobile.md).")
    ap.add_argument("workspace")
    ap.add_argument("flows", nargs="*", help="flows to run: path or case ID (default: every flow of the folder "
                                             "whose name starts with a case ID)")
    ap.add_argument("--output", help="JSON report (relative to the workspace, unless absolute)")
    a = ap.parse_args(argv)
    try:
        cfg = configuration(a.workspace)
        flows = (resolve_flows(a.flows, a.workspace, cfg["folder"]) if a.flows
                 else sorted(f for f in glob.glob(os.path.join(cfg["folder"], "*.yaml")) if case_of_flow(f)))
        if not flows:
            raise UsageError(f"no flow named after a case ID (e.g. TC-MOB-001_login.yaml) in {cfg['folder']}")
        no_case = [os.path.basename(f) for f in flows if not case_of_flow(f)]
        if no_case:
            raise UsageError(f"flows without a case ID in the name: {no_case}")
        maestro = shutil.which("maestro")
        if not maestro:
            raise UsageError("maestro not found -- install the Maestro CLI")
        extra, secrets = maestro_environment(a.workspace, cfg["variables"])
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    red = Redactor(secrets)
    ok, target = target_available(cfg["platform"])
    rep = {"platform": cfg["platform"], "target": target, "ts": datetime.now().isoformat(timespec="seconds")}
    if not ok:
        rep["results"] = [{"case": case_of_flow(f), "flow": os.path.basename(f), "suggested_status": "Blocked",
                           "message": f"no target: {target}", "time_s": 0} for f in flows]
        code = 3
    else:
        with tempfile.TemporaryDirectory() as tmp:
            junit = os.path.join(tmp, "junit.xml")
            try:
                r = subprocess.run([maestro, "test", *flows, "--format", "junit", "--output", junit],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, **extra}, timeout=cfg["timeout"], check=False)
                output = r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                output = f"timeout of {cfg['timeout']} s"
            if os.path.isfile(junit):
                with open(junit, encoding="utf-8") as f:
                    rep["results"] = read_junit(f.read(), red)
                code = 0
            else:
                rep["results"] = []
                rep["error"] = red.text(output.strip()[-500:])
                code = 3
        seen = {os.path.basename(x["flow"]) for x in rep["results"]}
        for f in flows:
            if os.path.basename(f) not in seen:
                rep["results"].append({"case": case_of_flow(f), "flow": os.path.basename(f),
                                       "suggested_status": "Blocked", "message": "the flow did not run",
                                       "time_s": 0})
    print(f"target: {rep['target']}")
    for x in rep["results"]:
        print(f"{x['case']}: {x['suggested_status']}" + (f" -- {x['message']}" if x["message"] else "")
              + f" ({x['time_s']:.0f} s)")
    if rep.get("error"):
        print(f"Maestro ERROR: {rep['error']}", file=sys.stderr)
    if a.output:
        target_path = a.output if os.path.isabs(a.output) else os.path.join(a.workspace, a.output)
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"report: {target_path}")
    return code


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
