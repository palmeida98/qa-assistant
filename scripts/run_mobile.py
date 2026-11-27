"""
Runs a workspace's Maestro flows and links each result to its test case
(technique-mobile.md; plan, task 3.3).

    python scripts/run_mobile.py <workspace> [flow ...] [--output runs/mobile_<date>.json]
    python scripts/run_mobile.py <workspace> --helper flows/_<name>.yaml [--serial S]

- --helper (plan 11.2): runs ONE flow that is not a case (its file name starts
  with '_', e.g. flows/_request_link.yaml, which asks the app for a login link)
  with the same MAESTRO_<VAR> values from .env, on --serial / $QA_MOBILE_SERIAL /
  mobile.serial when given (common.mobile_target, plan 11.8); its output is
  redacted and no result is recorded (it is not a test).
  Exit 0 when the flow passed, 3 when it failed or there is no target.

- The configuration comes from the mobile: section of project.yaml (platform, flows
  folder, variables). With no flows given, runs every flow of the folder whose name
  starts with a case ID.
- Each flow given is a path (from the current folder, the workspace or the flows
  folder) or the case ID (TC-MOB-003 -> the only TC-MOB-003_*.yaml of the folder).
  A flow that cannot be found is a usage error (code 2, nothing run), never a
  Blocked case.
- The case is the prefix of the flow's file name (TC-MOB-003_cart.yaml ->
  TC-MOB-003).
- The device: --serial, else $QA_MOBILE_SERIAL, else mobile.serial (Android; on iOS only --serial), passed to
  Maestro as --device; with no serial and several devices connected nothing runs (code 2): Maestro would ask
  which one and wait. A serial that is not connected is no target (code 3).
- Maestro needs Java: with none answering (JAVA_HOME or `java -version`), nothing runs (code 2), never a Blocked
  case (common.java_ready).
- Credentials: each variable listed in mobile.variables goes from .env to an
  environment variable MAESTRO_<VAR>, which the flow reads as ${MAESTRO_<VAR>};
  never through the command line. The report's messages are redacted.

Exit codes:
  0  flows run (Fail results are results, not errors)
  2  invalid configuration, flow not found, Maestro or Java not installed, several devices and no --serial
     (nothing run)
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

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import (
    OutsideWorkspace,
    Redactor,
    find_adb,
    java_ready,
    mobile_target,
    read_project,
    read_workspace_env,
    workspace_path,
)

PLATFORMS = ("android", "ios", "web")
TAGS = re.compile(r"[A-Za-z0-9_.-]+(,[A-Za-z0-9_.-]+)*")
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


def target_available(platform: str, serial: str | None = None) -> tuple[bool, str]:
    """(target ready, what it is). Android: the device `serial` when given, else the only one connected; several
    connected and no serial is a UsageError (Maestro would ask which one and wait until the timeout)."""
    if platform == "web":
        return True, "Maestro Web (beta) in Chrome"
    if platform == "android":
        adb = find_adb()
        if not adb:
            return False, "adb not found (no Android SDK)"
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, check=False).stdout
        devices = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        if not devices:
            return False, "no Android device/emulator connected"
        if serial:
            return (True, f"android: {serial}") if serial in devices else (
                False, f"device {serial} not connected (connected: {', '.join(devices)})")
        if len(devices) > 1:
            raise UsageError(f"several devices connected ({', '.join(devices)}): choose one with --serial or "
                             "QA_MOBILE_SERIAL")
        return True, f"android: {devices[0]}"
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return False, "xcrun not found (iOS only on macOS with Xcode)"
    out = subprocess.run([xcrun, "simctl", "list", "devices", "booted"], capture_output=True, text=True,
                         check=False).stdout
    return ("Booted" in out, "iOS simulator booted" if "Booted" in out else "no iOS simulator booted")


def device_of(platform: str, mobile: dict, serial: str | None) -> str | None:
    """The device Maestro is told to use: Android by common.mobile_target (--serial > QA_MOBILE_SERIAL >
    mobile.serial); iOS only an explicit --serial (a simulator's UDID); the web none."""
    if platform == "android":
        return mobile_target(mobile, serial, "adb")[0]
    return serial if platform == "ios" else None


def needs_java() -> None:
    ok, detail = java_ready()
    if not ok:
        raise UsageError(detail)


# what Maestro says when the device or its driver failed, not the application: a Blocked case, never a Fail
# (rules.md: a tool failure is never a defect). Seen on a real iOS simulator and Maestro Web, 27.11.2026.
TOOL_FAILURE = re.compile(r"DeviceUnreachable|Connection refused|driver not ready|session not created|"
                          r"Could not start a new session|Unknown error", re.IGNORECASE)


def flow_tags(path: str) -> list[str]:
    """The `tags:` of a flow's header (the part before `---`); [] when it has none or cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read().split("\n---", 1)[0]
        tags = (yaml.safe_load(head) or {}).get("tags")
    except (OSError, yaml.YAMLError, AttributeError):
        return []
    return [str(t) for t in tags] if isinstance(tags, list) else []


def selected_by_tags(path: str, include: str | None, exclude: str | None) -> bool:
    tags = set(flow_tags(path))
    if include and not tags & set(include.split(",")):
        return False
    return not (exclude and tags & set(exclude.split(",")))


def read_junit(xml_txt: str, red: Redactor) -> list[dict]:
    res = []
    for tc in ET.fromstring(xml_txt).iter("testcase"):
        failure = tc.find("failure")
        if failure is None:
            failure = tc.find("error")
        ok = tc.get("status", "SUCCESS") == "SUCCESS" and failure is None
        file = tc.get("file") or tc.get("name") or ""
        message = red.text((failure.text or failure.get("message") or "").strip()) if failure is not None else ""
        status = "Pass" if ok else ("Blocked" if TOOL_FAILURE.search(message) else "Fail")
        res.append({"case": case_of_flow(file), "flow": file, "suggested_status": status,
                    "message": (f"tool: {message}" if status == "Blocked" and not ok else message),
                    "time_s": float(tc.get("time") or 0)})
    return res


def run_maestro(cmd: list[str], env: dict, timeout: int) -> tuple[int, str]:
    """Runs Maestro (the tests replace it): (exit code, stdout + stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, **env}, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timeout of {timeout} s"
    return r.returncode, r.stdout + r.stderr


def run_helper(workspace: str, flow: str, serial: str | None = None) -> tuple[int, str]:
    """Runs a helper flow (not a case): (0 passed | 2 configuration | 3 failed or no target, redacted message).
    Also used in-process by scripts/remote_login.py."""
    try:
        cfg = configuration(workspace)
        path = next((c for c in (flow, os.path.join(workspace, flow), os.path.join(cfg["folder"], flow))
                     if os.path.isfile(c)), None)
        if path is None:
            raise UsageError(f"helper flow not found: {flow} (in the current folder, the workspace nor "
                             f"{cfg['folder']})")
        if not os.path.basename(path).startswith("_") or case_of_flow(path):
            raise UsageError(f"{os.path.basename(path)}: a helper flow's name starts with '_' (e.g. "
                             "_request_link.yaml); a flow named after a case is a test: run it without --helper")
        maestro = shutil.which("maestro")
        if not maestro:
            raise UsageError("maestro not found -- install the Maestro CLI")
        needs_java()
        # only the variables the helper uses: a runner's .env holds only ci.secrets (11.8 pilot: the login flow
        # needs the e-mail, not the password nor the phone of mobile.variables)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        used = [v for v in cfg["variables"] if f"MAESTRO_{v}" in text]
        extra, secrets = maestro_environment(workspace, used)
        env_ws = read_workspace_env(workspace)      # the others with a value are still redacted from the output
        secrets.update({v: env_ws.get(v) or os.environ.get(v) for v in cfg["variables"]
                        if v not in secrets and (env_ws.get(v) or os.environ.get(v))})
        # the input does not matter to Maestro: "adb" so that a QA_MOBILE_INPUT for device.py is not read here
        serial, _ = mobile_target(read_project(workspace).get("mobile") or {}, serial, "adb")
        ok, target = target_available(cfg["platform"], serial if cfg["platform"] == "android" else None)
    except UsageError as e:
        return 2, str(e)
    if not ok:
        return 3, f"no target: {target}"
    code, output = run_maestro([maestro, *(["--device", str(serial)] if serial else []), "test",
                                os.path.abspath(path)], extra, cfg["timeout"])
    red = Redactor(secrets, sensitive=True)
    lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    # the step that failed first: Maestro's last lines are often only its debug-folder notice (11.8 pilot)
    failed = [ln for ln in lines if re.search(r"FAILED|Element not found|Assertion is false|Timeout", ln)][:3]
    tail = " | ".join(red.text(ln) for ln in [*failed, *[ln for ln in lines[-5:] if ln not in failed]])
    if code != 0:
        return 3, f"{os.path.basename(path)} failed (Maestro exit {code}): {tail}"
    return 0, f"{os.path.basename(path)}: passed (helper flow, no result recorded)"


def report_path(workspace: str, output: str) -> str:
    """Where the report goes: inside the workspace, or in the throwaway temporary folder of a runner, where the
    workspace's CI keeps it until `workspace_ci.py import-flows` reads it (docs/workspace/ci/mobile.yml)."""
    temp = os.environ.get("RUNNER_TEMP")
    if temp and os.path.isabs(output):
        try:
            return workspace_path(temp, output)
        except OutsideWorkspace:
            pass
    return workspace_path(workspace, output)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Runs Maestro flows and links the results to the cases "
                                             "(technique-mobile.md).")
    ap.add_argument("workspace")
    ap.add_argument("flows", nargs="*", help="flows to run: path or case ID (default: every flow of the folder "
                                             "whose name starts with a case ID)")
    ap.add_argument("--output", help="JSON report (inside the workspace)")
    ap.add_argument("--helper", metavar="FLOW", help="run one helper flow (flows/_<name>.yaml), not a case: no "
                                                     "result recorded")
    ap.add_argument("--serial", help="the device (default: $QA_MOBILE_SERIAL, else mobile.serial; iOS: a simulator's "
                                     "UDID, only when given)")
    ap.add_argument("--include-tags", metavar="TAGS", help="only flows with one of these tags (comma separated; the "
                                                           "`tags:` of a flow's header), e.g. smoke,android")
    ap.add_argument("--exclude-tags", metavar="TAGS", help="skip flows with one of these tags")
    ap.add_argument("--evidence-dir", metavar="DIR", help="keep Maestro's screenshots and recordings and the JUnit "
                                                          "report in this folder (inside the workspace, e.g. "
                                                          "evidence/<batch>/maestro)")
    a = ap.parse_args(argv)
    for opt in ("include_tags", "exclude_tags"):
        value = getattr(a, opt)
        if value is not None and not TAGS.fullmatch(value):
            ap.error(f"--{opt.replace('_', '-')}: tags separated by commas, letters, digits, '-', '_' or '.' "
                     f"(got {value!r})")
    if a.helper and (a.include_tags or a.exclude_tags or a.evidence_dir):
        ap.error("--helper runs one flow: no tags and no --evidence-dir")
    if a.helper:
        if a.flows or a.output:
            ap.error("--helper runs one flow and records nothing: no other flows, no --output")
        code, message = run_helper(a.workspace, a.helper, a.serial)
        print(message, file=sys.stderr if code else sys.stdout)
        return code
    try:
        target_path = report_path(a.workspace, a.output) if a.output else None      # refused before anything runs
        keep = workspace_path(a.workspace, a.evidence_dir) if a.evidence_dir else None
    except OutsideWorkspace as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        cfg = configuration(a.workspace)
        flows = (resolve_flows(a.flows, a.workspace, cfg["folder"]) if a.flows
                 else sorted(f for f in glob.glob(os.path.join(cfg["folder"], "*.yaml")) if case_of_flow(f)))
        if not flows:
            raise UsageError(f"no flow named after a case ID (e.g. TC-MOB-001_login.yaml) in {cfg['folder']}")
        no_case = [os.path.basename(f) for f in flows if not case_of_flow(f)]
        if no_case:
            raise UsageError(f"flows without a case ID in the name: {no_case} (a helper flow, not a case: --helper)")
        if a.include_tags or a.exclude_tags:
            flows = [f for f in flows if selected_by_tags(f, a.include_tags, a.exclude_tags)]
            if not flows:
                raise UsageError("no flow has the tags asked for (--include-tags / --exclude-tags): see the `tags:` "
                                 "of the flows' headers")
        maestro = shutil.which("maestro")
        if not maestro:
            raise UsageError("maestro not found -- install the Maestro CLI")
        needs_java()
        extra, secrets = maestro_environment(a.workspace, cfg["variables"])
        device = device_of(cfg["platform"], read_project(a.workspace).get("mobile") or {}, a.serial)
        ok, target = target_available(cfg["platform"], device if cfg["platform"] == "android" else None)
    except (UsageError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    red = Redactor(secrets, sensitive=True)
    rep = {"platform": cfg["platform"], "target": target, "ts": datetime.now().isoformat(timespec="seconds")}
    if not ok:
        rep["results"] = [{"case": case_of_flow(f), "flow": os.path.basename(f), "suggested_status": "Blocked",
                           "message": f"no target: {target}", "time_s": 0} for f in flows]
        code = 3
    else:
        with tempfile.TemporaryDirectory() as tmp:
            junit = os.path.join(tmp, "junit.xml")
            try:
                tags = [f"--{k}-tags={v}" for k, v in (("include", a.include_tags), ("exclude", a.exclude_tags)) if v]
                if keep:
                    os.makedirs(keep, exist_ok=True)
                    tags += [f"--test-output-dir={keep}"]
                r = subprocess.run([maestro, *(["--device", str(device)] if device else []), "test", *flows, *tags,
                                    "--format", "junit", "--output", junit],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, **extra}, timeout=cfg["timeout"], check=False)
                output = r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                output = f"timeout of {cfg['timeout']} s"
            if os.path.isfile(junit):
                with open(junit, encoding="utf-8") as f:
                    text = f.read()
                rep["results"] = read_junit(text, red)
                if keep:
                    with open(os.path.join(keep, "junit.xml"), "w", encoding="utf-8") as f:
                        f.write(red.text(text))             # the evidence file never holds a secret
                    rep["evidence_dir"] = os.path.relpath(keep, a.workspace)
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
    if target_path:
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"report: {target_path}")
    if rep.get("evidence_dir"):
        print(f"evidence: {rep['evidence_dir']}")
    return code


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
