"""
The workspace's CI: remote execution of mobile batches on a GitHub Actions runner (methodology 12.4,
technique-mobile.md §11; plan 11.4). The templates live in docs/workspace/ci/; nothing of a project is written in
them — they read project.yaml.

    python scripts/workspace_ci.py <workspace> install [--yes] [--i-confirm-private]
    python scripts/workspace_ci.py <workspace> check [--batch NAME_OR_CASES]

  the steps of the workflow (docs/workspace/ci/mobile.yml) call:
    batch --input X --mode M --run-id N     resolve the batch, create its run log, write the step's outputs
    write-env                               .env from $QA_SECRETS_JSON, only the names of ci.secrets
    blocked --run-log R --cases ... --reason T      every case Blocked (e.g. no login)
    import-flows --run-log R --report F     run_mobile.py's report into the run log
    prompt --run-log R --cases ...          the agent's prompt (.github/qa/agent-prompt.md filled in; plan 11.5)
    post-check --run-log R [--report F]     the checks after the agent, before anything is committed (plan 11.5)
    pr-body --run-log R ...                 the pull request's body from .github/qa/pr-body.md (plan 11.6)
  and the apply workflow (docs/workspace/ci/apply.yml), after the merge:
    apply-merged --before SHA --after SHA   apply each run log the push added or changed (plan 11.6)

- install: copies the templates into the workspace (.github/workflows/qa-mobile.yml, ...), lists the secrets and
  the variable the workflows need and which exist (`gh secret list`), and checks that the repository is private
  (PUBLIC / INTERNAL refused; unknown only with --i-confirm-private). Without --yes nothing is written (a dry run).
  With --yes: the files copied, the variable QA_ASSISTANT_REPOSITORY set (it is not a secret: this qa-assistant's
  own origin) and, in the user's own interactive terminal (stdin and stdout a real console: on Windows the NUL
  device and pipes are not, although isatty() says so), `gh secret set NAME` for each secret missing — gh asks for
  the value, which the user types or pastes; it never goes on a command line, a file or a log. Outside a terminal
  (e.g. an agent session) no secret is set: the commands are printed for the user to run in their own terminal. A
  secret set wrongly is fixed by running `gh secret set NAME` again (it overwrites). Nothing is committed nor pushed.
- check: the installed copies equal the templates, every secret exists, ci: is valid (validate_project.py), the
  login's helper flow and the flows folder exist, mobile.build is a release, --batch resolves; and the account's
  Actions spending (a warning when it can be read and is not 0; the minutes used this month when GitHub's billing
  API gives them). A dry run of the workflow without running it (no `act`).
- post-check: every file to be committed (runs/, evidence/, context.md: the only paths the workflow commits) is
  scanned for any value of .env (text and binary; the variable's NAME is reported, never the value) -- one found and
  nothing is committed (safe=false, exit 1); every new_defect gets "proposed": true if the agent left it out (the
  approval is the user's, in the pull request: methodology 9.4); the run log is validated by the project's adapter
  WITHOUT applying it (Excel: validate_batch; Jira / Azure DevOps: the tracker's validate_batch), Question items and
  the "proposed" marks aside (both are resolved in the pull request). Its problems are listed in the report and the
  pull request, not blocking. Evidence only from the app in the foreground is the device driver's own rule.
- pr-body: the build, the batch, a count per status (and the cases not run), the proposed bugs (title, case,
  expected -> observed, links to their evidence in the branch), the questions, the checks, the agent's summary, the
  time and the turns used.
- apply-merged: every runs/batch_*.json added or changed between --before and --after; one with a new_defect that
  still has "proposed", or a Question, is refused and named (never applied); the others are applied with the
  project's adapter, as /checkpoint does (Excel: `python -m adapters.excel.apply`; Jira / Azure DevOps:
  `python -m adapters.tracker <ws> apply <log> --yes`: the merge was the approval). The workbook (only after exit 0
  or 5) or tracker/ is committed and pushed to the branch that was pushed, and the result is commented on the pull
  request of the merge. Exit 1 when a run log was refused or its application failed.
- A planned batch is batches/<name>.yaml ({cases: [ID, ...]}, written by /plan-batch); otherwise the input is a list
  of case IDs (name "adhoc"). The run log is runs/batch_<name>_r<run id>.json, the branch run/<name>-<run id>.

Exit codes: 0 ok · 1 problems (check) or refused (install) · 2 usage or configuration · 3 gh failed
"""

from __future__ import annotations

import argparse
import filecmp
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import run_log
import workspace_git as wg
from common import read_project, read_workspace_env

TEMPLATE_DIR = os.path.join(ROOT, "docs", "workspace", "ci")
# template -> where install copies it in the workspace
TEMPLATES = {"mobile.yml": ".github/workflows/qa-mobile.yml", "apply.yml": ".github/workflows/qa-apply.yml",
             "agent-prompt.md": ".github/qa/agent-prompt.md", "pr-body.md": ".github/qa/pr-body.md"}
