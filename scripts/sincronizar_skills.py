"""
Sincroniza as skills do repositorio (.claude/skills/) com a copia global
(~/.claude/skills/), que e a que o Claude Code carrega nos workspaces: um
workspace vive fora deste repositorio e nao ve as skills de projeto daqui.

    python scripts/sincronizar_skills.py            (so verifica; nunca escreve)
    python scripts/sincronizar_skills.py --aplicar  (copia do repositorio para a global)

A fonte de verdade e sempre o repositorio: a copia global nunca volta para
aqui. Diferencas so nos fins de linha (CRLF/LF) nao contam. Skills globais que
nao existem no repositorio (de outras origens) sao ignoradas.

Codigos de saida: 0 sincronizadas (ou copiadas com --aplicar) · 1 ha diferencas
por aplicar · 2 sem skills no repositorio
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGEM = os.path.join(ROOT, ".claude", "skills")
DESTINO = os.path.join(os.path.expanduser("~"), ".claude", "skills")


def _texto(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def comparar(origem: str = ORIGEM, destino: str = DESTINO) -> dict[str, str]:
    """{skill: "igual" | "diferente" | "em falta"} para cada skill do repositorio."""
    estado = {}
    for nome in sorted(os.listdir(origem)):
        fonte = os.path.join(origem, nome, "SKILL.md")
        if not os.path.isfile(fonte):
            continue
        copia = _texto(os.path.join(destino, nome, "SKILL.md"))
        estado[nome] = "em falta" if copia is None else ("igual" if copia == _texto(fonte) else "diferente")
    return estado


def aplicar(estado: dict[str, str], origem: str = ORIGEM, destino: str = DESTINO) -> list[str]:
    copiadas = []
    for nome, e in estado.items():
        if e != "igual":
            os.makedirs(os.path.join(destino, nome), exist_ok=True)
            shutil.copyfile(os.path.join(origem, nome, "SKILL.md"), os.path.join(destino, nome, "SKILL.md"))
            copiadas.append(nome)
    return copiadas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sincroniza .claude/skills/ com ~/.claude/skills/.")
    ap.add_argument("--aplicar", action="store_true", help="copia as skills diferentes ou em falta")
    ap.add_argument("--destino", default=DESTINO, help="pasta das skills globais (por omissao ~/.claude/skills)")
    a = ap.parse_args(argv)
    if not os.path.isdir(ORIGEM):
        print(f"ERRO: nao existe {ORIGEM}", file=sys.stderr)
        return 2
    estado = comparar(ORIGEM, a.destino)
    if not estado:
        print(f"ERRO: nenhuma skill em {ORIGEM}", file=sys.stderr)
        return 2
    for nome, e in estado.items():
        print(f"{e:9}  {nome}")
    por_aplicar = [n for n, e in estado.items() if e != "igual"]
    if not por_aplicar:
        print(f"{len(estado)} skill(s) sincronizadas com {a.destino}")
        return 0
    if not a.aplicar:
        print(f"{len(por_aplicar)} skill(s) por sincronizar: correr com --aplicar")
        return 1
    copiadas = aplicar(estado, ORIGEM, a.destino)
    print(f"copiadas para {a.destino}: {', '.join(copiadas)}")
    return 0


if __name__ == "__main__":
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
