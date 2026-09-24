"""
Codigo comum aos scripts de um workspace (plano 7.8): leitura do projeto.yaml
e do .env, e redacao de segredos na evidencia. So leitura; nada aqui escreve
no workspace.

Usado por api_pedido.py, auditar_a11y.py, testar_mobile.py, gerar_painel.py e
validar_projeto.py (a partir da pasta scripts/, que esta no sys.path deles).
"""

from __future__ import annotations

import json
import os
import re

import yaml

REDIGIDO = "«redigido»"
CABECALHOS_SECRETOS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                       "x-auth-token"}
CHAVE_SECRETA = re.compile(r"token|password|passwd|secret|api[_-]?key", re.IGNORECASE)


def ler_projeto(workspace: str) -> dict:
    """O projeto.yaml do workspace ({} se estiver vazio). FileNotFoundError se nao existir."""
    with open(os.path.join(workspace, "projeto.yaml"), encoding="utf-8-sig") as f:
        return yaml.safe_load(f) or {}


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


def ler_env_workspace(workspace: str) -> dict[str, str]:
    """O .env do workspace (vazio se nao existir)."""
    return ler_env(os.path.join(workspace, ".env"))


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
