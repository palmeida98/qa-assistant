---
name: executar-lote
description: Executa um lote de casos de teste no browser, classifica os resultados e regista-os.
---

1. Confirma o ambiente contra o projeto.yaml (tecnica-browser.md seccao 2).
2. Segue a tecnica-browser.md para autenticacao, localizadores e verificacao.
3. Aplica as regras de decisao da metodologia-core.md seccao 8 (Pass/Fail/N-A/Blocked/Duvida).
4. Escreve no registo leve a cada caso (seccao 7.1). A pasta vem de registo_leve no projeto.yaml;
   se nao estiver definida, usa runs/. Nunca escrevas em historico/ (seccao 13).
5. Faz checkpoint a cada 15-20 casos (seccao 7.2).
6. Em caso de duvida sobre o esperado, nao classifiques: marca Duvida e continua.
