# Contexto de sessão — demo-saucedemo
Atualizado: 2026-09-24 13:30 (decisões do utilizador sobre a delegação e o CT-LOGIN-006)

## Estado atual
Workbook com 38 casos (7 Login, 5 Produtos, 6 Checkout, 10 API, 7 Acessibilidade, 3 Mobile). Executados: 26/38 (UI: 7 Pass, 1 Fail, 10 por testar; API: 9 Pass, 1 Fail; Acessibilidade: 4 Pass, 1 Fail, 2 por testar; Mobile: 3 Pass). 3 defeitos abertos (BUG-1, BUG-2, BUG-3). Sem casos em Dúvida. Resumo do workbook verificado contra a contagem direta: bate certo.

## Ponto de execução (último lote, último caso)
Lotes aplicados, por ordem: `runs/lote_20260924_api01.json` (API), `lote_20260924_a11y01.json` (Acessibilidade), `lote_20260924_mob01.json` (Mobile), `lote_20260924_ui01.json` (UI, plano 2.3) e `lote_20260924_ui02.json` (CT-LOGIN-006, reclassificado). Último caso: CT-LOGIN-006. Por executar na UI: CT-LOGIN-004, 005, 007, CT-PROD-003, 004, CT-CHK-002…006. Por testar na Acessibilidade: CT-A11Y-005 (contraste dos incompletos) e CT-A11Y-007 (teclado), que precisam do browser (Playwright MCP).

## Trabalho concluído
- Casos de UI aprovados pelo utilizador (23.09.2026): 7 de Login, 5 de Produtos, 6 de Checkout.
- Onboarding (2.1) e Playwright MCP registado (2.2, confirmação do utilizador pendente).
- Plano 3.1 (24.09.2026): área API (10 casos); lote API 01 executado com `/testar-api` e `scripts/api_pedido.py` (evidência redigida; nenhum segredo nos ficheiros).
- Plano 3.2 (24.09.2026): área Acessibilidade (7 casos); auditoria automática com `scripts/auditar_a11y.py` (axe-core 4.13 no Chrome instalado) em login, produtos, carrinho e checkout-dados: 0 violações WCAG 2.1 A/AA; boas práticas em falta (sem `<h1>`, conteúdo fora de landmarks) como observação; 1 incompleto de contraste em Products (→ CT-A11Y-005). CT-A11Y-006 Fail → BUG-2 ([2.4.2 A] todas as páginas com o título "Swag Labs"). Relatório: `runs/a11y_20260924_01.json`.
- Plano 3.3 (24.09.2026): área Mobile (3 casos) com flows Maestro em `flows/`, corridos com `scripts/testar_mobile.py` em Maestro Web (beta, Chrome): 3 Pass. Sem dispositivo/emulador nesta máquina: a app nativa não foi testada. Relatório: `runs/mobile_20260924_01.json`.
- Plano 2.3 (24.09.2026): lote de UI com os 8 casos aprovados, agrupados por role (sem sessão → locked_out_user → standard_user → problem_user). Executado com a biblioteca Playwright no Chrome, conduzida pelo agente, porque o Playwright MCP não estava disponível na sessão (diferença de ambiente registada, tecnica-browser.md §1). 6 Pass, CT-PROD-005 Fail → BUG-3 (problem_user: os 6 produtos com a mesma imagem), CT-LOGIN-006 em Dúvida (resolvida depois: Pass). A 1.ª observação do problem_user veio vazia (o script não esperou pela lista); repetida com espera explícita antes de classificar. Estado reposto: o produto adicionado em CT-CHK-001 foi removido.
- Plano 2.5 (24.09.2026): CT-LOGIN-001…003 promovidos a specs Playwright em `e2e/`.
- Workbook reconstruível com `reconstruir_workbook.py` (gerador + lotes por ordem de execução).
- Recursos criados e limpos: reserva de teste 2501 (firstname `QA-AUTO-20260924-api01`) criada em CT-API-003 e apagada em CT-API-010 (GET seguinte = 404). Nada ficou por limpar.

## Decisões tomadas (e quem as tomou)
- Site de prática: saucedemo.com; workbook com casos próprios do site no formato da fixture do golden (utilizador, 23.09.2026).
- Formato de bug `BUG-{n}` com as colunas da sheet Defeitos; bugs resolvidos: remover da vista ativa; evidência: texto (utilizador, 23.09.2026).
- Módulos ativos: funcional-web, exploratorio, api, acessibilidade (utilizador, 23.09.2026); mobile (utilizador, 24.09.2026).
- Estados de defeito, severidade e prioridade, vista `Defeitos Ativos`: ver `projeto.yaml` (utilizador, 23.09.2026).
- "Executado" = todo o caso fora de `Por testar` (N/A e Blocked contam); fórmula do Resumo alinhada (utilizador, 24.09.2026).
- Aprovado pelo utilizador a 24.09.2026 o que tinha sido feito por delegação:
  - alvo de API: restful-booker (API pública de prática, documentação publicada); os 10 casos CT-API-001…010, incluindo o CT-API-009 com a norma HTTP (RFC 9110) como fonte, por a documentação ser omissa sobre erros;
  - os 7 casos CT-A11Y-001…007 e os 3 casos CT-MOB-001…003 com os respetivos flows;
  - BUG-1 (Média/Média), BUG-2 (Baixa/Baixa) e BUG-3 (Média/Média), com as severidades e prioridades propostas;
  - `.env` com as contas de demonstração publicadas pelos próprios sites (só local).
- CT-LOGIN-006: conta como Pass mostrar o formulário de login com o erro certo, mesmo que o URL continue /inventory.html; esperado reescrito no `gerar_workbook.py` (utilizador, 24.09.2026).

## Dúvidas em aberto
- Os esperados dos casos de UI por executar ainda não foram confirmados no browser.

## Problemas conhecidos / bloqueios
- Playwright MCP registado em `.mcp.json` (2.2), à espera de aprovação: abrir o `claude` nesta pasta, aprovar o servidor e confirmar com `/mcp`.
- BUG-1 (API): POST /booking sem campos obrigatórios devolve 500 em vez de 4xx. É um defeito da API de prática (não controlamos a correção); fica aberto como exemplo do fluxo de bugs.

## Trabalho pendente
- **25.09.2026: `--secrets .env` acrescentado ao `.mcp.json` (tecnica-browser.md §3).** Validado no mesmo dia num workspace real (não no demo): os campos do login ficaram `<secret>NOME_DA_VARIAVEL</secret>`, nunca o valor. Falta repetir aqui, no demo: abrir o `claude` nesta pasta, aprovar o `playwright` de novo (args mudaram), `/mcp` ligado; login com `standard_user` escrevendo só os NOMES das variáveis (`DEMO_SAUCE_STANDARD_USER`, `DEMO_SAUCE_PASSWORD`) e confirmar que a resposta mostra `<secret>…</secret>` e não o valor. Depois `/planear-lote` + `/executar-lote` com os 10 casos de UI por testar. 
- 2.2: confirmar `/mcp` com playwright ligado e um login com `standard_user` pelo MCP.
- Próximo lote de UI (10 casos por testar), de preferência com o Playwright MCP depois de fechar a 2.2.
- Acessibilidade manual: CT-A11Y-005 e CT-A11Y-007.
- Nota: o próximo defeito será BUG-4.

## Estado da validação
UI: 8/18, API: 10/10, Acessibilidade: 5/7 e Mobile: 3/3 executados e aplicados; cross-check limpo, recálculo OK, resumo vs contagem direta bate certo.
