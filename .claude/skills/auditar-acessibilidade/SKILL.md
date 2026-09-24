---
name: auditar-acessibilidade
description: Audita a acessibilidade (WCAG 2.1 AA) das paginas do workspace atual - parte automatica com axe-core e parte manual guiada - e mapeia cada falha ao criterio WCAG e nivel.
---
1. Le a tecnica-acessibilidade.md (<qa>/docs/) e, no workspace atual, a seccao acessibilidade: do
   projeto.yaml (norma, browser, login, paginas) e o contexto.md. <qa> e a raiz do qa-assistant:
   normalmente ../../../qa-assistant; no demo, ../.. . Se a seccao estiver vazia, propoe as paginas
   a partir dos fluxos principais e pergunta.
2. Confirma que as URLs sao do ambiente de TESTES (tecnica-browser.md seccao 2) e que as variaveis do
   login existem no .env (so que existem; nunca mostres valores).
3. Se tools/a11y/node_modules nao existir, instala uma vez: npm ci --prefix tools/a11y (a partir de <qa>).
4. Parte automatica, a partir de <qa>, com o caminho ABSOLUTO do workspace:
   python scripts/auditar_a11y.py <workspace> --saida runs/a11y_<AAAAMMDD>_<nn>.json
   - saida 3 ou "NAO AUDITADA"/"redirecionada": a pagina fica Blocked (ambiente), nunca Pass;
   - violacoes WCAG -> Fail do caso da pagina, com regras, criterios e nivel no resultado_obtido;
   - boas praticas -> observacoes (nunca Fail); incompletos -> revisao manual (passo 5).
5. Parte manual guiada (tecnica-acessibilidade.md seccao 3), com o Playwright MCP ou o browser: pelo
   menos teclado (2.1.1, 2.1.2), ordem e visibilidade do foco (2.4.3, 2.4.7), titulo da pagina (2.4.2),
   zoom 200% (1.4.4) e reflow a 320 px (1.4.10), e os incompletos do axe. Sem browser disponivel, os
   casos manuais ficam Por testar, com o motivo.
6. Registo leve no formato da metodologia seccao 7.1; um defeito por problema distinto (regra + causa),
   com o criterio e o nivel no titulo (ex.: "[2.4.2 A] ..."), proposto com /registar-bug e so criado
   depois de aprovado. O impacto do axe e so sugestao de severidade.
7. O relatorio JSON fica no workspace (pode ter excertos de HTML com dados do projeto); nunca vai
   para o repositorio qa-assistant. Segue para /checkpoint.
