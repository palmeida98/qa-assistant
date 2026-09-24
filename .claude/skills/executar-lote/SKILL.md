---
name: executar-lote
description: Executa um lote de casos de teste no browser, classifica os resultados e regista-os.
---
1. Parte do plano aprovado em /planear-lote e do contexto.md (ponto de execucao). Se nao houver plano
   aprovado, corre /planear-lote primeiro.
2. Confirma o ambiente contra o projeto.yaml (tecnica-browser.md seccao 2) e que as variaveis de
   credenciais das roles do lote existem no .env (so que existem; nunca mostres valores).
3. Usa o Playwright MCP (nivel N1, tecnica-browser.md seccao 1). Se nao estiver ligado, para e diz ao
   utilizador como o ligar (/mcp), ou usa outro browser disponivel registando a diferenca de ambiente.
4. Segue a tecnica-browser.md para autenticacao (um login por role), localizadores e verificacao.
5. Aplica as regras de decisao da metodologia-core.md seccao 8 (Pass/Fail/N-A/Blocked/Duvida), com os
   estados do vocabulario do projeto (excel.casos.estados no projeto.yaml).
6. Escreve no registo leve a cada caso (seccao 7.1): um ficheiro por lote,
   <registo_leve>/lote_<AAAAMMDD>_<nn>.json (registo_leve do projeto.yaml; por omissao runs/;
   nunca historico/), no formato {"resultados": [{"tc", "estado", "resultado_obtido", "ts"}, ...]}.
   resultado_obtido e o que se observou, nao o esperado copiado.
7. Fail:
   - problema ja registado -> acrescenta "bug": "<id existente>" ao item;
   - problema novo -> NAO cria o defeito: junta a evidencia (tecnica-browser.md seccao 10) e propoe
     /registar-bug ao utilizador.
8. Em caso de duvida sobre o esperado, nao classifiques: marca Duvida com a pergunta concreta e continua.
9. Faz checkpoint a cada 15-20 casos (seccao 7.2) com /checkpoint.
