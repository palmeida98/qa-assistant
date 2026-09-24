"""
Aplica um lote (registo leve) ao workbook de um workspace, num so passo
(tecnica-excel.md secao 1; plano, tarefa 2.3):

    load -> apply_lote -> regenerate_active_defects -> save_if_clean -> recalc

A configuracao vem da secao `excel:` do projeto.yaml do workspace.

    python -m adapters.excel.aplicar <workspace> <lote.json>
        [--etiqueta "[2026-09-23, lote 01]"] [--reportado-por "..."] [--sem-recalc]

Codigos de saida:
  0  aplicado e gravado (ou lote ja aplicado antes: nada muda)
  2  cross-check com problemas: original intacto, resultado em <nome>.REVISTO.xlsx
  3  gravado, mas o recalculo falhou (rever antes de dar por terminado)
  4  lote ou configuracao invalidos: nada foi escrito (ver a lista de problemas)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from .base import ConfigError, ExcelAdapter, LoteInvalido, load_lote


def _resolve(workspace: str, path: str) -> str:
    if os.path.exists(path):
        return path
    alt = os.path.join(workspace, path)
    if os.path.exists(alt):
        return alt
    raise FileNotFoundError(f"lote nao encontrado: {path} (nem em {alt})")


def aplicar(workspace: str, lote_path: str, etiqueta: str | None = None,
            reportado_por: str = "QA automatizado", recalc: bool = True) -> int:
    lote_path = _resolve(workspace, lote_path)
    ad = ExcelAdapter.from_projeto(workspace)
    ad.load()
    antes = ad.progress_summary()
    lote = load_lote(lote_path)
    etiqueta = etiqueta or f"[{date.today().isoformat()}, {os.path.splitext(os.path.basename(lote_path))[0]}]"

    print(f"Workbook: {ad.path}")
    print(f"Lote: {lote_path} ({len(lote)} itens)")
    r = ad.apply_lote(lote, etiqueta=etiqueta, reportado_por=reportado_por)
    if not r["aplicado"]:
        print(f"Nada aplicado: {r['motivo']}. O workbook nao foi alterado.")
        return 0
    if r["novos_defeitos"]:
        print("Defeitos novos:", ", ".join(r["novos_defeitos"]))

    v = ad.regenerate_active_defects()
    if v.get("total_aberto") is not None:
        print(f"Vista de ativos: {v['total_aberto']} abertos, {v['fechados']} fechados, por severidade {v['por_severidade']}")

    out = ad.save_if_clean()
    if not out["gravado_original"]:
        print(f"\nCross-check: {len(out['problemas'])} problema(s) -- ORIGINAL INTACTO.")
        print(f"Resultado gravado a parte em: {out['ficheiro']}")
        for p in out["problemas"]:
            print(" -", p)
        return 2
    print("Cross-check: 0 problemas. Gravado.")

    rc_code = 0
    if recalc:
        rc = ad.recalc()
        print(f"Recalculo ({rc['mecanismo']}):", "OK" if rc["ok"] else f"FALHOU -- {rc['erro']}")
        rc_code = 0 if rc["ok"] else 3

    depois = ad.progress_summary()
    print("\nAntes -> depois (contagem direta por sheet):")
    for sheet in depois:
        print(f"  {sheet}: {antes.get(sheet)} -> {depois[sheet]}")
    return rc_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aplica um lote ao workbook de um workspace")
    ap.add_argument("workspace")
    ap.add_argument("lote")
    ap.add_argument("--etiqueta", help="texto que antecede as notas desta sessao (por omissao: [data, nome do lote])")
    ap.add_argument("--reportado-por", default="QA automatizado")
    ap.add_argument("--sem-recalc", action="store_true")
    a = ap.parse_args(argv)
    try:
        return aplicar(a.workspace, a.lote, a.etiqueta, a.reportado_por, not a.sem_recalc)
    except (LoteInvalido, ConfigError, FileNotFoundError) as e:
        print(f"\nNADA APLICADO -- {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
