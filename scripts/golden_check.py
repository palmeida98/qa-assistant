"""
Teste golden do adaptador Excel (plano, tarefa 1.4; metodologia-core secao 15).

Gera o workbook ficticio (tests/golden/gerar_fixture.py) numa pasta
temporaria, aplica tests/golden/lote.json pelo adaptador com a configuracao
adapters/excel/exemplo.yaml, regenera a vista de ativos, grava, recalcula com
o LibreOffice e compara com tests/golden/esperado.json. Depois aplica o mesmo
lote outra vez e confirma que nada muda.

    python scripts/golden_check.py [--esperado <ficheiro>] [--mostrar]
    python scripts/golden_check.py --dir <workspace>/golden/<lote>   (ou QA_GOLDEN_DIR)

Com --dir (golden real, metodologia secao 15), depois do ficticio: aplica o lote historico da pasta
a uma copia de antes.xlsx, com a configuracao atual do projeto.yaml do
workspace e o instante de golden.yaml, e compara todas as celulas com
depois.xlsx; reaplicar nao pode mudar nada. Os dados reais nunca saem do
workspace: o resultado so se imprime.

Sai com 0 se tudo bater certo, 1 se houver diferencas (uma linha por
diferenca), 2 se o ambiente nao permitir correr o teste (ex.: sem LibreOffice).
--mostrar imprime o que foi obtido, para rever a mao; nunca reescreve o esperado.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))

import openpyxl
import yaml
from gerar_fixture import gerar

from adapters.excel import ExcelAdapter, load_lote
from adapters.excel.recalc import find_soffice

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
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
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

    # resumo (formulas recalculadas) vs contagem direta: tecnica-excel.md secao 5
    vr = ad.verificar_resumo()
    ob["resumo_vs_contagem"] = {"verificado": vr["verificado"], "problemas": vr["problemas"]}

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


# ---------------------------------------------------------------------------
# Golden real (metodologia secao 15): lote historico de um workspace
# ---------------------------------------------------------------------------


class GoldenInvalido(Exception):
    """Pasta de golden real incompleta ou sem projeto.yaml acima (codigo 2)."""


def _workspace_de(pasta: str) -> str:
    """A pasta acima mais proxima com projeto.yaml (o golden vive dentro do workspace)."""
    d = os.path.abspath(pasta)
    while True:
        if os.path.isfile(os.path.join(d, "projeto.yaml")):
            return d
        pai = os.path.dirname(d)
        if pai == d:
            raise GoldenInvalido(f"nenhum projeto.yaml em {pasta} nem nas pastas acima (o golden vive no workspace)")
        d = pai


def pastas_golden(pasta: str) -> list[str]:
    """A propria pasta, se tiver antes.xlsx; senao, as subpastas que o tenham."""
    if os.path.isfile(os.path.join(pasta, "antes.xlsx")):
        return [pasta]
    subs = sorted(os.path.join(pasta, d) for d in os.listdir(pasta)
                  if os.path.isfile(os.path.join(pasta, d, "antes.xlsx")))
    if not subs:
        raise GoldenInvalido(f"{pasta}: sem antes.xlsx, nem subpastas com antes.xlsx")
    return subs


def _celulas(path: str) -> dict:
    """{sheet: {celula: (valor ou formula, valor calculado)}}, so celulas com conteudo."""
    wbf, wbv = openpyxl.load_workbook(path), openpyxl.load_workbook(path, data_only=True)
    return {ws.title: {c.coordinate: (c.value, wbv[ws.title][c.coordinate].value)
                       for row in ws.iter_rows() for c in row if c.value is not None}
            for ws in wbf.worksheets}


def diferencas_celulas(esperado: dict, obtido: dict) -> list[str]:
    difs = [f"sheet '{s}': no esperado, em falta no obtido" for s in esperado if s not in obtido]
    difs += [f"sheet '{s}': nao esperada" for s in obtido if s not in esperado]
    for s, cel_e in esperado.items():
        cel_o = obtido.get(s)
        if cel_o is not None:
            for k in sorted(set(cel_e) | set(cel_o)):
                if cel_e.get(k) != cel_o.get(k):
                    difs.append(f"{s}!{k}: esperado {cel_e.get(k)!r}, obtido {cel_o.get(k)!r}")
    return difs


def run_real(pasta: str) -> list[str]:
    """Aplica o lote historico a uma copia de antes.xlsx pelo aplicar do /checkpoint
    e compara com depois.xlsx. Devolve as diferencas (vazia = golden OK)."""
    from adapters.excel.aplicar import aplicar

    lotes = sorted(f for f in os.listdir(pasta) if f.startswith("lote_") and f.endswith(".json"))
    faltam = [f for f in ("antes.xlsx", "depois.xlsx", "golden.yaml") if not os.path.isfile(os.path.join(pasta, f))]
    if faltam or len(lotes) != 1:
        raise GoldenInvalido(f"{pasta}: falta {faltam}" if faltam else
                             f"{pasta}: tem de ter exatamente um lote_*.json (tem {lotes})")
    with open(os.path.join(pasta, "golden.yaml"), encoding="utf-8-sig") as f:
        g = yaml.safe_load(f) or {}
    if not g.get("agora"):
        raise GoldenInvalido(f"{pasta}/golden.yaml: falta 'agora' (instante da aplicacao original, AAAA-MM-DDTHH:MM:SS)")
    agora = g["agora"] if isinstance(g["agora"], datetime) else datetime.fromisoformat(str(g["agora"]))
    with open(os.path.join(_workspace_de(pasta), "projeto.yaml"), encoding="utf-8-sig") as f:
        proj = yaml.safe_load(f) or {}
    if not proj.get("excel"):
        raise GoldenInvalido("o projeto.yaml do workspace nao tem a secao excel:")

    difs = []
    with tempfile.TemporaryDirectory() as tmp:
        # workspace temporario: a configuracao atual, com o workbook trocado pela copia de antes.xlsx
        excel = {**proj["excel"], "ficheiro": os.path.basename(proj["excel"]["ficheiro"])}
        with open(os.path.join(tmp, "projeto.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"excel": excel}, f, allow_unicode=True)
        workbook = os.path.join(tmp, excel["ficheiro"])
        shutil.copy(os.path.join(pasta, "antes.xlsx"), workbook)
        lote = os.path.join(tmp, lotes[0])
        shutil.copy(os.path.join(pasta, lotes[0]), lote)

        args = {"etiqueta": g.get("etiqueta"), "reportado_por": g.get("reportado_por") or "QA automatizado",
                "agora": agora}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                rc = aplicar(tmp, lote, **args)
            except Exception as e:  # noqa: BLE001 -- lote invalido, configuracao: uma diferenca a investigar
                return [f"o aplicar falhou: {e}"]
        if rc not in (0, 5):  # 5 = resumo do workbook errado; o depois.xlsx dira se era assim na altura
            return [f"o aplicar saiu com {rc}: {out.getvalue().strip()[-800:]}"]
        difs += diferencas_celulas(_celulas(os.path.join(pasta, "depois.xlsx")), _celulas(workbook))

        antes = _celulas(workbook)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            aplicar(tmp, lote, **args)
        if "Nada aplicado" not in out.getvalue() or _celulas(workbook) != antes:
            difs.append("reaplicar o mesmo lote mudou o workbook")
    return difs


def main_real(pasta: str) -> int:
    try:
        pastas = pastas_golden(pasta)
    except (GoldenInvalido, FileNotFoundError) as e:
        print(f"GOLDEN REAL NAO CORREU: {e}")
        return 2
    falhou = False
    for p in pastas:
        try:
            difs = run_real(p)
        except GoldenInvalido as e:
            print(f"GOLDEN REAL NAO CORREU: {e}")
            return 2
        if difs:
            falhou = True
            print(f"GOLDEN REAL FALHOU: {p} -- {len(difs)} diferenca(s) (investigar cada uma; metodologia secao 15)")
            for d in difs[:50]:
                print(f"  - {d}")
            if len(difs) > 50:
                print(f"  ... mais {len(difs) - 50}")
        else:
            print(f"GOLDEN REAL OK: {p} (celulas iguais ao depois.xlsx; reaplicar sem efeito)")
    return 1 if falhou else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Teste golden do adaptador Excel")
    ap.add_argument("--esperado", default=os.path.join(GOLDEN, "esperado.json"))
    ap.add_argument("--mostrar", action="store_true", help="imprimir o obtido (nunca grava o esperado)")
    ap.add_argument("--dir", default=os.environ.get("QA_GOLDEN_DIR") or None,
                    help="golden real, ALEM do ficticio: pasta no workspace com antes.xlsx, lote_*.json, "
                         "depois.xlsx e golden.yaml (ou uma pasta com varias destas); por omissao QA_GOLDEN_DIR")
    a = ap.parse_args(argv)

    try:
        find_soffice()
    except FileNotFoundError as e:
        print(f"GOLDEN NAO CORREU: {e}\n(os totais da Summary so se verificam com o recalculo)")
        return 2
    rc = golden_ficticio(a.esperado, a.mostrar)
    # o ficticio corre sempre (com QA_GOLDEN_DIR no ambiente, o pre-commit nao pode perde-lo)
    return max(rc, main_real(a.dir)) if a.dir else rc


def golden_ficticio(esperado_path: str, mostrar: bool) -> int:
    with open(esperado_path, encoding="utf-8") as f:
        esperado = _hoje(json.load(f))
    with tempfile.TemporaryDirectory() as tmp:
        obtido = run(tmp)

    if mostrar:
        print(json.dumps(obtido, ensure_ascii=False, indent=2, default=str))
    difs = compare(esperado, obtido)
    if difs:
        print(f"GOLDEN FALHOU: {len(difs)} diferenca(s) entre {os.path.relpath(esperado_path, ROOT)} e o obtido")
        for d in difs:
            print(f"  - {d}")
        return 1
    print("GOLDEN OK: o adaptador produz exatamente o esperado "
          f"({len(esperado['casos'])} casos, {len(esperado['defeitos'])} defeitos, totais da Summary, lote reaplicado sem efeito)")
    return 0


if __name__ == "__main__":
    # no Windows, a saida lida por um pipe (ex.: Claude Code) vinha em cp1252 e os acentos chegavam trocados
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
