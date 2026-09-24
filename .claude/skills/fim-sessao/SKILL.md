---
name: fim-sessao
description: Fecha a sessao de testes: checkpoint final, bugs, cross-check e resumo.
---
1. Confirma que nenhum caso ficou em Duvida sem ser apresentado ao utilizador; reclassifica com as
   respostas (seccao 8.5). O adaptador recusa lotes com Duvida.
2. Faz o ultimo checkpoint com /checkpoint (metodologia-core.md seccao 16.2), incluindo a aplicacao
   dos lotes ao workbook/tracker, com confirmacao do utilizador.
3. Atualiza bugs novos, existentes e resolvidos (seccao 9.3) com /registar-bug.
4. Cross-check entre casos, resultados, bugs e totais (seccao 11). Com tracker excel, o cross-check
   do adaptador tem de dar 0 problemas (adapters.excel.aplicar sai com 0).
5. Produz o resumo no formato da seccao 16.3, com os numeros lidos do workbook/tracker (nao de memoria).
6. Atualiza o contexto.md do workspace (seccao 12.2), com os proximos passos.
7. Com tracker excel, gera o painel a partir da raiz do qa-assistant:
   python scripts/gerar_painel.py <workspace>
   e diz ao utilizador onde esta o painel.html. Nao declares a sessao concluida com inconsistencias
   conhecidas por resolver (seccao 16.2).
