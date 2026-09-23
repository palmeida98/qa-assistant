# Contexto de sessão — demo-saucedemo
Atualizado: 2026-09-23 (preparação da 2.3)

## Estado atual
Onboarding concluído com `/onboarding-projeto` (plano, tarefa 2.1). `CLAUDE.md`, `projeto.yaml` (incluindo a secção `excel:`) e `.env` (sem valores) criados. Workbook `workbook/plano-demo.xlsx` gerado com 18 casos aprovados, todos em `Por testar`; sem defeitos registados.

## Ponto de execução (último lote, último caso)
Nenhum lote executado.

## Trabalho concluído
- Casos propostos e aprovados pelo utilizador (23.09.2026): 7 de Login, 5 de Produtos, 6 de Checkout.
- Onboarding: campos em aberto perguntados ao utilizador; secção `excel:` preenchida com `python -m adapters.excel.inspecionar` + respostas; validada com `ExcelAdapter.from_projeto(...).load()` (OK) e `cross_check()` (sem problemas). Workbook não alterado pela validação.

## Decisões tomadas (e quem as tomou)
- Site de prática: saucedemo.com; workbook com casos próprios do site no formato da fixture do golden (utilizador, 23.09.2026).
- Formato de bug `BUG-{n}` com as colunas da sheet Defeitos; bugs resolvidos: remover da vista ativa; evidência: texto (utilizador, 23.09.2026).
- Módulos ativos: funcional-web, exploratorio, api, acessibilidade (utilizador, 23.09.2026). `api` com pouca aplicação (sem API pública documentada); `acessibilidade` ainda por implementar (3.2).
- Estados de defeito: Aberto, Em análise, Em correção, Corrigido, Reaberto, Fechado, Não é defeito; fechados: Corrigido, Fechado, Não é defeito; inicial: Aberto. Severidade e prioridade: Crítica, Alta, Média, Baixa (utilizador, 23.09.2026). Decisão do demo, não regra da metodologia.
- Vista `Defeitos Ativos` ordenada por prioridade e depois por Área, com resumo em A2 (utilizador, 23.09.2026).

## Dúvidas em aberto
- Os resultados esperados foram escritos a partir do comportamento conhecido do site e ainda não foram confirmados no browser; confirmar na primeira execução (2.3).

## Problemas conhecidos / bloqueios
- `.env` sem valores: preencher localmente com as contas de demonstração publicadas na página de login do site antes de executar.
- Playwright MCP registado em `.mcp.json` (2.2), à espera de aprovação: abrir o `claude` nesta pasta, aprovar o servidor e confirmar com `/mcp`.

## Trabalho pendente
- 2.2: confirmar `/mcp` com playwright ligado e um login com `standard_user` pelo MCP.
- 2.3: primeiro lote, a executar numa sessão aberta nesta pasta (com o Playwright MCP). Seleção aprovada pelo utilizador (23.09.2026), ainda a passar por `/planear-lote`:
  - sem sessão: CT-LOGIN-003, CT-LOGIN-006
  - locked_out_user: CT-LOGIN-002
  - standard_user: CT-LOGIN-001, CT-PROD-001, CT-PROD-002, CT-CHK-001
  - problem_user: CT-PROD-005 (esperado Fail → `/registar-bug`, com aprovação do utilizador antes de escrever)
  - Registo leve em `runs/lote_20260923_01.json`, no formato do adaptador; aplicar com `python -m adapters.excel.aplicar examples/demo runs/lote_20260923_01.json` a partir da raiz do qa-assistant; depois `/checkpoint`.

## Estado da validação
Configuração validada pelo adaptador (load + cross-check). Nenhum caso executado.
