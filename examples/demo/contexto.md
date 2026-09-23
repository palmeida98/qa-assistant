# Contexto de sessão — demo-saucedemo
Atualizado: 2026-09-23

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
- Playwright MCP ainda não registado (tarefa 2.2).

## Trabalho pendente
- 2.2: registar o Playwright MCP no workspace.
- 2.3: primeiro lote (5–10 casos) com `/planear-lote` e `/executar-lote`, aplicado pelo adaptador, e `/checkpoint`.

## Estado da validação
Configuração validada pelo adaptador (load + cross-check). Nenhum caso executado.
