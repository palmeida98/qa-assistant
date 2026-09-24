"""
Auditoria automatica de acessibilidade (tecnica-acessibilidade.md secao 2;
plano, tarefa 3.2): axe-core via Playwright nas paginas declaradas no
projeto.yaml, com o resultado mapeado a criterios WCAG e nivel (A/AA).

    python scripts/auditar_a11y.py <workspace> [--saida runs/a11y_<data>.json]

Precisa de `npm ci --prefix tools/a11y` uma vez por maquina. As credenciais do
login (acessibilidade.login no projeto.yaml) vem do .env do workspace e passam
ao browser por variaveis de ambiente, nunca por argumentos.

Codigos de saida:
  0  auditoria feita (as violacoes sao resultados, nao erros)
  2  configuracao invalida ou ferramenta por instalar (nada corrido)
  3  o browser nao arrancou ou alguma pagina nao abriu (Blocked por ambiente)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from api_pedido import ler_env

FERRAMENTA = os.path.join(ROOT, "tools", "a11y")
NORMAS = {
    "WCAG 2.0 AA": ["wcag2a", "wcag2aa"],
    "WCAG 2.1 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    "WCAG 2.2 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
}
NIVEL = {"wcag2a": "A", "wcag21a": "A", "wcag2aa": "AA", "wcag21aa": "AA", "wcag22aa": "AA",
         "wcag2aaa": "AAA", "wcag21aaa": "AAA"}
CRITERIO = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")  # wcag143 -> 1.4.3, wcag1410 -> 1.4.10


class Erro(Exception):
    """Configuracao invalida ou ferramenta por instalar (codigo 2)."""


def criterios(tags: list[str]) -> list[str]:
    return [".".join(m.groups()) for t in tags if (m := CRITERIO.match(t))]


def nivel(tags: list[str]) -> str | None:
    niveis = [NIVEL[t] for t in tags if t in NIVEL]
    return min(niveis, key=["A", "AA", "AAA"].index) if niveis else None


def _sem_barra(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def classificar(bruto: dict, norma: str) -> dict:
    """Separa, por pagina, violacoes WCAG (dentro da norma), boas praticas e incompletos."""
    no_ambito = set(NORMAS[norma])
    paginas = []
    for p in bruto.get("paginas", []):
        pag = {k: p.get(k) for k in ("nome", "url", "url_final", "titulo", "erro")}
        if not pag["erro"] and pag["url_final"] and _sem_barra(pag["url_final"]) != _sem_barra(pag["url"]):
            # ex.: login falhado -> o site devolve a pagina de login; auditar essa daria um falso Pass
            pag["erro"] = f"redirecionada para {pag['url_final']} (login falhado ou sessao em falta?)"
            p = {**p, "violacoes": [], "incompletos": []}
        pag["violacoes_wcag"], pag["boas_praticas"] = [], []
        for v in p.get("violacoes", []):
            item = {"regra": v["id"], "criterios": criterios(v["tags"]), "nivel": nivel(v["tags"]),
                    "impacto": v.get("impacto"), "ajuda": v.get("ajuda"), "ajuda_url": v.get("ajuda_url"),
                    "elementos": v.get("elementos"), "exemplos": v.get("exemplos", [])}
            (pag["violacoes_wcag"] if no_ambito & set(v["tags"]) else pag["boas_praticas"]).append(item)
        pag["incompletos"] = [{"regra": v["id"], "criterios": criterios(v["tags"]), "elementos": v.get("elementos")}
                              for v in p.get("incompletos", [])]
        pag["passaram"] = p.get("passaram")
        paginas.append(pag)
    return {"norma": norma, "browser": bruto.get("browser"), "paginas": paginas}


def resumo_texto(rel: dict) -> list[str]:
    linhas = []
    for p in rel["paginas"]:
        if p.get("erro"):
            linhas.append(f"{p['nome']}: NAO AUDITADA -- {p['erro']}")
            continue
        v = p["violacoes_wcag"]
        det = "; ".join(f"{x['regra']} [{', '.join(x['criterios']) or '-'} {x['nivel'] or ''}] x{x['elementos']}"
                        for x in v)
        linhas.append(f"{p['nome']}: {len(v)} violacao(oes) WCAG" + (f" -- {det}" if det else "")
                      + f" | {len(p['boas_praticas'])} boa(s) pratica(s) | {len(p['incompletos'])} incompleto(s)")
    return linhas


def configuracao(workspace: str) -> tuple[dict, dict]:
    path = os.path.join(workspace, "projeto.yaml")
    if not os.path.isfile(path):
        raise Erro(f"nao existe {path}")
    with open(path, encoding="utf-8-sig") as f:
        a = (yaml.safe_load(f) or {}).get("acessibilidade") or {}
    paginas = a.get("paginas") or []
    if not paginas:
        raise Erro("acessibilidade.paginas vazio no projeto.yaml")
    norma = a.get("norma") or "WCAG 2.1 AA"
    if norma not in NORMAS:
        raise Erro(f"acessibilidade.norma {norma!r} nao suportada (validas: {list(NORMAS)})")
    login = a.get("login") or None
    env = {}
    if any(p.get("login") for p in paginas):
        if not login:
            raise Erro("ha paginas com login: true mas falta acessibilidade.login")
        faltam = [k for k in ("url", "campo_utilizador", "campo_palavra_passe", "botao",
                              "var_utilizador", "var_palavra_passe") if not login.get(k)]
        if faltam:
            raise Erro(f"acessibilidade.login incompleto: falta {faltam}")
        vars_ = {**os.environ, **ler_env(os.path.join(workspace, ".env"))}
        for k in ("var_utilizador", "var_palavra_passe"):
            if not vars_.get(login[k]):
                raise Erro(f"variavel {login[k]} ({k}) sem valor no .env do workspace")
        env = {"QA_A11Y_UTILIZADOR": vars_[login["var_utilizador"]],
               "QA_A11Y_PALAVRA_PASSE": vars_[login["var_palavra_passe"]]}
    cfg = {"browser": a.get("browser") or "chrome", "tags": NORMAS[norma] + ["best-practice"],
           "timeout_ms": int(a.get("timeout_ms") or 30000),
           "login": {k: login[k] for k in ("url", "campo_utilizador", "campo_palavra_passe", "botao")}
           if env else None,
           "paginas": [{"nome": p["nome"], "url": p["url"], "login": bool(p.get("login"))} for p in paginas]}
    return {"cfg": cfg, "norma": norma}, env


def correr_axe(cfg: dict, env: dict) -> dict:
    node = shutil.which("node")
    if not node:
        raise Erro("node nao encontrado -- instalar o Node.js")
    if not os.path.isdir(os.path.join(FERRAMENTA, "node_modules", "@axe-core")):
        raise Erro("ferramenta por instalar: npm ci --prefix tools/a11y (a partir da raiz do qa-assistant)")
    r = subprocess.run([node, os.path.join(FERRAMENTA, "axe_scan.mjs")], input=json.dumps(cfg),
                       capture_output=True, text=True, encoding="utf-8", env={**os.environ, **env},
                       cwd=FERRAMENTA, timeout=600, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError((r.stderr or "sem saida").strip().splitlines()[-1][:300])
    return json.loads(r.stdout)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auditoria automatica de acessibilidade (axe-core).")
    ap.add_argument("workspace")
    ap.add_argument("--saida", help="grava o relatorio JSON (relativo ao workspace, se nao for absoluto)")
    a = ap.parse_args(argv)
    try:
        conf, env = configuracao(a.workspace)
        bruto = correr_axe(conf["cfg"], env)
    except Erro as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"BLOCKED: o browser nao correu -- {e}", file=sys.stderr)
        return 3
    rel = classificar(bruto, conf["norma"])
    rel["ts"] = datetime.now().isoformat(timespec="seconds")
    for linha in resumo_texto(rel):
        print(linha)
    if a.saida:
        destino = a.saida if os.path.isabs(a.saida) else os.path.join(a.workspace, a.saida)
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(rel, f, ensure_ascii=False, indent=1)
        print(f"relatorio: {destino}")
    return 3 if any(p.get("erro") for p in rel["paginas"]) else 0


if __name__ == "__main__":
    sys.exit(main())
