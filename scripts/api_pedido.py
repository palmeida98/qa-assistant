"""
Um pedido HTTP com evidencia redigida (tecnica-api.md; plano, tarefa 3.1).

Substitui {{VARIAVEL}} (URL, cabecalhos, corpo) pelos valores do .env do
workspace e da sessao de API, faz o pedido e imprime a evidencia em JSON com
os segredos redigidos: cabecalhos de autenticacao, chaves JSON com token /
password / secret / api_key no nome, e o valor literal de qualquer variavel
do .env ou segredo da sessao. Nenhum segredo chega a saida.

    python scripts/api_pedido.py <workspace> <METODO> <url|caminho>
        [-H "Nome: valor"]... [--json '{...}' | --json-file f.json]
        [--esperado 201] [--guardar campo=NOME]... [--guardar-segredo campo=NOME]...
        [--saida runs/evidencia/<caso>.json] [--timeout 30]

- Um caminho relativo usa api.base_url do projeto.yaml. No Git Bash (Windows)
  escrever sem a barra inicial (booking/1): o shell converte /booking num
  caminho do Windows antes de o script o ver.
- --guardar / --guardar-segredo guardam um campo da resposta JSON (ex. token,
  bookingid, 0.bookingid) em <workspace>/.api-sessao.env, para os pedidos
  seguintes usarem {{NOME}}. Os segredos ficam sempre redigidos na saida.
  O .api-sessao.env tem de estar no .gitignore do workspace.

Codigos de saida:
  0  pedido feito (e, com --esperado, codigo igual)
  1  pedido feito, mas o codigo e diferente de --esperado
  2  uso ou configuracao invalidos (nada foi pedido)
  3  sem resposta (timeout, ligacao recusada, DNS, TLS): Blocked por ambiente
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import yaml

SESSAO = ".api-sessao.env"
REDIGIDO = "«redigido»"
CABECALHOS_SECRETOS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                       "x-auth-token"}
CHAVE_SECRETA = re.compile(r"token|password|passwd|secret|api[_-]?key", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class Erro(Exception):
    """Uso ou configuracao invalidos (codigo 2)."""


def ler_env(path: str) -> dict[str, str]:
    """KEY=VALUE por linha; ignora comentarios, linhas vazias e 'export '."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, _, v = linha.removeprefix("export ").partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    return out


def ler_sessao(workspace: str) -> tuple[dict[str, str], set[str]]:
    """Valores guardados e o conjunto dos que sao segredos (linhas '#segredo NOME')."""
    path = os.path.join(workspace, SESSAO)
    valores = ler_env(path)
    segredos = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            segredos = {m.group(1) for m in re.finditer(r"^#segredo (\w+)$", f.read(), re.MULTILINE)}
    return valores, segredos


def guardar_sessao(workspace: str, nome: str, valor: str, segredo: bool) -> None:
    valores, segredos = ler_sessao(workspace)
    valores[nome] = valor
    (segredos.add if segredo else segredos.discard)(nome)
    with open(os.path.join(workspace, SESSAO), "w", encoding="utf-8", newline="\n") as f:
        f.write("# sessao de API do qa-assistant (scripts/api_pedido.py) -- NAO vai para o git\n")
        f.writelines(f"#segredo {k}\n" for k in sorted(segredos))
        f.writelines(f"{k}={v}\n" for k, v in valores.items())


def substituir(texto: str, vars_: dict[str, str]) -> str:
    def troca(m):
        if m.group(1) not in vars_:
            raise Erro(f"variavel {{{{{m.group(1)}}}}} nao definida no .env nem na sessao de API")
        return vars_[m.group(1)]
    return PLACEHOLDER.sub(troca, texto)


