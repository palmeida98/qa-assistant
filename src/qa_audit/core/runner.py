"""Orchestration (docs/qa-audit.md §3): discovery -> module hooks on every page -> finalize -> reports."""

from __future__ import annotations

import datetime as dt
import logging
import os
import traceback

from .. import __version__
from .config import validate_url
from .evidence import RunFolder
from .findings import CheckTally, Evidence, Finding, assign_ids, sort_key, summarize
from .privacy import Redactor


class RunContext:
    """What a module sees: configuration, run folder and the reporting calls."""

    def __init__(self, cfg: dict, run: RunFolder, log: logging.Logger):
        self.cfg, self.run, self.log = cfg, run, log
        self.findings: list[Finding] = []
        self.tally = CheckTally()
        self.errors: list[dict] = []
        self.notes: list[str] = []
        self.session = None      # browser session (set by the runner)
        self.pages = []          # PageInfo of the pages visited so far
        self._state: dict[str, dict] = {}

    def state(self, module: str) -> dict:
        """Per-run scratch space of a module (module instances are shared between runs)."""
        return self._state.setdefault(module, {})

    def module_config(self, name: str) -> dict:
        return self.cfg["modules"].get(name) or {}

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def passed(self, module: str, n: int = 1) -> None:
        self.tally.add(module, n)

    def screenshot(self, target, label: str, full_page: bool = False, timeout_ms: int = 5000) -> Evidence | None:
        """Screenshot of a page or a locator into screenshots/; None if screenshots are off or it fails."""
        if self.cfg["evidence"]["screenshots"] == "off":
            return None
        path, rel = self.run.new_file("screenshots", label, "png")
        try:
            if full_page:
                target.screenshot(path=path, full_page=True, timeout=timeout_ms)
            else:
                target.screenshot(path=path, timeout=timeout_ms)
        except Exception as e:  # noqa: BLE001 - evidence is best effort; the finding keeps its text evidence
            self.log.warning("screenshot %s failed: %s", label, e)
            return None
        return Evidence(kind="screenshot", path=rel, label=label)


class RunResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def to_dict(self) -> dict:
        return {
            "tool": {"name": "qa-audit", "version": __version__},
            "target": self.target,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "browser": self.browser,
            "modules": self.modules,
            "summary": self.summary,
            "crawl": self.crawl,
            "pages": [p.to_dict() for p in self.pages],
            "findings": [f.to_dict() for f in self.findings],
            "manual_checks": self.manual_checks,
            "errors": self.errors,
            "notes": self.notes,
            "config": self.config,
            "run_folder": self.run_folder,
        }


def _logger(run: RunFolder) -> logging.Logger:
    log = logging.getLogger(f"qa_audit.run.{id(run)}")
    log.setLevel(logging.INFO)
    log.propagate = False
    h = logging.FileHandler(os.path.join(run.sub("logs"), "run.log"), encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    return log


def run(target: str, cfg: dict, modules: list, progress=None, session_factory=None) -> RunResult:
    """Audit target. progress(event, **data) receives: page, module_done, discovery_done.

    session_factory(cfg, run, target) -> Session is injectable for tests (e.g. a recording proxy)."""
    from ..browser.discovery import Crawler
    from ..browser.session import Session

    progress = progress or (lambda event, **data: None)
    target = validate_url(target)
    started = dt.datetime.now()
    run_folder = RunFolder(cfg["output"]["directory"], now=started)
    log = _logger(run_folder)
    log.info("qa-audit %s target %s modules %s", __version__, target, [m.name for m in modules])
    ctx = RunContext(cfg, run_folder, log)
    shots = cfg["evidence"]["screenshots"]
    factory = session_factory or (lambda c, r, t: Session(c, r, t))

    def on_page(page, info):
        ctx.pages.append(info)
        progress("page", url=info.url, n=len(ctx.pages))
        before = len(ctx.findings)
        for m in modules:
            try:
                m.on_page(page, info, ctx)
            except Exception as e:  # noqa: BLE001 - one module failing on one page never stops the audit
                ctx.errors.append({"module": m.name, "url": info.url, "message": f"{type(e).__name__}: {e}"})
                log.error("module %s failed on %s:\n%s", m.name, info.url, traceback.format_exc())
        new_problems = [f for f in ctx.findings[before:] if f.status in ("problem", "warning")]
        if shots == "all" or (shots == "on-problem" and new_problems):
            ev = ctx.screenshot(page, f"page {info.url}", full_page=True)
            if ev:
                for f in new_problems:
                    f.evidence.append(ev)

    session = factory(cfg, run_folder, target)
    try:
        with session:
            ctx.session = session
            crawl = Crawler(session, cfg, target, on_page=on_page, log=log.info, redactor=Redactor()).run()
            progress("discovery_done", pages=len(crawl.pages), skipped=len(crawl.skipped))
            module_rows = []
            for m in modules:
                try:
                    m.finalize(ctx)
                except Exception as e:  # noqa: BLE001
                    ctx.errors.append({"module": m.name, "url": "", "message": f"{type(e).__name__}: {e}"})
                    log.error("module %s finalize failed:\n%s", m.name, traceback.format_exc())
                mine = [f for f in ctx.findings if f.module == m.name]
                module_rows.append({"name": m.name, "title": m.title, "passed": ctx.tally.passed.get(m.name, 0),
                                    "findings": len(mine), "disclaimer": m.disclaimer,
                                    "errors": sum(1 for e in ctx.errors if e["module"] == m.name)})
                progress("module_done", module=m, findings=len(mine))
            has_problems = any(f.status == "problem" for f in ctx.findings)
            trace_mode = str(cfg["evidence"]["trace"])
            trace = session.close(keep_trace=trace_mode == "on" or (trace_mode == "retain-on-problem" and has_problems))
            if cfg["browser"].get("ignore_https_errors"):
                ctx.notes.append("--ignore-https-errors: certificate errors were ignored; certificate trust was not "
                                 "judged")
            if session.blocked:
                ctx.notes.append(f"{len(session.blocked)} third-party requests were blocked (--block-third-party)")
            browser = {"engine": session.engine, "version": session.version, "user_agent": session.user_agent,
                       "headless": cfg["browser"]["headless"], "viewport": cfg["browser"]["viewport"],
                       "device": cfg["browser"]["device"]}
            audited_target = session.target
    finally:
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)

    findings = sorted(ctx.findings, key=sort_key)
    assign_ids(findings, {m.name: m.id_prefix for m in modules})
    finished = dt.datetime.now()
    notes = list(ctx.notes)
    if trace:
        notes.append(f"trace kept: {run_folder.rel(trace)} (contains page content and cookies)")
    if audited_target != target:
        notes.append(f"the start URL redirected; audited origin: {audited_target}")
    return RunResult(
        target=target,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=finished.isoformat(timespec="seconds"),
        duration_s=round((finished - started).total_seconds(), 1),
        browser=browser,
        modules=module_rows,
        summary=summarize(findings, ctx.tally),
        crawl={"pages": len(crawl.pages), "skipped": crawl.skipped, "not_visited": crawl.not_visited,
               "robots": crawl.robots},
        pages=crawl.pages,
        findings=findings,
        manual_checks={m.name: list(m.manual_checks) for m in modules if m.manual_checks},
        errors=ctx.errors,
        notes=notes,
        config=cfg,
        run_folder=run_folder.path,
        run=run_folder,
    )