COMMITTED = ("runs", "evidence", "context.md")     # the only paths the workflow commits
MIN_SECRET_LEN = 4
WORKFLOW_SECRETS = ("CLAUDE_CODE_OAUTH_TOKEN", "QA_ASSISTANT_TOKEN")
VARIABLES = ("QA_ASSISTANT_REPOSITORY",)
CASE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MODES = ("conversational", "flows", "both")


class Refused(Exception):
    """A safety rule (code 1)."""


class UsageError(Exception):
    """Usage or configuration (code 2)."""


class Failed(Exception):
    """gh failed (code 3)."""


# --- the doors to the outside (the tests replace them) ----------------------------------------------------------

def gh(*args: str, interactive: bool = False) -> subprocess.CompletedProcess | None:
    """Runs gh (None when not installed). interactive: gh talks to the terminal (e.g. asks for a secret's value)."""
    exe = shutil.which("gh")
    if not exe:
        return None
    if interactive:
        return subprocess.run([exe, *args], check=False)
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, timeout=120)


WINDOWS = sys.platform == "win32"   # a constant, so the tests can simulate Windows without changing sys.platform


def _windows_console(stream) -> bool:
    """True when the stream's handle is a Windows console: GetConsoleMode succeeds (it fails for NUL and pipes)."""
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def is_terminal() -> bool:
    """A person at a real interactive console, stdin and stdout both. isatty() alone is not enough on Windows, where
    it is True for any character device, e.g. stdin from NUL in a non-interactive agent shell (found in the 11.8
    pilot: `gh secret set` then stored empty values)."""
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is None or not stream.isatty():
                return False
        except (AttributeError, OSError, ValueError):
            return False
        if WINDOWS and not _windows_console(stream):
            return False
    return True


def now() -> datetime:
    return datetime.now()


def output(**values) -> None:
    """The step's outputs ($GITHUB_OUTPUT on a runner) and the same lines on stdout."""
    lines = [f"{k}={v}" for k, v in values.items()]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# --- configuration ----------------------------------------------------------------------------------------------

def project(workspace: str) -> dict:
    try:
        return read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None


def ci_of(proj: dict) -> dict:
    ci = proj.get("ci")
    if ci is None:
        raise UsageError("project.yaml has no ci: section (technique-mobile.md §11.5; the template has it commented)")
    if not isinstance(ci, dict):
        raise UsageError("ci: must be a map")
    return ci


def slug_of(workspace: str) -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    slug = wg.repo_slug(wg.remote_url(workspace)) if wg.inside_repository(workspace) else None
    if not slug:
        raise UsageError("the workspace has no GitHub origin: create its private remote first "
                         "(workspace_git.py <workspace> remote --create OWNER/NAME)")
    return slug


def needed_secrets(proj: dict) -> list[str]:
    return [*WORKFLOW_SECRETS, *sorted(str(x) for x in (ci_of(proj).get("secrets") or []))]


def existing_secrets(slug: str) -> set[str] | None:
    r = gh("secret", "list", "--repo", slug, "--json", "name")
    if r is None or r.returncode != 0:
        return None
    try:
        return {str(x["name"]) for x in json.loads(r.stdout or "[]")}
    except (ValueError, KeyError, TypeError):
        return None


def qa_repository() -> str | None:
    """OWNER/NAME of this qa-assistant's origin (the value of QA_ASSISTANT_REPOSITORY)."""
    return wg.repo_slug(wg.remote_url(ROOT)) if wg.inside_repository(ROOT) else None


# --- batch ------------------------------------------------------------------------------------------------------

def resolve_batch(workspace: str, text: str) -> tuple[str, list[str]]:
    """(name, case IDs) of a planned batch (batches/<name>.yaml) or of a list of case IDs (name "adhoc")."""
    text = (text or "").strip()
    if not text:
        raise UsageError("the batch is empty: a planned batch's name or case IDs")
    path = os.path.join(workspace, "batches", f"{text}.yaml")
    if NAME.fullmatch(text) and os.path.isfile(path):
        import yaml
        with open(path, encoding="utf-8-sig") as f:
            doc = yaml.safe_load(f) or {}
        cases = doc.get("cases") if isinstance(doc, dict) else None
        if not (isinstance(cases, list) and cases and all(isinstance(c, str) and CASE.fullmatch(c) for c in cases)):
            raise UsageError(f"batches/{text}.yaml: expected cases: [<case ID>, ...] (written by /plan-batch)")
        return text, list(dict.fromkeys(cases))
    ids = [x for x in re.split(r"[\s,]+", text) if x]
    bad = [x for x in ids if not CASE.fullmatch(x)]
    if bad:
        planned = sorted(os.path.splitext(os.path.basename(p))[0]
                         for p in glob.glob(os.path.join(workspace, "batches", "*.yaml")))
        raise UsageError(f"{text!r} is neither a planned batch (batches/<name>.yaml: {planned or 'none'}) nor a list "
                         f"of case IDs ({bad[0]!r})")
    return "adhoc", list(dict.fromkeys(ids))