class Redator:
    """Redige segredos: por nome de cabecalho, por nome de chave JSON e por valor."""

    def __init__(self, segredos_por_valor: dict[str, str]):
        # valores mais compridos primeiro, para um segredo que contem outro ser trocado inteiro
        self.valores = sorted(((v, k) for k, v in segredos_por_valor.items() if v), key=lambda x: -len(x[0]))

    def texto(self, s: str) -> str:
        for valor, nome in self.valores:
            s = s.replace(valor, f"«{nome}»")
        return s

    def cabecalhos(self, h: dict) -> dict:
        return {k: (REDIGIDO if k.lower() in CABECALHOS_SECRETOS else self.texto(str(v))) for k, v in h.items()}

    def json(self, obj):
        if isinstance(obj, dict):
            return {k: (REDIGIDO if CHAVE_SECRETA.search(str(k)) and v not in (None, "") else self.json(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.json(x) for x in obj]
        if isinstance(obj, str):
            return self.texto(obj)
        return obj

    def corpo(self, texto: str | None, max_chars: int):
        if texto in (None, ""):
            return texto
        try:
            return self.json(json.loads(texto))
        except ValueError:
            t = self.texto(texto)
            return t if len(t) <= max_chars else t[:max_chars] + f"… (+{len(t) - max_chars} caracteres)"


def campo(obj, caminho: str):
    """'token', 'booking.firstname', '0.bookingid'."""
    for parte in caminho.split("."):
        if isinstance(obj, list) and parte.isdigit() and int(parte) < len(obj):
            obj = obj[int(parte)]
        elif isinstance(obj, dict) and parte in obj:
            obj = obj[parte]
        else:
            raise Erro(f"campo '{caminho}' nao existe na resposta")
    return obj


def base_url(workspace: str) -> str | None:
    path = os.path.join(workspace, "projeto.yaml")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        proj = yaml.safe_load(f) or {}
    return ((proj.get("api") or {}).get("base_url") or "").rstrip("/") or None


def pedido(a) -> tuple[int, dict]:
    env = {**ler_env(os.path.join(a.workspace, ".env"))}
    sessao, segredos_sessao = ler_sessao(a.workspace)
    vars_ = {**os.environ, **env, **sessao}
    # redigir por valor: tudo o que vem do .env (>= 4 caracteres) e os segredos da sessao
    por_valor = {k: v for k, v in env.items() if len(v) >= 4}
    por_valor.update({k: sessao[k] for k in segredos_sessao if k in sessao})
    red = Redator(por_valor)

    url = substituir(a.url, vars_)
    if re.match(r"^[A-Za-z]:[/\\]", url):
        # o Git Bash (MSYS) converte argumentos como /booking em C:/Program Files/Git/booking
        raise Erro(f"o caminho chegou convertido pelo shell ({red.texto(url)}): escrever o caminho sem a "
                   "barra inicial (booking/1) ou correr com MSYS_NO_PATHCONV=1")
    if not re.match(r"^https?://", url):  # caminho relativo a api.base_url, com ou sem barra inicial
        base = base_url(a.workspace)
        if not base:
            raise Erro(f"caminho relativo ({red.texto(url)}) sem api.base_url no projeto.yaml do workspace")
        url = base + "/" + url.lstrip("/")

    cab = {}
    for h in a.H:
        nome, sep, valor = h.partition(":")
        if not sep:
            raise Erro(f"cabecalho sem ':' -- {h!r}")
        cab[nome.strip()] = substituir(valor.strip(), vars_)
    corpo = None
    if a.json_file:
        with open(a.json_file, encoding="utf-8-sig") as f:
            a.json = f.read()
    if a.json is not None:
        corpo = substituir(a.json, vars_)
        try:
            json.loads(corpo)
        except ValueError as e:
            raise Erro(f"--json invalido: {e}") from None
        cab.setdefault("Content-Type", "application/json")
    cab.setdefault("Accept", "application/json")

    req = urllib.request.Request(url, data=corpo.encode("utf-8") if corpo is not None else None,
                                 headers=cab, method=a.metodo.upper())
    evid = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "pedido": {"metodo": a.metodo.upper(), "url": red.texto(url), "cabecalhos": red.cabecalhos(cab),
                   "corpo": red.corpo(corpo, a.max_corpo)},
    }
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as r:
            codigo, cab_r, texto = r.status, dict(r.headers), r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # 4xx/5xx: e uma resposta, nao uma falha de ambiente
        with e:
            codigo, cab_r, texto = e.code, dict(e.headers), e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        evid["erro"] = f"sem resposta: {red.texto(str(getattr(e, 'reason', e)))}"
        return 3, evid
    evid["resposta"] = {"codigo": codigo, "tempo_ms": round((time.perf_counter() - t0) * 1000),
                        "cabecalhos": red.cabecalhos({k: v for k, v in cab_r.items()
                                                      if k.lower() in {"content-type", "location"}
                                                      or k.lower() in CABECALHOS_SECRETOS}),
                        "corpo": red.corpo(texto, a.max_corpo)}

    guardar = [(g, False) for g in a.guardar] + [(g, True) for g in a.guardar_segredo]
    if guardar:
        try:
            dados = json.loads(texto)
        except ValueError:
            raise Erro("--guardar precisa de uma resposta JSON") from None
        evid["guardado"] = {}
        for g, segredo in guardar:
            caminho, sep, nome = g.partition("=")
            if not sep or not re.match(r"^[A-Za-z_]\w*$", nome):
                raise Erro(f"--guardar espera campo=NOME -- {g!r}")
            valor = str(campo(dados, caminho))
            guardar_sessao(a.workspace, nome, valor, segredo)
            evid["guardado"][nome] = f"{REDIGIDO} ({len(valor)} caracteres)" if segredo else valor

    if a.esperado is not None:
        evid["esperado"] = a.esperado
        evid["confere"] = codigo == a.esperado
        return (0 if evid["confere"] else 1), evid
    return 0, evid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pedido HTTP com evidencia redigida (tecnica-api.md).")
    ap.add_argument("workspace")
    ap.add_argument("metodo")
    ap.add_argument("url")
    ap.add_argument("-H", action="append", default=[], metavar="'Nome: valor'")
    ap.add_argument("--json")
    ap.add_argument("--json-file")
    ap.add_argument("--esperado", type=int)
    ap.add_argument("--guardar", action="append", default=[], metavar="campo=NOME")
    ap.add_argument("--guardar-segredo", action="append", default=[], metavar="campo=NOME")
    ap.add_argument("--saida", help="grava tambem a evidencia neste ficheiro JSON (relativo ao workspace, se nao for absoluto)")
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--max-corpo", type=int, default=4000)
    a = ap.parse_args(argv)
    try:
        rc, evid = pedido(a)
    except Erro as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    saida = json.dumps(evid, ensure_ascii=False, indent=1)
    print(saida)
    if a.saida:
        destino = a.saida if os.path.isabs(a.saida) else os.path.join(a.workspace, a.saida)
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            f.write(saida + "\n")
    return rc


if __name__ == "__main__":
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
