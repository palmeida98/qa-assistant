---
name: auditar-acessibilidade
description: Audita a acessibilidade (WCAG 2.1 AA) das paginas do workspace atual - parte automatica com o motor qa-audit (axe-core, teclado, reflow) e parte manual guiada - e mapeia cada falha ao criterio WCAG e nivel.
---
1. Le a tecnica-acessibilidade.md (<qa>/docs/) e, no workspace atual, a seccao acessibilidade: do
   projeto.yaml (norma, browser, login, paginas, auditar_paginas_com_erro_http) e o contexto.md. <qa> e a raiz do qa-assistant:
   normalmente ../../../qa-assistant; no demo, ../.. . Se a seccao estiver vazia, propoe as paginas
   a partir dos fluxos principais e pergunta.
2. Confirma que as URLs sao do ambiente de TESTES (tecnica-browser.md seccao 2) e que as variaveis do
   login existem no .env (so que existem; nunca mostres valores).
3. O motor qa-audit tem de estar instalado (tecnica-acessibilidade.md seccao 7): se o script sair com 2 a
   dizer "motor qa-audit por instalar", corre `qa-audit doctor` (a partir de <qa>) e mostra o que falta.
   O browser tem de arrancar fora da sandbox do Bash.
4. Parte automatica, a partir de <qa>, com o caminho ABSOLUTO do workspace:
   python scripts/auditar_a11y.py <workspace> --saida runs/a11y_<AAAAMMDD>_<nn>.json
   - saida 3 ou "NAO AUDITADA"/"redirecionada": a pagina fica Blocked (ambiente), nunca Pass; um login
     falhado nao se repete (conta partilhada);
   - violacoes WCAG -> Fail do caso da pagina, com regras, criterios e nivel no resultado_obtido;
   - boas praticas -> observacoes (nunca Fail); incompletos -> revisao manual (passo 5);
   - outras verificacoes (armadilha de teclado 2.1.2, foco invisivel 2.4.7, reflow 1.4.10) -> indicios
     para confirmar no passo 5, nunca Fail sozinhos;
   - "AVISO: ... 'chrome' tratado como 'chromium'": propoe atualizar acessibilidade.browser no projeto.yaml.
5. Parte manual guiada (tecnica-acessibilidade.md seccao 3), com o Playwright MCP ou o browser: pelo
   menos teclado (2.1.1, 2.1.2), ordem e visibilidade do foco (2.4.3, 2.4.7), titulo da pagina (2.4.2),
   zoom 200% (1.4.4) e reflow a 320 px (1.4.10), os incompletos do axe e as outras verificacoes do motor. Sem browser disponivel, os
   casos manuais ficam Por testar, com o motivo.
6. Registo leve no formato da metodologia seccao 7.1; um defeito por problema distinto (regra + causa),
   com o criterio e o nivel no titulo (ex.: "[2.4.2 A] ..."), proposto com /registar-bug e so criado
   depois de aprovado. A severidade_sugerida (do impacto do axe) e so sugestao.
7. O relatorio JSON fica no workspace (pode ter excertos de HTML com dados do projeto); nunca vai
   para o repositorio qa-assistant. Segue para /checkpoint.
