"""
Valida o projeto.yaml de um workspace (metodologia-core.md seccao 4 e 4.1;
plano, tarefa 3.4): campos base, modulos da lista fechada, seccao de cada
modulo ativo, Excel (quando tracker: excel) e seccao painel:.

    python scripts/validar_projeto.py <workspace>

So le; nunca altera nada. Problemas impedem dar o onboarding por concluido;
avisos mostram-se ao utilizador.

Codigos de saida: 0 sem problemas (pode haver avisos) · 1 problemas · 2 nao ha projeto.yaml
"""

from __future__ import annotations

import argparse
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

# lista fechada (metodologia 4.1): modulo -> (seccao propria, campos obrigatorios nela)
MODULOS = {
    "funcional-web": (None, ()),
    "exploratorio": (None, ()),
    "api": ("api", ("base_url",)),
    "acessibilidade": ("acessibilidade", ("paginas",)),
    "mobile": ("mobile", ("plataforma", "flows")),
}
TRACKERS = ("excel", "azure-devops", "jira")


def validar(workspace: str) -> tuple[list[str], list[str]]:
    with open(os.path.join(workspace, "projeto.yaml"), encoding="utf-8-sig") as f:
        proj = yaml.safe_load(f) or {}
    problemas, avisos = [], []

    for campo in ("projeto", "tracker", "formato_bug"):
        if not proj.get(campo):
            problemas.append(f"campo '{campo}' vazio")
    if not (proj.get("ambientes") or {}):
        problemas.append("'ambientes' vazio: e preciso pelo menos o ambiente de testes")
    if proj.get("tracker") and proj["tracker"] not in TRACKERS:
        problemas.append(f"tracker {proj['tracker']!r} fora de {list(TRACKERS)}")
    if proj.get("bugs_resolvidos") not in (None, "remover", "arquivar"):
        problemas.append("bugs_resolvidos tem de ser 'remover' ou 'arquivar'")
    if proj.get("evidencia") not in (None, "texto", "texto+screenshot"):
        problemas.append("evidencia tem de ser 'texto' ou 'texto+screenshot'")

    modulos = proj.get("modulos") or []
    if not modulos:
        problemas.append("'modulos' vazio: escolher da lista da metodologia 4.1")
    fora = [m for m in modulos if m not in MODULOS]
    if fora:
        problemas.append(f"modulos fora da lista fechada (metodologia 4.1): {fora}; validos: {list(MODULOS)}")
    for m in modulos:
        secao, obrig = MODULOS.get(m, (None, ()))
        if secao:
            s = proj.get(secao) or {}
            faltam = [c for c in obrig if not s.get(c)]
            if faltam:
                problemas.append(f"modulo '{m}' ativo mas a seccao {secao}: nao tem {faltam}")
    for m, (secao, obrig) in MODULOS.items():
        if secao and m not in modulos and any((proj.get(secao) or {}).get(c) for c in obrig):
            avisos.append(f"seccao {secao}: preenchida, mas o modulo '{m}' nao esta em 'modulos'")

    if proj.get("tracker") == "excel":
        from adapters.excel import ConfigError, ExcelAdapter
        try:
            ad = ExcelAdapter.from_projeto(workspace)
            ad.load()
        except (ConfigError, FileNotFoundError, KeyError) as e:
            problemas.append(f"excel: {e}")
        else:
            cc = ad.cross_check()
            if cc:
                avisos.append(f"excel: o workbook tem {len(cc)} problema(s) de cross-check (ex.: {cc[0]})")
            import gerar_painel
            try:
                tipos = gerar_painel.tipos_de_estado(list(ad.cfg["casos"]["estados"]), proj.get("painel"))
                gerar_painel.verificar_por_testar(tipos, ad.cfg)
            except ValueError as e:
                problemas.append(f"painel: {e}")
            if not (proj.get("painel") or {}).get("estados"):
                avisos.append("painel.estados vazio: o painel trata o 1.o estado como pendente e os outros como neutros")
    return problemas, avisos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Valida o projeto.yaml de um workspace.")
    ap.add_argument("workspace")
    a = ap.parse_args(argv)
    if not os.path.isfile(os.path.join(a.workspace, "projeto.yaml")):
        print(f"ERRO: nao existe projeto.yaml em {a.workspace}", file=sys.stderr)
        return 2
    problemas, avisos = validar(a.workspace)
    for p in problemas:
        print(f"PROBLEMA: {p}")
    for w in avisos:
        print(f"aviso: {w}")
    print(f"{len(problemas)} problema(s), {len(avisos)} aviso(s)")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
