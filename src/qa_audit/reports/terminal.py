"""Terminal output: banner, progress and summary (docs/qa-audit.md §2)."""

from __future__ import annotations

import os

from ..core.console import symbols
from ..core.findings import SEVERITIES

AUTHORIZATION = "Only audit sites you own or are authorized to test."
SEV_STYLE = {"critical": ("red", "bold"), "high": ("red",), "medium": ("yellow",), "low": ("cyan",), "info": ("dim",)}


class Progress:
    def __init__(self, console, steps: list[str]):
        self.c, self.steps = console, steps
        self.sym = symbols(console.stream)
        self.width = max(len(s) for s in steps) + 2

    def line(self):
        self.c.print(self.sym["line"] * 40, "dim")

    def banner(self, target: str, cfg: dict, modules: list):
        b = cfg["browser"]
        self.c.print("QA Assistant", "bold")
        self.line()
        self.c.print(f"Target:  {target}")
        view = f"device {b['device']}" if b.get("device") else f"{b['viewport']['width']}x{b['viewport']['height']}"
        mode = "headless" if b.get("headless", True) else "headed"
        self.c.print(f"Browser: {b['engine']} ({mode}, {view})")
        self.c.print(f"Modules: {', '.join(m.title for m in modules)}")
        c = cfg["crawl"]
        self.c.print(f"Limits:  {c['max_pages']} pages, depth {c['max_depth']}, {c['delay_ms']} ms between pages", "dim")
        self.c.print(AUTHORIZATION, "dim")
        e = cfg["evidence"]
        if e.get("video") or str(e.get("trace")) != "off":
            self.c.print(f"{self.sym['warn']} video/trace are on: they store page content, cookies and tokens "
                         "and cannot be redacted", "yellow")
        self.c.print()

    def step(self, i: int, ok: bool, detail: str = ""):
        mark = self.c.style(self.sym["ok"], "green") if ok else self.c.style(self.sym["fail"], "red")
        name = self.steps[i - 1].ljust(self.width)
        self.c.print(f"[{i}/{len(self.steps)}] {name}{mark}" + (f"  {detail}" if detail else ""))

    def page(self, url: str, n: int):
        self.c.print(f"      {self.sym['dot']} {n:>3}  {url}", "dim")


def summary(console, result, report_paths: dict, cleaned: bool, fail_on: str) -> None:
    sym = symbols(console.stream)
    s = result.summary
    st = s["by_status"]
    console.print(sym["line"] * 40, "dim")
    console.print()
    console.print("Results", "bold")
    console.print(f"  Passed checks  {s['passed']:>5}")
    console.print(f"  Problems       {st['problem']:>5}", *(("red",) if st["problem"] else ()))
    console.print(f"  Warnings       {st['warning']:>5}", *(("yellow",) if st["warning"] else ()))
    console.print(f"  Needs human    {st['needs-human']:>5}")
    console.print()
    for sev in SEVERITIES:
        n = s["by_severity"][sev]
        console.print(f"  {sev.capitalize():<13}{n:>5}", *(SEV_STYLE[sev] if n else ()))
    for m in result.modules:
        if m.get("disclaimer"):
            console.print()
            console.print(f"{m['title']}: {m['disclaimer']}", "dim")
    if result.errors:
        console.print()
        console.print(f"{sym['warn']} {len(result.errors)} module error(s); see logs/run.log", "yellow")
    console.print()
    if cleaned:
        console.print("Run folder deleted (--cleanup).", "dim")
    elif report_paths:
        main = report_paths.get("html") or next(iter(report_paths.values()))
        console.print("Report:")
        console.print(f"  {os.path.relpath(main) if _same_drive(main) else main}", "cyan")
    if fail_on != "none":
        console.print(f"Exit status fails on: {fail_on} and above", "dim")


def _same_drive(path: str) -> bool:
    try:
        os.path.relpath(path)
        return True
    except ValueError:      # Windows: different drive
        return False
