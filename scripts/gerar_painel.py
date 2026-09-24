"""
Painel de progresso de um workspace (plano, tarefa 5.1).

Le o workbook pelo adaptador (so leitura, nunca grava) e o contexto.md, e
gera um unico painel.html no workspace: progresso por area e estado, defeitos
abertos por severidade, ultimo checkpoint e proximos passos. Offline: sem
JavaScript, CDNs nem fontes externas.

O significado de cada estado de caso vem da secao `painel:` do projeto.yaml
(pendente | ok | falha | bloqueado | na); sem ela, o primeiro estado de
excel.casos.estados conta como pendente e os restantes ficam neutros.

Uso (a partir da raiz do qa-assistant; skill /painel, plano 5.2):
    python scripts/gerar_painel.py <workspace> [--saida painel.html] [--abrir]

Sai com 0 (gerado) ou 2 (configuracao invalida: nada gerado). --abrir abre o
ficheiro local no browser; o painel nunca se publica (tem dados do projeto).
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

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adapters.excel import ExcelAdapter

TIPOS = ("pendente", "ok", "falha", "bloqueado", "na")
# secoes do contexto.md (metodologia-core 12.2) mostradas no painel, por prefixo do titulo
SECOES_CONTEXTO = (
    ("ponto de execu", "Ponto de execução"),
    ("trabalho pendente", "Próximos passos"),
    ("dúvidas em aberto", "Dúvidas em aberto"),
    ("problemas conhecidos", "Problemas conhecidos / bloqueios"),
)


# ---------------------------------------------------------------------------
# Recolha de dados
# ---------------------------------------------------------------------------


def tipos_de_estado(estados: list[str], painel: dict | None) -> dict[str, str]:
    """estado -> tipo. Valida a secao painel.estados contra os estados do workbook."""
    mapa = (painel or {}).get("estados") or {}
    if mapa:
        fora = [e for e in mapa if e not in estados]
        maus = [f"{e}: {t}" for e, t in mapa.items() if t not in TIPOS]
        if fora or maus:
            raise ValueError(
                "secao painel.estados invalida: "
                + (f"estados que nao existem em excel.casos.estados: {fora}. " if fora else "")
                + (f"tipos invalidos (validos: {', '.join(TIPOS)}): {maus}" if maus else "")
            )
        return {e: mapa.get(e, "outro") for e in estados}
    return {e: ("pendente" if i == 0 else "outro") for i, e in enumerate(estados)}


def verificar_por_testar(tipos: dict[str, str], cfg_excel: dict) -> None:
    """A % executada do resumo (aba_resumo.estado_por_testar) e a do painel
    (estado pendente) tem de usar o mesmo "por testar" (metodologia 16.3)."""
    pend = (cfg_excel.get("aba_resumo") or {}).get("estado_por_testar")
    if pend and tipos.get(pend) != "pendente":
        raise ValueError(
            f"excel.aba_resumo.estado_por_testar e {pend!r}, mas no painel esse estado e "
            f"{tipos.get(pend)!r}: acertar a secao painel.estados para as duas % executadas coincidirem"
        )
    outros = [e for e, t in tipos.items() if t == "pendente" and e != pend]
    if pend and outros:
        raise ValueError(f"o painel trata {outros} como pendente, mas o resumo so conta {pend!r} como por testar")


def recolher_workbook(ad: ExcelAdapter, tipos: dict[str, str]) -> dict:
    """Numeros do painel a partir de um adaptador ja carregado."""
    cfg = ad.cfg
    estados = list(cfg["casos"]["estados"])
    por_area = ad.progress_summary()
    extra = sorted({e for c in por_area.values() for e in c if e not in estados}, key=str)
    ordem = estados + extra  # estados fora da lista aparecem no fim (o cross-check reporta-os)

    areas = []
    for area, cont in por_area.items():
        total = sum(cont.values())
        pend = sum(n for e, n in cont.items() if tipos.get(e) == "pendente")
        areas.append({"area": area, "total": total, "executados": total - pend,
                      "por_estado": {e: cont.get(e, 0) for e in ordem if cont.get(e, 0)}})
    tot_estado = {e: sum(a["por_estado"].get(e, 0) for a in areas) for e in ordem}
    total = sum(a["total"] for a in areas)

    d = cfg["defeitos"]
    col = d["colunas"]
    ab = ad.defeitos_abertos()
    sev_col = col.get("severidade")
    sev_cont: dict[str, int] = {}
    for row in ab["abertos"]:
        s = row.get(sev_col) if sev_col else None
        s = "(sem severidade)" if s in (None, "") else str(s)
        sev_cont[s] = sev_cont.get(s, 0) + 1
    # severidades que tambem sao prioridades seguem essa ordem; as outras, a da 1.a ocorrencia
    # na lista de abertos (ja ordenada por prioridade)
    prios, primeira = d["prioridades"], list(sev_cont)
    sev_ordem = sorted(sev_cont, key=lambda s: (prios.index(s) if s in prios else len(prios), primeira.index(s)))

    titulo_col = (d.get("campos_lote") or {}).get("titulo")
    defeitos = [
        {"id": r.get(col["id"]), "titulo": r.get(titulo_col) if titulo_col else None,
         "severidade": r.get(sev_col) if sev_col else None,
         "prioridade": r.get(col["prioridade"]) if col.get("prioridade") else None,
         "caso": r.get(col["caso"]) if col.get("caso") else None,
         "estado": r.get(col["estado"])}
        for r in ab["abertos"]
    ]
    return {
        "estados": ordem,
        "tipos": {e: tipos.get(e, "outro") for e in ordem},
        "casos": {"total": total, "executados": sum(a["executados"] for a in areas),
                  "por_estado": {e: n for e, n in tot_estado.items() if n}, "por_area": areas},
        "defeitos": {"abertos": len(defeitos), "fechados": ab["fechados"],
                     "por_severidade": {s: sev_cont[s] for s in sev_ordem}, "lista": defeitos},
    }


def ler_contexto(path: str) -> dict:
    """{"atualizado": str|None, "secoes": [(titulo, markdown)]} do contexto.md."""
    if not os.path.isfile(path):
        return {"atualizado": None, "secoes": []}
    with open(path, encoding="utf-8-sig") as f:
        texto = f.read()
    m = re.search(r"^Atualizado:\s*(.+)$", texto, re.MULTILINE)
    blocos = {}
    for bloco in re.split(r"^## ", texto, flags=re.MULTILINE)[1:]:
        titulo, _, corpo = bloco.partition("\n")
        blocos[titulo.strip()] = corpo.strip()
    secoes = []
    for prefixo, nome in SECOES_CONTEXTO:
        for titulo, corpo in blocos.items():
            if titulo.lower().startswith(prefixo):
                secoes.append((nome, corpo))
                break
    return {"atualizado": m.group(1).strip() if m else None, "secoes": secoes}


def recolher(workspace: str) -> dict:
    with open(os.path.join(workspace, "projeto.yaml"), encoding="utf-8-sig") as f:
        proj = yaml.safe_load(f) or {}
    tracker = proj.get("tracker")
    if tracker != "excel":
        raise ValueError(f"o painel so suporta tracker: excel por agora (este projeto: {tracker!r})")
    ad = ExcelAdapter.from_projeto(workspace)
    ad.load()  # so leitura: o painel nunca grava o workbook
    tipos = tipos_de_estado(list(ad.cfg["casos"]["estados"]), proj.get("painel"))
    verificar_por_testar(tipos, ad.cfg)
    dados = recolher_workbook(ad, tipos)
    dados["projeto"] = proj.get("projeto") or os.path.basename(os.path.abspath(workspace))
    dados["workbook"] = ad.cfg["ficheiro"]
    dados["gerado"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    dados["contexto"] = ler_contexto(os.path.join(workspace, "contexto.md"))
    # os totais do painel vem sempre da contagem direta; o resumo do workbook so se verifica
    dados["resumo"] = ad.verificar_resumo() if ad.cfg.get("aba_resumo") else None
    return dados


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;
--pendente:#d9d8d2;--ok:#2f7d4f;--falha:#c0392b;--bloqueado:#c98a14;--na:#6f7f95;
--o1:#4f6d9a;--o2:#8a5a9c;--o3:#3d8a8a;--o4:#9a6b4f;--code:#efeee9}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;
--line:#34332f;--pendente:#4a4944;--ok:#4caf74;--falha:#e0624f;--bloqueado:#e0a53a;--na:#8c9bb0;
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
.bar{display:flex;height:22px;border-radius:5px;overflow:hidden;background:var(--pendente)}
.bar i{display:block;height:100%}
.leg{display:flex;flex-wrap:wrap;gap:6px 16px;margin:10px 0 0;padding:0;list-style:none;font-size:.85rem}
.leg li::before{content:"";display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:var(--c)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.barra{width:40%;min-width:120px}
.scroll{overflow-x:auto}
.vazio{color:var(--muted)}
.alerta{border-color:var(--falha);border-width:2px}
.alerta h2{color:var(--falha)}
.alerta ul{margin:6px 0;padding-left:20px}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.88em}
.ctx ul{margin:4px 0;padding-left:20px}
.ctx p{margin:4px 0}
footer{margin-top:18px;color:var(--muted);font-size:.8rem}
"""

