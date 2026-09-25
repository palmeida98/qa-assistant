"""report.md: summary, findings and manual checks in Markdown.

Audited text is escaped (no raw HTML reaches a Markdown viewer) and code is fenced with a fence longer than any
backtick run inside it."""

from __future__ import annotations

import re

from ..core.findings import SEVERITIES

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#])")


def esc(text) -> str:
    s = "" if text is None else str(text)
    return _MD_SPECIAL.sub(r"\\\1", s).replace("\r", " ").replace("\n", " ")


def fence(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", code or "")), default=0)
    f = "`" * max(3, longest + 1)
    return f"{f}text\n{code}\n{f}"


def write(result, path: str) -> None:
    s, lines = result.summary, []
    add = lines.append
    add("# QA audit")
    add("")
    add(f"- **Target:** {esc(result.target)}")
    add(f"- **Date:** {esc(result.started_at)} ({result.duration_s} s)")
    b = result.browser
    add(f"- **Browser:** {esc(b['engine'])} {esc(b['version'])}")
    add(f"- **Pages audited:** {sum(1 for p in result.pages if p.audited)} of {result.crawl['pages']} visited")
    add("")
    for m in result.modules:
        if m.get("disclaimer"):
            add(f"> **{esc(m['title'])}:** {esc(m['disclaimer'])}")
            add("")
    add("## Summary")
    add("")
    add("| Passed | Problems | Warnings | Needs human |")
    add("|---:|---:|---:|---:|")
    st = s["by_status"]
    add(f"| {s['passed']} | {st['problem']} | {st['warning']} | {st['needs-human']} |")
    add("")
    add("| " + " | ".join(x.capitalize() for x in SEVERITIES) + " |")
    add("|" + "---:|" * len(SEVERITIES))
    add("| " + " | ".join(str(s["by_severity"][x]) for x in SEVERITIES) + " |")
    add("")
    add("## Findings")
    add("")
    if not result.findings:
        add("No findings.")
        add("")
    else:
        add("| ID | Status | Severity | Title | URL |")
        add("|---|---|---|---|---|")
        for f in result.findings:
            add(f"| {esc(f.id)} | {esc(f.status)} | {esc(f.severity)} | {esc(f.title)} | {esc(f.url)} |")
        add("")
        for f in result.findings:
            add(f"### {esc(f.id)} · {esc(f.title)}")
            add("")
            add(f"- **Module / check:** {esc(f.module)} / {esc(f.check)}")
            add(f"- **Status / severity / confidence:** {esc(f.status)} / {esc(f.severity)} / {esc(f.confidence)}")
            add(f"- **URL:** {esc(f.url)}")
            if f.element.get("selector"):
                add(f"- **Element:** {esc(f.element['selector'])}")
            if f.description:
                add(f"- **Description:** {esc(f.description)}")
            if f.expected:
                add(f"- **Expected:** {esc(f.expected)}")
            if f.actual:
                add(f"- **Actual:** {esc(f.actual)}")
            if f.recommendation:
                add(f"- **Recommendation:** {esc(f.recommendation)}")
            if f.steps:
                add("- **Steps to reproduce:**")
                for i, step in enumerate(f.steps, 1):
                    add(f"  {i}. {esc(step)}")
            if f.references:
                add("- **References:** " + ", ".join(esc(r) for r in f.references))
            if f.element.get("snippet"):
                add("")
                add(fence(f.element["snippet"]))
            if f.evidence:
                add("")
                add("Evidence:")
                for e in f.evidence:
                    add(f"- {esc(e.kind)}: {esc(e.path or e.value)}")
            add("")
    metrics = getattr(result, "metrics", None) or []
    if metrics:
        from . import metric_value
        add("## Metrics (observed · threshold · result)")
        add("")
        add("| Page | Metric | Observed | Threshold | Result |")
        add("|---|---|---:|---|---|")
        for m in metrics:
            add(f"| {esc(m['url'])} | {esc(m['metric'])} | {esc(metric_value(m))} | {esc(m['threshold'])} | "
                f"{esc(m['result'])} |")
        add("")
    if result.manual_checks:
        add("## Manual checks (not automated — require a person)")
        add("")
        for module, checks in result.manual_checks.items():
            add(f"**{esc(module)}**")
            add("")
            for c in checks:
                add(f"- [ ] {esc(c['title'])} — {esc(c.get('how', ''))}")
            add("")
    if result.errors or result.notes:
        add("## Notes")
        add("")
        for n in result.notes:
            add(f"- {esc(n)}")
        for e in result.errors:
            add(f"- Error in {esc(e['module'])} on {esc(e['url'] or 'finalize')}: {esc(e['message'])}")
        add("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
