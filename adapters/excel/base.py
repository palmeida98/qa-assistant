# -*- coding: utf-8 -*-
"""
Adaptador Excel universal (tecnica-excel.md secao 1).

Aplica um lote de resultados (registo leve, metodologia-core secao 7.1) a um
workbook Excel usado como fonte de verdade de casos e defeitos:

    carregar (preservando formulas) -> aplicar lote -> regenerar vista de
    defeitos ativos -> cross-check -> gravar so se limpo (senao .REVISTO.xlsx)
    -> recalcular em seguranca

Nenhum nome de sheet, coluna ou estado vive neste codigo: todo o layout vem da
configuracao (ver exemplo.yaml ao lado; campos documentados em from_dict()).

Uso tipico:

    from adapters.excel import ExcelAdapter, load_lote

    ad = ExcelAdapter.from_yaml("projeto.yaml", secao="excel")
    ad.load()
    res = ad.apply_lote(load_lote("runs/lote_x.json"), etiqueta="[2026-09-23]")
    ad.regenerate_active_defects()
    out = ad.save_if_clean()
    if out["gravado_original"]:
        ad.recalc()

Formato do lote: {"resultados": [item, ...]}, cada item
    {"tc": <id do caso>, "estado": <estado>, "resultado_obtido": <texto>,
     "defeito_novo": null | {<chave>: <valor>, ...}}
As chaves de "defeito_novo" sao mapeadas para colunas pela configuracao
(defeitos.campos_lote); "tc_relacionado" (por omissao o proprio caso),
"severidade" e "prioridade" (por omissao a severidade) tem papel proprio.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections import Counter
from datetime import date, datetime

import openpyxl

from .recalc import recalc_safe

CONTROL_SHEET_DEFAULT = "_qa_lotes"
_CONTROL_HEADERS = ["hash", "aplicado_em", "etiqueta", "itens", "defeitos_novos"]


class ConfigError(ValueError):
    pass


def load_lote(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["resultados"]


def lote_hash(lote: list) -> str:
    """Impressao digital do conteudo do lote (independente da ordem das chaves)."""
    canon = json.dumps(lote, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _req(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, "", [], {}):
        raise ConfigError(f"configuracao: falta '{where}{key}'")
    return d[key]


class ExcelAdapter:
    # ------------------------------------------------------------------
    # Configuracao
    # ------------------------------------------------------------------

    def __init__(self, config: dict, base_dir: str | None = None):
        self.cfg = self._validate(config)
        self.base_dir = base_dir or os.getcwd()
        self.wb: openpyxl.Workbook | None = None

    @classmethod
    def from_dict(cls, config: dict, base_dir: str | None = None) -> "ExcelAdapter":
        """Campos (obrigatorios salvo indicacao):

        ficheiro: caminho do workbook (relativo a base_dir)
        casos:
          sheets: {prefixo_do_id: nome_da_sheet}
          linha_cabecalho: int (opcional, 1)
          colunas: {id, estado, resultado_obtido (opc.), observacoes (opc.)}
                   (as opcionais so se usam nas sheets onde existem)
          estados: [estados validos de caso]
        defeitos:
          sheet, linha_cabecalho (opc., 1)
          formato_id: ex. "BUG-{n}"
          colunas: {id, estado, caso (opc.), severidade (opc.), prioridade (opc.),
                    reportado_por (opc.), data_report (opc.)}
          campos_lote: {chave_do_defeito_no_lote: coluna} (opc.)
          valores_omissao: {chave_do_defeito_no_lote: valor} (opc.)
          estados, estados_fechados, estado_inicial
          prioridades: [ordem, da mais alta para a mais baixa] (opc.)
        vista_ativos (opc.):
          sheet, linha_cabecalho (opc., 1)
          colunas: {cabecalho_da_vista: coluna_dos_defeitos} (cabecalhos
                   nao mapeados usam a coluna com o mesmo nome)
          ordenar_por: coluna dos defeitos usada como 2.o criterio (opc.)
          resumo: {celula, modelo} (opc.; modelo com {abertos}, {fechados},
                  {severidade[<valor>]}, {data:%d.%m.%Y})
        notas (opc.):
          resultado: modelo com {resultado} (usado se nao houver coluna resultado_obtido)
          defeito_novo: modelo com {id}
        controlo_lotes (opc.): {sheet} -- sheet escondida com os lotes aplicados
        recalc (opc.): {mecanismo: libreoffice | excel-com, timeout: int}
        """
        return cls(config, base_dir)

    @classmethod
    def from_yaml(cls, path: str, secao: str | None = None) -> "ExcelAdapter":
        import yaml

        with open(path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f) or {}
        if secao:
            if secao not in data:
                raise ConfigError(f"{path}: secao '{secao}' em falta")
            data = data[secao]
        return cls(data, base_dir=os.path.dirname(os.path.abspath(path)))

    @staticmethod
    def _validate(cfg: dict) -> dict:
        cfg = copy.deepcopy(cfg)
        _req(cfg, "ficheiro", "")
        casos = _req(cfg, "casos", "")
        _req(casos, "sheets", "casos.")
        ccol = _req(casos, "colunas", "casos.")
        _req(ccol, "id", "casos.colunas.")
        _req(ccol, "estado", "casos.colunas.")
        _req(casos, "estados", "casos.")
        casos.setdefault("linha_cabecalho", 1)

        d = _req(cfg, "defeitos", "")
        _req(d, "sheet", "defeitos.")
        fmt = _req(d, "formato_id", "defeitos.")
        if fmt.count("{n}") != 1:
            raise ConfigError("configuracao: defeitos.formato_id tem de conter '{n}' uma vez")
        dcol = _req(d, "colunas", "defeitos.")
        _req(dcol, "id", "defeitos.colunas.")
        _req(dcol, "estado", "defeitos.colunas.")
        _req(d, "estados", "defeitos.")
        d.setdefault("estados_fechados", [])
        _req(d, "estado_inicial", "defeitos.")
        for key in ("estados_fechados",):
            bad = [e for e in d[key] if e not in d["estados"]]
            if bad:
                raise ConfigError(f"configuracao: defeitos.{key} fora de defeitos.estados: {bad}")
        if d["estado_inicial"] not in d["estados"]:
            raise ConfigError("configuracao: defeitos.estado_inicial fora de defeitos.estados")
        d.setdefault("linha_cabecalho", 1)
        d.setdefault("campos_lote", {})
        d.setdefault("valores_omissao", {})
        d.setdefault("prioridades", [])
        pre, _, pos = fmt.partition("{n}")
        d["_id_re"] = re.compile("^" + re.escape(pre) + r"(\d+)" + re.escape(pos) + "$")

        v = cfg.get("vista_ativos")
        if v:
            _req(v, "sheet", "vista_ativos.")
            v.setdefault("linha_cabecalho", 1)
            v.setdefault("colunas", {})

        cfg.setdefault("notas", {})
        cfg["notas"].setdefault("resultado", "Resultado: {resultado}")
        cfg["notas"].setdefault("defeito_novo", "Defeito novo: {id}.")
        cfg.setdefault("controlo_lotes", {})
        cfg["controlo_lotes"].setdefault("sheet", CONTROL_SHEET_DEFAULT)
        cfg.setdefault("recalc", {})
        cfg["recalc"].setdefault("mecanismo", "libreoffice")
        cfg["recalc"].setdefault("timeout", 60)
        return cfg

    @property
    def path(self) -> str:
        return os.path.join(self.base_dir, self.cfg["ficheiro"])

    # ------------------------------------------------------------------
    # Carregamento / gravacao
    # ------------------------------------------------------------------

    def load(self, path: str | None = None) -> openpyxl.Workbook:
        """Carrega preservando formulas (data_only=False) -- o que se quer
        para EDITAR. Valores calculados leem-se numa copia a parte."""
        self.wb = openpyxl.load_workbook(path or self.path, data_only=False)
        self._check_layout()
        return self.wb

    def _need_wb(self):
        if self.wb is None:
            raise RuntimeError("workbook nao carregado -- chamar load() primeiro")
        return self.wb

    @staticmethod
    def _headers(ws, row: int) -> dict:
        return {c.value: c.column for c in ws[row] if c.value not in (None, "")}

    def _check_layout(self) -> None:
        """Falha cedo se a configuracao nao corresponde ao workbook real."""
        wb = self.wb
        problems = []
        casos, d = self.cfg["casos"], self.cfg["defeitos"]
        for sheet in set(casos["sheets"].values()):
            if sheet not in wb.sheetnames:
                problems.append(f"sheet de casos em falta: '{sheet}'")
                continue
            h = self._headers(wb[sheet], casos["linha_cabecalho"])
            # resultado_obtido / observacoes sao opcionais sheet a sheet
            for role in ("id", "estado"):
                col = casos["colunas"][role]
                if col not in h:
                    problems.append(f"'{sheet}': coluna '{col}' ({role}) em falta")
        if d["sheet"] not in wb.sheetnames:
            problems.append(f"sheet de defeitos em falta: '{d['sheet']}'")
        else:
            h = self._headers(wb[d["sheet"]], d["linha_cabecalho"])
            cols = list(d["colunas"].items()) + [(k, c) for k, c in d["campos_lote"].items()]
            for role, col in cols:
                if col and col not in h:
                    problems.append(f"'{d['sheet']}': coluna '{col}' ({role}) em falta")
        v = self.cfg.get("vista_ativos")
        if v and v["sheet"] not in wb.sheetnames:
            problems.append(f"sheet da vista de ativos em falta: '{v['sheet']}'")
        if problems:
            raise ConfigError("configuracao nao corresponde ao workbook:\n - " + "\n - ".join(problems))

    def save(self, path: str | None = None) -> str:
        out = path or self.path
        self._need_wb().save(out)
        return out

    def revisto_path(self) -> str:
        root, ext = os.path.splitext(self.path)
        return f"{root}.REVISTO{ext}"

    def save_if_clean(self) -> dict:
        """Grava por cima do original so se o cross-check devolver zero
        problemas; caso contrario grava <nome>.REVISTO.xlsx e deixa o
        original intacto (tecnica-excel.md secao 1, passo 5)."""
        problems = self.cross_check()
        if problems:
            alt = self.save(self.revisto_path())
            return {"gravado_original": False, "ficheiro": alt, "problemas": problems}
        return {"gravado_original": True, "ficheiro": self.save(), "problemas": []}

    def recalc(self, path: str | None = None) -> dict:
        r = self.cfg["recalc"]
        return recalc_safe(path or self.path, mecanismo=r["mecanismo"], timeout=r["timeout"])

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def _case_sheets(self):
        """(nome, ws, headers) de cada sheet de casos, pela ordem da configuracao."""
        wb, casos = self._need_wb(), self.cfg["casos"]
        seen = []
        for sheet in casos["sheets"].values():
            if sheet not in seen:
                seen.append(sheet)
                ws = wb[sheet]
                yield sheet, ws, self._headers(ws, casos["linha_cabecalho"])

    def _case_rows(self, ws, h):
        casos = self.cfg["casos"]
        idc = h[casos["colunas"]["id"]]
        for r in range(casos["linha_cabecalho"] + 1, ws.max_row + 1):
            v = ws.cell(r, idc).value
            if v not in (None, ""):
                yield r, v

    def sheet_for_case(self, case_id: str) -> str:
        for pref, sheet in self.cfg["casos"]["sheets"].items():
            if str(case_id).startswith(pref):
                return sheet
        raise KeyError(f"ID de caso sem prefixo configurado: {case_id}")

    def progress_summary(self) -> dict:
        """{sheet: {estado: n}} -- contagem direta, sem depender de formulas."""
        est = self.cfg["casos"]["colunas"]["estado"]
        out = {}
        for sheet, ws, h in self._case_sheets():
            cnt = Counter(ws.cell(r, h[est]).value for r, _ in self._case_rows(ws, h))
            out[sheet] = dict(cnt)
        return out

    def casos_por_estado(self, estado: str) -> dict:
        """{sheet: [linha como dict, com '_row']} dos casos num dado estado."""
        est = self.cfg["casos"]["colunas"]["estado"]
        out = {}
        for sheet, ws, h in self._case_sheets():
            rows = []
            for r, _ in self._case_rows(ws, h):
                if ws.cell(r, h[est]).value == estado:
                    row = {name: ws.cell(r, c).value for name, c in h.items()}
                    row["_row"] = r
                    rows.append(row)
            out[sheet] = rows
        return out

    def _defect_rows(self):
        d = self.cfg["defeitos"]
        ws = self._need_wb()[d["sheet"]]
        h = self._headers(ws, d["linha_cabecalho"])
        idc = h[d["colunas"]["id"]]
        rows = [(r, ws.cell(r, idc).value) for r in range(d["linha_cabecalho"] + 1, ws.max_row + 1)]
        return ws, h, [(r, v) for r, v in rows if v not in (None, "")]

    def _defect_number(self, defect_id) -> int | None:
        m = self.cfg["defeitos"]["_id_re"].match(str(defect_id).strip())
        return int(m.group(1)) if m else None

    def next_defect_number(self) -> int:
        _, _, rows = self._defect_rows()
        nums = [n for n in (self._defect_number(v) for _, v in rows) if n is not None]
        return max(nums, default=0) + 1

    # ------------------------------------------------------------------
    # Controlo de lotes aplicados
    # ------------------------------------------------------------------

    def _control_ws(self, create: bool):
        wb, name = self._need_wb(), self.cfg["controlo_lotes"]["sheet"]
        if name in wb.sheetnames:
            return wb[name]
        if not create:
            return None
        ws = wb.create_sheet(name)
        ws.sheet_state = "hidden"
        ws.append(_CONTROL_HEADERS)
        return ws

    def lotes_aplicados(self) -> list[dict]:
        ws = self._control_ws(create=False)
        if ws is None:
            return []
        return [dict(zip(_CONTROL_HEADERS, row)) for row in ws.iter_rows(min_row=2, values_only=True) if row[0]]

    def lote_ja_aplicado(self, lote: list) -> dict | None:
        h = lote_hash(lote)
        return next((x for x in self.lotes_aplicados() if x["hash"] == h), None)

    # ------------------------------------------------------------------
    # Aplicacao de um lote
    # ------------------------------------------------------------------

    def apply_lote(self, lote: list, etiqueta: str | None = None,
                   reportado_por: str = "QA automatizado") -> dict:
        """Aplica o lote: estado, resultado obtido e observacoes por caso, e
        cria os defeitos novos. Um lote ja aplicado a este workbook (mesmo
        conteudo) nao e reaplicado: devolve {"aplicado": False, ...} sem
        alterar nada (tecnica-excel.md secao 5).

        Todos os IDs de caso sao verificados antes de qualquer escrita."""
        wb = self._need_wb()
        h_lote = lote_hash(lote)
        anterior = self.lote_ja_aplicado(lote)
        if anterior:
            return {"aplicado": False, "hash": h_lote, "novos_defeitos": [],
                    "motivo": f"lote ja aplicado em {anterior['aplicado_em']}"}

        etiqueta = etiqueta or f"[{date.today().isoformat()}]"
        casos, d, notas = self.cfg["casos"], self.cfg["defeitos"], self.cfg["notas"]
        ccol = casos["colunas"]

        # mapa caso -> (ws, headers, linha), validado antes de escrever
        rowmap = {}
        for sheet, ws, h in self._case_sheets():
            for r, v in self._case_rows(ws, h):
                rowmap.setdefault(str(v).strip(), (ws, h, r))
        missing = [it["tc"] for it in lote if str(it["tc"]).strip() not in rowmap]
        if missing:
            raise KeyError(f"IDs de caso nao encontrados no workbook: {missing}")

        ws_def, h_def, def_rows = self._defect_rows()
        last_def_row = max([r for r, _ in def_rows] + [d["linha_cabecalho"]])
        next_num = self.next_defect_number()
        dcol = d["colunas"]
        novos = []

        for item in lote:
            tc = str(item["tc"]).strip()
            ws, h, r = rowmap[tc]
            ws.cell(r, h[ccol["estado"]]).value = item["estado"]
            has_res = ccol.get("resultado_obtido") in h
            if has_res:
                ws.cell(r, h[ccol["resultado_obtido"]]).value = item.get("resultado_obtido")

            def_novo = item.get("defeito_novo")
            def_id = None
            if def_novo:
                def_id = d["formato_id"].replace("{n}", str(next_num))
                next_num += 1
                novos.append(def_id)

            if ccol.get("observacoes") in h:
                partes = []
                if not has_res:
                    partes.append(notas["resultado"].format(resultado=item.get("resultado_obtido")))
                if def_id:
                    partes.append(notas["defeito_novo"].format(id=def_id))
                if partes:
                    nota = f"{etiqueta} " + " ".join(partes)
                    c = ws.cell(r, h[ccol["observacoes"]])
                    c.value = (str(c.value).rstrip() + "\n\n" + nota).strip() if c.value else nota

            if def_novo:
                last_def_row += 1
                vals = dict(d["valores_omissao"])
                vals.update(def_novo)

                def put(col, value):
                    if col:
                        ws_def.cell(last_def_row, h_def[col]).value = value

                put(dcol["id"], def_id)
                put(dcol.get("caso"), vals.get("tc_relacionado", tc))
                for key, col in d["campos_lote"].items():
                    put(col, vals.get(key))
                put(dcol.get("severidade"), vals.get("severidade"))
                put(dcol.get("prioridade"), vals.get("prioridade", vals.get("severidade")))
                put(dcol["estado"], d["estado_inicial"])
                put(dcol.get("reportado_por"), reportado_por)
                put(dcol.get("data_report"), date.today().isoformat())

        ctl = self._control_ws(create=True)
        ctl.append([h_lote, datetime.now().isoformat(timespec="seconds"), etiqueta,
                    len(lote), ", ".join(novos)])
        return {"aplicado": True, "hash": h_lote, "novos_defeitos": novos}

    def update_defect_note(self, defect_id: str, nota: str, coluna: str,
                           novo_estado: str | None = None) -> None:
        """Acrescenta uma nota a uma coluna de texto de um defeito existente,
        sem apagar o que la estava. So muda o estado se novo_estado for
        passado explicitamente."""
        d = self.cfg["defeitos"]
        ws, h, rows = self._defect_rows()
        if coluna not in h:
            raise KeyError(f"coluna '{coluna}' nao existe em '{d['sheet']}'")
        for r, v in rows:
            if str(v).strip() == defect_id:
                c = ws.cell(r, h[coluna])
                c.value = (str(c.value or "").rstrip() + "\n\n" + nota).strip()
                if novo_estado:
                    if novo_estado not in d["estados"]:
                        raise ValueError(f"estado de defeito invalido: {novo_estado}")
                    ws.cell(r, h[d["colunas"]["estado"]]).value = novo_estado
                return
        raise KeyError(f"defeito nao encontrado: {defect_id}")

    # ------------------------------------------------------------------
    # Vista de defeitos ativos
    # ------------------------------------------------------------------

    def regenerate_active_defects(self) -> dict:
        """Reconstroi as linhas de dados da vista de defeitos ativos a partir
        da sheet de defeitos (sem os estados fechados), ordenadas por
        prioridade, pelo criterio secundario configurado e pelo numero do
        defeito. Preserva o que esta acima da primeira linha de dados e
        reaplica o estilo da primeira linha de dados original."""
        v = self.cfg.get("vista_ativos")
        if not v:
            return {"total_aberto": None, "motivo": "vista_ativos nao configurada"}
        d = self.cfg["defeitos"]
        ws_def, h_def, def_rows = self._defect_rows()
        est_col = h_def[d["colunas"]["estado"]]
        fechados = set(d["estados_fechados"])

        rows, closed = [], 0
        for r, _ in def_rows:
            if ws_def.cell(r, est_col).value in fechados:
                closed += 1
                continue
            rows.append({name: ws_def.cell(r, c).value for name, c in h_def.items()})

        prio_col, prios = d["colunas"].get("prioridade"), d["prioridades"]
        sec = v.get("ordenar_por")

        def key(row):
            p = row.get(prio_col) if prio_col else None
            pk = prios.index(p) if p in prios else len(prios)
            n = self._defect_number(row.get(d["colunas"]["id"]))
            return (pk, row.get(sec) or "" if sec else "", n if n is not None else float("inf"))

        rows.sort(key=key)

        ws = self._need_wb()[v["sheet"]]
        hv = self._headers(ws, v["linha_cabecalho"])
        first = v["linha_cabecalho"] + 1
        styles = None
        if ws.cell(first, 1).value not in (None, ""):
            styles = {}
            for col in hv.values():
                c = ws.cell(first, col)
                styles[col] = (copy.copy(c.font), copy.copy(c.fill), copy.copy(c.border),
                               copy.copy(c.alignment), c.number_format)
        if ws.max_row >= first:
            ws.delete_rows(first, ws.max_row - first + 1)

        for i, row in enumerate(rows):
            target = first + i
            for head, col in hv.items():
                c = ws.cell(target, col)
                c.value = row.get(v["colunas"].get(head, head))
                if styles:
                    c.font, c.fill, c.border, c.alignment, c.number_format = styles[col]

        sev_col = d["colunas"].get("severidade")
        sev = Counter(row.get(sev_col) for row in rows) if sev_col else Counter()
        resumo = v.get("resumo")
        if resumo:
            ws[resumo["celula"]].value = resumo["modelo"].format_map(
                {"abertos": len(rows), "fechados": closed, "severidade": sev, "data": date.today()}
            )
        return {"total_aberto": len(rows), "por_severidade": dict(sev), "fechados": closed}

    # ------------------------------------------------------------------
    # Cross-check (metodologia-core secao 11)
    # ------------------------------------------------------------------

    def cross_check(self) -> list[str]:
        """Devolve uma lista de problemas em texto (vazia = 0 problemas):
        estados de caso e de defeito fora da lista valida, IDs de caso e de
        defeito duplicados, e defeitos ligados a um caso inexistente."""
        casos, d = self.cfg["casos"], self.cfg["defeitos"]
        estados_caso, estados_def = set(casos["estados"]), set(d["estados"])
        problems = []
        case_ids = Counter()
        for sheet, ws, h in self._case_sheets():
            ec = h[casos["colunas"]["estado"]]
            for r, v in self._case_rows(ws, h):
                case_ids[str(v).strip()] += 1
                est = ws.cell(r, ec).value
                if est not in estados_caso:
                    problems.append(f"{sheet} linha {r} ({v}): estado de caso invalido {est!r}")
        dup = sorted(k for k, n in case_ids.items() if n > 1)
        if dup:
            problems.append(f"ID de caso duplicado: {dup}")

        ws_def, h_def, def_rows = self._defect_rows()
        ec = h_def[d["colunas"]["estado"]]
        caso_col = d["colunas"].get("caso")
        seen = Counter()
        for r, v in def_rows:
            seen[str(v).strip()] += 1
            est = ws_def.cell(r, ec).value
            if est not in estados_def:
                problems.append(f"{d['sheet']} linha {r} ({v}): estado de defeito invalido {est!r}")
            if caso_col:
                ref = ws_def.cell(r, h_def[caso_col]).value
                for tc in self._case_refs(ref):
                    if tc not in case_ids:
                        problems.append(f"{d['sheet']} linha {r} ({v}): caso relacionado inexistente {tc!r}")
        dup = sorted(k for k, n in seen.items() if n > 1)
        if dup:
            problems.append(f"ID de defeito duplicado: {dup}")
        return problems

    def _case_refs(self, value) -> list[str]:
        """IDs de caso referidos numa celula (pode ter varios, separados por
        virgula, ponto e virgula, espaco ou quebra de linha). So conta como
        referencia o que comeca por um prefixo de caso configurado."""
        if value in (None, ""):
            return []
        prefixes = tuple(self.cfg["casos"]["sheets"].keys())
        return [t for t in re.split(r"[\s,;/]+", str(value)) if t.startswith(prefixes)]
