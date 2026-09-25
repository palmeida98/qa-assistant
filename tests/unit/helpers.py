"""A fake run result for report tests (no browser)."""

from __future__ import annotations

import copy

from qa_audit.browser.discovery import PageInfo
from qa_audit.core import config
from qa_audit.core.findings import (
    CheckTally,
    Evidence,
    Finding,
    assign_ids,
    sort_key,
    summarize,
)
from qa_audit.core.runner import RunResult

HOSTILE = '</pre><script>alert("x")</script><img src=x onerror=alert(1)>'


def fake_result(run_folder: str, findings: list[Finding] | None = None) -> RunResult:
    if findings is None:
        findings = [
            Finding(module="accessibility", check="axe/image-alt", title="Images must have alternative text",
                    status="problem", severity="high", url="https://shop.example/a", element={
                        "selector": "main > img", "snippet": HOSTILE},
                    evidence=[Evidence(kind="html", value=HOSTILE, label="main > img"),
                              Evidence(kind="screenshot", path="screenshots/0001_img.png")],
                    references=["WCAG 1.1.1", "javascript:alert(1)"], steps=["Open the page"]),
            Finding(module="accessibility", check="reflow", title="=HYPERLINK(\"http://evil.example\")",
                    status="warning", severity="medium", url="https://shop.example/b",
                    evidence=[Evidence(kind="metric", value="scrollWidth 900px > 320px")]),
            Finding(module="accessibility", check="focus-visible", title="Focus may not be visible",
                    status="needs-human", severity="medium", confidence="low", url="https://shop.example/c"),
        ]
    findings = sorted(findings, key=sort_key)
    assign_ids(findings, {"accessibility": "ACC"})
    tally = CheckTally()
    tally.add("accessibility", 10)
    page = PageInfo(url="https://shop.example/", depth=0, final_url="https://shop.example/", status=200,
                    title=HOSTILE, audited=True)
    return RunResult(
        target="https://shop.example/", started_at="2026-09-25T09:00:00", finished_at="2026-09-25T09:00:05",
        duration_s=5.0, browser={"engine": "chromium", "version": "1.0", "user_agent": "ua", "headless": True,
                                 "viewport": {"width": 1440, "height": 900}, "device": None},
        modules=[{"name": "accessibility", "title": "Accessibility", "passed": 10, "findings": len(findings),
                  "disclaimer": "Not a certification.", "errors": 0}],
        summary=summarize(findings, tally),
        crawl={"pages": 1, "skipped": [{"url": "https://shop.example/logout", "reason": "excluded"}],
               "not_visited": 0, "robots": "respected"},
        pages=[page], findings=findings,
        metrics=[{"module": "performance", "url": "https://shop.example/", "metric": "ttfb", "value": 912.4,
                  "unit": "ms", "threshold": "good <= 800, poor > 1800", "result": "needs-improvement",
                  "source": "web.dev"},
                 {"module": "performance", "url": "https://shop.example/", "metric": "cls", "value": None,
                  "unit": "", "threshold": "", "result": "n/a", "source": "=cmd"}],
        manual_checks={"accessibility": [{"id": "sr", "title": "Screen reader <b>walk</b>", "how": "NVDA"}]},
        errors=[], notes=[], config=copy.deepcopy(config.DEFAULTS), run_folder=run_folder, run=None)
