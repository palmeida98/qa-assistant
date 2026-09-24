---
name: painel
description: Gera e abre o painel de progresso do workspace atual (progresso por area e estado, defeitos abertos, ultimo checkpoint, verificacao do resumo do workbook).
---
1. Identifica a raiz do qa-assistant (<qa>) a partir do workspace atual: normalmente ../../../qa-assistant
   (workspace em qa-workspaces/<empresa>/<projeto>/); no demo, dentro do proprio repositorio, e ../..
   (examples/demo/). Confirma que <qa>/scripts/gerar_painel.py existe.
2. Confirma que o workspace atual tem projeto.yaml com tracker: excel. Com outro tracker, diz ao
   utilizador que o painel ainda so suporta excel e para.
3. Confirma que painel.html esta no .gitignore do workspace. Se nao estiver, acrescenta-o e diz ao
   utilizador (o painel tem dados do projeto e nao vai para o git).
4. A partir da raiz do qa-assistant, com o caminho ABSOLUTO do workspace:
   python scripts/gerar_painel.py <workspace> --abrir
   O script so le o workbook; nunca o grava.
5. Se sair com 2, nada foi gerado: mostra a mensagem (ex.: seccao painel.estados invalida, ou
   excel.aba_resumo.estado_por_testar diferente do estado pendente do painel) e propoe a correcao ao
   projeto.yaml. Nao alteres o projeto.yaml sem confirmacao.
6. Resume ao utilizador o que o script imprimiu: casos executados / total, defeitos abertos e o
   estado do resumo do workbook.
   - "N diferenca(s) com a contagem direta": o resumo tem formulas erradas (tecnica-excel.md seccao 5);
     os numeros do painel estao certos, porque vem da contagem caso a caso. Corrigem-se as formulas,
     nunca os numeros a mao.
   - "nao verificado -- ... recalcular primeiro": o workbook ainda nao foi recalculado desde a ultima
     gravacao; fica verificado no proximo /checkpoint (aplicar recalcula).
7. Privacidade (metodologia-core.md seccao 1.2): o painel de um projeto real contem dados do cliente.
   Abre-o so localmente. Nunca o publiques (Artifact, gist, pagina), nunca o anexes nem o envies para
   nenhum servico, e nunca o copies para o repositorio qa-assistant.