OUTROS = ("--o1", "--o2", "--o3", "--o4")


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _cores(dados: dict) -> dict[str, str]:
    cores, k = {}, 0
    for e in dados["estados"]:
        t = dados["tipos"][e]
        if t in TIPOS:
            cores[e] = f"var(--{t})"
        else:
            cores[e] = f"var({OUTROS[k % len(OUTROS)]})"
            k += 1
    return cores


def _barra(por_estado: dict, total: int, cores: dict) -> str:
    if not total:
        return '<div class="bar"></div>'
    partes = "".join(
        f'<i style="width:{100 * n / total:.3f}%;background:{cores[e]}" title="{_e(e)}: {n}"></i>'
        for e, n in por_estado.items()
    )
    return f'<div class="bar" role="img" aria-label="{_e(", ".join(f"{e}: {n}" for e, n in por_estado.items()))}">{partes}</div>'


def _legenda(por_estado: dict, cores: dict) -> str:
    return '<ul class="leg">' + "".join(
        f'<li style="--c:{cores[e]}">{_e(e)} <b>{n}</b></li>' for e, n in por_estado.items()
    ) + "</ul>"


def _inline(txt: str) -> str:
    t = html.escape(txt)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return re.sub(r"(?<![*\w])\*([^*]+)\*(?!\w)", r"<i>\1</i>", t)


