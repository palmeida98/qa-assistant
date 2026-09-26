"""
Progress dashboard of a workspace (plan, task 5.1).

Reads the workbook through the adapter (read only, never saves) and context.md,
and builds a single dashboard.html in the workspace: progress per area and status,
open defects by severity, last checkpoint and next steps. Offline: no
JavaScript, CDNs nor external fonts.

The meaning of each case status comes from the `dashboard:` section of
project.yaml (pending | ok | fail | blocked | na); without it, the first status of
excel.cases.statuses counts as pending and the others are neutral.

Use (from the root of qa-assistant; skill /dashboard, plan 5.2):
    python scripts/build_dashboard.py <workspace> [--output dashboard.html] [--open]

Exits with 0 (built) or 2 (invalid configuration: nothing built). --open opens the
local file in the browser; the dashboard is never published (it has project data).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from common import read_project

from adapters.excel import ExcelAdapter

TYPES = ("pending", "ok", "fail", "blocked", "na")
# sections of context.md (methodology 12.2) shown in the dashboard, by heading prefix
CONTEXT_SECTIONS = (
    ("execution point", "Execution point"),
    ("pending work", "Next steps"),
    ("open questions", "Open questions"),
    ("known problems", "Known problems / blockers"),
)


# ---------------------------------------------------------------------------
# Collecting the data
# ---------------------------------------------------------------------------


def status_types(statuses: list[str], dashboard: dict | None) -> dict[str, str]:
    """status -> type. Validates the dashboard.states section against the workbook's statuses."""
    mapping = (dashboard or {}).get("states") or {}
    if mapping:
        outside = [e for e in mapping if e not in statuses]
        bad = [f"{e}: {t}" for e, t in mapping.items() if t not in TYPES]
        if outside or bad:
            raise ValueError(
                "invalid dashboard.states section: "
                + (f"statuses that do not exist in excel.cases.statuses: {outside}. " if outside else "")
                + (f"invalid types (valid: {', '.join(TYPES)}): {bad}" if bad else "")
            )
        return {e: mapping.get(e, "other") for e in statuses}
    return {e: ("pending" if i == 0 else "other") for i, e in enumerate(statuses)}


def check_not_run(types: dict[str, str], cfg_excel: dict) -> None:
    """The summary's % run (summary.not_run_status) and the dashboard's (pending
    status) must use the same "not run" (methodology 16.3)."""
    pend = (cfg_excel.get("summary") or {}).get("not_run_status")
    if pend and types.get(pend) != "pending":
        raise ValueError(
            f"excel.summary.not_run_status is {pend!r}, but in the dashboard that status is "
            f"{types.get(pend)!r}: fix the dashboard.states section so that both % run match"
        )
    others = [e for e, t in types.items() if t == "pending" and e != pend]
    if pend and others:
        raise ValueError(f"the dashboard treats {others} as pending, but the summary only counts {pend!r} as not run")


