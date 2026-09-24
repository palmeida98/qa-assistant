"""
Reconstroi o workbook do demo: gera-o de novo (gerar_workbook.py) e reaplica,
pela ordem de execucao (1.o ts de cada lote), todos os lotes de runs/ (lote_*.json)
pelo adaptador.

Serve para acrescentar areas ou casos ao demo sem perder os resultados ja
registados: o workbook e sempre o gerador + os lotes aplicados. A etiqueta de
cada lote e derivada do nome do ficheiro (lote_AAAAMMDD_<id> ->
"[AAAA-MM-DD, lote_AAAAMMDD_<id>]"), por isso a reconstrucao e deterministica;
so a data dos defeitos novos e a do dia da reconstrucao.

    python examples/demo/reconstruir_workbook.py      (a partir da raiz do qa-assistant)

Sai com o primeiro codigo diferente de 0 do aplicar (tecnica-excel.md secao 1).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [AQUI, os.path.dirname(os.path.dirname(AQUI))]

import gerar_workbook

from adapters.excel.aplicar import main as aplicar


def etiqueta(nome: str) -> str:
    m = re.match(r"^lote_(\d{4})(\d{2})(\d{2})_", nome)
    return f"[{m.group(1)}-{m.group(2)}-{m.group(3)}, {nome}]" if m else f"[{nome}]"


def ordem(path: str) -> tuple[str, str]:
    """Ordem de aplicacao = ordem de execucao: o primeiro ts do lote, depois o nome.
    (Pelo nome so, lote_..._a11y01 viria antes de lote_..._api01 e os BUG-n trocavam.)"""
    with open(path, encoding="utf-8-sig") as f:
        tss = [x.get("ts") or "" for x in json.load(f).get("resultados", [])]
    return (min((t for t in tss if t), default="9999"), os.path.basename(path))


def main() -> int:
    destino = os.path.join(AQUI, "workbook", "plano-demo.xlsx")
    gerar_workbook.gerar(destino)
    print(f"workbook gerado de novo: {destino}")
    for lote in sorted(glob.glob(os.path.join(AQUI, "runs", "lote_*.json")), key=ordem):
        nome = os.path.splitext(os.path.basename(lote))[0]
        print(f"\n== {nome}")
        rc = aplicar([AQUI, lote, "--etiqueta", etiqueta(nome)])
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