def markdown_minimo(md: str) -> str:
    """Listas (com niveis por indentacao), paragrafos, `codigo`, **negrito**, *italico*."""
    out, pilha, para = [], [], []

    def fechar_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    for linha in md.splitlines():
        m = re.match(r"^(\s*)[-*]\s+(.*)$", linha)
        if m:
            fechar_para()
            nivel = len(m.group(1).expandtabs(2)) // 2
            while len(pilha) > nivel + 1:
                out.append("</li></ul>")
                pilha.pop()
            if len(pilha) < nivel + 1:
                while len(pilha) < nivel + 1:
                    out.append("<ul><li>")
                    pilha.append(nivel)
            else:
                out.append("</li><li>")
            out.append(_inline(m.group(2)))
        elif linha.strip():
            if pilha:  # continuacao de um item
                out.append(" " + _inline(linha.strip()))
            else:
                para.append(linha.strip())
        else:
            fechar_para()
            while pilha:
                out.append("</li></ul>")
                pilha.pop()
    fechar_para()
    while pilha:
        out.append("</li></ul>")
        pilha.pop()
    return "".join(out)


def render(dados: dict) -> str:
    cores = _cores(dados)
    c, d, ctx = dados["casos"], dados["defeitos"], dados["contexto"]
    pct = f"{100 * c['executados'] / c['total']:.0f}%" if c["total"] else "–"

    kpis = (
        f'<div class="kpi"><b>{c["total"]}</b><span>casos</span></div>'
        f'<div class="kpi"><b>{c["executados"]}</b><span>executados ({pct})</span></div>'
        f'<div class="kpi"><b>{d["abertos"]}</b><span>defeitos abertos</span></div>'
        f'<div class="kpi"><b>{d["fechados"]}</b><span>defeitos fechados</span></div>'
    )

    linhas_area = "".join(
        f'<tr><td>{_e(a["area"])}</td><td class="n">{a["executados"]}/{a["total"]}</td>'
        f'<td class="barra">{_barra(a["por_estado"], a["total"], cores)}</td>'
        f'<td>{", ".join(f"{_e(e)} {n}" for e, n in a["por_estado"].items())}</td></tr>'
        for a in c["por_area"]
    )

    if d["abertos"]:
        sev = "".join(
            f'<tr><td>{_e(s)}</td><td class="n">{n}</td></tr>' for s, n in d["por_severidade"].items()
        )
        lista = "".join(
            f'<tr><td>{_e(x["id"])}</td><td>{_e(x["titulo"])}</td><td>{_e(x["severidade"])}</td>'
            f'<td>{_e(x["prioridade"])}</td><td>{_e(x["caso"])}</td><td>{_e(x["estado"])}</td></tr>'
            for x in d["lista"]
        )
        bloco_def = (
            f'<table><thead><tr><th>Severidade</th><th class="n">Abertos</th></tr></thead><tbody>{sev}</tbody></table>'
            '<h3>Lista (por prioridade)</h3><div class="scroll"><table><thead><tr><th>ID</th><th>Título</th>'
            '<th>Severidade</th><th>Prioridade</th><th>Caso</th><th>Estado</th></tr></thead>'
            f"<tbody>{lista}</tbody></table></div>"
        )
    else:
        bloco_def = '<p class="vazio">Sem defeitos abertos.</p>'

    if ctx["secoes"]:
        bloco_ctx = "".join(
            f"<h3>{_e(t)}</h3>{markdown_minimo(md) or '<p class=vazio>—</p>'}" for t, md in ctx["secoes"]
        )
    else:
        bloco_ctx = '<p class="vazio">Sem contexto.md no workspace.</p>'

    rs = dados.get("resumo")
    if rs is None:
        linha_resumo, bloco_resumo = "", ""
    elif not rs["verificado"]:
        linha_resumo, bloco_resumo = f"<p>Resumo do workbook não verificado: {_e(rs['motivo'])}.</p>", ""
    elif not rs["problemas"]:
        linha_resumo, bloco_resumo = "<p>Resumo do workbook: bate certo com a contagem direta.</p>", ""
    else:
        linha_resumo = ""
        itens = "".join(f"<li>{_e(x)}</li>" for x in rs["problemas"])
        bloco_resumo = (
            '<section class="alerta"><h2>O resumo do workbook não bate com a contagem direta</h2>'
            f"<ul>{itens}</ul><p>Os números deste painel vêm da contagem direta, caso a caso, e não do resumo. "
            "Corrige as fórmulas do resumo, não os números à mão.</p></section>"
        )

    # numeros tambem em JSON, para verificar os totais sem ler o HTML
    numeros = {"casos": {k: v for k, v in c.items()},
               "defeitos": {k: v for k, v in d.items() if k != "lista"},
               "resumo": rs}
    json_txt = json.dumps(numeros, ensure_ascii=False, indent=1).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel · {_e(dados["projeto"])}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>{_e(dados["projeto"])}</h1>
