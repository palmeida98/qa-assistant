---
name: testar-api
description: Testa uma API HTTP do workspace atual (contrato, codigos, autenticacao por role, negativos, persistencia) com evidencia redigida, seguindo a tecnica-api.md.
---
1. Le a tecnica-api.md (<qa>/docs/) e, no workspace atual, a seccao api: do projeto.yaml (base_url,
   contrato, autenticacao, credenciais por role) e o contexto.md. <qa> e a raiz do qa-assistant:
   normalmente ../../../qa-assistant; no demo, ../.. . Se o modulo api nao estiver ativo ou a seccao
   api: estiver vazia, para e pergunta.
2. Confirma que base_url e o ambiente de TESTES (tecnica-api.md seccao 2). Se parecer producao, para.
3. Confirma que .env e .api-sessao.env estao no .gitignore do workspace (acrescenta se faltarem) e que
   as variaveis de credenciais existem no .env (so que existem; nunca mostres valores).
4. Casos: usa os casos de API aprovados no workbook/tracker. Se nao existirem, propoe-nos com
   /gerar-casos a partir do contrato (seccoes 3 a 7: contrato, negativos, matriz de roles,
   idempotencia, persistencia). O esperado vem do contrato; se o contrato for omisso, so entra com
   outra fonte objetiva escrita no caso (ex.: RFC 9110 para 4xx vs 5xx) ou fica como pergunta.
5. Ordena com /planear-lote: health check primeiro, depois os fluxos que criam recursos, os que os
   usam, e as limpezas no fim. Um token por role e por lote.
6. Todos os pedidos pelo script, a partir da raiz do qa-assistant, com o caminho ABSOLUTO do workspace:
   python scripts/api_pedido.py <workspace> <METODO> <caminho> [--json '...'] [-H "Nome: {{VAR}}"]
       [--esperado <codigo>] [--guardar campo=NOME] [--guardar-segredo token=TOKEN]
   - caminho relativo a api.base_url, SEM barra inicial (booking/1): no Git Bash do Windows um
     argumento /booking chega ao script convertido em C:/Program Files/Git/booking;
   - credenciais sempre como {{VARIAVEL}}; tokens sempre com --guardar-segredo, usados depois como
     {{TOKEN}}. Nunca escrevas um valor de credencial ou token num comando, nota ou ficheiro;
   - saida 3 (sem resposta) = Blocked por ambiente: uma nova tentativa e, se falhar, Blocked;
   - persistencia confirmada com um GET quando o caso o afirma.
7. Classifica pela metodologia-core.md seccao 8 e pela tecnica-api.md seccao 8 (5xx a entrada invalida
   = Fail). Escreve no registo leve (formato da metodologia seccao 7.1): resultado_obtido = codigo +
   resumo do corpo. Recursos criados (tipo + id) tambem ficam registados.
8. Fail novo -> junta a evidencia do script (ja redigida) e propoe /registar-bug; nunca crias o
   defeito sem aprovacao.
9. No fim: apaga os recursos de teste que criaste (se o projeto permitir) ou lista-os no contexto.md;
   confirma que nenhum segredo ficou no registo leve, nos bugs ou no contexto.md; segue para
   /checkpoint.
10. Nunca faz testes de carga, fuzzing, forca bruta nem pedidos em rajada (tecnica-api.md seccao 11).
