# Contexto de sessão — demo-saucedemo
Atualizado: 2026-09-24 12:26 (lote UI 01, plano 2.3)

## Estado atual
Workbook com 38 casos (7 Login, 5 Produtos, 6 Checkout, 10 API, 7 Acessibilidade, 3 Mobile). Executados: 25/38 (UI: 6 Pass, 1 Fail, 11 por testar; API: 9 Pass, 1 Fail; Acessibilidade: 4 Pass, 1 Fail, 2 por testar; Mobile: 3 Pass). 3 defeitos abertos (BUG-1, BUG-2, BUG-3). 1 caso em Dúvida (CT-LOGIN-006), fora dos lotes. Resumo do workbook verificado contra a contagem direta: bate certo.

## Ponto de execução (último lote, último caso)
Lotes `runs/lote_20260924_api01.json` (API), `runs/lote_20260924_a11y01.json` (Acessibilidade) e `runs/lote_20260924_mob01.json` (Mobile) e `runs/lote_20260924_ui01.json` (UI, plano 2.3) concluídos e aplicados. Último caso: CT-PROD-005. Por executar na UI: CT-LOGIN-004, 005, 007, CT-PROD-003, 004, CT-CHK-002…006. Por testar na Acessibilidade: CT-A11Y-005 (contraste dos incompletos) e CT-A11Y-007 (teclado), que precisam do browser (Playwright MCP). As áreas Login, Produtos e Checkout ainda não foram executadas (2.3).

## Trabalho concluído
- Casos de UI aprovados pelo utilizador (23.09.2026): 7 de Login, 5 de Produtos, 6 de Checkout.
- Onboarding (2.1) e Playwright MCP registado (2.2, confirmação do utilizador pendente).
- Plano 3.1 (24.09.2026): área API acrescentada ao workbook (10 casos, `gerar_workbook.py`); lote API 01 executado com `/testar-api` e `scripts/api_pedido.py` (evidência redigida; nenhum segredo nos ficheiros).
- Plano 3.2 (24.09.2026): área Acessibilidade (7 casos); auditoria automática com `scripts/auditar_a11y.py` (axe-core 4.13 no Chrome instalado) em login, produtos, carrinho e checkout-dados: 0 violações WCAG 2.1 A/AA; boas práticas em falta (sem `<h1>`, conteúdo fora de landmarks) como observação; 1 incompleto de contraste em Products (→ CT-A11Y-005). CT-A11Y-006 Fail → BUG-2 ([2.4.2 A] todas as páginas com o título "Swag Labs"). Relatório: `runs/a11y_20260924_01.json`.
- Plano 3.3 (24.09.2026): área Mobile (3 casos) com flows Maestro em `flows/`, corridos com `scripts/testar_mobile.py` em Maestro Web (beta, Chrome): 3 Pass. Sem dispositivo/emulador nesta máquina: a app nativa não foi testada. Relatório: `runs/mobile_20260924_01.json`.
- Plano 2.3 (24.09.2026): lote de UI com os 8 casos aprovados, agrupados por role (sem sessão → locked_out_user → standard_user → problem_user). Executado com a biblioteca Playwright no Chrome, conduzida pelo agente, porque o Playwright MCP não estava disponível na sessão (diferença de ambiente registada, tecnica-browser.md §1). 6 Pass, CT-PROD-005 Fail → BUG-3 (problem_user: os 6 produtos com a mesma imagem), CT-LOGIN-006 em Dúvida. A 1.ª observação do problem_user veio vazia (o script não esperou pela lista); repetida com espera explícita antes de classificar. Estado reposto: o produto adicionado em CT-CHK-001 foi removido.
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
  - módulo `mobile` ativado no demo, com os 3 casos CT-MOB-001…003 e os flows considerados aprovados;
  - BUG-3 registado com severidade e prioridade Média (o fluxo previa aprovação do utilizador antes de escrever).

## Dúvidas em aberto
- **CT-LOGIN-006 (Dúvida):** o esperado diz "volta à página de login"; observado: aparece o formulário de login com o erro certo ("Epic sadface: You can only access '/inventory.html' when you are logged in."), mas o URL continua /inventory.html (não muda para /). Conta como "voltar à página de login"? Se sim → Pass; se o URL tiver de ser / → Fail.
- Os esperados dos restantes casos de UI ainda não foram confirmados no browser.
- A documentação da API é omissa sobre respostas de erro; o CT-API-009 usa a norma HTTP (RFC 9110) como fonte. Confirmar se é aceitável como critério no demo.
- Rever as decisões por delegação acima (casos de API e BUG-1).

## Problemas conhecidos / bloqueios
- Playwright MCP registado em `.mcp.json` (2.2), à espera de aprovação: abrir o `claude` nesta pasta, aprovar o servidor e confirmar com `/mcp`.
- BUG-1 (API): POST /booking sem campos obrigatórios devolve 500 em vez de 4xx. É um defeito da API de prática (não controlamos a correção); fica aberto como exemplo do fluxo de bugs.

## Trabalho pendente
- 2.2: confirmar `/mcp` com playwright ligado e um login com `standard_user` pelo MCP.
- Responder à Dúvida do CT-LOGIN-006 e aplicá-lo num lote novo.
- Próximo lote de UI (11 casos por testar), de preferência com o Playwright MCP depois de fechar a 2.2.
- Nota: o próximo defeito será BUG-4.

## Estado da validação
API: 10/10, Acessibilidade: 5/7 e Mobile: 3/3 executados, aplicados; cross-check limpo, recálculo OK, resumo vs contagem direta bate certo. UI: 7/18 executados e aplicados (1 em Dúvida, fora dos lotes).
