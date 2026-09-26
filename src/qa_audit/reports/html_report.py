"""report.html: self-contained report (docs/qa-audit.md §8).

No script, no external resource. The Content-Security-Policy meta tag forbids everything except inline styles and
local images, so even content that escaped escaping could not run or load anything. Every string from the
audited site goes through esc(); links to audited URLs only keep http(s)."""

from __future__ import annotations

import html
from urllib.parse import urlsplit

from ..core.findings import SEVERITIES

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' file: data:; base-uri 'none'; form-action 'none'"

CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d1f;--muted:#5f6368;--line:#e2e2e6;--accent:#2f5bd3;
--critical:#8b0f1a;--high:#c62828;--medium:#b35c00;--low:#5b6b00;--info:#4a5568;--ok:#1e7a3c;--code:#f0f0f3}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--card:#1d1d22;--fg:#ececf1;--muted:#a0a3ad;--line:#33333b;
--accent:#8fb0ff;--critical:#ff7b86;--high:#ff8a80;--medium:#ffb95e;--low:#d4e157;--info:#b0bec5;--ok:#6fd88f;
--code:#26262d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 12px}.muted{color:var(--muted)}.meta{display:flex;flex-wrap:wrap;gap:4px 20px;
margin:8px 0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.note{border-left:4px solid var(--accent);margin:8px 0}.tiles{display:grid;
grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}.tile b{display:block;font-size:26px}
.sev{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;
border:1px solid currentColor}.sev.critical{color:var(--critical)}.sev.high{color:var(--high)}
.sev.medium{color:var(--medium)}.sev.low{color:var(--low)}.sev.info{color:var(--info)}
.st{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;
overflow:hidden}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}td.num,th.num{text-align:right}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
details>div{padding:0 14px 14px}dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}pre{background:var(--code);padding:10px;border-radius:8px;
overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;margin:8px 0}
img.shot{max-width:100%;max-height:360px;border:1px solid var(--line);border-radius:6px;margin:6px 6px 0 0}
a{color:var(--accent)}.wrap{overflow-wrap:anywhere}ul.check{list-style:none;padding-left:0}
ul.check li::before{content:"\\2610  ";color:var(--muted)}
@media (max-width:640px){dl{grid-template-columns:1fr}td,th{padding:6px}}
"""


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def link(url: str) -> str:
    """An audited URL as a link only when it is http(s); otherwise plain text."""
    if urlsplit(str(url)).scheme in ("http", "https"):
        return f'<a href="{esc(url)}" rel="noreferrer noopener" class="wrap">{esc(url)}</a>'
    return f'<span class="wrap">{esc(url)}</span>'


def _local_path(path: str) -> str:
    """Evidence paths are relative to the run folder; never absolute or outside it."""
    p = str(path).replace("\\", "/")
    if p.startswith("/") or ":" in p or ".." in p.split("/"):
        return ""
    return p


def _finding(f) -> str:
    rows = [("Module / check", esc(f"{f.module} / {f.check}")),
            ("Status", esc(f.status)), ("Confidence", esc(f.confidence)), ("URL", link(f.url))]
    if f.element.get("selector"):
        rows.append(("Element", f"<code class=\"wrap\">{esc(f.element['selector'])}</code>"))
    for label, value in (("Description", f.description), ("Expected", f.expected), ("Actual", f.actual),
                         ("Recommendation", f.recommendation)):
        if value:
            rows.append((label, esc(value)))
    if f.steps:
        rows.append(("Steps", "<ol>" + "".join(f"<li>{esc(s)}</li>" for s in f.steps) + "</ol>"))
    if f.references:
        rows.append(("References", " ".join(link(r) for r in f.references)))
    body = "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"
    if f.element.get("snippet"):
        body += f"<pre>{esc(f.element['snippet'])}</pre>"
    shots, texts = [], []
    for e in f.evidence:
        if e.path and e.kind == "screenshot":
            p = _local_path(e.path)
            if p:
                shots.append(f'<a href="{esc(p)}"><img class="shot" src="{esc(p)}" alt="{esc(e.label or "screenshot")}"'
                             '>/a>')
        elif e.value and not (e.kind == "html" and e.value == f.element.get("snippet")):
            texts.append(f"<pre>{esc(e.kind)}: {esc(e.value)}</pre>")
    if texts or shots:
        body += "<p class=\"st\">Evidence</p>" + "".join(texts) + "".join(shots)
    return (f'<details><summary><span class="sev {esc(f.severity)}">{esc(f.severity)}</span>'
            f'<b>{esc(f.id)}</b><span>{esc(f.title)}</span><span class="st">{esc(f.status)}</span>'
            f'<span class="muted wrap">{esc(f.url)}</span></summary><div>{body}</div></details>')


def render(result) -> str:
    s, b = result.summary, result.browser
    st = s["by_status"]
    out = [(f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
            '<meta name="referrer" content="no-referrer">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>QA audit</title><style>{CSS}</style></head><body><main>')]
    out.append(f"<h1>QA audit</h1><div class=\"muted wrap\">{link(result.target)}</div>")
    audited = sum(1 for p in result.pages if p.audited)
    out.append('<div class="meta muted">'
               f"<span>{esc(result.started_at)}</span><span>{esc(result.duration_s)} s</span>"
               f"<span>{esc(b['engine'])} {esc(b['version'])}</span>"
               f"<span>{audited} pages audited</span>"
               f"<span>qa-audit {esc(result.to_dict()['tool']['version'])}</span></div>")
    for m in result.modules:
        if m.get("disclaimer"):
            out.append(f'<p class="card note"><b>{esc(m["title"])}.</b> {esc(m["disclaimer"])}</p>')
    out.append("<h2>Summary</h2><div class=\"tiles\">")
    for label, value in (("Passed checks", s["passed"]), ("Problems", st["problem"]), ("Warnings", st["warning"]),
                         ("Needs human", st["needs-human"])):
        out.append(f'<div class="card tile"><span class="st">{label}</span><b>{value}</b></div>')
    out.append("</div><p></p><div class=\"tiles\">")
    for sev in SEVERITIES:
        out.append(f'<div class="card tile"><span class="sev {sev}">{sev}</span><b>{s["by_severity"][sev]}</b></div>')
    out.append("</div>")
    out.append("<h2>Modules</h2><table><tr><th>Module</th><th class=num>Passed</th><th class=num>Findings</th>"
               "<th class=num>Errors</th></tr>")
    for m in result.modules:
        out.append(f"<tr><td>{esc(m['title'])}</td><td class=num>{m['passed']}</td><td class=num>{m['findings']}</td>"
                   f"<td class=num>{m['errors']}</td></tr>")
    out.append("</table>")
    out.append("<h2>Findings</h2>")
    if not result.findings:
        out.append('<p class="card">No findings.</p>')
    for f in result.findings:
        out.append(_finding(f))
    metrics = getattr(result, "metrics", None) or []
    if metrics:
        from . import metric_value
        flagged = sum(1 for m in metrics if m["result"] in ("needs-improvement", "poor", "over-budget"))
        out.append(f"<h2>Metrics</h2><details><summary>{len(metrics)} measurements · {flagged} above a threshold"
                   "</summary><div><table><tr><th>Page</th><th>Metric</th><th class=num>Observed</th>"
                   "<th>Threshold</th><th>Result</th></tr>")
        for m in metrics:
            out.append(f"<tr><td class=wrap>{esc(m['url'])}</td><td>{esc(m['metric'])}</td>"
                       f"<td class=num>{esc(metric_value(m))}</td><td>{esc(m['threshold'])}</td>"
                       f"<td>{esc(m['result'])}</td></tr>")
        out.append("</table></div></details>")
    if result.manual_checks:
        out.append("<h2>Manual checks</h2><p class=\"muted\">Not automated. A person has to check these.</p>")
        for module, checks in result.manual_checks.items():
            out.append(f'<div class="card"><b>{esc(module)}</b><ul class="check">')
            for c in checks:
                out.append(f"<li><b>{esc(c['title'])}</b> <span class=\"muted\">{esc(c.get('how', ''))}</span></li>")
            out.append("</ul></div>")
    out.append("<h2>Pages</h2><table><tr><th>URL</th><th class=num>Status</th><th>Title</th><th>Note</th></tr>")
    for p in result.pages:
        out.append(f"<tr><td>{link(p.url)}</td><td class=num>{esc(p.status or '')}</td><td>{esc(p.title)}</td>"
                   f"<td>{esc(p.error)}</td></tr>")
    out.append("</table>")
    skipped = result.crawl["skipped"]
    if skipped or result.crawl["not_visited"]:
        out.append(f"<details><summary>Not visited: {len(skipped)} skipped, {result.crawl['not_visited']} beyond "
                   f"max pages · robots.txt {esc(result.crawl['robots'])}"
                   f" · sitemap {esc(result.crawl.get('sitemap', 'off'))}</summary><div><table>"
                   "<tr><th>URL</th><th>Reason</th></tr>")
        for x in skipped:
            out.append(f"<tr><td class=wrap>{esc(x['url'])}</td><td>{esc(x['reason'])}</td></tr>")
        out.append("</table></div></details>")
    if result.notes or result.errors:
        out.append("<h2>Notes</h2><ul>")
        for n in result.notes:
            out.append(f"<li>{esc(n)}</li>")
        for e in result.errors:
            out.append(f"<li>Error in {esc(e['module'])} on {esc(e['url'] or 'finalize')}: {esc(e['message'])}</li>")
        out.append("</ul>")
    out.append("</main></body></html>")
    return "\n".join(out)


def write(result, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(result))
