#!/usr/bin/env python3
"""
Converts a workspace made before the translation (plan 8.11) to the English names and keys.

    python scripts/migrate_workspace.py <workspace>            (dry run: lists what would change)
    python scripts/migrate_workspace.py <workspace> --apply    (writes)

What it changes:
- `projeto.yaml` -> `project.yaml`, with the keys and the values the framework defines in English
  (`modulos` -> `modules`, `remover` -> `remove`, templates `{resultado}` -> `{result}`, ...). Values that come
  from the project (sheet, column and status names, texts) are kept. When the project used the adapter's
  old defaults, they are pinned (note texts, the `_qa_lotes` control sheet, the reporter "QA automatizado"), so
  the next batch writes the same cells as before.
- Batches (`{"resultados": ...}`) in the run log folder: keys in English, and `translated_from` with the hash of
  the original content, which stays the batch's identity (a batch already applied is still recognised).
- Golden folders: `golden.yaml` keys, `antes.xlsx` -> `before.xlsx`, `depois.xlsx` -> `after.xlsx`. The batch
  file keeps its name and content (its name is the batch id recorded in the workbook); the golden check converts
  it in memory.
- `contexto.md` -> `context.md`, `historico/` -> `history/` (every file byte for byte, checked by hash),
  `painel.html` -> `dashboard.html`, `.api-sessao.env` -> `.api-session.env`; `.gitignore`, the protection rule
  in `.claude/settings.json` and the framework names in `CLAUDE.md` and `context.md` (documents, commands,
  scripts).

Refuses a workspace with uncommitted changes (git), so every change can be reviewed and undone. Running it
again on a converted workspace changes nothing.

Exit codes: 0 converted or nothing to do, 1 dry run with changes to make, 2 refused (usage, uncommitted changes,
invalid file).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys

import yaml

# ---------------------------------------------------------------------------
# Keys and values (the only place with the old vocabulary, plus the adapter's legacy control headers)
# ---------------------------------------------------------------------------

TOP_KEYS = {"projeto": "project", "empresa": "company", "ambientes": "environments", "formato_bug": "bug_format",
            "bugs_resolvidos": "resolved_bugs", "evidencia": "evidence", "modulos": "modules",
            "registo_leve": "run_log", "acessibilidade": "accessibility", "painel": "dashboard"}
RESOLVED_BUGS = {"remover": "remove", "arquivar": "archive"}
EVIDENCE = {"texto": "text", "texto+screenshot": "text+screenshot"}
MODULES = {"funcional-web": "functional-web", "exploratorio": "exploratory", "acessibilidade": "accessibility"}
ENVIRONMENTS = {"teste": "test", "producao": "production", "produção": "production"}
API_KEYS = {"contrato": "contract", "autenticacao": "auth", "credenciais": "credentials"}
AUTH = {"nenhuma": "none"}
CREDENTIAL_KEYS = {"var_utilizador": "user_var", "var_palavra_passe": "pass_var"}
A11Y_KEYS = {"norma": "standard", "paginas": "pages", "auditar_paginas_com_erro_http": "audit_error_pages"}
A11Y_LOGIN_KEYS = {"campo_utilizador": "user_field", "campo_palavra_passe": "pass_field", "botao": "button",
                   "sucesso_url": "success_url", "sucesso_seletor": "success_selector", **CREDENTIAL_KEYS}
PAGE_KEYS = {"nome": "name"}
MOBILE_KEYS = {"plataforma": "platform", "variaveis": "variables"}
DASHBOARD_KEYS = {"estados": "states"}
DASHBOARD_TYPES = {"pendente": "pending", "falha": "fail", "bloqueado": "blocked"}

EXCEL_KEYS = {"ficheiro": "file", "casos": "cases", "defeitos": "defects", "aba_resumo": "summary",
              "vista_ativos": "active_view", "notas": "notes", "controlo_lotes": "batch_control"}
CASES_KEYS = {"linha_cabecalho": "header_row", "colunas": "columns", "estados": "statuses"}
CASE_COLUMNS = {"estado": "status", "resultado_obtido": "actual_result", "observacoes": "notes"}
DEFECTS_KEYS = {"linha_cabecalho": "header_row", "formato_id": "id_format", "colunas": "columns",
                "campos_lote": "batch_fields", "valores_omissao": "defaults", "estados": "statuses",
                "estados_fechados": "closed_statuses", "estado_inicial": "initial_status",
                "prioridades": "priorities", "reportado_por": "reported_by"}
DEFECT_COLUMNS = {"estado": "status", "caso": "case", "severidade": "severity", "prioridade": "priority",
                  "reportado_por": "reported_by", "data_report": "report_date"}
SUMMARY_KEYS = {"linha_cabecalho": "header_row", "coluna_area": "area_column", "coluna_total": "total_column",
                "linha_total": "total_row", "colunas_estado": "status_columns", "coluna_percentagem": "percent_column",
                "estado_por_testar": "not_run_status", "tolerancia_percentagem": "percent_tolerance"}
VIEW_KEYS = {"linha_cabecalho": "header_row", "colunas": "columns", "ordenar_por": "sort_by", "resumo": "summary"}
VIEW_SUMMARY_KEYS = {"celula": "cell", "modelo": "template"}
NOTES_KEYS = {"resultado": "result", "defeito_novo": "new_defect", "defeito_existente": "existing_defect"}
RECALC_KEYS = {"mecanismo": "mechanism"}
# placeholders of the templates (notes and the active view's summary)
PLACEHOLDERS = [("{resultado}", "{result}"), ("{abertos", "{open"), ("{fechados", "{closed"),
                ("{severidade[", "{severity["), ("{data", "{date")]
# what an old workbook shows when the project used the adapter's defaults
OLD_NOTES = {"result": "Resultado: {result}", "new_defect": "Defeito novo: {id}.",
             "existing_defect": "Defeito associado: {id}."}
OLD_CONTROL_SHEET = "_qa_lotes"
OLD_REPORTED_BY = "QA automatizado"

BATCH_ITEM_KEYS = {"estado": "status", "resultado_obtido": "actual_result", "defeito_novo": "new_defect",
                   "nota": "note"}
NEW_DEFECT_KEYS = {"tc_relacionado": "related_tc", "severidade": "severity", "prioridade": "priority"}
GOLDEN_KEYS = {"agora": "now", "etiqueta": "label", "reportado_por": "reported_by"}

# the standard headings of context.md (methodology 12.2), which the dashboard reads
CONTEXT_HEADINGS = [
    (r"(?m)^# Contexto de sessão", "# Session context"), (r"(?m)^Atualizado:", "Updated:"),
    (r"(?m)^## Estado atual", "## Current state"),
    (r"(?m)^## Ponto de execução", "## Execution point"), (r"(?m)^## Trabalho concluído", "## Work done"),
    (r"(?m)^## Decisões tomadas", "## Decisions made"), (r"(?m)^## Dúvidas em aberto", "## Open questions"),
    (r"(?m)^## Problemas conhecidos", "## Known problems"), (r"(?m)^## Trabalho pendente", "## Pending work"),
    (r"(?m)^## Estado da validação", "## Validation state"),
    # the explanations in brackets of the template's headings (also on a heading converted before they were known)
    (r"(?m)^## Execution point \(último lote, último caso\)", "## Execution point (last batch, last case)"),
    (r"(?m)^## Decisions made \(e quem as tomou\)", "## Decisions made (and who made them)"),
    (r"(?m)^## Known problems / bloqueios", "## Known problems / blockers"),
]
RENAMES = [("projeto.yaml", "project.yaml"), ("contexto.md", "context.md"), ("historico", "history"),
           ("painel.html", "dashboard.html"), (".api-sessao.env", ".api-session.env")]
# framework names in a workspace's CLAUDE.md and context.md (longest first where one contains another)
TEXT_NAMES = [
    ("metodologia-core.md", "methodology.md"), ("tecnica-browser.md", "technique-browser.md"),
    ("tecnica-excel.md", "technique-excel.md"), ("tecnica-api.md", "technique-api.md"),
    ("tecnica-acessibilidade.md", "technique-accessibility.md"), ("tecnica-mobile.md", "technique-mobile.md"),
    ("heuristicas.md", "heuristics.md"), ("plano-desenvolvimento.md", "development-plan.md"),
    ("contexto-template.md", "context-template.md"), ("projeto.yaml", "project.yaml"),
    ("contexto.md", "context.md"), ("historico/", "history/"), ("painel.html", "dashboard.html"),
    ("/onboarding-projeto", "/project-onboarding"), ("/analisar-funcionalidade", "/analyze-feature"),
    ("/gerar-casos", "/generate-cases"), ("/planear-lote", "/plan-batch"), ("/executar-lote", "/run-batch"),
    ("/registar-bug", "/log-bug"), ("/fim-sessao", "/end-session"), ("/painel", "/dashboard"),
    ("/testar-api", "/test-api"), ("/auditar-acessibilidade", "/audit-accessibility"),
    ("/testar-mobile", "/test-mobile"),
    ("validar_projeto.py", "validate_project.py"), ("api_pedido.py", "api_request.py"),
    ("auditar_a11y.py", "audit_a11y.py"), ("testar_mobile.py", "run_mobile.py"),
    ("gerar_painel.py", "build_dashboard.py"), ("sincronizar_skills.py", "sync_skills.py"),
    ("adapters.excel.aplicar", "adapters.excel.apply"), ("adapters.excel.inspecionar", "adapters.excel.inspect"),
    ("gerar_workbook.py", "build_workbook.py"), ("reconstruir_workbook.py", "rebuild_workbook.py"),
    (".api-sessao.env", ".api-session.env"),
    # keys, options and module names quoted in the text (only in backticks or as options: prose is left alone)
    ("`registo_leve`", "`run_log`"), ("`acessibilidade:`", "`accessibility:`"), ("`funcional-web`", "`functional-web`"),
    ("`exploratorio`", "`exploratory`"), ("`acessibilidade`", "`accessibility`"), ("`painel:`", "`dashboard:`"),
    ("--guardar-segredo", "--save-secret"), ("--guardar", "--save"), ("--saida", "--output"), ("--abrir", "--open"),
    ("--esperado", "--expected"), ("--agora", "--now"), ("--etiqueta", "--label"),
]


def _keys(d, mapping: dict) -> dict:
    """The dict with its keys renamed by `mapping` (others kept), in the same order."""
    if not isinstance(d, dict):
        return d
    return {mapping.get(k, k): v for k, v in d.items()}


def _template(s):
    if not isinstance(s, str):
        return s
    for old, new in PLACEHOLDERS:
        s = s.replace(old, new)
    return s


def convert_project(old: dict) -> dict:
    """project.yaml in English. Project values are kept; old defaults the workbook relies on are pinned."""
    p = _keys(copy.deepcopy(old), TOP_KEYS)
    if isinstance(p.get("environments"), dict):
        p["environments"] = _keys(p["environments"], ENVIRONMENTS)
    p["resolved_bugs"] = RESOLVED_BUGS.get(p.get("resolved_bugs"), p.get("resolved_bugs"))
    p["evidence"] = EVIDENCE.get(p.get("evidence"), p.get("evidence"))
    if isinstance(p.get("modules"), list):
        p["modules"] = [MODULES.get(m, m) for m in p["modules"]]
    p = {k: v for k, v in p.items() if not (k in ("resolved_bugs", "evidence") and v is None)}

    if isinstance(p.get("api"), dict):
        api = _keys(p["api"], API_KEYS)
        api["auth"] = AUTH.get(api.get("auth"), api.get("auth"))
        if isinstance(api.get("credentials"), dict):
            api["credentials"] = {role: _keys(c, CREDENTIAL_KEYS) for role, c in api["credentials"].items()}
        p["api"] = api
    if isinstance(p.get("accessibility"), dict):
        a = _keys(p["accessibility"], A11Y_KEYS)
        a["login"] = _keys(a.get("login"), A11Y_LOGIN_KEYS)
        if isinstance(a.get("pages"), list):
            a["pages"] = [_keys(pg, PAGE_KEYS) for pg in a["pages"]]
        p["accessibility"] = a
    if isinstance(p.get("mobile"), dict):
        p["mobile"] = _keys(p["mobile"], MOBILE_KEYS)
    if isinstance(p.get("dashboard"), dict):
        dash = _keys(p["dashboard"], DASHBOARD_KEYS)
        if isinstance(dash.get("states"), dict):
            dash["states"] = {st: DASHBOARD_TYPES.get(t, t) for st, t in dash["states"].items()}
        p["dashboard"] = dash
    if isinstance(p.get("excel"), dict):
        p["excel"] = convert_excel(p["excel"])
    return p


def convert_excel(old: dict) -> dict:
    e = _keys(old, EXCEL_KEYS)
    if isinstance(e.get("cases"), dict):
        e["cases"] = _keys(e["cases"], CASES_KEYS)
        e["cases"]["columns"] = _keys(e["cases"].get("columns"), CASE_COLUMNS)
    if isinstance(e.get("defects"), dict):
        d = _keys(e["defects"], DEFECTS_KEYS)
        d["columns"] = _keys(d.get("columns"), DEFECT_COLUMNS)
        # batch_fields and defaults keep their keys: they are the project's own names, used in its batches
        d.setdefault("reported_by", OLD_REPORTED_BY)
        e["defects"] = d
    if isinstance(e.get("summary"), dict):
        e["summary"] = _keys(e["summary"], SUMMARY_KEYS)
    if isinstance(e.get("active_view"), dict):
        v = _keys(e["active_view"], VIEW_KEYS)
        if isinstance(v.get("summary"), dict):
            v["summary"] = _keys(v["summary"], VIEW_SUMMARY_KEYS)
            v["summary"]["template"] = _template(v["summary"].get("template"))
        e["active_view"] = v
    notes = {k: _template(t) for k, t in (_keys(e.get("notes"), NOTES_KEYS) or {}).items()}
    for k, text in OLD_NOTES.items():
        if notes.get(k) in (None, ""):
            notes[k] = text
    e["notes"] = notes
    ctl = e.get("batch_control") or {}
    if not ctl.get("sheet"):
        e["batch_control"] = {**ctl, "sheet": OLD_CONTROL_SHEET}
    if isinstance(e.get("recalc"), dict):
        e["recalc"] = _keys(e["recalc"], RECALC_KEYS)
    return e


def is_old_batch(doc) -> bool:
    """A batch from before the translation: {"resultados": [{"tc": ...}, ...]}. Reports of the old scripts (e.g. the
    mobile one, whose items have "caso") are not batches and stay as they are."""
    return (isinstance(doc, dict) and isinstance(doc.get("resultados"), list) and "results" not in doc
            and all(isinstance(x, dict) and "tc" in x for x in doc["resultados"]))


def convert_batch(doc: dict) -> dict:
    """A batch in English, with `translated_from`: the hash of the original results (the batch's identity in the
    workbook's control sheet). Same function as the adapter's batch_hash, repeated to keep this script standalone."""
    original = doc["resultados"]
    canon = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    out = {k: v for k, v in doc.items() if k != "resultados"}
    items = []
    for item in original:
        if isinstance(item, dict):
            item = _keys(item, BATCH_ITEM_KEYS)
            if isinstance(item.get("new_defect"), dict):
                item["new_defect"] = _keys(item["new_defect"], NEW_DEFECT_KEYS)
        items.append(item)
    out["results"] = items
    out["translated_from"] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return out


def convert_golden(g: dict) -> dict:
    return _keys(g or {}, GOLDEN_KEYS)


def convert_text(text: str) -> str:
    for old, new in TEXT_NAMES:
        text = re.sub(re.escape(old) + r"(?![\w-])", new, text)
    return text


def convert_context(text: str) -> str:
    """context.md: the framework names and the standard headings; the project's own text is kept."""
    text = convert_text(text)
    for pattern, new in CONTEXT_HEADINGS:
        text = re.sub(pattern, new, text)
    return text


# ---------------------------------------------------------------------------
# The workspace
# ---------------------------------------------------------------------------


class Refused(Exception):
    pass


def _hashes(root: str) -> dict:
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)


