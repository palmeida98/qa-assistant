---
name: checkpoint
description: Grava o registo leve, atualiza o contexto.md do workspace no formato da metodologia-core.md seccao 12.2 e faz commit local no workspace.
---

1. Le do projeto.yaml o caminho do registo leve (registo_leve). Se nao estiver definido, usa runs/.
   Nunca escrevas em historico/: e imutavel (metodologia-core.md seccao 13).
2. Grava no registo leve todos os resultados da sessao ainda nao gravados: caso, resultado, nota curta,
   bug associado e timestamp (seccao 7.1). Usa o formato do projeto, se estiver definido.
3. Casos em Duvida mantem-se como Duvida, com a pergunta concreta (seccao 8.5).
4. Atualiza o contexto.md com a estrutura da seccao 12.2: estado atual, ponto de execucao
   (ultimo lote, ultimo caso), trabalho concluido, decisoes (e quem as tomou), duvidas em aberto,
   bloqueios, trabalho pendente e estado da validacao. Atualiza a data e hora.
5. Nao reconstruas o progresso do zero: acrescenta ao que ja esta registado (seccao 12.1).
6. Se o workspace for um repositorio git, faz um commit local por checkpoint (seccao 12.1),
   so com os ficheiros do registo leve e o contexto.md. Nunca faz push.
7. Nao passa resultados para o workbook/tracker principal sem confirmacao do utilizador. Com tracker
   excel e confirmacao dada, aplica o lote pelo adaptador (tecnica-excel.md seccao 1), a partir da raiz
   do qa-assistant: python -m adapters.excel.aplicar <workspace> <registo_leve>/<lote>.json
   e so segue se sair com 0. Os outros codigos:
   - 4 = lote invalido (ex.: Duvida por resolver, estado fora do vocabulario, caso repetido): nada foi
     escrito; corrige o registo leve com o utilizador e volta a aplicar. Excecao: se o lote JA tinha
     sido aplicado e foi editado depois, nao o reapliques -- a correcao vai num lote novo, com outro
     nome, so com os casos a corrigir (e "bug" em vez de "defeito_novo" para defeitos ja criados);
   - 2 = cross-check com problemas: original intacto, resultado em .REVISTO.xlsx; mostra os problemas;
   - 3 = gravado, mas o recalculo falhou: rever antes de dar por terminado;
   - 5 = gravado e recalculado, mas o resumo do workbook nao bate com a contagem direta
     (aba_resumo): mostra as celulas ao utilizador; corrigem-se as FORMULAS do resumo, nunca os
     numeros a mao. Os totais do painel e do resumo de sessao vem da contagem direta.
   Reaplicar o mesmo lote nao muda nada (o adaptador deteta-o).
8. Indica ao utilizador o ponto exato onde a execucao ficou. Com tracker excel, pode gerar o painel:
   python scripts/gerar_painel.py <workspace> (a partir da raiz do qa-assistant).