def collect_workbook(ad: ExcelAdapter, types: dict[str, str]) -> dict:
    """The dashboard's numbers from an adapter already loaded."""
    cfg = ad.cfg
    statuses = list(cfg["cases"]["statuses"])
    by_area = ad.progress_summary()
    extra = sorted({e for c in by_area.values() for e in c if e not in statuses}, key=str)
    order = statuses + extra  # statuses outside the list come last (the cross-check reports them)

    areas = []
    for area, cnt in by_area.items():
        total = sum(cnt.values())
        pend = sum(n for e, n in cnt.items() if types.get(e) == "pending")
        areas.append({"area": area, "total": total, "run": total - pend,
                      "by_status": {e: cnt.get(e, 0) for e in order if cnt.get(e, 0)}})
    total_status = {e: sum(a["by_status"].get(e, 0) for a in areas) for e in order}
    total = sum(a["total"] for a in areas)

    d = cfg["defects"]
    col = d["columns"]
    od = ad.open_defects()
    sev_col = col.get("severity")
    sev_count: dict[str, int] = {}
    for row in od["open"]:
        s = row.get(sev_col) if sev_col else None
        s = "(no severity)" if s in (None, "") else str(s)
        sev_count[s] = sev_count.get(s, 0) + 1
    # severities that are also priorities follow that order; the others, the order of their 1st occurrence
    # in the open list (already sorted by priority)
    prios, first = d["priorities"], list(sev_count)
    sev_order = sorted(sev_count, key=lambda s: (prios.index(s) if s in prios else len(prios), first.index(s)))

    title_col = (d.get("batch_fields") or {}).get("title")
    defects = [
        {"id": r.get(col["id"]), "title": r.get(title_col) if title_col else None,
         "severity": r.get(sev_col) if sev_col else None,
         "priority": r.get(col["priority"]) if col.get("priority") else None,
         "case": r.get(col["case"]) if col.get("case") else None,
         "status": r.get(col["status"])}
        for r in od["open"]
    ]
    return {
        "statuses": order,
        "types": {e: types.get(e, "other") for e in order},
        "cases": {"total": total, "run": sum(a["run"] for a in areas),
                  "by_status": {e: n for e, n in total_status.items() if n}, "by_area": areas},
        "defects": {"open": len(defects), "closed": od["closed"],
                    "by_severity": {s: sev_count[s] for s in sev_order}, "list": defects},
    }


def read_context(path: str) -> dict:
    """{"updated": str|None, "sections": [(title, markdown)]} of context.md."""
    if not os.path.isfile(path):
        return {"updated": None, "sections": []}
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    m = re.search(r"^Updated:\s*(.+)$", text, re.MULTILINE)
    blocks = {}
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = block.partition("\n")
        blocks[title.strip()] = body.strip()
    sections = []
    for prefix, name in CONTEXT_SECTIONS:
        for title, body in blocks.items():
            if title.lower().startswith(prefix):
                sections.append((name, body))
                break
    return {"updated": m.group(1).strip() if m else None, "sections": sections}