def _git_dirty(ws: str) -> str:
    if not os.path.isdir(os.path.join(ws, ".git")):
        return ""
    r = subprocess.run(["git", "-C", ws, "status", "--porcelain"], capture_output=True, text=True,
                       encoding="utf-8", check=False)
    return r.stdout.strip()


def plan(ws: str) -> list[tuple[str, str, object]]:
    """[(kind, path relative to the workspace, payload)]: 'write' (new text), 'rename' (new relative path)."""
    steps = []
    old_proj, new_proj = os.path.join(ws, "projeto.yaml"), os.path.join(ws, "project.yaml")
    proj = None
    if os.path.isfile(old_proj):
        with open(old_proj, encoding="utf-8-sig") as f:
            proj = convert_project(yaml.safe_load(f) or {})
        steps.append(("write", "project.yaml", _dump_yaml(proj)))
        steps.append(("remove", "projeto.yaml", None))
    elif os.path.isfile(new_proj):
        with open(new_proj, encoding="utf-8-sig") as f:
            proj = yaml.safe_load(f) or {}
    else:
        raise Refused(f"{ws}: neither projeto.yaml nor project.yaml -- not a workspace")

    run_log = (proj.get("run_log") or "runs/").strip("/\\") or "runs"
    runs = os.path.join(ws, run_log)
    if os.path.isdir(runs):
        for d, _, files in os.walk(runs):
            for f in sorted(files):
                if not f.endswith(".json"):
                    continue
                p = os.path.join(d, f)
                try:
                    with open(p, encoding="utf-8-sig") as fh:
                        doc = json.load(fh)
                except (OSError, ValueError) as e:
                    raise Refused(f"{p}: not valid JSON ({e})") from None
                if is_old_batch(doc):
                    steps.append(("write", os.path.relpath(p, ws),
                                  json.dumps(convert_batch(doc), ensure_ascii=False, indent=2) + "\n"))

    golden = os.path.join(ws, "golden")
    if os.path.isdir(golden):
        for d, _, files in os.walk(golden):
            rel = os.path.relpath(d, ws)
            if "golden.yaml" in files:
                with open(os.path.join(d, "golden.yaml"), encoding="utf-8-sig") as fh:
                    text = fh.read()
                g = yaml.safe_load(text) or {}
                if set(g) & set(GOLDEN_KEYS):
                    new = text
                    for old, key in GOLDEN_KEYS.items():
                        new = re.sub(rf"(?m)^{old}:", f"{key}:", new)
                    steps.append(("write", os.path.join(rel, "golden.yaml"), new))
            for old, new in (("antes.xlsx", "before.xlsx"), ("depois.xlsx", "after.xlsx")):
                if old in files:
                    steps.append(("rename", os.path.join(rel, old), os.path.join(rel, new)))

    for old, new in RENAMES[1:]:
        if os.path.exists(os.path.join(ws, old)):
            if os.path.exists(os.path.join(ws, new)):
                raise Refused(f"both {old} and {new} exist in {ws}: merge them by hand first")
            steps.append(("rename", old, new))

    gi = os.path.join(ws, ".gitignore")
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as fh:
            text = fh.read()
        new = text
        for old, name in RENAMES:
            new = re.sub(rf"(?m)^(/?){re.escape(old)}(/?)[ 	]*$", rf"\g<1>{name}\g<2>", new)
        if new != text:
            steps.append(("write", ".gitignore", new))
    st = os.path.join(ws, ".claude", "settings.json")
    if os.path.isfile(st):
        with open(st, encoding="utf-8") as fh:
            text = fh.read()
        new = text.replace("/historico/", "/history/")
        if new != text:
            steps.append(("write", os.path.join(".claude", "settings.json"), new))
    for name, new_name, convert in (("CLAUDE.md", "CLAUDE.md", convert_text),
                                    ("contexto.md", "context.md", convert_context),
                                    ("context.md", "context.md", convert_context)):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            new = convert(text)
            if new != text:
                steps.append(("write", new_name, new))
    for name in (".api-sessao.env", ".api-session.env"):
        p = os.path.join(ws, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            new = re.sub(r"(?m)^#segredo ", "#secret ", text).replace("scripts/api_pedido.py", "scripts/api_request.py")
            if new != text:     # values are never printed: the plan lists only the path
                steps.append(("write", ".api-session.env", new))
    return steps


def apply_steps(ws: str, steps) -> None:
    # renames first (a write to context.md needs contexto.md moved), then writes, then removals
    history_before = _hashes(os.path.join(ws, "historico")) if os.path.isdir(os.path.join(ws, "historico")) else None
    for kind, rel, payload in steps:
        if kind == "rename":
            os.replace(os.path.join(ws, rel), os.path.join(ws, payload))
    for kind, rel, payload in steps:
        if kind == "write":
            with open(os.path.join(ws, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(payload)
    for kind, rel, _ in steps:
        if kind == "remove":
            os.remove(os.path.join(ws, rel))
    if history_before is not None and _hashes(os.path.join(ws, "history")) != history_before:
        raise Refused("history/ is not byte for byte the old historico/ -- check it before anything else")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Converts a workspace to the English names and keys (plan 8.11)")
    ap.add_argument("workspace")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: only list them)")
    a = ap.parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        if not os.path.isdir(ws):
            raise Refused(f"{ws} is not a folder")
        steps = plan(ws)
        if not steps:
            print(f"{ws}: already converted, nothing to do.")
            return 0
        for kind, rel, payload in steps:
            print(f"  {kind:<6} {rel}" + (f" -> {payload}" if kind == "rename" else ""))
        if not a.apply:
            print(f"{len(steps)} change(s) to make. Run again with --apply to write them.")
            return 1
        dirty = _git_dirty(ws)
        if dirty:
            raise Refused("the workspace has uncommitted changes; commit them first, so the conversion can be "
                          "reviewed and undone:\n" + dirty)
        apply_steps(ws, steps)
    except Refused as e:
        print(f"NOT CONVERTED -- {e}", file=sys.stderr)
        return 2
    print(f"{len(steps)} change(s) written. Check with `git -C {ws} diff --stat`, then run "
          "`python scripts/validate_project.py` on the workspace.")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
