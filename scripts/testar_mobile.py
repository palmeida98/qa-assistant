"""
Corre os flows Maestro de um workspace e liga cada resultado ao caso de teste
(tecnica-mobile.md; plano, tarefa 3.3).

    python scripts/testar_mobile.py <workspace> [flow ...] [--saida runs/mobile_<data>.json]

- A configuracao vem da secao mobile: do projeto.yaml (plataforma, pasta dos
  flows, variaveis). Sem flows indicados, corre todos os CT-*.yaml da pasta.
- Cada flow indicado e um caminho (a partir da pasta atual, do workspace ou da
  pasta dos flows) ou o ID do caso (CT-MOB-003 -> o unico CT-MOB-003_*.yaml da
  pasta). Um flow que nao se encontra e erro de uso (codigo 2, nada corrido),
  nunca um caso Blocked.
- O caso e o prefixo do nome do ficheiro do flow (CT-MOB-003_carrinho.yaml ->
  CT-MOB-003).
- Credenciais: cada variavel listada em mobile.variaveis vai do .env para uma
  variavel de ambiente MAESTRO_<VAR>, que o flow le como ${MAESTRO_<VAR>};
  nunca pela linha de comandos. As mensagens do relatorio sao redigidas.

Codigos de saida:
  0  flows corridos (os Fail sao resultados, nao erros)
  2  configuracao invalida, flow inexistente ou Maestro por instalar (nada corrido)
  3  sem alvo (nenhum dispositivo/emulador) ou o Maestro nao produziu relatorio:
     os casos ficam Blocked por ambiente
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from comum import Redator, ler_env_workspace, ler_projeto

PLATAFORMAS = ("android", "ios", "web")
CASO = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)")


class Erro(Exception):
    """Configuracao invalida ou ferramenta em falta (codigo 2)."""


def caso_do_flow(ficheiro: str) -> str | None:
    m = CASO.match(os.path.basename(ficheiro))
    return m.group(1) if m else None


def resolver_flows(nomes: list[str], workspace: str, pasta: str) -> list[str]:
    """Caminhos absolutos dos flows indicados (ver o cabecalho). Erro com
    sugestoes se algum nao existir: antes, um nome errado chegava ao Maestro
    e o caso ficava Blocked "o flow nao correu", como se fosse o ambiente."""
    flows, faltam = [], []
    for nome in nomes:
        caminho = next((c for c in (nome, os.path.join(workspace, nome), os.path.join(pasta, nome))
                        if os.path.isfile(c)), None)
        if caminho is None and CASO.fullmatch(nome):
            do_caso = sorted(glob.glob(os.path.join(pasta, glob.escape(nome) + "_*.yaml")))
            caminho = do_caso[0] if len(do_caso) == 1 else None
        if caminho:
            flows.append(os.path.abspath(caminho))
            continue
        # sugestoes: os flows do mesmo caso (apanha gralhas no resto do nome e IDs com varios flows)
        base = os.path.splitext(os.path.basename(nome))[0]
        caso = caso_do_flow(base) or base
        parecidos = sorted(os.path.basename(f) for f in glob.glob(os.path.join(pasta, glob.escape(caso) + "*.yaml")))
        faltam.append(nome + (f" (queria dizer {parecidos}?)" if parecidos else ""))
    if faltam:
        raise Erro(f"flow(s) nao encontrado(s) na pasta atual, no workspace nem em {pasta}: {'; '.join(faltam)}")
    return flows


def configuracao(workspace: str) -> dict:
    try:
        m = ler_projeto(workspace).get("mobile") or {}
    except FileNotFoundError:
        raise Erro(f"nao existe {os.path.join(workspace, 'projeto.yaml')}") from None
    plat = m.get("plataforma")
    if plat not in PLATAFORMAS:
        raise Erro(f"mobile.plataforma tem de ser um de {PLATAFORMAS} (esta: {plat!r})")
    pasta = os.path.join(workspace, m.get("flows") or "flows")
    if not os.path.isdir(pasta):
        raise Erro(f"pasta de flows em falta: {pasta}")
    return {"plataforma": plat, "pasta": pasta, "variaveis": list(m.get("variaveis") or []),
            "timeout": int(m.get("timeout_s") or 900)}


def ambiente_maestro(workspace: str, variaveis: list[str]) -> tuple[dict, dict]:
    """(env para o processo, segredos por valor para redigir)."""
    env_ws = ler_env_workspace(workspace)
    extra, segredos = {}, {}
    for var in variaveis:
        valor = env_ws.get(var) or os.environ.get(var)
        if not valor:
            raise Erro(f"variavel {var} (mobile.variaveis) sem valor no .env do workspace")
        extra[f"MAESTRO_{var}"] = valor
        segredos[var] = valor
    return extra, segredos


def alvo_disponivel(plataforma: str) -> tuple[bool, str]:
    if plataforma == "web":
        return True, "Maestro Web (beta) no Chrome"
    if plataforma == "android":
        adb = shutil.which("adb")
        if not adb:
            return False, "adb nao encontrado (sem Android SDK)"
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, check=False).stdout
        dispositivos = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")]
        return (True, f"android: {', '.join(dispositivos)}") if dispositivos else (False, "nenhum dispositivo/emulador Android ligado")
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return False, "xcrun nao encontrado (iOS so em macOS com Xcode)"
    out = subprocess.run([xcrun, "simctl", "list", "devices", "booted"], capture_output=True, text=True,
                         check=False).stdout
    return ("Booted" in out, "simulador iOS ligado" if "Booted" in out else "nenhum simulador iOS ligado")


def ler_junit(xml_txt: str, red: Redator) -> list[dict]:
    res = []
    for tc in ET.fromstring(xml_txt).iter("testcase"):
        falha = tc.find("failure")
        if falha is None:
            falha = tc.find("error")
        ok = tc.get("status", "SUCCESS") == "SUCCESS" and falha is None
        ficheiro = tc.get("file") or tc.get("name") or ""
        res.append({"caso": caso_do_flow(ficheiro), "flow": ficheiro, "estado_sugerido": "Pass" if ok else "Fail",
                    "mensagem": red.texto((falha.text or falha.get("message") or "").strip()) if falha is not None else "",
                    "tempo_s": float(tc.get("time") or 0)})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Corre flows Maestro e liga os resultados aos casos (tecnica-mobile.md).")
    ap.add_argument("workspace")
    ap.add_argument("flows", nargs="*", help="flows a correr: caminho ou ID do caso (por omissao todos os CT-*.yaml da pasta)")
    ap.add_argument("--saida", help="relatorio JSON (relativo ao workspace, se nao for absoluto)")
    a = ap.parse_args(argv)
    try:
        cfg = configuracao(a.workspace)
        flows = (resolver_flows(a.flows, a.workspace, cfg["pasta"]) if a.flows
                 else sorted(glob.glob(os.path.join(cfg["pasta"], "CT-*.yaml"))))
        if not flows:
            raise Erro(f"nenhum flow CT-*.yaml em {cfg['pasta']}")
        sem_caso = [os.path.basename(f) for f in flows if not caso_do_flow(f)]
        if sem_caso:
            raise Erro(f"flows sem ID de caso no nome: {sem_caso}")
        maestro = shutil.which("maestro")
        if not maestro:
            raise Erro("maestro nao encontrado -- instalar o Maestro CLI")
        extra, segredos = ambiente_maestro(a.workspace, cfg["variaveis"])
    except Erro as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    red = Redator(segredos)
    ok, alvo = alvo_disponivel(cfg["plataforma"])
    rel = {"plataforma": cfg["plataforma"], "alvo": alvo, "ts": datetime.now().isoformat(timespec="seconds")}
    if not ok:
        rel["resultados"] = [{"caso": caso_do_flow(f), "flow": os.path.basename(f), "estado_sugerido": "Blocked",
                              "mensagem": f"sem alvo: {alvo}", "tempo_s": 0} for f in flows]
        codigo = 3
    else:
        with tempfile.TemporaryDirectory() as tmp:
            junit = os.path.join(tmp, "junit.xml")
            try:
                r = subprocess.run([maestro, "test", *flows, "--format", "junit", "--output", junit],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, **extra}, timeout=cfg["timeout"], check=False)
                saida = r.stdout + r.stderr
            except subprocess.TimeoutExpired:
                saida = f"timeout de {cfg['timeout']} s"
            if os.path.isfile(junit):
                with open(junit, encoding="utf-8") as f:
                    rel["resultados"] = ler_junit(f.read(), red)
                codigo = 0
            else:
                rel["resultados"] = []
                rel["erro"] = red.texto(saida.strip()[-500:])
                codigo = 3
        vistos = {os.path.basename(x["flow"]) for x in rel["resultados"]}
        for f in flows:
            if os.path.basename(f) not in vistos:
                rel["resultados"].append({"caso": caso_do_flow(f), "flow": os.path.basename(f),
                                          "estado_sugerido": "Blocked", "mensagem": "o flow nao correu",
                                          "tempo_s": 0})
    print(f"alvo: {rel['alvo']}")
    for x in rel["resultados"]:
        print(f"{x['caso']}: {x['estado_sugerido']}" + (f" -- {x['mensagem']}" if x["mensagem"] else "")
              + f" ({x['tempo_s']:.0f} s)")
    if rel.get("erro"):
        print(f"ERRO do Maestro: {rel['erro']}", file=sys.stderr)
    if a.saida:
        destino = a.saida if os.path.isabs(a.saida) else os.path.join(a.workspace, a.saida)
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(rel, f, ensure_ascii=False, indent=1)
        print(f"relatorio: {destino}")
    return codigo


if __name__ == "__main__":
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
