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
"bug" (opcional) associa um defeito ja existente ao caso (so uma nota).
Outras chaves (ts, nota, ...) sao ignoradas. Um lote invalido e recusado
inteiro antes de qualquer escrita (validar_lote, tecnica-excel.md secao 4).
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
_CONTROL_HEADERS = ["hash", "aplicado_em", "etiqueta", "itens", "defeitos_novos", "lote_id"]


class ConfigError(ValueError):
    pass


class LoteInvalido(ValueError):
    """Lote recusado antes de qualquer escrita; .problemas tem a lista."""

    def __init__(self, problemas: list[str]):
        self.problemas = problemas
        super().__init__("lote invalido:\n - " + "\n - ".join(problemas))


def load_lote(path: str) -> list:
    with open(path, encoding="utf-8-sig") as f:  # -sig: aceita ficheiros gravados com BOM
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("resultados"), list):
        raise LoteInvalido([f"{path}: esperado {{\"resultados\": [...]}} (tecnica-excel.md secao 4)"])
    return data["resultados"]


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
    def from_dict(cls, config: dict, base_dir: str | None = None) -> ExcelAdapter:
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
          defeito_existente: modelo com {id} (item com "bug")
        controlo_lotes (opc.): {sheet} -- sheet escondida com os lotes aplicados
        recalc (opc.): {mecanismo: libreoffice | excel-com, timeout: int}
        """
        return cls(config, base_dir)

    @classmethod
    def from_yaml(cls, path: str, secao: str | None = None) -> ExcelAdapter:
        import yaml

        with open(path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f) or {}
        if secao:
            if secao not in data:
                raise ConfigError(f"{path}: secao '{secao}' em falta")
            data = data[secao]
        return cls(data, base_dir=os.path.dirname(os.path.abspath(path)))

    @classmethod
    def from_projeto(cls, workspace: str = ".") -> ExcelAdapter:
        """Le a secao `excel:` do projeto.yaml do workspace (tarefa 1.3 do
        plano). O caminho do workbook e relativo ao workspace."""
        path = os.path.join(workspace, "projeto.yaml")
        if not os.path.exists(path):
            raise ConfigError(f"nao existe {path}")
        return cls.from_yaml(path, secao="excel")

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
        _req(d, "estado_inicial", "defeitos.")
        d.setdefault("linha_cabecalho", 1)
        for key in ("campos_lote", "valores_omissao"):
            d[key] = d.get(key) or {}
        d["prioridades"] = d.get("prioridades") or []
        d["estados_fechados"] = d.get("estados_fechados") or []
        bad = [e for e in d["estados_fechados"] if e not in d["estados"]]
        if bad:
            raise ConfigError(f"configuracao: defeitos.estados_fechados fora de defeitos.estados: {bad}")
        if d["estado_inicial"] not in d["estados"]:
            raise ConfigError("configuracao: defeitos.estado_inicial fora de defeitos.estados")
        pre, _, pos = fmt.partition("{n}")
        d["_id_re"] = re.compile("^" + re.escape(pre) + r"(\d+)" + re.escape(pos) + "$")

        # blocos opcionais deixados em branco (como no template) contam como ausentes
        v = cfg.get("vista_ativos")
        if v and v.get("sheet"):
            v.setdefault("linha_cabecalho", 1)
            v["colunas"] = v.get("colunas") or {}
            if not (v.get("resumo") or {}).get("celula"):
                v["resumo"] = None
        else:
            cfg["vista_ativos"] = None

        r = cfg.get("aba_resumo")
        if r and r.get("sheet"):
            _req(r, "coluna_area", "aba_resumo.")
            r.setdefault("linha_cabecalho", 1)
            r["colunas_estado"] = r.get("colunas_estado") or {}
            fora = [e for e in r["colunas_estado"] if e not in casos["estados"]]
            if fora:
                raise ConfigError(f"configuracao: aba_resumo.colunas_estado com estados fora de casos.estados: {fora}")
            if r.get("coluna_percentagem"):
                pend = _req(r, "estado_por_testar", "aba_resumo.")
                if pend not in casos["estados"]:
                    raise ConfigError(f"configuracao: aba_resumo.estado_por_testar {pend!r} fora de casos.estados")
                tol = r.get("tolerancia_percentagem")
                r["tolerancia_percentagem"] = 0.001 if tol in (None, "") else float(tol)
        else:
            cfg["aba_resumo"] = None

        defaults = {
            "notas": {"resultado": "Resultado: {resultado}", "defeito_novo": "Defeito novo: {id}.",
                      "defeito_existente": "Defeito associado: {id}."},
            "controlo_lotes": {"sheet": CONTROL_SHEET_DEFAULT},
            "recalc": {"mecanismo": "libreoffice", "timeout": 60},
        }
        for block, vals in defaults.items():
            cfg[block] = cfg.get(block) or {}
            for k, val in vals.items():
                if cfg[block].get(k) in (None, ""):
                    cfg[block][k] = val
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
        r = self.cfg.get("aba_resumo")
        if r:
            if r["sheet"] not in wb.sheetnames:
                problems.append(f"sheet do resumo em falta: '{r['sheet']}'")
            else:
                h = self._headers(wb[r["sheet"]], r["linha_cabecalho"])
                for role in ("coluna_area", "coluna_total", "coluna_percentagem"):
                    if r.get(role) and r[role] not in h:
                        problems.append(f"'{r['sheet']}': coluna '{r[role]}' ({role}) em falta")
                if not any(r["colunas_estado"].get(e, e) in h for e in casos["estados"]):
                    problems.append(f"'{r['sheet']}': nenhuma coluna com o nome de um estado (usar colunas_estado)")
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
            ws = wb[name]
            if create:  # sheets de versoes anteriores ganham as colunas novas (ex.: lote_id)
                existentes = [c.value for c in ws[1]]
                for col in _CONTROL_HEADERS:
                    if col not in existentes:
                        ws.cell(1, len(existentes) + 1).value = col
                        existentes.append(col)
            return ws
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
        cab = [c.value for c in ws[1]]  # pela linha de cabecalho: sheets antigas nao tem lote_id
        return [dict(zip(cab, row)) for row in ws.iter_rows(min_row=2, values_only=True) if row[0]]

    def lote_ja_aplicado(self, lote: list) -> dict | None:
        h = lote_hash(lote)
        return next((x for x in self.lotes_aplicados() if x["hash"] == h), None)

    # ------------------------------------------------------------------
    # Aplicacao de um lote
    # ------------------------------------------------------------------

    def validar_lote(self, lote: list) -> list[str]:
        """So leitura. Problemas que impedem aplicar o lote (vazio = pode
        aplicar-se): tecnica-excel.md secao 4."""
        casos, d = self.cfg["casos"], self.cfg["defeitos"]
        if not isinstance(lote, list) or not lote:
            return ["lote vazio"]
        existentes = {str(v).strip() for _, ws, h in self._case_sheets() for _, v in self._case_rows(ws, h)}
        defeitos = {str(v).strip() for _, v in self._defect_rows()[2]}
        estados, prios = casos["estados"], d["prioridades"]
        problemas, vistos = [], Counter()
        for i, item in enumerate(lote, 1):
            if not isinstance(item, dict):
                problemas.append(f"item {i}: nao e um objeto")
                continue
            tc = str(item.get("tc") or "").strip()
            onde = f"item {i} ({tc or 'sem tc'})"
            if not tc:
                problemas.append(f"{onde}: falta 'tc'")
            elif tc not in existentes:
                problemas.append(f"{onde}: caso nao existe no workbook")
            vistos[tc] += 1
            if item.get("estado") in (None, ""):
                problemas.append(f"{onde}: falta 'estado'")
            elif item["estado"] not in estados:
                problemas.append(f"{onde}: estado {item['estado']!r} fora de {estados}"
                                 + (" -- resolver a Duvida antes de aplicar (metodologia 8.5)"
                                    if str(item["estado"]).lower().startswith(("dúvida", "duvida")) else ""))
            bug = item.get("bug")
            if bug not in (None, "") and str(bug).strip() not in defeitos:
                problemas.append(f"{onde}: bug {bug!r} nao existe em '{d['sheet']}'")
            novo = item.get("defeito_novo")
            if novo not in (None, {}):
                if not isinstance(novo, dict):
                    problemas.append(f"{onde}: 'defeito_novo' tem de ser um objeto ou null")
                elif prios:
                    pr = novo.get("prioridade", novo.get("severidade"))
                    if pr not in prios:
                        problemas.append(f"{onde}: prioridade do defeito novo {pr!r} fora de {prios}")
        dup = sorted(tc for tc, n in vistos.items() if tc and n > 1)
        if dup:
            problemas.append(f"caso repetido no lote: {dup}")
        return problemas

    def apply_lote(self, lote: list, etiqueta: str | None = None,
                   reportado_por: str = "QA automatizado", lote_id: str | None = None) -> dict:
        """Aplica o lote: estado, resultado obtido e observacoes por caso, e
        cria os defeitos novos. Um lote ja aplicado a este workbook (mesmo
        conteudo) nao e reaplicado: devolve {"aplicado": False, ...} sem
        alterar nada (tecnica-excel.md secao 5). Com `lote_id` (o aplicar usa
        o nome do ficheiro), um lote com esse id ja aplicado mas com conteudo
        diferente -- um lote editado depois de aplicado -- e recusado.

        O lote inteiro e validado antes de qualquer escrita (validar_lote);
        se tiver problemas, levanta LoteInvalido e nada muda."""
        self._need_wb()
        h_lote = lote_hash(lote)
        anterior = self.lote_ja_aplicado(lote)
        if anterior:
            return {"aplicado": False, "hash": h_lote, "novos_defeitos": [],
                    "motivo": f"lote ja aplicado em {anterior['aplicado_em']}"}
        if lote_id:
            mesmo_id = next((x for x in self.lotes_aplicados() if x.get("lote_id") == lote_id), None)
            if mesmo_id:
                raise LoteInvalido([(
                    f"o lote '{lote_id}' ja foi aplicado em {mesmo_id['aplicado_em']} com OUTRO conteudo "
                    "(foi editado depois de aplicado). Reaplica-lo repetiria notas e defeitos. Poe a "
                    "correcao num lote novo, com outro nome, so com os casos a corrigir "
                    "(tecnica-excel.md secao 5)."
                )])

        etiqueta = etiqueta or f"[{date.today().isoformat()}]"
        casos, d, notas = self.cfg["casos"], self.cfg["defeitos"], self.cfg["notas"]
        ccol = casos["colunas"]

        problemas = self.validar_lote(lote)
        if problemas:
            raise LoteInvalido(problemas)

        # mapa caso -> (ws, headers, linha)
        rowmap = {}
        for _sheet, ws, h in self._case_sheets():
            for r, v in self._case_rows(ws, h):
                rowmap.setdefault(str(v).strip(), (ws, h, r))

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
                if item.get("bug") not in (None, ""):
                    partes.append(notas["defeito_existente"].format(id=str(item["bug"]).strip()))
                if partes:
                    nota = f"{etiqueta} " + " ".join(partes)
                    c = ws.cell(r, h[ccol["observacoes"]])
                    c.value = (str(c.value).rstrip() + "\n\n" + nota).strip() if c.value else nota

            if def_novo:
                last_def_row += 1
                vals = dict(d["valores_omissao"])
                vals.update(def_novo)

                def put(col, value, row=last_def_row):
                    if col:
                        ws_def.cell(row, h_def[col]).value = value

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
        registo = {"hash": h_lote, "aplicado_em": datetime.now().isoformat(timespec="seconds"),
                   "etiqueta": etiqueta, "itens": len(lote), "defeitos_novos": ", ".join(novos),
                   "lote_id": lote_id}
        ctl.append([registo.get(c.value) for c in ctl[1]])
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

    def defeitos_abertos(self, ordenar_por: str | None = None) -> dict:
        """So leitura. {"abertos": [linha como dict], "fechados": n}: os
        defeitos cujo estado nao esta em estados_fechados, ordenados por
        prioridade, por `ordenar_por` (coluna dos defeitos, opcional) e pelo
        numero do defeito."""
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

        def key(row):
            p = row.get(prio_col) if prio_col else None
            pk = prios.index(p) if p in prios else len(prios)
            n = self._defect_number(row.get(d["colunas"]["id"]))
            sec = str(row.get(ordenar_por) or "") if ordenar_por else ""
            return (pk, sec, n if n is not None else float("inf"))

        rows.sort(key=key)
        return {"abertos": rows, "fechados": closed}

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
        ab = self.defeitos_abertos(v.get("ordenar_por"))
        rows, closed = ab["abertos"], ab["fechados"]

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

    def verificar_resumo(self, path: str | None = None) -> dict:
        """So leitura. Compara os valores CALCULADOS da aba de resumo
        (excel.aba_resumo) com a contagem direta dos estados, ambos lidos do
        ficheiro gravado em `path` (por omissao o workbook): por area, por
        estado, no total da area e na linha de total (tecnica-excel.md
        secao 5). Devolve {"verificado", "problemas", "motivo"}; verificado
        e False sem aba_resumo ou sem valores calculados (falta recalcular)."""
        r = self.cfg.get("aba_resumo")
        if not r:
            return {"verificado": False, "problemas": [], "motivo": "aba_resumo nao configurada"}
        wb = openpyxl.load_workbook(path or self.path, data_only=True)
        leitor = copy.copy(self)
        leitor.wb = wb
        direta = {area: Counter(c) for area, c in leitor.progress_summary().items()}
        total = sum(direta.values(), Counter())

        ws = wb[r["sheet"]]
        h = self._headers(ws, r["linha_cabecalho"])
        col_area = h[r["coluna_area"]]
        col_est = {e: h[r["colunas_estado"].get(e, e)] for e in self.cfg["casos"]["estados"]
                   if r["colunas_estado"].get(e, e) in h}
        col_tot = h.get(r["coluna_total"]) if r.get("coluna_total") else None
        # % executada (metodologia 16.3): (total - por testar) / total; N/A e Blocked contam
        col_pct = h.get(r["coluna_percentagem"]) if r.get("coluna_percentagem") else None
        pend, tol_pct = r.get("estado_por_testar"), r.get("tolerancia_percentagem")

        problemas, vistas, celulas, vazias = [], set(), 0, 0
        linha_total_vista = False
        for row in range(r["linha_cabecalho"] + 1, ws.max_row + 1):
            area = ws.cell(row, col_area).value
            area = "" if area is None else str(area).strip()
            if r.get("linha_total") and area == r["linha_total"]:
                alvo, nome, linha_total_vista = total, "linha de total", True
            elif area in direta:
                alvo, nome = direta[area], f"area '{area}'"
                vistas.add(area)
            else:
                continue  # titulos, notas e linhas vazias nao sao areas
            comparar = [(e, c, alvo.get(e, 0)) for e, c in col_est.items()]
            if col_tot:
                comparar.append(("total", col_tot, sum(alvo.values())))
            if col_pct:
                n = sum(alvo.values())
                comparar.append(("% executada", col_pct, (n - alvo.get(pend, 0)) / n if n else 0.0))
            for rotulo, col, esperado in comparar:
                cel = ws.cell(row, col)
                celulas += 1
                if cel.value is None:
                    vazias += 1
                    continue
                try:
                    v = float(cel.value)
                    if col == col_pct:  # fracao (0.2857) ou pontos percentuais (28.57)
                        igual = min(abs(v - esperado * 100), abs(v - esperado) * 100) <= tol_pct
                    else:
                        igual = v == esperado
                except (TypeError, ValueError):
                    igual = False
                if not igual:
                    regra = (f"(total - {pend}) / total da {esperado:.4%}" if col == col_pct
                             else f"a contagem direta da {esperado}")
                    problemas.append(f"{r['sheet']}!{cel.coordinate} ({nome}, {rotulo}): o resumo diz "
                                     f"{cel.value!r}, {regra}")
        if celulas and vazias == celulas:
            return {"verificado": False, "problemas": [],
                    "motivo": "o resumo nao tem valores calculados -- recalcular primeiro"}
        if vazias:
            problemas.append(f"{r['sheet']}: {vazias} celula(s) sem valor calculado")
        problemas += [f"{r['sheet']}: a area '{a}' nao aparece no resumo" for a in direta if a not in vistas]
        if r.get("linha_total") and not linha_total_vista:
            problemas.append(f"{r['sheet']}: linha de total '{r['linha_total']}' nao encontrada")
        return {"verificado": True, "problemas": problemas, "motivo": None}

    def _case_refs(self, value) -> list[str]:
        """IDs de caso referidos numa celula (pode ter varios, separados por
        virgula, ponto e virgula, espaco ou quebra de linha). So conta como
        referencia o que comeca por um prefixo de caso configurado."""
        if value in (None, ""):
            return []
        prefixes = tuple(self.cfg["casos"]["sheets"].keys())
        return [t for t in re.split(r"[\s,;/]+", str(value)) if t.startswith(prefixes)]
