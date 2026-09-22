---
name: onboarding-projeto
description: Configura um novo workspace de projeto - ambientes, roles, tracker, formato de bug - seguindo metodologia-core.md seccao 4.
---

1. Confirma se existem CLAUDE.md e projeto.yaml no workspace atual. Se nao existirem, copia-os de:
   - ../../../qa-assistant/docs/contexto-template.md -> CLAUDE.md
   - ../../../qa-assistant/docs/projeto.yaml -> projeto.yaml
2. Le o CLAUDE.md e o projeto.yaml. Para cada campo em branco, PERGUNTA ao utilizador
   (nao assumas nem inventes):
   - ambientes (confirma qual e o ambiente de TESTE, nao producao)
   - roles/perfis e diferencas de acesso
   - nomes das variaveis de credenciais (os valores vao para o .env, nunca para aqui)
   - documentacao de referencia
   - estrutura do workbook ou tracker
   - formato obrigatorio de bug
   - regra para bugs resolvidos (remover ou arquivar)
   - politica de evidencia (texto ou texto+screenshot)
   - modulos de teste ativos
3. Preenche o CLAUDE.md e o projeto.yaml com as respostas.
4. Cria o .env com as variaveis de credenciais identificadas, SEM valores.
5. Confirma que .env esta no .gitignore do workspace.
6. Cria o contexto.md inicial, no formato da metodologia-core.md seccao 12.2.
7. Nunca escreve informacao deste projeto nas camadas universais (docs/ do qa-assistant).