def flows_folder(proj: dict, workspace: str) -> str:
    return os.path.join(workspace, str((proj.get("mobile") or {}).get("flows") or "flows"))


def has_flow(folder: str, case: str) -> bool:
    return bool(glob.glob(os.path.join(folder, glob.escape(case) + "_*.yaml"))
                or os.path.isfile(os.path.join(folder, case + ".yaml")))


def cmd_batch(workspace: str, a) -> int:
    proj = project(workspace)
    ci = ci_of(proj)
    if a.mode not in MODES:
        raise UsageError(f"--mode {a.mode!r}: one of {list(MODES)}")
    if not NAME.fullmatch(a.run_id or ""):
        raise UsageError(f"--run-id {a.run_id!r}")
    name, cases = resolve_batch(workspace, a.input)
    folder = flows_folder(proj, workspace)
    with_flow = [c for c in cases if has_flow(folder, c)]
    if a.mode == "flows":
        flow_cases, agent_cases = with_flow, []
        missing = [c for c in cases if c not in with_flow]
        if missing:
            print(f"warning: no flow for {missing}: not run in mode flows (they stay Not run)", file=sys.stderr)
    elif a.mode == "both":
        flow_cases, agent_cases = with_flow, [c for c in cases if c not in with_flow]
    else:
        flow_cases, agent_cases = [], cases
    log = run_log.batch_id(f"{name}_r{a.run_id}")
    envs = list(proj.get("environments") or {})
    environment = "test" if "test" in envs else (envs[0] if envs else "")
    build = (proj.get("mobile") or {}).get("build") or {}
    path = os.path.join(workspace, "runs", f"{log}.json")
    if not os.path.exists(path):
        run_log.write_log(path, {"mode": f"{a.mode}: remote run {a.run_id} (technique-mobile.md §11)",
                                 "run": {"environment": environment,
                                         "build": str(build.get("version") or build.get("source") or "")},
                                 "remote": {"run_id": a.run_id, "batch": name, "cases": cases},
                                 "results": []})
    login = ((ci.get("login") or {}).get("mode")) or "none"
    output(name=name, run_log=log, branch=f"run/{name}-{a.run_id}", cases=" ".join(cases),
           flow_cases=" ".join(flow_cases), agent_cases=" ".join(agent_cases), login=login,
           max_turns=int(ci.get("max_turns") or 150))
    return 0


def cmd_blocked(workspace: str, a) -> int:
    try:
        blocked = run_log.project_status(read_project(workspace), "blocked")
        for case in a.cases:
            run_log.add(workspace, a.run_log, case, blocked, a.reason)
    except (run_log.UsageError, run_log.Refused) as e:
        raise UsageError(str(e)) from None
    print(f"{len(a.cases)} case(s) {blocked} in {a.run_log}: {a.reason}")
    return 0


def cmd_import_flows(workspace: str, a) -> int:
    """run_mobile.py's report into the run log: one item per flow, its suggested status and message."""
    if not os.path.isfile(a.report):
        raise UsageError(f"{a.report}: no report (run_mobile.py did not write one)")
    with open(a.report, encoding="utf-8") as f:
        report = json.load(f)
    n = 0
    for r in report.get("results") or []:
        if not r.get("case"):
            continue
        note = f"flow {r.get('flow')} on {report.get('target')}" + (f": {r['message']}" if r.get("message") else "")
        try:
            status = run_log.project_status(read_project(workspace), r["suggested_status"].lower())
            run_log.add(workspace, a.run_log, r["case"], status, note)
        except (run_log.UsageError, run_log.Refused) as e:
            raise UsageError(str(e)) from None
        n += 1
    print(f"{n} flow result(s) written to {a.run_log}")
    return 0


# --- write-env --------------------------------------------------------------------------------------------------

def cmd_write_env(workspace: str, a) -> int:
    proj = project(workspace)
    names = [str(x) for x in (ci_of(proj).get("secrets") or [])]
    try:
        secrets = json.loads(os.environ.get("QA_SECRETS_JSON") or "{}")
    except ValueError:
        raise UsageError("QA_SECRETS_JSON is not JSON (the workflow passes toJSON(secrets))") from None
    missing = [n for n in names if not secrets.get(n)]
    if missing:
        raise UsageError(f"Actions secrets missing or empty: {', '.join(missing)} (Settings > Secrets and variables "
                         "> Actions; workspace_ci.py <workspace> install --yes)")
    if wg.inside_repository(workspace) and \
            wg.git(workspace, "check-ignore", "-q", "--no-index", ".env").returncode != 0:
        raise Refused(".env is not ignored by the workspace's .gitignore: it would be committed")
    path = os.path.join(workspace, ".env")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{n}={secrets[n]}\n" for n in names))
    if os.name != "nt":
        os.chmod(path, 0o600)
    print(f".env: {len(names)} variable(s) from the Actions secrets ({', '.join(names) or 'none'}; values not shown)")
    return 0


