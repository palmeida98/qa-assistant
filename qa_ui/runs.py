"""
The runs the interface starts (docs/qa-ui.md sections 5.3, 7, 8 and 10).

- A batch: the module's skill in Claude Code headless (`claude -p`), in the workspace, with the user's
  subscription, as the remote run of the methodology (12.4). The prompt goes through standard input (never the
  command line: a prompt holds the cases' text).
- The two automated audits: scripts/audit_a11y.py and the qa-audit engine.
- Asking Claude for a reading of the cases (no tool allowed), new cases through the project's adapter, and applying
  a run log: a dry run first, the real run only with the user's confirmation.

One run at a time per workspace. The output of a run is redacted with the values of .env before it is stored
(.qa-ui/runs/<id>.log) or shown. Nothing here classifies a result: the run log is the only source.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime

import run_log
from common import Redactor, read_workspace_env

from . import MODULES, ROOT, intake, workspace
from .suggest import SKILLS, find_claude, find_test_environment

TAIL = 400                      # lines of output kept in memory per run
GRACE = 10                      # seconds between the interrupt and the kill (docs/qa-ui.md section 8.3)
ASK_TIMEOUT = 300
ADAPTER_TIMEOUT = 1800
SITE_AUDIT_TYPES = "accessibility,security,performance,functional"

# what each module's skill may use besides Read, Glob, Grep, Skill, run_log.py and writing in runs/ and evidence/
MODULE_SCRIPTS = {"functional-web": (), "exploratory": (), "api": ("api_request.py",),
                  "accessibility": ("audit_a11y.py",), "mobile": ("device.py", "run_mobile.py")}
MODULE_MCP = {"functional-web", "exploratory", "accessibility"}     # the Playwright MCP of the workspace's .mcp.json

EXCEL_ADD = {0: "added", 2: "cross-check problems: the original workbook is untouched", 3: "the recalculation failed",
             4: "refused: an ID that exists, a candidate duplicate (same title in the sheet) or an invalid file",
             5: "added, but the summary does not count the new rows: fix its formulas, never the numbers"}
EXCEL_APPLY = {0: "applied", 2: "cross-check problems: the original workbook is untouched",
               3: "the recalculation failed", 4: "invalid batch: nothing written",
               5: "applied and recalculated, but the workbook's summary does not match: fix its formulas"}
TRACKER = {0: "sent, or nothing to send", 1: "dry run: nothing sent yet", 2: "conflict with the tracker",
           3: "network, authentication or rate limit: a send may be partial, run it again to continue",
           4: "invalid input or configuration", 5: "sent, but what was read back differs"}


class Refused(Exception):
    """The run cannot start (one is in progress, a tool is missing, the batch cannot run)."""


class NotFound(Exception):
    """No such run."""


def posix(path: str) -> str:
    """A path as the agent types it: forward slashes also on Windows (Git Bash takes C:/...)."""
    return os.path.abspath(path).replace("\\", "/")


def template(name: str, values: dict[str, str]) -> str:
    """A prompt of qa_ui/prompts/ with its {{placeholders}} filled in, in one pass (a value is never read again
    for placeholders: the cases' text may hold braces)."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", name), encoding="utf-8") as f:
        text = f.read()
    unknown = sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", text)) - set(values))
    if unknown:
        raise ValueError(f"qa_ui/prompts/{name} has unknown placeholders {unknown}")
    return re.sub(r"\{\{([a-z_]+)\}\}", lambda m: values[m.group(1)], text)


def case_block(case: dict) -> str:
    lines = [f"### {case['id']} — {case.get('title') or ''}".rstrip()]
    for label, field in (("Role", "role"), ("Platform", "platform"), ("Preconditions", "preconditions"),
                         ("Steps", "steps"), ("Expected result", "expected")):
        value = str(case.get(field) or "").strip()
        if value:
            lines.append(f"{label}: {value}" if "\n" not in value else f"{label}:\n{value}")
    if not str(case.get("expected") or "").strip():
        lines.append("Expected result: (none in the library: mark it Question)")
    return "\n".join(lines)


def mcp_servers(ws: str) -> list[str]:
    """The names of the MCP servers of the workspace's .mcp.json ([] without one, or when it cannot be read)."""
    try:
        with open(os.path.join(ws, ".mcp.json"), encoding="utf-8-sig") as f:
            servers = json.load(f).get("mcpServers")
    except (OSError, ValueError, AttributeError):
        return []
    return [str(n) for n in servers] if isinstance(servers, dict) else []


def allowed_tools(module: str, qa: str, servers: list[str]) -> list[str]:
    tools = ["Read", "Glob", "Grep", "Skill", f"Bash(python {qa}/scripts/run_log.py *)", "Write(runs/**)",
             "Edit(runs/**)", "Write(evidence/**)", "Edit(evidence/**)"]
    tools += [f"Bash(python {qa}/scripts/{script} *)" for script in MODULE_SCRIPTS[module]]
    if module in MODULE_MCP:
        tools += [f"mcp__{name}" for name in servers]
    return tools


def batch_command(claude: str, module: str, ws: str, qa: str, max_turns: int) -> list[str]:
    servers = mcp_servers(ws) if module in MODULE_MCP else []
    cmd = [claude, "-p", "--max-turns", str(max_turns), "--permission-mode", "dontAsk", "--output-format",
           "stream-json", "--verbose", "--add-dir", qa, "--disallowedTools", "WebFetch,WebSearch",
           "--allowedTools", ",".join(allowed_tools(module, qa, servers))]
    if servers:
        cmd += ["--mcp-config", os.path.join(ws, ".mcp.json")]
    return cmd


def render_event(line: str) -> list[str] | None:
    """The readable lines of one stream-json event of Claude Code; None when the line is not such an event (it is
    then shown as it is). Tolerant on purpose: an unknown event shows nothing rather than break the run."""
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if not isinstance(ev, dict) or "type" not in ev:
        return None
    out = []
    if ev["type"] == "assistant":
        content = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                out += str(block["text"]).strip().splitlines()
            elif block.get("type") == "tool_use":
                given = block.get("input") if isinstance(block.get("input"), dict) else {}
                detail = next((str(given[k]) for k in ("command", "file_path", "path", "pattern", "skill", "url")
                               if given.get(k)), "")
                detail = detail.replace("\n", " ")
                out.append(f"> {block.get('name')}" + (f" {detail[:200]}{'…' if len(detail) > 200 else ''}"
                                                       if detail else ""))
    elif ev["type"] == "result":
        if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
            out += str(ev.get("result") or "").strip().splitlines()    # otherwise the last assistant text said it
        out.append(f"[{ev.get('subtype') or 'result'}: {ev.get('num_turns', '?')} turn(s)"
                   + (f", {round(ev['duration_ms'] / 1000)} s" if isinstance(ev.get("duration_ms"), int | float)
                      else "") + "]")
    return out


class Run:
    def __init__(self, run_id: str, kind: str, label: str, command: list[str], log_path: str,
                 batch: str | None = None, report: str | None = None):
        self.id, self.kind, self.label, self.command, self.log_path = run_id, kind, label, command, log_path
        self.batch, self.report = batch, report
        self.state = "running"
        self.reason: str | None = None
        self.code: int | None = None
        self.started = datetime.now().isoformat(timespec="seconds")
        self.finished: str | None = None
        self.tail: collections.deque[str] = collections.deque(maxlen=TAIL)
        self.lines = 0
        self.proc: subprocess.Popen | None = None
        self.stop_requested: str | None = None
        self.timer: threading.Timer | None = None
        self.done = threading.Event()

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "reason": self.reason,
                "code": self.code, "started": self.started, "finished": self.finished, "batch": self.batch,
                "report": self.report, "lines": self.lines, "tail": list(self.tail)}


