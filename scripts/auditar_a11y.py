"""
Auditoria automatica de acessibilidade (tecnica-acessibilidade.md secao 2;
plano, tarefas 3.2 e 8.10): as paginas declaradas no projeto.yaml auditadas
pelo motor qa-audit (docs/qa-audit.md secao 11), com o resultado mapeado a
criterios WCAG e nivel (A/AA) no formato do workspace.

    python scripts/auditar_a11y.py <workspace> [--saida runs/a11y_<data>.json]

Precisa do motor instalado uma vez por maquina (pip install -e . e um browser do
Playwright; `qa-audit doctor`). As paginas sem login correm numa execucao
anonima e as com login noutra, depois de um login pelo formulario declarado
(acessibilidade.login). As credenciais vem do .env do workspace e passam ao motor
por variaveis de ambiente, nunca por argumentos nem ficheiros.

Codigos de saida:
  0  auditoria feita (as violacoes sao resultados, nao erros)
  2  configuracao invalida ou motor por instalar (nada corrido)
  3  o browser nao arrancou, o login falhou ou alguma pagina nao foi auditada (Blocked por ambiente)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from urllib.parse import urlsplit

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from comum import ler_env_workspace, ler_projeto

NORMAS = {
    "WCAG 2.0 AA": ["wcag2a", "wcag2aa"],
    "WCAG 2.1 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
    "WCAG 2.2 AA": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
}
BROWSERS = ("chromium", "firefox", "webkit")
NIVEIS = ["A", "AA", "AAA"]
# referencias dos achados do motor (docs/qa-audit.md secao 11)
REF_CRITERIO = re.compile(r"^WCAG (\d+\.\d+\.\d+)$")
REF_NIVEL = re.compile(r"^WCAG (\d)\.(\d) (A{1,3})$")
ELEMENTOS = re.compile(r"^(\d+) element")
# variaveis com as credenciais, so no ambiente do processo do motor
VAR_UTILIZADOR, VAR_PALAVRA_PASSE = "QA_A11Y_UTILIZADOR", "QA_A11Y_PALAVRA_PASSE"
# verificacoes proprias do motor: indicios para a parte manual (tecnica-acessibilidade.md secao 2)
OUTRAS = {"keyboard-trap": ["2.1.2"], "focus-not-visible": ["2.4.7"], "focus-visible": ["2.4.7"],
          "reflow": ["1.4.10"]}


class Erro(Exception):
    """Configuracao invalida ou motor por instalar (codigo 2)."""


def _sem_barra(url: str) -> str:
    return (url or "").split("#")[0].rstrip("/")


def _origem(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def tag_da_referencia(ref: str) -> str | None:
    """'WCAG 2.1 AA' -> 'wcag21aa', 'WCAG 2.0 A' -> 'wcag2a' (as tags do axe)."""
    m = REF_NIVEL.match(ref)
    if not m:
        return None
    return f"wcag{m.group(1)}{'' if m.group(2) == '0' else m.group(2)}{m.group(3).lower()}"


def criterios(refs: list[str]) -> list[str]:
    return [m.group(1) for r in refs or [] if (m := REF_CRITERIO.match(r))]


def nivel(refs: list[str], norma: str) -> str | None:
    """O nivel WCAG mais baixo da regra dentro da norma; None fora da norma (ex.: so best-practice)."""
    no_ambito = set(NORMAS[norma])
    niveis = [tag_da_referencia(r) for r in refs or []]
    niveis = [t for t in niveis if t in no_ambito]
    if not niveis:
        return None
    return min((re.search(r"a+$", t).group(0).upper() for t in niveis), key=NIVEIS.index)


# --- configuracao --------------------------------------------------------------------------------------------

def _sucesso_por_omissao(login_url: str) -> str:
    """Regex que casa com qualquer URL exceto a da pagina de login (com ou sem barra, query ou ancora)."""
    base = re.escape(_sem_barra(login_url.split("?")[0]))
    return rf"^(?!{base}/?(?:[?#].*)?$)"


def configuracao(workspace: str) -> tuple[dict, dict]:
    """(conf, env): conf tem a norma, o browser, os avisos e os grupos de paginas, cada um com a configuracao
    do motor; env tem as credenciais (so quando ha paginas com login) e nunca entra em conf."""
    try:
        a = ler_projeto(workspace).get("acessibilidade") or {}
    except FileNotFoundError:
        raise Erro(f"nao existe {os.path.join(workspace, 'projeto.yaml')}") from None
    paginas = a.get("paginas") or []
    if not paginas:
        raise Erro("acessibilidade.paginas vazio no projeto.yaml")
    for p in paginas:
        if not p.get("nome") or not p.get("url") or urlsplit(p["url"]).scheme not in ("http", "https"):
            raise Erro(f"acessibilidade.paginas: cada pagina precisa de nome e de url http(s) ({p!r})")
    norma = a.get("norma") or "WCAG 2.1 AA"
    if norma not in NORMAS:
        raise Erro(f"acessibilidade.norma {norma!r} nao suportada (validas: {list(NORMAS)})")
    avisos = []
    browser = a.get("browser") or "chromium"
    if browser == "chrome":
        browser = "chromium"
        avisos.append("acessibilidade.browser 'chrome' tratado como 'chromium' (o motor qa-audit usa os browsers "
                      "do Playwright); atualizar o projeto.yaml")
    if browser not in BROWSERS:
        raise Erro(f"acessibilidade.browser {browser!r} nao suportado (validos: {list(BROWSERS)})")
    timeout_ms = int(a.get("timeout_ms") or 30000)

    login = a.get("login") or None
    env, auth = {}, None
    if any(p.get("login") for p in paginas):
        if not login:
            raise Erro("ha paginas com login: true mas falta acessibilidade.login")
        faltam = [k for k in ("url", "campo_utilizador", "campo_palavra_passe", "botao",
                              "var_utilizador", "var_palavra_passe") if not login.get(k)]
        if faltam:
            raise Erro(f"acessibilidade.login incompleto: falta {faltam}")
        vars_ = {**os.environ, **ler_env_workspace(workspace)}
        for k in ("var_utilizador", "var_palavra_passe"):
            if not vars_.get(login[k]):
                raise Erro(f"variavel {login[k]} ({k}) sem valor no .env do workspace")
        env = {VAR_UTILIZADOR: vars_[login["var_utilizador"]], VAR_PALAVRA_PASSE: vars_[login["var_palavra_passe"]]}
        auth = {"method": "form", "login_url": login["url"], "username_env": VAR_UTILIZADOR,
                "pass_env": VAR_PALAVRA_PASSE, "username_selector": login["campo_utilizador"],
                "pass_selector": login["campo_palavra_passe"], "submit_selector": login["botao"]}
        if login.get("sucesso_url") or login.get("sucesso_seletor"):
            if login.get("sucesso_url"):
                auth["success_url"] = login["sucesso_url"]
            if login.get("sucesso_seletor"):
                auth["success_selector"] = login["sucesso_seletor"]
        else:
            auth["success_url"] = _sucesso_por_omissao(login["url"])

    # um grupo por (login, origem): o motor so visita a origem do URL inicial e faz o login uma vez
    grupos: dict[tuple, list] = {}
    for p in paginas:
        grupos.setdefault((bool(p.get("login")), _origem(p["url"])), []).append(
            {"nome": p["nome"], "url": p["url"], "login": bool(p.get("login"))})
    lista = []
    for (com_login, _), pags in grupos.items():
        motor = {
            "browser": {"engine": browser, "timeout_ms": timeout_ms},
            "crawl": {"pages": [p["url"] for p in pags[1:]], "follow_links": False, "max_pages": len(pags),
                      "max_depth": 0, "audit_error_pages": bool(a.get("auditar_paginas_com_erro_http"))},
            "evidence": {"screenshots": "off", "video": False, "trace": "off"},
            "output": {"formats": ["json"], "cleanup": False},
            "fail_on": "none",
            "modules": {"accessibility": {"tags": NORMAS[norma] + ["best-practice"]}},
        }
        if com_login:
            motor["auth"] = auth
        lista.append({"login": com_login, "alvo": pags[0]["url"], "paginas": pags, "motor": motor})
    return {"norma": norma, "browser": browser, "avisos": avisos, "grupos": lista}, env


# --- motor ---------------------------------------------------------------------------------------------------

def motor_instalado() -> bool:
    r = subprocess.run([sys.executable, "-c", "import qa_audit.cli"], capture_output=True, check=False)
    return r.returncode == 0


def correr_motor(grupo: dict, env: dict, pasta: str) -> dict:
    """Corre o qa-audit so com o modulo de acessibilidade. Devolve {"codigo", "resultado" | None, "erro"}."""
    cfg = os.path.join(pasta, "qa-audit.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        yaml.safe_dump(grupo["motor"], f, allow_unicode=True, sort_keys=False)
    saida = os.path.join(pasta, "saida")
    cmd = [sys.executable, "-m", "qa_audit.cli", grupo["alvo"], "--config", cfg, "--type", "accessibility",
           "--output", saida, "--report", "json", "--fail-on", "none", "--no-color"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"}, timeout=1800, check=False)
    ficheiros = glob.glob(os.path.join(saida, "*", "results.json"))
    resultado = None
    if ficheiros:
        with open(ficheiros[0], encoding="utf-8") as f:
            resultado = json.load(f)
    linhas = [ln.strip() for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
    return {"codigo": r.returncode, "resultado": resultado, "erro": linhas[-1][:300] if linhas else "sem saida"}


# --- classificacao -------------------------------------------------------------------------------------------

def _pagina_do_motor(pag: dict, resultado: dict | None) -> dict | None:
    for info in (resultado or {}).get("pages", []):
        if _sem_barra(info.get("url")) == _sem_barra(pag["url"]):
            return info
    return None


def _elementos(f: dict) -> int:
    m = ELEMENTOS.match(f.get("actual") or "")
    return int(m.group(1)) if m else sum(1 for e in f.get("evidence") or [] if e.get("kind") == "html")


def _regra(f: dict, norma: str) -> dict:
    refs = f.get("references") or []
    return {"regra": f["check"].split("/", 1)[1], "criterios": criterios(refs), "nivel": nivel(refs, norma),
            "severidade_sugerida": f.get("severity"), "ajuda": f.get("title"),
            "ajuda_url": next((r for r in refs if r.startswith("http")), None), "elementos": _elementos(f),
            "exemplos": [{"alvo": e.get("label"), "html": e.get("value")}
                         for e in (f.get("evidence") or []) if e.get("kind") == "html"][:3]}


def classificar(execucoes: list[dict], norma: str) -> dict:
    """execucoes: [{"grupo", "codigo", "resultado", "erro"}]. Separa, por pagina, violacoes WCAG (dentro da
    norma), boas praticas, incompletos e as outras verificacoes do motor."""
    paginas, notas, browser = [], [], None
    for ex in execucoes:
        res = ex.get("resultado")
        if res:
            browser = browser or " ".join(x for x in (res.get("browser", {}).get("engine"),
                                                      res.get("browser", {}).get("version")) if x)
            notas += [n for n in res.get("notes", []) if n not in notas]
        saltadas = {_sem_barra(s.get("url")): s.get("reason") for s in (res or {}).get("crawl", {}).get("skipped", [])}
        for pag in ex["grupo"]["paginas"]:
            out = {"nome": pag["nome"], "url": pag["url"], "url_final": None, "titulo": None, "erro": None,
                   "login": pag["login"], "violacoes_wcag": [], "boas_praticas": [], "incompletos": [],
                   "outras_verificacoes": []}
            paginas.append(out)
            info = _pagina_do_motor(pag, res)
            if res is None or (ex["codigo"] in (2, 3, 4) and info is None):
                motivo = {2: "configuracao recusada pelo motor", 3: "alvo inacessivel ou login falhado",
                          4: "o browser nao arrancou"}.get(ex["codigo"], "o motor nao correu")
                out["erro"] = f"{motivo}: {ex.get('erro')}"
                continue
            if info is None:
                out["erro"] = ("nao visitada: " + saltadas[_sem_barra(pag["url"])] if _sem_barra(pag["url"]) in saltadas
                               else "nao visitada pelo motor (sessao terminada antes?)")
                continue
            out["url_final"], out["titulo"] = info.get("final_url"), info.get("title")
            if info.get("error"):
                out["erro"] = info["error"]
                continue
            if _sem_barra(info.get("final_url")) != _sem_barra(pag["url"]):
                # ex.: sessao perdida -> o site devolve a pagina de login; auditar essa daria um falso Pass
                out["erro"] = f"redirecionada para {info.get('final_url')} (login falhado ou sessao em falta?)"
                continue
            if not info.get("audited"):
                out["erro"] = "o motor visitou a pagina mas nao a auditou"
                continue
            urls = {_sem_barra(info.get("url")), _sem_barra(info.get("final_url"))}
            for f in res.get("findings", []):
                if f.get("module") != "accessibility" or _sem_barra(f.get("url")) not in urls:
                    continue
                if f["check"] == "axe-not-run":
                    out["erro"] = f"axe-core nao correu nesta pagina: {f.get('description')}"
                elif f["check"].startswith("axe/"):
                    r = _regra(f, norma)
                    if f["status"] == "needs-human":
                        out["incompletos"].append({k: r[k] for k in ("regra", "criterios", "elementos")})
                    elif r["nivel"]:
                        out["violacoes_wcag"].append(r)
                    else:
                        out["boas_praticas"].append(r)
                elif f["check"] in OUTRAS:
                    out["outras_verificacoes"].append({
                        "verificacao": f["check"], "criterios": OUTRAS[f["check"]], "estado": f["status"],
                        "resumo": f.get("actual") or f.get("title"),
                        "seletor": (f.get("element") or {}).get("selector")})
    return {"norma": norma, "browser": browser, "notas": notas, "paginas": paginas}


def resumo_texto(rel: dict) -> list[str]:
    linhas = []
    for p in rel["paginas"]:
        if p.get("erro"):
            linhas.append(f"{p['nome']}: NAO AUDITADA -- {p['erro']}")
            continue
        v = p["violacoes_wcag"]
        det = "; ".join(f"{x['regra']} [{', '.join(x['criterios']) or '-'} {x['nivel'] or ''}] x{x['elementos']}"
                        for x in v)
        outras = "".join(f" | {o['verificacao']} [{', '.join(o['criterios'])}] a confirmar"
                         for o in p["outras_verificacoes"])
        linhas.append(f"{p['nome']}: {len(v)} violacao(oes) WCAG" + (f" -- {det}" if det else "")
                      + f" | {len(p['boas_praticas'])} boa(s) pratica(s) | {len(p['incompletos'])} incompleto(s)"
                      + outras)
    return linhas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auditoria automatica de acessibilidade (motor qa-audit).")
    ap.add_argument("workspace")
    ap.add_argument("--saida", help="grava o relatorio JSON (relativo ao workspace, se nao for absoluto)")
    a = ap.parse_args(argv)
    try:
        conf, env = configuracao(a.workspace)
        if not motor_instalado():
            raise Erro("motor qa-audit por instalar: pip install -e . (a partir da raiz do qa-assistant) "
                       "e depois qa-audit doctor")
    except Erro as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    for aviso in conf["avisos"]:
        print(f"AVISO: {aviso}", file=sys.stderr)
    execucoes = []
    # a configuracao e o relatorio do motor sao temporarios: so fica o relatorio do workspace
    with tempfile.TemporaryDirectory(prefix="qa-a11y-") as tmp:
        for i, grupo in enumerate(conf["grupos"]):
            pasta = os.path.join(tmp, str(i))
            os.makedirs(pasta)
            try:
                ex = correr_motor(grupo, env if grupo["login"] else {}, pasta)
            except subprocess.TimeoutExpired:
                ex = {"codigo": 4, "resultado": None, "erro": "o motor excedeu 30 minutos"}
            if ex["codigo"] == 2 and ex["resultado"] is None:
                print(f"ERRO: o motor recusou a configuracao gerada -- {ex['erro']}", file=sys.stderr)
                return 2
            execucoes.append({"grupo": grupo, **ex})
    rel = classificar(execucoes, conf["norma"])
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
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