# --- install and check ------------------------------------------------------------------------------------------

def template_state(workspace: str) -> list[tuple[str, str, str]]:
    """(template, destination, new | changed | same)."""
    out = []
    for name, dest in TEMPLATES.items():
        target = os.path.join(workspace, *dest.split("/"))
        state = ("new" if not os.path.isfile(target) else
                 "same" if filecmp.cmp(os.path.join(TEMPLATE_DIR, name), target, shallow=False) else "changed")
        out.append((name, dest, state))
    return out


def check_private(workspace: str, slug: str, confirmed: bool) -> str:
    vis, why = wg.visibility(f"https://github.com/{slug}")
    if vis in ("PUBLIC", "INTERNAL"):
        raise Refused(f"{slug} is {vis}: the workspace's CI runs only in a private repository (it holds the "
                      "project's data and its runs)")
    if vis != "PRIVATE" and not confirmed:
        raise Refused(f"the visibility of {slug} could not be confirmed ({why}); check it and, if it is private, run "
                      "again with --i-confirm-private")
    return vis or "private (confirmed by the user)"


def cmd_install(workspace: str, a) -> int:
    proj = project(workspace)
    ci_of(proj)
    import validate_project as vp
    problems, _ = vp.ci_section(workspace, proj)
    if problems:
        raise UsageError("ci: has problems: " + "; ".join(problems))
    slug = slug_of(workspace)
    print(f"repository: {slug} ({check_private(workspace, slug, a.i_confirm_private)})")
    for name, dest, state in template_state(workspace):
        print(f"{'would copy' if not a.yes else 'copy'}: {name} -> {dest} ({state})")
    have = existing_secrets(slug)
    needed = needed_secrets(proj)
    missing = [n for n in needed if have is None or n not in have]
    for n in needed:
        print(f"secret {n}: " + ("exists" if have is not None and n in have else
                                 "missing" if have is not None else "unknown (gh secret list failed)"))
    qa = qa_repository()
    print(f"variable QA_ASSISTANT_REPOSITORY: {qa or 'unknown (this qa-assistant has no GitHub origin)'}")
    if not a.yes:
        print("dry run: nothing written -- with the user's yes, run again with --yes")
        return 0
    for name, dest, state in template_state(workspace):
        if state != "same":
            target = os.path.join(workspace, *dest.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(TEMPLATE_DIR, name), target)
    if qa:
        r = gh("variable", "set", "QA_ASSISTANT_REPOSITORY", "--repo", slug, "--body", qa)
        if r is None or r.returncode != 0:
            print("warning: QA_ASSISTANT_REPOSITORY not set (gh failed): set it in Settings > Secrets and variables "
                  "> Actions > Variables", file=sys.stderr)
    terminal = is_terminal()
    if missing and not terminal:
        print("not an interactive terminal (e.g. an agent session): no secret is set here -- run these commands in "
              "your own terminal; a secret set wrongly is fixed by running gh secret set NAME again (it overwrites)")
    for n in missing:
        command = ["secret", "set", n, "--repo", slug]
        if terminal:
            print(f"gh {' '.join(command)}  (paste the value when gh asks; it is never shown)")
            r = gh(*command, interactive=True)
            if r is None or r.returncode != 0:
                raise Failed(f"gh secret set {n} failed")
        else:
            print(f"to run by the user, in a terminal: gh {' '.join(command)}")
    print("installed: commit .github/ with the next checkpoint and push (with the user's approval); set the "
          "Actions spending limit to 0 (docs/workspace/ci/README.md)")
    return 0