<p>Último checkpoint: {_e(ctx["atualizado"] or "—")} · painel gerado a {_e(dados["gerado"])} a partir de <code>{_e(dados["workbook"])}</code></p>
{linha_resumo}
</header>
{bloco_resumo}
<section><h2>Progresso</h2>
<div class="kpis">{kpis}</div>
<div style="margin-top:14px">{_barra(c["por_estado"], c["total"], cores)}</div>
{_legenda(c["por_estado"], cores)}
</section>
<section><h2>Por área</h2>
<div class="scroll"><table><thead><tr><th>Área</th><th class="n">Executados</th><th>Distribuição</th><th>Estados</th></tr></thead>
<tbody>{linhas_area}</tbody></table></div>
</section>
<section><h2>Defeitos abertos</h2>{bloco_def}</section>
<section class="ctx"><h2>Contexto</h2>{bloco_ctx}</section>
<footer>Gerado por qa-assistant/scripts/gerar_painel.py · só leitura do workbook.</footer>
</main>
<script type="application/json" id="numeros">{json_txt}</script>
</body>
</html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera o painel.html de um workspace (plano 5.1).")
    ap.add_argument("workspace")
    ap.add_argument("--saida", default="painel.html", help="relativo ao workspace (por omissao painel.html)")
    ap.add_argument("--abrir", action="store_true", help="abre o painel gerado no browser (ficheiro local)")
    a = ap.parse_args(argv)
    try:
        dados = recolher(a.workspace)
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    destino = os.path.join(a.workspace, a.saida)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(dados))
    c, d = dados["casos"], dados["defeitos"]
    print(f"painel gerado: {destino} ({c['executados']}/{c['total']} casos executados, "
          f"{d['abertos']} defeitos abertos)")
    rs = dados.get("resumo")
    if rs is None:
        print("resumo do workbook: sem aba_resumo configurada")
    elif not rs["verificado"]:
        print(f"resumo do workbook: nao verificado -- {rs['motivo']}")
    elif rs["problemas"]:
        print(f"resumo do workbook: {len(rs['problemas'])} diferenca(s) com a contagem direta (ver o painel)")
    else:
        print("resumo do workbook: bate certo com a contagem direta")
    if a.abrir:
        webbrowser.open(Path(destino).resolve().as_uri())
    return 0


if __name__ == "__main__":
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
