"""scripts/migrate_workspace.py (plan 8.11): a workspace from before the translation, converted to the English
names and keys. Project values are kept, the old defaults a workbook relies on are pinned, history/ is moved
byte for byte, and running it again changes nothing.

python -m unittest discover -s tests   (from the root of qa-assistant)
A fictional old workspace in a temporary folder; no LibreOffice.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import migrate_workspace as mw

from adapters.excel import batch_hash

OLD_PROJECT = """projeto: "loja-demo"
empresa: "demo"
ambientes:
  teste: "https://loja.demo/"
tracker: "excel"
formato_bug: "BUG-{n}"
bugs_resolvidos: "arquivar"
evidencia: "texto+screenshot"
modulos: [funcional-web, exploratorio, api, acessibilidade, mobile]
registo_leve: "runs/"
api:
  base_url: "https://loja.demo/api"
  autenticacao: "nenhuma"
  credenciais:
    admin: {var_utilizador: LOJA_U, var_palavra_passe: LOJA_P}
acessibilidade:
  norma: "WCAG 2.1 AA"
  auditar_paginas_com_erro_http: true
  login: {url: "https://loja.demo/", campo_utilizador: "#u", campo_palavra_passe: "#p", botao: "#b",
          var_utilizador: LOJA_U, var_palavra_passe: LOJA_P, sucesso_url: "/conta"}
  paginas:
    - {nome: início, url: "https://loja.demo/", login: false}
mobile:
  plataforma: "web"
  flows: "flows"
  variaveis: [LOJA_U]
painel:
  estados: {"Por testar": pendente, "Passou": ok, "Falhou": falha, "Bloqueado": bloqueado, "N/A": na}
excel:
  ficheiro: "workbook/plano.xlsx"
  casos:
    sheets: {"CT-LOJA-": "Loja"}
    linha_cabecalho: 1
    colunas: {id: "ID", estado: "Estado", resultado_obtido: "Resultado Obtido", observacoes: "Observações"}
    estados: ["Por testar", "Passou", "Falhou", "Bloqueado", "N/A"]
  defeitos:
    sheet: "Defeitos"
    formato_id: "BUG-{n}"
    colunas: {id: "ID", estado: "Estado", caso: "Caso", severidade: "Severidade", reportado_por: "Reportado por"}
    campos_lote: {titulo: "Título"}
    estados: ["Aberto", "Fechado"]
    estados_fechados: ["Fechado"]
    estado_inicial: "Aberto"
  aba_resumo:
    sheet: "Resumo"
    coluna_area: "Área"
    coluna_percentagem: "% Executado"
    estado_por_testar: "Por testar"
  vista_ativos:
    sheet: "Defeitos Ativos"
    ordenar_por: "Área"
    resumo: {celula: "A2", modelo: "{abertos} abertos, {severidade[Alta]} Alta, {fechados} fechados, {data:%d.%m.%Y}"}
  notas:
    resultado: "Obtido: {resultado}"
  recalc:
    mecanismo: "libreoffice"
"""
OLD_BATCH = {"resultados": [
    {"tc": "CT-LOJA-001", "estado": "Passou", "resultado_obtido": "ok", "ts": "2026-09-20T10:00:00"},
    {"tc": "CT-LOJA-002", "estado": "Falhou", "resultado_obtido": "erro 500", "nota": "x",
     "defeito_novo": {"titulo": "Erro 500", "severidade": "Alta", "prioridade": "Alta", "tc_relacionado": "CT-LOJA-002"}},
    {"tc": "CT-LOJA-003", "estado": "Falhou", "resultado_obtido": "igual", "bug": "BUG-1"},
]}
OLD_CONTEXT = """# Contexto de sessão — loja-demo
Atualizado: 2026-09-20 10:00

## Estado atual
Lote aplicado com /executar-lote; ver projeto.yaml e historico/.

## Ponto de execução (último lote, último caso)
- lote 01

## Problemas conhecidos / bloqueios

