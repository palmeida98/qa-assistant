---
name: executar-lote
description: Executa um lote de casos de teste no browser, classifica os resultados e regista-os.
---

1. Confirma o ambiente contra o projeto.yaml (tecnica-browser.md seccao 2).
2. Segue a tecnica-browser.md para autenticacao, localizadores e verificacao.
3. Aplica as regras de decisao da metodologia-core.md seccao 8 (Pass/Fail/N-A/Blocked/Duvida).
4. Escreve no registo leve (runs/) a cada caso.
5. Faz checkpoint a cada 15-20 casos (seccao 7.2).
6. Em caso de duvida sobre o esperado, nao classifiques: marca Duvida e continua.