class Runner:
    """One per served workspace: starts the runs, one at a time, and keeps those of this session."""

    def __init__(self, ws: str, claude: str | None = None, max_turns: int = 150, timeout_minutes: float = 60,
                 qa: str = ROOT, grace: float = GRACE):
        self.ws = os.path.abspath(ws)
        self.claude_path = claude
        self.max_turns, self.timeout_minutes, self.qa, self.grace = int(max_turns), timeout_minutes, qa, grace
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self.pending_cases: str | None = None      # the new-cases file of a dry run not confirmed yet

    # ------------------------------------------------------------------ helpers

    def claude(self) -> str | None:
        return find_claude(self.claude_path)

    def redactor(self) -> Redactor:
        return Redactor(read_workspace_env(self.ws))

    def current(self) -> Run | None:
        return next((r for r in self.runs.values() if r.state == "running"), None)

    def _new_id(self, kind: str) -> str:
        stem = f"{datetime.now():%Y%m%dT%H%M%S}-{kind}"
        run_id, n = stem, 1
        while run_id in self.runs:
            n += 1
            run_id = f"{stem}-{n}"
        return run_id

    def _spawn(self, run: Run, cwd: str, stdin_text: str | None, render: bool) -> Run:
        os.makedirs(os.path.dirname(run.log_path), exist_ok=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        popen = {"start_new_session": True} if os.name != "nt" else \
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            run.proc = subprocess.Popen(run.command, cwd=cwd, env=env, text=True, encoding="utf-8",
                                        errors="replace", stdin=subprocess.PIPE if stdin_text is not None else
                                        subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        bufsize=1, **popen)
        except OSError as e:
            raise Refused(f"{os.path.basename(run.command[0])} could not be started: {e}") from None
        self.runs[run.id] = run
        redactor = self.redactor()
        threading.Thread(target=self._pump, args=(run, redactor, stdin_text, render), daemon=True).start()
        if self.timeout_minutes:
            run.timer = threading.Timer(self.timeout_minutes * 60, self._stop, args=(run, "the time limit was reached"))
            run.timer.daemon = True
            run.timer.start()
        return run

    def _pump(self, run: Run, redactor: Redactor, stdin_text: str | None, render: bool) -> None:
        proc = run.proc
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except OSError:
                pass                      # the command ended before reading its input: its exit code says why
        with proc.stdout, open(run.log_path, "w", encoding="utf-8", newline="\n") as log:
            for raw in proc.stdout:
                raw = raw.rstrip("\r\n")
                shown = render_event(raw) if render else None
                for line in ([raw] if shown is None else shown):
                    line = redactor.text(line)
                    log.write(line + "\n")
                    log.flush()
                    run.tail.append(line)
                    run.lines += 1
        run.code = proc.wait()
        if run.timer:
            run.timer.cancel()
        run.finished = datetime.now().isoformat(timespec="seconds")
        if run.stop_requested:
            run.state, run.reason = "stopped", run.stop_requested
        elif run.code == 0:
            run.state = "finished"
        else:
            run.state, run.reason = "failed", f"exit code {run.code}"
        run.done.set()

    def _signal(self, run: Run, hard: bool) -> None:
        proc = run.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                if hard:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
                else:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGINT)
        except (OSError, ValueError):
            pass                          # it ended meanwhile

    def _stop(self, run: Run, reason: str) -> None:
        if run.state != "running" or run.stop_requested:
            return
        run.stop_requested = reason
        self._signal(run, hard=False)
        if not run.done.wait(self.grace):
            self._signal(run, hard=True)

    # ------------------------------------------------------------------ the runs

    def start_batch(self, proj: dict, name: str, environment: str | None = None, build: str | None = None) -> Run:
        """A planned batch (batches/<name>.yaml) through its module's skill in Claude Code headless."""
        with self.lock:
            busy = self.current()
            if busy:
                raise Refused(f"a run is in progress ({busy.label}): one run at a time per workspace")
            try:
                batch = workspace.batch(self.ws, name)
            except workspace.NotFound as e:
                raise NotFound(str(e)) from None
            except workspace.Refused as e:
                raise Refused(str(e)) from None
            module = batch.get("module")
            if module not in MODULES:
                raise Refused(f"batches/{name}.yaml has no `module:` of the list {list(MODULES)}: plan it in the "
                              "interface, or add the key")
            if module not in (proj.get("modules") or []):
                raise Refused(f"`{module}` is not in `modules:` of project.yaml (methodology 4.1)")
            claude = self.claude()
            if not claude:
                raise Refused("Claude Code was not found on this computer (the `claude` executable, or --claude PATH)")
            environment, build = (environment or "").strip() or None, (build or "").strip() or None
            envs = proj.get("environments") or {}
            if environment and environment not in envs:
                raise Refused(f"environment {environment!r} is not a key of environments in project.yaml: {list(envs)}")
            if proj.get("tracker") in ("jira", "azure-devops") and not (environment and build):
                raise Refused(f"with the {proj.get('tracker')} tracker the run block is required: give the "
                              "environment and the build (methodology 7.1)")
            lib = intake.load_library(self.ws, proj)
            if lib["error"]:
                raise Refused(f"the cases' text cannot be read: {lib['error']}")
            by_id = {c["id"]: c for c in lib["cases"]}
            missing = [c for c in batch["cases"] if c not in by_id]
            if missing:
                raise Refused(f"{', '.join(missing)}: not in the library; a case is added first (docs/qa-ui.md "
                              "section 7)")
            try:
                workspace.create_run_log(self.ws, proj, name, environment, build)
            except workspace.Refused as e:
                raise Refused(str(e)) from None
            ws, qa = posix(self.ws), posix(self.qa)
            prompt = template("batch.md", {
                "skill": SKILLS[module], "module": module, "workspace": ws, "qa": qa,
                "run_log": run_log.batch_id(name), "cases": " ".join(batch["cases"]),
                "max_turns": str(self.max_turns),
                "case_text": "\n\n".join(case_block(by_id[c]) for c in batch["cases"])})
            run_id = self._new_id("batch")
            run = Run(run_id, "batch", f"{name} ({module}, {len(batch['cases'])} case(s))",
                      batch_command(claude, module, self.ws, qa, self.max_turns),
                      os.path.join(self.ws, ".qa-ui", "runs", run_id + ".log"), batch=run_log.batch_id(name))
            return self._spawn(run, self.ws, prompt, render=True)

    def start_audit(self, proj: dict, kind: str) -> Run:
        """The automated accessibility audit (a11y) or the automated site audit (site): docs/qa-ui.md 8.2."""
        with self.lock:
            busy = self.current()
            if busy:
                raise Refused(f"a run is in progress ({busy.label}): one run at a time per workspace")
            run_id = self._new_id(f"audit-{kind}")
            log = os.path.join(self.ws, ".qa-ui", "runs", run_id + ".log")
            if kind == "a11y":
                if "accessibility" not in (proj.get("modules") or []):
                    raise Refused("`accessibility` is not in `modules:` of project.yaml (methodology 4.1)")
                n = 0
                while True:
                    n += 1
                    report = f"runs/a11y_{datetime.now():%Y%m%d}_{n:02d}.json"
                    if not os.path.exists(os.path.join(self.ws, *report.split("/"))):
                        break
                run = Run(run_id, "audit-a11y", "Automated accessibility audit",
                          [sys.executable, os.path.join(self.qa, "scripts", "audit_a11y.py"), self.ws, "--output",
                           report], log, report=report)
                return self._spawn(run, self.qa, None, render=False)
            if kind == "site":
                key, reason = find_test_environment(proj)
                if not key:
                    raise Refused(reason)
                exe = shutil.which("qa-audit")
                if not exe:
                    raise Refused("the qa-audit command was not found: pip install -e . (from the root of "
                                  "qa-assistant), then qa-audit doctor")
                run = Run(run_id, "audit-site", f"Automated site audit ({key})",
                          [exe, str(proj["environments"][key]), "--type", SITE_AUDIT_TYPES], log, report="qa-output/")
                return self._spawn(run, self.ws, None, render=False)
            raise Refused(f"unknown audit {kind!r}: a11y or site")

    def stop(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFound(f"no run {run_id}")
        self._stop(run, "stopped by the user")
        return run.public()

    def get(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFound(f"no run {run_id}")
        return run.public()

    def listing(self) -> list[dict]:
        return [dict(r.public(), tail=[]) for r in sorted(self.runs.values(), key=lambda r: r.started, reverse=True)]

    def shutdown(self) -> None:
        for run in list(self.runs.values()):
            self._stop(run, "the interface was closed")

    # ------------------------------------------------------------------ one-shot commands

    def _once(self, cmd: list[str], cwd: str, timeout: float, stdin_text: str | None = None) -> tuple[int, str]:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, input=stdin_text, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"no answer in {int(timeout)} s"
        except OSError as e:
            return 127, f"{os.path.basename(cmd[0])} could not be started: {e}"
        return r.returncode, self.redactor().text((r.stdout + r.stderr).strip())

    def ask_claude(self, cases: list[dict], notes: list[str], proposals: bool = False) -> dict:
        """Claude's reading of the cases (docs/qa-ui.md 5.3): {"ok": True, assignments, gaps, proposals}, or
        {"ok": False, "text"} when the answer is not the expected JSON (it then changes nothing)."""
        claude = self.claude()
        if not claude:
            raise Refused("Claude Code was not found on this computer (the `claude` executable, or --claude PATH)")
        if not cases and not notes:
            raise Refused("there is nothing to read: no case and no note")
        given = [{k: c.get(k) or None for k in ("id", "title", "preconditions", "steps", "expected", "role",
                                                 "platform", "area")} for c in cases]
        prompt = template("ask.md", {
            "notes": "\n".join(f"- {n}" for n in notes) or "(none)",
            "cases": json.dumps(given, ensure_ascii=False, indent=1) if given else "(none)",
            "proposals_rule": ("Propose new cases only where the cases given clearly leave a behaviour of the notes "
                               "or of the cases untested; at most 10." if proposals else
                               'The user did not ask for new cases: "proposals" is an empty list.')})
        code, out = self._once([claude, "-p", "--max-turns", "2", "--permission-mode", "dontAsk", "--output-format",
                                "json", "--disallowedTools", "WebFetch,WebSearch"], self.ws, ASK_TIMEOUT, prompt)
        if code != 0:
            return {"ok": False, "text": out or f"Claude Code ended with exit code {code}", "code": code}
        parsed = parse_reading(out, {c["id"] for c in cases}, proposals)
        return parsed if parsed else {"ok": False, "text": out[:4000], "code": 0}

    def new_cases(self, proj: dict, cases: list[dict] | None = None, confirm: str | None = None) -> dict:
        """New cases through the project's adapter (docs/qa-ui.md 7). Without `confirm`: writes the cases file and
        runs the dry run. With `confirm` (the file the dry run returned): the real run."""
        tracker = proj.get("tracker")
        if tracker not in ("excel", "jira", "azure-devops"):
            raise Refused(f"tracker {tracker!r}: no adapter to add cases")
        folder = ["cases"] if tracker == "excel" else ["tracker", "export"]
        if confirm is not None:
            if confirm != self.pending_cases or not os.path.isfile(os.path.join(self.ws, *confirm.split("/"))):
                raise Refused("there is no dry run of that file to confirm: run the dry run again")
            rel = confirm
        else:
            if not cases:
                raise Refused("no case to add")
            self.discard_cases()
            n = 0
            while True:
                n += 1
                rel = "/".join([*folder, f"cases_{datetime.now():%Y%m%d}_{n:02d}.json"])
                if not os.path.exists(os.path.join(self.ws, *rel.split("/"))):
                    break
            path = os.path.join(self.ws, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"cases": [model_case(c) for c in cases]}, ensure_ascii=False, indent=1) + "\n")
        path = os.path.join(self.ws, *rel.split("/"))
        if tracker == "excel":
            cmd = [sys.executable, "-m", "adapters.excel.add_cases", self.ws, path]
            cmd += [] if confirm is not None else ["--dry-run"]
            meanings, ok_dry = EXCEL_ADD, (0,)
        else:
            cmd = [sys.executable, "-m", "adapters.tracker", self.ws, "export-cases", path]
            cmd += ["--yes"] if confirm is not None else []
            meanings, ok_dry = TRACKER, (1,)
        code, out = self._once(cmd, self.qa, ADAPTER_TIMEOUT)
        if confirm is not None:
            self.pending_cases = None
            return {"file": rel, "dry_run": False, "code": code, "meaning": meanings.get(code, f"exit code {code}"),
                    "output": out, "done": code == 0}
        can = code in ok_dry
        if can:
            self.pending_cases = rel
        else:
            os.remove(path)               # a refused file is never left behind: the next attempt is a new file
        return {"file": rel if can else None, "dry_run": True, "code": code, "output": out, "can_confirm": can,
                "meaning": ("the dry run found no problem: nothing was written yet" if can and tracker == "excel"
                            else meanings.get(code, f"exit code {code}"))}

    def discard_cases(self) -> None:
        """Removes the cases file of a dry run that was never confirmed."""
        if self.pending_cases:
            path = os.path.join(self.ws, *self.pending_cases.split("/"))
            if os.path.isfile(path):
                os.remove(path)
            self.pending_cases = None

    def apply(self, proj: dict, name: str, confirm: bool = False) -> dict:
        """Applies a run log through the project's adapter (docs/qa-ui.md 10). Without `confirm`: the check, and
        for Jira / Azure DevOps the adapter's dry run. With `confirm`: the real run."""
        check = workspace.check_apply(self.ws, proj, name)
        if not check["ready"]:
            return {**check, "dry_run": not confirm, "done": False, "output": "", "code": None, "meaning": ""}
        path = run_log.run_log_path(self.ws, name)
        tracker = proj.get("tracker")
        if tracker == "excel":
            if not confirm:
                return {**check, "dry_run": True, "done": False, "output": "", "code": None,
                        "meaning": "ready: nothing was written yet"}
            code, out = self._once([sys.executable, "-m", "adapters.excel.apply", self.ws, path], self.qa,
                                   ADAPTER_TIMEOUT)
            return {**check, "dry_run": False, "done": code == 0, "output": out, "code": code,
                    "meaning": EXCEL_APPLY.get(code, f"exit code {code}")}
        cmd = [sys.executable, "-m", "adapters.tracker", self.ws, "apply", path] + (["--yes"] if confirm else [])
        code, out = self._once(cmd, self.qa, ADAPTER_TIMEOUT)
        if not confirm:
            return {**check, "ready": code == 1, "dry_run": True, "done": False, "output": out, "code": code,
                    "meaning": TRACKER.get(code, f"exit code {code}")}
        return {**check, "dry_run": False, "done": code == 0, "output": out, "code": code,
                "meaning": TRACKER.get(code, f"exit code {code}")}


