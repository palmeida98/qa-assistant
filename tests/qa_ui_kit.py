"""Shared by the tests of qa-ui (docs/qa-ui.md section 12): a fictional workspace in a temporary folder (the Excel
adapter's golden fixture as its workbook) and fake executables, so no test ever starts a real Claude session, a
browser or a site. All data is the fictional Demo Shop."""

import copy
import os
import stat
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    EXCEL = yaml.safe_load(_f)

PROJECT = {
    "project": "demo-shop", "company": "demo",
    "environments": {"test": "https://shop.example/"},
    "roles": ["customer", "admin"],
    "tracker": "excel", "bug_format": "BUG-{n}", "resolved_bugs": "remove", "evidence": "text",
    "modules": ["functional-web", "api"],
    "run_log": "runs/",
    "api": {"base_url": "https://shop.example/api", "auth": "bearer",
            "credentials": {"customer": {"user_var": "SHOP_USER", "pass_var": "SHOP_PASSWORD"}}},
}
# fictional values of the .env the test writes (a .env is never committed, not even a fictional one)
ENV = dict(zip(("SHOP_USER", "SHOP_PASSWORD"), ("customer@shop.example", "fictional-value-555")))
SECRET = ENV["SHOP_PASSWORD"]


def workspace(**changes) -> tempfile.TemporaryDirectory:
    """A temporary workspace: project.yaml (PROJECT with `changes`), the fixture workbook and a .env."""
    tmp = tempfile.TemporaryDirectory()
    proj = copy.deepcopy(PROJECT)
    proj["excel"] = copy.deepcopy(EXCEL)
    proj.update(changes)
    build(os.path.join(tmp.name, EXCEL["file"]))
    write_project(tmp.name, proj)
    with open(os.path.join(tmp.name, ".env"), "w", encoding="utf-8") as f:
        f.write("".join(f"{k}={v}\n" for k, v in ENV.items()))
    return tmp


def write_project(ws: str, proj: dict) -> None:
    with open(os.path.join(ws, "project.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(proj, f, allow_unicode=True, sort_keys=False)


def executable(folder: str, name: str, body: str) -> str:
    """A fake command: a Python script run by this interpreter (a .cmd wrapper on Windows)."""
    script = os.path.join(folder, name + ".py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(body)
    if os.name == "nt":
        path = os.path.join(folder, name + ".cmd")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "{script}" %*\n')
        return path
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


# A fake Claude Code (docs/qa-ui.md section 12): it records how it was called, and behaves by QA_FAKE_CLAUDE.
# A batch run (stream-json) appends results to the run log named in the prompt, as the real agent would through
# scripts/run_log.py; an ask (json) prints an answer. No network, no model.
FAKE_CLAUDE = r'''
import json, os, re, signal, sys, time

args = sys.argv[1:]
prompt = sys.stdin.read()
mode = os.environ.get("QA_FAKE_CLAUDE", "ok")
record = os.environ.get("QA_FAKE_RECORD")
if record:
    with open(record, "w", encoding="utf-8") as f:
        json.dump({"args": args, "prompt": prompt, "cwd": os.getcwd()}, f)


def emit(event):
    print(json.dumps(event), flush=True)


def say(text):
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def tool(name, **given):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": given}]}})


if "stream-json" not in args:                        # asking for a reading of the cases
    if mode == "ask-bad":
        print(json.dumps({"type": "result", "result": "I think these are mostly web tests."}))
    elif mode == "ask-fail":
        print("not logged in", file=sys.stderr)
        sys.exit(1)
    else:
        ids = re.findall(r'"id": "([^"]+)"', prompt.rpartition("\nCases:\n")[2])
        answer = {"assignments": [{"id": i, "module": "api" if i.startswith("API") else "functional-web",
                                   "why": "read by the fake"} for i in ids]
                  + [{"id": "INVENTED-1", "module": "api", "why": "an ID that was not given"},
                     {"id": ids[0] if ids else "X-1", "module": "smoke", "why": "not a module of the list"}],
                  "gaps": [{"id": ids[0] if ids else None, "question": "Which message is expected?"},
                           {"id": "INVENTED-1", "question": "A gap about nothing given"}, {"question": " "}],
                  "proposals": [{"id": "whatever", "title": "Pay with an expired card",
                                 "steps": "1. Add an item\n2. Pay", "expected": "The payment is refused",
                                 "module": "functional-web", "technique": "Equivalence partitioning",
                                 "why": "the refused path"}, {"title": " "}]}
        text = json.dumps(answer)
        print(json.dumps({"type": "result", "result": "```json\n" + text + "\n```" if mode == "ask-fenced" else text}))
    sys.exit(0)

emit({"type": "system", "subtype": "init"})
say("Reading the workspace. The password is fictional-value-555.")
if mode == "fail":
    print("a line that is not an event", flush=True)
    sys.exit(3)
if mode == "refused":                              # the batch's tools do not cover what the agent needs
    tool("Bash", command="adb devices")
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content":
          "Claude requested permissions to use Bash, but you haven't granted it yet."}]}})
    say("I could not run any case: the shell was refused.")
    emit({"type": "result", "subtype": "success", "result": "none run", "num_turns": 3, "duration_ms": 900,
          "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "adb devices"}}]})
    sys.exit(0)
if mode in ("slow", "stubborn"):
    if mode == "stubborn":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    tool("Bash", command="sleep 60")
    time.sleep(60)
    sys.exit(0)
name = re.search(r"Run log: runs/(\S+)\.json", prompt).group(1)
cases = re.search(r"Cases, in this order: (.*)", prompt).group(1).split()
path = os.path.join(os.getcwd(), "runs", name + ".json")
with open(path, encoding="utf-8") as f:
    doc = json.load(f)
for n, case in enumerate(cases):
    item = {"tc": case, "status": "Pass", "actual_result": "observed as expected", "ts": "2026-10-08T10:42:00"}
    if n == 1:
        item.update(status="Fail", actual_result="the expired coupon is accepted",
                    new_defect={"title": "Expired coupon accepted", "description": "The discount is applied",
                                "severity": "High", "priority": "P2", "area": "Shop", "proposed": True})
    if n == 2:
        item.update(status="Question", actual_result="which message is expected?")
    tool("Bash", command="python qa/scripts/run_log.py ws add " + name + " " + case + " " + item["status"])
    doc["results"].append(item)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": "added"}]}})
say("Batch finished.")
emit({"type": "stream_event", "event": {"type": "an event the interface does not know"}})
emit({"type": "result", "subtype": "success", "result": "Batch finished.", "num_turns": 7, "duration_ms": 4200})
'''


def fake_claude(folder: str) -> str:
    return executable(folder, "claude", FAKE_CLAUDE)
