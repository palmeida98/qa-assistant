# Contexto de sessão — demo-saucedemo
Atualizado: 2026-09-24 13:50 (lote Mobile 01)

## Estado atual
Workbook com 38 casos (7 Login, 5 Produtos, 6 Checkout, 10 API, 7 Acessibilidade, 3 Mobile). Executados: 18/38 (API: 9 Pass, 1 Fail; Acessibilidade: 4 Pass, 1 Fail, 2 por testar; Mobile: 3 Pass). 2 defeitos abertos (BUG-1, BUG-2). Resumo do workbook verificado contra a contagem direta: bate certo.

## Ponto de execução (último lote, último caso)
Lotes `runs/lote_20260924_api01.json` (API), `runs/lote_20260924_a11y01.json` (Acessibilidade) e `runs/lote_20260924_mob01.json` (Mobile) concluídos e aplicados. Último caso: CT-MOB-003. Por testar na Acessibilidade: CT-A11Y-005 (contraste dos incompletos) e CT-A11Y-007 (teclado), que precisam do browser (Playwright MCP). As áreas Login, Produtos e Checkout ainda não foram executadas (2.3).

## Trabalho concluído
- Casos de UI aprovados pelo utilizador (23.09.2026): 7 de Login, 5 de Produtos, 6 de Checkout.
- Onboarding (2.1) e Playwright MCP registado (2.2, confirmação do utilizador pendente).
- Plano 3.1 (24.09.2026): área API acrescentada ao workbook (10 casos, `gerar_workbook.py`); lote API 01 executado com `/testar-api` e `scripts/api_pedido.py` (evidência redigida; nenhum segredo nos ficheiros).
- Plano 3.2 (24.09.2026): área Acessibilidade (7 casos); auditoria automática com `scripts/auditar_a11y.py` (axe-core 4.13 no Chrome instalado) em login, produtos, carrinho e checkout-dados: 0 violações WCAG 2.1 A/AA; boas práticas em falta (sem `<h1>`, conteúdo fora de landmarks) como observação; 1 incompleto de contraste em Products (→ CT-A11Y-005). CT-A11Y-006 Fail → BUG-2 ([2.4.2 A] todas as páginas com o título "Swag Labs"). Relatório: `runs/a11y_20260924_01.json`.
- Plano 3.3 (24.09.2026): área Mobile (3 casos) com flows Maestro em `flows/`, corridos com `scripts/testar_mobile.py` em Maestro Web (beta, Chrome): 3 Pass. Sem dispositivo/emulador nesta máquina: a app nativa não foi testada. Relatório: `runs/mobile_20260924_01.json`.
- Workbook reconstruído com `reconstruir_workbook.py` (gerador + lotes por ordem de execução) para acrescentar a área sem perder os resultados da API.
- Recursos criados e limpos: reserva de teste 2501 (firstname `QA-AUTO-20260924-api01`) criada em CT-API-003 e apagada em CT-API-010 (GET seguinte = 404). Nada ficou por limpar.

## Decisões tomadas (e quem as tomou)
- Site de prática: saucedemo.com; workbook com casos próprios do site no formato da fixture do golden (utilizador, 23.09.2026).
- Formato de bug `BUG-{n}` com as colunas da sheet Defeitos; bugs resolvidos: remover da vista ativa; evidência: texto (utilizador, 23.09.2026).
- Módulos ativos: funcional-web, exploratorio, api, acessibilidade (utilizador, 23.09.2026).
- Estados de defeito, severidade e prioridade, vista `Defeitos Ativos`: ver `projeto.yaml` (utilizador, 23.09.2026).
- "Executado" = todo o caso fora de `Por testar` (N/A e Blocked contam); fórmula do Resumo alinhada (utilizador, 24.09.2026).
- **Por delegação** (o utilizador pediu para avançar sem perguntar, 24.09.2026) — a rever:
  - alvo de API: restful-booker (API pública de prática, documentação publicada);
  - os 10 casos CT-API-001…010 considerados aprovados;
  - BUG-1 registado sem aprovação individual, com severidade e prioridade Média (o adaptador exige uma prioridade da lista);
  - `.env`: contas de demonstração do saucedemo preenchidas localmente (confirmadas no código do site: bloco "Password for all users");
  - os 7 casos CT-A11Y-001…007 considerados aprovados; BUG-2 registado com severidade e prioridade Baixa;
  - módulo `mobile` ativado no demo, com os 3 casos CT-MOB-001…003 e os flows considerados aprovados.

## Dúvidas em aberto
- Os resultados esperados das áreas de UI ainda não foram confirmados no browser (2.3).
- A documentação da API é omissa sobre respostas de erro; o CT-API-009 usa a norma HTTP (RFC 9110) como fonte. Confirmar se é aceitável como critério no demo.
- Rever as decisões por delegação acima (casos de API e BUG-1).

## Problemas conhecidos / bloqueios
- Playwright MCP registado em `.mcp.json` (2.2), à espera de aprovação: abrir o `claude` nesta pasta, aprovar o servidor e confirmar com `/mcp`.
- BUG-1 (API): POST /booking sem campos obrigatórios devolve 500 em vez de 4xx. É um defeito da API de prática (não controlamos a correção); fica aberto como exemplo do fluxo de bugs.

## Trabalho pendente
- 2.2: confirmar `/mcp` com playwright ligado e um login com `standard_user` pelo MCP.
- 2.3: primeiro lote de UI, a executar numa sessão aberta nesta pasta (com o Playwright MCP). Seleção aprovada pelo utilizador (23.09.2026), ainda a passar por `/planear-lote`:
  - sem sessão: CT-LOGIN-003, CT-LOGIN-006
  - locked_out_user: CT-LOGIN-002
  - standard_user: CT-LOGIN-001, CT-PROD-001, CT-PROD-002, CT-CHK-001
  - problem_user: CT-PROD-005 (esperado Fail → `/registar-bug`, com aprovação do utilizador antes de escrever)
  - Registo leve em `runs/lote_20260923_01.json`, no formato da metodologia §7.1; aplicar com `python -m adapters.excel.aplicar examples/demo runs/lote_20260923_01.json` a partir da raiz do qa-assistant; depois `/checkpoint`.
  - Nota: o próximo defeito será BUG-3 (BUG-1 e BUG-2 já existem).

## Estado da validação
API: 10/10, Acessibilidade: 5/7 e Mobile: 3/3 executados, aplicados; cross-check limpo, recálculo OK, resumo vs contagem direta bate certo. Áreas de UI (Login, Produtos, Checkout): por executar.