def model_case(case: dict) -> dict:
    """A case with the fields of the model a new-cases file has (technique-excel.md 4.1, technique-jira-xray.md 7):
    the steps as [{action, data, expected}], one per line of the case's steps."""
    steps = []
    for line in str(case.get("steps") or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", line).strip()
        if line:
            steps.append({"action": line, "data": None, "expected": None})
    out = {"id": str(case.get("id") or "").strip(), "title": str(case.get("title") or "").strip()}
    for field in ("area", "preconditions", "expected", "role", "platform", "priority", "technique", "module"):
        value = str(case.get(field) or "").strip()
        out[field] = value or None
    out["steps"] = steps or None
    out["origin"] = str(case.get("origin") or "").strip() or None
    return out


def parse_reading(output: str, ids: set[str], proposals: bool) -> dict | None:
    """Claude's answer read strictly. `output` is what `claude -p --output-format json` printed ({"result": text})
    or the text itself; None when it is not the expected JSON object."""
    text = output.strip()
    try:
        outer = json.loads(text)
        if isinstance(outer, dict) and isinstance(outer.get("result"), str):
            text = outer["result"].strip()
    except ValueError:
        pass
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("assignments"), list):
        return None
    assignments = {}
    for a in doc["assignments"]:
        if isinstance(a, dict) and a.get("id") in ids and a.get("module") in MODULES and a["id"] not in assignments:
            assignments[a["id"]] = {"module": a["module"], "why": str(a.get("why") or "").strip()}
    gaps = [{"id": g.get("id") if g.get("id") in ids else None, "question": str(g["question"]).strip()}
            for g in doc.get("gaps") or [] if isinstance(g, dict) and str(g.get("question") or "").strip()]
    new = []
    if proposals:
        for n, p in enumerate(doc.get("proposals") or [], 1):
            if not isinstance(p, dict) or not str(p.get("title") or "").strip():
                continue
            case = intake.new_case({**{k: p.get(k) for k in intake.FIELDS}, "id": f"NEW-{n}"}, "claude")
            if case["module"] not in MODULES:
                case["module"] = ""
            case.update(in_library=False, why=str(p.get("why") or "").strip())
            case["flags"].append("not-in-library")
            new.append(case)
    return {"ok": True, "assignments": assignments, "gaps": gaps, "proposals": new}
