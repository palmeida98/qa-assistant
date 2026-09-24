"""
Valida o projeto.yaml de um workspace (metodologia-core.md seccao 4 e 4.1;
plano, tarefa 3.4): campos base, URL de cada ambiente, modulos da lista
fechada, seccao de cada modulo ativo, URLs dos modulos dentro dos ambientes
declarados (nunca producao), Excel (quando tracker: excel), seccao painel: e,
num workspace com git, o .gitignore dos ficheiros com segredos (aviso) e a
protecao de historico/ no .claude/settings.json (aviso).

    python scripts/validar_projeto.py <workspace>

So le; nunca altera nada. Problemas impedem dar o onboarding por concluido;
avisos mostram-se ao utilizador.

Codigos de saida: 0 sem problemas (pode haver avisos) · 1 problemas · 2 nao ha projeto.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

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


def ambiente_de(url: str, ambientes: dict) -> str | None:
    """Nome do ambiente declarado que contem o URL (mesmo esquema e anfitriao, caminho por baixo)."""
    u = urlsplit(str(url))
    for nome, base in ambientes.items():
        b = urlsplit(str(base or ""))
        if b.netloc and (u.scheme, u.netloc.lower()) == (b.scheme, b.netloc.lower()):
            caminho = b.path.rstrip("/")
            if not caminho or u.path == caminho or u.path.startswith(caminho + "/"):
                return nome
    return None


def urls_dos_modulos(proj: dict, modulos: list) -> list[tuple[str, str]]:
    out = []
    if "api" in modulos and (proj.get("api") or {}).get("base_url"):
        out.append(("api.base_url", proj["api"]["base_url"]))
    if "acessibilidade" in modulos:
        a = proj.get("acessibilidade") or {}
        if (a.get("login") or {}).get("url"):
            out.append(("acessibilidade.login.url", a["login"]["url"]))
        out += [(f"acessibilidade.paginas[{p.get('nome')}]", p["url"])
                for p in a.get("paginas") or [] if isinstance(p, dict) and p.get("url")]
    return out


def avisos_gitignore(workspace: str, proj: dict, modulos: list) -> list[str]:
    """Ficheiros com segredos ou dados do projeto que o git do workspace tem de ignorar
    (metodologia seccao 4, passo 4). So num workspace dentro de um repositorio git."""
    git = shutil.which("git")
    if not git:
        return []
    dentro = subprocess.run([git, "-C", workspace, "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True, check=False)
    if dentro.returncode != 0:
        return []
    ficheiros = [".env"] + ([".api-sessao.env"] if "api" in modulos else [])         + (["painel.html"] if proj.get("tracker") == "excel" else [])
    falta = [f for f in ficheiros if subprocess.run([git, "-C", workspace, "check-ignore", "-q", f],
                                                    capture_output=True, check=False).returncode != 0]
    return [f"{f} nao esta no .gitignore do workspace (tem segredos ou dados do projeto)" for f in falta]


REGRA_HISTORICO = re.compile(r"^Edit\((/|\./)?historico/\*\*\)$")


def historico_protegido(workspace: str) -> bool:
    """O .claude/settings.json (ou settings.local.json) do workspace proibe editar historico/**."""
    for nome in ("settings.json", "settings.local.json"):
        path = os.path.join(workspace, ".claude", nome)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                deny = ((json.load(f) or {}).get("permissions") or {}).get("deny") or []
        except (ValueError, AttributeError):
            continue
        if any(REGRA_HISTORICO.match(str(r).strip()) for r in deny):
            return True
    return False


def validar(workspace: str) -> tuple[list[str], list[str]]:
    with open(os.path.join(workspace, "projeto.yaml"), encoding="utf-8-sig") as f:
        proj = yaml.safe_load(f) or {}
    problemas, avisos = [], []

    for campo in ("projeto", "tracker", "formato_bug"):
        if not proj.get(campo):
            problemas.append(f"campo '{campo}' vazio")
    ambientes = proj.get("ambientes") or {}
    if not ambientes:
        problemas.append("'ambientes' vazio: e preciso pelo menos o ambiente de testes")
    elif not isinstance(ambientes, dict):
        problemas.append("'ambientes' tem de ser um mapa nome -> URL")
        ambientes = {}
    for nome, url in ambientes.items():
        partes = urlsplit(str(url or "").strip())
        if not str(url or "").strip():
            problemas.append(f"ambiente '{nome}' sem URL")
        elif partes.scheme not in ("http", "https") or not partes.netloc:
            problemas.append(f"ambiente '{nome}': {url!r} nao e um URL http(s)://")
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
        # all: um campo com valor por omissao no template (ex.: mobile.flows) nao conta como preenchida
        if secao and m not in modulos and all((proj.get(secao) or {}).get(c) for c in obrig):
            avisos.append(f"seccao {secao}: preenchida, mas o modulo '{m}' nao esta em 'modulos'")

    # URLs dos modulos ativos: so de ambientes declarados, nunca de producao (tecnica-browser.md seccao 2)
    for campo_url, url in urls_dos_modulos(proj, modulos):
        amb = ambiente_de(url, ambientes)
        if amb is None:
            problemas.append(f"{campo_url} ({url}) fora dos ambientes declarados em 'ambientes'")
        elif "prod" in str(amb).lower():
            problemas.append(f"{campo_url} ({url}) aponta para o ambiente '{amb}' (producao?)")

    avisos += avisos_gitignore(workspace, proj, modulos)
    if not historico_protegido(workspace):
        avisos.append("historico/ sem protecao: falta Edit(/historico/**) em permissions.deny no .claude/settings.json "
                      "do workspace (copiar <qa>/docs/workspace/settings.json; metodologia seccao 13)")

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
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
