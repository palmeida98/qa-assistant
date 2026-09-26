"""compatibility.json / .md / .html: the comparison of one audit in several engines (docs/qa-audit.md §21).

Written at the root of the run folder, next to one sub-folder per engine. Same rules as the other reports: every
string from the audited site is escaped, and the HTML page loads and runs nothing.
"""

from __future__ import annotations

import json
import os

from . import html_report, md_report


def to_dict(comp: dict) -> dict:
    return {**comp, "differences": [f.to_dict() for f in comp["differences"]]}


def write_all(comp: dict, folder: str, formats: list[str]) -> dict[str, str]:
    out = {}
    for fmt, name, fn in (("json", "compatibility.json", _json), ("md", "compatibility.md", _md),
                          ("html", "compatibility.html", _html)):
        if fmt in formats:
            path = os.path.join(folder, name)
            fn(comp, path)
            out[fmt] = path
    return out


def _json(comp: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(comp), f, ensure_ascii=False, indent=2, default=str)


def _md(comp: dict, path: str) -> None:
    esc = md_report.esc
    lines = ["# QA audit: compatibility", "", f"- **Target:** {esc(comp['target'])}",
             f"- **Date:** {esc(comp['started_at'])}", "", "## Engines", "",
             "| Engine | Result | Problems | Warnings | Needs human | Report |", "|---|---|---|---|---|---|"]
    for e in comp["engines"]:
        s = e.get("summary") or {}
        st = s.get("by_status") or {}
        lines.append(f"| {esc(e['engine'])} {esc(e.get('version', ''))} | {esc(e['result'])} | "
                     f"{st.get('problem', '')} | {st.get('warning', '')} | {st.get('needs-human', '')} | "
                     f"{esc(e.get('folder', ''))} |")
    lines += ["", f"## Differences ({len(comp['differences'])})", ""]
    if not comp["differences"]:
        lines.append("None: every engine that ran gave the same results.")
    for f in comp["differences"]:
        lines += [f"### {esc(f.title)}", "", f"- **Page:** {esc(f.url)}", f"- **Engines:** {esc(f.actual)}"]
        if (f.element or {}).get("selector"):
            lines.append(f"- **Element:** {esc(f.element['selector'])}")
        lines += [f"- {esc(f.description)}", ""]
    if comp["notes"]:
        lines += ["## Notes", ""] + [f"- {esc(n)}" for n in comp["notes"]]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _html(comp: dict, path: str) -> None:
    esc, link = html_report.esc, html_report.link
    out = [(f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{html_report.CSP}">'
            '<meta name="referrer" content="no-referrer">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>QA audit: compatibility</title><style>{html_report.CSS}</style></head><body><main>"),
           f"<h1>QA audit: compatibility</h1><div class=\"muted wrap\">{link(comp['target'])}</div>",
           (f"<div class=\"meta muted\"><span>{esc(comp['started_at'])}</span>"
            f"<span>{len(comp['engines'])} engines</span></div>"),
           ("<h2>Engines</h2><table><tr><th>Engine</th><th>Result</th><th class=num>Problems</th>"
            "<th class=num>Warnings</th><th class=num>Needs human</th><th>Report</th></tr>")]
    for e in comp["engines"]:
        st = (e.get("summary") or {}).get("by_status") or {}
        report = f'<a href="{esc(e["folder"])}/report.html">{esc(e["folder"])}/</a>' if e.get("report") else ""
        out.append(f"<tr><td>{esc(e['engine'])} {esc(e.get('version', ''))}</td><td>{esc(e['result'])}</td>"
                   f"<td class=num>{esc(st.get('problem', ''))}</td><td class=num>{esc(st.get('warning', ''))}</td>"
                   f"<td class=num>{esc(st.get('needs-human', ''))}</td><td>{report}</td></tr>")
    out.append(f"</table><h2>Differences ({len(comp['differences'])})</h2>")
    if not comp["differences"]:
        out.append('<p class="card">None: every engine that ran gave the same results.</p>')
    for f in comp["differences"]:
        sel = (f.element or {}).get("selector")
        out.append(f"<details><summary><span class=\"sev info\">needs human</span> {esc(f.title)}</summary><div><dl>"
                   f"<dt>Page</dt><dd>{link(f.url)}</dd><dt>Engines</dt><dd>{esc(f.actual)}</dd>"
                   + (f"<dt>Element</dt><dd><code>{esc(sel)}</code></dd>" if sel else "")
                   + f"<dt>Why</dt><dd>{esc(f.description)}</dd></dl></div></details>")
    if comp["notes"]:
        out.append("<h2>Notes</h2>" + "".join(f'<p class="card note">{esc(n)}</p>' for n in comp["notes"]))
    out.append("</main></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