## Trabalho pendente
- correr /painel
"""


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Migrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = self.ws = self.tmp.name
        files = {
            "projeto.yaml": OLD_PROJECT,
            "contexto.md": OLD_CONTEXT,
            "CLAUDE.md": "- @../../qa-assistant/docs/metodologia-core.md\n- tecnica-browser.md, /registar-bug\n"
                         "- python scripts/validar_projeto.py; python -m adapters.excel.aplicar\n",
            ".gitignore": ".env\n.api-sessao.env\npainel.html\n",
            ".claude/settings.json": '{"permissions": {"deny": ["Edit(/historico/**)"]}}\n',
            ".api-sessao.env": "# sessao de API do qa-assistant (scripts/api_pedido.py) -- NAO vai para o git\n"
                               "#segredo TOKEN\nTOKEN=fictional-token\nID=42\n",
            "runs/lote_20260920_01.json": json.dumps(OLD_BATCH, ensure_ascii=False, indent=1),
            "runs/a11y_20260920_01.json": json.dumps({"norma": "WCAG 2.1 AA", "paginas": []}),
            "runs/mobile_20260920_01.json": json.dumps({"plataforma": "web", "resultados": [{"caso": "CT-MOB-001"}]}),
            "historico/lote_antigo.json": json.dumps(OLD_BATCH),
            "historico/relatorio.md": "# Relatório\nnão mexer\n",
            "golden/lote01/golden.yaml": "agora: '2026-09-20T10:00:00'\netiqueta: '[2026-09-20, lote_x]'\n",
            "golden/lote01/lote_20260920_01.json": json.dumps(OLD_BATCH),
            "golden/lote01/antes.xlsx": "not really a workbook",
            "golden/lote01/depois.xlsx": "not really a workbook either",
        }
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(ws, rel)) or ws, exist_ok=True)
            with open(os.path.join(ws, rel), "w", encoding="utf-8") as f:
                f.write(text)
        self.history = {rel: sha(os.path.join(ws, rel)) for rel in files if rel.startswith("historico/")}

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = mw.main([self.ws, *args])
        return rc, out.getvalue() + err.getvalue()

    def read(self, rel):
        with open(os.path.join(self.ws, rel), encoding="utf-8") as f:
            return f.read()

    def test_dry_run_lists_and_changes_nothing(self):
        before = sorted(os.listdir(self.ws))
        rc, out = self.run_it()
        self.assertEqual(rc, 1)
        self.assertIn("project.yaml", out)
        self.assertIn("--apply", out)
        self.assertEqual(sorted(os.listdir(self.ws)), before)
        self.assertNotIn("fictional-token", out)

    def test_project_keys_and_framework_values_in_english_project_values_kept(self):
        self.assertEqual(self.run_it("--apply")[0], 0, self.run_it()[1])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "projeto.yaml")))
        p = yaml.safe_load(self.read("project.yaml"))
        self.assertEqual((p["project"], p["company"], p["environments"]), ("loja-demo", "demo",
                                                                           {"test": "https://loja.demo/"}))
        self.assertEqual((p["resolved_bugs"], p["evidence"], p["run_log"]), ("archive", "text+screenshot", "runs/"))
        self.assertEqual(p["modules"], ["functional-web", "exploratory", "api", "accessibility", "mobile"])
        self.assertEqual(p["api"]["auth"], "none")
        self.assertEqual(p["api"]["credentials"]["admin"], {"user_var": "LOJA_U", "pass_var": "LOJA_P"})
        a = p["accessibility"]
        self.assertEqual((a["standard"], a["audit_error_pages"]), ("WCAG 2.1 AA", True))
        self.assertEqual(a["login"]["user_field"], "#u")
        self.assertEqual(a["login"]["success_url"], "/conta")
        self.assertEqual(a["pages"], [{"name": "início", "url": "https://loja.demo/", "login": False}])
        self.assertEqual(p["mobile"], {"platform": "web", "flows": "flows", "variables": ["LOJA_U"]})
        self.assertEqual(p["dashboard"]["states"], {"Por testar": "pending", "Passou": "ok", "Falhou": "fail",
                                                    "Bloqueado": "blocked", "N/A": "na"})
        e = p["excel"]
        self.assertEqual(e["file"], "workbook/plano.xlsx")
        self.assertEqual(e["cases"]["columns"], {"id": "ID", "status": "Estado", "actual_result": "Resultado Obtido",
                                                 "notes": "Observações"})
        self.assertEqual(e["cases"]["statuses"], ["Por testar", "Passou", "Falhou", "Bloqueado", "N/A"])
        self.assertEqual(e["defects"]["columns"]["reported_by"], "Reportado por")
        self.assertEqual(e["defects"]["batch_fields"], {"titulo": "Título"})      # the project's own keys
        self.assertEqual((e["defects"]["closed_statuses"], e["defects"]["initial_status"]), (["Fechado"], "Aberto"))
        self.assertEqual((e["summary"]["percent_column"], e["summary"]["not_run_status"]), ("% Executado", "Por testar"))
        self.assertEqual(e["active_view"]["sort_by"], "Área")
        self.assertEqual(e["active_view"]["summary"]["template"],
                         "{open} abertos, {severity[Alta]} Alta, {closed} fechados, {date:%d.%m.%Y}")
        self.assertEqual(e["recalc"], {"mechanism": "libreoffice"})

    def test_old_defaults_the_workbook_relies_on_are_pinned(self):
        self.run_it("--apply")
        e = yaml.safe_load(self.read("project.yaml"))["excel"]
        self.assertEqual(e["notes"], {"result": "Obtido: {result}", "new_defect": "Defeito novo: {id}.",
                                      "existing_defect": "Defeito associado: {id}."})
        self.assertEqual(e["batch_control"], {"sheet": "_qa_lotes"})
        self.assertEqual(e["defects"]["reported_by"], "QA automatizado")

    def test_batches_in_english_with_the_original_identity(self):
        self.run_it("--apply")
        doc = json.loads(self.read("runs/lote_20260920_01.json"))
        self.assertEqual(doc["translated_from"], batch_hash(OLD_BATCH["resultados"]))
        self.assertEqual(doc["results"][0], {"tc": "CT-LOJA-001", "status": "Passou", "actual_result": "ok",
                                             "ts": "2026-09-20T10:00:00"})
        self.assertEqual(doc["results"][1]["new_defect"], {"titulo": "Erro 500", "severity": "Alta",
                                                           "priority": "Alta", "related_tc": "CT-LOJA-002"})
        self.assertEqual(doc["results"][1]["note"], "x")
        self.assertEqual(doc["results"][2]["bug"], "BUG-1")
        self.assertEqual(json.loads(self.read("runs/a11y_20260920_01.json")), {"norma": "WCAG 2.1 AA", "paginas": []})
        self.assertNotIn("translated_from", self.read("runs/mobile_20260920_01.json"))   # a report, not a batch

    def test_history_moved_byte_for_byte_and_protection_rule_updated(self):
        self.run_it("--apply")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "historico")))
        for rel, digest in self.history.items():
            self.assertEqual(sha(os.path.join(self.ws, rel.replace("historico/", "history/"))), digest, rel)
        self.assertIn("Edit(/history/**)", self.read(".claude/settings.json"))

    def test_golden_folder_keeps_the_batch_and_renames_the_workbooks(self):
        self.run_it("--apply")
        g = os.path.join(self.ws, "golden", "lote01")
        self.assertEqual(sorted(os.listdir(g)), ["after.xlsx", "before.xlsx", "golden.yaml", "lote_20260920_01.json"])
        self.assertEqual(json.loads(self.read("golden/lote01/lote_20260920_01.json")), OLD_BATCH)
        self.assertEqual(yaml.safe_load(self.read("golden/lote01/golden.yaml")),
                         {"now": "2026-09-20T10:00:00", "label": "[2026-09-20, lote_x]"})

    def test_texts_session_and_gitignore(self):
        self.run_it("--apply")
        claude = self.read("CLAUDE.md")
        for new in ("docs/methodology.md", "technique-browser.md", "/log-bug", "scripts/validate_project.py",
                    "adapters.excel.apply"):
            self.assertIn(new, claude)
        ctx = self.read("context.md")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "contexto.md")))
        for new in ("# Session context — loja-demo", "Updated: 2026-09-20 10:00", "## Current state",
                    "## Pending work", "/run-batch", "project.yaml", "history/", "/dashboard",
                    "## Execution point (last batch, last case)", "## Known problems / blockers"):
            self.assertIn(new, ctx)
        session = self.read(".api-session.env")
        self.assertIn("#secret TOKEN", session)
        self.assertIn("TOKEN=fictional-token", session)
        self.assertEqual(self.read(".gitignore"), ".env\n.api-session.env\ndashboard.html\n")

    def test_running_again_changes_nothing(self):
        self.run_it("--apply")
        rc, out = self.run_it()
        self.assertEqual(rc, 0, out)
        self.assertIn("already converted", out)

    def test_the_converted_workspace_passes_the_scripts_that_read_it(self):
        self.run_it("--apply")
        from common import read_project
        self.assertEqual(read_project(self.ws)["project"], "loja-demo")

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_refuses_a_workspace_with_uncommitted_changes(self):
        subprocess.run(["git", "init", "-q", self.ws], check=True)
        rc, out = self.run_it("--apply")
        self.assertEqual(rc, 2)
        self.assertIn("uncommitted changes", out)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "projeto.yaml")))

    def test_not_a_workspace(self):
        with tempfile.TemporaryDirectory() as empty, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mw.main([empty, "--apply"]), 2)
        self.assertIn("not a workspace", err.getvalue())


if __name__ == "__main__":
    unittest.main()