def collect(workspace: str) -> dict:
    proj = read_project(workspace)
    tracker = proj.get("tracker")
    if tracker != "excel":
        raise ValueError(f"the dashboard only supports tracker: excel for now (this project: {tracker!r})")
    ad = ExcelAdapter.from_project(workspace)
    ad.load()  # read only: the dashboard never saves the workbook
    types = status_types(list(ad.cfg["cases"]["statuses"]), proj.get("dashboard"))
    check_not_run(types, ad.cfg)
    data = collect_workbook(ad, types)
    data["project"] = proj.get("project") or os.path.basename(os.path.abspath(workspace))
    data["workbook"] = ad.cfg["file"]
    data["built"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["context"] = read_context(os.path.join(workspace, "context.md"))
    # the dashboard's totals always come from the direct count; the workbook's summary is only checked
    data["summary"] = ad.check_summary() if ad.cfg.get("summary") else None
    return data


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;
--pending:#d9d8d2;--ok:#2f7d4f;--fail:#c0392b;--blocked:#c98a14;--na:#6f7f95;
--o1:#4f6d9a;--o2:#8a5a9c;--o3:#3d8a8a;--o4:#9a6b4f;--code:#efeee9}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;
--line:#34332f;--pending:#4a4944;--ok:#4caf74;--fail:#e0624f;--blocked:#e0a53a;--na:#8c9bb0;
--o1:#7f9cc9;--o2:#b98ac9;--o3:#6cb8b8;--o4:#c99a7f;--code:#2b2a27}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
header h1{margin:0;font-size:1.6rem}
header p{margin:4px 0 0;color:var(--muted)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-top:18px}
h2{margin:0 0 12px;font-size:1.1rem}
h3{margin:14px 0 4px;font-size:.95rem}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:1.6rem;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:.85rem}
.bar{display:flex;height:22px;border-radius:5px;overflow:hidden;background:var(--pending)}
.bar i{display:block;height:100%}
.leg{display:flex;flex-wrap:wrap;gap:6px 16px;margin:10px 0 0;padding:0;list-style:none;font-size:.85rem}
.leg li::before{content:"";display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:var(--c)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.bar-cell{width:40%;min-width:120px}
.scroll{overflow-x:auto}
.empty{color:var(--muted)}
.alert{border-color:var(--fail);border-width:2px}
.alert h2{color:var(--fail)}
.alert ul{margin:6px 0;padding-left:20px}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.88em}
.ctx ul{margin:4px 0;padding-left:20px}
.ctx p{margin:4px 0}
footer{margin-top:18px;color:var(--muted);font-size:.8rem}
"""

OTHERS = ("--o1", "--o2", "--o3", "--o4")


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _colours(data: dict) -> dict[str, str]:
    colours, k = {}, 0
    for e in data["statuses"]:
        t = data["types"][e]
        if t in TYPES:
            colours[e] = f"var(--{t})"
        else:
            colours[e] = f"var({OTHERS[k % len(OTHERS)]})"
            k += 1
    return colours


def _bar(by_status: dict, total: int, colours: dict) -> str:
    if not total:
        return '<div class="bar"></div>'
    parts = "".join(
        f'<i style="width:{100 * n / total:.3f}%;background:{colours[e]}" title="{_e(e)}: {n}"></i>'
        for e, n in by_status.items()
    )
    return f'<div class="bar" role="img" aria-label="{_e(", ".join(f"{e}: {n}" for e, n in by_status.items()))}">{parts}</div>'


def _legend(by_status: dict, colours: dict) -> str:
    return '<ul class="leg">' + "".join(
        f'<li style="--c:{colours[e]}">{_e(e)} <b>{n}</b></li>' for e, n in by_status.items()
    ) + "</ul>"


def _inline(txt: str) -> str:
    t = html.escape(txt)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return re.sub(r"(?<![*\w])\*([^*]+)\*(?!\w)", r"<i>\1</i>", t)


def minimal_markdown(md: str) -> str:
    """Lists (with levels by indentation), paragraphs, `code`, **bold**, *italic*."""
    out, stack, paragraph = [], [], []

    def close_para():
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    for line in md.splitlines():
        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            close_para()
            level = len(m.group(1).expandtabs(2)) // 2
            while len(stack) > level + 1:
                out.append("</li></ul>")
                stack.pop()
            if len(stack) < level + 1:
                while len(stack) < level + 1:
                    out.append("<ul><li>")
                    stack.append(level)
            else:
                out.append("</li><li>")
            out.append(_inline(m.group(2)))
        elif line.strip():
            if stack:  # continuation of an item
                out.append(" " + _inline(line.strip()))
            else:
                paragraph.append(line.strip())
        else:
            close_para()
            while stack:
                out.append("</li></ul>")
                stack.pop()
    close_para()
    while stack:
        out.append("</li></ul>")
        stack.pop()
    return "".join(out)


def render(data: dict) -> str:
    colours = _colours(data)
    c, d, ctx = data["cases"], data["defects"], data["context"]
    pct = f"{100 * c['run'] / c['total']:.0f}%" if c["total"] else "–"

    kpis = (
        f'<div class="kpi"><b>{c["total"]}</b><span>cases</span></div>'
        f'<div class="kpi"><b>{c["run"]}</b><span>run ({pct})</span></div>'
        f'<div class="kpi"><b>{d["open"]}</b><span>open defects</span></div>'
        f'<div class="kpi"><b>{d["closed"]}</b><span>closed defects</span></div>'
    )

    area_rows = "".join(
        f'<tr><td>{_e(a["area"])}</td><td class="n">{a["run"]}/{a["total"]}</td>'
        f'<td class="bar-cell">{_bar(a["by_status"], a["total"], colours)}</td>'
        f'<td>{", ".join(f"{_e(e)} {n}" for e, n in a["by_status"].items())}</td></tr>'
        for a in c["by_area"]
    )

    if d["open"]:
        sev = "".join(
            f'<tr><td>{_e(s)}</td><td class="n">{n}</td></tr>' for s, n in d["by_severity"].items()
        )
        rows = "".join(
            f'<tr><td>{_e(x["id"])}</td><td>{_e(x["title"])}</td><td>{_e(x["severity"])}</td>'
            f'<td>{_e(x["priority"])}</td><td>{_e(x["case"])}</td><td>{_e(x["status"])}</td></tr>'
            for x in d["list"]
        )
        defects_block = (
            f'<table><thead><tr><th>Severity</th><th class="n">Open</th></tr></thead><tbody>{sev}</tbody></table>'
            '<h3>List (by priority)</h3><div class="scroll"><table><thead><tr><th>ID</th><th>Title</th>'
            '<th>Severity</th><th>Priority</th><th>Case</th><th>Status</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        defects_block = '<p class="empty">No open defects.</p>'

    if ctx["sections"]:
        ctx_block = "".join(
            f"<h3>{_e(t)}</h3>{minimal_markdown(md) or '<p class=empty>—</p>'}" for t, md in ctx["sections"]
        )
    else:
        ctx_block = '<p class="empty">No context.md in the workspace.</p>'

    rs = data.get("summary")
    if rs is None:
        summary_line, summary_block = "", ""
    elif not rs["checked"]:
        summary_line, summary_block = f"<p>Workbook summary not checked: {_e(rs['reason'])}.</p>", ""
    elif not rs["problems"]:
        summary_line, summary_block = "<p>Workbook summary: it matches the direct count.</p>", ""
    else:
        summary_line = ""
        items = "".join(f"<li>{_e(x)}</li>" for x in rs["problems"])
        summary_block = (
            '<section class="alert"><h2>The workbook summary does not match the direct count</h2>'
            f"<ul>{items}</ul><p>The numbers of this dashboard come from the direct count, case by case, not from "
            "the summary. Fix the summary's formulas, not the numbers by hand.</p></section>"
        )

    # the numbers also as JSON, to check the totals without reading the HTML
    numbers = {"cases": {k: v for k, v in c.items()},
               "defects": {k: v for k, v in d.items() if k != "list"},
               "summary": rs}
    json_txt = json.dumps(numbers, ensure_ascii=False, indent=1).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard · {_e(data["project"])}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>{_e(data["project"])}</h1>
<p>Last checkpoint: {_e(ctx["updated"] or "—")} · dashboard built at {_e(data["built"])} from <code>{_e(data["workbook"])}</code></p>
{summary_line}
</header>
{summary_block}
<section><h2>Progress</h2>
<div class="kpis">{kpis}</div>
<div style="margin-top:14px">{_bar(c["by_status"], c["total"], colours)}</div>
{_legend(c["by_status"], colours)}
</section>
<section><h2>By area</h2>
<div class="scroll"><table><thead><tr><th>Area</th><th class="n">Run</th><th>Distribution</th><th>Statuses</th></tr></thead>
<tbody>{area_rows}</tbody></table></div>
</section>
<section><h2>Open defects</h2>{defects_block}</section>
<section class="ctx"><h2>Context</h2>{ctx_block}</section>
<footer>Built by qa-assistant/scripts/build_dashboard.py · the workbook is only read.</footer>
</main>
<script type="application/json" id="numbers">{json_txt}</script>
</body>
</html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Builds a workspace's dashboard.html (plan 5.1).")
    ap.add_argument("workspace")
    ap.add_argument("--output", default="dashboard.html", help="relative to the workspace (default dashboard.html)")
    ap.add_argument("--open", action="store_true", help="open the dashboard built in the browser (local file)")
    a = ap.parse_args(argv)
    try:
        data = collect(a.workspace)
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    target = os.path.join(a.workspace, a.output)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(data))
    c, d = data["cases"], data["defects"]
    print(f"dashboard built: {target} ({c['run']}/{c['total']} cases run, {d['open']} open defects)")
    rs = data.get("summary")
    if rs is None:
        print("workbook summary: no summary configured")
    elif not rs["checked"]:
        print(f"workbook summary: not checked -- {rs['reason']}")
    elif rs["problems"]:
        print(f"workbook summary: {len(rs['problems'])} difference(s) with the direct count (see the dashboard)")
    else:
        print("workbook summary: it matches the direct count")
    if a.open:
        webbrowser.open(Path(target).resolve().as_uri())
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
