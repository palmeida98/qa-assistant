---
name: onboarding-projeto
description: Configura um novo workspace de projeto - ambientes, roles, tracker, formato de bug - seguindo metodologia-core.md seccao 4.
---

1. Identifica a raiz do qa-assistant (<qa>) a partir do workspace atual: normalmente ../../../qa-assistant
   (workspace em qa-workspaces/<empresa>/<projeto>/); no demo, dentro do proprio repositorio, e ../..
   (examples/demo/). Confirma que <qa>/docs/metodologia-core.md existe antes de continuar.
   Confirma se existem CLAUDE.md e projeto.yaml no workspace atual. Se nao existirem, copia-os de:
   - <qa>/docs/contexto-template.md -> CLAUDE.md (e ajusta os imports @ para o mesmo <qa>)
   - <qa>/docs/projeto.yaml -> projeto.yaml
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
3. Preenche o CLAUDE.md e o projeto.yaml com as respostas. Com tracker excel, acrescenta ao CLAUDE.md
   o import de <qa>/docs/tecnica-excel.md, ao lado dos outros imports universais.
4. Se tracker for "excel", preenche a seccao excel: do projeto.yaml INSPECIONANDO o workbook real
   (tecnica-excel.md seccao 1; nunca adivinhar nomes de sheets, colunas ou estados):
   a. Corre, a partir da raiz do qa-assistant, sobre uma COPIA local do workbook (nunca o oficial
      numa pasta sincronizada; o script so le, mas a copia evita bloqueios):
      python -m adapters.excel.inspecionar <copia.xlsx> --yaml-out <scratchpad>/proposta.yaml
   b. Mostra ao utilizador a evidencia encontrada e a proposta. Tudo o que vier como
      "CONFIRMAR: ..." e PERGUNTADO, com as opcoes que o script listou: coluna de estado,
      resultado obtido e observacoes, estados validos, estados fechados (alinhados com a regra
      bugs_resolvidos), estado inicial de um defeito novo, ordem das prioridades, correspondencia
      das colunas da vista de ativos, texto de resumo (se existir).
   c. Mostra tambem as inconsistencias que a inspecao revelar (ex.: IDs fora do formato, listas de
      validacao que nao batem com os valores usados) -- registam-se, nao se corrigem aqui.
   d. Escreve a seccao excel: no projeto.yaml, sem nenhum "CONFIRMAR" por resolver.
   e. Valida: ExcelAdapter.from_projeto(<workspace>).load() tem de passar (falha se faltar uma sheet
      ou coluna) e cross_check() so para mostrar problemas que ja existam no workbook. Nao aplica
      lotes nem grava o workbook durante o onboarding.
   f. Se o workbook tiver uma aba de totais com formulas (resumo por area e estado), preenche
      excel.aba_resumo (sheet, linha_cabecalho, coluna_area, coluna_total, linha_total e, se os
      cabecalhos nao forem os nomes dos estados, colunas_estado) a partir dos cabecalhos reais, e
      confirma com o utilizador. Se houver uma coluna de % executada, preenche coluna_percentagem e
      estado_por_testar (o mesmo estado que sera "pendente" na seccao painel:); tolerancia_percentagem
      so se o resumo arredondar de proposito. Serve para verificar as formulas contra a contagem direta
      (tecnica-excel.md seccao 5); os totais nunca se leem do resumo.
   g. Preenche a seccao painel: do projeto.yaml (scripts/gerar_painel.py): para cada estado de
      excel.casos.estados, PERGUNTA o tipo -- pendente, ok, falha, bloqueado ou na. Propoe os
      tipos pelos nomes, mas o utilizador confirma (o vocabulario e do projeto).
5. Se um modulo de browser estiver ativo (ex.: funcional-web), regista o Playwright MCP NO WORKSPACE
   (tecnica-browser.md seccao 1, nivel N1). O comando corre a partir da pasta do workspace, porque
   --scope project escreve o .mcp.json na pasta atual:
   - Windows: claude mcp add playwright --scope project -- cmd /c npx @playwright/mcp@latest
     (sem o "cmd /c" o Claude Code nao consegue lancar o npx no Windows)
   - Linux/macOS: claude mcp add playwright --scope project -- npx @playwright/mcp@latest
   Depois:
   a. Confirma que o .mcp.json ficou na pasta do workspace e NUNCA na raiz do qa-assistant
      (unica excecao: o demo, em examples/demo/).
   b. Confirma que o .mcp.json nao tem credenciais nem URLs de clientes.
   c. Diz ao utilizador que, ao abrir o claude no workspace pela primeira vez, tem de aprovar o
      servidor (servidores de projeto pedem confirmacao) e confirmar com /mcp que "playwright"
      aparece ligado. Esta sessao nao ve as ferramentas do MCP registado agora.
6. Cria o .env com as variaveis de credenciais identificadas, SEM valores.
7. Confirma que .env e painel.html estao no .gitignore do workspace.
8. Cria o contexto.md inicial, no formato da metodologia-core.md seccao 12.2.
9. Nunca escreve informacao deste projeto nas camadas universais (docs/ do qa-assistant).