def billing(owner: str) -> list[str]:
    """Warnings about the account's Actions spending, when GitHub's API gives them (best effort)."""
    notes = []
    today = now()
    r = gh("api", f"/users/{owner}/settings/billing/usage/summary?year={today.year}&month={today.month}"
                  "&product=actions")
    if r is not None and r.returncode == 0:
        try:
            items = json.loads(r.stdout).get("usageItems") or []
            minutes = sum(float(i.get("grossQuantity") or 0) for i in items if "minute" in str(i.get("unitType",
                                                                                                    "")).lower())
            billed = sum(float(i.get("netAmount") or 0) for i in items)
            notes.append(f"Actions this month: {minutes:.0f} minutes used" + (
                f" -- WARNING: {billed:.2f} billed; set the spending limit to 0" if billed > 0 else ""))
        except (ValueError, AttributeError, TypeError):
            notes.append("Actions minutes: the billing API gave an unexpected answer")
    else:
        notes.append("Actions minutes: unknown (the billing API did not answer; see Settings > Billing)")
    r = gh("api", f"/organizations/{owner}/settings/billing/budgets")
    if r is not None and r.returncode == 0:
        try:
            budgets = [b for b in json.loads(r.stdout).get("budgets") or []
                       if "actions" in str(b.get("budget_product_sku") or b.get("budget_product_skus") or "").lower()]
            if not any(b.get("budget_amount") == 0 and b.get("prevent_further_usage") for b in budgets):
                notes.append("WARNING: no Actions budget of 0 that stops usage: set the spending limit to 0")
            else:
                notes.append("Actions spending limit: 0, usage stops at the limit")
        except (ValueError, AttributeError, TypeError):
            notes.append("Actions spending limit: the budgets API gave an unexpected answer")
    else:
        notes.append("Actions spending limit: cannot be read through the API for this account: check that it is 0 "
                     "(Settings > Billing and licensing > Budgets and alerts)")
    return notes


def cmd_check(workspace: str, a) -> int:
    proj = project(workspace)
    ci = ci_of(proj)
    import validate_project as vp
    problems, warnings = vp.ci_section(workspace, proj)
    for name, dest, state in template_state(workspace):
        if state == "new":
            problems.append(f"{dest} is not installed (workspace_ci.py <workspace> install)")
        elif state == "changed":
            problems.append(f"{dest} differs from the template {name}: install again (never edit the copy)")
    slug = slug_of(workspace)
    have = existing_secrets(slug)
    if have is None:
        warnings.append("the repository's secrets could not be listed (gh secret list)")
    else:
        missing = [n for n in needed_secrets(proj) if n not in have]
        if missing:
            problems.append(f"secrets missing: {', '.join(missing)} (workspace_ci.py <workspace> install --yes)")
    folder = flows_folder(proj, workspace)
    if not os.path.isdir(folder):
        problems.append(f"the flows folder {os.path.relpath(folder, workspace)} does not exist")
    login = ci.get("login") or {}
    if login.get("mode") == "manual-issue" and login.get("flow") and \
            not os.path.isfile(os.path.join(workspace, str(login["flow"]))):
        problems.append(f"ci.login.flow {login['flow']} does not exist")
    source = str(((proj.get("mobile") or {}).get("build") or {}).get("source") or "")
    if not source.startswith("release "):
        problems.append("mobile.build.source is not a release (workspace_build.py <workspace> publish <apk>)")
    if a.batch:
        try:
            name, cases = resolve_batch(workspace, a.batch)
            print(f"batch {name}: {len(cases)} case(s), {sum(has_flow(folder, c) for c in cases)} with a flow")
        except UsageError as e:
            problems.append(str(e))
    warnings += billing(slug.split("/")[0])
    for p in problems:
        print(f"PROBLEM: {p}")
    for w in warnings:
        print(f"warning: {w}")
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


# --- the agent (plan 11.5) --------------------------------------------------------------------------------------

LOGIN_LINES = {
    "done": "The login is done.",
    "skipped": ("The login was SKIPPED on purpose (a check of the pipeline, input `login: skip`): do not try to log "
                "in. If the app shows its login screen, write every case Blocked (the project's word for it) with "
                "\"login skipped (pipeline check)\" and stop."),
}


def cmd_prompt(workspace: str, a) -> int:
    """The installed prompt with its placeholders filled in (printed: the workflow passes it to `claude -p`)."""
    path = os.path.join(workspace, *TEMPLATES["agent-prompt.md"].split("/"))
    if not os.path.isfile(path):
        raise UsageError(f"{TEMPLATES['agent-prompt.md']} is not installed (workspace_ci.py <workspace> install)")
    ci = ci_of(project(workspace))
    with open(path, encoding="utf-8") as f:
        text = f.read()
    values = {"workspace": workspace, "qa": ROOT, "run_log": run_log.batch_id(a.run_log), "cases": " ".join(a.cases),
              "max_turns": str(int(ci.get("max_turns") or 150)), "login": LOGIN_LINES[a.login]}
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    left = sorted(set(re.findall(r"\{\{[a-z_]+\}\}", text)))
    if left:
        raise UsageError(f"the prompt has unknown placeholders {left}: install the templates again")
    sys.stdout.write(text)
    return 0


def files_to_commit(workspace: str) -> list[str]:
    """The files under runs/, evidence/ and context.md (relative paths), as the pull request step would add them."""
    out = []
    for top in COMMITTED:
        full = os.path.join(workspace, top)
        if os.path.isfile(full):
            out.append(top)
        for folder, _, files in os.walk(full):
            out += [os.path.relpath(os.path.join(folder, f), workspace).replace(os.sep, "/") for f in files]
    return sorted(out)


