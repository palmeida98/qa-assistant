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
