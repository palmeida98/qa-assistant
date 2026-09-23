# -*- coding: utf-8 -*-
"""
Teste golden do adaptador Excel (plano, tarefa 1.4; metodologia-core secao 15).

Gera o workbook ficticio (tests/golden/gerar_fixture.py) numa pasta
temporaria, aplica tests/golden/lote.json pelo adaptador com a configuracao
adapters/excel/exemplo.yaml, regenera a vista de ativos, grava, recalcula com
o LibreOffice e compara com tests/golden/esperado.json. Depois aplica o mesmo
lote outra vez e confirma que nada muda.

    python scripts/golden_check.py [--esperado <ficheiro>] [--mostrar]

Sai com 0 se tudo bater certo, 1 se houver diferencas (uma linha por
diferenca), 2 se o ambiente nao permitir correr o teste (ex.: sem LibreOffice).
--mostrar imprime o que foi obtido, para rever a mao; nunca reescreve o esperado.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))

import openpyxl  # noqa: E402
import yaml  # noqa: E402

from adapters.excel import ExcelAdapter, load_lote  # noqa: E402
from adapters.excel.recalc import find_soffice  # noqa: E402
from gerar_fixture import gerar  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden")
CONFIG = os.path.join(ROOT, "adapters", "excel", "exemplo.yaml")
ETIQUETA, REPORTADO_POR = "[golden]", "golden"


def _hoje(obj):
    """Substitui '{hoje}' pela data do dia em todas as strings do esperado."""
    if isinstance(obj, str):
        return obj.replace("{hoje}", date.today().isoformat())
    if isinstance(obj, list):
        return [_hoje(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _hoje(v) for k, v in obj.items()}
    return obj


def _rows_as_dicts(ws, header_row=1):
    h = [c.value for c in ws[header_row]]
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if row and row[0] not in (None, ""):
            yield dict(zip(h, row))


def _snapshot(path):
    wbf, wbv = openpyxl.load_workbook(path), openpyxl.load_workbook(path, data_only=True)
    return {ws.title: [[(c.value, wbv[ws.title][c.coordinate].value) for c in row] for row in ws.iter_rows()]
            for ws in wbf.worksheets}


def run(workdir: str) -> dict:
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    gerar(os.path.join(workdir, cfg["ficheiro"]))
    lote = load_lote(os.path.join(GOLDEN, "lote.json"))

    ad = ExcelAdapter.from_dict(cfg, base_dir=workdir)
    ad.load()
    r = ad.apply_lote(lote, etiqueta=ETIQUETA, reportado_por=REPORTADO_POR)
    ad.regenerate_active_defects()
    s = ad.save_if_clean()
    rc = ad.recalc() if s["gravado_original"] else {"ok": False, "erro": "nao gravado (cross-check)"}

    path = ad.path
    wb = openpyxl.load_workbook(path, data_only=True)
    ob = {
        "apply": {"aplicado": r["aplicado"], "novos_defeitos": r["novos_defeitos"]},
        "gravado_original": s["gravado_original"],
        "cross_check": s["problemas"],
        "recalc_ok": rc["ok"],
        "sheets": [[ws.title, ws.sheet_state] for ws in wb.worksheets],
        "casos": {},
        "defeitos": {},
    }
    if not rc["ok"]:
        ob["recalc_erro"] = rc.get("erro")

    for sheet in dict.fromkeys(cfg["casos"]["sheets"].values()):
        for row in _rows_as_dicts(wb[sheet]):
            ob["casos"][row["ID"]] = {k: v for k, v in row.items() if k not in ("ID", "Title")}
    for row in _rows_as_dicts(wb[cfg["defeitos"]["sheet"]]):
        ob["defeitos"][row["Key"]] = {k: v for k, v in row.items() if k != "Key"}

    v = cfg["vista_ativos"]
    wv = wb[v["sheet"]]
    first = v["linha_cabecalho"] + 1
    ob["vista"] = {
        "A1": wv["A1"].value,
        "A2": wv["A2"].value,
        "cabecalho": [c.value for c in wv[v["linha_cabecalho"]]],
        "linhas": [list(row) for row in wv.iter_rows(min_row=first, values_only=True) if row[0] not in (None, "")],
        "estilo_linhas_bold": all(c.font.bold for row in wv.iter_rows(min_row=first, max_row=wv.max_row) for c in row),
    }

    ws = wb["Summary"]
    h = [c.value for c in ws[1]]
    ob["totais_summary"] = {row[0]: dict(zip(h[1:], row[1:])) for row in ws.iter_rows(min_row=2, values_only=True)}

    ad_ctl = ExcelAdapter.from_dict(cfg, base_dir=workdir)
    ad_ctl.load()
    ob["controlo_lotes"] = [{k: x[k] for k in ("etiqueta", "itens", "defeitos_novos")} for x in ad_ctl.lotes_aplicados()]

    # reaplicar o mesmo lote: nada pode mudar
    antes = _snapshot(path)
    ad2 = ExcelAdapter.from_dict(cfg, base_dir=workdir)
    ad2.load()
    r2 = ad2.apply_lote(lote, etiqueta=ETIQUETA, reportado_por=REPORTADO_POR)
    ad2.regenerate_active_defects()
    s2 = ad2.save_if_clean()
    if s2["gravado_original"]:
        ad2.recalc()
    ob["reaplicar"] = {"aplicado": r2["aplicado"], "ficheiro_igual": _snapshot(path) == antes}
    return ob


def compare(esperado, obtido, path="") -> list[str]:
    difs = []
    if isinstance(esperado, dict) and isinstance(obtido, dict):
        for k in esperado:
            if k.startswith("_"):
                continue
            p = f"{path}.{k}" if path else k
            if k not in obtido:
                difs.append(f"{p}: esperado {esperado[k]!r}, em falta no obtido")
            else:
                difs += compare(esperado[k], obtido[k], p)
        for k in obtido:
            if k not in esperado and not k.startswith("_"):
                p = f"{path}.{k}" if path else k
                difs.append(f"{p}: nao esperado, obtido {obtido[k]!r}")
    elif isinstance(esperado, list) and isinstance(obtido, list) and len(esperado) == len(obtido):
        for i, (e, o) in enumerate(zip(esperado, obtido)):
            difs += compare(e, o, f"{path}[{i}]")
    elif esperado != obtido:
        difs.append(f"{path}: esperado {esperado!r}, obtido {obtido!r}")
    return difs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Teste golden do adaptador Excel")
    ap.add_argument("--esperado", default=os.path.join(GOLDEN, "esperado.json"))
    ap.add_argument("--mostrar", action="store_true", help="imprimir o obtido (nunca grava o esperado)")
    a = ap.parse_args(argv)

    try:
        find_soffice()
    except FileNotFoundError as e:
        print(f"GOLDEN NAO CORREU: {e}\n(os totais da Summary so se verificam com o recalculo)")
        return 2

    with open(a.esperado, encoding="utf-8") as f:
        esperado = _hoje(json.load(f))
    with tempfile.TemporaryDirectory() as tmp:
        obtido = run(tmp)

    if a.mostrar:
        print(json.dumps(obtido, ensure_ascii=False, indent=2, default=str))
    difs = compare(esperado, obtido)
    if difs:
        print(f"GOLDEN FALHOU: {len(difs)} diferenca(s) entre {os.path.relpath(a.esperado, ROOT)} e o obtido")
        for d in difs:
            print(f"  - {d}")
        return 1
    print("GOLDEN OK: o adaptador produz exatamente o esperado "
          f"({len(esperado['casos'])} casos, {len(esperado['defeitos'])} defeitos, totais da Summary, lote reaplicado sem efeito)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