def leaks(workspace: str, files: list[str]) -> list[str]:
    """'<file>: <VARIABLE>' for every .env value found in a file (text or binary). Never the value."""
    values = [(k, v) for k, v in read_workspace_env(workspace).items() if v and len(v) >= MIN_SECRET_LEN]
    found = []
    for rel in files:
        with open(os.path.join(workspace, rel), "rb") as f:
            data = f.read()
        for name, value in values:
            forms = {value.encode("utf-8"), value.encode("utf-16-le"), json.dumps(value)[1:-1].encode("utf-8")}
            if any(form in data for form in forms):
                found.append(f"{rel}: {name}")
    return found


def adapter_problems(workspace: str, proj: dict, doc: dict) -> list[str]:
    """The adapter's own validation of the run log, without applying it; Question items and "proposed" aside."""
    items = [dict(x) for x in doc.get("results") or [] if isinstance(x, dict)
             and not str(x.get("status") or "").lower().startswith("question")]
    for x in items:
        if isinstance(x.get("new_defect"), dict):
            x["new_defect"] = {k: v for k, v in x["new_defect"].items() if k != "proposed"}
    if not items:
        return []
    tracker = proj.get("tracker")
    if tracker == "excel":
        from adapters.excel import ConfigError, ExcelAdapter
        try:
            ad = ExcelAdapter.from_project(workspace)
            ad.load()
        except (ConfigError, FileNotFoundError, KeyError, OSError) as e:
            return [f"the workbook could not be read to validate the run log: {e}"]
        return ad.validate_batch(items)
    if tracker in ("jira", "azure-devops"):
        from adapters.tracker import base, config, errors
        try:
            return base.validate_batch({**doc, "results": items}, config.load(workspace))
        except errors.TrackerError as e:   # a configuration problem is reported in the pull request, never hidden
            return [f"the run log could not be validated: {e}"] + list(getattr(e, "problems", None) or [])
    return [f"tracker {tracker!r}: no adapter to validate the run log"]


def cmd_post_check(workspace: str, a) -> int:
    proj = project(workspace)
    name = run_log.batch_id(a.run_log)
    path = os.path.join(workspace, "runs", f"{name}.json")
    try:
        doc = run_log.read_log(path)
    except run_log.UsageError as e:
        raise UsageError(str(e)) from None
    report = {"run_log": f"runs/{name}.json", "files": files_to_commit(workspace), "proposed_added": [],
              "leaks": [], "problems": []}
    report["leaks"] = leaks(workspace, report["files"])
    for item in doc["results"]:
        new = item.get("new_defect") if isinstance(item, dict) else None
        if isinstance(new, dict) and new and new.get("proposed") is not True:
            new["proposed"] = True
            report["proposed_added"].append(str(item.get("tc")))
    if report["proposed_added"] and os.path.isfile(path):
        run_log.write_log(path, doc)
    report["problems"] = adapter_problems(workspace, proj, doc)
    report["safe"] = not report["leaks"]
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"files to commit: {len(report['files'])}")
    for x in report["leaks"]:
        print(f"LEAK: a value of .env in {x} -- nothing is committed; remove it from the file")
    if report["proposed_added"]:
        print(f'"proposed": true added to the new_defect of {", ".join(report["proposed_added"])} (approval in the '
              "pull request)")
    for p in report["problems"]:
        print(f"run log: {p}")
    output(safe="true" if report["safe"] else "false")
    return 0 if report["safe"] else 1


# --- review and apply (plan 11.6) -------------------------------------------------------------------------------

