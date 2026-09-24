---
name: gerar-casos
description: Gera propostas de casos de teste a partir da analise de uma funcionalidade, com a tecnica de desenho indicada em cada caso, no formato do tracker do projeto (metodologia-core.md seccoes 5.2 e 5.3).
---

1. Parte da saida de /analisar-funcionalidade. Se nao existir, corre-a primeiro ou pergunta ao utilizador.
2. Le o formato de caso e o tracker em projeto.yaml (workspace atual) e a estrutura do workbook/tracker
   no CLAUDE.md. Nao inventes colunas nem estados.
3. Escolhe a tecnica de desenho por comportamento (seccao 5.2): particao de equivalencia, valores-limite,
   tabela de decisao, transicao de estados, pairwise, matriz de roles, error guessing (heuristicas de
   <qa>/docs/heuristicas.md, seccao 17) ou charter exploratorio. Inclui casos negativos.
4. Cada caso tem os campos do modelo de dados (seccao 2): funcionalidade, titulo, pre-condicoes, passos,
   UM resultado esperado verificavel, role, plataforma, modulo, tecnica, prioridade e origem.
   Se um caso tiver varios resultados esperados, divide-o (seccao 5.3).
5. O resultado esperado vem da especificacao, nunca do comportamento atual da aplicacao.
6. A prioridade deriva do risco da funcionalidade, salvo regra diferente do projeto.
7. Antes de propor, procura casos existentes equivalentes e sinaliza duplicados (seccao 10).
8. Apresenta os casos como PROPOSTAS. So entram no workbook/tracker depois de aprovados pelo utilizador
   (seccao 5.3), com IDs estaveis no padrao do projeto.
