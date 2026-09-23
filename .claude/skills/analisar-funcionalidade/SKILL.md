---
name: analisar-funcionalidade
description: Analisa uma funcionalidade por role/perfil e produz a lista de comportamentos a testar, riscos e lacunas de especificacao (metodologia-core.md seccao 5.1).
---

1. Le o CLAUDE.md e o projeto.yaml do workspace atual (roles, ambientes, documentacao de referencia).
   Nao voltes a perguntar o que ja la esta (metodologia-core.md seccao 4).
2. Identifica a fonte de verdade da funcionalidade (requisitos, especificacao, Figma, casos existentes)
   e respeita a hierarquia da seccao 1.3. Se duas fontes se contradizem, para e pergunta.
3. Para cada role/perfil do projeto, indica se acede a funcionalidade e o que pode e nao pode fazer.
4. Lista as entradas, os estados (ciclo de vida), as regras de negocio e as integracoes.
5. Avalia o risco: probabilidade de falha x impacto para o utilizador ou para o negocio.
6. Regista as lacunas de especificacao como perguntas ou notas de "especificacao em falta".
   Nunca as preenchas com suposicoes (seccao 5.1).
7. Produz a lista de comportamentos a testar, cada um ligado a role, regra ou estado de origem,
   pronta para /gerar-casos.
8. Apresenta as perguntas em aberto ao utilizador de uma so vez, no fim.