def blob_url(slug: str, branch: str, path: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    return f"{server}/{slug}/blob/{branch}/{path}"


def evidence_of(item: dict) -> list[str]:
    out = []
    for value in (item.get("evidence"), (item.get("new_defect") or {}).get("evidence")):
        out += [value] if isinstance(value, str) else [x for x in value or [] if isinstance(x, str)]
    return list(dict.fromkeys(out))


def read_json_file(path: str | None) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def cmd_pr_body(workspace: str, a) -> int:
    proj = project(workspace)
    ci = ci_of(proj)
    path = os.path.join(workspace, *TEMPLATES["pr-body.md"].split("/"))
    if not os.path.isfile(path):
        raise UsageError(f"{TEMPLATES['pr-body.md']} is not installed (workspace_ci.py <workspace> install)")
    name = run_log.batch_id(a.run_log)
    try:
        doc = run_log.read_log(os.path.join(workspace, "runs", f"{name}.json"))
    except run_log.UsageError as e:
        raise UsageError(str(e)) from None
    remote = doc.get("remote") or {}
    slug = slug_of(workspace)
    branch = a.branch or f"run/{remote.get('batch', 'adhoc')}-{remote.get('run_id', '')}"
    results = [x for x in doc["results"] if isinstance(x, dict)]
    counts: dict[str, int] = {}
    for x in results:
        counts[str(x.get("status"))] = counts.get(str(x.get("status")), 0) + 1
    done = {str(x.get("tc")) for x in results}
    not_run = [c for c in remote.get("cases") or [] if c not in done]
    totals = [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    totals.append(f"| Not run | {len(not_run)}" + (f" ({', '.join(not_run)})" if not_run else "") + " |")
    proposed = []
    for x in results:
        new = x.get("new_defect")
        if isinstance(new, dict) and new:
            links = ", ".join(f"[{os.path.basename(e)}]({blob_url(slug, branch, e)})" for e in evidence_of(x))
            proposed.append(f"- **{new.get('title') or '(no title)'}** — case {x.get('tc')}: expected "
                            f"{new.get('expected') or '?'} → observed {new.get('observed') or '?'}"
                            + (f" · evidence: {links}" if links else ""))
    questions = [f"- **{x.get('tc')}**: {x.get('actual_result') or '(no question written)'}" for x in results
                 if str(x.get("status") or "").lower().startswith("question")]
    checks = read_json_file(a.checks)
    check_lines = ([f"- {len(checks.get('files') or [])} file(s) committed; no value of .env in them"]
                   if checks.get("safe") else [])
    check_lines += [f'- "proposed": true added to the new_defect of {c}' for c in checks.get("proposed_added") or []]
    check_lines += [f"- run log: {p}" for p in checks.get("problems") or []]
    agent = read_json_file(a.agent)
    turns = str(agent.get("num_turns", "—"))
    if agent.get("subtype") and agent.get("subtype") != "success":
        turns += f" ({agent['subtype']})"
    minutes = "?"
    if a.started:
        minutes = str(max(0, round((now().timestamp() - float(a.started)) / 60)))
    summary_path = os.path.join(workspace, "runs", f"{name}.summary.md")
    summary = "(none: the agent did not run or wrote no summary)"
    if os.path.isfile(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            summary = f.read().strip() or summary
    server, run = os.environ.get("GITHUB_SERVER_URL") or "https://github.com", os.environ.get("GITHUB_RUN_ID", "")
    values = {"batch": str(remote.get("batch") or name), "run_id": str(remote.get("run_id") or run),
              "build": a.build or str((doc.get("run") or {}).get("build") or "?"),
              "environment": str((doc.get("run") or {}).get("environment") or "?"),
              "mode": str(doc.get("mode") or "").split(":")[0] or "?", "minutes": minutes,
              "timeout": str(int(ci.get("timeout_minutes") or 60)), "turns": turns,
              "max_turns": str(int(ci.get("max_turns") or 150)),
              "run_url": f"{server}/{slug}/actions/runs/{run}" if run else f"{server}/{slug}/actions",
              "totals": "\n".join(totals), "proposed": "\n".join(proposed) or "None.",
              "questions": "\n".join(questions) or "None.", "checks": "\n".join(check_lines) or "Not run.",
              "summary": summary, "run_log": f"runs/{name}.json"}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    sys.stdout.write(text)
    return 0


def refusals(doc: dict) -> list[str]:
    """Why a merged run log cannot be applied: a proposal or a Question left."""
    out = []
    for x in doc.get("results") or []:
        if not isinstance(x, dict):
            continue
        new = x.get("new_defect")
        if isinstance(new, dict) and new.get("proposed", False) is not False:
            out.append(f'{x.get("tc")}: the new_defect still has "proposed" (approve it by removing it, or drop it)')
        if str(x.get("status") or "").lower().startswith("question"):
            out.append(f"{x.get('tc')}: still Question (answer it with the final status)")
    return out


def run_adapter(workspace: str, tracker: str, path: str) -> tuple[int, str]:
    """The project's adapter, the same command as /checkpoint (the tests replace it)."""
    cmd = ([sys.executable, "-m", "adapters.excel.apply", workspace, path] if tracker == "excel" else
           [sys.executable, "-m", "adapters.tracker", workspace, "apply", path, "--yes"])
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       check=False, timeout=1800)
    return r.returncode, (r.stdout + r.stderr).strip()


def merged_run_logs(workspace: str, before: str, after: str) -> list[str]:
    if not before or set(before) == {"0"}:
        out = wg.git_ok(workspace, "diff-tree", "--no-commit-id", "--name-only", "-r", "--diff-filter=AM", after,
                        "--", "runs/")
    else:
        out = wg.git_ok(workspace, "diff", "--name-only", "--diff-filter=AM", before, after, "--", "runs/")
    return sorted(n for n in out.splitlines() if re.fullmatch(r"runs/batch_[^/]+\.json", n.strip()))


def cmd_apply_merged(workspace: str, a) -> int:
    proj = project(workspace)
    tracker = proj.get("tracker")
    if tracker not in ("excel", "jira", "azure-devops"):
        raise UsageError(f"tracker {tracker!r}: no adapter to apply the run logs")
    logs = merged_run_logs(workspace, a.before, a.after)
    if not logs:
        print("no run log added or changed: nothing to apply")
        return 0
    lines, failed = [], False
    for rel in logs:
        try:
            doc = run_log.read_log(os.path.join(workspace, rel))
        except run_log.UsageError as e:
            lines.append(f"- `{rel}`: NOT APPLIED -- {e}")
            failed = True
            continue
        why = refusals(doc)
        if why:
            lines.append(f"- `{rel}`: NOT APPLIED -- " + "; ".join(why))
            failed = True
            continue
        code, out = run_adapter(workspace, tracker, os.path.join(workspace, rel))
        tail = out.splitlines()[-1] if out else ""
        lines.append(f"- `{rel}`: {'applied' if code == 0 else f'adapter exit {code}'}" + (f" -- {tail}" if tail else ""))
        failed = failed or code not in (0, 5)
    paths = ["tracker"] if tracker != "excel" else [str((proj.get("excel") or {}).get("file") or "")]
    for p in paths:
        if p and os.path.exists(os.path.join(workspace, p)):
            wg.git_ok(workspace, "add", "--", p)
    if wg.git(workspace, "diff", "--cached", "--quiet").returncode != 0:
        wg.git_ok(workspace, "commit", "-q", "-m", "QA apply: " + ", ".join(os.path.basename(x) for x in logs))
        wg.git_ok(workspace, "push", "origin", "HEAD")
        lines.append("- committed and pushed: " + ", ".join(p for p in paths if p))
    body = "QA apply (the merge was the approval, methodology 9.4):\n\n" + "\n".join(lines)
    print(body)
    slug = slug_of(workspace)
    r = gh("api", f"repos/{slug}/commits/{a.after}/pulls")
    numbers = []
    if r is not None and r.returncode == 0:
        try:
            numbers = [int(x["number"]) for x in json.loads(r.stdout or "[]")]
        except (ValueError, KeyError, TypeError):
            numbers = []
    for n in numbers:
        c = gh("pr", "comment", str(n), "--repo", slug, "--body", body)
        if c is None or c.returncode != 0:
            print(f"warning: the result could not be commented on #{n}", file=sys.stderr)
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The workspace's CI (remote execution, technique-mobile.md §11).")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="copy the templates, list the secrets (dry run without --yes)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--i-confirm-private", action="store_true")
    p = sub.add_parser("check", help="the installed workflow against the workspace (no run)")
    p.add_argument("--batch", help="a planned batch or case IDs to resolve")
    p = sub.add_parser("batch", help="(workflow) resolve the batch and create its run log")
    p.add_argument("--input", required=True)
    p.add_argument("--mode", default="conversational")
    p.add_argument("--run-id", required=True)
    sub.add_parser("write-env", help="(workflow) .env from $QA_SECRETS_JSON, only ci.secrets")
    p = sub.add_parser("blocked", help="(workflow) every case Blocked, with the reason")
    p.add_argument("--run-log", required=True)
    p.add_argument("--cases", nargs="+", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("import-flows", help="(workflow) run_mobile.py's report into the run log")
    p.add_argument("--run-log", required=True)
    p.add_argument("--report", required=True)
    p = sub.add_parser("prompt", help="(workflow) the agent's prompt, filled in")
    p.add_argument("--run-log", required=True)
    p.add_argument("--cases", nargs="+", required=True)
    p.add_argument("--login", choices=sorted(LOGIN_LINES), default="done",
                   help="skipped: a pipeline check without the owner (input login: skip)")
    p = sub.add_parser("post-check", help="(workflow) the checks after the agent, before the commit")
    p.add_argument("--run-log", required=True)
    p.add_argument("--report", help="JSON report for the pull request's body")
    p = sub.add_parser("pr-body", help="(workflow) the pull request's body")
    p.add_argument("--run-log", required=True)
    p.add_argument("--build", default="")
    p.add_argument("--branch", default="")
    p.add_argument("--started", default="", help="epoch seconds of the job's start")
    p.add_argument("--agent", help="claude -p --output-format json")
    p.add_argument("--checks", help="post-check's report")
    p = sub.add_parser("apply-merged", help="(apply workflow) apply the run logs of a merge")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    a = ap.parse_args(argv)
    workspace = os.path.abspath(a.workspace)
    commands = {"install": cmd_install, "check": cmd_check, "batch": cmd_batch, "write-env": cmd_write_env,
                "blocked": cmd_blocked, "import-flows": cmd_import_flows, "prompt": cmd_prompt,
                "post-check": cmd_post_check, "pr-body": cmd_pr_body, "apply-merged": cmd_apply_merged}
    try:
        if not os.path.isdir(workspace):
            raise UsageError(f"{workspace} is not a folder")
        return commands[a.command](workspace, a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (Failed, wg.Failed, subprocess.TimeoutExpired) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
